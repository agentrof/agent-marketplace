#!/usr/bin/env python3
"""Read-only Delivery closure: managed pull requests, their readiness, the closure audit and
the repository protection report.

Nothing here moves or deletes a ref, releases a Slot or a writer receipt, or
changes a provider object. The coordinator's atomic leased verbs keep every
allocation release; this module only names the outcome and the step that
owns its recovery. A pull request head is read as Git data and never run.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import delivery_compile
import delivery_git
from delivery_git import (
    canonical_github_pr, canonical_refs, run_git, split_remote_note, trailer,
)


CLOSURE_CONTEXT = "delivery-closure"
MANAGED_REF_PREFIX = "agentrof/"
DELIVERIES = (delivery_compile.delivery_root(Path("workspace") / "docs") / "deliveries").as_posix()
PRODUCT_EXCLUDED_ROOT = "workspace/docs/"
PR_INTENT_RECORDS = {"pr_creation_intent_v1", "pr_adoption_intent_v1"}
TARGET_MERGE_RECORDS = {"target_refresh_v1", "upgrade_target_merge_v1"}
OUTCOMES = ("closed", "merged_cleanup_pending", "awaiting_merge", "open", "external_product_merge",
            "unproven_record_merge")
PROTECTION_STATES = ("configured", "not_configured", "unknown")
CLOSURE_WORKFLOW = ".github/workflows/delivery-closure.yml"
CLOSURE_GATE_PATHS = (CLOSURE_WORKFLOW, ".github/agentrof/vault-gate.pyz")
# The GitHub Actions app: a required check pinned to it cannot be met by a commit status.
ACTIONS_INTEGRATION_ID = 15368
PROTECTION_LIMIT = (
    "Only an owner-installed ruleset on the target branch that requires the delivery-closure check from "
    "the GitHub Actions app (integration_id 15368) or requires the .github/workflows/delivery-closure.yml "
    "workflow of this repository, with no bypass actors, can prevent a direct provider merge, an admin "
    "bypass or an owner push; a check required by name only can be met by a commit status or a same-name "
    "job, and no command of this package can"
)
CONTROL_RECORD_CONTRACT = (
    Path(__file__).resolve().parents[1] / "skill-content" / "deliver" / "data"
    / "delivery-control-record-contract.json"
)


def control_record_contract() -> dict:
    return json.loads(CONTROL_RECORD_CONTRACT.read_text(encoding="utf-8"))


def record_kind(message: str) -> str | None:
    """The control-record contract key of a commit's Agentrof-Record trailer, or None without one.

    A record the contract does not declare refuses, as its unknown record
    policy is fail_closed, so a reader never guesses what a newer record means.
    """
    name = trailer(message, "Record")
    if name is None:
        return None
    contract = control_record_contract()
    key = name.replace("-", "_")
    if key not in contract.get("records", {}):
        if contract.get("unknown_record_policy") == "fail_closed":
            raise RuntimeError(f"DELIVERY_PROTOCOL_UNSUPPORTED: control record {name} is not in the "
                               "control-record contract of this package; update the package before reading it")
    return key


def delivery_trailer_lines(message: str, delivery_id: str) -> bool:
    """Whether a commit message carries this Delivery's exact Agentrof-Delivery trailer line."""
    return any(line.strip() == f"Agentrof-Delivery: {delivery_id}" for line in message.splitlines())


class ObjectReader:
    """One long-lived `git cat-file --batch` that reads objects by exact id, each once.

    *replace* keeps Git's replace refs as the command it stands in for reads
    them. Objects are immutable by id, so a read is cached for the reader's
    life; a name Git cannot resolve, or that is not an exact id or an exact
    id and path, returns None and the caller asks Git the way it always did.
    """

    def __init__(self, root: Path, replace: bool):
        self.process = subprocess.Popen(["git", *(() if replace else ("--no-replace-objects",)), "cat-file", "--batch"],
                                        cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL)
        self.objects: dict[str, tuple[str, str, bytes] | None] = {}
        self.trees: dict[str, list[tuple[str, str, str]]] = {}

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.process.stdin.close()
        self.process.wait()
        self.process.stdout.close()

    def read(self, name: str) -> tuple[str, str, bytes] | None:
        """The id, type and bytes of *name*, an exact id or an exact id, a colon and a path."""
        oid, colon, path = name.partition(":")
        if (not delivery_git.OID_RE.fullmatch(oid) or "\n" in path or "\r" in path
                or (colon and (not path or path.startswith("/")))):
            return None
        if name not in self.objects:
            self.process.stdin.write(name.encode("utf-8") + b"\n")
            self.process.stdin.flush()
            header = self.process.stdout.readline().split()
            found = None
            if len(header) == 3 and header[2].isdigit():
                data = self.process.stdout.read(int(header[2]) + 1)[:-1]
                found = (header[0].decode("ascii"), header[1].decode("ascii"), data)
            elif not header:
                raise RuntimeError("DELIVERY_COORDINATION_CORRUPT: the Git object reader stopped")
            self.objects[name] = found
        return self.objects[name]

    def commit(self, oid: str) -> tuple[str, list[str], bytes, bool] | None:
        """A commit's tree, parents, raw message and whether its header names another encoding."""
        found = self.read(oid)
        if found is None or found[1] != "commit":
            return None
        header, _blank, message = found[2].partition(b"\n\n")
        fields = [line.partition(b" ")[::2] for line in header.split(b"\n")]
        tree = next((value.decode("ascii") for key, value in fields if key == b"tree"), "")
        lineage = [value.decode("ascii") for key, value in fields if key == b"parent"]
        return tree, lineage, message, any(key == b"encoding" for key, _value in fields)

    def tree(self, oid: str) -> list[tuple[str, str, str]] | None:
        """A tree's entries in Git's order, each as its six-digit mode, id and name."""
        if oid not in self.trees:
            found = self.read(oid)
            if found is None or found[1] != "tree":
                return None
            entries, data, size, at = [], found[2], len(oid) // 2, 0
            while at < len(data):
                space, null = data.index(b" ", at), data.index(b"\0", at)
                entries.append((f"{int(data[at:space], 8):06o}", data[null + 1:null + 1 + size].hex(),
                                data[space + 1:null].decode("utf-8")))
                at = null + 1 + size
            self.trees[oid] = entries
        return self.trees[oid]

    def entry(self, commit: str, path: str) -> tuple[str, str] | None | bool:
        """The mode and id *path* has in *commit*, None where it has nothing, False when unreadable."""
        found = self.commit(commit)
        if found is None:
            return False
        mode, oid = "040000", found[0]
        for part in path.split("/"):
            if mode != "040000":
                return None
            entries = self.tree(oid)
            if entries is None:
                return False
            mode, oid = next(((mode, oid) for mode, oid, name in entries if name == part), ("", ""))
            if not oid:
                return None
        return mode, oid

    def listing(self, commit: str, prefix: str) -> list[str] | None:
        """Every non-tree path under the directory *prefix* of *commit*, as `ls-tree -r` lists it."""
        top = self.entry(commit, prefix)
        if top is False:
            return None
        if top is None or top[0] != "040000":
            return []
        paths: list[str] = []

        def walk(oid: str, base: str) -> bool:
            entries = self.tree(oid)
            if entries is None:
                return False
            for mode, child, name in entries:
                if mode == "040000":
                    if not walk(child, f"{base}/{name}"):
                        return False
                else:
                    paths.append(f"{base}/{name}")
            return True

        return paths if walk(top[1], prefix) else None


# The readers and per-object answers of one closure audit; None outside it.
_SESSION: dict | None = None


@contextlib.contextmanager
def reading_session(root: Path):
    """Share one object reader per replace mode and every per-commit answer across one audit's Deliveries.

    A shallow or grafted history keeps parents a raw commit object does not
    show, so it reads every object the way it always did.
    """
    global _SESSION
    shallow, grafts = (run_git(root, "rev-parse", "--is-shallow-repository", "--git-path", "info/grafts")
                       .splitlines() + ["", ""])[:2]
    if _SESSION is not None or shallow != "false" or (root / grafts).exists():
        yield
        return
    _SESSION = {"root": root, "plain": ObjectReader(root, True), "raw": ObjectReader(root, False), "memo": {}}
    try:
        yield
    finally:
        session, _SESSION = _SESSION, None
        session["plain"].close()
        session["raw"].close()


def reader(root: Path, kind: str) -> ObjectReader | None:
    return _SESSION[kind] if _SESSION is not None and _SESSION["root"] == root else None


def memoized(root: Path, key: tuple, compute):
    """*compute*'s answer for *key*, once per audit; keys name exact ids only, so an answer never goes stale."""
    if _SESSION is None or _SESSION["root"] != root or not all(
            delivery_git.OID_RE.fullmatch(part) for part in key[1:] if isinstance(part, str)):
        return compute()
    memo = _SESSION["memo"]
    if key not in memo:
        memo[key] = compute()
    return memo[key]


def text_output(data: bytes) -> str:
    """Bytes as a text-mode Git pipe returns them: strict UTF-8, universal newlines, stripped."""
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").strip()


def commit_message(root: Path, oid: str) -> str:
    plain = reader(root, "plain")
    found = plain.commit(oid) if plain else None
    if found is None or found[3]:
        return delivery_git.commit_message(root, oid)
    return text_output(found[2])


def has_object(root: Path, oid: str) -> bool:
    plain = reader(root, "plain")
    if plain and plain.commit(oid) is not None:
        return True
    return subprocess.run(["git", "cat-file", "-e", oid + "^{commit}"], cwd=root,
                          capture_output=True, check=False).returncode == 0


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    """Whether *ancestor* is *descendant* or one of its ancestors.

    Within an audit a commit a few parent steps below *descendant* is proven
    so from the parents themselves; every other answer is Git's.
    """
    plain = reader(root, "plain")
    if plain and delivery_git.OID_RE.fullmatch(ancestor):
        frontier = [descendant]
        for _step in range(4):
            if ancestor in frontier:
                return True
            found = [plain.commit(oid) for oid in frontier]
            if any(entry is None for entry in found):
                break
            frontier = [parent for entry in found for parent in entry[1]]
    return memoized(root, ("is_ancestor", ancestor, descendant),
                    lambda: delivery_git.is_ancestor(root, ancestor, descendant))


def ensure_object(root: Path, remote: str, oid: str, ref: str) -> None:
    """Fetch *ref* when this checkout lacks its tip *oid*, as another host may have moved it."""
    if oid and not has_object(root, oid):
        run_git(root, "fetch", "--no-tags", remote, ref)


