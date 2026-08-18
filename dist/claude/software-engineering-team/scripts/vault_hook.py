#!/usr/bin/env python3
"""Per-write vault consistency hook for the workspace docs tree.

Two moments, one law (the obsidian-vault skill):

- pre (PreToolUse, Write|Edit): content-shape denials BEFORE the write
  lands. A vault-internal relative markdown link, an unescaped alias pipe
  in a table-row wikilink, or an inline flow-list tags:/aliases: value
  never reaches disk, regardless of which agent writes. Content-only
  regexes, zero vault I/O. The one exception is workspace/config.json:
  team-owned keys have subprocess writers this hook never sees, so any
  tool-level change to them is denied, which needs one disk read.
- post (PostToolUse, Write|Edit): after a write lands under the vault,
  run vault_check's --changed fast path and surface its findings to the
  writing session immediately, so link and metadata duties are repaired
  in-session instead of at a distant gate. Gates stay the hard barrier.
- register (SessionStart): perform no global registration. The hook runs from
  its installed team package and all mutable inventory remains under the
  current project's ignored `.agentrof/` runtime.

The normalize shim gives both moments one payload shape (canonical tool
name, per-file write targets). Bash pre/post snapshots guard both vault
inventory and the machine-managed projection of workspace/config.json.
The inventory stays in the project runtime. A private, short-lived recovery
capsule outside the command's project tree lets post restore the config even
when that command removes its project-local snapshot. Stdlib only.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path

import vault_check
try:
    import experience_compile
except ImportError:  # source-tree tests may load only the shared overlay
    experience_compile = None

VAULT_SEGMENTS = ("workspace", "docs")

# Tool-name vocabulary -> the canonical pair this hook reasons in.
TOOL_NAME_CANON = {
    "Write": "Write",
    "Edit": "Edit", "MultiEdit": "Edit",
    "apply_patch": "Edit",
}

PATCH_HEADER_RE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$")

# Team-owned config keys have one of two sanctioned subprocess writers:
# project_config.py for ordinary fields and reconcile-designations for display
# wording. Neither traverses PreToolUse, so direct Write/Edit changes are denied.
CONFIG_GUARD_KEYS = (
    "team_id", "scale", "output_language",
    "terminology_language", "backend_stack", "frontend_stack",
    "environment_stack", "databases", "test_command", "mutation_command",
    "env_command", "source_dirs", "max_parallel", "limits",
    "doc_type_designations", "doc_type_designation_history",
)

CONFIG_GUARD_MESSAGE = (
    "team-owned workspace config fields are machine-managed; their writers are"
    " setup, project_config.py and vault_check.py reconcile-designations."
    " Direct edits desynchronize the workspace config.")

SHELL_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
PYTHON_COMMAND_RE = re.compile(r"^python(?:3(?:[.][0-9]+)?)?$")
SANCTIONED_PROJECT_CONFIG_COMMANDS = {"set", "unset"}
SANCTIONED_SHELL_ASSIGNMENTS = {"PYTHONDONTWRITEBYTECODE": "1"}
RECOVERY_TTL_SECONDS = 24 * 60 * 60

# A relative markdown link that is not http(s)/mailto/anchor/root form.
MD_LINK_RE = re.compile(
    r"(?<!\!)\[[^\]]*\]\((?!https?://|mailto:|#|/)([^)\s]+?)"
    r"(?:\s+\"[^\"]*\")?\)")
INLINE_FLOW_LIST_RE = re.compile(r"^\s*(tags|aliases):\s*\[", re.MULTILINE)
FENCE_RE = re.compile(r"^\s*```")
EXPERIENCE_MACHINE_FIELD_RE = re.compile(
    r"(?m)^\s*(approved_at_utc|approval_revision|revision|registry_hash|"
    r"source_hash|stamped_at):")
DESIGN_SYSTEM_MACHINE_FIELD_RE = re.compile(
    r"(?m)^\s*(status|revision|approved_at_utc|baseline_hash|"
    r"supersedes_hash):")
BACKLOG_MACHINE_FIELD_RE = re.compile(
    r"(?m)^\s*(approved_at_utc|source_hash|package_hash|approval_hash):")
BACKLOG_APPROVED_STATE_RE = re.compile(
    r"(?m)^\s*(?:status:\s*[\"']?approved[\"']?\s*$|"
    r"-\s*[\"']?status/approved[\"']?\s*$)"
)
EXPERIENCE_PATHS = (
    re.compile(r"^experience-design/experience\.md$"),
    re.compile(r"^experience-design/programs/prg-[0-9]+/program\.md$"),
    re.compile(r"^experience-design/programs/prg-[0-9]+/releases/rel-[0-9]+/release\.md$"),
    re.compile(r"^experience-design/programs/prg-[0-9]+/releases/rel-[0-9]+/spaces/[a-z0-9-]+/space\.md$"),
    re.compile(r"^experience-design/programs/prg-[0-9]+/releases/rel-[0-9]+/spaces/[a-z0-9-]+/(?:domains/[a-z0-9-]+/)+domain\.md$"),
    re.compile(r"^experience-design/programs/prg-[0-9]+/releases/rel-[0-9]+/(?:spaces/[a-z0-9-]+/(?:domains/[a-z0-9-]+/)*)?(?:journeys/[a-z0-9-]+-journey|screens/[a-z0-9-]+-screen|flows/[a-z0-9-]+-flows|artifacts/[a-z0-9-]+-artifact)\.md$"),
)


def note_status(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    if not lines or lines[0].strip() != "---":
        return ""
    for raw in lines[1:]:
        value = raw.strip()
        if value == "---":
            break
        if value.startswith("status:"):
            return value.split(":", 1)[1].strip().strip("\"'")
    return ""


def approved_experience_owner(path: Path) -> Path | None:
    for parent in (path.parent, *path.parents):
        release = parent / "release.md"
        if release.is_file() and note_status(release) == "approved":
            return release
        program = parent / "program.md"
        if program.is_file() and note_status(program) == "approved":
            return program
        if parent.name == "docs":
            break
    return None


def read_payload() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def parse_apply_patch(patch: str) -> list[dict]:
    lines = patch.splitlines()
    if not lines or lines[0].strip() != "*** Begin Patch" \
            or lines[-1].strip() != "*** End Patch":
        raise ValueError("missing apply_patch boundary")
    targets: list[dict] = []
    index = 1
    while index < len(lines) - 1:
        if not lines[index].strip():
            index += 1
            continue
        match = PATCH_HEADER_RE.match(lines[index])
        if match is None:
            raise ValueError(f"unexpected apply_patch line {index + 1}")
        operation, file_path = match.groups()
        file_path = file_path.strip()
        if not file_path:
            raise ValueError("empty apply_patch path")
        index += 1
        body: list[str] = []
        move_to = ""
        while index < len(lines) - 1 and PATCH_HEADER_RE.match(lines[index]) is None:
            if lines[index].startswith("*** Move to: "):
                candidate = lines[index][len("*** Move to: "):].strip()
                if operation != "Update":
                    raise ValueError("apply_patch move is valid only for Update File")
                if not candidate:
                    raise ValueError("empty apply_patch move target")
                if move_to:
                    raise ValueError("duplicate apply_patch move target")
                move_to = candidate
            else:
                body.append(lines[index])
            index += 1
        added = "\n".join(line[1:] for line in body if line.startswith("+"))
        removed = "\n".join(line[1:] for line in body if line.startswith("-"))
        target = {"file_path": file_path, "operation": operation.lower()}
        target["patch_body"] = body
        if move_to:
            target["move_to"] = move_to
        if operation == "Add":
            target["content"] = added
        elif operation == "Delete":
            target["old_string"] = removed
        else:
            target["new_string"] = added
            target["old_string"] = removed
        targets.append(target)
        if move_to:
            targets.append({
                "file_path": move_to,
                "operation": "move-target",
                "new_string": added,
                "old_string": removed,
            })
    if not targets:
        raise ValueError("apply_patch contains no file operations")
    return targets


def normalize(payload: dict) -> dict:
    """One canonical payload shape: tool_name mapped into Write/Edit,
    the write target expanded under 'file_targets'."""
    out = dict(payload) if isinstance(payload, dict) else {}
    raw_input = out.get("tool_input")
    tool_input = dict(raw_input) if isinstance(raw_input, dict) else {}
    raw_tool = str(out.get("tool_name", ""))
    tool = TOOL_NAME_CANON.get(raw_tool, raw_tool)
    targets: list[dict] = []
    out["raw_tool_name"] = raw_tool
    if raw_tool == "apply_patch":
        patch = ""
        if isinstance(raw_input, str):
            patch = raw_input
        else:
            for key in ("patch", "input", "text"):
                value = tool_input.get(key)
                if isinstance(value, str) and value.strip():
                    patch = value
                    break
        try:
            targets = parse_apply_patch(patch)
        except ValueError as exc:
            out["patch_parse_error"] = str(exc)
    elif tool in ("Write", "Edit"):
        file_path = str(tool_input.get("file_path", ""))
        if file_path:
            target = {"file_path": file_path}
            for key in ("content", "new_string", "old_string"):
                if key in tool_input:
                    target[key] = str(tool_input.get(key) or "")
            targets = [target]
    out["tool_name"] = tool
    out["tool_input"] = tool_input
    out["file_targets"] = targets
    return out


def register() -> int:
    """SessionStart is informational; no global registration is required."""
    return 0


def deny(message: str) -> int:
    print(f"vault law: {message}", file=sys.stderr)
    return 2


def normalize_posix(path: str) -> str:
    """Collapse . and .. segments in a posix path, platform-independently."""
    parts: list[str] = []
    for segment in path.split("/"):
        if segment in ("", "."):
            continue
        if segment == ".." and parts and parts[-1] != "..":
            parts.pop()
        else:
            parts.append(segment)
    return ("/" if path.startswith("/") else "") + "/".join(parts)


def vault_root(file_path: str) -> Path | None:
    path = Path(file_path)
    for parent in (path.parent, *path.parents):
        if parent.name == "docs" and (parent.parent / "config.json").is_file():
            return parent
    parts = path.as_posix().split("/")
    for i in range(len(parts) - len(VAULT_SEGMENTS)):
        if tuple(parts[i:i + 2]) == VAULT_SEGMENTS:
            return Path("/".join(parts[:i + 2]))
    return None


def vault_relative(file_path: str) -> str | None:
    """Vault-relative path under the configured workspace docs tree."""
    root = vault_root(file_path)
    if root is None:
        return None
    try:
        inner = Path(file_path).relative_to(root).as_posix()
    except ValueError:
        parts = Path(file_path).as_posix().split("/")
        marker = root.as_posix().split("/")
        inner = "/".join(parts[len(marker):])
    return inner or None


def written_content(tool_input: dict) -> str:
    if "content" in tool_input:
        return str(tool_input.get("content") or "")
    return str(tool_input.get("new_string") or "")


def outside_fences(text: str) -> list[tuple[int, str]]:
    lines = []
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append((lineno, vault_check.INLINE_CODE_RE.sub("", line)))
    return lines


def is_workspace_config(file_path: str, written: dict) -> bool:
    path = Path(file_path)
    if path.name != "config.json":
        return False
    candidates = []
    try:
        candidates.append(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        pass
    if "content" in written:
        try:
            candidates.append(json.loads(str(written.get("content") or "")))
        except Exception:
            pass
    managed = {"team_id", *CONFIG_GUARD_KEYS}
    return path.parent.name == "workspace" or any(
        isinstance(value, dict) and managed & set(value) for value in candidates
    )


def guarded_spans(text: str) -> list[tuple[int, int]]:
    """Char spans of the guarded keys' JSON blocks (key name through the
    matching close), string-aware, so an Edit fragment that lands inside
    a guarded block is recognized even when it never names the key."""
    spans: list[tuple[int, int]] = []
    for key in CONFIG_GUARD_KEYS:
        start = text.find(f'"{key}"')
        if start == -1:
            continue
        i = text.find(":", start)
        if i == -1:
            continue
        i += 1
        while i < len(text) and text[i] in " \t\r\n":
            i += 1
        if i >= len(text) or text[i] not in "{[":
            spans.append((start, min(len(text), i + 1)))
            continue
        opener, closer = text[i], {"{": "}", "[": "]"}[text[i]]
        depth = 0
        j = i
        in_str = False
        while j < len(text):
            ch = text[j]
            if in_str:
                if ch == "\\":
                    j += 2
                    continue
                if ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    break
            j += 1
        spans.append((start, min(len(text), j + 1)))
    return spans


def config_guard(tool_input: dict, file_path: str) -> int:
    """Deny any tool-level change to team-owned config keys, mint included.

    Introduction over an absent key is a change. Writes are
    diffed subtree-against-disk (a Write changing only other config keys
    passes); Edits are fragments the final JSON cannot be rebuilt from,
    so any fragment naming a guarded key or landing inside its on-disk
    block is denied conservatively."""
    try:
        disk_text = Path(file_path).read_text(encoding="utf-8")
    except OSError:
        disk_text = None
    if "content" in tool_input:
        try:
            proposed = json.loads(str(tool_input.get("content") or ""))
        except json.JSONDecodeError:
            return deny(
                "workspace/config.json is machine-managed JSON; an"
                " unparseable write would blind every designation check. "
                + CONFIG_GUARD_MESSAGE)
        if not isinstance(proposed, dict):
            return deny("workspace/config.json holds a JSON object. "
                        + CONFIG_GUARD_MESSAGE)
        disk = None
        if disk_text is not None:
            try:
                disk = json.loads(disk_text)
            except json.JSONDecodeError:
                disk = None
        for key in CONFIG_GUARD_KEYS:
            disk_value = disk.get(key) if isinstance(disk, dict) else None
            if proposed.get(key) != disk_value:
                return deny(CONFIG_GUARD_MESSAGE)
        return 0
    old = str(tool_input.get("old_string") or "")
    new = str(tool_input.get("new_string") or "")
    if any(key in old or key in new for key in CONFIG_GUARD_KEYS):
        return deny(CONFIG_GUARD_MESSAGE)
    if disk_text and old:
        spans = guarded_spans(disk_text)
        pos = disk_text.find(old)
        while pos != -1:
            end = pos + len(old)
            if any(pos < s_end and end > s_start
                   for (s_start, s_end) in spans):
                return deny(CONFIG_GUARD_MESSAGE)
            pos = disk_text.find(old, pos + 1)
    return 0


def relation_projection_guard(written: dict, file_path: str) -> int:
    try:
        disk = Path(file_path).read_text(encoding="utf-8")
    except OSError:
        disk = ""
    blocks = (
        (vault_check.RELATION_START, vault_check.RELATION_END,
         "inverse relation"),
        ("## Contents <!-- sec: structural:generated:start -->",
         "<!-- sec: structural:generated:end -->", "structural navigation"),
    )
    if "content" in written:
        proposed = str(written.get("content") or "")
        for start, end, label in blocks:
            pattern = re.compile(
                re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
            before = pattern.search(disk)
            after = pattern.search(proposed)
            if ((before.group(0) if before else "")
                    != (after.group(0) if after else "")):
                return deny(
                    f"machine-owned {label} blocks are written only by the"
                    " owning compiler")
        return 0
    old = str(written.get("old_string") or "")
    new = str(written.get("new_string") or "")
    for start, end, label in blocks:
        if start in old + new or end in old + new:
            return deny(
                f"machine-owned {label} blocks are written only by the"
                " owning compiler")
        if start in disk and end in disk and old:
            block_start = disk.index(start)
            block_end = disk.index(end, block_start) + len(end)
            position = disk.find(old)
            while position != -1:
                if position < block_end and position + len(old) > block_start:
                    return deny(
                        f"the edit overlaps a machine-owned {label} block;"
                        " run the owning renderer")
                position = disk.find(old, position + 1)
    return 0


def pre(payload: dict) -> int:
    if "file_targets" not in payload:
        payload = normalize(payload)
    for written in payload.get("file_targets", []):
        code = pre_target(written)
        if code:
            return code
    if payload.get("raw_tool_name") == "apply_patch":
        return virtual_overlay_check(payload)
    return 0


def apply_patch_body(original: str, body: list[str]) -> str:
    source = original.splitlines()
    trailing = original.endswith("\n")
    hunks: list[list[str]] = []
    current: list[str] = []
    for line in body:
        if line.startswith("@@"):
            if current:
                hunks.append(current)
            current = []
        elif line.startswith("*** Move to: "):
            continue
        else:
            current.append(line)
    if current:
        hunks.append(current)
    cursor = 0
    for hunk in hunks:
        old = [line[1:] for line in hunk
               if line.startswith((" ", "-"))]
        new = [line[1:] for line in hunk
               if line.startswith((" ", "+"))]
        found = next((index for index in range(cursor, len(source) + 1)
                      if source[index:index + len(old)] == old), None)
        if found is None:
            raise ValueError("patch hunk does not match the current file")
        source[found:found + len(old)] = new
        cursor = found + len(new)
    result = "\n".join(source)
    return result + ("\n" if trailing or not original else "")


def virtual_overlay_check(payload: dict) -> int:
    cwd = Path(str(payload.get("cwd") or ".")).resolve()
    groups: dict[Path, list[dict]] = {}
    for target in payload.get("file_targets", []):
        path = Path(str(target.get("file_path", "")))
        path = path if path.is_absolute() else cwd / path
        path = path.resolve()
        root = vault_root(str(path))
        if root is not None:
            groups.setdefault(root.resolve(), []).append({**target,
                                                          "resolved": path})
    for root, targets in groups.items():
        policy = vault_check.effective_policy(
            vault_check.load_policy(vault_check.DEFAULT_POLICY), root)
        baseline = vault_check.build_vault(root, policy)
        baseline_findings = []
        for name in ("wikilink_resolution", "link_policy",
                     "frontmatter_props"):
            vault_check.CHECKS[name](baseline, baseline_findings)
        baseline_keys = {
            (item.path, item.line, item.check, item.message)
            for item in baseline_findings
        }
        with tempfile.TemporaryDirectory(prefix="vault-overlay-") as temporary:
            overlay = Path(temporary) / "docs"
            if root.is_dir():
                shutil.copytree(root, overlay)
            else:
                overlay.mkdir(parents=True)
            touched = set()
            try:
                for target in targets:
                    actual = target["resolved"]
                    rel = actual.relative_to(root)
                    path = overlay / rel
                    touched.add(rel.as_posix())
                    operation = target.get("operation")
                    if operation == "add":
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(str(target.get("content", "")) + "\n",
                                        encoding="utf-8")
                    elif operation == "delete":
                        path.unlink(missing_ok=True)
                    elif operation == "move-target":
                        continue
                    else:
                        original = path.read_text(encoding="utf-8")
                        rendered = apply_patch_body(
                            original, list(target.get("patch_body", [])))
                        move_to = str(target.get("move_to", ""))
                        if move_to:
                            destination = Path(move_to)
                            destination = (destination if destination.is_absolute()
                                           else cwd / destination)
                            dest_rel = destination.relative_to(root)
                            dest_path = overlay / dest_rel
                            dest_path.parent.mkdir(parents=True, exist_ok=True)
                            dest_path.write_text(rendered, encoding="utf-8")
                            path.unlink(missing_ok=True)
                            touched.add(dest_rel.as_posix())
                        else:
                            path.write_text(rendered, encoding="utf-8")
            except (OSError, ValueError) as exc:
                return deny(f"multi-file patch virtual overlay failed: {exc}")
            overlay_policy = vault_check.effective_policy(
                vault_check.load_policy(vault_check.DEFAULT_POLICY), overlay)
            vault = vault_check.build_vault(overlay, overlay_policy)
            findings = []
            for name in ("wikilink_resolution", "link_policy",
                         "frontmatter_props"):
                vault_check.CHECKS[name](vault, findings)
            relevant = [finding for finding in findings
                        if (finding.path, finding.line, finding.check,
                            finding.message) not in baseline_keys]
            if relevant:
                finding = sorted(relevant, key=lambda item: (
                    item.path, item.line, item.check, item.message))[0]
                return deny(
                    "multi-file patch virtual overlay failed "
                    f"[{finding.check}] {finding.path}: {finding.message}")
    return 0


def pre_target(written: dict) -> int:
    file_path = str(written.get("file_path", ""))
    if is_workspace_config(file_path, written):
        try:
            return config_guard(written, file_path)
        except Exception:
            return 0  # a guard never takes the session down
    rel = vault_relative(file_path)
    if rel is None:
        return 0
    if rel.startswith("backlog/_generated/"):
        return deny(
            "backlog/_generated files are compiler-owned; run"
            " backlog_compile.py check --render"
        )
    if rel.startswith(("maps/_relations/", "maps/_navigation/")):
        return deny(
            "inverse relation catalogs are compiler-owned; run"
            " vault_check.py render-relations")
    if rel.endswith(".md"):
        relation_code = relation_projection_guard(written, file_path)
        if relation_code:
            return relation_code
    if rel.startswith("design-system/"):
        path = Path(file_path).resolve()
        docs = next((value for value in path.parents if value.name == "docs"), None)
        master = docs / "design-system" / "MASTER.md" if docs else None
        if master is not None and master.is_file() \
                and note_status(master) == "approved":
            return deny(
                "approved Design System content is immutable through Write/Edit;"
                " use design_system_compile.py begin-revision first"
            )
        content = written_content(written) + "\n" + str(
            written.get("old_string") or ""
        )
        if DESIGN_SYSTEM_MACHINE_FIELD_RE.search(content):
            return deny(
                "Design System status, revision, approval timestamp and hashes"
                " are machine-managed; use design_system_compile.py"
            )
    if rel.startswith("experience-design/"):
        if "/_generated/" in f"/{rel}":
            return deny("Experience Design _generated files are compiler-owned; run experience_compile.py render")
        if "/artifacts/" in rel and rel.endswith(".html"):
            manifest = Path(file_path).with_name(
                Path(file_path).name.removesuffix("-preview.html")
                + "-artifact.md"
            )
            if not manifest.is_file():
                return deny(
                    "Experience Design HTML must be born through"
                    " experience_compile.py init-artifact"
                )
            if note_status(manifest) == "approved":
                return deny(
                    "approved Experience Design artifacts are immutable;"
                    " create a new draft package"
                )
        if rel.endswith("-artifact.md") and Path(file_path).is_file() \
                and note_status(Path(file_path)) == "approved":
            return deny(
                "approved Experience Design artifact manifests are immutable;"
                " create a new draft package"
            )
        approved_owner = approved_experience_owner(Path(file_path).resolve())
        if approved_owner is not None:
            return deny(
                "approved Experience Design release/program content is immutable;"
                " create the next release through experience_compile.py"
            )
        if rel.endswith(".md") and not any(pattern.fullmatch(rel) for pattern in EXPERIENCE_PATHS):
            return deny(f"invalid Experience Design filename or path '{rel}'; use compiler init/stub commands")
        content = written_content(written) + "\n" + str(written.get("old_string") or "")
        if EXPERIENCE_MACHINE_FIELD_RE.search(content):
            return deny("Experience Design approval, revision hash and timestamp fields are machine-managed; use render/stamp")
    if rel.startswith("backlog/") and rel.endswith(".md"):
        proposed = written_content(written)
        removed = str(written.get("old_string") or "")
        content = proposed + "\n" + removed
        if BACKLOG_MACHINE_FIELD_RE.search(content):
            return deny(
                "Backlog approval timestamps and hashes are machine-managed;"
                " use backlog_compile.py"
            )
        approval_transition = bool(BACKLOG_APPROVED_STATE_RE.search(content))
        if "content" in written and Path(file_path).is_file():
            try:
                current = Path(file_path).read_text(encoding="utf-8")
            except OSError:
                current = ""
            approval_transition = approval_transition or (
                bool(BACKLOG_APPROVED_STATE_RE.search(current))
                != bool(BACKLOG_APPROVED_STATE_RE.search(proposed))
            )
        if approval_transition:
            return deny(
                "Backlog transitions into or out of approved status are"
                " machine-managed; use backlog_compile.py approve"
            )
    if not rel.endswith(".md"):
        return 0
    content = written_content(written)
    if INLINE_FLOW_LIST_RE.search(content):
        return deny(
            "tags:/aliases: as an inline flow list; the vault contract is a"
            " block list (key:, then one '- item' line per value).")
    for _lineno, line in outside_fences(content):
        for match in MD_LINK_RE.finditer(line):
            target = match.group(1).split("#", 1)[0]
            if not target:
                continue
            resolved = normalize_posix(
                (Path(file_path).parent / target).as_posix())
            if vault_relative(resolved) is not None:
                return deny(
                    f"relative markdown link '{match.group(1)}' targets vault"
                    " content; cite it as a vault-absolute wikilink with an"
                    " alias, [[subtree/path|display]]. External URLs keep"
                    " [text](url); targets outside the docs tree keep"
                    " relative links.")
        if vault_check.is_table_row(line):
            for match in vault_check.WIKILINK_RE.finditer(line):
                if re.search(r"(?<!\\)\|", match.group("inner")):
                    return deny(
                        "unescaped alias pipe inside a table-cell wikilink;"
                        " write [[target\\|alias]] or the pipe splits the"
                        " row.")
    return 0


def post(payload: dict) -> int:
    if "file_targets" not in payload:
        payload = normalize(payload)
    for written in payload.get("file_targets", []):
        code = post_target(str(written.get("file_path", "")))
        if code:
            return code
    return 0


def post_target(file_path: str) -> int:
    rel = vault_relative(file_path)
    if rel is None or not rel.endswith(".md"):
        return 0
    root = vault_root(file_path)
    if root is None:
        return 0
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = vault_check.main([
            "check", "--vault", str(root), "--impact", rel,
        ])
    if code == 1:
        sys.stderr.write(buffer.getvalue())
        print(
            "vault law: this write left the findings above; repair them in"
            " this session before moving on. Generated files are"
            " re-rendered, never edited.",
            file=sys.stderr)
        return 2
    if rel.startswith("experience-design/") and experience_compile is not None:
        parts = rel.split("/")
        try:
            release_index = parts.index("releases")
            release_root = root.joinpath(*parts[:release_index + 2])
        except (ValueError, IndexError):
            release_root = None
        if release_root is not None and (release_root / "release.md").is_file():
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = experience_compile.main([
                    "check", "--release-root", str(release_root), "--changed",
                ])
            if code == 1:
                sys.stderr.write(buffer.getvalue())
                print("vault law: Experience Design compiler found a scoped violation; repair it before continuing", file=sys.stderr)
                return 2
    return 0


def shell_project(payload: dict) -> Path:
    cwd = Path(str(payload.get("cwd") or ".")).resolve()
    for parent in (cwd, *cwd.parents):
        workspace = parent / "workspace"
        if (workspace / "docs").is_dir() \
                or (workspace / "config.json").is_file():
            return parent
        if (parent / ".git").exists():
            return parent
    return cwd


def shell_vault(payload: dict) -> Path | None:
    candidate = shell_project(payload) / "workspace" / "docs"
    return candidate if candidate.is_dir() else None


def _cli_path(value: str, cwd: Path) -> Path | None:
    if not value or "$" in value:
        return None
    path = Path(value).expanduser()
    return (path if path.is_absolute() else cwd / path).resolve()


def _installed_script_path(value: str, cwd: Path, name: str) -> Path | None:
    expanded = os.path.expandvars(value)
    candidate = _cli_path(expanded, cwd)
    if candidate is None:
        return None
    # In a built host distribution vault_hook and the writers are siblings.
    # Source-tree tests run the uncomposed overlay, so fall back to the
    # canonical directory that supplied the imported vault_check module.
    sibling = (Path(__file__).resolve().parent / name).resolve()
    expected = sibling if sibling.is_file() else (
        Path(vault_check.__file__).resolve().parent / name
    ).resolve()
    return candidate if candidate == expected else None


def _option_value(args: list[str], option: str) -> str:
    if args.count(option) != 1:
        return ""
    index = args.index(option)
    return args[index + 1] if index + 1 < len(args) else ""


def has_shell_composition(command: str) -> bool:
    """Detect shell composition outside quotes; quoted config values are data."""
    quote = ""
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        if escaped:
            escaped = False
        elif quote == "'":
            if char == "'":
                quote = ""
        elif quote == '"':
            if char == "\\":
                escaped = True
            elif char == '"':
                quote = ""
            elif char == "`" or command[index:index + 2] == "$(":
                return True
        elif char == "\\":
            escaped = True
        elif char in ("'", '"'):
            quote = char
        elif char in "\n\r;|&<>`" or command[index:index + 2] == "$(":
            return True
        index += 1
    return bool(quote or escaped)


def sanctioned_config_writer(payload: dict, config_path: Path) -> bool:
    """Recognize one direct, scoped config writer and no shell composition."""
    command = str(payload.get("tool_input", {}).get("command") or "").strip()
    if not command or has_shell_composition(command):
        return False
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return False
    while tokens and SHELL_ASSIGNMENT_RE.fullmatch(tokens[0]):
        name, value = tokens.pop(0).split("=", 1)
        if SANCTIONED_SHELL_ASSIGNMENTS.get(name) != value:
            return False
    if len(tokens) < 2 or not PYTHON_COMMAND_RE.fullmatch(
            Path(tokens[0]).name):
        return False
    cwd = Path(str(payload.get("cwd") or ".")).resolve()
    script = Path(tokens[1]).name
    if _installed_script_path(tokens[1], cwd, script) is None:
        return False
    args = tokens[2:]
    project = config_path.parent.parent.resolve()
    vault = project / "workspace" / "docs"
    if script == "setup_project.py":
        target = _cli_path(_option_value(args, "--project-root"), cwd)
        return target == project
    if script == "project_config.py":
        if not args or args[0] not in SANCTIONED_PROJECT_CONFIG_COMMANDS:
            return False
        target = _cli_path(_option_value(args, "--config"), cwd)
        return target == config_path.resolve()
    if script == "vault_check.py":
        index = 0
        while index < len(args) and args[index] == "--policy":
            index += 2
        if index >= len(args) or args[index] != "reconcile-designations":
            return False
        target = _cli_path(_option_value(args, "--vault"), cwd)
        return target == vault.resolve()
    return False


def guarded_config_hash(text: str | None, exists: bool) -> str:
    if not exists:
        projection: dict = {"state": "missing"}
    else:
        try:
            parsed = json.loads(text or "")
        except json.JSONDecodeError:
            parsed = None
        if not isinstance(parsed, dict):
            projection = {
                "state": "invalid",
                "raw_sha256": hashlib.sha256(
                    (text or "").encode("utf-8")
                ).hexdigest(),
            }
        else:
            projection = {
                "state": "object",
                "values": {
                    key: {"present": key in parsed, "value": parsed.get(key)}
                    for key in CONFIG_GUARD_KEYS
                },
            }
    encoded = json.dumps(
        projection, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def read_config_snapshot(path: Path) -> dict:
    if not path.exists():
        return {
            "path": str(path), "exists": False, "text": "", "mode": 0,
            "guard_hash": guarded_config_hash(None, False),
        }
    try:
        text = path.read_text(encoding="utf-8")
        mode = path.stat().st_mode & 0o777
    except (OSError, UnicodeError) as exc:
        return {"path": str(path), "exists": True, "read_error": str(exc)}
    return {
        "path": str(path), "exists": True, "text": text, "mode": mode,
        "guard_hash": guarded_config_hash(text, True),
    }


def restore_config(snapshot: dict, expected_path: Path) -> str | None:
    recorded = Path(str(snapshot.get("path") or ""))
    try:
        expected_path = expected_path.resolve()
        if recorded.resolve() != expected_path:
            return "recovery snapshot names a non-canonical config path"
    except (OSError, RuntimeError) as exc:
        return str(exc)
    path = expected_path
    try:
        if not snapshot.get("exists"):
            path.unlink(missing_ok=True)
            sync_directory(path.parent)
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.restore-", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(str(snapshot.get("text") or ""))
                handle.flush()
                os.fsync(handle.fileno())
            mode = snapshot.get("mode")
            os.chmod(temporary, mode if isinstance(mode, int) else 0o644)
            os.replace(temporary, path)
            sync_directory(path.parent)
        finally:
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass
    except OSError as exc:
        return str(exc)
    return None


def sync_directory(path: Path) -> None:
    """Best-effort durability after an atomic replace or unlink."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def registration_guard(payload: dict) -> int:
    """Project configuration is validated by its owning setup command."""
    return 0


