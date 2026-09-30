#!/usr/bin/env python3
"""Lifecycle compiler for the project Process Policy.

The package registry ``skill-content/configure/data/process-switches.json``
declares every process switch, its values and its default. The governed
project document ``workspace/docs/delivery/process-policy.md`` records only
the project's explicit choices, one table row per switch. A missing document
or a switch without a row follows the package default, including a default
that a later release promotes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import atomic_file
from ba_compile import (
    frontmatter_item, frontmatter_scalar, parse_frontmatter, without_generated_relations,
)


PACKAGE = Path(__file__).resolve().parents[1]
REGISTRY = "skill-content/configure/data/process-switches.json"
RELATIVE = "delivery/process-policy.md"
TYPE = "process-policy"
TITLE = "Process Policy"
STATUSES = ("draft", "approved")
SECTIONS = ("Switches", "Navigation")
MUTABLE = {"status", "approved_at_utc", "source_hash"}
PIN_FIELDS = ("process_policy_path", "process_policy_revision", "process_policy_source_hash")
TABLE_HEADER = ("Switch", "Value")
TABLE_SEPARATOR_RE = re.compile(r"^\|\s*:?-{3,}:?\s*\|\s*:?-{3,}:?\s*\|$")
NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
ROWS_NOTE = ("Each row is an explicit project choice. A switch without a row follows its"
             " package default.")


def docs_root(value: str | Path | None) -> Path:
    root = Path(value or "workspace/docs").resolve()
    if root.name == "docs":
        return root
    if (root / "workspace" / "docs").is_dir():
        return root / "workspace" / "docs"
    if (root / "docs").is_dir():
        return root / "docs"
    return root


def path_for(docs: Path) -> Path:
    return docs / RELATIVE


def load_registry(package: Path | None = None) -> dict[str, dict]:
    """Return the declared switches, each with its value ids and default."""
    path = (package or PACKAGE) / REGISTRY
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"process switch registry cannot be read: {exc}") from exc
    switches = data.get("switches") if isinstance(data, dict) else None
    if not isinstance(switches, dict):
        raise ValueError("process switch registry declares no switches object")
    registry = {}
    for name, spec in switches.items():
        values = [value.get("id") for value in spec.get("values", [])
                  if isinstance(value, dict)] if isinstance(spec, dict) else []
        if not values or spec.get("default") not in values:
            raise ValueError(f"process switch {name!r} has no valid values and default")
        registry[name] = {"values": values, "default": spec["default"], "spec": spec}
    return registry


def parse_text(text: str, path: Path) -> tuple[dict, str]:
    props, body_line, error = parse_frontmatter(text)
    if error:
        raise ValueError(f"{path}: {error}")
    return props, "\n".join(text.splitlines()[body_line - 1:]).strip()


def parse(path: Path) -> tuple[dict, str]:
    return parse_text(path.read_text(encoding="utf-8"), path)


def render(props: dict, body: str) -> str:
    lines = ["---"]
    for key, value in props.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {frontmatter_item(item)}" for item in value)
        else:
            lines.append(f"{key}: {frontmatter_scalar(value)}")
    return "\n".join(lines + ["---", "", body.strip(), ""])


def policy_hash(props: dict, body: str) -> str:
    """Hash the authored policy the way the Definition of Done is hashed."""
    stable = {key: value for key, value in props.items() if key not in MUTABLE}
    if isinstance(stable.get("tags"), list):
        stable["tags"] = [tag for tag in stable["tags"]
                          if not (isinstance(tag, str) and tag.startswith("status/"))]
    payload = json.dumps({"frontmatter": stable,
                          "body": without_generated_relations(body).rstrip() + "\n"},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def section_lines(body: str, title: str) -> list[str] | None:
    lines = body.splitlines()
    for index, line in enumerate(lines):
        if re.fullmatch(rf"## {re.escape(title)}\s*", line):
            end = next((later for later in range(index + 1, len(lines))
                        if lines[later].startswith("## ")), len(lines))
            return lines[index + 1:end]
    return None


def cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def unquote(cell: str) -> str:
    return cell[1:-1] if len(cell) > 1 and cell.startswith("`") and cell.endswith("`") else cell


def table_rows(body: str) -> tuple[dict[str, str], list[str]]:
    """Read the Switches table as switch to value, with its structural errors."""
    lines = section_lines(body, "Switches")
    if lines is None:
        return {}, []
    table = [line.strip() for line in lines if line.strip().startswith("|")]
    if not table:
        return {}, ["Switches must hold the switch table"]
    if tuple(cells(table[0])) != TABLE_HEADER or len(table) < 2 \
            or not TABLE_SEPARATOR_RE.match(table[1]):
        return {}, ["Switches table must start with the header | Switch | Value |"]
    rows: dict[str, str] = {}
    errors: list[str] = []
    for line in table[2:]:
        row = cells(line)
        if len(row) != 2 or not all(row):
            errors.append(f"Switches row must hold a switch and a value: {line}")
            continue
        switch, value = unquote(row[0]), unquote(row[1])
        if not NAME_RE.match(switch) or not NAME_RE.match(value):
            errors.append(f"Switches row must name a switch id and a value id: {line}")
        elif switch in rows:
            errors.append(f"switch {switch!r} has more than one row")
        else:
            rows[switch] = value
    return rows, errors


def render_rows(body: str, rows: dict[str, str]) -> str:
    """Replace the Switches section with the rows, sorted by switch id."""
    table = ["| Switch | Value |", "| --- | --- |",
             *(f"| `{switch}` | `{rows[switch]}` |" for switch in sorted(rows))]
    lines = body.splitlines()
    start = next(index for index, line in enumerate(lines)
                 if re.fullmatch(r"## Switches\s*", line))
    end = next((later for later in range(start + 1, len(lines))
                if lines[later].startswith("## ")), len(lines))
    return "\n".join([*lines[:start + 1], "", ROWS_NOTE, "", *table, "", *lines[end:]])


def integrity_findings(props: dict, body: str) -> list[str]:
    """Check the document's own lifecycle integrity, independent of the registry."""
    errors: list[str] = []
    if props.get("type") != TYPE:
        errors.append(f"Process Policy type must be {TYPE}")
    if props.get("status") not in STATUSES:
        errors.append("Process Policy status must be draft or approved")
    revision = props.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        errors.append("Process Policy revision must be a positive integer")
    present = {re.sub(r"\s*<!--.*?-->", "", match.group(1)).strip()
               for match in re.finditer(r"(?m)^## (.+?)\s*$", body)}
    missing = sorted(set(SECTIONS) - present)
    if missing:
        errors.append(f"Process Policy missing sections: {', '.join(missing)}")
    errors.extend(table_rows(body)[1])
    if props.get("status") == "approved":
        if props.get("source_hash") != policy_hash(props, body):
            errors.append("approved Process Policy source_hash is stale")
        if not isinstance(props.get("approved_at_utc"), str):
            errors.append("approved Process Policy requires approved_at_utc")
    return errors


