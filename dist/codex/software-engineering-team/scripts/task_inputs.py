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
# A switch whose instructions every task of its owning flows follows declares
# this reference_scope; its references bind whichever skill holds them.
OWNING_FLOWS_SCOPE = "owning_flows"
REFERENCE = re.compile(r"\[[^\]]+\]\((references/[^)#]+)(?:#[^)]*)?\)")
SWITCH_REFERENCE = re.compile(r"^switch-([a-z][a-z0-9_]*)-([a-z][a-z0-9_]*)\.md$")
CATALOG_NAME_MAPS = ("role_skills", "required_role_skills", "entries",
                     "required_references", "stack_reference_by_role", "read_only_entry_roles")
CANONICAL_SUFFIXES = {".md", ".json"}
OPAQUE_NAMES = {"artifacts", ".obsidian", ".trash"}
WIKILINK = re.compile(r"\[\[([^\]|#]+)")


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
    return identity(root, source_paths(root))


def source_paths(root: Path) -> list[str]:
    """The canonical source inventory's membership, without reading content."""
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
    return sorted(paths)


def cited_sources(root: Path, paths) -> set[str]:
    """The canonical notes that the given Markdown inputs cite by wikilink."""
    cited = set()
    for relative in sorted(paths):
        if not relative.endswith(".md"):
            continue
        for target in WIKILINK.findall(regular(root, relative).read_text(encoding="utf-8")):
            target = target.strip()
            name = "workspace/docs/" + target + ("" if target.endswith((".md", ".json")) else ".md")
            candidate = root / name
            if (canonical_source(name) and ".." not in PurePosixPath(name).parts
                    and candidate.is_file() and not candidate.is_symlink()):
                cited.add(name)
    return cited


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
    agents = {path.stem for path in (package / "agents").glob("*.md")}
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
        # A renamed entry may keep its switch references in the skill folder it
        # shared before the rename; its tasks bind them without that SKILL.md.
        shared = entry.get("reference_skills", [])
        if (not isinstance(shared, list) or not all(isinstance(skill, str) for skill in shared)
                or set(shared) - skills):
            raise ValueError("task input entry reference skills must be installed skills")
        if type(entry.get("allow_unborn_head", False)) is not bool:
            raise ValueError("task input unborn HEAD policy must be boolean")
        scope = entry.get("write_scope", {})
        if (scope.get("resolver") not in {"unresolved"}
                or not isinstance(scope.get("roles"), list)
                or set(scope["roles"]) - set(entry["roles"])
                or not isinstance(entry.get("next_transition"), str) or not entry["next_transition"].strip()):
            raise ValueError("task input entry needs a bounded write scope and next transition")
    for skill, references in policy["required_references"].items():
        if skill not in skills:
            raise ValueError("task input policy names an unknown skill")
        for reference in references:
            regular(package, f"skill-content/{skill}/{reference}")
    return policy


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


def switch_choices(project: Path | None, route: dict, package: Path) -> tuple[set, list[str]]:
    """Return the non-default switch values a task runs under and its policy input.

    Without a Process Policy both are empty, so the manifest is unchanged.
    """
    if project is None or not route["project_state"]:
        return set(), []
    import process_policy
    docs = project / "workspace" / "docs"
    if not process_policy.path_for(docs).exists():
        return set(), []
    values, _snapshot = process_policy.effective_values(docs, package)
    return ({(switch, value["value"]) for switch, value in values.items()
             if value["value"] != value["default"]}, ["workspace/docs/" + process_policy.RELATIVE])


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


def write_scope(read_only: bool) -> dict:
    """Describe selected authoring bounds without minting writer authority.

    Read dependencies are not write targets; an owning compiler resolves any
    target before writing.
    """
    return {"status": "read_only" if read_only else "unresolved", "allowed_write_area": [],
            "source_records": [], "writer_authority": False,
            "constraints": ["existing owner/compiler approval remains required",
                            "compiler-owned fields and generated projections are excluded"],
            "reason": "role or task mode is read-only" if read_only else "owning compiler has not resolved a supported write scope"}


def committed(project: Path, relative: str) -> bool:
    """Whether a tracked path matches HEAD in the index and byte for byte in the worktree."""
    command = ["git", "-C", str(project)]
    staged = subprocess.run([*command, "diff", "--quiet", "--cached", "HEAD", "--", relative], capture_output=True)
    # Git's clean filter (autocrlf) hides worktree bytes that differ from HEAD, so compare the bytes.
    blob = subprocess.run([*command, "cat-file", "blob", f"HEAD:{relative}"], capture_output=True)
    return (staged.returncode == 0 and blob.returncode == 0
            and blob.stdout == (project / relative).read_bytes())


def task_skills(policy: dict, entry: str, role: str | None, skills: list[str] | None,
                route: dict) -> set[str]:
    """Return the skills a task reads: its entry, the selected methods and its role's required ones."""
    selected_skills = {entry, *(skills or [])}
    if role:
        selected_skills.update(policy["required_role_skills"][role])
    return selected_skills

def read_only_task(policy: dict, entry: str, role: str | None, mode: str) -> bool:
    """Whether a task of this role and mode writes nothing."""
    return (role in policy["read_only_roles"]
            or role in policy["read_only_entry_roles"].get(entry, [])
            or mode in {"review", "consume"})

