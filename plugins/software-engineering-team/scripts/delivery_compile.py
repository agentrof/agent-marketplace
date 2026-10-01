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
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import atomic_file
from ba_compile import (
    frontmatter_item, frontmatter_scalar, parse_frontmatter, without_generated_relations,
)
import backlog_compile
import delivery_result
import operation_compile
import process_policy
import requirement_compile
import requirement_route
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
PROCESS_POLICY_SOURCE_FIELDS = process_policy.PIN_FIELDS
GIT_OID_RE = re.compile(r"^[0-9a-f]{40,64}$")
# The record of the "Record PR" commit that delivery_git writes as the PR head.
PR_RECORDED = "pr-url-recorded-v1"
REVIEW_LOOP = "review_loop"
FOLLOW_UP_COLUMNS = ("finding", "severity", "file", "description", "owner_role", "revisit_trigger")
# Each compiler-owned block starts at its marker line, after any authored text.
ITEM_FOLLOW_UPS = "Open code review follow-ups, copied by approve-item-evidence:"
DELIVERY_FOLLOW_UPS = "Open code review follow-ups of the integrated Items, listed by approve-review:"
CALIBRATION_COLUMNS = ("finding", "claimed_severity", "calibrated_severity", "reason")
ITEM_CALIBRATION = ("Severity calibration of the open critical and major claims,"
                    " recorded by approve-item-evidence:")
OWNER_GATES = "owner_gates"
TWO_FIXED_GATES = "two_fixed_gates"
USER_DECISION_COLUMNS = ("id", "class", "question", "options", "recommendation", "status",
                         "answer", "blocks", "wait_minutes")
USER_DECISION_STATUSES = ("pending", "answered")
USER_DECISION_ID_RE = re.compile(r"^D-[0-9]{2,}$")
QUEUED_DECISION_CLASS = "queued"
# The owner's questions are logged from the scope proposal through gate B.
DECISION_LOG_STATUSES = ("scope_proposed", "scope_approved", "execution_approved", "active", "review")
DELIVERY_PATH_SWITCH = "delivery_path"
LIGHT_WHEN_ELIGIBLE = "light_when_eligible"
# What a Delivery meets to plan its scope and its execution in one step with one
# owner gate. topology_unchanged applies once scope approval recorded the light path.
LIGHT_PATH_CONDITIONS = ("single_story", "no_architect_role", "architecture_not_applicable",
                         "operation_contracts_unchanged", "dependencies_met",
                         "within_story_size_budget", "topology_unchanged")
# The light path runs from the proposal until its Items are claimed.
LIGHT_PATH_STATUSES = ("scope_proposed", "scope_approved", "execution_approved")
# Execution approval stamps a plan on an approved scope. A gate that shows the
# plan runs before it, in gate A and on the light path even before scope approval.
EXECUTION_APPROVAL_STATUSES = ("scope_approved", "execution_approved")
PLAN_GATE_STATUSES = ("scope_proposed", *EXECUTION_APPROVAL_STATUSES)
# init writes this before any Software Architect has stated a reason.
NO_ARCHITECTURE_REASON = "No architecture delta is currently required."
# What a topology pass authors on an Item; the light path's record binds them with its body.
LIGHT_TOPOLOGY_FIELDS = ("execution_after", "waits_for", "path_claims", "contract_claims",
                         "runtime_required", "architecture_impact", "architecture_components",
                         "architecture_record_kinds", "architecture_reason", "role_sequence",
                         "verification_schedule", "implementation_schedule", "lane_scopes",
                         "lane_seams")
DELIVERY_PATH_LINE_RE = re.compile(r"^Delivery path: (light|standard)\b")
PATH_RECEIPT_RE = re.compile(r"\b(verification|environment) revision ([1-9][0-9]*) (sha256:[0-9a-f]{64})\b")
PATH_TOPOLOGY_RE = re.compile(r"\btopology (sha256:[0-9a-f]{64})\b")


atomic_text = atomic_file.replace_text


def docs_root(value: str | Path) -> Path:
    path = Path(value).resolve()
    if path.name == "docs":
        return path
    if (path / "docs").is_dir():
        return path / "docs"
    if (path / "workspace" / "docs").is_dir():
        return path / "workspace" / "docs"
    return path


def frontmatter(props: dict, body: str) -> str:
    rows = ["---"]
    for key, value in props.items():
        if isinstance(value, list):
            rows.append(f"{key}:")
            rows.extend(f"  - {frontmatter_item(item)}" for item in value)
        else:
            rows.append(f"{key}: {frontmatter_scalar(value)}")
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
    story_size: dict | None = None,
) -> tuple[dict[str, dict], dict, list[str]]:
    """Resolve the exact approved Story/Test Plan snapshots a Delivery may use.

    Delivery is deliberately a consumer of the canonical backlog.  It must not
    accept caller-provided hashes or treat a generated registry as a source of
    truth, so this resolver checks the authored package and its approval stamps
    before exposing one selected Story. With a story size budget, each selected
    Story also carries its measures under ``story_size`` for display only.
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
    if story_size is not None:
        entries = backlog_compile.story_size_entries(record, docs, story_size, set(selected))
        for story_id, entry in entries.items():
            selected[story_id]["story_size"] = entry
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


def with_process_policy_pin(props: dict, policy: dict) -> dict:
    """Return the Delivery front matter pinned to the Process Policy snapshot.

    Without a policy the front matter keeps its bytes: no key is added. The
    pin sits before the aliases and tags, beside the other source pins.
    """
    pinned = {key: value for key, value in props.items() if key not in PROCESS_POLICY_SOURCE_FIELDS}
    if not policy:
        return pinned
    anchor = next((key for key in ("aliases", "tags") if key in pinned), None)
    result: dict = {}
    for key, value in pinned.items():
        if key == anchor:
            result.update(policy)
        result[key] = value
    if anchor is None:
        result.update(policy)
    return result


def delivery_switch_value(docs: Path, delivery_id: str, switch: str) -> str:
    """Return the value of a process switch that a Delivery runs under.

    Without a Process Policy the switch is at its package default. A draft or
    invalid policy, or a pin whose value of the switch drifted while the pin is
    enforced, raises ValueError, as process_policy.py value --delivery refuses
    it. From the Delivery Review on, the value is the pinned revision's.
    """
    state = process_policy.delivery_values(docs, delivery_id, switches={switch})
    return state["values"][switch]["value"]


def policy_owner_gates(docs: Path) -> str | None:
    """Return the owner_gates value the Process Policy selects, or None when it cannot be read."""
    try:
        values, _snapshot = process_policy.effective_values(docs)
    except ValueError:
        return None
    return values.get(OWNER_GATES, {}).get("value")


def delivery_owner_gates(docs: Path, props: dict) -> str | None:
    """Return the owner_gates value a Delivery keeps its decision log under.

    Before scope approval the current policy decides. Later only a pin that
    still names the approved policy does: a policy revised or drafted since
    decides nothing here, and the pin checks report that drift where it
    blocks. Outside the gate window there is no open log to check.
    """
    status = props.get("status")
    if status not in DECISION_LOG_STATUSES:
        return None
    try:
        values, snapshot = process_policy.effective_values(docs)
    except ValueError:
        return None
    if status != "scope_proposed" and process_policy.pin_findings(props, snapshot):
        return None
    return values.get(OWNER_GATES, {}).get("value")


def owner_decision_classes() -> set[str]:
    path = Path(__file__).resolve().parents[1] / "skill-content/deliver/data/owner-decision-classes.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {entry["id"] for entry in (*data["agent_clauses"], *data["classes"])}


def decision_rows(body: str) -> tuple[list[dict[str, str]], list[str]]:
    """Read the User Decisions table as rows, with what keeps it from being read."""
    return backlog_compile.structured_table(section_bodies(body).get("User Decisions", ""),
                                            USER_DECISION_COLUMNS, "delivery.md", "User Decisions")


def holds_decision_log(body: str) -> bool:
    """Whether the User Decisions section holds the table two fixed owner gates keep."""
    lines = [line for line in section_bodies(body).get("User Decisions", "").splitlines()
             if line.strip().startswith("|")]
    return bool(lines) and tuple(cell.casefold().replace(" ", "_")
                                 for cell in backlog_compile.table_cells(lines[0])) == USER_DECISION_COLUMNS


def keeps_decision_log(docs: Path, props: dict, body: str) -> bool:
    """Whether a Delivery keeps the owner's questions in its User Decisions table.

    Its owner_gates value keeps the log from the proposal through gate B. A
    section that holds the log's table keeps it whatever policy is in force
    now, so a policy set for the next Delivery never turns this one's log off.
    """
    return holds_decision_log(body) or delivery_owner_gates(docs, props) == TWO_FIXED_GATES


def decision_blocks(row: dict[str, str]) -> list[str]:
    """The Story ids of the Items a decision row blocks."""
    return [story.strip() for story in row["blocks"].split(";") if story.strip()]


def pending_decisions(body: str, stories=None) -> list[str]:
    """The ids of the pending decision rows, or of those that block an Item of *stories*."""
    rows, _errors = decision_rows(body)
    wanted = None if stories is None else {str(story).casefold() for story in stories}
    return [row["id"] for row in rows if row["status"] == "pending"
            and (wanted is None or wanted & {story.casefold() for story in decision_blocks(row)})]


def pending_rows_text(ids: list[str]) -> str:
    return f"User Decisions {'row' if len(ids) == 1 else 'rows'} {', '.join(ids)}" \
           f" {'is' if len(ids) == 1 else 'are'} pending"


def user_decision_findings(body: str, stories: list[str]) -> list[str]:
    """Validate the User Decisions table a Delivery keeps under two fixed owner gates.

    A row names the Items that wait for its answer in blocks, by the Story ids
    of this Delivery's Items, and once answered records how long they waited.
    """
    rows, errors = decision_rows(body)
    classes = {QUEUED_DECISION_CLASS, *owner_decision_classes()}
    seen: set[str] = set()
    for number, row in enumerate(rows, 1):
        label = f"delivery.md User Decisions row {number}"
        if not USER_DECISION_ID_RE.fullmatch(row["id"]):
            errors.append(f"{label} id must be D- and at least two digits")
        elif row["id"] in seen:
            errors.append(f"{label} repeats id {row['id']}; every id is unique")
        seen.add(row["id"])
        if row["class"] not in classes:
            errors.append(f"{label} class must be {QUEUED_DECISION_CLASS} or an at-once class:"
                          f" {', '.join(sorted(classes - {QUEUED_DECISION_CLASS}))}")
        if not row["question"]:
            errors.append(f"{label} states no question")
        options = [option.strip() for option in row["options"].split(";") if option.strip()]
        if len(options) < 2:
            errors.append(f"{label} needs at least two options separated by semicolons")
        if row["recommendation"] not in options:
            errors.append(f"{label} recommendation must be one of its options")
        blocked = decision_blocks(row)
        unknown = [story for story in blocked if story not in stories]
        if unknown:
            errors.append(f"{label} blocks must name Items of this Delivery by Story id, separated by"
                          f" semicolons, not {', '.join(unknown)}")
        if row["status"] not in USER_DECISION_STATUSES:
            errors.append(f"{label} status must be {' or '.join(USER_DECISION_STATUSES)}")
        elif row["status"] == "answered" and not row["answer"]:
            errors.append(f"{label} is answered but records no answer")
        elif row["status"] == "pending" and row["answer"]:
            errors.append(f"{label} is pending but records an answer; only the owner's answer"
                          " closes a question")
        if row["wait_minutes"] and not re.fullmatch(r"[0-9]+", row["wait_minutes"]):
            errors.append(f"{label} wait_minutes must be a whole number of minutes")
        elif row["status"] == "answered" and blocked and not row["wait_minutes"]:
            errors.append(f"{label} is answered after {', '.join(blocked)} waited for it, so it records"
                          " that wait in wait_minutes")
    return errors


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
    # Scope approval writes the pin, and only a new execution approval can
    # re-pin it; outside those phases the pin is the Delivery's record.
    status = delivery_props.get("status")
    if status == "scope_proposed" or status in process_policy.PIN_ENFORCED_STATUSES:
        policy, policy_errors = process_policy.approved_snapshot(docs)
        errors.extend(policy_errors)
        if not policy_errors and status in process_policy.PIN_ENFORCED_STATUSES:
            errors.extend(process_policy.drift_findings(docs, delivery_props))

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
             (delivery_root(docs) / "definition-of-done.md", "Definition of Done"),
             (process_policy.path_for(docs), "Process Policy")]
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
        # The tracked status, never the one derived from Git history: the target
        # branch carries the Integration branch's bytes after a merge, so a map
        # that rendered history would go stale on one of the two.
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
    # One read-only candidate snapshot serves the strict read and the handoff check.
    with stage_package.candidate_session():
        # The proposal shows story sizes under story_size_budget; never a scope rule.
        try:
            budget, budget_errors = backlog_compile.story_size_budget(docs), []
        except ValueError as exc:
            budget, budget_errors = None, [str(exc)]
        sources, backlog_snapshot, source_errors = approved_backlog_sources(
            docs, stories, story_size=budget)
        dod_snapshot, dod_errors = approved_dod_source(docs)
        # New Items declare the implementation schedule the Process Policy selects.
        schedule, policy_errors = policy_implementation_schedule(docs)
        owner_gates = policy_owner_gates(docs)
        errors = sorted(set(source_errors + dod_errors + policy_errors + budget_errors))
        # The proposal refuses a selection that scope approval, the handoff, would refuse.
        if not errors:
            errors = handoff_binding_findings(
                docs, {story: sources[story]["story_path"] for story in stories})
        # Under switch delivery_path, the proposal reports whether it may take the light path.
        light = (light_path_state(docs, {story: sources[story] for story in stories}, None, budget)
                 if not errors and policy_delivery_path(docs) == LIGHT_WHEN_ELIGIBLE else None)
    if errors:
        delivery_result.write_line(json.dumps({"ok": False, "errors": errors}, indent=2, ensure_ascii=False))
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
        "User Decisions": ("\n".join(["| " + " | ".join(USER_DECISION_COLUMNS) + " |",
                                       "|" + "---|" * len(USER_DECISION_COLUMNS)])
                           if owner_gates == TWO_FIXED_GATES
                           else "Local scope proposal; awaiting scope approval."),
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
                      "architecture_record_kinds": [], "architecture_reason": NO_ARCHITECTURE_REASON,
                      "role_sequence": execution_roles(source),
                      "verification_schedule": verification_policy()["new_schedule"],
                      **new_item_lane_fields(schedule),
                      "tags": ["doc/delivery-item", "status/in-scope"]}
        atomic_text(item, frontmatter(item_props, body_for("item", item_props["title"], {
            "Delivery Scope": identifier, "Navigation": link(f"delivery/deliveries/{root.name}/delivery", identifier),
        })))
    render_map(docs)
    result = {"ok": True, "id": identifier, "slug": slug, "path": str(root), "stories": stories}
    if budget is not None:
        result["story_size"] = backlog_compile.story_size_block(
            budget, {story: sources[story]["story_size"] for story in stories})
    if light is not None:
        result["delivery_path"] = {"value": LIGHT_WHEN_ELIGIBLE, "eligible": light["eligible"],
                                   "failed": light["failed"], "pending": light["pending"],
                                   "receipts": light["receipts"]}
    print(json.dumps(result, indent=2))
    return 0


def pr_recorded_props(props: dict, body: str) -> dict | None:
    """Return a Delivery's front matter once its PR is recorded, or None when it keeps its status.

    Recording the PR hands a reviewed Delivery over to its merge. A cancelled
    Delivery publishes its cancellation through the same PR and stays cancelled.
    """
    if props.get("status") != "review":
        return None
    recorded = dict(props)
    recorded["status"] = "awaiting_merge"
    recorded["tags"] = [tag for tag in recorded.get("tags", []) if not str(tag).startswith("status/")] + ["status/awaiting-merge"]
    recorded["source_hash"] = content_hash(recorded, body)
    return recorded


class MergeStateUnknown(RuntimeError):
    """Git cannot tell whether a Delivery's recorded PR head was merged."""


