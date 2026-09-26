#!/usr/bin/env python3
"""Offline compiler for the Delivery knowledge model.

This module deliberately stops at semantic files. It never creates a branch,
worktree, remote ref or provider object; those mutations belong to the later
delivery_git coordinator and are only legal after these projections pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from ba_compile import parse_frontmatter, without_generated_relations
import backlog_compile
import operation_compile
import stage_package


DELIVERY_ID_RE = re.compile(r"^DLV-[0-9]{3,}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
STORY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
STATUSES = {
    "scope_proposed", "scope_approved", "execution_approved", "active",
    "review", "pr_handoff", "awaiting_merge", "merged", "cancelled",
}
ITEM_STATUSES = {"in_scope", "claimed", "active", "blocked", "paused", "integrated", "cancelled"}
# A terminal Item keeps the Operation bindings its evidence was produced against.
TERMINAL_ITEM_STATUSES = {"integrated", "cancelled"}
SECTIONS = {
    "delivery": (
        "Goal", "Observable Outcome", "Scope Rationale", "Exclusions",
        "Dependency Preconditions", "Definition of Done Baseline",
        "Risks and Conflict Summary", "User Decisions", "Navigation",
    ),
    "execution-plan": (
        "Preconditions", "Item Graph", "Execution Waves", "Role Sequences",
        "Path Claims", "Contract Claims", "Integration Order",
        "Verification Strategy", "Failure and Recovery", "Approval", "Navigation",
    ),
    "item": (
        "Delivery Scope", "Execution Steps", "Role Responsibilities",
        "Implementation Evidence", "Definition of Done Evidence",
        "Blocking or Pause Reason", "Deviations and Follow-ups",
        "Integration Handoff", "Navigation",
    ),
    "delivery-review": (
        "Goal Outcome", "Scope Disposition", "Definition of Done Evidence",
        "Integrated Quality Evidence", "Demonstration and Acceptance",
        "Deviations", "Lessons and Follow-up", "PR Decision", "Findings",
        "Verdict", "Navigation",
    ),
}
DOD_SECTIONS = ("Commands", "Evidence Rules", "Quality Gates", "Navigation")
MUTABLE = {"status", "approved_at_utc", "source_hash", "approval_hash", "pull_request_url"}
SOURCE_ITEM_FIELDS = (
    "story_id", "story_path", "story_source_hash", "test_plan_path",
    "test_plan_source_hash", "owner_role", "supporting_roles",
)
OPERATION_BINDING_FIELDS = (
    "verification_contract_ref", "verification_contract_hash",
    "environment_contract_ref", "environment_contract_hash",
)
DOD_SOURCE_FIELDS = (
    "definition_of_done_path", "definition_of_done_revision",
    "definition_of_done_source_hash",
)
GIT_OID_RE = re.compile(r"^[0-9a-f]{40,64}$")


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def docs_root(value: str | Path) -> Path:
    path = Path(value).resolve()
    if path.name == "docs":
        return path
    if (path / "docs").is_dir():
        return path / "docs"
    if (path / "workspace" / "docs").is_dir():
        return path / "workspace" / "docs"
    return path


def scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if (not text or text != text.strip() or ": " in text or text.startswith("[[")
            or text.lower() in {"true", "false", "null"}):
        return json.dumps(text, ensure_ascii=False)
    return text


def frontmatter(props: dict, body: str) -> str:
    rows = ["---"]
    for key, value in props.items():
        if isinstance(value, list):
            rows.append(f"{key}:")
            rows.extend(f"  - {scalar(item)}" for item in value)
        else:
            rows.append(f"{key}: {scalar(value)}")
    rows.extend(["---", "", body.rstrip(), ""])
    return "\n".join(rows)


def split_note(path: Path) -> tuple[dict, str]:
    props, body_line, error = parse_frontmatter(path.read_text(encoding="utf-8"))
    if error:
        raise ValueError(error)
    lines = path.read_text(encoding="utf-8").splitlines()
    body = "\n".join(lines[body_line - 1:]).lstrip("\n")
    return props, body


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sections(body: str) -> set[str]:
    return {match.group(1).replace("<!-- sec: nav -->", "").strip()
            for match in re.finditer(r"(?m)^## (.+?)\s*$", body)}


def content_hash(props: dict, body: str, *, exclude: set[str] | None = None) -> str:
    excluded = MUTABLE if exclude is None else exclude
    stable = {key: value for key, value in props.items() if key not in excluded}
    if isinstance(stable.get("tags"), list):
        stable["tags"] = [tag for tag in stable["tags"]
                           if not (isinstance(tag, str) and tag.startswith("status/"))]
    payload = json.dumps({"frontmatter": stable,
                          "body": without_generated_relations(body).rstrip() + "\n"},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


SECTION_PLACEHOLDER = "Record compiler-owned evidence here."


def section_bodies(body: str) -> dict[str, str]:
    """Each section's text keyed by its title, without the navigation marker."""
    headings = list(re.finditer(r"(?m)^## (.+?)\s*$", body))
    return {heading.group(1).replace("<!-- sec: nav -->", "").strip():
            body[heading.end():following.start() if following else len(body)].strip()
            for heading, following in zip(headings, [*headings[1:], None])}


def body_for(kind: str, heading: str, values: dict[str, str] | None = None) -> str:
    values = values or {}
    lines = [f"# {heading}", ""]
    for section in SECTIONS[kind]:
        content = values.get(section, SECTION_PLACEHOLDER)
        marker = ""
        if section == "Navigation":
            marker = " <!-- sec: nav -->"
            owning_map = link("maps/delivery", "Delivery map")
            if not content.startswith(owning_map):
                content = owning_map + "\n" + content
        lines.extend([f"## {section}{marker}", "", content, ""])
    return "\n".join(lines)


def delivery_root(docs: Path) -> Path:
    return docs / "delivery"


def delivery_dirs(docs: Path) -> list[Path]:
    root = delivery_root(docs) / "deliveries"
    return sorted(path for path in root.glob("dlv-*") if path.is_dir()) if root.is_dir() else []


def next_delivery_id(docs: Path) -> str:
    numbers = []
    for path in delivery_dirs(docs):
        match = re.match(r"dlv-([0-9]+)-", path.name)
        if match:
            numbers.append(int(match.group(1)))
    return f"DLV-{max(numbers, default=0) + 1:03d}"


def id_slug(identifier: str) -> str:
    return identifier.lower()


def delivery_path(docs: Path, identifier: str, slug: str) -> Path:
    return delivery_root(docs) / "deliveries" / f"{id_slug(identifier)}-{slug}"


def find_delivery(docs: Path, identifier: str) -> Path | None:
    for directory in delivery_dirs(docs):
        path = directory / "delivery.md"
        if not path.exists():
            continue
        try:
            props, _ = split_note(path)
        except (OSError, ValueError):
            continue
        if props.get("id") == identifier:
            return directory
    return None


