#!/usr/bin/env python3
"""Single writer and validator for the closed project bootstrap config."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

import atomic_file
import marketplace_paths
import role_settings


TEAM = "software-engineering-team"
SCHEMA_VERSION = 2
CONFIG_FIELDS = {
    "schema_version",
    "team_id",
    "output_language",
    "terminology_language",
    "tests_root",
    "workspace_layout",
    "pr_proof",
    "local_proof",
    "role_routing",
    "task_triggers",
    role_settings.CLASS_MODELS,
    role_settings.ROLE_CLASSES,
}
ORDINARY_FIELDS = {"output_language", "terminology_language", "tests_root", "workspace_layout",
                   "pr_proof", "local_proof", "role_routing", "task_triggers"}
# Deliver settings the project owns; setup writes none of them and keeps the project's.
PROJECT_FIELDS = ("pr_proof", "local_proof", "role_routing", "task_triggers")
# Who proves a task before its pull request; unset means remote_ci.
PR_PROOF_VALUES = ("remote_ci", "local_record")
# Setup writes these into a config that lacks them; the project owns them after.
# `workspace` and `docs` are the only top-level folders the workspace guard lets
# a write create under workspace/ and workspace/docs/. The project root is not
# guarded, so the files tools leave there (skills-lock.json, host folders) need
# no list.
DEFAULTS = {
    "tests_root": "workspace/tests",
    "workspace_layout": {
        "workspace": ["apps", "docs", "environment", "experience-design", "memory",
                      "project", "tests"],
        "docs": ["backlog", "business-analysis", "operation", "solution-design",
                 "system-architecture"],
    },
}
# Folders setup creates below the allowed ones.
NESTED_DIRS = ("docs/backlog/tasks", "docs/backlog/retrospectives")
# The builder writes the class map of every host into each installed package;
# a canonical source tree ships none, so it accepts no model or class setting.
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
# `classes` reads only the config and the package; whether a model is available
# is the project generators' to judge, and they report the roles they render.
CLASS_MAP_NOTE = (
    "Config-level effective map: each role's class, model and effort from"
    " workspace/config.json over the package class map. It knows nothing of model"
    " availability, so the model a host's roles run is the roles table its project"
    " generator reports."
)


def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not isinstance(value, dict):
        raise ValueError("config must be a JSON object")
    return value


def atomic(path: Path, value: dict) -> None:
    atomic_file.replace_text(
        path, json.dumps(value, ensure_ascii=False, indent=2) + "\n",
    )


def setting(config: dict, field: str):
    """A layout field's value in force: the config's own, else the package default."""
    return config.get(field, DEFAULTS[field])


def required_dirs(config: dict) -> tuple[str, ...]:
    """Every workspace-relative folder setup creates and its check requires."""
    layout = setting(config, "workspace_layout")
    return tuple(dict.fromkeys([
        *layout["workspace"], *(f"docs/{name}" for name in layout["docs"]), *NESTED_DIRS,
    ]))


def layout_errors(config: dict) -> list[str]:
    errors: list[str] = []
    tests_root = setting(config, "tests_root")
    if not isinstance(tests_root, str) or not tests_root.strip() \
            or tests_root.startswith("/") or ".." in tests_root.split("/"):
        errors.append("tests_root must be a relative path inside the project")
    layout = setting(config, "workspace_layout")
    if not isinstance(layout, dict) or set(layout) != set(DEFAULTS["workspace_layout"]):
        return errors + ["workspace_layout must hold exactly workspace and docs"]
    for key, names in layout.items():
        if not isinstance(names, list) or len(set(map(str, names))) != len(names) \
                or not all(isinstance(name, str) and name and "/" not in name
                           and name not in {".", ".."} for name in names):
            errors.append(f"workspace_layout.{key} must list unique single path names")
    if "docs" not in layout.get("workspace", []):
        errors.append("workspace_layout.workspace must allow docs")
    parts = tests_root.split("/") if isinstance(tests_root, str) else []
    if len(parts) > 1 and parts[0] == "workspace" \
            and parts[1] not in layout.get("workspace", []):
        errors.append("tests_root must lie in a folder workspace_layout.workspace allows")
    return errors


def load_class_map() -> dict | None:
    """Return the installed package's class map, or None when it ships none."""
    return role_settings.load_class_map()


