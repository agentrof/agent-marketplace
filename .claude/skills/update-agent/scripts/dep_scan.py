#!/usr/bin/env python3
"""Walk the depends_on graph from a target component and emit JSON.

BFS with visited set for cycle detection. Reads all .claude/skills/*/manifest.yaml.
Subagents under .claude/agents/ are treated as leaf nodes (no manifest in Phase 1).

Usage:
    dep_scan.py --target <id> [--output <path>]

Output (stdout or --output path):
{
  "target": "<id>",
  "forward_dependencies": [<id>, ...],
  "direct_dependents": [<id>, ...],
  "transitive_dependents": {"depth_2": [...], "depth_3": [...], ...},
  "cycles_detected": [[<id>, <id>, ...], ...]
}
"""

import argparse
import json
import os
import sys
from collections import deque
from pathlib import Path


def repo_root() -> Path:
    env = os.environ.get("MARKETPLACE_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[4]


def parse_manifest(path: Path) -> dict:
    out: dict = {"id": None, "depends_on": []}
    raw = path.read_text(encoding="utf-8")
    current_list: str | None = None
    for raw_line in raw.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line.startswith("  - ") and current_list:
            out[current_list].append(line[4:].strip())
            continue
        if line.startswith("- ") and current_list:
            out[current_list].append(line[2:].strip())
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if key == "depends_on":
            current_list = key
            if value.startswith("[") and value.endswith("]"):
                inner = value[1:-1].strip()
                out[key] = [item.strip() for item in inner.split(",") if item.strip()] if inner else []
                current_list = None
            else:
                out[key] = []
        elif key in out:
            current_list = None
            out[key] = value
    return out


def load_graph(root: Path) -> tuple[dict, dict]:
    """Return (forward, reverse) edge maps. forward[id] = list of ids id depends on."""
    forward: dict = {}
    skills_dir = root / ".claude" / "skills"
    if skills_dir.is_dir():
        for child in skills_dir.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            manifest = child / "manifest.yaml"
            if not manifest.is_file():
                continue
            try:
                parsed = parse_manifest(manifest)
            except Exception:
                continue
            node_id = parsed.get("id") or child.name
            forward[node_id] = list(parsed.get("depends_on", []) or [])
    # Subagents are leaf nodes in Phase 1; they have no manifest, no forward edges.
    agents_dir = root / ".claude" / "agents"
    if agents_dir.is_dir():
        for child in agents_dir.iterdir():
            if not child.is_file() or child.suffix != ".md" or child.name.startswith("."):
                continue
            forward.setdefault(child.stem, [])

    reverse: dict = {node: [] for node in forward}
    for src, dsts in forward.items():
        for dst in dsts:
            reverse.setdefault(dst, []).append(src)
    return forward, reverse


def bfs_dependents(reverse: dict, target: str) -> tuple[list, dict, list]:
    """Returns (direct, transitive_by_depth, cycles)."""
    direct = list(reverse.get(target, []))
    transitive: dict = {}
    cycles: list = []
    visited = {target}
    frontier = list(direct)
    for node in frontier:
        visited.add(node)
    depth = 2
    while frontier:
        next_frontier = []
        for node in frontier:
            for parent in reverse.get(node, []):
                if parent == target or parent in visited:
                    if parent == target or parent in {target, *direct}:
                        # cycle: from parent back into already-visited set
                        cycles.append([parent, node])
                    continue
                visited.add(parent)
                next_frontier.append(parent)
        if next_frontier:
            transitive[f"depth_{depth}"] = sorted(set(next_frontier))
            depth += 1
        frontier = next_frontier
    return sorted(set(direct)), transitive, cycles


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    root = repo_root()
    forward, reverse = load_graph(root)

    if args.target not in forward:
        sys.exit(f"error: target '{args.target}' not found in marketplace")

    direct, transitive, cycles = bfs_dependents(reverse, args.target)

    result = {
        "target": args.target,
        "forward_dependencies": forward.get(args.target, []),
        "direct_dependents": direct,
        "transitive_dependents": transitive,
        "cycles_detected": cycles,
    }

    payload = json.dumps(result, indent=2) + "\n"
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload, encoding="utf-8")
        print(f"wrote {out_path}")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
