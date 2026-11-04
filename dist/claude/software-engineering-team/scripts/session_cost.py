#!/usr/bin/env python3
"""Cost and activity of fast-lane tasks, read from Claude Code and Codex transcripts.

``scan`` streams the project's transcripts once and returns every model call and
tool event with its time, lane, role, model, effort, tokens and git branch. The
retrospective attributes an event to a task by one rule (``belongs``): a subagent
whose spawn description or first prompt names exactly one task id (``TK-###``)
is that task's; else a record on a ``task/...`` branch is that branch's task;
else a main-session event on the base branch falls in every task window that
holds it and its cost is split evenly between them; one no window holds goes
to the task branch its session switches to next (the calls before ``start``). A subagent with neither a
task id nor a task branch is other work and stays unattributed.
What matches no task is the unattributed remainder. Only times, counts, roles,
models, task ids, spawn descriptions, file paths and result sizes are kept; no
message text is stored.

``cost`` prices each call from a pricing table (USD per 1M tokens per model id,
matched exactly or by the longest id prefix; ``[1m]``-style suffixes ignored):
input, cache write 5 minutes and 1 hour, cache read and output, with an
optional ``long_context`` tier that applies to the whole call once its prompt
(input plus cache tokens) passes ``above_prompt_tokens``, and a ``fast_multiplier``
for a call Claude Code marks ``speed: fast``. A model missing from
the table counts tokens but no dollars and is listed as unpriced. A spawn's
startup context is the prompt of its first call: input plus cache write plus
cache read tokens.

``observe`` reads the registered Git worktrees and their local review evidence.
An explicit expected-session list restricts native transcript discovery to those
sessions and recorded reviewers. File, byte and time limits keep the observer
bounded. Missing evidence is reported separately from measured zero usage.
Model-facing source and packet comparisons retain hashes and outcomes only.
``scan`` preserves the historical event-list interface for existing callers.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

CLAUDE_PROJECTS = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"
CODEX_SESSIONS = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"
TOKEN_KEYS = ("input", "output", "cache_read", "cache_write")
LARGE_RESULT = 200 * 1024
REPEATED_READS = 5
PRICING_FILE = Path(__file__).resolve().parent.parent / "skill-content" / "retrospective" / "data" / "pricing.json"
PROJECT_PRICING = Path(".agentrof") / "pricing.json"
TASK_ID_RE = re.compile(r"\b(?:TK|tk)-([0-9]{3,})\b")
# The branch task_lane.py start creates; a base branch may also be named task/... .
TASK_BRANCH_RE = re.compile(r"^task/tk-([0-9]{3,})-")
MAX_SCAN_FILES = 128
MAX_SCAN_BYTES = 16 * 1024 * 1024
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_SCAN_SECONDS = 8
RUNTIME_REVIEW = Path(".agentrof/agent-marketplace/.runtime/review")


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


def task_hint(*texts) -> str:
    """The one task id the texts name, or "" when they name none or several."""
    found = {f"TK-{match}" for text in texts if isinstance(text, str) for match in TASK_ID_RE.findall(text)}
    return found.pop() if len(found) == 1 else ""


def text_of(content) -> str:
    if isinstance(content, list):
        return " ".join(item.get("text", "") for item in content if isinstance(item, dict))
    return content if isinstance(content, str) else ""


def claude_files(project: Path, home: Path) -> list[tuple[Path, str, str]]:
    """Each transcript with its role (``main`` or the subagent's agent type) and spawn description."""
    key = project_key(project)
    files = []
    for folder in sorted(home.glob(key + "*")) if home.is_dir() else []:
        if not folder.is_dir() or not (folder.name == key or folder.name.startswith(key + "--")):
            continue
        files += [(path, "main", "") for path in sorted(folder.glob("*.jsonl"))]
        for path in sorted(folder.glob("*/subagents/**/agent-*.jsonl")):
            meta = path.with_name(path.stem + ".meta.json")
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = {}
            role = data.get("agentType") or "agent"
            files.append((path, str(role).rsplit(":", 1)[-1], str(data.get("description") or "")))
    return files


def result_size(content) -> int:
    if isinstance(content, str):
        return len(content.encode("utf-8", errors="replace"))
    if isinstance(content, list):
        return sum(result_size(item.get("text", "") if isinstance(item, dict) else item) for item in content)
    return 0


def claude_events(path: Path, role: str, purpose: str = "", *, source_records=None) -> list[dict]:
    rows = list(records(path)) if source_records is None else source_records
    events, calls, task, started = [], {}, "", None
    if role != "main":
        first = next((record for record in rows if record.get("type") == "user"), {})
        task = task_hint(purpose, text_of((first.get("message") or {}).get("content")))
        # A subagent keeps the branch it started on: a later checkout switch under it is not its task.
        started = next((record["gitBranch"] for record in rows if record.get("gitBranch")), "")
    for record_index, record in enumerate(rows):
        at = epoch(record.get("timestamp"))
        if at is None:
            continue
        branch = (record.get("gitBranch") or "") if started is None else started
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        record_identity = str(message.get("id") or record.get("uuid") or record_index)
        base = {"at": at, "branch": branch, "role": role, "lane": f"claude:{path.stem}", "task": task,
                "purpose": purpose, "event_id": record_identity}
        if record.get("type") == "assistant":
            model = str(message.get("model") or "")
            if model.startswith("<"):
                continue
            usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
            tokens = {"input": usage.get("input_tokens") or 0, "output": usage.get("output_tokens") or 0,
                      "cache_read": usage.get("cache_read_input_tokens") or 0,
                      "cache_write": usage.get("cache_creation_input_tokens") or 0}
            split = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
            for index, block in enumerate(message.get("content") or []):
                block_identity = (block.get("id") or f"{record_identity}:{index}") if isinstance(block, dict) else f"{record_identity}:{index}"
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Read":
                    events.append({**base, "kind": "read", "path": str((block.get("input") or {}).get("file_path") or ""),
                                   "event_id": block_identity})
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Skill":
                    events.append({**base, "kind": "skill", "name": str((block.get("input") or {}).get("skill") or ""),
                                   "event_id": block_identity})
            if not any(key in usage for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")):
                continue
            key = record_identity
            # A reply streams as several records of one id; the largest output is its final count.
            if key in calls and calls[key]["tokens"]["output"] >= tokens["output"]:
                continue
            event = {**base, "kind": "call", "model": model,
                     "event_id": key,
                     "effort": str(record.get("effort") or record.get("perTurnEffort") or ""), "tokens": tokens,
                     "cache_write_1h": split.get("ephemeral_1h_input_tokens") or 0,
                     "fast": usage.get("speed") == "fast"}
            if key in calls:
                calls[key].update(event)
            else:
                calls[key] = event
                events.append(event)
        elif record.get("type") == "user":
            content = message.get("content")
            for index, block in enumerate(content if isinstance(content, list) else []):
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    events.append({**base, "kind": "result", "bytes": result_size(block.get("content")),
                                   "event_id": block.get("tool_use_id") or f"{record_identity}:{index}"})
            if isinstance(content, str):
                events.append({**base, "kind": "activity"})
    if role == "main":
        # A session's calls before start stamped requested_at lead to the task branch it switches to next.
        lead = ""
        for event in sorted(events, key=lambda event: event["at"], reverse=True):
            match = TASK_BRANCH_RE.match(event["branch"])
            lead = f"TK-{match.group(1)}" if match else lead
            event["lead"] = "" if match else lead
    return events


def codex_events(path: Path, project: Path, *, source_records=None, problems=None) -> list[dict]:
    rows = list(records(path)) if source_records is None else source_records
    first = next(iter(rows), {})
    meta = first.get("payload") if isinstance(first.get("payload"), dict) else {}
    cwd = str(Path(meta["cwd"]).resolve()) if meta.get("cwd") else ""
    root = str(project.resolve())
    if not (cwd == root or cwd.startswith(root + "/")):
        return []
    spawn = ((meta.get("source") or {}).get("subagent") or {}) if isinstance(meta.get("source"), dict) else {}
    role = "main"
    if isinstance(spawn, dict) and "thread_spawn" in spawn:
        role = str((spawn.get("thread_spawn") or {}).get("agent_path") or "agent").rsplit("/", 1)[-1].replace("_", "-")
    branch = str((meta.get("git") or {}).get("branch") or "") if isinstance(meta.get("git"), dict) else ""
    task = ""
    if role != "main":
        first = next((record for record in rows
                      if (record.get("payload") or {}).get("type") == "user_message"), {})
        task = task_hint((first.get("payload") or {}).get("message"))
    events, model, effort, previous = [], "", "", {}
    for record in rows:
        at = epoch(record.get("timestamp"))
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if record.get("type") == "turn_context":
            model, effort = str(payload.get("model") or model), str(payload.get("effort") or effort)
            continue
        if at is None:
            continue
        base = {"at": at, "branch": branch, "role": role, "lane": f"codex:{path.stem}", "task": task,
                "purpose": ""}
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        exit_code = item.get("exit_code") if type(item.get("exit_code")) is int else None
        if record.get("type") == "event_msg" and payload.get("type") == "item_completed" \
                and item.get("type") == "CommandExecution" \
                and (item.get("status") == "failed" or exit_code not in (None, 0)):
            identity = item.get("id")
            if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,200}", identity):
                if problems is not None:
                    problems.append("invalid_native_failure_id")
                continue
            status = item.get("status")
            if status not in ("failed", "completed", "in_progress", "declined", "cancelled", "interrupted"):
                status = "unknown"
                if problems is not None:
                    problems.append("invalid_native_failure_status")
            duration = item.get("duration") if isinstance(item.get("duration"), dict) else {}
            seconds, nanos = duration.get("secs"), duration.get("nanos")
            elapsed = seconds + nanos / 1_000_000_000 if type(seconds) is int and 0 <= seconds < 2**64 \
                and type(nanos) is int and 0 <= nanos < 1_000_000_000 else None
            if elapsed is None and item.get("duration") is not None and problems is not None:
                problems.append("invalid_native_failure_duration")
            events.append({**base, "kind": "failure", "event_id": identity,
                           "observation_source": "native_item_completed", "tool": "Bash",
                           "status": status, "exit_code": exit_code, "seconds": elapsed})
            continue
        info = payload.get("info") if isinstance(payload.get("info"), dict) else None
        if payload.get("type") == "token_count" and info and isinstance(info.get("last_token_usage"), dict):
            usage = info["last_token_usage"]
            total = info.get("total_token_usage")
            if isinstance(total, dict) and total:
                total = {key: value for key, value in total.items() if isinstance(value, (int, float)) and value >= 0}
                if any(value < previous.get(key, 0) for key, value in total.items()):
                    if problems is not None:
                        problems.append("non_monotonic_usage")
                    continue
                first_total = not previous
                usage = {key: value - previous.get(key, 0) for key, value in total.items()}
                previous = total
                if not first_total and not any(usage.values()):
                    continue
            cached = usage.get("cached_input_tokens") or 0
            written = usage.get("cache_write_input_tokens") or 0
            # Codex input_tokens include the cached and cache-written part.
            events.append({**base, "kind": "call", "model": model, "effort": effort,
                           "event_id": json.dumps(total, sort_keys=True) if isinstance(total, dict) else
                           str(record.get("uuid") or record.get("timestamp")) + json.dumps(usage, sort_keys=True),
                           "tokens": {"input": max((usage.get("input_tokens") or 0) - cached - written, 0),
                                      "output": usage.get("output_tokens") or 0,
                                      "cache_read": cached, "cache_write": written}})
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
    """Compatibility view. Use observe for explicit coverage and missing-source information."""
    return observe(project, since, claude_home, codex_home)["events"]


def worktrees(project: Path) -> list[Path]:
    """Only Git-registered checkouts share project observations."""
    roots = {project.resolve()}
    try:
        result = subprocess.run(["git", "worktree", "list", "--porcelain", "-z"], cwd=project,
                                capture_output=True, text=True, timeout=2, check=False)
        if result.returncode == 0:
            roots.update(Path(line[9:]).resolve() for line in result.stdout.split("\0")
                         if line.startswith("worktree "))
    except (OSError, subprocess.TimeoutExpired):
        pass
    return sorted(roots)


def visible_values(value, depth=0):
    """Decode model-facing tool output, including JSON carried inside a text block."""
    if depth > 8:
        return
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from visible_values(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            yield from visible_values(item, depth + 1)
    elif isinstance(value, str):
        yield value
        decoder, index = json.JSONDecoder(), 0
        while index < len(value):
            start = value.find("{", index)
            if start < 0:
                break
            try:
                item, length = decoder.raw_decode(value[start:])
                yield from visible_values(item, depth + 1)
                index = start + length
            except ValueError:
                index = start + 1


def native_identity(rows: list[dict], host: str) -> tuple[str, str]:
    if host == "codex":
        meta = next((row.get("payload", {}) for row in rows if row.get("type") == "session_meta"), {})
        session = meta.get("id") or next((row.get("thread_id") for row in rows
                                         if row.get("type") == "thread.started"), "")
        return str(session or ""), str(meta.get("cwd") or "")
    session = next((row.get("sessionId") or row.get("session_id") for row in rows
                    if row.get("sessionId") or row.get("session_id")), "")
    cwd = next((row.get("cwd") for row in rows if row.get("cwd")), "")
    return str(session), str(cwd)


def stream_usage(rows: list[dict], host: str, base: dict) -> list[dict]:
    """Completed native CLI totals are fallback observations, never additive to their transcript."""
    if host == "codex":
        usage = next((row.get("usage") for row in reversed(rows) if row.get("type") == "turn.completed"), None)
        if not isinstance(usage, dict):
            return []
        cached, written = usage.get("cached_input_tokens", 0), usage.get("cache_write_input_tokens", 0)
        return [{**base, "kind": "call", "usage_scope": "session_total", "tokens": {
            "input": max(0, usage.get("input_tokens", 0) - cached - written),
            "cache_read": cached, "cache_write": written, "output": usage.get("output_tokens", 0)}}]
    result = next((row for row in reversed(rows) if row.get("type") == "result"), {})
    models = result.get("modelUsage")
    if not isinstance(models, dict):
        return []
    return [{**base, "kind": "call", "model": model, "usage_scope": "session_total",
             "native_usd": usage.get("costUSD"), "tokens": {
                 "input": usage.get("inputTokens", 0), "output": usage.get("outputTokens", 0),
                 "cache_read": usage.get("cacheReadInputTokens", 0),
                 "cache_write": usage.get("cacheCreationInputTokens", 0)}}
            for model, usage in models.items() if isinstance(usage, dict)]


def observe(project: Path, since: float = 0.0, claude_home: Path = CLAUDE_PROJECTS,
            codex_home: Path = CODEX_SESSIONS, *, expected_sessions: list[dict] | None = None) -> dict:
    """Bounded local observation. An explicit session list disables unrelated session discovery.

    Missing, over-budget and unreadable evidence stays unknown. Text is compared in
    memory and discarded; returned records contain metadata and match results only.
    """
    roots = worktrees(project)
    deadline = time.monotonic() + MAX_SCAN_SECONDS
    coverage = {"state": "unavailable", "sources": [], "issues": [], "bytes_read": 0,
                "limits": {"files": MAX_SCAN_FILES, "bytes": MAX_SCAN_BYTES,
                           "source_bytes": MAX_SOURCE_BYTES, "seconds": MAX_SCAN_SECONDS}}
    cache, sources, reviews, expected_texts = {}, {}, {}, []
    homes = {"claude": claude_home.resolve(), "codex": codex_home.resolve()}
    allowed_homes = {host: [home] for host, home in homes.items()}
    if codex_home.name == "sessions":
        allowed_homes["codex"].append(codex_home.with_name("archived_sessions").resolve())

    def read(path: Path):
        path = path.resolve()
        if path in cache:
            return cache[path]
        try:
            size = path.stat().st_size
            if not path.is_file():
                raise OSError("not a regular file")
            if len(cache) >= MAX_SCAN_FILES or size > MAX_SOURCE_BYTES \
                    or coverage["bytes_read"] + size > MAX_SCAN_BYTES or time.monotonic() > deadline:
                coverage["issues"].append({"path": str(path), "reason": "scan_limit"})
                cache[path] = None
                return None
            limit = min(MAX_SOURCE_BYTES, MAX_SCAN_BYTES - coverage["bytes_read"])
            with path.open("rb") as handle:
                raw = handle.read(limit + 1)
            coverage["bytes_read"] += len(raw)
            if len(raw) > limit:
                coverage["issues"].append({"path": str(path), "reason": "scan_limit"})
                cache[path] = None
                return None
            cache[path] = raw
            return raw
        except OSError:
            coverage["issues"].append({"path": str(path), "reason": "missing_or_unreadable"})
            cache[path] = None
            return None

    def document(path: Path):
        raw = read(path)
        try:
            return json.loads(raw) if raw is not None else {}
        except (ValueError, UnicodeError):
            coverage["issues"].append({"path": str(path), "reason": "invalid_json"})
            return {}

    def add_source(path: Path, host: str, **metadata):
        path = path.resolve()
        sources.setdefault(path, {"host": host, **metadata})

    # Fixed runtime locations avoid walking application trees or following a receipt outside its owner.
    for root in roots:
        folder = root / RUNTIME_REVIEW
        for path in sorted(folder.glob("*.native-review.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            record = document(path)
            if not isinstance(record, dict) or record.get("host") not in homes:
                continue
            stdout = Path(record.get("stdout_path", ""))
            if not stdout.is_absolute() or not stdout.resolve().is_relative_to(folder.resolve()):
                continue
            requested = record.get("requested", {})
            reviews[stdout.resolve()] = {"host": record["host"], "role": requested.get("agent", "reviewer"),
                "model": requested.get("model", ""), "effort": requested.get("effort", ""),
                "at": epoch(record.get("finished_at")) or epoch(record.get("started_at")) or 0,
                "packet_sha256": record.get("packet_sha256"), "stdout_sha256": record.get("stdout_sha256")}
        for path in sorted(folder.glob("*/stdout.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
            if modified_since(path, since):
                add_source(path, reviews.get(path.resolve(), {}).get("host", ""), local_review=True)
        for path in folder.glob("*.methods.json"):
            records_by_name = document(path)
            if not isinstance(records_by_name, dict):
                continue
            for name, record in records_by_name.items():
                if not isinstance(record, dict) or not record.get("source_path"):
                    continue
                source = Path(record["source_path"])
                expected = {"kind": "method", "task": path.name.removesuffix(".methods.json"),
                            "name": name, "source_sha256": record.get("skill_sha256"),
                            "content_sha256": record.get("content_sha256"),
                            "state": "unknown", "_text": None}
                raw = read(source) if source.name == "SKILL.md" else None
                if raw is not None and hashlib.sha256(raw).hexdigest() == record.get("skill_sha256"):
                    try:
                        expected["_full_text"] = raw.decode("utf-8")
                        import review_execution
                        import task_lane
                        companion = name.removeprefix("companion/") if name.startswith("companion/") else None
                        excerpt = review_execution.source_excerpt(source, companion, task_lane)
                        if excerpt["sha256"] == record.get("skill_sha256"):
                            expected.update(_text=excerpt["content"], excerpt_sha256=hashlib.sha256(
                                excerpt["content"].encode()).hexdigest(), source_scope=excerpt["delivery"])
                    except (ImportError, OSError, ValueError, RuntimeError):
                        pass
                expected_texts.append(expected)

    wanted = list(expected_sessions or [])
    for session in wanted:
        for delivery in session.get("deliveries", []):
            if isinstance(delivery, dict) and delivery.get("name") and delivery.get("source_sha256"):
                expected_texts.append({"kind": "method", "state": "unknown", "_text": None,
                    **{key: delivery.get(key) for key in ("name", "task", "source_sha256", "content_sha256")}})
        if session.get("packet_sha256"):
            expected_texts.append({"kind": "review_packet", "session_id": session.get("session_id"),
                "source_sha256": session["packet_sha256"], "state": "unknown", "_text": None})
    if expected_sessions is None:
        for root in roots:
            for path, role, purpose in claude_files(root, claude_home):
                if modified_since(path, since):
                    add_source(path, "claude", role=role, purpose=purpose, project_path=True)
    for session in wanted:
        host, session_id = session.get("host"), session.get("session_id", "")
        if host not in homes or not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
            coverage["issues"].append({"reason": "invalid_expected_session"})
            continue
        if session.get("path"):
            path = Path(session["path"]).expanduser().resolve()
            if any(path.is_relative_to(home) for home in allowed_homes[host]):
                add_source(path, host, expected_id=session_id)
            else:
                coverage["issues"].append({"session_id": session_id, "reason": "transcript_outside_host_home"})

    # Local review streams also identify persistent CLI children. Search names, never unrelated contents.
    for path, source in list(sources.items()):
        if not source.get("local_review"):
            continue
        raw = read(path)
        if raw is None:
            continue
        try:
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        except (ValueError, UnicodeError):
            coverage["issues"].append({"path": str(path), "reason": "invalid_jsonl"})
            continue
        host = source["host"] or ("codex" if any(row.get("type") == "thread.started" for row in rows) else "claude")
        source["host"] = host
        session_id, _ = native_identity(rows, host)
        metadata = reviews.setdefault(path, {"host": host, "role": "reviewer", "model": "", "effort": "",
                                               "at": path.stat().st_mtime})
        if session_id:
            metadata["session_id"] = session_id
            wanted.append({"host": host, "session_id": session_id})
        packet = path.with_name("packet.txt")
        packet_raw = read(packet) if packet.is_file() else None
        if packet_raw is not None:
            digest = hashlib.sha256(packet_raw).hexdigest()
            if metadata.get("packet_sha256") in (None, digest):
                text = packet_raw.decode("utf-8", errors="replace")
                metadata["task"] = task_hint(text)
                expected_texts.append({"kind": "review_packet", "session_id": session_id,
                    "source_sha256": digest, "state": "unknown", "_text": text})

    ids = {host: {item["session_id"] for item in wanted if item.get("host") == host
                  and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(item.get("session_id", "")))} for host in homes}
    for host, home in ((host, home) for host, paths in allowed_homes.items() for home in paths):
        if not ids[host] and (host != "codex" or expected_sessions is not None):
            continue
        for directory, folders, files in os.walk(home):
            if time.monotonic() > deadline:
                coverage["issues"].append({"reason": "discovery_time_limit", "host": host})
                break
            folders.sort(reverse=True)
            files.sort(reverse=True)
            for filename in files:
                if not filename.endswith(".jsonl"):
                    continue
                path = Path(directory) / filename
                match = next((value for value in ids[host] if value in filename), None)
                if match or (host == "codex" and expected_sessions is None and modified_since(path, since)):
                    add_source(path, host, expected_id=match)
                if len(sources) >= MAX_SCAN_FILES:
                    coverage["issues"].append({"reason": "discovery_file_limit"})
                    folders.clear()
                    break
            if len(sources) >= MAX_SCAN_FILES:
                break

    by_session, summaries, seen_expected, observed_graphs = {}, {}, set(), []
    retained_sessions = {(item.get("host"), item.get("session_id"), item.get("agent_id", "")): item
                         for item in expected_sessions or []}
    for (host, session_id, agent_id), item in retained_sessions.items():
        usage = item.get("usage")
        if host not in homes or not session_id or not isinstance(usage, dict):
            continue
        raw = {"type": "turn.completed", "usage": usage} if host == "codex" else {
            "type": "result", "modelUsage": usage.get("model_usage")}
        lane = f"{host}:{session_id}" + (f"/{agent_id}" if agent_id else "")
        summaries[lane] = stream_usage([raw], host, {"at": epoch(item.get("finished_at")) or epoch(item.get("occurred_at")) or 0,
            "lane": lane, "role": item.get("role", "reviewer"), "task": item.get("task", ""),
            "branch": "", "purpose": "Independent review", "model": item.get("model", ""),
            "effort": item.get("effort", ""), "observation_source": "retained_review_record"})
    review_sessions = {value.get("session_id"): value for value in reviews.values() if value.get("session_id")}
    for path, source in sources.items():
        if time.monotonic() > deadline:
            coverage["issues"].append({"reason": "processing_time_limit"})
            break
        raw = read(path)
        if raw is None:
            continue
        rows = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
            except (ValueError, UnicodeError):
                coverage["issues"].append({"path": str(path), "reason": "invalid_jsonl"})
        host = source["host"]
        session_id, cwd = native_identity(rows, host)
        if source.get("expected_id") and session_id != source["expected_id"]:
            coverage["issues"].append({"path": str(path), "reason": "session_id_mismatch"})
            continue
        session_id = session_id or path.stem
        agent_id = ""
        if host == "claude":
            agent_id = next((row.get("agentId") for row in rows if row.get("agentId")), "")
            agent_id = agent_id or (path.stem.removeprefix("agent-") if path.stem.startswith("agent-") else "")
        retained = retained_sessions.get((host, session_id, agent_id), {})
        scoped = bool(cwd) and any(Path(cwd).resolve().is_relative_to(root) for root in roots)
        retained_scope = bool(cwd) and bool(retained.get("worktree")) and Path(cwd).resolve() == Path(retained["worktree"]).resolve()
        if not (scoped or retained_scope or source.get("project_path") or source.get("local_review") or session_id in review_sessions):
            coverage["issues"].append({"path": str(path), "reason": "project_membership_unverified"})
            continue
        seen_expected.add((host, session_id, agent_id))
        if agent_id:
            session_id = f"{session_id}/{agent_id}"
        lane = f"{host}:{session_id}"
        detail = review_sessions.get(session_id, retained)
        role = detail.get("role", source.get("role", "agent" if agent_id else "main"))
        task = detail.get("task", "")
        coverage["sources"].append({"path": str(path), "host": host, "session_id": session_id,
                                    "kind": "review_stream" if source.get("local_review") else "transcript",
                                    "membership_source": "retained_event" if retained_scope and not scoped else "current_project"})
        if source.get("local_review"):
            if detail.get("stdout_sha256") and hashlib.sha256(raw).hexdigest() != detail["stdout_sha256"]:
                coverage["issues"].append({"path": str(path), "reason": "review_stream_hash_mismatch"})
                continue
            summaries[lane] = stream_usage(rows, host, {"at": detail.get("at", 0), "lane": lane, "role": role,
                "task": task, "branch": "", "purpose": "Independent review", "model": detail.get("model", ""),
                "effort": detail.get("effort", "")})
            continue
        problems = []
        parsed = claude_events(path, role, source.get("purpose", ""), source_records=rows) if host == "claude" else \
            codex_events(path, Path(cwd), source_records=rows, problems=problems)
        if "role" not in detail and parsed:
            role = parsed[0]["role"]
        coverage["issues"].extend({"path": str(path), "reason": item} for item in set(problems))
        for event in parsed:
            event.update(lane=lane)
            if "role" in detail:
                event.update(role=role, task=task, purpose="Independent review")
        by_session.setdefault(lane, []).extend(parsed)
        for row in rows:
            if time.monotonic() > deadline:
                coverage["issues"].append({"reason": "visibility_time_limit"})
                break
            payload = row.get("payload", {})
            if not isinstance(payload, dict):
                continue
            tool_output = payload.get("type") in {"function_call_output", "custom_tool_call_output"}
            user_input = payload.get("role") == "user" or payload.get("type") == "user_message"
            content = payload.get("output") if tool_output else payload.get("content", payload.get("message"))
            if host == "claude" and row.get("type") == "user":
                content = row.get("message", {}).get("content")
                tool_output = isinstance(content, list) and any(item.get("type") == "tool_result"
                    for item in content if isinstance(item, dict))
                user_input = not tool_output
            if not (tool_output or user_input):
                continue
            for text in visible_values(content):
                for expected in expected_texts:
                    if isinstance(text, dict):
                        name = text.get("method") or (f"companion/{text['companion']}" if text.get("companion") else None)
                        returned = text.get("content")
                        matching_source = role == "main" and tool_output and expected["kind"] == "method" \
                                and expected.get("name") == name and expected.get("task") == text.get("task_id") \
                                and text.get("skill_sha256") == expected.get("source_sha256")
                        if matching_source and isinstance(returned, str) and hashlib.sha256(returned.encode()).hexdigest() == \
                                (expected.get("content_sha256") or expected.get("source_sha256")):
                            expected.update(state="observed", observed_in=lane, verification="complete_loader_content_hash")
                        action = text.get("action", {})
                        if matching_source and name == "companion/graphify" and isinstance(action, dict) \
                                and action.get("ok") is True and action.get("truncated") is False \
                                and isinstance(action.get("text"), str) and hashlib.sha256(action["text"].encode()).hexdigest() == action.get("stdout_sha256"):
                            observed_graphs.append({"kind": "graph_result", "task": expected.get("task"),
                                "state": "observed", "observed_in": lane, "source_sha256": action["stdout_sha256"],
                                "graph_sha256": action.get("graph_sha256"), "verification": "model_visible_stdout_hash",
                                "process_evidence": "loader_receipt"})
                        continue
                    eligible = (expected["kind"] == "review_packet" and user_input
                                and expected.get("session_id") == session_id) or \
                               (expected["kind"] == "method" and tool_output and role == "main")
                    if eligible and any(expected.get(key) and expected[key] in text
                                        for key in ("_text", "_full_text")):
                        expected.update(state="observed", observed_in=lane)
                    elif eligible and expected["kind"] == "review_packet" and hashlib.sha256(text.encode()).hexdigest() == expected.get("source_sha256"):
                        expected.update(state="observed", observed_in=lane, verification="retained_packet_hash")

    events = []
    for lane in set(by_session) | set(summaries):
        unique = {}
        for event in by_session.get(lane, []):
            identity = (event["kind"], event.get("event_id") or json.dumps(event, sort_keys=True))
            if identity not in unique or event.get("tokens", {}).get("output", 0) > unique[identity].get("tokens", {}).get("output", 0):
                unique[identity] = event
        observed = list(unique.values())
        calls = [event for event in observed if event["kind"] == "call"]
        totals = summaries.get(lane, [])
        if totals and (not calls or (all(sum(event["tokens"][key] for event in calls) <= sum(event["tokens"][key] for event in totals)
                                        for key in TOKEN_KEYS) and any(sum(event["tokens"][key] for event in calls) !=
                                        sum(event["tokens"][key] for event in totals) for key in TOKEN_KEYS))):
            observed = [event for event in observed if event["kind"] != "call"] + totals
        events.extend(observed)
    events = sorted((event for event in events if event["at"] >= since), key=lambda event: event["at"])
    calls = [event for event in events if event["kind"] == "call"]
    coverage["observed_sessions"] = sorted({event["lane"] for event in calls})
    for host, session_id, agent_id in {(item.get("host"), item.get("session_id"), item.get("agent_id", "")) for item in wanted}:
        lane = f"{host}:{session_id}" + (f"/{agent_id}" if agent_id else "")
        reason = "expected_session_unavailable" if (host, session_id, agent_id) not in seen_expected else \
                 "missing_usage" if lane not in coverage["observed_sessions"] else None
        if reason:
            coverage["issues"].append({"session_id": session_id, "agent_id": agent_id, "reason": reason})
    coverage["state"] = "partial" if calls and coverage["issues"] else "observed" if calls else "unavailable"
    totals = {key: sum(event["tokens"][key] for event in calls) for key in TOKEN_KEYS}
    usage = {"state": coverage["state"], "uncached_input": totals["input"], "cached_input": totals["cache_read"],
             "cache_write": totals["cache_write"], "input_including_cache": prompt_tokens(totals),
             "output": totals["output"], "cost_usd": None, "cost_source": "not_estimated"} if calls else None
    deliveries = {}
    for expected in expected_texts + observed_graphs:
        expected.pop("_text", None)
        expected.pop("_full_text", None)
        key = tuple(expected.get(name) for name in ("kind", "name", "task", "session_id", "source_sha256"))
        if key not in deliveries or expected["state"] == "observed":
            deliveries[key] = expected
    return {"events": events, "coverage": coverage, "deliveries": list(deliveries.values()), "usage": usage}


def belongs(event: dict, branch: str, start: float, end: float, task: str = "") -> bool:
    """A subagent naming a task id is that task's; else a task branch is its task's; else the main session's
    window decides. A subagent with neither belongs to no task: it is other work running at the same time."""
    if event.get("task"):
        return event["task"] == task
    if TASK_BRANCH_RE.match(event["branch"]):
        return event["branch"] == branch
    return event["role"] == "main" and start <= event["at"] <= end


def load_prices(project: Path) -> dict:
    """Packaged prices per model, with the project's .agentrof/pricing.json models on top."""
    prices = json.loads(PRICING_FILE.read_text(encoding="utf-8"))["models"]
    own = project / PROJECT_PRICING
    if own.is_file():
        prices = {**prices, **json.loads(own.read_text(encoding="utf-8")).get("models", {})}
    return prices


def rates(model: str, prices: dict) -> dict | None:
    model = model.split("[", 1)[0]
    keys = [key for key in prices if model == key or model.startswith(key + "-")]
    return prices[max(keys, key=len)] if keys else None


def prompt_tokens(tokens: dict) -> int:
    return tokens["input"] + tokens["cache_write"] + tokens["cache_read"]


def usd(call: dict, prices: dict) -> float | None:
    """One call's dollars, or None when its model has no price."""
    if call.get("usage_scope") == "session_total":
        # A session total cannot reconstruct per-request context tiers or fast-mode pricing.
        value = call.get("native_usd")
        return value if isinstance(value, (int, float)) and value >= 0 else None
    rate = rates(call.get("model", ""), prices)
    if rate is None:
        return None
    tokens = call["tokens"]
    tier = rate.get("long_context")
    if tier and prompt_tokens(tokens) > tier["above_prompt_tokens"]:
        rate = {**rate, **tier}
    hour = min(call.get("cache_write_1h") or 0, tokens["cache_write"])
    scale = rate.get("fast_multiplier", 1) if call.get("fast") else 1
    return scale * (tokens["input"] * rate["input"] + (tokens["cache_write"] - hour) * rate["cache_write_5m"]
                    + hour * rate["cache_write_1h"] + tokens["cache_read"] * rate["cache_read"]
                    + tokens["output"] * rate["output"]) / 1e6


def step_class(role: str, purpose: str) -> str:
    """main, or a spawn's class: review, security, fix or author."""
    if role == "main":
        return "main"
    text = f"{role} {purpose}".lower()
    if "reviewer" in role:
        return "security" if "security" in text else "review"
    if "mechanical" in text or re.search(r"\bfix", text):
        return "fix"
    if "security" in text:
        return "security"
    return "review" if "review" in text else "author"


def cost(events: list[dict], prices: dict | None = None) -> dict:
    """Tokens and dollars by role, model and class, the main session's part and each spawn.
    An event's ``share`` (default 1) scales it when several tasks split it."""
    prices = prices or {}
    calls = sorted((event for event in events if event["kind"] == "call"), key=lambda event: event["at"])
    by_role: dict[str, Counter] = defaultdict(Counter)
    total, main = Counter(), Counter()
    dollars = {"all": 0.0, "main": 0.0}
    by_model: Counter = Counter()
    by_class: Counter = Counter()
    unpriced, spawns = set(), {}
    for call in calls:
        share = call.get("share", 1.0)
        tokens = {key: call["tokens"][key] * share for key in TOKEN_KEYS}
        price = usd(call, prices)
        if price is None:
            unpriced.add(call["model"])
        price = (price or 0.0) * share
        by_role[call["role"]].update(tokens)
        total.update(tokens)
        dollars["all"] += price
        by_model[call["model"] or "unknown"] += price
        by_class[step_class(call["role"], call.get("purpose", ""))] += price
        if call["role"] == "main":
            main.update(tokens)
            dollars["main"] += price
            continue
        spawn = spawns.setdefault(call["lane"], {
            "role": call["role"], "model": call["model"], "purpose": call.get("purpose", "")[:80],
            "class": step_class(call["role"], call.get("purpose", "")), "effort": call["effort"],
            "startup_context": prompt_tokens(call["tokens"]), "startup_usd": round(price, 4),
            "tokens": Counter(), "usd": 0.0})
        spawn["tokens"].update(tokens)
        spawn["usd"] += price
    for spawn in spawns.values():
        spawn["tokens"] = {key: round(spawn["tokens"][key]) for key in TOKEN_KEYS}
        spawn["usd"] = round(spawn["usd"], 4)
    return {"agents": len({event["lane"] for event in events if event["role"] != "main"}),
            "models": dict(Counter(call["model"] for call in calls if call["model"])),
            "efforts": dict(Counter(call["effort"] for call in calls if call["effort"])),
            "tokens_by_role": {role: {key: round(tokens[key]) for key in TOKEN_KEYS}
                               for role, tokens in sorted(by_role.items())},
            "tokens": {key: round(total[key]) for key in TOKEN_KEYS}, "usd": round(dollars["all"], 4),
            "main": {"tokens": {key: round(main[key]) for key in TOKEN_KEYS}, "usd": round(dollars["main"], 4)},
            "spawns": list(spawns.values()),
            "by_model": {key: round(value, 4) for key, value in sorted(by_model.items())},
            "by_class": {key: round(value, 4) for key, value in sorted(by_class.items())},
            "unpriced_models": sorted(unpriced)}


def reading(events: list[dict]) -> dict:
    """Reading-waste signals: files read 5 or more times and tool results over 200 KB."""
    reads = Counter(event["path"] for event in events if event["kind"] == "read" and event["path"])
    return {"repeated_reads": {path: count for path, count in reads.most_common() if count >= REPEATED_READS},
            "results_over_200kb": sum(1 for event in events if event["kind"] == "result"
                                      and event["bytes"] > LARGE_RESULT)}


def active_minutes(events: list[dict]) -> set[int]:
    return {int(event["at"] // 60) for event in events}
