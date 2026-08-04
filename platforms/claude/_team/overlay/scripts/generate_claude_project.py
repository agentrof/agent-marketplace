#!/usr/bin/env python3
"""Materialize one team's managed Claude project-instruction block."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


TEAM_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
BLOCK_START_RE = re.compile(
    r"<!-- agentrof:([a-z0-9]+(?:-[a-z0-9]+)*):claude:start -->"
)


def plugin_name(plugin_root: Path) -> str:
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    try:
        value = json.loads(manifest.read_text(encoding="utf-8")).get("name", "")
    except Exception as exc:
        raise ValueError(f"unreadable plugin manifest: {manifest}") from exc
    if not TEAM_RE.fullmatch(str(value)):
        raise ValueError("plugin manifest has no valid name")
    return str(value)


def block_markers(team: str) -> tuple[str, str]:
    return (
        f"<!-- agentrof:{team}:claude:start -->",
        f"<!-- agentrof:{team}:claude:end -->",
    )


def rendered_template(plugin_root: Path, workspace: str) -> str:
    return (plugin_root / "templates" / "CLAUDE.md").read_text(
        encoding="utf-8"
    ).replace("{{workspace}}", workspace).rstrip()


def merge(existing: str, block: str, team: str) -> str:
    start_marker, end_marker = block_markers(team)
    foreign = {match.group(1) for match in BLOCK_START_RE.finditer(existing)} - {team}
    if foreign:
        raise ValueError(
            "project already carries another Agentrof team block: "
            + ", ".join(sorted(foreign))
        )
    managed = f"{start_marker}\n{block}\n{end_marker}"
    start, end = existing.find(start_marker), existing.find(end_marker)
    if start >= 0 or end >= 0:
        if start < 0 or end < start:
            raise ValueError("CLAUDE.md has an incomplete Agentrof managed block")
        end += len(end_marker)
        return existing[:start] + managed + existing[end:]
    if not existing.strip():
        return managed + "\n"
    occurrences = existing.count(block)
    if occurrences == 1:
        return existing.replace(block, managed, 1)
    raise ValueError(
        "unmanaged CLAUDE.md collision; preserve user instructions and add the"
        " Agentrof managed block explicitly before retrying"
    )


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def plan(project_root: Path, plugin_root: Path, workspace: str) -> tuple[str, str]:
    team = plugin_name(plugin_root)
    target = project_root / "CLAUDE.md"
    if target.is_symlink():
        raise ValueError(f"managed project target is a symbolic link: {target}")
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    return existing, merge(existing, rendered_template(plugin_root, workspace), team)


def owned_surfaces(project_root: Path, team: str) -> dict[str, str]:
    target = project_root / "CLAUDE.md"
    if not target.is_file():
        return {}
    text = target.read_text(encoding="utf-8")
    start, end = block_markers(team)
    left, right = text.find(start), text.find(end)
    if left < 0 or right < left:
        return {}
    right += len(end)
    return {
        "CLAUDE.md#agentrof": hashlib.sha256(
            text[left:right].encode()
        ).hexdigest()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "check", "apply"))
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()
    project = args.project_root.resolve()
    plugin_root = Path(__file__).resolve().parents[1]
    team = plugin_name(plugin_root)
    current = owned_surfaces(project, team)
    existing, target = plan(project, plugin_root, args.workspace)
    changes = ["CLAUDE.md"] if existing != target else []
    result = {
        "changes": changes,
        "current_surfaces": current,
        "target_surfaces": current,
    }
    if args.action in {"check", "apply"}:
        marker_start, marker_end = block_markers(team)
        left, right = target.find(marker_start), target.find(marker_end)
        if left >= 0 and right >= left:
            right += len(marker_end)
            result["target_surfaces"] = {
                "CLAUDE.md#agentrof": hashlib.sha256(
                    target[left:right].encode()
                ).hexdigest()
            }
    if args.action == "apply" and existing != target:
        atomic_write(project / "CLAUDE.md", target)
        result["current_surfaces"] = result["target_surfaces"]
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
