"""Read exact hash-bound document versions from the owning Git checkout."""

from __future__ import annotations

import hashlib
import copy
from collections import OrderedDict
import json
from pathlib import Path
import re
import subprocess
import threading

import ba_compile

HISTORY_POLICY = Path(__file__).resolve().parents[1] / "templates/project-context-policy.json"
_HISTORY_CACHE = OrderedDict()
_HISTORY_CACHE_BYTES = 0
_HISTORY_LOCK = threading.RLock()


def history_limit() -> int:
    limit = json.loads(HISTORY_POLICY.read_text(encoding="utf-8"))["reading_state"]["max_history_bytes"]
    if type(limit) is not int or limit <= 0:
        raise ValueError("reading_state.max_history_bytes must be a positive integer")
    return limit


def forget_history(key) -> None:
    global _HISTORY_CACHE_BYTES
    with _HISTORY_LOCK:
        record = _HISTORY_CACHE.pop(key, None)
        if record is not None:
            _HISTORY_CACHE_BYTES -= record[1]


def retain_history(key, unit: dict, limit: int) -> None:
    global _HISTORY_CACHE_BYTES
    size = len(json.dumps([key, unit], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    with _HISTORY_LOCK:
        forget_history(key)
        if size > limit:
            return
        while _HISTORY_CACHE and _HISTORY_CACHE_BYTES + size > limit:
            forget_history(next(iter(_HISTORY_CACHE)))
        _HISTORY_CACHE[key] = (copy.deepcopy(unit), size)
        _HISTORY_CACHE_BYTES += size


def is_project_repository(project: Path) -> bool:
    result = subprocess.run(["git", "--no-replace-objects", "-C", str(project),
                             "rev-parse", "--show-toplevel"], capture_output=True)
    return not result.returncode and Path(result.stdout.decode().strip()).resolve() == project.resolve()


def git_source(project: Path, path: str, revision: str) -> bytes:
    if not re.fullmatch(r"[a-f0-9]{40,64}", revision):
        raise ValueError("historical source needs an exact Git commit")
    relative = Path(path)
    if relative.is_absolute() or ".." in relative.parts or "\\" in path:
        raise ValueError("historical source path must stay in the project")
    if not is_project_repository(project):
        raise ValueError("historical source requires the selected project's Git root")
    result = subprocess.run(["git", "--no-replace-objects", "-C", str(project),
                             "show", f"{revision}:{path}"], capture_output=True)
    if result.returncode:
        raise ValueError("historical source is unavailable")
    return result.stdout


def matches(path: Path, raw: bytes, expected: str) -> bool:
    text = raw.decode("utf-8")
    props, start, error = ba_compile.parse_frontmatter(text)
    if error:
        return False
    body = "\n".join(text.splitlines()[start - 1:])
    kind = props.get("type")
    if kind in {"verification-contract", "environment-contract"}:
        import operation_compile
        props, body = operation_compile.parse_text(text, path)
        actual = operation_compile.receipt_hash(props, body)
    elif kind in {"story", "test-plan", "backlog", "epic", "epic-review", "backlog-review"}:
        import backlog_compile
        actual = backlog_compile.digest_text(text)
    elif kind == "requirement":
        import requirement_compile
        actual = requirement_compile.semantic_hash(props, body.lstrip("\n"))
    else:
        return False
    return actual == expected and props.get("source_hash") == expected


def bound_source(project: Path, path: str, expected: str) -> dict | None:
    """A matching stamp alone is insufficient: recompute the owning digest."""
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", expected):
        return None
    relative = "workspace/docs/" + path
    current = project / relative
    if current.is_file() and matches(current, current.read_bytes(), expected):
        return {"current": True}
    if not is_project_repository(project):
        return None
    project = project.resolve()
    head = subprocess.run(["git", "--no-replace-objects", "-C", str(project),
                           "rev-parse", "--verify", "HEAD^{commit}"], capture_output=True)
    commit = head.stdout.decode("ascii").strip() if not head.returncode else ""
    if not re.fullmatch(r"[a-f0-9]{40,64}", commit):
        return None
    key = (str(project), path, expected, commit)
    limit = history_limit()
    with _HISTORY_LOCK:
        while _HISTORY_CACHE and _HISTORY_CACHE_BYTES > limit:
            forget_history(next(iter(_HISTORY_CACHE)))
        cached = _HISTORY_CACHE.get(key) if re.fullmatch(r"[a-f0-9]{40,64}", commit) else None
    if cached is not None:
        try:
            raw = git_source(project, relative, cached[0]["git_revision"])
            if ("sha256:" + hashlib.sha256(raw).hexdigest() == cached[0]["source_hash"]
                    and matches(current, raw, expected)):
                with _HISTORY_LOCK:
                    if key in _HISTORY_CACHE:
                        _HISTORY_CACHE.move_to_end(key)
                return copy.deepcopy(cached[0])
        except ValueError:
            pass
        forget_history(key)
    log = subprocess.run(["git", "--no-replace-objects", "-C", str(project),
                          "log", "--format=%H", commit, "--", relative], capture_output=True)
    if log.returncode:
        return None
    for revision in log.stdout.decode("ascii").split():
        try:
            raw = git_source(project, relative, revision)
        except ValueError:
            continue
        if not matches(current, raw, expected):
            continue
        props, _start, _error = ba_compile.parse_frontmatter(raw.decode("utf-8"))
        unit = {"current": False, "unit_id": f"{path}::commit:{revision}",
                "path": path, "kind": "document", "label": str(props.get("title", path)),
                "ranges": [[1, len(raw.decode("utf-8").splitlines())]],
                "source_hash": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "content_hash": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw), "git_revision": revision, "historical": True,
                "bound_hash": expected, "historical_properties": props}
        if re.fullmatch(r"[a-f0-9]{40,64}", commit):
            retain_history(key, unit, limit)
        return unit
    return None
