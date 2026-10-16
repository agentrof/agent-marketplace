#!/usr/bin/env python3
"""Fast-lane task path: one task, one branch, one pull request, one clock.

Process switch ``process_profile`` at ``fast_lane`` routes a request through
this script instead of the Requirement, Backlog and Delivery ceremonies:

- ``start`` creates ``task/<id>-<slug>`` from the target branch and writes
  the task ``workspace/docs/backlog/tasks/<id>-<slug>.md`` with its
  ``requested_at`` time and the proposed ``rigor``.
- ``lint`` checks one task: title, size, rigor, 3 to 8 acceptance criteria
  and a test list. ``approve`` lints it and writes ``approved_at``.
- ``overlap`` warns, never refuses, when an open pull request touches the
  same files as this branch.
- ``open-pr`` marks the task delivered, pushes the branch and opens the PR.
- ``merge`` posts the reviewers' pass on the PR and, at rigor ``normal``,
  turns on auto-merge; at rigor ``high`` the owner merges.
- ``criteria`` lists a backlog story's numbered criteria, its open linked tasks
  and a warning when the story changed after an open task was requested;
  ``board`` groups tasks by epic and story and marks a story delivered once
  merged tasks cover every criterion, derived on every run.
- ``report`` is the retrospective: the tasks hold the request and approval
  times, GitHub holds the PR, review, CI and merge times, read at report
  time, and ``session_cost.py`` reads each task's cost and active minutes from
  the host session transcripts, so every wait gets an owner; ``--deep`` adds reading waste. ``--export`` rebuilds a derived file under ``.agentrof/``;
  ``--write-report`` keeps a Delivery's summary under
  ``workspace/docs/delivery/retrospectives/``. ``file-issues`` opens one
  issue per proposal the user approved, in the project's own repository.

Nothing is written after a merge, and no file is shared between tasks: each
task travels in its own pull request.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import statistics
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from ba_compile import parse_frontmatter

TASKS = Path("workspace") / "docs" / "backlog" / "tasks"
RETROSPECTIVES = Path("workspace") / "docs" / "delivery" / "retrospectives"
EXPORT = Path(".agentrof") / "retrospective"
TASK_TYPE = "task"
TASK_STATUSES = ("active", "delivered", "dropped")
SIZES = ("s", "m", "l")
RIGORS = ("normal", "high")
# A request naming one of these proposes rigor high; the owner confirms on the task.
HIGH_RIGOR_RE = re.compile(
    r"\b(auth\w*|login|password\w*|credential\w*|secret\w*|token\w*|api[ -]?key\w*|"
    r"permission\w*|delet\w*|drop\w*|purg\w*|wip\w*|truncat\w*|irreversib\w*|migrat\w*)\b",
    re.IGNORECASE)
ID_RE = re.compile(r"^TK-[0-9]{3,}$")
EPIC_RE = re.compile(r"^EP-[0-9]{3,}$")
STORY_RE = re.compile(r"^ST-[0-9]{3,}$")
STORIES = Path("workspace") / "docs" / "backlog" / "epics"
BOARD = EXPORT / "board.md"
DELIVERY_RE = re.compile(r"^DLV-[0-9]{3,}$")
AC_RANGE = (3, 8)
# knowledge: analysis, design, architecture, backlog and contract documents; build: code, devops, tests.
KINDS = ("build", "knowledge")
TARGETS_FILE = Path(__file__).resolve().parent.parent / "skill-content" / "deliver" / "data" / "targets.json"
# Request to merge targets in minutes per kind: s, m, l, max and the average.
TARGETS = json.loads(TARGETS_FILE.read_text(encoding="utf-8"))["kinds"]
TARGET_MINUTES = TARGETS["build"]["average"]
HISTORY = EXPORT / "history.jsonl"
LEDGER = Path(".agentrof") / "plugin-ledger.md"
PENDING_UPSTREAM = "## Pending upstream"
WAITS = ("agent_work", "ci_wait", "review_wait", "owner_wait", "idle")
# Who removes a loss: the project (its CI and rules), this plugin, the host tool
# (GitHub) or the owner; plugin causes wait in the plugin ledger.
STEP_OWNERS = {"define": "owner", "write": "process", "ci": "project", "review": "plugin",
               "merge_wait": "host tool"}
WAIT_OWNERS = {"ci_wait": "project", "review_wait": "plugin", "owner_wait": "owner",
               "idle": "host tool"}
WAIT_LEVERS = {
    "ci_wait": "Cut the CI wait: run only the touched suites on the PR, cache setup, and start"
               " the reviewer while CI runs.",
    "review_wait": "Cut the review wait: start the reviewer as soon as the PR opens and give it"
                   " the diff and the task only.",
    "owner_wait": "Cut the owner wait: approve tasks from a short queue, or delegate approval of"
                  " rigor normal tasks.",
    "idle": "Remove idle time: no session was active while nothing waited on CI, a reviewer or"
            " the owner; resume dropped sessions at once and keep one task per worktree.",
}
REVIEW_MARK = "fast-lane review: pass"
# step -> (start, end); CI and review run side by side after the PR opens.
STEPS = {"define": ("requested_at", "approved_at"), "write": ("approved_at", "pr_opened"),
         "ci": ("pr_opened", "ci_green"), "review": ("pr_opened", "review"),
         "merge_wait": (("ci_green", "review"), "merged")}
LEVERS = {
    "define": "Write the task in one pass from the request: cap it at 5 criteria, name"
            " tests by file and approve it at once.",
    "write": "Split tasks whose write step runs long, or run backend and frontend"
             " writers side by side on disjoint files.",
    "ci": "Run only the checks the diff touches (skip unit suites for document-only changes,"
          " scope the vault gate to the changed trees) and cache dependencies in CI.",
    "review": "Give the reviewer the diff and the task only, single pass, with a"
              " time budget; file non-critical findings as issues.",
    "merge_wait": "Remove the slower of CI and review from the merge path, or drop a"
                  " required approval or up-to-date rule that auto-merge waits on.",
}


class Refused(Exception):
    pass


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def run(*args: str, cwd: Path | None = None) -> str:
    out = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True)
    if out.returncode != 0:
        raise Refused(f"{' '.join(args[:3])} failed: {out.stderr.strip() or out.stdout.strip()}")
    return out.stdout.strip()


def project_root(value: str | None) -> Path:
    start = Path(value or Path.cwd()).resolve()
    return Path(run("git", "-C", str(start), "rev-parse", "--show-toplevel")).resolve()


# --- task ------------------------------------------------------------------

def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48].strip("-") or "task"


def next_id(names: list[str]) -> str:
    """One past the highest task number among task files and task branch names."""
    numbers = [int(match.group(1)) for name in names
               if (match := re.search(r"(?:^|/)tk-([0-9]+)-", name))]
    return f"TK-{max(numbers, default=0) + 1:03d}"


def taken_names(root: Path, base: str) -> list[str]:
    """Tasks on the target branch and every task branch on the remote, so parallel
    tasks never share an id; ``start`` pushes its branch at once to claim it."""
    tasks = run("git", "ls-tree", "--name-only", f"origin/{base}", f"{TASKS.as_posix()}/",
                cwd=root).splitlines()
    branches = run("git", "for-each-ref", "--format=%(refname:short)",
                   "refs/remotes/origin/task/", cwd=root).splitlines()
    return tasks + branches


def proposed_rigor(request: str) -> tuple[str, list[str]]:
    hits = sorted({match.group(0).lower() for match in HIGH_RIGOR_RE.finditer(request)})
    return ("high" if hits else "normal"), hits


def task_text(task: str, title: str, request: str, requested_at: str, delivery: str,
              rigor: str, epic: str = "", source_story: str = "", covers: list[int] | None = None,
              kind: str = "build", size: str = "s") -> str:
    grouped = f"delivery: {delivery}\n" if delivery else ""
    if kind == "knowledge":
        # A knowledge task needs no separate approval: the owner's request is the approval.
        return (f"---\ntype: {TASK_TYPE}\ntitle: {json.dumps(title)}\nstatus: active\nid: {task}\n"
                f"task_kind: knowledge\nrequested_at: {requested_at}\napproved_at: {requested_at}\n"
                f"size: {size}\nrigor: {rigor}\n{grouped}"
                f"tags:\n  - doc/{TASK_TYPE}\n  - status/active\naliases:\n  - {task}\n---\n"
                f"# {title}\n\n## Request\n\n{request.strip()}\n\n<!-- sec: nav -->\n## Navigation\n\n"
                "- [[maps/backlog|Backlog]]\n")
    grouped += f"epic: {epic}\n" if epic else ""
    if source_story:
        grouped += f"source_story: {source_story}\ncovers:\n" + "".join(
            f"  - {number}\n" for number in covers or [])
    return (f"---\ntype: {TASK_TYPE}\ntitle: {json.dumps(title)}\nstatus: active\nid: {task}\n"
            f"requested_at: {requested_at}\nsize: s\nrigor: {rigor}\n{grouped}"
            f"tags:\n  - doc/{TASK_TYPE}\n  - status/active\naliases:\n  - {task}\n---\n"
            f"# {title}\n\n## Request\n\n{request.strip()}\n\n## Acceptance Criteria\n\n"
            "1. \n2. \n3. \n\n## Tests\n\n- \n\n<!-- sec: nav -->\n## Navigation\n\n"
            "- [[maps/backlog|Backlog]]\n")


def section(body: str, title: str) -> list[str]:
    match = re.search(rf"(?ms)^## {re.escape(title)}\s*$\n(.*?)(?=^## |^<!-- sec:|\Z)", body)
    return [line.strip() for line in match.group(1).splitlines() if line.strip()] if match else []


def task_props(path: Path) -> dict:
    return parse_frontmatter(path.read_text(encoding="utf-8"))[0]


def lint(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    props, start, error = parse_frontmatter(text)
    if error:
        return [error]
    body = "\n".join(text.splitlines()[start - 1:])
    errors = []
    if props.get("type") != TASK_TYPE:
        errors.append(f"type must be {TASK_TYPE}")
    if not ID_RE.match(str(props.get("id", ""))):
        errors.append("id must be TK-<number>")
    if not str(props.get("title", "")).strip():
        errors.append("title is required")
    if props.get("status") not in TASK_STATUSES:
        errors.append(f"status must be one of {', '.join(TASK_STATUSES)}")
    if props.get("size") not in SIZES:
        errors.append(f"size must be one of {', '.join(SIZES)}")
    if props.get("task_kind", "build") not in KINDS:
        errors.append(f"task_kind must be one of {', '.join(KINDS)}")
    if props.get("rigor", "normal") not in RIGORS:
        errors.append(f"rigor must be one of {', '.join(RIGORS)}")
    for key in ("requested_at", "approved_at"):
        if key in props:
            try:
                parse_time(props[key])
            except ValueError:
                errors.append(f"{key} must be an ISO UTC time")
    if "requested_at" not in props:
        errors.append("requested_at is required")
    if props.get("delivery") and not DELIVERY_RE.match(str(props["delivery"])):
        errors.append("delivery must be DLV-<number>")
    if props.get("epic") and not EPIC_RE.match(str(props["epic"])):
        errors.append("epic must be EP-<number>")
    if props.get("source_story") and not STORY_RE.match(str(props["source_story"])):
        errors.append("source_story must be ST-<number>")
    covers = covered(props)
    if props.get("source_story") and not covers:
        errors.append("covers must list the source story's criterion numbers this task delivers")
    if "covers" in props and not props.get("source_story"):
        errors.append("covers needs a source_story")
    if covers is None:
        errors.append("covers items must be positive criterion numbers")
    if not section(body, "Request"):
        errors.append("Request section is empty")
    if props.get("task_kind") == "knowledge":
        return errors
    criteria = [line for line in section(body, "Acceptance Criteria")
                if re.match(r"^[0-9]+\.\s+\S", line)]
    if not AC_RANGE[0] <= len(criteria) <= AC_RANGE[1]:
        errors.append(f"Acceptance Criteria needs {AC_RANGE[0]} to {AC_RANGE[1]} numbered"
                      f" criteria, found {len(criteria)}")
    if not [line for line in section(body, "Tests") if re.match(r"^-\s+\S", line)]:
        errors.append("Tests needs at least one '- <test>' line")
    return errors


def covered(props: dict) -> list[int] | None:
    """The criterion numbers a task covers; None when one is not a positive number."""
    raw = props.get("covers") or []
    items = raw if isinstance(raw, list) else [raw]
    numbers = [int(str(item)) for item in items if str(item).isdigit()]
    if len(numbers) != len(items) or any(number < 1 for number in numbers):
        return None
    return sorted(set(numbers))


def task_path(root: Path, task: str) -> Path:
    matches = sorted((root / TASKS).glob(f"{task.lower()}-*.md"))
    if len(matches) != 1:
        raise Refused(f"no single task for {task} under {TASKS}")
    return matches[0]


def set_field(path: Path, key: str, value: str) -> None:
    """Set one front matter scalar, keeping every other byte of the task."""
    text = path.read_text(encoding="utf-8")
    head, sep, body = text[4:].partition("\n---\n")
    if re.search(rf"(?m)^{key}:", head):
        head = re.sub(rf"(?m)^{key}:.*$", f"{key}: {value}", head, count=1)
    else:
        head = re.sub(r"(?m)^(requested_at:.*)$", rf"\1\n{key}: {value}", head, count=1)
    if key == "status":
        head = re.sub(r"(?m)^  - status/.*$", f"  - status/{value}", head, count=1)
    path.write_text("---\n" + head + sep + body, encoding="utf-8")


# --- GitHub ----------------------------------------------------------------

def changed_files(root: Path, base: str) -> set[str]:
    return set(filter(None, run("git", "diff", "--name-only", f"origin/{base}...HEAD",
                                cwd=root).splitlines()))


def overlap(root: Path, base: str) -> list[dict]:
    mine = changed_files(root, base)
    branch = run("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=root)
    listed = json.loads(run("gh", "pr", "list", "--state", "open", "--json",
                            "number,headRefName,files", cwd=root) or "[]")
    warnings = []
    for pr in listed:
        if pr.get("headRefName") == branch:
            continue
        shared = sorted(mine & {item.get("path") for item in pr.get("files") or []})
        if shared:
            warnings.append({"pr": pr["number"], "branch": pr["headRefName"], "files": shared})
    return warnings


def passed(check: dict) -> bool:
    return (check.get("conclusion") or check.get("state") or "").upper() in {
        "SUCCESS", "NEUTRAL", "SKIPPED"}


def ci_green_at(checks: list[dict]) -> str | None:
    """The completion time of the last check, once every check passed."""
    if not checks or not all(passed(check) for check in checks):
        return None
    times = [check["completedAt"] for check in checks if check.get("completedAt")]
    return max(times, key=parse_time) if times else None


def check_minutes(checks: list[dict]) -> dict[str, float]:
    durations = {}
    for check in checks:
        if check.get("startedAt") and check.get("completedAt"):
            durations[check.get("name") or check.get("context") or "check"] = round(
                (parse_time(check["completedAt"]) - parse_time(check["startedAt"])).total_seconds()
                / 60, 1)
    return durations


def review_at(comments: list[dict]) -> str | None:
    times = [comment["createdAt"] for comment in comments
             if REVIEW_MARK in (comment.get("body") or "") and comment.get("createdAt")]
    return min(times, key=parse_time) if times else None


def pull_requests(root: Path) -> dict[str, dict]:
    """The newest task pull request of each task id."""
    listed = json.loads(run("gh", "pr", "list", "--state", "all", "--limit", "1000", "--json",
                            "number,headRefName", cwd=root) or "[]")
    found: dict[str, dict] = {}
    for pr in listed:
        match = re.match(r"^task/(tk-[0-9]+)-", pr.get("headRefName") or "")
        if match:
            found.setdefault(match.group(1).upper(), pr)
    return found


def pr_times(root: Path, number) -> dict:
    data = json.loads(run("gh", "pr", "view", str(number), "--json",
                          "createdAt,mergedAt,statusCheckRollup,comments", cwd=root))
    checks = data.get("statusCheckRollup") or []
    return {"pr": str(number), "pr_opened": data.get("createdAt"),
            "ci_green": ci_green_at(checks), "review": review_at(data.get("comments") or []),
            "merged": data.get("mergedAt"), "checks": check_minutes(checks)}


def repo_slug(root: Path) -> str:
    url = run("git", "remote", "get-url", "origin", cwd=root)
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    if not match:
        raise Refused(f"origin is not a GitHub repository: {url}")
    return match.group(1)


# --- retrospective ---------------------------------------------------------

def minutes_between(times: dict, start, end: str) -> float | None:
    if isinstance(start, tuple):
        if not all(times.get(name) for name in start):
            return None
        begin = max(parse_time(times[name]) for name in start)
    elif times.get(start):
        begin = parse_time(times[start])
    else:
        return None
    if not times.get(end):
        return None
    return round((parse_time(times[end]) - begin).total_seconds() / 60, 1)


def task_row(task: dict, pr: dict | None) -> dict:
    times = {"requested_at": task.get("requested_at"), "approved_at": task.get("approved_at"),
             **{key: (pr or {}).get(key) for key in ("pr_opened", "ci_green", "review", "merged")}}
    steps = {step: minutes_between(times, start, end) for step, (start, end) in STEPS.items()}
    known = {step: value for step, value in steps.items() if value is not None}
    return {"task_id": task["id"], "title": task.get("title", ""), "size": task.get("size", ""),
            "rigor": task.get("rigor", "normal"), "delivery": task.get("delivery", ""),
            "kind": task.get("task_kind", "build"),
            "pr": (pr or {}).get("pr", ""), **times,
            "total": minutes_between(times, "requested_at", "merged"), "steps": steps,
            "largest_step": max(known, key=known.get) if known else None,
            "checks": (pr or {}).get("checks", {})}


def classify(times: dict, minutes: set[int], rigor: str, until: float) -> dict[str, float]:
    """Minutes of one task by who it waited on: an active session is agent work; otherwise
    the phase decides: before approval the owner, then idle until the PR opens, then CI
    until green, the reviewer until it passed, and at rigor high the owner's merge."""
    def at(key):
        return parse_time(times[key]).timestamp() if times.get(key) else None
    start, approved, opened = at("requested_at"), at("approved_at"), at("pr_opened")
    green, reviewed, merged = at("ci_green"), at("review"), at("merged")
    result = dict.fromkeys(WAITS, 0)
    if start is None:
        return {key: 0.0 for key in WAITS}
    end = merged or until
    for minute in range(int(start // 60), int(end // 60)):
        moment = minute * 60
        if minute in minutes:
            kind = "agent_work"
        elif approved is None or moment < approved:
            kind = "owner_wait"
        elif opened is None or moment < opened:
            kind = "idle"
        elif green is None or moment < green:
            kind = "ci_wait"
        elif reviewed is None or moment < reviewed:
            kind = "review_wait"
        else:
            kind = "owner_wait" if rigor == "high" else "idle"
        result[kind] += 1
    return {key: float(value) for key, value in result.items()}


def task_branch(task: dict) -> str:
    return f"task/{Path(task.get('file', '')).stem}" if task.get("file") else ""


def attach_sessions(rows: list[dict], tasks: list[dict], events: list[dict], deep: bool,
                    until: float) -> None:
    """Add each task's cost, wait split and, with --deep, its reading-waste signals."""
    import session_cost
    for row, task in zip(rows, tasks):
        start = parse_time(row["requested_at"]).timestamp()
        end = parse_time(row["merged"]).timestamp() if row.get("merged") else until
        mine = [event for event in events
                if session_cost.belongs(event, task_branch(task), start, end)]
        row["cost"] = session_cost.cost(mine)
        row["waits"] = classify(row, session_cost.active_minutes(mine), row["rigor"], until)
        if deep:
            row["reading"] = session_cost.reading(mine)


def select(tasks: list[dict], delivery: str | None = None, since: str | None = None,
           recent: int | None = None, at: datetime | None = None) -> list[dict]:
    """The tasks in scope: one Delivery, a time window or the latest tasks."""
    chosen = [task for task in tasks if task.get("requested_at")]
    if delivery:
        chosen = [task for task in chosen if task.get("delivery") == delivery]
    if since:
        match = re.fullmatch(r"([0-9]+)([dh])", since)
        if not match:
            raise Refused("--since takes <n>d or <n>h, such as 7d")
        hours = int(match.group(1)) * (24 if match.group(2) == "d" else 1)
        cutoff = (at or datetime.now(timezone.utc)).timestamp() - hours * 3600
        chosen = [task for task in chosen if parse_time(task["requested_at"]).timestamp() >= cutoff]
    chosen.sort(key=lambda task: parse_time(task["requested_at"]))
    if recent and not (delivery or since):
        chosen = chosen[-recent:]
    return chosen


def summary(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "average": None, "median": None, "max": None}
    return {"count": len(values), "average": round(statistics.mean(values), 1),
            "median": round(statistics.median(values), 1), "max": round(max(values), 1)}


def proposals(rows: list[dict]) -> list[dict]:
    """Up to five levers from the data, each with a stable id and its evidence."""
    found = []
    for step in STEPS:
        values = [(row["task_id"], row["steps"][step]) for row in rows
                  if row["steps"][step] is not None]
        if not values:
            continue
        median = statistics.median(value for _sid, value in values)
        worst = max(values, key=lambda pair: pair[1])
        found.append({"id": f"RETRO-{step.upper().replace('_', '-')}", "step": step, "weight": median,
                      "evidence": f"{step} median {round(median, 1)} min over {len(values)}"
                                  f" tasks, max {worst[1]} min ({worst[0]})",
                      "proposal": LEVERS[step], "cause_owner": STEP_OWNERS[step],
                      "expected_gain": f"up to {round(median / 2, 1)} min per task (half the {step} median)",
                      "verify": f"the next retrospective over at least 5 tasks shows a {step} median"
                                f" at most {round(median / 2, 1)} min"})
    for wait in WAITS[1:]:
        values = [(row["task_id"], row["waits"][wait]) for row in rows
                  if row.get("waits") and row["waits"][wait]]
        if not values:
            continue
        median = statistics.median(value for _sid, value in values)
        worst = max(values, key=lambda pair: pair[1])
        found.append({"id": f"RETRO-WAIT-{wait.upper().replace('_', '-')}", "step": wait,
                      "weight": median,
                      "evidence": f"{wait} median {round(median, 1)} min over {len(values)} tasks,"
                                  f" max {worst[1]} min ({worst[0]})",
                      "proposal": WAIT_LEVERS[wait], "cause_owner": WAIT_OWNERS[wait],
                      "expected_gain": f"up to {round(median, 1)} min per task",
                      "verify": f"the next retrospective shows a {wait} median under"
                                f" {round(median / 2, 1)} min"})
    reads = [(row["task_id"], path, count) for row in rows
             for path, count in (row.get("reading") or {}).get("repeated_reads", {}).items()]
    large = sum((row.get("reading") or {}).get("results_over_200kb", 0) for row in rows)
    if reads or large:
        top = max(reads, key=lambda item: item[2]) if reads else None
        found.append({"id": "RETRO-READING", "step": "reading", "weight": float(len(reads) + large),
                      "evidence": f"{len(reads)} files read 5 or more times"
                                  + (f", most {top[2]} times ({Path(top[1]).name}, {top[0]})" if top else "")
                                  + f"; {large} tool results over 200 KB",
                      "proposal": "Point roles to the section they need instead of whole files, and"
                                  " cut tool outputs over 200 KB to a summary.",
                      "cause_owner": "plugin", "expected_gain": "fewer tokens and shorter agent turns",
                      "verify": "the next --deep retrospective shows no file read 5 or more times"
                                " and no result over 200 KB"})
    checks: dict[str, list[float]] = {}
    for row in rows:
        for name, minutes in row["checks"].items():
            checks.setdefault(name, []).append(minutes)
    if checks:
        name, values = max(checks.items(), key=lambda item: statistics.median(item[1]))
        median = statistics.median(values)
        slug = re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")[:40] or "CHECK"
        found.append({"id": f"RETRO-CHECK-{slug}", "step": "ci", "weight": median,
                      "evidence": f"required check '{name}' median {round(median, 1)} min,"
                                  f" max {max(values)} min over {len(values)} runs",
                      "proposal": f"Shorten check '{name}': run only what the diff touches,"
                                  " cache its setup, or move it off the required set.",
                      "cause_owner": "project",
                      "expected_gain": f"up to {round(median / 2, 1)} min per task",
                      "verify": f"check '{name}' median at most {round(median / 2, 1)} min in the next"
                                " retrospective"})
    open_rows = [row["task_id"] for row in rows if row["total"] is None]
    if open_rows:
        found.append({"id": "RETRO-OPEN-TASKS", "step": "open", "weight": -1.0,
                      "evidence": f"{len(open_rows)} tasks have no merge time:"
                                  f" {', '.join(open_rows[:10])}",
                      "proposal": "Finish or drop each open task; a stale pull request hides"
                                  " the real request-to-merge time.",
                      "cause_owner": "process", "expected_gain": "a true request-to-merge time",
                      "verify": "the next retrospective lists no open task older than a day"})
    found.sort(key=lambda item: -item["weight"])
    for item in found:
        item.pop("weight")
    return found[:5]


def kind_proposals(rows: list[dict]) -> list[dict]:
    """Levers per task kind, the kind furthest over its average target first, at most three
    per kind; each names its kind and the gap to that target."""
    found = []
    goals = targets(rows, [])
    kinds = {row.get("kind", "build") for row in rows}
    for kind in sorted(kinds, key=lambda name: goals.get(name, {}).get("target_gap", 0), reverse=True):
        goal = goals.get(kind)
        for item in proposals([row for row in rows if row.get("kind", "build") == kind])[:3]:
            versus = (f"; average {goal['average']} vs target {goal['average_target']} min"
                      f" ({'+' if goal['target_gap'] > 0 else ''}{goal['target_gap']})") if goal else ""
            found.append({**item, "id": item["id"].replace("RETRO-", f"RETRO-{kind.upper()}-", 1),
                          "kind": kind, "evidence": f"{kind}: {item['evidence']}{versus}"})
    return found


def targets(rows: list[dict], totals: list[float]) -> dict:
    """Per kind: the average against its average target, and each merged task against the
    target of its own size and the kind's maximum."""
    result = {}
    for kind, goal in TARGETS.items():
        done = [row for row in rows if row.get("kind", "build") == kind and row["total"] is not None]
        if not done:
            continue
        average = round(statistics.mean(row["total"] for row in done), 1)
        over = [row["task_id"] for row in done if row["total"] > goal.get(row["size"] or "s", goal["max"])]
        result[kind] = {"tasks": len(done), "average": average, "average_target": goal["average"],
                        "target_gap": round(average - goal["average"], 1),
                        "total": summary([row["total"] for row in done]),
                        "steps": {step: summary([row["steps"][step] for row in done
                                                 if row["steps"][step] is not None]) for step in STEPS},
                        "average_met": average <= goal["average"], "max": max(row["total"] for row in done),
                        "max_target": goal["max"], "max_met": all(row["total"] <= goal["max"] for row in done),
                        "size_targets": {size: goal[size] for size in SIZES}, "over_size_target": over}
    return result


def report(rows: list[dict], scope: str, previous: dict | None = None) -> dict:
    totals = [row["total"] for row in rows if row["total"] is not None]
    slowest = sorted((row for row in rows if row["total"] is not None),
                     key=lambda row: -row["total"])[:3]
    return {"scope": scope, "tasks": len(rows), "merged": len(totals),
            "target_minutes": TARGET_MINUTES, "request_to_merge_minutes": summary(totals),
            "per_task": rows,
            "slowest": [{"task_id": row["task_id"], "total": row["total"],
                         "largest_step": row["largest_step"],
                         "largest_minutes": row["steps"].get(row["largest_step"])}
                        for row in slowest],
            "targets": targets(rows, totals), "previous": previous,
            "proposals": kind_proposals(rows)}


def render(data: dict) -> str:
    total = data["request_to_merge_minutes"]
    lines = [f"Scope: {data['scope']}",
             f"Tasks: {data['tasks']} ({data['merged']} merged), target {data['target_minutes']} min",
             f"Request to merge: average {total['average']}, median {total['median']},"
             f" max {total['max']} min", "",
             "Per task (min): define / write / ci / review / merge_wait = total"]
    for row in data["per_task"]:
        steps = " / ".join("-" if row["steps"][step] is None else str(row["steps"][step])
                           for step in STEPS)
        lines.append(f"- {row['task_id']} ({row['size'] or '-'}, {row['rigor']}) PR"
                     f" {row['pr'] or '-'}: {steps}"
                     f" = {row['total'] if row['total'] is not None else 'open'}")
    if data.get("targets"):
        lines += ["", "By kind (min; average / median / max, then the average of each step):"]
    for kind, goal in (data.get("targets") or {}).items():
        total = goal["total"]
        lines.append(f"- {kind} ({goal['tasks']} merged): total {total['average']} / {total['median']} /"
                     f" {total['max']}; " + " / ".join(
                         f"{step} {goal['steps'][step]['average'] if goal['steps'][step]['average'] is not None else '-'}"
                         for step in STEPS))
    for kind, goal in (data.get("targets") or {}).items():
        sizes = "/".join(str(goal["size_targets"][size]) for size in SIZES)
        lines.append(f"Target {kind} ({goal['tasks']}): average {goal['average']} vs {goal['average_target']} min"
                     f" ({'met' if goal['average_met'] else 'missed'}), max {goal['max']} vs"
                     f" {goal['max_target']} ({'met' if goal['max_met'] else 'missed'}), s/m/l {sizes};"
                     f" over size target: {', '.join(goal['over_size_target']) or 'none'}")
    if data.get("previous"):
        before = data["previous"]
        lines.append(f"Previous retrospective ({before['at']}): {before['merged']} merged, average"
                     f" {before['average']}, median {before['median']}, max {before['max']} min")
    waits = [row for row in data["per_task"] if row.get("waits")]
    if waits:
        lines += ["", "Waits (min): agent_work / ci_wait / review_wait / owner_wait / idle; cost"]
        for row in waits:
            spent = sum(tokens["output"] for tokens in row["cost"]["tokens_by_role"].values())
            lines.append(f"- {row['task_id']}: " + " / ".join(str(row["waits"][key]) for key in WAITS)
                         + f"; {row['cost']['agents']} agents, models {row['cost']['models']},"
                         f" effort {row['cost']['efforts']}, output tokens {spent}")
            for path, count in (row.get("reading") or {}).get("repeated_reads", {}).items():
                lines.append(f"  - read {count} times: {path}")
            if (row.get("reading") or {}).get("results_over_200kb"):
                lines.append(f"  - tool results over 200 KB: {row['reading']['results_over_200kb']}")
    if data["slowest"]:
        lines += ["", "Slowest:"]
        lines += [f"- {item['task_id']}: {item['total']} min, largest step {item['largest_step']}"
                  f" ({item['largest_minutes']} min)" for item in data["slowest"]]
    if data["proposals"]:
        lines += ["", "Proposals (approve by number or id to file one issue each):"]
        lines += [f"{number}. [{item['id']}] {item['proposal']} Evidence: {item['evidence']}."
                  f" Cause: {item['cause_owner']}. Expected gain: {item['expected_gain']}."
                  f" Verify: {item['verify']}."
                  for number, item in enumerate(data["proposals"], 1)]
    if not data["tasks"]:
        lines.append("No task in scope.")
    return "\n".join(lines)


def export(data: dict, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(data, indent=2) + "\n"
    buffer = io.StringIO()
    fields = ["task_id", "title", "size", "rigor", "delivery", "pr", "requested_at",
              "approved_at", "pr_opened", "ci_green", "review", "merged", "total", *STEPS, *WAITS,
              "agents", "output_tokens"]
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in data["per_task"]:
        spent = sum(tokens["output"] for tokens in (row.get("cost") or {}).get("tokens_by_role", {}).values())
        writer.writerow({**row, **row["steps"], **(row.get("waits") or {}),
                         "agents": (row.get("cost") or {}).get("agents", ""), "output_tokens": spent})
    return buffer.getvalue()


def retrospective_note(data: dict, delivery: str) -> str:
    return (f"---\ntype: retrospective\ntitle: {json.dumps(delivery + ' retrospective')}\n"
            f"status: final\ndelivery: {delivery}\ntags:\n  - doc/retrospective\n"
            f"  - status/final\n---\n# {delivery} retrospective\n\n## Summary\n\n```text\n"
            f"{render(data)}\n```\n\n<!-- sec: nav -->\n## Navigation\n\n"
            "- [[maps/delivery|Delivery]]\n")


def previous_run(root: Path, scope: str) -> dict | None:
    path = root / HISTORY
    if not path.is_file():
        return None
    runs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return next((item for item in reversed(runs) if item.get("scope") == scope), None)


def remember(root: Path, data: dict) -> None:
    """Append this run's summary, the next retrospective's comparison point (derived, git-ignored)."""
    total = data["request_to_merge_minutes"]
    path = root / HISTORY
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": now(), "scope": data["scope"], "merged": data["merged"],
                                 "average": total["average"], "median": total["median"],
                                 "max": total["max"]}) + "\n")


