#!/usr/bin/env python3
"""Select fresh CI work; missing reusable evidence always expands coverage."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True, encoding="utf-8").strip()


def choose_mode(event: dict, event_name: str, head: str, directory: Path) -> tuple[str, str]:
    if event_name == "merge_group":
        # The queue commit becomes main unchanged, so it is selected the way a
        # PR merge is: over the complete diff from the queue's base.
        return "impact", event["merge_group"]["base_sha"]
    if event_name == "pull_request":
        return "impact", event["pull_request"]["base"]["sha"]
    if event_name != "push" or os.environ.get("GITHUB_REF") != "refs/heads/main":
        return "full", ""
    evidence = directory / "ci-reuse.json"
    run(sys.executable, "tools/ci_evidence.py", "find", "--expected-sha", head,
        "--output", str(evidence))
    result = json.loads(evidence.read_text(encoding="utf-8"))
    return ("reuse" if result.get("reused") else "full"), ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()
    directory = args.output_directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    event = json.loads(Path(event_path).read_text(encoding="utf-8")) if event_path else {}
    head = run("git", "rev-parse", "HEAD")
    mode, base = choose_mode(event, os.environ.get("GITHUB_EVENT_NAME", ""), head, directory)
    timings = directory / "ci-timings.json"
    run(sys.executable, "tools/ci_evidence.py", "timings", "--output", str(timings))
    command = [sys.executable, "tools/ci_tests.py", "plan", "--mode", mode,
               "--head", head, "--output", str(directory / "ci-plan.json"),
               "--timings", str(timings)]
    if base:
        command += ["--base", base]
    subprocess.run(command, cwd=ROOT, check=True)
    plan = json.loads((directory / "ci-plan.json").read_text(encoding="utf-8"))
    with args.github_output.open("a", encoding="utf-8") as output:
        output.write("matrix=" + json.dumps(plan["matrix"], separators=(",", ":")) + "\n")
        output.write("has_tests=" + str(plan["has_tests"]).lower() + "\n")
        output.write("python=" + plan["python"] + "\n")
        output.write("mode=" + plan["mode"] + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as output:
            output.write(f"## CI scope\n\nMode: `{plan['mode']}`\n\n")
            output.write(f"{plan['selection_reason']}\n\n")
            output.write(f"Selected test cases: {len(plan['selected_ids'])}. ")
            output.write(f"Test jobs: {sum(len(lane['shards']) for lane in plan['lanes'].values())}, "
                         f"worker processes: {sum(len(set(owners)) for lane in plan['lanes'].values() for owners in lane['worker_assignments'])}.\n")
            output.write("\n| Lane | Measured weights | Total tests | Jobs | Workers per job | "
                         "Longest estimated worker (seconds) |\n")
            output.write("| --- | ---: | ---: | ---: | ---: | ---: |\n")
            for name, lane in plan['lanes'].items():
                output.write(f"| {name} | {lane['measured_weights']} | {len(lane['selected_ids'])} | "
                             f"{len(lane['shards'])} | {lane['workers']} | "
                             f"{max(lane['estimated_shard_seconds']):.1f} |\n")
            for source in plan['timing_provenance']['sources']:
                output.write(f"\nTiming source: run {source['run_id']}, attempt {source['run_attempt']}, "
                             f"age {source['age_seconds']} seconds, {source['restored_tests']} weights.\n")
            for reason in plan['timing_provenance']['fallback_reasons']:
                output.write(f"\nTiming fallback: {reason}.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