def approved_backlog_sources(
    docs: Path,
    story_ids: list[str],
    *,
    historical_inputs: bool = False,
) -> tuple[dict[str, dict], dict, list[str]]:
    """Resolve the exact approved Story/Test Plan snapshots a Delivery may use.

    Delivery is deliberately a consumer of the canonical backlog.  It must not
    accept caller-provided hashes or treat a generated registry as a source of
    truth, so this resolver checks the authored package and its approval stamps
    before exposing one selected Story.
    """
    errors: list[str] = []
    if not story_ids:
        return {}, {}, ["Delivery must select at least one backlog Story"]
    if any(not isinstance(value, str) or not STORY_RE.fullmatch(value) for value in story_ids):
        return {}, {}, ["story ids must be stable project IDs"]
    if len(story_ids) != len(set(story_ids)):
        return {}, {}, ["Delivery cannot select the same Story more than once"]
    try:
        with stage_package.candidate_session(), backlog_compile.experience_validation_session():
            record, findings = backlog_compile.collect(
                docs, historical_inputs=historical_inputs,
            )
    except (OSError, RuntimeError, ValueError) as exc:
        return {}, {}, [f"approved backlog cannot be read: {exc}"]
    errors.extend(findings)
    if record.get("backlog") is None:
        errors.append("approved backlog is missing")
    elif not errors:
        errors.extend(backlog_compile.approval_findings(record, docs))
    if errors:
        return {}, {}, sorted(set(f"approved backlog: {error}" for error in errors))

    stories = {str(story["id"]): story for story in record["stories"]}
    story_by_path = {
        str(story["path"]).removesuffix(".md"): str(story["id"])
        for story in record["stories"]
    }
    selected: dict[str, dict] = {}
    for story_id in story_ids:
        story = stories.get(story_id)
        if story is None:
            errors.append(f"selected Story is absent from approved backlog: {story_id}")
            continue
        props = story["props"]
        test_props = story["test_props"]
        story_hash = props.get("source_hash")
        test_hash = test_props.get("source_hash")
        if not isinstance(story_hash, str) or not story_hash:
            errors.append(f"{story_id} has no approved Story source_hash")
        if not isinstance(test_hash, str) or not test_hash:
            errors.append(f"{story_id} has no approved Test Plan source_hash")
        owner = props.get("owner_role")
        if not isinstance(owner, str) or not owner:
            errors.append(f"{story_id} has no accountable implementation owner")
        dependencies = []
        for target in story.get("dependency_targets", []):
            dependency = story_by_path.get(str(target))
            if dependency is None:
                errors.append(f"{story_id} has an unresolved approved dependency: {target}")
            else:
                dependencies.append(dependency)
        selected[story_id] = {
            "story_id": story_id,
            "story_path": str(story["path"]),
            "story_source_hash": story_hash,
            "test_plan_path": str(story["test_plan"]),
            "test_plan_source_hash": test_hash,
            "owner_role": owner,
            "supporting_roles": backlog_compile.values(props, "supporting_roles"),
            "depends_on": sorted(set(dependencies)),
            "work_kind": str(story.get("work_kind", "")),
        }
    if errors:
        return {}, {}, sorted(set(errors))
    backlog_props = record["backlog"]["props"]
    snapshot = {
        "backlog_path": str(record["backlog"]["path"]),
        "backlog_package_hash": str(backlog_props.get("package_hash", "")),
    }
    return selected, snapshot, []


def approved_dod_source(docs: Path) -> tuple[dict, list[str]]:
    """Return the one current approved Definition of Done snapshot."""
    path = delivery_root(docs) / "definition-of-done.md"
    errors = check_dod(path)
    if errors:
        return {}, [f"Definition of Done: {error}" for error in errors]
    props, _ = split_note(path)
    if props.get("status") != "approved":
        return {}, ["Definition of Done must be approved"]
    source_hash = props.get("source_hash")
    if not isinstance(source_hash, str) or not source_hash:
        return {}, ["approved Definition of Done has no source_hash"]
    revision = props.get("revision")
    if not isinstance(revision, int) or revision < 1:
        return {}, ["approved Definition of Done has an invalid revision"]
    return {
        "definition_of_done_path": path.relative_to(docs).as_posix(),
        "definition_of_done_revision": revision,
        "definition_of_done_source_hash": source_hash,
    }, []


def operation_contract_snapshot(docs: Path, kind: str) -> tuple[dict, list[str]]:
    """Resolve one approved Operation Contract without trusting caller input.

    Operation is deliberately not a product-stage dependency.  It becomes
    mandatory only when a Delivery turns a Story into executable code work.
    The source hash is therefore pinned on the Delivery Item and checked again
    at every activation boundary.
    """
    receipt, errors = operation_compile.check_contract(docs, kind)
    if errors or not receipt.get("current"):
        return {}, [f"approved current {kind} contract is required: " + "; ".join(errors)]
    return {
        f"{kind}_contract_ref": f"operation/{kind}-contract",
        f"{kind}_contract_hash": str(receipt["source_hash"]),
    }, []


def item_operation_findings(docs: Path, props: dict) -> list[str]:
    """Validate compiler-owned Operation bindings for one executable Item."""
    errors: list[str] = []
    runtime = props.get("runtime_required", False)
    if not isinstance(runtime, bool):
        errors.append("runtime_required must be boolean")
        return errors
    verification, verification_errors = operation_contract_snapshot(docs, "verification")
    errors.extend(verification_errors)
    if verification and any(props.get(key) != value for key, value in verification.items()):
        errors.append("Verification Contract binding is stale or missing")
    if runtime:
        environment, environment_errors = operation_contract_snapshot(docs, "environment")
        errors.extend(environment_errors)
        if environment and any(props.get(key) != value for key, value in environment.items()):
            errors.append("Environment Contract binding is stale or missing")
    elif props.get("environment_contract_ref") or props.get("environment_contract_hash"):
        errors.append("non-runtime Item must not bind an Environment Contract")
    return sorted(set(errors))


def delivery_source_snapshots(docs: Path, root: Path) -> tuple[list[tuple[Path, dict]], dict[str, dict], dict, dict, list[str]]:
    """Resolve the approved Story, backlog and Definition of Done inputs one Delivery consumes."""
    item_paths = sorted(root.glob("items/*/item.md"))
    errors: list[str] = []
    if not item_paths:
        return [], {}, {}, {}, ["Delivery must contain at least one Item"]
    item_records: list[tuple[Path, dict]] = []
    for item_path in item_paths:
        try:
            item_props, _ = split_note(item_path)
        except (OSError, ValueError) as exc:
            errors.append(f"{item_path}: {exc}")
            continue
        story_id = item_props.get("story_id")
        if not isinstance(story_id, str) or not story_id:
            errors.append(f"{item_path} has no story_id")
            continue
        item_records.append((item_path, item_props))
    story_ids = [props.get("story_id") for _, props in item_records]
    if len(story_ids) != len(set(story_ids)):
        errors.append("Delivery Item story_id values must be unique")
    if errors:
        return [], {}, {}, {}, sorted(set(errors))

    sources, backlog_snapshot, source_errors = approved_backlog_sources(
        docs,
        [str(story_id) for story_id in story_ids],
        historical_inputs=True,
    )
    errors.extend(source_errors)
    dod, dod_errors = approved_dod_source(docs)
    errors.extend(dod_errors)
    if errors:
        return [], {}, {}, {}, sorted(set(errors))
    return item_records, sources, backlog_snapshot, dod, []


def item_source_pins(source: dict, story_id: str) -> dict:
    """Return the compiler-owned pins one Item carries for its approved Story."""
    pins = {key: source[key] for key in SOURCE_ITEM_FIELDS}
    pins["depends_on"] = source["depends_on"]
    pins["derives_from"] = [link(source["story_path"].removesuffix(".md"), story_id)]
    return pins


def delivery_source_findings(docs: Path, root: Path, delivery_props: dict, *,
                             compare_pins: bool = True) -> tuple[dict[str, dict], list[str]]:
    """Prove that a nonterminal Delivery still consumes its approved inputs."""
    item_records, sources, backlog_snapshot, dod, errors = delivery_source_snapshots(docs, root)
    if errors:
        return {}, errors
    if not compare_pins:
        return sources, []

    if delivery_props.get("backlog_path") != backlog_snapshot["backlog_path"]:
        errors.append("Delivery backlog_path does not identify the canonical backlog")
    if delivery_props.get("backlog_package_hash") != backlog_snapshot["backlog_package_hash"]:
        errors.append("Delivery backlog_package_hash is stale against the approved backlog")
    for key in DOD_SOURCE_FIELDS:
        if delivery_props.get(key) != dod[key]:
            errors.append(f"Delivery {key} is stale against the approved Definition of Done")

    for item_path, item_props in item_records:
        story_id = str(item_props["story_id"])
        source = sources[story_id]
        for key in SOURCE_ITEM_FIELDS:
            if item_props.get(key) != source[key]:
                errors.append(f"{item_path} {key} is stale against approved Story {story_id}")
        expected_source = [link(source["story_path"].removesuffix(".md"), story_id)]
        if item_props.get("derives_from") != expected_source:
            errors.append(f"{item_path} derives_from must contain only {story_id}")
    return sources, sorted(set(errors))

def link(path: str, label: str) -> str:
    return f"[[{path.removesuffix('.md')}|{label}]]"


def render_map(docs: Path) -> None:
    from delivery_governance import path_for as governance_path

    map_path = docs / "maps" / "delivery.md"
    rows = ["---", "type: moc", "title: Delivery", "tags:", "  - doc/moc", "---", "",
            "# Delivery", "", "Target-resident Delivery packages and their current semantic outcomes.", ""]
    rules = [(governance_path(docs), "Governance"),
             (delivery_root(docs) / "definition-of-done.md", "Definition of Done")]
    existing = [(path, title) for path, title in rules if path.is_file()]
    if existing:
        rows.extend(["## Project Rules", ""])
        rows.extend(f"- {link(path.relative_to(docs).as_posix(), title)}" for path, title in existing)
        rows.append("")
    rows.extend(["## Records", "", "<!-- delivery_compile.py: generated deliveries -->", ""])
    for directory in delivery_dirs(docs):
        path = directory / "delivery.md"
        try:
            props, _ = split_note(path)
        except (OSError, ValueError):
            continue
        identifier = str(props.get("id", directory.name)).strip()
        status = str(props.get("status", "unknown"))
        rows.append(f"- {link(path.relative_to(docs).as_posix(), identifier)} — `{status}`")
    # vault_check render-relations normalizes authored notes to this ending too.
    atomic_text(map_path, "\n".join(rows).rstrip() + "\n")


