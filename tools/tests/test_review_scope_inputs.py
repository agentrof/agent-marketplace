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
import os
import subprocess
import sys
import tempfile
import types
import unittest
from tools.tests.levels import integration
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
sys.path.insert(0, str(ROOT / "tools/tests"))
import process_policy  # noqa: E402
import task_inputs  # noqa: E402
from git_fixture import init_repository  # noqa: E402

GIT_ENV = {"GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
           "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
           "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
           "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00"}

FIXTURE_SWITCHES = {"schema_version": 1, "switches": {"fixture_mode": {
    "summary": "How the fixture step runs.", "flows": ["fixture-flow"],
    "values": [{"id": "current", "tradeoffs": "Today's behaviour."},
               {"id": "fast", "tradeoffs": "Fewer passes, unmeasured recall."}],
    "default": "current", "metric": "Minutes per fixture step.",
    "promotion": {"unit": "3 Deliveries", "threshold": "Half the baseline minutes."}}}}
def build_task_package(package: Path, *, resolver: str = "unresolved") -> None:
    """Write the smallest package the task-input catalog accepts."""
    catalog = {
        "schema_version": 1, "modes": ["create", "revise", "consume", "review", "repair"],
        "role_skills": {"fixture_writer": ["fixture-method", "obsidian-vault"],
                        "fixture_reader": ["fixture-method"]},
        "required_role_skills": {"fixture_writer": ["fixture-method"],
                                 "fixture_reader": ["fixture-method"]},
        "entries": {"fixture_entry": {
            "flows": ["fixture-flow"], "roles": ["fixture-writer", "fixture-reader"],
            "scope_kind": "fixture", "project_state": True,
            "write_scope": {"resolver": resolver,
                            "roles": [] if resolver == "unresolved" else ["fixture-writer"]},
            "next_transition": "The fixture compiler approves the exact result."}},
        "read_only_roles": ["fixture-reader"], "required_references": {},
        "stack_reference_by_role": {}, "repair_policy": "Preserve finding ids.",
        "barrier": "Wait for every reader before writing.", "output_contract": "Return the result.",
        "read_only_entry_roles": {}, "technology_method_skills": []}
    files = {
        "constitution.md": "# Constitution\n",
        "templates/task-input-contract.md": "# Derived task inputs\n",
        "templates/task-input-policy.json": json.dumps(catalog, indent=2) + "\n",
        "scripts/task_inputs.py": "# The fixture binds this path; its bytes are fixed.\n",
        "agents/fixture-writer.md": "---\nname: fixture-writer\n---\n\n# Writer\n",
        "agents/fixture-reader.md": "---\nname: fixture-reader\n---\n\n# Reader\n",
        "flows/fixture-flow.md": "# Fixture flow\n\nSwitch `fixture_mode` selects step 2.\n",
        "skill-content/fixture-entry/SKILL.md":
            "---\nname: fixture-entry\nexposure: entry\n---\n\n# Entry\n",
        "skill-content/fixture-method/SKILL.md":
            "---\nname: fixture-method\nexposure: internal\n---\n\n# Method\n\n"
            "- [guide](references/guide.md): the method. Read when reviewing.\n",
        "skill-content/fixture-method/references/guide.md": "# Guide\n",
        "skill-content/fixture-method/data/table.json": "{}\n",
        "skill-content/obsidian-vault/SKILL.md":
            "---\nname: obsidian-vault\nexposure: internal\n---\n\n# Vault\n",
        "skill-content/configure/data/process-switches.json":
            json.dumps(FIXTURE_SWITCHES, indent=2) + "\n",
    }
    for relative, text in files.items():
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


def build_task_project(project: Path, extra: dict | None = None) -> None:
    """Write and commit one project whose Git identity is fixed by GIT_ENV."""
    from git_fixture import init_repository

    init_repository(project)
    brief = project / "workspace" / "docs" / "brief.md"
    brief.parent.mkdir(parents=True)
    brief.write_bytes(b"---\ntype: note\n---\n\n# Brief\n")
    for relative, text in (extra or {}).items():
        (project / relative).parent.mkdir(parents=True, exist_ok=True)
        (project / relative).write_bytes(text.encode("utf-8"))
    for args in (("config", "core.autocrlf", "false"), ("add", "--all"),
                 ("commit", "-q", "-m", "fixture")):
        subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True,
                       env={**os.environ, **GIT_ENV})


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


