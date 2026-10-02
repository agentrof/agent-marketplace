#!/usr/bin/env python3
"""Reuse successful GitHub validation only for the same attested inputs."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import zipfile


WORKFLOW = ".github/workflows/validate.yml"
RECEIPT_NAME = "ci-evidence.json"
MAX_BYTES = 4 * 1024 * 1024
MAX_AGE_HOURS = 24
SHA = re.compile(r"[0-9a-f]{40}\Z")
PROFILES = {"full", "impact", "reuse"}
FRESH_REQUIRED = ["repository-policy", "git-history", "release-proof", "public-channel"]
ROOT = Path(__file__).resolve().parent.parent


class EvidenceError(RuntimeError):
    pass


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def closed_object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError("JSON contains duplicate keys")
        result[key] = value
    return result


def parse_json(raw: bytes) -> object:
    if len(raw) > MAX_BYTES:
        raise EvidenceError("evidence JSON exceeds the size limit")
    try:
        return json.loads(raw, object_pairs_hook=closed_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("invalid evidence JSON") from exc


def require(condition: object, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def positive(value: object) -> bool:
    return type(value) is int and value > 0


def sha(value: object) -> bool:
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def timestamp(value: object) -> dt.datetime:
    require(isinstance(value, str), "timestamp is missing")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError("timestamp is invalid") from exc
    require(parsed.tzinfo is not None, "timestamp must include its timezone")
    return parsed.astimezone(dt.timezone.utc)


def git(root: Path, *args: str) -> str:
    import release
    completed = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        check=False, timeout=30, env=release.hermetic_git_environment(),
    )
    if completed.returncode:
        raise EvidenceError("cannot resolve local Git evidence")
    return completed.stdout.strip()


def tree(root: Path, revision: str) -> str:
    require(sha(revision), "expected revision must be an exact commit SHA")
    return git(root, "rev-parse", f"{revision}^{{tree}}")


def contract_hash(root: Path, revision: str) -> str:
    """Hash the trusted emitter and its test selection/coverage contract."""
    require(sha(revision), "contract revision must be an exact commit SHA")
    files = git(root, "ls-tree", "-r", "--name-only", revision).splitlines()
    selected = sorted(path for path in files if (
        path == "Makefile" or path.startswith(".github/workflows/")
        or (path.startswith("tools/ci_") and path.endswith(".py"))
        or (path.startswith("tools/data/ci-") and path.endswith(".json"))
        or path == "tools/data/host-cli-versions.json"
    ))
    require(WORKFLOW in selected and "tools/ci_evidence.py" in selected,
            "revision lacks the trusted evidence contract")
    records = []
    for path in selected:
        records.append([path, git(root, "ls-tree", revision, "--", path)])
    return digest(records)


def test_tools():
    try:
        from tools import ci_tests
    except ModuleNotFoundError:
        import ci_tests
    return ci_tests


@contextmanager
def trusted_checkout(root: Path, revision: str):
    import release
    require(sha(revision), "trusted checkout requires an exact commit SHA")
    with tempfile.TemporaryDirectory(prefix="ci-evidence.") as temporary:
        trusted = Path(temporary) / "trusted"
        for command in (
            ["git", "clone", "--shared", "--no-checkout", str(root), str(trusted)],
            ["git", "-C", str(trusted), "-c", "core.autocrlf=false", "-c", "core.eol=lf",
             "checkout", "--detach", revision],
        ):
            completed = subprocess.run(command, capture_output=True, check=False,
                                       timeout=60, env=release.hermetic_git_environment())
            require(completed.returncode == 0, "cannot prepare trusted evidence checkout")
        yield trusted


def verify_plan_contract(root: Path, plan: dict, expected_sha: str) -> None:
    tools = test_tools()
    # Content equality allows the local commit to replace a synthetic merge
    # object that GitHub no longer advertises. The original diff base stays bound.
    local_plan = dict(plan, source_sha=expected_sha)
    local_plan["plan_hash"] = digest({key: value for key, value in local_plan.items() if key != "plan_hash"})
    try:
        if git(root, "rev-parse", "HEAD^{tree}") == plan["source_tree"]:
            tools.validate_plan(local_plan, root)
        else:
            with trusted_checkout(root, expected_sha) as trusted:
                tools.validate_plan(local_plan, trusted)
    except (ValueError, RuntimeError) as exc:
        raise EvidenceError("source coverage no longer matches the trusted test policy") from exc


def unpack_receipt(raw: bytes, filename: str = RECEIPT_NAME) -> dict:
    require(len(raw) <= MAX_BYTES, "artifact exceeds the size limit")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            require(len(entries) == 1, "artifact must contain exactly one receipt")
            entry = entries[0]
            mode = entry.external_attr >> 16
            require(entry.filename == filename and not entry.is_dir(),
                    "artifact has an unexpected path")
            require(stat.S_IFMT(mode) in {0, stat.S_IFREG}, "artifact entry is not a regular file")
            require(not entry.flag_bits & 1, "encrypted artifacts are unsupported")
            require(0 < entry.file_size <= MAX_BYTES, "receipt exceeds the size limit")
            result = parse_json(archive.read(entry))
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as exc:
        raise EvidenceError("invalid evidence archive") from exc
    require(isinstance(result, dict), "receipt must be an object")
    return result


class GitHub:
    def __init__(self, repository: str):
        require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository),
                "repository identity is invalid")
        self.repository = repository

    def raw(self, endpoint: str) -> bytes:
        completed = subprocess.run(
            ["gh", "api", endpoint], capture_output=True, check=False, timeout=30,
        )
        # New gh versions guard terminal escapes even when binary output is
        # captured. Retry only that guard, keeping older runner CLIs compatible.
        if completed.returncode and b"escape sequences" in completed.stderr.lower():
            completed = subprocess.run(
                ["gh", "api", "--allow-escape-sequences", endpoint],
                capture_output=True, check=False, timeout=30,
            )
        require(completed.returncode == 0, "GitHub evidence lookup failed")
        require(len(completed.stdout) <= MAX_BYTES, "GitHub response exceeds the size limit")
        return completed.stdout

    def get(self, suffix: str) -> object:
        return parse_json(self.raw(f"repos/{self.repository}/{suffix}"))


def artifact_name(run_id: int, attempt: int) -> str:
    return f"ci-evidence-{run_id}-{attempt}"


def validate_plan(plan: object, expected_tree: str) -> dict:
    require(isinstance(plan, dict), "receipt has no test plan")
    require(plan.get("schema_version") == 1, "unsupported test plan schema")
    require(plan.get("source_tree") == expected_tree, "test plan tree differs")
    require(plan.get("mode") in PROFILES, "test plan profile is invalid")
    require(sha(plan.get("source_sha")), "test plan revision is invalid")
    for key in ("policy_hash", "inventory_hash"):
        require(isinstance(plan.get(key), str) and
                re.fullmatch(r"[0-9a-f]{64}", plan[key]), f"test plan {key} is invalid")
    lanes = plan.get("lanes")
    require(isinstance(lanes, dict), "test plan has no lane coverage")
    selected = plan.get("selected_ids")
    require(isinstance(selected, list) and all(isinstance(item, str) for item in selected)
            and len(selected) == len(set(selected)), "test selection is invalid")
    all_ids: set[str] = set()
    for lane in lanes.values():
        require(isinstance(lane, dict), "test lane is invalid")
        ids = lane.get("selected_ids")
        shards = lane.get("shards")
        require(isinstance(ids, list) and all(isinstance(item, str) for item in ids)
                and len(ids) == len(set(ids)), "lane test selection is invalid")
        require(isinstance(shards, list) and all(isinstance(part, list) for part in shards),
                "lane shards are invalid")
        flattened = [item for part in shards for item in part]
        require(all(isinstance(item, str) for item in flattened)
                and sorted(flattened) == sorted(ids), "lane shards do not cover exactly its tests")
        require(isinstance(lane.get("os"), str) and isinstance(lane.get("python"), str),
                "lane runtime contract is missing")
        all_ids.update(ids)
    require(all_ids == set(selected), "selected tests and lane coverage differ")
    require(bool(selected) == plan.get("has_tests"), "test presence flag differs")
    return plan


def validate_run(run: dict, repository: str, event: str, head_sha: str,
                 now: dt.datetime, current_run: int = 0, *, workflow: str = WORKFLOW) -> None:
    require(positive(run.get("id")) and run["id"] != current_run, "invalid source run")
    require(positive(run.get("run_attempt")), "source attempt is missing")
    require(run.get("path") == workflow, "source workflow differs")
    require(run.get("repository", {}).get("full_name") == repository,
            "source repository differs")
    require(run.get("head_repository", {}).get("full_name") == repository,
            "fork evidence is forbidden")
    require(run.get("event") == event and run.get("head_sha") == head_sha,
            "source event or head differs")
    require(run.get("status") == "completed" and run.get("conclusion") == "success",
            "source validation is not successful")
    age = now - timestamp(run.get("updated_at"))
    require(dt.timedelta(0) <= age <= dt.timedelta(hours=MAX_AGE_HOURS),
            "source validation is stale or future-dated")


def read_run_artifact(api: GitHub, run: dict, prefix: str, filename: str) -> tuple[dict, dict]:
    listing = api.get(f"actions/runs/{run['id']}/artifacts?per_page=100")
    require(isinstance(listing, dict) and listing.get("total_count", 101) <= 100,
            "artifact list is incomplete")
    wanted = f"{prefix}-{run['id']}-{run['run_attempt']}"
    matches = [item for item in listing.get("artifacts", []) if item.get("name") == wanted]
    require(len(matches) == 1, "current attempt has missing or duplicate receipts")
    artifact = matches[0]
    require(positive(artifact.get("id")) and artifact.get("expired") is False,
            "receipt artifact is unavailable")
    require(type(artifact.get("size_in_bytes")) is int and
            0 < artifact["size_in_bytes"] <= MAX_BYTES, "artifact size is invalid")
    provenance = artifact.get("workflow_run", {})
    require(provenance.get("id") == run["id"] and
            provenance.get("head_sha") == run["head_sha"], "artifact provenance differs")
    repository_id = run.get("repository", {}).get("id")
    require(positive(repository_id) and provenance.get("repository_id") == repository_id
            and provenance.get("head_repository_id") == repository_id,
            "artifact repository provenance differs")
    raw = api.raw(f"repos/{api.repository}/actions/artifacts/{artifact['id']}/zip")
    expected_digest = artifact.get("digest")
    if expected_digest:
        require(expected_digest == "sha256:" + hashlib.sha256(raw).hexdigest(),
                "artifact download digest differs")
    return unpack_receipt(raw, filename), artifact


def read_run_receipt(api: GitHub, run: dict) -> tuple[dict, dict]:
    return read_run_artifact(api, run, "ci-evidence", RECEIPT_NAME)


def validate_timings(payload: dict, policy: dict, known_ids: list[str]) -> dict:
    require(payload.get("schema_version") == 1 and payload.get("policy_hash") == digest(policy),
            "timing policy differs")
    durations = payload.get("durations")
    runtimes = payload.get("runtimes")
    require(isinstance(durations, dict) and isinstance(runtimes, dict)
            and set(durations) == set(runtimes), "timing runtime coverage differs")
    tools = test_tools()
    normalized = {}
    for lane, values in durations.items():
        require(lane in policy["lanes"] and isinstance(values, dict), "unknown timing lane")
        lane_policy = policy["lanes"][lane]
        tools.validate_runtime(runtimes[lane], lane_policy)
        permitted = set(known_ids if lane_policy.get("groups") == ["all"] else
                        tools.group_ids(lane_policy["groups"], policy, known_ids))
        require(set(values) <= permitted, "timings name tests outside the current lane")
        normalized[lane] = {}
        for test_id, seconds in values.items():
            require(type(seconds) in {int, float} and math.isfinite(seconds)
                    and 0 <= seconds <= 600, "timing duration is outside the accepted range")
            # Six-decimal reports can round an instant skip to zero. Match the
            # sharder floor without discarding the run's other measured tests.
            normalized[lane][test_id] = max(float(seconds), 0.001)
    return normalized


def restore_timings(root: Path, api: GitHub, now: dt.datetime | None = None) -> dict:
    """Restore bounded performance hints; these never grant test coverage."""
    result: dict = {"schema_version": 1, "durations": {}, "sources": [], "fallback_reasons": []}
    now = now or dt.datetime.now(dt.timezone.utc)
    try:
        tools = test_tools()
        policy = tools.policy_at(root)
        known_ids, _ = tools.inventory(root)
        listing = api.get("actions/workflows/validate.yml/runs?status=success&per_page=5")
        require(isinstance(listing, dict) and isinstance(listing.get("workflow_runs"), list),
                "timing workflow list is invalid")
        runs = sorted(listing["workflow_runs"][:5],
                      key=lambda item: (timestamp(item.get("created_at")), item.get("id", 0)),
                      reverse=True)
        for candidate in runs:
            try:
                require(isinstance(candidate, dict) and positive(candidate.get("id")),
                        "timing source run is invalid")
                run = api.get(f"actions/runs/{candidate['id']}")
                require(isinstance(run, dict) and run.get("event") in {
                    "push", "pull_request", "workflow_dispatch", "schedule", "merge_group",
                }, "timing source event is invalid")
                validate_run(run, api.repository, run["event"], run["head_sha"], now)
                payload, artifact_digest = read_run_artifact(api, run, "ci-durations", "ci-durations.json")
                durations = validate_timings(payload, policy, known_ids)
                observed = api.get(f"actions/runs/{run['id']}")
                require(isinstance(observed, dict) and observed.get("run_attempt") == run["run_attempt"],
                        "timing source was rerun during verification")
                validate_run(observed, api.repository, run["event"], run["head_sha"], now)
                restored = 0
                for lane, values in durations.items():
                    for test_id, seconds in values.items():
                        lane_values = result["durations"].setdefault(lane, {})
                        if test_id not in lane_values:
                            lane_values[test_id] = seconds
                            restored += 1
                result["sources"].append({"run_id": run["id"], "run_attempt": run["run_attempt"],
                    "created_at": run["created_at"], "age_seconds": max(0, int((now - timestamp(run["created_at"])).total_seconds())),
                    "artifact_digest": artifact_digest, "restored_tests": restored})
            except (EvidenceError, OSError, subprocess.SubprocessError, ValueError,
                    KeyError, TypeError, AttributeError) as error:
                result["fallback_reasons"].append(str(error))
                continue
    except (EvidenceError, OSError, subprocess.SubprocessError, ValueError,
            KeyError, TypeError, AttributeError) as error:
        result["fallback_reasons"].append(str(error))
    if not result["durations"]:
        result["fallback_reasons"].append("no valid measured history; policy estimates used")
    return result


def validate_receipt(receipt: dict, run: dict, expected_tree: str,
                     contract: str, pull: dict, api: GitHub) -> dict:
    require(receipt.get("schema_version") == 1, "unsupported receipt schema")
    require(receipt.get("repository") == api.repository, "receipt repository differs")
    require(receipt.get("run_id") == run["id"] and
            receipt.get("run_attempt") == run["run_attempt"], "receipt attempt differs")
    require(receipt.get("event") == run["event"] and
            receipt.get("head_sha") == run["head_sha"], "receipt source event differs")
    require(receipt.get("contract_hash") == contract, "test or workflow contract changed")
    require(receipt.get("tested_tree") == expected_tree, "tested tree differs")
    tested_sha = receipt.get("tested_sha")
    require(sha(tested_sha), "tested commit is invalid")
    commit = api.get(f"git/commits/{tested_sha}")
    require(isinstance(commit, dict) and commit.get("sha") == tested_sha and
            commit.get("tree", {}).get("sha") == expected_tree,
            "tested commit does not contain the expected tree")
    require(run.get("head_branch") == pull["head"]["ref"], "source PR branch differs")
    require(receipt.get("pull_request") == {
        "number": pull["number"], "head_sha": pull["head"]["sha"],
        "head_repository": api.repository, "base_ref": "main",
    }, "receipt PR identity differs")
    parents = [parent.get("sha") for parent in commit.get("parents", [])]
    require(len(parents) == 2 and parents[1] == run["head_sha"],
            "receipt did not test the PR merge commit")
    require(receipt.get("ref") == f"refs/pull/{pull['number']}/merge",
            "receipt did not test the PR merge ref")
    plan = validate_plan(receipt.get("plan"), expected_tree)
    require(plan["source_sha"] == tested_sha, "plan did not test the attested commit")
    require(receipt.get("plan_hash") == plan.get("plan_hash") and
            plan.get("plan_hash") == digest({key: value for key, value in plan.items() if key != "plan_hash"}),
            "plan hash differs")
    runtimes = receipt.get("runtimes")
    require(isinstance(runtimes, dict) and set(runtimes) == set(plan["lanes"]),
            "runtime evidence does not cover the plan")
    for name, lane in plan["lanes"].items():
        runtime = runtimes[name]
        require(isinstance(runtime, dict), "runtime identity is invalid")
        require(str(runtime.get("python_version", "")).startswith(lane["python"] + "."),
                "observed Python version differs")
        wanted_os = {"ubuntu": "Linux", "macos": "Darwin", "windows": "Windows"}
        os_name = lane["os"].split("-", 1)[0]
        require(runtime.get("os") == wanted_os.get(os_name), "observed operating system differs")
    if plan["mode"] == "reuse":
        inherited = receipt.get("inherited")
        require(isinstance(inherited, dict) and inherited.get("reused") is True
                and inherited.get("expected_tree") == expected_tree
                and inherited.get("contract_hash") == contract
                and positive(inherited.get("source_run_id"))
                and inherited.get("source_run_id") != run["id"],
                "reuse profile has no valid inherited proof")
    return plan


def find_evidence(root: Path, api: GitHub, expected_sha: str,
                  now: dt.datetime | None = None, current_run: int = 0) -> dict:
    """Find the merged PR's successful validation of main commit ``expected_sha``."""
    result = {
        "schema_version": 1, "reused": False, "expected_sha": expected_sha,
        "lookup_mode": "main",
        "fresh_required": FRESH_REQUIRED, "reason": "no matching successful validation",
    }
    try:
        expected_tree = tree(root, expected_sha)
        contract = contract_hash(root, expected_sha)
        require(contract_hash(root, git(root, "rev-parse", "HEAD")) == contract,
                "current trusted workflow contract differs from the source")
        result.update(expected_tree=expected_tree, contract_hash=contract)
        now = now or dt.datetime.now(dt.timezone.utc)
        pulls = api.get(f"commits/{expected_sha}/pulls?per_page=100")
        require(isinstance(pulls, list) and len(pulls) < 100, "PR association is incomplete")
        candidates = [item for item in pulls if (
            item.get("merge_commit_sha") == expected_sha and item.get("merged_at")
            and item.get("base", {}).get("ref") == "main"
            and item.get("base", {}).get("repo", {}).get("full_name") == api.repository
            and item.get("head", {}).get("repo", {}).get("full_name") == api.repository
        )]
        require(len(candidates) == 1, "exact merged PR is missing or ambiguous")
        pull = candidates[0]
        require(positive(pull.get("number")) and sha(pull.get("head", {}).get("sha")),
                "merged PR identity is invalid")
        event, head_sha = "pull_request", pull["head"]["sha"]
        listing = api.get(f"actions/workflows/validate.yml/runs?event={event}&head_sha={head_sha}&per_page=100")
        require(isinstance(listing, dict) and listing.get("total_count", 101) <= 100,
                "workflow run list is incomplete")
        candidates = [run for run in listing.get("workflow_runs", []) if (
            run.get("id") != current_run and run.get("event") == event
            and run.get("head_sha") == head_sha and run.get("path") == WORKFLOW
        )]
        require(candidates, "no validation run matches this source")
        latest = max(candidates, key=lambda item: (timestamp(item.get("created_at")), item.get("id", 0)))
        run = api.get(f"actions/runs/{latest['id']}")
        require(isinstance(run, dict), "source run response is invalid")
        validate_run(run, api.repository, event, head_sha, now, current_run)
        receipt, artifact = read_run_receipt(api, run)
        plan = validate_receipt(receipt, run, expected_tree, contract, pull, api)
        verify_plan_contract(root, plan, expected_sha)
        # Re-read after downloading so a concurrent rerun cannot bless its old attempt.
        observed = api.get(f"actions/runs/{run['id']}")
        require(isinstance(observed, dict) and observed.get("run_attempt") == run["run_attempt"],
                "source validation was rerun during verification")
        validate_run(observed, api.repository, event, head_sha, now, current_run)
        result.update(
            reused=True, reason="successful validation covers the identical tested tree",
            source_run_id=run["id"], source_run_attempt=run["run_attempt"],
            artifact_id=artifact["id"], receipt_digest=digest(receipt),
            profile=plan["mode"], plan_hash=receipt["plan_hash"],
            tested_sha=receipt["tested_sha"],
        )
    except (EvidenceError, OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError) as exc:
        result["reason"] = f"full validation required: {exc}"
    return result


