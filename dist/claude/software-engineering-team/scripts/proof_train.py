"""Merge train: ready pull requests share one integration branch and one full proof.

``train add`` puts a reviewed pull request, with a valid proof record of its own, into the open batch;
it reads that record in the worktree of the task's branch, from whatever directory it runs in.
``train remove`` takes a member that does not merge onto the others out of an open or red batch.
``train prove`` seals the batch, builds ``train/<n>`` (base plus every member) in its own worktree and runs
the project's full ``local_proof`` there once, through the project's ``proof_runner`` queue. A green
batch writes a ``train`` proof record for each member, which satisfies that member's ``open-pr`` rule, and
``train merge`` then merges each member through the unchanged ``merge`` verdict path. ``train bisect``
finds the member that turns a red batch red. The state lives in the main checkout so every worktree sees it.
"""
from __future__ import annotations

import json
import os
import subprocess
from argparse import Namespace
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

    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.file, self.handle = directory / "state.json", open(directory / "state.lock", "a+", newline="\n")
        if not file_lock.acquire(self.handle.fileno(), timeout=30):
            raise RuntimeError("the train state is locked by another command")
        self.data = json.loads(self.file.read_text()) if self.file.is_file() else {"batches": []}

    def save(self) -> None:
        atomic_file.replace_text(self.file, json.dumps(self.data, indent=2) + "\n")

    def close(self) -> None:
        self.handle.close()

    def batch(self, number: int | None, lane) -> dict:
        found = [b for b in self.data["batches"] if b["id"] == number] if number else self.data["batches"][-1:]
        if not found:
            raise lane.Refused("no such batch; see train status")
        return found[0]


def green_for(root: Path, task: str, head: str | None) -> bool:
    try:
        state = json.loads((root / STATE / "state.json").read_text())
    except (OSError, ValueError):
        return False
    return any(b["status"] in (GREEN, MERGED) and m["task"] == task and m["head"] == head
               for b in state["batches"] for m in b["members"])


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
    if args.critical:
        raise lane.Refused("a review with critical findings cannot join a batch")
    root = own_root(root, args.task, lane)
    problems, record = lane.proof_problems(root, args.task)
    if problems:
        raise lane.Refused("the pull request has no valid proof of its own: " + "; ".join(problems))
    plan = lane.proof_plan(root, args.base)
    if plan["selection"] == "full" and record.get("selection") not in ("full", "deferred"):
        raise lane.Refused("this change needs the full proof; run prove again")
    # an auto-merge armed earlier would merge the pull request outside the batch proof
    if json.loads(lane.run("gh", "pr", "view", str(args.pr), "--json", "autoMergeRequest", cwd=root)).get("autoMergeRequest"):
        lane.run("gh", "pr", "merge", str(args.pr), "--disable-auto", cwd=root)
    current = state.data["batches"][-1] if state.data["batches"] else None
    if not current or current["status"] != OPEN:
        current = {"id": (current["id"] + 1) if current else 1, "status": OPEN, "members": []}
        state.data["batches"].append(current)
    if any(m["task"] == args.task for b in state.data["batches"] if b["status"] in (OPEN, SEALED, RED) for m in b["members"]):
        raise lane.Refused(f"{args.task} is already in a batch")
    current["members"].append({
        "task": args.task, "pr": args.pr, "head": lane.head_state(root)[0], "branch": lane.run(
            "git", "rev-parse", "--abbrev-ref", "HEAD", cwd=root), "root": str(root), "own_proof": record.get("selection", "full"),
        "merge": {"detail": args.detail, "delegated": args.delegated, "style": args.style,
                  "critical": args.critical, "non_critical": getattr(args, "non_critical", None),
                  "filed": args.filed}})
    state.save()
    sealed = [b["id"] for b in state.data["batches"] if b["status"] == SEALED]
    note = f"batch {sealed[0]} is sealed and being proved; this pull request joined the next batch" if sealed else ""
    return {"ok": True, "batch": current["id"], "members": [m["task"] for m in current["members"]], "note": note}