def base_errors(config: dict) -> list[str]:
    """The errors of the config's closed field set, schema, team and languages."""
    errors: list[str] = []
    for field in sorted(set(config) - CONFIG_FIELDS - set(role_settings.RETIRED_KEYS)):
        errors.append(f"config contains unknown or retired field: {field}")
    if config.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    if marketplace_paths.team_from_config(config) != TEAM:
        errors.append(f"team_id must be {TEAM}")
    for field in ("output_language", "terminology_language"):
        value = config.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field} must be a non-empty string")
    if "pr_proof" in config and config["pr_proof"] not in PR_PROOF_VALUES:
        errors.append(f"pr_proof must be one of {', '.join(PR_PROOF_VALUES)}")
    proof = config.get("local_proof", ["make test"])
    if not isinstance(proof, list) or not proof or not all(isinstance(item, str) and item.strip() for item in proof):
        errors.append("local_proof must be a non-empty list of commands")
    for field in ("role_routing", "task_triggers"):
        if not isinstance(config.get(field, {}), dict):
            errors.append(f"{field} must be an object")
    errors.extend(layout_errors(config))
    return errors


def check(config: dict) -> list[str]:
    """Validate the team-owned, intentionally narrow bootstrap surface."""
    errors = base_errors(config)
    if {role_settings.CLASS_MODELS, role_settings.ROLE_CLASSES,
            *role_settings.RETIRED_KEYS} & set(config):
        try:
            class_map = load_class_map()
        except ValueError as exc:
            errors.append(str(exc))
        else:
            errors.extend(role_settings.config_errors(config, class_map))
    return errors


def package_host() -> str | None:
    """The host this installed package is built for, from its manifest directory."""
    found = [path.name[1:-len("-plugin")] for path in PACKAGE_ROOT.glob(".*-plugin")
             if path.is_dir()]
    return found[0] if len(found) == 1 else None


def host_listing(host: str) -> dict:
    """The active host's own model list, or why the package catalog stands in.

    The host package's `host_models` adapter reads the list from the binary
    that runs the session. Only the package of ``host`` asks it, and a package
    without the adapter, or a list that fails, leaves the catalog.
    """
    if host != package_host():
        return {"source": "catalog", "status": "other_host"}
    try:
        # The adapter ships in the host package's overlay, beside this script;
        # the canonical tree and an older package have none.
        host_models = importlib.import_module("host_models")
    except ImportError:
        return {"source": "catalog", "status": "no_adapter"}
    try:
        listing = host_models.list_models()
    except Exception as exc:
        # A probe that breaks never stops the topic.
        return {"source": "catalog", "status": "failed", "detail": str(exc)}
    if not isinstance(listing, dict):
        return {"source": "catalog", "status": "failed",
                "detail": "the host list is not a JSON object"}
    if listing.get("status") != "ok" or not isinstance(listing.get("models"), dict):
        status = listing.get("status")
        return {"source": "catalog",
                "status": status if isinstance(status, str) and status != "ok" else "failed",
                **({"detail": listing["detail"]} if isinstance(listing.get("detail"), str)
                   else {})}
    models, hidden = {}, []
    for model, entry in listing["models"].items():
        efforts = entry.get("efforts") if isinstance(entry, dict) else None
        models[model] = efforts if isinstance(efforts, list) else None
        # A model the host's own picker hides is never offered as a choice.
        visibility = entry.get("visibility") if isinstance(entry, dict) else None
        if isinstance(visibility, str) and visibility != "list":
            hidden.append(model)
    return {"source": "host", "status": "ok", "view": listing.get("view"),
            "binary": listing.get("binary"), "version": listing.get("version"),
            "hidden": sorted(hidden), "models": models}


def recommended_effort(efforts: list[str], package: str | None, vocabulary: list[str]) -> str | None:
    """The slot's package effort when the model takes it, else the nearest below, else the lowest."""
    if not efforts:
        return None
    if package in efforts:
        return package
    if package in vocabulary:
        below = [effort for effort in efforts
                 if effort in vocabulary and vocabulary.index(effort) < vocabulary.index(package)]
        if below:
            return below[-1]
    return efforts[0]


