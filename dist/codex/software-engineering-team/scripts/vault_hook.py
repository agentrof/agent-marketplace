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
import base64
import hashlib
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import SimpleNamespace

import vault_check
try:
    import experience_compile
except ImportError:  # source-tree tests may load only the shared overlay
    experience_compile = None
try:
    import experience_application_check
except ImportError:  # source-tree tests may load only the shared overlay
    experience_application_check = None

VAULT_SEGMENTS = ("workspace", "docs")

# Tool-name vocabulary -> the canonical pair this hook reasons in.
TOOL_NAME_CANON = {
    "Write": "Write",
    "Edit": "Edit", "MultiEdit": "Edit",
    "apply_patch": "Edit",
    "PowerShell": "Bash",
}

PATCH_HEADER_RE = re.compile(r"^\*\*\* (Add|Update|Delete) File: (.+)$")

# The complete bootstrap config has only sanctioned subprocess writers:
# project_config.py for language and setup for structural replacement. Neither
# traverses PreToolUse, so direct Write/Edit changes are denied.
CONFIG_GUARD_KEYS = (
    "schema_version", "team_id", "output_language", "terminology_language",
)

CONFIG_GUARD_MESSAGE = (
    "team-owned workspace config fields are machine-managed; their writers are"
    " setup and project_config.py."
    " Direct edits desynchronize the workspace config.")

SHELL_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
PYTHON_COMMAND_RE = re.compile(r"^python(?:3(?:[.][0-9]+)?)?$")
# Native Windows authorization uses only syntax that is inert under both
# cmd.exe and PowerShell. Those shells disagree about quoting and expansion,
# so POSIX escape rules cannot establish a trusted direct command.
WINDOWS_CMD_ALWAYS_UNSAFE_RE = re.compile(r"[\x00-\x1f\x7f^%!`$]")
SANCTIONED_PROJECT_CONFIG_COMMANDS = {"set"}
SANCTIONED_SHELL_ASSIGNMENTS = {"PYTHONDONTWRITEBYTECODE": "1"}
APPLICATION_ROOT_WRITERS = {
    "init",
    "begin-application-revision",
    "render-application",
    "enter-application-review",
    "approve-set",
}
APPLICATION_PACKAGE_WRITERS = {
    "begin-revision", "enter-review", "stub", "render", "rename", "retire",
}
RECOVERY_TTL_SECONDS = 24 * 60 * 60
EXPERIENCE_ROOT_RELATIVE = "experience-design"
EXPERIENCE_ARTIFACT_ROOT_RELATIVE = "experience-design/artifacts"

# A relative markdown link that is not http(s)/mailto/anchor/root form.
MD_LINK_RE = re.compile(
    r"(?<!\!)\[[^\]]*\]\((?!https?://|mailto:|#|/)([^)\s]+?)"
    r"(?:\s+\"[^\"]*\")?\)")
INLINE_FLOW_LIST_RE = re.compile(r"^\s*(tags|aliases):\s*\[", re.MULTILINE)
FENCE_RE = re.compile(r"^\s*```")
EXPERIENCE_MACHINE_FIELD_RE = re.compile(
    r"(?m)^\s*(approved_at_utc|approval_revision|revision|registry_hash|"
    r"package_hash|source_hash|stamped_at):")
ARCHITECTURE_MACHINE_FIELD_RE = re.compile(
    r"(?m)^\s*(record_id|revision|record_state|supersedes|introduced_by|"
    r"architecture_delta_hash|revision_state):")
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
    re.compile(r"^experience-design/experiences/[a-z0-9]+(?:-[a-z0-9]+)*/experience\.md$"),
    re.compile(r"^experience-design/experiences/[a-z0-9]+(?:-[a-z0-9]+)*/(?:journeys/[a-z0-9-]+-journey|screens/[a-z0-9-]+-screen|flows/[a-z0-9-]+-flow-set|states/[a-z0-9-]+-state|transitions/[a-z0-9-]+-transition)\.md$"),
)
def note_field(path: Path, field: str) -> str:
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
        if value.startswith(field + ":"):
            return value.split(":", 1)[1].strip().strip("\"'")
    return ""


def note_status(path: Path) -> str:
    return note_field(path, "status")


def approved_experience_owner(path: Path) -> Path | None:
    for parent in (path.parent, *path.parents):
        experience = parent / "experience.md"
        if experience.is_file() and note_status(experience) == "approved":
            return experience
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


def canonical_shell_command(tool_input: dict) -> tuple[str, str]:
    """Resolve the documented command field and one defensive host alias."""
    if not isinstance(tool_input, dict):
        return "", "tool_input must be an object"
    command_present = "command" in tool_input
    cmd_present = "cmd" in tool_input
    command = tool_input.get("command")
    cmd = tool_input.get("cmd")
    if command_present and not isinstance(command, str):
        return "", "tool_input.command must be a string"
    if cmd_present and not isinstance(cmd, str):
        return "", "tool_input.cmd must be a string"
    if command_present and cmd_present and command != cmd:
        return "", "tool_input.command and tool_input.cmd disagree"
    return str(command if command_present else cmd if cmd_present else ""), ""


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
            # Codex native/freeform tool calls carry their payload under
            # ``command``. Other hosts use one of the legacy aliases below.
            for key in ("command", "patch", "patchText", "input", "text"):
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
    elif tool == "Bash":
        command, error = canonical_shell_command(tool_input)
        if error:
            out["shell_command_error"] = error
        else:
            tool_input["command"] = command
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


def is_opaque_artifact(rel: str) -> bool:
    """Use the vault policy's one artifact-path definition in write guards."""
    try:
        policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return vault_check.is_artifact_path(policy, rel)


def artifact_hard_cut_violation(rel: str) -> str:
    try:
        policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
    except (OSError, ValueError, json.JSONDecodeError):
        return ""
    return vault_check.artifact_hard_cut_violation(policy, rel)


def canonical_application_surface(rel: str) -> str:
    """Return the canonical spelling for any Experience Design path.

    The filesystem security boundary is the complete subtree, not only the
    application and package maps. On case-insensitive hosts, a case-variant
    `_ledger` or `_generated` path aliases the same compiler-owned file and
    must therefore be rejected before the ordinary case-sensitive guards run.
    """
    normalized = unicodedata.normalize("NFC", normalize_posix(rel))
    folded = normalized.casefold()
    if folded == "experience-design" or folded.startswith(
            "experience-design/"):
        return folded
    return ""


def application_surface_alias_violation(file_path: str) -> str:
    """Reject lexical or filesystem aliases to the Experience subtree."""
    raw_path = file_path.replace("\\", "/")
    raw_root = vault_root(file_path)
    try:
        resolved_path = Path(file_path).resolve(strict=False)
    except (OSError, RuntimeError):
        resolved_path = Path(file_path)
    resolved_vault = vault_root(str(resolved_path))
    root = raw_root or resolved_vault
    if root is None:
        return ""
    raw_rel = ""
    if raw_root is not None:
        raw_rel = vault_relative(file_path) or ""
    lexical_surface = canonical_application_surface(raw_rel)
    try:
        resolved_root = (resolved_vault or root).resolve(strict=False)
        resolved_rel = resolved_path.relative_to(resolved_root).as_posix()
    except (OSError, RuntimeError, ValueError):
        resolved_rel = ""
    resolved_surface = canonical_application_surface(resolved_rel)
    expected = lexical_surface or resolved_surface
    if not expected:
        return ""

    raw_segments = raw_path.split("/")
    noncanonical_segment = any(
        segment in ("", ".", "..")
        for index, segment in enumerate(raw_segments)
        if not (index == 0 and segment == "")
    )
    if (raw_path != unicodedata.normalize("NFC", raw_path)
            or noncanonical_segment
            or raw_rel != expected
            or (resolved_rel and resolved_rel != expected)):
        return (
            "non-canonical Experience Design path alias is forbidden;"
            f" use exactly workspace/docs/{expected}"
        )

    # Broad system aliases such as /var -> /private/var are outside the vault
    # security boundary. Inspect the project, workspace/docs and descendants.
    cursor = root
    candidates = [root.parent.parent, root.parent, root]
    for segment in Path(raw_rel).parts:
        cursor = cursor / segment
        candidates.append(cursor)
    try:
        if any(path_is_alias(candidate) for candidate in candidates):
            return (
                "filesystem aliases to Experience Design paths are"
                f" forbidden; use exactly workspace/docs/{expected}"
            )
    except OSError:
        return (
            "Experience Design path identity could not be verified;"
            " refusing the write"
        )
    return ""


