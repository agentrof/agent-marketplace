#!/usr/bin/env python3
"""Mark the bytes the packaged autopilot writer last put in its authority files.

The vault hook puts back a grant or arming record that a shell command added,
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
import os
import tempfile
from pathlib import Path


def marks_root() -> Path:
    user = getattr(os, "getuid", lambda: 0)()
    return Path(tempfile.gettempdir()) / f"agentrof-vault-hook-{user}" / "autopilot-writes"


def mark_path(directory: Path, name: str) -> Path:
    key = hashlib.sha256(os.path.realpath(directory).encode("utf-8")).hexdigest()[:32]
    return marks_root() / f"{key}-{name}"


def record(directory: Path, name: str, data: bytes | None) -> None:
    """Mark ``data`` as the packaged writer's latest bytes; None clears the mark."""
    path = mark_path(directory, name)
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(hashlib.sha256(data).hexdigest().encode("ascii"))
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def written_by_package(directory: Path, name: str, data: bytes) -> bool:
    try:
        mark = mark_path(directory, name).read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return False
    return mark == hashlib.sha256(data).hexdigest()
