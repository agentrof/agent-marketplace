#!/usr/bin/env python3
"""Record how long each flow step and role spawn takes, and compare it with its budget.

Process switch ``step_timing`` at ``recorded`` turns recording on. At
``off``, the default, ``start`` and ``end`` read the switch, write nothing and
print ``{"recorded": false}``, so a flow that calls them pays one policy read
and leaves no file.

A run (one entry invocation, such as one Backlog Planning revision) is one
append-only JSON Lines file, ``.agentrof/agent-marketplace/timing/<run>.jsonl``
in the project checkout. It sits outside ``.runtime/`` on purpose: runtime
scratch may be cleaned, while timing records are durable evidence for
promoting a switch and are never removed by a package command.

A span is either a flow ``step`` or a role ``spawn`` inside one, and carries a
phase: ``reading``, ``writing``, ``review``, ``re_review``, ``waiting``,
``approval``, ``compile`` or ``other``. A spawn names its parent step, so a
step's time breaks down into its children's phases and the unattributed
rest. ``end`` may record metrics, such as manifest bytes, closure size or
findings, as ``--metric name=integer``.

Budgets come from the ``step_budgets`` policy key: its parameters map budget
ids to whole minutes. A span names its budget with ``--budget`` (a step's own
name is used when it is a budget id). The moment a budgeted span ends over
its budget, ``end`` returns and records an ``overrun`` with the breakdown by
phase, the largest contributor and the lever that addresses it; ``overruns``
lists the spans still open that have already passed their budget. ``report``
prints the per-step breakdown of a run against its budgets, and ``report
--write`` keeps it durable as ``<run>.report.json`` beside the records.

Commands:
  step_timing.py start  --run R --step S [--kind step|spawn] [--phase P] [--role X]
                        [--parent SPAN] [--budget ID] [--project-root P] [--at ISO]
  step_timing.py end    --run R --span SPAN [--metric k=v]... [--project-root P] [--at ISO]
  step_timing.py overruns --run R [--project-root P] [--at ISO]
  step_timing.py report --run R [--project-root P] [--write]
  step_timing.py runs   [--project-root P]

``--at`` replaces the clock for a backfilled or replayed record.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_file  # noqa: E402
import file_lock  # noqa: E402

SWITCH = "step_timing"
RECORDED = "recorded"
BUDGET_SWITCH = "step_budgets"
TIMING = Path(".agentrof") / "agent-marketplace" / "timing"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
STEP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]{0,127}$")
KINDS = ("step", "spawn")
PHASES = ("reading", "writing", "review", "re_review", "waiting", "approval", "compile", "other")
# The lever each phase points at when it is the largest share of an overrun.
LEVERS = {
    "reading": "context pack missing or not used (context_pack role_digest)",
    "review": "closure too wide or relations missing (review_scope impact_closure)",
    "re_review": "confirmation re-review not limited to the fixed lines (review_scope impact_closure)",
    "waiting": "review levels run in sequence (review_levels concurrent_when_independent)",
    "writing": "writer transcribes what a compiler stub could write",
    "approval": "owner gate wait",
    "compile": "compiler run time",
    "other": "unattributed step time; record the spawns of this step",
    "unattributed": "unattributed step time; record the spawns of this step",
}


class Refused(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def name(value: str) -> str:
    if not NAME_RE.match(value):
        raise argparse.ArgumentTypeError(f"invalid run name: {value!r}")
    return value


def step_name(value: str) -> str:
    if not STEP_RE.match(value):
        raise argparse.ArgumentTypeError(f"invalid step name: {value!r}")
    return value


def metric(value: str) -> tuple[str, int]:
    key, sep, raw = value.partition("=")
    if not sep or not re.fullmatch(r"[a-z][a-z0-9_]*", key) or not re.fullmatch(r"-?[0-9]+", raw):
        raise argparse.ArgumentTypeError(f"metric must be name=integer: {value!r}")
    return key, int(raw)


def project_root(value: Path | None) -> Path:
    start = (value or Path.cwd()).resolve()
    out = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise Refused("TIMING_NOT_A_CHECKOUT", f"{start} is not inside a Git checkout")
    return Path(out.stdout.strip()).resolve()


def policy_values(root: Path) -> dict:
    import process_policy
    try:
        return process_policy.effective_values(process_policy.docs_root(root))[0]
    except ValueError as exc:
        raise Refused("TIMING_POLICY", f"process policy cannot set {SWITCH}: {exc}") from exc


def recording(values: dict) -> bool:
    return values.get(SWITCH, {}).get("value") == RECORDED


def budgets(values: dict) -> dict[str, int]:
    """The budgets in force, in minutes, by budget id; none while step_budgets is off."""
    spec = values.get(BUDGET_SWITCH, {})
    if spec.get("value") == spec.get("default"):
        return {}
    return {key: value for key, value in (spec.get("parameters") or {}).items()
            if isinstance(value, int) and value > 0}


def now(at: str | None) -> datetime:
    if at is None:
        return datetime.now(timezone.utc)
    try:
        moment = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Refused("TIMING_BAD_TIME", f"--at is not an ISO time: {at!r}") from exc
    if moment.tzinfo is None:
        raise Refused("TIMING_BAD_TIME", "--at needs a UTC offset")
    return moment.astimezone(timezone.utc)


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def parse_stamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def records_path(root: Path, run: str) -> Path:
    return root / TIMING / f"{run}.jsonl"


def read_events(path: Path) -> list[dict]:
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise Refused("TIMING_UNSAFE_PATH", f"timing records refuse the linked path {path}")
    if not path.is_file():
        return []
    events = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise Refused("TIMING_CORRUPT", f"{path.name} line {number} is not JSON: {exc}") from exc
    return events


EVENT_KEYS = {"start": ("span", "step", "kind", "phase", "at"), "end": ("span", "at"),
              "overrun": ("span", "at")}


def spans(events: list[dict]) -> dict[str, dict]:
    """Fold start and end events into spans, in start order."""
    result: dict[str, dict] = {}
    for number, event in enumerate(events, start=1):
        kind = event.get("event") if isinstance(event, dict) else None
        if kind not in EVENT_KEYS or any(
                not isinstance(event.get(key), str) for key in EVENT_KEYS[kind]):
            raise Refused("TIMING_CORRUPT", f"timing event {number} lacks its required keys")
        if event["event"] == "start":
            result[event["span"]] = {key: value for key, value in event.items() if key != "event"}
        elif event["event"] == "end" and event["span"] in result:
            span = result[event["span"]]
            span["ended_at"] = event["at"]
            span["seconds"] = round((parse_stamp(event["at"]) - parse_stamp(span["at"]))
                                    .total_seconds(), 3)
            if event.get("metrics"):
                span["metrics"] = event["metrics"]
    return result


def breakdown(span: dict, all_spans: dict[str, dict], until: datetime | None = None) -> dict:
    """Split a span's time into its children's phases and the unattributed rest."""
    total = span.get("seconds")
    if total is None:
        total = round(((until or datetime.now(timezone.utc)) - parse_stamp(span["at"]))
                      .total_seconds(), 3)
    phases: dict[str, float] = {}
    children = []
    for child in all_spans.values():
        if child.get("parent") != span["span"]:
            continue
        seconds = child.get("seconds")
        if seconds is None:
            seconds = round(((until or datetime.now(timezone.utc)) - parse_stamp(child["at"]))
                            .total_seconds(), 3)
        phases[child["phase"]] = round(phases.get(child["phase"], 0.0) + seconds, 3)
        children.append({"span": child["span"], "phase": child["phase"],
                         "role": child.get("role"), "seconds": seconds})
    if not children:
        phases = {span["phase"]: total}
    else:
        # Parallel children may sum past the step's wall clock; the rest is never negative.
        rest = round(total - sum(phases.values()), 3)
        if rest > 0:
            phases["unattributed"] = rest
    largest_phase = max(sorted(phases), key=lambda key: phases[key])
    largest_child = max(children, key=lambda child: (child["seconds"], child["span"]),
                        default=None)
    return {"seconds": total, "by_phase": dict(sorted(phases.items())),
            "largest_phase": largest_phase, "largest_contributor": largest_child or {
                "span": span["span"], "phase": largest_phase, "seconds": phases[largest_phase]},
            "lever": LEVERS[largest_phase]}


def overrun(span: dict, all_spans: dict[str, dict], limits: dict[str, int],
            until: datetime | None = None) -> dict | None:
    budget = span.get("budget")
    if budget not in limits:
        return None
    parts = breakdown(span, all_spans, until)
    if parts["seconds"] <= limits[budget] * 60:
        return None
    return {"span": span["span"], "step": span["step"], "budget": budget,
            "budget_minutes": limits[budget], "minutes": round(parts["seconds"] / 60, 2),
            "over_minutes": round(parts["seconds"] / 60 - limits[budget], 2), **parts}


def append(root: Path, run: str, build) -> dict:
    """Under the run's lock, build one event from the current spans and append it."""
    path = records_path(root, run)
    try:
        atomic_file.real_directory(root, TIMING)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND
                             | getattr(os, "O_NOFOLLOW", 0), 0o666)
    except OSError as exc:
        raise Refused("TIMING_UNSAFE_PATH", f"timing records refuse {path}: {exc}") from exc
    try:
        file_lock.lock(descriptor)
        try:
            events = read_events(path)
            event, result = build(events)
            os.write(descriptor, (json.dumps(event, sort_keys=True) + "\n").encode("utf-8"))
            for extra in result.pop("_extra_events", []):
                os.write(descriptor, (json.dumps(extra, sort_keys=True) + "\n").encode("utf-8"))
            return result
        finally:
            file_lock.unlock(descriptor)
    finally:
        os.close(descriptor)


