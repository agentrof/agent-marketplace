#!/usr/bin/env python3
"""Content validator for the Agent Marketplace repository.

Every rule in this repo is machine-enforced or it is not a rule. This tool
scans authored content (plugins/, platforms/, docs/, README.md,
CONTRIBUTING.md, .claude-plugin/, .agents/plugins/) and emits deterministic
findings. One error finding fails
the run.

Scope is an explicit allowlist; assets/, memory/, tools/ and .git/ are never
scanned. VALIDATOR_BUILDERS in tools/tests/test_validator_contract.py breaks a
valid fixture repository once per check, in lockstep with CHECKS.

Stdlib only. Deterministic output: findings sorted by (path, line, check).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import release as release_tool
import build_distributions

# ---------------------------------------------------------------------------
# Constants and policy tables
# ---------------------------------------------------------------------------

EM_DASH = "—"

MODEL_CONFIG_RELPATH = "tools/data/models.json"
LIMITS_CONFIG_RELPATH = "tools/data/limits.json"
PRODUCT_CONFIG_RELPATH = "product.json"

AGENT_REQUIRED_KEYS = {"name", "description", "reasoning", "output_contract"}
AGENT_REASONING_ENUM = {"high", "medium", "low", "lens", "mechanical", "inherit"}
# How the role hands results back. prose: findings/artifacts in the reply
# text (every current persona). structured: a forced tool call. Declared so
# a composer can refuse pairing a prose persona with schema forcing; the
# repo's own flows spawn via the Agent tool and never force schema.
AGENT_OUTPUT_CONTRACT_ENUM = {"prose", "structured"}
# Optional capability restriction: an agent MAY carry tools: as a whitelist,
# and only the read-only set is legal. Read-only roles (challengers, expert
# panels) are denied write capability at spawn time, not by instruction.
AGENT_OPTIONAL_KEYS = {"tools"}
AGENT_READONLY_TOOLS = {"Read", "Grep", "Glob"}
SKILL_REQUIRED_KEYS = {"name", "description", "exposure"}
SKILL_OPTIONAL_KEYS = {"project_scope"}
SKILL_EXPOSURE_ENUM = {"entry", "internal"}
SKILL_PROJECT_SCOPE_ENUM = {"project", "external"}

AGENT_REQUIRED_SECTIONS = ["Principles", "Boundaries", "Approach", "Output Contract"]
SKILL_REQUIRED_SECTIONS = ["When to Use"]

# Skills that must ship the reserved stack checklists.
TECH_SKILLS = {
    "python-fastapi",
    "react-typescript",
    "sql-database-design",
    "nosql-database-design",
    "docker-compose",
}
RESERVED_CHECKLISTS = ["review-checklist.md", "qa-checklist.md"]

# Authoring size caps. tools/data/limits.json (authoring_caps) is
# authoritative; these constants are the fallbacks when that file is
# unloadable, and a cap bump edits both in one commit.
AGENT_BODY_MAX_LINES = 80
SKILL_MAX_LINES = 150
SKILL_WARN_LINES = 120
SKILL_MAX_BYTES = 8192
CONSTITUTION_MAX_LINES = 60
FLOW_MAX_LINES = 424
REFERENCE_WARN_LINES = 500
PROJECT_INSTRUCTION_MAX_BYTES = 24576
PROJECT_INSTRUCTION_MAX_LINES = 180

AUTHORING_CAP_KEYS = {
    "agent_body_max_lines", "skill_max_lines", "skill_warn_lines",
    "skill_max_bytes", "constitution_max_lines", "flow_max_lines",
    "reference_warn_lines", "project_instruction_max_bytes",
    "project_instruction_max_lines",
}

AUTO_TRIGGER_RE = re.compile(
    r"use\s+proactively|use\s+when|use\s+this\s+skill\s+when|trigger\s+when|auto-?loads?\s+when|must\s+be\s+used",
    re.IGNORECASE,
)

VERSION_PIN_RE = re.compile(
    r"\b(python|node|react|next\.?js|vue|svelte|angular|fastapi|django|flask|"
    r"postgres(?:ql)?|mysql|mongodb|redis|typescript|javascript|java|golang|"
    r"rust|swift|kotlin|expo|tailwind)\s*v?\d",
    re.IGNORECASE,
)

MODEL_NAME_RE = re.compile(
    r"claude-[a-z0-9][a-z0-9.\-]*|gpt-\d|\b(?:opus|sonnet|haiku|fable)\b",
    re.IGNORECASE,
)

ABSOLUTE_PATH_RE = re.compile(r"(?:^|[\s\"'`(=])(?:/Users/|/home/|[A-Za-z]:\\\\|~/)")

HANDWRITTEN_COUNT_RE = re.compile(
    r"\b\d+\s+(?:agents?|skills?|plugins?|commands?)\b", re.IGNORECASE
)

# Stack/framework/vendor nouns banned in agent BODIES (tech lives in skills).
TECH_NOUN_RE = re.compile(
    r"\b(python|fastapi|django|flask|react|typescript|javascript|node|nextjs|"
    r"vue|svelte|angular|tailwind|postgres(?:ql)?|mysql|mongodb|redis|sql|"
    r"nosql|docker|kubernetes|aws|azure|gcp|pytest|vitest|playwright|npm|pip|"
    r"obsidian)\b",
    re.IGNORECASE,
)

MD_LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+?)(?:\s+\"[^\"]*\")?\)")

KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DISPLAY_TOKENS = {
    "api": "API", "cli": "CLI", "devops": "DevOps",
    "fastapi": "FastAPI", "nosql": "NoSQL",
    "qa": "QA", "sql": "SQL", "ui": "UI", "ux": "UX",
}

SNAKE_KEY_RE = re.compile(r"^[a-z0-9_]+$")

# Hook event names inside a plugin's hooks/hooks.json follow the host
# platform's PascalCase schema (SessionStart, PreToolUse, ...).
HOOK_EVENT_KEY_RE = re.compile(r"^[A-Z][A-Za-z]+$")

# Inline code spans render as code, not links; wikilink_ban strips them
# before scanning, the same way fenced blocks are skipped.
INLINE_CODE_RE = re.compile(r"`[^`]*`")

# The vault app's property types; vault-policy.json property_types values
# must come from this enum.
OBSIDIAN_PROPERTY_TYPES = {
    "text", "multitext", "number", "checkbox", "date", "datetime",
    "tags", "aliases",
}

COUNTS_START = "<!-- counts:start -->"
COUNTS_END = "<!-- counts:end -->"

CONSTITUTION_PLACEHOLDER = "{{constitution}}"
AGENT_ROLE_SUFFIX_RE_TPL = r"\b{plugin}-([a-z0-9]+(?:-[a-z0-9]+)*)\b"


@dataclass(frozen=True)
class Finding:
    severity: str  # "error" | "warning"
    path: str
    line: int
    check: str
    message: str
    remediation: str


@dataclass
class Tree:
    """Resolved scan scope for one repository root."""

    root: Path
    plugins_dir: Path
    docs_dir: Path
    readme: Path
    marketplace: Path
    codex_marketplace: Path
    config: dict | None = None  # tools/data/models.json, None when unloadable
    limits: dict | None = None  # tools/data/limits.json, None when unloadable
    product: dict | None = None  # product.json, None when unloadable
    md_files: list[Path] = field(default_factory=list)
    json_files: list[Path] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def rel(tree: Tree, path: Path) -> str:
    try:
        return path.relative_to(tree.root).as_posix()
    except ValueError:
        return path.as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def display_title(name: str) -> str:
    return " ".join(
        DISPLAY_TOKENS.get(part, part.capitalize()) for part in name.split("-")
    )


def parse_frontmatter(text: str) -> tuple[dict[str, str], int, str]:
    """Return (frontmatter dict, body start line index 1-based, body text).

    Minimal YAML subset: `key: value` lines between --- fences. Values keep
    their raw string form; booleans arrive as 'true'/'false'.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, 1, text
    fm: dict[str, str] = {}
    for i in range(1, len(lines)):
        stripped = lines[i].strip()
        if stripped == "---":
            body = "\n".join(lines[i + 1 :])
            return fm, i + 2, body
        if not stripped or stripped.startswith("#"):
            continue
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            fm[key.strip()] = value.strip().strip("\"'")
    return {}, 1, text


def heading_has_emoji(line: str) -> bool:
    for ch in line:
        code = ord(ch)
        if code >= 0x1F000:
            return True
        if 0x2600 <= code <= 0x27BF or 0x2B00 <= code <= 0x2BFF or code == 0xFE0F:
            return True
        if unicodedata.category(ch) == "So":
            return True
    return False


def iter_scope_files(tree: Tree, suffix: str) -> list[Path]:
    files: list[Path] = []
    if tree.plugins_dir.is_dir():
        files.extend(sorted(tree.plugins_dir.rglob(f"*{suffix}")))
    if tree.docs_dir.is_dir():
        files.extend(sorted(tree.docs_dir.rglob(f"*{suffix}")))
    if suffix == ".md" and tree.readme.is_file():
        files.append(tree.readme)
    if suffix == ".md":
        contributing = tree.root / "CONTRIBUTING.md"
        if contributing.is_file():
            files.append(contributing)
    if suffix == ".json":
        if tree.marketplace.is_file():
            files.append(tree.marketplace)
        if tree.codex_marketplace.is_file():
            files.append(tree.codex_marketplace)
    return files


def plugin_dirs(tree: Tree) -> list[Path]:
    if not tree.plugins_dir.is_dir():
        return []
    return sorted(p for p in tree.plugins_dir.iterdir() if p.is_dir())


def agent_files(plugin: Path) -> list[Path]:
    agents = plugin / "agents"
    return sorted(agents.glob("*.md")) if agents.is_dir() else []


def skill_dirs(plugin: Path) -> list[Path]:
    skills = plugin / "skill-content"
    if not skills.is_dir():
        return []
    return sorted(p for p in skills.iterdir() if p.is_dir())


def readme_marker_span(text: str) -> tuple[int, int]:
    """Line span (1-based, inclusive) of the counts marker block, or (0, -1)."""
    lines = text.splitlines()
    start = end = 0
    for idx, line in enumerate(lines, start=1):
        if COUNTS_START in line:
            start = idx
        if COUNTS_END in line:
            end = idx
    if start and end and start <= end:
        return start, end
    return 0, -1


# ---------------------------------------------------------------------------
# Checks. Each check function appends Finding objects.
# ---------------------------------------------------------------------------


def check_frontmatter_shape(tree: Tree, findings: list[Finding]) -> None:
    for plugin in plugin_dirs(tree):
        for path in agent_files(plugin):
            fm, _, _ = parse_frontmatter(read_text(path))
            keys = set(fm)
            missing = AGENT_REQUIRED_KEYS - keys
            extra = keys - AGENT_REQUIRED_KEYS - AGENT_OPTIONAL_KEYS
            if missing:
                findings.append(Finding(
                    "error", rel(tree, path), 1, "frontmatter_shape",
                    f"agent frontmatter missing keys: {sorted(missing)}",
                    "add the missing keys; agents require name, description,"
                    " reasoning, output_contract",
                ))
            if extra:
                findings.append(Finding(
                    "error", rel(tree, path), 1, "frontmatter_shape",
                    f"agent frontmatter has unsupported keys: {sorted(extra)}",
                    "remove them; agents carry name, description, reasoning,"
                    " output_contract and optionally a read-only tools whitelist",
                ))
            if "tools" in fm:
                tools = {t.strip() for t in fm["tools"].split(",") if t.strip()}
                illegal = tools - AGENT_READONLY_TOOLS
                if illegal:
                    findings.append(Finding(
                        "error", rel(tree, path), 1, "frontmatter_shape",
                        f"agent tools whitelist holds non-read-only tools: {sorted(illegal)}",
                        f"tools: exists only to make a role read-only; allowed:"
                        f" {sorted(AGENT_READONLY_TOOLS)}. Full-capability agents"
                        " omit the key",
                    ))
            reasoning = fm.get("reasoning", "")
            reasoning_enum = declared_tiers(tree)
            if reasoning and reasoning not in reasoning_enum:
                findings.append(Finding(
                    "error", rel(tree, path), 1, "frontmatter_shape",
                    f"agent reasoning '{reasoning}' is not in"
                    f" {sorted(reasoning_enum)}",
                    "use a host-neutral level from tools/data/models.json"
                    " reasoning_levels",
                ))
            contract = fm.get("output_contract", "")
            if contract and contract not in AGENT_OUTPUT_CONTRACT_ENUM:
                findings.append(Finding(
                    "error", rel(tree, path), 1, "frontmatter_shape",
                    f"agent output_contract '{contract}' is not in"
                    f" {sorted(AGENT_OUTPUT_CONTRACT_ENUM)}",
                    "declare how the role returns results: prose (findings in"
                    " the reply) or structured (a forced tool call)",
                ))
        for sdir in skill_dirs(plugin):
            skill_md = sdir / "SKILL.md"
            if not skill_md.is_file():
                findings.append(Finding(
                    "error", rel(tree, sdir), 1, "frontmatter_shape",
                    "skill directory has no SKILL.md",
                    "add SKILL.md with name and description frontmatter",
                ))
                continue
            fm, _, _ = parse_frontmatter(read_text(skill_md))
            keys = set(fm)
            missing = SKILL_REQUIRED_KEYS - keys
            extra = keys - SKILL_REQUIRED_KEYS - SKILL_OPTIONAL_KEYS
            if missing:
                findings.append(Finding(
                    "error", rel(tree, skill_md), 1, "frontmatter_shape",
                    f"skill frontmatter missing keys: {sorted(missing)}",
                    "add the missing keys; skills require name, description,"
                    " and exposure",
                ))
            if extra:
                findings.append(Finding(
                    "error", rel(tree, skill_md), 1, "frontmatter_shape",
                    f"skill frontmatter has unsupported keys: {sorted(extra)}",
                    "remove them; allowed keys are name, description, exposure"
                    " and optional project_scope",
                ))
            project_scope = fm.get("project_scope", "project")
            if project_scope not in SKILL_PROJECT_SCOPE_ENUM:
                findings.append(Finding(
                    "error", rel(tree, skill_md), 1, "frontmatter_shape",
                    f"skill project_scope '{project_scope}' is not in"
                    f" {sorted(SKILL_PROJECT_SCOPE_ENUM)}",
                    "use project for project-bound workflows or external for"
                    " stateless external entries",
                ))
            if project_scope == "external" and fm.get("exposure") != "entry":
                findings.append(Finding(
                    "error", rel(tree, skill_md), 1, "frontmatter_shape",
                    "only entry skills may use project_scope 'external'",
                    "make the skill an entry or remove the external scope",
                ))


def check_agent_name(tree: Tree, findings: list[Finding]) -> None:
    for plugin in plugin_dirs(tree):
        seen: dict[str, str] = {}
        for path in agent_files(plugin):
            fm, _, body = parse_frontmatter(read_text(path))
            name = fm.get("name", "")
            expected = path.stem
            if name != expected:
                findings.append(Finding(
                    "error", rel(tree, path), 1, "agent_name",
                    f"agent frontmatter name '{name}' must equal '{expected}'",
                    f"set name: {expected}; the host adds the plugin namespace",
                ))
            if name:
                if name in seen:
                    findings.append(Finding(
                        "error", rel(tree, path), 1, "agent_name",
                        f"agent name '{name}' already used by {seen[name]}",
                        "agent names must be unique within their plugin",
                    ))
                else:
                    seen[name] = rel(tree, path)
            h1 = next(
                (line[2:].strip() for line in body.splitlines()
                 if line.startswith("# ")),
                "",
            )
            expected_h1 = display_title(path.stem)
            if h1 != expected_h1:
                findings.append(Finding(
                    "error", rel(tree, path), 1, "agent_name",
                    f"agent title '{h1}' must equal '{expected_h1}'",
                    "keep the human label aligned with the canonical id and"
                    " the acronym casing registry",
                ))


def check_skill_name(tree: Tree, findings: list[Finding]) -> None:
    for plugin in plugin_dirs(tree):
        seen: dict[str, str] = {}
        if not KEBAB_RE.match(plugin.name):
            findings.append(Finding(
                "error", rel(tree, plugin), 1, "skill_name",
                f"plugin directory '{plugin.name}' is not kebab-case",
                "rename to lowercase words separated by single hyphens",
            ))
        if "__" in plugin.name:
            findings.append(Finding(
                "error", rel(tree, plugin), 1, "skill_name",
                "double underscore is reserved and banned in names",
                "rename without '__'",
            ))
        for path in agent_files(plugin):
            if not KEBAB_RE.match(path.stem):
                findings.append(Finding(
                    "error", rel(tree, path), 1, "skill_name",
                    f"agent file '{path.name}' is not kebab-case",
                    "rename to kebab-case",
                ))
        for sdir in skill_dirs(plugin):
            if not KEBAB_RE.match(sdir.name):
                findings.append(Finding(
                    "error", rel(tree, sdir), 1, "skill_name",
                    f"skill directory '{sdir.name}' is not kebab-case",
                    "rename to kebab-case",
                ))
            skill_md = sdir / "SKILL.md"
            if not skill_md.is_file():
                continue
            fm, _, _ = parse_frontmatter(read_text(skill_md))
            name = fm.get("name", "")
            if name != sdir.name:
                findings.append(Finding(
                    "error", rel(tree, skill_md), 1, "skill_name",
                    f"skill frontmatter name '{name}' must equal directory name '{sdir.name}'",
                    f"set name: {sdir.name}",
                ))
            if name:
                if name in seen:
                    findings.append(Finding(
                        "error", rel(tree, skill_md), 1, "skill_name",
                        f"skill name '{name}' already used by {seen[name]}",
                        "skill names must be unique within their plugin",
                    ))
                else:
                    seen[name] = rel(tree, skill_md)


