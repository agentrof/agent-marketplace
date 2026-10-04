#!/usr/bin/env python3
"""Derive bounded role inputs from canonical team instructions and project files."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess


PACKAGE = Path(__file__).resolve().parents[1]
POLICY = "templates/task-input-policy.json"
# process_policy.REGISTRY; the catalog reads it without importing the compiler.
SWITCH_REGISTRY = "skill-content/configure/data/process-switches.json"
# Entries whose tasks author what an execution approval then pins: the scope,
# the Item topology and the Operation contracts a plan revises.
PLANNING_ENTRIES = frozenset({"delivery-plan", "execution-plan", "configure"})
# A switch whose instructions every task of its owning flows follows declares
# this reference_scope; its references bind whichever skill holds them.
OWNING_FLOWS_SCOPE = "owning_flows"
# How a declared pass kind runs: as the owning writer's generated variant, or
# as a command of the entry itself with no role pass.
PASS_KIND_RUNS = {"writer_variant", "entry_command"}
# The switch value whose cross-epic backlog writer writes only its given inputs.
WRITERS_SWITCH, WRITERS_VALUE = "remediation_writers", "per_epic"
REFERENCE = re.compile(r"\[[^\]]+\]\((references/[^)#]+)(?:#[^)]*)?\)")
SWITCH_REFERENCE = re.compile(r"^switch-([a-z][a-z0-9_]*)-([a-z][a-z0-9_]*)\.md$")
DELIVERY_PACKAGE = re.compile(r"^workspace/docs/delivery/deliveries/([^/]+)/")
CATALOG_NAME_MAPS = ("role_skills", "required_role_skills", "entries",
                     "required_references", "stack_reference_by_role", "read_only_entry_roles")
CANONICAL_SUFFIXES = {".md", ".json"}
OPAQUE_NAMES = {"artifacts", ".obsidian", ".trash"}


def digest(value) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                                separators=(",", ":")).encode()).hexdigest()


def regular(root: Path, relative: str) -> Path:
    path = PurePosixPath(relative)
    if (not isinstance(relative, str) or path.is_absolute() or not path.parts
            or path.as_posix() != relative or "\\" in relative or ":" in relative
            or any(part in {".", ".."} or part.endswith((" ", ".")) for part in path.parts)):
        raise ValueError(f"input must be a relative POSIX file path: {relative}")
    result = root.joinpath(*path.parts)
    for parent in (result, *result.parents):
        if parent == root:
            break
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise ValueError(f"input cannot traverse a symlink: {relative}")
    if not result.is_file():
        raise ValueError(f"required input is missing: {relative}")
    return result


def canonical_source(relative: str) -> bool:
    """Whether the canonical source inventory holds this project-relative file."""
    path = PurePosixPath(relative)
    return (path.parts[:2] == ("workspace", "docs") and len(path.parts) > 2
            and not OPAQUE_NAMES & set(path.parts[2:])
            and path.suffix.lower() in CANONICAL_SUFFIXES)


def outside(relative: str, bound: frozenset[str] | None) -> bool:
    """Whether a task bound to ``bound`` leaves this path out: a canonical
    source it does not read. Without a bound set every path stays in."""
    return bound is not None and relative not in bound and canonical_source(relative)


def source_inventory(root: Path, bound: frozenset[str] | None = None) -> list[dict]:
    """Bind incoming canonical edges without reading opaque artifact interiors.

    A task given ``bound``, the sources it reads, binds and reads only the
    canonical sources among them.
    """
    if bound is not None:
        return identity(root, [path for path in bound if canonical_source(path)])
    docs = root / "workspace/docs"
    for path in (docs.parent, docs):
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("canonical source inventory cannot traverse a symlink")
    if not docs.exists():
        return []
    pending, paths = [docs], []
    while pending:
        with os.scandir(pending.pop()) as entries:
            children = sorted(entries, key=lambda entry: entry.name)
        for entry in children:
            if entry.name in OPAQUE_NAMES:
                continue
            path = Path(entry.path)
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ValueError("canonical source inventory cannot traverse a symlink")
            if entry.is_dir(follow_symlinks=False):
                pending.append(path)
            elif path.suffix.lower() in CANONICAL_SUFFIXES:
                paths.append(path.relative_to(root).as_posix())
    return identity(root, paths)


def identity(root: Path, paths) -> list[dict]:
    records = []
    for relative in sorted(set(paths)):
        path = regular(root, relative)
        before = path.stat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"input is not a regular file: {relative}")
        content = path.read_bytes()
        regular(root, relative)
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_mode) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_mode):
            raise ValueError(f"input changed while being read: {relative}")
        records.append({"path": relative, "sha256": hashlib.sha256(content).hexdigest(),
                        "executable": bool(after.st_mode & 0o111)})
    return records


def agent_variants(package: Path) -> dict[str, str]:
    """Return each generated agent variant the switch registry declares, with its base role.

    A build writes the variants beside their base agents, but a task always
    runs as the base role, so the role catalog leaves the variants out.
    """
    path = package / SWITCH_REGISTRY
    if not path.is_file():
        return {}
    try:
        switches = json.loads(path.read_text(encoding="utf-8"))["switches"]
        return {f"{agent}-{variant['suffix']}": agent
                for _switch, spec in sorted(switches.items())
                for _value, variant in sorted((spec.get("agent_variants") or {}).items())
                for agent in variant["agents"]}
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"process switch registry cannot be read: {exc}") from exc


def catalog(package: Path = PACKAGE) -> dict:
    policy = json.loads(regular(package, POLICY).read_text(encoding="utf-8"))
    if policy.get("schema_version") != 1:
        raise ValueError("unsupported task input policy")
    for name in CATALOG_NAME_MAPS:
        if not isinstance(policy.get(name), dict):
            raise ValueError(f"task input policy requires the {name} map")
        if any(not re.fullmatch(r"[a-z][a-z0-9_]*", key) for key in policy[name]):
            raise ValueError(f"{name} keys must use snake_case")
        policy[name] = {key.replace("_", "-"): value for key, value in policy[name].items()}
    agents = {path.stem for path in (package / "agents").glob("*.md")} - set(agent_variants(package))
    skills = {path.parent.name for path in (package / "skill-content").glob("*/SKILL.md")}
    entries = {path.parent.name for path in (package / "skill-content").glob("*/SKILL.md")
               if re.search(r"^exposure: entry$", path.read_text(encoding="utf-8"), re.M)}
    flows = {path.stem for path in (package / "flows").glob("*.md")}
    # A session entry changes only the orchestrating session and delegates no task.
    session = policy.get("session_entries", [])
    if (not isinstance(session, list) or len(session) != len(set(session))
            or set(session) & set(policy["entries"])):
        raise ValueError("session entries must be a unique list outside the task routes")
    if (set(policy["role_skills"]) != agents or set(policy["required_role_skills"]) != agents
            or set(policy["entries"]) | set(session) != entries):
        raise ValueError("task input policy must cover every current role and entry exactly")
    mapped_skills = set(policy["entries"]) | set(session) | {
        skill for bound in policy["role_skills"].values() for skill in bound}
    mapped_flows = {flow for entry in policy["entries"].values() for flow in entry["flows"]}
    if mapped_skills != skills or mapped_flows != flows:
        raise ValueError("task input policy has missing or unknown skills or flows")
    internal = skills - entries
    if not set(policy["technology_method_skills"]).issubset(internal):
        raise ValueError("technology methods must be installed internal skills")
    for role, bound in policy["role_skills"].items():
        required = policy["required_role_skills"][role]
        if (len(bound) != len(set(bound)) or len(required) != len(set(required))
                or not set(required).issubset(bound)
                or set(bound) - internal
                or set(required) & set(policy["technology_method_skills"])):
            raise ValueError("required role skills must be unique declared non-technology methods")
    if set(policy["read_only_roles"]) - agents or set(policy["stack_reference_by_role"]) - agents:
        raise ValueError("task input role policy names an unknown role")
    for entry, roles in policy["read_only_entry_roles"].items():
        if entry not in entries or set(roles) - set(policy["entries"][entry]["roles"]):
            raise ValueError("task input read-only role is outside its entry")
    for entry in policy["entries"].values():
        if (set(entry["roles"]) - agents or len(entry["roles"]) != len(set(entry["roles"]))
                or len(entry["flows"]) != len(set(entry["flows"]))):
            raise ValueError("task input entry names an unknown role")
        if type(entry.get("project_state")) is not bool:
            raise ValueError("task input entry needs an explicit project state boundary")
        if type(entry.get("allow_unborn_head", False)) is not bool:
            raise ValueError("task input unborn HEAD policy must be boolean")
        scope = entry.get("write_scope", {})
        if (scope.get("resolver") not in {"unresolved", "ba_documents", "backlog_documents", "item_claims"}
                or not isinstance(scope.get("roles"), list)
                or set(scope["roles"]) - set(entry["roles"])
                or not isinstance(entry.get("next_transition"), str) or not entry["next_transition"].strip()):
            raise ValueError("task input entry needs a bounded write scope and next transition")
    for skill, references in policy["required_references"].items():
        if skill not in skills:
            raise ValueError("task input policy names an unknown skill")
        for reference in references:
            regular(package, f"skill-content/{skill}/{reference}")
    policy["pass_kinds"] = pass_kind_catalog(policy, agents, package)
    return policy


def pass_kind_catalog(policy: dict, agents: set[str], package: Path) -> dict:
    """Validate the declared mechanical pass kinds, with each writer's documents by role."""
    kinds = policy.get("pass_kinds", {})
    if not isinstance(kinds, dict):
        raise ValueError("task input pass kinds must map a kind to its declaration")
    registry = registry_switches(package)
    for kind, spec in kinds.items():
        if (not re.fullmatch(r"[a-z][a-z0-9_]*", kind) or not isinstance(spec, dict)
                or spec.get("runs_as") not in PASS_KIND_RUNS
                or not all(isinstance(spec.get(key), str) for key in ("switch", "value"))):
            raise ValueError("a task input pass kind needs runs_as, switch and value")
        if spec["runs_as"] != "writer_variant":
            continue
        documents = spec.get("documents")
        if (not isinstance(spec.get("modes"), list) or not spec["modes"]
                or set(spec["modes"]) - set(policy["modes"]) or not isinstance(documents, dict)
                or any(not isinstance(patterns, list) or not patterns
                       or not all(isinstance(item, str) and item and not item.startswith("/")
                                  and ".." not in item.split("/") for item in patterns)
                       for patterns in documents.values())):
            raise ValueError(f"pass kind {kind} needs its modes and each writer's documents")
        spec["documents"] = {role.replace("_", "-"): patterns for role, patterns in documents.items()}
        variants = (registry.get(spec["switch"], {}).get("agent_variants") or {}).get(spec["value"])
        if set(spec["documents"]) - agents or (registry and (
                not isinstance(variants, dict)
                or set(spec["documents"]) != set(variants.get("agents") or []))):
            raise ValueError(f"pass kind {kind} must name exactly the writers whose variants"
                             f" switch {spec['switch']} declares at {spec['value']}")
    return kinds


