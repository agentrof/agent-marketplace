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
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
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
    r"(?m)^\s*(approved_at_utc|approval_revision|revision|registry_hash|"
    r"source_hash|stamped_at):")
DESIGN_SYSTEM_MACHINE_FIELD_RE = re.compile(
    r"(?m)^\s*(status|revision|approved_at_utc|baseline_hash|"
    r"supersedes_hash):")
EXPERIENCE_PATHS = (
    re.compile(r"^experience-design/experience\.md$"),
    re.compile(r"^experience-design/programs/prg-[0-9]+/program\.md$"),
    re.compile(r"^experience-design/programs/prg-[0-9]+/releases/rel-[0-9]+/release\.md$"),
    re.compile(r"^experience-design/programs/prg-[0-9]+/releases/rel-[0-9]+/spaces/[a-z0-9-]+/space\.md$"),
    re.compile(r"^experience-design/programs/prg-[0-9]+/releases/rel-[0-9]+/spaces/[a-z0-9-]+/(?:domains/[a-z0-9-]+/)+domain\.md$"),
    re.compile(r"^experience-design/programs/prg-[0-9]+/releases/rel-[0-9]+/(?:spaces/[a-z0-9-]+/(?:domains/[a-z0-9-]+/)*)?(?:journeys/[a-z0-9-]+-journey|screens/[a-z0-9-]+-screen|flows/[a-z0-9-]+-flows|reviews/[a-z0-9-]+-review|artifacts/[a-z0-9-]+-artifact)\.md$"),
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


def shell_vault(payload: dict) -> Path | None:
    cwd = Path(str(payload.get("cwd") or ".")).resolve()
    for parent in (cwd, *cwd.parents):
        candidate = parent / "workspace" / "docs"
        if candidate.is_dir():
            return candidate
    return None


def vault_inventory(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel in {".obsidian/workspace.json",
                   ".obsidian/workspace-mobile.json"} \
                or rel.startswith(".trash/"):
            continue
        result[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def inventory_path(payload: dict) -> Path:
    session = str(payload.get("session_id") or "unknown")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session)
    return data_dir() / "vault-inventory" / f"{safe}.json"


def shell_snapshot(payload: dict) -> int:
    root = shell_vault(payload)
    path = inventory_path(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = {"vault": str(root) if root else "",
             "inventory": vault_inventory(root) if root else {}}
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    return 0


def shell_verify(payload: dict) -> int:
    path = inventory_path(payload)
    try:
        before = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return deny("Bash vault inventory is missing or unreadable")
    path.unlink(missing_ok=True)
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
    argv = ["check", "--vault", str(root)]
    if len(changed) == 1:
        argv.extend(("--impact", changed[0]))
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = vault_check.main(argv)
    if code:
        sys.stderr.write(buffer.getvalue())
        return deny(
            "Bash changed vault inventory and left the full/impact gate red;"
            " repair or restore the changed paths")
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
    if payload.get("tool_name") == "Bash":
        return shell_snapshot(payload) if mode == "pre" else shell_verify(payload)
    if payload.get("tool_name") not in ("Write", "Edit"):
        return 0
    return pre(payload) if mode == "pre" else post(payload)


if __name__ == "__main__":
    sys.exit(main())