def local_tree_paths(root: Path, vault: Path) -> list[Path]:
    """List one local tree without traversing symlinks or reparse points."""
    if path_is_alias(root):
        raise OSError(
            f"{root.relative_to(vault).as_posix()}: path is an alias"
        )
    if not root.exists():
        return []
    if not root.is_dir():
        raise OSError(
            f"{root.relative_to(vault).as_posix()}: path is not a directory"
        )
    paths: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda entry: entry.name)
        for entry in children:
            path = Path(entry.path)
            relative = path.relative_to(vault).as_posix()
            if path_is_alias(path):
                raise OSError(f"{relative}: path is an alias")
            is_directory = entry.is_dir(follow_symlinks=False)
            is_file = entry.is_file(follow_symlinks=False)
            # Opaque prototype contents are author-owned and can contain large
            # dependency/build trees. Bind only the canonical artifact root;
            # their exact bytes/topology belong to compiler approval, not the
            # per-shell rollback hot path.
            if author_owned_artifact_path(relative):
                if relative != EXPERIENCE_ARTIFACT_ROOT_RELATIVE:
                    raise OSError(
                        f"{relative}: artifact root spelling is non-canonical;"
                        f" use {EXPERIENCE_ARTIFACT_ROOT_RELATIVE}"
                    )
                if not is_directory:
                    raise OSError(f"{relative}: artifact root is not a directory")
                continue
            if not is_directory and not is_file:
                raise OSError(f"{relative}: path is not a regular file")
            paths.append(path)
            if is_directory:
                pending.append(path)
    return sorted(paths)


def experience_topology_problem(vault: Path) -> str:
    """Describe the first unsafe Experience alias or directory shape."""
    try:
        local_tree_paths(vault / "experience-design", vault)
    except (OSError, RuntimeError, ValueError) as exc:
        return str(exc)
    return ""


def author_owned_artifact_path(relative: str) -> bool:
    folded = unicodedata.normalize("NFC", relative).casefold()
    return folded == EXPERIENCE_ARTIFACT_ROOT_RELATIVE or folded.startswith(
        EXPERIENCE_ARTIFACT_ROOT_RELATIVE + "/"
    )


def experience_hardlink_paths(vault: Path) -> list[str]:
    """Return canonical Experience files that share an inode.

    Symlinks are handled by the existing topology guards. A regular file with
    more than one link is equally unsafe because a write through the other
    name bypasses the canonical path and its lifecycle checks.
    """
    root = vault / "experience-design"
    try:
        paths = local_tree_paths(root, vault)
    except (OSError, RuntimeError, ValueError):
        return []
    violations: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            links = path.stat().st_nlink
        except OSError:
            violations.append(path.relative_to(vault).as_posix())
            continue
        if links != 1:
            violations.append(path.relative_to(vault).as_posix())
    return violations


def experience_tree_snapshot(vault: Path) -> dict[str, dict]:
    """Capture the recoverable Experience tree outside opaque artifacts."""
    root = vault / "experience-design"
    snapshot: dict[str, dict] = {}
    paths = local_tree_paths(root, vault)
    if root.exists():
        snapshot[EXPERIENCE_ROOT_RELATIVE] = {
            "kind": "directory",
            "mode": root.stat().st_mode & 0o777,
        }
    for path in paths:
        relative = path.relative_to(vault).as_posix()
        if author_owned_artifact_path(relative):
            continue
        if path.is_dir():
            snapshot[relative] = {
                "kind": "directory",
                "mode": path.stat().st_mode & 0o777,
            }
            continue
        try:
            raw = path.read_bytes()
            stat = path.stat()
        except OSError as exc:
            raise OSError(
                f"{relative}: file identity is unreadable: {exc}"
            ) from exc
        snapshot[relative] = {
            "kind": "file",
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "content_sha256": hashlib.sha256(raw).hexdigest(),
            "mode": stat.st_mode & 0o777,
            "nlink": stat.st_nlink,
            "device": stat.st_dev,
            "inode": stat.st_ino,
        }
    return snapshot


def experience_snapshot_path_problem(relative: str) -> str:
    """Reject path identities that can alias or escape on any supported OS."""
    if relative != unicodedata.normalize("NFC", relative):
        return "path must use canonical Unicode normalization"
    if "\\" in relative or ":" in relative:
        return "path contains a Windows drive, stream, or separator alias"
    if any(
        unicodedata.category(character) in {"Cc", "Zl", "Zp"}
        for character in relative
    ):
        return "path contains a control or line-separator character"
    pure = PurePosixPath(relative)
    if pure.is_absolute() or pure.as_posix() != relative:
        return "path must use canonical relative POSIX segments"
    parts = pure.parts
    if not parts or parts[0] != EXPERIENCE_ROOT_RELATIVE \
            or any(part in {"", ".", ".."} for part in parts):
        return "path escapes the Experience Design subtree"
    for part in parts:
        windows = PureWindowsPath(part)
        if part.endswith((" ", ".")) or windows.is_reserved():
            return "path is not portable to Windows"
    return ""


def experience_tree_snapshot_safety_problem(value: object) -> str:
    if not isinstance(value, dict):
        return "snapshot root is not an object"
    for relative, row in value.items():
        if not isinstance(relative, str):
            return f"snapshot contains a non-text path identity: {relative!r}"
        path_problem = experience_snapshot_path_problem(relative)
        if path_problem:
            return f"{relative}: {path_problem}"
        if author_owned_artifact_path(relative):
            return f"{relative}: opaque artifacts are outside recovery state"
        if not isinstance(row, dict):
            return f"{relative}: file identity snapshot is incomplete"
        if row.get("kind") == "directory":
            if set(row) != {"kind", "mode"} \
                    or not isinstance(row.get("mode"), int) \
                    or not 0 <= row["mode"] <= 0o777:
                return f"{relative}: directory snapshot is incomplete"
            continue
        if relative == EXPERIENCE_ROOT_RELATIVE:
            return f"{relative}: recovery root must remain a directory"
        if row.get("kind") != "file" or set(row) != {
            "kind", "content_base64", "content_sha256", "mode", "nlink",
            "device", "inode",
        }:
            return f"{relative}: file identity snapshot is incomplete"
        try:
            raw = base64.b64decode(
                str(row["content_base64"]), validate=True,
            )
        except (ValueError, TypeError):
            return f"{relative}: file bytes could not be snapshotted"
        if hashlib.sha256(raw).hexdigest() != row.get("content_sha256"):
            return f"{relative}: file snapshot checksum does not match"
        if (
            not isinstance(row.get("mode"), int)
            or not 0 <= row["mode"] <= 0o777
            or not isinstance(row.get("device"), int)
            or not isinstance(row.get("inode"), int)
        ):
            return f"{relative}: file mode or filesystem identity is unreadable"
        if not isinstance(row.get("nlink"), int) or row["nlink"] != 1:
            return (
                f"{relative}: file must have exactly one filesystem link;"
                " remove every hard-link alias"
            )
    return ""


def valid_experience_tree_snapshot(value: object) -> bool:
    return not experience_tree_snapshot_safety_problem(value)


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
    """Deny every direct workspace-config mutation, including unknown keys."""
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
                " unparseable write would blind every config check. "
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
        if proposed != disk:
            return deny(CONFIG_GUARD_MESSAGE)
        return 0
    return deny(CONFIG_GUARD_MESSAGE)


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


def rendered_edit(written: dict, disk: str) -> str | None:
    if "content" in written:
        return str(written.get("content") or "")
    body = written.get("patch_body")
    if isinstance(body, list):
        try:
            return apply_patch_body(disk, body)
        except ValueError:
            return None
    old = str(written.get("old_string") or "")
    new = str(written.get("new_string") or "")
    if old and disk.count(old) == 1:
        return disk.replace(old, new, 1)
    if not old and not new:
        return disk
    return None


def edit_overlaps_spans(text: str, old: str,
                        spans: list[tuple[int, int]]) -> bool:
    if not old:
        return False
    position = text.find(old)
    while position != -1:
        end = position + len(old)
        if any(position < span_end and end > span_start
               for span_start, span_end in spans):
            return True
        position = text.find(old, position + 1)
    return False


