#!/usr/bin/env python3
"""Safe Delivery Git coordination with exact remote leases and recovery.

All subprocesses use argument arrays and never accept a user value as a shell
fragment. Semantic compilers prepare Markdown; this module owns atomic Fence,
Integration, Item and Slot transitions, local receipts/worktrees, target
refresh and cancellation publication.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import delivery_governance

import delivery_result
import file_lock
from vault_check import rel_posix


DELIVERY_ID_RE = re.compile(r"^DLV-[0-9]{3,}$")
STORY_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-[0-9]{2,}$")
SLOT_RE = re.compile(r"^[0-9]{3,}$")
EPOCH_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")
OID_RE = re.compile(r"^[0-9a-f]{40,64}$")
# A branch name Git accepts in a refspec, never an option, a refspec separator or a pattern.
TARGET_BRANCH_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/-]*")
RECEIPT_SCHEMA_VERSION = 1
GITHUB_PR_RE = re.compile(r"^/([^/]+)/([^/]+)/pull/([1-9][0-9]*)$")
FENCE_MODES = {"open", "source_handoff", "governance", "upgrade"}
SOURCE_KINDS = {"none", "requirement_supersession", "backlog_revision"}
CARRIER_KINDS = {"none", "github_pr", "direct_target"}
UPGRADE_PHASES = {"none", "acquired", "target_handoff"}
FENCE_CANONICAL_KEYS = (
    "Mode", "Epoch", "Target", "Governance-Hash", "Source-Kind", "Source-Intent",
    "Target-Update-Intent", "Target-Update-Attempt", "Target-Repository",
    "Target-Carrier-Kind", "Target-Carrier-Ref", "Target-Carrier-Object",
    "Target-Carrier-Head", "Target-Carrier-Base", "Upgrade-Phase",
    "Upgrade-Contract", "Handoff-Target",
)


def validate_delivery_id(value: str) -> str:
    if not DELIVERY_ID_RE.fullmatch(value):
        raise ValueError("Delivery ID must match DLV- plus at least three digits")
    return value


def validate_story_id(value: str) -> str:
    if not STORY_ID_RE.fullmatch(value):
        raise ValueError("Story ID must be an injective project ID such as AUTH-01")
    return value


def story_key(value: str) -> str:
    validate_story_id(value)
    return value.lower()


def slot_key(value: str | int) -> str:
    text = f"{value:03d}" if isinstance(value, int) else str(value)
    if not SLOT_RE.fullmatch(text) or int(text) == 0:
        raise ValueError("DELIVERY_SLOT_INVALID: slot must be a positive number rendered with at least three digits")
    return text


def canonical_refs(delivery_id: str, story_id: str | None = None,
                   slot: str | int | None = None) -> dict[str, str]:
    validate_delivery_id(delivery_id)
    refs = {
        "fence": "refs/heads/agentrof/fence",
        "integration": f"refs/heads/agentrof/deliveries/{delivery_id.lower()}",
    }
    if story_id is not None:
        refs["item"] = f"refs/heads/agentrof/items/{story_key(story_id)}"
    if slot is not None:
        refs["slot"] = f"refs/heads/agentrof/slots/{slot_key(slot)}"
    return refs


def short_refs(delivery_id: str, story_id: str | None = None,
               slot: str | int | None = None) -> dict[str, str]:
    return {key: value.removeprefix("refs/heads/")
            for key, value in canonical_refs(delivery_id, story_id, slot).items()}


def worktree_paths(main_worktree: Path, delivery_id: str,
                   story_id: str | None = None) -> dict[str, Path]:
    validate_delivery_id(delivery_id)
    root = main_worktree / ".agentrof" / "agent-marketplace" / ".runtime" / "worktrees" / delivery_id.lower()
    paths = {"integration": root / "integration"}
    if story_id is not None:
        paths["item"] = root / "items" / story_key(story_id)
    return paths


def runtime_root(main_worktree: Path) -> Path:
    """Return the project-local disposable runtime anchor."""
    return main_worktree / ".agentrof" / "agent-marketplace" / ".runtime"


def writer_receipt_paths(main_worktree: Path, delivery_id: str,
                         story_id: str) -> tuple[Path, Path]:
    """Return the ignored receipt and sibling lock paths for one Item writer."""
    validate_delivery_id(delivery_id)
    validate_story_id(story_id)
    base = runtime_root(main_worktree) / "receipts"
    name = f"item-{delivery_id.lower()}-{story_key(story_id)}.json"
    return base / name, base / f"{name}.lock"


def _canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def receipt_digest(receipt: dict) -> str:
    """Hash the canonical receipt projection, excluding no mutable side field."""
    encoded = _canonical_json(receipt).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def receipt_lock(lock_path: Path):
    """Hold a crash-releasing process lock across receipt preimage transitions."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        file_lock.lock(descriptor)
        try:
            yield
        finally:
            file_lock.unlock(descriptor)
    finally:
        os.close(descriptor)


def _validate_receipt(receipt: dict) -> dict:
    required = {
        "schema_version", "kind", "state", "delivery", "story", "slot",
        "writer_epoch", "item_ref", "slot_ref", "candidate_oid",
        "created_at", "receipt_digest",
    }
    if set(receipt) != required:
        raise RuntimeError("writer receipt has an unexpected field set")
    if receipt["schema_version"] != RECEIPT_SCHEMA_VERSION or receipt["kind"] != "item-writer-v1":
        raise RuntimeError("writer receipt schema is unsupported")
    if receipt["state"] not in {"pending", "verified"}:
        raise RuntimeError("writer receipt state is invalid")
    validate_delivery_id(str(receipt["delivery"]))
    validate_story_id(str(receipt["story"]))
    slot_key(str(receipt["slot"]))
    if not EPOCH_RE.fullmatch(str(receipt["writer_epoch"])):
        raise RuntimeError("writer receipt epoch is invalid")
    if not OID_RE.fullmatch(str(receipt["candidate_oid"])):
        raise RuntimeError("writer receipt candidate OID is invalid")
    expected = dict(receipt)
    actual = expected.pop("receipt_digest")
    if actual != receipt_digest(expected):
        raise RuntimeError("writer receipt digest is invalid")
    return receipt


def read_writer_receipt(main_worktree: Path, delivery_id: str,
                        story_id: str) -> dict | None:
    receipt_path, lock_path = writer_receipt_paths(main_worktree, delivery_id, story_id)
    if not receipt_path.exists():
        return None
    with receipt_lock(lock_path):
        try:
            value = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"writer receipt cannot be read: {exc}")
        return _validate_receipt(value)


def _write_writer_receipt_locked(receipt_path: Path, receipt: dict) -> None:
    candidate = dict(receipt)
    candidate.pop("receipt_digest", None)
    candidate["receipt_digest"] = receipt_digest(candidate)
    data = json.dumps(candidate, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n",
                                     dir=receipt_path.parent,
                                     prefix=receipt_path.name + ".", delete=False) as temporary:
        temporary.write(data)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, receipt_path)
    _fsync_directory(receipt_path.parent)


def create_writer_receipt(main_worktree: Path, delivery_id: str, story_id: str,
                          slot: str, writer_epoch: str, item_ref: str,
                          slot_ref: str, candidate_oid: str,
                          *, allow_verified_replace: bool = False,
                          expected_previous_oid: str | None = None) -> dict:
    """Persist a pending activation before the remote CAS is attempted."""
    if not EPOCH_RE.fullmatch(writer_epoch):
        raise ValueError("writer epoch must be exactly 22 base64url characters")
    if not OID_RE.fullmatch(candidate_oid):
        raise ValueError("candidate OID is invalid")
    receipt_path, lock_path = writer_receipt_paths(main_worktree, delivery_id, story_id)
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "kind": "item-writer-v1",
        "state": "pending",
        "delivery": delivery_id,
        "story": story_id,
        "slot": slot_key(slot),
        "writer_epoch": writer_epoch,
        "item_ref": item_ref,
        "slot_ref": slot_ref,
        "candidate_oid": candidate_oid,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    with receipt_lock(lock_path):
        existing = None
        if receipt_path.exists():
            existing = _validate_receipt(json.loads(receipt_path.read_text(encoding="utf-8")))
        if existing is not None:
            if existing["candidate_oid"] != candidate_oid:
                if existing["state"] != "verified" or not allow_verified_replace:
                    raise RuntimeError("a different active writer receipt already exists")
                if expected_previous_oid is not None and existing["candidate_oid"] != expected_previous_oid:
                    raise RuntimeError("DELIVERY_WRITER_RECEIPT_STALE: takeover receipt does not match the previous writer tip")
            elif existing["state"] == "pending" or not allow_verified_replace:
                return existing
        _write_writer_receipt_locked(receipt_path, receipt)
    return _validate_receipt(json.loads(receipt_path.read_text(encoding="utf-8")))


def promote_writer_receipt(main_worktree: Path, delivery_id: str, story_id: str,
                           candidate_oid: str) -> dict:
    """Promote a pending receipt only after both remote refs equal its candidate."""
    receipt_path, lock_path = writer_receipt_paths(main_worktree, delivery_id, story_id)
    with receipt_lock(lock_path):
        if not receipt_path.exists():
            raise RuntimeError("DELIVERY_WRITER_RECEIPT_MISSING: pending writer receipt is missing")
        receipt = _validate_receipt(json.loads(receipt_path.read_text(encoding="utf-8")))
        if receipt["candidate_oid"] != candidate_oid:
            raise RuntimeError("DELIVERY_WRITER_RECEIPT_STALE: writer receipt candidate does not match remote activation")
        if receipt["state"] == "verified":
            return receipt
        receipt["state"] = "verified"
        _write_writer_receipt_locked(receipt_path, receipt)
        return _validate_receipt(json.loads(receipt_path.read_text(encoding="utf-8")))


def discard_pending_writer_receipt(main_worktree: Path, delivery_id: str,
                                   story_id: str, candidate_oid: str,
                                   replaced: dict | None = None) -> None:
    """Delete a pending receipt only after the remote CAS is conclusively rejected.

    A pending receipt that replaced a verified one gives that receipt back.
    """
    receipt_path, lock_path = writer_receipt_paths(main_worktree, delivery_id, story_id)
    with receipt_lock(lock_path):
        if not receipt_path.exists():
            return
        receipt = _validate_receipt(json.loads(receipt_path.read_text(encoding="utf-8")))
        if receipt["state"] != "pending" or receipt["candidate_oid"] != candidate_oid:
            raise RuntimeError("cannot discard a spent or different writer receipt")
        if replaced is not None:
            _write_writer_receipt_locked(receipt_path, replaced)
            return
        receipt_path.unlink()
        _fsync_directory(receipt_path.parent)


def clear_verified_writer_receipt(main_worktree: Path, delivery_id: str,
                                  story_id: str) -> None:
    """Remove a verified local-writer receipt after a successful pause."""
    receipt_path, lock_path = writer_receipt_paths(main_worktree, delivery_id, story_id)
    with receipt_lock(lock_path):
        if not receipt_path.exists():
            return
        receipt = _validate_receipt(json.loads(receipt_path.read_text(encoding="utf-8")))
        if receipt["state"] != "verified":
            raise RuntimeError("cannot clear an unverified writer receipt")
        receipt_path.unlink()
        _fsync_directory(receipt_path.parent)


def provider_receipt_paths(main_worktree: Path, delivery_id: str) -> tuple[Path, Path]:
    validate_delivery_id(delivery_id)
    base = runtime_root(main_worktree) / "provider-receipts"
    name = f"pr-{delivery_id.lower()}.json"
    return base / name, base / f"{name}.lock"


def _validate_provider_receipt(receipt: dict) -> dict:
    required = {"schema_version", "kind", "state", "delivery", "intent_oid",
                "provider", "attempt", "url", "receipt_digest"}
    if set(receipt) != required or receipt.get("schema_version") != 1 or receipt.get("kind") != "pr-create-v1":
        raise RuntimeError("provider receipt schema is unsupported")
    if receipt.get("state") not in {"prepared", "call_started", "verified"}:
        raise RuntimeError("provider receipt state is invalid")
    validate_delivery_id(str(receipt.get("delivery")))
    if receipt.get("provider") != "github" or not EPOCH_RE.fullmatch(str(receipt.get("attempt"))):
        raise RuntimeError("provider receipt provider or attempt is invalid")
    if receipt.get("url") not in {"none", None}:
        canonical_github_pr(str(receipt["url"]))
    expected = dict(receipt)
    digest = expected.pop("receipt_digest")
    if digest != receipt_digest(expected):
        raise RuntimeError("provider receipt digest is invalid")
    return receipt


def _write_provider_receipt_locked(path: Path, receipt: dict) -> dict:
    value = dict(receipt)
    value.pop("receipt_digest", None)
    value["receipt_digest"] = receipt_digest(value)
    _write_writer_receipt_locked(path, value)
    return value


def create_provider_receipt(main_worktree: Path, delivery_id: str,
                            intent_oid: str, attempt: str, *,
                            exact_pr_url: str | None = None) -> dict:
    """Persist the prepared receipt of one PR intent before any provider call.

    A Review published again brings a new intent while the earlier intent's
    receipt stays behind. That receipt gives way unless it still guards a PR
    the provider does not show: a call that started while no exact Delivery
    PR is visible, or a verified PR other than *exact_pr_url*, the one exact
    Delivery PR the provider shows now.
    """
    path, lock = provider_receipt_paths(main_worktree, delivery_id)
    if not OID_RE.fullmatch(intent_oid) or not EPOCH_RE.fullmatch(attempt):
        raise ValueError("provider receipt intent or attempt is invalid")
    value = {"schema_version": 1, "kind": "pr-create-v1", "state": "prepared",
             "delivery": delivery_id, "intent_oid": intent_oid, "provider": "github",
             "attempt": attempt, "url": "none"}
    with receipt_lock(lock):
        if path.exists():
            existing = _validate_provider_receipt(json.loads(path.read_text(encoding="utf-8")))
            if (existing["intent_oid"], existing["attempt"]) == (intent_oid, attempt):
                return existing
            if existing["state"] == "call_started" and exact_pr_url is None:
                raise RuntimeError("DELIVERY_PR_UNCERTAIN: a different provider receipt already exists: "
                                   "its provider call started and no exact Delivery PR is visible")
            if existing["state"] == "verified" and existing["url"] != exact_pr_url:
                raise RuntimeError("DELIVERY_PR_UNCERTAIN: a different provider receipt already exists: "
                                   f"it names {existing['url']}, which is not the exact Delivery PR")
        return _validate_provider_receipt(_write_provider_receipt_locked(path, value))


def mark_provider_call_started(main_worktree: Path, delivery_id: str,
                               intent_oid: str, attempt: str) -> tuple[dict, bool]:
    path, lock = provider_receipt_paths(main_worktree, delivery_id)
    with receipt_lock(lock):
        receipt = _validate_provider_receipt(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else None
        if receipt is None or (receipt["intent_oid"], receipt["attempt"]) != (intent_oid, attempt):
            raise RuntimeError("DELIVERY_PR_UNCERTAIN: provider receipt preimage is missing or stale")
        if receipt["state"] in {"call_started", "verified"}:
            return receipt, False
        receipt["state"] = "call_started"
        return _validate_provider_receipt(_write_provider_receipt_locked(path, receipt)), True


def mark_provider_verified(main_worktree: Path, delivery_id: str,
                           intent_oid: str, attempt: str, url: str) -> dict:
    canonical_url, _ = canonical_github_pr(url)
    path, lock = provider_receipt_paths(main_worktree, delivery_id)
    with receipt_lock(lock):
        receipt = _validate_provider_receipt(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else None
        if receipt is None or (receipt["intent_oid"], receipt["attempt"]) != (intent_oid, attempt):
            raise RuntimeError("DELIVERY_PR_UNCERTAIN: provider receipt preimage is missing or stale")
        receipt["state"] = "verified"
        receipt["url"] = canonical_url
        return _validate_provider_receipt(_write_provider_receipt_locked(path, receipt))


def target_receipt_paths(main_worktree: Path, mode: str) -> tuple[Path, Path]:
    if mode not in {"source_handoff", "governance", "upgrade"}:
        raise ValueError("target receipt mode is unsupported")
    base = runtime_root(main_worktree) / "target-update-receipts"
    name = f"{mode}.json"
    return base / name, base / f"{name}.lock"


def _validate_target_receipt(receipt: dict) -> dict:
    required = {
        "schema_version", "kind", "state", "mode", "attempt", "fence_candidate",
        "intent", "target_repository", "carrier_kind", "carrier_ref",
        "carrier_object", "carrier_head", "carrier_base", "receipt_digest",
    }
    if set(receipt) != required or receipt.get("schema_version") != 1 or receipt.get("kind") != "target-update-v1":
        raise RuntimeError("target update receipt schema is unsupported")
    if receipt["state"] not in {"prepared", "call_started", "verified"}:
        raise RuntimeError("target update receipt state is invalid")
    if receipt["mode"] not in {"source_handoff", "governance", "upgrade"}:
        raise RuntimeError("target update receipt mode is invalid")
    _validate_epoch(str(receipt["attempt"]), "target update attempt")
    if not OID_RE.fullmatch(str(receipt["fence_candidate"])):
        raise RuntimeError("target update receipt Fence candidate is invalid")
    _validate_hash_or_none(str(receipt["intent"]), "target update intent")
    # Validate carrier grammar without making the receipt a second Fence.
    if receipt["target_repository"] == "none" or receipt["carrier_kind"] not in {"github_pr", "direct_target"}:
        raise RuntimeError("target update receipt carrier is incomplete")
    if not receipt["carrier_ref"].startswith("refs/heads/") or receipt["carrier_ref"].startswith("refs/heads/agentrof/"):
        raise RuntimeError("target update receipt carrier ref is invalid")
    if not OID_RE.fullmatch(receipt["carrier_head"]) or not OID_RE.fullmatch(receipt["carrier_base"]):
        raise RuntimeError("target update receipt carrier OID is invalid")
    expected = dict(receipt)
    digest = expected.pop("receipt_digest")
    if digest != receipt_digest(expected):
        raise RuntimeError("target update receipt digest is invalid")
    return receipt


def create_target_update_receipt(main_worktree: Path, mode: str, attempt: str,
                                 fence_candidate: str, intent: str,
                                 target_repository: str, carrier_kind: str,
                                 carrier_ref: str, carrier_object: str,
                                 carrier_head: str, carrier_base: str) -> dict:
    path, lock = target_receipt_paths(main_worktree, mode)
    value = {
        "schema_version": 1, "kind": "target-update-v1", "state": "prepared",
        "mode": mode, "attempt": attempt, "fence_candidate": fence_candidate,
        "intent": intent, "target_repository": target_repository,
        "carrier_kind": carrier_kind, "carrier_ref": carrier_ref,
        "carrier_object": carrier_object, "carrier_head": carrier_head,
        "carrier_base": carrier_base,
    }
    value["receipt_digest"] = receipt_digest(value)
    with receipt_lock(lock):
        if path.exists():
            existing = _validate_target_receipt(json.loads(path.read_text(encoding="utf-8")))
            if existing["attempt"] != attempt:
                raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: a different target update receipt already exists")
            return existing
        _write_provider_receipt_locked(path, value)
        return _validate_target_receipt(json.loads(path.read_text(encoding="utf-8")))


def mark_target_call_started(main_worktree: Path, mode: str, attempt: str) -> dict:
    path, lock = target_receipt_paths(main_worktree, mode)
    with receipt_lock(lock):
        if not path.exists():
            raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: target update receipt is missing")
        value = _validate_target_receipt(json.loads(path.read_text(encoding="utf-8")))
        if value["attempt"] != attempt:
            raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: target update receipt attempt is stale")
        if value["state"] == "prepared":
            value["state"] = "call_started"
            _write_provider_receipt_locked(path, value)
        return _validate_target_receipt(json.loads(path.read_text(encoding="utf-8")))


def release_target_call(main_worktree: Path, mode: str, attempt: str) -> dict:
    """Return this attempt's started update call to prepared once it provably took no effect."""
    path, lock = target_receipt_paths(main_worktree, mode)
    with receipt_lock(lock):
        if not path.exists():
            raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: target update receipt is missing")
        value = _validate_target_receipt(json.loads(path.read_text(encoding="utf-8")))
        if value["attempt"] != attempt or value["state"] != "call_started":
            raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: only this attempt's started call can be released")
        value["state"] = "prepared"
        _write_provider_receipt_locked(path, value)
        return _validate_target_receipt(json.loads(path.read_text(encoding="utf-8")))


def mark_target_verified(main_worktree: Path, mode: str, attempt: str) -> dict:
    path, lock = target_receipt_paths(main_worktree, mode)
    with receipt_lock(lock):
        if not path.exists():
            raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: target update receipt is missing")
        value = _validate_target_receipt(json.loads(path.read_text(encoding="utf-8")))
        if value["attempt"] != attempt:
            raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: target update receipt attempt is stale")
        value["state"] = "verified"
        _write_provider_receipt_locked(path, value)
        return _validate_target_receipt(json.loads(path.read_text(encoding="utf-8")))


def discard_target_update_receipt(main_worktree: Path, mode: str, attempt: str) -> None:
    path, lock = target_receipt_paths(main_worktree, mode)
    with receipt_lock(lock):
        if not path.exists():
            return
        value = _validate_target_receipt(json.loads(path.read_text(encoding="utf-8")))
        if value["state"] != "prepared" or value["attempt"] != attempt:
            raise RuntimeError("cannot discard a spent or different target update receipt")
        path.unlink()
        _fsync_directory(path.parent)


def clear_target_update_receipt(main_worktree: Path, mode: str, attempt: str) -> None:
    path, lock = target_receipt_paths(main_worktree, mode)
    with receipt_lock(lock):
        if not path.exists():
            return
        value = _validate_target_receipt(json.loads(path.read_text(encoding="utf-8")))
        if value["state"] != "verified" or value["attempt"] != attempt:
            raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: cannot clear an unverified or different target update receipt")
        path.unlink()
        _fsync_directory(path.parent)


def materialize_item_worktree(main_worktree: Path, delivery_id: str, story_id: str,
                             candidate_oid: str) -> Path:
    """Create or verify the detached Item worktree after remote activation."""
    path = worktree_paths(main_worktree, delivery_id, story_id)["item"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            current = run_git(main_worktree, "-C", str(path), "rev-parse", "HEAD")
        except RuntimeError as exc:
            raise RuntimeError(f"DELIVERY_WORKTREE_UNSAFE: Item worktree path is occupied: {path}") from exc
        if current != candidate_oid:
            raise RuntimeError(f"DELIVERY_LOCAL_REF_DIVERGED: Item worktree is attached to a different OID: {path}")
        return path
    run_git(main_worktree, "worktree", "add", "--detach", str(path), candidate_oid)
    return path


def remove_item_worktree(main_worktree: Path, delivery_id: str, story_id: str) -> None:
    path = worktree_paths(main_worktree, delivery_id, story_id)["item"]
    if not path.exists():
        return
    from delivery_verification import guard_write
    guard_write(path)
    run_git(main_worktree, "worktree", "remove", str(path))


def split_remote_note(root: Path, oid: str, relative_path: str,
                      split_note_fn) -> tuple[dict, str]:
    """Parse a tracked Markdown note from the exact remote Item tree."""
    text = run_git(root, "show", f"{oid}:{relative_path}")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", suffix=".md",
                                     delete=False) as temporary:
        temporary.write(text)
        temporary_path = Path(temporary.name)
    try:
        return split_note_fn(temporary_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def worktree_is_clean_and_at(root: Path, path: Path, expected_oid: str) -> None:
    from delivery_verification import guard_write
    guard_write(path)
    if not path.is_dir():
        raise RuntimeError(f"DELIVERY_WORKTREE_UNSAFE: Item worktree is missing: {path}")
    head = run_git(root, "-C", str(path), "rev-parse", "HEAD")
    if head != expected_oid:
        raise RuntimeError("DELIVERY_LOCAL_REF_DIVERGED: Item worktree HEAD differs from the remote Item tip")
    dirty = run_git(root, "-C", str(path), "status", "--porcelain", "--untracked-files=all")
    if dirty:
        raise RuntimeError("DELIVERY_WORKTREE_UNSAFE: clean the Item worktree before pause")


def worktree_head(root: Path, path: Path) -> str:
    if not path.is_dir():
        raise RuntimeError(f"DELIVERY_WORKTREE_UNSAFE: Item worktree is missing: {path}")
    head = run_git(root, "-C", str(path), "rev-parse", "HEAD")
    if not OID_RE.fullmatch(head):
        raise RuntimeError("DELIVERY_WORKTREE_UNSAFE: Item worktree has no valid HEAD")
    return head


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=root,
        encoding="utf-8", capture_output=True, check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise RuntimeError(result.stderr.strip() or "cannot compare Git ancestry")


def require_visible_item_index(worktree: Path) -> None:
    """Refuse index flags that can hide different tested bytes from Git status."""
    entries = git_paths(worktree, "ls-files", "-v", "-z", failure="cannot inspect Item index flags")
    hidden = [entry[2:] for entry in entries if entry[0] == "S" or entry[0].islower()]
    if hidden:
        raise RuntimeError("DELIVERY_WORKTREE_UNSAFE: Item index flags hide tracked paths from verification: "
                           + json.dumps(sorted(hidden), ensure_ascii=False))


def worktree_pending_paths(root: Path, path: Path) -> set[str]:
    """Return every tracked or untracked path not yet committed in one Item worktree."""
    pending = set()
    for args in (("diff", "--name-only", "-z", "HEAD"),
                 ("diff", "--cached", "--name-only", "-z", "HEAD"),
                 ("ls-files", "-z", "--others", "--exclude-standard")):
        pending.update(git_paths(root, "-C", str(path), *args, failure="cannot inspect pending Item paths"))
    return pending


def worktree_holds_blob(worktree: Path, relative: str, oid: str) -> bool:
    """Whether Git would store the worktree file at *relative* as exactly blob *oid*.

    Git hashes the file through its clean filter, as git add does, so a checkout
    that converted line endings, as core.autocrlf does by default on native
    Windows, still holds the blob it came from. git add keeps a file whose blob
    already holds CRLF as it is, where the clean filter alone would store LF, so
    a file that holds the blob's own bytes holds it as well. A link or any other
    file that is not regular holds no blob.
    """
    path = worktree / relative
    if path.is_symlink() or not path.is_file():
        return False
    if run_git(worktree, "hash-object", "--path", relative, "--", str(path)) == oid:
        return True
    return run_git(worktree, "hash-object", "--no-filters", "--", str(path)) == oid


def require_candidate_holds_worktree(root: Path, path: Path, candidate_oid: str) -> None:
    """Prove that the candidate already contains the worktree's bytes, so moving there loses none."""
    if worktree_pending_paths(root, path):
        differing = sorted(git_paths(path, "diff", "--name-only", "-z", candidate_oid, "--",
                                     failure="cannot compare Item worktree to candidate"))
        if differing:
            raise RuntimeError("DELIVERY_WORKTREE_UNSAFE: the Item candidate does not contain the current worktree "
                               "bytes of " + ", ".join(differing))


def advance_worktree_to_candidate(root: Path, path: Path, candidate_oid: str) -> None:
    """Move a worktree only after proving the candidate already contains its bytes."""
    from delivery_verification import guard_write
    guard_write(path)
    require_candidate_holds_worktree(root, path, candidate_oid)
    run_git(root, "-C", str(path), "reset", "--hard", candidate_oid)


def active_writer_receipt(root: Path, delivery_id: str, story_id: str,
                          item_oid: str, slot_ref: str) -> dict:
    """Prove this machine still owns the active remote Item/Slot pair."""
    receipt = read_writer_receipt(root, delivery_id, story_id)
    refs = canonical_refs(delivery_id, story_id)
    if receipt is None or receipt.get("state") != "verified":
        raise RuntimeError("DELIVERY_WRITER_RECEIPT_MISSING: push-item requires this machine's verified Item writer receipt")
    if receipt.get("item_ref") != refs["item"] or receipt.get("slot_ref") != slot_ref:
        raise RuntimeError("DELIVERY_WRITER_RECEIPT_STALE: Item writer receipt does not match the current Item Slot pair")
    candidate = str(receipt.get("candidate_oid", ""))
    if not is_ancestor(root, candidate, item_oid):
        raise RuntimeError("DELIVERY_WRITER_RECEIPT_STALE: Item writer receipt is stale against the remote Item tip")
    return receipt


def git_paths(root: Path, *args: str, failure: str | None = None) -> list[str]:
    """Run a Git listing that ends each path with NUL and return each path as Git holds it.

    Without -z Git quotes a name holding a control character, a double quote or a
    backslash, and under its default core.quotePath any byte outside ASCII. A
    text-mode pipe would also turn a carriage return into a newline. So the paths
    come from the NUL-separated bytes, decoded as UTF-8.
    """
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip()
                           or failure or f"git {' '.join(args)} failed")
    return [path.decode("utf-8") for path in result.stdout.split(b"\0") if path]


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, encoding="utf-8",
                            capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def git_with_input(root: Path, args: list[str], data: str,
                   env: dict | None = None) -> subprocess.CompletedProcess:
    """Run Git with *data* on its stdin as exact UTF-8 bytes.

    A text-mode pipe writes os.linesep for every newline and encodes in the
    locale's code page, so on native Windows a blob or commit message handed to
    Git that way would hold CRLF and ANSI bytes that no other host writes.
    """
    result = subprocess.run(["git", *args], cwd=root, env=env, input=data.encode("utf-8"),
                            capture_output=True, check=False)
    return subprocess.CompletedProcess(result.args, result.returncode,
                                       result.stdout.decode("utf-8"),
                                       result.stderr.decode("utf-8", "replace"))


def commit_tree(root: Path, base: str, paths: list[str], subject: str,
                trailers: dict[str, str], *, delivery_projections: bool = False,
                operation_bindings: dict[str, dict] | None = None,
                blobs: dict[str, str] | None = None, body: str | None = None) -> str:
    """Create an unreferenced candidate tree from *base* plus exact paths.

    *blobs* maps further paths to the existing blob each one carries, whatever
    the checkout holds there. *body* is a paragraph between the subject and the
    trailers, for a record whose value is a list a trailer cannot repeat.
    """
    trailers = _normalise_control_trailers(trailers)
    with tempfile.TemporaryDirectory(prefix="agentrof-index-") as temporary:
        index = Path(temporary) / "index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        read = subprocess.run(["git", "read-tree", base], cwd=root, env=env,
                              encoding="utf-8", capture_output=True, check=False)
        if read.returncode:
            raise RuntimeError(read.stderr.strip() or "cannot materialize candidate index")
        if paths:
            add = subprocess.run(["git", "add", "--", *paths], cwd=root, env=env,
                                 encoding="utf-8", capture_output=True, check=False)
            if add.returncode:
                raise RuntimeError(add.stderr.strip() or "cannot stage candidate package")
        update_candidate_index(root, env, [("100644", oid, path) for path, oid in sorted((blobs or {}).items())])
        tree = subprocess.run(["git", "write-tree"], cwd=root, env=env,
                              encoding="utf-8", capture_output=True, check=False)
        if tree.returncode:
            raise RuntimeError(tree.stderr.strip() or "cannot write candidate tree")
        if delivery_projections:
            projected = write_delivery_projection_tree(root, env, tree.stdout.strip(), operation_bindings)
        else:
            projected = tree.stdout.strip()
        message = subject + "\n\n" + (body + "\n\n" if body else "") + "\n".join(
            f"Agentrof-{key}: {value}" for key, value in trailers.items()
        ) + "\n"
        commit = git_with_input(root, ["commit-tree", projected, "-p", base], message, env)
        if commit.returncode:
            raise RuntimeError(commit.stderr.strip() or "cannot create candidate commit")
        return commit.stdout.strip()


def update_candidate_index(root: Path, env: dict, entries: list[tuple[str, str, str]]) -> None:
    """Apply exact blob entries in one process without quoting Git path names."""
    if not entries:
        return
    index = env.get("GIT_INDEX_FILE", "")
    if not index or not Path(index).is_absolute() or Path(index).resolve() == (root / ".git/index").resolve():
        raise RuntimeError("candidate updates require an isolated temporary index")
    if len({path for _mode, _oid, path in entries}) != len(entries) or any(
            mode not in {"0", "100644", "100755"} or not isinstance(oid, str)
            or len(oid) not in {40, 64} or not OID_RE.fullmatch(oid)
            or (mode == "0" and set(oid) != {"0"}) or not isinstance(path, str) or not path or "\0" in path
            or any(part in {"", ".", ".."} for part in path.split("/"))
            for mode, oid, path in entries):
        raise RuntimeError("invalid candidate index entry")
    records = b"".join(mode.encode("ascii") + b" " + oid.encode("ascii")
                       + b"\t" + path.encode("utf-8") + b"\0"
                       for mode, oid, path in entries)
    result = subprocess.run(["git", "update-index", "-z", "--index-info"],
                            cwd=root, env=env, input=records, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip()
                           or "cannot stage candidate replacements")


def write_delivery_projection_tree(root: Path, env: dict, tree: str,
                                   operation_bindings: dict[str, dict] | None = None) -> str:
    entries = []
    for path, (mode, content) in delivery_projection_changes(root, tree, operation_bindings).items():
        if content is None:
            entries.append(("0", "0" * len(tree), path))
        else:
            blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=root,
                                  input=content, capture_output=True, check=True)
            entries.append((mode, blob.stdout.decode("ascii").strip(), path))
    update_candidate_index(root, env, entries)
    return subprocess.run(["git", "write-tree"], cwd=root, env=env,
                          encoding="utf-8", capture_output=True, check=True).stdout.strip()


def delivery_projection_changes(root: Path, tree: str,
                                operation_bindings: dict[str, dict] | None = None) -> dict[str, tuple[str, bytes | None]]:
    """Derive only owned projections from raw candidate blobs, never local notes."""
    import vault_check
    from delivery_compile import render_map

    listing = subprocess.run(["git", "--no-replace-objects", "ls-tree", "-rz", tree, "--", "workspace/docs/"],
                             cwd=root, capture_output=True, check=True).stdout
    entries = []
    for row in listing.split(b"\0"):
        if not row:
            continue
        metadata, path = row.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise RuntimeError("Delivery publication requires regular vault files")
        entries.append((path.decode("utf-8"), mode, oid))
    objects = subprocess.run(["git", "--no-replace-objects", "cat-file", "--batch"], cwd=root,
                             input="".join(oid + "\n" for _, _, oid in entries).encode(),
                             capture_output=True, check=True).stdout
    originals: dict[str, tuple[str, bytes]] = {}
    offset = 0
    with tempfile.TemporaryDirectory(prefix="agentrof-delivery-projections-") as temporary:
        candidate = Path(temporary)
        for path, mode, oid in entries:
            end = objects.index(b"\n", offset)
            actual_oid, kind, size = objects[offset:end].decode().split()
            length = int(size)
            if actual_oid != oid or kind != "blob" or length < 0:
                raise RuntimeError("cannot read exact Delivery candidate blob")
            offset = end + 1
            content = objects[offset:offset + length]
            offset += length
            if len(content) != length or objects[offset:offset + 1] != b"\n":
                raise RuntimeError("truncated Delivery candidate blob")
            offset += 1
            originals[path] = (mode, content)
            target = candidate / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        if offset != len(objects):
            raise RuntimeError("unexpected trailing Delivery candidate objects")
        docs = candidate / "workspace" / "docs"
        render_map(docs)
        policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
        vault = vault_check.build_vault(docs, vault_check.effective_policy(policy, docs))
        findings = []
        vault_check.check_relation_contract(vault, findings)
        if findings:
            raise RuntimeError("Delivery candidate relations are invalid: " + "; ".join(
                f"{finding.path}: {finding.message}" for finding in findings))
        blocks, catalogs = vault_check.relation_projection(vault)
        rendered = {"maps/delivery.md": (docs / "maps/delivery.md").read_text(encoding="utf-8")}
        for note in vault_check.authored(vault):
            current = note.path.read_text(encoding="utf-8")
            block = blocks.get(note.rel, "")
            if vault_check.relation_block(current) != block:
                rendered[note.rel] = vault_check.replace_relation_block(current, block)
        catalog_root = str(policy.get("relation_contract", {}).get(
            "catalog_root", "maps/_relations")).rstrip("/")
        removed = {rel for rel in vault.index
                   if rel.startswith(catalog_root + "/") and rel.endswith(".md")} - set(catalogs)
        rendered.update(catalogs)
        rendered.update(vault_check.relation_reports(vault))
        changes = {}
        for rel, content in rendered.items():
            path = "workspace/docs/" + rel
            previous_mode, previous = originals.get(path, ("100644", None))
            encoded = content.encode("utf-8")
            if previous != encoded:
                changes[path] = (previous_mode, encoded)
        for rel in removed:
            changes["workspace/docs/" + rel] = ("100644", None)
        if operation_bindings is not None:
            from delivery_compile import (TERMINAL_ITEM_STATUSES,
                                          item_operation_findings, split_note)
            for path, (_mode, content) in changes.items():
                target = candidate / path
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
            for relative, expected in operation_bindings.items():
                actual, _body = split_note(docs / relative)
                if any(actual.get(key) != value for key, value in expected.items()):
                    raise RuntimeError(f"Delivery candidate changed approved Operation bindings: {relative}")
                # The binding is still pinned above; a terminal Item's names an earlier
                # approved revision and is not compared against the current one.
                if actual.get("status") in TERMINAL_ITEM_STATUSES:
                    continue
                errors = item_operation_findings(docs, actual)
                if errors:
                    raise RuntimeError("DELIVERY_PLAN_STALE: Delivery candidate Operation bindings are invalid: " + "; ".join(errors))
        return changes


def commit_replacements(root: Path, base: str, replacements: dict[str, str],
                        subject: str, trailers: dict[str, str], *,
                        parents: tuple[str, ...] | None = None,
                        delivery_projections: bool = False) -> str:
    """Create a candidate from *base* with exact in-memory file replacements.

    The candidate's tree is *base* plus the replacements; its parents default to
    *base* alone. Passing *parents* records a different lineage for the same tree,
    which is how a sealed Item reopens on the Integration that absorbed it.
    *delivery_projections* derives the Delivery map and relation projections from
    that tree, as ``commit_tree`` does for every other Integration publication.
    """
    trailers = _normalise_control_trailers(trailers)
    parent_args = [arg for parent in (parents or (base,)) for arg in ("-p", parent)]
    projected = replacements_tree(root, base, replacements, delivery_projections=delivery_projections)
    message = subject + "\n\n" + "\n".join(
        f"Agentrof-{key}: {value}" for key, value in trailers.items()
    ) + "\n"
    commit = git_with_input(root, ["commit-tree", projected, *parent_args], message, os.environ.copy())
    if commit.returncode:
        raise RuntimeError(commit.stderr.strip() or "cannot create candidate commit")
    return commit.stdout.strip()


