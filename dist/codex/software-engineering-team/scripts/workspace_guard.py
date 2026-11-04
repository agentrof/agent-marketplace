#!/usr/bin/env python3
"""Guard workspace layout and direct procedure-output delivery.

Recognizable task_lane.py method/companion source loads must return directly,
with sufficient explicit output capacity. Evidence-only updates may be filtered.
This covers shell calls visible to this hook, not arbitrary interpreter wrappers.

pre, a file tool (Write, Edit, MultiEdit, apply_patch): a write that would
create a new folder directly under ``workspace/`` or ``workspace/docs/`` is
denied unless ``workspace_layout`` in ``workspace/config.json`` allows it.
Folders that already exist stay writable, so a project with older folders keeps
working until it moves them. A shell command (Bash, PowerShell) is checked for
``mkdir`` and ``>``/``>>`` redirect targets. Without ``workspace/config.json``
the project is not set up yet and everything passes; a config that exists but
cannot be read or is invalid denies the write and logs it, since a guard whose
data source fails must not pass.

shortcut: only plain `mkdir` and `>`/`>>` targets are parsed; cp, mv, tee,
touch, New-Item, interpreter writes and variables in paths pass. Parse more
when agents are seen routing around the guard.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

import project_config
from autopilot_guard import TOOL_NAME_CANON, canonical_shell_command, has_shell_composition, read_payload, written_paths

MKDIR_RE = re.compile(r"(?<![\w./-])mkdir\s+([^;&|<>()]+)")
REDIRECT_RE = re.compile(r">>?\s*([^\s;&|<>()]+)")
EVIDENCE_OPTIONS = {"--task", "--name", "--host", "--source", "--unavailable", "--evidence"}
SHELL_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def deny(message: str) -> int:
    print(f"workspace guard: {message}", file=sys.stderr)
    return 2


def log(root: Path, message: str) -> None:
    try:
        runtime = root / ".agentrof" / "agent-marketplace" / ".runtime"
        runtime.mkdir(parents=True, exist_ok=True)
        with (runtime / "workspace-guard.log").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}\n")
    except OSError:
        pass


def shell_targets(command: str) -> list[str]:
    """Paths a plain ``mkdir`` creates as folders (with a ``/_`` child) and redirect targets."""
    targets = []
    for match in MKDIR_RE.finditer(command):
        try:
            words = shlex.split(match.group(1))
        except ValueError:
            words = match.group(1).split()
        targets += [f"{word.rstrip('/')}/_" for word in words if not word.startswith("-")]
    for match in REDIRECT_RE.finditer(command):
        if not match.group(1).startswith("&"):  # 2>&1 duplicates a stream, writes no file
            targets.append(match.group(1).strip("'\""))
    return targets


def loads_procedure(command: str) -> bool:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()\n")
    lexer.whitespace_split = True
    lexer.whitespace = " \t\r"
    try:
        words = list(lexer)
    except ValueError:
        return "task_lane.py" in command and any(word in command for word in ("method", "companion"))
    tokens, variables, command_start = [], {}, True
    for word in words:
        expanded = SHELL_VARIABLE.sub(lambda match: variables.get(match[1] or match[2], match[0]), word)
        name, separator, value = expanded.partition("=")
        if command_start and separator and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            # Recognition only. Never evaluate substitutions or read ambient variables.
            if "$" not in value and "`" not in value:
                variables[name] = value
            else:
                variables.pop(name, None)
            continue
        tokens.append(expanded)
        command_start = bool(word) and all(char in ";&|<>()\n" for char in word)
    for index, token in enumerate(tokens):
        if token.replace("\\", "/").rsplit("/", 1)[-1] != "task_lane.py":
            continue
        arguments = tokens[index + 1:]
        if arguments[:1] == ["--project"]:
            arguments = arguments[2:]
        elif arguments and arguments[0].startswith("--project="):
            arguments = arguments[1:]
        if not arguments or arguments[0] not in ("method", "companion"):
            continue
        if arguments[1:2] in (["--help"], ["-h"]):
            continue
        values = {}
        position = 1
        while position < len(arguments):
            option = arguments[position]
            if option and all(char in ";&|<>()\n" for char in option):
                break
            name, separator, value = option.partition("=")
            if name not in EVIDENCE_OPTIONS:
                return True
            position += 1
            if not separator:
                if position >= len(arguments):
                    return True
                value = arguments[position]
                position += 1
            values[name] = value
        if not values.get("--evidence"):
            return True
    return False


def procedure_output_refusal(payload: dict, command: str) -> str | None:
    if not loads_procedure(command):
        return None
    direct = command.strip()
    if payload.get("tool_name") == "PowerShell" and direct.startswith("& "):
        direct = direct[2:].lstrip()
    # A single leading directory change is needed by hosts without a per-call cwd.
    prefix, separator, suffix = direct.partition("&&")
    if separator and not has_shell_composition(prefix):
        try:
            words = shlex.split(prefix)
        except ValueError:
            words = []
        if len(words) == 2 and words[0] == "cd":
            direct = suffix.strip()
    if has_shell_composition(direct):
        return ("return the bounded method/companion output directly, without pipes, redirection or wrappers. "
                "run one retrieval using task_lane.py --project <path> method|companion, or a single cd <path> && retrieval")
    limit = payload.get("tool_input", {}).get("max_output_tokens")
    if limit is not None:
        policy = Path(project_config.__file__).resolve().parent.parent / "skill-content/review/data/review-policy.json"
        try:
            minimum = json.loads(policy.read_text(encoding="utf-8"))["procedure_min_output_tokens"]
        except (OSError, ValueError, KeyError) as error:
            return f"cannot verify procedure output capacity: {error}"
        if isinstance(limit, bool) or not isinstance(limit, (int, float)) or limit < minimum:
            return f"bounded procedure retrieval needs max_output_tokens of at least {minimum}. Actual output remains bounded"
    return None


def refusal(path: Path) -> str | None:
    """Why writing ``path`` would create a folder the workspace layout refuses."""
    for workspace in path.parents:
        if workspace.name != "workspace" or not (workspace / "config.json").is_file():
            continue
        parts = path.relative_to(workspace).parts
        try:
            config = json.loads((workspace / "config.json").read_text(encoding="utf-8"))
            layout = project_config.setting(config, "workspace_layout")
            levels = (("workspace", workspace, parts[:-1]),
                      ("docs", workspace / "docs", parts[1:-1] if parts[0] == "docs" else ()))
            allowed = {key: set(layout[key]) for key, _base, _rest in levels}
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
            if parts == ("config.json",):
                return None  # the config itself stays writable so it can be repaired
            message = (f"cannot read {workspace / 'config.json'} ({exc}); write denied until"
                       " the config is valid again")
            log(workspace.parent, message)
            return message
        for key, base, rest in levels:
            if rest and rest[0] not in allowed[key] and not (base / rest[0]).is_dir():
                where = "workspace/" + ("docs/" if key == "docs" else "")
                return (f"{where}{rest[0]}/ is not a folder workspace_layout.{key} in"
                        " workspace/config.json allows. Put project-specific data under"
                        " workspace/project/<name>/, or add the folder through the"
                        " configure entry.")
        return None
    return None


def main() -> int:
    payload = read_payload()
    raw_tool = str(payload.get("tool_name", ""))
    tool = TOOL_NAME_CANON.get(raw_tool, raw_tool)
    if tool in ("Bash", "PowerShell"):
        command, error = canonical_shell_command(payload.get("tool_input"))
        message = error or procedure_output_refusal(payload, command)
        if message:
            return deny(message)
        paths = shell_targets(command)
    elif tool in ("Write", "Edit"):
        paths = written_paths(raw_tool, payload.get("tool_input"))
    else:
        return 0
    cwd = str(payload.get("cwd") or "")
    for file_path in paths:
        message = refusal(Path(os.path.abspath(os.path.join(cwd, file_path))))
        if message:
            return deny(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