def gather(root: Path, args) -> dict:
    tasks = []
    for path in sorted((root / TASKS).glob("tk-*.md")):
        tasks.append({**task_props(path), "file": path.name})
    chosen = select(tasks, args.delivery, args.since, args.recent)
    prs = pull_requests(root) if chosen else {}
    rows = [task_row(task, pr_times(root, prs[task["id"]]["number"]) if task["id"] in prs else None)
            for task in chosen]
    if chosen and not getattr(args, "no_sessions", False):
        import session_cost
        since = min(parse_time(task["requested_at"]).timestamp() for task in chosen)
        attach_sessions(rows, chosen, session_cost.scan(root, since), getattr(args, "deep", False),
                        datetime.now(timezone.utc).timestamp())
    scope = args.delivery or (f"since {args.since}" if args.since else f"latest {args.recent}")
    return report(rows, scope, previous_run(root, scope))


# --- story links and board -------------------------------------------------

def story_index(root: Path) -> dict[str, dict]:
    """Every backlog story by id: its file, epic, title and acceptance criteria."""
    index = {}
    for path in sorted((root / STORIES).glob("*/stories/*/story.md")):
        text = path.read_text(encoding="utf-8")
        props, start, _error = parse_frontmatter(text)
        epic = parse_frontmatter((path.parents[2] / "epic.md").read_text(encoding="utf-8"))[0] \
            if (path.parents[2] / "epic.md").is_file() else {}
        body = "\n".join(text.splitlines()[start - 1:])
        criteria = [re.sub(r"^- \[[ xX]\]\s*", "", line)
                    for line in section(body, "Acceptance") if line.startswith("- [")]
        index[str(props.get("id", ""))] = {
            "path": path.relative_to(root).as_posix(), "title": props.get("title", ""),
            "epic": str(epic.get("id", "")), "criteria": criteria,
            "test_plan": (path.parent / "test-plan.md").relative_to(root).as_posix()}
    return index