def pre(payload: dict) -> int:
    if "file_targets" not in payload:
        payload = normalize(payload)
    project_vault = shell_project(payload) / "workspace" / "docs"
    topology_problem = experience_topology_problem(project_vault)
    if topology_problem:
        return deny(
            "Experience Design must be one local directory tree: "
            + topology_problem
        )
    hardlinks = experience_hardlink_paths(project_vault)
    if hardlinks:
        return deny(
            "Experience Design files must have exactly one filesystem link; "
            "repair the hard-linked path(s): " + ", ".join(hardlinks)
        )
    for written in payload.get("file_targets", []):
        target = dict(written)
        raw_path = str(target.get("file_path", ""))
        cwd = str(payload.get("cwd") or "")
        if raw_path and cwd and not Path(raw_path).is_absolute():
            target["file_path"] = os.path.join(cwd, raw_path)
        code = pre_target(target)
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
    alias_violation = application_surface_alias_violation(file_path)
    if alias_violation:
        return deny(alias_violation)
    if is_workspace_config(file_path, written):
        try:
            return config_guard(written, file_path)
        except Exception:
            return 0  # a guard never takes the session down
    rel = vault_relative(file_path)
    if rel is None:
        return 0
    artifact = is_opaque_artifact(rel)
    if rel.startswith("backlog/_generated/"):
        return deny(
            "backlog/_generated files are compiler-owned; run"
            " backlog_compile.py check --render"
        )
    if rel.startswith(("maps/_relations/", "maps/_navigation/")):
        return deny(
            "inverse relation catalogs are compiler-owned; run"
            " vault_check.py render-relations")
    if rel.endswith(".md") and not artifact:
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
    if rel.startswith("operation/") and rel.endswith(".md"):
        path = Path(file_path).resolve()
        if path.is_file() and note_status(path) == "approved":
            return deny(
                "approved Operation contracts are immutable through Write/Edit;"
                " use operation_compile.py begin-revision first"
            )
        content = written_content(written) + "\n" + str(written.get("old_string") or "")
        if re.search(r"(?m)^\s*(status|revision|source_hash|approved_at_utc):", content):
            return deny(
                "Operation contract lifecycle fields are compiler-owned;"
                " use operation_compile.py"
            )
    if rel == "delivery/governance/governance.md":
        path = Path(file_path).resolve()
        if path.is_file() and note_status(path) == "approved":
            return deny(
                "approved Delivery Governance is immutable through Write/Edit;"
                " use delivery_governance.py begin-revision first"
            )
        content = written_content(written) + "\n" + str(written.get("old_string") or "")
        if re.search(r"(?m)^\s*(status|revision|max_parallel|governance_hash|source_hash|approved_at_utc):", content):
            return deny(
                "Delivery Governance lifecycle fields are compiler-owned;"
                " use delivery_governance.py"
            )
    if rel.startswith("experience-design/"):
        artifact_violation = artifact_hard_cut_violation(rel)
        if artifact_violation:
            return deny(artifact_violation)
        if re.match(r"^experience-design/experiences/exp-(?:[a-z0-9]+(?:-[a-z0-9]+)*)/", rel):
            return deny("Experience slugs are process names and must not use the retired exp- prefix")
        if "/_generated/" in f"/{rel}" or "/_ledger/" in f"/{rel}":
            return deny("Experience Design generated and ledger files are compiler-owned; run experience_compile.py")
        approved_owner = approved_experience_owner(Path(file_path).resolve())
        if approved_owner is not None:
            return deny(
                "approved Experience content is immutable;"
                " begin a living Experience revision through experience_compile.py"
            )
        if (rel.endswith(".md") and not artifact
                and not any(pattern.fullmatch(rel) for pattern in EXPERIENCE_PATHS)):
            return deny(f"invalid Experience Design filename or path '{rel}'; use compiler init/stub commands")
        content = written_content(written) + "\n" + str(written.get("old_string") or "")
        if EXPERIENCE_MACHINE_FIELD_RE.search(content):
            return deny("Experience approval, revision, hash and timestamp fields are machine-managed; use experience_compile.py")
    if rel.startswith("system-architecture/"):
        if "/_generated/" in f"/{rel}" or "/_ledger/" in f"/{rel}":
            return deny("System Architecture generated and ledger files are compiler-owned; use architecture_compile.py")
        owner = Path(file_path).resolve()
        if owner.is_file():
            if note_field(owner, "revision_state") == "sealed":
                return deny(
                    "sealed System Architecture content is immutable; use "
                    "architecture_compile.py begin-revision --item-ref"
                )
        if owner.is_file() and ARCHITECTURE_MACHINE_FIELD_RE.search(written_content(written) + "\n" + str(written.get("old_string") or "")):
            return deny("System Architecture record identity and revision fields are compiler-owned; use architecture_compile.py begin-revision --item-ref")
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
    project_vault = shell_project(payload) / "workspace" / "docs"
    topology_problem = experience_topology_problem(project_vault)
    if topology_problem:
        return deny(
            "this write left an unsafe Experience Design topology: "
            + topology_problem
        )
    hardlinks = experience_hardlink_paths(project_vault)
    if hardlinks:
        return deny(
            "this write left hard-linked Experience Design state: "
            + ", ".join(hardlinks)
        )
    for written in payload.get("file_targets", []):
        file_path = str(written.get("file_path", ""))
        cwd = str(payload.get("cwd") or "")
        if file_path and cwd and not Path(file_path).is_absolute():
            file_path = os.path.join(cwd, file_path)
        code = post_target(file_path)
        if code:
            return code
    return 0


