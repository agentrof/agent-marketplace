#!/usr/bin/env python3
"""Fast-lane task path: one task, one branch, one pull request, one clock.

Every implementation request runs through this script:

- ``start`` creates ``task/<id>-<slug>`` from the target branch and writes
  the task ``workspace/docs/backlog/tasks/<id>-<slug>.md`` with its
  ``requested_at`` time and the proposed ``rigor``.
- ``lint`` checks one task: title, size, rigor, 3 to 8 acceptance criteria
  and a test list. ``approve`` lints it and writes ``approved_at``.
- ``start`` also writes ``risks`` (``--risk`` and the risk flags its request hits),
  ``applied_skills`` (the method skills ``task-triggers.json`` makes mandatory)
  and ``sources`` (each doc it relies on with a short sha256); ``approve``
  hashes a source added since. ``review-inputs`` lists the reviewers the diff
  and the task's rigor and risks route to, writes the diff to a file the
  reviewers read themselves, and reports triggered skills the task did not
  apply and changed sources.
- ``overlap`` warns, never refuses, when an open pull request touches the
  same files as this branch.
- ``start`` and ``approve`` also route roles: each rule in
  ``skill-content/deliver/data/role-routing.json`` (and the project's
  ``.agentrof/role-routing.json``) that matches the task's kind, size, named
  files or keywords, at the trigger kinds its size allows, lands in its
  ``roles`` field, and the roles of a ``spawn_modes`` mode in ``spawn``;
  ``role-check`` records a role's verdict in the task's ``## Role Checks`` section.
- ``open-pr`` marks the task delivered, pushes the branch and opens the PR; it
  refuses while a ``require`` role, routed by the task or its diff, has no verdict.
- ``prove`` runs the project's local proof commands on HEAD and records each
  one's exit code and time; at switch ``pr_proof`` ``local_record`` ``open-pr``
  refuses without a green record of a clean HEAD and adds it to the PR body.
- ``merge`` posts the reviewers' pass on the PR and, at rigor ``normal``,
  turns on auto-merge; at rigor ``high`` the owner merges unless ``--delegated``.
- ``criteria`` lists a backlog story's numbered criteria, its open linked tasks
  and a warning when the story changed after an open task was requested;
  ``board`` groups tasks by epic and story and marks a story delivered once
  merged tasks cover every criterion, derived on every run.
- ``report`` is the retrospective: the tasks hold the request and approval
  times, GitHub holds the PR, review, CI and merge times, read at report
  time, and ``session_cost.py`` reads each task's cost and active minutes from
  the host session transcripts, so every wait gets an owner; ``--deep`` adds reading waste.
  Cost is tokens and dollars per task, its spawns and their startup context,
  priced by ``session_cost.load_prices``. Quality is read beside them: the
  review counts ``merge`` writes into its pass comment, CI first pass from the
  branch's workflow runs, escaped defects from later tasks' ``fixes`` and story
  coverage from the merged tasks' ``covers``. Rework asks why a task did not finish
  in one pass: red runs (with the first failing test and error line of a bounded number of
  failed logs), blocking findings, role ``changes`` verdicts, unexplained commits after the PR
  opened and causes recorded with ``rework``, classified by ``rework-causes.json``; causes that
  recur become lessons (``--write-lessons``), memory candidates and process levers. Each run appends every
  merged task's cost and waits to the history once, and later runs read that
  row first, so a task keeps its cost after the host deletes old transcripts. ``--export`` rebuilds a derived file under ``.agentrof/``;
  ``--write-report`` keeps a Delivery's summary under
  ``workspace/docs/backlog/retrospectives/``. ``file-issues`` opens one
  issue per proposal the user approved, in the project's own repository.

Nothing is written after a merge, and no file is shared between tasks: each
task travels in its own pull request.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import io
import json
import re
import shutil
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import process_policy
from vault_markdown import parse_frontmatter

TASKS = Path("workspace") / "docs" / "backlog" / "tasks"
RETROSPECTIVES = Path("workspace") / "docs" / "backlog" / "retrospectives"
EXPORT = Path(".agentrof") / "retrospective"
TASK_TYPE = "task"
TASK_STATUSES = ("active", "delivered", "dropped")
SIZES = ("s", "m", "l")
RIGORS = ("normal", "high")
# A request naming one of these proposes rigor high; the owner confirms on the task.
HIGH_RIGOR_RE = re.compile(
    r"\b(auth(?!orit)\w*|login|password\w*|credential\w*|secret\w*|token\w*|api[ -]?key\w*|"
    r"permission\w*|delet\w*|drop\w*|purg\w*|wip\w*|truncat\w*|irreversib\w*|migrat\w*)\b",
    re.IGNORECASE)
ID_RE = re.compile(r"^TK-[0-9]{3,}$")
EPIC_RE = re.compile(r"^EP-[0-9]{3,}$")
STORY_RE = re.compile(r"^ST-[0-9]{3,}$")
STORIES = Path("workspace") / "docs" / "backlog" / "epics"
BOARD = EXPORT / "board.md"
DELIVERY_RE = re.compile(r"^DLV-[0-9]{3,}$")
AC_RANGE = (3, 8)
# Every build task proves its change is wired into the running system and names its docs change.
LIVE_WIRING = "Live wiring:"
DOCS_DELTA = "Docs delta:"
TESTS_MAP_RE = re.compile(r"^-\s+([0-9]+(?:\s*,\s*[0-9]+)*)\s*:\s*\S")
# knowledge: analysis, design, architecture, backlog and contract documents; build: code, devops, tests.
KINDS = ("build", "knowledge")
TARGETS_FILE = Path(__file__).resolve().parent.parent / "skill-content" / "deliver" / "data" / "targets.json"
# Request to merge targets in minutes per kind: s, m, l, max and the average.
TARGETS = json.loads(TARGETS_FILE.read_text(encoding="utf-8"))["kinds"]
# Cost budget in USD per kind and size; the retrospective flags a merged task above its own.
COST_BUDGETS = json.loads(TARGETS_FILE.read_text(encoding="utf-8")).get("cost_budget_usd", {})
TARGET_MINUTES = TARGETS["build"]["average"]
ROUTING_FILE = TARGETS_FILE.with_name("role-routing.json")
PROJECT_ROUTING = Path(".agentrof") / "role-routing.json"
TRIGGERS_FILE = TARGETS_FILE.with_name("task-triggers.json")
PROJECT_TRIGGERS = Path(".agentrof") / "task-triggers.json"
TRIGGER_KEYS = {"method_skills": "skill", "risk_flags": "flag", "reviewers": "role"}
# Renamed skills: a project trigger rule or an open task may still name the old one.
SKILL_ALIASES = {"docker-compose": "container-environment"}
SKILLS = Path(__file__).resolve().parent.parent / "skill-content"
SOURCE_HASH = 12
GRAPH_REFRESH = Path(__file__).resolve().with_name("graphify_refresh.py")
# The project's local proof commands; {base} is the target branch. Without the file
# the proof runs the test command the flow names.
PROJECT_PROOF = Path(".agentrof") / "local-proof.json"
PROOF_COMMANDS = ("make test",)
PROOFS = Path(".agentrof") / "agent-marketplace" / ".runtime" / "proof"
REVIEWS = PROOFS.with_name("review")
ROLE_MODES = ("require", "suggest", "off")
ROLE_STEPS = ("pre_check", "author")
VERDICTS = ("go", "changes")
ROLE_RE = re.compile(r"^[a-z][a-z0-9-]*:(require|suggest):(pre_check|author)$")
CHECK_RE = re.compile(r"^- (\S+) ([a-z][a-z0-9-]*) (go|changes)(?:: (.*))?$")
# A token with a slash or a file extension is a file the text names.
PATH_RE = re.compile(r"[\w.*-]*/[\w./*-]*|[\w-]+\.[A-Za-z][\w]*")
HISTORY = EXPORT / "history.jsonl"
COST_TOKENS = ("input", "cache_write", "cache_read", "output")
COST_LEVERS = {
    "startup": "Cut the context each spawn starts with: disable plugins and memory the role does not"
               " need, and pass the role the diff and the task instead of whole documents.",
    "spawns": "Spawn fewer agents per task: continue the same writer for its fix pass instead of a"
              " fresh spawn, and use the mechanical writer variant for named fixes.",
    "effort": "Run routine review and mechanical fixes at high or medium effort instead of xhigh or max.",
    "main": "Keep the main session to orchestration: clear or compact it between tasks and leave"
            " large reads to spawned roles.",
}
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
FINDINGS_RE = re.compile(r"findings: critical ([0-9]+), non-critical ([0-9]+), filed ([0-9]+)")
RED_RUNS = {"failure", "timed_out", "startup_failure"}
# Quality read from GitHub, kept in the history once per merged task.
QUALITY_KEPT = ("critical", "non_critical", "filed", "ci_first_pass", "red_runs")
# Thresholds are a judgement call, not a measured baseline: a CI first-pass rate under 80%,
# any escaped defect, or critical findings on more than 20% of reviewed tasks earns a lever.
FIRST_PASS_FLOOR = 0.8
# A lever is proposed only from a kind with this many merged tasks; fewer is noise, not evidence.
MIN_PROPOSAL_TASKS = 5
MAX_PROPOSALS = 5
# Branch commits that are routine, never rework: merging the target in and the deliver-docs commit.
MAIN_MERGE_RE = re.compile(r"^Merge (?:remote-tracking )?branch '(?:origin/)?(?:main|master)'")
DELIVER_DOCS = "docs: deliver task "
MISSING_REASONS = {"requested_at": "no request time", "approved_at": "no approval time",
                   "pr_opened": "no pull request", "ci_green": "CI not green", "review": "no review pass",
                   "merged": "not merged"}
CRITICAL_CEILING = 0.2
QUALITY_LEVERS = {
    "ci": "Run the CI's test command for the touched suites before open-pr,"
          " and reproduce a red check locally before pushing again.",
    "escaped": "Turn each escaped defect into a criterion and a named test on the fixing task, and"
               " have the reviewer check every criterion against a test in the diff.",
    "critical": "Name each criterion's test before writing, and have the writer check the diff"
                " against every criterion before open-pr.",
}
REWORK_FILE = Path(__file__).resolve().parent.parent / "skill-content" / "retrospective" / "data" / "rework-causes.json"
PROJECT_REWORK = Path(".agentrof") / "rework-causes.json"
REWORK_OWNERS = ("project", "plugin", "process")
UNCLASSIFIED = "unclassified"
REWORK_RE = re.compile(r"^- (\S+) ([a-z][a-z0-9-]*)(?: ([a-z]+:\S+))?: (.*)$")
# A log line naming a credential, or a long mixed-case token with digits, is dropped, never stored.
SECRET_RE = re.compile(r"(?i)gh[pousr]_\w{16,}|github_pat_\w+|\bsk-[\w-]{16,}|xox[abpr]-[\w-]+|AKIA[0-9A-Z]{16}"
                       r"|-----BEGIN|\bbearer\s+\S+|\b(token|secret|password|passwd|api[_-]?key|authorization"
                       r"|credential)s?\b\s*[:=]")
ENTROPY_RE = re.compile(r"\b(?=[\w+/=-]*[0-9])(?=[\w+/=-]*[A-Z])(?=[\w+/=-]*[a-z])[\w+/=-]{32,}")
# pytest, unittest, go and jest or vitest failing test ids.
TEST_ID_RES = (re.compile(r"\bFAILED ([\w./-]+::[\w:.\[\]/-]+)"), re.compile(r"^(?:FAIL|ERROR): (\w+) \(([\w.]+)\)"),
               re.compile(r"--- FAIL: (\w+)"), re.compile(r"\bFAIL\s+(\S+\.(?:test|spec)\.[cm]?[jt]sx?)\b"))
ERROR_LINE_RE = re.compile(r"(?i)\b(error|failed|failure|exception|traceback|assert\w*)\b")
EXIT_LINE_RE = re.compile(r"Process completed with exit code")
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
          " scope checks to the changed trees) and cache dependencies in CI.",
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


def role_rules(root: Path | None = None) -> list[dict]:
    """Packaged rules, a project's .agentrof/role-routing.json rule replacing the one of its role."""
    rules = {rule["role"]: rule for rule in json.loads(ROUTING_FILE.read_text(encoding="utf-8"))["rules"]}
    own = root / PROJECT_ROUTING if root else None
    if own and own.is_file():
        rules.update({rule["role"]: rule for rule in json.loads(own.read_text(encoding="utf-8"))["rules"]})
    for rule in rules.values():
        if rule.get("mode") not in ROLE_MODES or rule.get("step") not in ROLE_STEPS:
            raise Refused(f"role rule {rule['role']}: mode must be one of {', '.join(ROLE_MODES)}"
                          f" and step one of {', '.join(ROLE_STEPS)}")
    return [rule for rule in rules.values() if rule["mode"] != "off"]


def routing_policy(root: Path | None = None) -> dict:
    """pre_check_triggers and spawn_modes, a project's .agentrof/role-routing.json value replacing the packaged one."""
    keys = ("pre_check_triggers", "spawn_modes")
    policy = {key: value for key, value in json.loads(ROUTING_FILE.read_text(encoding="utf-8")).items() if key in keys}
    own = root / PROJECT_ROUTING if root else None
    if own and own.is_file():
        policy |= {key: value for key, value in json.loads(own.read_text(encoding="utf-8")).items() if key in keys}
    return policy


