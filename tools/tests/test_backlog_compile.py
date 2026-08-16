"""Focused contracts for the project-local Markdown backlog compiler."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (ROOT / "plugins" / "software-engineering-team" / "scripts"
          / "backlog_compile.py")
SCRIPTS = SCRIPT.parent
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("backlog_compile_under_test", SCRIPT)
BACKLOG = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(BACKLOG)

CRITERION = (
    "[[business-analysis/erp/domains/inventory/acceptance/"
    "account-acceptance|erp:AC-INV-001]]"
)
EXPERIENCE = (
    "[[experience-design/programs/prg-001/releases/rel-001/release|REL-001]]"
)


def write_note(path: Path, props: dict, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(BACKLOG.front_matter(props, body), encoding="utf-8")


def update_note(path: Path, *, props: dict | None = None,
                body: str | None = None) -> None:
    current_props, current_body = BACKLOG.parse_front_matter(path)
    if props:
        current_props.update(props)
    path.write_text(BACKLOG.front_matter(
        current_props, current_body if body is None else body), encoding="utf-8")


class BacklogCompilerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.docs = self.root / "docs"
        self.docs.mkdir()
        self._write_upstream()

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *args,
             "--docs", str(self.docs)],
            capture_output=True, text=True, check=False,
        )

    def _write_upstream(self) -> None:
        acceptance_rel = (
            "business-analysis/erp/domains/inventory/acceptance/"
            "account-acceptance.md"
        )
        write_note(
            self.docs / acceptance_rel,
            {"type": "acceptance_set", "title": "Account acceptance",
             "status": "approved", "owner_role": "business_analyst",
             "tags": ["doc/acceptance-set", "status/approved"],
             "aliases": ["AC-INV-001"]},
            "# Account acceptance\n\n| id | criterion |\n|---|---|\n"
            "| AC-INV-001 | An account can be registered. |\n",
        )
        registry = self.docs / "business-analysis/erp/_generated/registry.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(json.dumps({
            "ids": {"AC-INV-001": {
                "doc": "domains/inventory/acceptance/account-acceptance.md",
                "doc_status": "approved",
            }}
        }), encoding="utf-8")
        write_note(
            self.docs / "experience-design/programs/prg-001/releases/rel-001/release.md",
            {"type": "release", "title": "Release 1", "status": "approved",
             "owner_role": "ux_designer", "tags": ["doc/release", "status/approved"],
             "aliases": ["REL-001"]},
            "# Release 1\n\nApproved experience boundary.\n",
        )

    def make_package(self, stories: tuple[tuple[str, str], ...] =
                     (("register-account", "ST-001"),)) -> None:
        self.assertEqual(self.run_cli("init").returncode, 0)
        self.assertEqual(self.run_cli(
            "stub-epic", "customer-accounts", "--id", "EP-001").returncode, 0)
        for slug, story_id in stories:
            result = self.run_cli(
                "stub-story", "customer-accounts", slug, "--id", story_id,
                "--criterion-ref", CRITERION,
                "--experience-ref", EXPERIENCE,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.configure_reviews(stories)

    def configure_reviews(self, stories: tuple[tuple[str, str], ...],
                          dependency_refs: list[str] | None = None) -> None:
        root_review = self.docs / "backlog/reviews/round-1-backlog-review.md"
        update_note(root_review, props={
            "verdict": "approved",
            "related_to": [
                "[[backlog/epics/customer-accounts/epic|EP-001]]",
            ],
            "dependency_refs": [],
        })
        verifies: list[str] = []
        scenarios: list[str] = []
        for slug, story_id in stories:
            base = f"backlog/epics/customer-accounts/stories/{slug}"
            verifies.extend([
                f"[[{base}/story|{story_id}]]",
                f"[[{base}/test-plan|{story_id}-TP]]",
            ])
            scenarios.append(f"{story_id}-TS-001")
        epic_review = (
            self.docs
            / "backlog/epics/customer-accounts/reviews/round-1-epic-review.md"
        )
        update_note(epic_review, props={
            "verdict": "approved", "verifies": verifies,
            "scenario_refs": scenarios,
            "dependency_refs": dependency_refs or [],
        })

    def test_render_is_deterministic_and_registry_keeps_role_model(self):
        self.make_package()
        first = self.run_cli("check", "--render", "--json")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        generated = self.docs / "backlog/_generated"
        before = {path.name: path.read_bytes() for path in generated.iterdir()}
        second = self.run_cli("check", "--render", "--json")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        after = {path.name: path.read_bytes() for path in generated.iterdir()}
        self.assertEqual(before, after)
        registry = json.loads((generated / "registry.json").read_text())
        self.assertNotIn("generated_at", registry)
        self.assertEqual(registry["stories"][0]["owner_role"],
                         "backend_developer")
        self.assertEqual(registry["stories"][0]["supporting_roles"], [])

    def test_verifies_relation_keeps_ba_process_and_backlog_targets(self):
        policy = json.loads((
            ROOT / "plugins/software-engineering-team/skill-content/"
            "obsidian-vault/data/vault-policy.json"
        ).read_text(encoding="utf-8"))
        targets = policy["relation_contract"]["keys"]["verifies"]["targets"]
        self.assertTrue({"process", "story", "test-plan"}.issubset(targets))

    def test_owner_supporting_roles_responsibilities_and_no_assignee(self):
        self.make_package()
        story = (self.docs / "backlog/epics/customer-accounts/stories/"
                 "register-account/story.md")
        props, body = BACKLOG.parse_front_matter(story)
        props["owner_role"] = "product_owner"
        props["supporting_roles"] = ["frontend_developer", "frontend_developer"]
        props["assignee"] = "codex/task-123"
        story.write_text(BACKLOG.front_matter(props, body), encoding="utf-8")
        result = self.run_cli("check", "--json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid owner_role", result.stdout)
        self.assertIn("duplicate supporting_roles", result.stdout)
        self.assertIn("must not contain assignee", result.stdout)
        self.assertIn("does not assign an implementation responsibility",
                      result.stdout)

    def test_supporting_role_is_valid_when_its_work_is_explicit(self):
        self.make_package()
        story = (self.docs / "backlog/epics/customer-accounts/stories/"
                 "register-account/story.md")
        props, body = BACKLOG.parse_front_matter(story)
        props["supporting_roles"] = ["frontend_developer", "ux_designer"]
        body = body.replace(
            "- backend_developer: Own implementation and integration.",
            "- backend_developer: Own implementation and integration.\n"
            "- frontend_developer: Implement the account form.\n"
            "- ux_designer: Verify interaction behavior.",
        )
        story.write_text(BACKLOG.front_matter(props, body), encoding="utf-8")
        result = self.run_cli("check", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_story_sections_and_dependency_reasons_are_hard_gates(self):
        stories = (("register-account", "ST-001"), ("confirm-account", "ST-002"))
        self.make_package(stories)
        dependent = (self.docs / "backlog/epics/customer-accounts/stories/"
                     "confirm-account/story.md")
        props, body = BACKLOG.parse_front_matter(dependent)
        target = ("[[backlog/epics/customer-accounts/stories/"
                  "register-account/story|ST-001]]")
        props["depends_on"] = [target]
        body = body.replace("## Dependencies\n\nNone.",
                            "## Dependencies\n\nDependency is implicit.")
        body = body.replace("## Non-Goals\n\n", "## Removed\n\n")
        dependent.write_text(BACKLOG.front_matter(props, body), encoding="utf-8")
        result = self.run_cli("check", "--json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing required section: Non-Goals", result.stdout)
        self.assertIn("dependencies missing reasons", result.stdout)

        body = body.replace("## Removed\n\n", "## Non-Goals\n\n")
        body = body.replace("Dependency is implicit.",
                            f"- {target}: Registration creates the account identity.")
        dependent.write_text(BACKLOG.front_matter(props, body), encoding="utf-8")
        self.configure_reviews(stories, ["ST-002 -> ST-001"])
        result = self.run_cli("check", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_criterion_and_experience_refs_must_resolve_and_be_approved(self):
        self.make_package()
        story = (self.docs / "backlog/epics/customer-accounts/stories/"
                 "register-account/story.md")
        props, body = BACKLOG.parse_front_matter(story)
        props["criterion_refs"] = ["erp:AC-INV-001"]
        props["experience_refs"] = [
            "[[experience-design/missing|REL-999]]"
        ]
        story.write_text(BACKLOG.front_matter(props, body), encoding="utf-8")
        result = self.run_cli("check", "--json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be a quoted vault-absolute wikilink", result.stdout)
        self.assertIn("targets missing note", result.stdout)

        props["criterion_refs"] = [CRITERION]
        props["experience_refs"] = [EXPERIENCE]
        story.write_text(BACKLOG.front_matter(props, body), encoding="utf-8")
        registry = self.docs / "business-analysis/erp/_generated/registry.json"
        payload = json.loads(registry.read_text())
        payload["ids"]["AC-INV-001"]["doc_status"] = "draft"
        registry.write_text(json.dumps(payload), encoding="utf-8")
        result = self.run_cli("check", "--json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("criterion owner is not approved", result.stdout)

    def test_scenarios_require_story_ids_coverage_and_automation_target(self):
        self.make_package()
        test_plan = (self.docs / "backlog/epics/customer-accounts/stories/"
                     "register-account/test-plan.md")
        text = test_plan.read_text(encoding="utf-8")
        text = text.replace("## ST-001-TS-001", "## ST-999-TS-001")
        text = text.replace(
            "- automation_target: tests/register_account.py::test_register_account\n",
            "",
        )
        text = text.replace(f"  - {CRITERION}",
                            "  - [[business-analysis/missing|erp:AC-INV-999]]")
        test_plan.write_text(text, encoding="utf-8")
        result = self.run_cli("check", "--json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not belong to ST-001", result.stdout)
        self.assertIn("missing automation_target", result.stdout)
        self.assertIn("does not map criterion", result.stdout)

    def test_reviews_require_exact_typed_sets(self):
        self.make_package()
        root_review = self.docs / "backlog/reviews/round-1-backlog-review.md"
        props, body = BACKLOG.parse_front_matter(root_review)
        props["related_to"] = []
        root_review.write_text(BACKLOG.front_matter(props, body), encoding="utf-8")
        epic_review = (self.docs / "backlog/epics/customer-accounts/reviews/"
                       "round-1-epic-review.md")
        props, body = BACKLOG.parse_front_matter(epic_review)
        props["verifies"] = props["verifies"][:1]
        props["scenario_refs"] = []
        epic_review.write_text(BACKLOG.front_matter(props, body), encoding="utf-8")
        result = self.run_cli("check", "--json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("related_to set does not exactly cover every epic",
                      result.stdout)
        self.assertIn("verifies set does not exactly cover every story and test plan",
                      result.stdout)
        self.assertIn("scenario_refs do not exactly cover every scenario",
                      result.stdout)

    def test_approve_is_atomic_and_approved_check_detects_stale_source(self):
        self.make_package()
        result = self.run_cli("approve")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["package_hash"].startswith("sha256:"))
        approved = self.run_cli("check", "--approved", "--json")
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)

        story = (self.docs / "backlog/epics/customer-accounts/stories/"
                 "register-account/story.md")
        props, body = BACKLOG.parse_front_matter(story)
        self.assertEqual(props["status"], "planned")
        self.assertTrue(props["source_hash"].startswith("sha256:"))
        for rel in (
            "backlog/backlog.md",
            "backlog/epics/customer-accounts/epic.md",
            "backlog/reviews/round-1-backlog-review.md",
            "backlog/epics/customer-accounts/reviews/round-1-epic-review.md",
            "backlog/epics/customer-accounts/stories/register-account/test-plan.md",
        ):
            approved_props, _ = BACKLOG.parse_front_matter(self.docs / rel)
            self.assertEqual(approved_props["status"], "approved")
        story.write_text(BACKLOG.front_matter(
            props, body.replace("observable user or business value",
                                "changed user or business value")), encoding="utf-8")
        stale = self.run_cli("check", "--approved", "--json")
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("approved source_hash is stale", stale.stdout)
        self.assertIn("approved package_hash is stale", stale.stdout)

    def test_approved_timestamp_is_shared_utc_and_hash_bound(self):
        self.make_package()
        approved = self.run_cli("approve")
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        record, errors = BACKLOG.collect(self.docs)
        self.assertEqual(errors, [])
        timestamps = set()
        for path in BACKLOG.package_paths(record, self.docs):
            props, _ = BACKLOG.parse_front_matter(path)
            timestamps.add(props.get("approved_at_utc"))
        self.assertEqual(len(timestamps), 1)
        self.assertTrue(next(iter(timestamps)).endswith("+00:00"))

        story = (self.docs / "backlog/epics/customer-accounts/stories/"
                 "register-account/story.md")
        props, body = BACKLOG.parse_front_matter(story)
        props.pop("approved_at_utc")
        story.write_text(BACKLOG.front_matter(props, body), encoding="utf-8")
        missing = self.run_cli("check", "--approved", "--json")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("approved_at_utc is missing", missing.stdout)
        self.assertIn("approved source_hash is stale", missing.stdout)

        props["approved_at_utc"] = "2026-01-01T00:00:00+00:00"
        story.write_text(BACKLOG.front_matter(props, body), encoding="utf-8")
        tampered = self.run_cli("check", "--approved", "--json")
        self.assertNotEqual(tampered.returncode, 0)
        self.assertIn("approved package timestamps are not identical",
                      tampered.stdout)
        self.assertIn("approved source_hash is stale", tampered.stdout)

    def test_generated_inverse_relations_do_not_invalidate_approval(self):
        self.make_package()
        approved = self.run_cli("approve")
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        story = (self.docs / "backlog/epics/customer-accounts/stories/"
                 "register-account/story.md")
        before = story.read_text(encoding="utf-8")
        self.assertNotIn(BACKLOG.RELATION_START, before)
        block = (
            BACKLOG.RELATION_START + "\n\n"
            "- Verified by: [[backlog/reviews/round-1-backlog-review|Review]]\n\n"
            + BACKLOG.RELATION_END
        )
        story.write_text(
            BACKLOG.vault_check.replace_relation_block(before, block),
            encoding="utf-8",
        )
        result = self.run_cli("check", "--approved", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_approve_restores_every_file_when_closing_render_fails(self):
        self.make_package()
        before = {path.relative_to(self.docs): path.read_bytes()
                  for path in self.docs.rglob("*") if path.is_file()}
        original = BACKLOG.render

        def fail_after_write(record, docs):
            marker = docs / "backlog/_generated/partial.md"
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("partial", encoding="utf-8")
            raise RuntimeError("synthetic closing failure")

        BACKLOG.render = fail_after_write
        try:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = BACKLOG.approve(argparse.Namespace(docs=str(self.docs)))
        finally:
            BACKLOG.render = original
        self.assertEqual(code, 1)
        self.assertIn("approval rolled back", output.getvalue())
        after = {path.relative_to(self.docs): path.read_bytes()
                 for path in self.docs.rglob("*") if path.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