def registry_findings(rows: dict[str, str], registry: dict[str, dict]) -> list[str]:
    errors = []
    for switch, value in sorted(rows.items()):
        if switch not in registry:
            errors.append(f"switch {switch!r} is not declared by this package")
        elif value not in registry[switch]["values"]:
            errors.append(f"switch {switch!r} has no value {value!r};"
                          f" its values are {registry[switch]['values']}")
    return errors


def read_state(docs: Path, registry: dict[str, dict]) -> tuple[dict | None, list[str]]:
    """Return the document state, or None when the project has no Process Policy."""
    path = path_for(docs)
    if not path.exists():
        return None, []
    try:
        props, body = parse(path)
    except (OSError, ValueError) as exc:
        return {"path": RELATIVE}, [str(exc)]
    rows, _row_errors = table_rows(body)
    errors = integrity_findings(props, body) + registry_findings(rows, registry)
    return {"path": RELATIVE, "status": props.get("status"), "revision": props.get("revision"),
            "source_hash": props.get("source_hash"), "rows": rows}, errors


def approved_snapshot(docs: Path, package: Path | None = None) -> tuple[dict, list[str]]:
    """Return the pin of the current approved Process Policy; empty without a document.

    A Delivery pins this snapshot the way it pins the Definition of Done.
    """
    if not path_for(docs).exists():
        return {}, []
    try:
        registry = load_registry(package)
    except ValueError as exc:
        return {}, [str(exc)]
    state, errors = read_state(docs, registry)
    if errors:
        return {}, [f"Process Policy: {error}" for error in errors]
    if state["status"] != "approved":
        return {}, [f"Process Policy revision {state['revision']} is a draft; approve it, or restore"
                    " its approved file from Git, before a Delivery or task reads switch values"]
    return {"process_policy_path": RELATIVE,
            "process_policy_revision": state["revision"],
            "process_policy_source_hash": state["source_hash"]}, []


