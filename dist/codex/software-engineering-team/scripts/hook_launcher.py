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
# vault_hook.py's tool vocabulary: the file tools its pre hook checks by
# target path, and the shells.
FILE_TOOLS = ("Write", "Edit", "MultiEdit")
SHELL_TOOLS = ("Bash", "PowerShell")
PATCH_TARGET_RE = re.compile(r"^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+)$", re.MULTILINE)
AUTOPILOT_RUNTIME = (".agentrof", "agent-marketplace", ".runtime", "autopilot")


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


def parts(path):
    separators = re.escape(os.sep + (os.altsep or ""))
    return tuple(part for part in re.split("[" + separators + "]", path) if part)


def governed(path):
    """Whether vault_hook.py governs a write to `path` in a plugin project.

    A copy of vault_hook's path rules: the autopilot runtime directory
    (in_autopilot_runtime) wherever it is, and workspace/config.json and the
    workspace/docs vault (is_workspace_config, vault_root) only in a project
    whose workspace/config.json exists, so a write outside a plugin project is
    never denied.
    """
    size = len(AUTOPILOT_RUNTIME)
    candidates = [os.path.abspath(path)]
    try:
        candidates.append(os.path.realpath(path))
    except (OSError, ValueError):
        pass  # a path the system cannot resolve is judged as written
    for candidate in candidates:
        names = parts(candidate)
        if any(names[index:index + size] == AUTOPILOT_RUNTIME
               for index in range(len(names) - size + 1)):
            return True
        if os.path.basename(candidate) == "config.json" \
                and os.path.basename(os.path.dirname(candidate)) == "workspace" \
                and os.path.isfile(candidate):
            return True
        directory = os.path.dirname(candidate)
        while True:
            parent = os.path.dirname(directory)
            if os.path.basename(directory) == "docs" and os.path.basename(parent) == "workspace" \
                    and os.path.isfile(os.path.join(parent, "config.json")):
                return True
            if parent == directory:
                break
            directory = parent
    return False


def written_paths(tool, tool_input):
    """The paths a file tool or an apply_patch call writes, as vault_hook reads them."""
    if tool in FILE_TOOLS:
        path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
        return [path] if isinstance(path, str) and path else []
    if tool != "apply_patch":
        return []
    patch = tool_input if isinstance(tool_input, str) else ""
    for key in ("command", "patch", "patchText", "input", "text"):
        value = tool_input.get(key) if isinstance(tool_input, dict) else None
        if not patch and isinstance(value, str) and value.strip():
            patch = value
    return [target.strip() for target in PATCH_TARGET_RE.findall(patch) if target.strip()]


def runs_plugin_script(command):
    """Whether a shell command names a script under this plugin's root.

    The resolved root is the AGENT_MARKETPLACE_SCRIPTS directory's parent.
    """
    text = command.replace("\\", "/")
    flags = re.IGNORECASE if os.name == "nt" else 0
    for root in sorted({os.path.abspath(ROOT), os.path.realpath(ROOT)}):
        prefix = re.escape(root.replace("\\", "/").rstrip("/") + "/")
        if re.search(prefix + r"[^\s\"'`;|&<>()]*\.py(?![\w.])", text, flags):
            return True
    return False


def plugin_work(payload):
    """Whether a pre-tool event is the plugin's own work.

    That is a write to a path the vault hook governs in a plugin project, or
    a shell command that runs one of this plugin's scripts. A raw shell write
    to a governed file is not checked: the plugin's own flows are halted.
    """
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if tool in SHELL_TOOLS:
        return isinstance(tool_input, dict) and any(
            isinstance(tool_input.get(key), str) and runs_plugin_script(tool_input[key])
            for key in ("command", "cmd"))
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else ""
    return any(governed(os.path.join(cwd, path)) for path in written_paths(tool, tool_input))


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

    SessionStart reports the unsupported runtime as session context. The vault
    hook's pre event denies the plugin's own work as each host's deny reads it,
    stderr and exit 2. The autopilot entry's prompt is blocked with the hosts'
    documented decision.
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
    if name == "vault_hook.py" and arguments[:1] == ["pre"] and plugin_work(payload):
        sys.stderr.write(runtime_floor.message() + "\n")
        return 2
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