def post_target(file_path: str) -> int:
    rel = vault_relative(file_path)
    if rel is None:
        return 0
    root = vault_root(file_path)
    if root is None:
        return 0
    if not rel.endswith(".md"):
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
        experience_root = root.joinpath(*parts[:3]) if len(parts) > 2 and parts[1] == "experiences" else None
        if experience_root is not None and (experience_root / "experience.md").is_file():
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = experience_compile.main([
                    "check", "--experience-root", str(experience_root),
                ])
            if code == 1:
                sys.stderr.write(buffer.getvalue())
                print("vault law: Experience compiler found a scoped violation; repair it before continuing", file=sys.stderr)
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
    candidate = _lexical_executable_path(expanded, cwd)
    if candidate is None or candidate.name != name:
        return None
    # In a built host distribution vault_hook and the writers are siblings.
    # Source-tree tests run the uncomposed overlay, so fall back to the
    # canonical directory that supplied the imported vault_check module.
    hook_directory = Path(__file__).resolve().parent
    imported_directory = Path(vault_check.__file__).resolve().parent
    packaged = hook_directory == imported_directory
    expected = (
        hook_directory / name if packaged else imported_directory / name
    )
    try:
        if path_is_alias(expected) or not expected.is_file() \
                or expected.stat().st_nlink != 1:
            return None
        lexical_expected = expected.parent.resolve() / expected.name
        if candidate != lexical_expected \
                or candidate.resolve() != expected.resolve():
            return None
    except (OSError, RuntimeError):
        return None
    manifest = expected.parent.parent / ".agent-marketplace-package.json"
    if packaged:
        if not manifest.is_file():
            return None
        try:
            package = json.loads(manifest.read_text(encoding="utf-8"))
            expected_hash = package["files"][f"scripts/{name}"]
            actual_hash = hashlib.sha256(expected.read_bytes()).hexdigest()
        except (KeyError, OSError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(expected_hash, str) or actual_hash != expected_hash:
            return None
    return candidate


def _lexical_executable_path(value: str, cwd: Path) -> Path | None:
    if not value or "$" in value:
        return None
    path = Path(value).expanduser()
    if path.is_absolute() or "/" in value or "\\" in value:
        path = path if path.is_absolute() else cwd / path
        path = Path(os.path.abspath(path))
        try:
            return path.parent.resolve() / path.name
        except (OSError, RuntimeError):
            return None
    resolved = shutil.which(value)
    if not resolved:
        return None
    path = Path(resolved)
    if not path.is_absolute():
        return None
    try:
        return path.parent.resolve() / path.name
    except (OSError, RuntimeError):
        return None


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _trusted_runtime_targets() -> set[Path]:
    values = {sys.executable, getattr(sys, "_base_executable", "")}
    targets = set()
    for value in values:
        if not value:
            continue
        try:
            targets.add(Path(value).resolve())
        except (OSError, RuntimeError):
            continue
    return targets


def _trusted_runtime_origins() -> set[Path]:
    origins = set()
    for value in (sys.executable, getattr(sys, "_base_executable", "")):
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            continue
        try:
            origins.add(path.parent.resolve() / path.name)
        except (OSError, RuntimeError):
            continue
    return origins


def _apple_developer_root(executables: set[Path]) -> Path | None:
    command_line_tools = Path("/Library/Developer/CommandLineTools")
    if any(_path_within(value, command_line_tools) for value in executables):
        return command_line_tools
    for value in executables:
        parts = value.parts
        try:
            index = parts.index("Contents")
        except ValueError:
            continue
        if index > 0 and index + 1 < len(parts) \
                and parts[index - 1].endswith(".app") \
                and parts[index + 1] == "Developer":
            return Path(*parts[:index + 2])
    return None


def _apple_python_launcher_matches(candidate: Path, cwd: Path) -> bool:
    """Bind Apple's fixed launcher to the interpreter running this hook."""
    launchers = {
        Path("/usr/bin/python3"),
        Path("/Library/Developer/CommandLineTools/usr/bin/python3"),
    }
    if sys.platform != "darwin" or candidate not in launchers:
        return False
    try:
        mode = candidate.stat()
    except OSError:
        return False
    if mode.st_uid != 0 or mode.st_mode & 0o022 or os.environ.get("TOOLCHAINS"):
        return False
    current = {
        Path(value)
        for value in (sys.executable, getattr(sys, "_base_executable", ""))
        if value and Path(value).is_absolute()
    }
    developer_root = _apple_developer_root(current)
    if developer_root is None:
        return False
    configured = os.environ.get("DEVELOPER_DIR")
    if configured:
        try:
            if Path(configured).resolve() != developer_root.resolve():
                return False
        except (OSError, RuntimeError):
            return False
    sdk_root = os.environ.get("SDKROOT")
    if sdk_root:
        try:
            if not _path_within(Path(sdk_root).resolve(), developer_root.resolve()):
                return False
        except (OSError, RuntimeError):
            return False
    try:
        result = subprocess.run(
            ["/usr/bin/xcrun", "--find", "python3"],
            capture_output=True, text=True, check=False, timeout=2,
        )
        lines = result.stdout.splitlines()
        selected = Path(lines[0]) if result.returncode == 0 and len(lines) == 1 else None
        if selected is None \
                or selected.resolve() not in _trusted_runtime_targets():
            return False
        project = shell_project({"cwd": str(cwd)}).resolve()
        return not _path_within(selected.resolve(), project)
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return False


def trusted_python_command(
    value: str, cwd: Path, allow_bare: bool = False,
) -> bool:
    name = re.split(r"[/\\]", value)[-1]
    if sys.platform == "win32":
        name = name.lower()
        if name.endswith(".exe"):
            name = name[:-4]
    if not PYTHON_COMMAND_RE.fullmatch(name) or "$" in value:
        return False
    # A bare name can be intercepted by a shell function or alias before PATH
    # resolution. Writer authorization therefore requires an exact executable
    # token; diagnostics still run, but receive no mutation exemption.
    if not Path(value).is_absolute() and not allow_bare:
        return False
    candidate = _lexical_executable_path(value, cwd)
    if candidate is None:
        return False
    project = shell_project({"cwd": str(cwd)}).resolve()
    try:
        if candidate in _trusted_runtime_origins() \
                and candidate.resolve() in _trusted_runtime_targets():
            return True
        if _path_within(candidate, project) \
                or _path_within(candidate.resolve(), project):
            return False
        # setup-python and similar managed runtimes commonly expose
        # ``python3`` as a symlink beside the interpreter that launched this
        # hook.  Preserve the lexical project-boundary check above, then
        # accept only that same-directory alias; an arbitrary PATH symlink to
        # the interpreter remains guard-only.
        if candidate.resolve() in _trusted_runtime_targets():
            trusted_directories = {
                Path(runtime).parent.resolve()
                for runtime in (
                    sys.executable,
                    getattr(sys, "_base_executable", ""),
                )
                if runtime and Path(runtime).is_absolute()
            }
            if candidate.parent.resolve() in trusted_directories:
                # A bare name is only eligible for the explicitly scoped
                # compatibility commands; direct absolute aliases retain the
                # normal writer path.
                return allow_bare or Path(value).is_absolute()
        return _apple_python_launcher_matches(candidate, cwd)
    except (OSError, RuntimeError):
        return False


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


def windows_cmd_has_composition(command: str) -> bool:
    """Reject cmd.exe operators outside canonical double-quoted argv."""
    quoted = False
    backslashes = 0
    for char in command:
        if char == '"' and backslashes % 2 == 0:
            quoted = not quoted
        elif not quoted and char in "&|<>()":
            return True
        backslashes = backslashes + 1 if char == "\\" else 0
    return quoted


def direct_shell_tokens(payload: dict) -> tuple[list[str], Path] | None:
    """Parse one direct command with only the closed safe assignment set."""
    command, error = canonical_shell_command(payload.get("tool_input", {}))
    command = command.strip()
    if error or not command:
        return None
    if sys.platform == "win32":
        # PowerShell is covered by the mutation guard, but it does not receive
        # writer authorization until a native launcher contract exists.
        family = payload.get("shell_family")
        if family != "cmd":
            return None
        unsafe = (
            WINDOWS_CMD_ALWAYS_UNSAFE_RE.search(command) is not None
            or windows_cmd_has_composition(command)
        )
        if payload.get("raw_tool_name") == "PowerShell" \
                or unsafe \
                or any(unicodedata.category(char) in {"Cc", "Zl", "Zp"}
                       for char in command):
            return None
        try:
            raw_tokens = shlex.split(command, posix=False)
        except ValueError:
            return None
        unquoted = []
        for token in raw_tokens:
            if token[:1] == '"':
                if len(token) < 2 or token[-1] != token[0]:
                    return None
                token = token[1:-1]
            if not token or "'" in token or '"' in token:
                return None
            unquoted.append(token)
        tokens = unquoted
        if not tokens or subprocess.list2cmdline(tokens) != command:
            return None
    else:
        # OpenCode explicitly attests the effective shell family. Keep
        # Claude/Codex POSIX payload compatibility when the field is absent,
        # but never turn an explicit custom/unknown identity into a grant.
        if "shell_family" in payload and payload.get("shell_family") != "posix":
            return None
        if has_shell_composition(command):
            return None
        try:
            tokens = shlex.split(command, posix=True)
        except ValueError:
            return None
    if sys.platform == "win32" and tokens \
            and SHELL_ASSIGNMENT_RE.fullmatch(tokens[0]):
        return None
    while sys.platform != "win32" and tokens \
            and SHELL_ASSIGNMENT_RE.fullmatch(tokens[0]):
        name, value = tokens.pop(0).split("=", 1)
        if SANCTIONED_SHELL_ASSIGNMENTS.get(name) != value:
            return None
    return tokens, Path(str(payload.get("cwd") or ".")).resolve()


def parsed_options(
        args: list[str], valued: set[str], flags: set[str]
) -> dict[str, str] | None:
    """Accept each declared option once and reject positional or unknown data."""
    result: dict[str, str] = {}
    index = 0
    while index < len(args):
        option = args[index]
        if option in flags:
            if option in result:
                return None
            result[option] = ""
            index += 1
            continue
        if option not in valued or option in result or index + 1 >= len(args):
            return None
        result[option] = args[index + 1]
        index += 2
    return result


def sanctioned_config_writer(payload: dict, config_path: Path) -> bool:
    """Recognize one direct, scoped config writer and no shell composition."""
    parsed = direct_shell_tokens(payload)
    if parsed is None:
        return False
    tokens, cwd = parsed
    if len(tokens) < 2 or not trusted_python_command(tokens[0], cwd):
        return False
    script = Path(tokens[1]).name
    if _installed_script_path(tokens[1], cwd, script) is None:
        return False
    args = tokens[2:]
    project = config_path.parent.parent.resolve()
    if script == "setup_project.py":
        target = _cli_path(_option_value(args, "--project-root"), cwd)
        return target == project
    if script == "project_config.py":
        if not args or args[0] not in SANCTIONED_PROJECT_CONFIG_COMMANDS:
            return False
        options = parsed_options(
            args[1:], {"--config", "--field", "--value"},
            {"--dry-run", "--json"},
        )
        target = _cli_path(
            options.get("--config", "") if options else "", cwd
        )
        return bool(
            options
            and target == config_path.resolve()
            and options.get("--field") in {
                "output_language", "terminology_language",
            }
            and options.get("--value")
        )
    return False


def relaxed_application_writer_spec(payload: dict, vault: Path) -> dict | None:
    """Parse the two legacy bare-runtime commands with attestable deltas."""
    parsed = direct_shell_tokens(payload)
    if parsed is None:
        return None
    tokens, cwd = parsed
    if len(tokens) < 3 or not trusted_python_command(
            tokens[0], cwd, allow_bare=True):
        return None
    if _installed_script_path(
            tokens[1], cwd, "experience_compile.py") is None:
        return None
    command_name = tokens[2]
    args = tokens[3:]
    experience_root = (vault / EXPERIENCE_ROOT_RELATIVE).resolve()
    if command_name == "render-application":
        options = parsed_options(args, {"--root"}, {"--json"})
        target = _cli_path(
            options.get("--root", "") if options else "", cwd,
        )
        if options is not None and target == experience_root:
            return {"command": command_name}
        return None
    if command_name != "init":
        return None

    single = {
        "--root", "--experience", "--origin-mode", "--primary-process-ref",
        "--requirement", "--scope-plan", "--proposal-hash", "--title",
    }
    repeated = {
        "--related-process-ref", "--ba-ref", "--solution-ref", "--design-ref",
    }
    options: dict[str, object] = {}
    index = 0
    while index < len(args):
        option = args[index]
        if option not in single | repeated or index + 1 >= len(args):
            return None
        value = args[index + 1]
        if option in single:
            if option in options:
                return None
            options[option] = value
        else:
            options.setdefault(option, [])
            repeated_values = options[option]
            if not isinstance(repeated_values, list):
                return None
            repeated_values.append(value)
        index += 2
    required = {
        "--root", "--experience", "--origin-mode", "--primary-process-ref",
        "--scope-plan", "--proposal-hash",
    }
    if not required.issubset(options):
        return None
    target = _cli_path(str(options["--root"]), cwd)
    scope_plan = _cli_path(str(options["--scope-plan"]), cwd)
    experience = str(options["--experience"])
    origin_mode = str(options["--origin-mode"])
    proposal_hash = str(options["--proposal-hash"])
    if (
        target != experience_root
        or scope_plan is None
        or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", experience) is None
        or experience.startswith("exp-")
        or experience == "application"
        or origin_mode not in {"manual", "requirement"}
        or re.fullmatch(r"sha256:[0-9a-f]{64}", proposal_hash) is None
    ):
        return None
    return {
        "command": command_name,
        "experience": experience,
        "origin_mode": origin_mode,
        "proposal_hash": proposal_hash,
        "scope_plan": str(scope_plan),
        "primary_process_ref": str(options["--primary-process-ref"]),
        "related_process_ref": list(options.get("--related-process-ref", [])),
        "requirement": str(options.get("--requirement", "")),
        "title": str(options.get("--title", "")),
        "ba_ref": list(options.get("--ba-ref", [])),
        "solution_ref": list(options.get("--solution-ref", [])),
        "design_ref": list(options.get("--design-ref", [])),
    }


def sanctioned_application_writer(
    payload: dict, vault: Path, allow_bare_runtime: bool = False,
) -> bool:
    """Recognize only direct invocations of application lifecycle writers."""
    if allow_bare_runtime:
        return relaxed_application_writer_spec(payload, vault) is not None
    parsed = direct_shell_tokens(payload)
    if parsed is None:
        return False
    tokens, cwd = parsed
    if len(tokens) < 3 or not trusted_python_command(
            tokens[0], cwd, allow_bare=allow_bare_runtime):
        return False
    if _installed_script_path(
            tokens[1], cwd, "experience_compile.py") is None:
        return False
    command_name = tokens[2]
    args = tokens[3:]
    experience_root = (vault / "experience-design").resolve()
    if command_name in APPLICATION_ROOT_WRITERS:
        target = _cli_path(_option_value(args, "--root"), cwd)
        return target == experience_root
    if command_name in APPLICATION_PACKAGE_WRITERS:
        target = _cli_path(_option_value(args, "--experience-root"), cwd)
        if target is None:
            return False
        if target.name == "experience.md":
            target = target.parent
        try:
            relative = target.relative_to(experience_root / "experiences")
        except ValueError:
            return False
        return len(relative.parts) == 1 and bool(relative.name)
    return False


def valid_application_writer_result(
    payload: dict, vault: Path, changed: list[str],
) -> bool:
    """Attest the exact relaxed command delta through the owning compiler."""
    spec = relaxed_application_writer_spec(payload, vault)
    if spec is None:
        return False
    if experience_compile is None or experience_application_check is None:
        return False
    changed_set = set(changed)
    if spec["command"] == "render-application":
        if changed_set != {
            "experience-design/_generated/application-registry.json",
        }:
            return False
        root = vault / "experience-design"
        target = root / "_generated" / "application-registry.json"
        try:
            expected, findings = (
                experience_application_check.compile_application(root)
            )
            expected_bytes = experience_application_check.canonical(expected)
            if (
                findings
                or path_is_alias(target)
                or not target.is_file()
                or target.stat().st_nlink != 1
                or target.stat().st_size != len(expected_bytes)
            ):
                return False
            actual_bytes = target.read_bytes()
        except (OSError, RuntimeError, ValueError):
            return False
        return actual_bytes == expected_bytes
    elif spec["command"] == "init":
        experience = str(spec["experience"])
        package_relative = f"experience-design/experiences/{experience}"
        allowed = {
            "home.md",
            "maps/experience-design.md",
            "experience-design/_generated",
            "experience-design/_generated/open-application-revision.json",
            "experience-design/experiences",
            package_relative,
            f"{package_relative}/experience.md",
            f"{package_relative}/journeys",
            f"{package_relative}/flows",
            f"{package_relative}/screens",
            f"{package_relative}/states",
            f"{package_relative}/transitions",
            f"{package_relative}/artifacts",
            f"{package_relative}/_generated",
            f"{package_relative}/_generated/open-revision.json",
            f"{package_relative}/_ledger",
        }
        required = {
            f"{package_relative}/experience.md",
            f"{package_relative}/_generated/open-revision.json",
        }
        if not required.issubset(changed_set) or not changed_set.issubset(allowed):
            return False
        package = vault / "experience-design" / "experiences" / experience
        try:
            root = vault / "experience-design"
            args = SimpleNamespace(
                origin_mode=spec["origin_mode"],
                requirement=spec["requirement"],
                ba_ref=spec["ba_ref"],
                solution_ref=spec["solution_ref"],
                design_ref=spec["design_ref"],
            )
            plan = experience_compile.load_scope_plan(
                str(spec["scope_plan"]), str(spec["proposal_hash"]),
            )
            if experience_compile.verify_scope_inputs(
                    root, plan, require_committed=True):
                return False
            receipts, input_errors, context = experience_compile.selected_inputs(
                root, args,
            )
            if input_errors:
                return False
            primary, primary_errors = experience_compile.process_from_inputs(
                root, str(spec["primary_process_ref"]), receipts,
                require_committed=True,
            )
            if primary_errors or primary is None:
                return False
            related = []
            for raw_related in spec["related_process_ref"]:
                related_process, related_errors = (
                    experience_compile.process_from_inputs(
                        root, str(raw_related), receipts,
                        require_committed=True,
                    )
                )
                if related_errors or related_process is None:
                    return False
                related.append(related_process)
            if plan.get("origin_mode") != spec["origin_mode"]:
                return False
            selected_action = experience_compile.action_for_plan(
                root, plan, action="create", experience=experience,
                process=primary, validate_current=False,
            )
            experience_compile.validate_open_revision(
                package, plan, selected_action, str(spec["proposal_hash"]),
                expected_status="draft",
            )
            experience_compile.validate_open_application_state(
                root, plan=plan, proposal_hash=str(spec["proposal_hash"]),
                expected_phase="draft",
            )
            fields = experience_compile.fields(package)
        except (OSError, ValueError):
            return False
        expected_title = str(spec["title"]) or (
            f"{experience.replace('-', ' ').title()} Experience"
        )
        if (
            fields.get("experience_id") != experience
            or fields.get("origin_mode") != spec["origin_mode"]
            or fields.get("status") != "draft"
            or fields.get("revision") != 1
            or fields.get("title") != expected_title
            or fields.get("primary_process_ref") != primary
            or experience_compile.list_value(
                fields, "related_process_refs",
            ) != related
            or experience_compile.list_value(
                fields, "input_bindings",
            ) != experience_compile.binding_rows(receipts)
        ):
            return False
        if spec["origin_mode"] == "requirement":
            requirement = str(spec["requirement"])
            if (
                plan.get("requirement") != requirement
                or experience_compile.list_value(
                    fields, "implements",
                ) != [requirement]
                or fields.get("upstream_stage_receipts_hash")
                != context.get("upstream_stage_receipts_hash")
            ):
                return False
        elif (
            experience_compile.list_value(fields, "implements")
            or fields.get("upstream_stage_receipts_hash")
        ):
            return False
        try:
            _registry, findings = (
                experience_application_check.compile_application(root)
            )
        except (OSError, RuntimeError, ValueError):
            return False
        expected_transient = (
            "active Experience packages require at least one author-owned "
            "prototype artifact before review"
        )
        if findings not in ([], [expected_transient]):
            return False
        return True
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
            # Config is a closed bootstrap contract. Hash all parsed values so
            # a Bash command cannot smuggle an unknown/retired field past the
            # writer guard by leaving the six canonical keys untouched.
            projection = {"state": "object", "values": parsed}
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


def path_is_alias(path: Path) -> bool:
    """Recognize symlinks and Windows reparse-point directory aliases."""
    try:
        mode = path.lstat()
    except OSError:
        return False
    return path.is_symlink() or bool(
        getattr(mode, "st_file_attributes", 0) & 0x400
    )


def make_windows_regular_file_writable(path: Path) -> None:
    """Clear the Windows READONLY attribute before recovery replacement."""
    if os.name != "nt" or path_is_alias(path) or not path.is_file():
        return
    mode = path.stat().st_mode
    if not mode & stat.S_IWRITE:
        os.chmod(path, mode | stat.S_IWRITE)


def remove_path_alias(path: Path) -> None:
    """Remove one alias itself without deleting anything in its target."""
    metadata = path.lstat()
    if path.is_symlink():
        path.unlink()
    elif stat.S_ISDIR(metadata.st_mode):
        os.rmdir(path)
    else:
        path.unlink()


def remove_local_tree(path: Path) -> None:
    """Remove one local path tree without traversing filesystem aliases."""
    if path_is_alias(path):
        remove_path_alias(path)
        return
    if path.is_dir():
        with os.scandir(path) as entries:
            children = [Path(entry.path) for entry in entries]
        for child in children:
            remove_local_tree(child)
        path.rmdir()
        return
    make_windows_regular_file_writable(path)
    path.unlink(missing_ok=True)


def config_topology_problem(path: Path) -> str:
    """Reject aliases, non-files and hard links at the config boundary."""
    parent = path.parent
    if path_is_alias(parent):
        return "workspace is a filesystem alias"
    if parent.exists() and not parent.is_dir():
        return "workspace is not a directory"
    if path_is_alias(path):
        return "workspace/config.json is a filesystem alias"
    if not path.exists():
        return ""
    if not path.is_file():
        return "workspace/config.json is not a regular file"
    try:
        if path.stat().st_nlink != 1:
            return "workspace/config.json has more than one filesystem link"
    except OSError as exc:
        return f"workspace/config.json identity is unreadable: {exc}"
    return ""


def ensure_local_directory(path: Path) -> str | None:
    """Replace one alias/non-directory with a local recovery directory."""
    try:
        if path_is_alias(path):
            remove_path_alias(path)
        elif path.exists() and not path.is_dir():
            make_windows_regular_file_writable(path)
            path.unlink()
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return str(exc)
    return None


def local_recovery_paths(root: Path, vault: Path) -> list[Path]:
    """List recoverable paths while pruning opaque artifact contents."""
    paths: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        mode = directory.stat().st_mode & 0o777
        os.chmod(directory, mode | 0o700)
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda entry: entry.name)
        for entry in children:
            path = Path(entry.path)
            relative = path.relative_to(vault).as_posix()
            if author_owned_artifact_path(relative):
                continue
            paths.append(path)
            if not path_is_alias(path) and entry.is_dir(follow_symlinks=False):
                pending.append(path)
    return paths