def story_changed_at(root: Path, base: str, path: str) -> str | None:
    """The commit time of the story file's last change on the target branch."""
    value = run("git", "log", "-1", "--format=%cI", f"origin/{base}", "--", path, cwd=root)
    return value or None


def remote_tasks(root: Path, base: str) -> tuple[list[dict], list[dict]]:
    """Merged tasks (on the target branch) and open tasks (only on a task branch)."""
    def read(ref: str, path: str) -> dict:
        return parse_frontmatter(run("git", "show", f"{ref}:{path}", cwd=root))[0]
    on_base = [line for line in run("git", "ls-tree", "--name-only", f"origin/{base}",
                                    f"{TASKS.as_posix()}/", cwd=root).splitlines() if line]
    merged = [read(f"origin/{base}", path) for path in on_base]
    merged_ids = {task.get("id") for task in merged}
    opened = []
    for branch in run("git", "for-each-ref", "--format=%(refname:short)",
                      "refs/remotes/origin/task/", cwd=root).splitlines():
        match = re.search(r"/(tk-[0-9]+)-", branch)
        if not match or match.group(1).upper() in merged_ids:
            continue
        paths = [line for line in run("git", "ls-tree", "--name-only", branch,
                                      f"{TASKS.as_posix()}/", cwd=root).splitlines()
                 if Path(line).name.startswith(match.group(1) + "-")]
        opened += [{**read(branch, path), "branch": branch} for path in paths]
    return merged, opened


