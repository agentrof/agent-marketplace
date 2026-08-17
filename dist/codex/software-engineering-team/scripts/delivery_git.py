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

import delivery_result


DELIVERY_ID_RE = re.compile(r"^DLV-[0-9]{3,}$")
STORY_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-[0-9]{2,}$")
SLOT_RE = re.compile(r"^[0-9]{3,}$")
EPOCH_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")
OID_RE = re.compile(r"^[0-9a-f]{40,64}$")
RECEIPT_SCHEMA_VERSION = 1
GITHUB_PR_RE = re.compile(r"^/([^/]+)/([^/]+)/pull/([1-9][0-9]*)$")


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
                    raise RuntimeError("takeover receipt does not match the previous writer tip")
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
                            intent_oid: str, attempt: str) -> dict:
    path, lock = provider_receipt_paths(main_worktree, delivery_id)
    if not OID_RE.fullmatch(intent_oid) or not EPOCH_RE.fullmatch(attempt):
        raise ValueError("provider receipt intent or attempt is invalid")
    value = {"schema_version": 1, "kind": "pr-create-v1", "state": "prepared",
             "delivery": delivery_id, "intent_oid": intent_oid, "provider": "github",
             "attempt": attempt, "url": "none"}
    with receipt_lock(lock):
        if path.exists():
            existing = _validate_provider_receipt(json.loads(path.read_text(encoding="utf-8")))
            if (existing["intent_oid"], existing["attempt"]) != (intent_oid, attempt):
                raise RuntimeError("a different provider receipt already exists")
            return existing
        return _validate_provider_receipt(_write_provider_receipt_locked(path, value))


def mark_provider_call_started(main_worktree: Path, delivery_id: str,
                               intent_oid: str, attempt: str) -> tuple[dict, bool]:
    path, lock = provider_receipt_paths(main_worktree, delivery_id)
    with receipt_lock(lock):
        receipt = _validate_provider_receipt(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else None
        if receipt is None or (receipt["intent_oid"], receipt["attempt"]) != (intent_oid, attempt):
            raise RuntimeError("provider receipt preimage is missing or stale")
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
            raise RuntimeError("provider receipt preimage is missing or stale")
        receipt["state"] = "verified"
        receipt["url"] = canonical_url
        return _validate_provider_receipt(_write_provider_receipt_locked(path, receipt))


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


def remove_item_worktree(main_worktree: Path, delivery_id: str, story_id: str) -> None:
    path = worktree_paths(main_worktree, delivery_id, story_id)["item"]
    if not path.exists():
        return
    run_git(main_worktree, "worktree", "remove", str(path))


def split_remote_note(root: Path, oid: str, relative_path: str,
                      split_note_fn) -> tuple[dict, str]:
    """Parse a tracked Markdown note from the exact remote Item tree."""
    text = run_git(root, "show", f"{oid}:{relative_path}")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False) as temporary:
        temporary.write(text)
        temporary_path = Path(temporary.name)
    try:
        return split_note_fn(temporary_path)
    finally:
        temporary_path.unlink(missing_ok=True)


def worktree_is_clean_and_at(root: Path, path: Path, expected_oid: str) -> None:
    if not path.is_dir():
        raise RuntimeError(f"Item worktree is missing: {path}")
    head = run_git(root, "-C", str(path), "rev-parse", "HEAD")
    if head != expected_oid:
        raise RuntimeError("Item worktree HEAD differs from the remote Item tip")
    dirty = run_git(root, "-C", str(path), "status", "--porcelain", "--untracked-files=all")
    if dirty:
        raise RuntimeError("DELIVERY_WORKTREE_UNSAFE: clean the Item worktree before pause")


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
    remote_line = run_git(root, "ls-remote", remote, f"refs/heads/{branch}")
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


def package_paths(root: Path, directory: Path, docs: Path,
                  include_items: bool = True) -> list[str]:
    paths = []
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        relative_to_delivery = path.relative_to(directory).parts
        if not include_items and relative_to_delivery and relative_to_delivery[0] == "items":
            continue
        paths.append(str(path.relative_to(root)))
    map_path = docs / "maps" / "delivery.md"
    if map_path.exists():
        paths.append(str(map_path.relative_to(root)))
    return sorted(set(paths))


def assert_integrated_items(root: Path, remote: str, directory: Path,
                            integration_oid: str, delivery_id: str) -> list[str]:
    from delivery_compile import split_note
    stories = []
    for item_path in sorted(directory.glob("items/*/item.md")):
        story = item_path.parent.name.upper()
        item_ref = canonical_refs(delivery_id, story)["item"]
        item_oid = remote_oid(root, remote, item_ref)
        relative = str(item_path.relative_to(root))
        item_props, _ = split_remote_note(root, item_oid, relative, split_note)
        if item_props.get("status") != "integrated":
            raise RuntimeError(f"Delivery Item is not integrated: {story}")
        try:
            run_git(root, "merge-base", "--is-ancestor", item_oid, integration_oid)
        except RuntimeError as exc:
            raise RuntimeError(f"Integration does not contain exact Item tip: {story}") from exc
        stories.append(story)
    if not stories:
        raise RuntimeError("Delivery Review requires at least one integrated Item")
    return stories


def publish_delivery_review(project_root: Path, delivery_id: str,
                            remote: str = "origin") -> dict:
    """Publish one approved Delivery Review as a real Integration child."""
    root = main_worktree(project_root.resolve())
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
    refs = canonical_refs(delivery_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    fence_message = commit_message(root, fence_oid)
    if trailer(fence_message, "Record") != "project-fence-v1" or trailer(fence_message, "Mode") != "open":
        raise RuntimeError("publish-delivery-review requires an open Fence")
    stories = assert_integrated_items(root, remote, directory, integration_oid, delivery_id)
    reviewed_parent = str(review_props.get("reviewed_integration_commit", "none"))
    if reviewed_parent != integration_oid:
        raise RuntimeError("Delivery Review reviewed_integration_commit is stale")
    candidate = commit_tree(
        root, integration_oid, package_paths(root, directory, docs, include_items=False),
        f"Publish delivery review for {delivery_id}",
        {"Record": "delivery-review-published-v1", "Protocol": "1", "Delivery": delivery_id,
         "Reviewed-Integration": integration_oid, "Approval-Hash": str(review_props.get("approval_hash", "none")),
         "Target": trailer(fence_message, "Target") or "none", "Cancellation-Intent-Hash": "none"},
    )
    fence_candidate = commit_tree(
        root, fence_oid, [], "Fence project in open mode",
        {"Record": "project-fence-v1", "Protocol": "1", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": trailer(fence_message, "Target") or "none",
         "Config-Hash": trailer(fence_message, "Config-Hash") or "none"},
    )
    atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                               (refs["integration"], integration_oid, candidate)])
    return {"ok": True, "delivery": delivery_id, "integration": candidate,
            "fence": fence_candidate, "stories": stories, "refs": short_refs(delivery_id)}


def prepare_pr_creation(project_root: Path, delivery_id: str,
                        remote: str = "origin") -> dict:
    """Publish the durable PR-create intent; this function never calls a provider."""
    root = main_worktree(project_root.resolve())
    refs = canonical_refs(delivery_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    fence_message = commit_message(root, fence_oid)
    integration_message = commit_message(root, integration_oid)
    if trailer(integration_message, "Record") != "delivery-review-published-v1":
        raise RuntimeError("PR creation requires a published Delivery Review")
    if trailer(fence_message, "Mode") != "open":
        raise RuntimeError("PR creation requires an open Fence")
    attempt = epoch_token()
    target = trailer(fence_message, "Target") or "none"
    intent = commit_tree(
        root, integration_oid, [], f"Prepare PR creation for {delivery_id}",
        {"Record": "pr-creation-intent-v1", "Protocol": "1", "Delivery": delivery_id,
         "Review-Head": integration_oid, "Target": target, "Provider": "github", "Attempt": attempt},
    )
    fence_candidate = commit_tree(
        root, fence_oid, [], "Fence project in open mode",
        {"Record": "project-fence-v1", "Protocol": "1", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": target, "Config-Hash": trailer(fence_message, "Config-Hash") or "none"},
    )
    atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                               (refs["integration"], integration_oid, intent)])
    return {"ok": True, "delivery": delivery_id, "intent": intent,
            "attempt": attempt, "provider": "github", "refs": short_refs(delivery_id)}


