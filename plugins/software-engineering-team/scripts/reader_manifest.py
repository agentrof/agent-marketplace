"""Bound the CLI presentation while retaining the complete source-bound manifest."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import atomic_file

POLICY = Path(__file__).resolve().parents[1] / "templates/project-context-policy.json"


def compact(project: Path, value: dict) -> dict:
    budget = json.loads(POLICY.read_text(encoding="utf-8"))["reader_manifest_bytes"]
    if type(budget) is not int or budget < 4096:
        raise ValueError("reader manifest budget must be at least 4096 bytes")
    raw = json.dumps(value, indent=2, sort_keys=True).encode("utf-8")
    if len(raw) <= budget:
        return value
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    from delivery_verification import runtime_anchor, safe_runtime_path
    directory = runtime_anchor(project.resolve()).resolve() / ".agentrof/agent-marketplace/.runtime/reader-manifests"
    directory = safe_runtime_path(project.resolve(), directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = safe_runtime_path(project.resolve(), directory / (digest[7:] + ".json"), file_only=True)
    atomic_file.replace_bytes(path, raw)
    result = dict(value)
    result["complete_manifest"] = {"path": str(path), "sha256": digest, "bytes": len(raw),
        "purpose": "Full source binding and optional detail. The source_hash still binds the complete manifest."}
    result["omitted_fields"] = {}
    # Keep the source identity and output contract inline; move the largest
    # collections first, regardless of which compiler supplied them.
    protected = {"ok", "schema_version", "source_hash", "structure_hash", "contract_hash", "scope",
                 "entry", "role", "mode", "head", "base", "write_boundary", "approval_authority",
                 "result_interface", "output_contract", "next_transition_conditions", "complete_manifest", "omitted_fields"}
    keys = sorted((key for key in value if key not in protected),
                  key=lambda key: len(json.dumps(value[key])), reverse=True)
    for key in keys:
        if len(json.dumps(result, indent=2, sort_keys=True).encode("utf-8")) <= budget:
            break
        item = result.pop(key)
        result["omitted_fields"][key] = {"json_pointer": "/" + key,
            "count": len(item) if isinstance(item, (list, dict)) else None}
    if len(json.dumps(result, indent=2, sort_keys=True).encode("utf-8")) > budget:
        raise ValueError("reader manifest contract exceeds its configured byte budget")
    return result


def expand(project: Path, value: dict) -> dict:
    reference = value.get("complete_manifest")
    if reference is None:
        return value
    path = Path(reference["path"])
    from delivery_verification import runtime_anchor, safe_runtime_path
    directory = runtime_anchor(project.resolve()).resolve() / ".agentrof/agent-marketplace/.runtime/reader-manifests"
    safe_runtime_path(project.resolve(), path, file_only=True)
    if path.parent != directory or not path.is_file():
        raise ValueError("complete reader manifest is outside its private directory")
    raw = path.read_bytes()
    if "sha256:" + hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise ValueError("complete reader manifest digest changed")
    result = json.loads(raw)
    if not isinstance(result, dict) or result.get("source_hash") != value.get("source_hash"):
        raise ValueError("complete reader manifest source binding changed")
    return result
