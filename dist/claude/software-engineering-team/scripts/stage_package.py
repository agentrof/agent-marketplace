#!/usr/bin/env python3
"""Shared product-stage package resolver.

Every stage handoff uses this module instead of trusting a neighbouring
document's ``status`` field.  It resolves only canonical package roots and
checks their lifecycle stamp, generated view and package hash.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import unicodedata
from pathlib import Path

from ba_compile import parse_frontmatter, without_generated_relations


STAGES = {
    "business-analysis": "business-analysis-package",
    "solution-design": "solution-design-package",
    "design-system": "design-system-package",
    "experience-design": "experience-package",
    "backlog-plan": "backlog-package",
}

# BA owns the process topology (including nested domain chains). This module
# only identifies the containing space and asks ba_compile for classification.
BA_PROCESS_REF = "business-analysis/{space}/(domains/<domain>/)*/processes/<slug>-process"


def _reserved_path_alias(name: str, expected: str) -> bool:
    return unicodedata.normalize("NFC", name).casefold() == expected.casefold()


def _regular_stage_directory(
    path: Path, label: str, *, reserved_name: str | None = None,
) -> None:
    if reserved_name is not None:
        if (
            path.name != reserved_name
            or unicodedata.normalize("NFC", path.name) != reserved_name
        ):
            raise ValueError(
                f"{label} must use exact NFC spelling and case: {reserved_name}"
            )
        if path.parent.is_dir() and not path.parent.is_symlink():
            aliases = [
                child.name for child in path.parent.iterdir()
                if _reserved_path_alias(child.name, reserved_name)
            ]
            if aliases != [reserved_name]:
                raise ValueError(
                    f"{label} must resolve from one exact {reserved_name} directory"
                )
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{label} must be one regular, non-symlink directory")


def _reject_resolved_reserved_aliases(path: Path) -> None:
    """Recover owner aliases erased by an upstream ``Path.resolve()`` call."""
    for candidate in (path, *path.parents):
        for reserved_name in ("workspace", "docs", "experience-design"):
            alias = candidate.parent / reserved_name
            if alias == candidate or not alias.is_symlink():
                continue
            try:
                if alias.resolve(strict=False) == candidate.resolve(strict=False):
                    raise ValueError(
                        "stage selector was resolved through a symlinked "
                        f"{reserved_name} owner"
                    )
            except OSError as exc:
                raise ValueError(
                    "stage selector owner identity could not be verified"
                ) from exc


def docs_root(value: str | Path) -> Path:
    """Resolve a docs selector only after lexical owner validation."""
    raw = Path(value).expanduser()
    lexical = Path(os.path.abspath(os.fspath(raw)))
    _reject_resolved_reserved_aliases(lexical)
    name = unicodedata.normalize("NFC", lexical.name).casefold()
    if name == "docs":
        docs = lexical
        chain = [(docs, "stage docs root", "docs")]
        workspace = docs.parent
        if _reserved_path_alias(workspace.name, "workspace"):
            chain.insert(0, (workspace, "stage workspace", "workspace"))
    elif name == "workspace":
        docs = lexical / "docs"
        chain = [
            (lexical, "stage workspace", "workspace"),
            (docs, "stage docs root", "docs"),
        ]
    else:
        _regular_stage_directory(lexical, "stage project selector")
        workspace = lexical / "workspace"
        direct = lexical / "docs"
        if workspace.exists() or workspace.is_symlink():
            docs = workspace / "docs"
            chain = [
                (workspace, "stage workspace", "workspace"),
                (docs, "stage docs root", "docs"),
            ]
        elif direct.exists() or direct.is_symlink():
            docs = direct
            chain = [(docs, "stage docs root", "docs")]
        else:
            return lexical.resolve()
    for path, label, reserved_name in chain:
        _regular_stage_directory(path, label, reserved_name=reserved_name)
    return docs.resolve()


def frontmatter(path: Path) -> dict:
    try:
        props, _line, error = parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    return props if not error else {}


def tree_hash(root: Path, omitted_fields: set[str]) -> str:
    """Hash authored source plus generated registry JSON deterministically.

    The registry is a package artefact for BA, so its absence or drift must
    change the receipt.  Other generated JSON stays excluded deliberately:
    it is not part of a stage's published source contract.
    """
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.md")):
        if path.is_symlink():
            raise ValueError(f"symlinked package source: {path}")
        text = path.read_text(encoding="utf-8")
        props, body_line, error = parse_frontmatter(text)
        if not error and props:
            lines = text.splitlines()
            kept = ["---"]
            for raw in lines[1:body_line - 2]:
                if raw.partition(":")[0].strip() not in omitted_fields:
                    kept.append(raw)
            kept.extend(lines[body_line - 1:])
            text = "\n".join(kept).rstrip() + "\n"
        text = without_generated_relations(text)
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(text.encode())
        digest.update(b"\0")
    registry = root / "_generated" / "registry.json"
    if registry.is_file():
        digest.update(registry.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(registry.read_bytes())
        digest.update(b"\0")
    capability = root / "_generated" / "capability-registry.json"
    if capability.is_file():
        digest.update(capability.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(capability.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def is_committed(package_root: Path) -> bool:
    """Check the whole published package, not only its overview note."""
    root = next((p for p in (package_root, *package_root.parents)
                 if (p / ".git").exists()), package_root.parent)
    result = subprocess.run(["git", "status", "--porcelain=v1", "--",
                             str(package_root)], cwd=root, capture_output=True,
                            text=True, check=False)
    return result.returncode == 0 and not result.stdout.strip()


def paths_are_committed(paths: list[Path]) -> bool:
    if not paths:
        return False
    root = next(
        (parent for path in paths for parent in (path.parent, *path.parents)
         if (parent / ".git").exists()),
        paths[0].parent,
    )
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--", *map(str, paths)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and not result.stdout.strip()


def receipt(stage: str, ref: str, result_type: str, package_hash: str,
            approved: bool, current: bool, path: Path, package_root: Path,
            verification_profile: str = "invalid") -> dict:
    return {
        "stage": stage, "result_ref": ref, "result_type": result_type,
        "package_hash": package_hash, "status": "approved" if approved else "draft",
        "current": current, "committed": is_committed(package_root),
        "path": str(path), "verification_profile": verification_profile,
        "legacy": verification_profile == "legacy-readonly",
    }


def ba_candidates(docs: Path) -> list[dict]:
    root = docs / "business-analysis"
    found = []
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        note = folder / "space.md"
        if not folder.is_dir() or not note.is_file():
            continue
        try:
            import ba_compile
            schema = ba_compile.load_schema(ba_compile.DEFAULT_SCHEMA)
            classification = ba_compile.classify_package(folder, schema, docs)
            digest = classification["package_hash"]
            profile = classification["profile"]
            current = classification["current"]
        except (ImportError, OSError, ValueError):
            digest = tree_hash(folder, {"package_hash", "package_status", "package_approved_at_utc",
                                        "package_contract_version"})
            props = frontmatter(note)
            authored = [path for path in folder.rglob("*.md")
                        if "_generated" not in path.parts]
            try:
                version = int(props.get("package_contract_version", 0) or 0)
            except (TypeError, ValueError):
                version = 0
            legacy = ((not props.get("package_status") and bool(authored) and all(
                frontmatter(path).get("status") in {"approved", "superseded"}
                for path in authored))
                or (props.get("package_status") == "approved"
                    and props.get("package_hash") == digest and version < 2))
            profile = "legacy-readonly" if legacy else "invalid"
            current = legacy
        found.append(receipt("business-analysis", f"business-analysis/{folder.name}/space",
                             "business-analysis-package", digest, current, current,
                             note, folder, profile))
    return found


def solution_candidates(docs: Path) -> list[dict]:
    root = docs / "solution-design"
    note = root / "landscape.md"
    if not note.is_file():
        return []
    props = frontmatter(note)
    compiler_ok = False
    try:
        import landscape_check
        digest = landscape_check.package_hash(root)
        # The stage resolver must consume the Solution compiler's current
        # verdict, not merely a matching frontmatter hash.
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            compiler_ok = landscape_check.main(["check", "--tree", str(root)]) == 0
    except (ImportError, OSError, ValueError):
        digest = tree_hash(root, {"package_hash", "package_status", "package_approved_at_utc"})
    engagements = list((root / "engagements").glob("*.md")) if (root / "engagements").is_dir() else []
    # Strict handoffs require the v3 allocation-universe contract. Earlier
    # approved landscapes remain available only through legacy-readonly.
    modern = int(props.get("topology_contract_version", 0) or 0) >= 3
    legacy = ((not props.get("package_status") and bool(engagements) and all(
        "Status: open" not in p.read_text(encoding="utf-8") for p in engagements))
        or (props.get("package_status") == "approved"
            and props.get("package_hash") == digest and not modern))
    # Pre-topology-contract approved packages remain inspectable and reusable
    # until their first revision. New topology packages must pass the complete
    # compiler check, not merely present a matching package hash.
    strict_current = (props.get("package_status") == "approved"
                      and props.get("package_hash") == digest
                      and modern and compiler_ok)
    current = strict_current or legacy
    return [receipt("solution-design", "solution-design/landscape", "solution-design-package",
                    digest, current or legacy, current or legacy, note, root,
                    "strict-current" if strict_current else "legacy-readonly" if legacy else "invalid")]


def design_candidates(docs: Path) -> list[dict]:
    root = docs / "design-system"
    note = root / "MASTER.md"
    if not note.is_file():
        return []
    props = frontmatter(note)
    design_system_compile = None
    try:
        import design_system_compile
        digest = design_system_compile.baseline_hash(root)
    except (ImportError, OSError, ValueError):
        digest = tree_hash(root, {"baseline_hash", "approved_at_utc"})
    basic = (props.get("status") == "approved"
             and props.get("baseline_hash") == digest)
    modern = bool(design_system_compile and (props.get("contract_version")
                  or design_system_compile.relation_values(note, "derives_from")
                  or design_system_compile.relation_values(note, "constrained_by")))
    semantic_ok = False
    if basic and modern:
        try:
            semantic_ok = not (design_system_compile.findings(root)
                               + design_system_compile.semantic_findings(root))
        except (AttributeError, OSError, ValueError):
            semantic_ok = False
    approved = basic and (semantic_ok or not modern)
    return [receipt("design-system", "design-system/MASTER", "design-system-package",
                    digest, approved, approved, note, root,
                    "strict-current" if basic and modern and semantic_ok
                    else "legacy-readonly" if basic and not modern else "invalid")]


def experience_candidates(docs: Path) -> list[dict]:
    experience_root = docs / "experience-design"
    root = experience_root / "experiences"
    try:
        import experience_application_check
        application, application_problems = (
            experience_application_check.compile_application(experience_root, True)
        )
    except (ImportError, OSError, ValueError):
        return []
    if application_problems or not application:
        return []
    application_path = experience_root / experience_application_check.REGISTRY_RELATIVE
    application_receipt = receipt(
        "experience-design",
        f"application@r{application['application_revision']}",
        "experience-application",
        str(application["application_hash"]),
        True,
        True,
        application_path,
        application_path.parent,
        "strict-current",
    )
    application_receipt["committed"] = paths_are_committed(
        experience_application_check.artifact_snapshot_paths(
            experience_root, application,
        )
    )
    found = [application_receipt]
    for folder in sorted(root.iterdir()) if root.is_dir() else []:
        note = folder / "experience.md"
        if not folder.is_dir() or not note.is_file():
            continue
        try:
            import experience_compile
            compiled, problems = experience_compile.compile_package(folder, True)
            props = frontmatter(note)
            package_hash = str(compiled.get("package_hash", ""))
            revision = props.get("revision")
            approved = (
                props.get("status") == "approved"
                and not problems
                and type(revision) is int
                and revision > 0
            )
        except (ImportError, OSError, ValueError):
            props = frontmatter(note)
            package_hash = ""
            revision = props.get("revision")
            approved = False
        if approved:
            found.append(receipt("experience-design",
                                 f"{folder.name}@r{revision}",
                                 "experience-package", package_hash,
                                 approved, approved, note, folder,
                                 "strict-current" if approved else "invalid"))
    return found


def verified_application_rows(experience_root: Path) -> list[dict]:
    """Load the immutable application publication ledger fail-closed."""
    import experience_application_check
    rows, findings = experience_application_check.verified_application_ledger(
        experience_root,
    )
    if findings:
        return []
    published: dict[str, str] = {}
    for application in rows:
        for package in application.get("packages", []):
            ref = package.get("result_ref") if isinstance(package, dict) else None
            package_hash = (
                package.get("package_hash") if isinstance(package, dict) else None
            )
            if not isinstance(ref, str) or not isinstance(package_hash, str):
                return []
            if ref in published and published[ref] != package_hash:
                return []
            published[ref] = package_hash
    return rows


def historical_application_row(
    experience_root: Path, ref: str,
) -> dict | None:
    match = re.fullmatch(r"application@r([1-9][0-9]*)", ref)
    if match is None:
        return None
    revision = int(match.group(1))
    return next(
        (
            row for row in verified_application_rows(experience_root)
            if row.get("application_revision") == revision
        ),
        None,
    )


def historical_process_evidence(
    experience_root: Path, ref: str, expected_hash: str,
) -> bool:
    """Verify one published process receipt without current-upstream gates."""
    match = re.fullmatch(
        r"((?!application$)[a-z0-9]+(?:-[a-z0-9]+)*)@r([1-9][0-9]*)",
        ref,
    )
    if match is None:
        return False
    experience_id, revision_text = match.groups()
    revision = int(revision_text)
    try:
        import experience_compile
        package = experience_compile.resolve_package(
            experience_root, experience_id,
        )
        if package is None or not historical_package_tree_is_regular(package):
            return False
        props = experience_compile.fields(package)
        current_revision = props.get("revision")
        if type(current_revision) is not int or current_revision < 1:
            return False
        history, findings = experience_compile.validate_process_ledger(
            package, current_revision,
        )
        if findings:
            return False
        historic = next(
            (
                row for row in history
                if row.get("experience_id") == experience_id
                and row.get("package_revision") == revision
            ),
            None,
        )
        if historic is not None:
            return historic.get("package_hash") == expected_hash
        if (
            props.get("status") != "approved"
            or current_revision != revision
            or experience_id not in experience_compile.package_aliases(package)
        ):
            return False
        current, current_findings = experience_compile.compile_package(
            package, True, allow_stale_inputs=True,
        )
        return (
            not current_findings
            and current.get("package_hash") == expected_hash
        )
    except (ImportError, OSError, TypeError, ValueError):
        return False


def historical_package_tree_is_regular(package: Path) -> bool:
    """Reject aliases, special files and shared inodes in historical evidence."""
    try:
        root_stat = os.lstat(package)
        if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
            return False
        for path in package.rglob("*"):
            path_stat = os.lstat(path)
            if stat.S_ISLNK(path_stat.st_mode):
                return False
            if stat.S_ISDIR(path_stat.st_mode):
                continue
            if not stat.S_ISREG(path_stat.st_mode) or path_stat.st_nlink != 1:
                return False
    except OSError:
        return False
    return True


def historical_process_refs(
    experience_root: Path, application: dict,
) -> list[str] | None:
    packages = application.get("packages")
    if not isinstance(packages, list):
        return None
    refs: list[str] = []
    for package in packages:
        if not isinstance(package, dict):
            return None
        ref = package.get("result_ref")
        package_hash = package.get("package_hash")
        if (
            not isinstance(ref, str)
            or not isinstance(package_hash, str)
            or not historical_process_evidence(
                experience_root, ref, package_hash,
            )
        ):
            return None
        refs.append(ref)
    return refs if len(refs) == len(set(refs)) else None


def historical_experience_candidate(docs: Path, ref: str) -> dict | None:
    """Resolve one immutable Experience receipt after it stops being current."""
    try:
        docs = docs_root(docs)
    except (OSError, ValueError):
        return None
    experience_root = docs / "experience-design"
    application_match = re.fullmatch(r"application@r([1-9][0-9]*)", ref)
    if application_match:
        try:
            import experience_application_check
            historic = historical_application_row(experience_root, ref)
        except (ImportError, OSError, TypeError, ValueError):
            return None
        if (
            historic is None
            or historical_process_refs(experience_root, historic) is None
        ):
            return None
        candidate = receipt(
            "experience-design", ref, "experience-application",
            str(historic.get("application_hash", "")), True, False,
            experience_root / experience_application_check.LEDGER_RELATIVE,
            experience_root, "historical",
        )
        candidate["committed"] = paths_are_committed([
            experience_root / experience_application_check.LEDGER_RELATIVE,
        ])
        return candidate

    package_match = re.fullmatch(
        r"((?!application$)[a-z0-9]+(?:-[a-z0-9]+)*)@r([1-9][0-9]*)",
        ref,
    )
    if package_match is None:
        return None
    try:
        import experience_application_check
        matches = [
            package
            for application in verified_application_rows(experience_root)
            for package in application.get("packages", [])
            if isinstance(package, dict) and package.get("result_ref") == ref
        ]
    except (ImportError, OSError, TypeError, ValueError):
        return None
    hashes = {
        package.get("package_hash") for package in matches
        if isinstance(package.get("package_hash"), str)
    }
    if len(hashes) != 1:
        return None
    package_hash = next(iter(hashes))
    if not historical_process_evidence(experience_root, ref, package_hash):
        return None
    candidate = receipt(
        "experience-design", ref, "experience-package",
        package_hash, True, False,
        experience_root / experience_application_check.LEDGER_RELATIVE,
        experience_root, "historical",
    )
    candidate["committed"] = paths_are_committed([
        experience_root / experience_application_check.LEDGER_RELATIVE,
    ])
    return candidate


def experience_application_process_refs(
    docs: Path, ref: str, *, allow_historical: bool = False,
) -> list[str] | None:
    """Return the process receipt set owned by one verified application."""
    try:
        docs = docs_root(docs)
    except (OSError, ValueError):
        return None
    match = re.fullmatch(r"application@r([1-9][0-9]*)", ref)
    if match is None:
        return None
    experience_root = docs / "experience-design"
    try:
        import experience_application_check
        revision = int(match.group(1))
        if allow_historical:
            registry = historical_application_row(experience_root, ref)
            if registry is None:
                return None
            return historical_process_refs(experience_root, registry)
        else:
            current, findings = experience_application_check.compile_application(
                experience_root, True,
            )
            if findings or current.get("application_revision") != revision:
                return None
            registry = current
    except (ImportError, OSError, TypeError, ValueError):
        return None
    if not isinstance(registry, dict) or not isinstance(registry.get("packages"), list):
        return None
    refs = []
    for row in registry["packages"]:
        if not isinstance(row, dict) or not isinstance(row.get("result_ref"), str):
            return None
        refs.append(row["result_ref"])
    return refs if len(refs) == len(set(refs)) else None


def experience_application_is_empty(
    docs: Path, ref: str, *, allow_historical: bool = False,
) -> bool:
    """Prove that an exact application receipt owns zero process receipts."""
    return experience_application_process_refs(
        docs, ref, allow_historical=allow_historical,
    ) == []


def backlog_candidates(docs: Path) -> list[dict]:
    note = docs / "backlog" / "backlog.md"
    if not note.is_file():
        return []
    props = frontmatter(note)
    try:
        import backlog_compile
        record, errors = backlog_compile.collect(docs)
        errors.extend(backlog_compile.approval_findings(record, docs))
        digest = (str(record["backlog"]["props"].get("package_hash", ""))
                  if record.get("backlog") else "")
        approved = not errors and digest.startswith("sha256:")
    except (ImportError, OSError, RuntimeError, ValueError):
        digest = str(props.get("package_hash", ""))
        approved = False
    return [receipt("backlog-plan", "backlog/backlog", "backlog-package",
                    digest, approved, approved, note, docs / "backlog",
                    "strict-current" if approved else "invalid")]


def canonical_ba_process_ref(value: str) -> tuple[str, str, str] | None:
    """Normalize a BA reference without reimplementing BA's topology.

    The third value is the path relative to the BA space. Its classification
    is intentionally deferred to ``ba_compile.scan_space``.
    """
    raw = value.strip().replace("\\", "/")
    if raw.endswith(".md"):
        raw = raw[:-3]
    parts = raw.split("/")
    if (len(parts) < 3 or parts[0] != "business-analysis"
            or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", parts[1])
            or any(part in {"", ".", ".."} for part in parts)):
        return None
    space = parts[1]
    relative = "/".join(parts[2:])
    return f"business-analysis/{space}/{relative}", space, relative


def resolve_ba_process(docs: Path, value: str, *, expected_ba_ref: str = "",
                       expected_ba_hash: str = "",
                       require_committed: bool = False,
                       require_strict_current: bool = False) -> tuple[str | None, list[str]]:
    """Resolve a process through the exact BA package that contains it."""
    parsed = canonical_ba_process_ref(value)
    if parsed is None:
        return None, [
            "primary_process_ref must be a vault-relative Business Analysis process "
            "under business-analysis/<space>/(domains/<domain>/)*/processes/"
        ]
    canonical, space, relative = parsed
    expected = f"business-analysis/{space}/space"
    receipt_value, errors = verify(
        docs, "business-analysis", expected, expected_ba_hash,
        require_committed, require_strict_current,
    )
    if expected_ba_ref and expected_ba_ref != expected:
        errors.append("primary_process_ref must belong to the bound Business Analysis package")
    process = (docs / canonical).with_suffix(".md")
    if not process.is_file():
        errors.append("primary_process_ref does not resolve to a Business Analysis process note")
        return None, errors
    try:
        import ba_compile
        schema = ba_compile.load_schema(ba_compile.DEFAULT_SCHEMA)
        scanned, _base = ba_compile.scan_space(
            docs / "business-analysis" / space, schema)
        document = scanned.docs.get(f"{relative}.md")
    except (ImportError, OSError, ValueError):
        document = None
    if document is None or document.doc_type != "process":
        errors.append(
            "primary_process_ref must resolve through the BA topology to a process note")
    props = frontmatter(process)
    if props.get("type") != "process":
        errors.append("primary_process_ref must resolve to a process note")
    if receipt_value and receipt_value.get("verification_profile") == "strict-current" and props.get("status") != "approved":
        errors.append("primary_process_ref must resolve to an approved process in a current BA package")
    return canonical, errors


def candidates(docs: Path, stage: str) -> list[dict]:
    if stage not in STAGES:
        raise ValueError(f"unsupported stage: {stage}")
    docs = docs_root(docs)
    return {
        "business-analysis": ba_candidates,
        "solution-design": solution_candidates,
        "design-system": design_candidates,
        "experience-design": experience_candidates,
        "backlog-plan": backlog_candidates,
    }[stage](docs)


def verify(docs: Path, stage: str, ref: str, expected_hash: str = "",
           require_committed: bool = False,
           require_strict_current: bool = False,
           allow_historical: bool = False) -> tuple[dict | None, list[str]]:
    docs = docs_root(docs)
    matches = [item for item in candidates(docs, stage) if item["result_ref"] == ref]
    if not matches and allow_historical and stage == "experience-design":
        historical = historical_experience_candidate(docs, ref)
        matches = [historical] if historical is not None else []
    if not matches:
        return None, [f"{stage} receipt must use its canonical result_ref, got {ref}"]
    item = matches[0]
    errors = []
    if item["status"] != "approved" or (not item["current"] and not allow_historical):
        errors.append(f"{ref} is not an approved/current {stage} package")
    if expected_hash and item["package_hash"] != expected_hash:
        errors.append(f"{ref} package hash is stale or does not match expected hash")
    if require_strict_current and item.get("verification_profile") != "strict-current":
        errors.append(f"{ref} is legacy-readonly; begin a revision before using it as a new {stage} handoff")
    if require_committed and not item["committed"]:
        errors.append(f"{ref} has uncommitted package changes")
    return item, errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("candidates", "verify"):
        command = sub.add_parser(name)
        command.add_argument("--docs", required=True)
        command.add_argument("--stage", choices=sorted(STAGES), required=True)
        command.add_argument("--json", action="store_true")
        if name == "verify":
            command.add_argument("--ref", required=True)
            command.add_argument("--expected-hash", default="")
            command.add_argument("--require-committed", action="store_true")
            command.add_argument("--strict-current", action="store_true")
    args = parser.parse_args(argv)
    try:
        docs = docs_root(args.docs)
        if args.command == "candidates":
            result, errors = {"stage": args.stage, "candidates": candidates(docs, args.stage)}, []
        else:
            value, errors = verify(docs, args.stage, args.ref, args.expected_hash,
                                   args.require_committed, args.strict_current)
            result = {"stage": args.stage, "receipt": value, "errors": errors}
    except (OSError, ValueError) as exc:
        result, errors = {"errors": [str(exc)]}, [str(exc)]
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