def document_pattern(pattern: str) -> re.Pattern:
    return re.compile("workspace/docs/" + re.escape(pattern).replace(r"\*", "[^/]+"))


def mechanical_pass(policy: dict, registry: dict[str, dict], kind: str, *, entry: str,
                    role: str | None, mode: str, route: dict, read_only: bool, chosen: set,
                    findings: str | None, inputs: list[str], project: Path | None) -> list[str]:
    """Check that a task may run as mechanical pass *kind*; return the documents it writes.

    A mechanical pass decides nothing, so a review, re-check, calibration,
    triage or repair task, and every role without a writer variant, keeps its
    role's tier.
    """
    kinds = policy["pass_kinds"]
    spec = kinds.get(kind)
    if spec is None:
        raise ValueError(f"unknown pass kind {kind!r}; the task input policy declares {sorted(kinds)}")
    if spec["runs_as"] == "entry_command":
        if role is not None:
            raise ValueError(f"pass kind {kind} runs as a direct entry command; derive no role"
                             " task for it")
        return []
    owners = set(registry.get(spec["switch"], {}).get("flows") or [])
    if not owners & set(route["flows"]):
        raise ValueError(f"pass kind {kind} belongs to the flows switch {spec['switch']} owns;"
                         f" a {entry} task, a code repair included, keeps its role's tier")
    if read_only:
        raise ValueError(f"pass kind {kind} never serves a review, re-check or calibration: this"
                         f" {mode} task of {role} keeps its role's tier")
    if mode not in spec["modes"]:
        raise ValueError(f"pass kind {kind} runs in mode {' or '.join(spec['modes'])}; a {mode}"
                         " task, authoring or repair, keeps its role's tier")
    if role not in spec["documents"]:
        raise ValueError(f"pass kind {kind} runs only as a writer whose variant switch"
                         f" {spec['switch']} declares ({', '.join(sorted(spec['documents']))});"
                         f" {role} keeps its tier")
    if (spec["switch"], spec["value"]) not in chosen:
        raise ValueError(f"pass kind {kind} runs only at switch {spec['switch']} {spec['value']}")
    if findings is None or project is None:
        raise ValueError(f"pass kind {kind} binds the verdict's findings; pass --findings <record>")
    try:
        record = json.loads(regular(project, findings).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"pass kind {kind} reads its findings as JSON: {exc}") from exc
    listed = record.get("findings") if isinstance(record, dict) else record
    if not isinstance(listed, list) or not listed:
        raise ValueError(f"pass kind {kind} needs at least one returned finding")
    for finding in listed:
        name = finding.get("id") if isinstance(finding, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"pass kind {kind} needs a stable id on every finding")
        repair = finding.get("repair")
        if not isinstance(repair, str) or not repair.strip():
            raise ValueError(f"pass kind {kind}: finding {name} names no exact repair; the base"
                             " writer triages it")
    patterns = [document_pattern(pattern) for pattern in spec["documents"][role]]
    documents = sorted(path for path in set(inputs)
                       if any(pattern.fullmatch(path) for pattern in patterns))
    if not documents:
        raise ValueError(f"pass kind {kind} needs the {role} document it changes as an --input")
    return documents


