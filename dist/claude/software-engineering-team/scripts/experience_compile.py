#!/usr/bin/env python3
"""Compile living, process-owned Experience Design packages.

Experience Design uses ``experiences/<slug>/experience.md``.  It never owns
deployment releases, numbered baselines or program trees.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import stage_package


GENERATED = "_generated"
LEDGER = "_ledger"
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
EXACT = re.compile(r"^([a-z0-9]+(?:-[a-z0-9]+)*):(JRN|FLW|SCR|STA|TRN)-[0-9]{3,}@r([1-9][0-9]*)$")
PACKAGE = re.compile(r"^([a-z0-9]+(?:-[a-z0-9]+)*)@r([1-9][0-9]*)$")
KIND = {
    "journey": ("journeys", "JRN", "-journey.md"),
    "flow-set": ("flows", "FLW", "-flow-set.md"),
    "screen": ("screens", "SCR", "-screen.md"),
    "state": ("states", "STA", "-state.md"),
    "transition": ("transitions", "TRN", "-transition.md"),
}


def valid_experience_slug(value: str) -> bool:
    return bool(SLUG.fullmatch(value)) and not value.startswith("exp-")


def fail(message: str, code: int = 1) -> int:
    print(f"experience_compile: {message}", file=sys.stderr)
    return code


def canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def sha(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def fm(path: Path) -> tuple[dict, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        raise ValueError("missing frontmatter")
    data, current, end = {}, "", -1
    for number, raw in enumerate(lines[1:], 1):
        line = raw.strip()
        if line == "---":
            end = number
            break
        if not line:
            continue
        if line.startswith("- ") and current:
            data.setdefault(current, []).append(line[2:].strip().strip("\"'"))
            continue
        if ":" not in line:
            raise ValueError(f"unparseable frontmatter line {number + 1}")
        key, value = (part.strip() for part in line.split(":", 1))
        if not value:
            data[key], current = [], key
        else:
            current = ""
            if value.startswith(("[", "{")):
                try:
                    data[key] = json.loads(value)
                except json.JSONDecodeError:
                    data[key] = value.strip("\"'")
            elif value.isdigit():
                data[key] = int(value)
            else:
                data[key] = value.strip("\"'")
    if end < 0:
        raise ValueError("unterminated frontmatter")
    return data, "\n".join(lines[end + 1:]).strip() + "\n"


def render_fm(data: dict, body: str) -> str:
    rows = ["---"]
    for key, value in data.items():
        if isinstance(value, list):
            rows.append(f"{key}:")
            rows.extend(f"  - {json.dumps(item, ensure_ascii=False)}" for item in value)
        elif isinstance(value, dict):
            rows.append(f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
        else:
            rows.append(f"{key}: {value}")
    return "\n".join(rows + ["---", "", body.rstrip(), ""])


def status_tags(data: dict) -> None:
    tags = [f"doc/{data['type']}"]
    if data.get("status"):
        tags.append("status/" + str(data["status"]).replace("_", "-"))
    data["tags"] = tags


def write(path: Path, data: dict, title: str, content: str, nav: str) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dict(data)
    data.setdefault("title", title)
    status_tags(data)
    body = (f"# {title}\n\n{content.strip()}\n\n## Navigation <!-- sec: nav -->\n\n"
            f"[[{nav}|{nav.rsplit('/', 1)[-1].replace('-', ' ').title()}]]\n")
    path.write_text(render_fm(data, body), encoding="utf-8")


def rewrite(path: Path, data: dict, body: str) -> None:
    path.write_text(render_fm(data, body), encoding="utf-8")


def root_for(value: str | Path) -> Path:
    path = Path(value).resolve()
    if path.name == "experience-design":
        return path
    if path.name == "docs":
        return path / "experience-design"
    if (path / "workspace" / "docs").is_dir():
        return path / "workspace" / "docs" / "experience-design"
    if (path / "experience-design").is_dir():
        return path / "experience-design"
    raise ValueError("--root must identify workspace/docs/experience-design")


def package_for(value: str | Path) -> Path:
    path = Path(value).resolve()
    if path.name == "experience.md":
        path = path.parent
    if path.parent.name != "experiences" or not (path / "experience.md").is_file():
        raise ValueError("--experience-root must identify experiences/<slug>")
    return path


def docs_for(package: Path) -> Path:
    if package.parents[2].name != "docs":
        raise ValueError("Experience package is not below workspace/docs")
    return package.parents[2]


def packages(root: Path) -> list[Path]:
    parent = root / "experiences"
    return sorted((item for item in parent.iterdir() if item.is_dir()), key=lambda item: item.name) if parent.is_dir() else []


def fields(package: Path) -> dict:
    try:
        return fm(package / "experience.md")[0]
    except (OSError, ValueError):
        return {}


def list_value(data: dict, key: str) -> list[str]:
    value = data.get(key, [])
    return [str(item) for item in value] if isinstance(value, list) else ([str(value)] if value else [])


def ref_value(value: str) -> str:
    value = value.strip()
    if value.startswith("[[") and value.endswith("]]" ):
        return value[2:-2].split("|", 1)[0].split("#", 1)[0]
    return value.removesuffix(".md")


def bindings(data: dict) -> dict[str, tuple[str, str]]:
    result = {}
    for entry in list_value(data, "input_bindings"):
        stage, marker, rest = entry.partition("|")
        reference, marker2, digest = rest.partition("|")
        if marker and marker2:
            result[stage] = (reference, digest)
    return result


def binding_rows(receipts: list[dict]) -> list[str]:
    return [f"{item['stage']}|{item['result_ref']}|{item['package_hash']}" for item in receipts]


def requirement_upstream_hash(results: dict[str, list[tuple[str, str]]]) -> str:
    """Hash only the receipts that form an Experience package input.

    The Experience receipt is written after approval.  Including that receipt
    in its own input hash creates a self-invalidating package.
    """
    upstream = {
        stage: sorted(results.get(stage, []))
        for stage in ("business-analysis", "solution-design", "design-system")
    }
    return sha(canonical(upstream))


def proposal_digest(plan: dict) -> str:
    stable = {key: value for key, value in plan.items() if key != "proposal_hash"}
    return sha(canonical(stable))


def input_rows(plan: dict) -> list[tuple[str, str, str]]:
    rows = []
    for value in plan.get("input_bindings", []):
        stage, marker, remainder = str(value).partition("|")
        reference, marker2, digest = remainder.partition("|")
        if not marker or not marker2:
            raise ValueError("scope plan contains an invalid input binding")
        rows.append((stage, reference, digest))
    return rows


def load_scope_plan(path_value: str, provided_hash: str) -> dict:
    if not path_value:
        raise ValueError("mutation requires --scope-plan produced by propose")
    try:
        plan = json.loads(Path(path_value).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"scope plan is unreadable: {exc}") from exc
    if not isinstance(plan, dict) or plan.get("schema_version") != 1:
        raise ValueError("scope plan has an unsupported schema")
    actual = proposal_digest(plan)
    if not provided_hash or provided_hash != actual or plan.get("proposal_hash") != actual:
        raise ValueError("scope plan hash does not match the approved proposal")
    if not isinstance(plan.get("actions"), list) or not plan["actions"]:
        raise ValueError("scope plan needs one or more actions")
    return plan


def verify_scope_inputs(root: Path, plan: dict, *, require_committed: bool) -> list[str]:
    errors = []
    expected = {"business-analysis", "solution-design", "design-system"}
    try:
        rows = input_rows(plan)
    except ValueError as exc:
        return [str(exc)]
    present = {stage for stage, _reference, _digest in rows}
    if present != expected or len(rows) != len(expected):
        return ["scope plan must pin exactly one Business Analysis, Solution and Design input"]
    for stage, reference, digest in rows:
        _receipt, findings = stage_package.verify(
            root.parent, stage, reference, digest, require_committed,
            require_strict_current=True,
        )
        errors.extend(findings)
    if plan.get("origin_mode") == "requirement":
        requirement = str(plan.get("requirement", ""))
        try:
            import requirement_compile, requirement_route
            matches = [path for path in requirement_compile.requirement_paths(root.parent)
                       if requirement_compile.split_note(path)[0].get("id") == requirement]
            if len(matches) != 1:
                errors.append("scope plan Requirement is not uniquely resolvable")
            else:
                props, body = requirement_compile.split_note(matches[0])
                semantic = requirement_compile.semantic_hash(props, body)
                upstream = requirement_upstream_hash(requirement_compile.stage_results(body))
                if props.get("status") != "approved" or semantic != plan.get("requirement_semantic_hash"):
                    errors.append("scope plan Requirement semantic state is stale")
                if upstream != plan.get("upstream_stage_receipts_hash"):
                    errors.append("scope plan Requirement upstream receipt set is stale")
                route = requirement_route.route(root.parent, requirement)
                if route.get("stage") != "experience-design" or route.get("action") != "author":
                    errors.append("scope plan Requirement is no longer ready to author Experience Design")
        except (ImportError, OSError, ValueError):
            errors.append("scope plan Requirement cannot be verified")
    return errors


def action_for_plan(root: Path, plan: dict, *, action: str, experience: str,
                    process: str, target: str = "", validate_current: bool = True) -> dict:
    matches = [row for row in plan["actions"] if isinstance(row, dict)
               and row.get("action") == action
               and row.get("experience") == experience
               and row.get("primary_process_ref") == process
               and (not target or row.get("target_experience", "") == target)]
    if len(matches) != 1:
        raise ValueError("mutation is not one of the approved scope-plan actions")
    selected = matches[0]
    if validate_current:
        package = root / "experiences" / experience
        expected = selected.get("expected_package", {})
        if action == "create":
            if package.exists():
                raise ValueError("scope-plan create target is no longer absent")
        elif not isinstance(expected, dict) or not package.is_dir():
            raise ValueError("scope-plan target package is missing")
        elif (expected.get("revision") != fields(package).get("revision")
              or expected.get("status") != fields(package).get("status")
              or expected.get("source_hash") != source_digest(package)):
            raise ValueError("scope-plan target package changed after proposal")
    return selected


def package_record_ids(package: Path) -> list[str]:
    return sorted(str(row.get("id", "")) for row in records(package, [])
                  if str(row.get("id", "")))


def external_active_record(root: Path, source: Path, reference: str,
                           related_processes: set[str]) -> bool:
    """Resolve one cross-Experience reference without recursively compiling it.

    A cross-package read must observe an approved, source-current target.  We
    intentionally consume its rendered registry here so two Experiences can
    reference each other without recursive compiler calls.
    """
    match = EXACT.fullmatch(reference)
    if match is None:
        return False
    target = resolve_package(root, match.group(1))
    if target is None or target == source:
        return False
    target_data = fields(target)
    if (target_data.get("status") != "approved"
            or str(target_data.get("primary_process_ref", "")) not in related_processes
            or target_data.get("source_hash") != source_digest(target)):
        return False
    try:
        target_registry = json.loads((target / GENERATED / "registry.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if (target_registry.get("registry_hash") != target_data.get("registry_hash")
            or target_registry.get("package_hash") != target_data.get("package_hash")):
        return False
    ident, revision = reference.split(":", 1)[1].split("@", 1)
    return any(row.get("id") == ident and row.get("revision") == int(revision[1:])
               and row.get("record_state") == "active"
               for row in target_registry.get("records", []) if isinstance(row, dict))


def authored(package: Path) -> list[Path]:
    return sorted(path for path in package.rglob("*.md") if GENERATED not in path.parts and LEDGER not in path.parts)


def source_digest(package: Path) -> str:
    ignored = {"status", "approval_revision", "registry_hash", "package_hash", "source_hash", "approved_at_utc", "tags", "artifact_sha256", "manifest_hash"}
    digest = hashlib.sha256()
    for path in authored(package):
        data, body = fm(path)
        stable = {key: value for key, value in data.items() if key not in ignored}
        digest.update(path.relative_to(package).as_posix().encode())
        digest.update(b"\0")
        digest.update(render_fm(stable, body).encode())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def artifact_digest(content: bytes) -> str:
    text = re.sub(r'(<meta\s+name=["\']experience-registry-hash["\']\s+content=["\'])[^"\']*(["\'])', r"\1__REGISTRY_HASH__\2", content.decode(), flags=re.I)
    return sha(text.encode())


def ledger(package: Path) -> list[dict]:
    try:
        data = json.loads((package / LEDGER / "package-revisions.json").read_text(encoding="utf-8"))
        return [row for row in data.get("revisions", []) if isinstance(row, dict)]
    except (OSError, json.JSONDecodeError):
        return []


def write_ledger(package: Path, rows: list[dict]) -> None:
    target = package / LEDGER / "package-revisions.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(canonical({"revisions": sorted(rows, key=lambda row: int(row.get("package_revision", 0) or 0))}))


def package_aliases(package: Path) -> set[str]:
    result = {package.name, str(fields(package).get("experience_id", ""))}
    result.update(list_value(fields(package), "aliases"))
    try:
        result.update(json.loads((package / LEDGER / "aliases.json").read_text(encoding="utf-8")).get("aliases", {}).keys())
    except (OSError, json.JSONDecodeError):
        pass
    return {item for item in result if item}


def resolve_package(root: Path, identifier: str) -> Path | None:
    candidates = [package for package in packages(root) if identifier in package_aliases(package)]
    return candidates[0] if len(candidates) == 1 else None


def snapshots(package: Path, ident: str, revision: int) -> dict | None:
    try:
        value = json.loads((package / LEDGER / "records" / ident / f"r{revision}.json").read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def records(package: Path, findings: list[str]) -> list[dict]:
    result, ids = [], set()
    for kind, (directory, prefix, suffix) in KIND.items():
        directory_path = package / directory
        for path in sorted(directory_path.glob(f"*{suffix}")) if directory_path.is_dir() else []:
            rel = path.relative_to(package).as_posix()
            try:
                data, _body = fm(path)
            except (OSError, ValueError) as exc:
                findings.append(f"{rel}: {exc}"); continue
            ident, revision = str(data.get("id", "")), data.get("revision")
            if data.get("type") != kind or not re.fullmatch(prefix + r"-[0-9]{3,}", ident):
                findings.append(f"{rel}: invalid {kind} identity")
            if not isinstance(revision, int) or revision < 1:
                findings.append(f"{rel}: revision must be a positive integer")
            if ident in ids:
                findings.append(f"{rel}: duplicate package record id {ident}")
            ids.add(ident)
            if data.get("record_state") not in {"active", "retired"}:
                findings.append(f"{rel}: record_state must be active or retired")
            if any(key in data for key in ("status", "approved_at_utc", "approval_hash")):
                findings.append(f"{rel}: child records cannot carry approval state")
            if data.get("record_state") == "active" and not list_value(data, "derives_from"):
                findings.append(f"{rel}: active record needs derives_from")
            if kind == "screen" and data.get("record_state") == "active" and not list_value(data, "uses_design"):
                findings.append(f"{rel}: active screen needs uses_design")
            result.append({**data, "path": rel})
    return sorted(result, key=lambda row: (str(row.get("id", "")), int(row.get("revision", 0) or 0)))


def artifacts(package: Path, registry: dict, findings: list[str], gate: bool) -> list[dict]:
    output, known = [], {str(row.get("id", "")) for row in registry["records"]}
    folder = package / "artifacts"
    for manifest in sorted(folder.glob("*-artifact.md")) if folder.is_dir() else []:
        rel = manifest.relative_to(package).as_posix()
        try:
            data, _body = fm(manifest)
        except (OSError, ValueError) as exc:
            findings.append(f"{rel}: {exc}"); continue
        artifact = package / str(data.get("artifact_path", ""))
        if data.get("type") != "artifact-manifest" or artifact.suffix != ".html" or not artifact.is_file():
            findings.append(f"{rel}: manifest must own local HTML artifact"); continue
        html = artifact.read_text(encoding="utf-8")
        if re.search(r"(?:href|src)=[\"'](?:https?:|ftp:|//)", html):
            findings.append(f"{rel}: artifact must be network-free")
        declared = sorted(set(re.findall(r"(?:id|data-experience-id)=[\"']((?:JRN|FLW|SCR|STA|TRN)-[0-9]{3,})[\"']", html)))
        if set(declared) - known:
            findings.append(f"{rel}: HTML declares unknown experience IDs")
        if declared != sorted(set(list_value(data, "declared_ids"))):
            findings.append(f"{rel}: declared_ids must match HTML")
        if gate:
            for key, expected in (("experience-id", registry["experience_id"]), ("experience-registry-hash", registry["registry_hash"])):
                match = re.search(rf'<meta\s+name=["\']{key}["\']\s+content=["\']([^"\']+)', html, re.I)
                if not match or match.group(1) != expected:
                    findings.append(f"{rel}: {key} does not match registry")
        output.append({"manifest": rel, "path": artifact.relative_to(package).as_posix(), "sha256": artifact_digest(artifact.read_bytes()), "declared_ids": declared})
    return output


def compile_package(package: Path, gate: bool = False) -> tuple[dict, list[str]]:
    problems = []
    try:
        data, _body = fm(package / "experience.md")
    except (OSError, ValueError) as exc:
        return {}, [f"experience.md: {exc}"]
    if data.get("type") != "experience": problems.append("experience.md: root type must be experience")
    if (not valid_experience_slug(str(data.get("experience_id", "")))
            or data.get("experience_id") != package.name):
        problems.append("experience.md: experience_id must match a non-exp lower-kebab folder")
    if data.get("status") not in {"draft", "in_review", "approved", "retired"}: problems.append("experience.md: invalid status")
    if not str(data.get("primary_process_ref", "")).strip(): problems.append("experience.md: primary_process_ref is required")
    if data.get("origin_mode") not in {"manual", "requirement"}: problems.append("experience.md: origin_mode must be manual or requirement")
    implemented_requirements = list_value(data, "implements")
    if data.get("origin_mode") == "requirement" and (len(implemented_requirements) != 1 or not re.fullmatch(r"REQ-[0-9]{3,}", implemented_requirements[0])): problems.append("experience.md: requirement mode needs exactly one implements: REQ-###")
    if data.get("origin_mode") == "requirement" and not str(data.get("upstream_stage_receipts_hash", "")).startswith("sha256:"):
        problems.append("experience.md: requirement mode needs upstream_stage_receipts_hash")
    if data.get("origin_mode") == "manual" and data.get("implements"): problems.append("experience.md: manual package cannot implement Requirement")
    for key in ("baseline_id", "inherits", "program_id", "release_id", "baseline", "program", "release"):
        if key in data: problems.append(f"experience.md: legacy field {key} is forbidden")
    docs = docs_for(package)
    if data.get("origin_mode") == "requirement" and len(implemented_requirements) == 1 and re.fullmatch(r"REQ-[0-9]{3,}", implemented_requirements[0]):
        try:
            import requirement_compile
            matches = [path for path in requirement_compile.requirement_paths(docs)
                       if requirement_compile.split_note(path)[0].get("id") == implemented_requirements[0]]
            if len(matches) != 1:
                problems.append("experience.md: implements Requirement is not uniquely resolvable")
            else:
                requirement_props, requirement_body = requirement_compile.split_note(matches[0])
                actual_results_hash = requirement_upstream_hash(
                    requirement_compile.stage_results(requirement_body))
                if (requirement_props.get("status") != "approved"
                        or data.get("upstream_stage_receipts_hash") != actual_results_hash):
                    problems.append("experience.md: Requirement receipt set is stale")
        except (ImportError, OSError, ValueError):
            problems.append("experience.md: Requirement receipt set cannot be verified")
    for stage in ("business-analysis", "solution-design", "design-system"):
        item = bindings(data).get(stage)
        if item is None:
            problems.append(f"experience.md: missing {stage} input binding")
        else:
            _receipt, errors = stage_package.verify(docs, stage, item[0], item[1])
            problems.extend(f"experience.md: {error}" for error in errors)
    ba_binding = bindings(data).get("business-analysis")
    if ba_binding is not None:
        canonical_process, process_errors = stage_package.resolve_ba_process(
            docs, str(data.get("primary_process_ref", "")),
            expected_ba_ref=ba_binding[0], expected_ba_hash=ba_binding[1],
        )
        problems.extend(f"experience.md: {error}" for error in process_errors)
        if canonical_process and canonical_process != data.get("primary_process_ref"):
            problems.append("experience.md: primary_process_ref is not canonical")
    rows = records(package, problems)
    live = {f"{package.name}:{row['id']}@r{row['revision']}" for row in rows
            if row.get("record_state") == "active"}
    related_processes = set(list_value(data, "related_process_refs"))
    experience_root = package.parent.parent
    for row in rows:
        for field in ("journey_refs", "flow_refs", "screen_refs", "state_refs", "transition_refs", "related_to"):
            for reference in list_value(row, field):
                if not EXACT.fullmatch(reference):
                    problems.append(f"{row['path']}: {field} needs exact Experience refs")
                elif reference not in live and not external_active_record(
                        experience_root, package, reference, related_processes):
                    problems.append(f"{row['path']}: {field} targets a missing, retired, or stale Experience record: {reference}")
    registry = {"schema_version": 4, "experience_id": package.name, "package_revision": data.get("revision", 1), "origin_mode": data.get("origin_mode"), "implements": implemented_requirements, "primary_process_ref": data.get("primary_process_ref", ""), "related_process_refs": list_value(data, "related_process_refs"), "input_bindings": {key: list(value) for key, value in bindings(data).items()}, "source_hash": source_digest(package), "records": rows}
    registry["artifacts"] = artifacts(package, registry, problems, False)
    registry["registry_hash"] = sha(canonical({key: value for key, value in registry.items() if key not in {"source_hash", "registry_hash", "package_hash"}}))
    registry["package_hash"] = sha(canonical({"source_hash": registry["source_hash"], "registry_hash": registry["registry_hash"], "artifacts": registry["artifacts"]}))
    generated = package / GENERATED / "registry.json"
    if generated.is_file():
        try:
            current_registry = json.loads(generated.read_text(encoding="utf-8"))
            if current_registry != registry:
                problems.append("_generated/registry.json: registry is stale or tampered")
        except (OSError, json.JSONDecodeError): problems.append("_generated/registry.json: registry is unreadable")
    root = package.parent.parent
    for other in packages(root):
        if other != package and fields(other).get("status") != "retired" and fields(other).get("primary_process_ref") == data.get("primary_process_ref"):
            problems.append(f"experience.md: primary process is already owned by {other.name}")
    previous = ledger(package)
    if previous:
        old = max(previous, key=lambda row: int(row.get("package_revision", 0) or 0))
        old_rows = {str(row.get("id")): row for row in old.get("records", []) if isinstance(row, dict)}
        for row in rows:
            prior = old_rows.get(str(row.get("id", "")))
            if prior and {k: v for k, v in prior.items() if k != "path"} != {k: v for k, v in row.items() if k != "path"}:
                old_revision = int(prior.get("revision", 0) or 0)
                if int(row.get("revision", 0) or 0) <= old_revision or row.get("supersedes") != f"{package.name}:{row.get('id')}@r{old_revision}":
                    problems.append(f"{row['path']}: changed record must increment revision and supersede {package.name}:{row.get('id')}@r{old_revision}")
    if gate:
        if data.get("status") != "approved": problems.append("experience.md: package is not approved")
        if not rows: problems.append("experience.md: empty Experience package cannot be approved")
        if data.get("registry_hash") != registry["registry_hash"] or data.get("package_hash") != registry["package_hash"]: problems.append("experience.md: approved hashes are stale")
        artifacts(package, registry, problems, True)
    return registry, sorted(set(problems))


def print_problems(problems: list[str], as_json: bool) -> int:
    if as_json:
        print(json.dumps({"ok": not problems, "findings": [{"message": item} for item in problems]}, indent=2))
    else:
        for item in problems: print(f"ERROR {item}")
    return 1 if problems else 0


def package_receipt(package: Path, registry: dict) -> dict:
    return {"stage": "experience-design", "result_ref": f"{package.name}@r{registry['package_revision']}", "result_type": "experience-package", "package_hash": registry["package_hash"], "status": "approved", "current": True}


def manual_inputs(root: Path, args) -> tuple[list[dict], list[str]]:
    receipts, errors = [], []
    for stage, refs in (("business-analysis", args.ba_ref), ("solution-design", args.solution_ref), ("design-system", args.design_ref)):
        if len(refs) != 1:
            errors.append(f"manual mode needs exactly one {stage} reference"); continue
        receipt, invalid = stage_package.verify(root.parent, stage, ref_value(refs[0]),
                                                require_committed=True,
                                                require_strict_current=True)
        if receipt is None or invalid: errors.extend(invalid or [f"invalid {stage} input"])
        else: receipts.append(receipt)
    return receipts, errors


def requirement_inputs(root: Path, requirement: str) -> tuple[list[dict], list[str], dict]:
    if not re.fullmatch(r"REQ-[0-9]{3,}", requirement):
        return [], ["requirement mode needs --requirement REQ-###"], {}
    import requirement_compile, requirement_route
    candidates = [path for path in requirement_compile.requirement_paths(root.parent)
                  if requirement_compile.split_note(path)[0].get("id") == requirement]
    route = requirement_route.route(root.parent, requirement)
    if len(candidates) != 1 or route.get("stage") != "experience-design" or route.get("action") != "author":
        return [], ["Requirement router is not ready to author Experience Design"], {}
    props, body = requirement_compile.split_note(candidates[0])
    result = requirement_compile.stage_results(body)
    receipts, errors = [], []
    for stage in ("business-analysis", "solution-design", "design-system"):
        rows = result.get(stage, [])
        if len(rows) != 1:
            errors.append(f"Requirement needs one {stage} receipt")
            continue
        receipt, invalid = stage_package.verify(root.parent, stage, rows[0][0], rows[0][1],
                                                require_committed=True,
                                                require_strict_current=True)
        if receipt is None or invalid:
            errors.extend(invalid or [f"invalid {stage} input"])
        else:
            receipts.append(receipt)
    context = {
        "requirement": requirement,
        "requirement_semantic_hash": requirement_compile.semantic_hash(props, body),
        "upstream_stage_receipts_hash": requirement_upstream_hash(result),
    }
    return receipts, errors, context


def selected_inputs(root: Path, args) -> tuple[list[dict], list[str], dict]:
    if args.origin_mode == "manual":
        if args.requirement:
            return [], ["manual mode cannot receive --requirement"], {}
        receipts, errors = manual_inputs(root, args)
        return receipts, errors, {}
    return requirement_inputs(root, args.requirement)


def process_from_inputs(root: Path, value: str, receipts: list[dict], *, require_committed: bool) -> tuple[str | None, list[str]]:
    ba = next((row for row in receipts if row.get("stage") == "business-analysis"), None)
    if ba is None:
        return None, ["Business Analysis input is required before selecting a primary process"]
    return stage_package.resolve_ba_process(
        root.parent, value, expected_ba_ref=str(ba["result_ref"]),
        expected_ba_hash=str(ba["package_hash"]), require_committed=require_committed,
        require_strict_current=True,
    )


def init(args) -> int:
    root = root_for(args.root)
    if not valid_experience_slug(args.experience): return fail("experience must be lower kebab-case and must not use exp-", 2)
    if resolve_package(root, args.experience) or (root / "experiences" / args.experience).exists(): return fail("Experience slug or retired alias is reserved", 2)
    receipts, problems, context = selected_inputs(root, args)
    if problems: return fail("; ".join(problems), 2)
    try:
        plan = load_scope_plan(args.scope_plan, args.proposal_hash)
        findings = verify_scope_inputs(root, plan, require_committed=True)
        if findings:
            raise ValueError("; ".join(findings))
        primary, process_errors = process_from_inputs(root, args.primary_process_ref, receipts,
                                                       require_committed=True)
        if process_errors or primary is None:
            raise ValueError("; ".join(process_errors))
        action_for_plan(root, plan, action="create", experience=args.experience,
                        process=primary)
    except ValueError as exc:
        return fail(str(exc), 2)
    if any(fields(item).get("primary_process_ref") == primary and fields(item).get("status") != "retired" for item in packages(root)): return fail("an active Experience already owns this primary process", 2)
    package = root / "experiences" / args.experience
    data = {"type": "experience", "experience_id": args.experience, "origin_mode": args.origin_mode, "status": "draft", "revision": 1, "primary_process_ref": primary, "input_bindings": binding_rows(receipts)}
    if args.related_process_ref: data["related_process_refs"] = args.related_process_ref
    if args.origin_mode == "requirement":
        data["implements"] = [args.requirement]
        data["upstream_stage_receipts_hash"] = context["upstream_stage_receipts_hash"]
    write(package / "experience.md", data, args.title or f"{args.experience.replace('-', ' ').title()} Experience", "Living process-owned Experience package.", "maps/experience-design")
    for name in [entry[0] for entry in KIND.values()] + ["artifacts", GENERATED, LEDGER]: (package / name).mkdir(parents=True, exist_ok=True)
    print(json.dumps({"experience": args.experience, "path": str(package), "origin_mode": args.origin_mode}, indent=2)); return 0


def propose(args) -> int:
    root = root_for(args.root)
    receipts, problems, context = selected_inputs(root, args)
    if problems:
        return fail("; ".join(problems), 2)
    if len(args.process_ref) > 1 and args.experience:
        return fail("a multi-process create derives each Experience slug from its process; omit --experience", 2)
    actions = []
    for raw_process in args.process_ref:
        process, process_errors = process_from_inputs(root, raw_process, receipts,
                                                      require_committed=True)
        if process_errors or process is None:
            return fail("; ".join(process_errors), 2)
        current = [item for item in packages(root)
                   if fields(item).get("primary_process_ref") == process
                   and fields(item).get("status") != "retired"]
        package = current[0] if current else None
        action = args.action or ("update" if package else "create")
        if action == "create":
            experience = args.experience or process.rsplit("/", 1)[-1].removesuffix("-process")
        elif package is not None:
            experience = package.name
        else:
            return fail(f"{action} needs an active Experience for {process}", 2)
        if action == "rename" and not valid_experience_slug(args.to):
            return fail("rename scope proposal needs --to with a non-exp lower-kebab slug", 2)
        expected = ({} if package is None else {
            "status": fields(package).get("status"),
            "revision": fields(package).get("revision"),
            "source_hash": source_digest(package),
        })
        actions.append({
            "primary_process_ref": process,
            "experience": experience,
            "target_experience": args.to if action == "rename" else "",
            "action": action,
            "affected_records": package_record_ids(package) if package else [],
            "expected_package": expected,
            "reason": args.reason or "Owner confirmation required.",
        })
    plan = {
        "schema_version": 1,
        "origin_mode": args.origin_mode,
        "input_bindings": binding_rows(receipts),
        "actions": actions,
        **context,
    }
    plan["proposal_hash"] = proposal_digest(plan)
    print(json.dumps(plan, indent=2, ensure_ascii=False, sort_keys=True)); return 0


def begin_revision(args) -> int:
    package = package_for(args.experience_root); data, body = fm(package / "experience.md")
    registry, problems = compile_package(package, True)
    if problems: return print_problems(problems, False)
    try:
        plan = load_scope_plan(args.scope_plan, args.proposal_hash)
        findings = verify_scope_inputs(package.parent.parent, plan, require_committed=True)
        if findings:
            raise ValueError("; ".join(findings))
        action_for_plan(package.parent.parent, plan, action="update", experience=package.name,
                        process=str(data.get("primary_process_ref", "")))
    except ValueError as exc:
        return fail(str(exc), 2)
    history = [row for row in ledger(package) if int(row.get("package_revision", 0) or 0) != int(registry["package_revision"])] + [registry]
    write_ledger(package, history)
    for row in registry["records"]:
        path = package / LEDGER / "records" / str(row["id"]) / f"r{row['revision']}.json"; path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists(): path.write_bytes(canonical(row))
    data["status"], data["revision"] = "draft", int(data.get("revision", 1) or 1) + 1
    for key in ("approval_revision", "registry_hash", "package_hash", "source_hash", "approved_at_utc"): data.pop(key, None)
    status_tags(data); rewrite(package / "experience.md", data, body); return 0


def enter_review(args) -> int:
    package = package_for(args.experience_root); data, body = fm(package / "experience.md")
    if data.get("status") != "draft": return fail("only draft Experience may enter review", 2)
    registry, problems = compile_package(package)
    if [item for item in problems if "registry is stale" not in item]: return print_problems(problems, False)
    data["status"] = "in_review"; status_tags(data); rewrite(package / "experience.md", data, body)
    return render(argparse.Namespace(experience_root=str(package)))


def stub(args) -> int:
    package = package_for(args.experience_root)
    if fields(package).get("status") not in {"draft", "in_review"}: return fail("approved Experience is immutable; begin revision first", 2)
    directory, prefix, suffix = KIND[args.kind]
    if not SLUG.fullmatch(args.slug) or not re.fullmatch(prefix + r"-[0-9]{3,}", args.id): return fail("invalid record slug or ID", 2)
    data = {"type": args.kind, "id": args.id, "revision": args.revision, "record_state": args.record_state, "derives_from": args.derives_from or [str(fields(package).get("primary_process_ref", ""))]}
    if args.criterion_ref: data["criterion_refs"] = args.criterion_ref; data["satisfies"] = args.criterion_ref
    for key in ("uses_design", "constrained_by", "journey_refs", "flow_refs", "screen_refs", "state_refs", "transition_refs", "related_to"):
        value = getattr(args, key)
        if value: data[key] = value
    if args.supersedes: data["supersedes"] = args.supersedes
    write(package / directory / f"{args.slug}{suffix}", data, args.title or args.id, "Define the bounded experience behavior and exact references.", f"experience-design/experiences/{package.name}/experience")
    return 0


def init_artifact(args) -> int:
    package = package_for(args.experience_root)
    if not SLUG.fullmatch(args.package): return fail("artifact package must be lower kebab-case", 2)
    html, manifest = package / "artifacts" / f"{args.package}-preview.html", package / "artifacts" / f"{args.package}-artifact.md"
    if html.exists() or manifest.exists(): return fail("artifact package already exists", 2)
    html.parent.mkdir(parents=True, exist_ok=True)
    html.write_text(f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8">\n<meta name="experience-id" content="{package.name}">\n<meta name="experience-registry-hash" content="draft">\n<title>{args.title or args.package.title()}</title></head><body></body></html>\n', encoding="utf-8")
    write(manifest, {"type": "artifact-manifest", "artifact_path": html.relative_to(package).as_posix(), "declared_ids": []}, args.title or f"{args.package.title()} Artifact", f"[Open preview]({html.name})", f"experience-design/experiences/{package.name}/experience")
    return 0


def render(args) -> int:
    package = package_for(args.experience_root); registry, problems = compile_package(package)
    if [item for item in problems if "registry is stale" not in item]: return print_problems(problems, False)
    generated = package / GENERATED; generated.mkdir(exist_ok=True)
    (generated / "registry.json").write_bytes(canonical(registry))
    (generated / "coverage.json").write_bytes(canonical({"experience_id": package.name, "active_records": [item["id"] for item in registry["records"] if item.get("record_state") == "active"]}))
    (generated / "artifact-registry.json").write_bytes(canonical({"artifacts": registry["artifacts"]}))
    for item in registry["artifacts"]:
        html = package / item["path"]; text = html.read_text(encoding="utf-8")
        html.write_text(re.sub(r'(<meta\s+name=["\']experience-registry-hash["\']\s+content=["\'])[^"\']*(["\'])', rf"\1{registry['registry_hash']}\2", text, flags=re.I), encoding="utf-8")
        manifest = package / item["manifest"]; data, body = fm(manifest); data.update({"artifact_sha256": item["sha256"], "registry_hash": registry["registry_hash"], "manifest_hash": sha(canonical(item))}); rewrite(manifest, data, body)
    render_experience_navigation(package.parent.parent)
    return 0


def check(args) -> int:
    registry, problems = compile_package(package_for(args.experience_root), args.gate)
    if args.json and not problems: print(json.dumps({"ok": True, "experience_id": registry.get("experience_id"), "registry_hash": registry.get("registry_hash"), "findings": []}, indent=2)); return 0
    return print_problems(problems, args.json)


def approve_set(args) -> int:
    root = root_for(args.root)
    selected = [resolve_package(root, item) for item in args.experience]
    if (not selected or any(item is None for item in selected)
            or len(set(selected)) != len(selected)):
        return fail("approve-set needs unique resolvable Experience packages", 2)
    try:
        plan = load_scope_plan(args.scope_plan, args.proposal_hash)
        findings = verify_scope_inputs(root, plan, require_committed=True)
        if findings:
            raise ValueError("; ".join(findings))
        expected = {
            str(row.get("target_experience") or row.get("experience"))
            for row in plan["actions"] if isinstance(row, dict)
            and row.get("action") in {"create", "update", "rename"}
        }
        actual = {item.name for item in selected if item is not None}
        if actual != expected:
            raise ValueError("approve-set packages must exactly match the approved scope-plan action set")
        for package in selected:
            data = fields(package)
            action = next((row for row in plan["actions"] if isinstance(row, dict)
                           and row.get("primary_process_ref") == data.get("primary_process_ref")
                           and str(row.get("target_experience") or row.get("experience")) == package.name), None)
            if action is None:
                raise ValueError(f"{package.name} is not in the approved scope plan")
    except ValueError as exc:
        return fail(str(exc), 2)
    # Rendering changes manifests, HTML metadata, registries and the map before
    # a package receives its approval stamp. Keep one filesystem transaction so
    # a failed member cannot leave a partially prepared multi-package set.
    map_path = root.parent / "maps" / "experience-design.md"
    original_map = map_path.read_bytes() if map_path.is_file() else None
    with tempfile.TemporaryDirectory(prefix="experience-approve-") as raw:
        backup_root = Path(raw)
        backups: dict[Path, Path] = {}
        for package in selected:
            backup = backup_root / package.name
            shutil.copytree(package, backup)
            backups[package] = backup
        prepared = []
        try:
            for package in selected:
                data, body = fm(package / "experience.md")
                if data.get("status") != "in_review":
                    raise ValueError(f"{package.name} must be in_review")
                if render(argparse.Namespace(experience_root=str(package))):
                    raise ValueError(f"cannot render {package.name}")
                registry, problems = compile_package(package)
                hard = [item for item in problems if "registry is stale" not in item]
                if hard:
                    raise ValueError("; ".join(hard))
                previous = ledger(package)
                if (previous and max(previous, key=lambda row: int(row.get("package_revision", 0) or 0)).get("source_hash")
                        == registry["source_hash"]):
                    raise ValueError(f"{package.name} has no record or input delta")
                prepared.append((package, data, body, registry))
            for package, data, body, registry in prepared:
                data.update({
                    "status": "approved",
                    "approval_revision": int(data.get("approval_revision", 0) or 0) + 1,
                    "registry_hash": registry["registry_hash"],
                    "package_hash": registry["package_hash"],
                    "source_hash": registry["source_hash"],
                    "approved_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                })
                status_tags(data)
                rewrite(package / "experience.md", data, body)
                if render(argparse.Namespace(experience_root=str(package))):
                    raise ValueError("cannot render approved Experience package")
                _registry, problems = compile_package(package, True)
                if problems:
                    raise ValueError("; ".join(problems))
        except Exception as exc:
            for package, backup in backups.items():
                if package.exists():
                    shutil.rmtree(package)
                shutil.copytree(backup, package)
            if original_map is None:
                map_path.unlink(missing_ok=True)
            else:
                map_path.parent.mkdir(parents=True, exist_ok=True)
                map_path.write_bytes(original_map)
            return fail(f"approve-set rolled back: {exc}")
    print(json.dumps({"receipts": [package_receipt(package, registry)
                                    for package, _data, _body, registry in prepared]}, indent=2))
    return 0


def rename(args) -> int:
    package = package_for(args.experience_root); root = package.parent.parent
    if not valid_experience_slug(args.to) or resolve_package(root, args.to) or (package.parent / args.to).exists(): return fail("new Experience slug is invalid or reserved", 2)
    data, body = fm(package / "experience.md")
    if data.get("status") not in {"draft", "in_review"}: return fail("rename requires a draft or in_review revision", 2)
    try:
        plan = load_scope_plan(args.scope_plan, args.proposal_hash)
        findings = verify_scope_inputs(root, plan, require_committed=True)
        if findings:
            raise ValueError("; ".join(findings))
        action_for_plan(root, plan, action="rename", experience=package.name,
                        target=args.to, process=str(data.get("primary_process_ref", "")))
    except ValueError as exc:
        return fail(str(exc), 2)
    old, destination = package.name, package.parent / args.to
    map_path = root.parent / "maps" / "experience-design.md"
    original_map = map_path.read_bytes() if map_path.is_file() else None
    with tempfile.TemporaryDirectory(prefix="experience-rename-") as raw:
        backup = Path(raw) / old
        shutil.copytree(package, backup)
        try:
            os.replace(package, destination)
            # Rewrite only live graph fields.  ``supersedes`` and ledger
            # snapshots identify history and must keep the old exact ref.
            for path in authored(destination):
                current_data, current_body = fm(path)
                changed = False
                for field in ("journey_refs", "flow_refs", "screen_refs", "state_refs", "transition_refs", "related_to"):
                    values = list_value(current_data, field)
                    rewritten = [value.replace(f"{old}:", f"{args.to}:", 1)
                                 if value.startswith(f"{old}:") else value
                                 for value in values]
                    if rewritten != values:
                        current_data[field] = rewritten
                        changed = True
                if changed:
                    rewrite(path, current_data, current_body)
            data["experience_id"] = args.to
            data["aliases"] = sorted(set(list_value(data, "aliases") + [old]))
            rewrite(destination / "experience.md", data, body)
            target = destination / LEDGER / "aliases.json"
            aliases = {}
            if target.is_file():
                try:
                    aliases = json.loads(target.read_text(encoding="utf-8")).get("aliases", {})
                except json.JSONDecodeError:
                    aliases = {}
            aliases[old] = args.to
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(canonical({"aliases": aliases}))
            render_experience_navigation(root)
            _registry, problems = compile_package(destination)
            if [item for item in problems if "registry is stale" not in item]:
                raise ValueError("; ".join(problems))
        except Exception:
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(backup, package)
            if original_map is None:
                map_path.unlink(missing_ok=True)
            else:
                map_path.parent.mkdir(parents=True, exist_ok=True)
                map_path.write_bytes(original_map)
            raise
    print(json.dumps({"from": old, "to": args.to}, indent=2)); return 0


def retire(args) -> int:
    package = package_for(args.experience_root)
    data, body = fm(package / "experience.md")
    if data.get("status") != "approved":
        return fail("only an approved Experience may retire", 2)
    try:
        plan = load_scope_plan(args.scope_plan, args.proposal_hash)
        findings = verify_scope_inputs(package.parent.parent, plan, require_committed=True)
        if findings:
            raise ValueError("; ".join(findings))
        action_for_plan(package.parent.parent, plan, action="retire", experience=package.name,
                        process=str(data.get("primary_process_ref", "")))
    except ValueError as exc:
        return fail(str(exc), 2)
    registry, problems = compile_package(package, True)
    if problems:
        return print_problems(problems, False)
    history = ledger(package)
    if not any(int(row.get("package_revision", 0) or 0) == int(registry["package_revision"]) for row in history):
        write_ledger(package, [*history, registry])
    data["status"] = "retired"
    data["revision"] = int(data.get("revision", 1) or 1) + 1
    for key in ("approval_revision", "registry_hash", "package_hash", "source_hash", "approved_at_utc"):
        data.pop(key, None)
    data["retired_at_utc"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    status_tags(data)
    rewrite(package / "experience.md", data, body)
    render_experience_navigation(package.parent.parent)
    print(json.dumps({"experience": package.name, "status": "retired"}, indent=2))
    return 0


def resolve(args) -> int:
    root = root_for(args.root); match = EXACT.fullmatch(args.ref)
    if match:
        package = resolve_package(root, match.group(1)); ident, revision = args.ref.split(":", 1)[1].split("@", 1)[0], int(match.group(3))
        if package:
            registry, problems = compile_package(package)
            current = next((row for row in registry.get("records", []) if row.get("id") == ident and row.get("revision") == revision), None) if not [item for item in problems if "registry is stale" not in item] else None
            if current: print(json.dumps(current, indent=2)); return 0
            historic = snapshots(package, ident, revision)
            if historic: print(json.dumps(historic, indent=2)); return 0
    package_match = PACKAGE.fullmatch(args.ref)
    if package_match:
        package = resolve_package(root, package_match.group(1))
        if package:
            registry, problems = compile_package(package)
            if not problems and int(registry["package_revision"]) == int(package_match.group(2)): print(json.dumps(package_receipt(package, registry), indent=2)); return 0
            for row in ledger(package):
                if int(row.get("package_revision", 0) or 0) == int(package_match.group(2)): print(json.dumps(row, indent=2)); return 0
    return fail("reference is not resolvable", 1)


def status(args) -> int:
    package = package_for(args.experience_root); registry, problems = compile_package(package, True)
    if problems: return print_problems(problems, True)
    print(json.dumps(package_receipt(package, registry), indent=2)); return 0


def render_experience_navigation(root: Path) -> None:
    experience_root = root_for(root)
    experience_root.mkdir(parents=True, exist_ok=True)
    map_path = experience_root.parent / "maps" / "experience-design.md"
    if not map_path.is_file():
        return
    marker = "<!-- experience_compile.py: generated packages -->"
    retained = map_path.read_text(encoding="utf-8").split(marker, 1)[0].rstrip()
    rows = ["", marker, ""]
    for package in packages(experience_root):
        rows.append(f"- [[experience-design/experiences/{package.name}/experience|{package.name}]] — `{fields(package).get('status', 'draft')}`")
    map_path.write_text("\n".join([retained, *rows]).rstrip() + "\n", encoding="utf-8")


def reconcile_vault_navigation(root: Path) -> None:
    render_experience_navigation(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__); sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("propose"); p.add_argument("--root", required=True); p.add_argument("--process-ref", action="append", required=True); p.add_argument("--experience", default=""); p.add_argument("--to", default=""); p.add_argument("--action", choices=("create", "update", "reuse", "rename", "retire"), default=""); p.add_argument("--reason", default=""); p.add_argument("--origin-mode", choices=("manual", "requirement"), required=True); p.add_argument("--requirement", default=""); p.add_argument("--ba-ref", action="append", default=[]); p.add_argument("--solution-ref", action="append", default=[]); p.add_argument("--design-ref", action="append", default=[]); p.set_defaults(func=propose)
    p = sub.add_parser("init"); p.add_argument("--root", required=True); p.add_argument("--experience", required=True); p.add_argument("--origin-mode", choices=("manual", "requirement"), required=True); p.add_argument("--primary-process-ref", required=True); p.add_argument("--related-process-ref", action="append", default=[]); p.add_argument("--requirement", default=""); p.add_argument("--scope-plan", required=True); p.add_argument("--proposal-hash", required=True); p.add_argument("--title", default=""); p.add_argument("--ba-ref", action="append", default=[]); p.add_argument("--solution-ref", action="append", default=[]); p.add_argument("--design-ref", action="append", default=[]); p.set_defaults(func=init)
    for name, handler in (("begin-revision", begin_revision), ("enter-review", enter_review), ("render", render), ("check", check), ("status", status), ("rename", rename), ("retire", retire)):
        p = sub.add_parser(name); p.add_argument("--experience-root", required=True)
        if name == "check": p.add_argument("--gate", action="store_true"); p.add_argument("--json", action="store_true")
        if name == "rename": p.add_argument("--to", required=True)
        if name in {"begin-revision", "rename", "retire"}:
            p.add_argument("--scope-plan", required=True); p.add_argument("--proposal-hash", required=True)
        p.set_defaults(func=handler)
    p = sub.add_parser("stub"); p.add_argument("--experience-root", required=True); p.add_argument("--kind", choices=sorted(KIND), required=True); p.add_argument("--id", required=True); p.add_argument("--slug", required=True); p.add_argument("--title", default=""); p.add_argument("--revision", type=int, default=1); p.add_argument("--record-state", choices=("active", "retired"), default="active"); p.add_argument("--derives-from", action="append", default=[]); p.add_argument("--criterion-ref", action="append", default=[]); p.add_argument("--supersedes", default="")
    for key in ("uses_design", "constrained_by", "journey_refs", "flow_refs", "screen_refs", "state_refs", "transition_refs", "related_to"): p.add_argument("--" + key.replace("_", "-"), action="append", default=[])
    p.set_defaults(func=stub)
    p = sub.add_parser("init-artifact"); p.add_argument("--experience-root", required=True); p.add_argument("--package", required=True); p.add_argument("--title", default=""); p.set_defaults(func=init_artifact)
    p = sub.add_parser("approve-set"); p.add_argument("--root", required=True); p.add_argument("--experience", action="append", required=True); p.add_argument("--scope-plan", required=True); p.add_argument("--proposal-hash", required=True); p.set_defaults(func=approve_set)
    p = sub.add_parser("resolve"); p.add_argument("--root", required=True); p.add_argument("--ref", required=True); p.set_defaults(func=resolve)
    args = parser.parse_args(argv)
    try: return args.func(args)
    except (OSError, ValueError) as exc: return fail(str(exc), 2)


if __name__ == "__main__":
    raise SystemExit(main())
