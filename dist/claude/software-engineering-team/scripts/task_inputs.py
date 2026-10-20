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
# At this value a reader reads the change's impact closure, not the whole package.
CLOSURE_SWITCH, CLOSURE_VALUE = "review_scope", "impact_closure"
CLOSURE_KEYS = ("changed", "closure", "proven_unchanged", "widened_by", "graph_gaps")
DOCS_PREFIX = "workspace/docs/"
# At this value a task binds the role's derived rule digest instead of its required reads.
PACK_SWITCH, PACK_VALUE = "context_pack", "role_digest"
PACK_CHECK = ("Run context_pack.py check --pack <the bound context_pack> before keeping the"
              " result; a stale pack refuses it. A case the pack does not cover: read the named"
              " source in full and record the read and its reason.")
# context_pack.build derives its sources from this module's manifest; that
# inner derivation binds no pack.
_PACK_BUILDS = []
HUNK = re.compile(rb"^@@ -[0-9]+(?:,[0-9]+)? \+([0-9]+)(?:,([0-9]+))? @@", re.M)
VIEWS_FIRST = ("Start with project_reading, then consult vault_views and typed relations as needed. "
               "If the context is insufficient or unavailable, use targeted manual discovery "
               "and report the extra sources and reasons; preserve the owning review scope.")
CLOSURE_READS = ("Read in full only impact_closure.closure, its graph_gaps and the project"
                 " inputs; a proven_unchanged note is its hash-bound entry, not a read. Any"
                 " note or file may still be read beyond the closure: record each such read"
                 " with its reason in beyond_closure (impact_closure.record_beyond) in the"
                 " output.")
DELTA_READS = ("Confirm each bound finding against impact_closure.fixed, the fixed lines of"
               " the notes the fix changed, and impact_closure.touches, what the fix reaches;"
               " never re-audit unchanged text. Record any read beyond them with its reason"
               " in beyond_closure.")


def pack_api():
    """Return the context pack module, which derives the role digest."""
    try:
        import context_pack
    except ImportError as exc:
        raise ValueError(f"{PACK_SWITCH} {PACK_VALUE} needs scripts/context_pack.py") from exc
    return context_pack


def pack_full_read(path: str, role: str | None) -> bool:
    """Whether a required read stays a full read beside the role digest."""
    return (path == "constitution.md" or (role is not None and path == f"agents/{role}.md")
            or "/references/switch-" in path)


def role_pack(entry: str, role: str | None, mode: str, project: Path | None, package: Path,
              expected: bool) -> dict:
    """Build the role digest, and check it when the result is about to be kept."""
    api = pack_api()
    _PACK_BUILDS.append(True)
    try:
        pack = api.build(entry=entry, role=role, mode=mode, project=project, package=package)
        if expected:
            api.check(pack, project=project, package=package)
    except api.Refused as exc:
        raise ValueError(f"context pack refused: {exc}") from exc
    finally:
        _PACK_BUILDS.pop()
    return pack


def closure_api():
    """Return the impact closure module, which owns the relation graph."""
    try:
        import impact_closure
    except ImportError as exc:
        raise ValueError(f"{CLOSURE_SWITCH} {CLOSURE_VALUE} needs scripts/impact_closure.py") from exc
    return impact_closure


def docs_relative(path) -> str:
    if not isinstance(path, str) or not path:
        raise ValueError(f"impact closure returned an invalid note path: {path!r}")
    return path.removeprefix(DOCS_PREFIX)


def row_path(row) -> str:
    """A closure row is a note path, or a gap row that names its note under ``path``."""
    return row.get("path") if isinstance(row, dict) else row


