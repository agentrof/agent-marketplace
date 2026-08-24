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
CATALOG_RELATIVE_PATH = Path("artifacts") / "standalone.html"
CATALOG_TEMPLATE = (Path(__file__).resolve().parent.parent / "skill-content"
                    / "design-system" / "data" / "standalone-template.html")
CATALOG_TOKEN_START = "/* catalog:tokens:start */"
CATALOG_TOKEN_END = "/* catalog:tokens:end */"
CATALOG_TOKEN_BLOCK_RE = re.compile(
    r"<!-- catalog:tokens:start -->\s*```css\s*(.*?)\s*```\s*"
    r"<!-- catalog:tokens:end -->", re.DOTALL)
CATALOG_BRAND_ASSETS_RE = re.compile(
    r"<!-- catalog:brand-assets:start -->\s*(.*?)\s*"
    r"<!-- catalog:brand-assets:end -->", re.DOTALL)
CATALOG_SECTIONS = (
    "sticky-header", "hero", "foundation", "brand", "content-voice", "color",
    "typography", "foundations", "components", "iconography", "accessibility", "footer",
)
CATALOG_SLOTS = (
    "project-lockup", "design-system-revision", "project-name", "design-posture",
    "foundation-card", "project-expression-card", "domain-additions-card",
    "core-system-rules", "project-layer-comparison", "brand-light-lockup",
    "brand-dark-lockup", "brand-clear-space", "brand-permitted-use",
    "brand-forbidden-use", "write-like-this", "avoid-writing", "primitive-scale",
    "semantic-themes", "accent-palette", "system-states", "type-scale",
    "weight-specimens", "specialized-text-roles", "spacing-scale", "radius-specimens",
    "layout-visualization", "elevation-specimens", "motion-specimens", "actions",
    "text-atoms", "surfaces-cards", "forms-inputs", "state-specimens",
    "domain-repeatable-specimens", "icon-family-specimens", "icon-rules",
    "accessibility-checklist", "source-binding", "catalog-disclaimer",
    "nav-foundation", "nav-brand", "nav-color", "nav-components",
    "nav-accessibility", "theme-toggle-label", "section-foundation",
    "section-brand", "section-content-voice", "section-color",
    "section-typography", "section-foundations", "section-components",
    "section-iconography", "section-accessibility", "document-title",
)
V3_HEADINGS = (
    "product position", "brand and asset fidelity", "global rules", "component specs",
    "style guidelines", "anti-patterns", "pre-delivery checklist", "navigation",
)
FORBIDDEN_TEMPLATE_TERMS = ("moneydorfin", "finance", "azure", "inter", "fluent")


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


def contract_version(root: Path) -> int:
    fields, _lines, _end = parse_frontmatter(root / "MASTER.md")
    try:
        return int(fields.get("contract_version", 0) or 0)
    except (TypeError, ValueError):
        return 0


def is_v3(root: Path) -> bool:
    return contract_version(root) >= 3


def master_source_hash(master: Path) -> str:
    content = without_generated_relations(normalized_master(master))
    return "sha256:" + hashlib.sha256(content).hexdigest()


def catalog_tokens(master: Path) -> str:
    text = master.read_text(encoding="utf-8")
    match = CATALOG_TOKEN_BLOCK_RE.search(text)
    if match is None:
        raise ValueError("MASTER.md is missing the machine-readable catalog token block")
    return match.group(1).strip() + "\n"


def catalog_brand_assets(master: Path) -> dict[str, str]:
    """Read explicit source paths and checksums; absent means no supplied asset."""
    match = CATALOG_BRAND_ASSETS_RE.search(master.read_text(encoding="utf-8"))
    if match is None:
        return {}
    assets: dict[str, str] = {}
    for path, checksum in re.findall(
            r"(?m)^\s*-\s*path:\s*([^\n]+)\n\s*sha256:\s*(sha256:[0-9a-f]{64})\s*$",
            match.group(1)):
        assets[path.strip()] = checksum
    return assets


def replace_marked(text: str, start: str, end: str, value: str) -> str:
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(text):
        raise ValueError(f"catalog is missing marker pair {start!r}")
    return pattern.sub(f"{start}\n{value.rstrip()}\n{end}", text, count=1)


