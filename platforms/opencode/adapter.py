"""OpenCode project-projection rendering policy for the distribution builder."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


PACKAGE_TOKEN = "{{AGENT_MARKETPLACE_OPENCODE_PACKAGE_ROOT}}"


def _permission_policy(role: str, canonical_tools: str) -> set[str]:
    path = Path(__file__).with_name("agent-permissions.json")
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: invalid OpenCode permission policy") from exc
    if policy.get("schema_version") != 1 or not isinstance(policy.get("default"), list):
        raise ValueError(f"{path}: invalid OpenCode permission policy")
    roles = policy.get("roles", {})
    selected = roles.get(role.replace("-", "_"), policy["default"])
    if not isinstance(selected, list) or not all(isinstance(item, str) for item in selected):
        raise ValueError(f"{path}: invalid permission policy for {role}")
    canonical = {item.strip() for item in canonical_tools.split(",") if item.strip()}
    # Explicit canonical safe-tool declarations take precedence. They are the
    # only role-local capability source available in the canonical frontmatter.
    if canonical:
        mapping = {"Read": "read", "Grep": "grep", "Glob": "glob"}
        return {mapping[item] for item in canonical if item in mapping}
    return set(selected)


def _permission_for(role: str, canonical_tools: str) -> str:
    allowed = _permission_policy(role, canonical_tools)
    supported = {"read", "grep", "glob", "edit", "bash", "task"}
    unsupported = allowed - supported
    if unsupported:
        raise ValueError(
            f"OpenCode permission policy for {role} has unsupported tools: "
            + ", ".join(sorted(unsupported))
        )
    permissions = ["permission:", "  \"*\": deny"]
    permissions.extend(f"  {host}: allow" for host in (
        "read", "grep", "glob", "edit", "bash", "task"
    ) if host in allowed)
    return "\n".join(permissions)


def skill_artifacts(context: dict, source_name: str, metadata: tuple[str, str, str, str]) -> list[tuple[str, str]]:
    name, description, exposure, project_scope = metadata
    generated_name = f"{source_name}-{name}"
    project_gate = (
        " Before changing a project, confirm the workspace config and local docs "
        "contract are present; setup is the only entry that may create them."
        if exposure == "entry" and project_scope == "project" else ""
    )
    run_note = (
        " This entry is available in `opencode run` only when it is declared "
        "choice-free by the OpenCode adapter."
        if exposure == "entry" else ""
    )
    text = (
        "---\n"
        f"name: {generated_name}\n"
        f"description: {description}\n"
        "---\n\n"
        f"{context['wrapper_marker']}\n\n"
        f"# {context['title_of'](name)}\n\n"
        f"Read `{PACKAGE_TOKEN}/host-contract.md` and "
        f"`{PACKAGE_TOKEN}/skill-content/{name}/SKILL.md` completely. "
        "The package is authoritative. Present declared choices as normal chat "
        "text with numbered options, recommendation, and tradeoffs. Use a native "
        "OpenCode question surface only when it is available; never require it."
        f"{project_gate}{run_note}\n"
    )
    return [(f"skills/{generated_name}/SKILL.md", text)]


def agent_artifacts(context: dict, source) -> list[tuple[str, str]]:
    fields, body = context["parse_frontmatter"](source)
    role = fields.get("name", source.stem)
    description = fields.get("description", "")
    tools = fields.get("tools", "")
    generated_name = f"software-engineering-team-{role}"
    frontmatter = (
        "---\n"
        f"description: {description}\n"
        "mode: subagent\n"
        f"{_permission_for(role, tools)}\n"
        "---\n\n"
    )
    return [(f"agents/{generated_name}.md", frontmatter + body.lstrip("\n"))]


def primary_agent_artifact(source_name: str) -> tuple[str, str]:
    path = Path(__file__).with_name("agent-permissions.json")
    policy = json.loads(path.read_text(encoding="utf-8"))
    primary = policy.get("primary", [])
    if not isinstance(primary, list) or not all(isinstance(item, str) for item in primary):
        raise ValueError(f"{path}: invalid OpenCode primary permission policy")
    permission = "\n".join(["permission:", "  \"*\": deny", *[
        f"  {item}: allow" for item in primary
    ]])
    return (
        f"agents/{source_name}.md",
        "---\n"
        "description: Coordinate the Software Engineering Team entry workflows.\n"
        f"{permission}\n"
        "---\n\n"
        "# Software Engineering Team\n\n"
        f"Read `{PACKAGE_TOKEN}/host-contract.md` and the requested namespaced skill. "
        "Use only the generated Software Engineering Team subagents.\n",
    )


def command_artifacts(source_name: str, skills: list[tuple[str, str, str, str]]) -> list[tuple[str, str]]:
    artifacts: list[tuple[str, str]] = []
    for name, description, exposure, project_scope in skills:
        if exposure != "entry":
            continue
        run_mode = "choice_free" if project_scope == "external" else "tui_only"
        artifacts.append((
            f"commands/{name}.md",
            "---\n"
            f"description: {description}\n"
            "agent: software-engineering-team\n"
            "---\n\n"
            f"Read `{PACKAGE_TOKEN}/skill-content/{name}/SKILL.md` and follow it exactly. "
            "For a declared decision, ask in ordinary conversation text with clear "
            "numbered options. Do not mutate until the user answers. "
            f"Agent Marketplace run mode: {run_mode}.\n",
        ))
    return artifacts


def run_support_artifact(skills: list[tuple[str, str, str, str]]) -> tuple[str, str]:
    mapping = {
        name: "choice_free" if project_scope == "external" else "tui_only"
        for name, _description, exposure, project_scope in skills
        if exposure == "entry"
    }
    return "run-support.json", json.dumps({
        "schema_version": 1,
        "entries": mapping,
    }, indent=2, sort_keys=True) + "\n"


def plugin_artifact(source_name: str, build_id: str) -> tuple[str, str]:
    template_path = Path(__file__).with_name("runtime-plugin.js")
    template = template_path.read_text(encoding="utf-8")
    hook_path = (Path(__file__).parent.parent / "shared" /
                 "software-engineering-team" / "overlay" / "scripts" /
                 "vault_hook.py")
    replacements = {
        "__AGENT_MARKETPLACE_BUILD_ID__": json.dumps(build_id),
        "__AGENT_MARKETPLACE_VAULT_HOOK_SHA256__": json.dumps(
            hashlib.sha256(hook_path.read_bytes()).hexdigest()
        ),
    }
    rendered = template
    for marker, replacement in replacements.items():
        if rendered.count(marker) != 1:
            raise ValueError(f"{template_path}: expected exactly one {marker} marker")
        rendered = rendered.replace(marker, replacement)
    return f"plugins/agent-marketplace-{source_name}.js", rendered


def native_manifest_directory(host_id: str) -> None:
    del host_id
    return None


def instruction_surface() -> dict:
    return {
        "filename": "AGENTS.md",
        "owner_host": "opencode",
        "user_companion": "AGENTS.user.md",
        "migrates_from_owners": ["codex"],
    }


def runtime_contracts() -> list[str]:
    return ["opencode_tool_execute_v1"]


def projection_entrypoint() -> str:
    return "scripts/project_opencode.py"


def scaffold_contract() -> str:
    return """# OpenCode Terminal Host Contract

- This package is projected into each consuming project's `.opencode` directory.
- It never installs an Agent Marketplace global plugin, cache, or dependency.
- Present canonical choices as normal conversation text; native questions are
  optional host ergonomics and are never a workflow prerequisite.
- `opencode run` is supported only for choice-free entry workflows.
"""