def spawn_roles(roles: list[dict], root: Path | None = None) -> list[str]:
    """The routed roles the flow spawns on its own; any other routed role spawns only when the owner asks."""
    modes = routing_policy(root).get("spawn_modes", ["require"])
    return [item["role"] for item in roles if item["mode"] in modes]


def named_files(text: str) -> set[str]:
    return {token.rstrip(".") for token in PATH_RE.findall(text) if token.rstrip(".")}


def route(rules: list[dict], text: str, kind: str, size: str, files: set[str],
          allowed: list[str] | None = None) -> list[dict]:
    """The rules a task hits, each with the triggers that matched as its reasons; ``allowed``
    names the trigger kinds (size, path, keyword) that may route at this size, all when None."""
    allowed = ["size", "path", "keyword"] if allowed is None else allowed
    found = []
    for rule in rules:
        if kind not in rule.get("kinds", KINDS):
            continue
        reasons = [f"size {size}"] if "size" in allowed and size in rule.get("sizes", []) else []
        reasons += [reason for reason in trigger_reasons(rule, text, files)
                    if ("path" if reason.startswith("file ") else "keyword") in allowed]
        if reasons:
            found.append({key: rule.get(key)
                          for key in ("role", "mode", "step", "model_class", "time_budget_minutes", "ask")}
                         | {"reasons": reasons})
    return found


def trigger_reasons(rule: dict, text: str, files: set[str]) -> list[str]:
    """The files and keywords of one rule that a task's text and files hit."""
    files = {name for name in files
             if not any(fnmatch.fnmatch(name, glob) for glob in rule.get("exclude_globs", []))}
    reasons = [f"file {name} matches {glob}" for glob in rule.get("path_globs", [])
               for name in sorted(files) if fnmatch.fnmatch(name, glob)]
    return reasons + [f"keyword {word}" for word in rule.get("keywords", [])
                      if re.search(rf"\b{re.escape(word)}(?:s|es)?\b", text, re.IGNORECASE)]


def task_triggers(root: Path | None = None) -> dict:
    """Packaged trigger tables, a project's .agentrof/task-triggers.json rule replacing the one of its id."""
    tables = json.loads(TRIGGERS_FILE.read_text(encoding="utf-8"))
    own = root / PROJECT_TRIGGERS if root else None
    extra = json.loads(own.read_text(encoding="utf-8")) if own and own.is_file() else {}
    result = {}
    for table, key in TRIGGER_KEYS.items():
        rules = {rule[key]: rule for rule in tables[table]}
        own_rules = [dict(rule, skill=SKILL_ALIASES.get(rule["skill"], rule["skill"])) if key == "skill" else rule
                     for rule in extra.get(table, [])]
        rules.update({rule[key]: rule for rule in own_rules})
        result[table] = list(rules.values())
    unknown = {rule["skill"] for rule in result["method_skills"]} - {
        path.parent.name for path in SKILLS.glob("*/SKILL.md")}
    if unknown:
        raise Refused(f"task triggers name skills this package does not ship: {', '.join(sorted(unknown))}")
    return result


def triggered(rules: list[dict], key: str, text: str, files: set[str]) -> list[str]:
    return sorted({rule[key] for rule in rules if trigger_reasons(rule, text, files)})


def field_items(props: dict, key: str) -> list[str]:
    """A comma-separated or list front matter field as its items."""
    value = props.get(key) or []
    items = value if isinstance(value, list) else str(value).split(",")
    return [str(item).strip() for item in items if str(item).strip()]


def applied_skills(props: dict) -> list[str]:
    return [SKILL_ALIASES.get(skill, skill) for skill in field_items(props, "applied_skills")]


def source_hash(root: Path, relative: str) -> str:
    path = root / relative
    return hashlib.sha256(path.read_bytes()).hexdigest()[:SOURCE_HASH] if path.is_file() else "missing"


def hashed_sources(root: Path, items: list[str]) -> list[str]:
    """Each source as path@hash; an item that already carries its hash keeps it."""
    return [item if "@" in item else f"{item}@{source_hash(root, item)}" for item in items]


def changed_sources(root: Path, items: list[str]) -> list[dict]:
    """The sources whose content changed since the task recorded their hash."""
    found = []
    for item in items:
        relative, _, recorded = item.rpartition("@")
        if relative and source_hash(root, relative) != recorded:
            found.append({"path": relative, "defined": recorded, "now": source_hash(root, relative)})
    return found


def task_body(path: Path) -> str:
    """The task's own words: the body before the navigation and the recorded verdicts."""
    return re.split(r"(?m)^<!-- sec: nav -->|^## Role Checks|^## Rework",
                    path.read_text(encoding="utf-8").split("\n---\n", 1)[-1])[0]


def task_roles(props: dict) -> dict[str, dict]:
    """The roles field: role -> mode and step, as start, approve or the main session wrote it."""
    items = [item.strip() for item in str(props.get("roles") or "").split(",") if item.strip()]
    return {role: {"role": role, "mode": mode, "step": step}
            for role, mode, step in (item.split(":") for item in items if ROLE_RE.match(item))}


def roles_value(roles: list[dict]) -> str:
    return ", ".join(f"{item['role']}:{item['mode']}:{item['step']}" for item in roles)


def role_checks(path: Path) -> list[dict]:
    body = path.read_text(encoding="utf-8").split("\n---\n", 1)[-1]
    return [{"at": match.group(1), "role": match.group(2), "verdict": match.group(3),
             "note": match.group(4) or ""}
            for line in section(body, "Role Checks") if (match := CHECK_RE.match(line))]


def rework_notes(path: Path) -> list[dict]:
    """The rework causes a person or agent recorded on the task, each optionally naming the event it classifies."""
    body = path.read_text(encoding="utf-8").split("\n---\n", 1)[-1]
    return [{"at": match.group(1), "category": match.group(2), "ref": match.group(3) or "", "note": match.group(4)}
            for line in section(body, "Rework") if (match := REWORK_RE.match(line))]


def routed_roles(root: Path | None, path: Path, files: set[str] = frozenset()) -> tuple[list[dict], list[str]]:
    """Every role the task routes to now (its roles field, then the rules its text, size and the
    given changed files hit), and the require roles that have no recorded verdict yet."""
    props = task_props(path)
    body = task_body(path)
    text = f"{props.get('title', '')}\n{body}"
    roles = task_roles(props)
    size = props.get("size", "s")
    for item in route(role_rules(root), text, props.get("task_kind", "build"), size,
                      named_files(body) | set(files), routing_policy(root).get("pre_check_triggers", {}).get(size)):
        roles.setdefault(item["role"], item)
        if item["mode"] == "require":
            roles[item["role"]] |= {"mode": "require"}
    done = {check["role"] for check in role_checks(path)}
    return list(roles.values()), [item["role"] for item in roles.values()
                                  if item["mode"] == "require" and item["role"] not in done]


def missing_message(task: str, missing: list[str]) -> str:
    return "; ".join(f"{role} has no recorded verdict; run task_lane.py role-check --task {task}"
                     f" --role {role} --verdict go|changes --note \"<reason>\"" for role in missing)


def role_minutes(task: dict) -> dict:
    """Role checks of one task and their minutes: each check from approval or the previous check."""
    checks = sorted(task.get("role_checks") or [], key=lambda check: parse_time(check["at"]))
    start = task.get("approved_at") or task.get("requested_at")
    minutes = 0.0
    for check in checks:
        if start:
            minutes += max(0.0, (parse_time(check["at"]) - parse_time(start)).total_seconds() / 60)
        start = check["at"]
    return {"checks": len(checks), "minutes": round(minutes, 1)}


def task_text(task: str, title: str, request: str, requested_at: str, delivery: str,
              rigor: str, epic: str = "", source_story: str = "", covers: list[int] | None = None,
              kind: str = "build", size: str = "s", fixes: str = "", risks: list[str] = (),
              skills: list[str] = (), sources: list[str] = ()) -> str:
    grouped = f"delivery: {delivery}\n" if delivery else ""
    grouped += f"fixes: {fixes}\n" if fixes else ""
    facts = "".join(f"{key}:{' ' if items else ''}{', '.join(items)}\n"
                    for key, items in (("risks", risks), ("sources", sources), ("applied_skills", skills)))
    if kind == "knowledge":
        # A knowledge task needs no separate approval: the owner's request is the approval.
        return (f"---\ntype: {TASK_TYPE}\ntitle: {json.dumps(title)}\nstatus: active\nid: {task}\n"
                f"task_kind: knowledge\nrequested_at: {requested_at}\napproved_at: {requested_at}\n"
                f"size: {size}\nrigor: {rigor}\n{facts}{grouped}"
                f"tags:\n  - doc/{TASK_TYPE}\n  - status/active\naliases:\n  - {task}\n---\n"
                f"# {title}\n\n## Request\n\n{request.strip()}\n\n<!-- sec: nav -->\n## Navigation\n")
    grouped += f"epic: {epic}\n" if epic else ""
    if source_story:
        grouped += f"source_story: {source_story}\ncovers:\n" + "".join(
            f"  - {number}\n" for number in covers or [])
    return (f"---\ntype: {TASK_TYPE}\ntitle: {json.dumps(title)}\nstatus: active\nid: {task}\n"
            f"requested_at: {requested_at}\nsize: {size}\nrigor: {rigor}\n{facts}{grouped}"
            f"tags:\n  - doc/{TASK_TYPE}\n  - status/active\naliases:\n  - {task}\n---\n"
            f"# {title}\n\n## Request\n\n{request.strip()}\n\n### Do\n\n- \n\n## Acceptance Criteria\n\n"
            f"1. \n2. {LIVE_WIRING} \n3. {DOCS_DELTA} \n\n## Tests\n\n- 1: \n- 2: \n- 3: \n\n"
            "<!-- sec: nav -->\n## Navigation\n")


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
    if props.get("fixes") and not ID_RE.match(str(props["fixes"])):
        errors.append("fixes must be TK-<number>")
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
    bad = [item.strip() for item in str(props.get("roles") or "").split(",")
           if item.strip() and not ROLE_RE.match(item.strip())]
    if bad:
        errors.append("roles items must be <role>:require|suggest:pre_check|author, found " + ", ".join(bad))
    if any(not CHECK_RE.match(line) for line in section(body, "Role Checks")):
        errors.append("Role Checks lines must be '- <time> <role> go|changes: <note>'")
    if any(not REWORK_RE.match(line) for line in section(body, "Rework")):
        errors.append("Rework lines must be '- <time> <category> [<ref>]: <note>'")
    triggers = task_triggers()
    flags = {rule["flag"] for rule in triggers["risk_flags"]}
    bad = sorted(set(field_items(props, "risks")) - flags)
    if bad:
        errors.append(f"risks must be among {', '.join(sorted(flags))}, found {', '.join(bad)}")
    bad = sorted(skill for skill in applied_skills(props)
                 if not (SKILLS / skill / "SKILL.md").is_file())
    if bad:
        errors.append("applied_skills names skills this package does not ship: " + ", ".join(bad))
    if props.get("task_kind") == "knowledge":
        return errors
    criteria = [line for line in section(body, "Acceptance Criteria")
                if re.match(r"^[0-9]+\.\s+\S", line)]
    if not AC_RANGE[0] <= len(criteria) <= AC_RANGE[1]:
        errors.append(f"Acceptance Criteria needs {AC_RANGE[0]} to {AC_RANGE[1]} numbered"
                      f" criteria, found {len(criteria)}")
    numbers = {int(line.split(".", 1)[0]) for line in criteria}
    for label in (LIVE_WIRING, DOCS_DELTA):
        if not any(re.match(rf"^[0-9]+\.\s+{label}\s*\S", line) for line in criteria):
            errors.append(f"Acceptance Criteria needs a filled '{label}' criterion")
    mapped = {int(number) for line in section(body, "Tests") if (match := TESTS_MAP_RE.match(line))
              for number in match.group(1).split(",")}
    if numbers - mapped:
        errors.append("Tests needs a '- <criterion>: <test>' line for criteria "
                      + ", ".join(str(number) for number in sorted(numbers - mapped)))
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
    path.write_bytes(("---\n" + head + sep + body).encode("utf-8"))


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


PROOF_SECTION_RE = re.compile(r"^## Local proof\n\nEvery check passed on HEAD `[0-9a-f]+` with a clean tree,"
                              r" finished (\S+?)\.\n\n\| Command \| Result \| Seconds \|\n\|---\|---\|---\|\n"
                              r"((?:\| `[^\n]*` \| pass \| [0-9.]+ \|\n?)+)", re.MULTILINE)


def local_proof(body: str) -> dict | None:
    """The local proof ``open-pr`` wrote into the PR body: when it finished and its minutes; None
    when the body holds none."""
    match = PROOF_SECTION_RE.search(body or "")
    if not match:
        return None
    seconds = [float(row.rsplit("|", 2)[1]) for row in match.group(2).strip().splitlines()]
    return {"finished": match.group(1), "minutes": round(sum(seconds) / 60, 1)}


def review_at(comments: list[dict]) -> str | None:
    times = [comment["createdAt"] for comment in comments
             if REVIEW_MARK in (comment.get("body") or "") and comment.get("createdAt")]
    return min(times, key=parse_time) if times else None