def experience_recovery_target(vault: Path, relative: str) -> Path:
    """Return one lexically contained target for a validated snapshot key."""
    problem = experience_snapshot_path_problem(relative)
    if problem:
        raise ValueError(f"{relative}: {problem}")
    base = Path(os.path.abspath(vault))
    target = Path(os.path.abspath(
        base.joinpath(*PurePosixPath(relative).parts)
    ))
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"{relative}: recovery target escapes the vault") from exc
    return target


def restore_config(snapshot: dict, expected_path: Path) -> str | None:
    recorded = Path(str(snapshot.get("path") or ""))
    try:
        expected_path = Path(os.path.abspath(expected_path))
        if Path(os.path.abspath(recorded)) != expected_path:
            return "recovery snapshot names a non-canonical config path"
    except (OSError, RuntimeError) as exc:
        return str(exc)
    path = expected_path
    try:
        directory_error = ensure_local_directory(path.parent)
        if directory_error:
            return directory_error
        if not snapshot.get("exists"):
            remove_local_tree(path)
            sync_directory(path.parent)
            return None
        if path_is_alias(path) or (path.exists() and not path.is_file()):
            remove_local_tree(path)
        make_windows_regular_file_writable(path)
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


def restore_experience_tree(snapshot: dict, vault: Path) -> str | None:
    """Restore protected Experience state while preserving opaque artifacts."""
    if not valid_experience_tree_snapshot(snapshot):
        return "Experience recovery snapshot is invalid"
    try:
        vault = Path(os.path.abspath(vault))
        root = vault / EXPERIENCE_ROOT_RELATIVE
        for directory in (vault.parent, vault):
            directory_error = ensure_local_directory(directory)
            if directory_error:
                return directory_error
        if path_is_alias(root):
            remove_path_alias(root)
        elif root.exists() and not root.is_dir():
            make_windows_regular_file_writable(root)
            root.unlink()
        root.mkdir(parents=True, exist_ok=True)
        if root.resolve() != root:
            return "Experience recovery root is non-canonical"

        expected_files = {
            relative for relative, row in snapshot.items()
            if row.get("kind") == "file"
        }
        expected_directories = {
            relative for relative, row in snapshot.items()
            if row.get("kind") == "directory"
        }
        current = sorted(
            local_recovery_paths(root, vault),
            key=lambda value: len(value.parts), reverse=True,
        )
        for path in current:
            relative = path.relative_to(vault).as_posix()
            if path_is_alias(path):
                remove_path_alias(path)
            elif path.is_file() and relative not in expected_files:
                make_windows_regular_file_writable(path)
                path.unlink()
            elif path.is_dir() and relative not in expected_directories:
                path.rmdir()
            elif not path.is_file() and not path.is_dir():
                make_windows_regular_file_writable(path)
                path.unlink()

        directories = sorted(
            (
                (relative, row) for relative, row in snapshot.items()
                if row.get("kind") == "directory"
            ),
            key=lambda item: len(Path(item[0]).parts),
        )
        for relative, row in directories:
            target = experience_recovery_target(vault, relative)
            if path_is_alias(target):
                remove_path_alias(target)
            elif target.exists() and not target.is_dir():
                make_windows_regular_file_writable(target)
                target.unlink()
            target.mkdir(parents=True, exist_ok=True)
            os.chmod(target, (target.stat().st_mode & 0o777) | 0o700)

        files = (
            (relative, row) for relative, row in sorted(snapshot.items())
            if row.get("kind") == "file"
        )
        for relative, row in files:
            target = experience_recovery_target(vault, relative)
            if path_is_alias(target.parent) or not target.parent.is_dir():
                return f"{relative}: recovery parent is not a local directory"
            raw = base64.b64decode(row["content_base64"], validate=True)
            if path_is_alias(target):
                remove_path_alias(target)
            if target.is_file():
                make_windows_regular_file_writable(target)
            elif target.is_dir():
                remove_local_tree(target)
            fd, temporary = tempfile.mkstemp(
                prefix=f".{target.name}.restore-", dir=str(target.parent),
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, int(row["mode"]))
                os.replace(temporary, target)
                sync_directory(target.parent)
            finally:
                Path(temporary).unlink(missing_ok=True)
        sync_directory(root)
        for relative, row in sorted(
            directories, key=lambda item: len(Path(item[0]).parts),
            reverse=True,
        ):
            os.chmod(
                experience_recovery_target(vault, relative), int(row["mode"]),
            )
        topology_problem = experience_topology_problem(vault)
        if topology_problem:
            return "restored tree has an unsafe topology: " + topology_problem
    except (OSError, ValueError, TypeError) as exc:
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