def _git_query(cwd: Path, *args: str) -> str:
    """Run one read-only Git query in the checkout holding *cwd*.

    A query Git cannot answer raises MergeStateUnknown, so a missing or broken
    history is reported instead of reading as an unmerged Delivery.
    """
    try:
        result = subprocess.run(["git", "--no-replace-objects", "-C", str(cwd), *args],
                                capture_output=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        raise MergeStateUnknown(f"Delivery merge state cannot be evaluated: {exc}") from exc
    if result.returncode:
        detail = next((line.strip() for line in result.stderr.splitlines() if line.strip()),
                      f"git {args[0]} exited with {result.returncode}")
        raise MergeStateUnknown(f"Delivery merge state cannot be evaluated: {detail}")
    return result.stdout


def recorded_pr_merged(cwd: Path, delivery_id: str, head: str = "HEAD") -> bool:
    """Prove offline that *head* contains a merge of this Delivery's recorded PR head."""
    return merged_pr_record(cwd, delivery_id, head) is not None


def merged_pr_record(cwd: Path, delivery_id: str, head: str = "HEAD") -> str | None:
    """Return the recorded PR head of this Delivery that *head* holds a merge of, or None.

    *head* is the checked-out HEAD unless the caller names a commit, as the
    coordinator names the fetched target tip. The proof is a two-parent merge
    reachable from *head*, on any path, whose second parent is the Delivery's
    "Record PR" commit: its trailers name its record and this Delivery, and
    its only parent is the intent it names. The next Delivery's Integration
    reaches the target's merge only through the second parent of a target
    refresh, so the path is not restricted. The merge itself must carry no
    Agentrof-Record trailer: the coordinator marks every commit it writes with
    one, and its own two-parent commits, such as the reopen commit whose
    second parent is the Integration head, merge nothing into the target. The
    one caveat: a manual merge of the Integration branch into any other branch
    also counts. A fast-forward, a squash or a rewritten head proves nothing.
    A shallow history or a failed Git query raises MergeStateUnknown instead
    of proving nothing.
    """
    from delivery_git import trailer

    if not DELIVERY_ID_RE.fullmatch(delivery_id):
        return None
    if _git_query(cwd, "rev-parse", "--is-shallow-repository").strip() == "true":
        raise MergeStateUnknown("Delivery merge state cannot be evaluated in a shallow clone; "
                                "fetch the full history, for example with git fetch --unshallow")
    listed = _git_query(cwd, "rev-list", "--fixed-strings", "--all-match",
                        f"--grep=Agentrof-Record: {PR_RECORDED}",
                        f"--grep=Agentrof-Delivery: {delivery_id}", head, "--")
    heads = set()
    for oid in listed.split():
        header, _, message = _git_query(cwd, "cat-file", "commit", oid).partition("\n\n")
        parents = [line.split()[1] for line in header.splitlines() if line.startswith("parent ")]
        if (trailer(message, "Record") == PR_RECORDED and trailer(message, "Delivery") == delivery_id
                and parents == [trailer(message, "Intent")]):
            heads.add(oid)
    if not heads:
        return None
    # A commit that a recorded head already contains cannot merge it, so the
    # walk stops where the Delivery branched off.
    merges = _git_query(cwd, "rev-list", "--merges", "--parents",
                        head, "--not", *sorted(heads), "--")
    for fields in (line.split() for line in merges.splitlines()):
        if len(fields) == 3 and fields[2] in heads:
            _header, _, message = _git_query(cwd, "cat-file", "commit", fields[0]).partition("\n\n")
            if trailer(message, "Record") is None:
                return fields[2]
    return None


def delivery_state(root: Path, props: dict) -> tuple[object, str | None]:
    """Return a Delivery's semantic status and the finding that stops its derivation.

    A Delivery whose Review records its PR is merged once HEAD contains a merge
    of its recorded PR head. That holds in awaiting_merge and in review, where a
    PR recorded before the record set awaiting_merge left it. When Git cannot
    tell, the tracked status comes back with the finding that says why.
    """
    status = props.get("status")
    if status not in {"review", "awaiting_merge"}:
        return status, None
    try:
        review, _ = split_note(root / "delivery-review.md")
    except (OSError, ValueError):
        return status, None
    if not review.get("pull_request_url"):
        return status, None
    try:
        merged = recorded_pr_merged(root, str(props.get("id", "")))
    except MergeStateUnknown as exc:
        return status, str(exc)
    return ("merged" if merged else status), None


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
    stories: list[str] = []
    for item_path in item_paths:
        item_props, item_body = split_note(item_path)
        stories.append(str(item_props.get("story_id", "")))
        if item_props.get("type") != "delivery-item": errors.append(f"{item_path} type must be delivery-item")
        if item_props.get("status") not in ITEM_STATUSES: errors.append(f"{item_path} invalid Item status")
        for read_schedule in (verification_schedule, implementation_schedule):
            try:
                read_schedule(item_props)
            except ValueError as exc:
                errors.append(f"{item_path}: {exc}")
        errors.extend(f"{item_path} missing section: {name}" for name in sorted(set(SECTIONS["item"]) - sections(item_body)))
    if keeps_decision_log(docs, props, body):
        errors.extend(user_decision_findings(body, stories))
    plan = root / "execution-plan.md"
    if plan.exists():
        plan_props, plan_body = split_note(plan)
        if plan_props.get("type") != "execution-plan": errors.append("execution-plan.md type must be execution-plan")
        errors.extend(f"execution-plan.md missing section: {name}" for name in sorted(set(SECTIONS["execution-plan"]) - sections(plan_body)))
    if records_bundle_rulings(docs, props):
        errors.extend(ruling_id_findings(body))
    # Closed Deliveries preserve their pinned historical source baseline. Every
    # mutable Delivery phase must instead prove that its selected Story/Test
    # Plan and Definition of Done are still the exact approved source bytes.
    # A Delivery is closed as merged once HEAD contains a merge of its recorded
    # PR head; when Git cannot tell, that finding stands in for both checks.
    status, unknown = delivery_state(root, props)
    if unknown is not None:
        errors.append(unknown)
    elif status not in {"merged", "cancelled"}:
        _, source_errors = delivery_source_findings(docs, root, props, compare_pins=compare_source_pins)
        errors.extend(source_errors)
    if (check_item_operation_bindings and unknown is None
            and status in {"execution_approved", "active", "review", "pr_handoff", "awaiting_merge"}):
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
    status = delivery_state(root, props)[0] if root is not None else props.get("status")
    result = {"ok": not errors, "id": props.get("id"), "status": status, "errors": errors}
    delivery_result.write_line(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if not errors else 1


def implemented_requirement_findings(docs: Path, stories: dict[str, dict]) -> list[str]:
    """Require every Requirement a selected Story implements to route to backlog.

    The Requirement entry only inspects a terminal Requirement, so that finding
    routes to a backlog revision that re-traces or drops the Story instead.
    """
    paths = {f"requirements/{path.stem}": path for path in requirement_compile.requirement_paths(docs)}
    routes: dict[str, dict] = {}
    errors: list[str] = []
    for story_id, props in sorted(stories.items()):
        for value in backlog_compile.values(props, "implements"):
            parts = backlog_compile.split_wikilink(value)
            path = paths.get(parts[0]) if parts else None
            identifier = requirement_compile.requirement_id(path) if path else ""
            if not requirement_route.REQ_ID_RE.fullmatch(identifier):
                errors.append(f"{story_id} implements a link that resolves to no Requirement: {value}")
                continue
            if identifier not in routes:
                routes[identifier] = requirement_route.route(docs, identifier)
            routing = routes[identifier]
            status = routing.get("status")
            if status == "approved" and routing.get("action") == "backlog":
                continue
            if status in requirement_compile.TERMINAL_STATUSES:
                successor = ""
                if status == "superseded":
                    relation = requirement_compile.split_note(path)[0].get("superseded_by", "")
                    target = backlog_compile.split_wikilink(str(relation))
                    successor = requirement_compile.requirement_id(paths[target[0]]) \
                        if target and target[0] in paths else ""
                errors.append(
                    f"{story_id} implements {identifier}, which is {status}"
                    + (f" by {successor}" if successor else "")
                    + f" and cannot be rebound; begin a backlog revision that re-traces {story_id} "
                    f"to {successor or 'a current Requirement'} or drops it, before handoff"
                )
                continue
            reason = routing.get("reason", "")
            remedy = f"rebind it through the Requirement entry, /requirement {identifier}"
            if not reason and status != "approved":
                reason = f"Requirement status is {status}"
            elif not reason and routing.get("stage") == "requirement":
                # An approved Requirement whose semantic hash drifted routes to its own
                # stage with no reason; the Requirement compiler names the drift. That
                # entry cannot revise an invalid Requirement, so the text comes back first.
                reason = "; ".join(requirement_compile.requirement_findings(path, require_approved=True))
                remedy = (f"restore its approved text, since the Requirement entry cannot revise an "
                          f"invalid Requirement, then continue through /requirement {identifier}")
            errors.append(
                f"{story_id} implements {identifier}, which does not route to backlog: "
                f"stage {routing.get('stage', 'requirement')}, action {routing.get('action', 'requirement')}"
                + (f", reason: {reason}" if reason else "")
                + f"; {remedy}, before handoff"
            )
    return errors


def application_binding_findings(docs: Path, citing: list[str]) -> list[str]:
    """Require a backlog whose selected Stories cite Experience records to bind the current application.

    Compiler-owned input_bindings bind it in either planning mode. A
    requirement-mode backlog approved before those bindings existed is
    transitional: until its next revision it binds through its root
    Requirement's Experience Stage Results, and binds none when that
    Requirement marks Experience not_applicable. A backlog without a planning
    mode predates application receipts.
    """
    props, _ = backlog_compile.parse_front_matter(docs / "backlog" / "backlog.md")
    mode = str(props.get("planning_mode", "")).strip().casefold()
    if mode not in {"manual", "requirement"}:
        return []
    requirement = str(props.get("requirement_ref", "")).strip()
    disposition, results = "", []
    if mode == "requirement":
        path = next((path for path in requirement_compile.requirement_paths(docs)
                     if requirement_compile.requirement_id(path) == requirement), None)
        try:
            body = requirement_compile.split_note(path)[1] if path else ""
        except (OSError, ValueError):
            body = ""
        disposition = next((row[1] for row in requirement_compile.impact_rows(body)
                            if row[0] == "experience-design"), "")
        results = requirement_compile.stage_results(body).get("experience-design", [])
    bindings = backlog_compile.values(props, "input_bindings")
    bound = mode == "manual" or bool(bindings)
    label = (f"the {mode}-mode input_bindings" if bound
             else f"root Requirement {requirement}'s Experience Stage Results")
    refs: list[str] = []
    problems: list[str] = []
    if bound:
        rows, problems = backlog_compile.verify_input_bindings(
            docs, [binding for binding in bindings if binding.startswith("experience-design|")],
            "backlog/backlog.md")
        refs = [reference for _stage, reference, _digest in rows]
    elif disposition == "not_applicable":
        problems.append(f"root Requirement {requirement} marks experience-design not_applicable")
    else:
        refs = [reference for reference, _digest in results]
        for reference, digest in results:
            _receipt, invalid = stage_package.verify(
                docs, "experience-design", reference, digest,
                require_committed=True, require_strict_current=True)
            problems.extend(invalid)
    if not refs and not problems:
        problems.append(f"{label} hold no application receipt")
    elif refs and not requirement_compile.valid_experience_receipt_refs(refs, docs):
        problems.insert(0, f"{label} are not the current application with its exact process receipts")
    if not problems:
        return []
    current = next((str(item["result_ref"]) for item in stage_package.candidates(docs, "experience-design")
                    if item.get("result_type") == "experience-application"), "")
    if mode == "manual":
        remedy = "begin a manual-mode backlog revision whose --input-ref values pin it"
    elif disposition == "not_applicable":
        remedy = "begin a requirement-mode backlog revision that pins it with --input-ref"
    else:
        remedy = (f"rebind {requirement}'s Experience stage through /requirement {requirement}, "
                  "then begin a requirement-mode backlog revision that binds it")
    return [
        f"{', '.join(citing)} {'cites' if len(citing) == 1 else 'cite'} experience_refs, but the backlog "
        f"does not bind the globally current {current or 'application receipt, and none resolves now'}: "
        + "; ".join(problems) + f"; {remedy}, before handoff"
    ]


def handoff_binding_findings(docs: Path, story_paths: dict[str, str]) -> list[str]:
    """Refuse a selection whose Stories rest on non-current upstream bindings.

    A reserved Delivery keeps verifying its pinned inputs historically, so these
    rules apply only when the proposal is rendered and at scope approval: every
    Requirement a selected Story implements must route to backlog, and a
    selection that cites Experience records needs the backlog to bind the
    globally current application receipt.
    """
    stories = {story_id: backlog_compile.parse_front_matter(docs / path)[0]
               for story_id, path in story_paths.items()}
    citing = sorted(story_id for story_id, props in stories.items()
                    if backlog_compile.values(props, "experience_refs"))
    errors = implemented_requirement_findings(docs, stories)
    if citing:
        errors.extend(application_binding_findings(docs, citing))
    return errors


# One read-only candidate snapshot serves the historical read and the handoff check.
@stage_package.candidate_session()
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
    # Gate A asks every queued question, and its approval runs this write first.
    pending = pending_decisions(body) if keeps_decision_log(docs, props, body) else []
    if pending:
        errors.append(f"{pending_rows_text(pending)}; gate A asks every queued question, so record the"
                      " owner's answers before approve-scope")
    # Scope approval is the handoff: the selected Stories' upstream bindings must
    # be current now, while every later phase keeps the historical read above.
    if not errors:
        items = [split_note(item_path)[0] for item_path in sorted(root.glob("items/*/item.md"))]
        errors.extend(handoff_binding_findings(
            docs, {str(item["story_id"]): str(item["story_path"]) for item in items}))
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, indent=2)); return 1
    # delivery_findings above already refused a draft or invalid policy.
    props = with_process_policy_pin(props, process_policy.approved_snapshot(docs)[0])
    # Under switch delivery_path, the scope hash binds the path the Delivery takes.
    path_record = delivery_path_record(docs, root, body, "approve-scope")
    if path_record is not None:
        body = path_record[0]
    props["status"] = "scope_approved"
    props["scope_hash"] = content_hash(props, body, exclude=MUTABLE | {"scope_hash"})
    props["approved_at_utc"] = utc_now()
    props["source_hash"] = content_hash(props, body)
    props["tags"] = [tag for tag in props.get("tags", []) if not str(tag).startswith("status/")] + ["status/scope-approved"]
    atomic_text(path, frontmatter(props, body))
    render_map(docs)
    result = {"ok": True, "id": props["id"], "scope_hash": props["scope_hash"]}
    if path_record is not None:
        result["delivery_path"] = path_record[1]
    print(json.dumps(result, indent=2)); return 0


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


