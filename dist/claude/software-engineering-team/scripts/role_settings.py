#!/usr/bin/env python3
"""Resolve the tier, model and reasoning effort of every role, per host.

Every built package ships `templates/tier-map.json`: each role's package
tier, generated variants included, and per host each tier's package model and
effort, the host's model catalog with the efforts each model takes, the
host's effort vocabulary and model ID shape, and the efforts the host asks the
owner to confirm or refuses. `workspace/config.json` holds only a project's
overrides:

- `role_tiers` moves any role to the high, medium or low tier on every host.
- `tier_models.<host>.<tier>` sets that tier's `model`, a model ID of the
  host's shape or `session`, and its `effort`; a missing key keeps the
  package value. `session` runs the tier's roles on the session's model.

`resolve` applies them in that order for the project config writer and the
host project generators, so every surface reports what setup renders.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path


TIER_MAP = Path(__file__).resolve().parents[1] / "templates" / "tier-map.json"
PROVENANCE = ".agent-marketplace-package.json"
TIER_MAP_SCHEMA_VERSION = 2
TIER_MODELS = "tier_models"
ROLE_TIERS = "role_tiers"
SESSION = "session"
# role_tiers moves a role only onto these tiers; tier_models sets the tiers
# of a host that pin a model.
ROLE_TIER_CHOICES = ("high", "medium", "low")
SETTING_KEYS = ("model", "effort")
PACKAGE, CONFIG = "package", "config"
REMEDY = " (change it through /configure models)"


def load_tier_map(path: Path = TIER_MAP) -> dict | None:
    """Return the installed package's tier map, or None when it ships none."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError(f"package tier map {path} is unreadable: {exc}") from exc
    if not valid_tier_map(value):
        raise ValueError(f"package tier map {path} is invalid; reinstall the package")
    return value


def valid_tier_map(value: object) -> bool:
    if not isinstance(value, dict) or value.get("schema_version") != TIER_MAP_SCHEMA_VERSION:
        return False
    roles, hosts = value.get("roles"), value.get("hosts")
    if not isinstance(roles, dict) or not isinstance(hosts, dict) or not all(
            isinstance(names, list) and all(isinstance(name, str) for name in names)
            for names in roles.values()):
        return False
    for spec in hosts.values():
        if not isinstance(spec, dict) or not isinstance(spec.get("tiers"), dict) \
                or not isinstance(spec.get("models"), dict) \
                or not isinstance(spec.get("efforts"), list) \
                or not isinstance(spec.get("model_id_shape"), str) \
                or not isinstance(spec.get("confirm"), dict) \
                or not isinstance(spec.get("refuse"), dict):
            return False
        if not all(isinstance(entry, dict) and isinstance(entry.get("efforts"), list)
                   for entry in spec["models"].values()):
            return False
        if not all(isinstance(tier, dict) for tier in spec["tiers"].values()):
            return False
    return True


def package_tiers(tier_map: dict) -> dict[str, str]:
    """Each role with the tier the package runs it on."""
    return {role: tier for tier, names in tier_map["roles"].items() for role in names}


def configurable_tiers(tier_map: dict, host: str) -> list[str]:
    """The tiers of one host that pin a model, which `tier_models` sets."""
    return [tier for tier, spec in tier_map["hosts"][host]["tiers"].items() if "model" in spec]


def model_known(host_spec: dict, model: object) -> bool:
    """Whether ``model`` is `session`, a catalog model or an ID of the host's shape."""
    if not isinstance(model, str):
        return False
    return model == SESSION or model in host_spec["models"] \
        or re.fullmatch(host_spec["model_id_shape"], model) is not None


def model_efforts(tier_map: dict, host: str, model: str) -> list[str]:
    """The efforts ``model`` takes on the host.

    A catalog model takes its catalog efforts; the session's model takes any
    effort the host's catalog knows; any other ID takes the host's vocabulary
    until the host's model list says more.
    """
    spec = tier_map["hosts"][host]
    if model == SESSION:
        known = {effort for entry in spec["models"].values() for effort in entry["efforts"]}
        return [effort for effort in spec["efforts"] if effort in known]
    entry = spec["models"].get(model)
    return list(entry["efforts"]) if entry is not None else list(spec["efforts"])


