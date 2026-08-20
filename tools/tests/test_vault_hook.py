from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
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
    @staticmethod
    def draft_backlog(docs: Path) -> Path:
        """A deliberately incomplete modern backlog for hook-only tests."""
        maps = docs / "maps"
        maps.mkdir(parents=True, exist_ok=True)
        (maps / "backlog.md").write_text(
            "---\ntype: moc\ntitle: Backlog map\ntags:\n  - doc/moc\n"
            "---\n\n# Backlog map\n", encoding="utf-8",
        )
        path = docs / "backlog" / "backlog.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\ntype: backlog\ntitle: Product backlog\nstatus: draft\n"
            "planning_mode: manual\nowner_role: product_owner\nrevision: 1\n"
            "tags:\n  - doc/backlog\n  - status/draft\naliases:\n  - BACKLOG\n"
            "---\n\n# Product backlog\n\n## Navigation <!-- sec: nav -->\n\n"
            "[[maps/backlog|Backlog map]]\n", encoding="utf-8",
        )
        return path

    def setup_project(self, root: Path) -> Path:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        result = subprocess.run(
            [sys.executable, str(SETUP), "--project-root", str(root)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return root / "workspace" / "docs"

    @staticmethod
    def setup_config_project(root: Path) -> Path:
        (root / "workspace" / "docs").mkdir(parents=True)
        config = root / "workspace" / "config.json"
        config.write_text(json.dumps({
            "schema_version": 2,
            "team_id": "software-engineering-team",
            "output_language": "English",
            "terminology_language": "English",
        }, indent=2) + "\n", encoding="utf-8")
        return config

    def run_hook(self, mode: str, payload: dict) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(SCRIPTS)
        return subprocess.run(
            [sys.executable, str(HOOK), mode], input=json.dumps(payload),
            capture_output=True, text=True, check=False, env=environment,
        )

    def run_bash_cycle(self, project: Path, command: str,
                       mutate) -> subprocess.CompletedProcess[str]:
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": command},
            "cwd": str(project),
            "session_id": f"vault-hook-test-{id(mutate)}",
        }
        before = self.run_hook("pre", payload)
        self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
        mutate()
        return self.run_hook("post", payload)

    @staticmethod
    def recovery_capsule(project: Path, payload: dict) -> Path:
        event = str(payload.get("tool_use_id") or "")
        if not event:
            command = str(payload.get("tool_input", {}).get("command") or "")
            event = hashlib.sha256(command.encode("utf-8")).hexdigest()
        binding = hashlib.sha256(json.dumps({
            "project": str(project.resolve()),
            "session_id": str(payload.get("session_id") or "unknown"),
            "event": event,
        }, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode("utf-8")).hexdigest()
        user = getattr(os, "getuid", lambda: 0)()
        return (Path(tempfile.gettempdir())
                / f"agentrof-vault-hook-{user}" / f"{binding}.json")

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
            self.draft_backlog(docs)
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
            backlog = self.draft_backlog(docs)
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

    def test_closed_config_rejects_direct_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            self.setup_project(project)
            config = project / "workspace" / "config.json"
            current = json.loads(config.read_text(encoding="utf-8"))
            proposed = dict(current)
            proposed["output_language"] = "Turkish"
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

    def test_bash_cannot_bypass_machine_managed_config_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            config = self.setup_config_project(project)
            original = config.read_bytes()

            def mutate():
                value = json.loads(config.read_text(encoding="utf-8"))
                value["output_language"] = "Turkish"
                config.write_text(json.dumps(value, indent=2) + "\n",
                                  encoding="utf-8")

            denied = self.run_bash_cycle(
                project, "python3 -c 'rewrite config directly'", mutate
            )
            self.assertEqual(denied.returncode, 2)
            self.assertIn("original config was restored", denied.stderr)
            self.assertEqual(config.read_bytes(), original)

    def test_bash_guard_hash_catches_unknown_config_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            config = self.setup_config_project(project)
            original = config.read_bytes()

            def mutate():
                value = json.loads(config.read_text(encoding="utf-8"))
                value["unexpected"] = "Work Item"
                config.write_text(json.dumps(value, indent=2) + "\n",
                                  encoding="utf-8")

            denied = self.run_bash_cycle(project, "python3 mutate.py", mutate)
            self.assertEqual(denied.returncode, 2)
            self.assertEqual(config.read_bytes(), original)

    def test_bash_restores_config_after_project_runtime_is_deleted(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            config = self.setup_config_project(project)
            original = config.read_bytes()
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": "python3 mutate_and_cleanup.py"},
                "cwd": str(project),
                "session_id": "runtime-deletion",
                "tool_use_id": "runtime-deletion-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0,
                             before.stdout + before.stderr)
            capsule = self.recovery_capsule(project, payload)
            self.assertTrue(capsule.is_file())

            value = json.loads(config.read_text(encoding="utf-8"))
            value["output_language"] = "Turkish"
            config.write_text(json.dumps(value, indent=2) + "\n",
                              encoding="utf-8")
            shutil.rmtree(
                project / ".agentrof" / "agent-marketplace" / ".runtime"
            )

            denied = self.run_hook("post", payload)
            self.assertEqual(denied.returncode, 2)
            self.assertIn("original config was restored", denied.stderr)
            self.assertIn("snapshot is missing", denied.stderr)
            self.assertEqual(config.read_bytes(), original)
            self.assertFalse(capsule.exists())

    def test_bash_restores_config_after_inventory_snapshot_is_tampered(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            config = self.setup_config_project(project)
            original = config.read_bytes()
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": "python3 mutate_snapshot.py"},
                "cwd": str(project),
                "session_id": "snapshot-tamper",
                "tool_use_id": "snapshot-tamper-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0,
                             before.stdout + before.stderr)
            snapshots = list((
                project / ".agentrof" / "agent-marketplace" / ".runtime"
                / "vault-inventory"
            ).glob("*.json"))
            self.assertEqual(len(snapshots), 1)
            snapshots[0].write_text('{"tampered":true}', encoding="utf-8")
            value = json.loads(config.read_text(encoding="utf-8"))
            value["output_language"] = "Turkish"
            config.write_text(json.dumps(value, indent=2) + "\n",
                              encoding="utf-8")

            denied = self.run_hook("post", payload)
            self.assertEqual(denied.returncode, 2)
            self.assertIn("original config was restored", denied.stderr)
            self.assertIn("snapshot was tampered", denied.stderr)
            self.assertEqual(config.read_bytes(), original)
            self.assertFalse(self.recovery_capsule(project, payload).exists())

    def test_duplicate_bash_pre_event_cannot_overwrite_recovery_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            self.setup_config_project(project)
            payload = {
                "tool_name": "Bash",
                "tool_input": {"command": "python3 same_event.py"},
                "cwd": str(project),
                "session_id": "same-session",
                "tool_use_id": "same-tool-event",
            }
            first = self.run_hook("pre", payload)
            self.assertEqual(first.returncode, 0, first.stderr)
            capsule = self.recovery_capsule(project, payload)
            original_capsule = capsule.read_bytes()

            duplicate = self.run_hook("pre", payload)
            self.assertEqual(duplicate.returncode, 2)
            self.assertIn("already owns this session/event", duplicate.stderr)
            self.assertEqual(capsule.read_bytes(), original_capsule)

            post = self.run_hook("post", payload)
            self.assertEqual(post.returncode, 0, post.stdout + post.stderr)
            self.assertFalse(capsule.exists())

    def test_exact_project_config_writer_is_allowed_through_bash(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            config = self.setup_config_project(project)
            argv = [
                sys.executable, str(SCRIPTS / "project_config.py"), "set",
                "--config", str(config), "--field", "output_language",
                "--value", "Turkish",
            ]

            def mutate():
                result = subprocess.run(
                    argv, cwd=project, capture_output=True, text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0,
                                 result.stdout + result.stderr)

            allowed = self.run_bash_cycle(
                project,
                "PYTHONDONTWRITEBYTECODE=1 " + shlex.join(argv),
                mutate,
            )
            self.assertEqual(allowed.returncode, 0,
                             allowed.stdout + allowed.stderr)
            value = json.loads(config.read_text(encoding="utf-8"))
            self.assertEqual(value["output_language"], "Turkish")

    def test_chained_command_cannot_impersonate_config_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            config = self.setup_config_project(project)
            original = config.read_bytes()
            sanctioned = shlex.join([
                sys.executable, str(SCRIPTS / "project_config.py"), "set",
                "--config", str(config), "--field", "output_language",
                "--value", "Turkish",
            ])

            def mutate():
                value = json.loads(config.read_text(encoding="utf-8"))
                value["output_language"] = "Turkish"
                config.write_text(json.dumps(value, indent=2) + "\n",
                                  encoding="utf-8")

            denied = self.run_bash_cycle(
                project, sanctioned + " && python3 mutate.py", mutate
            )
            self.assertEqual(denied.returncode, 2)
            self.assertEqual(config.read_bytes(), original)

    def test_environment_injection_cannot_impersonate_config_writer(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            config = self.setup_config_project(project)
            original = config.read_bytes()
            sanctioned = shlex.join([
                sys.executable, str(SCRIPTS / "project_config.py"), "set",
                "--config", str(config), "--field", "output_language",
                "--value", "Turkish",
            ])

            def mutate():
                value = json.loads(config.read_text(encoding="utf-8"))
                value["output_language"] = "Turkish"
                config.write_text(json.dumps(value, indent=2) + "\n",
                                  encoding="utf-8")

            denied = self.run_bash_cycle(
                project, "PYTHONPATH=/tmp/evil " + sanctioned, mutate
            )
            self.assertEqual(denied.returncode, 2)
            self.assertIn("original config was restored", denied.stderr)
            self.assertEqual(config.read_bytes(), original)

    def test_spoofed_writer_basename_is_not_sanctioned(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            config = self.setup_config_project(project)
            original = config.read_bytes()
            spoof = project / "project_config.py"
            command = shlex.join([
                sys.executable, str(spoof), "set", "--config", str(config),
                "--field", "output_language", "--value", "Turkish",
            ])

            def mutate():
                value = json.loads(config.read_text(encoding="utf-8"))
                value["output_language"] = "Turkish"
                config.write_text(json.dumps(value, indent=2) + "\n",
                                  encoding="utf-8")

            denied = self.run_bash_cycle(project, command, mutate)
            self.assertEqual(denied.returncode, 2)
            self.assertIn("original config was restored", denied.stderr)
            self.assertEqual(config.read_bytes(), original)

    def test_bash_cannot_add_unknown_config_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            config = self.setup_config_project(project)

            def mutate():
                value = json.loads(config.read_text(encoding="utf-8"))
                value["project_notes"] = "local"
                config.write_text(json.dumps(value, indent=2) + "\n",
                                  encoding="utf-8")

            denied = self.run_bash_cycle(project, "python3 mutate.py", mutate)
            self.assertEqual(denied.returncode, 2,
                             denied.stdout + denied.stderr)
            value = json.loads(config.read_text(encoding="utf-8"))
            self.assertNotIn("project_notes", value)

    def test_bash_ignores_policy_owned_obsidian_plugin_projection(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            self.setup_config_project(project)
            obsidian = project / "workspace" / "docs" / ".obsidian"
            plugin = obsidian / "plugins" / "retired-plugin"
            plugin.mkdir(parents=True)
            asset = plugin / "main.js"
            asset.write_text("retired", encoding="utf-8")
            enabled = obsidian / "community-plugins.json"
            enabled.write_text('["retired-plugin"]\n', encoding="utf-8")
            # If either ignored projection path entered the inventory, its
            # deletion would trigger a full check and surface this unrelated
            # pre-existing authored error.
            (project / "workspace" / "docs" / "unrelated.md").write_text(
                "# Unrelated\n\n[[missing-note|Missing]]\n", encoding="utf-8"
            )
            setup = shlex.join([
                sys.executable, str(SCRIPTS / "setup_project.py"),
                "--project-root", str(project),
            ])

            def mutate():
                asset.unlink()
                plugin.rmdir()
                enabled.unlink()

            allowed = self.run_bash_cycle(project, setup, mutate)
            self.assertEqual(allowed.returncode, 0,
                             allowed.stdout + allowed.stderr)

    def test_bash_still_checks_tracked_vault_deletions(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            self.setup_config_project(project)
            docs = project / "workspace" / "docs"
            trigger = docs / "tracked.md"
            trigger.write_text("# Tracked\n", encoding="utf-8")
            (docs / "unrelated.md").write_text(
                "# Unrelated\n\n[[missing-note|Missing]]\n", encoding="utf-8"
            )

            def mutate():
                trigger.unlink()

            denied = self.run_bash_cycle(project, "python3 cleanup.py", mutate)
            self.assertEqual(denied.returncode, 2)
            self.assertIn("Bash changed vault inventory", denied.stderr)

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

    def test_legacy_experience_release_path_is_rejected(self):
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
            self.assertIn("invalid Experience Design filename or path", result.stderr)

    def test_opaque_experience_artifact_bypasses_note_filename_and_relation_guards(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            docs = self.setup_project(project)
            artifact = docs / "experience-design/experiences/example/artifacts/arbitrary.md"
            result = self.run_hook("pre", {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(artifact),
                    "content": "opaque artifact, not frontmatter\n",
                },
            })
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_retired_experience_review_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            docs = self.setup_project(project)
            review = (
                docs / "experience-design/programs/prg-1/releases/rel-1"
                / "reviews/navigation-review.md"
            )
            result = self.run_hook("pre", {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(review),
                    "content": "# Retired review artifact\n",
                },
            })
            self.assertEqual(result.returncode, 2)
            self.assertIn("invalid Experience Design filename or path",
                          result.stderr)


if __name__ == "__main__":
    unittest.main()
