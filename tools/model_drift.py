#!/usr/bin/env python3
"""Report host models that are newer than the pinned models.

Each host pins its models by exact ID in ``platforms/<host>/model-catalog.json``,
and each tier of its ``execution-profiles.json`` names one of them. This tool
compares those pins with a copy of the host's own model catalog and prints a
JSON report: pinned IDs the host no longer lists, newer models of each pinned
family and changed effort support. ``--issue-body`` renders the upstream issue
instead: the catalog and profile bump diff and the frozen-task A/B that
decides it. The tool reads local files only and never calls the network or
GitHub.

Host catalogs it reads, detected by shape:

- Codex: only the JSON of ``codex debug models --bundled``, the catalog
  bundled with the installed CLI, with ``--cli-version codex=<version>``. The
  report records that CLI and refuses one older than the release the pinned
  sources name. The signed-in ``codex debug models`` is never a capture: when
  it cannot refresh online, or runs on an API key, it prints the cached or
  bundled catalog in the same shape and still exits 0.
- Claude: the JSON of the Models API list, ``GET /v1/models``, or any text
  such as the models overview page as Markdown, whose model IDs are read in
  the host's documented ID format.

Exit status: 0 without drift, 1 with drift, 2 for invalid input, 3 when a
registered host went unchecked without ``--subset``.
"""

from __future__ import annotations

import argparse
import datetime
import difflib
import json
import re
import sys
from pathlib import Path

import build_distributions


ROOT = Path(__file__).resolve().parent.parent
REPORT_SCHEMA_VERSION = 1
EXIT_CLEAN, EXIT_DRIFT, EXIT_INVALID, EXIT_UNCHECKED = 0, 1, 2, 3
AB_REFERENCE = (
    "plugins/software-engineering-team/skill-content/challenge-review/"
    "references/switch-mechanical_pass_tier-mechanical.md"
)
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
CLI_VERSION_RE = re.compile(r"(?<![0-9.])([0-9]+\.[0-9]+\.[0-9]+)(?![0-9.])")


def text_models(text: str, adapter) -> dict[str, list[str] | None]:
    """Return the model IDs a text names in the host's documented format."""
    found: dict[str, list[str] | None] = {}
    for token in TOKEN_RE.findall(text):
        token = token.rstrip("._-")
        # Provider forms such as anthropic.claude-... name the same model.
        for start in [0] + [index + 1 for index, char in enumerate(token) if char == "."]:
            if adapter.module.model_version(token[start:]) is not None:
                found.setdefault(token[start:], None)
                break
    return found


def anthropic_efforts(entry: dict, adapter) -> list[str] | None:
    """Return the efforts a Models API entry supports, None when unstated."""
    capabilities = entry.get("capabilities")
    effort = capabilities.get("effort") if isinstance(capabilities, dict) else None
    if not isinstance(effort, dict):
        return None
    if effort.get("supported") is not True:
        return []
    return [
        level for level in adapter.module.EFFORT_LEVELS
        if isinstance(effort.get(level), dict) and effort[level].get("supported") is True
    ]