def replacements_tree(root: Path, base: str, replacements: dict[str, str], *,
                      delivery_projections: bool = False) -> str:
    """The tree of *base* with exact in-memory file replacements and, optionally, derived projections.

    It writes blobs and trees only, so a checkout without a Git identity can
    compute it.
    """
    with tempfile.TemporaryDirectory(prefix="agentrof-index-") as temporary:
        index = Path(temporary) / "index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        read = subprocess.run(["git", "read-tree", base], cwd=root, env=env,
                              encoding="utf-8", capture_output=True, check=False)
        if read.returncode:
            raise RuntimeError(read.stderr.strip() or "cannot materialize candidate index")
        entries = []
        for path, text in replacements.items():
            blob = git_with_input(root, ["hash-object", "-w", "--stdin"], text, env)
            if blob.returncode:
                raise RuntimeError(blob.stderr.strip() or "cannot write candidate blob")
            entries.append(("100644", blob.stdout.strip(), path))
        update_candidate_index(root, env, entries)
        tree = subprocess.run(["git", "write-tree"], cwd=root, env=env,
                              encoding="utf-8", capture_output=True, check=False)
        if tree.returncode:
            raise RuntimeError(tree.stderr.strip() or "cannot write candidate tree")
        return (write_delivery_projection_tree(root, env, tree.stdout.strip())
                if delivery_projections else tree.stdout.strip())


def epoch_token() -> str:
    return base64.urlsafe_b64encode(os.urandom(16)).decode("ascii").rstrip("=")


def remote_has_ref(root: Path, remote: str, ref: str) -> bool:
    output = run_git(root, "ls-remote", remote, ref)
    return bool(output.strip())


# The finding-code prefix an absent coordination ref reports, by the ref family it names.
ABSENT_REF_PREFIXES = (
    ("refs/heads/agentrof/fence", "DELIVERY_FENCE_MISSING: "),
    ("refs/heads/agentrof/items/", "DELIVERY_ITEM_REF_MISSING: "),
    ("refs/heads/agentrof/slots/", "DELIVERY_ITEM_SLOT_MISSING: "),
)


def remote_oid(root: Path, remote: str, ref: str) -> str:
    output = run_git(root, "ls-remote", remote, ref)
    if not output:
        code = next((prefix for family, prefix in ABSENT_REF_PREFIXES if ref.startswith(family)), "")
        raise RuntimeError(f"{code}remote ref is absent: {ref}")
    return output.split()[0]


def commit_message(root: Path, oid: str) -> str:
    return run_git(root, "show", "-s", "--format=%B", oid)


def trailer(message: str, key: str) -> str | None:
    prefix = f"Agentrof-{key}:"
    for line in message.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def _validate_epoch(value: str, label: str = "epoch") -> str:
    if not EPOCH_RE.fullmatch(value):
        raise RuntimeError(f"DELIVERY_FENCE_CORRUPT: {label} must be a 22-character base64url token")
    return value


def _validate_hash_or_none(value: str, label: str) -> str:
    if value != "none" and not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise RuntimeError(f"DELIVERY_FENCE_CORRUPT: {label} must be none or a sha256 digest")
    return value


def _validate_fence_values(values: dict[str, str]) -> dict[str, str]:
    """Validate the canonical project-fence projection before any mutation."""
    missing = [key for key in FENCE_CANONICAL_KEYS if key not in values]
    if missing:
        raise RuntimeError("DELIVERY_FENCE_CORRUPT: missing fence fields " + ", ".join(missing))
    if values["Mode"] not in FENCE_MODES:
        raise RuntimeError("DELIVERY_FENCE_MODE: unsupported Fence mode")
    _validate_epoch(values["Epoch"])
    if not OID_RE.fullmatch(values["Target"]):
        raise RuntimeError("DELIVERY_FENCE_CORRUPT: Target must be an object ID")
    _validate_hash_or_none(values["Governance-Hash"], "Governance-Hash")
    if values["Source-Kind"] not in SOURCE_KINDS:
        raise RuntimeError("DELIVERY_FENCE_CORRUPT: Source-Kind is unsupported")
    _validate_hash_or_none(values["Source-Intent"], "Source-Intent")
    _validate_hash_or_none(values["Target-Update-Intent"], "Target-Update-Intent")
    attempt = values["Target-Update-Attempt"]
    if attempt != "none":
        _validate_epoch(attempt, "Target-Update-Attempt")
    repository = values["Target-Repository"]
    if repository not in {"none", "upstream"} and not re.fullmatch(r"github:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise RuntimeError("DELIVERY_FENCE_CORRUPT: Target-Repository is unsupported")
    carrier_kind = values["Target-Carrier-Kind"]
    if carrier_kind not in CARRIER_KINDS:
        raise RuntimeError("DELIVERY_FENCE_CORRUPT: Target-Carrier-Kind is unsupported")
    carrier_fields = (
        values["Target-Carrier-Ref"], values["Target-Carrier-Object"],
        values["Target-Carrier-Head"], values["Target-Carrier-Base"],
    )
    if carrier_kind == "none":
        if any(value != "none" for value in carrier_fields) or repository != "none":
            raise RuntimeError("DELIVERY_FENCE_CORRUPT: absent carrier has non-none fields")
    else:
        if repository == "none" or not values["Target-Carrier-Ref"].startswith("refs/heads/"):
            raise RuntimeError("DELIVERY_TARGET_CARRIER_INVALID: incomplete carrier binding")
        if values["Target-Carrier-Ref"].startswith("refs/heads/agentrof/"):
            raise RuntimeError("DELIVERY_TARGET_CARRIER_INVALID: coordination refs cannot be carriers")
        if not OID_RE.fullmatch(values["Target-Carrier-Head"]) or not OID_RE.fullmatch(values["Target-Carrier-Base"]):
            raise RuntimeError("DELIVERY_TARGET_CARRIER_INVALID: carrier head/base must be object IDs")
        if carrier_kind == "github_pr" and not re.fullmatch(r"pr:[1-9][0-9]*", values["Target-Carrier-Object"]):
            raise RuntimeError("DELIVERY_TARGET_CARRIER_INVALID: GitHub carrier object is invalid")
        if carrier_kind == "direct_target" and values["Target-Carrier-Object"] != "direct":
            raise RuntimeError("DELIVERY_TARGET_CARRIER_INVALID: direct carrier object is invalid")
    if values["Upgrade-Phase"] not in UPGRADE_PHASES:
        raise RuntimeError("DELIVERY_FENCE_CORRUPT: Upgrade-Phase is unsupported")
    _validate_hash_or_none(values["Upgrade-Contract"], "Upgrade-Contract")
    handoff_target = values["Handoff-Target"]
    if handoff_target != "none" and not OID_RE.fullmatch(handoff_target):
        raise RuntimeError("DELIVERY_FENCE_CORRUPT: Handoff-Target must be an object ID")
    if values["Mode"] == "source_handoff" and values["Source-Kind"] == "none":
        raise RuntimeError("DELIVERY_FENCE_CORRUPT: source_handoff requires Source-Kind")
    if values["Target-Update-Intent"] != "none" and values["Target-Carrier-Kind"] == "none":
        raise RuntimeError("DELIVERY_TARGET_CARRIER_INVALID: target intent has no carrier")
    if values["Mode"] == "upgrade":
        if values["Upgrade-Phase"] == "none" or values["Upgrade-Contract"] == "none":
            raise RuntimeError("DELIVERY_FENCE_CORRUPT: upgrade mode requires an upgrade contract and phase")
    elif any(values[key] != "none" for key in ("Upgrade-Phase", "Upgrade-Contract", "Handoff-Target")):
        raise RuntimeError("DELIVERY_FENCE_CORRUPT: upgrade fields outside upgrade mode")
    return values


def atomic_push(root: Path, remote: str, updates: list[tuple[str, str, str]]) -> None:
    args = ["push", "--atomic", remote]
    for ref, expected, candidate in updates:
        args.append(f"--force-with-lease={ref}:{expected}")
        args.append(f"{candidate}:{ref}")
    result = subprocess.run(["git", *args], cwd=root, encoding="utf-8",
                            capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(refused_transaction(root, remote, updates)
                           or result.stderr.strip() or "atomic remote transaction rejected")


def remote_ref_oids(root: Path, remote: str, refs: list[str]) -> dict[str, str]:
    """Each named ref's exact object ID on the remote, or "" where the remote has none."""
    values = dict.fromkeys(refs, "")
    for line in run_git(root, "ls-remote", remote, *refs).splitlines():
        oid, _tab, name = line.partition("\t")
        if name in values:
            values[name] = oid
    return values


def history_holds(root: Path, remote: str, ref: str, oid: str, candidate: str) -> bool:
    """Whether commit *oid*, which the remote's *ref* holds, is *candidate* or descends from it.

    Another host may have moved the ref to a commit this checkout lacks, so the
    ref is fetched before its history is read.
    """
    if oid == candidate:
        return True
    if subprocess.run(["git", "cat-file", "-e", oid + "^{commit}"], cwd=root,
                      capture_output=True, check=False).returncode:
        run_git(root, "fetch", "--no-tags", remote, ref)
    return is_ancestor(root, candidate, oid)


def item_claim(root: Path, remote: str, delivery_id: str, story: str) -> tuple[str, str]:
    """Return the tip of *story*'s Item ref and the Delivery its trailer names, or ("", "").

    Item refs are named by Story alone, so the ref can hold another Delivery's
    claim of the same Story.
    """
    ref = canonical_refs(delivery_id, story)["item"]
    tip = remote_ref_oids(root, remote, [ref])[ref]
    if not tip:
        return "", ""
    if subprocess.run(["git", "cat-file", "-e", tip + "^{commit}"], cwd=root,
                      capture_output=True, check=False).returncode:
        # Another Delivery's Item advances on its own hosts.
        run_git(root, "fetch", "--no-tags", remote, ref)
    return tip, trailer(commit_message(root, tip), "Delivery") or ""


def own_item_tip(root: Path, remote: str, delivery_id: str, story: str) -> str:
    """Return *story*'s Item tip when this Delivery holds the claim, else "".

    A claim another Delivery holds is not this Delivery's Item: its tip holds
    no package of this Delivery, and re-issuing it would take the Story over.
    It counts as absent here, and claim-items refuses the Story.
    """
    tip, owner = item_claim(root, remote, delivery_id, story)
    return tip if owner == delivery_id else ""


def push_never_landed(root: Path, remote: str, ref: str, candidate: str) -> bool:
    """Whether the refetched *ref* proves that a push of *candidate* to it never landed.

    A ref whose history holds the candidate may have taken it, and a ref that
    cannot be read proves nothing.
    """
    try:
        oid = remote_ref_oids(root, remote, [ref])[ref]
        return not oid or not history_holds(root, remote, ref, oid, candidate)
    except RuntimeError:
        return False


def refused_transaction(root: Path, remote: str, updates: list[tuple[str, str, str]]) -> str | None:
    """Name why the remote refused an atomic transaction, or None when that is unproven.

    Git words its refusals in the reader's language, so only exit statuses and
    refetched object IDs decide. A leased ref that holds its candidate, or moved
    on from a history that holds it, shows that the remote may have taken the
    transaction before its response was lost, so the result is uncertain, as
    is a transaction whose every ref holds its candidate. Otherwise a ref that
    no longer holds its leased value lost the transaction; the Fence is named
    apart because it serializes every coordinator. While every lease holds, a
    remote that takes the same no-op push without --atomic but not with it
    lacks atomic push support.
    """
    try:
        observed = remote_ref_oids(root, remote, [ref for ref, _expected, _candidate in updates])
        landed = sorted((ref, candidate) for ref, expected, candidate in updates
                        if candidate and observed[ref] not in ("", expected)
                        and history_holds(root, remote, ref, observed[ref], candidate))
    except RuntimeError:
        return None
    if not landed and all(observed[ref] == candidate for ref, _expected, candidate in updates):
        landed = sorted((ref, candidate) for ref, _expected, candidate in updates)
    if landed:
        def evidence(ref: str, candidate: str) -> str:
            if not candidate:
                return f"{ref} is absent, as pushed"
            if observed[ref] == candidate:
                return f"{ref} holds the pushed {candidate}"
            return f"{ref} is {observed[ref]}, whose history holds the pushed {candidate}"
        return ("DELIVERY_TRANSACTION_UNCERTAIN: the remote may have taken the atomic push before its "
                "response was lost, so read the refs again before any retry: "
                + "; ".join(evidence(ref, candidate) for ref, candidate in landed))
    moved = sorted((ref, expected) for ref, expected, _candidate in updates if observed[ref] != expected)
    detail = "; ".join(f"{ref} is {observed[ref] or 'absent'}, leased as {expected or 'absent'}"
                       for ref, expected in moved)
    if any(ref == canonical_refs("DLV-000")["fence"] for ref, _expected in moved):
        return "DELIVERY_FENCE_LEASE_LOST: the project Fence moved, so the atomic push changed no ref: " + detail
    if moved:
        return "DELIVERY_LEASE_LOST: a leased ref moved, so the atomic push changed no ref: " + detail
    held = next(((ref, expected) for ref, expected, _candidate in updates if expected), None)
    if held is not None and refuses_atomic_push(root, remote, *held):
        return ("DELIVERY_REMOTE_ATOMIC_UNSUPPORTED: the remote takes a push only without --atomic, "
                "which every Delivery transaction needs; no ref changed")
    return None


def refuses_atomic_push(root: Path, remote: str, ref: str, oid: str) -> bool:
    """Whether the remote takes a no-op push of *ref* at its current *oid* only without --atomic."""
    def dry_run(*flags: str) -> int:
        return subprocess.run(["git", "push", "--dry-run", "--no-verify", *flags, remote, f"{oid}:{ref}"],
                              cwd=root, capture_output=True, check=False).returncode
    return dry_run("--atomic") != 0 and dry_run() == 0


def _normalise_control_trailers(trailers: dict[str, str]) -> dict[str, str]:
    """Complete the canonical Fence projection at the commit boundary."""
    if trailers.get("Record") != "project-fence-v2":
        return trailers
    value = dict(trailers)
    for key in FENCE_CANONICAL_KEYS:
        if key not in value:
            value[key] = "none"
    value.setdefault("Source-Kind", "none")
    value.setdefault("Target-Repository", "none")
    value.setdefault("Target-Carrier-Kind", "none")
    value.setdefault("Target-Carrier-Ref", "none")
    value.setdefault("Target-Carrier-Object", "none")
    value.setdefault("Target-Carrier-Head", "none")
    value.setdefault("Target-Carrier-Base", "none")
    value.setdefault("Upgrade-Phase", "none")
    value.setdefault("Upgrade-Contract", "none")
    value.setdefault("Handoff-Target", "none")
    return value


class TargetBranchUnresolved(RuntimeError):
    """Neither a recorded branch, the remote's default branch nor a current branch names the target."""


def default_target_branch(root: Path, remote: str) -> str:
    """The remote's default branch from local refs alone, else the current branch; empty when detached."""
    try:
        symbolic = run_git(root, "symbolic-ref", f"refs/remotes/{remote}/HEAD")
        return symbolic.removeprefix(f"refs/remotes/{remote}/")
    except RuntimeError:
        return run_git(root, "branch", "--show-current")


def checked_target_branch(value) -> str | None:
    """A recorded target branch name, None when nothing is recorded; a name Git cannot use refuses."""
    if value is None or value == "":
        return None
    if (not isinstance(value, str) or not TARGET_BRANCH_RE.fullmatch(value) or ".." in value
            or "//" in value or value.endswith((".lock", "/", ".")) or "/." in value):
        raise RuntimeError(f"DELIVERY_TARGET_INVALID: target_branch {value!r} is not a branch name")
    return value


def resolve_target_branch(root: Path, remote: str, *, recorded: str | None) -> str:
    """Name the target branch from local refs alone.

    The branch a Delivery records answers where its remote-tracking ref
    exists; otherwise the remote's default branch does, else the current one.
    *recorded* is required so that every caller names the record it targets,
    or None where no Delivery applies.
    """
    branch = default_target_branch(root, remote)
    if recorded and recorded != branch and subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", f"refs/remotes/{remote}/{recorded}"],
            capture_output=True, check=False).returncode == 0:
        return recorded
    if not branch:
        raise TargetBranchUnresolved("target branch cannot be resolved")
    return branch


def recorded_target_branch(root: Path, delivery_id: str) -> str | None:
    """The target branch the local package of *delivery_id* records, or None without one."""
    from delivery_compile import docs_root, find_delivery, split_note
    directory = find_delivery(docs_root(root), delivery_id)
    if directory is None:
        return None
    try:
        props, _ = split_note(directory / "delivery.md")
    except (OSError, ValueError):
        return None
    return checked_target_branch(props.get("target_branch"))


def open_target_branch(root: Path, remote: str, recorded: str | None = None) -> str | None:
    """The one target branch every open Delivery records, together with *recorded*, or None.

    The project Fence names one target commit, so every open Delivery shares
    one target branch; a split refuses instead of letting a Fence-level verb
    move the target of one Delivery to another's branch.
    """
    from delivery_closure import ensure_object, listed_refs, package_directory, tree_note
    branches = {recorded} if recorded else set()
    for ref, oid in sorted(listed_refs(root, remote, "refs/heads/agentrof/deliveries/*").items()):
        ensure_object(root, remote, oid, ref)
        directory = package_directory(root, oid, ref.rsplit("/", 1)[1].upper())
        if directory:
            branch = checked_target_branch(tree_note(root, oid, f"{directory}/delivery.md")[0].get("target_branch"))
            if branch:
                branches.add(branch)
    if len(branches) > 1:
        raise RuntimeError("DELIVERY_TARGET_SPLIT: open Deliveries record the target branches "
                           + ", ".join(sorted(branches)) + ", but the project Fence names one target; finish or"
                           " cancel the Deliveries on one branch before a Delivery on another is reserved")
    return next(iter(branches), None)


def resolve_target(root: Path, remote: str, *, recorded: str | None) -> tuple[str, str]:
    """The target branch and its remote tip; see resolve_target_branch for *recorded*."""
    try:
        branch = resolve_target_branch(root, remote, recorded=recorded)
    except TargetBranchUnresolved:
        if not recorded:
            raise
        branch = ""
    if recorded and recorded != branch:
        # The remote can hold the recorded branch before any fetch tracks it.
        recorded_line = run_git(root, "ls-remote", remote, f"refs/heads/{recorded}")
        if recorded_line:
            return recorded, recorded_line.split()[0]
        if not branch:
            raise TargetBranchUnresolved("target branch cannot be resolved")
    remote_line =run_git(root, "ls-remote", remote, f"refs/heads/{branch}")
    if remote_line:
        oid = remote_line.split()[0]
    else:
        oid = run_git(root, "rev-parse", f"refs/remotes/{remote}/{branch}")
    return branch, oid


def canonical_github_pr(value: str) -> tuple[str, str]:
    parsed = urlsplit(value)
    match = GITHUB_PR_RE.fullmatch(parsed.path)
    if (parsed.scheme != "https" or parsed.netloc != "github.com" or
            parsed.query or parsed.fragment or parsed.username or parsed.port or
            match is None):
        raise ValueError("PR URL must be canonical https://github.com/<owner>/<repo>/pull/<number>")
    owner, repo, number = match.group(1), match.group(2), match.group(3)
    return f"https://github.com/{owner}/{repo}/pull/{number}", number


def pr_url_hash(url: str) -> str:
    """The URL-Hash trailer value that binds a canonical PR URL in a control record."""
    return "sha256:" + hashlib.sha256(url.encode("utf-8")).hexdigest()


def binds_pr(message: str, url: str) -> bool:
    """Whether a control record's Pull-Request and URL-Hash trailers name the canonical PR *url*."""
    _, number = canonical_github_pr(url)
    return (trailer(message, "Pull-Request"), trailer(message, "URL-Hash")) == (number, pr_url_hash(url))


def recorded_pr_url(root: Path, record: str, review_path: Path) -> str:
    """Return the PR URL that the PR record *record* carries.

    The record is the only source of the URL: its published Review names the
    PR and its trailers bind it. A local Review only mirrors the URL, and a
    Delivery cancelled before it had one has none.
    """
    from delivery_compile import split_note
    props, _ = split_remote_note(root, record, rel_posix(root, review_path), split_note)
    canonical_url, _ = canonical_github_pr(str(props.get("pull_request_url", "")))
    if not binds_pr(commit_message(root, record), canonical_url):
        raise RuntimeError("DELIVERY_COORDINATION_CORRUPT: the PR record's Review names a PR its trailers do not bind")
    return canonical_url


def merged_record(root: Path, remote: str, delivery_id: str) -> str | None:
    """Return the recorded PR head of *delivery_id* that the fetched target merged, or None.

    A merged Delivery drops its Integration ref, so its PR record is then found
    in the target history by the proof the Delivery compiler uses.
    """
    from delivery_compile import merged_pr_record
    _branch, target = fetch_target(root, remote, recorded=recorded_target_branch(root, delivery_id))
    return merged_pr_record(root, delivery_id, target)


def integration_record(root: Path, remote: str, delivery_id: str) -> str:
    """Return the Integration tip, or the merged PR record once the merge dropped that ref."""
    ref = canonical_refs(delivery_id)["integration"]
    tip = remote_ref_oids(root, remote, [ref])[ref]
    if tip:
        return tip
    record = merged_record(root, remote, delivery_id)
    if record is None:
        raise RuntimeError(f"remote ref is absent: {ref}")
    return record


def drop_merged_refs(root: Path, remote: str, delivery_id: str, record: str) -> list[str]:
    """Delete the refs of a Delivery whose recorded PR head *record* the target merged.

    The target's copy of the package records the closed Delivery, so its
    Integration ref and the Item refs of its integrated Stories go in one
    atomic transaction. A cancelled Story keeps its Item ref, the lock that
    keeps any other Delivery from claiming it again, and a ref that no longer
    names what the merge holds stays. Returns the deleted branch names.
    """
    from delivery_compile import delivery_root, docs_root, split_note
    deliveries = rel_posix(root, delivery_root(docs_root(root)) / "deliveries")
    items = {}
    for path in git_paths(root, "ls-tree", "-r", "-z", "--name-only", record, "--", deliveries + "/"):
        parts = path[len(deliveries) + 1:].split("/")
        if (len(parts) == 4 and parts[0].startswith(delivery_id.lower() + "-")
                and parts[1] == "items" and parts[3] == "item.md"):
            items[canonical_refs(delivery_id, parts[2].upper())["item"]] = path
    integration_ref = canonical_refs(delivery_id)["integration"]
    tips = remote_ref_oids(root, remote, [integration_ref, *items])
    updates = [(integration_ref, record, "")] if tips[integration_ref] == record else []
    for ref, path in sorted(items.items()):
        tip = tips[ref]
        # A tip this checkout lacks is not in the merge, whose history the target fetch brought.
        if not tip or subprocess.run(["git", "cat-file", "-e", tip + "^{commit}"], cwd=root,
                                     capture_output=True, check=False).returncode:
            continue
        if (is_ancestor(root, tip, record) and trailer(commit_message(root, tip), "Delivery") == delivery_id
                and split_remote_note(root, tip, path, split_note)[0].get("status") == "integrated"):
            updates.append((ref, tip, ""))
    if updates:
        atomic_push(root, remote, updates)
    return [ref.removeprefix("refs/heads/") for ref, _expected, _candidate in updates]


def package_paths(root: Path, directory: Path, docs: Path,
                  include_items: bool = True, include_map: bool = True) -> list[str]:
    from experience_application_check import is_os_metadata_path

    paths = []
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("DELIVERY_PATH_ESCAPE: Delivery publication forbids symlink package paths")
        if not path.is_file():
            continue
        if is_os_metadata_path(path) and path.stat().st_nlink == 1:
            continue
        relative_to_delivery = path.relative_to(directory).parts
        if not include_items and relative_to_delivery and relative_to_delivery[0] == "items":
            continue
        paths.append(rel_posix(root, path))
    map_path = docs / "maps" / "delivery.md"
    if include_map and map_path.exists():
        paths.append(rel_posix(root, map_path))
    return sorted(set(paths))


def assert_integrated_items(root: Path, remote: str, directory: Path,
                            integration_oid: str, delivery_id: str) -> list[str]:
    from delivery_compile import split_note
    stories = []
    for item_path in sorted(directory.glob("items/*/item.md")):
        story = item_path.parent.name.upper()
        item_ref = canonical_refs(delivery_id, story)["item"]
        item_oid = remote_oid(root, remote, item_ref)
        relative = rel_posix(root, item_path)
        item_props, _ = split_remote_note(root, item_oid, relative, split_note)
        if item_props.get("status") != "integrated":
            raise RuntimeError(f"DELIVERY_ITEM_NOT_READY: Delivery Item is not integrated: {story}")
        try:
            run_git(root, "merge-base", "--is-ancestor", item_oid, integration_oid)
        except RuntimeError as exc:
            raise RuntimeError(f"DELIVERY_COORDINATION_CORRUPT: Integration does not contain exact Item tip: {story}") from exc
        stories.append(story)
    if not stories:
        raise RuntimeError("Delivery Review requires at least one integrated Item")
    return stories


def publish_delivery_review(project_root: Path, delivery_id: str,
                            remote: str = "origin") -> dict:
    """Publish one approved Delivery Review as a real Integration child."""
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    from delivery_compile import docs_root, delivery_findings, split_note
    docs = docs_root(root)
    directory, findings = delivery_findings(docs, delivery_id)
    if directory is None or findings:
        raise RuntimeError("Delivery package is not portable: " + "; ".join(findings))
    delivery_props, _ = split_note(directory / "delivery.md")
    review_path = directory / "delivery-review.md"
    if delivery_props.get("status") != "review" or not review_path.exists():
        raise RuntimeError("publish-delivery-review requires an approved Delivery Review")
    review_props, _ = split_note(review_path)
    if review_props.get("status") != "approved":
        raise RuntimeError("publish-delivery-review requires an approved review record")
    refuse_pending_decisions(root, directory, "publish-delivery-review")
    refs = canonical_refs(delivery_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    fence_message = commit_message(root, fence_oid)
    require_fence_record(fence_message)
    if trailer(fence_message, "Mode") != "open":
        raise RuntimeError("DELIVERY_FENCE_MODE: publish-delivery-review requires an open Fence")
    stories = assert_integrated_items(root, remote, directory, integration_oid, delivery_id)
    reviewed_parent = str(review_props.get("reviewed_integration_commit", "none"))
    if reviewed_parent != integration_oid:
        raise RuntimeError("DELIVERY_REVIEW_STALE: Delivery Review reviewed_integration_commit is stale")
    candidate = commit_tree(
        root, integration_oid, package_paths(root, directory, docs, include_items=False),
        f"Publish delivery review for {delivery_id}",
        {"Record": "delivery-review-published-v1", "Protocol": "1", "Delivery": delivery_id,
         "Reviewed-Integration": integration_oid, "Approval-Hash": str(review_props.get("approval_hash", "none")),
         "Target": trailer(fence_message, "Target") or "none", "Cancellation-Intent-Hash": "none"},
        delivery_projections=True,
    )
    fence_candidate = commit_tree(
        root, fence_oid, [], "Fence project in open mode",
        {"Record": "project-fence-v2", "Protocol": "2", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": trailer(fence_message, "Target") or "none",
         "Governance-Hash": trailer(fence_message, "Governance-Hash") or "none",
         **carried_fence_barrier(fence_message)},
    )
    atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                               (refs["integration"], integration_oid, candidate)])
    return {"ok": True, "delivery": delivery_id, "integration": candidate,
            "fence": fence_candidate, "stories": stories, "refs": short_refs(delivery_id)}


def prepare_pr_creation(project_root: Path, delivery_id: str,
                        remote: str = "origin") -> dict:
    """Publish the durable PR-create intent; this function never calls a provider."""
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    refs = canonical_refs(delivery_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    fence_message = commit_message(root, fence_oid)
    require_fence_record(fence_message)
    integration_message = commit_message(root, integration_oid)
    if trailer(integration_message, "Record") != "delivery-review-published-v1":
        raise RuntimeError("PR creation requires a published Delivery Review")
    if trailer(fence_message, "Mode") != "open":
        raise RuntimeError("DELIVERY_FENCE_MODE: PR creation requires an open Fence")
    attempt = epoch_token()
    target = trailer(fence_message, "Target") or "none"
    intent = commit_tree(
        root, integration_oid, [], f"Prepare PR creation for {delivery_id}",
        {"Record": "pr-creation-intent-v1", "Protocol": "1", "Delivery": delivery_id,
         "Review-Head": integration_oid, "Target": target, "Provider": "github", "Attempt": attempt},
    )
    fence_candidate = commit_tree(
        root, fence_oid, [], "Fence project in open mode",
        {"Record": "project-fence-v2", "Protocol": "2", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": target, "Governance-Hash": trailer(fence_message, "Governance-Hash") or "none",
         **carried_fence_barrier(fence_message)},
    )
    atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                               (refs["integration"], integration_oid, intent)])
    return {"ok": True, "delivery": delivery_id, "intent": intent,
            "attempt": attempt, "provider": "github", "refs": short_refs(delivery_id)}


def pr_record_replacements(root: Path, intent: str, package: str, url: str) -> tuple[dict[str, str], bool]:
    """The notes the PR record of the canonical PR *url* replaces on the PR *intent*, and whether it re-renders.

    Only the Review's pull_request_url and source_hash, and a reviewed
    Delivery's awaiting_merge status, change; projections are derived only
    with that status. The closure check compares the record's notes with
    these, read from Git objects alone, so the PR head carries nothing the
    coordinator did not write; the projections it re-renders are only
    required to stay outside the product.
    """
    from delivery_compile import split_note, frontmatter, content_hash, pr_recorded_props
    canonical_url, _number = canonical_github_pr(url)
    relative_review = f"{package}/delivery-review.md"
    review_props, review_body = split_remote_note(root, intent, relative_review, split_note)
    review_props["pull_request_url"] = canonical_url
    review_props["source_hash"] = content_hash(review_props, review_body, exclude={"status", "approved_at_utc", "source_hash", "approval_hash"})
    replacements = {relative_review: frontmatter(review_props, review_body)}
    relative_delivery = f"{package}/delivery.md"
    delivery_props, delivery_body = split_remote_note(root, intent, relative_delivery, split_note)
    recorded = pr_recorded_props(delivery_props, delivery_body)
    if recorded is not None:
        replacements[relative_delivery] = frontmatter(recorded, delivery_body)
    return replacements, recorded is not None


def pr_record_candidate(root: Path, intent: str, package: str, delivery_id: str, url: str) -> str:
    """The PR record commit that records the canonical PR *url* on the PR *intent*."""
    canonical_url, number = canonical_github_pr(url)
    replacements, projections = pr_record_replacements(root, intent, package, canonical_url)
    return commit_replacements(
        root, intent, replacements,
        f"Record PR for {delivery_id}",
        {"Record": "pr-url-recorded-v1", "Protocol": "1", "Delivery": delivery_id,
         "Intent": intent, "Provider": "github", "Pull-Request": number,
         "URL-Hash": pr_url_hash(canonical_url)},
        delivery_projections=projections,
    )


def record_pr_remote(project_root: Path, delivery_id: str, url: str,
                     remote: str = "origin") -> dict:
    """Record a provider-verified PR URL as the exact intent child.

    The same commit, which is the PR head, moves a reviewed Delivery to
    awaiting_merge and re-renders the projections that mirror its status.
    It carries the Review published before the intent and adds only the PR
    URL: a cancellation writes its Review on the Integration alone, so the
    local Review may still hold the approval it replaced. That record is the
    only source of the URL; a local Review, when there is one, must already
    mirror it.
    """
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    from delivery_compile import docs_root, find_delivery, split_note
    canonical_url, number = canonical_github_pr(url)
    docs = docs_root(root)
    directory = find_delivery(docs, delivery_id)
    if directory is None:
        raise RuntimeError("Delivery package not found")
    review_path = directory / "delivery-review.md"
    if review_path.exists() and split_note(review_path)[0].get("pull_request_url") != canonical_url:
        raise RuntimeError("local Delivery Review URL does not match the requested PR")
    refs = canonical_refs(delivery_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    fence_message = commit_message(root, fence_oid)
    require_fence_record(fence_message)
    integration_oid = remote_oid(root, remote, refs["integration"])
    intent_message = commit_message(root, integration_oid)
    intent_record = trailer(intent_message, "Record")
    if intent_record not in {"pr-creation-intent-v1", "pr-adoption-intent-v1"}:
        raise RuntimeError("record-pr requires the exact unmatched PR intent")
    if intent_record == "pr-adoption-intent-v1" and not binds_pr(intent_message, canonical_url):
        raise RuntimeError("DELIVERY_PR_UNCERTAIN: the requested PR is not the PR the adoption intent names")
    candidate = pr_record_candidate(root, integration_oid, rel_posix(root, directory), delivery_id, canonical_url)
    fence_candidate = commit_tree(
        root, fence_oid, [], "Fence project in open mode",
        {"Record": "project-fence-v2", "Protocol": "2", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": trailer(fence_message, "Target") or "none",
         "Governance-Hash": trailer(fence_message, "Governance-Hash") or "none",
         **carried_fence_barrier(fence_message)},
    )
    atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                               (refs["integration"], integration_oid, candidate)])
    return {"ok": True, "delivery": delivery_id, "integration": candidate,
            "fence": fence_candidate, "pull_request_url": canonical_url,
            "pull_request": number, "refs": short_refs(delivery_id)}


def set_pr_body_to_review(root: Path, provider, oid: str, review_path: Path, url: str) -> None:
    """Give an existing Delivery PR the Review in *oid*'s tree as its body.

    The PR keeps the body it was opened with: an earlier Review, the approval
    that a cancellation replaced, or what its author wrote by hand. open-pr
    sets the body before the PR record, so a run that stops in between sets
    the same body again on its next run.
    """
    from delivery_compile import split_note
    provider.update_body(url, split_remote_note(root, oid, rel_posix(root, review_path), split_note)[1])


def open_pr(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    """Create or resume exactly one GitHub draft PR after a durable intent."""
    root = main_worktree(project_root.resolve())
    from delivery_compile import docs_root, find_delivery, split_note, record_pr_url
    from delivery_provider import GitHubProvider, ProviderError
    docs = docs_root(root)
    directory = find_delivery(docs, delivery_id)
    if directory is None:
        raise RuntimeError("Delivery package not found")
    review_path = directory / "delivery-review.md"
    refs = canonical_refs(delivery_id)
    integration_oid = integration_record(root, remote, delivery_id)
    integration_message = commit_message(root, integration_oid)
    record_name = trailer(integration_message, "Record")
    if record_name == "pr-url-recorded-v1":
        return {"ok": True, "delivery": delivery_id,
                "pull_request_url": recorded_pr_url(root, integration_oid, review_path),
                "reused": True, "provider_call": False}
    refuse_merged_delivery(root, delivery_id, remote)
    require_fence_record(commit_message(root, remote_oid(root, remote, refs["fence"])))
    provider = GitHubProvider(root, remote)
    target_branch, _ = resolve_target(root, remote, recorded=recorded_target_branch(root, delivery_id))
    head = short_refs(delivery_id)["integration"]
    if record_name in {"delivery-review-published-v1", "pr-adoption-intent-v1"}:
        existing = provider.exact_unmerged(head, target_branch)
        if len(existing) != 1:
            raise RuntimeError("external PR adoption requires exactly one unmerged exact PR")
        pr = existing[0]
        if str(pr.get("state", "")).upper() != "OPEN":
            raise RuntimeError("DELIVERY_PR_STATE_INVALID: external closed-unmerged PR requires explicit provider reopen before adoption")
        if pr.get("headRefName") != head or pr.get("baseRefName") != target_branch:
            raise RuntimeError("DELIVERY_PR_HEAD_BASE_MISMATCH: external PR head/base does not match the Delivery")
        if pr.get("headRefOid") and pr.get("headRefOid") != integration_oid:
            raise RuntimeError("DELIVERY_PR_HEAD_BASE_MISMATCH: external PR head does not match the reviewed Integration")
        url = pr.get("url")
        if not isinstance(url, str):
            raise ProviderError("DELIVERY_PR_UNCERTAIN: external PR has no canonical URL")
        canonical_url, number = canonical_github_pr(url)
        # An adoption that stopped before its record resumes from its intent,
        # which names the one PR it adopts; the provider must show that PR.
        if record_name == "pr-adoption-intent-v1" and not binds_pr(integration_message, canonical_url):
            raise RuntimeError("DELIVERY_PR_UNCERTAIN: the exact Delivery PR is not the PR the adoption intent names")
        if not pr.get("isDraft"):
            provider.ensure_draft(canonical_url)
        if record_name == "delivery-review-published-v1":
            fence_oid = remote_oid(root, remote, refs["fence"])
            fence_message = commit_message(root, fence_oid)
            adoption_intent = commit_tree(
                root, integration_oid, [], f"Adopt PR for {delivery_id}",
                {"Record": "pr-adoption-intent-v1", "Protocol": "1", "Delivery": delivery_id,
                 "Review-Head": integration_oid, "Target": trailer(fence_message, "Target") or "none",
                 "Provider": "github", "Pull-Request": number, "URL-Hash": pr_url_hash(canonical_url)},
            )
            fence_candidate = commit_tree(
                root, fence_oid, [], "Fence project in open mode",
                {"Record": "project-fence-v2", "Protocol": "2", "Mode": "open",
                 "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
                 "Target": trailer(fence_message, "Target") or "none",
                 "Governance-Hash": trailer(fence_message, "Governance-Hash") or "none",
                 **carried_fence_barrier(fence_message)},
            )
            atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                                       (refs["integration"], integration_oid, adoption_intent)])
        # The provider was already normalized to draft and the exact URL is
        # carried by the adoption intent. No create receipt or provider POST
        # is permitted on this path.
        set_pr_body_to_review(root, provider, integration_oid, review_path, canonical_url)
        record_pr_url(docs, delivery_id, canonical_url)
        recorded = record_pr_remote(root, delivery_id, canonical_url, remote)
        return {"ok": True, "delivery": delivery_id, "pull_request_url": canonical_url,
                "provider_call": False, "adopted": True,
                "integration": recorded["integration"], "fence": recorded["fence"],
                "refs": short_refs(delivery_id)}
    if record_name != "pr-creation-intent-v1":
        raise RuntimeError("open-pr requires an unmatched PR creation or adoption intent")
    attempt = trailer(integration_message, "Attempt")
    if not attempt:
        raise RuntimeError("DELIVERY_COORDINATION_CORRUPT: PR creation intent has no Attempt")
    # The PR body is the Review published at the intent. A cancellation writes
    # its Review on the Integration alone, so the local Review may be stale.
    _, review_body = split_remote_note(root, integration_oid, rel_posix(root, review_path), split_note)
    existing = provider.exact_unmerged(head, target_branch)
    if len(existing) > 1:
        raise RuntimeError("DELIVERY_PR_DUPLICATE: multiple exact unmerged Delivery PRs exist")
    shown = existing[0].get("url") if existing else None
    receipt = create_provider_receipt(root, delivery_id, integration_oid, attempt,
                                      exact_pr_url=shown if isinstance(shown, str) else None)
    if receipt["state"] == "verified" and receipt.get("url") not in {None, "none"}:
        return {"ok": True, "delivery": delivery_id, "pull_request_url": receipt["url"],
                "reused": True, "provider_call": False}
    if existing:
        pr = existing[0]
        if str(pr.get("state", "")).upper() != "OPEN":
            raise RuntimeError("DELIVERY_PR_STATE_INVALID: exact Delivery PR is closed without merge; manual reopen is required")
        url = pr.get("url")
        if not isinstance(url, str):
            raise ProviderError("DELIVERY_PR_UNCERTAIN: GitHub exact PR has no URL")
        provider.ensure_draft(url)
        provider_call = False
    else:
        receipt, elected = mark_provider_call_started(root, delivery_id, integration_oid, attempt)
        if not elected:
            raise RuntimeError("DELIVERY_PR_UNCERTAIN: another process owns the provider call")
        delivery_props, _ = split_remote_note(root, integration_oid, rel_posix(root, directory / "delivery.md"), split_note)
        created = provider.create_draft(head, target_branch, str(delivery_props.get("goal", delivery_id)), review_body)
        url = created["url"]
        provider_call = True
    canonical_url, _ = canonical_github_pr(url)
    if existing:
        set_pr_body_to_review(root, provider, integration_oid, review_path, canonical_url)
    record_pr_url(docs, delivery_id, canonical_url)
    recorded = record_pr_remote(root, delivery_id, canonical_url, remote)
    mark_provider_verified(root, delivery_id, integration_oid, attempt, canonical_url)
    return {"ok": True, "delivery": delivery_id, "pull_request_url": canonical_url,
            "provider_call": provider_call, "integration": recorded["integration"],
            "fence": recorded["fence"], "refs": short_refs(delivery_id)}


