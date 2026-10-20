#!/usr/bin/env python3
"""Mark the bytes the packaged autopilot writer last put in its authority files.

The autopilot guard hook puts back a grant or arming record that a shell command added,
judged from the command's pre and post snapshots. A packaged write that lands
while an unrelated long command runs falls inside that command's snapshots too.
Before each write of ``grant.json`` or ``arming.json``, autopilot.py and its
user-prompt hook record the digest of the bytes they write here, beside the
hook's recovery capsules and outside the project. The hook keeps a change whose
bytes match the latest mark for that file. Only the latest write counts, so a
command that puts back older packaged bytes, such as an ended grant's active
form or a consumed arming record, is still restored.
"""

from __future__ import annotations

import hashlib
import contextlib
import os
import tempfile
from pathlib import Path


def marks_root() -> Path:
    user = getattr(os, "getuid", lambda: 0)()
    base = Path(tempfile.gettempdir()) / f"agentrof-autopilot-guard-{user}"
    root = base / "autopilot-writes"
    for path in (base, root):
        if path.is_symlink():
            raise ValueError("autopilot provenance directory is a link")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if hasattr(os, "getuid") and path.stat().st_uid != os.getuid():
            raise ValueError("autopilot provenance directory has another owner")
        os.chmod(path, 0o700)
    return root


def mark_path(directory: Path, name: str) -> Path:
    if name not in {"grant.json", "arming.json"}:
        raise ValueError("unknown autopilot authority file")
    key = hashlib.sha256(os.path.realpath(directory).encode("utf-8")).hexdigest()[:32]
    return marks_root() / f"{key}-{name}"


def _replace(path: Path, value: bytes | None) -> None:
    if path.is_symlink():
        raise ValueError("autopilot provenance mark is a link")
    if value is None:
        path.unlink(missing_ok=True)
        return
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextlib.contextmanager
def mark_lock(path: Path):
    import file_lock
    lock = path.with_name(path.name + ".lock")
    if lock.is_symlink():
        raise ValueError("autopilot provenance lock is a link")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        file_lock.lock(descriptor)
        yield
    finally:
        file_lock.unlock(descriptor)
        os.close(descriptor)


@contextlib.contextmanager
def publication(directory: Path, name: str, data: bytes):
    """Keep the pending mark invisible to readers until its authority write commits."""
    path = mark_path(directory, name)
    with mark_lock(path):
        if path.is_symlink():
            raise ValueError("autopilot provenance mark is a link")
        before = path.read_bytes() if path.exists() else None
        _replace(path, hashlib.sha256(data).hexdigest().encode("ascii"))
        try:
            yield
        except BaseException:
            _replace(path, before)
            raise


def record(directory: Path, name: str, data: bytes | None) -> None:
    path = mark_path(directory, name)
    with mark_lock(path):
        _replace(path, hashlib.sha256(data).hexdigest().encode("ascii") if data is not None else None)


def written_by_package(directory: Path, name: str, data: bytes) -> bool:
    try:
        path = mark_path(directory, name)
        with mark_lock(path):
            if path.is_symlink():
                return False
            mark = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError, ValueError):
        return False
    return mark == hashlib.sha256(data).hexdigest()
