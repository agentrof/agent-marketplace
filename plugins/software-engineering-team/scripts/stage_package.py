#!/usr/bin/env python3
"""Shared product-stage package resolver.

Every stage handoff uses this module instead of trusting a neighbouring
document's ``status`` field.  It resolves only canonical package roots and
checks their lifecycle stamp, generated view and package hash.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import re
import subprocess
from pathlib import Path

from ba_compile import parse_frontmatter


STAGES = {
    "business-analysis": "business-analysis-package",
    "solution-design": "solution-design-package",
    "design-system": "design-system-package",
    "experience-design": "experience-package",
    "backlog-plan": "backlog-package",
}

BA_PROCESS_REF = "business-analysis/{space}/processes/{slug}-process"


def docs_root(value: str | Path) -> Path:
    path = Path(value).resolve()
    if path.name == "docs":
        return path
    if (path / "workspace" / "docs").is_dir():
        return path / "workspace" / "docs"
    if (path / "docs").is_dir():
        return path / "docs"
    return path


def frontmatter(path: Path) -> dict:
    try:
        props, _line, error = parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    return props if not error else {}


def tree_hash(root: Path, omitted_fields: set[str]) -> str:
    """Hash authored source plus generated registry JSON deterministically.

    The registry is a package artefact for BA, so its absence or drift must
    change the receipt.  Other generated JSON stays excluded deliberately:
    it is not part of a stage's published source contract.
    """
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.md")):
        if path.is_symlink():
            raise ValueError(f"symlinked package source: {path}")
        text = path.read_text(encoding="utf-8")
        props, body_line, error = parse_frontmatter(text)
        if not error and props:
            lines = text.splitlines()
            kept = ["---"]
            for raw in lines[1:body_line - 2]:
                if raw.partition(":")[0].strip() not in omitted_fields:
                    kept.append(raw)
            kept.extend(lines[body_line - 1:])
            text = "\n".join(kept).rstrip() + "\n"
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(text.encode())
        digest.update(b"\0")
    registry = root / "_generated" / "registry.json"
    if registry.is_file():
        digest.update(registry.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(registry.read_bytes())
        digest.update(b"\0")
    capability = root / "_generated" / "capability-registry.json"
    if capability.is_file():
        digest.update(capability.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(capability.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def is_committed(package_root: Path) -> bool:
    """Check the whole published package, not only its overview note."""
    root = next((p for p in (package_root, *package_root.parents)
                 if (p / ".git").exists()), package_root.parent)
    result = subprocess.run(["git", "status", "--porcelain=v1", "--",
                             str(package_root)], cwd=root, capture_output=True,
                            text=True, check=False)
    return result.returncode == 0 and not result.stdout.strip()


def receipt(stage: str, ref: str, result_type: str, package_hash: str,
            approved: bool, current: bool, path: Path, package_root: Path,
            verification_profile: str = "invalid") -> dict:
    return {
        "stage": stage, "result_ref": ref, "result_type": result_type,
        "package_hash": package_hash, "status": "approved" if approved else "draft",
        "current": current, "committed": is_committed(package_root),
        "path": str(path), "verification_profile": verification_profile,
        "legacy": verification_profile == "legacy-readonly",
    }


def ba_candidates(docs: Path) -> list[dict]:
    root = docs / "business-analysis"
    found = []
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        note = folder / "space.md"
        if not folder.is_dir() or not note.is_file():
            continue
        props = frontmatter(note)
        compiler_ok = False
        try:
            import ba_compile
            digest = ba_compile.package_hash(folder)
            schema = ba_compile.load_schema(ba_compile.DEFAULT_SCHEMA)
            space, base = ba_compile.scan_space(folder, schema)
            space.vault_root = docs
            findings = ba_compile.run_checks(space, base, gate=True)
            if not space.broken:
                warnings = [item for item in findings if item.severity == "warning"]
                findings += ba_compile.freshness_findings(space, warnings)
            compiler_ok = not any(item.severity == "error" for item in findings)
        except (ImportError, OSError, ValueError):
            digest = tree_hash(folder, {"package_hash", "package_status", "package_approved_at_utc",
                                        "package_contract_version"})
        authored = [p for p in folder.rglob("*.md") if "_generated" not in p.parts]
        version = int(props.get("package_contract_version", 0) or 0)
        strict_current = (props.get("package_status") == "approved"
                          and props.get("package_hash") == digest
                          and version >= 2 and compiler_ok)
        legacy = ((not props.get("package_status") and bool(authored) and all(
            frontmatter(p).get("status") in {"approved", "superseded"} for p in authored))
            or (props.get("package_status") == "approved"
                and props.get("package_hash") == digest and version < 2))
        current = strict_current or legacy
        found.append(receipt("business-analysis", f"business-analysis/{folder.name}/space",
                             "business-analysis-package", digest, current or legacy,
                             current or legacy, note, folder,
                             "strict-current" if strict_current else "legacy-readonly" if legacy else "invalid"))
    return found


def solution_candidates(docs: Path) -> list[dict]:
    root = docs / "solution-design"
    note = root / "landscape.md"
    if not note.is_file():
        return []
    props = frontmatter(note)
    compiler_ok = False
    try:
        import landscape_check
        digest = landscape_check.package_hash(root)
        # The stage resolver must consume the Solution compiler's current
        # verdict, not merely a matching frontmatter hash.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            compiler_ok = landscape_check.main(["check", "--tree", str(root)]) == 0
    except (ImportError, OSError, ValueError):
        digest = tree_hash(root, {"package_hash", "package_status", "package_approved_at_utc"})
    engagements = list((root / "engagements").glob("*.md")) if (root / "engagements").is_dir() else []
    # Strict handoffs require the v3 allocation-universe contract. Earlier
    # approved landscapes remain available only through legacy-readonly.
    modern = int(props.get("topology_contract_version", 0) or 0) >= 3
    legacy = ((not props.get("package_status") and bool(engagements) and all(
        "Status: open" not in p.read_text(encoding="utf-8") for p in engagements))
        or (props.get("package_status") == "approved"
            and props.get("package_hash") == digest and not modern))
    # Pre-topology-contract approved packages remain inspectable and reusable
    # until their first revision. New topology packages must pass the complete
    # compiler check, not merely present a matching package hash.
    strict_current = (props.get("package_status") == "approved"
                      and props.get("package_hash") == digest
                      and modern and compiler_ok)
    current = strict_current or legacy
    return [receipt("solution-design", "solution-design/landscape", "solution-design-package",
                    digest, current or legacy, current or legacy, note, root,
                    "strict-current" if strict_current else "legacy-readonly" if legacy else "invalid")]


def design_candidates(docs: Path) -> list[dict]:
    root = docs / "design-system"
    note = root / "MASTER.md"
    if not note.is_file():
        return []
    props = frontmatter(note)
    design_system_compile = None
    try:
        import design_system_compile
        digest = design_system_compile.baseline_hash(root)
    except (ImportError, OSError, ValueError):
        digest = tree_hash(root, {"baseline_hash", "approved_at_utc"})
    basic = (props.get("status") == "approved"
             and props.get("baseline_hash") == digest)
    modern = bool(design_system_compile and (props.get("contract_version")
                  or design_system_compile.relation_values(note, "derives_from")
                  or design_system_compile.relation_values(note, "constrained_by")))
    semantic_ok = False
    if basic and modern:
        try:
            semantic_ok = not (design_system_compile.findings(root)
                               + design_system_compile.semantic_findings(root))
        except (AttributeError, OSError, ValueError):
            semantic_ok = False
    approved = basic and (semantic_ok or not modern)
    return [receipt("design-system", "design-system/MASTER", "design-system-package",
                    digest, approved, approved, note, root,
                    "strict-current" if basic and modern and semantic_ok
                    else "legacy-readonly" if basic and not modern else "invalid")]


def experience_candidates(docs: Path) -> list[dict]:
    root = docs / "experience-design" / "experiences"
    found = []
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        note = folder / "experience.md"
        if not folder.is_dir() or not note.is_file():
            continue
        try:
            import experience_compile
            compiled, problems = experience_compile.compile_package(folder, True)
            props = frontmatter(note)
            package_hash = str(compiled.get("package_hash", ""))
            revision = props.get("revision")
            approved = not problems and isinstance(revision, int) and revision > 0
        except (ImportError, OSError, ValueError):
            props = frontmatter(note)
            package_hash = ""
            revision = props.get("revision")
            approved = False
        if approved:
            found.append(receipt("experience-design",
                                 f"{folder.name}@r{revision}",
                                 "experience-package", package_hash,
                                 approved, approved, note, folder,
                                 "strict-current" if approved else "invalid"))
    return found


def backlog_candidates(docs: Path) -> list[dict]:
    note = docs / "backlog" / "backlog.md"
    if not note.is_file():
        return []
    props = frontmatter(note)
    try:
        import backlog_compile
        record, errors = backlog_compile.collect(docs)
        errors.extend(backlog_compile.approval_findings(record, docs))
        digest = (str(record["backlog"]["props"].get("package_hash", ""))
                  if record.get("backlog") else "")
        approved = not errors and digest.startswith("sha256:")
    except (ImportError, OSError, RuntimeError, ValueError):
        digest = str(props.get("package_hash", ""))
        approved = False
    return [receipt("backlog-plan", "backlog/backlog", "backlog-package",
                    digest, approved, approved, note, docs / "backlog",
                    "strict-current" if approved else "invalid")]


def canonical_ba_process_ref(value: str) -> tuple[str, str, str] | None:
    """Return ``(canonical_ref, space, slug)`` for a root BA process note."""
    raw = value.strip().removesuffix(".md")
    match = re.fullmatch(
        r"business-analysis/([a-z0-9]+(?:-[a-z0-9]+)*)/processes/"
        r"([a-z0-9]+(?:-[a-z0-9]+)*)-process", raw,
    )
    if match is None:
        return None
    space, slug = match.groups()
    return BA_PROCESS_REF.format(space=space, slug=slug), space, slug


def resolve_ba_process(docs: Path, value: str, *, expected_ba_ref: str = "",
                       expected_ba_hash: str = "",
                       require_committed: bool = False,
                       require_strict_current: bool = False) -> tuple[str | None, list[str]]:
    """Resolve a process through the exact BA package that contains it."""
    parsed = canonical_ba_process_ref(value)
    if parsed is None:
        return None, ["primary_process_ref must be business-analysis/<space>/processes/<slug>-process"]
    canonical, space, _slug = parsed
    expected = f"business-analysis/{space}/space"
    receipt_value, errors = verify(
        docs, "business-analysis", expected, expected_ba_hash,
        require_committed, require_strict_current,
    )
    if expected_ba_ref and expected_ba_ref != expected:
        errors.append("primary_process_ref must belong to the bound Business Analysis package")
    process = (docs / canonical).with_suffix(".md")
    if not process.is_file():
        errors.append("primary_process_ref does not resolve to a Business Analysis process note")
        return None, errors
    props = frontmatter(process)
    if props.get("type") != "process":
        errors.append("primary_process_ref must resolve to a process note")
    if receipt_value and receipt_value.get("verification_profile") == "strict-current" and props.get("status") != "approved":
        errors.append("primary_process_ref must resolve to an approved process in a current BA package")
    return canonical, errors


def candidates(docs: Path, stage: str) -> list[dict]:
    if stage not in STAGES:
        raise ValueError(f"unsupported stage: {stage}")
    return {
        "business-analysis": ba_candidates,
        "solution-design": solution_candidates,
        "design-system": design_candidates,
        "experience-design": experience_candidates,
        "backlog-plan": backlog_candidates,
    }[stage](docs)


def verify(docs: Path, stage: str, ref: str, expected_hash: str = "",
           require_committed: bool = False,
           require_strict_current: bool = False) -> tuple[dict | None, list[str]]:
    matches = [item for item in candidates(docs, stage) if item["result_ref"] == ref]
    if not matches:
        return None, [f"{stage} receipt must use its canonical result_ref, got {ref}"]
    item = matches[0]
    errors = []
    if item["status"] != "approved" or not item["current"]:
        errors.append(f"{ref} is not an approved/current {stage} package")
    if expected_hash and item["package_hash"] != expected_hash:
        errors.append(f"{ref} package hash is stale or does not match expected hash")
    if require_strict_current and item.get("verification_profile") != "strict-current":
        errors.append(f"{ref} is legacy-readonly; begin a revision before using it as a new {stage} handoff")
    if require_committed and not item["committed"]:
        errors.append(f"{ref} has uncommitted package changes")
    return item, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("candidates", "verify"):
        command = sub.add_parser(name)
        command.add_argument("--docs", required=True)
        command.add_argument("--stage", choices=sorted(STAGES), required=True)
        command.add_argument("--json", action="store_true")
        if name == "verify":
            command.add_argument("--ref", required=True)
            command.add_argument("--expected-hash", default="")
            command.add_argument("--require-committed", action="store_true")
            command.add_argument("--strict-current", action="store_true")
    args = parser.parse_args(argv)
    docs = docs_root(args.docs)
    try:
        if args.command == "candidates":
            result, errors = {"stage": args.stage, "candidates": candidates(docs, args.stage)}, []
        else:
            value, errors = verify(docs, args.stage, args.ref, args.expected_hash,
                                   args.require_committed, args.strict_current)
            result = {"stage": args.stage, "receipt": value, "errors": errors}
    except (OSError, ValueError) as exc:
        result, errors = {"errors": [str(exc)]}, [str(exc)]
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
