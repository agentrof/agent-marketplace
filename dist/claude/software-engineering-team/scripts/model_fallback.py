#!/usr/bin/env python3
"""Warn when a role of this team cannot run on its pinned model.

Claude Code runs this hook after every Agent call, from PostToolUse and
PostToolUseFailure. A role's pinned model is the `model:` line of its agent
file in this plugin. A role that setup rendered into the project as
`<plugin>-<role>` is pinned by the `model:` line of that rendered file, which
carries this plugin's generated header, or, when no such file is in the
project's `.claude/agents/`, by the package tier map. The hook stays silent
unless the call spawned one of this plugin's roles, whose pin it found,
without a per-invocation `model` and with CLAUDE_CODE_SUBAGENT_MODEL_FORCE
off: a model the user chose never triggers it, and the re-spawn it asks for
passes `model`, so it never triggers again.

- PostToolUseFailure whose error names the pinned model as unavailable or
  refused in Claude Code's wording, or says this Claude Code does not
  support the model: a warning for the user and, for Claude, one re-spawn
  of the same role on the session's model when the failed run changed
  nothing, so a writer never repeats its edits on a partly changed tree.
- PostToolUse whose `tool_response.resolvedModel` is another Claude model:
  Claude Code substituted the pin, for example under an organization model
  policy, and the run stands. A warning for the user and a report for Claude.

The hook never blocks and exits 0 without output on input it cannot read.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
TIER_MAP = PLUGIN_ROOT / "templates" / "tier-map.json"
RENDERED_AGENTS = Path(".claude") / "agents"
ROLE_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# A Claude model ID inside any provider form, such as
# us.anthropic.claude-opus-5-5-v1:0 or claude-opus-5-5[1m].
CLAUDE_ID_RE = re.compile(r"claude-(?P<family>[a-z]+)-[0-9]+(?:-[0-9]{1,2})?(?:-[0-9]{8})?")
# Claude Code's wording for a model that cannot run
# (https://code.claude.com/docs/en/errors): the error type model_not_found,
# which a subagent's error detail names; its 404 forms "There's an issue with
# the selected model" and "The model <model> is not available on your
# <provider> deployment"; the plan form "Claude Opus is not available with the
# Claude Pro plan"; and Amazon Bedrock's "You don't have access to the model
# with the specified model ID". Other errors also name the pin, as the model
# sent to the API, so a broader match would move a role for any API error.
# The provider and plan names are bounded, so a long error stays linear.
UNAVAILABLE_RE = re.compile(
    r"\bmodel_not_found\b|There's an issue with the selected model"
    r"|is not available on your [^.\n]{1,80} deployment"
    r"|is not available with the [^.\n]{1,80} plan"
    r"|n[o']t have access to the model\b",
    re.IGNORECASE)
# A model too new for this Claude Code. The message names no model, and a
# role spawned without `model` runs only its pinned one.
VERSION_GATE_RE = re.compile(
    r"does not support this model|claude_code_version_too_old", re.IGNORECASE)
FORCE_OFF = {"", "0", "false", "no", "off"}
EXCERPT = 300


def plugin_name() -> str | None:
    try:
        value = json.loads((PLUGIN_ROOT / ".claude-plugin" / "plugin.json")
                           .read_text(encoding="utf-8")).get("name")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, str) and ROLE_RE.fullmatch(value) else None


def frontmatter(text: str) -> dict[str, str] | None:
    """Return an agent file's frontmatter fields, None without frontmatter."""
    end = text.find("\n---", 4)
    if not text.startswith("---\n") or end == -1:
        return None
    fields: dict[str, str] = {}
    for line in text[4:end].splitlines():
        key, separator, value = line.partition(":")
        if separator:
            fields.setdefault(key.strip(), value.strip().strip("\"'"))
    return fields


def claude_id(value: str | None) -> str | None:
    return value if value and CLAUDE_ID_RE.fullmatch(value) else None


def pinned_model(role: str) -> str | None:
    """Return the exact model ID the role's agent file pins, if any."""
    try:
        text = (PLUGIN_ROOT / "agents" / f"{role}.md").read_text(encoding="utf-8")
    except OSError:
        return None
    fields = frontmatter(text)
    return claude_id(fields.get("model")) if fields is not None else None


def tier_map_pin(role: str) -> str | None:
    """Return the model the package tier map pins the role's tier to, if any."""
    try:
        data = json.loads(TIER_MAP.read_text(encoding="utf-8"))
        tier = next(tier for tier, roles in data["roles"].items() if role in roles)
        return claude_id(data["hosts"]["claude"]["tiers"][tier].get("model"))
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration):
        return None


