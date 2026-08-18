#!/usr/bin/env python3
"""Host-neutral result envelope for Delivery coordinator commands.

The coordinator functions keep their rich Python return values for internal
callers and tests. The command-line boundary uses this module to expose one
stable, redacted shape to both host adapters.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any


SCHEMA_VERSION = 1
MUTATION_STATES = {"none", "complete", "partial", "uncertain"}
SEVERITIES = {"blocker", "error", "warning", "info"}
SEVERITY_RANK = {"blocker": 0, "error": 1, "warning": 2, "info": 3}
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_.-]{2,63}$")
OID_RE = re.compile(r"^[0-9a-f]{40,64}$")
HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")

FINDING_CODES = {
    "REQUIREMENT_ID_COLLISION", "REQUIREMENT_NOT_CURRENT", "REQUIREMENT_STAGE_ORDER",
    "REQUIREMENT_STAGE_IMPACT_INVALID", "REQUIREMENT_NOT_INCORPORATED",
    "BACKLOG_REVISION_STALE", "BACKLOG_SOURCE_CLAIMED", "BACKLOG_COVERAGE_MISMATCH",
    "DELIVERY_INPUT_INVALID", "DELIVERY_SCOPE_STALE", "DELIVERY_PLAN_STALE",
    "DELIVERY_REVIEW_STALE", "DELIVERY_CLAIM_CONFLICT", "DELIVERY_DEPENDENCY_UNMET",
    "DELIVERY_BARRIER_ACTIVE", "DELIVERY_ITEM_NOT_READY", "DELIVERY_ITEM_ALREADY_INTEGRATED",
    "DELIVERY_ITEM_REF_MISSING", "DELIVERY_PATH_CLAIM_EXCEEDED",
    "DELIVERY_CONTRACT_CLAIM_EXCEEDED", "DELIVERY_CANCELLATION_INVALID",
    "DELIVERY_CANCELLATION_FINALIZATION_STALE", "DELIVERY_TARGET_IMPACT_INVALID",
    "DELIVERY_TARGET_SOURCE_VIOLATION", "DELIVERY_TARGET_CONVERGENCE_REQUIRED",
    "DELIVERY_SOURCE_HANDOFF_STALE", "DELIVERY_TARGET_CARRIER_INVALID",
    "DELIVERY_TARGET_UPDATE_UNCERTAIN", "DELIVERY_FENCE_MISSING", "DELIVERY_FENCE_CORRUPT",
    "DELIVERY_FENCE_MODE", "DELIVERY_FENCE_LEASE_LOST", "DELIVERY_SLOT_UNAVAILABLE",
    "DELIVERY_SLOT_INVALID", "DELIVERY_SLOT_DUPLICATE", "DELIVERY_ITEM_SLOT_MISSING",
    "DELIVERY_ITEM_SLOT_DIVERGED", "DELIVERY_REMOTE_ATOMIC_UNSUPPORTED",
    "DELIVERY_REF_COLLISION", "DELIVERY_LEASE_LOST", "DELIVERY_LOCAL_REF_DIVERGED",
    "DELIVERY_WRITER_RECEIPT_MISSING", "DELIVERY_WRITER_RECEIPT_STALE",
    "DELIVERY_WORKTREE_UNSAFE", "DELIVERY_PROVIDER_UNSUPPORTED", "DELIVERY_PR_UNCERTAIN",
    "DELIVERY_PR_INTENT_STRANDED", "DELIVERY_PR_DUPLICATE", "DELIVERY_PR_STATE_INVALID",
    "DELIVERY_PR_HEAD_BASE_MISMATCH", "DELIVERY_REQUIRED_CHECK_FAILED",
    "DELIVERY_MERGE_POLICY_INVALID", "DELIVERY_MERGE_PROOF_INVALID",
    "DELIVERY_POST_MERGE_TRANSITION", "DELIVERY_UPGRADE_INCOMPATIBLE",
    "DELIVERY_UPGRADE_CONTRACT_MISMATCH", "DELIVERY_UPGRADE_HANDOFF_COLLISION",
    "DELIVERY_DESIGNATION_CHANGE_BLOCKED", "DELIVERY_PROTOCOL_UNSUPPORTED",
    "DELIVERY_PATH_ESCAPE", "DELIVERY_COORDINATION_CORRUPT", "DELIVERY_TARGET_DRIFT",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def mutation_plan_hash(operation: str, observations: list[dict], planned: list[dict]) -> str:
    projection = {
        "schema_version": SCHEMA_VERSION,
        "operation": operation,
        "observations": observations,
        "planned_mutations": planned,
    }
    return "sha256:" + hashlib.sha256(_canonical(projection).encode("utf-8")).hexdigest()


def _observation(kind: str, target: str, value: Any) -> dict:
    if kind not in {"config", "file", "provider", "ref", "worktree"}:
        raise ValueError(f"invalid observation kind: {kind}")
    if not isinstance(target, str) or not target or any(ch in target for ch in "\n\r"):
        raise ValueError("observation target must be a credential-free logical string")
    if isinstance(value, str):
        if value != "absent" and not OID_RE.fullmatch(value) and not HASH_RE.fullmatch(value):
            # Scalar values are legal only when they are registered by the
            # adapter. Keep generic command output conservative.
            if not value or any(ch in value for ch in "\n\r"):
                raise ValueError("invalid observation value")
    return {"kind": kind, "target": target, "value": value}


def _normalise_observations(values: Any) -> tuple[list[dict], list[str]]:
    """Validate observation records and return deterministic ordering."""
    if values is None:
        return [], []
    if not isinstance(values, list):
        return [], ["observations must be an array"]
    out: list[dict] = []
    errors: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for value in values:
        if not isinstance(value, dict) or set(value) != {"kind", "target", "value"}:
            errors.append("observation has an invalid shape")
            continue
        try:
            item = _observation(value["kind"], value["target"], value["value"])
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
            continue
        key = (item["kind"], item["target"], _canonical(item["value"]))
        if key in seen:
            errors.append("duplicate observation")
            continue
        seen.add(key)
        out.append(item)
    out.sort(key=lambda item: (item["kind"], item["target"], _canonical(item["value"])))
    return out, errors


def _normalise_planned(values: Any) -> tuple[list[dict], list[str]]:
    if values is None:
        return [], []
    if not isinstance(values, list):
        return [], ["planned_mutations must be an array"]
    required = {"atomic_group", "order", "kind", "target", "before", "after", "lease"}
    kinds = {
        "file_create", "file_update", "file_delete", "provider_create", "provider_update",
        "ref_create", "ref_update", "ref_delete", "worktree_create", "worktree_remove",
    }
    out: list[dict] = []
    errors: list[str] = []
    seen: set[tuple[str, int, str]] = set()
    for value in values:
        if not isinstance(value, dict) or set(value) != required:
            errors.append("planned mutation has an invalid shape")
            continue
        if (not isinstance(value["atomic_group"], str) or not SAFE_TOKEN_RE.fullmatch(value["atomic_group"])
                or not isinstance(value["order"], int) or isinstance(value["order"], bool)
                or value["order"] < 0 or value["kind"] not in kinds
                or not isinstance(value["target"], str) or not SAFE_TOKEN_RE.fullmatch(value["target"])):
            errors.append("planned mutation has invalid identity fields")
            continue
        if any(item is not None and not isinstance(item, (str, dict, list, int, bool))
               for item in (value["before"], value["after"], value["lease"])):
            errors.append("planned mutation has invalid state value")
            continue
        key = (value["atomic_group"], value["order"], value["target"])
        if key in seen:
            errors.append("duplicate planned mutation order")
            continue
        seen.add(key)
        out.append(dict(value))
    out.sort(key=lambda item: (item["atomic_group"], item["order"], item["kind"], item["target"]))
    return out, errors


def validate_envelope(envelope: dict) -> None:
    required = {"schema_version", "ok", "operation", "mutation_state", "mutation_plan_hash",
                "observations", "planned_mutations", "findings"}
    if set(envelope) != required:
        raise ValueError("result envelope keys are not closed")
    if envelope["schema_version"] != SCHEMA_VERSION or not isinstance(envelope["ok"], bool):
        raise ValueError("invalid result envelope header")
    if not isinstance(envelope["operation"], str) or not SAFE_TOKEN_RE.fullmatch(envelope["operation"]):
        raise ValueError("invalid operation")
    if envelope["mutation_state"] not in MUTATION_STATES or not HASH_RE.fullmatch(envelope["mutation_plan_hash"]):
        raise ValueError("invalid result envelope state/hash")
    observations, errors = _normalise_observations(envelope["observations"])
    if errors or observations != envelope["observations"]:
        raise ValueError("invalid observations")
    planned, errors = _normalise_planned(envelope["planned_mutations"])
    if errors or planned != envelope["planned_mutations"]:
        raise ValueError("invalid planned mutations")
    expected_hash = mutation_plan_hash(envelope["operation"], observations, planned)
    if envelope["mutation_plan_hash"] != expected_hash:
        raise ValueError("mutation plan hash does not match canonical projection")
    if not isinstance(envelope["findings"], list):
        raise ValueError("findings must be an array")
    finding_keys = {"code", "severity", "refs", "paths", "message", "next_entry"}
    for finding in envelope["findings"]:
        if not isinstance(finding, dict) or set(finding) != finding_keys:
            raise ValueError("invalid finding shape")
        if finding["code"] not in FINDING_CODES or finding["severity"] not in SEVERITIES:
            raise ValueError("invalid finding code/severity")
        if not isinstance(finding["refs"], list) or not isinstance(finding["paths"], list):
            raise ValueError("invalid finding references")
        if finding["next_entry"] is not None and not isinstance(finding["next_entry"], str):
            raise ValueError("invalid finding next_entry")


def from_raw(operation: str, raw: dict, *, error: str | None = None) -> dict:
    """Convert a rich coordinator result into the closed public envelope."""
    ok = bool(raw.get("ok")) and error is None
    state = raw.get("mutation_state")
    if state not in MUTATION_STATES:
        state = "complete" if ok else "none"
    observations: list[dict] = []
    for key, value in sorted(raw.items()):
        if key in {"ok", "errors", "findings", "mutation_state", "observations", "planned_mutations"}:
            continue
        if key in {"fence", "integration", "item", "slot", "target", "merge_commit", "reviewed_integration"}:
            if isinstance(value, str):
                observations.append(_observation("ref", key, value if OID_RE.fullmatch(value) else value))
        elif key == "pull_request_url" and isinstance(value, str):
            observations.append(_observation("provider", "pull_request_url", value))
    supplied_observations, observation_errors = _normalise_observations(raw.get("observations", []))
    observations.extend(supplied_observations)
    observations, duplicate_errors = _normalise_observations(observations)
    observation_errors.extend(duplicate_errors)
    planned, planned_errors = _normalise_planned(raw.get("planned_mutations", []))
    findings: list[dict] = []
    messages = []
    if error:
        messages.append(error)
    errors = raw.get("errors", [])
    if isinstance(errors, list):
        messages.extend(str(item) for item in errors)
    for message in messages:
        text = str(message)
        match = re.match(r"([A-Z][A-Z0-9_.-]{2,63})[: ]?(.*)", text)
        code = match.group(1) if match and match.group(1) in FINDING_CODES else "DELIVERY_INPUT_INVALID"
        detail = match.group(2).strip() if match else text
        findings.append({"code": code, "severity": "blocker", "refs": [], "paths": [],
                         "message": detail or code, "next_entry": None})
    for finding in raw.get("findings", []) if isinstance(raw.get("findings"), list) else []:
        if isinstance(finding, dict):
            code = finding.get("code", "DELIVERY_INPUT_INVALID")
            findings.append({
                "code": code if code in FINDING_CODES else "DELIVERY_INPUT_INVALID",
                "severity": finding.get("severity", "blocker") if finding.get("severity") in SEVERITIES else "blocker",
                "refs": sorted(set(str(value) for value in finding.get("refs", []) if isinstance(value, str))),
                "paths": sorted(set(str(value) for value in finding.get("paths", []) if isinstance(value, str))),
                "message": str(finding.get("message", code)),
                "next_entry": finding.get("next_entry"),
            })
    for detail in observation_errors + planned_errors:
        findings.append({"code": "DELIVERY_INPUT_INVALID", "severity": "blocker", "refs": [],
                         "paths": [], "message": detail, "next_entry": None})
    findings.sort(key=lambda item: (SEVERITY_RANK[item["severity"]], item["code"], item["refs"], item["paths"]))
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "ok": ok,
        "operation": operation,
        "mutation_state": state,
        "mutation_plan_hash": mutation_plan_hash(operation, observations, planned),
        "observations": observations,
        "planned_mutations": planned,
        "findings": findings,
    }
    validate_envelope(envelope)
    return envelope
