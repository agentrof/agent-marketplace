"""A bounded aggregate review keeps every procedure and task contract intact."""
from __future__ import annotations

import json
import unittest

from tools.tests import test_review_execution as fixtures


class ReviewPacketBudgetTests(unittest.TestCase):
    def test_full_aggregate_preserves_content_and_refuses_one_byte_over_budget(self):
        fixture = fixtures.ReviewExecutionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        lane, root, review = fixture.lane, fixture.root, fixtures.review
        methods = ("analyze", "data-design", "experience-design", "solution-design", "verify", "review")
        source_files = []
        for name in methods:
            path = fixture.skills / name / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((fixtures.ROOT / "plugins/software-engineering-team/skill-content" / name / "SKILL.md").read_bytes())
            source_files.append(path)
        lane.required_methods = lambda *args: list(methods[:-1])
        members = [{"task": f"TK-{number:03d}", "root": str(root), "head": fixture.head} for number in range(1, 13)]
        lane.task_path = lambda root, task: root / f"{task}.md"
        for member in members:
            lane.task_path(root, member["task"]).write_text(
                f"# {member['task']}\n" + "Preserve existing data, verify refusal paths and inspect the current implementation.\n" * 50,
                encoding="utf-8")
        graph_files = {}
        for name, content in (("graph", "{}"), ("executable", "synthetic executable"),
                              ("stdout", "NODE synthetic component"), ("stderr", "")):
            path = root / name
            path.write_text(content, encoding="utf-8")
            graph_files[name] = path
        action = {"ok": True, "exit_code": 0, "cleanup_confirmed": True, "head": fixture.head,
                  "text": graph_files["stdout"].read_text()}
        for name, path in graph_files.items():
            key = "graph_path" if name == "graph" else "executable" if name == "executable" else f"{name}_path"
            action[key] = str(path)
            action[f"{name}_sha256"] = review.digest(path.read_bytes())
        companions = ("ponytail", "graphify", "impeccable", "security-audit")
        fixture.records = {}
        for name in companions:
            path = root / f"{name}.md"
            path.write_text(f"---\nname: {name}\n---\n# {name}\n" +
                            "Inspect authorization, failure paths, persistence and candidate evidence.\n" * 70,
                            encoding="utf-8")
            source_files.append(path)
            fixture.records[f"companion/{name}"] = {
                "source_path": str(path), "evidence": "fallback.md", "action": action}
        lane.read_methods = lambda *args: fixture.records
        lane.companion_evidence = lambda *args, **kwargs: [{"companion": name, "ok": True} for name in companions]

        prepared = review.prepare(root, members, "main", lane)
        self.assertEqual(review.policy(lane)["packet_max_bytes"], 131072)
        self.assertEqual(review.policy(lane)["source_max_bytes"], 8192)
        packet_bytes = len(prepared["packet"].encode("utf-8"))
        self.assertGreater(packet_bytes, 65536)
        self.assertLess(packet_bytes, 131072)
        payload = json.loads(prepared["packet"].split("\n", 2)[2])
        for field, source in (("procedures", "sources"), ("tasks", "tasks"), ("evidence", "evidence"),
                              ("proof_contract", "proof_contract")):
            self.assertEqual(payload[field], json.loads(json.dumps(prepared["binding"][source])))
        self.assertEqual(payload["graph_results"], [{"task": member["task"], "text": action["text"],
                                                   "stdout_path": action["stdout_path"]} for member in members])
        expected_tasks = {member["task"] for member in members}
        sources = prepared["binding"]["sources"]
        self.assertEqual(len(sources), 10)
        self.assertEqual({entry["path"] for entry in sources}, {str(path.resolve()) for path in source_files})
        for entry in sources:
            self.assertEqual(set(entry["tasks"]), expected_tasks)
            self.assertEqual(entry["content"], fixtures.Path(entry["path"]).read_text(encoding="utf-8"))
            self.assertEqual(entry["delivery"], "complete_source")
        for task in prepared["binding"]["tasks"]:
            self.assertEqual(task["contract"], lane.task_path(root, task["task"]).read_text(encoding="utf-8"))
        review.validate_report(fixture.report(prepared), prepared, root, lane)
        self.assertEqual(prepared["binding"]["policy_sha256"], review.digest(review.encoded(review.policy(lane))))

        largest = lane.task_path(root, members[0]["task"])
        with largest.open("a", encoding="utf-8") as file:
            remaining = 131072 - packet_bytes
            file.write("é" * (remaining // 2) + "x" * (remaining % 2))
        boundary = review.prepare(root, members, "main", lane)
        self.assertEqual(len(boundary["packet"].encode("utf-8")), 131072)
        self.assertEqual(boundary["binding"]["tasks"][0]["contract"], largest.read_text(encoding="utf-8"))
        with largest.open("a", encoding="utf-8") as file:
            file.write("x")
        with self.assertRaisesRegex(lane.Refused, "packet exceeds its byte budget"):
            review.prepare(root, members, "main", lane)

        source = source_files[0]
        source.write_text("x" * 8192, encoding="utf-8")
        self.assertEqual(review.source_excerpt(source, None, lane)["content"], "x" * 8192)
        source.write_text("x" * 8193, encoding="utf-8")
        with self.assertRaisesRegex(lane.Refused, "exceeds its source budget"):
            review.source_excerpt(source, None, lane)


if __name__ == "__main__":
    unittest.main()
