#!/usr/bin/env python3
"""Lifecycle compiler for the project Process Policy.

The package registry ``skill-content/configure/data/process-switches.json``
declares every process switch, its values and its default. The governed
project document ``workspace/docs/delivery/process-policy.md`` records only
the project's explicit choices, one table row per switch. A missing document
or a switch without a row follows the package default, including a default
that a later release promotes.

A switch may declare owner-set parameters for the values that take them: the
registry names their type, how many a value needs and the package data file
that declares their ids. The package sets no parameter value. The optional
Parameters table holds one row per parameter the owner sets, and a parameter
exists only while its switch is at a value that takes it.
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
# A Delivery re-pins only through a new execution approval, so its pin must be
# current exactly while that approval is still possible. From the Delivery
# Review on, the pin records the policy the Delivery ran under.
PIN_ENFORCED_STATUSES = ("scope_approved", "execution_approved")
TABLE_HEADER = ("Switch", "Value")
TABLE_SEPARATOR_RE = re.compile(r"^\|\s*:?-{3,}:?\s*\|\s*:?-{3,}:?\s*\|$")
NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
ROWS_NOTE = ("Each row is an explicit project choice. A switch without a row follows its"
             " package default.")
PARAMETER_HEADER = ("Switch", "Parameter", "Value")
PARAMETER_SEPARATOR_RE = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|){3}$")
PARAMETERS_NOTE = ("Each row is an owner-set parameter of the switch value in force. The package"
                   " sets no parameter value, so a declared parameter without a row is unset.")
# Each parameter type: the pattern its cell must match and its typed value.
PARAMETER_TYPES = {"positive_integer": (re.compile(r"^[1-9][0-9]*$"), int)}


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
    """Return the declared switches, each with its value ids, default and parameters."""
    root = package or PACKAGE
    path = root / REGISTRY
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
        if "parameters" in spec:
            registry[name]["parameters"] = parameter_declaration(root, name, spec, values)
    return registry


def parameter_declaration(package: Path, name: str, spec: dict, values: list[str]) -> dict:
    """Resolve a switch's parameters: the values that take them, their ids and type."""
    declared = spec["parameters"]
    try:
        source = declared["declared_by"]
        relative = str(source["path"])
        if relative.startswith("/") or "\\" in relative or ".." in relative.split("/"):
            raise ValueError(f"declared_by path must stay inside the package: {relative}")
        items = json.loads((package / relative).read_text(encoding="utf-8"))[source["key"]]
        ids = {key: str(item["summary"]) for key, item in items.items()}
        result = {"summary": str(declared["summary"]), "values": list(declared["values"]),
                  "ids": ids, "type": declared["type"], "min_count": declared["min_count"]}
    except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError(f"process switch {name!r} parameters cannot be read: {exc}") from exc
    if (not ids or not all(NAME_RE.match(key) for key in ids)
            or result["type"] not in PARAMETER_TYPES
            or not result["values"] or not set(result["values"]) <= set(values) - {spec["default"]}
            or not isinstance(result["min_count"], int) or isinstance(result["min_count"], bool)
            or not 0 <= result["min_count"] <= len(ids)):
        raise ValueError(f"process switch {name!r} declares invalid parameters")
    return result


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


def parameter_rows(body: str) -> tuple[dict[tuple[str, str], str], list[str]]:
    """Read the optional Parameters table as (switch, parameter) to its value cell."""
    lines = section_lines(body, "Parameters")
    if lines is None:
        return {}, []
    table = [line.strip() for line in lines if line.strip().startswith("|")]
    if not table:
        return {}, ["Parameters must hold the parameter table"]
    if tuple(cells(table[0])) != PARAMETER_HEADER or len(table) < 2 \
            or not PARAMETER_SEPARATOR_RE.match(table[1]):
        return {}, ["Parameters table must start with the header | Switch | Parameter | Value |"]
    rows: dict[tuple[str, str], str] = {}
    errors: list[str] = []
    for line in table[2:]:
        row = cells(line)
        if len(row) != 3 or not all(row):
            errors.append(f"Parameters row must hold a switch, a parameter and a value: {line}")
            continue
        switch, parameter, value = (unquote(cell) for cell in row)
        if not NAME_RE.match(switch) or not NAME_RE.match(parameter) or not value:
            errors.append("Parameters row must name a switch id, a parameter id and a value:"
                          f" {line}")
        elif (switch, parameter) in rows:
            errors.append(f"parameter {parameter!r} of switch {switch!r} has more than one row")
        else:
            rows[(switch, parameter)] = value
    return rows, errors


