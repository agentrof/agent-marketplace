#!/usr/bin/env python3
"""Exercise checkout or public-stable installs on real host CLIs."""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


PMO = "project-management-office"
MARKETPLACE = "agent-marketplace"
PUBLIC_MARKETPLACE = "agentrof/agent-marketplace"


class SmokeFailure(RuntimeError):
    pass


def marketplace_home(env: dict[str, str]) -> Path:
    override = env.get("AGENT_MARKETPLACE_HOME", "").strip()
    if override:
        return Path(override)
    vendor = env.get("AGENTROF_HOME", "").strip()
    return (Path(vendor) if vendor else Path.home() / ".agentrof") / MARKETPLACE


def run(command: list[str], env: dict[str, str], input_text: str = "") -> str:
    completed = subprocess.run(
        command, input=input_text, capture_output=True, text=True, env=env,
        check=False, timeout=60,
    )
    if completed.returncode != 0:
        raise SmokeFailure(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed.stdout


def team_names(root: Path) -> list[str]:
    return sorted(
        path.name for path in (root / "plugins").iterdir()
        if path.is_dir() and path.name != PMO
    )


def require_cli(name: str) -> None:
    if shutil.which(name) is None:
        raise SmokeFailure(f"required host CLI is unavailable: {name}")


def assert_enabled(inventory: dict | list, expected: set[str], host: str) -> None:
    if host == "claude":
        enabled = {
            entry.get("id", "").split("@", 1)[0]
            for entry in inventory
            if entry.get("enabled") is True
        }
    else:
        enabled = {
            entry.get("name", "")
            for entry in inventory.get("installed", [])
            if entry.get("enabled") is True
        }
    if not expected <= enabled:
        raise SmokeFailure(
            f"{host} enabled set is incomplete: missing "
            + ", ".join(sorted(expected - enabled))
        )


def plugin_install_path(inventory: dict | list, plugin: str, host: str) -> Path:
    entries = inventory if host == "claude" else inventory.get("installed", [])
    for entry in entries:
        name = entry.get("id", "").split("@", 1)[0] \
            if host == "claude" else entry.get("name", "")
        if name == plugin:
            raw = entry.get("installPath") if host == "claude" \
                else entry.get("installedPath")
            if not raw and host == "codex":
                source = entry.get("source") or {}
                raw = source.get("path") if isinstance(source, dict) else ""
            if raw:
                return Path(raw)
    raise SmokeFailure(f"{host} inventory has no install path for {plugin}")


def hook_payload(session_id: str, project: Path, event: str, tool: str = "") -> str:
    payload = {
        "session_id": session_id,
        "cwd": str(project),
        "hook_event_name": event,
        "permission_mode": "default",
    }
    if tool:
        payload.update({
            "tool_name": tool,
            "tool_input": {
                "file_path": str(project / "smoke-change.txt"),
                "content": "smoke",
            },
        })
    return json.dumps(payload)


def initialize_project(project: Path, team: str, env: dict[str, str]) -> None:
    workspace = project / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "config.json").write_text(
        json.dumps({
            "team_id": team,
            "project_key": "smoke",
            "project_origin": "greenfield",
        }),
        encoding="utf-8",
    )
    run(["git", "init", "--initial-branch=main", str(project)], env)
    run(["git", "-C", str(project), "config", "user.name", "Agent Marketplace Smoke"], env)
    run([
        "git", "-C", str(project), "config", "user.email",
        "agent-marketplace-smoke@example.invalid",
    ], env)
    run(["git", "-C", str(project), "add", "--", "workspace/config.json"], env)
    run([
        "git", "-C", str(project), "commit", "--no-gpg-sign", "-m",
        "test: initialize smoke project",
    ], env)


def register_project_contract(
    env: dict[str, str], pmo_root: Path, team_root: Path, project: Path, team: str,
) -> None:
    run([
        sys.executable,
        str(marketplace_home(env) / "bin" / "marketplace_run.py"),
        "register", "--plugin", team, "--root", str(team_root),
    ], env)
    run([
        sys.executable, str(pmo_root / "scripts" / "pmo_cli.py"),
        "project", "register", "--key", "smoke", "--name", "Smoke",
        "--team", team,
        "--stamp-config", str(project / "workspace" / "config.json"),
        "--project-root", str(project), "--workspace", "workspace",
    ], env)


