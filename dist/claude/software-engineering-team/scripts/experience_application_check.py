#!/usr/bin/env python3
"""Validate the one canonical Experience Design application artifact.

The artifact is deliberately constrained: authors own semantic HTML, CSS and
one JSON graph; the only executable code is the shipped, checksum-verified
runtime.  Process packages publish exact-ref route maps and never contain a
second visual implementation.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import unicodedata
import zlib
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from pathlib import Path

import design_system_compile
import stage_package


APPLICATION_RELATIVE = Path("artifacts/application.html")
MAP_RELATIVE = Path("artifacts/application-map.json")
REGISTRY_RELATIVE = Path("_generated/application-registry.json")
LEDGER_RELATIVE = Path("_ledger/application-revisions.json")
DATA_DIR = (
    Path(__file__).resolve().parents[1]
    / "skill-content/experience-modeling/data"
)
TEMPLATE = DATA_DIR / "application-template.html"
MAP_SCHEMA_PATH = DATA_DIR / "application-map-schema.json"
CONTRACT_SCHEMA_PATH = DATA_DIR / "application-contract-schema.json"
MAX_JSON_TEXT_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 100_000


def _strict_json_object(pairs: list[tuple[str, object]]) -> dict:
    value: dict = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON field: {key}")
        value[key] = item
    return value


def _json_contains_non_scalar(value: object) -> bool:
    stack: list[tuple[object, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if depth > MAX_JSON_DEPTH or nodes > MAX_JSON_NODES:
            raise ValueError("JSON exceeds the canonical depth or node limit")
        if type(item) is str:
            if any(0xD800 <= ord(character) <= 0xDFFF for character in item):
                return True
        elif type(item) is list:
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is dict:
            for key, child in item.items():
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
    return False


def _bounded_json_text(raw: str) -> None:
    if len(raw.encode("utf-8", "surrogatepass")) > MAX_JSON_TEXT_BYTES:
        raise ValueError("JSON exceeds the canonical byte limit")
    depth = 0
    in_string = False
    escaped = False
    for character in raw:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("JSON exceeds the canonical nesting depth")
        elif character in "]}":
            depth -= 1


def strict_json_loads(raw: str) -> object:
    """Decode duplicate-free JSON whose strings are Unicode scalar sequences."""
    _bounded_json_text(raw)
    try:
        value = json.loads(raw, object_pairs_hook=_strict_json_object)
    except RecursionError as exc:
        raise ValueError("JSON exceeds the canonical nesting depth") from exc
    if _json_contains_non_scalar(value):
        raise ValueError("JSON strings must contain only Unicode scalar values")
    return value


def schema(path: Path) -> dict:
    value = strict_json_loads(path.read_text(encoding="utf-8"))
    if type(value) is not dict:
        raise ValueError(f"{path.name} must contain one JSON object")
    return value


MAP_SCHEMA = schema(MAP_SCHEMA_PATH)
CONTRACT_SCHEMA = schema(CONTRACT_SCHEMA_PATH)
EXACT = re.compile(str(MAP_SCHEMA["record_ref_pattern"]))
ROUTE = re.compile(str(MAP_SCHEMA["route_pattern"]))
CONTRACT_EXACT = re.compile(str(CONTRACT_SCHEMA["record_ref_pattern"]))
CONTRACT_ROUTE = re.compile(str(CONTRACT_SCHEMA["route_pattern"]))
SIMULATION_ID = re.compile(str(CONTRACT_SCHEMA["simulation_id_pattern"]))
OUTCOME = re.compile(str(CONTRACT_SCHEMA["outcome_pattern"]))
HASH = re.compile(r"^sha256:[0-9a-f]{64}$")
HTML_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
GENESIS_APPLICATION_HASH = "sha256:" + "0" * 64
WEB_SUFFIXES = {
    ".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".jsx", ".tsx",
    ".vue", ".svelte", ".wasm",
}
REQUIRED_STATE_CLASSES = set(map(str, CONTRACT_SCHEMA["state_classes"]))
MAP_STATE_CLASSES = set(map(str, MAP_SCHEMA["state_classes"]))
ALLOWED_APPLICATION_ACTIONS = {
    "toggle-theme", "toggle-privacy", "toggle-menu", "open-modal", "close-modal",
    "toggle-drawer", "toggle-pressed", "select-option", "return-route",
}
MACHINE_META = {
    "experience-application-status",
    "experience-application-revision",
    "experience-application-proposal-hash",
    "experience-application-source-hash",
    "experience-application-package-set-hash",
    "experience-application-coverage-hash",
    "experience-application-hash",
    "experience-application-approved-at-utc",
    "design-system-package-hash",
    "design-system-master-revision",
    "design-system-master-source-hash",
}
META_PATTERN = re.compile(
    r'(<meta\s+name=["\'](?P<name>[^"\']+)["\']\s+content=["\'])'
    r'(?P<value>[^"\']*)(["\'])',
    re.I,
)
TOKEN_PATTERN = re.compile(
    r"/\* application:design-tokens:start \*/\s*(.*?)\s*"
    r"/\* application:design-tokens:end \*/",
    re.S,
)
AUTHOR_STYLE_PATTERN = re.compile(
    r"/\* application:author-styles:start \*/\s*(.*?)\s*"
    r"/\* application:author-styles:end \*/",
    re.S,
)
STYLE_PATTERN = re.compile(
    r"<style(?P<attrs>[^>]*)>(?P<body>.*?)"
    r"</style(?:[\t\n\f\r />][^<>]*)?>",
    re.I | re.S,
)
SCRIPT_PATTERN = re.compile(
    r"<script(?P<attrs>[^>]*)>(?P<body>.*?)"
    r"</script(?:[\t\n\f\r />][^<>]*)?>",
    re.I | re.S,
)
CSS_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})(?:\s)?|([^\r\n]))")
INVALID_CSS_MARKER = "\u0000application-invalid-css\u0000"
ASCII_TAG_NAME = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:-"
)
CSP_PREFIX = "default-src 'none'; base-uri 'none'; connect-src 'none'; " \
    "form-action 'none'; font-src data:; img-src data:; " \
    "object-src 'none'; script-src 'sha256-"
CSP_SUFFIX = "'; style-src 'unsafe-inline'"
TOKEN_FREE_AUTHOR_PROPERTIES = {
    "align-content", "align-items", "align-self", "display",
    "flex-direction", "flex-wrap", "hyphens", "isolation",
    "justify-content", "justify-items", "justify-self", "object-fit",
    "overflow", "overflow-x", "overflow-y", "table-layout",
    "text-align", "text-overflow", "text-transform", "white-space",
    "word-break", "word-wrap",
}
SAFE_AUTHOR_DISPLAY_VALUES = {
    "block", "flex", "flow-root", "inline-block", "inline-flex",
}
RUNTIME_OWNED_AUTHOR_PROPERTIES = {
    "all", "appearance", "box-shadow", "box-sizing", "clip", "clip-path", "color-scheme",
    "content", "content-visibility", "counter-increment", "counter-reset",
    "counter-set", "cursor", "direction", "filter", "forced-color-adjust", "mask",
    "grid-auto-flow", "mask-image", "opacity", "overflow-wrap",
    "block-size", "flex-basis", "height", "inline-size", "margin",
    "margin-inline", "margin-inline-end", "margin-inline-start",
    "margin-left", "margin-right", "max-block-size",
    "max-height", "max-inline-size", "max-width", "min-block-size",
    "min-height", "min-inline-size", "min-width", "outline", "outline-color",
    "outline-offset", "outline-style",
    "outline-width", "pointer-events", "position", "touch-action",
    "quotes", "transform", "translate", "scale", "rotate", "unicode-bidi", "user-select",
    "visibility", "width",
}
EXACT_TOKEN_AUTHOR_PROPERTIES = {
    "block-size", "bottom", "column-gap", "flex-basis", "font-size", "gap",
    "height", "inline-size", "inset", "inset-block", "inset-block-end",
    "inset-block-start", "inset-inline", "inset-inline-end",
    "inset-inline-start", "left", "line-height", "margin", "margin-block",
    "margin-block-end", "margin-block-start", "margin-bottom", "margin-inline",
    "margin-inline-end", "margin-inline-start", "margin-left", "margin-right",
    "margin-top", "max-block-size", "max-height", "max-inline-size",
    "max-width", "min-block-size", "min-height", "min-inline-size",
    "min-width", "padding", "padding-block", "padding-block-end",
    "padding-block-start", "padding-bottom", "padding-inline",
    "padding-inline-end", "padding-inline-start", "padding-left",
    "padding-right", "padding-top", "right", "row-gap", "top", "width",
}
SCHEMA_PRIMITIVE_TYPES = {"array", "boolean", "integer", "object", "string"}
VOID_ELEMENTS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
}
SAFE_ROUTE_ROOT_TAGS = {"article", "div", "section"}
SAFE_DYNAMIC_CONTAINER_TAGS = {
    "article", "aside", "div", "nav", "ol", "section", "ul",
}
SAFE_COLLECTION_ITEM_TAGS = {"article", "aside", "div", "p", "section"}
SAFE_VISIBLE_LABEL_TARGET_TAGS = {
    "abbr", "b", "cite", "code", "div", "em", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6", "i", "kbd", "legend",
    "mark", "p", "q", "s", "samp", "small", "span", "strong",
    "sub", "sup", "u", "var",
}
SAFE_PRIVATE_TARGET_TAGS = {
    "abbr", "b", "cite", "code", "div", "em", "i", "kbd", "mark",
    "p", "q", "s", "samp", "small", "span", "strong", "sub", "sup",
    "u", "var",
}
RUNTIME_MUTABLE_LABEL_ATTRIBUTES = {
    "data-filter-item", "data-private", "data-search-item",
}
HEAD_ALLOWED_CHILDREN = {
    "base", "link", "meta", "title", "noscript", "noframes", "style",
    "template", "script",
}
INTRINSICALLY_NON_RENDERED_ELEMENTS = {
    "area", "base", "col", "colgroup", "datalist", "defs", "head", "link",
    "meta", "param", "script", "source", "style", "symbol", "template",
    "title", "track",
}
FORBIDDEN_BROWSER_UNSTABLE_ELEMENTS = {
    "acronym", "applet", "area", "audio", "base", "basefont", "bgsound",
    "big", "blink", "canvas", "center", "command", "datalist", "details", "dir",
    "embed", "fencedframe", "font", "frame", "frameset", "iframe", "image",
    "isindex", "keygen", "link", "listing", "map", "marquee", "menuitem",
    "math", "multicol", "nextid", "nobr", "noembed", "noframes", "noscript",
    "object", "param", "plaintext", "portal", "rb", "rp", "rt", "rtc",
    "ruby", "spacer", "strike", "summary", "svg", "template", "tt", "video",
    "xmp",
}
ALLOWED_APPLICATION_ELEMENTS = {
    "a", "abbr", "address", "article", "aside", "b", "blockquote",
    "body", "br", "button", "cite", "code", "del", "dialog",
    "div", "em", "fieldset", "figcaption", "figure", "footer", "form",
    "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hr",
    "html", "i", "img", "input", "ins", "kbd", "label", "legend",
    "li", "main", "mark", "meta", "nav", "ol", "optgroup", "option",
    "p", "q", "s", "samp", "script", "search", "section", "select",
    "small", "span", "strong", "style", "sub", "sup", "textarea",
    "title",
    "u", "ul", "var", "wbr",
}
FORBIDDEN_PRESENTATIONAL_ATTRIBUTES = {
    "align", "background", "bgcolor", "border", "cellpadding", "cellspacing",
    "char", "charoff", "color", "cols", "compact", "face", "frame",
    "height", "hspace", "marginheight", "marginwidth", "noshade", "nowrap",
    "rows", "rules", "size", "valign", "vspace", "width",
}
RUNTIME_RESERVED_NAMED_PROPERTIES = {
    "Array", "Boolean", "Element", "JSON", "Map", "Object", "String",
    "addEventListener", "document", "documentElement", "getElementById",
    "history", "location", "querySelectorAll", "window",
}
REQUIRED_APPLICATION_ROOT_TOKENS = {
    "--catalog-accent", "--catalog-background", "--catalog-border",
    "--catalog-border-width", "--catalog-card-min-width",
    "--catalog-content-width", "--catalog-error", "--catalog-focus",
    "--catalog-focus-offset", "--catalog-focus-width", "--catalog-font-body",
    "--catalog-font-heading", "--catalog-foreground", "--catalog-gutter",
    "--catalog-header-layer", "--catalog-line-height", "--catalog-motion-easing",
    "--catalog-motion-fast", "--catalog-muted", "--catalog-radius-md",
    "--catalog-radius-sm", "--catalog-scroll-offset", "--catalog-shadow-sm",
    "--catalog-space-2xl", "--catalog-space-3xl", "--catalog-space-lg",
    "--catalog-space-md", "--catalog-space-sm", "--catalog-space-xl",
    "--catalog-space-xs", "--catalog-success", "--catalog-surface",
    "--catalog-swatch-height", "--catalog-touch-target",
    "--catalog-type-display-size", "--catalog-type-display-weight",
    "--catalog-warning",
}
REQUIRED_APPLICATION_DARK_TOKENS = {
    "--catalog-accent", "--catalog-background", "--catalog-border",
    "--catalog-error", "--catalog-focus", "--catalog-foreground",
    "--catalog-muted", "--catalog-success", "--catalog-surface",
    "--catalog-warning",
}
APPLICATION_COLOR_TOKENS = {
    "--catalog-accent", "--catalog-background", "--catalog-border",
    "--catalog-error", "--catalog-focus", "--catalog-foreground",
    "--catalog-muted", "--catalog-success", "--catalog-surface",
    "--catalog-warning",
}
APPLICATION_LENGTH_BOUNDS_REM = {
    "--catalog-border-width": (Decimal("0.0625"), Decimal("0.25")),
    "--catalog-card-min-width": (Decimal("8"), Decimal("32")),
    "--catalog-content-width": (Decimal("20"), Decimal("120")),
    "--catalog-focus-offset": (Decimal("0"), Decimal("0.5")),
    "--catalog-focus-width": (Decimal("0.0625"), Decimal("0.25")),
    "--catalog-gutter": (Decimal("0.5"), Decimal("4")),
    "--catalog-radius-md": (Decimal("0"), Decimal("2")),
    "--catalog-radius-sm": (Decimal("0"), Decimal("2")),
    "--catalog-scroll-offset": (Decimal("1"), Decimal("16")),
    "--catalog-space-xs": (Decimal("0.125"), Decimal("1")),
    "--catalog-space-sm": (Decimal("0.25"), Decimal("1.5")),
    "--catalog-space-md": (Decimal("0.5"), Decimal("2")),
    "--catalog-space-lg": (Decimal("0.75"), Decimal("3")),
    "--catalog-space-xl": (Decimal("1"), Decimal("4")),
    "--catalog-space-2xl": (Decimal("1.5"), Decimal("6")),
    "--catalog-space-3xl": (Decimal("2"), Decimal("8")),
    "--catalog-swatch-height": (Decimal("2"), Decimal("12")),
    "--catalog-touch-target": (Decimal("2.5"), Decimal("4")),
    "--catalog-type-display-size": (Decimal("1.5"), Decimal("6")),
}
ARIA_WIDGET_ROLES = {
    "button", "checkbox", "combobox", "grid", "gridcell", "link",
    "listbox", "menu", "menubar", "menuitem", "menuitemcheckbox",
    "menuitemradio", "meter", "option", "progressbar", "radio",
    "radiogroup", "scrollbar", "searchbox", "slider", "spinbutton",
    "switch", "tab", "tablist", "textbox", "tree", "treegrid", "treeitem",
}
ALLOWED_EXPLICIT_ROLES = {"listbox", "option", "status"}
PASSIVE_ARIA_ATTRIBUTES = {
    "aria-description", "aria-describedby", "aria-label", "aria-labelledby",
}
AUTHORED_VISIBLE_VALUE_INPUT_TYPES = {
    "button", "email", "search", "submit", "tel", "text", "url",
}
ROUTED_FORM_INPUT_TYPES = {
    "checkbox", "date", "datetime-local", "email", "month", "number",
    "password", "radio", "range", "search", "tel", "text", "time",
    "url", "week",
}
ROUTED_FORM_LENGTH_INPUT_TYPES = {"password", "search", "tel", "text"}
HTML_NONNEGATIVE_INTEGER = re.compile(r"^(?:0|[1-9][0-9]*)$")
HTML_FLOAT = re.compile(
    r"^-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"
)
TABLE_CHILDREN = {
    "table": {"caption", "colgroup", "thead", "tbody", "tfoot"},
    "colgroup": {"col"},
    "thead": {"tr"},
    "tbody": {"tr"},
    "tfoot": {"tr"},
    "tr": {"td", "th"},
}
TABLE_REQUIRED_PARENTS = {
    "caption": {"table"},
    "colgroup": {"table"},
    "thead": {"table"},
    "tbody": {"table"},
    "tfoot": {"table"},
    "col": {"colgroup"},
    "tr": {"thead", "tbody", "tfoot"},
    "td": {"tr"},
    "th": {"tr"},
}
PHRASING_ELEMENTS = {
    "a", "abbr", "b", "br", "button", "cite", "code", "del",
    "em", "i", "img", "input", "ins", "kbd", "label", "mark",
    "q", "s", "samp", "select", "small", "span", "strong", "sub", "sup",
    "textarea", "u", "var", "wbr",
}
PHRASING_CONTAINERS = {
    "a", "abbr", "b", "button", "cite", "code", "del",
    "em", "h1", "h2", "h3", "h4", "h5", "h6", "i", "ins", "kbd",
    "label", "legend", "mark", "p", "q", "s", "samp", "small", "span",
    "strong", "sub", "sup", "u", "var",
}
LABELABLE_ELEMENTS = {"button", "input", "select", "textarea"}
DIRECT_CHILDREN = {
    **TABLE_CHILDREN,
    "ul": {"li"},
    "ol": {"li"},
    "dl": {"dt", "dd"},
    "select": {"option", "optgroup"},
    "optgroup": {"option"},
    "option": set(),
    **{tag: PHRASING_ELEMENTS for tag in PHRASING_CONTAINERS},
}
NON_TEXT_DIRECT_PARENTS = (
    set(TABLE_CHILDREN) | {"ul", "ol", "dl", "select", "optgroup"}
)
REQUIRED_DIRECT_PARENTS = {
    **TABLE_REQUIRED_PARENTS,
    "li": {"ul", "ol"},
    "dt": {"dl"},
    "dd": {"dl"},
    "legend": {"fieldset"},
    "figcaption": {"figure"},
    "option": {"select", "optgroup"},
    "optgroup": {"select"},
    "meta": {"head"},
    "style": {"head"},
    "title": {"head"},
}
P_IMPLIED_END_STARTS = {
    "address", "article", "aside", "blockquote", "div", "dl", "fieldset",
    "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hgroup", "hr", "main", "menu", "nav", "ol", "p", "pre", "search",
    "section", "table", "ul",
}
SMIL_MUTATION_ELEMENTS = {
    "animate", "animatecolor", "animatemotion", "animatetransform", "discard",
    "set",
}
RAWTEXT_OR_RCDATA_ELEMENTS = {
    "iframe", "noembed", "noframes", "noscript", "plaintext", "script",
    "style", "textarea", "title", "xmp",
}
PRESENTATION_URL_ATTRIBUTES = {
    "clip-path", "color-profile", "cursor", "fill", "filter", "marker",
    "marker-end", "marker-mid", "marker-start", "mask", "stroke",
}
REGISTRY_KEYS = {
    "schema_version", "application_revision", "source_hash", "package_set_hash",
    "coverage_hash", "design_system", "runtime_sha256", "packages", "coverage",
    "previous_application_hash", "application_hash",
}
DESIGN_SYSTEM_RECEIPT_KEYS = {
    "package_hash", "revision", "master_source_hash",
}
PACKAGE_RECEIPT_KEYS = {"result_ref", "package_hash"}
COVERAGE_KEYS = {
    "entry_route", "routes", "transitions", "simulations", "record_refs",
    "state_classes",
}
COVERAGE_ROUTE_KEYS = {"route", "state_class", "experience_id"}
COVERAGE_TRANSITION_KEYS = {
    "source", "transition_ref", "target", "outcome", "preserve_context",
    "return_route",
}
COVERAGE_SIMULATION_KEYS = {
    "simulation_id", "source", "target", "outcome", "return_route",
}
PACKAGE_RESULT_REF = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*@r[1-9][0-9]*$"
)
REQUIRED_EXPERIENCE_MARKERS = {
    ":focus-visible": "visible keyboard focus",
    ":hover": "visible hover behavior",
    ":active": "visible pressed behavior",
    '[aria-pressed="true"]': "visible toggled-pressed behavior",
    '[aria-selected="true"]': "visible listbox selection behavior",
    "prefers-reduced-motion": "reduced-motion behavior",
    "@media": "responsive behavior",
    'data-application-action="toggle-theme"': "accessible theme control",
    'data-application-action="toggle-privacy"': "privacy masking control",
    "data-private": "privacy-masked content",
}
STYLE_EXPERIENCE_MARKERS = {
    ":focus-visible", ":hover", ":active", '[aria-pressed="true"]',
    '[aria-selected="true"]', "prefers-reduced-motion", "@media",
}


def canonical(value: object) -> bytes:
    if _json_contains_non_scalar(value):
        raise ValueError("JSON strings must contain only Unicode scalar values")
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def sha(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def has_exact_primitive_type(value: object, expected: str) -> bool:
    """Return whether a JSON value has the schema's exact primitive type.

    ``bool`` is intentionally not an integer here even though Python subclasses
    ``bool`` from ``int``.
    """
    return {
        "array": type(value) is list,
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "object": type(value) is dict,
        "string": type(value) is str,
    }.get(expected, False)


def validate_exact_field_types(
    value: dict, type_map: object, label: str, findings: list[str]
) -> bool:
    if not isinstance(type_map, dict):
        findings.append(f"{label} schema has no exact field type contract")
        return False
    valid = True
    for field, expected in type_map.items():
        if field not in value:
            continue
        if type(expected) is not str or expected not in SCHEMA_PRIMITIVE_TYPES:
            findings.append(f"{label} schema has an unsupported type for {field}")
            valid = False
        elif not has_exact_primitive_type(value[field], expected):
            findings.append(f"{label}.{field} must be an exact JSON {expected}")
            valid = False
    return valid


def _reserved_alias(name: str, expected: str) -> bool:
    return unicodedata.normalize("NFC", name).casefold() == expected.casefold()


def _regular_directory(
    path: Path, label: str, *, reserved_name: str | None = None
) -> None:
    if reserved_name is not None:
        if (
            path.name != reserved_name
            or unicodedata.normalize("NFC", path.name) != reserved_name
        ):
            raise ValueError(
                f"{label} must use exact NFC spelling and case: {reserved_name}"
            )
        if path.parent.is_dir() and not path.parent.is_symlink():
            aliases = [
                child.name for child in path.parent.iterdir()
                if _reserved_alias(child.name, reserved_name)
            ]
            if aliases != [reserved_name]:
                raise ValueError(
                    f"{label} must resolve from one exact {reserved_name} directory"
                )
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{label} must be one regular, non-symlink directory")


def root_for(value: str | Path) -> Path:
    """Resolve only after validating the lexical canonical-root identity."""
    raw = Path(value).expanduser()
    lexical = Path(os.path.abspath(os.fspath(raw)))
    name = unicodedata.normalize("NFC", lexical.name).casefold()
    chain: list[tuple[Path, str, str | None]]
    if name == "experience-design":
        candidate = lexical
        chain = [(candidate, "Experience root", "experience-design")]
        docs = candidate.parent
        if _reserved_alias(docs.name, "docs"):
            chain.insert(0, (docs, "Experience owner", "docs"))
            workspace = docs.parent
            if _reserved_alias(workspace.name, "workspace"):
                chain.insert(0, (workspace, "Experience workspace", "workspace"))
    elif name == "docs":
        candidate = lexical / "experience-design"
        chain = [
            (lexical, "Experience owner", "docs"),
            (candidate, "Experience root", "experience-design"),
        ]
        workspace = lexical.parent
        if _reserved_alias(workspace.name, "workspace"):
            chain.insert(0, (workspace, "Experience workspace", "workspace"))
    elif name == "workspace":
        candidate = lexical / "docs" / "experience-design"
        chain = [
            (lexical, "Experience workspace", "workspace"),
            (lexical / "docs", "Experience owner", "docs"),
            (candidate, "Experience root", "experience-design"),
        ]
    else:
        _regular_directory(lexical, "project root selector")
        workspace = lexical / "workspace"
        direct = lexical / "experience-design"
        if workspace.exists() or workspace.is_symlink():
            candidate = workspace / "docs" / "experience-design"
            chain = [
                (workspace, "Experience workspace", "workspace"),
                (workspace / "docs", "Experience owner", "docs"),
                (candidate, "Experience root", "experience-design"),
            ]
        elif direct.exists() or direct.is_symlink():
            candidate = direct
            chain = [(candidate, "Experience root", "experience-design")]
        else:
            raise ValueError("--root must identify workspace/docs/experience-design")
    for path, label, reserved_name in chain:
        _regular_directory(path, label, reserved_name=reserved_name)
    return candidate.resolve()


def closed_artifact_surface_findings(
    root: Path, packages: list[Path]
) -> list[str]:
    """Enforce the hard-cut file and one-file artifact surfaces."""
    findings: list[str] = []
    scopes = [
        (root, "application.html", "Experience root"),
        *((package, "application-map.json", f"experiences/{package.name}")
          for package in packages),
    ]
    reserved = unicodedata.normalize("NFC", "artifacts").casefold()
    for owner, allowed_name, label in scopes:
        if owner.is_dir() and not owner.is_symlink():
            try:
                aliases = [
                    child for child in owner.iterdir()
                    if unicodedata.normalize("NFC", child.name).casefold() == reserved
                    and child.name != "artifacts"
                ]
            except OSError as exc:
                findings.append(f"{label}: cannot inspect artifact surface: {exc}")
                aliases = []
            for alias in aliases:
                findings.append(
                    f"{label}/{alias.name}: reserved artifacts directory must use exact NFC spelling and case"
                )
        artifacts = owner / "artifacts"
        if artifacts.is_symlink() or (artifacts.exists() and not artifacts.is_dir()):
            findings.append(f"{label}/artifacts: artifact surface must be one regular directory")
            continue
        if not artifacts.is_dir():
            continue
        try:
            entries = sorted(artifacts.rglob("*"))
        except OSError as exc:
            findings.append(f"{label}/artifacts: cannot inspect artifact surface: {exc}")
            continue
        for entry in entries:
            relative = entry.relative_to(artifacts)
            if (
                relative != Path(allowed_name)
                or not entry.is_file()
                or entry.is_symlink()
            ):
                findings.append(
                    f"{label}/artifacts/{relative.as_posix()}: closed artifact surface permits only {allowed_name}"
                )
    root_files = {
        APPLICATION_RELATIVE,
        REGISTRY_RELATIVE,
        LEDGER_RELATIVE,
        Path("_generated/open-application-revision.json"),
    }
    package_files = {
        Path("experience.md"),
        MAP_RELATIVE,
        Path("_generated/coverage.json"),
        Path("_generated/registry.json"),
        Path("_generated/open-revision.json"),
        Path("_ledger/package-revisions.json"),
        Path("_ledger/aliases.json"),
    }
    authored_suffixes = {
        "journeys": "-journey.md",
        "flows": "-flow-set.md",
        "screens": "-screen.md",
        "states": "-state.md",
        "transitions": "-transition.md",
    }
    snapshot_path = re.compile(
        r"^_ledger/records/(?:JRN|FLW|SCR|STA|TRN)-[0-9]{3,}/"
        r"r[1-9][0-9]*\.json$"
    )

    def allowed_package_file(relative: Path) -> bool:
        if relative in package_files:
            return True
        parts = relative.parts
        if len(parts) == 2:
            suffix = authored_suffixes.get(parts[0])
            if suffix and parts[1].endswith(suffix) \
                    and parts[1] != suffix:
                return True
        return bool(snapshot_path.fullmatch(relative.as_posix()))

    if root.is_dir() and not root.is_symlink():
        for path in sorted(root.rglob("*")):
            if not path.is_file() and not path.is_symlink():
                continue
            relative = path.relative_to(root)
            allowed = relative in root_files
            parts = relative.parts
            if len(parts) >= 3 and parts[0] == "experiences":
                allowed = allowed_package_file(Path(*parts[2:]))
            if not allowed:
                findings.append(
                    f"{relative.as_posix()}: Experience Design closed file surface does not permit this path"
                )
    return findings


def template_text() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def script_attribute(attrs: str, name: str) -> str:
    match = re.search(
        rf'\b{re.escape(name)}\s*=\s*["\']([^"\']*)["\']', attrs, re.I
    )
    return match.group(1) if match else ""


def template_runtime() -> str:
    matches = [
        match.group("body")
        for match in SCRIPT_PATTERN.finditer(template_text())
        if script_attribute(match.group("attrs"), "id")
        == "experience-application-runtime"
    ]
    if len(matches) != 1:
        raise ValueError("application template must contain one fixed runtime")
    return matches[0]


def runtime_sha256() -> str:
    return sha(template_runtime().encode())


def runtime_csp_sha256() -> str:
    digest = hashlib.sha256(template_runtime().encode()).digest()
    return base64.b64encode(digest).decode("ascii")


def expected_csp() -> str:
    return CSP_PREFIX + runtime_csp_sha256() + CSP_SUFFIX


def static_png_document(payload: bytes) -> bool:
    """Validate one bounded, non-interlaced, non-animated PNG image."""
    if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset = 8
    chunk_index = 0
    width = height = bit_depth = color_type = 0
    seen_idat = False
    seen_iend = False
    idat_ended = False
    idat_parts: list[bytes] = []
    while offset + 12 <= len(payload):
        length = int.from_bytes(payload[offset:offset + 4], "big")
        chunk_type = payload[offset + 4:offset + 8]
        chunk_end = offset + 12 + length
        if chunk_end > len(payload):
            return False
        chunk_data = payload[offset + 8:offset + 8 + length]
        expected_crc = int.from_bytes(
            payload[offset + 8 + length:chunk_end], "big"
        )
        if expected_crc != zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF:
            return False
        if chunk_type not in {b"IHDR", b"IDAT", b"IEND"}:
            return False
        if chunk_index == 0:
            if chunk_type != b"IHDR" or length != 13:
                return False
            width = int.from_bytes(chunk_data[0:4], "big")
            height = int.from_bytes(chunk_data[4:8], "big")
            bit_depth = chunk_data[8]
            color_type = chunk_data[9]
            valid_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                4: {8, 16},
                6: {8, 16},
            }
            if (
                not width
                or not height
                or width > 8192
                or height > 8192
                or width * height > 8_388_608
                or bit_depth not in valid_depths.get(color_type, set())
                or chunk_data[10:] != b"\x00\x00\x00"
            ):
                return False
        elif chunk_type == b"IHDR":
            return False
        if chunk_type == b"IDAT":
            if idat_ended:
                return False
            seen_idat = True
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            if length or not seen_idat or chunk_end != len(payload):
                return False
            seen_iend = True
            offset = chunk_end
            break
        elif seen_idat:
            idat_ended = True
        chunk_index += 1
        offset = chunk_end
    if offset != len(payload) or not seen_iend:
        return False
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    row_bytes = (width * channels * bit_depth + 7) // 8
    decoded_size = height * (row_bytes + 1)
    if decoded_size > 64 * 1024 * 1024:
        return False
    try:
        decoder = zlib.decompressobj()
        decoded = decoder.decompress(b"".join(idat_parts), decoded_size + 1)
        if len(decoded) > decoded_size or decoder.unconsumed_tail:
            return False
        decoded += decoder.flush()
    except zlib.error:
        return False
    return (
        len(decoded) == decoded_size
        and decoder.eof
        and not decoder.unused_data
        and not decoder.unconsumed_tail
        and all(decoded[row * (row_bytes + 1)] <= 4 for row in range(height))
    )


def static_image_data_url(value: str) -> bool:
    """Allow only one closed, base64, fully validated static PNG document."""
    match = re.fullmatch(
        r"data:image/png;base64,([A-Za-z0-9+/]+={0,2})",
        value,
    )
    if match is None or len(match.group(1)) > 8 * 1024 * 1024:
        return False
    try:
        payload = base64.b64decode(match.group(1), validate=True)
    except ValueError:
        return False
    return static_png_document(payload)


def allowed_application_target(attribute: str, target: str) -> bool:
    return (
        attribute in {"href", "usemap", "xlink:href"}
        and target.startswith("#")
    ) or (
        attribute in {"src", "poster"}
        and static_image_data_url(target)
    )


def strip_css_comments(value: str) -> tuple[str, bool]:
    """Remove real CSS comments without treating comment text in strings as syntax."""
    output: list[str] = []
    index = 0
    quote = ""
    while index < len(value):
        character = value[index]
        if quote:
            output.append(character)
            if character == "\\":
                index += 1
                if index >= len(value):
                    return "".join(output), False
                output.append(value[index])
            elif character == quote:
                quote = ""
            elif character in {"\n", "\r", "\f"}:
                return "".join(output), False
            index += 1
            continue
        if character in {'"', "'"}:
            quote = character
            output.append(character)
            index += 1
            continue
        if character == "/" and index + 1 < len(value) \
                and value[index + 1] == "*":
            comment_end = value.find("*/", index + 2)
            if comment_end < 0:
                return "".join(output), False
            output.append(" ")
            index = comment_end + 2
            continue
        output.append(character)
        index += 1
    return "".join(output), not quote


def forbidden_css_codepoint(character: str) -> bool:
    """Reject code points browsers do not tokenize as CSS whitespace."""
    return (
        character not in {" ", "\t", "\n", "\r", "\f"}
        and unicodedata.category(character)[0] in {"C", "Z"}
    )


def normalized_css(value: str) -> str:
    without_comments, valid = strip_css_comments(value)
    if not valid:
        return INVALID_CSS_MARKER + without_comments

    def decode(match: re.Match) -> str:
        if match.group(1):
            try:
                codepoint = int(match.group(1), 16)
                return chr(codepoint) if codepoint else "\ufffd"
            except (ValueError, OverflowError):
                return "\ufffd"
        return match.group(2) or ""

    decoded = CSS_ESCAPE.sub(decode, without_comments)
    if any(forbidden_css_codepoint(character) for character in decoded):
        return INVALID_CSS_MARKER + decoded
    return decoded


def split_ascii_space_tokens(value: str) -> list[str]:
    """Split a platform token list using only ASCII whitespace."""
    stripped = value.strip(" \t\n\r\f")
    if not stripped:
        return []
    return re.split(r"[ \t\n\r\f]+", stripped)


def parse_ascii_idrefs(value: str) -> list[str] | None:
    """Parse an ARIA IDREF list with only the platform's ASCII whitespace."""
    identifiers = split_ascii_space_tokens(value)
    if not identifiers:
        return None
    if any(not HTML_ID.fullmatch(identifier) for identifier in identifiers):
        return None
    return identifiers