def vault_inventory(root: Path) -> dict[str, dict[str, int | str]]:
    result = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                children = sorted(entries, key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in children:
            path = Path(entry.path)
            rel = path.relative_to(root).as_posix()
            if author_owned_artifact_path(rel):
                continue
            # App session files, trash and the ignored policy-owned community
            # plugin projection are not authored vault state. Setup owns their
            # convergence; package refresh must not widen into an authored gate.
            if rel in {
                ".obsidian/workspace.json",
                ".obsidian/workspace-mobile.json",
                ".obsidian/community-plugins.json",
            } or rel.startswith((".obsidian/plugins/", ".trash/")):
                continue
            if entry.is_dir(follow_symlinks=False):
                if rel == EXPERIENCE_ROOT_RELATIVE or rel.startswith(
                        EXPERIENCE_ROOT_RELATIVE + "/"):
                    result[rel] = {
                        "kind": "directory",
                        "mode": entry.stat(follow_symlinks=False).st_mode & 0o777,
                    }
                pending.append(path)
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            identity = path.stat()
            result[rel] = {
                "kind": "file",
                "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "mode": identity.st_mode & 0o777,
                "nlink": identity.st_nlink,
                "device": identity.st_dev,
                "inode": identity.st_ino,
            }
    return result


def canonical_json(value: dict) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def guard_event_id(payload: dict) -> str:
    for key in ("tool_use_id", "tool_call_id", "event_id"):
        if payload.get(key):
            return str(payload[key])
    return ""


def guard_locator(payload: dict) -> str:
    """Separate equal host event IDs that occur in different directories."""
    value = {
        "session_id": str(payload.get("session_id") or "unknown"),
        "event": guard_event_id(payload),
        "cwd": guard_cwd(payload),
    }
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def guard_command_digest(payload: dict) -> str:
    command, error = canonical_shell_command(payload.get("tool_input", {}))
    if error:
        command = canonical_json({
            "command": payload.get("tool_input", {}).get("command"),
            "cmd": payload.get("tool_input", {}).get("cmd"),
        })
    return hashlib.sha256(command.encode("utf-8")).hexdigest()


def guard_cwd(payload: dict) -> str:
    try:
        return str(Path(str(payload.get("cwd") or ".")).resolve())
    except (OSError, RuntimeError):
        return str(payload.get("cwd") or ".")


def guard_binding(payload: dict) -> str:
    value = {
        "locator": guard_locator(payload),
        "command_sha256": guard_command_digest(payload),
        "cwd": guard_cwd(payload),
    }
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def inventory_path(payload: dict, project: Path | None = None) -> Path:
    session = str(payload.get("session_id") or "unknown")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session)[:80]
    project = project or shell_project(payload)
    name = f"{safe}-{guard_locator(payload)[:16]}.json"
    return (project / ".agentrof" / "agent-marketplace" / ".runtime"
            / "vault-inventory" / name)