def check_trigger_policy(tree: Tree, findings: list[Finding]) -> None:
    for plugin in plugin_dirs(tree):
        for path in agent_files(plugin):
            fm, _, _ = parse_frontmatter(read_text(path))
            desc = fm.get("description", "")
            if AUTO_TRIGGER_RE.search(desc):
                findings.append(Finding(
                    "error", rel(tree, path), 1, "trigger_policy",
                    "agent description contains an auto-trigger phrase",
                    "agents are passive; describe the role and say it is invoked by the plugin's flows",
                ))
            if not desc:
                findings.append(Finding(
                    "error", rel(tree, path), 1, "trigger_policy",
                    "agent description is empty",
                    "add a one-sentence passive description",
                ))
        for sdir in skill_dirs(plugin):
            skill_md = sdir / "SKILL.md"
            if not skill_md.is_file():
                continue
            fm, _, _ = parse_frontmatter(read_text(skill_md))
            exposure = fm.get("exposure", "")
            if exposure not in SKILL_EXPOSURE_ENUM:
                findings.append(Finding(
                    "error", rel(tree, skill_md), 1, "trigger_policy",
                    "skill must declare a host-neutral exposure",
                    "set exposure to entry or internal",
                ))
            if not fm.get("description"):
                findings.append(Finding(
                    "error", rel(tree, skill_md), 1, "trigger_policy",
                    "skill description is empty",
                    "add a description stating purpose and invocation context",
                ))


def _cap(caps: dict, key: str, default: int) -> int:
    """Effective cap: the limits-file value when it is a usable integer,
    else the in-code fallback (a malformed value is limits_config_shape's
    finding, never a crash here)."""
    value = caps.get(key)
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default


def check_size_caps(tree: Tree, findings: list[Finding]) -> None:
    caps = (tree.limits or {}).get("authoring_caps") or {}
    if not isinstance(caps, dict):
        caps = {}
    agent_body_max = _cap(caps, "agent_body_max_lines", AGENT_BODY_MAX_LINES)
    skill_max = _cap(caps, "skill_max_lines", SKILL_MAX_LINES)
    skill_warn = _cap(caps, "skill_warn_lines", SKILL_WARN_LINES)
    skill_bytes = _cap(caps, "skill_max_bytes", SKILL_MAX_BYTES)
    constitution_max = _cap(caps, "constitution_max_lines",
                            CONSTITUTION_MAX_LINES)
    flow_max = _cap(caps, "flow_max_lines", FLOW_MAX_LINES)
    reference_warn = _cap(caps, "reference_warn_lines", REFERENCE_WARN_LINES)
    for plugin in plugin_dirs(tree):
        for path in agent_files(plugin):
            _, body_start, body = parse_frontmatter(read_text(path))
            body_lines = len([l for l in body.splitlines()])
            if body_lines > agent_body_max:
                findings.append(Finding(
                    "error", rel(tree, path), body_start, "size_caps",
                    f"agent body is {body_lines} lines (cap {agent_body_max})",
                    "cut to constitution altitude; move any depth into skills",
                ))
        for sdir in skill_dirs(plugin):
            skill_md = sdir / "SKILL.md"
            if skill_md.is_file():
                text = read_text(skill_md)
                n = len(text.splitlines())
                if n > skill_max:
                    findings.append(Finding(
                        "error", rel(tree, skill_md), 1, "size_caps",
                        f"SKILL.md is {n} lines (cap {skill_max})",
                        "move depth into references/ and keep SKILL.md a decision surface",
                    ))
                elif n > skill_warn:
                    findings.append(Finding(
                        "warning", rel(tree, skill_md), 1, "size_caps",
                        f"SKILL.md is {n} lines (warn threshold {skill_warn})",
                        "consider moving detail into references/",
                    ))
                if len(text.encode("utf-8")) > skill_bytes:
                    findings.append(Finding(
                        "error", rel(tree, skill_md), 1, "size_caps",
                        f"SKILL.md exceeds {skill_bytes} bytes",
                        "move depth into references/",
                    ))
            refs = sdir / "references"
            if refs.is_dir():
                for ref in sorted(refs.rglob("*.md")):
                    n = len(read_text(ref).splitlines())
                    if n > reference_warn:
                        findings.append(Finding(
                            "warning", rel(tree, ref), 1, "size_caps",
                            f"reference is {n} lines (warn threshold {reference_warn})",
                            "split into focused reference files",
                        ))
        constitution = plugin / "constitution.md"
        if constitution.is_file():
            n = len(read_text(constitution).splitlines())
            if n > constitution_max:
                findings.append(Finding(
                    "error", rel(tree, constitution), 1, "size_caps",
                    f"constitution is {n} lines (cap {constitution_max})",
                    "the constitution must stay terse; cut to principle altitude",
                ))
        flows = plugin / "flows"
        if flows.is_dir():
            for flow in sorted(flows.glob("*.md")):
                n = len(read_text(flow).splitlines())
                if n > flow_max:
                    findings.append(Finding(
                        "error", rel(tree, flow), 1, "size_caps",
                        f"flow is {n} lines (cap {flow_max})",
                        "tighten the state-machine prose; flows are procedures, not essays",
                    ))


def check_section_contract(tree: Tree, findings: list[Finding]) -> None:
    for plugin in plugin_dirs(tree):
        for path in agent_files(plugin):
            _, _, body = parse_frontmatter(read_text(path))
            h1_count = len(re.findall(r"^# ", body, flags=re.MULTILINE))
            if h1_count != 1:
                findings.append(Finding(
                    "error", rel(tree, path), 1, "section_contract",
                    f"agent body must have exactly one H1 (found {h1_count})",
                    "one H1 role title, then the fixed H2 sections",
                ))
            for section in AGENT_REQUIRED_SECTIONS:
                if not re.search(rf"^## {re.escape(section)}\s*$", body, flags=re.MULTILINE):
                    findings.append(Finding(
                        "error", rel(tree, path), 1, "section_contract",
                        f"agent body missing required section '## {section}'",
                        "agents carry Principles, Boundaries, Approach, Output Contract",
                    ))
        for sdir in skill_dirs(plugin):
            skill_md = sdir / "SKILL.md"
            if not skill_md.is_file():
                continue
            _, _, body = parse_frontmatter(read_text(skill_md))
            for section in SKILL_REQUIRED_SECTIONS:
                if not re.search(rf"^## {re.escape(section)}\s*$", body, flags=re.MULTILINE):
                    findings.append(Finding(
                        "error", rel(tree, skill_md), 1, "section_contract",
                        f"SKILL.md missing required section '## {section}'",
                        "add a When to Use section with concrete scenarios",
                    ))
            if sdir.name in TECH_SKILLS:
                for checklist in RESERVED_CHECKLISTS:
                    if not (sdir / "references" / checklist).is_file():
                        findings.append(Finding(
                            "error", rel(tree, sdir), 1, "section_contract",
                            f"tech skill missing references/{checklist}",
                            "every tech skill ships both reserved checklists",
                        ))


def check_content_bans(tree: Tree, findings: list[Finding]) -> None:
    for path in iter_scope_files(tree, ".md"):
        text = read_text(path)
        under_plugins = tree.plugins_dir in path.parents
        is_agent = under_plugins and path.parent.name == "agents"
        fm_end = 1
        if is_agent:
            _, fm_end, _ = parse_frontmatter(text)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if EM_DASH in line:
                findings.append(Finding(
                    "error", rel(tree, path), lineno, "content_bans",
                    "em dash character found",
                    "replace with a hyphen, comma, or rewrite the sentence",
                ))
            if line.lstrip().startswith("#") and heading_has_emoji(line):
                findings.append(Finding(
                    "error", rel(tree, path), lineno, "content_bans",
                    "emoji in heading",
                    "headings are plain text",
                ))
            if under_plugins and VERSION_PIN_RE.search(line):
                findings.append(Finding(
                    "error", rel(tree, path), lineno, "content_bans",
                    "version pin detected",
                    "state capabilities, not versions; pins rot",
                ))
            if under_plugins and MODEL_NAME_RE.search(line):
                if not (is_agent and lineno < fm_end):
                    findings.append(Finding(
                        "error", rel(tree, path), lineno, "content_bans",
                        "model name outside agent frontmatter",
                        "host model names belong only in platforms/<host>/"
                        "execution-profiles.json and generated distributions",
                    ))
            if ABSOLUTE_PATH_RE.search(line):
                findings.append(Finding(
                    "error", rel(tree, path), lineno, "content_bans",
                    "absolute or home path found",
                    "use project-relative paths anchored at the workspace",
                ))
            if re.search(r"\bassets/", line):
                findings.append(Finding(
                    "error", rel(tree, path), lineno, "content_bans",
                    "reference to assets/ in shipped content",
                    "shipped content must not depend on research material",
                ))


def check_agent_tech_nouns(tree: Tree, findings: list[Finding]) -> None:
    for plugin in plugin_dirs(tree):
        for path in agent_files(plugin):
            _, body_start, body = parse_frontmatter(read_text(path))
            for offset, line in enumerate(body.splitlines()):
                if TECH_NOUN_RE.search(line):
                    findings.append(Finding(
                        "error", rel(tree, path), body_start + offset, "agent_tech_nouns",
                        "technology noun in agent body",
                        "agents carry judgment, not technology; move stack facts into the bound skill",
                    ))


def check_handwritten_counts(tree: Tree, findings: list[Finding]) -> None:
    for path in iter_scope_files(tree, ".md"):
        text = read_text(path)
        skip_start, skip_end = (0, -1)
        if path == tree.readme:
            skip_start, skip_end = readme_marker_span(text)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if skip_start <= lineno <= skip_end:
                continue
            if HANDWRITTEN_COUNT_RE.search(line):
                findings.append(Finding(
                    "error", rel(tree, path), lineno, "handwritten_counts",
                    "hand-written derived count",
                    "counts live only in the README marker block, injected by tools/counts.py",
                ))


def check_dead_links(tree: Tree, findings: list[Finding]) -> None:
    for path in iter_scope_files(tree, ".md"):
        text = read_text(path)
        in_fence = False
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue  # fenced examples are illustrations, not links
            for match in MD_LINK_RE.finditer(line):
                target = match.group(1)
                if target.startswith(("http://", "https://", "mailto:", "#", "/")):
                    continue
                clean = target.split("#", 1)[0]
                if not clean:
                    continue
                if not (path.parent / clean).exists():
                    findings.append(Finding(
                        "error", rel(tree, path), lineno, "dead_links",
                        f"relative link target does not exist: {target}",
                        "fix the path or remove the link",
                    ))
    for plugin in plugin_dirs(tree):
        for sdir in skill_dirs(plugin):
            skill_md = sdir / "SKILL.md"
            refs = sdir / "references"
            if not (skill_md.is_file() and refs.is_dir()):
                continue
            body = read_text(skill_md)
            for ref in sorted(refs.rglob("*.md")):
                rel_ref = ref.relative_to(sdir).as_posix()
                if ref.parent == refs and SWITCH_REFERENCE_RE.match(ref.name):
                    continue  # the Process Policy binds it; check_switch_references owns it
                if rel_ref not in body:
                    findings.append(Finding(
                        "warning", rel(tree, ref), 1, "dead_links",
                        f"reference file not linked from SKILL.md ({rel_ref})",
                        "link every reference from SKILL.md so it is discoverable",
                    ))


def check_reference_triggers(tree: Tree, findings: list[Finding]) -> None:
    """Knowledge-skill reference links must carry a read-when trigger.

    Progressive disclosure is a checkable property: a link line that does
    not say when to read the file is a file nobody will load.
    """
    for plugin in plugin_dirs(tree):
        for sdir in skill_dirs(plugin):
            skill_md = sdir / "SKILL.md"
            if not skill_md.is_file():
                continue
            fm, body_start, body = parse_frontmatter(read_text(skill_md))
            if fm.get("exposure", "") != "internal":
                continue  # entry skills carry no knowledge references
            in_fence = False
            for lineno, line in enumerate(body.splitlines(), start=body_start):
                if line.lstrip().startswith("```"):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    continue
                if "](references/" in line and "Read when" not in line:
                    findings.append(Finding(
                        "error", rel(tree, skill_md), lineno,
                        "reference_triggers",
                        "reference link carries no read-when trigger",
                        "end the link line with '. Read when <situation>.'",
                    ))


def check_registration(tree: Tree, findings: list[Finding]) -> None:
    registered: dict[str, object] = {}
    if tree.marketplace.is_file():
        try:
            data = json.loads(read_text(tree.marketplace))
        except json.JSONDecodeError:
            return  # json_hygiene reports the parse failure
        for entry in data.get("plugins", []):
            name = entry.get("name", "")
            source = entry.get("source", "")
            registered[name] = source
    for plugin in plugin_dirs(tree):
        if plugin.name not in registered:
            findings.append(Finding(
                "error", rel(tree, plugin), 1, "registration",
                f"plugin directory '{plugin.name}' is not registered in marketplace.json",
                "add a plugins[] entry with source ./dist/claude/" + plugin.name,
            ))
        expected = release_tool.channel_source("claude", plugin.name)
        if plugin.name in registered and registered.get(plugin.name) != expected:
            findings.append(Finding(
                "error", rel(tree, tree.marketplace), 1, "registration",
                f"plugin '{plugin.name}' Claude source is"
                f" {registered.get(plugin.name)!r}, expected the channel-relative source",
                "point the Claude marketplace at its local dist/claude package",
            ))
        try:
            adapters = build_distributions.load_adapters(tree.root)
        except ValueError:
            adapters = {}
        for host, adapter in adapters.items():
            manifest = tree.root / "platforms" / host / plugin.name / "manifest.json"
            contract = tree.root / "platforms" / host / plugin.name / "host-contract.md"
            manifest_required = adapter.metadata.get("artifact_kind") == "native_marketplace"
            if (manifest_required and not manifest.is_file()) or not contract.is_file():
                findings.append(Finding(
                    "error", rel(tree, contract), 1, "registration",
                    f"plugin '{plugin.name}' lacks the {host} platform source",
                    "add its declared host contract and required native manifest under platforms/",
                ))


def check_distribution_packaging(tree: Tree, findings: list[Finding]) -> None:
    """Both host marketplaces, adapters, and generated distributions align."""
    if not tree.codex_marketplace.is_file():
        return

    def error(path: Path, message: str, remediation: str) -> None:
        findings.append(Finding(
            "error", rel(tree, path), 1, "distribution_packaging", message,
            remediation
        ))

    try:
        marketplace = json.loads(read_text(tree.codex_marketplace))
    except json.JSONDecodeError:
        return
    if marketplace.get("name") != "agent-marketplace":
        error(tree.codex_marketplace, "Codex marketplace name must be agent-marketplace",
              "keep the documented install selector stable")
    interface = marketplace.get("interface") or {}
    if interface.get("displayName") != "Agent Marketplace":
        error(tree.codex_marketplace,
              "Codex marketplace display name must be Agent Marketplace",
              "keep the public marketplace name identical across product surfaces")
    entries = {
        entry.get("name", ""): entry
        for entry in marketplace.get("plugins", [])
        if isinstance(entry, dict)
    }
    marker_name, _ = build_distributions.packaging_names(tree.root)
    for plugin in plugin_dirs(tree):
        entry = entries.get(plugin.name)
        if entry is None:
            error(tree.codex_marketplace,
                  f"Codex marketplace does not register {plugin.name}",
                  "add a policy-complete Codex marketplace entry")
            continue
        source = entry.get("source")
        expected_source = release_tool.channel_source("codex", plugin.name)
        if source != expected_source:
            error(tree.codex_marketplace,
                  f"{plugin.name} Codex source escapes the selected marketplace channel",
                  "point the Codex marketplace at its local dist/codex package")
        policy = entry.get("policy") or {}
        expected_install = "INSTALLED_BY_DEFAULT"
        if policy.get("installation") != expected_install \
                or policy.get("authentication") != "ON_INSTALL" \
                or entry.get("category") != "Engineering":
            error(tree.codex_marketplace,
                  f"{plugin.name} has incomplete or incorrect Codex policy",
                  "set installation/authentication/category to the repository contract")

        claude_archive = tree.root / "dist" / "claude" / plugin.name
        codex_archive = tree.root / "dist" / "codex" / plugin.name
        claude_manifest = (
            tree.root / "platforms" / "claude" / plugin.name / "manifest.json"
        )
        codex_manifest = (
            tree.root / "platforms" / "codex" / plugin.name / "manifest.json"
        )
        claude_archive_manifest = claude_archive / ".claude-plugin" / "plugin.json"
        codex_archive_manifest = codex_archive / ".codex-plugin" / "plugin.json"
        manifests: list[tuple[Path, dict]] = []
        for manifest_path in (
            claude_manifest,
            codex_manifest,
            claude_archive_manifest,
            codex_archive_manifest,
        ):
            try:
                manifests.append((manifest_path, json.loads(read_text(manifest_path))))
            except (OSError, json.JSONDecodeError):
                error(manifest_path, f"missing or invalid manifest for {plugin.name}",
                      "regenerate distributions and fix the platform manifest")
        names = {data.get("name", "") for _, data in manifests}
        if len(names) > 1 or names not in ({plugin.name}, set()):
            error(codex_manifest,
                  f"Claude/Codex manifest name drift for {plugin.name}",
                  "keep plugin names equal across both hosts")
        expected_display = display_title(plugin.name)
        for manifest_path, data in manifests:
            if manifest_path in {claude_manifest, claude_archive_manifest}:
                continue
            actual_display = (data.get("interface") or {}).get("displayName")
            if actual_display != expected_display:
                error(manifest_path,
                      f"{plugin.name} display name is {actual_display!r},"
                      f" expected {expected_display!r}",
                      "derive the public title from the technical plugin id"
                      " without a publisher prefix")
        for archive in (claude_archive, codex_archive):
            if not (archive / marker_name).is_file():
                error(archive, "distribution lacks its generated ownership marker",
                      "rebuild with tools/build_distributions.py")

        codex_surface = {
            path.parent.name for path in (codex_archive / "skills").glob("*/SKILL.md")
        }
        for name in codex_surface:
            metadata = codex_archive / "skills" / name / "agents" / "openai.yaml"
            metadata_text = read_text(metadata) if metadata.is_file() else ""
            if "allow_implicit_invocation: false" not in metadata_text:
                error(metadata, f"{name} lacks explicit-only Codex policy",
                      "generate agents/openai.yaml with implicit invocation disabled")
            if f"${plugin.name}:{name}" not in metadata_text:
                error(metadata, f"{name} lacks its fully namespaced Codex prompt",
                      "generate default_prompt with $<plugin>:<skill>")


