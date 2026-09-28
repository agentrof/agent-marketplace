"""Successful GitHub runs, immutable inputs, and hostile evidence boundaries."""

from __future__ import annotations

import copy
import datetime as dt
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ci_evidence as evidence


REPO = "owner/project"
HEAD = "a" * 40
TESTED = "b" * 40
MERGED = "c" * 40
BASE = "d" * 40
TREE = "e" * 40
CONTRACT = "f" * 64
NOW = dt.datetime(2026, 9, 28, 12, tzinfo=dt.timezone.utc)


def plan(mode="full"):
    selected = ["tools.tests.test_example.Example.test_safe"] if mode != "reuse" else []
    value = {
        "schema_version": 1, "source_sha": TESTED, "source_tree": TREE,
        "policy_hash": "1" * 64, "inventory_hash": "2" * 64,
        "mode": mode, "requested_mode": mode, "base_sha": BASE,
        "changed_paths": [], "selection_reason": "full coverage requested",
        "selected_ids": selected, "lanes": ({"linux": {
            "os": "ubuntu-latest", "python": "3.9", "selected_ids": selected,
            "shards": [selected],
        }} if selected else {}), "has_tests": bool(selected),
    }
    value["plan_hash"] = evidence.digest(value)
    return value


def run():
    return {
        "id": 123, "run_attempt": 1, "path": evidence.WORKFLOW,
        "event": "pull_request", "head_sha": HEAD, "head_branch": "feature/test",
        "status": "completed", "conclusion": "success",
        "created_at": "2026-09-28T10:00:00Z", "updated_at": "2026-09-28T11:00:00Z",
        "repository": {"id": 7, "full_name": REPO},
        "head_repository": {"id": 7, "full_name": REPO},
    }


def pull():
    return {
        "number": 9, "merge_commit_sha": MERGED, "merged_at": "2026-09-28T11:30:00Z",
        "base": {"ref": "main", "repo": {"full_name": REPO}},
        "head": {"sha": HEAD, "ref": "feature/test", "repo": {"full_name": REPO}},
    }


def receipt(mode="full"):
    selected_plan = plan(mode)
    return {
        "schema_version": 1, "repository": REPO, "run_id": 123, "run_attempt": 1,
        "event": "pull_request", "head_sha": HEAD, "ref": "refs/pull/9/merge",
        "tested_sha": TESTED, "tested_tree": TREE, "contract_hash": CONTRACT,
        "plan": selected_plan, "plan_hash": selected_plan["plan_hash"],
        "runtimes": ({"linux": {"python_version": "3.9.25", "os": "Linux"}}
                     if selected_plan["has_tests"] else {}),
        "pull_request": {"number": 9, "head_sha": HEAD,
                         "head_repository": REPO, "base_ref": "main"},
        "inherited": None,
    }


