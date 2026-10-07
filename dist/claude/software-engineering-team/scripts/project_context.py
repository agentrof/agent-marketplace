"""Prepare bounded, source-addressed reading plans from existing vault relations.

Plans guide navigation. They do not replace a review scope, approve a source,
or select skills. Metadata and source-text budgets are separate.
"""

from __future__ import annotations

import argparse
import base64
from collections import Counter, deque, ChainMap
import copy
import json
from pathlib import Path
import re
import sys
import zlib

import context_catalog as catalog

POLICY = Path(__file__).resolve().parents[1] / "templates/project-context-policy.json"
STATE_ROOT = Path(".agentrof/agent-marketplace/.runtime/context-requests")


def encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def state_limit(policy: dict) -> int:
    maximum = policy["reading_state"]["max_request_bytes"]
    if type(maximum) is not int or maximum <= 0:
        raise ValueError("reading_state.max_request_bytes must be a positive integer")
    return maximum


def navigation_data(project: Path, state: dict, *, policy: dict | None = None) -> dict:
    """Dereference disposable navigation state without trusting it as source authority."""
    policy = policy or json.loads(POLICY.read_text(encoding="utf-8"))
    maximum = state_limit(policy)
    if state.get("project_hash") != catalog.digest(str(project.resolve()).encode("utf-8")):
        raise ValueError("reading request state belongs to a different project root")
    if type(state.get("bytes")) is not int or not 0 < state["bytes"] <= maximum:
        raise ValueError("invalid reading request state size")
    if state.get("storage") == "inline":
        compressed = base64.b64decode(state["data"], validate=True)
        decoder = zlib.decompressobj()
        try:
            raw = decoder.decompress(compressed, maximum + 1)
        except zlib.error as exc:
            raise ValueError("invalid inline reading request state") from exc
        if not decoder.eof or decoder.unused_data or len(raw) > maximum:
            raise ValueError("invalid bounded inline reading request state")
    elif state.get("storage") == "runtime":
        expected = STATE_ROOT / (state["hash"].removeprefix("sha256:") + ".json")
        if state.get("path") != expected.as_posix() or not re.fullmatch(r"sha256:[0-9a-f]{64}", state["hash"]):
            raise ValueError("unsafe reading request state path")
        path = project / expected
        if any((project / Path(*expected.parts[:i])).is_symlink() for i in range(1, len(expected.parts) + 1)):
            raise ValueError("reading request state must not use a symbolic link")
        try:
            path = catalog.safe_file(project, expected.as_posix())
        except ValueError as exc:
            raise ValueError("reading request state is missing; rerun the original resolve or task invocation") from exc
        with path.open("rb") as stream:
            raw = stream.read(maximum + 1)
    else:
        raise ValueError("unknown reading request state storage")
    if len(raw) != state["bytes"] or catalog.digest(raw) != state["hash"]:
        raise ValueError("reading request state hash mismatch; rerun the original invocation")
    value = json.loads(raw)
    if not isinstance(value, dict) or encoded(value) != raw:
        raise ValueError("invalid canonical navigation state")
    return value


def request_data(project: Path, plan: dict, *, policy: dict | None = None) -> dict:
    request = copy.deepcopy(plan["request"])
    state = request.pop("state", None)
    if state is None:
        return request
    value = navigation_data(project, state, policy=policy)
    if (set(value) - {"refs", "manual_sources"} or not isinstance(value.get("refs"), list)
            or state["refs_count"] != len(value["refs"])):
        raise ValueError("invalid reading request state")
    if set(value) & set(request):
        raise ValueError("reading request state overlaps its envelope")
    request.update(value)
    return request


def navigation_state(project: Path, value: dict, policy: dict, *, persist: bool) -> dict:
    raw = encoded(value)
    maximum = state_limit(policy)
    if len(raw) > maximum:
        raise ValueError(f"reading request requires {len(raw)} state bytes; policy permits {maximum}")
    state = {"hash": catalog.digest(raw), "bytes": len(raw),
             "project_hash": catalog.digest(str(project.resolve()).encode("utf-8"))}
    if "refs" in value:
        state["refs_count"] = len(value["refs"])
    if persist:
        import atomic_file
        import vault_query
        try:
            directory = atomic_file.real_directory(project, STATE_ROOT)
            path = directory / (state["hash"].removeprefix("sha256:") + ".json")
            if path.is_symlink():
                raise ValueError("reading request state must not use a symbolic link")
            if not path.exists():
                atomic_file.replace_bytes(path, raw)
            elif path.read_bytes() != raw:
                raise ValueError("reading request state hash mismatch")
            state.update(storage="runtime", path=path.relative_to(project).as_posix())
        except OSError as exc:
            if exc.errno not in vault_query.READ_ONLY_ERRORS:
                raise
            persist = False
    if not persist:
        state.update(storage="inline", data=base64.b64encode(zlib.compress(raw)).decode("ascii"))
    return state