def check_single_team_contract(tree: Tree, findings: list[Finding]) -> None:
    """The marketplace ships one standalone team with no plugin dependency."""
    for plugin in plugin_dirs(tree):
        problems: list[str] = []
        claude_manifest_path = (
            tree.root / "platforms" / "claude" / plugin.name / "manifest.json"
        )
        codex_manifest_path = (
            tree.root / "platforms" / "codex" / plugin.name / "manifest.json"
        )
        claude_contract_path = (
            tree.root / "platforms" / "claude" / plugin.name / "host-contract.md"
        )
        codex_contract_path = (
            tree.root / "platforms" / "codex" / plugin.name / "host-contract.md"
        )
        try:
            claude_manifest = json.loads(read_text(claude_manifest_path))
        except (OSError, json.JSONDecodeError):
            claude_manifest = {}
        dependencies = claude_manifest.get("dependencies")
        if dependencies not in ([], None):
            problems.append("Claude manifest must not declare plugin dependencies")
        try:
            codex_manifest = json.loads(read_text(codex_manifest_path))
        except (OSError, json.JSONDecodeError):
            codex_manifest = {}
        if "dependencies" in codex_manifest:
            problems.append("Codex manifest declares unsupported plugin dependencies")
        long_description = str(
            (codex_manifest.get("interface") or {}).get("longDescription", "")
        )
        if "requires centralized project governance" in long_description.lower():
            problems.append("Codex visible description must not require another plugin")
        claude_contract = read_text(claude_contract_path) \
            if claude_contract_path.is_file() else ""
        for token in ("team_guard.py", "vault_hook.py", "One Software Engineering Team", "AskUserQuestion", "generated project check"):
            if token.lower() not in claude_contract.lower():
                problems.append(f"Claude host contract lacks {token!r}")
        codex_contract = read_text(codex_contract_path) \
            if codex_contract_path.is_file() else ""
        for token in ("team_guard.py", "vault_hook.py", "One Software Engineering Team", "request_user_input", "generated project check"):
            if token.lower() not in codex_contract.lower():
                problems.append(f"Codex host contract lacks {token!r}")
        hook_contracts = (
            (
                "Claude",
                tree.root / "platforms" / "claude" / plugin.name
                / "overlay" / "hooks" / "hooks.json",
                ("team_guard.py register", "vault_hook.py pre", "vault_hook.py post", "Write|Edit|Bash|PowerShell"),
            ),
            (
                "Codex",
                tree.root / "platforms" / "codex" / plugin.name
                / "overlay" / "hooks" / "hooks.json",
                ("team_guard.py register", "vault_hook.py pre", "vault_hook.py post", "Write|Edit|apply_patch|Bash"),
            ),
        )
        for label, hook_path, tokens in hook_contracts:
            hook_text = read_text(hook_path) if hook_path.is_file() else ""
            for token in tokens:
                if token not in hook_text:
                    problems.append(f"{label} hooks lack {token!r}")
        if problems:
            findings.append(Finding(
                "error", rel(tree, claude_manifest_path), 1,
                "single_team_contract",
                f"team plugin '{plugin.name}' has an incomplete standalone contract: "
                + "; ".join(problems),
                "remove plugin dependencies and keep both host preflights scoped"
                " to the project-local workspace",
            ))


PACKAGED_STATE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def check_packaged_state_files(tree: Tree, findings: list[Finding]) -> None:
    """Tracked and distributed product surfaces contain no state database."""
    roots = (
        tree.plugins_dir,
        tree.root / "platforms",
        tree.root / "dist",
        tree.root / ".release",
        tree.root / ".claude-plugin",
        tree.root / ".agents" / "plugins",
    )
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(candidate for candidate in root.rglob("*")
                           if candidate.is_file()):
            relative = rel(tree, path)
            if path.suffix.casefold() in PACKAGED_STATE_SUFFIXES:
                findings.append(Finding(
                    "error", relative, 1,
                    "packaged_state_files",
                    "state database is present in a packaged or release surface",
                    "remove runtime state from tracked and distributed files",
                ))


def _walk_keys(obj: object, path: str, out: list[tuple[str, str]]) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.append((path, key))
            _walk_keys(value, f"{path}.{key}", out)
    elif isinstance(obj, list):
        for item in obj:
            _walk_keys(item, path, out)


def check_json_hygiene(tree: Tree, findings: list[Finding]) -> None:
    json_paths: list[Path] = []
    if tree.marketplace.is_file():
        json_paths.append(tree.marketplace)
    if tree.codex_marketplace.is_file():
        json_paths.append(tree.codex_marketplace)
    if tree.plugins_dir.is_dir():
        json_paths.extend(sorted(tree.plugins_dir.rglob("*.json")))
    platforms = tree.root / "platforms"
    if platforms.is_dir():
        json_paths.extend(sorted(platforms.rglob("*.json")))
    for path in json_paths:
        try:
            data = json.loads(read_text(path))
        except json.JSONDecodeError as exc:
            findings.append(Finding(
                "error", rel(tree, path), max(exc.lineno, 1), "json_hygiene",
                f"invalid JSON: {exc.msg}",
                "fix the syntax",
            ))
            continue
        is_hooks_manifest = path.name == "hooks.json" and path.parent.name == "hooks"
        is_codex_schema = (
            path == tree.codex_marketplace
            or (
                path.name == "manifest.json"
                and "platforms" in path.parts
                and "codex" in path.parts
            )
        )
        # Vault payload files under a plugin's templates/ carry the vault
        # app's own key schema (camelCase settings, kebab plugin ids); only
        # the parse requirement applies there.
        is_vault_payload = "templates" in path.parts and ".obsidian" in path.parts
        keys: list[tuple[str, str]] = []
        _walk_keys(data, "$", keys)
        for where, key in keys:
            if is_vault_payload or is_codex_schema:
                continue
            if (is_hooks_manifest and where == "$.hooks"
                    and HOOK_EVENT_KEY_RE.match(key)):
                continue  # hook event names are the host platform's schema
            if not SNAKE_KEY_RE.match(key):
                findings.append(Finding(
                    "error", rel(tree, path), 1, "json_hygiene",
                    f"JSON key '{key}' at {where} is not snake_case",
                    "keys are lowercase snake_case",
                ))


def check_orchestrator_integrity(tree: Tree, findings: list[Finding]) -> None:
    for plugin in plugin_dirs(tree):
        agent_stems = {p.stem for p in agent_files(plugin)}
        flows = plugin / "flows"
        if flows.is_dir():
            for flow in sorted(flows.glob("*.md")):
                text = read_text(flow)
                if CONSTITUTION_PLACEHOLDER not in text:
                    findings.append(Finding(
                        "error", rel(tree, flow), 1, "orchestrator_integrity",
                        f"flow lacks the {CONSTITUTION_PLACEHOLDER} spawn placeholder",
                        "the spawn template must paste the constitution body",
                    ))
        pattern = re.compile(AGENT_ROLE_SUFFIX_RE_TPL.format(plugin=re.escape(plugin.name)))
        for path in sorted(plugin.rglob("*.md")):
            text = read_text(path)
            for lineno, line in enumerate(text.splitlines(), start=1):
                for match in pattern.finditer(line):
                    stem = match.group(1)
                    if stem not in agent_stems:
                        findings.append(Finding(
                            "error", rel(tree, path), lineno, "orchestrator_integrity",
                            f"spawn reference '{plugin.name}-{stem}' resolves to no agent file",
                            f"create agents/{stem}.md or fix the reference",
                        ))


CHOICE_GATE_MARKER = "explicit user choice"
CHOICE_GATE_TOKEN = "choice gate"
CHOICE_GATE_WINDOW = 3


def check_choice_gate(tree: Tree, findings: list[Finding]) -> None:
    """The decision-gate formula, per site (a window of lines absorbs
    prose wrapping): a gate declared with the marker phrase must name
    the host-neutral choice gate nearby, so its discipline cannot be silently
    stripped from a gate site."""
    for path in iter_scope_files(tree, ".md"):
        lines = read_text(path).splitlines()
        for idx, line in enumerate(lines):
            lo = max(0, idx - CHOICE_GATE_WINDOW)
            window = lines[lo:idx + CHOICE_GATE_WINDOW + 1]
            if (CHOICE_GATE_MARKER in line
                    and not any(CHOICE_GATE_TOKEN in w for w in window)):
                findings.append(Finding(
                    "error", rel(tree, path), idx + 1, "choice_gate",
                    f"gate marker ('{CHOICE_GATE_MARKER}') without a"
                    f" {CHOICE_GATE_TOKEN} named nearby",
                    "decision gates use the host-neutral choice-gate contract;"
                    " state it at the gate site",
                ))
    for host, token in (
        ("claude", "AskUserQuestion"),
        ("codex", "request_user_input"),
    ):
        contract = (tree.root / "platforms" / host
                    / "software-engineering-team" / "host-contract.md")
        if contract.is_file() and token not in read_text(contract):
            findings.append(Finding(
                "error", rel(tree, contract), 1, "choice_gate",
                f"{host} host contract lacks {token}",
                "map the canonical gate to the declared host choice surface",
            ))
    # Setup fields are project-specific and are checked by the owning config
    # writer. This validator only requires host-native gate wording above.


# Fallback for runtimes that predate sys.stdlib_module_names.
FALLBACK_STDLIB = frozenset(
    "__future__ abc argparse ast asyncio base64 bisect calendar collections "
    "configparser contextlib copy csv ctypes dataclasses datetime decimal difflib "
    "enum errno fcntl fnmatch functools getpass glob gzip hashlib heapq hmac html "
    "http importlib inspect io itertools json logging math mimetypes "
    "msvcrt multiprocessing operator os pathlib pickle platform pprint queue random "
    "re secrets shlex shutil signal socket statistics string struct "
    "stat subprocess sys tarfile tempfile textwrap threading time tomllib "
    "traceback types typing unicodedata unittest urllib uuid warnings "
    "webbrowser xml zipfile zlib".split()
)


def check_stdlib_only(tree: Tree, findings: list[Finding]) -> None:
    stdlib = set(getattr(sys, "stdlib_module_names", ())) or FALLBACK_STDLIB
    scanned: set[Path] = set()

    def scan(scripts: Path, local: set[str]) -> None:
        if not scripts.is_dir():
            return
        for script in sorted(scripts.glob("*.py")):
            if script in scanned:
                continue
            scanned.add(script)
            for lineno, line in enumerate(read_text(script).splitlines(), start=1):
                stripped = line.strip()
                module = ""
                if stripped.startswith("import "):
                    module = stripped[7:].split()[0].split(".")[0].rstrip(",")
                elif stripped.startswith("from "):
                    module = stripped[5:].split()[0].split(".")[0]
                if not module or module in ("", "."):
                    continue
                if module in stdlib or module in local:
                    continue
                findings.append(Finding(
                    "error", rel(tree, script), lineno, "stdlib_only",
                    f"non-stdlib import '{module}'",
                    "skill and platform runtime scripts run anywhere;"
                    " use only the standard library",
                ))

    for plugin in plugin_dirs(tree):
        script_dirs = [sdir / "scripts" for sdir in skill_dirs(plugin)]
        script_dirs.append(plugin / "scripts")  # plugin-level runtime scripts
        for scripts in script_dirs:
            if not scripts.is_dir():
                continue
            local = {p.stem for p in scripts.glob("*.py")}
            shared_scripts = (
                tree.root / "platforms" / "shared" / plugin.name
                / "overlay" / "scripts"
            )
            if shared_scripts.is_dir():
                local.update(p.stem for p in shared_scripts.glob("*.py"))
            scan(scripts, local)

    platform_dirs = sorted(
        path for path in (tree.root / "platforms").glob("*/*/overlay/scripts")
        if path.is_dir()
    )
    for scripts in platform_dirs:
        owner = scripts.parents[1].name
        owner_dirs = [
            path for path in platform_dirs
            if path.parents[1].name in {owner, "_team"}
        ]
        canonical = tree.plugins_dir / owner / "scripts"
        if canonical.is_dir():
            owner_dirs.append(canonical)
        if owner == "_team":
            owner_dirs.extend(
                plugin / "scripts" for plugin in plugin_dirs(tree)
                if (plugin / "scripts").is_dir()
            )
        local = {
            path.stem for directory in owner_dirs for path in directory.glob("*.py")
        }
        scan(scripts, local)


NAIVE_CLOCK_RE = re.compile(r"\bdate\.today\(\)|\bdatetime\.now\(\s*\)|\butcnow\(")


def check_naive_clock(tree: Tree, findings: list[Finding]) -> None:
    """One clock: plugin scripts read time as datetime.now(timezone.utc).
    A naive or local clock call (date.today(), no-arg datetime.now(),
    utcnow()) puts local or deprecated time into artifacts and diverges
    from the UTC values every other writer records."""
    all_script_dirs: set[Path] = set()
    for plugin in plugin_dirs(tree):
        candidates = [sdir / "scripts" for sdir in skill_dirs(plugin)]
        candidates.append(plugin / "scripts")
        all_script_dirs.update(path for path in candidates if path.is_dir())
    all_script_dirs.update(
        path for path in (tree.root / "platforms").glob("*/*/overlay/scripts")
        if path.is_dir()
    )
    for scripts in sorted(all_script_dirs):
        for script in sorted(scripts.glob("*.py")):
            for lineno, line in enumerate(read_text(script).splitlines(), start=1):
                if NAIVE_CLOCK_RE.search(line):
                    findings.append(Finding(
                        "error", rel(tree, script), lineno, "naive_clock",
                        "local or naive clock call",
                        "plugin and platform runtime scripts read the clock as"
                        " datetime.now(timezone.utc); local time never"
                        " enters artifacts",
                    ))


PACKAGED_SCRIPT_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])((?:scripts|skill-content/[a-z0-9-]+/scripts)"
    r"/[A-Za-z0-9_./-]+\.py)"
)
DIRECT_SCRIPT_REF_RE = re.compile(r"\bpython3?\s+scripts/[A-Za-z0-9_./-]+\.py\b")


def check_script_references(tree: Tree, findings: list[Finding]) -> None:
    """Packaged script references resolve from the installed plugin root."""
    for plugin in plugin_dirs(tree):
        for path in sorted(plugin.rglob("*.md")):
            for lineno, line in enumerate(read_text(path).splitlines(), start=1):
                if DIRECT_SCRIPT_REF_RE.search(line):
                    findings.append(Finding(
                        "error", rel(tree, path), lineno, "script_references",
                        "project-relative Python invocation cannot resolve an"
                        " installed plugin script",
                        "name the packaged plugin-relative script and let the"
                        " host wrapper resolve its installed root",
                    ))
                for match in PACKAGED_SCRIPT_REF_RE.finditer(line):
                    relpath = match.group(1)
                    candidates = [plugin / relpath]
                    try:
                        relative_parts = path.relative_to(plugin).parts
                    except ValueError:
                        relative_parts = ()
                    if len(relative_parts) >= 2 \
                            and relative_parts[0] == "skill-content":
                        candidates.append(
                            plugin / "skill-content" / relative_parts[1]
                            / relpath
                        )
                    if any(candidate.is_file() for candidate in candidates) \
                            or line.lstrip().startswith("#"):
                        continue
                    if not any(candidate.is_file() for candidate in candidates):
                        findings.append(Finding(
                            "error", rel(tree, path), lineno, "script_references",
                            f"packaged script does not exist: {relpath}",
                            "ship the script or fix the reference",
                        ))


PLACEHOLDER_RE = re.compile(r"\{\{([a-z0-9_]+)\}\}")

SPAWN_SHAPE_HEADING_RE = re.compile(r"^#{2,3}\s+Spawn Shape\s*$", re.MULTILINE)


