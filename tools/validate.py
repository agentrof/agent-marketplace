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
}
RESERVED_CHECKLISTS = ["review-checklist.md", "qa-checklist.md"]

AGENT_BODY_MAX_LINES = 80
SKILL_MAX_LINES = 150
SKILL_WARN_LINES = 120
SKILL_MAX_BYTES = 8192
CONSTITUTION_MAX_LINES = 60
FLOW_MAX_LINES = 400
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
    r"nosql|docker|kubernetes|aws|azure|gcp|pytest|vitest|playwright|npm|pip)\b",
    re.IGNORECASE,
)

MD_LINK_RE = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+?)(?:\s+\"[^\"]*\")?\)")

KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

SNAKE_KEY_RE = re.compile(r"^[a-z0-9_]+$")

# Hook event names inside a plugin's hooks/hooks.json follow the host
# platform's PascalCase schema (SessionStart, PreToolUse, ...).
HOOK_EVENT_KEY_RE = re.compile(r"^[A-Z][A-Za-z]+$")

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
            extra = keys - AGENT_REQUIRED_KEYS
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
                    "remove them; agents carry exactly name, description, model",
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
                    "agents are passive; describe the role and say it is invoked by software-team flows",
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
        keys: list[tuple[str, str]] = []
        _walk_keys(data, "$", keys)
        for where, key in keys:
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
    "stdlib_only": check_stdlib_only,
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