def findings(comments: list[dict]) -> dict:
    """The review counts ``merge`` wrote into the latest review pass; None when it wrote none."""
    for comment in reversed(comments):
        body = comment.get("body") or ""
        if REVIEW_MARK in body and (match := FINDINGS_RE.search(body)):
            return dict(zip(("critical", "non_critical", "filed"), map(int, match.groups())))
    return {"critical": None, "non_critical": None, "filed": None}


def ci_runs(root: Path, branch: str) -> list[dict]:
    """The branch's workflow runs, one gh call per pull request; none when the API refuses."""
    if not branch:
        return []
    try:
        out = run("gh", "api", f"repos/{{owner}}/{{repo}}/actions/runs?branch={quote(branch, safe='')}"
                  "&per_page=100", cwd=root)
    except Refused:
        return []
    return json.loads(out or "{}").get("workflow_runs") or []


def first_pass(runs: list[dict], merged: str | None = None) -> dict:
    """CI quality from the finished workflow runs up to the merge: green on the first pushed head
    (no red run and no re-run on it), and the red runs before green, re-run attempts included.
    shortcut: workflow runs stand in for required checks; an external status check is not seen."""
    done = sorted((item for item in runs if item.get("conclusion") and item.get("created_at")
                   and (not merged or parse_time(item["created_at"]) <= parse_time(merged))),
                  key=lambda item: parse_time(item["created_at"]))
    if not done:
        return {"ci_first_pass": None, "red_runs": None}
    attempts = [int(item.get("run_attempt") or 1) for item in done]
    head = done[0].get("head_sha")
    return {"ci_first_pass": all(item["conclusion"] not in RED_RUNS and attempt == 1
                                 for item, attempt in zip(done, attempts) if item.get("head_sha") == head),
            "red_runs": sum(item["conclusion"] in RED_RUNS for item in done) + sum(attempts) - len(done)}


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


def pr_times(root: Path, number, runs: bool = True, causes: dict | None = None) -> dict:
    """Times, check minutes and quality of one pull request; ``runs`` adds the CI first pass and
    the red runs' causes, read from at most ``ci_logs.max_calls_per_task`` failed logs."""
    data = json.loads(run("gh", "pr", "view", str(number), "--json",
                          "createdAt,mergedAt,statusCheckRollup,comments,headRefName,commits,body", cwd=root))
    checks = data.get("statusCheckRollup") or []
    comments = data.get("comments") or []
    # At pr_proof local_record no CI runs: the PR opens on a passed local proof, so it is green
    # when it opens and its CI step is the proof's own minutes.
    proof = None if checks else local_proof(data.get("body") or "")
    result = {"pr": str(number), "pr_opened": data.get("createdAt"),
              "ci_green": data.get("createdAt") if proof else ci_green_at(checks), "review": review_at(comments),
              **({"proof_minutes": proof["minutes"]} if proof else {}),
              "merged": data.get("mergedAt"), "checks": check_minutes(checks), **findings(comments),
              "review_note": next((scrub(FINDINGS_RE.sub("", body.replace(REVIEW_MARK, "")), 240)
                                   for comment in reversed(comments)
                                   if REVIEW_MARK in (body := comment.get("body") or "")), ""),
              "revisions": revisions(data.get("commits") or [], data.get("createdAt"))}
    if runs:
        listed = ci_runs(root, data.get("headRefName") or "")
        result.update(first_pass(listed, data.get("mergedAt")),
                      red=red_events(root, listed, data.get("mergedAt"), (causes or rework_causes())["ci_logs"]))
    return result


def scrub(text: str, limit: int) -> str:
    """One line of at most ``limit`` characters, without any line that looks like a credential."""
    kept = [line for line in str(text or "").splitlines() if not SECRET_RE.search(line) and not ENTROPY_RE.search(line)]
    return " ".join(" ".join(kept).split())[:limit]


def failure(log: str, limit: int) -> dict:
    """The job, first failing test id and first error line of a ``gh run view --log-failed`` output."""
    job, test, error, fallback = "", "", "", ""
    for raw in log.splitlines():
        parts = raw.split("\t", 2)
        job = job or (parts[0] if len(parts) == 3 else "")
        line = re.sub(r"^﻿?[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.]+Z\s?", "", parts[-1]).strip()
        if not line or SECRET_RE.search(line) or ENTROPY_RE.search(line):
            continue
        for pattern in TEST_ID_RES if not test else ():
            if match := pattern.search(line):
                name, owner = (match.groups() + ("",))[:2]
                test = owner if owner.endswith("." + name) else f"{owner}.{name}" if owner else name
                break
        if not error and ERROR_LINE_RE.search(line):
            if EXIT_LINE_RE.search(line):
                fallback = fallback or line
            else:
                error = line
    return {"job": scrub(job, 80), "test": test[:200], "text": scrub(error or fallback, limit)}


def failed_test_ids(output: str, limit: int = 50) -> list[str]:
    """The distinct failing test ids a test run printed, in order, at most ``limit``; the tail
    alone loses them when the summary runs long."""
    found: list[str] = []
    for line in output.splitlines():
        for pattern in TEST_ID_RES:
            if match := pattern.search(line):
                name, owner = (match.groups() + ("",))[:2]
                test = (owner if owner.endswith("." + name) else f"{owner}.{name}" if owner else name)[:200]
                if test not in found:
                    found.append(test)
                break
        if len(found) >= limit:
            break
    return found


def red_events(root: Path, runs: list[dict], merged: str | None, logs: dict) -> list[dict]:
    """One rework event per red workflow run before the merge and per re-run attempt; the first
    ``max_calls_per_task`` red runs read their failed log for the test id and error line."""
    done = sorted((item for item in runs if item.get("created_at")
                   and (not merged or parse_time(item["created_at"]) <= parse_time(merged))),
                  key=lambda item: parse_time(item["created_at"]))
    found, calls = [], 0
    for item in done:
        name = item.get("name") or "workflow"
        for attempt in range(1, int(item.get("run_attempt") or 1)):
            found.append({"ref": f"run:{item.get('id')}:{attempt}", "source": "ci", "at": item["created_at"],
                          "test": "", "text": f"{name}: re-run attempt {attempt + 1}"})
        if item.get("conclusion") not in RED_RUNS:
            continue
        detail = {"job": "", "test": "", "text": ""}
        if calls < logs["max_calls_per_task"] and item.get("id"):
            calls += 1
            try:
                detail = failure(run("gh", "run", "view", str(item["id"]), "--log-failed", cwd=root),
                                 logs["excerpt_chars"])
            except Refused:
                pass
        found.append({"ref": f"run:{item.get('id')}", "source": "ci", "at": item.get("updated_at") or item["created_at"],
                      "test": detail["test"], "text": scrub(name + (f" / {detail['job']}" if detail["job"] else "")
                                                            + (f": {detail['text']}" if detail["text"] else ""),
                                                            logs["excerpt_chars"])})
    return found


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


def revisions(commits: list[dict], opened: str | None) -> list[dict]:
    """Feature commits pushed after the pull request opened. Merges from main and the deliver-docs
    commit are routine; when no feature commit preceded the opening, the first one after it is the
    feature itself, not rework."""
    if not opened:
        return []
    feature = sorted((item for item in commits if item.get("committedDate")
                      and not MAIN_MERGE_RE.match(item.get("messageHeadline") or "")
                      and not (item.get("messageHeadline") or "").startswith(DELIVER_DOCS)),
                     key=lambda item: parse_time(item["committedDate"]))
    after = [item for item in feature if parse_time(item["committedDate"]) > parse_time(opened)]
    if after and len(after) == len(feature):
        after = after[1:]
    return [{"sha": item.get("oid", "")[:7], "at": item["committedDate"], "subject": item.get("messageHeadline") or ""}
            for item in after]


def task_row(task: dict, pr: dict | None) -> dict:
    times = {"requested_at": task.get("requested_at"), "approved_at": task.get("approved_at"),
             **{key: (pr or {}).get(key) for key in ("pr_opened", "ci_green", "review", "merged")}}
    steps = {step: minutes_between(times, start, end) for step, (start, end) in STEPS.items()}
    total = minutes_between(times, "requested_at", "merged")
    # ci and review overlap, so the larger counts; measured before a local proof replaces ci.
    side = [steps[step] for step in ("ci", "review") if steps[step] is not None]
    accounted = sum(steps[step] or 0.0 for step in ("define", "write", "merge_wait")) + max(side, default=0.0)
    missing = {step: ", ".join(MISSING_REASONS[key] for key in
                               ((start if isinstance(start, tuple) else (start,)) + (end,)) if not times.get(key))
               for step, (start, end) in STEPS.items() if steps[step] is None}
    if (pr or {}).get("proof_minutes") is not None:
        steps["ci"] = pr["proof_minutes"]
    known = {step: value for step, value in steps.items() if value is not None}
    return {"task_id": task["id"], "title": task.get("title", ""), "size": task.get("size", ""),
            "rigor": task.get("rigor", "normal"), "delivery": task.get("delivery", ""),
            "kind": task.get("task_kind", "build"), "story": task.get("source_story", ""),
            "fixes": task.get("fixes", ""), "role_checks": role_minutes(task),
            "quality": {key: (pr or {}).get(key) for key in QUALITY_KEPT},
            "pr": (pr or {}).get("pr", ""), **times,
            "total": total, "steps": steps, "missing": missing,
            "unaccounted": None if total is None else round(total - accounted, 1),
            "largest_step": max(known, key=known.get) if known else None,
            "checks": (pr or {}).get("checks", {}),
            "rework_auto": auto_events(task, pr), "rework_notes": task.get("rework_notes") or []}


def auto_events(task: dict, pr: dict | None) -> list[dict] | None:
    """The rework events the task and its PR show, unclassified: role ``changes`` verdicts, red CI
    runs, blocking review findings and the commits pushed after the PR opened that no earlier red
    run, role verdict or blocking finding explains. None while the PR's CI runs were not read."""
    if pr is not None and "red" not in pr:
        return None
    events = [{"ref": f"role:{check['role']}:{check['at']}", "source": "role", "at": check["at"], "test": "",
               "text": f"{check['role']} changes: {check['note']}"}
              for check in task.get("role_checks") or [] if check["verdict"] == "changes"]
    if pr is None:
        return events
    events += pr["red"]
    critical = int(pr.get("critical") or 0)
    events += [{"ref": f"review:critical:{number}", "source": "review", "at": None, "test": "",
                "text": f"blocking review finding: {pr.get('review_note') or '-'}"} for number in range(1, critical + 1)]
    opened = parse_time(pr["pr_opened"]) if pr.get("pr_opened") else None
    causes = sorted(moment for moment in (parse_time(event["at"]) for event in events if event["at"])
                    if opened is None or moment >= opened)
    for commit in sorted(pr.get("revisions") or [], key=lambda item: parse_time(item["at"])):
        if not commit["subject"].startswith("Merge "):
            earlier = [moment for moment in causes if moment <= parse_time(commit["at"])]
            if earlier:
                causes.remove(earlier[0])
                continue
            if critical:
                critical -= 1
                continue
        events.append({"ref": f"commit:{commit['sha']}", "source": "revision", "at": commit["at"], "test": "",
                       "text": scrub(commit["subject"], 240)})
    return events


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
                    until: float, prices: dict | None = None) -> dict:
    """Add each task's cost, wait split and, with --deep, its reading-waste signals; return the
    cost of the scanned calls no task claims. A call several task windows hold is split evenly."""
    import session_cost
    claims = []
    for row, task in zip(rows, tasks):
        start = parse_time(row["requested_at"]).timestamp()
        end = parse_time(row["merged"]).timestamp() if row.get("merged") else until
        claims.append([session_cost.belongs(event, task_branch(task), start, end, task.get("id", ""))
                       for event in events])
    holders = [sum(column) for column in zip(*claims)] if claims else [0] * len(events)
    for row, claimed in zip(rows, claims):
        mine = [{**event, "share": 1 / holders[index]} for index, event in enumerate(events) if claimed[index]]
        row["cost"] = session_cost.cost(mine, prices)
        row["waits"] = classify(row, session_cost.active_minutes(mine), row["rigor"], until)
        if deep:
            row["reading"] = session_cost.reading(mine)
    rest = session_cost.cost([event for index, event in enumerate(events) if not holders[index]], prices)
    return {"tokens": rest["tokens"], "usd": rest["usd"]}


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


def share(part: float, whole: float) -> float | None:
    """A share in [0, 1], or None without a whole."""
    return min(1.0, max(0.0, round(part / whole, 2))) if whole else None


def summary(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "average": None, "median": None, "max": None}
    return {"count": len(values), "average": round(statistics.mean(values), 1),
            "median": round(statistics.median(values), 1), "max": round(max(values), 1)}


def proposals(rows: list[dict], keep_weight: bool = False) -> list[dict]:
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
    for item in found if not keep_weight else []:
        item.pop("weight")
    return found[:5]


