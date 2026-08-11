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
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import marketplace_paths

PLUGIN_ROOTS_NAME = "plugin_roots.json"
PLUGIN_ROOTS_LOCK = ".plugin_roots.lock"


def data_dir() -> Path:
    return marketplace_paths.marketplace_home()


def registry_path() -> Path:
    return data_dir() / PLUGIN_ROOTS_NAME


def load_registry() -> dict:
    try:
        data = json.loads(registry_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_registry_unlocked(registry: dict) -> None:
    registry_path().parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(registry_path().parent),
                               prefix=".plugin_roots.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(registry, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, registry_path())


class RegistryLock:
    def __init__(self, timeout: float = 5.0):
        self.path = data_dir() / PLUGIN_ROOTS_LOCK
        self.timeout = timeout

    def __enter__(self):
        data_dir().mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                self.path.mkdir()
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 30:
                        self.path.rmdir()
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting for plugin registry lock")
                time.sleep(0.01)

    def __exit__(self, exc_type, exc, tb):
        try:
            self.path.rmdir()
        except OSError:
            pass
        return False


def update_registry(mutator) -> dict:
    with RegistryLock():
        registry = load_registry()
        if not isinstance(registry, dict):
            registry = {}
        mutator(registry)
        save_registry_unlocked(registry)
        return registry


def fail(message: str) -> int:
    print(f"agent-marketplace: {message}", file=sys.stderr)
    return 1


def package_manifest(root: Path) -> tuple[str, Path, str, dict] | None:
    candidates = []
    for manifest in sorted(root.glob(".*-plugin/plugin.json")):
        name = manifest.parent.name
        if not (name.startswith(".") and name.endswith("-plugin")):
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            version = data.get("version", "")
            snapshot = data.get("agent_marketplace", {})
        except Exception:
            continue
        candidates.append((
            name[1:-7], manifest, str(version),
            snapshot if isinstance(snapshot, dict) else {},
        ))
    return candidates[0] if len(candidates) == 1 else None


def resolve_root(plugin: str, host: str = "", relpath: str = "") -> Path | None:
    """The registered install root, staleness-checked. A moved or removed
    root (plugin updates relocate the install) is an error, never a
    silent run of the wrong copy; a version drift against the on-disk
    manifest refreshes the registry entry."""
    registry = load_registry()
    entry = (registry.get("plugins") or {}).get(plugin)
    if not entry:
        return None
    hosts = entry.get("hosts") if isinstance(entry, dict) else None
    if isinstance(hosts, dict):
        if host:
            entry = hosts.get(host)
        elif len(hosts) == 1:
            entry = next(iter(hosts.values()))
        else:
            candidates = []
            for value in hosts.values():
                root = Path(value.get("root", "")) if isinstance(value, dict) else Path()
                target = root / relpath if relpath else root
                if root.is_dir() and target.is_file():
                    candidates.append((root, hashlib.sha256(target.read_bytes()).hexdigest()))
            if candidates and len({digest for _, digest in candidates}) == 1:
                return sorted(root for root, _ in candidates)[0]
            return None
    if not isinstance(entry, dict):
        return None
    root = Path(entry.get("root", ""))
    if not root.is_dir():
        return None
    manifest_info = package_manifest(root)
    on_disk = manifest_info[2] if manifest_info else ""
    on_disk_build = str(manifest_info[3].get("build_id", "")) \
        if manifest_info else ""
    if on_disk and (
        on_disk != entry.get("version", "")
        or on_disk_build != entry.get("build_id", "")
    ):
        try:
            def refresh(current):
                latest = (current.get("plugins") or {}).get(plugin)
                if not isinstance(latest, dict):
                    return
                latest_hosts = latest.get("hosts")
                selected = latest_hosts.get(host) if isinstance(latest_hosts, dict) else latest
                if not isinstance(selected, dict):
                    return
                selected["version"] = on_disk
                selected["build_id"] = on_disk_build
                selected["registered_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            update_registry(refresh)
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
    root = resolve_root(args.plugin, args.host, args.relpath)
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
    root = resolve_root(args.plugin, args.host, args.relpath)
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
    def register(registry):
        registry["schema_version"] = 2
        plugins = registry.setdefault("plugins", {})
        manifest = package_manifest(root)
        if manifest is None:
            raise ValueError(f"root has no unambiguous host manifest: {root}")
        host, _manifest_path, version, snapshot = manifest
        entry = plugins.get(args.plugin, {})
        if not isinstance(entry, dict):
            entry = {}
        hosts = entry.get("hosts", {})
        if not isinstance(hosts, dict):
            hosts = {}
        v1_root = Path(str(entry.get("root", "")))
        v1_manifest = package_manifest(v1_root) if v1_root.is_dir() else None
        if v1_manifest is not None:
            v1_host, _v1_path, v1_version, v1_snapshot = v1_manifest
            hosts.setdefault(v1_host, {
                "root": str(v1_root.resolve()),
                "version": v1_version,
                "build_id": str(v1_snapshot.get("build_id", "")),
                "registered_at": str(entry.get("registered_at", "")),
            })
        hosts[host] = {
            "root": str(root),
            "version": version,
            "build_id": str(snapshot.get("build_id", "")),
            "registered_at": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
        }
        plugins[args.plugin] = {"hosts": hosts}
    try:
        update_registry(register)
    except (OSError, TimeoutError, ValueError) as exc:
        return fail(f"could not update plugin registry: {exc}")
    print(f"agent-marketplace: registered {args.plugin} at {root}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run")
    p.add_argument("plugin")
    p.add_argument("relpath")
    p.add_argument("--host", default="")
    p.add_argument("args", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("path")
    p.add_argument("plugin")
    p.add_argument("relpath")
    p.add_argument("--host", default="")
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