def authored_visible_value(tag: str, attrs: dict[str, str]) -> str:
    if tag != "input":
        return ""
    input_type = attrs.get("type", "text").casefold()
    if input_type == "image":
        return ""
    if input_type in AUTHORED_VISIBLE_VALUE_INPUT_TYPES:
        return attrs.get("value", "")
    return ""


def has_visible_content(value: object) -> bool:
    """Reject strings made only of whitespace or invisible Unicode controls."""
    text = str(value)
    return not any(forbidden_label_codepoint(character) for character in text) and any(
        unicodedata.category(character)[0] in {"L", "N", "P", "S"}
        for character in text
    )


def normalize_ascii_whitespace(value: object) -> str:
    """Collapse only whitespace that HTML/ARIA/CSS define as separators."""
    return re.sub(r"[ \t\n\r\f]+", " ", str(value)).strip(" \t\n\r\f")


def normalized_accessible_label(value: str) -> str:
    """Return the identity used to distinguish route announcements."""
    if has_forbidden_label_codepoint(value):
        return ""
    return unicodedata.normalize(
        "NFKC", normalize_ascii_whitespace(value)
    ).casefold()


def forbidden_label_codepoint(character: str) -> bool:
    """Reject invisible/control code points that can spoof an exact label."""
    codepoint = ord(character)
    if character in {" ", "\t", "\n", "\r", "\f"}:
        return False
    default_ignorable_or_blank = (
        (0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C),
        (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F),
        (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F),
        (0x2800, 0x2800), (0x3164, 0x3164), (0xFE00, 0xFE0F),
        (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8),
        (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A),
        (0xE0000, 0xE0FFF),
    )
    return (
        unicodedata.category(character).startswith("C")
        or unicodedata.category(character).startswith("Z")
        or any(start <= codepoint <= end for start, end in default_ignorable_or_blank)
    )


def has_forbidden_label_codepoint(value: object) -> bool:
    return any(forbidden_label_codepoint(character) for character in str(value))


def author_token_roles_are_compatible(
    property_name: str, references: list[str],
) -> bool:
    """Keep every Design System token in its mechanically approved CSS role."""
    suffixes = {
        reference.removeprefix("--catalog-") for reference in references
    }
    foreground = {
        "foreground", "muted", "accent", "success", "warning", "error",
    }
    surfaces = {"background", "surface"}
    boundaries = {
        "border", "focus", "muted", "accent", "success", "warning",
        "error", "foreground",
    }
    if property_name in {"background", "background-color"}:
        return suffixes <= surfaces
    if property_name.startswith("border") and property_name.endswith("color"):
        return suffixes <= boundaries
    if property_name == "column-rule-color":
        return suffixes <= boundaries
    if property_name in {"fill", "stroke"}:
        return suffixes <= foreground | boundaries
    if property_name == "accent-color":
        return suffixes <= {
            "accent", "focus", "success", "warning", "error",
        }
    if property_name == "color" or property_name.endswith("-color"):
        return suffixes <= foreground
    if property_name == "font-family":
        return suffixes <= {"font-body", "font-heading"}
    if property_name == "font-weight":
        return all(
            suffix.startswith("type-") and suffix.endswith("-weight")
            for suffix in suffixes
        )
    if property_name == "letter-spacing":
        return all(
            suffix == "letter-spacing"
            or suffix.startswith("type-") and suffix.endswith("-letter-spacing")
            for suffix in suffixes
        )
    if property_name == "word-spacing":
        return suffixes <= {"word-spacing"}
    if property_name == "text-indent":
        return suffixes <= {"text-indent"}
    if property_name == "text-decoration-thickness":
        return suffixes <= {"text-decoration-thickness"}
    if property_name == "border-radius" or property_name.endswith("-radius"):
        return bool(suffixes) and all(
            suffix.startswith("radius-") for suffix in suffixes
        )
    if (
        property_name == "border-width"
        or property_name.startswith("border-") and property_name.endswith("-width")
        or property_name in {"column-rule-width", "stroke-width"}
    ):
        return suffixes <= {"border-width"}
    if property_name in {
        "animation-delay", "animation-duration", "transition-delay",
        "transition-duration",
    }:
        return bool(suffixes) and all(
            suffix.startswith("motion-") and suffix != "motion-easing"
            for suffix in suffixes
        )
    if property_name in {
        "animation-timing-function", "transition-timing-function",
    }:
        return suffixes <= {"motion-easing"}
    if property_name == "z-index":
        return suffixes <= {"header-layer"}
    if property_name.startswith("scroll-margin") \
            or property_name.startswith("scroll-padding"):
        return suffixes <= {"scroll-offset"}
    return False


def token_free_author_value_is_valid(property_name: str, value: str) -> bool:
    """Validate the closed, flow-preserving literal CSS value grammar."""
    normalized = " ".join(value.casefold().split())
    css_wide = {"inherit", "initial", "revert", "revert-layer", "unset"}
    if property_name == "display":
        return normalized in SAFE_AUTHOR_DISPLAY_VALUES
    if property_name == "white-space":
        return normalized == "normal"
    if property_name == "flex-wrap":
        return normalized == "wrap"
    if normalized in css_wide:
        return True
    if property_name == "overflow":
        parts = normalized.split()
        return len(parts) in {1, 2} and all(
            part in {"auto", "scroll", "visible"} for part in parts
        )
    if property_name in {"overflow-x", "overflow-y"}:
        return normalized in {"auto", "scroll", "visible"}
    exact_values = {
        "align-content": {
            "baseline", "center", "end", "first baseline", "flex-end",
            "flex-start", "last baseline", "normal", "space-around",
            "space-between", "space-evenly", "start", "stretch",
        },
        "align-items": {
            "baseline", "center", "end", "first baseline", "flex-end",
            "flex-start", "last baseline", "normal", "self-end",
            "self-start", "start", "stretch",
        },
        "align-self": {
            "auto", "baseline", "center", "end", "first baseline",
            "flex-end", "flex-start", "last baseline", "normal",
            "self-end", "self-start", "start", "stretch",
        },
        "flex-direction": {"column", "row"},
        "hyphens": {"auto", "manual", "none"},
        "isolation": {"auto", "isolate"},
        "justify-content": {
            "center", "end", "flex-end", "flex-start", "normal",
            "space-around", "space-between", "space-evenly", "start",
            "stretch",
        },
        "justify-items": {
            "center", "end", "legacy", "normal", "self-end", "self-start",
            "start", "stretch",
        },
        "justify-self": {
            "auto", "center", "end", "normal", "self-end", "self-start",
            "start", "stretch",
        },
        "object-fit": {"contain", "cover", "fill", "none", "scale-down"},
        "table-layout": {"auto", "fixed"},
        "text-align": {"center", "end", "justify", "left", "right", "start"},
        "text-overflow": {"clip", "ellipsis"},
        "text-transform": {"none"},
        "white-space": {"normal"},
        "word-break": {"break-all", "keep-all", "normal"},
        "word-wrap": {"break-word", "normal"},
    }
    return normalized in exact_values.get(property_name, set())


def css_top_level_rules(value: str) -> tuple[list[tuple[str, str]], bool]:
    """Parse one CSS level while preserving quoted and escaped delimiters."""
    rules: list[tuple[str, str]] = []
    quote = ""
    escaped = False
    parentheses = 0
    brackets = 0
    statement_start = 0
    index = 0
    while index < len(value):
        character = value[index]
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
            index += 1
            continue
        if escaped:
            escaped = False
            index += 1
            continue
        if character == "\\":
            escaped = True
            index += 1
            continue
        if character in {'"', "'"}:
            quote = character
            index += 1
            continue
        if character == "(":
            parentheses += 1
        elif character == ")":
            if not parentheses:
                return rules, False
            parentheses -= 1
        elif character == "[":
            brackets += 1
        elif character == "]":
            if not brackets:
                return rules, False
            brackets -= 1
        elif character == ";" and not parentheses and not brackets:
            statement_start = index + 1
        elif character == "}" and not parentheses and not brackets:
            return rules, False
        elif character == "{" and not parentheses and not brackets:
            prelude = value[statement_start:index].strip()
            if not prelude:
                return rules, False
            body_start = index + 1
            depth = 1
            inner_quote = ""
            inner_escaped = False
            index += 1
            while index < len(value) and depth:
                inner_character = value[index]
                if inner_quote:
                    if inner_escaped:
                        inner_escaped = False
                    elif inner_character == "\\":
                        inner_escaped = True
                    elif inner_character == inner_quote:
                        inner_quote = ""
                elif inner_escaped:
                    inner_escaped = False
                elif inner_character == "\\":
                    inner_escaped = True
                elif inner_character in {'"', "'"}:
                    inner_quote = inner_character
                elif inner_character == "{":
                    depth += 1
                elif inner_character == "}":
                    depth -= 1
                index += 1
            if depth or inner_quote:
                return rules, False
            rules.append((prelude, value[body_start:index - 1]))
            statement_start = index
            continue
        index += 1
    return rules, not quote and not escaped and not parentheses and not brackets


def split_css_selector_list(value: str) -> tuple[list[str], bool]:
    """Split and normalize selectors without splitting functional selectors."""
    selectors: list[str] = []
    start = 0
    quote = ""
    escaped = False
    parentheses = 0
    brackets = 0
    for index, character in enumerate(value):
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
            continue
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
        elif character in {'"', "'"}:
            quote = character
        elif character == "(":
            parentheses += 1
        elif character == ")":
            if not parentheses:
                return selectors, False
            parentheses -= 1
        elif character == "[":
            brackets += 1
        elif character == "]":
            if not brackets:
                return selectors, False
            brackets -= 1
        elif character == "," and not parentheses and not brackets:
            selectors.append(value[start:index])
            start = index + 1
    selectors.append(value[start:])
    normalized_selectors: list[str] = []
    for selector in selectors:
        normalized = normalized_css(selector).strip()
        if INVALID_CSS_MARKER in normalized or not normalized:
            return normalized_selectors, False
        output: list[str] = []
        quote = ""
        escaped = False
        pending_space = False
        for character in normalized:
            if quote:
                output.append(character)
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = ""
                continue
            if character in {'"', "'"}:
                if pending_space and output and output[-1] not in {">", "+", "~"}:
                    output.append(" ")
                pending_space = False
                quote = character
                output.append(character)
            elif character.isspace():
                pending_space = True
            else:
                if (
                    pending_space and output
                    and character not in {">", "+", "~"}
                    and output[-1] not in {">", "+", "~"}
                ):
                    output.append(" ")
                pending_space = False
                output.append(character)
        normalized_selectors.append("".join(output))
    return normalized_selectors, not quote and not parentheses and not brackets


def css_author_rules(value: str) -> tuple[list[tuple[list[str], str]], bool]:
    """Flatten at-rule containers and reject unsupported CSS nesting."""
    collected: list[tuple[list[str], str]] = []

    def visit(segment: str, depth: int = 0) -> bool:
        if depth > 8:
            return False
        rules, valid = css_top_level_rules(segment)
        if not valid:
            return False
        for prelude, body in rules:
            children, child_valid = css_top_level_rules(body)
            if not child_valid:
                return False
            normalized_prelude = normalized_css(prelude).strip()
            if INVALID_CSS_MARKER in normalized_prelude:
                return False
            if children:
                if not normalized_prelude.startswith("@"):
                    return False
                if not visit(body, depth + 1):
                    return False
                continue
            if normalized_prelude.startswith("@"):
                selectors = [normalized_prelude]
            else:
                selectors, selector_valid = split_css_selector_list(prelude)
                if not selector_valid:
                    return False
            collected.append((selectors, body))
        return True

    return collected, visit(value)


def simple_selector_target_constraints(
    selector: str,
) -> tuple[str, frozenset[str]] | None:
    """Return only constraints that safely prove two target nodes disjoint."""
    output: list[str] = []
    quote = ""
    escaped = False
    brackets = 0
    parentheses = 0
    for character in selector.strip():
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = ""
            continue
        if escaped:
            escaped = False
            if not brackets and not parentheses:
                output.append(character)
            continue
        if character == "\\":
            escaped = True
            continue
        if character in {'"', "'"}:
            quote = character
            continue
        if character == "[":
            brackets += 1
            continue
        if character == "]":
            if not brackets:
                return None
            brackets -= 1
            continue
        if brackets:
            continue
        if character == "(":
            parentheses += 1
            continue
        if character == ")":
            if not parentheses:
                return None
            parentheses -= 1
            continue
        if parentheses:
            continue
        if character.isspace() or character in {">", "+", "~"}:
            return None
        output.append(character)
    if quote or brackets or parentheses:
        return None
    skeleton = "".join(output)
    type_match = re.match(r"^([A-Za-z][A-Za-z0-9-]*)", skeleton)
    html_types = {
        "a", "article", "aside", "button", "dd", "dialog", "div", "dl",
        "dt", "fieldset", "footer", "form", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "hr", "img", "input", "label", "li",
        "main", "nav", "ol", "option", "p", "section", "select", "span",
        "table", "tbody", "td", "textarea", "tfoot", "th", "thead", "tr",
        "ul",
    }
    type_name = (
        type_match.group(1).casefold()
        if type_match and type_match.group(1).casefold() in html_types else ""
    )
    identifiers = frozenset(re.findall(
        r"#([A-Za-z][A-Za-z0-9_-]*)", skeleton
    ))
    return type_name, identifiers


def selectors_provably_disjoint(first: str, second: str) -> bool:
    first_constraints = simple_selector_target_constraints(first)
    second_constraints = simple_selector_target_constraints(second)
    if first_constraints is None or second_constraints is None:
        return False
    first_type, first_ids = first_constraints
    second_type, second_ids = second_constraints
    return bool(
        (first_type and second_type and first_type != second_type)
        or (first_ids and second_ids and first_ids.isdisjoint(second_ids))
    )


def same_rule_zero_contrast_properties(
    styles: str, exact_var: re.Pattern,
) -> set[str]:
    """Reject a provably identical token for a surface and its visible cue."""
    conflicts: set[str] = set()
    rules, valid = css_author_rules(styles)
    if not valid:
        return {"invalid-css"}
    selector_usage: dict[str, dict[str, dict[str, set[str]]]] = {}
    for selectors, block in rules:
        block = normalized_css(block)
        if INVALID_CSS_MARKER in block:
            return {"invalid-css"}
        surfaces: dict[str, set[str]] = {}
        visible_cues: dict[str, set[str]] = {}
        for match in re.finditer(
            r"(?:^|;)\s*([a-zA-Z-]+)\s*:\s*([^;{}]+)", block, re.M,
        ):
            property_name = match.group(1).casefold()
            references = set(exact_var.findall(match.group(2)))
            if property_name in {"background", "background-color"}:
                target = surfaces
            elif (
                property_name in {
                    "accent-color", "caret-color", "color", "fill", "stroke",
                    "column-rule", "column-rule-color", "text-decoration-color",
                }
                or property_name == "border"
                or (
                    property_name.startswith("border-")
                    and property_name.endswith("-color")
                )
            ):
                target = visible_cues
            else:
                continue
            for reference in references:
                target.setdefault(reference, set()).add(property_name)
        for selector in selectors:
            usage = selector_usage.setdefault(
                selector, {"surfaces": {}, "visible_cues": {}}
            )
            for reference, properties in surfaces.items():
                usage["surfaces"].setdefault(reference, set()).update(properties)
            for reference, properties in visible_cues.items():
                usage["visible_cues"].setdefault(reference, set()).update(properties)
    selector_rows = list(selector_usage.items())
    for first_selector, first_usage in selector_rows:
        for second_selector, second_usage in selector_rows:
            if selectors_provably_disjoint(first_selector, second_selector):
                continue
            surfaces = first_usage["surfaces"]
            visible_cues = second_usage["visible_cues"]
            for reference in surfaces.keys() & visible_cues.keys():
                conflicts.update(surfaces[reference])
                conflicts.update(visible_cues[reference])
    return conflicts


def hard_coded_author_properties(
    styles: str, design_token_names: set[str]
) -> set[str]:
    """Return visual declarations containing anything beyond approved tokens.

    A token reference is not a waiver for a second literal value: declarations
    such as ``border: 1px solid var(--catalog-border)`` and token fallbacks are
    rejected. Geometry and typography must be one exact approved token so
    symbolic arithmetic cannot collapse content to zero.
    """
    hard_coded: set[str] = set()
    css_wide_keywords = {
        "inherit", "initial", "revert", "revert-layer", "unset",
    }
    exact_var = re.compile(r"var\(\s*(--[a-zA-Z0-9_-]+)\s*\)")
    structured, valid_structure = strip_css_comments(styles)
    normalized = normalized_css(styles)
    if not valid_structure or INVALID_CSS_MARKER in normalized:
        return {"invalid-css"}
    hard_coded.update(same_rule_zero_contrast_properties(structured, exact_var))
    rules, valid_rules = css_author_rules(structured)
    if not valid_rules:
        hard_coded.add("invalid-css")
    else:
        for _selectors, block in rules:
            declarations: dict[str, str] = {}
            normalized_block = normalized_css(block)
            for declaration in re.finditer(
                r"(?:^|;)\s*([a-zA-Z-]+)\s*:\s*([^;{}]+)",
                normalized_block,
                re.M,
            ):
                declarations[declaration.group(1).casefold()] = (
                    declaration.group(2).strip().casefold()
                )
            if (
                declarations.get("display") in {"flex", "inline-flex"}
                and declarations.get("flex-wrap") != "wrap"
            ):
                hard_coded.add("display")
    for match in re.finditer(
        r"(?:^|[;{])\s*([a-zA-Z-]+)\s*:\s*([^;{}]+)",
        normalized,
        re.M,
    ):
        property_name = match.group(1).casefold()
        property_value = match.group(2).strip()
        overflow_values = property_value.casefold().split()
        safe_overflow = (
            property_value.casefold() in css_wide_keywords
            or (
                property_name == "overflow"
                and len(overflow_values) in {1, 2}
                and all(
                    value in {"auto", "scroll", "visible"}
                    for value in overflow_values
                )
            )
            or (
                property_name in {"overflow-x", "overflow-y"}
                and len(overflow_values) == 1
                and overflow_values[0] in {"auto", "scroll", "visible"}
            )
        )
        if (
            property_name.startswith("-")
            or property_name in RUNTIME_OWNED_AUTHOR_PROPERTIES
            or "!important" in property_value.casefold()
            or (
                property_name == "display"
                and property_value.casefold()
                not in SAFE_AUTHOR_DISPLAY_VALUES
            )
            or (
                property_name in {"overflow", "overflow-x", "overflow-y"}
                and not safe_overflow
            )
            or (
                property_name in {
                    "color", "font-size", "height", "line-height",
                    "max-height", "max-width", "width",
                }
                and property_value.casefold() in {"0", "transparent"}
            )
        ):
            hard_coded.add(property_name)
            continue
        if property_name in EXACT_TOKEN_AUTHOR_PROPERTIES:
            token = exact_var.fullmatch(property_value)
            token_name = token.group(1) if token is not None else ""
            suffix = token_name.removeprefix("--catalog-")
            spacing_property = any(
                part in property_name
                for part in ("gap", "inset", "margin", "padding")
            ) or property_name in {"bottom", "left", "right", "top"}
            inline_size_property = (
                "width" in property_name or "inline-size" in property_name
                or property_name == "flex-basis"
            )
            block_size_property = (
                "height" in property_name or "block-size" in property_name
            )
            semantic = (
                spacing_property
                and (suffix.startswith("space-") or suffix == "gutter")
            ) or (
                inline_size_property
                and suffix in {"card-min-width", "content-width", "touch-target"}
            ) or (
                block_size_property
                and suffix in {"swatch-height", "touch-target"}
            ) or (
                property_name == "font-size"
                and suffix.startswith("type-") and suffix.endswith("-size")
            ) or (
                property_name == "line-height" and suffix == "line-height"
            )
            if (
                token is None
                or token_name not in design_token_names
                or not semantic
            ):
                hard_coded.add(property_name)
            continue
        if property_name in TOKEN_FREE_AUTHOR_PROPERTIES:
            if not token_free_author_value_is_valid(
                property_name, property_value
            ):
                hard_coded.add(property_name)
            continue
        token = exact_var.fullmatch(property_value)
        references = [token.group(1)] if token is not None else []
        if not references or references[0] not in design_token_names:
            hard_coded.add(property_name)
            continue
        if not author_token_roles_are_compatible(property_name, references):
            hard_coded.add(property_name)
    return hard_coded


def application_design_token_findings(tokens: str) -> list[str]:
    """Require the complete canonical light and dark application token scopes."""
    structured, valid_comments = strip_css_comments(tokens)
    rules, valid_rules = css_top_level_rules(structured)
    if not valid_comments or not valid_rules:
        return ["Design System application token block has invalid CSS structure"]

    def has_top_level_statement(value: str) -> bool:
        depth = 0
        quote = ""
        escaped = False
        statement_start = 0
        for index, character in enumerate(value):
            if quote:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == quote:
                    quote = ""
                continue
            if character in {'"', "'"}:
                quote = character
            elif character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    statement_start = index + 1
            elif character == ";" and depth == 0:
                if value[statement_start:index].strip():
                    return True
                statement_start = index + 1
        return bool(value[statement_start:].strip())

    def token_declarations(block: str) -> tuple[list[tuple[str, str]], bool]:
        normalized = normalized_css(block)
        if INVALID_CSS_MARKER in normalized or "{" in normalized or "}" in normalized:
            return [], False
        rows: list[tuple[str, str]] = []
        for segment in normalized.split(";"):
            if not segment.strip():
                continue
            match = re.fullmatch(
                r"\s*(--[a-zA-Z0-9_-]+)\s*:\s*(.*?)\s*",
                segment,
                re.S,
            )
            if match is None or "!important" in match.group(2).casefold():
                return rows, False
            rows.append((match.group(1), match.group(2).strip()))
        return rows, True

    if has_top_level_statement(structured):
        return [
            "Design System application token block may contain only closed canonical rule blocks"
        ]
    root_tokens: set[str] = set()
    dark_tokens: set[str] = set()
    root_values: dict[str, list[str]] = {}
    dark_values: dict[str, list[str]] = {}
    responsive_values: dict[str, list[str]] = {}
    structural_violations: list[str] = []
    responsive_rules = 0
    canonical_scope_order: list[str] = []
    for prelude, block in rules:
        normalized_prelude = normalized_css(prelude).strip()
        if normalized_prelude.startswith("@"):
            if not re.fullmatch(
                r"(?i)@media\s*\(\s*max-width\s*:\s*768px\s*\)",
                normalized_prelude,
            ):
                structural_violations.append(normalized_prelude)
                continue
            responsive_rules += 1
            canonical_scope_order.append("responsive")
            children, child_valid = css_top_level_rules(block)
            if not child_valid or len(children) != 1:
                structural_violations.append("responsive override structure")
                continue
            child_prelude, child_block = children[0]
            child_selectors, selector_valid = split_css_selector_list(
                child_prelude
            )
            declarations, declarations_valid = token_declarations(child_block)
            allowed_responsive = {
                "--catalog-gutter", "--catalog-type-display-size",
            }
            if (
                not selector_valid
                or child_selectors != [":root"]
                or not declarations_valid
                or {name for name, _value in declarations}
                != allowed_responsive
            ):
                structural_violations.append("responsive override contract")
                continue
            for name, value in declarations:
                responsive_values.setdefault(name, []).append(value)
            continue
        selectors, selector_valid = split_css_selector_list(prelude)
        if not selector_valid:
            return ["Design System application token block has invalid CSS structure"]
        declarations, declarations_valid = token_declarations(block)
        if not declarations_valid:
            structural_violations.append("non-token or important declaration")
            continue
        names = {name for name, _value in declarations}
        if selectors == [":root"]:
            canonical_scope_order.append("root")
            if not names <= REQUIRED_APPLICATION_ROOT_TOKENS:
                structural_violations.append("unknown root token")
                continue
            root_tokens.update(names)
            for name, value in declarations:
                root_values.setdefault(name, []).append(value.strip())
        elif selectors == ['[data-catalog-theme="dark"]']:
            canonical_scope_order.append("dark")
            if not names <= REQUIRED_APPLICATION_DARK_TOKENS:
                structural_violations.append("unknown dark-theme token")
                continue
            dark_tokens.update(names)
            for name, value in declarations:
                dark_values.setdefault(name, []).append(value.strip())
        else:
            structural_violations.append("non-canonical token selector")
    findings: list[str] = []
    if responsive_rules == 0:
        structural_violations.append("missing responsive override")
    elif responsive_rules > 1:
        structural_violations.append("duplicate responsive override")
    if canonical_scope_order != ["root", "dark", "responsive"]:
        structural_violations.append(
            "canonical scope order/count must be root, dark, responsive"
        )
    if structural_violations:
        findings.append(
            "Design System application token block must contain only exact root, dark-theme and canonical responsive token declarations: "
            + ", ".join(sorted(set(structural_violations)))
        )
    missing_root = sorted(REQUIRED_APPLICATION_ROOT_TOKENS - root_tokens)
    if missing_root:
        findings.append(
            "Design System application root token contract is incomplete: "
            + ", ".join(missing_root)
        )
    missing_dark = sorted(REQUIRED_APPLICATION_DARK_TOKENS - dark_tokens)
    if missing_dark:
        findings.append(
            "Design System application dark-theme token contract is incomplete: "
            + ", ".join(missing_dark)
        )
    css_wide = {"inherit", "initial", "revert", "revert-layer", "unset"}

    def ineffective(values: list[str]) -> bool:
        if len(values) != 1:
            return True
        value = normalized_css(values[0]).strip()
        return (
            not value
            or INVALID_CSS_MARKER in value
            or value.casefold() in css_wide
            or bool(re.search(r"(?i)(?:^|[^a-z-])(?:attr|env|var)\s*\(", value))
            or bool(re.search(r"(?i)</?style\b", value))
        )

    def static_length(value: str) -> Decimal | None:
        match = re.fullmatch(
            r"([+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+))"
            r"(px|rem|em)?",
            value.casefold(),
        )
        if match is None:
            return None
        try:
            number = Decimal(match.group(1))
        except InvalidOperation:
            return None
        unit = match.group(2)
        if not number.is_finite() or number != 0 and unit is None:
            return None
        if unit == "px":
            return number / Decimal(16)
        if unit == "rem" or number == 0 and unit is None:
            return number
        return None

    def opaque_hex_color(value: str) -> tuple[int, int, int] | None:
        match = re.fullmatch(r"#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", value)
        if match is None:
            return None
        digits = match.group(1)
        if len(digits) in {3, 4}:
            digits = "".join(character * 2 for character in digits)
        if len(digits) == 8 and digits[6:] != "ff":
            return None
        return tuple(int(digits[index:index + 2], 16) for index in (0, 2, 4))

    css_number = r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"

    def valid_font_family(value: str) -> bool:
        identifier = r"-?[_A-Za-z][_A-Za-z0-9-]*"
        unquoted = rf"{identifier}(?:\s+{identifier})*"
        quoted = r'''(?:"[ A-Za-z0-9_-]+"|'[ A-Za-z0-9_-]+')'''
        family = rf"(?:{unquoted}|{quoted})"
        return bool(re.fullmatch(rf"{family}(?:\s*,\s*{family})*", value))

    def valid_motion_easing(value: str) -> bool:
        lowered = value.casefold()
        if lowered in {
            "ease", "ease-in", "ease-in-out", "ease-out", "linear",
            "step-end", "step-start",
        }:
            return True
        cubic = re.fullmatch(
            rf"cubic-bezier\(\s*({css_number})\s*,\s*({css_number})\s*,\s*"
            rf"({css_number})\s*,\s*({css_number})\s*\)",
            lowered,
        )
        if cubic is not None:
            try:
                first, second, third, fourth = map(Decimal, cubic.groups())
            except InvalidOperation:
                return False
            return all(value.is_finite() for value in (first, second, third, fourth)) \
                and 0 <= first <= 1 and 0 <= third <= 1 \
                and -4 <= second <= 4 and -4 <= fourth <= 4
        steps = re.fullmatch(
            r"steps\(\s*([1-9][0-9]*)\s*(?:,\s*"
            r"(jump-start|jump-end|jump-none|jump-both|start|end)\s*)?\)",
            lowered,
        )
        return bool(steps and int(steps.group(1)) <= 100 and not (
            steps.group(2) == "jump-none" and int(steps.group(1)) < 2
        ))

    def valid_shadow(value: str) -> bool:
        if value.casefold() == "none":
            return True
        match = re.fullmatch(
            rf"(?:(inset)\s+)?"
            rf"({css_number}(?:px|rem|em)?)\s+"
            rf"({css_number}(?:px|rem|em)?)"
            rf"(?:\s+({css_number}(?:px|rem|em)?))?"
            rf"(?:\s+({css_number}(?:px|rem|em)?))?\s+(.+)",
            value,
            re.I,
        )
        if match is None:
            return False
        for length_index, raw_length in enumerate(match.groups()[1:5]):
            if raw_length is None:
                continue
            length = static_length(raw_length.casefold())
            if length is None or (
                length_index == 2 and not 0 <= length <= 8
            ) or (
                length_index != 2 and abs(length) > 4
            ):
                return False
        color = match.group(6).strip()
        if opaque_hex_color(color) is not None:
            return True
        rgba = re.fullmatch(
            rf"rgba\(\s*([0-9]{{1,3}})\s*,\s*([0-9]{{1,3}})\s*,\s*"
            rf"([0-9]{{1,3}})\s*,\s*({css_number})\s*\)",
            color,
            re.I,
        )
        if rgba is None or any(int(channel) > 255 for channel in rgba.groups()[:3]):
            return False
        try:
            alpha = Decimal(rgba.group(4))
        except InvalidOperation:
            return False
        return alpha.is_finite() and 0 <= alpha <= 1

    def contrast(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
        def luminance(color: tuple[int, int, int]) -> float:
            channels = []
            for channel in color:
                value = channel / 255
                channels.append(
                    value / 12.92
                    if value <= 0.04045
                    else ((value + 0.055) / 1.055) ** 2.4
                )
            return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]

        light, dark = sorted((luminance(first), luminance(second)), reverse=True)
        return (light + 0.05) / (dark + 0.05)

    def semantic_invalid(name: str, value: str) -> bool:
        normalized = normalized_css(value).strip()
        if name in APPLICATION_COLOR_TOKENS:
            return opaque_hex_color(normalized) is None
        if name in APPLICATION_LENGTH_BOUNDS_REM:
            length = static_length(normalized)
            minimum, maximum = APPLICATION_LENGTH_BOUNDS_REM[name]
            return length is None or not minimum <= length <= maximum
        if name == "--catalog-line-height":
            try:
                line_height = Decimal(normalized)
            except InvalidOperation:
                return True
            return not line_height.is_finite() or not Decimal("1.2") <= line_height <= 3
        if name == "--catalog-header-layer":
            return not normalized.isdigit() or not 1 <= int(normalized) <= 1000
        if name == "--catalog-type-display-weight":
            return not normalized.isdigit() or not 1 <= int(normalized) <= 1000
        if name == "--catalog-motion-fast":
            match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?|\.[0-9]+)(ms|s)", normalized.casefold())
            if match is None:
                return True
            duration = Decimal(match.group(1))
            milliseconds = duration * (1000 if match.group(2) == "s" else 1)
            return not Decimal("50") <= milliseconds <= Decimal("1000")
        if name in {"--catalog-font-body", "--catalog-font-heading"}:
            return not valid_font_family(normalized)
        if name == "--catalog-motion-easing":
            return not valid_motion_easing(normalized)
        if name == "--catalog-shadow-sm":
            return not valid_shadow(normalized)
        return False

    def scope_semantic_findings(
        required: set[str], values: dict[str, list[str]], label: str,
    ) -> None:
        invalid = sorted(
            name for name in required
            if len(values.get(name, [])) == 1
            and semantic_invalid(name, values[name][0])
        )
        if invalid:
            findings.append(
                f"Design System application {label} token values violate canonical semantic constraints: "
                + ", ".join(invalid)
            )
        colors = {
            name: opaque_hex_color(values[name][0])
            for name in APPLICATION_COLOR_TOKENS
            if len(values.get(name, [])) == 1
        }
        for foreground, background, minimum in (
            ("--catalog-foreground", "--catalog-background", 4.5),
            ("--catalog-foreground", "--catalog-surface", 4.5),
            ("--catalog-muted", "--catalog-background", 4.5),
            ("--catalog-muted", "--catalog-surface", 4.5),
            ("--catalog-accent", "--catalog-background", 4.5),
            ("--catalog-accent", "--catalog-surface", 4.5),
            ("--catalog-success", "--catalog-background", 4.5),
            ("--catalog-success", "--catalog-surface", 4.5),
            ("--catalog-warning", "--catalog-background", 4.5),
            ("--catalog-warning", "--catalog-surface", 4.5),
            ("--catalog-error", "--catalog-background", 4.5),
            ("--catalog-error", "--catalog-surface", 4.5),
            ("--catalog-border", "--catalog-background", 3.0),
            ("--catalog-border", "--catalog-surface", 3.0),
            ("--catalog-focus", "--catalog-background", 3.0),
            ("--catalog-focus", "--catalog-surface", 3.0),
        ):
            first, second = colors.get(foreground), colors.get(background)
            if first is not None and second is not None \
                    and contrast(first, second) < minimum:
                findings.append(
                    f"Design System application {label} token contrast is below {minimum:g}: "
                    f"{foreground} / {background}"
                )

    invalid_root = sorted(
        name for name in REQUIRED_APPLICATION_ROOT_TOKENS
        if name in root_tokens and ineffective(root_values.get(name, []))
    )
    if invalid_root:
        findings.append(
            "Design System application root tokens need one concrete effective value: "
            + ", ".join(invalid_root)
        )
    invalid_dark = sorted(
        name for name in REQUIRED_APPLICATION_DARK_TOKENS
        if name in dark_tokens and ineffective(dark_values.get(name, []))
    )
    if invalid_dark:
        findings.append(
            "Design System application dark-theme tokens need one concrete effective value: "
            + ", ".join(invalid_dark)
        )
    scope_semantic_findings(
        REQUIRED_APPLICATION_ROOT_TOKENS, root_values, "root",
    )
    scope_semantic_findings(
        REQUIRED_APPLICATION_DARK_TOKENS, dark_values, "dark-theme",
    )
    invalid_responsive = sorted(
        name for name in {
            "--catalog-gutter", "--catalog-type-display-size",
        }
        if name in responsive_values and (
            ineffective(responsive_values[name])
            or semantic_invalid(name, responsive_values[name][0])
        )
    )
    if invalid_responsive:
        findings.append(
            "Design System application responsive token overrides violate canonical semantic constraints: "
            + ", ".join(invalid_responsive)
        )
    return findings