def instruction_reads(policy: dict, package: Path, route: dict, role: str | None,
                      selected_skills: set[str], chosen: set) -> tuple[dict, set, set[str], dict, set[str]]:
    """Return the package instructions a task binds under the chosen switch values.

    The result is the switch registry, the chosen values of the switches the
    task's flows own, the required reads, the conditional reads and every
    instruction input whose identity the task binds.
    """
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
    for skill in sorted(selected_skills | set(route.get("reference_skills", []))):
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
        package / "skill-content" / skill
        for skill in selected_skills | set(route.get("reference_skills", [])))]
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
    return registry, chosen, required, references, instruction_inputs


def manifest(*, entry: str, role: str | None, mode: str, project: Path | None = None,
             inputs: list[str] | None = None, skills: list[str] | None = None,
             findings: str | None = None, base: str | None = None,
             expected_hash: str | None = None, package: Path = PACKAGE) -> dict:
    policy = catalog(package)
    package = package.resolve()
    project = project.resolve() if project is not None else None
    if entry not in policy["entries"] or mode not in policy["modes"]:
        raise ValueError("unknown entry or task mode")
    route = policy["entries"][entry]
    if role is not None and role not in route["roles"]:
        raise ValueError("role does not belong to the selected entry")
    if not route["project_state"] and (project is not None or inputs or findings or base):
        raise ValueError("external entry uses conversation inputs only; it creates no project manifest")
    internal = {skill for bound in policy["role_skills"].values() for skill in bound}
    if set(skills or []) - internal:
        raise ValueError("selected method skills must be installed internal skills")
    if not route["project_state"] and skills:
        raise ValueError("external entry does not select project method skills")
    selected_skills = task_skills(policy, entry, role, skills, route)
    try:
        chosen, policy_inputs = switch_choices(project, route, package)
    except ValueError as exc:
        raise ValueError(f"process policy cannot bind switch instructions: {exc}") from exc
    registry, chosen, required, references, instruction_inputs = instruction_reads(
        policy, package, route, role, selected_skills, chosen)
    instruction_files = identity(package, instruction_inputs)
    project_files = set(inputs or []) | set(policy_inputs)
    if findings:
        project_files.add(findings)
    read_only = read_only_task(policy, entry, role, mode)
    if project is None and project_files:
        raise ValueError("project root is required for project inputs")
    read_set = None
    # A task given explicit inputs, outside a code review base,
    # binds those inputs and the notes they cite by content, and the rest of
    # the canonical inventory by membership only, so a new or removed source
    # still invalidates it while a write to an unrelated note or a commit
    # leaves it fresh.
    input_scoped = bool(inputs) and base is None and project is not None
    if input_scoped:
        read_set = frozenset(project_files | cited_sources(project, project_files))
    records = identity(project, project_files) if project is not None else []
    inventory = source_inventory(project, read_set) if project is not None else []
    membership = source_paths(project) if input_scoped else None
    method_bindings = {}
    technology = set(skills or []) & set(policy["technology_method_skills"])
    if project is not None and technology:
        from vault_markdown import parse_frontmatter
        for relative in sorted(project_files):
            if not relative.startswith("workspace/docs/solution-design/decisions/") or not relative.endswith(".md"):
                continue
            path = regular(project, relative)
            props, _end, error = parse_frontmatter(path.read_text(encoding="utf-8"))
            bound = props.get("method_skills", [])
            if not error and props.get("status") == "accepted" and isinstance(bound, list):
                selected = technology & {value for value in bound if isinstance(value, str)}
                if selected and committed(project, relative):
                    for skill in selected:
                        method_bindings.setdefault(skill, []).append(relative)
        if technology - set(method_bindings):
            raise ValueError("technology methods require selected committed accepted Solution decision inputs")
    scope = write_scope(read_only)
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
        if (records != identity(project, project_files) or inventory != source_inventory(project, read_set)
                or (input_scoped and membership != source_paths(project))):
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
              "changed_paths": changes, "open_findings": findings,
              "write_boundary": "read_only" if read_only else "named_owner_only",
              "write_scope": scope, "next_transition_conditions": transitions,
              "barrier": policy["barrier"], "repair_policy": policy["repair_policy"],
              "output_contract": policy["output_contract"], "approval_authority": False,
              "available_method_skills": policy["role_skills"].get(role, []),
              "selected_method_skills": sorted(skills or [])}
    if input_scoped:
        result["context_membership"] = {"count": len(membership), "source_hash": digest(membership)}
    hashed = result
    if input_scoped:
        hashed = {key: value for key, value in result.items() if key != "head"}
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
    parser.add_argument("--expected-hash")
    args = parser.parse_args(argv)
    try:
        result = ({"ok": True, "entries": sorted(catalog()["entries"])} if args.check_catalog else
                  manifest(entry=args.entry, role=args.role, mode=args.mode, project=args.project_root,
                           inputs=args.input, skills=args.skill, findings=args.findings, base=args.base,
                           expected_hash=args.expected_hash))
        if args.project_root and result.get("write_boundary") == "read_only":
            from reader_manifest import compact
            result = compact(args.project_root, result)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