def start(root: Path, values: dict, *, run: str, step: str, kind: str = "step",
          phase: str = "other", role: str | None = None, parent: str | None = None,
          budget: str | None = None, at: str | None = None) -> dict:
    if not recording(values):
        return {"ok": True, "recorded": False}
    moment = now(at)
    limits = budgets(values)

    def build(events):
        known = spans(events)
        if parent is not None and (parent not in known or "ended_at" in known[parent]):
            raise Refused("TIMING_UNKNOWN_PARENT", f"parent span {parent!r} is not open in run {run}")
        if kind == "spawn" and parent is None:
            raise Refused("TIMING_SPAWN_NEEDS_PARENT", "a role spawn names its parent step span")
        base = f"{step}/{role}" if role else step
        count = sum(1 for span in known.values() if span.get("base") == base)
        span_id = f"{base}#{count + 1}"
        chosen = budget if budget is not None else (step if step in limits else None)
        event = {"event": "start", "span": span_id, "base": base, "step": step, "kind": kind,
                 "phase": phase, "at": stamp(moment)}
        for key, value in (("role", role), ("parent", parent), ("budget", chosen)):
            if value is not None:
                event[key] = value
        result = {"ok": True, "recorded": True, "span": span_id, "at": event["at"]}
        if chosen is not None:
            result["budget_minutes"] = limits.get(chosen)
        return event, result

    return append(root, run, build)