def model_choices(class_map: dict, host: str, slot: str, listing: dict) -> list[dict]:
    """A class slot's model choices for the configure topic: the package model
    first, then the other models of the host's list that its picker shows, or
    of the catalog, then `session`."""
    spec = class_map["hosts"][host]
    package = spec["classes"][slot]
    if listing["source"] == "host":
        others = sorted(model for model in listing["models"]
                        if model != role_settings.SESSION
                        and model not in listing.get("hidden", ())
                        and role_settings.model_known(spec, model))
    else:
        others = sorted(spec["models"])
    ordered = [package["model"], *[model for model in others if model != package["model"]],
               role_settings.SESSION]
    result = []
    for model in ordered:
        efforts = role_settings.choices(class_map, host, model)
        listed = listing.get("models", {}).get(model) if listing["source"] == "host" else None
        if model not in spec["models"] and model != role_settings.SESSION \
                and isinstance(listed, list):
            efforts = [effort for effort in efforts if effort in listed]
        result.append({
            "model": model, "efforts": efforts,
            "recommended_effort": recommended_effort(efforts, package.get("effort"),
                                                     spec["efforts"]),
        })
    return result


def class_report(config: dict, class_map: dict, host: str | None = None,
                 model_list: bool = False) -> dict:
    """Each host's config-level effective map: its class slots and its roles."""
    report = {}
    for name in sorted(class_map["hosts"]):
        if host is not None and name != host:
            continue
        spec = class_map["hosts"][name]
        roles = role_settings.resolve(class_map, config, name)
        overrides = role_settings.host_overrides(config, name)
        slots = {}
        for slot, setting in role_settings.slot_settings(class_map, config, name).items():
            package = spec["classes"][slot]
            override = overrides.get(slot)
            slots[slot] = {
                "roles": sorted(role for role, row in roles.items() if row["class"] == slot),
                "package_model": package["model"],
                "package_effort": package.get("effort"),
                "override": override if isinstance(override, dict) else None,
                **setting,
                "efforts": role_settings.choices(class_map, name, setting["model"])
                if isinstance(setting["model"], str) else [],
            }
        report[name] = {"classes": slots, "roles": roles, "confirm": spec["confirm"],
                        "refuse": spec["refuse"]}
        if model_list:
            listing = host_listing(name)
            report[name]["model_list"] = {key: value for key, value in listing.items()
                                          if key != "models"}
            report[name]["model_choices"] = {
                slot: model_choices(class_map, name, slot, listing) for slot in slots}
    return report


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


def effort_text(effort: object) -> str:
    return f"effort {effort}" if effort else "the session effort"


def print_classes(report: dict) -> None:
    print(CLASS_MAP_NOTE)
    for host, spec in report.items():
        for slot, row in spec["classes"].items():
            sources = role_settings.source_text({**row, "class_source": "package"})
            print(f"{host} {slot}: {row['model']} at {effort_text(row['effort'])} ({sources})")
        for line in role_settings.table(spec["roles"]):
            print(f"  {line}")
        print(f"{host} asks the owner to confirm {', '.join(sorted(spec['confirm'])) or 'nothing'}"
              f" and refuses {', '.join(sorted(spec['refuse'])) or 'nothing'}")