def merge_pr(project_root: Path, delivery_id: str, remote: str = "origin", *,
             verify_only: bool = False) -> dict:
    """Merge the one reviewed PR with exact head/base evidence.

    Provider mutation is followed by a fresh all-state query and target
    ancestry proof. A ready/squash/rebase/admin result or missing merge object
    is never interpreted as successful closure. With *verify_only*, as
    verify-merge calls it, no provider call changes the PR: a PR the provider
    does not show as merged is refused, and a merged one gets the same proof.
    Once the proof holds, the Delivery's refs are dropped, so a later run reads
    the PR record from the target history and drops whatever an earlier
    release left behind.
    """
    root = main_worktree(project_root.resolve())
    from delivery_compile import docs_root, find_delivery
    from delivery_provider import GitHubProvider, ProviderError
    docs = docs_root(root)
    directory = find_delivery(docs, delivery_id)
    if directory is None:
        raise RuntimeError("Delivery package not found")
    refs = canonical_refs(delivery_id)
    integration_oid = integration_record(root, remote, delivery_id)
    integration_message = commit_message(root, integration_oid)
    if trailer(integration_message, "Record") != "pr-url-recorded-v1":
        raise RuntimeError("merge-pr requires the current recorded Delivery PR")
    canonical_url = recorded_pr_url(root, integration_oid, directory / "delivery-review.md")
    from delivery_closure import recorded_head_findings
    unbound = recorded_head_findings(root, remote, delivery_id, integration_oid, canonical_url)
    if unbound:
        raise RuntimeError("DELIVERY_COORDINATION_CORRUPT: merge-pr refuses a PR head the coordinator did not"
                           " write as recorded: " + "; ".join(unbound))
    recorded = recorded_target_branch(root, delivery_id)
    target_branch, target_before = resolve_target(root, remote, recorded=recorded)
    provider = GitHubProvider(root, remote)
    candidates = [item for item in provider.list_pull_requests(short_refs(delivery_id)["integration"], target_branch)
                  if str(item.get("url", "")) == canonical_url]
    if len(candidates) != 1:
        raise ProviderError("DELIVERY_PR_HEAD_BASE_MISMATCH: exactly one lifecycle PR is required")
    pr = candidates[0]
    if str(pr.get("state", "")).upper() == "MERGED" or verify_only:
        merged = pr
    else:
        if str(pr.get("state", "")).upper() != "OPEN":
            raise ProviderError("DELIVERY_PR_STATE_INVALID: Delivery PR head/base/state is not mergeable")
        if pr.get("headRefName") != short_refs(delivery_id)["integration"] or pr.get("baseRefName") != target_branch:
            raise ProviderError("DELIVERY_PR_HEAD_BASE_MISMATCH: Delivery PR head/base/state is not mergeable")
        if pr.get("isDraft"):
            provider.make_ready(canonical_url)
        head_now = remote_oid(root, remote, refs["integration"])
        if head_now != integration_oid:
            raise RuntimeError("DELIVERY_REVIEW_STALE: Integration advanced after PR review; re-run Delivery Review")
        current = provider.inspect_pull_request(canonical_url)
        if str(current.get("state", "")).upper() != "OPEN" or current.get("isDraft"):
            raise ProviderError("DELIVERY_PR_STATE_INVALID: Delivery PR changed before the merge call")
        if (current.get("headRefName") != short_refs(delivery_id)["integration"]
                or current.get("baseRefName") != target_branch
                or current.get("headRefOid") != integration_oid):
            raise ProviderError("DELIVERY_PR_HEAD_BASE_MISMATCH: Delivery PR changed before the merge call")
        provider.require_green_checks(current)
        provider.merge_commit(canonical_url, integration_oid)
        refreshed = [item for item in provider.list_pull_requests(short_refs(delivery_id)["integration"], target_branch)
                     if str(item.get("url", "")) == canonical_url]
        if len(refreshed) != 1:
            raise ProviderError("DELIVERY_PR_UNCERTAIN: merged PR cannot be reconstructed")
        merged = refreshed[0]
    if str(merged.get("state", "")).upper() != "MERGED":
        raise ProviderError("DELIVERY_MERGE_PROOF_INVALID: provider PR is not merged")
    provider.require_green_checks(merged)
    merge_value = merged.get("mergeCommit")
    merge_oid = merge_value.get("oid") if isinstance(merge_value, dict) else merge_value
    if not isinstance(merge_oid, str) or not OID_RE.fullmatch(merge_oid):
        raise ProviderError("DELIVERY_MERGE_PROOF_INVALID: provider did not return an exact merge commit")
    target_after = resolve_target(root, remote, recorded=recorded)[1]
    run_git(root, "fetch", "--no-tags", remote, f"refs/heads/{target_branch}:refs/remotes/{remote}/{target_branch}")
    try:
        run_git(root, "merge-base", "--is-ancestor", merge_oid, target_after)
        run_git(root, "merge-base", "--is-ancestor", integration_oid, target_after)
        merge_object = run_git(root, "cat-file", "-p", merge_oid)
    except RuntimeError as exc:
        raise RuntimeError("DELIVERY_MERGE_PROOF_INVALID: provider merge is not present in the exact target ancestry") from exc
    parents = [line.split(" ", 1)[1] for line in merge_object.splitlines()
               if line.startswith("parent ") and " " in line]
    if len(parents) != 2 or parents[1] != integration_oid:
        raise ProviderError("DELIVERY_MERGE_POLICY_INVALID: provider merge is not an exact two-parent merge of the reviewed Integration")
    dropped = drop_merged_refs(root, remote, delivery_id, integration_oid)
    return {"ok": True, "delivery": delivery_id, "status": "merged",
            "pull_request_url": canonical_url, "merge_commit": merge_oid,
            "target_before": target_before, "target_after": target_after,
            "reviewed_integration": integration_oid, "refs": short_refs(delivery_id),
            "observations": [{"kind": "ref", "target": ref, "value": "absent"} for ref in dropped]}


def invalidate_delivery_review(project_root: Path, delivery_id: str,
                               finding_code: str, finding_hash: str,
                               remote: str = "origin") -> dict:
    """Persist an evidence/review-only change request on the Integration ref."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_.-]{2,63}", finding_code):
        raise ValueError("finding code must be an uppercase stable code")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", finding_hash):
        raise ValueError("finding hash must be a canonical sha256 digest")
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    from delivery_compile import docs_root, find_delivery, split_note, frontmatter, content_hash
    docs = docs_root(root)
    directory = find_delivery(docs, delivery_id)
    if directory is None:
        raise RuntimeError("Delivery package not found")
    refs = canonical_refs(delivery_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    fence_message = commit_message(root, fence_oid)
    require_fence_record(fence_message)
    integration_message = commit_message(root, integration_oid)
    if trailer(fence_message, "Mode") != "open":
        raise RuntimeError("DELIVERY_FENCE_MODE: review invalidation requires an open Fence")
    if trailer(integration_message, "Record") not in {"delivery-review-published-v1", "pr-url-recorded-v1"}:
        raise RuntimeError("review invalidation requires a published current Review")
    review_path = directory / "delivery-review.md"
    relative_review = rel_posix(root, review_path)
    review_props, review_body = split_remote_note(root, integration_oid, relative_review, split_note)
    if review_props.get("status") != "approved":
        raise RuntimeError("current Delivery Review is not approved")
    # A cancellation publishes one final Review: its Items are cancelled and a
    # second cancellation is refused, so nothing could publish a Review again.
    delivery_props, _ = split_remote_note(root, integration_oid, rel_posix(root, directory / "delivery.md"), split_note)
    if delivery_props.get("status") == "cancelled":
        raise RuntimeError("DELIVERY_CANCELLATION_INVALID: the cancellation Review of a cancelled Delivery is final "
                           "and cannot be invalidated")
    review_props["status"] = "changes_requested"
    review_props["tags"] = [tag for tag in review_props.get("tags", []) if not str(tag).startswith("status/")] + ["status/changes-requested"]
    review_props["finding_code"] = finding_code
    review_props["finding_hash"] = finding_hash
    review_props.pop("approval_hash", None)
    review_props["source_hash"] = content_hash(review_props, review_body)
    candidate = commit_replacements(
        root, integration_oid, {relative_review: frontmatter(review_props, review_body)},
        f"Invalidate Delivery Review for {delivery_id}",
        {"Record": "delivery-review-invalidated-v1", "Protocol": "1", "Delivery": delivery_id,
         "Previous-Review": integration_oid, "Finding-Code": finding_code,
         "Finding-Hash": finding_hash, "Target": trailer(fence_message, "Target") or "none"},
    )
    fence_candidate = commit_tree(
        root, fence_oid, [], "Fence project in open mode",
        {"Record": "project-fence-v2", "Protocol": "2", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": trailer(fence_message, "Target") or "none",
         "Governance-Hash": trailer(fence_message, "Governance-Hash") or "none",
         **carried_fence_barrier(fence_message)},
    )
    atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                               (refs["integration"], integration_oid, candidate)])
    return {"ok": True, "delivery": delivery_id, "status": "changes_requested",
            "finding_code": finding_code, "finding_hash": finding_hash,
            "integration": candidate, "fence": fence_candidate, "refs": short_refs(delivery_id)}


def cancellation_projection(delivery_id: str, scope_hash: str, reason: str,
                             stories: dict[str, dict[str, str]], target: str) -> tuple[dict, str]:
    if not reason.strip() or not OID_RE.fullmatch(target):
        raise ValueError("DELIVERY_CANCELLATION_INVALID: cancellation reason and exact target OID are required")
    normalized = {}
    for story, value in sorted(stories.items()):
        validate_story_id(story)
        if set(value) != {"disposition", "tip"}:
            raise ValueError("DELIVERY_CANCELLATION_INVALID: cancellation story projection has unexpected keys")
        disposition = str(value["disposition"])
        tip = str(value["tip"])
        if disposition == "not_started":
            if tip != "none":
                raise ValueError("DELIVERY_CANCELLATION_INVALID: not_started cancellation stories must use tip none")
        elif disposition in {"integrated_reverted", "unintegrated_discarded"}:
            if not OID_RE.fullmatch(tip):
                raise ValueError("DELIVERY_CANCELLATION_INVALID: executed cancellation stories require an exact previous Item tip")
        else:
            raise ValueError("DELIVERY_CANCELLATION_INVALID: unsupported cancellation disposition")
        normalized[story] = {"disposition": disposition, "tip": tip}
    projection = {"delivery": delivery_id, "reason": reason.strip(),
                  "scope_hash": scope_hash, "stories": normalized, "target": target}
    digest = "sha256:" + hashlib.sha256(_canonical_json(projection).encode("utf-8")).hexdigest()
    return projection, digest


def cancellation_projection_hash(delivery_id: str, intent_hash: str,
                                 stories: dict[str, dict[str, str]], target: str,
                                 barrier_epoch: str) -> str:
    """Hash the final, pre-finalization disposition projection.

    The commit OIDs are deliberately excluded. This makes the Review and the
    target package reproducible after an accepted-response-loss recovery.
    """
    if not EPOCH_RE.fullmatch(barrier_epoch):
        raise ValueError("DELIVERY_CANCELLATION_INVALID: cancellation barrier epoch is invalid")
    final_stories = {}
    for story, value in sorted(stories.items()):
        validate_story_id(story)
        if set(value) != {"disposition", "tip"}:
            raise ValueError("DELIVERY_CANCELLATION_INVALID: cancellation finalization story projection has unexpected keys")
        final_stories[story] = {
            "disposition": str(value["disposition"]),
            "previous_tip": str(value["tip"]),
        }
    value = {
        "barrier_epoch": barrier_epoch,
        "cancellation_intent_hash": intent_hash,
        "delivery": delivery_id,
        "stories": final_stories,
        "target": target,
    }
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def revert_merge_candidate(root: Path, base: str, merge_oid: str,
                           subject: str, trailers: dict[str, str]) -> str:
    """Create a deterministic reverse-order revert of one Item merge.

    Integration commits are merge commits whose first parent is the previous
    Integration head. Applying the inverse first-parent patch to the current
    head preserves all later unrelated history while requiring explicit
    conflict resolution instead of silently choosing a product tree.
    """
    parents = run_git(root, "show", "-s", "--format=%P", merge_oid).split()
    if len(parents) < 2:
        raise RuntimeError(f"DELIVERY_COORDINATION_CORRUPT: integrated Item tip is not a merge commit: {merge_oid}")
    first_parent = parents[0]
    with tempfile.TemporaryDirectory(prefix="agentrof-revert-index-") as temporary:
        index = Path(temporary) / "index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        read = subprocess.run(["git", "read-tree", base], cwd=root, env=env,
                              encoding="utf-8", capture_output=True, check=False)
        if read.returncode:
            raise RuntimeError(read.stderr.strip() or "cannot prepare cancellation revert index")

        def entry(tree: str, path: str) -> tuple[str, str] | None:
            # A path is a literal name here: under pathspec magic ":x.py" would name "x.py".
            result = subprocess.run(
                ["git", "--literal-pathspecs", "ls-tree", tree, "--", path], cwd=root,
                encoding="utf-8", capture_output=True, check=False,
            )
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "cannot inspect cancellation tree")
            line = result.stdout.rstrip("\n")
            if not line:
                return None
            metadata, _name = line.split("\t", 1)
            mode, _kind, oid = metadata.split()
            return mode, oid

        changed = git_paths(root, "diff", "--no-renames", "--name-only", "-z", first_parent, merge_oid,
                            failure="cannot inspect Item merge")
        for path in changed:
            parent_entry = entry(first_parent, path)
            merge_entry = entry(merge_oid, path)
            current_entry = entry(base, path)
            if current_entry != merge_entry and current_entry != parent_entry:
                raise RuntimeError(
                    "DELIVERY_CANCELLATION_INVALID: cancellation revert conflicts with current Integration: "
                    f"{path} (base={current_entry}, merge={merge_entry}, "
                    f"parent={parent_entry})"
                )
            if parent_entry is None:
                update = subprocess.run(
                    ["git", "update-index", "--force-remove", "--", path],
                    cwd=root, env=env, encoding="utf-8", capture_output=True, check=False,
                )
            else:
                mode, oid = parent_entry
                update = subprocess.run(
                    ["git", "update-index", "--add", "--cacheinfo",
                     f"{mode},{oid},{path}"],
                    cwd=root, env=env, encoding="utf-8", capture_output=True, check=False,
                )
            if update.returncode:
                raise RuntimeError(update.stderr.strip() or f"cannot apply cancellation revert: {path}")
        tree = subprocess.run(["git", "write-tree"], cwd=root, env=env,
                              encoding="utf-8", capture_output=True, check=False)
        if tree.returncode:
            raise RuntimeError(tree.stderr.strip() or "cannot write cancellation revert tree")
        message = subject + "\n\n" + "\n".join(
            f"Agentrof-{key}: {value}" for key, value in trailers.items()
        ) + "\n"
        commit = git_with_input(root, ["commit-tree", tree.stdout.strip(), "-p", base], message, env)
        if commit.returncode:
            raise RuntimeError(commit.stderr.strip() or "cannot create cancellation revert")
        return commit.stdout.strip()


def cancel_delivery(project_root: Path, delivery_id: str, reason: str,
                    remote: str = "origin") -> dict:
    """Cancel a Delivery through one intent, disposition and Review push.

    The operation is fail-closed: all Item/Slot/Integration/Fence leases are
    checked in one final atomic push. Integrated Item merge commits are
    reverted in reverse first-parent order before the cancelled projections
    are published. No remote partial cancellation is accepted.
    """
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    from delivery_compile import docs_root, find_delivery, split_note, frontmatter, body_for, content_hash
    docs = docs_root(root)
    directory = find_delivery(docs, delivery_id)
    if directory is None:
        raise RuntimeError("Delivery package not found")
    delivery_path_value = directory / "delivery.md"
    local_props, _ = split_note(delivery_path_value)
    if local_props.get("status") in {"draft", "cancelled", "target_merged"}:
        raise RuntimeError("DELIVERY_CANCELLATION_INVALID: cancel-delivery requires a nonterminal approved Delivery")
    refs = canonical_refs(delivery_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    fence_message = commit_message(root, fence_oid)
    require_fence_record(fence_message)
    integration_message = commit_message(root, integration_oid)
    if trailer(fence_message, "Mode") != "open" or trailer(integration_message, "Record") in {
            "cancellation-intent-v1", "delivery-barrier-v1", "cancellation-finalized-v1"}:
        raise RuntimeError("DELIVERY_BARRIER_ACTIVE: Delivery already has a barrier or non-open Fence")

    relative_delivery = rel_posix(root, delivery_path_value)
    remote_props, remote_body = split_remote_note(root, integration_oid, relative_delivery, split_note)
    # A cancellation publishes the cancelled status on the Integration alone,
    # so the local delivery.md cannot tell that the Delivery is cancelled.
    if remote_props.get("status") == "cancelled":
        raise RuntimeError("DELIVERY_CANCELLATION_INVALID: the published Delivery is already cancelled")
    # A barrier ends only through the finish or abort verb of its kind. The cancellation
    # would carry it to its own Fence child, where it outlives the cancellation's merge,
    # and a release after the cancellation would bury the Review its PR needs at the tip.
    barrier = trailer(fence_message, "Barrier-Kind") or "none"
    if barrier != "none":
        raise RuntimeError(f"DELIVERY_BARRIER_ACTIVE: the Fence carries a {barrier} barrier, which a cancellation "
                           f"cannot release; end it with finish-{barrier} or abort-{barrier} before cancel-delivery")
    scope_hash = str(remote_props.get("scope_hash", "none"))
    # Every integration of a Story sits on the Integration's own first-parent line after
    # the reservation, newest first. A reopened Item leaves the integration it reopened
    # there, and a later integration merges only what changed since, so each is reverted.
    integrated: list[tuple[str, str]] = []
    for oid in run_git(root, "rev-list", "--first-parent", integration_oid).splitlines():
        message = commit_message(root, oid)
        if trailer(message, "Delivery") != delivery_id:
            continue
        if trailer(message, "Record") == "delivery-reservation-v1":
            break
        if trailer(message, "Record") == "item-integration-v1":
            integrated.append((trailer(message, "Story") or "", oid))
    merged_stories = {story for story, _oid in integrated}
    all_slots = remote_slot_oids(root, remote)
    contexts: dict[str, dict] = {}
    stories: dict[str, dict[str, str]] = {}
    for item_path in sorted(directory.glob("items/*/item.md")):
        story = item_path.parent.name.upper()
        item_ref = canonical_refs(delivery_id, story)["item"]
        relative_item = rel_posix(root, item_path)
        context = {"path": relative_item, "item_ref": item_ref, "item_oid": None,
                   "slot": None, "props": None, "body": None}
        item_oid = own_item_tip(root, remote, delivery_id, story)
        if item_oid:
            item_props, item_body = split_remote_note(root, item_oid, relative_item, split_note)
            if item_props.get("status") == "cancelled":
                raise RuntimeError(f"DELIVERY_CANCELLATION_INVALID: Item is already cancelled: {story}")
            if item_props.get("status") == "integrated" and story not in merged_stories:
                raise RuntimeError(
                    f"DELIVERY_COORDINATION_CORRUPT: Integration history has no exact Item merge for {story}"
                )
            disposition = "integrated_reverted" if story in merged_stories else "unintegrated_discarded"
            context.update({"item_oid": item_oid, "slot": next((key for key, oid in all_slots.items() if oid == item_oid), None),
                            "props": item_props, "body": item_body})
            stories[story] = {"disposition": disposition, "tip": item_oid}
        else:
            stories[story] = {"disposition": "not_started", "tip": "none"}
        contexts[story] = context
    target = trailer(fence_message, "Target") or resolve_target(
        root, remote, recorded=recorded_target_branch(root, delivery_id))[1]
    projection, intent_hash = cancellation_projection(delivery_id, scope_hash, reason, stories, target)
    barrier_epoch = epoch_token()

    intent_props = dict(remote_props)
    intent_props["cancellation_intent_hash"] = intent_hash
    intent_props["source_hash"] = content_hash(intent_props, remote_body)
    intent = commit_replacements(
        root, integration_oid, {relative_delivery: frontmatter(intent_props, remote_body)},
        f"Record cancellation intent for {delivery_id}",
        {"Record": "cancellation-intent-v1", "Protocol": "1", "Delivery": delivery_id,
         "Scope-Hash": scope_hash, "Target": target,
         "Cancellation-Intent": intent_hash, "Cancellation-Intent-Hash": intent_hash},
    )
    barrier = commit_tree(
        root, intent, [], f"Quiesce {delivery_id} for cancellation",
        {"Record": "delivery-barrier-v1", "Protocol": "1", "Delivery": delivery_id,
         "Barrier-Kind": "cancellation", "Barrier-Epoch": barrier_epoch,
         "Cancellation-Intent-Hash": intent_hash},
    )

    current = barrier
    revert_commits = []
    for story, item_oid in integrated:
        if story not in contexts or not contexts[story]["item_oid"]:
            continue
        current = revert_merge_candidate(
            root, current, item_oid, f"Revert Item {story} for {delivery_id}",
            {"Record": "cancellation-revert-v1", "Protocol": "1", "Delivery": delivery_id,
             "Story": story, "Previous-Tip": item_oid, "Barrier-Epoch": barrier_epoch},
        )
        revert_commits.append(current)

    item_candidates: dict[str, str] = {}
    item_replacements: dict[str, str] = {}
    final_stories: dict[str, dict[str, str]] = {}
    for story, context in contexts.items():
        disposition = stories[story]["disposition"]
        if context["item_oid"]:
            props = dict(context["props"])
            body = context["body"]
            props["status"] = "cancelled"
            props["tags"] = [tag for tag in props.get("tags", []) if not str(tag).startswith("status/")] + ["status/cancelled"]
            props["cancellation_disposition"] = disposition
            props["cancellation_previous_tip"] = context["item_oid"]
            props["cancellation_intent_hash"] = intent_hash
            props["source_hash"] = content_hash(props, body)
            item_candidate = commit_replacements(
                root, context["item_oid"], {context["path"]: frontmatter(props, body)},
                f"Cancel Item {story} for {delivery_id}",
                {"Record": "item-cancelled-v1", "Protocol": "1", "Delivery": delivery_id,
                 "Story": story, "Disposition": disposition, "Previous-Tip": context["item_oid"],
                 "Barrier-Epoch": barrier_epoch, "Cancellation-Intent-Hash": intent_hash},
            )
            item_candidates[story] = item_candidate
            item_replacements[context["path"]] = frontmatter(props, body)
            final_stories[story] = {"disposition": disposition, "tip": context["item_oid"]}
        else:
            try:
                props, body = split_remote_note(root, current, context["path"], split_note)
            except RuntimeError:
                continue
            props["status"] = "cancelled"
            props["tags"] = [tag for tag in props.get("tags", []) if not str(tag).startswith("status/")] + ["status/cancelled"]
            props["cancellation_disposition"] = "not_started"
            props["cancellation_previous_tip"] = "none"
            props["cancellation_intent_hash"] = intent_hash
            props["source_hash"] = content_hash(props, body)
            item_replacements[context["path"]] = frontmatter(props, body)
            final_stories[story] = {"disposition": "not_started", "tip": "none"}

    projection_hash = cancellation_projection_hash(
        delivery_id, intent_hash, final_stories, target, barrier_epoch,
    )
    cancelled_props = dict(intent_props)
    cancelled_props["status"] = "cancelled"
    cancelled_props["tags"] = [tag for tag in cancelled_props.get("tags", []) if not str(tag).startswith("status/")] + ["status/cancelled"]
    cancelled_props["cancellation_projection_hash"] = projection_hash
    cancelled_props["source_hash"] = content_hash(cancelled_props, remote_body)
    item_replacements[relative_delivery] = frontmatter(cancelled_props, remote_body)
    finalization = commit_replacements(
        root, current, item_replacements,
        f"Finalize cancellation for {delivery_id}",
        {"Record": "cancellation-finalized-v1", "Protocol": "1", "Delivery": delivery_id,
         "Barrier-Epoch": barrier_epoch, "Cancellation-Intent-Hash": intent_hash,
         "Cancellation-Projection-Hash": projection_hash, "Target": target},
    )

    review_subject = str(remote_props.get("goal", delivery_id)).strip()
    review_props = {
        "type": "delivery-review", "id": f"{delivery_id}-REVIEW",
        "title": f"Outcome review for {review_subject}",
        "status": "approved", "derives_from": [f"[[{delivery_path_value.relative_to(docs).with_suffix('').as_posix()}|{delivery_id}]]"],
        "scope_hash": scope_hash, "cancellation_intent_hash": intent_hash,
        "cancellation_projection_hash": projection_hash, "reviewed_integration_commit": finalization,
        "approved_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "tags": ["doc/delivery-review", "status/approved"],
    }
    review_body = body_for("delivery-review", review_props["title"], {
        "Goal Outcome": remote_props.get("goal", ""),
        "Verdict": "Cancellation approved and finalized with exact Item dispositions.",
        "Cancellation": reason.strip(), "Cancellation Projection": _canonical_json(projection),
        "Navigation": f"[[{delivery_path_value.relative_to(docs).with_suffix('').as_posix()}|{delivery_id}]]",
    })
    review_props["approval_hash"] = content_hash(review_props, review_body, exclude={"status", "approved_at_utc", "source_hash", "approval_hash"})
    review_props["source_hash"] = content_hash(review_props, review_body)
    relative_review = rel_posix(root, directory / "delivery-review.md")
    review = commit_replacements(
        root, finalization, {relative_review: frontmatter(review_props, review_body)},
        f"Publish delivery review for {delivery_id}",
        {"Record": "delivery-review-published-v1", "Protocol": "1", "Delivery": delivery_id,
         "Reviewed-Integration": finalization, "Approval-Hash": review_props["approval_hash"],
         "Target": target, "Cancellation-Intent-Hash": intent_hash,
         "Cancellation-Projection-Hash": projection_hash},
        delivery_projections=True,
    )
    fence_candidate = commit_tree(
        root, fence_oid, [], "Fence project in open mode",
        {"Record": "project-fence-v2", "Protocol": "2", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(), "Target": target,
         "Governance-Hash": trailer(fence_message, "Governance-Hash") or "none",
         **carried_fence_barrier(fence_message)},
    )
    updates = [(refs["fence"], fence_oid, fence_candidate),
               (refs["integration"], integration_oid, review)]
    for story, item_candidate in sorted(item_candidates.items()):
        context = contexts[story]
        updates.append((context["item_ref"], context["item_oid"], item_candidate))
        if context["slot"] is not None:
            updates.append((f"refs/heads/agentrof/slots/{context['slot']}", context["item_oid"], ""))
    atomic_push(root, remote, updates)
    return {"ok": True, "delivery": delivery_id, "status": "cancelled", "intent": intent,
            "barrier": barrier, "reverts": revert_commits, "finalization": finalization,
            "review": review, "fence": fence_candidate, "cancellation_intent_hash": intent_hash,
            "cancellation_projection_hash": projection_hash, "refs": short_refs(delivery_id)}


def reserve_delivery(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    """Reserve an approved, unpublished Delivery under the project Fence.

    The Integration ref must be absent. A project without a Fence gets a new
    one on the target tip, and both refs are created in one atomic push.
    A locally approved Execution Plan may precede this reservation after an
    interrupted planning session; publication and Item claims still follow.

    A merged Delivery drops its Integration ref and integrated Item refs but
    leaves the Fence, so every later reservation meets it. That Fence is taken
    over only while it is idle: open, with no barrier and no source or
    target-update intent, held by no other Delivery's Integration ref or Slot,
    and carrying the approved Governance, which a reservation never changes;
    apply-governance does. A Delivery whose PR the target merged is refused,
    as by every other writer. Only a reservation onto an existing Fence can
    meet one, since that Delivery's reservation left a Fence no verb deletes.
    The reservation writes a Fence child with a new Epoch, as a new Fence
    gets, and the target tip as its Target, the baseline the new Integration
    starts from and writer readiness compares, then pushes it with the
    Integration in one atomic push that leases the Fence tip. No reader
    compares an Epoch: Delivery writers carry it, and handoff and migration
    transitions and barrier releases replace it.

    One open Delivery per project remains the limit. A reservation beside
    another open Delivery or an active Slot is refused, because no protocol
    lets concurrent Deliveries share the Fence Target and Epoch yet.
    """
    root = main_worktree(project_root.resolve())
    from delivery_compile import delivery_findings, docs_root, split_note
    docs = docs_root(root)
    directory, findings = delivery_findings(docs, delivery_id)
    if directory is None or findings:
        raise RuntimeError("Delivery package is not portable: " + "; ".join(findings))
    delivery_path_value = directory / "delivery.md"
    props, _ = split_note(delivery_path_value)
    if props.get("status") not in {"scope_approved", "execution_approved"}:
        raise RuntimeError("reserve-delivery requires scope_approved or execution_approved")
    recorded = open_target_branch(root, remote, checked_target_branch(props.get("target_branch")))
    target_branch, target_oid = resolve_target(root, remote, recorded=recorded)
    refs = canonical_refs(delivery_id)
    if remote_has_ref(root, remote, refs["integration"]):
        raise RuntimeError("DELIVERY_REF_COLLISION: reservation requires an absent Integration ref")
    fence_exists = remote_has_ref(root, remote, refs["fence"])
    if fence_exists:
        refuse_merged_delivery(root, delivery_id, remote)
        _fence_ref, previous_fence, values = _fence_context(root, remote)
        require_fence_takeover(values, lambda: run_git(
            root, "ls-remote", remote, "refs/heads/agentrof/deliveries/*", "refs/heads/agentrof/slots/*"),
            lambda: governed_governance_hash(root))
    package = package_paths(root, directory, docs, include_map=False)
    policy = carried_policy_blobs(root, directory, docs)
    integration_oid = commit_tree(
        root, target_oid, sorted(set(package)),
        f"Reserve Delivery {delivery_id}",
        {"Record": "delivery-reservation-v1", "Protocol": "1", "Delivery": delivery_id,
         "Slug": directory.name.removeprefix(delivery_id.lower() + "-"), "Target": target_oid},
        delivery_projections=True, blobs=policy,
    )
    if fence_exists:
        leased_fence = previous_fence
        fence_oid = _fence_child(root, previous_fence, {**values, "Epoch": epoch_token(), "Target": target_oid},
                                 f"Open Agentrof Fence for {delivery_id}")
    else:
        leased_fence = ""
        fence_oid = commit_tree(
            root, target_oid, [], f"Open Agentrof Fence for {delivery_id}",
            {"Record": "project-fence-v2", "Protocol": "2", "Mode": "open",
             "Epoch": epoch_token(), "Target": target_oid,
             "Governance-Hash": governed_governance_hash(root),
             "Barrier-Kind": "none", "Barrier-Epoch": "none"},
        )
    atomic_push(root, remote, [(refs["fence"], leased_fence, fence_oid), (refs["integration"], "", integration_oid)])
    return {"ok": True, "delivery": delivery_id, "target_branch": target_branch,
            "target": target_oid, "fence": fence_oid, "integration": integration_oid,
            "refs": short_refs(delivery_id)}


FENCE_HOLD_RECOVERY = {
    "Mode": "finish or abort the transition that holds the Fence: finish-source-handoff or abort-source-handoff,"
            " apply-governance, or finish-upgrade or abort-upgrade",
    "Barrier-Kind": "finish-plan-revision or abort-plan-revision, or finish-upgrade or abort-upgrade, of the"
                    " Delivery that began the barrier, or finish its cancellation",
    "Source-Intent": "finish-source-handoff or abort-source-handoff",
    "Target-Update-Intent": "apply-target-update, or reauthorize-target-update for a stale carrier",
}


def fence_holder_recovery(holder: str) -> str:
    """The step that releases one Delivery or Slot ref that holds the Fence."""
    match = re.fullmatch(r"agentrof/deliveries/(dlv-[0-9]{3,})", holder)
    if match:
        delivery_id = match.group(1).upper()
        return (f"{holder} (recovery: closure-audit --delivery {delivery_id} names its outcome; merge-pr merges"
                f" its recorded PR, verify-merge drops the refs of a proven merge, or /deliver {delivery_id}"
                " finishes or cancels it)")
    return (f"{holder} (recovery: the Delivery whose Item it holds, which closure-audit --all names, finishes"
            " the Item with integrate-item or cancels it with cancel-delivery; each releases the Slot atomically)")


def require_fence_takeover(values: dict[str, str], listed, governance_hash) -> None:
    """Refuse to take over a Fence that is not idle and open, that a Delivery or Slot ref in the
    ls-remote output *listed* returns holds, or that lacks the approved *governance_hash*.
    Each refusal names the step that releases what holds the Fence."""
    busy = [key for key, idle in (
        ("Mode", "open"), ("Barrier-Kind", "none"), ("Source-Intent", "none"), ("Target-Update-Intent", "none"),
    ) if values[key] != idle]
    if busy:
        raise RuntimeError("DELIVERY_REF_COLLISION: reservation requires an idle open Fence, not one with "
                           + ", ".join(f"{key} {values[key]}" for key in busy) + "; recovery: "
                           + "; ".join(FENCE_HOLD_RECOVERY[key] for key in busy))
    holders = sorted(line.partition("\t")[2].removeprefix("refs/heads/") for line in listed().splitlines())
    if holders:
        raise RuntimeError("DELIVERY_REF_COLLISION: another Delivery or Slot holds the Fence: "
                           + ", ".join(fence_holder_recovery(holder) for holder in holders))
    if values["Governance-Hash"] != governance_hash():
        raise RuntimeError("DELIVERY_FENCE_GOVERNANCE: the Fence does not carry the approved Governance; "
                           "apply it with apply-governance before reserving")


def execution_operation_inputs(root: Path, directory: Path, docs: Path) -> tuple[list[str], dict[str, dict]]:
    """Select only canonical contracts bound by the approved execution Items."""
    import operation_compile
    from delivery_compile import (OPERATION_BINDING_FIELDS, TERMINAL_ITEM_STATUSES,
                                  item_operation_findings, split_note)

    bindings = {}
    kinds = {"verification"}
    for item in sorted(directory.glob("items/*/item.md")):
        props, _body = split_note(item)
        # A terminal Item names the revision its evidence was produced against, so it
        # still contributes its binding and contract kind but is not compared against
        # a later approved revision.
        if props.get("status") not in TERMINAL_ITEM_STATUSES:
            errors = item_operation_findings(docs, props)
            if errors:
                raise RuntimeError("DELIVERY_PLAN_STALE: Item Operation bindings are invalid: " + "; ".join(errors))
        bindings[item.relative_to(docs).as_posix()] = {
            key: props.get(key) for key in (*OPERATION_BINDING_FIELDS, "runtime_required")}
        if props.get("runtime_required"):
            kinds.add("environment")
    paths = []
    for kind in sorted(kinds):
        path = operation_compile.contract_path(docs, kind)
        if not path.is_file() or path.is_symlink() or path.parent.is_symlink():
            raise RuntimeError(f"Execution publication requires a regular canonical {kind} contract")
        paths.append(rel_posix(root, path))
    return paths, bindings


def pinned_policy_paths(root: Path, directory: Path, docs: Path) -> list[str]:
    """Name the Process Policy the Delivery pins, or nothing without a pin."""
    import process_policy
    from delivery_compile import split_note
    if not split_note(directory / "delivery.md")[0].get("process_policy_path"):
        return []
    return [rel_posix(root, process_policy.path_for(docs))]


def carried_policy_blobs(root: Path, directory: Path, docs: Path) -> dict[str, str]:
    """Select the blob of the pinned Process Policy revision that the Integration carries.

    An Item worktree reads the switch values from its own tree, which comes from
    the Integration, so the pinned revision reaches the Integration with the
    package, as a pinned Operation contract does, even before its own commit
    reaches the target. The package checks that run first compare the pin by
    value, so they also pass a later revision that sets every Delivery switch
    the same way. The checkout's file is therefore carried only when it is the
    pinned revision; otherwise the approved file that the Git history of the
    policy holds under the pinned source hash is, and publication is refused
    when neither holds it.
    """
    import process_policy
    from delivery_compile import split_note

    paths = pinned_policy_paths(root, directory, docs)
    if not paths:
        return {}
    relative = paths[0]
    path = root / relative
    if path.is_symlink() or path.parent.is_symlink() or (path.exists() and not path.is_file()):
        raise RuntimeError("Delivery publication requires the pinned Process Policy as a regular file")
    props = split_note(directory / "delivery.md")[0]
    pin = {key: props.get(key) for key in process_policy.PIN_FIELDS}
    if path.is_file():
        try:
            current = process_policy.pinned_revision(*process_policy.parse(path), pin)
        except (OSError, ValueError):
            current = False
        if current:
            return {relative: run_git(root, "hash-object", "-w", "--", relative)}
    found = process_policy.history_revision(docs, pin)
    if found is None:
        raise RuntimeError(
            f"Delivery publication requires the pinned Process Policy revision"
            f" {pin['process_policy_revision']} ({pin['process_policy_source_hash']}), which is neither"
            f" the checkout's policy nor an approved file in the Git history of {relative}; fetch the"
            " history that holds it, for example by unshallowing a shallow clone, or restore that"
            " approved file from the commit or backup that holds it")
    return {relative: run_git(root, "rev-parse", f"{found[0]}:{relative}")}


def uncarried_operation_contracts(root: Path, docs: Path, integration_oid: str,
                                  pinned_paths: list[str]) -> list[str]:
    """Compare each Operation contract no Item pins with the Integration's copy.

    Publication carries only the contracts the Items pin, so an unpinned revision
    reaches the Delivery through the target branch and refresh-target. A local
    revision that is approved and current, and that the Integration does not
    hold, is refused. Any other local difference, such as a draft, could not be
    published anyway: its path is returned so the result names what stays out.
    The generated relation block and line endings are not compared, because
    publication renders the one and a checkout may convert the other.
    """
    import operation_compile
    from ba_compile import without_generated_relations

    def authored(text: str | None) -> str | None:
        return None if text is None else without_generated_relations(text.replace("\r\n", "\n")).rstrip()

    def held(text: str | None, path: Path) -> str:
        if text is None:
            return "no copy"
        try:
            props = operation_compile.parse_text(text, path)[0]
        except ValueError:
            props = {}
        status, revision = props.get("status"), props.get("revision")
        if isinstance(status, str) and isinstance(revision, int):
            return f"{status} revision {revision}"
        return "another copy"

    not_carried = []
    for kind in sorted(operation_compile.KINDS):
        path = operation_compile.contract_path(docs, kind)
        relative = rel_posix(root, path)
        if relative in pinned_paths:
            continue
        regular = path.is_file() and not path.is_symlink() and not path.parent.is_symlink()
        local = path.read_text(encoding="utf-8") if regular else None
        carried = published_plan_blobs(root, integration_oid, [relative]).get(relative)
        if authored(local) == authored(carried):
            continue
        receipt = operation_compile.check_contract(docs, kind, local)[0] if local is not None else {}
        if receipt.get("current"):
            # Every Item pins the Verification Contract, so only a contract that
            # runtime_required pins can be left unpinned.
            title = operation_compile.TYPE_FOR[kind].replace("-", " ").title()
            revision = receipt["revision"]
            raise RuntimeError(
                f"DELIVERY_OPERATION_UNCARRIED: no Item pins the approved {title} revision {revision}, "
                f"and the Integration holds {held(carried, path)} at {relative}, so publication would "
                f"leave revision {revision} out; record that revision on the target branch and run "
                "refresh-target, or pin it with runtime_required: true on an Item that needs a live "
                "service environment")
        not_carried.append(relative)
    return not_carried


def refuse_superseded_approval(root: Path, directory: Path, docs: Path, integration_oid: str,
                               pinned_paths: list[str]) -> None:
    """Refuse to publish an approval the Integration has already moved past.

    The leases publication takes stop only a concurrent publisher, so a checkout
    that still holds an earlier approval would otherwise put it back. The
    Integration's plan gives way only to the same plan or to an approval that
    lists the Integration's among those it supersedes. A sealed Item keeps its
    Operation bindings, so its plan hash cannot show an older contract: each
    contract the Items pin gives way only to the same approval or a later
    revision.
    """
    import operation_compile
    from ba_compile import parse_frontmatter
    from delivery_compile import split_note

    def front_matter(text: str | None) -> dict | None:
        if text is None:
            return None
        props, _line, error = parse_frontmatter(text)
        return {} if error else props

    plan = directory / "execution-plan.md"
    kinds = {rel_posix(root, operation_compile.contract_path(docs, kind)): kind for kind in operation_compile.KINDS}
    held = published_plan_blobs(root, integration_oid, [rel_posix(root, plan), *pinned_paths])
    published = front_matter(held.get(rel_posix(root, plan)))
    contracts = {path: front_matter(held.get(path)) for path in pinned_paths}
    local = split_note(plan)[0] if plan.is_file() else {}
    require_unsuperseded_approval(kinds, published, local, contracts,
                                  lambda path: operation_compile.parse(root / path)[0])


def require_unsuperseded_approval(kinds: dict[str, str], published: dict | None, local: dict,
                                  contracts: dict[str, dict | None], local_contract) -> None:
    """Refuse a checkout's plan approval *local* that the Integration's *published* plan has moved
    past, or a pinned contract the Integration holds in *contracts* at a later or different revision
    than the checkout's, which *local_contract* reads by path."""
    import operation_compile

    def holds(paths: list[str]) -> str:
        described = []
        for path in paths:
            title = operation_compile.TYPE_FOR[kinds[path]].replace("-", " ").title()
            copy = contracts[path]
            described.append(f"no {title}" if copy is None
                             else f"the {title} {copy.get('status')} at revision {copy.get('revision')}")
        plan_held = "no execution plan" if published is None else f"execution plan {published.get('plan_hash')}"
        return f"the Integration holds {plan_held} and " + " and ".join(described)

    remedy = ("take the Delivery package and the Operation contracts from the Integration, "
              "then revise inside begin-plan-revision")
    lineage = local.get("superseded_plan_approvals")
    if (published is not None and published.get("plan_hash") != local.get("plan_hash")
            and published.get("source_hash") not in (lineage if isinstance(lineage, list) else [])):
        raise RuntimeError(f"DELIVERY_PLAN_SUPERSEDED: {holds(list(contracts))}, which this checkout's approval of "
                           f"execution plan {local.get('plan_hash')} does not supersede; {remedy}")
    for path, copy in contracts.items():
        if not copy or copy.get("status") != "approved" or not isinstance(copy.get("revision"), int):
            continue
        props = local_contract(path)
        revision = props.get("revision") if isinstance(props.get("revision"), int) else 0
        if copy["revision"] > revision or (copy["revision"] == revision
                                           and copy.get("source_hash") != props.get("source_hash")):
            replacement = ("a different " if copy["revision"] == revision else "its ") + f"{props.get('status')} revision {revision}"
            raise RuntimeError(f"DELIVERY_PLAN_SUPERSEDED: {holds([path])}, which this checkout would replace "
                               f"with {replacement}; {remedy}")


