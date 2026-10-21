#!/usr/bin/env python3
"""Resolve the purpose class, model and reasoning effort of every role, per host.

Every built package ships `templates/model-classes.json`: each role's package
class, which classes escalate and on which risk flags, and per host each class
slot's package model and effort (a class that escalates has a second slot,
`<class>_escalated`), how a spawn sets its model, the host's model catalog with the
efforts each model takes, the host's effort vocabulary and model ID shape,
and the efforts the host asks the owner to confirm or refuses.
`workspace/config.json` holds only a project's overrides:

- `role_classes` moves a role to another class on every host.
- `class_models.<host>.<slot>` sets that slot's `model`, a model ID of the
  host's shape or `session`, and its `effort`; a missing key keeps the
  package value. `session` runs the slot's roles on the session's model.

`resolve` applies them in that order for the project config writer, the spawn
model resolver and the host project generators, so every surface reports
what setup renders.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path


CLASS_MAP = Path(__file__).resolve().parents[1] / "templates" / "model-classes.json"
PROVENANCE = ".agent-marketplace-package.json"
CLASS_MAP_SCHEMA_VERSION = 3
CLASS_MODELS = "class_models"
ROLE_CLASSES = "role_classes"
# Keys the purpose classes replaced; a config that still holds one is refused.
RETIRED_KEYS = {"tier_models": CLASS_MODELS, "role_tiers": ROLE_CLASSES}
ESCALATED = "escalated"
SESSION = "session"
SETTING_KEYS = ("model", "effort")
PACKAGE, CONFIG = "package", "config"
REMEDY = " (change it through /configure models)"


def load_class_map(path: Path = CLASS_MAP) -> dict | None:
    """Return the installed package's class map, or None when it ships none."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError(f"package class map {path} is unreadable: {exc}") from exc
    if not valid_class_map(value):
        raise ValueError(f"package class map {path} is invalid; reinstall the package")
    return value


def valid_class_map(value: object) -> bool:
    if not isinstance(value, dict) or value.get("schema_version") != CLASS_MAP_SCHEMA_VERSION:
        return False
    roles, hosts, classes = value.get("roles"), value.get("hosts"), value.get("model_classes")
    if not isinstance(roles, dict) or not isinstance(hosts, dict) \
            or not isinstance(classes, dict) or not isinstance(value.get("risk_flags"), list) \
            or not all(isinstance(spec, dict) and isinstance(spec.get("escalates"), bool)
                       for spec in classes.values()) \
            or not set(roles) <= set(classes) or not all(
                isinstance(names, list) and all(isinstance(name, str) for name in names)
                for names in roles.values()):
        return False
    for spec in hosts.values():
        if not isinstance(spec, dict) or not isinstance(spec.get("classes"), dict) \
                or not isinstance(spec.get("models"), dict) \
                or not isinstance(spec.get("efforts"), list) \
                or not isinstance(spec.get("model_id_shape"), str) \
                or not isinstance(spec.get("confirm"), dict) \
                or not isinstance(spec.get("refuse"), dict) \
                or spec.get("spawn_model") not in (None, "alias"):
            return False
        if not all(isinstance(entry, dict) and isinstance(entry.get("efforts"), list)
                   for entry in spec["models"].values()):
            return False
        if not all(isinstance(slot, dict) and isinstance(slot.get("model"), str)
                   for slot in spec["classes"].values()):
            return False
    return True


def escalated_slot(class_map: dict, model_class: str) -> str:
    """The slot one level up from ``model_class``: itself for a fixed class."""
    escalates = class_map["model_classes"].get(model_class, {}).get("escalates")
    return f"{model_class}_{ESCALATED}" if escalates else model_class


def movable_roles(class_map: dict) -> dict[str, str]:
    """Each role every host renders with its package class, which `role_classes` may move."""
    return {role: model_class for model_class, names in class_map["roles"].items()
            for role in names}


def package_classes(class_map: dict, host: str) -> dict[str, str]:
    """Each role the host renders with its package slot."""
    return movable_roles(class_map)


def class_slots(class_map: dict, host: str) -> list[str]:
    """The slots of one host, which `class_models` sets."""
    return list(class_map["hosts"][host]["classes"])


def model_known(host_spec: dict, model: object) -> bool:
    """Whether ``model`` is `session`, a catalog model or an ID of the host's shape."""
    if not isinstance(model, str):
        return False
    return model == SESSION or model in host_spec["models"] \
        or re.fullmatch(host_spec["model_id_shape"], model) is not None


