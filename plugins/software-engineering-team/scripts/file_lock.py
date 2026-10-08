#!/usr/bin/env python3
"""Hold an exclusive or shared file lock that ends with the process holding it.

A host with fcntl takes an flock on the open file. Native Windows has no
fcntl, so there the lock covers the file's first byte, which Windows allows on
an empty file too: an exclusive lock through msvcrt, a shared one through
LockFileEx, and the two conflict like the flock modes. Either lock ends when
its descriptor closes or its process exits, so a holder that crashes leaves no
stale lock.
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


def _windows_region(descriptor: int, shared: bool, unlock: bool) -> bool:
    """LockFileEx/UnlockFileEx over the first byte; False on lock contention."""
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class Overlapped(ctypes.Structure):
        _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                    ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    region = Overlapped()
    handle = msvcrt.get_osfhandle(descriptor)
    if unlock:
        function = kernel.UnlockFileEx
        function.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                             wintypes.DWORD, ctypes.POINTER(Overlapped)]
        arguments = (handle, 0, 1, 0, ctypes.byref(region))
    else:
        function = kernel.LockFileEx
        function.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                             wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
        # LOCKFILE_FAIL_IMMEDIATELY, plus LOCKFILE_EXCLUSIVE_LOCK for an exclusive lock.
        arguments = (handle, 1 | (0 if shared else 2), 0, 1, 0, ctypes.byref(region))
    function.restype = wintypes.BOOL
    if function(*arguments):
        return True
    error = ctypes.get_last_error()
    if not unlock and error == 33:  # ERROR_LOCK_VIOLATION
        return False
    raise ctypes.WinError(error)


def try_lock(descriptor: int, *, shared: bool = False) -> bool:
    """Take the lock at once; False while a conflicting holder has it."""
    fcntl = _flock()
    if fcntl is not None:
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True
    if shared:
        return _windows_region(descriptor, True, False)
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


def acquire(descriptor: int, *, shared: bool = False, timeout: float) -> bool:
    """Take the lock within ``timeout`` seconds; False when it stays held."""
    deadline = time.monotonic() + timeout
    while not try_lock(descriptor, shared=shared):
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(POLL_SECONDS, max(0, deadline - time.monotonic())))
    return True


def unlock(descriptor: int, *, shared: bool = False) -> None:
    """Release the lock this descriptor holds."""
    fcntl = _flock()
    if fcntl is not None:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return
    if shared:
        _windows_region(descriptor, True, True)
        return
    import msvcrt
    os.lseek(descriptor, 0, os.SEEK_SET)
    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