def compact_request(project: Path, request: dict, policy: dict, *, persist: bool) -> dict:
    """Retain the complete immutable seed; a cursor keeps page history bounded."""
    result = dict(request)
    value = {key: result.pop(key) for key in ("refs", "manual_sources") if key in result}
    result["state"] = navigation_state(project, value, policy, persist=persist)
    if result["state"]["storage"] == "inline":
        result["persist_state"] = False
    return result


def manual_units(project: Path, paths: list[str]) -> list[dict]:
    result = []
    for relative in sorted(set(paths)):
        path = catalog.safe_file(project, relative)
        if any((project / Path(*Path(relative).parts[:i])).is_symlink()
               for i in range(1, len(Path(relative).parts) + 1)):
            raise ValueError("selected manual evidence must not use a symbolic link")
        raw = path.read_bytes()
        result.append({"unit_id": relative + "::manual", "path": relative, "kind": "manual",
            "source_root": "project", "source_hash": catalog.digest(raw), "source_bytes": len(raw),
            "bytes": 0, "authority": "selected_evidence",
            "next_action": "Read the selected evidence manually; its source hash remains an owning-task obligation."})
    return result


def external_sources(project: Path, data: dict, refs: list[str], policy: dict) -> None:
    """Index explicitly named local sources; never walk an unrelated source tree."""
    for ref in refs:
        spec = next((value for value in policy["external_sources"].values()
                     if ref.startswith(value["root"] + "/")), None)
        if spec is None:
            continue
        if Path(ref).suffix not in spec["extensions"] or any(part.startswith(".") for part in Path(ref).parts):
            raise ValueError("external source type is not declared by the context policy")
        path = catalog.safe_file(project, ref)
        allowed = project / spec["root"]
        if allowed.resolve() != allowed.absolute() or not path.resolve().is_relative_to(allowed.resolve()):
            raise ValueError("external source escapes its declared source root")
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        lines = text.splitlines(keepends=True)
        identity = ref + "::document:1"
        data["units"][identity] = {"unit_id": identity, "path": ref, "kind": "document",
            "label": Path(ref).name, "ranges": [[1, len(lines)]], "source_root": "project",
            "source_hash": catalog.digest(raw), "content_hash": catalog.digest(raw),
            "bytes": len(raw), "authority": spec["authority"]}
        data["aliases"][ref] = [identity]
        data["documents"][ref] = {"path": ref, "type": spec["authority"],
            "title": Path(ref).name, "source_hash": catalog.digest(raw), "units": [identity]}


def snapshot(data: dict, policy: dict, *, store=None) -> str:
    """Membership and relations bind the plan as well as each selected source."""
    # Every graph edge and address is derived from these exact source bytes.
    # Re-serializing all parsed units here would make each small lookup pay for
    # the entire corpus again.
    if store is not None:
        inputs = dict(store.meta("snapshot_inputs"))
        sources = dict(inputs["sources"])
        overlay = data["catalog"]["documents"]
        if isinstance(overlay, ChainMap):
            sources.update({path: record["source_hash"] for path, record in overlay.maps[0].items()})
        inputs.update(sources=sources, policy={key: value for key, value in policy.items() if key != "reading_state"})
        return catalog.digest(encoded(inputs))
    files = {path: entry["sha"] for path, entry in data.get("files", {}).items()}
    sources = {path: record["source_hash"] for path, record in data["catalog"]["documents"].items()}
    return catalog.digest(encoded({"builder": data.get("builder"), "files": files,
        "sources": sources, "policy": {key: value for key, value in policy.items() if key != "reading_state"}}))


def catalog_view(source):
    """Invocation-local overlays leave the shared source index immutable."""
    return {key: ChainMap({}, value) for key, value in source.items()}