def check_spawn_shape_constitution(tree: Tree, findings: list[Finding]) -> None:
    """A spawn-prompt template outside a flow (a 'Spawn Shape' section in any
    plugin markdown) must paste the constitution, exactly as flows must."""
    for path in iter_scope_files(tree, ".md"):
        if tree.plugins_dir not in path.parents:
            continue
        text = read_text(path)
        if (SPAWN_SHAPE_HEADING_RE.search(text)
                and CONSTITUTION_PLACEHOLDER not in text):
            findings.append(Finding(
                "error", rel(tree, path), 1, "spawn_shape_constitution",
                "spawn shape lacks the {{constitution}} placeholder",
                "every spawn prompt template pastes the constitution verbatim",
            ))


def check_template_placeholders(tree: Tree, findings: list[Finding]) -> None:
    """Every {{placeholder}} a template ships must be named by one of the
    plugin's skills (the skill that substitutes it); an unmentioned token
    ships literally into consumer repositories."""
    for plugin in plugin_dirs(tree):
        templates = plugin / "templates"
        if not templates.is_dir():
            continue
        canonical_skills = skill_dirs(plugin)
        skill_text = "".join(
            read_text(skill / "SKILL.md")
            for skill in canonical_skills
            if (skill / "SKILL.md").is_file()
        )
        for tpl in sorted(p for p in templates.rglob("*") if p.is_file()):
            parts = tpl.parts
            if ".obsidian" in parts and "plugins" in parts:
                # Vendored third-party vault plugins are shipped verbatim;
                # their bundles carry their own template tokens, which are
                # never substitutions this plugin's skills owe.
                continue
            for lineno, line in enumerate(read_text(tpl).splitlines(), start=1):
                for token in PLACEHOLDER_RE.findall(line):
                    if token == "workspace" and "project-instructions" in tpl.parts:
                        continue
                    if f"{{{{{token}}}}}" not in skill_text:
                        findings.append(Finding(
                            "error", rel(tree, tpl), lineno,
                            "template_placeholders",
                            f"template placeholder {{{{{token}}}}} is not"
                            " named by any skill",
                            "name the substitution in the materializing"
                            " skill or drop the placeholder",
                        ))


def check_project_instruction_contract(
    tree: Tree, findings: list[Finding]
) -> None:
    """Project instructions have one common source and whole-file host outputs."""
    problems: list[str] = []
    templates = tree.plugins_dir / "software-engineering-team" / "templates"
    common = templates / "project-instructions" / "common.md"
    if not common.is_file():
        problems.append("common project instruction fragment is missing")
    for filename in ("agent-marketplace.md", "me.md", "profile.md"):
        if not (templates / "memory" / filename).is_file():
            problems.append(f"memory template is missing {filename}")
    try:
        adapters = build_distributions.load_adapters(tree.root)
    except ValueError as exc:
        problems.append(str(exc))
        adapters = {}
    host_deltas: dict[str, Path] = {}
    for host in adapters:
        delta = (
            tree.root / "platforms" / host / "_team" / "overlay"
            / "templates" / "project-instructions" / "host.md"
        )
        host_deltas[host] = delta
        if not delta.is_file():
            problems.append(f"{host} shared host instruction fragment is missing")
    allowed_workspace_fragments = {common, *host_deltas.values()}
    caps = (tree.limits or {}).get("authoring_caps", {})
    max_bytes = int(caps.get("project_instruction_max_bytes", 0) or 0)
    max_lines = int(caps.get("project_instruction_max_lines", 0) or 0)
    for plugin in plugin_dirs(tree):
        team = plugin / "templates" / "project-instructions" / "team.md"
        allowed_workspace_fragments.add(team)
        if not team.is_file():
            problems.append(f"{plugin.name} lacks templates/project-instructions/team.md")
        registry_path = tree.root / "dist" / next(iter(adapters), "claude") / plugin.name \
            / "templates" / "project-instruction-surfaces.json"
        try:
            surfaces = json.loads(read_text(registry_path)).get("surfaces", [])
        except (OSError, json.JSONDecodeError):
            surfaces = []
            problems.append(f"generated instruction surface registry is missing: {registry_path}")
        surface_by_host = {
            item.get("host_id"): item for item in surfaces if isinstance(item, dict)
        }
        if set(surface_by_host) != set(adapters):
            problems.append(f"generated instruction surface registry differs from adapters for {plugin.name}")
        for host in adapters:
            surface = surface_by_host.get(host, {})
            filename = surface.get("filename", "")
            owner_host = surface.get("owner_host", "")
            companion = surface.get("user_companion", "")
            migrations = surface.get("migrates_from_owners", [])
            if not isinstance(filename, str) or not isinstance(owner_host, str) \
                    or not isinstance(companion, str) or not companion \
                    or not isinstance(migrations, list) \
                    or not all(isinstance(value, str) for value in migrations):
                problems.append(f"generated {host}/{plugin.name} instruction surface is invalid")
                continue
            fork = (
                tree.root / "platforms" / host / plugin.name / "overlay"
                / "templates" / filename
            )
            if fork.exists():
                problems.append(f"{fork.relative_to(tree.root)} is a per-team host fork")
            generated = tree.root / "dist" / host / plugin.name / "templates" / filename
            if not generated.is_file():
                problems.append(f"generated {host}/{plugin.name}/{filename} is missing")
                continue
            text = read_text(generated)
            delta = host_deltas[host]
            if common.is_file() and team.is_file() and delta.is_file():
                expected = (
                    f"<!-- generated by agent-marketplace {plugin.name} for"
                    f" {owner_host}; do not edit by hand -->\n\n"
                    + "\n\n".join(
                        path.read_text(encoding="utf-8").strip()
                        for path in (common, team, delta)
                    )
                    + "\n"
                )
                if text != expected:
                    problems.append(
                        f"generated {host}/{plugin.name}/{filename} differs"
                        " from common + team + host composition"
                    )
            if max_bytes and len(text.encode("utf-8")) > max_bytes:
                problems.append(f"generated {host}/{plugin.name}/{filename} exceeds byte cap")
            if max_lines and len(text.splitlines()) > max_lines:
                problems.append(f"generated {host}/{plugin.name}/{filename} exceeds line cap")
    for base in (tree.plugins_dir, tree.root / "platforms"):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.md")):
            if "{{workspace}}" in read_text(path) \
                    and path not in allowed_workspace_fragments:
                problems.append(
                    f"{path.relative_to(tree.root)} uses {{workspace}} outside"
                    " an allowed project-instruction fragment"
                )
    root_claude = tree.root / "CLAUDE.md"
    root_text = read_text(root_claude) if root_claude.is_file() else ""
    for required in ("@AGENTS.md", "@memory/me.md"):
        if required not in root_text:
            problems.append(f"root CLAUDE.md lacks canonical import {required}")
    if problems:
        findings.append(Finding(
            "error", rel(tree, common), 1, "project_instruction_contract",
            "; ".join(problems),
            "restore common and memory sources, one team fragment, shared"
            " host deltas, canonical root imports, and regenerated distributions",
        ))


def check_ba_schema_shape(tree: Tree, findings: list[Finding]) -> None:
    """Any shipped space-schema.json must be well-formed: the compiler is
    parameterized by it, so a malformed schema is a broken product."""
    for plugin in plugin_dirs(tree):
        for skill in skill_dirs(plugin):
            path = skill / "data" / "space-schema.json"
            if not path.is_file():
                continue
            def err(message: str, fix: str) -> None:
                findings.append(Finding(
                    "error", rel(tree, path), 1, "ba_schema_shape", message, fix))
            try:
                schema = json.loads(read_text(path))
            except json.JSONDecodeError as exc:
                err(f"schema is not valid JSON: {exc}", "fix the JSON syntax")
                continue
            if not isinstance(schema.get("schema_version"), int):
                err("schema_version must be an integer",
                    "version the schema so the compiler can refuse unknowns")
            doc_types = schema.get("doc_types")
            if not isinstance(doc_types, dict) or not doc_types:
                err("doc_types must be a non-empty object",
                    "declare every document type the compiler may see")
                continue
            statuses = schema.get("statuses", [])
            if "approved" not in statuses:
                err("statuses must include 'approved'",
                    "gates hinge on the approved status")
            try:
                re.compile(schema.get("id_format", ""))
            except re.error:
                err("id_format does not compile as a regex",
                    "id_format is the id law; it must compile")
            row_schemas = schema.get("row_schemas", {})
            all_columns = {c for row in row_schemas.values()
                           if isinstance(row, dict)
                           for c in (row.get("columns") or [])}
            citation_columns = schema.get("id_citation_columns")
            if not (isinstance(citation_columns, list) and citation_columns
                    and all(isinstance(c, str) and c
                            for c in citation_columns)):
                err("id_citation_columns must be a non-empty list of column"
                    " names",
                    "the columns whose cells cite ids as wikilinks are"
                    " schema-listed, never inferred")
            else:
                for column in citation_columns:
                    if column not in all_columns:
                        err(f"id_citation_column '{column}' appears in no"
                            " row schema",
                            "every citation column is a real row-schema"
                            " column")
            seen_prefixes: dict[str, str] = {}
            for type_name, spec in sorted(doc_types.items()):
                for key, expected in (("required_sections", list),
                                      ("mints", list), ("gate_blocking", bool)):
                    if not isinstance(spec.get(key), expected):
                        err(f"doc type '{type_name}' key '{key}' must be"
                            f" {expected.__name__}",
                            "every doc type declares sections, mints and gating")
                location = spec.get("location")
                if isinstance(location, dict) \
                        and location.get("kind") == "folder":
                    suffix = location.get("filename_suffix")
                    if not (isinstance(suffix, str) and suffix):
                        err(f"doc type '{type_name}' folder location must"
                            " carry a non-empty filename_suffix",
                            "typed content files carry a -<suffix>; the"
                            " suffix is schema data, never implied")
                for kind in spec.get("mints", []) or []:
                    if str(kind).lower() not in row_schemas:
                        err(f"doc type '{type_name}' mints '{kind}' which has"
                            " no row schema",
                            "every minted kind carries a row schema")
            for kind, row in sorted(row_schemas.items()):
                columns = row.get("columns", [])
                if not isinstance(columns, list) or "id" not in columns:
                    err(f"row schema '{kind}' must list columns including 'id'",
                        "rows are the only carrier of minted ids")
                section = row.get("section", "")
                if section in seen_prefixes:
                    err(f"row schema '{kind}' reuses section '{section}'"
                        f" already claimed by {seen_prefixes[section]}",
                        "each id kind mints in its own section token")
                seen_prefixes[section] = kind


def _under_plugin_templates(tree: Tree, path: Path) -> bool:
    try:
        parts = path.relative_to(tree.plugins_dir).parts
    except ValueError:
        return False
    return len(parts) > 1 and parts[1] == "templates"


def check_wikilink_ban(tree: Tree, findings: list[Finding]) -> None:
    """Marketplace content links with standard relative markdown links; the
    wikilink grammar belongs to the product vault and ships only under a
    plugin's templates/ (consumer-bound seeds). Illustrations live in fenced
    blocks or inline code spans, which render as code, not links."""
    for path in iter_scope_files(tree, ".md"):
        if _under_plugin_templates(tree, path):
            continue  # consumer-bound vault seeds carry real wikilinks
        text = read_text(path)
        in_fence = False
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if "[[" in INLINE_CODE_RE.sub("", line):
                findings.append(Finding(
                    "error", rel(tree, path), lineno, "wikilink_ban",
                    "wikilink syntax outside a code example",
                    "marketplace content uses standard relative links; show"
                    " wikilink grammar only in fenced blocks or inline code",
                ))


def check_version_sync(tree: Tree, findings: list[Finding]) -> None:
    """One canonical plugin version drives every supported host and runtime output."""
    problems = [
        problem for problem in release_tool.validate_version_surfaces(tree.root)
        if "marketplace source must stay inside the selected channel" not in problem
    ]
    for problem in problems:
        findings.append(Finding(
            "error", "versions.json", 1, "version_sync", problem,
            "update versions.json once, run the release sync and rebuild every host",
        ))


def check_vault_wiring(tree: Tree, findings: list[Finding]) -> None:
    """A plugin that ships the vault-law skill wires every docs-facing
    surface to it: an entry or flow that names the docs tree must also
    name the obsidian-vault skill, so the law cannot be skipped by
    omission. Deliberately broad: readers pay one pointer sentence."""
    for plugin in plugin_dirs(tree):
        skills = skill_dirs(plugin)
        if not any(path.name == "obsidian-vault" for path in skills):
            continue
        surfaces = sorted(
            path / "SKILL.md" for path in skills if (path / "SKILL.md").is_file()
        )
        flows = plugin / "flows"
        if flows.is_dir():
            surfaces.extend(sorted(flows.glob("*.md")))
        for path in surfaces:
            text = read_text(path)
            if "workspace/docs" in text and "obsidian-vault" not in text:
                findings.append(Finding(
                    "error", rel(tree, path), 1, "vault_wiring",
                    "surface names the docs tree without the vault-law skill",
                    "add the obsidian-vault load or pointer sentence; every"
                    " docs-facing surface names the law"))