def effective_values(docs: Path | None, package: Path | None = None) -> tuple[dict[str, dict], dict]:
    """Return every declared switch's value in force and the pinned policy snapshot.

    Raises ValueError when a Process Policy exists but is not approved and valid.
    """
    registry = load_registry(package)
    snapshot, errors = approved_snapshot(docs, package) if docs is not None else ({}, [])
    if errors:
        raise ValueError("; ".join(errors))
    rows = read_state(docs, registry)[0]["rows"] if snapshot else {}
    values = {switch: {"value": rows.get(switch, spec["default"]),
                       "source": "policy" if switch in rows else "default",
                       "default": spec["default"]}
              for switch, spec in sorted(registry.items())}
    return values, snapshot


def pin_findings(props: dict, snapshot: dict) -> list[str]:
    """Compare a Delivery's process policy pin with the current approved policy.

    No policy and no pin agree. Any other difference is drift that a new
    execution approval re-pins.
    """
    if not any(key in props for key in PIN_FIELDS) and not snapshot:
        return []
    remedy = ("; revise the execution plan (begin-plan-revision, approve-execution) to pin the"
              " current Process Policy")
    if not snapshot:
        return ["Delivery pins a Process Policy that no longer exists" + remedy]
    if not any(key in props for key in PIN_FIELDS):
        return ["Delivery pins no Process Policy, but one is approved now" + remedy]
    return [f"Delivery {key} is stale against the approved Process Policy" + remedy
            for key in PIN_FIELDS if props.get(key) != snapshot[key]]


def emit(value: dict, code: int = 0) -> int:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return code


def render_delivery_map(docs: Path) -> None:
    import delivery_compile

    delivery_compile.render_map(docs)


def write(path: Path, props: dict, body: str) -> None:
    atomic_file.replace_text(path, render(props, body))


