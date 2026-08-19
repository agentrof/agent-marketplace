#!/usr/bin/env python3
"""Validate and stamp the directly-authored Design System baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import stage_package

HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RELATION_BLOCK_RE = re.compile(
    r"\n*## Related knowledge "
    r"<!-- sec: relations:generated:start -->.*?"
    r"<!-- sec: relations:generated:end -->\s*",
    re.DOTALL,
)
MACHINE_FIELDS = {
    "status", "approved_at_utc", "baseline_hash", "supersedes_hash",
}
REQUIRED_CONTENT = {
    "semantic palette": ("palette", "light", "dark"),
    "typography": ("typography",),
    "spacing": ("spacing",),
    "radius": ("radius",),
    "shadows": ("shadow",),
    "motion": ("motion", "reduced"),
    "breakpoints": ("breakpoint",),
    "icon set": ("icon",),
    "component specs": ("component",),
    "accessibility and focus": ("accessibility", "focus"),
    "anti-patterns": ("anti-pattern",),
    "pre-delivery checklist": ("pre-delivery", "checklist"),
}
WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")


def fail(message: str, code: int = 1) -> int:
    print(f"design_system_compile: {message}", file=sys.stderr)
    return code


def parse_frontmatter(path: Path) -> tuple[dict, list[str], int]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("MASTER.md is missing frontmatter")
    fields: dict = {}
    end = -1
    for index, raw in enumerate(lines[1:], start=1):
        value = raw.strip()
        if value == "---":
            end = index
            break
        if not value or value.startswith("#") or value.startswith("- "):
            continue
        if ":" not in value:
            raise ValueError(f"unparseable frontmatter line {index + 1}")
        key, scalar = value.split(":", 1)
        scalar = scalar.strip().strip("\"'")
        fields[key.strip()] = int(scalar) if scalar.isdigit() else scalar
    if end < 0:
        raise ValueError("MASTER.md frontmatter is unterminated")
    return fields, lines, end


def rewrite_frontmatter(path: Path, updates: dict, removals: set[str]) -> None:
    _fields, lines, end = parse_frontmatter(path)
    output = ["---"]
    written: set[str] = set()
    for raw in lines[1:end]:
        stripped = raw.strip()
        if stripped.startswith("- status/") and "status" in updates:
            prefix = raw[:len(raw) - len(raw.lstrip())]
            output.append(f"{prefix}- status/{updates['status']}")
            continue
        if ":" not in stripped or stripped.startswith("- "):
            output.append(raw)
            continue
        key = stripped.split(":", 1)[0].strip()
        if key in removals:
            continue
        if key in updates:
            output.append(f"{key}: {updates[key]}")
            written.add(key)
        else:
            output.append(raw)
    for key, value in updates.items():
        if key not in written:
            output.append(f"{key}: {value}")
    output.extend(lines[end:])
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def normalized_master(path: Path) -> bytes:
    _fields, lines, end = parse_frontmatter(path)
    kept = ["---"]
    for raw in lines[1:end]:
        stripped = raw.strip()
        if stripped.startswith("- status/"):
            continue
        key = stripped.split(":", 1)[0].strip() if ":" in stripped else ""
        if key in MACHINE_FIELDS:
            continue
        kept.append(raw)
    kept.extend(lines[end:])
    return ("\n".join(kept).rstrip() + "\n").encode()


def without_generated_relations(content: bytes) -> bytes:
    text = content.decode("utf-8")
    if "<!-- sec: relations:generated:start -->" not in text:
        return content
    return (RELATION_BLOCK_RE.sub("\n\n", text).rstrip() + "\n").encode()


def baseline_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.md")):
        if path.is_symlink():
            raise ValueError(f"symlinked Design System note: {path}")
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        content = normalized_master(path) if path.name == "MASTER.md" \
            else path.read_bytes()
        digest.update(without_generated_relations(content))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def relation_values(path: Path, key: str) -> list[str]:
    """Read a simple YAML scalar/list relation without accepting prose links."""
    lines = path.read_text(encoding="utf-8").splitlines()
    values: list[str] = []
    active = False
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if line.startswith(f"{key}:"):
            value = line.split(":", 1)[1].strip().strip('"\'')
            if value:
                values.append(value)
            active = True
            continue
        if active and line.lstrip().startswith("- "):
            values.append(line.lstrip()[2:].strip().strip('"\''))
            continue
        if active and line and not line.startswith((" ", "\t")):
            active = False
    return values


def semantic_findings(root: Path) -> list[str]:
    master = root / "MASTER.md"
    fields, _lines, _end = parse_frontmatter(master)
    # Existing approved masters remain reusable read-only.  A new contract is
    # opted into explicitly by authoring the required upstream bindings or a
    # contract_version; its first revision then becomes fail-closed.
    if (not fields.get("contract_version")
            and not relation_values(master, "derives_from")
            and not relation_values(master, "constrained_by")):
        return []
    text = master.read_text(encoding="utf-8").casefold()
    result = []
    for label, terms in REQUIRED_CONTENT.items():
        if not all(term in text for term in terms):
            result.append(f"MASTER.md is missing required {label} content")
    ba_refs = relation_values(master, "derives_from")
    solution_refs = relation_values(master, "constrained_by")
    if not ba_refs:
        result.append("MASTER.md needs derives_from exact Business Analysis package reference(s)")
    if len(solution_refs) != 1:
        result.append("MASTER.md needs exactly one constrained_by Solution package reference")
    docs = root.parent
    for raw in ba_refs:
        match = WIKILINK_RE.search(raw)
        if not match:
            result.append("MASTER.md derives_from must use exact package wikilinks")
            continue
        _receipt, errors = stage_package.verify(
            docs, "business-analysis", match.group(1), require_strict_current=True,
        )
        result.extend(f"MASTER.md derives_from: {error}" for error in errors)
    for raw in solution_refs:
        match = WIKILINK_RE.search(raw)
        if not match:
            result.append("MASTER.md constrained_by must use an exact package wikilink")
            continue
        _receipt, errors = stage_package.verify(
            docs, "solution-design", match.group(1), require_strict_current=True,
        )
        result.extend(f"MASTER.md constrained_by: {error}" for error in errors)
    for page in sorted((root / "pages").glob("*.md")) if (root / "pages").is_dir() else []:
        page_text = page.read_text(encoding="utf-8")
        if "[[design-system/MASTER" not in page_text:
            result.append(f"{page.relative_to(root)} needs exact uses_design MASTER linkage")
    return result


def findings(root: Path) -> list[str]:
    master = root / "MASTER.md"
    if not master.is_file():
        return ["MASTER.md is missing"]
    try:
        fields, lines, end = parse_frontmatter(master)
    except (OSError, ValueError) as exc:
        return [str(exc)]
    result: list[str] = []
    if fields.get("type") != "design_master":
        result.append("MASTER.md type must be design_master")
    status = fields.get("status")
    if status not in {"draft", "approved"}:
        result.append("MASTER.md status must be draft or approved")
    status_tags = [raw.strip()[2:] for raw in lines[1:end]
                   if raw.strip().startswith("- status/")]
    if status in {"draft", "approved"} and status_tags != [f"status/{status}"]:
        result.append("MASTER.md must carry exactly one status tag matching status")
    revision = fields.get("revision")
    if not isinstance(revision, int) or revision < 1:
        result.append("MASTER.md revision must be a positive integer")
    if isinstance(revision, int) and revision > 1 and not HASH_RE.fullmatch(
            str(fields.get("supersedes_hash", ""))):
        result.append("a later revision must carry a valid supersedes_hash")
    if status == "draft":
        if fields.get("approved_at_utc") or fields.get("baseline_hash"):
            result.append("a draft must not carry approval fields")
    elif status == "approved":
        stamp = str(fields.get("approved_at_utc", ""))
        try:
            approved = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if approved.tzinfo is None or approved > datetime.now(timezone.utc):
                raise ValueError
        except ValueError:
            result.append(
                "approved_at_utc must be a non-future ISO-8601 timestamp"
            )
        expected = str(fields.get("baseline_hash", ""))
        if not HASH_RE.fullmatch(expected):
            result.append("approved baseline_hash must be SHA-256")
        elif expected != baseline_hash(root):
            result.append("approved baseline_hash is stale")
    return result


def cmd_check(args) -> int:
    root = Path(args.root).resolve()
    problems = findings(root)
    for problem in problems:
        print(f"ERROR {root / 'MASTER.md'}:1 [design_system] {problem}")
    return 1 if problems else 0


def cmd_approve(args) -> int:
    root = Path(args.root).resolve()
    master = root / "MASTER.md"
    try:
        fields, _lines, _end = parse_frontmatter(master)
    except (OSError, ValueError) as exc:
        return fail(str(exc), 2)
    if fields.get("status") != "draft":
        return fail("only a draft Design System can be approved")
    problems = findings(root) + semantic_findings(root)
    if problems:
        return fail("; ".join(problems))
    digest = baseline_hash(root)
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    rewrite_frontmatter(master, {
        "status": "approved",
        "baseline_hash": digest,
        "approved_at_utc": stamp,
    }, set())
    print(
        f"design_system_compile: approved revision {fields['revision']}"
        f" ({digest})"
    )
    return 0


def cmd_begin_revision(args) -> int:
    root = Path(args.root).resolve()
    master = root / "MASTER.md"
    problems = findings(root)
    if problems:
        return fail("; ".join(problems))
    fields, _lines, _end = parse_frontmatter(master)
    if fields.get("status") != "approved":
        return fail("only an approved Design System can begin a new revision")
    previous = str(fields["baseline_hash"])
    revision = int(fields["revision"]) + 1
    rewrite_frontmatter(master, {
        "status": "draft",
        "revision": revision,
        "supersedes_hash": previous,
    }, {"approved_at_utc", "baseline_hash"})
    print(f"design_system_compile: began revision {revision}")
    return 0


def cmd_status(args) -> int:
    root = Path(args.root).resolve()
    master = root / "MASTER.md"
    try:
        fields, _lines, _end = parse_frontmatter(master)
    except (OSError, ValueError) as exc:
        return fail(str(exc), 2)
    digest = baseline_hash(root)
    current = fields.get("status") == "approved" and fields.get("baseline_hash") == digest
    print(json.dumps({
        "stage": "design-system", "result_ref": "design-system/MASTER",
        "result_type": "design-system-package", "package_hash": digest,
        "status": "approved" if current else "draft", "current": current,
    }, sort_keys=True))
    return 0 if current else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (
        ("check", cmd_check),
        ("approve", cmd_approve),
        ("begin-revision", cmd_begin_revision),
        ("status", cmd_status),
    ):
        command = sub.add_parser(name)
        command.add_argument("--root", required=True)
        command.set_defaults(func=handler)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
