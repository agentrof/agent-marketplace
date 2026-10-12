"""Temporary idle-sleep prevention and clocks for live verification commands."""
from __future__ import annotations

import contextlib
import ctypes
import functools
import platform
import select
import shutil
import subprocess
import sys
import time


@functools.lru_cache(maxsize=1)
def windows_api():
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.SetThreadExecutionState.argtypes = [ctypes.c_uint32]
    api.SetThreadExecutionState.restype = ctypes.c_uint32
    api.QueryUnbiasedInterruptTime.argtypes = [ctypes.POINTER(ctypes.c_uint64)]
    api.QueryUnbiasedInterruptTime.restype = ctypes.c_int
    return api


def clock_sample() -> tuple[float, float | None, float | None]:
    """Wall, awake and suspend-inclusive clocks; clock adjustments cannot prove sleep."""
    wall = time.time()
    try:
        system = platform.system()
        if system == "Darwin":
            return wall, time.clock_gettime(time.CLOCK_UPTIME_RAW), time.clock_gettime(time.CLOCK_MONOTONIC_RAW)
        if system == "Linux":
            return wall, time.monotonic(), time.clock_gettime(time.CLOCK_BOOTTIME)
        if system == "Windows":
            awake = ctypes.c_uint64()
            if not windows_api().QueryUnbiasedInterruptTime(ctypes.byref(awake)):
                raise OSError("awake clock unavailable")
            return wall, awake.value / 10_000_000, time.monotonic()
    except (AttributeError, OSError):
        pass
    return wall, None, None


def macos_assertion():
    core = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    io = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
    core.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    core.CFStringCreateWithCString.restype = ctypes.c_void_p
    core.CFRelease.argtypes = [ctypes.c_void_p]
    core.CFRelease.restype = None
    io.IOPMAssertionCreateWithName.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                              ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    io.IOPMAssertionCreateWithName.restype = ctypes.c_int
    io.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
    io.IOPMAssertionRelease.restype = ctypes.c_int
    kind = core.CFStringCreateWithCString(None, b"PreventUserIdleSystemSleep", 0x08000100)
    name = core.CFStringCreateWithCString(None, b"Agent Marketplace live verification", 0x08000100)
    assertion = ctypes.c_uint32()
    try:
        if not kind or not name or io.IOPMAssertionCreateWithName(kind, 255, name, ctypes.byref(assertion)):
            raise OSError("idle-sleep assertion refused")
    finally:
        for value in (kind, name):
            if value:
                core.CFRelease(value)
    return lambda: io.IOPMAssertionRelease(assertion) == 0


def stop_inhibitor(process):
    process.stdin.close()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    finally:
        process.stdout.close()


@contextlib.contextmanager
def keep_awake():
    """Hold only for the caller's command, preserving the prior power state."""
    system = platform.system()
    state = {"backend": system, "held": False,
             "warning": "Keep the laptop lid open and connect power; manual sleep and low battery can still interrupt the run."}
    release = None
    process = None
    try:
        try:
            if system == "Darwin":
                release = macos_assertion()
            elif system == "Windows":
                api = windows_api()
                previous = api.SetThreadExecutionState(0x80000001)
                if not previous:
                    raise OSError("idle-sleep assertion refused")
                release = lambda: bool(api.SetThreadExecutionState(previous | 0x80000000))
            elif system == "Linux":
                executable = shutil.which("systemd-inhibit")
                if not executable:
                    raise OSError("systemd-inhibit unavailable")
                # Readiness is emitted only after systemd grants the inhibitor.
                process = subprocess.Popen(
                    [executable, "--what=idle:sleep", "--mode=block", "--who=Agent Marketplace",
                     "--why=Live verification", "--", sys.executable, "-I", "-u", "-c",
                     "import sys; print('1', flush=True); sys.stdin.buffer.read()"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                if not select.select([process.stdout], [], [], 3)[0] or process.stdout.read(1) != b"1":
                    raise OSError("systemd idle-sleep inhibitor unavailable")
            else:
                raise OSError("idle-sleep prevention unsupported")
            state["held"] = True
        except (AttributeError, OSError) as error:
            state["reason"] = str(error)
        yield state
    finally:
        if process is not None:
            if state["held"] and process.poll() is not None:
                state.update(held=False, reason="idle-sleep inhibitor exited before the command finished")
            stop_inhibitor(process)
        if release is not None:
            state["released"] = release()