def replace_meta(text: str, name: str, value: str) -> str:
    pattern = re.compile(
        rf'(<meta\s+name=["\']{re.escape(name)}["\']\s+content=["\'])[^"\']*(["\'])',
        re.I,
    )
    if not pattern.search(text):
        raise ValueError(f"catalog is missing {name} metadata")
    return pattern.sub(rf"\g<1>{value}\g<2>", text, count=1)


def catalog_path(root: Path) -> Path:
    return root / CATALOG_RELATIVE_PATH


def template_findings() -> list[str]:
    try:
        text = CATALOG_TEMPLATE.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"catalog template cannot be read: {exc}"]
    return [f"catalog template contains forbidden project-specific term '{term}'"
            for term in FORBIDDEN_TEMPLATE_TERMS
            if re.search(rf"\b{re.escape(term)}\b", text, re.I)]


def catalog_findings(root: Path) -> list[str]:
    """Validate the v3 offline catalog without interpreting its specimens."""
    master = root / "MASTER.md"
    fields, _lines, _end = parse_frontmatter(master)
    catalog = catalog_path(root)
    if not catalog.is_file():
        return [f"{CATALOG_RELATIVE_PATH.as_posix()} is required for contract_version: 3"]
    if catalog.is_symlink():
        return ["catalog must be a regular file inside design-system/artifacts"]
    try:
        text = catalog.read_text(encoding="utf-8")
        tokens = catalog_tokens(master)
        declared_assets = catalog_brand_assets(master)
    except (OSError, ValueError) as exc:
        return [str(exc)]
    result: list[str] = []
    expected_meta = {
        "design-system-contract-version": "3",
        "design-system-master-revision": str(fields.get("revision", "")),
        "design-system-master-source-hash": master_source_hash(master),
    }
    for name, expected in expected_meta.items():
        match = re.search(
            rf'<meta\s+name=["\']{re.escape(name)}["\']\s+content=["\']([^"\']*)',
            text, re.I)
        if match is None or match.group(1) != expected:
            result.append(f"catalog {name} is missing or stale")
    token_match = re.search(
        re.escape(CATALOG_TOKEN_START) + r"\s*(.*?)\s*" + re.escape(CATALOG_TOKEN_END),
        text, re.DOTALL)
    if token_match is None or token_match.group(1).strip() != tokens.strip():
        result.append("catalog token block does not match MASTER.md")
    positions = []
    for section in CATALOG_SECTIONS:
        match = re.search(rf'data-catalog-section=["\']{re.escape(section)}["\']', text)
        if match is None:
            result.append(f"catalog is missing required section '{section}'")
        else:
            positions.append(match.start())
    if positions and positions != sorted(positions):
        result.append("catalog sections are not in the required order")
    for slot in CATALOG_SLOTS:
        match = re.search(
            rf'<(?P<tag>[a-z0-9]+)[^>]*data-catalog-slot=["\']{re.escape(slot)}["\'][^>]*>'
            rf'(?P<content>.*?)</(?P=tag)>', text, re.I | re.DOTALL)
        if match is None or not re.sub(r"<[^>]+>", "", match.group("content")).strip() \
                or "AUTHOR_REQUIRED" in match.group("content"):
            result.append(f"catalog slot '{slot}' is empty")
    if re.search(r"(?:src|href)=[\"']https?://", text, re.I) or re.search(
            r"@import\s+(?:url\()?\s*[\"']?https?://", text, re.I):
        result.append("catalog must not load remote URLs, CDNs, or runtime dependencies")
    if not re.search(r'id=["\']theme-toggle["\']', text) or "aria-pressed" not in text:
        result.append("catalog needs an accessible light/dark theme toggle")
    if "prefers-reduced-motion" not in text:
        result.append("catalog needs a prefers-reduced-motion rule")
    if "@media" not in text:
        result.append("catalog needs responsive rules")
    ids = set(re.findall(r'\bid=["\']([^"\']+)', text, re.I))
    for target in re.findall(r'<a\b[^>]*\bhref=["\']#([^"\']+)', text, re.I):
        if target not in ids:
            result.append(f"catalog navigation target '#{target}' does not exist")
    embedded_assets = {}
    for asset, uri in re.findall(
            r'data-catalog-asset=["\']([^"\']+)["\'][^>]*\bsrc=["\'](data:[^"\']+)', text, re.I):
        source = (root.parent / asset).resolve()
        try:
            source.relative_to(root.parent.resolve())
            encoded = uri.split(",", 1)[1]
            import base64
            actual = hashlib.sha256(base64.b64decode(encoded, validate=True)).hexdigest()
            expected = hashlib.sha256(source.read_bytes()).hexdigest()
            embedded_assets[asset] = "sha256:" + actual
            if actual != expected or declared_assets.get(asset) != "sha256:" + expected:
                result.append(f"catalog brand asset checksum does not match '{asset}'")
        except Exception:
            result.append(f"catalog brand asset is invalid or escapes the vault: '{asset}'")
    for asset in declared_assets:
        if asset not in embedded_assets:
            result.append(f"catalog is missing declared brand asset '{asset}'")
    return result


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
    if is_v3(root):
        catalog = catalog_path(root)
        if catalog.is_file():
            if catalog.is_symlink():
                raise ValueError(f"symlinked Design System catalog: {catalog}")
            digest.update(CATALOG_RELATIVE_PATH.as_posix().encode())
            digest.update(b"\0")
            digest.update(catalog.read_bytes())
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
    version = contract_version(root)
    if version >= 3:
        for heading in V3_HEADINGS:
            if not re.search(rf"(?m)^##\s+{re.escape(heading)}\s*$", text):
                result.append(f"MASTER.md is missing required v3 section '{heading}'")
        try:
            catalog_tokens(master)
        except ValueError as exc:
            result.append(str(exc))
    else:
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
    if version >= 3:
        result.extend(catalog_findings(root))
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
    if contract_version(root) >= 3:
        result.extend(template_findings())
    return result


