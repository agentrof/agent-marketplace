#!/usr/bin/env python3
"""Plugin-root dispatcher: the fixed way shipped content reaches plugin
files.

Skills and flows never know where a supported host installs plugins; they call
this dispatcher (synced into the data directory's bin/ next to the PMO
CLI) and it resolves through the plugin_roots registry, which hooks and
the setup entry maintain.

Verbs:
  run <plugin> <relpath> [args...]   execute a plugin script
  path <plugin> <relpath>            print the absolute path
  register --plugin X --root PATH

A missing or stale root errors with the re-run-setup instruction instead
of running the wrong copy. Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PLUGIN_ROOTS_NAME = "plugin_roots.json"


def data_dir() -> Path:
    override = os.environ.get("AGENTROF_HOME", "").strip()
    return Path(override) if override else Path.home() / ".agentrof"


def registry_path() -> Path:
    return data_dir() / PLUGIN_ROOTS_NAME


def load_registry() -> dict:
    try:
        data = json.loads(registry_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_registry(registry: dict) -> None:
    registry_path().parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(registry_path().parent),
                               prefix=".plugin_roots.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(registry, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, registry_path())


def fail(message: str) -> int:
    print(f"agentrof: {message}", file=sys.stderr)
    return 1


def manifest_version(root: Path) -> str:
    for manifest in (
        root / ".codex-plugin" / "plugin.json",
        root / ".claude-plugin" / "plugin.json",
    ):
        try:
            if manifest.is_file():
                return json.loads(
                    manifest.read_text(encoding="utf-8")
                ).get("version", "")
        except Exception:
            continue
    return ""


def resolve_root(plugin: str) -> Path | None:
    """The registered install root, staleness-checked. A moved or removed
    root (plugin updates relocate the install) is an error, never a
    silent run of the wrong copy; a version drift against the on-disk
    manifest refreshes the registry entry."""
    registry = load_registry()
    entry = (registry.get("plugins") or {}).get(plugin)
    if not entry:
        return None
    root = Path(entry.get("root", ""))
    if not root.is_dir():
        return None
    on_disk = manifest_version(root)
    if on_disk and on_disk != entry.get("version", ""):
        entry["version"] = on_disk
        entry["registered_at"] = datetime.now(timezone.utc).isoformat(
            timespec="seconds")
        registry["plugins"][plugin] = entry
        try:
            save_registry(registry)
        except Exception:
            pass
    return root


STALE_HINT = (
    "no usable install root registered for plugin '{plugin}'. The root is"
    " registered by the plugin's session-start hook, or by the team's"
    " setup entry when hooks are not active. Start a new session, or"
    " re-run the setup entry, then retry."
)


def cmd_run(args) -> int:
    root = resolve_root(args.plugin)
    if root is None:
        return fail(STALE_HINT.format(plugin=args.plugin))
    target = root / args.relpath
    if not target.is_file():
        return fail(
            f"'{args.relpath}' does not exist under the registered root"
            f" {root}; the install may be mid-update. Re-run the setup"
            " entry, then retry."
        )
    completed = subprocess.run(
        [sys.executable, str(target), *args.args], check=False)
    return completed.returncode


def cmd_path(args) -> int:
    root = resolve_root(args.plugin)
    if root is None:
        return fail(STALE_HINT.format(plugin=args.plugin))
    target = root / args.relpath
    if not target.exists():
        return fail(
            f"'{args.relpath}' does not exist under the registered root"
            f" {root}; the install may be mid-update. Re-run the setup"
            " entry, then retry."
        )
    print(target)
    return 0


def cmd_register(args) -> int:
    root = Path(args.root).resolve()
    if not root.is_dir():
        return fail(f"root is not a directory: {root}")
    registry = load_registry()
    registry.setdefault("schema_version", 1)
    plugins = registry.setdefault("plugins", {})
    plugins[args.plugin] = {
        "root": str(root),
        "version": manifest_version(root),
        "registered_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
    }
    save_registry(registry)
    print(f"agentrof: registered {args.plugin} at {root}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run")
    p.add_argument("plugin")
    p.add_argument("relpath")
    p.add_argument("args", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("path")
    p.add_argument("plugin")
    p.add_argument("relpath")
    p.set_defaults(func=cmd_path)

    p = sub.add_parser("register")
    p.add_argument("--plugin", required=True)
    p.add_argument("--root", required=True)
    p.set_defaults(func=cmd_register)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
