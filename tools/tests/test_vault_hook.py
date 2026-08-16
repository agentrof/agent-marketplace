from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins" / "software-engineering-team" / "scripts"
SETUP = SCRIPTS / "setup_project.py"
BACKLOG = SCRIPTS / "backlog_compile.py"
HOOK = ROOT / "platforms" / "shared" / "software-engineering-team" / "overlay" / "scripts" / "vault_hook.py"


class VaultHookTests(unittest.TestCase):
    def setup_project(self, root: Path) -> Path:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        result = subprocess.run(
            [sys.executable, str(SETUP), "--project-root", str(root)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return root / "workspace" / "docs"

    def run_hook(self, mode: str, payload: dict) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(SCRIPTS)
        return subprocess.run(
            [sys.executable, str(HOOK), mode], input=json.dumps(payload),
            capture_output=True, text=True, check=False, env=environment,
        )

    def test_generated_backlog_files_are_write_protected(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            docs = self.setup_project(project)
            generated = docs / "backlog" / "_generated" / "board.md"
            payload = {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(generated),
                    "content": "# Manual board\n",
                },
            }
            result = self.run_hook("pre", payload)
            self.assertEqual(result.returncode, 2)
            self.assertIn("compiler-owned", result.stderr)

    def test_post_write_does_not_require_a_complete_backlog_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            docs = self.setup_project(project)
            initialized = subprocess.run(
                [sys.executable, str(BACKLOG), "init", "--docs", str(docs)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(
                initialized.returncode, 0,
                initialized.stdout + initialized.stderr,
            )
            full = subprocess.run(
                [sys.executable, str(BACKLOG), "check", "--docs", str(docs)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(full.returncode, 1)
            backlog = docs / "backlog" / "backlog.md"
            payload = {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(backlog),
                    "old_string": "Product backlog",
                    "new_string": "Product backlog",
                },
            }
            result = self.run_hook("post", payload)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_only_approved_status_transitions_are_machine_owned(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            docs = self.setup_project(project)
            initialized = subprocess.run(
                [sys.executable, str(BACKLOG), "init", "--docs", str(docs)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(initialized.returncode, 0)
            backlog = docs / "backlog" / "backlog.md"
            changes_requested = {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(backlog),
                    "old_string": "status: draft",
                    "new_string": "status: changes_requested",
                },
            }
            allowed = self.run_hook("pre", changes_requested)
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            approved = {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": str(backlog),
                    "old_string": "status: draft",
                    "new_string": "status: approved",
                },
            }
            denied = self.run_hook("pre", approved)
            self.assertEqual(denied.returncode, 2)
            self.assertIn("machine-managed", denied.stderr)

    def test_team_owned_delivery_config_fields_require_the_config_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            self.setup_project(project)
            config = project / "workspace" / "config.json"
            current = json.loads(config.read_text(encoding="utf-8"))
            proposed = dict(current)
            proposed["test_command"] = "make test"
            payload = {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(config),
                    "content": json.dumps(proposed, indent=2) + "\n",
                },
            }
            denied = self.run_hook("pre", payload)
            self.assertEqual(denied.returncode, 2)
            self.assertIn("team-owned workspace config fields", denied.stderr)

    def test_unparseable_apply_patch_fails_closed(self):
        result = self.run_hook("pre", {
            "tool_name": "apply_patch",
            "tool_input": "not a patch",
        })
        self.assertEqual(result.returncode, 2)
        self.assertIn("could not be parsed safely", result.stderr)

    def test_multifile_apply_patch_cannot_hide_generated_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            docs = self.setup_project(project)
            generated = docs / "backlog/_generated/board.md"
            patch = (
                "*** Begin Patch\n"
                f"*** Add File: {project / 'README.md'}\n"
                "+safe\n"
                f"*** Add File: {generated}\n"
                "+manual board\n"
                "*** End Patch"
            )
            result = self.run_hook("pre", {
                "tool_name": "apply_patch", "tool_input": patch,
                "cwd": str(project),
            })
            self.assertEqual(result.returncode, 2)
            self.assertIn("compiler-owned", result.stderr)

    def test_apply_patch_outside_vault_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            patch = (
                "*** Begin Patch\n"
                f"*** Add File: {project / 'README.md'}\n"
                "+safe\n"
                "*** End Patch"
            )
            result = self.run_hook("pre", {
                "tool_name": "apply_patch", "tool_input": patch,
                "cwd": str(project),
            })
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_approved_design_system_is_immutable(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            docs = self.setup_project(project)
            master = docs / "design-system/MASTER.md"
            master.write_text(
                "---\ntype: design-master\ntitle: Product design master\n"
                "status: approved\ntags:\n  - doc/design-master\n"
                "  - status/approved\n---\n\n# Product design master\n",
                encoding="utf-8",
            )
            page = docs / "design-system/pages/account.md"
            result = self.run_hook("pre", {
                "tool_name": "Write",
                "tool_input": {"file_path": str(page), "content": "draft"},
            })
            self.assertEqual(result.returncode, 2)
            self.assertIn("approved Design System content is immutable", result.stderr)

    def test_approved_experience_release_is_immutable(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            docs = self.setup_project(project)
            release = docs / "experience-design/programs/prg-1/releases/rel-1"
            release.mkdir(parents=True)
            (release / "release.md").write_text(
                "---\ntype: release\ntitle: Release experience\nstatus: approved\n"
                "tags:\n  - doc/release\n  - status/approved\n---\n\n"
                "# Release experience\n",
                encoding="utf-8",
            )
            journey = release / "journeys/account-journey.md"
            result = self.run_hook("pre", {
                "tool_name": "Write",
                "tool_input": {"file_path": str(journey), "content": "draft"},
            })
            self.assertEqual(result.returncode, 2)
            self.assertIn("approved Experience Design", result.stderr)


if __name__ == "__main__":
    unittest.main()