def cost_summary(rows: list[dict]) -> dict | None:
    """Dollars and tokens over the rows that carry a cost: totals, per task, per spawn and split."""
    costed = [row["cost"] for row in rows if row.get("cost") and "usd" in row["cost"]]
    if not costed:
        return None
    count = len(costed)
    spawns = [spawn for item in costed for spawn in item.get("spawns", [])]
    spent = sum(item["usd"] for item in costed)
    main = sum(item["main"]["usd"] for item in costed)
    def merged(key):
        result: dict[str, float] = {}
        for item in costed:
            for name, value in item.get(key, {}).items():
                result[name] = round(result.get(name, 0.0) + value, 4)
        return result
    return {"tasks": count, "usd": round(spent, 2), "usd_per_task": round(spent / count, 2),
            "tokens_per_task": {key: round(sum(item["tokens"][key] for item in costed) / count)
                                for key in COST_TOKENS},
            "spawns_per_task": round(len(spawns) / count, 1),
            "startup_context_per_spawn": round(statistics.mean(spawn["startup_context"] for spawn in spawns))
            if spawns else None,
            "by_model": merged("by_model"), "by_class": merged("by_class"),
            "main_share": round(main / spent, 2) if spent else None,
            "unpriced_models": sorted({model for item in costed for model in item.get("unpriced_models", [])})}


def cost_proposals(rows: list[dict]) -> list[dict]:
    """Cost levers, each weighted by the share of the task cost it addresses."""
    found, spend = [], cost_summary(rows)
    if not spend or not spend["usd"]:
        return found
    count, per_task = spend["tasks"], spend["usd_per_task"]
    spawns = [spawn for row in rows if row.get("cost") for spawn in row["cost"].get("spawns", [])]
    def lever(key, addressed, evidence, saving, verify):
        share = addressed / per_task if per_task else 0
        if share >= 0.1:
            found.append({"id": f"RETRO-COST-{key.upper()}", "step": f"cost:{key}", "weight": share,
                          "evidence": evidence, "proposal": COST_LEVERS[key], "cause_owner": "plugin"
                          if key != "main" else "process", "expected_gain": saving, "verify": verify})
    startup = spend["startup_context_per_spawn"] or 0
    if startup > 30000:
        first = sum(spawn["startup_usd"] for spawn in spawns) / count
        lever("startup", first, f"startup context {round(startup / 1000)}k tokens per spawn over"
              f" {len(spawns)} spawns, ${round(first, 2)} per task on first calls",
              f"about ${round(first / 2, 2)} per task when the startup context halves",
              "the next retrospective shows a startup context per spawn under"
              f" {round(startup / 2000)}k tokens")
    if spend["spawns_per_task"] > 3:
        extra = (spend["spawns_per_task"] - 3) * (sum(spawn["usd"] for spawn in spawns) / len(spawns))
        lever("spawns", extra, f"{spend['spawns_per_task']} spawns per task",
              f"up to ${round(extra, 2)} per task at 3 spawns", "the next retrospective shows at most 3"
              " spawns per task")
    routine = [spawn for spawn in spawns if spawn["class"] in ("review", "fix")
               and spawn.get("effort") in ("xhigh", "max")]
    if routine:
        heavy = sum(spawn["usd"] for spawn in routine) / count
        lever("effort", heavy, f"{len(routine)} review or fix spawns at xhigh or max, ${round(heavy, 2)} per task",
              f"about ${round(heavy / 2, 2)} per task (xhigh costs about twice high per task)",
              "the next retrospective shows no review or fix spawn at xhigh or max")
    if (spend["main_share"] or 0) > 0.4:
        main = spend["main_share"] * per_task
        lever("main", main, f"the main session is {round(spend['main_share'] * 100)}% of the cost,"
              f" ${round(main, 2)} per task", f"up to ${round(main / 2, 2)} per task",
              "the next retrospective shows a main-session share under 40%")
    return found


def link_quality(rows: list[dict], tasks: list[dict], stories: dict) -> None:
    """Escaped defects and story coverage, derived from the task files on every run: each later
    task naming ``fixes: <id>`` is an escaped defect of that task; a story's coverage is the share
    of its criteria the delivered tasks cover (on the target branch a delivered task is merged)."""
    fixers: dict[str, list[str]] = {}
    for task in tasks:
        if task.get("fixes"):
            fixers.setdefault(str(task["fixes"]), []).append(str(task["id"]))
    groups = {story: group for epic in board(stories, [task for task in tasks
                                                       if task.get("status") == "delivered"], []).values()
              for story, group in epic.items()}
    for row in rows:
        group = groups.get(row["story"]) or {}
        row["quality"].update(fixed_by=sorted(fixers.get(row["task_id"], [])), story_coverage=round(
            (group["criteria"] - len(group["missing"])) / group["criteria"], 2) if group.get("criteria") else None)


def quality_summary(rows: list[dict]) -> dict | None:
    """Review findings, CI first pass, escaped defects and story coverage over the rows."""
    if not rows:
        return None
    reviewed = [row["quality"] for row in rows if row["quality"].get("critical") is not None]
    checked = [row["quality"] for row in rows if row["quality"].get("ci_first_pass") is not None]
    escaped = [row["task_id"] for row in rows if row["quality"].get("fixed_by")]
    coverage = {row["story"]: row["quality"]["story_coverage"] for row in rows
                if row["quality"].get("story_coverage") is not None}
    minor = sum(item["non_critical"] for item in reviewed)
    def mean(items, key, digits=2):
        return round(statistics.mean(item[key] for item in items), digits) if items else None
    return {"tasks": len(rows), "reviewed": len(reviewed), "critical_per_task": mean(reviewed, "critical"),
            "critical_tasks": [row["task_id"] for row in rows if row["quality"].get("critical")],
            "non_critical_per_task": mean(reviewed, "non_critical"),
            "filed_share": share(sum(min(item["filed"], item["non_critical"]) for item in reviewed), minor),
            "ci_checked": len(checked), "first_pass_rate": mean(checked, "ci_first_pass"),
            "red_runs_per_task": mean(checked, "red_runs", 1),
            "escaped": {task: row["quality"]["fixed_by"] for row in rows
                        if (task := row["task_id"]) in escaped},
            "escaped_rate": round(len(escaped) / len(rows), 2), "coverage": coverage,
            "coverage_average": round(statistics.mean(coverage.values()), 2) if coverage else None}


def quality_proposals(rows: list[dict]) -> list[dict]:
    """Quality levers, each weighted by the share of tasks it hits, so it ranks with time and cost."""
    found, spec = [], quality_summary(rows)
    if not spec:
        return found
    rate = spec["first_pass_rate"]
    if rate is not None and rate < FIRST_PASS_FLOOR:
        red = [row["task_id"] for row in rows if row["quality"].get("ci_first_pass") is False]
        found.append({"id": "RETRO-QUALITY-CI-FIRST-PASS", "step": "quality:ci", "weight": 1 - rate,
                      "evidence": f"CI first pass {round(rate * 100)}% over {spec['ci_checked']} tasks,"
                                  f" {spec['red_runs_per_task']} red runs per task; red first: {', '.join(red[:10])}",
                      "proposal": QUALITY_LEVERS["ci"], "cause_owner": "process",
                      "expected_gain": f"up to {spec['red_runs_per_task']} fewer CI rounds per task",
                      "verify": f"the next retrospective shows a CI first-pass rate of at least"
                                f" {round(FIRST_PASS_FLOOR * 100)}%"})
    if spec["escaped"]:
        found.append({"id": "RETRO-QUALITY-ESCAPED", "step": "quality:escaped", "weight": spec["escaped_rate"],
                      "evidence": f"{len(spec['escaped'])} of {spec['tasks']} tasks needed a later fix: "
                                  + ", ".join(f"{task} (fixed by {', '.join(by)})"
                                              for task, by in list(spec["escaped"].items())[:10]),
                      "proposal": QUALITY_LEVERS["escaped"], "cause_owner": "process",
                      "expected_gain": f"up to {len(spec['escaped'])} fix tasks fewer per {spec['tasks']} tasks",
                      "verify": "no task of the next retrospective's scope gets a later fix task"})
    share = len(spec["critical_tasks"]) / spec["reviewed"] if spec["reviewed"] else 0
    if share > CRITICAL_CEILING:
        found.append({"id": "RETRO-QUALITY-CRITICAL", "step": "quality:review", "weight": share,
                      "evidence": f"critical findings on {len(spec['critical_tasks'])} of {spec['reviewed']}"
                                  f" reviewed tasks, {spec['critical_per_task']} per task:"
                                  f" {', '.join(spec['critical_tasks'][:10])}",
                      "proposal": QUALITY_LEVERS["critical"], "cause_owner": "process",
                      "expected_gain": "one fix and re-review round less per affected task",
                      "verify": f"critical findings on at most {round(CRITICAL_CEILING * 100)}% of reviewed"
                                " tasks in the next retrospective"})
    return found


# --- rework: why a task did not finish in one pass ---------------------------

def rework_causes(root: Path | None = None) -> dict:
    """The packaged cause taxonomy and thresholds; a project's .agentrof/rework-causes.json category
    replaces the one of its id or adds one, and its settings override the packaged ones."""
    causes = json.loads(REWORK_FILE.read_text(encoding="utf-8"))
    own = root / PROJECT_REWORK if root else None
    if own and own.is_file():
        extra = json.loads(own.read_text(encoding="utf-8"))
        merged = {item["id"]: item for item in causes["categories"]}
        merged.update({item["id"]: item for item in extra.get("categories", [])})
        causes.update({key: {**causes[key], **value} if isinstance(causes.get(key), dict) else value
                       for key, value in extra.items() if key != "categories"}, categories=list(merged.values()))
    for item in causes["categories"]:
        if item.get("owner") not in REWORK_OWNERS or item.get("id") == UNCLASSIFIED:
            raise Refused(f"rework cause {item.get('id')}: owner must be one of {', '.join(REWORK_OWNERS)}"
                          f" and the id not {UNCLASSIFIED}")
    return causes


def cause_of(text: str, causes: dict) -> str:
    """The first category whose hint matches the text, else unclassified."""
    for item in causes["categories"]:
        if any(re.search(hint, text or "", re.IGNORECASE) for hint in item.get("hints", [])):
            return item["id"]
    return UNCLASSIFIED


def rework_record(item: dict, causes: dict) -> dict:
    """One task's classified rework: its events (a recorded cause naming an event's ref reclassifies
    it, any other recorded cause is an event of its own) and the minutes from its first timed event
    to the merge. Events stay None while the PR's CI runs were not read."""
    auto, notes = item.get("rework_auto"), item.get("rework_notes") or []
    events = None
    if auto is not None or notes:
        events = [dict(event) for event in auto or []]
        by_ref = {event["ref"]: event for event in events}
        for note in notes:
            if note["ref"] in by_ref:
                by_ref[note["ref"]].update(category=note["category"], note=note["note"])
            else:
                events.append({"ref": note["ref"] or f"note:{note['at']}", "source": "note", "at": note["at"],
                               "test": "", "text": note["note"], "category": note["category"]})
        for event in events:
            event.setdefault("category", cause_of(f"{event['text']} {event.get('test') or ''}", causes))
    timed = [parse_time(event["at"]) for event in events or [] if event.get("at")]
    merged = item.get("merged")
    return {"task_id": item["task_id"], "kind": item.get("kind", "build"), "requested_at": item.get("requested_at"),
            "merged": merged, "total": item.get("total"), "events": events,
            "minutes": round(max(0.0, (parse_time(merged) - min(timed)).total_seconds() / 60), 1)
            if merged and timed else (0.0 if events == [] else None)}


def rework_window(records: list[dict], causes: dict) -> list[dict]:
    """The latest ``recurring.window_tasks`` tasks whose rework is known, oldest first."""
    known = sorted((record for record in records if record["events"] is not None and record.get("requested_at")),
                   key=lambda record: parse_time(record["requested_at"]))
    return known[-causes["recurring"]["window_tasks"]:]


def rework_groups(window: list[dict], causes: dict) -> list[dict]:
    """Events grouped by category and, for a failing test, by test id; a group is recurring at
    ``min_events`` events across ``min_tasks`` tasks, and a test failing in two or more tasks is a
    recurring repeat offender; unclassified events wait for a person. Most events first."""
    info = {item["id"]: item for item in causes["categories"]}
    limits = causes["recurring"]
    groups: dict[str, dict] = {}
    for position, record in enumerate(window):
        for event in record["events"]:
            seen = event.get("at") or record["requested_at"]
            for key in [event["category"]] + ([f"test:{event['test']}"] if event.get("test") else []):
                group = groups.setdefault(key, {"key": key, "category": event["category"],
                                                "test": event["test"] if key.startswith("test:") else "",
                                                "events": 0, "tasks": [], "first_seen": seen, "last_seen": seen,
                                                "minutes": {}, "last_index": position})
                group["events"] += 1
                if record["task_id"] not in group["tasks"]:
                    group["tasks"].append(record["task_id"])
                group["first_seen"] = min(group["first_seen"], seen, key=parse_time)
                group["last_seen"] = max(group["last_seen"], seen, key=parse_time)
                group["last_index"] = position
                if event.get("at") and record.get("merged"):
                    spent = max(0.0, (parse_time(record["merged"]) - parse_time(event["at"])).total_seconds() / 60)
                    group["minutes"][record["task_id"]] = round(max(group["minutes"].get(record["task_id"], 0.0), spent), 1)
    for group in groups.values():
        cause = info.get(group["category"], {})
        group.update(owner=cause.get("owner", "process"), description=cause.get("description", "not classified yet"),
                     rule=causes["repeat_offender_rule"].format(test=group["test"]) if group["test"]
                     else cause.get("rule", ""), repeat_offender=bool(group["test"]) and len(group["tasks"]) >= 2,
                     since_last=len(window) - 1 - group.pop("last_index"))
        group["status"] = UNCLASSIFIED if group["category"] == UNCLASSIFIED and not group["test"] else \
            "recurring" if group["repeat_offender"] or (group["events"] >= limits["min_events"]
                                                        and len(group["tasks"]) >= limits["min_tasks"]) else "rare"
    return sorted(groups.values(), key=lambda group: (-group["events"], group["key"]))


