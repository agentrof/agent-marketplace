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
DESIGN = "[[design-system/MASTER|Design Master]]"
CONSTRAINT = "[[solution-design/landscape|Solution Landscape]]"


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
        write_note(
            self.docs / "design-system/MASTER.md",
            {"type": "design-master", "title": "Design Master",
             "status": "approved", "owner_role": "design_system_architect",
             "tags": ["doc/design-master", "status/approved"]},
            "# Design Master\n\nApproved design boundary.\n",
        )
        write_note(
            self.docs / "solution-design/landscape.md",
            {"type": "landscape", "title": "Solution Landscape",
             "status": "approved", "owner_role": "solution_architect",
             "tags": ["doc/landscape", "status/approved"]},
            "# Solution Landscape\n\nApproved solution boundary.\n",
        )

    @staticmethod
    def completed_review_body(title: str, sections: list[str],
                              deferrals: list[tuple[str, str, str, str]] | None = None) -> str:
        lines = [f"# {title}", ""]
        for section_name in sections:
            lines.extend([f"## {section_name}", ""])
            if section_name == "Deferred Criteria":
                lines.extend([
                    "| criterion_ref | owner_role | reason | revisit_trigger |",
                    "|---|---|---|---|",
                ])
                for row in deferrals or []:
                    cells = [value.replace("|", "\\|") for value in row]
                    lines.append("| " + " | ".join(cells) + " |")
            else:
                lines.extend([
                    f"Evidence [{section_name}]: [[backlog/backlog|Backlog]] "
                    f"records the exact inputs evaluated for {section_name}.",
                    f"Conclusion [{section_name}]: {section_name} is supported "
                    "by the cited inputs and their exact relation coverage.",
                ])
            lines.append("")
        return "\n".join(lines)

    def complete_review(self, path: Path, sections: list[str], *,
                        deferrals: list[tuple[str, str, str, str]] | None = None,
                        props: dict | None = None) -> None:
        current, _ = BACKLOG.parse_front_matter(path)
        update_note(
            path,
            props=props,
            body=self.completed_review_body(
                str(current["title"]), sections, deferrals
            ),
        )

    @staticmethod
    def assess_coverage(path: Path, story_id: str) -> None:
        text = path.read_text(encoding="utf-8")
        for class_name in BACKLOG.SCENARIO_COVERAGE_CLASSES:
            replacement = (
                f"| {class_name} | covered | {story_id}-TS-001 | |"
                if class_name == "empty" else
                f"| {class_name} | not_applicable | - | "
                f"The reviewed story declares no {class_name} behavior. |"
            )
            text = text.replace(
                f"| {class_name} | not_applicable | - | TODO: assess this coverage class. |",
                replacement,
            )
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def author_story(story: Path, test_plan: Path, story_id: str) -> None:
        props, body = BACKLOG.parse_front_matter(story)
        props["scope"] = f"Deliver the observable outcome defined by {story_id}."
        props["priority_reason"] = (
            f"{story_id} is required to achieve the approved epic outcome."
        )
        replacements = {
            "Describe the observable user or business value.":
                f"Users receive the approved business outcome for {story_id}.",
            "Describe the smallest valuable behavior.":
                f"Deliver the observable outcome defined by {story_id}.",
            "List behavior deliberately excluded from this story.":
                "Administrative bulk operations remain outside this slice.",
            "- backend_developer: Own implementation and integration.":
                "- backend_developer: Implement the validated account boundary and API integration.",
            "- [ ] Map every cited criterion to an observable result.":
                "- [ ] Every cited criterion has an observable passing result.",
            "Record delivery constraints without execution state.":
                "Preserve the approved API boundary and avoid delivery-state metadata.",
        }
        for old, new in replacements.items():
            body = body.replace(old, new)
        story.write_text(BACKLOG.front_matter(props, body), encoding="utf-8")

        text = test_plan.read_text(encoding="utf-8")
        text = text.replace(
            "the preconditions are satisfied",
            "an eligible customer supplies valid account details",
        ).replace(
            "the user performs the story action",
            "the customer submits the account request",
        ).replace(
            "the expected outcome is observable",
            "the account result and identifier are returned",
        )
        test_plan.write_text(text, encoding="utf-8")

    def make_package(self, stories: tuple[tuple[str, str], ...] =
                     (("register-account", "ST-001"),)) -> None:
        self.assertEqual(self.run_cli("init").returncode, 0)
        self.assertEqual(self.run_cli(
            "stub-epic", "customer-accounts", "--id", "EP-001",
            "--goal", "Enable customers to access approved account capabilities."
        ).returncode, 0)
        for slug, story_id in stories:
            result = self.run_cli(
                "stub-story", "customer-accounts", slug, "--id", story_id,
                "--criterion-ref", CRITERION,
                "--experience-ref", EXPERIENCE,
                "--uses-design", DESIGN,
                "--constrained-by", CONSTRAINT,
                "--scope", f"Deliver the observable outcome defined by {story_id}.",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assess_coverage(
                self.docs / "backlog/epics/customer-accounts/stories"
                / slug / "test-plan.md", story_id,
            )
            self.author_story(
                self.docs / "backlog/epics/customer-accounts/stories"
                / slug / "story.md",
                self.docs / "backlog/epics/customer-accounts/stories"
                / slug / "test-plan.md",
                story_id,
            )
        self.configure_reviews(stories)

    def configure_reviews(self, stories: tuple[tuple[str, str], ...],
                          dependency_refs: list[str] | None = None) -> None:
        root_review = self.docs / "backlog/reviews/round-1-backlog-review.md"
        self.complete_review(
            root_review,
            BACKLOG.backlog_contract()["required_backlog_review_sections"],
            props={
                "verdict": "approved",
                "related_to": [
                    "[[backlog/epics/customer-accounts/epic|EP-001]]",
                ],
                "dependency_refs": [],
            },
        )
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
        self.complete_review(
            epic_review,
            BACKLOG.backlog_contract()["required_epic_review_sections"],
            props={
                "verdict": "approved", "verifies": verifies,
                "scenario_refs": scenarios,
                "dependency_refs": dependency_refs or [],
            },
        )

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
        self.assertEqual(registry["stories"][0]["work_kind"], "feature")

    def test_stub_titles_use_project_designations(self):
        config = {
            "project_origin": "greenfield",
            "doc_type_designations": {
                "backlog": "ürün birikimi",
                "backlog-review": "birikim incelemesi",
                "epic": "epik",
                "epic-review": "epik incelemesi",
                "story": "kullanıcı hikayesi",
                "test-plan": "doğrulama planı",
            },
        }
        (self.docs.parent / "config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        write_note(
            self.docs / "maps/backlog.md",
            {"type": "moc", "title": "Backlog map", "tags": ["doc/moc"],
             "aliases": ["Backlog"]},
            "# Backlog map\n\n"
            "The canonical backlog is [[backlog/backlog|Product backlog]].\n",
        )
        self.make_package()
        expected = {
            "backlog/backlog.md": "Ürün birikimi",
            "backlog/reviews/round-1-backlog-review.md": "Birikim incelemesi",
            "backlog/epics/customer-accounts/epic.md": "epik",
            "backlog/epics/customer-accounts/reviews/round-1-epic-review.md": "epik incelemesi",
            "backlog/epics/customer-accounts/stories/register-account/story.md": "kullanıcı hikayesi",
            "backlog/epics/customer-accounts/stories/register-account/test-plan.md": "doğrulama planı",
        }
        for relative, expected_title in expected.items():
            props, body = BACKLOG.parse_front_matter(self.docs / relative)
            if relative in {
                "backlog/backlog.md",
                "backlog/reviews/round-1-backlog-review.md",
            }:
                self.assertEqual(props["title"], expected_title)
            else:
                self.assertTrue(
                    str(props["title"]).casefold().endswith(expected_title)
                )
            self.assertIn(f"# {props['title']}", body)
            folded = str(props["title"]).casefold()
            for leaked in (
                "product", "cross-epic", " epic", " story", " test plan"
            ):
                self.assertNotIn(leaked, folded)
        for path in self.docs.joinpath("backlog").rglob("*.md"):
            text = path.read_text(encoding="utf-8").casefold()
            self.assertNotIn("|product backlog]]", text)
            self.assertNotIn("|cross-epic", text)
        result = self.run_cli("check", "--render", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_turkish_designation_uses_dotted_initial_i(self):
        config = {
            "project_origin": "greenfield",
            "output_language": "Turkish",
            "doc_type_designations": {
                "backlog": "iş listesi",
                "backlog-review": "iş listesi incelemesi",
            },
        }
        (self.docs.parent / "config.json").write_text(
            json.dumps(config), encoding="utf-8"
        )
        initialized = self.run_cli("init")
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        backlog, _ = BACKLOG.parse_front_matter(
            self.docs / "backlog/backlog.md"
        )
        review, _ = BACKLOG.parse_front_matter(
            self.docs / "backlog/reviews/round-1-backlog-review.md"
        )
        self.assertEqual(backlog["title"], "İş listesi")
        self.assertEqual(review["title"], "İş listesi incelemesi")

    def test_global_ba_coverage_requires_story_or_structured_deferral(self):
        registry_path = self.docs / "business-analysis/erp/_generated/registry.json"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["ids"]["AC-INV-002"] = {
            "kind": "AC", "row_status": "active",
            "doc": "domains/inventory/acceptance/account-acceptance.md",
            "doc_status": "approved",
        }
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
        self.make_package()
        missing = self.run_cli("check", "--json")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("neither story-covered nor deferred: erp:AC-INV-002",
                      missing.stdout)

        review = self.docs / "backlog/reviews/round-1-backlog-review.md"
        criterion_2 = CRITERION.replace("AC-INV-001", "AC-INV-002")
        self.complete_review(
            review,
            BACKLOG.backlog_contract()["required_backlog_review_sections"],
            deferrals=[(
                criterion_2, "product_owner",
                "The second release intentionally excludes account recovery.",
                "Revisit when the second release scope is approved.",
            )],
        )
        covered = self.run_cli("check", "--json")
        self.assertEqual(covered.returncode, 0, covered.stdout + covered.stderr)

        self.complete_review(
            review,
            BACKLOG.backlog_contract()["required_backlog_review_sections"],
            deferrals=[(
                criterion_2, "banana",
                "The second release intentionally excludes account recovery.",
                "Revisit when the second release scope is approved.",
            )],
        )
        bad_owner = self.run_cli("check", "--json")
        self.assertNotEqual(bad_owner.returncode, 0)
        self.assertIn("owner_role must be product_owner", bad_owner.stdout)

        self.complete_review(
            review,
            BACKLOG.backlog_contract()["required_backlog_review_sections"],
            deferrals=[
                (CRITERION, "product_owner", "This duplicates covered scope deliberately.",
                 "Revisit at the next scope decision."),
                (CRITERION.replace("AC-INV-001", "AC-INV-999"),
                 "product_owner", "This criterion has no approved registry owner.",
                 "Revisit at the next scope decision."),
            ],
        )
        invalid = self.run_cli("check", "--json")
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("both story-covered and deferred", invalid.stdout)
        self.assertIn("outside the approved BA universe: erp:AC-INV-999",
                      invalid.stdout)

        self.complete_review(
            review,
            BACKLOG.backlog_contract()["required_backlog_review_sections"],
            deferrals=[(
                "[[business-analysis/erp/domains/inventory/acceptance/"
                "wrong-acceptance|erp:AC-INV-002]]",
                "product_owner",
                "The second release intentionally excludes this behavior.",
                "Revisit when the second release scope is approved.",
            )],
        )
        wrong = self.docs / (
            "business-analysis/erp/domains/inventory/acceptance/"
            "wrong-acceptance.md"
        )
        write_note(
            wrong,
            {"type": "acceptance_set", "title": "Wrong acceptance",
             "status": "approved", "owner_role": "business_analyst",
             "tags": ["doc/acceptance-set", "status/approved"]},
            "# Wrong acceptance\n\nThis note does not own the deferred identity.\n",
        )
        wrong_owner = self.run_cli("check", "--json")
        self.assertNotEqual(wrong_owner.returncode, 0)
        self.assertIn("criterion erp:AC-INV-002 belongs to", wrong_owner.stdout)

    def test_greenfield_global_coverage_fails_closed_on_bad_registry(self):
        self.make_package()
        registry = self.docs / "business-analysis/erp/_generated/registry.json"
        registry.write_text("{not-json", encoding="utf-8")
        result = self.run_cli("check", "--json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("registry.json is unreadable", result.stdout)

    def test_scenario_coverage_classes_are_exact_and_exclusions_need_reasons(self):
        self.make_package()
        test_plan = (self.docs / "backlog/epics/customer-accounts/stories/"
                     "register-account/test-plan.md")
        text = test_plan.read_text(encoding="utf-8")
        text = text.replace(
            "| boundary | not_applicable | - | The reviewed story declares no boundary behavior. |\n",
            "",
        ).replace(
            "| empty | covered | ST-001-TS-001 | |",
            "| empty | not_applicable | - | The reviewed story declares no empty behavior. |",
        ).replace(
            "| failure | not_applicable | - | The reviewed story declares no failure behavior. |",
            "| failure | not_applicable | ST-001-TS-999 | TBD |",
        )
        test_plan.write_text(text, encoding="utf-8")
        result = self.run_cli("check", "--json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing coverage classes: boundary", result.stdout)
        self.assertIn("cites unknown scenarios: ST-001-TS-999", result.stdout)
        self.assertIn("not_applicable class failure must not cite scenarios",
                      result.stdout)
        self.assertIn("not_applicable class failure needs a concrete reason",
                      result.stdout)
        self.assertIn(
            "scenarios are not classified by Coverage Classes: ST-001-TS-001",
            result.stdout,
        )

    def test_review_placeholders_are_rejected(self):
        self.make_package()
        review = (self.docs / "backlog/epics/customer-accounts/reviews/"
                  "round-1-epic-review.md")
        props, body = BACKLOG.parse_front_matter(review)
        body = body.replace(
            "Evidence [Scope]: [[backlog/backlog|Backlog]] records the exact "
            "inputs evaluated for Scope.\nConclusion [Scope]: Scope is "
            "supported by the cited inputs and their exact relation coverage.",
            "Evidence [Scope]: [[backlog/backlog|Backlog]] says the complete "
            "package has been reviewed carefully.\nConclusion [Scope]: No "
            "blocking gap remains after reviewing the complete package.",
        )
        review.write_text(BACKLOG.front_matter(props, body), encoding="utf-8")
        result = self.run_cli("check", "--json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("section-specific Evidence [Scope]", result.stdout)

        body = body.replace(
            "Evidence [Scope]: [[backlog/backlog|Backlog]] says the complete "
            "package has been reviewed carefully.\nConclusion [Scope]: No "
            "blocking gap remains after reviewing the complete package.",
            "Evidence [Scope]: [[backlog/backlog|Backlog]] maps ST-001 and its "
            "test plan to the EP-001 scope boundary.\nConclusion [Scope]: "
            "ST-001 and its test plan exactly match EP-001 scope, so no "
            "blocking gap remains in this lens.",
        )
        review.write_text(BACKLOG.front_matter(props, body), encoding="utf-8")
        concrete = self.run_cli("check", "--json")
        self.assertEqual(
            concrete.returncode, 0, concrete.stdout + concrete.stderr
        )

    def test_untouched_epic_story_and_scenario_stubs_are_rejected(self):
        self.assertEqual(self.run_cli("init").returncode, 0)
        self.assertEqual(self.run_cli(
            "stub-epic", "customer-accounts", "--id", "EP-001"
        ).returncode, 0)
        created = self.run_cli(
            "stub-story", "customer-accounts", "register-account",
            "--id", "ST-001", "--criterion-ref", CRITERION,
            "--experience-ref", EXPERIENCE, "--uses-design", DESIGN,
            "--constrained-by", CONSTRAINT,
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        self.assess_coverage(
            self.docs / "backlog/epics/customer-accounts/stories/"
            "register-account/test-plan.md", "ST-001",
        )
        self.configure_reviews((("register-account", "ST-001"),))
        checked = self.run_cli("check", "--json")
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("untouched goal stub", checked.stdout)
        self.assertIn("untouched User Value stub", checked.stdout)
        self.assertIn("untouched implementation responsibility stub",
                      checked.stdout)
        self.assertIn("untouched Given stub", checked.stdout)

    def test_existing_defect_uses_explicit_evidence_without_feature_upstreams(self):
        (self.docs.parent / "config.json").write_text(json.dumps({
            "project_origin": "existing",
        }), encoding="utf-8")
        issue = self.docs / "issues/account-regression.md"
        write_note(
            issue,
            {"type": "issue-report", "title": "Account regression issue report",
             "status": "approved", "owner_role": "product_owner",
             "tags": ["doc/issue-report", "status/approved"],
             "aliases": ["ISSUE-001"]},
            "# Account regression issue report\n\nApproved reproduction evidence.\n",
        )
        self.assertEqual(self.run_cli("init").returncode, 0)
        self.assertEqual(self.run_cli(
            "stub-epic", "customer-accounts", "--id", "EP-001",
            "--goal", "Repair the observable account regression safely."
        ).returncode, 0)
        evidence = "[[issues/account-regression|ISSUE-001]]"
        created = self.run_cli(
            "stub-story", "customer-accounts", "repair-account", "--id", "ST-001",
            "--work-kind", "defect", "--evidence-ref", evidence,
        )
        self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
        self.author_story(
            self.docs / "backlog/epics/customer-accounts/stories/repair-account/story.md",
            self.docs / "backlog/epics/customer-accounts/stories/repair-account/test-plan.md",
            "ST-001",
        )
        self.assess_coverage(
            self.docs / "backlog/epics/customer-accounts/stories/repair-account/test-plan.md",
            "ST-001",
        )
        self.configure_reviews((("repair-account", "ST-001"),))
        valid = self.run_cli("check", "--json")
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
        test_plan = (
            self.docs / "backlog/epics/customer-accounts/stories/"
            "repair-account/test-plan.md"
        )
        original_plan = test_plan.read_text(encoding="utf-8")
        self.assertIn(f"  - {evidence}", original_plan)
        test_plan.write_text(
            original_plan.replace(f"  - {evidence}\n", ""), encoding="utf-8"
        )
        unmapped = self.run_cli("check", "--json")
        self.assertNotEqual(unmapped.returncode, 0)
        self.assertIn("scenario ST-001-TS-001 is missing source_refs",
                      unmapped.stdout)
        self.assertIn("does not map planning source", unmapped.stdout)
        test_plan.write_text(original_plan, encoding="utf-8")

        backlog = self.docs / "backlog/backlog.md"
        backlog_props, backlog_body = BACKLOG.parse_front_matter(backlog)
        backlog_props["analysis_scopes"] = ["erp"]
        backlog.write_text(
            BACKLOG.front_matter(backlog_props, backlog_body), encoding="utf-8"
        )
        scoped_missing = self.run_cli("check", "--json")
        self.assertNotEqual(scoped_missing.returncode, 0)
        self.assertIn("neither story-covered nor deferred: erp:AC-INV-001",
                      scoped_missing.stdout)
        root_review = self.docs / "backlog/reviews/round-1-backlog-review.md"
        self.complete_review(
            root_review,
            BACKLOG.backlog_contract()["required_backlog_review_sections"],
            deferrals=[(
                CRITERION, "product_owner",
                "Historical account registration is outside this repair scope.",
                "Revisit when account registration behavior changes again.",
            )],
        )
        scoped = self.run_cli("check", "--json")
        self.assertEqual(scoped.returncode, 0, scoped.stdout + scoped.stderr)

        story = self.docs / "backlog/epics/customer-accounts/stories/repair-account/story.md"
        props, body = BACKLOG.parse_front_matter(story)
        props["related_to"] = []
        story.write_text(BACKLOG.front_matter(props, body), encoding="utf-8")
        missing = self.run_cli("check", "--json")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("defect/technical work needs related_to", missing.stdout)

        props["related_to"] = [evidence]
        story.write_text(BACKLOG.front_matter(props, body), encoding="utf-8")
        config = self.docs.parent / "config.json"
        config.write_text(json.dumps({"project_origin": "greenfield"}), encoding="utf-8")
        greenfield = self.run_cli("check", "--json")
        self.assertNotEqual(greenfield.returncode, 0)
        self.assertIn("greenfield work_kind must be feature", greenfield.stdout)

    def test_verifies_relation_keeps_ba_process_and_backlog_targets(self):
        policy = json.loads((
            ROOT / "plugins/software-engineering-team/skill-content/"
            "obsidian-vault/data/vault-policy.json"
        ).read_text(encoding="utf-8"))
        targets = policy["relation_contract"]["keys"]["verifies"]["targets"]
        self.assertTrue({"process", "story", "test-plan"}.issubset(targets))

    def test_owner_supporting_roles_responsibilities_and_no_assignee(self):
        self.make_package()
        backlog = self.docs / "backlog/backlog.md"
        backlog_props, backlog_body = BACKLOG.parse_front_matter(backlog)
        backlog_props["owner_role"] = "banana"
        backlog.write_text(
            BACKLOG.front_matter(backlog_props, backlog_body), encoding="utf-8"
        )
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
        self.assertIn("backlog.md owner_role must be product_owner", result.stdout)

    def test_supporting_role_is_valid_when_its_work_is_explicit(self):
        self.make_package()
        story = (self.docs / "backlog/epics/customer-accounts/stories/"
                 "register-account/story.md")
        props, body = BACKLOG.parse_front_matter(story)
        props["supporting_roles"] = ["frontend_developer", "ux_designer"]
        body = body.replace(
            "- backend_developer: Implement the validated account boundary and API integration.",
            "- backend_developer: Implement the validated account boundary and API integration.\n"
            "- frontend_developer: Implement the account form.\n"
            "- ux_designer: Verify interaction behavior.",
        )
        story.write_text(BACKLOG.front_matter(props, body), encoding="utf-8")
        result = self.run_cli("check", "--json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_existing_feature_requires_scoped_compiler_bound_upstreams(self):
        self.make_package()
        (self.docs.parent / "config.json").write_text(json.dumps({
            "project_origin": "existing",
        }), encoding="utf-8")
        unscoped = self.run_cli("check", "--json")
        self.assertNotEqual(unscoped.returncode, 0)
        self.assertIn(
            "existing feature backlog requires explicit analysis_scopes",
            unscoped.stdout,
        )

        path = self.docs / "backlog/backlog.md"
        props, body = BACKLOG.parse_front_matter(path)
        props["analysis_scopes"] = ["erp"]
        update_note(path, props=props, body=body)
        scoped = self.run_cli("check", "--json")
        self.assertNotEqual(scoped.returncode, 0)
        self.assertIn(
            "compiler-valid approved BA package", scoped.stdout
        )
        self.assertIn(
            "compiler-valid approved Experience package", scoped.stdout
        )

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
        text += (
            "\n## ST-001-TS-01\n\nMalformed scenario-like prose.\n"
            "\n## ST-001-ts-002\n\nLowercase malformed scenario-like prose.\n"
        )
        test_plan.write_text(text, encoding="utf-8")
        result = self.run_cli("check", "--json")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not belong to ST-001", result.stdout)
        self.assertIn("missing automation_target", result.stdout)
        self.assertIn("does not map planning source", result.stdout)
        self.assertIn("malformed scenario-like heading: ST-001-TS-01",
                      result.stdout)
        self.assertIn("malformed scenario-like heading: ST-001-ts-002",
                      result.stdout)

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
            props, body.replace("approved business outcome",
                                "changed business outcome")), encoding="utf-8")
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
