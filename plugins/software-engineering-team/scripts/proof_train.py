"""Prove reviewed changes together on one integration candidate.

The combined proof covers that candidate only. ``train publish`` creates or
reuses one integration PR, and ``train merge`` requires its reviewed head.
It never publishes separately unproved member prefixes. The shared state is
reconstructible local evidence, and dry runs change no files or Git refs.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import atomic_file
import file_lock

STATE = Path(".agentrof") / "agent-marketplace" / ".runtime" / "train"
OPEN, SEALED, GREEN, RED, MERGED = "open", "sealed", "green", "red", "merged"


def home(root: Path, lane) -> Path:
    common = lane.run("git", "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=root)
    return Path(common).parent / STATE


class State:
    """The batches file under an exclusive lock held while the command edits it."""

    def __init__(self, directory: Path, *, read_only: bool = False):
        self.file, self.handle = directory / "state.json", None
        if not read_only:
            directory.mkdir(parents=True, exist_ok=True)
            self.handle = open(directory / "state.lock", "a+", newline="\n")
            if not file_lock.acquire(self.handle.fileno(), timeout=30):
                self.handle.close()
                raise RuntimeError("the train state is locked by another command")
        self.data = json.loads(self.file.read_text()) if self.file.is_file() else {"batches": []}

    def save(self) -> None:
        if self.handle is None:
            raise RuntimeError("a dry run cannot save train state")
        atomic_file.replace_text(self.file, json.dumps(self.data, indent=2) + "\n")

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()

    def batch(self, number: int | None, lane) -> dict:
        found = [b for b in self.data["batches"] if b["id"] == number] if number else self.data["batches"][-1:]
        if not found:
            raise lane.Refused("no such batch; see train status")
        return found[0]


def own_root(root: Path, task: str, lane) -> Path:
    """The worktree that has the task's branch checked out, where ``prove`` wrote its proof record;
    ``root`` when no single worktree has it, so ``add`` can run from any other worktree."""
    where, found = "", []
    for line in lane.run("git", "worktree", "list", "--porcelain", cwd=root).splitlines():
        if line.startswith("worktree "):
            where = line[len("worktree "):]
        elif line.startswith(f"branch refs/heads/task/{task.lower()}-"):
            found.append(where)
    return Path(found[0]) if len(found) == 1 else root


def add(args, root, lane, state) -> dict:
    if not (args.task and args.pr):
        raise lane.Refused("train add needs --task and --pr")
    if getattr(args, "critical", None) not in (None, 0):
        raise lane.Refused("train add refuses unresolved critical or major findings")
    root = own_root(root, args.task, lane)
    path = lane.task_path(root, args.task)
    props = lane.task_props(path)
    files = lane.changed_files(root, args.base)
    methods = lane.required_methods(root, props, files, lane.task_body(path))
    missing = lane.missing_methods(root, args.task, methods)
    if missing:
        raise lane.Refused("method evidence is missing or changed: " + ", ".join(missing))
    missing = [row["companion"] for row in lane.companion_evidence(root, args.task, methods, props, files) if not row["ok"]]
    if missing:
        raise lane.Refused("companion use or explicit fallback evidence is missing or changed: " + ", ".join(missing))
    problems, record = lane.proof_problems(root, args.task)
    if problems:
        raise lane.Refused("the pull request has no valid proof of its own: " + "; ".join(problems))
    deferred_review = record.get("selection") == "deferred"
    if not deferred_review:
        problems = lane.review_problems(root, [{"task": args.task, "root": str(root)}], args.base)
        if problems:
            raise lane.Refused("; ".join(problems))
    plan = lane.proof_plan(root, args.base)
    if plan["selection"] == "full" and record.get("selection") not in ("full", "deferred"):
        raise lane.Refused("this change needs the full proof; run prove again")
    head = lane.head_state(root)[0]
    remote = json.loads(lane.run("gh", "pr", "view", str(args.pr), "--json", "headRefOid,state,autoMergeRequest", cwd=root))
    if remote.get("state") != "OPEN" or remote.get("headRefOid") != head:
        raise lane.Refused("train add requires an open pull request at the reviewed local head")
    current = state.data["batches"][-1] if state.data["batches"] else None
    if not current or current["status"] != OPEN:
        current = {"id": (current["id"] + 1) if current else 1, "status": OPEN, "members": []}
        state.data["batches"].append(current)
    if any(m["task"] == args.task for b in state.data["batches"] if b["status"] in (OPEN, SEALED, RED, GREEN) for m in b["members"]):
        raise lane.Refused(f"{args.task} is already in a batch")
    current["members"].append({
        "task": args.task, "pr": args.pr, "head": head, "reviewed_head": None if deferred_review else head,
        "review_status": "pending_aggregate" if deferred_review else "native_review", "branch": lane.run(
            "git", "rev-parse", "--abbrev-ref", "HEAD", cwd=root), "root": str(root), "own_proof": record.get("selection", "full"),
        "merge": {"detail": args.detail, "delegated": args.delegated, "style": args.style,
                  "critical": None if deferred_review else 0, "non_critical": getattr(args, "non_critical", None),
                  "filed": args.filed}})
    if getattr(args, "dry_run", False):  # every check above ran; the batch is only named, never saved
        return {"ok": True, "dry_run": True, "batch": current["id"], "members": [m["task"] for m in current["members"]]}
    if remote.get("autoMergeRequest"):
        lane.run("gh", "pr", "merge", str(args.pr), "--disable-auto", cwd=root)
    state.save()
    sealed = [b["id"] for b in state.data["batches"] if b["status"] == SEALED]
    note = f"batch {sealed[0]} is sealed and being proved; this pull request joined the next batch" if sealed else ""
    return {"ok": True, "batch": current["id"], "members": [m["task"] for m in current["members"]], "note": note}


def remove(args, root, lane, state) -> dict:
    """A member that does not merge onto the batch leaves it, so the rest of the batch can be proved again."""
    if not args.task:
        raise lane.Refused("train remove needs --task")
    batch = state.batch(args.batch, lane)
    if batch.get("publication_pending"):
        raise lane.Refused("reconcile the pending integration publication before removing its members")
    if batch["status"] not in (OPEN, RED, GREEN):
        raise lane.Refused(f"batch {batch['id']} is {batch['status']}; only an open, red or green batch gives up a member")
    left = [m for m in batch["members"] if m["task"] != args.task]
    if len(left) == len(batch["members"]):
        raise lane.Refused(f"{args.task} is not in batch {batch['id']}")
    if getattr(args, "dry_run", False):
        return {"ok": True, "dry_run": True, "batch": batch["id"], "members": [m["task"] for m in left]}
    batch.update(members=left, status=OPEN)
    for key in ("head", "base_head", "base", "commands", "proof_scope", "finished_at"):
        batch.pop(key, None)
    state.save()
    return {"ok": True, "batch": batch["id"], "members": [m["task"] for m in left]}


def drop(root: Path, tree: Path, name: str) -> None:
    subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=root, capture_output=True)
    subprocess.run(["git", "worktree", "prune"], cwd=root, capture_output=True)
    subprocess.run(["git", "branch", "-D", name], cwd=root, capture_output=True)


def integrate(root: Path, lane, batch: dict, members: list[dict], base: str, suffix: str = "",
              base_head: str | None = None) -> tuple[Path, str]:
    """A worktree of ``train/<n>``: base plus the members' heads, merged in order."""
    tree = home(root, lane) / f"wt-{batch['id']}{suffix}"
    name = f"train/{batch['id']}{suffix}"
    if base_head is None:
        lane.run("git", "fetch", "origin", base, cwd=root)
        base_head = lane.run("git", "rev-parse", f"origin/{base}", cwd=root)
    drop(root, tree, name)
    lane.run("git", "worktree", "add", "-b", name, str(tree), base_head, cwd=root)
    for member in members:
        done = subprocess.run(["git", "merge", "--no-ff", "-m", f"train {batch['id']}: {member['task']}",
                               member["head"]], cwd=tree, capture_output=True, text=True)
        if done.returncode:
            drop(root, tree, name)
            raise lane.Refused(f"{member['task']} does not merge onto the batch: {(done.stdout + done.stderr).strip()[-300:]}")
    return tree, base_head