def listed_models(path: Path, adapter) -> tuple[str, dict[str, list[str] | None]]:
    """Return a host catalog's format and its listed models with their efforts."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"{path}: unreadable host catalog: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    models: dict[str, list[str] | None] = {}
    if isinstance(data, dict) and isinstance(data.get("models"), list):
        form = "codex_models"
        for entry in data["models"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("slug"), str):
                raise ValueError(f"{path}: a models entry has no slug")
            if entry.get("visibility") == "hide":
                continue
            levels = entry.get("supported_reasoning_levels")
            models[entry["slug"]] = [
                level["effort"] for level in levels
                if isinstance(level, dict) and isinstance(level.get("effort"), str)
            ] if isinstance(levels, list) else None
    elif isinstance(data, dict) and isinstance(data.get("data"), list):
        form = "anthropic_models"
        for entry in data["data"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                raise ValueError(f"{path}: a data entry has no id")
            models[entry["id"]] = anthropic_efforts(entry, adapter)
    elif data is None:
        form = "text"
        models = text_models(text, adapter)
    else:
        raise ValueError(
            f"{path}: neither a Codex models catalog, a Models API list nor text"
        )
    known = {
        model_id: efforts for model_id, efforts in models.items()
        if adapter.module.model_version(model_id) is not None
    }
    if not known:
        raise ValueError(
            f"{path}: lists no {adapter.host_id} model ID in the host's documented format"
        )
    return form, known


def roles_by_tier(root: Path) -> dict[str, list[str]]:
    """Return every generated agent per tier: canonical agents and variants."""
    roles: dict[str, set[str]] = {}
    for source in sorted(path for path in (root / "plugins").iterdir() if path.is_dir()):
        for agent in sorted((source / "agents").glob("*.md")):
            tier = build_distributions.parse_frontmatter(agent)[0].get("reasoning", "")
            roles.setdefault(tier, set()).add(agent.stem)
        for _agent, name, tier, _text in build_distributions.agent_variants(
                source, build_distributions.CANONICAL_REASONING_LEVELS):
            roles.setdefault(tier, set()).add(name)
    return {tier: sorted(names) for tier, names in roles.items()}


def model_report(
    model_id: str, entry: dict, tiers: list[str], roles: list[str],
    listed: dict[str, list[str] | None], adapter,
) -> dict:
    family, version = adapter.module.model_version(model_id)
    newer = []
    for candidate, efforts in listed.items():
        parsed = adapter.module.model_version(candidate)
        if parsed is not None and parsed[0] == family and parsed[1] > version:
            newer.append((parsed[1], candidate, efforts))
    newer.sort(key=lambda item: (item[0], item[1]), reverse=True)
    report = {
        "family": family,
        "pinned": model_id,
        "listed": model_id in listed,
        "newer": [{"id": candidate, "efforts": efforts}
                  for _version, candidate, efforts in newer],
        "tiers": tiers,
        "roles": roles,
    }
    listed_efforts = listed.get(model_id)
    if listed_efforts is not None and set(listed_efforts) != set(entry["efforts"]):
        report["efforts"] = {"pinned": entry["efforts"], "listed": listed_efforts}
    report["drift"] = bool(newer) or not report["listed"] or "efforts" in report
    return report


def bundled_source(adapter):
    """Return the pattern of a pinned source that names a bundled-catalog
    release, or None for a host whose catalog capture comes from no CLI."""
    return getattr(adapter.module, "BUNDLED_CATALOG_SOURCE_RE", None)


def capture_cli(host: str, catalog: dict, adapter, version: str | None) -> str | None:
    """Return the CLI release a bundled-catalog capture came from.

    It may not be older than the newest release whose bundled catalog a
    pinned source names.
    """
    pattern = bundled_source(adapter)
    if pattern is None:
        return None
    if version is None:
        raise ValueError(
            f"--cli-version {host}=<version> is required: the {host} catalog is the one"
            " bundled with its CLI, so the report records the CLI that printed it"
        )
    named = [
        match["version"] for entry in catalog["models"].values()
        for match in map(pattern.fullmatch, entry["sources"]) if match
    ]
    floor = max(named, key=build_distributions.cli_version, default=None)
    if floor is not None and \
            build_distributions.cli_version(version) < build_distributions.cli_version(floor):
        raise ValueError(
            f"the {host} catalog comes from CLI {version}, older than {floor}, the release"
            " the pinned sources name; capture it with that release or a newer one"
        )
    return version


def drift_report(
    root: Path, catalogs: dict[str, Path], cli_versions: dict[str, str] | None = None,
) -> dict:
    """Compare each named host's pinned models with its listed models."""
    adapters = build_distributions.load_adapters(root)
    cli_versions = cli_versions or {}
    unknown = sorted((set(catalogs) | set(cli_versions)) - set(adapters))
    if unknown:
        raise ValueError(
            f"unknown host {', '.join(unknown)}; hosts are {', '.join(adapters)}"
        )
    for host in sorted(cli_versions):
        if bundled_source(adapters[host]) is None:
            raise ValueError(f"--cli-version names {host}, whose catalog capture comes from no CLI")
    tier_roles = roles_by_tier(root)
    hosts = {}
    for host in sorted(catalogs):
        adapter = adapters[host]
        catalog, table = build_distributions.load_model_tables(root, adapter)
        cli = capture_cli(host, catalog, adapter, cli_versions.get(host))
        form, listed = listed_models(catalogs[host], adapter)
        auto = table["profiles"][build_distributions.AUTO_EXECUTION_PROFILE]
        models = {}
        for model_id, entry in catalog["models"].items():
            tiers = [tier for tier, setting in auto.items() if setting.get("model") == model_id]
            roles = sorted({role for tier in tiers for role in tier_roles.get(tier, [])})
            models[model_id] = model_report(model_id, entry, tiers, roles, listed, adapter)
        hosts[host] = {
            "catalog": {"path": str(catalogs[host]), "format": form, "models": len(listed),
                        "cli_version": cli},
            "models": models,
        }
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "drift": any(
            report["drift"] for host in hosts.values() for report in host["models"].values()
        ),
        "hosts": hosts,
        "unchecked": sorted(set(adapters) - set(catalogs)),
    }