def paged_context(project: Path, source: dict, all_required: list[dict], optional: dict,
                  reasons: dict, gaps: list[dict], unresolved: list[dict], request: dict,
                  version: str, *, offset: int, cursor: dict | None, state: dict | None,
                  reason: str | None, policy: dict, persist_state: bool,
                  state_reuse: dict | None = None) -> dict:
    """Bound pages independently of the immutable scope and logical-unit size."""
    limits = request["budget"]
    scope = catalog.digest(encoded([unit["unit_id"] for unit in all_required]))
    if cursor is not None:
        if set(cursor) != {"unit", "byte", "scope_hash"} or cursor["scope_hash"] != scope:
            raise ValueError("reading cursor scope changed; restart the complete required reading set")
        position, begin = cursor["unit"], cursor["byte"]
        if type(position) is not int or type(begin) is not int or not 0 <= position <= len(all_required) \
                or begin < 0 or (position == len(all_required) and begin):
            raise ValueError("invalid reading cursor")
    else:
        seen = set(request.get("seen_units", []))
        position = 0
        while position < len(all_required) and all_required[position]["unit_id"] in seen:
            position += 1
        # A prior widening can leave a non-prefix read set. Restarting is safe;
        # serializing every old identity would make continuation grow forever.
        if any(unit["unit_id"] in seen for unit in all_required[position:]):
            position = 0
        position += offset
        begin = 0
    if position > len(all_required):
        raise ValueError("continuation is outside the required reading set")
    request = {key: value for key, value in request.items() if key != "seen_units"}
    request["cursor"] = {"unit": position, "byte": begin, "scope_hash": scope}
    if state is not None:
        request = {key: value for key, value in request.items() if key not in {"refs", "manual_sources"}}
        request["state"] = state
    initial = position

    def assemble(rows, endpoint, byte_offset, source_bytes, oversized=None):
        remaining = len(all_required) - endpoint
        result = {"schema_version": 1, "snapshot_id": version, "request": request,
            "offset": 0, "status": "needs_resolution" if unresolved else "needs_split" if remaining else "ready",
            "approval_authority": False, "must_read": [row for row in rows if row["kind"] != "manual"],
            "coverage": {"required_units": len(all_required), "returned_units": endpoint - initial,
                "previously_returned_units": initial, "remaining_required_units": remaining,
                "optional_units": len(optional), "relevant_gaps": len(gaps), "source_bytes": source_bytes},
            "optional_groups": dict(Counter(source["documents"][u["path"]]["type"] for u in optional.values())),
            "gaps": gaps[:3], "unresolved_required_refs": unresolved[:3],
            "unresolved_required_count": len(unresolved), "reason": reason}
        manual = [row for row in rows if row["kind"] == "manual"]
        if manual:
            result["manual_reads"] = manual
        if remaining:
            result["continuation"] = {"offset": endpoint,
                "cursor": {"unit": endpoint, "byte": byte_offset, "scope_hash": scope}, "snapshot_id": version}
        if oversized:
            result["oversized"] = oversized
        result["plan_hash"] = catalog.digest(encoded(result))
        return result

    def fit(result):
        return len(encoded(result)) <= limits["max_metadata_bytes"]

    baseline = assemble([], position, begin, 0)
    if not fit(baseline) and state is None:
        request = compact_request(project, request, policy, persist=persist_state)
        baseline = assemble([], position, begin, 0)
    if not fit(baseline):
        raise ValueError(f"metadata budget requires at least {len(encoded(baseline))} bytes for the complete request envelope")
    rows, paths, source_bytes = [], set(), 0
    result = baseline
    while position < len(all_required):
        unit = all_required[position]
        if len(paths | {unit["path"]}) > limits["max_files"]:
            break
        available = limits["max_source_bytes"] - source_bytes
        if not available and unit["bytes"]:
            break
        endpoint, byte_offset = position + 1, 0
        addressed = unit
        if begin or unit["bytes"] > available:
            content = catalog.unit_content(project / "workspace/docs", unit)
            addressed = catalog.fragment(unit, content, begin, available)
            if addressed is None:
                minimum = len(content[begin:].decode("utf-8")[0].encode("utf-8"))
                if not rows:
                    result = assemble([], position, begin, 0, {"unit_id": unit["unit_id"],
                        "bytes": unit["bytes"], "minimum_source_bytes": minimum,
                        "next_action": f"Increase max_source_bytes to at least {minimum} to read the next UTF-8 character."})
                    if not fit(result):
                        raise ValueError(f"metadata budget requires at least {len(encoded(result))} bytes for the blocked address")
                break
            byte_offset = addressed["byte_range"][1]
            if byte_offset < unit["bytes"]:
                endpoint = position
            else:
                byte_offset = 0
        item = {key: value for key, value in addressed.items() if key not in {"references", "historical_properties"}}
        if unit["kind"] != "manual":
            item["reasons"] = reasons[unit["unit_id"]]
            source_state = unit.get("historical_properties", source["documents"][unit["path"]])
            item["source_state"] = {key: source_state.get(key)
                for key in ("status", "revision", "record_state", "revision_state")}
        proposed = assemble(rows + [item], endpoint, byte_offset, source_bytes + addressed["bytes"])
        if not fit(proposed) and not rows and "state" not in request:
            request = compact_request(project, request, policy, persist=persist_state)
            proposed = assemble([item], endpoint, byte_offset, source_bytes + addressed["bytes"])
        if not fit(proposed) and not rows and item.get("reasons"):
            provenance = item["reasons"]
            descriptor = (state_reuse or {}).get(catalog.digest(encoded({"reasons": provenance})))
            if descriptor is None:
                descriptor = navigation_state(project, {"reasons": provenance}, policy,
                    persist=persist_state and request.get("persist_state", True))
            bounded = {"count": len(provenance), "state": descriptor}
            if len(encoded(bounded)) < len(encoded(provenance)):
                item["reasons"] = bounded
                if descriptor["storage"] == "inline":
                    request["persist_state"] = False
                proposed = assemble([item], endpoint, byte_offset, source_bytes + addressed["bytes"])
        if not fit(proposed):
            if not rows:
                raise ValueError(f"metadata budget requires at least {len(encoded(proposed))} bytes for one complete reading address")
            break
        rows.append(item)
        result = proposed
        paths.add(unit["path"])
        source_bytes += addressed["bytes"]
        position, begin = endpoint, byte_offset
    return result


