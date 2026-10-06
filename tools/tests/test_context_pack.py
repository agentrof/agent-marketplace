"""Context pack (`context_pack` at `role_digest`, #441): a derived, hash-bound
digest of the rules one role applies at one entry. It is deterministic, covers
every rule line of its sources, leaves another role's rules out by id, and a
changed source or an edited pack is refused as stale."""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from tools.tests.levels import integration
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "plugins/software-engineering-team"
sys.path.insert(0, str(PACKAGE / "scripts"))
sys.path.insert(0, str(ROOT / "tools/tests"))
import context_pack  # noqa: E402
import task_inputs  # noqa: E402
from git_fixture import init_repository, remove_temporary  # noqa: E402

# Independent of the extractor: any line with one of these words is a rule line.
RULE_WORDS = re.compile(r"\b(must|never|always|refuses?|required|do not|cannot|shall)\b",
                        re.IGNORECASE)
SAMPLE = [("backlog-plan", "product-owner"), ("backlog-plan", "backlog-reviewer"),
          ("deliver", "code-reviewer"), ("deliver", None), ("requirement", "business-analyst"),
          ("execution-plan", "qa-engineer")]


def call(argv: list[str]) -> tuple[int, dict]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = context_pack.main(argv)
    return code, json.loads(output.getvalue())


def rule_lines(text: str) -> list[int]:
    """Lines outside front matter, headings and code fences that state a rule."""
    lines = text.splitlines()
    skip = 0
    if lines and lines[0].strip() == "---":
        skip = next(index + 1 for index, line in enumerate(lines[1:], start=1)
                    if line.strip() == "---")
    found, fenced = [], False
    for number, line in enumerate(lines, start=1):
        if number <= skip:
            continue
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        # A heading is carried as each unit's section, not as a rule of its own.
        if not fenced and not line.startswith("#") and RULE_WORDS.search(line):
            found.append(number)
    return found


class ContextPackTests(unittest.TestCase):
    def test_build_is_deterministic(self) -> None:
        for entry, role in SAMPLE:
            first = context_pack.build(entry=entry, role=role, mode="review")
            second = context_pack.build(entry=entry, role=role, mode="review")
            self.assertEqual(context_pack.canonical(first), context_pack.canonical(second))
            self.assertNotRegex(json.dumps(first), r"\d{4}-\d{2}-\d{2}T")
            self.assertEqual(first["pack_hash"], context_pack.sha256(context_pack.canonical(
                {key: value for key, value in first.items() if key != "pack_hash"})))

    def test_sources_are_exactly_the_task_required_reads_with_hashes(self) -> None:
        for entry, role in SAMPLE:
            pack = context_pack.build(entry=entry, role=role, mode="review")
            task = task_inputs.manifest(entry=entry, role=role, mode="review")
            self.assertEqual([source["path"] for source in pack["sources"]], task["required_reads"])
            self.assertEqual([link["path"] for link in pack["conditional_reads"]],
                             [read["path"] for read in task["conditional_reads"]])
            for record in [*pack["sources"], *pack["conditional_reads"]]:
                self.assertEqual(record["sha256"], context_pack.sha256(
                    (PACKAGE / record["path"]).read_bytes()))

    def test_every_source_rule_line_has_a_pack_entry(self) -> None:
        """The completeness test: a rule line outside every pack unit fails the build."""
        catalog = task_inputs.catalog()
        for entry, route in sorted(catalog["entries"].items()):
            for role in [None, *route["roles"]]:
                pack = context_pack.build(entry=entry, role=role, mode="review")
                covered: dict[str, set[int]] = {}
                for unit in [*pack["rules"], *pack["excluded"]]:
                    covered.setdefault(unit["source"], set()).update(
                        range(unit["lines"][0], unit["lines"][1] + 1))
                for source in pack["sources"]:
                    if source["kind"] != "rules":
                        continue
                    text = (PACKAGE / source["path"]).read_text(encoding="utf-8")
                    missing = [line for line in rule_lines(text)
                               if line not in covered.get(source["path"], set())]
                    self.assertEqual(missing, [], f"{entry}/{role}: {source['path']}")

    def test_extractor_keeps_rules_and_drops_description(self) -> None:
        text = "\n".join([
            "---", "name: x", "description: never shown", "---", "# Title", "",
            "The flow describes the backlog.", "",
            "- The writer must stamp every note.",
            "  It continues here.",
            "- A note about colour.", "",
            "Switch `lane_table`: at `recorded`, record lanes.", "",
            "```text", "backlog_compile.py check --docs <docs>", "```", "",
            "```text", "plain example", "```", "",
            "| Field | Rule |", "|---|---|", "| id | Required for every story |",
            "| title | free text |"])
        kept, excluded = context_pack.extract("f.md", text, "product-owner", [])
        self.assertEqual(excluded, [])
        self.assertEqual([rule["id"] for rule in kept], ["f.md#L9", "f.md#L13", "f.md#L15", "f.md#L25"])
        self.assertEqual(kept[0]["text"], "- The writer must stamp every note. It continues here.")
        self.assertEqual(kept[0]["lines"], [9, 10])
        self.assertEqual(kept[2]["text"], "backlog_compile.py check --docs <docs>")
        self.assertEqual(kept[0]["section"], "Title")

    def test_rule_naming_only_another_role_is_excluded_by_id(self) -> None:
        text = ("- The QA engineer must write the plan.\n\n"
                "- The product owner and the qa-engineer must agree.\n\n"
                "- Every role must cite its sources.\n")
        kept, excluded = context_pack.extract("f.md", text, "product-owner", ["qa-engineer"])
        self.assertEqual([rule["id"] for rule in kept], ["f.md#L3", "f.md#L5"])
        self.assertEqual(excluded, [{"id": "f.md#L1", "source": "f.md", "lines": [1, 1],
                                     "kind": "item", "section": "", "names": ["qa-engineer"]}])
        self.assertNotIn("text", excluded[0])
        everything, none = context_pack.extract("f.md", text, None, ["qa-engineer"])
        self.assertEqual(len(everything), 3)
        self.assertEqual(none, [])

    def test_check_accepts_a_fresh_pack(self) -> None:
        pack = context_pack.build(entry="backlog-plan", role="product-owner", mode="revise")
        self.assertTrue(context_pack.check(pack)["ok"])

    def test_an_empty_pack_is_refused(self) -> None:
        with mock.patch.object(context_pack, "extract", return_value=([], [])):
            with self.assertRaises(context_pack.Refused) as caught:
                context_pack.build(entry="backlog-plan", role="backlog-reviewer", mode="review")
        self.assertEqual(caught.exception.code, "CONTEXT_PACK_EMPTY")



class StalePackTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        self.base = Path(temporary.name).resolve()
        self.package = self.base / "package"
        shutil.copytree(PACKAGE, self.package, ignore=shutil.ignore_patterns("__pycache__"))
        self.pack = context_pack.build(entry="backlog-plan", role="product-owner",
                                       mode="revise", package=self.package)

    def test_changed_source_stales_the_pack(self) -> None:
        flow = self.package / "flows/backlog-planning.md"
        flow.write_text(flow.read_text(encoding="utf-8") + "\n- A new step must run.\n",
                        encoding="utf-8")
        with self.assertRaises(context_pack.Refused) as caught:
            context_pack.check(self.pack, package=self.package)
        self.assertEqual(caught.exception.code, "CONTEXT_PACK_STALE")
        self.assertEqual([item["path"] for item in caught.exception.detail["stale"]],
                         ["flows/backlog-planning.md"])
        rebuilt = context_pack.build(entry="backlog-plan", role="product-owner", mode="revise",
                                     package=self.package)
        self.assertIn("- A new step must run.", [rule["text"] for rule in rebuilt["rules"]])

    def test_changed_conditional_read_stales_the_pack(self) -> None:
        link = self.package / self.pack["conditional_reads"][0]["path"]
        link.write_text(link.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
        with self.assertRaises(context_pack.Refused) as caught:
            context_pack.check(self.pack, package=self.package)
        self.assertEqual(caught.exception.code, "CONTEXT_PACK_STALE")

    def test_edited_pack_is_refused(self) -> None:
        edited = json.loads(json.dumps(self.pack))
        edited["rules"].pop()
        with self.assertRaises(context_pack.Refused) as caught:
            context_pack.check(edited, package=self.package)
        self.assertEqual(caught.exception.code, "CONTEXT_PACK_STALE")
        edited["pack_hash"] = context_pack.sha256(context_pack.canonical(
            {key: value for key, value in edited.items() if key != "pack_hash"}))
        with self.assertRaises(context_pack.Refused):
            context_pack.check(edited, package=self.package)

    def test_check_refuses_a_source_outside_the_package(self) -> None:
        outside = self.base / "outside.md"
        outside.write_text("secret\n", encoding="utf-8")
        for path in (str(outside), "../outside.md"):
            edited = json.loads(json.dumps(self.pack))
            edited["sources"][0]["path"] = path
            with self.subTest(path=path), self.assertRaises(context_pack.Refused) as caught:
                context_pack.stale_sources(edited, package=self.package)
            self.assertEqual(caught.exception.code, "CONTEXT_PACK_SOURCE")

    def test_cli_check_refuses_stale_pack(self) -> None:
        target = self.base / "pack.json"
        target.write_text(json.dumps(dict(self.pack, pack_hash="sha256:" + "0" * 64)),
                          encoding="utf-8")
        code, result = call(["check", "--pack", str(target)])
        self.assertEqual(code, 1)
        self.assertEqual(result["code"], "CONTEXT_PACK_STALE")


@integration
class ProjectSwitchTests(unittest.TestCase):
    """A real project whose approved Process Policy sets context_pack; nothing mocked."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        self.project = Path(temporary.name).resolve() / "project"
        self.docs = self.project / "workspace/docs"
        (self.docs / "maps").mkdir(parents=True)
        (self.project / "workspace/config.json").write_text(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English"}), encoding="utf-8")
        init_repository(self.project)
        (self.project / "README.md").write_text("x\n", encoding="utf-8")

    def policy(self, value: str | None) -> None:
        import process_policy
        commands = [("init", [])]
        if value is not None:
            commands.append(("set", ["--switch", "context_pack", "--value", value]))
        commands.append(("approve", []))
        for command, arguments in commands:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = process_policy.main([command, "--docs", str(self.docs), *arguments])
            self.assertEqual(code, 0, output.getvalue())
        subprocess.run(["git", "-C", str(self.project), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=t", "-c",
                        "user.email=t@example.invalid", "commit", "-qm", "policy"], check=True)

    def test_default_off_refuses_and_writes_nothing(self) -> None:
        self.policy(None)
        code, result = call(["build", "--entry", "backlog-plan", "--role", "product-owner",
                             "--project-root", str(self.project)])
        self.assertEqual(code, 1)
        self.assertEqual(result["code"], "CONTEXT_PACK_OFF")
        self.assertFalse((self.project / ".agentrof").exists())

    def test_role_digest_builds_a_full_pack_for_the_project(self) -> None:
        self.policy("role_digest")
        code, result = call(["build", "--entry", "backlog-plan", "--role", "backlog-reviewer",
                             "--mode", "review", "--project-root", str(self.project)])
        self.assertEqual(code, 0, result)
        path = Path(result["path"])
        self.assertEqual(path, self.project / ".agentrof/agent-marketplace/.runtime"
                         "/context-packs/backlog-plan/backlog-reviewer-review.json")
        pack = json.loads(path.read_text(encoding="utf-8"))
        paths = [source["path"] for source in pack["sources"]]
        self.assertIn("constitution.md", paths)
        self.assertIn("flows/backlog-planning.md", paths)
        self.assertIn("skill-content/challenge-review/references/"
                      "switch-context_pack-role_digest.md", paths)
        self.assertGreater(len(pack["rules"]), 50)
        code, checked = call(["check", "--pack", str(path), "--project-root", str(self.project)])
        self.assertEqual(code, 0, checked)
        self.assertEqual(checked["sources"], len(paths))
        # The task binds the same pack, and keeps the constitution, the role
        # and every switch reference as full reads.
        import task_inputs
        task = task_inputs.manifest(entry="backlog-plan", role="backlog-reviewer",
                                    mode="review", project=self.project)
        self.assertEqual(task["context_pack"]["pack_hash"], pack["pack_hash"])
        self.assertIn("constitution.md", task["required_reads"])
        self.assertIn("agents/backlog-reviewer.md", task["required_reads"])
        self.assertIn("skill-content/challenge-review/references/"
                      "switch-context_pack-role_digest.md", task["required_reads"])
        self.assertNotIn("flows/backlog-planning.md", task["required_reads"])

    def test_a_kept_result_checks_its_pack_against_the_current_package(self) -> None:
        import task_inputs
        self.policy("role_digest")
        package = self.project.parent / "package"
        shutil.copytree(PACKAGE, package, ignore=shutil.ignore_patterns("__pycache__"))
        task = dict(entry="backlog-plan", role="backlog-reviewer", mode="review",
                    project=self.project, package=package)
        result = task_inputs.manifest(**task)
        self.assertEqual(result["next_transition_conditions"][0]["condition"],
                         "context_pack_check")
        self.assertEqual(task_inputs.manifest(**task, expected_hash=result["source_hash"]), result)
        flow = package / "flows/backlog-planning.md"
        flow.write_text(flow.read_text(encoding="utf-8") + "\n- A new step must run.\n",
                        encoding="utf-8")
        with self.assertRaises(ValueError):
            task_inputs.manifest(**task, expected_hash=result["source_hash"])


    def test_out_never_writes_a_vault_or_project_file(self) -> None:
        self.policy("role_digest")
        note = self.project / "workspace/docs/backlog/story.md"
        note.parent.mkdir(parents=True)
        note.write_text("# Story\n", encoding="utf-8")
        inside = self.project / "workspace/docs/.agentrof/agent-marketplace/.runtime/context-packs/x.json"
        cased = self.project / "Workspace/Docs/.agentrof/agent-marketplace/.runtime/context-packs/x.json"
        for out in (note, self.project / "README.md", self.project / "pack.json", inside, cased):
            with self.subTest(out=out.name):
                code, result = call(["build", "--entry", "backlog-plan", "--role",
                                     "product-owner", "--project-root", str(self.project),
                                     "--out", str(out)])
                self.assertEqual((code, result["code"]), (1, "CONTEXT_PACK_OUT"), result)
        self.assertEqual(note.read_text(encoding="utf-8"), "# Story\n")
        self.assertEqual((self.project / "README.md").read_text(encoding="utf-8"), "x\n")
        self.assertFalse((self.project / "pack.json").exists())
        self.assertFalse(inside.exists())

if __name__ == "__main__":
    unittest.main()
