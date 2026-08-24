#!/usr/bin/env python3
"""Single writer and validator for the closed project bootstrap config."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import marketplace_paths


TEAM = "software-engineering-team"
SCHEMA_VERSION = 2
CONFIG_FIELDS = {
    "schema_version",
    "team_id",
    "output_language",
    "terminology_language",
}
ORDINARY_FIELDS = {"output_language", "terminology_language"}


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
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def check(config: dict) -> list[str]:
    """Validate the team-owned, intentionally narrow bootstrap surface."""
    errors: list[str] = []
    for field in sorted(set(config) - CONFIG_FIELDS):
        errors.append(f"config contains unknown or retired field: {field}")
    if config.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if marketplace_paths.team_from_config(config) != TEAM:
        errors.append(f"team_id must be {TEAM}")
    for field in ("output_language", "terminology_language"):
        value = config.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field} must be a non-empty string")
    return errors


def parse_value(raw: str) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def write_result(path: Path, before: object, after: object,
                 *, dry_run: bool, json_output: bool) -> None:
    result = {
        "ok": True, "config": str(path), "before": before, "after": after,
        "dry_run": dry_run,
    }
    if json_output:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        suffix = " (dry run)" if dry_run else ""
        print(f"project_config: {before!r} -> {after!r}{suffix}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check")
    p.add_argument("--config", required=True)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("set")
    p.add_argument("--config", required=True)
    p.add_argument("--field", required=True, choices=sorted(ORDINARY_FIELDS))
    p.add_argument("--value", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    path = Path(args.config).resolve()
    try:
        config = load(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"project_config: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    if args.command == "check":
        errors = check(config)
        if args.json:
            print(json.dumps({"ok": not errors, "errors": errors}, indent=2))
        else:
            for value in errors:
                print(f"ERROR {path}:1 [project_config] {value}")
        return 1 if errors else 0
    before = config.get(args.field)
    after = parse_value(args.value)
    proposed = dict(config)
    proposed[args.field] = after
    errors = check(proposed)
    if errors:
        for value in errors:
            print(f"project_config: {value}", file=sys.stderr)
        return 1
    if not args.dry_run:
        atomic(path, proposed)
    write_result(path, before, after, dry_run=args.dry_run,
                 json_output=args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