def resolve_context(project: Path, index: dict, *, entry: str, role: str, refs: list[str] | None = None,
                    purpose: str = "discover", budget: dict | None = None,
                    offset: int = 0, expected_snapshot: str | None = None,
                    reason: str | None = None, policy: dict | None = None,
                    seen_units: list[str] | None = None,
                    snapshot_scope: str = "vault", cursor: dict | None = None,
                    state: dict | None = None, persist_state: bool = True,
                    manual_sources: list[str] | None = None,
                    state_reuse: dict | None = None, allow_state_writes: bool = True) -> dict:
    policy = policy or json.loads(POLICY.read_text(encoding="utf-8"))
    policy = copy.deepcopy(policy)
    if state is not None:
        restored = request_data(project, {"request": {"state": state}}, policy=policy)
        if refs is not None or manual_sources is not None:
            raise ValueError("reading state and explicit sources cannot overlap")
        refs, manual_sources = restored["refs"], restored.get("manual_sources")
    for group in ("entry_profiles", "role_profiles"):
        policy[group] = {key.replace("_", "-"): value for key, value in policy[group].items()}
    if entry not in policy["entry_profiles"] or purpose not in policy["purposes"]:
        raise ValueError("entry or purpose is not declared by the context policy")
    tasks = json.loads((POLICY.parent / "task-input-policy.json").read_text(encoding="utf-8"))
    route = tasks["entries"].get(entry.replace("-", "_"), {})
    if role is not None and role not in route.get("roles", []):
        raise ValueError("role does not belong to this entry")
    if (not refs and not manual_sources) or offset < 0:
        raise ValueError("role, references and a non-negative offset are required")
    refs = refs or []
    if any(not isinstance(ref, str) or not ref for ref in refs):
        raise ValueError("references must be nonempty strings")
    if snapshot_scope not in {"vault", "selection"}:
        raise ValueError("unknown snapshot scope")
    if (offset or (cursor and (cursor.get("unit") or cursor.get("byte")))) \
            and (not expected_snapshot or not reason or not reason.strip()):
        raise ValueError("continuation requires its snapshot and a reason")
    limits = dict(policy["budgets"])
    if budget:
        if set(budget) - set(limits):
            raise ValueError("unknown context budget")
        limits.update(budget)
    if any(type(value) is not int or value <= 0 for value in limits.values()):
        raise ValueError("context budgets must be positive integers")
    store = getattr(index, "store", None)
    data = dict(index, catalog=catalog_view(index["catalog"]))
    external_sources(project, data["catalog"], refs, policy)
    version = snapshot(data, policy, store=store) if snapshot_scope == "vault" else None
    if snapshot_scope == "vault" and not manual_sources and expected_snapshot is not None and version != expected_snapshot:
        raise ValueError("stale context snapshot; resolve the current sources again")
    profile = policy["entry_profiles"][entry]
    required_keys = set(policy["required_relations"]) | set(profile["required_relations"])
    required_keys.update(policy["purpose_profiles"][purpose]["required_relations"])
    optional_keys = set(policy["optional_relations"])
    preferred = profile["preferred_types"] + policy["role_profiles"].get(role, [])
    source = data["catalog"]
    manual = manual_units(project, manual_sources or [])
    required, optional, reasons, unresolved, bound_cache = {}, {}, {}, [], {}
    pending = deque()

    def include(unit, why):
        identity = unit["unit_id"]
        reasons.setdefault(identity, [])
        if why not in reasons[identity]:
            reasons[identity].append(why)
        if identity not in required:
            required[identity] = unit
            pending.append(unit)

    for ref in refs:
        hits = catalog.resolve(source, ref)
        if len(hits) != 1:
            raise ValueError(f"reference must resolve exactly once: {ref} ({len(hits)} matches)")
        include(hits[0], "requested reference: " + ref)
    outgoing = {}
    if store is None:
        for origin, target, key, _tiers in data.get("edges", []):
            outgoing.setdefault(origin, []).append((target, key))
    visited = set()
    while pending:
        current = pending.popleft()
        path = current["path"]
        if current["kind"] in {"row", "scenario", "item", "block"}:
            for reference in current.get("references", []):
                targets = catalog.resolve(source, reference)
                if len(targets) == 1:
                    include(targets[0], f"citation from {current['unit_id']}")
                else:
                    unresolved.append({"source": current["unit_id"], "reference": reference})
        versioned_path = (path, current["source_hash"])
        if versioned_path in visited:
            continue
        visited.add(versioned_path)
        covered = set()
        references = source["documents"][path].get("references", {})
        if current.get("historical_properties"):
            references = {key: value if isinstance(value, list) else [value]
                          for key, value in current["historical_properties"].items()}
        for key, values in references.items():
            if key not in required_keys:
                continue
            for reference in values:
                if not isinstance(reference, str):
                    continue
                targets = catalog.resolve(source, reference)
                if len(targets) == 1:
                    stem = re.sub(r"_(ref|path)$", "", key)
                    bound = references.get(stem + "_source_hash", references.get(stem + "_hash", []))
                    if bound and isinstance(bound[0], str):
                        from context_history import bound_source
                        binding = (targets[0]["path"], bound[0])
                        if binding not in bound_cache:
                            bound_cache[binding] = bound_source(project, *binding)
                        pinned = bound_cache[binding]
                        if pinned is None:
                            unresolved.append({"source": path, "reference": reference,
                                               "reason": "bound version is unavailable"})
                            covered.add((targets[0]["path"], key))
                            continue
                        if not pinned["current"]:
                            source["units"][pinned["unit_id"]] = pinned
                            targets = [pinned]
                    covered.add((targets[0]["path"], key))
                    include(targets[0], f"{key} from {path}")
                else:
                    unresolved.append({"source": path, "reference": reference, "key": key})
        links = (sorted((target, key) for _origin, target, key, _tiers in store.edges_for(path))
                 if store is not None else outgoing.get(path, []))
        for target, key in ([] if current.get("historical") else links):
            if (target, key) in covered:
                continue
            units = catalog.resolve(source, target)
            if not units:
                continue
            if key in required_keys:
                for unit in units:
                    include(unit, f"{key} from {path}")
            elif key in optional_keys:
                for unit in units:
                    optional.setdefault(unit["unit_id"], unit)
    # A complete document subsumes its rows/sections, while distinct requested
    # records of the same document remain separately addressed.
    whole_paths = {(u["path"], u["source_hash"]) for u in required.values() if u["kind"] == "document"}
    required = {key: unit for key, unit in required.items()
                if unit["kind"] == "document" or (unit["path"], unit["source_hash"]) not in whole_paths}
    optional = {key: unit for key, unit in optional.items()
                if key not in required and (unit["path"], unit["source_hash"]) not in whole_paths}
    def order(unit):
        kind = source["documents"][unit["path"]]["type"]
        return (0 if any(r.startswith("requested reference:") for r in reasons.get(unit["unit_id"], [])) else 1,
                preferred.index(kind) if kind in preferred else len(preferred), unit["path"], unit["unit_id"])
    all_required = sorted(required.values(), key=order) + manual
    if manual and snapshot_scope == "vault":
        version = catalog.digest(encoded({"vault_snapshot": version, "manual": manual}))
        if expected_snapshot is not None and version != expected_snapshot:
            raise ValueError("stale context snapshot; resolve the current sources again")
    if snapshot_scope == "selection":
        # Re-resolving detects new ambiguity and changed edges. Unrelated edits
        # must not stale concurrently running tasks with disjoint input sets.
        version = catalog.digest(encoded({"builder": data.get("builder"),
            "policy": {key: value for key, value in policy.items() if key != "reading_state"},
            "required": all_required, "optional": sorted(optional), "unresolved": unresolved}))
        if expected_snapshot is not None and version != expected_snapshot:
            raise ValueError("stale context snapshot; resolve the current sources again")
    seen = set(seen_units or [])
    if any(unit not in source["units"] for unit in seen):
        raise ValueError("previously returned unit no longer exists")
    ordered = [unit for unit in all_required if unit["unit_id"] not in seen]
    if offset >= len(ordered) and offset:
        raise ValueError("continuation is outside the required reading set")
    relevant_paths = {unit["path"] for unit in ordered}
    available_gaps = store.gaps_for(relevant_paths) if store is not None else data.get("gaps", [])
    gaps = [gap for gap in available_gaps
            if gap.get("path") in relevant_paths or gap.get("source") in relevant_paths
            or gap.get("target") in relevant_paths]
    picked, paths, source_bytes = [], set(), 0
    for unit in ordered[offset:]:
        if unit["kind"] == "manual":
            break
        if len(paths | {unit["path"]}) > limits["max_files"] or source_bytes + unit["bytes"] > limits["max_source_bytes"]:
            break
        item = {key: value for key, value in unit.items() if key not in {"references", "historical_properties"}}
        item["reasons"] = reasons[unit["unit_id"]]
        source_state = unit.get("historical_properties", source["documents"][unit["path"]])
        item["source_state"] = {key: source_state.get(key)
                                for key in ("status", "revision", "record_state", "revision_state")}
        if len(encoded(picked + [item])) > limits["max_metadata_bytes"] // 2:
            break
        picked.append(item)
        paths.add(unit["path"])
        source_bytes += unit["bytes"]
    remaining = len(ordered) - offset - len(picked)
    request = {"entry": entry, "role": role, "refs": refs, "purpose": purpose, "budget": limits,
               "seen_units": sorted(seen), "snapshot_scope": snapshot_scope}
    if manual_sources:
        request["manual_sources"] = sorted(set(manual_sources))
    if not persist_state:
        request["persist_state"] = False
    result = {"schema_version": 1, "snapshot_id": version, "request": request,
        "offset": offset, "status": "needs_resolution" if unresolved else "needs_split" if remaining else "ready",
        "approval_authority": False, "must_read": picked,
        "coverage": {"required_units": len(all_required), "returned_units": len(picked),
                     "previously_returned_units": len(seen & set(required)),
                     "remaining_required_units": remaining, "optional_units": len(optional),
                     "relevant_gaps": len(gaps), "source_bytes": source_bytes},
        "optional_groups": dict(Counter(source["documents"][u["path"]]["type"] for u in optional.values())),
        "gaps": gaps[:3], "unresolved_required_refs": unresolved[:3],
        "unresolved_required_count": len(unresolved), "reason": reason}
    if remaining:
        result["continuation"] = {"offset": offset + len(picked), "snapshot_id": version}
        if not picked:
            unit = ordered[offset]
            alternatives = [source["units"][key] for key in source["documents"].get(unit["path"], {}).get("units", [])
                            if source["units"][key]["kind"] != "document"]
            result["oversized"] = {"unit_id": unit["unit_id"], "bytes": unit["bytes"],
                "available_smaller_units": len(alternatives),
                "next_action": "inspect this source's units or increase the explicit budget"}
    result["plan_hash"] = catalog.digest(encoded(result))
    if cursor is not None or state is not None or manual or (remaining and not picked) \
            or len(encoded(result)) > limits["max_metadata_bytes"]:
        return paged_context(project, source, all_required, optional, reasons, gaps, unresolved,
            request, version, offset=offset, cursor=cursor, state=state, reason=reason,
            policy=policy, persist_state=persist_state and allow_state_writes, state_reuse=state_reuse)
    return result