def check_dod(path: Path) -> list[str]:
    errors: list[str] = []
    if not path.exists():
        return [f"missing Definition of Done: {path}"]
    try:
        props, body = split_note(path)
    except (OSError, ValueError) as exc:
        return [str(exc)]
    if props.get("type") != "definition-of-done":
        errors.append("DoD type must be definition-of-done")
    if props.get("id") != "DOD":
        errors.append("DoD id must be DOD")
    if props.get("status") not in {"draft", "approved"}:
        errors.append("DoD status must be draft or approved")
    missing = sorted(set(DOD_SECTIONS) - sections(body))
    if missing:
        errors.append(f"DoD missing sections: {', '.join(missing)}")
    if props.get("status") == "approved":
        expected = content_hash(props, body)
        if props.get("source_hash") != expected:
            errors.append("approved DoD source_hash is stale")
        if not isinstance(props.get("approved_at_utc"), str):
            errors.append("approved DoD requires approved_at_utc")
    return errors


def init_dod(args) -> int:
    docs = docs_root(args.docs)
    path = delivery_root(docs) / "definition-of-done.md"
    if path.exists():
        print(json.dumps({"ok": False, "errors": ["Definition of Done already exists"]}))
        return 1
    heading = (args.title or "Definition of Done").strip()
    props = {"type": "definition-of-done", "id": "DOD", "title": heading,
             "status": "draft", "revision": 1, "aliases": ["DOD"],
             "tags": ["doc/definition-of-done", "status/draft"]}
    dod_body = "\n".join([
        f"# {heading}", "",
        "## Commands", "", "Record project verification commands.", "",
        "## Evidence Rules", "", "Record the evidence required for each gate.", "",
        "## Quality Gates", "", "Record the acceptance and review gates.", "",
        "## Navigation <!-- sec: nav -->", "", link("maps/delivery", "Delivery map"), "",
    ])
    atomic_text(path, frontmatter(props, dod_body))
    print(json.dumps({"ok": True, "path": str(path)}))
    return 0


def approve_dod(args) -> int:
    path = Path(args.file).resolve() if args.file else delivery_root(docs_root(args.docs)) / "definition-of-done.md"
    errors = check_dod(path)
    if errors and errors != ["approved DoD source_hash is stale"]:
        print(json.dumps({"ok": False, "errors": errors}, indent=2))
        return 1
    props, body = split_note(path)
    props["status"] = "approved"
    props["approved_at_utc"] = utc_now()
    props["source_hash"] = content_hash(props, body)
    props["tags"] = [tag for tag in props.get("tags", []) if not str(tag).startswith("status/")] + ["status/approved"]
    atomic_text(path, frontmatter(props, body))
    print(json.dumps({"ok": True, "path": str(path), "source_hash": props["source_hash"]}, indent=2))
    return 0


def check_dod_cmd(args) -> int:
    path = Path(args.file).resolve() if args.file else delivery_root(docs_root(args.docs)) / "definition-of-done.md"
    errors = check_dod(path)
    print(json.dumps({"ok": not errors, "errors": errors}, indent=2))
    return 0 if not errors else 1


def begin_dod_revision(args) -> int:
    docs = docs_root(args.docs)
    path = delivery_root(docs) / "definition-of-done.md"
    errors = check_dod(path)
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, indent=2))
        return 1
    props, body = split_note(path)
    if props.get("status") != "approved":
        print(json.dumps({"ok": False, "errors": ["DoD revision requires an approved current DoD"]}, indent=2))
        return 1
    props["revision"] = int(props.get("revision", 1)) + 1
    props["status"] = "draft"
    for key in ("approved_at_utc", "source_hash"):
        props.pop(key, None)
    props["tags"] = [tag for tag in props.get("tags", []) if not str(tag).startswith("status/")] + ["status/draft"]
    atomic_text(path, frontmatter(props, body))
    print(json.dumps({"ok": True, "revision": props["revision"], "path": str(path)}, indent=2))
    return 0


def init_delivery(args) -> int:
    docs = docs_root(args.docs)
    identifier = args.id or next_delivery_id(docs)
    if not DELIVERY_ID_RE.fullmatch(identifier):
        print(json.dumps({"ok": False, "errors": ["invalid Delivery id"]}))
        return 2
    slug = args.slug or re.sub(r"[^a-z0-9]+", "-", args.goal.lower()).strip("-")[:48]
    if not SLUG_RE.fullmatch(slug):
        print(json.dumps({"ok": False, "errors": ["invalid Delivery slug"]}))
        return 2
    root = delivery_path(docs, identifier, slug)
    if root.exists():
        print(json.dumps({"ok": False, "errors": [f"Delivery already exists: {root}"]}))
        return 1
    stories = list(args.story or [])
    sources, backlog_snapshot, source_errors = approved_backlog_sources(docs, stories)
    dod_snapshot, dod_errors = approved_dod_source(docs)
    errors = sorted(set(source_errors + dod_errors))
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, indent=2, ensure_ascii=False))
        return 2
    root.mkdir(parents=True)
    item_links = [link(f"delivery/deliveries/{root.name}/items/{id_slug(story)}/item", f"Implementation work for {story}") for story in stories]
    dod_link = link(dod_snapshot["definition_of_done_path"].removesuffix(".md"), "Definition of Done")
    goal = str(args.goal).strip()
    props = {"type": "delivery", "id": identifier, "title": f"Delivery scope for {goal}",
             "status": "scope_proposed", "owner_role": "product_owner", "goal": args.goal,
             "derives_from": item_links, "definition_of_done": dod_link,
             "target_branch": args.target_branch, "revision": 1,
             **backlog_snapshot, **dod_snapshot,
             "aliases": [identifier], "tags": ["doc/delivery", "status/scope-proposed"]}
    body = body_for("delivery", props["title"], {
        "Goal": args.goal, "Observable Outcome": args.outcome or "Define the observable result.",
        "Scope Rationale": "Selected stories are the exact executable scope.",
        "Exclusions": "No release management or unrelated work.",
        "Definition of Done Baseline": dod_link,
        "User Decisions": "Local scope proposal; awaiting scope approval.",
        "Navigation": "\n".join([link("maps/delivery", "Delivery map"), *item_links]),
    })
    atomic_text(root / "delivery.md", frontmatter(props, body))
    for story in stories:
        item = root / "items" / id_slug(story) / "item.md"
        source = sources[story]
        item_props = {"type": "delivery-item", "title": f"Implementation work for {story}",
                      "status": "in_scope",
                      "derives_from": [link(source["story_path"].removesuffix(".md"), story)],
                      "related_to": [link(f"delivery/deliveries/{root.name}/delivery", identifier)],
                      **{key: source[key] for key in SOURCE_ITEM_FIELDS},
                      "depends_on": source["depends_on"],
                      "execution_after": [], "dependency_bindings": [],
                      "waits_for": [], "waits_for_bindings": [],
                      "path_claims": [], "contract_claims": [],
                      "runtime_required": False,
                      "architecture_impact": "not_applicable", "architecture_components": [],
                      "architecture_record_kinds": [], "architecture_reason": "No architecture delta is currently required.",
                      "role_sequence": execution_roles(source),
                      "tags": ["doc/delivery-item", "status/in-scope"]}
        atomic_text(item, frontmatter(item_props, body_for("item", item_props["title"], {
            "Delivery Scope": identifier, "Navigation": link(f"delivery/deliveries/{root.name}/delivery", identifier),
        })))
    render_map(docs)
    print(json.dumps({"ok": True, "id": identifier, "slug": slug, "path": str(root), "stories": stories}, indent=2))
    return 0