def model_efforts(class_map: dict, host: str, model: str) -> list[str]:
    """The efforts ``model`` takes on the host.

    A catalog model takes its catalog efforts; the session's model takes any
    effort the host's catalog knows; any other ID takes the host's vocabulary
    until the host's model list says more.
    """
    spec = class_map["hosts"][host]
    if model == SESSION:
        known = {effort for entry in spec["models"].values() for effort in entry["efforts"]}
        return [effort for effort in spec["efforts"] if effort in known]
    entry = spec["models"].get(model)
    return list(entry["efforts"]) if entry is not None else list(spec["efforts"])


def choices(class_map: dict, host: str, model: str) -> list[str]:
    """The efforts a project may set with ``model``: what it takes, less the refused."""
    refused = class_map["hosts"][host]["refuse"]
    return [effort for effort in model_efforts(class_map, host, model) if effort not in refused]


def model_name(model: str) -> str:
    return "the session's model" if model == SESSION else model


def slot_setting(class_map: dict, host: str, slot: str, override: object = None) -> dict:
    """One slot's model and effort, each with its source.

    A missing key keeps the package value; a model that takes no effort
    drops the package effort.
    """
    package = class_map["hosts"][host]["classes"].get(slot, {})
    override = override if isinstance(override, dict) else {}
    model = override["model"] if "model" in override else package.get("model", SESSION)
    if "effort" in override:
        effort, effort_source = override["effort"], CONFIG
    else:
        effort, effort_source = package.get("effort"), PACKAGE
        if effort is not None and isinstance(model, str) \
                and not model_efforts(class_map, host, model):
            effort = None
    return {"model": model, "model_source": CONFIG if "model" in override else PACKAGE,
            "effort": effort, "effort_source": effort_source}


def host_overrides(config: dict, host: str) -> dict:
    """The `class_models` entries of one host, an empty map for any other shape."""
    value = config.get(CLASS_MODELS)
    slots = value.get(host) if isinstance(value, dict) else None
    return slots if isinstance(slots, dict) else {}


def slot_settings(class_map: dict, config: dict, host: str) -> dict[str, dict]:
    """Every slot of one host with its model and effort."""
    overrides = host_overrides(config, host)
    return {slot: slot_setting(class_map, host, slot, overrides.get(slot))
            for slot in class_slots(class_map, host)}


def role_classes(class_map: dict, config: dict) -> dict[str, str]:
    """Each movable role's class: `role_classes`, else the package's."""
    moved = config.get(ROLE_CLASSES)
    moved = moved if isinstance(moved, dict) else {}
    return {role: moved[role] if moved.get(role) in class_map["model_classes"] else package
            for role, package in movable_roles(class_map).items()}


def resolve(class_map: dict, config: dict, host: str) -> dict[str, dict]:
    """Every role's class slot, model and effort on one host, each with its source.

    A role's class comes from `role_classes`, else the package; that slot's
    model and effort come from `class_models`, else the package profile.
    """
    classes = role_classes(class_map, config)
    package = package_classes(class_map, host)
    overrides = host_overrides(config, host)
    rows = {}
    for role, package_slot in sorted(package.items()):
        slot = classes.get(role, package_slot)
        rows[role] = {"class": slot, "class_source": PACKAGE if slot == package_slot else CONFIG,
                      **slot_setting(class_map, host, slot, overrides.get(slot))}
    return rows


def config_errors(config: dict, class_map: dict | None) -> list[str]:
    """Why the project's `class_models` and `role_classes` do not fit the package."""
    errors = [f"{old} is retired: the purpose classes replaced the tiers; a setup refresh"
              f" drops it, then set {new}" + REMEDY for old, new in RETIRED_KEYS.items()
              if old in config]
    present = [key for key in (CLASS_MODELS, ROLE_CLASSES) if key in config]
    if not present:
        return errors
    if class_map is None:
        return errors + [f"{key} needs the package class map templates/model-classes.json,"
                         " which this package does not ship; run the installed package's"
                         " scripts" for key in present]
    if CLASS_MODELS in config:
        errors.extend(class_models_errors(config[CLASS_MODELS], class_map))
    if ROLE_CLASSES in config:
        errors.extend(role_classes_errors(config[ROLE_CLASSES], class_map))
    return errors