def setting_command(args, path: Path, config: dict) -> int:
    try:
        class_map = load_class_map()
    except ValueError as exc:
        print(f"project_config: {exc}", file=sys.stderr)
        return 2
    if class_map is None:
        print("project_config: this package ships no class map templates/model-classes.json;"
              " run the installed package's scripts", file=sys.stderr)
        return 2
    if args.command == "classes":
        if args.host is not None and args.host not in class_map["hosts"]:
            print(f"project_config: unknown host {args.host!r}; hosts are"
                  f" {', '.join(sorted(class_map['hosts']))}", file=sys.stderr)
            return 1
        errors = check(config)
        report = class_report(config, class_map, args.host, args.model_list)
        if args.json:
            print(json.dumps({"ok": not errors, "config": str(path), "errors": errors,
                              "hosts": report,
                              "role_classes": config.get(role_settings.ROLE_CLASSES),
                              "note": CLASS_MAP_NOTE},
                             ensure_ascii=False, indent=2, sort_keys=True))
        else:
            print_classes(report)
            for value in errors:
                print(f"ERROR {path}:1 [project_config] {value}")
        return 1 if errors else 0
    # The verbs change an override of the config setup wrote, and never create
    # one or write into one that check refuses outside its overrides.
    if not path.is_file():
        print(f"project_config: {path} does not exist; setup writes it", file=sys.stderr)
        return 1
    invalid = base_errors(config)
    if invalid:
        for value in invalid:
            print(f"project_config: {value}", file=sys.stderr)
        return 1
    slot = None
    try:
        if args.command == "set-class":
            if args.default and (args.model is not None or args.effort is not None):
                raise ValueError("--default takes no --model or --effort")
            if not args.default and args.model is None and args.effort is None:
                raise ValueError("set-class needs --model, --effort or --default")
            slot = f"{args.model_class}_{role_settings.ESCALATED}" if args.escalated \
                else args.model_class
            proposed, before, after = role_settings.set_class(
                config, class_map, args.host, slot, model=args.model, effort=args.effort,
                default=args.default, confirmed=args.confirmed)
        else:
            if args.default == (args.model_class is not None):
                raise ValueError("set-role-class needs exactly one of --class or --default")
            proposed, before, after = role_settings.set_role_class(
                config, class_map, args.role, args.model_class, default=args.default)
    except ValueError as exc:
        print(f"project_config: {exc}", file=sys.stderr)
        return 1
    # A repair may leave another broken override; it must add no error of its own.
    previous = set(check(config))
    errors = check(proposed)
    added = [value for value in errors if value not in previous]
    if added:
        for value in added:
            print(f"project_config: {value}", file=sys.stderr)
        return 1
    if not args.dry_run and proposed != config:
        atomic(path, proposed)
    result = {"ok": True, "config": str(path), "before": before, "after": after,
              "dry_run": args.dry_run, "errors": errors}
    if args.command == "set-class":
        where = f"{role_settings.CLASS_MODELS}.{args.host}.{slot}"
        # A removed override of a retired slot or host has no setting left.
        known = args.host in class_map["hosts"] \
            and slot in role_settings.class_slots(class_map, args.host)
        setting = role_settings.slot_setting(
            class_map, args.host, slot,
            role_settings.host_overrides(proposed, args.host).get(slot)) if known else {}
        result.update(host=args.host, slot=slot, model=setting.get("model"),
                      effort=setting.get("effort"))
    else:
        where = f"{role_settings.ROLE_CLASSES}.{args.role}"
        roles = role_settings.movable_roles(class_map)
        result.update(role=args.role, model_class=after or roles.get(args.role))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        suffix = " (dry run)" if args.dry_run else ""
        print(f"project_config: {where}: {before!r} -> {after!r}{suffix}")
    return 0


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
    p = sub.add_parser("classes", help="report the config-level effective map of each host")
    p.add_argument("--config", required=True)
    p.add_argument("--host")
    p.add_argument("--model-list", action="store_true",
                   help="add each class slot's model choices, from the active host's own list")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("set-class",
                       help="set or remove one class slot's model and effort override")
    p.add_argument("--config", required=True)
    p.add_argument("--host", required=True)
    p.add_argument("--class", dest="model_class", required=True)
    p.add_argument("--escalated", action="store_true",
                   help="set the class's escalated slot instead of its default one")
    p.add_argument("--model", help="a model ID of the host's list, or session")
    p.add_argument("--effort")
    p.add_argument("--default", action="store_true",
                   help="remove the override, so the package model and effort apply")
    p.add_argument("--confirmed", action="store_true",
                   help="the owner confirmed an effort the host asks to confirm")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("set-role-class", help="move one role to another class, or back")
    p.add_argument("--config", required=True)
    p.add_argument("--role", required=True)
    p.add_argument("--class", dest="model_class")
    p.add_argument("--default", action="store_true",
                   help="remove the override, so the role runs on its package class")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    path = Path(args.config).resolve()
    try:
        config = load(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"project_config: cannot read {path}: {exc}", file=sys.stderr)
        return 2
    if args.command in {"classes", "set-class", "set-role-class"}:
        return setting_command(args, path, config)
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
