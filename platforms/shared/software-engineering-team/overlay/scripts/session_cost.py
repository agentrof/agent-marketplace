#!/usr/bin/env python3
"""Cost and activity of fast-lane tasks, read from Claude Code and Codex transcripts.

``scan`` streams the project's transcripts once and returns every model call and
tool event with its time, lane, role, model, effort, tokens and git branch. The
retrospective attributes an event to a task by the task's branch when the record
names one, and otherwise by the task's time window. Only times, counts, roles,
models, file paths and result sizes are kept; no message text is stored.

Claude Code: ``~/.claude/projects/<project key>/*.jsonl`` and their
``*/subagents/agent-*.jsonl`` with the ``agent-*.meta.json`` naming the agent
type. Codex: ``~/.codex/sessions/**/*.jsonl`` whose session ``cwd`` lies in the
project. A missing or unreadable transcript counts nothing.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CODEX_SESSIONS = Path.home() / ".codex" / "sessions"
TOKEN_KEYS = ("input", "output", "cache_read", "cache_write")
LARGE_RESULT = 200 * 1024
REPEATED_READS = 5


def epoch(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def records(path: Path):
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def project_key(project: Path) -> str:
    return str(project.resolve()).replace("/", "-").replace(".", "-")


def claude_files(project: Path, home: Path) -> list[tuple[Path, str]]:
    """Each transcript with its role: ``main`` or the subagent's agent type."""
    key = project_key(project)
    files = []
    for folder in sorted(home.glob(key + "*")) if home.is_dir() else []:
        if not folder.is_dir() or not (folder.name == key or folder.name.startswith(key + "--")):
            continue
        files += [(path, "main") for path in sorted(folder.glob("*.jsonl"))]
        for path in sorted(folder.glob("*/subagents/**/agent-*.jsonl")):
            meta = path.with_name(path.stem + ".meta.json")
            try:
                role = json.loads(meta.read_text(encoding="utf-8")).get("agentType") or "agent"
            except (OSError, ValueError):
                role = "agent"
            files.append((path, str(role).rsplit(":", 1)[-1]))
    return files


def result_size(content) -> int:
    if isinstance(content, str):
        return len(content.encode("utf-8", errors="replace"))
    if isinstance(content, list):
        return sum(result_size(item.get("text", "") if isinstance(item, dict) else item) for item in content)
    return 0


def claude_events(path: Path, role: str) -> list[dict]:
    events, calls = [], {}
    for record in records(path):
        at = epoch(record.get("timestamp"))
        if at is None:
            continue
        branch = record.get("gitBranch") or ""
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        base = {"at": at, "branch": branch, "role": role, "lane": f"claude:{path.stem}"}
        if record.get("type") == "assistant":
            model = str(message.get("model") or "")
            if model.startswith("<"):
                continue
            usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
            tokens = {"input": usage.get("input_tokens") or 0, "output": usage.get("output_tokens") or 0,
                      "cache_read": usage.get("cache_read_input_tokens") or 0,
                      "cache_write": usage.get("cache_creation_input_tokens") or 0}
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Read":
                    events.append({**base, "kind": "read", "path": str((block.get("input") or {}).get("file_path") or "")})
            key = message.get("id") or record.get("uuid")
            # A reply streams as several records of one id; the largest output is its final count.
            if key in calls and calls[key]["tokens"]["output"] >= tokens["output"]:
                continue
            event = {**base, "kind": "call", "model": model,
                     "effort": str(record.get("effort") or record.get("perTurnEffort") or ""), "tokens": tokens}
            if key in calls:
                calls[key].update(event)
            else:
                calls[key] = event
                events.append(event)
        elif record.get("type") == "user":
            content = message.get("content")
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    events.append({**base, "kind": "result", "bytes": result_size(block.get("content"))})
            if isinstance(content, str):
                events.append({**base, "kind": "activity"})
    return events


