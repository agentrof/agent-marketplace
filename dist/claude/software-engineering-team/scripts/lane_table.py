#!/usr/bin/env python3
"""Record parallel lanes so a restarted session never relaunches a finished one.

Process switch `lane_table` at `recorded` uses start, finish and pending;
`lane_isolation` at `scratch_clone` adds the working-directory and main
checkout branch checks. The table is project-local runtime scratch.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atomic_file  # noqa: E402

RUNTIME = Path(".agentrof") / "agent-marketplace" / ".runtime" / "lanes"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
STATES = ("running", "finished", "failed")
ISOLATION = ("shared_checkout", "scratch_clone")


class Refused(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def name(value: str) -> str:
    if not NAME_RE.match(value):
        raise argparse.ArgumentTypeError(f"invalid name: {value!r}")
    return value


def git_root(path: Path) -> Path:
    out = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise Refused("LANE_NOT_A_CHECKOUT", f"{path} is not inside a Git checkout")
    return Path(out.stdout.strip()).resolve()


def branch(path: Path) -> str:
    out = subprocess.run(["git", "-C", str(path), "symbolic-ref", "--quiet", "--short", "HEAD"],
                         capture_output=True, text=True)
    if out.returncode == 0:
        return out.stdout.strip()
    head = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          capture_output=True, text=True)
    return f"detached:{head.stdout.strip()}"


def table_path(root: Path, run: str) -> Path:
    return root / RUNTIME / f"{run}.json"


def load(path: Path, run: str) -> dict:
    if not path.is_file():
        return {"schema_version": 1, "run": run, "main_branch": None, "lanes": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Refused("LANE_TABLE_CORRUPT", f"{path} cannot be read: {exc}") from exc
    if not isinstance(data, dict) or data.get("run") != run or not isinstance(data.get("lanes"), dict):
        raise Refused("LANE_TABLE_CORRUPT", f"{path} is not the lane table of run {run}")
    return data


def save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_file.replace_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def inside(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def start(args, root: Path) -> dict:
    path = table_path(root, args.run)
    data = load(path, args.run)
    lane = data["lanes"].get(args.lane)
    if lane and lane["state"] == "finished":
        raise Refused("LANE_ALREADY_FINISHED",
                      f"lane {args.lane} finished at {lane['finished_at']}: {lane.get('note', '')}")
    workdir = Path(args.workdir).resolve()
    if args.isolation == "scratch_clone":
        if not args.main:
            raise Refused("LANE_MAIN_REQUIRED", "scratch_clone needs --main <main checkout>")
        main = git_root(Path(args.main))
        if inside(workdir, main):
            raise Refused("LANE_IN_MAIN_CHECKOUT",
                          f"lane {args.lane} works in the main checkout; clone it to its own scratch directory")
        if data["main_branch"] is None:
            data["main_branch"] = branch(main)
    data["lanes"][args.lane] = {
        "role": args.role, "branch": args.branch, "workdir": str(workdir),
        "isolation": args.isolation, "state": "running", "started_at": now(),
        "finished_at": None, "note": "",
    }
    save(path, data)
    return {"status": "started", "run": args.run, "lane": args.lane, "table": str(path)}


def finish(args, root: Path) -> dict:
    path = table_path(root, args.run)
    data = load(path, args.run)
    lane = data["lanes"].get(args.lane)
    if lane is None:
        raise Refused("LANE_UNKNOWN", f"lane {args.lane} was never started in run {args.run}")
    lane.update(state=args.state, finished_at=now(), note=args.note)
    save(path, data)
    return {"status": args.state, "run": args.run, "lane": args.lane}


def pending(args, root: Path) -> dict:
    data = load(table_path(root, args.run), args.run)
    lanes = data["lanes"]
    return {"status": "ok", "run": args.run,
            "finished": sorted(k for k, v in lanes.items() if v["state"] == "finished"),
            "resume": sorted(k for k, v in lanes.items() if v["state"] == "running"),
            "retry": sorted(k for k, v in lanes.items() if v["state"] == "failed")}


def check(args, root: Path) -> dict:
    data = load(table_path(root, args.run), args.run)
    expected = data.get("main_branch")
    actual = branch(git_root(Path(args.main)))
    if expected is not None and actual != expected:
        raise Refused("LANE_MAIN_BRANCH_CHANGED",
                      f"main checkout is on {actual}, the run began on {expected}")
    return {"status": "ok", "run": args.run, "main_branch": actual}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", help="project root; defaults to the Git checkout of the current directory")
    sub = p.add_subparsers(dest="verb", required=True)
    s = sub.add_parser("start")
    s.add_argument("--run", type=name, required=True)
    s.add_argument("--lane", type=name, required=True)
    s.add_argument("--role", default="")
    s.add_argument("--branch", default="")
    s.add_argument("--workdir", required=True)
    s.add_argument("--isolation", choices=ISOLATION, default="shared_checkout")
    s.add_argument("--main")
    f = sub.add_parser("finish")
    f.add_argument("--run", type=name, required=True)
    f.add_argument("--lane", type=name, required=True)
    f.add_argument("--state", choices=STATES[1:], required=True)
    f.add_argument("--note", default="")
    q = sub.add_parser("pending")
    q.add_argument("--run", type=name, required=True)
    c = sub.add_parser("check")
    c.add_argument("--run", type=name, required=True)
    c.add_argument("--main", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        root = Path(args.root).resolve() if args.root else git_root(Path.cwd())
        result = {"start": start, "finish": finish, "pending": pending, "check": check}[args.verb](args, root)
    except Refused as exc:
        print(json.dumps({"status": "refused", "code": exc.code, "message": str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