def drift(stories: dict, opened: list[dict], changed: dict[str, str | None]) -> list[dict]:
    """Open tasks whose source story changed on the target branch after they were requested."""
    warnings = []
    for task in opened:
        story = task.get("source_story")
        when = changed.get(story)
        if story in stories and when and task.get("requested_at") \
                and parse_time(when) > parse_time(task["requested_at"]):
            warnings.append({"task": task.get("id"), "story": story, "story_changed_at": when,
                             "task_requested_at": task["requested_at"]})
    return warnings


def board(stories: dict, merged: list[dict], opened: list[dict]) -> dict:
    """Tasks grouped by epic and story; a story is delivered once merged tasks cover
    every one of its criteria. Derived on every run, never edited."""
    epics: dict[str, dict] = {}
    for task in [*({**task, "state": "merged"} for task in merged),
                 *({**task, "state": "open"} for task in opened)]:
        story = task.get("source_story") or ""
        epic = task.get("epic") or stories.get(story, {}).get("epic") or "standalone"
        group = epics.setdefault(epic, {}).setdefault(story or "standalone", {"tasks": []})
        group["tasks"].append({"id": task.get("id"), "title": task.get("title"),
                               "state": task["state"], "covers": covered(task) or []})
    for groups in epics.values():
        for story, group in groups.items():
            if story == "standalone":
                continue
            total = len(stories.get(story, {}).get("criteria", []))
            done = sorted({number for task in group["tasks"] if task["state"] == "merged"
                           for number in task["covers"]})
            group.update({"criteria": total, "covered": done,
                          "missing": [n for n in range(1, total + 1) if n not in done],
                          "delivered": bool(total) and len(done) >= total
                          and set(range(1, total + 1)) <= set(done)})
    return epics