def delivery_findings(docs: Path, identifier: str, *,
                      check_item_operation_bindings: bool = True,
                      compare_source_pins: bool = True) -> tuple[Path | None, list[str]]:
    root = find_delivery(docs, identifier) if identifier else None
    if root is None:
        return None, ["Delivery not found"]
    errors: list[str] = []
    path = root / "delivery.md"
    try:
        props, body = split_note(path)
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
        props, body = {}, ""
    if props.get("type") != "delivery": errors.append("delivery.md type must be delivery")
    if not DELIVERY_ID_RE.fullmatch(str(props.get("id", ""))): errors.append("invalid Delivery id")
    if props.get("status") not in STATUSES: errors.append("invalid Delivery status")
    errors.extend(f"delivery.md missing section: {name}" for name in sorted(set(SECTIONS["delivery"]) - sections(body)))
    dod = delivery_root(docs) / "definition-of-done.md"
    if not dod.exists(): errors.append("approved Definition of Done is required")
    elif split_note(dod)[0].get("status") != "approved": errors.append("Definition of Done must be approved")
    item_paths = sorted(root.glob("items/*/item.md"))
    if not item_paths: errors.append("Delivery must contain at least one Item")
    for item_path in item_paths:
        item_props, item_body = split_note(item_path)
        if item_props.get("type") != "delivery-item": errors.append(f"{item_path} type must be delivery-item")
        if item_props.get("status") not in ITEM_STATUSES: errors.append(f"{item_path} invalid Item status")
        errors.extend(f"{item_path} missing section: {name}" for name in sorted(set(SECTIONS["item"]) - sections(item_body)))
    plan = root / "execution-plan.md"
    if plan.exists():
        plan_props, plan_body = split_note(plan)
        if plan_props.get("type") != "execution-plan": errors.append("execution-plan.md type must be execution-plan")
        errors.extend(f"execution-plan.md missing section: {name}" for name in sorted(set(SECTIONS["execution-plan"]) - sections(plan_body)))
    # Closed Deliveries preserve their pinned historical source baseline. Every
    # mutable Delivery phase must instead prove that its selected Story/Test
    # Plan and Definition of Done are still the exact approved source bytes.
    if props.get("status") not in {"merged", "cancelled"}:
        _, source_errors = delivery_source_findings(docs, root, props, compare_pins=compare_source_pins)
        errors.extend(source_errors)
    if check_item_operation_bindings and props.get("status") in {"execution_approved", "active", "review", "pr_handoff", "awaiting_merge"}:
        for item_path in item_paths:
            try:
                item_props, _item_body = split_note(item_path)
            except (OSError, ValueError):
                continue
            # Its binding names the revision its evidence was produced against.
            if item_props.get("status") in TERMINAL_ITEM_STATUSES:
                continue
            errors.extend(f"{item_path}: {error}" for error in item_operation_findings(docs, item_props))
    return root, sorted(set(errors))


def check_delivery(args) -> int:
    docs = docs_root(args.docs)
    root, errors = delivery_findings(docs, args.delivery)
    props = {}
    if root is not None:
        try:
            props, _ = split_note(root / "delivery.md")
        except (OSError, ValueError):
            pass
    result = {"ok": not errors, "id": props.get("id"), "status": props.get("status"), "errors": errors}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if not errors else 1


def approve_scope(args) -> int:
    docs = docs_root(args.docs)
    root, errors = delivery_findings(docs, args.delivery)
    if root is None:
        print(json.dumps({"ok": False, "errors": errors}, indent=2)); return 1
    path = root / "delivery.md"
    props, body = split_note(path)
    errors = list(errors)
    if props.get("status") != "scope_proposed": errors.append("scope approval requires scope_proposed")
    dod = delivery_root(docs) / "definition-of-done.md"
    if not dod.exists() or split_note(dod)[0].get("status") != "approved":
        errors.append("Definition of Done must be approved before scope approval")
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, indent=2)); return 1
    props["status"] = "scope_approved"
    props["scope_hash"] = content_hash(props, body, exclude=MUTABLE | {"scope_hash"})
    props["approved_at_utc"] = utc_now()
    props["source_hash"] = content_hash(props, body)
    props["tags"] = [tag for tag in props.get("tags", []) if not str(tag).startswith("status/")] + ["status/scope-approved"]
    atomic_text(path, frontmatter(props, body))
    render_map(docs)
    print(json.dumps({"ok": True, "id": props["id"], "scope_hash": props["scope_hash"]}, indent=2)); return 0


