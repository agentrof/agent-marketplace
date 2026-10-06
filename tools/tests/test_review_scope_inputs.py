"""Review scope inputs: at `review_scope` `full`, the default, every task,
review manifest and verification manifest is derived as released. At
`impact_closure` a reader reads the change's impact closure, lists every proven
unchanged note with its hashes instead of reading it, starts from the vault's
relation views and carries a `beyond_closure` list for reads past the closure;
a confirmation re-check reads only the fixed lines' notes and what the fix
touches; a writer starts from the vault views (#441).

`impact_closure.py` owns the relation graph; these tests replace it with a stub
whose closure is the changed notes plus a fixed dependents map.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
sys.path.insert(0, str(ROOT / "tools/tests"))
import backlog_compile as backlog  # noqa: E402
import backlog_review_inputs as inputs  # noqa: E402
import delivery_verification as verification  # noqa: E402
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
import test_backlog_review_speed as speed  # noqa: E402
import test_delivery_verification as verification_tests  # noqa: E402
from test_default_equivalence import (FIXTURE_SWITCHES, build_task_package,  # noqa: E402
                                      build_task_project)

SWITCH, VALUE = "review_scope", "impact_closure"
VIEWS = {"maps/_generated/relation-status.md": "Which relations each note declares and lacks."}


def switch_spec(flows: list[str]) -> dict:
    return {"summary": "How much of a package a reader reads.", "flows": flows,
            "values": [{"id": "full", "tradeoffs": "Today's reads."},
                       {"id": VALUE, "tradeoffs": "Reads scale with the change."}],
            "default": "full", "metric": "Reader minutes per review.",
            "promotion": {"unit": "3 runs", "threshold": "Same verdicts."}}


def with_switch():
    """Declare review_scope in the shipped registry until it ships there."""
    original = process_policy.load_registry

    def load(package=None):
        registry = original(package)
        if SWITCH not in registry:
            spec = switch_spec(["backlog-planning"])
            registry[SWITCH] = {"values": [row["id"] for row in spec["values"]],
                                "default": "full", "spec": spec}
        return registry
    return mock.patch.object(process_policy, "load_registry", load)


class Stub:
    """The documented impact_closure API over a fixed dependents map of docs paths."""

    def __init__(self, dependents=None, proven=(), gaps=(), widened=()):
        self.dependents = dependents or {}
        self.proven = list(proven)
        self.gaps = list(gaps)
        self.widened = list(widened)
        self.calls = []

    def closure(self, docs, changed, *, policy=None):
        self.calls.append(list(changed))
        reach = set(changed)
        for path in changed:
            reach |= set(self.dependents.get(path, []))
        return {"changed": list(changed), "closure": sorted(reach),
                "proven_unchanged": [{"path": path, "approval_hash": "sha256:approved-" + path}
                                     for path in self.proven if path not in reach],
                "widened_by": self.widened, "graph_gaps": self.gaps}

    def vault_views(self, docs):
        return dict(VIEWS)

    def record_beyond(self, manifest, path, reason):
        manifest.setdefault("beyond_closure", []).append({"path": path, "reason": reason})
        return manifest

    def install(self):
        module = types.ModuleType("impact_closure")
        for name in ("closure", "vault_views", "record_beyond"):
            setattr(module, name, getattr(self, name))
        return mock.patch.dict(sys.modules, {"impact_closure": module})


def quiet_policy(docs: Path, package: Path | None, *argv: str) -> None:
    output = io.StringIO()
    patch = (mock.patch.object(process_policy, "PACKAGE", package) if package
             else contextlib.nullcontext())
    with patch, contextlib.redirect_stdout(output):
        code = process_policy.main([argv[0], "--docs", str(docs), *argv[1:]])
    if code:
        raise AssertionError(output.getvalue())


def choose(docs: Path, value: str | None, package: Path | None = None) -> None:
    quiet_policy(docs, package, "begin-revision" if process_policy.path_for(docs).exists() else "init")
    if value is None:
        quiet_policy(docs, package, "set", "--switch", SWITCH, "--default")
    else:
        quiet_policy(docs, package, "set", "--switch", SWITCH, "--value", value)
    quiet_policy(docs, package, "approve")


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def commit(root: Path) -> None:
    git(root, "add", "--all")
    git(root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture")


NOTES = {name: f"workspace/docs/package/{name}.md" for name in ("a", "b", "c", "d")}


class TaskInputScopeTests(unittest.TestCase):
    """A reader given notes a, b, c of one package; d depends on a."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        self.package, self.project = base / "package", base / "project"
        build_task_package(self.package, switch_files=False)
        registry = json.loads(json.dumps(FIXTURE_SWITCHES))
        registry["switches"][SWITCH] = switch_spec(["fixture-flow"])
        (self.package / process_policy.REGISTRY).write_text(json.dumps(registry, indent=2) + "\n",
                                                            encoding="utf-8")
        build_task_project(self.project, {path: f"---\ntype: note\n---\n\n# {name}\n\nLine one.\n"
                                                f"Line two.\nLine three.\n"
                                          for name, path in NOTES.items()})
        self.docs = self.project / "workspace/docs"
        self.stub = Stub(dependents={"package/a.md": ["package/d.md"]},
                         proven=["package/b.md", "package/c.md"])
        patch = self.stub.install()
        patch.start()
        self.addCleanup(patch.stop)
        self.reader = dict(entry="fixture-entry", role="fixture-reader", mode="review",
                           project=self.project, package=self.package,
                           inputs=[NOTES["a"], NOTES["b"], NOTES["c"]])
        self.writer = dict(self.reader, role="fixture-writer", mode="revise")

    def edit(self, name: str, old: str = "Line two.", new: str = "Line two, fixed.") -> None:
        path = self.project / NOTES[name]
        path.write_text(path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")

    def read_paths(self, result: dict) -> list[str]:
        return [row["path"] for row in result["project_inputs"]]

    def test_full_and_an_unregistered_switch_derive_the_released_manifest(self):
        choose(self.docs, None, self.package)
        plain = {name: task_inputs.manifest(**kwargs)
                 for name, kwargs in (("reader", self.reader), ("writer", self.writer))}
        # The same project under a registry that never declared the switch.
        registry = json.loads(json.dumps(FIXTURE_SWITCHES))
        (self.package / process_policy.REGISTRY).write_text(json.dumps(registry, indent=2) + "\n",
                                                            encoding="utf-8")
        for name, kwargs in (("reader", self.reader), ("writer", self.writer)):
            with self.subTest(task=name):
                released = task_inputs.manifest(**kwargs)
                self.assertEqual({key: value for key, value in plain[name].items()
                                  if key not in {"instructions", "source_hash"}},
                                 {key: value for key, value in released.items()
                                  if key not in {"instructions", "source_hash"}})
                self.assertNotIn(SWITCH, plain[name])
                self.assertNotIn("vault_views", plain[name])
        self.assertEqual(self.stub.calls, [])
        with self.assertRaisesRegex(ValueError, "--changed names the change set"):
            task_inputs.manifest(**self.reader, changed=[NOTES["a"]])

    def test_a_reader_reads_the_closure_and_lists_proven_notes_by_hash(self):
        choose(self.docs, VALUE, self.package)
        self.edit("a")
        result = task_inputs.manifest(**self.reader)
        self.assertEqual(self.stub.calls, [["package/a.md"]])
        self.assertEqual(result[SWITCH], VALUE)
        self.assertEqual(result["vault_views"], VIEWS)
        scope = result[VALUE]
        self.assertEqual(scope["read"], "closure")
        self.assertEqual(scope["closure"], [NOTES["a"], NOTES["d"]])
        self.assertEqual(scope["beyond_closure"], [])
        self.assertEqual(self.read_paths(result),
                         sorted([NOTES["a"], NOTES["d"], "workspace/docs/delivery/process-policy.md"]))
        proven = {row["path"]: row for row in scope["proven_unchanged"]}
        self.assertEqual(sorted(proven), [NOTES["b"], NOTES["c"]])
        self.assertEqual(proven[NOTES["b"]]["approval_hash"], "sha256:approved-package/b.md")
        self.assertEqual(proven[NOTES["b"]]["sha256"], hashlib.sha256(
            (self.project / NOTES["b"]).read_bytes()).hexdigest())
        conditions = [row["condition"] for row in result["next_transition_conditions"]]
        self.assertEqual(conditions[:2], ["vault_views_first", "impact_closure_reads"])
        # A proven note's bytes bind the manifest it is listed in unread.
        task_inputs.manifest(**self.reader, expected_hash=result["source_hash"])
        self.edit("b", "Line one.", "Line one changed.")
        with self.assertRaisesRegex(ValueError, "stale"):
            task_inputs.manifest(**self.reader, expected_hash=result["source_hash"])

    def test_an_input_the_closure_cannot_prove_unchanged_stays_a_full_read(self):
        choose(self.docs, VALUE, self.package)
        self.stub.proven = ["package/b.md"]
        self.stub.gaps = ["package/c.md"]
        self.edit("a")
        result = task_inputs.manifest(**self.reader)
        self.assertIn(NOTES["c"], self.read_paths(result))
        self.assertNotIn(NOTES["b"], self.read_paths(result))
        self.assertEqual(result[VALUE]["graph_gaps"], [NOTES["c"]])

    def test_without_a_change_the_reader_reads_every_input(self):
        choose(self.docs, VALUE, self.package)
        commit(self.project)
        result = task_inputs.manifest(**self.reader)
        self.assertEqual(result[VALUE]["read"], "full")
        self.assertTrue({NOTES["a"], NOTES["b"], NOTES["c"]} <= set(self.read_paths(result)))
        # A named change starts the closure even with a clean worktree.
        named = task_inputs.manifest(**self.reader, changed=[NOTES["c"]])
        self.assertEqual(named[VALUE]["changed"], [NOTES["c"]])
        self.assertNotIn(NOTES["b"], self.read_paths(named))

    def test_a_writer_starts_from_the_views_and_keeps_its_inputs(self):
        plain = task_inputs.manifest(**self.writer)
        choose(self.docs, VALUE, self.package)
        self.edit("a")
        result = task_inputs.manifest(**self.writer)
        self.assertEqual(result["vault_views"], VIEWS)
        self.assertNotIn(VALUE, result)
        self.assertEqual(result["next_transition_conditions"][0]["condition"], "vault_views_first")
        self.assertEqual(set(self.read_paths(result)) - set(self.read_paths(plain)),
                         {"workspace/docs/delivery/process-policy.md"})

    def test_a_recheck_reads_only_the_fixed_lines_notes_and_what_the_fix_touches(self):
        choose(self.docs, VALUE, self.package)
        commit(self.project)
        reviewed = git(self.project, "rev-parse", "HEAD")
        self.edit("a")
        commit(self.project)
        findings = self.project / ".agentrof/findings.json"
        findings.parent.mkdir()
        findings.write_text(json.dumps({"findings": [{"id": "F-1", "repair": "Fix line two."}]}),
                            encoding="utf-8")
        result = task_inputs.manifest(**self.reader, base=reviewed,
                                      findings=".agentrof/findings.json")
        scope = result[VALUE]
        self.assertEqual(scope["read"], "delta")
        self.assertEqual(scope["base"], reviewed)
        self.assertEqual(scope["fixed"], [{"path": NOTES["a"], "lines": [[8, 8]]}])
        self.assertEqual(scope["touches"], [NOTES["d"]])
        self.assertEqual(self.read_paths(result), sorted([
            ".agentrof/findings.json", NOTES["a"], NOTES["d"],
            "workspace/docs/delivery/process-policy.md"]))
        self.assertEqual([row["path"] for row in scope["unchanged_since_base"]], [])
        self.assertEqual(sorted(row["path"] for row in scope["proven_unchanged"]),
                         [NOTES["b"], NOTES["c"]])
        self.assertIn("impact_closure_reads",
                      [row["condition"] for row in result["next_transition_conditions"]])
        # An input the stub cannot prove is still unchanged since the reviewed commit.
        self.stub.proven = []
        unproven = task_inputs.manifest(**self.reader, base=reviewed,
                                        findings=".agentrof/findings.json")
        self.assertEqual([row["path"] for row in unproven[VALUE]["unchanged_since_base"]],
                         [NOTES["b"], NOTES["c"]])
        self.assertNotIn(NOTES["b"], self.read_paths(unproven))

    def test_the_switch_needs_the_closure_module(self):
        choose(self.docs, VALUE, self.package)
        self.edit("a")
        with mock.patch.dict(sys.modules, {"impact_closure": None}):
            with self.assertRaisesRegex(ValueError, "needs scripts/impact_closure.py"):
                task_inputs.manifest(**self.reader)


class ContextPackTests(unittest.TestCase):
    """At context_pack role_digest a task binds the role digest instead of its required reads."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        self.package, self.project = base / "package", base / "project"
        build_task_package(self.package, switch_files=False)
        registry = json.loads(json.dumps(FIXTURE_SWITCHES))
        spec = switch_spec(["fixture-flow"])
        spec["values"] = [{"id": "off", "tradeoffs": "Full sources."},
                          {"id": "role_digest", "tradeoffs": "One digest."}]
        spec["default"] = "off"
        registry["switches"]["context_pack"] = spec
        (self.package / process_policy.REGISTRY).write_text(json.dumps(registry, indent=2) + "\n",
                                                            encoding="utf-8")
        build_task_project(self.project)
        self.docs = self.project / "workspace/docs"
        self.task = dict(entry="fixture-entry", role="fixture-reader", mode="review",
                         project=self.project, package=self.package,
                         inputs=["workspace/docs/brief.md"])
        self.stale = False
        module = types.ModuleType("context_pack")

        class Refused(Exception):
            pass

        def build(*, entry, role, mode, project, package):
            # As the pack lane's build does: its sources are this task's required reads.
            inner = task_inputs.manifest(entry=entry, role=role, mode=mode, project=project,
                                         package=package)
            self.assertNotIn("context_pack", inner)
            pack = {"entry": entry, "role": role, "mode": mode,
                    "sources": [{"path": path} for path in inner["required_reads"]]}
            pack["pack_hash"] = "sha256:" + hashlib.sha256(
                json.dumps(pack, sort_keys=True).encode()).hexdigest()
            return pack

        def check(pack, *, project=None, package=None):
            self.checked = pack["pack_hash"]
            if self.stale:
                raise Refused("the pack no longer matches its sources; rebuild it")
            return {"ok": True}

        module.Refused, module.build, module.check = Refused, build, check
        patch = mock.patch.dict(sys.modules, {"context_pack": module})
        patch.start()
        self.addCleanup(patch.stop)

    def choose(self, value):
        quiet_policy(self.docs, self.package,
                     "begin-revision" if process_policy.path_for(self.docs).exists() else "init")
        quiet_policy(self.docs, self.package, "set", "--switch", "context_pack", "--value", value)
        quiet_policy(self.docs, self.package, "approve")

    def test_off_binds_the_required_reads(self):
        self.choose("off")
        result = task_inputs.manifest(**self.task)
        self.assertTrue(result["required_reads"])
        self.assertNotIn("context_pack", result)

    def test_role_digest_binds_the_pack_and_checks_it_before_a_result_is_kept(self):
        self.choose("off")
        reads = task_inputs.manifest(**self.task)["required_reads"]
        self.choose("role_digest")
        result = task_inputs.manifest(**self.task)
        self.assertEqual(result["required_reads"], [])
        self.assertEqual([row["path"] for row in result["context_pack"]["sources"]], reads)
        self.assertTrue(result["context_pack"]["pack_hash"].startswith("sha256:"))
        self.assertEqual(result["next_transition_conditions"][0]["condition"], "context_pack_check")
        self.assertEqual(task_inputs.manifest(**self.task, expected_hash=result["source_hash"]),
                         result)
        self.assertEqual(self.checked, result["context_pack"]["pack_hash"])
        self.stale = True
        with self.assertRaisesRegex(ValueError, "context pack refused"):
            task_inputs.manifest(**self.task, expected_hash=result["source_hash"])


class BacklogScopeTests(unittest.TestCase):
    """The four-story revision-2 backlog of the root review scope tests."""

    setUp = speed.RootReviewScopeTests.setUp
    reopen = speed.RootReviewScopeTests.reopen
    path = speed.RootReviewScopeTests.path
    depends = speed.RootReviewScopeTests.depends
    EPIC = speed.RootReviewScopeTests.EPIC

    def choose(self, value):
        with with_switch():
            choose(self.docs, value)

    def manifest(self, **kwargs):
        with with_switch():
            return inputs.manifest(self.docs, **kwargs)

    def stub(self, **kwargs) -> Stub:
        stub = Stub(**kwargs)
        patch = stub.install()
        patch.start()
        self.addCleanup(patch.stop)
        return stub

    def test_full_reads_as_released(self):
        self.depends(3, 4)
        stub = self.stub()
        plain = {name: inputs.manifest(self.docs, **kwargs) for name, kwargs in (
            ("root", {}), ("epic", {"epic": "EP-001"}), ("writer", {"epic": "EP-001", "writer": True}))}
        self.choose(None)
        for name, kwargs in (("root", {}), ("epic", {"epic": "EP-001"}),
                             ("writer", {"epic": "EP-001", "writer": True})):
            with self.subTest(scope=name):
                self.assertEqual(self.manifest(**kwargs), plain[name])
        self.assertEqual(stub.calls, [])

    def test_a_reader_reads_the_changed_story_and_its_closure_only(self):
        self.depends(3, 4)
        full = inputs.manifest(self.docs, epic="EP-001")
        stub = self.stub(dependents={self.path(3): [self.path(4)]},
                         proven=[self.path(1), self.path(2)])
        self.choose(VALUE)
        for kwargs in ({"epic": "EP-001"}, {}):
            with self.subTest(**kwargs):
                value = self.manifest(**kwargs)
                self.assertEqual(stub.calls[-1], [self.path(3), self.path(3, "test-plan")])
                self.assertEqual(value[SWITCH], VALUE)
                self.assertEqual(value["vault_views"], VIEWS)
                scope = value[VALUE]
                self.assertEqual(scope["read"], "closure")
                self.assertEqual(scope["beyond_closure"], [])
                self.assertNotIn("reads", scope)
                self.assertNotIn("stories", scope)
                for number in (3, 4):
                    self.assertIn(self.path(number), value["paths"])
                for number in (1, 2):
                    for name in ("story", "test-plan"):
                        self.assertNotIn(self.path(number, name), value["paths"])
                self.assertEqual(sorted(row["path"] for row in scope["proven_unchanged"]),
                                 [self.path(1), self.path(2)])
                graph = value["check"]["backlog_graph"]["stories"]
                self.assertEqual({identity: row["read"] for identity, row in graph.items()},
                                 {"ST-001": "summary", "ST-002": "summary", "ST-003": "full",
                                  "ST-004": "full"})
                self.assertEqual(self.manifest(expected_hash=value["source_hash"], **kwargs), value)
        self.assertLess(len(self.manifest(epic="EP-001")["paths"]), len(full["paths"]))

    def test_adding_unchanged_approved_stories_leaves_the_read_set_unchanged(self):
        self.depends(3, 4)
        self.stub(dependents={self.path(3): [self.path(4)]})
        self.choose(VALUE)
        small = self.manifest(epic="EP-001")
        # The same revision of a package with two more approved, unchanged stories.
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.docs = Path(temporary.name) / "workspace/docs"
        (self.docs / "maps").mkdir(parents=True)
        (self.docs.parent / "config.json").write_text(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English"}), encoding="utf-8")
        speed.make_approved_backlog(self.docs, *(f"ST-{number:03d}" for number in range(1, 7)))
        self.reopen(2)
        self.depends(3, 4)
        self.choose(VALUE)
        large = self.manifest(epic="EP-001")
        self.assertEqual(large["paths"], small["paths"])
        # The two new stories appear only as hash-bound summaries.
        self.assertEqual(len(small["check"]["backlog_graph"]["stories"]), 4)
        self.assertEqual({identity: row["read"] for identity, row
                          in large["check"]["backlog_graph"]["stories"].items()
                          if identity in {"ST-005", "ST-006"}},
                         {"ST-005": "summary", "ST-006": "summary"})

    def test_a_first_revision_reads_the_whole_package(self):
        self.reopen(1)
        self.stub()
        self.choose(VALUE)
        value = self.manifest()
        self.assertEqual(value[VALUE], {"read": "full", "reason": "first backlog revision",
                                        "beyond_closure": []})
        for number in range(1, 5):
            self.assertIn(self.path(number, "test-plan"), value["paths"])

    def test_a_writer_starts_from_the_views_and_reads_as_released(self):
        self.depends(3, 4)
        stub = self.stub()
        plain = inputs.manifest(self.docs, epic="EP-001", writer=True)
        self.choose(VALUE)
        value = self.manifest(epic="EP-001", writer=True)
        self.assertEqual(value["vault_views"], VIEWS)
        self.assertNotIn(VALUE, value)
        self.assertEqual(value["paths"], plain["paths"])
        self.assertEqual(stub.calls, [])


class VerificationScopeTests(unittest.TestCase):
    """The verification fixture with two architecture notes, one the story reaches."""

    setUp = verification_tests.VerificationTests.setUp
    write = verification_tests.VerificationTests.write
    note = verification_tests.VerificationTests.note
    commit = verification_tests.VerificationTests.commit
    freeze = verification_tests.VerificationTests.freeze

    REACHED = "workspace/docs/system-architecture/auth.md"
    OTHER = "workspace/docs/system-architecture/billing.md"

    def prepare(self):
        """Approve the architecture before the Item's base, so the Item changes only product code."""
        for path in (self.REACHED, self.OTHER):
            self.note(path, {"status": "approved"})
        self.commit()
        item = self.root / self.item_path
        props, body = verification.delivery.split_note(item)
        props["integration_base_commit"] = verification.git(self.root, "rev-parse", "HEAD")
        self.write(self.item_path, verification.delivery.frontmatter(props, body))
        self.write("src/product.py", "value = 3\n")
        self.commit()
        self.freeze()

    def test_full_reads_every_bound_input(self):
        self.prepare()
        released = verification.manifest(self.root, "DLV-001", "AUTH-01", "qa_engineer", "qa_final")
        self.assertIn(self.OTHER, released["full_read"])
        self.assertNotIn(SWITCH, released)
        self.assertEqual(verification.review_scope(self.root, "DLV-001"), "full")

    def test_the_policy_value_reaches_the_verification_manifest(self):
        self.prepare()
        with with_switch():
            choose(self.root / "workspace/docs", VALUE)
            self.assertEqual(verification.review_scope(self.root, "DLV-001"), VALUE)

    def test_a_reader_reads_the_item_closure_and_lists_other_architecture_by_hash(self):
        self.prepare()
        stub = Stub(dependents={"backlog/story.md": ["system-architecture/auth.md"]})
        with stub.install(), mock.patch.object(verification, "review_scope", return_value=VALUE):
            for role, mode in (("code_reviewer", "review_initial"), ("qa_engineer", "qa_final")):
                with self.subTest(role=role):
                    value = verification.manifest(self.root, "DLV-001", "AUTH-01", role, mode)
                    self.assertIn(self.REACHED, value["full_read"])
                    self.assertNotIn(self.OTHER, value["full_read"])
                    self.assertIn("src/product.py", value["full_read"])
                    self.assertIn("workspace/docs/backlog/story.md", value["full_read"])
                    scope = value[VALUE]
                    self.assertEqual(scope["unread_inputs"], [{
                        "path": self.OTHER, "sha256": value["inputs"][self.OTHER]}])
                    self.assertEqual(scope["beyond_closure"], [])
                    self.assertEqual(value["vault_views"], VIEWS)
        self.assertIn("operation/verification-contract.md", stub.calls[0])


class RealClosureTests(unittest.TestCase):
    """task_inputs over the shipped impact_closure module, no stub: its gap
    rows and widening rows reach the manifest as docs paths under the prefix."""

    def setUp(self):
        import test_impact_closure as closure_tests
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.docs = Path(temporary.name) / "docs"
        for rel, text in closure_tests.VAULT.items():
            (self.docs / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.docs / rel).write_text(text, encoding="utf-8")
        self.digest = closure_tests.stamp(self.docs / "backlog/story-g.md")
        self.contract = closure_tests.CONTRACT

    def test_the_real_closure_reaches_the_manifest_with_paths_under_the_prefix(self):
        prefix = "workspace/docs/"
        scope = task_inputs.impact_closure(self.docs, [prefix + self.contract + ".md"], prefix)
        self.assertEqual(scope["changed"], [prefix + self.contract + ".md"])
        self.assertIn(prefix + "backlog/story-e.md", scope["closure"])
        self.assertEqual(scope["widened_by"], [{
            "path": prefix + self.contract + ".md", "reason": "shared_contract",
            "citers": [prefix + "backlog/story-e.md", prefix + "backlog/story-f.md"]}])
        self.assertIn(prefix + "backlog/story-g.md", scope["graph_gaps"])
        story_g = self.docs / "backlog/story-g.md"
        self.assertEqual(scope["proven_unchanged"], [{
            "path": prefix + "backlog/story-g.md", "approval_hash": self.digest,
            "sha256": hashlib.sha256(story_g.read_bytes()).hexdigest()}])
        self.assertEqual(scope["beyond_closure"], [])
        self.assertEqual(sorted(task_inputs.vault_views(self.docs)), ["docs", "views"])


if __name__ == "__main__":
    unittest.main()
