#!/usr/bin/env python3
"""Content validator for the Agent Marketplace repository.

Every rule in this repo is machine-enforced or it is not a rule. This tool
scans authored content (plugins/, platforms/, docs/, README.md,
CONTRIBUTING.md, .claude-plugin/, .agents/plugins/) and emits deterministic
findings. One error finding fails
the run.

Scope is an explicit allowlist; assets/ and .git/ are never scanned. The
home-directory path check alone reads every file the repository would
publish, generated dist/ and memory/ included.
VALIDATOR_BUILDERS in tools/tests/test_validator_contract.py breaks a valid
fixture repository once per check, in lockstep with CHECKS.

Stdlib only. Deterministic output: findings sorted by (path, line, check).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
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

AGENT_REQUIRED_KEYS = {"name", "description", "model_class", "output_contract"}
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
SKILL_OPTIONAL_KEYS = {"project_scope", "model_invocable", "user_invocable"}
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
    "container-environment",
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

# A home directory with its user name, /Users/<name>, /home/<name> or
# C:\Users\<name>, names a person and often the project a path was copied
# from. The POSIX form may follow a WSL, Cygwin or Git Bash drive segment
# (/mnt/c, /cygdrive/c, /c) and the Windows form may use forward slashes.
# A Claude Code project folder encodes a home as -Users-<name>-, and again
# after a dash when a session starts in a folder already named that way;
# -Users-<name> and, right after projects/, -home-<name> may end the folder
# name. The lowercase -home- form needs its trailing dash elsewhere, so a
# flag such as --home-dir never matches. The name must be written out, so a
# placeholder such as <name>, $USER or %USERNAME%, and a regex that detects
# such paths, never match. scripts/file_issue.py refuses the same paths in
# an issue it files.
HOME_PATH_RE = re.compile(
    r"(?:(?<![\w.~$/-])|(?<=file://)|(?<=/mnt/[a-z])|(?<=/cygdrive/[a-z])"
    r"|(?<=(?<![\w.~$/-])/[a-z]))/(?:Users|home)/(?P<posix>[A-Za-z0-9._-]+)"
    r"|(?<![A-Za-z0-9])[A-Za-z]:[\\/]+(?i:users)[\\/]+"
    r"(?P<windows>[^\\/:*?\"<>|\s%${}]+(?: [^\\/:*?\"<>|\s%${}]+)*)"
    r"|(?:(?<![A-Za-z0-9-])|(?<![A-Za-z0-9])[A-Za-z]-|(?<=[0-9-]-))"
    r"-(?:Users-(?P<encoded>[A-Za-z0-9]+)|home-(?P<encoded_home>[A-Za-z0-9]+)-)"
    r"|(?<=projects[/\\])-home-(?P<projects_home>[A-Za-z0-9]+)"
)
# Every HOME_PATH_RE match holds "home" or, in any case, "users"; this cheap
# search spares the full pattern text that holds neither.
HOME_PATH_HINT_RE = re.compile(r"home|(?i:users)")
# The homes of system and service accounts, which name no person: the
# GitHub Actions runner, the Node.js image user, Homebrew on Linux, the dev
# container user, the Ubuntu cloud image user, and the shared and default
# profiles of macOS and Windows. scripts/file_issue.py passes the same.
SYSTEM_HOMES = frozenset({"runner", "node", "linuxbrew", "vscode", "ubuntu",
                          "Shared", "Public", "Default"})
# Besides SYSTEM_HOMES, the check's one exception: a test fixture that
# needs a fake home declares its file and fake user names, which pass only
# in that file.
HOME_PATH_FIXTURES = {
    "tools/tests/test_issue_report.py": frozenset({"fixture"}),
    "tools/tests/test_model_fallback.py": frozenset({"dev"}),
    "tools/tests/test_validator_contract.py": frozenset({"fixture"}),
}

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
                    " model_class, output_contract",
                ))
            if extra:
                findings.append(Finding(
                    "error", rel(tree, path), 1, "frontmatter_shape",
                    f"agent frontmatter has unsupported keys: {sorted(extra)}",
                    "remove them; agents carry name, description, model_class,"
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
            model_class = fm.get("model_class", "")
            classes = declared_classes(tree)
            if model_class and classes and model_class not in classes:
                findings.append(Finding(
                    "error", rel(tree, path), 1, "frontmatter_shape",
                    f"agent model_class '{model_class}' is not in"
                    f" {sorted(classes)}",
                    "use a purpose class of tools/data/models.json"
                    " model_classes",
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
                    " and optional project_scope, model_invocable and user_invocable",
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
                        "model-catalog.json and generated distributions",
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


def home_path_user(match: re.Match) -> str:
    """The user name a HOME_PATH_RE match writes out, whichever form it has."""
    return next((value for value in match.groupdict().values() if value), "")


def refused_home_paths(text: str, relative: str = "") -> list[re.Match]:
    """Each home-directory path in text that names a person. A system or
    service home passes, and so does a fake user HOME_PATH_FIXTURES declares
    for the file at relative."""
    if HOME_PATH_HINT_RE.search(text) is None:
        return []
    passing = SYSTEM_HOMES | HOME_PATH_FIXTURES.get(relative, frozenset())
    return [match for match in HOME_PATH_RE.finditer(text)
            if home_path_user(match) not in passing]


def git_listed_files(root: Path) -> list[str] | None:
    """The files Git would commit at the top of a checkout: tracked ones and
    untracked ones it does not ignore. None when root is no checkout top."""
    try:
        top = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=False)
        if top.returncode or Path(top.stdout.strip()).resolve() != root.resolve():
            return None
        listed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others",
             "--exclude-standard"], capture_output=True, check=False)
    except OSError:
        return None
    if listed.returncode:
        return None
    return sorted({name for name in listed.stdout.decode("utf-8", "surrogateescape").split("\0")
                   if name})


def home_path_files(tree: Tree) -> list[Path]:
    """Every file the repository publishes: in a checkout, what Git would
    commit, generated dist/ and memory/ included; in an export, every file."""
    listed = git_listed_files(tree.root)
    if listed is None:
        paths = sorted(path for path in tree.root.rglob("*")
                       if not {".git", "__pycache__"} & set(path.relative_to(tree.root).parts))
    else:
        paths = [tree.root / name for name in listed]
    return [path for path in paths if path.is_file() and not path.is_symlink()]


def check_home_paths(tree: Tree, findings: list[Finding]) -> None:
    """No published file carries an absolute home-directory path (#357). The
    check names no project; only SYSTEM_HOMES and HOME_PATH_FIXTURES pass."""
    for path in home_path_files(tree):
        relative = rel(tree, path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if HOME_PATH_HINT_RE.search(text) is None:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            for _match in refused_home_paths(line, relative):
                findings.append(Finding(
                    "error", relative, lineno, "home_paths",
                    "absolute home-directory path found",
                    "write a project-relative path or a placeholder such as /home/<user>/;"
                    " a test fixture that needs a fake home declares its file and user in"
                    " HOME_PATH_FIXTURES",
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
            source = plugin / "skill-content" / name / "SKILL.md"
            header = read_text(source).split("---", 2)[1] if source.is_file() else ""
            # An entry is agent-invocable unless its canonical source opts out.
            implicit = "false" if re.search(r"(?m)^model_invocable:\s*false\s*$", header) else "true"
            if f"allow_implicit_invocation: {implicit}" not in metadata_text:
                error(metadata, f"{name} Codex invocation policy does not follow its canonical"
                      f" model_invocable ({implicit})",
                      "regenerate agents/openai.yaml with tools/build_distributions.py")
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
        for token in ("team_guard.py", "One Software Engineering Team", "AskUserQuestion", "generated project check"):
            if token.lower() not in claude_contract.lower():
                problems.append(f"Claude host contract lacks {token!r}")
        codex_contract = read_text(codex_contract_path) \
            if codex_contract_path.is_file() else ""
        for token in ("team_guard.py", "One Software Engineering Team", "request_user_input", "generated project check"):
            if token.lower() not in codex_contract.lower():
                problems.append(f"Codex host contract lacks {token!r}")
        hook_contracts = (
            (
                "Claude",
                tree.root / "platforms" / "claude" / plugin.name
                / "overlay" / "hooks" / "hooks.json",
                ("team_guard.py register", "model_fallback.py",
                 "autopilot_guard.py pre", "autopilot_guard.py post", "workspace_guard.py"),
            ),
            (
                "Codex",
                tree.root / "platforms" / "codex" / plugin.name
                / "overlay" / "hooks" / "hooks.json",
                ("team_guard.py register", "autopilot_guard.py pre", "autopilot_guard.py post",
                 "workspace_guard.py"),
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
        # A host's model catalog is keyed by the host's exact model IDs, which
        # the execution_profiles check validates against the adapter's format.
        is_model_catalog = path.name == build_distributions.MODEL_CATALOG_FILE \
            and "platforms" in path.parts
        keys: list[tuple[str, str]] = []
        _walk_keys(data, "$", keys)
        for where, key in keys:
            if is_codex_schema:
                continue
            if (is_hooks_manifest and where == "$.hooks"
                    and HOOK_EVENT_KEY_RE.match(key)):
                continue  # hook event names are the host platform's schema
            if is_hooks_manifest and "claude" in path.parts and key == "asyncRewake":
                continue  # Native host command-hook field.
            if (is_model_catalog or path.name == "pricing.json") and where == "$.models":
                continue  # keyed by the providers' exact model ids
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
    """Runtime scripts import only the standard library and their package's scripts.

    Imports are read from each script's syntax tree, those inside functions
    included, so prose that starts with "from " or "import " is never one. A
    relative import names a package script. A script that does not parse is
    its own finding, since its imports cannot be read.
    """
    stdlib = set(getattr(sys, "stdlib_module_names", ())) or FALLBACK_STDLIB
    scanned: set[Path] = set()

    def scan(scripts: Path, local: set[str]) -> None:
        if not scripts.is_dir():
            return
        for script in sorted(scripts.glob("*.py")):
            if script in scanned:
                continue
            scanned.add(script)
            try:
                # Bytes, so a coding cookie or BOM reads as Python reads the file.
                parsed = ast.parse(script.read_bytes(), filename=script.name)
            except (SyntaxError, ValueError) as error:  # ValueError: a NUL byte before Python 3.12
                findings.append(Finding(
                    "error", rel(tree, script), getattr(error, "lineno", None) or 1, "stdlib_only",
                    f"script does not parse, so its imports cannot be read: {getattr(error, 'msg', error)}",
                    "fix the syntax; runtime scripts run on every supported Python",
                ))
                continue
            imports: set[tuple[int, str]] = set()
            for node in ast.walk(parsed):
                if isinstance(node, ast.Import):
                    imports.update((node.lineno, alias.name.split(".")[0]) for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imports.add((node.lineno, node.module.split(".")[0]))
            for lineno, module in sorted(imports):
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
            # A skill script may import the runtime scripts its own package ships.
            local = {p.stem for p in (*scripts.glob("*.py"), *(plugin / "scripts").glob("*.py"))}
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
    for filename in ("agent-marketplace.md", "current-plan.md", "product-decisions.md",
                     "me.md", "profile.md"):
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
    try:
        user_context = build_distributions.user_context_files(tree.root)
    except ValueError as exc:
        problems.append(str(exc))
        user_context = None
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
            if user_context is not None and common.is_file() and team.is_file() \
                    and delta.is_file():
                try:
                    expected = build_distributions.project_instruction_text(
                        plugin.name, owner_host, companion, (common, team, delta),
                        user_context,
                    )
                except ValueError as exc:
                    problems.append(str(exc))
                else:
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
            "restore common and memory sources, the shared user context list,"
            " one team fragment, shared host deltas, canonical root imports,"
            " and regenerated distributions",
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


def check_model_config_shape(tree: Tree, findings: list[Finding]) -> None:
    """tools/data/models.json is the purpose class declaration the canonical
    agent frontmatter, the execution profiles and the class map read from; a
    malformed one would let those checks silently pass, so its shape is
    validated like any other policy artifact."""
    if tree.config is None:
        findings.append(Finding(
            "error", MODEL_CONFIG_RELPATH, 1, "model_config_shape",
            "model config is missing or not valid JSON",
            "restore tools/data/models.json; the purpose classes live there",
        ))
        return
    for problem in build_distributions.model_classes_problems(tree.config):
        findings.append(Finding(
            "error", MODEL_CONFIG_RELPATH, 1, "model_config_shape", problem,
            "fix the declaration; it feeds the frontmatter_shape model_class check"
            " and every execution profile",
        ))


def declared_classes(tree: Tree) -> set[str]:
    """Return the purpose classes tools/data/models.json declares, or none
    when it is malformed (model_config_shape reports that)."""
    if build_distributions.model_classes_problems(tree.config):
        return set()
    return set(tree.config["model_classes"])


def declared_slots(tree: Tree) -> set[str]:
    """Return every class slot the execution profiles map."""
    if build_distributions.model_classes_problems(tree.config):
        return set()
    return set(build_distributions.class_slots(tree.config["model_classes"]))


def check_execution_profiles(tree: Tree, findings: list[Finding]) -> None:
    """Every host pins its catalog models by documented exact IDs and maps
    exactly the class slots to a catalog model and a supported
    effort; model names stay under platforms/."""
    try:
        adapters = build_distributions.load_adapters(tree.root)
    except ValueError:
        return  # registration and product_namespace report adapter failures
    slots = declared_slots(tree)
    if not slots:
        return  # model_config_shape reports a malformed class declaration
    for host, adapter in adapters.items():
        tables = {}
        for kind, path, hint in (
                ("catalog", build_distributions.model_catalog_path(tree.root, host),
                 "key each catalog model by an exact ID the host documents, with"
                 " its family, supported efforts, sources and verified date"),
                ("profile", build_distributions.execution_profile_path(tree.root, host),
                 "map every class slot of tools/data/models.json to a model"
                 " of the host's model catalog and an effort that model supports")):
            try:
                tables[kind] = (path, json.loads(read_text(path)), hint)
            except (OSError, json.JSONDecodeError):
                findings.append(Finding(
                    "error", rel(tree, path), 1, "execution_profiles",
                    f"{path.name} is missing or not valid JSON", hint,
                ))
        catalog = None
        if "catalog" in tables:
            path, value, hint = tables["catalog"]
            problems = build_distributions.model_catalog_problems(value, adapter)
            findings.extend(Finding(
                "error", rel(tree, path), 1, "execution_profiles", problem, hint,
            ) for problem in problems)
            catalog = None if problems else value
        if "profile" in tables:
            path, value, hint = tables["profile"]
            findings.extend(Finding(
                "error", rel(tree, path), 1, "execution_profiles", problem, hint,
            ) for problem in build_distributions.execution_profile_problems(
                value, catalog, adapter, slots))


def check_host_cli_versions(tree: Tree, findings: list[Finding]) -> None:
    """The host CLIs CI installs are no older than any catalog model's
    min_cli_version: the host gates start no role, so a lower pin would pass
    on a version whose roles cannot run their pinned model."""
    try:
        adapters = build_distributions.load_adapters(tree.root)
    except ValueError:
        return  # registration and product_namespace report adapter failures
    path = tree.root / build_distributions.HOST_CLI_VERSIONS_RELPATH
    hint = ("pin in tools/data/host-cli-versions.json a host CLI no older than every"
            " catalog model's min_cli_version")
    try:
        pins = json.loads(read_text(path))
    except (OSError, json.JSONDecodeError):
        findings.append(Finding("error", rel(tree, path), 1, "host_cli_versions",
                                "host CLI versions are missing or not valid JSON", hint))
        return
    catalogs = {}
    for host in adapters:
        try:
            catalogs[host] = json.loads(read_text(
                build_distributions.model_catalog_path(tree.root, host)))
        except (OSError, json.JSONDecodeError):
            continue  # execution_profiles reports a missing or broken catalog
    findings.extend(
        Finding("error", rel(tree, path), 1, "host_cli_versions", problem, hint)
        for problem in build_distributions.host_cli_problems(pins, catalogs, adapters))


def check_effort_policy(tree: Tree, findings: list[Finding]) -> None:
    """Each host's effort policy confirms and refuses only efforts of its
    vocabulary, states its evidence with sources and a verified date, and
    refuses no effort the package profile pins: a project may override a role
    class's effort only within it."""
    try:
        adapters = build_distributions.load_adapters(tree.root)
    except ValueError:
        return  # registration and product_namespace report adapter failures
    hint = ("name only the host's efforts; give each confirmed effort its evidence and each"
            " refused effort its reason, with official https sources and a verified date")
    for host, adapter in adapters.items():
        path = build_distributions.effort_policy_path(tree.root, host)
        try:
            policy = json.loads(read_text(path))
        except (OSError, json.JSONDecodeError):
            findings.append(Finding("error", rel(tree, path), 1, "effort_policy",
                                    f"{path.name} is missing or not valid JSON", hint))
            continue
        try:
            profile = json.loads(read_text(
                build_distributions.execution_profile_path(tree.root, host)))
        except (OSError, json.JSONDecodeError):
            profile = None  # execution_profiles reports a missing or broken table
        findings.extend(
            Finding("error", rel(tree, path), 1, "effort_policy", problem, hint)
            for problem in build_distributions.effort_policy_problems(policy, adapter, profile))


REVIEW_STEP_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    keys = [key for key, _value in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        raise ValueError(f"duplicate keys {duplicates}")
    return dict(pairs)


def _nonblank(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def implemented_derivations(source: str, registry: str = "STORY_SIZE_DERIVATIONS") -> set[str] | None:
    """Return the keys of a script's module-level registry dict, by default the
    backlog compiler's STORY_SIZE_DERIVATIONS, read from its source so the
    validator never imports a plugin script."""
    try:
        module = ast.parse(source)
    except SyntaxError:
        return None
    for node in module.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) and any(
                isinstance(target, ast.Name) and target.id == registry
                for target in node.targets):
            return {key.value for key in node.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)}
    return None


AUTOPILOT_POLICY_RELPATH = "skill-content/autopilot/data/autopilot-policy.json"
AUTOPILOT_SCRIPT_RELPATH = "skill-content/autopilot/scripts/autopilot.py"
AUTOPILOT_NUMBERS = ("default_duration_hours", "default_goal_cap_hours", "max_duration_hours",
                     "arming_ttl_minutes", "transport_interval_seconds", "transport_max_attempts",
                     "native_arming_wait_seconds", "native_watch_seconds", "native_poll_seconds", "native_retry_delay_seconds", "native_permit_ttl_seconds")
AUTOPILOT_KEYS = {"schema_version", "classes", "goal_kinds", "transport_error_codes",
                  *AUTOPILOT_NUMBERS}
AUTOPILOT_DEFAULTS = ("allowed", "excluded", "never")


def autopilot_policy_problems(data: object, plugin: Path) -> list[str]:
    """Return the problems of the autopilot entry's classes, goal kinds and durations."""
    if not isinstance(data, dict) or set(data) != AUTOPILOT_KEYS or data.get("schema_version") != 1:
        return [f"policy must hold exactly {', '.join(sorted(AUTOPILOT_KEYS))},"
                " with schema_version 1"]
    problems: list[str] = []
    numbers = {key: data[key] for key in AUTOPILOT_NUMBERS
               if isinstance(data[key], int) and not isinstance(data[key], bool) and data[key] > 0}
    problems += [f"{key} must be a positive whole number"
                 for key in AUTOPILOT_NUMBERS if key not in numbers]
    if not 1 <= numbers.get("transport_max_attempts", 0) <= 3:
        problems.append("transport_max_attempts must be between 1 and 3")
    if not 1 <= numbers.get("transport_interval_seconds", 0) <= 3600:
        problems.append("transport_interval_seconds must be between 1 and 3600")
    if numbers.get("native_watch_seconds", 0) > numbers.get("max_duration_hours", 0) * 3600:
        problems.append("native_watch_seconds must not outlive the maximum grant")
    if numbers.get("native_arming_wait_seconds", 0) > numbers.get("arming_ttl_minutes", 0) * 60:
        problems.append("native_arming_wait_seconds must not outlive the arming record")
    codes = data.get("transport_error_codes")
    if (not isinstance(codes, list) or not codes or any(not _nonblank(code) for code in codes)
            or len(codes) != len(set(codes))):
        problems.append("transport_error_codes must list unique nonempty error codes")
    maximum = numbers.get("max_duration_hours")
    problems += [f"{key} {numbers[key]} is above max_duration_hours {maximum}"
                 for key in ("default_duration_hours", "default_goal_cap_hours")
                 if maximum and key in numbers and numbers[key] > maximum]
    ids: dict[str, list[str]] = {"class": [], "goal kind": []}
    classes = data["classes"] if isinstance(data["classes"], list) else []
    for entry in classes:
        if not isinstance(entry, dict) or set(entry) != {"id", "description", "default"} \
                or not _nonblank(entry.get("id")) or not _nonblank(entry.get("description")):
            problems.append("every class holds exactly an id, a description and a default")
            continue
        ids["class"].append(entry["id"])
        if entry["default"] not in AUTOPILOT_DEFAULTS:
            problems.append(f"class {entry['id']!r} has unknown default {entry['default']!r}")
    if not any(isinstance(entry, dict) and entry.get("default") == "never" for entry in classes):
        problems.append("the never set is empty: at least one class is never delegated")
    script = plugin / AUTOPILOT_SCRIPT_RELPATH
    readers = implemented_derivations(read_text(script), "GOAL_READERS") if script.is_file() else None
    if readers is None:
        problems.append(f"{AUTOPILOT_SCRIPT_RELPATH} declares no GOAL_READERS registry")
        readers = set()
    kinds = data["goal_kinds"] if isinstance(data["goal_kinds"], list) else []
    if not kinds:
        problems.append("goal_kinds must declare at least one goal kind")
    for entry in kinds:
        if not isinstance(entry, dict) or set(entry) != {"id", "description", "end"} \
                or not _nonblank(entry.get("id")) or not _nonblank(entry.get("description")):
            problems.append("every goal kind holds exactly an id, a description and its end")
            continue
        ids["goal kind"].append(entry["id"])
        end = entry["end"]
        if end == "none":
            continue
        if not isinstance(end, dict) or set(end) != {"compiler", "terminal_statuses"}:
            problems.append(f"goal kind {entry['id']!r} end is none or holds exactly a compiler"
                            " and terminal_statuses")
            continue
        statuses = end["terminal_statuses"]
        if not isinstance(statuses, list) or not statuses \
                or not all(_nonblank(status) for status in statuses) \
                or len(set(statuses)) != len(statuses):
            problems.append(f"readable goal kind {entry['id']!r} declares no distinct terminal"
                            " statuses")
        if end["compiler"] not in readers or not (plugin / str(end["compiler"])).is_file():
            problems.append(f"goal kind {entry['id']!r} names compiler {end['compiler']!r},"
                            " which is no package script autopilot.py reads")
    for label, values in ids.items():
        problems += [f"{label} id {value!r} must be lowercase snake_case"
                     for value in values if not REVIEW_STEP_ID_RE.match(value)]
        problems += [f"duplicate {label} {value!r}"
                     for value in sorted({value for value in values if values.count(value) > 1})]
    return problems


def check_autopilot_policy(tree: Tree, findings: list[Finding]) -> None:
    """The autopilot entry's classes, goal kinds and durations are validated
    data, and each readable goal ends at statuses its owning compiler reads."""
    for plugin in plugin_dirs(tree):
        path = plugin / AUTOPILOT_POLICY_RELPATH
        if not path.is_file():
            if path.parents[1].is_dir():
                findings.append(Finding(
                    "error", rel(tree, path.parents[1]), 1, "autopilot_policy",
                    "the autopilot entry has no data/autopilot-policy.json",
                    "declare its classes, goal kinds and durations"))
            continue
        try:
            data = json.loads(read_text(path), object_pairs_hook=_unique_json_object)
        except (json.JSONDecodeError, ValueError) as exc:
            findings.append(Finding(
                "error", rel(tree, path), 1, "autopilot_policy",
                f"autopilot policy is not valid unique-key JSON: {exc}", "fix the syntax"))
            continue
        for problem in autopilot_policy_problems(data, plugin):
            findings.append(Finding(
                "error", rel(tree, path), 1, "autopilot_policy", problem,
                "declare each class and goal kind once, with a known default, a readable"
                " end and durations within the maximum"))


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

CHECKS = {
    "frontmatter_shape": check_frontmatter_shape,
    "agent_name": check_agent_name,
    "skill_name": check_skill_name,
    "trigger_policy": check_trigger_policy,
    "size_caps": check_size_caps,
    "section_contract": check_section_contract,
    "content_bans": check_content_bans,
    "home_paths": check_home_paths,
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
    "model_config_shape": check_model_config_shape,
    "execution_profiles": check_execution_profiles,
    "host_cli_versions": check_host_cli_versions,
    "effort_policy": check_effort_policy,
    "autopilot_policy": check_autopilot_policy,
    "limits_config_shape": check_limits_config_shape,
    "product_namespace": check_product_namespace,
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