def check_vault_policy_shape(tree: Tree, findings: list[Finding]) -> None:
    """Any shipped vault-policy.json must be well-formed and in parity with
    the plugin's templates/vault seeds: the vault checker, the seeds and the
    graph config are all parameterized by it, so drift is a broken product."""
    for plugin in plugin_dirs(tree):
        for skill in skill_dirs(plugin):
            path = skill / "data" / "vault-policy.json"
            if not path.is_file():
                continue
            def err(message: str, fix: str) -> None:
                findings.append(Finding(
                    "error", rel(tree, path), 1, "vault_policy_shape",
                    message, fix))
            try:
                policy = json.loads(read_text(path))
            except json.JSONDecodeError as exc:
                err(f"policy is not valid JSON: {exc}", "fix the JSON syntax")
                continue
            if not isinstance(policy.get("schema_version"), int):
                err("schema_version must be an integer",
                    "version the policy so the checker can refuse unknowns")
            for key in ("vault_root_dirname", "home_file",
                        "maps_dir", "generated_marker_prefix", "attachments_dir",
                        "artifact_directory_name"):
                value = policy.get(key)
                if not (isinstance(value, str) and value):
                    err(f"'{key}' must be a non-empty string",
                        "every structural name is policy-driven, never implied")
            extensions = policy.get("attachment_extensions")
            if (not isinstance(extensions, list) or not extensions
                    or not all(isinstance(value, str)
                               and re.fullmatch(r"\.[a-z0-9]+", value)
                               for value in extensions)
                    or len(extensions) != len(set(extensions))):
                err("attachment_extensions must be unique lowercase suffixes",
                    "declare every supported binary attachment format once")
            subtrees = policy.get("subtrees")
            if (not isinstance(subtrees, list) or not subtrees
                    or not all(isinstance(s, str) and KEBAB_RE.match(s)
                               for s in subtrees)):
                err("subtrees must be a non-empty list of kebab-case names",
                    "list every note-bearing top-level directory of the vault")
                subtrees = []
            extra_maps = policy.get("extra_maps", [])
            if (not isinstance(extra_maps, list)
                    or not all(isinstance(s, str) and KEBAB_RE.match(s)
                               for s in extra_maps)):
                err("extra_maps must be a list of kebab-case names",
                    "name the non-subtree map notes (or ship an empty list)")
                extra_maps = []
            machine_dirs = policy.get("machine_dirs")
            if (not isinstance(machine_dirs, list) or not machine_dirs
                    or not all(isinstance(s, str) and s and "/" not in s
                               for s in machine_dirs)):
                err("machine_dirs must be a non-empty list of directory"
                    " names",
                    "compiler-owned directories are policy-listed so the"
                    " checker tolerates them mechanically")
            banned = policy.get("banned_basenames")
            if (not isinstance(banned, list) or not banned
                    or not all(isinstance(s, str) and s.endswith(".md")
                               for s in banned)):
                err("banned_basenames must be a non-empty list of .md"
                    " basenames",
                    "generic basenames are policy-listed; the naming law is"
                    " data, not prose")
            community = policy.get("community_plugins")
            if (not isinstance(community, list)
                    or not all(isinstance(s, str) and KEBAB_RE.match(s)
                               for s in community)):
                err("community_plugins must be a list of kebab-case plugin"
                    " ids",
                    "the vetted community-plugin set is policy-listed; an"
                    " empty list means none are allowed")
                community = []
            graph_search = policy.get("graph_search")
            if not isinstance(graph_search, str):
                err("graph_search must be a string",
                    "the global graph filter is policy data")
                graph_search = None
            color_specs = policy.get("graph_color_groups")
            color_groups = None
            palette = None
            if not isinstance(color_specs, list) or not color_specs:
                err("graph_color_groups must be a non-empty list of"
                    " {id, query, rgb} objects",
                    "bind each stable group id to its graph query and"
                    " 0..16777215 RGB color in one policy record")
            else:
                valid_specs = True
                ids: list[str] = []
                queries: list[str] = []
                rgbs: list[int] = []
                for group in color_specs:
                    if (not isinstance(group, dict)
                            or set(group) != {"id", "query", "rgb"}):
                        err("each graph_color_groups entry must contain"
                            " exactly id, query and rgb",
                            "keep group identity, selector and standard"
                            " color together so ordering cannot reassign"
                            " colors")
                        valid_specs = False
                        continue
                    group_id = group.get("id")
                    query = group.get("query")
                    rgb = group.get("rgb")
                    if not isinstance(group_id, str) \
                            or not KEBAB_RE.match(group_id):
                        err("graph color group ids must be non-empty"
                            " kebab-case strings",
                            "use a stable semantic id such as rule-set")
                        valid_specs = False
                    if not isinstance(query, str) or not query:
                        err("graph color group queries must be non-empty"
                            " strings",
                            "declare the exact Obsidian graph query")
                        valid_specs = False
                    elif "|" in query or re.search(r"tag:#\S*\*", query):
                        err(f"graph_color_groups query '{query}' uses"
                            " grammar the graph does not support",
                            "graph queries have no pipe-OR and no tag"
                            " wildcards; write OR-joined full tags")
                        valid_specs = False
                    if (not isinstance(rgb, int) or isinstance(rgb, bool)
                            or not 0 <= rgb <= 0xFFFFFF):
                        err("graph color group rgb values must be integers"
                            " from 0 through 16777215",
                            "store each standard six-digit RGB value as"
                            " its decimal integer")
                        valid_specs = False
                    if isinstance(group_id, str):
                        ids.append(group_id)
                    if isinstance(query, str):
                        queries.append(query)
                    if isinstance(rgb, int) and not isinstance(rgb, bool):
                        rgbs.append(rgb)
                if len(ids) != len(set(ids)):
                    err("graph color group ids must be unique",
                        "one semantic id owns one standard color")
                    valid_specs = False
                if len(queries) != len(set(queries)):
                    err("graph color group queries must be unique",
                        "one graph selector owns one standard color")
                    valid_specs = False
                if valid_specs:
                    color_groups = queries
                    palette = list(zip(queries, rgbs))
            hubs = policy.get("hubs")
            if not isinstance(hubs, list):
                err("hubs must be a list of {note, covers} objects",
                    "the hub ladder is policy data; ship an empty list when"
                    " no subtree has hubs")
            else:
                for entry in hubs:
                    if (not isinstance(entry, dict)
                            or not (isinstance(entry.get("note"), str)
                                    and entry.get("note", "").endswith(".md"))
                            or not (isinstance(entry.get("covers"), str)
                                    and entry.get("covers"))):
                        err("each hubs entry needs a .md note glob and a"
                            " covers glob",
                            "a hub entry names the hub file pattern and the"
                            " subtree it owns")
                        continue
                    if entry["note"].rsplit("/", 1)[0] != entry["covers"]:
                        err(f"hubs note '{entry['note']}' does not live in"
                            f" its covers directory '{entry['covers']}'",
                            "the hub file sits at the root of the tree it"
                            " covers; the ladder and the matcher share"
                            " segments")
            namespaces = policy.get("tag_namespaces")
            if (not isinstance(namespaces, list) or not namespaces
                    or not all(isinstance(s, str) and KEBAB_RE.match(s)
                               for s in namespaces)):
                err("tag_namespaces must be a non-empty list of kebab-case names",
                    "the tag vocabulary is closed; declare its namespaces")
            trees = policy.get("decision_trees")
            if not isinstance(trees, dict):
                err("decision_trees must be an object",
                    "declare each decision tree's path and id grammar")
                trees = {}
            for tree_name, spec in sorted(trees.items()):
                if not isinstance(spec, dict):
                    err(f"decision tree '{tree_name}' must be an object",
                        "give it path, id_prefix and id_min_width")
                    continue
                if not (isinstance(spec.get("path"), str) and spec.get("path")):
                    err(f"decision tree '{tree_name}' needs a non-empty path",
                        "point at the decisions directory inside the vault")
                if not re.fullmatch(r"[A-Z][A-Z0-9]*", str(spec.get("id_prefix", ""))):
                    err(f"decision tree '{tree_name}' id_prefix must be"
                        " uppercase letters/digits",
                        "ids read as PREFIX-number; the prefix is the law")
                width = spec.get("id_min_width")
                if not isinstance(width, int) or isinstance(width, bool) or width < 1:
                    err(f"decision tree '{tree_name}' id_min_width must be a"
                        " positive integer",
                        "ids are zero-padded to the minimum width; more digits"
                        " stay legal")
                if spec.get("id_source") != "alias":
                    err(f"decision tree '{tree_name}' id_source must be 'alias'",
                        "record ids live in frontmatter aliases")
                if not isinstance(spec.get("render_index"), bool):
                    err(f"decision tree '{tree_name}' must carry a boolean"
                        " render_index",
                        "each tree declares its index stance explicitly;"
                        " no default")
            for key in ("generated_views", "generated_subtrees"):
                value = policy.get(key)
                if (not isinstance(value, list)
                        or not all(isinstance(s, str) and s for s in value)):
                    err(f"'{key}' must be a list of non-empty strings",
                        "generated surfaces are policy-listed, never inferred")
            prop_types = policy.get("property_types")
            if not isinstance(prop_types, dict) or not prop_types:
                err("property_types must be a non-empty object",
                    "one value type per frontmatter key, vault-wide")
                prop_types = {}
            else:
                for key, value in sorted(prop_types.items()):
                    if value not in OBSIDIAN_PROPERTY_TYPES:
                        err(f"property_types['{key}'] value '{value}' is not"
                            f" one of {sorted(OBSIDIAN_PROPERTY_TYPES)}",
                            "use the vault app's property type enum")
            lazy_fragments = policy.get("lazy_fragments")
            if not isinstance(lazy_fragments, dict):
                err("lazy_fragments must be an object of property lists",
                    "declare each lazy payload fragment as a list of policy properties")
            else:
                for fragment, properties in sorted(lazy_fragments.items()):
                    if (not isinstance(properties, list)
                            or not all(isinstance(key, str) and key for key in properties)):
                        err(f"lazy_fragments['{fragment}'] must be a list of property names",
                            "use one non-empty property name per lazy fragment entry")
                        continue
                    for key in properties:
                        if key not in prop_types:
                            err(f"lazy_fragments['{fragment}'] names property '{key}' missing from property_types",
                                "declare every lazy fragment property in the vault-wide property type map")
            extra_types = policy.get("extra_doc_types")
            if (not isinstance(extra_types, list)
                    or not all(isinstance(s, str) and KEBAB_RE.match(s)
                               for s in extra_types)):
                err("extra_doc_types must be a list of kebab-case type"
                    " names",
                    "non-schema doc types (navigation, architecture and"
                    " design layers) are policy-listed so the graph legend"
                    " can be judged complete")
                extra_types = []
            path_patterns = policy.get("type_path_patterns")
            if not isinstance(path_patterns, dict) or not path_patterns:
                err("type_path_patterns must be a non-empty object",
                    "close every non-analysis document type over explicit"
                    " vault-relative path regexes")
                path_patterns = {}
            else:
                for doc_type, patterns in sorted(path_patterns.items()):
                    if (not isinstance(doc_type, str)
                            or not KEBAB_RE.match(doc_type.replace("_", "-"))
                            or not isinstance(patterns, list) or not patterns
                            or not all(isinstance(item, str) and item
                                       for item in patterns)):
                        err(f"type_path_patterns['{doc_type}'] must be a"
                            " non-empty regex list",
                            "each closed type declares one or more exact"
                            " vault-relative path grammars")
                        continue
                    for pattern in patterns:
                        try:
                            re.compile(pattern)
                        except re.error as exc:
                            err(f"type_path_patterns['{doc_type}'] has invalid"
                                f" regex '{pattern}': {exc}",
                                "ship compilable deterministic path regexes")
            status_values = policy.get("status_values")
            if not isinstance(status_values, dict):
                err("status_values must be an object",
                    "status vocabularies are closed by document type")
            else:
                for doc_type, values in sorted(status_values.items()):
                    if (not isinstance(values, list) or not values
                            or not all(isinstance(value, str)
                                       and KEBAB_RE.match(value.replace("_", "-"))
                                       for value in values)):
                        err(f"status_values['{doc_type}'] must be a non-empty"
                            " machine-safe list",
                            "declare every legal lifecycle value explicitly")
            relations = policy.get("relation_contract")
            if not isinstance(relations, dict):
                err("relation_contract must be an object",
                    "cross-subtree traceability is policy data")
            else:
                keys = relations.get("keys")
                if not isinstance(keys, dict) or not keys:
                    err("relation_contract.keys must be a non-empty object",
                        "declare the typed outgoing relation vocabulary")
                else:
                    for name, spec in sorted(keys.items()):
                        if (not isinstance(name, str)
                                or not SNAKE_KEY_RE.match(name)
                                or not isinstance(spec, dict)
                                or set(spec) != {"targets", "inverse_label"}
                                or not isinstance(spec.get("targets"), list)
                                or not spec.get("targets")
                                or not all(value == "*" or (
                                    isinstance(value, str)
                                    and KEBAB_RE.match(value))
                                    for value in spec.get("targets", []))
                                or not isinstance(spec.get("inverse_label"), str)
                                or not spec.get("inverse_label")):
                            err(f"relation_contract key '{name}' has an"
                                " invalid target or inverse-label contract",
                                "each relation owns targets and one stable"
                                " machine-layer inverse label")
                for name in ("inverse_inline_max", "catalog_page_size"):
                    value = relations.get(name)
                    if (not isinstance(value, int) or isinstance(value, bool)
                            or value < 1):
                        err(f"relation_contract.{name} must be a positive"
                            " integer",
                            "bound inverse relation surfaces mechanically")
                catalog_root = relations.get("catalog_root")
                if not isinstance(catalog_root, str) or not catalog_root:
                    err("relation_contract.catalog_root must be non-empty",
                        "name the compiler-owned relation catalog root")
                cardinalities = relations.get("cardinality_by_type")
                if not isinstance(cardinalities, dict):
                    err("relation_contract.cardinality_by_type must be an object",
                        "declare source-type relation cardinalities")
                else:
                    for doc_type, specs in sorted(cardinalities.items()):
                        if not isinstance(specs, dict):
                            err(f"relation cardinality for '{doc_type}' must"
                                " be an object", "map relation keys to min/max")
                            continue
                        for relation, limits in sorted(specs.items()):
                            if (relation not in (keys or {})
                                    or not isinstance(limits, dict)
                                    or set(limits) != {"min", "max"}
                                    or not all(isinstance(limits.get(name), int)
                                               and not isinstance(limits.get(name), bool)
                                               for name in ("min", "max"))
                                    or not 0 <= limits["min"] <= limits["max"]):
                                err(f"invalid relation cardinality"
                                    f" '{doc_type}.{relation}'",
                                    "use integer 0 <= min <= max for a known"
                                    " relation key")
            # Graph color completeness: every doc type known to the
            # taxonomy (sibling space-schema doc_types, kebab-ized, UNION
            # extra_doc_types) owns a tag:#doc/<type> color group, and
            # every group tag names a known type. An uncolored type or a
            # dead legend entry is a mechanical error, never an oversight.
            schema_types: set[str] = set()
            for skill in skill_dirs(plugin):
                spath = skill / "data" / "space-schema.json"
                if not spath.is_file():
                    continue
                try:
                    sdata = json.loads(read_text(spath))
                except json.JSONDecodeError:
                    continue  # ba_schema_shape reports the parse failure
                if isinstance(sdata.get("doc_types"), dict):
                    schema_types.update(k.replace("_", "-")
                                        for k in sdata["doc_types"])
            universe = schema_types | set(extra_types)
            if color_groups is not None:
                tagged = {m for query in color_groups
                          for m in re.findall(r"tag:#doc/([a-z0-9-]+)",
                                              query)}
                for name in sorted(universe - tagged):
                    err(f"doc type '{name}' has no graph color group",
                        "every known doc type carries a tag:#doc/<type>"
                        " entry in graph_color_groups; add the group (and"
                        " its color) in the same commit as the type")
                for name in sorted(tagged - universe):
                    err(f"graph color group tag '#doc/{name}' names no"
                        " known doc type",
                        "a legend entry without a type is dead; drop the"
                        " group or declare the type in extra_doc_types")
            # Parity with the shipped seeds: the policy and templates/vault
            # describe one product; they may not drift.
            vault_tpl = plugin / "templates" / "vault"
            if not vault_tpl.is_dir():
                continue
            types_seed = vault_tpl / ".obsidian" / "types.json"
            if not types_seed.is_file():
                err("templates/vault/.obsidian/types.json is missing",
                    "ship the Obsidian property seed beside the vault policy")
            else:
                try:
                    types_document = json.loads(read_text(types_seed))
                except json.JSONDecodeError:
                    types_document = None  # json_hygiene reports syntax
                seed_types = types_document.get("types") \
                    if isinstance(types_document, dict) else None
                if seed_types != prop_types:
                    err("types.json property map does not match policy"
                        " property_types",
                        "derive the complete Obsidian property seed from the"
                        " vault-wide property type map in the same change")
            maps_dir = policy.get("maps_dir") or "maps"
            home_file = policy.get("home_file") or "home.md"
            expected_maps = set(subtrees) | set(extra_maps)
            seeds_dir = vault_tpl / maps_dir
            seeds = ({p.stem for p in seeds_dir.glob("*.md")}
                     if seeds_dir.is_dir() else set())
            for name in sorted(expected_maps - seeds):
                err(f"policy names '{name}' but templates/vault/{maps_dir}/"
                    f"{name}.md is missing",
                    "ship one map seed per policy subtree and extra map")
            for name in sorted(seeds - expected_maps):
                err(f"templates/vault/{maps_dir}/{name}.md is not named by"
                    " the policy",
                    "add it to subtrees or extra_maps, or drop the seed")
            home = vault_tpl / home_file
            if not home.is_file():
                err(f"templates/vault/{home_file} is missing",
                    "the home seed is the vault's navigation root")
            else:
                home_text = read_text(home)
                for name in sorted(extra_maps):
                    if f"[[{maps_dir}/{name}" not in home_text:
                        err(f"home seed does not link [[{maps_dir}/{name}]]",
                            "home always links the extra maps")
                for name in sorted(subtrees):
                    if f"[[{maps_dir}/{name}" in home_text:
                        err(f"home seed links [[{maps_dir}/{name}]]",
                            "home is dynamic: a subtree's map line is added"
                            " by the entry that births the tree, never"
                            " shipped in the seed")
            graph = vault_tpl / ".obsidian" / "graph.json"
            if graph.is_file():
                try:
                    graph_data = json.loads(read_text(graph))
                except json.JSONDecodeError:
                    graph_data = {}  # json_hygiene reports the parse failure
                queries = [str(group.get("query", ""))
                           for group in graph_data.get("colorGroups", [])
                           if isinstance(group, dict)]
                seed_palette = [
                    (str(group.get("query", "")),
                     (group.get("color") or {}).get("rgb"))
                    for group in graph_data.get("colorGroups", [])
                    if isinstance(group, dict)
                ]
                if palette is not None and seed_palette != palette:
                    err("graph.json colorGroups do not match policy"
                        " graph_color_groups (same query, RGB and order)",
                        "the committed graph legend derives from the policy;"
                        " regenerate graph.json from the named palette")
                if graph_search is not None \
                        and graph_data.get("search", "") != graph_search:
                    err("graph.json search does not match policy"
                        " graph_search",
                        "the global graph filter is policy data; restore it")
            cp_file = vault_tpl / ".obsidian" / "community-plugins.json"
            if community:
                cp_data = None
                if not cp_file.is_file():
                    err("templates/vault/.obsidian/community-plugins.json is"
                        " missing",
                        "the vetted plugin enable list ships with the"
                        " payload")
                else:
                    try:
                        cp_data = json.loads(read_text(cp_file))
                    except json.JSONDecodeError:
                        cp_data = None  # json_hygiene reports the failure
                if cp_data is not None and sorted(cp_data) != sorted(community):
                    err("community-plugins.json does not match policy"
                        " community_plugins",
                        "the enable list and the policy set are one fact")
            for plugin_id in community:
                plugin_dir = vault_tpl / ".obsidian" / "plugins" / plugin_id
                if not plugin_dir.is_dir():
                    continue  # vendored payload lands separately; parity
                    # binds the moment the directory exists
                for fname in ("manifest.json", "main.js", "data.json"):
                    if not (plugin_dir / fname).is_file():
                        err(f"vendored plugin '{plugin_id}' is missing"
                            f" {fname}",
                            "a vendored plugin ships manifest, build and"
                            " settings together")
                manifest_path = plugin_dir / "manifest.json"
                if manifest_path.is_file():
                    try:
                        manifest = json.loads(read_text(manifest_path))
                    except json.JSONDecodeError:
                        manifest = {}  # json_hygiene reports the failure
                    if isinstance(manifest, dict) \
                            and manifest.get("id") != plugin_id:
                        err(f"vendored plugin manifest id"
                            f" '{manifest.get('id')}' does not match its"
                            f" directory '{plugin_id}'",
                            "the enable list, the directory and the manifest"
                            " name one plugin")


def _config_shape_errors(config: dict) -> list[str]:
    problems: list[str] = []
    if not isinstance(config.get("schema_version"), int):
        problems.append("schema_version must be an integer")
    levels = config.get("reasoning_levels")
    if (not isinstance(levels, list) or not levels
            or not all(isinstance(level, str) and KEBAB_RE.match(level)
                       for level in levels)):
        problems.append("reasoning_levels must be a non-empty kebab-case list")
    return problems


