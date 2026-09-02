#!/usr/bin/env python3
"""Install the standalone team on real host CLIs and exercise its package."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

try:
    import build_distributions
except ModuleNotFoundError:  # Imported as tools.smoke_plugin_installs in tests.
    from tools import build_distributions


TEAM = "software-engineering-team"
MARKETPLACE = "agent-marketplace"
VERIFIER_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_MARKETPLACES = {
    "claude": "https://github.com/agentrof/agent-marketplace.git#stable",
    "codex": "agentrof/agent-marketplace@stable",
}
PUBLIC_REPOSITORY = "https://github.com/agentrof/agent-marketplace.git"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
CLAUDE_RUNTIME_CONTRACTS = ["in_use_pid_marker_v1"]
CLAUDE_IN_USE_ROOT = ".in_use"
CLAUDE_PID_RE = re.compile(r"^[1-9][0-9]*$")
CLAUDE_PID_MAX = (1 << 32) - 1
CLAUDE_MARKER_MAX_BYTES = 512
APPLICATION_RESOURCES = (
    "skill-content/experience-modeling/data/experience-schema.json",
)


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


def public_stable_sha(env: dict[str, str]) -> str:
    rows = run([
        "git", "ls-remote", "--heads", PUBLIC_REPOSITORY, "refs/heads/stable",
    ], env).splitlines()
    if len(rows) != 1:
        raise SmokeFailure("public stable ref is missing or ambiguous")
    fields = rows[0].split()
    if len(fields) != 2 or fields[1] != "refs/heads/stable" \
            or SHA_RE.fullmatch(fields[0]) is None:
        raise SmokeFailure("public stable ref response is invalid")
    return fields[0]


def require_public_stable(expected_sha: str, env: dict[str, str]) -> None:
    actual = public_stable_sha(env)
    if actual != expected_sha:
        raise SmokeFailure(
            f"public stable ref differs: expected {expected_sha}, got {actual}"
        )


def package_inventory(root: Path, host: str) -> dict[str, str]:
    """Return a closed regular-file/directory inventory without following links."""
    try:
        if not root.exists() or root.is_symlink() or not root.is_dir():
            raise SmokeFailure(f"{host} package root is not a real directory")
    except OSError as exc:
        raise SmokeFailure(f"{host} package root cannot be inspected") from exc
    inventory: dict[str, str] = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise SmokeFailure(f"{host} package tree cannot be inspected") from exc
        for entry in entries:
            relative = entry.relative_to(root).as_posix()
            try:
                mode = entry.lstat().st_mode
            except OSError as exc:
                raise SmokeFailure(
                    f"{host} package entry cannot be inspected: {relative}"
                ) from exc
            if stat.S_ISLNK(mode):
                raise SmokeFailure(f"{host} package tree contains a link: {relative}")
            if stat.S_ISDIR(mode):
                inventory[relative] = "directory"
                pending.append(entry)
            elif stat.S_ISREG(mode):
                inventory[relative] = "file"
            else:
                raise SmokeFailure(
                    f"{host} package tree contains a special entry: {relative}"
                )
    return inventory


def claude_runtime_inventory_entries(
    installed: Path,
    inventory: dict[str, str],
    provenance: dict,
    host: str,
) -> set[str]:
    """Validate and return only Claude's attested host-runtime entries."""
    runtime_inventory = {
        relative: kind for relative, kind in inventory.items()
        if relative == CLAUDE_IN_USE_ROOT
        or relative.startswith(f"{CLAUDE_IN_USE_ROOT}/")
    }
    if not runtime_inventory:
        return set()
    if host != "claude" \
            or provenance.get("runtime_contracts") != CLAUDE_RUNTIME_CONTRACTS:
        return set()
    if runtime_inventory.get(CLAUDE_IN_USE_ROOT) != "directory":
        raise SmokeFailure("claude install has invalid .in_use runtime directory")

    def closed_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        payload: dict[str, object] = {}
        for key, value in pairs:
            if key in payload:
                raise ValueError(f"duplicate marker key: {key}")
            payload[key] = value
        return payload

    for relative, kind in runtime_inventory.items():
        if relative == CLAUDE_IN_USE_ROOT:
            continue
        marker_name = relative.removeprefix(f"{CLAUDE_IN_USE_ROOT}/")
        if "/" in marker_name or kind != "file" \
                or CLAUDE_PID_RE.fullmatch(marker_name) is None:
            raise SmokeFailure("claude install has invalid .in_use runtime entry")
        marker = installed / relative
        try:
            marker_stat = marker.lstat()
            if not stat.S_ISREG(marker_stat.st_mode) \
                    or marker_stat.st_mode & 0o111 \
                    or not 0 < marker_stat.st_size <= CLAUDE_MARKER_MAX_BYTES:
                raise SmokeFailure("claude install has invalid .in_use runtime marker")
            marker_bytes = marker.read_bytes()
            if len(marker_bytes) != marker_stat.st_size:
                raise SmokeFailure("claude install has unstable .in_use runtime marker")
            payload = json.loads(
                marker_bytes.decode("utf-8"), object_pairs_hook=closed_object,
            )
        except SmokeFailure:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise SmokeFailure(
                "claude install has invalid .in_use runtime marker"
            ) from exc
        if not isinstance(payload, dict) \
                or set(payload) != {"pid", "procStart"}:
            raise SmokeFailure("claude install has invalid .in_use marker schema")
        pid = payload["pid"]
        proc_start = payload["procStart"]
        if type(pid) is not int or not 0 < pid <= CLAUDE_PID_MAX \
                or str(pid) != marker_name or not isinstance(proc_start, str) \
                or not proc_start or not proc_start.isprintable():
            raise SmokeFailure("claude install has invalid .in_use marker values")
        try:
            proc_start_size = len(proc_start.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise SmokeFailure(
                "claude install has invalid .in_use marker values"
            ) from exc
        if proc_start_size > 256:
            raise SmokeFailure("claude install has invalid .in_use marker values")
    return set(runtime_inventory)


def verify_installed_package(
    installed: Path, expected: Path, host: str,
) -> None:
    provenance_name = build_distributions.PROVENANCE
    try:
        actual_provenance = json.loads(
            (installed / provenance_name).read_text(encoding="utf-8")
        )
        expected_provenance = json.loads(
            (expected / provenance_name).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise SmokeFailure(f"{host} install has invalid package provenance") from exc
    if actual_provenance != expected_provenance:
        raise SmokeFailure(f"{host} installed package provenance differs from candidate")
    if actual_provenance.get("host") != host \
            or actual_provenance.get("component") != TEAM:
        raise SmokeFailure(f"{host} installed package provenance has wrong identity")
    actual_inventory = package_inventory(installed, host)
    expected_inventory = package_inventory(expected, host)
    normalized_inventory = dict(actual_inventory)
    for relative in claude_runtime_inventory_entries(
        installed, actual_inventory, actual_provenance, host,
    ):
        normalized_inventory.pop(relative)
    if normalized_inventory != expected_inventory:
        raise SmokeFailure(f"{host} installed package tree differs from candidate")
    files = actual_provenance.get("files")
    if not isinstance(files, dict) or not files:
        raise SmokeFailure(f"{host} installed package provenance has no file hashes")
    executables = actual_provenance.get("executables")
    if actual_provenance.get("schema_version") != 3 \
            or not isinstance(executables, list) \
            or any(not isinstance(path, str) for path in executables) \
            or len(executables) != len(set(executables)):
        raise SmokeFailure(f"{host} installed package provenance has invalid modes")
    executable_paths = set(executables)
    if not executable_paths <= set(files):
        raise SmokeFailure(f"{host} installed package provenance has unsafe modes")
    expected_attested_files = {
        relative for relative, kind in expected_inventory.items()
        if kind == "file" and relative != provenance_name
    }
    if set(files) != expected_attested_files:
        raise SmokeFailure(f"{host} package provenance does not cover its full tree")
    for relative, expected_digest in files.items():
        path = Path(relative) if isinstance(relative, str) else Path("..")
        if path.is_absolute() or ".." in path.parts \
                or not isinstance(expected_digest, str):
            raise SmokeFailure(f"{host} installed package provenance is unsafe")
        installed_path = installed / path
        if not installed_path.is_file() or installed_path.is_symlink():
            raise SmokeFailure(f"{host} installed package is missing {relative}")
        actual_digest = hashlib.sha256(installed_path.read_bytes()).hexdigest()
        if actual_digest != expected_digest:
            raise SmokeFailure(f"{host} installed package hash differs for {relative}")
        expected_path = expected / path
        expected_executable = build_distributions.is_executable(expected_path)
        actual_executable = build_distributions.is_executable(installed_path)
        attested_executable = relative in executable_paths
        if expected_executable != attested_executable \
                or actual_executable != attested_executable:
            raise SmokeFailure(
                f"{host} installed package mode differs for {relative}"
            )


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


def exercise_application_resources(
    team_root: Path, env: dict[str, str]
) -> None:
    application_check = team_root / "scripts" / "experience_application_check.py"
    for path in (application_check, *(team_root / item for item in APPLICATION_RESOURCES)):
        if not path.is_file():
            raise SmokeFailure(
                f"installed package is missing {path.relative_to(team_root)}"
            )
    application_self_check = json.loads(run([
        sys.executable, str(application_check), "self-check", "--json",
    ], dict(env, PYTHONDONTWRITEBYTECODE="1")))
    if application_self_check.get("ok") is not True:
        raise SmokeFailure(
            "installed opaque prototype snapshot checker self-check failed: "
            + json.dumps(application_self_check.get("findings", []))
        )


def exercise_package(team_root: Path, project: Path, env: dict[str, str]) -> None:
    env = dict(env, PYTHONDONTWRITEBYTECODE="1")
    setup = team_root / "scripts" / "setup_project.py"
    check = team_root / "scripts" / "setup_check.py"
    route = team_root / "scripts" / "requirement_route.py"
    delivery_compile = team_root / "scripts" / "delivery_compile.py"
    delivery_git = team_root / "scripts" / "delivery_git.py"
    product_chain_scripts = (
        "stage_package.py", "ba_compile.py", "landscape_check.py",
        "design_system_compile.py", "experience_compile.py",
        "experience_application_check.py",
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
    exercise_application_resources(team_root, env)
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


def smoke_claude(source: str | Path, expected: Path | None = None) -> None:
    require_cli("claude")
    with tempfile.TemporaryDirectory(prefix="marketplace-claude-smoke.") as temporary:
        state = Path(temporary)
        env = {
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(state / "claude"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        project = state / "project"
        init_project(project, env)
        run(["claude", "plugin", "marketplace", "add", str(source)], env)
        run(["claude", "plugin", "install", f"{TEAM}@{MARKETPLACE}"], env)
        inventory = json.loads(run(["claude", "plugin", "list", "--json"], env))
        root = installed_root(inventory, "claude")
        if expected is not None:
            verify_installed_package(root, expected, "claude")
        exercise_package(root, project, env)
        run(["claude", "plugin", "update", f"{TEAM}@{MARKETPLACE}"], env)
        updated = installed_root(json.loads(run(
            ["claude", "plugin", "list", "--json"], env)), "claude")
        if expected is not None:
            verify_installed_package(updated, expected, "claude")
        exercise_package(updated, project, env)


def smoke_codex(
    root: Path, source: str | Path, expected: Path | None = None,
) -> None:
    require_cli("codex")
    with tempfile.TemporaryDirectory(prefix="marketplace-codex-smoke.") as temporary:
        state = Path(temporary)
        codex_home = state / "codex"
        codex_home.mkdir()
        env = {
            **os.environ,
            "CODEX_HOME": str(codex_home),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        project = state / "project"
        init_project(project, env)
        run(["codex", "plugin", "marketplace", "add", str(source), "--json"], env)
        run(["codex", "plugin", "add", f"{TEAM}@{MARKETPLACE}", "--json"], env)
        inventory = json.loads(run(["codex", "plugin", "list", "--json"], env))
        team_root = installed_root(inventory, "codex")
        if expected is not None:
            verify_installed_package(team_root, expected, "codex")
        exercise_package(team_root, project, env)
        expected_skills = {
            f"{TEAM}:{path.parent.name}"
            for path in (root / "plugins" / TEAM / "skill-content").glob("*/SKILL.md")
            if "exposure: entry" in path.read_text(encoding="utf-8")
        }
        names = codex_skill_names(env, project)
        if not expected_skills <= names:
            raise SmokeFailure(
                "Codex omitted entry skills: "
                + ", ".join(sorted(expected_skills - names))
            )
        if isinstance(source, Path):
            # Local marketplaces are refreshed by reinstalling the plugin;
            # Codex reserves `marketplace upgrade` for Git-backed sources.
            run(["codex", "plugin", "add", f"{TEAM}@{MARKETPLACE}", "--json"], env)
        else:
            run(["codex", "plugin", "marketplace", "upgrade", MARKETPLACE, "--json"], env)
        updated = installed_root(json.loads(run(
            ["codex", "plugin", "list", "--json"], env)), "codex")
        if expected is not None:
            verify_installed_package(updated, expected, "codex")
        exercise_package(updated, project, env)


def smoke_public(
    root: Path,
    selected: set[str],
    expected_sha: str,
    *,
    attempts: int = 3,
    retry_delay: float = 5,
) -> None:
    if SHA_RE.fullmatch(expected_sha) is None:
        raise SmokeFailure("public smoke requires an exact lowercase 40-hex SHA")
    if attempts < 1 or attempts > 5:
        raise SmokeFailure("public smoke attempts must be between 1 and 5")
    # Adapter discovery is verifier policy. A historical candidate may supply
    # package bytes, but its Python adapters must not define or alter the gate.
    adapters = set(build_distributions.load_adapters(VERIFIER_ROOT))
    if set(PUBLIC_MARKETPLACES) != adapters:
        raise SmokeFailure("public marketplace sources differ from registered hosts")
    last_error: SmokeFailure | None = None
    for attempt in range(1, attempts + 1):
        try:
            for host in sorted(selected):
                require_public_stable(expected_sha, os.environ.copy())
                expected = root / "dist" / host / TEAM
                if host == "claude":
                    smoke_claude(PUBLIC_MARKETPLACES[host], expected)
                elif host == "codex":
                    smoke_codex(root, PUBLIC_MARKETPLACES[host], expected)
                else:
                    raise SmokeFailure(
                        f"{host} public marketplace smoke is not implemented"
                    )
                require_public_stable(expected_sha, os.environ.copy())
            return
        except SmokeFailure as exc:
            last_error = exc
            if attempt == attempts:
                break
            time.sleep(retry_delay)
    assert last_error is not None
    raise SmokeFailure(
        f"public stable smoke failed after {attempts} fresh attempt(s): {last_error}"
    )


def checkout_marketplace(root: Path, target: Path) -> Path:
    shutil.copytree(root / "dist", target / "dist")
    shutil.copytree(root / ".claude-plugin", target / ".claude-plugin")
    shutil.copytree(root / ".agents", target / ".agents")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    root_default = VERIFIER_ROOT
    host_choices = ("all", *build_distributions.load_adapters(root_default))
    parser.add_argument("--host", choices=host_choices, default="all")
    parser.add_argument("--channel", choices=("checkout", "public"), default="checkout")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--expected-sha", default="")
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=5)
    args = parser.parse_args()
    root = args.root.resolve()
    adapters = build_distributions.load_adapters(VERIFIER_ROOT)
    selected = set(adapters) if args.host == "all" else {args.host}
    if args.channel == "public":
        smoke_public(
            root,
            selected,
            args.expected_sha,
            attempts=args.attempts,
            retry_delay=args.retry_delay,
        )
        return 0
    if args.expected_sha:
        raise SmokeFailure("--expected-sha is only valid for the public channel")
    with tempfile.TemporaryDirectory(prefix="marketplace-checkout.") as temporary:
        source = checkout_marketplace(root, Path(temporary))
        for host in sorted(selected):
            if host == "claude":
                smoke_claude(source)
            elif host == "codex":
                smoke_codex(root, source)
            else:
                raise SmokeFailure(f"{host} native marketplace smoke is not implemented")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        raise SystemExit(f"plugin-smoke: {exc}") from exc