def _string_list(value: object, label: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append(f"{label} must be a list of non-empty strings")
        return []
    return [item.strip() for item in value]


def _is_normalized_claim(value: str) -> bool:
    if (not value or value.startswith("/") or "\\" in value
            or re.match(r"^[A-Za-z]:", value)
            or any(ord(character) < 32 for character in value)):
        return False
    path = PurePosixPath(value)
    return bool(path.parts) and value == path.as_posix() and all(
        part not in {"", ".", ".."} for part in path.parts)


def execution_roles(source: dict, architecture_required: bool = False) -> list[str]:
    implementation = [source["owner_role"], *source["supporting_roles"]]
    if architecture_required:
        implementation = ["software_architect", *(
            role for role in implementation if role != "software_architect")]
    return [*implementation, "code_reviewer", "qa_engineer"]


def _claims_overlap(first: str, second: str) -> bool:
    left, right = PurePosixPath(first), PurePosixPath(second)
    return left == right or left in right.parents or right in left.parents


def execution_plan_findings(root: Path, sources: dict[str, dict], docs: Path) -> list[str]:
    """Validate the authored Item topology before execution approval.

    The Delivery compiler owns hashes and rendered plan summaries. People own
    the topology, claims and role sequence, so approval rejects omitted or
    contradictory execution intent rather than silently inventing defaults.
    """
    errors: list[str] = []
    selected = set(sources)
    graph: dict[str, set[str]] = {}
    path_owners: dict[str, str] = {}
    contract_owners: dict[str, str] = {}
    for item_path in sorted(root.glob("items/*/item.md")):
        props, _ = split_note(item_path)
        story_id = str(props.get("story_id", ""))
        if story_id not in sources:
            errors.append(f"{item_path} does not resolve to a selected approved Story")
            continue
        source = sources[story_id]
        after = _string_list(props.get("execution_after"), f"{story_id} execution_after", errors)
        waits_for = _string_list(props.get("waits_for"), f"{story_id} waits_for", errors)
        paths = _string_list(props.get("path_claims"), f"{story_id} path_claims", errors)
        contracts = _string_list(props.get("contract_claims"), f"{story_id} contract_claims", errors)
        roles = _string_list(props.get("role_sequence"), f"{story_id} role_sequence", errors)
        architecture_impact = str(props.get("architecture_impact", ""))
        architecture_components = _string_list(props.get("architecture_components"), f"{story_id} architecture_components", errors)
        architecture_kinds = _string_list(props.get("architecture_record_kinds"), f"{story_id} architecture_record_kinds", errors)
        architecture_reason = str(props.get("architecture_reason", "")).strip()
        if not isinstance(props.get("runtime_required", False), bool):
            errors.append(f"{story_id} runtime_required must be boolean")
        if not paths and not contracts:
            errors.append(f"{story_id} needs at least one exact path_claim or contract_claim")
        if len(after) != len(set(after)):
            errors.append(f"{story_id} execution_after contains duplicate Story IDs")
        if story_id in after:
            errors.append(f"{story_id} cannot execute after itself")
        unknown_after = sorted(set(after) - selected)
        if unknown_after:
            errors.append(f"{story_id} execution_after targets outside this Delivery: {', '.join(unknown_after)}")
        graph[story_id] = set(after)
        required_internal = set(source["depends_on"]) & selected
        missing_internal = sorted(required_internal - set(after))
        if missing_internal:
            errors.append(f"{story_id} execution_after omits approved dependencies: {', '.join(missing_internal)}")
        required_external = set(source["depends_on"]) - selected
        missing_external = sorted(required_external - set(waits_for))
        if missing_external:
            errors.append(f"{story_id} waits_for omits external approved dependencies: {', '.join(missing_external)}")
        if architecture_impact not in {"required", "not_applicable"}:
            errors.append(f"{story_id} architecture_impact must be required or not_applicable")
        if not architecture_reason:
            errors.append(f"{story_id} architecture_reason is required")
        if architecture_impact == "required":
            if not architecture_components or not architecture_kinds:
                errors.append(f"{story_id} architecture impact requires component and record-kind claims")
            try:
                import architecture_compile
                available = architecture_compile.solution_components(docs)
                unknown = sorted(set(architecture_components) - set(available))
                if unknown:
                    errors.append(f"{story_id} architecture components are absent from current Solution topology: {', '.join(unknown)}")
                # Item ownership includes shared tests and infrastructure; component
                # interiors still require an explicit architecture component claim.
                for component, detail in available.items():
                    if detail.get("sourcing") != "build" or component in architecture_components:
                        continue
                    code_path = str(detail.get("code_path", ""))
                    if code_path and any(_claims_overlap(path, code_path) for path in paths):
                        errors.append(f"{story_id} path_claims overlap unselected built component {component}: {code_path}")
            except (ImportError, ValueError) as exc:
                errors.append(f"{story_id} architecture impact cannot resolve the current Solution catalog: {exc}")
        else:
            if architecture_components or architecture_kinds:
                errors.append(f"{story_id} non-applicable architecture impact cannot declare architecture claims")
        expected_roles = execution_roles(source, architecture_impact == "required")
        if roles != expected_roles:
            errors.append(
                f"{story_id} role_sequence must be {', '.join(expected_roles)}"
            )
        if len(roles) != len(set(roles)):
            errors.append(f"{story_id} role_sequence contains duplicate roles")
        # A claim reserves a path against concurrent writers. A terminal Item has
        # no writer left, so its claim is a record of what it wrote rather than a
        # reservation, and holding it would keep any later Item out of that path
        # for the life of the Delivery. Its own claims are still validated.
        reserving = props.get("status") not in TERMINAL_ITEM_STATUSES
        for claim in paths:
            if not _is_normalized_claim(claim):
                errors.append(f"{story_id} path_claim is not normalized: {claim}")
                continue
            if not reserving:
                continue
            for previous_claim, previous in path_owners.items():
                if previous != story_id and _claims_overlap(claim, previous_claim):
                    errors.append(f"path_claim {claim} overlaps {previous_claim} owned by both {previous} and {story_id}")
            path_owners.setdefault(claim, story_id)
        for claim in contracts:
            previous = contract_owners.setdefault(claim, story_id)
            if previous != story_id:
                errors.append(f"contract_claim {claim} is owned by both {previous} and {story_id}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(story_id: str) -> bool:
        if story_id in visiting:
            return True
        if story_id in visited:
            return False
        visiting.add(story_id)
        cycle = any(visit(dependency) for dependency in graph.get(story_id, set()))
        visiting.remove(story_id)
        visited.add(story_id)
        return cycle

    if any(visit(story_id) for story_id in sorted(graph)):
        errors.append("Delivery execution_after graph contains a cycle")
    # Operation contracts are intentionally checked only at execution approval.
    # Bindings themselves are written below, after this preflight proves the
    # current source contracts are approved and current.
    _verification, verification_errors = operation_contract_snapshot(docs, "verification")
    errors.extend(verification_errors)
    if any(split_note(path)[0].get("runtime_required", False)
           for path in sorted(root.glob("items/*/item.md"))):
        _environment, environment_errors = operation_contract_snapshot(docs, "environment")
        errors.extend(environment_errors)
    return sorted(set(errors))


def reopen_findings(reopen: list[str], item_records: list[tuple[Path, dict]]) -> list[str]:
    """Allow a sealed Item to be rebound only when this approval names it for reopen."""
    statuses = {str(props["story_id"]): props.get("status") for _, props in item_records}
    errors: list[str] = []
    for story in reopen:
        if story not in statuses:
            errors.append(f"reopen names a Story outside this Delivery: {story}")
        elif statuses[story] != "integrated":
            errors.append(f"reopen requires an integrated Item: {story}")
    return errors


# merge-pr merges a Delivery PR only on green provider checks, so execution
# approval requires a GitHub workflow that the Delivery PR runs: a `.yml` or
# `.yaml` file directly in `.github/workflows/`, the only place GitHub reads.
# An approved Verification Contract that declares an external source of those
# checks lifts the requirement.
# The check reads lines instead of parsing YAML to stay dependency-free. It
# accepts a top-level `on` key, bare or quoted, whose value is one event or a
# one-line flow sequence, or whose direct children are event keys or block
# sequence items, and it ignores trailing comments. Under a pull_request or
# pull_request_target key it reads `types` as a scalar, a one-line flow sequence
# or a block sequence, also inside a one-line flow mapping. It does not resolve
# other flow mappings, flow sequences that span lines, anchors, aliases or tags,
# and it does not evaluate branch or path filters.
PULL_REQUEST_EVENTS = {"pull_request", "pull_request_target"}
# merge-pr reads the checks of the head that record-pr-remote pushes after the
# Delivery PR opens. Of the activity types GitHub runs a pull request workflow for
# when it lists none, only synchronize fires for that push; opened and reopened
# check earlier heads.
PULL_REQUEST_ACTIVITY_TYPES = {"synchronize"}
YAML_COMMENT_RE = re.compile(r"(?:^|\s)#.*$")
TRIGGER_KEY_RE = re.compile(r"""^(?:on|"on"|'on')\s*:(?:\s+(?P<value>.*))?$""")
FLOW_TYPES_RE = re.compile(r"""[{,]\s*["']?types["']?\s*:\s*(\[[^\]]*\]|[^,}]+)""")


def _names(value: str) -> set[str]:
    """Names in one scalar or one-line flow sequence."""
    value = value.strip()
    names = value[1:-1].split(",") if value.startswith("[") and value.endswith("]") else [value]
    return {name.strip().strip("\"'") for name in names}


def _children(lines: list[str]) -> list[tuple[str, str, list[str]]]:
    """Split one block into its direct children as (key, inline value, nested lines).

    A sequence item has the key ``-``. An item at the column of an empty key
    before it is that key's value, the compact form of a block sequence.
    """
    children: list[tuple[str, str, list[str]]] = []
    column = None
    for line in lines:
        entry = line.strip()
        if not entry:
            continue
        depth = len(line) - len(line.lstrip())
        column = depth if column is None else column
        item = entry == "-" or entry.startswith("- ")
        if children and (depth > column or (item and children[-1][0] != "-" and not children[-1][1])):
            children[-1][2].append(line)
        elif depth == column:
            key, _, value = ("-", "", entry[1:]) if item else entry.partition(":")
            children.append((key.strip().strip("\"'"), value.strip(), []))
        else:
            break
    return children


def _event_runs(value: str, nested: list[str]) -> bool:
    """Whether one pull request event key lists no activity types or one that counts."""
    if value.startswith("{") and value.endswith("}"):
        match = FLOW_TYPES_RE.search(value)
        types = _names(match.group(1)) if match else None
    elif value.lower() in {"", "~", "null"}:
        types = None
        for key, inline, lines in _children(nested):
            if key == "types":
                types = _names(inline) if inline else {
                    name for item, text, _ in _children(lines) if item == "-" for name in _names(text)}
    else:
        return False
    return types is None or bool(types & PULL_REQUEST_ACTIVITY_TYPES)


def pull_request_triggers(text: str) -> set[str]:
    """The pull request events in one workflow's top-level ``on`` whose activity types count."""
    lines = [YAML_COMMENT_RE.sub("", line).rstrip() for line in text.splitlines()]
    for index, line in enumerate(lines):
        match = TRIGGER_KEY_RE.match(line)
        if match is None:
            continue
        if match.group("value"):
            return _names(match.group("value")) & PULL_REQUEST_EVENTS
        block = []
        for child in lines[index + 1:]:
            entry = child.strip()
            # A block sequence may start at the column of its own key.
            if entry and child == child.lstrip() and not entry.startswith("- "):
                break
            block.append(child)
        events = set()
        for key, value, nested in _children(block):
            if key == "-":
                events |= _names(value) & PULL_REQUEST_EVENTS
            elif key in PULL_REQUEST_EVENTS and _event_runs(value, nested):
                events.add(key)
        return events
    return set()


def committed_workflows(checkout: Path, ref: str) -> list[str] | None:
    """The workflow files directly in `.github/workflows/` at one ref, or None without the ref."""
    git = ["git", "--no-replace-objects", "-C", str(checkout)]
    listing = subprocess.run([*git, "ls-tree", "--full-tree", "-z", ref, "--", ".github/workflows/"],
                             capture_output=True, check=False)
    if listing.returncode:
        return None
    texts = []
    for row in filter(None, listing.stdout.split(b"\0")):
        metadata, _, name = row.partition(b"\t")
        mode, _kind, oid = metadata.decode("ascii").split()
        if mode not in {"100644", "100755"} or not name.endswith((b".yml", b".yaml")):
            continue
        blob = subprocess.run([*git, "cat-file", "blob", oid], capture_output=True, check=False)
        try:
            texts.append(blob.stdout.decode("utf-8-sig"))
        except UnicodeDecodeError:
            continue
    return texts


def pull_request_workflow_findings(docs: Path, delivery: str, remote: str = "origin") -> list[str]:
    """Require a workflow that the Delivery PR will run, read from committed trees.

    GitHub runs a pull_request workflow from the PR merge commit, which joins the
    Integration head and the target, and a pull_request_target workflow from the
    default branch alone. So a pull_request trigger counts in the target's or the
    Integration's remote-tracking ref, and a pull_request_target trigger only in
    the target's. Integration alone would not do: reservation cuts it from the
    target before this approval, so a workflow merged into the target afterwards
    is in the merge commit before a target refresh brings it into Integration.
    HEAD stands in for a target that has no remote-tracking ref. Only local refs
    are read, as current as the last fetch or push; outside a Git checkout the
    working tree of the project stands in.
    """
    project = docs.parent.parent if docs.parent.name == "workspace" else docs.parent
    checkout = next((parent for parent in (docs, *docs.parents) if (parent / ".git").exists()), None)
    if checkout is None:
        workflows = project / ".github" / "workflows"
        texts = []
        for path in (*workflows.glob("*.yml"), *workflows.glob("*.yaml")):
            try:
                texts.append(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeDecodeError):
                continue
        trees = [(str(workflows), texts, PULL_REQUEST_EVENTS)]
        where, remedy = f"is in {workflows} outside a Git checkout", ""
    else:
        from delivery_git import resolve_target_branch, short_refs
        try:
            branch = resolve_target_branch(checkout, remote)
        except RuntimeError:
            branch = ""
        target = f"refs/remotes/{remote}/{branch}"
        texts = committed_workflows(checkout, target) if branch else None
        remedy = f", commit it, push it to {branch} on {remote} and fetch"
        if texts is None:
            target, texts, remedy = "HEAD", committed_workflows(checkout, "HEAD") or [], ", then commit it"
        trees = [(target, texts, PULL_REQUEST_EVENTS)]
        integration = f"refs/remotes/{remote}/{short_refs(delivery)['integration']}"
        integration_texts = committed_workflows(checkout, integration)
        if integration_texts is not None:
            trees.append((integration, integration_texts, {"pull_request"}))
        where = "is committed in " + " or ".join(ref for ref, _texts, _events in trees)
    if any(pull_request_triggers(text) & events for _ref, texts, events in trees for text in texts):
        return []
    names = sorted(PULL_REQUEST_ACTIVITY_TYPES)
    types = " or ".join(filter(None, (", ".join(names[:-1]), names[-1])))
    return [f"Execution approval requires a workflow in .github/workflows/ triggered by pull_request "
            f"or pull_request_target, listing no activity types or including {types}, since merge-pr "
            f"needs a green check on the Delivery PR; none {where}. When the checks come from outside "
            "the repository's workflows, declare pull_request_check_source: external in an approved "
            "Verification Contract revision instead; otherwise materialize the workflow with "
            "operation_compile.py render-ci as the CI bootstrap contract "
            f"(skill-content/setup/references/ci-bootstrap.md) describes{remedy}"]


def approved_pull_request_checks(docs: Path) -> dict:
    """Where the approved current Verification Contract declares the Delivery PR checks come from.

    Without such a contract, approval is refused anyway and the default stands.
    """
    receipt, errors = operation_compile.check_contract(docs, "verification")
    props = {}
    if not errors and receipt.get("current"):
        props, _body = operation_compile.parse(operation_compile.contract_path(docs, "verification"))
    source, provider = operation_compile.pull_request_checks(props)
    if source != "external":
        return {"source": source}
    return {"source": source, "provider": provider,
            "merge_requirement": "No repository workflow is required, but merge-pr still merges the "
                                 f"Delivery PR only on green checks, so {provider} must report them on it"}


def approve_execution(args) -> int:
    docs = docs_root(args.docs)
    # This verb writes the Item Operation bindings and refreshes the approved source
    # pins, so it cannot require either to already match. The contracts themselves are
    # still proved approved and current by execution_plan_findings before anything is
    # written, and the sources are re-resolved from the approved backlog below.
    root, findings = delivery_findings(docs, args.delivery, check_item_operation_bindings=False,
                                       compare_source_pins=False)
    if root is None:
        print(json.dumps({"ok": False, "errors": findings}, indent=2)); return 1
    path = root / "delivery.md"
    props, body = split_note(path)
    if props.get("status") not in {"scope_approved", "execution_approved"}:
        print(json.dumps({"ok": False, "errors": ["Execution approval requires a scope-approved Delivery"]}, indent=2)); return 1
    if findings:
        print(json.dumps({"ok": False, "errors": findings}, indent=2)); return 1
    items = sorted(root.glob("items/*/item.md"))
    if not items:
        print(json.dumps({"ok": False, "errors": ["Execution Plan requires at least one Item"]}, indent=2)); return 1
    item_records, sources, backlog_snapshot, dod, source_errors = delivery_source_snapshots(docs, root)
    reopen = sorted(set(str(story) for story in (getattr(args, "reopen", None) or [])))
    plan_errors = source_errors + reopen_findings(reopen, item_records)
    if not plan_errors:
        plan_errors = execution_plan_findings(root, sources, docs)
    pull_request_checks = approved_pull_request_checks(docs)
    if pull_request_checks["source"] != "external":
        plan_errors += pull_request_workflow_findings(docs, args.delivery, getattr(args, "remote", "origin"))
    if plan_errors:
        print(json.dumps({"ok": False, "errors": sorted(set(plan_errors))}, indent=2)); return 1
    refreshed_sources: list[str] = []
    rebound: list[str] = []
    item_ids = []
    item_graph: list[str] = []
    role_sequences: list[str] = []
    path_claims: list[str] = []
    contract_claims: list[str] = []
    item_hashes: list[str] = []
    operation_hashes: list[str] = []
    verification_binding, _ = operation_contract_snapshot(docs, "verification")
    environment_binding, _ = operation_contract_snapshot(docs, "environment")
    for item_path in items:
        item_props, item_body = split_note(item_path)
        story = str(item_props["story_id"])
        item_ids.append(story)
        pins = item_source_pins(sources[story], story)
        if any(item_props.get(key) != value for key, value in pins.items()):
            refreshed_sources.append(story)
        item_props.update(pins)
        item_props["dependency_bindings"] = sorted(
            set(sources[story]["depends_on"]) & set(item_props["execution_after"])
        )
        item_props["waits_for_bindings"] = sorted(
            set(sources[story]["depends_on"]) - set(item_props["execution_after"])
        )
        # A sealed Item keeps the binding its evidence was produced against unless this
        # approval names it for reopen, which rebinds it to the current contracts.
        if item_props.get("status") not in TERMINAL_ITEM_STATUSES or story in reopen:
            if story in reopen:
                rebound.append(story)
            item_props.update(verification_binding)
            if item_props.get("runtime_required"):
                item_props.update(environment_binding)
            else:
                item_props.pop("environment_contract_ref", None)
                item_props.pop("environment_contract_hash", None)
        item_props["item_plan_hash"] = content_hash(item_props, item_body, exclude=MUTABLE | {"item_plan_hash"})
        atomic_text(item_path, frontmatter(item_props, item_body))
        item_hashes.append(f"{story}:{item_props['item_plan_hash']}")
        item_graph.append(
            f"{story} after " + (", ".join(item_props["execution_after"]) or "none")
        )
        role_sequences.append(f"{story}: " + " -> ".join(item_props["role_sequence"]))
        path_claims.extend(f"{story}: {claim}" for claim in item_props["path_claims"])
        contract_claims.extend(f"{story}: {claim}" for claim in item_props["contract_claims"])
        operation_hashes.append(
            f"{story}: verification={item_props['verification_contract_hash']}"
            + (f", environment={item_props['environment_contract_hash']}"
               if item_props.get("runtime_required") else "")
        )
        for kind, filename, status in (("code-review", "code-review.md", "draft"), ("verification", "verification.md", "draft")):
            evidence = item_path.parent / filename
            if not evidence.exists():
                evidence_title = (
                    f"Implementation review for {story}"
                    if kind == "code-review"
                    else f"Verification evidence for {story}"
                )
                ev_props = {"type": kind, "id": f"{props['id']}-{story}-{'CR' if kind == 'code-review' else 'QA'}",
                            "title": evidence_title,
                            "status": status, "derives_from": [link(item_path.relative_to(docs).as_posix(), item_props["title"])],
                            "item_plan_hash": item_props["item_plan_hash"], "tags": [f"doc/{kind}", f"status/{status}"]}
                atomic_text(evidence, frontmatter(ev_props, body_for("item", ev_props["title"], {"Navigation": link(item_path.relative_to(docs).as_posix(), item_props["title"])})))
    plan_path = root / "execution-plan.md"
    plan_subject = str(props.get("goal", props["id"])).strip()
    plan_props = {"type": "execution-plan", "id": f"{props['id']}-EXEC", "title": f"Execution approach for {plan_subject}",
                  "status": "approved", "revision": 1, "scope_hash": props["scope_hash"],
                  "item_plan_hashes": sorted(item_hashes),
                  "operation_contract_hashes": sorted(operation_hashes),
                  "derives_from": [link(path.relative_to(docs).as_posix(), props["id"])],
                  "tags": ["doc/execution-plan", "status/approved"]}
    plan_body = body_for("execution-plan", plan_props["title"], {
        "Preconditions": "Approved backlog Story/Test Plan snapshots, the pinned Definition of Done and the exact Operation Contract hashes are current.",
        "Item Graph": "\n".join(f"- {row}" for row in item_graph),
        "Execution Waves": "Execution follows the acyclic Item Graph; independent Items may activate only within the global Slot cap.",
        "Role Sequences": "\n".join(f"- {row}" for row in role_sequences),
        "Path Claims": "\n".join(f"- {row}" for row in path_claims),
        "Contract Claims": "\n".join(f"- {row}" for row in [*contract_claims, *operation_hashes]) or "- none",
        "Integration Order": " -> ".join(item_ids),
        "Verification Strategy": "Each Item must bind review and verification to its exact worktree product commit before integration.",
        "Failure and Recovery": "A stale source snapshot, target conflict or missing verified writer receipt blocks activation and requires the named recovery path.",
        "Approval": "Execution approval binds this plan hash and the exact Item plan hashes listed in front matter.",
        "Navigation": link(path.relative_to(docs).as_posix(), props["id"]),
    })
    plan_props["plan_hash"] = content_hash(plan_props, plan_body, exclude=MUTABLE | {"plan_hash"})
    plan_props["approved_at_utc"] = utc_now()
    plan_props["source_hash"] = content_hash(plan_props, plan_body)
    atomic_text(plan_path, frontmatter(plan_props, plan_body))
    delivery_pins = {**backlog_snapshot, **{key: dod[key] for key in DOD_SOURCE_FIELDS}}
    refreshed_delivery_pins = sorted(key for key, value in delivery_pins.items() if props.get(key) != value)
    props.update(delivery_pins)
    props["status"] = "execution_approved"
    props["plan_hash"] = plan_props["plan_hash"]
    props["source_hash"] = content_hash(props, body)
    props["tags"] = [tag for tag in props.get("tags", []) if not str(tag).startswith("status/")] + ["status/execution-approved"]
    atomic_text(path, frontmatter(props, body))
    print(json.dumps({"ok": True, "id": props["id"], "plan_hash": props["plan_hash"], "items": item_ids,
                      "refreshed_sources": refreshed_sources, "refreshed_delivery_pins": refreshed_delivery_pins,
                      "rebound": rebound, "pull_request_checks": pull_request_checks}, indent=2)); return 0


def status(args) -> int:
    docs = docs_root(args.docs)
    root = find_delivery(docs, args.delivery)
    if root is None: print(json.dumps({"ok": False, "errors": ["Delivery not found"]}, indent=2)); return 1
    props, _ = split_note(root / "delivery.md")
    result = {"ok": True, "id": props.get("id"), "status": props.get("status"), "path": str(root),
              "execution_plan": (root / "execution-plan.md").exists(),
              "items": sorted(path.parent.name.upper() for path in root.glob("items/*/item.md"))}
    print(json.dumps(result, indent=2)); return 0


def render(args) -> int:
    docs = docs_root(args.docs)
    render_map(docs)
    print(json.dumps({"ok": True, "map": str(docs / "maps" / "delivery.md")}, indent=2))
    return 0


def prepare_item_transition(args) -> int:
    docs = docs_root(args.docs)
    root = find_delivery(docs, args.delivery)
    item = root / "items" / id_slug(args.story) / "item.md" if root else None
    if item is None or not item.exists():
        print(json.dumps({"ok": False, "errors": ["Delivery Item not found"]}, indent=2)); return 1
    if args.to not in ITEM_STATUSES:
        print(json.dumps({"ok": False, "errors": ["invalid Item transition status"]}, indent=2)); return 2
    props, body = split_note(item)
    previous = props.get("status")
    props["status"] = args.to
    props["tags"] = [tag for tag in props.get("tags", []) if not str(tag).startswith("status/")] + [f"status/{args.to.replace('_', '-')}" ]
    atomic_text(item, frontmatter(props, body))
    print(json.dumps({"ok": True, "story": args.story, "from": previous, "to": args.to}, indent=2)); return 0


def check_item_ready(args) -> int:
    docs = docs_root(args.docs)
    root = find_delivery(docs, args.delivery)
    item = root / "items" / id_slug(args.story) / "item.md" if root else None
    errors = []
    if item is None or not item.exists():
        errors.append("Delivery Item not found")
    else:
        props, _ = split_note(item)
        review = item.parent / "code-review.md"
        verification = item.parent / "verification.md"
        if not review.exists() or split_note(review)[0].get("status") != "approved": errors.append("code review is not approved")
        if not verification.exists() or split_note(verification)[0].get("status") != "passed": errors.append("verification is not passed")
        if not props.get("item_plan_hash"): errors.append("Item has no item_plan_hash")
    print(json.dumps({"ok": not errors, "errors": errors}, indent=2)); return 0 if not errors else 1


def item_worktree_head(worktree: Path) -> tuple[str, list[str]]:
    """Return a clean Item worktree HEAD without trusting caller-provided OIDs."""
    try:
        top = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "--show-toplevel"],
            text=True, capture_output=True, check=False,
        )
        if top.returncode:
            raise RuntimeError(top.stderr.strip() or "not a Git worktree")
        if Path(top.stdout.strip()).resolve() != worktree.resolve():
            raise RuntimeError("worktree must be the Item worktree root")
        head = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            text=True, capture_output=True, check=False,
        )
        if head.returncode or not GIT_OID_RE.fullmatch(head.stdout.strip()):
            raise RuntimeError(head.stderr.strip() or "Item worktree has no valid HEAD")
        dirty = subprocess.run(
            ["git", "-C", str(worktree), "status", "--porcelain", "--untracked-files=all"],
            text=True, capture_output=True, check=False,
        )
        if dirty.returncode:
            raise RuntimeError(dirty.stderr.strip() or "cannot inspect Item worktree")
    except OSError as exc:
        raise RuntimeError(f"cannot inspect Item worktree: {exc}") from exc
    return head.stdout.strip(), [line for line in dirty.stdout.splitlines() if line]


