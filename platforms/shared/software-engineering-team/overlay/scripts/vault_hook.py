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
import subprocess
import sys
import tempfile
import time
import unicodedata
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
SANCTIONED_PROJECT_CONFIG_COMMANDS = {"set"}
SANCTIONED_SHELL_ASSIGNMENTS = {"PYTHONDONTWRITEBYTECODE": "1"}
READ_ONLY_GIT_STATUS_ARGS = {
    "--ahead-behind", "--branch", "--ignored", "--long",
    "--no-ahead-behind", "--no-column", "--no-renames", "--porcelain",
    "--porcelain=v1", "--porcelain=v2", "--renames", "--short",
    "--show-stash", "--untracked-files=all", "--untracked-files=no",
    "--untracked-files=normal", "-b", "-s", "-uno", "-uall", "-unormal",
    "-z",
}
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
EXPERIENCE_APPLICATION_REL = "experience-design/artifacts/application.html"
EXPERIENCE_APPLICATION_MAP_RE = re.compile(
    r"^experience-design/experiences/(?!exp-)[a-z0-9]+(?:-[a-z0-9]+)*/"
    r"artifacts/application-map\.json$")
APPLICATION_MACHINE_META_NAMES = (
    "experience-application-contract-version",
    "experience-application-status",
    "experience-application-revision",
    "experience-application-proposal-hash",
    "experience-application-source-hash",
    "experience-application-package-set-hash",
    "experience-application-coverage-hash",
    "experience-application-hash",
    "experience-application-approved-at-utc",
    "experience-application-runtime-sha256",
    "design-system-package-hash",
    "design-system-master-revision",
    "design-system-master-source-hash",
)
APPLICATION_MACHINE_META_RE = re.compile(
    r"<meta\b(?=[^>]*\bname\s*=\s*[\"'](?:"
    + "|".join(re.escape(value) for value in APPLICATION_MACHINE_META_NAMES)
    + r")[\"'])[^>]*>",
    re.IGNORECASE | re.DOTALL,
)
APPLICATION_TOKEN_BLOCK_RE = re.compile(
    r"/\* application:design-tokens:start \*/.*?"
    r"/\* application:design-tokens:end \*/",
    re.DOTALL,
)
APPLICATION_RUNTIME_RE = re.compile(
    r"<script\b(?=[^>]*\bid\s*=\s*[\"']experience-application-runtime"
    r"[\"'])[^>]*>.*?</script(?:[\t\n\f\r />][^<>]*)?>",
    re.IGNORECASE | re.DOTALL,
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


def is_experience_application_surface(rel: str) -> bool:
    return (rel == EXPERIENCE_APPLICATION_REL
            or EXPERIENCE_APPLICATION_MAP_RE.fullmatch(rel) is not None)


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
        if any(candidate.is_symlink() for candidate in candidates):
            return (
                "symlink aliases to Experience Design paths are"
                f" forbidden; use exactly workspace/docs/{expected}"
            )
    except OSError:
        return (
            "Experience Design path identity could not be verified;"
            " refusing the write"
        )
    return ""


def experience_hardlink_paths(vault: Path) -> list[str]:
    """Return canonical Experience files that share an inode.

    Symlinks are handled by the existing topology guards. A regular file with
    more than one link is equally unsafe because a write through the other
    name bypasses the canonical path and its lifecycle checks.
    """
    root = vault / "experience-design"
    if not root.is_dir() or root.is_symlink():
        return []
    violations: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
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
    """Capture recoverable bytes and inode identity for Experience files."""
    root = vault / "experience-design"
    if not root.exists() and not root.is_symlink():
        return {}
    if root.is_symlink() or not root.is_dir():
        raise OSError(
            "experience-design: root is not one regular directory"
        )
    snapshot: dict[str, dict] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(vault).as_posix()
        if path.is_symlink():
            raise OSError(f"{relative}: path is symlinked")
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
            stat = path.stat()
        except OSError as exc:
            raise OSError(
                f"{relative}: file identity is unreadable: {exc}"
            ) from exc
        snapshot[relative] = {
            "content_base64": base64.b64encode(raw).decode("ascii"),
            "content_sha256": hashlib.sha256(raw).hexdigest(),
            "mode": stat.st_mode & 0o777,
            "nlink": stat.st_nlink,
            "device": stat.st_dev,
            "inode": stat.st_ino,
        }
    return snapshot


def experience_tree_snapshot_safety_problem(value: object) -> str:
    if not isinstance(value, dict):
        return "snapshot root is not an object"
    for relative, row in value.items():
        if not isinstance(relative, str):
            return f"snapshot contains a non-text path identity: {relative!r}"
        if not relative.startswith("experience-design/"):
            return f"{relative}: path escapes the Experience Design subtree"
        if normalize_posix(relative) != relative:
            return f"{relative}: path must use canonical POSIX segments"
        if (
            not isinstance(row, dict)
            or set(row) != {
                "content_base64", "content_sha256", "mode", "nlink",
                "device", "inode",
            }
        ):
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


def noncanonical_experience_snapshot_paths(value: dict[str, dict]) -> list[str]:
    """Return canonical spelling violations without rejecting recovery data."""
    return sorted(
        relative for relative in value
        if (
            unicodedata.normalize("NFC", relative) != relative
            or relative.casefold() != relative
        )
    )


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


def html_attribute(tag: str, name: str) -> str:
    match = re.search(
        rf"\b{re.escape(name)}\s*=\s*([\"'])(.*?)\1", tag,
        re.IGNORECASE | re.DOTALL,
    )
    return match.group(2) if match else ""


def application_meta_value(text: str, name: str) -> str:
    for match in APPLICATION_MACHINE_META_RE.finditer(text):
        tag = match.group(0)
        if html_attribute(tag, "name").casefold() == name.casefold():
            return html_attribute(tag, "content")
    return ""


def application_status(text: str) -> str:
    return application_meta_value(
        text, "experience-application-status"
    ).casefold()


def application_machine_spans(text: str) -> list[tuple[int, int]]:
    spans = [match.span() for match in APPLICATION_MACHINE_META_RE.finditer(text)]
    spans.extend(match.span() for match in APPLICATION_TOKEN_BLOCK_RE.finditer(text))
    spans.extend(match.span() for match in APPLICATION_RUNTIME_RE.finditer(text))
    return sorted(spans)


def application_machine_projection(text: str) -> tuple[str, ...]:
    return tuple(text[start:end] for start, end in application_machine_spans(text))


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


def application_machine_guard(written: dict, file_path: str) -> int:
    try:
        disk = Path(file_path).read_text(encoding="utf-8")
    except OSError:
        disk = ""
    proposed = rendered_edit(written, disk)
    current_status = application_status(disk)
    proposed_status = application_status(proposed) if proposed is not None else ""
    if current_status == "approved" and proposed != disk:
        return deny(
            "approved Experience application content is immutable; open an"
            " application revision through experience_compile.py")
    protected = {current_status, proposed_status} & {
        "draft", "in_review", "approved",
    }
    if not protected:
        return 0
    if (proposed is not None
            and application_machine_projection(proposed)
            != application_machine_projection(disk)):
        return deny(
            "Experience application metadata, Design System token block and"
            " fixed runtime are machine-managed; use experience_compile.py")
    old = str(written.get("old_string") or "")
    combined = old + "\n" + str(written.get("new_string") or "")
    if (APPLICATION_MACHINE_META_RE.search(combined)
            or APPLICATION_TOKEN_BLOCK_RE.search(combined)
            or APPLICATION_RUNTIME_RE.search(combined)
            or edit_overlaps_spans(disk, old,
                                   application_machine_spans(disk))):
        return deny(
            "the edit overlaps machine-managed Experience application"
            " metadata, Design System tokens or fixed runtime")
    return 0


def pre(payload: dict) -> int:
    if "file_targets" not in payload:
        payload = normalize(payload)
    project_vault = shell_project(payload) / "workspace" / "docs"
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
        if rel == EXPERIENCE_APPLICATION_REL:
            application_code = application_machine_guard(written, file_path)
            if application_code:
                return application_code
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


def application_checker_path() -> Path | None:
    candidates = (
        Path(__file__).resolve().parent / "experience_application_check.py",
        Path(vault_check.__file__).resolve().parent
        / "experience_application_check.py",
    )
    return next((path for path in candidates if path.is_file()), None)


def run_application_post_check(root: Path, *, force_gate: bool = False) -> int:
    checker = application_checker_path()
    if checker is None:
        return deny(
            "Experience application checker is unavailable; refusing to"
            " leave a protected application surface unchecked"
        )
    application = root / EXPERIENCE_APPLICATION_REL
    status = ""
    if application.is_file():
        try:
            status = application_status(application.read_text(encoding="utf-8"))
        except OSError as exc:
            return deny(f"Experience application post-write check failed: {exc}")
    mode = "--gate" if force_gate or status == "approved" else "--authoring"
    try:
        completed = subprocess.run(
            [sys.executable, str(checker), "check", "--root",
             str(root / "experience-design"), mode, "--json"],
            capture_output=True, text=True, check=False, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return deny(f"Experience application post-write check failed: {exc}")
    if completed.returncode:
        if completed.stdout:
            sys.stderr.write(completed.stdout)
        if completed.stderr:
            sys.stderr.write(completed.stderr)
        return deny(
            "Experience application checker found a global violation in"
            f" {mode[2:]} mode; repair the application and package maps"
            " before continuing")
    return 0


def post(payload: dict) -> int:
    if "file_targets" not in payload:
        payload = normalize(payload)
    project_vault = shell_project(payload) / "workspace" / "docs"
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
    if is_experience_application_surface(rel):
        return run_application_post_check(root)
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


def trusted_python_command(value: str, cwd: Path) -> bool:
    if not PYTHON_COMMAND_RE.fullmatch(Path(value).name) or "$" in value:
        return False
    if Path(value).is_absolute() or "/" in value:
        candidate = _cli_path(value, cwd)
    else:
        resolved = shutil.which(value)
        candidate = Path(resolved).resolve() if resolved else None
    try:
        return candidate is not None and candidate.resolve() \
            == Path(sys.executable).resolve()
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


def direct_shell_tokens(payload: dict) -> tuple[list[str], Path] | None:
    """Parse one direct command with only the closed safe assignment set."""
    command = str(payload.get("tool_input", {}).get("command") or "").strip()
    if not command or has_shell_composition(command):
        return None
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        return None
    while tokens and SHELL_ASSIGNMENT_RE.fullmatch(tokens[0]):
        name, value = tokens.pop(0).split("=", 1)
        if SANCTIONED_SHELL_ASSIGNMENTS.get(name) != value:
            return None
    return tokens, Path(str(payload.get("cwd") or ".")).resolve()


def trusted_system_command(value: str, cwd: Path, name: str) -> bool:
    """Reject project-local executable shadowing for a named diagnostic."""
    if "$" in value or Path(value).name not in {name, name + ".exe"}:
        return False
    if Path(value).is_absolute() or "/" in value or "\\" in value:
        candidate = _cli_path(value, cwd)
    else:
        resolved = shutil.which(value)
        candidate = Path(resolved).resolve() if resolved else None
    if candidate is None or not candidate.is_file():
        return False
    project = shell_project({"cwd": str(cwd)}).resolve()
    try:
        candidate.relative_to(project)
    except ValueError:
        return True
    return False


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


def scoped_experience_package(value: str, cwd: Path, root: Path) -> bool:
    target = _cli_path(value, cwd)
    if target is None:
        return False
    if target.name == "experience.md":
        target = target.parent
    try:
        relative = target.relative_to(root / "experiences")
    except ValueError:
        return False
    return len(relative.parts) == 1 and bool(relative.name)


def sanctioned_bash_diagnostic(payload: dict, vault: Path | None) -> bool:
    """Recognize direct diagnostics that remain safe without a snapshot."""
    parsed = direct_shell_tokens(payload)
    if parsed is None:
        return False
    tokens, cwd = parsed
    if not tokens:
        return False
    if trusted_system_command(tokens[0], cwd, "pwd"):
        return all(value in {"-L", "-P"} for value in tokens[1:])
    if trusted_system_command(tokens[0], cwd, "git"):
        return (
            len(tokens) >= 2
            and tokens[1] == "status"
            and all(value in READ_ONLY_GIT_STATUS_ARGS for value in tokens[2:])
        )
    if len(tokens) < 3 or not trusted_python_command(tokens[0], cwd):
        return False
    project = shell_project(payload).resolve()
    if _installed_script_path(
            tokens[1], cwd, "setup_project.py") is not None:
        command_name = tokens[2]
        values = {"--project-root"}
        flags = {"--json"}
        if command_name not in {"inspect", "check"}:
            return False
        options = parsed_options(tokens[3:], values, flags)
        target = _cli_path(
            options.get("--project-root", "") if options else "", cwd
        )
        return (
            options is not None
            and target == project
        )
    if vault is None or _installed_script_path(
            tokens[1], cwd, "experience_compile.py") is None:
        return False
    command_name = tokens[2]
    experience_root = vault / "experience-design"
    if command_name in {"check", "status"}:
        flags = {"--gate", "--json"} if command_name == "check" else set()
        options = parsed_options(
            tokens[3:], {"--experience-root"}, flags
        )
        return bool(
            options
            and scoped_experience_package(
                options.get("--experience-root", ""), cwd, experience_root
            )
        )
    if command_name in {"check-application", "application-status"}:
        flags = ({"--gate", "--json"}
                 if command_name == "check-application" else set())
        options = parsed_options(tokens[3:], {"--root"}, flags)
        target = _cli_path(
            options.get("--root", "") if options else "", cwd
        )
        return options is not None and target == experience_root.resolve()
    return False


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
    cwd = Path(str(payload.get("cwd") or ".")).resolve()
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
        target = _cli_path(_option_value(args, "--config"), cwd)
        return target == config_path.resolve()
    return False


def sanctioned_application_writer(payload: dict, vault: Path) -> bool:
    """Recognize only direct invocations of application lifecycle writers."""
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
    cwd = Path(str(payload.get("cwd") or ".")).resolve()
    if len(tokens) < 3 or not trusted_python_command(tokens[0], cwd):
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


def read_application_snapshot(vault: Path) -> dict:
    path = vault / EXPERIENCE_APPLICATION_REL
    if not path.exists() and not path.is_symlink():
        return {
            "path": str(path), "exists": False, "status": "",
            "source_hash": "", "content_sha256": "", "text": "",
            "mode": 0, "nlink": 0, "device": 0, "inode": 0,
        }
    try:
        text = path.read_text(encoding="utf-8")
        stat = path.stat()
    except (OSError, UnicodeError) as exc:
        return {"path": str(path), "exists": True, "read_error": str(exc)}
    return {
        "path": str(path),
        "exists": True,
        "status": application_status(text),
        "source_hash": application_meta_value(
            text, "experience-application-source-hash"
        ),
        "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text": text,
        "mode": stat.st_mode & 0o777,
        "nlink": stat.st_nlink,
        "device": stat.st_dev,
        "inode": stat.st_ino,
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


def restore_experience_tree(snapshot: dict, vault: Path) -> str | None:
    """Restore the exact pre-Bash Experience bytes with fresh inodes."""
    if not valid_experience_tree_snapshot(snapshot):
        return "Experience recovery snapshot is invalid"
    root = vault / "experience-design"
    try:
        vault = vault.resolve()
        if root.is_symlink():
            root.unlink()
        elif root.exists() and not root.is_dir():
            root.unlink()
        root.mkdir(parents=True, exist_ok=True)
        if root.resolve() != vault / "experience-design":
            return "Experience recovery root is non-canonical"

        expected = set(snapshot)
        current = sorted(root.rglob("*"), key=lambda value: len(value.parts),
                         reverse=True)
        for path in current:
            relative = path.relative_to(vault).as_posix()
            if path.is_symlink():
                path.unlink()
            elif path.is_file() and relative not in expected:
                path.unlink()

        for relative, row in sorted(snapshot.items()):
            target = vault.joinpath(*relative.split("/"))
            cursor = vault
            for segment in Path(relative).parts[:-1]:
                cursor /= segment
                if cursor.is_symlink():
                    cursor.unlink()
                elif cursor.exists() and not cursor.is_dir():
                    cursor.unlink()
                cursor.mkdir(exist_ok=True)
            raw = base64.b64decode(row["content_base64"], validate=True)
            if target.is_symlink() or target.is_file():
                pass
            elif target.is_dir():
                shutil.rmtree(target)
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
        stat = path.stat()
        result[rel] = {
            "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "nlink": stat.st_nlink,
            "device": stat.st_dev,
            "inode": stat.st_ino,
        }
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


def valid_application_snapshot(snapshot: object, vault: Path) -> bool:
    if not isinstance(snapshot, dict):
        return False
    try:
        recorded = Path(str(snapshot.get("path") or "")).resolve(strict=False)
        expected = (vault / EXPERIENCE_APPLICATION_REL).resolve(strict=False)
    except (OSError, RuntimeError):
        return False
    if recorded != expected or not isinstance(snapshot.get("exists"), bool):
        return False
    if snapshot.get("read_error"):
        return False
    for field in ("status", "source_hash", "content_sha256", "text"):
        if not isinstance(snapshot.get(field), str):
            return False
    for field in ("mode", "nlink", "device", "inode"):
        if not isinstance(snapshot.get(field), int):
            return False
    if not snapshot["exists"]:
        return not any(
            snapshot[field]
            for field in (
                "status", "source_hash", "content_sha256", "text", "mode",
                "nlink", "device", "inode",
            )
        )
    encoded = snapshot["text"].encode("utf-8")
    return (
        bool(re.fullmatch(r"[0-9a-f]{64}", snapshot["content_sha256"]))
        and hashlib.sha256(encoded).hexdigest() == snapshot["content_sha256"]
        and 0 <= snapshot["mode"] <= 0o777
        and snapshot["nlink"] == 1
        and snapshot["device"] >= 0
        and snapshot["inode"] >= 0
        and application_status(snapshot["text"]) == snapshot["status"]
        and application_meta_value(
            snapshot["text"], "experience-application-source-hash"
        ) == snapshot["source_hash"]
    )


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
    expected_vault = expected_path.parent / "docs"
    if not valid_application_snapshot(
            state.get("application"), expected_vault):
        return None, "recovery application snapshot is invalid"
    if not valid_experience_tree_snapshot(state.get("experience_tree")):
        return None, "recovery Experience tree snapshot is invalid"
    application = state["application"]
    tree = state["experience_tree"]
    application_row = tree.get(EXPERIENCE_APPLICATION_REL)
    if application["exists"]:
        if not isinstance(application_row, dict):
            return None, "recovery tree omits the application snapshot"
        try:
            decoded = base64.b64decode(
                application_row["content_base64"], validate=True,
            ).decode("utf-8")
        except (ValueError, UnicodeError):
            return None, "recovery application bytes are invalid"
        if (
            decoded != application["text"]
            or application_row["mode"] != application["mode"]
            or application_row["nlink"] != application["nlink"]
            or application_row["device"] != application["device"]
            or application_row["inode"] != application["inode"]
        ):
            return None, "recovery application and tree snapshots differ"
    elif application_row is not None:
        return None, "recovery tree unexpectedly contains an application"
    if not isinstance(state.get("application_writer_allowed"), bool):
        return None, "recovery application writer authorization is invalid"
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
    application_vault = root or config_path.parent / "docs"
    application = read_application_snapshot(application_vault)
    if application.get("read_error"):
        return deny(
            "Experience application could not be snapshotted before Bash;"
            " refusing a command whose application effects cannot be"
            " attributed"
        )
    try:
        experience_tree = experience_tree_snapshot(application_vault)
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
    noncanonical_paths = noncanonical_experience_snapshot_paths(
        experience_tree
    )
    if (
        root is not None
        and application.get("status") == "approved"
        and not noncanonical_paths
    ):
        strict_code = run_application_post_check(root, force_gate=True)
        if strict_code:
            return strict_code
    value = {
        "vault": str(root) if root else "",
        "inventory": vault_inventory(root) if root else {},
        "config": config,
        "config_writer_allowed": sanctioned_config_writer(
            payload, config_path
        ),
        "application": application,
        "experience_tree": experience_tree,
        "application_writer_allowed": bool(
            root and sanctioned_application_writer(payload, root)
        ),
    }
    primary_text = canonical_json(value)
    state = {
        "binding": guard_binding(payload),
        "config": config,
        "config_writer_allowed": value["config_writer_allowed"],
        "application": application,
        "experience_tree": experience_tree,
        "application_writer_allowed": value[
            "application_writer_allowed"
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
        application_before = recovery_state["application"]
        experience_before = recovery_state["experience_tree"]
        application_writer_allowed = bool(
            recovery_state.get("application_writer_allowed")
        )
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
        application_before = (
            before.get("application") if isinstance(before, dict) else None
        )
        experience_before = (
            before.get("experience_tree") if isinstance(before, dict) else None
        )
        expected_vault = expected_config.parent / "docs"
        if not valid_application_snapshot(
                application_before, expected_vault):
            application_before = None
        if not valid_experience_tree_snapshot(experience_before):
            experience_before = None
        application_writer_allowed = bool(
            before.get("application_writer_allowed")
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
                        " setup_project.py or project_config.py"
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
        application_after = read_application_snapshot(root)
        if application_after.get("read_error"):
            return deny(
                "Bash left the Experience application unreadable; refusing"
                " to accept unverifiable application state"
            )
        application_changed = (
            not isinstance(application_before, dict)
            or application_after != application_before
        )
        hardlinks_after = experience_hardlink_paths(root)
        if hardlinks_after or (
                application_changed and not application_writer_allowed):
            restore_error = (
                restore_experience_tree(experience_before, root)
                if isinstance(experience_before, dict)
                else "the Experience recovery snapshot is unavailable"
            )
            reason = (
                "Bash created a hard-link alias for Experience Design state"
                if hardlinks_after
                else "Bash changed the canonical Experience application "
                     "outside an authorized experience_compile.py lifecycle "
                     "command"
            )
            if restore_error:
                return deny(f"{reason}; restore failed: {restore_error}")
            return deny(f"{reason}; the original Experience tree was restored")
        old = before.get("inventory", {})
        new = vault_inventory(root) if root.is_dir() else {}
        changed = sorted(key for key in set(old) | set(new)
                         if old.get(key) != new.get(key))
        if not changed:
            return 0
        machine_changes = [
            key for key in changed
            if key.startswith("experience-design/")
            and (
                "/_generated/" in f"/{key}"
                or "/_ledger/" in f"/{key}"
            )
        ]
        if machine_changes and not application_writer_allowed:
            restore_error = (
                restore_experience_tree(experience_before, root)
                if isinstance(experience_before, dict)
                else "the Experience recovery snapshot is unavailable"
            )
            detail = ", ".join(machine_changes)
            if restore_error:
                return deny(
                    "Bash changed compiler-owned Experience state "
                    f"({detail}); restore failed: {restore_error}"
                )
            return deny(
                "Bash changed compiler-owned Experience state outside the "
                "official lifecycle; the original Experience tree was "
                f"restored ({detail})"
            )
        if (application_changed
                or any(is_experience_application_surface(key)
                       for key in changed)):
            application_code = run_application_post_check(root)
            if application_code:
                return application_code
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
        if sanctioned_bash_diagnostic(payload, shell_vault(payload)):
            return 0
        if mode == "pre":
            code = registration_guard(payload)
            return code if code else shell_snapshot(payload)
        return shell_verify(payload)
    if payload.get("tool_name") not in ("Write", "Edit"):
        return 0
    return pre(payload) if mode == "pre" else post(payload)


if __name__ == "__main__":
    sys.exit(main())
