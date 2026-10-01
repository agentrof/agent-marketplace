#!/usr/bin/env python3
"""Read Claude Code's own model list from the binary that runs this session.

Interface, the same on both hosts; `host_listing.py` defines the listing:

- list_models(environ=os.environ, timeout=30.0, min_version=None) -> listing
- verdict(model_id, listing) -> "available" | "unavailable" | "unverified"
- efforts(model_id, listing) -> the listed effort levels, or None

Binary: `CLAUDE_CODE_EXECPATH`, which Claude Code sets for the commands it
runs without documenting it, else the executable of `CLAUDE_PID`, the
documented process id of Claude Code (2.1.214 or later), else `claude` on
the PATH of `environ` when its version meets `min_version`, the catalog's
`min_cli_version`; without `min_version` no `claude` on PATH is used, since an
older one would judge a newer pin unavailable. Every candidate must print a
Claude Code version, which the Node of an npm install does not.

List: the `models` of the binary's reply to one `initialize` control request,
sent with no user message, so no model request is made, with every hook off
and with no MCP server loaded. A model id is a row's `resolvedModel` without
its context window suffix, such as `[1m]`, with the row's
`supportedEffortLevels`; a model that takes no effort lists none.

View: `account` only when the reply's `account.tokenSource` names a
claude.ai login, the only sign-in for which Claude Code serves the account's
own catalog; otherwise `generic`: a gateway token, an `apiKeyHelper`, an API
key, no sign-in or a source this module does not know gets the built-in rows.

Every failure, a timeout included, gives a listing that judges every model
`unverified`; list_models never raises.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Iterator, Mapping, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import host_listing
from host_listing import CommandFailed, efforts, verdict  # noqa: F401

HOST = "claude"
# `-p` starts the project's `.mcp.json` servers without the approval an
# interactive session asks for, and every user and plugin server;
# `--strict-mcp-config` with no `--mcp-config` loads none. Claude Code refuses
# the flag under an organization's managed MCP config, which gives `unverified`.
PROBE = ("-p", "--input-format", "stream-json", "--output-format", "stream-json",
         "--verbose", "--no-session-persistence", "--strict-mcp-config",
         "--settings", '{"disableAllHooks":true}')
REQUEST_ID = "host_models_initialize"
VERSION_RE = re.compile(r"(\d+\.\d+\.\d+) \(Claude Code\)")
# The token sources of a claude.ai login: `/login`, and its OAuth token
# passed in the environment or a file descriptor.
CLAUDE_AI_SOURCES = ("claude.ai", "CLAUDE_CODE_OAUTH_TOKEN",
                     "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR")
CONTEXT_SUFFIX_RE = re.compile(r"\[[^\]]*\]$")
NOT_A_LIST = "its `initialize` models are not a model list"


def claude_version(binary: str, environ: Mapping[str, str],
                   deadline: host_listing.Deadline) -> Optional[str]:
    match = VERSION_RE.fullmatch(host_listing.version_line([binary], deadline, environ) or "")
    return match.group(1) if match else None


def session_binaries(environ: Mapping[str, str],
                     deadline: host_listing.Deadline) -> Iterator[str]:
    """The executables that may run this session, in order."""
    if environ.get("CLAUDE_CODE_EXECPATH"):
        yield environ["CLAUDE_CODE_EXECPATH"]
    pid = environ.get("CLAUDE_PID", "")
    if pid.isdigit():
        executable = host_listing.process_executable(int(pid), deadline)
        if executable:
            yield executable


def locate(environ: Mapping[str, str], deadline: host_listing.Deadline,
           min_version: Optional[str]) -> tuple:
    """Return the binary and its version, or None, None and why there is none."""
    for binary in session_binaries(environ, deadline):
        version = claude_version(binary, environ, deadline)
        if version:
            return binary, version, ""
    on_path = host_listing.which("claude", environ)
    if on_path is None:
        return None, None, ("no Claude Code binary: CLAUDE_CODE_EXECPATH and CLAUDE_PID name"
                            " none, and no `claude` is on PATH")
    floor = host_listing.release(min_version)
    if floor is None:
        return None, None, (f"no Claude Code binary runs this session, and `claude` on PATH"
                            f" ({on_path}) is used only with the catalog's min_cli_version")
    version = claude_version(on_path, environ, deadline)
    if version is None:
        return None, None, f"`claude` on PATH ({on_path}) prints no Claude Code version"
    if host_listing.release(version) < floor:
        return None, None, f"`claude` on PATH ({on_path}) is {version}, below {min_version}"
    return on_path, version, ""


def reply(out: str) -> Optional[dict]:
    """Return the reply to this module's `initialize` request, or None."""
    for line in out.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        response = event.get("response") \
            if isinstance(event, dict) and event.get("type") == "control_response" else None
        if isinstance(response, dict) and response.get("request_id") == REQUEST_ID:
            return response
    return None


