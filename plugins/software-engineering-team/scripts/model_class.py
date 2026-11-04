#!/usr/bin/env python3
"""Resolve an explicit compute profile and access boundary for a native worker.

    model_class.py resolve --profile work --access write --host <host> --json

The launch file pins the requested model and effort. Its presence proves
neither host selection nor the model actually served. Record observations
from the host separately. A fallback is another explicit resolution for the
same attempt, never a rewrite of shared project settings.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import project_config
import role_settings


def resolve(class_map: dict, config: dict, host: str, profile: str, *, access: str) -> dict:
    """Return the technical launch identity and its requested runtime settings."""
    if host not in class_map["hosts"]:
        raise ValueError(f"unknown host {host!r}")
    errors = role_settings.config_errors(config, class_map)
    if errors:
        raise ValueError("; ".join(errors))
    launches = class_map.get("launch_profiles", {})
    matches = [name for name, spec in launches.items()
               if spec["profile"] == profile and spec["access"] == access]
    if len(matches) != 1:
        raise ValueError(f"no unique launch definition for profile {profile!r} with access {access!r}")
    agent = matches[0]
    setting = role_settings.resolve(class_map, config, host)[agent]
    read_only = access == "read"
    read_access = class_map["hosts"][host]["read_access"]
    enforced = read_only and read_access["definition_restricts_tools"]
    permission_note = read_access["note"] if read_only else (
        "The worker inherits the host's parent permissions and the attempt's granted scope.")
    return {
        "host": host, "profile": profile, "access": access, "agent": agent,
        "model": setting["model"], "effort": setting["effort"],
        "requested_model": setting["model"], "requested_effort": setting["effort"],
        "model_source": setting["model_source"], "effort_source": setting["effort_source"],
        "observed_model": None, "observed_effort": None, "runtime_status": "unverified",
        "spawn": {},
        "permissions": {"declared_read_only": read_only,
                        "definition_restricts_tools": enforced,
                        "runtime_verified": False, "note": permission_note},
        "note": "Use the generated launch identity without a spawn-time model override."
                " Confirm its loaded settings with host evidence.",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("resolve", help="the launch identity for one bounded attempt")
    command.add_argument("--profile", required=True, choices=("light", "work", "review", "critical"))
    command.add_argument("--access", required=True, choices=("read", "write"))
    command.add_argument("--host", required=True)
    command.add_argument("--task", help="task identifier recorded with this resolution")
    command.add_argument("--project-root", default=".")
    command.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        class_map = role_settings.load_class_map()
        if class_map is None:
            raise ValueError("this package ships no templates/model-classes.json; run the installed package scripts")
        config = project_config.load(Path(args.project_root).resolve() / "workspace" / "config.json")
        result = resolve(class_map, config, args.host, args.profile, access=args.access)
        if args.task:
            result["task"] = args.task
    except (OSError, ValueError) as exc:
        print(f"model_class: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{result['agent']}: {result['profile']}/{result['access']} -> "
              f"{result['requested_model']} at {result['requested_effort']}; runtime unverified")
        print(result["permissions"]["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
