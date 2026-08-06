#!/usr/bin/env python3
"""Per-write vault consistency hook for the workspace docs tree.

Two moments, one law (the obsidian-vault skill):

- pre (PreToolUse, Write|Edit): content-shape denials BEFORE the write
  lands. A vault-internal relative markdown link, an unescaped alias pipe
  in a table-row wikilink, or an inline flow-list tags:/aliases: value
  never reaches disk, regardless of which agent writes. Content-only
  regexes, zero vault I/O. The one exception is workspace/config.json:
  its designation keys are machine-managed with a single writer (the
  reconcile-designations verb, a subprocess this hook never sees), so
  ANY tool-level change to them is denied, which needs one disk read.
- post (PostToolUse, Write|Edit): after a write lands under the vault,
  run vault_check's --changed fast path and surface its findings to the
  writing session immediately, so link and metadata duties are repaired
  in-session instead of at a distant gate. Gates stay the hard barrier.
- register (SessionStart): record this plugin's install root in the
  shared plugin_roots registry the marketplace_run dispatcher resolves
  from; env-free (the root comes from this file's own location).

The normalize shim gives both moments one payload shape (canonical tool
name, per-file write targets). File operations through the shell (moves,
deletes) bypass Write/Edit hooks by nature; the next --changed write or
gate-time vault_check surfaces them. Stdlib only.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
from pathlib import Path

import team_guard
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

# Machine-managed config keys with a single sanctioned writer (the
# vault_check.py reconcile-designations verb). The verb writes via
# subprocess and never traverses PreToolUse, so the deny below needs no
# handshake: no Write/Edit call ever changes these keys legitimately.
CONFIG_GUARD_KEYS = (
    "project_origin", "doc_type_designations", "doc_type_designation_history",
)

CONFIG_GUARD_MESSAGE = (
    "project_origin, doc_type_designations and its history ledger are"
    " machine-managed; their single writers are setup project_config.py, PMO"
    " project classify-origin and"
    " vault_check.py reconcile-designations, driven by setup/configure."
    " Setup and organize-docs mint through the same verb."
    " Direct edits desynchronize the project contract.")

# A relative markdown link that is not http(s)/mailto/anchor/root form.
MD_LINK_RE = re.compile(
    r"(?<!\!)\[[^\]]*\]\((?!https?://|mailto:|#|/)([^)\s]+?)"
    r"(?:\s+\"[^\"]*\")?\)")
INLINE_FLOW_LIST_RE = re.compile(r"^\s*(tags|aliases):\s*\[", re.MULTILINE)
FENCE_RE = re.compile(r"^\s*```")
EXPERIENCE_MACHINE_FIELD_RE = re.compile(
    r"(?m)^\s*(approved_at|approval_revision|registry_hash|source_hash|"
    r"stamped_at):")
EXPERIENCE_PATHS = (
    re.compile(r"^experience-design/experience\.md$"),
    re.compile(r"^experience-design/programs/prg-[0-9]+/program\.md$"),
    re.compile(r"^experience-design/programs/prg-[0-9]+/releases/rel-[0-9]+/release\.md$"),
    re.compile(r"^experience-design/programs/prg-[0-9]+/releases/rel-[0-9]+/(?:spaces/[a-z0-9-]+/)?space\.md$"),
    re.compile(r"^experience-design/programs/prg-[0-9]+/releases/rel-[0-9]+/(?:spaces/[a-z0-9-]+/)?(?:domains/[a-z0-9-]+/)+domain\.md$"),
    re.compile(r"^experience-design/programs/prg-[0-9]+/releases/rel-[0-9]+/(?:spaces/[a-z0-9-]+/(?:domains/[a-z0-9-]+/)*)?(?:journeys/[a-z0-9-]+-journey|screens/[a-z0-9-]+-screen|flows/[a-z0-9-]+-flows|reviews/[a-z0-9-]+-review)\.md$"),
)


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


def data_dir() -> Path:
    return team_guard.data_dir()


def register() -> int:
    """Register through the PMO-owned launcher when it is available."""
    team_guard.register(team_guard.plugin_name())
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
    managed = {"team_id", "project_key", *CONFIG_GUARD_KEYS}
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
    """Deny any tool-level change to the guarded designation keys, mint
    included: introduction over an absent key is a change. Writes are
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


def pre(payload: dict) -> int:
    if "file_targets" not in payload:
        payload = normalize(payload)
    for written in payload.get("file_targets", []):
        code = pre_target(written)
        if code:
            return code
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
    if rel.startswith("experience-design/"):
        if "/_generated/" in f"/{rel}":
            return deny("Experience Design _generated files are compiler-owned; run experience_compile.py render")
        if "/artifacts/" in rel and rel.endswith(".html"):
            return deny("approved Experience Design artifacts are immutable through Write/Edit; promote a new package through experience_compile.py")
        if rel.endswith(".md") and not any(pattern.fullmatch(rel) for pattern in EXPERIENCE_PATHS):
            return deny(f"invalid Experience Design filename or path '{rel}'; use compiler init/stub commands")
        content = written_content(written) + "\n" + str(written.get("old_string") or "")
        if EXPERIENCE_MACHINE_FIELD_RE.search(content):
            return deny("Experience Design approval, revision hash and timestamp fields are machine-managed; use render/stamp")
        claims = Path(file_path).resolve().parents
        workspace = next((value for value in claims if value.name == "workspace"), None)
        if workspace is not None:
            claim_file = workspace / "experience-design-work" / "node-claims.json"
            try:
                claim_map = json.loads(claim_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                claim_map = {}
            current = os.environ.get("CODEX_SESSION_ID") or os.environ.get("CLAUDE_SESSION_ID") or ""
            owner = claim_map.get(rel) if isinstance(claim_map, dict) else None
            if owner and owner != current:
                return deny(f"Experience Design node is claimed by another session: {rel}")
    if not rel.endswith(".md"):
        return 0
    content = written_content(written)
    if INLINE_FLOW_LIST_RE.search(content):
        return deny(
            "tags:/aliases: as an inline flow list; the vault contract is a"
            " block list (key:, then one '- item' line per value).")
    for lineno, line in outside_fences(content):
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
            "check", "--vault", str(root), "--changed", rel,
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
    if payload.get("tool_name") not in ("Write", "Edit"):
        return 0
    return pre(payload) if mode == "pre" else post(payload)


if __name__ == "__main__":
    sys.exit(main())