def render_board(epics: dict, stories: dict, warnings: list[dict]) -> str:
    lines = ["# Fast-lane board", "", "Derived by task_lane.py board; never edit by hand.", ""]
    for epic in sorted(epics):
        lines.append(f"## {epic}")
        for story in sorted(epics[epic]):
            group = epics[epic][story]
            if story == "standalone":
                lines.append("- Standalone tasks")
            else:
                state = "delivered" if group["delivered"] else \
                    f"{len(group['covered'])}/{group['criteria']} criteria, missing {group['missing']}"
                lines.append(f"- {story} {stories.get(story, {}).get('title', '')}: {state}")
            lines += [f"  - {task['id']} {task['state']}: {task['title']}"
                      + (f" (covers {task['covers']})" if task["covers"] else "")
                      for task in sorted(group["tasks"], key=lambda task: str(task["id"]))]
        lines.append("")
    if warnings:
        lines.append("## Story changed under open tasks")
        lines += [f"- {item['task']}: {item['story']} changed at {item['story_changed_at']},"
                  f" after the task was requested at {item['task_requested_at']}" for item in warnings]
    return "\n".join(lines).rstrip() + "\n"


def cmd_criteria(args, root: Path) -> dict:
    stories = story_index(root)
    if args.story not in stories:
        raise Refused(f"no backlog story {args.story}")
    run("git", "fetch", "--prune", "origin", cwd=root)
    _merged, opened = remote_tasks(root, args.base)
    story = stories[args.story]
    changed = {args.story: story_changed_at(root, args.base, story["path"])}
    return {"story_id": args.story, **story,
            "criteria": [{"number": n, "text": text} for n, text in enumerate(story["criteria"], 1)],
            "open_tasks": [task.get("id") for task in opened if task.get("source_story") == args.story],
            "drift": drift(stories, opened, changed)}