def codex_events(path: Path, project: Path) -> list[dict]:
    first = next(iter(records(path)), {})
    meta = first.get("payload") if isinstance(first.get("payload"), dict) else {}
    cwd = str(meta.get("cwd") or "")
    root = str(project.resolve())
    if not (cwd == root or cwd.startswith(root + "/")):
        return []
    spawn = ((meta.get("source") or {}).get("subagent") or {}) if isinstance(meta.get("source"), dict) else {}
    role = "main"
    if isinstance(spawn, dict) and "thread_spawn" in spawn:
        role = str((spawn.get("thread_spawn") or {}).get("agent_path") or "agent").rsplit("/", 1)[-1].replace("_", "-")
    events, model, effort = [], "", ""
    for record in records(path):
        at = epoch(record.get("timestamp"))
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if record.get("type") == "turn_context":
            model, effort = str(payload.get("model") or model), str(payload.get("effort") or effort)
            continue
        if at is None:
            continue
        base = {"at": at, "branch": "", "role": role, "lane": f"codex:{path.stem}"}
        info = payload.get("info") if isinstance(payload.get("info"), dict) else None
        if payload.get("type") == "token_count" and info and isinstance(info.get("last_token_usage"), dict):
            usage = info["last_token_usage"]
            cached = usage.get("cached_input_tokens") or 0
            events.append({**base, "kind": "call", "model": model, "effort": effort,
                           "tokens": {"input": max((usage.get("input_tokens") or 0) - cached, 0),
                                      "output": usage.get("output_tokens") or 0,
                                      "cache_read": cached, "cache_write": 0}})
        elif record.get("type") in {"response_item", "event_msg"}:
            events.append({**base, "kind": "activity"})
    return events


def modified_since(path: Path, since: float) -> bool:
    try:
        return path.stat().st_mtime >= since
    except OSError:
        return False


def scan(project: Path, since: float = 0.0, claude_home: Path = CLAUDE_PROJECTS,
         codex_home: Path = CODEX_SESSIONS) -> list[dict]:
    """Events at or after *since*; a transcript last written before it is skipped unread."""
    events = []
    for path, role in claude_files(project, claude_home):
        if modified_since(path, since):
            events += claude_events(path, role)
    for path in sorted(codex_home.rglob("*.jsonl")) if codex_home.is_dir() else []:
        if modified_since(path, since):
            events += codex_events(path, project)
    return sorted((event for event in events if event["at"] >= since), key=lambda event: event["at"])


def belongs(event: dict, branch: str, start: float, end: float) -> bool:
    """An event is the task's when it names the task's branch, or names none inside its window."""
    if event["branch"]:
        return event["branch"] == branch
    return start <= event["at"] <= end


def cost(events: list[dict]) -> dict:
    calls = [event for event in events if event["kind"] == "call"]
    by_role: dict[str, Counter] = defaultdict(Counter)
    for call in calls:
        by_role[call["role"]].update(call["tokens"])
    return {"agents": len({event["lane"] for event in events if event["role"] != "main"}),
            "models": dict(Counter(call["model"] for call in calls if call["model"])),
            "efforts": dict(Counter(call["effort"] for call in calls if call["effort"])),
            "tokens_by_role": {role: {key: tokens[key] for key in TOKEN_KEYS}
                               for role, tokens in sorted(by_role.items())}}


def reading(events: list[dict]) -> dict:
    """Reading-waste signals: files read 5 or more times and tool results over 200 KB."""
    reads = Counter(event["path"] for event in events if event["kind"] == "read" and event["path"])
    return {"repeated_reads": {path: count for path, count in reads.most_common() if count >= REPEATED_READS},
            "results_over_200kb": sum(1 for event in events if event["kind"] == "result"
                                      and event["bytes"] > LARGE_RESULT)}


def active_minutes(events: list[dict]) -> set[int]:
    return {int(event["at"] // 60) for event in events}
