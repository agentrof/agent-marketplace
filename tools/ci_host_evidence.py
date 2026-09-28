#!/usr/bin/env python3
"""Reuse verified checkout host lifecycle evidence, never public install smoke."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

import ci_evidence as evidence


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ".github/workflows/release-hosts.yml"
POLICY = "tools/data/ci-host-policy.json"
CLI_VERSIONS = "tools/data/host-cli-versions.json"
PREFIX = "ci-host-evidence"
FILENAME = PREFIX + ".json"
KIND = "checkout-host-lifecycle"
ERRORS = (evidence.EvidenceError, OSError, subprocess.SubprocessError,
          ValueError, KeyError, TypeError, AttributeError)


def json_at(root: Path, revision: str, path: str) -> dict:
    value = evidence.parse_json(evidence.git(root, "show", f"{revision}:{path}").encode("utf-8"))
    evidence.require(isinstance(value, dict), "host contract must be a JSON object")
    return value


def host_policy(root: Path, revision: str) -> dict:
    policy = json_at(root, revision, POLICY)
    evidence.require(set(policy) == {"schema_version", "runner_os", "python", "node"}
                     and policy.get("schema_version") == 1, "unsupported host runtime policy")
    evidence.require(policy.get("runner_os") in {"macos-latest", "ubuntu-latest", "windows-latest"},
                     "unsupported host operating system")
    evidence.require(isinstance(policy.get("python"), str)
                     and re.fullmatch(r"[0-9]+\.[0-9]+", policy["python"]),
                     "host Python policy must pin major.minor")
    evidence.require(isinstance(policy.get("node"), str)
                     and re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,2}", policy["node"]),
                     "host Node policy is invalid")
    return policy


def host_versions(root: Path, revision: str) -> dict:
    versions = json_at(root, revision, CLI_VERSIONS)
    evidence.require(set(versions) == {"schema_version", "claude_code", "codex"}
                     and versions.get("schema_version") == 1, "unsupported host CLI policy")
    for key in ("claude_code", "codex"):
        evidence.require(isinstance(versions[key], str)
                         and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", versions[key]),
                         "host CLI version must be exact")
    return {key: versions[key] for key in ("claude_code", "codex")}


def contract_hash(root: Path, revision: str) -> str:
    evidence.require(evidence.sha(revision), "host contract requires an exact commit")
    entries = {}
    for record in evidence.git(root, "ls-tree", "-rz", revision).split("\0"):
        if record:
            metadata, path = record.split("\t", 1)
            entries[path] = metadata
    required = {WORKFLOW, POLICY, CLI_VERSIONS, "tools/ci_host_evidence.py",
                "tools/ci_evidence.py", "tools/smoke_plugin_installs.py", "tools/build_distributions.py"}
    evidence.require(required <= set(entries), "revision lacks the trusted host evidence contract")
    paths = sorted(path for path in entries if (
        path in required or path.startswith(("plugins/", "platforms/", "dist/", ".github/workflows/"))
        or path in {"product.json", "package-modes.json", "versions.json", "Makefile"}
        or (path.startswith("tools/") and path.endswith(".py") and "/tests/" not in path)
    ))
    return evidence.digest([[path, entries[path]] for path in paths])


def payload_from_environment() -> dict:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        return {}
    payload = evidence.parse_json(Path(path).read_bytes())
    evidence.require(isinstance(payload, dict), "GitHub event must be an object")
    return payload


def caller_mode(expected_sha: str, repository: str, event: dict, environment: dict) -> str:
    evidence.require(evidence.sha(expected_sha), "host candidate must be an exact commit")
    evidence.require(environment.get("GITHUB_REPOSITORY") == repository, "caller repository differs")
    evidence.require(environment.get("CI_CANDIDATE_SHA") == expected_sha,
                     "standalone host runs must execute fresh lifecycle checks")
    event_name = environment.get("GITHUB_EVENT_NAME")
    if event_name == "workflow_dispatch":
        evidence.require(environment.get("GITHUB_REF") == "refs/heads/main"
                         and environment.get("GITHUB_SHA") == expected_sha,
                         "prepare host reuse requires the dispatched main commit")
        return "prepare"
    evidence.require(event_name == "pull_request_target", "host reuse is unavailable for this event")
    pull = event.get("pull_request", {})
    evidence.require(event.get("action") == "closed" and pull.get("merged") is True
                     and pull.get("merge_commit_sha") == expected_sha
                     and pull.get("base", {}).get("ref") == "main"
                     and pull.get("base", {}).get("repo", {}).get("full_name") == repository
                     and pull.get("head", {}).get("ref") == "release/stable"
                     and pull.get("head", {}).get("repo", {}).get("full_name") == repository,
                     "publish host reuse requires the exact merged same-repository release PR")
    return "publish"


def version_output(command: str) -> str:
    result = subprocess.run([command, "--version"], capture_output=True, text=True,
                            encoding="utf-8", check=False, timeout=30)
    evidence.require(result.returncode == 0, f"cannot observe {command} version")
    versions = re.findall(r"(?<![0-9.])([0-9]+\.[0-9]+\.[0-9]+)(?![0-9.])", result.stdout)
    evidence.require(len(versions) == 1, f"ambiguous {command} version")
    return versions[0]


def runtime_identity() -> dict:
    return {
        "os": platform.system(), "os_release": platform.release(), "machine": platform.machine(),
        "python": platform.python_version(), "python_implementation": platform.python_implementation(),
        "node": version_output("node"), "claude_code": version_output("claude"),
        "codex": version_output("codex"),
    }


def validate_runtime(runtime: dict, policy: dict, versions: dict) -> None:
    systems = {"macos-latest": "Darwin", "ubuntu-latest": "Linux", "windows-latest": "Windows"}
    evidence.require(isinstance(runtime, dict), "host runtime identity is missing")
    evidence.require(runtime.get("os") == systems[policy["runner_os"]], "host operating system differs")
    for key in ("os_release", "machine", "python_implementation"):
        evidence.require(isinstance(runtime.get(key), str) and runtime[key], "host runtime identity is incomplete")
    for key in ("python", "node"):
        value = runtime.get(key)
        evidence.require(isinstance(value, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value),
                         "host runtime version is invalid")
        evidence.require(value.split(".")[:len(policy[key].split("."))] == policy[key].split("."),
                         f"host {key} version differs")
    for key, version in versions.items():
        evidence.require(runtime.get(key) == version, "observed host CLI differs from its pinned version")


def create_receipt(root: Path, candidate_sha: str, event: dict, environment: dict) -> dict:
    repository = environment.get("GITHUB_REPOSITORY", "")
    evidence.GitHub(repository)
    evidence.require(not environment.get("CI_CANDIDATE_SHA"),
                     "called workflows cannot publish reusable host receipts")
    evidence.require(evidence.sha(candidate_sha) and evidence.git(root, "rev-parse", "HEAD") == candidate_sha
                     and environment.get("GITHUB_SHA") == candidate_sha,
                     "host checkout differs from the triggering commit")
    evidence.require(not evidence.git(root, "diff", "--name-only", "HEAD", "--"),
                     "host receipt requires unchanged tracked files")
    event_name = environment.get("GITHUB_EVENT_NAME")
    pull_identity = None
    head_sha = candidate_sha
    if event_name == "pull_request":
        pull = event.get("pull_request", {})
        number = event.get("number")
        evidence.require(evidence.positive(number) and pull.get("base", {}).get("ref") == "main"
                         and pull.get("base", {}).get("repo", {}).get("full_name") == repository
                         and pull.get("head", {}).get("repo", {}).get("full_name") == repository
                         and environment.get("GITHUB_REF") == f"refs/pull/{number}/merge",
                         "host receipts require a same-repository PR to main")
        head_sha = pull["head"]["sha"]
        parents = evidence.git(root, "rev-list", "--parents", "-n", "1", candidate_sha).split()
        evidence.require(parents == [candidate_sha, pull["base"]["sha"], head_sha],
                         "host receipt must exercise the actual PR merge commit")
        pull_identity = {"number": number, "head_sha": head_sha, "head_ref": pull["head"]["ref"],
                         "head_repository": repository, "base_ref": "main", "base_sha": pull["base"]["sha"]}
    else:
        evidence.require(event_name == "workflow_dispatch"
                         and environment.get("GITHUB_REF") == "refs/heads/main",
                         "host receipts require a trusted PR or manual main run")
    run_id = int(environment.get("GITHUB_RUN_ID", "0"))
    attempt = int(environment.get("GITHUB_RUN_ATTEMPT", "0"))
    evidence.require(evidence.positive(run_id) and evidence.positive(attempt), "host run identity is missing")
    policy, versions = host_policy(root, candidate_sha), host_versions(root, candidate_sha)
    runtime = runtime_identity()
    validate_runtime(runtime, policy, versions)
    return {
        "schema_version": 1, "kind": KIND, "channel": "checkout", "repository": repository,
        "run_id": run_id, "run_attempt": attempt, "event": event_name,
        "ref": environment.get("GITHUB_REF"), "head_sha": head_sha, "pull_request": pull_identity,
        "tested_sha": candidate_sha, "tested_tree": evidence.tree(root, candidate_sha),
        "contract_hash": contract_hash(root, candidate_sha), "policy": policy,
        "host_versions": versions, "runtime": runtime,
    }


def find_evidence(root: Path, api: evidence.GitHub, expected_sha: str, event: dict,
                  environment: dict, asserted_mode: str | None = None,
                  now: dt.datetime | None = None) -> dict:
    result = {"schema_version": 1, "kind": KIND, "reused": False, "expected_sha": expected_sha,
              "fresh_required": ["release-proof", "public-channel"], "reason": "fresh host lifecycle required"}
    try:
        mode = caller_mode(expected_sha, api.repository, event, environment)
        evidence.require(asserted_mode in {None, mode}, "host proof mode disagrees with its caller")
        result["lookup_mode"] = mode
        expected_tree = evidence.tree(root, expected_sha)
        contract = contract_hash(root, expected_sha)
        evidence.require(contract_hash(root, evidence.git(root, "rev-parse", "HEAD")) == contract,
                         "current trusted host contract differs from candidate")
        result.update(expected_tree=expected_tree, contract_hash=contract)
        policy, versions = host_policy(root, expected_sha), host_versions(root, expected_sha)
        pulls = api.get(f"commits/{expected_sha}/pulls?per_page=100")
        evidence.require(isinstance(pulls, list) and len(pulls) < 100, "host PR association is incomplete")
        candidates = [pull for pull in pulls if (
            pull.get("merge_commit_sha") == expected_sha and pull.get("merged_at")
            and pull.get("base", {}).get("ref") == "main"
            and pull.get("base", {}).get("repo", {}).get("full_name") == api.repository
            and pull.get("head", {}).get("repo", {}).get("full_name") == api.repository
            and (mode != "publish" or pull.get("head", {}).get("ref") == "release/stable")
        )]
        evidence.require(len(candidates) == 1, "exact merged host PR is missing or ambiguous")
        pull = candidates[0]
        head_sha = pull["head"]["sha"]
        evidence.require(evidence.positive(pull.get("number")) and evidence.sha(head_sha), "invalid host PR identity")
        listing = api.get(f"actions/workflows/release-hosts.yml/runs?event=pull_request&head_sha={head_sha}&per_page=100")
        evidence.require(isinstance(listing, dict) and listing.get("total_count", 101) <= 100,
                         "host run list is incomplete")
        current_run = int(environment.get("GITHUB_RUN_ID", "0"))
        runs = [run for run in listing.get("workflow_runs", []) if (
            run.get("id") != current_run and run.get("path") == WORKFLOW
            and run.get("event") == "pull_request" and run.get("head_sha") == head_sha
        )]
        evidence.require(runs, "no checkout host run matches the merged PR")
        latest = max(runs, key=lambda run: (evidence.timestamp(run.get("created_at")), run.get("id", 0)))
        run = api.get(f"actions/runs/{latest['id']}")
        now = now or dt.datetime.now(dt.timezone.utc)
        evidence.validate_run(run, api.repository, "pull_request", head_sha, now, current_run, workflow=WORKFLOW)
        evidence.require(run.get("head_branch") == pull["head"]["ref"], "host source branch differs")
        receipt, artifact = evidence.read_run_artifact(api, run, PREFIX, FILENAME)
        evidence.require(receipt.get("schema_version") == 1 and receipt.get("kind") == KIND
                         and receipt.get("channel") == "checkout", "artifact is not checkout host evidence")
        for key, expected in {"repository": api.repository, "run_id": run["id"],
                              "run_attempt": run["run_attempt"], "event": "pull_request",
                              "head_sha": head_sha, "tested_tree": expected_tree,
                              "contract_hash": contract, "policy": policy, "host_versions": versions,
                              "ref": f"refs/pull/{pull['number']}/merge"}.items():
            evidence.require(receipt.get(key) == expected, f"host receipt {key} differs")
        identity = receipt.get("pull_request", {})
        evidence.require(identity.get("number") == pull["number"] and identity.get("head_sha") == head_sha
                         and identity.get("head_ref") == pull["head"]["ref"]
                         and identity.get("head_repository") == api.repository and identity.get("base_ref") == "main"
                         and evidence.sha(identity.get("base_sha")), "host receipt PR identity differs")
        tested_sha = receipt.get("tested_sha")
        evidence.require(evidence.sha(tested_sha), "host tested commit is invalid")
        commit = api.get(f"git/commits/{tested_sha}")
        evidence.require(commit.get("sha") == tested_sha and commit.get("tree", {}).get("sha") == expected_tree
                         and [parent.get("sha") for parent in commit.get("parents", [])] ==
                         [identity["base_sha"], head_sha], "host receipt did not exercise the matching PR merge tree")
        validate_runtime(receipt.get("runtime"), policy, versions)
        observed = api.get(f"actions/runs/{run['id']}")
        evidence.require(observed.get("run_attempt") == run["run_attempt"], "host source was rerun during verification")
        evidence.validate_run(observed, api.repository, "pull_request", head_sha, now, current_run, workflow=WORKFLOW)
        result.update(reused=True, reason="successful checkout host lifecycle covers this exact tree",
                      source_run_id=run["id"], source_run_attempt=run["run_attempt"], artifact_id=artifact["id"],
                      receipt_digest=evidence.digest(receipt), tested_sha=tested_sha)
    except ERRORS as exc:
        result["reason"] = f"fresh host lifecycle required: {exc}"
    return result


def recheck(root: Path, api: evidence.GitHub, proof: dict, event: dict, environment: dict,
            now: dt.datetime | None = None) -> dict:
    result = find_evidence(root, api, proof.get("expected_sha"), event, environment,
                           asserted_mode=proof.get("lookup_mode"), now=now)
    keys = ("kind", "expected_sha", "expected_tree", "lookup_mode", "contract_hash", "source_run_id",
            "source_run_attempt", "artifact_id", "receipt_digest", "tested_sha")
    if proof.get("reused") is not True or any(result.get(key) != proof.get(key) for key in keys):
        result.update(reused=False, reason="fresh host lifecycle required: original host proof changed")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--candidate-sha", required=True)
    create.add_argument("--output", type=Path, required=True)
    for name in ("find", "recheck"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--github-output", type=Path)
        if name == "find":
            command.add_argument("--expected-sha", required=True)
            command.add_argument("--mode", choices=("prepare", "publish"))
        else:
            command.add_argument("--proof", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "create":
            result = create_receipt(args.root, args.candidate_sha, payload_from_environment(), dict(os.environ))
        else:
            policy = host_policy(args.root, evidence.git(args.root, "rev-parse", "HEAD"))
            try:
                event = payload_from_environment()
                api = evidence.GitHub(os.environ.get("GITHUB_REPOSITORY", ""))
                if args.command == "find":
                    result = find_evidence(args.root, api, args.expected_sha, event, dict(os.environ), args.mode)
                else:
                    proof = evidence.parse_json(args.proof.read_bytes())
                    result = recheck(args.root, api, proof, event, dict(os.environ))
            except ERRORS as exc:
                result = {"schema_version": 1, "kind": KIND, "reused": False,
                          "fresh_required": ["release-proof", "public-channel"],
                          "reason": f"fresh host lifecycle required: {exc}"}
            result["runtime_policy"] = policy
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if args.command != "create" and args.github_output:
            with args.github_output.open("a", encoding="utf-8") as target:
                target.write(f"reused={str(result['reused']).lower()}\n")
                for key in ("runner_os", "python", "node"):
                    target.write(f"{key}={result['runtime_policy'][key]}\n")
        print(json.dumps(result if args.command != "create" else {
            "artifact_name": f"{PREFIX}-{result['run_id']}-{result['run_attempt']}",
            "tested_sha": result["tested_sha"], "channel": result["channel"],
        }, sort_keys=True))
        return 1 if args.command == "recheck" and not result["reused"] else 0
    except ERRORS as exc:
        print(f"ci-host-evidence: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
