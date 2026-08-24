#!/usr/bin/env python3
"""Maintain one installed OpenCode Agent Marketplace projection."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlparse
from urllib.request import url2pathname
from platform import machine
from pathlib import Path


OPENCODE_COMMAND_TIMEOUT = 45.0
OPENCODE_CONFIG_TIMEOUT = 80.0
OPENCODE_CONFIG_ATTEMPTS = 2
PROCESS_TERMINATION_TIMEOUT = 5.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def root() -> Path:
    return Path(__file__).resolve().parent


def project_root(private: Path) -> Path:
    return private.parents[2]


def public_path(project: Path, relative: object) -> Path:
    """Resolve an installation-owned public path without allowing an escape."""
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise RuntimeError("unsafe_path")
    root = (project / ".opencode").resolve()
    target = (root / relative).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("unsafe_path") from exc
    return target


def installation(private: Path) -> dict:
    path = private / "installation.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("unsupported_installation_schema") from exc
    if value.get("schema_version") != 1:
        raise RuntimeError("unsupported_installation_schema")
    return value


def package_manifest(package: Path) -> dict:
    """Verify one immutable, projected package before lifecycle deletion."""
    path = package / ".agent-marketplace-package.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("manifest_hash_mismatch") from exc
    files = value.get("files")
    if value.get("component") != "software-engineering-team" \
            or value.get("host") != "opencode" or not isinstance(files, dict):
        raise RuntimeError("manifest_hash_mismatch")
    allowed = {".agent-marketplace-package.json", *files}
    actual: set[str] = set()
    for item in package.rglob("*"):
        if item.is_symlink():
            raise RuntimeError("symlink_or_reparse")
        if not item.is_file():
            continue
        relative = item.relative_to(package).as_posix()
        if relative.endswith((".pyc", ".pyo")) or "__pycache__" in item.parts:
            raise RuntimeError("projection_drift")
        actual.add(relative)
    if actual != allowed:
        raise RuntimeError("projection_drift")
    for relative, expected in files.items():
        target = package / relative
        if not isinstance(relative, str) or not isinstance(expected, str) \
                or not target.is_file() or sha256(target) != expected:
            raise RuntimeError("manifest_hash_mismatch")
    return value


def executable_identity(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError("runtime_binding_drift")
    return {"path": str(path.resolve()), "sha256": sha256(path)}


def plugin_path(value: object) -> Path:
    if not isinstance(value, str) or not value:
        raise RuntimeError("unsupported_plugin_set")
    parsed = urlparse(value)
    if parsed.scheme not in {"", "file"}:
        raise RuntimeError("unsupported_plugin_set")
    candidate = Path(url2pathname(parsed.path) if parsed.scheme else value)
    if not candidate.is_absolute():
        raise RuntimeError("unsupported_plugin_set")
    return candidate.resolve()


def terminate_process_tree(process: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                check=False,
                timeout=PROCESS_TERMINATION_TIMEOUT,
            )
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.communicate(timeout=PROCESS_TERMINATION_TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.communicate(timeout=PROCESS_TERMINATION_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            pass


def run_opencode(
    argv: list[str], project: Path, *, stdout: object | None = None,
    timeout: float = OPENCODE_COMMAND_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    options: dict[str, object] = {
        "cwd": project,
        "stdout": subprocess.PIPE if stdout is None else stdout,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    try:
        process = subprocess.Popen(argv, **options)
    except OSError as exc:
        raise RuntimeError("runtime_unbound") from exc
    try:
        output, error = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        terminate_process_tree(process)
        raise RuntimeError("runtime_unbound") from exc
    return subprocess.CompletedProcess(argv, process.returncode, output, error)


def effective_config(executable: str, project: Path) -> tuple[str, list[Path]]:
    config: dict | None = None
    failure: Exception | None = None
    for _ in range(OPENCODE_CONFIG_ATTEMPTS):
        temporary: str | None = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix="agent-marketplace-opencode-config.")
            with os.fdopen(descriptor, "wb") as output:
                result = run_opencode(
                    [executable, "debug", "config"], project, stdout=output,
                    timeout=OPENCODE_CONFIG_TIMEOUT,
                )
            if result.returncode:
                raise RuntimeError("runtime_unbound")
            candidate = json.loads(Path(temporary).read_text(encoding="utf-8"))
            if not isinstance(candidate, dict):
                raise RuntimeError("hook_contract_incompatible")
            config = candidate
            break
        except RuntimeError as exc:
            if str(exc) != "runtime_unbound":
                raise
            failure = exc
            continue
        except (OSError, json.JSONDecodeError) as exc:
            failure = exc
            continue
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
    if config is None:
        if isinstance(failure, (OSError, json.JSONDecodeError)):
            raise RuntimeError("hook_contract_incompatible") from failure
        raise RuntimeError("runtime_unbound")
    plugins = config.get("plugin", [])
    if not isinstance(plugins, list):
        raise RuntimeError("unsupported_plugin_set")
    resolved = [plugin_path(value) for value in plugins]
    expected = (project / ".opencode" / "plugins" /
                "agent-marketplace-software-engineering-team.js").resolve()
    if resolved != [expected]:
        raise RuntimeError("unsupported_plugin_set")
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest(), resolved


@contextlib.contextmanager
def maintenance_guard(project: Path, timeout_seconds: float = 3.0):
    """Use the same bounded, process-held guard as setup and the projector."""
    runtime = project / ".agentrof" / "agent-marketplace" / ".runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    path = runtime / "setup-apply.guard"
    handle = path.open("a+b")
    acquired = False
    deadline = time.monotonic() + timeout_seconds
    try:
        while time.monotonic() < deadline:
            try:
                if os.name == "nt":
                    msvcrt = __import__("msvcrt")
                    if path.stat().st_size == 0:
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl = __import__("fcntl")
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                time.sleep(0.05)
        if not acquired:
            raise RuntimeError("maintenance_busy")
        yield
    finally:
        if acquired:
            if os.name == "nt":
                msvcrt = __import__("msvcrt")
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl = __import__("fcntl")
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def emit(action: str, ok: bool, code: str = "ok", **extra: object) -> None:
    payload = {"schema_version": 1, "action": action, "ok": ok, "code": code}
    payload.update(extra)
    print(json.dumps(payload, indent=2, sort_keys=True))


def check(private: Path) -> list[str]:
    value = installation(private)
    project = project_root(private)
    findings = []
    active_key = str(value.get("active_build_key", ""))
    expected_manifest = package_root(private, active_key) / ".agent-marketplace-package.json"
    package_path = value.get("package_manifest_path")
    if package_path != str(expected_manifest.relative_to(private)).replace("\\", "/") \
            or not expected_manifest.is_file() \
            or sha256(expected_manifest) != value.get("package_manifest_sha256"):
        findings.append("manifest_hash_mismatch")
    active = private / "packages" / active_key
    if not active.is_dir():
        findings.append("projection_drift")
    else:
        try:
            package_manifest(active / "software-engineering-team")
        except RuntimeError as exc:
            findings.append(str(exc))
    for relative, item in value.get("public_owned_files", {}).items():
        try:
            path = public_path(project, relative)
        except RuntimeError:
            findings.append("unsafe_path")
            continue
        if not isinstance(item, dict) or not path.is_file() or sha256(path) != item.get("sha256"):
            findings.append(f"projection_drift:{relative}")
    bindings = value.get("runtime_bindings", [])
    if bindings:
        if not isinstance(bindings, list) or len(bindings) != 1:
            findings.append("runtime_binding_drift")
        else:
            binding = bindings[0]
            try:
                open_code = executable_identity(Path(binding["opencode_path"]))
                python = executable_identity(Path(binding["python_path"]))
                if open_code["sha256"] != binding.get("opencode_sha256") \
                        or python["sha256"] != binding.get("python_sha256") \
                        or python["path"] != str(Path(sys.executable).resolve()):
                    raise RuntimeError("runtime_binding_drift")
                result = run_opencode([open_code["path"], "--version"], project)
                version = (result.stdout or result.stderr).strip().removeprefix("v")
                if result.returncode or version != binding.get("opencode_version") \
                        or version not in value.get("tested_opencode_versions", []):
                    raise RuntimeError("runtime_binding_drift")
                fingerprint, _plugins = effective_config(open_code["path"], project)
                if fingerprint != binding.get("config_fingerprint"):
                    raise RuntimeError("runtime_binding_drift")
            except (KeyError, RuntimeError, OSError):
                findings.append("runtime_binding_drift")
    return findings


def bind_runtime(private: Path, executable: str | None) -> None:
    value = installation(private)
    path = executable or shutil.which("opencode")
    if not path:
        raise RuntimeError("runtime_unbound")
    result = run_opencode([path, "--version"], project_root(private))
    if result.returncode:
        raise RuntimeError("runtime_unbound")
    version = (result.stdout or result.stderr).strip().removeprefix("v")
    if version not in value.get("tested_opencode_versions", []):
        raise RuntimeError("unsupported_opencode_version")
    open_code = executable_identity(Path(path))
    python = executable_identity(Path(sys.executable))
    config_fingerprint, plugins = effective_config(open_code["path"], project_root(private))
    binding = {
        "opencode_path": open_code["path"],
        "opencode_sha256": open_code["sha256"],
        "opencode_version": version,
        "python_path": python["path"],
        "python_sha256": python["sha256"],
        "python_version": ".".join(map(str, sys.version_info[:3])),
        "platform": sys.platform,
        "architecture": machine(),
        "config_fingerprint": config_fingerprint,
        "effective_plugins": [str(item) for item in plugins],
    }
    value["runtime_bindings"] = [binding]
    atomic(private / "installation.json", json.dumps(value, indent=2, sort_keys=True).encode() + b"\n")


def package_root(private: Path, key: str) -> Path:
    return private / "packages" / key / "software-engineering-team"


def prune(private: Path) -> None:
    value = installation(private)
    keep = {str(value.get("active_build_key", "")), *map(str, value.get("retained_builds", []))}
    packages = private / "packages"
    if packages.is_dir():
        for child in packages.iterdir():
            if child.name not in keep:
                if not child.is_dir() or not package_root(private, child.name).is_dir():
                    raise RuntimeError("unmanaged_collision")
                package_manifest(package_root(private, child.name))
                shutil.rmtree(child)


def uninstall(private: Path) -> None:
    value = installation(private)
    project = project_root(private)
    modified = []
    for relative, item in value.get("public_owned_files", {}).items():
        path = public_path(project, relative)
        if path.exists() and (not isinstance(item, dict) or not path.is_file()
                              or sha256(path) != item.get("sha256")):
            modified.append(relative)
    if modified:
        raise RuntimeError("owned_file_modified:" + ",".join(modified))
    packages = private / "packages"
    if packages.is_dir():
        for child in packages.iterdir():
            if not child.is_dir() or not package_root(private, child.name).is_dir():
                raise RuntimeError("unmanaged_collision")
            package_manifest(package_root(private, child.name))
    allowed_root = {"manage.py", "installation.json", "packages", "runtime"}
    unknown_root = [item.name for item in private.iterdir() if item.name not in allowed_root]
    if unknown_root:
        raise RuntimeError("unmanaged_collision:" + ",".join(sorted(unknown_root)))
    runtime = private / "runtime"
    if runtime.exists():
        maintenance = runtime / "maintenance.json"
        transactions = runtime / "transactions"
        unknown_runtime = [item.name for item in runtime.iterdir()
                           if item.name not in {"maintenance.json", "transactions"}]
        if unknown_runtime or maintenance.exists() \
                or (transactions.exists() and any(transactions.iterdir())):
            raise RuntimeError("maintenance_busy")
    for relative in value.get("public_owned_files", {}):
        path = public_path(project, relative)
        path.unlink(missing_ok=True)
        for parent in path.parents:
            if parent == project / ".opencode":
                break
            try:
                parent.rmdir()
            except OSError:
                break
    if packages.exists():
        shutil.rmtree(packages)
    if runtime.exists():
        transactions = runtime / "transactions"
        if transactions.exists():
            transactions.rmdir()
        runtime.rmdir()
    (private / "manage.py").unlink(missing_ok=True)
    (private / "installation.json").unlink(missing_ok=True)
    private.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "bind-runtime", "prune", "uninstall"))
    parser.add_argument("--clients-stopped", action="store_true")
    parser.add_argument("--opencode")
    args = parser.parse_args()
    private = root()
    try:
        if args.command == "check":
            findings = check(private)
            code = "ok" if not findings else str(findings[0]).split(":", 1)[0]
            emit(args.command, not findings, code, findings=findings)
            return 0 if not findings else 1
        if args.command in {"prune", "uninstall"} and not args.clients_stopped:
            raise RuntimeError("clients_stopped_required")
        project = project_root(private)
        if args.command == "bind-runtime":
            with maintenance_guard(project):
                bind_runtime(private, args.opencode)
        elif args.command == "prune":
            with maintenance_guard(project):
                prune(private)
        else:
            with maintenance_guard(project):
                uninstall(private)
        emit(args.command, True)
        return 0
    except RuntimeError as exc:
        code = str(exc)
        emit(args.command, False, code)
        return 4 if code in {"runtime_unbound", "unsupported_opencode_version", "runtime_binding_drift"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