def record_pr_remote(project_root: Path, delivery_id: str, url: str,
                     remote: str = "origin") -> dict:
    """Record a provider-verified PR URL as the exact intent child."""
    root = main_worktree(project_root.resolve())
    from delivery_compile import docs_root, find_delivery, split_note, frontmatter, content_hash
    canonical_url, number = canonical_github_pr(url)
    docs = docs_root(root)
    directory = find_delivery(docs, delivery_id)
    if directory is None:
        raise RuntimeError("Delivery package not found")
    review_path = directory / "delivery-review.md"
    review_props, review_body = split_note(review_path)
    if review_props.get("pull_request_url") != canonical_url:
        raise RuntimeError("local Delivery Review URL does not match the requested PR")
    refs = canonical_refs(delivery_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    intent_message = commit_message(root, integration_oid)
    if trailer(intent_message, "Record") not in {"pr-creation-intent-v1", "pr-adoption-intent-v1"}:
        raise RuntimeError("record-pr requires the exact unmatched PR intent")
    review_props["pull_request_url"] = canonical_url
    review_props["source_hash"] = content_hash(review_props, review_body, exclude={"status", "approved_at_utc", "source_hash", "approval_hash"})
    relative_review = str(review_path.relative_to(root))
    candidate = commit_replacements(
        root, integration_oid, {relative_review: frontmatter(review_props, review_body)},
        f"Record PR for {delivery_id}",
        {"Record": "pr-url-recorded-v1", "Protocol": "1", "Delivery": delivery_id,
         "Intent": integration_oid, "Provider": "github", "Pull-Request": number,
         "URL-Hash": "sha256:" + hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()},
    )
    fence_message = commit_message(root, fence_oid)
    fence_candidate = commit_tree(
        root, fence_oid, [], "Fence project in open mode",
        {"Record": "project-fence-v1", "Protocol": "1", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": trailer(fence_message, "Target") or "none",
         "Config-Hash": trailer(fence_message, "Config-Hash") or "none"},
    )
    atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                               (refs["integration"], integration_oid, candidate)])
    return {"ok": True, "delivery": delivery_id, "integration": candidate,
            "fence": fence_candidate, "pull_request_url": canonical_url,
            "pull_request": number, "refs": short_refs(delivery_id)}


def open_pr(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    """Create or resume exactly one GitHub draft PR after a durable intent."""
    root = main_worktree(project_root.resolve())
    from delivery_compile import docs_root, find_delivery, split_note, record_pr
    from delivery_provider import GitHubProvider, ProviderError
    docs = docs_root(root)
    directory = find_delivery(docs, delivery_id)
    if directory is None:
        raise RuntimeError("Delivery package not found")
    delivery_props, _ = split_note(directory / "delivery.md")
    review_path = directory / "delivery-review.md"
    review_props, review_body = split_note(review_path)
    refs = canonical_refs(delivery_id)
    integration_oid = remote_oid(root, remote, refs["integration"])
    integration_message = commit_message(root, integration_oid)
    record_name = trailer(integration_message, "Record")
    if record_name == "pr-url-recorded-v1":
        url = str(review_props.get("pull_request_url", ""))
        canonical_url, _ = canonical_github_pr(url)
        return {"ok": True, "delivery": delivery_id, "pull_request_url": canonical_url,
                "reused": True, "provider_call": False}
    adoption = False
    provider = GitHubProvider(root, remote)
    target_branch, _ = resolve_target(root, remote)
    head = short_refs(delivery_id)["integration"]
    if record_name == "delivery-review-published-v1":
        existing = provider.exact_unmerged(head, target_branch)
        if len(existing) != 1:
            raise RuntimeError("external PR adoption requires exactly one unmerged exact PR")
        pr = existing[0]
        if str(pr.get("state", "")).upper() != "OPEN":
            raise RuntimeError("external closed-unmerged PR requires explicit provider reopen before adoption")
        if pr.get("headRefName") != head or pr.get("baseRefName") != target_branch:
            raise RuntimeError("external PR head/base does not match the Delivery")
        if pr.get("headRefOid") and pr.get("headRefOid") != integration_oid:
            raise RuntimeError("external PR head does not match the reviewed Integration")
        url = pr.get("url")
        if not isinstance(url, str):
            raise ProviderError("external PR has no canonical URL")
        canonical_url, number = canonical_github_pr(url)
        if not pr.get("isDraft"):
            provider.ensure_draft(canonical_url)
        fence_oid = remote_oid(root, remote, refs["fence"])
        fence_message = commit_message(root, fence_oid)
        adoption_intent = commit_tree(
            root, integration_oid, [], f"Adopt PR for {delivery_id}",
            {"Record": "pr-adoption-intent-v1", "Protocol": "1", "Delivery": delivery_id,
             "Review-Head": integration_oid, "Target": trailer(fence_message, "Target") or "none",
             "Provider": "github", "Pull-Request": number,
             "URL-Hash": "sha256:" + hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()},
        )
        fence_candidate = commit_tree(
            root, fence_oid, [], "Fence project in open mode",
            {"Record": "project-fence-v1", "Protocol": "1", "Mode": "open",
             "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
             "Target": trailer(fence_message, "Target") or "none",
             "Config-Hash": trailer(fence_message, "Config-Hash") or "none"},
        )
        atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                                   (refs["integration"], integration_oid, adoption_intent)])
        integration_oid = adoption_intent
        integration_message = commit_message(root, integration_oid)
        record_name = "pr-adoption-intent-v1"
        adoption = True
    if record_name != "pr-creation-intent-v1":
        if record_name != "pr-adoption-intent-v1":
            raise RuntimeError("open-pr requires an unmatched PR creation or adoption intent")
    attempt = trailer(integration_message, "Attempt")
    if not adoption and not attempt:
        raise RuntimeError("PR creation intent has no Attempt")
    if adoption:
        # The provider was already normalized to draft and the exact URL is
        # carried by the adoption intent. No create receipt or provider POST
        # is permitted on this path.
        canonical_url, _ = canonical_github_pr(url)
        record_pr(type("Args", (), {"docs": str(docs), "delivery": delivery_id, "url": canonical_url}))
        recorded = record_pr_remote(root, delivery_id, canonical_url, remote)
        return {"ok": True, "delivery": delivery_id, "pull_request_url": canonical_url,
                "provider_call": False, "adopted": True,
                "integration": recorded["integration"], "fence": recorded["fence"],
                "refs": short_refs(delivery_id)}
    existing = provider.exact_unmerged(head, target_branch)
    if len(existing) > 1:
        raise RuntimeError("multiple exact unmerged Delivery PRs exist")
    receipt = create_provider_receipt(root, delivery_id, integration_oid, attempt)
    if receipt["state"] == "verified" and receipt.get("url") not in {None, "none"}:
        return {"ok": True, "delivery": delivery_id, "pull_request_url": receipt["url"],
                "reused": True, "provider_call": False}
    if existing:
        pr = existing[0]
        if str(pr.get("state", "")).upper() != "OPEN":
            raise RuntimeError("exact Delivery PR is closed without merge; manual reopen is required")
        url = pr.get("url")
        if not isinstance(url, str):
            raise ProviderError("GitHub exact PR has no URL")
        provider.ensure_draft(url)
        provider_call = False
    else:
        receipt, elected = mark_provider_call_started(root, delivery_id, integration_oid, attempt)
        if not elected:
            raise RuntimeError("DELIVERY_PR_UNCERTAIN: another process owns the provider call")
        title_value = str(delivery_props.get("goal", delivery_id))
        created = provider.create_draft(head, target_branch, title_value, review_body)
        url = created["url"]
        provider_call = True
    canonical_url, _ = canonical_github_pr(url)
    record_pr(type("Args", (), {"docs": str(docs), "delivery": delivery_id, "url": canonical_url}))
    recorded = record_pr_remote(root, delivery_id, canonical_url, remote)
    mark_provider_verified(root, delivery_id, integration_oid, attempt, canonical_url)
    return {"ok": True, "delivery": delivery_id, "pull_request_url": canonical_url,
            "provider_call": provider_call, "integration": recorded["integration"],
            "fence": recorded["fence"], "refs": short_refs(delivery_id)}