def sealed_item_records(root: Path, directory: Path, integration_oid: str) -> set[str]:
    """Name the Item records publication must leave as the Integration holds them.

    A sealed Item record, with its lifecycle, base and stamp, and its approved
    review and verification records reach the Integration only when the Item is
    integrated or cancelled, and nothing brings them back into a checkout's
    package. For an Item the Integration holds that way, publication keeps the
    review and verification records, and keeps the Item record unless the
    checkout's copy has the same lifecycle, base and stamp: the sealed record an
    approval started from, as one that rebinds it for reopen does. The sealed
    evidence binds the whole record, so nothing is merged into it.
    """
    from delivery_compile import TERMINAL_ITEM_STATUSES, split_note
    kept = set()
    for item in integration_item_paths(root, directory, integration_oid):
        relative = rel_posix(root, item)
        sealed, _body = split_remote_note(root, integration_oid, relative, split_note)
        if sealed.get("status") not in TERMINAL_ITEM_STATUSES:
            continue
        kept.update(rel_posix(root, item.parent / name) for name in ("code-review.md", "verification.md"))
        local = split_note(item)[0] if item.is_file() else {}
        if any(local.get(key) != sealed.get(key) for key in ITEM_WRITER_FIELDS):
            kept.add(relative)
    return kept


def publish_execution_plan(project_root: Path, delivery_id: str,
                           remote: str = "origin") -> dict:
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    from delivery_compile import delivery_findings, docs_root
    docs = docs_root(root)
    directory, findings = delivery_findings(docs, delivery_id)
    if directory is None or findings:
        raise RuntimeError("Delivery package is not portable: " + "; ".join(findings))
    delivery_path_value = directory / "delivery.md"
    from delivery_compile import split_note
    props, _ = split_note(delivery_path_value)
    if props.get("status") != "execution_approved":
        raise RuntimeError("publish-execution-plan requires execution_approved")
    refs = canonical_refs(delivery_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    fence_message = commit_message(root, fence_oid)
    require_fence_record(fence_message)
    if trailer(fence_message, "Mode") != "open":
        raise RuntimeError("DELIVERY_FENCE_MODE: publish-execution-plan requires an open Fence")
    refuse_cancelled_delivery(root, directory, integration_oid, "publish-execution-plan")
    refuse_reviewed_delivery(root, directory, integration_oid, delivery_id)
    package = package_paths(root, directory, docs, include_map=False)
    policy = carried_policy_blobs(root, directory, docs)
    operation_paths, operation_bindings = execution_operation_inputs(root, directory, docs)
    refuse_superseded_approval(root, directory, docs, integration_oid, operation_paths)
    not_carried = uncarried_operation_contracts(root, docs, integration_oid, operation_paths)
    sealed = sealed_item_records(root, directory, integration_oid)
    integration_candidate = commit_tree(
        root, integration_oid, sorted(set(package + operation_paths) - sealed), f"Publish execution plan for {delivery_id}",
        {"Record": "execution-plan-published-v1", "Protocol": "1", "Delivery": delivery_id,
         "Scope-Hash": str(props.get("scope_hash", "none")),
         "Plan-Hash": str(props.get("plan_hash", "none")), "Target": trailer(fence_message, "Target") or "none"},
        delivery_projections=True,
        operation_bindings={relative: binding for relative, binding in operation_bindings.items()
                            if rel_posix(root, docs / relative) not in sealed},
        blobs=policy,
    )
    epoch = trailer(fence_message, "Epoch") or epoch_token()
    fence_candidate = commit_tree(
        root, fence_oid, [], f"Publish execution plan for {delivery_id}",
        {"Record": "project-fence-v2", "Protocol": "2", "Mode": "open", "Epoch": epoch,
         "Target": trailer(fence_message, "Target") or "none", "Governance-Hash": trailer(fence_message, "Governance-Hash") or "none",
         **carried_fence_barrier(fence_message)},
    )
    atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                               (refs["integration"], integration_oid, integration_candidate)])
    return {"ok": True, "delivery": delivery_id, "fence": fence_candidate,
            "integration": integration_candidate, "operation_not_carried": not_carried,
            "refs": short_refs(delivery_id)}


def target_impact_hash(delivery_id: str, previous_target: str, target: str,
                       items: dict[str, dict], previous_plan_hash: str = "none",
                       barrier_epoch: str = "none") -> str:
    """Return the canonical target-impact digest for a nonempty mapping."""
    if not OID_RE.fullmatch(previous_target) or not OID_RE.fullmatch(target):
        raise ValueError("DELIVERY_TARGET_IMPACT_INVALID: target impact requires exact previous and current target OIDs")
    value = {
        "barrier_epoch": barrier_epoch,
        "delivery": delivery_id,
        "items": {story: items[story] for story in sorted(items)},
        "previous_plan_hash": previous_plan_hash,
        "previous_target": previous_target,
        "target": target,
    }
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _changed_target_paths(root: Path, previous_target: str, target: str) -> list[str]:
    """List normalized target paths, fetching the target objects when needed."""
    return sorted(git_paths(root, "diff", "--no-renames", "--name-only", "-z", previous_target, target,
                            failure="cannot inspect target drift"))


def fetch_target(root: Path, remote: str, *, recorded: str | None) -> tuple[str, str]:
    branch, _advertised = resolve_target(root, remote, recorded=recorded)
    tracking = f"refs/remotes/{remote}/{branch}"
    run_git(root, "fetch", "--no-tags", remote, f"refs/heads/{branch}:{tracking}")
    return branch, run_git(root, "rev-parse", tracking)


def published_pr_recorded(root: Path, remote: str, delivery_id: str, tip: str, directory: Path) -> bool:
    """Whether the Review published at the Integration *tip* records the Delivery's PR.

    Only that Review says so. An Integration holds none before
    publish-delivery-review, and a Review without pull_request_url recorded
    no PR yet. The commit that records the PR URL in the Review is the one
    that sets awaiting_merge, so while the published Delivery is in
    awaiting_merge a Review that is missing or has no pull_request_url is
    broken, not one that recorded no PR. Such a Review, or a record that
    cannot be read, raises DELIVERY_COORDINATION_CORRUPT naming it, as the
    Delivery compiler reports such a merge state as unknown. Another host may
    have moved the Integration to a commit this checkout lacks, so the ref is
    fetched before its tree is read.
    """
    from delivery_compile import split_note
    ref = canonical_refs(delivery_id)["integration"]
    if subprocess.run(["git", "cat-file", "-e", tip + "^{commit}"], cwd=root,
                      capture_output=True, check=False).returncode:
        run_git(root, "fetch", "--no-tags", remote, ref)

    def unknown(relative: str, reason: str) -> RuntimeError:
        return RuntimeError("DELIVERY_COORDINATION_CORRUPT: Delivery merge state cannot be evaluated: "
                            f"{relative} on {ref.removeprefix('refs/heads/')} {reason}")

    def published(relative: str) -> dict | None:
        try:
            if not git_paths(root, "ls-tree", "-z", "--name-only", tip, "--", relative):
                return None
            return split_remote_note(root, tip, relative, split_note)[0]
        except (RuntimeError, ValueError) as exc:
            raise unknown(relative, f"cannot be read: {exc}") from exc

    review_path = rel_posix(root, directory / "delivery-review.md")
    review = published(review_path)
    if review is not None and review.get("pull_request_url"):
        return True
    status = (published(rel_posix(root, directory / "delivery.md")) or {}).get("status")
    return review_records_pr(ref, review_path, review, status)


def review_records_pr(ref: str, review_path: str, review: dict | None, status) -> bool:
    """Whether the published *review* records the PR, given the published Delivery *status*.

    A Review that is missing or lacks pull_request_url cannot say so while the
    Delivery is awaiting_merge, which only the PR record sets.
    """
    if review is not None and review.get("pull_request_url"):
        return True
    if status != "awaiting_merge":
        return False
    raise RuntimeError("DELIVERY_COORDINATION_CORRUPT: Delivery merge state cannot be evaluated: "
                       f"{review_path} on {ref.removeprefix('refs/heads/')} "
                       + ("is missing" if review is None else "records no pull_request_url")
                       + ", but a Delivery reaches awaiting_merge only with its PR recorded there")


def refuse_merged_delivery(root: Path, delivery_id: str, remote: str = "origin") -> None:
    """Refuse to change a Delivery whose PR the target has merged.

    It decides as the Delivery compiler does. Only a Delivery whose published
    Review records its PR can be merged, so any other passes without fetching
    the target or walking its history, and a published Review that cannot say
    whether the PR was recorded refuses (published_pr_recorded). For a Review
    that records it, the merge proof decides on the freshly fetched target
    tip: a merge without a coordinator record whose second parent is the
    Delivery's recorded PR head. A merged Delivery is closed, so every verb
    that would change its refs calls this first. The merge drops the
    Integration ref, so without one the fetched target alone decides. A
    history that cannot answer the proof, such as a shallow clone, refuses as
    well.
    """
    from delivery_compile import docs_root, find_delivery, recorded_pr_merged
    directory = find_delivery(docs_root(root), delivery_id)
    listed = run_git(root, "ls-remote", remote, canonical_refs(delivery_id)["integration"])
    if directory is None:
        return
    if listed:
        if not published_pr_recorded(root, remote, delivery_id, listed.split()[0], directory):
            return
        _branch, target = fetch_target(root, remote, recorded=recorded_target_branch(root, delivery_id))
        merged = recorded_pr_merged(root, delivery_id, target)
    else:
        merged = merged_record(root, remote, delivery_id) is not None
    require_unmerged(delivery_id, merged)


def require_unmerged(delivery_id: str, merged: bool) -> None:
    """Refuse a change to a Delivery whose PR the target has *merged*."""
    if merged:
        raise RuntimeError(f"DELIVERY_POST_MERGE_TRANSITION: the target has merged the PR of {delivery_id}, "
                           "so the Delivery is closed")


def refuse_cancelled_delivery(root: Path, directory: Path, integration_oid: str, verb: str) -> None:
    """Refuse to continue a Delivery its Integration records as cancelled.

    A cancellation publishes the cancelled status on the Integration alone, so a
    checkout's own delivery.md keeps the status it had and cannot say whether the
    Delivery was cancelled. A cancellation is final: its Review reaches the target
    through its PR, and nothing publishes, revises, refreshes, claims, bars or
    upgrades the Delivery again.
    """
    from delivery_compile import split_note
    props, _body = split_remote_note(root, integration_oid, rel_posix(root, directory / "delivery.md"), split_note)
    require_not_cancelled(props.get("status"), verb)


def require_not_cancelled(status, verb: str) -> None:
    """Refuse *verb* on a Delivery whose published *status* is cancelled."""
    if status == "cancelled":
        raise RuntimeError(f"DELIVERY_CANCELLATION_INVALID: the published Delivery is cancelled and a cancellation "
                           f"is final, so {verb} cannot continue it; its cancellation Review reaches the target "
                           "through its PR")


def refuse_pending_decisions(root: Path, directory: Path, verb: str, stories: list[str] | None = None) -> None:
    """Refuse a step that waits for a pending question of the Delivery's decision log.

    Under two fixed owner gates, a question between the gates is a pending row of
    the User Decisions table in the checkout's delivery.md, where the Delivery
    Coordinator queues it, and only the owner's answer closes it. A row holds the
    Items of *stories* that its blocks column names; without *stories*, every
    pending row holds the step. A table that cannot be read shows no answer, so
    it holds the step as well.
    """
    from delivery_compile import (decision_blocks, decision_rows, docs_root, keeps_decision_log,
                                  pending_rows_text, split_note)
    props, body = split_note(directory / "delivery.md")
    if not keeps_decision_log(docs_root(root), props, body):
        return
    rows, errors = decision_rows(body)
    if errors:
        raise RuntimeError(f"DELIVERY_DECISION_PENDING: {verb} waits until the User Decisions table can be"
                           " read: " + "; ".join(errors))
    if stories is None:
        pending = [row["id"] for row in rows if row["status"] == "pending"]
        if pending:
            raise RuntimeError(f"DELIVERY_DECISION_PENDING: {pending_rows_text(pending)}; gate B asks every"
                               f" queued question, so record the owner's answers before {verb}")
        return
    wanted = {story.casefold(): story for story in stories}
    pending, held = [], []
    for row in rows:
        blocked = [wanted[story.casefold()] for story in decision_blocks(row) if story.casefold() in wanted]
        if row["status"] == "pending" and blocked:
            pending.append(row["id"])
            held.extend(story for story in blocked if story not in held)
    if pending:
        raise RuntimeError(f"DELIVERY_DECISION_PENDING: {', '.join(held)} {'waits' if len(held) == 1 else 'wait'}"
                           f" for the owner's answer to User Decisions {', '.join(pending)}; record it before {verb}")


# Once its Review is published, an Integration's delivery.md records the Delivery
# past its execution plan, and the Review and PR records at its tip carry it to the PR.
PAST_EXECUTION_STATUSES = ("review", "pr_handoff", "awaiting_merge", "merged")
REVIEW_ROUTE_RECORDS = ("delivery-review-published-v1", "delivery-review-invalidated-v1",
                        "pr-creation-intent-v1", "pr-adoption-intent-v1", "pr-url-recorded-v1")


def refuse_reviewed_delivery(root: Path, directory: Path, integration_oid: str, delivery_id: str) -> None:
    """Refuse to publish an execution plan over a Delivery its Integration records past that plan.

    Publication writes the checkout's package over the Integration's, so a checkout
    that still holds the execution_approved package would take a reviewed Delivery
    back to execution_approved and put a plan record on top of the Review, the PR
    intent or the PR record that the PR route reads at the tip.
    """
    from delivery_compile import split_note
    status = split_remote_note(root, integration_oid, rel_posix(root, directory / "delivery.md"),
                               split_note)[0].get("status")
    record = trailer(commit_message(root, integration_oid), "Record")
    if status in PAST_EXECUTION_STATUSES or record in REVIEW_ROUTE_RECORDS:
        raise RuntimeError(f"DELIVERY_PLAN_SUPERSEDED: the Integration records {delivery_id} at {status} with "
                           f"{record} at its tip, past its execution plan, so publication would take it back to "
                           "execution_approved and off the route of its Review and PR; take the Delivery package "
                           "from the Integration")


def direct_update_took_no_effect(root: Path, remote: str, head: str) -> bool:
    """Whether the refetched target proves that a direct target update changed nothing.

    The update moves only the target, to the carrier head, so a target whose
    history lacks that head was not changed by it. A target that cannot be
    refetched proves nothing.
    """
    try:
        _branch, target = fetch_target(root, remote, recorded=open_target_branch(root, remote))
        return not is_ancestor(root, head, target)
    except RuntimeError:
        return False


def require_target_ancestry(root: Path, remote: str, fence_message: str,
                            integration_oid: str, item_oid: str | None = None, *, delivery_id: str) -> str:
    require_fence_record(fence_message)
    if trailer(fence_message, "Mode") != "open":
        raise RuntimeError("DELIVERY_FENCE_MODE: writer readiness requires an open Fence")
    _branch, target = fetch_target(root, remote, recorded=recorded_target_branch(root, delivery_id))
    if trailer(fence_message, "Target") != target:
        raise RuntimeError("DELIVERY_TARGET_DRIFT: target advanced; refresh the Delivery before Item activation")
    if not is_ancestor(root, target, integration_oid):
        raise RuntimeError("DELIVERY_TARGET_CONVERGENCE_REQUIRED: Integration does not contain the current target")
    if item_oid is not None and not is_ancestor(root, target, item_oid):
        raise RuntimeError("DELIVERY_TARGET_CONVERGENCE_REQUIRED: Item does not contain the current target")
    return target


def integration_item_paths(root: Path, directory: Path, integration: str) -> list[Path]:
    """Enumerate exact canonical Item files from the authoritative Git tree."""
    prefix = rel_posix(root, directory / "items") + "/"
    listing = subprocess.run(["git", "ls-tree", "-rz", integration, "--", prefix],
                             cwd=root, capture_output=True, check=True).stdout
    paths = []
    for row in listing.split(b"\0"):
        if not row:
            continue
        metadata, raw_path = row.split(b"\t", 1)
        path = raw_path.decode("utf-8")
        parts = path.removeprefix(prefix).split("/")
        if len(parts) != 2 or parts[1] != "item.md":
            continue
        mode, kind, _oid = metadata.decode().split()
        if mode not in {"100644", "100755"} or kind != "blob":
            raise RuntimeError("Integration Item must be a regular file")
        paths.append(root / path)
    return paths


def target_input_bindings(root: Path, directory: Path, integration: str,
                          target: str, changed: list[str]) -> dict[str, dict]:
    """Reject changed pinned inputs while leaving unchanged target drafts alone."""
    import backlog_compile
    import operation_compile
    import process_policy
    from delivery_compile import OPERATION_BINDING_FIELDS, split_note, content_hash, _is_normalized_claim

    def backlog_record(path):
        return backlog_compile.parse_front_matter(path)[0], backlog_compile.digest(path)

    def dod_record(path):
        props, body = split_note(path)
        return props, content_hash(props, body)

    def operation_record(path):
        props, body = operation_compile.parse(path)
        return props, operation_compile.receipt_hash(props, body)

    delivery, _body = split_remote_note(root, integration, rel_posix(root, directory / "delivery.md"), split_note)
    inputs = {str(delivery["definition_of_done_path"]): (delivery["definition_of_done_source_hash"], dod_record, "approved")}
    bindings = {}
    for item in integration_item_paths(root, directory, integration):
        props, _body = split_remote_note(root, integration, rel_posix(root, item), split_note)
        for kind in ("story", "test_plan"):
            inputs[str(props[kind + "_path"])] = (props[kind + "_source_hash"], backlog_record, "planned" if kind == "story" else "approved")
        if props.get("verification_contract_hash"):
            bindings[item.relative_to(root / "workspace/docs").as_posix()] = {
                key: props.get(key) for key in (*OPERATION_BINDING_FIELDS, "runtime_required")}
            for kind in ("verification", "environment"):
                if props.get(kind + "_contract_hash"):
                    inputs["operation/" + operation_compile.FILE_FOR[kind]] = (
                        props[kind + "_contract_hash"], operation_record, "approved")
    for relative, (expected, reader, status) in inputs.items():
        if not _is_normalized_claim(relative):
            raise RuntimeError("target refresh contains an invalid pinned input path")
        path = "workspace/docs/" + relative
        if path not in changed:
            continue
        props, digest = split_remote_note(root, target, path, reader)
        require_pinned_input(path, props, digest, expected, status)
    # The Process Policy pin is compared only while a new execution approval can
    # re-pin it. From the Delivery Review on it records the policy the Delivery
    # ran under, so a policy set for the next Delivery never strands this one.
    if delivery.get("status") in process_policy.PIN_ENFORCED_STATUSES:
        relative = str(delivery.get("process_policy_path") or process_policy.RELATIVE)
        if not _is_normalized_claim(relative):
            raise RuntimeError("target refresh contains an invalid pinned input path")
        path = "workspace/docs/" + relative
        if path in changed:
            pinned = delivery.get("process_policy_source_hash")
            present = subprocess.run(["git", "cat-file", "-e", f"{target}:{path}"], cwd=root,
                                     capture_output=True, check=False).returncode == 0
            if present:
                props, body = split_remote_note(root, target, path, process_policy.parse)
                current = (props.get("status") == "approved" and pinned is not None
                           and props.get("source_hash") == pinned == process_policy.policy_hash(props, body))
            else:
                current = pinned is None
            if not current:
                raise RuntimeError("DELIVERY_TARGET_SOURCE_VIOLATION: target changed a pinned source or Operation receipt: " + path)
    return bindings


def require_pinned_input(path: str, props: dict, digest: str, expected: str, status: str) -> None:
    """Refuse a target copy of a pinned input that lost its approved *status* or the pinned hash *expected*."""
    if props.get("status") != status or props.get("source_hash") != expected or digest != expected:
        raise RuntimeError("DELIVERY_TARGET_SOURCE_VIOLATION: target changed a pinned source or Operation receipt: " + path)


def refreshed_claim_updates(root: Path, remote: str, delivery_id: str, directory: Path,
                            integration_oid: str, integration_candidate: str,
                            target: str) -> list[tuple[str, str, str]]:
    """Re-issue every untouched Item claim against the refreshed Integration.

    A claim names the Integration an Item will be built on. Refreshing the target
    moves that Integration, so a claim left behind can never contain the current
    target and its Item can never be activated. A claim that already carries work
    is not re-issued: its writer owns convergence. A closed Item is history.
    """
    from delivery_compile import content_hash, frontmatter, split_note

    updates = []
    for item_path in integration_item_paths(root, directory, integration_oid):
        story = item_path.parent.name.upper()
        item_ref = canonical_refs(delivery_id, story)["item"]
        item_oid = own_item_tip(root, remote, delivery_id, story)
        if not item_oid:
            continue
        if trailer(commit_message(root, item_oid), "Record") != "item-claim-v1":
            continue
        if is_ancestor(root, target, item_oid):
            continue
        relative = rel_posix(root, item_path)
        item_props, item_body = split_remote_note(
            root, integration_candidate, relative, split_note)
        item_props["integration_base_commit"] = integration_candidate
        item_props["source_hash"] = content_hash(item_props, item_body)
        delivery_props, _delivery_body = split_remote_note(
            root, integration_candidate,
            rel_posix(root, directory / "delivery.md"), split_note)
        updates.append((item_ref, item_oid, commit_replacements(
            root, integration_candidate,
            {relative: frontmatter(item_props, item_body)},
            f"Refresh claim {story} for {delivery_id}",
            {"Record": "item-claim-v1", "Protocol": "1", "Delivery": delivery_id,
             "Story": story,
             "Scope-Hash": str(delivery_props.get("scope_hash", "none")),
             "Plan-Hash": str(delivery_props.get("plan_hash", "none"))})))
    return updates


def refresh_target(project_root: Path, delivery_id: str,
                   remote: str = "origin") -> dict:
    """Merge a fresh target tip into one open Delivery under the Fence lease.

    Claimed-path and pinned-input changes fail before ref mutation. Compiler-owned
    projections are regenerated from the merged candidate without revising approvals.
    """
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    from delivery_compile import docs_root, find_delivery, split_note
    docs = docs_root(root)
    directory = find_delivery(docs, delivery_id)
    if directory is None:
        raise RuntimeError("Delivery package not found")
    refs = canonical_refs(delivery_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    fence_message = commit_message(root, fence_oid)
    require_fence_record(fence_message)
    if trailer(fence_message, "Mode") != "open":
        raise RuntimeError("DELIVERY_FENCE_MODE: target-refresh requires an open Fence")
    refuse_cancelled_delivery(root, directory, integration_oid, "refresh-target")
    previous_target = trailer(fence_message, "Target")
    if not previous_target or not OID_RE.fullmatch(previous_target):
        raise RuntimeError("DELIVERY_FENCE_CORRUPT: Fence has no valid target baseline")
    recorded = recorded_target_branch(root, delivery_id)
    _target_branch, target = fetch_target(root, remote, recorded=recorded)
    integrated = is_ancestor(root, target, integration_oid)
    if target == previous_target and integrated:
        # The Fence and the Integration are already converged, but a claim issued
        # before an earlier refresh can still be behind. Re-issue those alone.
        claim_updates = refreshed_claim_updates(
            root, remote, delivery_id, directory, integration_oid, integration_oid, target)
        if claim_updates:
            atomic_push(root, remote, claim_updates)
        return {"ok": True, "delivery": delivery_id, "changed": bool(claim_updates),
                "claims_refreshed": [ref for ref, _old, _new in claim_updates],
                "target": target, "refs": short_refs(delivery_id)}
    if not integrated:
        previous_target = unique_merge_base(root, integration_oid, target)
    changed = _changed_target_paths(root, previous_target, target)
    operation_bindings = target_input_bindings(root, directory, integration_oid, target, changed)
    claimed: dict[str, str] = {}
    item_records: dict[str, dict] = {}
    for item_path in integration_item_paths(root, directory, integration_oid):
        story = item_path.parent.name.upper()
        item_oid = own_item_tip(root, remote, delivery_id, story)
        if not item_oid:
            continue
        item_props, _ = split_remote_note(root, item_oid, rel_posix(root, item_path), split_note)
        from delivery_compile import _is_normalized_claim
        for claim in item_props.get("path_claims", []) or []:
            if not isinstance(claim, str) or not _is_normalized_claim(claim):
                raise RuntimeError("claimed Item has an invalid path claim")
            claimed[claim] = story
        item_records[story] = item_props
    reserved = provisional_target_holds(
        root, delivery_provisional_claims(root, remote, delivery_id, integration_oid), item_records)
    from delivery_compile import _claims_overlap
    overlaps = sorted(path for path in changed if any(_claims_overlap(path, claim) for claim in claimed)
                      or any(provisional_overlap(path, claim) for claim in reserved))
    if overlaps:
        raise RuntimeError("DELIVERY_TARGET_SOURCE_VIOLATION: target changed claimed paths " + ", ".join(overlaps))
    final_candidate = merge_candidate(
        root, integration_oid, target, f"Refresh target for {delivery_id}",
        {"Record": "target-refresh-v1", "Protocol": "1", "Delivery": delivery_id,
         "Previous-Target": previous_target, "Target": target,
         "Target-Impact-Hash": "none", "Refresh-Kind": "disjoint"},
        delivery_projections=True, operation_bindings=operation_bindings,
        preserve_delivery=directory,
    )
    fence_candidate = commit_tree(
        root, fence_oid, [], "Refresh project target",
        {"Record": "project-fence-v2", "Protocol": "2", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(), "Target": target,
         "Governance-Hash": trailer(fence_message, "Governance-Hash") or "none",
         **carried_fence_barrier(fence_message)},
    )
    updates = [(refs["fence"], fence_oid, fence_candidate),
               (refs["integration"], integration_oid, final_candidate)]
    updates.extend(refreshed_claim_updates(
        root, remote, delivery_id, directory, integration_oid, final_candidate, target))
    atomic_push(root, remote, updates)
    _target_branch, observed_target = resolve_target(root, remote, recorded=recorded)
    partial = observed_target != target
    return {"ok": True, "delivery": delivery_id, "changed": True, "target": target,
            "previous_target": previous_target, "paths": changed,
            "plan_invalidated": False, "integration": final_candidate,
            "fence": fence_candidate, "current_target": observed_target,
            "partial": partial, "writer_ready": not partial,
            "refs": short_refs(delivery_id)}


def revise_unclaimed_scope(project_root: Path, delivery_id: str,
                           remote: str = "origin") -> dict:
    """Publish a revised pre-claim scope after a fresh local approval."""
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    from delivery_compile import docs_root, find_delivery, split_note
    docs = docs_root(root)
    directory = find_delivery(docs, delivery_id)
    if directory is None:
        raise RuntimeError("Delivery package not found")
    local_props, _ = split_note(directory / "delivery.md")
    if local_props.get("status") != "scope_approved":
        raise RuntimeError("revise-unclaimed-scope requires a scope-approved Delivery")
    refs = canonical_refs(delivery_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    fence_message = commit_message(root, fence_oid)
    require_fence_record(fence_message)
    if trailer(fence_message, "Mode") != "open":
        raise RuntimeError("DELIVERY_FENCE_MODE: revise-unclaimed-scope requires an open Fence")
    refuse_cancelled_delivery(root, directory, integration_oid, "revise-unclaimed-scope")
    for item_path in sorted(directory.glob("items/*/item.md")):
        story = item_path.parent.name.upper()
        if own_item_tip(root, remote, delivery_id, story):
            raise RuntimeError(f"DELIVERY_CLAIM_CONFLICT: scope revision is forbidden after Item claim: {story}")
    occupied = remote_slot_oids(root, remote)
    if occupied:
        raise RuntimeError("scope revision requires no active global Slot")
    previous_scope = trailer(commit_message(root, integration_oid), "Scope-Hash") or "none"
    target_branch, target = resolve_target(root, remote, recorded=recorded_target_branch(root, delivery_id))
    base = integration_oid
    if trailer(fence_message, "Target") and trailer(fence_message, "Target") != target:
        run_git(root, "fetch", "--no-tags", remote, f"refs/heads/{target_branch}:refs/remotes/{remote}/{target_branch}")
        base = merge_candidate(
            root, integration_oid, target, f"Refresh target before scope revision for {delivery_id}",
            {"Record": "target-refresh-v1", "Protocol": "1", "Delivery": delivery_id,
             "Previous-Target": trailer(fence_message, "Target"), "Target": target,
             "Target-Impact-Hash": "none", "Refresh-Kind": "scope_revision"},
        )
    package = package_paths(root, directory, docs, include_map=False)
    candidate = commit_tree(
        root, base, package, f"Revise scope for {delivery_id}",
        {"Record": "delivery-scope-revised-v1", "Protocol": "1", "Delivery": delivery_id,
         "Previous-Scope-Hash": previous_scope, "Scope-Hash": str(local_props.get("scope_hash", "none")),
         "Target": target},
        delivery_projections=True,
    )
    fence_candidate = commit_tree(
        root, fence_oid, [], "Fence project in open mode",
        {"Record": "project-fence-v2", "Protocol": "2", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(), "Target": target,
         "Governance-Hash": trailer(fence_message, "Governance-Hash") or "none",
         **carried_fence_barrier(fence_message)},
    )
    atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                               (refs["integration"], integration_oid, candidate)])
    return {"ok": True, "delivery": delivery_id, "integration": candidate,
            "fence": fence_candidate, "previous_scope_hash": previous_scope,
            "scope_hash": local_props.get("scope_hash", "none"), "target": target,
            "refs": short_refs(delivery_id)}


def carried_fence_barrier(fence_message: str) -> dict[str, str]:
    """Carry an active barrier across a Fence writer that does not own it.

    Only the barrier verbs install and release a barrier. Every other Fence
    child must restate the two trailers it inherited, because a Fence commit
    that omits them reads back as ``none`` and silently clears the barrier.
    """
    return {
        "Barrier-Kind": trailer(fence_message, "Barrier-Kind") or "none",
        "Barrier-Epoch": trailer(fence_message, "Barrier-Epoch") or "none",
    }


def require_fence_record(message: str) -> None:
    """Refuse a Fence that is not a protocol-2 record, naming the migration a protocol-1 one needs.

    Only upgrade-fence-v1 reads a protocol-1 Fence, so every other reader points there
    instead of calling that Fence corrupt or closed.
    """
    record = trailer(message, "Record")
    if record == "project-fence-v1":
        raise RuntimeError("DELIVERY_PROTOCOL_UNSUPPORTED: the Fence is protocol 1; migrate it with "
                           "upgrade-fence-v1 before new mutations")
    if record != "project-fence-v2":
        raise RuntimeError("DELIVERY_FENCE_CORRUPT: current Fence record is unsupported")


def _fence_context(root: Path, remote: str) -> tuple[str, str, dict[str, str]]:
    """Read the current Fence tip and its closed control trailers."""
    ref = canonical_refs("DLV-000")["fence"]
    fence_oid = remote_oid(root, remote, ref)
    message = commit_message(root, fence_oid)
    require_fence_record(message)
    protocol = trailer(message, "Protocol")
    if protocol != "2":
        raise RuntimeError("DELIVERY_PROTOCOL_UNSUPPORTED: Fence protocol is not 2; migrate the Fence before new mutations")
    values = {key: trailer(message, key) or "none" for key in FENCE_CANONICAL_KEYS}
    values["Barrier-Kind"] = trailer(message, "Barrier-Kind") or "none"
    values["Barrier-Epoch"] = trailer(message, "Barrier-Epoch") or "none"
    _validate_fence_values(values)
    return ref, fence_oid, values


def _fence_child(root: Path, fence_oid: str, values: dict[str, str], subject: str) -> str:
    canonical = {key: values.get(key, "none") for key in FENCE_CANONICAL_KEYS}
    canonical["Epoch"] = values.get("Epoch", epoch_token())
    canonical["Target"] = values.get("Target", "none")
    canonical["Mode"] = values.get("Mode", "open")
    canonical["Source-Intent"] = values.get("Source-Intent", "none")
    canonical["Target-Update-Attempt"] = values.get("Target-Update-Attempt", "none")
    if canonical["Mode"] == "open" and canonical["Target"] == "none":
        raise RuntimeError("DELIVERY_FENCE_CORRUPT: open Fence requires a target")
    _validate_fence_values(canonical)
    trailers = {
        "Record": "project-fence-v2", "Protocol": "2", **canonical,
        "Barrier-Kind": values.get("Barrier-Kind", "none"),
        "Barrier-Epoch": values.get("Barrier-Epoch", "none"),
    }
    return commit_tree(root, fence_oid, [], subject, trailers)


def begin_source_handoff(project_root: Path, source_hash: str = "none",
                         remote: str = "origin",
                         source_kind: str = "requirement_supersession") -> dict:
    """Acquire the shared Fence for a source/configuration handoff."""
    root = main_worktree(project_root.resolve())
    if source_kind not in SOURCE_KINDS - {"none"}:
        raise ValueError("source_kind is unsupported")
    if source_hash != "none" and not re.fullmatch(r"sha256:[0-9a-f]{64}", source_hash):
        raise ValueError("source_hash must be none or a canonical sha256 digest")
    ref = canonical_refs("DLV-000")["fence"]
    try:
        _checked_ref, fence_oid, values = _fence_context(root, remote)
        if values["Mode"] != "open":
            raise RuntimeError("DELIVERY_FENCE_MODE: source handoff requires an open Fence")
        target = values["Target"]
        if target == "none":
            _branch, target = resolve_target(root, remote, recorded=open_target_branch(root, remote))
        values.update({"Mode": "source_handoff", "Epoch": epoch_token(), "Target": target,
                       "Source-Kind": source_kind, "Source-Intent": source_hash,
                       "Barrier-Kind": "none",
                       "Barrier-Epoch": "none", "Target-Update-Intent": "none",
                       "Target-Update-Attempt": "none"})
        candidate = _fence_child(root, fence_oid, values, "Acquire source handoff Fence")
        atomic_push(root, remote, [(ref, fence_oid, candidate)])
    except RuntimeError as exc:
        if "remote ref is absent" not in str(exc).lower() and "does not exist" not in str(exc).lower():
            raise
        _branch, target = resolve_target(root, remote, recorded=open_target_branch(root, remote))
        values = {"Mode": "source_handoff", "Epoch": epoch_token(), "Target": target,
                  "Governance-Hash": "none", "Source-Kind": source_kind,
                  "Source-Intent": source_hash,
                  "Barrier-Kind": "none", "Barrier-Epoch": "none",
                  "Target-Update-Intent": "none", "Target-Update-Attempt": "none"}
        candidate = commit_tree(root, target, [], "Acquire source handoff Fence", {
            "Record": "project-fence-v2", "Protocol": "2", **values})
        atomic_push(root, remote, [(ref, "", candidate)])
        fence_oid = ""
    return {"ok": True, "mode": "source_handoff", "fence": candidate,
            "previous_fence": fence_oid, "source_hash": source_hash, "refs": {"fence": ref}}


