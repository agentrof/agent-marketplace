#!/usr/bin/env python3
"""Check or apply the optional third-party companion skills.

``check`` is read-only and exits non-zero when a selected companion is not
installed, not enabled, not loaded by the host or its project settings are
missing. ``apply`` installs missing companions through their official CLIs,
then writes Claude deny rules and environment into ``.claude/settings.json``,
the Codex skill disable block into the user Codex config and each companion's
project files. ``uninstall`` removes every companion from the selected hosts
and the managed settings, then proves each is gone. Each action ends with a
table. Command output is never echoed, so a CLI that prints a token cannot
leak it here.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import threading
import time
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


def find_codex_cli() -> None:
    """The desktop app ships the Codex CLI without putting it on PATH; add it for this process."""
    if shutil.which("codex"):
        return
    for folder in ("/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin",):
        if (Path(folder) / "codex").is_file():
            os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
            return


find_codex_cli()


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


def plugin_state(verify: dict, root: Path) -> tuple[bool, bool]:
    """(present, enabled) of the plugin record; Claude counts only a user-scoped record with its folder."""
    argv = shlex.split(verify["command"])
    if shutil.which(argv[0]) is None:
        return False, False
    result = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return False, False
    try:
        listing = json.loads(result.stdout)
        if argv[0] == "codex":
            items = [item for item in listing.get("installed", [])
                     if item.get("pluginId") == verify["plugin_id"] and item.get("installed") is True]
        else:
            items = [item for item in listing if isinstance(item, dict) and item.get("id") == verify["plugin_id"]
                     and item.get("scope") == "user" and Path(item.get("installPath", "")).is_dir()]
    except (json.JSONDecodeError, AttributeError):
        return False, False
    return bool(items), any(item.get("enabled") is True for item in items)


def installed(verify: dict, root: Path) -> bool:
    if verify["kind"] == "path_exists":
        base = Path.home() if verify.get("base") == "home" else root
        return (base / verify["path"]).is_file()
    if verify["kind"] == "plugin_list":
        return all(plugin_state(verify, root))
    argv = shlex.split(verify["command"])
    if shutil.which(argv[0]) is None:
        return False
    result = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False)
    return result.returncode == 0 and verify.get("contains", "") in result.stdout


def present(verify: dict, root: Path) -> bool:
    """Any trace of the companion; a disabled plugin record still counts."""
    return plugin_state(verify, root)[0] if verify["kind"] == "plugin_list" else installed(verify, root)


def component_type(spec: dict, name: str) -> str:
    kind = spec["verify"]["kind"]
    return "plugin" if kind == "plugin_list" else "skill" if kind == "path_exists" else \
        "hook" if "hook" in name else "cli"


def lines_from(process) -> queue.Queue:
    """Lines of the child's stdout on a queue, None at its end, so reads can time out."""
    replies = queue.Queue()

    def read_replies():
        for line in process.stdout:
            replies.put(line)
        replies.put(None)

    threading.Thread(target=read_replies, daemon=True).start()
    return replies


def stop(process) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    process.stdin and process.stdin.close()
    process.stdout.close()


def claude_skills(root: Path) -> tuple[set[str] | None, list[str]]:
    """Skills a fresh Claude session loads, from its init event; stopped before any model turn."""
    process = None
    try:
        process = subprocess.Popen(["claude", "-p", "x", "--output-format", "stream-json", "--verbose", "--max-turns", "1"],
                                   cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        replies, deadline = lines_from(process), time.monotonic() + 90
        while True:
            line = replies.get(timeout=max(0, deadline - time.monotonic()))
            if line is None:
                raise ValueError("host closed before its init event")
            event = json.loads(line)
            if isinstance(event, dict) and event.get("subtype") == "init":
                return set(event["skills"]), []
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, queue.Empty):
        return None, ["claude: could not verify skills through the host loader; availability is unverified"]
    finally:
        if process is not None:
            stop(process)