def create_receipt(root: Path, plan_path: Path, reports: Path,
                   inherited_path: Path | None = None) -> dict:
    ci_tests = test_tools()
    current_sha = git(root, "rev-parse", "HEAD")
    require(not git(root, "diff", "--name-only", "HEAD", "--"),
            "receipt creation requires unchanged tracked files")
    current_tree = tree(root, current_sha)
    plan = validate_plan(parse_json(plan_path.read_bytes()), current_tree)
    require(plan["source_sha"] == current_sha, "plan is not for this checkout")
    ci_tests.validate_plan(plan, root)
    verified = ci_tests.verify_reports(plan, reports)
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    require(event in {"push", "pull_request", "workflow_dispatch"},
            "unsupported receipt event")
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    GitHub(repository)
    run_id = int(os.environ.get("GITHUB_RUN_ID", "0"))
    attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT", "0"))
    require(positive(run_id) and positive(attempt), "GitHub run identity is missing")
    require(os.environ.get("GITHUB_SHA") == current_sha,
            "checkout differs from the triggering commit")
    receipt = {
        "schema_version": 1, "repository": repository, "run_id": run_id,
        "run_attempt": attempt, "event": event, "ref": os.environ.get("GITHUB_REF", ""),
        "head_sha": current_sha, "tested_sha": current_sha, "tested_tree": current_tree,
        "contract_hash": contract_hash(root, current_sha), "plan": plan, "plan_hash": plan["plan_hash"],
        "runtimes": verified["runtimes"], "pull_request": None, "inherited": None,
    }
    if event == "pull_request":
        payload = parse_json(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
        pull = payload["pull_request"]
        require(pull["head"]["repo"]["full_name"] == repository and pull["base"]["ref"] == "main",
                "receipt requires a same-repository PR to main")
        receipt["head_sha"] = pull["head"]["sha"]
        receipt["pull_request"] = {
            "number": payload["number"], "head_sha": pull["head"]["sha"],
            "head_repository": repository, "base_ref": "main",
        }
    if inherited_path:
        inherited = parse_json(inherited_path.read_bytes())
        require(isinstance(inherited, dict) and inherited.get("reused") is True,
                "inherited validation was not verified")
        require(event == "push" and inherited.get("lookup_mode") == "main",
                "inherited lookup does not match this workflow transition")
        confirmed = find_evidence(root, GitHub(repository), inherited.get("expected_sha"),
                                  current_run=run_id)
        require(confirmed.get("reused") is True and all(
            confirmed.get(key) == inherited.get(key) for key in (
                "expected_sha", "expected_tree", "contract_hash", "source_run_id",
                "source_run_attempt", "artifact_id", "receipt_digest",
            )
        ), "inherited validation changed before receipt creation")
        require(inherited.get("expected_sha") == current_sha
                and inherited.get("expected_tree") == current_tree
                and inherited.get("contract_hash") == receipt["contract_hash"],
                "inherited validation does not cover this exact checkout")
        receipt["inherited"] = inherited
    require(plan["mode"] != "reuse" or receipt["inherited"] is not None,
            "the reuse profile requires inherited evidence")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    find = commands.add_parser("find")
    find.add_argument("--expected-sha", required=True)
    find.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    find.add_argument("--output", type=Path, required=True)
    find.add_argument("--github-output", type=Path)
    create = commands.add_parser("create")
    create.add_argument("--plan", type=Path, required=True)
    create.add_argument("--reports", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--inherited", type=Path)
    timings = commands.add_parser("timings")
    timings.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    timings.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "create":
            value = create_receipt(args.root, args.plan, args.reports, args.inherited)
        elif args.command == "timings":
            try:
                value = restore_timings(args.root, GitHub(args.repository))
            except EvidenceError:
                value = {"schema_version": 1, "durations": {}, "sources": [],
                         "fallback_reasons": ["timing service unavailable; policy estimates used"]}
        else:
            try:
                value = find_evidence(args.root, GitHub(args.repository), args.expected_sha,
                                      current_run=int(os.environ.get("GITHUB_RUN_ID", "0")))
            except (EvidenceError, ValueError) as exc:
                value = {"schema_version": 1, "reused": False, "expected_sha": args.expected_sha,
                         "lookup_mode": "main", "fresh_required": FRESH_REQUIRED,
                         "reason": f"full validation required: {exc}"}
        args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if args.command == "find" and args.github_output:
            with args.github_output.open("a", encoding="utf-8") as target:
                target.write(f"reused={str(value['reused']).lower()}\n")
                target.write(f"source_run_id={value.get('source_run_id', '')}\n")
        displayed = value
        if args.command == "timings":
            displayed = {"restored_lanes": len(value["durations"]),
                         "restored_timings": sum(len(items) for items in value["durations"].values())}
        print(json.dumps(displayed if args.command != "create" else {
            "artifact_name": artifact_name(value["run_id"], value["run_attempt"]),
            "tested_sha": value["tested_sha"], "profile": value["plan"]["mode"],
        }, sort_keys=True))
        return 0
    except (EvidenceError, OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        print(f"ci-evidence: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
