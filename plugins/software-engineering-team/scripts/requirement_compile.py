#!/usr/bin/env python3
"""Compile and approve project-local Requirement records.

Requirement Markdown is the durable intake source for a new backlog delta.
The compiler owns identity, stage-impact shape, approval metadata, status tags,
semantic hashes and the generated requirements map. It deliberately has no
network or Git-writer behavior; the normal authoring branch and its handoff
remain the source-control boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from ba_compile import parse_frontmatter
import stage_package


TEAM = "software-engineering-team"
ID_RE = re.compile(r"^REQ-[0-9]{3,}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
STAGES = (
    "business-analysis", "solution-design", "design-system",
    "experience-design",
)
DISPOSITIONS = {"required", "reuse", "not_applicable"}
REQUEST_KINDS = {"feature", "defect", "technical"}
URGENCIES = {"low", "normal", "high", "critical"}
STATUSES = {"draft", "approved", "resolved_no_change", "superseded", "withdrawn"}
TERMINAL_STATUSES = {"resolved_no_change", "superseded", "withdrawn"}
MUTABLE_FIELDS = {"status", "approved_at_utc", "source_hash", "stage_results_hash"}
NAV_MARKER = "<!-- sec: nav -->"
MAP_MARKER = "<!-- requirement_compile.py: generated requirements -->"
REQUIRED_SECTIONS = (
    "Intent", "Outcome and Acceptance", "Scope and Non-Goals",
    "Evidence and Constraints", "Stage Impact", "Navigation",
)
PLACEHOLDER_RE = re.compile(
    r"(?im)^\s*(?:todo|tbd|replace this|describe |record |add evidence)\b"
)
WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]+)\]\]")
TABLE_ROW_RE = re.compile(r"^\|.*\|$")


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def docs_root(value: str | Path) -> Path:
    path = Path(value).resolve()
    if path.name == "docs":
        return path
    if (path / "docs").is_dir():
        return path / "docs"
    if (path / "workspace" / "docs").is_dir():
        return path / "workspace" / "docs"
    return path


def load_config(docs: Path) -> dict:
    try:
        value = json.loads((docs.parent / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def designation(docs: Path) -> str:
    values = load_config(docs).get("doc_type_designations", {})
    if isinstance(values, dict):
        value = values.get("requirement")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "requirement"


def typed_title(docs: Path, base: str) -> str:
    suffix = designation(docs)
    title = base.strip()
    folded_title = unicodedata.normalize("NFKC", title).casefold()
    folded_suffix = unicodedata.normalize("NFKC", suffix).casefold()
    start = len(folded_title) - len(folded_suffix)
    if (start >= 0 and folded_title.endswith(folded_suffix)
            and (start == 0 or not (folded_title[start - 1].isalnum()
                                    or folded_title[start - 1] == "_"))):
        return title
    return f"{title} {suffix}".strip()


def split_note(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    props, body_line, error = parse_frontmatter(text)
    if error:
        raise ValueError(error)
    lines = text.splitlines()
    body = "\n".join(lines[body_line - 1:]).lstrip("\n")
    if text.endswith("\n"):
        body += "\n"
    return props, body


def scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if (not text or text != text.strip() or ": " in text
            or text.startswith("[[") or text.lower() in {"true", "false", "null"}):
        return json.dumps(text, ensure_ascii=False)
    return text


def render_note(props: dict, body: str) -> str:
    rows = ["---"]
    for key, value in props.items():
        if isinstance(value, list):
            rows.append(f"{key}:")
            rows.extend(f"  - {scalar(item)}" for item in value)
        else:
            rows.append(f"{key}: {scalar(value)}")
    rows.extend(["---", "", body.rstrip(), ""])
    return "\n".join(rows)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def valid_utc(value: object) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0


def requirement_paths(docs: Path) -> list[Path]:
    root = docs / "requirements"
    return sorted(path for path in root.glob("req-*.md") if path.is_file()) \
        if root.is_dir() else []


def requirement_id(path: Path) -> str:
    try:
        props, _ = split_note(path)
    except (OSError, ValueError):
        return ""
    return str(props.get("id", ""))


def next_id(docs: Path) -> str:
    values = []
    for path in requirement_paths(docs):
        match = re.fullmatch(r"REQ-([0-9]{3,})", requirement_id(path))
        if match:
            values.append(int(match.group(1)))
    return f"REQ-{(max(values, default=0) + 1):03d}"


def split_slug(path: Path) -> str:
    match = re.fullmatch(r"req-[0-9]+-(.+)\.md", path.name)
    return match.group(1) if match else ""


def section_text(body: str, heading: str) -> str:
    match = re.search(
        rf"(?ms)^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", body
    )
    return match.group(1).strip() if match else ""


def authored_body(body: str) -> str:
    value = body.split(NAV_MARKER, 1)[0]
    value = re.sub(
        r"(?ms)^## Stage Results(?:\s+<!--.*?-->)?\s*\n.*?(?=^## |\Z)",
        "", value,
    )
    return value.rstrip() + "\n"


def semantic_hash(props: dict, body: str) -> str:
    stable = {
        key: value for key, value in props.items() if key not in MUTABLE_FIELDS
    }
    tags = stable.get("tags")
    if isinstance(tags, list):
        stable["tags"] = [
            tag for tag in tags
            if not (isinstance(tag, str) and tag.startswith("status/"))
        ]
    payload = json.dumps(
        {"frontmatter": stable, "body": authored_body(body)},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def stage_results(body: str) -> dict[str, list[tuple[str, str]]]:
    match = re.search(r"(?ms)^## Stage Results(?:\s+<!--.*?-->)?\s*\n(.*?)(?=^## |\Z)", body)
    section = match.group(1).strip() if match else ""
    results: dict[str, list[tuple[str, str]]] = {}
    for line in section.splitlines():
        if not line.startswith("|") or line.lstrip().startswith("|---"):
            continue
        cells = split_cells(line)
        if len(cells) == 3 and cells[0].casefold() != "stage":
            stage, reference, digest = (cell.strip() for cell in cells)
            if stage in STAGES and reference and digest:
                results.setdefault(stage, []).append((reference, digest))
    return results


def stage_results_body(body: str, results: dict[str, list[tuple[str, str]]]) -> str:
    rows = ["## Stage Results <!-- compiler-owned -->", "", "| stage | result_ref | result_hash |", "|---|---|---|"]
    for stage in STAGES:
        entries = results.get(stage, [])
        if entries:
            rows.extend(f"| {stage} | {reference} | {digest} |"
                        for reference, digest in entries)
        else:
            rows.append(f"| {stage} |  |  |")
    generated = "\n".join(rows) + "\n\n"
    pattern = r"(?ms)^## Stage Results(?:\s+<!--.*?-->)?\s*\n.*?(?=^## |\Z)"
    if re.search(pattern, body):
        body = re.sub(pattern, generated, body)
    else:
        marker = "## Navigation <!-- sec: nav -->"
        body = body.replace(marker, generated + marker, 1)
    return body


def bind_stage(path: Path, stage: str, result_refs: list[str] | str,
               expected_hash: str = "") -> None:
    """Write one compiler-owned receipt after resolving the exact package.

    A result is never accepted merely because a note at a similar path is
    approved.  The shared resolver owns the stage/type/path/hash contract.
    Rebinding a predecessor invalidates every downstream receipt.
    """
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    props, body = split_note(path)
    if props.get("status") != "approved":
        raise ValueError("Stage Results can only bind to an approved Requirement")
    raw_refs = [result_refs] if isinstance(result_refs, str) else result_refs
    targets = []
    for raw in raw_refs:
        target = raw.strip()
        if target.startswith("[[") and target.endswith("]]" ):
            target = target[2:-2].split("|", 1)[0]
        if target:
            targets.append(target)
    if not targets or len(set(targets)) != len(targets):
        raise ValueError("bind-stage needs one or more unique result references")
    rows = {row[0]: row for row in impact_rows(body)}
    disposition, reuse_refs = rows[stage][1], rows[stage][2]
    if disposition == "not_applicable":
        raise ValueError(f"{stage} is not_applicable and cannot receive a receipt")
    if disposition == "reuse" and set(targets) != set(reuse_refs):
        raise ValueError(f"{stage} reuse receipt set must match Stage Impact references")
    if stage != "experience-design" and len(targets) != 1:
        raise ValueError(f"{stage} accepts exactly one package receipt")
    docs = path.parents[1]
    receipts = []
    for target in targets:
        receipt, errors = stage_package.verify(
            docs, stage, target, expected_hash, require_committed=True,
        )
        if errors or receipt is None:
            raise ValueError("; ".join(errors or ["invalid stage package"]))
        receipts.append(receipt)
    results = stage_results(body)
    results[stage] = [(str(receipt["result_ref"]), str(receipt["package_hash"]))
                      for receipt in receipts]
    # A changed predecessor invalidates every dependent receipt, even where
    # their old hash happened to match coincidentally.
    for downstream in STAGES[STAGES.index(stage) + 1:]:
        results.pop(downstream, None)
    body = stage_results_body(body, results)
    props["stage_results_hash"] = "sha256:" + hashlib.sha256(
        json.dumps(results, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    atomic_text(path, render_note(props, body))


def split_cells(line: str) -> list[str]:
    raw = line.strip().strip("|")
    cells: list[str] = []
    current: list[str] = []
    link_depth = 0
    index = 0
    while index < len(raw):
        if raw.startswith("[[", index):
            link_depth += 1
            current.extend("[[")
            index += 2
            continue
        if raw.startswith("]]", index) and link_depth:
            link_depth -= 1
            current.extend("]]" )
            index += 2
            continue
        char = raw[index]
        if char == "|" and link_depth == 0:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    cells.append("".join(current).strip())
    return cells


def impact_rows(body: str) -> list[tuple[str, str, list[str], str]]:
    text = section_text(body, "Stage Impact")
    rows = []
    for line in text.splitlines():
        if not TABLE_ROW_RE.match(line) or line.lstrip().startswith("|---"):
            continue
        cells = split_cells(line)
        if len(cells) != 4 or cells[0].casefold() == "stage":
            continue
        refs = [
            match.group(1).split("|", 1)[0].split("#", 1)[0].strip()
            for match in WIKILINK_RE.finditer(cells[2])
        ]
        # A Stage Impact receipt is an exact package ref, not necessarily a
        # vault link.  Keep plain canonical refs machine-readable too.
        raw_refs = cells[2].strip()
        if not refs and raw_refs and raw_refs not in {"-", "—", "none", "n/a"}:
            refs = [value.strip() for value in cells[2].split(",") if value.strip()]
        rows.append((cells[0], cells[1], refs, cells[3]))
    return rows


def map_content(docs: Path) -> str:
    links = []
    for path in requirement_paths(docs):
        props, _ = split_note(path)
        title = str(props.get("title", path.stem))
        links.append(f"- [[requirements/{path.stem}|{title}]]")
    return (
        "---\n"
        "type: moc\n"
        "title: Requirements\n"
        "tags:\n"
        "  - doc/moc\n"
        "---\n\n"
        "# Requirements\n\n"
        "Approved and in-progress Requirement records.\n\n"
        "## Records\n\n"
        f"{MAP_MARKER}\n"
        + ("\n".join(links) + "\n" if links else "")
    )


def render_navigation(docs: Path) -> int:
    changed = 0
    map_path = docs / "maps" / "requirements.md"
    expected = map_content(docs)
    current = map_path.read_text(encoding="utf-8") if map_path.is_file() else ""
    if current != expected:
        atomic_text(map_path, expected)
        changed += 1
    home = docs / "home.md"
    if home.is_file():
        text = home.read_text(encoding="utf-8")
        link = "[[maps/requirements|Requirements]]"
        if link not in text:
            atomic_text(home, text.rstrip() + "\n\n" + link + "\n")
            changed += 1
    for path in requirement_paths(docs):
        props, body = split_note(path)
        nav = "\n\n## Navigation " + NAV_MARKER + "\n\n"
        nav += "- [[maps/requirements|Requirements]]\n"
        updated = authored_body(body).rstrip() + nav
        rendered = render_note(props, updated)
        if rendered != path.read_text(encoding="utf-8"):
            atomic_text(path, rendered)
            changed += 1
    return changed


def identity_findings(docs: Path) -> list[str]:
    owners: dict[str, list[str]] = {}
    for path in requirement_paths(docs):
        identifier = requirement_id(path)
        if ID_RE.fullmatch(identifier):
            owners.setdefault(identifier, []).append(path.name)
    return [
        f"Requirement identity {identifier} has multiple owners: {', '.join(paths)}"
        for identifier, paths in sorted(owners.items()) if len(paths) > 1
    ]


def requirement_findings(path: Path, require_approved: bool = False) -> list[str]:
    findings: list[str] = []
    try:
        props, body = split_note(path)
    except (OSError, ValueError) as exc:
        return [f"{path}: {exc}"]
    docs = path.parents[1] if path.parent.name == "requirements" else path.parent
    if path.parent != docs / "requirements":
        findings.append("Requirement must live under workspace/docs/requirements")
    identifier = props.get("id")
    if not isinstance(identifier, str) or not ID_RE.fullmatch(identifier):
        findings.append("id must match REQ-###")
    slug = split_slug(path)
    if not SLUG_RE.fullmatch(slug) or len(slug) > 48:
        findings.append("path slug must be lower-kebab and at most 48 characters")
    if isinstance(identifier, str) and ID_RE.fullmatch(identifier):
        expected = f"req-{int(identifier[4:]):03d}-{slug}.md"
        if path.name != expected:
            findings.append("path must use the lower-case zero-padded Requirement id")
    if props.get("type") != "requirement":
        findings.append("type must be requirement")
    title = props.get("title")
    if not isinstance(title, str) or not title.strip():
        findings.append("title is required")
    else:
        if typed_title(docs, title) != title:
            findings.append("title must end with the configured requirement designation")
        if not re.search(rf"(?m)^# {re.escape(title)}\s*$", body):
            findings.append("the first H1 must match title")
    if props.get("owner_role") != "product_owner":
        findings.append("owner_role must be product_owner")
    if props.get("request_kind") not in REQUEST_KINDS:
        findings.append("request_kind must be feature, defect or technical")
    if props.get("urgency") not in URGENCIES:
        findings.append("urgency must be low, normal, high or critical")
    status = props.get("status")
    if status not in STATUSES:
        findings.append("status is not a legal Requirement status")
    aliases = props.get("aliases")
    if not isinstance(aliases, list) or aliases != [identifier]:
        findings.append("aliases must contain exactly the Requirement id")
    tags = props.get("tags")
    if not isinstance(tags, list) or "doc/requirement" not in tags:
        findings.append("tags must contain doc/requirement")
    if not isinstance(tags, list) or f"status/{status}" not in tags:
        findings.append("tags must mirror the Requirement status")
    derives = props.get("derives_from", [])
    if not isinstance(derives, list):
        findings.append("derives_from must be a list when present")
    for heading in REQUIRED_SECTIONS[:-1]:
        content = section_text(body, heading)
        if not content:
            findings.append(f"section is missing or empty: {heading}")
        elif PLACEHOLDER_RE.search(content):
            findings.append(f"section still contains placeholder text: {heading}")
    rows = impact_rows(body)
    if len(rows) != len(STAGES):
        findings.append("Stage Impact must contain exactly one row per knowledge stage")
    else:
        seen = set()
        for stage, disposition, refs, rationale in rows:
            if stage in seen or stage not in STAGES:
                findings.append(f"Stage Impact has an unknown or duplicate stage: {stage}")
            seen.add(stage)
            if disposition not in DISPOSITIONS:
                findings.append(f"invalid disposition for {stage}: {disposition}")
            if disposition == "required" and refs:
                findings.append(f"{stage} required must have an empty reuse_refs set")
            if disposition == "reuse" and (not refs or (stage != "experience-design" and len(refs) != 1)):
                findings.append(f"{stage} reuse requires one package reference, or one-or-more Experience packages")
            if disposition == "not_applicable" and refs:
                findings.append(f"{stage} not_applicable must have an empty evidence set")
            if not rationale.strip() or PLACEHOLDER_RE.search(rationale):
                findings.append(f"{stage} needs a concrete impact rationale")
        missing = sorted(set(STAGES) - seen)
        if missing:
            findings.append("Stage Impact is missing: " + ", ".join(missing))
        by_stage = {stage: disposition for stage, disposition, _refs, _why in rows}
        prerequisites = {
            "solution-design": ("business-analysis",),
            "design-system": ("business-analysis", "solution-design"),
            "experience-design": ("business-analysis", "solution-design", "design-system"),
        }
        for dependent, parents in prerequisites.items():
            if by_stage.get(dependent) != "not_applicable":
                absent = [parent for parent in parents
                          if by_stage.get(parent) == "not_applicable"]
                if absent:
                    findings.append(
                        f"{dependent} cannot apply while prerequisite stage(s) are not_applicable: "
                        + ", ".join(absent)
                    )
    if NAV_MARKER not in body or "[[maps/requirements|Requirements]]" not in body.split(NAV_MARKER, 1)[1]:
        findings.append("Navigation must start from maps/requirements")
    if status in {"approved", "resolved_no_change", "superseded", "withdrawn"} or require_approved:
        if status not in {"approved", "resolved_no_change", "superseded", "withdrawn"}:
            findings.append("Requirement is not approved")
        if not valid_utc(props.get("approved_at_utc")):
            findings.append("approved_at_utc must be a UTC timestamp")
        if props.get("source_hash") != semantic_hash(props, body):
            findings.append("approved source_hash is stale")
    results = stage_results(body)
    if props.get("stage_results_hash"):
        expected_results_hash = "sha256:" + hashlib.sha256(
            json.dumps(results, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if props.get("stage_results_hash") != expected_results_hash:
            findings.append("stage_results_hash is stale")
    return findings


def package_findings(docs: Path) -> list[str]:
    return identity_findings(docs)


def requirement_incorporated(docs: Path, identifier: str) -> bool:
    """Return the one compiler-owned backlog incorporation predicate.

    A Requirement is incorporated only when an approved root backlog review
    names a non-empty current Story set for it and each named Story links back
    to the exact Requirement. Older approved backlogs without the new coverage
    projection therefore remain historically valid but are not treated as
    newly incorporated by this flow.
    """
    if not ID_RE.fullmatch(identifier):
        return False
    reviews = sorted((docs / "backlog" / "reviews").glob("round-*-backlog-review.md"))
    approved_review = None
    for review in reversed(reviews):
        try:
            props, body = split_note(review)
        except (OSError, ValueError):
            continue
        if props.get("status") == "approved":
            approved_review = body
            break
    if approved_review is None:
        return False
    story_paths = sorted((docs / "backlog" / "epics").glob("*/stories/*/story.md"))
    linked: list[str] = []
    needle = f"requirements/req-{int(identifier[4:]):03d}-"
    for story in story_paths:
        try:
            props, _ = split_note(story)
        except (OSError, ValueError):
            continue
        links = props.get("implements", [])
        if not isinstance(links, list):
            continue
        if any(isinstance(value, str) and needle in value for value in links):
            aliases = props.get("aliases", [])
            if isinstance(aliases, list) and len(aliases) == 1 and isinstance(aliases[0], str):
                linked.append(aliases[0])
    if not linked:
        return False
    coverage = re.search(
        rf"(?m)^\|\s*{re.escape(identifier)}\s*\|([^\n]+)$", approved_review
    )
    if coverage is None:
        return False
    return all(story_id in coverage.group(1) for story_id in linked)


def create_requirement(docs: Path, slug: str, title: str, request_kind: str,
                       urgency: str, identifier: str | None,
                       derives_from: list[str]) -> Path:
    docs = docs.resolve()
    if not SLUG_RE.fullmatch(slug) or len(slug) > 48:
        raise ValueError("slug must be lower-kebab and at most 48 characters")
    if request_kind not in REQUEST_KINDS:
        raise ValueError("request_kind must be feature, defect or technical")
    if urgency not in URGENCIES:
        raise ValueError("urgency must be low, normal, high or critical")
    identifier = (identifier or next_id(docs)).upper()
    if not ID_RE.fullmatch(identifier):
        raise ValueError("id must match REQ-###")
    if any(requirement_id(path) == identifier for path in requirement_paths(docs)):
        raise ValueError(f"Requirement id is already owned: {identifier}")
    path = docs / "requirements" / f"req-{int(identifier[4:]):03d}-{slug}.md"
    if path.exists():
        raise FileExistsError(f"Requirement already exists: {path}")
    title = typed_title(docs, title)
    props = {
        "type": "requirement", "id": identifier, "title": title,
        "status": "draft", "owner_role": "product_owner",
        "request_kind": request_kind, "urgency": urgency, "revision": 1,
        "tags": ["doc/requirement", "status/draft"], "aliases": [identifier],
    }
    if derives_from:
        props["derives_from"] = derives_from
    rows = "\n".join(
        f"| {stage} | required |  | TODO: explain why this stage must change. |"
        for stage in STAGES
    )
    body = (
        f"# {title}\n\n"
        "## Intent\n\nTODO: state the requested change and who needs it.\n\n"
        "## Outcome and Acceptance\n\nTODO: state the observable outcome and acceptance boundary.\n\n"
        "## Scope and Non-Goals\n\nTODO: define included and excluded behavior.\n\n"
        "## Evidence and Constraints\n\nTODO: record evidence, constraints and urgency rationale.\n\n"
        "## Stage Impact\n\n"
        "| stage | disposition | reuse_refs | rationale |\n"
        "|---|---|---|---|\n" + rows + "\n\n"
        "## Stage Results <!-- compiler-owned -->\n\n"
        "| stage | result_ref | result_hash |\n"
        "|---|---|---|\n"
        + "\n".join(f"| {stage} |  |  |" for stage in STAGES) + "\n\n"
        "## Navigation <!-- sec: nav -->\n\n"
        "- [[maps/requirements|Requirements]]\n"
    )
    atomic_text(path, render_note(props, body))
    render_navigation(docs)
    return path


def approve_requirement(path: Path) -> None:
    findings = requirement_findings(path)
    docs = path.parents[1]
    findings.extend(package_findings(docs))
    if findings:
        raise ValueError("; ".join(findings))
    props, body = split_note(path)
    if props.get("status") != "draft":
        raise ValueError("only a draft Requirement can be approved")
    original = path.read_text(encoding="utf-8")
    props["status"] = "approved"
    props["approved_at_utc"] = utc_now()
    props["source_hash"] = semantic_hash(props, body)
    props["tags"] = [
        tag for tag in props.get("tags", [])
        if isinstance(tag, str) and not tag.startswith("status/")
    ] + ["status/approved"]
    atomic_text(path, render_note(props, body))
    closing = requirement_findings(path, require_approved=True)
    if closing:
        atomic_text(path, original)
        raise ValueError("approval closing check failed: " + "; ".join(closing))


def begin_revision(path: Path) -> None:
    """Open a semantic Requirement revision and invalidate every receipt."""
    props, body = split_note(path)
    if props.get("status") != "approved":
        raise ValueError("only an approved Requirement can begin a revision")
    findings = requirement_findings(path, require_approved=True)
    if findings:
        raise ValueError("cannot revise invalid Requirement: " + "; ".join(findings))
    props["status"] = "draft"
    props["revision"] = int(props.get("revision", 1) or 1) + 1
    for key in ("approved_at_utc", "source_hash", "stage_results_hash"):
        props.pop(key, None)
    props["tags"] = [
        tag for tag in props.get("tags", [])
        if isinstance(tag, str) and not tag.startswith("status/")
    ] + ["status/draft"]
    body = stage_results_body(body, {})
    atomic_text(path, render_note(props, body))


def transition_terminal(path: Path, status: str, reason: str,
                        evidence: list[str]) -> None:
    if status not in {"resolved_no_change", "withdrawn"}:
        raise ValueError("unsupported terminal Requirement transition")
    props, body = split_note(path)
    current = props.get("status")
    if current not in {"draft", "approved"}:
        raise ValueError("only a nonterminal draft or approved Requirement can transition")
    if current == "draft" and status != "withdrawn":
        raise ValueError("a draft Requirement may only be withdrawn")
    if not reason.strip():
        raise ValueError("a terminal transition requires a concrete reason")
    if current == "draft":
        if not is_committed(path):
            raise ValueError("a draft Requirement must reach Git before withdrawal")
    docs = path.parents[1]
    identifier = str(props.get("id", ""))
    if requirement_incorporated(docs, identifier):
        raise ValueError("an incorporated Requirement cannot be withdrawn or resolved in place")
    findings = requirement_findings(path, require_approved=(current == "approved"))
    if findings:
        raise ValueError("; ".join(findings))
    evidence_text = section_text(body, "Evidence and Constraints")
    prefix = evidence_text.rstrip()
    label = "Resolution" if status == "resolved_no_change" else "Withdrawal"
    addition = f"\n\n### {label}\n\n{reason.strip()}"
    if evidence:
        addition += "\n\nEvidence:\n" + "\n".join(f"- {item}" for item in evidence)
    updated_evidence = prefix + addition
    body = re.sub(
        r"(?ms)(^## Evidence and Constraints\s*\n).*?(?=^## Stage Impact\s*$)",
        lambda match: match.group(1) + updated_evidence.rstrip() + "\n\n",
        body,
    )
    props["status"] = status
    props["approved_at_utc"] = utc_now()
    props["source_hash"] = semantic_hash(props, body)
    props["tags"] = [
        tag for tag in props.get("tags", [])
        if isinstance(tag, str) and not tag.startswith("status/")
    ] + [f"status/{status}"]
    original = path.read_text(encoding="utf-8")
    atomic_text(path, render_note(props, body))
    closing = requirement_findings(path, require_approved=True)
    if closing:
        atomic_text(path, original)
        raise ValueError("terminal transition closing check failed: " + "; ".join(closing))


def is_committed(path: Path) -> bool:
    """Return whether the exact Requirement path has no uncommitted Git bytes."""
    try:
        root = Path(subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path.parent, text=True, stderr=subprocess.DEVNULL,
        ).strip())
    except (OSError, subprocess.CalledProcessError):
        return False
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--", str(path)],
        cwd=root, text=True, capture_output=True, check=False,
    )
    return result.returncode == 0 and not result.stdout.strip()


def discard_requirement(path: Path) -> None:
    """Discard one exact uncommitted draft and regenerate its navigation map."""
    props, _body = split_note(path)
    if props.get("status") != "draft":
        raise ValueError("only a draft Requirement can be discarded")
    if is_committed(path):
        raise ValueError("a committed draft must be withdrawn, not discarded")
    docs = path.parents[1]
    if requirement_incorporated(docs, str(props.get("id", ""))):
        raise ValueError("a Requirement with downstream coverage cannot be discarded")
    path.unlink()
    render_navigation(docs)


def supersede_requirement(old_path: Path, replacement_path: Path) -> None:
    """Approve a relation-bound replacement and terminalize the old record atomically."""
    docs = old_path.parents[1]
    if replacement_path.parents[1] != docs:
        raise ValueError("old and replacement Requirements must belong to the same project vault")
    old_props, old_body = split_note(old_path)
    replacement_props, _ = split_note(replacement_path)
    if old_props.get("status") != "approved":
        raise ValueError("only an approved Requirement can be superseded")
    if replacement_props.get("status") != "draft":
        raise ValueError("the replacement must be an exact reviewed draft")
    if old_path == replacement_path:
        raise ValueError("a Requirement cannot supersede itself")
    old_id = str(old_props.get("id", ""))
    relation = replacement_props.get("supersedes")
    relation_values = relation if isinstance(relation, list) else [relation]
    if not any(old_id == str(value) or old_path.stem in str(value)
               for value in relation_values if value is not None):
        raise ValueError("replacement must contain the approved supersedes relation before approval")
    replacement_findings = requirement_findings(replacement_path)
    if replacement_findings:
        raise ValueError("replacement is not approvable: " + "; ".join(replacement_findings))
    old_original = old_path.read_text(encoding="utf-8")
    replacement_original = replacement_path.read_text(encoding="utf-8")
    try:
        approve_requirement(replacement_path)
        replacement_props, _ = split_note(replacement_path)
        replacement_id = str(replacement_props.get("id", ""))
        old_props["status"] = "superseded"
        old_props["superseded_by"] = f"[[requirements/{replacement_path.stem}|{replacement_id}]]"
        old_props["approved_at_utc"] = utc_now()
        old_props["source_hash"] = semantic_hash(old_props, old_body)
        old_props["tags"] = [
            tag for tag in old_props.get("tags", [])
            if isinstance(tag, str) and not tag.startswith("status/")
        ] + ["status/superseded"]
        atomic_text(old_path, render_note(old_props, old_body))
        if requirement_findings(old_path, require_approved=True):
            raise ValueError("superseded Requirement closing check failed")
    except Exception:
        atomic_text(old_path, old_original)
        atomic_text(replacement_path, replacement_original)
        raise
    render_navigation(docs)


def status_requirement(path: Path) -> dict:
    props, _body = split_note(path)
    docs = path.parents[1]
    incorporated = requirement_incorporated(docs, str(props.get("id", "")))
    return {
        "ok": True,
        "id": props.get("id"),
        "status": props.get("status"),
        "incorporated": incorporated,
        "path": str(path),
        "findings": requirement_findings(path, require_approved=props.get("status") in TERMINAL_STATUSES),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--docs", required=True)
    init.add_argument("--slug", required=True)
    init.add_argument("--title", required=True)
    init.add_argument("--request-kind", choices=sorted(REQUEST_KINDS), required=True)
    init.add_argument("--urgency", choices=sorted(URGENCIES), default="normal")
    init.add_argument("--id")
    init.add_argument("--derives-from", action="append", default=[])
    check = sub.add_parser("check")
    check.add_argument("--docs", required=True)
    check.add_argument("--requirement")
    check.add_argument("--approved", action="store_true")
    check.add_argument("--render", action="store_true")
    check.add_argument("--json", action="store_true")
    approve = sub.add_parser("approve")
    approve.add_argument("--requirement", required=True)
    render = sub.add_parser("render")
    render.add_argument("--docs", required=True)
    terminal = sub.add_parser("resolve-no-change")
    terminal.add_argument("--requirement", required=True)
    terminal.add_argument("--reason", required=True)
    terminal.add_argument("--evidence", action="append", default=[])
    withdraw = sub.add_parser("withdraw")
    withdraw.add_argument("--requirement", required=True)
    withdraw.add_argument("--reason", required=True)
    withdraw.add_argument("--evidence", action="append", default=[])
    discard = sub.add_parser("discard")
    discard.add_argument("--requirement", required=True)
    supersede = sub.add_parser("supersede")
    supersede.add_argument("--requirement", required=True)
    supersede.add_argument("--replacement", required=True)
    status = sub.add_parser("status")
    status.add_argument("--requirement", required=True)
    status.add_argument("--json", action="store_true")
    bind = sub.add_parser("bind-stage")
    bind.add_argument("--requirement", required=True)
    bind.add_argument("--stage", choices=STAGES, required=True)
    bind.add_argument("--result-ref", action="append", required=True)
    bind.add_argument("--expected-hash", default="")
    # Kept as a compatibility spelling, but never trusted over resolver output.
    bind.add_argument("--result-hash", default="")
    revision = sub.add_parser("begin-revision")
    revision.add_argument("--requirement", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            path = create_requirement(
                docs_root(args.docs), args.slug, args.title,
                args.request_kind, args.urgency, args.id, args.derives_from,
            )
            print(f"requirement_compile: created {path}")
            return 0
        if args.command == "render":
            changed = render_navigation(docs_root(args.docs))
            print(f"requirement_compile: rendered navigation ({changed} write(s))")
            return 0
        if args.command == "approve":
            approve_requirement(Path(args.requirement).resolve())
            print(f"requirement_compile: approved {args.requirement}")
            return 0
        if args.command in {"resolve-no-change", "withdraw"}:
            transition_terminal(
                Path(args.requirement).resolve(),
                "resolved_no_change" if args.command == "resolve-no-change" else "withdrawn",
                args.reason, args.evidence,
            )
            print(f"requirement_compile: {args.command} completed")
            return 0
        if args.command == "discard":
            discard_requirement(Path(args.requirement).resolve())
            print(f"requirement_compile: discarded {args.requirement}")
            return 0
        if args.command == "supersede":
            supersede_requirement(
                Path(args.requirement).resolve(), Path(args.replacement).resolve()
            )
            print(f"requirement_compile: superseded {args.requirement}")
            return 0
        if args.command == "status":
            payload = status_requirement(Path(args.requirement).resolve())
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"{payload['id']}: {payload['status']}")
            return 0 if not payload["findings"] else 1
        if args.command == "bind-stage":
            bind_stage(Path(args.requirement).resolve(), args.stage,
                       args.result_ref, args.expected_hash or args.result_hash)
            print(f"requirement_compile: bound {args.stage}")
            return 0
        if args.command == "begin-revision":
            begin_revision(Path(args.requirement).resolve())
            print(f"requirement_compile: began revision for {args.requirement}")
            return 0
        docs = docs_root(args.docs)
        if args.render:
            render_navigation(docs)
        paths = [Path(args.requirement).resolve()] if args.requirement \
            else requirement_paths(docs)
        findings = [
            finding for path in paths
            for finding in requirement_findings(path, require_approved=args.approved)
        ]
        findings.extend(package_findings(docs))
        if not paths:
            findings.append("no Requirement records found")
        payload = {"ok": not findings, "requirements": len(paths), "findings": findings}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        elif findings:
            for finding in findings:
                print(f"requirement_compile: FAIL: {finding}")
        else:
            print(f"requirement_compile: OK: {len(paths)} Requirement(s)")
        return 1 if findings else 0
    except (OSError, ValueError) as exc:
        print(f"requirement_compile: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
