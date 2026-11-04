"""Run an installed Graphify query and retain bounded, verifiable evidence.

The native CLI may write its query timestamp beside the graph. Run this in
the task owner's context. It never installs Graphify or rebuilds a graph.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import signal
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import atomic_file


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stop(process: subprocess.Popen) -> bool:
    try:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, timeout=10, check=False)
            if result.returncode and process.poll() is None:
                return False
        process.wait(timeout=10)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def run_query(root: Path, *, graph: Path, question: str, output_dir: Path,
              budget: int = 800, executable: str | None = None,
              seed: str | None = None, context: str | None = None,
              timeout_seconds: float = 10, max_output_bytes: int = 4096) -> dict:
    """Capture a query with at least 256 visible bytes for bounded status text."""
    if not question.strip() or len(question.encode("utf-8")) > 4096:
        raise ValueError("Graphify question must contain 1-4096 UTF-8 bytes")
    if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
        raise ValueError("Graphify budget must be a positive integer")
    if not isinstance(max_output_bytes, int) or max_output_bytes < 256:
        raise ValueError("Graphify output limit must be an integer of at least 256 bytes")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Graphify timeout must be positive and finite")
    root = Path(root).resolve(strict=True)
    graph = Path(graph)
    graph = (graph if graph.is_absolute() else root / graph).resolve(strict=True)
    if not graph.is_file() or graph.suffix != ".json":
        raise ValueError("Graphify requires an existing .json graph file")
    command = shutil.which(executable or "graphify")
    if command is None:
        raise ValueError("Graphify executable is unavailable; installation is a separate action")
    binary = Path(command).resolve(strict=True)
    if binary.suffix.lower() in (".bat", ".cmd"):
        raise ValueError("Graphify requires a native executable for argv-only invocation")
    destination = Path(output_dir)
    destination = (destination if destination.is_absolute() else root / destination).resolve()
    try:
        relative = destination.relative_to(root)
    except ValueError as exc:
        raise ValueError("Graphify evidence must be stored inside the project") from exc
    destination = atomic_file.real_directory(root, relative)
    destination = atomic_file.real_directory(destination, Path(uuid.uuid4().hex))
    stdout_path = destination / "stdout.txt"
    stderr_path = destination / "stderr.txt"
    argv = [str(binary), "query", question, "--graph", str(graph), "--budget", str(budget)]
    if seed is not None:
        argv.extend(["--seed", seed])
    if context is not None:
        argv.extend(["--context", context])
    before = {"graph": _digest(graph), "executable": _digest(binary)}
    environment = dict(os.environ, GRAPHIFY_NO_AUTO_REFRESH="1",
                       GRAPHIFY_QUERY_LOG_DISABLE="1", PYTHONDONTWRITEBYTECODE="1")
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    timed_out = False
    cleanup_confirmed = True
    unexpected_children = False
    spawn_error = None
    exit_code = None
    options = {"start_new_session": True} if os.name == "posix" else {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        try:
            process = subprocess.Popen(argv, cwd=root, env=environment,
                                       stdin=subprocess.DEVNULL, stdout=stdout,
                                       stderr=stderr, shell=False, **options)
        except OSError as exc:
            spawn_error = str(exc)
        else:
            try:
                process.wait(timeout=timeout_seconds)
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        pass
                    else:
                        unexpected_children = True
                        cleanup_confirmed = _stop(process)
            except subprocess.TimeoutExpired:
                timed_out = True
                cleanup_confirmed = _stop(process)
            except BaseException:
                _stop(process)
                raise
            exit_code = process.returncode
    with stdout_path.open("rb") as stdout:
        preview = stdout.read(max_output_bytes + 1)
    output_limited = len(preview) > max_output_bytes
    text = preview[:max_output_bytes].decode("utf-8", errors="replace")
    output_limited = output_limited or len(text.encode("utf-8")) > max_output_bytes
    native_truncated = "[!] TRUNCATED:" in text
    truncated = output_limited or native_truncated
    if output_limited:
        text = "Graphify output exceeds the visible byte limit. Narrow the seed or context; the complete raw output is retained."
    try:
        inputs_changed = before != {"graph": _digest(graph), "executable": _digest(binary)}
    except OSError:
        inputs_changed = True
    result = {
        "ok": (exit_code == 0 and not timed_out and not truncated and not inputs_changed
               and not unexpected_children and cleanup_confirmed and bool(text.strip())),
        "text": text, "truncated": truncated, "output_limited": output_limited,
        "native_truncated": native_truncated, "exit_code": exit_code,
        "ambiguous": exit_code == 3, "not_found": exit_code == 4,
        "timed_out": timed_out, "cleanup_confirmed": cleanup_confirmed,
        "unexpected_children": unexpected_children, "spawn_error": spawn_error,
        "inputs_changed": inputs_changed, "question": question, "argv": argv,
        "seed": seed, "context": context,
        "graph_path": str(graph), "graph_sha256": before["graph"],
        "executable": str(binary), "executable_sha256": before["executable"],
        "executable_hash_scope": "launcher_file",
        "stdout_path": str(stdout_path), "stdout_sha256": _digest(stdout_path),
        "stderr_path": str(stderr_path), "stderr_sha256": _digest(stderr_path),
        "stdout_bytes": stdout_path.stat().st_size, "stderr_bytes": stderr_path.stat().st_size,
        "budget": budget, "max_output_bytes": max_output_bytes,
        "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
        "wall_seconds": time.monotonic() - started,
        "evidence_source": "native_process", "skill_invoked": False,
        "side_effects": "Native Graphify may update graph-parent/cache/last_query_stamp",
    }
    atomic_file.replace_text(destination / "receipt.json", json.dumps(result, indent=2) + "\n")
    return result


def validate(record: dict) -> bool:
    """Check retained process evidence and current inputs, not semantic compliance."""
    if (not record.get("ok") or record.get("exit_code") != 0 or record.get("truncated")
            or record.get("timed_out") or record.get("inputs_changed")
            or record.get("unexpected_children") or not record.get("cleanup_confirmed")):
        return False
    fields = (("graph_path", "graph_sha256"), ("executable", "executable_sha256"),
              ("stdout_path", "stdout_sha256"), ("stderr_path", "stderr_sha256"))
    try:
        return all(_digest(Path(record[path])) == record[digest] for path, digest in fields)
    except (OSError, KeyError, TypeError, ValueError):
        return False