def class_models_errors(value: object, class_map: dict) -> list[str]:
    if not isinstance(value, dict) or not value:
        return [f"{CLASS_MODELS} must map a host to its class slots' model and effort" + REMEDY]
    hosts = class_map["hosts"]
    errors: list[str] = []
    for host in sorted(value):
        where = f"{CLASS_MODELS}.{host}"
        slots = value[host]
        if host not in hosts:
            errors.append(f"{where}: unknown host; hosts are {', '.join(sorted(hosts))}" + REMEDY)
        elif not isinstance(slots, dict) or not slots:
            errors.append(f"{where} must map a class slot to its model and effort" + REMEDY)
        else:
            for slot in sorted(slots):
                errors.extend(slot_errors(class_map, host, slot, slots[slot]))
    return errors


def slot_errors(class_map: dict, host: str, slot: str, override: object) -> list[str]:
    """Why one slot's override does not fit the host."""
    where = f"{CLASS_MODELS}.{host}.{slot}"
    slots = class_slots(class_map, host)
    if slot not in slots:
        return [f"{where}: unknown class slot; slots are {', '.join(sorted(slots))}" + REMEDY]
    if not isinstance(override, dict) or not override or not set(override) <= set(SETTING_KEYS):
        return [f"{where} must hold a model, an effort or both" + REMEDY]
    spec = class_map["hosts"][host]
    model = override.get("model", spec["classes"][slot]["model"])
    if not model_known(spec, model):
        return [f"{where}.model: {model!r} is neither {SESSION!r} nor a model ID of the"
                " host's model list" + REMEDY]
    efforts = model_efforts(class_map, host, model)
    named = model_name(model)
    if "effort" not in override:
        package = spec["classes"][slot].get("effort")
        if package is not None and efforts and package not in efforts:
            return [f"{where}: {named} does not take the package effort {package!r}; set one"
                    f" of {', '.join(choices(class_map, host, model))}" + REMEDY]
        return []
    effort = override["effort"]
    if not isinstance(effort, str) or not effort:
        return [f"{where}.effort must name an effort" + REMEDY]
    refused = spec["refuse"].get(effort)
    if refused is not None:
        return [f"{where}.effort: {effort!r} is refused for every class: {refused['reason']}"
                + REMEDY]
    if not efforts:
        return [f"{where}.effort: {named} takes no effort; leave the effort out" + REMEDY]
    if effort not in efforts:
        return [f"{where}.effort: {named} does not take {effort!r}; it takes"
                f" {', '.join(choices(class_map, host, model))}" + REMEDY]
    return []


def role_classes_errors(value: object, class_map: dict) -> list[str]:
    classes = ", ".join(class_map["model_classes"])
    if not isinstance(value, dict) or not value:
        return [f"{ROLE_CLASSES} must map a role to one of the classes {classes}" + REMEDY]
    roles = movable_roles(class_map)
    errors: list[str] = []
    for role in sorted(value):
        where = f"{ROLE_CLASSES}.{role}"
        if role not in roles:
            errors.append(f"{where}: unknown role; roles are {', '.join(sorted(roles))}" + REMEDY)
        elif value[role] not in class_map["model_classes"]:
            errors.append(f"{where}: the class must be one of {classes}" + REMEDY)
    return errors


def normalized(config: dict) -> dict:
    """The project's overrides without the empty maps that set nothing.

    Any other shape is kept as it is, so the check reports it.
    """
    kept: dict = {}
    value = config.get(CLASS_MODELS)
    if isinstance(value, dict):
        hosts = {}
        for host, slots in value.items():
            if isinstance(slots, dict):
                slots = {slot: entry for slot, entry in slots.items() if entry != {}}
                if not slots:
                    continue
            hosts[host] = slots
        if hosts:
            kept[CLASS_MODELS] = hosts
    elif CLASS_MODELS in config:
        kept[CLASS_MODELS] = value
    value = config.get(ROLE_CLASSES)
    if value != {} and ROLE_CLASSES in config:
        kept[ROLE_CLASSES] = value
    return kept