def end(root: Path, values: dict, *, run: str, span: str, metrics: dict[str, int] | None = None,
        at: str | None = None) -> dict:
    if not recording(values):
        return {"ok": True, "recorded": False}
    moment = now(at)
    limits = budgets(values)

    def build(events):
        known = spans(events)
        if span not in known:
            raise Refused("TIMING_UNKNOWN_SPAN", f"span {span!r} was never started in run {run}")
        if "ended_at" in known[span]:
            raise Refused("TIMING_SPAN_ENDED", f"span {span!r} already ended")
        if parse_stamp(known[span]["at"]) > moment:
            raise Refused("TIMING_BAD_TIME", f"span {span!r} cannot end before it started")
        event = {"event": "end", "span": span, "at": stamp(moment)}
        if metrics:
            event["metrics"] = dict(sorted(metrics.items()))
        closed = spans([*events, event])
        result = {"ok": True, "recorded": True, "span": span,
                  "seconds": closed[span]["seconds"]}
        found = overrun(closed[span], closed, limits)
        if found is not None:
            result["overrun"] = found
            result["_extra_events"] = [{"event": "overrun", "at": event["at"], **found}]
        return event, result

    return append(root, run, build)


def open_overruns(root: Path, values: dict, run: str, at: str | None = None) -> dict:
    limits = budgets(values)
    moment = now(at)
    known = spans(read_events(records_path(root, run)))
    found = [item for item in (overrun(span, known, limits, moment)
                               for span in known.values() if "ended_at" not in span) if item]
    return {"ok": True, "run": run, "overruns": found}