def authorize_target_update(project_root: Path, mode: str = "source_handoff",
                            candidate_hash: str = "none", remote: str = "origin",
                            carrier_kind: str = "none", carrier_ref: str = "none",
                            carrier_object: str = "none", carrier_head: str = "none",
                            carrier_base: str = "none",
                            target_repository: str = "none") -> dict:
    """Install the durable target-update intent before an external target write."""
    root = main_worktree(project_root.resolve())
    ref, fence_oid, values = _fence_context(root, remote)
    if values["Mode"] != mode:
        raise RuntimeError(f"DELIVERY_FENCE_MODE: expected {mode}, found {values['Mode']}")
    if values["Target-Update-Intent"] != "none":
        raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: target-update intent already exists")
    if candidate_hash == "none" or not re.fullmatch(r"sha256:[0-9a-f]{64}", candidate_hash):
        raise ValueError("candidate_hash must be a canonical sha256 digest")
    if carrier_kind not in {"github_pr", "direct_target"}:
        raise ValueError("DELIVERY_TARGET_CARRIER_INVALID: carrier_kind must be github_pr or direct_target")
    if target_repository not in {"upstream"} and not re.fullmatch(
            r"github:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", target_repository):
        raise ValueError("DELIVERY_TARGET_CARRIER_INVALID: target_repository must be upstream or github:<owner>/<repo>")
    if (not carrier_ref.startswith("refs/heads/") or carrier_ref.startswith("refs/heads/agentrof/")
            or not OID_RE.fullmatch(carrier_head) or not OID_RE.fullmatch(carrier_base)):
        raise ValueError("DELIVERY_TARGET_CARRIER_INVALID: carrier ref/head/base is invalid")
    if carrier_kind == "github_pr" and not re.fullmatch(r"pr:[1-9][0-9]*", carrier_object):
        raise ValueError("DELIVERY_TARGET_CARRIER_INVALID: github_pr carrier_object must be pr:<number>")
    if carrier_kind == "direct_target" and carrier_object != "direct":
        raise ValueError("DELIVERY_TARGET_CARRIER_INVALID: direct_target carrier_object must be direct")
    observed_carrier = remote_oid(root, remote, carrier_ref)
    if observed_carrier != carrier_head:
        raise RuntimeError("DELIVERY_TARGET_CARRIER_INVALID: carrier ref does not equal carrier head")
    values["Target-Update-Intent"] = candidate_hash
    values.update({
        "Target-Update-Attempt": epoch_token(),
        "Target-Repository": target_repository, "Target-Carrier-Kind": carrier_kind,
        "Target-Carrier-Ref": carrier_ref, "Target-Carrier-Object": carrier_object,
        "Target-Carrier-Head": carrier_head, "Target-Carrier-Base": carrier_base,
    })
    candidate = _fence_child(root, fence_oid, values, "Authorize target update")
    receipt = create_target_update_receipt(
        root, mode, values["Target-Update-Attempt"], candidate, candidate_hash,
        target_repository, carrier_kind, carrier_ref, carrier_object,
        carrier_head, carrier_base,
    )
    try:
        atomic_push(root, remote, [(ref, fence_oid, candidate)])
    except Exception:
        # Only a Fence that provably never took the candidate lets the prepared
        # receipt go, so a fresh authorization can retry; a Fence that may carry
        # the intent keeps the receipt its handoff needs.
        if push_never_landed(root, remote, ref, candidate):
            try:
                discard_target_update_receipt(root, mode, values["Target-Update-Attempt"])
            except Exception:
                pass
        raise
    return {"ok": True, "mode": mode, "fence": candidate,
            "target_update_intent": candidate_hash, "attempt": values["Target-Update-Attempt"],
            "receipt": receipt,
            "carrier": {key: values[key] for key in (
                "Target-Repository", "Target-Carrier-Kind", "Target-Carrier-Ref",
                "Target-Carrier-Object", "Target-Carrier-Head", "Target-Carrier-Base")}}


def finish_source_handoff(project_root: Path, remote: str = "origin") -> dict:
    """Close a source/config handoff only after the target is observable."""
    root = main_worktree(project_root.resolve())
    ref, fence_oid, values = _fence_context(root, remote)
    if values["Mode"] not in {"source_handoff", "governance", "upgrade"}:
        raise RuntimeError("DELIVERY_FENCE_MODE: no source handoff is active")
    if values["Target-Update-Intent"] == "none":
        raise RuntimeError("DELIVERY_TARGET_CONVERGENCE_REQUIRED: finish-source-handoff requires an authorized target-update intent")
    _branch, target = resolve_target(root, remote, recorded=open_target_branch(root, remote))
    handoff_mode = values["Mode"]
    attempt = values["Target-Update-Attempt"]
    if attempt != "none":
        # The target writer must explicitly prove the provider/direct
        # postcondition before the Fence is released. A prepared receipt is
        # never treated as a successful external handoff.
        receipt_path, _lock = target_receipt_paths(root, values["Mode"])
        if receipt_path.exists():
            receipt = _validate_target_receipt(json.loads(receipt_path.read_text(encoding="utf-8")))
            if receipt["state"] != "verified":
                raise RuntimeError("DELIVERY_TARGET_CONVERGENCE_REQUIRED: target update is not verified")
        else:
            raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: target update receipt is missing")
    values.update({"Mode": "open", "Epoch": epoch_token(), "Target": target,
                   "Source-Kind": "none", "Source-Intent": "none",
                   "Barrier-Kind": "none", "Barrier-Epoch": "none",
                   "Target-Update-Intent": "none", "Target-Update-Attempt": "none",
                   "Target-Repository": "none", "Target-Carrier-Kind": "none",
                   "Target-Carrier-Ref": "none", "Target-Carrier-Object": "none",
                   "Target-Carrier-Head": "none", "Target-Carrier-Base": "none",
                   "Upgrade-Phase": "none",
                   "Upgrade-Contract": "none", "Handoff-Target": "none"})
    candidate = _fence_child(root, fence_oid, values, "Finish source handoff")
    atomic_push(root, remote, [(ref, fence_oid, candidate)])
    clear_target_update_receipt(root, handoff_mode, attempt)
    return {"ok": True, "mode": "open", "fence": candidate, "target": target}


def abort_source_handoff(project_root: Path, remote: str = "origin") -> dict:
    """Abort only an acquired handoff whose external write never began or took no effect.

    Past a target-update intent, only the host whose receipt holds the current
    direct attempt still prepared can abort, and only while the target does not
    contain the carrier head. It holds that receipt's lock throughout, so no
    update call can start meanwhile.
    """
    root = main_worktree(project_root.resolve())
    ref, fence_oid, values = _fence_context(root, remote)
    if values["Mode"] not in {"source_handoff", "governance", "upgrade"}:
        raise RuntimeError("DELIVERY_FENCE_MODE: no source handoff is active")
    receipt_path, lock_path = target_receipt_paths(root, values["Mode"])
    intent, attempt = values["Target-Update-Intent"], values["Target-Update-Attempt"]
    with contextlib.ExitStack() as held:
        if intent != "none":
            held.enter_context(receipt_lock(lock_path))
            receipt = (_validate_target_receipt(json.loads(receipt_path.read_text(encoding="utf-8")))
                       if receipt_path.exists() else None)
            if (values["Target-Carrier-Kind"] != "direct_target" or receipt is None
                    or receipt["attempt"] != attempt or receipt["state"] != "prepared"
                    or not direct_update_took_no_effect(root, remote, values["Target-Carrier-Head"])):
                raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: abort after a target-update intent "
                                   "requires this host's prepared direct attempt, absent from the target")
        values.update({"Mode": "open", "Epoch": epoch_token(), "Source-Kind": "none",
                       "Source-Intent": "none",
                       "Barrier-Kind": "none", "Barrier-Epoch": "none",
                       "Target-Update-Intent": "none", "Target-Update-Attempt": "none",
                       "Target-Repository": "none", "Target-Carrier-Kind": "none",
                       "Target-Carrier-Ref": "none", "Target-Carrier-Object": "none",
                       "Target-Carrier-Head": "none", "Target-Carrier-Base": "none",
                       "Upgrade-Phase": "none",
                       "Upgrade-Contract": "none", "Handoff-Target": "none"})
        candidate = _fence_child(root, fence_oid, values, "Abort source handoff")
        atomic_push(root, remote, [(ref, fence_oid, candidate)])
        if intent != "none":
            receipt_path.unlink()
            _fsync_directory(receipt_path.parent)
    return {"ok": True, "mode": "open", "fence": candidate}


# The command that begins each barrier kind a Delivery takes.
BARRIER_BEGIN_VERBS = {"plan-revision": "begin-plan-revision", "upgrade": "quiesce-upgrade"}


def published_cancellation(root: Path, remote: str, delivery_id: str) -> bool:
    """Whether the Integration, or the target once the merge dropped it, records the Delivery cancelled.

    A cancellation writes the cancelled status on the Integration alone, and its
    PR carries it to the target.
    """
    from delivery_compile import docs_root, find_delivery, split_note
    directory = find_delivery(docs_root(root), delivery_id)
    if directory is None:
        return False
    ref = canonical_refs(delivery_id)["integration"]
    try:
        source = remote_ref_oids(root, remote, [ref])[ref] or fetch_target(
            root, remote, recorded=recorded_target_branch(root, delivery_id))[1]
        props, _body = split_remote_note(root, source, rel_posix(root, directory / "delivery.md"), split_note)
    except RuntimeError:
        return False
    return props.get("status") == "cancelled"


def _barrier_transition(project_root: Path, kind: str, action: str,
                        delivery_id: str | None = None, remote: str = "origin") -> dict:
    """Install or release a lightweight barrier on existing coordination refs.

    A cancellation is final and its Review stays at the Integration tip for its
    PR, so a plan revision barrier that a cancellation carried, as one could
    before cancel-delivery refused a barrier, is released on the Fence alone,
    also once the merge dropped the Integration ref.
    """
    root = main_worktree(project_root.resolve())
    validate_delivery_id(delivery_id or "DLV-000") if delivery_id else None
    fence_ref, fence_oid, values = _fence_context(root, remote)
    integration_ref = canonical_refs(delivery_id)["integration"] if delivery_id else None
    if (action != "begin" and kind == "plan-revision" and integration_ref
            and published_cancellation(root, remote, delivery_id)):
        integration_ref = None
    integration_oid = remote_oid(root, remote, integration_ref) if integration_ref else None
    if action == "begin":
        if values["Mode"] != "open" or values["Barrier-Kind"] != "none":
            raise RuntimeError("DELIVERY_BARRIER_ACTIVE: an incompatible Fence barrier is already active")
        if integration_ref:
            from delivery_compile import docs_root, find_delivery
            directory = find_delivery(docs_root(root), delivery_id)
            if directory is None:
                raise RuntimeError("Delivery package not found")
            # The barrier record would bury the cancellation Review its PR needs at the tip.
            refuse_cancelled_delivery(root, directory, integration_oid, BARRIER_BEGIN_VERBS[kind])
        epoch = epoch_token()
        values.update({"Barrier-Kind": kind, "Barrier-Epoch": epoch,
                       "Mode": "upgrade" if kind == "upgrade" else "open"})
        if kind == "upgrade":
            values.update({
                "Upgrade-Phase": "acquired",
                "Upgrade-Contract": "sha256:" + hashlib.sha256(
                    f"agentrof-upgrade-v1:{delivery_id}:{epoch}".encode("utf-8")
                ).hexdigest(),
                "Handoff-Target": "none",
            })
        fence_candidate = _fence_child(root, fence_oid, values, f"Begin {kind} barrier")
        updates = [(fence_ref, fence_oid, fence_candidate)]
        integration_candidate = None
        if integration_ref:
            integration_candidate = commit_tree(
                root, integration_oid, [], f"Begin {kind} barrier for {delivery_id}",
                {"Record": "delivery-barrier-v1", "Protocol": "1", "Delivery": delivery_id,
                 "Barrier-Kind": kind, "Barrier-Epoch": epoch,
                 "Cancellation-Intent-Hash": "none",
                 "Upgrade-Contract": values.get("Upgrade-Contract", "none")},
            )
            updates.append((integration_ref, integration_oid, integration_candidate))
        atomic_push(root, remote, updates)
        return {"ok": True, "action": "begin", "barrier_kind": kind,
                "barrier_epoch": epoch, "fence": fence_candidate,
                "integration": integration_candidate}
    if values["Barrier-Kind"] != kind or values["Barrier-Epoch"] == "none":
        raise RuntimeError("DELIVERY_BARRIER_ACTIVE: requested barrier is not the current barrier")
    if action == "abort" and kind == "cancellation":
        raise RuntimeError("DELIVERY_CANCELLATION_INVALID: cancellation barriers are irreversible")
    barrier_epoch = values["Barrier-Epoch"]
    values.update({
        "Barrier-Kind": "none", "Barrier-Epoch": "none", "Mode": "open",
        "Epoch": epoch_token(), "Upgrade-Phase": "none",
        "Upgrade-Contract": "none", "Handoff-Target": "none",
    })
    fence_candidate = _fence_child(root, fence_oid, values, f"Release {kind} barrier")
    updates = [(fence_ref, fence_oid, fence_candidate)]
    integration_candidate = None
    released = []
    if integration_ref:
        # Each live provisional claim of the barrier ends in the same transaction as the barrier.
        base = integration_oid
        if kind == "plan-revision":
            from delivery_compile import docs_root, find_delivery
            directory = find_delivery(docs_root(root), delivery_id)
            released = provisional_dispositions(root, remote, delivery_id, directory, integration_oid,
                                                barrier_epoch, action) if directory is not None else []
        for story, disposition, claim_record in released:
            base = commit_tree(
                root, base, [], f"Release provisional claim {story} for {delivery_id}",
                {"Record": "provisional-claim-release-v1", "Protocol": "1", "Delivery": delivery_id,
                 "Story": story, "Barrier-Epoch": barrier_epoch, "Claim-Record": claim_record,
                 "Disposition": disposition})
        integration_candidate = commit_tree(
            root, base, [], f"Release {kind} barrier for {delivery_id}",
             {"Record": "delivery-barrier-release-v1", "Protocol": "1", "Delivery": delivery_id,
             "Barrier-Kind": kind, "Barrier-Epoch": barrier_epoch,
             "Barrier-OID": integration_oid, "Target": values.get("Target", "none"),
             "Plan-Hash": "none", "Target-Impact-Hash": "none",
             "Upgrade-Contract": values.get("Upgrade-Contract", "none")},
        )
        updates.append((integration_ref, integration_oid, integration_candidate))
    atomic_push(root, remote, updates)
    result = {"ok": True, "action": action, "barrier_kind": kind,
              "fence": fence_candidate, "integration": integration_candidate}
    if released:
        result["provisional_claims"] = [{"story": story, "disposition": disposition}
                                        for story, disposition, _record in released]
    return result


