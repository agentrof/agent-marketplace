#!/usr/bin/env python3
"""Content validator for the Agent Marketplace repository.

Every rule in this repo is machine-enforced or it is not a rule. This tool
scans authored content (plugins/, docs/, README.md, CONTRIBUTING.md,
.claude-plugin/) and emits deterministic findings. One error finding fails
the run.

Scope is an explicit allowlist; assets/, memory/, tools/ and .git/ are never
scanned. Fixtures under tools/tests/fixtures/ exercise every check.

Stdlib only. Deterministic output: findings sorted by (path, line, check).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants and policy tables
# ---------------------------------------------------------------------------

EM_DASH = "—"

AGENT_REQUIRED_KEYS = {"name", "description", "model"}
AGENT_MODEL_ENUM = {"opus", "sonnet", "haiku", "inherit"}
# Optional capability restriction: an agent MAY carry tools: as a whitelist,
# and only the read-only set is legal. Read-only roles (challengers, expert
# panels) are denied write capability at spawn time, not by instruction.
AGENT_OPTIONAL_KEYS = {"tools"}
AGENT_READONLY_TOOLS = {"Read", "Grep", "Glob"}
SKILL_REQUIRED_KEYS = {"name", "description"}
SKILL_VISIBILITY_KEYS = {"user-invocable", "disable-model-invocation"}

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

AGENT_BODY_MAX_LINES = 80
SKILL_MAX_LINES = 150
SKILL_WARN_LINES = 120
SKILL_MAX_BYTES = 8192
CONSTITUTION_MAX_LINES = 60
# Raised from 400 with the vault-law wiring, then from 416 with the
# harness-neutral dispatcher paragraph and the decision-gate formula
# (develop.md carries both); applies to every flow.
FLOW_MAX_LINES = 432
REFERENCE_WARN_LINES = 500

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

SNAKE_KEY_RE = re.compile(r"^[a-z0-9_]+$")

# Hook event names inside a plugin's hooks/hooks.json follow the host
# platform's PascalCase schema (SessionStart, PreToolUse, ...).
HOOK_EVENT_KEY_RE = re.compile(r"^[A-Z][A-Za-z]+$")

# Inline code spans render as code, not links; wikilink_ban strips them
# before scanning, the same way fenced blocks are skipped.
INLINE_CODE_RE = re.compile(r"`[^`]*`")

# The vault app's property types; vault-policy.json property_types values
# must come from this enum.
OBSIDIAN_PROPERTY_TYPES = {"text", "list", "number", "checkbox", "date", "datetime"}

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
    return files


def plugin_dirs(tree: Tree) -> list[Path]:
    if not tree.plugins_dir.is_dir():
        return []
    return sorted(p for p in tree.plugins_dir.iterdir() if p.is_dir())


def agent_files(plugin: Path) -> list[Path]:
    agents = plugin / "agents"
    return sorted(agents.glob("*.md")) if agents.is_dir() else []


def skill_dirs(plugin: Path) -> list[Path]:
    skills = plugin / "skills"
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
                    "add the missing keys; agents require exactly name, description, model",
                ))
            if extra:
                findings.append(Finding(
                    "error", rel(tree, path), 1, "frontmatter_shape",
                    f"agent frontmatter has unsupported keys: {sorted(extra)}",
                    "remove them; agents carry name, description, model and"
                    " optionally a read-only tools whitelist",
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
            model = fm.get("model", "")
            if model and model not in AGENT_MODEL_ENUM:
                findings.append(Finding(
                    "error", rel(tree, path), 1, "frontmatter_shape",
                    f"agent model '{model}' is not in {sorted(AGENT_MODEL_ENUM)}",
                    "use an alias from the enum; concrete model ids are banned",
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
            extra = keys - SKILL_REQUIRED_KEYS - SKILL_VISIBILITY_KEYS
            if missing:
                findings.append(Finding(
                    "error", rel(tree, skill_md), 1, "frontmatter_shape",
                    f"skill frontmatter missing keys: {sorted(missing)}",
                    "add the missing keys; skills require name and description",
                ))
            if extra:
                findings.append(Finding(
                    "error", rel(tree, skill_md), 1, "frontmatter_shape",
                    f"skill frontmatter has unsupported keys: {sorted(extra)}",
                    "remove them; allowed keys are name, description and one visibility flag",
                ))


def check_agent_name(tree: Tree, findings: list[Finding]) -> None:
    seen: dict[str, str] = {}
    for plugin in plugin_dirs(tree):
        for path in agent_files(plugin):
            fm, _, _ = parse_frontmatter(read_text(path))
            name = fm.get("name", "")
            expected = f"{plugin.name}-{path.stem}"
            if name != expected:
                findings.append(Finding(
                    "error", rel(tree, path), 1, "agent_name",
                    f"agent frontmatter name '{name}' must equal '{expected}'",
                    f"set name: {expected} (plugin-prefixed, collision-safe)",
                ))
            if name:
                if name in seen:
                    findings.append(Finding(
                        "error", rel(tree, path), 1, "agent_name",
                        f"agent name '{name}' already used by {seen[name]}",
                        "agent names must be globally unique across the repo",
                    ))
                else:
                    seen[name] = rel(tree, path)


def check_skill_name(tree: Tree, findings: list[Finding]) -> None:
    seen: dict[str, str] = {}
    for plugin in plugin_dirs(tree):
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
                        "skill names must be unique across the repo",
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
            hidden = fm.get("user-invocable", "") == "false"
            entry = fm.get("disable-model-invocation", "") == "true"
            if hidden == entry:
                findings.append(Finding(
                    "error", rel(tree, skill_md), 1, "trigger_policy",
                    "skill must carry exactly one visibility flag",
                    "entry skills set disable-model-invocation: true; knowledge skills set user-invocable: false",
                ))
            if not fm.get("description"):
                findings.append(Finding(
                    "error", rel(tree, skill_md), 1, "trigger_policy",
                    "skill description is empty",
                    "add a description stating purpose and invocation context",
                ))


def check_size_caps(tree: Tree, findings: list[Finding]) -> None:
    for plugin in plugin_dirs(tree):
        for path in agent_files(plugin):
            _, body_start, body = parse_frontmatter(read_text(path))
            body_lines = len([l for l in body.splitlines()])
            if body_lines > AGENT_BODY_MAX_LINES:
                findings.append(Finding(
                    "error", rel(tree, path), body_start, "size_caps",
                    f"agent body is {body_lines} lines (cap {AGENT_BODY_MAX_LINES})",
                    "cut to constitution altitude; move any depth into skills",
                ))
        for sdir in skill_dirs(plugin):
            skill_md = sdir / "SKILL.md"
            if skill_md.is_file():
                text = read_text(skill_md)
                n = len(text.splitlines())
                if n > SKILL_MAX_LINES:
                    findings.append(Finding(
                        "error", rel(tree, skill_md), 1, "size_caps",
                        f"SKILL.md is {n} lines (cap {SKILL_MAX_LINES})",
                        "move depth into references/ and keep SKILL.md a decision surface",
                    ))
                elif n > SKILL_WARN_LINES:
                    findings.append(Finding(
                        "warning", rel(tree, skill_md), 1, "size_caps",
                        f"SKILL.md is {n} lines (warn threshold {SKILL_WARN_LINES})",
                        "consider moving detail into references/",
                    ))
                if len(text.encode("utf-8")) > SKILL_MAX_BYTES:
                    findings.append(Finding(
                        "error", rel(tree, skill_md), 1, "size_caps",
                        f"SKILL.md exceeds {SKILL_MAX_BYTES} bytes",
                        "move depth into references/",
                    ))
            refs = sdir / "references"
            if refs.is_dir():
                for ref in sorted(refs.rglob("*.md")):
                    n = len(read_text(ref).splitlines())
                    if n > REFERENCE_WARN_LINES:
                        findings.append(Finding(
                            "warning", rel(tree, ref), 1, "size_caps",
                            f"reference is {n} lines (warn threshold {REFERENCE_WARN_LINES})",
                            "split into focused reference files",
                        ))
        constitution = plugin / "constitution.md"
        if constitution.is_file():
            n = len(read_text(constitution).splitlines())
            if n > CONSTITUTION_MAX_LINES:
                findings.append(Finding(
                    "error", rel(tree, constitution), 1, "size_caps",
                    f"constitution is {n} lines (cap {CONSTITUTION_MAX_LINES})",
                    "the constitution must stay terse; cut to principle altitude",
                ))
        flows = plugin / "flows"
        if flows.is_dir():
            for flow in sorted(flows.glob("*.md")):
                n = len(read_text(flow).splitlines())
                if n > FLOW_MAX_LINES:
                    findings.append(Finding(
                        "error", rel(tree, flow), 1, "size_caps",
                        f"flow is {n} lines (cap {FLOW_MAX_LINES})",
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
                        "model aliases live only in agent frontmatter model:",
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
            if fm.get("user-invocable", "") != "false":
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
    registered: dict[str, str] = {}
    if tree.marketplace.is_file():
        try:
            data = json.loads(read_text(tree.marketplace))
        except json.JSONDecodeError:
            return  # json_hygiene reports the parse failure
        for entry in data.get("plugins", []):
            name = entry.get("name", "")
            source = entry.get("source", "")
            registered[name] = source
            src_dir = (tree.marketplace.parent.parent / source).resolve()
            plugin_json = src_dir / ".claude-plugin" / "plugin.json"
            if not plugin_json.is_file():
                findings.append(Finding(
                    "error", rel(tree, tree.marketplace), 1, "registration",
                    f"registered plugin '{name}' has no plugin.json at {source}",
                    "create .claude-plugin/plugin.json or fix the source path",
                ))
                continue
            try:
                pj = json.loads(read_text(plugin_json))
            except json.JSONDecodeError:
                continue
            if pj.get("name") != name:
                findings.append(Finding(
                    "error", rel(tree, plugin_json), 1, "registration",
                    f"plugin.json name '{pj.get('name')}' does not match registry entry '{name}'",
                    "keep directory, plugin.json name and registry entry identical",
                ))
    for plugin in plugin_dirs(tree):
        if plugin.name not in registered:
            findings.append(Finding(
                "error", rel(tree, plugin), 1, "registration",
                f"plugin directory '{plugin.name}' is not registered in marketplace.json",
                "add a plugins[] entry with source ./plugins/" + plugin.name,
            ))
        pj = plugin / ".claude-plugin" / "plugin.json"
        if not pj.is_file():
            findings.append(Finding(
                "error", rel(tree, plugin), 1, "registration",
                f"plugin '{plugin.name}' has no .claude-plugin/plugin.json",
                "add the plugin manifest",
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
    if tree.plugins_dir.is_dir():
        json_paths.extend(sorted(tree.plugins_dir.rglob("*.json")))
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
        # Vault payload files under a plugin's templates/ carry the vault
        # app's own key schema (camelCase settings, kebab plugin ids); only
        # the parse requirement applies there.
        is_vault_payload = "templates" in path.parts and ".obsidian" in path.parts
        keys: list[tuple[str, str]] = []
        _walk_keys(data, "$", keys)
        for where, key in keys:
            if is_vault_payload:
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


QUESTION_POPUP_MARKER = "explicit user choice"
QUESTION_POPUP_TOKEN = "AskUserQuestion"
QUESTION_POPUP_FALLBACK = "numbered option list"
QUESTION_POPUP_WINDOW = 3


def check_question_popup(tree: Tree, findings: list[Finding]) -> None:
    """The decision-gate formula, bidirectional and per site (a window of
    lines absorbs prose wrapping):
    (a) a gate declared with the marker phrase must name the popup
        nearby, so the popup discipline cannot be silently stripped from
        a gate site;
    (b) the popup token must carry the harness fallback (a numbered
        option list) nearby, so the same shipped content still asks on a
        harness without the popup."""
    for path in iter_scope_files(tree, ".md"):
        lines = read_text(path).splitlines()
        for idx, line in enumerate(lines):
            lo = max(0, idx - QUESTION_POPUP_WINDOW)
            window = lines[lo:idx + QUESTION_POPUP_WINDOW + 1]
            if (QUESTION_POPUP_MARKER in line
                    and not any(QUESTION_POPUP_TOKEN in w for w in window)):
                findings.append(Finding(
                    "error", rel(tree, path), idx + 1, "question_popup",
                    f"gate marker ('{QUESTION_POPUP_MARKER}') without the"
                    f" {QUESTION_POPUP_TOKEN} popup named nearby",
                    "decision gates ask through the AskUserQuestion popup"
                    " (a numbered option list where the popup is"
                    " unavailable); state it at the gate site",
                ))
            if (QUESTION_POPUP_TOKEN in line
                    and not any(QUESTION_POPUP_FALLBACK in w for w in window)):
                findings.append(Finding(
                    "error", rel(tree, path), idx + 1, "question_popup",
                    f"{QUESTION_POPUP_TOKEN} named without the harness"
                    " fallback nearby",
                    f"every popup site carries '{QUESTION_POPUP_FALLBACK}'"
                    " within three lines, so the gate still asks on a"
                    " harness without the popup",
                ))


# Fallback for runtimes that predate sys.stdlib_module_names.
FALLBACK_STDLIB = frozenset(
    "__future__ abc argparse ast asyncio base64 bisect calendar collections "
    "configparser contextlib copy csv dataclasses datetime decimal difflib "
    "enum errno fnmatch functools getpass glob gzip hashlib heapq hmac html "
    "http importlib inspect io itertools json logging math mimetypes "
    "multiprocessing operator os pathlib pickle platform pprint queue random "
    "re secrets shlex shutil signal socket sqlite3 statistics string struct "
    "subprocess sys tarfile tempfile textwrap threading time tomllib "
    "traceback types typing unicodedata unittest urllib uuid warnings "
    "webbrowser xml zipfile zlib".split()
)


def check_stdlib_only(tree: Tree, findings: list[Finding]) -> None:
    stdlib = set(getattr(sys, "stdlib_module_names", ())) or FALLBACK_STDLIB
    for plugin in plugin_dirs(tree):
        script_dirs = [sdir / "scripts" for sdir in skill_dirs(plugin)]
        script_dirs.append(plugin / "scripts")  # plugin-level runtime scripts
        for scripts in script_dirs:
            if not scripts.is_dir():
                continue
            local = {p.stem for p in scripts.glob("*.py")}
            for script in sorted(scripts.glob("*.py")):
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
                        "skill scripts run anywhere; use only the standard library",
                    ))


NAIVE_CLOCK_RE = re.compile(r"\bdate\.today\(\)|\bdatetime\.now\(\s*\)|\butcnow\(")


def check_naive_clock(tree: Tree, findings: list[Finding]) -> None:
    """One clock: plugin scripts read time as datetime.now(timezone.utc).
    A naive or local clock call (date.today(), no-arg datetime.now(),
    utcnow()) puts local or deprecated time into artifacts and diverges
    from the UTC values every other writer records."""
    for plugin in plugin_dirs(tree):
        script_dirs = [sdir / "scripts" for sdir in skill_dirs(plugin)]
        script_dirs.append(plugin / "scripts")
        for scripts in script_dirs:
            if not scripts.is_dir():
                continue
            for script in sorted(scripts.glob("*.py")):
                for lineno, line in enumerate(read_text(script).splitlines(), start=1):
                    if NAIVE_CLOCK_RE.search(line):
                        findings.append(Finding(
                            "error", rel(tree, script), lineno, "naive_clock",
                            "local or naive clock call",
                            "plugin scripts read the clock as"
                            " datetime.now(timezone.utc); local time never"
                            " enters artifacts",
                        ))


SCRIPT_REF_RE = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([A-Za-z0-9_\-./]+\.py)\b")

# The dispatcher grammar shipped content uses to reach plugin files:
# "$RUN" run|path "$TEAM"|<plugin-name> <relpath>. "$TEAM" resolves to
# the plugin whose markdown carries the reference.
RUN_REF_RE = re.compile(
    r"\"\$RUN\"\s+(run|path)\s+(\"\$TEAM\"|[a-z0-9]+(?:-[a-z0-9]+)*)"
    r"\s+([A-Za-z0-9_\-./]+)")


def check_script_references(tree: Tree, findings: list[Finding]) -> None:
    """Every plugin-file reference in shipped markdown must resolve to a
    shipped file: a flow naming a script or flow that does not exist is a
    broken contract, not documentation. Covers the legacy
    ${CLAUDE_PLUGIN_ROOT} script tokens and the dispatcher grammar
    ("$RUN" run|path ... <relpath>)."""
    for plugin in plugin_dirs(tree):
        for path in sorted(plugin.rglob("*.md")):
            for lineno, line in enumerate(read_text(path).splitlines(), start=1):
                for match in SCRIPT_REF_RE.finditer(line):
                    if not (plugin / match.group(1)).is_file():
                        findings.append(Finding(
                            "error", rel(tree, path), lineno, "script_references",
                            f"referenced script does not exist: {match.group(1)}",
                            "ship the script or fix the reference; flows carry"
                            " only real mechanics",
                        ))
                for match in RUN_REF_RE.finditer(line):
                    verb, owner, relpath = match.groups()
                    target_plugin = plugin if owner == '"$TEAM"' \
                        else tree.plugins_dir / owner
                    if not target_plugin.is_dir():
                        findings.append(Finding(
                            "error", rel(tree, path), lineno, "script_references",
                            f"dispatcher reference names unknown plugin: {owner}",
                            "name a shipped plugin or use \"$TEAM\"",
                        ))
                        continue
                    target = target_plugin / relpath
                    if verb == "run" and relpath.endswith(".py") \
                            and not target.is_file():
                        findings.append(Finding(
                            "error", rel(tree, path), lineno, "script_references",
                            f"dispatched script does not exist: {relpath}",
                            "ship the script or fix the reference; flows carry"
                            " only real mechanics",
                        ))
                    elif verb == "path" and "<" not in relpath \
                            and not target.exists():
                        findings.append(Finding(
                            "error", rel(tree, path), lineno, "script_references",
                            f"dispatched path does not exist: {relpath}",
                            "ship the file or fix the reference",
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
        skills_dir = plugin / "skills"
        skill_text = "".join(
            read_text(p) for p in sorted(skills_dir.glob("*/SKILL.md"))
        ) if skills_dir.is_dir() else ""
        for tpl in sorted(p for p in templates.rglob("*") if p.is_file()):
            parts = tpl.parts
            if ".obsidian" in parts and "plugins" in parts:
                # Vendored third-party vault plugins are shipped verbatim;
                # their bundles carry their own template tokens, which are
                # never substitutions this plugin's skills owe.
                continue
            for lineno, line in enumerate(read_text(tpl).splitlines(), start=1):
                for token in PLACEHOLDER_RE.findall(line):
                    if f"{{{{{token}}}}}" not in skill_text:
                        findings.append(Finding(
                            "error", rel(tree, tpl), lineno,
                            "template_placeholders",
                            f"template placeholder {{{{{token}}}}} is not"
                            " named by any skill",
                            "name the substitution in the materializing"
                            " skill or drop the placeholder",
                        ))


def check_ba_schema_shape(tree: Tree, findings: list[Finding]) -> None:
    """Any shipped space-schema.json must be well-formed: the compiler is
    parameterized by it, so a malformed schema is a broken product."""
    for plugin in plugin_dirs(tree):
        for path in sorted(plugin.glob("skills/*/data/space-schema.json")):
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
            challenge = schema.get("challenge")
            for key in ("round_file_format", "space_round_file_format"):
                value = (challenge.get(key)
                         if isinstance(challenge, dict) else None)
                if not (isinstance(value, str) and "{n}" in value
                        and value.endswith("-review.md")):
                    err(f"challenge.{key} must be a format string carrying"
                        " {n} and ending in -review.md",
                        "review round filenames are schema-driven; every"
                        " typed review file carries the -review suffix like"
                        " the rest of the folder law")
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
                            " suffix is schema data, never implied (review"
                            " rounds carry the review suffix through their"
                            " round file format)")
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
    """A registered plugin's marketplace entry and its plugin.json declare
    the same version: a release bumps both in one commit, and drift ships a
    catalog that lies about what installs."""
    if not tree.marketplace.is_file():
        return
    try:
        data = json.loads(read_text(tree.marketplace))
    except json.JSONDecodeError:
        return  # json_hygiene reports the parse failure
    for entry in data.get("plugins", []):
        source = entry.get("source", "")
        entry_version = entry.get("version", "")
        plugin_json = (tree.marketplace.parent.parent / source).resolve() \
            / ".claude-plugin" / "plugin.json"
        if not plugin_json.is_file():
            continue  # registration reports the missing manifest
        try:
            pj = json.loads(read_text(plugin_json))
        except json.JSONDecodeError:
            continue
        plugin_version = pj.get("version", "")
        if entry_version and plugin_version and entry_version != plugin_version:
            findings.append(Finding(
                "error", rel(tree, plugin_json), 1, "version_sync",
                f"plugin version '{plugin_version}' does not match marketplace"
                f" entry '{entry_version}'",
                "bump plugin.json and its marketplace.json entry together",
            ))


def check_vault_wiring(tree: Tree, findings: list[Finding]) -> None:
    """A plugin that ships the vault-law skill wires every docs-facing
    surface to it: an entry or flow that names the docs tree must also
    name the obsidian-vault skill, so the law cannot be skipped by
    omission. Deliberately broad: readers pay one pointer sentence."""
    for plugin in plugin_dirs(tree):
        if not (plugin / "skills" / "obsidian-vault").is_dir():
            continue
        surfaces = sorted(plugin.glob("skills/*/SKILL.md"))
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
        for path in sorted(plugin.glob("skills/*/data/vault-policy.json")):
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
                        "maps_dir", "generated_marker_prefix", "attachments_dir"):
                value = policy.get(key)
                if not (isinstance(value, str) and value):
                    err(f"'{key}' must be a non-empty string",
                        "every structural name is policy-driven, never implied")
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
            color_groups = policy.get("graph_color_groups")
            if (not isinstance(color_groups, list) or not color_groups
                    or not all(isinstance(s, str) and s
                               for s in color_groups)):
                err("graph_color_groups must be a non-empty list of graph"
                    " query strings",
                    "the graph legend is policy-ordered; the committed"
                    " graph config derives from it")
                color_groups = None
            else:
                for query in color_groups:
                    if "|" in query or re.search(r"tag:#\S*\*", query):
                        err(f"graph_color_groups query '{query}' uses"
                            " grammar the graph does not support",
                            "graph queries have no pipe-OR and no tag"
                            " wildcards; write OR-joined full tags")
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
            peer_min = policy.get("nav_peer_min")
            peer_max = policy.get("nav_peer_max")
            if (not isinstance(peer_min, int) or not isinstance(peer_max, int)
                    or isinstance(peer_min, bool) or isinstance(peer_max, bool)
                    or not 1 <= peer_min <= peer_max):
                err("nav_peer_min/nav_peer_max must be integers with"
                    " 1 <= min <= max",
                    "the nav footer peer range is policy, not prose")
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
                if spec.get("id_source") not in ("alias", "filename"):
                    err(f"decision tree '{tree_name}' id_source must be"
                        " 'alias' or 'filename'",
                        "the record id's home is declared per tree: alias"
                        " is the law, filename the legacy escape hatch")
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
            else:
                for key, value in sorted(prop_types.items()):
                    if value not in OBSIDIAN_PROPERTY_TYPES:
                        err(f"property_types['{key}'] value '{value}' is not"
                            f" one of {sorted(OBSIDIAN_PROPERTY_TYPES)}",
                            "use the vault app's property type enum")
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
            # Graph color completeness: every doc type known to the
            # taxonomy (sibling space-schema doc_types, kebab-ized, UNION
            # extra_doc_types) owns a tag:#doc/<type> color group, and
            # every group tag names a known type. An uncolored type or a
            # dead legend entry is a mechanical error, never an oversight.
            schema_types: set[str] = set()
            for spath in sorted(plugin.glob("skills/*/data/space-schema.json")):
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
            colors = policy.get("graph_group_colors")
            if colors is not None and (
                    not isinstance(colors, list)
                    or not all(isinstance(c, int)
                               and not isinstance(c, bool)
                               and 0 <= c <= 0xFFFFFF for c in colors)
                    or (color_groups is not None
                        and len(colors) != len(color_groups))):
                err("graph_group_colors must list one 0..16777215 rgb int"
                    " per graph_color_groups entry, same order",
                    "the palette is policy data: one authored color per"
                    " group; the committed graph config derives from it")
            # Parity with the shipped seeds: the policy and templates/vault
            # describe one product; they may not drift.
            vault_tpl = plugin / "templates" / "vault"
            if not vault_tpl.is_dir():
                continue
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
                if color_groups is not None and queries != color_groups:
                    err("graph.json colorGroups do not match policy"
                        " graph_color_groups (same queries, same order)",
                        "the committed graph legend derives from the policy;"
                        " regenerate graph.json from graph_color_groups")
                if (isinstance(colors, list) and color_groups is not None
                        and len(colors) == len(color_groups)):
                    rgbs = [(group.get("color") or {}).get("rgb")
                            for group in graph_data.get("colorGroups", [])
                            if isinstance(group, dict)]
                    if rgbs != colors:
                        err("graph.json group colors do not match policy"
                            " graph_group_colors (same ints, same order)",
                            "the palette is authored once in the policy;"
                            " regenerate graph.json from it")
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
    "json_hygiene": check_json_hygiene,
    "orchestrator_integrity": check_orchestrator_integrity,
    "question_popup": check_question_popup,
    "stdlib_only": check_stdlib_only,
    "naive_clock": check_naive_clock,
    "script_references": check_script_references,
    "template_placeholders": check_template_placeholders,
    "spawn_shape_constitution": check_spawn_shape_constitution,
    "ba_schema_shape": check_ba_schema_shape,
    "wikilink_ban": check_wikilink_ban,
    "version_sync": check_version_sync,
    "vault_policy_shape": check_vault_policy_shape,
    "vault_wiring": check_vault_wiring,
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def build_tree(root: Path) -> Tree:
    return Tree(
        root=root,
        plugins_dir=root / "plugins",
        docs_dir=root / "docs",
        readme=root / "README.md",
        marketplace=root / ".claude-plugin" / "marketplace.json",
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