def item_evidence_file_findings(worktree: Path, head: str, paths: tuple[Path, Path]) -> list[str]:
    """Evidence authoring may update existing reports, never replace their file boundary."""
    for path in paths:
        tracked = subprocess.run(["git", "--no-replace-objects", "-C", str(worktree), "ls-tree", head, "--",
                                  path.relative_to(worktree).as_posix()],
                                 text=True, capture_output=True, check=False)
        if (path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1
                or tracked.returncode or not tracked.stdout.startswith("100644 blob ")
                or path.stat().st_mode & 0o111):
            return ["Item evidence must remain initialized regular non-executable files"]
    return []


def approve_item_evidence(args) -> int:
    worktree_value = getattr(args, "worktree", None)
    if not isinstance(worktree_value, str) or not worktree_value.strip():
        print(json.dumps({"ok": False, "errors": ["an Item worktree is required"]}, indent=2)); return 2
    worktree = Path(worktree_value).resolve()
    docs = docs_root(worktree)
    requested_docs = docs_root(args.docs)
    try:
        head, dirty = item_worktree_head(worktree)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2)); return 2
    if str(getattr(args, "docs", ".")) not in {"", "."} and requested_docs != docs:
        print(json.dumps({"ok": False, "errors": ["--docs must resolve inside the active Item worktree"]}, indent=2)); return 2
    root = find_delivery(docs, args.delivery)
    item = root / "items" / id_slug(args.story) / "item.md" if root else None
    if item is None or not item.exists():
        print(json.dumps({"ok": False, "errors": ["Delivery Item not found"]}, indent=2)); return 1
    review = item.parent / "code-review.md"
    verification = item.parent / "verification.md"
    if not review.exists() or not verification.exists():
        print(json.dumps({"ok": False, "errors": ["Item evidence files are not initialized"]}, indent=2)); return 1
    from delivery_git import require_visible_item_index, worktree_pending_paths
    try:
        require_visible_item_index(worktree)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2)); return 2
    allowed = {path.relative_to(worktree).as_posix() for path in (review, verification)}
    if dirty and not worktree_pending_paths(worktree, worktree).issubset(allowed):
        print(json.dumps({"ok": False, "errors": ["commit or remove non-evidence Item worktree changes before approving evidence"]}, indent=2)); return 2
    findings = item_evidence_file_findings(worktree, head, (review, verification))
    if findings:
        print(json.dumps({"ok": False, "errors": findings}, indent=2)); return 2
    item_props, _ = split_note(item)
    review_props, review_body = split_note(review)
    verification_props, verification_body = split_note(verification)
    if item_props.get("status") != "active":
        print(json.dumps({"ok": False, "errors": ["Item evidence requires an active Item worktree"]}, indent=2)); return 2
    reviewed = head
    verified = head
    review_props["status"] = "approved"; review_props["reviewed_commit"] = reviewed
    review_props["item_plan_hash"] = item_props.get("item_plan_hash", "none")
    review_props["source_hash"] = content_hash(review_props, review_body)
    verification_props["status"] = "passed"; verification_props["verified_commit"] = verified
    verification_props["item_plan_hash"] = item_props.get("item_plan_hash", "none")
    verification_props["source_hash"] = content_hash(verification_props, verification_body)
    review_props["tags"] = [tag for tag in review_props.get("tags", []) if not str(tag).startswith("status/")] + ["status/approved"]
    verification_props["tags"] = [tag for tag in verification_props.get("tags", []) if not str(tag).startswith("status/")] + ["status/passed"]
    atomic_text(review, frontmatter(review_props, review_body))
    atomic_text(verification, frontmatter(verification_props, verification_body))
    print(json.dumps({"ok": True, "story": args.story, "reviewed_commit": reviewed,
                      "verified_commit": verified, "worktree": str(worktree)}, indent=2)); return 0


