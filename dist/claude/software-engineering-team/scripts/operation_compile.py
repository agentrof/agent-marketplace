#!/usr/bin/env python3
"""Lifecycle compiler for the project Operation contracts.

Operation truth lives in ``workspace/docs/operation``. Verification and
environment contracts are independent, approved revisioned documents. They
are deliberately outside product-stage package hashes: a command change is a
delivery concern, although its cited accepted Solution decision is rechecked
at every approval and consumption boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import atomic_file
from ba_compile import (
    frontmatter_item, frontmatter_scalar, parse_frontmatter, restamp_frontmatter, without_generated_relations,
)
import stage_package


KINDS = {"verification", "environment"}
TYPE_FOR = {
    "verification": "verification-contract",
    "environment": "environment-contract",
}
FILE_FOR = {
    "verification": "verification-contract.md",
    "environment": "environment-contract.md",
}
COMMAND_FIELDS = {
    "verification": (
        "test_command", "mutation_command", "dependency_audit_command", "diagnostic_test_command",
        "test_partition_command", "live_test_command", "live_diagnostic_command", "live_resume_command",
    ),
    "environment": ("env_command",),
}
WORKDIR_FIELDS = {
    "verification": (
        "test_workdir", "mutation_workdir", "dependency_audit_workdir",
    ),
    "environment": ("env_workdir",),
}
WRITER_ROLES = {"verification": "qa_engineer", "environment": "devops_engineer"}
# An accepted minor review finding is followed up by one of the contract writers.
MINOR_FINDING_OWNER_ROLES = tuple(WRITER_ROLES.values())
ACCEPTED_MINOR_FINDINGS = "Accepted Minor Findings"
# The sections of the review record that switch review_loop keeps at
# blocking_delta; backlog_compile validates them as it does a review note's.
REVIEW_RECORD_SECTIONS = ("Returned Findings", "Severity Calibration", ACCEPTED_MINOR_FINDINGS)
DISPOSITIONS = {"required", "not_applicable"}
# Where the checks that merge-pr requires on a Delivery PR come from. The first
# is the default, so a contract approved before the field existed keeps it.
PULL_REQUEST_CHECK_SOURCES = ("repository_workflow", "external")
TOKEN_RE = re.compile(r"(?:\{\{[^{}]+\}\}|\$\{[^{}]+\})")
# The optional command_variables of a Verification Contract name the environment
# variables its commands read, which every run identity then covers.
VARIABLE_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# The runner sets the variables of this namespace, and run evidence binds them
# without a declaration: a selection file the runner writes by its content.
RUNNER_VARIABLE_PREFIX = "AGENTROF_"
CREDENTIAL_RE = re.compile(
    r"(?i)(?:api[_-]?key|token|password|secret)\s*=\s*[^\s]+"
)


def docs_root(value: str | None) -> Path:
    root = Path(value or "workspace/docs").resolve()
    if root.name == "docs":
        return root
    if (root / "workspace" / "docs").is_dir():
        return root / "workspace" / "docs"
    if (root / "docs").is_dir():
        return root / "docs"
    return root


def contract_path(docs: Path, kind: str) -> Path:
    return docs / "operation" / FILE_FOR[kind]


def parse_text(text: str, path: Path) -> tuple[dict, str]:
    props, body_line, error = parse_frontmatter(text)
    if error:
        raise ValueError(f"{path}: {error}")
    return props, "\n".join(text.splitlines()[body_line - 1:]).strip()


def parse(path: Path) -> tuple[dict, str]:
    return parse_text(path.read_text(encoding="utf-8"), path)


def render(props: dict, body: str) -> str:
    lines = ["---"]
    for key, value in props.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {frontmatter_item(item)}" for item in value)
        else:
            lines.append(f"{key}: {frontmatter_scalar(value)}")
    lines.extend(["---", "", body.strip(), ""])
    return "\n".join(lines)


def _source_hash(props: dict, body: str) -> str:
    excluded = {"source_hash", "approved_at_utc"}
    view = {key: value for key, value in props.items() if key not in excluded}
    return "sha256:" + hashlib.sha256(
        json.dumps({"frontmatter": view, "body": body}, ensure_ascii=False,
                   sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def source_hash(props: dict, body: str) -> str:
    return _source_hash(props, without_generated_relations(body).rstrip())


def receipt_hash(props: dict, body: str) -> str:
    """Retain only a recomputed approved receipt from the two historical endings."""
    authored = without_generated_relations(body).rstrip()
    canonical = _source_hash(props, authored)
    # Before canonicalization, removing an inverse block added one terminal
    # newline, while approvals without a block hashed the stripped body.
    if props.get("status") == "approved":
        legacy = _source_hash(props, authored + "\n")
        if props.get("source_hash") == legacy:
            return legacy
    return canonical


def valid_workdir(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or "\\" in value:
        return False
    if value == ".":
        return True
    path = PurePosixPath(value)
    return not path.is_absolute() and path.as_posix() == value and all(
        item not in {"", ".", ".."} for item in path.parts
    )


def command_variable_problem(names: object) -> str | None:
    """Why a Verification Contract's command_variables declare no valid list, or None.

    The contract check and the Delivery runner both apply it. Windows matches
    variable names without case, so the runner's namespace is matched so too.
    """
    if (not isinstance(names, list)
            or any(not isinstance(name, str) or not VARIABLE_NAME_RE.fullmatch(name) for name in names)
            or len(set(names)) != len(names)):
        return "command_variables must list unique environment variable names"
    reserved = [name for name in names if name.upper().startswith(RUNNER_VARIABLE_PREFIX)]
    if reserved:
        noun = "a variable" if len(reserved) == 1 else "variables"
        return (f"command_variables must not name {', '.join(reserved)}, {noun} of the runner's own"
                f" {RUNNER_VARIABLE_PREFIX} namespace, which run evidence binds without a declaration")
    return None


def literal_id(value: object) -> bool:
    """Whether a declared id passes as data: nonempty, unpadded, no option prefix or control character."""
    return (isinstance(value, str) and bool(value) and value == value.strip() and not value.startswith("-")
            and not any(ord(character) < 32 or ord(character) == 127 for character in value))


def unique_literal_ids(values: object) -> bool:
    return (isinstance(values, list) and bool(values) and all(literal_id(value) for value in values)
            and len(set(values)) == len(values))


def scratch_relative_path(value: object) -> bool:
    """Whether a path is a normalized relative file path that stays inside the directory it is joined to."""
    return (valid_workdir(value) and value != "." and ":" not in value
            and all(part.rstrip(". ") == part for part in PurePosixPath(value).parts))


def test_group_problems(props: dict) -> list[str]:
    """Why a Verification Contract's optional test group report declaration is invalid.

    test_groups names the groups the test command runs and test_group_report
    the file it writes under AGENTROF_VERIFICATION_SCRATCH with one status per
    group; a contract declares both or neither.
    """
    declared = [name for name in ("test_groups", "test_group_report") if name in props]
    if not declared:
        return []
    errors = []
    if len(declared) == 1:
        errors.append("test_groups and test_group_report are declared together or not at all")
    if "test_groups" in props and not unique_literal_ids(props["test_groups"]):
        errors.append("test_groups must list unique literal group ids")
    if "test_group_report" in props and not scratch_relative_path(props["test_group_report"]):
        errors.append("test_group_report must be a normalized relative path under the verification scratch")
    return errors


# A Verification Contract may declare opt-in live test groups that QA runs one
# at a time: a command naming the group through one {group} placeholder, its
# workdir and the ordered group names, all or none. Group names enter the
# command, so they are identifier tokens.
LIVE_TEST_FIELDS = ("live_test_command", "live_test_workdir", "live_groups")
LIVE_GROUP_PLACEHOLDER = "{group}"
LIVE_GROUP_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
# How a pass runs the selected live groups: one run per group, the default, or
# one combined run whose {group} receives the comma-separated names, which the
# command must accept.
LIVE_GROUP_RUN = "live_group_run"
LIVE_GROUP_RUNS = ("per_group", "combined")
# At live_case_reuse delta_since_green the impact map that proves a live group
# unchanged since its base run: "<group>=<glob>" entries, where group "*" names
# catch-all paths that change every group. Globs match as fnmatch, "*" crossing "/".
LIVE_GROUP_PATHS = "live_group_paths"
LIVE_ALL_GROUPS = "*"


def live_impact_map(props: dict) -> dict[str, list[str]]:
    """The declared live_group_paths as group -> sorted globs, the catch-all under "*"."""
    impact: dict[str, list[str]] = {}
    for entry in props.get(LIVE_GROUP_PATHS) or []:
        group, _, glob = str(entry).partition("=")
        impact.setdefault(group, []).append(glob)
    return {group: sorted(globs) for group, globs in sorted(impact.items())}


# A Verification Contract may opt in to a host guard for long verification
# commands: an idle-sleep inhibitor and host sleep detection, and a host:port
# a live run probes for network loss.
HOST_KEEP_AWAKE = "host_keep_awake"
INHIBIT_SLEEP = "inhibit_sleep"
KEEP_AWAKE_VALUES = ("off", INHIBIT_SLEEP)
REACHABILITY_PROBE = "reachability_probe"
PROBE_TARGET_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252}[A-Za-z0-9])?:([1-9][0-9]{0,4})")


def host_guard_problems(props: dict) -> list[str]:
    """Why a Verification Contract's optional host guard is invalid."""
    errors = []
    if HOST_KEEP_AWAKE in props and props[HOST_KEEP_AWAKE] not in KEEP_AWAKE_VALUES:
        errors.append(f"{HOST_KEEP_AWAKE} must be one of {', '.join(KEEP_AWAKE_VALUES)}")
    if REACHABILITY_PROBE in props:
        match = PROBE_TARGET_RE.fullmatch(str(props[REACHABILITY_PROBE]))
        if not isinstance(props[REACHABILITY_PROBE], str) or not match or int(match.group(1)) > 65535:
            errors.append(f"{REACHABILITY_PROBE} must be one host:port, a DNS name or address and a port")
    return errors