def switch_reference(package: Path, path: Path) -> tuple[str, str] | None:
    """Return the switch and value a skill's switch reference file is named for."""
    relative = path.relative_to(package).parts
    if len(relative) != 4 or relative[0] != "skill-content" or relative[2] != "references":
        return None
    match = SWITCH_REFERENCE.match(relative[3])
    return (match.group(1), match.group(2)) if match else None


def switch_data(package: Path) -> dict[tuple[str, str], list[str]]:
    """Return the package data files that the registry declares for each switch value.

    Only the instructions of the values that list a file read it, so it is
    bound together with the switch references of whichever of them the project
    chose, and never on another path. A file that several values read, of one
    switch or of several, is listed under each of them.
    """
    from process_policy import REGISTRY
    path = package / REGISTRY
    if not path.is_file():
        return {}
    try:
        switches = json.loads(path.read_text(encoding="utf-8"))["switches"]
        return {(switch, value): list(paths)
                for switch, spec in sorted(switches.items())
                for value, paths in sorted(spec.get("value_data", {}).items())}
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"process switch registry cannot be read: {exc}") from exc


def task_deliveries(project: Path, delivery: str | None, inputs: list[str]) -> list[str]:
    """Return the Deliveries a task runs inside: the one it names and every one
    whose package holds a selected input."""
    found = {delivery} if delivery else set()
    for relative in inputs:
        match = DELIVERY_PACKAGE.match(relative)
        if match is None or match.group(1) in {".", ".."}:
            continue
        record = project / "workspace/docs/delivery/deliveries" / match.group(1) / "delivery.md"
        if not record.is_file():
            continue
        from ba_compile import parse_frontmatter
        props, _line, error = parse_frontmatter(record.read_text(encoding="utf-8"))
        if error or not isinstance(props.get("id"), str) or not props["id"]:
            raise ValueError(f"Delivery record cannot be read: {record.relative_to(project).as_posix()}")
        found.add(props["id"])
    return sorted(found)


