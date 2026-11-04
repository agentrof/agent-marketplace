"""Identify reusable local proof without claiming unobserved dependency state."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import atomic_file

PROBE_SECONDS = 5
PROBE_BYTES = 65536
VOLATILE_ENV = {"_", "SHLVL", "PWD", "OLDPWD"}


class ProbeCleanupUnconfirmed(ValueError):
    def __init__(self, pid: int):
        self.pid = pid
        super().__init__(f"proof identity probe {pid} cleanup is unconfirmed; reconcile the blocked proof resource")


def _encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _git(root: Path, environment: dict, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", *arguments],
        cwd=root, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, timeout=PROBE_SECONDS, check=True).stdout


def _file(path: Path) -> tuple:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or path.resolve(strict=True) != path:
        raise ValueError("proof input must be a regular file without path aliases")
    generation = (before.st_dev, before.st_ino, before.st_mode, before.st_size,
                  before.st_mtime_ns, before.st_ctime_ns)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    after = path.lstat()
    if generation != (after.st_dev, after.st_ino, after.st_mode, after.st_size,
                      after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("proof input changed while being read")
    return generation, digest.hexdigest()


def _tracked(root: Path, environment: dict) -> dict:
    head = _git(root, environment, "rev-parse", "HEAD").decode().strip()
    if _git(root, environment, "status", "--porcelain", "-z"):
        raise ValueError("proof reuse needs a clean candidate")
    files = []
    for row in _git(root, environment, "ls-files", "--stage", "-z").split(b"\0"):
        if not row:
            continue
        metadata, name = row.split(b"\t", 1)
        mode, blob, stage = metadata.decode("ascii").split()
        if mode not in {"100644", "100755"} or stage != "0":
            raise ValueError("unsupported tracked proof input")
        path = root / os.fsdecode(name)
        files.append((os.fsdecode(name), mode, blob, _file(path)))
    if _git(root, environment, "rev-parse", "HEAD").decode().strip() != head:
        raise ValueError("proof candidate changed while being read")
    return {"head": head, "files": files}


def _key(common: Path) -> bytes:
    directory = atomic_file.real_directory(common, Path("agentrof/runtime"))
    path = directory / "proof-key"
    if not path.exists():
        descriptor, temporary = tempfile.mkstemp(prefix="proof-key.", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(os.urandom(32))
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                pass
        finally:
            os.unlink(temporary)
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 \
            or os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError("unsafe proof identity key")
    key = path.read_bytes()
    if len(key) != 32:
        raise ValueError("invalid proof identity key")
    return key


def _stop(process: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=PROBE_SECONDS, check=False)
    process.wait(timeout=PROBE_SECONDS)


def _probe(root: Path, command: list[str], environment: dict) -> str:
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(command, cwd=root, env=environment, shell=False,
                                   stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                                   start_new_session=os.name == "posix")
        deadline = time.monotonic() + PROBE_SECONDS
        try:
            while process.poll() is None:
                if time.monotonic() >= deadline or os.fstat(output.fileno()).st_size > PROBE_BYTES:
                    raise ValueError("proof identity probe exceeded its budget")
                time.sleep(0.01)
            if process.returncode:
                raise ValueError("proof identity probe failed")
            if os.name == "posix":
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    pass
                else:
                    raise ValueError("proof identity probe left child processes")
            output.seek(0)
            content = output.read(PROBE_BYTES + 1)
            if len(content) > PROBE_BYTES or not content.strip():
                raise ValueError("proof identity probe returned no bounded identity")
            return hashlib.sha256(content).hexdigest()
        except BaseException:
            try:
                _stop(process)
            except BaseException as error:
                raise ProbeCleanupUnconfirmed(process.pid) from error
            raise


def snapshot(root: Path, commands: list[str], runner: str, scope: dict,
             config: dict, runtime_files: list[Path]) -> dict | None:
    """Bind local inputs and the project's opaque dependency/environment probe.

    The caller owns the proof lock. Unknown identity disables reuse. File
    generations conservatively invalidate even a change restored to old bytes.
    """
    try:
        policy = config.get("proof_reuse")
        if not isinstance(policy, dict):
            return None
        command = policy.get("identity_command")
        age = policy.get("max_age_seconds", 86400)
        if not isinstance(command, list) or not command \
                or any(not isinstance(item, str) or not item.strip() or "\0" in item for item in command) \
                or type(age) is not int or not 0 < age <= 86400:
            return None
        root = Path(root).resolve(strict=True)
        environment = dict(os.environ)
        executable = Path(command[0])
        if os.path.dirname(command[0]):
            executable = executable if executable.is_absolute() else root / executable
        else:
            found = shutil.which(command[0], path=environment.get("PATH", os.defpath))
            if not found:
                return None
            executable = Path(found)
        executable = executable.resolve(strict=True)
        if executable.suffix.lower() in {".bat", ".cmd"}:
            return None
        common = Path(os.fsdecode(_git(root, environment, "rev-parse", "--path-format=absolute",
                                      "--git-common-dir")).strip()).resolve(strict=True)
        key = _key(common)
        before = _tracked(root, environment)
        runtime_paths = sorted({Path(path).absolute() for path in runtime_files}
                               | {Path(__file__).absolute(), Path(sys.executable).resolve(), executable})
        runtime = [(str(path), _file(path)) for path in runtime_paths]
        probe = _probe(root, [str(executable), *command[1:]], environment)
        if before != _tracked(root, environment) \
                or runtime != [(str(path), _file(path)) for path in runtime_paths]:
            return None
        variables = {name: value for name, value in environment.items() if name not in VOLATILE_ENV}
        inputs = {"root": str(root), "tracked": before, "commands": commands, "runner": runner,
                  "scope": scope, "config": config, "runtime_files": runtime, "probe": probe,
                  "platform": (platform.system(), platform.release(), platform.machine(), sys.version),
                  "python": str(Path(sys.executable).resolve()),
                  "environment": hmac.new(key, _encoded(variables), hashlib.sha256).hexdigest()}
        return {"schema_version": 1, "sha256": hashlib.sha256(_encoded(inputs)).hexdigest()}
    except ProbeCleanupUnconfirmed as error:
        blocked = common / "agentrof/runtime/proof.blocked.json"
        atomic_file.replace_text(blocked, json.dumps({
            "pid": error.pid, "at": datetime.now(timezone.utc).isoformat(),
            "reason": "identity_probe_cleanup_unconfirmed"}) + "\n")
        raise
    except (OSError, ValueError, TypeError, RuntimeError, subprocess.SubprocessError):
        return None


def seal(record: dict) -> dict:
    """Detect record corruption. A local digest does not authenticate its author."""
    result = {key: value for key, value in record.items() if key != "proof_digest"}
    return {**result, "proof_digest": hashlib.sha256(_encoded(result)).hexdigest()}


def reusable(record: dict, inputs: dict | None, commands: list[str],
             max_age_seconds: int = 86400) -> bool:
    try:
        if not isinstance(record, dict) or not isinstance(inputs, dict) \
                or type(inputs.get("schema_version")) is not int or inputs["schema_version"] != 1 \
                or not isinstance(inputs.get("sha256"), str) or len(inputs["sha256"]) != 64 \
                or any(character not in "0123456789abcdef" for character in inputs["sha256"]) \
                or not isinstance(commands, list) or any(not isinstance(command, str) or not command for command in commands) \
                or record.get("proof_inputs") != inputs \
                or record.get("ok") is not True or record.get("tree_clean") is not True \
                or record.get("proof_state") != "observed" \
                or record.get("status", "complete") != "complete" \
                or type(max_age_seconds) is not int or not 0 < max_age_seconds <= 86400 \
                or record.get("proof_digest") != seal(record)["proof_digest"]:
            return False
        finished = datetime.fromisoformat(record["finished_at"].replace("Z", "+00:00"))
        if finished.tzinfo is None or not 0 <= time.time() - finished.timestamp() <= max_age_seconds:
            return False
        results = record["commands"]
        return isinstance(results, list) and len(results) == len(commands) \
            and (bool(commands) or record.get("selection") == "impact") \
            and all(isinstance(row, dict) and row.get("command") == command
                    and type(row.get("exit_code")) is int and row["exit_code"] == 0
                    and not row.get("timed_out") and not row.get("leftover_children")
                    for row, command in zip(results, commands))
    except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
        return False
