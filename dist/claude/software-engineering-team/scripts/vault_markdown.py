#!/usr/bin/env python3
"""Shared Markdown note parsing for the vault tooling.

One home for the note grammar every vault script reads: frontmatter, the
wikilink and link patterns, generated body blocks and the body scan that fills
a Doc. Stdlib only; it imports no compiler, so the vault tooling and the fast
lane keep working without any of them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# Wikilink grammar (single parsing home).
WIKILINK_RE = re.compile(r"(?P<embed>!?)\[\[(?P<inner>[^\[\]\n]+?)\]\]")
GENERATED_BODY_BLOCKS = (
    ("## Related knowledge <!-- sec: relations:generated:start -->",
     "<!-- sec: relations:generated:end -->"),
    ("## Contents <!-- sec: structural:generated:start -->",
     "<!-- sec: structural:generated:end -->"),
)
RELATION_BLOCK_RE = re.compile(
    r"\n*" + re.escape(GENERATED_BODY_BLOCKS[0][0]) + r".*?"
    + re.escape(GENERATED_BODY_BLOCKS[0][1]) + r"\s*",
    re.DOTALL,
)
INLINE_CODE_RE = re.compile(r"`[^`]*`")


def without_code(text: str) -> str:
    """Return the text scanned for links: a line starting with ```
    after indentation toggles a fence, fence and fenced lines are dropped, and
    inline code spans are removed line by line."""
    kept: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced:
            kept.append(INLINE_CODE_RE.sub("", line))
    return "\n".join(kept)


def without_generated_relations(text: str) -> str:
    """Remove only the renderer-owned inverse-relation projection."""
    start, end = GENERATED_BODY_BLOCKS[0]
    if start not in text or end not in text:
        return text
    return RELATION_BLOCK_RE.sub("\n\n", text).rstrip() + "\n"


def split_wikilink(inner: str) -> tuple[str, str, str]:
    """Split a wikilink body into (target, anchor, alias). The alias
    separator is '|' or its table-escaped form '\\|'."""
    alias = ""
    for sep in ("\\|", "|"):
        if sep in inner:
            inner, alias = inner.split(sep, 1)
            break
    target, _, anchor = inner.partition("#")
    return target.strip(), anchor.strip(), alias.strip()


def split_row_cells(raw: str) -> list[str]:
    """Split a table row on unescaped pipes only: an escaped alias pipe
    ('\\|') inside a cell wikilink stays inside its cell."""
    return re.split(r"(?<!\\)\|", raw.strip().strip("|"))


def normalize_cell(cell: str) -> str:
    """Collapse id-aliased wikilinks to their bare ids. The normalized
    form feeds the rows dict (registry, render, cited_by, statements and
    their sha256 hashes), so rewriting a bare citation cell into a
    wikilink never changes a statement hash."""
    def collapse(match: re.Match) -> str:
        _target, _anchor, alias = split_wikilink(match.group("inner"))
        if NAMESPACED_ID_RE.fullmatch(alias) or BARE_ID_RE.fullmatch(alias):
            return alias
        return match.group(0)
    return WIKILINK_RE.sub(collapse, cell).strip()
SEC_RE = re.compile(r"<!--\s*sec:\s*([a-z_]+)\s*-->")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
NAMESPACED_ID_RE = re.compile(r"\b(BR|AC|AS|OQ|DEC)-([A-Z]{2,4})-(\d{3,})\b")
BARE_ID_RE = re.compile(r"\b(BR|AC|AS|OQ|DEC)-(\d{3,})\b")


@dataclass
class Table:
    section: str | None
    header_line: int
    columns: list[str]
    rows: list[tuple[int, list[str]]]       # NORMALIZED cells
    # Cells exactly as authored. Enforcement (mint shape, bare citation
    # cells) reads the raw evidence; registry, render, cited_by and the
    # statement hashes read the normalized rows, so a cell rewrite from
    # bare id to wikilink never trips a statement-hash tripwire.
    raw_cells: list[tuple[int, list[str]]] = field(default_factory=list)


@dataclass
class Doc:
    rel: str
    abs_path: Path
    fm: dict
    fm_end: int
    doc_type: str
    node: str
    lines: list[str] = field(default_factory=list)
    fenced: set = field(default_factory=set)
    fences: list = field(default_factory=list)  # (start, end, info)
    headings: list = field(default_factory=list)  # (lineno, level, text)
    sections: dict = field(default_factory=dict)  # token -> (h_line, start, end)
    tables: list = field(default_factory=list)
    summary_lines: int = 0
    links: list = field(default_factory=list)  # (lineno, text, target)
    wikilinks: list = field(default_factory=list)  # (lineno, embed, target, anchor, alias)


# YAML indicators that cannot open a plain scalar.
PLAIN_SCALAR_UNSAFE_START = frozenset("[]{},#&*!|>'\"%@`")
# Characters JSON leaves unescaped that a note line cannot hold as written:
# str.splitlines() ends a line at the last three, and a YAML plain scalar
# cannot hold DEL.
JSON_UNESCAPED_BREAKS = "\x7f\x85\u2028\u2029"


def frontmatter_value(token: str) -> str:
    """Decode one stripped frontmatter scalar token.

    A token that parses as exactly one JSON string is a YAML double-quoted
    scalar, the form frontmatter_scalar writes, and decodes to its value.
    Every other token keeps the surrounding-quote strip this parser has
    always applied, so a note without such a token reads as before.
    """
    if len(token) >= 2 and token[0] == token[-1] == '"':
        try:
            value = json.loads(token)
            value.encode("utf-8")
        except ValueError:
            pass
        else:
            return value
    return token.strip("\"'")