def rework_summary(records: list[dict]) -> dict | None:
    """One-pass rate over the merged tasks whose rework is known, and the share of their
    request-to-merge minutes spent after the first rework event."""
    known = [record for record in records if record["events"] is not None and record.get("merged")]
    if not known:
        return None
    clean = [record["task_id"] for record in known if not record["events"]]
    spent = sum(record["minutes"] or 0.0 for record in known)
    total = sum(record["total"] or 0.0 for record in known)
    return {"tasks": len(known), "one_pass": len(clean), "one_pass_rate": round(len(clean) / len(known), 2),
            "events": sum(len(record["events"]) for record in known), "minutes": round(spent, 1),
            "minutes_share": share(spent, total)}


def promotions(groups: list[dict], causes: dict) -> list[dict]:
    """At most ``promote_max`` project-owned recurring lessons, most events first, as one-line rules
    for the main session to weigh; nothing is written to any memory store."""
    picked = [group for group in groups if group["status"] == "recurring" and group["owner"] == "project"]
    return [{"key": group["key"], "rule": f"{group['rule']} (rework seen {group['events']} times in"
                                          f" {len(group['tasks'])} tasks)"} for group in picked[:causes["promote_max"]]]


def rework_proposals(records: list[dict], causes: dict) -> list[dict]:
    """Process- and plugin-owned recurring causes as levers, weighted by the share of tasks they hit."""
    window = rework_window(records, causes)
    found = []
    for group in rework_groups(window, causes):
        if group["status"] != "recurring" or group["owner"] == "project":
            continue
        slug = re.sub(r"[^A-Z0-9]+", "-", group["key"].upper()).strip("-")[:40]
        spent = list(group["minutes"].values())
        gain = round(statistics.mean(spent), 1) if spent else None
        found.append({"id": f"RETRO-REWORK-{slug}", "step": f"rework:{group['key']}",
                      "weight": len(group["tasks"]) / len(window),
                      "evidence": f"{group['key']} ({group['description']}): {group['events']} rework events in"
                                  f" {len(group['tasks'])} of {len(window)} tasks ({', '.join(group['tasks'][:10])}),"
                                  f" first {group['first_seen'][:10]}, last {group['last_seen'][:10]}",
                      "proposal": f"Fix the process behind {group['key']}: {group['rule']}",
                      "cause_owner": group["owner"],
                      "expected_gain": f"up to {gain} rework min per affected task (first red or blocking event"
                                       " to merge)" if gain is not None else "one rework round less per affected task",
                      "verify": f"no {group['key']} rework event in the next {causes['lessons']['fixed_after_tasks']}"
                                " tasks"})
    return found


def rework_report(records: list[dict], extra: list[dict], causes: dict) -> dict:
    """Rework over the scope's tasks and the kept history: the summary of the scope, the groups of
    the window, the unclassified events to classify and the memory candidates."""
    window = rework_window(records + extra, causes)
    groups = rework_groups(window, causes)
    return {"summary": rework_summary([record for record in records if record.get("merged")]),
            "window_tasks": len(window), "thresholds": causes["recurring"], "groups": groups,
            "repeat_offenders": [group["key"] for group in groups if group["repeat_offender"]],
            "unclassified": [{"task_id": record["task_id"], "ref": event["ref"], "text": event["text"]}
                             for record in window for event in record["events"]
                             if event["category"] == UNCLASSIFIED][:20],
            "promote": promotions(groups, causes)}


def write_lessons(root: Path, rework: dict, causes: dict) -> dict:
    """Update the tracked lessons file in place: one entry per recurring group, its Rule line kept
    when a person edited it; an open entry with no event in ``fixed_after_tasks`` tasks turns fixed
    and an earlier fixed one reopens when it recurs; at most ``max_open`` open entries and as many
    fixed ones, the oldest fixed dropped first. The measurement history is never touched."""
    settings = causes["lessons"]
    path = root / settings["path"]
    text = path.read_text(encoding="utf-8") if path.is_file() else (
        "# Lessons learnt\n\nRecurring rework causes from the fast-lane retrospective, written by"
        " `task_lane.py report --write-lessons`.\nEntries update in place; an edited Rule line is kept."
        " Promote marks a memory candidate only.\n")
    head, *blocks = re.split(r"(?m)^(?=### )", text)
    entries = {block.split()[1]: block for block in blocks}
    rule = {key: match.group(1) for key, block in entries.items() if (match := re.search(r"(?m)^- Rule: (.*)$", block))}
    status = {key: "fixed" if "Status: fixed" in block else "open" for key, block in entries.items()}
    groups = {group["key"]: group for group in rework["groups"]}
    promoted = {item["key"] for item in rework["promote"]}
    added, skipped, fixed = [], [], []
    for key, block in entries.items():
        group = groups.get(key)
        quiet = (group["since_last"] if group else rework["window_tasks"]) >= settings["fixed_after_tasks"]
        if status[key] == "open" and quiet and not (group and group["status"] == "recurring"):
            status[key] = "fixed"
            entries[key] = re.sub(r"Status: open\.( Promote: memory\.)?", "Status: fixed.", block)
            fixed.append(key)
    for key, group in groups.items():
        if group["status"] != "recurring":
            continue
        if key not in entries and sum(value == "open" for value in status.values()) >= settings["max_open"]:
            skipped.append(key)
            continue
        if key not in entries:
            added.append(key)
        status[key] = "open"
        tasks = ", ".join(group["tasks"][:8]) + (", ..." if len(group["tasks"]) > 8 else "")
        entries[key] = (f"### {key}\n\n- Cause: {group['description']}"
                        + (f"; test {group['test']} failed in {len(group['tasks'])} tasks" if group["test"] else "")
                        + f"\n- Rule: {rule.get(key) or group['rule']}\n- Owner: {group['owner']}. Status: open."
                        + (" Promote: memory." if key in promoted else "")
                        + f"\n- Evidence: {group['events']} events in {len(group['tasks'])} tasks ({tasks}),"
                          f" first {group['first_seen'][:10]}, last {group['last_seen'][:10]}.\n\n")
    closed = [key for key in entries if status[key] == "fixed"]
    for key in closed[:max(0, len(closed) - settings["max_open"])]:
        del entries[key]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((head.rstrip("\n") + "\n\n" + "".join(block.rstrip("\n") + "\n\n" for block in entries.values())
                    .rstrip("\n") + "\n").encode("utf-8"))
    return {"path": settings["path"], "added": added, "fixed": fixed, "over_cap": skipped,
            "open": sum(value == "open" for key, value in status.items() if key in entries)}


def kind_proposals(rows: list[dict], records: list[dict] | None = None, causes: dict | None = None) -> list[dict]:
    """One proposal per cause, at most ``MAX_PROPOSALS``, from the kinds with at least
    ``MIN_PROPOSAL_TASKS`` merged tasks; time, cost and quality levers rank together by the share of
    the task's minutes, dollars or tasks they address, and recurring process or plugin rework causes
    by the share of the kind's tasks they hit. A cause seen in several kinds is one proposal whose
    evidence names each kind and its gap to target."""
    found: dict[str, dict] = {}
    records = records or []
    goals = targets(rows, [])
    kinds = {row.get("kind", "build") for row in rows}
    for kind in sorted(kinds, key=lambda name: goals.get(name, {}).get("target_gap", 0), reverse=True):
        goal = goals.get(kind)
        mine = [row for row in rows if row.get("kind", "build") == kind]
        done = [row["total"] for row in mine if row["total"] is not None]
        if len(done) < MIN_PROPOSAL_TASKS:
            continue
        minutes = statistics.mean(done)
        levers = [{**item, "weight": item["weight"] / minutes if minutes and item["weight"] > 0
                   else item["weight"]} for item in proposals(mine, keep_weight=True)]
        rework = rework_proposals([record for record in records if record["kind"] == kind],
                                  causes or rework_causes()) if records else []
        levers = sorted(levers + cost_proposals(mine) + quality_proposals(mine) + rework,
                        key=lambda item: -item["weight"])
        for item in levers:
            versus = (f"; average {goal['average']} vs target {goal['average_target']} min"
                      f" ({'+' if goal['target_gap'] > 0 else ''}{goal['target_gap']})") if goal else ""
            evidence = f"{kind}: {item['evidence']}{versus}"
            if item["id"] in found:
                same = found[item["id"]]
                same.update(kinds=same["kinds"] + [kind], evidence=f"{same['evidence']}; {evidence}",
                            weight=max(same["weight"], item["weight"]))
            else:
                found[item["id"]] = {**item, "kinds": [kind], "evidence": evidence}
    ranked = sorted(found.values(), key=lambda item: -item["weight"])[:MAX_PROPOSALS]
    for item in ranked:
        item.pop("weight")
    return ranked


def calibrated(rows: list[dict], history: list[dict], kind: str) -> dict:
    """Per size, the median request-to-merge of this kind's merged tasks in scope and in the kept
    history, once at least ``MIN_PROPOSAL_TASKS`` tasks of that size exist; else None."""
    seen = {item["task_id"]: item for item in history if item.get("total") is not None
            and item.get("kind", "build") == kind and item.get("size")}
    seen.update({row["task_id"]: row for row in rows if row["total"] is not None
                 and row.get("kind", "build") == kind and row.get("size")})
    result = {}
    for size in SIZES:
        values = [item["total"] for item in seen.values() if item["size"] == size]
        result[size] = {"tasks": len(values), "median": round(statistics.median(values), 1)
                        if len(values) >= MIN_PROPOSAL_TASKS else None}
    return result


def targets(rows: list[dict], totals: list[float], history: list[dict] | None = None) -> dict:
    """Per kind: the average against its average target, each merged task against the target
    of its own size and the kind's maximum, and the per-size medians calibrated from history."""
    result = {}
    for kind, goal in TARGETS.items():
        done = [row for row in rows if row.get("kind", "build") == kind and row["total"] is not None]
        if not done:
            continue
        average = round(statistics.mean(row["total"] for row in done), 1)
        over = [row["task_id"] for row in done if row["total"] > goal.get(row["size"] or "s", goal["max"])]
        budget = COST_BUDGETS.get(kind, {})
        over_cost = [row["task_id"] for row in done
                     if (row.get("cost") or {}).get("usd", 0) > budget.get(row["size"] or "s", float("inf"))]
        result[kind] = {"tasks": len(done), "average": average, "average_target": goal["average"],
                        "target_gap": round(average - goal["average"], 1),
                        "total": summary([row["total"] for row in done]),
                        "steps": {step: summary([row["steps"][step] for row in done
                                                 if row["steps"][step] is not None]) for step in STEPS},
                        "average_met": average <= goal["average"], "max": max(row["total"] for row in done),
                        "max_target": goal["max"], "max_met": all(row["total"] <= goal["max"] for row in done),
                        "size_targets": {size: goal[size] for size in SIZES}, "over_size_target": over,
                        "cost_budget_usd": budget, "over_cost_budget": over_cost,
                        "calibrated": calibrated(rows, history or [], kind),
                        "cost": cost_summary(done), "quality": quality_summary(done),
                        "rework": rework_summary([row["rework"] for row in done if row.get("rework")]),
                        "role_checks": role_summary(done)}
    return result


def role_summary(rows: list[dict]) -> dict:
    spent = [row.get("role_checks") or {"checks": 0, "minutes": 0.0} for row in rows]
    return {"tasks": sum(1 for item in spent if item["checks"]), "checks": sum(item["checks"] for item in spent),
            "minutes": round(sum(item["minutes"] for item in spent), 1)}


def report(rows: list[dict], scope: str, previous: dict | None = None,
           unattributed: dict | None = None, extra: list[dict] | None = None,
           causes: dict | None = None) -> dict:
    """``extra`` holds the kept rework of merged tasks outside the scope, so causes group across them."""
    causes = causes or rework_causes()
    for row in rows:
        row["rework"] = rework_record(row, causes)
    scoped = {row["task_id"] for row in rows}
    records = [row["rework"] for row in rows] + [rework_record(item, causes) for item in extra or []
                                                 if item["task_id"] not in scoped]
    totals = [row["total"] for row in rows if row["total"] is not None]
    slowest = sorted((row for row in rows if row["total"] is not None),
                     key=lambda row: -row["total"])[:3]
    return {"scope": scope, "tasks": len(rows), "merged": len(totals),
            "target_minutes": TARGET_MINUTES, "request_to_merge_minutes": summary(totals),
            "per_task": rows,
            "slowest": [{"task_id": row["task_id"], "total": row["total"],
                         "largest_step": row["largest_step"],
                         "largest_minutes": row["steps"].get(row["largest_step"]), "steps": row["steps"],
                         "size": row["size"] or "s",
                         "target": TARGETS.get(row.get("kind", "build"), TARGETS["build"]).get(row["size"] or "s")}
                        for row in slowest],
            "targets": targets(rows, totals, extra), "previous": previous,
            "missing_cost": [row["task_id"] for row in rows if row["total"] is not None and not row.get("cost")],
            "cost": cost_summary([row for row in rows if row["total"] is not None]),
            "quality": quality_summary([row for row in rows if row["total"] is not None]),
            "unattributed": unattributed,
            "rework": rework_report(records[:len(rows)], records[len(rows):], causes),
            "proposals": kind_proposals(rows, records, causes)}


