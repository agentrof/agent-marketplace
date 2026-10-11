"""The claude host's explicit-session recovery command; permissions remain unchanged."""
import os
import shutil
import subprocess


def resume_command(session, prompt):
    executable = shutil.which("claude")
    if executable is None:
        raise ValueError("claude CLI is unavailable for recovery")
    return [executable, "--resume", session, "--print", "--output-format", "stream-json", "--verbose", prompt]


def spawn_options():
    return ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt" else {"start_new_session": True})