def expand_context(project: Path, index: dict, plan: dict, *, reason: str,
                   refs: list[str] | None = None, policy: dict | None = None,
                   persist_state: bool | None = None) -> dict:
    if not reason or not reason.strip():
        raise ValueError("expanding context requires a reason")
    validate_plan(project, index, plan, policy=policy)
    request = request_data(project, plan, policy=policy)
    if persist_state is False:
        request["persist_state"] = False

    def reuse_seed():
        state = plan["request"].get("state")
        seed = {key: request[key] for key in ("refs", "manual_sources") if key in request}
        if state is not None and catalog.digest(encoded(seed)) == state["hash"]:
            return {**{key: value for key, value in request.items() if key not in seed}, "state": state}
        return request
    if "cursor" in request:
        request["cursor"] = plan.get("continuation", {}).get("cursor", {
            **request["cursor"], "unit": plan["coverage"]["previously_returned_units"] +
                plan["coverage"]["returned_units"], "byte": 0})
        if refs:
            request["refs"] = list(dict.fromkeys(request["refs"] + refs))
            try:
                return resolve_context(project, index, **reuse_seed(), expected_snapshot=plan["snapshot_id"],
                    reason=reason, policy=policy)
            except ValueError as exc:
                if "reading cursor scope changed" not in str(exc) and "stale context snapshot" not in str(exc):
                    raise
                request.pop("cursor")
                request["seen_units"] = []
                return resolve_context(project, index, **reuse_seed(), reason=reason, policy=policy)
        next_cursor = plan.get("continuation", {}).get("cursor")
        if not next_cursor or next_cursor == plan["request"]["cursor"]:
            raise ValueError(plan.get("oversized", {}).get("next_action", "the required reading set is complete"))
        return resolve_context(project, index, **reuse_seed(), expected_snapshot=plan["snapshot_id"],
            reason=reason, policy=policy)
    request["seen_units"] = sorted(set(request.get("seen_units", [])) |
                                   {unit["unit_id"] for unit in plan["must_read"]})
    if refs:
        request["refs"] = list(dict.fromkeys(request["refs"] + refs))
        return resolve_context(project, index, **request, reason=reason, policy=policy)
    next_page = plan.get("continuation")
    if not next_page or next_page["offset"] == plan["offset"]:
        raise ValueError("choose smaller units or an explicit larger budget")
    return resolve_context(project, index, **request, **{
        "offset": 0, "expected_snapshot": plan["snapshot_id"],
        "reason": reason, "policy": policy})


