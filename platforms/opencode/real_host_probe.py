#!/usr/bin/env python3
"""Exercise the pinned OpenCode binary against a localhost-only fake provider."""

from __future__ import annotations

import argparse
import json
import os
import select
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


TEAM = "software-engineering-team"
EXPECTED_VERSION = "1.18.17"
CONFIGURATION_TIMEOUT = 180.0
CLEANUP_TIMEOUT = 30.0
TUI_COMMAND = "/issue-report Prepare a deterministic probe issue\r"
TUI_READY_MARKERS = (b"ctrl+p", b"commands")


class ProbeError(RuntimeError):
    pass


def command(
    argv: list[str], *, cwd: Path, environment: dict[str, str], timeout: float = 45.0
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"process_timeout:{Path(argv[0]).name}") from exc


def require(result: subprocess.CompletedProcess[str], label: str) -> str:
    if result.returncode:
        message = (result.stderr or result.stdout).strip().replace("\n", " ")
        raise ProbeError(f"{label}:{message[:4000]}")
    return result.stdout


def ndjson(text: str) -> list[dict[str, Any]]:
    events = []
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events


def child_session(events: list[dict[str, Any]]) -> str:
    for event in events:
        part = event.get("part")
        if not isinstance(part, dict) or part.get("tool") != "task":
            continue
        metadata = (part.get("state") or {}).get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("sessionId"), str):
            return metadata["sessionId"]
    excerpt = json.dumps(events[-6:], sort_keys=True)
    raise ProbeError(f"child_session_missing:{excerpt[:3500]}")


def session_id(events: list[dict[str, Any]]) -> str:
    for event in events:
        value = event.get("sessionID")
        if isinstance(value, str):
            return value
    raise ProbeError("session_id_missing")


