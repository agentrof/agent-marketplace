#!/usr/bin/env python3
"""Shared core of each host's `host_models.py`: one verdict on both hosts.

A listing is the dict `host_models.list_models()` returns:

    {"status": "ok" | "no_binary" | "failed",
     "binary": path of the host binary or None,
     "version": its version or None,
     "view": "account" | "generic",
     "models": {model id: {"efforts": [effort, ...]}},
     "detail": one line that says where the list came from or why there is none}

`view` is `account` only when the list reflects the signed-in account;
`models` is empty unless `status` is `ok`. A model of a host whose list also
holds models its own picker hides carries that host's `visibility`, `list`
for a model the picker shows, as Codex's catalog does; Claude Code's reply
names none. A verdict judges every model of the list; only the configure
topic's choices leave the hidden ones out.

- verdict(model_id, listing) -> "available" | "unavailable" | "unverified":
  a model the list holds is `available` in the account view and `unverified`
  in the generic one; a model it does not hold is `unavailable` in either,
  since this binary cannot run it; without a list every model is `unverified`.
- efforts(model_id, listing) -> the listed effort levels, or None.

`run()` gives every host command a share of one time limit and stops the
command, and every process it started, when the limit passes.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

DEFAULT_TIMEOUT = 30.0
PROC = Path("/proc")
# A walk never climbs further than this, so a corrupt table cannot loop.
MAX_ANCESTORS = 64


class CommandFailed(Exception):
    """A host command that did not run, ran too long or printed no list."""


class Deadline:
    """One time limit shared by every command of one listing."""

    def __init__(self, timeout: float) -> None:
        self.timeout = float(timeout)
        self.end = time.monotonic() + self.timeout

    def remaining(self) -> float:
        return self.end - time.monotonic()

    def expired(self) -> CommandFailed:
        return CommandFailed(f"it timed out after {self.timeout:g} s")


def listing(status: str, *, binary: Optional[str] = None, version: Optional[str] = None,
            view: str = "generic", models: Optional[dict] = None, detail: str = "") -> dict:
    return {"status": status, "binary": binary, "version": version, "view": view,
            "models": dict(models or {}) if status == "ok" else {}, "detail": detail}


def listed(listing: object) -> Optional[dict]:
    """Return the models of a usable listing, or None."""
    if isinstance(listing, dict) and listing.get("status") == "ok" \
            and isinstance(listing.get("models"), dict):
        return listing["models"]
    return None


def verdict(model_id: str, listing: object) -> str:
    """Judge one model against a listing."""
    models = listed(listing)
    if models is None:
        return "unverified"
    if model_id not in models:
        return "unavailable"
    return "available" if listing.get("view") == "account" else "unverified"


def efforts(model_id: str, listing: object) -> Optional[list]:
    """Return the effort levels a listing names for one model, or None."""
    models = listed(listing)
    entry = models.get(model_id) if models is not None else None
    levels = entry.get("efforts") if isinstance(entry, dict) else None
    return list(levels) if isinstance(levels, list) else None


def pinned(settings: Mapping[str, dict], session: str, order: Iterable[str] = ()) -> dict:
    """Each model the resolved roles run, the session's excluded, with its tiers
    in ``order`` and its roles."""
    rank = {tier: index for index, tier in enumerate(order)}
    models: dict = {}
    for role, row in sorted(settings.items()):
        if row["model"] == session:
            continue
        entry = models.setdefault(row["model"], {"tiers": [], "roles": []})
        if row["tier"] not in entry["tiers"]:
            entry["tiers"].append(row["tier"])
        entry["roles"].append(role)
    for entry in models.values():
        entry["tiers"].sort(key=lambda tier: (rank.get(tier, len(rank)), tier))
    return models


def model_check(listing: Optional[dict], pins: Mapping[str, dict]) -> dict:
    """What setup reports of the host's list: where it came from and each verdict."""
    if listing is None:
        return {"status": "not_run"}
    report = {key: listing.get(key) for key in ("status", "binary", "version", "view", "detail")}
    report["verdicts"] = {model: verdict(model, listing) for model in sorted(pins)}
    return report