def report(root: Path, values: dict, run: str, write: bool = False) -> dict:
    path = records_path(root, run)
    events = read_events(path)
    if not events:
        raise Refused("TIMING_NO_RUN", f"no timing records for run {run}")
    limits = budgets(values)
    known = spans(events)
    last = max(parse_stamp(event["at"]) for event in events)
    steps = []
    for span in known.values():
        if span["kind"] != "step":
            continue
        parts = breakdown(span, known, last)
        budget = span.get("budget")
        verdict = ("open" if "ended_at" not in span else "no_budget" if budget not in limits
                   else "over" if parts["seconds"] > limits[budget] * 60 else "within")
        row = {"span": span["span"], "step": span["step"], "started_at": span["at"],
               "ended_at": span.get("ended_at"), "minutes": round(parts["seconds"] / 60, 2),
               "budget": budget, "budget_minutes": limits.get(budget), "verdict": verdict,
               **parts}
        if span.get("metrics"):
            row["metrics"] = span["metrics"]
        steps.append(row)
    first = min(parse_stamp(event["at"]) for event in events)
    result = {"ok": True, "run": run, "wall_minutes": round((last - first).total_seconds() / 60, 2),
              "steps": steps, "over_budget": [row["span"] for row in steps if row["verdict"] == "over"],
              "open": [span["span"] for span in known.values() if "ended_at" not in span]}
    if write:
        target = path.with_name(f"{run}.report.json")
        atomic_file.replace_text(target, json.dumps(result, indent=2, sort_keys=True) + "\n")
        result["path"] = str(target.relative_to(root))
    return result


def runs(root: Path) -> dict:
    folder = root / TIMING
    names = sorted(path.stem for path in folder.glob("*.jsonl")) if folder.is_dir() else []
    return {"ok": True, "runs": names}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    begun = sub.add_parser("start")
    begun.add_argument("--run", type=name, required=True)
    begun.add_argument("--step", type=step_name, required=True)
    begun.add_argument("--kind", choices=KINDS, default="step")
    begun.add_argument("--phase", choices=PHASES, default="other")
    begun.add_argument("--role", type=name)
    begun.add_argument("--parent")
    begun.add_argument("--budget", type=step_name)
    finished = sub.add_parser("end")
    finished.add_argument("--run", type=name, required=True)
    finished.add_argument("--span", required=True)
    finished.add_argument("--metric", type=metric, action="append", default=[])
    late = sub.add_parser("overruns")
    late.add_argument("--run", type=name, required=True)
    shown = sub.add_parser("report")
    shown.add_argument("--run", type=name, required=True)
    shown.add_argument("--write", action="store_true")
    listed = sub.add_parser("runs")
    for command in (begun, finished, late, shown, listed):
        command.add_argument("--project-root", type=Path)
    for command in (begun, finished, late):
        command.add_argument("--at")
    args = parser.parse_args(argv)
    try:
        root = project_root(args.project_root)
        if args.command == "runs":
            result = runs(root)
        else:
            values = policy_values(root)
            if args.command == "start":
                result = start(root, values, run=args.run, step=args.step, kind=args.kind,
                               phase=args.phase, role=args.role, parent=args.parent,
                               budget=args.budget, at=args.at)
            elif args.command == "end":
                result = end(root, values, run=args.run, span=args.span,
                             metrics=dict(args.metric), at=args.at)
            elif args.command == "overruns":
                result = open_overruns(root, values, args.run, args.at)
            else:
                result = report(root, values, args.run, args.write)
    except Refused as exc:
        print(json.dumps({"ok": False, "code": exc.code, "error": str(exc)}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