def live_test_problems(props: dict) -> list[str]:
    """Why a Verification Contract's optional live test group declaration is invalid."""
    if not any(name in props for name in LIVE_TEST_FIELDS):
        return ([f"{LIVE_GROUP_RUN} needs live_test_command, live_test_workdir and live_groups"]
                if LIVE_GROUP_RUN in props else []) + (["live_diagnostic_command needs live test groups"]
                if "live_diagnostic_command" in props else []) + (["live_resume_command needs live test groups"]
                if "live_resume_command" in props else []) + ([f"{LIVE_GROUP_PATHS} needs live test groups"]
                if LIVE_GROUP_PATHS in props else [])
    errors = []
    if not all(name in props for name in LIVE_TEST_FIELDS):
        errors.append("live_test_command, live_test_workdir and live_groups are declared together or not at all")
    command = props.get("live_test_command")
    if "live_test_command" in props and (not isinstance(command, str) or not command.strip()
                                         or command.count(LIVE_GROUP_PLACEHOLDER) != 1):
        errors.append("live_test_command must be a non-empty command containing {group} exactly once")
    workdir = props.get("live_test_workdir")
    if "live_test_workdir" in props and workdir != "." and not scratch_relative_path(workdir):
        errors.append("live_test_workdir must be a normalized repository-relative path")
    groups = props.get("live_groups")
    if "live_groups" in props and (
            not isinstance(groups, list) or not groups or len(set(map(str, groups))) != len(groups)
            or any(not isinstance(group, str) or not LIVE_GROUP_RE.fullmatch(group) for group in groups)):
        errors.append("live_groups must list unique group names of letters, digits, '.', '_' and '-'")
    if LIVE_GROUP_RUN in props and props[LIVE_GROUP_RUN] not in LIVE_GROUP_RUNS:
        errors.append(f"{LIVE_GROUP_RUN} must be one of {', '.join(LIVE_GROUP_RUNS)}")
    if "live_diagnostic_command" in props:
        command = props["live_diagnostic_command"]
        if not isinstance(command, str) or not command.strip() or command.count(LIVE_GROUP_PLACEHOLDER) != 1:
            errors.append("live_diagnostic_command must contain {group} exactly once")
    if "live_resume_command" in props:
        resume = props["live_resume_command"]
        if not isinstance(resume, str) or not resume.strip() or resume.count(LIVE_GROUP_PLACEHOLDER) != 1:
            errors.append("live_resume_command must contain {group} exactly once")
    if LIVE_GROUP_PATHS in props:
        entries = props[LIVE_GROUP_PATHS]
        known = set(groups) if isinstance(groups, list) else set()
        if (not isinstance(entries, list) or not entries or len(set(map(str, entries))) != len(entries)
                or any(not isinstance(entry, str) or entry.partition("=")[0] not in known | {LIVE_ALL_GROUPS}
                       or not entry.partition("=")[2].strip() for entry in entries)):
            errors.append(f"{LIVE_GROUP_PATHS} must list unique '<group>=<glob>' entries naming a declared live"
                          " group or '*'")
    return errors


