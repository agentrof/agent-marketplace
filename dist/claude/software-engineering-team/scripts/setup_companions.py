#!/usr/bin/env python3
"""Check or apply the optional third-party companion skills.

``check`` is read-only and exits non-zero when a selected companion is not
installed or its project settings are missing. ``apply`` installs missing
companions through their official CLIs, then writes Claude deny rules and
environment into ``.claude/settings.json``, the Codex skill disable block
into the user Codex config and each companion's project files. Command output
is never echoed, so a CLI that prints a token cannot leak it here.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import tomllib
from pathlib import Path

import atomic_file

REGISTRY = Path(__file__).resolve().parents[1] / "skill-content" / "setup" / "data" / "companions.json"
HOSTS = ("claude", "codex")
CODEX_START = "# agent-marketplace:software-engineering-team:companions:start"
CODEX_END = "# agent-marketplace:software-engineering-team:companions:end"


def load_registry(path: Path = REGISTRY) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def codex_config_path() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"


def blocked_names(companion: dict) -> list[str]:
    namespace = companion.get("skill_namespace", companion["id"])
    return [f"{namespace}:{skill}" for skill in companion.get("blocked_skills", [])]


def strings(value) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return set().union(*(strings(item) for item in value)) if value else set()
    return set()


def installed(verify: dict, root: Path) -> bool:
    if verify["kind"] == "path_exists":
        return (root / verify["path"]).is_file()
    argv = shlex.split(verify["command"])
    if shutil.which(argv[0]) is None:
        return False
    result = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return False
    try:
        return verify["plugin_id"] in strings(json.loads(result.stdout))
    except json.JSONDecodeError:
        return False


def codex_disabled() -> set[str]:
    try:
        config = tomllib.loads(codex_config_path().read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    entries = config.get("skills", {}).get("config", [])
    return {entry.get("name") for entry in entries
            if isinstance(entry, dict) and entry.get("enabled") is False}


def file_present(path: Path, content) -> bool:
    if isinstance(content, str):
        return path.is_file() and path.read_text(encoding="utf-8") == content
    current = read_json(path)
    return all(current.get(key) == value for key, value in content.items())


def status(registry: dict, root: Path, hosts: tuple[str, ...]) -> tuple[list[dict], list[str]]:
    settings = read_json(root / ".claude" / "settings.json")
    deny = set(settings.get("permissions", {}).get("deny", []))
    env = settings.get("env", {})
    disabled = codex_disabled() if "codex" in hosts else set()
    rows, findings = [], []
    for companion in registry["companions"]:
        names = blocked_names(companion)
        files_ok = all(file_present(root / item["path"], item["content"])
                       for item in companion.get("files", []))
        for path in companion.get("forbidden_paths", []):
            if (root / path).exists():
                findings.append(f"{companion['id']}: {path} must not exist; {companion['forbidden_reason']}")
        for host in hosts:
            if host == "claude":
                blocked = all(f"Skill({name})" in deny for name in names) if names else None
                env_ok = all(env.get(item["name"]) == item["value"] for item in companion["env"]) \
                    if companion.get("env") else None
            else:
                blocked = set(names) <= disabled if names else None
                env_ok = None
            rows.append({
                "id": companion["id"], "host": host,
                "installed": installed(companion["hosts"][host]["verify"], root),
                "blocked_rules_present": blocked, "env_present": env_ok,
                "files_present": files_ok if companion.get("files") else None,
            })
    return rows, findings


def write_claude_settings(registry: dict, root: Path) -> None:
    path = root / ".claude" / "settings.json"
    settings = read_json(path)
    deny = settings.setdefault("permissions", {}).setdefault("deny", [])
    env = settings.setdefault("env", {})
    for companion in registry["companions"]:
        deny.extend(rule for rule in (f"Skill({name})" for name in blocked_names(companion))
                    if rule not in deny)
        env.update((item["name"], item["value"]) for item in companion.get("env", []))
    text = json.dumps(settings, indent=2) + "\n"
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        atomic_file.replace_text(path, text)


def write_codex_config(registry: dict) -> None:
    path = codex_config_path()
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    block = "\n".join([CODEX_START, *(
        f'[[skills.config]]\nname = "{name}"\nenabled = false'
        for companion in registry["companions"] for name in blocked_names(companion)
    ), CODEX_END])
    if CODEX_START in current and CODEX_END in current:
        head, rest = current.split(CODEX_START, 1)
        text = head + block + rest.split(CODEX_END, 1)[1]
    else:
        text = current + ("\n" if current and not current.endswith("\n") else "") + block + "\n"
    if text != current:
        atomic_file.replace_text(path, text)


def write_files(registry: dict, root: Path) -> None:
    for companion in registry["companions"]:
        for item in companion.get("files", []):
            path, content = root / item["path"], item["content"]
            if file_present(path, content):
                continue
            if not isinstance(content, str):
                content = json.dumps({**read_json(path), **content}, indent=2) + "\n"
            atomic_file.replace_text(path, content)


def install(registry: dict, root: Path, hosts: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Run missing installs; return the commands and app steps the owner must run by hand."""
    owner, notes = [], []
    for companion in registry["companions"]:
        for host in hosts:
            spec = companion["hosts"][host]
            if installed(spec["verify"], root):
                continue
            # Exit codes are not trusted: a repeated "marketplace add" fails
            # on some CLI versions, so the verify step alone decides success.
            for command in spec["install"]:
                argv = shlex.split(command)
                if shutil.which(argv[0]) is not None:
                    subprocess.run(argv, cwd=root, stdin=subprocess.DEVNULL,
                                   capture_output=True, check=False)
            if installed(spec["verify"], root):
                continue
            verify = spec["verify"]
            if (host == "codex" and verify["kind"] == "plugin_list"
                    and shutil.which(shlex.split(verify["command"])[0]) is None):
                plugin, _, marketplace = verify["plugin_id"].partition("@")
                notes.append(f"Codex CLI not found: install plugin {plugin} from marketplace"
                             f" {marketplace} in the Codex app via /plugins")
            else:
                owner.extend(spec["install"])
    return owner, notes


def mark(value) -> str:
    return "-" if value is None else ("yes" if value else "NO")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("check", "apply"), default="check")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--host", choices=(*HOSTS, "all"), default="all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.project_root).resolve()
    if not root.is_dir():
        parser.error(f"project root is not a directory: {root}")
    hosts = HOSTS if args.host == "all" else (args.host,)
    registry = load_registry()
    owner, notes = [], []
    if args.action == "apply":
        owner, notes = install(registry, root, hosts)
        if "claude" in hosts:
            write_claude_settings(registry, root)
        if "codex" in hosts:
            write_codex_config(registry)
        write_files(registry, root)
    rows, findings = status(registry, root, hosts)
    ok = not findings and not owner and not notes and all(
        value is not False for row in rows for value in row.values())
    if args.json:
        print(json.dumps({"ok": ok, "rows": rows, "findings": findings,
                          "owner_commands": owner, "owner_app_steps": notes}, indent=2))
    else:
        columns = ("id", "host", "installed", "blocked_rules_present", "env_present", "files_present")
        print(" | ".join(columns))
        for row in rows:
            print(" | ".join(row[c] if c in ("id", "host") else mark(row[c]) for c in columns))
        for value in findings:
            print(f"ERROR {value}")
        for command in owner:
            print(f"RUN   {command}")
        for note in notes:
            print(f"APP   {note}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
