#!/usr/bin/env python3
"""Report measured validation costs per CI job."""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
from pathlib import Path
import subprocess
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import ci_tests

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = "tools/data/ci-performance-policy.json"


def timestamp(value):
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ci_tests.CIError("measurement timestamp must include its timezone")
    return parsed


def seconds(start, end):
    start, end = timestamp(start), timestamp(end)
    return max(0.0, (end - start).total_seconds()) if start and end else None


def job_measurements(jobs, policy):
    """Time after known dependencies is scheduling delay, not test execution."""
    measurements = []
    for job in jobs:
        name = job.get("name", "")
        patterns = next((row["depends_on"] for row in policy["job_dependencies"]
                         if fnmatch.fnmatchcase(name.rsplit(" / ", 1)[-1], row["pattern"])), None)
        scope = name.rsplit(" / ", 1)[0] if " / " in name else ""
        completed = []
        complete_dependencies = patterns is not None
        for pattern in patterns or []:
            matches = [item for item in jobs
                       if (item.get("name", "").rsplit(" / ", 1)[0] if " / " in item.get("name", "") else "") == scope
                       and fnmatch.fnmatchcase(item.get("name", "").rsplit(" / ", 1)[-1], pattern)]
            if not matches or any(not item.get("completed_at") for item in matches):
                complete_dependencies = False
            completed.extend(item["completed_at"] for item in matches if item.get("completed_at"))
        ready = max(completed, key=timestamp) if completed and complete_dependencies else None
        steps = {item.get("name", ""): seconds(item.get("started_at"), item.get("completed_at"))
                 for item in job.get("steps", [])}
        test_steps = [value for name, value in steps.items()
                      if any(fnmatch.fnmatchcase(name, pattern) for pattern in policy["test_steps"])]
        test_seconds = sum(test_steps) if test_steps and all(value is not None for value in test_steps) else None
        measurements.append({"name": name, "job_id": job.get("id"), "status": job.get("status"),
            "conclusion": job.get("conclusion"), "dependency_ready_at": ready,
            "scheduling_seconds": seconds(ready, job.get("started_at")),
            "wall_seconds": seconds(job.get("started_at"), job.get("completed_at")),
            "test_step_seconds": test_seconds, "step_seconds": steps})
    return measurements


def fetch_jobs(repository, run_id, attempt):
    if len(repository.split("/")) != 2 or not all(part and all(c.isalnum() or c in "-_." for c in part)
                                                   for part in repository.split("/")):
        raise ci_tests.CIError("invalid repository identity")
    if not run_id.isdigit() or not attempt.isdigit():
        raise ci_tests.CIError("run and attempt must be numeric")
    payload = json.loads(subprocess.check_output(["gh", "api", "--paginate", "--slurp",
        f"repos/{repository}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100"], text=True, encoding="utf-8"))
    return [job for page in payload for job in page["jobs"]]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=ROOT / POLICY_PATH)
    commands = parser.add_subparsers(dest="command", required=True)
    jobs = commands.add_parser("jobs")
    jobs.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    jobs.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    jobs.add_argument("--attempt", default=os.environ.get("GITHUB_RUN_ATTEMPT", ""))
    jobs.add_argument("--jobs-json", type=Path)
    jobs.add_argument("--durations", type=Path)
    jobs.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        policy = ci_tests.read_json(args.policy)
        if policy.get("schema_version") != 1:
            raise ci_tests.CIError("unsupported performance policy")
        jobs = ci_tests.read_json(args.jobs_json) if args.jobs_json else fetch_jobs(args.repository, args.run_id, args.attempt)
        result = {"schema_version": 1, "authority": "measurement_only", "run_id": args.run_id,
                  "run_attempt": args.attempt, "jobs": job_measurements(jobs, policy)}
        if args.durations:
            result["test_measurements"] = ci_tests.read_json(args.durations).get("measurements", [])
        ci_tests.write_json(args.output, result)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with Path(summary_path).open("a", encoding="utf-8") as output:
                output.write("\n## Validation time\n\nScheduling time starts after known dependencies complete. "
                             "An unfinished job has no completed wall time.\n\n")
                output.write("| Job | Scheduling seconds | Test step seconds | Job seconds |\n| --- | ---: | ---: | ---: |\n")
                for row in result["jobs"]:
                    name = row['name'].replace('|', '/').replace('\n', ' ')
                    output.write(f"| {name} | {row['scheduling_seconds']} | {row['test_step_seconds']} | {row['wall_seconds']} |\n")
        return 0
    except (ci_tests.CIError, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"ci-performance: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
