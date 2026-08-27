from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "plugins" / "software-engineering-team" / "scripts" / "setup_project.py"
GATE_INSTALLER = (
    ROOT / "plugins/software-engineering-team/scripts/vault_gate.py"
)


class PortableVaultGateTests(unittest.TestCase):
    def setup_project(self, root: Path) -> Path:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        result = subprocess.run(
            [sys.executable, str(SETUP), "--project-root", str(root)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        gate = root / ".github" / "agentrof" / "vault-gate.pyz"
        self.assertTrue(gate.is_file())
        return gate

    def run_gate(self, gate: Path, project: Path) -> dict:
        result = subprocess.run(
            [sys.executable, str(gate), "check", "--project-root",
             str(project), "--json"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_archive_contains_design_and_backlog_compilers(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            gate = self.setup_project(project)
            with zipfile.ZipFile(gate) as archive:
                names = set(archive.namelist())
            self.assertIn("scripts/design_system_compile.py", names)
            self.assertIn("scripts/backlog_compile.py", names)
            self.assertIn("scripts/vault_check.py", names)
            self.assertIn("scripts/experience_application_check.py", names)
            self.assertIn(
                "skill-content/experience-modeling/data/application-template.html",
                names,
            )
            self.assertIn(
                "skill-content/experience-modeling/data/application-map-schema.json",
                names,
            )

    def test_clean_clone_may_omit_ignored_local_obsidian_projection(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            gate = self.setup_project(project)
            obsidian = project / "workspace/docs/.obsidian"
            (obsidian / "community-plugins.json").unlink()
            shutil.rmtree(obsidian / "plugins")

            local_check = subprocess.run(
                [sys.executable, str(SETUP), "check", "--project-root",
                 str(project), "--json"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(local_check.returncode, 1)
            self.assertIn("local", local_check.stdout.lower())

            portable = subprocess.run(
                [sys.executable, str(gate), "check", "--project-root",
                 str(project), "--json"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(
                portable.returncode, 0, portable.stdout + portable.stderr
            )

    def test_present_malformed_design_system_is_checked(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            gate = self.setup_project(project)
            master = project / "workspace" / "docs" / "design-system" / "MASTER.md"
            master.write_text(
                "---\n"
                "type: design_master\n"
                "title: Design system\n"
                "status: draft\n"
                "revision: 0\n"
                "tags:\n  - doc/design-master\n"
                "aliases:\n  - Design System\n"
                "---\n# Design system\n",
                encoding="utf-8",
            )
            payload = self.run_gate(gate, project)
            result = next(item for item in payload["results"]
                          if item["name"] == "design-system")
            self.assertFalse(result["ok"])
            self.assertIn("positive integer", result["stdout"] + result["stderr"])

    def test_noncanonical_second_workspace_fails_the_portable_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            gate = self.setup_project(project)
            alternate = project / "alternate"
            alternate.mkdir()
            (alternate / "config.json").write_text(json.dumps({
                "team_id": "software-engineering-team",
            }) + "\n", encoding="utf-8")
            payload = self.run_gate(gate, project)
            result = next(item for item in payload["results"]
                          if item["name"] == "workspace-contract")
            self.assertFalse(result["ok"])
            self.assertIn("non-canonical managed workspace", result["stdout"])

    def test_present_approved_malformed_backlog_uses_approved_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            gate = self.setup_project(project)
            backlog = project / "workspace" / "docs" / "backlog" / "backlog.md"
            backlog.write_text(
                "---\n"
                "type: backlog\n"
                "title: Product backlog\n"
                "status: approved\n"
                "owner_role: product_owner\n"
                "revision: 1\n"
                "tags:\n  - doc/backlog\n  - status/approved\n"
                "aliases:\n  - BACKLOG\n"
                "---\n# Product backlog\n",
                encoding="utf-8",
            )
            payload = self.run_gate(gate, project)
            result = next(item for item in payload["results"]
                          if item["name"] == "backlog:approved")
            self.assertFalse(result["ok"])
            self.assertIn("at least one epic", result["stdout"] + result["stderr"])

    def test_application_without_any_package_still_runs_global_checker(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            gate = self.setup_project(project)
            application = (
                project / "workspace/docs/experience-design/artifacts/"
                "application.html"
            )
            application.parent.mkdir(parents=True, exist_ok=True)
            application.write_text(
                "<!doctype html><html><head>"
                '<meta name="experience-application-status" content="draft">'
                "</head><body></body></html>\n",
                encoding="utf-8",
            )
            payload = self.run_gate(gate, project)
            result = next(item for item in payload["results"]
                          if item["name"] == "experience-application")
            self.assertFalse(result["ok"])
            self.assertIn("application", (
                result["stdout"] + result["stderr"]
            ).lower())

    def test_portable_gate_rejects_hardlinked_experience_machine_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            gate = self.setup_project(project)
            ledger = (
                project / "workspace/docs/experience-design/_ledger/"
                "application-revisions.json"
            )
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.write_text(
                '{"schema_version":2,"revisions":[]}\n', encoding="utf-8",
            )
            alias = project / "ledger-alias.json"
            try:
                os.link(ledger, alias)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"hard links are unavailable: {exc}")

            payload = self.run_gate(gate, project)
            result = next(
                item for item in payload["results"]
                if item["name"] == "experience-path-identity"
            )
            self.assertFalse(result["ok"])
            self.assertIn("more than one filesystem link", result["stdout"])

    def test_portable_gate_routes_retired_package_through_historical_integrity(self):
        from tools.tests.experience_fixture import write_empty_application
        from tools.tests.test_living_experience_flow import (
            LivingExperienceFlowTests,
        )

        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            config = project / "workspace/config.json"
            config.parent.mkdir(parents=True)
            config.write_text(json.dumps({
                "schema_version": 2,
                "team_id": "software-engineering-team",
                "output_language": "English",
                "terminology_language": "English",
            }, indent=2) + "\n", encoding="utf-8")
            root = project / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            fixture = LivingExperienceFlowTests(methodName="runTest")
            fixture.prepare_inputs(root.parent)
            package, _receipts = fixture.approve_single(root)
            fixture.commit_docs(root, "approve before retirement")
            plan, proposal_hash = fixture.propose_manual(
                root, experience="checkout", action="retire",
            )
            fixture.run_cli(
                "retire", "--experience-root", package,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            write_empty_application(root)
            fixture.run_cli(
                "enter-application-review", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            fixture.approve_experience_set(
                root, plan, proposal_hash, "checkout",
            )
            fixture.commit_docs(root, "retire final process")
            installed = subprocess.run(
                [sys.executable, str(GATE_INSTALLER), "install",
                 "--project-root", str(project)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(installed.returncode, 0,
                             installed.stdout + installed.stderr)
            gate = project / ".github/agentrof/vault-gate.pyz"
            checked = subprocess.run(
                [sys.executable, str(gate), "check", "--project-root",
                 str(project), "--json"],
                capture_output=True, text=True, check=False,
            )
            self.assertIn(checked.returncode, (0, 1),
                          checked.stdout + checked.stderr)
            payload = json.loads(checked.stdout)
            self.assertIsNotNone(next(
                item for item in payload["results"]
                if item["name"] == "experience-application"
            ), checked.stdout + checked.stderr)
            self.assertTrue(next(
                item for item in payload["results"]
                if item["name"] == "experience:checkout"
            )["ok"], checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