def check_model_config_shape(tree: Tree, findings: list[Finding]) -> None:
    """tools/data/models.json is the reasoning policy file the canonical
    agent frontmatter reads from; an unloadable or malformed config would
    let the enum silently fall back, so its shape is validated like any
    other policy artifact."""
    if tree.config is None:
        try:
            json.loads((tree.root / MODEL_CONFIG_RELPATH).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            problem = "model config is missing or not valid JSON"
        else:
            problem = ("model config must be a JSON object holding schema_version"
                       " and reasoning_levels")
        findings.append(Finding(
            "error", MODEL_CONFIG_RELPATH, 1, "model_config_shape", problem,
            "restore tools/data/models.json; the agent reasoning enum"
            " lives there",
        ))
        return
    for problem in _config_shape_errors(tree.config):
        findings.append(Finding(
            "error", MODEL_CONFIG_RELPATH, 1, "model_config_shape", problem,
            "fix the config block; the enum feeds the frontmatter_shape"
            " reasoning check",
        ))
    levels = tree.config.get("reasoning_levels")
    builder_levels = build_distributions.CANONICAL_REASONING_LEVELS
    if isinstance(levels, list) \
            and all(isinstance(level, str) for level in levels) \
            and set(levels) != builder_levels:
        findings.append(Finding(
            "error", MODEL_CONFIG_RELPATH, 1, "model_config_shape",
            f"reasoning_levels {sorted(set(levels))} differ from the"
            f" distribution builder's tiers {sorted(builder_levels)}",
            "change reasoning_levels and CANONICAL_REASONING_LEVELS in"
            " tools/build_distributions.py together",
        ))


def declared_tiers(tree: Tree) -> set[str]:
    """Return the reasoning tiers tools/data/models.json declares."""
    levels = (tree.config or {}).get("reasoning_levels")
    return set(levels) if isinstance(levels, list) and levels and all(
        isinstance(level, str) for level in levels
    ) else set(AGENT_REASONING_ENUM)  # model_config_shape reports bad shapes


def check_execution_profiles(tree: Tree, findings: list[Finding]) -> None:
    """Every host maps exactly the canonical reasoning tiers to its own
    documented model and effort values; model names stay under platforms/."""
    try:
        adapters = build_distributions.load_adapters(tree.root)
    except ValueError:
        return  # registration and product_namespace report adapter failures
    tiers = declared_tiers(tree)
    for host, adapter in adapters.items():
        path = build_distributions.execution_profile_path(tree.root, host)
        try:
            table = json.loads(read_text(path))
        except (OSError, json.JSONDecodeError):
            findings.append(Finding(
                "error", rel(tree, path), 1, "execution_profiles",
                "execution profile table is missing or not valid JSON",
                "restore the host table that maps each reasoning tier to"
                " model and effort",
            ))
            continue
        for problem in build_distributions.execution_profile_problems(
                table, adapter, tiers):
            findings.append(Finding(
                "error", rel(tree, path), 1, "execution_profiles", problem,
                "map every reasoning tier in tools/data/models.json to the"
                " host's documented model and effort values",
            ))


REVIEW_PANELS_RELPATH = "skill-content/challenge-review/data/review-panels.json"
VAULT_POLICY_RELPATH = "skill-content/obsidian-vault/data/vault-policy.json"
REVIEW_PANEL_ANCHOR_RE = re.compile(r"\breview\s+panel\s+`([a-z][a-z0-9_]*)`")
LENS_REFERENCE_RE = re.compile(r"\blens\s+`([a-z][a-z0-9-]*)`")
REVIEW_PANELS_SWITCH = "review_panels"
REVIEW_PANELS_VALUE = "lens_panel"
REVIEW_STEP_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
REVIEW_PANEL_DATA_KEYS = {"schema_version", "review_steps"}
REVIEW_STEP_KEYS = {"reader_role", "lenses", "default_panel"}
LENS_KEYS = {"id", "focus"}


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    keys = [key for key, _value in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"duplicate keys {duplicates}")
    return dict(pairs)


def _policy_list(policy: object, dotted: object) -> list[str] | None:
    value = policy
    for part in dotted.split(".") if isinstance(dotted, str) and dotted else [None]:
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return value
    return None


def review_panel_problems(data: object, agents: set[str], policy: object) -> list[str]:
    """Return the shape problems of one plugin's review-panel lens data."""
    if not isinstance(data, dict) or set(data) != REVIEW_PANEL_DATA_KEYS \
            or data.get("schema_version") != 1:
        return ["data must hold exactly schema_version 1 and review_steps"]
    problems: list[str] = []
    steps = data["review_steps"]
    if not isinstance(steps, dict) or not steps:
        return problems + ["review_steps must declare at least one review step"]
    for step, spec in sorted(steps.items()):
        where = f"review step {step!r}"
        if not REVIEW_STEP_ID_RE.match(step):
            problems.append(f"{where}: id must be lowercase snake_case")
        if not isinstance(spec, dict) or not REVIEW_STEP_KEYS <= set(spec) \
                or not set(spec) <= REVIEW_STEP_KEYS | {"review_note"}:
            problems.append(
                f"{where}: must hold reader_role, lenses, default_panel and"
                " optionally review_note")
            continue
        if spec["reader_role"] not in agents:
            problems.append(f"{where}: unknown reader_role {spec['reader_role']!r}")
        lenses = spec["lenses"]
        if not isinstance(lenses, list) or not lenses:
            problems.append(f"{where}: empty panel, lenses declares no lens")
            continue
        ids: list[str] = []
        for lens in lenses:
            if not isinstance(lens, dict) or not LENS_KEYS <= set(lens) \
                    or not set(lens) <= LENS_KEYS | {"covers"} \
                    or not isinstance(lens.get("id"), str) \
                    or not isinstance(lens.get("focus"), str) \
                    or not lens["focus"].strip():
                problems.append(
                    f"{where}: every lens holds an id, non-empty focus text and"
                    " optionally covers")
                continue
            if not KEBAB_RE.match(lens["id"]):
                problems.append(f"{where}: lens id {lens['id']!r} must be kebab-case")
            ids.append(lens["id"])
        for duplicate in sorted({lens for lens in ids if ids.count(lens) > 1}):
            problems.append(f"{where}: duplicate lens id {duplicate!r}")
        panel = spec["default_panel"]
        if not isinstance(panel, list) or not panel:
            problems.append(f"{where}: empty panel, default_panel has no assignment")
        else:
            assigned: list[str] = []
            for assignment in panel:
                if not isinstance(assignment, list) or not assignment \
                        or not all(isinstance(lens, str) for lens in assignment):
                    problems.append(
                        f"{where}: empty panel assignment; each assignment lists"
                        " one or more lens ids")
                    continue
                assigned.extend(assignment)
            for lens in sorted(set(assigned) - set(ids)):
                problems.append(f"{where}: default_panel names unknown lens id {lens!r}")
            for lens in sorted({lens for lens in assigned if assigned.count(lens) > 1}):
                problems.append(
                    f"{where}: duplicate lens id {lens!r} across default_panel assignments")
            for lens in sorted(set(ids) - set(assigned)):
                problems.append(f"{where}: lens {lens!r} is in no default_panel assignment")
        covers = {lens["id"]: lens["covers"] for lens in lenses
                  if isinstance(lens, dict) and "covers" in lens and isinstance(lens.get("id"), str)}
        note = spec.get("review_note")
        if note is None:
            if covers:
                problems.append(f"{where}: covers needs a review_note declaration")
            continue
        sections = _policy_list(policy, note.get("sections")) \
            if isinstance(note, dict) and set(note) == {"sections", "panel_sections"} else None
        panel_sections = note.get("panel_sections") if isinstance(note, dict) else None
        if sections is None or not isinstance(panel_sections, list) \
                or not all(isinstance(item, str) for item in panel_sections):
            problems.append(
                f"{where}: review_note needs sections, a dotted vault-policy path to a"
                " section list, and a panel_sections list")
            continue
        covered: list[str] = []
        for lens in ids:
            value = covers.get(lens)
            if not isinstance(value, list) or not value \
                    or not all(isinstance(item, str) for item in value):
                problems.append(f"{where}: lens {lens!r} must list the note sections it covers")
                continue
            covered.extend(value)
        for section in sorted(set(covered + panel_sections) - set(sections)):
            problems.append(f"{where}: unknown review-note section {section!r}")
        for section in sorted({item for item in covered if covered.count(item) > 1}
                              | (set(covered) & set(panel_sections))):
            problems.append(f"{where}: review-note section {section!r} has more than one owner")
        for section in sorted(set(sections) - set(covered) - set(panel_sections)):
            problems.append(f"{where}: no lens covers review-note section {section!r}")
    return problems


def lens_variant_problems(plugin: Path, steps: dict) -> list[str]:
    """Every read-only panel reader runs as its lens_panel variant, and every
    variant reads a declared review step."""
    try:
        registry = json.loads(read_text(plugin / PROCESS_SWITCHES_RELPATH))
        variant = registry["switches"][REVIEW_PANELS_SWITCH]["agent_variants"][REVIEW_PANELS_VALUE]
        variants = set(variant["agents"])
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return [f"switch {REVIEW_PANELS_SWITCH!r} must declare the {REVIEW_PANELS_VALUE!r}"
                " agent variants of the read-only panel readers"]
    readers = {spec.get("reader_role") for spec in steps.values() if isinstance(spec, dict)}
    read_only = set()
    for reader in readers:
        path = plugin / "agents" / f"{reader}.md"
        tools = parse_frontmatter(read_text(path))[0].get("tools", "") if path.is_file() else ""
        if {tool.strip() for tool in str(tools).split(",") if tool.strip()} == AGENT_READONLY_TOOLS:
            read_only.add(reader)
    return ([f"read-only panel reader {reader!r} has no {REVIEW_PANELS_VALUE!r} agent variant"
             for reader in sorted(read_only - variants)]
            + [f"{REVIEW_PANELS_VALUE!r} agent variant {agent!r} reads no read-only review step"
               for agent in sorted(variants - read_only)])


def check_review_panels(tree: Tree, findings: list[Finding]) -> None:
    """Review-panel lens sets are validated data. Every flow anchor names a
    declared step, every declared step is wired into a flow that also names
    switch `review_panels`, every read-only reader runs as its lens variant
    and every lens a prose reference names is declared."""
    for plugin in plugin_dirs(tree):
        path = plugin / REVIEW_PANELS_RELPATH
        anchors: dict[str, list[Path]] = {}
        lens_references: dict[str, list[Path]] = {}
        for source in sorted(plugin.rglob("*.md")):
            text = read_text(source)
            for step in REVIEW_PANEL_ANCHOR_RE.findall(text):
                anchors.setdefault(step, []).append(source)
            for lens in LENS_REFERENCE_RE.findall(text):
                lens_references.setdefault(lens, []).append(source)

        def err(where: Path, message: str, fix: str) -> None:
            findings.append(Finding("error", rel(tree, where), 1, "review_panels", message, fix))

        if not path.is_file():
            if anchors or lens_references:
                err(path, "review panels are referenced but their lens data is missing",
                    "restore the review-panel data file the flows anchor to")
            continue
        try:
            data = json.loads(read_text(path), object_pairs_hook=_unique_json_object)
        except (json.JSONDecodeError, ValueError) as exc:
            err(path, f"review-panel data is not valid unique-key JSON: {exc}",
                "fix the data file; every key and lens id is declared once")
            continue
        try:
            policy = json.loads(read_text(plugin / VAULT_POLICY_RELPATH))
        except (OSError, json.JSONDecodeError):
            policy = None  # vault_policy_shape reports a broken policy
        agents = {agent.stem for agent in agent_files(plugin)}
        for problem in review_panel_problems(data, agents, policy):
            err(path, problem, "declare each review step, lens and assignment once; a new"
                " lens or step is a data change plus its flow anchor")
        steps = data.get("review_steps") if isinstance(data, dict) else None
        steps = steps if isinstance(steps, dict) else {}
        for problem in lens_variant_problems(plugin, steps):
            err(plugin / PROCESS_SWITCHES_RELPATH, problem,
                "list every read-only panel reader, and only those, as a lens_panel"
                " agent variant")
        declared_lenses = {
            lens["id"] for spec in steps.values() if isinstance(spec, dict)
            for lens in spec.get("lenses", []) if isinstance(spec.get("lenses"), list)
            and isinstance(lens, dict) and isinstance(lens.get("id"), str)
        }
        flows = plugin / "flows"
        for step, sources in sorted(anchors.items()):
            if step not in steps:
                err(sources[0], f"review panel {step!r} is missing from the lens data",
                    "declare the step in the review-panel data or fix the anchor")
        for step in sorted(steps):
            if not any(source.parent == flows for source in anchors.get(step, [])):
                err(path, f"review step {step!r} is unknown to every flow",
                    "anchor the step in its flow as review panel `<step>`")
        for flow in sorted({source for sources in anchors.values() for source in sources
                            if source.parent == flows}):
            if REVIEW_PANELS_SWITCH not in SWITCH_ANCHOR_RE.findall(read_text(flow)):
                err(flow, f"flow {flow.name} runs a review panel but never names switch"
                    f" `{REVIEW_PANELS_SWITCH}`",
                    "anchor the switch that selects the panel at the review step")
        for lens, sources in sorted(lens_references.items()):
            if lens not in declared_lenses:
                err(sources[0], f"prose names unknown lens {lens!r}",
                    "name a lens id the review-panel data declares")


PROCESS_SWITCHES_RELPATH = build_distributions.PROCESS_SWITCHES_RELPATH
SWITCH_ANCHOR_RE = re.compile(r"\b[Ss]witch\s+`([a-z][a-z0-9_]*)`")
SWITCH_REFERENCE_RE = re.compile(r"^switch-([a-z][a-z0-9_]*)-([a-z][a-z0-9_]*)\.md$")
PROCESS_SWITCH_KEYS = {"summary", "flows", "values", "default", "metric", "promotion"}
PROCESS_SWITCH_OPTIONAL_KEYS = {"issue", "agent_variants", "parameters", "value_data"}
SWITCH_PARAMETER_KEYS = {"summary", "values", "declared_by", "type", "min_count"}
SWITCH_PARAMETER_TYPES = {"positive_integer"}
VALUE_DATA_RE = re.compile(r"^skill-content/[a-z0-9]+(?:-[a-z0-9]+)*/data/[a-z0-9]+(?:-[a-z0-9]+)*\.json$")
AGENT_VARIANT_KEYS = {"suffix", "tier", "description", "agents"}
MECHANICAL_PASS_SWITCH = "mechanical_pass_tier"
MECHANICAL_PASS_VALUE = "mechanical"
TASK_INPUT_POLICY_RELPATH = "templates/task-input-policy.json"


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def agent_variant_problems(where: str, variants: object, values: list[str], default: object,
                           agents: set[str], tiers: set[str]) -> list[str]:
    """Return the problems of one switch's generated agent variants."""
    if not isinstance(variants, dict) or not variants:
        return [f"{where}: agent_variants must map a switch value to its variant"]
    problems: list[str] = []
    for value, spec in sorted(variants.items()):
        at = f"{where} value {value!r}"
        if value not in values or value == default:
            problems.append(f"{at}: agent variants belong to a declared value other than the default")
        if not isinstance(spec, dict) or set(spec) != AGENT_VARIANT_KEYS:
            problems.append(f"{at}: an agent variant holds exactly suffix, tier, description and agents")
            continue
        if not isinstance(spec["suffix"], str) or not KEBAB_RE.match(spec["suffix"]):
            problems.append(f"{at}: variant suffix must be kebab-case")
        if spec["tier"] not in tiers:
            problems.append(f"{at}: variant tier {spec['tier']!r} is not a declared reasoning tier")
        # The description is rendered into agent frontmatter as one plain scalar.
        if not _nonblank(spec["description"]) or ":" in spec["description"] \
                or "\n" in spec["description"]:
            problems.append(f"{at}: variant description must be one non-empty line without a colon")
        listed = spec["agents"]
        if not isinstance(listed, list) or not listed \
                or not all(isinstance(agent, str) for agent in listed):
            problems.append(f"{at}: variant agents must list at least one canonical agent")
            continue
        for agent in sorted({agent for agent in listed if listed.count(agent) > 1}):
            problems.append(f"{at}: duplicate variant agent {agent!r}")
        for agent in sorted(set(listed) - agents):
            problems.append(f"{at}: unknown variant agent {agent!r}")
    return problems


def value_data_problems(plugin: Path, switches: dict) -> list[str]:
    """Package data that only one switch value reads is bound with that value's
    switch references, so each file exists once, belongs to a value other than
    the default and travels with at least one reference of that value."""
    problems: list[str] = []
    owners: dict[str, str] = {}
    references = {SWITCH_REFERENCE_RE.match(path.name).groups()
                  for skill in skill_dirs(plugin)
                  for path in (skill / "references").glob("switch-*.md")
                  if SWITCH_REFERENCE_RE.match(path.name)}
    for switch, spec in sorted(switches.items()):
        if not isinstance(spec, dict) or "value_data" not in spec:
            continue
        where = f"switch {switch!r}"
        data = spec["value_data"]
        values = [item.get("id") for item in spec.get("values", []) if isinstance(item, dict)] \
            if isinstance(spec.get("values"), list) else []
        if not isinstance(data, dict) or not data:
            problems.append(f"{where}: value_data must map a switch value to its data files")
            continue
        for value, paths in sorted(data.items()):
            at = f"{where} value {value!r}"
            if value not in values or value == spec.get("default"):
                problems.append(f"{at}: value data belongs to a declared value other than the default")
            if not isinstance(paths, list) or not paths \
                    or not all(isinstance(path, str) for path in paths):
                problems.append(f"{at}: value data must list at least one data file")
                continue
            for path in paths:
                if not VALUE_DATA_RE.match(path):
                    problems.append(f"{at}: value data {path!r} is not a skill data JSON file")
                elif not (plugin / path).is_file():
                    problems.append(f"{at}: value data {path!r} does not exist")
                if path in owners:
                    problems.append(f"{at}: value data {path!r} is already declared by {owners[path]}")
                owners.setdefault(path, at)
            if (switch, value) not in references:
                problems.append(f"{at}: value data needs a switch reference of that value to bind it")
    return problems


def mechanical_variant_problems(plugin: Path, switches: dict) -> list[str]:
    """A mechanical pass applies fixes as its owning writer, so the switch
    declares writer variants and never a read-only reviewer's or challenger's:
    every review, re-check and calibration keeps its role's tier."""
    spec = switches.get(MECHANICAL_PASS_SWITCH)
    if not isinstance(spec, dict):
        return []
    variants = spec.get("agent_variants")
    variant = variants.get(MECHANICAL_PASS_VALUE) if isinstance(variants, dict) else None
    listed = variant.get("agents") if isinstance(variant, dict) else None
    if not isinstance(listed, list) or not listed:
        return [f"switch {MECHANICAL_PASS_SWITCH!r} must declare the {MECHANICAL_PASS_VALUE!r}"
                " agent variants of the writers whose passes it moves"]
    try:
        policy = json.loads(read_text(plugin / TASK_INPUT_POLICY_RELPATH))
        read_only = {role for role in policy["read_only_roles"] if isinstance(role, str)}
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        read_only = set()  # task_input_catalog reports a broken task input policy
    problems = []
    for agent in sorted({agent for agent in listed if isinstance(agent, str)}):
        path = plugin / "agents" / f"{agent}.md"
        tools = parse_frontmatter(read_text(path))[0].get("tools", "") if path.is_file() else ""
        if agent in read_only or {tool.strip() for tool in tools.split(",") if tool.strip()}:
            problems.append(
                f"{MECHANICAL_PASS_VALUE!r} agent variant {agent!r} is a read-only reviewer or"
                " challenger; its reviews, re-checks and calibrations keep their tier")
    return problems


def parameter_problems(where: str, parameters: object, values: list[str], default: object,
                       plugin: Path | None) -> list[str]:
    """Return the problems of one switch's owner-set parameter declaration."""
    if not isinstance(parameters, dict) or set(parameters) != SWITCH_PARAMETER_KEYS:
        return [f"{where}: parameters hold exactly summary, values, declared_by, type and min_count"]
    problems: list[str] = []
    if not _nonblank(parameters["summary"]):
        problems.append(f"{where}: parameters need a summary the choice gate shows")
    taking = parameters["values"]
    if not isinstance(taking, list) or not taking or len(taking) != len(set(map(str, taking))):
        problems.append(f"{where}: parameters must list the values that take them, each once")
    else:
        for value in taking:
            if value not in values or value == default:
                problems.append(f"{where}: parameters belong to a declared value other than the"
                                f" default, not {value!r}")
    if parameters["type"] not in SWITCH_PARAMETER_TYPES:
        problems.append(f"{where}: parameter type {parameters['type']!r} is not one of"
                        f" {sorted(SWITCH_PARAMETER_TYPES)}")
    source = parameters["declared_by"]
    declared = None
    if not isinstance(source, dict) or set(source) != {"path", "key"} \
            or not all(_nonblank(source.get(key)) for key in ("path", "key")):
        problems.append(f"{where}: declared_by must name the package data file and the key that"
                        " declare the parameter ids")
    elif plugin is not None:
        relative = source["path"]
        target = plugin / relative
        if relative.startswith("/") or "\\" in relative or ".." in relative.split("/") \
                or not target.is_file():
            problems.append(f"{where}: declared_by path {relative!r} is not a file of the package")
        else:
            try:
                declared = json.loads(read_text(target)).get(source["key"])
            except (json.JSONDecodeError, AttributeError):
                declared = None
            if not isinstance(declared, dict) or not declared or not all(
                    REVIEW_STEP_ID_RE.match(key) and isinstance(item, dict)
                    and _nonblank(item.get("summary")) for key, item in declared.items()):
                problems.append(f"{where}: declared_by {relative} key {source['key']!r} must map"
                                " snake_case parameter ids to a summary")
                declared = None
    minimum = parameters["min_count"]
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 0 \
            or (declared is not None and minimum > len(declared)):
        problems.append(f"{where}: min_count must be a whole number no larger than the declared"
                        " parameters")
    return problems


def process_switch_problems(data: object, flows: set[str], agents: set[str],
                            tiers: set[str], plugin: Path | None = None) -> list[str]:
    """Return the shape problems of the package's process switch registry."""
    if not isinstance(data, dict) or set(data) != {"schema_version", "switches"} \
            or data.get("schema_version") != 1:
        return ["registry must hold exactly schema_version 1 and switches"]
    switches = data["switches"]
    if not isinstance(switches, dict):
        return ["switches must be an object keyed by switch id"]
    problems: list[str] = []
    generated: dict[str, str] = {}
    for switch, spec in sorted(switches.items()):
        where = f"switch {switch!r}"
        if not REVIEW_STEP_ID_RE.match(switch):
            problems.append(f"{where}: id must be lowercase snake_case")
        if not isinstance(spec, dict):
            problems.append(f"{where}: must be an object")
            continue
        unknown = sorted(set(spec) - PROCESS_SWITCH_KEYS - PROCESS_SWITCH_OPTIONAL_KEYS)
        if unknown:
            problems.append(f"{where}: unknown keys {unknown}")
        if not _nonblank(spec.get("summary")):
            problems.append(f"{where}: needs a summary of what it decides")
        if not _nonblank(spec.get("metric")):
            problems.append(f"{where}: needs a component metric")
        promotion = spec.get("promotion")
        if not isinstance(promotion, dict) or set(promotion) != {"unit", "threshold"} \
                or not all(_nonblank(promotion.get(key)) for key in ("unit", "threshold")):
            problems.append(f"{where}: needs a promotion rule with a unit and a threshold")
        owners = spec.get("flows")
        if not isinstance(owners, list) or not owners \
                or not all(isinstance(flow, str) for flow in owners):
            problems.append(f"{where}: flows must list at least one owning flow")
        else:
            for flow in sorted({flow for flow in owners if owners.count(flow) > 1}):
                problems.append(f"{where}: duplicate owning flow {flow!r}")
            for flow in sorted(set(owners) - flows):
                problems.append(f"{where}: names unknown flow {flow!r}")
        declared = spec.get("values")
        ids: list[str] = []
        if not isinstance(declared, list) or len(declared) < 2:
            problems.append(f"{where}: values must list at least two values")
        else:
            for value in declared:
                if not isinstance(value, dict) or set(value) != {"id", "tradeoffs"} \
                        or not isinstance(value.get("id"), str) \
                        or not _nonblank(value.get("tradeoffs")):
                    problems.append(f"{where}: every value holds an id and non-empty tradeoffs")
                    continue
                if not REVIEW_STEP_ID_RE.match(value["id"]):
                    problems.append(f"{where}: value id {value['id']!r} must be lowercase snake_case")
                ids.append(value["id"])
            for value in sorted({value for value in ids if ids.count(value) > 1}):
                problems.append(f"{where}: duplicate value {value!r}")
        default = spec.get("default")
        if default not in ids:
            problems.append(f"{where}: default {default!r} is not one of its values {ids}")
        issue = spec.get("issue")
        if "issue" in spec and (not isinstance(issue, int) or isinstance(issue, bool) or issue < 1):
            problems.append(f"{where}: issue must be a positive issue number")
        if "parameters" in spec:
            problems.extend(parameter_problems(where, spec["parameters"], ids, default, plugin))
        if "agent_variants" in spec:
            variants = spec["agent_variants"]
            problems.extend(agent_variant_problems(where, variants, ids, default, agents, tiers))
            for variant in variants.values() if isinstance(variants, dict) else []:
                if not isinstance(variant, dict) or not isinstance(variant.get("agents"), list):
                    continue
                for agent in variant["agents"]:
                    name = f"{agent}-{variant.get('suffix')}"
                    if name in agents or name in generated:
                        problems.append(
                            f"{where}: variant {name!r} collides with"
                            f" {'a canonical agent' if name in agents else generated[name]}")
                    generated.setdefault(name, where)
    return problems


def check_process_switches(tree: Tree, findings: list[Finding]) -> None:
    """Process switches are declared once in the package registry, and every
    switch is anchored in each flow that owns it as switch `<id>`."""
    tiers = declared_tiers(tree)
    for plugin in plugin_dirs(tree):
        path = plugin / PROCESS_SWITCHES_RELPATH
        flows_dir = plugin / "flows"
        flow_paths = sorted(flows_dir.glob("*.md")) if flows_dir.is_dir() else []
        anchors: dict[str, dict[str, Path]] = {}
        for flow in flow_paths:
            for name in SWITCH_ANCHOR_RE.findall(read_text(flow)):
                anchors.setdefault(name, {})[flow.stem] = flow

        def err(where: Path, message: str, fix: str) -> None:
            findings.append(Finding("error", rel(tree, where), 1, "process_switches", message, fix))

        if not path.is_file():
            if anchors or any((skill / "references").glob("switch-*.md")
                              for skill in skill_dirs(plugin)):
                err(path, "process switches are named but the switch registry is missing",
                    "restore the registry the flows and switch references name")
            continue
        try:
            data = json.loads(read_text(path), object_pairs_hook=_unique_json_object)
        except (json.JSONDecodeError, ValueError) as exc:
            err(path, f"switch registry is not valid unique-key JSON: {exc}",
                "fix the registry; every key and switch is declared once")
            continue
        agents = {agent.stem for agent in agent_files(plugin)}
        for problem in process_switch_problems(data, {flow.stem for flow in flow_paths},
                                               agents, tiers, plugin):
            err(path, problem, "declare each switch once with its owning flows, values,"
                " default, component metric and promotion rule")
        switches = data.get("switches") if isinstance(data, dict) else None
        switches = switches if isinstance(switches, dict) else {}
        for problem in value_data_problems(plugin, switches):
            err(path, problem, "declare each data file once, under the switch value whose"
                " references read it")
        for problem in mechanical_variant_problems(plugin, switches):
            err(path, problem, "list only the writers whose fix passes the switch moves as"
                " its mechanical agent variants")
        for name, owners in sorted(anchors.items()):
            spec = switches.get(name)
            listed = spec.get("flows") if isinstance(spec, dict) else None
            for stem, flow in sorted(owners.items()):
                if not isinstance(spec, dict):
                    err(flow, f"flow {flow.name} names undeclared switch {name!r}",
                        "declare the switch in the registry or fix the anchor")
                elif not isinstance(listed, list) or stem not in listed:
                    err(flow, f"flow {flow.name} names switch {name!r} but is not one of"
                        " its owning flows", "list the flow in the switch's flows or"
                        " remove the anchor")
        for name, spec in sorted(switches.items()):
            listed = spec.get("flows") if isinstance(spec, dict) else None
            for stem in listed if isinstance(listed, list) else []:
                if isinstance(stem, str) and (flows_dir / f"{stem}.md").is_file() \
                        and stem not in anchors.get(name, {}):
                    err(path, f"switch {name!r} is not named by its owning flow {stem!r}",
                        "anchor the switch as switch `<id>` at the flow step it changes")
        check_switch_references(plugin, switches, err)


def check_switch_references(plugin: Path, switches: dict, err) -> None:
    """A switch reference holds the instructions of one non-default switch
    value. The Process Policy binds it, so no SKILL.md links it, and an owning
    flow names its path so the orchestrating entry finds it."""
    flows_dir = plugin / "flows"
    for skill in skill_dirs(plugin):
        skill_md = skill / "SKILL.md"
        if skill_md.is_file() and "](references/switch-" in read_text(skill_md):
            err(skill_md, "SKILL.md links a switch reference",
                "switch references are bound by the Process Policy; never link one from SKILL.md")
        for reference in sorted((skill / "references").glob("switch-*.md")):
            match = SWITCH_REFERENCE_RE.match(reference.name)
            if match is None:
                err(reference, "switch reference must be named switch-<switch>-<value>.md",
                    "name the file for one declared switch and value")
                continue
            name, value = match.groups()
            spec = switches.get(name)
            ids = [item.get("id") for item in spec.get("values", []) if isinstance(item, dict)] \
                if isinstance(spec, dict) and isinstance(spec.get("values"), list) else []
            if not isinstance(spec, dict):
                err(reference, f"switch reference names undeclared switch {name!r}",
                    "declare the switch in the registry or rename the reference")
            elif value not in ids:
                err(reference, f"switch reference names undeclared value {value!r} of switch {name!r}",
                    "name one of the switch's declared values")
            elif value == spec.get("default"):
                err(reference, f"switch reference names the default value {value!r} of switch {name!r}",
                    "the default path keeps its instructions in the ordinary files")
            else:
                relative = reference.relative_to(plugin).as_posix()
                owners = spec.get("flows") if isinstance(spec.get("flows"), list) else []
                if not any(isinstance(flow, str) and (flows_dir / f"{flow}.md").is_file()
                           and relative in read_text(flows_dir / f"{flow}.md") for flow in owners):
                    err(reference, f"switch reference {relative} is named by no owning flow of"
                        f" switch {name!r}", "name the reference in the owning flow step it replaces")


STORY_SIZE_MEASURES_RELPATH = "skill-content/product-planning/data/story-size-measures.json"
BACKLOG_COMPILER_RELPATH = "scripts/backlog_compile.py"


def implemented_derivations(source: str) -> set[str] | None:
    """Return the keys of the backlog compiler's STORY_SIZE_DERIVATIONS, read
    from its source so the validator never imports a plugin script."""
    try:
        module = ast.parse(source)
    except SyntaxError:
        return None
    for node in module.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) and any(
                isinstance(target, ast.Name) and target.id == "STORY_SIZE_DERIVATIONS"
                for target in node.targets):
            return {key.value for key in node.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)}
    return None