def required_experience_findings(
    text: str, scanner: ApplicationScanner | None = None
) -> list[str]:
    if scanner is None:
        scanner = ApplicationScanner()
        scanner.feed(text)
        scanner.close()
    normalized_styles = normalized_css(
        "\n".join(scanner.styles)
    ).casefold()
    reachable_containers = reachable_application_containers(scanner)
    root_attrs = scanner.html_attributes[0] if len(scanner.html_attributes) == 1 else {}
    expected_pressed = {
        "toggle-theme": "true" if root_attrs.get("data-theme") == "dark" else "false",
        "toggle-privacy": "true" if root_attrs.get("data-privacy") == "masked" else "false",
    }
    findings = browser_stable_html_source_findings(text)
    findings.extend(
        f"application needs {label}"
        for marker, label in REQUIRED_EXPERIENCE_MARKERS.items()
        if marker in STYLE_EXPERIENCE_MARKERS and marker not in normalized_styles
    )
    for action, label in (
        ("toggle-theme", "accessible theme control"),
        ("toggle-privacy", "privacy masking control"),
    ):
        controls = [
            control for control in scanner.controls
            if control["action"] == action
            and control["route"] == ""
            and not control["dialog_ancestors"]
            and native_actionable_control(control)
            and reachable_control(control, reachable_containers)
            and bool(control_accessible_name(control, scanner))
            and visible_action_label(control, scanner)
            and sequentially_keyboard_reachable(control)
            and control["aria_pressed"] == expected_pressed[action]
        ]
        if len(controls) != 1:
            findings.append(f"application needs {label}")
    if not any(
        reachable_node(target, reachable_containers)
        and (
            has_visible_content(target.get("text", ""))
            or has_visible_content(target.get("value", ""))
        )
        for target in scanner.private_targets
    ):
        findings.append("application needs privacy-masked content")
    return findings


def browser_stable_html_source_findings(text: str) -> list[str]:
    """Ban HTML comment/bogus-declaration channels with tokenizer ambiguity."""
    doctype = re.match(r"(?i)<!doctype html>", text)
    remainder = text[doctype.end():] if doctype is not None else text
    if re.search(r"(?i)<!|--!?>|<\?", remainder):
        return [
            "application HTML comments and bogus declarations are forbidden for browser-stable parsing"
        ]
    return []