def cmd_board(args, root: Path):
    run("git", "fetch", "--prune", "origin", cwd=root)
    stories = story_index(root)
    merged, opened = remote_tasks(root, args.base)
    changed = {story: story_changed_at(root, args.base, stories[story]["path"])
               for story in {task.get("source_story") for task in opened} if story in stories}
    warnings = drift(stories, opened, changed)
    text = render_board(board(stories, merged, opened), stories, warnings)
    if args.write:
        (root / BOARD).parent.mkdir(parents=True, exist_ok=True)
        (root / BOARD).write_text(text, encoding="utf-8")
        text += f"\nWrote {BOARD.as_posix()}"
    return text


# --- commands --------------------------------------------------------------

def cmd_start(args, root: Path) -> dict:
    if args.delivery and not DELIVERY_RE.match(args.delivery):
        raise Refused("--delivery must be DLV-<number>")
    requested_at = args.at or now()
    parse_time(requested_at)
    request = Path(args.request_file).read_text(encoding="utf-8") if args.request_file else args.request
    request = request or args.title
    rigor, reasons = proposed_rigor(f"{args.title}\n{request}")
    epic, covers = args.epic or "", []
    if args.epic and not EPIC_RE.match(args.epic):
        raise Refused("--epic must be EP-<number>")
    if args.source_story:
        stories = story_index(root)
        if args.source_story not in stories:
            raise Refused(f"no backlog story {args.source_story}")
        covers = sorted({int(item) for item in (args.covers or "").split(",") if item.strip().isdigit()})
        total = len(stories[args.source_story]["criteria"])
        if not covers or covers[0] < 1 or covers[-1] > total:
            raise Refused(f"--covers must name criteria 1 to {total} of {args.source_story}")
        story_epic = stories[args.source_story]["epic"]
        if epic and story_epic and epic != story_epic:
            raise Refused(f"{args.source_story} belongs to {story_epic}, not {epic}")
        epic = epic or story_epic
    elif args.covers:
        raise Refused("--covers needs --source-story")
    run("git", "fetch", "--prune", "origin", cwd=root)
    task = next_id(taken_names(root, args.base))
    slug = slugify(args.title)
    branch = f"task/{task.lower()}-{slug}"
    run("git", "switch", "-c", branch, f"origin/{args.base}", cwd=root)
    path = root / TASKS / f"{task.lower()}-{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(task_text(task, args.title, request, requested_at, args.delivery or "",
                              rigor, epic, args.source_story or "", covers,
                              "knowledge" if args.knowledge else "build", args.size), encoding="utf-8")
    run("git", "push", "-u", "origin", "HEAD", cwd=root)
    return {"task_id": task, "branch": branch, "task": str(path.relative_to(root)),
            "proposed_rigor": rigor, "rigor_reasons": reasons}


