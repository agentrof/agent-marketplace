#!/usr/bin/env python3
"""Delivery-scoped compiler for the living System Architecture tree.

Architecture is never a product-design stage.  A record is legal only while
an active Delivery Item owns its delta, and the same Item must carry the
architecture delta hash into verification.  The implementation intentionally
uses plain Markdown plus a durable JSON ledger so it remains portable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ba_compile import parse_frontmatter


SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RECORD = re.compile(r"^(MOD|IFC|DAT|THR|RUN|REL|OBS|CON|STD|ADR)-[0-9]{3,}$|^HUB-(ROOT|[a-z0-9]+(?:-[a-z0-9]+)*)$")
EXACT = re.compile(r"^ARC:(ROOT|[a-z0-9]+(?:-[a-z0-9]+)*):(?:(MOD|IFC|DAT|THR|RUN|REL|OBS|STD|ADR)-[0-9]{3,}|HUB-(?:ROOT|[a-z0-9]+(?:-[a-z0-9]+)*))@r([1-9][0-9]*)$")
CONNECTION = re.compile(r"^ARC:(CON-[0-9]{3,})@r([1-9][0-9]*)$")
KIND = {
    "module": ("MOD", "modules", "module.md", "architecture-module"),
    "interface": ("IFC", "interfaces", "interface.md", "interface-contract"),
    "data": ("DAT", "data", "data-model.md", "data-model"),
    "security": ("THR", "security", "threat-model.md", "threat-model"),
    "runtime": ("RUN", "runtime", "runtime.md", "runtime-view"),
    "reliability": ("REL", "reliability", "reliability.md", "reliability-view"),
    "observability": ("OBS", "observability", "observability.md", "observability-view"),
    "connection": ("CON", "connections", "connection.md", "architecture-connection"),
    "standard": ("STD", "standards", "standard.md", "architecture-standard"),
    "decision": ("ADR", "decisions", "decision.md", "decision"),
}


def kind_for_id(record_id: str) -> tuple[str, str, str, str] | None:
    return next((entry for entry in KIND.values()
                 if record_id.startswith(entry[0] + "-")), None)


def atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def docs_root(value: str | Path) -> Path:
    path = Path(value).resolve()
    if path.name == "docs":
        return path
    if (path / "workspace" / "docs").is_dir():
        return path / "workspace" / "docs"
    return path / "docs" if (path / "docs").is_dir() else path


def frontmatter(props: dict, body: str) -> str:
    rows = ["---"]
    for key, value in props.items():
        if isinstance(value, list):
            rows.append(f"{key}:")
            rows.extend(f"  - {item}" for item in value)
        elif isinstance(value, bool):
            rows.append(f"{key}: {'true' if value else 'false'}")
        else:
            rows.append(f"{key}: {value}")
    return "\n".join([*rows, "---", "", body.rstrip(), ""])


def rewrite(path: Path, updates: dict, remove: set[str] = set()) -> None:
    text = path.read_text(encoding="utf-8")
    props, body_start, error = parse_frontmatter(text)
    if error:
        raise ValueError(f"{path}: {error}")
    for key in remove:
        props.pop(key, None)
    props.update(updates)
    body = "\n".join(text.splitlines()[body_start - 1:])
    atomic(path, frontmatter(props, body))


def item_context(docs: Path, item_ref: str) -> tuple[Path, dict]:
    candidates: list[tuple[Path, dict]] = []
    for path in (docs / "delivery" / "deliveries").glob("*/items/*/item.md"):
        try:
            props, _line, error = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if error:
            continue
        values = {str(props.get("story_id", "")), str(props.get("id", ""))}
        delivery = path.parents[2].name.upper()
        values.add(f"{delivery}:{props.get('story_id', '')}")
        if item_ref in values:
            candidates.append((path, props))
    if len(candidates) != 1:
        raise ValueError(f"item_ref must resolve to exactly one Delivery Item: {item_ref}")
    path, props = candidates[0]
    if props.get("status") not in {"claimed", "active"}:
        raise ValueError("System Architecture changes require a claimed or active Delivery Item")
    return path, props


def solution_components(docs: Path) -> dict[str, dict]:
    path = docs / "solution-design" / "_generated" / "component-catalog.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"current Solution component catalog is required: {exc}") from exc
    landscape = docs / "solution-design" / "landscape.md"
    props, _line, error = parse_frontmatter(landscape.read_text(encoding="utf-8"))
    try:
        import landscape_check
        current_hash = landscape_check.package_hash(landscape.parent)
    except (ImportError, OSError, ValueError):
        current_hash = ""
    if (error or props.get("package_status") != "approved"
            or (props.get("package_hash")
                and (not current_hash or props.get("package_hash") != current_hash))):
        raise ValueError("current approved Solution package is required")
    values = data.get("components", [])
    if not isinstance(values, list):
        raise ValueError("Solution component catalog is invalid")
    return {str(row.get("component_id")): row for row in values if isinstance(row, dict)}


def root_for(docs: Path) -> Path:
    return docs / "system-architecture"


def records(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.md") if path.name != "decision-log.md"]


def record_props(path: Path) -> dict:
    props, _line, error = parse_frontmatter(path.read_text(encoding="utf-8"))
    if error:
        raise ValueError(f"{path}: {error}")
    return props


def stable_id(props: dict) -> str:
    return str(props.get("record_id", ""))


def source_hash(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def current_ref(props: dict) -> str:
    record_id = stable_id(props)
    revision = int(props.get("revision", 0) or 0)
    if record_id.startswith("CON-"):
        return f"ARC:{record_id}@r{revision}"
    component = str(props.get("component_ref", "ROOT")) or "ROOT"
    return f"ARC:{component}:{record_id}@r{revision}"


def expected_hub(path: Path, root: Path) -> tuple[str, str, str] | None:
    if path == root / "architecture.md":
        return "HUB-ROOT", "ROOT", "system-architecture"
    if path.parent.name == "components" or (path.name == "component.md" and path.parent.parent.name == "components"):
        component = path.parent.name
        return f"HUB-{component}", component, "architecture-component"
    return None


def expected_lca_path(root: Path, scopes: list[str]) -> Path:
    """Resolve component/module scopes to the deepest shared architecture hub."""
    paths: list[Path] = []
    for scope in scopes:
        component, separator, module = scope.partition("#module/")
        path = root / "components" / component if component else root
        if separator:
            for part in module.split("/"):
                path = path / "modules" / part
        paths.append(path)
    if not paths:
        return root
    common = list(paths[0].parts)
    for path in paths[1:]:
        common = common[:next((index for index, pair in enumerate(zip(common, path.parts))
                              if pair[0] != pair[1]), min(len(common), len(path.parts)))]
    result = Path(*common) if common else root
    if result == root / "components":
        return root
    return result


def expected_decision_directory(root: Path, props: dict) -> Path:
    scope = expected_lca_path(root, [str(value) for value in props.get("affected_scopes", [])])
    return root / "standards" if props.get("type") == "architecture-standard" else scope / "decisions"


def validate_affected_scopes(docs: Path, root: Path, scopes: list[str]) -> list[str]:
    components = solution_components(docs)
    errors = []
    for scope in scopes:
        component, marker, module = scope.partition("#module/")
        if component not in components:
            errors.append(f"affected scope references unknown Solution component: {scope}")
            continue
        if marker:
            try:
                module_scope(root, component, module)
            except ValueError:
                errors.append(f"affected scope references an unknown Architecture module: {scope}")
    return errors


def registry(root: Path) -> tuple[dict, list[str]]:
    findings: list[str] = []
    rows = []
    seen: set[str] = set()
    for path in sorted(records(root)):
        try:
            props = record_props(path)
        except ValueError as exc:
            findings.append(str(exc))
            continue
        record_id = stable_id(props)
        if not RECORD.fullmatch(record_id):
            findings.append(f"{path.relative_to(root)} record_id must be a stable architecture id")
            continue
        if record_id in seen:
            findings.append(f"duplicate architecture record_id: {record_id}")
            continue
        seen.add(record_id)
        component = str(props.get("component_ref", ""))
        hub = expected_hub(path, root)
        expected = kind_for_id(record_id)
        if hub and (record_id != hub[0] or (component or "ROOT") != hub[1] or props.get("type") != hub[2]):
            findings.append(f"{path.relative_to(root)} hub identity must match its canonical path")
        elif expected and props.get("type") != expected[3]:
            findings.append(f"{path.relative_to(root)} type does not match {record_id}")
        revision = props.get("revision")
        if not isinstance(revision, int) or revision < 1:
            findings.append(f"{path.relative_to(root)} revision must be a positive integer")
        state = props.get("record_state", "active")
        if state not in {"active", "retired"}:
            findings.append(f"{path.relative_to(root)} record_state must be active or retired")
        if not hub and component:
            component_root = root / "components" / component
            if component_root not in path.parents:
                findings.append(f"{path.relative_to(root)} component_ref does not match its physical architecture path")
        if props.get("type") in {"decision", "architecture-standard"}:
            affected = props.get("affected_scopes", [])
            if not isinstance(affected, list) or not affected:
                findings.append(f"{path.relative_to(root)} needs affected_scopes for LCA placement")
            elif expected_decision_directory(root, props) != path.parent:
                findings.append(f"{path.relative_to(root)} is not at the lowest common architecture scope")
        if props.get("type") == "architecture-connection":
            endpoints = props.get("connects", [])
            if (not isinstance(endpoints, list) or len(endpoints) != 2
                    or len(set(endpoints)) != 2):
                findings.append(f"{path.relative_to(root)} connection must connect exactly two components")
        if props.get("revision_state") not in {"draft", "sealed"}:
            findings.append(f"{path.relative_to(root)} revision_state must be draft or sealed")
        if props.get("revision_state") == "sealed":
            snapshot = root / "_ledger" / "records" / record_id / f"r{revision}.json"
            try:
                saved = json.loads(snapshot.read_text(encoding="utf-8"))
                if saved.get("source_hash") != source_hash(path) or saved.get("content") != path.read_text(encoding="utf-8"):
                    findings.append(f"{path.relative_to(root)} sealed revision differs from its ledger snapshot")
            except (OSError, json.JSONDecodeError):
                findings.append(f"{path.relative_to(root)} sealed revision lacks an immutable ledger snapshot")
        rows.append({"id": record_id, "revision": revision, "record_state": state,
                     "component_ref": component, "type": props.get("type"),
                     "path": path.relative_to(root).as_posix()})
    rendered = {"schema_version": 1, "records": rows}
    digest = hashlib.sha256(json.dumps(rendered, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    rendered["registry_hash"] = f"sha256:{digest}"
    return rendered, findings


def check(root: Path, item_ref: str | None = None) -> tuple[dict, list[str]]:
    findings: list[str] = []
    architecture = root / "architecture.md"
    if not architecture.is_file():
        return {}, ["system-architecture/architecture.md is missing"]
    try:
        root_props = record_props(architecture)
    except ValueError as exc:
        return {}, [str(exc)]
    if root_props.get("type") != "system-architecture":
        findings.append("architecture.md type must be system-architecture")
    if item_ref:
        try:
            item_context(root.parent, item_ref)
        except ValueError as exc:
            findings.append(str(exc))
    docs = root.parent
    try:
        components = solution_components(docs)
    except ValueError as exc:
        findings.append(str(exc))
        components = {}
    for component_note in root.glob("components/*/component.md"):
        component = component_note.parent.name
        if component not in components:
            findings.append(f"architecture component {component} does not resolve to current Solution topology")
    rendered, registry_findings = registry(root)
    findings.extend(registry_findings)
    rendered_path = root / "_generated" / "registry.json"
    if rendered_path.is_file():
        try:
            current = json.loads(rendered_path.read_text(encoding="utf-8"))
            if current.get("registry_hash") != rendered["registry_hash"]:
                findings.append("_generated/registry.json is stale")
        except (OSError, json.JSONDecodeError):
            findings.append("_generated/registry.json is unreadable")
    elif records(root):
        findings.append("_generated/registry.json is missing")
    return rendered, sorted(set(findings))


def render(root: Path) -> tuple[dict, list[str]]:
    result, findings = check(root)
    if findings and findings != ["_generated/registry.json is missing"] and any("stale" not in item and "registry.json" not in item for item in findings):
        return result, findings
    atomic(root / "_generated" / "registry.json", json.dumps(result, indent=2, sort_keys=True) + "\n")
    atomic(root / "_generated" / "topology.json", json.dumps({"schema_version": 1, "components": sorted({row["component_ref"] for row in result.get("records", []) if row.get("component_ref")})}, indent=2, sort_keys=True) + "\n")
    map_path = root.parent / "maps" / "system-architecture.md"
    if map_path.is_file():
        marker = "<!-- architecture_compile.py: generated hubs -->"
        retained = map_path.read_text(encoding="utf-8").split(marker, 1)[0].rstrip()
        rows = ["", marker, "", "- [[system-architecture/architecture|System Architecture]]"]
        rows.extend(f"- [[system-architecture/components/{component}/component|{component}]]"
                    for component in sorted(path.parent.name for path in root.glob("components/*/component.md")))
        atomic(map_path, "\n".join([retained, *rows]).rstrip() + "\n")
    return result, []


def ledger(root: Path, record_id: str, revision: int, payload: dict) -> None:
    target = root / "_ledger" / "records" / record_id / f"r{revision}.json"
    if target.exists():
        raise ValueError(f"ledger already has {record_id}@r{revision}")
    atomic(target, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def seal_record(root: Path, path: Path, item_ref: str) -> dict:
    """Persist an immutable revision snapshot after authoring is complete."""
    props = record_props(path)
    record_id = stable_id(props)
    revision = int(props.get("revision", 0) or 0)
    props["revision_state"] = "sealed"
    rewrite(path, props)
    payload = {
        "record_id": record_id,
        "revision": revision,
        "exact_ref": current_ref(props),
        "path": path.relative_to(root).as_posix(),
        "item_ref": item_ref,
        "source_hash": source_hash(path),
        "content": path.read_text(encoding="utf-8"),
    }
    target = root / "_ledger" / "records" / record_id / f"r{revision}.json"
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing.get("source_hash") != payload["source_hash"]:
            raise ValueError(f"sealed revision drifted: {record_id}@r{revision}")
    else:
        ledger(root, record_id, revision, payload)
    return payload


def item_delta_hash(rendered: dict) -> str:
    """Hash only one Item's immutable architecture delta."""
    return "sha256:" + hashlib.sha256(
        json.dumps(rendered, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def item_delta(root: Path, item_ref: str) -> dict:
    rows = []
    for path in sorted(records(root)):
        props = record_props(path)
        if item_ref not in props.get("introduced_by", []):
            continue
        rows.append({
            "exact_ref": current_ref(props),
            "record_id": stable_id(props),
            "revision": props.get("revision"),
            "record_state": props.get("record_state", "active"),
            "component_ref": props.get("component_ref", ""),
            "type": props.get("type", ""),
            "connects": sorted(props.get("connects", [])) if isinstance(props.get("connects"), list) else [],
            "path": path.relative_to(root).as_posix(),
            "source_hash": source_hash(path),
        })
    return {"schema_version": 2, "item_ref": item_ref,
            "records": rows}


def current_item_delta(root: Path, item_ref: str) -> dict:
    target = root / "_ledger" / "item-deltas" / f"{item_ref.replace(':', '-').replace('/', '-')}.json"
    value = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("item_ref") != item_ref:
        raise ValueError("architecture Item delta is missing or invalid")
    actual = item_delta(root, item_ref)
    if value.get("records") != actual.get("records"):
        raise ValueError("architecture Item delta is stale")
    if value.get("architecture_delta_hash") != item_delta_hash(actual):
        raise ValueError("architecture Item delta hash is stale")
    return value


def init_root(docs: Path, item_ref: str) -> int:
    item_context(docs, item_ref)
    root = root_for(docs)
    if (root / "architecture.md").exists():
        print("architecture_compile: architecture root already exists")
        return 1
    body = "# System Architecture\n\nDelivery-owned architecture records are materialized only for active Delivery Items.\n\n## Navigation\n\n- [[maps/system-architecture|System Architecture map]]\n"
    atomic(root / "architecture.md", frontmatter({"type": "system-architecture", "title": "System Architecture", "status": "draft", "record_id": "HUB-ROOT", "revision": 1, "record_state": "active", "revision_state": "draft", "introduced_by": [item_ref], "tags": ["doc/system-architecture", "status/draft"]}, body))
    print(json.dumps({"root": str(root), "item_ref": item_ref, "created": "architecture.md"}, sort_keys=True))
    return 0


def init_component(docs: Path, component_ref: str, item_ref: str) -> int:
    item_context(docs, item_ref)
    components = solution_components(docs)
    if component_ref not in components:
        print(f"architecture_compile: unknown Solution component: {component_ref}")
        return 1
    root = root_for(docs)
    if not (root / "architecture.md").exists():
        print("architecture_compile: init-root must run first")
        return 1
    target = root / "components" / component_ref / "component.md"
    if target.exists():
        print("architecture_compile: component architecture already exists")
        return 1
    body = f"# {component_ref}\n\n## Responsibility\n\nDerived from the approved Solution component boundary.\n\n## Navigation\n\n- [[system-architecture/architecture|System Architecture]]\n"
    atomic(target, frontmatter({"type": "architecture-component", "title": component_ref,
                                "record_id": f"HUB-{component_ref}", "revision": 1,
                                "record_state": "active", "revision_state": "draft",
                                "component_ref": component_ref, "introduced_by": [item_ref],
                                "derives_from": [f"solution-component:{component_ref}"],
                                "tags": ["doc/architecture-component"]}, body))
    print(json.dumps({"component": component_ref, "item_ref": item_ref}, sort_keys=True))
    return 0


def module_scope(root: Path, component: str, module_path: str) -> Path:
    base = root / "components" / component
    if not module_path:
        return base
    current = base
    for part in module_path.split("/"):
        if not SLUG.fullmatch(part):
            raise ValueError("module_path must use lower-kebab module segments")
        current = current / "modules" / part
        if not (current / "module.md").is_file():
            raise ValueError(f"module_path does not resolve to a module: {module_path}")
    return current


def stub(docs: Path, kind: str, component: str, record_id: str, slug: str,
         item_ref: str, module_path: str = "", connects: list[str] | None = None,
         affected_scopes: list[str] | None = None) -> int:
    item_context(docs, item_ref)
    if kind not in KIND or not RECORD.fullmatch(record_id) or not SLUG.fullmatch(slug):
        print("architecture_compile: invalid kind, stable record id, or slug")
        return 2
    expected, directory, filename, doc_type = KIND[kind]
    if not record_id.startswith(expected + "-"):
        print(f"architecture_compile: {kind} needs an {expected}-### record_id")
        return 2
    root = root_for(docs)
    affected_scopes = list(affected_scopes or [])
    if kind in {"standard", "decision"}:
        scope_errors = validate_affected_scopes(docs, root, affected_scopes)
        if scope_errors:
            print("architecture_compile: " + "; ".join(scope_errors))
            return 2
    if kind == "connection":
        endpoints = list(connects or [])
        available = solution_components(docs)
        if len(endpoints) != 2 or len(set(endpoints)) != 2 or any(item not in available for item in endpoints):
            print("architecture_compile: connection requires exactly two distinct current Solution components")
            return 2
        target = root / directory / slug / filename
        component_ref = ""
    elif kind == "standard":
        if not affected_scopes or expected_lca_path(root, affected_scopes) != root:
            print("architecture_compile: a project standard needs affected scopes whose LCA is the architecture root")
            return 2
        target = root / directory / f"{slug}-standard.md"
        component_ref = ""
    elif kind == "decision":
        if not affected_scopes:
            print("architecture_compile: a decision needs one or more --affected-scope values")
            return 2
        scope = expected_lca_path(root, affected_scopes)
        if scope != root and not scope.is_relative_to(root / "components"):
            print("architecture_compile: decision affected scopes are invalid")
            return 2
        lca_component = scope.relative_to(root / "components").parts[0] if scope != root else ""
        if component and component != lca_component:
            print("architecture_compile: decision component must match the affected-scope LCA")
            return 2
        target = scope / directory / f"{slug}-decision.md"
        component_ref = lca_component
    else:
        components = solution_components(docs)
        if component not in components:
            print(f"architecture_compile: unknown Solution component: {component}")
            return 1
        if kind == "module" and components[component].get("sourcing") != "build":
            print("architecture_compile: only build components may own internal modules")
            return 1
        scope = module_scope(root, component, module_path)
        if kind == "decision":
            target = scope / directory / f"{slug}-decision.md"
        else:
            target = scope / directory / slug / filename
        component_ref = component
    if target.exists():
        print(f"architecture_compile: record already exists: {target}")
        return 1
    props = {"type": doc_type, "title": slug, "record_id": record_id, "revision": 1,
             "record_state": "active", "revision_state": "draft",
             "introduced_by": [item_ref], "tags": [f"doc/{doc_type}"]}
    if component_ref:
        props["component_ref"] = component_ref
    if kind == "connection":
        props["connects"] = sorted(connects or [])
    if kind in {"standard", "decision"}:
        props["affected_scopes"] = sorted(set(affected_scopes))
    body = f"# {slug}\n\n## Scope\n\nDelivery Item {item_ref} owns this architecture delta.\n\n## Navigation\n\n- [[system-architecture/architecture|System Architecture]]\n"
    atomic(target, frontmatter(props, body))
    print(json.dumps({"record": record_id, "path": str(target), "item_ref": item_ref}, sort_keys=True))
    return 0


def begin_revision(docs: Path, ref: str, item_ref: str) -> int:
    item_context(docs, item_ref)
    root = root_for(docs)
    _result, findings = check(root)
    if findings:
        print("architecture_compile: " + "; ".join(findings))
        return 1
    match = EXACT.fullmatch(ref) or CONNECTION.fullmatch(ref)
    if match is None:
        print("architecture_compile: ref must be ARC:<component|ROOT>:<id>@rN or ARC:CON-###@rN")
        return 2
    record_id = ref.split("@", 1)[0].rsplit(":", 1)[-1]
    revision = int(ref.rsplit("@r", 1)[1])
    for path in records(root):
        props = record_props(path)
        if stable_id(props) == record_id and props.get("revision") == revision:
            if props.get("revision_state") != "sealed":
                print("architecture_compile: only a sealed revision can begin a revision")
                return 1
            next_revision = revision + 1
            rewrite(path, {"revision": next_revision, "supersedes": ref,
                           "revision_state": "draft", "introduced_by": [item_ref]})
            print(json.dumps({"record": record_id, "revision": next_revision, "item_ref": item_ref}, sort_keys=True))
            return 0
    print("architecture_compile: exact ref does not resolve to a current record")
    return 1


def retire(docs: Path, ref: str, item_ref: str) -> int:
    code = begin_revision(docs, ref, item_ref)
    if code:
        return code
    root = root_for(docs)
    record_id = ref.split("@", 1)[0].rsplit(":", 1)[-1]
    for path in records(root):
        props = record_props(path)
        if stable_id(props) == record_id:
            rewrite(path, {"record_state": "retired"})
            return 0
    return 1


def stamp_item(docs: Path, item_ref: str) -> int:
    item_path, item_props = item_context(docs, item_ref)
    root = root_for(docs)
    rendered, findings = render(root)
    if findings:
        print("architecture_compile: " + "; ".join(findings))
        return 1
    delta = item_delta(root, item_ref)
    if not delta["records"]:
        print("architecture_compile: Item owns no architecture records")
        return 1
    claimed_components = set(item_props.get("architecture_components", []))
    claimed_kinds = set(item_props.get("architecture_record_kinds", []))
    for row in delta["records"]:
        if (claimed_components and row["component_ref"]
                and row["component_ref"] not in claimed_components):
            print("architecture_compile: Item delta exceeds claimed components")
            return 1
        if claimed_kinds and row["type"] not in claimed_kinds:
            print("architecture_compile: Item delta exceeds claimed record kinds")
            return 1
        if row["connects"] and not set(row["connects"]).issubset(claimed_components):
            print("architecture_compile: connection delta exceeds claimed components")
            return 1
    for path in records(root):
        props = record_props(path)
        if item_ref in props.get("introduced_by", []):
            if props.get("revision_state") != "draft":
                print("architecture_compile: Item includes an already sealed record")
                return 1
            seal_record(root, path, item_ref)
    _rendered, findings = render(root)
    if findings:
        print("architecture_compile: " + "; ".join(findings))
        return 1
    delta = item_delta(root, item_ref)
    digest = item_delta_hash(delta)
    rewrite(item_path, {"architecture_delta_hash": digest})
    target = root / "_ledger" / "item-deltas" / f"{item_ref.replace(':', '-').replace('/', '-')}.json"
    atomic(target, json.dumps({**delta, "architecture_delta_hash": digest,
                               "stamped_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"item_ref": item_ref, "architecture_delta_hash": digest}, sort_keys=True))
    return 0


def resolve(docs: Path, ref: str) -> int:
    root = root_for(docs)
    match = EXACT.fullmatch(ref) or CONNECTION.fullmatch(ref)
    if match:
        record_id = ref.split("@", 1)[0].rsplit(":", 1)[-1]
        revision = int(ref.rsplit("@r", 1)[1])
        ledger_path = root / "_ledger" / "records" / record_id / f"r{revision}.json"
        if ledger_path.is_file():
            print(ledger_path.read_text(encoding="utf-8"))
            return 0
    print("architecture_compile: unresolved exact architecture reference")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile Delivery-owned System Architecture.")
    parser.add_argument("command", choices=["init-root", "init-component", "stub", "begin-revision", "retire", "check", "render", "stamp-item", "resolve"])
    parser.add_argument("--docs", required=True)
    parser.add_argument("--item-ref", default="")
    parser.add_argument("--component-ref", default="")
    parser.add_argument("--component", default="")
    parser.add_argument("--kind", default="")
    parser.add_argument("--record-id", default="")
    parser.add_argument("--slug", default="")
    parser.add_argument("--module-path", default="")
    parser.add_argument("--connects", action="append", default=[])
    parser.add_argument("--affected-scope", action="append", default=[])
    parser.add_argument("--ref", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        docs = docs_root(args.docs)
        root = root_for(docs)
        if args.command == "init-root":
            return init_root(docs, args.item_ref)
        if args.command == "init-component":
            return init_component(docs, args.component_ref, args.item_ref)
        if args.command == "stub":
            return stub(docs, args.kind, args.component, args.record_id, args.slug,
                        args.item_ref, args.module_path, args.connects,
                        args.affected_scope)
        if args.command == "begin-revision":
            return begin_revision(docs, args.ref, args.item_ref)
        if args.command == "retire":
            return retire(docs, args.ref, args.item_ref)
        if args.command == "check":
            result, findings = check(root, args.item_ref or None)
            print(json.dumps({"ok": not findings, "registry": result, "errors": findings}, indent=2, sort_keys=True))
            return 0 if not findings else 1
        if args.command == "render":
            _result, findings = render(root)
            if findings:
                print("architecture_compile: " + "; ".join(findings))
                return 1
            return 0
        if args.command == "stamp-item":
            return stamp_item(docs, args.item_ref)
        return resolve(docs, args.ref)
    except (OSError, ValueError) as exc:
        print(f"architecture_compile: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
