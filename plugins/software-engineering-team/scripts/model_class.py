#!/usr/bin/env python3
"""Resolve the model and effort of one spawn from its purpose class.

Every step that spawns an agent names the purpose class of the work: the
role's own class (its `model_class`) unless the step says another, such as a
security review. The class runs one level up, its escalated slot, for a task
of size `l`, at rigor `high` or on a risk flag the package declares; a class
that does not escalate never moves. The model and effort come from the
package class map and the project's `workspace/config.json` overrides, as
`role_settings.resolve` reads them.

A host whose `spawn_model` is `alias` takes the model's family alias and the
effort on the spawn itself; on a host whose role file decides, the resolver
names the rendered variant pinned to the slot (`<agent>-escalated`, or a
purpose variant), and the spawn passes no model.

    model_class.py resolve --class routine_review --role code-reviewer \\
        --task TK-012 --project-root . --host <host> --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import project_config
import role_settings
import task_lane

SIZES = task_lane.SIZES
RIGORS = task_lane.RIGORS


def escalates(size: str | None, rigor: str | None, risks: list[str]) -> bool:
    """Whether the work runs one level up: size l, rigor high or any risk flag."""
    return size == "l" or rigor == "high" or bool(risks)


def pick_agent(class_map: dict, config: dict, host: str, role: str, slot: str) -> str | None:
    """The rendered agent that runs ``role`` on ``slot`` on this host, if any.

    The role itself when its class is the slot's; else a variant rendered for
    the slot whose agent is the role.
    """
    rows = role_settings.resolve(class_map, config, host)
    if rows.get(role, {}).get("class") == slot:
        return role
    variants = class_map["hosts"][host]["variants"]
    for name, variant in sorted(variants.items()):
        if variant["agent"] == role and rows[name]["class"] == slot:
            return name
    return None


def resolve(class_map: dict, config: dict, host: str, model_class: str | None = None, *,
            role: str | None = None, size: str | None = None, rigor: str | None = None,
            risks: list[str] | None = None) -> dict:
    """The model, effort and agent one spawn runs.

    ``model_class`` defaults to the role's own class. The result names the
    class, the slot, whether it escalated, the model and effort in force, the
    agent to spawn and the `spawn` fields the spawn call passes; ``note``
    says when the host has no variant for the slot, so the agent runs its own
    pins.
    """
    if host not in class_map["hosts"]:
        raise ValueError(f"unknown host {host!r}; hosts are {', '.join(sorted(class_map['hosts']))}")
    risks = list(risks or [])
    unknown = sorted(set(risks) - set(class_map["risk_flags"]))
    if unknown:
        raise ValueError(f"unknown risk flag {', '.join(unknown)}; flags are"
                         f" {', '.join(class_map['risk_flags'])}")
    if size is not None and size not in SIZES:
        raise ValueError(f"size must be one of {', '.join(SIZES)}")
    if rigor is not None and rigor not in RIGORS:
        raise ValueError(f"rigor must be one of {', '.join(RIGORS)}")
    roles = role_settings.role_classes(class_map, config)
    if role is not None and role not in roles:
        raise ValueError(f"unknown role {role!r}; roles are {', '.join(sorted(roles))}")
    if model_class is None:
        if role is None:
            raise ValueError("name a --class, a --role or both")
        model_class = roles[role]
    if model_class not in class_map["model_classes"]:
        raise ValueError(f"unknown class {model_class!r}; classes are"
                         f" {', '.join(class_map['model_classes'])}")
    slot = role_settings.escalated_slot(class_map, model_class) \
        if escalates(size, rigor, risks) else model_class
    setting = role_settings.slot_setting(
        class_map, host, slot, role_settings.host_overrides(config, host).get(slot))
    spec = class_map["hosts"][host]
    result = {"host": host, "class": model_class, "slot": slot,
              "escalated": slot != model_class, "model": setting["model"],
              "effort": setting["effort"], "agent": role, "spawn": {}, "note": None}
    if spec["spawn_model"] == "alias":
        # The spawn sets both; `session` leaves the model to the session.
        family = spec["models"].get(setting["model"], {}).get("family")
        if setting["model"] != role_settings.SESSION:
            result["spawn"]["model"] = family or setting["model"]
        if setting["effort"] is not None:
            result["spawn"]["effort"] = setting["effort"]
    elif role is not None:
        agent = pick_agent(class_map, config, host, role, slot)
        if agent is None:
            row = role_settings.resolve(class_map, config, host)[role]
            result.update(model=row["model"], effort=row["effort"],
                          note=f"no {role} variant renders {slot} on {host};"
                               f" it runs its own {row['class']} pins")
        else:
            result["agent"] = agent
    return result


def task_facts(project_root: Path, task: str) -> tuple[str | None, str | None]:
    """A fast-lane task's size and rigor from its task file."""
    props = task_lane.task_props(task_lane.task_path(project_root, task))
    return props.get("size"), props.get("rigor", "normal")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("resolve", help="the model, effort and agent of one spawn")
    p.add_argument("--class", dest="model_class", help="the spawn's purpose class;"
                   " defaults to the role's own class")
    p.add_argument("--role", help="the role spawned, which names its rendered variant")
    p.add_argument("--host", required=True)
    p.add_argument("--task", help="a fast-lane task id; its size and rigor apply")
    p.add_argument("--project-root", default=".")
    p.add_argument("--size", choices=SIZES)
    p.add_argument("--rigor", choices=RIGORS)
    p.add_argument("--risk", action="append", default=[],
                   help="a risk flag of the class map; repeatable")
    p.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.project_root).resolve()
    try:
        class_map = role_settings.load_class_map()
        if class_map is None:
            raise ValueError("this package ships no class map templates/model-classes.json;"
                             " run the installed package's scripts")
        config_path = root / "workspace" / "config.json"
        config = project_config.load(config_path)
        errors = role_settings.config_errors(config, class_map)
        if errors:
            raise ValueError("; ".join(errors))
        size, rigor = args.size, args.rigor
        if args.task:
            task_size, task_rigor = task_facts(root, args.task)
            size, rigor = size or task_size, rigor or task_rigor
        result = resolve(class_map, config, args.host, args.model_class, role=args.role,
                         size=size, rigor=rigor, risks=args.risk)
    except (OSError, ValueError, task_lane.Refused) as exc:
        print(f"model_class: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        spawn = ", ".join(f"{key} {value}" for key, value in result["spawn"].items()) \
            or "the role file's pins"
        print(f"{result['agent'] or '-'}: {result['slot']} -> {result['model']} at"
              f" {result['effort'] or 'the session effort'}; spawn with {spawn}")
        if result["note"]:
            print(f"note: {result['note']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