def merge_pr(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    """Merge the one reviewed PR with exact head/base evidence.

    Provider mutation is followed by a fresh all-state query and target
    ancestry proof. A ready/squash/rebase/admin result or missing merge object
    is never interpreted as successful closure.
    """
    root = main_worktree(project_root.resolve())
    from delivery_compile import docs_root, find_delivery, split_note
    from delivery_provider import GitHubProvider, ProviderError
    docs = docs_root(root)
    directory = find_delivery(docs, delivery_id)
    if directory is None:
        raise RuntimeError("Delivery package not found")
    review_props, _ = split_note(directory / "delivery-review.md")
    url = str(review_props.get("pull_request_url", ""))
    canonical_url, _ = canonical_github_pr(url)
    refs = canonical_refs(delivery_id)
    integration_oid = remote_oid(root, remote, refs["integration"])
    integration_message = commit_message(root, integration_oid)
    if trailer(integration_message, "Record") != "pr-url-recorded-v1":
        raise RuntimeError("merge-pr requires the current recorded Delivery PR")
    target_branch, target_before = resolve_target(root, remote)
    provider = GitHubProvider(root, remote)
    candidates = [item for item in provider.list_pull_requests(short_refs(delivery_id)["integration"], target_branch)
                  if str(item.get("url", "")) == canonical_url]
    if len(candidates) != 1:
        raise ProviderError("exactly one lifecycle PR is required")
    pr = candidates[0]
    if str(pr.get("state", "")).upper() == "MERGED":
        merged = pr
    else:
        if str(pr.get("state", "")).upper() != "OPEN" or pr.get("headRefName") != short_refs(delivery_id)["integration"] or pr.get("baseRefName") != target_branch:
            raise ProviderError("Delivery PR head/base/state is not mergeable")
        if pr.get("isDraft"):
            provider.make_ready(canonical_url)
        head_now = remote_oid(root, remote, refs["integration"])
        if head_now != integration_oid:
            raise RuntimeError("Integration advanced after PR review; re-run Delivery Review")
        provider.merge_commit(canonical_url, integration_oid)
        refreshed = [item for item in provider.list_pull_requests(short_refs(delivery_id)["integration"], target_branch)
                     if str(item.get("url", "")) == canonical_url]
        if len(refreshed) != 1:
            raise ProviderError("merged PR cannot be reconstructed")
        merged = refreshed[0]
    if str(merged.get("state", "")).upper() != "MERGED":
        raise ProviderError("provider PR is not merged")
    merge_value = merged.get("mergeCommit")
    merge_oid = merge_value.get("oid") if isinstance(merge_value, dict) else merge_value
    if not isinstance(merge_oid, str) or not OID_RE.fullmatch(merge_oid):
        raise ProviderError("provider did not return an exact merge commit")
    target_after = resolve_target(root, remote)[1]
    run_git(root, "fetch", "--no-tags", remote, f"refs/heads/{target_branch}:refs/remotes/{remote}/{target_branch}")
    try:
        run_git(root, "merge-base", "--is-ancestor", merge_oid, target_after)
        run_git(root, "merge-base", "--is-ancestor", integration_oid, target_after)
    except RuntimeError as exc:
        raise RuntimeError("provider merge is not present in the exact target ancestry") from exc
    return {"ok": True, "delivery": delivery_id, "status": "merged",
            "pull_request_url": canonical_url, "merge_commit": merge_oid,
            "target_before": target_before, "target_after": target_after,
            "reviewed_integration": integration_oid, "refs": short_refs(delivery_id)}


def invalidate_delivery_review(project_root: Path, delivery_id: str,
                               finding_code: str, finding_hash: str,
                               remote: str = "origin") -> dict:
    """Persist an evidence/review-only change request on the Integration ref."""
    if not re.fullmatch(r"[A-Z][A-Z0-9_.-]{2,63}", finding_code):
        raise ValueError("finding code must be an uppercase stable code")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", finding_hash):
        raise ValueError("finding hash must be a canonical sha256 digest")
    root = main_worktree(project_root.resolve())
    from delivery_compile import docs_root, find_delivery, split_note, frontmatter, content_hash
    docs = docs_root(root)
    directory = find_delivery(docs, delivery_id)
    if directory is None:
        raise RuntimeError("Delivery package not found")
    refs = canonical_refs(delivery_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    fence_message = commit_message(root, fence_oid)
    integration_message = commit_message(root, integration_oid)
    if trailer(fence_message, "Mode") != "open":
        raise RuntimeError("review invalidation requires an open Fence")
    if trailer(integration_message, "Record") not in {"delivery-review-published-v1", "pr-url-recorded-v1"}:
        raise RuntimeError("review invalidation requires a published current Review")
    review_path = directory / "delivery-review.md"
    relative_review = str(review_path.relative_to(root))
    review_props, review_body = split_remote_note(root, integration_oid, relative_review, split_note)
    if review_props.get("status") != "approved":
        raise RuntimeError("current Delivery Review is not approved")
    review_props["status"] = "changes_requested"
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
        {"Record": "project-fence-v1", "Protocol": "1", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": trailer(fence_message, "Target") or "none",
         "Config-Hash": trailer(fence_message, "Config-Hash") or "none"},
    )
    atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                               (refs["integration"], integration_oid, candidate)])
    return {"ok": True, "delivery": delivery_id, "status": "changes_requested",
            "finding_code": finding_code, "finding_hash": finding_hash,
            "integration": candidate, "fence": fence_candidate, "refs": short_refs(delivery_id)}