def recovery_root() -> Path:
    user = getattr(os, "getuid", lambda: 0)()
    return Path(tempfile.gettempdir()) / f"agentrof-vault-hook-{user}"


def recovery_path(payload: dict) -> Path:
    return recovery_root() / f"{guard_locator(payload)}.json"


def recovery_path_for_post(payload: dict) -> Path:
    """Recover a host-drifted event only when correlation is unambiguous."""
    exact = recovery_path(payload)
    if exact.is_file():
        return exact
    session = str(payload.get("session_id") or "unknown")
    event = guard_event_id(payload)
    candidates = []
    try:
        paths = list(recovery_root().glob("*.json"))
    except OSError:
        return exact
    for path in paths:
        envelope, _, _ = load_json_file(path)
        state = envelope.get("state") if isinstance(envelope, dict) else None
        if isinstance(state, dict) and state.get("session_id") == session:
            candidates.append((path, state))
    if event:
        matching = [
            path for path, state in candidates
            if state.get("event_id") == event
        ]
    else:
        matching = [
            path for path, state in candidates
            if state.get("command_sha256") == guard_command_digest(payload)
            and state.get("cwd") == guard_cwd(payload)
        ]
    if len(matching) == 1:
        return matching[0]
    return exact


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
    if path_is_alias(root) or not root.is_dir():
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
        recorded = Path(os.path.abspath(str(snapshot.get("path") or "")))
        expected = Path(os.path.abspath(expected_path))
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


