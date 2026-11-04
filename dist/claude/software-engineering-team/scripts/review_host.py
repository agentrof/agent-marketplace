"""Claude Code transport for a read-only review packet supplied on stdin."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

HOST = "claude"


def environment() -> dict[str, str]:
    # Match Anthropic SDK PR 732's narrow inherited-marker handling.
    inherited = dict(os.environ)
    inherited.pop("CLAUDECODE", None)
    inherited.pop("CLAUDE_CODE_EFFORT_LEVEL", None)
    inherited["PYTHONDONTWRITEBYTECODE"] = "1"
    inherited["CLAUDE_CODE_MAX_RETRIES"] = "1"
    return inherited


def command(project: Path, settings: dict, schema_path: Path, output_dir: Path) -> list[str]:
    """Keep native authentication, plugin hooks and session observations intact."""
    if settings.get("host") != "claude" or settings.get("access") != "read":
        raise ValueError("Claude review requires a resolved Claude read profile")
    model, effort = settings.get("model"), settings.get("effort")
    if not model or model in {"inherit", "session"} or not effort or effort in {"inherit", "session"}:
        raise ValueError("an independent review requires an explicit model and effort")
    executable = shutil.which("claude")
    if not executable:
        raise ValueError("Claude Code is unavailable on PATH")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return [
        executable, "--print", "--model", model, "--effort", effort,
        "--plugin-dir", str(Path(__file__).resolve().parents[1]),
        "--tools", "Read,Grep,Glob", "--allowedTools", "Read,Grep,Glob",
        "--permission-mode", "dontAsk", "--permission-prompts", "none",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--no-chrome", "--output-format", "stream-json", "--verbose",
        "--include-hook-events", "--json-schema", json.dumps(schema, separators=(",", ":")),
    ]


def parse_output(raw: str) -> dict:
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if not events or any(not isinstance(event, dict) for event in events):
        raise ValueError("invalid Claude review event stream")
    results = [event for event in events if event.get("type") == "result"]
    if not results:
        raise ValueError("Claude review has no final result")
    result = results[-1]
    if result.get("is_error") is not False or result.get("subtype") != "success":
        raise ValueError("Claude review did not complete successfully")
    session_id = result.get("session_id")
    report = result.get("structured_output")
    if not isinstance(session_id, str) or not session_id or not isinstance(report, dict):
        raise ValueError("Claude review requires a session ID and structured report")
    if any(event.get("session_id", session_id) != session_id for event in events):
        raise ValueError("Claude review stream contains multiple sessions")
    models = {
        event["message"]["model"] for event in events
        if event.get("type") == "assistant" and not event.get("parent_tool_use_id")
        and isinstance(event.get("message"), dict)
        and isinstance(event["message"].get("model"), str)
    }
    return {
        "session_id": session_id, "report": report,
        "observed_model": next(iter(models)) if len(models) == 1 else None,
        "observed_effort": None,
        "usage": {
            "model_usage": result.get("modelUsage"),
            "last_result_usage": result.get("usage"),
            "total_cost_usd": result.get("total_cost_usd"),
            "note": "Final native cumulative model usage and cost telemetry; last-result usage may cover only the last turn. Account billing is not independently observed.",
        },
    }