def remote_branch_tip(root: Path, remote: str, branch: str) -> str:
    """The remote tip of *branch*, fetched into its remote-tracking ref."""
    tracking = f"refs/remotes/{remote}/{branch}"
    run_git(root, "fetch", "--no-tags", remote, f"refs/heads/{branch}:{tracking}")
    return run_git(root, "rev-parse", tracking)


def listed_refs(root: Path, remote: str, pattern: str) -> dict[str, str]:
    refs = {}
    for line in run_git(root, "ls-remote", remote, pattern).splitlines():
        oid, _tab, name = line.partition("\t")
        if name:
            refs[name] = oid
    return refs


def coordination_state(root: Path, remote: str) -> dict:
    """Every Delivery's Integration tip, every Item ref with the Delivery its tip names, every Slot
    and the project Fence's target."""
    integrations = {}
    for ref, oid in listed_refs(root, remote, "refs/heads/agentrof/deliveries/*").items():
        identifier = ref.rsplit("/", 1)[1].upper()
        if delivery_compile.DELIVERY_ID_RE.fullmatch(identifier):
            ensure_object(root, remote, oid, ref)
            integrations[identifier] = oid
    items: dict[str, dict] = {}
    for ref, oid in listed_refs(root, remote, "refs/heads/agentrof/items/*").items():
        ensure_object(root, remote, oid, ref)
        items[ref] = {"tip": oid, "story": ref.rsplit("/", 1)[1].upper(),
                      "delivery": trailer(commit_message(root, oid), "Delivery") or ""}
    slots = {}
    for ref, oid in listed_refs(root, remote, "refs/heads/agentrof/slots/*").items():
        ensure_object(root, remote, oid, ref)
        slots[ref] = {"tip": oid, "delivery": trailer(commit_message(root, oid), "Delivery") or "",
                      "story": trailer(commit_message(root, oid), "Story") or ""}
    return {"integrations": integrations, "items": items, "slots": slots,
            "fence_target": fence_target(root, remote)}


def fence_target(root: Path, remote: str) -> str:
    """The target commit the project Fence names, or "" without a readable one."""
    target = ""
    for ref, oid in listed_refs(root, remote, "refs/heads/agentrof/fence").items():
        ensure_object(root, remote, oid, ref)
        target = trailer(commit_message(root, oid), "Target") or ""
    return target if delivery_git.OID_RE.fullmatch(target) and has_object(root, target) else ""


def tree_paths(root: Path, commit: str, prefix: str) -> list[str]:
    plain = reader(root, "plain")
    # A pathspec magic or wildcard character is Git's to match.
    listed = plain.listing(commit, prefix) if plain and not re.search(r"[*?\[\\]|^:", prefix) else None
    if listed is not None:
        return listed
    return delivery_git.git_paths(root, "ls-tree", "-r", "-z", "--name-only", commit, "--", prefix + "/")


def package_directory(root: Path, commit: str, delivery_id: str) -> str | None:
    """The Delivery package directory, relative to the checkout, that *commit*'s tree holds."""
    marker = delivery_id.lower() + "-"
    for path in tree_paths(root, commit, DELIVERIES):
        parts = path[len(DELIVERIES) + 1:].split("/")
        if len(parts) == 2 and parts[0].startswith(marker) and parts[1] == "delivery.md":
            return f"{DELIVERIES}/{parts[0]}"
    return None


def tree_note(root: Path, commit: str, relative: str) -> tuple[dict, str]:
    plain = reader(root, "plain")
    found = plain.read(f"{commit}:{relative}") if plain else None
    if found is None or found[1] != "blob":
        return split_remote_note(root, commit, relative, delivery_compile.split_note)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", suffix=".md", delete=False) as temporary:
        temporary.write(text_output(found[2]))
        written = Path(temporary.name)
    try:
        return delivery_compile.split_note(written)
    finally:
        written.unlink(missing_ok=True)


def tree_items(root: Path, commit: str, directory: str) -> dict[str, tuple[str, dict]]:
    """Each Story's Item record path and front matter in *commit*'s package *directory*."""
    items = {}
    for path in tree_paths(root, commit, directory + "/items"):
        parts = path[len(directory) + 1:].split("/")
        if len(parts) == 3 and parts[2] == "item.md":
            props, _body = tree_note(root, commit, path)
            items[str(props.get("story_id") or parts[1].upper())] = (path, props)
    return items


def claims_cover(path: str, claims) -> bool:
    """Whether a path claim covers *path*, compared case-folded as a case-insensitive volume would."""
    folded = path.casefold()
    for claim in claims or []:
        if isinstance(claim, str) and delivery_compile._is_normalized_claim(claim):
            value = claim.casefold()
            if folded == value or folded.startswith(value + "/"):
                return True
    return False


def merge_base(root: Path, first: str, second: str) -> str | None:
    """The merge base of two commits, or None when their histories share no commit, as an orphan branch's."""
    return memoized(root, ("merge_base", first, second), lambda: git_merge_base(root, first, second))


def git_merge_base(root: Path, first: str, second: str) -> str | None:
    result = subprocess.run(["git", "merge-base", first, second], cwd=root, encoding="utf-8",
                            capture_output=True, check=False)
    if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
        return None
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git merge-base {first} {second} failed")
    return result.stdout.strip()


def changed_paths(root: Path, base: str, head: str) -> list[str] | None:
    """The paths *head* changes against its merge base with *base*, or None without a merge base."""
    start = merge_base(root, base, head)
    if start is None:
        return None
    return delivery_git.git_paths(root, "--no-replace-objects", "diff", "--no-renames", "--name-only", "-z",
                                  start, head)


def rev_set(root: Path, *args: str) -> set[str]:
    return set(run_git(root, "rev-list", *args, "--").split()) if args else set()


def is_product_path(path: str) -> bool:
    return not path.startswith(PRODUCT_EXCLUDED_ROOT)


def tree_delta(root: Path, before: str, after: str) -> list[str]:
    return list(memoized(root, ("tree_delta", before, after), lambda: delivery_git.git_paths(
        root, "--no-replace-objects", "diff", "--no-renames", "--name-only", "-z", before, after)))


def prefetch_deltas(root: Path, pairs) -> None:
    """Read the tree_delta of every (before, after) commit pair in one `git diff-tree --stdin` within an audit.

    Git lists each pair under its *after* id. A pair Git cannot read, or a
    path that spells a listed id and so makes the output ambiguous, leaves
    every pair to tree_delta's own Git call.
    """
    if _SESSION is None or _SESSION["root"] != root:
        return
    memo = _SESSION["memo"]
    pending = list(dict.fromkeys(
        (before, after) for before, after in pairs
        if delivery_git.OID_RE.fullmatch(before or "") and delivery_git.OID_RE.fullmatch(after or "")
        and ("tree_delta", before, after) not in memo))
    if not pending:
        return
    listed = subprocess.run(["git", "--no-replace-objects", "diff-tree", "--stdin", "--always", "-r", "-z",
                             "--no-renames", "--name-only"], cwd=root,
                            input="".join(f"{after} {before}\n" for before, after in pending).encode("ascii"),
                            capture_output=True, check=False)
    if listed.returncode:
        return
    deltas: list[list[str]] = []
    expected = iter(pending)
    upcoming = next(expected, None)
    try:
        for token in listed.stdout.split(b"\0"):
            name = token.decode("utf-8")
            if name and upcoming is not None and name == upcoming[1]:
                deltas.append([])
                upcoming = next(expected, None)
            elif name and deltas:
                deltas[-1].append(name)
            elif name:
                return
    except UnicodeDecodeError:
        return
    ids = {oid for pair in pending for oid in pair}
    if len(deltas) != len(pending) or any(path in ids for paths in deltas for path in paths):
        return
    for pair, paths in zip(pending, deltas):
        memo[("tree_delta", *pair)] = paths


def blobs(root: Path, commit: str, paths) -> dict[str, str]:
    """The mode and blob each of *paths*, read as literal names, has in *commit*."""
    paths = sorted(set(paths))
    if not paths:
        return {}
    raw = reader(root, "raw")
    entries = {path: raw.entry(commit, path) for path in paths} if raw else {}
    if raw and False not in entries.values():
        # `ls-tree -r` lists what a path names, never a directory itself.
        return {path: f"{entry[0]} {entry[1]}" for path, entry in entries.items()
                if entry and entry[0] != "040000"}
    listing = subprocess.run(["git", "--no-replace-objects", "--literal-pathspecs", "ls-tree", "-r", "-z", commit,
                              "--", *paths], cwd=root, capture_output=True, check=False)
    if listing.returncode:
        raise RuntimeError("DELIVERY_COORDINATION_CORRUPT: cannot read the tree of "
                           + commit + ": " + listing.stderr.decode("utf-8", "replace").strip())
    found = {}
    for row in listing.stdout.split(b"\0"):
        if row:
            metadata, name = row.split(b"\t", 1)
            mode, _kind, oid = metadata.decode("ascii").split()
            found[name.decode("utf-8")] = f"{mode} {oid}"
    return {path: found[path] for path in paths if path in found}


def item_product_blobs(root: Path, product: str, start: str) -> dict[str, str | None]:
    """Each product path the Item changed from *start* to its product tip, with the tip's blob or None."""
    changed = [path for path in tree_delta(root, start, product) if is_product_path(path)]
    held = blobs(root, product, changed)
    return {path: held.get(path) for path in changed}


def holds_item_bytes(root: Path, item: dict[str, str | None], commit: str) -> list[str]:
    """The Item paths whose exact product bytes, or absence, *commit* holds."""
    held = blobs(root, commit, item)
    return sorted(path for path, blob in item.items() if held.get(path) == blob)


