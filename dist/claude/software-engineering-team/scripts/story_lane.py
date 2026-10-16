#!/usr/bin/env python3
"""Fast-lane story path: one card, one branch, one pull request, one clock.

Process switch ``process_profile`` at ``fast_lane`` routes a request through
this script instead of the Requirement, Backlog and Delivery ceremonies:

- ``start`` creates ``story/<id>-<slug>`` from the target branch and writes
  the story card ``workspace/docs/backlog/cards/<id>-<slug>.md`` with its
  ``requested_at`` time and the proposed ``rigor``.
- ``lint`` checks one card: title, size, rigor, 3 to 8 acceptance criteria
  and a test list. ``approve`` lints it and writes ``approved_at``.
- ``overlap`` warns, never refuses, when an open pull request touches the
  same files as this branch.
- ``open-pr`` marks the card delivered, pushes the branch and opens the PR.
- ``merge`` posts the reviewers' pass on the PR and, at rigor ``normal``,
  turns on auto-merge; at rigor ``high`` the owner merges.
- ``report`` is the retrospective: the cards hold the request and approval
  times, GitHub holds the PR, review, CI and merge times, read at report
  time. ``--export`` rebuilds a derived file under ``.agentrof/``;
  ``--write-report`` keeps a Delivery's summary under
  ``workspace/docs/delivery/retrospectives/``. ``file-issues`` opens one
  issue per proposal the user approved, in the project's own repository.

Nothing is written after a merge, and no file is shared between stories: each
card travels in its own story's pull request.
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

CARDS = Path("workspace") / "docs" / "backlog" / "cards"
RETROSPECTIVES = Path("workspace") / "docs" / "delivery" / "retrospectives"
EXPORT = Path(".agentrof") / "retrospective"
CARD_TYPE = "story-card"
CARD_STATUSES = ("active", "delivered", "dropped")
SIZES = ("s", "m", "l")
RIGORS = ("normal", "high")
# A request naming one of these proposes rigor high; the owner confirms on the card.
HIGH_RIGOR_RE = re.compile(
    r"\b(auth\w*|login|password\w*|credential\w*|secret\w*|token\w*|api[ -]?key\w*|"
    r"permission\w*|delet\w*|drop\w*|purg\w*|wip\w*|truncat\w*|irreversib\w*|migrat\w*)\b",
    re.IGNORECASE)
ID_RE = re.compile(r"^FL-[0-9]{3,}$")
DELIVERY_RE = re.compile(r"^DLV-[0-9]{3,}$")
AC_RANGE = (3, 8)
TARGET_MINUTES = 15
REVIEW_MARK = "fast-lane review: pass"
# step -> (start, end); CI and review run side by side after the PR opens.
STEPS = {"card": ("requested_at", "approved_at"), "write": ("approved_at", "pr_opened"),
         "ci": ("pr_opened", "ci_green"), "review": ("pr_opened", "review"),
         "merge_wait": (("ci_green", "review"), "merged")}
LEVERS = {
    "card": "Write the card in one pass from the request: cap it at 5 criteria, name"
            " tests by file and approve it at once.",
    "write": "Split stories whose write step runs long, or run backend and frontend"
             " writers side by side on disjoint files.",
    "ci": "Run only the suites the diff touches on the PR and move the rest to a"
          " nightly run on main; cache dependencies in CI.",
    "review": "Give the reviewer the diff and the card only, single pass, with a"
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


# --- card ------------------------------------------------------------------

def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48].strip("-") or "story"


def next_id(names: list[str]) -> str:
    """One past the highest story number among card files and story branch names."""
    numbers = [int(match.group(1)) for name in names
               if (match := re.search(r"(?:^|/)fl-([0-9]+)-", name))]
    return f"FL-{max(numbers, default=0) + 1:03d}"


def taken_names(root: Path, base: str) -> list[str]:
    """Cards on the target branch and every story branch on the remote, so parallel
    stories never share an id; ``start`` pushes its branch at once to claim it."""
    cards = run("git", "ls-tree", "--name-only", f"origin/{base}", f"{CARDS.as_posix()}/",
                cwd=root).splitlines()
    branches = run("git", "for-each-ref", "--format=%(refname:short)",
                   "refs/remotes/origin/story/", cwd=root).splitlines()
    return cards + branches


def proposed_rigor(request: str) -> tuple[str, list[str]]:
    hits = sorted({match.group(0).lower() for match in HIGH_RIGOR_RE.finditer(request)})
    return ("high" if hits else "normal"), hits


def card_text(story: str, title: str, request: str, requested_at: str, delivery: str,
              rigor: str) -> str:
    grouped = f"delivery: {delivery}\n" if delivery else ""
    return (f"---\ntype: {CARD_TYPE}\ntitle: {json.dumps(title)}\nstatus: active\nid: {story}\n"
            f"requested_at: {requested_at}\nsize: s\nrigor: {rigor}\n{grouped}"
            f"tags:\n  - doc/{CARD_TYPE}\n  - status/active\naliases:\n  - {story}\n---\n"
            f"# {title}\n\n## Request\n\n{request.strip()}\n\n## Acceptance Criteria\n\n"
            "1. \n2. \n3. \n\n## Tests\n\n- \n\n<!-- sec: nav -->\n## Navigation\n\n"
            "- [[maps/backlog|Backlog]]\n")


def section(body: str, title: str) -> list[str]:
    match = re.search(rf"(?ms)^## {re.escape(title)}\s*$\n(.*?)(?=^## |^<!-- sec:|\Z)", body)
    return [line.strip() for line in match.group(1).splitlines() if line.strip()] if match else []


def card_props(path: Path) -> dict:
    return parse_frontmatter(path.read_text(encoding="utf-8"))[0]


def lint(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    props, start, error = parse_frontmatter(text)
    if error:
        return [error]
    body = "\n".join(text.splitlines()[start - 1:])
    errors = []
    if props.get("type") != CARD_TYPE:
        errors.append(f"type must be {CARD_TYPE}")
    if not ID_RE.match(str(props.get("id", ""))):
        errors.append("id must be FL-<number>")
    if not str(props.get("title", "")).strip():
        errors.append("title is required")
    if props.get("status") not in CARD_STATUSES:
        errors.append(f"status must be one of {', '.join(CARD_STATUSES)}")
    if props.get("size") not in SIZES:
        errors.append(f"size must be one of {', '.join(SIZES)}")
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
    if not section(body, "Request"):
        errors.append("Request section is empty")
    criteria = [line for line in section(body, "Acceptance Criteria")
                if re.match(r"^[0-9]+\.\s+\S", line)]
    if not AC_RANGE[0] <= len(criteria) <= AC_RANGE[1]:
        errors.append(f"Acceptance Criteria needs {AC_RANGE[0]} to {AC_RANGE[1]} numbered"
                      f" criteria, found {len(criteria)}")
    if not [line for line in section(body, "Tests") if re.match(r"^-\s+\S", line)]:
        errors.append("Tests needs at least one '- <test>' line")
    return errors


def card_path(root: Path, story: str) -> Path:
    matches = sorted((root / CARDS).glob(f"{story.lower()}-*.md"))
    if len(matches) != 1:
        raise Refused(f"no single card for {story} under {CARDS}")
    return matches[0]


def set_field(path: Path, key: str, value: str) -> None:
    """Set one front matter scalar, keeping every other byte of the card."""
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
    """The newest story pull request of each story id."""
    listed = json.loads(run("gh", "pr", "list", "--state", "all", "--limit", "1000", "--json",
                            "number,headRefName", cwd=root) or "[]")
    found: dict[str, dict] = {}
    for pr in listed:
        match = re.match(r"^story/(fl-[0-9]+)-", pr.get("headRefName") or "")
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


def story_row(card: dict, pr: dict | None) -> dict:
    times = {"requested_at": card.get("requested_at"), "approved_at": card.get("approved_at"),
             **{key: (pr or {}).get(key) for key in ("pr_opened", "ci_green", "review", "merged")}}
    steps = {step: minutes_between(times, start, end) for step, (start, end) in STEPS.items()}
    known = {step: value for step, value in steps.items() if value is not None}
    return {"story_id": card["id"], "title": card.get("title", ""), "size": card.get("size", ""),
            "rigor": card.get("rigor", "normal"), "delivery": card.get("delivery", ""),
            "pr": (pr or {}).get("pr", ""), **times,
            "total": minutes_between(times, "requested_at", "merged"), "steps": steps,
            "largest_step": max(known, key=known.get) if known else None,
            "checks": (pr or {}).get("checks", {})}


def select(cards: list[dict], delivery: str | None = None, since: str | None = None,
           recent: int | None = None, at: datetime | None = None) -> list[dict]:
    """The cards in scope: one Delivery, a time window or the latest stories."""
    chosen = [card for card in cards if card.get("requested_at")]
    if delivery:
        chosen = [card for card in chosen if card.get("delivery") == delivery]
    if since:
        match = re.fullmatch(r"([0-9]+)([dh])", since)
        if not match:
            raise Refused("--since takes <n>d or <n>h, such as 7d")
        hours = int(match.group(1)) * (24 if match.group(2) == "d" else 1)
        cutoff = (at or datetime.now(timezone.utc)).timestamp() - hours * 3600
        chosen = [card for card in chosen if parse_time(card["requested_at"]).timestamp() >= cutoff]
    chosen.sort(key=lambda card: parse_time(card["requested_at"]))
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
        values = [(row["story_id"], row["steps"][step]) for row in rows
                  if row["steps"][step] is not None]
        if not values:
            continue
        median = statistics.median(value for _sid, value in values)
        worst = max(values, key=lambda pair: pair[1])
        found.append({"id": f"FL-{step.upper().replace('_', '-')}", "step": step, "weight": median,
                      "evidence": f"{step} median {round(median, 1)} min over {len(values)}"
                                  f" stories, max {worst[1]} min ({worst[0]})",
                      "proposal": LEVERS[step]})
    checks: dict[str, list[float]] = {}
    for row in rows:
        for name, minutes in row["checks"].items():
            checks.setdefault(name, []).append(minutes)
    if checks:
        name, values = max(checks.items(), key=lambda item: statistics.median(item[1]))
        median = statistics.median(values)
        slug = re.sub(r"[^A-Z0-9]+", "-", name.upper()).strip("-")[:40] or "CHECK"
        found.append({"id": f"FL-CHECK-{slug}", "step": "ci", "weight": median,
                      "evidence": f"required check '{name}' median {round(median, 1)} min,"
                                  f" max {max(values)} min over {len(values)} runs",
                      "proposal": f"Shorten check '{name}': run only what the diff touches,"
                                  " cache its setup, or move it off the required set."})
    open_rows = [row["story_id"] for row in rows if row["total"] is None]
    if open_rows:
        found.append({"id": "FL-OPEN-STORIES", "step": "open", "weight": -1.0,
                      "evidence": f"{len(open_rows)} stories have no merge time:"
                                  f" {', '.join(open_rows[:10])}",
                      "proposal": "Finish or drop each open story; a stale pull request hides"
                                  " the real request-to-merge time."})
    found.sort(key=lambda item: -item["weight"])
    for item in found:
        item.pop("weight")
    return found[:5]


def report(rows: list[dict], scope: str) -> dict:
    totals = [row["total"] for row in rows if row["total"] is not None]
    slowest = sorted((row for row in rows if row["total"] is not None),
                     key=lambda row: -row["total"])[:3]
    return {"scope": scope, "stories": len(rows), "merged": len(totals),
            "target_minutes": TARGET_MINUTES, "request_to_merge_minutes": summary(totals),
            "per_story": rows,
            "slowest": [{"story_id": row["story_id"], "total": row["total"],
                         "largest_step": row["largest_step"],
                         "largest_minutes": row["steps"].get(row["largest_step"])}
                        for row in slowest],
            "proposals": proposals(rows)}


def render(data: dict) -> str:
    total = data["request_to_merge_minutes"]
    lines = [f"Scope: {data['scope']}",
             f"Stories: {data['stories']} ({data['merged']} merged), target {data['target_minutes']} min",
             f"Request to merge: average {total['average']}, median {total['median']},"
             f" max {total['max']} min", "",
             "Per story (min): card / write / ci / review / merge_wait = total"]
    for row in data["per_story"]:
        steps = " / ".join("-" if row["steps"][step] is None else str(row["steps"][step])
                           for step in STEPS)
        lines.append(f"- {row['story_id']} ({row['size'] or '-'}, {row['rigor']}) PR"
                     f" {row['pr'] or '-'}: {steps}"
                     f" = {row['total'] if row['total'] is not None else 'open'}")
    if data["slowest"]:
        lines += ["", "Slowest:"]
        lines += [f"- {item['story_id']}: {item['total']} min, largest step {item['largest_step']}"
                  f" ({item['largest_minutes']} min)" for item in data["slowest"]]
    if data["proposals"]:
        lines += ["", "Proposals (approve by number or id to file one issue each):"]
        lines += [f"{number}. [{item['id']}] {item['proposal']} Evidence: {item['evidence']}"
                  for number, item in enumerate(data["proposals"], 1)]
    if not data["stories"]:
        lines.append("No story card in scope.")
    return "\n".join(lines)


def export(data: dict, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(data, indent=2) + "\n"
    buffer = io.StringIO()
    fields = ["story_id", "title", "size", "rigor", "delivery", "pr", "requested_at",
              "approved_at", "pr_opened", "ci_green", "review", "merged", "total", *STEPS]
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in data["per_story"]:
        writer.writerow({**row, **row["steps"]})
    return buffer.getvalue()


def retrospective_note(data: dict, delivery: str) -> str:
    return (f"---\ntype: retrospective\ntitle: {json.dumps(delivery + ' retrospective')}\n"
            f"status: final\ndelivery: {delivery}\ntags:\n  - doc/retrospective\n"
            f"  - status/final\n---\n# {delivery} retrospective\n\n## Summary\n\n```text\n"
            f"{render(data)}\n```\n\n<!-- sec: nav -->\n## Navigation\n\n"
            "- [[maps/delivery|Delivery]]\n")


def gather(root: Path, args) -> dict:
    cards = [card_props(path) for path in sorted((root / CARDS).glob("fl-*.md"))]
    chosen = select(cards, args.delivery, args.since, args.recent)
    prs = pull_requests(root) if chosen else {}
    rows = [story_row(card, pr_times(root, prs[card["id"]]["number"]) if card["id"] in prs else None)
            for card in chosen]
    scope = args.delivery or (f"since {args.since}" if args.since else f"latest {args.recent}")
    return report(rows, scope)


# --- commands --------------------------------------------------------------

def cmd_start(args, root: Path) -> dict:
    if args.delivery and not DELIVERY_RE.match(args.delivery):
        raise Refused("--delivery must be DLV-<number>")
    requested_at = args.at or now()
    parse_time(requested_at)
    request = Path(args.request_file).read_text(encoding="utf-8") if args.request_file else args.request
    request = request or args.title
    rigor, reasons = proposed_rigor(f"{args.title}\n{request}")
    run("git", "fetch", "--prune", "origin", cwd=root)
    story = next_id(taken_names(root, args.base))
    slug = slugify(args.title)
    branch = f"story/{story.lower()}-{slug}"
    run("git", "switch", "-c", branch, f"origin/{args.base}", cwd=root)
    path = root / CARDS / f"{story.lower()}-{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(card_text(story, args.title, request, requested_at, args.delivery or "",
                              rigor), encoding="utf-8")
    run("git", "push", "-u", "origin", "HEAD", cwd=root)
    return {"story_id": story, "branch": branch, "card": str(path.relative_to(root)),
            "proposed_rigor": rigor, "rigor_reasons": reasons}


def cmd_lint(args, root: Path) -> dict:
    path = Path(args.card) if args.card else card_path(root, args.story)
    errors = lint(path)
    return {"ok": not errors, "card": str(path), "errors": errors}


def cmd_approve(args, root: Path) -> dict:
    path = card_path(root, args.story)
    errors = lint(path)
    if errors:
        raise Refused("card lint failed: " + "; ".join(errors))
    if not card_props(path).get("approved_at"):
        set_field(path, "approved_at", args.at or now())
    props = card_props(path)
    return {"ok": True, "story_id": args.story, "approved_at": props["approved_at"],
            "rigor": props.get("rigor", "normal")}


def cmd_overlap(args, root: Path) -> dict:
    return {"ok": True, "advisory": True, "overlaps": overlap(root, args.base)}


def cmd_open_pr(args, root: Path) -> dict:
    path = card_path(root, args.story)
    errors = lint(path)
    if not card_props(path).get("approved_at"):
        errors.append("approved_at is missing; run approve first")
    if errors:
        raise Refused("card is not ready: " + "; ".join(errors))
    if card_props(path).get("status") == "active":
        set_field(path, "status", "delivered")
    run("git", "add", str(path), cwd=root)
    if run("git", "diff", "--cached", "--name-only", cwd=root):
        run("git", "commit", "-m", f"docs: deliver story card {args.story}", cwd=root)
    warnings = overlap(root, args.base)
    run("git", "push", "-u", "origin", "HEAD", cwd=root)
    body = path.read_text(encoding="utf-8").split("\n---\n", 1)[-1]
    if warnings:
        body += "\n\nAdvisory overlap with open pull requests: " + "; ".join(
            f"#{item['pr']} ({', '.join(item['files'])})" for item in warnings)
    url = run("gh", "pr", "create", "--base", args.base, "--title",
              f"{args.story}: {card_props(path).get('title')}", "--body", body, cwd=root)
    return {"story_id": args.story, "pr": url.rstrip("/").rsplit("/", 1)[-1], "url": url,
            "overlaps": warnings}


def cmd_merge(args, root: Path) -> dict:
    rigor = card_props(card_path(root, args.story)).get("rigor", "normal")
    note = REVIEW_MARK + (f"\n\n{args.detail}" if args.detail else "")
    if rigor == "high":
        note += "\n\nRigor high: the owner merges this pull request."
    run("gh", "pr", "comment", args.pr, "--body", note, cwd=root)
    if rigor == "high":
        return {"story_id": args.story, "pr": args.pr, "auto_merge": False,
                "next": "rigor high: the owner reviews and merges"}
    run("gh", "pr", "merge", args.pr, "--auto", f"--{args.style}", cwd=root)
    return {"story_id": args.story, "pr": args.pr, "auto_merge": True}


def cmd_report(args, root: Path):
    data = gather(root, args)
    written = []
    if args.export:
        target = root / EXPORT / f"stories.{args.export}"
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
    repo = repo_slug(root)
    total = data["request_to_merge_minutes"]
    opened = []
    for proposal_id in dict.fromkeys(by_id[item]["id"] for item in wanted):
        item = by_id[proposal_id]
        body = (f"{item['proposal']}\n\nEvidence: {item['evidence']}\n\n"
                f"Fast-lane retrospective ({data['scope']}): {data['merged']} merged stories,"
                f" request to merge average {total['average']}, median {total['median']}, max"
                f" {total['max']} min (target {data['target_minutes']}).\n\nProposal id: {proposal_id}")
        url = run("gh", "issue", "create", "--repo", repo, "--title",
                  f"Fast lane: {item['step']} - {proposal_id}", "--body", body, cwd=root)
        opened.append({"id": proposal_id, "url": url})
    return {"ok": True, "repo": repo, "opened": opened}


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
    start.add_argument("--delivery", help="group the story under a Delivery label, such as DLV-007")
    check = sub.add_parser("lint")
    check.add_argument("--card")
    check.add_argument("--story")
    approve = sub.add_parser("approve")
    approve.add_argument("--story", required=True)
    approve.add_argument("--at")
    sub.add_parser("overlap").add_argument("--base", default="main")
    opened = sub.add_parser("open-pr")
    opened.add_argument("--story", required=True)
    opened.add_argument("--base", default="main")
    merge = sub.add_parser("merge")
    merge.add_argument("--story", required=True)
    merge.add_argument("--pr", required=True)
    merge.add_argument("--style", choices=("merge", "squash", "rebase"), default="merge")
    merge.add_argument("--detail")
    retro = sub.add_parser("report")
    retro.add_argument("--json", action="store_true")
    retro.add_argument("--export", choices=("csv", "json"))
    retro.add_argument("--write-report", action="store_true")
    issues = sub.add_parser("file-issues")
    issues.add_argument("--ids", required=True, help="comma-separated proposal numbers or ids")
    for scoped in (retro, issues):
        scoped.add_argument("--delivery")
        scoped.add_argument("--since")
        scoped.add_argument("--recent", type=int, default=20)
    args = parser.parse_args(argv)
    handlers = {"start": cmd_start, "lint": cmd_lint, "approve": cmd_approve,
                "overlap": cmd_overlap, "open-pr": cmd_open_pr, "merge": cmd_merge,
                "report": cmd_report, "file-issues": cmd_file_issues}
    try:
        if args.command == "lint" and not (args.card or args.story):
            raise Refused("lint needs --card or --story")
        result = handlers[args.command](args, project_root(args.project))
    except (Refused, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1
    print(result if isinstance(result, str) else json.dumps(result, indent=2))
    return 1 if isinstance(result, dict) and result.get("ok") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