def approve_review(args) -> int:
    docs = docs_root(args.docs)
    root = find_delivery(docs, args.delivery)
    if root is None:
        print(json.dumps({"ok": False, "errors": ["Delivery not found"]}, indent=2)); return 1
    reviewed_commit = getattr(args, "reviewed_commit", None)
    reviewed_integration = getattr(args, "reviewed_integration_commit", None)
    if (not isinstance(reviewed_commit, str) or not GIT_OID_RE.fullmatch(reviewed_commit)
            or not isinstance(reviewed_integration, str) or not GIT_OID_RE.fullmatch(reviewed_integration)):
        print(json.dumps({"ok": False, "errors": [
            "review approval requires exact reviewed_commit and reviewed_integration_commit Git OIDs"
        ]}, indent=2)); return 2
    delivery_path_value = root / "delivery.md"
    delivery_props, _ = split_note(delivery_path_value)
    review_path = root / "delivery-review.md"
    review_subject = str(delivery_props.get("goal", args.delivery)).strip()
    review_props = {"type": "delivery-review", "id": f"{args.delivery}-REVIEW",
                    "title": f"Outcome review for {review_subject}",
                    "status": "approved", "derives_from": [link(delivery_path_value.relative_to(docs).as_posix(), args.delivery)],
                    "plan_hash": delivery_props.get("plan_hash", "none"),
                    "reviewed_commit": reviewed_commit,
                    "reviewed_integration_commit": reviewed_integration,
                    "approved_at_utc": utc_now(), "tags": ["doc/delivery-review", "status/approved"]}
    # The review is authored before it is approved: what its author wrote in each
    # section is kept, and the compiler fills only the sections left empty and the
    # navigation it owns. The approval then binds the authored review.
    authored = {}
    if review_path.exists():
        authored = {title: text for title, text
                    in section_bodies(without_generated_relations(split_note(review_path)[1])).items()
                    if title in SECTIONS["delivery-review"] and title != "Navigation"
                    and text and text != SECTION_PLACEHOLDER}
    review_body = body_for("delivery-review", review_props["title"], {
        "Goal Outcome": delivery_props.get("goal", ""), "Verdict": "Approved for PR handoff.", **authored,
        "Navigation": link(delivery_path_value.relative_to(docs).as_posix(), args.delivery)})
    review_props["approval_hash"] = content_hash(review_props, review_body, exclude=MUTABLE | {"approval_hash"})
    review_props["source_hash"] = content_hash(review_props, review_body)
    atomic_text(review_path, frontmatter(review_props, review_body))
    delivery_props["status"] = "review"
    delivery_props["tags"] = [tag for tag in delivery_props.get("tags", []) if not str(tag).startswith("status/")] + ["status/review"]
    delivery_props["source_hash"] = content_hash(delivery_props, split_note(delivery_path_value)[1])
    atomic_text(delivery_path_value, frontmatter(delivery_props, split_note(delivery_path_value)[1]))
    print(json.dumps({"ok": True, "review": str(review_path), "approval_hash": review_props["approval_hash"]}, indent=2)); return 0