# A Verification Contract may split its suite into partitions that run in
# parallel on isolated test engines: a partition command, the engines and a
# Test Partitions table, all or none, and the profiles whose partitions may
# share an engine. Partition, engine and profile ids name files and engines.
TEST_PARTITIONS = "Test Partitions"
TEST_PARTITION_COLUMNS = ("partition", "groups", "profile")
PARTITION_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
VERIFICATION_REHEARSALS = "Verification Rehearsals"
OWNER_STEPS = "Owner Steps"


def owner_steps(body: str) -> tuple[list[dict], list[str]]:
    """Ordered owner handoffs declared by an approved Verification Contract."""
    import backlog_compile

    if OWNER_STEPS not in backlog_compile.headings(body):
        return [], []
    rows, errors = backlog_compile.structured_table(
        backlog_compile.section(body, OWNER_STEPS),
        ("step", "command", "expected_output", "resume_at", "timeout_seconds"),
        "verification contract", OWNER_STEPS)
    plan, seen = [], set()
    for raw in rows:
        row = {key: value.strip().strip("`") for key, value in raw.items()}
        step = row["step"]
        if not PARTITION_ID_RE.fullmatch(step) or step in seen:
            errors.append("Owner Steps requires unique literal step ids")
        seen.add(step)
        if not row["command"] or TOKEN_RE.search(row["command"]) or CREDENTIAL_RE.search(row["command"]):
            errors.append("Owner Steps command must be non-empty, without credentials or unresolved tokens")
        if not row["expected_output"] or row["resume_at"] in {"", "-"}:
            errors.append("Owner Steps requires expected output and a resume instruction")
        try:
            timeout = int(row["timeout_seconds"]) if row["timeout_seconds"].isdigit() else 0
            if timeout < 1 or timeout > sys.float_info.max:
                raise ValueError("invalid timeout")
            row["timeout_seconds"] = timeout
        except ValueError:
            errors.append("Owner Steps timeout seconds must be a finite positive integer")
        plan.append({**row, "runner": "owner"})
    if not plan:
        errors.append("Owner Steps must declare at least one step")
    return plan, errors


def verification_rehearsals(props: dict, body: str) -> tuple[list[dict], list[str]]:
    """Approved short live targets to rehearse before a full verification pass."""
    import backlog_compile

    if VERIFICATION_REHEARSALS not in backlog_compile.headings(body):
        return [], []
    rows, errors = backlog_compile.structured_table(
        backlog_compile.section(body, VERIFICATION_REHEARSALS),
        ("step", "phase", "group", "target", "waiver"), "verification contract", VERIFICATION_REHEARSALS)
    if not props.get("live_diagnostic_command"):
        errors.append("Verification Rehearsals requires live_diagnostic_command")
    seen, targets, plan = set(), set(), []
    for row in rows:
        row = {key: value.strip().strip("`") for key, value in row.items()}
        step, phase, group, target = (row[key] for key in ("step", "phase", "group", "target"))
        if not PARTITION_ID_RE.fullmatch(step) or step in seen:
            errors.append("Verification Rehearsals requires unique step ids")
        if phase not in {"lifecycle", "live"}:
            errors.append("Verification Rehearsals phase must be lifecycle or live")
        if not isinstance(props.get("live_groups"), list) or group not in props["live_groups"]:
            errors.append("Verification Rehearsals group must be a declared live group")
        if not literal_id(target) or (group, target) in targets:
            errors.append("Verification Rehearsals requires unique literal test targets per group")
        if row["waiver"] != "-" and not backlog_compile.meaningful_text(row["waiver"]):
            errors.append("Verification Rehearsals waiver must be '-' or an explicit owner reason")
        seen.add(step)
        targets.add((group, target))
        plan.append(row)
    if not plan:
        errors.append("Verification Rehearsals must declare at least one step")
    return sorted(plan, key=lambda row: row["phase"] != "lifecycle"), errors