def choices(tier_map: dict, host: str, model: str) -> list[str]:
    """The efforts a project may set with ``model``: what it takes, less the refused."""
    refused = tier_map["hosts"][host]["refuse"]
    return [effort for effort in model_efforts(tier_map, host, model) if effort not in refused]


def model_name(model: str) -> str:
    return "the session's model" if model == SESSION else model


def tier_setting(tier_map: dict, host: str, tier: str, override: object = None) -> dict:
    """One tier's model and effort, each with its source.

    A missing key keeps the package value; a model that takes no effort
    drops the package effort.
    """
    package = tier_map["hosts"][host]["tiers"].get(tier, {})
    override = override if isinstance(override, dict) else {}
    model = override["model"] if "model" in override else package.get("model", SESSION)
    if "effort" in override:
        effort, effort_source = override["effort"], CONFIG
    else:
        effort, effort_source = package.get("effort"), PACKAGE
        if effort is not None and isinstance(model, str) \
                and not model_efforts(tier_map, host, model):
            effort = None
    return {"model": model, "model_source": CONFIG if "model" in override else PACKAGE,
            "effort": effort, "effort_source": effort_source}


def host_overrides(config: dict, host: str) -> dict:
    """The `tier_models` entries of one host, an empty map for any other shape."""
    value = config.get(TIER_MODELS)
    tiers = value.get(host) if isinstance(value, dict) else None
    return tiers if isinstance(tiers, dict) else {}


def tier_settings(tier_map: dict, config: dict, host: str) -> dict[str, dict]:
    """Every tier of one host that pins a model, with its model and effort."""
    overrides = host_overrides(config, host)
    return {tier: tier_setting(tier_map, host, tier, overrides.get(tier))
            for tier in configurable_tiers(tier_map, host)}


def resolve(tier_map: dict, config: dict, host: str) -> dict[str, dict]:
    """Every role's tier, model and effort on one host, each with its source.

    The role's tier comes from `role_tiers`, else the package; that tier's
    model and effort come from `tier_models`, else the package profile.
    """
    moved = config.get(ROLE_TIERS)
    moved = moved if isinstance(moved, dict) else {}
    overrides = host_overrides(config, host)
    rows = {}
    for role, package_tier in sorted(package_tiers(tier_map).items()):
        tier = moved.get(role, package_tier)
        if tier not in ROLE_TIER_CHOICES:
            tier = package_tier
        rows[role] = {"tier": tier, "tier_source": PACKAGE if tier == package_tier else CONFIG,
                      **tier_setting(tier_map, host, tier, overrides.get(tier))}
    return rows


def config_errors(config: dict, tier_map: dict | None) -> list[str]:
    """Why the project's `tier_models` and `role_tiers` do not fit the package."""
    present = [key for key in (TIER_MODELS, ROLE_TIERS) if key in config]
    if not present:
        return []
    if tier_map is None:
        return [f"{key} needs the package tier map templates/tier-map.json, which this package"
                " does not ship; run the installed package's scripts" for key in present]
    errors: list[str] = []
    if TIER_MODELS in config:
        errors.extend(tier_models_errors(config[TIER_MODELS], tier_map))
    if ROLE_TIERS in config:
        errors.extend(role_tiers_errors(config[ROLE_TIERS], tier_map))
    return errors