def rendered_pin(plugin: str, role: str, payload: dict, environ) -> str | None:
    """Return the pin of the project's rendered role `<plugin>-<role>`.

    Its own file decides: a refresh may not have reached it yet, so the tier
    map serves only when the project holds no file of that name. A file
    without this plugin's generated header is the user's own agent.
    """
    root = environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
    if isinstance(root, str) and root:
        path = Path(root) / RENDERED_AGENTS / f"{plugin}-{role}.md"
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            text = None
        except (OSError, ValueError):
            return None
        if text is not None:
            lines = text.splitlines()
            fields = frontmatter(text)
            owner = f"# Generated by Agent Marketplace {plugin}; do not edit by hand."
            if fields is None or len(lines) < 2 or lines[1] != owner \
                    or fields.get("name") != f"{plugin}-{role}":
                return None
            return claude_id(fields.get("model"))
    return tier_map_pin(role)


def names(text: str, model: str) -> bool:
    """Whether text names the model, in its own or a provider form."""
    pattern = (r"(?<![A-Za-z0-9-])" + re.escape(model)
               + r"(?![0-9])(?!-[0-9]{1,2}(?![0-9]))")
    if re.search(pattern, text):
        return True
    family = CLAUDE_ID_RE.fullmatch(model)
    return bool(family and re.search(r"\bClaude " + family["family"].capitalize() + r"\b", text))


def excerpt(text: str) -> str:
    """The error as one sentence of at most EXCERPT characters."""
    text = " ".join(text.split())
    if len(text) > EXCERPT:
        text = text[:EXCERPT].rstrip() + "..."
    return text if text.endswith((".", "!", "?")) else text + "."


def unavailable(payload: dict, agent: str, pinned: str) -> dict | None:
    error = payload.get("error")
    if not isinstance(error, str):
        return None
    if not (VERSION_GATE_RE.search(error)
            or (names(error, pinned) and UNAVAILABLE_RE.search(error))):
        return None
    reason = excerpt(error)
    return {
        "systemMessage": (f"{agent} could not run on its pinned model {pinned}, so Claude"
                          " spawns it once more on this session's model if the failed run"
                          f" changed nothing. Error: {reason}"),
        "hookSpecificOutput": {
            "hookEventName": "PostToolUseFailure",
            "additionalContext": (
                f"The role {agent} failed because its pinned model {pinned} cannot run"
                f" here. Error: {reason} Tell the user which role, which model and why."
                " Spawn it again only when the failed run changed nothing: its task"
                " manifest's `write_boundary` is `read_only`, as a reader's is, or the"
                " `task_inputs.py` invocation that derived that manifest, run again with"
                " `--expected-hash <source_hash>`, still passes, since the manifest binds the"
                " content of its inputs and of every modified or new source it covers."
                " Otherwise stop and report the error and the changed paths to the user."
                " When nothing"
                f" changed, spawn the same subagent_type {agent} once more, with the same"
                " description and prompt and with `model` set to the family alias of the"
                " model this conversation runs on, such as `opus` or `sonnet`, which runs"
                " the role on this conversation's exact model. If that spawn fails too,"
                " stop and report both errors to the user instead of trying again."),
        },
    }


def substituted(payload: dict, agent: str, pinned: str) -> dict | None:
    response = payload.get("tool_response")
    resolved = response.get("resolvedModel") if isinstance(response, dict) else None
    # A provider ARN or deployment name cannot be compared with the pin.
    if not isinstance(resolved, str) or names(resolved, pinned) \
            or CLAUDE_ID_RE.search(resolved) is None:
        return None
    return {
        "systemMessage": f"{agent} is pinned to {pinned} but started on {resolved}.",
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                f"The role {agent} started on {resolved} instead of its pinned model"
                f" {pinned}: Claude Code substituted the model, for example because an"
                f" organization model policy blocks {pinned}. Tell the user which role ran"
                " on which model and why. Keep this run and do not spawn the role again"
                " for this."),
        },
    }


def respond(payload: object, environ) -> dict | None:
    if not isinstance(payload, dict) or payload.get("tool_name") != "Agent":
        return None
    if environ.get("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "").strip().lower() not in FORCE_OFF:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or tool_input.get("model"):
        return None
    agent, plugin = tool_input.get("subagent_type"), plugin_name()
    if not isinstance(agent, str) or plugin is None \
            or not agent.startswith((f"{plugin}:", f"{plugin}-")):
        return None
    role = agent[len(plugin) + 1:]
    if not ROLE_RE.fullmatch(role):
        return None
    pinned = pinned_model(role) if agent[len(plugin)] == ":" \
        else rendered_pin(plugin, role, payload, environ)
    if pinned is None:
        return None
    event = payload.get("hook_event_name")
    if event == "PostToolUseFailure":
        return unavailable(payload, agent, pinned)
    if event == "PostToolUse":
        return substituted(payload, agent, pinned)
    return None


def main() -> int:
    try:
        # Claude Code writes UTF-8 whatever the locale's stdin encoding is.
        text = sys.stdin.buffer.read().decode("utf-8")
        output = respond(json.loads(text or "{}"), os.environ)
    except Exception:
        # A hook that cannot read its input leaves the Agent result as it is.
        return 0
    if output is not None:
        print(json.dumps(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