def vault_inventory(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        # App session files, trash and the ignored policy-owned community
        # plugin projection are not authored vault state. Setup owns their
        # convergence; package refresh must not widen into an authored gate.
        if rel in {
            ".obsidian/workspace.json",
            ".obsidian/workspace-mobile.json",
            ".obsidian/community-plugins.json",
        } or rel.startswith((".obsidian/plugins/", ".trash/")):
            continue
        result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def canonical_json(value: dict) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def guard_binding(payload: dict) -> str:
    command = str(payload.get("tool_input", {}).get("command") or "")
    event = ""
    for key in ("tool_use_id", "tool_call_id", "event_id"):
        if payload.get(key):
            event = str(payload[key])
            break
    value = {
        "project": str(shell_project(payload).resolve()),
        "session_id": str(payload.get("session_id") or "unknown"),
        "event": event or hashlib.sha256(command.encode("utf-8")).hexdigest(),
    }
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def inventory_path(payload: dict) -> Path:
    session = str(payload.get("session_id") or "unknown")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session)[:80]
    project = shell_project(payload)
    name = f"{safe}-{guard_binding(payload)[:16]}.json"
    return (project / ".agentrof" / "agent-marketplace" / ".runtime"
            / "vault-inventory" / name)


def recovery_root() -> Path:
    user = getattr(os, "getuid", lambda: 0)()
    return Path(tempfile.gettempdir()) / f"agentrof-vault-hook-{user}"