def closure_raw(api, docs: Path, present: list, earlier: dict, overrides: dict | None = None) -> dict:
    """``api.closure`` over the verified vault index when this Python has SQLite FTS5.

    The result equals the reparse ``api.closure`` computes. A closure module that
    provides only the documented ``closure`` call, a vault that is not a project's
    own ``workspace/docs`` (which has no checkout index), a project without a
    published index bound to it and a Python without SQLite FTS5 use that reparse.
    """
    def reparse():
        options = {"policy": overrides} if overrides else {}
        return api.closure(docs, present, deleted=earlier, **options) if earlier \
            else api.closure(docs, present, **options)
    spelled = Path(docs).absolute()
    if not hasattr(api, "closure_from") or spelled.name != "docs" or spelled.parent.name != "workspace":
        return reparse()
    import vault_index
    import vault_query
    try:
        vault_index.capabilities()
        root = vault_query.project_docs(spelled.parents[1])
        cache = vault_query.default_cache(root)
    except ValueError:
        return reparse()
    # Building an index costs more than one reparse, so only an existing bound cache is used.
    if not vault_index.published_binding_matches(root, cache, vault_query.builder_hash()):
        return reparse()
    import project_context
    import vault_check

    def run(index):
        snap = vault_query.Index(index).snapshot()
        policy = api.closure_policy(vault_check.effective_policy(
            vault_check.load_policy(vault_check.DEFAULT_POLICY), docs), overrides)
        result = api.closure_from(snap, api.changed_paths(docs, present, snap["notes"]), policy)
        if earlier:
            relations = api.earlier_relations(docs, earlier, vault=index.store.vault(),
                                              owners=index.store.catalog.owner_lookup)
            seeds = sorted({target for targets in relations.values() for target in targets})
            if seeds:
                more = api.closure_from(snap, sorted({*result["changed"], *seeds}), policy)
                more["changed"] = result["changed"]
                result = more
            result["deleted"] = sorted(earlier)
        return result
    return project_context.with_index(root.parents[1], run)


def impact_closure(docs: Path, changed, prefix: str = "", deleted: dict | None = None) -> dict:
    """Return the closure of ``changed`` docs notes with every path under ``prefix``.

    Each proven unchanged note also carries the sha256 of its current bytes,
    so a later write to it stales the manifest that lists it unread.
    """
    named = sorted({docs_relative(path) for path in changed})
    if any(".." in path.split("/") or path.startswith("/") for path in named):
        raise ValueError("a changed path must stay inside workspace/docs")
    # A deleted note is still a change; the closure starts from what exists.
    missing = [path for path in named if not (docs / path).is_file()]
    earlier = {docs_relative(path): text for path, text in (deleted or {}).items()}
    api = closure_api()
    present = [path for path in named if path not in missing]
    raw = closure_raw(api, docs, present, earlier)
    if not isinstance(raw, dict) or any(not isinstance(raw.get(key), list) for key in CLOSURE_KEYS):
        raise ValueError(f"impact closure must return the lists {', '.join(CLOSURE_KEYS)}")
    raw = dict(raw, changed=[*raw["changed"], *missing])
    members = set(raw["closure"]) | set(raw["changed"])
    raw["graph_gaps"] = [row for row in raw["graph_gaps"] if not isinstance(row, dict)
                         or row.get("reason") not in {"no_typed_relations", "text_only_relation"}
                         or any(row.get(key) in members for key in ("path", "source", "target"))]
    result = {key: sorted({prefix + docs_relative(row_path(row)) for row in raw[key]})
              for key in ("changed", "closure", "graph_gaps")}
    result["widened_by"] = sorted(
        ({"path": prefix + docs_relative(row["path"]), "reason": str(row.get("reason")),
          "citers": sorted({prefix + docs_relative(path) for path in row.get("citers", [])})}
         if isinstance(row, dict) else {"reason": str(row)} for row in raw["widened_by"]),
        key=lambda row: json.dumps(row, sort_keys=True))
    proven = {}
    for row in raw["proven_unchanged"]:
        if not isinstance(row, dict) or not isinstance(row.get("approval_hash"), str):
            raise ValueError("impact closure proven_unchanged rows need a path and approval_hash")
        path = docs_relative(row.get("path"))
        source = docs / path
        proven[prefix + path] = {
            "path": prefix + path, "approval_hash": row["approval_hash"],
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else None}
    result["proven_unchanged"] = [proven[path] for path in sorted(proven)]
    result["beyond_closure"] = []
    return result


def vault_views(docs: Path) -> dict:
    """Return the relation views every role consults first."""
    views = closure_api().vault_views(docs)
    if not isinstance(views, dict):
        raise ValueError("impact closure vault_views must return a mapping")
    return views


def approval_event(props: dict, previous: dict, anchor: dict) -> bool:
    """An approval is a changed receipt, not a render of unchanged approval fields."""
    value = props.get(anchor["field"])
    approved = bool(value) if anchor["value"] == "*" else str(value) == anchor["value"]
    fields = (anchor["field"], "revision", "source_hash", "package_hash", "approved_at_utc",
              "package_approved_at_utc", "approval_revision", "scope_hash", "plan_hash")
    return approved and any(props.get(key) != previous.get(key) for key in fields)


