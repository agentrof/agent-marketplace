#!/usr/bin/env python3
"""Cost and activity of fast-lane tasks, read from Claude Code and Codex transcripts.

``scan`` streams the project's transcripts once and returns every model call and
tool event with its time, lane, role, model, effort, tokens and git branch. The
retrospective attributes an event to a task by one rule (``belongs``): a subagent
whose spawn description or first prompt names exactly one task id (``TK-###``)
is that task's; else a record on a ``task/...`` branch is that branch's task;
else a main-session event on the base branch falls in every task window that
holds it and its cost is split evenly between them. A subagent with neither a
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

Claude Code: ``~/.claude/projects/<project key>/*.jsonl`` and their
``*/subagents/agent-*.jsonl`` with the ``agent-*.meta.json`` naming the agent
type. Codex: ``~/.codex/sessions/**/*.jsonl`` whose session ``cwd`` lies in the
project. A missing or unreadable transcript counts nothing.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CODEX_SESSIONS = Path.home() / ".codex" / "sessions"
TOKEN_KEYS = ("input", "output", "cache_read", "cache_write")
LARGE_RESULT = 200 * 1024
REPEATED_READS = 5
PRICING_FILE = Path(__file__).resolve().parent.parent / "skill-content" / "retrospective" / "data" / "pricing.json"
PROJECT_PRICING = Path(".agentrof") / "pricing.json"
TASK_ID_RE = re.compile(r"\b(?:TK|tk)-([0-9]{3,})\b")


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


def claude_events(path: Path, role: str, purpose: str = "") -> list[dict]:
    events, calls, task = [], {}, ""
    if role != "main":
        first = next((record for record in records(path) if record.get("type") == "user"), {})
        task = task_hint(purpose, text_of((first.get("message") or {}).get("content")))
    for record in records(path):
        at = epoch(record.get("timestamp"))
        if at is None:
            continue
        branch = record.get("gitBranch") or ""
        message = record.get("message") if isinstance(record.get("message"), dict) else {}
        base = {"at": at, "branch": branch, "role": role, "lane": f"claude:{path.stem}", "task": task,
                "purpose": purpose}
        if record.get("type") == "assistant":
            model = str(message.get("model") or "")
            if model.startswith("<"):
                continue
            usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
            tokens = {"input": usage.get("input_tokens") or 0, "output": usage.get("output_tokens") or 0,
                      "cache_read": usage.get("cache_read_input_tokens") or 0,
                      "cache_write": usage.get("cache_creation_input_tokens") or 0}
            split = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
            for block in message.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == "Read":
                    events.append({**base, "kind": "read", "path": str((block.get("input") or {}).get("file_path") or "")})
            key = message.get("id") or record.get("uuid")
            # A reply streams as several records of one id; the largest output is its final count.
            if key in calls and calls[key]["tokens"]["output"] >= tokens["output"]:
                continue
            event = {**base, "kind": "call", "model": model,
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
    branch = str((meta.get("git") or {}).get("branch") or "") if isinstance(meta.get("git"), dict) else ""
    task = ""
    if role != "main":
        first = next((record for record in records(path)
                      if (record.get("payload") or {}).get("type") == "user_message"), {})
        task = task_hint((first.get("payload") or {}).get("message"))
    events, model, effort = [], "", ""
    for record in records(path):
        at = epoch(record.get("timestamp"))
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if record.get("type") == "turn_context":
            model, effort = str(payload.get("model") or model), str(payload.get("effort") or effort)
            continue
        if at is None:
            continue
        base = {"at": at, "branch": branch, "role": role, "lane": f"codex:{path.stem}", "task": task,
                "purpose": ""}
        info = payload.get("info") if isinstance(payload.get("info"), dict) else None
        if payload.get("type") == "token_count" and info and isinstance(info.get("last_token_usage"), dict):
            usage = info["last_token_usage"]
            cached = usage.get("cached_input_tokens") or 0
            written = usage.get("cache_write_input_tokens") or 0
            # Codex input_tokens include the cached and cache-written part.
            events.append({**base, "kind": "call", "model": model, "effort": effort,
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
    """Events at or after *since*; a transcript last written before it is skipped unread."""
    events = []
    for path, role, purpose in claude_files(project, claude_home):
        if modified_since(path, since):
            events += claude_events(path, role, purpose)
    for path in sorted(codex_home.rglob("*.jsonl")) if codex_home.is_dir() else []:
        if modified_since(path, since):
            events += codex_events(path, project)
    return sorted((event for event in events if event["at"] >= since), key=lambda event: event["at"])


def belongs(event: dict, branch: str, start: float, end: float, task: str = "") -> bool:
    """A subagent naming a task id is that task's; else a task branch is its task's; else the main session's
    window decides. A subagent with neither belongs to no task: it is other work running at the same time."""
    if event.get("task"):
        return event["task"] == task
    if event["branch"].startswith("task/"):
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