def run_commands(root: Path, lane, tree: Path, base: str) -> list[dict]:
    return lane.run_proof_commands(tree, lane.proof_commands(tree, base))


def alive(pid: int | None) -> bool:
    try:
        return bool(pid) and (os.kill(pid, 0) is None)
    except OSError:
        return False


def check_reviewed_heads(batch: dict, lane) -> None:
    for member in batch["members"]:
        reviewed = member.get("reviewed_head")
        pending = member.get("review_status") == "pending_aggregate" and member.get("own_proof") == "deferred"
        if not pending and (not reviewed or reviewed != member["head"] or member["merge"].get("critical") != 0):
            raise lane.Refused(f"{member['task']} lacks a review bound to its head; remove and add it after review")
        ref = "HEAD" if member["branch"] == "HEAD" else f"refs/heads/{member['branch']}"
        current = lane.run("git", "rev-parse", ref, cwd=Path(member["root"]))
        if current != member["head"]:
            raise lane.Refused(f"{member['task']} changed after review; remove and add it after reviewing the new head")


def proof_scope(batch: dict, base_head: str) -> dict:
    return {"batch": batch["id"], "base_head": base_head,
            "members": [{"task": member["task"], "head": member["head"]} for member in batch["members"]]}


def prove(args, root, lane, state) -> dict:
    batch = state.batch(args.batch, lane)
    busy = [b["id"] for b in state.data["batches"] if b["status"] == SEALED and b is not batch and alive(b.get("pid"))]
    if busy:
        raise lane.Refused(f"batch {busy[0]} is being proved; one batch proof runs at a time")
    if batch["status"] not in (OPEN, RED, SEALED, GREEN) or not batch["members"]:
        raise lane.Refused(f"batch {batch['id']} is {batch['status']}; nothing to prove")
    if batch["status"] == SEALED and alive(batch.get("pid")):
        raise lane.Refused(f"batch {batch['id']} is being proved")
    if batch.get("publication_pending"):
        raise lane.Refused("reconcile the pending integration publication before rebuilding its candidate")
    check_reviewed_heads(batch, lane)
    if args.dry_run:
        return {"ok": True, "dry_run": True, "batch": batch["id"], "integration_branch": f"train/{batch['id']}",
                "members": [m["task"] for m in batch["members"]],
                "would_run": [lane.proof_runner(root, c) for c in lane.proof_commands(root, args.base)]}
    before = batch["status"]
    # Sealing diverts new members to the next batch while this proof runs.
    batch.update(status=SEALED, pid=os.getpid())
    state.save()
    number, directory = batch["id"], state.file.parent
    state.close()
    path = directory / f"batch-{number}.proof.json"
    candidate_state = {}
    try:
        if before != GREEN:
            path.unlink(missing_ok=True)
        lane.run("git", "fetch", "origin", args.base, cwd=root)
        base_head = lane.run("git", "rev-parse", f"origin/{args.base}", cwd=root)
        scope = proof_scope(batch, base_head)
        tree = directory / f"wt-{number}"
        unchanged = (tree.is_dir() and batch.get("base") == args.base and batch.get("proof_scope") == scope
                     and lane.head_state(tree) == (batch.get("head"), True)
                     and lane.run("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=tree) == f"train/{number}")
        if not unchanged:
            path.unlink(missing_ok=True)
            tree, base_head = integrate(root, lane, batch, batch["members"], args.base, base_head=base_head)
        head = lane.run("git", "rev-parse", "HEAD", cwd=tree)
        candidate_state = {"head": head, "base_head": base_head, "base": args.base, "proof_scope": scope}
        record = lane.cached_proof(tree, lane.proof_commands(tree, args.base), base=args.base, scope=scope,
                                   path=path, metadata={"selection": "full", "why": ["one combined candidate"]},
                                   expected_head=head)
        if record["head"] != head or not record["tree_clean"]:
            raise lane.Refused("the integration candidate changed while proof ran; restore it and prove again")
    except BaseException:
        reopened = State(directory)
        try:
            failed = reopened.batch(number, lane)
            failed.update(candidate_state, status=RED)
            failed.pop("pid", None)
            failed.pop("commands", None)
            failed.pop("finished_at", None)
            reopened.save()
        finally:
            reopened.close()
        raise
    ok = record["ok"]
    done = State(directory)
    try:
        batch = done.batch(number, lane)
        batch.update(candidate_state, status=GREEN if ok else RED,
                     commands=record["commands"], finished_at=record["finished_at"])
        batch.pop("pid", None)
        done.save()
    finally:
        done.close()
    return {"ok": ok, "batch": number, "integration_head": head, "commands": record["commands"],
            "finished_at": record["finished_at"], "reused": record["reused"]}


def candidate(batch: dict, root: Path, lane, base: str) -> Path:
    if batch["status"] != GREEN:
        raise lane.Refused(f"batch {batch['id']} is {batch['status']}; only a green batch can publish or merge")
    if batch.get("base") != base:
        raise lane.Refused("the batch has no proof for this target branch; run train prove again")
    check_reviewed_heads(batch, lane)
    tree = home(root, lane) / f"wt-{batch['id']}"
    head, clean = lane.head_state(tree)
    if head != batch["head"] or not clean:
        raise lane.Refused("the integration candidate changed after proof; run train prove again")
    results = batch.get("commands", [])
    if not results or any(item.get("exit_code") != 0 for item in results) \
            or [item.get("command") for item in results] != lane.proof_commands(tree, base):
        raise lane.Refused("the batch has no passing proof for its configured commands")
    remote = lane.run("git", "ls-remote", "--exit-code", "origin", f"refs/heads/{base}", cwd=root).split()
    if not remote or remote[0] != batch["base_head"]:
        raise lane.Refused(f"{base} moved since the batch was proved; run train prove again")
    path = home(root, lane) / f"batch-{batch['id']}.proof.json"
    if "proof_scope" in batch:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise lane.Refused("the batch proof record is missing or invalid; run train prove again") from exc
        if (not isinstance(record, dict) or record.get("ok") is not True or record.get("tree_clean") is not True
                or record.get("head") != head or record.get("base") != base or record.get("selection") != "full"
                or record.get("proof_scope") != proof_scope(batch, batch["base_head"])
                or record.get("commands") != results):
            raise lane.Refused("the batch proof record does not match its candidate; run train prove again")
        problems = lane.cached_proof_problems(tree, record, lane.proof_commands(tree, base),
                                             base=base, scope=proof_scope(batch, batch["base_head"]), path=path)
        if problems:
            raise lane.Refused("; ".join(problems))
    for member in batch["members"]:
        path = lane.task_path(tree, member["task"])
        props = lane.task_props(path)
        files = set(lane.run("git", "diff", "--name-only", f"{batch['base_head']}...{member['head']}", cwd=tree).splitlines())
        methods = lane.required_methods(tree, props, files, lane.task_body(path))
        missing = lane.missing_methods(Path(member["root"]), member["task"], methods)
        if missing:
            raise lane.Refused(f"{member['task']} method evidence is missing or changed: " + ", ".join(missing))
        missing = [row["companion"] for row in lane.companion_evidence(
            Path(member["root"]), member["task"], methods, props, files, head=member["head"]) if not row["ok"]]
        if missing:
            raise lane.Refused(f"{member['task']} companion use or explicit fallback evidence is missing or changed: "
                               + ", ".join(missing))
    members = [{"task": member["task"], "root": member["root"], "head": member["head"]}
               for member in batch["members"]]
    missing_review = lane.review_problems(tree, members, base)
    if missing_review:
        raise lane.Refused("; ".join(missing_review))
    return tree


def pr_marker(batch: dict) -> str:
    return f"<!-- agent-marketplace:train:{batch['id']} -->"


def find_integration_pr(batch: dict, root: Path, lane) -> dict | None:
    entries = json.loads(lane.run("gh", "pr", "list", "--head", f"train/{batch['id']}",
                                  "--state", "all", "--limit", "100", "--json",
                                  "number,url,state,headRefOid,headRefName,baseRefName,body,autoMergeRequest", cwd=root))
    known = batch.get("integration_pr")
    matches = [entry for entry in entries if str(entry["number"]) == str(known)] if known else entries
    if not matches:
        if known:
            raise lane.Refused("the recorded integration PR is absent; reconcile it before publication")
        return None
    if len(matches) != 1:
        raise lane.Refused("multiple integration PRs match this branch; reconcile them before publication")
    found = matches[0]
    allowed_heads = {batch["head"]}
    if known and batch.get("published_head"):
        allowed_heads.add(batch["published_head"])
    if found.get("headRefName") != f"train/{batch['id']}" or found.get("baseRefName") != batch["base"] \
            or found.get("headRefOid") not in allowed_heads or pr_marker(batch) not in found.get("body", ""):
        raise lane.Refused("the integration PR identity or candidate changed; reconcile it before publication")
    if found.get("state") == "CLOSED":
        raise lane.Refused("the integration PR was closed; resolve that decision before publication")
    return found


def integration_body(batch: dict, tree: Path, lane) -> str:
    members, issues = [], set()
    for member in batch["members"]:
        props = lane.task_props(lane.task_path(tree, member["task"]))
        source_issue = props.get("source_issue")
        if source_issue is not None:
            if not re.fullmatch(r"[1-9][0-9]*", str(source_issue)):
                raise lane.Refused(f"{member['task']} has an invalid source_issue")
            issues.add(int(source_issue))
        members.append({"task": member["task"], "pr": str(member["pr"]),
                        "head": member["head"], "source_issue": source_issue})
    record = {"schema_version": 1, "batch": batch["id"], "head": batch["head"],
              "base_head": batch["base_head"], "members": members}
    metadata = "<!-- agent-marketplace:integration " + json.dumps(record, separators=(",", ":")) + " -->"
    rows = "\n".join(f"| {m['task']} | #{m['pr']} | `{m['head']}` |" for m in batch["members"])
    closes = "\n".join(f"Closes #{issue}." for issue in sorted(issues))
    proof = lane.proof_section({"head": batch["head"], "commands": batch["commands"],
                                "selection": "integration", "why": ["one combined candidate"]})
    return (f"{pr_marker(batch)}\n{metadata}\n\n# Integration batch {batch['id']}\n\n"
            f"Candidate `{batch['head']}` combines the following member heads. "
            f"Its full proof ran on base `{batch['base_head']}`.\n\n"
            f"| Task | Member PR | Member head |\n| --- | --- | --- |\n{rows}\n\n"
            "Review this complete integration candidate before merging it atomically. "
            "Member PRs remain separate records until their disposition is reconciled.\n\n"
            f"{closes}\n{proof}")


def publish(args, root, lane, state) -> dict:
    batch = state.batch(args.batch, lane)
    tree = candidate(batch, root, lane, args.base)
    found = find_integration_pr(batch, root, lane)
    branch = f"train/{batch['id']}"
    if args.dry_run:
        return {"ok": True, "dry_run": True, "batch": batch["id"], "integration_head": batch["head"],
                "integration_branch": branch, "integration_pr": found.get("number") if found else None}
    if found and found["state"] == "MERGED":
        if found["headRefOid"] != batch["head"]:
            raise lane.Refused("the earlier integration candidate already merged; reconcile this batch")
        batch.update(integration_pr=found["number"], published_head=batch["head"])
        batch.pop("publication_pending", None)
        state.save()
        return {"ok": True, "batch": batch["id"], "integration_pr": found["number"], "url": found["url"],
                "next": "observe the merged integration through train merge"}
    if not found and batch.get("publication_pending"):
        return {"ok": False, "batch": batch["id"], "publication_unknown": True,
                "next": "reconcile the prior creation outcome; train publish will search again without duplicating it"}
    if found and found.get("autoMergeRequest"):
        lane.run("gh", "pr", "merge", str(found["number"]), "--disable-auto", cwd=root)
    remote = lane.run("git", "ls-remote", "origin", f"refs/heads/{branch}", cwd=root).split()
    previous = remote[0] if remote else ""
    allowed = {"", batch["head"]}
    if found:
        allowed.add(found["headRefOid"])
    if previous not in allowed:
        raise lane.Refused("the integration branch has an unrecognized head; preserve and reconcile it")
    lane.run("git", "push", f"--force-with-lease=refs/heads/{branch}:{previous}",
             "origin", f"{batch['head']}:refs/heads/{branch}", cwd=tree)
    with tempfile.TemporaryDirectory(prefix="train-pr-") as temporary:
        body = Path(temporary) / "body.md"
        body.write_bytes(integration_body(batch, tree, lane).encode("utf-8"))
        if found:
            batch.update(integration_pr=found["number"], published_head=batch["head"])
            state.save()
            lane.run("gh", "pr", "edit", str(found["number"]), "--body-file", str(body), cwd=root)
        else:
            batch["publication_pending"] = batch["head"]
            state.save()
            try:
                lane.run("gh", "pr", "create", "--head", branch, "--base", args.base,
                         "--title", f"Integrate reviewed batch {batch['id']}", "--body-file", str(body), cwd=root)
            except (lane.Refused, OSError):
                # A failed response can follow a successful remote creation.
                pass
        found = find_integration_pr(batch, root, lane)
    if found is None:
        return {"ok": False, "batch": batch["id"], "publication_unknown": True,
                "next": "reconcile the creation outcome; train publish will search again before further work"}
    if found["headRefOid"] != batch["head"]:
        raise lane.Refused("the integration PR has not reached the published candidate; reconcile it")
    batch.update(integration_pr=found["number"], published_head=batch["head"])
    batch.pop("publication_pending", None)
    state.save()
    return {"ok": True, "batch": batch["id"], "integration_pr": found["number"], "url": found["url"],
            "integration_head": batch["head"], "next": "review the complete integration candidate before train merge"}


def reconcile_members(batch: dict, root: Path, lane, state, integration_url: str) -> dict:
    completed, pending, errors = [], [], {}
    receipts = batch.setdefault("member_reconciliation", {})
    for member in batch["members"]:
        pr = str(member["pr"])
        receipt = receipts.get(pr, {})
        if receipt.get("head") == member["head"] and receipt.get("state") in ("CLOSED", "MERGED"):
            completed.append(pr)
            continue
        try:
            remote = json.loads(lane.run("gh", "pr", "view", pr, "--json", "headRefOid,state,comments", cwd=root))
            if remote.get("headRefOid") != member["head"]:
                raise lane.Refused("member head changed; preserve its current work")
            marker = f"<!-- agent-marketplace:train:{batch['id']}:member:{member['head']} -->"
            if remote.get("state") == "OPEN":
                linked = any(marker in comment.get("body", "") for comment in remote.get("comments", []))
                if not linked:
                    note = (f"{marker}\n\nSuperseded by {integration_url}. "
                            f"Integration commit `{batch['head']}` includes reviewed member `{member['head']}`.")
                    lane.run("gh", "pr", "comment", pr, "--body", note, cwd=root)
                remote = json.loads(lane.run("gh", "pr", "view", pr, "--json", "headRefOid,state", cwd=root))
                if remote.get("headRefOid") != member["head"]:
                    raise lane.Refused("member head changed before closure; preserve its current work")
                if remote.get("state") == "OPEN":
                    try:
                        lane.run("gh", "pr", "close", pr, cwd=root)
                    except (lane.Refused, OSError):
                        pass
                    remote = json.loads(lane.run("gh", "pr", "view", pr, "--json", "headRefOid,state", cwd=root))
            if remote.get("headRefOid") != member["head"]:
                raise lane.Refused("member head changed during reconciliation; inspect its closure before retrying")
            if remote.get("state") not in ("CLOSED", "MERGED"):
                raise lane.Refused("member PR closure is not confirmed")
            receipts[pr] = {"head": member["head"], "state": remote["state"], "observed_at": lane.now()}
            state.save()
            completed.append(pr)
        except (lane.Refused, OSError, ValueError) as error:
            pending.append(pr)
            errors[pr] = str(error)
    return {"member_prs_reconciled": completed, "member_prs_pending_reconciliation": pending,
            "member_reconciliation_errors": errors}


def merge(args, root, lane, state) -> dict:
    batch = state.batch(args.batch, lane)
    if batch["status"] not in (GREEN, MERGED):
        raise lane.Refused(f"batch {batch['id']} is {batch['status']}; only a green batch merges")
    head = getattr(args, "head", None)
    pr = getattr(args, "pr", None)
    if head != batch.get("head") or not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
        raise lane.Refused("train merge requires --head with the full integration commit reviewed and tested")
    if getattr(args, "critical", None) != 0:
        raise lane.Refused("train merge requires --critical 0 after aggregate candidate review")
    if not pr or str(pr) != str(batch.get("integration_pr")):
        raise lane.Refused("train merge requires --pr identifying the published integration PR")
    remote = json.loads(lane.run("gh", "pr", "view", str(pr), "--json",
                                "headRefOid,headRefName,baseRefName,state,mergedAt,url", cwd=root))
    if remote.get("headRefOid") != head or remote.get("headRefName") != f"train/{batch['id']}" \
            or remote.get("baseRefName") != batch.get("base") or args.base != batch.get("base"):
        raise lane.Refused("the integration PR changed after review; reconcile its current candidate")
    merge_error = None
    if remote.get("state") != "MERGED":
        if remote.get("state") != "OPEN":
            raise lane.Refused("only an open integration PR can merge")
        tree = candidate(batch, root, lane, args.base)
        for member in batch["members"]:
            path = lane.task_path(tree, member["task"])
            props = lane.task_props(path)
            if (props.get("rigor") == "high" or lane.field_items(props, "risks")) and not getattr(args, "delegated", False):
                raise lane.Refused(f"{member['task']} is high risk; the aggregate merge needs explicit delegation")
        if lane.pr_proof(tree) != "local_record":
            lane.run("gh", "pr", "checks", str(pr), "--required", cwd=root)
        if args.dry_run:
            return {"ok": True, "dry_run": True, "batch": batch["id"], "integration_pr": pr,
                    "integration_head": head, "merged": []}
        note = lane.REVIEW_MARK + f"\n\nReviewed integration head: {head}\n\nBlocking findings: 0"
        if getattr(args, "detail", None):
            note += "\n\n" + args.detail
        lane.run("gh", "pr", "comment", str(pr), "--body", note, cwd=root)
        # Refresh the observed base after checks, immediately before the merge request.
        current = lane.run("git", "ls-remote", "--exit-code", "origin", f"refs/heads/{args.base}", cwd=root).split()
        if not current or current[0] != batch["base_head"]:
            raise lane.Refused("the target base moved during checks; re-prove the integration candidate")
        try:
            lane.run("gh", "pr", "merge", str(pr), f"--{args.style}", "--match-head-commit", head, cwd=root)
        except (lane.Refused, OSError) as error:
            # Reconcile the remote outcome before any caller retries a merge.
            merge_error = str(error)
        remote = json.loads(lane.run("gh", "pr", "view", str(pr), "--json",
                                    "headRefOid,headRefName,baseRefName,state,mergedAt,url", cwd=root))
    merged = remote.get("state") == "MERGED" and remote.get("headRefOid") == head \
        and remote.get("headRefName") == f"train/{batch['id']}" and remote.get("baseRefName") == batch["base"]
    cleanup = {"member_prs_reconciled": [],
               "member_prs_pending_reconciliation": [str(m["pr"]) for m in batch["members"]],
               "member_reconciliation_errors": {}}
    feedback = {}
    if merged and not args.dry_run:
        batch.update(status=MERGED, merged_at=remote.get("mergedAt"))
        state.save()
        feedback = lane.queue_retrospective(root, {"kind": "completion", "operation": "train",
                                                 "head": head, "source_id": str(batch["id"])})
        cleanup = reconcile_members(batch, root, lane, state, remote.get("url") or f"#{pr}")
    complete = merged and not cleanup["member_prs_pending_reconciliation"]
    next_step = "observe the pending integration merge"
    if merged:
        next_step = "reconcile pending member PRs"
    if complete:
        next_step = "observe readiness"
    return {"ok": complete or (merged and bool(args.dry_run)), "dry_run": bool(args.dry_run),
            "batch": batch["id"], "integration_pr": pr, "integration_head": head,
            "merged": [m["task"] for m in batch["members"]] if merged else [],
            "merge_state": remote.get("state"), "merge_error": merge_error if not merged else None,
            **cleanup, **feedback,
            "base_check": "observed before request; concurrent remote base changes require native protection",
            "next": next_step}


def bisect(args, root, lane, state) -> dict:
    """Sample prefixes to identify a suspect without asserting a monotone failure cause."""
    batch = state.batch(args.batch, lane)
    if batch["status"] != RED:
        raise lane.Refused("only a red batch can be bisected")
    if not batch["members"]:
        raise lane.Refused("an empty batch cannot be bisected")
    if batch.get("base") != args.base or not batch.get("base_head") \
            or not any(item.get("exit_code") != 0 for item in batch.get("commands", [])):
        raise lane.Refused("the batch lacks a failed proof for this base")
    check_reviewed_heads(batch, lane)
    if args.dry_run:
        return {"ok": True, "dry_run": True, "batch": batch["id"],
                "members": [m["task"] for m in batch["members"]],
                "would_run": [lane.proof_runner(root, c) for c in lane.proof_commands(root, args.base)]}
    low, high = 1, len(batch["members"])
    observations = [{"prefix_length": high, "head": batch["head"], "base_head": batch["base_head"],
                     "passed": False, "source": "batch_proof", "commands": batch["commands"]}]
    try:
        while low < high:
            mid = (low + high) // 2
            tree, base_head = integrate(root, lane, batch, batch["members"][:mid], args.base,
                                        suffix="-bisect", base_head=batch["base_head"])
            results = run_commands(root, lane, tree, args.base)
            passed = all(item["exit_code"] == 0 for item in results)
            observations.append({"prefix_length": mid, "head": lane.head_state(tree)[0],
                                 "base_head": base_head, "passed": passed, "source": "prefix_probe",
                                 "commands": results})
            low, high = (mid + 1, high) if passed else (low, mid)
    finally:
        drop(root, home(root, lane) / f"wt-{batch['id']}-bisect", f"train/{batch['id']}-bisect")
    return {"ok": True, "suspect": batch["members"][low - 1]["task"], "observations": observations,
            "note": "Heuristic suspect from sampled prefixes. Earlier failures, interacting repairs, "
                    "base defects and flaky checks require a separate diagnosis; failure need not be monotone."}


def cmd_train(args, root: Path, lane) -> dict:
    directory = home(root, lane)
    state = State(directory, read_only=getattr(args, "dry_run", False))
    try:  # prove closes the state itself while the proof runs, so a batch can be joined meanwhile
        if args.verb == "status":
            return {"ok": True, "batches": [{"id": b["id"], "status": b["status"],
                                             "members": [m["task"] for m in b["members"]]} for b in state.data["batches"]]}
        return {"add": add, "remove": remove, "prove": prove, "publish": publish, "merge": merge, "bisect": bisect}[args.verb](args, root, lane, state)
    finally:
        if state.handle is not None and not state.handle.closed:
            state.close()
