#!/usr/bin/env python3
"""Single writer and checker for team-owned project configuration fields."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

ALLOWED_ORIGINS = {"greenfield", "existing"}
CONTRACT_RELATIVE = Path(".agentrof") / "agent-marketplace" / "project.json"


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not isinstance(value, dict):
        raise ValueError("config must be a JSON object")
    return value


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix="config.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def state_exists(workspace: Path) -> bool:
    return any((
        any((workspace / "docs" / "experience-design" / "programs").glob("*")),
        any((workspace / "planning").glob("*.json")),
        any((workspace / "work-orders").glob("*")),
    ))


def contract_root(config_path: Path) -> Path | None:
    for candidate in (config_path.parent, *config_path.parents):
        if (candidate / CONTRACT_RELATIVE).is_file():
            return candidate
    return None


def check(config: dict) -> list[str]:
    errors = []
    if config.get("team_id") != "software-engineering-team":
        errors.append("team_id must be software-engineering-team")
    if config.get("project_origin") not in ALLOWED_ORIGINS:
        errors.append("project_origin must be greenfield or existing")
    scale = config.get("scale", "small")
    if scale not in {"small", "medium", "large", "x-large", "xx-large", "enterprise"}:
        errors.append("unsupported scale")
    return errors


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check"); p.add_argument("--config", required=True)
    p = sub.add_parser("set-origin"); p.add_argument("--config", required=True); p.add_argument("--origin", required=True, choices=sorted(ALLOWED_ORIGINS))
    args = parser.parse_args(argv)
    path = Path(args.config).resolve()
    config = load(path)
    if args.command == "set-origin":
        root = contract_root(path)
        if root is not None:
            print(
                "project_config: registered projects must use PMO project "
                "classify-origin so the managed fingerprint and audit ledger move together",
                file=__import__("sys").stderr,
            )
            return 1
        current = config.get("project_origin")
        if current and current != "unclassified" and current != args.origin:
            if state_exists(path.parent):
                print("project_config: project_origin is immutable after program, planning or delivery state", file=__import__("sys").stderr)
                return 1
        if current == "unclassified" and state_exists(path.parent):
            # Migration state is allowed to be classified exactly once even
            # when legacy delivery exists.
            pass
        config["project_origin"] = args.origin
        atomic(path, config)
        print(f"project_config: project_origin={args.origin}")
        return 0
    errors = check(config)
    for value in errors:
        print(f"ERROR {path}:1 [project_config] {value}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