def verification_policy() -> dict:
    path = Path(__file__).resolve().parents[1] / "skill-content/deliver/data/delivery-verification-policy.json"
    return json.loads(path.read_text(encoding="utf-8"))


def verification_schedule(props: dict) -> str:
    contract = verification_policy()
    value = props.get("verification_schedule", contract["legacy_schedule"])
    if value not in contract["schedules"]:
        raise ValueError("unsupported verification_schedule")
    return value


def document_contract() -> dict:
    path = Path(__file__).resolve().parents[1] / "skill-content/deliver/data/delivery-document-contract.json"
    return json.loads(path.read_text(encoding="utf-8"))


def implementation_schedule(props: dict) -> str:
    """Read an Item's implementation schedule; an Item without one runs sequentially."""
    contract = document_contract()["document_types"]["delivery_item"]
    value = props.get("implementation_schedule", contract["missing_implementation_schedule"])
    if value not in contract["implementation_schedules"]:
        raise ValueError("unsupported implementation_schedule")
    return value


READER_ROLES = ("code_reviewer", "qa_engineer")
# Vault, Git and runtime state stay serial across lanes, so no lane scope reaches them.
LANE_EXCLUDED_ROOTS = ("workspace/docs", ".git", ".agentrof")
LANE_SEAM_RE = re.compile(r"^([a-z][a-z0-9_]*) -> ([a-z][a-z0-9_]*) via (\S(?:.*\S)?)$")


def lane_roles(props: dict) -> list[str]:
    """Return the Item's lane roles: its implementation roles except the Software Architect."""
    return [role for role in props.get("role_sequence", []) or []
            if role not in READER_ROLES and role != "software_architect"]


def lane_scope_map(props: dict) -> tuple[dict[str, list[str]], list[str]]:
    """Read lane_scopes, entries of `<role>:<path>`, as role to paths, with unreadable entries."""
    scopes: dict[str, list[str]] = {}
    unreadable: list[str] = []
    entries = props.get("lane_scopes", [])
    for entry in entries if isinstance(entries, list) else [entries]:
        role, separator, path = entry.partition(":") if isinstance(entry, str) else ("", "", "")
        if (not separator or not re.fullmatch(r"[a-z][a-z0-9_]*", role) or path != path.strip()
                or not _is_normalized_claim(path)):
            unreadable.append(str(entry))
        else:
            scopes.setdefault(role, []).append(path)
    return scopes, unreadable


def lane_seam_edges(props: dict) -> tuple[list[tuple[str, str, str]], list[str]]:
    """Read lane_seams, entries of `<producer> -> <consumer> via <interface>`, with unreadable entries."""
    edges: list[tuple[str, str, str]] = []
    unreadable: list[str] = []
    entries = props.get("lane_seams", [])
    for entry in entries if isinstance(entries, list) else [entries]:
        match = LANE_SEAM_RE.fullmatch(entry) if isinstance(entry, str) else None
        if match is None:
            unreadable.append(str(entry))
        else:
            edges.append((match.group(1), match.group(2), match.group(3)))
    return edges, unreadable


def lane_dependencies(props: dict) -> dict[str, list[str]]:
    """Map each lane role to the producer lanes its seams name, in role order.

    A consumer lane starts as soon as these producers have finished; a lane
    that no seam of it names never holds it back.
    """
    lanes = lane_roles(props)
    edges, unreadable = lane_seam_edges(props)
    if unreadable:
        raise ValueError("lane_seams cannot be read: " + ", ".join(unreadable))
    named = {role: {producer for producer, consumer, _interface in edges if consumer == role}
             for role in lanes}
    finished: set[str] = set()
    while len(finished) < len(lanes):
        ready = {role for role in lanes if role not in finished and named[role] <= finished}
        if not ready:
            raise ValueError("lane_seams contain a cycle or name a role without a lane")
        finished |= ready
    return {role: [lane for lane in lanes if lane in named[role]] for role in lanes}