def check_story_size_measures(tree: Tree, findings: list[Finding]) -> None:
    """Each story size measure is declared once, with a derivation the backlog
    compiler implements, so a limit the owner sets always has a count."""
    for plugin in plugin_dirs(tree):
        path = plugin / STORY_SIZE_MEASURES_RELPATH
        if not path.is_file():
            continue

        def err(message: str, fix: str) -> None:
            findings.append(Finding("error", rel(tree, path), 1, "story_size_measures", message, fix))

        try:
            data = json.loads(read_text(path), object_pairs_hook=_unique_json_object)
        except (json.JSONDecodeError, ValueError) as exc:
            err(f"story size measures are not valid unique-key JSON: {exc}",
                "declare each measure once in valid JSON")
            continue
        if not isinstance(data, dict) or set(data) != {"schema_version", "measures"} \
                or data.get("schema_version") != 1 or not isinstance(data.get("measures"), dict) \
                or not data["measures"]:
            err("story size measures must hold exactly schema_version 1 and a non-empty measures"
                " object", "restore the measures object keyed by measure id")
            continue
        compiler = plugin / BACKLOG_COMPILER_RELPATH
        derivations = implemented_derivations(read_text(compiler)) if compiler.is_file() else None
        if derivations is None:
            err(f"{BACKLOG_COMPILER_RELPATH} declares no STORY_SIZE_DERIVATIONS registry",
                "keep the compiler's derivation registry beside the measures it counts")
            derivations = set()
        owners: dict[str, str] = {}
        for name, spec in sorted(data["measures"].items()):
            where = f"measure {name!r}"
            if not REVIEW_STEP_ID_RE.match(name):
                err(f"{where}: id must be lowercase snake_case", "rename the measure")
            if not isinstance(spec, dict) or set(spec) != {"summary", "derivation"} \
                    or not _nonblank(spec.get("summary")):
                err(f"{where}: holds exactly a non-empty summary and a derivation",
                    "state what the measure counts and how the compiler derives it")
                continue
            derivation = spec["derivation"]
            if derivation not in derivations:
                err(f"{where}: derivation {derivation!r} is not one the backlog compiler implements",
                    "name a key of STORY_SIZE_DERIVATIONS in scripts/backlog_compile.py, or"
                    " implement the derivation there first")
            elif derivation in owners:
                err(f"{where}: repeats the derivation of measure {owners[derivation]!r}",
                    "give each measure its own derivation")
            else:
                owners[derivation] = name


FACT_OWNERSHIP_RELPATH = "skill-content/execution-plan/data/fact-ownership.json"
FACT_DOCUMENT_KEYS = {"type", "title", "flow"}
FACT_OWNER_KEYS = {"document", "section", "writer"}


def flow_names_writer(flow_text: str, role: str, title: str) -> bool:
    """The flow names the role as the document's writer in the form
    flows/operation.md uses: "`<role>` is the only <title> writer", where one
    role may name several titles before the next role's statement starts."""
    for clause in re.split(r"[.;]\s", " ".join(flow_text.split())):
        for part in re.split(r"(?=`[a-z][a-z0-9-]*` is the only )", clause):
            if part.startswith(f"`{role}` is the only ") and f"{title} writer" in part:
                return True
    return False


