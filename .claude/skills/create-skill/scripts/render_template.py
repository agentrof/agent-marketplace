#!/usr/bin/env python3
"""Clone skill-template into .claude/skills/<id>/ with placeholder substitution.

Usage:
    render_template.py <skill_id> <description> [--purpose TEXT] [--when-to-use TEXT]

Exits non-zero on error (id collision, invalid id, missing template).
"""

import argparse
import os
import re
import shutil
import sys
from pathlib import Path


ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")
PLACEHOLDERS = (
    "SKILL_ID",
    "DESCRIPTION",
    "PURPOSE",
    "WHEN_TO_USE",
    "INPUT_1",
    "INPUT_2",
    "STEP_1",
    "STEP_2",
)


def repo_root() -> Path:
    """Locate repo root from this script's path."""
    env = os.environ.get("MARKETPLACE_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[4]


def validate_id(skill_id: str) -> None:
    if not ID_PATTERN.match(skill_id):
        sys.exit(f"error: invalid id '{skill_id}', expected ^[a-z][a-z0-9-]*[a-z0-9]$")
    if len(skill_id) > 64:
        sys.exit(f"error: id '{skill_id}' exceeds 64 chars")
    if "--" in skill_id:
        sys.exit(f"error: id '{skill_id}' contains consecutive dashes")


def substitute(text: str, values: dict) -> str:
    for key in PLACEHOLDERS:
        text = text.replace("{{" + key + "}}", values.get(key, ""))
    return text


def render(template_root: Path, target_root: Path, values: dict) -> list[Path]:
    written: list[Path] = []
    for src in template_root.rglob("*"):
        rel = src.relative_to(template_root)
        dst = target_root / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
            continue
        if src.name == ".gitkeep":
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            written.append(dst)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        raw = src.read_text(encoding="utf-8")
        dst.write_text(substitute(raw, values), encoding="utf-8")
        written.append(dst)
    return written


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("skill_id")
    parser.add_argument("description")
    parser.add_argument("--purpose", default="")
    parser.add_argument("--when-to-use", default="")
    parser.add_argument("--input-1", default="")
    parser.add_argument("--input-2", default="")
    parser.add_argument("--step-1", default="")
    parser.add_argument("--step-2", default="")
    args = parser.parse_args()

    validate_id(args.skill_id)

    root = repo_root()
    template_root = root / ".claude" / "skills" / "create-skill" / "assets" / "skill-template"
    target_root = root / ".claude" / "skills" / args.skill_id

    if not template_root.is_dir():
        sys.exit(f"error: template missing at {template_root.relative_to(root)}")

    if target_root.exists():
        sys.exit(f"error: target already exists at {target_root.relative_to(root)}")

    values = {
        "SKILL_ID": args.skill_id,
        "DESCRIPTION": args.description,
        "PURPOSE": args.purpose,
        "WHEN_TO_USE": args.when_to_use,
        "INPUT_1": args.input_1,
        "INPUT_2": args.input_2,
        "STEP_1": args.step_1,
        "STEP_2": args.step_2,
    }

    written = render(template_root, target_root, values)
    for path in written:
        print(path.relative_to(root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