def cancellation_projection(delivery_id: str, scope_hash: str, reason: str,
                             stories: dict[str, dict[str, str]], target: str) -> tuple[dict, str]:
    if not reason.strip() or not OID_RE.fullmatch(target):
        raise ValueError("cancellation reason and exact target OID are required")
    normalized = {}
    for story, value in sorted(stories.items()):
        validate_story_id(story)
        if set(value) != {"disposition", "tip"}:
            raise ValueError("cancellation story projection has unexpected keys")
        disposition = str(value["disposition"])
        tip = str(value["tip"])
        if disposition == "not_started":
            if tip != "none":
                raise ValueError("not_started cancellation stories must use tip none")
        elif disposition in {"integrated_reverted", "unintegrated_discarded"}:
            if not OID_RE.fullmatch(tip):
                raise ValueError("executed cancellation stories require an exact previous Item tip")
        else:
            raise ValueError("unsupported cancellation disposition")
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
        raise ValueError("cancellation barrier epoch is invalid")
    final_stories = {}
    for story, value in sorted(stories.items()):
        validate_story_id(story)
        if set(value) != {"disposition", "tip"}:
            raise ValueError("cancellation finalization story projection has unexpected keys")
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
        raise RuntimeError(f"integrated Item tip is not a merge commit: {merge_oid}")
    first_parent = parents[0]
    with tempfile.TemporaryDirectory(prefix="agentrof-revert-index-") as temporary:
        index = Path(temporary) / "index"
        env = os.environ.copy()
        env["GIT_INDEX_FILE"] = str(index)
        read = subprocess.run(["git", "read-tree", base], cwd=root, env=env,
                              text=True, capture_output=True, check=False)
        if read.returncode:
            raise RuntimeError(read.stderr.strip() or "cannot prepare cancellation revert index")

        def entry(tree: str, path: str) -> tuple[str, str] | None:
            result = subprocess.run(
                ["git", "ls-tree", tree, "--", path], cwd=root,
                text=True, capture_output=True, check=False,
            )
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or "cannot inspect cancellation tree")
            line = result.stdout.rstrip("\n")
            if not line:
                return None
            metadata, _name = line.split("\t", 1)
            mode, _kind, oid = metadata.split()
            return mode, oid

        changed = subprocess.run(
            ["git", "diff", "--name-only", first_parent, merge_oid], cwd=root,
            text=True, capture_output=True, check=False,
        )
        if changed.returncode:
            raise RuntimeError(changed.stderr.strip() or "cannot inspect Item merge")
        for path in (value for value in changed.stdout.splitlines() if value):
            parent_entry = entry(first_parent, path)
            merge_entry = entry(merge_oid, path)
            current_entry = entry(base, path)
            if current_entry != merge_entry and current_entry != parent_entry:
                raise RuntimeError(
                    "cancellation revert conflicts with current Integration: "
                    f"{path} (base={current_entry}, merge={merge_entry}, "
                    f"parent={parent_entry})"
                )
            if parent_entry is None:
                update = subprocess.run(
                    ["git", "update-index", "--remove", "--", path],
                    cwd=root, env=env, text=True, capture_output=True, check=False,
                )
            else:
                mode, oid = parent_entry
                update = subprocess.run(
                    ["git", "update-index", "--add", "--cacheinfo",
                     f"{mode},{oid},{path}"],
                    cwd=root, env=env, text=True, capture_output=True, check=False,
                )
            if update.returncode:
                raise RuntimeError(update.stderr.strip() or f"cannot apply cancellation revert: {path}")
        tree = subprocess.run(["git", "write-tree"], cwd=root, env=env,
                              text=True, capture_output=True, check=False)
        if tree.returncode:
            raise RuntimeError(tree.stderr.strip() or "cannot write cancellation revert tree")
        message = subject + "\n\n" + "\n".join(
            f"Agentrof-{key}: {value}" for key, value in trailers.items()
        ) + "\n"
        commit = subprocess.run(
            ["git", "commit-tree", tree.stdout.strip(), "-p", base],
            cwd=root, env=env, input=message, text=True,
            capture_output=True, check=False,
        )
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
    from delivery_compile import docs_root, find_delivery, split_note, frontmatter, body_for, title, designation, content_hash
    docs = docs_root(root)
    directory = find_delivery(docs, delivery_id)
    if directory is None:
        raise RuntimeError("Delivery package not found")
    delivery_path_value = directory / "delivery.md"
    local_props, _ = split_note(delivery_path_value)
    if local_props.get("status") in {"draft", "cancelled", "target_merged"}:
        raise RuntimeError("cancel-delivery requires a nonterminal approved Delivery")
    refs = canonical_refs(delivery_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    fence_message = commit_message(root, fence_oid)
    integration_message = commit_message(root, integration_oid)
    if trailer(fence_message, "Mode") != "open" or trailer(integration_message, "Record") in {
            "cancellation-intent-v1", "delivery-barrier-v1", "cancellation-finalized-v1"}:
        raise RuntimeError("Delivery already has a barrier or non-open Fence")

    relative_delivery = str(delivery_path_value.relative_to(root))
    remote_props, remote_body = split_remote_note(root, integration_oid, relative_delivery, split_note)
    scope_hash = str(remote_props.get("scope_hash", "none"))
    all_slots = remote_slot_oids(root, remote)
    contexts: dict[str, dict] = {}
    stories: dict[str, dict[str, str]] = {}
    for item_path in sorted(directory.glob("items/*/item.md")):
        story = item_path.parent.name.upper()
        item_ref = canonical_refs(delivery_id, story)["item"]
        relative_item = str(item_path.relative_to(root))
        context = {"path": relative_item, "item_ref": item_ref, "item_oid": None,
                   "slot": None, "props": None, "body": None}
        if remote_has_ref(root, remote, item_ref):
            item_oid = remote_oid(root, remote, item_ref)
            item_props, item_body = split_remote_note(root, item_oid, relative_item, split_note)
            if item_props.get("status") == "cancelled":
                raise RuntimeError(f"Item is already cancelled: {story}")
            disposition = "integrated_reverted" if item_props.get("status") == "integrated" else "unintegrated_discarded"
            context.update({"item_oid": item_oid, "slot": next((key for key, oid in all_slots.items() if oid == item_oid), None),
                            "props": item_props, "body": item_body})
            stories[story] = {"disposition": disposition, "tip": item_oid}
        else:
            stories[story] = {"disposition": "not_started", "tip": "none"}
        contexts[story] = context
    target = trailer(fence_message, "Target") or resolve_target(root, remote)[1]
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

    first_parent_history = run_git(
        root, "rev-list", "--first-parent", integration_oid
    ).splitlines()
    first_parent_order = {
        oid: index for index, oid in enumerate(first_parent_history)
    }
    integrated = []
    for story, context in contexts.items():
        if not context["item_oid"] or context["props"].get("status") != "integrated":
            continue
        merge_oid = None
        for candidate_oid in first_parent_history:
            message = commit_message(root, candidate_oid)
            if (trailer(message, "Record") == "item-integration-v1"
                    and trailer(message, "Story") == story):
                merge_oid = candidate_oid
                break
        if merge_oid is None:
            raise RuntimeError(
                f"Integration history has no exact Item merge for {story}"
            )
        integrated.append((story, merge_oid))
    integrated.sort(key=lambda pair: first_parent_order[pair[1]])
    current = barrier
    revert_commits = []
    for story, item_oid in integrated:
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

    review_props = {
        "type": "delivery-review", "id": f"{delivery_id}-REVIEW",
        "title": title(remote_props.get("goal", delivery_id), designation(docs, "delivery-review", "delivery review")),
        "status": "approved", "derives_from": [f"[[{delivery_path_value.relative_to(docs).with_suffix('')}|{delivery_id}]]"],
        "scope_hash": scope_hash, "cancellation_intent_hash": intent_hash,
        "cancellation_projection_hash": projection_hash, "reviewed_integration_commit": finalization,
        "approved_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "tags": ["doc/delivery-review", "status/approved"],
    }
    review_body = body_for("delivery-review", review_props["title"], {
        "Goal Outcome": remote_props.get("goal", ""),
        "Verdict": "Cancellation approved and finalized with exact Item dispositions.",
        "Cancellation": reason.strip(), "Cancellation Projection": _canonical_json(projection),
        "Navigation": f"[[{delivery_path_value.relative_to(docs).with_suffix('')}|{delivery_id}]]",
    })
    review_props["approval_hash"] = content_hash(review_props, review_body, exclude={"status", "approved_at_utc", "source_hash", "approval_hash"})
    review_props["source_hash"] = content_hash(review_props, review_body)
    relative_review = str((directory / "delivery-review.md").relative_to(root))
    review = commit_replacements(
        root, finalization, {relative_review: frontmatter(review_props, review_body)},
        f"Publish delivery review for {delivery_id}",
        {"Record": "delivery-review-published-v1", "Protocol": "1", "Delivery": delivery_id,
         "Reviewed-Integration": finalization, "Approval-Hash": review_props["approval_hash"],
         "Target": target, "Cancellation-Intent-Hash": intent_hash,
         "Cancellation-Projection-Hash": projection_hash},
    )
    fence_candidate = commit_tree(
        root, fence_oid, [], "Fence project in open mode",
        {"Record": "project-fence-v1", "Protocol": "1", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(), "Target": target,
         "Config-Hash": trailer(fence_message, "Config-Hash") or "none"},
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


def cancel_scope_delivery(project_root: Path, delivery_id: str, reason: str,
                          remote: str = "origin") -> dict:
    """Backward-compatible alias for the public cancellation coordinator."""
    return cancel_delivery(project_root, delivery_id, reason, remote)


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


def target_impact_hash(delivery_id: str, previous_target: str, target: str,
                       items: dict[str, dict], previous_plan_hash: str = "none",
                       barrier_epoch: str = "none") -> str:
    """Return the canonical target-impact digest for a nonempty mapping."""
    if not OID_RE.fullmatch(previous_target) or not OID_RE.fullmatch(target):
        raise ValueError("target impact requires exact previous and current target OIDs")
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
    result = subprocess.run(
        ["git", "diff", "--name-only", previous_target, target], cwd=root,
        text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "cannot inspect target drift")
    return sorted(path for path in result.stdout.splitlines() if path)


def refresh_target(project_root: Path, delivery_id: str,
                   remote: str = "origin") -> dict:
    """Merge a fresh target tip into one open Delivery under the Fence lease.

    Disjoint target movement is fully supported. A path/contract overlap with
    an already claimed Item is rejected before any ref mutation. A relevant
    pre-claim overlap invalidates the published Plan and returns the package to
    scope-approved state; a fresh Execution Plan approval is then required.
    """
    root = main_worktree(project_root.resolve())
    from delivery_compile import docs_root, find_delivery, split_note, frontmatter, content_hash
    docs = docs_root(root)
    directory = find_delivery(docs, delivery_id)
    if directory is None:
        raise RuntimeError("Delivery package not found")
    refs = canonical_refs(delivery_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    fence_message = commit_message(root, fence_oid)
    if trailer(fence_message, "Record") != "project-fence-v1" or trailer(fence_message, "Mode") != "open":
        raise RuntimeError("target-refresh requires an open Fence")
    previous_target = trailer(fence_message, "Target")
    if not previous_target or not OID_RE.fullmatch(previous_target):
        raise RuntimeError("Fence has no valid target baseline")
    target_branch, target = resolve_target(root, remote)
    if target == previous_target:
        return {"ok": True, "delivery": delivery_id, "changed": False,
                "target": target, "refs": short_refs(delivery_id)}
    # Ensure the target objects exist locally without changing any semantic ref.
    run_git(root, "fetch", "--no-tags", remote, f"refs/heads/{target_branch}:refs/remotes/{remote}/{target_branch}")
    changed = _changed_target_paths(root, previous_target, target)
    selected: dict[str, dict] = {}
    claimed: dict[str, str] = {}
    for item_path in sorted(directory.glob("items/*/item.md")):
        story = item_path.parent.name.upper()
        item_ref = canonical_refs(delivery_id, story)["item"]
        if not remote_has_ref(root, remote, item_ref):
            continue
        item_oid = remote_oid(root, remote, item_ref)
        item_props, _ = split_remote_note(root, item_oid, str(item_path.relative_to(root)), split_note)
        for claim in item_props.get("path_claims", []) or []:
            claimed[str(claim).lstrip("./")] = story
    overlaps = sorted(path for path in changed if path in claimed)
    if overlaps:
        raise RuntimeError("claimed_source_violation: target changed claimed paths " + ", ".join(overlaps))
    relevant = [
        path for path in changed
        if path in {"workspace/docs/maps/delivery.md", "workspace/docs/definition-of-done.md"}
    ]
    merge = merge_candidate(
        root, integration_oid, target, f"Refresh target for {delivery_id}",
        {"Record": "target-refresh-v1", "Protocol": "1", "Delivery": delivery_id,
         "Previous-Target": previous_target, "Target": target,
         "Target-Impact-Hash": "none"},
    )
    final_candidate = merge
    invalidated = bool(relevant)
    if invalidated:
        delivery_path = directory / "delivery.md"
        plan_path = directory / "execution-plan.md"
        replacements = {}
        remote_delivery_props, remote_delivery_body = split_remote_note(root, merge, str(delivery_path.relative_to(root)), split_note)
        remote_delivery_props["status"] = "scope_approved"
        remote_delivery_props.pop("plan_hash", None)
        remote_delivery_props["source_hash"] = content_hash(remote_delivery_props, remote_delivery_body)
        replacements[str(delivery_path.relative_to(root))] = frontmatter(remote_delivery_props, remote_delivery_body)
        if plan_path.exists():
            try:
                plan_props, plan_body = split_remote_note(root, merge, str(plan_path.relative_to(root)), split_note)
                plan_props["status"] = "draft"
                plan_props["source_hash"] = content_hash(plan_props, plan_body)
                replacements[str(plan_path.relative_to(root))] = frontmatter(plan_props, plan_body)
            except RuntimeError:
                pass
        final_candidate = commit_replacements(
            root, merge, replacements, f"Invalidate execution plan for {delivery_id}",
            {"Record": "target-refresh-v1", "Protocol": "1", "Delivery": delivery_id,
             "Previous-Target": previous_target, "Target": target,
             "Target-Impact-Hash": "none", "Plan-Invalidated": "true"},
        )
    fence_candidate = commit_tree(
        root, fence_oid, [], "Refresh project target",
        {"Record": "project-fence-v1", "Protocol": "1", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(), "Target": target,
         "Config-Hash": trailer(fence_message, "Config-Hash") or "none"},
    )
    atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                               (refs["integration"], integration_oid, final_candidate)])
    _target_branch, observed_target = resolve_target(root, remote)
    partial = observed_target != target
    return {"ok": True, "delivery": delivery_id, "changed": True, "target": target,
            "previous_target": previous_target, "paths": changed,
            "plan_invalidated": invalidated, "integration": final_candidate,
            "fence": fence_candidate, "current_target": observed_target,
            "partial": partial, "writer_ready": not partial,
            "refs": short_refs(delivery_id)}


def revise_unclaimed_scope(project_root: Path, delivery_id: str,
                           remote: str = "origin") -> dict:
    """Publish a revised pre-claim scope after a fresh local approval."""
    root = main_worktree(project_root.resolve())
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
    if trailer(fence_message, "Mode") != "open":
        raise RuntimeError("revise-unclaimed-scope requires an open Fence")
    for item_path in sorted(directory.glob("items/*/item.md")):
        story = item_path.parent.name.upper()
        if remote_has_ref(root, remote, canonical_refs(delivery_id, story)["item"]):
            raise RuntimeError(f"scope revision is forbidden after Item claim: {story}")
    occupied = remote_slot_oids(root, remote)
    if occupied:
        raise RuntimeError("scope revision requires no active global Slot")
    previous_scope = trailer(commit_message(root, integration_oid), "Scope-Hash") or "none"
    target_branch, target = resolve_target(root, remote)
    base = integration_oid
    if trailer(fence_message, "Target") and trailer(fence_message, "Target") != target:
        run_git(root, "fetch", "--no-tags", remote, f"refs/heads/{target_branch}:refs/remotes/{remote}/{target_branch}")
        base = merge_candidate(
            root, integration_oid, target, f"Refresh target before scope revision for {delivery_id}",
            {"Record": "target-refresh-v1", "Protocol": "1", "Delivery": delivery_id,
             "Previous-Target": trailer(fence_message, "Target"), "Target": target,
             "Target-Impact-Hash": "none"},
        )
    package = package_paths(root, directory, docs)
    candidate = commit_tree(
        root, base, package, f"Revise scope for {delivery_id}",
        {"Record": "delivery-scope-revised-v1", "Protocol": "1", "Delivery": delivery_id,
         "Previous-Scope-Hash": previous_scope, "Scope-Hash": str(local_props.get("scope_hash", "none")),
         "Target": target},
    )
    fence_candidate = commit_tree(
        root, fence_oid, [], "Fence project in open mode",
        {"Record": "project-fence-v1", "Protocol": "1", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(), "Target": target,
         "Config-Hash": trailer(fence_message, "Config-Hash") or "none"},
    )
    atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                               (refs["integration"], integration_oid, candidate)])
    return {"ok": True, "delivery": delivery_id, "integration": candidate,
            "fence": fence_candidate, "previous_scope_hash": previous_scope,
            "scope_hash": local_props.get("scope_hash", "none"), "target": target,
            "refs": short_refs(delivery_id)}