def cmd_check(args) -> int:
    root = Path(args.root).resolve()
    problems = findings(root) + semantic_findings(root)
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
        "contract_version": max(contract_version(root), 3),
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


def cmd_init_catalog(args) -> int:
    root = Path(args.root).resolve()
    master = root / "MASTER.md"
    if not master.is_file():
        return fail("MASTER.md is missing", 2)
    if not is_v3(root):
        return fail("init-catalog requires contract_version: 3", 2)
    catalog = catalog_path(root)
    if catalog.exists():
        return fail("catalog already exists; init-catalog never overwrites it", 2)
    try:
        text = CATALOG_TEMPLATE.read_text(encoding="utf-8")
        catalog.parent.mkdir(parents=True, exist_ok=True)
        catalog.write_text(text, encoding="utf-8")
        sync_catalog(root)
    except (OSError, ValueError) as exc:
        return fail(str(exc), 2)
    print(f"design_system_compile: initialized {catalog}")
    return 0


def sync_catalog(root: Path) -> None:
    master = root / "MASTER.md"
    if not is_v3(root):
        raise ValueError("sync-catalog requires contract_version: 3")
    fields, _lines, _end = parse_frontmatter(master)
    catalog = catalog_path(root)
    if not catalog.is_file():
        raise ValueError("catalog is missing; run init-catalog first")
    text = catalog.read_text(encoding="utf-8")
    text = replace_meta(text, "design-system-contract-version", "3")
    text = replace_meta(text, "design-system-master-revision", str(fields.get("revision", "")))
    text = replace_meta(text, "design-system-master-source-hash", master_source_hash(master))
    text = replace_marked(text, CATALOG_TOKEN_START, CATALOG_TOKEN_END, catalog_tokens(master))
    catalog.write_text(text, encoding="utf-8")


def cmd_sync_catalog(args) -> int:
    try:
        sync_catalog(Path(args.root).resolve())
    except (OSError, ValueError) as exc:
        return fail(str(exc), 2)
    print("design_system_compile: synchronized catalog bindings and tokens")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler in (
        ("check", cmd_check),
        ("init-catalog", cmd_init_catalog),
        ("sync-catalog", cmd_sync_catalog),
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