def catalog_text(catalog: dict) -> str:
    return json.dumps(catalog, indent=2) + "\n"


def renames(models: dict) -> dict[str, str]:
    """Each drifting pin that moves to a newer model, with that model's ID."""
    return {model_id: report["newer"][0]["id"]
            for model_id, report in models.items() if report["drift"] and report["newer"]}


def bumped_catalog(catalog: dict, models: dict, today: str) -> dict:
    """Return the catalog with each drifting pin moved to its newest model.

    A moved entry takes the new ID as its key, in its own place.
    """
    bumped = {}
    for model_id, entry in catalog["models"].items():
        report = models.get(model_id, {})
        entry = dict(entry)
        if report.get("newer"):
            candidate = report["newer"][0]
            if candidate["efforts"] is not None:
                entry["efforts"] = candidate["efforts"]
            model_id = candidate["id"]
        elif "efforts" in report:
            entry["efforts"] = report["efforts"]["listed"]
        else:
            bumped[model_id] = entry
            continue
        entry["verified"] = today
        bumped[model_id] = entry
    return {**catalog, "models": bumped}


def unified(root: Path, path: Path, current: str, proposed: str, context: int) -> str:
    relative = path.relative_to(root).as_posix()
    return "".join(difflib.unified_diff(
        current.splitlines(keepends=True), proposed.splitlines(keepends=True),
        f"a/{relative}", f"b/{relative}", n=context,
    ))


def catalog_diff(root: Path, host: str, models: dict, today: str, context: int = 3) -> str:
    path = build_distributions.model_catalog_path(root, host)
    current = path.read_text(encoding="utf-8")
    proposed = catalog_text(bumped_catalog(json.loads(current), models, today))
    return unified(root, path, current, proposed, context)


def profile_diff(root: Path, host: str, models: dict, context: int = 3) -> str:
    """The execution profile change that points every tier on a moved pin at its
    new ID, in the table's own layout."""
    path = build_distributions.execution_profile_path(root, host)
    current = path.read_text(encoding="utf-8")
    proposed = current
    for old, new in renames(models).items():
        proposed = proposed.replace(f'"model": {json.dumps(old)}', f'"model": {json.dumps(new)}')
    return unified(root, path, current, proposed, context)


def listing(values: list[str]) -> str:
    return ", ".join(f"`{value}`" for value in values) or "none"


def finding_title(host: str, report: dict) -> str:
    if report["newer"]:
        return f"{host} {report['pinned']} to {report['newer'][0]['id']}"
    if not report["listed"]:
        return f"{host} {report['pinned']} not listed"
    return f"{host} {report['pinned']} efforts changed"


def finding_text(host: str, report: dict) -> str:
    pinned = f"`{report['pinned']}`"
    if report["newer"]:
        text = (f"{host}: the catalog pins {pinned}; the host catalog lists the newer"
                f" {listing([model['id'] for model in report['newer']])} in family"
                f" `{report['family']}`.")
    elif not report["listed"]:
        text = (f"{host}: the catalog pins {pinned}, which the host catalog no longer"
                f" lists, and no newer `{report['family']}` model is listed. The owner"
                " decides whether its tiers move to another model.")
    else:
        text = f"{host}: the catalog pins {pinned}."
    if "efforts" in report:
        text += (f" The host catalog lists the efforts {listing(report['efforts']['listed'])}"
                 f" for {pinned}; the pinned catalog records"
                 f" {listing(report['efforts']['pinned'])}.")
    return (f"- {text} Tiers on it: {listing(report['tiers'])}. Roles on them:"
            f" {listing(report['roles'])}.")