def codex_skills(registry: dict, root: Path) -> tuple[dict[str, bool], list[str]]:
    """Ask the host loader; files and installation records alone are not proof."""
    available, findings = {}, []
    process = None
    try:
        listing = subprocess.run(["codex", "plugin", "list", "--json"], cwd=root,
                                 capture_output=True, text=True, check=True, timeout=30)
        plugins = {item["pluginId"]: item for item in json.loads(listing.stdout)["installed"]}
        process = subprocess.Popen(["codex", "app-server", "--stdio"], cwd=root,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        replies = lines_from(process)
        request_id = 0

        def request(method, params):
            nonlocal request_id
            request_id += 1
            process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}) + "\n")
            process.stdin.flush()
            deadline = time.monotonic() + 30
            while True:
                line = replies.get(timeout=max(0, deadline - time.monotonic()))
                if line is None:
                    raise ValueError("host closed before replying")
                response = json.loads(line)
                if response.get("id") == request_id:
                    if "error" in response:
                        raise ValueError(f"{method} failed")
                    return response["result"]

        request("initialize", {"clientInfo": {"name": "companion_check", "version": "1"},
                               "capabilities": {"experimentalApi": True}})
        process.stdin.write('{"method":"initialized","params":{}}\n')
        process.stdin.flush()
        loaded = request("skills/list", {"cwds": [str(root)], "forceReload": True})
        for entry in loaded["data"]:
            for skill in entry["skills"]:
                available[skill["name"]] = (skill.get("enabled") is True
                                            and Path(skill["path"]).is_file()
                                            and bool(Path(skill["path"]).read_text(encoding="utf-8").strip()))
        for companion in registry["companions"]:
            spec = companion["hosts"]["codex"]["verify"]
            plugin = plugins.get(spec.get("plugin_id"), {})
            if plugin.get("source", {}).get("source") != "remote":
                continue
            remote_id = plugin["source"]["id"]
            marketplace = plugin["marketplaceName"]
            detail = request("plugin/read", {"pluginName": remote_id, "remoteMarketplaceName": marketplace})["plugin"]
            summary = detail["summary"]
            enabled = {skill["name"] for skill in detail["skills"] if skill.get("enabled") is True}
            for name in spec.get("skills", []):
                short_name = name.split(":", 1)[-1]
                available[name] = (summary.get("installed") is True and summary.get("enabled") is True
                                   and short_name in enabled and bool(request("plugin/skill/read", {
                                       "remoteMarketplaceName": marketplace, "remotePluginId": remote_id,
                                       "skillName": short_name}).get("contents")))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, queue.Empty):
        findings.append("codex: could not verify skills through the host loader; availability is unverified")
    finally:
        if process is not None:
            stop(process)
    return available, findings