def fact_ownership_problems(data: object, plugin: Path, vault_types: set[str]) -> list[str]:
    """Return the problems of the execution-planning fact ownership data."""
    if not isinstance(data, dict) or set(data) != {"schema_version", "documents", "fact_classes"} \
            or data.get("schema_version") != 1:
        return ["data must hold exactly schema_version 1, documents and fact_classes"]
    agents = {agent.stem for agent in agent_files(plugin)}
    problems: list[str] = []
    documents = data["documents"] if isinstance(data["documents"], dict) else {}
    if not documents:
        problems.append("documents must declare at least one owning document")
    for key, spec in sorted(documents.items()):
        where = f"document {key!r}"
        if not isinstance(spec, dict) or set(spec) != FACT_DOCUMENT_KEYS \
                or not all(_nonblank(spec[field]) for field in FACT_DOCUMENT_KEYS):
            problems.append(f"{where}: holds exactly a vault type, a title and the flow that"
                            " names its writer")
            continue
        if spec["type"] not in vault_types:
            problems.append(f"{where}: unknown vault document type {spec['type']!r}")
        if not (plugin / "flows" / f"{spec['flow']}.md").is_file():
            problems.append(f"{where}: unknown flow {spec['flow']!r}")
    classes = data["fact_classes"]
    if not isinstance(classes, dict) or not classes:
        return problems + ["fact_classes must declare at least one fact class"]
    for name, spec in sorted(classes.items()):
        where = f"fact class {name!r}"
        if not REVIEW_STEP_ID_RE.match(name):
            problems.append(f"{where}: id must be lowercase snake_case")
        if not isinstance(spec, dict) or not set(spec) <= {"facts", "owner"} \
                or not _nonblank(spec.get("facts")):
            problems.append(f"{where}: holds the facts it covers and its owner")
            continue
        owner = spec.get("owner")
        if not owner:
            problems.append(f"{where}: has no owner")
            continue
        if not isinstance(owner, dict) or any(isinstance(owner.get(field), list)
                                              for field in FACT_OWNER_KEYS):
            problems.append(f"{where}: has two owners; exactly one document, section and"
                            " writer own a fact")
            continue
        if set(owner) != FACT_OWNER_KEYS or not all(_nonblank(owner[field])
                                                    for field in FACT_OWNER_KEYS):
            problems.append(f"{where}: has no owner; its owner names one document, section"
                            " and writer")
            continue
        writer = owner["writer"].replace("_", "-")
        if not REVIEW_STEP_ID_RE.match(owner["writer"]) or writer not in agents:
            problems.append(f"{where}: names unknown writer role {owner['writer']!r}")
            continue
        document = documents.get(owner["document"])
        if not isinstance(document, dict) or set(document) != FACT_DOCUMENT_KEYS:
            problems.append(f"{where}: names undeclared document {owner['document']!r}")
            continue
        flow = plugin / "flows" / f"{document['flow']}.md"
        if flow.is_file() and not flow_names_writer(read_text(flow), writer, document["title"]):
            problems.append(
                f"{where}: flow {document['flow']!r} does not name `{writer}` as the"
                f" {document['title']} writer")
    return problems


def check_fact_ownership(tree: Tree, findings: list[Finding]) -> None:
    """Each execution-planning fact class has exactly one owning document,
    section and writer, and the owning flow names that writer."""
    for plugin in plugin_dirs(tree):
        path = plugin / FACT_OWNERSHIP_RELPATH
        if not path.is_file():
            continue
        try:
            data = json.loads(read_text(path), object_pairs_hook=_unique_json_object)
            policy = json.loads(read_text(plugin / VAULT_POLICY_RELPATH))
            vault_types = set(policy.get("type_path_patterns", {}))
        except (OSError, json.JSONDecodeError, ValueError, AttributeError) as exc:
            findings.append(Finding(
                "error", rel(tree, path), 1, "fact_ownership",
                f"fact ownership data is not valid unique-key JSON: {exc}",
                "declare every fact class and document once"))
            continue
        for problem in fact_ownership_problems(data, plugin, vault_types):
            findings.append(Finding(
                "error", rel(tree, path), 1, "fact_ownership", problem,
                "give every fact class one owning document, section and writer that its"
                " flow names"))


def _limits_shape_errors(config: dict) -> list[str]:
    problems: list[str] = []
    if not isinstance(config.get("schema_version"), int):
        problems.append("schema_version must be an integer")
    caps = config.get("authoring_caps")
    if not isinstance(caps, dict):
        problems.append("authoring_caps must be an object")
        return problems
    missing = AUTHORING_CAP_KEYS - set(caps)
    if missing:
        problems.append(f"authoring_caps missing keys: {sorted(missing)}")
    extra = set(caps) - AUTHORING_CAP_KEYS
    if extra:
        problems.append(f"authoring_caps has unknown keys: {sorted(extra)}")
    bad = sorted(k for k in AUTHORING_CAP_KEYS & set(caps)
                 if not isinstance(caps[k], int) or isinstance(caps[k], bool)
                 or caps[k] <= 0)
    if bad:
        problems.append(
            f"authoring_caps values must be positive integers: {bad}")
    return problems


def check_limits_config_shape(tree: Tree, findings: list[Finding]) -> None:
    """tools/data/limits.json carries the authoring size caps that
    check_size_caps enforces; an unloadable or malformed file (or a
    typoed key) would let a cap silently fall back to the in-code
    default, so its shape is validated like any other policy artifact
    with a closed key set."""
    if tree.limits is None:
        findings.append(Finding(
            "error", LIMITS_CONFIG_RELPATH, 1, "limits_config_shape",
            "limits config is missing or not valid JSON",
            "restore tools/data/limits.json; the authoring size caps live"
            " there",
        ))
        return
    for problem in _limits_shape_errors(tree.limits):
        findings.append(Finding(
            "error", LIMITS_CONFIG_RELPATH, 1, "limits_config_shape",
            problem,
            "fix the config block; authoring_caps feeds the size_caps"
            " check",
        ))


DELIVERY_CONTRACT_ROOT = Path(
    "plugins/software-engineering-team/skill-content/deliver/data"
)
DELIVERY_CONTRACT_FILES = {
    "delivery-control-record-contract.json",
    "delivery-document-contract.json",
    "delivery-verification-policy.json",
    "delivery-protocol-1.json",
    "delivery-provider-contract.json",
    "delivery-receipt-contract.json",
    "delivery-result-contract.json",
}


def check_delivery_contract_shape(
    tree: Tree, findings: list[Finding]
) -> None:
    """Keep every shipped Delivery contract required and mechanically owned."""
    root = tree.root / DELIVERY_CONTRACT_ROOT
    actual = {
        path.name for path in root.glob("*.json") if path.is_file()
    } if root.is_dir() else set()
    if actual != DELIVERY_CONTRACT_FILES:
        findings.append(Finding(
            "error", DELIVERY_CONTRACT_ROOT.as_posix(), 1,
            "delivery_contract_shape",
            "Delivery contract file set differs from the closed registry: "
            f"missing={sorted(DELIVERY_CONTRACT_FILES - actual)}, "
            f"extra={sorted(actual - DELIVERY_CONTRACT_FILES)}",
            "restore the exact canonical Delivery contract set",
        ))
        return

    contracts: dict[str, dict] = {}
    for name in sorted(DELIVERY_CONTRACT_FILES):
        path = root / name
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            findings.append(Finding(
                "error", rel(tree, path), 1, "delivery_contract_shape",
                f"Delivery contract is not valid JSON: {exc}",
                "restore a valid JSON object",
            ))
            continue
        if not isinstance(value, dict):
            findings.append(Finding(
                "error", rel(tree, path), 1, "delivery_contract_shape",
                "Delivery contract root must be an object",
                "replace the root value with the declared contract object",
            ))
            continue
        contracts[name] = value
    if set(contracts) != DELIVERY_CONTRACT_FILES:
        return

    provider = contracts["delivery-provider-contract.json"]
    receipt = contracts["delivery-receipt-contract.json"]
    protocol = contracts["delivery-protocol-1.json"]
    records = contracts["delivery-control-record-contract.json"]
    result = contracts["delivery-result-contract.json"]
    problems = []
    if provider.get("schema_version") != 1 \
            or provider.get("provider") != "github" \
            or provider.get("adapter") != "delivery_provider.GitHubProvider":
        problems.append("provider identity or schema is invalid")
    if receipt.get("schema_version") != 1 \
            or receipt.get("kind") != "item-writer-v1" \
            or receipt.get("states") != ["pending", "verified"]:
        problems.append("writer receipt identity or states are invalid")
    if receipt.get("provider_receipt", {}).get("kind") != "pr-create-v1" \
            or receipt.get("target_update_receipt", {}).get("kind") \
            != "target-update-v1":
        problems.append("provider or target-update receipt identity is invalid")
    if protocol.get("protocol_version") != "delivery-protocol-1" \
            or protocol.get("merge_policy") \
            != "merge-commit-only; squash and rebase fail closed":
        problems.append("protocol version or merge policy is invalid")
    record_names = set(records.get("records", {}))
    if records.get("unknown_record_policy") != "fail_closed" \
            or record_names != set(records.get("subjects", {})):
        problems.append("control-record and subject registries differ")
    verification = contracts["delivery-verification-policy.json"]
    expected_keys = {"schema_version", "raw_evidence_max_age_seconds", "schedules", "legacy_schedule", "new_schedule",
                     "blocking_severities", "nonblocking_severities", "review_checks", "qa_checks", "role_modes", "final_modes", "mutation_scope"}
    if set(verification) != expected_keys or verification.get("schema_version") != 1:
        problems.append("verification policy has an unsupported schema or field set")
    if (verification.get("schedules") != ["sequential_v1", "parallel_snapshot_v1"]
            or verification.get("legacy_schedule") != "sequential_v1"
            or verification.get("new_schedule") != "parallel_snapshot_v1"):
        problems.append("verification schedule compatibility is invalid")
    if (verification.get("review_checks") != ["correctness", "conformance", "security"]
            or verification.get("qa_checks") != ["full_test_suite", "coverage", "right_reason"]
            or verification.get("role_modes") != {"code_reviewer": ["review_initial", "review_repair"], "qa_engineer": ["qa_diagnostic", "qa_final"]}
            or verification.get("final_modes") != {"code_reviewer": ["review_initial", "review_repair"], "qa_engineer": ["qa_final"]}):
        problems.append("verification final gates or independent role modes are invalid")
    age = verification.get("raw_evidence_max_age_seconds")
    if not isinstance(age, int) or isinstance(age, bool) or not 0 < age <= 86400:
        problems.append("verification evidence age must be positive and at most 24 hours")
    scope = verification.get("mutation_scope", {})
    if (not isinstance(scope, dict) or set(scope) != {"excluded_prefixes", "non_code_suffixes", "test_path_segments", "test_name_patterns", "unknown_file_policy", "additional_include_property"}
            or scope.get("unknown_file_policy") != "include" or scope.get("additional_include_property") != "mutation_include_paths"
            or scope.get("excluded_prefixes") != ["workspace/docs/", "workspace/environment/", ".agentrof/"]
            or any(not isinstance(scope.get(key), list) or any(not isinstance(item, str) or not item for item in scope[key])
                   for key in ("non_code_suffixes", "test_path_segments", "test_name_patterns"))):
        problems.append("verification mutation scope must exclude environment and docs, include unknown code and support approved expansion")
    # An Item records the implementation_schedule switch value it runs under;
    # an Item without one ran before the switch existed, so it reads as today's order.
    item = (contracts["delivery-document-contract.json"].get("document_types") or {}).get("delivery_item") or {}
    try:
        registry = json.loads(read_text(root.parents[2] / PROCESS_SWITCHES_RELPATH))
        switch_values = [value.get("id") for value in registry["switches"]["implementation_schedule"]["values"]]
    except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
        switch_values = None
    if (switch_values is None or item.get("implementation_schedules") != switch_values
            or item.get("missing_implementation_schedule") != "sequential_v1"):
        problems.append("Item implementation schedules must equal the implementation_schedule switch values,"
                        " and an Item without one must read as sequential_v1")
    codes = result.get("finding_codes", [])
    if not isinstance(codes, list) or len(codes) != len(set(codes)):
        problems.append("result finding-code registry is not a unique list")
    for problem in problems:
        findings.append(Finding(
            "error", DELIVERY_CONTRACT_ROOT.as_posix(), 1,
            "delivery_contract_shape", problem,
            "align the canonical Delivery contracts with the runtime protocol",
        ))


def check_product_namespace(tree: Tree, findings: list[Finding]) -> None:
    """The vendor identity and product runtime namespace stay distinct."""
    if tree.product is None:
        findings.append(Finding(
            "error", PRODUCT_CONFIG_RELPATH, 1, "product_namespace",
            "product contract is missing or not valid JSON",
            "restore product.json; packaging and scaffolding read it",
        ))
        return
    try:
        build_distributions.load_product_contract(tree.root)
    except ValueError as exc:
        findings.append(Finding(
            "error", PRODUCT_CONFIG_RELPATH, 1, "product_namespace", str(exc),
            "restore the supported Agentrof vendor and Agent Marketplace product contract",
        ))
        return

    helpers = []
    expected_helper = build_distributions.marketplace_paths_source(
        tree.product
    ).encode()
    for plugin in sorted(path for path in tree.plugins_dir.iterdir() if path.is_dir()):
        helper = plugin / "scripts" / "marketplace_paths.py"
        if not helper.is_file():
            findings.append(Finding(
                "error", rel(tree, helper), 1, "product_namespace",
                f"{plugin.name} has no Agent Marketplace path resolver",
                "create plugins through tools/scaffold.py and keep the resolver in parity",
            ))
            continue
        helpers.append(helper)
    for helper in helpers:
        if helper.read_bytes() != expected_helper:
            findings.append(Finding(
                "error", rel(tree, helper), 1, "product_namespace",
                "plugin path resolver drifts from product.json",
                "regenerate it from the canonical product contract",
            ))

def check_task_input_catalog(tree: Tree, findings: list[Finding]) -> None:
    """Every shipped team role, skill and flow has one declared task route."""
    import importlib.util
    for plugin in plugin_dirs(tree):
        path = plugin / "scripts" / "task_inputs.py"
        if not path.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location("validated_task_inputs", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.catalog(plugin)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            findings.append(Finding("error", rel(tree, path), 1, "task_input_catalog",
                                    str(exc), "align canonical task routes with the shipped catalog"))


CHECKS = {
    "frontmatter_shape": check_frontmatter_shape,
    "agent_name": check_agent_name,
    "skill_name": check_skill_name,
    "trigger_policy": check_trigger_policy,
    "size_caps": check_size_caps,
    "section_contract": check_section_contract,
    "content_bans": check_content_bans,
    "agent_tech_nouns": check_agent_tech_nouns,
    "handwritten_counts": check_handwritten_counts,
    "dead_links": check_dead_links,
    "reference_triggers": check_reference_triggers,
    "registration": check_registration,
    "distribution_packaging": check_distribution_packaging,
    "single_team_contract": check_single_team_contract,
    "packaged_state_files": check_packaged_state_files,
    "json_hygiene": check_json_hygiene,
    "orchestrator_integrity": check_orchestrator_integrity,
    "choice_gate": check_choice_gate,
    "stdlib_only": check_stdlib_only,
    "naive_clock": check_naive_clock,
    "script_references": check_script_references,
    "template_placeholders": check_template_placeholders,
    "project_instruction_contract": check_project_instruction_contract,
    "spawn_shape_constitution": check_spawn_shape_constitution,
    "ba_schema_shape": check_ba_schema_shape,
    "wikilink_ban": check_wikilink_ban,
    "version_sync": check_version_sync,
    "vault_policy_shape": check_vault_policy_shape,
    "vault_wiring": check_vault_wiring,
    "model_config_shape": check_model_config_shape,
    "execution_profiles": check_execution_profiles,
    "review_panels": check_review_panels,
    "process_switches": check_process_switches,
    "story_size_measures": check_story_size_measures,
    "fact_ownership": check_fact_ownership,
    "limits_config_shape": check_limits_config_shape,
    "delivery_contract_shape": check_delivery_contract_shape,
    "product_namespace": check_product_namespace,
    "task_input_catalog": check_task_input_catalog,
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def load_policy_json(root: Path, relpath: str) -> dict | None:
    try:
        config = json.loads((root / relpath).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # ValueError covers JSONDecodeError and the UnicodeDecodeError of
        # bytes that are not UTF-8; the owning *_config_shape check reports it.
        return None
    return config if isinstance(config, dict) else None


def build_tree(root: Path) -> Tree:
    return Tree(
        root=root,
        plugins_dir=root / "plugins",
        docs_dir=root / "docs",
        readme=root / "README.md",
        marketplace=root / ".claude-plugin" / "marketplace.json",
        codex_marketplace=root / ".agents" / "plugins" / "marketplace.json",
        config=load_policy_json(root, MODEL_CONFIG_RELPATH),
        limits=load_policy_json(root, LIMITS_CONFIG_RELPATH),
        product=load_policy_json(root, PRODUCT_CONFIG_RELPATH),
    )


def run(root: Path) -> list[Finding]:
    tree = build_tree(root)
    findings: list[Finding] = []
    for check in CHECKS.values():
        check(tree, findings)
    findings.sort(key=lambda f: (f.path, f.line, f.check, f.message))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--json", action="store_true", help="emit findings as JSON lines")
    args = parser.parse_args()

    findings = run(args.root.resolve())
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]

    for f in findings:
        if args.json:
            print(json.dumps(f.__dict__, sort_keys=True))
        else:
            print(f"{f.severity.upper():7} {f.path}:{f.line} [{f.check}] {f.message} | Fix: {f.remediation}")

    print(f"validate: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
