"""Mutation tests for the repository validator's active architecture rules."""

from __future__ import annotations

import hashlib
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
                 lambda value: value["profiles"]["auto"]["high"].update({"class": "ghost"})),
                ("platforms/claude/execution-profiles.json",
                 lambda value: value["profiles"]["auto"]["high"].update(model="claude-opus-5-5")),
                ("platforms/claude/execution-profiles.json",
                 lambda value: value["profiles"]["auto"]["low"].update(effort="high")),
                ("platforms/codex/execution-profiles.json",
                 lambda value: value["profiles"]["auto"]["inherit"].update(effort="low")),
                ("platforms/codex/execution-profiles.json",
                 lambda value: value["profiles"].update(fast={})),
                ("platforms/claude/model-catalog.json",
                 lambda value: value["classes"]["frontier"].update(id="opus")),
                ("platforms/codex/model-catalog.json",
                 lambda value: value["classes"]["fast"].update(efforts=["low", "ultra", "turbo"])),
                ("tools/data/models.json",
                 lambda value: value["reasoning_levels"].append("extreme"))):
            with self.subTest(path=relative), \
                    tempfile.TemporaryDirectory() as temporary:
                root = self.fixture(temporary)
                path = root / relative
                value = json.loads(path.read_text(encoding="utf-8"))
                mutate(value)
                path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
                findings = validate.run(root)
                self.assertIn("execution_profiles", {finding.check for finding in findings})
                if relative.startswith("platforms/"):
                    self.assertIn((relative, "execution_profiles"),
                                  {(finding.path, finding.check) for finding in findings})

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

        for relative in ("platforms/codex/execution-profiles.json",
                         "platforms/claude/model-catalog.json"):
            with self.subTest(missing=relative), \
                    tempfile.TemporaryDirectory() as temporary:
                root = self.fixture(temporary)
                (root / relative).unlink()
                findings = validate.run(root)
                self.assertIn(
                    (relative, "execution_profiles"),
                    {(finding.path, finding.check) for finding in findings},
                )

    def test_malformed_model_config_is_a_finding_not_a_crash(self):
        missing = "model config is missing or not valid JSON"
        shape = "reasoning_levels must be a non-empty kebab-case list"
        cases = {
            "missing file": (None, missing),
            "invalid JSON": (b'{"schema_version": 1,', missing),
            "not UTF-8": (b'{"schema_version": 1, "reasoning_levels": ["\xff"]}', missing),
            "not an object": (b'["high", "medium"]', "model config must be a JSON object"),
            "levels a number": (b'{"schema_version": 1, "reasoning_levels": 5}', shape),
            "levels a boolean": (b'{"schema_version": 1, "reasoning_levels": true}', shape),
            "levels a string": (b'{"schema_version": 1, "reasoning_levels": "high"}', shape),
            "levels nested lists": (b'{"schema_version": 1, "reasoning_levels": [["high"]]}', shape),
            "levels objects": (b'{"schema_version": 1, "reasoning_levels": [{"id": "high"}]}', shape),
            "levels absent": (b'{"schema_version": 1}', shape),
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            # An unrelated defect proves the run still reaches every later check.
            (root / "plugins/software-engineering-team/cache.sqlite").write_bytes(b"fixture")
            path = root / "tools/data/models.json"
            for case, (content, message) in cases.items():
                with self.subTest(case=case):
                    if content is None:
                        path.unlink()
                    else:
                        path.write_bytes(content)
                    findings = validate.run(root)
                    self.assertTrue(any(
                        finding.path == "tools/data/models.json"
                        and finding.check == "model_config_shape"
                        and message in finding.message
                        for finding in findings), findings)
                    self.assertIn("packaged_state_files", {finding.check for finding in findings})
                    # The agent tiers fall back to the builder's tiers instead of
                    # being judged against a malformed list.
                    self.assertEqual([finding for finding in findings
                                      if finding.check == "frontmatter_shape"], [])

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

    def test_item_implementation_schedules_follow_the_switch_registry(self):
        """An Item records an implementation_schedule switch value, and one without reads as today's order."""
        document = "plugins/software-engineering-team/skill-content/deliver/data/delivery-document-contract.json"
        registry = "plugins/software-engineering-team/skill-content/configure/data/process-switches.json"
        for relative, mutate in (
                (document, lambda value: value["document_types"]["delivery_item"].update(
                    implementation_schedules=["sequential_v1"])),
                (document, lambda value: value["document_types"]["delivery_item"].update(
                    missing_implementation_schedule="parallel_lanes_v1")),
                (registry, lambda value: value["switches"]["implementation_schedule"]["values"].append(
                    {"id": "parallel_lanes_v2", "tradeoffs": "Unmeasured."}))):
            with self.subTest(path=relative), tempfile.TemporaryDirectory() as temporary:
                root = self.fixture(temporary)
                path = root / relative
                value = json.loads(path.read_text(encoding="utf-8"))
                mutate(value)
                path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
                self.assertTrue(any(finding.check == "delivery_contract_shape"
                                    and "implementation schedules" in finding.message
                                    for finding in validate.run(root)))


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
            (switch(reference_scope="every_task"),
             "reference_scope must be one of ['owning_flows']"),
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

    def test_switch_parameters_are_validated(self):
        declared = {"summary": "Owner-set limit per fixture measure.", "values": ["fast"],
                    "declared_by": {"path": MEASURES_RELPATH, "key": "measures"},
                    "type": "positive_integer", "min_count": 1}
        self.anchor()
        self.declare(fixture_mode=dict(FIXTURE_SWITCH, parameters=declared))
        self.assertEqual(self.messages(), [])
        # A promotion of a value that takes parameters ships package limits.
        measure = sorted(json.loads((self.root / PLUGIN_ROOT / MEASURES_RELPATH).read_text(
            encoding="utf-8"))["measures"])[0]
        self.declare(fixture_mode=dict(FIXTURE_SWITCH, parameters=dict(
            declared, package_limits={measure: 8})))
        self.assertEqual(self.messages(), [])
        cases = (
            (dict(declared, values=["current"]),
             "parameters belong to a declared value other than the default, not 'current'"),
            (dict(declared, values=["ghost"]),
             "parameters belong to a declared value other than the default, not 'ghost'"),
            (dict(declared, values=[]), "parameters must list the values that take them"),
            (dict(declared, type="fraction"), "parameter type 'fraction' is not one of"),
            (dict(declared, declared_by={"path": "skill-content/ghost.json", "key": "measures"}),
             "declared_by path 'skill-content/ghost.json' is not a file of the package"),
            (dict(declared, declared_by={"path": "../outside.json", "key": "measures"}),
             "is not a file of the package"),
            (dict(declared, declared_by={"path": MEASURES_RELPATH, "key": "ghost"}),
             "key 'ghost' must map snake_case parameter ids to a summary"),
            (dict(declared, declared_by=MEASURES_RELPATH),
             "declared_by must name the package data file and the key"),
            (dict(declared, min_count=5), "min_count must be a whole number no larger than"),
            (dict(declared, min_count=True), "min_count must be a whole number no larger than"),
            (dict(declared, summary=" "), "parameters need a summary"),
            ({key: value for key, value in declared.items() if key != "type"},
             "parameters hold exactly summary, values, declared_by, type and min_count"),
            (dict(declared, limits={}),
             "parameters hold exactly summary, values, declared_by, type and min_count"),
            (dict(declared, package_limits={"ghost": 3}),
             "package_limits must map declared parameter ids to positive whole numbers"),
            (dict(declared, package_limits={"acceptance_criteria": 0}),
             "package_limits must map declared parameter ids to positive whole numbers"),
        )
        for parameters, fragment in cases:
            with self.subTest(fragment=fragment):
                self.declare(fixture_mode=dict(FIXTURE_SWITCH, parameters=parameters))
                self.assert_rejected(fragment)

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

    def test_value_data_is_bound_with_one_value_and_its_references(self):
        reference = "skill-content/challenge-review/references/switch-fixture_mode-fast.md"
        data = "skill-content/challenge-review/data/fast-table.json"
        (self.root / PLUGIN_ROOT / reference).write_text("# Fast fixture step\n", encoding="utf-8")
        (self.root / PLUGIN_ROOT / data).write_text("{}\n", encoding="utf-8")
        self.anchor(FIXTURE_ANCHOR + f"At `fast` follow `{reference}`.\n")
        self.declare(fixture_mode=dict(FIXTURE_SWITCH, value_data={"fast": [data]}))
        self.assertEqual(self.messages(), [])
        # A file that values of several switches read is listed under each.
        shared = "skill-content/challenge-review/data/review-panels.json"
        self.declare(fixture_mode=dict(FIXTURE_SWITCH, value_data={"fast": [data, shared]}))
        self.assertEqual(self.messages(), [])
        cases = (
            ({"current": [data]}, "value data belongs to a declared value other than the default"),
            ({"slow": [data]}, "value data belongs to a declared value other than the default"),
            ({"fast": []}, "value data must list at least one data file"),
            ({"fast": ["skill-content/challenge-review/references/triage.md"]},
             "is not a skill data JSON file"),
            ({"fast": ["skill-content/challenge-review/data/ghost.json"]}, "does not exist"),
            ({"fast": [data, data]}, f"value data {data!r} is listed more than once"),
            ({}, "value_data must map a switch value to its data files"),
        )
        for value_data, fragment in cases:
            with self.subTest(fragment=fragment):
                self.declare(fixture_mode=dict(FIXTURE_SWITCH, value_data=value_data))
                self.assert_rejected(fragment)
        (self.root / PLUGIN_ROOT / reference).unlink()
        self.anchor()
        self.declare(fixture_mode=dict(FIXTURE_SWITCH, value_data={"fast": [data]}))
        self.assert_rejected("value data needs a switch reference of that value to bind it")


MEASURES_RELPATH = "skill-content/product-planning/data/story-size-measures.json"
MEASURES = f"{PLUGIN_ROOT}/{MEASURES_RELPATH}"


class StorySizeMeasureValidatorTests(unittest.TestCase):
    """Every story size measure names a derivation the backlog compiler has."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        fixtures.make_valid_root(self.root)
        self.path = self.root / MEASURES
        self.original = json.loads(self.path.read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def messages(self) -> list[str]:
        return [finding.message for finding in validate.run(self.root)
                if finding.check == "story_size_measures"]

    def write(self, mutate) -> None:
        value = json.loads(json.dumps(self.original))
        mutate(value)
        self.path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def test_shipped_measures_are_clean(self):
        self.assertEqual(self.messages(), [])

    def test_measure_shape_and_derivation_errors_are_rejected(self):
        def measure(name, **changes):
            return lambda value: value["measures"][name].update(changes)

        def extra(value):
            value["measures"]["acceptance_criteria"]["limit"] = 12

        def rename(value):
            value["measures"]["Test-Scenarios"] = value["measures"].pop("test_scenarios")

        cases = (
            (measure("acceptance_criteria", derivation="ghost_count"),
             "measure 'acceptance_criteria': derivation 'ghost_count' is not one the backlog"
             " compiler implements"),
            # A list is a finding, never a crash on an unhashable value.
            (measure("acceptance_criteria", derivation=["acceptance_checklist_lines"]),
             "measure 'acceptance_criteria': derivation must be one derivation name, not list"),
            (measure("test_scenarios", derivation={"name": "plan_scenario_blocks"}),
             "measure 'test_scenarios': derivation must be one derivation name, not dict"),
            (measure("contract_deltas", derivation="owner_and_supporting_roles"),
             "measure 'implementation_roles': repeats the derivation of measure"
             " 'contract_deltas'"),
            (measure("test_scenarios", summary=" "), "holds exactly a non-empty summary and a"
             " derivation"),
            (extra, "holds exactly a non-empty summary and a derivation"),
            (rename, "measure 'Test-Scenarios': id must be lowercase snake_case"),
            (lambda value: value.update(schema_version=2), "must hold exactly schema_version 1"),
            (lambda value: value.update(measures={}), "must hold exactly schema_version 1"),
        )
        for mutate, fragment in cases:
            with self.subTest(fragment=fragment):
                self.write(mutate)
                messages = self.messages()
                self.assertTrue(any(fragment in message for message in messages), messages)
        self.path.write_text('{"schema_version": 1, "measures": {}, "measures": {}}\n',
                             encoding="utf-8")
        self.assertTrue(any("not valid unique-key JSON" in message for message in self.messages()))

    def test_a_compiler_without_the_derivation_registry_is_rejected(self):
        compiler = self.root / PLUGIN_ROOT / "scripts/backlog_compile.py"
        compiler.write_text(compiler.read_text(encoding="utf-8").replace(
            "STORY_SIZE_DERIVATIONS = {", "STORY_SIZE_COUNTS = {", 1), encoding="utf-8")
        self.assertIn("scripts/backlog_compile.py declares no STORY_SIZE_DERIVATIONS registry",
                      self.messages())


# ---------------------------------------------------------------------------
# Builder registry: one deliberately broken fixture per validator check, the
# doctrine test_ba_compile.py and test_vault_check.py apply to their checkers.
# Each builder breaks the valid fixture repository so that its check reports
# it; a check without a builder fails the lockstep test. Builders change files
# only through the helpers below, which remember every original so the test
# puts the fixture back after each builder instead of copying it per check.
# ---------------------------------------------------------------------------

AGENT = f"{PLUGIN_ROOT}/agents/product-owner.md"
FLOW = f"{PLUGIN_ROOT}/flows/operation.md"
ORIGINALS: dict = {}


def remember(path: Path) -> Path:
    ORIGINALS.setdefault(path, path.read_bytes() if path.is_file() else None)
    return path


def restore() -> None:
    for path, original in ORIGINALS.items():
        if original is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(original)
    ORIGINALS.clear()


def edit(root: Path, relative: str, old: str, new: str) -> None:
    path = remember(root / relative)
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(f"{relative} no longer holds {old!r}; update its builder")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def append(root: Path, relative: str, text: str) -> None:
    path = remember(root / relative)
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def write(root: Path, relative: str, text: str) -> None:
    remember(root / relative).write_text(text, encoding="utf-8")


def remove(root: Path, relative: str) -> None:
    remember(root / relative).unlink()


def edit_json(root: Path, relative: str, mutate) -> None:
    path = remember(root / relative)
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8") + b"\0")
        if path.is_file():
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


VALIDATOR_BUILDERS = {
    "frontmatter_shape": lambda root: edit(
        root, AGENT, "output_contract: prose", "output_contract: table"),
    "agent_name": lambda root: edit(root, AGENT, "name: product-owner", "name: product-lead"),
    "skill_name": lambda root: edit(
        root, f"{PLUGIN_ROOT}/skill-content/configure/SKILL.md",
        "name: configure", "name: configuration"),
    "trigger_policy": lambda root: edit(
        root, AGENT, "description: ", "description: Use when planning. "),
    "size_caps": lambda root: append(
        root, f"{PLUGIN_ROOT}/constitution.md", "- Filler principle.\n" * 61),
    "section_contract": lambda root: edit(root, AGENT, "## Output Contract", "## Output"),
    "content_bans": lambda root: append(root, FLOW, "\nOne step \u2014 then another.\n"),
    "agent_tech_nouns": lambda root: append(root, AGENT, "- Reads pytest reports.\n"),
    "handwritten_counts": lambda root: append(root, FLOW, "\nThe team ships 12 agents.\n"),
    "dead_links": lambda root: append(root, FLOW, "\nSee [the ghost](ghost.md).\n"),
    "reference_triggers": lambda root: append(
        root, f"{PLUGIN_ROOT}/skill-content/challenge-review/SKILL.md",
        "- [triage again](references/triage.md): triage.\n"),
    "registration": lambda root: edit_json(
        root, ".claude-plugin/marketplace.json",
        lambda value: value["plugins"][0].update(source="./elsewhere")),
    "distribution_packaging": lambda root: edit_json(
        root, ".agents/plugins/marketplace.json", lambda value: value.update(name="other")),
    "single_team_contract": lambda root: edit_json(
        root, "platforms/claude/software-engineering-team/manifest.json",
        lambda value: value.update(dependencies=["some-team"])),
    "packaged_state_files": lambda root: write(root, f"{PLUGIN_ROOT}/cache.sqlite", "state"),
    "json_hygiene": lambda root: write(
        root, f"{PLUGIN_ROOT}/skill-content/configure/data/probe.json", '{"camelCase": 1}\n'),
    "orchestrator_integrity": lambda root: edit(
        root, FLOW, "{{constitution}}", "the constitution"),
    "choice_gate": lambda root: append(root, FLOW, "\n\n\n\nThis needs an explicit user choice.\n"),
    "stdlib_only": lambda root: write(
        root, f"{PLUGIN_ROOT}/scripts/probe_import.py", "import requests\n"),
    "naive_clock": lambda root: write(
        root, f"{PLUGIN_ROOT}/scripts/probe_clock.py",
        "from datetime import date\n\nTODAY = date.today()\n"),
    "script_references": lambda root: append(root, FLOW, "\nRun scripts/ghost_tool.py first.\n"),
    "template_placeholders": lambda root: append(
        root, f"{PLUGIN_ROOT}/templates/task-input-contract.md", "\n{{ghost_token}}\n"),
    "project_instruction_contract": lambda root: remove(
        root, f"{PLUGIN_ROOT}/templates/memory/profile.md"),
    "spawn_shape_constitution": lambda root: append(
        root, f"{PLUGIN_ROOT}/skill-content/challenge-review/references/triage.md",
        "\n## Spawn Shape\n\nSpawn the reviewer with the named files.\n"),
    "ba_schema_shape": lambda root: edit_json(
        root, f"{PLUGIN_ROOT}/skill-content/business-analysis/data/space-schema.json",
        lambda value: value.update(schema_version="one")),
    "wikilink_ban": lambda root: append(root, FLOW, "\nSee [[ghost]].\n"),
    "version_sync": lambda root: edit_json(
        root, "versions.json",
        lambda value: value["plugins"].update({fixtures.PLUGIN: "9.9.9"})),
    "vault_policy_shape": lambda root: edit_json(
        root, f"{PLUGIN_ROOT}/skill-content/obsidian-vault/data/vault-policy.json",
        lambda value: value.update(schema_version="one")),
    "vault_wiring": lambda root: edit(root, FLOW, "`obsidian-vault` skill", "vault skill"),
    "model_config_shape": lambda root: edit_json(
        root, "tools/data/models.json", lambda value: value["reasoning_levels"].remove("low")),
    "execution_profiles": lambda root: edit_json(
        root, "platforms/codex/execution-profiles.json",
        lambda value: value["profiles"].update(fast={})),
    "review_panels": lambda root: edit_json(
        root, PANELS, lambda value: value["review_steps"]["design_system"].update(lenses=[])),
    "process_switches": lambda root: edit_json(
        root, SWITCHES, lambda value: value["switches"]["review_panels"].update(default="ghost")),
    "story_size_measures": lambda root: edit_json(
        root, MEASURES,
        lambda value: value["measures"]["acceptance_criteria"].update(derivation="ghost_count")),
    "fact_ownership": lambda root: edit_json(
        root, f"{PLUGIN_ROOT}/skill-content/execution-plan/data/fact-ownership.json",
        lambda value: value["fact_classes"]["runtime_topology"].pop("owner")),
    "fact_ownership_anchors": lambda root: edit_json(
        root, f"{PLUGIN_ROOT}/skill-content/execution-plan/data/fact-ownership.json",
        lambda value: value["fact_classes"]["structural_decisions"]["owner"].update(
            section="Decision")),
    "owner_decision_classes": lambda root: edit_json(
        root, f"{PLUGIN_ROOT}/skill-content/deliver/data/owner-decision-classes.json",
        lambda value: value["classes"].append(dict(value["classes"][0]))),
    "autopilot_policy": lambda root: edit_json(
        root, f"{PLUGIN_ROOT}/skill-content/autopilot/data/autopilot-policy.json",
        lambda value: value["goal_kinds"].append(dict(value["goal_kinds"][0]))),
    "limits_config_shape": lambda root: edit_json(
        root, "tools/data/limits.json",
        lambda value: value["authoring_caps"].update(ghost_cap=1)),
    "delivery_contract_shape": lambda root: remove(
        root, f"{PLUGIN_ROOT}/skill-content/deliver/data/delivery-receipt-contract.json"),
    "product_namespace": lambda root: append(
        root, f"{PLUGIN_ROOT}/scripts/marketplace_paths.py", "# drift\n"),
    "task_input_catalog": lambda root: edit_json(
        root, f"{PLUGIN_ROOT}/templates/task-input-policy.json",
        lambda value: value["role_skills"].pop("ux_designer")),
}


class ValidatorBuilderTests(unittest.TestCase):
    """Every validator check fires on its broken fixture and stays silent on
    the valid one; CHECKS and VALIDATOR_BUILDERS name the same checks."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "valid"
        fixtures.make_valid_root(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_registry_lockstep_with_checks(self):
        self.assertEqual(sorted(VALIDATOR_BUILDERS), sorted(validate.CHECKS))

    def test_contributing_and_the_validator_name_the_registry(self):
        contributing = (TESTS.parents[1] / "CONTRIBUTING.md").read_text(encoding="utf-8")
        for name, text in (("CONTRIBUTING.md", contributing), ("validate.py", validate.__doc__)):
            with self.subTest(document=name):
                text = " ".join(text.split())
                self.assertTrue("VALIDATOR_BUILDERS" in text
                                and "tools/tests/test_validator_contract.py" in text,
                                f"{name} does not name the builder registry")
                self.assertFalse("tools/tests/fixtures/" in text,
                                 f"{name} names a fixtures folder that does not exist")

    def test_each_builder_fires_its_check(self):
        pristine = tree_digest(self.root)
        for check, builder in sorted(VALIDATOR_BUILDERS.items()):
            with self.subTest(check=check):
                silent: list = []
                validate.CHECKS[check](validate.build_tree(self.root), silent)
                self.assertEqual(silent, [])
                found: list = []
                try:
                    builder(self.root)
                    validate.CHECKS[check](validate.build_tree(self.root), found)
                finally:
                    restore()
                self.assertEqual(tree_digest(self.root), pristine,
                                 f"the {check} builder changed a file outside the helpers")
                self.assertTrue([finding for finding in found if finding.check == check],
                                f"{check} reported nothing on its broken fixture")


if __name__ == "__main__":
    unittest.main()
