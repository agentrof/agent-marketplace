#!/usr/bin/env python3
"""Report measured validation costs and compare like-for-like performance samples."""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import math
import os
from pathlib import Path
import statistics
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


def compare(samples, policy, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    groups = {}
    for sample in samples:
        if sample.get("cohort") not in {"baseline", "candidate"} or sample.get("metric") not in set(policy["targets"]) | set(policy.get("observed_metrics", [])):
            raise ci_tests.CIError("unknown performance cohort or metric")
        if not isinstance(sample.get("change_class"), str) or not sample["change_class"]:
            raise ci_tests.CIError("performance sample requires a change class")
        value = sample.get("value")
        if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            raise ci_tests.CIError("performance sample value must be finite and nonnegative")
        age = (now - timestamp(sample["observed_at"])).total_seconds()
        if not 0 <= age <= policy["window_days"] * 86400:
            continue
        key = (sample["change_class"], sample["metric"])
        groups.setdefault(key, {"baseline": [], "candidate": []})[sample["cohort"]].append(value)
    comparisons = []
    for (change_class, metric), cohorts in sorted(groups.items()):
        sufficient = all(len(values) >= policy["minimum_samples_per_cohort"] for values in cohorts.values())
        baseline = statistics.median(cohorts["baseline"]) if cohorts["baseline"] else None
        candidate = statistics.median(cohorts["candidate"]) if cohorts["candidate"] else None
        reduction = 1 - candidate / baseline if baseline and candidate is not None else None
        target = policy["targets"].get(metric, {})
        if not sufficient:
            status = "insufficient_samples"
        elif not target:
            status = "observed"
        elif "maximum_value" in target:
            status = "met" if max(cohorts["candidate"]) <= target["maximum_value"] else "missed"
        else:
            status = "met" if reduction is not None and reduction >= target["minimum_reduction"] else "missed"
        comparisons.append({"change_class": change_class, "metric": metric, "status": status,
            "baseline_count": len(cohorts["baseline"]), "candidate_count": len(cohorts["candidate"]),
            "baseline_median": baseline, "candidate_median": candidate, "reduction": reduction, "target": target})
    return {"schema_version": 1, "authority": "measurement_only", "window_days": policy["window_days"],
            "comparisons": comparisons,
            "missing_metrics": sorted(set(policy["targets"]) - {row["metric"] for row in comparisons})}


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
    summary = commands.add_parser("compare")
    summary.add_argument("--samples", type=Path, required=True)
    summary.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        policy = ci_tests.read_json(args.policy)
        if policy.get("schema_version") != 1:
            raise ci_tests.CIError("unsupported performance policy")
        if args.command == "compare":
            result = compare(ci_tests.read_json(args.samples), policy)
        else:
            jobs = ci_tests.read_json(args.jobs_json) if args.jobs_json else fetch_jobs(args.repository, args.run_id, args.attempt)
            result = {"schema_version": 1, "authority": "measurement_only", "run_id": args.run_id,
                      "run_attempt": args.attempt, "jobs": job_measurements(jobs, policy)}
            if args.durations:
                result["test_measurements"] = ci_tests.read_json(args.durations).get("measurements", [])
        ci_tests.write_json(args.output, result)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path and args.command == "jobs":
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