def partition_ids(values: object) -> bool:
    return (isinstance(values, list) and bool(values)
            and all(isinstance(value, str) and PARTITION_ID_RE.fullmatch(value) for value in values)
            and len(set(values)) == len(values))


def test_partition_plan(props: dict, body: str) -> tuple[list[dict] | None, list[str]]:
    """The Verification Contract's test partition plan, or None when it declares none, with its problems.

    Each partition runs the groups of test_groups its row names, every group
    in exactly one partition, on an engine its profile allows.
    """
    import backlog_compile

    authored = without_generated_relations(body)
    table = TEST_PARTITIONS in backlog_compile.headings(authored)
    declared = {"test_partition_command": "test_partition_command" in props,
                "test_engines": "test_engines" in props, TEST_PARTITIONS: table}
    if not any(declared.values()):
        return None, (["shared_profiles requires test_partition_command, test_engines and a Test Partitions"
                       " table"] if "shared_profiles" in props else [])
    if not all(declared.values()):
        return None, [f"test_partition_command, test_engines and a {TEST_PARTITIONS} table are declared together"
                      " or not at all"]
    errors = []
    command = props["test_partition_command"]
    if not isinstance(command, str) or not command.strip():
        errors.append("test_partition_command must be a non-empty approved command")
    if not partition_ids(props["test_engines"]):
        errors.append("test_engines must list unique engine ids of letters, digits, '.', '_' and '-'")
    groups = props.get("test_groups")
    if not unique_literal_ids(groups):
        errors.append(f"a {TEST_PARTITIONS} table needs test_groups, the groups it partitions")
        groups = []
    rows, table_errors = backlog_compile.structured_table(
        backlog_compile.section(authored, TEST_PARTITIONS), TEST_PARTITION_COLUMNS,
        "verification contract", TEST_PARTITIONS)
    errors.extend(table_errors)
    plan, placed = [], {}
    for number, row in enumerate(rows, 1):
        partition = row["partition"].strip("`")
        members = [group.strip().strip("`") for group in row["groups"].split(",")]
        profile = row["profile"].strip("`")
        label = f"{TEST_PARTITIONS} row {number}"
        if not PARTITION_ID_RE.fullmatch(partition) or not PARTITION_ID_RE.fullmatch(profile):
            errors.append(f"{label} needs a partition id and a profile id of letters, digits, '.', '_' and '-'")
        if not all(members) or len(set(members)) != len(members):
            errors.append(f"{label} must name each of its groups once")
        for group in members:
            if group and group not in groups:
                errors.append(f"{label} names {group}, which test_groups does not declare")
            if group in placed:
                errors.append(f"group {group} is in partitions {placed[group]} and {partition}")
            placed.setdefault(group, partition)
        plan.append({"partition": partition, "groups": members, "profile": profile})
    ids = [entry["partition"] for entry in plan]
    if not plan:
        errors.append(f"the {TEST_PARTITIONS} table must hold at least one partition")
    if len(set(ids)) != len(ids):
        errors.append(f"the {TEST_PARTITIONS} table names a partition more than once")
    for group in groups:
        if group not in placed:
            errors.append(f"group {group} is in no partition")
    shared = props.get("shared_profiles", [])
    if "shared_profiles" in props and (not partition_ids(shared) or not set(shared) <= {
            entry["profile"] for entry in plan}):
        errors.append("shared_profiles must list unique profiles of the Test Partitions table")
    return plan, errors


def pull_request_checks(props: dict) -> tuple[object, object]:
    """The declared source of Delivery PR checks and the provider that reports them."""
    return (props.get("pull_request_check_source", PULL_REQUEST_CHECK_SOURCES[0]),
            props.get("pull_request_check_provider", ""))