def codex_disabled() -> set[str]:
    try:
        config = tomllib.loads(codex_config_path().read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    entries = config.get("skills", {}).get("config", [])
    return {entry.get("name") for entry in entries
            if isinstance(entry, dict) and entry.get("enabled") is False}


def file_present(path: Path, content, if_missing: bool = False) -> bool:
    if if_missing:
        return path.is_file()
    if isinstance(content, str):
        return path.is_file() and path.read_text(encoding="utf-8") == content
    current = read_json(path)
    return all(current.get(key) == value for key, value in content.items())


def hook_files(step: dict, root: Path) -> list[Path]:
    """The step's installed hook scripts; git resolves core.hooksPath and linked worktrees."""
    spec = step.get("hook_env")
    if not spec or shutil.which("git") is None:
        return []
    result = subprocess.run(["git", "rev-parse", "--git-path", "hooks"], cwd=root,
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return []
    hooks = root / result.stdout.strip()
    return [hooks / name for name in spec["hooks"]
            if (hooks / name).is_file() and spec["marker"] in (hooks / name).read_text(encoding="utf-8")]


def hook_missing(step: dict, root: Path) -> list[Path]:
    line = step.get("hook_env", {}).get("line")
    return [path for path in hook_files(step, root)
            if line not in path.read_text(encoding="utf-8").splitlines()]


def write_hook_env(registry: dict, root: Path) -> None:
    """Put the step's env line right under each installed hook's shebang, once."""
    for companion in registry["companions"]:
        for step in companion.get("steps", []):
            for path in hook_missing(step, root):
                head, _, body = path.read_text(encoding="utf-8").partition("\n")
                path.write_bytes(f"{head}\n{step['hook_env']['line']}\n{body}".encode("utf-8"))


def loaded(names: list[str], available) -> tuple[bool | None, str | None]:
    """Whether the host loader returned every skill name, and how many of them."""
    if not names:
        return None, None
    have = sum(1 for name in names if (name in available if isinstance(available, set) else available.get(name) is True))
    return have == len(names), f"{have}/{len(names)}"


def status(registry: dict, root: Path, hosts: tuple[str, ...]) -> tuple[list[dict], list[str]]:
    settings = read_json(root / ".claude" / "settings.json")
    deny = set(settings.get("permissions", {}).get("deny", []))
    env = settings.get("env", {})
    disabled = codex_disabled() if "codex" in hosts else set()
    rows, findings = [], []
    codex_loaded, codex_findings = codex_skills(registry, root) if "codex" in hosts else ({}, [])
    claude_loaded, claude_findings = claude_skills(root) if "claude" in hosts else (set(), [])
    findings.extend(codex_findings + claude_findings)
    for companion in registry["companions"]:
        names = blocked_names(companion)
        files_ok = all(file_present(root / item["path"], item["content"], item.get("if_missing", False))
                       for item in companion.get("files", []))
        for step in companion.get("steps", []):
            rows.append({"id": companion["id"], "type": component_type(step, step["name"]), "host": step["name"],
                         "installed": installed(step["verify"], root), "enabled": None, "skills_loaded": None,
                         "blocked_rules_present": None, "env_present": None, "files_present": None, "skills_available": None})
            for path in hook_missing(step, root):
                findings.append(f"{companion['id']}: hook {path.name} lacks `{step['hook_env']['line']}`;"
                                " rerun companions apply")
        for path in companion.get("forbidden_paths", []):
            if (root / path).exists():
                findings.append(f"{companion['id']}: {path} must not exist; {companion['forbidden_reason']}")
        for host in hosts:
            spec = companion["hosts"][host]
            if host == "claude":
                blocked = all(f"Skill({name})" in deny for name in names) if names else None
                env_ok = all(env.get(item["name"]) == item["value"] for item in companion["env"]) \
                    if companion.get("env") else None
                available = claude_loaded
            else:
                blocked = set(names) <= disabled if names else None
                env_ok = None
                available = codex_loaded
            # A Claude skill list has no per-host names; the Codex spec carries the same skill names.
            wanted = spec["verify"].get("skills") or companion["hosts"]["codex"]["verify"].get("skills", [])
            ok, count = loaded(wanted, available if available is not None else set())
            if available is None:
                ok, count = False, "unverified"
            plugin = spec["verify"]["kind"] == "plugin_list"
            present_, enabled = plugin_state(spec["verify"], root) if plugin else (installed(spec["verify"], root), None)
            rows.append({
                "id": companion["id"], "type": component_type(spec, host), "host": host,
                "installed": present_, "enabled": enabled, "skills_loaded": count,
                "blocked_rules_present": blocked, "env_present": env_ok,
                "files_present": files_ok if companion.get("files") else None,
                "skills_available": ok,
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
            if file_present(path, content, item.get("if_missing", False)):
                continue
            if not isinstance(content, str):
                content = json.dumps({**read_json(path), **content}, indent=2) + "\n"
            atomic_file.replace_text(path, content)


def install(registry: dict, root: Path, hosts: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """Run missing installs; return the commands and app steps the owner must run by hand."""
    owner, notes = [], []
    for companion in registry["companions"]:
        env = {**os.environ, **{item["name"]: item["value"] for item in companion.get("env", [])}}
        # Host-independent steps (a CLI the host skills need, a git hook) run first.
        for host, spec in [*((step["name"], step) for step in companion.get("steps", [])),
                           *((host, companion["hosts"][host]) for host in hosts)]:
            if installed(spec["verify"], root):
                continue
            # Exit codes are not trusted: a repeated "marketplace add" fails
            # on some CLI versions, so the verify step alone decides success.
            for command in spec["install"]:
                argv = shlex.split(command)
                if shutil.which(argv[0]) is not None:
                    subprocess.run(argv, cwd=root, env=env, stdin=subprocess.DEVNULL,
                                   capture_output=True, check=False)
            if installed(spec["verify"], root):
                continue
            verify, requires = spec["verify"], spec.get("requires")
            if requires and shutil.which(requires["command"]) is None:
                notes.append(requires["hint"])
            if (host == "codex" and verify["kind"] == "plugin_list"
                    and shutil.which(shlex.split(verify["command"])[0]) is None):
                plugin, _, marketplace = verify["plugin_id"].partition("@")
                notes.append(f"Codex CLI not found: install plugin {plugin} from marketplace"
                             f" {marketplace} in the Codex app via /plugins")
            else:
                owner.extend(spec["install"])
    return owner, notes


def remove_claude_settings(registry: dict, root: Path) -> None:
    path = root / ".claude" / "settings.json"
    if not path.is_file():
        return
    settings = read_json(path)
    rules = {f"Skill({name})" for companion in registry["companions"] for name in blocked_names(companion)}
    permissions, env = settings.get("permissions", {}), settings.get("env", {})
    permissions["deny"] = [rule for rule in permissions.get("deny", []) if rule not in rules]
    for item in (item for companion in registry["companions"] for item in companion.get("env", [])):
        if env.get(item["name"]) == item["value"]:
            del env[item["name"]]
    for key, value in (("permissions", {k: v for k, v in permissions.items() if v}), ("env", env)):
        settings.pop(key, None)
        if value:
            settings[key] = value
    text = json.dumps(settings, indent=2) + "\n"
    if path.read_text(encoding="utf-8") != text:
        atomic_file.replace_text(path, text)


def remove_codex_config() -> None:
    path = codex_config_path()
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    if CODEX_START in current and CODEX_END in current:
        head, rest = current.split(CODEX_START, 1)
        atomic_file.replace_text(path, head.rstrip("\n") + ("\n" if head.strip() else "") + rest.split(CODEX_END, 1)[1].lstrip("\n"))


def uninstall(registry: dict, root: Path, hosts: tuple[str, ...]) -> list[dict]:
    """Remove every companion from the hosts, then report whether each is gone."""
    rows = []
    for companion in registry["companions"]:
        steps = companion.get("steps", [])
        # Steps unwind in reverse; the first one (the CLI the others call) goes last.
        units = [*((step["name"], step) for step in steps[1:][::-1]),
                 *((host, companion["hosts"][host]) for host in hosts),
                 *((step["name"], step) for step in steps[:1])]
        for name, spec in units:
            for command in spec.get("uninstall", []):
                argv = shlex.split(command)
                if shutil.which(argv[0]) is not None:
                    subprocess.run(argv, cwd=root, stdin=subprocess.DEVNULL, capture_output=True, check=False)
            for pattern in spec.get("remove_paths", []):
                for path in Path.home().glob(pattern):
                    shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
            rows.append({"id": companion["id"], "type": component_type(spec, name), "host": name,
                         "removed": not present(spec["verify"], root)})
    return rows


def mark(value) -> str:
    return "-" if value is None else value if isinstance(value, str) else ("yes" if value else "NO")


def table(rows: list[dict], columns: tuple[str, ...]) -> None:
    print(" | ".join(columns))
    for row in rows:
        print(" | ".join(row[c] if c in ("id", "type", "host") else mark(row[c]) for c in columns))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", nargs="?", choices=("check", "apply", "uninstall"), default="check")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--host", choices=(*HOSTS, "all"), default="all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.project_root).resolve()
    if not root.is_dir():
        parser.error(f"project root is not a directory: {root}")
    hosts = HOSTS if args.host == "all" else (args.host,)
    registry = load_registry()
    if args.action == "uninstall":
        rows = uninstall(registry, root, hosts)
        if "claude" in hosts:
            remove_claude_settings(registry, root)
        if "codex" in hosts:
            remove_codex_config()
        ok = all(row["removed"] for row in rows)
        if args.json:
            print(json.dumps({"ok": ok, "rows": rows}, indent=2))
        else:
            table(rows, ("id", "type", "host", "removed"))
        return 0 if ok else 1
    owner, notes = [], []
    if args.action == "apply":
        owner, notes = install(registry, root, hosts)
        if "claude" in hosts:
            write_claude_settings(registry, root)
        if "codex" in hosts:
            write_codex_config(registry)
        write_files(registry, root)
        write_hook_env(registry, root)
    rows, findings = status(registry, root, hosts)
    info = [spec["note"] for companion in registry["companions"]
            for host in hosts if (spec := companion["hosts"][host]).get("note")]
    ok = not findings and not owner and not notes and all(
        value is not False for row in rows for value in row.values())
    if args.json:
        print(json.dumps({"ok": ok, "rows": rows, "findings": findings,
                          "owner_commands": owner, "owner_app_steps": notes, "notes": info}, indent=2))
    else:
        table(rows, ("id", "type", "host", "installed", "enabled", "skills_loaded",
                     "blocked_rules_present", "env_present", "files_present"))
        for value in findings:
            print(f"ERROR {value}")
        for command in owner:
            print(f"RUN   {command}")
        for note in notes:
            print(f"APP   {note}")
        for note in info:
            print(f"NOTE  {note}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
