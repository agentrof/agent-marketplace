#!/usr/bin/env python3
"""Resolve the public Requirement entry without fuzzy resumption.

Free text always starts a new Requirement proposal. An explicit ``REQ-###``
argument selects exactly one existing record. Bare routing only offers a sole
eligible open record; zero or multiple candidates require an explicit intake
or id. This module performs no document mutation.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import requirement_compile
import stage_package


REQ_ID_RE = re.compile(r"^REQ-[0-9]{3,}$")


def read_props(path: Path) -> dict:
    try:
        props, _ = requirement_compile.split_note(path)
    except (OSError, ValueError):
        return {}
    return props


def eligible_paths(docs: Path) -> list[Path]:
    return [
        path for path in requirement_compile.requirement_paths(docs)
        if read_props(path).get("status") == "approved"
        and not requirement_compile.requirement_incorporated(
            docs, str(read_props(path).get("id", ""))
        )
    ]


def is_committed(path: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--", str(path)],
        cwd=path.parents[3], capture_output=True, text=True, check=False,
    )
    return result.returncode == 0 and not result.stdout.strip()


def route_stage(path: Path) -> dict:
    """Return the first authoring, binding or repair action for a Requirement."""
    try:
        props, body = requirement_compile.split_note(path)
    except (OSError, ValueError):
        return {"next_entry": "requirement", "stage": "requirement", "action": "requirement"}
    docs = path.parents[1]
    if props.get("status") != "approved" or props.get("source_hash") != requirement_compile.semantic_hash(props, body):
        return {"next_entry": "requirement", "stage": "requirement", "action": "requirement"}
    if not is_committed(path):
        return {"next_entry": "requirement", "stage": "requirement", "action": "requirement",
                "reason": "Requirement is not committed"}
    results = requirement_compile.stage_results(body)
    upstream_receipts: dict[str, dict] = {}
    for stage, disposition, refs, _rationale in requirement_compile.impact_rows(body):
        if disposition == "not_applicable":
            continue
        result_rows = results.get(stage, [])
        if not result_rows:
            return {
                "next_entry": stage, "stage": stage,
                "action": "bind_reuse" if disposition == "reuse" else "author",
                "upstream_receipts": upstream_receipts,
            }
        if stage != "experience-design" and len(result_rows) != 1:
            return {"next_entry": stage, "stage": stage, "action": "repair",
                    "upstream_receipts": upstream_receipts,
                    "reason": f"{stage} has an invalid receipt count"}
        if stage == "experience-design" and not requirement_compile.valid_experience_receipt_refs(
                [row[0] for row in result_rows], docs):
            return {"next_entry": stage, "stage": stage, "action": "repair",
                    "upstream_receipts": upstream_receipts,
                    "reason": "experience-design needs the exact verified application/process receipt set; zero process receipts require an empty application"}
        receipts, errors = [], []
        for result_ref, result_hash in result_rows:
            receipt, invalid = stage_package.verify(
                docs, stage, result_ref, result_hash, require_committed=True,
            )
            errors.extend(invalid)
            if receipt is not None:
                receipts.append(receipt)
        if errors:
            return {"next_entry": stage, "stage": stage, "action": "repair",
                    "upstream_receipts": upstream_receipts, "reason": "; ".join(errors)}
        upstream_receipts[stage] = receipts if stage == "experience-design" else receipts[0]
    return {"next_entry": "backlog-plan", "stage": "backlog-plan", "action": "backlog",
            "upstream_receipts": upstream_receipts}


def next_stage(path: Path) -> str:
    """Compatibility helper for callers that only need the entry name."""
    return str(route_stage(path)["next_entry"])


def route(docs: Path, argument: str | None = None) -> dict:
    docs = docs.resolve()
    if argument and REQ_ID_RE.fullmatch(argument.upper()):
        identifier = argument.upper()
        matches = [
            path for path in requirement_compile.requirement_paths(docs)
            if read_props(path).get("id") == identifier
        ]
        if not matches:
            return {
                "ok": False, "mode": "exact", "next_entry": "requirement",
                "reason": f"Requirement {identifier} was not found",
                "candidates": [],
            }
        path = matches[0]
        props = read_props(path)
        status = props.get("status")
        if status == "draft":
            actions = ["continue", "revise", "approve"]
            actions.append("withdraw" if is_committed(path) else "discard_draft")
        elif status == "approved":
            incorporated = requirement_compile.requirement_incorporated(docs, identifier)
            actions = ["inspect", "supersede"]
            if not incorporated:
                actions = ["continue", "revise", "resolve_no_change", "withdraw", "supersede"]
        else:
            actions = {
            "resolved_no_change": ["inspect"],
            "superseded": ["inspect"],
            "withdrawn": ["inspect"],
            }.get(status, ["inspect"])
        routing = route_stage(path) if status == "approved" else {
            "next_entry": "requirement", "stage": "requirement", "action": "requirement"}
        return {
            "ok": True, "mode": "exact", "requirement_id": identifier,
            "path": path.as_posix(), "status": status,
            "next_entry": routing["next_entry"], "stage": routing["stage"],
            "action": routing["action"], "upstream_receipts": routing.get("upstream_receipts", {}),
            "actions": actions,
        }
    if argument:
        return {
            "ok": True, "mode": "new", "next_entry": "requirement",
            "intake": argument, "reason": "free text always creates a new Requirement proposal",
        }
    candidates = eligible_paths(docs)
    if len(candidates) == 1:
        path = candidates[0]
        props = read_props(path)
        return {
            "ok": True, "mode": "sole_candidate", **route_stage(path),
            "requirement_id": props.get("id"), "path": path.as_posix(),
            "status": props.get("status"),
        }
    return {
        "ok": False, "mode": "ambiguous" if candidates else "new_intake",
        "next_entry": "requirement", "candidates": [path.as_posix() for path in candidates],
        "reason": "explicit intake or REQ-### is required",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("argument", nargs="?")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.project_root).resolve()
    docs = root / "workspace" / "docs"
    result = route(docs, args.argument)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["next_entry"])
        print(result.get("reason", ""))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