def set_class(config: dict, class_map: dict, host: str, slot: str, *,
              model: str | None = None, effort: str | None = None, default: bool = False,
              confirmed: bool = False) -> tuple[dict, object, object]:
    """Return the config with one slot's override changed, and its entry before and after.

    A value equal to the package's records nothing, so a later package change
    reaches the project, and a key not given keeps its override. ``default``
    removes the entry the config holds, also for a slot or host the package
    no longer has. An effort the host confirms needs ``confirmed``.
    """
    current = copy.deepcopy(config.get(CLASS_MODELS))
    current = current if isinstance(current, dict) else {}
    slots = current.get(host) if isinstance(current.get(host), dict) else {}
    before = slots.get(slot)
    hosts = class_map["hosts"]
    if not default or before is None:
        if host not in hosts:
            raise ValueError(f"{CLASS_MODELS}.{host}: unknown host; hosts are"
                             f" {', '.join(sorted(hosts))}")
        if slot not in class_slots(class_map, host):
            raise ValueError(f"{CLASS_MODELS}.{host}.{slot}: unknown class slot; slots are"
                             f" {', '.join(sorted(class_slots(class_map, host)))}")
    if default:
        after = None
    else:
        package = hosts[host]["classes"][slot]
        entry = dict(before) if isinstance(before, dict) else {}
        for key, value in (("model", model), ("effort", effort)):
            if value is None:
                continue
            if value == package.get(key):
                entry.pop(key, None)
            else:
                entry[key] = value
        after = entry or None
        if after is not None:
            errors = slot_errors(class_map, host, slot, after)
            if errors:
                raise ValueError(errors[0])
        was = slot_setting(class_map, host, slot, before)["effort"]
        now = slot_setting(class_map, host, slot, after)["effort"]
        if now in hosts[host]["confirm"] and now != was and not confirmed:
            raise ValueError(
                f"{CLASS_MODELS}.{host}.{slot}: effort {now!r} needs the owner's confirmation:"
                " ask the confirmation choice-gate question with the evidence that `classes`"
                " reports, then pass --confirmed")
    if after is None:
        slots.pop(slot, None)
    else:
        slots[slot] = after
    if slots:
        current[host] = slots
    else:
        current.pop(host, None)
    proposed = dict(config)
    proposed.pop(CLASS_MODELS, None)
    if current:
        proposed[CLASS_MODELS] = current
    return proposed, before, after


def set_role_class(config: dict, class_map: dict, role: str, model_class: str | None = None,
                   *, default: bool = False) -> tuple[dict, object, object]:
    """Return the config with one role's class changed, and its entry before and after.

    The role's package class records nothing; ``default`` removes the entry
    the config holds, also for a role the package no longer has.
    """
    current = dict(config.get(ROLE_CLASSES)) if isinstance(config.get(ROLE_CLASSES), dict) else {}
    before = current.get(role)
    roles = movable_roles(class_map)
    if default:
        if before is None and role not in roles:
            raise ValueError(role_classes_errors({role: None}, class_map)[0])
        after = None
    else:
        errors = role_classes_errors({role: model_class}, class_map)
        if errors:
            raise ValueError(errors[0])
        after = None if model_class == roles[role] else model_class
    if after is None:
        current.pop(role, None)
    else:
        current[role] = after
    proposed = dict(config)
    proposed.pop(ROLE_CLASSES, None)
    if current:
        proposed[ROLE_CLASSES] = current
    return proposed, before, after


def settings_digest(setting: dict) -> str:
    """The digest of what a rendered role runs: its model and its effort."""
    value = {"model": setting["model"], "effort": setting["effort"]}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def package_version(plugin_root: Path) -> str:
    """Return the installed package's version from its provenance manifest."""
    try:
        value = json.loads((plugin_root / PROVENANCE).read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"the installed package's {PROVENANCE} names no version;"
                         " reinstall the package") from exc
    if not isinstance(value, str) or re.fullmatch(r"\S+", value) is None:
        raise ValueError(f"the installed package's {PROVENANCE} names no valid version;"
                         " reinstall the package")
    return value


def stamp(team: str, version: str, source: Path, setting: dict) -> str:
    """Return the header line naming the package, the source agent and the
    resolved settings a role renders from, on either host.

    A plugin update changes the version or the source, and a project config
    change the role's model or effort, so a rendered role that keeps the
    previous stamp runs the previous role until a refresh renders it again.
    """
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return (f"# Rendered from {team} {version} agents/{source.name} sha256:{digest}"
            f" settings sha256:{settings_digest(setting)}")


def source_text(row: dict) -> str:
    """`package`, or which of the class, model and effort another source sets."""
    named: dict[str, list[str]] = {}
    for key in ("class", "model", "effort"):
        source = row.get(f"{key}_source", PACKAGE)
        if source != PACKAGE:
            named.setdefault(source, []).append(key)
    return "; ".join(f"{source}: {', '.join(keys)}" for source, keys in named.items()) \
        or PACKAGE


def table(rows: dict[str, dict]) -> list[str]:
    """The effective table: role, class, model, effort and source of every role."""
    lines = [("role", "class", "model", "effort", "source")]
    lines.extend((role, row["class"], row["model"], row["effort"] or SESSION, source_text(row))
                 for role, row in sorted(rows.items()))
    widths = [max(len(line[index]) for line in lines) for index in range(5)]
    return ["  ".join(cell.ljust(width) for cell, width in zip(line, widths)).rstrip()
            for line in lines]