def remove(args, root, lane, state) -> dict:
    """A member that does not merge onto the batch leaves it, so the rest of the batch can be proved again."""
    if not args.task:
        raise lane.Refused("train remove needs --task")
    batch = state.batch(args.batch, lane)
    if batch["status"] not in (OPEN, RED):
        raise lane.Refused(f"batch {batch['id']} is {batch['status']}; only an open or red batch gives up a member")
    left = [m for m in batch["members"] if m["task"] != args.task]
    if len(left) == len(batch["members"]):
        raise lane.Refused(f"{args.task} is not in batch {batch['id']}")
    batch.update(members=left, status=OPEN)
    for key in ("head", "base_head", "commands"):
        batch.pop(key, None)
    state.save()
    return {"ok": True, "batch": batch["id"], "members": [m["task"] for m in left]}


def drop(root: Path, tree: Path, name: str) -> None:
    subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=root, capture_output=True)
    subprocess.run(["git", "worktree", "prune"], cwd=root, capture_output=True)
    subprocess.run(["git", "branch", "-D", name], cwd=root, capture_output=True)


def integrate(root: Path, lane, batch: dict, members: list[dict], base: str, suffix: str = "") -> Path:
    """A worktree of ``train/<n>``: base plus the members' heads, merged in order."""
    tree = home(root, lane) / f"wt-{batch['id']}{suffix}"
    name = f"train/{batch['id']}{suffix}"
    lane.run("git", "fetch", "origin", base, cwd=root)
    drop(root, tree, name)
    lane.run("git", "worktree", "add", "-b", name, str(tree), f"origin/{base}", cwd=root)
    for member in members:
        done = subprocess.run(["git", "merge", "--no-ff", "-m", f"train {batch['id']}: {member['task']}",
                               member["head"]], cwd=tree, capture_output=True, text=True)
        if done.returncode:
            drop(root, tree, name)
            raise lane.Refused(f"{member['task']} does not merge onto the batch: {(done.stdout + done.stderr).strip()[-300:]}")
    return tree


def run_commands(root: Path, lane, tree: Path, base: str) -> list[dict]:
    results = []
    for command in lane.proof_commands(root, base):
        clock = lane.datetime.now(lane.timezone.utc)
        done = subprocess.run(lane.proof_runner(root, command), shell=True, cwd=tree, capture_output=True, text=True)
        out = done.stdout + done.stderr
        results.append({"command": command, "exit_code": done.returncode, "started_at": clock.isoformat(),
                        "finished_at": lane.now(), "seconds": round((lane.datetime.now(lane.timezone.utc) - clock).total_seconds(), 1),
                        "tail": "" if done.returncode == 0 else lane.proof_tail(out),
                        "failed_tests": [] if done.returncode == 0 else lane.failed_test_ids(out)})
    return results


def alive(pid: int | None) -> bool:
    try:
        return bool(pid) and (os.kill(pid, 0) is None)
    except OSError:
        return False


def prove(args, root, lane, state) -> dict:
    batch = state.batch(args.batch, lane)
    busy = [b["id"] for b in state.data["batches"] if b["status"] == SEALED and b is not batch and alive(b.get("pid"))]
    if busy:
        raise lane.Refused(f"batch {busy[0]} is being proved; one batch proof runs at a time")
    # a green batch whose base moved can no longer merge, so it must be provable again
    stale = batch["status"] == GREEN and lane.run("git", "rev-parse", f"origin/{args.base}", cwd=root) != batch.get("base_head")
    if (batch["status"] not in (OPEN, RED, SEALED) and not stale) or not batch["members"]:
        raise lane.Refused(f"batch {batch['id']} is {batch['status']}; nothing to prove")
    if batch["status"] == SEALED and alive(batch.get("pid")):
        raise lane.Refused(f"batch {batch['id']} is being proved")
    before = batch["status"]
    for member in batch["members"]:  # a fix pushed to the member's branch after a red run joins the rerun
        ref = "HEAD" if member["branch"] == "HEAD" else f"refs/heads/{member['branch']}"  # a detached member worktree has no branch
        member["head"] = lane.run("git", "rev-parse", ref, cwd=Path(member["root"]))
    if not args.dry_run:
        # sealed before anything else: a pull request added from now on joins the next batch
        batch.update(status=SEALED, pid=os.getpid())
        state.save()
    number, directory = batch["id"], state.file.parent
    state.close()
    try:
        tree = integrate(root, lane, batch, batch["members"], args.base)
        head = lane.run("git", "rev-parse", "HEAD", cwd=tree)
        if args.dry_run:
            plan = [lane.proof_runner(root, c) for c in lane.proof_commands(root, args.base)]
            drop(root, tree, f"train/{number}")
            return {"ok": True, "dry_run": True, "batch": number, "integration_head": head,
                    "members": [m["task"] for m in batch["members"]], "would_run": plan}
        base_head = lane.run("git", "rev-parse", f"origin/{args.base}", cwd=root)
        results = run_commands(root, lane, tree, args.base)
    except BaseException:
        if not args.dry_run:
            reopened = State(directory)
            reopened.batch(number, lane).update(status=before)
            reopened.save()
            reopened.close()
        raise
    ok = all(r["exit_code"] == 0 for r in results)
    done = State(directory)
    try:
        batch = done.batch(number, lane)
        batch.update(status=GREEN if ok else RED, head=head, base_head=base_head, commands=results)
        batch.pop("pid", None)
        done.save()
    finally:
        done.close()
    if ok:
        for member in batch["members"]:
            record = {"task": member["task"], "head": member["head"], "tree_clean": True, "base": args.base,
                      "selection": "train", "why": [f"batch {number} integration HEAD {head}",
                                                    "members: " + ", ".join(m["task"] for m in batch["members"])],
                      "finished_at": lane.now(), "ok": True, "commands": results}
            target = Path(member["root"]) / lane.PROOFS / f"{member['task']}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_file.replace_text(target, json.dumps(record, indent=2) + "\n")
    return {"ok": ok, "batch": number, "integration_head": head, "commands": results}


