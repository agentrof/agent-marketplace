#!/usr/bin/env python3
"""Safe naming and read-only preflight for Delivery Git coordination.

Remote mutation verbs are intentionally added only after this layer is
validated. All subprocesses use argument arrays and never accept a user value
as a shell fragment.
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


DELIVERY_ID_RE = re.compile(r"^DLV-[0-9]{3,}$")
STORY_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-[0-9]{2,}$")
SLOT_RE = re.compile(r"^[0-9]{3,}$")
EPOCH_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")
OID_RE = re.compile(r"^[0-9a-f]{40,64}$")
RECEIPT_SCHEMA_VERSION = 1


def _fcntl_module():
    """Load the optional POSIX lock module without adding a runtime dependency."""
    try:
        return __import__("fcntl")
    except ImportError:  # pragma: no cover - Windows hosts use the adapter fallback.
        return None


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
        raise ValueError("slot must be a positive number rendered with at least three digits")
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
    module = _fcntl_module()
    try:
        if module is None:
            raise RuntimeError("receipt locking is unavailable on this host")
        module.flock(descriptor, module.LOCK_EX)
        yield
    finally:
        if module is not None:
            module.flock(descriptor, module.LOCK_UN)
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
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=receipt_path.parent,
                                     prefix=receipt_path.name + ".", delete=False) as temporary:
        temporary.write(data)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, receipt_path)
    _fsync_directory(receipt_path.parent)


def create_writer_receipt(main_worktree: Path, delivery_id: str, story_id: str,
                          slot: str, writer_epoch: str, item_ref: str,
                          slot_ref: str, candidate_oid: str) -> dict:
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
                raise RuntimeError("a different active writer receipt already exists")
            return existing
        _write_writer_receipt_locked(receipt_path, receipt)
    return _validate_receipt(json.loads(receipt_path.read_text(encoding="utf-8")))


def promote_writer_receipt(main_worktree: Path, delivery_id: str, story_id: str,
                           candidate_oid: str) -> dict:
    """Promote a pending receipt only after both remote refs equal its candidate."""
    receipt_path, lock_path = writer_receipt_paths(main_worktree, delivery_id, story_id)
    with receipt_lock(lock_path):
        if not receipt_path.exists():
            raise RuntimeError("pending writer receipt is missing")
        receipt = _validate_receipt(json.loads(receipt_path.read_text(encoding="utf-8")))
        if receipt["candidate_oid"] != candidate_oid:
            raise RuntimeError("writer receipt candidate does not match remote activation")
        if receipt["state"] == "verified":
            return receipt
        receipt["state"] = "verified"
        _write_writer_receipt_locked(receipt_path, receipt)
        return _validate_receipt(json.loads(receipt_path.read_text(encoding="utf-8")))


def discard_pending_writer_receipt(main_worktree: Path, delivery_id: str,
                                   story_id: str, candidate_oid: str) -> None:
    """Delete a pending receipt only after the remote CAS is conclusively rejected."""
    receipt_path, lock_path = writer_receipt_paths(main_worktree, delivery_id, story_id)
    with receipt_lock(lock_path):
        if not receipt_path.exists():
            return
        receipt = _validate_receipt(json.loads(receipt_path.read_text(encoding="utf-8")))
        if receipt["state"] != "pending" or receipt["candidate_oid"] != candidate_oid:
            raise RuntimeError("cannot discard a spent or different writer receipt")
        receipt_path.unlink()
        _fsync_directory(receipt_path.parent)


def materialize_item_worktree(main_worktree: Path, delivery_id: str, story_id: str,
                             candidate_oid: str) -> Path:
    """Create or verify the detached Item worktree after remote activation."""
    path = worktree_paths(main_worktree, delivery_id, story_id)["item"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            current = run_git(main_worktree, "-C", str(path), "rev-parse", "HEAD")
        except RuntimeError as exc:
            raise RuntimeError(f"Item worktree path is occupied: {path}") from exc
        if current != candidate_oid:
            raise RuntimeError(f"Item worktree is attached to a different OID: {path}")
        return path
    run_git(main_worktree, "worktree", "add", "--detach", str(path), candidate_oid)
    return path


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True,
                            capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def commit_tree(root: Path, base: str, paths: list[str], subject: str,
                trailers: dict[str, str]) -> str:
    """Create an unreferenced candidate tree from *base* plus exact paths."""
    with tempfile.TemporaryDirectory(prefix="agentrof-index-") as temporary:
        index = Path(temporary) / "index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        read = subprocess.run(["git", "read-tree", base], cwd=root, env=env,
                              text=True, capture_output=True, check=False)
        if read.returncode:
            raise RuntimeError(read.stderr.strip() or "cannot materialize candidate index")
        if paths:
            add = subprocess.run(["git", "add", "--", *paths], cwd=root, env=env,
                                 text=True, capture_output=True, check=False)
            if add.returncode:
                raise RuntimeError(add.stderr.strip() or "cannot stage candidate package")
        tree = subprocess.run(["git", "write-tree"], cwd=root, env=env,
                              text=True, capture_output=True, check=False)
        if tree.returncode:
            raise RuntimeError(tree.stderr.strip() or "cannot write candidate tree")
        message = subject + "\n\n" + "\n".join(
            f"Agentrof-{key}: {value}" for key, value in trailers.items()
        ) + "\n"
        commit = subprocess.run(["git", "commit-tree", tree.stdout.strip(), "-p", base],
                                cwd=root, env=env, input=message, text=True,
                                capture_output=True, check=False)
        if commit.returncode:
            raise RuntimeError(commit.stderr.strip() or "cannot create candidate commit")
        return commit.stdout.strip()


def commit_replacements(root: Path, base: str, replacements: dict[str, str],
                        subject: str, trailers: dict[str, str]) -> str:
    """Create a candidate from *base* with exact in-memory file replacements."""
    with tempfile.TemporaryDirectory(prefix="agentrof-index-") as temporary:
        index = Path(temporary) / "index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        read = subprocess.run(["git", "read-tree", base], cwd=root, env=env,
                              text=True, capture_output=True, check=False)
        if read.returncode:
            raise RuntimeError(read.stderr.strip() or "cannot materialize candidate index")
        for path, text in replacements.items():
            blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=root,
                                  env=env, input=text, text=True, capture_output=True, check=False)
            if blob.returncode:
                raise RuntimeError(blob.stderr.strip() or "cannot write candidate blob")
            update = subprocess.run(["git", "update-index", "--add", "--cacheinfo",
                                     "100644", blob.stdout.strip(), path], cwd=root, env=env,
                                    text=True, capture_output=True, check=False)
            if update.returncode:
                raise RuntimeError(update.stderr.strip() or "cannot stage candidate replacement")
        tree = subprocess.run(["git", "write-tree"], cwd=root, env=env,
                              text=True, capture_output=True, check=False)
        if tree.returncode:
            raise RuntimeError(tree.stderr.strip() or "cannot write candidate tree")
        message = subject + "\n\n" + "\n".join(
            f"Agentrof-{key}: {value}" for key, value in trailers.items()
        ) + "\n"
        commit = subprocess.run(["git", "commit-tree", tree.stdout.strip(), "-p", base],
                                cwd=root, env=env, input=message, text=True,
                                capture_output=True, check=False)
        if commit.returncode:
            raise RuntimeError(commit.stderr.strip() or "cannot create candidate commit")
        return commit.stdout.strip()


def epoch_token() -> str:
    return base64.urlsafe_b64encode(os.urandom(16)).decode("ascii").rstrip("=")


def remote_has_ref(root: Path, remote: str, ref: str) -> bool:
    output = run_git(root, "ls-remote", remote, ref)
    return bool(output.strip())


def remote_oid(root: Path, remote: str, ref: str) -> str:
    output = run_git(root, "ls-remote", remote, ref)
    if not output:
        raise RuntimeError(f"remote ref is absent: {ref}")
    return output.split()[0]


def commit_message(root: Path, oid: str) -> str:
    return run_git(root, "show", "-s", "--format=%B", oid)


def trailer(message: str, key: str) -> str | None:
    prefix = f"Agentrof-{key}:"
    for line in message.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def atomic_push(root: Path, remote: str, updates: list[tuple[str, str, str]]) -> None:
    args = ["push", "--atomic", remote]
    for ref, expected, candidate in updates:
        args.append(f"--force-with-lease={ref}:{expected}")
        args.append(f"{candidate}:{ref}")
    result = subprocess.run(["git", *args], cwd=root, text=True,
                            capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "atomic remote transaction rejected")


def resolve_target(root: Path, remote: str) -> tuple[str, str]:
    try:
        symbolic = run_git(root, "symbolic-ref", f"refs/remotes/{remote}/HEAD")
        branch = symbolic.removeprefix(f"refs/remotes/{remote}/")
    except RuntimeError:
        branch = run_git(root, "branch", "--show-current")
    if not branch:
        raise RuntimeError("target branch cannot be resolved")
    oid = run_git(root, "rev-parse", f"refs/remotes/{remote}/{branch}")
    return branch, oid


def reserve_delivery(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    """Reserve a ref-free scope-approved Delivery with an atomic two-ref push.

    This v1 slice intentionally handles only a completely ref-free project.
    Existing Fence/Delivery refs are classified as a coordinator handoff and
    rejected until the dedicated resume/retry protocol is implemented.
    """
    root = main_worktree(project_root.resolve())
    from delivery_compile import delivery_findings, docs_root, split_note
    docs = docs_root(root)
    directory, findings = delivery_findings(docs, delivery_id)
    if directory is None or findings:
        raise RuntimeError("Delivery package is not portable: " + "; ".join(findings))
    delivery_path_value = directory / "delivery.md"
    props, _ = split_note(delivery_path_value)
    if props.get("status") != "scope_approved":
        raise RuntimeError("reserve-delivery requires scope_approved")
    target_branch, target_oid = resolve_target(root, remote)
    refs = canonical_refs(delivery_id)
    if any(remote_has_ref(root, remote, ref) for ref in refs.values()):
        raise RuntimeError("reservation requires absent Fence and Integration refs")
    package = [str(path.relative_to(root)) for path in directory.rglob("*") if path.is_file()]
    map_path = docs / "maps" / "delivery.md"
    if map_path.exists():
        package.append(str(map_path.relative_to(root)))
    integration_oid = commit_tree(
        root, target_oid, sorted(set(package)),
        f"Reserve Delivery {delivery_id}",
        {"Record": "delivery-reservation-v1", "Protocol": "1", "Delivery": delivery_id,
         "Slug": directory.name.removeprefix(delivery_id.lower() + "-"), "Target": target_oid},
    )
    fence_oid = commit_tree(
        root, target_oid, [], f"Open Agentrof Fence for {delivery_id}",
        {"Record": "project-fence-v1", "Protocol": "1", "Mode": "open",
         "Epoch": epoch_token(), "Target": target_oid, "Config-Hash": "none"},
    )
    push_args = ["push", "--atomic", remote,
                 f"--force-with-lease={refs['fence']}:",
                 f"--force-with-lease={refs['integration']}:",
                 f"{fence_oid}:{refs['fence']}", f"{integration_oid}:{refs['integration']}"]
    result = subprocess.run(["git", *push_args], cwd=root, text=True,
                            capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "atomic Delivery reservation rejected")
    return {"ok": True, "delivery": delivery_id, "target_branch": target_branch,
            "target": target_oid, "fence": fence_oid, "integration": integration_oid,
            "refs": short_refs(delivery_id)}


def publish_execution_plan(project_root: Path, delivery_id: str,
                           remote: str = "origin") -> dict:
    root = main_worktree(project_root.resolve())
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
    if trailer(fence_message, "Record") != "project-fence-v1" or trailer(fence_message, "Mode") != "open":
        raise RuntimeError("publish-execution-plan requires an open Fence")
    package = [str(path.relative_to(root)) for path in directory.rglob("*") if path.is_file()]
    integration_candidate = commit_tree(
        root, integration_oid, sorted(package), f"Publish execution plan for {delivery_id}",
        {"Record": "execution-plan-published-v1", "Protocol": "1", "Delivery": delivery_id,
         "Scope-Hash": str(props.get("scope_hash", "none")),
         "Plan-Hash": str(props.get("plan_hash", "none")), "Target": trailer(fence_message, "Target") or "none"},
    )
    epoch = trailer(fence_message, "Epoch") or epoch_token()
    fence_candidate = commit_tree(
        root, fence_oid, [], f"Publish execution plan for {delivery_id}",
        {"Record": "project-fence-v1", "Protocol": "1", "Mode": "open", "Epoch": epoch,
         "Target": trailer(fence_message, "Target") or "none", "Config-Hash": trailer(fence_message, "Config-Hash") or "none"},
    )
    atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                               (refs["integration"], integration_oid, integration_candidate)])
    return {"ok": True, "delivery": delivery_id, "fence": fence_candidate,
            "integration": integration_candidate, "refs": short_refs(delivery_id)}


def claim_items(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    root = main_worktree(project_root.resolve())
    from delivery_compile import delivery_findings, docs_root, split_note, frontmatter, content_hash
    docs = docs_root(root)
    directory, findings = delivery_findings(docs, delivery_id)
    if directory is None or findings:
        raise RuntimeError("Delivery package is not portable: " + "; ".join(findings))
    delivery_props, _ = split_note(directory / "delivery.md")
    if delivery_props.get("status") != "execution_approved":
        raise RuntimeError("claim-items requires an execution-approved Delivery")
    refs = canonical_refs(delivery_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    fence_message = commit_message(root, fence_oid)
    if trailer(fence_message, "Record") != "project-fence-v1" or trailer(fence_message, "Mode") != "open":
        raise RuntimeError("claim-items requires an open Fence")
    marker = commit_tree(root, integration_oid, [], f"Establish claims for {delivery_id}",
                         {"Record": "claims-established-v1", "Protocol": "1", "Delivery": delivery_id,
                          "Scope-Hash": str(delivery_props.get("scope_hash", "none")),
                          "Plan-Hash": str(delivery_props.get("plan_hash", "none"))})
    updates = [(refs["fence"], fence_oid, commit_tree(
        root, fence_oid, [], f"Establish claims for {delivery_id}",
        {"Record": "project-fence-v1", "Protocol": "1", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": trailer(fence_message, "Target") or "none",
         "Config-Hash": trailer(fence_message, "Config-Hash") or "none"})),
               (refs["integration"], integration_oid, marker)]
    stories = []
    for item_path in sorted(directory.glob("items/*/item.md")):
        story = item_path.parent.name.upper()
        item_ref = canonical_refs(delivery_id, story)["item"]
        if remote_has_ref(root, remote, item_ref):
            raise RuntimeError(f"story is already claimed: {story}")
        item_props, item_body = split_note(item_path)
        item_props["integration_base_commit"] = marker
        item_props["source_hash"] = content_hash(item_props, item_body)
        relative = str(item_path.relative_to(root))
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


def remote_slot_oids(root: Path, remote: str) -> dict[str, str]:
    output = run_git(root, "ls-remote", remote, "refs/heads/agentrof/slots/*")
    result = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 2:
            result[parts[1].removeprefix("refs/heads/agentrof/slots/")] = parts[0]
    return result


def project_max_parallel(root: Path) -> int:
    config_path = root / "workspace" / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"project config cannot be read: {exc}")
    value = config.get("max_parallel")
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RuntimeError("max_parallel must be configured before Item activation")
    return value


def start_item(project_root: Path, delivery_id: str, story_id: str,
               remote: str = "origin") -> dict:
    root = main_worktree(project_root.resolve())
    from delivery_compile import docs_root, split_note, frontmatter, content_hash
    docs = docs_root(root)
    directory = find_delivery_dir_from_remote(root, remote, delivery_id)
    if directory is None:
        raise RuntimeError("local Delivery package is required for Item activation")
    refs = canonical_refs(delivery_id, story_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    item_oid = remote_oid(root, remote, refs["item"])
    fence_message = commit_message(root, fence_oid)
    if trailer(fence_message, "Record") != "project-fence-v1" or trailer(fence_message, "Mode") != "open":
        raise RuntimeError("start-item requires an open Fence")
    max_parallel = project_max_parallel(root)
    occupied = remote_slot_oids(root, remote)
    free = next((slot for slot in range(1, max_parallel + 1) if slot_key(slot) not in occupied), None)
    if free is None:
        raise RuntimeError("no global execution Slot is available")
    slot = slot_key(free)
    slot_ref = canonical_refs(delivery_id, story_id, slot)["slot"]
    item_path = directory / "items" / story_key(story_id) / "item.md"
    if not item_path.exists():
        raise RuntimeError(f"missing local Item projection: {item_path}")
    item_props, item_body = split_note(item_path)
    if item_props.get("status") not in {"in_scope", "paused", "blocked"}:
        raise RuntimeError("Item is not startable from its current status")
    writer = epoch_token()
    item_props["status"] = "active"
    item_props["tags"] = [tag for tag in item_props.get("tags", []) if not str(tag).startswith("status/")] + ["status/active"]
    item_props["source_hash"] = content_hash(item_props, item_body)
    item_candidate = commit_replacements(
        root, item_oid, {str(item_path.relative_to(root)): frontmatter(item_props, item_body)},
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
        {"Record": "project-fence-v1", "Protocol": "1", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": trailer(fence_message, "Target") or "none",
         "Config-Hash": trailer(fence_message, "Config-Hash") or "none"},
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
        observed_item = remote_oid(root, remote, refs["item"]) if remote_has_ref(root, remote, refs["item"]) else None
        observed_slot = remote_oid(root, remote, slot_ref) if remote_has_ref(root, remote, slot_ref) else None
        if observed_item is None and observed_slot is None:
            discard_pending_writer_receipt(root, delivery_id, story_id, item_candidate)
        elif observed_item == item_candidate and observed_slot == item_candidate:
            promote_writer_receipt(root, delivery_id, story_id, item_candidate)
        raise
    if remote_oid(root, remote, refs["item"]) != item_candidate or remote_oid(root, remote, slot_ref) != item_candidate:
        raise RuntimeError("activation refs did not converge to the receipt candidate")
    receipt = promote_writer_receipt(root, delivery_id, story_id, item_candidate)
    worktree = materialize_item_worktree(root, delivery_id, story_id, item_candidate)
    return {"ok": True, "delivery": delivery_id, "story": story_id, "slot": slot,
            "writer_epoch": writer, "item": item_candidate, "integration": integration_candidate,
            "fence": fence_candidate, "receipt": receipt, "worktree": str(worktree),
            "refs": short_refs(delivery_id, story_id, slot)}


def push_item(project_root: Path, delivery_id: str, story_id: str,
              remote: str = "origin") -> dict:
    root = main_worktree(project_root.resolve())
    from delivery_compile import docs_root, split_note, frontmatter, content_hash
    docs = docs_root(root)
    directory = find_delivery_dir_from_remote(root, remote, delivery_id)
    if directory is None:
        raise RuntimeError("local Delivery package is required for Item push")
    refs = canonical_refs(delivery_id, story_id)
    item_oid = remote_oid(root, remote, refs["item"])
    slots = remote_slot_oids(root, remote)
    slot = next((key for key, oid in slots.items() if oid == item_oid), None)
    if slot is None:
        raise RuntimeError("Item and Slot refs diverge; refuse active writer push")
    slot_ref = f"refs/heads/agentrof/slots/{slot}"; slot_oid = slots[slot]
    item_path = directory / "items" / story_key(story_id) / "item.md"
    if not item_path.exists():
        raise RuntimeError(f"missing local Item projection: {item_path}")
    item_props, item_body = split_note(item_path)
    if item_props.get("status") != "active":
        raise RuntimeError("push-item requires an active local Item projection")
    review = item_path.parent / "code-review.md"
    verification = item_path.parent / "verification.md"
    if not review.exists() or not verification.exists():
        raise RuntimeError("push-item requires Item review and verification files")
    review_props, review_body = split_note(review)
    verification_props, verification_body = split_note(verification)
    if review_props.get("status") != "approved" or verification_props.get("status") != "passed":
        raise RuntimeError("push-item requires approved code review and passed verification")
    item_props["source_hash"] = content_hash(item_props, item_body)
    replacements = {
        str(item_path.relative_to(root)): frontmatter(item_props, item_body),
        str(review.relative_to(root)): frontmatter(review_props, review_body),
        str(verification.relative_to(root)): frontmatter(verification_props, verification_body),
    }
    candidate = commit_replacements(root, item_oid, replacements, f"Update Item {story_id}", {})
    atomic_push(root, remote, [(refs["item"], item_oid, candidate), (slot_ref, slot_oid, candidate)])
    return {"ok": True, "delivery": delivery_id, "story": story_id, "item": candidate, "slot": candidate}


def integrate_item(project_root: Path, delivery_id: str, story_id: str,
                  remote: str = "origin") -> dict:
    root = main_worktree(project_root.resolve())
    from delivery_compile import docs_root, split_note, frontmatter, content_hash
    docs = docs_root(root)
    directory = find_delivery_dir_from_remote(root, remote, delivery_id)
    if directory is None:
        raise RuntimeError("local Delivery package is required for Item integration")
    refs = canonical_refs(delivery_id, story_id)
    integration_oid = remote_oid(root, remote, refs["integration"])
    item_oid = remote_oid(root, remote, refs["item"])
    slots = remote_slot_oids(root, remote)
    slot = next((key for key, oid in slots.items() if oid == item_oid), None)
    if slot is None:
        raise RuntimeError("Item and Slot refs diverge; refuse integration")
    slot_ref = f"refs/heads/agentrof/slots/{slot}"; slot_oid = slots[slot]
    item_path = directory / "items" / story_key(story_id) / "item.md"
    item_props, item_body = split_note(item_path)
    if item_props.get("status") != "active":
        raise RuntimeError("integrate-item requires an active Item")
    review = item_path.parent / "code-review.md"; verification = item_path.parent / "verification.md"
    review_props, _ = split_note(review); verification_props, _ = split_note(verification)
    if review_props.get("status") != "approved" or verification_props.get("status") != "passed":
        raise RuntimeError("integrate-item requires approved code review and passed verification")
    item_props["status"] = "integrated"
    item_props["tags"] = [tag for tag in item_props.get("tags", []) if not str(tag).startswith("status/")] + ["status/integrated"]
    item_props["integration_base_commit"] = integration_oid
    item_props["source_hash"] = content_hash(item_props, item_body)
    seal = commit_replacements(root, item_oid,
                               {str(item_path.relative_to(root)): frontmatter(item_props, item_body)},
                               f"Seal Item {story_id} for {delivery_id}",
                               {"Record": "item-integration-v1", "Protocol": "1", "Delivery": delivery_id,
                                "Story": story_id, "Item-Plan-Hash": str(item_props.get("item_plan_hash", "none")),
                                "Reviewed-Tip": item_oid, "Integration-Parent": integration_oid})
    integration_candidate = merge_candidate(root, integration_oid, seal,
                                            f"Integrate Item {story_id} for {delivery_id}",
                                            {"Record": "item-integration-v1", "Protocol": "1", "Delivery": delivery_id,
                                             "Story": story_id, "Item-Plan-Hash": str(item_props.get("item_plan_hash", "none")),
                                             "Reviewed-Tip": seal, "Integration-Parent": integration_oid})
    atomic_push(root, remote, [(refs["integration"], integration_oid, integration_candidate),
                               (refs["item"], item_oid, integration_candidate),
                               (slot_ref, slot_oid, "")])
    return {"ok": True, "delivery": delivery_id, "story": story_id,
            "integration": integration_candidate, "item": integration_candidate,
            "slot_released": slot_ref}


def merge_candidate(root: Path, first_parent: str, second_parent: str,
                    subject: str, trailers: dict[str, str]) -> str:
    with tempfile.TemporaryDirectory(prefix="agentrof-merge-index-") as temporary:
        index = Path(temporary) / "index"
        env = os.environ.copy(); env["GIT_INDEX_FILE"] = str(index)
        merge = subprocess.run(["git", "read-tree", "-m", first_parent, second_parent], cwd=root, env=env,
                               text=True, capture_output=True, check=False)
        if merge.returncode:
            raise RuntimeError(merge.stderr.strip() or "Item and Integration trees conflict")
        tree = subprocess.run(["git", "write-tree"], cwd=root, env=env, text=True,
                              capture_output=True, check=False)
        if tree.returncode:
            raise RuntimeError(tree.stderr.strip() or "cannot write integration tree")
        message = subject + "\n\n" + "\n".join(f"Agentrof-{key}: {value}" for key, value in trailers.items()) + "\n"
        commit = subprocess.run(["git", "commit-tree", tree.stdout.strip(), "-p", first_parent, "-p", second_parent],
                                cwd=root, env=env, input=message, text=True, capture_output=True, check=False)
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
            target = run_git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        except RuntimeError:
            try:
                target = run_git(root, "branch", "--show-current")
            except RuntimeError as exc:
                errors.append(str(exc))
    return {"ok": not errors, "errors": sorted(set(errors)), "main_worktree": str(anchor) if anchor else None,
            "target_branch": target.removeprefix("origin/") if target else None,
            "refs": refs, "worktrees": paths}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    names = sub.add_parser("names"); names.add_argument("--delivery", required=True); names.add_argument("--story"); names.add_argument("--slot"); names.set_defaults(func="names")
    check = sub.add_parser("preflight"); check.add_argument("--project-root", default="."); check.add_argument("--delivery", required=True); check.add_argument("--story"); check.add_argument("--slot"); check.set_defaults(func="preflight")
    reserve = sub.add_parser("reserve-delivery"); reserve.add_argument("--project-root", default="."); reserve.add_argument("--delivery", required=True); reserve.add_argument("--remote", default="origin"); reserve.set_defaults(func="reserve")
    publish = sub.add_parser("publish-execution-plan"); publish.add_argument("--project-root", default="."); publish.add_argument("--delivery", required=True); publish.add_argument("--remote", default="origin"); publish.set_defaults(func="publish")
    claim = sub.add_parser("claim-items"); claim.add_argument("--project-root", default="."); claim.add_argument("--delivery", required=True); claim.add_argument("--remote", default="origin"); claim.set_defaults(func="claim")
    start = sub.add_parser("start-item"); start.add_argument("--project-root", default="."); start.add_argument("--delivery", required=True); start.add_argument("--story", required=True); start.add_argument("--remote", default="origin"); start.set_defaults(func="start")
    push_item_parser = sub.add_parser("push-item"); push_item_parser.add_argument("--project-root", default="."); push_item_parser.add_argument("--delivery", required=True); push_item_parser.add_argument("--story", required=True); push_item_parser.add_argument("--remote", default="origin"); push_item_parser.set_defaults(func="push-item")
    integrate = sub.add_parser("integrate-item"); integrate.add_argument("--project-root", default="."); integrate.add_argument("--delivery", required=True); integrate.add_argument("--story", required=True); integrate.add_argument("--remote", default="origin"); integrate.set_defaults(func="integrate")
    args = parser.parse_args(argv)
    try:
        if args.func == "names":
            result = {"ok": True, "refs": short_refs(args.delivery, args.story, args.slot),
                      "full_refs": canonical_refs(args.delivery, args.story, args.slot)}
        else:
            if args.func == "reserve":
                result = reserve_delivery(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "publish":
                result = publish_execution_plan(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "claim":
                result = claim_items(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "start":
                result = start_item(Path(args.project_root), args.delivery, args.story, args.remote)
            elif args.func == "push-item":
                result = push_item(Path(args.project_root), args.delivery, args.story, args.remote)
            elif args.func == "integrate":
                result = integrate_item(Path(args.project_root), args.delivery, args.story, args.remote)
            else:
                result = preflight(Path(args.project_root), args.delivery, args.story, args.slot)
    except (ValueError, RuntimeError) as exc:
        result = {"ok": False, "errors": [str(exc)]}
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
