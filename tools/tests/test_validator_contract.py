"""Mutation tests for the repository validator's active architecture rules."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

import fixtures  # noqa: E402
import validate  # noqa: E402


class ValidatorContractTests(unittest.TestCase):
    def fixture(self, temporary: str) -> Path:
        root = Path(temporary)
        fixtures.make_valid_root(root)
        return root

    @staticmethod
    def checks(root: Path) -> list[str]:
        return [finding.check for finding in validate.run(root)]

    def test_valid_single_team_fixture_is_clean_and_deterministic(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            first = validate.run(root)
            second = validate.run(root)
            self.assertEqual(first, [])
            self.assertEqual(first, second)

    def test_database_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            database = root / "plugins/software-engineering-team/cache.sqlite"
            database.write_bytes(b"fixture")
            self.assertIn("packaged_state_files", self.checks(root))

    def test_skill_project_scope_is_closed_and_external_is_entry_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            path = (
                root / "plugins/software-engineering-team/skill-content/"
                "issue-report/SKILL.md"
            )
            text = path.read_text(encoding="utf-8").replace(
                "project_scope: external", "project_scope: remote"
            )
            path.write_text(text, encoding="utf-8")
            self.assertIn("frontmatter_shape", self.checks(root))

        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            path = (
                root / "plugins/software-engineering-team/skill-content/"
                "issue-report/SKILL.md"
            )
            text = path.read_text(encoding="utf-8").replace(
                "exposure: entry", "exposure: internal"
            )
            path.write_text(text, encoding="utf-8")
            self.assertIn("frontmatter_shape", self.checks(root))

    def test_plugin_dependency_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            path = root / "platforms/claude/software-engineering-team/manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["dependencies"] = ["some-team"]
            path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            self.assertIn("single_team_contract", self.checks(root))

    def test_graph_palette_identity_query_and_rgb_are_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            path = (
                root / "plugins/software-engineering-team/skill-content/"
                "obsidian-vault/data/vault-policy.json"
            )
            policy = json.loads(path.read_text(encoding="utf-8"))
            story = next(
                group for group in policy["graph_color_groups"]
                if group["id"] == "story"
            )
            story["query"] = "tag:#doc/wrong"
            story["rgb"] = -1
            path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
            self.assertIn("vault_policy_shape", self.checks(root))

    def test_lazy_fragment_property_drift_is_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            path = (
                root / "plugins/software-engineering-team/skill-content/"
                "obsidian-vault/data/vault-policy.json"
            )
            policy = json.loads(path.read_text(encoding="utf-8"))
            del policy["property_types"]["package_status"]
            path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
            findings = validate.run(root)
            self.assertTrue(any(
                finding.check == "vault_policy_shape"
                and "lazy_fragments['business_analysis']"
                in finding.message
                and "package_status" in finding.message
                for finding in findings
            ))

    def test_vault_policy_and_types_seed_property_maps_cannot_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            path = (
                root / "plugins/software-engineering-team/templates/vault/"
                ".obsidian/types.json"
            )
            types = json.loads(path.read_text(encoding="utf-8"))
            types["types"]["owner_role"] = "number"
            path.write_text(json.dumps(types, indent=2) + "\n", encoding="utf-8")
            findings = validate.run(root)
            self.assertTrue(any(
                finding.check == "vault_policy_shape"
                and "types.json property map" in finding.message
                for finding in findings
            ))

    def test_delivery_contract_set_and_merge_policy_are_validated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            data = (
                root / "plugins/software-engineering-team/skill-content/"
                "deliver/data"
            )
            receipt = data / "delivery-receipt-contract.json"
            receipt.unlink()
            self.assertIn("delivery_contract_shape", self.checks(root))

        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            protocol_path = (
                root / "plugins/software-engineering-team/skill-content/"
                "deliver/data/delivery-protocol-1.json"
            )
            protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
            protocol["merge_policy"] = "provider default"
            protocol_path.write_text(
                json.dumps(protocol, indent=2) + "\n", encoding="utf-8"
            )
            self.assertIn("delivery_contract_shape", self.checks(root))

    def test_execution_profile_tables_are_validated(self):
        for relative, mutate in (
                ("platforms/claude/execution-profiles.json",
                 lambda value: value["profiles"]["auto"]["high"].update(model="gpt-5")),
                ("platforms/claude/execution-profiles.json",
                 lambda value: value["profiles"]["auto"]["low"].update(effort="extreme")),
                ("platforms/codex/execution-profiles.json",
                 lambda value: value["profiles"]["auto"]["inherit"].update(effort="low")),
                ("platforms/codex/execution-profiles.json",
                 lambda value: value["profiles"].update(fast={})),
                ("tools/data/models.json",
                 lambda value: value["reasoning_levels"].append("extreme"))):
            with self.subTest(path=relative), \
                    tempfile.TemporaryDirectory() as temporary:
                root = self.fixture(temporary)
                path = root / relative
                value = json.loads(path.read_text(encoding="utf-8"))
                mutate(value)
                path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
                self.assertIn("execution_profiles", self.checks(root))

        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            path = root / "tools/data/models.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["reasoning_levels"].remove("low")
            path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
            findings = validate.run(root)
            self.assertIn(
                ("tools/data/models.json", "model_config_shape"),
                {(finding.path, finding.check) for finding in findings},
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            (root / "platforms/codex/execution-profiles.json").unlink()
            findings = validate.run(root)
            self.assertIn(
                ("platforms/codex/execution-profiles.json", "execution_profiles"),
                {(finding.path, finding.check) for finding in findings},
            )

    def test_delivery_verification_policy_rejects_diagnostic_seal_and_weakened_scope(self):
        for mutate in (
                lambda value: value["final_modes"].update(qa_engineer=["qa_diagnostic", "qa_final"]),
                lambda value: value["mutation_scope"].update(unknown_file_policy="skip"),
                lambda value: value.update(raw_evidence_max_age_seconds=86401),
                lambda value: value.update(undeclared_field=True)):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as temporary:
                root = self.fixture(temporary)
                path = root / "plugins/software-engineering-team/skill-content/deliver/data/delivery-verification-policy.json"
                value = json.loads(path.read_text())
                mutate(value)
                path.write_text(json.dumps(value))
                self.assertIn("delivery_contract_shape", self.checks(root))


PLUGIN_ROOT = "plugins/software-engineering-team"
PANELS = f"{PLUGIN_ROOT}/skill-content/challenge-review/data/review-panels.json"


class ReviewPanelValidatorTests(unittest.TestCase):
    """Lens sets are validated data; flows and prose anchor to declared ids."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        fixtures.make_valid_root(self.root)
        self.panels = self.root / PANELS
        self.original = self.panels.read_bytes()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def panel_messages(self) -> list[str]:
        return [finding.message for finding in validate.run(self.root)
                if finding.check == "review_panels"]

    def write_steps(self, mutate) -> None:
        data = json.loads(self.original)
        mutate(data["review_steps"])
        self.panels.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def edit_text(self, relative: str, old: str, new: str) -> str:
        path = self.root / relative
        before = path.read_text(encoding="utf-8")
        self.assertIn(old, before)
        path.write_text(before.replace(old, new, 1), encoding="utf-8")
        return before

    def assert_rejected(self, fragment: str) -> None:
        messages = self.panel_messages()
        self.assertTrue(any(fragment in message for message in messages), messages)

    def test_shipped_panel_data_is_clean(self):
        self.assertEqual(self.panel_messages(), [])

    def test_data_shape_errors_are_rejected(self):
        def duplicate_lens(steps):
            steps["solution_design"]["lenses"].append(dict(steps["solution_design"]["lenses"][0]))

        def unknown_panel_lens(steps):
            steps["solution_design"]["default_panel"][0].append("ghost-lens")

        def unassigned_lens(steps):
            steps["design_system"]["default_panel"].pop()

        def twice_assigned_lens(steps):
            steps["design_system"]["default_panel"].append(["accessibility-and-states"])

        def uncovered_section(steps):
            steps["backlog_epic"]["lenses"][0]["covers"].remove("Slicing")

        def unknown_section(steps):
            steps["backlog_epic"]["lenses"][0]["covers"].append("Ghost Section")

        def double_owned_section(steps):
            steps["backlog_epic"]["lenses"][1]["covers"].append("Scope")

        def lens_owns_panel_section(steps):
            steps["backlog_root"]["lenses"][2]["covers"].append("Verdict")

        def uncovering_lens(steps):
            del steps["backlog_epic"]["lenses"][2]["covers"]

        def covers_without_note(steps):
            steps["solution_design"]["lenses"][0]["covers"] = ["Scope"]

        def unresolvable_note(steps):
            steps["backlog_root"]["review_note"]["sections"] = "backlog_contract.no_such_list"

        def bad_step_id(steps):
            steps["Design-System"] = steps.pop("design_system")

        def bad_lens_id(steps):
            steps["design_system"]["lenses"][0]["id"] = "Token_System"

        def extra_step_key(steps):
            steps["design_system"]["severity"] = "strict"

        def blank_focus(steps):
            steps["design_system"]["lenses"][1]["focus"] = "  "

        cases = (
            (duplicate_lens, "duplicate lens id 'technology-fit-and-traceability'"),
            (unknown_panel_lens, "default_panel names unknown lens id 'ghost-lens'"),
            (lambda steps: steps["design_system"].update(lenses=[]),
             "empty panel, lenses declares no lens"),
            (lambda steps: steps["design_system"].update(default_panel=[]),
             "empty panel, default_panel has no assignment"),
            (lambda steps: steps["design_system"]["default_panel"].append([]),
             "empty panel assignment"),
            (unassigned_lens,
             "lens 'contradictions-and-upstream-fit' is in no default_panel assignment"),
            (twice_assigned_lens,
             "duplicate lens id 'accessibility-and-states' across default_panel assignments"),
            (lambda steps: steps["design_system"].update(reader_role="ghost-reviewer"),
             "unknown reader_role 'ghost-reviewer'"),
            (uncovered_section, "no lens covers review-note section 'Slicing'"),
            (unknown_section, "unknown review-note section 'Ghost Section'"),
            (double_owned_section, "review-note section 'Scope' has more than one owner"),
            (lens_owns_panel_section, "review-note section 'Verdict' has more than one owner"),
            (uncovering_lens,
             "lens 'dependencies-and-role-ownership' must list the note sections it covers"),
            (covers_without_note, "covers needs a review_note declaration"),
            (unresolvable_note, "review_note needs sections"),
            (bad_step_id, "id must be lowercase snake_case"),
            (bad_lens_id, "lens id 'Token_System' must be kebab-case"),
            (extra_step_key, "must hold reader_role, lenses, default_panel"),
            (blank_focus, "every lens holds an id, non-empty focus text"),
            (lambda steps: steps.clear(), "review_steps must declare at least one review step"),
        )
        for mutate, fragment in cases:
            with self.subTest(fragment=fragment):
                self.panels.write_bytes(self.original)
                self.write_steps(mutate)
                self.assert_rejected(fragment)

        data = json.loads(self.original)
        data["schema_version"] = 2
        self.panels.write_text(json.dumps(data), encoding="utf-8")
        self.assert_rejected("data must hold exactly schema_version 1 and review_steps")

    def test_duplicate_raw_json_key_is_rejected(self):
        self.edit_text(PANELS, '"reader_role": "design-system-reviewer",',
                       '"reader_role": "design-system-reviewer",'
                       ' "reader_role": "design-system-reviewer",')
        self.assert_rejected("not valid unique-key JSON")

    def test_steps_and_flow_anchors_must_match(self):
        self.write_steps(lambda steps: steps.update(ghost_step=dict(steps["design_system"])))
        self.assert_rejected("review step 'ghost_step' is unknown to every flow")

        self.panels.write_bytes(self.original)
        self.write_steps(lambda steps: steps.pop("design_system"))
        self.assert_rejected("review panel 'design_system' is missing from the lens data")

        self.panels.write_bytes(self.original)
        flow = f"{PLUGIN_ROOT}/flows/operation.md"
        before = self.edit_text(flow, "review panel `operation_environment`",
                                "review panel `operation_runtime`")
        messages = self.panel_messages()
        self.assertTrue(any("review panel 'operation_runtime' is missing" in message
                            for message in messages), messages)
        self.assertTrue(any("review step 'operation_environment' is unknown to every flow"
                            in message for message in messages), messages)
        (self.root / flow).write_text(before, encoding="utf-8")

        # An anchor outside flows/ does not wire a step into a flow.
        agent = f"{PLUGIN_ROOT}/agents/design-system-reviewer.md"
        self.edit_text(f"{PLUGIN_ROOT}/flows/design-system.md",
                       "review panel `design_system`", "the design panel")
        self.assertIn("review panel `design_system`",
                      (self.root / agent).read_text(encoding="utf-8").replace("\n", " "))
        self.assert_rejected("review step 'design_system' is unknown to every flow")

    def test_prose_lens_names_must_be_declared(self):
        self.edit_text(f"{PLUGIN_ROOT}/agents/backlog-reviewer.md",
                       "`scope-and-slicing` owns", "`scope-and-sizing` owns")
        self.assert_rejected("prose names unknown lens 'scope-and-sizing'")

    def test_missing_data_with_live_anchors_is_rejected(self):
        self.panels.unlink()
        self.assert_rejected("review panels are referenced but their lens data is missing")

    def test_new_lens_regrouping_and_step_are_data_plus_anchor(self):
        def extend(steps):
            steps["design_system"]["lenses"].append({
                "id": "content-and-voice",
                "focus": "Microcopy and voice rules are complete and consistent.",
            })
            steps["design_system"]["default_panel"].append(["content-and-voice"])
            steps["solution_design"]["default_panel"] = [
                ["technology-fit-and-traceability", "sustainability-and-operability"],
                ["cost-and-lock-in", "security-and-compliance"],
            ]
            steps["operation_governance"] = {
                "reader_role": "qa-engineer",
                "lenses": [{"id": "gate-fit", "focus": "Every gate names its owner."}],
                "default_panel": [["gate-fit"]],
            }

        self.write_steps(extend)
        flow = self.root / PLUGIN_ROOT / "flows/operation.md"
        flow.write_text(flow.read_text(encoding="utf-8")
                        + "\nGovernance changes run review panel `operation_governance`.\n",
                        encoding="utf-8")
        self.assertEqual(validate.run(self.root), [])


if __name__ == "__main__":
    unittest.main()