def render(data: dict) -> str:
    total = data["request_to_merge_minutes"]
    lines = [f"Scope: {data['scope']}",
             f"Tasks: {data['tasks']} ({data['merged']} merged), target {data['target_minutes']} min",
             f"Request to merge: average {total['average']}, median {total['median']},"
             f" max {total['max']} min", *headline(data), "",
             "Per task (min): define / write / ci / review / merge_wait / unaccounted = total"
             " (ci and review overlap; the larger counts)"]
    for row in data["per_task"]:
        steps = " / ".join("-" if row["steps"][step] is None else str(row["steps"][step])
                           for step in STEPS)
        missing = "; ".join(f"{step} ({reason})" for step, reason in (row.get("missing") or {}).items())
        lines.append(f"- {row['task_id']} ({row['size'] or '-'}, {row['rigor']}) PR"
                     f" {row['pr'] or '-'}: {steps} / {dash(row.get('unaccounted'))}"
                     f" = {row['total'] if row['total'] is not None else 'open'}"
                     + (f"; missing: {missing}" if missing else ""))
    if data["per_task"]:
        lines += ["", "Quality per task: findings critical / non-critical / filed; CI first pass (red runs);"
                      " fixed by; story coverage"]
        lines += [f"- {row['task_id']}: {task_quality_line(row)}" for row in data["per_task"]]
        lines += ["", "Rework per task: events (by source); categories; minutes from the first event to merge"]
        lines += [f"- {row['task_id']}: {task_rework_line(row.get('rework') or {})}" for row in data["per_task"]]
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
                     f" over size target: {', '.join(goal['over_size_target']) or 'none'}; calibrated s/m/l "
                     + "/".join(dash(goal["calibrated"][size]["median"]) for size in SIZES)
                     + " (median of " + "/".join(str(goal["calibrated"][size]["tasks"]) for size in SIZES)
                     + f" tasks, shown at {MIN_PROPOSAL_TASKS} or more)")
    for kind, goal in (data.get("targets") or {}).items():
        spent = goal.get("role_checks")
        if spent and spent["checks"]:
            lines.append(f"Role checks {kind}: {spent['checks']} on {spent['tasks']} of {goal['tasks']} tasks,"
                         f" {spent['minutes']} min inside the write step")
    for kind, goal in (data.get("targets") or {}).items():
        if goal.get("cost"):
            lines.append(f"Cost {kind}: {cost_line(goal['cost'])}")
        if goal.get("over_cost_budget"):
            lines.append(f"Over cost budget {kind} (s/m/l $" + "/".join(
                str(goal["cost_budget_usd"].get(size, "-")) for size in SIZES)
                + f"): {', '.join(goal['over_cost_budget'])}")
    if data.get("cost"):
        lines.append(f"Cost all merged: {cost_line(data['cost'])}")
    for kind, goal in (data.get("targets") or {}).items():
        if goal.get("quality"):
            lines.append(f"Quality {kind}: {quality_line(goal['quality'])}")
    for kind, goal in (data.get("targets") or {}).items():
        spent = goal.get("rework")
        if spent:
            lines.append(f"Rework {kind}: one pass {spent['one_pass']}/{spent['tasks']} ({percent(spent['one_pass_rate'])}),"
                         f" {spent['events']} events, {spent['minutes']} min after the first event"
                         f" ({percent(spent['minutes_share'])} of request to merge)")
    if data.get("missing_cost"):
        lines.append(f"Cost missing: {', '.join(data['missing_cost'])} (no host session record in the scanned"
                     " window, or sessions not scanned)")
    if data.get("unattributed"):
        lines.append(f"Unattributed in the scanned window: ${data['unattributed']['usd']:.2f},"
                     f" tokens {tokens_line(data['unattributed']['tokens'])}")
    if data.get("previous"):
        before = data["previous"]
        lines.append(f"Previous retrospective ({before['at']}): {before['merged']} merged, average"
                     f" {before['average']}, median {before['median']}, max {before['max']} min"
                     + (f"; ${before['usd_per_task']:.2f} and {before['tokens_per_task']} tokens per task"
                        if before.get("usd_per_task") is not None else "")
                     + (f"; critical {dash(before['quality']['critical_per_task'])} per task, CI first pass"
                        f" {percent(before['quality']['first_pass_rate'])}, escaped"
                        f" {percent(before['quality']['escaped_rate'])}, coverage"
                        f" {percent(before['quality']['coverage_average'])}" if before.get("quality") else ""))
    waits = [row for row in data["per_task"] if row.get("waits")]
    if waits:
        lines += ["", "Waits (min): agent_work / ci_wait / review_wait / owner_wait / idle; cost"]
        for row in waits:
            spent = sum(tokens["output"] for tokens in row["cost"]["tokens_by_role"].values())
            lines.append(f"- {row['task_id']}: " + " / ".join(str(row["waits"][key]) for key in WAITS)
                         + f"; {row['cost']['agents']} agents, models {row['cost']['models']},"
                         f" effort {row['cost']['efforts']}, output tokens {spent}")
            if "usd" in row["cost"]:
                spawns = row["cost"]["spawns"]
                startup = round(statistics.mean(spawn["startup_context"] for spawn in spawns)) if spawns else 0
                lines.append(f"  - cost ${row['cost']['usd']:.2f}; tokens {tokens_line(row['cost']['tokens'])};"
                             f" {len(spawns)} spawns, startup context {startup} per spawn;"
                             f" main session ${row['cost']['main']['usd']:.2f}")
            for path, count in (row.get("reading") or {}).get("repeated_reads", {}).items():
                lines.append(f"  - read {count} times: {path}")
            if (row.get("reading") or {}).get("results_over_200kb"):
                lines.append(f"  - tool results over 200 KB: {row['reading']['results_over_200kb']}")
    if data["slowest"]:
        lines += ["", "Slowest:"]
        lines += [f"- {item['task_id']}: {item['total']} min vs {item['size']} target {item['target']}"
                  f" ({'met' if item['total'] <= item['target'] else 'missed'}); "
                  + " / ".join(f"{step} {dash(item['steps'][step])}" for step in STEPS)
                  + f"; largest step {item['largest_step']} ({item['largest_minutes']} min)" for item in data["slowest"]]
    lines += rework_lines(data.get("rework"))
    if not data["proposals"] and data["merged"]:
        lines += ["", f"Proposals: none; a kind needs {MIN_PROPOSAL_TASKS} merged tasks before a lever is evidence."]
    if data["proposals"]:
        lines += ["", "Proposals (approve by number or id to file one issue each):"]
        lines += [f"{number}. [{item['id']}] {item['proposal']} Evidence: {item['evidence']}."
                  f" Cause: {item['cause_owner']}. Expected gain: {item['expected_gain']}."
                  f" Verify: {item['verify']}."
                  for number, item in enumerate(data["proposals"], 1)]
    if not data["tasks"]:
        lines.append("No task in scope.")
    return "\n".join(lines)


def headline(data: dict) -> list[str]:
    """Verdict against target, baseline and step breakdown for the scope's request-to-merge."""
    total, before = data["request_to_merge_minutes"], data.get("previous")
    if total["average"] is None:
        return []
    goal = data["target_minutes"]
    lines = [f"Verdict: average {total['average']} vs target {goal} min"
             f" ({'met' if total['average'] <= goal else 'missed'}); baseline "
             + (f"{before['average']} min average at the previous retrospective ({before['at'][:10]})"
                if before and before.get("average") is not None else "none, the first retrospective of this scope")]
    merged = [row for row in data["per_task"] if row["total"] is not None]
    averages = {step: [row["steps"][step] for row in merged if row["steps"][step] is not None] for step in STEPS}
    spare = [row["unaccounted"] for row in merged if row.get("unaccounted") is not None]
    lines.append("Steps (average min): " + " / ".join(
        f"{step} {round(statistics.mean(values), 1) if values else '-'}" for step, values in averages.items())
        + f" / unaccounted {round(statistics.mean(spare), 1) if spare else '-'}")
    if before and before.get("cost_method") and data.get("cost_method") and before["cost_method"] != data["cost_method"]:
        lines.append(f"Cost method changed since the previous retrospective ({before['cost_method']} ->"
                     f" {data['cost_method']}): dollars are not comparable with that baseline.")
    return lines


def dash(value) -> str:
    return "-" if value is None else str(value)


def percent(value) -> str:
    return "-" if value is None else f"{round(value * 100)}%"


def task_quality_line(row: dict) -> str:
    item = row["quality"]
    first = {True: "yes", False: "no", None: "-"}[item.get("ci_first_pass")]
    return (f"{dash(item.get('critical'))} / {dash(item.get('non_critical'))} / {dash(item.get('filed'))};"
            f" CI first pass {first} ({dash(item.get('red_runs'))}); fixed by"
            f" {', '.join(item.get('fixed_by') or []) or '-'}; "
            + (f"{row['story']} {percent(item.get('story_coverage'))}" if row.get("story") else "-"))


def task_rework_line(record: dict) -> str:
    events = record.get("events")
    if events is None:
        return "- (CI runs not read)"
    if not events:
        return "none, one pass"
    def counted(key):
        found: dict[str, int] = {}
        for event in events:
            found[event[key]] = found.get(event[key], 0) + 1
        return ", ".join(f"{name} {count}" for name, count in found.items())
    return f"{len(events)} ({counted('source')}); {counted('category')}; {dash(record.get('minutes'))} min"


def rework_lines(rework: dict | None) -> list[str]:
    if not rework or not rework["window_tasks"]:
        return []
    limits = rework["thresholds"]
    def line(group):
        return (f"- {group['key']} [{group['owner']}]{' repeat offender' if group['repeat_offender'] else ''}:"
                f" {group['events']} events in {len(group['tasks'])} tasks ({', '.join(group['tasks'][:8])}),"
                f" first {group['first_seen'][:10]}, last {group['last_seen'][:10]}")
    lines = ["", f"Recurring rework over the last {rework['window_tasks']} tasks (at least {limits['min_events']}"
                 f" events in {limits['min_tasks']} tasks, or one test failing in 2 or more tasks):"]
    lines += [line(group) + f"; rule: {group['rule']}" for group in rework["groups"] if group["status"] == "recurring"] \
        or ["- none"]
    rare = [group for group in rework["groups"] if group["status"] == "rare"]
    if rare:
        lines += ["Rare rework (listed, not promoted):"] + [line(group) for group in rare]
    if rework["unclassified"]:
        lines += ["Unclassified rework (classify: task_lane.py rework --task <id> --ref <ref> --category <id>"
                  " --note \"<cause>\"):"]
        lines += [f"- {item['task_id']} {item['ref']}: {item['text']}" for item in rework["unclassified"]]
    if rework["promote"]:
        lines += ["Memory candidates (project-owned recurring rules; the main session decides, nothing is written):"]
        lines += [f"- {item['rule']}" for item in rework["promote"]]
    return lines


def quality_line(spec: dict) -> str:
    return (f"critical {dash(spec['critical_per_task'])} and non-critical {dash(spec['non_critical_per_task'])}"
            f" per task over {spec['reviewed']} reviewed, filed {percent(spec['filed_share'])};"
            f" CI first pass {percent(spec['first_pass_rate'])} over {spec['ci_checked']}"
            f" ({dash(spec['red_runs_per_task'])} red runs per task); escaped {len(spec['escaped'])}/{spec['tasks']}"
            f" ({percent(spec['escaped_rate'])}){': ' + ', '.join(spec['escaped']) if spec['escaped'] else ''};"
            f" coverage " + (", ".join(f"{story} {percent(value)}" for story, value in spec["coverage"].items())
                             or "-"))


def tokens_line(tokens: dict) -> str:
    return " / ".join(f"{key} {tokens[key]}" for key in COST_TOKENS)


def cost_line(cost: dict) -> str:
    startup = cost["startup_context_per_spawn"]
    return (f"${cost['usd']:.2f} over {cost['tasks']} tasks, ${cost['usd_per_task']:.2f} per task;"
            f" tokens per task {tokens_line(cost['tokens_per_task'])}; {cost['spawns_per_task']} spawns per"
            f" task, startup context {startup if startup is not None else '-'} per spawn;"
            f" main share {cost['main_share'] if cost['main_share'] is not None else '-'};"
            f" by model {cost['by_model']}; by class {cost['by_class']}"
            + (f"; unpriced {', '.join(cost['unpriced_models'])}" if cost["unpriced_models"] else ""))