def record_pr(args) -> int:
    docs = docs_root(args.docs)
    root = find_delivery(docs, args.delivery)
    review = root / "delivery-review.md" if root else None
    if review is None or not review.exists():
        print(json.dumps({"ok": False, "errors": ["Delivery Review not found"]}, indent=2)); return 1
    props, body = split_note(review)
    props["pull_request_url"] = args.url
    props["source_hash"] = content_hash(props, body, exclude=MUTABLE - {"pull_request_url"})
    atomic_text(review, frontmatter(props, body))
    print(json.dumps({"ok": True, "pull_request_url": args.url}, indent=2)); return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init-dod", "begin-dod-revision", "check-dod", "approve-dod"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--file")
        cmd.add_argument("--title")
    init_d = sub.choices["init-dod"]; init_d.set_defaults(func=init_dod)
    sub.choices["begin-dod-revision"].set_defaults(func=begin_dod_revision)
    sub.choices["check-dod"].set_defaults(func=check_dod_cmd)
    sub.choices["approve-dod"].set_defaults(func=approve_dod)
    init = sub.add_parser("init"); init.add_argument("--id"); init.add_argument("--slug"); init.add_argument("--goal", required=True); init.add_argument("--outcome"); init.add_argument("--target-branch", default="main"); init.add_argument("--story", action="append"); init.set_defaults(func=init_delivery)
    for name, func in (("check", check_delivery), ("approve-scope", approve_scope), ("approve-execution", approve_execution), ("status", status)):
        cmd = sub.add_parser(name); cmd.add_argument("--delivery", required=True); cmd.set_defaults(func=func)
    sub.choices["approve-execution"].add_argument(
        "--reopen", action="append", default=[], metavar="STORY",
        help="rebind this integrated Item to the current Operation contracts so that it can be reopened")
    sub.choices["approve-execution"].add_argument(
        "--remote", default="origin",
        help="the Git remote whose local remote-tracking refs hold the target and Integration branches")
    sub.add_parser("render").set_defaults(func=render)
    transition = sub.add_parser("prepare-item-transition")
    transition.add_argument("--delivery", required=True); transition.add_argument("--story", required=True)
    transition.add_argument("--to", required=True, choices=sorted(ITEM_STATUSES)); transition.set_defaults(func=prepare_item_transition)
    ready = sub.add_parser("check-item-ready")
    ready.add_argument("--delivery", required=True); ready.add_argument("--story", required=True); ready.set_defaults(func=check_item_ready)
    evidence = sub.add_parser("approve-item-evidence")
    evidence.add_argument("--delivery", required=True); evidence.add_argument("--story", required=True)
    evidence.add_argument("--worktree", required=True)
    evidence.set_defaults(func=approve_item_evidence)
    review = sub.add_parser("approve-review")
    review.add_argument("--delivery", required=True); review.add_argument("--reviewed-commit", required=True); review.add_argument("--reviewed-integration-commit", required=True); review.set_defaults(func=approve_review)
    pr = sub.add_parser("record-pr")
    pr.add_argument("--delivery", required=True); pr.add_argument("--url", required=True); pr.set_defaults(func=record_pr)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