def lane_phases(props: dict) -> list[list[str]]:
    """Group implementation roles: the Software Architect alone first, then every lane together.

    Inside the lane phase each lane waits only for the producers that
    lane_dependencies names for it.
    """
    lane_dependencies(props)
    lanes = lane_roles(props)
    phases = [["software_architect"]] if "software_architect" in (props.get("role_sequence") or []) else []
    return phases + ([lanes] if lanes else [])


def role_sequence_text(props: dict) -> str:
    """Render an Item's phases for Role Sequences; a consumer lane names the producers it waits for."""
    waits = lane_dependencies(props) if implementation_schedule(props) == "parallel_lanes_v1" else {}
    return " -> ".join(" + ".join(
        role + (f" (after {', '.join(waits[role])})" if waits.get(role) else "") for role in phase)
        for phase in execution_phases(props))


def execution_phases(props: dict) -> list[list[str]]:
    """Derive phase grouping without rewriting legacy plan inputs."""
    roles = list(props.get("role_sequence", []))
    if implementation_schedule(props) == "parallel_lanes_v1":
        readers = ([list(READER_ROLES)] if verification_schedule(props) == "parallel_snapshot_v1"
                   else [[role] for role in roles if role in READER_ROLES])
        return lane_phases(props) + readers
    if verification_schedule(props) == "parallel_snapshot_v1":
        return [[role] for role in roles if role not in {"code_reviewer", "qa_engineer"}] + [["code_reviewer", "qa_engineer"]]
    return [[role] for role in roles]


def _claims_overlap(first: str, second: str) -> bool:
    left, right = PurePosixPath(first), PurePosixPath(second)
    return left == right or left in right.parents or right in left.parents


def lane_plan_findings(story_id: str, props: dict, paths: list[str], contracts: list[str],
                       architecture_kinds: list[str]) -> list[str]:
    """Validate an Item's lane scopes and seams against its schedule and claims."""
    try:
        schedule = implementation_schedule(props)
    except ValueError as exc:
        return [f"{story_id} {exc}"]
    if schedule != "parallel_lanes_v1":
        if props.get("lane_scopes") or props.get("lane_seams"):
            return [f"{story_id} declares lane_scopes or lane_seams, which only"
                    " implementation_schedule parallel_lanes_v1 reads"]
        return []
    errors: list[str] = []
    lanes = lane_roles(props)
    if not lanes:
        return [f"{story_id} has no implementation role besides the Software Architect to run as a lane;"
                " declare implementation_schedule sequential_v1"]
    scopes, unreadable = lane_scope_map(props)
    errors.extend(f"{story_id} lane_scope must be <role>:<normalized path>: {entry}" for entry in unreadable)
    for role in sorted(set(scopes) - set(lanes)):
        errors.append(f"{story_id} lane_scopes name {role}, which "
                      + ("runs alone before the lanes and takes no lane scope" if role == "software_architect"
                         else "is not an implementation role of this Item"))
    for role in lanes:
        if role not in scopes:
            errors.append(f"{story_id} implementation role {role} has no lane scope; give it one or"
                          " declare implementation_schedule sequential_v1")
    owned = [(role, path) for role in sorted(scopes) for path in scopes[role]]
    if len(owned) != len(set(owned)):
        errors.append(f"{story_id} lane_scopes repeat an entry")
    for role, path in owned:
        excluded = next((root for root in LANE_EXCLUDED_ROOTS if _claims_overlap(path, root)), None)
        if excluded:
            errors.append(f"{story_id} lane scope {path} of {role} overlaps {excluded}, which stays serial")
    for index, (role, path) in enumerate(owned):
        for other_role, other in owned[index + 1:]:
            if role != other_role and _claims_overlap(path, other):
                errors.append(f"{story_id} lane scopes of {role} and {other_role} overlap: {path}, {other}")
    union = {path for _role, path in owned}
    if union != set(paths):
        missing, extra = sorted(set(paths) - union), sorted(union - set(paths))
        errors.append(f"{story_id} lane scopes must together equal path_claims"
                      + (f"; unassigned claims: {', '.join(missing)}" if missing else "")
                      + (f"; unclaimed lane paths: {', '.join(extra)}" if extra else ""))
    edges, unreadable = lane_seam_edges(props)
    errors.extend(f"{story_id} lane_seam must be <producer> -> <consumer> via <interface>: {entry}"
                  for entry in unreadable)
    if len(edges) != len(set(edges)):
        errors.append(f"{story_id} lane_seams repeat a seam")
    for producer, consumer, interface in edges:
        seam = f"{producer} -> {consumer} via {interface}"
        if producer not in lanes or consumer not in lanes or producer == consumer:
            errors.append(f"{story_id} lane seam {seam} must join two different lane roles")
        if interface in contracts:
            continue
        import architecture_compile
        kind = architecture_compile.kind_for_id(interface) if architecture_compile.RECORD.fullmatch(interface) else None
        if kind is None or kind[3] not in architecture_kinds:
            errors.append(f"{story_id} lane seam {seam} must name one of its contract_claims or an"
                          " architecture record id of a claimed record kind")
    if not errors:
        try:
            lane_dependencies(props)
        except ValueError as exc:
            errors.append(f"{story_id} {exc}")
    return errors


def policy_implementation_schedule(docs: Path) -> tuple[str | None, list[str]]:
    """Return the implementation schedule the Process Policy selects, or its refusal."""
    try:
        values, _snapshot = process_policy.effective_values(docs)
    except ValueError as exc:
        return None, [str(exc)]
    # A registry that does not declare the switch keeps today's order.
    missing = document_contract()["document_types"]["delivery_item"]["missing_implementation_schedule"]
    return values.get("implementation_schedule", {}).get("value", missing), []


def new_item_lane_fields(schedule: str | None) -> dict:
    """Return the lane fields every new Item declares; none keeps today's Item bytes."""
    missing = document_contract()["document_types"]["delivery_item"]["missing_implementation_schedule"]
    if schedule in {None, missing}:
        return {}
    return {"implementation_schedule": schedule, "lane_scopes": [], "lane_seams": []}


