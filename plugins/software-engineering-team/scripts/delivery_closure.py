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
    canonical_github_pr, canonical_refs, commit_message, is_ancestor, run_git,
    split_remote_note, trailer,
)


CLOSURE_CONTEXT = "delivery-closure"
MANAGED_REF_PREFIX = "agentrof/"
DELIVERIES = (delivery_compile.delivery_root(Path("workspace") / "docs") / "deliveries").as_posix()
PRODUCT_EXCLUDED_ROOT = "workspace/docs/"
PRE_RESERVATION_STATUSES = {"scope_proposed", "scope_approved", "execution_approved"}
PR_INTENT_RECORDS = {"pr_creation_intent_v1", "pr_adoption_intent_v1"}
OUTCOMES = ("closed", "merged_cleanup_pending", "awaiting_merge", "open", "external_product_merge")
PROTECTION_STATES = ("configured", "not_configured", "unknown")
PROTECTION_LIMIT = (
    "Only an owner-installed ruleset that requires the delivery-closure check on the target branch, "
    "with no bypass actors, can prevent a direct provider merge, an admin bypass or an owner push; "
    "no command of this package can"
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


def has_object(root: Path, oid: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", oid + "^{commit}"], cwd=root,
                          capture_output=True, check=False).returncode == 0


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
    """Every Delivery's Integration tip, every Item ref with the Delivery its tip names, and every Slot."""
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
    return {"integrations": integrations, "items": items, "slots": slots}


def tree_paths(root: Path, commit: str, prefix: str) -> list[str]:
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
    return split_remote_note(root, commit, relative, delivery_compile.split_note)


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


def changed_paths(root: Path, base: str, head: str) -> list[str]:
    """The paths *head* changes against its merge base with *base*."""
    merge_base = run_git(root, "merge-base", base, head)
    return delivery_git.git_paths(root, "--no-replace-objects", "diff", "--no-renames", "--name-only", "-z",
                                  merge_base, head)


def rev_set(root: Path, *args: str) -> set[str]:
    return set(run_git(root, "rev-list", *args, "--").split()) if args else set()


def classify(root: Path, remote: str, head: str, base_tip: str, paths: list[str],
             head_ref: str = "", state: dict | None = None) -> dict:
    """Whether a pull request is managed by an open Delivery, and why.

    A pull request is managed when its head ref is an Agentrof ref, when its
    head holds commits the base lacks that an open Delivery's Integration or
    Item ref reaches, or when a path it changes lies under a path claim of a
    non-terminal Item of an open Delivery. Labels, branch names outside the
    Agentrof namespace and PR text never decide it, so renaming a branch or
    removing a label cannot take a PR out of the check. An open Delivery is
    one whose Integration ref exists.
    """
    state = state or coordination_state(root, remote)
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
        tips = [integration] + [item["tip"] for item in state["items"].values()
                                if item["delivery"] == delivery_id]
        if head_only and head_only & rev_set(root, *tips, "--not", base_tip):
            reasons.append(f"its head holds commits of {delivery_id} that the base lacks")
            deliveries.add(delivery_id)
        directory = package_directory(root, integration, delivery_id)
        if directory is None:
            continue
        for story, (_path, props) in sorted(tree_items(root, integration, directory).items()):
            if props.get("status") in delivery_compile.TERMINAL_ITEM_STATUSES:
                continue
            claimed = sorted(path for path in paths if claims_cover(path, props.get("path_claims")))
            if claimed:
                reasons.append(f"it changes {', '.join(claimed)} under a path claim of {story} of {delivery_id}")
                deliveries.add(delivery_id)
    return {"managed": bool(reasons), "reasons": reasons, "deliveries": sorted(deliveries)}


def recovery(step: str) -> str:
    return f"; recovery: {step}"


def pr_record_chain(root: Path, head: str, delivery_id: str, url: str) -> tuple[dict, list[str]]:
    """Walk the recorded PR head back to its intent and published Review, each exactly.

    The head must be the PR record that binds this PR, whose only parent is
    the intent it names; the intent's only parent is its Review-Head, the
    published Review, whose only parent is the Integration it reviewed.
    """
    chain: dict = {}
    errors: list[str] = []
    resume = recovery(f"/deliver {delivery_id} runs open-pr, which records this PR on the reviewed Integration head")

    def parents(oid: str) -> list[str]:
        return run_git(root, "show", "-s", "--format=%P", oid).split()

    message = commit_message(root, head)
    if record_kind(message) != "pr_url_recorded_v1" or trailer(message, "Delivery") != delivery_id:
        return chain, [f"DELIVERY_CLOSURE_INCOMPLETE: the PR head {head} is not the PR record of {delivery_id}" + resume]
    if not delivery_git.binds_pr(message, url):
        errors.append(f"DELIVERY_PR_HEAD_BASE_MISMATCH: the PR record of {delivery_id} binds another PR than {url}"
                      + recovery(f"merge only the PR that the record names, through merge-pr in /deliver {delivery_id}"))
    intent = trailer(message, "Intent") or ""
    if parents(head) != [intent]:
        return chain, errors + [f"DELIVERY_COORDINATION_CORRUPT: the PR record of {delivery_id} is not the exact child"
                                " of the intent it names" + resume]
    intent_message = commit_message(root, intent)
    review = trailer(intent_message, "Review-Head") or ""
    if (record_kind(intent_message) not in PR_INTENT_RECORDS or trailer(intent_message, "Delivery") != delivery_id
            or parents(intent) != [review]):
        return chain, errors + [f"DELIVERY_COORDINATION_CORRUPT: the PR intent of {delivery_id} is not the exact child"
                                " of its published Review" + resume]
    review_message = commit_message(root, review)
    reviewed = trailer(review_message, "Reviewed-Integration") or ""
    if (record_kind(review_message) != "delivery_review_published_v1"
            or trailer(review_message, "Delivery") != delivery_id or parents(review) != [reviewed]):
        return chain, errors + [f"DELIVERY_REVIEW_STALE: the PR intent of {delivery_id} does not follow its published"
                                " Delivery Review" + recovery(f"approve and publish the Delivery Review again in"
                                                              f" /deliver {delivery_id}, then open-pr")]
    chain.update(record=head, intent=intent, review=review, reviewed_integration=reviewed,
                 approval_hash=trailer(review_message, "Approval-Hash"))
    return chain, errors


def item_integration(root: Path, head: str, delivery_id: str, story: str) -> dict | None:
    """The latest integration of *story* on *head*'s first-parent line: its merge, seal, evidence and product tips."""
    listed = run_git(root, "rev-list", "--first-parent", "--merges", "--fixed-strings", "--all-match",
                     "--grep=Agentrof-Record: item-integration-v1", f"--grep=Agentrof-Delivery: {delivery_id}",
                     f"--grep=Agentrof-Story: {story}", head, "--")
    for merge in listed.split():
        message = commit_message(root, merge)
        merge_parents = run_git(root, "show", "-s", "--format=%P", merge).split()
        seal = trailer(message, "Reviewed-Tip") or ""
        if (trailer(message, "Story") != story or len(merge_parents) != 2 or merge_parents[1] != seal):
            continue
        seal_message = commit_message(root, seal)
        evidence = trailer(seal_message, "Reviewed-Tip") or ""
        product = trailer(seal_message, "Product-Tip") or ""
        if run_git(root, "show", "-s", "--format=%P", seal).split() != [evidence]:
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
    directory = item_path.rsplit("/", 1)[0]
    errors = []
    try:
        review, review_body = tree_note(root, head, f"{directory}/code-review.md")
        verification, verification_body = tree_note(root, head, f"{directory}/verification.md")
    except (RuntimeError, ValueError) as exc:
        return [f"DELIVERY_ITEM_NOT_READY: the evidence of {story} of {delivery_id} cannot be read: {exc}" + resume]
    product = found["product"]
    evidence_parents = run_git(root, "show", "-s", "--format=%P", found["evidence"]).split()
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
    return [error + resume for error in errors]


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
        where = f"the Integration tip {integration}" if integration else "no Integration ref"
        return [f"DELIVERY_PR_HEAD_BASE_MISMATCH: the PR head {head} is not the recorded Integration head of"
                f" {delivery_id}, which has {where}; only the Delivery PR that open-pr records on the reviewed"
                " Integration head can merge Delivery work"
                + recovery(f"close this PR and continue through /deliver {delivery_id}")]
    chain, errors = pr_record_chain(root, head, delivery_id, url)
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
    fence = listed_refs(root, remote, canonical_refs(delivery_id)["fence"])
    fence_target = ""
    for ref, oid in fence.items():
        ensure_object(root, remote, oid, ref)
        fence_target = trailer(commit_message(root, oid), "Target") or ""
    if not fence_target or not has_object(root, fence_target) or not is_ancestor(root, fence_target, base_tip):
        errors.append(f"DELIVERY_TARGET_DRIFT: the PR base {base} does not hold the Fence target of {delivery_id}"
                      + recovery(f"run refresh-target through /deliver {delivery_id} against the Delivery's target"))
    if bindings:
        with materialized(root, head) as tree:
            errors.extend(binding_findings(tree, delivery_id))
    return errors


def check_pull_request(project_root: Path, *, head: str, base: str, url: str, head_ref: str = "",
                       remote: str = "origin") -> dict:
    """The closure check one pull request runs: unmanaged PRs pass, managed PRs must be closure-ready."""
    root = delivery_git.main_worktree(project_root.resolve())
    canonical, _number = canonical_github_pr(url)
    if not delivery_git.OID_RE.fullmatch(head) or not has_object(root, head):
        raise RuntimeError("DELIVERY_INPUT_INVALID: the PR head must be an exact commit this checkout holds")
    base_tip = remote_branch_tip(root, remote, base)
    state = coordination_state(root, remote)
    classified = classify(root, remote, head, base_tip, changed_paths(root, base_tip, head), head_ref, state)
    result = {"ok": True, "managed": classified["managed"], "reasons": classified["reasons"],
              "observations": [{"kind": "provider", "target": "pull_request_head", "value": head},
                               {"kind": "ref", "target": "closure/classification",
                                "value": "managed" if classified["managed"] else "not_managed"}]}
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


def holds_product_bytes(root: Path, product: str, start: str, target: str) -> bool:
    """Whether *target* holds every product path the Item changed with the Item's exact bytes."""
    changed = [path for path in delivery_git.git_paths(
        root, "--no-replace-objects", "diff", "--no-renames", "--name-only", "-z", start, product)
        if not path.startswith(PRODUCT_EXCLUDED_ROOT)]
    if not changed:
        return False

    def blobs(commit: str) -> dict[str, str]:
        listing = run_git(root, "ls-tree", "-r", commit, "--", *changed)
        return {line.split("\t", 1)[1]: line.split()[2] for line in listing.splitlines() if "\t" in line}

    return blobs(product) == blobs(target)


def external_product_merge(root: Path, delivery_id: str, target: str, integration: str,
                           items: list[dict]) -> list[str]:
    """The evidence that this Delivery's work reached *target* without its recorded-head merge."""
    evidence = []
    listed = run_git(root, "rev-list", "--fixed-strings", f"--grep=Agentrof-Delivery: {delivery_id}", target, "--")
    if any(delivery_trailer_lines(commit_message(root, oid), delivery_id) for oid in listed.split()):
        evidence.append(f"the target holds a commit of {delivery_id}")
    for product in sorted(product_tips(root, delivery_id, integration, items)):
        start = product_start(root, product, delivery_id)
        if is_ancestor(root, product, target):
            evidence.append(f"the target holds the Item product tip {product}")
        elif start and holds_product_bytes(root, product, start, target):
            evidence.append(f"the target holds the exact bytes of the Item product tip {product}")
    directory = package_directory(root, target, delivery_id)
    if directory:
        integrated = sorted(story for story, (_path, props) in tree_items(root, target, directory).items()
                            if props.get("status") == "integrated")
        if integrated:
            evidence.append(f"the target's package records integrated Items {', '.join(integrated)}")
    return evidence


def writer_receipts(root: Path, delivery_id: str) -> list[str]:
    folder = delivery_git.runtime_root(root) / "receipts"
    prefix = f"item-{delivery_id.lower()}-"
    return sorted(path.name for path in folder.glob(prefix + "*.json")) if folder.is_dir() else []


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
    receipts = writer_receipts(root, delivery_id)
    record = delivery_compile.merged_pr_record(root, delivery_id, target)
    if record is not None:
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
    evidence = external_product_merge(root, delivery_id, target, integration, items)
    if evidence:
        return {"delivery": delivery_id, "outcome": "external_product_merge", "evidence": evidence,
                "leftovers": [f"Slot {ref.removeprefix('refs/heads/')}" for ref in slots],
                "next_entry": f"/deliver {delivery_id}",
                "recovery": f"the project owner decides in /deliver {delivery_id}: the Delivery's product reached the"
                            " target outside merge-pr, so it is not merged and never complete; a Slot is released"
                            " only by integrate-item or cancel-delivery"}
    if integration and record_kind(commit_message(root, integration)) == "pr_url_recorded_v1":
        readiness = recorded_head_readiness(root, remote, delivery_id, integration, branch, target, state)
        return {"delivery": delivery_id, "outcome": "awaiting_merge", "next_entry": None, "readiness": readiness,
                "recovery": f"merge-pr in /deliver {delivery_id} merges the recorded PR head"
                            + (" once each readiness finding is resolved" if readiness else "")}
    return {"delivery": delivery_id, "outcome": "open", "next_entry": None,
            "recovery": f"continue the Delivery through /deliver {delivery_id}"}


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
    for identifier in deliveries:
        try:
            outcome = audit_delivery(root, identifier, target, state, remote, branch)
        except delivery_compile.MergeStateUnknown as exc:
            errors.append(f"DELIVERY_COORDINATION_CORRUPT: {identifier}: {exc}")
            continue
        results.append(outcome)
        if outcome.get("readiness"):
            findings.append({"code": "DELIVERY_CLOSURE_INCOMPLETE", "severity": "blocker", "refs": [identifier],
                             "paths": [], "next_entry": f"/deliver {identifier}",
                             "message": f"{identifier} is awaiting_merge, but its recorded PR head cannot close it: "
                                        + "; ".join(outcome["readiness"])})
        details = "; ".join(outcome.get("evidence", []) + outcome.get("leftovers", []))
        message = f"{identifier} is {outcome['outcome']}: {details}; recovery: {outcome.get('recovery')}"
        if outcome["outcome"] == "external_product_merge":
            findings.append({"code": "DELIVERY_EXTERNAL_MERGE", "severity": "blocker", "refs": [identifier],
                             "paths": [], "message": message, "next_entry": outcome["next_entry"]})
        elif outcome["outcome"] == "merged_cleanup_pending":
            findings.append({"code": "DELIVERY_CLOSURE_INCOMPLETE", "severity": "blocker", "refs": [identifier],
                             "paths": [], "message": message, "next_entry": outcome["next_entry"]})
    return {"ok": not errors and not findings, "deliveries": results, "errors": errors, "findings": findings,
            "observations": [{"kind": "ref", "target": f"closure/{item['delivery']}", "value": item["outcome"]}
                             for item in results]}


def _state(found: bool, readable: bool) -> str:
    return "configured" if found else ("not_configured" if readable else "unknown")


def protection_status(project_root: Path, branch: str | None = None, remote: str = "origin",
                      provider=None) -> dict:
    """Report, read-only, whether the provider requires the closure check on the target branch.

    Each property is configured, not_configured or unknown: unknown whenever
    the provider does not show it, as it hides classic protection and ruleset
    bypass actors from a token without admin rights. The report never refuses
    anything and never claims a guarantee no CLI check has.
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
    closure_rulesets = sorted({rule.get("ruleset_id") for rule in rules or []
                               if rule.get("type") == "required_status_checks"
                               and any(isinstance(check, dict) and check.get("context") == CLOSURE_CONTEXT
                                       for check in (rule.get("parameters") or {}).get("required_status_checks", []))
                               and isinstance(rule.get("ruleset_id"), int)})
    checks = (classic or {}).get("required_status_checks") or {}
    classic_contexts = set(checks.get("contexts") or []) | {
        check.get("context") for check in checks.get("checks") or [] if isinstance(check, dict)}
    classic_closure = CLOSURE_CONTEXT in classic_contexts
    both_readable = rules is not None and classic is not None
    closure_context = _state(bool(closure_rulesets) or classic_closure, both_readable)
    pull_request = _state(any(rule.get("type") == "pull_request" for rule in rules or [])
                          or bool((classic or {}).get("required_pull_request_reviews")), both_readable)
    bypass_states = []
    for ruleset_id in closure_rulesets:
        ruleset = provider.ruleset(ruleset_id)
        actors = ruleset.get("bypass_actors") if isinstance(ruleset, dict) else None
        bypass_states.append("unknown" if not isinstance(actors, list) else
                             ("configured" if not actors else "not_configured"))
    if classic_closure:
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
    return {
        "ok": True, "branch": branch, "status": status, "properties": properties, "limit": PROTECTION_LIMIT,
        "observations": [{"kind": "provider", "target": f"protection/{name}", "value": value}
                         for name, value in {"status": status, **properties}.items()],
        "findings": [{"code": "DELIVERY_PROTECTION_STATUS",
                      "severity": "info" if status == "configured" else "warning",
                      "refs": [branch], "paths": [],
                      "message": f"repository protection of {branch} is {status}"
                                 f" ({', '.join(f'{name} {value}' for name, value in properties.items())})."
                                 f" {PROTECTION_LIMIT}",
                      "next_entry": None}],
    }
