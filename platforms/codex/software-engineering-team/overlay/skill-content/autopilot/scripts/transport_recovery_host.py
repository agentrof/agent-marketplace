"""The codex host's explicit-session recovery command; permissions remain unchanged."""
import os
import shutil
import subprocess


def resume_command(session, prompt):
    executable = shutil.which("codex")
    if executable is None:
        raise ValueError("codex CLI is unavailable for recovery")
    return [executable, "exec", "resume", session, "--json", prompt]


def spawn_options():
    return ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt" else {"start_new_session": True})