def load_recovery(payload: dict, path: Path) -> tuple[dict | None, str]:
    envelope, _, error = load_json_file(path)
    if envelope is None:
        return None, error
    state = envelope.get("state")
    if not isinstance(state, dict):
        return None, "recovery state is missing"
    digest = hashlib.sha256(canonical_json(state).encode("utf-8")).hexdigest()
    if envelope.get("state_sha256") != digest:
        return None, "recovery state checksum does not match"
    if state.get("session_id") != str(payload.get("session_id") or "unknown") \
            or not isinstance(state.get("command_sha256"), str) \
            or not isinstance(state.get("cwd"), str) \
            or not isinstance(state.get("event_id"), str):
        return None, "recovery event identity is invalid"
    project_value = state.get("project")
    if not isinstance(project_value, str) or not project_value:
        return None, "recovery project root is invalid"
    project = Path(project_value)
    try:
        if not project.is_absolute() or str(project.resolve()) != project_value:
            return None, "recovery project root is invalid"
    except (OSError, RuntimeError):
        return None, "recovery project root is invalid"
    expected_path = project / "workspace" / "config.json"
    if not valid_config_snapshot(state.get("config"), expected_path):
        return None, "recovery config snapshot is invalid"
    if not valid_experience_tree_snapshot(state.get("experience_tree")):
        return None, "recovery Experience tree snapshot is invalid"
    vault_value = state.get("vault")
    expected_vault = str(project / "workspace" / "docs")
    if vault_value not in {"", expected_vault}:
        return None, "recovery vault root is invalid"
    if not isinstance(state.get("config_writer_allowed"), bool):
        return None, "recovery config writer authorization is invalid"
    if not isinstance(state.get("application_writer_allowed"), bool):
        return None, "recovery application writer authorization is invalid"
    if not isinstance(state.get("application_writer_candidate"), bool):
        return None, "recovery application writer candidate is invalid"
    if state.get("binding") != guard_binding(payload):
        state = dict(state)
        state["config_writer_allowed"] = False
        state["application_writer_allowed"] = False
        state["application_writer_candidate"] = False
        return state, "recovery shell command binding changed"
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
    if not guard_event_id(payload):
        return deny(
            "the shell hook payload has no stable tool-call id; refusing a"
            " command whose recovery state could not be correlated"
        )
    root = shell_vault(payload)
    project = shell_project(payload).resolve()
    path = inventory_path(payload, project)
    config_path = project / "workspace" / "config.json"
    topology_problem = config_topology_problem(config_path)
    if topology_problem:
        return deny(
            "workspace/config.json cannot be safely snapshotted before Bash: "
            + topology_problem
        )
    config = read_config_snapshot(config_path)
    if config.get("read_error"):
        return deny(
            "workspace/config.json could not be snapshotted before Bash;"
            " refusing a command whose config effects could not be restored"
        )
    try:
        experience_tree = experience_tree_snapshot(root or config_path.parent / "docs")
    except OSError as exc:
        return deny(
            "Experience Design could not be snapshotted before Bash: "
            + str(exc)
        )
    snapshot_problem = experience_tree_snapshot_safety_problem(experience_tree)
    if snapshot_problem:
        return deny(
            "Experience Design cannot be safely snapshotted: "
            + snapshot_problem
        )
    application_writer_allowed = bool(
        root and sanctioned_application_writer(payload, root)
    )
    application_writer_candidate = bool(
        application_writer_allowed
        or root and sanctioned_application_writer(
            payload, root, allow_bare_runtime=True,
        )
    )
    value = {
        "vault": str(root) if root else "",
        "inventory": vault_inventory(root) if root else {},
        "config": config,
        "config_writer_allowed": sanctioned_config_writer(
            payload, config_path
        ),
        "experience_tree": experience_tree,
        "application_writer_allowed": application_writer_allowed,
        "application_writer_candidate": application_writer_candidate,
    }
    primary_text = canonical_json(value)
    state = {
        "binding": guard_binding(payload),
        "command_sha256": guard_command_digest(payload),
        "cwd": guard_cwd(payload),
        "event_id": guard_event_id(payload),
        "project": str(project),
        "session_id": str(payload.get("session_id") or "unknown"),
        "vault": value["vault"],
        "config": config,
        "config_writer_allowed": value["config_writer_allowed"],
        "experience_tree": experience_tree,
        "application_writer_allowed": value[
            "application_writer_allowed"
        ],
        "application_writer_candidate": value[
            "application_writer_candidate"
        ],
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
    recovery = recovery_path_for_post(payload)
    recovery_state, recovery_error = load_recovery(payload, recovery)
    project = (
        Path(recovery_state["project"])
        if recovery_state is not None
        else shell_project(payload).resolve()
    )
    path = inventory_path(payload, project)
    expected_config = project / "workspace" / "config.json"
    before, primary_raw, primary_error = load_json_file(path)
    integrity_error = recovery_error if recovery_state is not None else ""
    if recovery_state is not None:
        config_before = recovery_state["config"]
        writer_allowed = bool(recovery_state.get("config_writer_allowed"))
        experience_before = recovery_state["experience_tree"]
        application_writer_allowed = bool(
            recovery_state.get("application_writer_allowed")
        )
        application_writer_candidate = bool(
            recovery_state.get("application_writer_candidate")
        )
        root_value = str(recovery_state.get("vault") or "")
        primary_hash = hashlib.sha256(primary_raw or b"").hexdigest()
        if before is None:
            integrity_error = "project-local vault snapshot is missing or unreadable"
            writer_allowed = False
            application_writer_allowed = False
            application_writer_candidate = False
        elif primary_hash != recovery_state.get("primary_sha256"):
            integrity_error = "project-local vault snapshot was tampered with"
            before = None
            writer_allowed = False
            application_writer_allowed = False
            application_writer_candidate = False
    else:
        integrity_error = (
            "config recovery capsule is missing or unreadable"
            + (f": {recovery_error}" if recovery_error else "")
        )
        config_before = before.get("config") if isinstance(before, dict) else None
        if not valid_config_snapshot(config_before, expected_config):
            config_before = None
        # The project-local snapshot is recoverable evidence, not an
        # authorization credential. Only the separately checksummed recovery
        # capsule may carry a writer grant; losing it must fail closed.
        writer_allowed = False
        experience_before = (
            before.get("experience_tree") if isinstance(before, dict) else None
        )
        if not valid_experience_tree_snapshot(experience_before):
            experience_before = None
        application_writer_allowed = False
        application_writer_candidate = False
        root_value = str(before.get("vault", "")) \
            if isinstance(before, dict) else ""
        expected_vault = project / "workspace" / "docs"
        if root_value:
            try:
                root_matches = Path(os.path.abspath(root_value)) == expected_vault
            except (OSError, RuntimeError):
                root_matches = False
            if not root_matches:
                integrity_error += "; project-local vault root is unbound"
                root_value = str(expected_vault)
    retain_guard_state = False
    try:
        config_topology_changed = bool(
            config_topology_problem(expected_config)
        )
        vault_topology_changed = bool(
            root_value and (
                path_is_alias(Path(root_value).parent)
                or path_is_alias(Path(root_value))
                or experience_topology_problem(Path(root_value))
            )
        )
        config_violation = ""
        if isinstance(config_before, dict):
            config_after = (
                {}
                if config_topology_changed
                else read_config_snapshot(expected_config)
            )
            changed = config_topology_changed or (
                config_after.get("guard_hash")
                != config_before.get("guard_hash")
            ) or (
                config_after.get("mode") != config_before.get("mode")
            )
            if changed and (not writer_allowed or config_topology_changed):
                restore_error = restore_config(config_before, expected_config)
                if restore_error:
                    retain_guard_state = True
                    message = (
                        "Bash changed machine-managed workspace/config.json"
                        f" values; restore failed: {restore_error}; recovery"
                        " state was retained"
                    )
                else:
                    message = (
                        "Bash changed machine-managed workspace/config.json"
                        " values; the original config was restored. Use"
                        " setup_project.py or project_config.py"
                    )
                config_violation = message
        if integrity_error and not isinstance(before, dict):
            restore_error = (
                restore_experience_tree(experience_before, Path(root_value))
                if root_value and isinstance(experience_before, dict)
                else None
            )
            detail = f" ({primary_error})" if primary_error else ""
            if restore_error:
                retain_guard_state = True
                detail += f"; Experience restore failed: {restore_error}"
            elif root_value:
                detail += "; the pre-command Experience snapshot was restored"
            if config_violation:
                detail += f"; {config_violation}"
            return deny(
                f"Bash guard state failed closed: {integrity_error}{detail};"
                " protected effects could not be verified"
            )
        if not isinstance(before, dict):
            return deny("Bash vault inventory is missing or unreadable")
        if not root_value:
            if integrity_error:
                return deny(
                    f"Bash guard state failed closed: {integrity_error};"
                    " protected effects were restored or verified"
                )
            if config_violation:
                return deny(config_violation)
            return 0
        root = Path(root_value)

        def protected_message(message: str) -> str:
            details = [message]
            if config_violation:
                details.append(config_violation)
            if integrity_error:
                details.append(
                    f"Bash guard state failed closed: {integrity_error}"
                )
            return "; ".join(details)

        if vault_topology_changed:
            restore_error = (
                restore_experience_tree(experience_before, root)
                if isinstance(experience_before, dict)
                else "the Experience recovery snapshot is unavailable"
            )
            reason = (
                "Bash replaced the workspace, vault, or Experience subtree"
                " with an unsafe path topology"
            )
            if restore_error:
                retain_guard_state = True
                return deny(protected_message(
                    f"{reason}; restore failed: {restore_error}; recovery"
                    " state was retained"
                ))
            return deny(protected_message(
                f"{reason}; the original Experience tree was restored"
            ))

        hardlinks_after = experience_hardlink_paths(root)
        if hardlinks_after:
            restore_error = (
                restore_experience_tree(experience_before, root)
                if isinstance(experience_before, dict)
                else "the Experience recovery snapshot is unavailable"
            )
            reason = "Bash created a hard-link alias for Experience Design state"
            if restore_error:
                retain_guard_state = True
                return deny(protected_message(
                    f"{reason}; restore failed: {restore_error}; recovery"
                    " state was retained"
                ))
            return deny(protected_message(
                f"{reason}; the original Experience tree was restored"
            ))
        old = before.get("inventory", {})
        new = vault_inventory(root) if root.is_dir() else {}
        changed = sorted(key for key in set(old) | set(new)
                         if old.get(key) != new.get(key))
        if not changed:
            if integrity_error or config_violation:
                return deny(protected_message(
                    "protected shell effects were restored or verified"
                ))
            return 0
        machine_changes = [
            key for key in changed
            if key.startswith("experience-design/")
            and (
                "/_generated/" in f"/{key}/"
                or "/_ledger/" in f"/{key}/"
            )
        ]
        candidate_is_valid = False
        if (
            machine_changes
            and not application_writer_allowed
            and application_writer_candidate
        ):
            try:
                candidate_is_valid = valid_application_writer_result(
                    payload, root, changed,
                )
            except Exception:
                # Candidate attestation is an authorization boundary. Any
                # unexpected compiler or filesystem failure revokes the grant
                # so the protected pre-command tree is restored below.
                candidate_is_valid = False
        if machine_changes and not (
                application_writer_allowed or candidate_is_valid):
            restore_error = (
                restore_experience_tree(experience_before, root)
                if isinstance(experience_before, dict)
                else "the Experience recovery snapshot is unavailable"
            )
            detail = ", ".join(machine_changes)
            if restore_error:
                retain_guard_state = True
                return deny(protected_message(
                    "Bash changed compiler-owned Experience state "
                    f"({detail}); restore failed: {restore_error}; recovery"
                    " state was retained"
                ))
            return deny(protected_message(
                "Bash changed compiler-owned Experience state outside the "
                "official lifecycle or left compiler validation red; the "
                "original Experience tree was "
                f"restored ({detail})"
            ))
        if integrity_error:
            return deny(protected_message(
                "protected shell effects were restored or verified"
            ))
        deleted = [key for key in changed if key not in new]
        experience_non_notes = [
            key for key in changed
            if key.startswith("experience-design/") and not key.endswith(".md")
        ]
        checks = [None] if deleted or experience_non_notes else [
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
                return deny(protected_message(
                    "Bash changed vault inventory and left its scoped vault"
                    " check red; repair or restore the changed paths"))
        if config_violation:
            return deny(config_violation)
        return 0
    except Exception:
        retain_guard_state = True
        raise
    finally:
        if not retain_guard_state:
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
    if payload.get("shell_command_error"):
        if mode == "post" and payload.get("tool_name") == "Bash":
            return shell_verify(payload)
        return deny(
            "the shell payload is ambiguous ("
            + str(payload["shell_command_error"])
            + "); the vault guard fails closed."
        )
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
