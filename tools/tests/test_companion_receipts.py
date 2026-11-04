from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
import task_lane  # noqa: E402
import proof_train  # noqa: E402


class CompanionReceiptTests(unittest.TestCase):
    def test_local_load_receipt_is_namespaced_and_binds_completion_to_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            task = root / task_lane.TASKS / "tk-004-change.md"
            task.parent.mkdir(parents=True)
            task.write_text("---\ntype: task\nid: TK-004\n---\n", encoding="utf-8")
            skills = Path(directory) / "package"
            catalog = skills / "setup/data/companions.json"
            catalog.parent.mkdir(parents=True)
            catalog.write_text(json.dumps({"companions": [
                {"id": "ponytail", "recommendation": "required_when_available", "hosts": {"codex": {}, "claude": {}}},
                {"id": "retired", "recommendation": "cleanup-only"},
            ]}), encoding="utf-8")
            method = skills / "data-design/SKILL.md"
            method.parent.mkdir(parents=True)
            method.write_text("# Data design\n", encoding="utf-8")
            source = Path(directory) / "installed/ponytail/SKILL.md"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"---\nname: ponytail\n---\nSmallest complete change.\r\n")
            other = source.parent.parent / "other/SKILL.md"
            other.parent.mkdir()
            other.write_bytes(source.read_bytes())
            evidence = root / "review.md"
            evidence.write_text("Reviewed the affected callers.\n", encoding="utf-8")
            outside = Path(directory) / "outside.md"
            outside.write_text("Outside the project.\n", encoding="utf-8")
            args = SimpleNamespace(task="TK-004", name="ponytail", source=str(source), evidence=None, host="codex")

            def companion(**changes):
                return task_lane.cmd_companion(SimpleNamespace(**{**vars(args), **changes}), root)

            with mock.patch.object(task_lane, "SKILLS", skills), \
                    mock.patch.object(task_lane, "project_root", return_value=root), \
                    mock.patch.object(task_lane, "run", return_value="a" * 40), \
                    mock.patch.object(task_lane, "now", return_value="2026-11-04T10:00:00+00:00"):
                for name in ("unknown", "../data-design", "data-design", "retired"):
                    with self.subTest(name=name), self.assertRaisesRegex(task_lane.Refused, "active companion"):
                        companion(name=name)
                with self.assertRaisesRegex(task_lane.Refused, "local SKILL.md"):
                    companion(source=str(outside))
                with self.assertRaisesRegex(task_lane.Refused, "load this version"):
                    companion(evidence="review.md")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    self.assertEqual(task_lane.main([
                        "--project", str(root), "companion", "--task", "TK-004",
                        "--name", "ponytail", "--host", "codex", "--source", str(source)]), 0)
                loaded = json.loads(output.getvalue())
                self.assertEqual(loaded["content"], source.read_bytes().decode("utf-8"))
                self.assertEqual(loaded["skill_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
                retained = task_lane.read_methods(root, "TK-004")["companion/ponytail"]
                self.assertEqual(retained["content_sha256"], hashlib.sha256(loaded["content"].encode()).hexdigest())
                self.assertEqual(retained["source_scope"], loaded["source_scope"])
                self.assertEqual(loaded["source_path"], str(source.resolve()))
                self.assertEqual(loaded["loaded_at"], "2026-11-04T10:00:00+00:00")
                self.assertEqual(loaded["load_source"], "companion_tool")
                self.assertNotIn("completion_source", loaded)
                self.assertEqual(set(task_lane.read_methods(root, "TK-004")), {"companion/ponytail"})
                self.assertEqual(task_lane.missing_methods(root, "TK-004", ["data-design"]), ["data-design"])
                with self.assertRaisesRegex(task_lane.Refused, "inside the project"):
                    companion(evidence="../outside.md")
                with self.assertRaisesRegex(task_lane.Refused, "load this version"):
                    companion(source=str(other), evidence="review.md")
                completed = companion(evidence="review.md")
                self.assertEqual(completed["completion_source"], "worker_report")
                self.assertEqual(completed["evidence_sha256"], hashlib.sha256(evidence.read_bytes()).hexdigest())
                self.assertNotIn("content", completed)
                self.assertEqual(task_lane.missing_methods(root, "TK-004", ["data-design"]), ["data-design"])
                method_args = SimpleNamespace(task="TK-004", name="data-design", evidence=None)
                task_lane.cmd_method(method_args, root)
                method_args.evidence = "review.md"
                task_lane.cmd_method(method_args, root)
                self.assertEqual(task_lane.missing_methods(root, "TK-004", ["data-design"]), [])
                source.write_text("---\nname: ponytail\n---\nChanged companion.\n", encoding="utf-8")
                with self.assertRaisesRegex(task_lane.Refused, "load this version"):
                    companion(evidence="review.md")
                reloaded = companion()
                self.assertNotIn("completion_source", reloaded)
                records = task_lane.read_methods(root, "TK-004")
                self.assertEqual(set(records), {"companion/ponytail", "data-design"})
                self.assertEqual(records["data-design"]["load_source"], "method_tool")
                receipt = task_lane.methods_path(root, "TK-004")
                receipt.unlink()
                try:
                    receipt.symlink_to(outside)
                except OSError:
                    return  # Symlink creation requires a privilege on some Windows hosts.
                with self.assertRaisesRegex(task_lane.Refused, "symbolic links"):
                    companion()
                self.assertEqual(outside.read_text(encoding="utf-8"), "Outside the project.\n")

    def test_applicable_companions_require_current_use_or_explicit_fallback_at_delivery_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = root / task_lane.TASKS / "tk-004-change.md"
            task.parent.mkdir(parents=True)
            task.write_text("---\ntype: task\nid: TK-004\napproved_at: 2026-11-04T10:00:00Z\n---\n", encoding="utf-8")
            evidence = root / "fallback.md"
            evidence.write_text("Reviewed callers, data boundaries and scoped tests.\n", encoding="utf-8")
            skills = root / "package"
            catalog = skills / "setup/data/companions.json"
            catalog.parent.mkdir(parents=True)
            ids = ["ponytail", "graphify", "impeccable", "security-audit"]
            catalog.write_text(json.dumps({"companions": [
                {"id": name, "recommendation": "required_when_available", "hosts": {"codex": {}, "claude": {}}} for name in ids
            ]}), encoding="utf-8")
            source = root / "installed/SKILL.md"
            source.parent.mkdir()
            source.write_text("---\nname: ponytail\n---\nReview the affected callers.\n", encoding="utf-8")
            args = SimpleNamespace(task="TK-004", name="ponytail", source=None, host="codex",
                                   unavailable="Host exposure is unverified; used core review guidance.", evidence="fallback.md")
            triggers = {"method_skills": [], "risk_flags": [], "reviewers": [], "confirmed_risk_methods": {}}
            with mock.patch.object(task_lane, "SKILLS", skills), \
                    mock.patch.object(task_lane, "task_triggers", return_value=triggers), \
                    mock.patch.object(task_lane, "run", return_value="a" * 40), \
                    mock.patch.object(task_lane, "changed_files", return_value=set()):
                cases = [({}, [], {"ponytail", "graphify"}),
                         ({"task_kind": "knowledge"}, [], set()),
                         ({"task_kind": "knowledge"}, ["experience-design"], {"ponytail", "impeccable"}),
                         ({"task_kind": "knowledge"}, ["solution-design"], {"ponytail", "security-audit"}),
                         ({"task_kind": "knowledge", "risks": "auth"}, [], {"security-audit"})]
                for props, methods, expected in cases:
                    with self.subTest(props=props, methods=methods):
                        rows = task_lane.companion_evidence(root, "TK-004", methods, props)
                        self.assertEqual({row["companion"] for row in rows}, expected)
                        self.assertFalse(any(row["ok"] for row in rows))
                rows = task_lane.companion_evidence(root, "TK-004", [], {"task_kind": "knowledge"}, {"app.py"})
                self.assertEqual({row["companion"] for row in rows}, {"ponytail", "graphify"})
                with mock.patch.object(task_lane, "run", return_value=""):
                    inputs = task_lane.cmd_review_inputs(SimpleNamespace(task="TK-004", base="main"), root)
                self.assertEqual(inputs["missing_companions"], ["ponytail", "graphify"])
                for changes in ({"unavailable": " "}, {"evidence": None}, {"source": str(source)}):
                    with self.assertRaisesRegex(task_lane.Refused, "core fallback evidence"):
                        task_lane.cmd_companion(SimpleNamespace(**{**vars(args), **changes}), root)
                with self.assertRaisesRegex(task_lane.Refused, "host registered"):
                    task_lane.cmd_companion(SimpleNamespace(**{**vars(args), "host": "unregistered"}), root)
                for name in ("ponytail", "graphify"):
                    task_lane.cmd_companion(SimpleNamespace(**{**vars(args), "name": name}), root)
                rows = task_lane.companion_evidence(root, "TK-004", [])
                self.assertTrue(all(row["ok"] and row["state"] == "unavailable" for row in rows))
                self.assertTrue(all(row["host_context"]["enabled"] is None for row in rows))
                self.assertTrue(all(row["completion_source"] == "worker_report" for row in rows))
                loaded = SimpleNamespace(**{**vars(args), "source": str(source), "unavailable": None, "evidence": None})
                with self.assertRaisesRegex(task_lane.Refused, "skill name does not match"):
                    task_lane.cmd_companion(SimpleNamespace(**{**vars(loaded), "name": "security-audit"}), root)
                source.write_text("---\nname: ponytail-help\n---\nHelp only.\n", encoding="utf-8")
                with self.assertRaisesRegex(task_lane.Refused, "skill name does not match"):
                    task_lane.cmd_companion(loaded, root)
                source.write_text("---\nname: ponytail\n---\nReview callers.\n", encoding="utf-8")
                task_lane.cmd_companion(loaded, root)
                self.assertFalse(task_lane.companion_evidence(root, "TK-004", [])[0]["ok"])
                loaded.evidence = "fallback.md"
                task_lane.cmd_companion(loaded, root)
                self.assertTrue(task_lane.companion_evidence(root, "TK-004", [])[0]["ok"])
                with mock.patch.object(task_lane, "only_task_changed", return_value=False):
                    self.assertFalse(task_lane.companion_evidence(root, "TK-004", [], head="b" * 40)[0]["ok"])
                with mock.patch.object(task_lane, "only_task_changed", return_value=True):
                    self.assertFalse(task_lane.companion_evidence(root, "TK-004", [], head="b" * 40)[0]["ok"])
                with mock.patch.object(task_lane, "run", return_value="b" * 40):
                    renewed = task_lane.cmd_companion(loaded, root)
                    self.assertEqual(renewed["evidence_head"], "b" * 40)
                    self.assertTrue(task_lane.companion_evidence(root, "TK-004", [])[0]["ok"])
                source.write_text("# Changed guidance\n", encoding="utf-8")
                self.assertFalse(task_lane.companion_evidence(root, "TK-004", [])[0]["ok"])
                evidence.write_text("Changed fallback evidence.\n", encoding="utf-8")
                self.assertFalse(any(row["ok"] for row in task_lane.companion_evidence(root, "TK-004", [])))

                head = "a" * 40
                remote = json.dumps({"headRefOid": head, "baseRefName": "main", "state": "OPEN"})
                merge = SimpleNamespace(task="TK-004", pr="9", head=head, detail=None,
                                        critical=0, non_critical=0, filed=0)
                with mock.patch.object(task_lane, "lint", return_value=[]), \
                        mock.patch.object(task_lane, "missing_methods", return_value=[]), \
                        mock.patch.object(task_lane, "head_state", return_value=(head, True)), \
                        mock.patch.object(task_lane, "run", return_value=remote) as run:
                    with self.assertRaisesRegex(task_lane.Refused, "companion use or explicit fallback"):
                        task_lane.cmd_open_pr(SimpleNamespace(task="TK-004", base="main"), root)
                    with self.assertRaisesRegex(task_lane.Refused, "companion use or explicit fallback"):
                        task_lane.cmd_merge(merge, root)
                    self.assertFalse(any(call.args[:3] in (("gh", "pr", "comment"), ("gh", "pr", "merge"),
                                                          ("gh", "pr", "create")) for call in run.call_args_list))
                with mock.patch.object(proof_train, "own_root", return_value=root), \
                        self.assertRaisesRegex(task_lane.Refused, "companion use or explicit fallback"):
                    proof_train.add(SimpleNamespace(task="TK-004", pr="9", critical=0, base="main"), root,
                                    task_lane, SimpleNamespace())
                batch = {"id": 1, "status": proof_train.GREEN, "base": "main", "head": head,
                         "base_head": "b" * 40, "commands": [{"command": "check", "exit_code": 0}],
                         "members": [{"task": "TK-004", "head": head, "root": str(root)}]}
                with mock.patch.object(proof_train, "home", return_value=root), \
                        mock.patch.object(proof_train, "check_reviewed_heads"), \
                        mock.patch.object(task_lane, "task_path", return_value=task), \
                        mock.patch.object(task_lane, "head_state", return_value=(head, True)), \
                        mock.patch.object(task_lane, "proof_commands", return_value=["check"]), \
                        mock.patch.object(task_lane, "run", side_effect=["b" * 40 + " refs/heads/main", ""]), \
                        self.assertRaisesRegex(task_lane.Refused, "companion use or explicit fallback"):
                    proof_train.candidate(batch, root, task_lane, "main")


if __name__ == "__main__":
    unittest.main()