def execution_plan_findings(root: Path, sources: dict[str, dict], docs: Path,
                            reopen: list[str] | tuple = (),
                            pending: frozenset | set = frozenset()) -> list[str]:
    """Validate the authored Item topology before execution approval.

    The Delivery compiler owns hashes and rendered plan summaries. People own
    the topology, claims and role sequence, so approval rejects omitted or
    contradictory execution intent rather than silently inventing defaults.
    """
    errors: list[str] = []
    policy_schedule: str | None = None
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
        try:
            verification_schedule(props)
        except ValueError as exc:
            errors.append(f"{story_id} {exc}")
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
        errors.extend(lane_plan_findings(story_id, props, paths, contracts, architecture_kinds))
        # Lane roles bind their instructions only while the Process Policy selects
        # parallel lanes, so an Item that will still run lanes needs that value.
        if (props.get("implementation_schedule") == "parallel_lanes_v1"
                and (props.get("status") not in TERMINAL_ITEM_STATUSES or story_id in reopen)):
            if policy_schedule is None:
                policy_schedule, policy_errors = policy_implementation_schedule(docs)
                errors.extend(policy_errors)
            if policy_schedule not in {None, "parallel_lanes_v1"}:
                errors.append(f"{story_id} implementation_schedule parallel_lanes_v1 needs the Process"
                              " Policy to select it; declare sequential_v1 or set switch"
                              " implementation_schedule with /configure process")
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
    # current source contracts are approved and current. A kind in *pending* is an
    # open revision that the gate showing this plan approves before approval runs.
    if "verification" not in pending:
        _verification, verification_errors = operation_contract_snapshot(docs, "verification")
        errors.extend(verification_errors)
    if "environment" not in pending and any(split_note(path)[0].get("runtime_required", False)
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


def superseded_plan_approvals(root: Path, delivery_props: dict) -> list[str]:
    """Name every execution approval a new approval of this Delivery supersedes, newest first.

    An approval is named by the source_hash of its execution plan. Approval is
    offline, so it cannot tell whether the approval it replaces was published:
    it lists that approval together with everything that one superseded, and
    publication accepts any of them as the plan it revises. A plan hash cannot
    name an approval, because a re-approval that changes nothing keeps it and a
    revision that undoes another brings an earlier one back.
    """
    plan = root / "execution-plan.md"
    if delivery_props.get("status") != "execution_approved" or not plan.is_file():
        return []
    previous, _body = split_note(plan)
    approval, earlier = previous.get("source_hash"), previous.get("superseded_plan_approvals")
    return ([approval] if isinstance(approval, str) and approval else []) + (
        [str(value) for value in earlier] if isinstance(earlier, list) else [])


# Gate A's writes end with the Delivery's first execution approval and gate B's
# begin with approve-review, so a Delivery that keeps a decision log is between
# its two owner gates while it is execution_approved.
BETWEEN_GATES_STATUSES = ("execution_approved", "active")


def names_document(answer: str, document: str) -> bool:
    """Whether an answer names *document*, such as Verification Contract revision 6, as a whole phrase."""
    return re.search(rf"(?<![0-9A-Za-z]){re.escape(document)}(?![0-9A-Za-z])", answer,
                     re.IGNORECASE) is not None


def between_gates_refusals(docs: Path, document: str, delivery_id: str | None = None) -> list[str]:
    """Refuse an approval of *document* while a Delivery is between its two fixed owner gates.

    No approved document changes between gate A and gate B unless an answered
    User Decisions row names it in its answer. An Operation contract and the
    Delivery Governance serve every Delivery, so their approvals ask each one
    between its gates; an execution plan asks only its own Delivery.
    """
    errors = []
    for directory in delivery_dirs(docs):
        try:
            props, body = split_note(directory / "delivery.md")
        except (OSError, ValueError):
            continue
        if delivery_id is not None and props.get("id") != delivery_id:
            continue
        if props.get("status") not in BETWEEN_GATES_STATUSES or not keeps_decision_log(docs, props, body):
            continue
        rows, _errors = decision_rows(body)
        if not any(row["status"] == "answered" and names_document(row["answer"], document) for row in rows):
            errors.append(f"{props.get('id')} is between its two owner gates, where {document} is approved only"
                          " once an answered User Decisions row names it in its answer; queue the question and"
                          f" record the owner's answer naming {document} first")
    return errors


def plan_approval_refusals(docs: Path, root: Path, props: dict) -> list[str]:
    """Refuse an execution approval between the two fixed owner gates that no answered row names.

    Gate A covers the first execution approval of the approved scope. Each later
    approval is named by its number: the approvals it supersedes, plus one.
    """
    if props.get("status") not in BETWEEN_GATES_STATUSES:
        return []
    approval = len(superseded_plan_approvals(root, props)) + 1
    return between_gates_refusals(docs, f"execution plan approval {approval}", str(props.get("id")))


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


def approved_pull_request_checks(docs: Path, pending: bool = False) -> dict:
    """Where the approved current Verification Contract declares the Delivery PR checks come from.

    Without such a contract, approval is refused anyway and the default stands.
    A *pending* open revision that passes its own check is read as its approval
    will read it.
    """
    receipt, errors = operation_compile.check_contract(docs, "verification")
    props = {}
    if not errors and (receipt.get("current") or (pending and receipt.get("status") == "draft")):
        props, _body = operation_compile.parse(operation_compile.contract_path(docs, "verification"))
    source, provider = operation_compile.pull_request_checks(props)
    if source != "external":
        return {"source": source}
    return {"source": source, "provider": provider,
            "merge_requirement": "No repository workflow is required, but merge-pr still merges the "
                                 f"Delivery PR only on green checks, so {provider} must report them on it"}


def execution_approval_findings(docs: Path, root: Path, delivery_id: str, reopen: list[str] | tuple = (),
                                remote: str = "origin",
                                pending: frozenset | set = frozenset()) -> tuple[tuple, list[str]]:
    """Resolve what execution approval binds, with everything it refuses in the plan."""
    item_records, sources, backlog_snapshot, dod, source_errors = delivery_source_snapshots(docs, root)
    policy, policy_errors = process_policy.approved_snapshot(docs)
    errors = source_errors + policy_errors + reopen_findings(list(reopen), item_records)
    if not errors:
        errors = execution_plan_findings(root, sources, docs, reopen, pending)
    pull_request_checks = approved_pull_request_checks(docs, "verification" in pending)
    if pull_request_checks["source"] != "external":
        errors += pull_request_workflow_findings(docs, delivery_id, remote)
    return (item_records, sources, backlog_snapshot, dod, policy, pull_request_checks), sorted(set(errors))


def execution_approval_refusals(docs: Path, delivery_id: str, reopen: list[str] | tuple = (),
                                remote: str = "origin", statuses: tuple = EXECUTION_APPROVAL_STATUSES,
                                pending: frozenset | set = frozenset()) -> tuple[dict | None, list[str]]:
    """Return what execution approval binds, or everything it refuses, in the order it reports them.

    Approval runs on an approved scope. A gate that shows the plan before
    approval passes the statuses it runs in and the Operation contract kinds
    whose open revision it approves itself before execution approval runs.
    """
    # Approval writes the Item Operation bindings and refreshes the approved source
    # pins, so it cannot require either to already match. The contracts themselves are
    # still proved approved and current by execution_plan_findings before anything is
    # written, and the sources are re-resolved from the approved backlog.
    root, findings = delivery_findings(docs, delivery_id, check_item_operation_bindings=False,
                                       compare_source_pins=False)
    if root is None:
        return None, findings
    props, body = split_note(root / "delivery.md")
    if props.get("status") not in statuses:
        return None, ["Execution approval requires a scope-approved Delivery"]
    if findings:
        return None, findings
    if not any(root.glob("items/*/item.md")):
        return None, ["Execution Plan requires at least one Item"]
    inputs, errors = execution_approval_findings(docs, root, delivery_id, reopen, remote, pending)
    errors = [*light_record_findings(docs, root, body, delivery_id), *errors]
    if errors:
        return None, errors
    return {"root": root, "props": props, "body": body, "inputs": inputs}, []


def pending_operation_revisions(docs: Path, root: Path) -> tuple[list[dict], dict[str, str]]:
    """Return each open Operation revision the plan binds that its approval takes, and why it refuses the rest.

    Under two fixed owner gates, gate A approves such a revision before
    execution approval runs, so the gate may show the plan while it is open.
    Each revision is checked as its approval renders it, and its entry names
    the receipt that approval stamps, so gate A approves exact bytes. An open
    revision that passes its own check but not its approval's is refused by
    kind with what the approval finds.
    """
    runtime = any(split_note(path)[0].get("runtime_required", False)
                  for path in sorted(root.glob("items/*/item.md")))
    pending, refused = [], {}
    for kind in ("verification", "environment") if runtime else ("verification",):
        if not operation_compile.contract_path(docs, kind).is_file():
            continue
        receipt, errors = operation_compile.check_contract(docs, kind)
        if receipt.get("status") != "draft" or errors:
            continue
        try:
            approved, errors = operation_compile.check_contract(
                docs, kind, operation_compile.approval_text(docs, kind))
        except ValueError as exc:
            approved, errors = {}, [str(exc)]
        if errors:
            refused[kind] = (f"approved current {kind} contract is required: revision {receipt['revision']} is"
                             " open and its approval would refuse it: " + "; ".join(errors))
        else:
            pending.append({"kind": kind, "revision": approved["revision"],
                            "source_hash": approved["source_hash"]})
    return pending, refused


def check_plan(args) -> int:
    """Report everything execution approval would refuse, before an owner gate shows the plan."""
    docs = docs_root(args.docs)
    root = find_delivery(docs, args.delivery)
    status, pending, refused, decisions = None, [], {}, None
    if root is not None:
        props, body = split_note(root / "delivery.md")
        status = props.get("status")
        if delivery_owner_gates(docs, props) == TWO_FIXED_GATES:
            pending, refused = pending_operation_revisions(docs, root)
        # The queued questions the gate asks; the gate's writes refuse while one is pending.
        if keeps_decision_log(docs, props, body):
            decisions = pending_decisions(body)
    reopen = sorted(set(str(story) for story in (args.reopen or [])))
    with stage_package.candidate_session():
        # A refused revision is reported once, with what its approval finds.
        _approval, errors = execution_approval_refusals(
            docs, args.delivery, reopen, args.remote, PLAN_GATE_STATUSES,
            frozenset(revision["kind"] for revision in pending) | frozenset(refused))
    errors = [*refused.values(), *errors, *(plan_approval_refusals(docs, root, props) if root else [])]
    result = {"ok": not errors, "id": args.delivery, "status": status, "errors": errors,
              "pending_operation_revisions": pending}
    if decisions is not None:
        result["pending_decisions"] = decisions
    print(json.dumps(result, indent=2))
    return 0 if not errors else 1


def approve_execution(args) -> int:
    docs = docs_root(args.docs)
    reopen = sorted(set(str(story) for story in (getattr(args, "reopen", None) or [])))
    remote = getattr(args, "remote", "origin")
    approval, errors = execution_approval_refusals(docs, args.delivery, reopen, remote)
    if approval is not None:
        errors = plan_approval_refusals(docs, approval["root"], approval["props"])
    if approval is None or errors:
        print(json.dumps({"ok": False, "errors": errors}, indent=2)); return 1
    root, props, body = approval["root"], approval["props"], approval["body"]
    path = root / "delivery.md"
    items = sorted(root.glob("items/*/item.md"))
    _item_records, sources, backlog_snapshot, dod, policy, pull_request_checks = approval["inputs"]
    # Under switch delivery_path, execution approval keeps or leaves the light path record.
    path_record = delivery_path_record(docs, root, body, "approve-execution", remote)
    if path_record is not None:
        body = path_record[0]
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
        role_sequences.append(f"{story}: " + role_sequence_text(item_props))
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
    # Outside the plan hash, so a re-approval that changes nothing keeps it; the
    # source_hash below covers the lineage and so names this approval.
    plan_props["superseded_plan_approvals"] = superseded_plan_approvals(root, props)
    plan_props["approved_at_utc"] = utc_now()
    plan_props["source_hash"] = content_hash(plan_props, plan_body)
    atomic_text(plan_path, frontmatter(plan_props, plan_body))
    delivery_pins = {**backlog_snapshot, **{key: dod[key] for key in DOD_SOURCE_FIELDS}}
    refreshed_delivery_pins = sorted(
        [key for key, value in delivery_pins.items() if props.get(key) != value]
        + [key for key in PROCESS_POLICY_SOURCE_FIELDS if props.get(key) != policy.get(key)])
    props.update(delivery_pins)
    props = with_process_policy_pin(props, policy)
    props["status"] = "execution_approved"
    props["plan_hash"] = plan_props["plan_hash"]
    props["source_hash"] = content_hash(props, body)
    props["tags"] = [tag for tag in props.get("tags", []) if not str(tag).startswith("status/")] + ["status/execution-approved"]
    atomic_text(path, frontmatter(props, body))
    result = {"ok": True, "id": props["id"], "plan_hash": props["plan_hash"], "items": item_ids,
              "refreshed_sources": refreshed_sources, "refreshed_delivery_pins": refreshed_delivery_pins,
              "rebound": rebound, "pull_request_checks": pull_request_checks}
    if path_record is not None:
        result["delivery_path"] = path_record[1]
    print(json.dumps(result, indent=2)); return 0


def policy_delivery_path(docs: Path) -> str | None:
    """Return the delivery_path value the current Process Policy selects, or None when it cannot be read."""
    try:
        values, _snapshot = process_policy.effective_values(docs)
    except ValueError:
        return None
    return values.get(DELIVERY_PATH_SWITCH, {}).get("value")


def reused_contract_receipts(docs: Path, runtime: bool) -> tuple[list[dict], list[str]]:
    """Return the Operation contract receipts a light plan reuses, and why it cannot reuse them.

    The light path revises no contract: no revision may be open, and the
    Verification Contract, with the Environment Contract for a runtime Item,
    must be approved and current.
    """
    receipts: list[dict] = []
    problems: list[str] = []
    for kind in ("verification", "environment"):
        title = f"{kind.title()} Contract"
        needed = kind == "verification" or runtime
        if not operation_compile.contract_path(docs, kind).is_file():
            if needed:
                problems.append(f"the {title} is missing")
            continue
        receipt, errors = operation_compile.check_contract(docs, kind)
        if receipt.get("status") == "draft":
            problems.append(f"{title} revision {receipt.get('revision')} is open")
        elif needed and not receipt.get("current"):
            problems.append(f"the {title} is not approved and current: " + ", ".join(errors))
        elif needed:
            receipts.append({"kind": kind, "revision": receipt["revision"],
                             "source_hash": receipt["source_hash"]})
    return receipts, problems


def receipt_text(receipts: list[dict]) -> str:
    return " and ".join(f"{receipt['kind']} revision {receipt['revision']} {receipt['source_hash']}"
                        for receipt in receipts) or "no contract"


def waited_for_stories(sources: dict[str, dict], items: dict[str, tuple[dict, str]] | None) -> list[str]:
    """Every Story outside the selection that the selection waits for: approved dependencies and waits_for."""
    waited = {story for source in sources.values() for story in source["depends_on"]}
    for props, _body in (items or {}).values():
        declared = props.get("waits_for")
        if isinstance(declared, list):
            waited.update(story for story in declared if isinstance(story, str))
    return sorted(waited - set(sources))


def unmet_dependency_findings(docs: Path, stories: list[str], remote: str) -> list[str]:
    """Name each Story that no Delivery the target branch holds merged records integrated.

    The target's remote-tracking ref answers, as current as the last fetch,
    else HEAD; the proof is the one start-item accepts from a merged package.
    """
    if not stories:
        return []
    checkout = next((parent for parent in (docs, *docs.parents) if (parent / ".git").exists()), None)
    if checkout is None:
        return [f"{story} cannot be proven delivered outside a Git checkout" for story in stories]
    from delivery_git import merged_story_owners, resolve_target_branch
    try:
        ref = f"refs/remotes/{remote}/{resolve_target_branch(checkout, remote)}"
        if subprocess.run(["git", "-C", str(checkout), "rev-parse", "--verify", "--quiet", ref + "^{commit}"],
                          capture_output=True, check=False).returncode:
            ref = "HEAD"
        owners = merged_story_owners(checkout, ref, stories)
    except RuntimeError as exc:
        return [f"{story} cannot be proven delivered: {exc}" for story in stories]
    return [f"{story} is recorded integrated by no Delivery that the target branch holds merged"
            for story in stories if story not in owners]


def light_topology_hash(items: dict[str, tuple[dict, str]]) -> str:
    """Hash what a topology pass authors: each Item's topology fields and its body."""
    value = {story: {"fields": {key: props.get(key) for key in LIGHT_TOPOLOGY_FIELDS},
                     "body": without_generated_relations(body).rstrip() + "\n"}
             for story, (props, body) in sorted(items.items())}
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                  separators=(",", ":")).encode("utf-8")).hexdigest()