def tier_models_errors(value: object, tier_map: dict) -> list[str]:
    if not isinstance(value, dict) or not value:
        return [f"{TIER_MODELS} must map a host to its tiers' model and effort" + REMEDY]
    hosts = tier_map["hosts"]
    errors: list[str] = []
    for host in sorted(value):
        where = f"{TIER_MODELS}.{host}"
        tiers = value[host]
        if host not in hosts:
            errors.append(f"{where}: unknown host; hosts are {', '.join(sorted(hosts))}" + REMEDY)
        elif not isinstance(tiers, dict) or not tiers:
            errors.append(f"{where} must map a tier to its model and effort" + REMEDY)
        else:
            for tier in sorted(tiers):
                errors.extend(tier_errors(tier_map, host, tier, tiers[tier]))
    return errors


def tier_errors(tier_map: dict, host: str, tier: str, override: object) -> list[str]:
    """Why one tier's override does not fit the host."""
    where = f"{TIER_MODELS}.{host}.{tier}"
    tiers = configurable_tiers(tier_map, host)
    if tier not in tiers:
        return [f"{where}: unknown tier; tiers are {', '.join(sorted(tiers))}" + REMEDY]
    if not isinstance(override, dict) or not override or not set(override) <= set(SETTING_KEYS):
        return [f"{where} must hold a model, an effort or both" + REMEDY]
    spec = tier_map["hosts"][host]
    model = override.get("model", spec["tiers"][tier]["model"])
    if not model_known(spec, model):
        return [f"{where}.model: {model!r} is neither {SESSION!r} nor a model ID of the"
                " host's model list" + REMEDY]
    efforts = model_efforts(tier_map, host, model)
    named = model_name(model)
    if "effort" not in override:
        package = spec["tiers"][tier].get("effort")
        if package is not None and efforts and package not in efforts:
            return [f"{where}: {named} does not take the package effort {package!r}; set one"
                    f" of {', '.join(choices(tier_map, host, model))}" + REMEDY]
        return []
    effort = override["effort"]
    if not isinstance(effort, str) or not effort:
        return [f"{where}.effort must name an effort" + REMEDY]
    refused = spec["refuse"].get(effort)
    if refused is not None:
        return [f"{where}.effort: {effort!r} is refused for every tier: {refused['reason']}"
                + REMEDY]
    if not efforts:
        return [f"{where}.effort: {named} takes no effort; leave the effort out" + REMEDY]
    if effort not in efforts:
        return [f"{where}.effort: {named} does not take {effort!r}; it takes"
                f" {', '.join(choices(tier_map, host, model))}" + REMEDY]
    return []


def role_tiers_errors(value: object, tier_map: dict) -> list[str]:
    if not isinstance(value, dict) or not value:
        return [f"{ROLE_TIERS} must map a role to the high, medium or low tier" + REMEDY]
    roles = package_tiers(tier_map)
    errors: list[str] = []
    for role in sorted(value):
        where = f"{ROLE_TIERS}.{role}"
        if role not in roles:
            errors.append(f"{where}: unknown role; roles are {', '.join(sorted(roles))}" + REMEDY)
        elif value[role] not in ROLE_TIER_CHOICES:
            errors.append(f"{where}: the tier must be one of {', '.join(ROLE_TIER_CHOICES)}"
                          + REMEDY)
    return errors


def normalized(config: dict) -> dict:
    """The project's overrides without the empty maps that set nothing.

    Any other shape is kept as it is, so the check reports it.
    """
    kept: dict = {}
    value = config.get(TIER_MODELS)
    if isinstance(value, dict):
        hosts = {}
        for host, tiers in value.items():
            if isinstance(tiers, dict):
                tiers = {tier: entry for tier, entry in tiers.items() if entry != {}}
                if not tiers:
                    continue
            hosts[host] = tiers
        if hosts:
            kept[TIER_MODELS] = hosts
    elif TIER_MODELS in config:
        kept[TIER_MODELS] = value
    value = config.get(ROLE_TIERS)
    if value != {} and ROLE_TIERS in config:
        kept[ROLE_TIERS] = value
    return kept