def classify(root: Path, remote: str, head: str, base_tip: str, paths: list[str],
             head_ref: str = "", state: dict | None = None, target_tip: str = "", *,
             pr_paths: list[str] | None = None, targets: tuple[str, ...] = (),
             delivery_paths: dict[str, list[str]] | None = None) -> dict:
    """Whether a pull request is managed by an open Delivery, and why.

    A pull request is managed when its head ref is an Agentrof ref, when its
    head holds commits the base lacks that only an open Delivery's Integration
    or Item refs reach, when a path it changes lies under a path claim of a
    not cancelled Item of an open Delivery, or when it writes the exact
    product bytes of such an Item that its base lacks. Commits the base, the
    Fence target or a Delivery target (*target_tip* and *targets*) already
    hold are no Delivery's own, so a promotion between other branches is not
    managed for them; *paths* are what the head changes against its merge
    base with the Delivery target and *pr_paths*, by default the same, what
    it changes against its merge base with its own base. *delivery_paths*
    replaces *paths* for each Delivery it names, as measured from that
    Delivery's own target. The exact bytes
    count only at paths the pull request itself changes, so a branch that
    merely predates an Item's deletion carries nothing of it. Labels, branch
    names outside the Agentrof namespace and PR text never decide it, so
    renaming a branch or removing a label cannot take a PR out of the check.
    An open Delivery is one whose Integration ref exists.
    """
    state = state or coordination_state(root, remote)
    pr_paths = set(paths if pr_paths is None else pr_paths)
    stops = sorted({oid for oid in (base_tip, state.get("fence_target", ""), target_tip, *targets) if oid})
    reasons: list[str] = []
    deliveries: set[str] = set()
    ref = head_ref.removeprefix("refs/heads/")
    if ref.startswith(MANAGED_REF_PREFIX):
        reasons.append(f"its head ref {ref} is an Agentrof coordination ref")
        match = re.fullmatch(r"agentrof/deliveries/(dlv-[0-9]{3,})", ref)
        if match:
            deliveries.add(match.group(1).upper())
    head_only = rev_set(root, head, "--not", base_tip)
    for delivery_id, integration in sorted(state["integrations"].items()):
        items = [dict(item, ref=ref) for ref, item in sorted(state["items"].items())
                 if item["delivery"] == delivery_id]
        tips = [integration] + [item["tip"] for item in items]
        if head_only and head_only & rev_set(root, *tips, "--not", *stops):
            reasons.append(f"its head holds commits of {delivery_id} that the base lacks")
            deliveries.add(delivery_id)
        directory = package_directory(root, integration, delivery_id)
        if directory is not None:
            for story, (_path, props) in sorted(tree_items(root, integration, directory).items()):
                # An integrated Item's claim holds until the Delivery's recorded merge closes it.
                if props.get("status") == "cancelled":
                    continue
                claimed = sorted(path for path in (delivery_paths or {}).get(delivery_id, paths)
                                 if claims_cover(path, props.get("path_claims")))
                if claimed:
                    reasons.append(f"it changes {', '.join(claimed)} under a path claim of {story} of {delivery_id}")
                    deliveries.add(delivery_id)
        for product in sorted(product_tips(root, delivery_id, integration, items)):
            start = product_start(root, product, delivery_id)
            item = item_product_blobs(root, product, start) if start else {}
            item = {path: blob for path, blob in item.items() if path in pr_paths}
            if (item and len(holds_item_bytes(root, item, head)) == len(item)
                    and len(holds_item_bytes(root, item, base_tip)) != len(item)):
                reasons.append(f"it carries the exact product bytes of the Item product tip {product} of {delivery_id}")
                deliveries.add(delivery_id)
    return {"managed": bool(reasons), "reasons": reasons, "deliveries": sorted(deliveries)}


def recovery(step: str) -> str:
    return f"; recovery: {step}"


def parents(root: Path, oid: str) -> list[str]:
    plain = reader(root, "plain")
    found = plain.commit(oid) if plain else None
    return list(found[1]) if found else run_git(root, "show", "-s", "--format=%P", oid).split()


def tree_of(root: Path, oid: str) -> str:
    plain = reader(root, "plain")
    found = plain.commit(oid) if plain else None
    return found[0] if found else run_git(root, "rev-parse", oid + "^{tree}")


def merge_delta_findings(root: Path, merge: str, first: str, second: str) -> list[str]:
    """The product paths whose change in *merge* is not the change its second parent made.

    A coordinator merge resolves only trivially: each product path it changes
    against its first parent holds the second parent's blob, and the first
    parent left it as their merge base had it.
    """
    changed = [path for path in tree_delta(root, first, merge) if is_product_path(path)]
    if not changed:
        return []
    try:
        base = memoized(root, ("unique_merge_base", first, second),
                        lambda: delivery_git.unique_merge_base(root, first, second))
    except RuntimeError as exc:
        return [f"its merge base is not unique: {exc}"]
    merged, ours, theirs, common = (blobs(root, oid, changed) for oid in (merge, first, second, base))
    return sorted(path for path in changed
                  if merged.get(path) != theirs.get(path) or ours.get(path) != common.get(path))


def line_commits(root: Path, head: str, stops: list[str]) -> list[tuple[str, list[str], str]]:
    """The first-parent line from *head* back to *stops*, oldest first, with each commit's parents and message.

    One Git call reads the whole line, once per audit.
    """
    return memoized(root, ("line_commits", head, *stops), lambda: read_line_commits(root, head, stops))


def read_line_commits(root: Path, head: str, stops: list[str]) -> list[tuple[str, list[str], str]]:
    listed = subprocess.run(["git", "--no-replace-objects", "-c", "log.showSignature=false", "log", "--no-color",
                             "--first-parent", "-z", "--format=%H %P%n%B", head,
                             *(("--not", *stops) if stops else ()), "--"],
                            cwd=root, capture_output=True, check=False)
    if listed.returncode:
        raise RuntimeError("DELIVERY_COORDINATION_CORRUPT: cannot read the Integration line of " + head + ": "
                           + listed.stderr.decode("utf-8", "replace").strip())
    commits = []
    for entry in listed.stdout.decode("utf-8", "replace").split("\0"):
        if entry:
            header, _newline, message = entry.partition("\n")
            oid, *lineage = header.split()
            commits.append((oid, lineage, message.strip()))
    return list(reversed(commits))


def line_delta_pairs(root: Path, commits: list[tuple[str, list[str], str]]) -> list[tuple[str, str]]:
    """The commit pairs whose tree_delta integration_line_findings reads for the merges of a line."""
    pairs = []
    for oid, lineage, message in commits:
        if len(lineage) != 2:
            continue
        pairs.append((lineage[0], oid))
        if trailer(message, "Record") == "item-integration-v1":
            with contextlib.suppress(RuntimeError, UnicodeDecodeError):
                pairs.append((trailer(commit_message(root, lineage[1]), "Product-Tip") or "", lineage[1]))
    return pairs


def single_parent_deltas(root: Path, commits: list[str]) -> dict[str, list[str]]:
    """The paths each single-parent commit of *commits* changes against its parent, read in one Git call."""
    if not commits:
        return {}
    listed = subprocess.run(["git", "--no-replace-objects", "diff-tree", "--stdin", "--always", "-r", "-z",
                             "--no-renames", "--name-only"], cwd=root, input="".join(f"{oid}\n" for oid in commits)
                            .encode("ascii"), capture_output=True, check=False)
    if listed.returncode:
        raise RuntimeError("DELIVERY_COORDINATION_CORRUPT: cannot read the Integration line changes: "
                           + listed.stderr.decode("utf-8", "replace").strip())
    deltas: dict[str, list[str]] = {}
    expected, current = iter(commits), None
    upcoming = next(expected, None)
    for token in listed.stdout.decode("utf-8", "replace").split("\0"):
        if token and token == upcoming:
            current, upcoming = token, next(expected, None)
            deltas[current] = []
        elif token and current is not None:
            deltas[current].append(token)
    # A path that spells a commit id makes the batch ambiguous, so each commit is then read alone.
    if set(deltas) != set(commits) or any(path in deltas for paths in deltas.values() for path in paths):
        return {oid: tree_delta(root, oid + "^", oid) for oid in commits}
    return deltas


def strip_code(message: str) -> str:
    return re.sub(r"^[A-Z][A-Z0-9_]+: ", "", message)


def integration_line_findings(root: Path, delivery_id: str, reviewed: str, stops: list[str]) -> list[str]:
    """Every commit on the reviewed Integration's first-parent line that brings product bytes no step owns.

    The line runs from the reviewed Integration back to what the PR base or
    the Fence target already holds. Each commit must be a declared control
    record of this Delivery. A record changes no product path, except an Item
    integration, whose product delta must be its sealed Item's own, a target
    merge, whose delta must come from a target commit, and a cancellation
    revert, which must restore what the Item merge it names replaced. Every
    Item integration, not only a Story's latest, must merge a Story of the
    reviewed package, which a cancelled Story stays only with the revert that
    names that merge, and must carry in its own tree the approved code review
    and passed verification of the product tip its seal names.
    """
    resume = recovery(f"rebuild the Integration through /deliver {delivery_id}; only coordinator records may"
                      " change it")
    commits = line_commits(root, reviewed, stops)
    prefetch_deltas(root, line_delta_pairs(root, commits))
    deltas = single_parent_deltas(root, [oid for oid, lineage, _message in commits if len(lineage) == 1])
    package = package_directory(root, reviewed, delivery_id)
    reviewed_items = tree_items(root, reviewed, package) if package else {}
    reverted = {trailer(message, "Previous-Tip") for _oid, _lineage, message in commits
                if trailer(message, "Record") == "cancellation-revert-v1"}
    errors: list[str] = []
    item_merges: dict[str, str] = {}
    for oid, lineage, message in commits:
        try:
            kind = record_kind(message)
        except RuntimeError as exc:
            errors.append(str(exc))
            continue
        if kind is None or trailer(message, "Delivery") != delivery_id:
            errors.append(f"DELIVERY_COORDINATION_CORRUPT: the Integration commit {oid} is not a control record of"
                          f" {delivery_id}" + resume)
            continue
        if len(lineage) == 2 and kind == "item_integration_v1":
            seal = lineage[1]
            seal_message = commit_message(root, seal)
            product = trailer(seal_message, "Product-Tip") or ""
            story = trailer(message, "Story") or ""
            sealed = (record_kind(seal_message) == "item_integration_v1"
                      and trailer(seal_message, "Delivery") == delivery_id
                      and trailer(seal_message, "Story") == story
                      and trailer(message, "Reviewed-Tip") == seal
                      and delivery_git.OID_RE.fullmatch(product) is not None and has_object(root, product))
            if not sealed or any(is_product_path(path) for path in tree_delta(root, product, seal)):
                errors.append(f"DELIVERY_COORDINATION_CORRUPT: the Item integration {oid} of {delivery_id} does not"
                              " merge a sealed Item whose product bytes are its product tip's" + resume)
                continue
            item_merges[oid] = story
            errors.extend(f"DELIVERY_COORDINATION_CORRUPT: the Item integration {oid} of {delivery_id} {problem}"
                          + resume for problem in merge_evidence_findings(
                              root, delivery_id, oid, seal, seal_message, product, story,
                              reviewed_items, oid in reverted))
        elif len(lineage) == 2 and kind in TARGET_MERGE_RECORDS:
            if not any(is_ancestor(root, lineage[1], stop) for stop in stops):
                errors.append(f"DELIVERY_COORDINATION_CORRUPT: the target merge {oid} of {delivery_id} does not"
                              " merge a commit of the target" + resume)
                continue
        elif len(lineage) == 1 and kind == "cancellation_revert_v1":
            reverted_merge = trailer(message, "Previous-Tip") or ""
            if item_merges.get(reverted_merge) != trailer(message, "Story"):
                errors.append(f"DELIVERY_COORDINATION_CORRUPT: the cancellation revert {oid} of {delivery_id} names"
                              " no Item integration on the Integration line" + resume)
                continue
            first = parents(root, reverted_merge)[0]
            changed = [path for path in deltas[oid] if is_product_path(path)]
            # The coordinator's revert refuses a path a later commit rewrote, so it restores every one.
            merged = [path for path in tree_delta(root, first, reverted_merge) if is_product_path(path)]
            restored, replaced = blobs(root, oid, changed + merged), blobs(root, first, changed + merged)
            wrong = sorted(path for path in changed if restored.get(path) != replaced.get(path))
            kept = sorted(path for path in set(merged) - set(changed) if restored.get(path) != replaced.get(path))
            if wrong:
                errors.append(f"DELIVERY_COORDINATION_CORRUPT: the cancellation revert {oid} of {delivery_id} writes"
                              f" {', '.join(wrong)} other than the reverted Item merge had them" + resume)
            if kept:
                errors.append(f"DELIVERY_COORDINATION_CORRUPT: the cancellation revert {oid} of {delivery_id} leaves"
                              f" {', '.join(kept)} as the reverted Item merge wrote them" + resume)
            continue
        elif len(lineage) == 1:
            changed = sorted(path for path in deltas[oid] if is_product_path(path))
            if changed:
                errors.append(f"DELIVERY_COORDINATION_CORRUPT: the {kind} record {oid} of {delivery_id} changes the"
                              f" product paths {', '.join(changed)}, which no such record writes" + resume)
            continue
        else:
            errors.append(f"DELIVERY_COORDINATION_CORRUPT: the {kind} record {oid} of {delivery_id} has"
                          f" {len(lineage)} parents, which no such record has" + resume)
            continue
        wrong = merge_delta_findings(root, oid, lineage[0], lineage[1])
        if wrong:
            errors.append(f"DELIVERY_COORDINATION_CORRUPT: the {kind} merge {oid} of {delivery_id} writes"
                          f" {', '.join(wrong)} other than its merged side had them" + resume)
    return errors