def cmd_lint(args, root: Path) -> dict:
    path = Path(args.file) if args.file else task_path(root, args.task)
    errors = lint(path)
    return {"ok": not errors, "task": str(path), "errors": errors}


def cmd_approve(args, root: Path) -> dict:
    path = task_path(root, args.task)
    errors = lint(path)
    if errors:
        raise Refused("task lint failed: " + "; ".join(errors))
    if not task_props(path).get("approved_at"):
        set_field(path, "approved_at", args.at or now())
    props = task_props(path)
    return {"ok": True, "task_id": args.task, "approved_at": props["approved_at"],
            "rigor": props.get("rigor", "normal")}


def cmd_overlap(args, root: Path) -> dict:
    return {"ok": True, "advisory": True, "overlaps": overlap(root, args.base)}


def cmd_open_pr(args, root: Path) -> dict:
    path = task_path(root, args.task)
    errors = lint(path)
    if not task_props(path).get("approved_at"):
        errors.append("approved_at is missing; run approve first")
    if errors:
        raise Refused("task is not ready: " + "; ".join(errors))
    if task_props(path).get("status") == "active":
        set_field(path, "status", "delivered")
    run("git", "add", str(path), cwd=root)
    if run("git", "diff", "--cached", "--name-only", cwd=root):
        run("git", "commit", "-m", f"docs: deliver task {args.task}", cwd=root)
    warnings = overlap(root, args.base)
    run("git", "push", "-u", "origin", "HEAD", cwd=root)
    body = path.read_text(encoding="utf-8").split("\n---\n", 1)[-1]
    if warnings:
        body += "\n\nAdvisory overlap with open pull requests: " + "; ".join(
            f"#{item['pr']} ({', '.join(item['files'])})" for item in warnings)
    url = run("gh", "pr", "create", "--base", args.base, "--title",
              f"{args.task}: {task_props(path).get('title')}", "--body", body, cwd=root)
    return {"task_id": args.task, "pr": url.rstrip("/").rsplit("/", 1)[-1], "url": url,
            "overlaps": warnings}


def cmd_merge(args, root: Path) -> dict:
    rigor = task_props(task_path(root, args.task)).get("rigor", "normal")
    note = REVIEW_MARK + (f"\n\n{args.detail}" if args.detail else "")
    if rigor == "high":
        note += "\n\nRigor high: the owner merges this pull request."
    run("gh", "pr", "comment", args.pr, "--body", note, cwd=root)
    if rigor == "high":
        return {"task_id": args.task, "pr": args.pr, "auto_merge": False,
                "next": "rigor high: the owner reviews and merges"}
    try:
        run("gh", "pr", "merge", args.pr, "--auto", f"--{args.style}", cwd=root)
    except Refused as refused:
        # GitHub refuses auto-merge once required checks are already green; merge now.
        if "clean status" not in str(refused):
            raise
        run("gh", "pr", "merge", args.pr, f"--{args.style}", cwd=root)
        return {"task_id": args.task, "pr": args.pr, "auto_merge": False, "merged": True}
    return {"task_id": args.task, "pr": args.pr, "auto_merge": True}


def cmd_report(args, root: Path):
    data = gather(root, args)
    remember(root, data)
    written = []
    if args.export:
        target = root / EXPORT / f"tasks.{args.export}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(export(data, args.export), encoding="utf-8")
        written.append(str(target.relative_to(root)))
    if args.write_report:
        if not args.delivery:
            raise Refused("--write-report needs a Delivery scope, such as DLV-007")
        target = root / RETROSPECTIVES / f"{args.delivery.lower()}-retrospective.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(retrospective_note(data, args.delivery), encoding="utf-8")
        written.append(str(target.relative_to(root)))
    if args.json:
        return {**data, "written": written}
    return render(data) + "".join(f"\nWrote {path}" for path in written)