def effort_warnings(table: dict, report: dict) -> list[str]:
    """Name each tier whose effort the bumped model does not list."""
    if report["newer"]:
        model_id, efforts = report["newer"][0]["id"], report["newer"][0]["efforts"]
    elif "efforts" in report:
        model_id, efforts = report["pinned"], report["efforts"]["listed"]
    else:
        return []
    if efforts is None:
        return [f"- Confirm the efforts of `{model_id}` on its official page before merging."]
    auto = table["profiles"][build_distributions.AUTO_EXECUTION_PROFILE]
    return [
        f"- Tier `{tier}` runs effort `{auto[tier]['effort']}`, which `{model_id}` does"
        " not list: change that tier in `execution-profiles.json` with the bump."
        for tier in report["tiers"]
        if "effort" in auto[tier] and auto[tier]["effort"] not in efforts
    ]


def issue_body(root: Path, report: dict, today: str) -> str:
    """Render the upstream issue that proposes the catalog bump."""
    adapters = build_distributions.load_adapters(root)
    drifting = [
        (host, item)
        for host, data in report["hosts"].items()
        for item in data["models"].values() if item["drift"]
    ]
    title = "; ".join(finding_title(host, item) for host, item in drifting)
    lines = [f"# Model catalog drift: {title}", "", "## What changed", ""]
    lines += [finding_text(host, item) for host, item in drifting]
    lines += ["", "## Evidence", "",
              "| Host | Host catalog | Format | Models | CLI |",
              "| --- | --- | --- | --- | --- |"]
    for host, data in report["hosts"].items():
        catalog = data["catalog"]
        lines.append(f"| {host} | `{Path(catalog['path']).name}` | {catalog['format']}"
                     f" | {catalog['models']} | {catalog['cli_version'] or 'none'} |")
    if report["unchecked"]:
        lines += ["", f"Not checked in this run: {', '.join(report['unchecked'])}."]
    lines += ["", "## Catalog change", ""]
    changed = False
    for host in sorted({host for host, _item in drifting}):
        models = report["hosts"][host]["models"]
        diff = catalog_diff(root, host, models, today) + profile_diff(root, host, models)
        if diff:
            changed = True
            lines += ["```diff", diff.rstrip("\n"), "```", ""]
        _catalog, table = build_distributions.load_model_tables(root, adapters[host])
        for item in models.values():
            if item["drift"]:
                lines += effort_warnings(table, item)
    if lines[-1]:
        lines.append("")
    lines += [(
        "The pull request confirms each new ID, its efforts and its `min_cli_version`, the"
        " oldest host CLI that runs it, on the model's official pages, sets `sources` to"
        " them, points every tier on the old ID at the new one in"
        " `execution-profiles.json`, raises `tools/data/host-cli-versions.json` to any"
        " newer minimum,"
        " regenerates `dist/` with `python3 tools/build_distributions.py` and adds a"
        " `.changes/<name>.json` at `minor` for `software-engineering-team`, because"
        " default role models change."
    ) if changed else (
        "No drifting pin has a newer model to move to, so the owner's decision above"
        " comes before any pull request."
    ), "", "## Frozen-task A/B", ""]
    candidates = [(host, item) for host, item in drifting if item["newer"]]
    if not candidates:
        lines.append("No pin moves to another model, so no A/B is due.")
    else:
        lines += [
            "The pull request carries this comparison of the pinned and the candidate"
            " model for every tier on a moving pin. It follows the Measurement section"
            f" of `{AB_REFERENCE}` with the two models as the only candidates:",
            "",
            "1. Freeze at least one task per tier from Git history. For a writer tier"
            " the task is a fix pass: the commit before it, the findings it applied and"
            " the accepted fix commit. For a reviewer tier it is a review: the commit it"
            " read and the accepted review's valid critical and major findings.",
            "2. Run each task once per model on an unchanged checkout, each tier at its"
            " table effort:",
        ]
        lines += [
            "   - " + getattr(adapters[host].module, "MODEL_TRIAL",
                              f"{host}: run every role on the candidate as its host contract"
                              " describes.")
            for host in sorted({host for host, _item in candidates})
        ]
        lines += [
            "3. A fresh, read-only judge on the strongest tier compares each result with"
            " the reference: for a writer, every finding applied, no unrelated edit and"
            " compilers green; for a reviewer, the valid critical and major findings"
            " found and none invalid. Record wall time and output tokens per run.",
            "4. Record the results:",
            "",
            "| Host | Tier | Task | Model | Quality | Wall time | Output tokens |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for host, item in candidates:
            for tier in item["tiers"]:
                lines += [f"| {host} | {tier} | | `{model}` | | | |"
                          for model in (item["pinned"], item["newer"][0]["id"])]
        lines += [
            "",
            "Quality decides: the candidate replaces the pin only when it matches or"
            " beats the pinned model on every task. Speed is reported beside it, and a"
            " slower candidate needs the owner's explicit acceptance.",
        ]
    lines += [
        "",
        "## Approval and release",
        "",
        "1. The pull request links this issue and carries the catalog change, the"
        " regenerated `dist/`, the changeset and the A/B results.",
        "2. The owner approves the merge; the release then follows Flow B of"
        " `docs/maintainer-operations-protocol.md`.",
        "3. Projects receive the new model with their next package refresh. A project"
        " that cannot use it follows the main conversation instead: Codex with"
        " `generate_codex_project.py apply --execution-profile inherit`, Claude Code"
        " with `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`.",
    ]
    return "\n".join(lines) + "\n"


def parse_catalog_arguments(values: list[str]) -> dict[str, Path]:
    catalogs: dict[str, Path] = {}
    for value in values:
        host, separator, path = value.partition("=")
        if not separator or not host or not path:
            raise ValueError(f"--catalog takes HOST=PATH, not {value!r}")
        if host in catalogs:
            raise ValueError(f"--catalog names {host} twice")
        catalogs[host] = Path(path)
    return catalogs


def parse_cli_arguments(values: list[str]) -> dict[str, str]:
    """Read HOST=VERSION pairs; VERSION may be the CLI's `--version` output."""
    versions: dict[str, str] = {}
    for value in values:
        host, separator, text = value.partition("=")
        found = CLI_VERSION_RE.findall(text)
        if not separator or not host or len(found) != 1:
            raise ValueError(
                f"--cli-version takes HOST=VERSION with exactly one X.Y.Z, not {value!r}"
            )
        if host in versions:
            raise ValueError(f"--cli-version names {host} twice")
        versions[host] = found[0]
    return versions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n", 1)[0],
        epilog="Exit status: 0 without drift, 1 with drift, 2 for invalid input, 3 when"
               " a registered host went unchecked without --subset.",
    )
    parser.add_argument(
        "--catalog", action="append", required=True, metavar="HOST=PATH",
        help="the host's own model catalog file; repeat once per host",
    )
    parser.add_argument(
        "--cli-version", action="append", default=[], metavar="HOST=VERSION",
        help="the host CLI that printed a bundled catalog, such as"
             ' "codex=$(codex --version)"; required for Codex',
    )
    parser.add_argument(
        "--issue-body", action="store_true",
        help="render the upstream issue with the bump diff and the frozen-task A/B",
    )
    parser.add_argument(
        "--subset", action="store_true",
        help="check only the hosts --catalog names; without it an omitted host fails the run",
    )
    parser.add_argument(
        "--date", default=None, metavar="YYYY-MM-DD",
        help="the verified date a bump records; defaults to today from the system clock",
    )
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        today = args.date or datetime.date.today().isoformat()
        datetime.date.fromisoformat(today)
        report = drift_report(root, parse_catalog_arguments(args.catalog),
                              parse_cli_arguments(args.cli_version))
        unchecked = report["unchecked"]
        if unchecked:
            print(f"model-drift: not checked: {', '.join(unchecked)}" + (
                "" if args.subset else
                f"; pass {'its' if len(unchecked) == 1 else 'their'} catalog, or --subset"
                " to check only the named hosts"), file=sys.stderr)
        if not args.issue_body:
            output = json.dumps(report, indent=2) + "\n"
        elif report["drift"]:
            output = issue_body(root, report, today)
        else:
            output = ""
            checked = f" of {', '.join(report['hosts'])}" if unchecked else ""
            print(f"model-drift: every pinned model{checked} matches its host catalog",
                  file=sys.stderr)
    except ValueError as exc:
        print(f"model-drift: {exc}", file=sys.stderr)
        return EXIT_INVALID
    sys.stdout.write(output)
    if report["drift"]:
        return EXIT_DRIFT
    return EXIT_UNCHECKED if unchecked and not args.subset else EXIT_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
