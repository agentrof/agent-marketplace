#!/usr/bin/env python3
"""Single writer and checker for team-owned project configuration fields."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path, PurePosixPath

import marketplace_paths


TEAM = "software-engineering-team"
ALLOWED_SCALES = {
    "small", "medium", "large", "x-large", "xx-large", "enterprise",
}
STACK_FIELDS = {
    "backend_stack": {"python-fastapi"},
    "frontend_stack": {"react-typescript"},
    "environment_stack": {"docker-compose"},
}
COMMAND_FIELDS = {"test_command", "mutation_command", "env_command"}
ORDINARY_FIELDS = {
    "scale", "output_language", "terminology_language", *STACK_FIELDS,
    "databases", *COMMAND_FIELDS, "source_dirs", "max_parallel", "limits",
}
OPTIONAL_FIELDS = {
    *STACK_FIELDS, "databases", *COMMAND_FIELDS, "source_dirs",
    "max_parallel", "limits",
}
LIMIT_KEYS = {
    "node_direct_docs_warn", "rule_sets_per_node_warn",
    "active_br_per_node_warn", "rules_per_set_warn",
    "criteria_per_set_warn", "process_doc_lines_warn",
    "open_row_age_days_warn", "space_docs_warn", "space_bytes_warn",
    "nesting_warn_depth", "nesting_fail_depth",
    "summary_max_lines_space", "summary_max_lines_default",
    "nav_peer_min", "nav_peer_max", "experience_flows_per_set",
    "experience_transitions_per_set", "experience_screens_per_leaf_domain",
}
NONNEGATIVE_LIMIT_KEYS = {"nav_peer_min", "nav_peer_max"}


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


def positive_integer(value: object, *, allow_zero: bool = False) -> bool:
    return (
        isinstance(value, int) and not isinstance(value, bool)
        and (value >= 0 if allow_zero else value > 0)
    )


def valid_relative_path(value: object) -> bool:
    if not isinstance(value, str) or not value.strip() or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and value == path.as_posix() \
        and all(part not in {"", ".", ".."} for part in path.parts)


def effective_limit_pairs(config: dict) -> tuple[tuple[int, int], tuple[int, int]]:
    """Resolve paired limits after shipped defaults, scale, then overrides."""
    plugin = Path(__file__).resolve().parents[1]
    space_schema = json.loads((
        plugin / "skill-content" / "business-analysis" / "data"
        / "space-schema.json"
    ).read_text(encoding="utf-8"))
    vault_policy = json.loads((
        plugin / "skill-content" / "obsidian-vault" / "data"
        / "vault-policy.json"
    ).read_text(encoding="utf-8"))
    thresholds = space_schema["thresholds"]
    nesting_warn = int(thresholds["nesting_warn_depth"])
    nesting_fail = int(thresholds["nesting_fail_depth"])
    scale = config.get("scale", "small")
    profile = next(
        (row for row in space_schema.get("scale_profiles", [])
         if row.get("level") == scale),
        {},
    )
    bonus = profile.get("nesting_bonus", 0)
    if isinstance(bonus, int) and not isinstance(bonus, bool) and bonus >= 0:
        nesting_warn += bonus
        nesting_fail += bonus
    nav_min = int(vault_policy["nav_peer_min"])
    nav_max = int(vault_policy["nav_peer_max"])
    limits = config.get("limits")
    if isinstance(limits, dict):
        nesting_warn = limits.get("nesting_warn_depth", nesting_warn)
        nesting_fail = limits.get("nesting_fail_depth", nesting_fail)
        nav_min = limits.get("nav_peer_min", nav_min)
        nav_max = limits.get("nav_peer_max", nav_max)
    return (nesting_warn, nesting_fail), (nav_min, nav_max)


def check(config: dict) -> list[str]:
    """Validate active fields and any optional delivery field that is set."""
    errors: list[str] = []
    owner = marketplace_paths.team_from_config(config)
    if owner != TEAM:
        errors.append(f"team_id must be {TEAM}")
    if "project_origin" in config:
        errors.append("project_origin is retired; run setup apply to remove it")
    if config.get("scale", "small") not in ALLOWED_SCALES:
        errors.append("unsupported scale")
    for field in ("output_language", "terminology_language"):
        value = config.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field} must be a non-empty string")
    for field, allowed in STACK_FIELDS.items():
        if field in config and config[field] not in allowed:
            errors.append(f"{field} must be one of: {', '.join(sorted(allowed))}")
    if "databases" in config:
        databases = config["databases"]
        if not isinstance(databases, list) or not databases:
            errors.append("databases must be a non-empty list")
        elif any(value not in {"sql", "nosql"} for value in databases):
            errors.append("databases values must be sql or nosql")
        elif len(databases) != len(set(databases)):
            errors.append("databases values must be unique")
    for field in sorted(COMMAND_FIELDS):
        if field in config and (
            not isinstance(config[field], str) or not config[field].strip()
        ):
            errors.append(f"{field} must be a non-empty command string")
    if "source_dirs" in config:
        values = config["source_dirs"]
        if not isinstance(values, list) or not values:
            errors.append("source_dirs must be a non-empty list")
        elif any(not valid_relative_path(value) for value in values):
            errors.append("source_dirs must contain normalized repository-relative paths")
        elif len(values) != len(set(values)):
            errors.append("source_dirs values must be unique")
    if "max_parallel" in config and not positive_integer(config["max_parallel"]):
        errors.append("max_parallel must be a positive integer")
    if "model_overrides" in config:
        errors.append("model_overrides is retired and must be removed")
    if "limits" in config:
        limits = config["limits"]
        if not isinstance(limits, dict):
            errors.append("limits must be an object")
        else:
            for key in sorted(set(limits) - LIMIT_KEYS):
                errors.append(f"limits contains unknown key: {key}")
            for key, value in sorted(limits.items()):
                if key in LIMIT_KEYS and not positive_integer(
                    value, allow_zero=key in NONNEGATIVE_LIMIT_KEYS
                ):
                    qualifier = "non-negative" if key in NONNEGATIVE_LIMIT_KEYS else "positive"
                    errors.append(f"limits.{key} must be a {qualifier} integer")
            try:
                (warn, fail), (minimum, maximum) = effective_limit_pairs(config)
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                errors.append("shipped limit defaults are missing or invalid")
            else:
                if isinstance(warn, int) and isinstance(fail, int) and warn >= fail:
                    errors.append("effective nesting_warn_depth must be lower than nesting_fail_depth")
                if isinstance(minimum, int) and isinstance(maximum, int) and minimum > maximum:
                    errors.append("effective nav_peer_min must not exceed nav_peer_max")
    return errors


def parse_value(raw: str) -> object:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def has_workflow_state(config_path: Path) -> bool:
    docs = config_path.parent / "docs"
    roots = (
        "requirements", "business-analysis", "solution-design", "system-architecture",
        "design-system", "experience-design", "delivery", "backlog",
    )
    return any(
        any((docs / relative).rglob("*.md"))
        for relative in roots if (docs / relative).is_dir()
    )


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
    p = sub.add_parser("unset")
    p.add_argument("--config", required=True)
    p.add_argument("--field", required=True, choices=sorted(OPTIONAL_FIELDS))
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
    before: object
    after: object
    proposed = dict(config)
    if args.command == "set":
        before = config.get(args.field)
        after = parse_value(args.value)
        proposed[args.field] = after
    else:
        before = config.get(args.field)
        after = None
        proposed.pop(args.field, None)
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