def set_tier(config: dict, tier_map: dict, host: str, tier: str, *,
             model: str | None = None, effort: str | None = None, default: bool = False,
             confirmed: bool = False) -> tuple[dict, object, object]:
    """Return the config with one tier's override changed, and its entry before and after.

    A value equal to the package's records nothing, so a later package change
    reaches the project, and a key not given keeps its override. ``default``
    removes the entry the config holds, also for a tier or host the package
    no longer has. An effort the host confirms needs ``confirmed``.
    """
    current = copy.deepcopy(config.get(TIER_MODELS))
    current = current if isinstance(current, dict) else {}
    tiers = current.get(host) if isinstance(current.get(host), dict) else {}
    before = tiers.get(tier)
    hosts = tier_map["hosts"]
    if not default or before is None:
        if host not in hosts:
            raise ValueError(f"{TIER_MODELS}.{host}: unknown host; hosts are"
                             f" {', '.join(sorted(hosts))}")
        if tier not in configurable_tiers(tier_map, host):
            raise ValueError(f"{TIER_MODELS}.{host}.{tier}: unknown tier; tiers are"
                             f" {', '.join(sorted(configurable_tiers(tier_map, host)))}")
    if default:
        after = None
    else:
        package = hosts[host]["tiers"][tier]
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
            errors = tier_errors(tier_map, host, tier, after)
            if errors:
                raise ValueError(errors[0])
        was = tier_setting(tier_map, host, tier, before)["effort"]
        now = tier_setting(tier_map, host, tier, after)["effort"]
        if now in hosts[host]["confirm"] and now != was and not confirmed:
            raise ValueError(
                f"{TIER_MODELS}.{host}.{tier}: effort {now!r} needs the owner's confirmation:"
                " ask the confirmation choice-gate question with the evidence that `tiers`"
                " reports, then pass --confirmed")
    if after is None:
        tiers.pop(tier, None)
    else:
        tiers[tier] = after
    if tiers:
        current[host] = tiers
    else:
        current.pop(host, None)
    proposed = dict(config)
    proposed.pop(TIER_MODELS, None)
    if current:
        proposed[TIER_MODELS] = current
    return proposed, before, after


def set_role_tier(config: dict, tier_map: dict, role: str, tier: str | None = None, *,
                  default: bool = False) -> tuple[dict, object, object]:
    """Return the config with one role's tier changed, and its entry before and after.

    The role's package tier records nothing; ``default`` removes the entry the
    config holds, also for a role the package no longer has.
    """
    current = dict(config.get(ROLE_TIERS)) if isinstance(config.get(ROLE_TIERS), dict) else {}
    before = current.get(role)
    roles = package_tiers(tier_map)
    if default:
        if before is None and role not in roles:
            raise ValueError(role_tiers_errors({role: None}, tier_map)[0])
        after = None
    else:
        errors = role_tiers_errors({role: tier}, tier_map)
        if errors:
            raise ValueError(errors[0])
        after = None if tier == roles[role] else tier
    if after is None:
        current.pop(role, None)
    else:
        current[role] = after
    proposed = dict(config)
    proposed.pop(ROLE_TIERS, None)
    if current:
        proposed[ROLE_TIERS] = current
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
    """`package`, or which of the tier, model and effort another source sets."""
    named: dict[str, list[str]] = {}
    for key in ("tier", "model", "effort"):
        source = row.get(f"{key}_source", PACKAGE)
        if source != PACKAGE:
            named.setdefault(source, []).append(key)
    return "; ".join(f"{source}: {', '.join(keys)}" for source, keys in named.items()) \
        or PACKAGE


def table(rows: dict[str, dict]) -> list[str]:
    """The effective table: role, tier, model, effort and source of every role."""
    lines = [("role", "tier", "model", "effort", "source")]
    lines.extend((role, row["tier"], row["model"], row["effort"] or SESSION, source_text(row))
                 for role, row in sorted(rows.items()))
    widths = [max(len(line[index]) for line in lines) for index in range(5)]
    return ["  ".join(cell.ljust(width) for cell, width in zip(line, widths)).rstrip()
            for line in lines]