def archive(payload, filename=evidence.RECEIPT_NAME, mode=None, extra=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as output:
        info = zipfile.ZipInfo(filename)
        if mode is not None:
            info.external_attr = mode << 16
        output.writestr(info, json.dumps(payload))
        if extra:
            output.writestr("extra.json", "{}")
    return stream.getvalue()


class FakeGitHub:
    repository = REPO

    def __init__(self, source=None):
        self.run = run()
        self.pull = pull()
        self.receipt = source or receipt()
        self.runs = [self.run]
        self.second_run = None
        self.run_reads = 0
        self.archive_override = None
        self.artifact_changes = {}
        self.extra_artifact = False
        self.commit_changes = {}

    def get(self, path):
        if path.startswith("commits/"):
            return [copy.deepcopy(self.pull)]
        if path.startswith("actions/workflows/"):
            return {"total_count": len(self.runs), "workflow_runs": copy.deepcopy(self.runs)}
        if path == "actions/runs/123":
            self.run_reads += 1
            return copy.deepcopy(self.second_run if self.run_reads > 1 and self.second_run else self.run)
        if "/artifacts?" in path:
            artifact = {
                "id": 456, "name": "ci-evidence-123-1", "expired": False,
                "size_in_bytes": 2048, "workflow_run": {
                    "id": 123, "head_sha": self.run["head_sha"],
                    "repository_id": 7, "head_repository_id": 7,
                },
            }
            artifact.update(self.artifact_changes)
            return {"total_count": 2 if self.extra_artifact else 1,
                    "artifacts": [artifact, copy.deepcopy(artifact)] if self.extra_artifact else [artifact]}
        if path.startswith("git/commits/"):
            value = {"sha": self.receipt["tested_sha"], "tree": {"sha": TREE},
                     "parents": [{"sha": BASE}, {"sha": self.run["head_sha"]}]}
            value.update(self.commit_changes)
            return value
        raise AssertionError(path)

    def raw(self, path):
        return self.archive_override or archive(self.receipt)


class EvidenceReuseTests(unittest.TestCase):
    def find(self, api, mode="main", expected=MERGED):
        with mock.patch.object(evidence, "tree", return_value=TREE), \
             mock.patch.object(evidence, "git", return_value=MERGED), \
             mock.patch.object(evidence, "contract_hash", return_value=CONTRACT), \
             mock.patch.object(evidence, "verify_plan_contract"):
            return evidence.find_evidence(Path("."), api, expected, mode, NOW)

    def test_successful_pr_merge_tree_can_cover_resulting_main_commit(self):
        result = self.find(FakeGitHub())
        self.assertTrue(result["reused"], result)
        self.assertEqual(result["tested_sha"], TESTED)
        self.assertEqual(result["expected_sha"], MERGED)
        self.assertEqual(result["source_run_id"], 123)
        self.assertIn("git-history", result["fresh_required"])
        self.assertIn("public-channel", result["fresh_required"])

    def test_wrong_candidate_tree_does_not_reuse(self):
        for target in ("receipt", "commit"):
            with self.subTest(target=target):
                api = FakeGitHub()
                if target == "receipt":
                    api.receipt["tested_tree"] = "9" * 40
                else:
                    api.commit_changes["tree"] = {"sha": "9" * 40}
                self.assertFalse(self.find(api)["reused"])

    def test_fork_wrong_workflow_event_or_head_cannot_supply_evidence(self):
        changes = [
            {"head_repository": {"full_name": "fork/project"}},
            {"repository": {"id": 7, "full_name": "fork/project"}},
            {"path": ".github/workflows/other.yml"},
            {"event": "workflow_dispatch"}, {"head_sha": "9" * 40},
        ]
        for fields in changes:
            with self.subTest(fields=fields):
                api = FakeGitHub()
                api.run.update(fields)
                self.assertFalse(self.find(api)["reused"])

    def test_unmerged_or_wrong_base_or_other_merge_cannot_supply_evidence(self):
        for field, value in (("merged_at", None), ("merge_commit_sha", HEAD),
                             ("base", {"ref": "stable", "repo": {"full_name": REPO}})):
            api = FakeGitHub()
            api.pull[field] = value
            self.assertFalse(self.find(api)["reused"])

    def test_incomplete_cancelled_failed_or_stale_run_cannot_supply_evidence(self):
        cases = [
            {"status": "in_progress"}, {"conclusion": "failure"}, {"conclusion": "cancelled"},
            {"updated_at": "2026-09-26T11:00:00Z"}, {"updated_at": "2026-09-29T11:00:00Z"},
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                api = FakeGitHub()
                api.run.update(fields)
                self.assertFalse(self.find(api)["reused"])

    def test_newer_failed_run_invalidates_older_green(self):
        api = FakeGitHub()
        newer = dict(api.run, id=124, conclusion="failure", created_at="2026-09-28T11:15:00Z")
        api.runs.append(newer)
        original = api.get
        api.get = lambda path: newer if path == "actions/runs/124" else original(path)
        self.assertFalse(self.find(api)["reused"])

    def test_prior_attempt_and_concurrent_rerun_are_rejected(self):
        api = FakeGitHub()
        api.run["run_attempt"] = 2
        self.assertFalse(self.find(api)["reused"])
        api = FakeGitHub()
        api.second_run = dict(api.run, run_attempt=2, status="in_progress")
        self.assertFalse(self.find(api)["reused"])

    def test_receipt_from_another_attempt_or_contract_is_rejected(self):
        for field, value in (("run_attempt", 2), ("contract_hash", "9" * 64),
                             ("run_id", 124), ("repository", "fork/project")):
            api = FakeGitHub()
            api.receipt[field] = value
            self.assertFalse(self.find(api)["reused"])

    def test_artifact_provenance_expiry_digest_and_uniqueness_are_required(self):
        cases = [
            {"expired": True}, {"size_in_bytes": evidence.MAX_BYTES + 1},
            {"digest": "sha256:" + "0" * 64},
            {"workflow_run": {"id": 999}},
        ]
        for fields in cases:
            api = FakeGitHub()
            api.artifact_changes.update(fields)
            self.assertFalse(self.find(api)["reused"])
        api = FakeGitHub()
        api.extra_artifact = True
        self.assertFalse(self.find(api)["reused"])

    def test_head_checkout_does_not_claim_tested_merge_coverage(self):
        api = FakeGitHub()
        api.commit_changes["parents"] = [{"sha": BASE}]
        self.assertFalse(self.find(api)["reused"])
        api = FakeGitHub()
        api.receipt["ref"] = "refs/heads/feature/test"
        self.assertFalse(self.find(api)["reused"])

    def test_runtime_and_partial_lane_evidence_are_rejected(self):
        for runtimes in ({}, {"linux": {"python_version": "3.14.0", "os": "Linux"}},
                         {"linux": {"python_version": "3.9.25", "os": "Windows"}}):
            api = FakeGitHub()
            api.receipt["runtimes"] = runtimes
            self.assertFalse(self.find(api)["reused"])

    def test_prepare_requires_exact_push_main_not_just_equal_tree(self):
        api = FakeGitHub()
        api.run.update(event="push", head_sha=MERGED, head_branch="main")
        api.receipt.update(event="push", head_sha=MERGED, tested_sha=MERGED,
                           ref="refs/heads/main", pull_request=None)
        api.receipt["plan"]["source_sha"] = MERGED
        api.receipt["plan"]["plan_hash"] = evidence.digest({
            key: value for key, value in api.receipt["plan"].items() if key != "plan_hash"
        })
        api.receipt["plan_hash"] = api.receipt["plan"]["plan_hash"]
        self.assertTrue(self.find(api, "prepare")["reused"])
        api.receipt["tested_sha"] = TESTED
        self.assertFalse(self.find(api, "prepare")["reused"])

    def test_publish_requires_the_selected_release_pr(self):
        api = FakeGitHub()
        self.assertFalse(self.find(api, "publish")["reused"])
        api.pull["head"]["ref"] = "release/stable"
        api.run["head_branch"] = "release/stable"
        self.assertTrue(self.find(api, "publish")["reused"])

    def test_impact_profile_is_preserved_and_cannot_claim_full(self):
        api = FakeGitHub(receipt("impact"))
        result = self.find(api)
        self.assertTrue(result["reused"], result)
        self.assertEqual(result["profile"], "impact")

    def test_reuse_profile_requires_a_different_successful_source_link(self):
        api = FakeGitHub(receipt("reuse"))
        self.assertFalse(self.find(api)["reused"])
        api.receipt["inherited"] = {"reused": True, "expected_tree": TREE,
                                    "contract_hash": CONTRACT, "source_run_id": 122}
        self.assertTrue(self.find(api)["reused"])
        api.receipt["inherited"]["source_run_id"] = 123
        self.assertFalse(self.find(api)["reused"])

    def test_api_failure_falls_back_without_approving_a_skip(self):
        api = FakeGitHub()
        api.get = mock.Mock(side_effect=evidence.EvidenceError("authentication unavailable"))
        result = self.find(api)
        self.assertFalse(result["reused"])
        self.assertIn("full validation required", result["reason"])


class EvidenceArchiveTests(unittest.TestCase):
    def test_valid_single_json(self):
        self.assertEqual(evidence.unpack_receipt(archive({"ok": True})), {"ok": True})

    def test_traversal_symlink_extra_file_corruption_and_oversize_are_rejected(self):
        hostile = [
            archive({}, "../ci-evidence.json"), archive({}, "/ci-evidence.json"),
            archive({}, mode=stat.S_IFLNK | 0o777), archive({}, extra=True), b"not a zip",
            b"x" * (evidence.MAX_BYTES + 1),
        ]
        for payload in hostile:
            with self.subTest(size=len(payload)):
                with self.assertRaises(evidence.EvidenceError):
                    evidence.unpack_receipt(payload)

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaises(evidence.EvidenceError):
            evidence.parse_json(b'{"reused":false,"reused":true}')

    def test_binary_transport_retries_only_an_explicit_escape_guard(self):
        failed = subprocess.CompletedProcess([], 1, b"", b"response contains terminal escape sequences")
        succeeded = subprocess.CompletedProcess([], 0, b"binary zip", b"")
        with mock.patch.object(evidence.subprocess, "run", side_effect=[failed, succeeded]) as process:
            self.assertEqual(evidence.GitHub(REPO).raw("endpoint"), b"binary zip")
        self.assertEqual(process.call_count, 2)
        self.assertIn("--allow-escape-sequences", process.call_args.args[0])
        with mock.patch.object(evidence.subprocess, "run", return_value=succeeded) as process:
            evidence.GitHub(REPO).raw("endpoint")
        self.assertEqual(process.call_count, 1)
        self.assertNotIn("--allow-escape-sequences", process.call_args.args[0])

    def test_authentication_failure_is_not_retried_with_a_different_transport(self):
        failed = subprocess.CompletedProcess([], 1, b"", b"authentication required")
        with mock.patch.object(evidence.subprocess, "run", return_value=failed) as process:
            with self.assertRaises(evidence.EvidenceError):
                evidence.GitHub(REPO).raw("endpoint")
        self.assertEqual(process.call_count, 1)


class ReceiptCreationTests(unittest.TestCase):
    def test_creation_independently_verifies_selection_and_complete_reports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = plan()
            (root / "plan.json").write_text(json.dumps(data), encoding="utf-8")
            fake_tools = mock.Mock()
            fake_tools.verify_reports.return_value = {"runtimes": receipt()["runtimes"]}
            environment = {
                "GITHUB_EVENT_NAME": "push", "GITHUB_REPOSITORY": REPO,
                "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_SHA": TESTED, "GITHUB_REF": "refs/heads/main",
            }
            with mock.patch.object(evidence, "git", side_effect=lambda root, *args: "" if args[0] == "diff" else TESTED), \
                 mock.patch.object(evidence, "tree", return_value=TREE), \
                 mock.patch.object(evidence, "contract_hash", return_value=CONTRACT), \
                 mock.patch.object(evidence, "test_tools", return_value=fake_tools), \
                 mock.patch.dict(os.environ, environment, clear=True):
                result = evidence.create_receipt(root, root / "plan.json", root / "reports")
            fake_tools.validate_plan.assert_called_once_with(data, root)
            fake_tools.verify_reports.assert_called_once_with(data, root / "reports")
            self.assertEqual(result["plan_hash"], data["plan_hash"])

    def test_reuse_creation_rechecks_inherited_run_before_emitting_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = plan("reuse")
            (root / "plan.json").write_text(json.dumps(data), encoding="utf-8")
            inherited = {
                "reused": True, "lookup_mode": "main", "expected_sha": TESTED,
                "expected_tree": TREE, "contract_hash": CONTRACT, "source_run_id": 122,
                "source_run_attempt": 1, "artifact_id": 455, "receipt_digest": "4" * 64,
            }
            (root / "inherited.json").write_text(json.dumps(inherited), encoding="utf-8")
            fake_tools = mock.Mock()
            fake_tools.verify_reports.return_value = {"runtimes": {}}
            environment = {
                "GITHUB_EVENT_NAME": "push", "GITHUB_REPOSITORY": REPO,
                "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
                "GITHUB_SHA": TESTED, "GITHUB_REF": "refs/heads/main",
            }
            with mock.patch.object(evidence, "git", side_effect=lambda root, *args: "" if args[0] == "diff" else TESTED), \
                 mock.patch.object(evidence, "tree", return_value=TREE), \
                 mock.patch.object(evidence, "contract_hash", return_value=CONTRACT), \
                 mock.patch.object(evidence, "test_tools", return_value=fake_tools), \
                 mock.patch.object(evidence, "find_evidence", return_value={"reused": False}) as lookup, \
                 mock.patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(evidence.EvidenceError, "changed before receipt creation"):
                    evidence.create_receipt(root, root / "plan.json", root / "reports", root / "inherited.json")
                lookup.return_value = inherited
                result = evidence.create_receipt(root, root / "plan.json", root / "reports", root / "inherited.json")
            self.assertEqual(result["inherited"], inherited)
            self.assertEqual(lookup.call_count, 2)

    def test_source_plan_is_verified_in_its_own_tree_when_release_tree_differs(self):
        fake_tools = mock.Mock()
        checkout = mock.MagicMock()
        checkout.__enter__.return_value = Path("trusted-source")
        with mock.patch.object(evidence, "test_tools", return_value=fake_tools), \
             mock.patch.object(evidence, "git", return_value="9" * 40), \
             mock.patch.object(evidence, "trusted_checkout", return_value=checkout) as clone:
            evidence.verify_plan_contract(Path("release-candidate"), plan(), BASE)
        clone.assert_called_once_with(Path("release-candidate"), BASE)
        self.assertEqual(fake_tools.validate_plan.call_args.args[1], Path("trusted-source"))

    def test_release_profile_cannot_omit_inherited_main_evidence(self):
        api = FakeGitHub(receipt("release"))
        with mock.patch.object(evidence, "tree", return_value=TREE), \
             mock.patch.object(evidence, "git", return_value=MERGED), \
             mock.patch.object(evidence, "contract_hash", return_value=CONTRACT), \
             mock.patch.object(evidence, "verify_plan_contract"):
            result = evidence.find_evidence(Path("."), api, MERGED, "main", NOW)
        self.assertFalse(result["reused"])
        self.assertIn("no validated main source", result["reason"])


class TimingHistoryTests(unittest.TestCase):
    def setUp(self):
        self.ids = ["tools.tests.test_example.Example.test_safe"]
        self.policy = {"lanes": {"linux": {
            "groups": ["all"], "os": "ubuntu-latest", "python": "3.9",
        }}}
        self.runtime = {
            "os": "Linux", "python_version": "3.9.25", "python_implementation": "CPython",
            "os_release": "6.8", "machine": "x86_64", "git_version": "git version 2.50.0",
        }
        self.payload = {
            "schema_version": 1, "policy_hash": evidence.digest(self.policy),
            "durations": {"linux": {self.ids[0]: 32.5}}, "runtimes": {"linux": self.runtime},
        }

    def source(self, payload=None):
        api = FakeGitHub()
        api.artifact_changes = {"name": "ci-durations-123-1"}
        api.archive_override = archive(payload or self.payload, "ci-durations.json")
        return api

    def restore(self, api):
        actual_tools = evidence.test_tools()
        with mock.patch.object(actual_tools, "policy_at", return_value=self.policy), \
             mock.patch.object(actual_tools, "inventory", return_value=(self.ids, "inventory")):
            return evidence.restore_timings(Path("."), api, NOW)

    def test_matching_successful_timing_artifact_restores_only_duration_hints(self):
        result = self.restore(self.source())
        self.assertEqual(result, {"schema_version": 1, "durations": self.payload["durations"]})
        self.assertNotIn("reused", result)

    def test_policy_runtime_or_unknown_tests_invalidate_timing_history(self):
        changes = [
            {"policy_hash": "9" * 64},
            {"durations": {"other-lane": {self.ids[0]: 1}}},
            {"durations": {"linux": {"unknown.test": 1}}},
            {"runtimes": {"linux": dict(self.runtime, os="Windows")}},
        ]
        for fields in changes:
            with self.subTest(fields=fields):
                payload = dict(self.payload, **fields)
                self.assertEqual(self.restore(self.source(payload))["durations"], {})

    def test_invalid_or_excessive_durations_cannot_influence_shards(self):
        for seconds in (-1, 0, float("inf"), float("nan"), 601, True, "30"):
            with self.subTest(seconds=seconds):
                payload = dict(self.payload, durations={"linux": {self.ids[0]: seconds}})
                self.assertEqual(self.restore(self.source(payload))["durations"], {})

    def test_expired_fork_stale_or_unavailable_history_uses_defaults(self):
        for kind in ("expired", "fork", "stale", "api"):
            with self.subTest(kind=kind):
                api = self.source()
                if kind == "expired":
                    api.artifact_changes["expired"] = True
                elif kind == "fork":
                    api.run["head_repository"] = {"full_name": "fork/project"}
                elif kind == "stale":
                    api.run["updated_at"] = "2026-09-25T12:00:00Z"
                else:
                    api.get = mock.Mock(side_effect=evidence.EvidenceError("no API"))
                self.assertEqual(self.restore(api), {"schema_version": 1, "durations": {}})

    def test_history_lookup_is_bounded_to_five_sources(self):
        api = self.source()
        api.runs = [api.run] * 7
        self.assertEqual(self.restore(api)["durations"], self.payload["durations"])
        self.assertEqual(api.run_reads, 10)

    def test_timing_archive_cannot_write_a_receipt_or_extra_file(self):
        api = self.source()
        api.archive_override = archive(self.payload, "ci-evidence.json")
        self.assertEqual(self.restore(api)["durations"], {})

    def test_recent_partial_history_preserves_older_timings_for_other_tests(self):
        self.ids.append("tools.tests.test_example.Example.test_other")
        api = self.source()
        older = dict(api.run, id=122, created_at="2026-09-28T09:00:00Z")
        api.runs = [older, api.run]
        original = api.get
        api.get = lambda path: older if path == "actions/runs/122" else original(path)
        prior = copy.deepcopy(self.payload)
        prior["durations"]["linux"] = {self.ids[0]: 99, self.ids[1]: 15}
        with mock.patch.object(evidence, "read_run_artifact", side_effect=lambda api, source, prefix, filename:
                               (prior if source["id"] == 122 else self.payload, {})):
            result = self.restore(api)
        self.assertEqual(result["durations"]["linux"], {self.ids[0]: 32.5, self.ids[1]: 15})


if __name__ == "__main__":
    unittest.main()