def render_parameters(body: str, parameters: dict[tuple[str, str], str]) -> str:
    """Write the Parameters section after Switches, or drop it when no row remains."""
    lines = body.splitlines()
    start = next((index for index, line in enumerate(lines)
                  if re.fullmatch(r"## Parameters\s*", line)), None)
    if start is not None:
        end = next((later for later in range(start + 1, len(lines))
                    if lines[later].startswith("## ")), len(lines))
        lines = lines[:start] + lines[end:]
    if not parameters:
        return "\n".join(lines)
    anchor = next(index for index, line in enumerate(lines)
                  if re.fullmatch(r"## Switches\s*", line))
    end = next((later for later in range(anchor + 1, len(lines))
                if lines[later].startswith("## ")), len(lines))
    table = ["## Parameters", "", PARAMETERS_NOTE, "",
             "| Switch | Parameter | Value |", "| --- | --- | --- |",
             *(f"| `{switch}` | `{parameter}` | `{parameters[(switch, parameter)]}` |"
               for switch, parameter in sorted(parameters)), ""]
    return "\n".join([*lines[:end], *table, *lines[end:]])


def parameter_value(declared: dict, raw: str):
    """Return a parameter cell as its declared type, or None when it does not match it."""
    pattern, convert = PARAMETER_TYPES[declared["type"]]
    return convert(raw) if pattern.match(raw) else None


def parameter_findings(rows: dict[str, str], parameters: dict[tuple[str, str], str],
                       registry: dict[str, dict]) -> list[str]:
    """Check the parameter rows against the registry and the switch values in force."""
    errors = []
    for (switch, parameter), raw in sorted(parameters.items()):
        declared = registry.get(switch, {}).get("parameters")
        if switch not in registry:
            errors.append(f"parameter {parameter!r} names switch {switch!r}, which this package"
                          " does not declare")
        elif declared is None:
            errors.append(f"switch {switch!r} declares no parameters")
        elif parameter not in declared["ids"]:
            errors.append(f"switch {switch!r} has no parameter {parameter!r}; its parameters are"
                          f" {sorted(declared['ids'])}")
        elif parameter_value(declared, raw) is None:
            errors.append(f"parameter {parameter!r} of switch {switch!r} must be a"
                          f" {declared['type'].replace('_', ' ')}, not {raw!r}")
    for switch, spec in sorted(registry.items()):
        declared = spec.get("parameters")
        if declared is None:
            continue
        value = rows.get(switch, spec["default"])
        present = [parameter for (name, parameter) in parameters
                   if name == switch and parameter in declared["ids"]]
        if value not in declared["values"]:
            if present:
                errors.append(f"parameters of switch {switch!r} apply only at"
                              f" {' or '.join(declared['values'])}; its value is {value!r}")
        elif len(present) < declared["min_count"]:
            errors.append(f"switch {switch!r} at {value!r} needs at least {declared['min_count']}"
                          f" of its parameters {sorted(declared['ids'])}")
    return errors


def typed_parameters(switch: str, spec: dict, parameters: dict[tuple[str, str], str]) -> dict:
    """Return one switch's parameter values in force as their declared types."""
    return {parameter: parameter_value(spec["parameters"], raw)
            for (name, parameter), raw in sorted(parameters.items()) if name == switch}


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
    errors.extend(parameter_rows(body)[1])
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
    parameters, _parameter_errors = parameter_rows(body)
    errors = (integrity_findings(props, body) + registry_findings(rows, registry)
              + parameter_findings(rows, parameters, registry))
    state = {"path": RELATIVE, "status": props.get("status"), "revision": props.get("revision"),
             "source_hash": props.get("source_hash"), "rows": rows}
    if parameters:
        state["parameters"] = {}
        for (switch, parameter), raw in sorted(parameters.items()):
            state["parameters"].setdefault(switch, {})[parameter] = raw
    return state, errors


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
    state = read_state(docs, registry)[0] if snapshot else {}
    rows = state.get("rows", {})
    parameters = {(switch, parameter): raw
                  for switch, named in state.get("parameters", {}).items()
                  for parameter, raw in named.items()}
    values = {}
    for switch, spec in sorted(registry.items()):
        values[switch] = {"value": rows.get(switch, spec["default"]),
                          "source": "policy" if switch in rows else "default",
                          "default": spec["default"]}
        # Only a switch that declares parameters reports them, so every other
        # switch reads exactly as before parameters existed.
        if "parameters" in spec:
            values[switch]["parameters"] = typed_parameters(switch, spec, parameters)
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
    if not errors and args.parameter:
        return set_parameter(args, path, props, body, registry)
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
    value = rows.get(args.switch, registry.get(args.switch, {}).get("default"))
    # A value that takes no parameters keeps none: the owner's limits would
    # mean nothing there, and approval would refuse them.
    parameters = parameter_rows(body)[0]
    declared = registry.get(args.switch, {}).get("parameters")
    removed = {parameter: raw for (switch, parameter), raw in sorted(parameters.items())
               if switch == args.switch and (declared is None or value not in declared["values"])}
    if rows != before or removed:
        kept = {key: raw for key, raw in parameters.items()
                if key[0] != args.switch or key[1] not in removed}
        write(path, props, render_parameters(render_rows(body, rows), kept))
    result = {"ok": True, "switch": args.switch, "value": value,
              "source": "policy" if args.switch in rows else "default",
              "changed": rows != before or bool(removed)}
    if removed:
        result["removed_parameters"] = {
            parameter: shown_parameter(declared, parameter, raw)
            for parameter, raw in removed.items()}
    return emit(result)


