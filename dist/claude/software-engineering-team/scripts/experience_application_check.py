#!/usr/bin/env python3
"""Verify opaque Experience Design prototype snapshots.

The prototype is deliberately author-owned. This checker never parses or
interprets artifact contents: it records a safe file inventory and binds that
inventory to the Experience lifecycle receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
import unicodedata
from pathlib import Path


ARTIFACTS_RELATIVE = Path("artifacts")
# Kept as a public compatibility name. It now identifies the complete,
# author-owned artifact tree rather than one prescribed HTML file.
APPLICATION_RELATIVE = ARTIFACTS_RELATIVE
REGISTRY_RELATIVE = Path("_generated/application-registry.json")
LEDGER_RELATIVE = Path("_ledger/application-revisions.json")
GENESIS_APPLICATION_HASH = "sha256:" + "0" * 64
HASH_PREFIX = "sha256:"
HASH_LENGTH = 64
LEGACY_REGISTRY_FIELDS = {
    "schema_version", "application_revision", "source_hash", "package_set_hash",
    "coverage_hash", "design_system", "runtime_sha256", "packages", "coverage",
    "previous_application_hash", "application_hash",
}
LEGACY_DESIGN_SYSTEM_FIELDS = {
    "package_hash", "revision", "master_source_hash",
}


def _json_contains_non_scalar(value: object) -> bool:
    if isinstance(value, str):
        return any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    if isinstance(value, dict):
        return any(
            _json_contains_non_scalar(key) or _json_contains_non_scalar(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_json_contains_non_scalar(item) for item in value)
    return False


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON member: {key}")
        value[key] = item
    return value


def strict_json_loads(text: str) -> object:
    value = json.loads(text, object_pairs_hook=_unique_json_object)
    if _json_contains_non_scalar(value):
        raise ValueError("JSON strings must contain only Unicode scalar values")
    return value


def _bounded_json_text(value: str, *, label: str = "JSON input") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    if len(value.encode("utf-8")) > 1_000_000:
        raise ValueError(f"{label} exceeds the JSON input limit")
    return value


def canonical(value: object) -> bytes:
    if _json_contains_non_scalar(value):
        raise ValueError("JSON strings must contain only Unicode scalar values")
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def sha(value: bytes) -> str:
    return HASH_PREFIX + hashlib.sha256(value).hexdigest()


def _hash(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(HASH_PREFIX)
        and len(value) == len(HASH_PREFIX) + HASH_LENGTH
        and all(character in "0123456789abcdef" for character in value[len(HASH_PREFIX):])
    )


def _reserved_alias(value: str, expected: str) -> bool:
    return unicodedata.normalize("NFC", value).casefold() == expected.casefold()


def _regular_directory(path: Path, label: str, *, reserved_name: str | None = None) -> None:
    if reserved_name is not None:
        if path.name != reserved_name or unicodedata.normalize("NFC", path.name) != reserved_name:
            raise ValueError(f"{label} must use exact NFC spelling and case: {reserved_name}")
        if path.parent.is_dir() and not path.parent.is_symlink():
            aliases = [
                child.name for child in path.parent.iterdir()
                if _reserved_alias(child.name, reserved_name)
            ]
            if aliases != [reserved_name]:
                raise ValueError(f"{label} must resolve from one exact {reserved_name} directory")
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"{label} must be one regular, non-symlink directory")


def root_for(value: str | Path) -> Path:
    """Resolve a canonical Experience root without following aliases."""
    raw = Path(value).expanduser()
    lexical = Path(os.path.abspath(os.fspath(raw)))
    name = unicodedata.normalize("NFC", lexical.name).casefold()
    if name == "experience-design":
        candidate = lexical
        chain = [(candidate, "Experience root", "experience-design")]
        docs = candidate.parent
        if _reserved_alias(docs.name, "docs"):
            chain.insert(0, (docs, "Experience owner", "docs"))
            workspace = docs.parent
            if _reserved_alias(workspace.name, "workspace"):
                chain.insert(0, (workspace, "Experience workspace", "workspace"))
    elif name == "docs":
        candidate = lexical / "experience-design"
        chain = [
            (lexical, "Experience owner", "docs"),
            (candidate, "Experience root", "experience-design"),
        ]
        workspace = lexical.parent
        if _reserved_alias(workspace.name, "workspace"):
            chain.insert(0, (workspace, "Experience workspace", "workspace"))
    elif name == "workspace":
        candidate = lexical / "docs" / "experience-design"
        chain = [
            (lexical, "Experience workspace", "workspace"),
            (lexical / "docs", "Experience owner", "docs"),
            (candidate, "Experience root", "experience-design"),
        ]
    else:
        _regular_directory(lexical, "project root selector")
        workspace = lexical / "workspace"
        direct = lexical / "experience-design"
        if workspace.exists() or workspace.is_symlink():
            candidate = workspace / "docs" / "experience-design"
            chain = [
                (workspace, "Experience workspace", "workspace"),
                (workspace / "docs", "Experience owner", "docs"),
                (candidate, "Experience root", "experience-design"),
            ]
        elif direct.exists() or direct.is_symlink():
            candidate = direct
            chain = [(candidate, "Experience root", "experience-design")]
        else:
            raise ValueError("--root must identify workspace/docs/experience-design")
    for path, label, reserved_name in chain:
        _regular_directory(path, label, reserved_name=reserved_name)
    return candidate.resolve()


def _safe_relative(path: Path) -> bool:
    return bool(path.parts) and all(part not in {"", ".", ".."} for part in path.parts)


def artifact_inventory(root_value: str | Path) -> tuple[list[dict], list[str]]:
    """Return a byte inventory without assigning semantics to any artifact."""
    root = root_for(root_value)
    artifacts = root / ARTIFACTS_RELATIVE
    if not artifacts.exists() and not artifacts.is_symlink():
        return [], []
    if artifacts.is_symlink() or not artifacts.is_dir():
        return [], ["artifacts must be one regular, non-symlink directory"]
    rows: list[dict] = []
    findings: list[str] = []
    try:
        paths = sorted(artifacts.rglob("*"))
    except OSError as exc:
        return [], [f"artifacts cannot be enumerated: {exc}"]
    for path in paths:
        relative = path.relative_to(artifacts)
        display = f"artifacts/{relative.as_posix()}"
        if not _safe_relative(relative):
            findings.append(f"{display}: non-canonical artifact path")
            continue
        if path.is_symlink():
            findings.append(f"{display}: symlinks are not permitted in a snapshot")
            continue
        try:
            metadata = path.stat()
        except OSError as exc:
            findings.append(f"{display}: cannot inspect artifact identity: {exc}")
            continue
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if not stat.S_ISREG(metadata.st_mode):
            findings.append(f"{display}: prototype snapshots contain regular files only")
            continue
        if metadata.st_nlink != 1:
            findings.append(f"{display}: hard-linked files are not permitted in a snapshot")
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            findings.append(f"{display}: cannot read artifact: {exc}")
            continue
        rows.append({"path": relative.as_posix(), "sha256": sha(raw), "size": len(raw)})
    rows.sort(key=lambda row: row["path"])
    return rows, sorted(set(findings))


def artifact_tree_hash(rows: list[dict]) -> str:
    return sha(canonical(rows))


def _inventory_valid(rows: object, label: str, findings: list[str]) -> None:
    if not isinstance(rows, list):
        findings.append(f"{label} must be an array")
        return
    paths: list[str] = []
    for index, row in enumerate(rows):
        item_label = f"{label}[{index}]"
        if not isinstance(row, dict) or set(row) != {"path", "sha256", "size"}:
            findings.append(f"{item_label} must have the exact artifact file fields")
            continue
        path = row.get("path")
        if not isinstance(path, str) or not _safe_relative(Path(path)) or Path(path).as_posix() != path:
            findings.append(f"{item_label}.path must be a canonical relative path")
        else:
            paths.append(path)
        if not _hash(row.get("sha256")):
            findings.append(f"{item_label}.sha256 must be a SHA-256 digest")
        if type(row.get("size")) is not int or int(row["size"]) < 0:
            findings.append(f"{item_label}.size must be a non-negative integer")
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        findings.append(f"{label} must be sorted and unique by path")


def _packages_valid(rows: object, label: str, findings: list[str]) -> None:
    if not isinstance(rows, list):
        findings.append(f"{label} must be an array")
        return
    refs: list[str] = []
    for index, row in enumerate(rows):
        item_label = f"{label}[{index}]"
        if not isinstance(row, dict) or set(row) != {"result_ref", "package_hash"}:
            findings.append(f"{item_label} must have exact process receipt fields")
            continue
        ref = row.get("result_ref")
        if not isinstance(ref, str) or not ref:
            findings.append(f"{item_label}.result_ref must be non-empty text")
        else:
            refs.append(ref)
        if not _hash(row.get("package_hash")):
            findings.append(f"{item_label}.package_hash must be a SHA-256 digest")
    if refs != sorted(refs) or len(refs) != len(set(refs)):
        findings.append(f"{label} must be sorted and unique by result_ref")


def _validate_legacy_registry(registry: dict, label: str, findings: list[str]) -> None:
    """Validate v2 only to let a later v3 receipt supersede it safely.

    This does not reinstate v2's UI rules. It validates historic chain and
    package identity; the next approval snapshots the current opaque tree.
    """
    if set(registry) != LEGACY_REGISTRY_FIELDS:
        findings.append(f"{label} must contain the exact legacy receipt fields")
        return
    if registry.get("schema_version") != 2:
        findings.append(f"{label}.schema_version must be integer 2")
    if type(registry.get("application_revision")) is not int or registry["application_revision"] < 1:
        findings.append(f"{label}.application_revision must be a positive integer")
    for field in (
        "source_hash", "package_set_hash", "coverage_hash", "runtime_sha256",
        "previous_application_hash", "application_hash",
    ):
        if not _hash(registry.get(field)):
            findings.append(f"{label}.{field} must be a SHA-256 digest")
    design_system = registry.get("design_system")
    if not isinstance(design_system, dict) or set(design_system) != LEGACY_DESIGN_SYSTEM_FIELDS:
        findings.append(f"{label}.design_system must contain the exact legacy binding fields")
    else:
        if not _hash(design_system.get("package_hash")):
            findings.append(f"{label}.design_system.package_hash must be a SHA-256 digest")
        if not _hash(design_system.get("master_source_hash")):
            findings.append(f"{label}.design_system.master_source_hash must be a SHA-256 digest")
        if type(design_system.get("revision")) is not int or design_system["revision"] < 1:
            findings.append(f"{label}.design_system.revision must be a positive integer")
    _packages_valid(registry.get("packages"), f"{label}.packages", findings)
    if isinstance(registry.get("packages"), list) and registry.get("package_set_hash") != sha(canonical(registry["packages"])):
        findings.append(f"{label}.package_set_hash does not match packages")
    if not isinstance(registry.get("coverage"), dict):
        findings.append(f"{label}.coverage must be an object")
    unsigned = {key: value for key, value in registry.items() if key != "application_hash"}
    if _hash(registry.get("application_hash")) and registry.get("application_hash") != sha(canonical(unsigned)):
        findings.append(f"{label}.application_hash is invalid")


def _validate_registry(registry: object, label: str, findings: list[str]) -> None:
    if isinstance(registry, dict) and registry.get("schema_version") == 2:
        _validate_legacy_registry(registry, label, findings)
        return
    expected = {
        "schema_version", "application_revision", "artifact_files",
        "artifact_tree_hash", "package_set_hash", "packages",
        "previous_application_hash", "application_hash",
    }
    if not isinstance(registry, dict) or set(registry) != expected:
        findings.append(f"{label} must contain the exact opaque snapshot receipt fields")
        return
    if registry.get("schema_version") != 3:
        findings.append(f"{label}.schema_version must be integer 3")
    if type(registry.get("application_revision")) is not int or registry["application_revision"] < 1:
        findings.append(f"{label}.application_revision must be a positive integer")
    _inventory_valid(registry.get("artifact_files"), f"{label}.artifact_files", findings)
    _packages_valid(registry.get("packages"), f"{label}.packages", findings)
    if isinstance(registry.get("artifact_files"), list) and registry.get("artifact_tree_hash") != artifact_tree_hash(registry["artifact_files"]):
        findings.append(f"{label}.artifact_tree_hash does not match artifact_files")
    if isinstance(registry.get("packages"), list) and registry.get("package_set_hash") != sha(canonical(registry["packages"])):
        findings.append(f"{label}.package_set_hash does not match packages")
    for field in ("artifact_tree_hash", "package_set_hash", "previous_application_hash", "application_hash"):
        if not _hash(registry.get(field)):
            findings.append(f"{label}.{field} must be a SHA-256 digest")
    unsigned = {key: value for key, value in registry.items() if key != "application_hash"}
    if _hash(registry.get("application_hash")) and registry.get("application_hash") != sha(canonical(unsigned)):
        findings.append(f"{label}.application_hash is invalid")


def verified_application_ledger(root_value: str | Path) -> tuple[list[dict], list[str]]:
    root = root_for(root_value)
    target = root / LEDGER_RELATIVE
    if not target.exists() and not target.is_symlink():
        return [], []
    if not target.is_file() or target.is_symlink():
        return [], ["application revision ledger must be one regular file"]
    try:
        value = strict_json_loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [], [f"application revision ledger is unreadable: {exc}"]
    findings: list[str] = []
    if not isinstance(value, dict) or set(value) != {"schema_version", "revisions"}:
        return [], ["application revision ledger must contain schema_version and revisions"]
    if value.get("schema_version") not in {2, 3}:
        findings.append("application revision ledger schema_version must be integer 2 or 3")
    rows = value.get("revisions")
    if not isinstance(rows, list):
        return [], sorted(set([*findings, "application revision ledger revisions must be an array"]))
    previous = GENESIS_APPLICATION_HASH
    revisions: list[int] = []
    published: dict[str, str] = {}
    for index, row in enumerate(rows):
        _validate_registry(row, f"application revision ledger revisions[{index}]", findings)
        if isinstance(row, dict):
            revision = row.get("application_revision")
            if type(revision) is int:
                revisions.append(revision)
            if row.get("previous_application_hash") != previous:
                findings.append("application revision ledger hash chain is stale or tampered")
            if _hash(row.get("application_hash")):
                previous = str(row["application_hash"])
            for package in row.get("packages", []) if isinstance(row.get("packages"), list) else []:
                if isinstance(package, dict):
                    ref = package.get("result_ref")
                    package_hash = package.get("package_hash")
                    if isinstance(ref, str) and isinstance(package_hash, str):
                        if ref in published and published[ref] != package_hash:
                            findings.append("application revision ledger reuses a process receipt with conflicting immutable hashes")
                        published[ref] = package_hash
    if revisions != list(range(1, len(rows) + 1)):
        findings.append("application revision ledger must contain one ordered, contiguous receipt per revision")
    return [row for row in rows if isinstance(row, dict)], sorted(set(findings))


def _read_registry(root: Path) -> tuple[dict, list[str]]:
    target = root / REGISTRY_RELATIVE
    if not target.is_file() or target.is_symlink():
        return {}, ["approved application registry is missing"]
    try:
        value = strict_json_loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {}, [f"approved application registry is unreadable: {exc}"]
    findings: list[str] = []
    _validate_registry(value, "approved application registry", findings)
    return value if isinstance(value, dict) else {}, sorted(set(findings))


def _active_packages(root: Path, candidates: list[Path] | None) -> tuple[list[dict], list[str]]:
    import experience_compile
    candidate_paths = candidates if candidates is not None else experience_compile.packages(root)
    rows: list[dict] = []
    findings: list[str] = []
    for package in sorted(candidate_paths, key=lambda item: item.name):
        status = experience_compile.fields(package).get("status")
        if status in {"retirement_pending", "retired"}:
            continue
        if not (package / "experience.md").is_file():
            findings.append(f"experiences/{package.name}: every package needs experience.md")
            continue
        registry, package_findings = experience_compile.compile_package(package, gate=False, allow_stale_inputs=True)
        findings.extend(f"{package.name}: {finding}" for finding in package_findings)
        rows.append({
            "result_ref": f"{package.name}@r{registry.get('package_revision', 0)}",
            "package_hash": str(registry.get("package_hash", "")),
        })
    rows.sort(key=lambda row: row["result_ref"])
    return rows, sorted(set(findings))


def _opened_revision(root: Path) -> tuple[int, str, list[str]]:
    """Find the current receipt revision from lifecycle state or history."""
    open_state = root / "_generated" / "open-application-revision.json"
    if open_state.exists() or open_state.is_symlink():
        if not open_state.is_file() or open_state.is_symlink():
            return 0, "", ["application open revision state is not a regular file"]
        try:
            state = strict_json_loads(open_state.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return 0, "", [f"application open revision state is unreadable: {exc}"]
        revision = state.get("opened_revision") if isinstance(state, dict) else None
        if type(revision) is not int or revision < 1:
            return 0, "", ["application open revision state has no valid opened_revision"]
        ledger, findings = verified_application_ledger(root)
        if findings:
            return 0, "", findings
        expected = 1 if not ledger else int(ledger[-1]["application_revision"]) + 1
        if revision != expected:
            return 0, "", ["application open revision does not append the approved ledger"]
        previous = GENESIS_APPLICATION_HASH if not ledger else str(ledger[-1]["application_hash"])
        return revision, previous, []
    registry, registry_findings = _read_registry(root)
    ledger, ledger_findings = verified_application_ledger(root)
    findings = [*registry_findings, *ledger_findings]
    if findings:
        return 0, "", sorted(set(findings))
    if not registry and not ledger:
        return 1, GENESIS_APPLICATION_HASH, []
    revision = registry.get("application_revision")
    if type(revision) is not int or not ledger or len(ledger) != revision or ledger[-1] != registry:
        return 0, "", ["approved application registry and ledger do not agree"]
    return revision, str(registry.get("previous_application_hash", "")), []


def compile_application(root_value: str | Path, gate: bool = False, *, package_paths: list[Path] | None = None, authoring: bool = False) -> tuple[dict, list[str]]:
    """Compile a receipt without opening, parsing or changing prototype files."""
    del authoring
    root = root_for(root_value)
    artifact_files, findings = artifact_inventory(root)
    package_rows, package_findings = _active_packages(root, package_paths)
    findings.extend(package_findings)
    if package_rows and not artifact_files:
        findings.append("active Experience packages require at least one author-owned prototype artifact before review")
    revision, previous, revision_findings = _opened_revision(root)
    findings.extend(revision_findings)
    if revision < 1 or not _hash(previous):
        return {}, sorted(set(findings))
    registry = {
        "schema_version": 3,
        "application_revision": revision,
        "artifact_files": artifact_files,
        "artifact_tree_hash": artifact_tree_hash(artifact_files),
        "package_set_hash": sha(canonical(package_rows)),
        "packages": package_rows,
        "previous_application_hash": previous,
    }
    registry["application_hash"] = sha(canonical(registry))
    if gate:
        stored, stored_findings = _read_registry(root)
        ledger, ledger_findings = verified_application_ledger(root)
        findings.extend(stored_findings)
        findings.extend(ledger_findings)
        if stored != registry:
            findings.append("approved application registry is stale or does not match the artifact snapshot")
        current = [row for row in ledger if row.get("application_revision") == revision]
        if len(current) != 1 or current[0] != registry:
            findings.append("application revision ledger does not contain the approved artifact receipt")
        if (root / "_generated" / "open-application-revision.json").exists():
            findings.append("approved application still has an open lifecycle revision")
    return registry, sorted(set(findings))


def approved_snapshot(root_value: str | Path) -> tuple[dict, list[str]]:
    root = root_for(root_value)
    registry, findings = _read_registry(root)
    ledger, ledger_findings = verified_application_ledger(root)
    findings.extend(ledger_findings)
    if findings:
        return registry, sorted(set(findings))
    revision = registry.get("application_revision")
    if not ledger or type(revision) is not int or len(ledger) != revision or ledger[-1] != registry:
        return registry, ["approved application registry and ledger do not agree"]
    files, snapshot_findings = artifact_inventory(root)
    findings.extend(snapshot_findings)
    if registry.get("schema_version") == 2:
        # A v2 record can be the predecessor of the first opaque snapshot.
        # Derive the byte inventory only for this transition, without reading
        # any artifact as HTML, CSS, JavaScript, or a product contract.
        package_rows, package_findings = _active_packages(root, None)
        findings.extend(package_findings)
        if package_rows != registry.get("packages") or sha(canonical(package_rows)) != registry.get("package_set_hash"):
            findings.append("approved application process set is stale")
        return {
            **registry,
            "artifact_files": files,
            "artifact_tree_hash": artifact_tree_hash(files),
        }, sorted(set(findings))
    if files != registry.get("artifact_files") or artifact_tree_hash(files) != registry.get("artifact_tree_hash"):
        findings.append("approved artifact tree differs from its receipt")
    package_rows, package_findings = _active_packages(root, None)
    findings.extend(package_findings)
    if package_rows != registry.get("packages") or sha(canonical(package_rows)) != registry.get("package_set_hash"):
        findings.append("approved application process set is stale")
    return registry, sorted(set(findings))


def application_receipt(root: Path, registry: dict) -> dict:
    return {
        "stage": "experience-design",
        "result_ref": f"application@r{registry['application_revision']}",
        "result_type": "experience-application",
        "package_hash": registry["application_hash"],
        "status": "approved",
        "current": True,
    }


def artifact_snapshot_paths(root_value: str | Path, registry: dict) -> list[Path]:
    root = root_for(root_value)
    files = registry.get("artifact_files", [])
    if not isinstance(files, list):
        raise ValueError("application registry has no artifact inventory")
    paths = []
    for row in files:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            raise ValueError("application registry has an invalid artifact inventory")
        paths.append(root / ARTIFACTS_RELATIVE / row["path"])
    return [*paths, root / REGISTRY_RELATIVE, root / LEDGER_RELATIVE]


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def write_registry_and_ledger(root_value: str | Path, registry: dict) -> None:
    root = root_for(root_value)
    findings: list[str] = []
    _validate_registry(registry, "application registry", findings)
    if findings:
        raise ValueError("; ".join(sorted(set(findings))))
    rows, ledger_findings = verified_application_ledger(root)
    if ledger_findings:
        raise ValueError("; ".join(ledger_findings))
    expected_revision = 1 if not rows else int(rows[-1]["application_revision"]) + 1
    expected_previous = GENESIS_APPLICATION_HASH if not rows else str(rows[-1]["application_hash"])
    if registry.get("application_revision") != expected_revision:
        raise ValueError(f"application revision must append exactly revision {expected_revision}")
    if registry.get("previous_application_hash") != expected_previous:
        raise ValueError("application receipt must bind the exact previous application hash")
    generated = root / REGISTRY_RELATIVE
    ledger = root / LEDGER_RELATIVE
    for target, label in ((generated, "generated application registry"), (ledger, "application revision ledger")):
        if target.parent.is_symlink() or (target.parent.exists() and not target.parent.is_dir()):
            raise ValueError(f"{label} parent must be one regular directory")
        if target.exists() and (not target.is_file() or target.is_symlink() or target.stat().st_nlink != 1):
            raise ValueError(f"{label} must be one regular file")
    _atomic_write(generated, canonical(registry))
    _atomic_write(ledger, canonical({"schema_version": 3, "revisions": [*rows, registry]}))


def self_check() -> list[str]:
    """Keep a lightweight host-install integrity command without a UI schema."""
    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("--root", required=True)
    check.add_argument("--gate", action="store_true")
    check.add_argument("--authoring", action="store_true")
    check.add_argument("--json", action="store_true")
    selfcheck = sub.add_parser("self-check")
    selfcheck.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "self-check":
        findings = self_check()
        payload = {"ok": not findings, "findings": findings}
        print(json.dumps(payload, indent=2) if args.json else "\n".join(findings))
        return 1 if findings else 0
    try:
        registry, findings = compile_application(args.root, args.gate, authoring=args.authoring)
    except (OSError, ValueError) as exc:
        registry, findings = {}, [str(exc)]
    payload = {"ok": not findings, "application": registry, "findings": findings}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        for finding in findings:
            print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