def direct_shell_command(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def config_writer_command(project: Path) -> str:
    private = project / ".opencode" / "agentrof" / "agent-marketplace"
    return direct_shell_command([
        sys.executable,
        str(private / "packages" / active_package_key(private)
            / TEAM / "scripts" / "project_config.py"),
        "set", "--config", str(project / "workspace" / "config.json"),
        "--field", "output_language", "--value", "Turkish",
    ])


class FakeProvider:
    """Minimal OpenAI-compatible streaming provider with deterministic tools."""

    def __init__(self, project: Path):
        self.project = project
        self.mode = "text"
        self.requests: list[dict[str, Any]] = []
        self.last_error: str | None = None
        self._lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: object) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                if self.path == "/v1/models":
                    self._json({"object": "list", "data": [
                        {"id": "probe-1", "object": "model"},
                        {"id": "gpt-5-probe", "object": "model"},
                    ]})
                    return
                self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                try:
                    if self.path != "/v1/chat/completions":
                        self.send_error(404)
                        return
                    raw = self.rfile.read(int(self.headers.get("content-length", "0")))
                    request = json.loads(raw)
                    with owner._lock:
                        owner.requests.append(request)
                    self._stream(owner.response(request))
                except Exception as exc:  # pragma: no cover - reported by the probe caller
                    owner.last_error = repr(exc)
                    self.send_error(500, str(exc))

            def _json(self, value: dict[str, Any]) -> None:
                payload = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _stream(self, chunks: list[dict[str, Any]]) -> None:
                payload = "".join(
                    "data: " + json.dumps(chunk) + "\n\n" for chunk in chunks
                ) + "data: [DONE]\n\n"
                data = payload.encode()
                self.send_response(200)
                self.send_header("content-type", "text/event-stream")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def request_count(self) -> int:
        with self._lock:
            return len(self.requests)

    def request_transcript(self, start: int) -> str:
        with self._lock:
            return json.dumps(self.requests[start:], sort_keys=True)

    def __enter__(self) -> "FakeProvider":
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    @staticmethod
    def _chunk(delta: dict[str, Any], finish_reason: str | None) -> dict[str, Any]:
        return {
            "id": "probe",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "gpt-5-probe",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }

    @staticmethod
    def _tool(name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            FakeProvider._chunk(
                {
                    "role": "assistant",
                    "tool_calls": [{
                        "index": 0,
                        "id": f"call_probe_{name}",
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(arguments)},
                    }],
                },
                None,
            ),
            FakeProvider._chunk({}, "tool_calls"),
        ]

    @staticmethod
    def _text() -> list[dict[str, Any]]:
        return [
            FakeProvider._chunk({"role": "assistant", "content": "probe response"}, None),
            FakeProvider._chunk({}, "stop"),
        ]

    @staticmethod
    def _unknown_text() -> list[dict[str, Any]]:
        return [
            FakeProvider._chunk({"role": "assistant", "content": "probe response"}, None),
            FakeProvider._chunk({}, "unknown"),
        ]

    def response(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        if self.mode == "text":
            return self._text()
        if self.mode == "unknown_text":
            return self._unknown_text()
        tools = {
            item.get("function", {}).get("name")
            for item in request.get("tools", [])
            if isinstance(item, dict)
        }
        has_result = any(
            isinstance(item, dict) and item.get("role") == "tool"
            for item in request.get("messages", [])
        )
        if has_result or not tools:
            return self._text()
        if "task" in tools and "write" not in tools:
            return self._tool("task", {
                "description": "exercise mutation guard",
                "prompt": "Perform the requested probe mutation and then report the result.",
                "subagent_type": "software-engineering-team-backend-developer",
            })
        if self.mode == "write":
            return self._tool("write", {"filePath": "probe-write.txt", "content": "probe written"})
        if self.mode == "escape":
            return self._tool("write", {"filePath": "../probe-escape.txt", "content": "escaped"})
        if self.mode == "guarded_config":
            return self._tool("write", {
                "filePath": "workspace/config.json",
                "content": '{"schema_version":9}\n',
            })
        if self.mode == "edit":
            return self._tool("edit", {
                "filePath": "probe-edit.txt",
                "oldString": "before",
                "newString": "after",
                "replaceAll": False,
            })
        if self.mode == "apply_patch":
            return self._tool("apply_patch", {
                "patchText": "*** Begin Patch\n*** Update File: probe-patch.txt\n@@\n-before\n+after\n*** End Patch\n",
            })
        if self.mode == "bash":
            return self._tool("bash", {
                "command": "printf 'probe bash' > probe-bash.txt",
                "timeout": 5000,
                "workdir": str(self.project),
            })
        if self.mode == "bash_guarded_config":
            return self._tool("bash", {
                "command": "printf '{\"schema_version\":9}\\n' > workspace/config.json",
                "timeout": 5000,
                "workdir": str(self.project),
            })
        if self.mode == "bash_config_writer":
            return self._tool("bash", {
                "command": config_writer_command(self.project),
                "timeout": 5000,
                "workdir": str(self.project),
            })
        raise ProbeError(f"unsupported_probe_mode:{self.mode}")


def environment(root: Path) -> dict[str, str]:
    home = root / "home"
    temporary = root / "tmp"
    home.mkdir()
    temporary.mkdir()
    values = dict(os.environ)
    values.update({
        "AGENT_MARKETPLACE_HOME": str(root / "global-agent-marketplace"),
        "OPENCODE_TEST_HOME": str(home),
        "PYTHONDONTWRITEBYTECODE": "1",
        "XDG_CONFIG_HOME": str(root / "xdg-config"),
        "XDG_DATA_HOME": str(root / "xdg-data"),
        "XDG_CACHE_HOME": str(root / "xdg-cache"),
        "XDG_STATE_HOME": str(root / "xdg-state"),
        # Keep the Windows and POSIX temporary-directory spellings aligned on
        # one existing, probe-owned directory for OpenCode's TUI subprocess.
        "TEMP": str(temporary),
        "TMP": str(temporary),
        "TMPDIR": str(temporary),
    })
    return values


def active_package_key(private: Path) -> str:
    try:
        installation = json.loads(
            (private / "installation.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError("installation_missing") from exc
    key = installation.get("active_build_key")
    if not isinstance(key, str) or not key:
        raise ProbeError("installation_missing")
    return key


def assert_no_global_marketplace_state(root: Path) -> None:
    forbidden = []
    global_home = root / "global-agent-marketplace"
    if global_home.exists():
        forbidden.append(global_home)
    for name in ("home", "xdg-config", "xdg-data", "xdg-cache", "xdg-state"):
        base = root / name
        if not base.is_dir():
            continue
        forbidden.extend(
            path for path in base.rglob("*")
            if path.name in {"agentrof", "agent-marketplace"}
        )
    if forbidden:
        raise ProbeError(
            "global_marketplace_write:"
            + ",".join(str(path.relative_to(root)) for path in forbidden[:20])
        )


def write_config(project: Path, provider: FakeProvider, model_id: str = "probe-1") -> None:
    (project / "opencode.json").write_text(json.dumps({
        "model": f"probe/{model_id}",
        "subagent_depth": 3,
        "provider": {
            "probe": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Agent Marketplace deterministic probe",
                "options": {"baseURL": provider.url, "apiKey": "probe-key"},
                "models": {
                    "probe-1": {"name": "Probe model"},
                    "gpt-5-probe": {"name": "Probe patch model"},
                },
            },
        },
    }, indent=2) + "\n", encoding="utf-8")


def host_version(executable: Path, project: Path, env: dict[str, str]) -> None:
    version = require(command([str(executable), "--version"], cwd=project, environment=env), "version")
    if version.strip().removeprefix("v") != EXPECTED_VERSION:
        raise ProbeError("unsupported_opencode_version")


def configure_project(package: Path, project: Path, executable: Path, env: dict[str, str]) -> Path:
    source = project.parent / "source"
    shutil.copytree(package, source)
    projector = source / "scripts" / "project_opencode.py"
    require(command([
        sys.executable, "-B", str(projector), "apply", "--project-root", str(project),
        "--clients-stopped", "--development-source",
    ], cwd=project, environment=env), "apply")
    private = project / ".opencode" / "agentrof" / "agent-marketplace"
    manage = private / "manage.py"
    (project / ".opencode" / "agents" / "probe-mutator.md").write_text(
        "---\n"
        "description: Deterministic OpenCode compatibility probe agent.\n"
        "permission:\n"
        "  \"*\": deny\n"
        "  read: allow\n"
        "  edit: allow\n"
        "---\n\n"
        "Apply only the supplied patch.\n",
        encoding="utf-8",
    )
    (project / ".opencode" / "agents" / "probe-readonly.md").write_text(
        "---\n"
        "description: Deterministic read-only permission probe agent.\n"
        "permission:\n"
        "  \"*\": deny\n"
        "  read: allow\n"
        "  grep: allow\n"
        "  glob: allow\n"
        "---\n\n"
        "Read only.\n",
        encoding="utf-8",
    )
    require(command([
        sys.executable, "-B", str(manage), "bind-runtime", "--opencode", str(executable),
    ], cwd=project, environment=env, timeout=CONFIGURATION_TIMEOUT), "bind_runtime")
    return manage


def check_discovery(executable: Path, project: Path, env: dict[str, str]) -> None:
    config = require(command([str(executable), "debug", "config"], cwd=project, environment=env), "debug_config")
    if "agent-marketplace-software-engineering-team.js" not in config:
        raise ProbeError("plugin_not_discovered")
    agents = require(command([str(executable), "agent", "list"], cwd=project, environment=env), "agent_list")
    if "software-engineering-team" not in agents:
        raise ProbeError("agent_not_discovered")
    skills = require(command([str(executable), "debug", "skill"], cwd=project, environment=env), "debug_skill")
    if "software-engineering-team-issue-report" not in skills:
        raise ProbeError("skill_not_discovered")
    nested = project / "nested" / "directory"
    nested.mkdir(parents=True)
    nested_config = require(command([str(executable), "debug", "config"], cwd=nested, environment=env), "nested_debug_config")
    if "agent-marketplace-software-engineering-team.js" not in nested_config:
        raise ProbeError("nested_project_root_unresolved")


def exported(executable: Path, project: Path, env: dict[str, str], session_id: str) -> str:
    return require(command([str(executable), "export", session_id], cwd=project, environment=env), "export")


def run_command(
    executable: Path, project: Path, env: dict[str, str], provider: FakeProvider,
    mode: str, *, agent: str = TEAM,
) -> str:
    provider.mode = mode
    argv = [str(executable), "run", "probe", mode]
    if agent == TEAM:
        argv.extend(["--command", "issue-report"])
    argv.extend([
        "--agent", agent, "--format", "json", "--dir", str(project),
        "--print-logs", "--log-level", "DEBUG",
    ])
    result = command(argv, cwd=project, environment=env)
    if provider.last_error:
        raise ProbeError(f"fake_provider:{provider.last_error}")
    stdout = require(result, f"run_{mode}")
    if not ndjson(stdout):
        raise ProbeError(f"ndjson_missing:{mode}")
    return stdout


def assert_mutator(executable: Path, project: Path, env: dict[str, str], provider: FakeProvider, mode: str) -> None:
    write_config(project, provider, "gpt-5-probe" if mode == "apply_patch" else "probe-1")
    if mode == "edit":
        (project / "probe-edit.txt").write_text("before", encoding="utf-8")
    if mode == "apply_patch":
        (project / "probe-patch.txt").write_text("before\n", encoding="utf-8")
    output = run_command(
        executable, project, env, provider, mode,
        agent="probe-mutator" if mode == "apply_patch" else TEAM,
    )
    events = ndjson(output)
    detail = exported(
        executable, project, env,
        session_id(events) if mode == "apply_patch" else child_session(events),
    )
    if '"status": "error"' in detail:
        start = max(0, detail.index('"status": "error"') - 180)
        raise ProbeError(f"mutator_error:{mode}:{detail[start:start + 900]}")
    expected = {
        "write": (project / "probe-write.txt", "probe written"),
        "edit": (project / "probe-edit.txt", "after"),
        "apply_patch": (project / "probe-patch.txt", "after\n"),
        "bash": (project / "probe-bash.txt", "probe bash"),
    }[mode]
    path, value = expected
    if not path.is_file() or path.read_text(encoding="utf-8") != value:
        actual = path.read_text(encoding="utf-8") if path.is_file() else "<missing>"
        marker = detail.find(f'"tool": "{mode}"')
        context = detail[marker:marker + 1500] if marker >= 0 else detail[-900:]
        raise ProbeError(f"mutator_effect_missing:{mode}:{actual!r}:{context}")


def assert_unknown_text_is_bounded(
    executable: Path, project: Path, env: dict[str, str], provider: FakeProvider,
) -> None:
    before = provider.request_count()
    run_command(executable, project, env, provider, "unknown_text")
    requests = provider.request_count() - before
    if requests > 2:
        raise ProbeError(f"unknown_text_unbounded:{requests}")


def assert_pre_deny(executable: Path, project: Path, env: dict[str, str], provider: FakeProvider) -> None:
    write_config(project, provider)
    escaped = project.parent / "probe-escape.txt"
    escaped.unlink(missing_ok=True)
    output = run_command(executable, project, env, provider, "escape")
    detail = exported(executable, project, env, child_session(ndjson(output)))
    if "pre_hook_denied" not in detail or escaped.exists():
        raise ProbeError("pre_deny_failed")


def guarded_config(project: Path) -> tuple[Path, str]:
    path = project / "workspace" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps({
        "schema_version": 2,
        "team_id": TEAM,
        "output_language": "English",
        "terminology_language": "English",
    }, sort_keys=True) + "\n"
    path.write_text(original, encoding="utf-8")
    return path, original


def assert_canonical_hook_guards(
    executable: Path, project: Path, env: dict[str, str], provider: FakeProvider,
) -> None:
    config, original = guarded_config(project)
    write_config(project, provider)
    output = run_command(executable, project, env, provider, "guarded_config")
    detail = exported(executable, project, env, child_session(ndjson(output)))
    if "pre_hook_denied" not in detail or config.read_text(encoding="utf-8") != original:
        raise ProbeError("canonical_pre_hook_not_enforced")

    write_config(project, provider)
    output = run_command(executable, project, env, provider, "bash_guarded_config")
    detail = exported(executable, project, env, child_session(ndjson(output)))
    if "post_hook_failed" not in detail or config.read_text(encoding="utf-8") != original:
        raise ProbeError("canonical_post_hook_not_enforced")


def assert_sanctioned_config_writer(
    executable: Path, project: Path, env: dict[str, str], provider: FakeProvider,
) -> None:
    config, _original = guarded_config(project)
    write_config(project, provider)
    output = run_command(executable, project, env, provider, "bash_config_writer")
    detail = exported(executable, project, env, child_session(ndjson(output)))
    if '"status": "error"' in detail:
        raise ProbeError(f"sanctioned_config_writer_failed:{detail[-1200:]}")
    try:
        value = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProbeError("sanctioned_config_writer_invalid") from exc
    if value.get("output_language") != "Turkish":
        raise ProbeError("sanctioned_config_writer_restored")


def assert_readonly_permission_denial(
    executable: Path, project: Path, env: dict[str, str], provider: FakeProvider,
) -> None:
    for mode, path in (("write", project / "probe-write.txt"), ("bash", project / "probe-bash.txt")):
        path.unlink(missing_ok=True)
        write_config(project, provider)
        output = run_command(executable, project, env, provider, mode, agent="probe-readonly")
        detail = exported(executable, project, env, session_id(ndjson(output)))
        if path.exists() or '"status": "error"' not in detail:
            raise ProbeError(f"readonly_permission_not_denied:{mode}")


def assert_plugin_set_deny(executable: Path, project: Path, env: dict[str, str], provider: FakeProvider) -> None:
    write_config(project, provider)
    extra = project / ".opencode" / "plugins" / "third-party.js"
    extra.write_text("export const ThirdParty = async () => ({});\n", encoding="utf-8")
    try:
        output = run_command(executable, project, env, provider, "write")
        events = ndjson(output)
        details = [exported(executable, project, env, session_id(events))]
        try:
            child = child_session(events)
        except ProbeError:
            child = None
        if child is not None:
            details.append(exported(executable, project, env, child))
        combined = "\n".join(details)
        if "unsupported_plugin_set" not in combined:
            raise ProbeError(f"plugin_set_not_denied:{combined[-1200:]}")
    finally:
        extra.unlink(missing_ok=True)


def write_tui_artifact(artifact_dir: Path | None, transcript: bytes) -> None:
    if artifact_dir is not None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "opencode-tui-transcript.bin").write_bytes(transcript)


def terminate_windows_process_tree(pid: object) -> None:
    """Terminate the TUI host and any descendants which keep its project open."""
    if not isinstance(pid, int) or pid <= 0:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        # PtyProcess.terminate is still attempted by the caller. This fallback
        # is only needed for OpenCode descendants which inherit the project CWD.
        pass


def cleanup_probe_root(root: Path) -> None:
    """Remove an isolated probe root after Windows releases a killed TUI tree."""
    deadline = time.monotonic() + CLEANUP_TIMEOUT
    while True:
        try:
            shutil.rmtree(root)
            return
        except FileNotFoundError:
            return
        except PermissionError as exc:
            if time.monotonic() >= deadline:
                raise ProbeError(f"temporary_cleanup_failed:{exc}") from exc
            time.sleep(0.2)


def tui_ready(transcript: bytes) -> bool:
    return all(marker in transcript for marker in TUI_READY_MARKERS)


def close_windows_tui_process(process: Any) -> None:
    """Kill OpenCode's native tree before and after closing the winpty wrapper."""
    pid = getattr(process, "pid", None)
    terminate_windows_process_tree(pid)
    try:
        process.terminate(force=True)
    except Exception:  # pywinpty may raise WinptyError after child exit.
        pass
    try:
        process.close(force=True)
    except Exception:  # cleanup must not mask a successful ConPTY proof.
        pass
    # Winpty may detach conhost/node while closing; retry with the captured root.
    terminate_windows_process_tree(pid)


def tui_windows(
    executable: Path, project: Path, env: dict[str, str], artifact_dir: Path | None,
) -> None:
    os.environ["PYWINPTY_BLOCK"] = "0"
    try:
        from winpty import PtyProcess
    except ImportError as exc:
        raise ProbeError("tui_conpty_unavailable") from exc

    process = PtyProcess.spawn(
        [str(executable)],
        cwd=str(project),
        env=dict(env, TERM="xterm-256color"),
        dimensions=(30, 120),
    )
    process.fileobj.setblocking(False)
    transcript = bytearray()
    started = time.monotonic()
    prompt_sent = False
    try:
        while time.monotonic() - started < 25:
            ready, _, _ = select.select([process.fileobj], [], [], 0.2)
            if ready:
                try:
                    data = process.fileobj.recv(65536)
                except BlockingIOError:
                    data = b""
                transcript.extend(data.replace(b"0011Ignore", b""))
            if not prompt_sent and tui_ready(bytes(transcript)):
                process.write(TUI_COMMAND)
                prompt_sent = True
            if b"probe response" in transcript:
                return
            if not process.isalive():
                break
        excerpt = transcript.decode("utf-8", errors="replace")[-1200:]
        raise ProbeError(f"tui_command_timeout:{excerpt!r}")
    finally:
        close_windows_tui_process(process)
        write_tui_artifact(artifact_dir, bytes(transcript))


def tui_posix(
    executable: Path, project: Path, env: dict[str, str], artifact_dir: Path | None,
) -> None:
    import pty

    child, master = pty.fork()
    if child == 0:  # pragma: no cover - executed in the isolated child process
        os.chdir(project)
        os.execvpe(str(executable), [str(executable)], dict(env, TERM="xterm-256color"))
    transcript = bytearray()
    try:
        deadline = time.monotonic() + 20
        prompt_sent = False
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    transcript.extend(os.read(master, 65536))
                except OSError:
                    break
            if not prompt_sent and tui_ready(bytes(transcript)):
                os.write(master, TUI_COMMAND.encode("utf-8"))
                prompt_sent = True
            if b"probe response" in transcript:
                return
        excerpt = transcript.decode("utf-8", errors="replace")[-1200:]
        raise ProbeError(f"tui_command_timeout:{excerpt!r}")
    finally:
        try:
            os.killpg(child, signal.SIGTERM)
        except ProcessLookupError:
            pass
        wait_deadline = time.monotonic() + 5
        while time.monotonic() < wait_deadline:
            try:
                finished, _status = os.waitpid(child, os.WNOHANG)
            except ChildProcessError:
                finished = child
            if finished == child:
                break
            time.sleep(0.05)
        else:
            try:
                os.killpg(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(child, 0)
            except ChildProcessError:
                pass
        os.close(master)
        write_tui_artifact(artifact_dir, bytes(transcript))


def tui(
    executable: Path, project: Path, env: dict[str, str],
    artifact_dir: Path | None, provider: FakeProvider,
) -> None:
    provider.mode = "text"
    request_start = provider.request_count()
    if os.name == "nt":
        tui_windows(executable, project, env, artifact_dir)
    else:
        tui_posix(executable, project, env, artifact_dir)
    requests = provider.request_transcript(request_start)
    required = (
        "skill-content/issue-report/SKILL.md",
        "Agent Marketplace run mode: choice_free.",
    )
    if any(marker not in requests for marker in required):
        raise ProbeError(f"tui_command_not_routed:{requests[-2000:]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opencode", required=True, type=Path)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--tui", action="store_true", help="exercise the interactive TUI through a real PTY")
    parser.add_argument("--tui-only", action="store_true",
                        help="run only the setup/binding/TUI proof; useful for bounded PTY runners")
    parser.add_argument("--artifact-dir", type=Path,
                        help="optional explicit directory for sanitized PTY test artifacts")
    args = parser.parse_args()
    if args.tui_only:
        args.tui = True
    executable = args.opencode.resolve()
    package = args.package.resolve()
    if not executable.is_file() or not package.is_dir():
        print(json.dumps({"ok": False, "code": "runtime_unbound"}))
        return 4
    try:
        root = Path(tempfile.mkdtemp(prefix="agent-marketplace-opencode-real."))
        try:
            # Spaces and parentheses exercise native shell quoting, especially
            # cmd.exe on Windows, in every real-host lifecycle scenario.
            project = (root / "project (shell contract)").resolve()
            project.mkdir()
            env = environment(root)
            with FakeProvider(project) as provider:
                write_config(project, provider)
                host_version(executable, project, env)
                manage = configure_project(package, project, executable, env)
                require(command([sys.executable, "-B", str(manage), "check"], cwd=project, environment=env), "manage_check")
                check_discovery(executable, project, env)
                if args.tui_only:
                    tui(executable, project, env, args.artifact_dir, provider)
                else:
                    run_command(executable, project, env, provider, "text")
                    assert_unknown_text_is_bounded(executable, project, env, provider)
                    for mode in ("write", "edit", "apply_patch", "bash"):
                        assert_mutator(executable, project, env, provider, mode)
                    assert_pre_deny(executable, project, env, provider)
                    assert_canonical_hook_guards(executable, project, env, provider)
                    assert_sanctioned_config_writer(executable, project, env, provider)
                    assert_readonly_permission_denial(executable, project, env, provider)
                    assert_plugin_set_deny(executable, project, env, provider)
                    if args.tui:
                        tui(executable, project, env, args.artifact_dir, provider)
                assert_no_global_marketplace_state(root)
        finally:
            cleanup_probe_root(root)
        print(json.dumps({"ok": True, "version": EXPECTED_VERSION, "surface": "terminal"}))
        return 0
    except ProbeError as exc:
        print(json.dumps({"ok": False, "code": "hook_contract_incompatible", "detail": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
