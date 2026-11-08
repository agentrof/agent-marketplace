from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
import review_execution as review
import task_lane


class ReviewExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.skills = self.root / "package"
        for name in ("review", "data-design"):
            path = self.skills / name / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_text(f"# {name}\nInspect the current contract and failure paths.\n", encoding="utf-8")
        policy = self.skills / "review/data/review-policy.json"
        policy.parent.mkdir()
        policy.write_bytes((ROOT / "plugins/software-engineering-team/skill-content/review/data/review-policy.json").read_bytes())
        (self.root / "app.py").write_text("value = 1\n", encoding="utf-8")
        (self.root / "task.md").write_text("Preserve the existing value.\n", encoding="utf-8")
        (self.root / "fallback.md").write_text("Core review checked the local source.\n", encoding="utf-8")
        self.head, self.base = "a" * 40, "b" * 40
        self.members = [{"task": "TK-001", "root": str(self.root)}]
        self.records = {"companion/ponytail": {"unavailable_reason": "not installed in this test host",
                        "evidence": "fallback.md"}}
        self.lane = SimpleNamespace(
            Refused=task_lane.Refused, SKILLS=self.skills, REVIEWS=Path(".runtime/review"),
            parse_frontmatter=task_lane.parse_frontmatter, field_items=task_lane.field_items,
            head_state=lambda root: (self.head, True), changed_files=lambda root, base: {"app.py"},
            run=self.git, task_path=lambda root, task: root / "task.md",
            task_props=lambda path: {"title": "Keep the value", "rigor": "normal"},
            task_body=lambda path: path.read_text(encoding="utf-8"),
            required_methods=lambda *args: ["data-design"], read_methods=lambda *args: self.records,
            companion_evidence=lambda *args, **kwargs: [{"companion": "ponytail", "ok": True}],
            proof_path=lambda root, task: root / "proof.json", now=lambda: "2026-11-04T10:00:00Z",
            proof_commands=lambda root, base: ["test-command"],
            proof_plan=lambda root, base, files=None: {"commands": self.lane.proof_commands(root, base)},
            stop_proof=mock.Mock(), wait_process_group=task_lane.wait_process_group,
        )

    def git(self, *args, **kwargs):
        if args[:2] == ("git", "ls-tree"):
            return "app.py\ntask.md\nfallback.md"
        if args[:3] == ("git", "diff", "--name-only"):
            return "app.py"
        if args[:2] == ("git", "diff"):
            return "diff --git a/app.py b/app.py\n+value = 1\n"
        return self.base

    def report(self, prepared):
        return {"head": self.head, "verdict": "approve", "checks": [
            {"source_id": source["source_id"], "tasks": source["tasks"],
             "assessment": "The current value preserves the accepted contract.",
             "citations": [{"path": "app.py", "line": 1, "quote": "value = 1"}]}
            for source in prepared["binding"]["sources"]], "findings": [], "limitations": []}

    def test_review_contract_uses_the_fixed_candidate_impact_plan(self):
        self.lane.proof_plan = mock.Mock(return_value={"commands": ["focused-check"]})
        prepared = review.prepare(self.root, self.members, "main", self.lane)
        self.lane.proof_plan.assert_called_once_with(self.root, "main", files={"app.py"})
        self.assertEqual(prepared["binding"]["proof_contract"]["commands"], ["focused-check"])
        self.lane.proof_plan.return_value = {"commands": ["changed-check"]}
        changed = review.prepare(self.root, self.members, "main", self.lane)
        self.assertNotEqual(prepared["binding"], changed["binding"])

    def test_review_diff_preserves_trailing_lines_without_changing_default_command_results(self):
        output = "diff --git a/app.py b/app.py\n+value = 1\n\n"
        with mock.patch.object(task_lane.subprocess, "run", return_value=SimpleNamespace(
                stdout=output, stderr="", returncode=0)):
            self.assertEqual(task_lane.run("git", "diff"), output.strip())
            self.assertEqual(task_lane.run("git", "diff", strip_output=False), output)
        self.lane.run = mock.Mock(side_effect=self.git)
        prepared = review.prepare(self.root, self.members, "main", self.lane)
        self.lane.run.assert_any_call("git", "diff", f"{self.base}...{self.head}",
                                      cwd=self.root, strip_output=False)
        self.assertTrue(prepared["diff"].endswith("\n"))

    def test_packet_preserves_exact_scoped_sections_and_fails_closed_at_budget(self):
        for companion in review.policy(self.lane)["companions"]:
            for rule in companion["sources"]:
                with self.subTest(companion=companion["id"], source=rule["id"]):
                    source = self.root / "companion.md"
                    source.write_text(f"---\nname: {rule['id']}\n---\n## Unrelated\n" + "x" * 9000
                                      + "".join(f"\n## {heading}\nApply this procedure.\n" for heading in rule["sections"]),
                                      encoding="utf-8")
                    excerpt = review.source_excerpt(source, companion["id"], self.lane)
                    self.assertEqual(excerpt["delivery"], "scoped_sections")
                    self.assertNotIn("x" * 100, excerpt["content"])
                    for heading in rule["sections"]:
                        self.assertIn(f"## {heading}", excerpt["content"])
        source = self.root / "graphify.md"
        source.write_text("---\nname: graphify\n---\n# Graphify\n## Long unrelated procedure\n" + "x" * 9000
                          + "\n## For /graphify query\nQuery the existing graph.\n```text\n## Honesty Rules\n```\n"
                          + "## Honesty Rules\nNever invent an edge.\n", encoding="utf-8")
        excerpt = review.source_excerpt(source, "graphify", self.lane)
        self.assertEqual(excerpt["delivery"], "scoped_sections")
        self.assertNotIn("x" * 100, excerpt["content"])
        self.assertIn("Query the existing graph.", excerpt["content"])
        self.assertIn("Never invent an edge.", excerpt["content"])
        lines = source.read_text().splitlines(keepends=True)
        self.assertEqual(excerpt["content"], "\n".join("".join(lines[start-1:end]) for start, end in excerpt["line_ranges"]))
        source.write_text(source.read_text().replace("## For /graphify query", "## Renamed query"))
        with self.assertRaisesRegex(task_lane.Refused, "missing or ambiguous"):
            review.source_excerpt(source, "graphify", self.lane)
        with mock.patch.object(review, "policy", return_value={"packet_max_bytes": 10, "source_max_bytes": 8192}):
            with self.assertRaisesRegex(task_lane.Refused, "packet exceeds"):
                review.prepare(self.root, self.members, "main", self.lane)

    def test_report_requires_every_source_task_and_current_quote_without_semantic_claim(self):
        prepared = review.prepare(self.root, self.members, "main", self.lane)
        contract = review.schema(prepared)["properties"]
        self.assertEqual(contract["head"]["enum"], [self.head])
        check = contract["checks"]["items"]["properties"]
        self.assertEqual(check["source_id"]["enum"], [row["source_id"] for row in prepared["binding"]["sources"]])
        self.assertEqual(check["tasks"]["items"]["enum"], ["TK-001"])
        valid = self.report(prepared)
        review.validate_report(valid, prepared, self.root, self.lane)
        for mutation in ("head", "missing", "unknown", "untracked", "quote", "line", "empty", "blocking"):
            broken = copy.deepcopy(valid)
            if mutation == "head":
                broken["head"] = f"Candidate {self.head[:12]}: approve."
            elif mutation == "missing":
                broken["checks"].pop()
            elif mutation == "unknown":
                broken["checks"][0]["tasks"] = ["TK-999"]
            elif mutation == "untracked":
                broken["checks"][0]["citations"][0]["path"] = ".agentrof/proof.json"
            elif mutation == "quote":
                broken["checks"][0]["citations"][0]["quote"] = "value = 2"
            elif mutation == "line":
                broken["checks"][0]["citations"][0]["line"] = 2
            elif mutation == "empty":
                broken["checks"][0]["assessment"] = ""
            else:
                broken["findings"] = [{"severity": "major", "path": "app.py", "line": 1,
                                       "quote": "value = 1", "detail": "Observed defect."}]
            with self.subTest(mutation=mutation), self.assertRaises(task_lane.Refused):
                review.validate_report(broken, prepared, self.root, self.lane)

    def test_a_citation_a_few_lines_off_is_moved_to_its_quote_and_a_far_one_is_refused(self):
        (self.root / "app.py").write_text("first = 0\nsecond = 0\nvalue = 1\n" + "x = 0\n" * 6 + "last = 1\n", encoding="utf-8")
        prepared = review.prepare(self.root, self.members, "main", self.lane)
        near = self.report(prepared)
        near["checks"][0]["citations"][0]["line"] = 2
        review.validate_report(near, prepared, self.root, self.lane)
        self.assertEqual(near["checks"][0]["citations"][0]["line"], 3)
        far = self.report(prepared)
        far["checks"][0]["citations"][0].update(line=10, quote="value = 1")
        with self.assertRaises(task_lane.Refused):
            review.validate_report(far, prepared, self.root, self.lane)

    def test_unrelated_base_tip_does_not_invalidate_same_compared_candidate(self):
        original = self.git
        tips = ["c" * 40, "d" * 40]
        packets = []
        for tip in tips:
            def git(*args, **kwargs):
                return tip if args[:3] == ("git", "rev-parse", "origin/main") else original(*args, **kwargs)
            self.lane.run = git
            packets.append(review.prepare(self.root, self.members, "main", self.lane))
        self.assertEqual(packets[0]["binding_sha256"], packets[1]["binding_sha256"])
        self.base = "e" * 40
        changed = review.prepare(self.root, self.members, "main", self.lane)
        self.assertNotEqual(packets[0]["binding_sha256"], changed["binding_sha256"])

    def test_parallel_proof_completion_preserves_review_but_changed_proof_contract_invalidates(self):
        before = review.prepare(self.root, self.members, "main", self.lane)
        self.assertIn("Perform pre-merge review.", before["packet"])
        self.assertEqual(before["binding"]["proof_contract"]["receipts"],
                         [{"task": "TK-001", "path": str(self.root / "proof.json")}])
        (self.root / "proof.json").write_text(json.dumps({"head": self.head, "ok": True}))
        after = review.prepare(self.root, self.members, "main", self.lane)
        self.assertEqual(before["binding_sha256"], after["binding_sha256"])
        self.lane.proof_commands = lambda root, base: ["changed-test-command"]
        changed = review.prepare(self.root, self.members, "main", self.lane)
        self.assertNotEqual(after["binding_sha256"], changed["binding_sha256"])

    def test_unconfirmed_process_cleanup_blocks_another_native_review(self):
        directory = self.root / self.lane.REVIEWS
        directory.mkdir(parents=True)
        self.lane.stop_proof.side_effect = OSError("cannot confirm termination")
        with self.assertRaisesRegex(OSError, "cannot confirm"):
            review.stop_review(SimpleNamespace(pid=1234), directory, self.lane)
        self.assertEqual(json.loads((directory / "cleanup-unconfirmed.json").read_text())["pid"], 1234)
        args = SimpleNamespace(task="TK-001", batch=None, host="codex", base="main")
        with mock.patch.object(review, "run_review") as run, self.assertRaisesRegex(task_lane.Refused, "cleanup is unconfirmed"):
            review.execute(args, self.root, self.lane)
        run.assert_not_called()

    def test_aggregate_deduplicates_procedures_without_widening_member_effects(self):
        source = self.skills / "experience-design/SKILL.md"
        source.parent.mkdir()
        source.write_text("# Experience design\nInspect the changed interface.\n")
        members = [{**self.members[0], "head": "1" * 40},
                   {"task": "TK-002", "root": str(self.root), "head": "2" * 40}]
        original = self.git
        def git(*args, **kwargs):
            if args[:3] == ("git", "diff", "--name-only"):
                return "app.py" if args[-1].endswith("1" * 40) else "page.tsx"
            return original(*args, **kwargs)
        self.lane.run = git
        self.lane.required_methods = lambda root, props, files, body: [
            "experience-design" if "page.tsx" in files else "data-design"]
        packet = review.prepare(self.root, members, "main", self.lane)
        sources = packet["binding"]["sources"]
        shared = next(source for source in sources if source["source_id"].startswith("method/review/"))
        data = next(source for source in sources if source["source_id"].startswith("method/data-design/"))
        interface = next(source for source in sources if source["source_id"].startswith("method/experience-design/"))
        self.assertEqual(shared["tasks"], ["TK-001", "TK-002"])
        self.assertEqual(data["tasks"], ["TK-001"])
        self.assertEqual(interface["tasks"], ["TK-002"])

    def test_review_retains_valid_explicit_internal_method_selection(self):
        source = self.skills / "experience-design/SKILL.md"
        source.parent.mkdir()
        source.write_text("---\nname: experience-design\nexposure: internal\n---\n# Experience\nInspect the UI.\n")
        self.records["experience-design"] = {"load_source": "method_tool", "skill_sha256": review.digest(source.read_bytes())}
        self.lane.required_methods = lambda *args: []
        with mock.patch.object(self.lane, "companion_evidence", wraps=self.lane.companion_evidence) as companions:
            prepared = review.prepare(self.root, self.members, "main", self.lane)
        self.assertIn("experience-design", companions.call_args.args[2])
        self.assertTrue(any(row["source_id"].startswith("method/experience-design/") for row in prepared["binding"]["sources"]))
        source.write_text(source.read_text() + "Changed procedure.\n")
        self.assertIn("experience-design", review.selected_methods(self.records, self.lane))
        refreshed = review.prepare(self.root, self.members, "main", self.lane)
        self.assertTrue(any("Changed procedure." in row["content"] for row in refreshed["binding"]["sources"]))
        self.records["experience-design"]["skill_sha256"] = review.digest(source.read_bytes())
        source.write_text(source.read_text().replace("exposure: internal", "exposure: entry"))
        self.records["experience-design"]["skill_sha256"] = review.digest(source.read_bytes())
        self.assertNotIn("experience-design", review.selected_methods(self.records, self.lane))

    def test_graph_refresh_replays_the_successful_owner_query(self):
        self.records["companion/graphify"] = {"source_path": "installed/SKILL.md", "action": {
            "ok": True, "head": "old", "question": "quantity_label render_item callers", "graph_path": "graph.json",
            "seed": "quantity_label", "context": "call"}}
        args = SimpleNamespace(task="TK-001", batch=None, host="codex", base="main")
        with mock.patch.object(review, "run_graph") as query, \
                mock.patch.object(review, "prepare", side_effect=task_lane.Refused("prepared")), \
                self.assertRaisesRegex(task_lane.Refused, "prepared"):
            review.execute(args, self.root, self.lane)
        self.assertEqual(query.call_args.args[2], "quantity_label render_item callers")
        self.assertEqual(query.call_args.kwargs, {"seed": "quantity_label", "context": "call"})

    def test_native_runner_delivers_packet_and_invalidates_changed_source_artifact_and_membership(self):
        prepared = review.prepare(self.root, self.members, "main", self.lane)
        observed = {"session_id": "native-session", "report": self.report(prepared),
                    "observed_model": None, "observed_effort": None, "usage": {}}
        host = SimpleNamespace(HOST="codex", command=lambda *args: ["readonly-native-host"],
                               parse_output=json.loads, environment=lambda: {"ISOLATED": "1"})
        delivered = []

        def start(command, **kwargs):
            delivered.append(kwargs["stdin"].read().decode("utf-8"))
            kwargs["stdout"].write(json.dumps(observed).encode("utf-8"))
            return SimpleNamespace(pid=43210, wait=lambda timeout: 0)

        args = SimpleNamespace(task="TK-001", batch=None, host="codex", base="main")
        with mock.patch.object(review, "adapter", return_value=host), \
                mock.patch.object(review.model_class, "resolve", return_value={"host": "codex", "model": "pinned"}), \
                mock.patch.object(review.subprocess, "Popen", side_effect=start), \
                mock.patch.object(review.os, "killpg", side_effect=ProcessLookupError):
            result = review.execute(args, self.root, self.lane)
            self.assertTrue(result["ok"])
            self.assertIn("Inspect the current contract", delivered[0])
            self.assertEqual(review.problems(self.root, self.members, "main", self.lane), [])
            record_path = Path(result["record"])
            record = json.loads(record_path.read_text())
            self.assertEqual(record["source_delivery"], "submitted_to_host")
            self.assertEqual(record["application_source"], "reviewer_report")
            self.assertEqual(record["result"]["session_id"], "native-session")
            self.assertEqual(Path(record["diff_path"]).parent, Path(record["packet_path"]).parent)
            self.assertTrue(review.problems(self.root, self.members + [{"task": "TK-002", "root": str(self.root)}], "main", self.lane))
            source = self.skills / "data-design/SKILL.md"
            original = source.read_bytes()
            source.write_bytes(original + b"New required check.\n")
            self.assertTrue(review.problems(self.root, self.members, "main", self.lane))
            source.write_bytes(original)
            artifact = Path(record["diff_path"])
            artifact.write_text("changed diff", encoding="utf-8")
            self.assertTrue(review.problems(self.root, self.members, "main", self.lane))

    def test_native_runner_uses_bounded_policy_timeout_for_each_host_and_cleans_up_expiry(self):
        prepared = review.prepare(self.root, self.members, "main", self.lane)
        observed = {"session_id": "native-session", "report": self.report(prepared)}
        for name in ("codex", "claude"):
            for expired in (False, True):
                with self.subTest(host=name, expired=expired):
                    process = mock.Mock(pid=43210)
                    process.wait.return_value = 0
                    if expired:
                        process.wait.side_effect = subprocess.TimeoutExpired("native", 600)
                    host = SimpleNamespace(HOST=name, command=lambda *args: ["native"],
                                           parse_output=mock.Mock(return_value=observed))

                    def start(command, **kwargs):
                        kwargs["stdout"].write(json.dumps(observed).encode())
                        return process

                    self.lane.stop_proof.reset_mock()
                    args = SimpleNamespace(task="TK-001", batch=None, host=name, base="main")
                    with mock.patch.object(review, "adapter", return_value=host), \
                            mock.patch.object(review.model_class, "resolve", return_value={}), \
                            mock.patch.object(review.subprocess, "Popen", side_effect=start), \
                            mock.patch.object(self.lane, "wait_process_group", return_value=True):
                        result = review.execute(args, self.root, self.lane)
                    process.wait.assert_called_once_with(timeout=600)
                    self.assertEqual(result["ok"], not expired)
                    if expired:
                        self.lane.stop_proof.assert_called_once_with(process)
                        self.assertEqual(result["status"], "failed")
                        self.assertIn("timed out", result["error"])
                        host.parse_output.assert_not_called()
                    else:
                        self.lane.stop_proof.assert_not_called()
                        host.parse_output.assert_called_once()

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_native_cleanup_allows_transient_members_but_never_approves_persistent_or_unknown_groups(self):
        prepared = review.prepare(self.root, self.members, "main", self.lane)
        observed = {"session_id": "native-session", "report": self.report(prepared)}
        host = SimpleNamespace(command=lambda *args: ["native"], parse_output=json.loads)
        args = SimpleNamespace(task="TK-001", batch=None, host="codex", base="main")
        snapshot = {"pgid": 43210, "members": [{"pid": 43211, "state": "Z", "name": "helper"}]}

        def start(command, **kwargs):
            kwargs["stdout"].write(json.dumps(observed).encode())
            return SimpleNamespace(pid=43210, wait=lambda timeout: 0)

        for case in ("transient", "persistent", "unknown", "cleanup_failed"):
            self.lane.stop_proof.reset_mock(side_effect=True)
            if case == "cleanup_failed":
                self.lane.stop_proof.side_effect = task_lane.Refused("cleanup unconfirmed")
            with self.subTest(case=case), mock.patch.object(review, "adapter", return_value=host), \
                    mock.patch.object(review.model_class, "resolve", return_value={}), \
                    mock.patch.object(review.subprocess, "Popen", side_effect=start), \
                    mock.patch.object(self.lane, "wait_process_group", return_value=case == "transient") as wait, \
                    mock.patch.object(review, "process_group_snapshot", return_value=snapshot):
                if case == "unknown":
                    wait.side_effect = PermissionError("inspection unavailable")
                result = review.execute(args, self.root, self.lane)
                wait.assert_called_once_with(43210, 1)
                self.assertEqual(result["ok"], case == "transient")
                if case == "transient":
                    self.lane.stop_proof.assert_not_called()
                else:
                    self.lane.stop_proof.assert_called_once()
                    record = json.loads(Path(result["record"]).read_text())
                    self.assertEqual(record["process_cleanup"], snapshot)
                    self.assertNotIn("result", record)
                    self.assertTrue(review.problems(self.root, self.members, "main", self.lane))
                marker = self.root / self.lane.REVIEWS / "cleanup-unconfirmed.json"
                self.assertEqual(marker.exists(), case == "cleanup_failed")
                if marker.exists():
                    with self.assertRaisesRegex(task_lane.Refused, "cleanup is unconfirmed"):
                        review.execute(args, self.root, self.lane)

    def test_process_snapshot_retains_only_bounded_group_metadata(self):
        rows = [f"{100 + n} 10 10 Z Wed Nov 4 12:00:00 2026 helper" for n in range(33)]
        rows.append("999 20 20 S Wed Nov 4 12:00:00 2026 unrelated")
        with mock.patch.object(review.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="\n".join(rows))) as ps:
            snapshot = review.process_group_snapshot(10)
        self.assertEqual(ps.call_args.args[0], ["ps", "-A", "-o", "pid=,ppid=,pgid=,stat=,lstart=,comm="])
        self.assertEqual(ps.call_args.kwargs["timeout"], 1)
        self.assertTrue(snapshot["truncated"])
        self.assertEqual(len(snapshot["members"]), 32)
        self.assertEqual(snapshot["members"][0], {"pid": 100, "ppid": 10, "state": "Z",
                                                "started_at": "Wed Nov 4 12:00:00 2026", "name": "helper"})
        with mock.patch.object(review.subprocess, "run", side_effect=OSError("unavailable")):
            self.assertEqual(review.process_group_snapshot(10), {"pgid": 10, "observation": "unavailable"})

    def test_native_wait_error_survives_failed_cleanup(self):
        host = SimpleNamespace(command=lambda *args: ["native"], parse_output=json.loads)
        args = SimpleNamespace(task="TK-001", batch=None, host="codex", base="main")
        marker = self.root / self.lane.REVIEWS / "cleanup-unconfirmed.json"
        for error in (subprocess.TimeoutExpired("native", 1), KeyboardInterrupt()):
            process = mock.Mock(pid=43210)
            process.wait.side_effect = error
            self.lane.stop_proof.side_effect = task_lane.Refused("cleanup unconfirmed")
            with self.subTest(error=type(error).__name__), \
                    mock.patch.object(review, "adapter", return_value=host), \
                    mock.patch.object(review.model_class, "resolve", return_value={}), \
                    mock.patch.object(review.subprocess, "Popen", return_value=process):
                if isinstance(error, KeyboardInterrupt):
                    with self.assertRaises(KeyboardInterrupt) as caught:
                        review.execute(args, self.root, self.lane)
                    self.assertIs(caught.exception, error)
                    self.assertIsInstance(error.__cause__, task_lane.Refused)
                else:
                    result = review.execute(args, self.root, self.lane)
                    self.assertFalse(result["ok"])
                    self.assertIn("timed out", result["error"])
                self.assertTrue(marker.exists())
                marker.unlink()

    def test_native_failure_and_candidate_change_never_produce_approval(self):
        prepared = review.prepare(self.root, self.members, "main", self.lane)
        host = SimpleNamespace(command=lambda *args: ["native"], parse_output=json.loads)
        args = SimpleNamespace(task="TK-001", batch=None, host="codex", base="main")
        for changed in (False, True):
            def start(command, **kwargs):
                output = {"session_id": "observed", "report": self.report(prepared)}
                kwargs["stdout"].write(json.dumps(output).encode())
                if changed:
                    (self.root / "task.md").write_text("Changed contract.", encoding="utf-8")
                return SimpleNamespace(pid=43210, wait=lambda timeout: 0 if changed else 1)
            with self.subTest(changed=changed), mock.patch.object(review, "adapter", return_value=host), \
                    mock.patch.object(review.model_class, "resolve", return_value={}), \
                    mock.patch.object(review.subprocess, "Popen", side_effect=start), \
                    mock.patch.object(review.os, "killpg", side_effect=ProcessLookupError):
                result = review.execute(args, self.root, self.lane)
                self.assertFalse(result["ok"])
                if not changed:
                    self.assertIn("normal per-command permission flow", result["error"])
                    self.assertIn("preserving child read-only access", result["error"])
                self.assertTrue(review.problems(self.root, self.members, "main", self.lane))


if __name__ == "__main__":
    unittest.main()
