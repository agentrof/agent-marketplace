#!/usr/bin/env python3
"""Compile and verify immutable baseline, replan and feature backlog plans."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

PROGRAM_RE = re.compile(r"^PRG-(?:[0-9]{3,}|LEGACY)$")
RELEASE_RE = re.compile(r"^REL-(?:[0-9]{3,}|LEGACY)$")
STORY_RE = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]{2,}$")
QUALIFIED_CRITERION_RE = re.compile(
    r"^(?:[a-z0-9]+(?:-[a-z0-9]+)*:(?:AC|BR)-[A-Z0-9-]+"
    r"|legacy:[A-Za-z0-9._-]+)$"
)
EXACT_UX_RE = re.compile(r"^PRG-[0-9]{3,}:(?:JRN|FLW|SCR|STA|TRN)-[0-9]{3,}@r[1-9][0-9]*$")


def load(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read plan: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("plan must be a JSON object")
    return value


def canonical_plan(plan: dict) -> bytes:
    transient = {"approved_hash", "compiler_hash", "verified_at", "applied_at"}
    stable = {key: value for key, value in plan.items() if key not in transient}
    return (json.dumps(stable, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def plan_hash(plan: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical_plan(plan)).hexdigest()


def cycle(graph: dict[str, set[str]]) -> list[str]:
    visiting, visited, stack = set(), set(), []
    def walk(node):
        if node in visiting:
            return stack[stack.index(node):] + [node]
        if node in visited:
            return []
        visiting.add(node); stack.append(node)
        for target in sorted(graph.get(node, set())):
            found = walk(target)
            if found:
                return found
        stack.pop(); visiting.remove(node); visited.add(node)
        return []
    for node in sorted(graph):
        found = walk(node)
        if found:
            return found
    return []


def findings(
    plan: dict,
    mode: str,
    plan_path: Path | None = None,
    require_gates: bool = False,
) -> list[str]:
    result: list[str] = []
    if plan.get("mode") != mode:
        result.append(f"plan mode must be {mode}")
    if not PROGRAM_RE.fullmatch(str(plan.get("program_id", ""))):
        result.append("invalid program_id")
    releases = plan.get("releases")
    if not isinstance(releases, list) or not releases:
        result.append("releases must be a non-empty list")
        releases = []
    release_order: dict[str, int] = {}
    release_specs: dict[str, dict] = {}
    for index, release in enumerate(releases):
        if not isinstance(release, dict) or not RELEASE_RE.fullmatch(str(release.get("release_id", ""))):
            result.append(f"invalid release at index {index}")
            continue
        release_id = release["release_id"]
        if release_id in release_order:
            result.append(f"duplicate release {release_id}")
        release_order[release_id] = index
        release_specs[release_id] = release
    epics = plan.get("epics")
    if not isinstance(epics, list) or not epics:
        result.append("epics must be a non-empty list")
        epics = []
    epic_ids: set[str] = set()
    for index, epic in enumerate(epics):
        if not isinstance(epic, dict):
            result.append(f"epic[{index}] must be an object")
            continue
        ident = str(epic.get("external_id", ""))
        if not STORY_RE.fullmatch(ident):
            result.append(f"epic[{index}] has invalid external_id")
        if ident in epic_ids:
            result.append(f"duplicate epic {ident}")
        epic_ids.add(ident)
        if not str(epic.get("title", "")).strip() or not str(epic.get("goal", "")).strip():
            result.append(f"{ident or f'epic[{index}]'} requires title and goal")
    stories = plan.get("stories")
    if not isinstance(stories, list) or not stories:
        result.append("stories must be a non-empty list")
        stories = []
    by_id: dict[str, dict] = {}
    graph: dict[str, set[str]] = {}
    criteria_owner: dict[str, str] = {}
    for index, story in enumerate(stories):
        label = f"story[{index}]"
        if not isinstance(story, dict):
            result.append(f"{label} must be an object"); continue
        ident = str(story.get("external_id", ""))
        if not STORY_RE.fullmatch(ident):
            result.append(f"{label} has invalid external_id")
        elif ident in by_id:
            result.append(f"duplicate story {ident}")
        by_id[ident] = story
        for required in ("title", "scope", "excludes", "priority"):
            if not str(story.get(required, "")).strip():
                result.append(f"{ident or label} missing {required}")
        if str(story.get("epic", "")) not in epic_ids:
            result.append(f"{ident or label} references an unknown epic")
        release_id = str(story.get("release_id", ""))
        if release_id not in release_order:
            result.append(f"{ident or label} has no valid release allocation")
        owners = story.get("delivery_owners")
        if not isinstance(owners, dict) or not str(owners.get("owner", "")):
            result.append(f"{ident or label} missing delivery owner mapping")
        elif not isinstance(owners.get("supporting", []), list) or not all(
            isinstance(value, str) and value.strip()
            for value in owners.get("supporting", [])
        ):
            result.append(f"{ident or label} supporting owners must be a string list")
        for field in ("dor", "dod"):
            value = story.get(field)
            if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
                result.append(f"{ident or label} {field} must be a non-empty structured list")
        criteria = story.get("criteria", [])
        if not isinstance(criteria, list) or not criteria:
            result.append(f"{ident or label} has no qualified criteria")
        else:
            for criterion in criteria:
                criterion = str(criterion)
                if not QUALIFIED_CRITERION_RE.fullmatch(criterion):
                    result.append(f"{ident or label} has invalid qualified criterion {criterion}")
                if criterion in criteria_owner and criteria_owner[criterion] != ident:
                    result.append(f"criterion {criterion} is owned by multiple stories")
                criteria_owner[criterion] = ident
        if story.get("ui", False):
            refs = story.get("ux_refs", [])
            if not isinstance(refs, list) or not refs or not all(EXACT_UX_RE.fullmatch(str(value)) for value in refs):
                result.append(f"{ident or label} requires exact UX revision refs")
        for field in ("solution_refs", "budget_refs"):
            refs = story.get(field, [])
            if not isinstance(refs, list) or not refs:
                result.append(f"{ident or label} missing {field}")
        deps = story.get("depends_on", [])
        if not isinstance(deps, list):
            result.append(f"{ident or label} depends_on must be a list"); deps = []
        graph[ident] = set()
        for dependency in deps:
            if not isinstance(dependency, dict) or not str(dependency.get("item", "")) \
                    or not str(dependency.get("reason", "")).strip():
                result.append(f"{ident or label} dependencies require item and reason")
                target = str(dependency.get("item", "")) if isinstance(dependency, dict) else str(dependency)
            else:
                target = str(dependency["item"])
            if target:
                graph[ident].add(target)
        if story.get("status") == "deferred":
            deferred = story.get("deferred")
            if not isinstance(deferred, dict) or not all(deferred.get(key) for key in ("reason", "owner", "revisit")):
                result.append(f"{ident or label} missing deferred metadata")
    for ident, deps in graph.items():
        for dep in deps:
            if dep not in by_id:
                result.append(f"{ident} depends on unknown story {dep}")
                continue
            if release_order.get(str(by_id[dep].get("release_id")), 10**9) > release_order.get(str(by_id[ident].get("release_id")), -1):
                result.append(f"{ident} has a dependency from a future release: {dep}")
    for release_id, spec in release_specs.items():
        ui_refs = {
            str(ref) for story in stories
            if isinstance(story, dict) and story.get("release_id") == release_id
            and story.get("ui", False)
            for ref in story.get("ux_refs", [])
        }
        if not ui_refs:
            continue
        registry_value = str(spec.get("experience_registry", ""))
        expected_hash = str(spec.get("experience_registry_hash", ""))
        if not registry_value or not expected_hash:
            result.append(f"release {release_id} needs an experience registry path and hash")
            continue
        registry_path = Path(registry_value)
        if not registry_path.is_absolute() and plan_path is not None:
            registry_path = plan_path.parent / registry_path
        try:
            registry = load(registry_path)
        except ValueError as exc:
            result.append(f"release {release_id} registry is unreadable: {exc}")
            continue
        if registry.get("release_id") != release_id or registry.get("program_id") != plan.get("program_id"):
            result.append(f"release {release_id} registry identity mismatch")
        if registry.get("registry_hash") != expected_hash:
            result.append(f"release {release_id} registry hash mismatch")
        exact = {
            f"{registry.get('program_id')}:{item.get('id')}@r{item.get('revision')}"
            for item in registry.get("records", []) if isinstance(item, dict)
        }
        for ref in sorted(ui_refs - exact):
            result.append(f"UX ref is absent from {release_id} effective registry: {ref}")
    found = cycle(graph)
    if found:
        result.append("dependency cycle: " + " -> ".join(found))
    if mode == "feature":
        execution = plan.get("execution_set", [])
        if not isinstance(execution, list) or not execution:
            result.append("feature mode requires a non-empty execution_set")
            execution = []
        execution_ids = {str(value) for value in execution}
        for ident in sorted(execution_ids):
            story = by_id.get(ident)
            if story is None:
                result.append(f"execution_set contains unknown story {ident}")
                continue
            if not story.get("feature_story") and not story.get("approved_prerequisite"):
                result.append(f"execution story {ident} is neither feature scope nor an approved prerequisite")
            for dep in graph.get(ident, set()):
                target = by_id.get(dep)
                if target and target.get("status") not in {"done", "deferred"} and dep not in execution_ids:
                    result.append(f"execution_set omits unfinished transitive prerequisite {dep}")
    shares = plan.get("shares", [])
    if not isinstance(shares, list):
        result.append("SHARES must be a list")
    else:
        seen_shares = set()
        for index, share in enumerate(shares):
            if not isinstance(share, dict):
                result.append(f"SHARES[{index}] must be an object")
                continue
            left, right = str(share.get("left", "")), str(share.get("right", ""))
            subject = str(share.get("subject", "")).strip()
            if left not in by_id or right not in by_id or left == right or not subject:
                result.append(f"SHARES[{index}] requires two distinct stories and a subject")
                continue
            key = (left, right, subject)
            if key in seen_shares:
                result.append(f"duplicate SHARES record {left}/{right}/{subject}")
            seen_shares.add(key)
    gates = plan.get("gates", {})
    if not isinstance(gates, dict):
        result.append("gates must be an object")
    else:
        if not isinstance(gates.get("domains"), list) or not gates.get("domains") \
                or not all(isinstance(value, str) and value.strip()
                           for value in gates.get("domains", [])):
            result.append("expected domain gate identities are missing")
        if require_gates:
            if gates.get("reviewer") != "approved":
                result.append("backlog reviewer gate is not approved")
            if gates.get("program") != "approved":
                result.append("program gate is not approved")
    blockers = plan.get("findings", [])
    if isinstance(blockers, list):
        for finding in blockers:
            if require_gates and isinstance(finding, dict) and finding.get("severity") == "blocker" and finding.get("status") != "resolved":
                result.append(f"unresolved reviewer blocker {finding.get('id', '')}")
    else:
        result.append("findings must be a list")
    if plan.get("approved_hash") and plan["approved_hash"] != plan_hash(plan):
        result.append("approved_hash does not match the canonical plan")
    return sorted(set(result))


def check(args) -> int:
    try:
        plan_path = Path(args.plan)
        plan = load(plan_path)
    except ValueError as exc:
        print(f"backlog_compile: {exc}", file=sys.stderr); return 2
    values = findings(plan, args.mode, plan_path)
    result = {"ok": not values, "plan_hash": plan_hash(plan), "findings": values}
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for value in values:
            print(f"ERROR {args.plan}:1 [backlog_plan] {value}")
        if not values:
            print(result["plan_hash"])
    return 1 if values else 0


def diff(args) -> int:
    plan = load(Path(args.plan))
    command = [args.pmo, "item", "list", "--project-key", args.project_key, "--json"]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        print(completed.stderr, file=sys.stderr); return 1
    existing = {item["external_id"]: item for item in json.loads(completed.stdout)}
    planned = {item["external_id"]: item for item in plan.get("stories", [])}
    protected = []
    for ident in sorted(existing.keys() & planned.keys()):
        if existing[ident].get("status") in {"in_development", "done"}:
            for field in ("title", "scope", "excludes"):
                if str(existing[ident].get(field, "")) != str(planned[ident].get(field, "")):
                    protected.append(f"{ident}.{field}")
    result = {"added": sorted(planned.keys() - existing.keys()),
              "missing": sorted(existing.keys() - planned.keys()),
              "protected_mutations": protected}
    print(json.dumps(result, indent=2))
    return 1 if protected else 0


def verify_apply(args) -> int:
    plan = load(Path(args.plan))
    actual = plan_hash(plan)
    # PMO owns the append-only findings and gate ledger. This verifier proves
    # that the exact file still compiles; PMO verify/apply proves approvals.
    values = findings(plan, str(plan.get("mode", "")), Path(args.plan))
    if actual != args.draft_hash:
        values.append("draft hash does not match the exact plan")
    if plan.get("approved_hash") and plan.get("approved_hash") != args.draft_hash:
        values.append("plan was not approved at the supplied hash")
    print(json.dumps({"ok": not values, "plan_hash": actual, "findings": sorted(set(values))}, indent=2))
    return 1 if values else 0


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check"); p.add_argument("--plan", required=True); p.add_argument("--mode", required=True, choices=["baseline", "replan", "feature"]); p.add_argument("--json", action="store_true"); p.set_defaults(func=check)
    p = sub.add_parser("diff"); p.add_argument("--plan", required=True); p.add_argument("--against-pmo", action="store_true"); p.add_argument("--pmo", required=True); p.add_argument("--project-key", required=True); p.set_defaults(func=diff)
    p = sub.add_parser("verify-apply"); p.add_argument("--plan", required=True); p.add_argument("--draft-hash", required=True); p.set_defaults(func=verify_apply)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"backlog_compile: {exc}", file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
