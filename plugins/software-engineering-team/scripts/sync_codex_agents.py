#!/usr/bin/env python3
"""Sync the generated Codex agent definitions into the personal agents
directory.

Codex plugins carry no agents component, and the project-level agents
directory is a protected read-only path inside the workspace sandbox, so
the generated TOML tree ships inside this plugin and lands in the
personal scope (the CODEX_HOME agents directory, default ~/.codex/agents)
through this script, driven by the setup entry. Codex loads agent
definitions at session start; a new session after syncing makes them
resolvable by name.

Idempotent: unchanged files are skipped; agents removed from the plugin
are removed from the personal scope when they carry this plugin's name
prefix. Stdlib only.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PLUGIN_ROOT / "codex" / "agents"
PREFIX = PLUGIN_ROOT.name + "-"


def dest_dir() -> Path:
    override = os.environ.get("CODEX_HOME", "").strip()
    base = Path(override) if override else Path.home() / ".codex"
    return base / "agents"


def main() -> int:
    if not SOURCE_DIR.is_dir():
        print(
            "sync_codex_agents: no generated codex/agents tree in this"
            " install; this plugin build predates Codex support or the"
            " install is incomplete.",
            file=sys.stderr,
        )
        return 1
    target = dest_dir()
    target.mkdir(parents=True, exist_ok=True)
    synced = removed = 0
    shipped = {p.name: p for p in sorted(SOURCE_DIR.glob("*.toml"))}
    for name, source in shipped.items():
        dst = target / name
        content = source.read_text(encoding="utf-8")
        if dst.is_file() and dst.read_text(encoding="utf-8") == content:
            continue
        dst.write_text(content, encoding="utf-8")
        synced += 1
    for stale in sorted(target.glob(f"{PREFIX}*.toml")):
        if stale.name not in shipped:
            stale.unlink()
            removed += 1
    print(
        f"sync_codex_agents: {synced} synced, {removed} removed,"
        f" {len(shipped)} total in {target}. Agent definitions load at"
        " session start: start a new session before dispatching them."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
