"""Checkout host proof reuse must preserve source, tree, runtime and caller trust."""

from __future__ import annotations

import contextlib
import copy
import datetime as dt
import hashlib
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_host_evidence as host
import git_fixture


REPO = "owner/project"
NOW = dt.datetime(2026, 9, 28, 12, tzinfo=dt.timezone.utc)
POLICY = {"schema_version": 1, "runner_os": "macos-latest", "python": "3.14", "node": "24"}
VERSIONS = {"claude_code": "2.1.284", "codex": "0.159.1"}
RUNTIME = {"os": "Darwin", "os_release": "25.0.0", "machine": "arm64", "python": "3.14.1",
           "python_implementation": "CPython", "node": "24.1.0", **VERSIONS}


def archive(payload, filename=host.FILENAME, extra=False, mode=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as output:
        entry = zipfile.ZipInfo(filename)
        if mode is not None:
            entry.external_attr = mode << 16
        output.writestr(entry, json.dumps(payload))
        if extra:
            output.writestr("extra.json", "{}")
    return stream.getvalue()


class FakeGitHub:
    repository = REPO

    def __init__(self, root, receipt, merged):
        self.root = root
        self.receipt = copy.deepcopy(receipt)
        self.pull = {"number": 9, "merge_commit_sha": merged, "merged_at": "2026-09-28T11:30:00Z",
                     "merged": True,
                     "base": {"sha": receipt["pull_request"]["base_sha"], "ref": "main",
                              "repo": {"full_name": REPO}},
                     "head": {"sha": receipt["head_sha"], "ref": "feature/test",
                              "repo": {"full_name": REPO}}}
        self.run = {"id": 123, "run_attempt": 1, "path": host.WORKFLOW, "event": "pull_request",
                    "head_sha": receipt["head_sha"], "head_branch": "feature/test", "status": "completed",
                    "conclusion": "success", "created_at": "2026-09-28T10:00:00Z",
                    "updated_at": "2026-09-28T11:00:00Z",
                    "repository": {"id": 7, "full_name": REPO},
                    "head_repository": {"id": 7, "full_name": REPO}}
        self.runs = [self.run]
        self.pull_list = [self.pull]
        self.second_run = None
        self.run_reads = 0
        self.reads = []
        self.archive_override = None
        self.artifact_changes = {}
        self.commit_changes = {}
        self.extra_artifact = False

    def get(self, path):
        self.reads.append(path)
        if path.startswith("commits/"):
            return copy.deepcopy(self.pull_list)
        if path.startswith("actions/workflows/"):
            return {"total_count": len(self.runs), "workflow_runs": copy.deepcopy(self.runs)}
        if path.startswith("actions/runs/") and "/artifacts?" not in path:
            self.run_reads += 1
            number = int(path.rsplit("/", 1)[1])
            if number != self.run["id"]:
                return copy.deepcopy(next(run for run in self.runs if run["id"] == number))
            return copy.deepcopy(self.second_run if self.run_reads > 1 and self.second_run else self.run)
        if "/artifacts?" in path:
            raw = self.raw("")
            artifact = {"id": 456, "name": "ci-host-evidence-123-1", "expired": False,
                        "size_in_bytes": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                        "workflow_run": {"id": 123, "head_sha": self.run["head_sha"],
                                         "repository_id": 7, "head_repository_id": 7}}
            artifact.update(self.artifact_changes)
            return {"total_count": 2 if self.extra_artifact else 1,
                    "artifacts": [artifact, copy.deepcopy(artifact)] if self.extra_artifact else [artifact]}
        if path.startswith("git/commits/"):
            tested = path.rsplit("/", 1)[1]
            value = {"sha": tested, "tree": {"sha": host.evidence.tree(self.root, tested)},
                     "parents": [{"sha": parent} for parent in
                                 host.evidence.git(self.root, "rev-list", "--parents", "-n", "1", tested).split()[1:]]}
            value.update(self.commit_changes)
            return value
        raise AssertionError(path)

    def raw(self, path):
        return self.archive_override or archive(self.receipt)


class HostEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        git_fixture.init_repository(cls.root)
        cls.git("config", "user.name", "Host evidence tests")
        cls.git("config", "user.email", "test@example.invalid")
        cls.git("config", "core.autocrlf", "false")
        contents = {host.POLICY: json.dumps(POLICY),
                    host.CLI_VERSIONS: json.dumps({"schema_version": 1, **VERSIONS}),
                    host.WORKFLOW: "name: native-host-lifecycle\n",
                    "tools/ci_host_evidence.py": "# host contract\n",
                    "tools/ci_evidence.py": "# shared contract\n",
                    "tools/smoke_plugin_installs.py": "# smoke contract\n",
                    "tools/build_distributions.py": "# build contract\n",
                    "platforms/example/adapter.py": "# adapter contract\n",
                    "dist/example/plugin.txt": "generated package\n",
                    "plugins/example/plugin.txt": "canonical source\n"}
        for path, value in contents.items():
            target = cls.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value, encoding="utf-8")
        cls.git("add", ".")
        cls.git("commit", "-qm", "Base")
        cls.base = cls.git("rev-parse", "HEAD")
        (cls.root / "plugins/example/plugin.txt").write_text("candidate source\n", encoding="utf-8")
        cls.git("add", ".")
        cls.git("commit", "-qm", "Candidate")
        cls.head = cls.git("rev-parse", "HEAD")
        cls.tree = cls.git("rev-parse", "HEAD^{tree}")
        cls.tested = cls.git("commit-tree", cls.tree, "-p", cls.base, "-p", cls.head, "-m", "PR test merge")
        cls.merged = cls.git("commit-tree", cls.tree, "-p", cls.base, "-p", cls.head, "-m", "Merged to main")
        cls.git("checkout", "-q", "--detach", cls.merged)
        cls.receipt = {"schema_version": 1, "kind": host.KIND, "channel": "checkout", "repository": REPO,
                       "run_id": 123, "run_attempt": 1, "event": "pull_request", "ref": "refs/pull/9/merge",
                       "head_sha": cls.head, "tested_sha": cls.tested, "tested_tree": cls.tree,
                       "contract_hash": host.contract_hash(cls.root, cls.tested), "policy": POLICY,
                       "host_versions": VERSIONS, "runtime": RUNTIME,
                       "pull_request": {"number": 9, "head_sha": cls.head, "head_ref": "feature/test",
                                        "head_repository": REPO, "base_ref": "main", "base_sha": cls.base}}

    @classmethod
    def tearDownClass(cls):
        git_fixture.remove_temporary(cls.temporary)

    @classmethod
    def git(cls, *args):
        return host.evidence.git(cls.root, *args)

    def setUp(self):
        self.api = FakeGitHub(self.root, self.receipt, self.merged)
        self.environment = {"GITHUB_REPOSITORY": REPO, "GITHUB_EVENT_NAME": "workflow_dispatch",
                            "GITHUB_REF": "refs/heads/main", "GITHUB_SHA": self.merged,
                            "GITHUB_RUN_ID": "999", "CI_CANDIDATE_SHA": self.merged}

    def find(self, api=None, event=None, environment=None, mode=None):
        return host.find_evidence(self.root, api or self.api, self.merged, event or {},
                                  environment or self.environment, asserted_mode=mode, now=NOW)

    def publish(self):
        self.api.pull["head"]["ref"] = "release/stable"
        self.api.run["head_branch"] = "release/stable"
        self.api.receipt["pull_request"]["head_ref"] = "release/stable"
        self.environment["GITHUB_EVENT_NAME"] = "pull_request_target"
        return {"action": "closed", "pull_request": copy.deepcopy(self.api.pull)}

    @contextlib.contextmanager
    def source_checkout(self, revision=None):
        self.git("checkout", "-q", "--detach", revision or self.tested)
        try:
            yield
        finally:
            self.git("reset", "--hard", "-q")
            self.git("checkout", "-q", "--detach", self.merged)

    def create(self, event=None, environment=None, candidate=None):
        env = {"GITHUB_REPOSITORY": REPO, "GITHUB_EVENT_NAME": "pull_request",
               "GITHUB_REF": "refs/pull/9/merge", "GITHUB_SHA": self.tested,
               "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"}
        env.update(environment or {})
        payload = {"number": 9, "pull_request": copy.deepcopy(self.api.pull)} if event is None else event
        with mock.patch.object(host, "runtime_identity", return_value=copy.deepcopy(RUNTIME)):
            return host.create_receipt(self.root, candidate or self.tested, payload, env)

    def test_successful_pr_tree_covers_exact_prepare_commit(self):
        result = self.find(mode="prepare")
        self.assertTrue(result["reused"], result)
        self.assertNotEqual(self.tested, self.merged)
        self.assertEqual(result["tested_sha"], self.tested)
        self.assertEqual(result["expected_sha"], self.merged)
        self.assertEqual(result["fresh_required"], ["release-proof", "public-channel"])
        self.assertEqual(self.api.run_reads, 2)

    def test_publish_requires_release_pr_context_and_matching_source(self):
        event = self.publish()
        self.assertTrue(self.find(event=event, mode="publish")["reused"])
        for key, value in (("merged", False), ("merge_commit_sha", self.head)):
            changed = copy.deepcopy(event)
            changed["pull_request"][key] = value
            self.assertFalse(self.find(event=changed)["reused"])

    def test_standalone_and_untrusted_callers_never_lookup_evidence(self):
        cases = [{"CI_CANDIDATE_SHA": ""}, {"CI_CANDIDATE_SHA": self.head},
                 {"GITHUB_EVENT_NAME": "pull_request"}, {"GITHUB_EVENT_NAME": "schedule"},
                 {"GITHUB_REF": "refs/heads/other"}, {"GITHUB_SHA": self.head},
                 {"GITHUB_REPOSITORY": "fork/project"}]
        for change in cases:
            with self.subTest(change=change):
                api = FakeGitHub(self.root, self.receipt, self.merged)
                self.assertFalse(self.find(api=api, environment={**self.environment, **change})["reused"])
                self.assertEqual(api.reads, [])

    def test_caller_cannot_select_different_proof_mode(self):
        self.assertFalse(self.find(mode="publish")["reused"])
        self.assertEqual(self.api.reads, [])
        event = self.publish()
        self.assertFalse(self.find(event=event, mode="prepare")["reused"])

    def test_publish_refuses_feature_fork_and_wrong_base(self):
        event = self.publish()
        for side, field, value in (("head", "ref", "feature/test"), ("base", "ref", "stable"),
                                  ("head", "repo", {"full_name": "fork/project"})):
            changed = copy.deepcopy(event)
            changed["pull_request"][side][field] = value
            self.assertFalse(self.find(event=changed)["reused"])

    def test_missing_unmerged_or_ambiguous_pr_falls_back(self):
        for change in ("missing", "unmerged", "ambiguous", "fork", "other_merge"):
            api = FakeGitHub(self.root, self.receipt, self.merged)
            if change == "missing":
                api.pull_list = []
            elif change == "unmerged":
                api.pull["merged_at"] = None
            elif change == "ambiguous":
                api.pull_list.append(copy.deepcopy(api.pull))
            elif change == "fork":
                api.pull["head"]["repo"]["full_name"] = "fork/project"
            else:
                api.pull["merge_commit_sha"] = self.head
            self.assertFalse(self.find(api=api)["reused"], change)

    def test_wrong_workflow_event_branch_or_source_repository_falls_back(self):
        changes = [{"path": host.evidence.WORKFLOW}, {"event": "workflow_dispatch"},
                   {"head_branch": "other"}, {"head_sha": self.base},
                   {"repository": {"id": 7, "full_name": "fork/project"}},
                   {"head_repository": {"id": 8, "full_name": "fork/project"}}]
        for change in changes:
            api = FakeGitHub(self.root, self.receipt, self.merged)
            api.run.update(change)
            self.assertFalse(self.find(api=api)["reused"], change)

    def test_non_success_stale_future_or_current_run_falls_back(self):
        changes = [{"status": "in_progress"}, {"conclusion": "cancelled"}, {"conclusion": "failure"},
                   {"updated_at": "2026-09-26T11:00:00Z"}, {"updated_at": "2026-09-29T11:00:00Z"},
                   {"id": 999}, {"run_attempt": 0}]
        for change in changes:
            api = FakeGitHub(self.root, self.receipt, self.merged)
            api.run.update(change)
            self.assertFalse(self.find(api=api)["reused"], change)

    def test_newer_failed_run_prevents_older_green_reuse(self):
        newer = {**self.api.run, "id": 124, "created_at": "2026-09-28T11:30:00Z", "conclusion": "failure"}
        self.api.runs.append(newer)
        self.assertFalse(self.find()["reused"])

    def test_source_rerun_during_download_invalidates_proof(self):
        self.api.second_run = {**self.api.run, "run_attempt": 2}
        self.assertFalse(self.find()["reused"])

    def test_unit_public_or_changed_contract_receipts_cannot_cover_checkout(self):
        changes = [{"kind": "unit-tests"}, {"channel": "stable"}, {"contract_hash": "f" * 64},
                   {"policy": {**POLICY, "python": "3.9"}}, {"host_versions": {**VERSIONS, "codex": "0.1.0"}},
                   {"run_attempt": 2}, {"ref": "refs/heads/main"}, {"repository": "fork/project"}]
        for change in changes:
            api = FakeGitHub(self.root, self.receipt, self.merged)
            api.receipt.update(change)
            self.assertFalse(self.find(api=api)["reused"], change)

    def test_same_tree_requires_actual_pr_merge_parents(self):
        for change in ({"tested_tree": "a" * 40}, {"tested_sha": self.head},
                       {"pull_request": {**self.receipt["pull_request"], "base_sha": self.head}}):
            api = FakeGitHub(self.root, self.receipt, self.merged)
            api.receipt.update(change)
            self.assertFalse(self.find(api=api)["reused"], change)
        self.api.commit_changes["tree"] = {"sha": "a" * 40}
        self.assertFalse(self.find()["reused"])

    def test_runtime_axes_and_exact_cli_versions_are_bound(self):
        changes = {"os": "Linux", "python": "3.9.25", "node": "22.1.0", "claude_code": "2.1.233",
                   "codex": "0.146.0", "machine": "", "python_implementation": "", "os_release": ""}
        for key, value in changes.items():
            api = FakeGitHub(self.root, self.receipt, self.merged)
            api.receipt["runtime"][key] = value
            self.assertFalse(self.find(api=api)["reused"], key)

    def test_artifact_provenance_digest_expiry_and_uniqueness_are_required(self):
        changes = [{"expired": True}, {"name": "ci-host-evidence-123-2"}, {"digest": "sha256:wrong"},
                   {"size_in_bytes": host.evidence.MAX_BYTES + 1},
                   {"workflow_run": {"id": 123, "head_sha": self.head, "repository_id": 7, "head_repository_id": 8}}]
        for change in changes:
            api = FakeGitHub(self.root, self.receipt, self.merged)
            api.artifact_changes = change
            self.assertFalse(self.find(api=api)["reused"], change)
        self.api.extra_artifact = True
        self.assertFalse(self.find()["reused"])

    def test_malicious_or_unit_archive_is_not_extracted(self):
        for raw in (archive(self.receipt, filename="../ci-host-evidence.json"),
                    archive(self.receipt, filename="ci-evidence.json"), archive(self.receipt, extra=True),
                    archive(self.receipt, mode=stat.S_IFLNK | 0o777), b"not a zip"):
            api = FakeGitHub(self.root, self.receipt, self.merged)
            api.archive_override = raw
            self.assertFalse(self.find(api=api)["reused"])

    def test_api_error_falls_back_to_fresh_lifecycle(self):
        with mock.patch.object(self.api, "get", side_effect=host.evidence.EvidenceError("network unavailable")):
            self.assertFalse(self.find()["reused"])

    def test_recheck_requires_original_source_attempt_and_receipt(self):
        proof = self.find()
        result = host.recheck(self.root, self.api, proof, {}, self.environment, now=NOW)
        self.assertTrue(result["reused"], result)
        for key, value in (("artifact_id", 789), ("receipt_digest", "f" * 64), ("source_run_attempt", 2)):
            changed = {**proof, key: value}
            self.assertFalse(host.recheck(self.root, self.api, changed, {}, self.environment, now=NOW)["reused"])
        self.api.run["conclusion"] = "failure"
        self.assertFalse(host.recheck(self.root, self.api, proof, {}, self.environment, now=NOW)["reused"])

    def test_receipt_creation_records_actual_merge_and_observed_runtime(self):
        with self.source_checkout():
            receipt = self.create()
        self.assertEqual(receipt, self.receipt)

    def test_receipt_creation_refuses_called_workflows_forks_and_wrong_targets(self):
        with self.source_checkout():
            for fields in ({"CI_CANDIDATE_SHA": self.tested}, {"GITHUB_SHA": self.head},
                           {"GITHUB_EVENT_NAME": "schedule"}, {"GITHUB_REF": "refs/pull/8/merge"}):
                with self.assertRaises(host.evidence.EvidenceError):
                    self.create(environment=fields)
            event = {"number": 9, "pull_request": copy.deepcopy(self.api.pull)}
            event["pull_request"]["head"]["repo"]["full_name"] = "fork/project"
            with self.assertRaises(host.evidence.EvidenceError):
                self.create(event=event)

    def test_receipt_creation_refuses_dirty_checkout_and_unmerged_head(self):
        with self.source_checkout():
            (self.root / "plugins/example/plugin.txt").write_text("untracked modification\n", encoding="utf-8")
            with self.assertRaises(host.evidence.EvidenceError):
                self.create()
        with self.source_checkout(self.head):
            with self.assertRaises(host.evidence.EvidenceError):
                self.create(candidate=self.head, environment={"GITHUB_SHA": self.head})

    def test_manual_main_can_create_fresh_receipt_but_cannot_reuse_without_candidate(self):
        with self.source_checkout(self.merged):
            result = self.create(event={}, candidate=self.merged,
                                 environment={"GITHUB_EVENT_NAME": "workflow_dispatch", "GITHUB_REF": "refs/heads/main",
                                              "GITHUB_SHA": self.merged})
        self.assertIsNone(result["pull_request"])
        self.assertEqual(result["event"], "workflow_dispatch")

    def test_contract_binds_tools_adapters_packages_modes_and_runtime_policy(self):
        before = host.contract_hash(self.root, self.merged)
        for path in ("tools/ci_evidence.py", "platforms/example/adapter.py", "dist/example/plugin.txt", host.POLICY):
            with self.source_checkout():
                target = self.root / path
                target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
                self.git("add", path)
                tree = self.git("write-tree")
                revision = self.git("commit-tree", tree, "-p", self.merged, "-m", "Changed contract")
                self.assertNotEqual(before, host.contract_hash(self.root, revision), path)
        with self.source_checkout():
            self.git("update-index", "--chmod=+x", "tools/ci_evidence.py")
            revision = self.git("commit-tree", self.git("write-tree"), "-p", self.merged, "-m", "Changed mode")
            self.assertNotEqual(before, host.contract_hash(self.root, revision))

    def test_changed_current_trusted_contract_cannot_authorize_candidate(self):
        with self.source_checkout():
            target = self.root / "tools/ci_evidence.py"
            target.write_text("# changed trusted verifier\n", encoding="utf-8")
            self.git("add", ".")
            self.git("commit", "-qm", "Verifier changed")
            self.assertFalse(self.find()["reused"])
            self.assertEqual(self.api.reads, [])

    def test_cli_fresh_find_emits_runtime_policy_and_failed_recheck_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            github_output = Path(directory) / "github-output"
            proof = Path(directory) / "proof.json"
            proof.write_text(json.dumps({"reused": False, "expected_sha": self.merged}), encoding="utf-8")
            for command in ("find", "recheck"):
                arguments = ["ci_host_evidence.py", "--root", str(self.root), command, "--output", str(output),
                             "--github-output", str(github_output)]
                arguments += ["--expected-sha", self.merged] if command == "find" else ["--proof", str(proof)]
                env = {**self.environment, "CI_CANDIDATE_SHA": "", "GITHUB_EVENT_PATH": ""}
                with mock.patch.object(sys, "argv", arguments), mock.patch.dict(os.environ, env, clear=True), \
                     mock.patch.object(host.evidence.GitHub, "get") as get, contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(host.main(), 0 if command == "find" else 1)
                    get.assert_not_called()
                self.assertFalse(json.loads(output.read_text(encoding="utf-8"))["reused"])
            outputs = github_output.read_text(encoding="utf-8")
            for line in ("reused=false", "runner_os=macos-latest", "python=3.14", "node=24"):
                self.assertIn(line, outputs)

    def test_version_parser_observes_one_exact_version_and_rejects_ambiguity(self):
        for output, expected in (("v24.1.0\n", "24.1.0"), ("codex-cli 0.147.0\n", "0.147.0"),
                                 ("2.1.234 (Claude Code)\n", "2.1.234")):
            with mock.patch.object(subprocess, "run", return_value=mock.Mock(returncode=0, stdout=output)):
                self.assertEqual(host.version_output("host-cli"), expected)
        for output in ("24", "1.2.3 and 4.5.6"):
            with mock.patch.object(subprocess, "run", return_value=mock.Mock(returncode=0, stdout=output)):
                with self.assertRaises(host.evidence.EvidenceError):
                    host.version_output("host-cli")


if __name__ == "__main__":
    unittest.main()
