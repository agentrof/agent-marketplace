#!/usr/bin/env python3
"""Create, validate, approve and navigate project issue reports.

Issue reports are ordinary project-vault Markdown. This compiler owns only
their stable shape, approval stamp and navigation projection; external filing
is deliberately isolated in ``file_issue.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from ba_compile import parse_frontmatter


REPORT_ID_RE = re.compile(r"^ISSUE-[0-9]{3,}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
KINDS = {"defect", "improvement"}
STATUSES = {"draft", "approved", "filed"}
TEAM = "software-engineering-team"
FILED_URL_RE = re.compile(
    r"^https://github\.com/agentrof/agent-marketplace/issues/[0-9]+/?$"
)
REQUIRED_SECTIONS = (
    "Summary",
    "Reproduction or Motivation",
    "Expected Behavior",
    "Actual Behavior",
    "Impact and Severity",
    "Evidence",
    "Proposed Next Action",
)
PLACEHOLDER_RE = re.compile(
    r"(?im)^\s*(?:todo|tbd|replace this|describe |record |add evidence)\b"
)
NAV_MARKER = "<!-- sec: nav -->"
MAP_MARKER = "<!-- issue-report compiler: report links -->"
MUTABLE_FIELDS = {
    "status", "approved_at_utc", "source_hash", "external_url",
    "filed_at_utc",
}


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f"{path.name}.", dir=path.parent)
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


def scalar(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if (not text or text != text.strip() or ": " in text or text.startswith("[[")
            or text.lower() in {"true", "false", "null"}):
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


def load_config(docs: Path) -> dict:
    path = docs.parent / "config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def designation(docs: Path) -> str:
    values = load_config(docs).get("doc_type_designations", {})
    if isinstance(values, dict):
        value = values.get("issue-report")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "issue report"


def typed_title(docs: Path, base: str) -> str:
    suffix = designation(docs)
    title = base.strip()
    folded_title = unicodedata.normalize("NFKC", title).casefold()
    folded_suffix = unicodedata.normalize("NFKC", suffix).casefold()
    start = len(folded_title) - len(folded_suffix)
    present = (
        start >= 0 and folded_title.endswith(folded_suffix)
        and (start == 0 or not (folded_title[start - 1].isalnum()
                                or folded_title[start - 1] == "_"))
    )
    return title if present else f"{title} {suffix}"


def managed_docs_findings(docs: Path) -> list[str]:
    if docs.name != "docs" or docs.parent.name != "workspace":
        return ["report must live under <project>/workspace/docs/issues"]
    config = load_config(docs)
    if config.get("team_id") != TEAM:
        return [f"workspace config team_id must be {TEAM}"]
    return []


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def valid_utc(value) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    offset = parsed.utcoffset()
    return offset is not None and offset.total_seconds() == 0


def authored_body(body: str) -> str:
    return body.split(NAV_MARKER, 1)[0].rstrip() + "\n"


def semantic_hash(props: dict, body: str) -> str:
    stable = {
        key: value for key, value in props.items()
        if key not in MUTABLE_FIELDS
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


def section_text(body: str, heading: str) -> str:
    match = re.search(
        rf"(?ms)^## {re.escape(heading)}\s*$\n(.*?)(?=^## |\Z)", body
    )
    return match.group(1).strip() if match else ""


def report_findings(path: Path, require_approved: bool = False) -> list[str]:
    findings: list[str] = []
    try:
        props, body = split_note(path)
    except (OSError, ValueError) as exc:
        return [f"{path}: {exc}"]
    docs = path.parents[1] if path.parent.name == "issues" else path.parent
    findings.extend(managed_docs_findings(docs))
    if path.parent != docs / "issues" or not SLUG_RE.fullmatch(path.stem):
        findings.append(
            "report path must be workspace/docs/issues/<kebab-slug>.md"
        )
    if props.get("type") != "issue-report":
        findings.append("type must be issue-report")
    title = props.get("title")
    if not isinstance(title, str) or not title.strip():
        findings.append("title is required")
    elif typed_title(docs, title) != title:
        findings.append("title must end with the configured issue-report designation")
    if props.get("issue_kind") not in KINDS:
        findings.append("issue_kind must be defect or improvement")
    if props.get("owner_role") != "product_owner":
        findings.append("owner_role must be product_owner")
    status = props.get("status")
    if status not in STATUSES:
        findings.append("status is not a legal issue-report status")
    aliases = props.get("aliases")
    ids = [value for value in aliases or []
           if isinstance(value, str) and REPORT_ID_RE.fullmatch(value)] \
        if isinstance(aliases, list) else []
    if len(ids) != 1 or not isinstance(aliases, list) or len(aliases) != 1:
        findings.append("aliases must contain exactly one ISSUE-### identity")
    tags = props.get("tags")
    if not isinstance(tags, list) or "doc/issue-report" not in tags:
        findings.append("tags must contain doc/issue-report")
    if not isinstance(tags, list) or f"status/{status}" not in tags:
        findings.append("tags must mirror the issue-report status")
    if isinstance(title, str) and not re.search(
            rf"(?m)^# {re.escape(title)}\s*$", body):
        findings.append("the first H1 must match title")
    for heading in REQUIRED_SECTIONS:
        content = section_text(body, heading)
        if not content:
            findings.append(f"section is missing or empty: {heading}")
        elif PLACEHOLDER_RE.search(content):
            findings.append(f"section still contains placeholder text: {heading}")
    if NAV_MARKER not in body:
        findings.append("navigation section is missing")
    elif "[[maps/issues|Issue reports]]" not in body.split(NAV_MARKER, 1)[1]:
        findings.append("navigation must start from maps/issues")
    if status in {"approved", "filed"} or require_approved:
        if status not in {"approved", "filed"}:
            findings.append("report is not approved")
        if not valid_utc(props.get("approved_at_utc")):
            findings.append("approved_at_utc must be a UTC timestamp")
        if props.get("source_hash") != semantic_hash(props, body):
            findings.append("approved source_hash is stale")
    if status == "filed":
        if not valid_utc(props.get("filed_at_utc")):
            findings.append("filed_at_utc must be a UTC timestamp")
        external = props.get("external_url")
        if not isinstance(external, str) or FILED_URL_RE.fullmatch(external) is None:
            findings.append(
                "filed report needs a canonical agentrof/agent-marketplace "
                "GitHub issue URL"
            )
    return findings


def status_tags(props: dict, status: str) -> None:
    tags = props.get("tags")
    retained = [tag for tag in tags or []
                if isinstance(tag, str) and not tag.startswith("status/")] \
        if isinstance(tags, list) else []
    props["tags"] = retained + [f"status/{status}"]


def report_paths(docs: Path) -> list[Path]:
    issues = docs / "issues"
    return sorted(path for path in issues.glob("*.md") if path.is_file()) \
        if issues.is_dir() else []


def map_content(paths: list[Path]) -> str:
    links = []
    for path in paths:
        props, _ = split_note(path)
        title = str(props.get("title", path.stem))
        links.append(f"- [[issues/{path.stem}|{title}]]")
    return (
        "---\ntype: moc\ntitle: Issue reports\ntags:\n  - doc/moc\n---\n\n"
        "# Issue reports\n\n"
        "Tracked defects and improvements owned by the Software Engineering Team.\n\n"
        "## Reports\n\n" + MAP_MARKER + "\n"
        + ("\n".join(links) + "\n" if links else "")
    )


def navigation_findings(docs: Path) -> list[str]:
    """Require one exact project-level issue index and home entry."""
    paths = report_paths(docs)
    findings: list[str] = []
    map_path = docs / "maps" / "issues.md"
    expected = map_content(paths)
    try:
        actual = map_path.read_text(encoding="utf-8")
    except OSError:
        actual = ""
    if actual != expected:
        findings.append(
            "maps/issues.md is missing or stale; run issue_compile.py render"
        )
    home = docs / "home.md"
    try:
        home_text = home.read_text(encoding="utf-8")
    except OSError:
        home_text = ""
    link = "[[maps/issues|Issue reports]]"
    if home_text.count(link) != 1:
        findings.append(
            "home.md must contain exactly one [[maps/issues|Issue reports]] link"
        )
    return findings


def identity_findings(docs: Path) -> list[str]:
    """Enforce ISSUE identities across the package, not only during init."""
    owners: dict[str, list[str]] = {}
    for path in report_paths(docs):
        try:
            props, _ = split_note(path)
        except (OSError, ValueError):
            continue
        aliases = props.get("aliases")
        if not isinstance(aliases, list):
            continue
        for value in aliases:
            if isinstance(value, str) and REPORT_ID_RE.fullmatch(value):
                owners.setdefault(value, []).append(
                    path.relative_to(docs).as_posix()
                )
    return [
        f"issue identity {identifier} has multiple owners: "
        + ", ".join(paths)
        for identifier, paths in sorted(owners.items()) if len(paths) != 1
    ]


def package_findings(docs: Path) -> list[str]:
    return identity_findings(docs) + navigation_findings(docs)


def render_navigation(docs: Path) -> int:
    paths = report_paths(docs)
    changed = 0
    if not paths:
        return changed
    map_path = docs / "maps" / "issues.md"
    expected = map_content(paths)
    current = map_path.read_text(encoding="utf-8") if map_path.is_file() else ""
    if current != expected:
        atomic_text(map_path, expected)
        changed += 1
    home = docs / "home.md"
    if home.is_file():
        text = home.read_text(encoding="utf-8")
        link = "[[maps/issues|Issue reports]]"
        if link not in text:
            atomic_text(home, text.rstrip() + "\n\n" + link + "\n")
            changed += 1
    for path in paths:
        props, body = split_note(path)
        base = authored_body(body).rstrip()
        updated = base + (
            "\n\n## Links " + NAV_MARKER + "\n\n"
            "- [[maps/issues|Issue reports]]\n"
        )
        rendered = render_note(props, updated)
        if rendered != path.read_text(encoding="utf-8"):
            atomic_text(path, rendered)
            changed += 1
    return changed


def create_report(docs: Path, slug: str, base_title: str, issue_kind: str,
                  report_id: str) -> Path:
    docs = docs.resolve()
    contract_findings = managed_docs_findings(docs)
    if contract_findings:
        raise ValueError("; ".join(contract_findings))
    if not SLUG_RE.fullmatch(slug):
        raise ValueError("slug must be lowercase kebab-case")
    report_id = report_id.upper()
    if not REPORT_ID_RE.fullmatch(report_id):
        raise ValueError("id must match ISSUE-###")
    if issue_kind not in KINDS:
        raise ValueError("kind must be defect or improvement")
    path = docs / "issues" / f"{slug}.md"
    if path.exists():
        raise FileExistsError(f"report already exists: {path}")
    for existing in report_paths(docs):
        props, _ = split_note(existing)
        if report_id in (props.get("aliases") or []):
            raise ValueError(f"report id is already owned: {report_id}")
    title = typed_title(docs, base_title)
    props = {
        "type": "issue-report",
        "title": title,
        "status": "draft",
        "issue_kind": issue_kind,
        "owner_role": "product_owner",
        "tags": ["doc/issue-report", "status/draft"],
        "aliases": [report_id],
    }
    prompts = {
        "Summary": "TODO: state the problem or improvement in one testable paragraph.",
        "Reproduction or Motivation": "TODO: give exact reproduction steps or the improvement motivation.",
        "Expected Behavior": "TODO: state the observable expected behavior.",
        "Actual Behavior": "TODO: state the observed behavior or current limitation.",
        "Impact and Severity": "TODO: name affected users, scope and justified severity.",
        "Evidence": "TODO: add logs, commands, files or screenshots without secrets.",
        "Proposed Next Action": "TODO: propose triage, backlog intake or closure.",
    }
    body = f"# {title}\n\n" + "\n\n".join(
        f"## {heading}\n\n{prompts[heading]}" for heading in REQUIRED_SECTIONS
    )
    atomic_text(path, render_note(props, body))
    render_navigation(docs)
    return path


def approve_report(path: Path) -> None:
    findings = report_findings(path)
    docs = path.parents[1] if path.parent.name == "issues" else path.parent
    findings.extend(package_findings(docs))
    if findings:
        raise ValueError("; ".join(findings))
    props, body = split_note(path)
    if props.get("status") != "draft":
        raise ValueError("only a draft issue report can be approved")
    original = path.read_text(encoding="utf-8")
    props["status"] = "approved"
    props["approved_at_utc"] = utc_now()
    status_tags(props, "approved")
    props["source_hash"] = semantic_hash(props, body)
    atomic_text(path, render_note(props, body))
    closing = report_findings(path, require_approved=True)
    if closing:
        atomic_text(path, original)
        raise ValueError("approval closing check failed: " + "; ".join(closing))


def mark_filed(path: Path, url: str) -> None:
    if FILED_URL_RE.fullmatch(url) is None:
        raise ValueError("external filing returned no canonical marketplace issue URL")
    findings = report_findings(path, require_approved=True)
    docs = path.parents[1] if path.parent.name == "issues" else path.parent
    findings.extend(package_findings(docs))
    if findings:
        raise ValueError("; ".join(findings))
    props, body = split_note(path)
    if props.get("status") != "approved":
        raise ValueError("only an approved, not-yet-filed report can be filed")
    original = path.read_text(encoding="utf-8")
    props["status"] = "filed"
    props["external_url"] = url
    props["filed_at_utc"] = utc_now()
    status_tags(props, "filed")
    atomic_text(path, render_note(props, body))
    closing = report_findings(path, require_approved=True)
    if closing:
        atomic_text(path, original)
        raise ValueError("filing closing check failed: " + "; ".join(closing))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--docs", required=True)
    init.add_argument("--slug", required=True)
    init.add_argument("--title", required=True)
    init.add_argument("--kind", choices=sorted(KINDS), required=True)
    init.add_argument("--id", required=True)
    check = sub.add_parser("check")
    check.add_argument("--docs", required=True)
    check.add_argument("--report")
    check.add_argument("--render", action="store_true")
    approve = sub.add_parser("approve")
    approve.add_argument("--report", required=True)
    render = sub.add_parser("render")
    render.add_argument("--docs", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            path = create_report(
                docs_root(args.docs), args.slug, args.title, args.kind, args.id
            )
            print(f"issue_compile: created {path}")
        elif args.command == "check":
            docs = docs_root(args.docs)
            if args.render:
                render_navigation(docs)
            paths = [Path(args.report).resolve()] if args.report \
                else report_paths(docs)
            findings = [
                finding for path in paths for finding in report_findings(path)
            ]
            findings.extend(package_findings(docs))
            if not paths:
                findings.append("no issue reports found")
            if findings:
                for finding in findings:
                    print(f"issue_compile: FAIL: {finding}")
                return 1
            print(f"issue_compile: OK: {len(paths)} report(s)")
        elif args.command == "approve":
            approve_report(Path(args.report).resolve())
            print(f"issue_compile: approved {args.report}")
        else:
            changed = render_navigation(docs_root(args.docs))
            print(f"issue_compile: rendered navigation ({changed} write(s))")
    except (OSError, ValueError) as exc:
        print(f"issue_compile: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
