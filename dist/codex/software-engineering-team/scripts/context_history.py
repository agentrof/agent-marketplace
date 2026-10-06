"""Read exact hash-bound document versions from the owning Git checkout."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import subprocess

import ba_compile


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
        actual = operation_compile.receipt_hash(props, body)
    elif kind in {"story", "test-plan", "backlog", "epic", "epic-review", "backlog-review"}:
        import backlog_compile
        actual = backlog_compile.digest_text(text)
    elif kind == "requirement":
        import requirement_compile
        actual = requirement_compile.semantic_hash(props, body)
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
    log = subprocess.run(["git", "--no-replace-objects", "-C", str(project),
                          "log", "--format=%H", "--", relative], capture_output=True)
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
        return {"current": False, "unit_id": f"{path}::commit:{revision}",
                "path": path, "kind": "document", "label": str(props.get("title", path)),
                "ranges": [[1, len(raw.decode("utf-8").splitlines())]],
                "source_hash": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "content_hash": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw), "git_revision": revision, "historical": True,
                "bound_hash": expected, "historical_properties": props}
    return None
