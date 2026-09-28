#!/usr/bin/env python3
"""Select fresh CI work; missing reusable evidence always expands coverage."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True, encoding="utf-8").strip()


def release_request(event: dict, repository: str) -> bool:
    pr = event.get("pull_request", {})
    return (
        pr.get("base", {}).get("ref") == "main"
        and pr.get("head", {}).get("ref") == "release/stable"
        and pr.get("head", {}).get("repo", {}).get("full_name") == repository
    )


def release_delta(event: dict, head: str, directory: Path) -> bool:
    pull = event["pull_request"]
    base, release_head = pull["base"]["sha"], pull["head"]["sha"]
    if run("git", "rev-parse", head + "^{tree}") != run("git", "rev-parse", release_head + "^{tree}"):
        return False
    metadata = json.loads(run("git", "show", release_head + ":.release/stable.json"))
    with tempfile.TemporaryDirectory(prefix="ci-release-plan.") as temporary:
        trusted = Path(temporary) / "trusted"
        commands = [
            ["git", "clone", "--shared", "--no-checkout", str(ROOT), str(trusted)],
            ["git", "-C", str(trusted), "checkout", "--detach", base],
            [sys.executable, str(trusted / "tools/release.py"), "verify-release-pr",
             "--base-sha", base, "--head-sha", release_head, "--stable-sha", metadata["stable_base"]],
            [sys.executable, str(trusted / "tools/ci_release.py"), "classify",
             "--base", base, "--head", release_head, "--output", str(directory / "release-delta.json")],
        ]
        for command in commands:
            result = subprocess.run(command, cwd=trusted if trusted.exists() else ROOT,
                                    capture_output=True, timeout=120, check=False)
            if result.returncode:
                return False
    return json.loads((directory / "release-delta.json").read_text(encoding="utf-8")).get("release_only") is True


def choose_mode(event: dict, event_name: str, supplied_candidate: str,
                repository: str, head: str, directory: Path) -> tuple[str, str]:
    base = ""
    if event_name == "pull_request" and not supplied_candidate:
        base = event["pull_request"]["base"]["sha"]
        if not release_request(event, repository):
            return "impact", base
        try:
            release_only = release_delta(event, head, directory)
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
            release_only = False
        if not release_only:
            return "full", base
        expected, proof_mode, selected = base, "prepare", "release"
    elif supplied_candidate or (event_name == "push" and os.environ.get("GITHUB_REF") == "refs/heads/main"):
        expected, proof_mode, selected = head, "main", "reuse"
        if event_name == "pull_request_target":
            proof_mode = "publish"
        elif supplied_candidate:
            proof_mode = "prepare"
    else:
        return "full", base
    evidence = directory / "ci-reuse.json"
    run(sys.executable, "tools/ci_evidence.py", "find", "--expected-sha", expected,
        "--mode", proof_mode, "--output", str(evidence))
    result = json.loads(evidence.read_text(encoding="utf-8"))
    if not result.get("reused"):
        return "full", base
    return selected, base


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
    mode, base = choose_mode(event, os.environ.get("GITHUB_EVENT_NAME", ""),
                             os.environ.get("CI_CANDIDATE_SHA", ""),
                             os.environ.get("GITHUB_REPOSITORY", ""), head, directory)
    timings = directory / "ci-timings.json"
    run(sys.executable, "tools/ci_evidence.py", "timings", "--output", str(timings))
    command = [sys.executable, "tools/ci_tests.py", "plan", "--mode", mode,
               "--head", head, "--output", str(directory / "ci-plan.json"),
               "--timings", str(timings)]
    if base:
        command += ["--base", base]
    subprocess.run(command, cwd=ROOT, check=True)
    plan = json.loads((directory / "ci-plan.json").read_text(encoding="utf-8"))
    apple = any("test_vault_hook." in item for item in plan["selected_ids"])
    with args.github_output.open("a", encoding="utf-8") as output:
        output.write("matrix=" + json.dumps(plan["matrix"], separators=(",", ":")) + "\n")
        output.write("has_tests=" + str(plan["has_tests"]).lower() + "\n")
        output.write("apple_launcher=" + str(apple).lower() + "\n")
        output.write("mode=" + plan["mode"] + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as output:
            output.write(f"## CI scope\n\nMode: `{plan['mode']}`\n\n")
            output.write(f"{plan['selection_reason']}\n\n")
            output.write(f"Selected test cases: {len(plan['selected_ids'])}. ")
            output.write(f"Parallel groups: {sum(len(lane['shards']) for lane in plan['lanes'].values())}.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