def unavailable(check: dict) -> set:
    """The models a check judged `unavailable`."""
    return {model for model, value in check.get("verdicts", {}).items()
            if value == "unavailable"}


def effort_conflicts(listing: object, tiers: Mapping[str, dict], catalog) -> list:
    """Each tier that runs a model outside the catalog at an effort the host's
    list does not name for it; only a listed model has efforts to compare."""
    conflicts = []
    for tier, setting in tiers.items():
        model, effort = setting.get("model"), setting.get("effort")
        if not isinstance(model, str) or model in catalog or effort is None:
            continue
        levels = efforts(model, listing)
        if levels is not None and effort not in levels:
            conflicts.append({"tier": tier, "model": model, "effort": effort, "efforts": levels})
    return conflicts


def tier_words(tiers: list) -> str:
    named = tiers[0] if len(tiers) == 1 else ", ".join(tiers[:-1]) + " and " + tiers[-1]
    return f"the {named} tier" + ("s" if len(tiers) > 1 else "")


def check_notes(prefix: str, check: dict, pins: Mapping[str, dict], fallen, conflicts: list,
                session: str) -> list:
    """The warnings and notes a project generator prints for its model check.

    ``fallen`` holds the models whose roles run on ``session``, the session's
    model, because a check judged them unavailable, now or at the last setup.
    """
    notes = []
    for model in sorted(fallen):
        entry = pins[model]
        why = (f"this host's own model list ({check.get('view')} view,"
               f" {check.get('binary')} {check.get('version')}) does not hold it"
               if check.get("status") == "ok" else "the last setup judged it unavailable")
        whose = "Its" if len(entry["tiers"]) == 1 else "Their"
        notes.append(f"{prefix}: warning: {model} is unavailable for {tier_words(entry['tiers'])}:"
                     f" {why}. {whose} roles {', '.join(entry['roles'])} run on {session} at their"
                     " own effort; every setup or refresh judges it again.")
    kept = sorted(model for model, value in check.get("verdicts", {}).items()
                  if value == "unverified")
    if kept:
        one = len(kept) == 1
        notes.append(f"{prefix}: note: {', '.join(kept)} keep{'s its pin' if one else ' their pins'}"
                     f" unverified: {check.get('detail')}; the run-time rule covers"
                     f" {'it' if one else 'them'}.")
    for conflict in conflicts:
        levels = ", ".join(conflict["efforts"]) or "it takes no effort"
        notes.append(f"{prefix}: warning: the {conflict['tier']} tier runs {conflict['model']} at"
                     f" effort {conflict['effort']}, which this host's own model list does not"
                     f" name for it ({levels}); choose another effort through /configure models.")
    return notes


def stop(process: subprocess.Popen) -> None:
    """Kill a command and every process it started, then reap it."""
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except OSError:
        pass
    try:
        process.communicate(timeout=5)
    except (subprocess.TimeoutExpired, OSError, ValueError):
        # A process outside the group still holds the output open.
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def run(argv: list, deadline: Deadline, *, environ: Mapping[str, str],
        input_text: Optional[str] = None, cwd: Optional[str] = None) -> tuple:
    """Run one host command within the deadline; return its exit status and stdout."""
    remaining = deadline.remaining()
    if remaining <= 0:
        raise deadline.expired()
    try:
        # A new session puts every process the command starts in one group,
        # so a timeout stops a child that holds the output open as well.
        process = subprocess.Popen(
            [str(part) for part in argv], cwd=cwd, env=dict(environ),
            stdin=subprocess.DEVNULL if input_text is None else subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=os.name == "posix")
    except (OSError, ValueError) as exc:
        reason = getattr(exc, "strerror", None) or exc
        raise CommandFailed(f"it did not start: {reason}") from None
    try:
        out, _ = process.communicate(
            None if input_text is None else input_text.encode("utf-8"), timeout=remaining)
    except subprocess.TimeoutExpired:
        stop(process)
        raise deadline.expired() from None
    return process.returncode, out.decode("utf-8", errors="replace")


