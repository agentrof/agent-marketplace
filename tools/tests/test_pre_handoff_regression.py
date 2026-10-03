"""Pre-handoff regression: switch `pre_handoff_regression` keeps today's handoff at
`off` and, at `touched_suites`, runs the suites of the earlier stories an Item's
change touches, with the Item's own Test Plan targets, before `freeze`, which
refuses until such a run passed on the exact candidate (#351)."""

from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(TEAM / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import backlog_compile  # noqa: E402
import delivery_compile as delivery  # noqa: E402
import delivery_result  # noqa: E402
import delivery_verification as verification  # noqa: E402
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
from git_fixture import init_repository, remove_temporary  # noqa: E402

SWITCH = "pre_handoff_regression"
REFERENCE = "skill-content/deliver/references/switch-pre_handoff_regression-touched_suites.md"
CODE = "DELIVERY_PRE_HANDOFF_MISSING"
REUSED_MARKER = "Earlier-story targets QA's final test run reused, recorded by approve-item-evidence:"
PYTHON = subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)
DOCS = "workspace/docs/"
EARLIER = DOCS + "delivery/deliveries/dlv-001-core/"
OPEN = DOCS + "delivery/deliveries/dlv-003-open/"
CURRENT = DOCS + "delivery/deliveries/dlv-002-next/"
PLANS = DOCS + "backlog/epics/core/stories/"
# The approved diagnostic adapter: it runs exactly the selected test ids.
ADAPTER = """import json, os, pathlib, runpy, sys
selection = json.loads(pathlib.Path(os.environ["AGENTROF_DIAGNOSTIC_TESTS"]).read_text(encoding="utf-8"))
print("IGNORED-VISIBLE" if pathlib.Path("ignored.txt").exists() else "TRACKED-ONLY")
print("PYTHONPATH=" + os.environ.get("PYTHONPATH", ""))
failed = []
for identifier in selection["selected_test_ids"]:
    path, _, name = identifier.partition("::")
    try:
        runpy.run_path(path)[name]()
        print("PASS " + identifier)
    except AssertionError:
        failed.append(identifier)
        print("FAIL " + identifier)
sys.exit(1 if failed else 0)
"""
# The approved full test command: it runs every test of every story but those
# the runner names as reused from the pre-handoff run.
FULL_SUITE = """import json, os, pathlib, runpy, sys
print("SELECTION=" + str("AGENTROF_DIAGNOSTIC_TESTS" in os.environ))
print("REUSE=" + str("AGENTROF_REUSED_TESTS" in os.environ))
reused = set()
if "AGENTROF_REUSED_TESTS" in os.environ:
    reused = set(json.loads(pathlib.Path(os.environ["AGENTROF_REUSED_TESTS"]).read_text(encoding="utf-8"))[
        "reused_test_ids"])
failed = []
for path in sorted(pathlib.Path("tests").rglob("test_*.py")):
    for name, value in sorted(runpy.run_path(str(path)).items()):
        if name.startswith("test_") and callable(value):
            identifier = path.as_posix() + "::" + name
            if identifier in reused:
                print("REUSED " + identifier)
                continue
            try:
                value()
                print("PASS " + identifier)
            except AssertionError:
                failed.append(identifier)
                print("FAIL " + identifier)
sys.exit(1 if failed else 0)
"""
# An approved full test command that skips the reused ids by node id prefix, as pytest's --deselect does.
PREFIX_SKIP = FULL_SUITE.replace("if identifier in reused:", "if identifier.startswith(tuple(reused)):")
# An approved adapter that first empties a selection file, then runs what its own selection holds.
EMPTYING = """import json, os, pathlib
path = pathlib.Path({target})
value = json.loads(path.read_text(encoding="utf-8"))
for key in ("affected_test_ids", "selected_test_ids"):
    if key in value:
        value[key] = []
path.write_text(json.dumps(value), encoding="utf-8")
"""
# An approved full test command that first adds a suite to the reuse list it received, then skips what the list holds.
WIDENING = """import json, os, pathlib
if "AGENTROF_REUSED_TESTS" in os.environ:
    path = pathlib.Path(os.environ["AGENTROF_REUSED_TESTS"])
    value = json.loads(path.read_text(encoding="utf-8"))
    value["reused_test_ids"].append("tests/misc/test_misc.py::test_misc")
    path.write_text(json.dumps(value), encoding="utf-8")
"""
# An approved adapter that, given a gate, signals its start and holds the run until the test releases it.
GATED = """import os, pathlib, time
if os.environ.get("FIXTURE_GATE"):
    gate = pathlib.Path(os.environ["FIXTURE_GATE"])
    (gate / "started").write_text("started", encoding="utf-8")
    deadline = time.monotonic() + 60
    while not (gate / "release").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
"""


def suite(path: str, name: str, expected: str) -> str:
    return (f"import pathlib\n\n\ndef {name}():\n"
            f"    assert pathlib.Path({path!r}).read_text(encoding='utf-8').strip() == {expected!r}\n")


def read(relative: str) -> str:
    return (TEAM / relative).read_text(encoding="utf-8")


def policy(docs: Path, *argv: str) -> None:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = process_policy.main([argv[0], "--docs", str(docs), *argv[1:]])
    if code:
        raise AssertionError(output.getvalue())


def scenario(identifier: str, automation: str, target: str) -> str:
    return "\n".join([f"## {identifier}", "", "- category: happy-path", "- target: api",
                      f"- automation: {automation}", f"- automation_target: {target}",
                      "- source_refs:", "  - [[business-analysis/core/acceptance|AC-1]]",
                      "- Given: an approved fixture", "- When: the suite runs",
                      "- Then: the value holds", ""])


class RegistryTests(unittest.TestCase):
    def test_the_refusal_code_is_declared_and_reaches_the_envelope(self):
        self.assertIn(CODE, delivery_result.FINDING_CODES)
        contract = json.loads(read("skill-content/deliver/data/delivery-result-contract.json"))
        self.assertIn(CODE, contract["finding_codes"])
        envelope = delivery_result.from_raw("freeze", {"ok": False, "errors": [
            f"{CODE}: no pre-handoff regression run binds candidate tree {'a' * 40}"]})
        self.assertEqual(envelope["findings"][0]["code"], CODE)


class BindingTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        self.root = Path(temporary.name).resolve()
        init_repository(self.root)
        (self.root / ".gitignore").write_text(".agentrof/\n", encoding="utf-8")
        (self.root / "brief.md").write_text("Accepted intent.\n", encoding="utf-8")
        for args in (("add", "-A"), ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                                     "commit", "-qm", "Fixture")):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)
        self.docs = self.root / "workspace" / "docs"

    def bound(self, entry: str, role: str) -> bool:
        result = task_inputs.manifest(entry=entry, role=role, mode="review", project=self.root)
        return REFERENCE in result["required_reads"]

    def test_only_delivery_execution_tasks_at_touched_suites_bind_the_reference(self):
        tasks = (("deliver", "delivery-coordinator"), ("deliver", "qa-engineer"),
                 ("deliver", "backend-developer"), ("deliver", "code-reviewer"),
                 ("execution-plan", "delivery-coordinator"), ("configure", "qa-engineer"))
        for entry, role in tasks:
            with self.subTest(value="off", task=(entry, role)):
                self.assertFalse(self.bound(entry, role))
        policy(self.docs, "init")
        policy(self.docs, "set", "--switch", SWITCH, "--value", "touched_suites")
        policy(self.docs, "approve")
        for entry, role in tasks:
            with self.subTest(value="touched_suites", task=(entry, role)):
                self.assertEqual(self.bound(entry, role), entry == "deliver")