def validate_plan(project: Path, index: dict, plan: dict, *, policy: dict | None = None) -> None:
    # An index loaded in this invocation already hashed every eligible source.
    if index.get("files") and not getattr(index, "verified_inventory", False):
        import vault_query
        files = vault_query.scan_files(project / "workspace/docs")
        if {p: v["sha"] for p, v in files.items()} != {p: v["sha"] for p, v in index["files"].items()}:
            raise ValueError("stale context source inventory")
    state_reuse = {}
    for row in plan["must_read"]:
        reasons = row.get("reasons")
        if isinstance(reasons, dict):
            value = navigation_data(project, reasons["state"], policy=policy)
            if set(value) != {"reasons"} or not isinstance(value["reasons"], list) \
                    or len(value["reasons"]) != reasons["count"]:
                raise ValueError("invalid reading provenance state")
            state_reuse[catalog.digest(encoded(value))] = reasons["state"]
    current = resolve_context(project, index, **plan["request"], offset=plan["offset"],
        expected_snapshot=plan["snapshot_id"], reason=plan.get("reason"), policy=policy,
        state_reuse=state_reuse, allow_state_writes=False)
    if encoded(current) != encoded(plan):
        raise ValueError("context plan was modified or no longer matches its sources")


def read_plan(project: Path, index: dict, plan: dict, *, policy: dict | None = None) -> dict:
    validate_plan(project, index, plan, policy=policy)
    policy = policy or json.loads(POLICY.read_text(encoding="utf-8"))
    request = request_data(project, plan, policy=policy)
    source = catalog_view(index["catalog"])
    external_sources(project, source, request["refs"], policy)
    for unit in plan["must_read"]:
        if unit.get("git_revision") or unit.get("kind") == "fragment":
            source["units"][unit["unit_id"]] = unit
    result = catalog.read_units(project / "workspace/docs", source,
        [row["unit_id"] for row in plan["must_read"]], request["budget"]["max_source_bytes"])
    result.update(plan_status=plan["status"], coverage=plan["coverage"])
    if "manual_reads" in plan:
        result["manual_reads"] = plan["manual_reads"]
        result["status"] = "needs_manual_read"
    if "continuation" in plan:
        result["continuation"] = plan["continuation"]
    if not plan["must_read"] and "manual_reads" not in plan and plan["status"] != "ready":
        result["status"] = plan["status"]
    return result


