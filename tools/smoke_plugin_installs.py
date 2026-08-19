#!/usr/bin/env python3
"""Install the standalone team on real host CLIs and exercise its package."""

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


TEAM = "software-engineering-team"
MARKETPLACE = "agent-marketplace"
PUBLIC_MARKETPLACES = {
    "claude": "https://github.com/agentrof/agent-marketplace.git#stable",
    "codex": "agentrof/agent-marketplace@stable",
}


class SmokeFailure(RuntimeError):
    pass


def run(command: list[str], env: dict[str, str]) -> str:
    completed = subprocess.run(
        command, capture_output=True, text=True, env=env, check=False,
        timeout=120,
    )
    if completed.returncode:
        raise SmokeFailure(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return completed.stdout


def require_cli(name: str) -> None:
    if shutil.which(name) is None:
        raise SmokeFailure(f"required host CLI is unavailable: {name}")


def init_project(path: Path, env: dict[str, str]) -> None:
    run(["git", "init", "--initial-branch=main", str(path)], env)
    run(["git", "-C", str(path), "config", "user.name", "Marketplace Smoke"], env)
    run(["git", "-C", str(path), "config", "user.email", "smoke@example.invalid"], env)


def installed_root(inventory: dict | list, host: str) -> Path:
    entries = inventory if host == "claude" else inventory.get("installed", [])
    for entry in entries:
        name = entry.get("id", "").split("@", 1)[0] \
            if host == "claude" else entry.get("name", "")
        if name != TEAM or entry.get("enabled") is not True:
            continue
        raw = entry.get("installPath") if host == "claude" \
            else entry.get("installedPath")
        if not raw and host == "codex":
            source = entry.get("source") or {}
            raw = source.get("path") if isinstance(source, dict) else ""
        if raw:
            return Path(raw)
    raise SmokeFailure(f"{host} inventory has no enabled {TEAM} install")


def exercise_package(team_root: Path, project: Path, env: dict[str, str]) -> None:
    setup = team_root / "scripts" / "setup_project.py"
    check = team_root / "scripts" / "setup_check.py"
    route = team_root / "scripts" / "requirement_route.py"
    delivery_compile = team_root / "scripts" / "delivery_compile.py"
    delivery_git = team_root / "scripts" / "delivery_git.py"
    product_chain_scripts = (
        "stage_package.py", "ba_compile.py", "landscape_check.py",
        "design_system_compile.py", "experience_compile.py",
        "backlog_compile.py", "architecture_compile.py",
    )
    for path in (
        setup, check, route,
        *(team_root / "scripts" / name for name in product_chain_scripts),
        delivery_compile, delivery_git, team_root / "scripts" / "delivery_provider.py",
    ):
        if not path.is_file():
            raise SmokeFailure(f"installed package is missing {path.relative_to(team_root)}")
    for script in (delivery_compile, delivery_git,
                   *(team_root / "scripts" / name for name in product_chain_scripts)):
        run([sys.executable, str(script), "--help"], env)
    required_flows = {
        "requirement.md", "business-analysis.md", "solution-design.md",
        "design-system.md", "experience-design.md", "backlog-planning.md",
    }
    present_flows = {path.name for path in (team_root / "flows").glob("*.md")}
    missing_flows = required_flows - present_flows
    if missing_flows:
        raise SmokeFailure("installed package is missing product-chain flows: "
                           + ", ".join(sorted(missing_flows)))
    required_agents = {
        "business-analyst.md", "solution-architect.md", "solution-reviewer.md",
        "ux-designer.md", "experience-reviewer.md", "product-owner.md",
        "software-architect.md",
    }
    missing_agents = [name for name in sorted(required_agents)
                      if not (team_root / "agents" / name).is_file()]
    if missing_agents:
        raise SmokeFailure("installed package is missing product-chain agents: "
                           + ", ".join(missing_agents))
    inspected = json.loads(run([
        sys.executable, str(setup), "inspect", "--project-root", str(project),
        "--json",
    ], env))
    if inspected.get("ok") is not True:
        raise SmokeFailure("project refresh inspection did not produce a viable plan")
    first = json.loads(run([
        sys.executable, str(setup), "apply", "--project-root", str(project),
        "--json",
    ], env))
    if first.get("next_entry") != "requirement":
        raise SmokeFailure("fresh setup did not route to Requirement Flow")
    host = "claude" if (team_root / ".claude-plugin" / "plugin.json").is_file() \
        else "codex" if (team_root / ".codex-plugin" / "plugin.json").is_file() \
        else ""
    if not host:
        raise SmokeFailure("installed package has no host manifest")
    generator = team_root / "scripts" / f"generate_{host}_project.py"
    if not generator.is_file():
        raise SmokeFailure(f"installed package is missing {generator.name}")
    generator_args = [
        sys.executable, str(generator), "apply", "--project-root", str(project),
        "--seed-user-files", "--scope", "all",
    ]
    run(generator_args, env)
    run([
        sys.executable, str(generator), "check", "--project-root", str(project),
        "--scope", "all",
    ], env)
    sentinel = project / "workspace" / "user-authored.md"
    sentinel.write_text("# User-owned package refresh sentinel\n", encoding="utf-8")
    config_path = project / "workspace/config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["consumer_refresh_fixture"] = "preserve"
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    app_path = project / "workspace/docs/.obsidian/app.json"
    app = json.loads(app_path.read_text(encoding="utf-8"))
    app["alwaysUpdateLinks"] = False
    app["consumer_knob"] = "preserve"
    app_path.write_text(json.dumps(app, indent=2) + "\n", encoding="utf-8")
    drift = json.loads(run([
        sys.executable, str(setup), "inspect", "--project-root", str(project),
        "--json",
    ], env))
    if "workspace/docs/.obsidian/app.json" not in {
        item.get("path") for item in drift.get("operations", [])
    }:
        raise SmokeFailure("project refresh inspection omitted managed payload drift")
    rejected = subprocess.run([
        sys.executable, str(setup), "check", "--project-root", str(project),
        "--json",
    ], capture_output=True, text=True, env=env, check=False, timeout=60)
    if rejected.returncode != 1:
        raise SmokeFailure("project refresh check accepted managed payload drift")
    run([
        sys.executable, str(setup), "apply", "--project-root", str(project),
        "--json",
    ], env)
    run(generator_args, env)
    if sentinel.read_text(encoding="utf-8") \
            != "# User-owned package refresh sentinel\n":
        raise SmokeFailure("setup refresh changed a user-owned project file")
    if "consumer_refresh_fixture" in json.loads(
        config_path.read_text(encoding="utf-8")
    ):
        raise SmokeFailure("setup refresh retained an unknown config field")
    refreshed_app = json.loads(app_path.read_text(encoding="utf-8"))
    if refreshed_app.get("alwaysUpdateLinks") is not True \
            or refreshed_app.get("consumer_knob") != "preserve":
        raise SmokeFailure("setup refresh did not isolate managed Obsidian keys")
    run([
        sys.executable, str(setup), "check", "--project-root", str(project),
        "--json",
    ], env)
    run([
        sys.executable, str(check), "check", "--project-root", str(project),
        "--json",
    ], env)
    portable = project / ".github" / "agentrof" / "vault-gate.pyz"
    run([sys.executable, str(portable), "check", "--project-root", str(project), "--json"], env)
    routed = subprocess.run([
        sys.executable, str(route), "--project-root", str(project),
        "--json",
    ], capture_output=True, text=True, env=env, check=False, timeout=60)
    payload = json.loads(routed.stdout)
    if routed.returncode != 1 or payload.get("next_entry") != "requirement":
        raise SmokeFailure("document router did not remain at Requirement Flow")


def codex_skill_names(env: dict[str, str], project: Path) -> set[str]:
    process = subprocess.Popen(
        ["codex", "app-server"], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    assert process.stdin is not None and process.stdout is not None
    responses: queue.Queue[str] = queue.Queue()
    threading.Thread(
        target=lambda: [responses.put(line) for line in process.stdout],
        daemon=True,
    ).start()

    def send(value: dict) -> None:
        process.stdin.write(json.dumps(value) + "\n")
        process.stdin.flush()

    def receive(request_id: int) -> dict:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                value = json.loads(responses.get(timeout=1))
            except (queue.Empty, json.JSONDecodeError):
                continue
            if value.get("id") == request_id:
                return value
        raise SmokeFailure(f"Codex app-server omitted response {request_id}")

    try:
        send({"method": "initialize", "id": 1, "params": {
            "clientInfo": {"name": "marketplace_smoke", "title": "Smoke", "version": "1"},
        }})
        if receive(1).get("error"):
            raise SmokeFailure("Codex app-server initialization failed")
        send({"method": "initialized", "params": {}})
        send({"method": "skills/list", "id": 2, "params": {
            "cwds": [str(project)], "forceReload": True,
        }})
        response = receive(2)
        for item in response.get("result", {}).get("data", []):
            if item.get("cwd") == str(project):
                return {skill.get("name", "") for skill in item.get("skills", [])}
        raise SmokeFailure("Codex skills/list omitted the project")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def smoke_claude(source: str | Path) -> None:
    require_cli("claude")
    with tempfile.TemporaryDirectory(prefix="marketplace-claude-smoke.") as temporary:
        state = Path(temporary)
        env = {**os.environ, "CLAUDE_CONFIG_DIR": str(state / "claude")}
        project = state / "project"
        init_project(project, env)
        run(["claude", "plugin", "marketplace", "add", str(source)], env)
        run(["claude", "plugin", "install", f"{TEAM}@{MARKETPLACE}"], env)
        inventory = json.loads(run(["claude", "plugin", "list", "--json"], env))
        root = installed_root(inventory, "claude")
        exercise_package(root, project, env)
        run(["claude", "plugin", "update", f"{TEAM}@{MARKETPLACE}"], env)
        exercise_package(installed_root(json.loads(run(
            ["claude", "plugin", "list", "--json"], env)), "claude"), project, env)


def smoke_codex(root: Path, source: str | Path) -> None:
    require_cli("codex")
    with tempfile.TemporaryDirectory(prefix="marketplace-codex-smoke.") as temporary:
        state = Path(temporary)
        codex_home = state / "codex"
        codex_home.mkdir()
        env = {**os.environ, "CODEX_HOME": str(codex_home)}
        project = state / "project"
        init_project(project, env)
        run(["codex", "plugin", "marketplace", "add", str(source), "--json"], env)
        run(["codex", "plugin", "add", f"{TEAM}@{MARKETPLACE}", "--json"], env)
        inventory = json.loads(run(["codex", "plugin", "list", "--json"], env))
        team_root = installed_root(inventory, "codex")
        exercise_package(team_root, project, env)
        expected = {
            f"{TEAM}:{path.parent.name}"
            for path in (root / "plugins" / TEAM / "skill-content").glob("*/SKILL.md")
            if "exposure: entry" in path.read_text(encoding="utf-8")
        }
        names = codex_skill_names(env, project)
        if not expected <= names:
            raise SmokeFailure("Codex omitted entry skills: " + ", ".join(sorted(expected - names)))
        if isinstance(source, Path):
            # Local marketplaces are refreshed by reinstalling the plugin;
            # Codex reserves `marketplace upgrade` for Git-backed sources.
            run(["codex", "plugin", "add", f"{TEAM}@{MARKETPLACE}", "--json"], env)
        else:
            run(["codex", "plugin", "marketplace", "upgrade", MARKETPLACE, "--json"], env)
        exercise_package(installed_root(json.loads(run(
            ["codex", "plugin", "list", "--json"], env)), "codex"), project, env)


def checkout_marketplace(root: Path, target: Path) -> Path:
    shutil.copytree(root / "dist", target / "dist")
    shutil.copytree(root / ".claude-plugin", target / ".claude-plugin")
    shutil.copytree(root / ".agents", target / ".agents")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=("all", "claude", "codex"), default="all")
    parser.add_argument("--channel", choices=("checkout", "public"), default="checkout")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.channel == "public":
        if args.host in {"all", "claude"}:
            smoke_claude(PUBLIC_MARKETPLACES["claude"])
        if args.host in {"all", "codex"}:
            smoke_codex(root, PUBLIC_MARKETPLACES["codex"])
        return 0
    with tempfile.TemporaryDirectory(prefix="marketplace-checkout.") as temporary:
        source = checkout_marketplace(root, Path(temporary))
        if args.host in {"all", "claude"}:
            smoke_claude(source)
        if args.host in {"all", "codex"}:
            smoke_codex(root, source)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        raise SystemExit(f"plugin-smoke: {exc}") from exc