def shown_parameter(declared: dict | None, parameter: str, raw: str):
    """Show a parameter cell as its declared type, or as written when it is invalid."""
    typed = (parameter_value(declared, raw)
             if declared is not None and parameter in declared["ids"] else None)
    return raw if typed is None else typed


def set_parameter(args, path: Path, props: dict, body: str, registry: dict[str, dict]) -> int:
    """Set or unset one owner-set parameter of a switch's value in force."""
    rows = table_rows(body)[0]
    parameters = parameter_rows(body)[0]
    key = (args.switch, args.parameter)
    spec = registry.get(args.switch)
    declared = spec.get("parameters") if spec else None
    errors = []
    if args.default:
        # Unsetting also repairs a row that a later package no longer declares.
        if key not in parameters and (declared is None or args.parameter not in declared["ids"]):
            errors = [f"parameter {args.parameter!r} of switch {args.switch!r} has no row and is"
                      " not declared by this package"]
    elif spec is None:
        errors = [f"switch {args.switch!r} is not declared by this package"]
    elif declared is None:
        errors = [f"switch {args.switch!r} declares no parameters"]
    elif args.parameter not in declared["ids"]:
        errors = [f"switch {args.switch!r} has no parameter {args.parameter!r}; its parameters are"
                  f" {sorted(declared['ids'])}"]
    elif parameter_value(declared, args.value) is None:
        errors = [f"parameter {args.parameter!r} of switch {args.switch!r} must be a"
                  f" {declared['type'].replace('_', ' ')}, not {args.value!r}"]
    elif rows.get(args.switch, spec["default"]) not in declared["values"]:
        errors = [f"parameters of switch {args.switch!r} apply only at"
                  f" {' or '.join(declared['values'])}; set its value first"]
    if errors:
        return emit({"ok": False, "errors": errors}, 1)
    before = dict(parameters)
    if args.default:
        parameters.pop(key, None)
    else:
        parameters[key] = args.value
    if parameters != before:
        write(path, props, render_parameters(body, parameters))
    return emit({"ok": True, "switch": args.switch, "parameter": args.parameter,
                 "value": parameter_value(declared, parameters[key]) if key in parameters else None,
                 "source": "policy" if key in parameters else "unset",
                 "changed": parameters != before})


def approve(args) -> int:
    docs = docs_root(args.docs)
    path = path_for(docs)
    registry = load_registry()
    if not path.exists():
        return emit({"ok": False, "errors": ["Process Policy does not exist; run init"]}, 1)
    props, body = parse(path)
    rows = table_rows(body)[0]
    errors = (integrity_findings(props, body) + registry_findings(rows, registry)
              + parameter_findings(rows, parameter_rows(body)[0], registry))
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
    if props.get("status") not in PIN_ENFORCED_STATUSES:
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
    set_parameters = (state or {}).get("parameters", {})
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
        declared = entry.get("parameters")
        if declared is not None:
            in_force = {parameter: shown_parameter(declared, parameter, raw)
                        for parameter, raw in sorted(set_parameters.get(name, {}).items())}
            listed[-1]["parameters"] = {
                "summary": declared["summary"], "values": declared["values"],
                "type": declared["type"], "min_count": declared["min_count"],
                "declared": [{"id": key, "summary": text}
                             for key, text in sorted(declared["ids"].items())],
                "in_force": in_force,
            }
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
                        help="remove the switch's row so it follows the package default, or with"
                             " --parameter unset that parameter")
    selected.add_argument("--parameter",
                          help="set one owner-set parameter of the switch's value in force")
    sub.choices["value"].add_argument("--switch", required=True)
    sub.choices["value"].add_argument("--delivery")
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError) as exc:
        return emit({"ok": False, "errors": [str(exc)]}, 2)


if __name__ == "__main__":
    raise SystemExit(main())