def _fence_context(root: Path, remote: str) -> tuple[str, str, dict[str, str]]:
    """Read the current Fence tip and its closed control trailers."""
    ref = canonical_refs("DLV-000")["fence"]
    fence_oid = remote_oid(root, remote, ref)
    message = commit_message(root, fence_oid)
    if trailer(message, "Record") != "project-fence-v1":
        raise RuntimeError("DELIVERY_FENCE_CORRUPT: current Fence record is unsupported")
    protocol = trailer(message, "Protocol")
    if protocol != "1":
        raise RuntimeError("DELIVERY_PROTOCOL_UNSUPPORTED: Fence protocol is not 1")
    values = {
        key: trailer(message, key) or "none"
        for key in ("Mode", "Epoch", "Target", "Config-Hash", "Source-Hash",
                    "Barrier-Kind", "Barrier-Epoch", "Target-Update-Intent", "Attempt")
    }
    return ref, fence_oid, values


def _fence_child(root: Path, fence_oid: str, values: dict[str, str], subject: str) -> str:
    trailers = {
        "Record": "project-fence-v1", "Protocol": "1",
        "Mode": values.get("Mode", "open"), "Epoch": values.get("Epoch", epoch_token()),
        "Target": values.get("Target", "none"), "Config-Hash": values.get("Config-Hash", "none"),
        "Source-Hash": values.get("Source-Hash", "none"),
        "Barrier-Kind": values.get("Barrier-Kind", "none"),
        "Barrier-Epoch": values.get("Barrier-Epoch", "none"),
        "Target-Update-Intent": values.get("Target-Update-Intent", "none"),
        "Attempt": values.get("Attempt", "none"),
    }
    return commit_tree(root, fence_oid, [], subject, trailers)


def begin_source_handoff(project_root: Path, source_hash: str = "none",
                         remote: str = "origin") -> dict:
    """Acquire the shared Fence for a source/configuration handoff."""
    root = main_worktree(project_root.resolve())
    if source_hash != "none" and not re.fullmatch(r"sha256:[0-9a-f]{64}", source_hash):
        raise ValueError("source_hash must be none or a canonical sha256 digest")
    ref = canonical_refs("DLV-000")["fence"]
    try:
        fence_oid = remote_oid(root, remote, ref)
        current = commit_message(root, fence_oid)
        values = {key: trailer(current, key) or "none" for key in
                  ("Mode", "Epoch", "Target", "Config-Hash", "Barrier-Kind",
                   "Barrier-Epoch", "Target-Update-Intent", "Attempt")}
        if values["Mode"] != "open":
            raise RuntimeError("DELIVERY_FENCE_MODE: source handoff requires an open Fence")
        target = values["Target"]
        if target == "none":
            _branch, target = resolve_target(root, remote)
        values.update({"Mode": "source_handoff", "Epoch": epoch_token(), "Target": target,
                       "Source-Hash": source_hash, "Barrier-Kind": "none",
                       "Barrier-Epoch": "none", "Target-Update-Intent": "none", "Attempt": "none"})
        candidate = _fence_child(root, fence_oid, values, "Acquire source handoff Fence")
        atomic_push(root, remote, [(ref, fence_oid, candidate)])
    except RuntimeError as exc:
        if "remote ref is absent" not in str(exc).lower() and "does not exist" not in str(exc).lower():
            raise
        _branch, target = resolve_target(root, remote)
        values = {"Mode": "source_handoff", "Epoch": epoch_token(), "Target": target,
                  "Config-Hash": "none", "Source-Hash": source_hash,
                  "Barrier-Kind": "none", "Barrier-Epoch": "none",
                  "Target-Update-Intent": "none", "Attempt": "none"}
        candidate = commit_tree(root, target, [], "Acquire source handoff Fence", {
            "Record": "project-fence-v1", "Protocol": "1", **values})
        atomic_push(root, remote, [(ref, "", candidate)])
        fence_oid = ""
    return {"ok": True, "mode": "source_handoff", "fence": candidate,
            "previous_fence": fence_oid, "source_hash": source_hash, "refs": {"fence": ref}}


def authorize_target_update(project_root: Path, mode: str = "source_handoff",
                            candidate_hash: str = "none", remote: str = "origin") -> dict:
    """Install the durable target-update intent before an external target write."""
    root = main_worktree(project_root.resolve())
    ref, fence_oid, values = _fence_context(root, remote)
    if values["Mode"] != mode:
        raise RuntimeError(f"DELIVERY_FENCE_MODE: expected {mode}, found {values['Mode']}")
    if values["Target-Update-Intent"] != "none":
        raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: target-update intent already exists")
    if candidate_hash != "none" and not re.fullmatch(r"sha256:[0-9a-f]{64}", candidate_hash):
        raise ValueError("candidate_hash must be none or a canonical sha256 digest")
    values["Target-Update-Intent"] = candidate_hash
    values["Attempt"] = epoch_token()
    candidate = _fence_child(root, fence_oid, values, "Authorize target update")
    atomic_push(root, remote, [(ref, fence_oid, candidate)])
    return {"ok": True, "mode": mode, "fence": candidate,
            "target_update_intent": candidate_hash, "attempt": values["Attempt"]}