def begin_plan_revision(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    refuse_merged_delivery(main_worktree(project_root.resolve()), delivery_id, remote)
    return _barrier_transition(project_root, "plan-revision", "begin", delivery_id, remote)


def finish_plan_revision(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    return _barrier_transition(project_root, "plan-revision", "finish", delivery_id, remote)


def abort_plan_revision(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    return _barrier_transition(project_root, "plan-revision", "abort", delivery_id, remote)


def begin_upgrade(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    refuse_merged_delivery(main_worktree(project_root.resolve()), delivery_id, remote)
    return _barrier_transition(project_root, "upgrade", "begin", delivery_id, remote)


def finish_upgrade(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    return _barrier_transition(project_root, "upgrade", "finish", delivery_id, remote)


def abort_upgrade(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    return _barrier_transition(project_root, "upgrade", "abort", delivery_id, remote)


def upgrade_target_merge(project_root: Path, delivery_id: str,
                         remote: str = "origin") -> dict:
    """Merge the current target into one Delivery during an acquired upgrade."""
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    validate_delivery_id(delivery_id)
    from delivery_compile import docs_root, find_delivery, split_note
    directory = find_delivery(docs_root(root), delivery_id)
    if directory is None:
        raise RuntimeError("Delivery package not found")
    fence_ref, fence_oid, values = _fence_context(root, remote)
    if values["Mode"] != "upgrade" or values["Upgrade-Phase"] not in {"acquired", "target_handoff"}:
        raise RuntimeError("DELIVERY_UPGRADE_INCOMPATIBLE: upgrade target merge requires acquired/target_handoff Fence")
    if values["Target-Update-Intent"] == "none" or values["Upgrade-Contract"] == "none":
        raise RuntimeError("DELIVERY_UPGRADE_INCOMPATIBLE: upgrade target intent/contract is missing")
    refs = canonical_refs(delivery_id)
    integration_oid = remote_oid(root, remote, refs["integration"])
    refuse_cancelled_delivery(root, directory, integration_oid, "upgrade-target-merge")
    recorded = recorded_target_branch(root, delivery_id)
    _branch, target = resolve_target(root, remote, recorded=recorded)
    previous_target = values["Handoff-Target"] if values["Upgrade-Phase"] == "target_handoff" and values["Handoff-Target"] != "none" else values["Target"]
    if target == previous_target:
        return {"ok": True, "changed": False, "delivery": delivery_id,
                "target": target, "upgrade_contract": values["Upgrade-Contract"]}
    target_branch, _target_oid = resolve_target(root, remote, recorded=recorded)
    run_git(root, "fetch", "--no-tags", remote,
            f"refs/heads/{target_branch}:refs/remotes/{remote}/{target_branch}")
    props, _ = split_note(directory / "delivery.md")
    candidate = merge_candidate(
        root, integration_oid, target,
        f"Merge target during upgrade for {delivery_id}",
        {"Record": "upgrade-target-merge-v1", "Protocol": "1",
         "Delivery": delivery_id, "Upgrade-Epoch": values["Epoch"],
         "Upgrade-Contract": values["Upgrade-Contract"],
         "Previous-Target": previous_target, "Target": target,
         "Scope-Hash": str(props.get("scope_hash", "none")),
         "Plan-Hash": str(props.get("plan_hash", "none")),
         "Target-Impact-Hash": "none"},
    )
    values["Handoff-Target"] = target if values["Upgrade-Phase"] == "target_handoff" else "none"
    fence_candidate = _fence_child(root, fence_oid, values,
                                   f"Merge target during upgrade for {delivery_id}")
    atomic_push(root, remote, [
        (fence_ref, fence_oid, fence_candidate),
        (refs["integration"], integration_oid, candidate),
    ])
    return {"ok": True, "changed": True, "delivery": delivery_id,
            "previous_target": previous_target, "target": target,
            "integration": candidate, "fence": fence_candidate,
            "upgrade_contract": values["Upgrade-Contract"]}


def begin_applying_governance(project_root: Path, desired_hash: str, remote: str = "origin") -> dict:
    """Acquire the project Fence for an approved Governance handoff."""
    root = main_worktree(project_root.resolve())
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", desired_hash):
        raise ValueError("desired governance hash must be canonical")
    ref, fence_oid, values = _fence_context(root, remote)
    if values["Mode"] != "open" or values["Target-Update-Intent"] != "none":
        raise RuntimeError("DELIVERY_FENCE_MODE: governance requires an open, unoccupied Fence")
    values.update({
        "Mode": "governance", "Epoch": epoch_token(), "Governance-Hash": desired_hash,
        "Source-Kind": "none", "Source-Intent": "none",
        "Target-Repository": "none", "Target-Carrier-Kind": "none",
        "Target-Carrier-Ref": "none", "Target-Carrier-Object": "none",
        "Target-Carrier-Head": "none", "Target-Carrier-Base": "none",
        "Upgrade-Phase": "none", "Upgrade-Contract": "none", "Handoff-Target": "none",
    })
    candidate = _fence_child(root, fence_oid, values, "Begin governance handoff")
    atomic_push(root, remote, [(ref, fence_oid, candidate)])
    return {"ok": True, "mode": "governance", "fence": candidate,
            "governance_hash": desired_hash}


def apply_governance(project_root: Path, *, dry_run: bool = False,
                     remote: str = "origin") -> dict:
    """Bind an already-approved Governance revision to the live Fence.

    This command never edits Governance. Reducing the slot ceiling is safe
    only once every allocated slot lies inside the new range.
    """
    root = main_worktree(project_root.resolve())
    governance, errors = delivery_governance.status(root / "workspace" / "docs")
    if errors or not governance.get("current"):
        raise RuntimeError("apply-governance requires approved/current Delivery Governance: " + "; ".join(errors))
    value = governance["max_parallel"]
    desired = governance["governance_hash"]
    fence_ref = canonical_refs("DLV-000")["fence"]
    if not remote_has_ref(root, remote, fence_ref):
        return {"ok": True, "changed": False, "governance_hash": desired,
                "max_parallel": value, "requires_target_handoff": False}
    occupied = remote_slot_oids(root, remote)
    if any(int(slot) > value for slot in occupied if SLOT_RE.fullmatch(slot)):
        raise RuntimeError("cannot lower max_parallel while an out-of-range Slot is allocated")
    _ref, _oid, fence = _fence_context(root, remote)
    if fence["Governance-Hash"] == desired:
        return {"ok": True, "changed": False, "governance_hash": desired,
                "max_parallel": value, "requires_target_handoff": False}
    if dry_run:
        return {"ok": True, "changed": True, "dry_run": True,
                "governance_hash": desired, "max_parallel": value,
                "requires_target_handoff": True}
    handoff = begin_applying_governance(root, desired, remote)
    return {"ok": True, "changed": True, "governance_hash": desired,
            "max_parallel": value, "handoff": handoff,
            "requires_target_handoff": True}


def upgrade_fence_v1(project_root: Path, *, dry_run: bool = False,
                     remote: str = "origin") -> dict:
    """Perform the one-way, quiescent v1-to-v2 Fence conversion.

    The old Fence is read only for this migration.  It cannot carry a new Item
    mutation: an open Fence with no allocated slots is the only safe source
    state.  Governance is read from its approved vault contract, never from a
    CLI argument or retired config field.
    """
    root = main_worktree(project_root.resolve())
    ref = canonical_refs("DLV-000")["fence"]
    fence_oid = remote_oid(root, remote, ref)
    message = commit_message(root, fence_oid)
    if trailer(message, "Record") != "project-fence-v1" or trailer(message, "Protocol") != "1":
        raise RuntimeError("DELIVERY_PROTOCOL_UNSUPPORTED: upgrade-fence-v1 requires an exact protocol-1 Fence")
    if trailer(message, "Mode") != "open":
        raise RuntimeError("DELIVERY_FENCE_MODE: v1 Fence must be quiesced in open mode before migration")
    if remote_slot_oids(root, remote):
        raise RuntimeError("DELIVERY_UPGRADE_INCOMPATIBLE: v1 Fence migration requires every Delivery Slot to be free")
    target = trailer(message, "Target") or "none"
    if not OID_RE.fullmatch(target):
        raise RuntimeError("DELIVERY_FENCE_CORRUPT: v1 Fence target is invalid")
    prior_epoch = trailer(message, "Epoch") or epoch_token()
    _validate_epoch(prior_epoch)
    governance, errors = delivery_governance.status(root / "workspace" / "docs")
    if errors or not governance.get("current"):
        raise RuntimeError("upgrade-fence-v1 requires approved/current Delivery Governance: " + "; ".join(errors))
    desired = str(governance["governance_hash"])
    if dry_run:
        return {"ok": True, "changed": True, "from_protocol": 1,
                "to_protocol": 2, "governance_hash": desired,
                "requires_target_handoff": False}
    values = {
        "Mode": "open", "Epoch": epoch_token(), "Target": target,
        "Governance-Hash": desired, "Source-Kind": "none", "Source-Intent": "none",
        "Target-Update-Intent": "none", "Target-Update-Attempt": "none",
        "Target-Repository": "none", "Target-Carrier-Kind": "none",
        "Target-Carrier-Ref": "none", "Target-Carrier-Object": "none",
        "Target-Carrier-Head": "none", "Target-Carrier-Base": "none",
        "Upgrade-Phase": "none", "Upgrade-Contract": "none", "Handoff-Target": "none",
        "Barrier-Kind": "none", "Barrier-Epoch": "none",
    }
    candidate = _fence_child(root, fence_oid, values, "Upgrade Delivery Fence protocol to v2")
    atomic_push(root, remote, [(ref, fence_oid, candidate)])
    return {"ok": True, "changed": True, "from_protocol": 1,
            "to_protocol": 2, "fence": candidate, "governance_hash": desired}


def reauthorize_target_update(project_root: Path, mode: str = "source_handoff",
                              candidate_hash: str = "none", remote: str = "origin") -> dict:
    """Rebase one prepared target handoff under an exact Fence/carrier CAS.

    This path is intentionally narrow: it is legal only while the previous
    attempt is still ``prepared`` (therefore no external call could have
    started), the target moved, and the existing carrier ref/PR is unchanged.
    The new carrier is a fast-forward descendant of the old carrier head and
    the Fence plus that exact carrier ref advance atomically.
    """
    root = main_worktree(project_root.resolve())
    _fence_ref, fence_oid, values = _fence_context(root, remote)
    if values["Mode"] != mode or values["Target-Update-Intent"] == "none":
        raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: no matching target intent")
    if candidate_hash not in {"none", values["Target-Update-Intent"]}:
        raise ValueError("candidate_hash does not match the durable target intent")
    old_attempt = values["Target-Update-Attempt"]
    receipt_path, receipt_lock_path = target_receipt_paths(root, mode)
    if not receipt_path.exists():
        raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: target receipt is missing")
    with receipt_lock(receipt_lock_path):
        old_receipt = _validate_target_receipt(json.loads(receipt_path.read_text(encoding="utf-8")))
        if old_receipt["attempt"] != old_attempt or old_receipt["state"] != "prepared":
            raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: only an unspent prepared attempt can be reauthorized")
        target_branch, target = resolve_target(root, remote, recorded=open_target_branch(root, remote))
        old_base = values["Target-Carrier-Base"]
        if target == old_base:
            raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: target did not advance")
        carrier_ref = values["Target-Carrier-Ref"]
        old_head = values["Target-Carrier-Head"]
        if remote_oid(root, remote, carrier_ref) != old_head:
            raise RuntimeError("DELIVERY_TARGET_CARRIER_INVALID: carrier ref moved before reauthorization")
        target_ref = f"refs/heads/{target_branch}"
        fetched_target = f"refs/remotes/{remote}/{target_branch}"
        run_git(root, "fetch", "--no-tags", remote, f"{target_ref}:{fetched_target}")
        carrier_candidate = merge_candidate(
            root, old_head, target,
            "Rebase target carrier",
            {"Record": "target-carrier-reauthorization-v1", "Protocol": "1",
             "Mode": mode, "Previous-Attempt": old_attempt,
             "Previous-Target": old_base, "Target": target},
        )
        new_attempt = epoch_token()
        values.update({
            "Target": target, "Target-Update-Attempt": new_attempt,
            "Target-Carrier-Head": carrier_candidate,
            "Target-Carrier-Base": target,
        })
        if values["Target-Carrier-Kind"] == "github_pr":
            from delivery_provider import GitHubProvider
            repository = values["Target-Repository"].removeprefix("github:")
            provider = GitHubProvider(root, remote)
            if values["Target-Repository"] not in {"upstream", f"github:{provider.repository}"}:
                raise RuntimeError("DELIVERY_TARGET_CARRIER_INVALID: carrier repository does not match the project remote")
            number = values["Target-Carrier-Object"].removeprefix("pr:")
            url = f"https://github.com/{repository}/pull/{number}"
            observed = provider.inspect_pull_request(url)
            head_name = carrier_ref.removeprefix("refs/heads/")
            if (str(observed.get("state", "")).upper() != "OPEN" or not observed.get("isDraft")
                    or observed.get("headRefName") != head_name
                    or observed.get("headRefOid") != old_head
                    or observed.get("baseRefName") != target_branch):
                raise RuntimeError("DELIVERY_TARGET_CARRIER_INVALID: provider PR is not an unchanged draft carrier")
        new_receipt = {
            "schema_version": 1, "kind": "target-update-v1", "state": "prepared",
            "mode": mode, "attempt": new_attempt, "fence_candidate": "pending",
            "intent": values["Target-Update-Intent"],
            "target_repository": values["Target-Repository"],
            "carrier_kind": values["Target-Carrier-Kind"], "carrier_ref": carrier_ref,
            "carrier_object": values["Target-Carrier-Object"],
            "carrier_head": carrier_candidate, "carrier_base": target,
        }
        fence_candidate = _fence_child(root, fence_oid, values, "Reauthorize target update")
        new_receipt["fence_candidate"] = fence_candidate
        new_receipt["receipt_digest"] = receipt_digest(new_receipt)
        _validate_target_receipt(new_receipt)
        _write_provider_receipt_locked(receipt_path, new_receipt)
        try:
            atomic_push(root, remote, [
                (_fence_ref, fence_oid, fence_candidate),
                (carrier_ref, old_head, carrier_candidate),
            ])
        except Exception as exc:
            # Keep the old prepared receipt when the Fence/carrier lease was
            # conclusively rejected; on an ambiguous transport, retain the
            # new receipt and let a fresh clone reconcile the exact pair.
            try:
                observed_fence = remote_oid(root, remote, _fence_ref)
                observed_carrier = remote_oid(root, remote, carrier_ref)
                unchanged = observed_fence == fence_oid and observed_carrier == old_head
                landed = not unchanged and (
                    history_holds(root, remote, _fence_ref, observed_fence, fence_candidate)
                    and history_holds(root, remote, carrier_ref, observed_carrier, carrier_candidate))
            except Exception:
                raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: reauthorization response is ambiguous")
            if unchanged:
                _write_provider_receipt_locked(receipt_path, old_receipt)
                raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: reauthorization lease was rejected")
            if landed:
                # Both refs hold, or moved on from, their candidates, so the push
                # landed and the refetched result already says so.
                raise exc
            raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: Fence/carrier pair is mixed")
        if values["Target-Carrier-Kind"] == "github_pr":
            observed = provider.inspect_pull_request(url)
            if observed.get("headRefOid") != carrier_candidate or str(observed.get("state", "")).upper() != "OPEN":
                raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: provider carrier did not follow reauthorization")
        return {"ok": True, "mode": mode, "attempt": new_attempt,
                "target": target, "fence": fence_candidate,
                "carrier": carrier_candidate, "receipt": new_receipt}


def apply_target_update(project_root: Path, mode: str = "source_handoff",
                        remote: str = "origin") -> dict:
    """Execute the one authorized direct/provider target mutation.

    The Fence intent and its target receipt are the only authorization. A
    direct carrier is handled with an exact fast-forward lease; GitHub PR
    carriers use the provider adapter and merge-commit-only postcondition.
    """
    root = main_worktree(project_root.resolve())
    _ref, _fence_oid, values = _fence_context(root, remote)
    if values["Mode"] != mode or values["Target-Update-Intent"] == "none":
        raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: no matching target intent")
    carrier = values["Target-Carrier-Kind"]
    attempt = values["Target-Update-Attempt"]
    if carrier == "none" or attempt == "none":
        raise RuntimeError("DELIVERY_TARGET_CARRIER_INVALID: target carrier is incomplete")
    receipt_path, _lock = target_receipt_paths(root, mode)
    if not receipt_path.exists():
        raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: target receipt is missing")
    receipt = _validate_target_receipt(json.loads(receipt_path.read_text(encoding="utf-8")))
    if receipt["state"] == "verified":
        return {"ok": True, "mode": mode, "state": "verified", "target": values["Target"]}
    target_branch, target_before = resolve_target(root, remote, recorded=open_target_branch(root, remote))
    base = values["Target-Carrier-Base"]
    if target_before != base:
        # A direct push can be accepted by the remote while its response is
        # lost locally. The authorized candidate is an exact target tip in
        # that case, so reconstruct the durable local receipt instead of
        # treating the normal response-loss path as target drift.
        if (carrier == "direct_target" and
                target_before == values["Target-Carrier-Head"]):
            verified = mark_target_verified(root, mode, attempt)
            return {"ok": True, "mode": mode, "carrier": carrier,
                    "target": f"refs/heads/{target_branch}",
                    "target_oid": target_before, "receipt": verified,
                    "recovered": True}
        # A person may merge the authorized draft PR by hand. When the target
        # holds exactly the merge commit this call would have produced (first
        # parent the authorized base, second the authorized head), the update
        # happened as authorized and only its local receipt is missing.
        if carrier == "github_pr":
            from delivery_provider import GitHubProvider
            repository = values["Target-Repository"].removeprefix("github:")
            number = values["Target-Carrier-Object"].removeprefix("pr:")
            url = f"https://github.com/{repository}/pull/{number}"
            observed = GitHubProvider(root, remote).inspect_pull_request(url)
            merge_oid = str((observed.get("mergeCommit") or {}).get("oid") or "")
            if (str(observed.get("state", "")).upper() == "MERGED" and merge_oid
                    and observed.get("headRefOid") == values["Target-Carrier-Head"]
                    and is_ancestor(root, merge_oid, target_before)):
                commit = run_git(root, "cat-file", "-p", merge_oid)
                parents = [line.split(" ", 1)[1] for line in commit.splitlines()
                           if line.startswith("parent ") and " " in line]
                if parents == [base, values["Target-Carrier-Head"]]:
                    verified = mark_target_verified(root, mode, attempt)
                    return {"ok": True, "mode": mode, "carrier": carrier,
                            "pull_request_url": url, "merge_commit": merge_oid,
                            "target": target_before, "receipt": verified,
                            "recovered": True}
        # No external mutation was elected yet: keep the exact prepared
        # receipt so a fresh target-carrier attempt can be based safely.
        if receipt["state"] == "prepared":
            raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: target moved before authorized mutation; reauthorize target update")
        raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: target moved after target mutation election")
    if receipt["state"] != "call_started":
        receipt = mark_target_call_started(root, mode, attempt)
    if carrier == "direct_target":
        target_ref = f"refs/heads/{target_branch}"
        head = values["Target-Carrier-Head"]
        try:
            atomic_push(root, remote, [(target_ref, base, head)])
        except RuntimeError as exc:
            if not direct_update_took_no_effect(root, remote, head):
                raise
            release_target_call(root, mode, attempt)
            raise RuntimeError(f"{exc}; the target does not contain the update, so its call was released "
                               "for a fresh attempt or an abort") from exc
        verified = mark_target_verified(root, mode, attempt)
        return {"ok": True, "mode": mode, "carrier": carrier,
                "target": target_ref, "target_oid": head, "receipt": verified}
    if carrier == "github_pr":
        from delivery_provider import GitHubProvider
        repository = values["Target-Repository"].removeprefix("github:")
        provider = GitHubProvider(root, remote)
        number = values["Target-Carrier-Object"].removeprefix("pr:")
        url = f"https://github.com/{repository}/pull/{number}"
        provider.ensure_draft(url)
        provider.make_ready(url)
        merged = provider.merge_commit(url, values["Target-Carrier-Head"])
        merge_oid = merged["merge_commit"]
        target_ref = f"refs/heads/{target_branch}"
        fetched_target_ref = f"refs/remotes/{remote}/{target_branch}"
        run_git(root, "fetch", "--no-tags", remote,
                f"{target_ref}:{fetched_target_ref}")
        target_after = remote_oid(root, remote, target_ref)
        try:
            run_git(root, "merge-base", "--is-ancestor", merge_oid, target_after)
            commit = run_git(root, "cat-file", "-p", merge_oid)
        except RuntimeError as exc:
            raise RuntimeError("DELIVERY_TARGET_CONVERGENCE_REQUIRED: provider merge is not in target ancestry") from exc
        parents = [line.split(" ", 1)[1] for line in commit.splitlines()
                   if line.startswith("parent ") and " " in line]
        if len(parents) != 2 or parents[1] != values["Target-Carrier-Head"]:
            raise RuntimeError("DELIVERY_TARGET_CARRIER_INVALID: provider result is not an exact merge commit")
        verified = mark_target_verified(root, mode, attempt)
        return {"ok": True, "mode": mode, "carrier": carrier,
                "pull_request_url": url, "merge_commit": merge_oid,
                "target": target_after, "receipt": verified}
    raise RuntimeError("DELIVERY_TARGET_CARRIER_INVALID: unsupported carrier kind")


def claim_items(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    from delivery_compile import delivery_findings, docs_root, split_note, frontmatter, content_hash
    docs = docs_root(root)
    directory, findings = delivery_findings(docs, delivery_id)
    if directory is None or findings:
        raise RuntimeError("Delivery package is not portable: " + "; ".join(findings))
    delivery_props, _ = split_note(directory / "delivery.md")
    if delivery_props.get("status") != "execution_approved":
        raise RuntimeError("claim-items requires an execution-approved Delivery")
    # A claim starts no work: start-item, resume-item and reopen-item hold an
    # Item a pending User Decisions row blocks.
    refs = canonical_refs(delivery_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    fence_message = commit_message(root, fence_oid)
    require_fence_record(fence_message)
    if trailer(fence_message, "Mode") != "open":
        raise RuntimeError("DELIVERY_FENCE_MODE: claim-items requires an open Fence")
    refuse_cancelled_delivery(root, directory, integration_oid, "claim-items")
    require_target_ancestry(root, remote, fence_message, integration_oid, delivery_id=delivery_id)
    marker = commit_tree(root, integration_oid, [], f"Establish claims for {delivery_id}",
                         {"Record": "claims-established-v1", "Protocol": "1", "Delivery": delivery_id,
                          "Scope-Hash": str(delivery_props.get("scope_hash", "none")),
                          "Plan-Hash": str(delivery_props.get("plan_hash", "none"))})
    updates = [(refs["fence"], fence_oid, commit_tree(
        root, fence_oid, [], f"Establish claims for {delivery_id}",
        {"Record": "project-fence-v2", "Protocol": "2", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": trailer(fence_message, "Target") or "none",
         "Governance-Hash": trailer(fence_message, "Governance-Hash") or "none",
         **carried_fence_barrier(fence_message)})),
               (refs["integration"], integration_oid, marker)]
    item_paths = sorted(directory.glob("items/*/item.md"))
    # The Integration holds the current target, so it holds every merged Delivery's package.
    delivered = merged_story_owners(root, integration_oid, [path.parent.name.upper() for path in item_paths])
    stories = []
    for item_path in item_paths:
        story = item_path.parent.name.upper()
        item_ref = canonical_refs(delivery_id, story)["item"]
        claimed, holder = item_claim(root, remote, delivery_id, story)
        require_claimable(story, claimed, holder, delivered)
        item_props, item_body = split_note(item_path)
        item_props["integration_base_commit"] = marker
        item_props["source_hash"] = content_hash(item_props, item_body)
        relative = rel_posix(root, item_path)
        item_commit = commit_replacements(root, marker, {relative: frontmatter(item_props, item_body)},
                                          f"Claim {story} for {delivery_id}",
                                          {"Record": "item-claim-v1", "Protocol": "1", "Delivery": delivery_id,
                                           "Story": story, "Scope-Hash": str(delivery_props.get("scope_hash", "none")),
                                           "Plan-Hash": str(delivery_props.get("plan_hash", "none"))})
        updates.append((item_ref, "", item_commit))
        stories.append(story)
    atomic_push(root, remote, updates)
    return {"ok": True, "delivery": delivery_id, "claims": stories,
            "integration": marker, "refs": short_refs(delivery_id)}


def require_claimable(story: str, claimed: str, holder: str, delivered: dict[str, str]) -> None:
    """Refuse to claim *story* while an Item ref holds it or a merged Delivery in *delivered* delivered it."""
    if claimed:
        raise RuntimeError(f"DELIVERY_CLAIM_CONFLICT: story is already claimed by {holder or 'another Delivery'}: {story}")
    if story in delivered:
        raise RuntimeError(f"DELIVERY_CLAIM_CONFLICT: story is already delivered by {delivered[story]}: {story}")


def remote_slot_oids(root: Path, remote: str) -> dict[str, str]:
    output = run_git(root, "ls-remote", remote, "refs/heads/agentrof/slots/*")
    result = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 2:
            result[parts[1].removeprefix("refs/heads/agentrof/slots/")] = parts[0]
    return result


def governed_governance_hash(root: Path) -> str:
    """Return the current approved Governance hash, or none before activation."""
    value, errors = delivery_governance.status(root / "workspace" / "docs")
    if errors:
        if any(error.startswith("missing delivery governance:") for error in errors):
            return "none"
        raise RuntimeError("Delivery Governance is invalid: " + "; ".join(errors))
    if not value.get("current"):
        raise RuntimeError("Delivery Governance must be approved/current")
    return str(value["governance_hash"])


def project_max_parallel(root: Path, expected_hash: str | None = None) -> int:
    receipt, errors = delivery_governance.status(root / "workspace" / "docs")
    if errors or not receipt.get("current"):
        raise RuntimeError("approved/current Delivery Governance is required before Item activation")
    value = receipt.get("max_parallel")
    observed_hash = governed_governance_hash(root)
    if expected_hash is not None and expected_hash != observed_hash:
        raise RuntimeError("DELIVERY_FENCE_GOVERNANCE: the Fence does not carry the approved Governance; "
                           "apply it with apply-governance before Item activation")
    return value


def require_item_operation_bindings(root: Path, item_props: dict) -> None:
    """Fail closed when executable work lost its approved Operation receipt."""
    from delivery_compile import item_operation_findings
    findings = item_operation_findings(root / "workspace" / "docs", item_props)
    if findings:
        raise RuntimeError("DELIVERY_PLAN_STALE: Item Operation Contract bindings are invalid: " + "; ".join(findings))


def require_item_architecture_binding(worktree: Path, item_props: dict,
                                      story_id: str, *, tree: str | None = None) -> None:
    """Require a compiler-stamped Architecture delta only when planned."""
    expected = item_architecture_delta_hash(item_props)
    if expected is None:
        return
    try:
        # Read committed blobs: a local edit must never authorize the reviewed tip.
        tree = tree or run_git(worktree, "--no-replace-objects", "rev-parse", "HEAD")
        prefix = "workspace/docs/system-architecture/"
        listing = git_paths(worktree, "--no-replace-objects", "ls-tree", "-rz", tree, "--", prefix)
        with tempfile.TemporaryDirectory(prefix="agentrof-item-architecture-") as temporary:
            architecture = Path(temporary)
            for entry in listing:
                metadata, path = entry.split("\t", 1)
                mode, kind, oid = metadata.split()
                if kind != "blob" or mode not in {"100644", "100755"}:
                    raise RuntimeError("Item Architecture files must be regular")
                target = architecture / path.removeprefix(prefix)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(subprocess.run(
                    ["git", "--no-replace-objects", "cat-file", "blob", oid], cwd=worktree,
                    capture_output=True, check=True).stdout)
            require_architecture_delta(architecture, item_props, story_id, expected)
    except (ImportError, OSError, ValueError) as exc:
        raise RuntimeError("Item Architecture binding is invalid: " + str(exc)) from exc


def item_architecture_delta_hash(item_props: dict) -> str | None:
    """The Architecture delta hash an Item record must carry, or None when it plans no Architecture."""
    impact = str(item_props.get("architecture_impact", "not_applicable"))
    if impact == "not_applicable":
        return None
    if impact != "required":
        raise RuntimeError("Item architecture_impact is invalid")
    expected = str(item_props.get("architecture_delta_hash", ""))
    if not expected.startswith("sha256:"):
        raise RuntimeError("architecture-impact Item lacks architecture_delta_hash")
    return expected


def require_architecture_delta(architecture: Path, item_props: dict, story_id: str, expected: str) -> None:
    """Require that the Architecture tree holds the sealed delta *expected* names, within the Item's claims."""
    try:
        import architecture_compile
        delta = architecture_compile.current_item_delta(architecture, story_id)
        if expected != delta.get("architecture_delta_hash"):
            raise RuntimeError("architecture_delta_hash is stale")
        _registry, findings = architecture_compile.registry(architecture)
        if findings:
            raise RuntimeError("Item Architecture sealed records are invalid: " + "; ".join(findings))
        components = set(item_props.get("architecture_components", []))
        kinds = set(item_props.get("architecture_record_kinds", []))
        if not delta.get("records") or not components or not kinds:
            raise RuntimeError("Item Architecture requires a nonempty claimed delta")
        for row in delta["records"]:
            props = architecture_compile.record_props(architecture / row["path"])
            affected = {scope.split("#module/", 1)[0] for scope in props.get("affected_scopes", [])}
            if (props.get("revision_state") != "sealed"
                    or row["type"] not in kinds
                    or (row["component_ref"] and row["component_ref"] not in components)
                    or not set(row["connects"]).issubset(components)
                    or not affected.issubset(components)):
                raise RuntimeError("Item Architecture delta is unsealed or exceeds approved claims")
    except (ImportError, OSError, ValueError) as exc:
        raise RuntimeError("Item Architecture binding is invalid: " + str(exc)) from exc


def item_control_note(root: Path, tree: str, relative_item: str) -> tuple[str, str, dict, str]:
    """The Item control file in one tree: its mode, text, frontmatter and body."""
    entry = run_git(root, "--no-replace-objects", "ls-tree", tree, "--", relative_item)
    if not entry:
        raise RuntimeError("product/test commits may not add or remove the active Item")
    metadata, _path = entry.split("\t", 1)
    mode, kind, oid = metadata.split()
    if kind != "blob" or mode not in {"100644", "100755"}:
        raise RuntimeError("Item publication requires a regular control file")
    text = subprocess.run(["git", "--no-replace-objects", "cat-file", "blob", oid], cwd=root,
                          capture_output=True, check=True).stdout.decode("utf-8")
    return parse_item_control(mode, text)


def parse_item_control(mode: str, text: str) -> tuple[str, str, dict, str]:
    """An Item control file's mode and committed text with its frontmatter and body."""
    from delivery_compile import parse_frontmatter
    props, body_line, error = parse_frontmatter(text)
    if error:
        raise RuntimeError("Item publication frontmatter is invalid: " + error)
    body = "\n".join(text.splitlines()[body_line - 1:]).lstrip("\n")
    return mode, text, props, body


def converged_integration(root: Path, before_props: dict, after_props: dict, after: str,
                          integration: str | None) -> str | None:
    """The newer Integration commit an Item has taken, or None while its base stands.

    A target refresh re-issues an untouched claim on the new Integration and
    leaves an Item that already carries work to its writer. That writer takes a
    commit of the Integration's own line into the Item and records it as the
    Item's integration base, which only ever moves forward along that line.
    """
    previous = before_props.get("integration_base_commit")
    taken = after_props.get("integration_base_commit")
    if taken == previous:
        return None
    try:
        converged = bool(
            all(isinstance(value, str) and OID_RE.fullmatch(value) for value in (previous, taken, integration))
            and taken in run_git(root, "--no-replace-objects", "rev-list", "--first-parent",
                                 f"{previous}..{integration}").split()
            and is_ancestor(root, previous, taken) and is_ancestor(root, taken, after))
    except (RuntimeError, subprocess.CalledProcessError):
        converged = False
    if not converged:
        raise RuntimeError("an Item may move its integration base only forward to an Integration commit it has taken")
    return taken


def plan_owned_item_fields(props: dict) -> dict:
    """What the published plan owns in an Item record: all but the writer's lifecycle, base and stamp."""
    owned = {key: value for key, value in props.items() if key not in {*ITEM_WRITER_FIELDS, "source_hash"}}
    owned["tags"] = [tag for tag in props.get("tags") or [] if not str(tag).startswith("status/")]
    return owned


def item_lifecycle(props: dict) -> tuple:
    return props.get("status"), [tag for tag in props.get("tags") or [] if str(tag).startswith("status/")]


def require_item_publication_controls(root: Path, before: str, after: str,
                                      relative_delivery: str, relative_item: str,
                                      integration: str | None = None) -> None:
    """Permit only the current Item's Architecture stamp inside Delivery controls,
    and what an Item carries once its writer converges it on a newer Integration.

    Converging brings the Delivery controls of the Integration commit the Item
    took, byte for byte. The Item record then holds that commit's plan-owned
    fields while it keeps its own lifecycle, its stamp and its new base.
    """
    prefix = relative_delivery.rstrip("/") + "/"
    changed = git_paths(root, "--no-replace-objects", "diff", "--name-only", "-z", before, after, "--", prefix)
    notes = {}
    converged = None
    if relative_item in changed:
        notes = {tree: item_control_note(root, tree, relative_item) for tree in (before, after)}
        converged = converged_integration(root, notes[before][2], notes[after][2], after, integration)
    require_carried_control_paths(changed, relative_item, lambda path: converged is not None and (
        run_git(root, "--no-replace-objects", "ls-tree", after, "--", path)
        == run_git(root, "--no-replace-objects", "ls-tree", converged, "--", path)))
    if relative_item not in changed:
        return
    published = None if converged is None else item_control_note(root, converged, relative_item)
    require_item_controls(notes[before], notes[after], published)


def require_carried_control_paths(changed: list[str], relative_item: str, carried) -> None:
    """Refuse a changed Delivery control path other than the Item record that *carried* does not vouch for."""
    for path in changed:
        if path != relative_item and not carried(path):
            raise RuntimeError("product/test commits may not edit Delivery control files")


def require_item_controls(before: tuple[str, str, dict, str], after: tuple[str, str, dict, str],
                          published: tuple[str, str, dict, str] | None = None) -> None:
    """Permit an Item record change only as its Architecture stamp, or as the record of the
    converged Integration commit whose control note is *published*, keeping its lifecycle and stamp."""
    from delivery_compile import content_hash
    if published is None:
        versions = []
        for index, (mode, text, props, body) in enumerate((before, after)):
            if props.get("architecture_impact") != "required":
                raise RuntimeError("only a required Architecture Item may publish its stamp")
            if index and props.get("source_hash") != content_hash(props, body):
                raise RuntimeError("Item Architecture stamp source_hash is stale")
            # The closing delimiter line may end with CRLF, as a text-mode write on native
            # Windows commits it under setup's -text rule; the bytes around it compare exactly.
            parts = re.split(r"\n---\r?\n", text, maxsplit=1)
            if len(parts) != 2:
                raise RuntimeError("Item publication frontmatter is invalid: no closing delimiter line")
            header, body_text = parts
            header = re.sub(r"(?m)^(?:architecture_delta_hash|source_hash):[^\n]*\n?", "", header)
            versions.append((mode, header.rstrip("\n"), body_text))
        if versions[0] != versions[1]:
            raise RuntimeError("product/test commits changed authored Item controls beyond its Architecture stamp")
        return
    previous_mode, _previous_text, previous, _previous_body = before
    mode, _text, props, body = after
    _published_mode, _published_text, published, published_body = published
    if props.get("source_hash") != content_hash(props, body):
        raise RuntimeError("Item Architecture stamp source_hash is stale")
    if (props.get("architecture_delta_hash") != previous.get("architecture_delta_hash")
            and props.get("architecture_impact") != "required"):
        raise RuntimeError("only a required Architecture Item may publish its stamp")
    if (mode != previous_mode or body != published_body
            or plan_owned_item_fields(props) != plan_owned_item_fields(published)
            or item_lifecycle(props) != item_lifecycle(previous)):
        raise RuntimeError("product/test commits changed authored Item controls beyond its Architecture stamp "
                           "and its converged Integration")


def require_item_path_claims(root: Path, before: str, after: str, relative_item: str,
                             provisional=None) -> None:
    """Refuse a committed product or test path outside the Item's path claims.

    A claim covers its exact path and every path below it. Vault paths keep their
    own rules instead: the Delivery controls, the claimed Architecture delta and
    the compiler projections. A path the product tip holds exactly as the Item's
    recorded integration base holds it is not the Item's change; its writer took
    it with that Integration. *provisional* names the refusal of paths a
    provisional claim covers, as require_paths_within_claims takes it.
    """
    props = item_control_note(root, after, relative_item)[2]

    def product_paths(start: str) -> set[str]:
        listing = git_paths(root, "--no-replace-objects", "diff", "--no-renames", "--name-only", "-z", start, after,
                            failure="cannot list the Item's committed paths")
        return {path for path in listing if not path.startswith("workspace/docs/")}

    changed = product_paths(before)
    base = props.get("integration_base_commit")
    if changed and isinstance(base, str) and OID_RE.fullmatch(base):
        changed &= product_paths(base)
    require_paths_within_claims(changed, props.get("path_claims"), provisional)


def paths_outside_claims(changed, path_claims) -> list[str]:
    """The changed paths that no normalized path claim covers, by its exact path or a parent."""
    from delivery_compile import _is_normalized_claim
    claims = [claim for claim in path_claims or []
              if isinstance(claim, str) and _is_normalized_claim(claim)]
    return sorted(path for path in changed if not any(claim_covers(claim, path) for claim in claims))


def claim_covers(claim: str, path: str) -> bool:
    return path == claim or path.startswith(claim + "/")


def require_paths_within_claims(changed: set[str], path_claims, provisional=None) -> None:
    """Refuse a changed product path that no normalized path claim covers, by its exact path or a parent.

    *provisional*, given the paths outside, returns the refusal of those a
    provisional claim of the Item covers, which names why they are not yet,
    or no longer, the Item's to publish, or None.
    """
    outside = paths_outside_claims(changed, path_claims)
    if outside:
        refusal = provisional(outside) if provisional is not None else None
        if refusal:
            raise RuntimeError(refusal)
        raise RuntimeError("DELIVERY_PATH_CLAIM_EXCEEDED: the Item's product change lies outside its path claims: "
                           + ", ".join(outside))


PROVISIONAL_SWITCH = "provisional_claims"
PROVISIONAL_VALUE = "during_plan_revision"
PROVISIONAL_RECORD = "provisional-claim-v1"
PROVISIONAL_RELEASE = "provisional-claim-release-v1"
PROVISIONAL_DISPOSITIONS = ("promoted", "orphaned", "withdrawn")
# The roots no implementation write scope reaches, as task_inputs.py excludes them.
PROVISIONAL_EXCLUDED_ROOTS = ("workspace/docs", ".git", ".agentrof")
# The Integration records that elect an Item's writer.
WRITER_ELECTION_RECORDS = ("item-start-authorized-v1", "item-takeover-v1", "item-reopen-authorized-v1")


def require_commit(root: Path, remote: str, ref: str, oid: str) -> str:
    """Return *oid*, fetching *ref* first when this checkout lacks the commit another host wrote."""
    if subprocess.run(["git", "cat-file", "-e", oid + "^{commit}"], cwd=root,
                      capture_output=True, check=False).returncode:
        run_git(root, "fetch", "--no-tags", remote, ref)
    return oid


def delivery_integration_commit(root: Path, oid: str, delivery_id: str) -> bool:
    """Whether *oid* is a record commit of *delivery_id*'s Integration line, as far as this checkout can tell.

    Every commit of that first-parent line carries its record kind and Delivery
    trailers, and the line starts at the Delivery's reservation. Without the
    remote nothing proves that the remote's line still holds the commit.
    """
    try:
        message = commit_message(root, oid + "^{commit}")
        if trailer(message, "Record") is None or trailer(message, "Delivery") != delivery_id:
            return False
        reservations = run_git(root, "log", "--first-parent", "--format=%H", "--fixed-strings",
                               "--grep=Agentrof-Record: delivery-reservation-v1", oid).split()
    except RuntimeError:
        return False
    return any(trailer(commit_message(root, found), "Delivery") == delivery_id for found in reservations)


def held_plan_revision_epoch(root: Path, remote: str, delivery_id: str,
                             tips: dict[str, str] | None = None) -> str | None:
    """The epoch of the plan-revision barrier the remote Fence holds for *delivery_id*, or None.

    The Fence names no Delivery: the barrier's own Integration record binds its
    epoch to the Delivery that began it.
    """
    refs = canonical_refs(delivery_id)
    tips = tips if tips is not None else remote_ref_oids(root, remote, [refs["fence"], refs["integration"]])
    fence, integration = tips.get(refs["fence"]), tips.get(refs["integration"])
    if not fence or not integration:
        return None
    message = commit_message(root, require_commit(root, remote, refs["fence"], fence))
    epoch = trailer(message, "Barrier-Epoch")
    if trailer(message, "Barrier-Kind") != "plan-revision" or epoch in {None, "none"}:
        return None
    begun = run_git(root, "log", "--format=%H", "--fixed-strings", f"--grep=Agentrof-Barrier-Epoch: {epoch}",
                    require_commit(root, remote, refs["integration"], integration))
    for oid in begun.split():
        record = commit_message(root, oid)
        if all(trailer(record, key) == expected for key, expected in (
                ("Record", "delivery-barrier-v1"), ("Delivery", delivery_id),
                ("Barrier-Kind", "plan-revision"), ("Barrier-Epoch", epoch))):
            return epoch
    return None


def provisional_paths_hash(paths: list[str]) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(sorted(paths)).encode("utf-8")).hexdigest()


def provisional_path_problem(path) -> str | None:
    """Why *path* cannot be provisionally claimed, or None.

    The excluded roots are compared with their case folded, since a file
    system that folds case writes Workspace/docs into workspace/docs.
    """
    from delivery_compile import _is_normalized_claim
    if not isinstance(path, str) or not _is_normalized_claim(path):
        return f"{path!r} is not a normalized repository path"
    if any(claim_covers(excluded, path.casefold()) for excluded in PROVISIONAL_EXCLUDED_ROOTS):
        return f"{path} lies under {', '.join(PROVISIONAL_EXCLUDED_ROOTS)}, which no implementation write scope reaches"
    return None


def provisional_claims(root: Path, tip: str, delivery_id: str) -> list[dict]:
    """Every provisional claim *delivery_id* recorded on the Integration line up to *tip*, oldest first.

    A release record ends the one claim record its Claim-Record names, so each
    claim carries its disposition and release record or None, and a claim that was void when its
    Story's other claim was released stays unreleased. Every record is checked
    again as the verb wrote it, so a hand-pushed record grants nothing.
    """
    oids = run_git(root, "log", "--first-parent", "--reverse", "--format=%H", "--fixed-strings",
                   "--grep=Agentrof-Record: provisional-claim", tip).split()
    claims: list[dict] = []

    def corrupt(oid: str, cause: str) -> RuntimeError:
        return RuntimeError(f"DELIVERY_COORDINATION_CORRUPT: provisional claim record {oid} {cause}")

    for oid in oids:
        message = commit_message(root, oid)
        record, story, epoch = (trailer(message, key) for key in ("Record", "Story", "Barrier-Epoch"))
        if trailer(message, "Delivery") != delivery_id or record not in {PROVISIONAL_RECORD, PROVISIONAL_RELEASE}:
            continue
        if trailer(message, "Protocol") != "1":
            raise corrupt(oid, "does not carry Protocol 1")
        trees = run_git(root, "rev-parse", f"{oid}^{{tree}}", f"{oid}^1^{{tree}}").split()
        if len(trees) != 2 or trees[0] != trees[1]:
            raise corrupt(oid, "changes the Integration tree")
        if record == PROVISIONAL_RELEASE:
            released = trailer(message, "Claim-Record")
            disposition = trailer(message, "Disposition")
            if disposition not in PROVISIONAL_DISPOSITIONS:
                raise corrupt(oid, f"records disposition {disposition!r}, not one of "
                                   + ", ".join(PROVISIONAL_DISPOSITIONS))
            claim = next((claim for claim in claims if claim["record"] == released), None)
            if claim is None or claim["disposition"] is not None or (claim["story"], claim["epoch"]) != (story, epoch):
                raise corrupt(oid, f"releases {released}, which is no unreleased claim of {story} in its epoch")
            claim["disposition"], claim["released_by"] = disposition, oid
            continue
        listed = next((line for line in message.splitlines() if line.startswith("[")), "")
        try:
            paths = json.loads(listed)
        except json.JSONDecodeError:
            paths = None
        if (not isinstance(paths, list) or not all(isinstance(path, str) for path in paths)
                or provisional_paths_hash(paths) != trailer(message, "Claims-Hash")):
            raise corrupt(oid, "does not hold the path list its Claims-Hash binds")
        problems = [problem for problem in map(provisional_path_problem, paths) if problem]
        if not paths or problems or len(set(paths)) != len(paths):
            raise corrupt(oid, "claims paths no provisional claim may hold: "
                               + ("; ".join(problems) or "an empty or repeated path"))
        claims.append({"record": oid, "story": story, "epoch": epoch, "paths": sorted(paths),
                       "item_tip": trailer(message, "Item-Tip"), "slot": trailer(message, "Slot"),
                       "writer_epoch": trailer(message, "Writer-Epoch"), "disposition": None,
                       "released_by": None})
    return claims


def current_writer_epoch(root: Path, integration: str, delivery_id: str, story: str) -> str | None:
    """The Writer-Epoch of the newest Integration record that elected *story*'s writer, or None."""
    oids = run_git(root, "log", "--first-parent", "--format=%H", "--fixed-strings",
                   f"--grep=Agentrof-Story: {story}", integration).split()
    for oid in oids:
        message = commit_message(root, oid)
        if (trailer(message, "Delivery") == delivery_id and trailer(message, "Story") == story
                and trailer(message, "Record") in WRITER_ELECTION_RECORDS):
            return trailer(message, "Writer-Epoch")
    return None


def provisional_claim_states(root: Path, remote: str, delivery_id: str, integration: str,
                             claims: list[dict], held_epoch: str | None) -> list[dict]:
    """Each claim with its state: its release disposition, or live while the barrier it was recorded
    under is still held by this Delivery and the Item keeps the tip lineage, Slot and writer it
    was recorded with, else void. Validity is derived here and never stored."""
    unreleased = sorted({claim["story"] for claim in claims if claim["disposition"] is None})
    tips = remote_ref_oids(root, remote, [canonical_refs(delivery_id, story)["item"] for story in unreleased]) \
        if unreleased else {}
    slots = remote_slot_oids(root, remote) if unreleased else {}
    writers = {story: current_writer_epoch(root, integration, delivery_id, story) for story in unreleased}
    states = []
    for claim in claims:
        state, reason = claim["disposition"], None
        if state is None:
            item_ref = canonical_refs(delivery_id, claim["story"])["item"]
            tip = tips.get(item_ref, "")
            if claim["epoch"] != held_epoch:
                reason = "its plan-revision barrier is no longer held"
            elif not tip or not descends(root, claim["item_tip"], require_commit(root, remote, item_ref, tip)):
                reason = "the Item ref left the tip it was recorded on"
            elif slots.get(claim["slot"]) != tip:
                reason = "the Item no longer holds the Slot it was recorded with"
            elif writers.get(claim["story"]) != claim["writer_epoch"]:
                reason = "another writer took the Item over"
            state = "void" if reason else "live"
        states.append({**claim, "state": state, **({"reason": reason} if reason else {})})
    return states


def provisional_target_holds(root: Path, claims: list[dict], item_records: dict[str, dict]) -> set[str]:
    """The paths provisional claims reserve against a target refresh, beside the Item refs' own claims.

    Only provisional_claims during_plan_revision records a provisional claim. A
    live one reserves its paths as a published claim does. A promoted one keeps
    the paths its Item ref does not claim yet only while that Item, by its
    *item_records* entry, neither ended nor converged on an Integration commit
    at or after the claim's release; from then on the Item ref's own claims
    govern, also when a later revision dropped the path.
    """
    from delivery_compile import TERMINAL_ITEM_STATUSES
    reserved: set[str] = set()
    for provisional in claims:
        if provisional["state"] == "live":
            reserved.update(provisional["paths"])
            continue
        record = item_records.get(provisional["story"], {})
        if provisional["state"] != "promoted" or record.get("status") in TERMINAL_ITEM_STATUSES:
            continue
        base = record.get("integration_base_commit")
        if isinstance(base, str) and OID_RE.fullmatch(base) and descends(root, provisional["released_by"], base):
            continue
        own = [claim for claim in record.get("path_claims") or [] if isinstance(claim, str)]
        reserved.update(path for path in provisional["paths"]
                        if not any(claim_covers(claim, path) for claim in own))
    return reserved


def descends(root: Path, ancestor: str, descendant: str) -> bool:
    """Whether *descendant* holds *ancestor* in its history; an object no fetched history holds does not."""
    try:
        return is_ancestor(root, ancestor, descendant)
    except RuntimeError:
        return False


def delivery_provisional_claims(root: Path, remote: str, delivery_id: str,
                                integration: str | None = None) -> list[dict]:
    """Every provisional claim of *delivery_id* with its derived state, from the remote Integration,
    or from the Integration tip *integration* a caller already read. A Delivery that recorded
    none reads nothing else."""
    refs = canonical_refs(delivery_id)
    if integration is None:
        integration = remote_ref_oids(root, remote, [refs["integration"]])[refs["integration"]]
        if not integration:
            return []
    claims = provisional_claims(root, require_commit(root, remote, refs["integration"], integration), delivery_id)
    if not claims:
        return []
    return provisional_claim_states(root, remote, delivery_id, integration, claims,
                                    held_plan_revision_epoch(root, remote, delivery_id))


def provisional_path_refusal(root: Path, remote: str, delivery_id: str, story: str,
                             outside: list[str]) -> str | None:
    """The refusal of product paths outside an Item's claims that one of its provisional claims covers.

    A live or promoted claim waits for the published plan the writer converges
    on; an orphaned, withdrawn or void one never becomes the Item's, so its
    writer reverts or reworks that change. Paths no provisional claim covers
    are left to the claim rule, which refuses them as exceeded.
    """
    claims = [claim for claim in delivery_provisional_claims(root, remote, delivery_id) if claim["story"] == story]
    covered: dict[str, str] = {}
    for path in outside:
        state = next((claim["state"] for claim in reversed(claims)
                      if any(claim_covers(claimed, path) for claimed in claim["paths"])), None)
        if state is not None:
            covered[path] = state
    ended = {path: state for path, state in covered.items() if state not in {"live", "promoted"}}
    if ended:
        return ("DELIVERY_PROVISIONAL_CLAIM_ORPHANED: the Item's product change writes paths whose provisional "
                "claim ended without a published claim: "
                + ", ".join(f"{path} ({state})" for path, state in sorted(ended.items()))
                + "; revert or rework that change in the Item worktree, or revise the plan to claim them")
    if covered:
        return ("DELIVERY_PROVISIONAL_CLAIM_PENDING: the Item's product change writes provisionally claimed "
                "paths that its published plan does not grant yet: " + ", ".join(sorted(covered))
                + "; approve and publish the revised execution plan and converge the Item on that "
                "Integration before freeze or push-item")
    return None


def require_provisional_switch(docs: Path, delivery_id: str) -> None:
    from delivery_compile import delivery_switch_value
    try:
        value = delivery_switch_value(docs, delivery_id, PROVISIONAL_SWITCH)
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"DELIVERY_PROVISIONAL_CLAIM_REFUSED: the Delivery's {PROVISIONAL_SWITCH} value "
                           f"cannot be read: {exc}") from exc
    if value != PROVISIONAL_VALUE:
        raise RuntimeError(f"DELIVERY_PROVISIONAL_CLAIM_REFUSED: {delivery_id} runs switch {PROVISIONAL_SWITCH} "
                           f"at {value}; only {PROVISIONAL_VALUE} records provisional claims")


def provisional_overlap(first: str, second: str) -> bool:
    """Whether two claims overlap as written or on a file system that folds case."""
    from delivery_compile import _claims_overlap
    return _claims_overlap(first, second) or _claims_overlap(first.casefold(), second.casefold())


def provisional_claim(project_root: Path, delivery_id: str, story_id: str, paths: list[str],
                      remote: str = "origin") -> dict:
    """Record, on the Integration line, a provisional claim of paths the plan revision adds to an active Item.

    The claim lets the Item's writer commit the change in its worktree while the
    revision runs. Nothing freezes, reviews, pushes or integrates it before the
    approved plan publishes the paths, and its validity is derived from the
    barrier, the Item and its writer at every read.
    """
    validate_delivery_id(delivery_id)
    validate_story_id(story_id)
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    from delivery_compile import (TERMINAL_ITEM_STATUSES, docs_root, find_delivery, implementation_schedule,
                                  split_note)

    def refused(cause: str) -> RuntimeError:
        return RuntimeError(f"DELIVERY_PROVISIONAL_CLAIM_REFUSED: {cause}")

    docs = docs_root(root)
    directory = find_delivery(docs, delivery_id)
    if directory is None:
        raise RuntimeError("Delivery package not found")
    require_provisional_switch(docs, delivery_id)
    refs = canonical_refs(delivery_id, story_id)
    tips = remote_ref_oids(root, remote, [refs["fence"], refs["integration"], refs["item"]])
    epoch = held_plan_revision_epoch(root, remote, delivery_id, tips)
    if epoch is None:
        raise refused(f"the Fence holds no plan-revision barrier that {delivery_id} began; "
                      "run begin-plan-revision first")
    integration_oid = require_commit(root, remote, refs["integration"], tips[refs["integration"]])
    item_oid = tips[refs["item"]]
    if not item_oid or trailer(commit_message(root, require_commit(root, remote, refs["item"], item_oid)),
                               "Delivery") != delivery_id:
        raise refused(f"{story_id} has no Item ref of {delivery_id}")
    relative_item = rel_posix(root, directory / "items" / story_key(story_id) / "item.md")
    live, _body = split_remote_note(root, item_oid, relative_item, split_note)
    if live.get("status") != "active":
        raise refused(f"{story_id} is not active: its Item ref records {live.get('status')}")
    slot = next((key for key, oid in remote_slot_oids(root, remote).items() if oid == item_oid), None)
    if slot is None:
        raise refused(f"{story_id} holds no Slot at its Item tip")
    try:
        receipt = active_writer_receipt(root, delivery_id, story_id, item_oid, f"refs/heads/agentrof/slots/{slot}")
    except RuntimeError as exc:
        raise refused(f"this host holds no verified writer receipt of {story_id}: {exc}") from exc
    published, _body = split_remote_note(root, integration_oid, relative_item, split_note)
    try:
        schedule = implementation_schedule(published)
    except ValueError as exc:
        raise refused(f"{story_id} declares an unsupported implementation_schedule: {exc}") from exc
    if schedule == "parallel_lanes_v1":
        raise refused(f"{story_id} runs parallel lanes, whose lane scopes a provisional claim cannot extend; "
                      "wait for the approved plan")
    draft_path = directory / "items" / story_key(story_id) / "item.md"
    if not draft_path.is_file():
        raise refused(f"the checkout holds no draft Item record of {story_id}")
    draft = split_note(draft_path)[0].get("path_claims") or []
    requested = list(paths)
    if not requested:
        raise refused("name at least one path")
    if len(set(requested)) != len(requested):
        raise refused("a path is named twice")
    granted = [claim for claim in published.get("path_claims") or [] if isinstance(claim, str)]
    for path in requested:
        problem = provisional_path_problem(path)
        if problem:
            raise refused(problem)
        if path not in draft:
            raise refused(f"{path} is not in the checkout's draft path claims of {story_id}")
        if any(claim_covers(claim, path) for claim in granted):
            raise refused(f"{path} is already claimed by the published plan of {story_id}")
    for item_path in integration_item_paths(root, directory, integration_oid):
        other = item_path.parent.name.upper()
        if other == story_id.upper():
            continue
        props, _ = split_remote_note(root, integration_oid, rel_posix(root, item_path), split_note)
        if props.get("status") in TERMINAL_ITEM_STATUSES:
            continue
        for claim in props.get("path_claims") or []:
            if isinstance(claim, str) and any(provisional_overlap(path, claim) for path in requested):
                raise refused(f"the published plan gives {claim} to {props.get('story_id', other)}, which overlaps "
                              + ", ".join(requested))
    for item_path in sorted(directory.glob("items/*/item.md")):
        props = split_note(item_path)[0]
        if props.get("story_id") == story_id or props.get("status") in TERMINAL_ITEM_STATUSES:
            continue
        for claim in props.get("path_claims") or []:
            if isinstance(claim, str) and any(provisional_overlap(path, claim) for path in requested):
                raise refused(f"the draft plan gives {claim} to {props.get('story_id')}, which overlaps "
                              + ", ".join(requested))
    claims = provisional_claim_states(root, remote, delivery_id, integration_oid,
                                      provisional_claims(root, integration_oid, delivery_id), epoch)
    for claim in claims:
        if claim["state"] != "live":
            continue
        if claim["story"] == story_id:
            if claim["paths"] == sorted(requested):
                return {"ok": True, "delivery": delivery_id, "story": story_id, "paths": claim["paths"],
                        "barrier_epoch": epoch, "record": claim["record"], "integration": integration_oid,
                        "reused": True}
            raise refused(f"{story_id} already holds a live provisional claim of {', '.join(claim['paths'])}; "
                          "withdraw it, then claim the complete set")
        clash = sorted({claimed for claimed in claim["paths"]
                        if any(provisional_overlap(path, claimed) for path in requested)})
        if clash:
            raise refused(f"{claim['story']} holds a live provisional claim of {', '.join(clash)}, which overlaps "
                          + ", ".join(requested))
    listed = json.dumps(sorted(requested))
    candidate = commit_tree(
        root, integration_oid, [], f"Provisionally claim {story_id} for {delivery_id}",
        {"Record": "provisional-claim-v1", "Protocol": "1", "Delivery": delivery_id, "Story": story_id,
         "Barrier-Epoch": epoch, "Writer-Epoch": str(receipt["writer_epoch"]), "Slot": slot,
         "Item-Tip": item_oid, "Claims-Hash": provisional_paths_hash(requested)},
        body=listed)
    atomic_push(root, remote, [(refs["integration"], integration_oid, candidate)])
    return {"ok": True, "delivery": delivery_id, "story": story_id, "paths": sorted(requested),
            "barrier_epoch": epoch, "record": candidate, "integration": candidate}


def withdraw_provisional_claim(project_root: Path, delivery_id: str, story_id: str,
                               remote: str = "origin") -> dict:
    """Release an Item's live provisional claim before its plan revision ends."""
    validate_delivery_id(delivery_id)
    validate_story_id(story_id)
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    from delivery_compile import docs_root
    require_provisional_switch(docs_root(root), delivery_id)
    refs = canonical_refs(delivery_id)
    tips = remote_ref_oids(root, remote, [refs["fence"], refs["integration"]])
    epoch = held_plan_revision_epoch(root, remote, delivery_id, tips)
    if epoch is None:
        raise RuntimeError(f"DELIVERY_PROVISIONAL_CLAIM_REFUSED: the Fence holds no plan-revision barrier that "
                           f"{delivery_id} began")
    integration_oid = require_commit(root, remote, refs["integration"], tips[refs["integration"]])
    claims = provisional_claim_states(root, remote, delivery_id, integration_oid,
                                      provisional_claims(root, integration_oid, delivery_id), epoch)
    own = [claim for claim in claims if claim["story"] == story_id and claim["epoch"] == epoch]
    live = next((claim for claim in own if claim["state"] == "live"), None)
    if live is None:
        if own and own[-1]["state"] == "withdrawn":
            # A retry after a lost response finds the withdrawal that landed.
            return {"ok": True, "delivery": delivery_id, "story": story_id, "disposition": "withdrawn",
                    "barrier_epoch": epoch, "integration": integration_oid, "reused": True}
        raise RuntimeError(f"DELIVERY_PROVISIONAL_CLAIM_REFUSED: {story_id} holds no live provisional claim")
    candidate = commit_tree(
        root, integration_oid, [], f"Release provisional claim {story_id} for {delivery_id}",
        {"Record": "provisional-claim-release-v1", "Protocol": "1", "Delivery": delivery_id,
         "Story": story_id, "Barrier-Epoch": epoch, "Claim-Record": live["record"], "Disposition": "withdrawn"})
    atomic_push(root, remote, [(refs["integration"], integration_oid, candidate)])
    return {"ok": True, "delivery": delivery_id, "story": story_id, "disposition": "withdrawn",
            "barrier_epoch": epoch, "integration": candidate}


def provisional_dispositions(root: Path, remote: str, delivery_id: str, directory: Path,
                             integration: str, epoch: str, action: str) -> list[tuple[str, str, str]]:
    """The Story, disposition and claim record of each live provisional claim the end of barrier *epoch*
    releases; a void claim stays void.

    An abort withdraws every claim. A finish promotes a claim whose paths the
    published Item record now claims, and orphans one the approval dropped or
    moved.
    """
    claims = [claim for claim in provisional_claims(root, integration, delivery_id)
              if claim["epoch"] == epoch and claim["disposition"] is None]
    if not claims:
        return []
    from delivery_compile import split_note
    released = []
    for claim in provisional_claim_states(root, remote, delivery_id, integration, claims, epoch):
        if claim["state"] != "live":
            continue
        disposition = "withdrawn"
        if action == "finish":
            relative = rel_posix(root, directory / "items" / story_key(claim["story"]) / "item.md")
            try:
                published = split_remote_note(root, integration, relative, split_note)[0]
            except RuntimeError:
                # The approved plan dropped the Item, and with it every path of the claim.
                published = {}
            granted = [value for value in published.get("path_claims") or [] if isinstance(value, str)]
            covered = all(any(claim_covers(value, path) for value in granted) for path in claim["paths"])
            disposition = "promoted" if covered else "orphaned"
        released.append((claim["story"], disposition, claim["record"]))
    return released


def require_current_activation_target(root: Path, remote: str, delivery_id: str,
                                      story_id: str, target_before: str, slot: str,
                                      item_candidate: str, relative_item: str,
                                      item_props: dict, item_body: str) -> None:
    """Quiesce an exact granted Item/Slot pair if target moved during its CAS."""
    from delivery_compile import frontmatter, content_hash
    refs = canonical_refs(delivery_id, story_id, slot)
    slot_ref = refs["slot"]
    _target_branch, target_after = resolve_target(root, remote, recorded=recorded_target_branch(root, delivery_id))
    if target_after != target_before:
        paused_props = dict(item_props)
        paused_props["status"] = "paused"
        paused_props["tags"] = [tag for tag in paused_props.get("tags", [])
                                  if not str(tag).startswith("status/")] + ["status/paused"]
        paused_props["source_hash"] = content_hash(paused_props, item_body)
        paused = commit_replacements(
            root, item_candidate,
            {relative_item: frontmatter(paused_props, item_body)},
            f"Quiesce {story_id} after target advance",
            {"Record": "item-quiesce-v1", "Protocol": "1", "Delivery": delivery_id,
             "Story": story_id, "Kind": "target-drift", "Previous-Tip": item_candidate,
             "Slot": slot},
        )
        atomic_push(root, remote, [(refs["item"], item_candidate, paused),
                                   (slot_ref, item_candidate, "")])
        discard_pending_writer_receipt(root, delivery_id, story_id, item_candidate)
        raise RuntimeError(
            "DELIVERY_TARGET_DRIFT: target advanced after Item activation; Item was paused before worktree creation"
        )


ITEM_WRITER_FIELDS = ("status", "tags", "architecture_delta_hash", "integration_base_commit")


def published_plan_paths(root: Path, directory: Path, docs: Path) -> list[str]:
    """Exactly what the published execution plan owns: its package, its contracts and its policy.

    Item evidence files are deliberately excluded. The plan owns each Item's control
    file; the writer owns the review and verification records beside it.
    """
    paths = package_paths(root, directory, docs, include_items=False, include_map=False)
    paths += pinned_policy_paths(root, directory, docs)
    paths += [rel_posix(root, item) for item in directory.glob("items/*/item.md")]
    operation_paths, _bindings = execution_operation_inputs(root, directory, docs)
    return sorted(set(paths + operation_paths))


def published_plan_blobs(root: Path, source: str, paths: list[str]) -> dict[str, str]:
    """Read the exact published bytes of each plan path, skipping what it does not carry."""
    values = {}
    for relative in paths:
        entry = run_git(root, "--no-replace-objects", "ls-tree", source, "--", relative)
        if not entry:
            continue
        metadata, _path = entry.split("\t", 1)
        mode, kind, oid = metadata.split()
        if kind != "blob" or mode != "100644":
            raise RuntimeError("published plan path is not a regular file: " + relative)
        values[relative] = subprocess.run(
            ["git", "--no-replace-objects", "cat-file", "blob", oid], cwd=root,
            capture_output=True, check=True).stdout.decode("utf-8")
    return values


def unintegrated_predecessors(root: Path, remote: str, delivery_id: str, directory: Path,
                              integration_oid: str, predecessors) -> list[str]:
    """The Items one Item executes after that are not integrated yet.

    An Item is integrated when its remote Item tip records status integrated and
    the Integration contains that exact tip, as publish-delivery-review requires
    of every Item. A tip this checkout lacks cannot be in the Integration.
    """
    from delivery_compile import split_note
    waiting = []
    for story in sorted(set(predecessors or [])):
        tip = remote_oid(root, remote, canonical_refs(delivery_id, story)["item"])
        relative = (directory / "items" / story_key(story) / "item.md").relative_to(root).as_posix()
        present = subprocess.run(["git", "cat-file", "-e", tip + "^{commit}"], cwd=root,
                                 capture_output=True, check=False).returncode == 0
        if not (present and is_ancestor(root, tip, integration_oid)
                and split_remote_note(root, tip, relative, split_note)[0].get("status") == "integrated"):
            waiting.append(story)
    return waiting


def merged_story_owners(root: Path, commit: str, stories) -> dict[str, str]:
    """Map each of *stories* that a merged Delivery delivered to that Delivery.

    A merged Delivery drops its Item refs, and the copy of its package in
    *commit* records it instead. An Item recorded integrated there counts only
    when *commit* also holds the merge of that Delivery's recorded PR, so a
    package that reached the tree any other way proves nothing.
    """
    from delivery_compile import delivery_root, docs_root, recorded_pr_merged, split_note
    wanted = {story_key(story): story for story in stories}
    if not wanted:
        return {}
    deliveries = rel_posix(root, delivery_root(docs_root(root)) / "deliveries")
    owners: dict[str, str] = {}
    proven: dict[str, bool] = {}
    for path in git_paths(root, "ls-tree", "-r", "-z", "--name-only", commit, "--", deliveries + "/"):
        parts = path[len(deliveries) + 1:].split("/")
        if len(parts) != 4 or parts[1] != "items" or parts[3] != "item.md" or parts[2] not in wanted:
            continue
        owner = "-".join(parts[0].split("-", 2)[:2]).upper()
        if not DELIVERY_ID_RE.fullmatch(owner):
            continue
        if split_remote_note(root, commit, path, split_note)[0].get("status") != "integrated":
            continue
        if owner not in proven:
            proven[owner] = recorded_pr_merged(root, owner, commit)
        if proven[owner]:
            owners[wanted[parts[2]]] = owner
    return owners


def unmet_waits_for(root: Path, remote: str, delivery_id: str, integration_oid: str,
                    waits_for) -> tuple[list[str], list[str]]:
    """The Stories one Item waits for that it cannot start after yet.

    A waits_for Story is met when its remote Item tip records status integrated
    and this Delivery's Integration contains that exact tip: the Story's own
    Delivery merged into the target and this Delivery refreshed onto it. The
    tip's Delivery trailer names the package that records the Story's status.
    A merged Delivery drops its Item refs, so without one the merged package
    answers: in this Integration the Story is met, only in the target it is on
    its way. The first list names each Story still on its way, with its
    Delivery. The second names each Story no Delivery is delivering: one never
    claimed, and one its Delivery cancelled, whose Item ref keeps any other
    Delivery from claiming it again.
    """
    from delivery_compile import delivery_root, docs_root, split_note
    stories = sorted(set(waits_for or []))
    if not stories:
        return [], []
    deliveries = rel_posix(root, delivery_root(docs_root(root)) / "deliveries")
    item_refs = {story: canonical_refs(delivery_id, story)["item"] for story in stories}
    tips = remote_ref_oids(root, remote, list(item_refs.values()))
    unclaimed = [story for story in stories if not tips[item_refs[story]]]
    delivered = merged_story_owners(root, integration_oid, unclaimed)
    arriving = {}
    if len(delivered) < len(unclaimed):
        _branch, target = fetch_target(root, remote, recorded=recorded_target_branch(root, delivery_id))
        arriving = merged_story_owners(root, target, [story for story in unclaimed if story not in delivered])
    waiting, undeliverable = [], []
    for story in stories:
        tip = tips[item_refs[story]]
        if not tip:
            if story in arriving:
                waiting.append(f"{story} from {arriving[story]}")
            elif story not in delivered:
                undeliverable.append(f"{story} was never claimed")
            continue
        if subprocess.run(["git", "cat-file", "-e", tip + "^{commit}"], cwd=root,
                          capture_output=True, check=False).returncode:
            # Another Delivery's Item advances on its own hosts, and integration moves no Fence.
            run_git(root, "fetch", "--no-tags", remote, item_refs[story])
        owner = trailer(commit_message(root, tip), "Delivery") or ""
        packages = [path for path in git_paths(root, "ls-tree", "-z", "--name-only", tip, "--", deliveries + "/")
                    if owner and path.rsplit("/", 1)[-1].startswith(owner.lower() + "-")]
        if len(packages) != 1:
            raise RuntimeError(f"DELIVERY_COORDINATION_CORRUPT: the Item tip of {story} does not hold "
                               f"one package of its Delivery {owner or 'none'}")
        relative = f"{packages[0]}/items/{story_key(story)}/item.md"
        status = split_remote_note(root, tip, relative, split_note)[0].get("status")
        if status == "cancelled":
            undeliverable.append(f"{story} was cancelled with {owner}")
        elif status != "integrated" or not is_ancestor(root, tip, integration_oid):
            waiting.append(f"{story} from {owner}")
    return waiting, undeliverable


def activation_took_no_effect(root: Path, remote: str, item_ref: str, leased_tip: str) -> bool:
    """Whether a rejected activation provably changed no ref.

    An activation pushes all its refs in one atomic transaction, so an Item ref
    that is absent or still holds the tip the activation leased proves that none
    changed, whatever the Slot holds now.
    """
    return remote_ref_oids(root, remote, [item_ref])[item_ref] in ("", leased_tip)


def activation_landed(root: Path, remote: str, item_ref: str, slot_ref: str, candidate: str) -> bool:
    """Whether a rejected activation's push landed anyway: its Item and Slot refs hold its candidate."""
    return remote_ref_oids(root, remote, [item_ref, slot_ref]) == {item_ref: candidate, slot_ref: candidate}


def start_item(project_root: Path, delivery_id: str, story_id: str,
               remote: str = "origin", allowed_statuses: set[str] | None = None) -> dict:
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    from delivery_compile import docs_root, split_note, frontmatter, content_hash
    directory = find_delivery_dir_from_remote(root, remote, delivery_id)
    if directory is None:
        raise RuntimeError("local Delivery package is required for Item activation")
    refuse_pending_decisions(root, directory, "start-item", [story_id])
    refs = canonical_refs(delivery_id, story_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    item_oid = remote_oid(root, remote, refs["item"])
    fence_message = commit_message(root, fence_oid)
    require_fence_record(fence_message)
    if trailer(fence_message, "Mode") != "open":
        raise RuntimeError("DELIVERY_FENCE_MODE: start-item requires an open Fence")
    fence_target = trailer(fence_message, "Target")
    if not fence_target or not OID_RE.fullmatch(fence_target):
        raise RuntimeError("DELIVERY_FENCE_CORRUPT: start-item requires a valid Fence target baseline")
    target_before = require_target_ancestry(root, remote, fence_message, integration_oid, item_oid,
                                            delivery_id=delivery_id)
    max_parallel = project_max_parallel(root, trailer(fence_message, "Governance-Hash") or "none")
    occupied = remote_slot_oids(root, remote)
    free = next((slot for slot in range(1, max_parallel + 1) if slot_key(slot) not in occupied), None)
    if free is None:
        raise RuntimeError("DELIVERY_SLOT_UNAVAILABLE: no global execution Slot is available")
    slot = slot_key(free)
    slot_ref = canonical_refs(delivery_id, story_id, slot)["slot"]
    item_path = directory / "items" / story_key(story_id) / "item.md"
    if not item_path.exists():
        raise RuntimeError(f"missing local Item projection: {item_path}")
    relative_item = rel_posix(root, item_path)
    live_props, item_body = split_remote_note(root, item_oid, relative_item, split_note)
    allowed = {"in_scope", "paused", "blocked"} if allowed_statuses is None else allowed_statuses
    if live_props.get("status") not in allowed:
        raise RuntimeError("Item is not startable from its current status")
    # Activation carries the currently published plan and its Operation contracts into
    # the Item. Activating on the Item's own stale tree would hand its writer a plan, and
    # a contract, that a later approval already replaced, with no supported way to reach
    # the current ones: the Item ref is only ever built from the plan at claim time.
    # The plan owns the Item's claims and bindings; the Item ref owns its lifecycle and
    # whatever its writer has already stamped.
    plan_props, _plan_body = split_remote_note(root, integration_oid, relative_item, split_note)
    waiting = unintegrated_predecessors(root, remote, delivery_id, directory, integration_oid,
                                        plan_props.get("execution_after"))
    if waiting:
        raise RuntimeError(f"DELIVERY_DEPENDENCY_UNMET: {story_id} starts only after these Items are integrated: "
                           + ", ".join(waiting))
    waiting, undeliverable = unmet_waits_for(root, remote, delivery_id, integration_oid,
                                             plan_props.get("waits_for"))
    if undeliverable:
        raise RuntimeError(f"DELIVERY_DEPENDENCY_UNMET: {story_id} waits for Stories no Delivery is delivering: "
                           + ", ".join(undeliverable)
                           + f"; revise the backlog so {story_id} no longer depends on them")
    if waiting:
        raise RuntimeError(f"DELIVERY_DEPENDENCY_UNMET: {story_id} starts only after this Integration holds "
                           "these Stories integrated: " + ", ".join(waiting)
                           + "; merge their Deliveries into the target, then refresh this one")
    item_props = dict(plan_props)
    for key in ITEM_WRITER_FIELDS:
        if key in live_props:
            item_props[key] = live_props[key]
    require_item_operation_bindings(root, item_props)
    writer = epoch_token()
    item_props["status"] = "active"
    item_props["tags"] = [tag for tag in item_props.get("tags", []) if not str(tag).startswith("status/")] + ["status/active"]
    item_props["source_hash"] = content_hash(item_props, item_body)
    refreshed = published_plan_blobs(root, integration_oid,
                                     published_plan_paths(root, directory, docs_root(root)))
    refreshed[relative_item] = frontmatter(item_props, item_body)
    item_candidate = commit_replacements(
        root, item_oid, refreshed,
        f"Activate {story_id} for {delivery_id}",
        {"Record": "item-activation-v1", "Protocol": "1", "Delivery": delivery_id,
         "Story": story_id, "Claim": item_oid, "Item-Plan-Hash": str(item_props.get("item_plan_hash", "none")),
         "Slot": slot, "Writer-Epoch": writer},
    )
    integration_candidate = commit_tree(
        root, integration_oid, [], f"Authorize Item {story_id} for {delivery_id}",
        {"Record": "item-start-authorized-v1", "Protocol": "1", "Delivery": delivery_id,
         "Story": story_id, "Plan-Hash": str(item_props.get("item_plan_hash", "none")),
         "Item-Tip": item_candidate, "Target": trailer(fence_message, "Target") or "none",
         "Slot": slot, "Writer-Epoch": writer},
    )
    fence_candidate = commit_tree(
        root, fence_oid, [], f"Authorize Item {story_id} for {delivery_id}",
        {"Record": "project-fence-v2", "Protocol": "2", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": trailer(fence_message, "Target") or "none",
         "Governance-Hash": trailer(fence_message, "Governance-Hash") or "none",
         **carried_fence_barrier(fence_message)},
    )
    receipt = create_writer_receipt(
        root, delivery_id, story_id, slot, writer, refs["item"], slot_ref,
        item_candidate,
    )
    updates = [(refs["fence"], fence_oid, fence_candidate),
               (refs["integration"], integration_oid, integration_candidate),
               (refs["item"], item_oid, item_candidate),
               (slot_ref, "", item_candidate)]
    try:
        atomic_push(root, remote, updates)
    except RuntimeError:
        if activation_took_no_effect(root, remote, refs["item"], item_oid):
            discard_pending_writer_receipt(root, delivery_id, story_id, item_candidate)
        elif activation_landed(root, remote, refs["item"], slot_ref, item_candidate):
            require_current_activation_target(root, remote, delivery_id, story_id, target_before,
                                              slot, item_candidate, relative_item, item_props, item_body)
            promote_writer_receipt(root, delivery_id, story_id, item_candidate)
        raise
    if remote_oid(root, remote, refs["item"]) != item_candidate or remote_oid(root, remote, slot_ref) != item_candidate:
        raise RuntimeError("activation refs did not converge to the receipt candidate")
    require_current_activation_target(root, remote, delivery_id, story_id, target_before,
                                      slot, item_candidate, relative_item, item_props, item_body)
    receipt = promote_writer_receipt(root, delivery_id, story_id, item_candidate)
    worktree = materialize_item_worktree(root, delivery_id, story_id, item_candidate)
    return {"ok": True, "delivery": delivery_id, "story": story_id, "slot": slot,
            "writer_epoch": writer, "item": item_candidate, "integration": integration_candidate,
            "fence": fence_candidate, "receipt": receipt, "worktree": str(worktree),
            "refs": short_refs(delivery_id, story_id, slot)}


def _set_active_item_status(project_root: Path, delivery_id: str, story_id: str,
                            status: str, remote: str = "origin") -> dict:
    """Advance an active Item and its retained Slot to blocked/active."""
    if status not in {"active", "blocked"}:
        raise ValueError("active Item status transition must be active or blocked")
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    from delivery_compile import docs_root, split_note, frontmatter, content_hash
    directory = find_delivery_dir_from_remote(root, remote, delivery_id)
    if directory is None:
        raise RuntimeError("local Delivery package is required for Item status transition")
    refs = canonical_refs(delivery_id, story_id)
    item_oid = remote_oid(root, remote, refs["item"])
    slots = remote_slot_oids(root, remote)
    slot = next((key for key, oid in slots.items() if oid == item_oid), None)
    if slot is None:
        raise RuntimeError("DELIVERY_ITEM_SLOT_DIVERGED: Item and Slot refs diverge; refuse active status transition")
    relative_item = rel_posix(root, directory / "items" / story_key(story_id) / "item.md")
    props, body = split_remote_note(root, item_oid, relative_item, split_note)
    if props.get("status") not in {"active", "blocked"}:
        raise RuntimeError("Item is not active or blocked")
    worktree = worktree_paths(root, delivery_id, story_id)["item"]
    if worktree.exists():
        worktree_is_clean_and_at(root, worktree, item_oid)
    previous = props.get("status")
    props["status"] = status
    props["tags"] = [tag for tag in props.get("tags", []) if not str(tag).startswith("status/")] + [f"status/{status}"]
    props["source_hash"] = content_hash(props, body)
    candidate = commit_replacements(
        root, item_oid, {relative_item: frontmatter(props, body)},
        f"Set {story_id} {status} for {delivery_id}",
        {"Record": "item-status-v1", "Protocol": "1", "Delivery": delivery_id,
         "Story": story_id, "Previous-Tip": item_oid, "Status": status, "Slot": slot},
    )
    slot_ref = f"refs/heads/agentrof/slots/{slot}"
    atomic_push(root, remote, [(refs["item"], item_oid, candidate),
                               (slot_ref, item_oid, candidate)])
    if worktree.exists():
        run_git(root, "-C", str(worktree), "reset", "--hard", candidate)
    return {"ok": True, "delivery": delivery_id, "story": story_id,
            "from": previous, "status": status, "item": candidate, "slot": candidate}


def block_item(project_root: Path, delivery_id: str, story_id: str,
               remote: str = "origin") -> dict:
    return _set_active_item_status(project_root, delivery_id, story_id, "blocked", remote)


def unblock_item(project_root: Path, delivery_id: str, story_id: str,
                 remote: str = "origin") -> dict:
    return _set_active_item_status(project_root, delivery_id, story_id, "active", remote)


def reopen_item(project_root: Path, delivery_id: str, story_id: str,
                remote: str = "origin") -> dict:
    """Reopen one integrated Item through the explicit failure path.

    A sealed Item is never passed back through ``start-item``. It reopens on the
    Integration that absorbed it: the new tip carries the Integration's tree, so
    it contains every target the Integration contains, and keeps the sealed tip
    as its first parent so the Item's history stays one line. Integration
    receives a separate authorization record and the new Item tip alone
    acquires the Slot. Evidence sealed on the previous tip no longer names the
    product tip, so the Item must be reviewed and verified again before it can
    push.
    """
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    from delivery_compile import docs_root, split_note, frontmatter, content_hash
    directory = find_delivery_dir_from_remote(root, remote, delivery_id)
    if directory is None:
        raise RuntimeError("local Delivery package is required for Item reopen")
    refuse_pending_decisions(root, directory, "reopen-item", [story_id])
    refs = canonical_refs(delivery_id, story_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    item_oid = remote_oid(root, remote, refs["item"])
    fence_message = commit_message(root, fence_oid)
    require_fence_record(fence_message)
    if trailer(fence_message, "Mode") != "open":
        raise RuntimeError("DELIVERY_FENCE_MODE: reopen-item requires an open Fence")
    if not is_ancestor(root, item_oid, integration_oid):
        raise RuntimeError("reopen-item requires an Item its Integration has absorbed")
    target_before = require_target_ancestry(root, remote, fence_message, integration_oid, delivery_id=delivery_id)
    # A reopen activates the Item, so it reads the limit only while the Fence carries it.
    max_parallel = project_max_parallel(root, trailer(fence_message, "Governance-Hash") or "none")
    if any(oid == item_oid for oid in remote_slot_oids(root, remote).values()):
        raise RuntimeError("reopen-item requires a sealed, slotless Item")
    relative_item = rel_posix(root, directory / "items" / story_key(story_id) / "item.md")
    # The Integration carries the sealed control file byte for byte; a refresh
    # after integration may only have regenerated projections around it.
    props, body = split_remote_note(root, integration_oid, relative_item, split_note)
    if props.get("status") != "integrated":
        raise RuntimeError("reopen-item requires an integrated Item")
    require_item_operation_bindings(root, props)
    props["status"] = "active"
    props["tags"] = [tag for tag in props.get("tags", []) if not str(tag).startswith("status/")] + ["status/active"]
    props["integration_base_commit"] = integration_oid
    props["source_hash"] = content_hash(props, body)
    writer = epoch_token()
    item_candidate = commit_replacements(
        root, integration_oid, {relative_item: frontmatter(props, body)},
        f"Reopen {story_id} for {delivery_id}",
        {"Record": "item-reopen-v1", "Protocol": "1", "Delivery": delivery_id,
         "Story": story_id, "Previous-Tip": item_oid, "Integration-Base": integration_oid,
         "Writer-Epoch": writer},
        parents=(item_oid, integration_oid),
    )
    integration_candidate = commit_tree(
        root, integration_oid, [], f"Authorize reopen of {story_id} for {delivery_id}",
        {"Record": "item-reopen-authorized-v1", "Protocol": "1", "Delivery": delivery_id,
         "Story": story_id, "Previous-Tip": item_oid, "Item-Tip": item_candidate,
         "Integration-Base": integration_oid, "Writer-Epoch": writer},
    )
    occupied = remote_slot_oids(root, remote)
    free = next((slot for slot in range(1, max_parallel + 1) if slot_key(slot) not in occupied), None)
    if free is None:
        raise RuntimeError("DELIVERY_SLOT_UNAVAILABLE: no global execution Slot is available for reopen")
    slot = slot_key(free)
    slot_ref = canonical_refs(delivery_id, story_id, slot)["slot"]
    fence_candidate = commit_tree(
        root, fence_oid, [], "Authorize Item reopen",
        {"Record": "project-fence-v2", "Protocol": "2", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": trailer(fence_message, "Target") or "none",
         "Governance-Hash": trailer(fence_message, "Governance-Hash") or "none",
         **carried_fence_barrier(fence_message)},
    )
    receipt = create_writer_receipt(
        root, delivery_id, story_id, slot, writer, refs["item"], slot_ref,
        item_candidate, allow_verified_replace=True, expected_previous_oid=item_oid,
    )
    try:
        atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                                   (refs["integration"], integration_oid, integration_candidate),
                                   (refs["item"], item_oid, item_candidate),
                                   (slot_ref, "", item_candidate)])
    except RuntimeError:
        if activation_took_no_effect(root, remote, refs["item"], item_oid):
            discard_pending_writer_receipt(root, delivery_id, story_id, item_candidate)
        elif activation_landed(root, remote, refs["item"], slot_ref, item_candidate):
            require_current_activation_target(root, remote, delivery_id, story_id, target_before,
                                              slot, item_candidate, relative_item, props, body)
            promote_writer_receipt(root, delivery_id, story_id, item_candidate)
        raise
    require_current_activation_target(root, remote, delivery_id, story_id, target_before,
                                      slot, item_candidate, relative_item, props, body)
    receipt = promote_writer_receipt(root, delivery_id, story_id, item_candidate)
    worktree = materialize_item_worktree(root, delivery_id, story_id, item_candidate)
    return {"ok": True, "delivery": delivery_id, "story": story_id, "status": "active",
            "writer_epoch": writer, "item": item_candidate, "integration": integration_candidate,
            "slot": slot, "receipt": receipt, "worktree": str(worktree),
            "refs": short_refs(delivery_id, story_id, slot)}


def pause_item(project_root: Path, delivery_id: str, story_id: str,
               remote: str = "origin") -> dict:
    """Pause an active Item only after proving its local worktree is flushable."""
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    from delivery_compile import split_note, frontmatter, content_hash
    directory = find_delivery_dir_from_remote(root, remote, delivery_id)
    if directory is None:
        raise RuntimeError("local Delivery package is required for Item pause")
    refs = canonical_refs(delivery_id, story_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    fence_message = commit_message(root, fence_oid)
    require_fence_record(fence_message)
    item_oid = remote_oid(root, remote, refs["item"])
    slots = remote_slot_oids(root, remote)
    slot = next((key for key, oid in slots.items() if oid == item_oid), None)
    if slot is None:
        raise RuntimeError("DELIVERY_ITEM_SLOT_DIVERGED: pause-item requires one exact Item Slot pair")
    slot_ref = f"refs/heads/agentrof/slots/{slot}"
    worktree = worktree_paths(root, delivery_id, story_id)["item"]
    worktree_is_clean_and_at(root, worktree, item_oid)
    relative_item = rel_posix(root, directory / "items" / story_key(story_id) / "item.md")
    item_props, item_body = split_remote_note(root, item_oid, relative_item, split_note)
    if item_props.get("status") not in {"active", "blocked"}:
        raise RuntimeError("pause-item requires an active or blocked Item")
    item_props["status"] = "paused"
    item_props["tags"] = [tag for tag in item_props.get("tags", []) if not str(tag).startswith("status/")] + ["status/paused"]
    item_props["source_hash"] = content_hash(item_props, item_body)
    item_candidate = commit_replacements(
        root, item_oid, {relative_item: frontmatter(item_props, item_body)},
        f"Pause {story_id} for {delivery_id}",
        {"Record": "item-quiesce-v1", "Protocol": "1", "Delivery": delivery_id,
         "Story": story_id, "Kind": "pause", "Previous-Tip": item_oid, "Slot": slot},
    )
    fence_candidate = commit_tree(
        root, fence_oid, [], f"Fence project in open mode",
        {"Record": "project-fence-v2", "Protocol": "2", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": trailer(fence_message, "Target") or "none",
         "Governance-Hash": trailer(fence_message, "Governance-Hash") or "none",
         **carried_fence_barrier(fence_message)},
    )
    atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                               (refs["item"], item_oid, item_candidate),
                               (slot_ref, item_oid, "")])
    remove_item_worktree(root, delivery_id, story_id)
    clear_verified_writer_receipt(root, delivery_id, story_id)
    return {"ok": True, "delivery": delivery_id, "story": story_id,
            "status": "paused", "item": item_candidate, "fence": fence_candidate,
            "slot_released": slot_ref, "refs": short_refs(delivery_id, story_id)}


def resume_item(project_root: Path, delivery_id: str, story_id: str,
                remote: str = "origin") -> dict:
    """Resume only a paused, slotless Item through the normal activation CAS."""
    root = main_worktree(project_root.resolve())
    refs = canonical_refs(delivery_id, story_id)
    item_oid = remote_oid(root, remote, refs["item"])
    from delivery_compile import docs_root, split_note, find_delivery
    directory = find_delivery(docs_root(root), delivery_id)
    if directory is None:
        raise RuntimeError("local Delivery package is required for Item resume")
    item_path = directory / "items" / story_key(story_id) / "item.md"
    item_props, _ = split_remote_note(root, item_oid, rel_posix(root, item_path), split_note)
    if item_props.get("status") != "paused":
        raise RuntimeError("resume-item requires a paused remote Item")
    if any(oid == item_oid for oid in remote_slot_oids(root, remote).values()):
        raise RuntimeError("resume-item requires a slotless paused Item")
    return start_item(root, delivery_id, story_id, remote, allowed_statuses={"paused"})


def lane_work(root: Path, worktree: Path, item_props: dict) -> tuple[dict[str, list[str]], list[str]]:
    """Split the Item worktree's uncommitted paths, against its committed head, by approved lane scope.

    Returns each lane role's changed paths inside its scope and the changed
    paths no lane scope holds, such as the Software Architect's records.
    """
    from delivery_compile import lane_roles, lane_scope_map
    scopes, _unreadable = lane_scope_map(item_props)
    pending = sorted(worktree_pending_paths(root, worktree))
    lanes = {role: [path for path in pending
                    if any(path == scope or path.startswith(scope + "/") for scope in scopes.get(role, []))]
             for role in lane_roles(item_props)}
    owned = {path for paths in lanes.values() for path in paths}
    return lanes, [path for path in pending if path not in owned]


def writer_receipt_state(root: Path, delivery_id: str, story_id: str, item_oid: str,
                         slot_ref: str | None) -> str:
    """Whether this host holds the Item's writer receipt: verified, pending, stale or missing.

    A receipt that cannot be read, or that no longer matches the remote Item and
    Slot pair, is stale.
    """
    try:
        receipt = read_writer_receipt(root, delivery_id, story_id)
    except RuntimeError:
        return "stale"
    if receipt is None or receipt.get("state") != "verified":
        return "missing" if receipt is None else "pending"
    try:
        active_writer_receipt(root, delivery_id, story_id, item_oid, slot_ref or "")
    except RuntimeError:
        return "stale"
    return "verified"


def lane_status(project_root: Path, delivery_id: str, story_id: str, remote: str = "origin") -> dict:
    """Report where each parallel lane of an Item stands in this host's Item worktree.

    After a host loss the lanes' work exists only uncommitted in that worktree,
    so each lane's state is its changed paths inside its approved scope against
    the worktree's committed head; a lane with none has no work there.
    """
    root = main_worktree(project_root.resolve())
    from delivery_compile import implementation_schedule, lane_roles, split_note
    directory = find_delivery_dir_from_remote(root, remote, delivery_id)
    if directory is None:
        raise RuntimeError("local Delivery package is required for lane status")
    refs = canonical_refs(delivery_id, story_id)
    item_oid = remote_oid(root, remote, refs["item"])
    relative_item = rel_posix(root, directory / "items" / story_key(story_id) / "item.md")
    props, _body = split_remote_note(root, item_oid, relative_item, split_note)
    if implementation_schedule(props) != "parallel_lanes_v1":
        raise RuntimeError(f"{story_id} runs its implementation roles in sequence and has no lanes")
    slot = next((key for key, oid in remote_slot_oids(root, remote).items() if oid == item_oid), None)
    slot_ref = f"refs/heads/agentrof/slots/{slot}" if slot else None
    worktree = worktree_paths(root, delivery_id, story_id)["item"]
    head = worktree_head(root, worktree) if worktree.exists() else "absent"
    lanes, outside = (lane_work(root, worktree, props) if worktree.exists()
                      else ({role: [] for role in lane_roles(props)}, []))
    observations = [
        {"kind": "worktree", "target": "item_worktree_head", "value": head},
        {"kind": "file", "target": "writer_receipt",
         "value": writer_receipt_state(root, delivery_id, story_id, item_oid, slot_ref)},
        {"kind": "worktree", "target": "lanes_with_work", "value": [role for role, paths in lanes.items() if paths]},
        {"kind": "worktree", "target": "outside_lane_scopes", "value": outside},
        *({"kind": "worktree", "target": f"lane:{role}", "value": paths} for role, paths in lanes.items()),
    ]
    return {"ok": True, "mutation_state": "none", "item": item_oid, "observations": observations}


def refuse_to_discard_lane_work(root: Path, delivery_id: str, story_id: str, worktree: Path,
                                item_oid: str, slot_ref: str, item_props: dict) -> None:
    """Refuse a takeover that would discard uncommitted lane work, naming it and the owner's choice.

    Item commits stay local until push-item, so a worktree ahead of the remote
    Item tip holds committed work no ref holds. The discard commands then reset
    to the worktree's own HEAD, which keeps those commits, and the refusal lists
    them: dropping them is a separate choice.
    """
    from delivery_compile import implementation_schedule
    if implementation_schedule(item_props) != "parallel_lanes_v1" or not worktree_pending_paths(root, worktree):
        return
    lanes, outside = lane_work(root, worktree, item_props)
    report = "; ".join(f"{role}: {', '.join(paths) if paths else 'no work'}" for role, paths in lanes.items())
    if outside:
        report += "; outside every lane scope: " + ", ".join(outside)
    head = worktree_head(root, worktree)
    diverged = head != item_oid
    ahead = run_git(root, "-C", str(worktree), "log", "--format=%H %s", f"{item_oid}..{head}") if diverged else ""
    discard = (f"discard it with `git -C {worktree} reset --hard {'HEAD' if diverged else item_oid}`"
               f" and `git -C {worktree} clean -fd`, then run takeover-item again")
    if writer_receipt_state(root, delivery_id, story_id, item_oid, slot_ref) == "verified":
        choice = ("This host still holds the Item's verified writer receipt, so the choice is to keep it"
                  " without takeover: finish the lanes that have work and commit it as the coordinator in"
                  f" {worktree}; or to {discard}")
    else:
        choice = ("This host holds no verified writer receipt for the Item, so it cannot commit and publish"
                  f" that work: copy out any path to keep and {discard}")
    if ahead:
        choice += (f". The worktree's HEAD also holds commits the remote Item tip {item_oid} does not, which no"
                   f" ref holds: {'; '.join(ahead.splitlines())}. `reset --hard HEAD` keeps them, and takeover"
                   " refuses while the worktree is ahead of the tip, since it would drop them: discarding them"
                   " is a separate, explicit choice.")
    raise RuntimeError(f"DELIVERY_WORKTREE_UNSAFE: takeover would discard the uncommitted lane work in the Item"
                       f" worktree {worktree}: {report}. {choice}")


def takeover_item(project_root: Path, delivery_id: str, story_id: str,
                  remote: str = "origin", *, confirm: bool = False) -> dict:
    """Take over one active Item after explicit host-loss confirmation."""
    if not confirm:
        raise RuntimeError("takeover requires explicit host-loss confirmation")
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    from delivery_compile import split_note, frontmatter, content_hash
    directory = find_delivery_dir_from_remote(root, remote, delivery_id)
    if directory is None:
        raise RuntimeError("local Delivery package is required for Item takeover")
    refs = canonical_refs(delivery_id, story_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    item_oid = remote_oid(root, remote, refs["item"])
    target_before = require_target_ancestry(root, remote, commit_message(root, fence_oid), integration_oid, item_oid,
                                            delivery_id=delivery_id)
    slots = remote_slot_oids(root, remote)
    slot = next((key for key, oid in slots.items() if oid == item_oid), None)
    if slot is None:
        raise RuntimeError("DELIVERY_ITEM_SLOT_DIVERGED: takeover requires one exact existing Item Slot pair")
    slot_ref = f"refs/heads/agentrof/slots/{slot}"
    relative_item = rel_posix(root, directory / "items" / story_key(story_id) / "item.md")
    item_props, item_body = split_remote_note(root, item_oid, relative_item, split_note)
    if item_props.get("status") not in {"active", "blocked"}:
        raise RuntimeError("takeover requires an active or blocked remote Item")
    require_item_operation_bindings(root, item_props)
    worktree = worktree_paths(root, delivery_id, story_id)["item"]
    removed_worktree = worktree.exists()
    if removed_worktree:
        refuse_to_discard_lane_work(root, delivery_id, story_id, worktree, item_oid, slot_ref, item_props)
        worktree_is_clean_and_at(root, worktree, item_oid)
        remove_item_worktree(root, delivery_id, story_id)
    writer = epoch_token()
    item_props["status"] = "active"
    item_props["tags"] = [tag for tag in item_props.get("tags", []) if not str(tag).startswith("status/")] + ["status/active"]
    item_props["source_hash"] = content_hash(item_props, item_body)
    item_candidate = commit_replacements(
        root, item_oid, {relative_item: frontmatter(item_props, item_body)},
        f"Take over {story_id} for {delivery_id}",
        {"Record": "item-takeover-v1", "Protocol": "1", "Delivery": delivery_id,
         "Story": story_id,
         "Item-Plan-Hash": str(item_props.get("item_plan_hash", "none")),
         "Previous-Tip": item_oid, "Slot": slot, "Writer-Epoch": writer},
    )
    fence_message = commit_message(root, fence_oid)
    integration_candidate = commit_tree(
        root, integration_oid, [], f"Take over {story_id} for {delivery_id}",
        {"Record": "item-takeover-v1", "Protocol": "1", "Delivery": delivery_id,
         "Story": story_id,
         "Item-Plan-Hash": str(item_props.get("item_plan_hash", "none")),
         "Previous-Tip": item_oid, "Item-Tip": item_candidate,
         "Slot": slot, "Writer-Epoch": writer},
    )
    fence_candidate = commit_tree(
        root, fence_oid, [], "Fence project in open mode",
        {"Record": "project-fence-v2", "Protocol": "2", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": trailer(fence_message, "Target") or "none",
         "Governance-Hash": trailer(fence_message, "Governance-Hash") or "none",
         **carried_fence_barrier(fence_message)},
    )
    replaced = read_writer_receipt(root, delivery_id, story_id)
    create_writer_receipt(
        root, delivery_id, story_id, slot, writer, refs["item"], slot_ref,
        item_candidate, allow_verified_replace=True, expected_previous_oid=item_oid,
    )
    try:
        atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                                   (refs["integration"], integration_oid, integration_candidate),
                                   (refs["item"], item_oid, item_candidate),
                                   (slot_ref, item_oid, item_candidate)])
    except RuntimeError:
        # A takeover that changed no ref leaves this host the writer state it had.
        if activation_took_no_effect(root, remote, refs["item"], item_oid):
            discard_pending_writer_receipt(root, delivery_id, story_id, item_candidate, replaced)
            if removed_worktree:
                materialize_item_worktree(root, delivery_id, story_id, item_oid)
        elif activation_landed(root, remote, refs["item"], slot_ref, item_candidate):
            require_current_activation_target(root, remote, delivery_id, story_id, target_before,
                                              slot, item_candidate, relative_item, item_props, item_body)
            promote_writer_receipt(root, delivery_id, story_id, item_candidate)
        raise
    if remote_oid(root, remote, refs["item"]) != item_candidate or remote_oid(root, remote, slot_ref) != item_candidate:
        raise RuntimeError("takeover refs did not converge to the receipt candidate")
    require_current_activation_target(root, remote, delivery_id, story_id, target_before,
                                      slot, item_candidate, relative_item, item_props, item_body)
    receipt = promote_writer_receipt(root, delivery_id, story_id, item_candidate)
    materialized = materialize_item_worktree(root, delivery_id, story_id, item_candidate)
    return {"ok": True, "delivery": delivery_id, "story": story_id, "slot": slot,
            "writer_epoch": writer, "item": item_candidate, "integration": integration_candidate,
            "fence": fence_candidate, "receipt": receipt, "worktree": str(materialized),
            "refs": short_refs(delivery_id, story_id, slot)}


def push_item(project_root: Path, delivery_id: str, story_id: str,
              remote: str = "origin") -> dict:
    """Publish one Item's real product/test commit plus its exact evidence.

    The primary worktree is deliberately only a coordinator.  Product bytes and
    code-review/verification evidence are read from the active Item worktree,
    then attached to the remote Item ref as a single fast-forward child.
    """
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    from delivery_compile import split_note, frontmatter, content_hash
    directory = find_delivery_dir_from_remote(root, remote, delivery_id)
    if directory is None:
        raise RuntimeError("local Delivery package path is required for Item push")
    refs = canonical_refs(delivery_id, story_id)
    item_oid = remote_oid(root, remote, refs["item"])
    slots = remote_slot_oids(root, remote)
    slot = next((key for key, oid in slots.items() if oid == item_oid), None)
    if slot is None:
        raise RuntimeError("DELIVERY_ITEM_SLOT_DIVERGED: Item and Slot refs diverge; refuse active writer push")
    slot_ref = f"refs/heads/agentrof/slots/{slot}"; slot_oid = slots[slot]
    receipt = active_writer_receipt(root, delivery_id, story_id, item_oid, slot_ref)
    relative_delivery = rel_posix(root, directory)
    worktree = worktree_paths(root, delivery_id, story_id)["item"]
    require_visible_item_index(worktree)
    product_tip = worktree_head(root, worktree)
    if product_tip == item_oid or not is_ancestor(root, item_oid, product_tip):
        raise RuntimeError("DELIVERY_ITEM_NOT_READY: push-item requires a committed product/test change after the active remote Item tip")
    relative_item = rel_posix(root, directory / "items" / story_key(story_id) / "item.md")
    relative_review = rel_posix(root, directory / "items" / story_key(story_id) / "code-review.md")
    relative_verification = rel_posix(root, directory / "items" / story_key(story_id) / "verification.md")
    committed_changes = set(git_paths(root, "diff", "--name-only", "-z", item_oid, product_tip))
    if not committed_changes:
        raise RuntimeError("DELIVERY_ITEM_NOT_READY: push-item requires a committed product/test change")
    # A converged Item is checked against the Integration's own line, which
    # another host may have advanced since this one last saw it.
    integration_oid = remote_oid(root, remote, refs["integration"])
    run_git(root, "fetch", "--no-tags", remote, refs["integration"])
    require_item_publication_controls(root, item_oid, product_tip, relative_delivery, relative_item,
                                      integration_oid)
    require_item_path_claims(root, item_oid, product_tip, relative_item, lambda outside: provisional_path_refusal(
        root, remote, delivery_id, story_id, outside))
    pending = worktree_pending_paths(root, worktree)
    allowed_pending = {relative_review, relative_verification}
    if not pending.issubset(allowed_pending):
        unexpected = ", ".join(sorted(pending - allowed_pending))
        raise RuntimeError(f"DELIVERY_WORKTREE_UNSAFE: commit or remove non-evidence changes before push: {unexpected}")
    item_path = worktree / relative_item
    if not item_path.exists():
        raise RuntimeError(f"DELIVERY_WORKTREE_UNSAFE: missing Item worktree projection: {item_path}")
    committed_item = subprocess.run(
        ["git", "--no-replace-objects", "show", f"{product_tip}:{relative_item}"],
        cwd=root, capture_output=True, check=True).stdout
    committed_oid = run_git(root, "--no-replace-objects", "rev-parse", f"{product_tip}:{relative_item}")
    if not worktree_holds_blob(worktree, relative_item, committed_oid):
        raise RuntimeError("DELIVERY_WORKTREE_UNSAFE: Item worktree control differs from the committed product tip")
    from ba_compile import parse_frontmatter
    item_text = committed_item.decode("utf-8")
    item_props, body_line, error = parse_frontmatter(item_text)
    if error:
        raise RuntimeError("Item product control is invalid: " + error)
    item_body = "\n".join(item_text.splitlines()[body_line - 1:]).lstrip("\n")
    remote_item_props, _ = split_remote_note(root, item_oid, relative_item, split_note)
    if remote_item_props.get("status") != "active" or item_props.get("status") != "active":
        raise RuntimeError("push-item requires an active remote Item projection")
    review = item_path.parent / "code-review.md"
    verification = item_path.parent / "verification.md"
    if not review.exists() or not verification.exists():
        raise RuntimeError("DELIVERY_ITEM_NOT_READY: push-item requires Item review and verification files")
    from delivery_compile import item_evidence_file_findings
    evidence_findings = item_evidence_file_findings(worktree, product_tip, (review, verification))
    if evidence_findings:
        raise RuntimeError("DELIVERY_WORKTREE_UNSAFE: " + "; ".join(evidence_findings))
    review_props, review_body = split_note(review)
    verification_props, verification_body = split_note(verification)
    if review_props.get("status") != "approved" or verification_props.get("status") != "passed":
        raise RuntimeError("DELIVERY_ITEM_NOT_READY: push-item requires approved code review and passed verification")
    if review_props.get("reviewed_commit") != product_tip or verification_props.get("verified_commit") != product_tip:
        raise RuntimeError("DELIVERY_ITEM_NOT_READY: Item evidence must bind the exact committed product/test tip")
    if review_props.get("item_plan_hash") != item_props.get("item_plan_hash") or verification_props.get("item_plan_hash") != item_props.get("item_plan_hash"):
        raise RuntimeError("DELIVERY_ITEM_NOT_READY: Item evidence does not bind the active Item plan hash")
    require_item_architecture_binding(worktree, item_props, story_id, tree=product_tip)
    from delivery_compile import verification_schedule
    from delivery_verification import guard_write, validate as validate_verification, validate_evidence
    guard_write(worktree)
    validate_evidence(item_props, review_props, verification_props)
    if verification_schedule(item_props) == "parallel_snapshot_v1":
        session = validate_verification(worktree, delivery_id, story_id)
        if review_props.get("verification_candidate_hash") != session["candidate"]["candidate_hash"]:
            raise RuntimeError("DELIVERY_ITEM_NOT_READY: reports do not bind the current verification candidate")
    if review_props.get("source_hash") != content_hash(review_props, review_body):
        raise RuntimeError("DELIVERY_ITEM_NOT_READY: code review source_hash is stale")
    if verification_props.get("source_hash") != content_hash(verification_props, verification_body):
        raise RuntimeError("DELIVERY_ITEM_NOT_READY: verification source_hash is stale")
    item_props["source_hash"] = content_hash(item_props, item_body)
    replacements = {
        relative_item: frontmatter(item_props, item_body),
        relative_review: frontmatter(review_props, review_body),
        relative_verification: frontmatter(verification_props, verification_body),
    }
    candidate = commit_replacements(
        root, product_tip, replacements, f"Update Item {story_id}",
        {"Record": "item-evidence-v1", "Protocol": "1", "Delivery": delivery_id,
         "Story": story_id, "Previous-Tip": item_oid, "Product-Tip": product_tip,
         "Reviewed-Commit": product_tip, "Verified-Commit": product_tip,
         "Item-Plan-Hash": str(item_props.get("item_plan_hash", "none")),
         "Writer-Epoch": str(receipt["writer_epoch"]), "Slot": slot},
    )
    # The worktree moves to the candidate after the push, so prove first that the move keeps
    # every worktree byte; a refusal after the push would leave the Item ref already published.
    require_candidate_holds_worktree(root, worktree, candidate)
    atomic_push(root, remote, [(refs["item"], item_oid, candidate), (slot_ref, slot_oid, candidate)])
    if remote_oid(root, remote, refs["item"]) != candidate or remote_oid(root, remote, slot_ref) != candidate:
        raise RuntimeError("Item evidence refs did not converge to the published candidate")
    advance_worktree_to_candidate(root, worktree, candidate)
    return {"ok": True, "delivery": delivery_id, "story": story_id, "item": candidate,
            "slot": candidate, "product_tip": product_tip, "writer_epoch": receipt["writer_epoch"]}


def integrate_item(project_root: Path, delivery_id: str, story_id: str,
                  remote: str = "origin") -> dict:
    """Seal and merge only evidence that is already on the remote Item branch."""
    root = main_worktree(project_root.resolve())
    refuse_merged_delivery(root, delivery_id, remote)
    from delivery_compile import split_note, frontmatter, content_hash
    directory = find_delivery_dir_from_remote(root, remote, delivery_id)
    if directory is None:
        raise RuntimeError("local Delivery package is required for Item integration")
    refs = canonical_refs(delivery_id, story_id)
    integration_oid = remote_oid(root, remote, refs["integration"])
    item_oid = remote_oid(root, remote, refs["item"])
    slots = remote_slot_oids(root, remote)
    slot = next((key for key, oid in slots.items() if oid == item_oid), None)
    if slot is None:
        raise RuntimeError("DELIVERY_ITEM_SLOT_DIVERGED: Item and Slot refs diverge; refuse integration")
    slot_ref = f"refs/heads/agentrof/slots/{slot}"; slot_oid = slots[slot]
    active_writer_receipt(root, delivery_id, story_id, item_oid, slot_ref)
    worktree = worktree_paths(root, delivery_id, story_id)["item"]
    worktree_is_clean_and_at(root, worktree, item_oid)
    relative_item = rel_posix(root, directory / "items" / story_key(story_id) / "item.md")
    relative_review = rel_posix(root, directory / "items" / story_key(story_id) / "code-review.md")
    relative_verification = rel_posix(root, directory / "items" / story_key(story_id) / "verification.md")
    item_props, item_body = split_remote_note(root, item_oid, relative_item, split_note)
    if item_props.get("status") != "active":
        raise RuntimeError("integrate-item requires an active Item")
    review_props, review_body = split_remote_note(root, item_oid, relative_review, split_note)
    verification_props, verification_body = split_remote_note(root, item_oid, relative_verification, split_note)
    if review_props.get("status") != "approved" or verification_props.get("status") != "passed":
        raise RuntimeError("DELIVERY_ITEM_NOT_READY: integrate-item requires approved code review and passed verification")
    parents = run_git(root, "show", "-s", "--format=%P", item_oid).split()
    if len(parents) != 1 or not OID_RE.fullmatch(parents[0]):
        raise RuntimeError("DELIVERY_ITEM_NOT_READY: integrate-item requires one exact Item evidence parent")
    product_tip = parents[0]
    if review_props.get("reviewed_commit") != product_tip or verification_props.get("verified_commit") != product_tip:
        raise RuntimeError("DELIVERY_ITEM_NOT_READY: integrate-item evidence does not bind the published product/test tip")
    if review_props.get("item_plan_hash") != item_props.get("item_plan_hash") or verification_props.get("item_plan_hash") != item_props.get("item_plan_hash"):
        raise RuntimeError("DELIVERY_ITEM_NOT_READY: integrate-item evidence does not bind the Item plan hash")
    require_item_architecture_binding(worktree, item_props, story_id, tree=item_oid)
    from delivery_verification import validate_evidence
    validate_evidence(item_props, review_props, verification_props)
    if review_props.get("source_hash") != content_hash(review_props, review_body):
        raise RuntimeError("DELIVERY_ITEM_NOT_READY: integrate-item code review source_hash is stale")
    if verification_props.get("source_hash") != content_hash(verification_props, verification_body):
        raise RuntimeError("DELIVERY_ITEM_NOT_READY: integrate-item verification source_hash is stale")
    if not is_ancestor(root, item_oid, product_tip) and not is_ancestor(root, product_tip, item_oid):
        raise RuntimeError("integrate-item Item evidence ancestry is invalid")
    item_props["status"] = "integrated"
    item_props["tags"] = [tag for tag in item_props.get("tags", []) if not str(tag).startswith("status/")] + ["status/integrated"]
    item_props["integration_base_commit"] = integration_oid
    item_props["source_hash"] = content_hash(item_props, item_body)
    seal = commit_replacements(root, item_oid,
                               {relative_item: frontmatter(item_props, item_body)},
                               f"Seal Item {story_id} for {delivery_id}",
                               {"Record": "item-integration-v1", "Protocol": "1", "Delivery": delivery_id,
                                "Story": story_id, "Item-Plan-Hash": str(item_props.get("item_plan_hash", "none")),
                                "Reviewed-Tip": item_oid, "Product-Tip": product_tip,
                                "Integration-Parent": integration_oid})
    # The sealed Item's own control and evidence records were validated above and
    # win over whatever the Integration re-projected for them; the compiler-owned
    # projections are regenerated from the merged tree, as every other publication does.
    integration_candidate = merge_candidate(root, integration_oid, seal,
                                            f"Integrate Item {story_id} for {delivery_id}",
                                            {"Record": "item-integration-v1", "Protocol": "1", "Delivery": delivery_id,
                                             "Story": story_id, "Item-Plan-Hash": str(item_props.get("item_plan_hash", "none")),
                                             "Reviewed-Tip": seal, "Integration-Parent": integration_oid},
                                            delivery_projections=True,
                                            prefer_second=(relative_item, relative_review, relative_verification))
    atomic_push(root, remote, [(refs["integration"], integration_oid, integration_candidate),
                               (refs["item"], item_oid, integration_candidate),
                               (slot_ref, slot_oid, "")])
    remove_item_worktree(root, delivery_id, story_id)
    clear_verified_writer_receipt(root, delivery_id, story_id)
    return {"ok": True, "delivery": delivery_id, "story": story_id,
            "integration": integration_candidate, "item": integration_candidate,
            "slot_released": slot_ref}


def unique_merge_base(root: Path, first_parent: str, second_parent: str) -> str:
    result = subprocess.run(["git", "merge-base", "--all", first_parent, second_parent],
                            cwd=root, encoding="utf-8", capture_output=True, check=False)
    if result.returncode not in {0, 1}:
        raise RuntimeError(result.stderr.strip() or "cannot inspect merge ancestry")
    bases = result.stdout.splitlines()
    if len(bases) != 1:
        raise RuntimeError("merge requires one unambiguous common base")
    return bases[0]


def require_delivery_controls_unchanged(root: Path, directory: Path, reference: str, candidate: str) -> None:
    """Preserve the selected package's control paths, authored content and receipts."""
    from ba_compile import without_generated_relations

    def snapshot(tree):
        prefix = rel_posix(root, directory) + "/"
        listing = subprocess.run(["git", "ls-tree", "-rz", tree, "--", prefix],
                                 cwd=root, capture_output=True, check=True).stdout
        controls = {}
        for row in listing.split(b"\0"):
            if not row:
                continue
            metadata, raw_path = row.split(b"\t", 1)
            path = raw_path.decode("utf-8")
            if not path.endswith(".md"):
                continue
            mode, kind, oid = metadata.decode().split()
            if kind != "blob" or mode not in {"100644", "100755"}:
                raise RuntimeError("selected Delivery control must remain a regular file: " + path)
            content = subprocess.run(["git", "cat-file", "blob", oid], cwd=root,
                                     capture_output=True, check=True).stdout.decode("utf-8")
            controls[path] = (mode, without_generated_relations(content))
        return controls

    before, after = snapshot(reference), snapshot(candidate)
    changed = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    if changed:
        raise RuntimeError("DELIVERY_TARGET_SOURCE_VIOLATION: target changed selected Delivery control content or path set: " + ", ".join(changed))


def reconcile_delivery_projection_conflicts(root: Path, env: dict) -> None:
    """Resolve only owned projections, preserving ordinary authored merge conflicts."""
    import vault_check
    from ba_compile import without_generated_relations

    policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
    prefix = "workspace/docs/"
    reports = vault_check.relation_reports(vault_check.Vault(
        root=Path(env["GIT_INDEX_FILE"]).parent, policy=policy))
    owned_files = {prefix + "maps/delivery.md", *(prefix + path for path in reports)}
    catalog_root = prefix + str(policy.get("relation_contract", {}).get(
        "catalog_root", "maps/_relations")).rstrip("/") + "/"
    listing = subprocess.run(["git", "ls-files", "--unmerged", "-z"], cwd=root, env=env,
                             capture_output=True, check=True).stdout
    conflicts = {}
    for row in listing.split(b"\0"):
        if row:
            metadata, raw_path = row.split(b"\t", 1)
            mode, oid, stage = metadata.decode().split()
            conflicts.setdefault(raw_path.decode("utf-8"), {})[int(stage)] = (mode, oid)
    for path, stages in conflicts.items():
        if any(mode not in {"100644", "100755"} for mode, _oid in stages.values()):
            continue
        if path in owned_files or (path.startswith(catalog_root) and path.endswith(".md")):
            subprocess.run(["git", "update-index", "--force-remove", "--", path],
                           cwd=root, env=env, capture_output=True, check=True)
            continue
        if (not path.startswith(prefix) or not path.endswith(".md")
                or path.startswith(prefix + ".obsidian/")
                or vault_check.is_artifact_location(policy, path.removeprefix(prefix))):
            continue
        semantic = {}
        has_projection = False
        for stage, (mode, oid) in stages.items():
            if mode not in {"100644", "100755"}:
                break
            raw = subprocess.run(["git", "cat-file", "blob", oid], cwd=root,
                                 capture_output=True, check=True).stdout
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                break
            if text.startswith(policy.get("generated_marker_prefix", "<!-- generated by")):
                break
            start, end = vault_check.RELATION_START, vault_check.RELATION_END
            if start in text or end in text:
                lines = text.splitlines()
                if lines.count(start) != 1 or lines.count(end) != 1 or lines.index(start) > lines.index(end):
                    break
                has_projection = True
            semantic[stage] = (mode, without_generated_relations(text))
        if len(semantic) != len(stages) or not has_projection:
            continue
        base, ours, theirs = (semantic.get(stage) for stage in (1, 2, 3))
        chosen = 2 if ours == theirs or theirs == base else 3 if ours == base else None
        if chosen is None:
            continue
        subprocess.run(["git", "update-index", "--force-remove", "--", path],
                       cwd=root, env=env, capture_output=True, check=True)
        if chosen in stages:
            mode, oid = stages[chosen]
            subprocess.run(["git", "update-index", "--add", "--cacheinfo", mode, oid, path],
                           cwd=root, env=env, capture_output=True, check=True)


def unmerged_paths(root: Path, env: dict) -> list[str]:
    listing = subprocess.run(["git", "ls-files", "--unmerged", "-z"], cwd=root, env=env,
                             capture_output=True, check=True).stdout
    return sorted({row.split(b"\t", 1)[1].decode("utf-8") for row in listing.split(b"\0") if row})


def resolve_to_second_parent(root: Path, env: dict, second_parent: str, paths: tuple[str, ...]) -> None:
    """Resolve only the named paths to the second parent's exact entry.

    Every other conflict stays exactly as the three-way read left it, so an
    authored conflict is still refused by name rather than merged by guess.
    """
    pending = set(unmerged_paths(root, env)) & set(paths)
    for path in sorted(pending):
        entry = subprocess.run(["git", "ls-tree", second_parent, "--", path], cwd=root,
                               encoding="utf-8", capture_output=True, check=True).stdout.strip()
        subprocess.run(["git", "update-index", "--force-remove", "--", path],
                       cwd=root, env=env, capture_output=True, check=True)
        if not entry:
            continue
        metadata, _path = entry.split("\t", 1)
        mode, kind, oid = metadata.split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise RuntimeError("merge can only carry a regular file forward: " + path)
        subprocess.run(["git", "update-index", "--add", "--cacheinfo", f"{mode},{oid},{path}"],
                       cwd=root, env=env, capture_output=True, check=True)


def merge_candidate(root: Path, first_parent: str, second_parent: str,
                    subject: str, trailers: dict[str, str], *, delivery_projections: bool = False,
                    operation_bindings: dict[str, dict] | None = None,
                    preserve_delivery: Path | None = None,
                    prefer_second: tuple[str, ...] = ()) -> str:
    merge_base = unique_merge_base(root, first_parent, second_parent)
    with tempfile.TemporaryDirectory(prefix="agentrof-merge-index-") as temporary:
        index = Path(temporary) / "index"
        env = os.environ.copy(); env["GIT_INDEX_FILE"] = str(index)
        # --aggressive resolves what any merge resolves without looking at content:
        # a path one side deleted and the other left untouched, or both sides changed
        # identically. Everything else stays unmerged for the callers below.
        merge = subprocess.run(["git", "read-tree", "-m", "--aggressive", merge_base, first_parent, second_parent],
                               cwd=root, env=env, encoding="utf-8", capture_output=True, check=False)
        if merge.returncode:
            raise RuntimeError(merge.stderr.strip() or "Item and Integration trees conflict")
        if prefer_second:
            resolve_to_second_parent(root, env, second_parent, prefer_second)
        if delivery_projections:
            reconcile_delivery_projection_conflicts(root, env)
        conflicts = unmerged_paths(root, env)
        if conflicts:
            raise RuntimeError("merge left authored paths unmerged: " + ", ".join(conflicts))
        tree = subprocess.run(["git", "write-tree"], cwd=root, env=env, encoding="utf-8",
                              capture_output=True, check=False)
        if tree.returncode:
            raise RuntimeError(tree.stderr.strip() or "cannot write integration tree")
        if preserve_delivery is not None:
            require_delivery_controls_unchanged(root, preserve_delivery, first_parent, tree.stdout.strip())
        projected = (write_delivery_projection_tree(root, env, tree.stdout.strip(), operation_bindings)
                     if delivery_projections else tree.stdout.strip())
        message = subject + "\n\n" + "\n".join(f"Agentrof-{key}: {value}" for key, value in trailers.items()) + "\n"
        commit = git_with_input(root, ["commit-tree", projected, "-p", first_parent, "-p", second_parent],
                                message, env)
        if commit.returncode:
            raise RuntimeError(commit.stderr.strip() or "cannot create integration commit")
        return commit.stdout.strip()


def find_delivery_dir_from_remote(root: Path, remote: str, delivery_id: str) -> Path | None:
    from delivery_compile import docs_root, find_delivery
    return find_delivery(docs_root(root), delivery_id)


def main_worktree(root: Path) -> Path:
    common = Path(run_git(root, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (root / common).resolve()
    output = run_git(root, "worktree", "list", "--porcelain")
    records = output.split("\n\n") if output else []
    candidates = []
    for record in records:
        line = next((line for line in record.splitlines() if line.startswith("worktree ")), "")
        if line:
            candidate = Path(line.removeprefix("worktree ")).resolve()
            candidate_common = (candidate / ".git").resolve() if (candidate / ".git").is_file() else candidate / ".git"
            if candidate_common.exists() or candidate == root.resolve():
                candidates.append(candidate)
    if not candidates:
        raise RuntimeError("main worktree cannot be resolved")
    for candidate in candidates:
        if (candidate / "workspace" / "config.json").exists():
            return candidate
    return candidates[0]


def preflight(project_root: Path, delivery_id: str, story_id: str | None = None,
              slot: str | None = None) -> dict:
    root = project_root.resolve()
    errors = []
    if not (root / ".git").exists():
        errors.append("project root is not a Git worktree")
    try:
        anchor = main_worktree(root)
    except (RuntimeError, OSError) as exc:
        anchor = None
        errors.append(str(exc))
    refs = {}
    paths = {}
    try:
        refs = short_refs(delivery_id, story_id, slot)
    except ValueError as exc:
        errors.append(str(exc))
    if anchor is not None:
        paths = {key: str(path) for key, path in worktree_paths(anchor, delivery_id, story_id).items()}
    target = None
    if not errors:
        try:
            target = resolve_target_branch(root, "origin", recorded=recorded_target_branch(anchor, delivery_id))
        except TargetBranchUnresolved:
            target = None
        except RuntimeError as exc:
            errors.append(str(exc))
    return {"ok": not errors, "errors": sorted(set(errors)), "main_worktree": str(anchor) if anchor else None,
            "target_branch": target,
            "refs": refs, "worktrees": paths}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    names = sub.add_parser("names"); names.add_argument("--delivery", required=True); names.add_argument("--story"); names.add_argument("--slot"); names.set_defaults(func="names")
    check = sub.add_parser("preflight"); check.add_argument("--project-root", default="."); check.add_argument("--delivery", required=True); check.add_argument("--story"); check.add_argument("--slot"); check.set_defaults(func="preflight")
    reserve = sub.add_parser("reserve-delivery"); reserve.add_argument("--project-root", default="."); reserve.add_argument("--delivery", required=True); reserve.add_argument("--remote", default="origin"); reserve.set_defaults(func="reserve")
    governance = sub.add_parser("apply-governance"); governance.add_argument("--project-root", default="."); governance.add_argument("--dry-run", action="store_true"); governance.add_argument("--remote", default="origin"); governance.set_defaults(func="apply-governance")
    fence_upgrade = sub.add_parser("upgrade-fence-v1"); fence_upgrade.add_argument("--project-root", default="."); fence_upgrade.add_argument("--dry-run", action="store_true"); fence_upgrade.add_argument("--remote", default="origin"); fence_upgrade.set_defaults(func="upgrade-fence-v1")
    source_begin = sub.add_parser("begin-source-handoff"); source_begin.add_argument("--project-root", default="."); source_begin.add_argument("--source-hash", default="none"); source_begin.add_argument("--source-kind", choices=["requirement_supersession", "backlog_revision"], default="requirement_supersession"); source_begin.add_argument("--remote", default="origin"); source_begin.set_defaults(func="source-begin")
    source_auth = sub.add_parser("authorize-target-update"); source_auth.add_argument("--project-root", default="."); source_auth.add_argument("--mode", choices=["source_handoff", "governance", "upgrade"], default="source_handoff"); source_auth.add_argument("--candidate-hash", required=True); source_auth.add_argument("--carrier-kind", choices=["github_pr", "direct_target"], required=True); source_auth.add_argument("--carrier-ref", required=True); source_auth.add_argument("--carrier-object", required=True); source_auth.add_argument("--carrier-head", required=True); source_auth.add_argument("--carrier-base", required=True); source_auth.add_argument("--target-repository", required=True); source_auth.add_argument("--remote", default="origin"); source_auth.set_defaults(func="source-authorize")
    source_apply = sub.add_parser("apply-target-update"); source_apply.add_argument("--project-root", default="."); source_apply.add_argument("--mode", choices=["source_handoff", "governance", "upgrade"], default="source_handoff"); source_apply.add_argument("--remote", default="origin"); source_apply.set_defaults(func="source-apply")
    source_reauth = sub.add_parser("reauthorize-target-update"); source_reauth.add_argument("--project-root", default="."); source_reauth.add_argument("--mode", choices=["source_handoff", "governance", "upgrade"], default="source_handoff"); source_reauth.add_argument("--candidate-hash", default="none"); source_reauth.add_argument("--remote", default="origin"); source_reauth.set_defaults(func="source-reauthorize")
    source_finish = sub.add_parser("finish-source-handoff"); source_finish.add_argument("--project-root", default="."); source_finish.add_argument("--remote", default="origin"); source_finish.set_defaults(func="source-finish")
    source_abort = sub.add_parser("abort-source-handoff"); source_abort.add_argument("--project-root", default="."); source_abort.add_argument("--remote", default="origin"); source_abort.set_defaults(func="source-abort")
    publish = sub.add_parser("publish-execution-plan"); publish.add_argument("--project-root", default="."); publish.add_argument("--delivery", required=True); publish.add_argument("--remote", default="origin"); publish.set_defaults(func="publish")
    refresh = sub.add_parser("refresh-target"); refresh.add_argument("--project-root", default="."); refresh.add_argument("--delivery", required=True); refresh.add_argument("--remote", default="origin"); refresh.set_defaults(func="refresh")
    revise_scope = sub.add_parser("revise-unclaimed-scope"); revise_scope.add_argument("--project-root", default="."); revise_scope.add_argument("--delivery", required=True); revise_scope.add_argument("--remote", default="origin"); revise_scope.set_defaults(func="revise-scope")
    claim = sub.add_parser("claim-items"); claim.add_argument("--project-root", default="."); claim.add_argument("--delivery", required=True); claim.add_argument("--remote", default="origin"); claim.set_defaults(func="claim")
    plan_begin = sub.add_parser("begin-plan-revision"); plan_begin.add_argument("--project-root", default="."); plan_begin.add_argument("--delivery", required=True); plan_begin.add_argument("--remote", default="origin"); plan_begin.set_defaults(func="plan-begin")
    quiesce_delivery = sub.add_parser("quiesce-delivery"); quiesce_delivery.add_argument("--project-root", default="."); quiesce_delivery.add_argument("--delivery", required=True); quiesce_delivery.add_argument("--remote", default="origin"); quiesce_delivery.set_defaults(func="plan-begin")
    plan_finish = sub.add_parser("finish-plan-revision"); plan_finish.add_argument("--project-root", default="."); plan_finish.add_argument("--delivery", required=True); plan_finish.add_argument("--remote", default="origin"); plan_finish.set_defaults(func="plan-finish")
    plan_abort = sub.add_parser("abort-plan-revision"); plan_abort.add_argument("--project-root", default="."); plan_abort.add_argument("--delivery", required=True); plan_abort.add_argument("--remote", default="origin"); plan_abort.set_defaults(func="plan-abort")
    upgrade_begin = sub.add_parser("quiesce-upgrade"); upgrade_begin.add_argument("--project-root", default="."); upgrade_begin.add_argument("--delivery", required=True); upgrade_begin.add_argument("--remote", default="origin"); upgrade_begin.set_defaults(func="upgrade-begin")
    upgrade_merge = sub.add_parser("upgrade-target-merge"); upgrade_merge.add_argument("--project-root", default="."); upgrade_merge.add_argument("--delivery", required=True); upgrade_merge.add_argument("--remote", default="origin"); upgrade_merge.set_defaults(func="upgrade-merge")
    upgrade_finish = sub.add_parser("finish-upgrade"); upgrade_finish.add_argument("--project-root", default="."); upgrade_finish.add_argument("--delivery", required=True); upgrade_finish.add_argument("--remote", default="origin"); upgrade_finish.set_defaults(func="upgrade-finish")
    upgrade_abort = sub.add_parser("abort-upgrade"); upgrade_abort.add_argument("--project-root", default="."); upgrade_abort.add_argument("--delivery", required=True); upgrade_abort.add_argument("--remote", default="origin"); upgrade_abort.set_defaults(func="upgrade-abort")
    start = sub.add_parser("start-item"); start.add_argument("--project-root", default="."); start.add_argument("--delivery", required=True); start.add_argument("--story", required=True); start.add_argument("--remote", default="origin"); start.set_defaults(func="start")
    block = sub.add_parser("block-item"); block.add_argument("--project-root", default="."); block.add_argument("--delivery", required=True); block.add_argument("--story", required=True); block.add_argument("--remote", default="origin"); block.set_defaults(func="block")
    unblock = sub.add_parser("unblock-item"); unblock.add_argument("--project-root", default="."); unblock.add_argument("--delivery", required=True); unblock.add_argument("--story", required=True); unblock.add_argument("--remote", default="origin"); unblock.set_defaults(func="unblock")
    reopen = sub.add_parser("reopen-item"); reopen.add_argument("--project-root", default="."); reopen.add_argument("--delivery", required=True); reopen.add_argument("--story", required=True); reopen.add_argument("--remote", default="origin"); reopen.set_defaults(func="reopen")
    pause = sub.add_parser("pause-item"); pause.add_argument("--project-root", default="."); pause.add_argument("--delivery", required=True); pause.add_argument("--story", required=True); pause.add_argument("--remote", default="origin"); pause.set_defaults(func="pause")
    resume = sub.add_parser("resume-item"); resume.add_argument("--project-root", default="."); resume.add_argument("--delivery", required=True); resume.add_argument("--story", required=True); resume.add_argument("--remote", default="origin"); resume.set_defaults(func="resume")
    takeover = sub.add_parser("takeover-item"); takeover.add_argument("--project-root", default="."); takeover.add_argument("--delivery", required=True); takeover.add_argument("--story", required=True); takeover.add_argument("--remote", default="origin"); takeover.add_argument("--confirm", action="store_true"); takeover.set_defaults(func="takeover")
    provisional = sub.add_parser("provisional-claim"); provisional.add_argument("--project-root", default="."); provisional.add_argument("--delivery", required=True); provisional.add_argument("--story", required=True); provisional.add_argument("--path", action="append", required=True); provisional.add_argument("--remote", default="origin"); provisional.set_defaults(func="provisional-claim")
    withdraw = sub.add_parser("withdraw-provisional-claim"); withdraw.add_argument("--project-root", default="."); withdraw.add_argument("--delivery", required=True); withdraw.add_argument("--story", required=True); withdraw.add_argument("--remote", default="origin"); withdraw.set_defaults(func="withdraw-provisional-claim")
    lanes = sub.add_parser("lane-status"); lanes.add_argument("--project-root", default="."); lanes.add_argument("--delivery", required=True); lanes.add_argument("--story", required=True); lanes.add_argument("--remote", default="origin"); lanes.set_defaults(func="lane-status")
    push_item_parser = sub.add_parser("push-item"); push_item_parser.add_argument("--project-root", default="."); push_item_parser.add_argument("--delivery", required=True); push_item_parser.add_argument("--story", required=True); push_item_parser.add_argument("--remote", default="origin"); push_item_parser.set_defaults(func="push-item")
    integrate = sub.add_parser("integrate-item"); integrate.add_argument("--project-root", default="."); integrate.add_argument("--delivery", required=True); integrate.add_argument("--story", required=True); integrate.add_argument("--remote", default="origin"); integrate.set_defaults(func="integrate")
    publish_review = sub.add_parser("publish-delivery-review"); publish_review.add_argument("--project-root", default="."); publish_review.add_argument("--delivery", required=True); publish_review.add_argument("--remote", default="origin"); publish_review.set_defaults(func="publish-review")
    prepare_pr = sub.add_parser("prepare-pr-creation"); prepare_pr.add_argument("--project-root", default="."); prepare_pr.add_argument("--delivery", required=True); prepare_pr.add_argument("--remote", default="origin"); prepare_pr.set_defaults(func="prepare-pr")
    record_pr_parser = sub.add_parser("record-pr-remote"); record_pr_parser.add_argument("--project-root", default="."); record_pr_parser.add_argument("--delivery", required=True); record_pr_parser.add_argument("--url", required=True); record_pr_parser.add_argument("--remote", default="origin"); record_pr_parser.set_defaults(func="record-pr-remote")
    open_pr_parser = sub.add_parser("open-pr"); open_pr_parser.add_argument("--project-root", default="."); open_pr_parser.add_argument("--delivery", required=True); open_pr_parser.add_argument("--remote", default="origin"); open_pr_parser.set_defaults(func="open-pr")
    merge_pr_parser = sub.add_parser("merge-pr"); merge_pr_parser.add_argument("--project-root", default="."); merge_pr_parser.add_argument("--delivery", required=True); merge_pr_parser.add_argument("--remote", default="origin"); merge_pr_parser.set_defaults(func="merge-pr")
    invalidate_review = sub.add_parser("invalidate-delivery-review"); invalidate_review.add_argument("--project-root", default="."); invalidate_review.add_argument("--delivery", required=True); invalidate_review.add_argument("--finding-code", required=True); invalidate_review.add_argument("--finding-hash", required=True); invalidate_review.add_argument("--remote", default="origin"); invalidate_review.set_defaults(func="invalidate-review")
    cancel = sub.add_parser("cancel-delivery"); cancel.add_argument("--project-root", default="."); cancel.add_argument("--delivery", required=True); cancel.add_argument("--reason", required=True); cancel.add_argument("--remote", default="origin"); cancel.set_defaults(func="cancel")
    verify = sub.add_parser("verify-merge"); verify.add_argument("--project-root", default="."); verify.add_argument("--delivery", required=True); verify.add_argument("--remote", default="origin"); verify.set_defaults(func="verify-merge")
    reconcile = sub.add_parser("reconcile"); reconcile.add_argument("--project-root", default="."); reconcile.add_argument("--delivery", required=True); reconcile.add_argument("--remote", default="origin"); reconcile.set_defaults(func="reconcile")
    board = sub.add_parser("board"); board.add_argument("--project-root", default="."); board.add_argument("--delivery", required=True); board.add_argument("--remote", default="origin"); board.set_defaults(func="board")
    closure_audit = sub.add_parser("closure-audit"); closure_audit.add_argument("--project-root", default="."); closure_scope = closure_audit.add_mutually_exclusive_group(required=True); closure_scope.add_argument("--delivery"); closure_scope.add_argument("--all", action="store_true"); closure_audit.add_argument("--remote", default="origin"); closure_audit.set_defaults(func="closure-audit")
    closure_check = sub.add_parser("closure-check"); closure_check.add_argument("--project-root", default="."); closure_check.add_argument("--pr-url", required=True); closure_check.add_argument("--head", required=True); closure_check.add_argument("--head-ref", default=""); closure_check.add_argument("--base", required=True); closure_check.add_argument("--target", default=""); closure_check.add_argument("--remote", default="origin"); closure_check.set_defaults(func="closure-check")
    protection = sub.add_parser("protection-status"); protection.add_argument("--project-root", default="."); protection.add_argument("--branch"); protection.add_argument("--remote", default="origin"); protection.set_defaults(func="protection-status")
    locate = sub.add_parser("locate"); locate.add_argument("--delivery", required=True); locate.add_argument("--story"); locate.add_argument("--slot"); locate.set_defaults(func="names")
    args = parser.parse_args(argv)
    try:
        if args.func == "names":
            result = {"ok": True, "refs": short_refs(args.delivery, args.story, args.slot),
                      "full_refs": canonical_refs(args.delivery, args.story, args.slot)}
        else:
            if args.func == "reserve":
                result = reserve_delivery(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "apply-governance":
                result = apply_governance(Path(args.project_root), dry_run=args.dry_run, remote=args.remote)
            elif args.func == "upgrade-fence-v1":
                result = upgrade_fence_v1(Path(args.project_root), dry_run=args.dry_run, remote=args.remote)
            elif args.func == "source-begin":
                result = begin_source_handoff(Path(args.project_root), args.source_hash, args.remote, args.source_kind)
            elif args.func == "source-authorize":
                result = authorize_target_update(
                    Path(args.project_root), args.mode, args.candidate_hash, args.remote,
                    args.carrier_kind, args.carrier_ref, args.carrier_object,
                    args.carrier_head, args.carrier_base, args.target_repository,
                )
            elif args.func == "source-reauthorize":
                result = reauthorize_target_update(Path(args.project_root), args.mode, args.candidate_hash, args.remote)
            elif args.func == "source-apply":
                result = apply_target_update(Path(args.project_root), args.mode, args.remote)
            elif args.func == "source-finish":
                result = finish_source_handoff(Path(args.project_root), args.remote)
            elif args.func == "source-abort":
                result = abort_source_handoff(Path(args.project_root), args.remote)
            elif args.func == "publish":
                result = publish_execution_plan(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "refresh":
                result = refresh_target(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "revise-scope":
                result = revise_unclaimed_scope(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "claim":
                result = claim_items(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "plan-begin":
                result = begin_plan_revision(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "plan-finish":
                result = finish_plan_revision(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "plan-abort":
                result = abort_plan_revision(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "upgrade-begin":
                result = begin_upgrade(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "upgrade-merge":
                result = upgrade_target_merge(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "upgrade-finish":
                result = finish_upgrade(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "upgrade-abort":
                result = abort_upgrade(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "start":
                result = start_item(Path(args.project_root), args.delivery, args.story, args.remote)
            elif args.func == "block":
                result = block_item(Path(args.project_root), args.delivery, args.story, args.remote)
            elif args.func == "unblock":
                result = unblock_item(Path(args.project_root), args.delivery, args.story, args.remote)
            elif args.func == "reopen":
                result = reopen_item(Path(args.project_root), args.delivery, args.story, args.remote)
            elif args.func == "pause":
                result = pause_item(Path(args.project_root), args.delivery, args.story, args.remote)
            elif args.func == "resume":
                result = resume_item(Path(args.project_root), args.delivery, args.story, args.remote)
            elif args.func == "takeover":
                result = takeover_item(Path(args.project_root), args.delivery, args.story, args.remote, confirm=args.confirm)
            elif args.func == "provisional-claim":
                result = provisional_claim(Path(args.project_root), args.delivery, args.story, args.path, args.remote)
            elif args.func == "withdraw-provisional-claim":
                result = withdraw_provisional_claim(Path(args.project_root), args.delivery, args.story, args.remote)
            elif args.func == "lane-status":
                result = lane_status(Path(args.project_root), args.delivery, args.story, args.remote)
            elif args.func == "push-item":
                result = push_item(Path(args.project_root), args.delivery, args.story, args.remote)
            elif args.func == "integrate":
                result = integrate_item(Path(args.project_root), args.delivery, args.story, args.remote)
            elif args.func == "publish-review":
                result = publish_delivery_review(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "prepare-pr":
                result = prepare_pr_creation(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "record-pr-remote":
                result = record_pr_remote(Path(args.project_root), args.delivery, args.url, args.remote)
            elif args.func == "open-pr":
                result = open_pr(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "merge-pr":
                result = merge_pr(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "verify-merge":
                result = merge_pr(Path(args.project_root), args.delivery, args.remote, verify_only=True)
            elif args.func == "invalidate-review":
                result = invalidate_delivery_review(Path(args.project_root), args.delivery, args.finding_code, args.finding_hash, args.remote)
            elif args.func == "cancel":
                result = cancel_delivery(Path(args.project_root), args.delivery, args.reason, args.remote)
            elif args.func == "closure-audit":
                from delivery_closure import audit
                result = audit(Path(args.project_root), None if args.all else args.delivery, args.remote)
            elif args.func == "closure-check":
                from delivery_closure import check_pull_request
                result = check_pull_request(Path(args.project_root), head=args.head, base=args.base,
                                            url=args.pr_url, head_ref=args.head_ref, remote=args.remote,
                                            target=args.target)
            elif args.func == "protection-status":
                from delivery_closure import protection_status
                result = protection_status(Path(args.project_root), args.branch, args.remote)
            elif args.func == "reconcile":
                result = preflight(Path(args.project_root), args.delivery, None, None)
            elif args.func == "board":
                result = preflight(Path(args.project_root), args.delivery, None, None)
            else:
                result = preflight(Path(args.project_root), args.delivery, args.story, args.slot)
    except (ValueError, RuntimeError) as exc:
        result = {"ok": False, "errors": [str(exc)]}
    envelope = delivery_result.from_raw(args.command, result)
    delivery_result.write_line(json.dumps(envelope, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if envelope["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