def export(data: dict, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(data, indent=2) + "\n"
    buffer = io.StringIO()
    fields = ["task_id", "title", "size", "rigor", "delivery", "pr", "requested_at",
              "approved_at", "pr_opened", "ci_green", "review", "merged", "total", *STEPS, *WAITS,
              "agents", "output_tokens", "usd", "input_tokens", "cache_write_tokens", "cache_read_tokens",
              "spawns", "startup_context", "main_usd", *QUALITY_KEPT, "fixes", "fixed_by", "story",
              "story_coverage"]
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in data["per_task"]:
        spent = sum(tokens["output"] for tokens in (row.get("cost") or {}).get("tokens_by_role", {}).values())
        cost = row.get("cost") or {}
        spawns = cost.get("spawns") or []
        writer.writerow({**row, **row["steps"], **(row.get("waits") or {}), **row["quality"],
                         "fixed_by": " ".join(row["quality"].get("fixed_by") or []),
                         "agents": cost.get("agents", ""), "output_tokens": spent, "usd": cost.get("usd", ""),
                         **{f"{key}_tokens": (cost.get("tokens") or {}).get(key, "")
                            for key in ("input", "cache_write", "cache_read")},
                         "spawns": len(spawns) if cost else "",
                         "startup_context": round(statistics.mean(spawn["startup_context"] for spawn in spawns))
                         if spawns else "", "main_usd": (cost.get("main") or {}).get("usd", "")})
    return buffer.getvalue()


def retrospective_note(data: dict, delivery: str) -> str:
    return (f"---\ntype: retrospective\ntitle: {json.dumps(delivery + ' retrospective')}\n"
            f"status: final\ndelivery: {delivery}\ntags:\n  - doc/retrospective\n"
            f"  - status/final\n---\n# {delivery} retrospective\n\n## Summary\n\n```text\n"
            f"{render(data)}\n```\n\n<!-- sec: nav -->\n## Navigation\n")


def history(root: Path) -> list[dict]:
    path = root / HISTORY
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def previous_run(root: Path, scope: str) -> dict | None:
    return next((item for item in reversed(history(root)) if item.get("scope") == scope), None)


def kept_costs(root: Path) -> dict[str, dict]:
    """Each merged task's latest saved cost and waits, by task id; a rescan appends a newer row, never rewrites."""
    return {item["task_id"]: item for item in history(root) if item.get("type") == "task_cost"}


def kept_quality(root: Path) -> dict[str, dict]:
    """Each merged task's latest saved GitHub quality (findings and CI first pass), by task id."""
    return {item["task_id"]: item for item in history(root) if item.get("type") == "task_quality"}


def kept_rework(root: Path) -> dict[str, dict]:
    """Each merged task's latest saved rework events, unclassified, by task id."""
    return {item["task_id"]: item for item in history(root) if item.get("type") == "task_rework"}


def remember(root: Path, data: dict, rescan: bool = False) -> None:
    """Append this run's summary, the next retrospective's comparison point, and once per merged
    task its cost and waits, which outlive the host's transcripts, and its GitHub quality
    (derived, git-ignored)."""
    total = data["request_to_merge_minutes"]
    spend = data.get("cost") or {}
    spec = data.get("quality") or {}
    kept = {} if rescan else kept_costs(root)
    graded = {} if rescan else kept_quality(root)
    reworked = {} if rescan else kept_rework(root)
    path = root / HISTORY
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"at": now(), "scope": data["scope"], "merged": data["merged"],
                                 "average": total["average"], "median": total["median"],
                                 "max": total["max"], "usd_per_task": spend.get("usd_per_task"),
                                 "cost_method": data.get("cost_method"),
                                 "tokens_per_task": sum((spend.get("tokens_per_task") or {}).values())
                                 if spend else None,
                                 "quality": {key: spec.get(key) for key in (
                                     "critical_per_task", "first_pass_rate", "escaped_rate", "coverage_average")}
                                 if spec else None}) + "\n")
        for row in data["per_task"]:
            if row["total"] is not None and row.get("cost") and row["task_id"] not in kept:
                handle.write(json.dumps({"type": "task_cost", "at": now(), "task_id": row["task_id"],
                                         "cost": row["cost"], "waits": row.get("waits")}) + "\n")
            if row["total"] is not None and row["task_id"] not in graded:
                handle.write(json.dumps({"type": "task_quality", "at": now(), "task_id": row["task_id"],
                                         "quality": {key: row["quality"].get(key) for key in QUALITY_KEPT}})
                             + "\n")
            if row["total"] is not None and row.get("rework_auto") is not None and row["task_id"] not in reworked:
                handle.write(json.dumps({"type": "task_rework", "at": now(), "task_id": row["task_id"],
                                         **{key: row.get(key) for key in ("kind", "size", "requested_at", "merged", "total")},
                                         "events": row["rework_auto"]}) + "\n")


def gather(root: Path, args) -> dict:
    tasks = []
    for path in sorted((root / TASKS).glob("tk-*.md")):
        tasks.append({**task_props(path), "file": path.name, "role_checks": role_checks(path),
                      "rework_notes": rework_notes(path)})
    chosen = select(tasks, args.delivery, args.since, args.recent)
    prs = pull_requests(root) if chosen else {}
    causes = rework_causes(root)
    graded = {} if getattr(args, "rescan", False) else kept_quality(root)
    reworked = kept_rework(root)
    fresh_rework = {} if getattr(args, "rescan", False) else reworked
    rows = [task_row(task, pr_times(root, prs[task["id"]]["number"], causes=causes,
                                    runs=task["id"] not in graded or task["id"] not in fresh_rework)
                     if task["id"] in prs else None) for task in chosen]
    for row in rows:
        if row["total"] is not None and row["task_id"] in graded:
            row["quality"].update(graded[row["task_id"]]["quality"])
        if row["total"] is not None and row["task_id"] in fresh_rework:
            row["rework_auto"] = fresh_rework[row["task_id"]]["events"]
    notes = {task["id"]: task["rework_notes"] for task in tasks}
    extra = [{**item, "rework_auto": item["events"], "rework_notes": notes.get(item["task_id"], [])}
             for item in reworked.values()]
    link_quality(rows, tasks, story_index(root) if any(task.get("source_story") for task in tasks) else {})
    unattributed = None
    kept = {} if getattr(args, "rescan", False) else kept_costs(root)
    deep = getattr(args, "deep", False)
    fresh = [row for row in rows if row["task_id"] not in kept or row["total"] is None]
    if chosen and not getattr(args, "no_sessions", False) and (fresh or deep):
        import session_cost
        since = min(parse_time(task["requested_at"]).timestamp() for task in chosen)
        unattributed = attach_sessions(rows, chosen, session_cost.scan(root, since), deep,
                                       datetime.now(timezone.utc).timestamp(), session_cost.load_prices(root))
    for row in rows:
        if row["total"] is not None and row["task_id"] in kept:
            row.update(cost=kept[row["task_id"]]["cost"], waits=kept[row["task_id"]]["waits"])
    scope = args.delivery or (f"since {args.since}" if args.since else f"latest {args.recent}")
    return {**report(rows, scope, previous_run(root, scope), unattributed, extra, causes),
            "cost_method": cost_method(root)}


def cost_method(root: Path) -> str | None:
    """A fingerprint of the effective price table; a change makes dollars incomparable."""
    try:
        import session_cost
    except ImportError:
        return None
    return hashlib.sha256(json.dumps(session_cost.load_prices(root), sort_keys=True).encode()).hexdigest()[:12]


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
        (root / BOARD).write_bytes(text.encode("utf-8"))
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
    if getattr(args, "fixes", None) and not ID_RE.match(args.fixes):
        raise Refused("--fixes must be TK-<number>")
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
    kind = "knowledge" if args.knowledge else "build"
    text, files = f"{args.title}\n{request}", named_files(request)
    roles = route(role_rules(root), text, kind, args.size, files,
                  routing_policy(root).get("pre_check_triggers", {}).get(args.size))
    triggers = task_triggers(root)
    flags = {rule["flag"] for rule in triggers["risk_flags"]}
    asked = set(getattr(args, "risk", None) or [])
    if asked - flags:
        raise Refused(f"--risk must be one of {', '.join(sorted(flags))}")
    risks = sorted(asked | set(triggered(triggers["risk_flags"], "flag", text, files)))
    skills = triggered(triggers["method_skills"], "skill", text, files)
    run("git", "fetch", "--prune", "origin", cwd=root)
    task = next_id(taken_names(root, args.base))
    slug = slugify(args.title)
    branch = f"task/{task.lower()}-{slug}"
    run("git", "switch", "-c", branch, f"origin/{args.base}", cwd=root)
    path = root / TASKS / f"{task.lower()}-{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    # The docs the task relies on: the ones it is given and the existing docs its request names.
    named = sorted(name for name in files if name.startswith("workspace/docs/") and (root / name).is_file())
    sources = hashed_sources(root, list(dict.fromkeys([*(getattr(args, "source", None) or []), *named])))
    path.write_bytes((task_text(task, args.title, request, requested_at, args.delivery or "",
                              rigor, epic, args.source_story or "", covers,
                              kind, args.size, getattr(args, "fixes", None) or "",
                              risks, skills, sources)).encode("utf-8"))
    if roles:
        set_field(path, "roles", roles_value(roles))
    run("git", "push", "-u", "origin", "HEAD", cwd=root)
    return {"task_id": task, "branch": branch, "task": str(path.relative_to(root)),
            "proposed_rigor": rigor, "rigor_reasons": reasons, "roles": roles, "spawn": spawn_roles(roles, root),
            "risks": risks, "applied_skills": skills, "sources": sources}


def cmd_lint(args, root: Path) -> dict:
    path = Path(args.file) if args.file else task_path(root, args.task)
    errors = lint(path)
    _, missing = ([], []) if errors else routed_roles(root, path)
    return {"ok": not errors, "task": str(path), "errors": errors,
            "warnings": [missing_message(task_props(path).get("id", args.task), missing)] if missing else []}


def cmd_approve(args, root: Path) -> dict:
    path = task_path(root, args.task)
    errors = lint(path)
    if errors:
        raise Refused("task lint failed: " + "; ".join(errors))
    if not task_props(path).get("approved_at"):
        set_field(path, "approved_at", args.at or now())
    # Sources added while the task was filled are hashed now, still at define time.
    sources = field_items(task_props(path), "sources")
    if sources != hashed_sources(root, sources):
        set_field(path, "sources", ", ".join(hashed_sources(root, sources)))
    roles, missing = routed_roles(root, path)
    if roles:
        set_field(path, "roles", roles_value(roles))
    props = task_props(path)
    return {"ok": True, "task_id": args.task, "approved_at": props["approved_at"],
            "rigor": props.get("rigor", "normal"), "roles": roles, "spawn": spawn_roles(roles, root),
            "pending_roles": missing}


def cmd_review_inputs(args, root: Path) -> dict:
    """What the review needs: its reviewers, the diff written to a file each reviewer reads itself,
    triggered skills the task did not apply and sources whose content changed since the task was defined."""
    path = task_path(root, args.task)
    props = task_props(path)
    files = changed_files(root, args.base)
    triggers = task_triggers(root)
    skills = triggered(triggers["method_skills"], "skill", f"{props.get('title', '')}\n{task_body(path)}",
                       named_files(task_body(path)) | files)
    risks = field_items(props, "risks")
    reviewers = triggered(triggers["reviewers"], "role", "", files) + [
        rule["role"] for rule in triggers["reviewers"]
        if props.get("rigor", "normal") in rule.get("rigors", []) or (risks and rule.get("on_any_risk"))]
    diff = REVIEWS / f"{args.task.lower()}.diff"
    (root / diff).parent.mkdir(parents=True, exist_ok=True)
    (root / diff).write_bytes(run("git", "diff", f"origin/{args.base}...HEAD", cwd=root).encode("utf-8"))
    return {"ok": True, "task_id": args.task, "reviewers": list(dict.fromkeys(reviewers)), "risks": risks,
            "diff_file": diff.as_posix(),
            "triggered_skills": skills,
            "missing_skills": sorted(set(skills) - set(applied_skills(props))),
            "changed_sources": changed_sources(root, field_items(props, "sources"))}


def cmd_role_check(args, root: Path) -> dict:
    """Record one role's verdict in the task's Role Checks section, with its time."""
    path = task_path(root, args.task)
    at = args.at or now()
    parse_time(at)
    line = f"- {at} {args.role} {args.verdict}: {' '.join((args.note or '-').split())}"
    if not CHECK_RE.match(line):
        raise Refused("--role must be a plugin agent id such as software-architect")
    add_line(path, "Role Checks", line)
    _, missing = routed_roles(root, path)
    return {"ok": True, "task_id": args.task, "role": args.role, "verdict": args.verdict, "at": at,
            "pending_roles": missing}


def add_line(path: Path, title: str, line: str) -> None:
    """Append one line to the task's section, creating the section before the navigation."""
    text = path.read_text(encoding="utf-8")
    found = re.search(rf"(?ms)^## {re.escape(title)}\s*$\n.*?(?=^## |^<!-- sec:|\Z)", text)
    if found:
        text = text[:found.end()].rstrip("\n") + f"\n{line}\n\n" + text[found.end():]
    else:
        nav = text.find("<!-- sec: nav -->")
        block = f"## {title}\n\n{line}\n\n"
        text = text[:nav] + block + text[nav:] if nav >= 0 else text.rstrip("\n") + "\n\n" + block
    path.write_bytes(text.encode("utf-8"))