def light_path_state(docs: Path, sources: dict[str, dict], items: dict[str, tuple[dict, str]] | None,
                     budget: dict | None, recorded: dict | None = None, remote: str = "origin") -> dict:
    """Evaluate every light-path condition on a selection and, once it exists, its Item topology.

    Before the topology pass there are no Items to read, so
    architecture_not_applicable is pending. After scope approval recorded the
    light path, the plan must still bind the Item topology and the contract
    receipts recorded there.
    """
    failed: list[dict] = []

    def fail(condition: str, finding: str) -> None:
        failed.append({"condition": condition, "finding": " ".join(finding.split())})

    stories = sorted(sources)
    if len(stories) != 1:
        fail("single_story", f"the selection holds {len(stories)} Stories, and the light path plans exactly one")
    for story in stories:
        if "software_architect" in {sources[story]["owner_role"], *sources[story]["supporting_roles"]}:
            fail("no_architect_role", f"{story} lists software_architect among its roles")
    for story in stories if items is not None else []:
        props = items.get(story, ({}, ""))[0]
        impact = props.get("architecture_impact")
        reason = str(props.get("architecture_reason", "")).strip()
        if impact != "not_applicable":
            fail("architecture_not_applicable", f"{story} declares architecture_impact {impact or 'nothing'}")
        elif reason in {"", NO_ARCHITECTURE_REASON}:
            fail("architecture_not_applicable", f"{story} declares no architecture impact without the"
                 " Software Architect's reason, since the reason init writes is a placeholder")
    runtime = any(props.get("runtime_required") is True for props, _body in (items or {}).values())
    receipts, problems = reused_contract_receipts(docs, runtime)
    if recorded is not None and not problems and receipt_text(receipts) != receipt_text(recorded["receipts"]):
        problems.append(f"the plan binds {receipt_text(receipts)}, not"
                        f" {receipt_text(recorded['receipts'])} that scope approval recorded")
    for problem in problems:
        fail("operation_contracts_unchanged", problem)
    for finding in unmet_dependency_findings(docs, waited_for_stories(sources, items), remote):
        fail("dependencies_met", finding)
    if budget is None or not budget.get("limits"):
        fail("within_story_size_budget", "switch story_size_budget sets no size limit, and without an"
             " owner-set limit no Story counts as small")
    else:
        for story in stories:
            entry = sources[story].get("story_size")
            if entry is None:
                fail("within_story_size_budget", f"{story} has no size measures")
                continue
            for name in entry["over_budget"]:
                measure = entry["measures"][name]
                fail("within_story_size_budget",
                     f"{story} {name} is {measure['value']}, over its limit of {measure['limit']}")
    topology = light_topology_hash(items) if items is not None else None
    if recorded is not None and topology != recorded["topology"]:
        fail("topology_unchanged", "the Item topology differs from the one scope approval recorded")
    return {"eligible": not failed, "failed": failed,
            "pending": [] if items is not None else ["architecture_not_applicable"],
            "receipts": receipts, "topology_hash": topology}


def light_path_evaluation(docs: Path, root: Path, recorded: dict | None, remote: str = "origin") -> dict:
    """Evaluate the light-path conditions on one Delivery's Items and approved Stories."""
    items: dict[str, tuple[dict, str]] = {}
    for item_path in sorted(root.glob("items/*/item.md")):
        props, body = split_note(item_path)
        items[str(props.get("story_id", ""))] = (props, body)
    budget = backlog_compile.story_size_budget(docs)
    sources, _snapshot, errors = approved_backlog_sources(docs, sorted(items), historical_inputs=True,
                                                           story_size=budget)
    if errors:
        return {"eligible": False, "pending": [], "receipts": [], "topology_hash": None,
                "failed": [{"condition": DELIVERY_PATH_SWITCH, "finding": " ".join(
                    ("the approved Stories cannot be read: " + ", ".join(errors)).split())}]}
    return light_path_state(docs, sources, items, budget, recorded, remote)


def recorded_delivery_path(body: str) -> dict | None:
    """Read the Delivery path line that approval keeps in User Decisions, or None without one."""
    for line in section_bodies(body).get("User Decisions", "").splitlines():
        match = DELIVERY_PATH_LINE_RE.match(line.strip())
        if match:
            topology = PATH_TOPOLOGY_RE.search(line)
            return {"path": match.group(1), "line": line.strip(),
                    "topology": topology.group(1) if topology else None,
                    "receipts": [{"kind": kind, "revision": int(revision), "source_hash": digest}
                                 for kind, revision, digest in PATH_RECEIPT_RE.findall(line)]}
    return None


def with_delivery_path_line(body: str, line: str) -> str:
    """Put the one Delivery path line first in User Decisions and keep every other line as written."""
    kept = [row for row in section_bodies(body).get("User Decisions", "").splitlines()
            if not DELIVERY_PATH_LINE_RE.match(row.strip())]
    rest = "\n".join(kept).strip()
    return replace_section(body, "User Decisions", line + ("\n\n" + rest if rest else ""))


def standard_path_line(step: str, left: bool, failed: list[dict]) -> str:
    """The Delivery path line that records the standard path and each failed condition."""
    reason = "the Delivery left the light path" if left else "the light path does not hold"
    return (f"Delivery path: standard. {step} recorded that {reason}: "
            + "; ".join(f"{failure['condition']}: {failure['finding']}" for failure in failed) + ".")


def delivery_path_record(docs: Path, root: Path, body: str, step: str,
                         remote: str = "origin") -> tuple[str, dict] | None:
    """Record in User Decisions the path an approval finds, or None when switch delivery_path takes no part.

    Scope approval records light when every condition holds, with the Item
    topology and the contract receipts it approves, and standard otherwise.
    Execution approval refuses a plan whose topology or receipts differ from a
    light record, so here it keeps the record while every other condition
    holds, and records standard otherwise or for a scope approved without one.
    A standard record never turns light.
    """
    value = policy_delivery_path(docs)
    recorded = recorded_delivery_path(body)
    if value != LIGHT_WHEN_ELIGIBLE and recorded is None:
        return None
    if recorded is not None and recorded["path"] == "standard":
        return body, {"path": "standard", "failed": [], "line": recorded["line"]}
    if value != LIGHT_WHEN_ELIGIBLE:
        state = {"eligible": False, "failed": [{"condition": DELIVERY_PATH_SWITCH, "finding":
                 f"the Process Policy that {step} pins sets it to {value or 'an unreadable value'}"}]}
    elif step == "approve-execution" and recorded is None:
        state = {"eligible": False, "failed": [{"condition": DELIVERY_PATH_SWITCH, "finding":
                 "scope approval recorded no light path"}]}
    else:
        state = light_path_evaluation(docs, root, recorded, remote)
    if state["eligible"]:
        line = recorded["line"] if recorded is not None else (
            f"Delivery path: light. {step} approved the scope with the Item topology"
            f" {state['topology_hash']} and the reused contract receipts {receipt_text(state['receipts'])}.")
    else:
        line = standard_path_line(step, recorded is not None, state["failed"])
    return (with_delivery_path_line(body, line),
            {"path": "light" if state["eligible"] else "standard", "failed": state["failed"], "line": line})


def light_record_findings(docs: Path, root: Path, body: str, delivery_id: str) -> list[str]:
    """Refuse a plan other than the one the light path's one owner gate approved.

    Scope approval records the Item topology and the contract receipts that gate
    approved. Until the Delivery records its fallback, execution approval takes
    only that plan, since the owner saw no other: a changed one goes to the
    owner gate of /execution-plan.
    """
    recorded = recorded_delivery_path(body)
    if recorded is None or recorded["path"] != "light":
        return []
    items: dict[str, tuple[dict, str]] = {}
    for item_path in sorted(root.glob("items/*/item.md")):
        props, item_body = split_note(item_path)
        items[str(props.get("story_id", ""))] = (props, item_body)
    failed = []
    if light_topology_hash(items) != recorded["topology"]:
        failed.append("topology_unchanged: the Item topology differs from the one scope approval recorded")
    runtime = any(props.get("runtime_required") is True for props, _body in items.values())
    receipts: list[dict] | None = []
    for kind in ("verification", "environment") if runtime else ("verification",):
        receipt, errors = operation_compile.check_contract(docs, kind)
        if errors or not receipt.get("current"):
            # The plan's own checks refuse a contract that is not approved and current.
            receipts = None
            break
        receipts.append({"kind": kind, "revision": receipt["revision"], "source_hash": receipt["source_hash"]})
    if receipts is not None and receipt_text(receipts) != receipt_text(recorded["receipts"]):
        failed.append(f"operation_contracts_unchanged: the plan binds {receipt_text(receipts)}, not"
                      f" {receipt_text(recorded['receipts'])} that scope approval recorded")
    if not failed:
        return []
    return [f"{delivery_id} left the light path after its one owner gate: {'; '.join(failed)}; the owner has"
            " not seen this plan, so run light-path-check, which records the fallback, and take the plan to"
            f" the owner gate of /execution-plan {delivery_id}"]


def record_delivery_path_line(path: Path, props: dict, body: str, line: str) -> None:
    """Write the Delivery path line into delivery.md, keeping an approved record's source_hash current."""
    body = with_delivery_path_line(body, line)
    if "source_hash" in props:
        props = {**props, "source_hash": content_hash(props, body)}
    atomic_text(path, frontmatter(props, body))


# The steps the light path's one owner gate authorizes, in their order.
LIGHT_SEQUENCE = ("approve-scope", "reserve-delivery", "approve-execution", "publish-execution-plan",
                  "claim-items")


def light_path_check(args) -> int:
    """Repeat the light-path conditions and the plan's approval checks before a light step.

    The first check that fails ends the light path for good. It records the
    fallback as the Delivery's path line, so a later check or approval that
    finds every condition met again keeps the standard path. A step of the
    light sequence that was refused, named with --refused, records it the same way.
    """
    docs = docs_root(args.docs)
    root = find_delivery(docs, args.delivery)
    refused = getattr(args, "refused", None)

    def refuse(error: str, fallback: str | None = None) -> int:
        result = {"ok": False, "errors": [error]}
        if fallback is not None:
            result["fallback"] = fallback
        print(json.dumps(result, indent=2)); return 1

    if root is None:
        return refuse("Delivery not found")
    props, body = split_note(root / "delivery.md")
    status = props.get("status")
    if status not in LIGHT_PATH_STATUSES:
        return refuse(f"{args.delivery} is {status}; the light path ends once its Items are claimed")
    recorded = recorded_delivery_path(body)
    light = recorded if recorded is not None and recorded["path"] == "light" else None
    on_light_path = recorded is None or light is not None

    def fall_back(failed: list[dict]) -> str:
        line = standard_path_line("light-path-check", light is not None, failed)
        record_delivery_path_line(root / "delivery.md", props, body, line)
        return line

    if refused is not None:
        # A proposal is on the light path while the policy selects it; an approved scope records its path.
        proposed = (recorded is None and status == "scope_proposed"
                    and policy_delivery_path(docs) == LIGHT_WHEN_ELIGIBLE)
        fallback = fall_back([{"condition": DELIVERY_PATH_SWITCH, "finding": f"{refused} was refused"}]) \
            if light is not None or proposed else None
        result = {"ok": False, "delivery": args.delivery, "status": status, "path": "standard",
                  "recorded": "standard" if fallback is not None or recorded is not None else None}
        if fallback is not None:
            result["fallback"] = fallback
        print(json.dumps(result, indent=2, sort_keys=True)); return 1
    try:
        value = delivery_switch_value(docs, args.delivery, DELIVERY_PATH_SWITCH)
    except ValueError as exc:
        # A light record whose pinned policy no longer holds ends the light path.
        return refuse(str(exc), fall_back([{"condition": DELIVERY_PATH_SWITCH, "finding": str(exc)}])
                      if light is not None else None)
    if value != LIGHT_WHEN_ELIGIBLE:
        return refuse(f"{args.delivery} runs switch {DELIVERY_PATH_SWITCH} at {value}; only"
                      f" {LIGHT_WHEN_ELIGIBLE} plans a Delivery on the light path",
                      fall_back([{"condition": DELIVERY_PATH_SWITCH,
                                  "finding": f"the Process Policy sets it to {value}"}])
                      if light is not None else None)
    with stage_package.candidate_session():
        state = light_path_evaluation(docs, root, light, args.remote)
        fallback = fall_back(state["failed"]) if on_light_path and not state["eligible"] else None
        _approval, plan_findings = execution_approval_refusals(docs, args.delivery, remote=args.remote,
                                                               statuses=PLAN_GATE_STATUSES)
    path = "light" if state["eligible"] and on_light_path else "standard"
    result = {"ok": path == "light" and not plan_findings, "delivery": args.delivery, "status": status,
              "value": value, "path": path,
              "recorded": "standard" if fallback else recorded["path"] if recorded else None,
              "eligible": state["eligible"], "failed": state["failed"], "receipts": state["receipts"],
              "topology_hash": state["topology_hash"], "plan_findings": plan_findings}
    if fallback is not None:
        result["fallback"] = fallback
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


