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

from ba_compile import parse_frontmatter
import backlog_compile
import operation_compile


DELIVERY_ID_RE = re.compile(r"^DLV-[0-9]{3,}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
STORY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
STATUSES = {
    "scope_proposed", "scope_approved", "execution_approved", "active",
    "review", "pr_handoff", "awaiting_merge", "merged", "cancelled",
}
ITEM_STATUSES = {"in_scope", "claimed", "active", "blocked", "paused", "integrated", "cancelled"}
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


def load_config(docs: Path) -> dict:
    try:
        value = json.loads((docs.parent / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def designation(docs: Path, key: str, default: str) -> str:
    values = load_config(docs).get("doc_type_designations", {})
    value = values.get(key) if isinstance(values, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else default


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
    return {match.group(1).strip() for match in re.finditer(r"(?m)^## (.+?)\s*$", body)}


def content_hash(props: dict, body: str, *, exclude: set[str] | None = None) -> str:
    excluded = MUTABLE if exclude is None else exclude
    stable = {key: value for key, value in props.items() if key not in excluded}
    if isinstance(stable.get("tags"), list):
        stable["tags"] = [tag for tag in stable["tags"]
                           if not (isinstance(tag, str) and tag.startswith("status/"))]
    payload = json.dumps({"frontmatter": stable, "body": body.rstrip() + "\n"},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def title(base: str, designation_value: str) -> str:
    return base if base.casefold().endswith(designation_value.casefold()) else f"{base} {designation_value}"


def body_for(kind: str, heading: str, values: dict[str, str] | None = None) -> str:
    values = values or {}
    lines = [f"# {heading}", ""]
    for section in SECTIONS[kind]:
        lines.extend([f"## {section}", "", values.get(section, "Record compiler-owned evidence here."), ""])
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


def approved_backlog_sources(docs: Path, story_ids: list[str]) -> tuple[dict[str, dict], dict, list[str]]:
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
        record, findings = backlog_compile.collect(docs)
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
        "definition_of_done_path": str(path.relative_to(docs)),
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


def delivery_source_findings(docs: Path, root: Path, delivery_props: dict) -> tuple[dict[str, dict], list[str]]:
    """Prove that a nonterminal Delivery still consumes its approved inputs."""
    item_paths = sorted(root.glob("items/*/item.md"))
    errors: list[str] = []
    if not item_paths:
        return {}, ["Delivery must contain at least one Item"]
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
        return {}, sorted(set(errors))

    sources, backlog_snapshot, source_errors = approved_backlog_sources(
        docs, [str(story_id) for story_id in story_ids]
    )
    errors.extend(source_errors)
    dod, dod_errors = approved_dod_source(docs)
    errors.extend(dod_errors)
    if errors:
        return {}, sorted(set(errors))

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
        expected_source = [story_id]
        if item_props.get("derives_from") != expected_source:
            errors.append(f"{item_path} derives_from must contain only {story_id}")
    return sources, sorted(set(errors))


def link(path: str, label: str) -> str:
    return f"[[{path}|{label}]]"


def render_map(docs: Path) -> None:
    map_path = docs / "maps" / "delivery.md"
    rows = ["---", "type: moc", "title: Delivery", "tags:", "  - doc/moc", "---", "",
            "# Delivery", "", "Target-resident Delivery packages and their current semantic outcomes.",
            "", "## Records", "", "<!-- delivery_compile.py: generated deliveries -->", ""]
    for directory in delivery_dirs(docs):
        path = directory / "delivery.md"
        try:
            props, _ = split_note(path)
        except (OSError, ValueError):
            continue
        identifier = str(props.get("id", directory.name)).strip()
        status = str(props.get("status", "unknown"))
        rows.append(f"- {link(str(path.relative_to(docs)), identifier)} — `{status}`")
    atomic_text(map_path, "\n".join(rows))


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
    heading = title(args.title or "Definition of Done", designation(docs, "definition-of-done", "definition of done"))
    props = {"type": "definition-of-done", "id": "DOD", "title": heading,
             "status": "draft", "revision": 1, "aliases": ["DOD"],
             "tags": ["doc/definition-of-done", "status/draft"]}
    dod_body = "\n".join([
        f"# {heading}", "",
        "## Commands", "", "Record project verification commands.", "",
        "## Evidence Rules", "", "Record the evidence required for each gate.", "",
        "## Quality Gates", "", "Record the acceptance and review gates.", "",
        "## Navigation", "", link("maps/delivery", "Delivery map"), "",
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
    item_links = [link(f"delivery/deliveries/{root.name}/items/{id_slug(story)}/item", story) for story in stories]
    dod_link = link(dod_snapshot["definition_of_done_path"].removesuffix(".md"), "Definition of Done")
    props = {"type": "delivery", "id": identifier, "title": title(args.goal, designation(docs, "delivery", "delivery")),
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
        item_props = {"type": "delivery-item", "title": title(story, designation(docs, "delivery-item", "delivery item")),
                      "status": "in_scope", "derives_from": [story], "related_to": [identifier],
                      **{key: source[key] for key in SOURCE_ITEM_FIELDS},
                      "depends_on": source["depends_on"],
                      "execution_after": [], "dependency_bindings": [],
                      "waits_for": [], "waits_for_bindings": [],
                      "path_claims": [], "contract_claims": [],
                      "runtime_required": False,
                      "architecture_impact": "not_applicable", "architecture_components": [],
                      "architecture_record_kinds": [], "architecture_reason": "No architecture delta is currently required.",
                      "role_sequence": [source["owner_role"], *source["supporting_roles"], "code_reviewer", "qa_engineer"],
                      "tags": ["doc/delivery-item", "status/in-scope"]}
        atomic_text(item, frontmatter(item_props, body_for("item", item_props["title"], {
            "Delivery Scope": identifier, "Navigation": link(f"delivery/deliveries/{root.name}/delivery", identifier),
        })))
    render_map(docs)
    print(json.dumps({"ok": True, "id": identifier, "slug": slug, "path": str(root), "stories": stories}, indent=2))
    return 0


def delivery_findings(docs: Path, identifier: str) -> tuple[Path | None, list[str]]:
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
        _, source_errors = delivery_source_findings(docs, root, props)
        errors.extend(source_errors)
    if props.get("status") in {"execution_approved", "active", "review", "pr_handoff", "awaiting_merge"}:
        for item_path in item_paths:
            try:
                item_props, _item_body = split_note(item_path)
            except (OSError, ValueError):
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
    if not value or value.startswith("/") or "\\" in value:
        return False
    path = PurePosixPath(value)
    return value == path.as_posix() and all(part not in {"", ".", ".."} for part in path.parts)


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
                built_paths = [str(available[component].get("code_path", ""))
                               for component in architecture_components
                               if available.get(component, {}).get("sourcing") == "build"]
                if paths and built_paths and any(
                        not any(path == code_path or path.startswith(code_path + "/")
                                for code_path in built_paths)
                        for path in paths):
                    errors.append(f"{story_id} path_claims must stay below the selected built component code_path")
                if paths and not built_paths:
                    errors.append(f"{story_id} external-only architecture impact cannot claim project source paths")
            except (ImportError, ValueError) as exc:
                errors.append(f"{story_id} architecture impact cannot resolve the current Solution catalog: {exc}")
            expected_roles = ["software_architect", source["owner_role"], *source["supporting_roles"], "code_reviewer", "qa_engineer"]
        else:
            if architecture_components or architecture_kinds:
                errors.append(f"{story_id} non-applicable architecture impact cannot declare architecture claims")
            expected_roles = [source["owner_role"], *source["supporting_roles"], "code_reviewer", "qa_engineer"]
        if roles != expected_roles:
            errors.append(
                f"{story_id} role_sequence must be owner/supporting roles followed by code_reviewer and qa_engineer"
            )
        if len(roles) != len(set(roles)):
            errors.append(f"{story_id} role_sequence contains duplicate roles")
        for claim in paths:
            if not _is_normalized_claim(claim):
                errors.append(f"{story_id} path_claim is not normalized: {claim}")
                continue
            previous = path_owners.setdefault(claim, story_id)
            if previous != story_id:
                errors.append(f"path_claim {claim} is owned by both {previous} and {story_id}")
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


def approve_execution(args) -> int:
    docs = docs_root(args.docs)
    root, findings = delivery_findings(docs, args.delivery)
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
    sources, source_errors = delivery_source_findings(docs, root, props)
    plan_errors = source_errors + execution_plan_findings(root, sources, docs)
    if plan_errors:
        print(json.dumps({"ok": False, "errors": sorted(set(plan_errors))}, indent=2)); return 1
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
        item_props["dependency_bindings"] = sorted(
            set(sources[story]["depends_on"]) & set(item_props["execution_after"])
        )
        item_props["waits_for_bindings"] = sorted(
            set(sources[story]["depends_on"]) - set(item_props["execution_after"])
        )
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
                ev_props = {"type": kind, "id": f"{props['id']}-{story}-{'CR' if kind == 'code-review' else 'QA'}",
                            "title": title(story, designation(docs, kind, kind.replace('-', ' '))),
                            "status": status, "derives_from": [link(str(item_path.relative_to(docs)), story)],
                            "item_plan_hash": item_props["item_plan_hash"], "tags": [f"doc/{kind}", f"status/{status}"]}
                atomic_text(evidence, frontmatter(ev_props, body_for("item", ev_props["title"], {"Navigation": link(str(item_path.relative_to(docs)), story)})))
    plan_path = root / "execution-plan.md"
    plan_props = {"type": "execution-plan", "id": f"{props['id']}-EXEC", "title": title(props.get("goal", props["id"]), designation(docs, "execution-plan", "execution plan")),
                  "status": "approved", "revision": 1, "scope_hash": props["scope_hash"],
                  "item_plan_hashes": sorted(item_hashes),
                  "operation_contract_hashes": sorted(operation_hashes),
                  "derives_from": [link(str(path.relative_to(docs)), props["id"])],
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
        "Navigation": link(str(path.relative_to(docs)), props["id"]),
    })
    plan_props["plan_hash"] = content_hash(plan_props, plan_body, exclude=MUTABLE | {"plan_hash"})
    plan_props["approved_at_utc"] = utc_now()
    plan_props["source_hash"] = content_hash(plan_props, plan_body)
    atomic_text(plan_path, frontmatter(plan_props, plan_body))
    props["status"] = "execution_approved"
    props["plan_hash"] = plan_props["plan_hash"]
    props["source_hash"] = content_hash(props, body)
    props["tags"] = [tag for tag in props.get("tags", []) if not str(tag).startswith("status/")] + ["status/execution-approved"]
    atomic_text(path, frontmatter(props, body))
    print(json.dumps({"ok": True, "id": props["id"], "plan_hash": props["plan_hash"], "items": item_ids}, indent=2)); return 0


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
    if dirty:
        print(json.dumps({"ok": False, "errors": ["commit or remove all Item worktree changes before approving evidence"]}, indent=2)); return 2
    root = find_delivery(docs, args.delivery)
    item = root / "items" / id_slug(args.story) / "item.md" if root else None
    if item is None or not item.exists():
        print(json.dumps({"ok": False, "errors": ["Delivery Item not found"]}, indent=2)); return 1
    review = item.parent / "code-review.md"
    verification = item.parent / "verification.md"
    if not review.exists() or not verification.exists():
        print(json.dumps({"ok": False, "errors": ["Item evidence files are not initialized"]}, indent=2)); return 1
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
    review_props = {"type": "delivery-review", "id": f"{args.delivery}-REVIEW",
                    "title": title(delivery_props.get("goal", args.delivery), designation(docs, "delivery-review", "delivery review")),
                    "status": "approved", "derives_from": [link(str(delivery_path_value.relative_to(docs)), args.delivery)],
                    "plan_hash": delivery_props.get("plan_hash", "none"),
                    "reviewed_commit": reviewed_commit,
                    "reviewed_integration_commit": reviewed_integration,
                    "approved_at_utc": utc_now(), "tags": ["doc/delivery-review", "status/approved"]}
    review_body = body_for("delivery-review", review_props["title"], {"Goal Outcome": delivery_props.get("goal", ""), "Verdict": "Approved for PR handoff.", "Navigation": link(str(delivery_path_value.relative_to(docs)), args.delivery)})
    review_props["approval_hash"] = content_hash(review_props, review_body, exclude=MUTABLE | {"approval_hash"})
    review_props["source_hash"] = content_hash(review_props, review_body)
    atomic_text(review_path, frontmatter(review_props, review_body))
    delivery_props["status"] = "review"
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
