#!/usr/bin/env python3
"""Derived-count injector for the Agent Marketplace README.

Counts are never hand-written. This tool computes them from the tree and
rewrites the single legal location: the block between the README markers.
`--check` fails (exit 1) when the committed README differs from the
computed block, which is the CI drift gate.

Stdlib only. Deterministic output.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

COUNTS_START = "<!-- counts:start -->"
COUNTS_END = "<!-- counts:end -->"


def parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if ":" in stripped and not stripped.startswith("#"):
            key, _, value = stripped.partition(":")
            fm[key.strip()] = value.strip().strip("\"'")
    return fm


def compute(root: Path) -> dict[str, int]:
    plugins_dir = root / "plugins"
    plugins = sorted(p for p in plugins_dir.iterdir() if p.is_dir()) if plugins_dir.is_dir() else []
    agents = 0
    entry_skills = 0
    knowledge_skills = 0
    for plugin in plugins:
        agents_dir = plugin / "agents"
        if agents_dir.is_dir():
            agents += len(list(agents_dir.glob("*.md")))
        skills_dir = plugin / "skills"
        if skills_dir.is_dir():
            for sdir in sorted(d for d in skills_dir.iterdir() if d.is_dir()):
                skill_md = sdir / "SKILL.md"
                if not skill_md.is_file():
                    continue
                fm = parse_frontmatter(skill_md.read_text(encoding="utf-8"))
                if fm.get("disable-model-invocation") == "true":
                    entry_skills += 1
                else:
                    knowledge_skills += 1
    return {
        "plugins": len(plugins),
        "agents": agents,
        "entry_skills": entry_skills,
        "knowledge_skills": knowledge_skills,
    }


def render_block(counts: dict[str, int]) -> str:
    lines = [
        COUNTS_START,
        "| Plugins | Agents | Entry skills | Knowledge skills |",
        "|---|---|---|---|",
        f"| {counts['plugins']} | {counts['agents']} | {counts['entry_skills']} | {counts['knowledge_skills']} |",
        COUNTS_END,
    ]
    return "\n".join(lines)


def inject(readme: Path, block: str) -> tuple[str, str]:
    """Return (old_text, new_text)."""
    text = readme.read_text(encoding="utf-8")
    pattern = re.compile(
        re.escape(COUNTS_START) + r".*?" + re.escape(COUNTS_END), re.DOTALL
    )
    if not pattern.search(text):
        raise SystemExit(
            f"counts: README has no marker block ({COUNTS_START} ... {COUNTS_END})"
        )
    new_text = pattern.sub(block, text, count=1)
    return text, new_text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--check", action="store_true", help="fail if README differs from computed counts")
    args = parser.parse_args()

    root = args.root.resolve()
    readme = root / "README.md"
    if not readme.is_file():
        print("counts: README.md not found", file=sys.stderr)
        return 1

    block = render_block(compute(root))
    old_text, new_text = inject(readme, block)

    if args.check:
        if old_text != new_text:
            print("counts: README marker block is stale; run `make counts`", file=sys.stderr)
            return 1
        print("counts: README marker block is current")
        return 0

    if old_text != new_text:
        readme.write_text(new_text, encoding="utf-8")
        print("counts: README marker block updated")
    else:
        print("counts: README marker block already current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