def finish_source_handoff(project_root: Path, remote: str = "origin") -> dict:
    """Close a source/config handoff only after the target is observable."""
    root = main_worktree(project_root.resolve())
    ref, fence_oid, values = _fence_context(root, remote)
    if values["Mode"] not in {"source_handoff", "configuring", "upgrade"}:
        raise RuntimeError("DELIVERY_FENCE_MODE: no source handoff is active")
    if values["Target-Update-Intent"] == "none":
        raise RuntimeError("finish-source-handoff requires an authorized target-update intent")
    _branch, target = resolve_target(root, remote)
    values.update({"Mode": "open", "Epoch": epoch_token(), "Target": target,
                   "Source-Hash": "none", "Barrier-Kind": "none", "Barrier-Epoch": "none",
                   "Target-Update-Intent": "none", "Attempt": "none"})
    candidate = _fence_child(root, fence_oid, values, "Finish source handoff")
    atomic_push(root, remote, [(ref, fence_oid, candidate)])
    return {"ok": True, "mode": "open", "fence": candidate, "target": target}


def abort_source_handoff(project_root: Path, remote: str = "origin") -> dict:
    """Abort only an acquired handoff whose external write never began."""
    root = main_worktree(project_root.resolve())
    ref, fence_oid, values = _fence_context(root, remote)
    if values["Mode"] not in {"source_handoff", "configuring", "upgrade"}:
        raise RuntimeError("DELIVERY_FENCE_MODE: no source handoff is active")
    if values["Target-Update-Intent"] != "none":
        raise RuntimeError("DELIVERY_TARGET_UPDATE_UNCERTAIN: abort is forbidden after target-update intent")
    values.update({"Mode": "open", "Epoch": epoch_token(), "Source-Hash": "none",
                   "Barrier-Kind": "none", "Barrier-Epoch": "none", "Attempt": "none"})
    candidate = _fence_child(root, fence_oid, values, "Abort source handoff")
    atomic_push(root, remote, [(ref, fence_oid, candidate)])
    return {"ok": True, "mode": "open", "fence": candidate}


