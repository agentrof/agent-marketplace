"""Source-addressed project records. Indexes are navigation, never approval."""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

import ba_compile


def digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def safe_file(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or not path.parts or ".." in path.parts or "\\" in relative:
        raise ValueError("source must be a relative path inside its declared root")
    result = root / path
    if not result.resolve().is_relative_to(root.resolve()) or not result.is_file():
        raise ValueError(f"source is missing or escapes its declared root: {relative}")
    return result


def record_aliases(props: dict, relative: str) -> list[str]:
    aliases = list(props.get("aliases", [])) if isinstance(props.get("aliases"), list) else []
    aliases += [props.get(key) for key in ("id", "record_id", "component_id")]
    component = props.get("component_id")
    if component:
        aliases.append(f"solution-component:{component}")
    revision = props.get("revision")
    record = props.get("record_id")
    if record and revision:
        aliases.append(f"ARC:{record}@r{revision}" if str(record).startswith("CON-") else
                       f"ARC:{props.get('component_ref') or 'ROOT'}:{record}@r{revision}")
    parts = Path(relative).parts
    if parts[:2] == ("experience-design", "experiences") and len(parts) > 2:
        if props.get("id") and revision:
            aliases.append(f"{parts[2]}:{props['id']}@r{revision}")
        if props.get("type") == "experience":
            rev = props.get("approval_revision") or revision
            if rev:
                aliases.append(f"{parts[2]}@r{rev}")
    return sorted({item for item in aliases if isinstance(item, str) and item})


def catalog(vault) -> dict:
    """Build addresses from authored source, without trusting a stale registry's text."""
    documents, units, aliases = {}, {}, defaultdict(set)

    def alias(name: str, unit: str) -> None:
        if name:
            aliases[name].add(unit)

    for relative, note in sorted(vault.notes.items()):
        if note.generated or note.subtree == "maps" or relative == "home.md":
            continue
        path = safe_file(vault.root, relative)
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        if text.splitlines() != note.lines:
            raise ValueError(f"source changed while indexing: {relative}")
        lines = text.splitlines(keepends=True)
        props, start, error = ba_compile.parse_frontmatter(text)
        if error:
            raise ValueError(f"cannot address malformed source: {relative}: {error}")
        parsed = ba_compile.Doc(relative, path, props, start, str(props.get("type", "")), "",
                                lines=text.splitlines())
        ba_compile.parse_doc_body(parsed)
        document = {"path": relative, "source_hash": digest(raw),
                    "type": str(props.get("type", "")).replace("_", "-"),
                    "title": str(props.get("title", relative)),
                    "status": props.get("status"), "revision": props.get("revision"),
                    "record_state": props.get("record_state"),
                    "revision_state": props.get("revision_state"), "units": [],
                    "references": {key: value if isinstance(value, list) else [value]
                                   for key, value in props.items() if isinstance(value, (str, list))}}
        documents[relative] = document
        excluded = set()
        for begin, finish in ba_compile.GENERATED_BODY_BLOCKS:
            active = False
            for number, line in enumerate(lines, 1):
                if begin in line:
                    active = True
                if active:
                    excluded.add(number)
                if finish in line:
                    active = False
        nav = next((number for number, line in enumerate(lines, 1)
                    if "<!-- sec: nav -->" in line), len(lines) + 1)
        excluded.update(range(nav, len(lines) + 1))

        # Capture list ancestry once; every small read keeps its governing
        # condition without rescanning the preceding document per item.
        list_context, stack = {}, []
        for number in range(start, len(lines) + 1):
            line = lines[number - 1]
            if number in excluded:
                stack = []
                continue
            if number in parsed.fenced:
                continue
            match = re.match(r"^( *)(?:[-*+] |[0-9]+[.)] )", line)
            if match:
                depth = len(match.group(1))
                stack = [parent for parent in stack if parent[1] < depth]
                list_context[number] = [[n, end] for n, _depth, end in stack]
                stack.append([number, depth, number])
            elif line.strip():
                indent = len(line) - len(line.lstrip(" "))
                if not stack or indent <= stack[-1][1]:
                    stack = []
                else:
                    list_context[number] = [[n, end] for n, _depth, end in stack]
                    stack[-1][2] = number

        def add(kind: str, label: str, ranges: list[list[int]]) -> str | None:
            ancestry = []
            for number, level, heading in parsed.headings:
                if number >= ranges[-1][0]:
                    break
                ancestry = [item for item in ancestry if item[1] < level]
                ancestry.append((number, level, heading))
            if kind in {"row", "item", "block"}:
                ranges = [[number, number] for number, _level, _heading in ancestry] + ranges
            if kind in {"item", "block"}:
                ranges = ranges[:-1] + list_context.get(ranges[-1][0], []) + ranges[-1:]
            kept = [[a, b] for a, b in ranges if a <= b and
                    not any(number in excluded for number in range(a, b + 1))]
            if not kept:
                return None
            identity = f"{relative}::{kind}:{kept[-1][0]}"
            content = "\n".join("".join(lines[a - 1:b]) for a, b in kept)
            units[identity] = {"unit_id": identity, "path": relative, "kind": kind,
                               "label": label, "ranges": kept,
                               "source_hash": document["source_hash"],
                               "section_path": [heading for _number, _level, heading in ancestry],
                               "content_hash": digest(content.encode("utf-8")),
                               "bytes": len(content.encode("utf-8")),
                               "references": ["[[" + match.group("inner").replace("\\|", "|") + "]]"
                                              for match in ba_compile.WIKILINK_RE.finditer(
                                                  ba_compile.without_code(content))
                                              if not match.group("embed")]}
            document["units"].append(identity)
            return identity

        # A document address includes every authored line, excluding generated projections.
        ranges = []
        for number in range(1, len(lines) + 1):
            if number in excluded:
                continue
            if ranges and ranges[-1][1] == number - 1:
                ranges[-1][1] = number
            else:
                ranges.append([number, number])
        whole = add("document", document["title"], ranges)
        if whole:
            for name in [relative, relative.removesuffix(".md"), *record_aliases(props, relative)]:
                alias(name, whole)
        for pos, (number, level, title) in enumerate(parsed.headings):
            if level < 2 or number in excluded:
                continue
            end = next((n - 1 for n, depth, _ in parsed.headings[pos + 1:] if depth <= level),
                       len(lines))
            end = min(end, next((n - 1 for n in sorted(excluded) if n >= number), end))
            unit = add("section", title, [[number, end]])
            if unit:
                alias(f"{relative}::section:{title}", unit)
                token = ba_compile.SEC_RE.search(title)
                if token:
                    alias(f"{relative}::section:{token.group(1)}", unit)
                if re.fullmatch(r"ST-[0-9]{3,}-TS-[0-9]{3,}", title):
                    units[unit]["kind"] = "scenario"
                    alias(title, unit)
        space = Path(relative).parts[1] if relative.startswith("business-analysis/") else None
        for table in parsed.tables:
            for number, cells in table.rows:
                if len(cells) != len(table.columns):
                    continue
                row = dict(zip(table.columns, cells))
                label = row.get("id", f"row {number}")
                unit = add("row", label, [[table.header_line, table.header_line + 1], [number, number]])
                if unit and (ba_compile.BARE_ID_RE.fullmatch(label) or ba_compile.NAMESPACED_ID_RE.fullmatch(label)):
                    units[unit]["row_status"] = row.get("status")
                    alias(label, unit)
                    if space:
                        alias(f"{space}:{label}", unit)
        items = []
        for number, line in enumerate(lines, 1):
            if number < start or number in parsed.fenced or number in excluded:
                continue
            match = re.match(r"^( *)(?:[-*+] |[0-9]+[.)] )", line)
            if match:
                items.append((number, len(match.group(1))))
        for pos, (number, indent) in enumerate(items):
            end = next((n - 1 for n, depth in items[pos + 1:] if depth <= indent), len(lines))
            end = min(end, next((n - 1 for n, _level, _title in parsed.headings if n > number), end))
            end = min(end, next((n - 1 for n in sorted(excluded) if n >= number), end))
            add("item", lines[number - 1].strip()[:160], [[number, end]])
        for number, line in enumerate(lines, 1):
            match = re.search(r"(?:^|\s)\^([a-z0-9-]+)\s*$", line)
            if match and number not in excluded:
                unit = add("block", match.group(1), [[number, number]])
                if unit:
                    alias(f"{relative.removesuffix('.md')}#^{match.group(1)}", unit)
    result = {"documents": documents, "units": units,
              "aliases": {key: sorted(value) for key, value in sorted(aliases.items())}}
    add_receipts(vault.root, result)
    return result


def add_receipts(root: Path, data: dict) -> None:
    """Exact immutable record/package receipts, without upgrading a pinned revision."""
    paths = set(root.glob("system-architecture/_ledger/records/*/*.json"))
    paths.update(root.glob("experience-design/experiences/*/_ledger/records/*/*.json"))
    paths.update(root.glob("experience-design/**/application-revisions.json"))
    paths.update(root.glob("experience-design/**/package-revisions.json"))
    paths.update(root.glob("experience-design/**/_generated/registry.json"))
    paths.update(root.glob("experience-design/_generated/application-registry.json"))
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        raw = safe_file(root, relative).read_bytes()
        value = json.loads(raw)
        rows = [((), value)]
        if isinstance(value, dict) and isinstance(value.get("revisions"), list):
            rows = [(("revisions", i), row) for i, row in enumerate(value["revisions"])]
        for pointer, row in rows:
            if not isinstance(row, dict):
                continue
            ref = row.get("exact_ref")
            if not ref and row.get("application_revision"):
                ref = f"application@r{row['application_revision']}"
            if not ref and row.get("experience_id") and row.get("package_revision"):
                ref = f"{row['experience_id']}@r{row['package_revision']}"
            parts = path.relative_to(root).parts
            if not ref and row.get("id") and row.get("revision") and "experiences" in parts:
                ref = f"{parts[parts.index('experiences') + 1]}:{row['id']}@r{row['revision']}"
            immutable = "_ledger" in parts
            if not isinstance(ref, str) or (ref in data["aliases"] and not immutable):
                continue
            identity = f"{relative}::receipt:{'/'.join(map(str, pointer))}"
            content = json.dumps(row, sort_keys=True, ensure_ascii=False)
            data["units"][identity] = {"unit_id": identity, "path": relative,
                "kind": "receipt", "label": ref, "json_pointer": list(pointer),
                "source_hash": digest(raw), "content_hash": digest(content.encode()),
                "bytes": len(content.encode()), "historical": immutable}
            if isinstance(row.get("content"), str):
                props, _start, error = ba_compile.parse_frontmatter(row["content"])
                if not error:
                    data["units"][identity]["historical_properties"] = props
            document = data["documents"].setdefault(relative, {"path": relative, "type": "receipt",
                "title": ref, "source_hash": digest(raw), "units": []})
            document["units"].append(identity)
            previous = [key for key in data["aliases"].get(ref, [])
                        if data["units"][key].get("historical")]
            data["aliases"][ref] = previous + [identity]


def resolve(data: dict, reference: str) -> list[dict]:
    """Exact identities only; ambiguous bare IDs remain visibly ambiguous."""
    if reference.startswith("[[") and reference.endswith("]]" ):
        path, anchor, label = ba_compile.split_wikilink(reference[2:-2])
        if label in data["aliases"]:
            hits = [data["units"][unit] for unit in data["aliases"][label]
                    if data["units"][unit]["path"].removesuffix(".md") == path]
            return hits
        identifier = label.split(":", 1)[-1] if label else ""
        if ba_compile.BARE_ID_RE.fullmatch(identifier) or ba_compile.NAMESPACED_ID_RE.fullmatch(identifier):
            return []
        reference = path + ("#" + anchor if anchor else "")
    reference = reference.removeprefix("workspace/docs/")
    ids = [reference] if reference in data["units"] else data["aliases"].get(reference, [])
    return [data["units"][unit] for unit in ids]


def read_units(root: Path, data: dict, identities: list[str], max_bytes: int) -> dict:
    """Read addressed text against its exact source, never silently truncate a unit."""
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    selected = []
    for identity in dict.fromkeys(identities):
        if identity not in data["units"]:
            raise ValueError(f"unknown unit: {identity}")
        unit = data["units"][identity]
        source_root = root.parents[1] if unit.get("source_root") == "project" else root
        if unit.get("git_revision"):
            from context_history import git_source
            raw = git_source(root.parents[1], "workspace/docs/" + unit["path"], unit["git_revision"])
        else:
            raw = safe_file(source_root, unit["path"]).read_bytes()
        if digest(raw) != unit["source_hash"]:
            raise ValueError(f"stale source: {unit['path']}")
        if "json_pointer" in unit:
            value = json.loads(raw)
            for part in unit["json_pointer"]:
                value = value[part]
            content = json.dumps(value, sort_keys=True, ensure_ascii=False)
        else:
            lines = raw.decode("utf-8").splitlines(keepends=True)
            content = "\n".join("".join(lines[a - 1:b]) for a, b in unit["ranges"])
        if digest(content.encode("utf-8")) != unit["content_hash"]:
            raise ValueError(f"stale unit: {identity}")
        public = {key: value for key, value in unit.items() if key not in {"references", "historical_properties"}}
        selected.append({**public, "text": content})
    size = sum(unit["bytes"] for unit in selected)
    if size > max_bytes:
        return {"status": "needs_split", "required_bytes": size,
                "unit_ids": list(dict.fromkeys(identities))}
    return {"status": "ready", "bytes": size, "units": selected}
