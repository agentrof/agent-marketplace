"""Replay a closed Test Plan schema repair against a committed approval."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess

import atomic_file
import backlog_compile as backlog

MIGRATION = "test-plan-level-v1"
RECEIPTS = "backlog/artifacts/schema-migrations"
SWITCH = "backlog_schema_migration"
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
NOTE = re.compile(
    r"backlog/(?:backlog\.md|reviews/round-[0-9]+-backlog-review\.md|"
    r"epics/[^/]+/(?:epic\.md|reviews/round-[0-9]+-epic-review\.md|"
    r"stories/[^/]+/(?:story|test-plan)\.md))")


def byte_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def encoded(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def replace_stamp(text: str, field: str, value: str) -> str:
    boundary = text.find("\n---", 4)
    if not text.startswith("---\n") or boundary < 0:
        raise ValueError("migration needs compiler-stamped LF front matter")
    header, body = text[:boundary], text[boundary:]
    matches = list(re.finditer(rf"(?m)^{field}: [^\n]+$", header))
    if len(matches) != 1:
        raise ValueError(f"migration needs exactly one {field} stamp")
    match = matches[0]
    return header[:match.start()] + field + ": " + value + header[match.end():] + body


def migrate_test_plan(data: bytes) -> bytes:
    """Split only an unambiguous leading level; retain the entire old explanation."""
    text = data.decode("utf-8")
    result = text
    for scenario, block in backlog.scenario_blocks(text):
        fields, duplicates = backlog.scenario_fields(block)
        value = fields.get("level")
        if value is None or value in backlog.LEVELS:
            continue
        if duplicates or "level_reason" in fields:
            raise ValueError(f"{scenario} has duplicate or already split level fields")
        # The explanation is copied verbatim rather than interpreting its words.
        match = re.fullmatch(r"(" + "|".join(map(re.escape, backlog.LEVELS))
                             + r")(?::[ \t]*\S.*|[ \t]+\(\S.*\)|[ \t]+-[ \t]+\S.*)", value)
        if match is None:
            raise ValueError(f"{scenario} level has no unambiguous leading enum; use a reviewed revision")
        lines = list(re.finditer(r"(?m)^-\s+level:[ \t]*[^\n]*$", block))
        if len(lines) != 1:
            raise ValueError(f"{scenario} needs one single-line level field")
        replacement = "- level: " + match.group(1) + "\n- level_reason: " + value
        updated = block[:lines[0].start()] + replacement + block[lines[0].end():]
        if result.count(block) != 1:
            raise ValueError(f"{scenario} scenario block is ambiguous")
        result = result.replace(block, updated, 1)
    if result == text:
        return data
    return replace_stamp(result, "source_hash", backlog.digest_text(result)).encode("utf-8")


def package_hash(sources: dict[str, bytes]) -> str:
    hashes = {name: backlog.digest_text(data.decode("utf-8")) for name, data in sources.items()}
    return byte_hash(json.dumps(hashes, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8"))


def migration_plan(sources: dict[str, bytes], commit: str, from_version: str,
                   to_version: str) -> tuple[dict, dict[str, bytes]]:
    """Pure replay; the caller proves that sources are a committed approved boundary."""
    if not VERSION.fullmatch(from_version) or not VERSION.fullmatch(to_version):
        raise ValueError("migration versions must be numeric SemVer")
    if tuple(map(int, from_version.split("."))) > tuple(map(int, to_version.split("."))):
        raise ValueError("migration cannot downgrade package versions")
    if not sources or any(NOTE.fullmatch(name) is None for name in sources):
        raise ValueError("migration inventory is not the canonical backlog note set")
    root = "backlog/backlog.md"
    props, _body = backlog.parse_front_matter_text(sources[root].decode("utf-8"))
    if props.get("status") != "approved" or props.get("package_hash") != package_hash(sources):
        raise ValueError("migration predecessor is not an intact approved backlog")
    outputs = dict(sources)
    for name, data in sources.items():
        if name.endswith("/test-plan.md"):
            outputs[name] = migrate_test_plan(data)
    changed = [name for name in sources if outputs[name] != sources[name]]
    if not changed:
        raise ValueError("no legacy level fields need this migration")
    new_hash = package_hash(outputs)
    outputs[root] = replace_stamp(outputs[root].decode("utf-8"), "package_hash", new_hash).encode("utf-8")
    receipt = {"schema_version": 1, "migration": MIGRATION, "source_commit": commit,
               "from_version": from_version, "to_version": to_version,
               "before_package_hash": props["package_hash"], "after_package_hash": new_hash,
               "files": [{"path": name, "before_hash": byte_hash(sources[name]),
                          "after_hash": byte_hash(outputs[name]),
                          "before_source_hash": backlog.digest_text(sources[name].decode("utf-8")),
                          "after_source_hash": backlog.digest_text(outputs[name].decode("utf-8"))}
                         for name in sorted(sources)]}
    receipt["owner_approval"] = byte_hash(encoded(receipt))
    return receipt, outputs


def safe_path(docs: Path, relative: str) -> Path:
    if not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("migration path is outside the vault")
    path = docs
    for part in Path(relative).parts:
        path /= part
        if path.is_symlink():
            raise ValueError("migration path is symlinked")
    if path.exists():
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ValueError("migration file must be regular and not hard-linked")
    return path


def approved_sources(docs: Path, commit: str) -> tuple[str, dict[str, bytes]]:
    project = backlog.history_project(docs)
    if project is None:
        raise ValueError("migration requires a Git-tracked approval")
    oid = backlog.git_object_id(project, commit + "^{commit}")
    if oid is None:
        raise ValueError("migration source commit cannot be resolved")
    ancestor = subprocess.run(["git", "--no-replace-objects", "merge-base", "--is-ancestor", oid, "HEAD"],
                              cwd=project, capture_output=True, check=False)
    if ancestor.returncode:
        raise ValueError("migration source commit must be an ancestor of HEAD")
    sources = backlog.approved_history_sources(project, docs, oid)
    if sources is None:
        raise ValueError("migration source is not a committed hash-verified approval")
    return oid, {path.relative_to(docs).as_posix(): data for path, data in sources.items()
                 if NOTE.fullmatch(path.relative_to(docs).as_posix())}


def installed_version() -> str:
    package = Path(__file__).resolve().parents[1]
    manifest = package / ".agent-marketplace-package.json"
    if manifest.is_file():
        return str(json.loads(manifest.read_text(encoding="utf-8"))["version"])
    return str(json.loads((package.parents[1] / "versions.json").read_text(encoding="utf-8"))
               ["plugins"]["software-engineering-team"])


def verify_inventory(docs: Path, before: dict[str, bytes], after: dict[str, bytes], *,
                     resumable: bool = False) -> None:
    actual = {path.relative_to(docs).as_posix() for path in (docs / "backlog").rglob("*.md")
              if NOTE.fullmatch(path.relative_to(docs).as_posix())}
    if actual != set(after):
        raise ValueError("migration canonical note inventory changed")
    for name, output in after.items():
        current = safe_path(docs, name).read_bytes()
        if current != output and (not resumable or current != before[name]):
            raise ValueError(f"migration has unrelated or concurrent changes: {name}")


def replay_receipt(docs: Path, receipt: dict) -> dict:
    if not isinstance(receipt, dict) or receipt.get("migration") != MIGRATION:
        raise ValueError("unknown migration receipt")
    strings = ("source_commit", "from_version", "to_version", "owner_approval",
               "before_package_hash", "after_package_hash")
    if (type(receipt.get("schema_version")) is not int or receipt["schema_version"] != 1
            or any(not isinstance(receipt.get(key), str) for key in strings)
            or not isinstance(receipt.get("files"), list)):
        raise ValueError("migration receipt has invalid field types")
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", receipt["source_commit"]) is None:
        raise ValueError("migration receipt must name one full commit object id")
    oid, sources = approved_sources(docs, receipt["source_commit"])
    expected, outputs = migration_plan(sources, oid, receipt["from_version"], receipt["to_version"])
    if receipt != expected:
        raise ValueError("migration receipt does not equal deterministic replay")
    verify_inventory(docs, sources, outputs)
    return expected


def compatible_pins(docs: Path, old_hash: str, new_hash: str) -> dict[str, tuple[str, str]]:
    """Return only the exact hash aliases proven by a retained owner-approved receipt."""
    if old_hash == new_hash:
        return {}
    directory = docs / RECEIPTS
    if not directory.exists():
        return {}
    receipts = []
    for path in sorted(directory.glob("*.json")):
        safe_path(docs, path.relative_to(docs).as_posix())
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(receipt, dict) and receipt.get("before_package_hash") == old_hash \
                and receipt.get("after_package_hash") == new_hash:
            receipts.append(replay_receipt(docs, receipt))
    if len(receipts) > 1:
        raise ValueError("migration binding is ambiguous")
    return {row["path"]: (row["before_source_hash"], row["after_source_hash"])
            for row in receipts[0]["files"]} if receipts else {}


def require_opt_in(docs: Path) -> None:
    import process_policy
    values, _snapshot = process_policy.effective_values(docs)
    if values[SWITCH]["value"] != "receipt_only":
        raise ValueError("backlog_schema_migration must select receipt_only in the approved Process Policy")


def command(args) -> int:
    docs = backlog.docs_root(args.docs)
    try:
        require_opt_in(docs)
        if args.command == "plan-schema-migration":
            oid, before = approved_sources(docs, args.source_commit)
            receipt, outputs = migration_plan(before, oid, args.from_version, installed_version())
            verify_inventory(docs, before, outputs, resumable=True)
        else:
            import setup_project
            project = backlog.history_project(docs)
            if project is None:
                raise ValueError("migration requires a Git project")
            with setup_project.refresh_guard(project):
                require_opt_in(docs)
                oid, before = approved_sources(docs, args.source_commit)
                receipt, outputs = migration_plan(before, oid, args.from_version, installed_version())
                if args.approve_receipt != receipt["owner_approval"]:
                    raise ValueError("owner approval must name the exact planned receipt hash")
                verify_inventory(docs, before, outputs, resumable=True)
                path = safe_path(docs, RECEIPTS + "/" + receipt["owner_approval"][7:] + ".json")
                receipt_bytes = encoded(receipt)
                if path.exists() and path.read_bytes() != receipt_bytes:
                    raise ValueError("migration receipt was changed")
                changed = {}
                try:
                    for name, data in outputs.items():
                        target = safe_path(docs, name)
                        old = target.read_bytes()
                        if old == data:
                            continue
                        def unchanged(target=target, old=old):
                            if target.read_bytes() != old:
                                raise ValueError("migration source changed before replacement")
                        atomic_file.replace_bytes(target, data, unchanged)
                        changed[target] = (old, data)
                    with backlog.stage_package.candidate_session(), backlog.experience_validation_session():
                        record, errors = backlog.collect(docs, historical_inputs=True)
                    if record.get("backlog"):
                        errors.extend(backlog.approval_findings(record, docs))
                    if errors:
                        raise ValueError("; ".join(errors))
                    backlog.render(record, docs)
                    verify_inventory(docs, before, outputs)
                    if not path.exists():
                        atomic_file.replace_bytes(path, receipt_bytes)
                except Exception:
                    for target, (old, data) in reversed(list(changed.items())):
                        if target.read_bytes() == data:
                            atomic_file.replace_bytes(target, old)
                    raise
        print(json.dumps({"ok": True, "receipt": receipt}, indent=2, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "errors": [str(exc)]}, indent=2, ensure_ascii=False))
        return 1