def _barrier_transition(project_root: Path, kind: str, action: str,
                        delivery_id: str | None = None, remote: str = "origin") -> dict:
    """Install or release a lightweight barrier on existing coordination refs."""
    root = main_worktree(project_root.resolve())
    validate_delivery_id(delivery_id or "DLV-000") if delivery_id else None
    fence_ref, fence_oid, values = _fence_context(root, remote)
    integration_ref = canonical_refs(delivery_id)["integration"] if delivery_id else None
    integration_oid = remote_oid(root, remote, integration_ref) if integration_ref else None
    if action == "begin":
        if values["Mode"] != "open" or values["Barrier-Kind"] != "none":
            raise RuntimeError("DELIVERY_BARRIER_ACTIVE: an incompatible Fence barrier is already active")
        epoch = epoch_token()
        values.update({"Barrier-Kind": kind, "Barrier-Epoch": epoch,
                       "Mode": "upgrade" if kind == "upgrade" else "open"})
        fence_candidate = _fence_child(root, fence_oid, values, f"Begin {kind} barrier")
        updates = [(fence_ref, fence_oid, fence_candidate)]
        integration_candidate = None
        if integration_ref:
            integration_candidate = commit_tree(
                root, integration_oid, [], f"Begin {kind} barrier for {delivery_id}",
                {"Record": "delivery-barrier-v1", "Protocol": "1", "Delivery": delivery_id,
                 "Barrier-Kind": kind, "Barrier-Epoch": epoch,
                 "Cancellation-Intent-Hash": "none"},
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
    values.update({"Barrier-Kind": "none", "Barrier-Epoch": "none", "Mode": "open", "Epoch": epoch_token()})
    fence_candidate = _fence_child(root, fence_oid, values, f"Release {kind} barrier")
    updates = [(fence_ref, fence_oid, fence_candidate)]
    integration_candidate = None
    if integration_ref:
        integration_candidate = commit_tree(
            root, integration_oid, [], f"Release {kind} barrier for {delivery_id}",
             {"Record": "delivery-barrier-release-v1", "Protocol": "1", "Delivery": delivery_id,
             "Barrier-Kind": kind, "Barrier-Epoch": barrier_epoch,
             "Target": values.get("Target", "none"), "Target-Impact-Hash": "none"},
        )
        updates.append((integration_ref, integration_oid, integration_candidate))
    atomic_push(root, remote, updates)
    return {"ok": True, "action": action, "barrier_kind": kind,
            "fence": fence_candidate, "integration": integration_candidate}


def begin_plan_revision(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    return _barrier_transition(project_root, "plan-revision", "begin", delivery_id, remote)


def finish_plan_revision(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    return _barrier_transition(project_root, "plan-revision", "finish", delivery_id, remote)


def abort_plan_revision(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    return _barrier_transition(project_root, "plan-revision", "abort", delivery_id, remote)


def begin_upgrade(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    return _barrier_transition(project_root, "upgrade", "begin", delivery_id, remote)


def finish_upgrade(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    return _barrier_transition(project_root, "upgrade", "finish", delivery_id, remote)


def abort_upgrade(project_root: Path, delivery_id: str, remote: str = "origin") -> dict:
    return _barrier_transition(project_root, "upgrade", "abort", delivery_id, remote)


def configure_parallelism(project_root: Path, value: int, *, dry_run: bool = False,
                          remote: str = "origin") -> dict:
    """Set the single project-wide Delivery Item WIP value.

    The configuration writer remains the owner of the tracked JSON. This
    coordinator helper validates the just-in-time activation decision and
    performs only that one field write; a remote-aware handoff can then carry
    the resulting commit through the normal project policy.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("max_parallel must be a positive integer")
    root = main_worktree(project_root.resolve())
    path = root / "workspace" / "config.json"
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read project config: {exc}") from exc
    previous = config.get("max_parallel")
    if isinstance(previous, int) and value < previous:
        raise RuntimeError("max_parallel is monotonic in v1; decreases require a future config epoch")
    if previous == value:
        return {"ok": True, "changed": False, "max_parallel": value,
                "next_entry": "/deliver", "dry_run": dry_run}
    handoff = None
    if not dry_run:
        fence_ref = canonical_refs("DLV-000")["fence"]
        if remote_has_ref(root, remote, fence_ref):
            # Config changes compete with Delivery reservation/claim through
            # the same Fence. The user commits the local config while this
            # source_handoff is held, then finishes it after target handoff.
            handoff = begin_source_handoff(root, "none", remote)
    config["max_parallel"] = value
    try:
        if not dry_run:
            from project_config import atomic
            atomic(path, config)
    except Exception:
        if handoff is not None:
            try:
                abort_source_handoff(root, remote)
            except Exception:
                pass
        raise
    return {"ok": True, "changed": True, "max_parallel": value,
            "previous": previous if isinstance(previous, int) else "none",
            "next_entry": "/deliver", "dry_run": dry_run,
            "handoff": handoff, "requires_target_handoff": handoff is not None}


def reauthorize_target_update(project_root: Path, mode: str = "source_handoff",
                              candidate_hash: str = "none", remote: str = "origin") -> dict:
    """Refuse reauthorization unless the caller supplies a fresh zero-effect proof.

    The proof is deliberately not inferred from a transport error. A future
    provider adapter may pass a validated proof and replace the immutable
    carrier in the same Fence transaction; v1 remains fail-closed here.
    """
    raise RuntimeError(
        "DELIVERY_TARGET_UPDATE_UNCERTAIN: reauthorization requires a provider-validated zero-target-effect proof"
    )


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
               remote: str = "origin", allowed_statuses: set[str] | None = None) -> dict:
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
    fence_target = trailer(fence_message, "Target")
    if not fence_target or not OID_RE.fullmatch(fence_target):
        raise RuntimeError("start-item requires a valid Fence target baseline")
    _target_branch, target_before = resolve_target(root, remote)
    if target_before != fence_target:
        raise RuntimeError("target advanced; refresh the Delivery before Item activation")
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
    relative_item = str(item_path.relative_to(root))
    item_props, item_body = split_remote_note(root, item_oid, relative_item, split_note)
    allowed = {"in_scope", "paused", "blocked"} if allowed_statuses is None else allowed_statuses
    if item_props.get("status") not in allowed:
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
    _target_branch, target_after = resolve_target(root, remote)
    if target_after != fence_target:
        paused_props = dict(item_props)
        paused_props["status"] = "paused"
        paused_props["tags"] = [tag for tag in paused_props.get("tags", [])
                                  if not str(tag).startswith("status/")] + ["status/paused"]
        paused_props["source_hash"] = content_hash(paused_props, item_body)
        paused = commit_replacements(
            root, item_candidate,
            {str(item_path.relative_to(root)): frontmatter(paused_props, item_body)},
            f"Quiesce {story_id} after target advance",
            {"Record": "item-quiesce-v1", "Protocol": "1", "Delivery": delivery_id,
             "Story": story_id, "Kind": "target-drift", "Previous-Tip": item_candidate,
             "Slot": slot},
        )
        atomic_push(root, remote, [(refs["item"], item_candidate, paused),
                                   (slot_ref, item_candidate, "")])
        discard_pending_writer_receipt(root, delivery_id, story_id, item_candidate)
        raise RuntimeError(
            "target advanced after Item activation; Item was paused before worktree creation"
        )
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
    from delivery_compile import docs_root, split_note, frontmatter, content_hash
    directory = find_delivery_dir_from_remote(root, remote, delivery_id)
    if directory is None:
        raise RuntimeError("local Delivery package is required for Item status transition")
    refs = canonical_refs(delivery_id, story_id)
    item_oid = remote_oid(root, remote, refs["item"])
    slots = remote_slot_oids(root, remote)
    slot = next((key for key, oid in slots.items() if oid == item_oid), None)
    if slot is None:
        raise RuntimeError("Item and Slot refs diverge; refuse active status transition")
    relative_item = str((directory / "items" / story_key(story_id) / "item.md").relative_to(root))
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

    A sealed Item is never passed back through ``start-item``. Its remote tip
    becomes a child with invalidated evidence, Integration receives a separate
    authorization record and the new Item tip alone acquires the Slot.
    """
    root = main_worktree(project_root.resolve())
    from delivery_compile import docs_root, split_note, frontmatter, content_hash
    directory = find_delivery_dir_from_remote(root, remote, delivery_id)
    if directory is None:
        raise RuntimeError("local Delivery package is required for Item reopen")
    refs = canonical_refs(delivery_id, story_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    item_oid = remote_oid(root, remote, refs["item"])
    fence_message = commit_message(root, fence_oid)
    if trailer(fence_message, "Mode") != "open":
        raise RuntimeError("reopen-item requires an open Fence")
    if any(oid == item_oid for oid in remote_slot_oids(root, remote).values()):
        raise RuntimeError("reopen-item requires a sealed, slotless Item")
    relative_item = str((directory / "items" / story_key(story_id) / "item.md").relative_to(root))
    props, body = split_remote_note(root, item_oid, relative_item, split_note)
    if props.get("status") != "integrated":
        raise RuntimeError("reopen-item requires an integrated Item")
    props["status"] = "active"
    props["tags"] = [tag for tag in props.get("tags", []) if not str(tag).startswith("status/")] + ["status/active"]
    props["integration_base_commit"] = integration_oid
    props["source_hash"] = content_hash(props, body)
    writer = epoch_token()
    item_candidate = commit_replacements(
        root, item_oid, {relative_item: frontmatter(props, body)},
        f"Reopen {story_id} for {delivery_id}",
        {"Record": "item-reopen-v1", "Protocol": "1", "Delivery": delivery_id,
         "Story": story_id, "Previous-Tip": item_oid, "Integration-Base": integration_oid,
         "Writer-Epoch": writer},
    )
    integration_candidate = commit_tree(
        root, integration_oid, [], f"Authorize reopen of {story_id} for {delivery_id}",
        {"Record": "item-reopen-authorized-v1", "Protocol": "1", "Delivery": delivery_id,
         "Story": story_id, "Previous-Tip": item_oid, "Item-Tip": item_candidate,
         "Integration-Base": integration_oid, "Writer-Epoch": writer},
    )
    max_parallel = project_max_parallel(root)
    occupied = remote_slot_oids(root, remote)
    free = next((slot for slot in range(1, max_parallel + 1) if slot_key(slot) not in occupied), None)
    if free is None:
        raise RuntimeError("no global execution Slot is available for reopen")
    slot = slot_key(free)
    slot_ref = canonical_refs(delivery_id, story_id, slot)["slot"]
    fence_candidate = commit_tree(
        root, fence_oid, [], "Authorize Item reopen",
        {"Record": "project-fence-v1", "Protocol": "1", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": trailer(fence_message, "Target") or "none",
         "Config-Hash": trailer(fence_message, "Config-Hash") or "none"},
    )
    receipt = create_writer_receipt(
        root, delivery_id, story_id, slot, writer, refs["item"], slot_ref,
        item_candidate, allow_verified_replace=True, expected_previous_oid=item_oid,
    )
    atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                               (refs["integration"], integration_oid, integration_candidate),
                               (refs["item"], item_oid, item_candidate),
                               (slot_ref, "", item_candidate)])
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
    from delivery_compile import docs_root, split_note, frontmatter, content_hash
    docs = docs_root(root)
    directory = find_delivery_dir_from_remote(root, remote, delivery_id)
    if directory is None:
        raise RuntimeError("local Delivery package is required for Item pause")
    refs = canonical_refs(delivery_id, story_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    item_oid = remote_oid(root, remote, refs["item"])
    slots = remote_slot_oids(root, remote)
    slot = next((key for key, oid in slots.items() if oid == item_oid), None)
    if slot is None:
        raise RuntimeError("pause-item requires one exact Item Slot pair")
    slot_ref = f"refs/heads/agentrof/slots/{slot}"
    worktree = worktree_paths(root, delivery_id, story_id)["item"]
    worktree_is_clean_and_at(root, worktree, item_oid)
    relative_item = str((directory / "items" / story_key(story_id) / "item.md").relative_to(root))
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
    fence_message = commit_message(root, fence_oid)
    fence_candidate = commit_tree(
        root, fence_oid, [], f"Fence project in open mode",
        {"Record": "project-fence-v1", "Protocol": "1", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": trailer(fence_message, "Target") or "none",
         "Config-Hash": trailer(fence_message, "Config-Hash") or "none"},
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
    item_props, _ = split_remote_note(root, item_oid, str(item_path.relative_to(root)), split_note)
    if item_props.get("status") != "paused":
        raise RuntimeError("resume-item requires a paused remote Item")
    if any(oid == item_oid for oid in remote_slot_oids(root, remote).values()):
        raise RuntimeError("resume-item requires a slotless paused Item")
    return start_item(root, delivery_id, story_id, remote, allowed_statuses={"paused"})


def takeover_item(project_root: Path, delivery_id: str, story_id: str,
                  remote: str = "origin", *, confirm: bool = False) -> dict:
    """Take over one active Item after explicit host-loss confirmation."""
    if not confirm:
        raise RuntimeError("takeover requires explicit host-loss confirmation")
    root = main_worktree(project_root.resolve())
    from delivery_compile import docs_root, split_note, frontmatter, content_hash
    docs = docs_root(root)
    directory = find_delivery_dir_from_remote(root, remote, delivery_id)
    if directory is None:
        raise RuntimeError("local Delivery package is required for Item takeover")
    refs = canonical_refs(delivery_id, story_id)
    fence_oid = remote_oid(root, remote, refs["fence"])
    integration_oid = remote_oid(root, remote, refs["integration"])
    item_oid = remote_oid(root, remote, refs["item"])
    slots = remote_slot_oids(root, remote)
    slot = next((key for key, oid in slots.items() if oid == item_oid), None)
    if slot is None:
        raise RuntimeError("takeover requires one exact existing Item Slot pair")
    slot_ref = f"refs/heads/agentrof/slots/{slot}"
    relative_item = str((directory / "items" / story_key(story_id) / "item.md").relative_to(root))
    item_props, item_body = split_remote_note(root, item_oid, relative_item, split_note)
    if item_props.get("status") not in {"active", "blocked"}:
        raise RuntimeError("takeover requires an active or blocked remote Item")
    worktree = worktree_paths(root, delivery_id, story_id)["item"]
    if worktree.exists():
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
         "Story": story_id, "Previous-Tip": item_oid, "Slot": slot, "Writer-Epoch": writer},
    )
    fence_message = commit_message(root, fence_oid)
    integration_candidate = commit_tree(
        root, integration_oid, [], f"Take over {story_id} for {delivery_id}",
        {"Record": "item-takeover-v1", "Protocol": "1", "Delivery": delivery_id,
         "Story": story_id, "Previous-Tip": item_oid, "Item-Tip": item_candidate,
         "Slot": slot, "Writer-Epoch": writer},
    )
    fence_candidate = commit_tree(
        root, fence_oid, [], "Fence project in open mode",
        {"Record": "project-fence-v1", "Protocol": "1", "Mode": "open",
         "Epoch": trailer(fence_message, "Epoch") or epoch_token(),
         "Target": trailer(fence_message, "Target") or "none",
         "Config-Hash": trailer(fence_message, "Config-Hash") or "none"},
    )
    create_writer_receipt(
        root, delivery_id, story_id, slot, writer, refs["item"], slot_ref,
        item_candidate, allow_verified_replace=True, expected_previous_oid=item_oid,
    )
    atomic_push(root, remote, [(refs["fence"], fence_oid, fence_candidate),
                               (refs["integration"], integration_oid, integration_candidate),
                               (refs["item"], item_oid, item_candidate),
                               (slot_ref, item_oid, item_candidate)])
    if remote_oid(root, remote, refs["item"]) != item_candidate or remote_oid(root, remote, slot_ref) != item_candidate:
        raise RuntimeError("takeover refs did not converge to the receipt candidate")
    receipt = promote_writer_receipt(root, delivery_id, story_id, item_candidate)
    materialized = materialize_item_worktree(root, delivery_id, story_id, item_candidate)
    return {"ok": True, "delivery": delivery_id, "story": story_id, "slot": slot,
            "writer_epoch": writer, "item": item_candidate, "integration": integration_candidate,
            "fence": fence_candidate, "receipt": receipt, "worktree": str(materialized),
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
    remove_item_worktree(root, delivery_id, story_id)
    clear_verified_writer_receipt(root, delivery_id, story_id)
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
    config = sub.add_parser("configure-parallelism"); config.add_argument("--project-root", default="."); config.add_argument("--value", type=int, required=True); config.add_argument("--dry-run", action="store_true"); config.add_argument("--remote", default="origin"); config.set_defaults(func="configure")
    source_begin = sub.add_parser("begin-source-handoff"); source_begin.add_argument("--project-root", default="."); source_begin.add_argument("--source-hash", default="none"); source_begin.add_argument("--remote", default="origin"); source_begin.set_defaults(func="source-begin")
    source_auth = sub.add_parser("authorize-target-update"); source_auth.add_argument("--project-root", default="."); source_auth.add_argument("--mode", choices=["source_handoff", "configuring", "upgrade"], default="source_handoff"); source_auth.add_argument("--candidate-hash", default="none"); source_auth.add_argument("--remote", default="origin"); source_auth.set_defaults(func="source-authorize")
    source_reauth = sub.add_parser("reauthorize-target-update"); source_reauth.add_argument("--project-root", default="."); source_reauth.add_argument("--mode", choices=["source_handoff", "configuring", "upgrade"], default="source_handoff"); source_reauth.add_argument("--candidate-hash", default="none"); source_reauth.add_argument("--remote", default="origin"); source_reauth.set_defaults(func="source-reauthorize")
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
    upgrade_finish = sub.add_parser("finish-upgrade"); upgrade_finish.add_argument("--project-root", default="."); upgrade_finish.add_argument("--delivery", required=True); upgrade_finish.add_argument("--remote", default="origin"); upgrade_finish.set_defaults(func="upgrade-finish")
    upgrade_abort = sub.add_parser("abort-upgrade"); upgrade_abort.add_argument("--project-root", default="."); upgrade_abort.add_argument("--delivery", required=True); upgrade_abort.add_argument("--remote", default="origin"); upgrade_abort.set_defaults(func="upgrade-abort")
    start = sub.add_parser("start-item"); start.add_argument("--project-root", default="."); start.add_argument("--delivery", required=True); start.add_argument("--story", required=True); start.add_argument("--remote", default="origin"); start.set_defaults(func="start")
    block = sub.add_parser("block-item"); block.add_argument("--project-root", default="."); block.add_argument("--delivery", required=True); block.add_argument("--story", required=True); block.add_argument("--remote", default="origin"); block.set_defaults(func="block")
    unblock = sub.add_parser("unblock-item"); unblock.add_argument("--project-root", default="."); unblock.add_argument("--delivery", required=True); unblock.add_argument("--story", required=True); unblock.add_argument("--remote", default="origin"); unblock.set_defaults(func="unblock")
    reopen = sub.add_parser("reopen-item"); reopen.add_argument("--project-root", default="."); reopen.add_argument("--delivery", required=True); reopen.add_argument("--story", required=True); reopen.add_argument("--remote", default="origin"); reopen.set_defaults(func="reopen")
    pause = sub.add_parser("pause-item"); pause.add_argument("--project-root", default="."); pause.add_argument("--delivery", required=True); pause.add_argument("--story", required=True); pause.add_argument("--remote", default="origin"); pause.set_defaults(func="pause")
    resume = sub.add_parser("resume-item"); resume.add_argument("--project-root", default="."); resume.add_argument("--delivery", required=True); resume.add_argument("--story", required=True); resume.add_argument("--remote", default="origin"); resume.set_defaults(func="resume")
    takeover = sub.add_parser("takeover-item"); takeover.add_argument("--project-root", default="."); takeover.add_argument("--delivery", required=True); takeover.add_argument("--story", required=True); takeover.add_argument("--remote", default="origin"); takeover.add_argument("--confirm", action="store_true"); takeover.set_defaults(func="takeover")
    push_item_parser = sub.add_parser("push-item"); push_item_parser.add_argument("--project-root", default="."); push_item_parser.add_argument("--delivery", required=True); push_item_parser.add_argument("--story", required=True); push_item_parser.add_argument("--remote", default="origin"); push_item_parser.set_defaults(func="push-item")
    integrate = sub.add_parser("integrate-item"); integrate.add_argument("--project-root", default="."); integrate.add_argument("--delivery", required=True); integrate.add_argument("--story", required=True); integrate.add_argument("--remote", default="origin"); integrate.set_defaults(func="integrate")
    publish_review = sub.add_parser("publish-delivery-review"); publish_review.add_argument("--project-root", default="."); publish_review.add_argument("--delivery", required=True); publish_review.add_argument("--remote", default="origin"); publish_review.set_defaults(func="publish-review")
    prepare_pr = sub.add_parser("prepare-pr-creation"); prepare_pr.add_argument("--project-root", default="."); prepare_pr.add_argument("--delivery", required=True); prepare_pr.add_argument("--remote", default="origin"); prepare_pr.set_defaults(func="prepare-pr")
    record_pr_parser = sub.add_parser("record-pr-remote"); record_pr_parser.add_argument("--project-root", default="."); record_pr_parser.add_argument("--delivery", required=True); record_pr_parser.add_argument("--url", required=True); record_pr_parser.add_argument("--remote", default="origin"); record_pr_parser.set_defaults(func="record-pr-remote")
    open_pr_parser = sub.add_parser("open-pr"); open_pr_parser.add_argument("--project-root", default="."); open_pr_parser.add_argument("--delivery", required=True); open_pr_parser.add_argument("--remote", default="origin"); open_pr_parser.set_defaults(func="open-pr")
    merge_pr_parser = sub.add_parser("merge-pr"); merge_pr_parser.add_argument("--project-root", default="."); merge_pr_parser.add_argument("--delivery", required=True); merge_pr_parser.add_argument("--remote", default="origin"); merge_pr_parser.set_defaults(func="merge-pr")
    invalidate_review = sub.add_parser("invalidate-delivery-review"); invalidate_review.add_argument("--project-root", default="."); invalidate_review.add_argument("--delivery", required=True); invalidate_review.add_argument("--finding-code", required=True); invalidate_review.add_argument("--finding-hash", required=True); invalidate_review.add_argument("--remote", default="origin"); invalidate_review.set_defaults(func="invalidate-review")
    cancel = sub.add_parser("cancel-delivery"); cancel.add_argument("--project-root", default="."); cancel.add_argument("--delivery", required=True); cancel.add_argument("--reason", required=True); cancel.add_argument("--remote", default="origin"); cancel.set_defaults(func="cancel")
    verify = sub.add_parser("verify-merge"); verify.add_argument("--project-root", default="."); verify.add_argument("--delivery", required=True); verify.add_argument("--remote", default="origin"); verify.set_defaults(func="merge-pr")
    reconcile = sub.add_parser("reconcile"); reconcile.add_argument("--project-root", default="."); reconcile.add_argument("--delivery", required=True); reconcile.add_argument("--remote", default="origin"); reconcile.set_defaults(func="reconcile")
    board = sub.add_parser("board"); board.add_argument("--project-root", default="."); board.add_argument("--delivery", required=True); board.add_argument("--remote", default="origin"); board.set_defaults(func="board")
    locate = sub.add_parser("locate"); locate.add_argument("--delivery", required=True); locate.add_argument("--story"); locate.add_argument("--slot"); locate.set_defaults(func="names")
    args = parser.parse_args(argv)
    try:
        if args.func == "names":
            result = {"ok": True, "refs": short_refs(args.delivery, args.story, args.slot),
                      "full_refs": canonical_refs(args.delivery, args.story, args.slot)}
        else:
            if args.func == "reserve":
                result = reserve_delivery(Path(args.project_root), args.delivery, args.remote)
            elif args.func == "configure":
                result = configure_parallelism(Path(args.project_root), args.value, dry_run=args.dry_run, remote=args.remote)
            elif args.func == "source-begin":
                result = begin_source_handoff(Path(args.project_root), args.source_hash, args.remote)
            elif args.func == "source-authorize":
                result = authorize_target_update(Path(args.project_root), args.mode, args.candidate_hash, args.remote)
            elif args.func == "source-reauthorize":
                result = reauthorize_target_update(Path(args.project_root), args.mode, args.candidate_hash, args.remote)
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
            elif args.func == "invalidate-review":
                result = invalidate_delivery_review(Path(args.project_root), args.delivery, args.finding_code, args.finding_hash, args.remote)
            elif args.func == "cancel":
                result = cancel_delivery(Path(args.project_root), args.delivery, args.reason, args.remote)
            elif args.func == "reconcile":
                result = preflight(Path(args.project_root), args.delivery, None, None)
            elif args.func == "board":
                result = preflight(Path(args.project_root), args.delivery, None, None)
            else:
                result = preflight(Path(args.project_root), args.delivery, args.story, args.slot)
    except (ValueError, RuntimeError) as exc:
        result = {"ok": False, "errors": [str(exc)]}
    envelope = delivery_result.from_raw(args.command, result)
    print(json.dumps(envelope, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if envelope["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
