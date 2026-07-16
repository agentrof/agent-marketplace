#!/usr/bin/env python3
"""Per-write vault consistency hook for the workspace docs tree.

Two moments, one law (the obsidian-vault skill):

- pre (PreToolUse, Write|Edit): content-shape denials BEFORE the write
  lands. A vault-internal relative markdown link, an unescaped alias pipe
  in a table-row wikilink, or an inline flow-list tags:/aliases: value
  never reaches disk, regardless of which agent writes. Content-only
  regexes, zero vault I/O.
- post (PostToolUse, Write|Edit): after a write lands under the vault,
  run vault_check's --changed fast path and surface its findings to the
  writing session immediately, so link and metadata duties are repaired
  in-session instead of at a distant gate. Gates stay the hard barrier.

File operations through the shell (moves, deletes) bypass Write/Edit
hooks by nature; the next --changed write or gate-time vault_check
surfaces them. Stdlib only.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sys
from pathlib import Path

import vault_check

VAULT_SEGMENTS = ("workspace", "docs")

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


def pre(payload: dict) -> int:
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0
    file_path = str(tool_input.get("file_path", ""))
    rel = vault_relative(file_path)
    if rel is None or not rel.endswith(".md"):
        return 0
    content = written_content(tool_input)
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
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0
    file_path = str(tool_input.get("file_path", ""))
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
    if payload.get("tool_name") not in ("Write", "Edit"):
        return 0
    return pre(payload) if mode == "pre" else post(payload)


if __name__ == "__main__":
    sys.exit(main())