def version_line(argv: list, deadline: Deadline, environ: Mapping[str, str]) -> Optional[str]:
    """Return the first line a binary prints for --version, or None.

    A binary that does not run is no candidate; a passed deadline ends the
    whole listing.
    """
    try:
        code, out = run([*argv, "--version"], deadline, environ=environ)
    except CommandFailed:
        if deadline.remaining() <= 0:
            raise
        return None
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    return lines[0] if code == 0 and lines else None


def release(version: Optional[str]) -> Optional[tuple]:
    """Return the X.Y.Z release of a version as a comparable tuple."""
    parts = (version or "").split("-", 1)[0].split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def which(name: str, environ: Mapping[str, str]) -> Optional[str]:
    """Find an executable on the PATH of `environ`, never on this process's."""
    path = environ.get("PATH")
    return shutil.which(name, path=path) if path else None


def parse_ps_table(text: str) -> dict:
    """Map each pid of `ps -o pid=,ppid=,comm=` output to its parent and executable."""
    table = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            table[int(parts[0])] = (int(parts[1]), parts[2])
    return table


def proc_entry(pid: int, root: Path = PROC) -> Optional[tuple]:
    """Return the parent and executable of a Linux process, or None."""
    try:
        stat = (root / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
        parent = int(stat.rsplit(")", 1)[1].split()[1])
    except (OSError, IndexError, ValueError):
        return None
    try:
        executable = os.readlink(root / str(pid) / "exe")
    except OSError:
        executable = ""
    return parent, executable


def walk(pid: int, lookup: Callable[[int], Optional[tuple]]) -> list:
    """Return the executables from `pid` up to, not including, pid 1."""
    executables, seen = [], set()
    while pid > 1 and pid not in seen and len(executables) < MAX_ANCESTORS:
        seen.add(pid)
        entry = lookup(pid)
        if entry is None:
            break
        pid, executable = entry
        executables.append(executable)
    return executables


def ps(deadline: Deadline, *options: str) -> str:
    command = shutil.which("ps", path=os.defpath)
    if command is None:
        raise CommandFailed("no ps")
    code, out = run([command, *options], deadline,
                    environ={"PATH": os.defpath, "LC_ALL": "C"})
    if code != 0:
        raise CommandFailed(f"ps exited with status {code}")
    return out


def ancestor_executables(deadline: Deadline) -> list:
    """Return the executables of this process's ancestors, nearest first.

    Linux reads /proc; macOS and the BSDs read one `ps` table. Elsewhere, and
    whenever the table cannot be read, the list is empty.
    """
    try:
        if (PROC / "self" / "stat").is_file():
            return walk(os.getppid(), proc_entry)
        if os.name == "posix":
            return walk(os.getppid(), parse_ps_table(
                ps(deadline, "-A", "-o", "pid=,ppid=,comm=")).get)
    except CommandFailed:
        pass
    return []


def process_executable(pid: int, deadline: Deadline) -> Optional[str]:
    """Return the executable path of one process, or None."""
    if (PROC / "self" / "stat").is_file():
        entry = proc_entry(pid)
        return entry[1] if entry and entry[1] else None
    if os.name != "posix":
        return None
    try:
        path = ps(deadline, "-o", "comm=", "-p", str(pid)).strip()
    except CommandFailed:
        return None
    return path if os.path.isabs(path) else None


def report(listing: dict, models: list) -> str:
    """The JSON a host_models.py command prints for a listing and the models it judges."""
    return json.dumps({"listing": listing,
                       "verdicts": {model: verdict(model, listing) for model in models},
                       "efforts": {model: efforts(model, listing) for model in models}},
                      sort_keys=True)
