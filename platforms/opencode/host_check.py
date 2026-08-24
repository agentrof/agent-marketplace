#!/usr/bin/env python3
"""Release-facing OpenCode Terminal compatibility gate.

The deterministic portion verifies the generated project projection and its
local lifecycle. A real-host proof is deliberately supplied as a separate
argv-only probe executable: it must drive the pinned OpenCode binary with a
deterministic fake provider and leave evidence for all hook/permission cases.
Without that probe this command fails closed; a content-only release is not a
valid OpenCode release.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEAM = "software-engineering-team"


class HostCheckError(RuntimeError):
    def __init__(self, code: str, detail: str | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def command_detail(result: subprocess.CompletedProcess[str], label: str) -> str:
    """Return bounded child output when a deterministic subcheck fails."""
    output = (result.stderr or result.stdout).strip()
    return f"{label}: {output[-2000:]}" if output else label


def policy_version() -> str:
    data = json.loads((ROOT / "tools/data/host-cli-versions.json").read_text(
        encoding="utf-8"
    ))
    version = data.get("opencode")
    if not isinstance(version, str):
        raise HostCheckError("unsupported_opencode_version")
    return version


def run(argv: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False, env=env)


def deterministic_projection_check() -> Path:
    package = ROOT / "dist" / "opencode" / TEAM
    provenance_path = package / ".agent-marketplace-package.json"
    try:
        provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HostCheckError("manifest_hash_mismatch") from exc
    versions = json.loads((ROOT / "versions.json").read_text(encoding="utf-8"))
    expected_package_version = (versions.get("plugins") or {}).get(TEAM)
    if provenance.get("host") != "opencode" or provenance.get("version") != expected_package_version:
        raise HostCheckError("projection_drift")
    plugin = package / "plugins" / "agent-marketplace-software-engineering-team.js"
    node = shutil.which("node")
    if node is None:
        raise HostCheckError("node_unavailable")
    checked = run([node, "--check", str(plugin)])
    if checked.returncode:
        raise HostCheckError("plugin_syntax_invalid")
    plugin_text = plugin.read_text(encoding="utf-8")
    hook = package / "scripts" / "vault_hook.py"
    required_hook_contract = (
        "spawnSync",
        "scripts/vault_hook.py",
        "'tool.execute.before'",
        "'tool.execute.after'",
        "pre_hook_denied",
        "post_hook_failed",
    )
    if not hook.is_file() or any(
        token not in plugin_text for token in required_hook_contract
    ):
        raise HostCheckError("hook_contract_incompatible")
    with tempfile.TemporaryDirectory(prefix="opencode-host-check.") as temporary:
        root = Path(temporary)
        source = root / "source"
        project = root / "project"
        shutil.copytree(package, source)
        project.mkdir()
        projector = source / "scripts" / "project_opencode.py"
        applied = run([
            sys.executable, "-B", str(projector), "apply",
            "--project-root", str(project), "--clients-stopped", "--development-source",
        ], env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        if applied.returncode:
            raise HostCheckError(
                "projection_drift", command_detail(applied, "projector_apply_failed")
            )
        manage = project / ".opencode" / "agentrof" / "agent-marketplace" / "manage.py"
        checked = run([sys.executable, "-B", str(manage), "check"])
        if checked.returncode:
            raise HostCheckError(
                "projection_drift", command_detail(checked, "manage_check_failed")
            )
    return package


def executable_version(executable: Path, expected: str) -> None:
    result = run([str(executable), "--version"])
    output = (result.stdout or result.stderr).strip().removeprefix("v")
    if result.returncode or output != expected:
        raise HostCheckError("unsupported_opencode_version")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opencode", type=Path)
    parser.add_argument(
        "--probe",
        type=Path,
        help="absolute deterministic real-host probe executable",
    )
    parser.add_argument(
        "--static-only",
        action="store_true",
        help="run only deterministic package/projection checks; never release evidence",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="require the real probe to complete an interactive terminal PTY/ConPTY check",
    )
    args = parser.parse_args()
    try:
        package = deterministic_projection_check()
        if args.static_only:
            print(json.dumps({"ok": True, "scope": "static", "package": str(package)}))
            return 0
        executable = args.opencode or (Path(shutil.which("opencode")) if shutil.which("opencode") else None)
        if executable is None or not executable.is_absolute():
            raise HostCheckError("runtime_unbound")
        executable_version(executable, policy_version())
        if args.probe is None or not args.probe.is_absolute() or not args.probe.is_file():
            raise HostCheckError("hook_contract_incompatible")
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        probe_argv = [str(args.probe)]
        if args.probe.suffix == ".py":
            probe_argv = [sys.executable, "-B", str(args.probe)]
        probe_args = [
            "--opencode", str(executable), "--package", str(package),
        ]
        if args.tui:
            probe_args.append("--tui")
        result = run(probe_argv + probe_args, env=environment)
        if result.returncode:
            if result.stdout:
                print(result.stdout, file=sys.stderr, end="")
            if result.stderr:
                print(result.stderr, file=sys.stderr, end="")
            raise HostCheckError("hook_contract_incompatible")
        print(json.dumps({"ok": True, "scope": "real-host", "package": str(package)}))
        return 0
    except HostCheckError as exc:
        payload = {"ok": False, "code": exc.code}
        if exc.detail:
            payload["detail"] = exc.detail
        print(json.dumps(payload))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