def registry_switches(package: Path) -> dict[str, dict]:
    """Return the switch registry's declarations, or none when the package has no registry."""
    path = package / SWITCH_REGISTRY
    if not path.is_file():
        return {}
    try:
        switches = json.loads(path.read_text(encoding="utf-8"))["switches"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ValueError(f"process switch registry cannot be read: {exc}") from exc
    if not isinstance(switches, dict) or not all(isinstance(spec, dict) for spec in switches.values()):
        raise ValueError("process switch registry cannot be read: switches must map ids to objects")
    return switches


def owned_switches(registry: dict[str, dict], route: dict) -> set[str]:
    """Return the switches that own one of the flows a task's entry runs."""
    return {switch for switch, spec in registry.items()
            if set(route["flows"]) & set(spec.get("flows") or [])}


def plan_revision_held(project: Path, delivery: str, remote: str) -> bool:
    """Whether the remote Fence holds a plan-revision barrier that this Delivery began."""
    import delivery_git

    try:
        refs = delivery_git.canonical_refs(delivery)
        tips = delivery_git.remote_ref_oids(project, remote, [refs["fence"], refs["integration"]])
        if not all(tips.values()):
            return False
        fence = delivery_git.commit_message(project, tips[refs["fence"]])
        epoch = delivery_git.trailer(fence, "Barrier-Epoch")
        if delivery_git.trailer(fence, "Barrier-Kind") != "plan-revision" or epoch in {None, "none"}:
            return False
        # The barrier's own Integration record binds its epoch to this Delivery.
        begun = delivery_git.run_git(project, "log", "--format=%H", "--fixed-strings",
                                     f"--grep=Agentrof-Barrier-Epoch: {epoch}",
                                     tips[refs["integration"]])
        for oid in begun.split():
            record = delivery_git.commit_message(project, oid)
            if all(delivery_git.trailer(record, key) == expected for key, expected in (
                    ("Record", "delivery-barrier-v1"), ("Delivery", delivery),
                    ("Barrier-Kind", "plan-revision"), ("Barrier-Epoch", epoch))):
                return True
    except (RuntimeError, OSError, ValueError):
        return False
    return False


def switch_choices(project: Path | None, route: dict, package: Path,
                   deliveries: list[str] | None = None, entry: str | None = None,
                   remote: str = "origin") -> tuple[set, list[str]]:
    """Return the non-default switch values a task runs under and its policy input.

    Without a Process Policy both are empty, so the manifest is unchanged. A
    task inside a Delivery runs under the switch values that Delivery pinned:
    while the pin is enforced, a policy that changed a value of a switch the
    task's flows own and the Delivery still reads refuses the derivation, as a
    switch read of that Delivery does, and from the Delivery Review on the
    pinned revision's values are bound. A switch no Delivery flow owns is
    bound from the current policy. A planning task binds the approved policy
    instead where the next execution approval pins it: before the first one,
    and while a plan-revision barrier is held.
    """
    if project is None or not route["project_state"]:
        return set(), []
    import process_policy
    docs = project / "workspace" / "docs"
    policy_input = (["workspace/docs/" + process_policy.RELATIVE]
                    if process_policy.path_for(docs).exists() else [])
    if deliveries:
        owned = owned_switches(registry_switches(package), route)
        chosen = None
        for delivery in deliveries:
            try:
                values = process_policy.delivery_values(docs, delivery, package, owned)["values"]
            except process_policy.PinDrift as drift:
                if not (entry in PLANNING_ENTRIES and (drift.status == "scope_approved" or (
                        drift.status == "execution_approved"
                        and plan_revision_held(project, delivery, remote)))):
                    raise ValueError(f"{delivery}: {drift}") from drift
                values = process_policy.effective_values(docs, package)[0]
            except ValueError as exc:
                raise ValueError(f"{delivery}: {exc}") from exc
            pairs = {(switch, value["value"]) for switch, value in values.items()
                     if value["value"] != value["default"]}
            if chosen is not None and pairs != chosen:
                raise ValueError("the task's inputs span Deliveries that run different switch values")
            chosen = pairs
        return chosen, policy_input
    if not policy_input:
        return set(), []
    values, _snapshot = process_policy.effective_values(docs, package)
    return ({(switch, value["value"]) for switch, value in values.items()
             if value["value"] != value["default"]}, policy_input)


def git_bytes(command: list[str], *args: str) -> bytes:
    result = subprocess.run([*command, *args], capture_output=True)
    if result.returncode:
        raise ValueError("cannot derive complete task change inventory")
    return result.stdout


def working_inventory(root: Path, command: list[str], *, unborn: bool = False,
                      bound: frozenset[str] | None = None) -> list[dict]:
    """Bind dirty and new sources that a HEAD-only diff cannot describe.

    With ``bound``, a canonical source outside it is neither listed nor read.
    """
    flags = git_bytes(command, "ls-files", "-v", "-z")
    if any(row and (row[:1].islower() or row[:1] in {b"S", b"s"})
           for row in flags.split(b"\0")):
        raise ValueError("task inputs require visible Git index entries; remove hidden index flags")
    changed = (git_bytes(command, "ls-files", "-z") if unborn else
               git_bytes(command, "diff", "--no-ext-diff", "--no-textconv",
                         "--name-only", "--no-renames", "-z", "HEAD", "--"))
    untracked = git_bytes(command, "ls-files", "--others", "--exclude-standard",
                          "--exclude=workspace/docs/**/artifacts/**", "-z")
    names = {os.fsdecode(raw) for raw in (changed + untracked).split(b"\0") if raw}
    records = []
    for name in sorted(names):
        parts = PurePosixPath(name).parts
        if (parts[:2] == ("workspace", "docs") and "artifacts" in parts[2:]) or outside(name, bound):
            continue
        path = root / name
        if not path.exists() and not path.is_symlink():
            records.append({"path": name, "deleted": True})
        else:
            records.extend(identity(root, [name]))
    return records


def write_scope(project: Path | None, paths: set[str], role: str | None, route: dict,
                read_only: bool, closure: dict | None, package: Path,
                cross_epic: bool = False) -> dict:
    """Describe selected authoring bounds without minting writer authority.

    Read dependencies are not write targets. Unknown compiler selections and
    new documents stay unresolved rather than granting a whole stage directory.
    A cross-epic remediation writer, at remediation_writers per_epic, writes
    only the backlog documents it was given as inputs.
    """
    result = {"status": "read_only" if read_only else "unresolved", "allowed_write_area": [],
              "source_records": [], "writer_authority": False,
              "constraints": ["existing owner/compiler approval remains required",
                              "compiler-owned fields and generated projections are excluded"],
              "reason": "role or task mode is read-only" if read_only else "owning compiler has not resolved a supported write scope"}
    spec = route["write_scope"]
    if read_only or project is None or role not in spec["roles"]:
        return result
    from ba_compile import parse_frontmatter

    def properties(relative: str) -> dict:
        props, _line, error = parse_frontmatter(regular(project, relative).read_text(encoding="utf-8"))
        if error:
            raise ValueError(f"cannot derive write scope from {relative}: {error}")
        return props

    selected = {path for path in paths if path.endswith(".md")
                and not {"_generated", "artifacts", ".obsidian", ".trash"} & set(PurePosixPath(path).parts)}
    targets = []
    sources = []
    if spec["resolver"] == "ba_documents":
        selected = {path for path in selected
                    if re.fullmatch(r"workspace/docs/business-analysis/[^/]+/.+\.md", path)}
        if len({PurePosixPath(path).parts[3] for path in selected}) > 1:
            result["reason"] = "selected BA inputs span multiple spaces; resolve one owning space"
            return result
        schema = json.loads(regular(package, "skill-content/business-analysis/data/space-schema.json").read_text(encoding="utf-8"))
        for path in sorted(selected):
            props = properties(path)
            if props.get("type") in schema["doc_types"] and props.get("owner_role") == "business_analyst":
                targets.append({"path": path, "coverage": "exact_file", "source": path})
                sources.append(path)
    elif spec["resolver"] == "backlog_documents":
        if closure is None and not cross_epic:
            result["reason"] = "backlog write scope needs the existing --epic closure to separate primary sources from dependencies"
            return result
        if closure is None:
            # The notes that cross-epic findings name, given one --input each.
            bounded = {path.removeprefix("workspace/docs/") for path in selected}
        else:
            bounded = set(closure["primary_paths"]) | {closure["review"]["path"]}
            if closure["scope"] != "backlog":
                bounded.discard("backlog/backlog.md")
        selected &= {"workspace/docs/" + path for path in bounded}
        patterns = {
            "backlog": r"backlog/backlog\.md",
            "backlog-review": r"backlog/reviews/[^/]+\.md",
            "epic": r"backlog/epics/[^/]+/epic\.md",
            "epic-review": r"backlog/epics/[^/]+/reviews/[^/]+\.md",
            "story": r"backlog/epics/[^/]+/stories/[^/]+/story\.md",
            "test-plan": r"backlog/epics/[^/]+/stories/[^/]+/test-plan\.md",
        }
        for path in sorted(selected):
            if not path.startswith("workspace/docs/backlog/"):
                continue
            props = properties(path)
            kind = props.get("type")
            if kind not in patterns or not re.fullmatch(patterns[kind], path.removeprefix("workspace/docs/")):
                continue
            # Story implementation ownership and QA content ownership do not
            # transfer the Product Owner's canonical backlog writing boundary.
            if kind not in {"story", "test-plan"} and props.get("owner_role") != "product_owner":
                continue
            targets.append({"path": path, "coverage": "exact_file", "source": path})
            sources.append(path)
    elif spec["resolver"] == "item_claims":
        from delivery_compile import _is_normalized_claim, implementation_schedule, lane_roles, lane_scope_map
        items = sorted(path for path in selected if re.fullmatch(
            r"workspace/docs/delivery/deliveries/[^/]+/items/[^/]+/item\.md", path))
        if len(items) != 1:
            result["reason"] = "implementation scope needs exactly one selected Item record"
            return result
        item = items[0]
        props = properties(item)
        claims = props.get("path_claims")
        roles = props.get("role_sequence")
        if (props.get("type") != "delivery-item" or not isinstance(roles, list)
                or role.replace("-", "_") not in roles
                or not isinstance(claims, list) or not claims
                or any(not isinstance(path, str) or not _is_normalized_claim(path) for path in claims)
                or len(claims) != len(set(claims))):
            result["reason"] = "selected Item has no valid product path claims for this implementation role"
            return result
        try:
            lane = (implementation_schedule(props) == "parallel_lanes_v1"
                    and role.replace("-", "_") in lane_roles(props))
        except ValueError:
            result["reason"] = "selected Item declares an unsupported implementation_schedule"
            return result
        owned = claims
        if lane:
            # A lane writes only its approved lane scope; the other lanes of the
            # Item write theirs in the same worktree at the same time.
            scopes, unreadable = lane_scope_map(props)
            owned = scopes.get(role.replace("-", "_"), [])
            if unreadable or not owned or not set(owned) <= set(claims):
                result["reason"] = "selected Item has no valid lane scope for this implementation role"
                return result
        targets = [{"path": path, "coverage": "path_and_descendants", "source": item}
                   for path in sorted(owned)
                   if not any(path == root or path.startswith(root + "/")
                              for root in ("workspace/docs", ".git", ".agentrof"))]
        sources = [item]
        result["excluded_subtrees"] = ["workspace/docs", ".git", ".agentrof"]
        result["constraints"].append("approved Item plan, current writer receipt, and completed/cancelled readers remain mandatory")
        if lane:
            result["constraints"].append("parallel lane: write only this lane scope; the other lanes of the Item write theirs concurrently and the coordinator alone commits")
    if targets:
        result.update(status="resolved", allowed_write_area=targets, source_records=sources,
                      reason="derived only from selected bound owner records; no new writer authority")
    return result


def manifest(*, entry: str, role: str | None, mode: str, project: Path | None = None,
             inputs: list[str] | None = None, skills: list[str] | None = None,
             findings: str | None = None, base: str | None = None, epic: str | None = None,
             expected_hash: str | None = None, package: Path = PACKAGE,
             delivery: str | None = None, remote: str = "origin",
             pass_kind: str | None = None, full_root_reason: str | None = None) -> dict:
    policy = catalog(package)
    package = package.resolve()
    project = project.resolve() if project is not None else None
    if entry not in policy["entries"] or mode not in policy["modes"]:
        raise ValueError("unknown entry or task mode")
    route = policy["entries"][entry]
    variants = agent_variants(package)
    if role in variants:
        raise ValueError(f"{role} is a generated variant of {variants[role]}; derive its task as"
                         f" {variants[role]}")
    if role is not None and role not in route["roles"]:
        raise ValueError("role does not belong to the selected entry")
    if not route["project_state"] and (project is not None or inputs or findings or base or epic):
        raise ValueError("external entry uses conversation inputs only; it creates no project manifest")
    internal = {skill for bound in policy["role_skills"].values() for skill in bound}
    if set(skills or []) - internal:
        raise ValueError("selected method skills must be installed internal skills")
    if not route["project_state"] and skills:
        raise ValueError("external entry does not select project method skills")
    selected_skills = {entry, *(skills or [])}
    if role:
        selected_skills.update(policy["required_role_skills"][role])
    if route["project_state"]:
        selected_skills.add("obsidian-vault")
    if delivery is not None and (project is None or not route["project_state"]):
        raise ValueError("a Delivery belongs to a project task")
    try:
        deliveries = task_deliveries(project, delivery, list(inputs or [])) \
            if project is not None and route["project_state"] else []
        chosen, policy_inputs = switch_choices(project, route, package, deliveries, entry, remote)
    except ValueError as exc:
        raise ValueError(f"process policy cannot bind switch instructions: {exc}") from exc
    registry = registry_switches(package)
    # A task follows only the switches that own one of its entry's flows.
    owned = owned_switches(registry, route)
    chosen = {pair for pair in chosen if pair[0] in owned}
    value_data = switch_data(package)
    switch_only = {path for paths in value_data.values() for path in paths}
    required = {"constitution.md", POLICY, "templates/task-input-contract.md"}
    if role:
        required.add(f"agents/{role}.md")
    required.update(f"flows/{flow}.md" for flow in route["flows"])
    references = {}
    for skill in sorted(selected_skills):
        relative = f"skill-content/{skill}/SKILL.md"
        text = regular(package, relative).read_text(encoding="utf-8")
        required.add(relative)
        for line in text.splitlines():
            for reference in REFERENCE.findall(line):
                path = f"skill-content/{skill}/{reference}"
                regular(package, path)
                references[path] = {"path": path, "read_when": line.strip()}
        required.update(f"skill-content/{skill}/{reference}"
                        for reference in policy["required_references"].get(skill, []))
        stack_reference = policy["stack_reference_by_role"].get(role)
        if stack_reference:
            path = f"skill-content/{skill}/references/{stack_reference}"
            if (package / path).is_file():
                required.add(path)
        switch_root = package / "skill-content" / skill / "references"
        for path in sorted(switch_root.glob("switch-*.md")) if switch_root.is_dir() else []:
            pair = switch_reference(package, path)
            if pair in chosen:
                required.add(path.relative_to(package).as_posix())
                required.update(value_data.get(pair, []))
    for switch, value in sorted(chosen):
        if registry[switch].get("reference_scope") == OWNING_FLOWS_SCOPE:
            for path in sorted((package / "skill-content").glob(
                    f"*/references/switch-{switch}-{value}.md")):
                required.add(path.relative_to(package).as_posix())
                required.update(value_data.get((switch, value), []))
    instruction_inputs = required | set(references) | {"scripts/task_inputs.py"}
    # Tools and data influence the role's result even when they are not prose
    # reads. Bind them without turning an input index into extra reading work.
    implementation_roots = [package / "scripts", *(
        package / "skill-content" / skill for skill in selected_skills)]
    for root in implementation_roots:
        # A switch reference or switch value data of a value the project did not
        # choose is neither read nor hashed, so the default path binds exactly
        # what it bound before.
        instruction_inputs.update(path.relative_to(package).as_posix()
                                  for path in root.rglob("*")
                                  if path.is_file() and "__pycache__" not in path.parts
                                  and path.suffix not in {".pyc", ".pyo"}
                                  and path.name != ".DS_Store"
                                  and switch_reference(package, path) is None
                                  and path.relative_to(package).as_posix() not in switch_only)
    instruction_files = identity(package, instruction_inputs)
    project_files = set(inputs or []) | set(policy_inputs)
    if findings:
        project_files.add(findings)
    read_only = (role in policy["read_only_roles"]
                 or role in policy["read_only_entry_roles"].get(entry, [])
                 or mode in {"review", "consume"})
    documents = None if pass_kind is None else mechanical_pass(
        policy, registry, pass_kind, entry=entry, role=role, mode=mode, route=route,
        read_only=read_only, chosen=chosen, findings=findings, inputs=list(inputs or []),
        project=project)
    closure = None
    if epic is not None:
        if entry != "backlog-plan" or project is None:
            raise ValueError("epic scope belongs to a project backlog task")
        import backlog_review_inputs
        closure = backlog_review_inputs.manifest(project / "workspace/docs", epic=epic or None,
                                                 writer=not read_only,
                                                 full_root_reason=full_root_reason)
        project_files.update("workspace/docs/" + path for path in closure["paths"])
    elif full_root_reason is not None:
        raise ValueError("a full root read request belongs to a root review task (--epic with no id)")
    if project is None and project_files:
        raise ValueError("project root is required for project inputs")
    # An exact epic's closure is derived again on every run, so a source that
    # reaches it, an incoming dependency edge included, joins its paths. Like
    # the epic's review manifest, the task binds those and none of the other
    # canonical sources, which another epic's writer changes in parallel.
    read_set = frozenset(project_files) if epic else None
    records = identity(project, project_files) if project is not None else []
    inventory = source_inventory(project, read_set) if project is not None else []
    method_bindings = {}
    technology = set(skills or []) & set(policy["technology_method_skills"])
    if project is not None and technology:
        from ba_compile import parse_frontmatter
        from stage_package import paths_are_committed
        for relative in sorted(project_files):
            if not relative.startswith("workspace/docs/solution-design/decisions/") or not relative.endswith(".md"):
                continue
            path = regular(project, relative)
            props, _end, error = parse_frontmatter(path.read_text(encoding="utf-8"))
            bound = props.get("method_skills", [])
            if not error and props.get("status") == "accepted" and isinstance(bound, list):
                selected = technology & {value for value in bound if isinstance(value, str)}
                if selected and paths_are_committed([path]):
                    for skill in selected:
                        method_bindings.setdefault(skill, []).append(relative)
        if technology - set(method_bindings):
            raise ValueError("technology methods require selected committed accepted Solution decision inputs")
    # At remediation_writers per_epic, a writer without --epic that applies
    # findings is the cross-epic writer.
    cross_epic = (epic is None and findings is not None and not read_only
                  and (WRITERS_SWITCH, WRITERS_VALUE) in chosen)
    scope = write_scope(project, set(inputs or []) | ({"workspace/docs/" + path for path in closure["paths"]}
                                                    if closure else set()),
                        role, route, read_only, closure, package, cross_epic)
    if documents:
        scope.update(status="resolved", source_records=documents,
                     allowed_write_area=[{"path": path, "coverage": "exact_file", "source": path}
                                         for path in documents],
                     reason=f"{pass_kind} writes only the owning writer's documents that the bound"
                            " findings change; no new writer authority",
                     constraints=[*scope["constraints"],
                                  f"{pass_kind}: apply exactly the repairs the bound findings name"])
    transitions = [
        {"condition": "source_identity", "status": "required",
         "detail": "Rerun this invocation with --expected-hash before returning or persisting the result."},
        {"condition": "complete_reads_and_result", "status": "required",
         "detail": "Complete required and applicable conditional reads, role passes, and the output contract."},
        {"condition": "entry_gate", "status": "required", "detail": route["next_transition"]},
    ]
    if not read_only and route["project_state"]:
        transitions.extend([
            {"condition": "write_scope", "status": "required" if scope["status"] == "resolved" else "unresolved",
             "detail": "The owning compiler must resolve any new or unsupported target before writing; the manifest grants no authority."},
            {"condition": "reader_barrier", "status": "required", "detail": policy["barrier"]},
        ])
    changes = []
    head = None
    working = []
    if project is not None:
        project = project.resolve()
        command = ["git", "--no-replace-objects", "-C", str(project)]
        toplevel = git_bytes(command, "rev-parse", "--show-toplevel").decode("utf-8").strip()
        if Path(toplevel).resolve() != project:
            raise ValueError("project root must be the Git worktree root")
        observed = subprocess.run([*command, "rev-parse", "--verify", "HEAD"], capture_output=True)
        unborn = bool(observed.returncode)
        if unborn and (not route.get("allow_unborn_head", False) or base is not None):
            raise ValueError("project must have a committed Git HEAD")
        head = observed.stdout.decode("ascii").strip() if not unborn else None
        working = working_inventory(project, command, unborn=unborn, bound=read_set)
        changes = [record["path"] for record in working]
        if base is not None:
            if base.startswith("-"):
                raise ValueError("task base must not be a Git option")
            resolved = subprocess.run([*command, "rev-parse", "--verify", base + "^{commit}"], capture_output=True)
            if resolved.returncode:
                raise ValueError("task base does not resolve to an exact commit")
            base = resolved.stdout.decode("ascii").strip()
            changed = subprocess.run([*command, "diff", "--no-ext-diff", "--no-textconv",
                                      "--name-only", "--no-renames", "-z", base, head, "--"], capture_output=True)
            if changed.returncode:
                raise ValueError("cannot derive complete task change inventory")
            changes = sorted({os.fsdecode(path) for path in changed.stdout.split(b"\0")
                              if path and not outside(os.fsdecode(path), read_set)}
                             | {record["path"] for record in working})
        if records != identity(project, project_files) or inventory != source_inventory(project, read_set):
            raise ValueError("project inputs changed while building task inputs")
        current = subprocess.run([*command, "rev-parse", "--verify", "HEAD"], capture_output=True)
        if bool(current.returncode) != unborn or (not unborn and current.stdout.decode("ascii").strip() != head):
            raise ValueError("project HEAD changed while building task inputs")
        if working != working_inventory(project, command, unborn=unborn, bound=read_set):
            raise ValueError("project worktree changed while building task inputs")
    if instruction_files != identity(package, instruction_inputs):
        raise ValueError("instructions changed while building task inputs")
    result = {"schema_version": 1, "entry": entry, "role": role, "mode": mode,
              "required_reads": sorted(required), "conditional_reads": [references[path] for path in sorted(references) if path not in required],
              "instructions": instruction_files, "project_inputs": records, "head": head, "base": base,
              "canonical_source_inventory": inventory, "method_bindings": method_bindings,
              "working_inputs": working,
              "changed_paths": changes, "backlog_scope": closure, "open_findings": findings,
              "write_boundary": "read_only" if read_only else "named_owner_only",
              "write_scope": scope, "next_transition_conditions": transitions,
              "barrier": policy["barrier"], "repair_policy": policy["repair_policy"],
              "output_contract": policy["output_contract"], "approval_authority": False,
              "available_method_skills": policy["role_skills"].get(role, []),
              "selected_method_skills": sorted(skills or [])}
    if pass_kind is not None:
        result["pass_kind"] = pass_kind
    hashed = result
    if epic:
        # The closure is bound as its own source_hash binds it: the stubs it
        # lists from notes outside its paths are information, never an input.
        hashed = dict(result, backlog_scope=backlog_review_inputs.bound_view(closure))
    result["source_hash"] = digest(hashed)
    if expected_hash is not None and result["source_hash"] != expected_hash:
        raise ValueError("task inputs are stale; regenerate before persisting a result")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-catalog", action="store_true")
    parser.add_argument("--entry")
    parser.add_argument("--role")
    parser.add_argument("--mode", default="review")
    parser.add_argument("--project-root", type=Path)
    parser.add_argument("--input", action="append", default=[])
    parser.add_argument("--skill", action="append", default=[])
    parser.add_argument("--findings")
    parser.add_argument("--base")
    parser.add_argument("--epic", nargs="?", const="")
    parser.add_argument("--expected-hash")
    parser.add_argument("--delivery")
    parser.add_argument("--remote", default="origin",
                        help="the Delivery remote whose Fence shows a held plan-revision barrier")
    parser.add_argument("--full-root-reason",
                        help="a root reader's reason to read the whole package (root_review_scope"
                             " revision_delta)")
    parser.add_argument("--pass-kind",
                        help="a mechanical pass kind that templates/task-input-policy.json declares")
    args = parser.parse_args(argv)
    try:
        result = ({"ok": True, "entries": sorted(catalog()["entries"])} if args.check_catalog else
                  manifest(entry=args.entry, role=args.role, mode=args.mode, project=args.project_root,
                           inputs=args.input, skills=args.skill, findings=args.findings, base=args.base,
                           epic=args.epic, expected_hash=args.expected_hash,
                           delivery=args.delivery, remote=args.remote,
                           pass_kind=args.pass_kind, full_root_reason=args.full_root_reason))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
