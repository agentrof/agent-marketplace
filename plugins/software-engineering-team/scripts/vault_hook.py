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
  shared plugin_roots registry the agentrof_run dispatcher resolves
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
import tempfile
from pathlib import Path

import vault_check

VAULT_SEGMENTS = ("workspace", "docs")

# Tool-name vocabulary -> the canonical pair this hook reasons in.
TOOL_NAME_CANON = {
    "Write": "Write",
    "Edit": "Edit", "MultiEdit": "Edit",
}

# Machine-managed config keys with a single sanctioned writer (the
# vault_check.py reconcile-designations verb). The verb writes via
# subprocess and never traverses PreToolUse, so the deny below needs no
# handshake: no Write/Edit call ever changes these keys legitimately.
CONFIG_GUARD_KEYS = ("doc_type_designations", "doc_type_designation_history")

CONFIG_GUARD_MESSAGE = (
    "doc_type_designations and its history ledger are machine-managed;"
    " their single writer is vault_check.py reconcile-designations, driven"
    " by the configure entry (build-docs-vault and setup mint through the"
    " same verb). Hand edits desynchronize every vault title.")

# A relative markdown link that is not http(s)/mailto/anchor/root form.
MD_LINK_RE = re.compile(
    r"(?<!\!)\[[^\]]*\]\((?!https?://|mailto:|#|/)([^)\s]+?)"
    r"(?:\s+\"[^\"]*\")?\)")
INLINE_FLOW_LIST_RE = re.compile(r"^\s*(tags|aliases):\s*\[", re.MULTILINE)
FENCE_RE = re.compile(r"^\s*```")


def read_payload() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def normalize(payload: dict) -> dict:
    """One canonical payload shape: tool_name mapped into Write/Edit,
    the write target expanded under 'file_targets'."""
    out = dict(payload) if isinstance(payload, dict) else {}
    tool_input = out.get("tool_input")
    tool_input = dict(tool_input) if isinstance(tool_input, dict) else {}
    tool = TOOL_NAME_CANON.get(str(out.get("tool_name", "")),
                               str(out.get("tool_name", "")))
    targets: list[dict] = []
    if tool in ("Write", "Edit"):
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
    override = os.environ.get("AGENTROF_HOME", "").strip()
    return Path(override) if override else Path.home() / ".agentrof"


def register() -> int:
    """Record this plugin's root in the shared plugin_roots registry;
    a bookkeeping hook, so it never takes a session down."""
    try:
        root = Path(__file__).resolve().parents[1]
        registry_file = data_dir() / "plugin_roots.json"
        registry_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            registry = json.loads(registry_file.read_text(encoding="utf-8"))
        except Exception:
            registry = {}
        if not isinstance(registry, dict):
            registry = {}
        registry.setdefault("schema_version", 1)
        version = ""
        try:
            version = json.loads(
                (root / ".claude-plugin" / "plugin.json")
                .read_text(encoding="utf-8")).get("version", "")
        except Exception:
            pass
        from datetime import datetime, timezone
        registry.setdefault("plugins", {})[root.name] = {
            "root": str(root),
            "version": version,
            "registered_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
        }
        fd, tmp = tempfile.mkstemp(dir=str(registry_file.parent),
                                   prefix=".plugin_roots.")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(registry, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, registry_file)
    except Exception:
        pass
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


def vault_relative(file_path: str) -> str | None:
    """Vault-relative posix path when file_path sits under workspace/docs/."""
    parts = Path(file_path).as_posix().split("/")
    for i in range(len(parts) - len(VAULT_SEGMENTS)):
        if tuple(parts[i:i + 2]) == VAULT_SEGMENTS:
            inner = "/".join(parts[i + 2:])
            return inner or None
    return None


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


def is_workspace_config(file_path: str) -> bool:
    parts = Path(file_path).as_posix().split("/")
    return parts[-2:] == ["workspace", "config.json"]


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
    if is_workspace_config(file_path):
        try:
            return config_guard(written, file_path)
        except Exception:
            return 0  # a guard never takes the session down
    rel = vault_relative(file_path)
    if rel is None or not rel.endswith(".md"):
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
            if f"/{VAULT_SEGMENTS[0]}/{VAULT_SEGMENTS[1]}/" in f"/{resolved}":
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
    parts = Path(file_path).as_posix().split("/")
    for i in range(len(parts) - 1):
        if tuple(parts[i:i + 2]) == VAULT_SEGMENTS:
            vault_dir = "/".join(parts[:i + 2])
            break
    else:
        return 0
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = vault_check.main([
            "check", "--vault", vault_dir, "--changed", rel,
        ])
    if code == 1:
        sys.stderr.write(buffer.getvalue())
        print(
            "vault law: this write left the findings above; repair them in"
            " this session before moving on. Generated files are"
            " re-rendered, never edited.",
            file=sys.stderr)
        return 2
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "pre"
    payload = read_payload()
    if mode == "register":
        return register()
    payload = normalize(payload)
    if payload.get("tool_name") not in ("Write", "Edit"):
        return 0
    return pre(payload) if mode == "pre" else post(payload)


if __name__ == "__main__":
    sys.exit(main())