EXECUTION_PLANNING = "execution_planning"
SINGLE_SOURCE_BUNDLE = "single_source_bundle"
# The bundle is reviewed while its execution plan can still be approved.
BUNDLE_STATUSES = ("scope_proposed", "scope_approved", "execution_approved")
# A ruling line of User Decisions starts with its id, after an optional list
# marker or heading; a decision table row keeps it in its first cell.
RULING_LINE_RE = re.compile(r"^(?:[-*+]\s+|[0-9]+[.)]\s+|#{3,6}\s+)?(?:\*\*)?(D-[0-9]+)\b")
RULING_CELL_RE = re.compile(r"^D-[0-9]+$")


def ruling_id_findings(body: str) -> list[str]:
    """Refuse a malformed ruling id, and one id that starts two rulings.

    Every document cites an owner ruling by its id, so an id names exactly one
    ruling. A decision table row of switch owner_gates counts under its id
    against the ruling lines; the table's own check validates its rows.
    """
    lines, rows = [], []
    for line in section_bodies(body).get("User Decisions", "").splitlines():
        text = line.strip()
        if text.startswith("|"):
            cell = text.strip("|").split("|", 1)[0].strip()
            if RULING_CELL_RE.fullmatch(cell):
                rows.append(cell)
        elif match := RULING_LINE_RE.match(text):
            lines.append(match.group(1))
    errors = [f"delivery.md User Decisions ruling {ruling} needs an id of D- and at least two digits"
              for ruling in lines if not USER_DECISION_ID_RE.fullmatch(ruling)]
    for ruling in sorted(set(lines)):
        count = lines.count(ruling) + rows.count(ruling)
        if count > 1:
            errors.append(f"delivery.md User Decisions gives id {ruling} to {count} rulings;"
                          " every ruling keeps its own id")
    return errors


def records_bundle_rulings(docs: Path, props: dict) -> bool:
    """Whether a Delivery records its owner rulings under execution_planning single_source_bundle.

    Rulings are made while the owner's questions are logged, whatever
    owner_gates is. A policy that cannot be read, or a pin that drifted,
    decides nothing here: the source checks report it. A package that
    declares no such switch runs none of its values.
    """
    if props.get("status") not in DECISION_LOG_STATUSES:
        return False
    try:
        value = delivery_switch_value(docs, str(props.get("id", "")), EXECUTION_PLANNING)
    except (KeyError, ValueError):
        return False
    return value == SINGLE_SOURCE_BUNDLE


def _file_record(docs: Path, relative: str) -> dict:
    path = docs / relative
    if not path.is_file():
        raise ValueError(f"bundle input is missing: {relative}")
    return {"path": relative, "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}


def operation_holder(docs: Path, delivery_id: str, remote: str) -> tuple[Path, str | None] | None:
    """Name the checkout and the ref whose Operation contracts a Delivery's publication meets.

    Publication refuses an approved contract that no Item pins while the
    Integration lacks it, so the Integration's remote-tracking ref answers once
    reservation has cut it. Before that the target's does, since reservation
    cuts the Integration from it, and HEAD stands in for a target without one.
    Only local refs are read, as current as the last fetch or push. None
    outside a Git checkout.
    """
    checkout = next((parent for parent in (docs, *docs.parents) if (parent / ".git").exists()), None)
    if checkout is None:
        return None
    from delivery_git import resolve_target_branch, short_refs
    refs = [f"refs/remotes/{remote}/{short_refs(delivery_id)['integration']}"]
    try:
        refs.append(f"refs/remotes/{remote}/{resolve_target_branch(checkout, remote)}")
    except RuntimeError:
        pass
    for ref in (*refs, "HEAD"):
        if not subprocess.run(["git", "-C", str(checkout), "rev-parse", "--verify", "--quiet",
                               ref + "^{commit}"], capture_output=True, check=False).returncode:
            return checkout, ref
    return checkout, None


def operation_held(holder: tuple[Path, str | None], path: Path, text: str) -> bool:
    """Whether the holder's copy of a contract has the local authored text.

    As publication compares them, the generated relation block and line
    endings do not count.
    """
    checkout, ref = holder
    if ref is None:
        return False
    relative = path.resolve().relative_to(checkout.resolve()).as_posix()
    shown = subprocess.run(["git", "--no-replace-objects", "-C", str(checkout), "cat-file", "blob",
                            f"{ref}:{relative}"], capture_output=True, check=False)
    if shown.returncode:
        return False

    def authored(value: str) -> str:
        return without_generated_relations(value.replace("\r\n", "\n")).rstrip()

    try:
        return authored(shown.stdout.decode("utf-8")) == authored(text)
    except UnicodeDecodeError:
        return False


def bundle_manifest(docs: Path, delivery_id: str, remote: str = "origin") -> dict:
    """Return the contract and topology bundle one execution plan is reviewed on.

    Only a Delivery that runs switch execution_planning at single_source_bundle
    has one. It lists every Operation contract the plan revises, pins or has
    to carry, every Item record with its Story and Test Plan and the switch
    value's package data, each with the hash of its bytes, and the Delivery's
    User Decisions section, which owns every owner ruling, with the hash of its
    text. It names the counterpart reader of every revised contract as
    task_inputs.py --role takes it. An unpinned revision is one no open Item
    pins that the Integration, or before reservation the target, does not hold:
    approval does not end it, only the target and refresh-target do. It
    changes nothing.
    """
    root = find_delivery(docs, delivery_id)
    if root is None:
        raise ValueError("Delivery not found")
    value = delivery_switch_value(docs, delivery_id, EXECUTION_PLANNING)
    if value != SINGLE_SOURCE_BUNDLE:
        raise ValueError(f"{delivery_id} runs switch {EXECUTION_PLANNING} at {value}; only"
                         f" {SINGLE_SOURCE_BUNDLE} reviews an execution-plan bundle")
    props, body = split_note(root / "delivery.md")
    if props.get("status") not in BUNDLE_STATUSES:
        raise ValueError(f"{delivery_id} is {props.get('status')}; its bundle is reviewed"
                         " during execution planning")
    delivery = (root / "delivery.md").relative_to(docs).as_posix()
    rulings = section_bodies(body).get("User Decisions")
    if rulings is None:
        raise ValueError(f"bundle input is missing: {delivery} User Decisions")
    decisions = {"path": delivery, "section": "User Decisions",
                 "sha256": "sha256:" + hashlib.sha256(rulings.encode("utf-8")).hexdigest()}
    items, sources = [], []
    for item_path in sorted(root.glob("items/*/item.md")):
        item, _item_body = split_note(item_path)
        items.append({**_file_record(docs, item_path.relative_to(docs).as_posix()),
                      "story": item.get("story_id"), "status": item.get("status"),
                      "runtime_required": item.get("runtime_required") is True})
        sources.extend(_file_record(docs, str(item.get(key, ""))) for key in ("story_path", "test_plan_path"))
    if not items:
        raise ValueError("Delivery must contain at least one Item")
    # A sealed Item keeps the bindings its evidence was produced against.
    open_items = [item for item in items if item["status"] not in TERMINAL_ITEM_STATUSES]
    holder = operation_holder(docs, delivery_id, remote)
    contracts = []
    for kind in ("verification", "environment"):
        path = operation_compile.contract_path(docs, kind)
        if not path.is_file():
            if kind == "verification":
                raise ValueError("bundle input is missing: operation/verification-contract.md")
            continue
        text = path.read_text(encoding="utf-8")
        contract, _contract_body = operation_compile.parse_text(text, path)
        pinned = bool(open_items) and (kind == "verification"
                                       or any(item["runtime_required"] for item in open_items))
        revised = contract.get("status") == "draft"
        # Outside Git only the draft status tells a revision apart.
        held = operation_held(holder, path, text) if holder is not None else not revised
        if pinned or revised or not held:
            writer = operation_compile.WRITER_ROLES[kind]
            counterpart = next(role for role in operation_compile.WRITER_ROLES.values() if role != writer)
            contracts.append({**_file_record(docs, path.relative_to(docs).as_posix()),
                              "kind": kind, "status": contract.get("status"),
                              "revision": contract.get("revision"), "revised": revised,
                              "pinned": pinned, "held": held, "writer": writer.replace("_", "-"),
                              "counterpart": counterpart.replace("_", "-")})
    package = Path(__file__).resolve().parents[1]
    spec = process_policy.load_registry()[EXECUTION_PLANNING]["spec"]
    data = [{"path": relative, "sha256": "sha256:" + hashlib.sha256(
        (package / relative).read_bytes()).hexdigest()}
        for relative in spec.get("value_data", {}).get(SINGLE_SOURCE_BUNDLE, [])]
    files = [record["path"] for record in (*contracts, *items, *sources, decisions)]
    result = {"delivery": delivery_id, "contracts": contracts, "items": items,
              "sources": sources, "user_decisions": decisions, "data": data,
              "held_by": holder[1] if holder is not None else None,
              "readers": [contract["counterpart"] for contract in contracts if contract["revised"]],
              "unpinned_revisions": [contract["path"] for contract in contracts
                                     if not contract["pinned"] and not contract["held"]],
              "inputs": sorted(dict.fromkeys(f"workspace/docs/{path}" for path in files))}
    result["source_hash"] = "sha256:" + hashlib.sha256(json.dumps(
        result, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    return result


def bundle(args) -> int:
    docs = docs_root(args.docs)
    try:
        result = bundle_manifest(docs, args.delivery, getattr(args, "remote", "origin"))
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2)); return 1
    expected = getattr(args, "expected_hash", None)
    if expected is not None and result["source_hash"] != expected:
        print(json.dumps({"ok": False, "errors": [
            "bundle manifest is stale; regenerate it and rerun every affected reader"]}, indent=2))
        return 1
    print(json.dumps({"ok": True, **result}, indent=2, sort_keys=True)); return 0


def status(args) -> int:
    docs = docs_root(args.docs)
    root = find_delivery(docs, args.delivery)
    if root is None: print(json.dumps({"ok": False, "errors": ["Delivery not found"]}, indent=2)); return 1
    props, _ = split_note(root / "delivery.md")
    state, unknown = delivery_state(root, props)
    result = {"ok": unknown is None, "id": props.get("id"), "status": state, "path": str(root),
              "execution_plan": (root / "execution-plan.md").exists(),
              "items": sorted(path.parent.name.upper() for path in root.glob("items/*/item.md"))}
    if unknown is not None:
        result["errors"] = [unknown]
    print(json.dumps(result, indent=2)); return 0 if unknown is None else 1


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
    import delivery_verification
    try:
        delivery_verification.guard_write(docs.parent.parent, [item])
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2)); return 2
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
        if not errors:
            from delivery_verification import validate_evidence
            try:
                validate_evidence(props, split_note(review)[0], split_note(verification)[0])
            except (RuntimeError, ValueError) as exc:
                errors.append(str(exc))
    print(json.dumps({"ok": not errors, "errors": errors}, indent=2)); return 0 if not errors else 1


