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

    def write_data(self, mutate) -> None:
        data = json.loads(self.original)
        mutate(data)
        self.panels.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def write_steps(self, mutate) -> None:
        self.write_data(lambda data: mutate(data["review_steps"]))

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

    def test_every_read_only_reader_runs_as_its_lens_variant(self):
        registry = self.root / SWITCHES
        original = registry.read_bytes()

        def variants(mutate) -> None:
            data = json.loads(original)
            mutate(data["switches"]["review_panels"])
            registry.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

        variants(lambda switch: switch["agent_variants"]["lens_panel"]["agents"].remove(
            "solution-reviewer"))
        self.assert_rejected("read-only panel reader 'solution-reviewer' has no 'lens_panel'"
                             " agent variant")
        variants(lambda switch: switch["agent_variants"]["lens_panel"]["agents"].append(
            "analysis-challenger"))
        self.assert_rejected("'lens_panel' agent variant 'analysis-challenger' reads no"
                             " read-only review step")
        variants(lambda switch: switch.pop("agent_variants"))
        self.assert_rejected("switch 'review_panels' must declare the 'lens_panel' agent variants")
        registry.write_bytes(original)
        self.assertEqual(self.panel_messages(), [])

    def test_a_panel_flow_must_name_the_review_panels_switch(self):
        flow = f"{PLUGIN_ROOT}/flows/design-system.md"
        self.edit_text(flow, "Switch `review_panels`:", "The panel switch:")
        self.assert_rejected(
            "flow design-system.md runs a review panel but never names switch `review_panels`")

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
        protocol = (f"{PLUGIN_ROOT}/skill-content/challenge-review/references/"
                    "switch-review_panels-lens_panel.md")
        self.edit_text(f"{PLUGIN_ROOT}/flows/design-system.md",
                       "review panel `design_system`", "the design panel")
        self.assertIn("review panel `design_system`",
                      (self.root / protocol).read_text(encoding="utf-8").replace("\n", " "))
        self.assert_rejected("review step 'design_system' is unknown to every flow")

    def test_prose_lens_names_must_be_declared(self):
        flow = self.root / PLUGIN_ROOT / "flows/backlog-planning.md"
        flow.write_text(flow.read_text(encoding="utf-8")
                        + "\nThe lens `scope-and-slicing` owns story size.\n", encoding="utf-8")
        self.assertEqual(self.panel_messages(), [])
        self.edit_text(f"{PLUGIN_ROOT}/flows/backlog-planning.md",
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


SWITCHES = f"{PLUGIN_ROOT}/skill-content/configure/data/process-switches.json"
FIXTURE_SWITCH = {
    "summary": "How the fixture step runs.",
    "flows": ["operation"],
    "values": [
        {"id": "current", "tradeoffs": "Today's behaviour and the measured baseline."},
        {"id": "fast", "tradeoffs": "Fewer passes; its recall is still unmeasured."},
    ],
    "default": "current",
    "metric": "Minutes per fixture step against the baseline.",
    "promotion": {"unit": "3 Deliveries", "threshold": "Median minutes at most half the baseline."},
}
FIXTURE_ANCHOR = "\nSwitch `fixture_mode` selects how step 3 runs.\n"


class ProcessSwitchValidatorTests(unittest.TestCase):
    """The package registry declares every process switch and its owning flows."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        fixtures.make_valid_root(self.root)
        self.registry = self.root / SWITCHES
        self.original = self.registry.read_bytes()
        self.flow = self.root / PLUGIN_ROOT / "flows/operation.md"
        self.flow_text = self.flow.read_text(encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def messages(self) -> list[str]:
        return [finding.message for finding in validate.run(self.root)
                if finding.check == "process_switches"]

    def declare(self, **switches) -> None:
        data = json.loads(self.original)
        data["switches"].update(switches)
        self.registry.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def anchor(self, text: str = FIXTURE_ANCHOR) -> None:
        self.flow.write_text(self.flow_text + text, encoding="utf-8")

    def assert_rejected(self, fragment: str) -> None:
        messages = self.messages()
        self.assertTrue(any(fragment in message for message in messages), messages)

    def test_shipped_registry_and_an_anchored_switch_are_clean(self):
        self.assertEqual(self.messages(), [])
        self.declare(fixture_mode=FIXTURE_SWITCH)
        self.anchor()
        self.assertEqual(validate.run(self.root), [])

    def test_registry_shape_errors_are_rejected(self):
        def switch(**changes):
            spec = json.loads(json.dumps(FIXTURE_SWITCH))
            spec.update(changes)
            return {key: value for key, value in spec.items() if value is not None}

        cases = (
            (switch(default="ghost"),
             "default 'ghost' is not one of its values ['current', 'fast']"),
            (switch(metric=None), "needs a component metric"),
            (switch(metric="  "), "needs a component metric"),
            (switch(promotion=None), "needs a promotion rule with a unit and a threshold"),
            (switch(promotion={"unit": "3 Deliveries"}),
             "needs a promotion rule with a unit and a threshold"),
            (switch(flows=[]), "flows must list at least one owning flow"),
            (switch(flows=["operation", "ghost-flow"]), "names unknown flow 'ghost-flow'"),
            (switch(values=[FIXTURE_SWITCH["values"][0]]), "values must list at least two values"),
            (switch(values=[*FIXTURE_SWITCH["values"], dict(FIXTURE_SWITCH["values"][1])]),
             "duplicate value 'fast'"),
            (switch(values=[{"id": "current"}, FIXTURE_SWITCH["values"][1]]),
             "every value holds an id and non-empty tradeoffs"),
            (switch(summary=None), "needs a summary of what it decides"),
            (switch(owner="qa"), "unknown keys ['owner']"),
            (switch(issue=0), "issue must be a positive issue number"),
        )
        self.anchor()
        for spec, fragment in cases:
            with self.subTest(fragment=fragment):
                self.declare(fixture_mode=spec)
                self.assert_rejected(fragment)
        self.declare(**{"Fixture-Mode": FIXTURE_SWITCH})
        self.assert_rejected("id must be lowercase snake_case")
        self.registry.write_text('{"schema_version": 2, "switches": {}}\n', encoding="utf-8")
        self.assert_rejected("registry must hold exactly schema_version 1 and switches")
        self.registry.write_text('{"schema_version": 1, "switches": {}, "switches": {}}\n',
                                 encoding="utf-8")
        self.assert_rejected("not valid unique-key JSON")

    def test_flows_and_switches_must_name_each_other(self):
        self.declare(fixture_mode=FIXTURE_SWITCH)
        self.assert_rejected("switch 'fixture_mode' is not named by its owning flow 'operation'")

        self.registry.write_bytes(self.original)
        self.anchor()
        self.assert_rejected("flow operation.md names undeclared switch 'fixture_mode'")

        self.declare(fixture_mode=dict(FIXTURE_SWITCH, flows=["design-system"]))
        messages = self.messages()
        self.assertTrue(any("flow operation.md names switch 'fixture_mode' but is not one of"
                            " its owning flows" in message for message in messages), messages)
        self.assertTrue(any("is not named by its owning flow 'design-system'" in message
                            for message in messages), messages)

        self.registry.unlink()
        self.assert_rejected("process switches are named but the switch registry is missing")

    def test_switch_references_are_bound_by_the_policy_never_linked(self):
        reference = "skill-content/challenge-review/references/switch-fixture_mode-fast.md"
        path = self.root / PLUGIN_ROOT / reference
        self.declare(fixture_mode=FIXTURE_SWITCH)
        self.anchor(FIXTURE_ANCHOR + f"At `fast` follow `{reference}`.\n")
        path.write_text("# Fast fixture step\n", encoding="utf-8")
        # No SKILL.md links it, and no unlinked-reference warning is raised for it.
        self.assertEqual(validate.run(self.root), [])

        self.anchor()
        self.assert_rejected(f"switch reference {reference} is named by no owning flow of"
                             " switch 'fixture_mode'")
        self.anchor(FIXTURE_ANCHOR + f"At `fast` follow `{reference}`.\n")
        cases = (
            ("switch-ghost_mode-fast.md", "switch reference names undeclared switch 'ghost_mode'"),
            ("switch-fixture_mode-slow.md",
             "switch reference names undeclared value 'slow' of switch 'fixture_mode'"),
            ("switch-fixture_mode-current.md",
             "switch reference names the default value 'current' of switch 'fixture_mode'"),
            ("switch-fixture.md", "switch reference must be named switch-<switch>-<value>.md"),
        )
        for name, fragment in cases:
            with self.subTest(name=name):
                extra = path.with_name(name)
                extra.write_text("# Other\n", encoding="utf-8")
                self.assert_rejected(fragment)
                extra.unlink()
        skill = self.root / PLUGIN_ROOT / "skill-content/challenge-review/SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8")
                         + "\n- [fast](references/switch-fixture_mode-fast.md): fast mode."
                           " Read when fast.\n", encoding="utf-8")
        self.assert_rejected("SKILL.md links a switch reference")
        self.registry.unlink()
        self.flow.write_text(self.flow_text, encoding="utf-8")
        self.assert_rejected("process switches are named but the switch registry is missing")

    def test_agent_variants_are_validated(self):
        variant = {"suffix": "quick", "tier": "medium", "description": "Quick variant.",
                   "agents": ["backlog-reviewer"]}
        self.anchor()
        self.declare(fixture_mode=dict(FIXTURE_SWITCH, agent_variants={"fast": variant}))
        self.assertEqual(self.messages(), [])
        cases = (
            ({"current": variant}, "agent variants belong to a declared value other than the default"),
            ({"fast": dict(variant, tier="extreme")},
             "variant tier 'extreme' is not a declared reasoning tier"),
            ({"fast": dict(variant, agents=["ghost-reviewer"])}, "unknown variant agent 'ghost-reviewer'"),
            ({"fast": dict(variant, agents=[])}, "variant agents must list at least one canonical agent"),
            ({"fast": dict(variant, suffix="Quick_Pass")}, "variant suffix must be kebab-case"),
            ({"fast": dict(variant, description="Runs as: quick")},
             "variant description must be one non-empty line without a colon"),
            ({"fast": dict(variant, model="fast")},
             "an agent variant holds exactly suffix, tier, description and agents"),
            ({}, "agent_variants must map a switch value to its variant"),
        )
        for variants, fragment in cases:
            with self.subTest(fragment=fragment):
                self.declare(fixture_mode=dict(FIXTURE_SWITCH, agent_variants=variants))
                self.assert_rejected(fragment)
        second = dict(FIXTURE_SWITCH, agent_variants={"fast": variant})
        self.declare(fixture_mode=dict(FIXTURE_SWITCH, agent_variants={"fast": variant}),
                     other_mode=second)
        self.anchor(FIXTURE_ANCHOR + "Switch `other_mode` selects the same step.\n")
        self.assert_rejected("variant 'backlog-reviewer-quick' collides with switch 'fixture_mode'")


if __name__ == "__main__":
    unittest.main()