def listed_models(rows: object) -> dict:
    """Map each model id of the reply's rows to the effort levels it supports."""
    if not isinstance(rows, list):
        raise CommandFailed("its `initialize` reply holds no model list")
    models: dict = {}
    for row in rows:
        if not isinstance(row, dict):
            raise CommandFailed(NOT_A_LIST)
        levels = row.get("supportedEffortLevels") or []
        if not isinstance(levels, list) or not all(isinstance(level, str) for level in levels):
            raise CommandFailed(NOT_A_LIST)
        name = row.get("resolvedModel") or row.get("value")
        if not isinstance(name, str) or not name or name == "default":
            continue
        known = models.setdefault(CONTEXT_SUFFIX_RE.sub("", name), {"efforts": []})["efforts"]
        known.extend(level for level in levels if level not in known)
    if not models:
        raise CommandFailed("it lists no model")
    return models


def list_models(environ: Mapping[str, str] = os.environ,
                timeout: float = host_listing.DEFAULT_TIMEOUT,
                min_version: Optional[str] = None) -> dict:
    """Read the model list of the Claude Code binary that runs this session."""
    deadline = host_listing.Deadline(timeout)
    binary = version = None
    try:
        binary, version, why = locate(environ, deadline, min_version)
        if binary is None:
            return host_listing.listing("no_binary", detail=why)
        request = {"type": "control_request", "request_id": REQUEST_ID,
                   "request": {"subtype": "initialize"}}
        code, out = host_listing.run([binary, *PROBE], deadline, environ=environ,
                                     input_text=json.dumps(request) + "\n")
        response = reply(out)
        if response is None:
            raise CommandFailed(f"it exited with status {code} and sent no `initialize` reply"
                                if code else "it sent no `initialize` reply")
        body = response.get("response")
        if response.get("subtype") != "success" or not isinstance(body, dict):
            raise CommandFailed("its `initialize` reply is an error")
        models = listed_models(body.get("models"))
        account = body.get("account")
        token = account.get("tokenSource") if isinstance(account, dict) else None
        if token in CLAUDE_AI_SOURCES:
            view, whose = "account", f"the models of the signed-in account (token source {token})"
        elif isinstance(token, str) and token not in ("", "none"):
            view, whose = "generic", (f"its built-in models, since token source {token} is no"
                                      " claude.ai login")
        else:
            view, whose = "generic", "its built-in models, since no account is signed in"
        return host_listing.listing(
            "ok", binary=binary, version=version, view=view, models=models,
            detail=f"the `initialize` reply of Claude Code {version} lists {whose}")
    except CommandFailed as exc:
        return host_listing.listing("failed", binary=binary, version=version,
                                    detail=f"Claude Code's `initialize` probe failed: {exc}")
    except Exception as exc:  # never an error that stops setup
        return host_listing.listing("failed", binary=binary, version=version,
                                    detail=f"the model listing failed: {exc!r}")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print Claude Code's own model list and judge each MODEL against it.")
    parser.add_argument("models", nargs="*", metavar="MODEL")
    parser.add_argument("--timeout", type=float, default=host_listing.DEFAULT_TIMEOUT)
    parser.add_argument("--min-version",
                        help="the catalog's min_cli_version, which `claude` on PATH must meet")
    args = parser.parse_args(argv)
    print(host_listing.report(
        list_models(timeout=args.timeout, min_version=args.min_version), args.models))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