def merge_evidence_findings(root: Path, delivery_id: str, merge: str, seal: str, seal_message: str, product: str,
                            story: str, reviewed_items: dict, reverted: bool) -> list[str]:
    """Why one Item integration on the line does not merge a reviewed Story's own evidenced product.

    The Story must be one the reviewed package holds, and not cancelled there
    unless a cancellation revert names this merge. The merge's own tree must
    hold the integrated Item and the evidence that integrate-item checked
    for the product tip its seal names, so each integration of a Story, as
    a reopen yields several, carries its own.
    """
    listed = reviewed_items.get(story)
    if listed is None:
        return [f"merges {story}, which the reviewed package of {delivery_id} does not hold"]
    item_path, reviewed_props = listed
    if reviewed_props.get("status") == "cancelled" and not reverted:
        return [f"merges {story}, which the reviewed package cancelled, and no cancellation revert names it"]
    found = {"merge": merge, "seal": seal, "evidence": trailer(seal_message, "Reviewed-Tip") or "",
             "product": product}
    if parents(root, seal) != [found["evidence"]]:
        return [f"merges a seal of {story} that is not the exact child of its Item evidence"]
    try:
        item_props = tree_note(root, merge, item_path)[0]
    except (RuntimeError, ValueError) as exc:
        return [f"holds no readable Item record of {story}: {exc}"]
    if item_props.get("status") != "integrated":
        return [f"holds {story} as {item_props.get('status') or 'without a status'}, not integrated"]
    return [f"lacks its own evidence for {story}: {strip_code(problem)}"
            for problem in evidence_findings(root, merge, delivery_id, story, item_path, item_props, found)]


def record_binding_findings(root: Path, delivery_id: str, chain: dict, stops: list[str],
                            recompute_notes: bool = True) -> list[str]:
    """Every way the PR record, its intent and its published Review carry more than their verbs write.

    The notes open-pr authors on the intent, the Review's PR URL and the
    Delivery's awaiting_merge status, must hold exactly the bytes it writes;
    every other path the record changes is a projection it re-renders, which
    a later package release may render differently, so it may only be a
    non-product path under the workspace docs. The intent's tree is its
    Review's; the Review changes no product path; it reviewed the Integration
    its note names; and the reviewed Integration line holds no product bytes
    no step owns. Without *recompute_notes*, as for a merged record whose
    Integration ref verify-merge dropped, the notes open-pr authors are not
    recomputed, since a later package release may write them differently,
    and every path the record changes must only stay outside the product.
    """
    resume = recovery(f"close this PR; /deliver {delivery_id} records the PR again on the reviewed Integration")
    record, intent, review, reviewed = (chain[key] for key in ("record", "intent", "review", "reviewed_integration"))
    errors: list[str] = []
    package = package_directory(root, review, delivery_id)
    if package is None:
        return [f"DELIVERY_COORDINATION_CORRUPT: the published Review of {delivery_id} holds no package" + resume]
    try:
        url, _number = canonical_github_pr(str(tree_note(root, record, f"{package}/delivery-review.md")[0]
                                               .get("pull_request_url", "")))
        replacements = (delivery_git.pr_record_replacements(root, intent, package, url)[0]
                        if recompute_notes else {})
        reviewed_note = tree_note(root, review, f"{package}/delivery-review.md")[0]
    except (RuntimeError, ValueError) as exc:
        return [f"DELIVERY_COORDINATION_CORRUPT: the PR record of {delivery_id} cannot be recomputed: {exc}" + resume]
    if _SESSION is not None:
        try:
            line = line_commits(root, reviewed, stops)
        except RuntimeError:
            line = []
        prefetch_deltas(root, [(intent, record), (reviewed, review), *line_delta_pairs(root, line)])
    held = blobs(root, record, replacements)
    wrong = sorted(path for path, text in replacements.items() if held.get(path) != f"100644 {blob_oid(root, text)}")
    wrong += sorted(path for path in tree_delta(root, intent, record)
                    if path not in replacements and is_product_path(path))
    if wrong:
        errors.append(f"DELIVERY_COORDINATION_CORRUPT: the PR record {record} of {delivery_id} is not the tree open-pr"
                      f" writes on its intent; it differs at {', '.join(wrong)}" + resume)
    if tree_of(root, intent) != tree_of(root, review):
        errors.append(f"DELIVERY_COORDINATION_CORRUPT: the PR intent {intent} of {delivery_id} changes the tree of"
                      f" its published Review, which an intent never does" + resume)
    # Publishing writes the package notes and re-renders vault projections, never a product path.
    foreign = sorted(path for path in tree_delta(root, reviewed, review) if is_product_path(path))
    if foreign:
        errors.append(f"DELIVERY_COORDINATION_CORRUPT: the published Review {review} of {delivery_id} changes"
                      f" {', '.join(foreign)}, which publishing a Review never writes" + resume)
    if reviewed_note.get("reviewed_integration_commit") != reviewed:
        errors.append(f"DELIVERY_REVIEW_STALE: the published Review of {delivery_id} reviewed"
                      f" {reviewed_note.get('reviewed_integration_commit')}, not the Integration {reviewed} its record"
                      " names" + recovery(f"approve and publish the Delivery Review again in /deliver {delivery_id}"))
    return errors + integration_line_findings(root, delivery_id, reviewed, stops)


def pr_record_chain(root: Path, head: str, delivery_id: str, url: str,
                    stops: list[str], recompute_notes: bool = True) -> tuple[dict, list[str]]:
    """Walk the recorded PR head back to its intent and published Review, each exactly.

    The head must be the PR record that binds this PR, whose only parent is
    the intent it names; the intent's only parent is its Review-Head, the
    published Review, whose only parent is the Integration it reviewed. Each
    of them, and the Integration line down to *stops*, must carry only what
    its verb writes.
    """
    chain: dict = {}
    errors: list[str] = []
    resume = recovery(f"/deliver {delivery_id} runs open-pr, which records this PR on the reviewed Integration head")

    message = commit_message(root, head)
    if record_kind(message) != "pr_url_recorded_v1" or trailer(message, "Delivery") != delivery_id:
        return chain, [f"DELIVERY_CLOSURE_INCOMPLETE: the PR head {head} is not the PR record of {delivery_id}" + resume]
    if not delivery_git.binds_pr(message, url):
        errors.append(f"DELIVERY_PR_HEAD_BASE_MISMATCH: the PR record of {delivery_id} binds another PR than {url}"
                      + recovery(f"merge only the PR that the record names, through merge-pr in /deliver {delivery_id}"))
    intent = trailer(message, "Intent") or ""
    if parents(root, head) != [intent]:
        return chain, errors + [f"DELIVERY_COORDINATION_CORRUPT: the PR record of {delivery_id} is not the exact child"
                                " of the intent it names" + resume]
    intent_message = commit_message(root, intent)
    review = trailer(intent_message, "Review-Head") or ""
    if (record_kind(intent_message) not in PR_INTENT_RECORDS or trailer(intent_message, "Delivery") != delivery_id
            or parents(root, intent) != [review]):
        return chain, errors + [f"DELIVERY_COORDINATION_CORRUPT: the PR intent of {delivery_id} is not the exact child"
                                " of its published Review" + resume]
    review_message = commit_message(root, review)
    reviewed = trailer(review_message, "Reviewed-Integration") or ""
    if (record_kind(review_message) != "delivery_review_published_v1"
            or trailer(review_message, "Delivery") != delivery_id or parents(root, review) != [reviewed]):
        return chain, errors + [f"DELIVERY_REVIEW_STALE: the PR intent of {delivery_id} does not follow its published"
                                " Delivery Review" + recovery(f"approve and publish the Delivery Review again in"
                                                              f" /deliver {delivery_id}, then open-pr")]
    chain.update(record=head, intent=intent, review=review, reviewed_integration=reviewed,
                 approval_hash=trailer(review_message, "Approval-Hash"))
    return chain, errors + record_binding_findings(root, delivery_id, chain, stops, recompute_notes)