def style_scaffold(value: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for match in STYLE_PATTERN.finditer(value):
        body = TOKEN_PATTERN.sub(
            "/* application:design-tokens:start */\n"
            "__DESIGN_TOKENS__\n"
            "/* application:design-tokens:end */",
            match.group("body"),
            count=1,
        )
        body = AUTHOR_STYLE_PATTERN.sub(
            "/* application:author-styles:start */\n"
            "__AUTHOR_STYLES__\n"
            "/* application:author-styles:end */",
            body,
            count=1,
        )
        result.append((match.group("attrs").strip(), body.strip()))
    return result


class DirectHeadMetaParser(HTMLParser):
    """Locate actual direct-head metadata and retain its exact source span."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.text = text
        self.line_offsets = [0]
        self.line_offsets.extend(
            match.end() for match in re.finditer(r"\n", text)
        )
        self.stack: list[str] = []
        self.rows: list[dict] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.casefold()
        parent = self.stack[-1] if self.stack else ""
        if tag == "meta" and parent == "head":
            values: dict[str, str] = {}
            duplicate = False
            for key, value in attrs:
                key = key.casefold()
                if key in values:
                    duplicate = True
                values[key] = value or ""
            raw = self.get_starttag_text() or ""
            line, column = self.getpos()
            start = self.line_offsets[line - 1] + column
            if self.text[start:start + len(raw)] == raw:
                self.rows.append({
                    "attrs": values,
                    "duplicate": duplicate,
                    "start": start,
                    "end": start + len(raw),
                })
        if tag not in VOID_ELEMENTS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index] == tag:
                del self.stack[index:]
                return


def direct_head_meta_rows(text: str) -> list[dict]:
    parser = DirectHeadMetaParser(text)
    parser.feed(text)
    parser.close()
    return parser.rows


def metadata(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in direct_head_meta_rows(text):
        attrs = row["attrs"]
        if attrs.get("name"):
            result[attrs["name"].casefold()] = attrs.get("content", "")
    return result


def replace_meta(text: str, name: str, value: str) -> str:
    rows = [
        row for row in direct_head_meta_rows(text)
        if row["attrs"].get("name", "").casefold() == name.casefold()
    ]
    if len(rows) != 1:
        raise ValueError(f"application is missing metadata {name}")
    if any(character in value for character in {'"', "<", ">", "&"}):
        raise ValueError(f"application metadata {name} contains unsafe characters")
    row = rows[0]
    replacement = f'<meta name="{name}" content="{value}">'
    return text[:row["start"]] + replacement + text[row["end"]:]


def replace_tokens(text: str, tokens: str) -> str:
    if not TOKEN_PATTERN.search(text):
        raise ValueError("application is missing its Design System token markers")
    replacement = (
        "/* application:design-tokens:start */\n"
        + tokens.rstrip()
        + "\n/* application:design-tokens:end */"
    )
    return TOKEN_PATTERN.sub(lambda _match: replacement, text, count=1)


def normalized_source(text: str) -> bytes:
    replacements: list[tuple[int, int, str]] = []
    for row in direct_head_meta_rows(text):
        name = row["attrs"].get("name", "").casefold()
        if name not in MACHINE_META:
            continue
        marker = f"__{name.upper().replace('-', '_')}__"
        replacements.append((
            row["start"], row["end"],
            f'<meta name="{name}" content="{marker}">',
        ))
    for start, end, replacement in reversed(replacements):
        text = text[:start] + replacement + text[end:]
    return text.encode()


def source_hash(text: str) -> str:
    return sha(normalized_source(text))


def design_binding(root: Path) -> tuple[dict, dict, list[str]]:
    design_root = root.parent / "design-system"
    findings: list[str] = []
    receipt, errors = stage_package.verify(
        root.parent,
        "design-system",
        "design-system/MASTER",
        require_strict_current=True,
    )
    findings.extend(errors)
    master = design_root / "MASTER.md"
    fields: dict = {}
    try:
        fields, _lines, _end = design_system_compile.parse_frontmatter(master)
    except (OSError, ValueError) as exc:
        findings.append(f"Design System MASTER is unreadable: {exc}")
    if design_system_compile.contract_version(design_root) < 3:
        findings.append("Experience application requires Design System contract_version 3")
    try:
        tokens = design_system_compile.catalog_tokens(master)
        master_hash = design_system_compile.master_source_hash(master)
        findings.extend(application_design_token_findings(tokens))
    except (OSError, ValueError) as exc:
        findings.append(f"Design System application binding is invalid: {exc}")
        tokens, master_hash = "", ""
    return (
        {
            "package_hash": str((receipt or {}).get("package_hash", "")),
            "revision": fields.get("revision"),
            "master_source_hash": master_hash,
            "tokens": tokens,
        },
        receipt or {},
        findings,
    )


def render_template(root: Path, proposal_hash: str, revision: int) -> str:
    design, _receipt, findings = design_binding(root)
    if findings:
        raise ValueError("; ".join(findings))
    text = template_text()
    replacements = {
        "PROPOSAL_HASH": proposal_hash,
        "SOURCE_HASH": "",
        "PACKAGE_SET_HASH": "",
        "COVERAGE_HASH": "",
        "APPLICATION_HASH": "",
        "RUNTIME_SHA256": runtime_sha256(),
        "RUNTIME_CSP_SHA256": runtime_csp_sha256(),
        "DESIGN_SYSTEM_PACKAGE_HASH": str(design["package_hash"]),
        "DESIGN_SYSTEM_MASTER_REVISION": str(design["revision"]),
        "DESIGN_SYSTEM_MASTER_SOURCE_HASH": str(design["master_source_hash"]),
    }
    for marker, value in sorted(
            replacements.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(marker, value)
    text = replace_meta(text, "experience-application-revision", str(revision))
    return replace_tokens(text, str(design["tokens"]))


def load_application_map(package: Path) -> tuple[dict, list[str]]:
    path = package / MAP_RELATIVE
    findings: list[str] = []
    if not path.is_file() or path.is_symlink():
        return {}, [
            f"{package.name}/{MAP_RELATIVE.as_posix()}: regular application map is required"
        ]
    try:
        value = strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {}, [f"{package.name}/{MAP_RELATIVE.as_posix()}: {exc}"]
    if type(value) is not dict:
        return {}, [f"{package.name}/{MAP_RELATIVE.as_posix()}: root must be an object"]
    expected_keys = set(MAP_SCHEMA["required_fields"])
    if set(value) != expected_keys:
        findings.append(
            f"{package.name}/{MAP_RELATIVE.as_posix()}: keys must be exactly "
            + ", ".join(sorted(expected_keys))
        )
    validate_exact_field_types(
        value,
        MAP_SCHEMA.get("field_types"),
        f"{package.name}/{MAP_RELATIVE.as_posix()}",
        findings,
    )
    if value.get("schema_version") != MAP_SCHEMA["schema_version"]:
        findings.append(f"{package.name}/{MAP_RELATIVE.as_posix()}: unsupported schema")
    if value.get("application_path") != "experience-design/artifacts/application.html":
        findings.append(
            f"{package.name}/{MAP_RELATIVE.as_posix()}: application_path must target the canonical application"
        )
    if value.get("experience_id") != package.name:
        findings.append(
            f"{package.name}/{MAP_RELATIVE.as_posix()}: experience_id must match its package"
        )
    bindings = value.get("bindings")
    if type(bindings) is not list:
        findings.append(f"{package.name}/{MAP_RELATIVE.as_posix()}: bindings must be an array")
        bindings = []
    binding_keys = set(MAP_SCHEMA["binding_required_fields"])
    entry_keys = set(MAP_SCHEMA["entry_required_fields"])
    minimum_entries = int(MAP_SCHEMA["minimum_entries"])
    normalized: list[dict] = []
    seen: set[str] = set()
    for index, row in enumerate(bindings):
        label = f"{package.name}/{MAP_RELATIVE.as_posix()}: bindings[{index}]"
        if type(row) is not dict or set(row) != binding_keys:
            findings.append(
                f"{label} must contain exactly " + ", ".join(sorted(binding_keys))
            )
            continue
        validate_exact_field_types(
            row, MAP_SCHEMA.get("binding_field_types"), label, findings
        )
        reference_value = row.get("record_ref")
        reference = reference_value if type(reference_value) is str else ""
        entries = row.get("entries")
        if not EXACT.fullmatch(reference) or not reference.startswith(package.name + ":"):
            findings.append(f"{label} needs an exact ref owned by {package.name}")
        if reference in seen:
            findings.append(f"{label} duplicates {reference}")
        seen.add(reference)
        if type(entries) is not list or len(entries) < minimum_entries:
            findings.append(
                f"{label}.entries needs at least {minimum_entries} route/state entry"
            )
            entries = []
        normalized_entries: list[dict] = []
        seen_entries: set[tuple[str, str]] = set()
        for entry_index, entry in enumerate(entries):
            entry_label = f"{label}.entries[{entry_index}]"
            if type(entry) is not dict or set(entry) != entry_keys:
                findings.append(
                    f"{entry_label} must contain exactly "
                    + ", ".join(sorted(entry_keys))
                )
                continue
            validate_exact_field_types(
                entry, MAP_SCHEMA.get("entry_field_types"), entry_label, findings
            )
            route_value = entry.get("route")
            state_value = entry.get("state_class")
            route = route_value if type(route_value) is str else ""
            state_class = state_value if type(state_value) is str else ""
            if not ROUTE.fullmatch(route):
                findings.append(f"{entry_label} needs a normalized deep route")
            if state_class not in MAP_STATE_CLASSES:
                findings.append(f"{entry_label} has an unsupported state_class")
            key = (route, state_class)
            if key in seen_entries:
                findings.append(f"{entry_label} duplicates {route} / {state_class}")
            seen_entries.add(key)
            normalized_entries.append(
                {"route": route, "state_class": state_class}
            )
        normalized.append(
            {
                "record_ref": reference,
                "entries": sorted(
                    normalized_entries,
                    key=lambda entry: (entry["route"], entry["state_class"]),
                ),
            }
        )
    return {
        "schema_version": MAP_SCHEMA["schema_version"],
        "application_path": MAP_SCHEMA["application_path"],
        "experience_id": package.name,
        "bindings": sorted(normalized, key=lambda row: row["record_ref"]),
    }, findings


def valid_language_tag(value: str) -> bool:
    """Accept one closed, structurally valid ASCII BCP 47 language tag."""
    if not value or not re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", value):
        return False
    parts = value.split("-")
    index = 0
    if parts[0].casefold() == "x":
        return len(parts) > 1 and all(1 <= len(part) <= 8 for part in parts[1:])
    language = parts[0]
    if not language.isalpha() or not 2 <= len(language) <= 8:
        return False
    index = 1
    if len(language) in {2, 3}:
        extlangs = 0
        while (
            index < len(parts)
            and len(parts[index]) == 3
            and parts[index].isalpha()
            and extlangs < 3
        ):
            index += 1
            extlangs += 1
    if index < len(parts) and len(parts[index]) == 4 and parts[index].isalpha():
        index += 1
    if index < len(parts) and (
        (len(parts[index]) == 2 and parts[index].isalpha())
        or (len(parts[index]) == 3 and parts[index].isdigit())
    ):
        index += 1
    variants: set[str] = set()
    while index < len(parts) and (
        (5 <= len(parts[index]) <= 8)
        or (len(parts[index]) == 4 and parts[index][0].isdigit())
    ):
        variant = parts[index].casefold()
        if variant in variants:
            return False
        variants.add(variant)
        index += 1
    singletons: set[str] = set()
    while (
        index < len(parts)
        and len(parts[index]) == 1
        and parts[index].isalnum()
        and parts[index].casefold() != "x"
    ):
        singleton = parts[index].casefold()
        if singleton in singletons:
            return False
        singletons.add(singleton)
        index += 1
        start = index
        while index < len(parts) and 2 <= len(parts[index]) <= 8:
            index += 1
        if index == start:
            return False
    if index < len(parts) and parts[index].casefold() == "x":
        index += 1
        start = index
        while index < len(parts) and 1 <= len(parts[index]) <= 8:
            index += 1
        if index == start:
            return False
    return index == len(parts)


def html_tag_token(text: str, position: int) -> tuple[bool, str, int] | None:
    """Lex one conservative HTML tag token without regex tokenizer ambiguity."""
    if position >= len(text) or text[position] != "<":
        return None
    index = position + 1
    closing = index < len(text) and text[index] == "/"
    if closing:
        index += 1
    if index >= len(text) or text[index] not in (
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    ):
        return None
    start = index
    while index < len(text) and text[index] in ASCII_TAG_NAME:
        index += 1
    name = text[start:index].casefold()
    if index >= len(text) or text[index] not in "\t\n\f\r />":
        return None
    quote = ""
    while index < len(text):
        character = text[index]
        if quote:
            if character == quote:
                quote = ""
        elif character in "\"'":
            quote = character
        elif character == ">":
            return closing, name, index + 1
        index += 1
    return None


def raw_text_markup_findings(text: str) -> list[str]:
    """Detect markup-like tokens inside browser raw-text and RCDATA elements."""
    findings: list[str] = []
    raw_tag = ""
    index = 0
    while index < len(text):
        token = html_tag_token(text, index)
        if token is None:
            index += 1
            continue
        closing, tag, end = token
        if raw_tag:
            if closing and tag == raw_tag:
                raw_tag = ""
            else:
                findings.append(f"{raw_tag}>{tag}")
            index = end
            continue
        if not closing and tag in RAWTEXT_OR_RCDATA_ELEMENTS:
            raw_tag = tag
        index = end
    return sorted(set(findings))


class ApplicationScanner(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.position = 0
        self.declarations: list[str] = []
        self.element_counts: dict[str, int] = {}
        self.element_parents: dict[str, list[tuple[str, str]]] = {}
        self.top_level_elements: list[str] = []
        self.html_children: list[str] = []
        self.html_attributes: list[dict[str, str]] = []
        self.head_children: list[dict] = []
        self.body_children: list[dict] = []
        self.main_children: list[dict] = []
        self.toolbar_children: list[dict] = []
        self.action_children: list[dict] = []
        self.scaffold_direct_text: list[str] = []
        self.class_uses: dict[str, list[dict]] = {}
        self.structural_text: list[str] = []
        self.end_positions: dict[str, int] = {}
        self.structural_errors: list[str] = []
        self.raw_text_markup: list[str] = []
        self.self_closing_non_void: list[str] = []
        self.unsupported_interactive_attributes: list[str] = []
        self.forbidden_presentational_attributes: list[str] = []
        self.runtime_named_property_conflicts: list[str] = []
        self.orphan_routing_attributes: list[str] = []
        self.invalid_aria_disabled: list[str] = []
        self.invalid_roles: list[str] = []
        self.invalid_ids: list[str] = []
        self.invalid_class_attributes: list[str] = []
        self.invalid_hidden_values: list[str] = []
        self.invalid_tabindex_values: list[str] = []
        self.invalid_language_attributes: list[str] = []
        self.invalid_aria_hidden: list[str] = []
        self.unsupported_global_aria: list[str] = []
        self.unmanaged_live_regions: list[str] = []
        self.unmanaged_native_widgets: list[str] = []
        self.passive_descriptions: list[dict] = []
        self.labels: list[dict] = []
        self.native_optgroups: list[dict] = []
        self.form_owner_overrides: list[str] = []
        self.parser_reparenting_risks: list[str] = []
        self.nested_form_or_interactive: list[str] = []
        self._elements: list[dict] = []
        self.metas: dict[str, str] = {}
        self.meta_counts: dict[str, int] = {}
        self.scripts: list[dict] = []
        self._script: dict | None = None
        self.styles: list[str] = []
        self.inline_styles: list[str] = []
        self._style: str | None = None
        self._stack: list[tuple[str, str]] = []
        self._route = ""
        self.route_views: dict[str, int] = {}
        self.route_parents: dict[str, list[tuple[str, str]]] = {}
        self.route_states: dict[str, list[str]] = {}
        self.route_targets: dict[str, list[dict]] = {}
        self.record_routes: dict[str, set[str]] = {}
        self.record_targets: dict[str, list[dict]] = {}
        self.context_sources: dict[str, set[str]] = {}
        self.context_targets: dict[str, set[str]] = {}
        self.controls: list[dict] = []
        self.fragment_anchors: list[dict] = []
        self.skip_links: list[dict] = []
        self.titles: list[dict] = []
        self.targets: list[tuple[str, str]] = []
        self.inline_handlers: list[str] = []
        self.forbidden_elements: list[str] = []
        self.smil_mutations: list[str] = []
        self.presentation_urls: list[tuple[str, str]] = []
        self.meta_refresh = False
        self.duplicate_attributes: list[str] = []
        self.element_ids: dict[str, int] = {}
        self.elements_by_id: dict[str, list[dict]] = {}
        self.route_labels: dict[str, list[str]] = {}
        self.forms_without_route = 0
        self.routed_forms: list[dict] = []
        self.search_controls: list[dict] = []
        self.search_items: list[dict] = []
        self.filter_controls: list[dict] = []
        self.filter_items: list[dict] = []
        self.filter_options: list[dict] = []
        self.onboarding_targets: list[dict] = []
        self.settings_targets: list[dict] = []
        self.private_targets: list[dict] = []
        self.outcome_targets: list[dict] = []
        self.announcer_sinks: list[dict] = []
        self.images: list[dict] = []
        self.interactive_elements: list[dict] = []
        self.context_elements: list[dict] = []
        self.context_options: list[dict] = []
        self.native_radios: list[dict] = []
        self.dialogs: list[dict] = []
        self.listboxes: list[dict] = []
        self.listbox_options: list[dict] = []
        self.http_equivs: list[str] = []
        self.csp_values: list[str] = []
        self._source_parts: list[str] = []
        self._raw_text_scanned = False

    def feed(self, data: str) -> None:
        self._source_parts.append(data)
        self._raw_text_scanned = False
        super().feed(data)

    def close(self) -> None:
        super().close()
        if not self._raw_text_scanned:
            self.raw_text_markup.extend(
                raw_text_markup_findings("".join(self._source_parts))
            )
            self._raw_text_scanned = True

    def handle_decl(self, decl: str) -> None:
        self.declarations.append(decl.casefold().strip())

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag.casefold() not in VOID_ELEMENTS:
            self.self_closing_non_void.append(tag.casefold())
        super().handle_startendtag(tag, attrs)

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.casefold()
        self.position += 1
        values: dict[str, str] = {}
        for key, value in attrs:
            normalized = key.casefold()
            if normalized in values:
                self.duplicate_attributes.append(f"{tag}[{normalized}]")
                continue
            values[normalized] = value or ""
        if "lang" in values and not valid_language_tag(values["lang"]):
            self.invalid_language_attributes.append(f'{tag}[lang="{values["lang"]}"]')
        if "xml:lang" in values:
            self.invalid_language_attributes.append(f"{tag}[xml:lang]")
        if "dir" in values and values["dir"] not in {"ltr", "rtl", "auto"}:
            self.invalid_language_attributes.append(f'{tag}[dir="{values["dir"]}"]')
        if "tabindex" in values and not (
            values["tabindex"] == "0"
            or (
                tag == "main"
                and values.get("id") == "application-main"
                and values["tabindex"] == "-1"
            )
        ):
            self.invalid_tabindex_values.append(f'{tag}[tabindex="{values["tabindex"]}"]')
        if "role" in values and values["role"] not in ALLOWED_EXPLICIT_ROLES:
            self.invalid_roles.append(f'{tag}[role="{values["role"]}"]')
        if "id" in values and not HTML_ID.fullmatch(values["id"]):
            self.invalid_ids.append(f'{tag}[id="{values["id"]}"]')
        if "form" in values and tag in {
            "button", "fieldset", "input", "object", "output", "select",
            "textarea",
        }:
            self.form_owner_overrides.append(f"{tag}[form]")
        parent = self._elements[-1] if self._elements else {}
        ancestors = tuple(self._elements)
        in_body = any(ancestor["tag"] == "body" for ancestor in ancestors)
        parent_tag = parent.get("tag", "")
        if parent_tag == "head" and tag not in HEAD_ALLOWED_CHILDREN:
            self.parser_reparenting_risks.append(f"head>{tag}")
        foreign_scope = tag in {"math", "svg"} or any(
            ancestor["tag"] in {"math", "svg"} for ancestor in ancestors
        )
        foreign_semantics = (
            tag in {
                "a", "button", "dialog", "form", "input", "select",
                "textarea",
            }
            or values.get("role") in {"listbox", "option"}
            or any(
                key.startswith("data-application-")
                or key in {
                    "data-context-key", "data-experience-ref", "data-private",
                    "data-route-target", "data-transition-ref",
                    "data-simulation-id", "data-preserve-context",
                    "data-return-route",
                }
                for key in values
            )
        )
        if foreign_scope and foreign_semantics:
            self.parser_reparenting_risks.append(
                f"foreign-content>{tag} application semantics"
            )
        allowed_direct_children = DIRECT_CHILDREN.get(parent_tag)
        if allowed_direct_children is not None and tag not in allowed_direct_children:
            self.parser_reparenting_risks.append(f"{parent_tag}>{tag}")
        required_parents = REQUIRED_DIRECT_PARENTS.get(tag)
        if required_parents is not None and parent_tag not in required_parents:
            self.parser_reparenting_risks.append(f"{parent_tag or 'document'}>{tag}")
        direct_siblings = parent.get("direct_child_tags", [])
        if parent_tag == "fieldset" and tag == "legend":
            if direct_siblings or parent.get("legend_seen"):
                self.parser_reparenting_risks.append("fieldset>legend-order")
            parent["legend_seen"] = True
        if parent_tag == "figure":
            if tag == "figcaption":
                if parent.get("figcaption_seen"):
                    self.parser_reparenting_risks.append("figure>figcaption-count")
                parent["figcaption_seen"] = True
                if direct_siblings:
                    parent["figcaption_requires_last"] = True
            elif parent.get("figcaption_requires_last"):
                self.parser_reparenting_risks.append("figure>figcaption-order")
        if parent:
            parent.setdefault("direct_child_tags", []).append(tag)
        if (
            "aria-disabled" in values
            and values["aria-disabled"] not in {"true", "false"}
        ):
            self.invalid_aria_disabled.append(f"{tag}[aria-disabled]")
        if (
            "aria-hidden" in values
            and values["aria-hidden"] not in {"true", "false"}
        ):
            self.invalid_aria_hidden.append(f"{tag}[aria-hidden]")
        aria_attributes = {
            name for name in values if name.startswith("aria-")
        }
        globally_supported_aria = PASSIVE_ARIA_ATTRIBUTES | {
            "aria-controls", "aria-disabled", "aria-expanded",
            "aria-haspopup", "aria-hidden", "aria-live",
            "aria-modal", "aria-multiselectable", "aria-orientation",
            "aria-pressed", "aria-readonly", "aria-selected",
        }
        unsupported_aria = aria_attributes - globally_supported_aria
        action_owned_aria = {
            "aria-controls", "aria-expanded", "aria-haspopup",
            "aria-pressed", "aria-selected",
        }
        if unsupported_aria:
            self.unsupported_global_aria.extend(
                f"{tag}[{name}]" for name in sorted(unsupported_aria)
            )
        for name in sorted(action_owned_aria & aria_attributes):
            if "data-application-action" not in values:
                self.unsupported_global_aria.append(f"{tag}[{name}]")
        if "aria-disabled" in values and not any(
            identity in values for identity in (
                "data-application-action", "data-transition-ref",
                "data-simulation-id",
            )
        ):
            self.unsupported_global_aria.append(f"{tag}[aria-disabled]")
        for name in sorted(
            {"aria-multiselectable", "aria-orientation"} & aria_attributes
        ):
            if values.get("role") != "listbox":
                self.unsupported_global_aria.append(f"{tag}[{name}]")
        if "aria-modal" in values and tag != "dialog":
            self.unsupported_global_aria.append(f"{tag}[aria-modal]")
        if (
            "aria-readonly" in values
            and "data-application-search" not in values
        ):
            self.unsupported_global_aria.append(f"{tag}[aria-readonly]")
        if (
            "aria-live" in values
            and values.get("id") != "application-announcer"
        ):
            self.unsupported_global_aria.append(f"{tag}[aria-live]")
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"} and any(
            ancestor["tag"] in {"h1", "h2", "h3", "h4", "h5", "h6"}
            for ancestor in ancestors
        ):
            self.parser_reparenting_risks.append("heading>heading")
        if tag == "label" and any(
            ancestor["tag"] == "label" for ancestor in ancestors
        ):
            self.parser_reparenting_risks.append("label>label")
        if any(ancestor["tag"] == "address" for ancestor in ancestors) and tag in {
            "address", "article", "aside", "footer", "header", "h1", "h2",
            "h3", "h4", "h5", "h6", "main", "nav", "section",
        }:
            self.parser_reparenting_risks.append(f"address>{tag}")
        if tag in {"header", "footer"} and any(
            ancestor["tag"] in {"header", "footer"} for ancestor in ancestors
        ):
            self.parser_reparenting_risks.append(f"header/footer>{tag}")
        if tag in P_IMPLIED_END_STARTS and any(
            ancestor["tag"] == "p" for ancestor in ancestors
        ):
            self.parser_reparenting_risks.append(f"p>{tag}")
        if tag == "li" and any(
            ancestor["tag"] == "li" for ancestor in ancestors
        ):
            self.parser_reparenting_risks.append("li>li")
        if tag in {"dt", "dd"} and any(
            ancestor["tag"] in {"dt", "dd"} for ancestor in ancestors
        ):
            self.parser_reparenting_risks.append(f"dt/dd>{tag}")
        raw_ancestor = next(
            (
                ancestor["tag"] for ancestor in reversed(ancestors)
                if ancestor["tag"] in RAWTEXT_OR_RCDATA_ELEMENTS
            ),
            "",
        )
        if raw_ancestor:
            self.raw_text_markup.append(f"{raw_ancestor}>{tag}")
        if tag == "form" and any(
            ancestor["tag"] == "form" for ancestor in ancestors
        ):
            self.nested_form_or_interactive.append("form>form")
        if tag in {"a", "button", "input", "select", "textarea"}:
            interactive_ancestor = next((
                ancestor["tag"] for ancestor in reversed(ancestors)
                if ancestor["tag"] in {"a", "button"}
            ), "")
            if interactive_ancestor:
                self.nested_form_or_interactive.append(
                    f"{interactive_ancestor}>{tag}"
                )
        select_ancestor = next((
            ancestor["tag"] for ancestor in reversed(ancestors)
            if ancestor["tag"] in {"select", "option", "optgroup"}
        ), "")
        allowed_in_select = (
            select_ancestor == "select" and tag in {"hr", "optgroup", "option"}
        ) or (
            select_ancestor == "optgroup" and tag == "option"
        )
        if select_ancestor and not allowed_in_select:
            self.nested_form_or_interactive.append(
                f"{select_ancestor}>{tag}"
            )
        hidden_ancestors = tuple(
            ancestor for ancestor in ancestors
            if "hidden" in ancestor["attrs"]
            and "data-application-route" not in ancestor["attrs"]
        )
        dialog_ancestors = tuple(
            ancestor for ancestor in ancestors if ancestor["tag"] == "dialog"
        )
        ancestor_ids = tuple(
            ancestor["id"] for ancestor in ancestors if ancestor["id"]
        )
        ancestor_positions = tuple(
            ancestor["position"] for ancestor in ancestors
        )
        label_ancestor_positions = tuple(
            ancestor["position"] for ancestor in ancestors
            if ancestor["tag"] == "label"
        )
        runtime_mutable_label_context = bool(
            RUNTIME_MUTABLE_LABEL_ATTRIBUTES.intersection(values)
            or any(
                RUNTIME_MUTABLE_LABEL_ATTRIBUTES.intersection(
                    ancestor["attrs"]
                )
                for ancestor in ancestors
            )
        )
        hidden_ancestor_ids = tuple(
            ancestor["id"] for ancestor in hidden_ancestors if ancestor["id"]
        )
        dialog_ancestor_ids = tuple(
            ancestor["id"] for ancestor in dialog_ancestors if ancestor["id"]
        )
        anonymous_hidden_ancestor = any(
            not ancestor["id"] for ancestor in hidden_ancestors
        )
        anonymous_dialog_ancestor = any(
            not ancestor["id"] for ancestor in dialog_ancestors
        )
        disabled_ancestor = any(
            "inert" in ancestor["attrs"]
            or ancestor["attrs"].get("aria-hidden", "").casefold() == "true"
            or ancestor["attrs"].get("aria-disabled", "") == "true"
            or (
                ancestor["tag"] == "fieldset"
                and "disabled" in ancestor["attrs"]
            )
            for ancestor in ancestors
        )
        effectively_disabled = (
            "disabled" in values
            or "inert" in values
            or values.get("aria-hidden", "").casefold() == "true"
            or values.get("aria-disabled", "") == "true"
            or disabled_ancestor
        )
        for attribute in (
            "accesskey", "autofocus", "closedby", "command", "commandfor", "contenteditable",
            "draggable", "for", "interestfor", "placeholder", "popover",
            "popovertarget", "popovertargetaction", "title",
        ):
            if attribute in values:
                self.unsupported_interactive_attributes.append(
                    f"{tag}[{attribute}]"
                )
        if "attributionsrc" in values:
                self.unsupported_interactive_attributes.append(
                    f"{tag}[attributionsrc]"
                )
        if "hidden" in values and values["hidden"] not in {"", "hidden"}:
            self.invalid_hidden_values.append(f"{tag}[hidden={values['hidden']}]")
        live_attributes = {
            "aria-atomic", "aria-busy", "aria-live", "aria-relevant",
        } & values.keys()
        if values.get("id") != "application-announcer" and (
            values.get("role") == "status"
            or live_attributes
            or tag == "output"
        ):
            self.unmanaged_live_regions.append(
                f"{tag}[{','.join(sorted(live_attributes or {'native-live'}))}]"
            )
        if tag in {"meter", "progress"}:
            self.unmanaged_native_widgets.append(tag)
        for attribute in sorted(FORBIDDEN_PRESENTATIONAL_ATTRIBUTES & values.keys()):
            self.forbidden_presentational_attributes.append(
                f"{tag}[{attribute}]"
            )
        for attribute in ("id", "name"):
            if values.get(attribute) in RUNTIME_RESERVED_NAMED_PROPERTIES:
                self.runtime_named_property_conflicts.append(
                    f"{tag}[{attribute}={values[attribute]}]"
                )
        if tag == "form" and "name" in values:
            self.runtime_named_property_conflicts.append("form[name]")
        parent_id = parent.get("id", "")
        raw_class = values.get("class", "")
        if has_forbidden_label_codepoint(raw_class):
            self.invalid_class_attributes.append(f"{tag}[class]")
        for class_name in split_ascii_space_tokens(raw_class):
            self.class_uses.setdefault(class_name, []).append({
                "tag": tag,
                "attrs": dict(values),
                "parent_tag": parent_tag,
                "parent_id": parent_id,
            })
        self.element_counts[tag] = self.element_counts.get(tag, 0) + 1
        self.element_parents.setdefault(tag, []).append((parent_tag, parent_id))
        if not parent_tag:
            self.top_level_elements.append(tag)
        if parent_tag == "html":
            self.html_children.append(tag)
        if tag == "html":
            self.html_attributes.append(dict(values))
        element = {
            "tag": tag,
            "id": values.get("id", ""),
            "position": self.position,
            "attrs": values,
            "route": self._route,
            "text_parts": [],
            "intrinsic_text_parts": [],
            "accessible_text_parts": [],
            "accessible_name_chunks": [],
            "direct_child_tags": [],
            "label_caption_parts": [],
        }
        if RUNTIME_MUTABLE_LABEL_ATTRIBUTES.intersection(values):
            for ancestor in ancestors:
                if ancestor.get("label_row") is not None:
                    ancestor["label_row"]["runtime_mutable_context"] = True
        is_labelable = tag in LABELABLE_ELEMENTS and not (
            tag == "input" and values.get("type", "text").casefold() == "hidden"
        )
        if is_labelable:
            for ancestor in ancestors:
                if ancestor.get("label_row") is not None:
                    ancestor["label_row"]["labelable_descendants"] += 1
        if tag == "label":
            label_row = {
                "position": self.position,
                "labelable_descendants": 0,
                "caption": "",
                "runtime_mutable_context": runtime_mutable_label_context,
                "invalid_caption_context": False,
            }
            element["label_row"] = label_row
            self.labels.append(label_row)
        if tag == "optgroup":
            optgroup_row = {
                "position": self.position,
                "parent_tag": parent_tag,
                "has_label": "label" in values,
                "label": values.get("label", ""),
                "hidden": "hidden" in values,
                "disabled": "disabled" in values,
                "direct_options": 0,
            }
            element["optgroup_row"] = optgroup_row
            self.native_optgroups.append(optgroup_row)
        if tag == "option" and parent.get("optgroup_row") is not None:
            parent["optgroup_row"]["direct_options"] += 1
        if "aria-description" in values or "aria-describedby" in values:
            self.passive_descriptions.append({
                "id": values.get("id", ""),
                "position": self.position,
                "tag": tag,
                "route": self._route,
                "hidden_ancestor_ids": hidden_ancestor_ids,
                "dialog_ancestor_ids": dialog_ancestor_ids,
                "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
                "aria_description": values.get("aria-description", ""),
                "has_aria_description": "aria-description" in values,
                "aria_describedby": values.get("aria-describedby", ""),
                "has_aria_describedby": "aria-describedby" in values,
            })
        for ancestor in ancestors:
            listbox_row = ancestor.get("listbox_row")
            if listbox_row is None:
                continue
            listbox_row["descendant_positions"].append(self.position)
            if ancestor is parent:
                listbox_row["direct_child_positions"].append(self.position)
        for ancestor in ancestors:
            if ancestor.get("id_row") is not None:
                ancestor["id_row"]["_has_element_descendant_internal"] = True
        for ancestor in ancestors:
            if ancestor.get("runtime_text_sink") is not None:
                ancestor["runtime_text_sink"]["has_element_descendant"] = True
            if ancestor.get("private_target") is not None:
                ancestor["private_target"]["has_element_descendant"] = True
        if (
            "data-application-outcome" in values
            or values.get("id") == "application-announcer"
        ):
            runtime_text_sink = {
                "id": values.get("id", ""),
                "tag": tag,
                "attrs": dict(values),
                "type": values.get("type", ""),
                "in_body": in_body,
                "route": self._route,
                "marker_value": values.get("data-application-outcome", ""),
                "text": "",
                "has_element_descendant": False,
                "hidden": "hidden" in values,
                "disabled": effectively_disabled,
                "hidden_ancestor_ids": hidden_ancestor_ids,
                "dialog_ancestor_ids": dialog_ancestor_ids,
                "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
            }
            element["runtime_text_sink"] = runtime_text_sink
            if "data-application-outcome" in values:
                self.outcome_targets.append(runtime_text_sink)
            if values.get("id") == "application-announcer":
                self.announcer_sinks.append(runtime_text_sink)
        if tag == "img":
            self.images.append({
                "position": self.position,
                "in_body": in_body,
                "has_alt": "alt" in values,
                "alt": values.get("alt", ""),
                "has_src": "src" in values,
                "src": values.get("src", ""),
            })
        native_interactive = (
            tag == "button"
            or (
                tag == "input"
                and values.get("type", "text").casefold() != "hidden"
            )
            or tag in {"select", "textarea"}
            or (tag == "a" and "href" in values)
            or (tag in {"audio", "video"} and "controls" in values)
            or (
                "contenteditable" in values
                and values.get("contenteditable", "").casefold() != "false"
            )
            or "tabindex" in values
            or values.get("role", "").casefold() in ARIA_WIDGET_ROLES
        )
        if native_interactive:
            self.interactive_elements.append({
                "position": self.position,
                "tag": tag,
                "type": values.get("type", ""),
                "in_body": in_body,
            })
        if tag == "title":
            title_row = {"text": "", "parent_tag": parent_tag}
            self.titles.append(title_row)
            element["title_row"] = title_row
        if tag == "form" and (
            values.get("data-transition-ref") or values.get("data-simulation-id")
        ):
            routed_form = {
                "id": values.get("id", ""),
                "tag": tag,
                "type": values.get("type", ""),
                "position": self.position,
                "in_body": in_body,
                "route": self._route,
                "submit_affordances": [],
                "fields": [],
                "native_overrides": sorted(
                    name for name in (
                        "accept-charset", "action", "enctype", "method",
                        "novalidate", "target",
                    ) if name in values
                ),
                "has_tabindex": "tabindex" in values,
                "hidden": "hidden" in values,
                "disabled": effectively_disabled,
                "hidden_ancestor_ids": hidden_ancestor_ids,
                "dialog_ancestor_ids": dialog_ancestor_ids,
                "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
            }
            element["routed_form"] = routed_form
            self.routed_forms.append(routed_form)
        if parent_tag == "head":
            self.head_children.append({"tag": tag, "attrs": values, "position": self.position})
        if parent_tag == "body":
            self.body_children.append({"tag": tag, "attrs": values, "position": self.position})
        child = {"tag": tag, "attrs": dict(values), "position": self.position}
        scaffold_rows: list[dict] = []
        if parent.get("id") == "application-main":
            self.main_children.append(child)
        if (
            parent.get("tag") == "header"
            and parent.get("attrs", {}).get("class")
            == "application-shell application-toolbar"
        ):
            self.toolbar_children.append(child)
            scaffold_rows.append(child)
        if (
            parent.get("tag") == "div"
            and parent.get("attrs", {}).get("class") == "application-actions"
        ):
            self.action_children.append(child)
            scaffold_rows.append(child)
        if scaffold_rows:
            element["scaffold_rows"] = scaffold_rows
        previous_route = self._route
        if tag not in VOID_ELEMENTS:
            self._elements.append(element)
            self._stack.append((tag, previous_route))
        if "data-application-route" in values:
            self._route = values["data-application-route"]
            element["route"] = self._route
            if element.get("routed_form") is not None:
                element["routed_form"]["route"] = self._route
            self.route_views[self._route] = self.route_views.get(self._route, 0) + 1
            self.route_parents.setdefault(self._route, []).append(
                (parent_tag, parent_id)
            )
            self.route_states.setdefault(self._route, []).append(
                values.get("data-application-state", "")
            )
            self.route_labels.setdefault(self._route, []).append(
                values.get("aria-labelledby", "")
            )
            self.route_targets.setdefault(self._route, []).append({
                "id": values.get("id", ""),
                "tag": tag,
                "type": values.get("type", ""),
                "role": values.get("role", ""),
                "aria_attributes": {
                    name for name in values if name.startswith("aria-")
                },
                "in_body": in_body,
                "route": self._route,
                "hidden": False,
                "disabled": effectively_disabled,
                "hidden_ancestor_ids": hidden_ancestor_ids,
                "dialog_ancestor_ids": dialog_ancestor_ids,
                "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
            })
        reference = values.get("data-experience-ref", "")
        if reference:
            self.record_routes.setdefault(reference, set()).add(self._route)
            record_target = {
                "id": values.get("id", ""),
                "tag": tag,
                "type": values.get("type", ""),
                "in_body": in_body,
                "value": authored_visible_value(tag, values),
                "text": "",
                "route": self._route,
                "hidden": "hidden" in values,
                "disabled": effectively_disabled,
                "hidden_ancestor_ids": hidden_ancestor_ids,
                "dialog_ancestor_ids": dialog_ancestor_ids,
                "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
            }
            self.record_targets.setdefault(reference, []).append(record_target)
            element["record_target"] = record_target
        has_context_key = "data-context-key" in values
        context_key = values.get("data-context-key", "")
        if has_context_key:
            self.context_sources.setdefault(self._route, set()).add(context_key)
            self.context_targets.setdefault(self._route, set()).add(context_key)
        identity_attributes = {
            "data-transition-ref", "data-simulation-id", "data-application-action",
        }
        routing_attributes = {
            "data-route-target", "data-preserve-context", "data-return-route",
        }
        if not identity_attributes.intersection(values) \
                and routing_attributes.intersection(values):
            self.orphan_routing_attributes.append(
                f"{tag}[{','.join(sorted(routing_attributes.intersection(values)))}]"
            )
        if (
            "data-transition-ref" in values
            or "data-simulation-id" in values
            or "data-application-action" in values
        ):
            control = {
                    "id": values.get("id", ""),
                    "position": self.position,
                    "tag": tag,
                    "in_body": in_body,
                    "route": self._route,
                    "target": values.get("data-route-target", ""),
                    "has_route_target": "data-route-target" in values,
                    "transition_ref": values.get("data-transition-ref", ""),
                    "has_transition_ref": "data-transition-ref" in values,
                    "preserve_context": values.get("data-preserve-context", ""),
                    "has_preserve_context": "data-preserve-context" in values,
                    "return_route": values.get("data-return-route", ""),
                    "has_return_route": "data-return-route" in values,
                    "action": values.get("data-application-action", ""),
                    "has_action": "data-application-action" in values,
                    "simulation_id": values.get("data-simulation-id", ""),
                    "has_simulation_id": "data-simulation-id" in values,
                    "type": values.get("type", ""),
                    "tabindex": values.get("tabindex", ""),
                    "href": values.get("href", ""),
                    "aria_controls": values.get("aria-controls", ""),
                    "aria_haspopup": values.get("aria-haspopup", ""),
                    "aria_expanded": values.get("aria-expanded", ""),
                    "aria_pressed": values.get("aria-pressed", ""),
                    "aria_selected": values.get("aria-selected", ""),
                    "aria_label": values.get("aria-label", ""),
                    "aria_labelledby": values.get("aria-labelledby", ""),
                    "has_aria_label": "aria-label" in values,
                    "has_aria_labelledby": "aria-labelledby" in values,
                    "aria_attributes": {
                        name for name in values if name.startswith("aria-")
                    },
                    "value": values.get("value", ""),
                    "has_value": "value" in values,
                    "text": "",
                    "role": values.get("role", ""),
                    "context_key": context_key,
                    "data_value": values.get("data-value", ""),
                    "hidden": "hidden" in values,
                    "disabled": effectively_disabled,
                    "ancestor_ids": ancestor_ids,
                    "ancestor_positions": ancestor_positions,
                    "hidden_ancestor_ids": hidden_ancestor_ids,
                    "dialog_ancestor_ids": dialog_ancestor_ids,
                    "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                    "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
                    "dialog_ancestors": [
                        {
                            "id": ancestor["id"],
                            "route": ancestor["route"],
                            "hidden": "hidden" in ancestor["attrs"],
                            "permanently_hidden_ancestor": any(
                                "hidden" in outer["attrs"]
                                and "data-application-route" not in outer["attrs"]
                                for outer in ancestors[:ancestor_index]
                            ),
                        }
                        for ancestor_index, ancestor in enumerate(ancestors)
                        if ancestor["tag"] == "dialog"
                    ],
                    "label_ancestor_positions": [
                        ancestor["position"] for ancestor in ancestors
                        if ancestor["tag"] == "label"
                    ],
                    "listbox_ancestors": [
                        {
                            "id": ancestor["id"],
                            "route": ancestor["route"],
                            "hidden": "hidden" in ancestor["attrs"],
                            "permanently_hidden_ancestor": any(
                                "hidden" in outer["attrs"]
                                and "data-application-route" not in outer["attrs"]
                                for outer in ancestors[:ancestor_index]
                            ),
                        }
                        for ancestor_index, ancestor in enumerate(ancestors)
                        if ancestor["attrs"].get("role") == "listbox"
                    ],
                }
            self.controls.append(control)
            element["control"] = control
        if tag == "a" and values.get("href"):
            self.fragment_anchors.append({
                "href": values.get("href", ""),
                "transition_ref": values.get("data-transition-ref", ""),
                "simulation_id": values.get("data-simulation-id", ""),
                "action": values.get("data-application-action", ""),
                "route": self._route,
            })
        if tag == "a" and values.get("href") == "#application-main":
            skip_link = {
                "attrs": dict(values),
                "tag": tag,
                "type": values.get("type", ""),
                "role": values.get("role", ""),
                "position": self.position,
                "in_body": in_body,
                "href": values.get("href", ""),
                "class": values.get("class", ""),
                "route": self._route,
                "text": "",
                "tabindex": values.get("tabindex", ""),
                "label_ancestor_positions": label_ancestor_positions,
                "disabled": effectively_disabled,
            }
            self.skip_links.append(skip_link)
            element["skip_link"] = skip_link
        if tag == "form" and not (
            values.get("data-transition-ref") or values.get("data-simulation-id")
        ):
            self.forms_without_route += 1
        element_id = values.get("id", "")
        if element_id:
            self.element_ids[element_id] = self.element_ids.get(element_id, 0) + 1
            id_row = {
                "tag": tag,
                **values,
                "_in_body_internal": in_body,
                "_application_route_internal": self._route,
                "_position_internal": self.position,
                "_ancestor_ids_internal": ancestor_ids,
                "_ancestor_positions_internal": ancestor_positions,
                "_hidden_ancestor_ids_internal": hidden_ancestor_ids,
                "_dialog_ancestor_ids_internal": dialog_ancestor_ids,
                "_anonymous_hidden_ancestor_internal": anonymous_hidden_ancestor,
                "_anonymous_dialog_ancestor_internal": anonymous_dialog_ancestor,
                "_disabled_internal": effectively_disabled,
                "_runtime_mutable_label_internal": (
                    runtime_mutable_label_context
                ),
                "_text_internal": "",
                "_has_element_descendant_internal": False,
            }
            self.elements_by_id.setdefault(element_id, []).append(id_row)
            element["id_row"] = id_row
        routed_form_ancestor = next(
            (
                ancestor.get("routed_form") for ancestor in reversed(ancestors)
                if ancestor.get("routed_form") is not None
            ),
            None,
        )
        form_field = (
            tag in {"select", "textarea"}
            or (
                tag == "input"
                and values.get("type", "text") not in {
                    "button", "hidden", "image", "reset", "submit",
                }
            )
        )
        if routed_form_ancestor is not None and form_field:
            field_row = {
                "position": self.position,
                "in_body": in_body,
                "tag": tag,
                "type": values.get("type", "text" if tag == "input" else ""),
                "role": values.get("role", ""),
                "aria_label": values.get("aria-label", ""),
                "aria_labelledby": values.get("aria-labelledby", ""),
                "has_aria_label": "aria-label" in values,
                "has_aria_labelledby": "aria-labelledby" in values,
                "aria_attributes": {
                    name for name in values if name.startswith("aria-")
                },
                "form_override": "form" in values,
                "tabindex": values.get("tabindex", ""),
                "label_ancestor_positions": label_ancestor_positions,
                "hidden": "hidden" in values,
                "disabled": effectively_disabled,
                "hidden_ancestor_ids": hidden_ancestor_ids,
                "dialog_ancestor_ids": dialog_ancestor_ids,
                "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
                "constraints": {
                    name: values.get(name, "")
                    for name in (
                        "max", "maxlength", "min", "minlength", "multiple",
                        "pattern", "required", "step",
                    )
                    if name in values
                },
                "options": [],
            }
            routed_form_ancestor["fields"].append(field_row)
            element["form_field"] = field_row
        enabled_submit = (
            tag == "button" and values.get("type", "submit") == "submit"
        ) or (
            tag == "input" and values.get("type", "text") == "submit"
        )
        if (
            routed_form_ancestor is not None
            and enabled_submit
            and "disabled" not in values
            and "hidden" not in values
        ):
            submit_affordance = {
                "id": values.get("id", ""),
                "position": self.position,
                "in_body": in_body,
                "tag": tag,
                "type": values.get("type", "submit" if tag == "button" else ""),
                "action": values.get("data-application-action", ""),
                "transition_ref": values.get("data-transition-ref", ""),
                "simulation_id": values.get("data-simulation-id", ""),
                "route_target": values.get("data-route-target", ""),
                "preserve_context": values.get("data-preserve-context", ""),
                "return_route": values.get("data-return-route", ""),
                "form_override": any(
                    name in values for name in (
                        "form", "formaction", "formenctype", "formmethod",
                        "formtarget", "formnovalidate",
                    )
                ),
                "aria_label": values.get("aria-label", ""),
                "aria_labelledby": values.get("aria-labelledby", ""),
                "has_aria_label": "aria-label" in values,
                "has_aria_labelledby": "aria-labelledby" in values,
                "aria_attributes": {
                    name for name in values if name.startswith("aria-")
                },
                "role": values.get("role", ""),
                "value": authored_visible_value(tag, values),
                "has_value": "value" in values,
                "alt": values.get("alt", ""),
                "tabindex": values.get("tabindex", ""),
                "label_ancestor_positions": label_ancestor_positions,
                "text": "",
                "hidden": False,
                "disabled": effectively_disabled,
                "hidden_ancestor_ids": hidden_ancestor_ids,
                "dialog_ancestor_ids": dialog_ancestor_ids,
                "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
            }
            routed_form_ancestor["submit_affordances"].append(submit_affordance)
            element["submit_affordance"] = submit_affordance
        if "data-application-search" in values:
            self.search_controls.append({
                "id": values.get("id", ""),
                "position": self.position,
                "ancestor_positions": ancestor_positions,
                "in_body": in_body,
                "tag": tag,
                "type": values.get("type", ""),
                "readonly": "readonly" in values,
                "aria_readonly": values.get("aria-readonly", ""),
                "tabindex": values.get("tabindex", ""),
                "label_ancestor_positions": label_ancestor_positions,
                "route": self._route,
                "hidden": "hidden" in values,
                "disabled": effectively_disabled,
                "aria_label": values.get("aria-label", ""),
                "aria_labelledby": values.get("aria-labelledby", ""),
                "has_aria_label": "aria-label" in values,
                "has_aria_labelledby": "aria-labelledby" in values,
                "aria_attributes": {
                    name for name in values if name.startswith("aria-")
                },
                "role": values.get("role", ""),
                "hidden_ancestor_ids": hidden_ancestor_ids,
                "dialog_ancestor_ids": dialog_ancestor_ids,
                "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
            })
        if "data-application-filter" in values:
            filter_control = {
                "id": values.get("id", ""),
                "position": self.position,
                "ancestor_positions": ancestor_positions,
                "in_body": in_body,
                "tag": tag,
                "route": self._route,
                "multiple": "multiple" in values,
                "tabindex": values.get("tabindex", ""),
                "label_ancestor_positions": label_ancestor_positions,
                "aria_label": values.get("aria-label", ""),
                "aria_labelledby": values.get("aria-labelledby", ""),
                "has_aria_label": "aria-label" in values,
                "has_aria_labelledby": "aria-labelledby" in values,
                "aria_attributes": {
                    name for name in values if name.startswith("aria-")
                },
                "role": values.get("role", ""),
                "hidden": "hidden" in values,
                "disabled": effectively_disabled,
                "hidden_ancestor_ids": hidden_ancestor_ids,
                "dialog_ancestor_ids": dialog_ancestor_ids,
                "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
            }
            self.filter_controls.append(filter_control)
            element["filter_control"] = filter_control
        filter_control_ancestor = next((
            ancestor.get("filter_control") for ancestor in reversed(ancestors)
            if ancestor.get("filter_control") is not None
        ), None)
        form_select_ancestor = next((
            ancestor.get("form_field") for ancestor in reversed(ancestors)
            if ancestor.get("form_field") is not None
            and ancestor.get("form_field", {}).get("tag") == "select"
        ), None)
        if tag == "option" and form_select_ancestor is not None:
            form_option = {
                "has_value": "value" in values,
                "value": values.get("value", ""),
                "has_label": "label" in values,
                "label": values.get("label", ""),
                "text": "",
                "hidden": "hidden" in values or any(
                    ancestor["tag"] == "optgroup"
                    and "hidden" in ancestor["attrs"]
                    for ancestor in ancestors
                ),
                "disabled": effectively_disabled or any(
                    ancestor["tag"] == "optgroup"
                    and "disabled" in ancestor["attrs"]
                    for ancestor in ancestors
                ),
            }
            form_select_ancestor["options"].append(form_option)
            element["form_option"] = form_option
        if tag == "option" and filter_control_ancestor is not None:
            filter_option = {
                "filter_position": filter_control_ancestor["position"],
                "has_value": "value" in values,
                "value": values.get("value", ""),
                "has_label": "label" in values,
                "label": values.get("label", ""),
                "text": "",
                "hidden": "hidden" in values or any(
                    ancestor["tag"] == "optgroup"
                    and "hidden" in ancestor["attrs"]
                    for ancestor in ancestors
                ),
                "disabled": effectively_disabled or any(
                    ancestor["tag"] == "optgroup"
                    and "disabled" in ancestor["attrs"]
                    for ancestor in ancestors
                ),
            }
            self.filter_options.append(filter_option)
            element["filter_option"] = filter_option
        if "data-search-item" in values:
            search_item = {
                "id": values.get("id", ""),
                "position": self.position,
                "ancestor_positions": ancestor_positions,
                "tag": tag,
                "type": values.get("type", ""),
                "in_body": in_body,
                "value": values.get("value", ""),
                "text": "",
                "route": self._route,
                "hidden": "hidden" in values,
                "disabled": effectively_disabled,
                "hidden_ancestor_ids": hidden_ancestor_ids,
                "dialog_ancestor_ids": dialog_ancestor_ids,
                "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
            }
            self.search_items.append(search_item)
            element["search_item"] = search_item
        if "data-filter-item" in values:
            filter_item = {
                "id": values.get("id", ""),
                "position": self.position,
                "ancestor_positions": ancestor_positions,
                "tag": tag,
                "type": values.get("type", ""),
                "in_body": in_body,
                "value": values.get("value", ""),
                "text": "",
                "route": self._route,
                "filter_value": values.get("data-filter-value", ""),
                "hidden": "hidden" in values,
                "disabled": effectively_disabled,
                "hidden_ancestor_ids": hidden_ancestor_ids,
                "dialog_ancestor_ids": dialog_ancestor_ids,
                "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
            }
            self.filter_items.append(filter_item)
            element["filter_item"] = filter_item
        for attribute, collection in (
            ("data-application-onboarding", self.onboarding_targets),
            ("data-application-settings", self.settings_targets),
        ):
            if attribute in values:
                collection.append({
                    "id": values.get("id", ""),
                    "tag": tag,
                    "type": values.get("type", ""),
                    "in_body": in_body,
                    "route": self._route,
                    "hidden": "hidden" in values,
                    "disabled": effectively_disabled,
                    "hidden_ancestor_ids": hidden_ancestor_ids,
                    "dialog_ancestor_ids": dialog_ancestor_ids,
                    "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                    "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
                })
        if "data-private" in values:
            private_target = {
                "id": values.get("id", ""),
                "position": self.position,
                "ancestor_positions": ancestor_positions,
                "tag": tag,
                "type": values.get("type", ""),
                "in_body": in_body,
                "value": authored_visible_value(tag, values),
                "text": "",
                "route": self._route,
                "hidden": "hidden" in values,
                "disabled": effectively_disabled,
                "hidden_ancestor_ids": hidden_ancestor_ids,
                "dialog_ancestor_ids": dialog_ancestor_ids,
                "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
                "has_element_descendant": False,
                "identity_ancestor": any(
                    ancestor["tag"] in {
                        "a", "button", "h1", "h2", "h3", "h4", "h5",
                        "h6", "input", "label", "legend", "select",
                        "textarea",
                    }
                    for ancestor in ancestors
                ),
            }
            self.private_targets.append(private_target)
            element["private_target"] = private_target
        if has_context_key:
            context_constraint_names = (
                "max", "maxlength", "min", "minlength", "multiple",
                "pattern", "readonly", "required", "step",
            )
            native_form_owner_position = next(
                (
                    ancestor["position"]
                    for ancestor in reversed(ancestors)
                    if ancestor["tag"] == "form"
                ),
                0,
            )
            context_element = {
                "key": context_key,
                "tag": tag,
                "type": values.get("type", ""),
                "role": values.get("role", ""),
                "multiple": "multiple" in values,
                "route": self._route,
                "value": values.get("value", ""),
                "name": values.get("name", ""),
                "form_owner_position": native_form_owner_position,
                "aria_label": values.get("aria-label", ""),
                "aria_labelledby": values.get("aria-labelledby", ""),
                "has_aria_label": "aria-label" in values,
                "has_aria_labelledby": "aria-labelledby" in values,
                "aria_attributes": {
                    name for name in values if name.startswith("aria-")
                },
                "checked": "checked" in values,
                "constraints": tuple(
                    (name, name in values, values.get(name, ""))
                    for name in context_constraint_names
                ),
                "position": self.position,
                "tabindex": values.get("tabindex", ""),
                "label_ancestor_positions": label_ancestor_positions,
                "in_body": in_body,
                "disabled": effectively_disabled,
                "hidden": "hidden" in values,
                "hidden_ancestor_ids": hidden_ancestor_ids,
                "dialog_ancestor_ids": dialog_ancestor_ids,
                "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
            }
            self.context_elements.append(context_element)
            element["context_element"] = context_element
        if tag == "input" and values.get("type", "text").casefold() == "radio":
            native_form_owner_position = next(
                (
                    ancestor["position"]
                    for ancestor in reversed(ancestors)
                    if ancestor["tag"] == "form"
                ),
                0,
            )
            self.native_radios.append({
                "position": self.position,
                "route": self._route,
                "context_key": context_key,
                "has_context_key": has_context_key,
                "name": values.get("name", ""),
                "form_owner_position": native_form_owner_position,
            })
        context_ancestor = next((
            ancestor.get("context_element") for ancestor in reversed(ancestors)
            if ancestor.get("context_element") is not None
            and ancestor["tag"] == "select"
        ), None)
        if tag == "option" and context_ancestor is not None:
            context_option = {
                "context_position": context_ancestor["position"],
                "has_value": "value" in values,
                "value": values.get("value", ""),
                "has_label": "label" in values,
                "label": values.get("label", ""),
                "text": "",
                "hidden": "hidden" in values or any(
                    ancestor["tag"] == "optgroup"
                    and "hidden" in ancestor["attrs"]
                    for ancestor in ancestors
                ),
                "disabled": effectively_disabled or any(
                    ancestor["tag"] == "optgroup"
                    and "disabled" in ancestor["attrs"]
                    for ancestor in ancestors
                ),
            }
            self.context_options.append(context_option)
            element["context_option"] = context_option
        if values.get("role") == "listbox":
            listbox_row = {
                "id": values.get("id", ""),
                "tag": tag,
                "type": values.get("type", ""),
                "in_body": in_body,
                "position": self.position,
                "route": self._route,
                "aria_label": values.get("aria-label", ""),
                "aria_labelledby": values.get("aria-labelledby", ""),
                "has_aria_label": "aria-label" in values,
                "has_aria_labelledby": "aria-labelledby" in values,
                "aria_multiselectable": values.get(
                    "aria-multiselectable", ""
                ),
                "aria_activedescendant": values.get(
                    "aria-activedescendant", ""
                ),
                "aria_orientation": values.get("aria-orientation", ""),
                "aria_attributes": {
                    name for name in values if name.startswith("aria-")
                },
                "has_tabindex": "tabindex" in values,
                "label_ancestor_positions": label_ancestor_positions,
                "text": "",
                "hidden": "hidden" in values,
                "disabled": effectively_disabled,
                "hidden_ancestor_ids": hidden_ancestor_ids,
                "dialog_ancestor_ids": dialog_ancestor_ids,
                "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
                "descendant_positions": [],
                "direct_child_positions": [],
            }
            self.listboxes.append(listbox_row)
            element["listbox_row"] = listbox_row
        if values.get("role") == "option":
            option_row = {
                "id": values.get("id", ""),
                "position": self.position,
                "tag": tag,
                "type": values.get("type", ""),
                "in_body": in_body,
                "route": self._route,
                "action": values.get("data-application-action", ""),
                "aria_selected": values.get("aria-selected", ""),
                "aria_label": values.get("aria-label", ""),
                "aria_labelledby": values.get("aria-labelledby", ""),
                "has_aria_label": "aria-label" in values,
                "has_aria_labelledby": "aria-labelledby" in values,
                "data_value": values.get("data-value", ""),
                "value": values.get("value", ""),
                "text": "",
                "listbox_ancestor_positions": tuple(
                    ancestor["position"] for ancestor in ancestors
                    if ancestor["attrs"].get("role") == "listbox"
                ),
                "hidden": "hidden" in values,
                "disabled": effectively_disabled,
                "hidden_ancestor_ids": hidden_ancestor_ids,
                "dialog_ancestor_ids": dialog_ancestor_ids,
                "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
            }
            self.listbox_options.append(option_row)
            element["option_row"] = option_row
        if tag == "dialog":
            self.dialogs.append({
                "id": values.get("id", ""),
                "tag": tag,
                "type": values.get("type", ""),
                "in_body": in_body,
                "position": self.position,
                "route": self._route,
                "open": "open" in values,
                "hidden": "hidden" in values,
                "disabled": effectively_disabled,
                "aria_label": values.get("aria-label", ""),
                "aria_labelledby": values.get("aria-labelledby", ""),
                "has_aria_label": "aria-label" in values,
                "has_aria_labelledby": "aria-labelledby" in values,
                "aria_modal": values.get("aria-modal", ""),
                "has_aria_modal": "aria-modal" in values,
                "aria_attributes": {
                    name for name in values if name.startswith("aria-")
                },
                "text": "",
                "hidden_ancestor_ids": hidden_ancestor_ids,
                "dialog_ancestor_ids": dialog_ancestor_ids,
                "anonymous_hidden_ancestor": anonymous_hidden_ancestor,
                "anonymous_dialog_ancestor": anonymous_dialog_ancestor,
            })
        for key, value in values.items():
            if key.startswith("on"):
                self.inline_handlers.append(key)
            if key in {
                "action", "archive", "background", "cite", "classid", "codebase",
                "data", "formaction", "href", "icon", "longdesc", "manifest",
                "ping", "poster", "profile", "src", "srcset", "usemap",
                "xlink:href",
            }:
                self.targets.append((key, value))
            if key == "style":
                self.inline_styles.append(value)
                if value:
                    self.styles.append(value)
            if key in PRESENTATION_URL_ATTRIBUTES and re.search(
                r"url\s*\(", normalized_css(value), re.I
            ):
                self.presentation_urls.append((key, value))
        if (
            tag in FORBIDDEN_BROWSER_UNSTABLE_ELEMENTS
            or tag not in ALLOWED_APPLICATION_ELEMENTS
        ):
            self.forbidden_elements.append(tag)
        if tag in SMIL_MUTATION_ELEMENTS:
            self.smil_mutations.append(tag)
        if tag == "meta" and values.get("name"):
            name = values["name"].casefold()
            self.metas[name] = values.get("content", "")
            self.meta_counts[name] = self.meta_counts.get(name, 0) + 1
        if tag == "meta" and values.get("http-equiv"):
            directive = values["http-equiv"].casefold()
            self.http_equivs.append(directive)
            if directive == "content-security-policy":
                self.csp_values.append(values.get("content", ""))
            self.meta_refresh = directive == "refresh" or self.meta_refresh
        if tag == "script":
            self._script = {
                "attrs": values,
                "body": "",
                "parent_tag": parent_tag,
                "parent_id": parent_id,
                "position": self.position,
            }
        if tag == "style":
            self._style = ""
        if tag in VOID_ELEMENTS:
            self._route = previous_route

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script["body"] += data
        if self._style is not None:
            self._style += data
        for element in self._elements:
            element["text_parts"].append(data)
        for index, element in enumerate(self._elements):
            if element.get("label_row") is not None and not any(
                descendant["tag"] in LABELABLE_ELEMENTS
                and not (
                    descendant["tag"] == "input"
                    and descendant["attrs"].get("type", "text").casefold()
                    == "hidden"
                )
                for descendant in self._elements[index + 1:]
            ):
                caption_path = self._elements[index + 1:]
                if any(
                    descendant["tag"] in INTRINSICALLY_NON_RENDERED_ELEMENTS
                    or "hidden" in descendant["attrs"]
                    or "inert" in descendant["attrs"]
                    or descendant["attrs"].get(
                        "aria-hidden", ""
                    ).casefold() == "true"
                    for descendant in caption_path
                ):
                    if normalize_ascii_whitespace(data):
                        element["label_row"]["invalid_caption_context"] = True
                else:
                    element["label_caption_parts"].append(data)
        intrinsic_blocked = any(
            element["tag"] in INTRINSICALLY_NON_RENDERED_ELEMENTS
            or (
                element["tag"] == "input"
                and element["attrs"].get("type", "").casefold() == "hidden"
            )
            for element in self._elements
        )
        if not intrinsic_blocked:
            for element in self._elements:
                element["intrinsic_text_parts"].append(data)
        name_blocked = any(
            "inert" in element["attrs"]
            or element["attrs"].get("aria-hidden", "").casefold() == "true"
            for element in self._elements
        ) or intrinsic_blocked
        if not name_blocked:
            hidden_elements = [
                element for element in self._elements
                if "hidden" in element["attrs"]
                and "data-application-route" not in element["attrs"]
            ]
            chunk = {
                "text": data,
                "hidden_ids": tuple(
                    element["id"] for element in hidden_elements
                    if element["id"]
                ),
                "anonymous_hidden": any(
                    not element["id"] for element in hidden_elements
                ),
            }
            for element in self._elements:
                element["accessible_name_chunks"].append(chunk)
        if not intrinsic_blocked and not any(
            "hidden" in element["attrs"]
            or "inert" in element["attrs"]
            or element["attrs"].get("aria-hidden", "").casefold() == "true"
            for element in self._elements
        ):
            for element in self._elements:
                element["accessible_text_parts"].append(data)
        parent_tag = self._elements[-1]["tag"] if self._elements else ""
        parent = self._elements[-1] if self._elements else {}
        if normalize_ascii_whitespace(data) and (
            parent.get("id") == "application-main"
            or (
                parent.get("tag") == "header"
                and parent.get("attrs", {}).get("class")
                == "application-shell application-toolbar"
            )
            or (
                parent.get("tag") == "div"
                and parent.get("attrs", {}).get("class")
                == "application-actions"
            )
        ):
            self.scaffold_direct_text.append(parent.get("tag", ""))
        if normalize_ascii_whitespace(data) and parent_tag in NON_TEXT_DIRECT_PARENTS:
            self.parser_reparenting_risks.append(f"{parent_tag}>#text")
        if (
            self._script is None
            and self._style is None
            and normalize_ascii_whitespace(data)
            and parent_tag in {"", "html", "body"}
        ):
            self.structural_text.append(parent_tag or "document")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        self.position += 1
        if tag in VOID_ELEMENTS:
            self.parser_reparenting_risks.append(f"void-end-tag>{tag}")
            return
        if tag == "script" and self._script is not None:
            self._script["end_position"] = self.position
            self.scripts.append(self._script)
            self._script = None
        if tag == "style" and self._style is not None:
            self.styles.append(self._style)
            self._style = None
        element_index = next(
            (
                index for index in range(len(self._elements) - 1, -1, -1)
                if self._elements[index]["tag"] == tag
            ),
            None,
        )
        if element_index is None:
            if tag not in VOID_ELEMENTS:
                self.structural_errors.append(f"unexpected closing </{tag}>")
        else:
            if element_index != len(self._elements) - 1:
                self.structural_errors.append(f"misnested closing </{tag}>")
            closed = self._elements[element_index:]
            for element in closed:
                rendered_text = normalize_ascii_whitespace(
                    "".join(element["text_parts"])
                )
                intrinsic_text = normalize_ascii_whitespace(
                    "".join(element["intrinsic_text_parts"])
                )
                accessible_text = normalize_ascii_whitespace(
                    "".join(element["accessible_text_parts"])
                )
                if element.get("control") is not None:
                    element["control"]["text"] = accessible_text
                    element["control"]["accessible_name_chunks"] = list(
                        element["accessible_name_chunks"]
                    )
                if element.get("label_row") is not None:
                    element["label_row"]["caption"] = normalize_ascii_whitespace(
                        "".join(element["label_caption_parts"])
                    )
                if element.get("submit_affordance") is not None:
                    element["submit_affordance"]["text"] = accessible_text
                    element["submit_affordance"]["accessible_name_chunks"] = list(
                        element["accessible_name_chunks"]
                    )
                if element.get("option_row") is not None:
                    element["option_row"]["text"] = accessible_text
                    element["option_row"]["accessible_name_chunks"] = list(
                        element["accessible_name_chunks"]
                    )
                if element.get("skip_link") is not None:
                    element["skip_link"]["text"] = accessible_text
                    element["skip_link"]["accessible_name_chunks"] = list(
                        element["accessible_name_chunks"]
                    )
                if element.get("private_target") is not None:
                    element["private_target"]["text"] = accessible_text
                if element.get("record_target") is not None:
                    element["record_target"]["text"] = accessible_text
                    element["record_target"]["accessible_name_chunks"] = list(
                        element["accessible_name_chunks"]
                    )
                if element.get("title_row") is not None:
                    element["title_row"]["text"] = rendered_text
                if element.get("search_item") is not None:
                    element["search_item"]["text"] = accessible_text
                    element["search_item"]["accessible_name_chunks"] = list(
                        element["accessible_name_chunks"]
                    )
                if element.get("filter_item") is not None:
                    element["filter_item"]["text"] = accessible_text
                    element["filter_item"]["accessible_name_chunks"] = list(
                        element["accessible_name_chunks"]
                    )
                if element.get("filter_option") is not None:
                    element["filter_option"]["text"] = accessible_text
                if element.get("context_option") is not None:
                    element["context_option"]["text"] = accessible_text
                if element.get("form_option") is not None:
                    element["form_option"]["text"] = accessible_text
                if element.get("runtime_text_sink") is not None:
                    element["runtime_text_sink"]["text"] = rendered_text
                for scaffold_row in element.get("scaffold_rows", []):
                    scaffold_row["text"] = rendered_text
                if element.get("id_row") is not None:
                    element["id_row"]["_text_internal"] = rendered_text
                    element["id_row"]["_intrinsic_text_internal"] = intrinsic_text
                    element["id_row"]["_accessible_text_internal"] = accessible_text
                if element["id"]:
                    self.end_positions[element["id"]] = self.position
            del self._elements[element_index:]
        for index in range(len(self._stack) - 1, -1, -1):
            opened, previous = self._stack[index]
            if opened == tag:
                self._route = previous
                del self._stack[index:]
                break


def document_structure_findings(scanner: ApplicationScanner) -> list[str]:
    findings: list[str] = []
    if scanner.declarations != ["doctype html"]:
        findings.append("application must begin with exactly one HTML5 doctype")
    if (
        scanner.element_counts.get("html") != 1
        or scanner.element_counts.get("head") != 1
        or scanner.element_counts.get("body") != 1
        or scanner.element_parents.get("html") != [("", "")]
        or scanner.element_parents.get("head") != [("html", "")]
        or scanner.element_parents.get("body") != [("html", "")]
        or scanner.top_level_elements != ["html"]
        or scanner.html_children != ["head", "body"]
    ):
        findings.append("application must contain one canonical html/head/body structure")
    root_attrs = scanner.html_attributes[0] if len(scanner.html_attributes) == 1 else {}
    if (
        not valid_language_tag(root_attrs.get("lang", ""))
        or root_attrs.get("data-theme") not in {"light", "dark"}
        or root_attrs.get("data-catalog-theme") != root_attrs.get("data-theme")
        or root_attrs.get("data-privacy") not in {"visible", "masked"}
    ):
        findings.append(
            "application html root needs a valid lang, synchronized exact application/catalog theme, and visible/masked privacy state"
        )
    if scanner.invalid_language_attributes:
        findings.append(
            "application lang/dir attributes must use the closed BCP47 and exact direction grammar: "
            + ", ".join(sorted(set(scanner.invalid_language_attributes)))
        )
    if scanner.structural_errors or scanner._elements:
        findings.append("application HTML elements must be properly nested and closed")
    if scanner.parser_reparenting_risks:
        findings.append(
            "application HTML must use explicit, browser-stable content models: "
            + ", ".join(sorted(set(scanner.parser_reparenting_risks)))
        )
    reserved_class_owners = {
        "application-skip": [{
            "tag": "a",
            "attrs": {"class": "application-skip", "href": "#application-main"},
            "parent_tag": "body",
            "parent_id": "",
        }],
        "application-toolbar": [{
            "tag": "header",
            "attrs": {"class": "application-shell application-toolbar"},
            "parent_tag": "body",
            "parent_id": "",
        }],
        "application-actions": [{
            "tag": "div",
            "attrs": {
                "class": "application-actions",
                "aria-label": "Application preferences",
            },
            "parent_tag": "header",
            "parent_id": "",
        }],
        "application-shell": [
            {
                "tag": "header",
                "attrs": {"class": "application-shell application-toolbar"},
                "parent_tag": "body",
                "parent_id": "",
            },
            {
                "tag": "main",
                "attrs": {
                    "id": "application-main",
                    "class": "application-shell",
                    "tabindex": "-1",
                },
                "parent_tag": "body",
                "parent_id": "",
            },
            {
                "tag": "div",
                "attrs": {
                    "id": "application-announcer",
                    "role": "status",
                    "aria-live": "polite",
                    "class": "application-shell",
                },
                "parent_tag": "body",
                "parent_id": "",
            },
        ],
    }
    if any(
        scanner.class_uses.get(class_name, []) != owners
        for class_name, owners in reserved_class_owners.items()
    ):
        findings.append(
            "fixed application scaffold classes must have only their exact canonical owners"
        )
    invalid_labels = [
        row for row in scanner.labels
        if row["labelable_descendants"] != 1
        or not has_visible_content(row["caption"])
        or has_forbidden_label_codepoint(row["caption"])
        or row["runtime_mutable_context"]
        or row["invalid_caption_context"]
    ]
    if invalid_labels:
        findings.append(
            "every implicit label must have one visible scalar caption and exactly one labelable descendant"
        )
    invalid_optgroups = [
        row for row in scanner.native_optgroups
        if row["parent_tag"] != "select"
        or not row["has_label"]
        or not has_visible_content(row["label"])
        or has_forbidden_label_codepoint(row["label"])
        or row["hidden"]
        or row["disabled"]
        or row["direct_options"] < 1
    ]
    if invalid_optgroups:
        findings.append(
            "every optgroup must be an enabled, visible direct select child with a visible scalar label and at least one direct option"
        )
    if scanner.invalid_aria_disabled:
        findings.append(
            "aria-disabled must use the exact true/false tokens: "
            + ", ".join(sorted(set(scanner.invalid_aria_disabled)))
        )
    if scanner.invalid_aria_hidden:
        findings.append(
            "aria-hidden must use the exact true/false tokens: "
            + ", ".join(sorted(set(scanner.invalid_aria_hidden)))
        )
    if scanner.unsupported_global_aria:
        findings.append(
            "application ARIA attributes must belong to one exact canonical accessibility owner: "
            + ", ".join(sorted(set(scanner.unsupported_global_aria)))
        )
    if scanner.invalid_roles:
        findings.append(
            "application explicit ARIA roles must be exact lowercase canonical "
            "listbox, option or status roles: "
            + ", ".join(sorted(set(scanner.invalid_roles)))
        )
    if scanner.invalid_ids:
        findings.append(
            "application element ids must be normalized single-token HTML/ARIA "
            "identifiers: " + ", ".join(sorted(set(scanner.invalid_ids)))
        )
    if scanner.invalid_class_attributes:
        findings.append(
            "application class token lists may use only ASCII whitespace separators and scalar class names: "
            + ", ".join(sorted(set(scanner.invalid_class_attributes)))
        )
    if scanner.invalid_hidden_values:
        findings.append(
            "application hidden attributes must use only the canonical empty or hidden boolean value: "
            + ", ".join(sorted(set(scanner.invalid_hidden_values)))
        )
    if scanner.invalid_tabindex_values:
        findings.append(
            "application tabindex attributes must be absent, exact 0, or the fixed main exact -1: "
            + ", ".join(sorted(set(scanner.invalid_tabindex_values)))
        )
    if scanner.inline_styles:
        findings.append(
            "inline style attributes are forbidden; use the canonical author-style block"
        )
    if scanner.inline_handlers:
        findings.append(
            "inline event handlers are forbidden; use the fixed declarative runtime"
        )
    if scanner.unmanaged_live_regions:
        findings.append(
            "application cannot create an announcement channel outside the fixed application-announcer: "
            + ", ".join(sorted(set(scanner.unmanaged_live_regions)))
        )
    if scanner.unmanaged_native_widgets:
        findings.append(
            "application cannot use native widgets outside the fixed control model: "
            + ", ".join(sorted(set(scanner.unmanaged_native_widgets)))
        )
    invalid_descriptions: list[str] = []
    for described in scanner.passive_descriptions:
        direct = described["aria_description"]
        if described["has_aria_description"] and (
            not has_visible_content(direct)
            or has_forbidden_label_codepoint(direct)
        ):
            invalid_descriptions.append(
                f"{described['tag']}[aria-description]"
            )
        raw_refs = described["aria_describedby"]
        parsed_refs = parse_ascii_idrefs(raw_refs)
        refs = parsed_refs or []
        if described["has_aria_describedby"] and (
            parsed_refs is None
            or len(refs) != len(set(refs))
        ):
            invalid_descriptions.append(
                f"{described['tag']}[aria-describedby]"
            )
            continue
        for identifier in refs:
            rows = scanner.elements_by_id.get(identifier, [])
            if (
                len(rows) != 1
                or rows[0].get("_position_internal") == described["position"]
                or not intrinsically_rendered_element(rows[0])
                or rows[0].get("tag") not in SAFE_VISIBLE_LABEL_TARGET_TAGS
                or rows[0].get("_has_element_descendant_internal")
                or rows[0].get("_runtime_mutable_label_internal")
                or "aria-label" in rows[0]
                or "aria-labelledby" in rows[0]
                or not label_target_matches_control_topology(
                    described, rows[0]
                )
                or not has_visible_content(
                    rows[0].get("_accessible_text_internal", "")
                )
                or has_forbidden_label_codepoint(
                    rows[0].get("_accessible_text_internal", "")
                )
            ):
                invalid_descriptions.append(
                    f"{described['tag']}[aria-describedby={identifier}]"
                )
    if invalid_descriptions:
        findings.append(
            "passive ARIA descriptions need visible scalar text or unique exact in-body description targets: "
            + ", ".join(sorted(set(invalid_descriptions)))
        )
    if scanner.form_owner_overrides:
        findings.append(
            "application form-associated controls cannot override native form "
            "ownership: "
            + ", ".join(sorted(set(scanner.form_owner_overrides)))
        )
    if scanner.raw_text_markup:
        findings.append(
            "application cannot declare markup inside browser raw-text or RCDATA elements"
        )
    if scanner.forbidden_elements:
        findings.append(
            "application contains forbidden dependency elements: "
            + ", ".join(sorted(set(scanner.forbidden_elements)))
        )
    if scanner.self_closing_non_void:
        findings.append(
            "application cannot self-close non-void HTML elements: "
            + ", ".join(sorted(set(scanner.self_closing_non_void)))
        )
    if scanner.unsupported_interactive_attributes:
        findings.append(
            "application cannot use unmanaged native invocation behavior: "
            + ", ".join(sorted(set(scanner.unsupported_interactive_attributes)))
        )
    if scanner.forbidden_presentational_attributes:
        findings.append(
            "application cannot bypass Design System geometry with HTML presentational attributes: "
            + ", ".join(sorted(set(scanner.forbidden_presentational_attributes)))
        )
    if scanner.runtime_named_property_conflicts:
        findings.append(
            "application cannot shadow fixed runtime DOM properties: "
            + ", ".join(sorted(set(scanner.runtime_named_property_conflicts)))
        )
    if scanner.orphan_routing_attributes:
        findings.append(
            "application routing attributes require exactly one owning declarative identity: "
            + ", ".join(sorted(set(scanner.orphan_routing_attributes)))
        )
    if scanner.nested_form_or_interactive:
        findings.append(
            "application cannot nest forms or native interactive controls: "
            + ", ".join(sorted(set(scanner.nested_form_or_interactive)))
        )
    if scanner.structural_text:
        findings.append(
            "application canonical structure cannot contain direct document, html or body text"
        )

    expected_head = [
        {"charset": "utf-8"},
        {
            "http-equiv": "Content-Security-Policy",
            "content": expected_csp(),
        },
    ]
    actual_head = [
        child.get("attrs", {}) for child in scanner.head_children[:2]
        if child.get("tag") == "meta"
    ]
    if actual_head != expected_head:
        findings.append(
            "application head must begin with the exact charset and shipped CSP metadata"
        )
    expected_named_head = [
        "viewport",
        "experience-application-contract-version",
        "experience-application-status",
        "experience-application-revision",
        "experience-application-proposal-hash",
        "experience-application-source-hash",
        "experience-application-package-set-hash",
        "experience-application-coverage-hash",
        "experience-application-hash",
        "experience-application-approved-at-utc",
        "experience-application-runtime-sha256",
        "design-system-package-hash",
        "design-system-master-revision",
        "design-system-master-source-hash",
    ]
    canonical_head_shape = (
        len(scanner.head_children) == 18
        and [row.get("tag") for row in scanner.head_children]
        == ["meta"] * 16 + ["title", "style"]
        and scanner.head_children[0].get("attrs") == {"charset": "utf-8"}
        and scanner.head_children[1].get("attrs") == {
            "http-equiv": "Content-Security-Policy",
            "content": expected_csp(),
        }
        and all(
            row.get("attrs", {}).get("name") == name
            and set(row.get("attrs", {})) == {"name", "content"}
            for row, name in zip(
                scanner.head_children[2:16], expected_named_head,
            )
        )
        and scanner.head_children[2].get("attrs", {}).get("content")
        == "width=device-width, initial-scale=1"
        and scanner.head_children[16].get("attrs") == {}
        and scanner.head_children[17].get("attrs") == {}
    )
    if not canonical_head_shape:
        findings.append(
            "application head must use the closed canonical child order and exact attribute surface"
        )
    for name in sorted(MACHINE_META):
        rows = [
            child for child in scanner.head_children
            if child.get("tag") == "meta"
            and child.get("attrs", {}).get("name", "").casefold() == name
        ]
        if (
            len(rows) != 1
            or set(rows[0].get("attrs", {})) != {"name", "content"}
        ):
            findings.append(
                f"application machine metadata {name} must be one exact direct head meta"
            )
    if (
        scanner.meta_counts.get("viewport") != 1
        or scanner.metas.get("viewport")
        != "width=device-width, initial-scale=1"
    ):
        findings.append("application needs the exact responsive viewport metadata")
    if (
        len(scanner.titles) != 1
        or scanner.titles[0].get("parent_tag") != "head"
        or not has_visible_content(scanner.titles[0].get("text", ""))
        or normalize_ascii_whitespace(scanner.titles[0].get("text", ""))
        != "Application acceptance prototype"
    ):
        findings.append(
            "application needs one exact direct document title matching the fixed brand"
        )

    direct_body_ids = [
        child.get("attrs", {}).get("id", "") for child in scanner.body_children
    ]
    if (
        direct_body_ids.count("application-main") != 1
        or direct_body_ids.count("application-announcer") != 1
    ):
        findings.append(
            "application main and announcer must be direct children of body"
        )
    actual_body_shape = [
        {"tag": row.get("tag"), "attrs": row.get("attrs", {})}
        for row in scanner.body_children
    ]
    expected_body_shape = [
        {
            "tag": "a",
            "attrs": {"class": "application-skip", "href": "#application-main"},
        },
        {
            "tag": "header",
            "attrs": {"class": "application-shell application-toolbar"},
        },
        {
            "tag": "main",
            "attrs": {
                "id": "application-main", "class": "application-shell",
                "tabindex": "-1",
            },
        },
        {
            "tag": "div",
            "attrs": {
                "id": "application-announcer", "role": "status",
                "aria-live": "polite", "class": "application-shell",
            },
        },
        {
            "tag": "script",
            "attrs": {
                "type": "application/json",
                "id": "experience-application-contract",
            },
        },
        {
            "tag": "script",
            "attrs": {
                "id": "experience-application-runtime",
                "data-runtime-sha256": runtime_sha256(),
            },
        },
    ]
    if actual_body_shape != expected_body_shape:
        findings.append(
            "application body must use the exact closed skip/header/main/announcer/contract/runtime topology"
        )
    actual_toolbar_shape = [
        {"tag": row.get("tag"), "attrs": row.get("attrs", {})}
        for row in scanner.toolbar_children
    ]
    if actual_toolbar_shape != [
        {"tag": "strong", "attrs": {}},
        {
            "tag": "div",
            "attrs": {
                "class": "application-actions",
                "aria-label": "Application preferences",
            },
        },
    ]:
        findings.append(
            "application toolbar must use the exact fixed brand and preference-control slots"
        )
    actual_action_shape = [
        {"tag": row.get("tag"), "attrs": row.get("attrs", {})}
        for row in scanner.action_children
    ]
    if actual_action_shape != [
        {
            "tag": "button",
            "attrs": {
                "type": "button", "data-application-action": "toggle-theme",
                "aria-label": "Toggle color theme",
                "aria-pressed": (
                    "true" if root_attrs.get("data-theme") == "dark" else "false"
                ),
            },
        },
        {
            "tag": "button",
            "attrs": {
                "type": "button", "data-application-action": "toggle-privacy",
                "aria-pressed": (
                    "true"
                    if root_attrs.get("data-privacy") == "masked" else "false"
                ),
            },
        },
    ]:
        findings.append(
            "application preferences must contain only the exact fixed theme and privacy controls"
        )
    toolbar_text = [
        normalize_ascii_whitespace(row.get("text", ""))
        for row in scanner.toolbar_children
    ]
    action_text = [
        normalize_ascii_whitespace(row.get("text", ""))
        for row in scanner.action_children
    ]
    if (
        toolbar_text[:1] != ["Application acceptance prototype"]
        or action_text != ["Theme", "Privacy mask"]
        or any(
            not has_visible_content(value)
            or has_forbidden_label_codepoint(value)
            for value in [*toolbar_text[:1], *action_text]
        )
    ):
        findings.append(
            "application fixed brand, theme and privacy controls must keep their exact visible labels"
        )
    if (
        scanner.scaffold_direct_text
        or len(scanner.main_children) != sum(scanner.route_views.values())
        or any(
            "data-application-route" not in child.get("attrs", {})
            for child in scanner.main_children
        )
    ):
        findings.append(
            "application fixed scaffold may expose content only through direct main route roots and canonical toolbar descendants"
        )
    main = scanner.elements_by_id.get("application-main", [])
    main_attributes = {
        key: value
        for key, value in (main[0].items() if len(main) == 1 else ())
        if key != "tag" and not key.startswith("_")
    }
    if (
        len(main) != 1
        or scanner.element_counts.get("main") != 1
        or main[0].get("tag") != "main"
        or main_attributes != {
            "id": "application-main",
            "class": "application-shell",
            "tabindex": "-1",
        }
        or not intrinsically_rendered_element(main[0])
    ):
        findings.append(
            "application needs the exact fixed main#application-main runtime target"
        )
    first_body = scanner.body_children[0] if scanner.body_children else {}
    if (
        len(scanner.skip_links) != 1
        or scanner.skip_links[0].get("attrs") != {
            "class": "application-skip", "href": "#application-main",
        }
        or not has_visible_content(scanner.skip_links[0].get("text", ""))
        or scanner.skip_links[0].get("disabled")
        or not sequentially_keyboard_reachable(scanner.skip_links[0])
        or first_body.get("tag") != "a"
        or first_body.get("attrs", {}).get("href") != "#application-main"
    ):
        findings.append(
            "application needs one visible first-body skip link to #application-main"
        )
    announcer = scanner.elements_by_id.get("application-announcer", [])
    if (
        len(announcer) != 1
        or announcer[0].get("role") != "status"
        or announcer[0].get("aria-live") != "polite"
        or "hidden" in announcer[0]
        or not element_context_reachable(announcer[0], set())
    ):
        findings.append(
            "application needs one visible, assistive-technology-reachable polite status application-announcer"
        )
    if (
        len(scanner.announcer_sinks) != 1
        or scanner.announcer_sinks[0]["tag"] != "div"
        or scanner.announcer_sinks[0]["attrs"] != {
            "id": "application-announcer",
            "role": "status",
            "aria-live": "polite",
            "class": "application-shell",
        }
        or scanner.announcer_sinks[0]["route"]
        or scanner.announcer_sinks[0]["has_element_descendant"]
        or normalize_ascii_whitespace(scanner.announcer_sinks[0]["text"])
    ):
        findings.append(
            "application-announcer must be one empty, element-free fixed-runtime text sink"
        )
    if any(
        not image["has_alt"]
        or (image["alt"] and not has_visible_content(image["alt"]))
        for image in scanner.images
    ):
        findings.append("every application img must declare an alt attribute")
    if any(
        not image["has_src"] or not static_image_data_url(image["src"])
        for image in scanner.images
    ):
        findings.append(
            "every application img must embed one valid static PNG src"
        )
    owned_interactive_positions = {
        row["position"]
        for row in [
            *scanner.controls,
            *scanner.skip_links,
            *scanner.search_controls,
            *scanner.filter_controls,
            *scanner.context_elements,
            *scanner.listboxes,
        ]
    }
    owned_interactive_positions.update(
        row.get("_position_internal", -1) for row in main
    )
    owned_interactive_positions.update(
        field["position"]
        for form in scanner.routed_forms
        for field in form["fields"]
    )
    owned_interactive_positions.update(
        submit["position"]
        for form in scanner.routed_forms
        for submit in form["submit_affordances"]
    )
    if any(
        not row["in_body"]
        or row["position"] not in owned_interactive_positions
        for row in scanner.interactive_elements
    ):
        findings.append(
            "every interactive element must have one validated application runtime or routed-form owner"
        )
    expected_scripts = [
        "experience-application-contract", "experience-application-runtime",
    ]
    actual_scripts = [
        child.get("attrs", {}).get("id", "")
        for child in scanner.body_children[-2:]
        if child.get("tag") == "script"
    ]
    if actual_scripts != expected_scripts:
        findings.append(
            "application contract and runtime must be the final direct body children in canonical order"
        )
    dom_end = max(
        scanner.end_positions.get("application-main", 0),
        scanner.end_positions.get("application-announcer", 0),
    )
    if dom_end == 0 or any(
        row.get("parent_tag") != "body" or row.get("position", 0) <= dom_end
        for row in scanner.scripts
    ):
        findings.append(
            "application scripts must be direct body children loaded after the application DOM"
        )
    for route, parents in sorted(scanner.route_parents.items()):
        if any(parent != ("main", "application-main") for parent in parents):
            findings.append(
                f"deep route {route} must be a direct child of #application-main"
            )
    return findings


def dynamic_svg_findings(scanner: ApplicationScanner) -> list[str]:
    findings: list[str] = []
    if scanner.smil_mutations:
        findings.append(
            "application contains forbidden SMIL mutation elements: "
            + ", ".join(sorted(set(scanner.smil_mutations)))
        )
    if scanner.presentation_urls:
        findings.append(
            "application SVG presentation attributes cannot contain dynamic URL references"
        )
    return findings


def native_actionable_control(control: dict) -> bool:
    return (
        control["tag"] == "button" and control["type"] == "button"
    ) or (
        control["tag"] == "a" and control["href"].startswith("#")
    ) or (
        control["tag"] == "form" and not control["action"]
    ) or (
        control["tag"] == "input" and control["type"] in {"button", "submit"}
    )


def sequentially_keyboard_reachable(node: dict) -> bool:
    """Accept only the native tab order or an explicit, normalized tabindex=0."""
    return str(node.get("tabindex", "")) in {"", "0"}


def routed_form_field_constraints_are_satisfiable(field: dict) -> bool:
    """Accept only native constraint sets whose non-empty domain is provable."""
    tag = str(field.get("tag", ""))
    input_type = str(field.get("type", ""))
    constraints = field.get("constraints", {})
    if type(constraints) is not dict:
        return False
    names = set(constraints)
    if tag == "select":
        if not names <= {"multiple", "required"}:
            return False
        options = field.get("options", [])
        if type(options) is not list or not options:
            return False
        values = [str(option.get("value", "")) for option in options]
        labels = [
            str(
                option.get("label", "")
                if option.get("has_label") else option.get("text", "")
            )
            for option in options
        ]
        if (
            any(
                option.get("hidden")
                or option.get("disabled")
                or not option.get("has_value")
                for option in options
            )
            or len(values) != len(set(values))
            or any(not has_visible_content(label) for label in labels)
            or len({normalized_accessible_label(label) for label in labels})
            != len(labels)
        ):
            return False
        return (
            "required" not in names
            or any(value for value in values)
        )
    if tag == "textarea":
        allowed = {"maxlength", "minlength", "required"}
    elif tag == "input" and input_type in ROUTED_FORM_INPUT_TYPES:
        allowed = {"required"}
        if input_type in ROUTED_FORM_LENGTH_INPUT_TYPES:
            allowed.update({"maxlength", "minlength"})
        if input_type in {"number", "range"}:
            allowed.update({"max", "min", "step"})
    else:
        return False
    if not names <= allowed:
        return False
    lengths: dict[str, int] = {}
    for name in ("minlength", "maxlength"):
        if name not in constraints:
            continue
        raw = str(constraints[name])
        if not HTML_NONNEGATIVE_INTEGER.fullmatch(raw):
            return False
        lengths[name] = int(raw)
    if (
        "minlength" in lengths
        and "maxlength" in lengths
        and lengths["minlength"] > lengths["maxlength"]
    ):
        return False
    if "required" in names and lengths.get("maxlength") == 0:
        return False

    numeric: dict[str, Decimal] = {}
    browser_numeric: dict[str, float] = {}
    for name in ("min", "max"):
        if name not in constraints:
            continue
        raw = str(constraints[name])
        if not HTML_FLOAT.fullmatch(raw):
            return False
        try:
            value = Decimal(raw)
        except InvalidOperation:
            return False
        if not value.is_finite():
            return False
        try:
            browser_value = float(raw)
        except (OverflowError, ValueError):
            return False
        if (
            not math.isfinite(browser_value)
            or Decimal(str(browser_value)) != value
        ):
            return False
        numeric[name] = value
        browser_numeric[name] = browser_value
    if "min" in numeric and "max" in numeric \
            and (
                numeric["min"] > numeric["max"]
                or browser_numeric["min"] > browser_numeric["max"]
            ):
        return False
    if "step" in constraints:
        raw_step = str(constraints["step"])
        if raw_step != "any":
            if not HTML_FLOAT.fullmatch(raw_step):
                return False
            try:
                step = Decimal(raw_step)
            except InvalidOperation:
                return False
            if not step.is_finite() or step <= 0:
                return False
            try:
                browser_step = float(raw_step)
            except (OverflowError, ValueError):
                return False
            if (
                not math.isfinite(browser_step)
                or browser_step <= 0
                or Decimal(str(browser_step)) != step
            ):
                return False
    return True


def context_control_constraints_are_satisfiable(
    control: dict, options: list[dict],
) -> bool:
    """Apply the same provable native domain contract to preserved context."""
    raw_constraints = control.get("constraints", ())
    if type(raw_constraints) is not tuple:
        return False
    constraints = {
        str(name): str(value)
        for name, present, value in raw_constraints
        if present
    }
    tag = str(control.get("tag", ""))
    input_type = str(control.get("type", "")) or "text"
    if "readonly" in constraints:
        readonly_types = {
            "date", "datetime-local", "email", "month", "number",
            "password", "search", "tel", "text", "textarea", "time",
            "url", "week",
        }
        if ("textarea" if tag == "textarea" else input_type) \
                not in readonly_types:
            return False
        constraints.pop("readonly")
    if tag == "input" and input_type == "email" \
            and "multiple" in constraints:
        constraints.pop("multiple")
    field = {
        "tag": tag,
        "type": input_type,
        "constraints": constraints,
        "options": options,
    }
    if tag == "input" and input_type == "color":
        return not constraints
    return routed_form_field_constraints_are_satisfiable(field)


def label_target_matches_control_topology(control: dict, target: dict) -> bool:
    control_dialogs = tuple(control.get("dialog_ancestor_ids", ()))
    if control.get("tag") == "dialog" and control.get("id"):
        control_dialogs += (str(control["id"]),)
    return (
        target.get("_application_route_internal") == control.get("route", "")
        and "hidden" not in target
        and not target.get("_disabled_internal")
        and tuple(target.get("_hidden_ancestor_ids_internal", ()))
        == tuple(control.get("hidden_ancestor_ids", ()))
        and tuple(target.get("_dialog_ancestor_ids_internal", ()))
        == control_dialogs
        and bool(target.get("_anonymous_hidden_ancestor_internal"))
        == bool(control.get("anonymous_hidden_ancestor"))
        and bool(target.get("_anonymous_dialog_ancestor_internal"))
        == bool(control.get("anonymous_dialog_ancestor"))
    )


def aria_accessible_name(
    control: dict, scanner: ApplicationScanner,
) -> str:
    raw_labelledby = str(control.get("aria_labelledby", ""))
    labelledby = [raw_labelledby] if HTML_ID.fullmatch(raw_labelledby) else []
    raw_direct = str(control.get("aria_label", ""))
    direct = raw_direct.strip(" \t\n\r\f")
    has_labelledby = bool(
        control.get("has_aria_labelledby") or raw_labelledby
    )
    has_direct = bool(control.get("has_aria_label") or raw_direct)
    if has_labelledby and has_direct:
        return ""
    if has_labelledby:
        if len(labelledby) != 1:
            return ""
        labels: list[str] = []
        for identifier in labelledby:
            rows = scanner.elements_by_id.get(identifier, [])
            if (
                len(rows) != 1
                or not intrinsically_rendered_element(rows[0])
                or rows[0].get("tag") not in SAFE_VISIBLE_LABEL_TARGET_TAGS
                or rows[0].get("_has_element_descendant_internal")
                or rows[0].get("_runtime_mutable_label_internal")
                or "aria-label" in rows[0]
                or "aria-labelledby" in rows[0]
                or not label_target_matches_control_topology(
                    control, rows[0],
                )
            ):
                return ""
            text = normalize_ascii_whitespace(
                rows[0].get("_accessible_text_internal", "")
            )
            if not has_visible_content(text):
                return ""
            labels.append(text)
        return " ".join(labels)
    if has_direct:
        return (
            direct
            if not has_forbidden_label_codepoint(raw_direct)
            and has_visible_content(direct)
            else ""
        )
    positions = tuple(control.get("label_ancestor_positions", ()))
    labels = [
        row for row in scanner.labels if row.get("position") in positions
    ]
    if len(positions) == 1 and len(labels) == 1:
        caption = normalize_ascii_whitespace(labels[0].get("caption", ""))
        return (
            caption
            if not labels[0].get("runtime_mutable_context")
            and not labels[0].get("invalid_caption_context")
            and has_visible_content(caption)
            else ""
        )
    return ""


def visible_control_label_matches(
    control: dict, scanner: ApplicationScanner,
) -> bool:
    """Bind each user-input purpose to visible text and the accessible name."""
    accessible = aria_accessible_name(control, scanner)
    positions = tuple(control.get("label_ancestor_positions", ()))
    labels = [
        row for row in scanner.labels if row.get("position") in positions
    ]
    visible = ""
    if len(positions) == 1 and len(labels) == 1:
        if (
            labels[0].get("runtime_mutable_context")
            or labels[0].get("invalid_caption_context")
        ):
            return False
        visible = normalize_ascii_whitespace(labels[0].get("caption", ""))
    elif control.get("has_aria_labelledby"):
        visible = accessible
    return bool(
        has_visible_content(visible)
        and normalized_accessible_label(visible)
        == normalized_accessible_label(accessible)
    )


def resolved_accessible_text(node: dict, scanner: ApplicationScanner) -> str:
    chunks = node.get("accessible_name_chunks", [])
    if chunks:
        reachable = reachable_application_containers(scanner)
        text = "".join(
            str(chunk.get("text", ""))
            for chunk in chunks
            if not chunk.get("anonymous_hidden")
            and set(chunk.get("hidden_ids", ())) <= reachable
        )
        normalized = normalize_ascii_whitespace(text)
        return normalized if has_visible_content(normalized) else ""
    text = normalize_ascii_whitespace(node.get("text", ""))
    return text if has_visible_content(text) else ""


def control_accessible_name(
    control: dict, scanner: ApplicationScanner,
) -> str:
    aria_name = aria_accessible_name(control, scanner)
    if (
        aria_name
        or control.get("has_aria_label")
        or control.get("has_aria_labelledby")
        or control.get("aria_label")
        or control.get("aria_labelledby")
    ):
        return aria_name
    if control.get("tag") == "input":
        raw_value = str(control.get("value", ""))
        value = raw_value.strip(" \t\n\r\f")
        if has_visible_content(value):
            return value
        if control.get("type") == "submit":
            return "Submit" if not control.get("has_value") else ""
        if control.get("type") == "image":
            alt = str(control.get("alt", "")).strip(" \t\n\r\f")
            return alt if has_visible_content(alt) else ""
    return resolved_accessible_text(control, scanner)


def visible_action_label(
    control: dict, scanner: ApplicationScanner,
) -> bool:
    """Require a sighted-user label in addition to an accessible name."""
    if control.get("tag") in {"a", "button"}:
        return bool(resolved_accessible_text(control, scanner))
    if control.get("tag") == "input":
        return has_visible_content(control.get("value", ""))
    return control.get("tag") == "form"


def visible_label_is_in_accessible_name(
    control: dict, scanner: ApplicationScanner,
) -> bool:
    if control.get("tag") in {"a", "button"}:
        visible = resolved_accessible_text(control, scanner)
    elif control.get("tag") == "input":
        visible = (
            "Submit"
            if control.get("type") == "submit" and not control.get("has_value")
            else str(control.get("value", ""))
        )
    else:
        return control.get("tag") == "form"
    visible_name = normalized_accessible_label(visible)
    accessible_name = normalized_accessible_label(
        control_accessible_name(control, scanner)
    )
    return bool(visible_name and visible_name in accessible_name)


def intrinsically_rendered(
    tag: str, element_type: str, in_body: bool,
) -> bool:
    return (
        in_body
        and tag not in INTRINSICALLY_NON_RENDERED_ELEMENTS
        and not (
            tag == "input" and element_type.casefold() == "hidden"
        )
    )


def visible_submit_label(
    submit: dict, scanner: ApplicationScanner,
) -> bool:
    if submit.get("tag") == "button":
        return bool(resolved_accessible_text(submit, scanner))
    if submit.get("tag") == "input" and submit.get("type") == "submit":
        return (
            not submit.get("has_value")
            or has_visible_content(submit.get("value", ""))
        )
    return False


def intrinsically_rendered_element(element: dict) -> bool:
    return intrinsically_rendered(
        str(element.get("tag", "")),
        str(element.get("type", "")),
        bool(element.get("_in_body_internal", True)),
    )


def reachable_node(node: dict, reachable_containers: set[str]) -> bool:
    return (
        intrinsically_rendered(
            str(node.get("tag", "")),
            str(node.get("type", "")),
            bool(node.get("in_body", True)),
        )
        and (not node.get("hidden") or node.get("id") in reachable_containers)
        and not node.get("disabled")
        and not node.get("anonymous_hidden_ancestor")
        and not node.get("anonymous_dialog_ancestor")
        and set(node.get("hidden_ancestor_ids", ())) <= reachable_containers
        and set(node.get("dialog_ancestor_ids", ())) <= reachable_containers
    )


def runtime_collection_item_reachable(
    node: dict, reachable_containers: set[str],
) -> bool:
    """Evaluate an item after search/filter runtime removes its own hidden state."""
    return (
        intrinsically_rendered(
            str(node.get("tag", "")),
            str(node.get("type", "")),
            bool(node.get("in_body", True)),
        )
        and not node.get("disabled")
        and not node.get("anonymous_hidden_ancestor")
        and not node.get("anonymous_dialog_ancestor")
        and set(node.get("hidden_ancestor_ids", ())) <= reachable_containers
        and set(node.get("dialog_ancestor_ids", ())) <= reachable_containers
    )


def runtime_collection_accessible_text(
    node: dict, scanner: ApplicationScanner,
) -> str:
    """Resolve item text as it will be exposed after runtime unhides the item."""
    chunks = node.get("accessible_name_chunks", [])
    if not chunks:
        text = normalize_ascii_whitespace(node.get("text", ""))
        return text if has_visible_content(text) else ""
    reachable = reachable_application_containers(scanner)
    own_hidden_id = (
        {str(node.get("id"))}
        if node.get("hidden") and node.get("id") else set()
    )
    own_anonymous_hidden = bool(
        node.get("hidden")
        and not node.get("id")
        and not node.get("anonymous_hidden_ancestor")
    )
    text = "".join(
        str(chunk.get("text", ""))
        for chunk in chunks
        if (
            (not chunk.get("anonymous_hidden") or own_anonymous_hidden)
            and (
                set(chunk.get("hidden_ids", ())) - own_hidden_id
                <= reachable
            )
        )
    )
    normalized = normalize_ascii_whitespace(text)
    return normalized if has_visible_content(normalized) else ""


def runtime_interaction_topology(node: dict) -> tuple:
    """Return the disclosure/dialog boundary that gates native interaction."""
    return (
        tuple(node.get("hidden_ancestor_ids", ())),
        tuple(node.get("dialog_ancestor_ids", ())),
        bool(node.get("anonymous_hidden_ancestor")),
        bool(node.get("anonymous_dialog_ancestor")),
    )


def reachable_control(
    control: dict, reachable_containers: set[str] | None = None,
) -> bool:
    return reachable_node(control, reachable_containers or set())


def element_context_reachable(
    element: dict, reachable_containers: set[str],
) -> bool:
    return (
        intrinsically_rendered_element(element)
        and not element.get("_disabled_internal")
        and not element.get("_anonymous_hidden_ancestor_internal")
        and not element.get("_anonymous_dialog_ancestor_internal")
        and set(element.get("_hidden_ancestor_ids_internal", ()))
        <= reachable_containers
        and set(element.get("_dialog_ancestor_ids_internal", ()))
        <= reachable_containers
    )


def disclosure_relation_is_invalid(control: dict, target: dict) -> bool:
    return (
        control.get("position") == target.get("_position_internal")
        or target.get("_position_internal")
        in control.get("ancestor_positions", ())
        or control.get("position")
        in target.get("_ancestor_positions_internal", ())
    )


def reachable_application_containers(scanner: ApplicationScanner) -> set[str]:
    """Resolve hidden disclosures and closed dialogs from valid route-local openers."""
    reachable: set[str] = set()
    candidates: list[tuple[dict, str, dict]] = []
    for control in scanner.controls:
        action = control.get("action")
        if action not in {"toggle-menu", "toggle-drawer", "open-modal"}:
            continue
        target_id = control.get("aria_controls", "")
        rows = scanner.elements_by_id.get(target_id, [])
        if (
            not target_id
            or len(rows) != 1
            or not native_actionable_control(control)
            or control.get("disabled")
        ):
            continue
        target = rows[0]
        if (
            target.get("_application_route_internal") != control.get("route")
            or disclosure_relation_is_invalid(control, target)
        ):
            continue
        if action == "open-modal":
            if (
                target.get("tag") != "dialog"
                or "open" in target
                or "hidden" in target
            ):
                continue
        elif (
            target.get("tag") == "dialog"
            or control.get("aria_expanded") not in {"true", "false"}
            or ("hidden" in target)
            != (control.get("aria_expanded") == "false")
        ):
            continue
        candidates.append((control, target_id, target))

    changed = True
    while changed:
        changed = False
        for control, target_id, target in candidates:
            if target_id in reachable:
                continue
            if (
                reachable_control(control, reachable)
                and element_context_reachable(target, reachable)
            ):
                reachable.add(target_id)
                changed = True
    return reachable


def parse_contract(scanner: ApplicationScanner, findings: list[str]) -> dict:
    contracts = [
        row for row in scanner.scripts
        if row["attrs"].get("id") == "experience-application-contract"
    ]
    runtimes = [
        row for row in scanner.scripts
        if row["attrs"].get("id") == "experience-application-runtime"
    ]
    if len(scanner.scripts) != 2 or len(contracts) != 1 or len(runtimes) != 1:
        findings.append("application must contain only its one JSON contract and fixed runtime")
    contract: dict = {}
    if contracts:
        row = contracts[0]
        if set(row["attrs"]) != {"type", "id"} or row["attrs"].get("type") != "application/json":
            findings.append("application contract script must use only its canonical inline attributes")
        try:
            value = strict_json_loads(row["body"])
            if type(value) is dict:
                contract = value
            else:
                findings.append("application contract root must be an object")
        except (json.JSONDecodeError, ValueError) as exc:
            findings.append(f"application contract JSON is invalid: {exc}")
    if runtimes:
        row = runtimes[0]
        actual = sha(row["body"].encode())
        if set(row["attrs"]) != {"id", "data-runtime-sha256"}:
            findings.append("application runtime script must use only its canonical executable attributes")
        if row["body"] != template_runtime():
            findings.append("application runtime differs from the shipped fixed runtime")
        if row["attrs"].get("data-runtime-sha256") != runtime_sha256() or actual != runtime_sha256():
            findings.append("application runtime checksum is stale or invalid")
    return contract


def validate_contract(
    contract: dict,
    expected_records: dict[str, str],
    active_experiences: set[str],
    scanner: ApplicationScanner,
    maps: list[dict],
    findings: list[str],
    *,
    empty_application: bool = False,
    authoring: bool = False,
) -> dict:
    expected_refs = set(expected_records)
    expected_keys = set(CONTRACT_SCHEMA["required_fields"])
    if (
        set(contract) != expected_keys
        or contract.get("schema_version") != CONTRACT_SCHEMA["schema_version"]
    ):
        findings.append("application contract must use the exact supported schema")
    validate_exact_field_types(
        contract,
        CONTRACT_SCHEMA.get("field_types"),
        "application contract",
        findings,
    )
    state_classes = contract.get("state_classes")
    if (
        type(state_classes) is not list
        or any(type(value) is not str for value in state_classes)
        or len(state_classes) != len(set(state_classes))
        or set(state_classes) != REQUIRED_STATE_CLASSES
    ):
        findings.append(
            "application contract must cover ordinary, loading, empty, validation, permission, stale, conflict, failure, retry and recovery"
        )
    routes = contract.get("routes")
    if type(routes) is not list or not routes:
        findings.append("application contract needs one or more routes")
        routes = []
    by_route: dict[str, dict] = {}
    route_labels: dict[str, str] = {}
    contract_refs: set[str] = set()
    transition_refs: set[str] = set()
    edges: list[dict] = []
    route_keys = set(CONTRACT_SCHEMA["route_required_fields"])
    edge_keys = set(CONTRACT_SCHEMA["transition_required_fields"])
    simulation_keys = set(CONTRACT_SCHEMA["simulation_required_fields"])
    experience_pattern = re.compile(CONTRACT_SCHEMA["experience_pattern"])
    for index, row in enumerate(routes):
        label = f"application contract routes[{index}]"
        if type(row) is not dict or set(row) != route_keys:
            findings.append(f"{label} must contain the exact route fields")
            continue
        validate_exact_field_types(
            row, CONTRACT_SCHEMA.get("route_field_types"), label, findings
        )
        route_value = row.get("route")
        state_value = row.get("state_class")
        experience_value = row.get("experience_id")
        label_value = row.get("label")
        route = route_value if type(route_value) is str else ""
        state_class = state_value if type(state_value) is str else ""
        experience_id = experience_value if type(experience_value) is str else ""
        route_label = label_value if type(label_value) is str else ""
        refs = row.get("record_refs")
        transitions = row.get("transitions")
        if not CONTRACT_ROUTE.fullmatch(route):
            findings.append(f"{label} has an invalid deep route")
        if route in by_route:
            findings.append(f"{label} duplicates route {route}")
        if (
            type(refs) is not list
            or any(type(reference) is not str for reference in refs)
            or len(refs) != len(set(refs))
        ):
            findings.append(f"{label} record_refs must be a unique string array")
            refs = []
        if type(transitions) is not list:
            findings.append(f"{label} transitions must be an array")
            transitions = []
        by_route[route] = {
            "route": route,
            "state_class": state_class,
            "experience_id": experience_id,
            "label": route_label,
            "record_refs": refs,
        }
        if state_class not in REQUIRED_STATE_CLASSES:
            findings.append(f"{label} has an unsupported state_class")
        if not experience_pattern.fullmatch(experience_id):
            findings.append(f"{label} has an invalid experience_id")
        valid_experience = (
            experience_id == "application" if empty_application
            else experience_id in active_experiences
        )
        if not authoring and not valid_experience:
            findings.append(f"{label} names a missing or retired Experience")
        if not has_visible_content(route_label) \
                or has_forbidden_label_codepoint(route_label):
            findings.append(
                f"{label} needs a non-empty label without invisible or control code points"
            )
        if has_visible_content(route_label):
            normalized_label = normalized_accessible_label(route_label)
            if normalized_label in route_labels:
                findings.append(
                    f"{label} duplicates normalized route label from "
                    f"{route_labels[normalized_label]}"
                )
            else:
                route_labels[normalized_label] = route
        for reference in refs:
            if not CONTRACT_EXACT.fullmatch(reference):
                findings.append(f"{label} has an invalid exact record ref: {reference}")
            if not authoring and reference not in expected_refs:
                findings.append(f"{label} maps an unknown, retired or stale ref: {reference}")
            if experience_id and not reference.startswith(experience_id + ":"):
                findings.append(f"{label} maps a ref owned by another Experience: {reference}")
            contract_refs.add(reference)
        for edge_index, edge in enumerate(transitions):
            edge_label = f"{label}.transitions[{edge_index}]"
            if type(edge) is not dict or set(edge) != edge_keys:
                findings.append(f"{edge_label} must contain the exact transition fields")
                continue
            validate_exact_field_types(
                edge,
                CONTRACT_SCHEMA.get("transition_field_types"),
                edge_label,
                findings,
            )
            transition_value = edge.get("transition_ref")
            target_value = edge.get("target")
            outcome_value = edge.get("outcome")
            return_value = edge.get("return_route")
            transition = transition_value if type(transition_value) is str else ""
            target = target_value if type(target_value) is str else ""
            outcome = outcome_value if type(outcome_value) is str else ""
            return_route = return_value if type(return_value) is str else ""
            if (
                not CONTRACT_EXACT.fullmatch(transition)
                or ":TRN-" not in transition
                or (not authoring and transition not in expected_refs)
            ):
                findings.append(f"{edge_label} needs an active exact transition ref")
            if transition in transition_refs:
                findings.append(f"{edge_label} duplicates transition {transition}")
            if not OUTCOME.fullmatch(outcome):
                findings.append(f"{edge_label} has an invalid outcome")
            preserve = edge.get("preserve_context")
            if (
                type(preserve) is not list
                or any(type(value) is not str or not value.strip() for value in preserve)
                or len(preserve) != len(set(preserve))
            ):
                findings.append(f"{edge_label} preserve_context must be a unique string array")
                preserve = []
            transition_refs.add(transition)
            edges.append({
                "source": route,
                "transition_ref": transition,
                "target": target,
                "outcome": outcome,
                "preserve_context": preserve,
                "return_route": return_route,
            })
    if not authoring and contract_refs != expected_refs:
        for reference in sorted(expected_refs - contract_refs):
            findings.append(f"application contract does not cover active ref {reference}")
    if not authoring:
        for reference in sorted(contract_refs - expected_refs):
            findings.append(f"application contract contains unknown ref {reference}")
    expected_transitions = {ref for ref in expected_refs if ":TRN-" in ref}
    if not authoring and transition_refs != expected_transitions:
        for reference in sorted(expected_transitions - transition_refs):
            findings.append(f"application contract has no edge for transition {reference}")
    entry_value = contract.get("entry_route")
    entry = entry_value if type(entry_value) is str else ""
    if entry not in by_route:
        findings.append("application entry_route does not exist")
    route_experiences = {row["experience_id"] for row in by_route.values()}
    expected_experiences = {"application"} if empty_application else active_experiences
    if not authoring and route_experiences != expected_experiences:
        findings.append("application routes must cover exactly every active Experience")
    if not authoring and empty_application and (
        len(by_route) != 1 or contract_refs or transition_refs
    ):
        findings.append(
            "an application with no active Experience packages must expose exactly one empty application route"
        )
    for route in by_route:
        if scanner.route_views.get(route) != 1:
            findings.append(f"deep route {route} must have exactly one rendered view")
        route_targets = scanner.route_targets.get(route, [])
        if (
            len(route_targets) != 1
            or route_targets[0].get("tag") not in SAFE_ROUTE_ROOT_TAGS
            or route_targets[0].get("role")
            or route_targets[0].get("aria_attributes") != {"aria-labelledby"}
            or not reachable_node(route_targets[0], set())
        ):
            findings.append(
                f"deep route {route} must use a browser-stable section/article/div root activatable by the fixed runtime"
            )
        rendered_states = scanner.route_states.get(route, [])
        if (
            len(rendered_states) != 1
            or rendered_states[0] != by_route[route]["state_class"]
        ):
            findings.append(
                f"deep route {route} must render its exact contract state_class"
            )
        labels = scanner.route_labels.get(route, [])
        label_rows = (
            scanner.elements_by_id.get(labels[0], [])
            if len(labels) == 1 and labels[0] else []
        )
        if (
            len(labels) != 1
            or not labels[0]
            or scanner.element_ids.get(labels[0]) != 1
            or len(label_rows) != 1
            or label_rows[0].get("tag") not in {
                "h1", "h2", "h3", "h4", "h5", "h6",
            }
            or label_rows[0].get("role")
            or any(
                attribute.startswith("aria-")
                for attribute in label_rows[0]
            )
            or label_rows[0].get("_has_element_descendant_internal")
            or not str(
                label_rows[0].get("_accessible_text_internal", "")
            )
            or not has_visible_content(
                label_rows[0].get("_accessible_text_internal", "")
            )
            or has_forbidden_label_codepoint(
                label_rows[0].get("_accessible_text_internal", "")
            )
            or label_rows[0].get("_runtime_mutable_label_internal")
            or normalize_ascii_whitespace(
                label_rows[0].get("_accessible_text_internal", "")
            ) != normalize_ascii_whitespace(by_route[route]["label"])
            or label_rows[0].get("_application_route_internal") != route
            or not element_context_reachable(label_rows[0], set())
        ):
            findings.append(
                f"deep route {route} needs one visible heading whose exact text matches the contract label"
            )
    for route in sorted(set(scanner.route_views) - set(by_route)):
        findings.append(f"HTML contains undeclared route view {route}")
    represented_states = {row["state_class"] for row in by_route.values()}
    required_route_states = {"empty"} if empty_application else REQUIRED_STATE_CLASSES
    if not authoring and represented_states != required_route_states:
        findings.append(
            "application routes must represent exactly the required state taxonomy"
        )
    if not authoring and entry in by_route:
        expected_entry_state = "empty" if empty_application else "ordinary"
        if by_route[entry]["state_class"] != expected_entry_state:
            findings.append(
                f"application entry_route must use the {expected_entry_state} state_class"
            )
    declared_transitions: dict[str, dict] = {}
    for edge in edges:
        target = edge["target"]
        source = edge["source"]
        if target not in by_route:
            findings.append(f"transition {edge.get('transition_ref')} targets missing route {target}")
            continue
        source_experience = by_route[source]["experience_id"]
        target_experience = by_route[target]["experience_id"]
        return_route = edge["return_route"]
        preserve = edge.get("preserve_context")
        outcome = edge["outcome"]
        transition_ref = edge["transition_ref"]
        declared_transitions[transition_ref] = edge
        if not transition_ref.startswith(source_experience + ":"):
            findings.append(
                f"transition {edge.get('transition_ref')} must be owned by its source Experience"
            )
        if return_route and return_route not in by_route:
            findings.append(
                f"transition {edge.get('transition_ref')} has an invalid return route"
            )
        if source_experience != target_experience:
            if type(preserve) is not list or not preserve or any(
                type(value) is not str or not value.strip() for value in preserve
            ):
                findings.append(
                    f"cross-Experience transition {edge.get('transition_ref')} needs preserved context keys"
                )
            if return_route not in by_route:
                findings.append(
                    f"cross-Experience transition {edge.get('transition_ref')} needs a valid return route"
                )
        if target in by_route and outcome != by_route[target]["state_class"]:
            findings.append(
                f"transition {transition_ref} outcome must equal its target state_class"
            )
        for key in preserve if type(preserve) is list else []:
            if key not in scanner.context_sources.get(source, set()):
                findings.append(
                    f"transition {transition_ref} cannot capture missing context key {key}"
                )
            if key not in scanner.context_targets.get(target, set()):
                findings.append(
                    f"transition {transition_ref} cannot restore missing context key {key}"
                )
        controls = [
            control for control in scanner.controls
            if control["route"] == source
            and control["transition_ref"] == edge.get("transition_ref")
        ]
        if len(controls) != 1:
            findings.append(
                f"transition {edge.get('transition_ref')} needs exactly one actionable DOM control"
            )
        else:
            control = controls[0]
            expected_context = ",".join(preserve or [])
            if (
                control["target"] != target
                or control["preserve_context"] != expected_context
                or control["return_route"] != return_route
            ):
                findings.append(
                    f"transition {edge.get('transition_ref')} DOM declaration is stale"
                )
    simulations = contract.get("simulations")
    if type(simulations) is not list:
        findings.append("application contract simulations must be an array")
        simulations = []
    declared_simulations: dict[str, dict] = {}
    simulation_edges: list[dict] = []
    for index, simulation in enumerate(simulations):
        label = f"application contract simulations[{index}]"
        if type(simulation) is not dict or set(simulation) != simulation_keys:
            findings.append(f"{label} must contain the exact simulation fields")
            continue
        validate_exact_field_types(
            simulation,
            CONTRACT_SCHEMA.get("simulation_field_types"),
            label,
            findings,
        )
        simulation_value = simulation.get("simulation_id")
        source_value = simulation.get("source")
        target_value = simulation.get("target")
        outcome_value = simulation.get("outcome")
        return_value = simulation.get("return_route")
        simulation_id = simulation_value if type(simulation_value) is str else ""
        source = source_value if type(source_value) is str else ""
        target = target_value if type(target_value) is str else ""
        outcome = outcome_value if type(outcome_value) is str else ""
        return_route = return_value if type(return_value) is str else ""
        if not SIMULATION_ID.fullmatch(simulation_id):
            findings.append(f"{label} has an invalid simulation_id")
        if simulation_id in declared_simulations:
            findings.append(f"{label} duplicates simulation_id {simulation_id}")
        declared_simulations[simulation_id] = {
            "simulation_id": simulation_id,
            "source": source,
            "target": target,
            "outcome": outcome,
            "return_route": return_route,
        }
        if source not in by_route:
            findings.append(f"{label} has a missing source route")
        if target not in by_route:
            findings.append(f"{label} has a missing target route")
        if return_route not in by_route:
            findings.append(f"{label} needs a valid return_route")
        if not OUTCOME.fullmatch(outcome):
            findings.append(f"{label} has an invalid outcome")
        if target in by_route and outcome != by_route[target]["state_class"]:
            findings.append(
                f"{label} outcome must equal its target state_class"
            )
        controls = [
            control for control in scanner.controls
            if control["route"] == source
            and control["simulation_id"] == simulation_id
        ]
        if len(controls) != 1:
            findings.append(
                f"simulation {simulation_id} needs exactly one actionable DOM control"
            )
        elif controls[0]["target"] != target:
            findings.append(f"simulation {simulation_id} DOM target is stale")
        simulation_edges.append(
            {
                "simulation_id": simulation_id,
                "source": source,
                "target": target,
                "outcome": outcome,
                "return_route": return_route,
            }
        )
    if not authoring and empty_application and simulations:
        findings.append("the empty application cannot declare simulations")
    if not empty_application:
        required_simulation_states = represented_states - {"ordinary"}
        simulated_states = {
            by_route[simulation["target"]]["state_class"]
            for simulation in simulation_edges
            if simulation["target"] in by_route
            and simulation["outcome"]
            == by_route[simulation["target"]]["state_class"]
        }
        missing_simulations = sorted(
            required_simulation_states - simulated_states
        )
        if missing_simulations:
            findings.append(
                "application deterministic simulations must cover every non-ordinary state_class: "
                + ", ".join(missing_simulations)
            )
    return_targets = {
        (edge.get("target", ""), edge.get("return_route", ""))
        for edge in [*edges, *simulation_edges]
        if edge.get("return_route", "")
    }
    return_routes_by_target: dict[str, set[str]] = {}
    for target, return_route in return_targets:
        return_routes_by_target.setdefault(target, set()).add(return_route)
    for target, return_routes in sorted(return_routes_by_target.items()):
        controls = [
            control for control in scanner.controls
            if control["route"] == target
            and control["action"] == "return-route"
        ]
        if len(controls) != 1:
            findings.append(
                f"route {target} needs exactly one return-route state owner for "
                + ", ".join(sorted(return_routes))
            )
    expected_return_control_routes = set(return_routes_by_target)
    actual_return_control_routes = {
        control["route"] for control in scanner.controls
        if control["action"] == "return-route"
    }
    if actual_return_control_routes != expected_return_control_routes:
        findings.append(
            "return-route controls must exist exactly on routes targeted by a declared non-empty return route"
        )
    reachable_containers = reachable_application_containers(scanner)
    for anchor in scanner.fragment_anchors:
        if anchor["href"] == "#application-main":
            continue
        if not CONTRACT_ROUTE.fullmatch(anchor["href"]):
            findings.append(
                "application fragment anchors must use the canonical skip target or a validated deep-route identity"
            )
        elif not anchor["transition_ref"] and not anchor["simulation_id"]:
            findings.append(
                "application deep-route anchors must declare one validated transition or simulation identity"
            )
    if any(dialog["open"] for dialog in scanner.dialogs):
        findings.append(
            "application dialogs must begin closed and open through the fixed runtime"
        )
    for control in scanner.controls:
        transition_ref = control["transition_ref"]
        simulation_id = control["simulation_id"]
        if transition_ref and transition_ref not in declared_transitions:
            findings.append(
                f"DOM route control declares an unknown transition: {transition_ref}"
            )
        if simulation_id and simulation_id not in declared_simulations:
            findings.append(
                f"DOM route control declares an unknown simulation: {simulation_id}"
            )
        if transition_ref and simulation_id:
            findings.append("DOM controls cannot combine transition and simulation identities")
        action = control["action"]
        allowed_control_aria = {
            "aria-description", "aria-describedby", "aria-disabled",
            "aria-label", "aria-labelledby",
        }
        if action in {"toggle-theme", "toggle-privacy", "toggle-pressed"}:
            allowed_control_aria.add("aria-pressed")
        elif action in {"toggle-menu", "toggle-drawer"}:
            allowed_control_aria.update({"aria-controls", "aria-expanded"})
        elif action == "open-modal":
            allowed_control_aria.update({"aria-controls", "aria-haspopup"})
        elif action == "select-option":
            allowed_control_aria.add("aria-selected")
        unsupported_control_aria = (
            control["aria_attributes"] - allowed_control_aria
        )
        if unsupported_control_aria:
            findings.append(
                "application control ARIA state must belong to its exact runtime action: "
                + ", ".join(sorted(unsupported_control_aria))
            )
        identity_count = sum((
            bool(transition_ref), bool(simulation_id), bool(action),
        ))
        declared_identity_count = sum((
            control["has_transition_ref"], control["has_simulation_id"],
            control["has_action"],
        ))
        if identity_count != 1 or declared_identity_count != 1:
            findings.append(
                "application controls must declare exactly one non-empty transition, simulation or action identity"
            )
        if action and action not in ALLOWED_APPLICATION_ACTIONS:
            findings.append(f"DOM control declares an unsupported application action: {action}")
        if action and (control["target"] or transition_ref or simulation_id):
            findings.append("DOM controls cannot combine an application action with routing")
        if transition_ref and not (
            control["has_route_target"]
            and control["has_preserve_context"]
            and control["has_return_route"]
        ):
            findings.append(
                "transition controls must declare target, preserved context and return route attributes"
            )
        if simulation_id and (
            not control["has_route_target"]
            or control["has_preserve_context"]
            or control["has_return_route"]
        ):
            findings.append(
                "simulation controls may declare only their exact route target"
            )
        if action and any((
            control["has_route_target"], control["has_preserve_context"],
            control["has_return_route"],
        )):
            findings.append(
                "application actions cannot own transition routing attributes"
            )
        actionable = action or transition_ref or simulation_id
        if actionable and not native_actionable_control(control):
            findings.append(
                "application controls must use native actionable HTML elements"
            )
        if actionable and control["label_ancestor_positions"]:
            findings.append(
                "fixed-runtime controls cannot be activated through an implicit label owner"
            )
        if actionable and action != "select-option" and control["role"]:
            findings.append(
                "fixed-runtime native controls cannot override their implicit accessibility role"
            )
        if actionable and control["tag"] == "a" \
                and control["href"] != control["target"]:
            findings.append(
                "deep-route anchor href must exactly match its declared runtime target"
            )
        if actionable and control["tag"] != "form" \
                and not control_accessible_name(control, scanner):
            findings.append(
                "application controls must expose a non-empty accessible name"
            )
        if actionable and not visible_action_label(control, scanner):
            findings.append(
                "application controls must expose a non-empty visible label"
            )
        if (
            actionable
            and control["tag"] != "form"
            and not visible_label_is_in_accessible_name(control, scanner)
        ):
            findings.append(
                "application control accessible names must contain their visible labels"
            )
        if actionable and not reachable_control(control, reachable_containers):
            findings.append(
                "application controls must be enabled and reachable from visible application UI"
            )
        if actionable and not sequentially_keyboard_reachable(control):
            findings.append(
                "application controls must remain in sequential keyboard navigation"
            )
        if actionable and control["tag"] == "button" \
                and control["type"] != "button":
            findings.append("application action buttons must declare type=button")
        if action and control["tag"] == "input" \
                and control["type"] != "button":
            findings.append(
                "input application actions must declare type=button"
            )
        if action and control["tag"] not in {"button", "input"}:
            findings.append(
                "application actions must use button controls"
            )
        if action in {"toggle-menu", "toggle-drawer", "open-modal"}:
            target_id = control["aria_controls"]
            if not target_id or scanner.element_ids.get(target_id) != 1:
                findings.append(f"{action} needs aria-controls targeting one element")
            target_rows = scanner.elements_by_id.get(target_id, [])
            target_row = target_rows[0] if len(target_rows) == 1 else None
            if target_row is not None and (
                target_row.get("_application_route_internal") != control["route"]
                or not element_context_reachable(
                    target_row, reachable_containers,
                )
            ):
                findings.append(
                    f"{action} target must be reachable in the control's same route"
                )
            if target_row is not None and disclosure_relation_is_invalid(
                control, target_row,
            ):
                findings.append(
                    f"{action} target must not be the control itself, its ancestor or its descendant"
                )
            if action in {"toggle-menu", "toggle-drawer"} \
                    and target_row is not None \
                    and target_row.get("tag") not in SAFE_DYNAMIC_CONTAINER_TAGS:
                findings.append(
                    f"{action} target must use a browser-stable semantic container"
                )
            if action == "open-modal" and target_row is not None and (
                target_row.get("tag") != "dialog"
                or "open" in target_row
                or "hidden" in target_row
            ):
                findings.append(
                    "open-modal must target one initially closed, visible dialog"
                )
            if action in {"toggle-menu", "toggle-drawer"} \
                    and target_row is not None \
                    and target_row.get("tag") == "dialog":
                findings.append(
                    f"{action} cannot target a dialog; use open-modal"
                )
        if action == "open-modal" and control["aria_haspopup"] != "dialog":
            findings.append("open-modal needs aria-haspopup=dialog")
        if action in {"toggle-menu", "toggle-drawer"} \
                and control["aria_expanded"] not in {"true", "false"}:
            findings.append(f"{action} needs an explicit aria-expanded state")
        if action in {"toggle-menu", "toggle-drawer"}:
            target_rows = scanner.elements_by_id.get(control["aria_controls"], [])
            if len(target_rows) == 1 \
                    and control["aria_expanded"] in {"true", "false"}:
                target_hidden = "hidden" in target_rows[0]
                if target_hidden != (control["aria_expanded"] == "false"):
                    findings.append(
                        f"{action} target hidden state must match aria-expanded"
                    )
        if action in {"toggle-theme", "toggle-privacy", "toggle-pressed"} \
                and control["aria_pressed"] not in {"true", "false"}:
            findings.append(f"{action} needs an explicit aria-pressed state")
        if action in {"toggle-theme", "toggle-privacy"}:
            root_attrs = (
                scanner.html_attributes[0]
                if len(scanner.html_attributes) == 1 else {}
            )
            expected_pressed = (
                "true"
                if (
                    action == "toggle-theme"
                    and root_attrs.get("data-theme") == "dark"
                ) or (
                    action == "toggle-privacy"
                    and root_attrs.get("data-privacy") == "masked"
                )
                else "false"
            )
            if control["aria_pressed"] != expected_pressed:
                findings.append(
                    f"{action} aria-pressed must match the html root's initial state"
                )
        if action == "select-option" and (
            control["tag"] != "button"
            or control["type"] != "button"
            or control["role"] != "option"
            or control["aria_selected"] not in {"true", "false"}
            or not control["data_value"]
        ):
            findings.append(
                "select-option needs a button with role=option, aria-selected and data-value"
            )
        if action == "select-option" and (
            len(control["listbox_ancestors"]) != 1
            or control["listbox_ancestors"][0]["route"] != control["route"]
        ):
            findings.append(
                "select-option must be inside one reachable listbox in its same route"
            )
        if action == "close-modal" and (
            len(control["dialog_ancestors"]) != 1
            or control["dialog_ancestors"][0]["route"] != control["route"]
        ):
            findings.append(
                "close-modal must be inside one reachable dialog in its same route"
            )
    for singleton_action in ("toggle-theme", "toggle-privacy"):
        controls = [
            control for control in scanner.controls
            if control["action"] == singleton_action
        ]
        if len(controls) != 1 or controls[0]["route"] != "":
            findings.append(
                f"{singleton_action} must have exactly one application-wide control"
            )
    disclosure_targets: dict[str, int] = {}
    for control in scanner.controls:
        if control["action"] in {"toggle-menu", "toggle-drawer"}:
            key = control["aria_controls"]
            disclosure_targets[key] = disclosure_targets.get(key, 0) + 1
    if any(count != 1 for count in disclosure_targets.values()):
        findings.append(
            "each toggle-menu or toggle-drawer target must have exactly one state owner"
        )
    listboxes = {row["position"]: row for row in scanner.listboxes}
    options_by_listbox: dict[int, list[dict]] = {}
    for option in scanner.listbox_options:
        ancestors = option["listbox_ancestor_positions"]
        if len(ancestors) != 1 or ancestors[0] not in listboxes:
            findings.append(
                "every role=option must belong to exactly one canonical listbox"
            )
            continue
        options_by_listbox.setdefault(ancestors[0], []).append(option)
        if (
            option["tag"] != "button"
            or option["type"] != "button"
            or option["action"] != "select-option"
            or option["aria_selected"] not in {"true", "false"}
            or not option["data_value"]
            or not reachable_node(option, reachable_containers)
            or not control_accessible_name(option, scanner)
            or not visible_action_label(option, scanner)
        ):
            findings.append(
                "every listbox option must be one reachable, named select-option button with exact state and value"
            )
    for position, listbox in listboxes.items():
        if listbox.get("tag") not in SAFE_DYNAMIC_CONTAINER_TAGS:
            findings.append(
                "listboxes must use a browser-stable semantic container"
            )
        if listbox["aria_multiselectable"] not in {"", "false"}:
            findings.append(
                "the fixed listbox runtime supports only single-select listboxes"
            )
        if (
            listbox["has_tabindex"]
            or listbox["aria_activedescendant"]
            or listbox["aria_orientation"] not in {"", "vertical"}
            or listbox["aria_attributes"] - {
                "aria-label", "aria-labelledby", "aria-multiselectable",
                "aria-orientation",
            }
        ):
            findings.append(
                "listbox containers cannot declare an independent focus or horizontal keyboard model"
            )
        if not reachable_node(listbox, reachable_containers):
            findings.append(
                "listboxes must be reachable from visible application UI"
            )
        if not control_accessible_name(listbox, scanner):
            findings.append("listboxes must expose a non-empty accessible name")
        if not visible_control_label_matches(listbox, scanner):
            findings.append(
                "listboxes must bind their accessible purpose to one matching visible label"
            )
        options = options_by_listbox.get(position, [])
        if not options:
            findings.append("every listbox must contain at least one reachable option")
        option_positions = {option["position"] for option in options}
        if (
            set(listbox["descendant_positions"]) != option_positions
            or set(listbox["direct_child_positions"]) != option_positions
        ):
            findings.append(
                "listboxes may contain only direct, text-only canonical option controls"
            )
        values = [option["data_value"] for option in options]
        if len(values) != len(set(values)):
            findings.append("listbox option data-value values must be unique")
        names = [
            normalized_accessible_label(
                control_accessible_name(option, scanner)
            )
            for option in options
        ]
        if any(not name for name in names) or len(names) != len(set(names)):
            findings.append(
                "listbox option accessible names must be non-empty and unique"
            )
        visible_names = [
            normalized_accessible_label(
                resolved_accessible_text(option, scanner)
            )
            for option in options
        ]
        if (
            any(not name for name in visible_names)
            or len(visible_names) != len(set(visible_names))
            or any(
                visible not in accessible
                for visible, accessible in zip(visible_names, names)
            )
        ):
            findings.append(
                "listbox option visible labels must be unique and contained in their accessible names"
            )
        if sum(
            option["aria_selected"] == "true" for option in options
        ) > 1:
            findings.append(
                "single-select listboxes cannot begin with multiple selected options"
            )
    if scanner.forms_without_route:
        findings.append("forms must declare a route target handled by the fixed runtime")
    for form in scanner.routed_forms:
        if form["has_tabindex"]:
            findings.append(
                "routed form owners cannot declare tabindex; only validated descendant fields and submit controls own focus"
            )
        if form["native_overrides"]:
            findings.append(
                "routed forms cannot declare native navigation or validation overrides: "
                + ", ".join(form["native_overrides"])
            )
        form_topology = runtime_interaction_topology(form)
        if any(
            runtime_interaction_topology(node) != form_topology
            for node in [*form["fields"], *form["submit_affordances"]]
        ):
            findings.append(
                "routed form owners, fields and submit controls must share one exact disclosure and dialog topology"
            )
        composed_submit = any(
            submit["action"]
            or submit["transition_ref"]
            or submit["simulation_id"]
            or submit["route_target"]
            or submit["preserve_context"]
            or submit["return_route"]
            or submit["form_override"]
            for submit in form["submit_affordances"]
        )
        if composed_submit:
            findings.append(
                "routed form submit controls cannot declare independent application actions or routing identities"
            )
        if any(submit.get("role") for submit in form["submit_affordances"]):
            findings.append(
                "routed form submit controls cannot override their native accessibility role"
            )
        if any(
            field["form_override"] for field in form["fields"]
        ):
            findings.append(
                "routed form fields cannot override their exact owning form"
            )
        if any(
            field["role"]
            or field["aria_attributes"] - PASSIVE_ARIA_ATTRIBUTES
            or not aria_accessible_name(field, scanner)
            or not visible_control_label_matches(field, scanner)
            or not reachable_node(field, reachable_containers)
            or not sequentially_keyboard_reachable(field)
            for field in form["fields"]
        ):
            findings.append(
                "routed form fields must be named, reachable native controls in sequential keyboard navigation"
            )
        if any(
            not routed_form_field_constraints_are_satisfiable(field)
            for field in form["fields"]
        ):
            findings.append(
                "routed form fields need a type-appropriate, mechanically satisfiable native constraint domain"
            )
        if (
            not reachable_node(form, reachable_containers)
            or not form["submit_affordances"]
            or any(
                not reachable_node(submit, reachable_containers)
                or not sequentially_keyboard_reachable(submit)
                or not control_accessible_name(submit, scanner)
                or not visible_submit_label(submit, scanner)
                or not visible_label_is_in_accessible_name(submit, scanner)
                or bool(
                    submit["aria_attributes"] - PASSIVE_ARIA_ATTRIBUTES
                )
                for submit in form["submit_affordances"]
            )
        ):
            findings.append(
                "routed forms need only named, enabled, reachable descendant submit controls in sequential keyboard navigation with passive ARIA"
            )
    for search in scanner.search_controls:
        route_items = [
            item for item in scanner.search_items
            if item["route"] == search["route"]
        ]
        valid_items = [
            item for item in route_items
            if runtime_collection_item_reachable(item, reachable_containers)
            and bool(runtime_collection_accessible_text(item, scanner))
        ]
        if (
            search["tag"] != "input"
            or search["type"] != "search"
            or search.get("role", "")
            or search["readonly"]
            or search["aria_readonly"] not in {"", "false"}
            or search["aria_attributes"] - PASSIVE_ARIA_ATTRIBUTES
            or not search["route"]
            or not reachable_node(search, reachable_containers)
            or not sequentially_keyboard_reachable(search)
            or not aria_accessible_name(search, scanner)
            or not visible_control_label_matches(search, scanner)
            or not route_items
            or len(valid_items) != len(route_items)
        ):
            findings.append(
                "application search needs one enabled input[type=search] and same-route search items"
            )
    search_counts: dict[str, int] = {}
    for search in scanner.search_controls:
        search_counts[search["route"]] = search_counts.get(search["route"], 0) + 1
    if any(count != 1 for count in search_counts.values()):
        findings.append(
            "each route with application search must expose exactly one search control"
        )
    search_item_routes = {item["route"] for item in scanner.search_items}
    if search_item_routes != set(search_counts):
        findings.append(
            "each route with search items must own exactly one application search control"
        )
    filter_options: dict[int, list[dict]] = {}
    for option in scanner.filter_options:
        filter_options.setdefault(option["filter_position"], []).append(option)
    filter_token = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    filter_token_list = re.compile(
        r"^[a-z0-9]+(?:-[a-z0-9]+)*(?: [a-z0-9]+(?:-[a-z0-9]+)*)*$"
    )
    for filter_control in scanner.filter_controls:
        route_items = [
            item for item in scanner.filter_items
            if item["route"] == filter_control["route"]
        ]
        options = filter_options.get(filter_control["position"], [])
        option_values = [option["value"] for option in options]
        option_domain = set(option_values)
        raw_item_values = [item["filter_value"] for item in route_items]
        item_value_rows = [value.split(" ") for value in raw_item_values]
        item_values = {value for row in item_value_rows for value in row}
        if (
            filter_control["tag"] != "select"
            or filter_control.get("role", "")
            or filter_control["multiple"]
            or filter_control["aria_attributes"] - PASSIVE_ARIA_ATTRIBUTES
            or not filter_control["route"]
            or not reachable_node(filter_control, reachable_containers)
            or not sequentially_keyboard_reachable(filter_control)
            or not aria_accessible_name(filter_control, scanner)
            or not visible_control_label_matches(filter_control, scanner)
            or not route_items
            or any(
                not runtime_collection_item_reachable(
                    item, reachable_containers,
                )
                or not runtime_collection_accessible_text(item, scanner)
                for item in route_items
            )
            or any(not option["has_value"] for option in options)
            or len(option_values) != len(option_domain)
            or option_values.count("") != 1
            or not (option_domain - {""})
            or any(option["hidden"] or option["disabled"] for option in options)
            or any(
                not has_visible_content(
                    option["label"] if option["has_label"] else option["text"]
                )
                for option in options
            )
            or len({
                normalized_accessible_label(
                    option["label"] if option["has_label"] else option["text"]
                )
                for option in options
            }) != len(options)
            or any(
                not filter_token_list.fullmatch(value)
                for value in raw_item_values
            )
            or any(len(row) != len(set(row)) for row in item_value_rows)
            or any(not filter_token.fullmatch(value) for value in item_values)
            or item_values != option_domain - {""}
        ):
            findings.append(
                "application filters need one named reachable select and exact same-route filter values"
            )
    filter_counts: dict[str, int] = {}
    for filter_control in scanner.filter_controls:
        route = filter_control["route"]
        filter_counts[route] = filter_counts.get(route, 0) + 1
    if any(count != 1 for count in filter_counts.values()):
        findings.append(
            "each route with application filters must expose exactly one filter control"
        )
    filter_item_routes = {item["route"] for item in scanner.filter_items}
    if filter_item_routes != set(filter_counts):
        findings.append(
            "each route with filter items must own exactly one application filter control"
        )
    collection_items = list({
        item["position"]: item
        for item in [*scanner.search_items, *scanner.filter_items]
    }.values())
    for item in collection_items:
        if item["tag"] not in SAFE_COLLECTION_ITEM_TAGS:
            findings.append(
                "application collection items must use a non-form browser-stable semantic container"
            )
    for index, item in enumerate(collection_items):
        for peer in collection_items[index + 1:]:
            if item["route"] != peer["route"]:
                continue
            if (
                item["position"] in peer["ancestor_positions"]
                or peer["position"] in item["ancestor_positions"]
            ):
                findings.append(
                    "same-route application collection items cannot contain one another"
                )
                break
    collection_positions = {item["position"] for item in collection_items}
    for control in scanner.controls:
        if collection_positions.intersection(
            control.get("ancestor_positions", ())
        ):
            findings.append(
                "application routing and action controls must remain outside every collection item the runtime can hide"
            )
    for control in scanner.controls:
        if control["action"] not in {"toggle-menu", "toggle-drawer"}:
            continue
        targets = scanner.elements_by_id.get(control["aria_controls"], [])
        if (
            len(targets) == 1
            and targets[0].get("_position_internal") in collection_positions
        ):
            findings.append(
                "application collection items cannot also be fixed-runtime disclosure targets"
            )
    for controller in [*scanner.search_controls, *scanner.filter_controls]:
        for item in collection_items:
            if controller["route"] != item["route"]:
                continue
            if (
                controller["position"] == item["position"]
                or controller["position"] in item["ancestor_positions"]
                or item["position"] in controller["ancestor_positions"]
            ):
                findings.append(
                    "application search/filter controls must remain outside every collection item they can hide"
                )
                break
    for dialog in scanner.dialogs:
        close_controls = [
            control for control in scanner.controls
            if control["action"] == "close-modal"
            and dialog["id"] in control["dialog_ancestor_ids"]
        ]
        dialog_aria_invalid = (
            bool(
                dialog["aria_attributes"]
                - PASSIVE_ARIA_ATTRIBUTES
                - {"aria-modal"}
            )
            or dialog["has_aria_modal"] and dialog["aria_modal"] != "true"
        )
        if dialog_aria_invalid:
            findings.append(
                "dialogs allow passive naming/description ARIA and optional aria-modal=true only"
            )
        if (
            not dialog["id"]
            or dialog["id"] not in reachable_containers
            or not reachable_node(dialog, reachable_containers)
            or not aria_accessible_name(dialog, scanner)
            or not close_controls
        ):
            findings.append(
                "every dialog needs one reachable open/close path and non-empty accessible name"
            )
    for target in scanner.private_targets:
        if (
            not reachable_node(target, reachable_containers)
            or not (
                has_visible_content(target.get("text", ""))
                or has_visible_content(target.get("value", ""))
            )
        ):
            findings.append(
                "privacy-masked content must be non-empty and reachable from visible application UI"
            )
        if (
            target["tag"] not in SAFE_PRIVATE_TARGET_TAGS
            or target["has_element_descendant"]
            or target["identity_ancestor"]
        ):
            findings.append(
                "data-private must identify a text-only passive leaf outside application identity and controls"
            )
    for target in scanner.outcome_targets:
        if (
            target["tag"] not in {"div", "p", "span"}
            or target["route"] not in by_route
            or target["marker_value"]
            or target["has_element_descendant"]
            or normalize_ascii_whitespace(target["text"])
            or not reachable_node(target, reachable_containers)
        ):
            findings.append(
                "data-application-outcome must be an empty, reachable, text-only leaf in one declared route"
            )
    if not authoring and not empty_application:
        actions = [control["action"] for control in scanner.controls]
        onboarding_ok = (
            len(scanner.onboarding_targets) == 1
            and scanner.onboarding_targets[0]["tag"] == "dialog"
            and scanner.onboarding_targets[0]["id"] in reachable_containers
            and reachable_node(
                scanner.onboarding_targets[0], reachable_containers
            )
        )
        disclosure_target_ids = {
            control["aria_controls"] for control in scanner.controls
            if control["action"] in {"toggle-menu", "toggle-drawer"}
        }
        settings_ok = (
            len(scanner.settings_targets) == 1
            and scanner.settings_targets[0]["id"] in disclosure_target_ids
            and reachable_node(scanner.settings_targets[0], reachable_containers)
            and any(
                scanner.settings_targets[0]["id"] in control["ancestor_ids"]
                for control in scanner.controls
            )
        )
        menu_ok = any(
            control["action"] == "toggle-menu"
            and any(
                control["aria_controls"] in nested["ancestor_ids"]
                for nested in scanner.controls
            )
            for control in scanner.controls
        )
        missing_features = [
            label for present, label in (
                (bool(scanner.routed_forms), "forms"),
                (bool(scanner.filter_controls), "filters"),
                (bool(scanner.search_controls), "search"),
                (menu_ok, "custom menus"),
                (bool(scanner.listboxes), "custom listboxes"),
                ("toggle-drawer" in actions, "drawers"),
                ("open-modal" in actions and bool(scanner.dialogs), "modal/overlay"),
                (onboarding_ok, "onboarding"),
                (settings_ok, "settings"),
            ) if not present
        ]
        if missing_features:
            findings.append(
                "application working interaction coverage is missing: "
                + ", ".join(missing_features)
            )
    context_key_pattern = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
    context_groups: dict[tuple[str, str], list[dict]] = {}
    allowed_input_types = {
        "checkbox", "color", "date", "datetime-local", "email", "month",
        "number", "password", "radio", "range", "search", "tel", "text",
        "time", "url", "week",
    }
    for context in scanner.context_elements:
        context_groups.setdefault(
            (context["route"], context["key"]), []
        ).append(context)
        if not context_key_pattern.fullmatch(context["key"]):
            findings.append(
                "data-context-key must use a normalized lowercase identifier"
            )
        input_type = context["type"] or "text"
        if context["tag"] == "input" and input_type == "file":
            findings.append(
                "file inputs cannot preserve context through the fixed local runtime"
            )
        supported = (
            context["role"] == "listbox"
            or context["tag"] in {"select", "textarea"}
            or (
                context["tag"] == "input"
                and input_type in allowed_input_types
            )
        )
        if (
            not supported
            or not context["route"]
            or not reachable_node(context, reachable_containers)
            or (
                context["tag"] in {"input", "select", "textarea"}
                and not sequentially_keyboard_reachable(context)
            )
            or not aria_accessible_name(context, scanner)
            or not visible_control_label_matches(context, scanner)
            or context["aria_attributes"] - PASSIVE_ARIA_ATTRIBUTES
            or (
                context["tag"] in {"input", "select", "textarea"}
                and context["role"]
            )
        ):
            findings.append(
                "data-context-key requires one named, reachable supported leaf control or canonical listbox without native role overrides"
            )
    context_shapes: dict[tuple[str, str], tuple] = {}
    context_option_values: dict[int, list[dict]] = {}
    for option in scanner.context_options:
        context_option_values.setdefault(
            option["context_position"], []
        ).append(option)
    for context in scanner.context_elements:
        if context["role"] == "listbox":
            continue
        if not context_control_constraints_are_satisfiable(
            context, context_option_values.get(context["position"], []),
        ):
            findings.append(
                "data-context-key native controls need type-appropriate mechanically satisfiable constraints"
            )
    for identity, group in context_groups.items():
        input_types = [
            (item["type"] or "text") if item["tag"] == "input" else ""
            for item in group
        ]
        if group and all(
            item["tag"] == "input" and input_type == "checkbox"
            for item, input_type in zip(group, input_types)
        ):
            values = [item["value"] for item in group]
            names = [
                normalized_accessible_label(
                    aria_accessible_name(item, scanner)
                )
                for item in group
            ]
            if any(not value for value in values) or len(values) != len(set(values)):
                findings.append(
                    "checkbox context groups need explicit unique non-empty values"
                )
            if any(not name for name in names) or len(names) != len(set(names)):
                findings.append(
                    "checkbox context groups need distinct accessible choice names"
                )
            context_shapes[identity] = (
                "checkbox",
                tuple(sorted(
                    (
                        item["value"],
                        aria_accessible_name(item, scanner),
                        item["constraints"],
                    )
                    for item in group
                )),
            )
            continue
        if group and all(
            item["tag"] == "input" and input_type == "radio"
            for item, input_type in zip(group, input_types)
        ):
            values = [item["value"] for item in group]
            accessible_names = [
                normalized_accessible_label(
                    aria_accessible_name(item, scanner)
                )
                for item in group
            ]
            names = {item["name"] for item in group}
            form_owners = {item["form_owner_position"] for item in group}
            if (
                any(not value for value in values)
                or len(values) != len(set(values))
                or len(names) != 1
                or "" in names
                or sum(bool(item["checked"]) for item in group) > 1
                or any(not name for name in accessible_names)
                or len(accessible_names) != len(set(accessible_names))
            ):
                findings.append(
                    "radio context groups need one name, unique values and at most one checked control"
                )
            if len(form_owners) != 1:
                findings.append(
                    "radio context groups must share one native form owner"
                )
            owner_shape = (
                "form" if len(form_owners) == 1 and next(iter(form_owners))
                else "document" if form_owners == {0}
                else "split"
            )
            context_shapes[identity] = (
                "radio", owner_shape,
                tuple(sorted(
                    (
                        item["value"],
                        aria_accessible_name(item, scanner),
                        item["constraints"],
                    )
                    for item in group
                )),
            )
            continue
        if len(group) != 1:
            findings.append(
                "non-checkbox/radio context keys must identify exactly one control per route"
            )
            continue
        item = group[0]
        if item["role"] == "listbox":
            option_values = tuple(sorted(
                (
                    option["data_value"],
                    control_accessible_name(option, scanner),
                )
                for option in options_by_listbox.get(item["position"], [])
            ))
            context_shapes[identity] = ("listbox", option_values)
        elif item["tag"] == "input":
            context_shapes[identity] = (
                "input", item["type"] or "text", item["constraints"],
            )
        elif item["tag"] == "select":
            select_options = context_option_values.get(item["position"], [])
            values = [option["value"] for option in select_options]
            option_names = [
                normalized_accessible_label(
                    option["label"] if option["has_label"] else option["text"]
                )
                for option in select_options
            ]
            if (
                not select_options
                or any(not option["has_value"] for option in select_options)
                or len(values) != len(set(values))
                or any(not name for name in option_names)
                or len(option_names) != len(set(option_names))
                or any(
                    option["hidden"]
                    or option["disabled"]
                    or not has_visible_content(
                        option["label"]
                        if option["has_label"] else option["text"]
                    )
                    for option in select_options
                )
            ):
                findings.append(
                    "select context controls need visible, enabled, named options with explicit unique values"
                )
            context_shapes[identity] = (
                "select", "multiple" if item["multiple"] else "single",
                tuple(sorted(
                    (
                        option["value"],
                        str(
                            option["label"]
                            if option["has_label"] else option["text"]
                        ),
                    )
                    for option in select_options
                )),
                item["constraints"],
            )
        elif item["tag"] == "textarea":
            context_shapes[identity] = ("textarea", item["constraints"])
    contextual_radio_names = {
        radio["name"] for radio in scanner.native_radios
        if radio["has_context_key"] and radio["name"]
    }
    for name in sorted(contextual_radio_names):
        owners = {
            (radio["route"], radio["context_key"])
            if radio["has_context_key"]
            else ("<non-context>", str(radio["position"]))
            for radio in scanner.native_radios
            if radio["name"] == name
        }
        if len(owners) != 1:
            findings.append(
                f"context radio name {name} must belong to exactly one route/context identity"
            )
    for edge in edges:
        for key in edge.get("preserve_context", []):
            source_shape = context_shapes.get((edge["source"], key))
            target_shape = context_shapes.get((edge["target"], key))
            if source_shape is not None and target_shape is not None \
                    and source_shape != target_shape:
                findings.append(
                    f"transition {edge['transition_ref']} context key {key} has incompatible source and target control shapes"
                )
    reachable_record_routes: dict[str, set[str]] = {}
    for reference, targets in scanner.record_targets.items():
        for target in targets:
            if (
                reachable_node(target, reachable_containers)
                and (
                    resolved_accessible_text(target, scanner)
                    or has_visible_content(target.get("value", ""))
                )
            ):
                reachable_record_routes.setdefault(reference, set()).add(
                    target["route"]
                )
            else:
                findings.append(
                    f"rendered Experience record {reference} must expose non-empty accessible content reachable from visible application UI"
                )
    adjacency = {route: set() for route in by_route}
    for edge in edges:
        if edge["source"] in adjacency and edge.get("target") in by_route:
            adjacency[edge["source"]].add(edge["target"])
        if edge.get("target") in adjacency and edge.get("return_route") in by_route:
            adjacency[edge["target"]].add(edge["return_route"])
    for simulation in simulation_edges:
        if simulation["source"] in adjacency and simulation["target"] in by_route:
            adjacency[simulation["source"]].add(simulation["target"])
        if simulation["target"] in adjacency and simulation["return_route"] in by_route:
            adjacency[simulation["target"]].add(simulation["return_route"])
    visited: set[str] = set()
    pending = deque([entry] if entry in by_route else [])
    while pending:
        route = pending.popleft()
        if route in visited:
            continue
        visited.add(route)
        pending.extend(sorted(adjacency[route] - visited))
    for route in sorted(set(by_route) - visited):
        findings.append(f"application route is unreachable from entry_route: {route}")
    map_refs: set[str] = set()
    map_entries: dict[str, set[tuple[str, str]]] = {}
    for package_map in maps:
        map_experience = package_map.get("experience_id", "")
        for binding in package_map.get("bindings", []):
            reference = binding.get("record_ref", "")
            map_refs.add(reference)
            for mapped_entry in binding.get("entries", []):
                route = mapped_entry.get("route", "")
                state_class = mapped_entry.get("state_class", "")
                map_entries.setdefault(reference, set()).add((route, state_class))
                if not authoring and (
                    route not in by_route
                    or reference not in set(by_route.get(route, {}).get("record_refs", []))
                ):
                    findings.append(
                        f"application map binding {reference} -> {route} / {state_class} is absent from the contract"
                    )
                if route in by_route and state_class != by_route[route]["state_class"]:
                    findings.append(
                        f"application map binding {reference} -> {route} has a stale state_class"
                    )
                if not authoring and route not in reachable_record_routes.get(reference, set()):
                    findings.append(
                        f"application map binding {reference} -> {route} has no rendered DOM record"
                    )
                if route in by_route and by_route[route]["experience_id"] != map_experience:
                    findings.append(
                        f"application map binding {reference} -> {route} crosses route ownership"
                    )
    if not authoring and map_refs != expected_refs:
        for reference in sorted(expected_refs - map_refs):
            findings.append(f"process application maps do not cover active ref {reference}")
    if not authoring:
        for reference in sorted(map_refs - expected_refs):
            findings.append(f"process application maps contain unknown ref {reference}")
        for reference in sorted(set(scanner.record_routes) - expected_refs):
            findings.append(f"HTML renders an unknown, retired or stale ref {reference}")
    contract_entries: dict[str, set[tuple[str, str]]] = {}
    for route, row in by_route.items():
        for reference in row.get("record_refs", []):
            contract_entries.setdefault(reference, set()).add(
                (route, row.get("state_class", ""))
            )
    if not authoring:
        for reference in sorted(expected_refs):
            if map_entries.get(reference, set()) != contract_entries.get(reference, set()):
                findings.append(
                    f"application map route/state entries are not exact for {reference}"
                )
            rendered_entries = {
                (route, by_route.get(route, {}).get("state_class", ""))
                for route in reachable_record_routes.get(reference, set())
            }
            if map_entries.get(reference, set()) != rendered_entries:
                findings.append(
                    f"rendered DOM route/state entries are not exact for {reference}"
                )
            required_state = expected_records.get(reference, "")
            if required_state and {
                state for _route, state in map_entries.get(reference, set())
            } != {required_state}:
                findings.append(
                    f"state record {reference} is not rendered as {required_state}"
                )
    return {
        "entry_route": entry,
        "routes": [
            {
                "route": route,
                "state_class": by_route[route].get("state_class", ""),
                "experience_id": by_route[route].get("experience_id", ""),
            }
            for route in sorted(by_route)
        ],
        "transitions": sorted(
            [
                {
                    "source": edge["source"],
                    "transition_ref": edge.get("transition_ref"),
                    "target": edge.get("target"),
                    "outcome": edge.get("outcome"),
                    "preserve_context": edge.get("preserve_context"),
                    "return_route": edge.get("return_route"),
                }
                for edge in edges
            ],
            key=lambda row: (row["source"], row["transition_ref"]),
        ),
        "simulations": sorted(
            simulation_edges,
            key=lambda row: (row["source"], row["simulation_id"]),
        ),
        "record_refs": sorted(contract_refs),
        "state_classes": sorted(represented_states),
    }


def _hash_field(value: object, label: str, findings: list[str]) -> bool:
    if type(value) is not str or not HASH.fullmatch(value):
        findings.append(f"{label} must be a sha256 digest")
        return False
    return True


def _string_list(
    value: object,
    label: str,
    findings: list[str],
    *,
    pattern: re.Pattern | None = None,
    allowed: set[str] | None = None,
    sorted_unique: bool = False,
) -> list[str]:
    if type(value) is not list or any(type(item) is not str for item in value):
        findings.append(f"{label} must be an exact string array")
        return []
    result = value
    if len(result) != len(set(result)):
        findings.append(f"{label} must not contain duplicates")
    if sorted_unique and result != sorted(set(result)):
        findings.append(f"{label} must be sorted and unique")
    for item in result:
        if pattern is not None and not pattern.fullmatch(item):
            findings.append(f"{label} contains an invalid value: {item}")
        if allowed is not None and item not in allowed:
            findings.append(f"{label} contains an unsupported value: {item}")
    return result


def _validate_coverage(value: object, label: str, findings: list[str]) -> None:
    if type(value) is not dict or set(value) != COVERAGE_KEYS:
        findings.append(f"{label} must contain the exact coverage fields")
        return
    entry = value.get("entry_route")
    if type(entry) is not str or not CONTRACT_ROUTE.fullmatch(entry):
        findings.append(f"{label}.entry_route must be a normalized deep route")
    routes = value.get("routes")
    if type(routes) is not list:
        findings.append(f"{label}.routes must be an array")
        routes = []
    route_order: list[str] = []
    experience_pattern = re.compile(CONTRACT_SCHEMA["experience_pattern"])
    for index, route in enumerate(routes):
        row_label = f"{label}.routes[{index}]"
        if type(route) is not dict or set(route) != COVERAGE_ROUTE_KEYS:
            findings.append(f"{row_label} must contain the exact route fields")
            continue
        route_value = route.get("route")
        state_value = route.get("state_class")
        experience_value = route.get("experience_id")
        if type(route_value) is not str or not CONTRACT_ROUTE.fullmatch(route_value):
            findings.append(f"{row_label}.route must be a normalized deep route")
        else:
            route_order.append(route_value)
        if type(state_value) is not str or state_value not in REQUIRED_STATE_CLASSES:
            findings.append(f"{row_label}.state_class is unsupported")
        if type(experience_value) is not str or not experience_pattern.fullmatch(experience_value):
            findings.append(f"{row_label}.experience_id is invalid")
    if route_order != sorted(set(route_order)):
        findings.append(f"{label}.routes must be sorted and unique")

    transitions = value.get("transitions")
    if type(transitions) is not list:
        findings.append(f"{label}.transitions must be an array")
        transitions = []
    transition_order: list[tuple[str, str]] = []
    for index, transition in enumerate(transitions):
        row_label = f"{label}.transitions[{index}]"
        if type(transition) is not dict or set(transition) != COVERAGE_TRANSITION_KEYS:
            findings.append(f"{row_label} must contain the exact transition fields")
            continue
        source = transition.get("source")
        reference = transition.get("transition_ref")
        target = transition.get("target")
        outcome = transition.get("outcome")
        return_route = transition.get("return_route")
        for field, route_value, allow_empty in (
            ("source", source, False), ("target", target, False),
            ("return_route", return_route, True),
        ):
            if type(route_value) is not str or (
                route_value == "" and not allow_empty
            ) or (route_value and not CONTRACT_ROUTE.fullmatch(route_value)):
                findings.append(f"{row_label}.{field} must be a normalized deep route")
        if type(reference) is not str or not CONTRACT_EXACT.fullmatch(reference) \
                or ":TRN-" not in reference:
            findings.append(f"{row_label}.transition_ref is invalid")
        if type(outcome) is not str or not OUTCOME.fullmatch(outcome):
            findings.append(f"{row_label}.outcome is invalid")
        _string_list(
            transition.get("preserve_context"),
            f"{row_label}.preserve_context",
            findings,
        )
        if type(source) is str and type(reference) is str:
            transition_order.append((source, reference))
    if transition_order != sorted(set(transition_order)):
        findings.append(f"{label}.transitions must be sorted and unique")

    simulations = value.get("simulations")
    if type(simulations) is not list:
        findings.append(f"{label}.simulations must be an array")
        simulations = []
    simulation_order: list[tuple[str, str]] = []
    for index, simulation in enumerate(simulations):
        row_label = f"{label}.simulations[{index}]"
        if type(simulation) is not dict or set(simulation) != COVERAGE_SIMULATION_KEYS:
            findings.append(f"{row_label} must contain the exact simulation fields")
            continue
        simulation_id = simulation.get("simulation_id")
        source = simulation.get("source")
        for field in ("source", "target", "return_route"):
            route_value = simulation.get(field)
            if type(route_value) is not str or not CONTRACT_ROUTE.fullmatch(route_value):
                findings.append(f"{row_label}.{field} must be a normalized deep route")
        if type(simulation_id) is not str or not SIMULATION_ID.fullmatch(simulation_id):
            findings.append(f"{row_label}.simulation_id is invalid")
        outcome = simulation.get("outcome")
        if type(outcome) is not str or not OUTCOME.fullmatch(outcome):
            findings.append(f"{row_label}.outcome is invalid")
        if type(source) is str and type(simulation_id) is str:
            simulation_order.append((source, simulation_id))
    if simulation_order != sorted(set(simulation_order)):
        findings.append(f"{label}.simulations must be sorted and unique")

    _string_list(
        value.get("record_refs"), f"{label}.record_refs", findings,
        pattern=CONTRACT_EXACT, sorted_unique=True,
    )
    _string_list(
        value.get("state_classes"), f"{label}.state_classes", findings,
        allowed=REQUIRED_STATE_CLASSES, sorted_unique=True,
    )


def _validate_registry_receipt(
    value: object, label: str, findings: list[str]
) -> None:
    if type(value) is not dict or set(value) != REGISTRY_KEYS:
        findings.append(f"{label} must contain the exact registry fields")
        return
    if type(value.get("schema_version")) is not int \
            or value.get("schema_version") != 2:
        findings.append(f"{label}.schema_version must be integer 2")
    revision = value.get("application_revision")
    if type(revision) is not int or revision < 1:
        findings.append(f"{label}.application_revision must be a positive integer")
    for field in (
        "source_hash", "package_set_hash", "coverage_hash", "runtime_sha256",
        "previous_application_hash", "application_hash",
    ):
        _hash_field(value.get(field), f"{label}.{field}", findings)

    design = value.get("design_system")
    if type(design) is not dict or set(design) != DESIGN_SYSTEM_RECEIPT_KEYS:
        findings.append(f"{label}.design_system must contain the exact binding fields")
    else:
        _hash_field(design.get("package_hash"), f"{label}.design_system.package_hash", findings)
        _hash_field(
            design.get("master_source_hash"),
            f"{label}.design_system.master_source_hash",
            findings,
        )
        if type(design.get("revision")) is not int or design.get("revision") < 1:
            findings.append(f"{label}.design_system.revision must be a positive integer")

    packages = value.get("packages")
    if type(packages) is not list:
        findings.append(f"{label}.packages must be an array")
        packages = []
    package_order: list[str] = []
    for index, package in enumerate(packages):
        row_label = f"{label}.packages[{index}]"
        if type(package) is not dict or set(package) != PACKAGE_RECEIPT_KEYS:
            findings.append(f"{row_label} must contain the exact package fields")
            continue
        result_ref = package.get("result_ref")
        if type(result_ref) is not str or not PACKAGE_RESULT_REF.fullmatch(result_ref):
            findings.append(f"{row_label}.result_ref is invalid")
        else:
            package_order.append(result_ref)
        _hash_field(package.get("package_hash"), f"{row_label}.package_hash", findings)
    if package_order != sorted(set(package_order)):
        findings.append(f"{label}.packages must be sorted and unique")
    if type(packages) is list:
        try:
            expected_package_hash = sha(canonical(packages))
        except (TypeError, ValueError):
            findings.append(f"{label}.packages must contain only JSON primitives")
        else:
            if value.get("package_set_hash") != expected_package_hash:
                findings.append(f"{label}.package_set_hash does not match packages")

    _validate_coverage(value.get("coverage"), f"{label}.coverage", findings)
    unsigned = {key: item for key, item in value.items() if key != "application_hash"}
    try:
        expected_application_hash = sha(canonical(unsigned))
    except (TypeError, ValueError):
        findings.append(f"{label} must contain only JSON primitives")
    else:
        if value.get("application_hash") != expected_application_hash:
            findings.append(f"{label}.application_hash does not match its receipt")


def verified_application_ledger(
    root_value: str | Path,
) -> tuple[list[dict], list[str]]:
    """Return ledger rows plus exact-schema, sequence and hash findings."""
    root = root_for(root_value)
    ledger = root / LEDGER_RELATIVE
    ledger_parent = ledger.parent
    if ledger_parent.is_symlink() or (
        ledger_parent.exists() and not ledger_parent.is_dir()
    ):
        return [], ["application revision ledger parent must be one regular directory"]
    if ledger.is_symlink():
        return [], ["application revision ledger must be one regular file"]
    if not ledger.exists():
        return [], []
    findings: list[str] = []
    if not ledger.is_file() or ledger.is_symlink():
        return [], ["application revision ledger must be one regular file"]
    try:
        if ledger.stat().st_nlink != 1:
            findings.append("application revision ledger must not be hard-linked")
        value = strict_json_loads(ledger.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [], [f"application revision ledger is unreadable: {exc}"]
    if (
        type(value) is not dict
        or set(value) != {"schema_version", "revisions"}
    ):
        return [], ["application revision ledger must contain the exact root fields"]
    if type(value.get("schema_version")) is not int \
            or value.get("schema_version") != 2:
        findings.append("application revision ledger schema_version must be integer 2")
    rows = value.get("revisions")
    if type(rows) is not list:
        return [], findings + ["application revision ledger revisions must be an array"]
    revisions: list[int] = []
    expected_previous_hash = GENESIS_APPLICATION_HASH
    for index, row in enumerate(rows):
        _validate_registry_receipt(
            row, f"application revision ledger revisions[{index}]", findings
        )
        if type(row) is dict and type(row.get("application_revision")) is int:
            revisions.append(row["application_revision"])
        if type(row) is dict:
            if row.get("previous_application_hash") != expected_previous_hash:
                findings.append(
                    "application revision ledger hash chain is stale or tampered"
                )
            if HASH.fullmatch(str(row.get("application_hash", ""))):
                expected_previous_hash = str(row["application_hash"])
    if revisions != list(range(1, len(rows) + 1)):
        findings.append(
            "application revision ledger must contain one ordered, contiguous receipt per revision"
        )
    published_processes: dict[str, str] = {}
    for row in rows:
        if type(row) is not dict or type(row.get("packages")) is not list:
            continue
        for package in row["packages"]:
            if type(package) is not dict:
                continue
            result_ref = package.get("result_ref")
            package_hash = package.get("package_hash")
            if type(result_ref) is not str or type(package_hash) is not str:
                continue
            if (
                result_ref in published_processes
                and published_processes[result_ref] != package_hash
            ):
                findings.append(
                    "application revision ledger reuses a process receipt with conflicting immutable hashes"
                )
            published_processes[result_ref] = package_hash
    return [row for row in rows if type(row) is dict], sorted(set(findings))


def application_ledger(root: Path) -> list[dict]:
    rows, findings = verified_application_ledger(root)
    return [] if findings else rows


def validate_application_ledger(
    root: Path, current_revision: int, findings: list[str]
) -> list[dict]:
    rows, ledger_findings = verified_application_ledger(root)
    findings.extend(ledger_findings)
    revisions = [
        row["application_revision"] for row in rows
        if type(row.get("application_revision")) is int
    ]
    if revisions != list(range(1, current_revision + 1)):
        findings.append(
            "application revision ledger must contain one ordered, contiguous receipt per revision"
        )
    return rows


def approved_snapshot(root_value: str | Path) -> tuple[dict, list[str]]:
    """Verify the last approved application without requiring current inputs.

    This is a revision-opening preimage check only. It proves that the bytes,
    fixed runtime, process hashes, generated registry and durable ledger still
    match the prior approval. New approval always uses the current inputs.
    """
    root = root_for(root_value)
    application = root / APPLICATION_RELATIVE
    findings: list[str] = []
    if not application.is_file() or application.is_symlink():
        return {}, ["approved application snapshot is missing"]
    text = application.read_text(encoding="utf-8")
    scanner = ApplicationScanner()
    scanner.feed(text)
    scanner.close()
    findings.extend(document_structure_findings(scanner))
    findings.extend(browser_stable_html_source_findings(text))
    if scanner.metas.get("experience-application-status") != "approved":
        findings.append("application snapshot is not approved")
    parse_contract(scanner, findings)
    findings.extend(dynamic_svg_findings(scanner))
    if (
        scanner.http_equivs != ["content-security-policy"]
        or scanner.csp_values != [expected_csp()]
    ):
        findings.append("approved application snapshot CSP is stale or invalid")
    try:
        registry = strict_json_loads(
            (root / REGISTRY_RELATIVE).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {}, [f"approved application registry is unreadable: {exc}"]
    if not isinstance(registry, dict):
        return {}, ["approved application registry must be an object"]
    _validate_registry_receipt(
        registry, "approved application registry", findings
    )
    if registry.get("source_hash") != source_hash(text):
        findings.append("approved application source hash is stale")
    unsigned = {
        key: value for key, value in registry.items()
        if key != "application_hash"
    }
    if registry.get("application_hash") != sha(canonical(unsigned)):
        findings.append("approved application registry hash is invalid")
    expected_meta = {
        "experience-application-revision": str(registry.get("application_revision", "")),
        "experience-application-source-hash": str(registry.get("source_hash", "")),
        "experience-application-package-set-hash": str(registry.get("package_set_hash", "")),
        "experience-application-coverage-hash": str(registry.get("coverage_hash", "")),
        "experience-application-hash": str(registry.get("application_hash", "")),
        "experience-application-runtime-sha256": str(registry.get("runtime_sha256", "")),
        "design-system-package-hash": str(registry.get("design_system", {}).get("package_hash", "")),
        "design-system-master-revision": str(registry.get("design_system", {}).get("revision", "")),
        "design-system-master-source-hash": str(registry.get("design_system", {}).get("master_source_hash", "")),
    }
    for name, value in expected_meta.items():
        if scanner.meta_counts.get(name) != 1 or scanner.metas.get(name) != value:
            findings.append(f"approved application snapshot metadata {name} is stale")
    revision = registry.get("application_revision")
    current_revision = revision if type(revision) is int else 0
    ledger_rows = validate_application_ledger(root, current_revision, findings)
    current_ledger = [
        row for row in ledger_rows
        if row.get("application_revision") == current_revision
    ]
    if len(current_ledger) != 1 or current_ledger[0] != registry:
        findings.append("approved application snapshot ledger is stale")
    try:
        import experience_compile
        package_rows = []
        for package in experience_compile.packages(root):
            if experience_compile.fields(package).get("status") != "approved":
                continue
            compiled, problems = experience_compile.compile_package(
                package, True, allow_stale_inputs=True
            )
            findings.extend(
                f"{package.name}: {problem}" for problem in problems
            )
            package_rows.append({
                "result_ref": f"{package.name}@r{compiled.get('package_revision', 0)}",
                "package_hash": str(compiled.get("package_hash", "")),
            })
        package_rows.sort(key=lambda row: row["result_ref"])
        if package_rows != registry.get("packages"):
            findings.append("approved application snapshot process set is stale")
        if sha(canonical(package_rows)) != registry.get("package_set_hash"):
            findings.append("approved application snapshot package-set hash is stale")
    except (ImportError, OSError, ValueError) as exc:
        findings.append(f"approved application process snapshot cannot be verified: {exc}")
    return registry, sorted(set(findings))


def compile_application(
    root_value: str | Path,
    gate: bool = False,
    *,
    package_paths: list[Path] | None = None,
    authoring: bool = False,
) -> tuple[dict, list[str]]:
    root = root_for(root_value)
    findings: list[str] = []
    if gate and authoring:
        return {}, ["application check cannot combine gate and authoring modes"]
    try:
        import experience_compile
    except ImportError as exc:
        return {}, [f"Experience compiler is unavailable: {exc}"]
    candidates = package_paths if package_paths is not None else experience_compile.packages(root)
    all_packages = experience_compile.packages(root)
    findings.extend(closed_artifact_surface_findings(root, all_packages))
    for package in all_packages:
        if not (package / "experience.md").is_file():
            findings.append(
                f"experiences/{package.name}: every package directory needs experience.md"
            )
    active = [
        package for package in candidates
        if experience_compile.fields(package).get("status")
        not in {"retirement_pending", "retired"}
        and (package / "experience.md").is_file()
    ]
    for path in sorted(root.rglob("*")) if root.is_dir() else []:
        if path.is_symlink():
            findings.append(f"{path.relative_to(root)}: symlinks are forbidden in Experience Design")
            continue
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        try:
            if path.stat().st_nlink != 1:
                findings.append(
                    f"{relative}: hard-linked files are forbidden in Experience Design"
                )
        except OSError as exc:
            findings.append(f"{relative}: cannot inspect file identity: {exc}")
        if path.suffix.casefold() in WEB_SUFFIXES and relative != APPLICATION_RELATIVE:
            findings.append(
                f"{relative}: only artifacts/application.html may contain a visual or executable implementation"
            )
        if path.name.endswith("-artifact.md"):
            findings.append(f"{relative}: package-local artifact manifests were removed")
        if path.name in {"artifact-registry.json", "artifact-manifest.md"}:
            findings.append(f"{relative}: legacy artifact indexes were removed")
        if path.suffix.casefold() in {".html", ".htm"} and relative != APPLICATION_RELATIVE:
            findings.append(f"{relative}: only artifacts/application.html may be HTML")
    if not active and not any(
            (package / "experience.md").is_file() for package in all_packages):
        if (root / APPLICATION_RELATIVE).exists():
            findings.append("application.html must not exist before the first Experience package")
        return {}, sorted(set(findings))
    application = root / APPLICATION_RELATIVE
    if not application.is_file() or application.is_symlink():
        return {}, sorted(set(findings + [
            "artifacts/application.html is required as the one regular application artifact"
        ]))
    text = application.read_text(encoding="utf-8")
    scanner = ApplicationScanner()
    try:
        scanner.feed(text)
        scanner.close()
    except Exception as exc:
        findings.append(f"application HTML cannot be parsed: {exc}")
    findings.extend(document_structure_findings(scanner))
    if scanner.inline_handlers:
        findings.append("inline event handlers are forbidden; use the fixed declarative runtime")
    if scanner.inline_styles:
        findings.append(
            "inline style attributes are forbidden; use the canonical author-style block"
        )
    if scanner.duplicate_attributes:
        findings.append(
            "application elements must not repeat attributes: "
            + ", ".join(sorted(set(scanner.duplicate_attributes)))
        )
    duplicate_ids = sorted(
        element_id for element_id, count in scanner.element_ids.items() if count != 1
    )
    if duplicate_ids:
        findings.append("application element IDs must be unique: " + ", ".join(duplicate_ids))
    main = scanner.elements_by_id.get("application-main", [])
    if (
        len(main) != 1
        or main[0].get("tag") != "main"
        or main[0].get("tabindex") != "-1"
    ):
        findings.append("application needs one focusable main#application-main runtime target")
    announcer = scanner.elements_by_id.get("application-announcer", [])
    if (
        len(announcer) != 1
        or announcer[0].get("role") != "status"
        or announcer[0].get("aria-live") != "polite"
        or "hidden" in announcer[0]
        or not element_context_reachable(announcer[0], set())
    ):
        findings.append(
            "application needs one visible, assistive-technology-reachable polite status application-announcer"
        )
    if scanner.forbidden_elements:
        findings.append(
            "application contains forbidden dependency elements: "
            + ", ".join(sorted(set(scanner.forbidden_elements)))
        )
    findings.extend(dynamic_svg_findings(scanner))
    if scanner.meta_refresh:
        findings.append("application meta refresh is forbidden")
    if (
        scanner.http_equivs != ["content-security-policy"]
        or scanner.csp_values != [expected_csp()]
    ):
        findings.append(
            "application must contain exactly the shipped network-denying Content Security Policy"
        )
    for attribute, target in scanner.targets:
        if not allowed_application_target(attribute, target):
            findings.append(f"application dependency or form target is forbidden: {target}")
    for style in scanner.styles:
        normalized_style = normalized_css(style)
        if re.search(
            r"@import\b|(?:-webkit-)?image-set\s*\(|url\s*\(|"
            r"expression\s*\(|(?:^|[;{])\s*behavior\s*:",
            normalized_style,
            re.I,
        ):
            findings.append(
                "application CSS must not import, execute or reference external resources"
            )
    contract = parse_contract(scanner, findings)
    package_rows: list[dict] = []
    maps: list[dict] = []
    expected_records: dict[str, str] = {}
    design_hashes: set[str] = set()
    for package in sorted(active, key=lambda value: value.name):
        registry, package_findings = experience_compile.compile_package(package, gate)
        findings.extend(f"{package.name}: {message}" for message in package_findings)
        package_rows.append(
            {
                "result_ref": f"{package.name}@r{registry.get('package_revision', 0)}",
                "package_hash": str(registry.get("package_hash", "")),
            }
        )
        for row in registry.get("records", []):
            if row.get("record_state") != "active":
                continue
            reference = (
                f"{package.name}:{row.get('id')}@r{row.get('revision')}"
            )
            expected_records[reference] = (
                str(row.get("state_class", ""))
                if str(row.get("id", "")).startswith("STA-")
                else ""
            )
        binding = registry.get("input_bindings", {}).get("design-system", [])
        if isinstance(binding, (list, tuple)) and len(binding) == 2:
            design_hashes.add(str(binding[1]))
        package_map, map_findings = load_application_map(package)
        maps.append(package_map)
        findings.extend(map_findings)
    design, _receipt, design_findings = design_binding(root)
    findings.extend(design_findings)
    if active and design_hashes != {str(design["package_hash"])}:
        findings.append("all active Experience packages must bind the application's current Design System receipt")
    tokens = TOKEN_PATTERN.search(text)
    design_token_names: set[str] = set()
    if tokens is None or tokens.group(1).strip() + "\n" != str(design["tokens"]):
        findings.append("application Design System token block is missing or stale")
    if tokens is not None:
        design_token_names = set(
            re.findall(r"(--[a-zA-Z0-9_-]+)\s*:", tokens.group(0))
        )
        outside_tokens = TOKEN_PATTERN.sub("", text, count=1)
        normalized_outside_tokens = normalized_css(outside_tokens)
        overridden = sorted(
            design_token_names
            & set(re.findall(
                r"(--[a-zA-Z0-9_-]+)\s*:", normalized_outside_tokens
            ))
        )
        if overridden:
            findings.append(
                "application cannot redefine Design System tokens outside the approved block: "
                + ", ".join(overridden)
            )
        consumed = set(
            re.findall(
                r"var\(\s*(--[a-zA-Z0-9_-]+)", normalized_outside_tokens
            )
        )
        if not design_token_names.intersection(consumed):
            findings.append(
                "application CSS must consume its approved Design System tokens"
            )
    if style_scaffold(text) != style_scaffold(template_text()):
        findings.append(
            "application fixed style scaffold differs from the shipped template"
        )
    author_styles = AUTHOR_STYLE_PATTERN.findall(text)
    if len(author_styles) != 1:
        findings.append("application needs exactly one canonical author-style block")
    else:
        hard_coded = hard_coded_author_properties(
            author_styles[0], design_token_names
        )
        if hard_coded:
            findings.append(
                "application author styles hard-code Design System values for: "
                + ", ".join(sorted(hard_coded))
            )
    expected_meta = {
        "experience-application-contract-version": "2",
        "experience-application-runtime-sha256": runtime_sha256(),
        "design-system-package-hash": str(design["package_hash"]),
        "design-system-master-revision": str(design["revision"]),
        "design-system-master-source-hash": str(design["master_source_hash"]),
    }
    for name, value in expected_meta.items():
        if scanner.meta_counts.get(name) != 1 or scanner.metas.get(name) != value:
            findings.append(f"application metadata {name} is missing or stale")
    for name in sorted(MACHINE_META):
        if scanner.meta_counts.get(name) != 1:
            findings.append(f"application must contain exactly one metadata field {name}")
    application_status = scanner.metas.get("experience-application-status", "")
    if application_status not in {"draft", "in_review", "approved"}:
        findings.append("application status must be draft, in_review or approved")
    if not HASH.fullmatch(
            scanner.metas.get("experience-application-proposal-hash", "")):
        findings.append("application proposal hash must identify its approved scope plan")
    graph = validate_contract(
        contract,
        expected_records,
        {package.name for package in active},
        scanner,
        maps,
        findings,
        empty_application=not active,
        authoring=authoring,
    )
    findings.extend(required_experience_findings(text, scanner))
    package_rows.sort(key=lambda row: row["result_ref"])
    normalized_maps = sorted(maps, key=lambda row: str(row.get("experience_id", "")))
    package_set_hash = sha(canonical(package_rows))
    coverage_hash = sha(canonical({"maps": normalized_maps, "graph": graph}))
    try:
        revision = int(scanner.metas.get("experience-application-revision", "0"))
    except ValueError:
        revision = 0
    if revision < 1:
        findings.append("application revision must be a positive integer")
    registry = {
        "schema_version": 2,
        "application_revision": revision,
        "source_hash": source_hash(text),
        "package_set_hash": package_set_hash,
        "coverage_hash": coverage_hash,
        "design_system": {
            "package_hash": str(design["package_hash"]),
            "revision": design["revision"],
            "master_source_hash": str(design["master_source_hash"]),
        },
        "runtime_sha256": runtime_sha256(),
        "packages": package_rows,
        "coverage": graph,
    }
    ledger_rows, ledger_findings = verified_application_ledger(root)
    findings.extend(ledger_findings)
    if revision == 1:
        registry["previous_application_hash"] = GENESIS_APPLICATION_HASH
    else:
        predecessors = [
            row for row in ledger_rows
            if row.get("application_revision") == revision - 1
        ]
        if len(predecessors) != 1:
            findings.append(
                "application revision is missing its exact predecessor receipt"
            )
            registry["previous_application_hash"] = ""
        else:
            registry["previous_application_hash"] = str(
                predecessors[0].get("application_hash", "")
            )
    registry["application_hash"] = sha(canonical(registry))
    if gate:
        if scanner.metas.get("experience-application-status") != "approved":
            findings.append("application is not approved")
        for name, value in (
            ("experience-application-source-hash", registry["source_hash"]),
            ("experience-application-package-set-hash", registry["package_set_hash"]),
            ("experience-application-coverage-hash", registry["coverage_hash"]),
            ("experience-application-hash", registry["application_hash"]),
        ):
            if scanner.metas.get(name) != value:
                findings.append(f"approved application metadata {name} is stale")
        stamp = scanner.metas.get("experience-application-approved-at-utc", "")
        try:
            approved = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if approved.tzinfo is None or approved > datetime.now(timezone.utc):
                raise ValueError
        except ValueError:
            findings.append("approved application timestamp is invalid")
        generated = root / REGISTRY_RELATIVE
        try:
            if strict_json_loads(
                generated.read_text(encoding="utf-8")
            ) != registry:
                findings.append("_generated/application-registry.json is stale or tampered")
        except (OSError, json.JSONDecodeError, ValueError):
            findings.append("_generated/application-registry.json is missing or unreadable")
        ledger_rows = validate_application_ledger(root, revision, findings)
        current = [
            row for row in ledger_rows
            if row.get("application_revision") == revision
        ]
        if len(current) != 1 or current[0] != registry:
            findings.append("application revision ledger does not contain the approved receipt")
    return registry, sorted(set(findings))


def stamp_application(text: str, registry: dict, status: str, proposal_hash: str) -> str:
    values = {
        "experience-application-status": status,
        "experience-application-revision": str(registry["application_revision"]),
        "experience-application-proposal-hash": proposal_hash,
        "experience-application-source-hash": registry["source_hash"],
        "experience-application-package-set-hash": registry["package_set_hash"],
        "experience-application-coverage-hash": registry["coverage_hash"],
        "experience-application-hash": registry["application_hash"],
        "experience-application-approved-at-utc": (
            datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            if status == "approved" else ""
        ),
    }
    for name, value in values.items():
        text = replace_meta(text, name, value)
    return text


def application_receipt(root: Path, registry: dict) -> dict:
    return {
        "stage": "experience-design",
        "result_ref": f"application@r{registry['application_revision']}",
        "result_type": "experience-application",
        "package_hash": registry["application_hash"],
        "status": "approved",
        "current": True,
    }


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        directory = os.open(path.parent, directory_flags)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise


def write_registry_and_ledger(root: Path, registry: dict) -> None:
    root = root_for(root)
    registry_findings: list[str] = []
    _validate_registry_receipt(registry, "application registry", registry_findings)
    if registry_findings:
        raise ValueError("; ".join(sorted(set(registry_findings))))
    rows, ledger_findings = verified_application_ledger(root)
    if ledger_findings:
        raise ValueError("; ".join(ledger_findings))
    revision = registry.get("application_revision")
    expected_revision = 1 if not rows else rows[-1]["application_revision"] + 1
    if type(revision) is not int or revision != expected_revision:
        raise ValueError(
            "application revision must append exactly revision "
            f"{expected_revision}; replay and gaps are forbidden"
        )
    expected_previous_hash = (
        GENESIS_APPLICATION_HASH if not rows
        else str(rows[-1].get("application_hash", ""))
    )
    if registry.get("previous_application_hash") != expected_previous_hash:
        raise ValueError(
            "application receipt must bind the exact previous application hash"
        )
    generated = root / REGISTRY_RELATIVE
    for parent, label in (
        (generated.parent, "generated application registry"),
        ((root / LEDGER_RELATIVE).parent, "application revision ledger"),
    ):
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise ValueError(f"{label} parent must be one regular directory")
    if generated.exists() and (
        not generated.is_file()
        or generated.is_symlink()
        or generated.stat().st_nlink != 1
    ):
        raise ValueError("generated application registry must be one regular file")
    appended = {"schema_version": 2, "revisions": [*rows, registry]}
    _atomic_write(generated, canonical(registry))
    _atomic_write(root / LEDGER_RELATIVE, canonical(appended))


def self_check() -> list[str]:
    findings: list[str] = []

    def check_shape(name: str, document: dict, shape: str = "") -> None:
        prefix = f"{shape}_" if shape else ""
        shape_label = f" {shape}" if shape else " top-level"
        required = document.get(f"{prefix}required_fields")
        allowed = document.get(f"{prefix}allowed_fields")
        types = document.get(f"{prefix}field_types")
        if (
            type(required) is not list
            or any(type(field) is not str for field in required)
            or len(required) != len(set(required))
            or type(allowed) is not list
            or any(type(field) is not str for field in allowed)
            or set(required) != set(allowed)
        ):
            findings.append(f"{name} schema must define one exact{shape_label} shape")
            return
        if (
            type(types) is not dict
            or set(types) != set(required)
            or any(
                type(value) is not str or value not in SCHEMA_PRIMITIVE_TYPES
                for value in types.values()
            )
        ):
            findings.append(
                f"{name} schema must type every exact{shape_label} field"
            )

    for name, document in (
        ("application map", MAP_SCHEMA),
        ("application contract", CONTRACT_SCHEMA),
    ):
        if type(document.get("schema_version")) is not int \
                or document.get("schema_version") != 2:
            findings.append(f"{name} schema must use version 2")
        states = document.get("state_classes")
        if (
            type(states) is not list
            or any(type(state) is not str for state in states)
            or len(states) != len(set(states))
        ):
            findings.append(f"{name} schema state_classes must be exact strings")
        check_shape(name, document)
    for name, document, shapes in (
        ("application map", MAP_SCHEMA, ("binding", "entry")),
        (
            "application contract",
            CONTRACT_SCHEMA,
            ("route", "transition", "simulation"),
        ),
        ):
        for shape in shapes:
            check_shape(name, document, shape)
    if MAP_STATE_CLASSES != REQUIRED_STATE_CLASSES:
        findings.append("application map and contract state taxonomies differ")
    for field in ("record_ref_pattern", "route_pattern"):
        if type(MAP_SCHEMA.get(field)) is not str \
                or type(CONTRACT_SCHEMA.get(field)) is not str:
            findings.append(f"application schemas must define string {field} values")
        if MAP_SCHEMA.get(field) != CONTRACT_SCHEMA.get(field):
            findings.append(
                f"application map and contract {field} values differ"
            )
    for field in ("experience_pattern", "simulation_id_pattern", "outcome_pattern"):
        if type(CONTRACT_SCHEMA.get(field)) is not str:
            findings.append(f"application contract schema {field} must be a string")
    if type(MAP_SCHEMA.get("minimum_entries")) is not int \
            or MAP_SCHEMA.get("minimum_entries") < 1:
        findings.append("application map schema minimum_entries must be a positive integer")
    rendered = (
        template_text()
        .replace("RUNTIME_SHA256", runtime_sha256())
        .replace("RUNTIME_CSP_SHA256", runtime_csp_sha256())
    )
    scanner = ApplicationScanner()
    scanner.feed(rendered)
    scanner.close()
    findings.extend(document_structure_findings(scanner))
    contract = parse_contract(scanner, findings)
    if (
        set(contract) != set(CONTRACT_SCHEMA["required_fields"])
        or contract.get("schema_version") != CONTRACT_SCHEMA["schema_version"]
    ):
        findings.append("application template contract does not match its schema")
    validate_exact_field_types(
        contract,
        CONTRACT_SCHEMA.get("field_types"),
        "application template contract",
        findings,
    )
    for index, route in enumerate(contract.get("routes", [])):
        if type(route) is dict:
            validate_exact_field_types(
                route,
                CONTRACT_SCHEMA.get("route_field_types"),
                f"application template contract.routes[{index}]",
                findings,
            )
    if scanner.csp_values != [expected_csp()]:
        findings.append("application template CSP does not bind the fixed runtime")
    if scanner.http_equivs != ["content-security-policy"]:
        findings.append("application template has unexpected http-equiv directives")
    if style_scaffold(rendered) != style_scaffold(template_text()):
        findings.append("application template fixed style scaffold is invalid")
    if len(AUTHOR_STYLE_PATTERN.findall(rendered)) != 1:
        findings.append("application template author-style block is invalid")
    template_tokens = set(re.findall(
        r"var\(\s*(--catalog-[a-z0-9-]+)", rendered,
    ))
    if not template_tokens <= REQUIRED_APPLICATION_ROOT_TOKENS:
        findings.append("application template consumes undeclared root tokens")
    if not REQUIRED_APPLICATION_DARK_TOKENS <= REQUIRED_APPLICATION_ROOT_TOKENS:
        findings.append("application dark token contract is outside the root contract")
    findings.extend(required_experience_findings(rendered, scanner))
    return sorted(set(findings))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--root", required=True)
    check.add_argument("--gate", action="store_true")
    check.add_argument(
        "--authoring",
        action="store_true",
        help="relax only cross-file completeness while retaining security checks",
    )
    check.add_argument("--json", action="store_true")
    runtime = sub.add_parser("runtime-checksum")
    runtime.add_argument("--json", action="store_true")
    selfcheck = sub.add_parser("self-check")
    selfcheck.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "runtime-checksum":
        payload = {"runtime_sha256": runtime_sha256()}
        print(json.dumps(payload) if args.json else payload["runtime_sha256"])
        return 0
    if args.command == "self-check":
        findings = self_check()
        if args.json:
            print(json.dumps({"ok": not findings, "findings": findings}, indent=2))
        else:
            for message in findings:
                print(f"ERROR {message}")
        return 1 if findings else 0
    try:
        registry, findings = compile_application(
            args.root,
            args.gate,
            authoring=args.authoring,
        )
    except (OSError, ValueError) as exc:
        registry, findings = {}, [str(exc)]
    if args.json:
        print(
            json.dumps(
                {
                    "ok": not findings,
                    "application": registry,
                    "findings": [{"message": message} for message in findings],
                },
                indent=2,
            )
        )
    else:
        for message in findings:
            print(f"ERROR {message}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
