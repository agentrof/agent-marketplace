"""Prepare bounded, source-addressed reading plans from existing vault relations.

Plans guide navigation. They do not replace a review scope, approve a source,
or select skills. Metadata and source-text budgets are separate.
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
import copy
import json
from pathlib import Path
import re
import sys

import context_catalog as catalog

POLICY = Path(__file__).resolve().parents[1] / "templates/project-context-policy.json"


def encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


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


def snapshot(data: dict, policy: dict) -> str:
    """Membership and relations bind the plan as well as each selected source."""
    # Every graph edge and address is derived from these exact source bytes.
    # Re-serializing all parsed units here would make each small lookup pay for
    # the entire corpus again.
    files = {path: entry["sha"] for path, entry in data.get("files", {}).items()}
    sources = {path: record["source_hash"] for path, record in data["catalog"]["documents"].items()}
    return catalog.digest(encoded({"builder": data.get("builder"), "files": files,
                                   "sources": sources, "policy": policy}))


def resolve_context(project: Path, index: dict, *, entry: str, role: str, refs: list[str],
                    purpose: str = "discover", budget: dict | None = None,
                    offset: int = 0, expected_snapshot: str | None = None,
                    reason: str | None = None, policy: dict | None = None,
                    seen_units: list[str] | None = None) -> dict:
    policy = policy or json.loads(POLICY.read_text(encoding="utf-8"))
    policy = copy.deepcopy(policy)
    for group in ("entry_profiles", "role_profiles"):
        policy[group] = {key.replace("_", "-"): value for key, value in policy[group].items()}
    if entry not in policy["entry_profiles"] or purpose not in policy["purposes"]:
        raise ValueError("entry or purpose is not declared by the context policy")
    tasks = json.loads((POLICY.parent / "task-input-policy.json").read_text(encoding="utf-8"))
    route = tasks["entries"].get(entry.replace("-", "_"), {})
    if role not in route.get("roles", []):
        raise ValueError("role does not belong to this entry")
    if not role or not refs or offset < 0:
        raise ValueError("role, references and a non-negative offset are required")
    if offset and (not expected_snapshot or not reason or not reason.strip()):
        raise ValueError("continuation requires its snapshot and a reason")
    limits = dict(policy["budgets"])
    if budget:
        if set(budget) - set(limits):
            raise ValueError("unknown context budget")
        limits.update(budget)
    if any(type(value) is not int or value <= 0 for value in limits.values()):
        raise ValueError("context budgets must be positive integers")
    data = dict(index, catalog={key: dict(value) for key, value in index["catalog"].items()})
    external_sources(project, data["catalog"], refs, policy)
    version = snapshot(data, policy)
    if expected_snapshot is not None and version != expected_snapshot:
        raise ValueError("stale context snapshot; resolve the current sources again")
    profile = policy["entry_profiles"][entry]
    required_keys = set(policy["required_relations"]) | set(profile["required_relations"])
    required_keys.update(policy["purpose_profiles"][purpose]["required_relations"])
    optional_keys = set(policy["optional_relations"])
    preferred = profile["preferred_types"] + policy["role_profiles"].get(role, [])
    source = data["catalog"]
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
    for origin, target, key, _tiers in data.get("edges", []):
        outgoing.setdefault(origin, []).append((target, key))
    visited = set()
    while pending:
        current = pending.popleft()
        path = current["path"]
        if current["kind"] in {"row", "scenario"}:
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
        for target, key in ([] if current.get("historical") else outgoing.get(path, [])):
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
    all_required = sorted(required.values(), key=order)
    seen = set(seen_units or [])
    if seen - set(source["units"]):
        raise ValueError("previously returned unit no longer exists")
    ordered = [unit for unit in all_required if unit["unit_id"] not in seen]
    if offset >= len(ordered) and offset:
        raise ValueError("continuation is outside the required reading set")
    relevant_paths = {unit["path"] for unit in ordered}
    gaps = [gap for gap in data.get("gaps", [])
            if gap.get("path") in relevant_paths or gap.get("source") in relevant_paths
            or gap.get("target") in relevant_paths]
    picked, paths, source_bytes = [], set(), 0
    for unit in ordered[offset:]:
        if len(paths | {unit["path"]}) > limits["max_files"] or source_bytes + unit["bytes"] > limits["max_source_bytes"]:
            break
        item = {key: value for key, value in unit.items() if key not in {"references", "historical_properties"}}
        item["reasons"] = reasons[unit["unit_id"]]
        state = unit.get("historical_properties", source["documents"][unit["path"]])
        item["source_state"] = {key: state.get(key)
                                for key in ("status", "revision", "record_state", "revision_state")}
        if len(encoded(picked + [item])) > limits["max_metadata_bytes"] // 2:
            break
        picked.append(item)
        paths.add(unit["path"])
        source_bytes += unit["bytes"]
    remaining = len(ordered) - offset - len(picked)
    request = {"entry": entry, "role": role, "refs": refs, "purpose": purpose, "budget": limits,
               "seen_units": sorted(seen)}
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
            alternatives = [source["units"][key] for key in source["documents"][unit["path"]]["units"]
                            if source["units"][key]["kind"] != "document"]
            result["oversized"] = {"unit_id": unit["unit_id"], "bytes": unit["bytes"],
                "available_smaller_units": len(alternatives),
                "next_action": "inspect this source's units or increase the explicit budget"}
    if len(encoded(result)) > limits["max_metadata_bytes"]:
        raise ValueError("metadata budget is too small for the request and reading addresses")
    result["plan_hash"] = catalog.digest(encoded(result))
    if len(encoded(result)) > limits["max_metadata_bytes"]:
        raise ValueError("metadata budget is too small for the signed reading plan")
    return result


def expand_context(project: Path, index: dict, plan: dict, *, reason: str,
                   refs: list[str] | None = None, policy: dict | None = None) -> dict:
    if not reason or not reason.strip():
        raise ValueError("expanding context requires a reason")
    validate_plan(project, index, plan, policy=policy)
    request = dict(plan["request"])
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
    if index.get("files"):
        import vault_query
        files = vault_query.scan_files(project / "workspace/docs", {}, verify=True)
        if {p: v["sha"] for p, v in files.items()} != {p: v["sha"] for p, v in index["files"].items()}:
            raise ValueError("stale context source inventory")
    current = resolve_context(project, index, **plan["request"], offset=plan["offset"],
        expected_snapshot=plan["snapshot_id"], reason=plan.get("reason"), policy=policy)
    if encoded(current) != encoded(plan):
        raise ValueError("context plan was modified or no longer matches its sources")


def load_index(project: Path, *, no_cache: bool = False) -> dict:
    import vault_query
    docs = project / "workspace/docs"
    cache = vault_query.default_cache(docs)
    if no_cache:
        data, _status = vault_query.refresh(docs, cache, verify=True, persist=False)
    else:
        try:
            data, _status = vault_query.locked_refresh(docs, cache, verify=True)
        except OSError as exc:
            if exc.errno not in vault_query.READ_ONLY_ERRORS:
                raise
            data, _status = vault_query.refresh(docs, cache, verify=True, persist=False)
    return data


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
    try:
        project = args.project_root.resolve()
        index = load_index(project, no_cache=args.no_cache)
        if args.command == "resolve":
            budget = {key: getattr(args, key) for key in
                      ("max_files", "max_source_bytes", "max_metadata_bytes") if getattr(args, key) is not None}
            result = resolve_context(project, index, entry=args.entry, role=args.role,
                                     refs=args.refs, purpose=args.purpose, budget=budget)
        elif args.command == "units":
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
            result = {"units": listed, "remaining": max(0, len(identities) - args.offset - len(listed))}
        else:
            plan = json.loads(args.plan.read_text(encoding="utf-8"))
            validate_plan(project, index, plan)
            if args.command == "expand":
                result = expand_context(project, index, plan, reason=args.reason, refs=args.refs)
            elif args.command == "read":
                policy = json.loads(POLICY.read_text(encoding="utf-8"))
                external_sources(project, index["catalog"], plan["request"]["refs"], policy)
                for unit in plan["must_read"]:
                    if unit.get("git_revision"):
                        index["catalog"]["units"][unit["unit_id"]] = unit
                result = catalog.read_units(project / "workspace/docs", index["catalog"],
                    [row["unit_id"] for row in plan["must_read"]], plan["request"]["budget"]["max_source_bytes"])
                result.update(plan_status=plan["status"], coverage=plan["coverage"])
                if "continuation" in plan:
                    result["continuation"] = plan["continuation"]
                if not plan["must_read"] and plan["status"] != "ready":
                    result["status"] = plan["status"]
            else:
                result = {"status": "current", "plan_hash": plan["plan_hash"]}
        print(encoded(result).decode("utf-8"))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "refused", "reason": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