def approval_base(project: Path, scope_kind: str | None, package: Path = PACKAGE,
                  inputs=()) -> str | None:
    """Oldest required approval event, scoped to the selected packages.

    A later navigation/render commit retaining the same receipt is not a new
    approval. Missing history never authorizes a narrower read.
    """
    try:
        policy = json.loads(regular(package, POLICY).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    definitions = (policy.get("approval_anchors") or {}).get((scope_kind or "").replace("-", "_"), [])
    selected = [PurePosixPath(path) for path in inputs]
    anchors = []
    for definition in definitions:
        pattern = definition["path"]
        if pattern.startswith("/") or ".." in PurePosixPath(pattern).parts:
            raise ValueError("approval anchors must stay inside the project")
        for path in sorted(project.glob(pattern)):
            relative = PurePosixPath(path.relative_to(project).as_posix())
            scope = definition.get("scope")
            if scope and selected:
                if scope == "file" and relative not in selected:
                    continue
                if scope == "package" and not any(relative.parent == p.parent or
                        relative.parent in p.parents for p in selected):
                    continue
            regular(project, relative.as_posix())
            anchors.append(dict(definition, path=relative.as_posix()))
    if not anchors:
        return None
    command = ["git", "--no-replace-objects", "-C", str(project)]

    def properties(commit, path):
        shown = subprocess.run([*command, "show", f"{commit}:{path}"], capture_output=True)
        return {} if shown.returncode else frontmatter_props(shown.stdout.decode("utf-8", "replace"))

    candidates = []
    for anchor in anchors:
        log = subprocess.run([*command, "log", "--first-parent", "--format=%H", "--",
                              anchor["path"]], capture_output=True)
        if log.returncode:
            return None
        found = None
        for commit in log.stdout.decode("ascii").split():
            props = properties(commit, anchor["path"])
            value = props.get(anchor["field"])
            approved = bool(value) if anchor["value"] == "*" else str(value) == anchor["value"]
            if not approved:
                continue
            previous = properties(commit + "^", anchor["path"])
            if approval_event(props, previous, anchor):
                found = commit
                break
        if found is None:
            return None
        candidates.append(found)
    history = subprocess.run([*command, "rev-list", "--first-parent", "HEAD"], capture_output=True)
    if history.returncode:
        return None
    positions = {sha: pos for pos, sha in enumerate(history.stdout.decode("ascii").split())}
    return max(candidates, key=lambda sha: positions.get(sha, -1))


def frontmatter_props(text: str) -> dict:
    from vault_markdown import parse_frontmatter
    props = parse_frontmatter(text)[0]
    return props if isinstance(props, dict) else {}


def deleted_texts(project: Path, commit: str | None, paths) -> dict[str, str]:
    """The bytes each deleted canonical note had at ``commit``, by project path."""
    if commit is None:
        return {}
    command = ["git", "--no-replace-objects", "-C", str(project)]
    result = {}
    for path in sorted(paths):
        if path.endswith(".md") and not (project / path).exists():
            shown = subprocess.run([*command, "show", f"{commit}:{path}"], capture_output=True)
            if not shown.returncode:
                result[path] = shown.stdout.decode("utf-8", "replace")
    return result


def impact_changes(command: list[str], base: str | None, bound) -> tuple[set[str], str | None]:
    """Return the canonical notes changed since ``base``, or since HEAD, and the base commit.

    A task given inputs and no base sees only the changes among its inputs.
    """
    reference = base or "HEAD"
    if reference.startswith("-"):
        raise ValueError("task base must not be a Git option")
    resolved = subprocess.run([*command, "rev-parse", "--verify", reference + "^{commit}"],
                              capture_output=True)
    if resolved.returncode:
        if base is not None:
            raise ValueError("task base does not resolve to an exact commit")
        return set(), None
    commit = resolved.stdout.decode("ascii").strip()
    names = (git_bytes(command, "diff", "--no-ext-diff", "--no-textconv", "--name-only",
                       "--no-renames", "-z", commit, "--")
             + git_bytes(command, "ls-files", "--others", "--exclude-standard", "-z"))
    found = {os.fsdecode(raw) for raw in names.split(b"\0") if raw}
    return ({path for path in found if canonical_source(path)
             and (bound is None or path in bound)}, commit)


def fixed_lines(project: Path, command: list[str], base: str, paths) -> list[dict]:
    """Return each changed note with the line ranges its fix wrote, in its current text.

    A range of a pure deletion marks the line the removed text stood before.
    """
    rows = []
    for path in sorted(paths):
        target = project / path
        if not target.is_file():
            rows.append({"path": path, "deleted": True, "lines": []})
            continue
        tracked = subprocess.run([*command, "cat-file", "-e", f"{base}:{path}"], capture_output=True)
        if tracked.returncode:
            count = len(target.read_bytes().splitlines())
            rows.append({"path": path, "lines": [[1, max(count, 1)]]})
            continue
        diff = git_bytes(command, "diff", "--no-ext-diff", "--no-textconv", "-U0", base, "--", path)
        ranges = []
        for match in HUNK.finditer(diff):
            start, length = int(match.group(1)), int(match.group(2) or b"1")
            ranges.append([max(start, 1), max(start + length - 1, start, 1)])
        rows.append({"path": path, "lines": ranges})
    return rows


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
    if relative.startswith(".agentrof/agent-marketplace/.runtime/vault-index/"):
        return True  # Disposable navigation bytes never identify project work.
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


def closure_reads(project: Path, project_files: set[str], inputs: set[str], base: str | None,
                  findings: str | None, named: list[str],
                  approved: str | None = None) -> tuple[set[str], dict]:
    """Narrow a reader's project reads to the impact closure of the change.

    The change is every canonical note Git sees changed since ``base``, or
    since HEAD among the task's inputs, plus the notes ``named``. A reader
    reads the closure, its graph gaps and every input the closure cannot
    prove unchanged; a proven unchanged input is listed with its hashes
    instead. Without a change, or before the first commit, it reads all its
    inputs. A re-check, given findings and the reviewed commit as ``base``,
    reads only the notes the fix changed, with their fixed lines, and what the
    fix touches; every other input is unchanged since the reviewed commit.
    """
    command = ["git", "--no-replace-objects", "-C", str(project)]
    for path in named:
        if ".." in path.split("/") or not canonical_source(path) or not path.endswith(".md"):
            raise ValueError(f"--changed must name a workspace/docs note: {path}")
    if approved is None and base is None:
        return project_files, {"read": "full", "reason": "no proven approval baseline",
                               "changed": [], "beyond_closure": []}
    if base is None and approved is not None:
        # Every vault file changed since the package's last approved revision
        # starts the closure, never only the notes that lost a stamp.
        seen, commit = impact_changes(command, approved, None)
    else:
        seen, commit = impact_changes(command, base, None if base else frozenset(inputs) or None)
    changed = seen | set(named)
    if not changed:
        return project_files, {"read": "full", "reason": "no changed note: the inputs are read in full",
                               "changed": [], "beyond_closure": []}
    scope = impact_closure(project / "workspace/docs", changed, DOCS_PREFIX,
                           deleted_texts(project, commit, changed))
    if approved is not None and base is None:
        scope["approved_base"] = approved
    recheck = findings is not None and base is not None
    reach = set(scope["closure"]) | set(scope["graph_gaps"]) | changed
    reads = {path for path in reach if (project / path).is_file()}
    proven = {row["path"] for row in scope["proven_unchanged"]}
    unread = {path for path in inputs if canonical_source(path) and path not in reads
              and (recheck or path in proven)}
    scope["proven_unchanged"] = [row for row in scope["proven_unchanged"] if row["path"] not in reads]
    scope["read"] = "closure"
    if recheck:
        scope.update(read="delta", base=commit,
                     fixed=fixed_lines(project, command, commit, sorted(changed)),
                     touches=sorted(reads - changed))
        listed = proven | {row["path"] for row in scope["fixed"]}
        scope["unchanged_since_base"] = [
            {"path": path, "sha256": hashlib.sha256((project / path).read_bytes()).hexdigest()}
            for path in sorted(unread - listed)]
    return (project_files - unread) | reads, scope


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
    if route["project_state"]:
        selected_skills.add("obsidian-vault")
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
             expected_hash: str | None = None, package: Path = PACKAGE,
             changed: list[str] | None = None, context_plan: str | None = None) -> dict:
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
    impact = project is not None and (CLOSURE_SWITCH, CLOSURE_VALUE) in chosen
    digest_pack = (PACK_SWITCH, PACK_VALUE) in chosen and not _PACK_BUILDS
    if changed and not impact:
        raise ValueError(f"--changed names the change set of {CLOSURE_SWITCH} {CLOSURE_VALUE}")
    instruction_files = identity(package, instruction_inputs)
    project_files = set(inputs or []) | set(policy_inputs)
    project_reading = None
    if context_plan:
        if project is None or not route["project_state"]:
            raise ValueError("a context plan requires a project task")
        import project_context
        project_reading = json.loads(regular(project, context_plan).read_text(encoding="utf-8"))
        if (project_reading["request"]["entry"], project_reading["request"]["role"]) != (entry, role):
            raise ValueError("context plan belongs to a different entry or role")
        project_context.with_index(project, lambda index: project_context.validate_plan(project, index, project_reading))
        project_files.add(context_plan)
        project_files.update(row["path"] if row.get("source_root") == "project" else
                             "workspace/docs/" + row["path"] for row in project_reading["must_read"])
    if findings:
        project_files.add(findings)
    read_only = read_only_task(policy, entry, role, mode)
    if project is None and project_files:
        raise ValueError("project root is required for project inputs")
    scoped = None
    if impact and read_only:
        project_files, scoped = closure_reads(
            project, project_files, set(inputs or []), base, findings, changed or [],
            approval_base(project, route.get("scope_kind"), package, inputs or []))
    if project is not None and route["project_state"] and project_reading is None:
        import project_context
        seeds = set(project_files) if scoped is not None else set(inputs or []) or set(project_files)
        project_reading = project_context.task_context(project, entry=entry, role=role,
                                                       mode=mode, paths=seeds, change_base=base)
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
    if project_reading is not None:
        transitions.insert(0, {"condition": "project_context_first", "status": "required",
            "detail": "Start with project_reading and batch-read its units with project_context.py read --plan <task manifest>. "
                      "Expand incomplete plans; if context is insufficient, wrong or unavailable, use manual discovery/read "
                      "on your initiative or parent direction and report sources and reasons. Preserve every owning-flow read obligation."})
    pack = None
    if digest_pack:
        pack = role_pack(entry, role, mode, project if route["project_state"] else None,
                         package, expected_hash is not None)
        transitions.insert(0, {"condition": "context_pack_check", "status": "required",
                               "detail": PACK_CHECK})
    if impact:
        transitions.insert(0, {"condition": "vault_views_first", "status": "required",
                               "detail": VIEWS_FIRST})
        if scoped is not None and scoped["read"] != "full":
            transitions.insert(1, {"condition": "impact_closure_reads", "status": "required",
                                   "detail": DELTA_READS if scoped["read"] == "delta" else CLOSURE_READS})
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
    if project_reading is not None:
        result["project_reading"] = project_reading
        result["context_inventory"] = {"count": len(inventory), "source_hash": digest(inventory)}
        result["canonical_source_inventory"] = [row for row in inventory
                                                if row["path"] in project_files | set(read_set or [])]
    if pack is not None:
        # The digest replaces the other required reads; the constitution, the
        # role's own file and every switch reference stay full reads, so no
        # rule without a keyword is lost. Every source stays bound in
        # instructions and one read away.
        result["required_reads"] = sorted(path for path in result["required_reads"]
                                          if pack_full_read(path, role))
        result[PACK_SWITCH] = pack
    if impact:
        # Readers and writers alike start from the relation views.
        result[CLOSURE_SWITCH] = CLOSURE_VALUE
        result["vault_views"] = vault_views(project / "workspace/docs")
        if scoped is not None:
            result[CLOSURE_VALUE] = scoped
    if input_scoped:
        if project_reading is not None:
            result["context_membership"] = {"count": len(membership), "source_hash": digest(membership)}
        else:
            result["canonical_source_paths"] = membership
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
    parser.add_argument("--changed", action="append", default=[],
                        help="a workspace/docs note the change made (review_scope impact_closure);"
                             " the closure starts from these and the notes Git sees changed")
    parser.add_argument("--context-plan", help="project-relative path to a source-bound project reading plan")
    args = parser.parse_args(argv)
    try:
        result = ({"ok": True, "entries": sorted(catalog()["entries"])} if args.check_catalog else
                  manifest(entry=args.entry, role=args.role, mode=args.mode, project=args.project_root,
                           inputs=args.input, skills=args.skill, findings=args.findings, base=args.base,
                           expected_hash=args.expected_hash, changed=args.changed, context_plan=args.context_plan))
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