def recovery_path(payload: dict) -> Path:
    return recovery_root() / f"{guard_binding(payload)}.json"


def cleanup_stale_recovery(root: Path) -> None:
    cutoff = time.time() - RECOVERY_TTL_SECONDS
    try:
        candidates = list(root.glob("*.json"))
    except OSError:
        return
    for candidate in candidates:
        try:
            if candidate.is_file() and candidate.stat().st_mtime < cutoff:
                candidate.unlink()
        except OSError:
            continue


def prepare_recovery_root() -> Path:
    root = recovery_root()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise OSError("config recovery root is not a private directory")
    os.chmod(root, 0o700)
    cleanup_stale_recovery(root)
    return root


def atomic_replace_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.",
                                      dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_create_text(path: Path, text: str) -> None:
    """Publish a fully-written capsule only when this event has no owner."""
    fd, temporary = tempfile.mkstemp(prefix=".recovery-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.link(temporary, path, follow_symlinks=False)
        sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def valid_config_snapshot(snapshot: object, expected_path: Path) -> bool:
    if not isinstance(snapshot, dict):
        return False
    try:
        recorded = Path(str(snapshot.get("path") or "")).resolve()
        expected = expected_path.resolve()
    except (OSError, RuntimeError):
        return False
    if recorded != expected:
        return False
    exists = snapshot.get("exists")
    if not isinstance(exists, bool):
        return False
    text = snapshot.get("text")
    if not isinstance(text, str):
        return False
    mode = snapshot.get("mode")
    if not isinstance(mode, int) or not 0 <= mode <= 0o7777:
        return False
    expected_hash = guarded_config_hash(text if exists else None, exists)
    return snapshot.get("guard_hash") == expected_hash


def load_json_file(path: Path) -> tuple[dict | None, bytes | None, str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, None, str(exc)
    if not isinstance(value, dict):
        return None, raw, "snapshot root is not an object"
    return value, raw, ""


def load_recovery(payload: dict, expected_path: Path) \
        -> tuple[dict | None, str]:
    envelope, _, error = load_json_file(recovery_path(payload))
    if envelope is None:
        return None, error
    state = envelope.get("state")
    if not isinstance(state, dict):
        return None, "recovery state is missing"
    digest = hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()
    if envelope.get("state_sha256") != digest:
        return None, "recovery state checksum does not match"
    if state.get("binding") != guard_binding(payload):
        return None, "recovery state belongs to another Bash event"
    if not valid_config_snapshot(state.get("config"), expected_path):
        return None, "recovery config snapshot is invalid"
    return state, ""


def cleanup_guard_state(primary: Path, recovery: Path) -> None:
    for path in (primary, recovery):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        recovery.parent.rmdir()
    except OSError:
        pass


def shell_snapshot(payload: dict) -> int:
    root = shell_vault(payload)
    path = inventory_path(payload)
    config_path = shell_project(payload) / "workspace" / "config.json"
    config = read_config_snapshot(config_path)
    if config.get("read_error"):
        return deny(
            "workspace/config.json could not be snapshotted before Bash;"
            " refusing a command whose config effects could not be restored"
        )
    value = {
        "vault": str(root) if root else "",
        "inventory": vault_inventory(root) if root else {},
        "config": config,
        "config_writer_allowed": sanctioned_config_writer(
            payload, config_path
        ),
    }
    primary_text = canonical_json(value)
    state = {
        "binding": guard_binding(payload),
        "config": config,
        "config_writer_allowed": value["config_writer_allowed"],
        "primary_sha256": hashlib.sha256(
            primary_text.encode("utf-8")
        ).hexdigest(),
    }
    capsule = canonical_json({
        "state": state,
        "state_sha256": hashlib.sha256(
            canonical_json(state).encode("utf-8")
        ).hexdigest(),
    })
    recovery = recovery_path(payload)
    try:
        prepare_recovery_root()
        atomic_create_text(recovery, capsule)
    except FileExistsError:
        return deny(
            "a Bash guard already owns this session/event identity;"
            " refusing to overwrite its recovery state"
        )
    except OSError as exc:
        return deny(f"Bash config recovery capsule could not be created: {exc}")
    try:
        atomic_replace_text(path, primary_text)
    except OSError as exc:
        recovery.unlink(missing_ok=True)
        return deny(f"Bash vault inventory could not be snapshotted: {exc}")
    return 0


def shell_verify(payload: dict) -> int:
    path = inventory_path(payload)
    recovery = recovery_path(payload)
    expected_config = shell_project(payload) / "workspace" / "config.json"
    before, primary_raw, primary_error = load_json_file(path)
    recovery_state, recovery_error = load_recovery(payload, expected_config)
    integrity_error = ""
    if recovery_state is not None:
        primary_hash = hashlib.sha256(primary_raw or b"").hexdigest()
        if before is None:
            integrity_error = "project-local vault snapshot is missing or unreadable"
        elif primary_hash != recovery_state.get("primary_sha256"):
            integrity_error = "project-local vault snapshot was tampered with"
            before = None
        config_before = recovery_state["config"]
        writer_allowed = bool(recovery_state.get("config_writer_allowed"))
    else:
        integrity_error = (
            "config recovery capsule is missing or unreadable"
            + (f": {recovery_error}" if recovery_error else "")
        )
        config_before = before.get("config") if isinstance(before, dict) else None
        if not valid_config_snapshot(config_before, expected_config):
            config_before = None
        writer_allowed = bool(
            before.get("config_writer_allowed")
        ) if isinstance(before, dict) else False
    try:
        if isinstance(config_before, dict):
            config_after = read_config_snapshot(expected_config)
            changed = (
                config_after.get("guard_hash")
                != config_before.get("guard_hash")
            )
            if changed and not writer_allowed:
                restore_error = restore_config(config_before, expected_config)
                if restore_error:
                    message = (
                        "Bash changed machine-managed workspace/config.json"
                        f" values; restore failed: {restore_error}"
                    )
                else:
                    message = (
                        "Bash changed machine-managed workspace/config.json"
                        " values; the original config was restored. Use"
                        " setup_project.py, project_config.py or vault_check.py"
                        " reconcile-designations"
                    )
                if integrity_error:
                    message += f"; {integrity_error}"
                return deny(message)
        if integrity_error:
            detail = f" ({primary_error})" if primary_error else ""
            return deny(
                f"Bash guard state failed closed: {integrity_error}{detail};"
                " vault effects could not be verified"
            )
        if not isinstance(before, dict):
            return deny("Bash vault inventory is missing or unreadable")
        root_value = str(before.get("vault", ""))
        if not root_value:
            return 0
        root = Path(root_value)
        old = before.get("inventory", {})
        new = vault_inventory(root) if root.is_dir() else {}
        changed = sorted(key for key in set(old) | set(new)
                         if old.get(key) != new.get(key))
        if not changed:
            return 0
        deleted = [key for key in changed if key not in new]
        checks = [None] if deleted else [
            key for key in changed if key.endswith(".md")
        ]
        for impacted in checks:
            argv = ["check", "--vault", str(root)]
            if impacted is not None:
                argv.extend(("--impact", impacted))
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = vault_check.main(argv)
            if code:
                sys.stderr.write(buffer.getvalue())
                return deny(
                    "Bash changed vault inventory and left its scoped vault"
                    " check red; repair or restore the changed paths")
        return 0
    finally:
        cleanup_guard_state(path, recovery)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "pre"
    payload = read_payload()
    if mode == "register":
        code = register()
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": (
                    "AGENT_MARKETPLACE_HOOKS_ACTIVE: software-engineering-team"
                ),
            }
        }))
        return code
    payload = normalize(payload)
    if payload.get("raw_tool_name") == "apply_patch" \
            and payload.get("patch_parse_error"):
        return deny(
            "the apply_patch payload could not be parsed safely ("
            + str(payload["patch_parse_error"])
            + "); the vault guard fails closed. Retry with a valid patch."
        )
    if payload.get("tool_name") == "Bash":
        if mode == "pre":
            code = registration_guard(payload)
            return code if code else shell_snapshot(payload)
        return shell_verify(payload)
    if payload.get("tool_name") not in ("Write", "Edit"):
        return 0
    return pre(payload) if mode == "pre" else post(payload)


if __name__ == "__main__":
    sys.exit(main())
