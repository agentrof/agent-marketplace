#!/usr/bin/env python3
"""Render assets/agent-template.md into .claude/agents/<id>.md with substitution.

Usage:
    render_template.py <agent_id> <description> [--tools "Read, Grep"] [--model opus]
                       [--purpose TEXT] [--input-1 TEXT] [--input-2 TEXT]
                       [--step-1 TEXT] [--step-2 TEXT]

Exits non-zero on error.
"""

import argparse
import os
import re
import sys
from pathlib import Path


ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*[a-z0-9]$")
VALID_MODELS = {"opus", "sonnet", "haiku"}


def repo_root() -> Path:
    env = os.environ.get("MARKETPLACE_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[4]


def validate_id(agent_id: str) -> None:
    if not ID_PATTERN.match(agent_id):
        sys.exit(f"error: invalid id '{agent_id}', expected ^[a-z][a-z0-9-]*[a-z0-9]$")
    if len(agent_id) > 64:
        sys.exit(f"error: id '{agent_id}' exceeds 64 chars")
    if "--" in agent_id:
        sys.exit(f"error: id '{agent_id}' contains consecutive dashes")


def validate_model(model: str) -> None:
    if model and model not in VALID_MODELS:
        sys.exit(f"error: model '{model}' not in {sorted(VALID_MODELS)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("agent_id")
    parser.add_argument("description")
    parser.add_argument("--tools", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--purpose", default="")
    parser.add_argument("--input-1", default="")
    parser.add_argument("--input-2", default="")
    parser.add_argument("--step-1", default="")
    parser.add_argument("--step-2", default="")
    args = parser.parse_args()

    validate_id(args.agent_id)
    validate_model(args.model)

    root = repo_root()
    template_path = root / ".claude" / "skills" / "create-agent" / "assets" / "agent-template.md"
    target_path = root / ".claude" / "agents" / f"{args.agent_id}.md"

    if not template_path.is_file():
        sys.exit(f"error: template missing at {template_path.relative_to(root)}")
    if target_path.exists():
        sys.exit(f"error: target already exists at {target_path.relative_to(root)}")

    raw = template_path.read_text(encoding="utf-8")

    tools_line = f"tools: {args.tools}" if args.tools else ""
    model_line = f"model: {args.model}" if args.model else ""

    values = {
        "AGENT_ID": args.agent_id,
        "DESCRIPTION": args.description,
        "TOOLS_LINE": tools_line,
        "MODEL_LINE": model_line,
        "PURPOSE": args.purpose,
        "INPUT_1": args.input_1,
        "INPUT_2": args.input_2,
        "STEP_1": args.step_1,
        "STEP_2": args.step_2,
    }

    rendered = raw
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)

    # collapse blank lines inside the frontmatter only (between the two --- markers)
    lines = rendered.splitlines()
    if lines and lines[0] == "---":
        end_idx = next((i for i in range(1, len(lines)) if lines[i] == "---"), -1)
        if end_idx > 1:
            fm = [ln for ln in lines[1:end_idx] if ln.strip()]
            lines = lines[:1] + fm + lines[end_idx:]
    rendered = "\n".join(lines) + ("\n" if rendered.endswith("\n") else "")

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(rendered, encoding="utf-8")
    print(target_path.relative_to(root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
