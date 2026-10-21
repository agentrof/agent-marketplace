#!/usr/bin/env python3
"""Run one plugin hook after the Python runtime floor check.

Every hook command is `python3 <plugin root>/scripts/hook_launcher.py <script>
[arguments]`, the script named from the plugin root. At or above
runtime_floor.MINIMUM the launcher runs the script in this process as
`python3 <script> [arguments]` would. Below it the script is never read, since
its syntax may need the newer Python; the launcher then stops only the
plugin's own work (below_floor) and lets every other event pass silently.

This file parses under Python 3.9 grammar and imports only the standard
library, so any python3 can run it.
"""

import os
import re
import sys

import runtime_floor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(script, arguments):
    """Run the hook script in this process, as `python3 <script>` would."""
    import runpy
    if sys.path and not getattr(sys.flags, "safe_path", False):
        # Python puts the script's directory first, symlinks resolved on POSIX.
        located = os.path.abspath(script) if os.name == "nt" else os.path.realpath(script)
        sys.path[0] = os.path.dirname(located)
    sys.argv = [script] + list(arguments)
    try:
        runpy.run_path(script, run_name="__main__")
    except Exception:
        # A crash reads as it would without the launcher: from the script's frame on.
        kind, error, trace = sys.exc_info()
        while trace is not None and trace.tb_frame.f_code.co_filename != script:
            trace = trace.tb_next
        error.__traceback__ = trace
        sys.excepthook(kind, error, trace)
        return 1
    return 0


def read_payload():
    """The hook's JSON input; {} when there is none or it is not an object."""
    import json
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        data = getattr(sys.stdin, "buffer", sys.stdin).read()
        if isinstance(data, bytes):
            data = data.decode("utf-8", "replace")
        value = json.loads(data or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def invokes(payload, entry):
    """Whether a prompt runs `entry`, as autopilot.py's typed_arguments reads it."""
    if not entry:
        return False
    name = payload.get("command_name")
    if isinstance(name, str):
        if name != entry or payload.get("expansion_type", "slash_command") != "slash_command":
            return False
        if isinstance(payload.get("command_args"), str):
            return True
    prompt = payload.get("prompt")
    if not isinstance(prompt, str):
        return False
    first = prompt.lstrip().split("\n", 1)[0].rstrip()
    for token in ("/" + entry, "$" + entry):
        if first == token or first.startswith((token + " ", token + "\t")):
            return True
    return re.match(r"\[\$" + re.escape(entry) + r"\]\([^)]*\)(?:\s+(.*))?$", first) is not None


def below_floor(name, arguments, payload):
    """Stop only the plugin's own work; every other event exits 0 silently.

    SessionStart reports the unsupported runtime as session context. The
    autopilot entry's prompt is blocked with the hosts' documented decision.
    """
    import json
    if name == "team_guard.py" and arguments[:1] == ["register"]:
        context = "\n".join((
            "AGENT_MARKETPLACE_HOOKS_ACTIVE: none",
            "AGENT_MARKETPLACE_PYTHON: unsupported (Python %s at %s)"
            % (runtime_floor.found(), sys.executable),
            runtime_floor.message()))
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "SessionStart", "additionalContext": context}}))
        return 0
    if name == "autopilot.py" and arguments[:2] == ["hook", "user-prompt"]:
        options = dict(zip(arguments[2::2], arguments[3::2]))
        if invokes(payload, options.get("--entry")):
            print(json.dumps({"decision": "block", "reason": runtime_floor.message()}))
    return 0


def main(argv):
    if len(argv) < 2:
        sys.stderr.write("usage: hook_launcher.py <script> [arguments]\n")
        return 1
    script = os.path.abspath(os.path.join(ROOT, argv[1]))
    if runtime_floor.supported():
        return run(script, argv[2:])
    try:
        return below_floor(os.path.basename(script), argv[2:], read_payload())
    except Exception:
        return 0  # an event the launcher cannot judge is not the plugin's to stop


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
