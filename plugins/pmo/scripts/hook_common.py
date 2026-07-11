"""Shared plumbing for the PMO hook scripts.

Hooks must never break a session: every entry point reads stdin defensively,
logs failures to hooks.log in the data directory, and exits 0 unless the
hook's whole purpose is to block (the database write guard).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pmo_cli

# Team plugins whose subagents are recorded as tasks. Maps the agent-name
# prefix to the team plugin name; future team plugins add a line here.
TEAM_AGENT_PREFIXES = {
    "software-team-": "software-team",
}


def read_payload() -> dict:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def log(message: str) -> None:
    try:
        target = pmo_cli.data_dir() / "hooks.log"
        target.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with target.open("a", encoding="utf-8") as fh:
            fh.write(f"{stamp} {message}\n")
    except Exception:
        pass  # logging must never take a session down


def run_cli(argv: list[str]) -> int:
    """Invoke the CLI in-process, discarding stdout; failures are logged."""
    import contextlib
    import io
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = pmo_cli.main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        except Exception as exc:  # a hook must not crash on CLI errors
            log(f"cli-error {argv[0] if argv else '?'}: {exc}")
            return 1
    if code != 0:
        log(f"cli-nonzero {' '.join(argv[:3])}: {err.getvalue().strip()}")
    return code


def resolve_project(cwd: str) -> tuple[str, str] | None:
    """Walk up from cwd to a directory holding workspace/config.json with a
    project_key. Returns (project_key, project_root) or None."""
    try:
        current = Path(cwd).resolve()
    except Exception:
        return None
    for candidate in [current, *current.parents][:6]:
        config = candidate / "workspace" / "config.json"
        if config.is_file():
            try:
                data = json.loads(config.read_text(encoding="utf-8"))
            except Exception:
                return None
            key = data.get("project_key", "")
            if key:
                return key, str(candidate)
            return None
    return None


def team_role(agent_type: str) -> str | None:
    """Map a spawned agent's type to a snake_case role, or None when the
    agent does not belong to a registered team plugin.

    Plugin-shipped agents arrive namespaced as '<plugin>:<agent-name>'
    (measured empirically); direct spawns may carry the bare agent name."""
    bare = agent_type.split(":", 1)[-1]
    for prefix in TEAM_AGENT_PREFIXES:
        if bare.startswith(prefix):
            return bare[len(prefix):].replace("-", "_")
    return None
