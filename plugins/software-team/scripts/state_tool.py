#!/usr/bin/env python3
"""Deterministic run-state operations for the develop flow.

The orchestrating conversation calls this tool instead of hand-writing
state.json: init acquires the single-run lock, snapshots the brief and
config into the run directory, and writes a schema-valid skeleton; the
mutation commands enforce the enums, the transition guard and the
run-complete guard. A hand-edited or malformed state fails `validate`.

Stdlib only. Exit 0 on success, 1 on a rule violation, 2 on usage errors.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

STEP_STATUSES = {"pending", "in_progress", "done", "blocked", "escalated"}
RUN_STATUSES = {"running", "waiting_gate", "blocked", "escalated", "complete"}
STEP_IDS = ["0", "1", "2", "3", "4", "5"]
LOCK_NAME = ".lock"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fail(msg: str, code: int = 1) -> int:
    print(f"state_tool: {msg}", file=sys.stderr)
    return code


def load(state_path: Path) -> dict:
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(fail(f"no state file at {state_path}", 2))
    except json.JSONDecodeError as exc:
        raise SystemExit(fail(f"state.json is not valid JSON: {exc}"))


def save(state_path: Path, state: dict) -> None:
    state["updated_at"] = now()
    state_path.write_text(json.dumps(state, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def validate_state(state: dict) -> list[str]:
    problems: list[str] = []
    required = ["run_id", "request", "status", "current_step", "steps", "gates",
                "iterations", "bindings", "ownership", "created_at", "updated_at"]
    for key in required:
        if key not in state:
            problems.append(f"missing key: {key}")
    if state.get("status") not in RUN_STATUSES:
        problems.append(f"run status not in enum: {state.get('status')}")
    steps = state.get("steps", {})
    for sid in STEP_IDS:
        step = steps.get(sid)
        if not isinstance(step, dict):
            problems.append(f"missing step: {sid}")
            continue
        if step.get("status") not in STEP_STATUSES:
            problems.append(f"step {sid} status not in enum: {step.get('status')}")
        if "artifact" not in step or "attempts" not in step:
            problems.append(f"step {sid} missing artifact/attempts")
    for name, gate in state.get("gates", {}).items():
        if not isinstance(gate, dict) or "decision" not in gate or "decided_at" not in gate:
            problems.append(f"gate {name} missing decision/decided_at")
    iterations = state.get("iterations", {})
    for counter in ("review", "qa"):
        if not isinstance(iterations.get(counter), int):
            problems.append(f"iterations.{counter} missing or not an integer")
    for role in state.get("ownership", {}):
        if not role.replace("_", "").isalnum() or role != role.lower():
            problems.append(f"ownership key not a snake_case role name: {role}")
    if state.get("status") == "complete":
        for sid in STEP_IDS:
            if steps.get(sid, {}).get("status") != "done":
                problems.append(f"run complete but step {sid} is not done")
    return problems


def cmd_init(args) -> int:
    workspace = Path(args.workspace)
    runs = workspace / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    lock = runs / LOCK_NAME
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps({"run_id": args.run_id, "pid": os.getpid(), "acquired_at": now()}) + "\n")
    except FileExistsError:
        holder = lock.read_text(encoding="utf-8", errors="replace").strip()
        return fail(
            f"another run holds the lock ({holder}); resume it or, after confirming it is dead, "
            f"remove {lock} and retry"
        )

    run_dir = runs / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.constitution, run_dir / "constitution.md")
    if args.brief:
        shutil.copyfile(args.brief, run_dir / "brief.snapshot.md")
    config = workspace / "config.json"
    if config.is_file():
        shutil.copyfile(config, run_dir / "config.snapshot.json")

    state = {
        "run_id": args.run_id,
        "request": args.request,
        "status": "running",
        "current_step": 0,
        "steps": {sid: {"status": "pending", "artifact": "", "attempts": 0} for sid in STEP_IDS},
        "gates": {},
        "iterations": {"review": 0, "qa": 0},
        "bindings": json.loads(args.bindings) if args.bindings else {},
        "ownership": {},
        "created_at": now(),
        "updated_at": now(),
    }
    state["steps"]["0"]["status"] = "in_progress"
    save(run_dir / "state.json", state)
    print(f"state_tool: initialized {run_dir / 'state.json'} and acquired the run lock")
    return 0


def cmd_set_step(args) -> int:
    state = load(Path(args.state))
    sid = str(args.step)
    if sid not in STEP_IDS:
        return fail(f"unknown step: {sid}", 2)
    if args.status not in STEP_STATUSES:
        return fail(f"status not in enum: {args.status}", 2)
    if args.status == "in_progress":
        for prior in STEP_IDS[: STEP_IDS.index(sid)]:
            if state["steps"][prior]["status"] != "done":
                return fail(f"transition guard: step {prior} is not done; step {sid} cannot start")
    state["steps"][sid]["status"] = args.status
    if args.artifact:
        state["steps"][sid]["artifact"] = args.artifact
    if args.bump_attempts:
        state["steps"][sid]["attempts"] += 1
    if args.status == "in_progress":
        state["current_step"] = int(sid)
    save(Path(args.state), state)
    print(f"state_tool: step {sid} -> {args.status}")
    return 0


def cmd_record_gate(args) -> int:
    state = load(Path(args.state))
    state.setdefault("gates", {})[args.gate] = {"decision": args.decision, "decided_at": now()}
    save(Path(args.state), state)
    print(f"state_tool: gate {args.gate} -> {args.decision}")
    return 0


def cmd_bump(args) -> int:
    state = load(Path(args.state))
    if args.counter not in ("review", "qa"):
        return fail(f"unknown counter: {args.counter}", 2)
    state["iterations"][args.counter] += 1
    save(Path(args.state), state)
    print(f"state_tool: iterations.{args.counter} = {state['iterations'][args.counter]}")
    return 0


def cmd_set_ownership(args) -> int:
    state = load(Path(args.state))
    ownership = json.loads(args.ownership)
    for role in ownership:
        if role != role.lower() or not role.replace("_", "").isalnum():
            return fail(f"ownership key must be a snake_case role name: {role}")
    state["ownership"] = ownership
    save(Path(args.state), state)
    print("state_tool: ownership map stored")
    return 0


def cmd_set_run_status(args) -> int:
    state = load(Path(args.state))
    if args.status not in RUN_STATUSES:
        return fail(f"status not in enum: {args.status}", 2)
    if args.status == "complete":
        not_done = [sid for sid in STEP_IDS if state["steps"][sid]["status"] != "done"]
        if not_done:
            return fail(f"run-complete guard: steps not done: {', '.join(not_done)}")
        bad_gates = [name for name, g in state.get("gates", {}).items()
                     if "decision" not in g or "decided_at" not in g]
        if bad_gates:
            return fail(f"run-complete guard: gates missing decision/decided_at: {', '.join(bad_gates)}")
    state["status"] = args.status
    save(Path(args.state), state)
    print(f"state_tool: run status -> {args.status}")
    return 0


def cmd_validate(args) -> int:
    problems = validate_state(load(Path(args.state)))
    if problems:
        for p in problems:
            print(f"state_tool: INVALID: {p}", file=sys.stderr)
        return 1
    print("state_tool: state is valid")
    return 0


def cmd_release_lock(args) -> int:
    lock = Path(args.workspace) / "runs" / LOCK_NAME
    if lock.is_file():
        lock.unlink()
        print("state_tool: lock released")
    else:
        print("state_tool: no lock to release")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic run-state operations.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init")
    p.add_argument("--workspace", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--request", required=True)
    p.add_argument("--constitution", required=True)
    p.add_argument("--brief", default="")
    p.add_argument("--bindings", default="")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("set-step")
    p.add_argument("--state", required=True)
    p.add_argument("--step", required=True)
    p.add_argument("--status", required=True)
    p.add_argument("--artifact", default="")
    p.add_argument("--bump-attempts", action="store_true")
    p.set_defaults(func=cmd_set_step)

    p = sub.add_parser("record-gate")
    p.add_argument("--state", required=True)
    p.add_argument("--gate", required=True)
    p.add_argument("--decision", required=True)
    p.set_defaults(func=cmd_record_gate)

    p = sub.add_parser("bump")
    p.add_argument("--state", required=True)
    p.add_argument("--counter", required=True)
    p.set_defaults(func=cmd_bump)

    p = sub.add_parser("set-ownership")
    p.add_argument("--state", required=True)
    p.add_argument("--ownership", required=True, help="JSON object: snake_case role -> path list")
    p.set_defaults(func=cmd_set_ownership)

    p = sub.add_parser("set-run-status")
    p.add_argument("--state", required=True)
    p.add_argument("--status", required=True)
    p.set_defaults(func=cmd_set_run_status)

    p = sub.add_parser("validate")
    p.add_argument("--state", required=True)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("release-lock")
    p.add_argument("--workspace", required=True)
    p.set_defaults(func=cmd_release_lock)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