def load_index(project: Path, *, no_cache: bool = False, repair: bool = False, failed=None) -> dict:
    import vault_query
    docs = vault_query.project_docs(project)
    cache = vault_query.default_cache(docs)
    # A no-write repair compiles the sources instead of reading the damaged cache.
    if no_cache:
        data, _status = vault_query.refresh(docs, cache, persist=False, rebuild=repair)
    else:
        try:
            data, _status = vault_query.locked_refresh(docs, cache, repair=repair, failed=failed)
        except OSError as exc:
            if exc.errno not in vault_query.READ_ONLY_ERRORS:
                raise
            data, _status = vault_query.refresh(docs, cache, persist=False, rebuild=repair)
    return data


def with_index(project: Path, action, *, no_cache: bool = False):
    """Run ``action(index)``; corruption found mid-query rebuilds the cache once and retries."""
    import vault_index
    failed = None
    for attempt in range(2):
        index = load_index(project, no_cache=no_cache, repair=attempt == 1, failed=failed)
        store = getattr(index, "store", None)
        try:
            return action(index)
        except vault_index.DATABASE_ERRORS as exc:
            failure = vault_index.sqlite_failure(exc)
            if attempt or not isinstance(failure, vault_index.CacheCorruptError):
                raise failure from exc
            failed = store.session.identity if store is not None else None
        finally:
            if store is not None:
                store.close()