def assert_preparation_payload(
    env: dict[str, str], team_root: Path, project: Path, team: str,
) -> None:
    script = team_root / "scripts" / "preparation_check.py"
    if team != "software-engineering-team" or not team_root.exists():
        return  # deterministic host simulations use synthetic install paths
    required = (
        script,
        team_root / "scripts" / "experience_compile.py",
        team_root / "scripts" / "backlog_compile.py",
        team_root / "skill-content" / "experience-design" / "SKILL.md",
        team_root / "skill-content" / "backlog-plan" / "SKILL.md",
        team_root / "agents" / "experience-reviewer.md",
        team_root / "agents" / "backlog-reviewer.md",
        team_root / "flows" / "experience-design.md",
        team_root / "flows" / "backlog-planning.md",
    )
    missing = [str(path.relative_to(team_root)) for path in required if not path.is_file()]
    if missing:
        raise SmokeFailure("greenfield preparation payload is incomplete: " + ", ".join(missing))
    if (team_root / "skill-content" / "plan-backlog").exists():
        raise SmokeFailure("removed plan-backlog alias is still packaged")
    completed = subprocess.run(
        [sys.executable, str(script), "route", "--project-root", str(project),
         "--intent", "deliver", "--json"],
        capture_output=True, text=True, env=env, check=False, timeout=30,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeFailure("preparation route returned invalid JSON") from exc
    if completed.returncode != 1 or payload.get("next_entry") != "business-analysis":
        raise SmokeFailure("fresh greenfield route did not stop at business-analysis")


def assert_team_gate(
    env: dict[str, str], team_root: Path, project: Path, session_id: str,
    expected: int,
) -> None:
    completed = subprocess.run(
        [sys.executable, str(team_root / "scripts" / "team_guard.py"), "pre"],
        input=hook_payload(session_id, project, "PreToolUse", "Write"),
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=30,
    )
    if completed.returncode != expected:
        diagnostic = ""
        launcher = marketplace_home(env) / "bin" / "pmo_cli.py"
        if launcher.is_file():
            inspected = subprocess.run(
                [sys.executable, str(launcher), "upgrade", "status",
                 "--project-root", str(project), "--json"],
                capture_output=True, text=True, env=env, check=False, timeout=30,
            )
            diagnostic = (
                f"\nupgrade status ({inspected.returncode}):\n"
                f"{inspected.stdout}{inspected.stderr}"
            )
        raise SmokeFailure(
            f"team preflight returned {completed.returncode}, expected {expected}:\n"
            f"{completed.stderr}{diagnostic}"
        )


def mark_pmo_ready(
    env: dict[str, str], pmo_root: Path, project: Path, session_id: str,
) -> None:
    output = run(
        [sys.executable, str(pmo_root / "scripts" / "hook_session_start.py")],
        env,
        hook_payload(session_id, project, "SessionStart"),
    )
    if "AGENT_MARKETPLACE_PMO_READY: project-management-office" not in output:
        raise SmokeFailure("PMO SessionStart did not mark the smoke session ready")


def codex_skills(env: dict[str, str], project: Path) -> list[dict]:
    process = subprocess.Popen(
        ["codex", "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert process.stdin is not None
    assert process.stdout is not None
    responses: queue.Queue[str] = queue.Queue()

    def collect_stdout() -> None:
        for line in process.stdout:
            responses.put(line)

    threading.Thread(target=collect_stdout, daemon=True).start()
    seen: list[str] = []

    def send(message: dict) -> None:
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    def receive(request_id: int) -> dict:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                line = responses.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            seen.append(line)
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == request_id:
                return message
        raise SmokeFailure(
            f"codex app-server returned no response for request {request_id}\n"
            + "".join(seen)
        )

    try:
        send({
            "method": "initialize", "id": 1,
            "params": {"clientInfo": {
                "name": "agent_marketplace_smoke", "title": "Agent Marketplace Smoke",
                "version": "1.0.0",
            }},
        })
        initialized = receive(1)
        if initialized.get("error"):
            raise SmokeFailure(f"codex initialize error: {initialized['error']}")
        send({"method": "initialized", "params": {}})
        send({
            "method": "skills/list", "id": 2,
            "params": {"cwds": [str(project)], "forceReload": True},
        })
        response = receive(2)
        if response.get("error"):
            raise SmokeFailure(f"codex skills/list error: {response['error']}")
        data = response.get("result", {}).get("data", [])
        for entry in data:
            if entry.get("cwd") == str(project):
                return entry.get("skills", [])
        raise SmokeFailure("codex skills/list response omitted the project cwd")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def smoke_claude(
    root: Path, teams: list[str], marketplace_source: str | Path | None = None
) -> None:
    require_cli("claude")
    version = run(["claude", "--version"], os.environ.copy()).strip()
    for team in teams:
        with tempfile.TemporaryDirectory(prefix="agent-marketplace-claude-smoke.") as state:
            state_root = Path(state)
            env = {
                **os.environ,
                "CLAUDE_CONFIG_DIR": str(state_root / "claude"),
                "AGENTROF_HOME": str(state_root / "agentrof"),
            }
            project = state_root / "project"
            initialize_project(project, team, env)
            run([
                "claude", "plugin", "marketplace", "add",
                str(marketplace_source or root),
            ], env)
            run(["claude", "plugin", "install", f"{team}@{MARKETPLACE}"], env)
            installed = json.loads(run(
                ["claude", "plugin", "list", "--json"], env
            ))
            assert_enabled(installed, {PMO, team}, "claude")
            run([
                "claude", "plugin", "update", f"{team}@{MARKETPLACE}",
            ], env)
            team_root = plugin_install_path(installed, team, "claude")
            pmo_root = plugin_install_path(installed, PMO, "claude")
            assert_preparation_payload(env, team_root, project, team)
            assert_team_gate(env, team_root, project, "missing-pmo", 2)
            first_setup = json.loads(run([
                sys.executable,
                str(team_root / "scripts" / "generate_claude_project.py"),
                "apply", "--project-root", str(project),
                "--workspace", "workspace",
            ], env))
            if not first_setup.get("changes"):
                raise SmokeFailure("Claude project generator wrote no managed surface")
            second_setup = json.loads(run([
                sys.executable,
                str(team_root / "scripts" / "generate_claude_project.py"),
                "apply", "--project-root", str(project),
                "--workspace", "workspace",
            ], env))
            if second_setup.get("changes") != []:
                raise SmokeFailure("Claude project generator is not idempotent")
            mark_pmo_ready(env, pmo_root, project, "ready-pmo")
            register_project_contract(env, pmo_root, team_root, project, team)
            assert_team_gate(env, team_root, project, "ready-pmo", 0)
            run(["claude", "plugin", "disable", f"{team}@{MARKETPLACE}"], env)
            run(["claude", "plugin", "disable", f"{PMO}@{MARKETPLACE}"], env)
            disabled = json.loads(run(
                ["claude", "plugin", "list", "--json"], env
            ))
            assert_team_gate(env, team_root, project, "disabled-pmo", 2)
            run(["claude", "plugin", "enable", f"{PMO}@{MARKETPLACE}"], env)
            run(["claude", "plugin", "enable", f"{team}@{MARKETPLACE}"], env)
            run(["claude", "plugin", "uninstall", f"{team}@{MARKETPLACE}"], env)
            removed = json.loads(run(
                ["claude", "plugin", "list", "--json"], env
            ))
            if any(entry.get("id", "").split("@", 1)[0] == team for entry in removed):
                raise SmokeFailure(f"Claude did not remove {team}")
            run(["claude", "plugin", "install", f"{team}@{MARKETPLACE}"], env)
            assert_enabled(json.loads(run(
                ["claude", "plugin", "list", "--json"], env
            )), {PMO, team}, "claude")
    print(f"plugin-smoke: Claude lifecycle passed for every team ({version})")


def smoke_codex(
    root: Path, teams: list[str], marketplace_source: str | Path | None = None
) -> None:
    require_cli("codex")
    version = run(["codex", "--version"], os.environ.copy()).strip()
    for team in teams:
        with tempfile.TemporaryDirectory(prefix="agent-marketplace-codex-smoke.") as state:
            state_root = Path(state)
            codex_home = state_root / "codex"
            codex_home.mkdir()
            env = {
                **os.environ,
                "CODEX_HOME": str(codex_home),
                "AGENT_MARKETPLACE_HOME": str(state_root / "marketplace"),
            }
            project = state_root / "project"
            initialize_project(project, team, env)
            run([
                "codex", "plugin", "marketplace", "add",
                str(marketplace_source or root), "--json",
            ], env)
            available = json.loads(run([
                "codex", "plugin", "list", "--available", "--json",
            ], env))
            available_names = {entry.get("name") for entry in available.get("available", [])}
            if not {PMO, team} <= available_names:
                raise SmokeFailure("Codex available inventory is incomplete")
            run([
                "codex", "plugin", "add", f"{team}@{MARKETPLACE}", "--json",
            ], env)
            if marketplace_source == PUBLIC_MARKETPLACE:
                run([
                    "codex", "plugin", "marketplace", "upgrade",
                    MARKETPLACE, "--json",
                ], env)
            inventory = json.loads(run([
                "codex", "plugin", "list", "--json",
            ], env))
            assert_enabled(inventory, {team}, "codex")
            team_root = plugin_install_path(inventory, team, "codex")
            assert_preparation_payload(env, team_root, project, team)
            assert_team_gate(env, team_root, project, "missing-pmo", 2)
            run([
                "codex", "plugin", "add", f"{PMO}@{MARKETPLACE}", "--json",
            ], env)
            inventory = json.loads(run([
                "codex", "plugin", "list", "--json",
            ], env))
            assert_enabled(inventory, {PMO, team}, "codex")
            pmo_root = plugin_install_path(inventory, PMO, "codex")
            first_setup = json.loads(run([
                sys.executable,
                str(team_root / "scripts" / "generate_codex_project.py"),
                "--project-root", str(project), "--workspace", "workspace",
            ], env))
            if not first_setup.get("written"):
                raise SmokeFailure("Codex project generator wrote no managed surfaces")
            second_setup = json.loads(run([
                sys.executable,
                str(team_root / "scripts" / "generate_codex_project.py"),
                "--project-root", str(project), "--workspace", "workspace",
            ], env))
            if second_setup.get("written") != []:
                raise SmokeFailure("Codex project generator is not idempotent")
            mark_pmo_ready(env, pmo_root, project, "ready-pmo")
            register_project_contract(env, pmo_root, team_root, project, team)
            assert_team_gate(env, team_root, project, "ready-pmo", 0)
            skills = codex_skills(env, project)
            names = {entry.get("name", "") for entry in skills}
            expected_entries = {
                f"{team}:{path.name}"
                for path in (root / "plugins" / team / "skill-content").iterdir()
                if path.is_dir() and "exposure: entry" in
                (path / "SKILL.md").read_text(encoding="utf-8")
            }
            internal = {
                f"{team}:{path.name}"
                for path in (root / "plugins" / team / "skill-content").iterdir()
                if path.is_dir() and "exposure: internal" in
                (path / "SKILL.md").read_text(encoding="utf-8")
            }
            if not expected_entries <= names:
                raise SmokeFailure(
                    "Codex skills/list misses entry skills: "
                    + ", ".join(sorted(expected_entries - names))
                )
            if internal & names:
                raise SmokeFailure(
                    "Codex skills/list exposed internal skills: "
                    + ", ".join(sorted(internal & names))
                )
            run([
                "codex", "plugin", "remove", f"{team}@{MARKETPLACE}", "--json",
            ], env)
            removed = json.loads(run([
                "codex", "plugin", "list", "--json",
            ], env))
            if any(entry.get("name") == team for entry in removed.get("installed", [])):
                raise SmokeFailure(f"Codex did not remove {team}")
            names_after_remove = {
                entry.get("name", "") for entry in codex_skills(env, project)
            }
            if expected_entries & names_after_remove:
                raise SmokeFailure("Codex skills remained discoverable after removal")
            run([
                "codex", "plugin", "add", f"{team}@{MARKETPLACE}", "--json",
            ], env)
            assert_enabled(json.loads(run([
                "codex", "plugin", "list", "--json",
            ], env)), {PMO, team}, "codex")
    print(f"plugin-smoke: Codex lifecycle passed for every team ({version})")


def checkout_marketplace(root: Path, target: Path) -> Path:
    """Create a local-only catalog that installs the current generated dist."""
    shutil.copytree(root / "dist", target / "dist")
    claude = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(
        encoding="utf-8"
    ))
    for entry in claude.get("plugins", []):
        entry["source"] = f"./dist/claude/{entry['name']}"
    claude_path = target / ".claude-plugin" / "marketplace.json"
    claude_path.parent.mkdir(parents=True)
    claude_path.write_text(json.dumps(claude, indent=2) + "\n", encoding="utf-8")
    codex = json.loads((root / ".agents" / "plugins" / "marketplace.json").read_text(
        encoding="utf-8"
    ))
    for entry in codex.get("plugins", []):
        entry["source"] = {
            "source": "local",
            "path": f"./dist/codex/{entry['name']}",
        }
    codex_path = target / ".agents" / "plugins" / "marketplace.json"
    codex_path.parent.mkdir(parents=True)
    codex_path.write_text(json.dumps(codex, indent=2) + "\n", encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("all", "claude", "codex"),
                        default="all")
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--channel", choices=("checkout", "public"),
                        default="checkout")
    args = parser.parse_args()
    root = args.root.resolve()
    teams = team_names(root)
    if not teams:
        raise SmokeFailure("marketplace contains no team plugins")
    if args.channel == "public":
        source: str | Path = PUBLIC_MARKETPLACE
        if args.host in ("all", "claude"):
            smoke_claude(root, teams, source)
        if args.host in ("all", "codex"):
            smoke_codex(root, teams, source)
    else:
        with tempfile.TemporaryDirectory(prefix="agent-marketplace-checkout.") as tmp:
            source = checkout_marketplace(root, Path(tmp))
            if args.host in ("all", "claude"):
                smoke_claude(root, teams, source)
            if args.host in ("all", "codex"):
                smoke_codex(root, teams, source)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        raise SystemExit(f"plugin-smoke: {exc}") from exc
