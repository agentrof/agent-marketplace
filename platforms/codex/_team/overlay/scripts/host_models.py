#!/usr/bin/env python3
"""Read Codex's own model list from the binary that runs this session.

Interface, the same on both hosts; `host_listing.py` defines the listing:

- list_models(environ=os.environ, timeout=30.0) -> listing
- verdict(model_id, listing) -> "available" | "unavailable" | "unverified"
- efforts(model_id, listing) -> the listed effort levels, or None

Binary: Codex exports no variable that names its executable, so the first of
these that prints a Codex version is used, and while `CODEX_VERSION` is set
only one of that version:
1. the nearest ancestor process whose executable is named `codex`;
2. the target of the `apply_patch` alias on PATH, a link Codex makes to its
   own executable;
3. `CODEX_CLI_PATH` when the environment carries it; no Codex config file is
   read, since one can hold MCP server secrets;
4. `codex` on PATH;
5. a Codex CLI that an app in the applications folders bundles, as the
   ChatGPT app does.

List: `models_cache.json` in the Codex home (`CODEX_HOME`, else `~/.codex`),
the account's catalog read with no network, when its `client_version` is the
binary's version and it is fresh: fetched at most 24 hours ago, and at most
5 minutes ahead of this clock. Codex rewrites the file each time it fetches
the account's catalog, which it does once the file is 5 minutes old, so a
session started within the day leaves it fresh; a model change within the
day is left to the run-time rule. Only the cache's model ids, effort levels
and visibility are read. Otherwise the list is the output of `codex debug
models`. Each model keeps its `visibility`: Codex's picker shows only `list`
models, and keeps `hide` and `none` ones, such as its review model, out.

View: `account` for the cache and for a `codex debug models` output that
differs from `codex debug models --bundled`; `generic` when that command can
only have printed the catalog bundled with the binary, which it does
silently when it cannot fetch the account's: inside the command sandbox (a
`CODEX_SANDBOX` variable set), which blocks the fetch, for an output equal
to the bundled catalog, and when that comparison cannot run.

Every failure, a timeout included, gives a listing that judges every model
`unverified`; list_models never raises.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Mapping, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import host_listing
from host_listing import CommandFailed, efforts, verdict  # noqa: F401

HOST = "codex"
NAMES = ("codex", "codex.exe")
APP_FOLDERS = ("/Applications", "~/Applications")
BUNDLED_CLI = ("Contents", "Resources", "codex-cli", "bin", "codex")
VERSION_RE = re.compile(r"codex(?:-cli)? (\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)")
CACHE = "models_cache.json"
CACHE_MAX_AGE = timedelta(hours=24)
CLOCK_SKEW = timedelta(minutes=5)
LISTING = ("debug", "models")
BUNDLED = ("debug", "models", "--bundled")
TIME_RE = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})")
NOT_A_CATALOG = "`codex debug models` printed what is not a model catalog"


def candidates(environ: Mapping[str, str],
               deadline: host_listing.Deadline) -> Iterator[tuple]:
    """Each place a Codex binary may be, with its name, in order."""
    for executable in host_listing.ancestor_executables(deadline):
        if Path(executable).name in NAMES:
            yield "the nearest codex ancestor process", executable
            break
    alias = host_listing.which("apply_patch", environ)
    # The alias name selects apply_patch inside Codex, so its target runs.
    target = os.path.realpath(alias) if alias else None
    if target and Path(target).name in NAMES:
        yield "the apply_patch alias", target
    if environ.get("CODEX_CLI_PATH"):
        yield "CODEX_CLI_PATH", environ["CODEX_CLI_PATH"]
    on_path = host_listing.which("codex", environ)
    if on_path:
        yield "PATH", on_path
    for bundled in bundled_clis():
        yield "an app's bundled CLI", bundled


def bundled_clis() -> list:
    """The Codex CLIs that apps in the applications folders bundle."""
    found = []
    for folder in APP_FOLDERS:
        try:
            apps = sorted(Path(os.path.expanduser(folder)).glob("*.app"))
        except OSError:
            continue
        found.extend(str(app.joinpath(*BUNDLED_CLI)) for app in apps
                     if app.joinpath(*BUNDLED_CLI).is_file())
    return found


def locate(environ: Mapping[str, str], deadline: host_listing.Deadline) -> tuple:
    """Return the binary and its version, or None, None and why there is none."""
    expected = environ.get("CODEX_VERSION") or None
    skipped, tried = [], set()
    for source, binary in candidates(environ, deadline):
        if binary in tried:
            continue
        tried.add(binary)
        match = VERSION_RE.fullmatch(host_listing.version_line([binary], deadline, environ) or "")
        if match is None:
            skipped.append(f"{binary} ({source}) prints no Codex version")
        elif expected and match.group(1) != expected:
            skipped.append(f"{binary} ({source}) is {match.group(1)}, not CODEX_VERSION"
                           f" {expected}")
        else:
            return binary, match.group(1), ""
    return None, None, "no codex binary: " + ("; ".join(skipped) or (
        "no codex ancestor process, apply_patch alias, CODEX_CLI_PATH, codex on PATH or"
        " ChatGPT app"))


def catalog(entries: object) -> Optional[dict]:
    """Map each slug of a Codex model catalog to its effort levels and its
    visibility, or None."""
    if not isinstance(entries, list):
        return None
    models: dict = {}
    for entry in entries:
        slug = entry.get("slug") if isinstance(entry, dict) else None
        levels = (entry.get("supported_reasoning_levels") or []) if slug else None
        if not isinstance(slug, str) or not isinstance(levels, list):
            return None
        model = models.setdefault(slug, {"efforts": []})
        known = model["efforts"]
        for level in levels:
            effort = level.get("effort") if isinstance(level, dict) else None
            if not isinstance(effort, str):
                return None
            if effort not in known:
                known.append(effort)
        visibility = entry.get("visibility")
        # A slug the catalog lists twice is shown when either entry is.
        if isinstance(visibility, str) and model.get("visibility") != "list":
            model["visibility"] = visibility
    return models


def fetch_time(value: object) -> Optional[datetime]:
    """Parse the RFC 3339 time Codex records, any fraction of a second included."""
    match = TIME_RE.fullmatch(value) if isinstance(value, str) else None
    if match is None:
        return None
    *fields, fraction, zone = match.groups()
    offset = timedelta(0) if zone == "Z" else (-1 if zone[0] == "-" else 1) * timedelta(
        hours=int(zone[1:3]), minutes=int(zone[4:6]))
    try:
        return datetime(*(int(field) for field in fields),
                        int((fraction or "0")[:6].ljust(6, "0")), tzinfo=timezone(offset))
    except ValueError:
        return None


def cached(environ: Mapping[str, str], version: str) -> tuple:
    """Return the cached account catalog of this version, or None and why not."""
    home = environ.get("CODEX_HOME")
    path = (Path(home) if home else Path.home() / ".codex") / CACHE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, f"no readable {CACHE}"
    if not isinstance(data, dict):
        return None, f"{CACHE} holds no model catalog"
    if data.get("client_version") != version:
        return None, f"{CACHE} is from Codex {data.get('client_version')}, not {version}"
    fetched = fetch_time(data.get("fetched_at"))
    if fetched is None:
        return None, f"{CACHE} has no fetch time"
    age = datetime.now(timezone.utc) - fetched
    if age > CACHE_MAX_AGE:
        return None, f"{CACHE} was fetched more than 24 hours ago"
    if age < -CLOCK_SKEW:
        return None, f"{CACHE} was fetched in the future"
    models = catalog(data.get("models"))
    if not models:
        return None, f"{CACHE} holds no model catalog"
    stamp = fetched.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return models, f"{CACHE} holds the account catalog Codex {version} fetched at {stamp}"


def printed(binary: str, environ: Mapping[str, str],
            deadline: host_listing.Deadline) -> tuple:
    """Return the view, models and source of the `codex debug models` catalog."""
    try:
        code, out = host_listing.run([binary, *LISTING], deadline, environ=environ)
    except CommandFailed as exc:
        raise CommandFailed(f"`codex debug models` failed: {exc}") from None
    if code != 0:
        raise CommandFailed(f"`codex debug models` exited with status {code}")
    try:
        data = json.loads(out)
    except ValueError:
        raise CommandFailed(NOT_A_CATALOG) from None
    models = catalog(data.get("models") if isinstance(data, dict) else None)
    if models is None:
        raise CommandFailed(NOT_A_CATALOG)
    if not models:
        raise CommandFailed("`codex debug models` lists no model")
    sandbox = sorted(name for name, value in environ.items()
                     if name.startswith("CODEX_SANDBOX") and value)
    if sandbox:
        return "generic", models, (
            f"`codex debug models` ran inside the command sandbox ({', '.join(sandbox)}),"
            " which blocks the account catalog's fetch, so it printed the bundled catalog")
    try:
        code, bundled = host_listing.run([binary, *BUNDLED], deadline, environ=environ)
        unknown = f"exited with status {code}" if code != 0 else ""
    except CommandFailed as exc:
        bundled, unknown = None, f"failed: {exc}"
    if unknown:
        return "generic", models, (
            f"`codex debug models --bundled` {unknown}, so whether `codex debug models`"
            " printed the account catalog is unknown")
    if bundled == out:
        return "generic", models, (
            "`codex debug models` printed the catalog bundled with the binary, so it"
            " reflects no account")
    return "account", models, "`codex debug models` printed the account catalog"


def list_models(environ: Mapping[str, str] = os.environ,
                timeout: float = host_listing.DEFAULT_TIMEOUT) -> dict:
    """Read the model list of the Codex binary that runs this session."""
    deadline = host_listing.Deadline(timeout)
    binary = version = None
    try:
        binary, version, why = locate(environ, deadline)
        if binary is None:
            return host_listing.listing("no_binary", detail=why)
        models, note = cached(environ, version)
        if models:
            return host_listing.listing("ok", binary=binary, version=version, view="account",
                                        models=models, detail=note)
        try:
            view, models, source = printed(binary, environ, deadline)
        except CommandFailed as exc:
            raise CommandFailed(f"{note}; {exc}") from None
        return host_listing.listing("ok", binary=binary, version=version, view=view,
                                    models=models, detail=f"{note}; {source}")
    except CommandFailed as exc:
        return host_listing.listing("failed", binary=binary, version=version, detail=str(exc))
    except Exception as exc:  # never an error that stops setup
        return host_listing.listing("failed", binary=binary, version=version,
                                    detail=f"the model listing failed: {exc!r}")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print Codex's own model list and judge each MODEL against it.")
    parser.add_argument("models", nargs="*", metavar="MODEL")
    parser.add_argument("--timeout", type=float, default=host_listing.DEFAULT_TIMEOUT)
    args = parser.parse_args(argv)
    print(host_listing.report(list_models(timeout=args.timeout), args.models))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
