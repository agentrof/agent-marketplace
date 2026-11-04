"""Pass bounded native lifecycle observations to the local retrospective worker."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import sys
import uuid


EVENTS = {
    "claude": {"SessionStart", "Stop", "SessionEnd", "SubagentStop", "StopFailure", "PostToolUseFailure"},
    "codex": {"SessionStart", "Stop", "SessionEnd", "SubagentStop", "Interrupt", "PostToolUse"},
}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,255}\Z")
ERROR_CATEGORIES = {
    "rate_limit", "overloaded", "authentication_failed", "oauth_org_not_allowed", "account_on_hold",
    "billing_error", "invalid_request", "model_not_found", "server_error", "max_output_tokens",
    "cloud_credential_error", "unknown",
}


def transcript(payload: dict, host: str) -> dict:
    raw = payload.get("agent_transcript_path") or payload.get("transcript_path")
    if not isinstance(raw, str) or not raw:
        return {"transcript_status": "missing"}
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR", str(Path.home() / ".claude")) if host == "claude"
                else os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser().resolve()
    path = Path(raw).expanduser()
    resolved = path.resolve()
    folders = ("projects",) if host == "claude" else ("sessions", "archived_sessions")
    if not path.is_absolute() or resolved.suffix != ".jsonl" or not any(
            resolved.is_relative_to(base / folder) for folder in folders):
        return {"transcript_status": "rejected"}
    if resolved.exists() and not resolved.is_file():
        return {"transcript_status": "rejected"}
    # The collector also verifies session/project identity before reading content.
    return {"transcript_path": str(resolved), "transcript_status": "available" if resolved.is_file() else "missing"}


def failed_exit(payload: dict) -> int | None:
    response = payload.get("tool_response")
    if isinstance(response, dict):
        code = response.get("exit_code")
        if type(code) is int:
            return code
    text = payload.get("error") if payload.get("hook_event_name") == "PostToolUseFailure" else None
    if isinstance(text, str):
        match = re.search(r"(?m)^(?:Exit code |Process exited with code )(-?\d+)(?:\s|$)", text)
        if match:
            return int(match.group(1))
    return None


def diagnostic(category) -> None:
    safe = category if isinstance(category, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", category) else "unknown"
    print(f"Retrospective tracking unavailable ({safe}).", file=sys.stderr)


def observe(payload: dict, host: str) -> None:
    operation = payload.get("hook_event_name")
    session = payload.get("session_id")
    if operation not in EVENTS.get(host, ()) or not isinstance(session, str) or not IDENTIFIER.fullmatch(session):
        return
    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        return
    directory = Path(cwd).resolve()
    root = next((parent for parent in (directory, *directory.parents)
                 if (parent / "workspace/config.json").is_file()), None)
    if root is None:
        return
    tool_input = payload.get("tool_input")
    command = tool_input.get("command", tool_input.get("cmd", "")) if isinstance(tool_input, dict) else ""
    if isinstance(command, str) and re.search(r"\bretrospective(?:_hook)?\.py\b", command):
        return
    code = failed_exit(payload)
    response = payload.get("tool_response")
    explicit_error = isinstance(response, dict) and (response.get("isError") is True or response.get("is_error") is True)
    failure = operation in {"StopFailure", "PostToolUseFailure"} or (
        operation == "PostToolUse" and ((code is not None and code != 0) or explicit_error))
    # Codex Bash hooks can omit exit metadata; the collector reads native completion records.
    unknown_shell = (host == "codex" and operation == "PostToolUse" and payload.get("tool_name") == "Bash"
                     and code is None and not explicit_error)
    if operation == "PostToolUse" and not failure and not unknown_shell:
        return
    event = {"kind": "failure" if failure else "session", "operation": operation,
             "host": host, "session_id": session, "status": "failed" if failure else "observed",
             "source_id": uuid.uuid4().hex,
             **transcript(payload, host)}
    if unknown_shell:
        event["status"] = "unknown"
    for field in ("turn_id", "agent_id", "tool_use_id"):
        value = payload.get(field)
        if isinstance(value, str) and IDENTIFIER.fullmatch(value):
            event[field] = value
    tool = payload.get("tool_name")
    if isinstance(tool, str) and IDENTIFIER.fullmatch(tool):
        event["tool"] = tool
    duration = payload.get("duration_ms")
    if type(duration) in (int, float) and math.isfinite(duration) and duration >= 0:
        event["duration_ms"] = duration
    if code is not None:
        event["exit_code"] = code
    if operation == "Interrupt" or payload.get("is_interrupt") is True:
        event["status"] = "interrupted"
    if failure:
        category = payload.get("error") if operation == "StopFailure" else "tool_failure"
        event["error_category"] = category if isinstance(category, str) and (
            category in ERROR_CATEGORIES or category == "tool_failure") else "unknown"
    import retrospective
    result = retrospective.emit(root, event)
    if result.get("queued") is False or (result.get("observer_started") is False and result.get("error_category")):
        diagnostic(result.get("error_category"))


def main(argv: list[str]) -> int:
    try:
        if len(argv) != 2 or argv[0] != "--host" or argv[1] not in EVENTS:
            return 0
        raw = sys.stdin.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            return 0
        payload = json.loads(raw)
        if isinstance(payload, dict):
            observe(payload, argv[1])
    except Exception as error:
        diagnostic(type(error).__name__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