def frontmatter_scalar(value: object) -> str:
    """Render the value of one `key: value` line so frontmatter_value reads
    it back as-is.

    Text stays plain only when this parser and a YAML reader both keep it
    unchanged. Other text becomes one JSON string, which is also a YAML
    double-quoted scalar. An empty string stays the empty value every writer
    has always rendered, which parse_frontmatter reads as an empty list.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if not text or (
            text == text.strip()
            and text[0] not in PLAIN_SCALAR_UNSAFE_START
            and not re.match(r"[-?:](?:\s|$)", text)
            and text[-1] not in "\"':"
            and not re.search(r":\s|\s#", text)
            and text.lower() not in {"true", "false", "null", "~"}
            and not any((char < " " and char != "\t") or char in JSON_UNESCAPED_BREAKS
                        for char in text)):
        return text
    return quoted_scalar(text)


def frontmatter_item(value: object) -> str:
    """Render one `- item` line's value; an empty string stays an item."""
    return frontmatter_scalar(value) or '""'


def quoted_scalar(text: str) -> str:
    """Render text as one JSON string that stays on its note line."""
    encoded = json.dumps(text, ensure_ascii=False)
    for char in JSON_UNESCAPED_BREAKS:
        encoded = encoded.replace(char, f"\\u{ord(char):04x}")
    return encoded


def parse_frontmatter(text: str) -> tuple[dict, int, str | None]:
    """Minimal YAML subset: scalar `key: value` and lists of `- item` lines.
    Returns (frontmatter, body start line 1-based, error or None)."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, 1, "missing frontmatter block"
    fm: dict = {}
    current_list: str | None = None
    for i in range(1, len(lines)):
        raw = lines[i]
        stripped = raw.strip()
        if stripped == "---":
            return fm, i + 2, None
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and current_list:
            fm[current_list].append(frontmatter_value(stripped[2:].strip()))
            continue
        if ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = frontmatter_value(value.strip())
            if value == "":
                fm[key] = []
                current_list = key
            else:
                current_list = None
                if value in ("true", "false"):
                    fm[key] = value == "true"
                elif value.isdigit():
                    fm[key] = int(value)
                else:
                    fm[key] = value
            continue
        return fm, i + 1, f"unparseable frontmatter line: {stripped[:40]}"
    return fm, 1, "unterminated frontmatter block"


def parse_doc_body(doc: Doc) -> None:
    """Fill headings, sections, summary, tables and links from doc.lines."""
    in_fence = False
    fence_start = 0
    fence_info = ""
    for idx, line in enumerate(doc.lines, start=1):
        if line.lstrip().startswith("```"):
            if not in_fence:
                fence_start = idx
                fence_info = line.lstrip()[3:].strip().lower()
            else:
                doc.fences.append((fence_start, idx, fence_info))
            in_fence = not in_fence
            doc.fenced.add(idx)
            continue
        if in_fence:
            doc.fenced.add(idx)

    h1_line = 0
    first_h2 = 0
    for idx, line in enumerate(doc.lines, start=1):
        if idx < doc.fm_end or idx in doc.fenced:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            doc.headings.append((idx, level, m.group(2).strip()))
            if level == 1 and not h1_line:
                h1_line = idx
            if level == 2 and not first_h2:
                first_h2 = idx

    if h1_line:
        end = first_h2 if first_h2 else len(doc.lines) + 1
        doc.summary_lines = sum(
            1 for i in range(h1_line + 1, end)
            if doc.lines[i - 1].strip() and i not in doc.fenced
        )

    h2s = [(idx, text) for idx, level, text in doc.headings if level == 2]
    for pos, (idx, text) in enumerate(h2s):
        m = SEC_RE.search(text)
        token = m.group(1) if m else None
        end = h2s[pos + 1][0] if pos + 1 < len(h2s) else len(doc.lines) + 1
        if token and token not in doc.sections:
            doc.sections[token] = (idx, idx + 1, end)

    def section_of(lineno: int) -> str | None:
        for token, (_h, start, end) in doc.sections.items():
            if start <= lineno < end:
                return token
        return None

    idx = doc.fm_end
    total = len(doc.lines)
    while idx <= total:
        line = doc.lines[idx - 1]
        if idx not in doc.fenced and line.strip().startswith("|"):
            block_start = idx
            block = []
            while idx <= total and idx not in doc.fenced \
                    and doc.lines[idx - 1].strip().startswith("|"):
                block.append((idx, doc.lines[idx - 1]))
                idx += 1
            if len(block) >= 2 and set(block[1][1].replace("|", "").strip()) <= set("-: "):
                columns = [c.strip().lower() for c in block[0][1].strip().strip("|").split("|")]
                rows = []
                raw_cells = []
                for lineno, raw in block[2:]:
                    cells = [c.strip() for c in split_row_cells(raw)]
                    raw_cells.append((lineno, cells))
                    rows.append((lineno, [normalize_cell(c) for c in cells]))
                doc.tables.append(Table(section_of(block_start), block[0][0],
                                        columns, rows, raw_cells))
            continue
        idx += 1

    for idx, line in enumerate(doc.lines, start=1):
        if idx < doc.fm_end or idx in doc.fenced:
            continue
        scrubbed = INLINE_CODE_RE.sub(" ", line)
        for m in LINK_RE.finditer(scrubbed):
            doc.links.append((idx, m.group(1), m.group(2)))
        for m in WIKILINK_RE.finditer(scrubbed):
            target, anchor, alias = split_wikilink(m.group("inner"))
            doc.wikilinks.append(
                (idx, m.group("embed") == "!", target, anchor, alias))