def init(args) -> int:
    docs = docs_root(args.docs)
    path = path_for(docs)
    if path.exists():
        return emit({"ok": False, "errors": [f"Process Policy already exists: {path}"]}, 1)
    title = (args.title or TITLE).strip()
    props = {"type": TYPE, "title": title, "status": "draft", "revision": 1,
             "tags": [f"doc/{TYPE}", "status/draft"]}
    body = "\n".join([
        f"# {title}", "",
        "## Switches", "", ROWS_NOTE, "",
        "| Switch | Value |", "| --- | --- |", "",
        "## Navigation <!-- sec: nav -->", "", "[[maps/delivery|Delivery map]]",
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    write(path, props, body)
    render_delivery_map(docs)
    return emit({"ok": True, "path": RELATIVE, "status": "draft", "revision": 1})


def begin_revision(args) -> int:
    docs = docs_root(args.docs)
    path = path_for(docs)
    if not path.exists():
        return emit({"ok": False, "errors": ["Process Policy does not exist; run init"]}, 1)
    props, body = parse(path)
    # Only the document's own integrity gates a revision: a revision is how a
    # policy that names a switch this package no longer declares is repaired.
    errors = integrity_findings(props, body)
    if not errors and props.get("status") != "approved":
        errors = ["Process Policy revision requires an approved current policy"]
    if errors:
        return emit({"ok": False, "errors": errors}, 1)
    props["revision"] = int(props["revision"]) + 1
    props["status"] = "draft"
    for key in ("approved_at_utc", "source_hash"):
        props.pop(key, None)
    props["tags"] = [tag for tag in props.get("tags", [])
                     if not str(tag).startswith("status/")] + ["status/draft"]
    write(path, props, body)
    return emit({"ok": True, "path": RELATIVE, "status": "draft", "revision": props["revision"]})


def set_value(args) -> int:
    docs = docs_root(args.docs)
    path = path_for(docs)
    registry = load_registry()
    if not path.exists():
        return emit({"ok": False, "errors": ["Process Policy does not exist; run init"]}, 1)
    props, body = parse(path)
    errors = integrity_findings(props, body)
    if not errors and props.get("status") != "draft":
        errors = ["approved Process Policy changes only in a revision; run begin-revision"]
    rows = table_rows(body)[0]
    if not errors and args.default:
        if args.switch not in rows and args.switch not in registry:
            errors = [f"switch {args.switch!r} has no row and is not declared by this package"]
    elif not errors:
        if args.switch not in registry:
            errors = [f"switch {args.switch!r} is not declared by this package"]
        elif args.value not in registry[args.switch]["values"]:
            errors = [f"switch {args.switch!r} has no value {args.value!r};"
                      f" its values are {registry[args.switch]['values']}"]
    if errors:
        return emit({"ok": False, "errors": errors}, 1)
    before = dict(rows)
    # The default is never written: a row records an explicit choice, so a
    # switch left at its default follows a later promoted default too.
    if args.default or args.value == registry[args.switch]["default"]:
        rows.pop(args.switch, None)
    else:
        rows[args.switch] = args.value
    if rows != before:
        write(path, props, render_rows(body, rows))
    value = rows.get(args.switch, registry.get(args.switch, {}).get("default"))
    return emit({"ok": True, "switch": args.switch, "value": value,
                 "source": "policy" if args.switch in rows else "default",
                 "changed": rows != before})


def approve(args) -> int:
    docs = docs_root(args.docs)
    path = path_for(docs)
    registry = load_registry()
    if not path.exists():
        return emit({"ok": False, "errors": ["Process Policy does not exist; run init"]}, 1)
    props, body = parse(path)
    errors = integrity_findings(props, body) + registry_findings(table_rows(body)[0], registry)
    if not errors and props.get("status") != "draft":
        errors = ["approve requires a draft Process Policy"]
    if errors:
        return emit({"ok": False, "errors": errors}, 1)
    props["status"] = "approved"
    props["approved_at_utc"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    props["source_hash"] = policy_hash(props, body)
    props["tags"] = [tag for tag in props.get("tags", [])
                     if not str(tag).startswith("status/")] + ["status/approved"]
    # Check what the file will hold before writing it, so a refusal keeps the draft.
    final_errors = integrity_findings(*parse_text(render(props, body), path))
    if final_errors:
        return emit({"ok": False, "errors": final_errors}, 1)
    write(path, props, body)
    render_delivery_map(docs)
    return emit({"ok": True, "path": RELATIVE, "revision": props["revision"],
                 "source_hash": props["source_hash"]})


def check(args) -> int:
    docs = docs_root(args.docs)
    registry = load_registry()
    state, errors = read_state(docs, registry)
    if state is None:
        return emit({"ok": True, "exists": False, "path": RELATIVE, "errors": []})
    return emit({"ok": not errors, "exists": True, **state, "errors": errors}, 1 if errors else 0)


def delivery_pin_findings(docs: Path, delivery: str, snapshot: dict) -> list[str]:
    import delivery_compile

    root = delivery_compile.find_delivery(docs, delivery)
    if root is None:
        return [f"Delivery not found: {delivery}"]
    props, _body = delivery_compile.split_note(root / "delivery.md")
    if props.get("status") in {"scope_proposed", "merged", "cancelled"}:
        return []
    return pin_findings(props, snapshot)


def value(args) -> int:
    docs = docs_root(args.docs)
    try:
        values, snapshot = effective_values(docs)
    except ValueError as exc:
        return emit({"ok": False, "errors": [str(exc)]}, 1)
    if args.switch not in values:
        return emit({"ok": False, "errors": [f"switch {args.switch!r} is not declared by this"
                                             " package"]}, 1)
    errors = delivery_pin_findings(docs, args.delivery, snapshot) if args.delivery else []
    if errors:
        return emit({"ok": False, "errors": errors}, 1)
    return emit({"ok": True, "switch": args.switch, **values[args.switch],
                 "policy": snapshot or None})


def switches(args) -> int:
    """List every declared switch with its choice-gate text and the value in force."""
    docs = docs_root(args.docs)
    registry = load_registry()
    state, errors = read_state(docs, registry)
    rows = (state or {}).get("rows", {})
    listed = []
    for name, entry in sorted(registry.items()):
        spec = entry["spec"]
        listed.append({
            "switch": name, "summary": spec.get("summary"), "default": entry["default"],
            "values": [{"id": item["id"], "tradeoffs": item["tradeoffs"]}
                       for item in spec["values"]],
            "value": rows.get(name, entry["default"]),
            "source": "policy" if name in rows else "default",
            "metric": spec.get("metric"), "promotion": spec.get("promotion"),
        })
    policy = None if state is None else {key: state.get(key) for key in
                                         ("path", "status", "revision", "source_hash")}
    return emit({"ok": not errors, "policy": policy, "switches": listed, "errors": errors},
                1 if errors else 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    commands = {"init": init, "begin-revision": begin_revision, "set": set_value,
                "approve": approve, "check": check, "value": value, "switches": switches}
    for name, handler in commands.items():
        command = sub.add_parser(name)
        command.add_argument("--docs")
        command.set_defaults(func=handler)
    sub.choices["init"].add_argument("--title")
    selected = sub.choices["set"]
    selected.add_argument("--switch", required=True)
    choice = selected.add_mutually_exclusive_group(required=True)
    choice.add_argument("--value")
    choice.add_argument("--default", action="store_true",
                        help="remove the switch's row so it follows the package default")
    sub.choices["value"].add_argument("--switch", required=True)
    sub.choices["value"].add_argument("--delivery")
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError) as exc:
        return emit({"ok": False, "errors": [str(exc)]}, 2)


if __name__ == "__main__":
    raise SystemExit(main())