@integration
class TaskInputScopeTests(unittest.TestCase):
    """A reader given notes a, b, c of one package; d depends on a."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name).resolve()
        self.package, self.project = base / "package", base / "project"
        build_task_package(self.package)
        policy_path = self.package / task_inputs.POLICY
        task_policy = json.loads(policy_path.read_text())
        task_policy["approval_anchors"] = {"fixture": [{
            "path": NOTES["a"], "field": "type", "value": "note"}]}
        policy_path.write_text(json.dumps(task_policy), encoding="utf-8")
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
        commit(self.project)
        self.reader["base"] = git(self.project, "rev-parse", "HEAD")
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
        self.reader["base"] = git(self.project, "rev-parse", "HEAD")
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

    def test_a_changed_path_cannot_leave_the_vault(self):
        choose(self.docs, VALUE, self.package)
        for path in ("workspace/docs/../../outside.md", "workspace/docs/a/../../../x.md"):
            with self.subTest(path=path), Stub().install(), self.assertRaises(ValueError):
                task_inputs.manifest(**self.reader, changed=[path])
        with self.assertRaisesRegex(ValueError, "inside workspace/docs"):
            task_inputs.impact_closure(self.docs, ["workspace/docs/../outside.md"],
                                       "workspace/docs/")

    def test_the_switch_needs_the_closure_module(self):
        with mock.patch.dict(sys.modules, {"impact_closure": None}):
            with self.assertRaisesRegex(ValueError, "needs scripts/impact_closure.py"):
                task_inputs.closure_api()


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
        self.contract = closure_tests.CONTRACT

    def test_the_real_closure_reaches_the_manifest_with_paths_under_the_prefix(self):
        prefix = "workspace/docs/"
        scope = task_inputs.impact_closure(self.docs, [prefix + self.contract + ".md"], prefix)
        self.assertEqual(scope["changed"], [prefix + self.contract + ".md"])
        self.assertIn(prefix + "backlog/story-e.md", scope["closure"])
        self.assertEqual(scope["widened_by"], [{
            "path": prefix + self.contract + ".md", "reason": "shared_contract",
            "citers": [prefix + "backlog/story-e.md", prefix + "backlog/story-f.md"]}])
        self.assertNotIn(prefix + "backlog/story-g.md", scope["graph_gaps"])
        selected_gap = task_inputs.impact_closure(self.docs, ["backlog/story-g.md"], prefix)
        self.assertIn(prefix + "backlog/story-g.md", selected_gap["graph_gaps"])
        self.assertEqual(scope["proven_unchanged"], [])
        self.assertEqual(scope["beyond_closure"], [])
        self.assertEqual(sorted(task_inputs.vault_views(self.docs)), ["docs", "views"])


@integration
class ChangeInventoryTests(unittest.TestCase):
    """A changed canonical input of any canonical suffix is a change, so a
    reader reads it rather than listing it unchanged."""

    def test_a_changed_json_input_is_a_change(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        project = Path(temporary.name)
        init_repository(project)
        docs = project / "workspace/docs/solution-design/_generated"
        docs.mkdir(parents=True)
        (docs / "topology.json").write_text("{}\n", encoding="utf-8")
        (docs.parent / "note.md").write_text("# Note\n", encoding="utf-8")
        command = ["git", "-C", str(project), "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*command, "add", "-A"], check=True)
        subprocess.run([*command, "commit", "-qm", "base"], check=True)
        (docs / "topology.json").write_text('{"a": 1}\n', encoding="utf-8")
        changed, commit = task_inputs.impact_changes(["git", "-C", str(project)], None, None)
        self.assertEqual(changed, {"workspace/docs/solution-design/_generated/topology.json"})
        self.assertTrue(commit)


if __name__ == "__main__":
    unittest.main()