class CandidateTests(unittest.TestCase):
    """One project whose DLV-002 Item ST-005 changes a path that DLV-001's merged ST-001 claimed."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        self.root = Path(temporary.name).resolve()
        self.docs = self.root / "workspace" / "docs"
        # Other implementation workers may edit the package in this shared checkout.
        identity = mock.patch.object(verification, "instruction_identity", return_value="sha256:policy")
        identity.start()
        self.addCleanup(identity.stop)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True,
                              text=True).stdout.strip()

    def commit(self, message: str = "Fixture", *extra: str) -> str:
        self.git("add", "-A")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-q", "--allow-empty", "-m", message, *extra)
        return self.git("rev-parse", "HEAD")

    def write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def note(self, relative: str, props: dict, body: str = "# Record\n\nFixture record.\n") -> None:
        self.write(relative, delivery.frontmatter(props, body))

    def plan(self, story: str, *scenarios: str) -> None:
        self.note(PLANS + story.lower() + "/test-plan.md",
                  {"type": "test-plan", "story_id": story, "status": "approved"},
                  f"# {story} Test Plan\n\n" + "\n".join(scenarios))
        self.note(PLANS + story.lower() + "/story.md",
                  {"type": "story", "id": story, "status": "approved"})

    def plan_path(self, story: str) -> Path:
        return self.root / (PLANS + story.lower() + "/test-plan.md")

    def plan_hash(self, story: str) -> str:
        """The digest an Item records as test_plan_source_hash for the story's Test Plan as written."""
        return backlog_compile.digest(self.plan_path(story))

    def item(self, directory: str, story: str, status: str, claims: list[str], **extra) -> None:
        self.note(directory + "items/" + story.lower() + "/item.md", {
            "type": "delivery-item", "title": story + " Item", "story_id": story, "status": status,
            "story_path": "backlog/epics/core/stories/" + story.lower() + "/story.md",
            "test_plan_path": "backlog/epics/core/stories/" + story.lower() + "/test-plan.md",
            "path_claims": claims, **extra})

    def contract(self, **changes) -> None:
        path = DOCS + "operation/verification-contract.md"
        props, body = delivery.split_note(self.root / path)
        for key, value in changes.items():
            if value is None:
                props.pop(key, None)
            else:
                props[key] = value
        self.write(path, delivery.frontmatter(props, body))

    def build(self, value: str | None = "touched_suites") -> None:
        """Merge DLV-001, leave DLV-003 open and commit DLV-002's Item ST-005 candidate."""
        init_repository(self.root, initial_branch="main")
        self.git("config", "core.autocrlf", "false")
        self.write(".gitignore", ".agentrof/\nignored.txt\n")
        self.write("src/api/limit.txt", "10\n")
        self.write("src/web/page.txt", "home\n")
        self.write("tests/st001/test_api.py", suite("src/api/limit.txt", "test_limit", "10"))
        self.write("tests/st002/test_web.py", suite("src/web/page.txt", "test_page", "home"))
        self.write("diagnose.py", ADAPTER)
        self.write("run_all.py", FULL_SUITE)
        (self.docs / "maps").mkdir(parents=True)
        self.note(DOCS + "delivery/definition-of-done.md", {"type": "definition-of-done", "status": "approved"})
        self.note(DOCS + "operation/verification-contract.md", {
            "type": "verification-contract", "status": "approved",
            "test_command": PYTHON + " run_all.py", "test_workdir": ".",
            "diagnostic_test_command": PYTHON + " diagnose.py", "diagnostic_test_workdir": ".",
            "mutation_disposition": "not_applicable", "dependency_audit_disposition": "not_applicable"})
        self.plan("ST-001", scenario("ST-001-TS-001", "required", "tests/st001/test_api.py::test_limit"),
                  scenario("ST-001-TS-002", "manual", "tests/st001/manual-check.md"))
        self.plan("ST-002", scenario("ST-002-TS-001", "required", "tests/st002/test_web.py::test_page"))
        self.plan("ST-005", scenario("ST-005-TS-001", "required", "tests/st005/test_new.py::test_total"))
        self.commit("Base")
        # DLV-001 reaches main only through the merge of its recorded PR head.
        self.git("checkout", "-q", "-b", "integration")
        self.note(EARLIER + "delivery.md", {"type": "delivery", "id": "DLV-001", "status": "awaiting_merge"})
        self.note(EARLIER + "delivery-review.md", {"type": "delivery-review",
                                                   "pull_request_url": "https://example.invalid/pull/1"})
        self.item(EARLIER, "ST-001", "integrated", ["src/api", "tests/st001"],
                  test_plan_source_hash=self.plan_hash("ST-001"))
        self.item(EARLIER, "ST-002", "integrated", ["src/web", "tests/st002"],
                  test_plan_source_hash=self.plan_hash("ST-002"))
        # A cancelled Item's change was reverted; its Test Plan is never read.
        self.item(EARLIER, "ST-003", "cancelled", ["src/api/limit.txt"])
        intent = self.commit("Publish the DLV-001 Review")
        self.commit("Record PR", "-m", "Agentrof-Record: pr-url-recorded-v1\nAgentrof-Delivery: DLV-001\n"
                                       f"Agentrof-Intent: {intent}")
        self.git("checkout", "-q", "main")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "merge", "-q", "--no-ff", "-m", "Merge pull request #1", "integration")
        # DLV-003 is not merged, so its integrated Item is no earlier story.
        self.note(OPEN + "delivery.md", {"type": "delivery", "id": "DLV-003", "status": "active"})
        self.item(OPEN, "ST-004", "integrated", ["src/api"])
        if value is not None:
            policy(self.docs, "init")
            policy(self.docs, "set", "--switch", SWITCH, "--value", value)
            policy(self.docs, "approve")
        base = self.commit("Open DLV-003")
        self.note(CURRENT + "delivery.md", {"type": "delivery", "id": "DLV-002", "status": "active",
                                            "definition_of_done_path": "delivery/definition-of-done.md"})
        self.note(CURRENT + "execution-plan.md", {"type": "execution-plan", "plan_hash": "sha256:plan"})
        self.item(CURRENT, "ST-005", "active", ["src/api/limit.txt", "src/api/total.txt", "tests/st005"],
                  item_plan_hash="sha256:plan", verification_schedule="parallel_snapshot_v1",
                  integration_base_commit=base, verification_contract_ref="operation/verification-contract",
                  role_sequence=["backend_developer", "code_reviewer", "qa_engineer"])
        for name, kind in (("code-review.md", "code-review"), ("verification.md", "verification")):
            self.note(CURRENT + "items/st-005/" + name, {"type": kind, "title": name, "status": "draft",
                                                        "tags": [], "item_plan_hash": "sha256:plan"})
        # ST-005's change breaks the suite of the earlier story ST-001.
        self.write("src/api/limit.txt", "20\n")
        self.write("src/api/total.txt", "30\n")
        self.write("tests/st005/test_new.py", suite("src/api/total.txt", "test_total", "30"))
        self.commit("ST-005 candidate")

    def selection(self) -> dict:
        return verification.regression_selection(self.root, "DLV-002", "ST-005")

    def run_regression(self) -> dict:
        return verification.regression_run(self.root, "DLV-002", "ST-005")

    def freeze(self) -> dict:
        return verification.freeze(self.root, "DLV-002", "ST-005")

    def output(self, run: dict) -> str:
        return Path(run["output_path"]).read_text(encoding="utf-8")

    def reader_result(self, role: str, verdict: str = "passed", fresh: bool = False) -> dict:
        """A reader's result on the frozen session: passing with every required check, QA's test run *fresh*
        when asked, or a confirmed cancellation."""
        session = verification.read_session(self.root)
        result = {"candidate_hash": session["candidate"]["candidate_hash"], "session_id": session["session_id"],
                  "role": role, "mode": "review_initial" if role == "code_reviewer" else "qa_final",
                  "verdict": verdict, "report": f"Independent {role} result", "findings": []}
        if verdict == "cancelled":
            return {**result, "cancellation_confirmed": True}
        result["checks"] = {name: {"passed": True, "evidence": "Independently verified"}
                            for name in verification.required_checks(self.root, session["candidate"], role)}
        if role == "qa_engineer":
            raw = verification.run_check(self.root, "test", fresh=fresh)
            result["checks"]["full_test_suite"].update(command=PYTHON + " run_all.py", exit_code=0,
                                                       environment=raw["identity"]["environment_hash"],
                                                       raw_evidence_hash=raw["evidence_hash"])
        return result

    def approve_evidence(self, fresh: bool = False) -> str:
        """Settle both readers passing, QA's test run *fresh* when asked, approve the Item's evidence and return
        its verification record body."""
        for role in ("code_reviewer", "qa_engineer"):
            verification.register_result(self.root, self.reader_result(role, fresh=fresh))
        args = type("Args", (), {"docs": ".", "worktree": str(self.root), "delivery": "DLV-002", "story": "ST-005"})
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(delivery.approve_item_evidence(args), 0, output.getvalue())
        return delivery.split_note(self.root / (CURRENT + "items/st-005/verification.md"))[1]

    def test_off_freezes_as_released_and_the_regression_verbs_refuse(self):
        self.build(value=None)
        refusal = "pre-handoff regression runs only at process switch pre_handoff_regression touched_suites"
        for verb in (self.selection, self.run_regression):
            with self.subTest(verb=verb.__name__), self.assertRaisesRegex(RuntimeError, refusal):
                verb()
        session = self.freeze()
        self.assertNotIn("pre_handoff", session)
        self.assertNotIn("pre_handoff_history", session)
        self.assertEqual(set(session["metrics"]), {"freeze_seconds", "command_cache_hits", "command_seconds",
                                                  "model_seconds", "model_tokens"})
        self.assertFalse((verification.session_path(self.root).parent / "pre-handoff.json").exists())
        # The Item's verification record is approved as released, with no pre-handoff block.
        self.write("src/api/limit.txt", "10\n")
        self.commit("Repair the ST-001 regression")
        verification.register_result(self.root, self.reader_result("code_reviewer", "cancelled"))
        verification.register_result(self.root, self.reader_result("qa_engineer", "cancelled"))
        self.freeze()
        self.assertNotIn("Pre-handoff regression runs", self.approve_evidence())
        # QA's final run reuses nothing: the approved command runs every suite, as released.
        raw = verification.read_session(self.root)["raw_evidence"]["test"]
        self.assertNotIn("reused_pre_handoff", raw["identity"])
        output = verification.raw_output_path(self.root, raw["output_file"]).read_text(encoding="utf-8")
        self.assertIn("REUSE=False", output)
        self.assertNotIn("REUSED ", output)

    def test_every_pre_handoff_run_reaches_the_items_verification_record(self):
        """Every run's seconds and result travel from session to session and reach the evidence approval."""
        self.build()
        failed = self.run_regression()
        self.write("src/api/limit.txt", "10\n")
        self.commit("Repair the ST-001 regression")
        passed = self.run_regression()
        fields = ("evidence_hash", "candidate_tree", "kind", "exit_code", "candidate_intact", "duration_seconds",
                  "earlier_stories")
        history = [{key: run[key] for key in fields} for run in (failed, passed)]
        self.assertEqual(self.freeze().get("pre_handoff_history"), history)
        # The readers stop, a runtime cleanup moves the run record aside, the run is repeated and the
        # same tree frozen again: the history keeps every earlier run once and adds the new one.
        for role in ("code_reviewer", "qa_engineer"):
            verification.register_result(self.root, self.reader_result(role, "cancelled"))
        record = verification.session_path(self.root).parent / "pre-handoff.json"
        record.rename(record.with_name("pre-handoff.moved.json"))
        again = self.run_regression()
        history.append({key: again[key] for key in fields})
        self.assertEqual(verification.freeze(self.root, "DLV-002", "ST-005", fresh=True)["pre_handoff_history"],
                         history)
        section = delivery.section_bodies(self.approve_evidence())["Implementation Evidence"]
        marker = "Pre-handoff regression runs, recorded by approve-item-evidence:"
        self.assertTrue(section.startswith("Independent qa_engineer result\n\n" + marker), section)
        rows = [f"| {number} | {run['candidate_tree']} | diagnostic_test | {result} | {run['exit_code']} |"
                f" {round(run['duration_seconds'], 1)} | ST-001 of DLV-001 |"
                for number, run, result in ((1, failed, "failed"), (2, passed, "passed"), (3, again, "passed"))]
        self.assertEqual(section.split(marker, 1)[1], "\n".join([
            "", "", "| run | candidate_tree | kind | result | exit_code | seconds | earlier_stories |",
            "|---|---|---|---|---|---|---|", *rows, "", REUSED_MARKER, "",
            "| story | test_ids | pre_handoff_run | evidence_hash |", "|---|---|---|---|",
            f"| ST-001 of DLV-001 | tests/st001/test_api.py::test_limit | 3 | {again['evidence_hash']} |"]))

    def test_selection_holds_the_merged_earlier_stories_the_change_touches_and_the_items_own(self):
        self.build()
        result = self.selection()
        # The candidate's own Test Plan is the revision ST-001's Item integrated.
        self.assertEqual(result["earlier_stories"], [{
            "delivery": "DLV-001", "story": "ST-001", "item": EARLIER + "items/st-001/item.md",
            "test_plan": PLANS + "st-001/test-plan.md", "test_plan_source_hash": self.plan_hash("ST-001"),
            "test_plan_commit": self.git("rev-parse", "HEAD"), "touched_claims": ["src/api"],
            "touched_paths": ["src/api/limit.txt", "src/api/total.txt"],
            "automation_targets": ["tests/st001/test_api.py::test_limit"]}])
        self.assertEqual(result["own_targets"], ["tests/st005/test_new.py::test_total"])
        self.assertEqual(result["affected_test_ids"], ["tests/st001/test_api.py::test_limit",
                                                       "tests/st005/test_new.py::test_total"])
        self.assertEqual((result["kind"], result["command"]), ("diagnostic_test", PYTHON + " diagnose.py"))
        current = verification.candidate(self.root, "DLV-002", "ST-005", allow_evidence=True)
        self.assertEqual(result["candidate_tree"], self.git("rev-parse", "HEAD^{tree}"))
        # The file is the diagnostic adapter's own selection, so QA can run it on this candidate.
        path = Path(result["selection_file"])
        self.assertEqual(path, verification.session_path(self.root).parent / "scratch/pre-handoff-selection.json")
        written = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(written, {"schema_version": 1, "candidate_hash": current["candidate_hash"],
                                   "failed_test_ids": [], "affected_test_ids": result["affected_test_ids"]})
        self.assertEqual(verification.diagnostic_selection(self.root, path, current)["selected_test_ids"],
                         result["affected_test_ids"])
        # Deriving it again leaves the file a QA diagnostic may be reading untouched.
        generation = verification.source_file_generation(path)
        self.assertEqual(self.selection()["selection_file"], str(path))
        self.assertEqual(verification.source_file_generation(path), generation)

    def test_an_unreadable_earlier_record_refuses_instead_of_dropping_its_suite(self):
        """Each record that decides whether a merged Delivery's suite runs refuses the selection when it cannot be read."""
        self.build()
        review = self.root / (EARLIER + "delivery-review.md")
        records = (
            # A byte order mark, as an editor may write one, hides the review record's PR record,
            # which alone proves that DLV-001 merged: the compiler's merge state refuses it.
            (EARLIER + "delivery-review.md", lambda data: b"\xef\xbb\xbf" + data,
             "whether DLV-001 merged decides which earlier suites the candidate touches: Delivery merge state"
             f" cannot be evaluated: {review} cannot be read: missing frontmatter block"),
            (EARLIER + "delivery.md",
             lambda data: data.replace(b"status: awaiting_merge", b"status: awaiting_merge \xe9"),
             EARLIER + "delivery.md cannot be read, so the suites it merged cannot be selected"),
            (EARLIER + "items/st-001/item.md", lambda data: data.replace(b"\npath_claims:", b"\npath claims\npath_claims:"),
             EARLIER + "items/st-001/item.md cannot be read, so its suite cannot be selected"),
        )
        for relative, corrupt, refusal in records:
            with self.subTest(record=relative):
                path = self.root / relative
                original = path.read_bytes()
                path.write_bytes(corrupt(original))
                self.commit("Hand edit " + relative)
                try:
                    for verb in (self.selection, self.run_regression):
                        with self.assertRaisesRegex(RuntimeError, "^" + re.escape(refusal)):
                            verb()
                finally:
                    path.write_bytes(original)
                    self.commit("Restore " + relative)
        self.assertEqual([entry["story"] for entry in self.selection()["earlier_stories"]], ["ST-001"])
        # A history Git cannot read whole refuses as well, since the merge proof walks it.
        shallow = Path(self.git("rev-parse", "--git-path", "shallow"))
        shallow = shallow if shallow.is_absolute() else self.root / shallow
        shallow.write_text(self.git("rev-list", "--max-parents=0", "HEAD") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "^whether DLV-001 merged decides which earlier suites the candidate"
                                                  " touches: Delivery merge state cannot be evaluated in a shallow"
                                                  " clone"):
            self.selection()

    def test_a_merged_delivery_without_its_pr_record_refuses_instead_of_dropping_its_suite(self):
        """The commit that records the PR URL in the Review sets awaiting_merge, so a Delivery at awaiting_merge
        whose Review record is missing or has no pull_request_url is a broken record, not one that recorded no
        PR: the selection and the run refuse it, and freeze cannot accept the candidate."""
        self.build()
        review = self.root / (EARLIER + "delivery-review.md")
        recorded = review.read_bytes()
        props, body = delivery.split_note(review)
        unrecorded = delivery.frontmatter({key: value for key, value in props.items() if key != "pull_request_url"},
                                          body)
        reached = ", but a Delivery reaches awaiting_merge only with its PR recorded there"
        for name, breaks, finding in (
                ("deleted", review.unlink, f"{review} is missing{reached}"),
                ("without its URL", lambda: review.write_text(unrecorded, encoding="utf-8"),
                 f"{review} records no pull_request_url{reached}")):
            with self.subTest(review=name):
                breaks()
                self.commit("Break the DLV-001 Review record")
                refusal = ("whether DLV-001 merged decides which earlier suites the candidate touches: Delivery"
                           " merge state cannot be evaluated: " + finding)
                try:
                    for verb in (self.selection, self.run_regression, self.freeze):
                        with self.assertRaisesRegex(RuntimeError, "^" + re.escape(refusal)):
                            verb()
                    self.assertIsNone(verification.read_session(self.root, required=False))
                finally:
                    review.write_bytes(recorded)
                    self.commit("Restore the DLV-001 Review record")
        self.assertEqual([entry["story"] for entry in self.selection()["earlier_stories"]], ["ST-001"])

    def test_an_earlier_story_selects_the_test_plan_revision_its_item_integrated(self):
        """A Test Plan revised or removed after an earlier Item integrated it selects the revision the Item bound."""
        self.build()
        integrated = self.plan_hash("ST-001")
        base = self.git("rev-list", "--max-parents=0", "HEAD")
        # A backlog revision for a later Delivery renames the suite's test and adds one nobody wrote yet.
        self.plan("ST-001", scenario("ST-001-TS-001", "required", "tests/st001/test_api.py::test_renamed"),
                  scenario("ST-001-TS-003", "required", "tests/st001/test_api.py::test_ceiling"))
        self.commit("Revise ST-001 for a later Delivery")
        entry = self.selection()["earlier_stories"][0]
        self.assertEqual((entry["automation_targets"], entry.get("test_plan_source_hash"),
                          entry.get("test_plan_commit")),
                         (["tests/st001/test_api.py::test_limit"], integrated, base))
        # The run takes the integrated suite, so the regression in it still blocks the freeze.
        output = self.output(self.run_regression())
        self.assertIn("FAIL tests/st001/test_api.py::test_limit", output)
        self.assertNotIn("test_renamed", output)
        # A Test Plan the candidate no longer holds is read from the candidate's history.
        self.plan_path("ST-001").unlink()
        self.commit("Remove ST-001's Test Plan")
        entry = self.selection()["earlier_stories"][0]
        self.assertEqual((entry["automation_targets"], entry["test_plan_commit"]),
                         (["tests/st001/test_api.py::test_limit"], base))
        # A revision that neither the candidate nor its history holds refuses instead of dropping the suite.
        claims = ["src/api", "tests/st001"]
        for recorded, refusal in (
                ("sha256:" + "0" * 64,
                 "ST-001 of DLV-001 integrated Test Plan backlog/epics/core/stories/st-001/test-plan.md at sha256:"
                 + "0" * 64 + ", a revision neither the candidate nor its history holds; its suite cannot be selected"),
                (None, "ST-001 of DLV-001 records no test_plan_source_hash, so the Test Plan revision it integrated"
                       " cannot be selected")):
            with self.subTest(recorded=recorded):
                extra = {} if recorded is None else {"test_plan_source_hash": recorded}
                self.item(EARLIER, "ST-001", "integrated", claims, **extra)
                self.commit("Edit ST-001's Item record")
                with self.assertRaisesRegex(RuntimeError, "^" + re.escape(refusal)):
                    self.selection()

    def test_a_story_integrated_again_selects_its_newest_integrated_revision(self):
        """When the candidate holds a later integration of an earlier story, that integration's revision selects its suite."""
        self.build()
        # The current Delivery integrated ST-001 again, on a revision that renamed its test.
        self.write("tests/st001/test_api.py", suite("src/api/limit.txt", "test_limit_holds", "10"))
        self.plan("ST-001", scenario("ST-001-TS-001", "required", "tests/st001/test_api.py::test_limit_holds"))
        revised = self.plan_hash("ST-001")
        self.item(CURRENT, "ST-001", "integrated", ["src/api/limit.txt", "tests/st001"],
                  test_plan_source_hash=revised)
        integration = self.commit("Integrate ST-001 again")
        # A revision that no integration bound yet changes nothing.
        self.plan("ST-001", scenario("ST-001-TS-001", "required", "tests/st001/test_api.py::test_unwritten"))
        self.commit("Revise ST-001 once more")
        entry = self.selection()["earlier_stories"]
        self.assertEqual([(item["delivery"], item["story"]) for item in entry], [("DLV-001", "ST-001")])
        self.assertEqual((entry[0]["automation_targets"], entry[0].get("test_plan_source_hash"),
                          entry[0].get("test_plan_commit")),
                         (["tests/st001/test_api.py::test_limit_holds"], revised, integration))

    def test_a_target_that_is_no_literal_test_id_is_refused(self):
        self.build()
        self.plan("ST-005", scenario("ST-005-TS-001", "required", "-ktotal"))
        self.commit("An option-like target")
        with self.assertRaisesRegex(RuntimeError, "automation target -ktotal of ST-005 is no literal test id"):
            self.selection()

    def test_a_regression_in_an_earlier_story_blocks_the_freeze_until_its_repair_passes(self):
        self.build()
        missing = rf"^{CODE}: no pre-handoff regression run binds candidate tree [0-9a-f]{{40}} "
        with self.assertRaisesRegex(RuntimeError, missing):
            self.freeze()
        failed = self.run_regression()
        self.assertEqual((failed["exit_code"], failed["candidate_intact"], failed["kind"]),
                         (1, True, "diagnostic_test"))
        self.assertEqual(failed["earlier_stories"], [{"delivery": "DLV-001", "story": "ST-001"}])
        output = self.output(failed)
        self.assertIn("FAIL tests/st001/test_api.py::test_limit", output)
        self.assertIn("PASS tests/st005/test_new.py::test_total", output)
        self.assertNotIn("tests/st002", output)
        with self.assertRaisesRegex(RuntimeError, rf"^{CODE}: the latest pre-handoff regression run on"
                                                  r" candidate tree [0-9a-f]{40} exited 1"):
            self.freeze()
        self.assertFalse(verification.session_path(self.root).is_file())
        # The repair is a new candidate, so the failed run's tree no longer binds it.
        self.write("src/api/limit.txt", "10\n")
        self.commit("Repair the ST-001 regression")
        with self.assertRaisesRegex(RuntimeError, missing):
            self.freeze()
        passed = self.run_regression()
        self.assertEqual((passed["exit_code"], passed["candidate_intact"]), (0, True))
        self.assertGreaterEqual(passed["duration_seconds"], 0)
        session = self.freeze()
        self.assertEqual(session["pre_handoff"]["evidence_hash"], passed["evidence_hash"])
        self.assertEqual(session["pre_handoff"]["earlier_stories"], [{"delivery": "DLV-001", "story": "ST-001"}])
        self.assertEqual(session["pre_handoff"]["candidate_tree"], self.git("rev-parse", "HEAD^{tree}"))
        self.assertEqual(session["metrics"]["pre_handoff_seconds"], passed["duration_seconds"])
        runs = json.loads((verification.session_path(self.root).parent / "pre-handoff.json")
                          .read_text(encoding="utf-8"))["runs"]
        self.assertEqual([run["evidence_hash"] for run in runs], [failed["evidence_hash"], passed["evidence_hash"]])
        # QA can run the same selection as its diagnostic on the frozen candidate.
        selector = verification.session_path(self.root).parent / "scratch/pre-handoff-selection.json"
        diagnostic = verification.run_check(self.root, "diagnostic_test", selection_file=selector)
        self.assertEqual((diagnostic["exit_code"], diagnostic["candidate_intact"], diagnostic["selection_intact"]),
                         (0, True, True))
        self.assertEqual(diagnostic["diagnostic_selection"]["selected_test_ids"], passed["affected_test_ids"])

    def test_a_run_whose_command_rewrites_a_selection_never_passes_the_gate(self):
        """A run whose command changed the selection it received, or the one QA reads, is not intact."""
        self.build()
        received = 'os.environ["AGENTROF_DIAGNOSTIC_TESTS"]'
        shared = 'os.path.join(os.environ["AGENTROF_VERIFICATION_SCRATCH"], "pre-handoff-selection.json")'
        # Emptying the selection it received runs nothing, so the broken ST-001 suite would pass.
        for label, target, repair in (("received", received, False), ("shared", shared, True)):
            with self.subTest(selection=label):
                self.write("diagnose.py", EMPTYING.format(target=target) + ADAPTER)
                if repair:
                    self.write("src/api/limit.txt", "10\n")
                self.commit("An adapter that empties the " + label + " selection")
                run = self.run_regression()
                with self.assertRaisesRegex(RuntimeError, rf"^{CODE}: the latest pre-handoff regression run on"
                                                          r" candidate tree [0-9a-f]{40} exited 0 and changed the"
                                                          " selection it ran;"):
                    self.freeze()
                self.assertEqual((run["exit_code"], run["candidate_intact"], run.get("selection_intact")),
                                 (0, False, False))
        self.write("diagnose.py", ADAPTER)
        self.commit("Restore the approved adapter")
        run = self.run_regression()
        self.assertEqual((run["exit_code"], run["candidate_intact"], run["selection_intact"]), (0, True, True))
        self.assertIn("pre_handoff", self.freeze())

    def test_a_run_whose_checkout_leaves_a_tracked_path_out_names_it_and_never_passes_the_gate(self):
        """A private clone that leaves a tracked file out differs from its commit before the command
        runs. The run is not intact, and it and the freeze refusal name the missing path (#358)."""
        self.build()
        self.write("src/api/limit.txt", "10\n")
        self.commit("Repair the earlier story's regression")
        original = verification.clone_private_checkout

        def short(root, execution_root, commit):
            original(root, execution_root, commit)
            (execution_root / "src/web/page.txt").unlink()

        with mock.patch.object(verification, "clone_private_checkout", side_effect=short):
            run = self.run_regression()
        self.assertEqual((run["exit_code"], run["candidate_intact"], run["checkout_difference"]),
                         (0, False, "missing src/web/page.txt"))
        with self.assertRaisesRegex(RuntimeError, rf"^{CODE}: the latest pre-handoff regression run on candidate"
                                                  r" tree [0-9a-f]{40} exited 0 and changed its checkout"
                                                  r" \(missing src/web/page\.txt\);"):
            self.freeze()

    def test_a_selection_derived_differently_for_the_same_tree_needs_a_new_run(self):
        self.build()
        self.write("src/api/limit.txt", "10\n")
        self.commit("Repair the ST-001 regression")
        self.assertEqual(self.run_regression()["exit_code"], 0)
        targets = verification.plan_automation_targets

        def more(docs, relative, label):
            return [*targets(docs, relative, label), "tests/st001/test_api.py::test_other"]

        with mock.patch.object(verification, "plan_automation_targets", side_effect=more), \
                self.assertRaisesRegex(RuntimeError, f"^{CODE}: no pre-handoff regression run binds"):
            self.freeze()
        self.assertIn("pre_handoff", self.freeze())

    def test_the_run_holds_the_environment_lock_and_reads_only_the_committed_candidate(self):
        self.build()
        with verification.environment_lock(self.root, "devops_engineer", "environment --verb up"):
            with self.assertRaisesRegex(RuntimeError, "^DELIVERY_ENVIRONMENT_BUSY: the Item environment is held"
                                                      " by devops_engineer"):
                self.run_regression()
        self.assertIsNone(verification.environment_holder(self.root))
        self.write("stray.txt", "uncommitted\n")
        with self.assertRaisesRegex(RuntimeError, "commit or remove non-evidence changes"):
            self.run_regression()
        (self.root / "stray.txt").unlink()
        # An ignored worktree file and a search path outside the checkout never reach the run.
        self.write("ignored.txt", "worktree only\n")
        outside = self.root.parent / "other-checkout"
        with mock.patch.dict(os.environ, {"PYTHONPATH": str(outside)}):
            for name in ("PYTHONHOME", "NODE_PATH"):
                os.environ.pop(name, None)
            run = self.run_regression()
        self.assertEqual(run["dropped_search_paths"], {"PYTHONPATH": [str(outside)]})
        output = self.output(run)
        self.assertIn("TRACKED-ONLY", output)
        self.assertIn("PYTHONPATH=\n", output.replace("\r\n", "\n"))
        self.assertEqual(run["interrupted_holder"], None)

    @contextlib.contextmanager
    def held_run(self):
        """Run regression-run in a thread and hold it twice: inside its selection write, made while it reads
        the candidate, until the yielded function lets it go on, and inside its gated command until the block
        ends. Yields that function, which returns once the command runs, and the outcome the run leaves."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        gate = Path(temporary.name)
        reading, resume = threading.Event(), threading.Event()
        write = verification.write_pre_handoff_selection
        outcome: dict = {}

        def paused(root: Path, derived: dict):
            reading.set()
            resume.wait(60)
            return write(root, derived)

        def run() -> None:
            try:
                outcome["run"] = self.run_regression()
            except Exception as exc:  # noqa: BLE001 - reported by the caller's assertions
                outcome["error"] = exc

        def command_running() -> None:
            resume.set()
            deadline = time.monotonic() + 60
            while not (gate / "started").exists() and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue((gate / "started").exists(), outcome)

        thread = threading.Thread(target=run)
        with mock.patch.dict(os.environ, {"FIXTURE_GATE": str(gate)}), \
                mock.patch.object(verification, "write_pre_handoff_selection", side_effect=paused):
            thread.start()
            try:
                self.assertTrue(reading.wait(60), outcome)
                yield command_running, outcome
            finally:
                resume.set()
                (gate / "release").write_text("release", encoding="utf-8")
                thread.join(120)
        self.assertNotIn("error", outcome)

    def test_while_its_command_runs_the_worktree_takes_writes_and_the_environment_stays_held(self):
        """The run holds the Item's command lock only while it derives the selection and clones the candidate,
        and its environment lock to the end: a guarded write waits only while the run reads the candidate, and
        every command on the Item environment, freeze included, waits for the whole run."""
        self.build()
        self.write("diagnose.py", GATED + ADAPTER)
        self.write("src/api/limit.txt", "10\n")
        self.commit("Repair the ST-001 regression with a slow adapter")
        # An earlier run passed on this candidate, so only the run in flight keeps freeze from accepting it.
        self.assertEqual(self.run_regression()["exit_code"], 0)
        held = (r"^DELIVERY_ENVIRONMENT_BUSY: the Item environment is held by delivery_coordinator, running"
                r" `regression-run` in process [0-9]+ since [0-9T:-]+Z; ")
        other = self.root / "src/api/other.txt"
        with self.held_run() as (command_running, outcome):
            # While the run reads the candidate, a guarded write waits and names the run, not readers.
            self.assertIsNone(verification.read_session(self.root, required=False))
            for paths in ([other], None):
                with self.subTest(phase="reading", paths=paths), self.assertRaisesRegex(
                        RuntimeError, held + "writes to the Item worktree wait until it has derived its selection"
                                             " and cloned the candidate$"):
                    verification.guard_write(self.root, paths)
            verification.guard_write(self.root, [verification.session_path(self.root).parent / "scratch/a.txt"])
            command_running()
            # Its command reads only the private clone, so the Item worktree takes writes again.
            for paths in ([other], None):
                with self.subTest(phase="command", paths=paths):
                    verification.guard_write(self.root, paths)
            for name, command in (("regression-run", self.run_regression),
                                  ("environment", lambda: verification.run_environment(self.root, "up")),
                                  ("regression-selection", self.selection), ("freeze", self.freeze)):
                with self.subTest(command=name), self.assertRaisesRegex(RuntimeError,
                                                                        held + "run this after it finishes$"):
                    command()
        run = outcome["run"]
        self.assertEqual((run["exit_code"], run["candidate_intact"], run["selection_intact"]), (0, True, True))
        self.assertEqual(self.freeze()["pre_handoff"]["evidence_hash"], run["evidence_hash"])

    def test_the_run_records_the_coordinator_as_its_command_owner(self):
        """While the run holds the verification command lock, its owner record names the coordinator and the run,
        so no reader takes it for its own command, and the lock is free once the command runs (#355)."""
        self.build()
        self.write("diagnose.py", GATED + ADAPTER)
        self.write("src/api/limit.txt", "10\n")
        self.commit("Repair the ST-001 regression with a slow adapter")
        with self.held_run() as (command_running, _outcome):
            holder = verification.command_holder(self.root)
            self.assertEqual({key: holder.get(key) for key in ("role", "command", "pid")},
                             {"role": "delivery_coordinator", "command": "regression-run", "pid": os.getpid()})
            command_running()
            self.assertIsNone(verification.command_holder(self.root))

    def test_a_candidate_committed_during_the_run_needs_a_run_of_its_own(self):
        """A change committed while the run's command runs is a new candidate: the run binds the tree it
        cloned and freeze checks the current one, so freeze refuses the new candidate until a run binds it."""
        self.build()
        self.write("diagnose.py", GATED + ADAPTER)
        self.write("src/api/limit.txt", "10\n")
        self.commit("Repair the ST-001 regression with a slow adapter")
        cloned = self.git("rev-parse", "HEAD^{tree}")
        with self.held_run() as (command_running, outcome):
            command_running()
            verification.guard_write(self.root, [self.root / "src/api/notes.txt"])
            self.write("src/api/notes.txt", "written while the run ran\n")
            self.commit("A change committed while the run ran")
            committed = self.git("rev-parse", "HEAD^{tree}")
            # The new candidate's selection waits for the run, so the run keeps the selection it runs.
            with self.assertRaisesRegex(RuntimeError, "^DELIVERY_ENVIRONMENT_BUSY: .* `regression-run` "):
                self.selection()
        run = outcome["run"]
        self.assertEqual((run["candidate_tree"], run["exit_code"], run["candidate_intact"], run["selection_intact"]),
                         (cloned, 0, True, True))
        self.assertNotEqual(committed, cloned)
        with self.assertRaisesRegex(RuntimeError, f"^{CODE}: no pre-handoff regression run binds candidate tree"
                                                  f" {committed} "):
            self.freeze()
        again = self.run_regression()
        self.assertEqual((again["candidate_tree"], again["exit_code"]), (committed, 0))
        self.assertEqual(self.freeze()["pre_handoff"]["evidence_hash"], again["evidence_hash"])

    def test_readers_at_work_refuse_the_run(self):
        self.build()
        self.write("src/api/limit.txt", "10\n")
        self.commit("Repair the ST-001 regression")
        self.run_regression()
        self.freeze()
        with self.assertRaisesRegex(RuntimeError, "DELIVERY_VERIFICATION_READERS_ACTIVE"):
            self.run_regression()

    def test_a_malformed_contract_variable_list_refuses_the_run_before_its_command(self):
        """The run checks the approved contract's command_variables while it still reads the candidate, so a
        hand-edited contract costs no run of the selection and leaves no output behind."""
        self.build()
        self.write("src/api/limit.txt", "10\n")
        self.contract(command_variables="ITEM_TOKEN")
        self.commit("Repair, and a hand-edited contract")
        runtime = verification.session_path(self.root).parent
        with mock.patch.object(verification, "private_checkout_run",
                               wraps=verification.private_checkout_run) as private_run, \
                self.assertRaisesRegex(RuntimeError, "^command_variables must list unique environment variable names$"):
            self.run_regression()
        # The call count alone, so a failure prints no environment.
        self.assertEqual(private_run.call_count, 0)
        self.assertEqual(list(runtime.glob("scratch/pre-handoff-*.log")), [])
        self.assertFalse((runtime / "pre-handoff.json").exists())
        self.assertIsNone(verification.environment_holder(self.root))

    def test_without_a_diagnostic_adapter_the_full_approved_test_command_runs(self):
        self.build()
        self.contract(diagnostic_test_command=None, diagnostic_test_workdir=None)
        self.write("src/api/limit.txt", "10\n")
        self.commit("Withdraw the diagnostic adapter")
        selection = self.selection()
        self.assertEqual((selection["kind"], selection["command"]), ("test", PYTHON + " run_all.py"))
        run = self.run_regression()
        self.assertEqual((run["exit_code"], run["kind"]), (0, "test"))
        output = self.output(run)
        self.assertIn("SELECTION=False", output)
        self.assertIn("PASS tests/st002/test_web.py::test_page", output)
        self.assertEqual(self.freeze()["pre_handoff"]["kind"], "test")

    # QA's gate reuses the pre-handoff run for the earlier stories it covered on the frozen tree (#354).
    def frozen_after_a_passing_run(self, age: float = 0.0) -> tuple[dict, dict]:
        """Repair the candidate, pass its pre-handoff run, recorded *age* seconds ago, and freeze it; return the
        run and its selection."""
        self.build()
        self.write("src/api/limit.txt", "10\n")
        self.commit("Repair the earlier story's regression")
        real = time.time
        with (mock.patch.object(verification.time, "time", side_effect=lambda: real() - age) if age
              else contextlib.nullcontext()):
            passed = self.run_regression()
        self.assertEqual((passed["exit_code"], passed["candidate_intact"]), (0, True))
        current = self.freeze()["candidate"]
        selection = verification.derive_regression(self.root, current["delivery"], current["story"], current)
        self.assertEqual(len(selection["earlier_stories"]), 1)
        self.assertEqual(len(selection["own_targets"]), 1)
        return passed, selection

    def validate(self) -> dict:
        current = verification.read_session(self.root)["candidate"]
        return verification.validate(self.root, current["delivery"], current["story"])

    def suite_output(self, raw: dict) -> str:
        return verification.raw_output_path(self.root, raw["output_file"]).read_text(encoding="utf-8")

    def rewrite_session(self, change) -> None:
        session = verification.read_session(self.root)
        change(session)
        verification.write_session(self.root, session)

    def forge_test_evidence(self, change) -> None:
        """Apply *change* to the identity of QA's settled test evidence, keeping every hash that binds it."""
        def rewrite(session):
            raw = session["raw_evidence"]["test"]
            change(raw["identity"])
            raw.pop("evidence_hash")
            raw["evidence_hash"] = verification.digest(raw)
            result = session["workers"]["qa_engineer"]["result"]
            result.pop("result_hash")
            result["checks"]["full_test_suite"]["raw_evidence_hash"] = raw["evidence_hash"]
            result["result_hash"] = verification.digest(result)
        self.rewrite_session(rewrite)

    def test_qa_reuses_the_pre_handoff_run_for_the_earlier_stories_on_the_frozen_tree(self):
        passed, selection = self.frozen_after_a_passing_run()
        earlier, = selection["earlier_stories"]
        targets, own = earlier["automation_targets"], selection["own_targets"]
        raw = verification.run_check(self.root, "test")
        self.assertEqual((raw["exit_code"], raw["candidate_intact"], raw["reused"]), (0, True, False))
        self.assertEqual(raw["identity"]["reused_pre_handoff"], {
            "evidence_hash": passed["evidence_hash"], "test_ids": targets,
            "earlier_stories": [{"delivery": earlier["delivery"], "story": earlier["story"], "test_ids": targets}]})
        output = self.suite_output(raw)
        self.assertIn("REUSE=True", output)
        for target in targets:
            self.assertIn("REUSED " + target, output)
            self.assertNotIn("PASS " + target, output)
        # The Item's own story and every suite the run did not cover run as before.
        for target in own:
            self.assertIn("PASS " + target, output)
        self.assertTrue([line for line in output.splitlines()
                         if line.startswith("PASS ") and line[5:] not in selection["affected_test_ids"]], output)
        reused = json.loads((verification.session_path(self.root).parent / "reused-tests.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(reused, {"schema_version": 1, "candidate_hash": raw["identity"]["candidate_hash"],
                                  "pre_handoff_evidence_hash": passed["evidence_hash"], "reused_test_ids": targets})
        # QA may still spot-run a reused group through the diagnostic adapter.
        sample = verification.session_path(self.root).parent / "scratch/sample.json"
        sample.write_text(json.dumps({"schema_version": 1, "candidate_hash": raw["identity"]["candidate_hash"],
                                      "failed_test_ids": [], "affected_test_ids": targets}), encoding="utf-8")
        spot = verification.run_check(self.root, "diagnostic_test", selection_file=sample)
        self.assertEqual(spot["exit_code"], 0)
        for target in targets:
            self.assertIn("PASS " + target, self.suite_output(spot))
        self.assertNotIn("reused_pre_handoff", spot["identity"])
        # The reused run is QA's full-suite evidence and approves, from QA's cached record.
        self.approve_evidence()
        self.assertEqual(verification.read_session(self.root)["raw_evidence"]["test"]["evidence_hash"],
                         raw["evidence_hash"])

    def test_qa_reuses_no_pre_handoff_run_older_than_final_evidence_may_be(self):
        """A reused run stands in QA's final evidence, so QA's run reuses only a run as fresh as final evidence
        must be; an older one leaves every suite to QA's run."""
        maximum = verification.policy()["raw_evidence_max_age_seconds"]
        _passed, selection = self.frozen_after_a_passing_run(age=maximum + 600)
        raw = verification.run_check(self.root, "test")
        self.assertEqual((raw["exit_code"], raw["reused"], raw["pre_handoff_reuse"]),
                         (0, False, "the accepted pre-handoff run expired"))
        self.assertNotIn("reused_pre_handoff", raw["identity"])
        output = self.suite_output(raw)
        self.assertIn("REUSE=False", output)
        for target in selection["affected_test_ids"]:
            self.assertIn("PASS " + target, output)

    def test_approval_refuses_a_reused_run_that_expired_after_qa_ran(self):
        """Approval checks the reused run's age again, as it checks the age of QA's own record: a run young
        enough when QA's run reused it is refused once it is older than final evidence may be."""
        maximum = verification.policy()["raw_evidence_max_age_seconds"]
        passed, _selection = self.frozen_after_a_passing_run(age=maximum - 600)
        for role in ("code_reviewer", "qa_engineer"):
            verification.register_result(self.root, self.reader_result(role))
        raw = verification.read_session(self.root)["raw_evidence"]["test"]
        self.assertEqual(raw["identity"]["reused_pre_handoff"]["evidence_hash"], passed["evidence_hash"])
        self.validate()
        # Twenty minutes on, QA's own record is still fresh and the reused run is not.
        with mock.patch.object(verification.time, "time", return_value=time.time() + 1200), \
                self.assertRaisesRegex(RuntimeError, "^full_test_suite evidence reuses a pre-handoff run that"
                                                     " expired$"):
            self.validate()

    def test_a_fresh_test_run_reuses_no_pre_handoff_run(self):
        """run --fresh reruns its command, so QA's fresh final test run runs every suite itself."""
        _passed, selection = self.frozen_after_a_passing_run()
        self.assertIn("reused_pre_handoff", verification.run_check(self.root, "test")["identity"])
        raw = verification.run_check(self.root, "test", fresh=True)
        self.assertEqual((raw["exit_code"], raw["reused"], raw["pre_handoff_reuse"]),
                         (0, False, "run --fresh runs every suite itself"))
        self.assertNotIn("reused_pre_handoff", raw["identity"])
        output = self.suite_output(raw)
        self.assertIn("REUSE=False", output)
        for target in selection["affected_test_ids"]:
            self.assertIn("PASS " + target, output)

    def test_a_command_that_changes_its_reuse_list_never_passes_the_gate(self):
        """QA's run checks after its command that the reuse list it handed over is unchanged, as it checks a
        diagnostic selection: a command that widened the list skipped a suite the run never covered."""
        self.build()
        self.write("src/api/limit.txt", "10\n")
        # A suite no Test Plan names, which the candidate breaks, so a full run fails.
        self.write("tests/misc/test_misc.py", suite("src/api/total.txt", "test_misc", "31"))
        self.write("run_all.py", WIDENING + FULL_SUITE)
        self.commit("Repair, and a test command that widens its reuse list")
        self.assertEqual(self.run_regression()["exit_code"], 0)
        self.freeze()
        raw = verification.run_check(self.root, "test")
        self.assertIn("REUSED tests/misc/test_misc.py::test_misc", self.suite_output(raw))
        self.assertEqual((raw["exit_code"], raw["candidate_intact"], raw["selection_intact"]), (0, False, False))
        self.assertFalse(verification.run_check(self.root, "test")["reused"])
        verification.register_result(self.root, self.reader_result("code_reviewer"))
        with self.assertRaisesRegex(RuntimeError, "^full_test_suite requires successful same-candidate command"
                                                  " evidence from run$"):
            verification.register_result(self.root, self.reader_result("qa_engineer"))

    def test_the_verification_record_names_what_qas_final_test_run_reused(self):
        """approve-item-evidence records the earlier stories, test ids and run that QA's final test run reused,
        since the runtime that holds the reuse is disposable and the owner measures it per Delivery."""
        passed, selection = self.frozen_after_a_passing_run()
        earlier, = selection["earlier_stories"]
        section = delivery.section_bodies(self.approve_evidence())["Implementation Evidence"]
        self.assertTrue(section.endswith("\n".join([
            "", REUSED_MARKER, "", "| story | test_ids | pre_handoff_run | evidence_hash |", "|---|---|---|---|",
            f"| {earlier['story']} of {earlier['delivery']} | {', '.join(earlier['automation_targets'])} | 1"
            f" | {passed['evidence_hash']} |"])), section)

    def test_the_verification_record_says_when_qas_final_test_run_reused_nothing(self):
        self.frozen_after_a_passing_run()
        section = delivery.section_bodies(self.approve_evidence(fresh=True))["Implementation Evidence"]
        self.assertTrue(section.endswith("\n\n" + REUSED_MARKER + " none."), section)

    def test_a_binding_the_frozen_session_does_not_hold_runs_the_full_suite(self):
        passed, selection = self.frozen_after_a_passing_run()
        targets = verification.plan_automation_targets

        def more(docs, relative, label):
            return [*targets(docs, relative, label), "tests/unselected/test_more.py::test_more"]

        def receipt(**changes):
            return lambda session: session["pre_handoff"].update(changes)

        cases = (
            ("declared environment", "the pre-handoff run's declared environment differs from this run's",
             lambda: mock.patch.dict(os.environ, {"TZ": "Pacific/Auckland",
                                                  "AGENTROF_REUSED_TESTS": "inherited.json"}), None),
            ("selection", "the pre-handoff run's selection differs from the frozen candidate's",
             lambda: mock.patch.object(verification, "plan_automation_targets", side_effect=more), None),
            ("candidate tree", "the pre-handoff run's candidate tree differs from the frozen candidate's",
             contextlib.nullcontext, receipt(candidate_tree="0" * 40)),
            ("approved command", "the pre-handoff run's approved command differs from the frozen candidate's",
             contextlib.nullcontext, receipt(command=PYTHON + " other.py")),
            ("failed run", "the accepted pre-handoff run did not pass intact",
             contextlib.nullcontext, receipt(exit_code=1)),
            ("no run", "the frozen session holds no accepted pre-handoff run",
             contextlib.nullcontext, lambda session: session.pop("pre_handoff")),
        )
        earlier = selection["earlier_stories"][0]["automation_targets"]
        original = verification.session_path(self.root).read_bytes()
        for label, reason, context, change in cases:
            with self.subTest(binding=label):
                verification.session_path(self.root).write_bytes(original)
                if change is not None:
                    self.rewrite_session(change)
                with context():
                    raw = verification.run_check(self.root, "test")
                self.assertEqual((raw["exit_code"], raw["reused"], raw["pre_handoff_reuse"]), (0, False, reason))
                self.assertNotIn("reused_pre_handoff", raw["identity"])
                output = self.suite_output(raw)
                self.assertIn("REUSE=False", output)
                for target in earlier:
                    self.assertIn("PASS " + target, output)
        verification.session_path(self.root).write_bytes(original)
        raw = verification.run_check(self.root, "test")
        self.assertEqual(raw["identity"]["reused_pre_handoff"]["evidence_hash"], passed["evidence_hash"])

    def test_approval_refuses_a_reuse_the_frozen_session_does_not_bind(self):
        _passed, selection = self.frozen_after_a_passing_run()
        for role in ("code_reviewer", "qa_engineer"):
            verification.register_result(self.root, self.reader_result(role))
        self.validate()

        def adding(target):
            def change(reuse):
                reuse["test_ids"] = sorted([*reuse["test_ids"], target])
                reuse["earlier_stories"][0]["test_ids"] = list(reuse["test_ids"])
            return change

        original = verification.session_path(self.root).read_bytes()
        for message, change in (
                ("reuses a pre-handoff run the frozen session does not hold",
                 lambda reuse: reuse.update(evidence_hash="sha256:" + "0" * 64)),
                ("reuses test ids the pre-handoff run did not select",
                 adding("tests/unselected/test_more.py::test_more")),
                ("reuses a test id that is, prefixes or lies under one of the Item's own Test Plan targets, which"
                 " QA runs itself", adding(selection["own_targets"][0])),
                ("names its reused test ids apart from their earlier stories",
                 lambda reuse: reuse.update(test_ids=[]))):
            with self.subTest(message=message):
                verification.session_path(self.root).write_bytes(original)
                self.forge_test_evidence(lambda identity: change(identity["reused_pre_handoff"]))
                with self.assertRaisesRegex(RuntimeError, re.escape("full_test_suite evidence " + message)):
                    self.validate()
        # The frozen session's receipt of the reused run is checked as recorded too.
        intact = "reuses a pre-handoff run that did not pass intact on the frozen tree"
        command = "reuses a pre-handoff run of another command than the approved one"
        for message, changes in (
                (intact, {"exit_code": 1}), (intact, {"candidate_intact": False}),
                (intact, {"candidate_tree": "0" * 40}),
                (command, {"command": PYTHON + " other.py"}), (command, {"workdir": "tests"}),
                (command, {"kind": "mutation"}),
                ("reuses a pre-handoff run of another declared environment",
                 {"environment_hash": "hmac-sha256:" + "0" * 64})):
            with self.subTest(message=message, receipt=changes):
                verification.session_path(self.root).write_bytes(original)
                self.rewrite_session(lambda session: session["pre_handoff"].update(changes))
                with self.assertRaisesRegex(RuntimeError, "^" + re.escape("full_test_suite evidence " + message) + "$"):
                    self.validate()
        verification.session_path(self.root).write_bytes(original)
        self.validate()

    def test_no_earlier_target_that_prefixes_an_own_target_is_reused(self):
        """A command that skips the reused ids by node id prefix, as pytest's --deselect does, also skips an own
        target that an earlier target prefixes, so no such earlier target is reused, and approval refuses a
        reuse that holds one."""
        self.build()
        self.write("src/api/limit.txt", "10\n")
        # The Item's own target lives in the earlier story's test module, under the earlier target's name.
        self.write("tests/st001/test_api.py", suite("src/api/limit.txt", "test_limit", "10")
                   + "\n\ndef test_limit_total():\n"
                     "    assert pathlib.Path('src/api/total.txt').read_text(encoding='utf-8').strip() == '30'\n")
        self.plan("ST-005", scenario("ST-005-TS-001", "required", "tests/st001/test_api.py::test_limit_total"))
        self.write("run_all.py", PREFIX_SKIP)
        self.commit("An own target beside the earlier one, and a test command that skips by prefix")
        passed = self.run_regression()
        self.assertEqual(passed["exit_code"], 0, self.output(passed))
        self.freeze()
        raw = verification.run_check(self.root, "test")
        self.assertEqual(raw["pre_handoff_reuse"],
                         "the pre-handoff run covered no earlier story's target beyond the Item's own")
        output = self.suite_output(raw)
        for target in ("tests/st001/test_api.py::test_limit", "tests/st001/test_api.py::test_limit_total"):
            self.assertIn("PASS " + target, output)
        for role in ("code_reviewer", "qa_engineer"):
            verification.register_result(self.root, self.reader_result(role))
        self.validate()
        earlier = ["tests/st001/test_api.py::test_limit"]
        self.forge_test_evidence(lambda identity: identity.update(reused_pre_handoff={
            "evidence_hash": passed["evidence_hash"], "test_ids": earlier,
            "earlier_stories": [{"delivery": "DLV-001", "story": "ST-001", "test_ids": earlier}]}))
        with self.assertRaisesRegex(RuntimeError, "^full_test_suite evidence reuses a test id that is, prefixes or"
                                                  " lies under one of the Item's own Test Plan targets, which QA"
                                                  " runs itself$"):
            self.validate()

    def test_the_command_line_reports_the_run_and_the_refusal(self):
        self.build()

        def main(*argv: str) -> tuple[int, dict]:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = verification.main(["--worktree", str(self.root), *argv,
                                          "--delivery", "DLV-002", "--story", "ST-005"])
            return code, json.loads(output.getvalue())

        code, result = main("regression-selection")
        self.assertEqual((code, result["ok"], result["kind"]), (0, True, "diagnostic_test"))
        code, result = main("regression-run")
        self.assertEqual((code, result["ok"], result["exit_code"]), (1, True, 1))
        code, result = main("freeze")
        self.assertEqual((code, result["ok"]), (2, False))
        envelope = delivery_result.from_raw("freeze", result)
        self.assertEqual(envelope["findings"][0]["code"], CODE)


if __name__ == "__main__":
    unittest.main()