def blob_oid(root: Path, text: str) -> str:
    """The id of the blob that holds *text* as exact UTF-8 bytes, written nowhere."""
    hashed = delivery_git.git_with_input(root, ["hash-object", "--stdin"], text)
    if hashed.returncode:
        raise RuntimeError(hashed.stderr.strip() or "cannot hash a recomputed note")
    return hashed.stdout.strip()


def target_before_merge(root: Path, record: str, target: str) -> str:
    """The target-side parent of the first commit on *target*'s first-parent line that holds *record*.

    That is the target as it was before it merged the record. A record on the
    line itself, as a fast-forward leaves it, has no such parent, so "" lets
    the proof walk the whole line.
    """
    line = run_git(root, "rev-list", "--first-parent", target, "--not", record, "--").split()
    low, high = 0, len(line)
    # The line runs newest first, and every commit that holds the record precedes every one that does not.
    while low < high:
        middle = (low + high) // 2
        if is_ancestor(root, record, line[middle]):
            low = middle + 1
        else:
            high = middle
    if low == 0:
        return ""
    lineage = parents(root, line[low - 1])
    before = lineage[0] if lineage else ""
    return "" if not before or is_ancestor(root, record, before) else before


def own_line_stop(root: Path, delivery_id: str, head: str, stop: str) -> bool:
    """Whether *stop* lies on *head*'s own first-parent line at or above a commit of this Delivery.

    The line enters the target's history below the Delivery's reservation, so
    an honest stop on it is a target commit that holds no commit of the
    Delivery; one that does would hide the line commits below it.
    """
    line = run_git(root, "rev-list", "--first-parent", head, "--not", stop, "--").split()
    if (parents(root, line[-1])[:1] if line else [head]) != [stop]:
        return False
    if not delivery_in_history(root, delivery_id, stop):
        return False
    listed = run_git(root, "rev-list", "--first-parent", "--fixed-strings", f"--grep=Agentrof-Delivery: {delivery_id}",
                     stop, "--")
    return any(delivery_trailer_lines(commit_message(root, oid), delivery_id) for oid in listed.split())


def delivery_in_history(root: Path, delivery_id: str, stop: str) -> bool:
    """Whether any commit *stop* holds may carry this Delivery's trailer line; True outside an audit.

    An audit scans each commit's history once for all Deliveries: a later
    stop lists only the commits no earlier stop held, so the Deliveries
    found so far cover every commit any scanned stop holds.
    """
    if _SESSION is None or _SESSION["root"] != root or not delivery_git.OID_RE.fullmatch(stop):
        return True
    scan = _SESSION["memo"].setdefault("delivery_commits", {"stops": [], "found": set()})
    if stop not in scan["stops"]:
        listed = run_git(root, "rev-list", "--fixed-strings", "--grep=Agentrof-Delivery: ", stop,
                         "--not", *scan["stops"], "--")
        for oid in listed.split():
            scan["found"].update(line.strip().removeprefix("Agentrof-Delivery: ")
                                 for line in commit_message(root, oid).splitlines()
                                 if line.strip().startswith("Agentrof-Delivery: "))
        scan["stops"].append(stop)
    return delivery_id in scan["found"]


def proof_stops(root: Path, delivery_id: str, head: str, target: str, fence: str) -> tuple[list[str], list[str]]:
    """Where the Integration line proof of *head* stops, and the findings that keep a stop out of it.

    The proof stops at the target, or, once the target holds *head*, at the
    target as it was before the merge, so a merged line is still walked. The
    Fence target is a stop only when that target holds it; a Fence target
    the target never had could hide any commit below it, so it is drift. A
    Fence target that holds the merged *head*, as a handoff after the merge
    leaves it, is post-merge state and neither a stop nor drift. A head the
    target holds on its own first-parent line, as a fast-forward leaves it,
    has no target before the merge, so it is refused and gets no Fence stop.
    A stop on *head*'s own first-parent line at or above a commit of the
    Delivery is refused.
    """
    merged = bool(target) and is_ancestor(root, head, target)
    base = target_before_merge(root, head, target) if merged else target
    stops: set[str] = set()
    findings = []
    resume = recovery(f"the project owner decides in /deliver {delivery_id}; the target never held this commit"
                      " before the Delivery's merge")
    if merged and not base:
        findings.append(f"DELIVERY_COORDINATION_CORRUPT: the target holds the recorded PR head of {delivery_id} on its"
                        " own first-parent line, as a fast-forward leaves it, so no target before its merge bounds"
                        " its proof" + recovery(f"the project owner decides in /deliver {delivery_id}; merge-pr"
                                                " merges the recorded head only with a two-parent merge"))
    for name, stop in (("target", base), ("Fence target", fence)):
        if not stop:
            continue
        if name == "Fence target":
            if merged and (not base or is_ancestor(root, head, stop)):
                continue
            if not base or not is_ancestor(root, stop, base):
                findings.append(f"DELIVERY_TARGET_DRIFT: the Fence target {stop} of {delivery_id} is not in the target"
                                " history its recorded PR head is proven against"
                                + recovery(f"run refresh-target through /deliver {delivery_id} against the Delivery's"
                                           " target"))
                continue
        if own_line_stop(root, delivery_id, head, stop):
            findings.append(f"DELIVERY_COORDINATION_CORRUPT: the {name} {stop} the proof of {delivery_id} would stop at"
                            " lies on its recorded PR head's own line after a commit of the Delivery" + resume)
            continue
        stops.add(stop)
    return sorted(stops), findings


def recorded_head_findings(root: Path, remote: str, delivery_id: str, head: str, url: str) -> list[str]:
    """Everything that keeps *head* from being the PR record the coordinator wrote for *url*.

    merge-pr runs it before its provider call and, with verify-merge, after
    the provider merged, when the proof walks the line against the target
    as it was before that merge.
    """
    _branch, target = delivery_git.fetch_target(root, remote)
    stops, drift = proof_stops(root, delivery_id, head, target, fence_target(root, remote))
    _chain, errors = pr_record_chain(root, head, delivery_id, url, stops)
    return drift + errors


def item_integration(root: Path, head: str, delivery_id: str, story: str) -> dict | None:
    """The latest integration of *story* on *head*'s first-parent line: its merge, seal, evidence and product tips."""
    listed = run_git(root, "rev-list", "--first-parent", "--merges", "--fixed-strings", "--all-match",
                     "--grep=Agentrof-Record: item-integration-v1", f"--grep=Agentrof-Delivery: {delivery_id}",
                     f"--grep=Agentrof-Story: {story}", head, "--")
    for merge in listed.split():
        message = commit_message(root, merge)
        merge_parents = parents(root, merge)
        seal = trailer(message, "Reviewed-Tip") or ""
        if (trailer(message, "Story") != story or len(merge_parents) != 2 or merge_parents[1] != seal):
            continue
        seal_message = commit_message(root, seal)
        evidence = trailer(seal_message, "Reviewed-Tip") or ""
        product = trailer(seal_message, "Product-Tip") or ""
        if parents(root, seal) != [evidence]:
            return None
        return {"merge": merge, "seal": seal, "evidence": evidence, "product": product}
    return None


def item_findings(root: Path, head: str, delivery_id: str, story: str, item_path: str,
                  item_props: dict) -> list[str]:
    """Readiness of one Item in the PR head: integrated with exact evidence, or cancelled."""
    status = item_props.get("status")
    if status == "cancelled":
        return []
    resume = recovery(f"finish {story} through /deliver {delivery_id}: integrate-item merges only an Item with"
                      " approved code review and passed verification, then approve and publish the Delivery Review")
    if status != "integrated":
        return [f"DELIVERY_ITEM_NOT_READY: {story} of {delivery_id} is {status or 'without a status'}, not integrated"
                " or cancelled" + resume]
    found = item_integration(root, head, delivery_id, story)
    if found is None:
        return [f"DELIVERY_ITEM_NOT_READY: the PR head holds no exact integration of {story} of {delivery_id}" + resume]
    return [error + resume for error in evidence_findings(root, head, delivery_id, story, item_path, item_props, found)]


def evidence_findings(root: Path, head: str, delivery_id: str, story: str, item_path: str, item_props: dict,
                      found: dict) -> list[str]:
    """Why the Item evidence in *head*'s tree does not bind the integration *found* as integrate-item checks it."""
    directory = item_path.rsplit("/", 1)[0]
    errors = []
    try:
        review, review_body = tree_note(root, head, f"{directory}/code-review.md")
        verification, verification_body = tree_note(root, head, f"{directory}/verification.md")
    except (RuntimeError, ValueError) as exc:
        return [f"DELIVERY_ITEM_NOT_READY: the evidence of {story} of {delivery_id} cannot be read: {exc}"]
    product = found["product"]
    evidence_parents = parents(root, found["evidence"])
    if evidence_parents != [product]:
        errors.append(f"DELIVERY_ITEM_NOT_READY: the evidence of {story} of {delivery_id} is not the exact child of its"
                      " product tip")
    if review.get("status") != "approved" or verification.get("status") != "passed":
        errors.append(f"DELIVERY_ITEM_NOT_READY: {story} of {delivery_id} lacks approved code review and passed"
                      " verification")
    if review.get("reviewed_commit") != product or verification.get("verified_commit") != product:
        errors.append(f"DELIVERY_ITEM_NOT_READY: the evidence of {story} of {delivery_id} does not bind its product tip")
    plan = item_props.get("item_plan_hash")
    if review.get("item_plan_hash") != plan or verification.get("item_plan_hash") != plan:
        errors.append(f"DELIVERY_ITEM_NOT_READY: the evidence of {story} of {delivery_id} does not bind its Item plan")
    for name, props, body in (("code review", review, review_body), ("verification", verification, verification_body)):
        if props.get("source_hash") != delivery_compile.content_hash(props, body):
            errors.append(f"DELIVERY_ITEM_NOT_READY: the {name} source_hash of {story} of {delivery_id} is stale")
    from delivery_verification import validate_evidence
    try:
        validate_evidence(item_props, review, verification)
    except (RuntimeError, ValueError) as exc:
        errors.append(f"{exc} ({story} of {delivery_id})")
    for oid in (found["seal"], product):
        if not is_ancestor(root, oid, head):
            errors.append(f"DELIVERY_ITEM_NOT_READY: the PR head does not contain the Item tip {oid} of {story}")
    return errors


