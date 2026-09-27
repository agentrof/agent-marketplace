#!/usr/bin/env python3
"""Replace one file atomically with the mode a plain write gives it.

tempfile.mkstemp creates its file owner-only and os.replace keeps that mode,
so a replacement sets the mode before it lands: an existing file keeps its
own mode, and a new file takes 0666 less the process umask, as open() gives
it. Text is written as UTF-8 with LF line endings on every OS.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import Callable


def replacement_mode(path: Path) -> int:
    """The existing file's mode, else the mode open() gives a new file."""
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        # Reading the umask sets it for an instant; package scripts run one thread.
        umask = os.umask(0)
        os.umask(umask)
        return 0o666 & ~umask


def discard(temporary: Path) -> None:
    """Remove a temporary file that took a read-only mode from its target.

    Windows refuses to delete a read-only file, which is the mode the
    temporary copies from a read-only target whose replacement then fails.
    """
    try:
        temporary.unlink(missing_ok=True)
    except PermissionError:
        os.chmod(temporary, stat.S_IREAD | stat.S_IWRITE)
        temporary.unlink(missing_ok=True)


def replace_bytes(path: Path, data: bytes,
                  before_replace: Callable[[], None] | None = None) -> None:
    """Write data beside path, then replace path with it in one step."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, replacement_mode(path))
        if before_replace is not None:
            before_replace()
        os.replace(temporary, path)
    except BaseException:
        discard(temporary)
        raise


def replace_text(path: Path, text: str,
                 before_replace: Callable[[], None] | None = None) -> None:
    """Replace path with text as UTF-8, keeping its LF line endings."""
    replace_bytes(path, text.encode("utf-8"), before_replace)