def merge(args, root, lane, state) -> dict:
    batch = state.batch(args.batch, lane)
    if batch["status"] != GREEN:
        raise lane.Refused(f"batch {batch['id']} is {batch['status']}; only a green batch merges")
    lane.run("git", "fetch", "origin", args.base, cwd=root)
    if lane.run("git", "rev-parse", f"origin/{args.base}", cwd=root) != batch["base_head"]:
        # shortcut: any move of the base re-proves in full; refine to overlap checks when main moves often
        raise lane.Refused(f"{args.base} moved since the batch was proved (another batch merged); run train prove again."
                           " Merges are serial: the train state lock is held while a batch merges")
    if args.dry_run:
        return {"ok": True, "dry_run": True, "batch": batch["id"], "would_merge": [m["task"] for m in batch["members"]]}
    merged = []
    for member in batch["members"]:
        m = member["merge"]
        proof = (f"Proof: merge train batch {batch['id']}, integration HEAD {batch['head']}, "
                 f"members {', '.join(x['task'] for x in batch['members'])}.")
        lane.cmd_merge(Namespace(task=member["task"], pr=member["pr"], style=m["style"], detail=f"{m['detail']}\n\n{proof}" if m["detail"] else proof,
                                 delegated=m["delegated"], critical=m["critical"], non_critical=m["non_critical"],
                                 filed=m["filed"]), Path(member["root"]))
        merged.append(member["task"])
    batch["status"] = MERGED
    state.save()
    return {"ok": True, "batch": batch["id"], "merged": merged}


def bisect(args, root, lane, state) -> dict:
    """The smallest prefix of members whose integration is red names the culprit (one proof per step)."""
    batch = state.batch(args.batch, lane)
    low, high = 1, len(batch["members"])
    while low < high:
        mid = (low + high) // 2
        tree = integrate(root, lane, batch, batch["members"][:mid], args.base, suffix="-bisect")
        red = any(r["exit_code"] for r in run_commands(root, lane, tree, args.base))
        low, high = (low, mid) if red else (mid + 1, high)
    drop(root, home(root, lane) / f"wt-{batch['id']}-bisect", f"train/{batch['id']}-bisect")
    return {"ok": True, "culprit": batch["members"][low - 1]["task"], "note": "red from this member's merge onward"}


def cmd_train(args, root: Path, lane) -> dict:
    directory = home(root, lane)
    state = State(directory)
    try:  # prove closes the state itself while the proof runs, so a batch can be joined meanwhile
        if args.verb == "status":
            return {"ok": True, "batches": [{"id": b["id"], "status": b["status"],
                                             "members": [m["task"] for m in b["members"]]} for b in state.data["batches"]]}
        return {"add": add, "remove": remove, "prove": prove, "merge": merge, "bisect": bisect}[args.verb](args, root, lane, state)
    finally:
        if not state.handle.closed:
            state.close()