@contextlib.contextmanager
def materialized(root: Path, commit: str):
    """A detached, disposable worktree of *commit* for the compilers to read.

    No hook runs and no Git LFS smudge filter fetches anything: the tree is
    data. The worktree and its administrative files are removed afterwards.
    """
    with tempfile.TemporaryDirectory(prefix="delivery-closure-") as temporary:
        hooks = Path(temporary) / "hooks"
        hooks.mkdir()
        tree = Path(temporary) / "tree"
        environment = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"}
        added = subprocess.run(["git", "-c", f"core.hooksPath={hooks}", "worktree", "add", "--detach", "--quiet",
                                str(tree), commit], cwd=root, env=environment, capture_output=True, check=False)
        if added.returncode:
            raise RuntimeError("DELIVERY_COORDINATION_CORRUPT: the PR head cannot be read as a tree: "
                               + added.stderr.decode("utf-8", "replace").strip())
        try:
            yield tree
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=root,
                           capture_output=True, check=False)
            subprocess.run(["git", "worktree", "prune"], cwd=root, capture_output=True, check=False)


def binding_findings(tree: Path, delivery_id: str) -> list[str]:
    """The Delivery compiler's findings for the package in *tree*: sources, pins, Definition of Done and decisions.

    This is the one place closure checks pin and plan bindings, so an alias
    the source resolver accepts reaches the closure check unchanged.
    """
    docs = delivery_compile.docs_root(tree)
    _root, findings = delivery_compile.delivery_findings(docs, delivery_id)
    return [f"DELIVERY_CLOSURE_INCOMPLETE: {finding}" for finding in findings]


def check_managed(root: Path, remote: str, *, delivery_id: str, head: str, base: str, base_tip: str,
                  url: str, state: dict, bindings: bool = True) -> list[str]:
    """Every reason the managed PR at *head* cannot close *delivery_id*, each naming its recovery step.

    With *bindings*, the Delivery compiler also checks the head's sources and
    pins in a disposable worktree; the audit reads Git objects alone.
    """
    integration = state["integrations"].get(delivery_id)
    if integration != head:
        try:
            merged = bool(integration) and delivery_compile.merged_pr_record(root, delivery_id, base_tip) == integration
        except delivery_compile.MergeStateUnknown:
            merged = False
        if merged:
            return [f"DELIVERY_CLOSURE_INCOMPLETE: {delivery_id} is merged, but its Integration ref still holds its"
                    " claims" + recovery(f"verify-merge {delivery_id} drops its Integration and integrated Item refs;"
                                         " then re-run this check")]
        where = f"the Integration tip {integration}" if integration else "no Integration ref"
        return [f"DELIVERY_PR_HEAD_BASE_MISMATCH: the PR head {head} is not the recorded Integration head of"
                f" {delivery_id}, which has {where}; only the Delivery PR that open-pr records on the reviewed"
                " Integration head can merge Delivery work"
                + recovery(f"close this PR and continue through /deliver {delivery_id}")]
    fence = state.get("fence_target", "")
    # A Fence target the base lacks is reported below as drift, so only the other findings are taken here.
    stops, located = proof_stops(root, delivery_id, head, base_tip, fence)
    chain, errors = pr_record_chain(root, head, delivery_id, url, stops)
    errors = [finding for finding in located if not finding.startswith("DELIVERY_TARGET_DRIFT")] + errors
    if not chain:
        return errors
    directory = package_directory(root, head, delivery_id)
    if directory is None:
        return errors + [f"DELIVERY_COORDINATION_CORRUPT: the PR head holds no package of {delivery_id}"]
    try:
        delivery_props, delivery_body = tree_note(root, head, f"{directory}/delivery.md")
        review_props, review_body = tree_note(root, head, f"{directory}/delivery-review.md")
    except (RuntimeError, ValueError) as exc:
        return errors + [f"DELIVERY_COORDINATION_CORRUPT: the PR head's package of {delivery_id} cannot be read: {exc}"]
    status = delivery_props.get("status")
    if status not in {"awaiting_merge", "cancelled"}:
        errors.append(f"DELIVERY_CLOSURE_INCOMPLETE: {delivery_id} is {status} in the PR head, not awaiting_merge"
                      " or cancelled" + recovery(f"approve the Delivery Review and run open-pr in /deliver {delivery_id}"))
    recomputed = delivery_compile.content_hash(review_props, review_body,
                                               exclude=delivery_compile.MUTABLE | {"approval_hash"})
    if (review_props.get("status") != "approved" or review_props.get("approval_hash") != recomputed
            or chain["approval_hash"] != recomputed):
        errors.append(f"DELIVERY_REVIEW_STALE: the Delivery Review of {delivery_id} in the PR head is not the approved"
                      " Review its published record binds"
                      + recovery(f"approve and publish the Delivery Review again in /deliver {delivery_id}"))
    if review_props.get("pull_request_url") != url:
        errors.append(f"DELIVERY_PR_HEAD_BASE_MISMATCH: the Delivery Review of {delivery_id} records another PR"
                      + recovery(f"merge only the recorded PR, through merge-pr in /deliver {delivery_id}"))
    if status != "cancelled":
        for story, (path, props) in sorted(tree_items(root, head, directory).items()):
            errors.extend(item_findings(root, head, delivery_id, story, path, props))
    for ref, slot in sorted(state["slots"].items()):
        if slot["delivery"] == delivery_id:
            errors.append(f"DELIVERY_CLOSURE_INCOMPLETE: {ref.removeprefix('refs/heads/')} still holds"
                          f" {slot['story'] or 'an Item'} of {delivery_id}"
                          + recovery(f"finish the Item with integrate-item or cancel it with cancel-delivery in"
                                     f" /deliver {delivery_id}; each releases its Slot atomically"))
    pending = delivery_compile.pending_decisions(delivery_body)
    if pending:
        errors.append(f"DELIVERY_DECISION_PENDING: {delivery_compile.pending_rows_text(pending)} in {delivery_id}"
                      + recovery(f"answer them at gate B of /deliver {delivery_id}"))
    target_branch = delivery_props.get("target_branch")
    if isinstance(target_branch, str) and target_branch and target_branch != base:
        errors.append(f"DELIVERY_PR_HEAD_BASE_MISMATCH: the PR targets {base}, but {delivery_id} targets"
                      f" {target_branch}" + recovery(f"merge only the Delivery PR into {target_branch}"))
    if not fence or not is_ancestor(root, fence, base_tip):
        errors.append(f"DELIVERY_TARGET_DRIFT: the PR base {base} does not hold the Fence target of {delivery_id}"
                      + recovery(f"run refresh-target through /deliver {delivery_id} against the Delivery's target"))
    if bindings:
        with materialized(root, head) as tree:
            errors.extend(binding_findings(tree, delivery_id))
    return errors


def delivery_target_tips(root: Path, remote: str, state: dict) -> dict[str, str]:
    """The remote tip of the target branch each open Delivery's package names, where that branch exists."""
    tips = {}
    for delivery_id, integration in sorted(state["integrations"].items()):
        directory = package_directory(root, integration, delivery_id)
        try:
            branch = tree_note(root, integration, f"{directory}/delivery.md")[0].get("target_branch") if directory else None
            if isinstance(branch, str) and branch and listed_refs(root, remote, f"refs/heads/{branch}"):
                tips[delivery_id] = remote_branch_tip(root, remote, branch)
        except (RuntimeError, ValueError):
            continue
    return tips


def check_pull_request(project_root: Path, *, head: str, base: str, url: str, head_ref: str = "",
                       remote: str = "origin", target: str = "") -> dict:
    """The closure check one pull request runs: unmanaged PRs pass, managed PRs must be closure-ready.

    A CI checkout is a detached commit with no remote HEAD and no current
    branch, so nothing here guesses the target from them: the workflow names
    the repository's default branch as *target*, the base stands in without
    one, and each open Delivery's package names its own target branch.
    """
    root = delivery_git.main_worktree(project_root.resolve())
    canonical, _number = canonical_github_pr(url)
    if not delivery_git.OID_RE.fullmatch(head) or not has_object(root, head):
        raise RuntimeError("DELIVERY_INPUT_INVALID: the PR head must be an exact commit this checkout holds")
    base_tip = remote_branch_tip(root, remote, base)
    target_tip = remote_branch_tip(root, remote, target) if target and target != base else base_tip
    state = coordination_state(root, remote)
    tips = delivery_target_tips(root, remote, state)
    targets = tuple(sorted(set(tips.values())))
    # A base that shares no history with the head, as an orphan pages branch, compares trees directly.
    pr_paths = changed_paths(root, base_tip, head)
    if pr_paths is None:
        pr_paths = tree_delta(root, base_tip, head)

    def claimable(tip: str) -> list[str]:
        # A PR claims only paths it changes itself; a promotion still changes them against its base.
        measured = changed_paths(root, tip, head)
        return sorted(set(pr_paths if measured is None else measured) & set(pr_paths))

    claimed_paths = claimable(target_tip)
    delivery_paths = {delivery_id: claimable(tips[delivery_id]) if delivery_id in tips else claimed_paths
                      for delivery_id in state["integrations"]}
    classified = classify(root, remote, head, base_tip, claimed_paths, head_ref, state, target_tip,
                          pr_paths=pr_paths, targets=targets, delivery_paths=delivery_paths)
    result = {"ok": True, "managed": classified["managed"], "reasons": classified["reasons"],
              "observations": [{"kind": "provider", "target": "pull_request_head", "value": head},
                               {"kind": "ref", "target": "closure/classification",
                                "value": "managed" if classified["managed"] else "not_managed"}],
              "findings": gate_change_findings(pr_paths, base)}
    if not classified["managed"]:
        return result
    candidates = sorted({*classified["deliveries"],
                         *(delivery for delivery, tip in state["integrations"].items() if tip == head)})
    if not candidates:
        result["errors"] = ["DELIVERY_PR_HEAD_BASE_MISMATCH: the PR is managed because "
                            + "; ".join(classified["reasons"]) + ", but no open Delivery records it"
                            + recovery("open Delivery work only through /deliver DLV-###, which opens its PR")]
    else:
        result["errors"] = [error for delivery_id in candidates for error in check_managed(
            root, remote, delivery_id=delivery_id, head=head, base=base, base_tip=base_tip, url=canonical,
            state=state)]
        result["observations"] += [{"kind": "ref", "target": f"closure/{delivery_id}", "value": "managed"}
                                   for delivery_id in candidates]
    result["ok"] = not result["errors"]
    return result