def accepted_solution_ref(docs: Path, value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    ref = value.removeprefix("[[").split("|", 1)[0].removesuffix("]] ").removesuffix("]]")
    ref = ref.removesuffix(".md")
    if not ref.startswith("solution-design/decisions/"):
        return False
    candidate = docs / (ref + ".md")
    if not candidate.is_file():
        return False
    try:
        props, _body = parse(candidate)
    except (OSError, ValueError):
        return False
    _receipt, package_errors = stage_package.verify(
        docs, "solution-design", "solution-design/landscape"
    )
    return props.get("status") == "accepted" and not package_errors


def review_record_findings(docs: Path, kind: str, props: dict, body: str) -> list[str]:
    """Validate the review record that the blocking_delta or single_pass loop keeps in a contract.

    At any other review_loop value a section of a record's name is authored
    text, as it was before the switch. A contract without such a section never
    reads the Process Policy, nor does an approved contract without Returned
    Findings, which was approved before its review kept a record and stays as
    it was.
    """
    authored = without_generated_relations(body)
    if not any(re.search(rf"(?m)^##\s+{title}\s*$", authored) for title in REVIEW_RECORD_SECTIONS):
        return []
    import backlog_compile

    approved = props.get("status") == "approved"
    if approved and backlog_compile.RETURNED_FINDINGS not in backlog_compile.headings(authored):
        return []
    try:
        loop = backlog_compile.review_loop_value(docs)
    except ValueError as exc:
        return [f"the review record needs the review_loop value of the Process Policy: {exc}"]
    if loop not in backlog_compile.RECORDING_LOOPS:
        return []
    path = f"operation/{FILE_FOR[kind]}"
    # The backlog review note records accepted minor findings in the same table.
    errors = backlog_compile.accepted_minor_findings(
        docs, authored, path, {"minor_finding_owner_roles": MINOR_FINDING_OWNER_ROLES})
    return errors + backlog_compile.review_record_findings(docs, authored, path, approved=approved,
                                                           loop=loop)


def check_contract(docs: Path, kind: str, text: str | None = None) -> tuple[dict, list[str]]:
    """Check the contract file, or ``text`` as its content before it is written."""
    path = contract_path(docs, kind)
    if text is None and not path.is_file():
        return {}, [f"missing {kind} contract: {path}"]
    try:
        props, body = parse(path) if text is None else parse_text(text, path)
    except (OSError, ValueError) as exc:
        return {}, [str(exc)]
    errors: list[str] = []
    if "live_environment_plan" in props and (kind != "verification" or props["live_environment_plan"] != "effect_ordered_v1"):
        errors.append("live_environment_plan must be effect_ordered_v1 on a Verification Contract")
    if props.get("type") != TYPE_FOR[kind]:
        errors.append(f"type must be {TYPE_FOR[kind]}")
    if props.get("status") not in {"draft", "approved"}:
        errors.append("status must be draft or approved")
    if not isinstance(props.get("revision"), int) or props["revision"] < 1:
        errors.append("revision must be a positive integer")
    refs = props.get("constrained_by")
    if refs is not None and (not isinstance(refs, list)
                             or any(not accepted_solution_ref(docs, ref) for ref in refs)):
        errors.append("constrained_by contains a missing or non-accepted Solution decision")
    for field in COMMAND_FIELDS[kind]:
        value = props.get(field, "")
        if value and (not isinstance(value, str) or TOKEN_RE.search(value)
                      or CREDENTIAL_RE.search(value)):
            errors.append(f"{field} is empty, contains a credential literal, or has an unresolved token")
    for field in WORKDIR_FIELDS[kind]:
        if not valid_workdir(props.get(field, "")):
            errors.append(f"{field} must be a normalized repository-relative path")
    if kind == "verification":
        if "diagnostic_test_command" in props:
            diagnostic = props["diagnostic_test_command"]
            if not isinstance(diagnostic, str) or not diagnostic.strip():
                errors.append("diagnostic_test_command must be a non-empty approved adapter command when declared")
            # The optional workdir defaults only at execution; do not insert it
            # into an existing contract or change an older approved receipt.
            directory = props.get("diagnostic_test_workdir", ".")
            if (not valid_workdir(directory) or ":" in directory
                    or any(part.rstrip(". ") != part for part in PurePosixPath(directory).parts if part != ".")):
                errors.append("diagnostic_test_workdir must be a normalized repository-relative path")
        elif "diagnostic_test_workdir" in props:
            errors.append("diagnostic_test_workdir requires diagnostic_test_command")
        if props.get("status") == "approved" and (not isinstance(refs, list) or not refs):
            errors.append("approved contract must cite at least one accepted Solution decision in constrained_by")
        if props.get("status") == "approved" and (not isinstance(props.get("test_command"), str) or not props["test_command"].strip()):
            errors.append("test_command is required")
        for prefix in ("mutation", "dependency_audit"):
            disposition = props.get(f"{prefix}_disposition")
            if disposition not in DISPOSITIONS:
                errors.append(f"{prefix}_disposition must be required or not_applicable")
            command = props.get(f"{prefix}_command", "")
            rationale = props.get(f"{prefix}_rationale", "")
            if disposition == "required" and (not isinstance(command, str) or not command.strip()):
                errors.append(f"{prefix}_command is required when disposition is required")
            if disposition == "not_applicable" and (not isinstance(rationale, str) or not rationale.strip()):
                errors.append(f"{prefix}_rationale is required when disposition is not_applicable")
        include = props.get("mutation_include_paths", [])
        if (not isinstance(include, list) or any(not isinstance(path, str) or path == "." or not valid_workdir(path)
                                              for path in include)):
            errors.append("mutation_include_paths must be normalized repository-relative paths")
        problem = command_variable_problem(props.get("command_variables", []))
        if problem:
            errors.append(problem)
        errors.extend(test_group_problems(props))
        errors.extend(live_test_problems(props))
        errors.extend(host_guard_problems(props))
        errors.extend(verification_rehearsals(props, body)[1])
        errors.extend(owner_steps(body)[1])
        errors.extend(test_partition_plan(props, body)[1])
        source, provider = pull_request_checks(props)
        if source not in PULL_REQUEST_CHECK_SOURCES:
            errors.append("pull_request_check_source must be repository_workflow or external")
        elif source == "external":
            if (not isinstance(provider, str) or not provider.strip()
                    or TOKEN_RE.search(provider) or CREDENTIAL_RE.search(provider)):
                errors.append("pull_request_check_provider must name the external source of pull request "
                              "checks, without a credential literal or an unresolved token")
        elif provider:
            errors.append("pull_request_check_provider is declared only with pull_request_check_source external")
    else:
        if props.get("status") == "approved" and (not isinstance(refs, list) or not refs):
            errors.append("approved contract must cite at least one accepted Solution decision in constrained_by")
        if props.get("status") == "approved" and (not isinstance(props.get("env_command"), str) or not props["env_command"].strip()):
            errors.append("env_command is required")
        scenarios = props.get("scenarios")
        if props.get("status") == "approved" and (not isinstance(scenarios, list) or not scenarios):
            errors.append("scenarios must be a non-empty list")
        for name in ("tolerated_warnings", "service_catalog"):
            if not isinstance(props.get(name), list):
                errors.append(f"{name} must be a list")
        if "test_engines" in props and not partition_ids(props["test_engines"]):
            errors.append("test_engines must list unique engine ids of letters, digits, '.', '_' and '-'")
    errors.extend(review_record_findings(docs, kind, props, body))
    digest = receipt_hash(props, body)
    if props.get("status") == "approved":
        if props.get("source_hash") != digest:
            errors.append("approved contract source_hash is stale")
        if not isinstance(props.get("approved_at_utc"), str):
            errors.append("approved contract needs compiler-owned approved_at_utc")
    value = {"path": str(path), "kind": kind, "status": props.get("status"),
             "revision": props.get("revision"), "source_hash": digest,
             "current": not errors and props.get("status") == "approved"}
    if kind == "verification" and props.get("status") == "approved":
        environment = paired_environment(docs)
        if environment is not None or any(field in props for field in PAIRED_FIELDS):
            pairing = {
                "revision": props.get("paired_environment_revision"),
                "source_hash": props.get("paired_environment_source_hash"),
                "current": environment is not None
                and environment == (props.get("paired_environment_revision"), props.get("paired_environment_source_hash")),
            }
            if not pairing["current"]:
                parts = [sys.executable, str(Path(__file__).resolve()), "re-pair", "--docs", str(docs)]
                pairing["repair_command"] = ("& " + " ".join("'" + part.replace("'", "''") + "'" for part in parts) if os.name == "nt"
                                             else shlex.join(parts))
            value["paired_environment"] = pairing
    return value, errors


PAIRED_FIELDS = ("paired_environment_revision", "paired_environment_source_hash")


def paired_environment(docs: Path) -> tuple[int, str] | None:
    """The revision and approved source_hash of the current Environment Contract, if any."""
    if not contract_path(docs, "environment").is_file():
        return None
    environment, errors = check_contract(docs, "environment")
    if errors or not environment.get("current"):
        return None
    return environment["revision"], environment["source_hash"]


def pairing_findings(docs: Path, receipt: dict | None = None) -> list[str]:
    """Refuse stale pairing at execution boundaries, without reapproving authored text."""
    if receipt is None:
        receipt, _errors = check_contract(docs, "verification")
    pairing = receipt.get("paired_environment")
    if pairing is None or pairing["current"]:
        return []
    return ["Verification Contract is not paired with the current approved Environment Contract; "
            "run: " + pairing["repair_command"]]


def repair_pairing(args) -> int:
    docs = docs_root(args.docs)
    path = contract_path(docs, "verification")
    environment_path = contract_path(docs, "environment")
    original = path.read_bytes()
    environment_original = environment_path.read_bytes()
    text = original.decode("utf-8")
    receipt, errors = check_contract(docs, "verification", text)
    environment, environment_errors = check_contract(docs, "environment", environment_original.decode("utf-8"))
    if errors or not receipt.get("current"):
        raise ValueError("re-pair requires an unchanged approved Verification Contract: " + "; ".join(errors))
    if environment_errors or not environment.get("current"):
        raise ValueError("re-pair requires an approved current Environment Contract: " + "; ".join(environment_errors))
    if not pairing_findings(docs, receipt):
        print(json.dumps({**receipt, "repaired": False}, sort_keys=True))
        return 0
    props, body = parse_text(text, path)
    stamps = dict(zip(PAIRED_FIELDS, (environment["revision"], environment["source_hash"])))
    stamps["source_hash"] = source_hash({**props, **stamps}, body)
    updated = restamp_frontmatter(text, stamps)
    value, errors = check_contract(docs, "verification", updated)
    if errors:
        raise ValueError("re-pair check failed: " + "; ".join(errors))
    import delivery_compile
    # Pairing is covered by the same ruling that approved this Environment revision.
    refusals = delivery_compile.between_gates_refusals(
        docs, f"Environment Contract revision {environment['revision']}")
    if refusals:
        raise ValueError("; ".join(refusals))

    def unchanged() -> None:
        if path.read_bytes() != original or environment_path.read_bytes() != environment_original:
            raise ValueError("Operation contracts changed during re-pair; retry against the current contracts")

    atomic_file.replace_bytes(path, updated.encode("utf-8"), before_replace=unchanged)
    print(json.dumps({**value, "repaired": True}, sort_keys=True))
    return 0


def initial_props(kind: str, refs: list[str]) -> dict:
    # The hash, the commands and an unbound relation start absent: the vault
    # rejects an empty relation, and an empty value parses as a list, which
    # it rejects for these text properties.
    common = {
        "type": TYPE_FOR[kind], "title": TYPE_FOR[kind].replace("-", " ").title(),
        "status": "draft", "revision": 1, **({"constrained_by": refs} if refs else {}),
        "tags": [f"doc/{TYPE_FOR[kind]}", "status/draft"],
    }
    if kind == "verification":
        return common | {
            "test_workdir": ".",
            "mutation_disposition": "not_applicable",
            "mutation_workdir": ".", "mutation_rationale": "Describe why mutation testing is not applicable.",
            "dependency_audit_disposition": "not_applicable",
            "dependency_audit_workdir": ".", "dependency_audit_rationale": "Describe why dependency auditing is not applicable.",
            "pull_request_check_source": PULL_REQUEST_CHECK_SOURCES[0],
        }
    return common | {
        "env_workdir": ".", "scenarios": ["default"],
        "tolerated_warnings": [], "service_catalog": [],
    }


def init(args) -> int:
    docs = docs_root(args.docs)
    path = contract_path(docs, args.kind)
    if path.exists():
        raise ValueError(f"refusing to overwrite {path}")
    refs = args.constrained_by or []
    props = initial_props(args.kind, refs)
    body = "# " + props["title"] + "\n\n## Contract\n\nFill the declared command contract, then approve it through this compiler.\n\n## Navigation <!-- sec: nav -->\n\n[[maps/operation|Operation]]"
    path.parent.mkdir(parents=True, exist_ok=True)
    map_path = docs / "maps" / "operation.md"
    if not map_path.exists():
        template = Path(__file__).resolve().parents[1] / "templates" / "vault" / "maps" / "operation.md"
        map_path.parent.mkdir(parents=True, exist_ok=True)
        map_path.write_bytes(template.read_text(encoding="utf-8").encode("utf-8"))
    path.write_bytes(render(props, body).encode("utf-8"))
    print(json.dumps({"kind": args.kind, "path": str(path), "status": "draft"}, sort_keys=True))
    return 0


def revise(args) -> int:
    docs = docs_root(args.docs)
    path = contract_path(docs, args.kind)
    props, body = parse(path)
    if props.get("status") != "approved":
        raise ValueError("begin-revision requires an approved contract")
    props["status"] = "draft"
    props["revision"] = int(props.get("revision", 0)) + 1
    props.pop("approved_at_utc", None)
    props.pop("source_hash", None)
    for field in PAIRED_FIELDS:
        props.pop(field, None)
    props["tags"] = [f"doc/{TYPE_FOR[args.kind]}", "status/draft"]
    path.write_bytes(render(props, body).encode("utf-8"))
    print(json.dumps({"kind": args.kind, "path": str(path), "status": "draft", "revision": props["revision"]}, sort_keys=True))
    return 0


def approval_text(docs: Path, kind: str) -> str:
    """Render the draft contract as its approval writes it.

    The approval stamps a source_hash that leaves approved_at_utc out, so the
    receipt an approval produces is known before it runs.
    """
    props, body = parse(contract_path(docs, kind))
    if props.get("status") != "draft":
        raise ValueError("approve requires a draft contract")
    # The review record binds the draft its review read, before the stamp
    # makes the contract one that was approved.
    record_errors = review_record_findings(docs, kind, props, body)
    if record_errors:
        raise ValueError("approval check failed: " + "; ".join(record_errors))
    props["status"] = "approved"
    props["tags"] = [f"doc/{TYPE_FOR[kind]}", "status/approved"]
    props["approved_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if kind == "verification":
        # The paired Environment Contract receipt is compiler-projected from the
        # approved current Environment Contract, never authored by a writer.
        for field in PAIRED_FIELDS:
            props.pop(field, None)
        paired = paired_environment(docs)
        if paired is not None:
            props.update(zip(PAIRED_FIELDS, paired))
    props["source_hash"] = source_hash(props, body)
    return render(props, body)


def approve(args) -> int:
    docs = docs_root(args.docs)
    text = approval_text(docs, args.kind)
    # Check the text the file will hold before writing it, so a refusal leaves the draft as it was.
    value, errors = check_contract(docs, args.kind, text)
    if errors:
        raise ValueError("approval check failed: " + "; ".join(errors))
    # A Delivery between its two fixed owner gates takes a revision only by an owner ruling.
    import delivery_compile
    refusals = delivery_compile.between_gates_refusals(
        docs, f"{TYPE_FOR[args.kind].replace('-', ' ').title()} revision {value['revision']}")
    if refusals:
        raise ValueError("; ".join(refusals))
    contract_path(docs, args.kind).write_bytes(text.encode("utf-8"))
    if args.kind == "environment":
        verification, verification_errors = check_contract(docs, "verification")
        if verification.get("paired_environment"):
            value["verification_pairing"] = {"path": verification["path"],
                                             **verification["paired_environment"]}
            if verification_errors:
                value["verification_pairing"]["errors"] = verification_errors
    print(json.dumps(value, sort_keys=True))
    return 0


def command_in_workdir(command: str, workdir: str) -> str:
    """Render a declared command with its normalized repository workdir."""
    return command if workdir == "." else f"cd {shlex.quote(workdir)} && {command}"


def ci_audit_job(props: dict) -> str:
    if props.get("dependency_audit_disposition") != "required":
        return ""
    command = command_in_workdir(
        str(props["dependency_audit_command"]),
        str(props["dependency_audit_workdir"]),
    )
    return "\n".join((
        "", "  dependency_audit:", "    runs-on: ubuntu-latest", "    steps:",
        "      - uses: actions/checkout@v4",
        "      - name: Audit locked dependencies for known advisories",
        "        run: " + command,
    ))


def ci_environment_job(props: dict) -> str:
    command = command_in_workdir(str(props["env_command"]), str(props["env_workdir"]))
    return "\n".join((
        "", "  environment_smoke:", "    runs-on: ubuntu-latest",
        "    timeout-minutes: 15", "    steps:", "      - uses: actions/checkout@v4",
        "      - name: Stand the environment up from scratch",
        "        run: " + command + " up",
        "      - name: Dump service logs for diagnosis", "        if: failure()",
        "        run: " + command + " logs", "      - name: Tear the environment down",
        "        if: always()", "        run: " + command + " down",
    ))


atomic_text = atomic_file.replace_text


def render_ci(args) -> int:
    """Materialize CI solely from approved Operation Contract receipts."""
    docs = docs_root(args.docs)
    verification, errors = check_contract(docs, "verification")
    if errors or not verification.get("current"):
        raise ValueError("approved current Verification Contract is required: " + "; ".join(errors))
    verification_props, _body = parse(contract_path(docs, "verification"))
    source, provider = pull_request_checks(verification_props)
    if source == "external":
        raise ValueError(f"the approved Verification Contract declares that {provider} reports the pull "
                         "request checks, so the project uses no repository workflow for render-ci to "
                         "materialize")
    environment_props = None
    if args.include_environment:
        environment, env_errors = check_contract(docs, "environment")
        if env_errors or not environment.get("current"):
            raise ValueError("approved current Environment Contract is required: " + "; ".join(env_errors))
        environment_props, _body = parse(contract_path(docs, "environment"))
    template_path = Path(args.template).resolve() if args.template else (
        Path(__file__).resolve().parents[1] / "templates" / "ci-tests.yml"
    )
    template = template_path.read_text(encoding="utf-8")
    substitutions = {
        "{{test_command}}": command_in_workdir(
            str(verification_props["test_command"]),
            str(verification_props["test_workdir"]),
        ),
        "{{dependency_audit_job}}": ci_audit_job(verification_props),
        "{{environment_smoke_job}}": ci_environment_job(environment_props)
        if environment_props is not None else "",
    }
    for token, value in substitutions.items():
        if token not in template:
            raise ValueError(f"CI template is missing required token {token}")
        template = template.replace(token, value)
    if TOKEN_RE.search(template):
        raise ValueError("CI materialization left an unresolved template token")
    output = Path(args.output).resolve()
    atomic_text(output, template)
    print(json.dumps({
        "output": str(output), "verification_hash": verification["source_hash"],
        "environment_hash": None if environment_props is None else check_contract(docs, "environment")[0]["source_hash"],
    }, sort_keys=True))
    return 0


CLOSURE_GATE = Path(".github") / "agentrof" / "vault-gate.pyz"


def require_closure_gate(project_root: Path) -> None:
    """Refuse unless the project's tracked portable gate carries the delivery-closure subcommand.

    The workflow runs the base branch's archive, so an archive installed
    before the subcommand existed would fail every pull request.
    """
    import zipfile

    archive_path = project_root / CLOSURE_GATE
    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            entry = archive.read("__main__.py").decode("utf-8") if "__main__.py" in names else ""
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"{CLOSURE_GATE.as_posix()} cannot be read ({exc}); install it with vault_gate.py install"
                         " and commit it with the workflow") from exc
    if "scripts/delivery_closure.py" not in names or '"delivery-closure"' not in entry:
        raise ValueError(f"{CLOSURE_GATE.as_posix()} has no delivery-closure subcommand; reinstall it with"
                         " vault_gate.py install and commit it in the same commit as the workflow")


def render_closure_ci(args) -> int:
    """Materialize the opt-in Delivery closure workflow.

    It reads no contract value, so the template is written as it ships: its
    GitHub expressions are the workflow's own, not package placeholders. It
    refuses while the project's tracked gate lacks the closure subcommand.
    """
    template_path = Path(args.template).resolve() if args.template else (
        Path(__file__).resolve().parents[1] / "templates" / "delivery-closure.yml"
    )
    require_closure_gate(Path(args.project_root).resolve())
    output = Path(args.output).resolve()
    atomic_text(output, template_path.read_text(encoding="utf-8"))
    print(json.dumps({"output": str(output)}, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "check", "begin-revision", "approve", "status"):
        entry = sub.add_parser(name)
        entry.add_argument("--kind", choices=sorted(KINDS), required=True)
        entry.add_argument("--docs")
        entry.add_argument("--json", action="store_true")
        if name == "init":
            entry.add_argument("--constrained-by", action="append")
    repair_parser = sub.add_parser("re-pair", help="refresh only an unchanged approved Verification Contract's pairing")
    repair_parser.add_argument("--docs")
    render_ci_parser = sub.add_parser("render-ci")
    render_ci_parser.add_argument("--docs")
    render_ci_parser.add_argument("--output", required=True)
    render_ci_parser.add_argument("--template")
    render_ci_parser.add_argument("--include-environment", action="store_true")
    render_closure_parser = sub.add_parser("render-closure-ci")
    render_closure_parser.add_argument("--output", required=True)
    render_closure_parser.add_argument("--template")
    render_closure_parser.add_argument("--project-root", default=".")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return init(args)
        if args.command == "begin-revision":
            return revise(args)
        if args.command == "approve":
            return approve(args)
        if args.command == "re-pair":
            return repair_pairing(args)
        if args.command == "render-ci":
            return render_ci(args)
        if args.command == "render-closure-ci":
            return render_closure_ci(args)
        value, errors = check_contract(docs_root(args.docs), args.kind)
        print(json.dumps({"ok": not errors, "receipt": value, "errors": errors},
                         ensure_ascii=False, sort_keys=True))
        return 1 if errors else 0
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