def item_worktree_head(worktree: Path) -> tuple[str, list[str]]:
    """Return a clean Item worktree HEAD without trusting caller-provided OIDs."""
    try:
        top = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "--show-toplevel"],
            encoding="utf-8", capture_output=True, check=False,
        )
        if top.returncode:
            raise RuntimeError(top.stderr.strip() or "not a Git worktree")
        if Path(top.stdout.strip()).resolve() != worktree.resolve():
            raise RuntimeError("worktree must be the Item worktree root")
        head = subprocess.run(
            ["git", "-C", str(worktree), "rev-parse", "HEAD"],
            encoding="utf-8", capture_output=True, check=False,
        )
        if head.returncode or not GIT_OID_RE.fullmatch(head.stdout.strip()):
            raise RuntimeError(head.stderr.strip() or "Item worktree has no valid HEAD")
        dirty = subprocess.run(
            ["git", "-C", str(worktree), "status", "--porcelain", "--untracked-files=all"],
            encoding="utf-8", capture_output=True, check=False,
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
                                 encoding="utf-8", capture_output=True, check=False)
        if (path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1
                or tracked.returncode or not tracked.stdout.startswith("100644 blob ")
                or path.stat().st_mode & 0o111):
            return ["Item evidence must remain initialized regular non-executable files"]
    return []


def table_cell(value: object) -> str:
    """One Markdown table cell: a single line with its pipes escaped."""
    return " ".join(str(value).split()).replace("|", "\\|")


def table_block(marker: str, columns: tuple[str, ...], rows: list[str]) -> str:
    """A compiler-owned block: its marker line and a table of rendered rows."""
    if not rows:
        return f"{marker} none."
    return "\n".join([marker, "", "| " + " | ".join(columns) + " |",
                      "|" + "---|" * len(columns), *rows])


def block_rows(text: str, marker: str) -> list[str]:
    """The rows of the table that follows ``marker`` in a compiler-owned block.

    The block ends with its own table. A block whose marker line says
    ``none.`` has no table, so a later block's table never becomes its rows.
    """
    if marker not in text:
        return []
    remainder, *lines = text.split(marker, 1)[1].splitlines() or [""]
    if remainder.strip():
        return []
    rows: list[str] = []
    for line in lines:
        if line.strip().startswith("|"):
            rows.append(line.strip())
        elif rows or line.strip():
            break
    return rows[2:]


def replace_section(body: str, title: str, content: str) -> str:
    """Replace the content of one `## title` section and keep every other byte."""
    heading = re.search(rf"(?m)^## {re.escape(title)}[ \t]*$", body)
    if heading is None:
        raise ValueError(f"section {title} is missing")
    following = re.search(r"(?m)^## ", body[heading.end():])
    end = heading.end() + following.start() if following else len(body)
    return body[:heading.end()] + "\n\n" + content.strip() + "\n\n" + body[end:]


def with_compiler_block(body: str, title: str, marker: str, block: str) -> str:
    """Keep a section's authored text and replace the compiler-owned block after it."""
    authored = section_bodies(body).get(title, "").split(marker, 1)[0].strip()
    if authored == SECTION_PLACEHOLDER:
        authored = ""
    return replace_section(body, title, f"{authored}\n\n{block}" if authored else block)


def delivery_follow_ups(root: Path) -> list[str]:
    """The follow-up rows of every integrated Item's code review record."""
    rows: list[str] = []
    for item in sorted(root.glob("items/*/item.md")):
        props, _ = split_note(item)
        review = item.parent / "code-review.md"
        if props.get("status") != "integrated" or not review.is_file():
            continue
        text = section_bodies(split_note(review)[1]).get("Deviations and Follow-ups", "")
        story = table_cell(props.get("story_id", item.parent.name))
        rows.extend(f"| {story} {row}" for row in block_rows(text, ITEM_FOLLOW_UPS))
    return rows


def approve_item_evidence(args) -> int:
    value = getattr(args, "worktree", None)
    if isinstance(value, str) and value.strip():
        worktree = Path(value).resolve()
        root = find_delivery(docs_root(worktree), args.delivery)
        item = root / "items" / id_slug(args.story) / "item.md" if root else None
        if item and item.is_file():
            try:
                props, _ = split_note(item)
                if verification_schedule(props) == "parallel_snapshot_v1":
                    import delivery_verification
                    with delivery_verification.locked(worktree):
                        return _approve_item_evidence(args)
            except (RuntimeError, ValueError) as exc:
                print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2)); return 2
    return _approve_item_evidence(args)


def _approve_item_evidence(args) -> int:
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
    review_result = verification_result = None
    try:
        schedule = verification_schedule(item_props)
        if schedule == "parallel_snapshot_v1":
            import delivery_verification
            session = delivery_verification.validate(worktree, args.delivery, args.story)
            review_result = session["workers"]["code_reviewer"]["result"]
            verification_result = session["workers"]["qa_engineer"]["result"]
            # The owner persists each independent report only after the read barrier.
            review_body = body_for("item", review_props["title"], {
                "Implementation Evidence": review_result["report"],
                "Navigation": link(item.relative_to(docs).as_posix(), item_props["title"]),
            })
            verification_body = body_for("item", verification_props["title"], {
                "Implementation Evidence": verification_result["report"],
                "Definition of Done Evidence": json.dumps(verification_result["checks"], sort_keys=True, ensure_ascii=False),
                "Navigation": link(item.relative_to(docs).as_posix(), item_props["title"]),
            })
            if set(SECTIONS["item"]).issubset(sections(review_result["report"])):
                review_body = review_result["report"]
            if set(SECTIONS["item"]).issubset(sections(verification_result["report"])):
                verification_body = verification_result["report"]
            if delivery_switch_value(docs, args.delivery, REVIEW_LOOP) == "blocking_delta":
                # The Item record keeps its approved bytes; its code review record
                # carries the follow-ups, since evidence approval writes only the reports.
                rows = ["| " + " | ".join(table_cell({**finding, "finding": finding["id"]}[column])
                                          for column in FOLLOW_UP_COLUMNS) + " |"
                        for finding in delivery_verification.open_follow_ups(review_result, item_props)]
                block = table_block(ITEM_FOLLOW_UPS, FOLLOW_UP_COLUMNS, rows)
                calibration = sorted(review_result.get("calibration", []), key=lambda row: row["finding"])
                if calibration:
                    block += "\n\n" + table_block(ITEM_CALIBRATION, CALIBRATION_COLUMNS, [
                        "| " + " | ".join(table_cell(row[column]) for column in CALIBRATION_COLUMNS) + " |"
                        for row in calibration])
                review_body = with_compiler_block(review_body, "Deviations and Follow-ups",
                                                  ITEM_FOLLOW_UPS, block)
            for target, result in ((review_props, review_result), (verification_props, verification_result)):
                target["verification_candidate_hash"] = session["candidate"]["candidate_hash"]
                target["verification_mode"] = result["mode"]
                target["verification_result_hash"] = result["result_hash"]
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2)); return 2
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
    delivery_props, delivery_body = split_note(delivery_path_value)
    try:
        review_loop = delivery_switch_value(docs, args.delivery, REVIEW_LOOP)
    except ValueError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2)); return 1
    # Gate B presents the decision log and asks every queued question before this write.
    if keeps_decision_log(docs, delivery_props, delivery_body):
        errors = user_decision_findings(delivery_body, [
            str(split_note(item)[0].get("story_id", "")) for item in sorted(root.glob("items/*/item.md"))])
        pending = pending_decisions(delivery_body)
        if pending:
            errors.append(f"{pending_rows_text(pending)}; gate B asks every queued question, so record the"
                          " owner's answers before approve-review")
        if errors:
            print(json.dumps({"ok": False, "errors": errors}, indent=2)); return 1
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
    if review_loop == "blocking_delta":
        review_body = with_compiler_block(
            review_body, "Lessons and Follow-up", DELIVERY_FOLLOW_UPS,
            table_block(DELIVERY_FOLLOW_UPS, ("item", *FOLLOW_UP_COLUMNS), delivery_follow_ups(root)))
    review_props["approval_hash"] = content_hash(review_props, review_body, exclude=MUTABLE | {"approval_hash"})
    review_props["source_hash"] = content_hash(review_props, review_body)
    atomic_text(review_path, frontmatter(review_props, review_body))
    delivery_props["status"] = "review"
    delivery_props["tags"] = [tag for tag in delivery_props.get("tags", []) if not str(tag).startswith("status/")] + ["status/review"]
    delivery_props["source_hash"] = content_hash(delivery_props, split_note(delivery_path_value)[1])
    atomic_text(delivery_path_value, frontmatter(delivery_props, split_note(delivery_path_value)[1]))
    print(json.dumps({"ok": True, "review": str(review_path), "approval_hash": review_props["approval_hash"]}, indent=2)); return 0


def record_pr_url(docs: Path, delivery_id: str, url: str) -> None:
    """Mirror a PR URL in the local Delivery Review and move a reviewed Delivery to awaiting_merge.

    The PR record on the Integration is the only source of the URL. A Delivery
    cancelled before it had a local Review has nothing to mirror, so its
    package stays unchanged. It prints nothing, so open-pr can record the PR
    and still print only its own result.
    """
    root = find_delivery(docs, delivery_id)
    if root is None:
        raise RuntimeError("Delivery Review not found")
    review = root / "delivery-review.md"
    if not review.exists():
        return
    props, body = split_note(review)
    props["pull_request_url"] = url
    props["source_hash"] = content_hash(props, body, exclude=MUTABLE - {"pull_request_url"})
    atomic_text(review, frontmatter(props, body))
    delivery_path_value = root / "delivery.md"
    delivery_props, delivery_body = split_note(delivery_path_value)
    recorded = pr_recorded_props(delivery_props, delivery_body)
    if recorded is not None:
        atomic_text(delivery_path_value, frontmatter(recorded, delivery_body))


def record_pr(args) -> int:
    try:
        record_pr_url(docs_root(args.docs), args.delivery, args.url)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2)); return 1
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
    plan_check = sub.add_parser("check-plan")
    plan_check.add_argument("--delivery", required=True)
    plan_check.add_argument("--reopen", action="append", default=[], metavar="STORY",
                            help="an integrated Item the approval will name for reopen")
    plan_check.add_argument("--remote", default="origin",
                            help="the Git remote whose local remote-tracking refs hold the target and"
                                 " Integration branches")
    plan_check.set_defaults(func=check_plan)
    light = sub.add_parser("light-path-check")
    light.add_argument("--delivery", required=True)
    light.add_argument("--remote", default="origin",
                       help="the Git remote whose local remote-tracking refs hold the target branch")
    light.add_argument("--refused", choices=LIGHT_SEQUENCE,
                       help="record that this step of the light sequence was refused, which ends the light path")
    light.set_defaults(func=light_path_check)
    bundle_cmd = sub.add_parser("bundle-manifest")
    bundle_cmd.add_argument("--delivery", required=True); bundle_cmd.add_argument("--expected-hash")
    bundle_cmd.add_argument("--remote", default="origin",
                            help="the Git remote whose local remote-tracking refs hold the target and"
                                 " Integration branches")
    bundle_cmd.set_defaults(func=bundle)
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
