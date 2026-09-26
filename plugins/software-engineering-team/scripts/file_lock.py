#!/usr/bin/env python3
"""Hold an exclusive file lock that ends with the process holding it.

A host with fcntl takes an flock on the open file. Native Windows has no
fcntl, so there the lock covers the file's first byte through msvcrt, which
Windows allows on an empty file too. Either lock ends when its descriptor
closes or its process exits, so a holder that crashes leaves no stale lock.
"""

from __future__ import annotations

import os
import time

POLL_SECONDS = 0.05


def _flock():
    """The fcntl module, or None on a host without it, such as native Windows."""
    try:
        import fcntl
    except ImportError:
        return None
    return fcntl


def try_lock(descriptor: int) -> bool:
    """Take the lock at once; False while another holder has it."""
    fcntl = _flock()
    if fcntl is not None:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True
    import msvcrt
    os.lseek(descriptor, 0, os.SEEK_SET)
    try:
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
    except PermissionError:
        # EACCES: another open file holds the byte. Any other error is no
        # contention and raises, so a waiting caller cannot spin on it.
        return False
    return True


def lock(descriptor: int) -> None:
    """Take the lock, waiting for as long as another holder has it."""
    fcntl = _flock()
    if fcntl is not None:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return
    while not try_lock(descriptor):
        time.sleep(POLL_SECONDS)


def unlock(descriptor: int) -> None:
    """Release the lock this descriptor holds."""
    fcntl = _flock()
    if fcntl is not None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return
    import msvcrt
    os.lseek(descriptor, 0, os.SEEK_SET)
    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
