import json
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
COMPILER = ROOT / "plugins/software-engineering-team/scripts/experience_compile.py"
SETUP = ROOT / "plugins/software-engineering-team/scripts/setup_project.py"
VAULT_CHECK = ROOT / "plugins/software-engineering-team/scripts/vault_check.py"
sys.path.insert(0, str(COMPILER.parent))
import experience_compile


class ExperienceCompilerTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(COMPILER), *map(str, args)],
                              cwd=ROOT, text=True, capture_output=True, check=False)

    def test_legacy_program_commands_are_rejected(self):
        result = self.run_cli("init-program", "--root", "/tmp/x", "--program", "PRG-001")
        self.assertNotEqual(result.returncode, 0)

    def test_removed_process_local_artifact_command_is_rejected(self):
        result = self.run_cli(
            "init-artifact", "--experience-root", "/tmp/checkout"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid choice", result.stderr)

    def test_child_status_is_rejected_by_living_package_check(self):
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "workspace/docs/experience-design/experiences/checkout"
            package.mkdir(parents=True)
            (package / "experience.md").write_text(
                "---\ntype: experience\nexperience_id: checkout\norigin_mode: manual\nstatus: draft\nrevision: 1\nprimary_process_ref: marketplace:PRC-001\ninput_bindings:\n---\n# Checkout\n",
                encoding="utf-8",
            )
            child = package / "journeys/checkout-journey.md"
            child.parent.mkdir()
            child.write_text(
                "---\ntype: journey\nid: JRN-001\nrevision: 1\nstatus: approved\nrecord_state: active\nderives_from:\n  - marketplace:PRC-001\n---\n# Checkout\n",
                encoding="utf-8",
            )
            result = self.run_cli("check", "--experience-root", package, "--json")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("child records cannot carry approval state", result.stdout)

    def test_active_state_requires_canonical_state_class(self):
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "workspace/docs/experience-design/experiences/checkout"
            package.mkdir(parents=True)
            (package / "experience.md").write_text(
                "---\ntype: experience\nexperience_id: checkout\norigin_mode: manual\n"
                "status: draft\nrevision: 1\nprimary_process_ref: marketplace:PRC-001\n"
                "input_bindings:\n---\n# Checkout\n\n"
                "## Navigation <!-- sec: nav -->\n\n"
                "[[maps/experience-design|Experience Design]]\n",
                encoding="utf-8",
            )
            missing = self.run_cli(
                "stub", "--experience-root", package, "--kind", "state",
                "--id", "STA-001", "--slug", "ready",
            )
            self.assertEqual(missing.returncode, 2)
            self.assertIn("requires --state-class", missing.stderr)

            created = self.run_cli(
                "stub", "--experience-root", package, "--kind", "state",
                "--id", "STA-001", "--slug", "ready",
                "--state-class", "ordinary",
            )
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            state = package / "states/ready-state.md"
            state.write_text(
                state.read_text(encoding="utf-8").replace(
                    "state_class: ordinary\n", "", 1,
                ),
                encoding="utf-8",
            )
            checked = self.run_cli(
                "check", "--experience-root", package, "--json",
            )
            self.assertNotEqual(checked.returncode, 0)
            self.assertIn("active state needs a canonical state_class", checked.stdout)

    def test_transaction_rollback_restores_root_map_and_home(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            root = docs / "experience-design"
            root.mkdir(parents=True)
            map_path = docs / "maps/experience-design.md"
            map_path.parent.mkdir()
            map_path.write_text("map before\n", encoding="utf-8")
            home = docs / "home.md"
            home.write_text("home before\n", encoding="utf-8")
            marker = root / "marker.txt"
            marker.write_text("root before\n", encoding="utf-8")

            transaction_id = experience_compile.begin_transaction(
                root, "render",
            )
            marker.write_text("root after\n", encoding="utf-8")
            map_path.write_text("map after\n", encoding="utf-8")
            home.write_text("home after\n", encoding="utf-8")
            experience_compile.rollback_transaction(root, transaction_id)

            self.assertEqual(marker.read_text(encoding="utf-8"), "root before\n")
            self.assertEqual(map_path.read_text(encoding="utf-8"), "map before\n")
            self.assertEqual(home.read_text(encoding="utf-8"), "home before\n")

    def test_legacy_transaction_journal_remains_readable(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            journal = experience_compile.transaction_journal(root)
            journal.parent.mkdir(parents=True)
            journal.write_text(json.dumps({
                "schema_version": 1,
                "transaction_id": "1" * 32,
                "command": "render",
                "root": str(root),
                "root_existed": True,
                "map_path": str(experience_compile.transaction_map(root)),
                "map_existed": False,
                "phase": "prepared",
            }), encoding="utf-8")
            value = experience_compile.read_transaction_journal(root)
            self.assertIsNotNone(value)
            self.assertEqual(value["schema_version"], 1)

    def test_requirement_reference_round_trips_as_a_vault_link(self):
        with tempfile.TemporaryDirectory() as raw:
            docs = Path(raw) / "workspace/docs"
            requirement = docs / "requirements/req-001-checkout.md"
            requirement.parent.mkdir(parents=True)
            requirement.write_text(
                "---\ntype: requirement\nid: REQ-001\n---\n# Checkout\n",
                encoding="utf-8",
            )
            link = experience_compile.requirement_reference_link(
                docs, "REQ-001",
            )
            self.assertEqual(
                link, "[[requirements/req-001-checkout|REQ-001]]",
            )
            self.assertEqual(
                experience_compile.requirement_reference_value(link),
                "REQ-001",
            )

    def test_all_active_stubs_pass_scoped_vault_and_compiler_contracts(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            repository = subprocess.run(
                ["git", "init", "-q", str(project)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(
                repository.returncode, 0,
                repository.stdout + repository.stderr,
            )
            setup = subprocess.run(
                [sys.executable, str(SETUP), "apply", "--project-root",
                 str(project), "--json"],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)

            docs = project / "workspace/docs"
            root = docs / "experience-design"
            package = root / "experiences/checkout"
            process = "business-analysis/commerce/processes/checkout-process"
            process_note = (docs / process).with_suffix(".md")
            process_note.parent.mkdir(parents=True)
            process_note.write_text(
                "---\ntype: process\ntitle: Checkout process\n"
                "tags:\n  - doc/process\n---\n\n# Checkout process\n",
                encoding="utf-8",
            )
            design_note = docs / "design-system/MASTER.md"
            design_note.parent.mkdir(parents=True, exist_ok=True)
            design_note.write_text(
                "---\ntype: design-master\ntitle: Product design\n"
                "tags:\n  - doc/design-master\n---\n\n# Product design\n",
                encoding="utf-8",
            )
            template_root = (
                ROOT / "plugins/software-engineering-team/templates/vault"
            )
            for subtree, target, title in (
                ("business-analysis", process, "Checkout process"),
                ("design-system", "design-system/MASTER", "Product design"),
            ):
                map_path = docs / "maps" / f"{subtree}.md"
                map_path.parent.mkdir(parents=True, exist_ok=True)
                map_path.write_text(
                    (template_root / "maps" / f"{subtree}.md").read_text(
                        encoding="utf-8"
                    ).rstrip()
                    + f"\n\n- [[{target}|{title}]]\n",
                    encoding="utf-8",
                )
            home = docs / "home.md"
            home.write_text(
                home.read_text(encoding="utf-8").rstrip()
                + "\n\n- [[maps/business-analysis|Business Analysis]]"
                + "\n- [[maps/design-system|Design System]]\n",
                encoding="utf-8",
            )
            receipts = [
                {
                    "stage": stage,
                    "result_ref": reference,
                    "package_hash": "sha256:" + character * 64,
                }
                for stage, reference, character in (
                    ("business-analysis", "business-analysis/commerce/space", "a"),
                    ("solution-design", "solution-design/landscape", "b"),
                    ("design-system", "design-system/MASTER", "c"),
                )
            ]
            action = {
                "action": "create",
                "source_experience": "checkout",
                "target_experience": "checkout",
                "proposal_hash": "sha256:" + "1" * 64,
            }
            plan = {"origin_mode": "manual", "actions": [action]}
            init_args = [
                "init", "--root", str(root), "--experience", "checkout",
                "--origin-mode", "manual", "--primary-process-ref", process,
                "--scope-plan", str(project / "scope-plan.json"),
                "--proposal-hash", action["proposal_hash"],
                "--title", "Checkout Experience",
                "--ba-ref", "business-analysis/commerce/space",
                "--solution-ref", "solution-design/landscape",
                "--design-ref", "design-system/MASTER",
            ]
            output, errors = io.StringIO(), io.StringIO()
            with mock.patch.object(
                experience_compile, "selected_inputs",
                return_value=(receipts, [], {}),
            ), mock.patch.object(
                experience_compile, "load_scope_plan", return_value=plan,
            ), mock.patch.object(
                experience_compile, "verify_scope_inputs", return_value=[],
            ), mock.patch.object(
                experience_compile, "process_from_inputs",
                return_value=(process, []),
            ), mock.patch.object(
                experience_compile, "action_for_plan", return_value=action,
            ), mock.patch.object(
                experience_compile, "open_application",
            ), mock.patch.object(
                experience_compile, "write_open_revision",
            ), redirect_stdout(output), redirect_stderr(errors):
                initialized = experience_compile.main(init_args)
            self.assertEqual(initialized, 0, output.getvalue() + errors.getvalue())

            cases = (
                ("journey", "JRN-001", "purchase", "Purchase Journey", []),
                ("flow-set", "FLW-001", "checkout", "Checkout Flow", []),
                (
                    "screen", "SCR-001", "payment", "Payment Screen",
                    ["--uses-design", "design-system/MASTER"],
                ),
                (
                    "state", "STA-001", "ready", "Ready State",
                    ["--state-class", "ordinary"],
                ),
                (
                    "transition", "TRN-001", "submit", "Submit Transition",
                    ["--related-to", "checkout:STA-001@r1"],
                ),
            )
            for kind, identifier, slug, title, extra in cases:
                with self.subTest(kind=kind):
                    created = self.run_cli(
                        "stub", "--experience-root", package, "--kind", kind,
                        "--id", identifier, "--slug", slug, "--title", title,
                        *extra,
                    )
                    self.assertEqual(
                        created.returncode, 0,
                        created.stdout + created.stderr,
                    )
                    directory, _prefix, suffix = experience_compile.KIND[kind]
                    note = package / directory / f"{slug}{suffix}"
                    impacted = subprocess.run(
                        [sys.executable, str(VAULT_CHECK), "check", "--vault",
                         str(docs), "--impact",
                         note.relative_to(docs).as_posix(), "--json"],
                        cwd=ROOT, text=True, capture_output=True, check=False,
                    )
                    self.assertEqual(
                        impacted.returncode, 0,
                        impacted.stdout + impacted.stderr,
                    )

            state = package / "states/ready-state.md"
            self.assertEqual(
                experience_compile.fm(state)[0]["state_class"], "ordinary",
            )
            relations = subprocess.run(
                [sys.executable, str(VAULT_CHECK), "render-relations",
                 "--vault", str(docs)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(
                relations.returncode, 0,
                relations.stdout + relations.stderr,
            )
            vault = subprocess.run(
                [sys.executable, str(VAULT_CHECK), "check", "--vault",
                 str(docs), "--scope", "experience-design", "--json"],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(vault.returncode, 0, vault.stdout + vault.stderr)

            output, errors = io.StringIO(), io.StringIO()
            with mock.patch.object(
                experience_compile.stage_package, "verify",
                return_value=({}, []),
            ), mock.patch.object(
                experience_compile.stage_package, "resolve_ba_process",
                return_value=(process, []),
            ), redirect_stdout(output), redirect_stderr(errors):
                checked = experience_compile.main([
                    "check", "--experience-root", str(package), "--json",
                ])
            self.assertEqual(checked, 0, output.getvalue() + errors.getvalue())

    def test_exact_lifecycle_json_rejects_boolean_integer_aliases(self):
        hashes = {
            key: "sha256:" + "1" * 64
            for key in (
                "artifact_tree_hash", "package_set_hash", "application_hash",
            )
        }
        preimage = {
            "exists": True, "status": "approved", "revision": True,
            **hashes,
        }
        self.assertFalse(experience_compile.exact_application_preimage(preimage))

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "experience-design"
            state = root / "_generated/open-application-revision.json"
            state.parent.mkdir(parents=True)
            state.write_text(json.dumps({
                "schema_version": True,
                "proposal_hash": "sha256:" + "2" * 64,
                "application_action": "update",
                "package_actions_hash": "sha256:" + "3" * 64,
                "expected_application": {**preimage, "revision": 1},
                "opened_revision": 2,
                "phase": "draft",
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid exact schema"):
                experience_compile.read_open_application_state(root)


if __name__ == "__main__":
    unittest.main()
