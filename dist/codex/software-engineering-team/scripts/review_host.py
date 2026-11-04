"""Codex transport for a read-only review packet supplied on stdin."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

HOST = "codex"


def mcp_inventory(executable: str, project: Path, overrides: list[str]) -> list[dict]:
    result = subprocess.run(
        [executable, "--no-daemon", *overrides, "mcp", "list", "--json"], cwd=project,
        capture_output=True, text=True, encoding="utf-8", timeout=15, check=False,
    )
    if result.returncode:
        raise ValueError("Codex --no-daemon MCP inventory failed; a compatible independent CLI is required")
    servers = json.loads(result.stdout)
    if not isinstance(servers, list) or any(
        not isinstance(server, dict) or not isinstance(server.get("name"), str)
        or not server["name"] or not isinstance(server.get("enabled"), bool)
        for server in servers
    ):
        raise ValueError("Codex returned an invalid MCP inventory")
    return servers


def command(project: Path, settings: dict, schema_path: Path, output_dir: Path) -> list[str]:
    if settings.get("host") != "codex" or settings.get("access") != "read":
        raise ValueError("Codex review requires a resolved Codex read profile")
    model, effort = settings.get("model"), settings.get("effort")
    if not model or model in {"inherit", "session"} or not effort or effort in {"inherit", "session"}:
        raise ValueError("an independent review requires an explicit model and effort")
    executable = shutil.which("codex")
    if not executable:
        raise ValueError("Codex is unavailable on PATH")
    overrides = [
        "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
        "-c", "features.apps=false", "-c", "features.multi_agent=false",
        "-c", "features.multi_agent_v2=false", "-c", "features.skill_mcp_dependency_install=false",
    ]
    servers = mcp_inventory(executable, project, overrides)
    entries = []
    for server in servers:
        transport = server.get("transport")
        if not isinstance(transport, dict):
            raise ValueError("Codex MCP inventory has no transport metadata")
        transport = transport.get("type")
        if transport == "stdio":
            placeholder = "command=" + json.dumps(sys.executable)
        elif transport in {"streamable_http", "http"}:
            placeholder = 'url="http://127.0.0.1/disabled-review-server"'
        else:
            raise ValueError("Codex MCP transport cannot be safely disabled")
        entries.append(json.dumps(server["name"]) + "={enabled=false," + placeholder + "}")
    # Empty tables recursively merge. Explicit entries also shadow plugin-owned servers.
    overrides += ["-c", "mcp_servers={" + ",".join(entries) + "}"]
    verified = mcp_inventory(executable, project, overrides)
    if any(server["enabled"] for server in verified):
        raise ValueError("Codex still exposes an enabled MCP server; review was not started")
    (output_dir / "host-preflight.json").write_bytes((json.dumps({
        "mcp_servers": [{"name": server["name"], "enabled": server["enabled"]} for server in verified],
        "note": "Native metadata preflight only; external configuration changes after this check remain outside this observation.",
    }, indent=2) + "\n").encode("utf-8"))
    return [
        executable, "--no-daemon", "exec", *overrides, "--sandbox", "read-only", "--cd", str(project),
        "--model", model, "-c", "model_reasoning_effort=" + json.dumps(effort),
        "--output-schema", str(schema_path), "--json", "-",
    ]


def parse_output(raw: str) -> dict:
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if not events or any(not isinstance(event, dict) for event in events):
        raise ValueError("invalid Codex review event stream")
    if any(event.get("type") in {"turn.failed", "error"} for event in events):
        raise ValueError("Codex review reported a failure")
    sessions = [event.get("thread_id") for event in events if event.get("type") == "thread.started"]
    completed = [event for event in events if event.get("type") == "turn.completed"]
    messages = [event["item"].get("text") for event in events
                if event.get("type") == "item.completed" and isinstance(event.get("item"), dict)
                and event["item"].get("type") == "agent_message"]
    if len(sessions) != 1 or not completed or not messages or events[-1].get("type") != "turn.completed":
        raise ValueError("Codex review requires one completed session and a final report")
    session_id = next(iter(sessions))
    if not isinstance(session_id, str) or not session_id or not isinstance(messages[-1], str):
        raise ValueError("Codex review has an invalid session or final message")
    report = json.loads(messages[-1])
    if not isinstance(report, dict):
        raise ValueError("Codex review final report must be a JSON object")
    return {
        "session_id": session_id, "report": report,
        "observed_model": None, "observed_effort": None,
        "usage": completed[-1].get("usage"),
    }