def task_context(project: Path, *, entry: str, role: str | None, mode: str,
                 paths: set[str], no_cache: bool = False) -> dict:
    """Resolve a task's already-selected sources; never infer a larger scope."""
    import vault_query
    vault_query.project_docs(project)  # A fallback cannot authorize a foreign vault.
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    def eligible(path):
        if path.startswith("workspace/docs/"):
            return (path.endswith(".md") and not set(Path(path).parts) &
                {"_generated", "_ledger", "artifacts", "maps", ".obsidian"}
                and path != "workspace/docs/home.md")
        return any(path.startswith(value["root"] + "/") and Path(path).suffix in value["extensions"]
                   for value in policy["external_sources"].values())
    refs = sorted(path for path in paths if eligible(path))
    manual = sorted(paths - set(refs))
    if not refs and not manual:
        return {"status": "needs_scope", "must_read": [],
                "next_action": "Select source references with the owning flow or compiler, then resolve; use manual discovery if needed."}
    try:
        return with_index(project, lambda index: resolve_context(project, index, entry=entry, role=role, refs=refs,
            purpose=policy["task_purposes"][mode], snapshot_scope="selection",
            manual_sources=manual or None, persist_state=not no_cache), no_cache=no_cache or not refs)
    except (ValueError, OSError, UnicodeError, TypeError) as exc:
        return {"status": "unavailable", "must_read": [], "reason": str(exc)[:500],
                "next_action": "Use targeted manual reads within the project, preserve required scope and report the context finding."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--no-cache", action="store_true", help="read sources and build in memory without filesystem writes")
    sub = parser.add_subparsers(dest="command", required=True)
    resolve = sub.add_parser("resolve")
    resolve.add_argument("--entry", required=True)
    resolve.add_argument("--role", required=True)
    resolve.add_argument("--ref", dest="refs", action="append", required=True)
    resolve.add_argument("--purpose", default="discover")
    for key in ("max_files", "max_source_bytes", "max_metadata_bytes"):
        resolve.add_argument("--" + key.replace("_", "-"), type=int)
    for verb in ("expand", "check", "read"):
        child = sub.add_parser(verb)
        child.add_argument("--plan", type=Path, required=True)
        if verb == "expand":
            child.add_argument("--reason", required=True)
            child.add_argument("--ref", dest="refs", action="append")
    inspect = sub.add_parser("units")
    inspect.add_argument("--ref", required=True)
    inspect.add_argument("--offset", type=int, default=0)
    args = parser.parse_args(argv)
    project = args.project_root.resolve()

    def run(index):
        if args.command == "resolve":
            budget = {key: getattr(args, key) for key in
                      ("max_files", "max_source_bytes", "max_metadata_bytes") if getattr(args, key) is not None}
            return resolve_context(project, index, entry=args.entry, role=args.role,
                                   refs=args.refs, purpose=args.purpose, budget=budget,
                                   persist_state=not args.no_cache)
        if args.command == "units":
            hits = catalog.resolve(index["catalog"], args.ref)
            if len(hits) != 1 or args.offset < 0:
                raise ValueError("name exactly one source and a non-negative offset")
            identities = index["catalog"]["documents"][hits[0]["path"]]["units"]
            maximum = json.loads(POLICY.read_text(encoding="utf-8"))["budgets"]["max_metadata_bytes"]
            listed = []
            for key in identities[args.offset:]:
                unit = {k: v for k, v in index["catalog"]["units"][key].items()
                        if k not in {"references", "historical_properties"}}
                proposed = {"units": listed + [unit], "remaining": len(identities) - args.offset - len(listed) - 1}
                if len(encoded(proposed)) > maximum:
                    break
                listed.append(unit)
            if not listed and args.offset < len(identities):
                raise ValueError("one unit's address exceeds the metadata budget")
            return {"units": listed, "remaining": max(0, len(identities) - args.offset - len(listed))}
        payload = json.loads(args.plan.read_text(encoding="utf-8"))
        if args.command == "read" and "candidate_hash" in payload and "product_commit" in payload:
            raise ValueError("use delivery_verification.py inspect-context for a frozen candidate manifest")
        plan = payload.get("project_reading", payload)
        if "request" not in plan:
            raise ValueError("task has no resolved plan; follow its next_action before reading")
        if args.command == "read":
            return read_plan(project, index, plan)
        validate_plan(project, index, plan)
        if args.command == "expand":
            return expand_context(project, index, plan, reason=args.reason, refs=args.refs,
                                  persist_state=False if args.no_cache else None)
        return {"status": "current", "plan_hash": plan["plan_hash"]}

    try:
        result = with_index(project, run, no_cache=args.no_cache)
        print(encoded(result).decode("utf-8"))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