def gate_change_findings(paths: list[str], base: str) -> list[dict]:
    """A warning for a pull request that changes the closure workflow or the archive it runs.

    The base branch's copy still runs for this pull request, but after the
    merge the changed copy decides every later one, so the change needs the
    project owner's review whatever the check reports.
    """
    changed = sorted(path for path in paths if path in CLOSURE_GATE_PATHS)
    if not changed:
        return []
    return [{"code": "DELIVERY_CLOSURE_GATE_CHANGED", "severity": "warning", "refs": [base], "paths": changed,
             "next_entry": None,
             "message": f"this pull request changes {', '.join(changed)}, which decides the closure check of every"
                        f" later pull request into {base}; the project owner reviews it before the merge, and"
                        " protects these paths with CODEOWNERS or a ruleset"}]


def product_tips(root: Path, delivery_id: str, integration: str, items: list[dict]) -> set[str]:
    """The product tips the Delivery's Items recorded, from their Item refs and the Integration."""
    tips = set()
    for item in items:
        message = commit_message(root, item["tip"])
        kind = record_kind(message)
        if kind == "item_evidence_v1":
            tips.add(trailer(message, "Product-Tip") or "")
        elif kind == "item_integration_v1":
            seal = trailer(message, "Reviewed-Tip") or ""
            if seal and has_object(root, seal):
                tips.add(trailer(commit_message(root, seal), "Product-Tip") or "")
    if integration:
        directory = package_directory(root, integration, delivery_id)
        for story in tree_items(root, integration, directory) if directory else {}:
            found = item_integration(root, integration, delivery_id, story)
            if found:
                tips.add(found["product"])
    return {tip for tip in tips if delivery_git.OID_RE.fullmatch(tip) and has_object(root, tip)}


def product_start(root: Path, product: str, delivery_id: str) -> str | None:
    """The nearest first-parent ancestor of *product* that a coordinator record of this Delivery wrote."""
    for oid in run_git(root, "rev-list", "--first-parent", product, "--").split():
        if delivery_trailer_lines(commit_message(root, oid), delivery_id):
            return oid
    return None


def external_product_merge(root: Path, delivery_id: str, target: str, integration: str,
                           items: list[dict], fence: str = "") -> tuple[list[str], list[str]]:
    """The evidence that this Delivery's work reached *target* without its recorded-head merge.

    Returns the proof, a commit, product tip or package of this Delivery in
    the target's history, and the qualified signs: Item product bytes the
    target holds that the Fence target *fence* did not, which an independent
    change can also write, so they alone never make an external merge.
    """
    evidence, signs = [], []
    listed = run_git(root, "rev-list", "--fixed-strings", f"--grep=Agentrof-Delivery: {delivery_id}", target, "--")
    if any(delivery_trailer_lines(commit_message(root, oid), delivery_id) for oid in listed.split()):
        evidence.append(f"the target holds a commit of {delivery_id}")
    for product in sorted(product_tips(root, delivery_id, integration, items)):
        start = product_start(root, product, delivery_id)
        if is_ancestor(root, product, target):
            evidence.append(f"the target holds the Item product tip {product}")
            continue
        item = item_product_blobs(root, product, start) if start else {}
        before = set(holds_item_bytes(root, item, fence)) if fence else set()
        held = [path for path in holds_item_bytes(root, item, target) if path not in before]
        missing = sorted(set(item) - set(held) - before)
        if held and not missing:
            signs.append(f"the target holds the exact bytes of the Item product tip {product} at {', '.join(held)},"
                         " which an independent change can also write")
        elif held:
            signs.append(f"the target holds the exact bytes of the Item product tip {product} at {', '.join(held)}"
                         f" but not at {', '.join(missing)}, which a partial hand merge leaves")
    directory = package_directory(root, target, delivery_id)
    if directory:
        integrated = sorted(story for story, (_path, props) in tree_items(root, target, directory).items()
                            if props.get("status") == "integrated")
        if integrated:
            evidence.append(f"the target's package records integrated Items {', '.join(integrated)}")
    return evidence, signs


def writer_receipts(root: Path, delivery_id: str, state: dict | None = None) -> list[str]:
    """This Delivery's writer receipts, or with *state* only those that bind a live writer.

    A receipt binds a live writer while its Story's Item ref names this
    Delivery and the Item there is neither integrated nor cancelled; a
    cancelled or integrated Story's receipt is stale runtime state no closure
    step needs. An unreadable receipt counts, as nothing proves it stale.
    """
    folder = delivery_git.runtime_root(root) / "receipts"
    prefix = f"item-{delivery_id.lower()}-"
    names = sorted(path.name for path in folder.glob(prefix + "*.json")) if folder.is_dir() else []
    if state is None:
        return names
    live = []
    for name in names:
        try:
            story = str(json.loads((folder / name).read_text(encoding="utf-8"))["story"])
            ref = canonical_refs(delivery_id, story)["item"]
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            live.append(name)
            continue
        item = state["items"].get(ref)
        if item is None or item["delivery"] != delivery_id:
            continue
        directory = package_directory(root, item["tip"], delivery_id)
        props = tree_items(root, item["tip"], directory).get(story.upper(), ("", {}))[1] if directory else {}
        if props.get("status") not in delivery_compile.TERMINAL_ITEM_STATUSES:
            live.append(name)
    return live


def known_deliveries(root: Path, target: str, state: dict) -> list[str]:
    identifiers = set(state["integrations"])
    for path in tree_paths(root, target, DELIVERIES):
        match = re.match(r"(dlv-[0-9]{3,})-", path[len(DELIVERIES) + 1:])
        if match:
            identifiers.add(match.group(1).upper())
    docs = delivery_compile.docs_root(root)
    for directory in delivery_compile.delivery_dirs(docs):
        match = re.match(r"(dlv-[0-9]{3,})-", directory.name)
        if match:
            identifiers.add(match.group(1).upper())
    return sorted(identifiers)


def recorded_head_readiness(root: Path, remote: str, delivery_id: str, integration: str, branch: str,
                            target: str, state: dict) -> list[str]:
    """Everything that keeps the recorded PR head at the Integration tip from closing the Delivery."""
    directory = package_directory(root, integration, delivery_id)
    if directory is None:
        return [f"DELIVERY_COORDINATION_CORRUPT: the recorded PR head holds no package of {delivery_id}"]
    try:
        url = str(tree_note(root, integration, f"{directory}/delivery-review.md")[0].get("pull_request_url", ""))
        canonical, _number = canonical_github_pr(url)
    except (RuntimeError, ValueError) as exc:
        return [f"DELIVERY_COORDINATION_CORRUPT: the recorded PR head of {delivery_id} names no PR: {exc}"]
    return check_managed(root, remote, delivery_id=delivery_id, head=integration, base=branch,
                         base_tip=target, url=canonical, state=state, bindings=False)


def audit_delivery(root: Path, delivery_id: str, target: str, state: dict, remote: str = "origin",
                   branch: str = "") -> dict:
    """One Delivery's closure outcome, with the leftovers or evidence behind it and its recovery owner.

    An awaiting_merge Delivery also carries what keeps its recorded PR head
    from closing it, such as a mismatched PR head, draft evidence or a Slot.
    """
    integration = state["integrations"].get(delivery_id, "")
    items = [dict(item, ref=ref) for ref, item in sorted(state["items"].items()) if item["delivery"] == delivery_id]
    slots = sorted(ref for ref, slot in state["slots"].items() if slot["delivery"] == delivery_id)
    receipts = writer_receipts(root, delivery_id, state)
    record = delivery_compile.merged_pr_record(root, delivery_id, target)
    if record is not None:
        # Deleting the Delivery's refs never skips the proof; only the notes' bytes are not recomputed then.
        unproven = merged_record_findings(root, delivery_id, record, integration, target, state)
        if unproven:
            return {"delivery": delivery_id, "outcome": "unproven_record_merge", "record": record,
                    "evidence": unproven, "leftovers": [f"Slot {ref.removeprefix('refs/heads/')}" for ref in slots],
                    "next_entry": f"/deliver {delivery_id}",
                    "recovery": f"the project owner decides in /deliver {delivery_id}: the target merged a PR record"
                                " that is not the Delivery's current recorded head as the coordinator wrote it, so"
                                " the Delivery is not merged and verify-merge does not apply"}
        leftovers = [f"Integration ref {canonical_refs(delivery_id)['integration'].removeprefix('refs/heads/')}"
                     ] if integration else []
        for item in items:
            directory = package_directory(root, item["tip"], delivery_id)
            item_props = tree_items(root, item["tip"], directory).get(item["story"], ("", {}))[1] if directory else {}
            if is_ancestor(root, item["tip"], record) and item_props.get("status") == "integrated":
                leftovers.append(f"Item ref {item['ref'].removeprefix('refs/heads/')}")
        leftovers += [f"Slot {ref.removeprefix('refs/heads/')}" for ref in slots]
        leftovers += [f"writer receipt {name}" for name in receipts]
        if not leftovers:
            return {"delivery": delivery_id, "outcome": "closed", "record": record}
        return {"delivery": delivery_id, "outcome": "merged_cleanup_pending", "record": record,
                "leftovers": leftovers, "next_entry": "verify-merge",
                "recovery": f"verify-merge {delivery_id} proves the merge and drops its Integration and integrated"
                            " Item refs; a Slot or writer receipt that stays after it is the project owner's to"
                            " resolve, since only the atomic leased verbs release one"}
    evidence, signs = external_product_merge(root, delivery_id, target, integration, items,
                                             state.get("fence_target", ""))
    if evidence:
        return {"delivery": delivery_id, "outcome": "external_product_merge", "evidence": evidence + signs,
                "leftovers": [f"Slot {ref.removeprefix('refs/heads/')}" for ref in slots],
                "next_entry": f"/deliver {delivery_id}",
                "recovery": f"the project owner decides in /deliver {delivery_id}: the Delivery's product reached the"
                            " target outside merge-pr, so it is not merged and never complete; a Slot is released"
                            " only by integrate-item or cancel-delivery"}
    qualified = {"signs": signs} if signs else {}
    if integration and record_kind(commit_message(root, integration)) == "pr_url_recorded_v1":
        readiness = recorded_head_readiness(root, remote, delivery_id, integration, branch, target, state)
        return {"delivery": delivery_id, "outcome": "awaiting_merge", "next_entry": None, "readiness": readiness,
                **qualified, "recovery": f"merge-pr in /deliver {delivery_id} merges the recorded PR head"
                + (" once each readiness finding is resolved" if readiness else "")}
    return {"delivery": delivery_id, "outcome": "open", "next_entry": None, **qualified,
            "recovery": f"continue the Delivery through /deliver {delivery_id}"}