def cmd_rework(args, root: Path) -> dict:
    """Record a rework cause only a person or agent knows; with --ref it classifies that event."""
    causes = rework_causes(root)
    known = [item["id"] for item in causes["categories"]]
    if args.category not in known:
        raise Refused(f"--category must be one of {', '.join(known)}")
    if args.ref and not re.match(r"^[a-z]+:\S+$", args.ref):
        raise Refused("--ref must be an event ref the report lists, such as run:123 or commit:abc1234")
    path = task_path(root, args.task)
    at = args.at or now()
    parse_time(at)
    note = scrub(args.note, 240)
    if not note:
        raise Refused("--note is empty or looks like a credential")
    line = f"- {at} {args.category}" + (f" {args.ref}" if args.ref else "") + f": {note}"
    add_line(path, "Rework", line)
    return {"ok": True, "task_id": args.task, "category": args.category, "ref": args.ref or "", "at": at}


def cmd_overlap(args, root: Path) -> dict:
    return {"ok": True, "advisory": True, "overlaps": overlap(root, args.base)}


def pr_proof(root: Path) -> str:
    """The ``pr_proof`` value in force; the default when the policy cannot be read."""
    try:
        return process_policy.effective_values(root / "workspace" / "docs")[0]["pr_proof"]["value"]
    except (KeyError, OSError, ValueError):
        return "remote_ci"


def proof_commands(root: Path, base: str) -> list[str]:
    path = root / PROJECT_PROOF
    commands = json.loads(path.read_text(encoding="utf-8"))["commands"] if path.is_file() else PROOF_COMMANDS
    if not commands or not all(isinstance(item, str) and item.strip() for item in commands):
        raise Refused(f"{PROJECT_PROOF.as_posix()} must list at least one command under commands")
    return [item.replace("{base}", base) for item in commands]


def head_state(root: Path) -> tuple[str, bool]:
    """HEAD and whether no tracked file differs from it."""
    return (run("git", "rev-parse", "HEAD", cwd=root),
            not run("git", "status", "--porcelain", "--untracked-files=no", cwd=root))


def proof_path(root: Path, task: str) -> Path:
    if not ID_RE.match(task):
        raise Refused("--task must be TK-<number>")
    return root / PROOFS / f"{task}.json"


def cmd_prove(args, root: Path) -> dict:
    """Run every local proof command on HEAD and record each exit code and time."""
    path = proof_path(root, args.task)
    head, clean = head_state(root)
    results = []
    for command in proof_commands(root, args.base):
        started, clock = now(), datetime.now(timezone.utc)
        done = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True)
        results.append({"command": command, "exit_code": done.returncode,
                        "seconds": round((datetime.now(timezone.utc) - clock).total_seconds(), 1),
                        "started_at": started, "finished_at": now(),
                        "tail": "" if done.returncode == 0 else scrub((done.stdout + done.stderr)[-2000:], 400),
                        "failed_tests": [] if done.returncode == 0 else failed_test_ids(done.stdout + done.stderr)})
    record = {"task": args.task, "head": head, "tree_clean": clean, "base": args.base,
              "ok": clean and all(item["exit_code"] == 0 for item in results), "commands": results}
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, indent=2) + "\n")
    return {**record, "record": str(path.relative_to(root))}


def only_task_changed(root: Path, task: str, proved: str | None, head: str) -> bool:
    """A commit after the proof that touches only the task's own record (a role check) changes no code it proved."""
    if not proved:
        return False
    try:
        changed = run("git", "diff", "--name-only", proved, head, cwd=root).splitlines()
        own = task_path(root, task).relative_to(root).as_posix()
    except Refused:
        return False
    return bool(changed) and all(path == own for path in changed) and not lint(root / own)


def proof_problems(root: Path, task: str) -> tuple[list[str], dict]:
    """Why the local proof does not cover HEAD, and the record."""
    path = proof_path(root, task)
    if not path.is_file():
        return [f"no local proof record; run prove --task {task}"], {}
    record = json.loads(path.read_text(encoding="utf-8"))
    head, clean = head_state(root)
    problems = []
    if record.get("head") != head and not only_task_changed(root, task, record.get("head"), head):
        problems.append(f"the proof ran on {str(record.get('head'))[:12]}, HEAD is {head[:12]}; run prove again")
    if not record.get("tree_clean"):
        problems.append("the proof ran on a tree with uncommitted changes; commit and run prove again")
    if not clean:
        problems.append("tracked files changed since HEAD; commit and run prove again")
    problems += [f"`{item['command']}` exited {item['exit_code']}"
                 + (f"; failed: {', '.join(item['failed_tests'])}" if item.get("failed_tests") else "")
                 for item in record.get("commands", []) if item.get("exit_code") != 0]
    if not record.get("commands"):
        problems.append("the proof record holds no command")
    return problems, record


def proof_section(record: dict) -> str:
    rows = "\n".join(f"| `{item['command']}` | pass | {item['seconds']} |" for item in record["commands"])
    finished = record["commands"][-1]["finished_at"]
    return (f"\n\n## Local proof\n\nEvery check passed on HEAD `{record['head']}` with a clean tree,"
            f" finished {finished}.\n\n| Command | Result | Seconds |\n|---|---|---|\n{rows}\n")


def cmd_open_pr(args, root: Path) -> dict:
    path = task_path(root, args.task)
    errors = lint(path)
    if not task_props(path).get("approved_at"):
        errors.append("approved_at is missing; run approve first")
    if not errors:
        _, missing = routed_roles(root, path, changed_files(root, args.base))
        if missing:
            errors.append(missing_message(args.task, missing))
    if errors:
        raise Refused("task is not ready: " + "; ".join(errors))
    record = None
    if pr_proof(root) == "local_record":
        # Checked before the delivered-status commit, the only change open-pr adds.
        problems, record = proof_problems(root, args.task)
        if problems:
            raise Refused("no local proof for this HEAD (switch pr_proof is local_record): " + "; ".join(problems))
    if task_props(path).get("status") == "active":
        set_field(path, "status", "delivered")
    run("git", "add", str(path), cwd=root)
    if run("git", "diff", "--cached", "--name-only", cwd=root):
        run("git", "commit", "-m", f"docs: deliver task {args.task}", cwd=root)
    warnings = overlap(root, args.base)
    run("git", "push", "-u", "origin", "HEAD", cwd=root)
    body = path.read_text(encoding="utf-8").split("\n---\n", 1)[-1]
    if record:
        body += proof_section(record)
    if warnings:
        body += "\n\nAdvisory overlap with open pull requests: " + "; ".join(
            f"#{item['pr']} ({', '.join(item['files'])})" for item in warnings)
    url = run("gh", "pr", "create", "--base", args.base, "--title",
              f"{args.task}: {task_props(path).get('title')}", "--body", body, cwd=root)
    return {"task_id": args.task, "pr": url.rstrip("/").rsplit("/", 1)[-1], "url": url,
            "overlaps": warnings}


def refresh_graph(root: Path, pr: str) -> None:
    """Start the graph refresh for the pull request's range in the background when it and graphify
    are installed; never blocks a merge."""
    if GRAPH_REFRESH.is_file() and shutil.which("graphify"):
        try:
            data = json.loads(run("gh", "pr", "view", str(pr), "--json", "baseRefName,headRefOid", cwd=root))
            commit_range = f"origin/{data['baseRefName']}...{data['headRefOid']}"
            subprocess.Popen([sys.executable, str(GRAPH_REFRESH), "--project-root", str(root),
                              "--range", commit_range], cwd=root, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except (OSError, Refused, ValueError, KeyError):
            pass


def cmd_merge(args, root: Path) -> dict:
    rigor = task_props(task_path(root, args.task)).get("rigor", "normal")
    note = REVIEW_MARK + (f"\n\n{args.detail}" if args.detail else "")
    counts = [getattr(args, key, None) for key in ("critical", "non_critical", "filed")]
    if any(count is not None and count < 0 for count in counts) or (counts[2] or 0) > (counts[1] or 0):
        raise Refused("finding counts are non-negative and --filed is at most --non-critical;"
                      " pass counts, never an issue number")
    if any(count is not None for count in counts):
        note += "\n\nfindings: critical {}, non-critical {}, filed {}".format(*(count or 0 for count in counts))
    owner_merges = rigor == "high" and not getattr(args, "delegated", False)
    if owner_merges:
        note += "\n\nRigor high: the owner merges this pull request."
    elif rigor == "high":
        note += "\n\nRigor high: auto-merged under the owner's delegation."
    run("gh", "pr", "comment", args.pr, "--body", note, cwd=root)
    if owner_merges:
        return {"task_id": args.task, "pr": args.pr, "auto_merge": False,
                "next": "rigor high: the owner reviews and merges"}
    try:
        run("gh", "pr", "merge", args.pr, "--auto", f"--{args.style}", cwd=root)
    except Refused as refused:
        # GitHub refuses auto-merge once required checks are already green; merge now.
        if "clean status" not in str(refused):
            raise
        run("gh", "pr", "merge", args.pr, f"--{args.style}", cwd=root)
        refresh_graph(root, args.pr)
        return {"task_id": args.task, "pr": args.pr, "auto_merge": False, "merged": True}
    refresh_graph(root, args.pr)
    return {"task_id": args.task, "pr": args.pr, "auto_merge": True}


def cmd_report(args, root: Path):
    data = gather(root, args)
    remember(root, data, getattr(args, "rescan", False))
    written = []
    if args.export:
        target = root / EXPORT / f"tasks.{args.export}"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(export(data, args.export).encode("utf-8"))
        written.append(str(target.relative_to(root)))
    if getattr(args, "write_lessons", False):
        lessons = write_lessons(root, data["rework"], rework_causes(root))
        written.append(lessons["path"])
        data = {**data, "lessons": lessons}
    if args.write_report:
        if not args.delivery:
            raise Refused("--write-report needs a Delivery scope, such as DLV-007")
        target = root / RETROSPECTIVES / f"{args.delivery.lower()}-retrospective.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(retrospective_note(data, args.delivery).encode("utf-8"))
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
    path.write_bytes((head + PENDING_UPSTREAM + tail.rstrip("\n") + "\n" + row + "\n").encode("utf-8"))
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
    start.add_argument("--fixes", help="the earlier task whose defect this task repairs, such as TK-007")
    start.add_argument("--risk", action="append", help="a risk flag of the task; repeatable")
    start.add_argument("--source", action="append", help="a project file the task relies on; repeatable")
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
    checked = sub.add_parser("role-check", help="record a routed role's verdict on the task")
    checked.add_argument("--task", required=True)
    checked.add_argument("--role", required=True, help="the plugin agent id, such as software-architect")
    checked.add_argument("--verdict", choices=VERDICTS, required=True)
    checked.add_argument("--note", help="the reasons, one line")
    checked.add_argument("--at")
    sub.add_parser("overlap").add_argument("--base", default="main")
    reviewed = sub.add_parser("review-inputs", help="reviewers, the diff file, missing triggered skills and changed sources")
    reviewed.add_argument("--task", required=True)
    reviewed.add_argument("--base", default="main")
    opened = sub.add_parser("open-pr")
    opened.add_argument("--task", required=True)
    opened.add_argument("--base", default="main")
    proved = sub.add_parser("prove", help="run the local proof commands on HEAD and record them")
    proved.add_argument("--task", required=True)
    proved.add_argument("--base", default="main")
    merge = sub.add_parser("merge")
    merge.add_argument("--task", required=True)
    merge.add_argument("--pr", required=True)
    merge.add_argument("--style", choices=("merge", "squash", "rebase"), default="merge")
    merge.add_argument("--detail")
    merge.add_argument("--delegated", action="store_true",
                       help="rigor high under a delegation that allows merge: auto-merge too")
    for key in ("critical", "non-critical", "filed"):
        merge.add_argument(f"--{key}", type=int, help=f"the review's {key} finding count"
                           if key != "filed" else "the non-critical findings filed as issues")
    retro = sub.add_parser("report")
    retro.add_argument("--json", action="store_true")
    retro.add_argument("--export", choices=("csv", "json"))
    retro.add_argument("--write-report", action="store_true")
    retro.add_argument("--rescan", action="store_true",
                       help="price every task from the transcripts again and save the result as its newest row")
    retro.add_argument("--write-lessons", action="store_true",
                       help="update the tracked lessons file with the recurring rework causes")
    retro.add_argument("--deep", action="store_true",
                       help="also report files read 5 or more times and tool results over 200 KB")
    redo = sub.add_parser("rework", help="record why the task needed another pass")
    redo.add_argument("--task", required=True)
    redo.add_argument("--category", required=True, help="a cause id of rework-causes.json")
    redo.add_argument("--note", required=True, help="the cause, one line")
    redo.add_argument("--ref", help="the event the cause explains, as the report lists it")
    redo.add_argument("--at")
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
                "overlap": cmd_overlap, "open-pr": cmd_open_pr, "prove": cmd_prove, "merge": cmd_merge,
                "report": cmd_report, "file-issues": cmd_file_issues,
                "criteria": cmd_criteria, "board": cmd_board,
                "role-check": cmd_role_check, "rework": cmd_rework, "review-inputs": cmd_review_inputs}
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