def cmd_file_issues(args, root: Path) -> dict:
    wanted = [item.strip() for item in args.ids.split(",") if item.strip()]
    data = gather(root, args)
    by_id = {item["id"]: item for item in data["proposals"]}
    by_id.update({str(number): item for number, item in enumerate(data["proposals"], 1)})
    unknown = [item for item in wanted if item not in by_id]
    if unknown:
        raise Refused(f"unknown proposals: {', '.join(unknown)}; run report again")
    total = data["request_to_merge_minutes"]
    repo = None
    opened, commented, ledgered = [], [], []
    for proposal_id in dict.fromkeys(by_id[item]["id"] for item in wanted):
        item = by_id[proposal_id]
        body = (f"{item['proposal']}\n\nEvidence: {item['evidence']}\n\n"
                f"Cause owner: {item['cause_owner']}. Expected gain: {item['expected_gain']}."
                f" How to verify: {item['verify']}.\n\n"
                f"Fast-lane retrospective ({data['scope']}): {data['merged']} merged tasks,"
                f" request to merge average {total['average']}, median {total['median']}, max"
                f" {total['max']} min (target {data['target_minutes']}).\n\nProposal id: {proposal_id}")
        if item["cause_owner"] == "plugin":
            # The plugin's own losses wait in the ledger until the owner sends them upstream.
            ledgered.append(pending_upstream(root, proposal_id, item))
            continue
        repo = repo or repo_slug(root)
        found = json.loads(run("gh", "issue", "list", "--repo", repo, "--state", "all", "--search",
                               f'"Proposal id: {proposal_id}" in:body', "--json", "number,state,url",
                               cwd=root) or "[]")
        if found:
            run("gh", "issue", "comment", str(found[0]["number"]), "--repo", repo, "--body",
                "New evidence.\n\n" + body, cwd=root)
            commented.append({"id": proposal_id, "url": found[0]["url"], "state": found[0]["state"]})
            continue
        url = run("gh", "issue", "create", "--repo", repo, "--title",
                  f"Fast lane: {item['step']} - {proposal_id}", "--body", body, cwd=root)
        opened.append({"id": proposal_id, "url": url})
    return {"ok": True, "repo": repo, "opened": opened, "commented": commented,
            "plugin_ledger": ledgered}


def pending_upstream(root: Path, proposal_id: str, item: dict) -> dict:
    """Append one plugin-caused proposal to the plugin ledger's pending upstream table, once."""
    path = root / LEDGER
    text = path.read_text(encoding="utf-8") if path.is_file() else "# Plugin ledger\n"
    if PENDING_UPSTREAM not in text:
        text = text.rstrip("\n") + (f"\n\n{PENDING_UPSTREAM}\n\n| Date | Proposal | Evidence | Expected gain"
                                    " | Verify |\n|---|---|---|---|---|\n")
    row = (f"| {now()[:10]} | {proposal_id}: {item['proposal']} | {item['evidence']} |"
           f" {item['expected_gain']} | {item['verify']} |")
    if f"| {proposal_id}: " in text.split(PENDING_UPSTREAM, 1)[1]:
        return {"id": proposal_id, "path": str(LEDGER), "added": False}
    head, tail = text.split(PENDING_UPSTREAM, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(head + PENDING_UPSTREAM + tail.rstrip("\n") + "\n" + row + "\n", encoding="utf-8")
    return {"id": proposal_id, "path": str(LEDGER), "added": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", help="project checkout; default the current one")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start")
    start.add_argument("--title", required=True)
    start.add_argument("--request")
    start.add_argument("--request-file")
    start.add_argument("--base", default="main")
    start.add_argument("--at")
    start.add_argument("--delivery", help="group the task under a Delivery label, such as DLV-007")
    start.add_argument("--epic", help="the epic the task belongs to, such as EP-003")
    start.add_argument("--source-story", help="the backlog story the task delivers part of, such as ST-012")
    start.add_argument("--covers", help="comma-separated criterion numbers of the source story")
    start.add_argument("--knowledge", action="store_true",
                       help="a knowledge task (documents only): the request is the approval, no criteria or reviewer")
    start.add_argument("--size", choices=SIZES, default="s")
    criteria = sub.add_parser("criteria")
    criteria.add_argument("--story", required=True)
    criteria.add_argument("--base", default="main")
    shown = sub.add_parser("board")
    shown.add_argument("--base", default="main")
    shown.add_argument("--write", action="store_true", help=f"also write {BOARD.as_posix()}")
    check = sub.add_parser("lint")
    check.add_argument("--file", help="a task file path")
    check.add_argument("--task")
    approve = sub.add_parser("approve")
    approve.add_argument("--task", required=True)
    approve.add_argument("--at")
    sub.add_parser("overlap").add_argument("--base", default="main")
    opened = sub.add_parser("open-pr")
    opened.add_argument("--task", required=True)
    opened.add_argument("--base", default="main")
    merge = sub.add_parser("merge")
    merge.add_argument("--task", required=True)
    merge.add_argument("--pr", required=True)
    merge.add_argument("--style", choices=("merge", "squash", "rebase"), default="merge")
    merge.add_argument("--detail")
    retro = sub.add_parser("report")
    retro.add_argument("--json", action="store_true")
    retro.add_argument("--export", choices=("csv", "json"))
    retro.add_argument("--write-report", action="store_true")
    retro.add_argument("--deep", action="store_true",
                       help="also report files read 5 or more times and tool results over 200 KB")
    issues = sub.add_parser("file-issues")
    issues.add_argument("--ids", required=True, help="comma-separated proposal numbers or ids")
    for scoped in (retro, issues):
        scoped.add_argument("--delivery")
        scoped.add_argument("--since")
        scoped.add_argument("--recent", type=int, default=20)
        scoped.add_argument("--no-sessions", action="store_true",
                            help="skip transcript cost and wait classification")
    args = parser.parse_args(argv)
    handlers = {"start": cmd_start, "lint": cmd_lint, "approve": cmd_approve,
                "overlap": cmd_overlap, "open-pr": cmd_open_pr, "merge": cmd_merge,
                "report": cmd_report, "file-issues": cmd_file_issues,
                "criteria": cmd_criteria, "board": cmd_board}
    try:
        if args.command == "lint" and not (args.file or args.task):
            raise Refused("lint needs --file or --task")
        result = handlers[args.command](args, project_root(args.project))
    except (Refused, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(result if isinstance(result, str) else json.dumps(result, indent=2))
    return 1 if isinstance(result, dict) and result.get("ok") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