def merged_record_findings(root: Path, delivery_id: str, record: str, integration: str, target: str,
                           state: dict) -> list[str]:
    """Why the PR record the target merged does not prove this Delivery's closure.

    It proves closure only as the Delivery's current recorded head, the
    Integration tip or, once verify-merge dropped that ref, the record itself,
    and only as the record, intent and Review the coordinator wrote. Without
    the Integration ref the notes open-pr authors are not recomputed, as a
    later package release may write them differently; every other check runs.
    """
    if integration and integration != record:
        return [f"the target merged the PR record {record}, but the Integration of {delivery_id} has since moved to"
                f" {integration}"]
    try:
        directory = package_directory(root, record, delivery_id)
        url = str(tree_note(root, record, f"{directory}/delivery-review.md")[0].get("pull_request_url", "")
                  ) if directory else ""
        canonical, _number = canonical_github_pr(url)
    except (RuntimeError, ValueError) as exc:
        return [f"the merged PR record {record} of {delivery_id} names no PR: {exc}"]
    stops, drift = proof_stops(root, delivery_id, record, target, state.get("fence_target", ""))
    _chain, errors = pr_record_chain(root, record, delivery_id, canonical, stops, recompute_notes=bool(integration))
    return drift + errors


def audit(project_root: Path, delivery_id: str | None = None, remote: str = "origin") -> dict:
    """Read every named Delivery's closure outcome; nothing is deleted, released or merged."""
    root = delivery_git.main_worktree(project_root.resolve())
    if delivery_id is not None:
        delivery_git.validate_delivery_id(delivery_id)
    try:
        run_git(root, "remote", "get-url", remote)
    except RuntimeError:
        # A project without the remote has no published Delivery to audit yet.
        return {"ok": True, "deliveries": [], "errors": [], "findings": [], "observations": []}
    branch, target = delivery_git.fetch_target(root, remote)
    state = coordination_state(root, remote)
    deliveries = [delivery_id] if delivery_id else known_deliveries(root, target, state)
    results, errors, findings = [], [], []
    # One Delivery's unreadable state never hides the others from --all; --delivery fails closed.
    tolerated = delivery_compile.MergeStateUnknown if delivery_id else (RuntimeError, ValueError)
    with reading_session(root):
        for identifier in deliveries:
            try:
                outcome = audit_delivery(root, identifier, target, state, remote, branch)
            except tolerated as exc:
                errors.append(f"DELIVERY_COORDINATION_CORRUPT: {identifier}: {exc}")
                continue
            results.append(outcome)
            if outcome.get("readiness"):
                findings.append({"code": "DELIVERY_CLOSURE_INCOMPLETE", "severity": "blocker", "refs": [identifier],
                                 "paths": [], "next_entry": f"/deliver {identifier}",
                                 "message": f"{identifier} is awaiting_merge, but its recorded PR head cannot close"
                                            " it: " + "; ".join(outcome["readiness"])})
            if outcome.get("signs"):
                findings.append({"code": "DELIVERY_EXTERNAL_MERGE", "severity": "warning", "refs": [identifier],
                                 "paths": [], "next_entry": f"/deliver {identifier}",
                                 "message": f"{identifier} is {outcome['outcome']}, and "
                                            + "; ".join(outcome["signs"]) + "; this alone proves no external merge,"
                                            f" so the project owner checks it in /deliver {identifier}"})
            details = "; ".join(outcome.get("evidence", []) + outcome.get("leftovers", []))
            message = f"{identifier} is {outcome['outcome']}: {details}; recovery: {outcome.get('recovery')}"
            if outcome["outcome"] in {"external_product_merge", "unproven_record_merge"}:
                findings.append({"code": "DELIVERY_EXTERNAL_MERGE", "severity": "blocker", "refs": [identifier],
                                 "paths": [], "message": message, "next_entry": outcome["next_entry"]})
            elif outcome["outcome"] == "merged_cleanup_pending":
                findings.append({"code": "DELIVERY_CLOSURE_INCOMPLETE", "severity": "blocker", "refs": [identifier],
                                 "paths": [], "message": message, "next_entry": outcome["next_entry"]})
    blocked = any(finding["severity"] == "blocker" for finding in findings)
    return {"ok": not errors and not blocked, "deliveries": results, "errors": errors, "findings": findings,
            "observations": [{"kind": "ref", "target": f"closure/{item['delivery']}", "value": item["outcome"]}
                             for item in results]}


def _state(found: bool, readable: bool) -> str:
    return "configured" if found else ("not_configured" if readable else "unknown")


def closure_requirements(rules: list | None, classic: dict | None, repository_id: int | None) -> dict:
    """Where the provider requires the closure check, split into pinned and name-only requirements.

    A requirement is pinned when a status-check rule names the check from the
    GitHub Actions app, or a workflows rule requires this repository's closure
    workflow; a name alone can be met by a commit status or a same-name job.
    """
    pinned, by_name = set(), False
    for rule in rules or []:
        parameters = rule.get("parameters") or {}
        if rule.get("type") == "required_status_checks":
            for check in parameters.get("required_status_checks") or []:
                if isinstance(check, dict) and check.get("context") == CLOSURE_CONTEXT:
                    if check.get("integration_id") == ACTIONS_INTEGRATION_ID:
                        pinned.add(rule.get("ruleset_id"))
                    else:
                        by_name = True
        elif rule.get("type") == "workflows":
            for workflow in parameters.get("workflows") or []:
                if (isinstance(workflow, dict) and workflow.get("path") == CLOSURE_WORKFLOW
                        and repository_id is not None and workflow.get("repository_id") == repository_id):
                    pinned.add(rule.get("ruleset_id"))
    checks = (classic or {}).get("required_status_checks") or {}
    classic_pinned = any(isinstance(check, dict) and check.get("context") == CLOSURE_CONTEXT
                         and check.get("app_id") == ACTIONS_INTEGRATION_ID for check in checks.get("checks") or [])
    by_name = by_name or (not classic_pinned and (CLOSURE_CONTEXT in (checks.get("contexts") or []) or any(
        isinstance(check, dict) and check.get("context") == CLOSURE_CONTEXT for check in checks.get("checks") or [])))
    return {"rulesets": sorted(value for value in pinned if isinstance(value, int)),
            "pinned_ruleset": bool(pinned), "classic": classic_pinned, "by_name": by_name}


def protection_status(project_root: Path, branch: str | None = None, remote: str = "origin",
                      provider=None) -> dict:
    """Report, read-only, whether the provider requires the closure check on the target branch.

    Each property is configured, not_configured or unknown. A property
    neither source shows is not_configured only when the branch rules and the
    classic protection are both readable, as for a branch GitHub reports as
    not protected; a property the provider hides from a token without admin
    rights, such as classic protection or ruleset bypass actors, is unknown,
    as is a workflows rule whose repository id cannot be read. A check required by name only is not_configured. The report
    never refuses anything and never claims a guarantee no CLI check has.
    """
    root = project_root.resolve()
    branch = branch or delivery_git.resolve_target_branch(root, remote)
    if provider is None:
        from delivery_provider import GitHubProvider, ProviderError
        try:
            provider = GitHubProvider(root, remote)
        except ProviderError:
            provider = None
    rules = provider.branch_rules(branch) if provider is not None else None
    classic = provider.branch_protection(branch) if provider is not None else None
    repository_id = (provider.repository_id() if provider is not None
                     and any(rule.get("type") == "workflows" for rule in rules or []) else None)
    required = closure_requirements(rules, classic, repository_id)
    # A rule is absent only when both sources answered; GitHub hides classic protection from a token
    # without admin rights, and only its "Branch not protected" answer reads as readable and empty.
    readable = rules is not None and classic is not None
    reasons = {}
    closure_context = _state(required["pinned_ruleset"] or required["classic"], readable)
    unresolved = repository_id is None and any(
        rule.get("type") == "workflows" and any(isinstance(workflow, dict) and workflow.get("path") == CLOSURE_WORKFLOW
                                                for workflow in (rule.get("parameters") or {}).get("workflows") or [])
        for rule in rules or [])
    if closure_context != "configured" and unresolved:
        closure_context = "unknown"
        reasons["closure_context_required"] = (
            f"a workflows rule requires {CLOSURE_WORKFLOW}, but the repository id it must name cannot be read")
    elif closure_context != "configured" and required["by_name"]:
        closure_context = "not_configured"
        reasons["closure_context_required"] = (
            "the delivery-closure check is required by name only, which a commit status or a same-name job can"
            " meet; require it from the GitHub Actions app (integration_id 15368) or require"
            f" {CLOSURE_WORKFLOW} with a workflows rule")
    elif closure_context == "configured" and not required["rulesets"] and required["pinned_ruleset"]:
        closure_context = "unknown"
        reasons["closure_context_required"] = "a rule requires the check, but its ruleset cannot be identified"
    pull_request = _state(any(rule.get("type") == "pull_request" for rule in rules or [])
                          or bool((classic or {}).get("required_pull_request_reviews")), readable)
    bypass_states = []
    for ruleset_id in required["rulesets"]:
        ruleset = provider.ruleset(ruleset_id)
        actors = ruleset.get("bypass_actors") if isinstance(ruleset, dict) else None
        bypass_states.append("unknown" if not isinstance(actors, list) else
                             ("configured" if not actors else "not_configured"))
    if required["classic"]:
        enforced = ((classic or {}).get("enforce_admins") or {}).get("enabled")
        bypass_states.append("configured" if enforced is True else "not_configured")
    if not bypass_states:
        bypass_states.append("unknown" if closure_context == "unknown" else "not_configured")
    no_bypass = ("configured" if all(value == "configured" for value in bypass_states)
                 else "not_configured" if "not_configured" in bypass_states else "unknown")
    properties = {"closure_context_required": closure_context, "pull_request_required": pull_request,
                  "no_bypass": no_bypass}
    values = set(properties.values())
    status = ("configured" if values == {"configured"}
              else "not_configured" if "not_configured" in values else "unknown")
    because = "".join(f" {name}: {reason}." for name, reason in reasons.items())
    return {
        "ok": True, "branch": branch, "status": status, "properties": properties, "reasons": reasons,
        "limit": PROTECTION_LIMIT,
        "observations": [{"kind": "provider", "target": f"protection/{name}", "value": value}
                         for name, value in {"status": status, **properties}.items()],
        "findings": [{"code": "DELIVERY_PROTECTION_STATUS",
                      "severity": "info" if status == "configured" else "warning",
                      "refs": [branch], "paths": [],
                      "message": f"repository protection of {branch} is {status}"
                                 f" ({', '.join(f'{name} {value}' for name, value in properties.items())})."
                                 f"{because} {PROTECTION_LIMIT}",
                      "next_entry": None}],
    }
