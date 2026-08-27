from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "plugins" / "software-engineering-team" / "scripts" / "setup_project.py"
CHECK = ROOT / "plugins" / "software-engineering-team" / "scripts" / "setup_check.py"
BACKLOG = ROOT / "plugins" / "software-engineering-team" / "scripts" / "backlog_compile.py"
REQUIREMENT_ROUTE = ROOT / "plugins" / "software-engineering-team" / "scripts" / "requirement_route.py"
SCRIPTS = SETUP.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import vault_check as vault_payload
import setup_project as setup_module
from unittest import mock


class SetupProjectTests(unittest.TestCase):
    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    def test_bootstrap_is_project_local_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            inspected = self.run_script(
                SETUP, "inspect", "--project-root", str(project), "--json"
            )
            self.assertEqual(
                inspected.returncode, 0, inspected.stdout + inspected.stderr
            )
            by_path = {
                item["path"]: item
                for item in json.loads(inspected.stdout)["operations"]
            }
            self.assertTrue(
                by_path["workspace/docs/.obsidian/app.json"]["changes"]
            )
            self.assertEqual(
                by_path[
                    "workspace/docs/.obsidian/community-plugins.json"
                ]["changes"][0]["key"],
                "$",
            )
            for relative in (
                "workspace/docs/home.md",
                "workspace/docs/.obsidian/snippets/brand.css",
                "workspace/docs/.obsidian/plugins/"
                "obsidian-front-matter-title-plugin/main.js",
            ):
                self.assertTrue(by_path[relative]["after_hash"].startswith(
                    "sha256:"
                ))
            first = self.run_script(SETUP, "--project-root", str(project), "--json")
            self.assertEqual(first.returncode, 0, first.stderr)
            payload = json.loads(first.stdout)
            self.assertEqual(payload["next_entry"], "requirement")
            runtime = project / ".agentrof" / "agent-marketplace" / ".runtime"
            self.assertEqual(Path(payload["runtime_root"]), runtime.resolve())
            self.assertTrue(runtime.is_dir())
            self.assertEqual(
                {path.name for path in runtime.iterdir()},
                {"setup-apply.guard"},
            )
            self.assertIn("/.agentrof/", (project / ".gitignore").read_text())
            ignored = subprocess.run(
                ["git", "check-ignore", "--no-index", "-q",
                 ".agentrof/agent-marketplace/.runtime/probe"],
                cwd=project, check=False,
            )
            self.assertEqual(ignored.returncode, 0)
            runtime_sentinel = runtime / "cache.txt"
            runtime_sentinel.write_text("disposable\n", encoding="utf-8")
            authored = project / "README.user.md"
            authored.write_text("user content\n", encoding="utf-8")
            second = self.run_script(SETUP, "--project-root", str(project), "--json")
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(authored.read_text(encoding="utf-8"), "user content\n")
            self.assertEqual(runtime_sentinel.read_text(encoding="utf-8"), "disposable\n")
            checked = self.run_script(CHECK, "check", "--project-root", str(project), "--json")
            self.assertEqual(checked.returncode, 0, checked.stderr)
            gate = project / ".github" / "agentrof" / "vault-gate.pyz"
            self.assertTrue(gate.is_file())
            portable = subprocess.run(
                [sys.executable, str(gate), "check", "--project-root", str(project), "--json"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(portable.returncode, 0, portable.stderr)
            before = self.run_script(
                REQUIREMENT_ROUTE, "--project-root", str(project), "--json"
            )
            self.assertEqual(before.returncode, 1, before.stderr)
            shutil.rmtree(project / ".agentrof")
            after = self.run_script(
                REQUIREMENT_ROUTE, "--project-root", str(project), "--json"
            )
            self.assertEqual(after.returncode, 1, after.stderr)
            self.assertEqual(json.loads(after.stdout), json.loads(before.stdout))

            config = json.loads(
                (project / "workspace/config.json").read_text(encoding="utf-8")
            )
            serialized = json.dumps(config, sort_keys=True)
            for retired_identity in (
                "build_id", "contract_version", "marketplace_release",
                "source_commit", "source_ref",
            ):
                self.assertNotIn(retired_identity, serialized)

    def test_preflight_allows_an_unconfigured_git_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            result = self.run_script(
                CHECK, "preflight", "--project-root", str(project), "--json"
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(json.loads(result.stdout)["ok"])

    def test_refresh_inspect_check_apply_converges_and_preserves_project_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            first = self.run_script(
                SETUP, "apply", "--project-root", str(project), "--json",
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(json.loads(first.stdout)["next_entry"], "requirement")

            config_path = project / "workspace/config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["custom_project_field"] = {"owner": "consumer"}
            config_path.write_text(
                json.dumps(config, indent=2) + "\n", encoding="utf-8"
            )
            authored = project / "workspace/docs/user-notes/upgrade-sentinel.md"
            authored.parent.mkdir(parents=True)
            authored.write_text("# Consumer authored\n", encoding="utf-8")
            authored_before = authored.read_bytes()

            obsidian = project / "workspace/docs/.obsidian"
            graph_path = obsidian / "graph.json"
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            graph["search"] = "stale-filter"
            graph["colorGroups"][0]["color"]["rgb"] = 7
            graph["scale"] = 2
            graph_path.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
            types_path = obsidian / "types.json"
            types = json.loads(types_path.read_text(encoding="utf-8"))
            types["types"]["owner_role"] = "number"
            types["types"]["consumer_property"] = "text"
            types["types"]["locked"] = "checkbox"
            types["types"]["challenge_status"] = "text"
            types["types"]["challenge_hash"] = "text"
            types_path.write_text(json.dumps(types, indent=2) + "\n", encoding="utf-8")
            app_path = obsidian / "app.json"
            app = json.loads(app_path.read_text(encoding="utf-8"))
            app["alwaysUpdateLinks"] = False
            app["spellcheck"] = False
            app_path.write_text(json.dumps(app, indent=2) + "\n", encoding="utf-8")
            community_path = obsidian / "community-plugins.json"
            community_path.write_text(
                json.dumps(["unvetted-plugin"], indent=2) + "\n",
                encoding="utf-8",
            )
            plugin_main = (
                obsidian / "plugins/obsidian-front-matter-title-plugin/main.js"
            )
            plugin_main.write_text("stale package projection\n", encoding="utf-8")
            plugin_orphan = plugin_main.parent / "removed-after-package-n.js"
            plugin_orphan.write_text("unshipped package asset\n", encoding="utf-8")
            unrelated_plugin = obsidian / "plugins/user-unrelated/keep.js"
            unrelated_plugin.parent.mkdir()
            unrelated_plugin.write_text("consumer plugin\n", encoding="utf-8")
            gate = project / ".github/agentrof/vault-gate.pyz"
            gate.write_bytes(b"stale portable gate")

            inspected = self.run_script(
                SETUP, "inspect", "--project-root", str(project), "--json"
            )
            self.assertEqual(
                inspected.returncode, 0, inspected.stdout + inspected.stderr
            )
            plan = json.loads(inspected.stdout)
            planned = {item["path"] for item in plan["operations"]}
            self.assertTrue({
                "workspace/docs/.obsidian/app.json",
                "workspace/docs/.obsidian/community-plugins.json",
                "workspace/docs/.obsidian/graph.json",
                "workspace/docs/.obsidian/types.json",
                "workspace/docs/.obsidian/plugins/"
                "obsidian-front-matter-title-plugin/main.js",
                "workspace/docs/.obsidian/plugins/"
                "obsidian-front-matter-title-plugin/removed-after-package-n.js",
                ".github/agentrof/vault-gate.pyz",
            } <= planned)
            by_path = {item["path"]: item for item in plan["operations"]}
            graph_change_keys = {
                item["key"] for item in by_path[
                    "workspace/docs/.obsidian/graph.json"
                ]["changes"]
            }
            self.assertIn("search", graph_change_keys)
            plugin_update = by_path[
                "workspace/docs/.obsidian/plugins/"
                "obsidian-front-matter-title-plugin/main.js"
            ]
            self.assertTrue(plugin_update["before_hash"].startswith("sha256:"))
            self.assertTrue(plugin_update["after_hash"].startswith("sha256:"))
            self.assertEqual(authored.read_bytes(), authored_before)
            self.assertEqual(graph["search"], "stale-filter")
            rejected = self.run_script(
                SETUP, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("managed refresh drift", rejected.stdout)

            applied = self.run_script(
                SETUP, "apply", "--project-root", str(project), "--json"
            )
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            payload = json.loads(applied.stdout)
            self.assertEqual(payload["next_entry"], "requirement")
            routed = self.run_script(
                REQUIREMENT_ROUTE, "--project-root", str(project), "--json"
            )
            self.assertEqual(routed.returncode, 1)
            self.assertEqual(json.loads(routed.stdout)["next_entry"], "requirement")
            self.assertEqual(authored.read_bytes(), authored_before)
            refreshed_config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertNotIn("custom_project_field", refreshed_config)
            refreshed_graph = json.loads(graph_path.read_text(encoding="utf-8"))
            policy = json.loads((
                ROOT / "plugins/software-engineering-team/skill-content/"
                "obsidian-vault/data/vault-policy.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual(refreshed_graph["search"], policy["graph_search"])
            self.assertEqual(refreshed_graph["scale"], 2)
            self.assertNotEqual(
                refreshed_graph["colorGroups"][0]["color"]["rgb"], 7
            )
            refreshed_types = json.loads(types_path.read_text(encoding="utf-8"))
            self.assertEqual(refreshed_types["types"]["owner_role"], "text")
            self.assertEqual(refreshed_types["types"]["consumer_property"], "text")
            self.assertTrue(
                set(policy["retired_managed_properties"]).isdisjoint(
                    refreshed_types["types"]
                )
            )
            refreshed_app = json.loads(app_path.read_text(encoding="utf-8"))
            self.assertTrue(refreshed_app["alwaysUpdateLinks"])
            self.assertFalse(refreshed_app["spellcheck"])
            self.assertEqual(
                json.loads(community_path.read_text(encoding="utf-8")),
                policy["community_plugins"],
            )
            packaged_main = (
                ROOT / "plugins/software-engineering-team/templates/vault/.obsidian/"
                "plugins/obsidian-front-matter-title-plugin/main.js"
            )
            self.assertEqual(plugin_main.read_bytes(), packaged_main.read_bytes())
            self.assertFalse(plugin_orphan.exists())
            self.assertEqual(
                unrelated_plugin.read_text(encoding="utf-8"), "consumer plugin\n"
            )

            checked = self.run_script(
                SETUP, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            second = self.run_script(
                SETUP, "inspect", "--project-root", str(project), "--json"
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(json.loads(second.stdout)["operations"], [])

    def test_inspect_migrates_retired_nested_fields_before_any_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = self.run_script(SETUP, "apply", "--project-root", str(project))
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            config_path = project / "workspace/config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["schema_version"] = 1
            config["legacy_nested_settings"] = {"story": "Work item", "retired": "Retired"}
            config["legacy_settings_history"] = {"story": [{"value": "Old work item"}]}
            config_path.write_text(
                json.dumps(config, indent=2) + "\n", encoding="utf-8"
            )
            before = config_path.read_bytes()

            inspected = self.run_script(
                SETUP, "inspect", "--project-root", str(project), "--json"
            )
            self.assertEqual(inspected.returncode, 0, inspected.stdout + inspected.stderr)
            payload = json.loads(inspected.stdout)
            self.assertTrue(payload["ok"])
            self.assertTrue(any(
                item["surface"] == "workspace_config"
                and "legacy_nested_settings" in item.get("removed_fields", [])
                for item in payload["operations"]
            ))
            self.assertEqual(config_path.read_bytes(), before)

            applied = self.run_script(
                SETUP, "apply", "--project-root", str(project), "--json"
            )
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            migrated = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertNotIn("legacy_nested_settings", migrated)
            self.assertNotIn("legacy_settings_history", migrated)
            self.assertEqual(migrated["schema_version"], 2)

    def test_inspect_surfaces_forbidden_runtime_state_before_apply(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = self.run_script(
                SETUP, "apply", "--project-root", str(project), "--json"
            )
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            forbidden = (
                project / ".agentrof/agent-marketplace/.runtime/cache.sqlite"
            )
            forbidden.write_text("not a database\n", encoding="utf-8")

            inspected = self.run_script(
                SETUP, "inspect", "--project-root", str(project), "--json"
            )
            self.assertEqual(inspected.returncode, 1, inspected.stdout)
            payload = json.loads(inspected.stdout)
            self.assertFalse(payload["ok"])
            self.assertTrue(any(
                "cache.sqlite" in blocker for blocker in payload["blockers"]
            ))

    def test_setup_refuses_legacy_process_local_experience_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = self.run_script(
                SETUP, "apply", "--project-root", str(project), "--json"
            )
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            preview = (
                project / "workspace/docs/experience-design/experiences/checkout/"
                "artifacts/preview.html"
            )
            preview.parent.mkdir(parents=True)
            preview.write_text("<!doctype html><title>Old preview</title>\n",
                               encoding="utf-8")

            inspected = self.run_script(
                SETUP, "inspect", "--project-root", str(project), "--json"
            )
            self.assertEqual(inspected.returncode, 1, inspected.stdout)
            blockers = json.loads(inspected.stdout)["blockers"]
            self.assertTrue(any(
                "process-local Experience web implementation" in blocker
                and "artifacts/application.html" in blocker
                for blocker in blockers
            ))

    def test_setup_refuses_nested_legacy_experience_registry(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = self.run_script(
                SETUP, "apply", "--project-root", str(project), "--json"
            )
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            registry = (
                project / "workspace/docs/experience-design/experiences/checkout/"
                "_generated/artifact-registry.json"
            )
            registry.parent.mkdir(parents=True)
            registry.write_text("{}\n", encoding="utf-8")

            inspected = self.run_script(
                SETUP, "inspect", "--project-root", str(project), "--json"
            )
            self.assertEqual(inspected.returncode, 1, inspected.stdout)
            blockers = json.loads(inspected.stdout)["blockers"]
            self.assertTrue(any(
                "legacy Experience artifact index" in blocker
                and "_generated/artifact-registry.json" in blocker
                for blocker in blockers
            ))
            checked = self.run_script(
                CHECK, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(checked.returncode, 1, checked.stdout)
            self.assertIn("_generated/artifact-registry.json", checked.stdout)

    def test_setup_refuses_symlink_anywhere_in_experience_subtree(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = self.run_script(
                SETUP, "apply", "--project-root", str(project), "--json"
            )
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            external = project / "external-experience"
            external.mkdir()
            (external / "sentinel.md").write_text(
                "# Outside\n", encoding="utf-8"
            )
            link = (
                project / "workspace/docs/experience-design/experiences/linked"
            )
            link.parent.mkdir(parents=True, exist_ok=True)
            try:
                link.symlink_to(external, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks are unavailable: {exc}")

            inspected = self.run_script(
                SETUP, "inspect", "--project-root", str(project), "--json"
            )
            self.assertEqual(inspected.returncode, 1, inspected.stdout)
            blockers = json.loads(inspected.stdout)["blockers"]
            self.assertTrue(any(
                "Experience subtree symlink" in blocker
                and "experience-design/experiences/linked" in blocker
                for blocker in blockers
            ))
            checked = self.run_script(
                CHECK, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(checked.returncode, 1, checked.stdout)
            self.assertIn("Experience subtree symlink", checked.stdout)
            self.assertTrue((external / "sentinel.md").is_file())

    def test_setup_and_check_refuse_hardlinks_in_experience_subtree(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = self.run_script(
                SETUP, "apply", "--project-root", str(project), "--json"
            )
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            ledger = (
                project / "workspace/docs/experience-design/_ledger/"
                "application-revisions.json"
            )
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.write_text(
                '{"schema_version":2,"revisions":[]}\n', encoding="utf-8",
            )
            alias = project / "application-ledger-alias.json"
            try:
                os.link(ledger, alias)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"hard links are unavailable: {exc}")

            inspected = self.run_script(
                SETUP, "inspect", "--project-root", str(project), "--json"
            )
            self.assertEqual(inspected.returncode, 1, inspected.stdout)
            blockers = json.loads(inspected.stdout)["blockers"]
            self.assertTrue(any(
                "hard-link alias" in blocker
                and "application-revisions.json" in blocker
                for blocker in blockers
            ))
            checked = self.run_script(
                CHECK, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(checked.returncode, 1, checked.stdout)
            self.assertIn("hard-link alias", checked.stdout)

    def test_refresh_rolls_back_every_managed_write_on_closing_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = self.run_script(SETUP, "--project-root", str(project))
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            config_path = project / "workspace/config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["custom_project_field"] = {"preserve": True}
            config_path.write_text(
                json.dumps(config, indent=4) + "\n", encoding="utf-8"
            )
            graph_path = (
                project / "workspace/docs/.obsidian/graph.json"
            ).resolve()
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            graph["search"] = "stale-before-failure"
            graph_path.write_text(json.dumps(graph, indent=4) + "\n", encoding="utf-8")
            subprocess.run(
                ["git", "add", "-f",
                 "workspace/docs/.obsidian/community-plugins.json"],
                cwd=project, check=True,
            )
            config_before = config_path.read_bytes()
            graph_before = graph_path.read_bytes()

            failed = self.run_script(
                SETUP, "apply", "--project-root", str(project), "--json"
            )
            self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
            result = json.loads(failed.stdout)
            self.assertTrue(result["rolled_back"])
            self.assertTrue(any(
                "plugin files are tracked" in finding
                for finding in result["findings"]
            ))
            self.assertEqual(config_path.read_bytes(), config_before)
            self.assertEqual(graph_path.read_bytes(), graph_before)

    def test_rollback_preserves_concurrent_authored_markdown(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = self.run_script(SETUP, "apply", "--project-root", str(project))
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            graph_path = project / "workspace/docs/.obsidian/graph.json"
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            graph["search"] = "pre-refresh-drift"
            graph_path.write_text(
                json.dumps(graph, indent=2) + "\n", encoding="utf-8"
            )
            graph_before = graph_path.read_bytes()
            authored = project / "workspace/docs/project-notes/concurrent.md"

            args = argparse.Namespace(
                project_root=str(project), workspace="workspace",
                scale="small", output_language="English",
                terminology_language="English", command="apply", json=True,
            )
            plan = setup_module.build_plan(args)

            def closing_failure(_root: Path, _workspace: str) -> list[str]:
                authored.parent.mkdir(parents=True, exist_ok=True)
                authored.write_text(
                    "# Concurrent user-authored note\n", encoding="utf-8"
                )
                return ["forced closing failure"]

            with mock.patch.object(
                setup_module.setup_check, "closing", side_effect=closing_failure
            ):
                code, result = setup_module.apply_plan(args, plan)
            self.assertEqual(code, 1)
            self.assertTrue(result["rolled_back"])
            self.assertEqual(result["rollback_conflicts"], [])
            self.assertEqual(graph_path.read_bytes(), graph_before)
            self.assertEqual(
                authored.read_text(encoding="utf-8"),
                "# Concurrent user-authored note\n",
            )

    def test_rollback_preserves_concurrent_edit_to_unchanged_managed_note(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = self.run_script(SETUP, "apply", "--project-root", str(project))
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            graph_path = project / "workspace/docs/.obsidian/graph.json"
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            graph["search"] = "pre-refresh-drift"
            graph_path.write_text(
                json.dumps(graph, indent=2) + "\n", encoding="utf-8"
            )
            graph_before = graph_path.read_bytes()
            home = project / "workspace/docs/home.md"
            concurrent_home = home.read_text(encoding="utf-8") + (
                "\nConcurrent project note.\n"
            )
            args = argparse.Namespace(
                project_root=str(project), workspace="workspace", origin=None,
                scale="small", output_language="English",
                terminology_language="English", command="apply", json=True,
            )
            plan = setup_module.build_plan(args)
            original_write = setup_module.RefreshSnapshot.write_bytes
            edited = False

            def write_then_edit(snapshot, path, content, mode=0o644):
                nonlocal edited
                original_write(snapshot, path, content, mode)
                if not edited:
                    home.write_text(concurrent_home, encoding="utf-8")
                    edited = True

            with mock.patch.object(
                setup_module.RefreshSnapshot, "write_bytes",
                new=write_then_edit,
            ), mock.patch.object(
                setup_module.setup_check, "closing",
                return_value=["forced closing failure"],
            ):
                code, result = setup_module.apply_plan(args, plan)
            self.assertEqual(code, 1)
            self.assertTrue(result["rolled_back"])
            self.assertEqual(result["rollback_conflicts"], [])
            self.assertEqual(home.read_text(encoding="utf-8"), concurrent_home)
            self.assertEqual(graph_path.read_bytes(), graph_before)

    def test_rollback_reports_concurrent_edit_to_written_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = self.run_script(SETUP, "apply", "--project-root", str(project))
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            graph_path = (
                project / "workspace/docs/.obsidian/graph.json"
            ).resolve()
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            graph["search"] = "pre-refresh-drift"
            graph_path.write_text(
                json.dumps(graph, indent=2) + "\n", encoding="utf-8"
            )
            args = argparse.Namespace(
                project_root=str(project), workspace="workspace", origin=None,
                scale="small", output_language="English",
                terminology_language="English", command="apply", json=True,
            )
            plan = setup_module.build_plan(args)
            original_write = setup_module.RefreshSnapshot.write_bytes

            def write_then_conflict(snapshot, path, content, mode=0o644):
                original_write(snapshot, path, content, mode)
                if path == graph_path:
                    concurrent = json.loads(path.read_text(encoding="utf-8"))
                    concurrent["consumer_zoom"] = 1.25
                    path.write_text(
                        json.dumps(concurrent, indent=2) + "\n",
                        encoding="utf-8",
                    )

            with mock.patch.object(
                setup_module.RefreshSnapshot, "write_bytes",
                new=write_then_conflict,
            ), mock.patch.object(
                setup_module.setup_check, "closing",
                return_value=["forced closing failure"],
            ):
                code, result = setup_module.apply_plan(args, plan)
            self.assertEqual(code, 1)
            self.assertTrue(result["rolled_back"])
            relative = "workspace/docs/.obsidian/graph.json"
            self.assertIn(relative, result["rollback_conflicts"])
            self.assertEqual(
                json.loads(graph_path.read_text(encoding="utf-8"))[
                    "consumer_zoom"
                ],
                1.25,
            )

    def test_pre_replace_recheck_preserves_racing_target_edit(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = self.run_script(SETUP, "apply", "--project-root", str(project))
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            graph_path = (
                project / "workspace/docs/.obsidian/graph.json"
            ).resolve()
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            graph["search"] = "pre-refresh-drift"
            graph_path.write_text(
                json.dumps(graph, indent=2) + "\n", encoding="utf-8"
            )
            args = argparse.Namespace(
                project_root=str(project), workspace="workspace", origin=None,
                scale="small", output_language="English",
                terminology_language="English", command="apply", json=True,
            )
            plan = setup_module.build_plan(args)
            original_atomic = setup_module.atomic_bytes
            injected = False

            def atomic_with_race(path, content, mode=0o644,
                                 before_replace=None):
                nonlocal injected
                if path == graph_path and not injected:
                    concurrent = json.loads(path.read_text(encoding="utf-8"))
                    concurrent["consumer_zoom"] = 1.5
                    path.write_text(
                        json.dumps(concurrent, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    injected = True
                return original_atomic(
                    path, content, mode, before_replace=before_replace
                )

            with mock.patch.object(
                setup_module, "atomic_bytes", new=atomic_with_race
            ):
                code, result = setup_module.apply_plan(args, plan)
            self.assertEqual(code, 1)
            self.assertIn("concurrent edit changed refresh target",
                          result["error"])
            self.assertIn(
                "workspace/docs/.obsidian/graph.json",
                result["rollback_conflicts"],
            )
            self.assertEqual(
                json.loads(graph_path.read_text(encoding="utf-8"))[
                    "consumer_zoom"
                ],
                1.5,
            )

    def test_noncanonical_managed_workspace_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            alternate = project / "alternate"
            alternate.mkdir()
            (alternate / "config.json").write_text(json.dumps({
                "team_id": "software-engineering-team",
            }) + "\n", encoding="utf-8")
            result = self.run_script(
                SETUP, "--project-root", str(project), "--json"
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("non-canonical managed workspace", result.stderr)

    def test_setup_check_rejects_database_state_in_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = self.run_script(SETUP, "--project-root", str(project))
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            database = (
                project / ".agentrof" / "agent-marketplace" / ".runtime"
                / "cache.sqlite"
            )
            database.write_bytes(b"not a database")
            checked = self.run_script(
                CHECK, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(checked.returncode, 1)
            findings = json.loads(checked.stdout)["findings"]
            self.assertTrue(any("forbidden in runtime" in item
                                for item in findings))

    def test_setup_check_rejects_state_next_to_the_runtime_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = self.run_script(SETUP, "--project-root", str(project))
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            residue = project / ".agentrof/agent-marketplace/backlog.json"
            residue.write_text("{}\n", encoding="utf-8")
            checked = self.run_script(
                CHECK, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(checked.returncode, 1)
            findings = json.loads(checked.stdout)["findings"]
            self.assertTrue(any("only .runtime" in item for item in findings))

    def test_local_obsidian_plugin_projection_is_recreated_but_not_clone_truth(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = self.run_script(SETUP, "--project-root", str(project))
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            obsidian = project / "workspace/docs/.obsidian"
            (obsidian / "community-plugins.json").unlink()
            shutil.rmtree(obsidian / "plugins")

            portable = project / ".github/agentrof/vault-gate.pyz"
            clone_gate = subprocess.run([
                sys.executable, str(portable), "check", "--project-root",
                str(project), "--json",
            ], capture_output=True, text=True, check=False)
            self.assertEqual(
                clone_gate.returncode, 0, clone_gate.stdout + clone_gate.stderr
            )
            local_check = self.run_script(
                SETUP, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(local_check.returncode, 1)
            self.assertIn("package-projected local", local_check.stdout)
            repaired = self.run_script(
                SETUP, "apply", "--project-root", str(project), "--json"
            )
            self.assertEqual(repaired.returncode, 0, repaired.stdout + repaired.stderr)
            self.assertTrue((obsidian / "community-plugins.json").is_file())
            self.assertTrue((
                obsidian / "plugins/obsidian-front-matter-title-plugin/main.js"
            ).is_file())

    def test_package_projection_converges_both_file_directory_shape_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary)
            vault = fixture / "vault"
            payload = fixture / "payload"
            source_plugin = payload / "plugins/fixture-plugin"
            source_plugin.mkdir(parents=True)
            (source_plugin / "file-now.js").write_text(
                "package file\n", encoding="utf-8"
            )
            (source_plugin / "directory-now").mkdir()
            (source_plugin / "directory-now/asset.js").write_text(
                "package nested asset\n", encoding="utf-8"
            )

            target_plugin = (
                vault / ".obsidian/plugins/fixture-plugin"
            )
            (target_plugin / "file-now.js").mkdir(parents=True)
            (target_plugin / "file-now.js/unshipped.js").write_text(
                "old directory shape\n", encoding="utf-8"
            )
            (target_plugin / "directory-now").write_text(
                "old file shape\n", encoding="utf-8"
            )
            unrelated = vault / ".obsidian/plugins/user-plugin/keep.js"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_text("consumer plugin\n", encoding="utf-8")
            policy = {"community_plugins": ["fixture-plugin"]}

            planned_deletions = vault_payload.payload_reconcile_deletions(
                vault, policy, payload
            )
            self.assertIn(target_plugin / "file-now.js", planned_deletions)
            self.assertIn(target_plugin / "directory-now", planned_deletions)
            planned_updates = vault_payload.payload_reconcile_updates(
                vault, policy, payload
            )
            self.assertIn(target_plugin / "file-now.js", planned_updates)

            reconciled = vault_payload.payload_reconcile(vault, policy, payload)
            self.assertGreater(reconciled, 0)
            copied = vault_payload.materialize_payload(vault, policy, payload)
            self.assertGreater(copied, 0)
            self.assertEqual(
                vault_payload.payload_reconcile(vault, policy, payload), 0
            )
            self.assertEqual(
                (target_plugin / "file-now.js").read_text(encoding="utf-8"),
                "package file\n",
            )
            self.assertEqual(
                (target_plugin / "directory-now/asset.js").read_text(
                    encoding="utf-8"
                ),
                "package nested asset\n",
            )
            self.assertEqual(
                unrelated.read_text(encoding="utf-8"), "consumer plugin\n"
            )
            self.assertEqual(
                vault_payload.payload_reconcile_deletions(vault, policy, payload),
                [],
            )
            self.assertEqual(
                vault_payload.payload_reconcile_updates(vault, policy, payload),
                {},
            )

    def test_backlog_init_requires_explicit_modern_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            setup = self.run_script(SETUP, "--project-root", str(project))
            self.assertEqual(setup.returncode, 0, setup.stderr)
            docs = project / "workspace" / "docs"
            result = self.run_script(BACKLOG, "init", "--docs", str(docs))
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertFalse((docs / "backlog" / "backlog.md").exists())

    def test_runtime_symlink_is_rejected_without_following_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            target = Path(temporary) / "outside"
            project.mkdir()
            target.mkdir()
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            (project / ".agentrof").symlink_to(target, target_is_directory=True)
            result = self.run_script(
                SETUP, "--project-root", str(project), "--json"
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("runtime path is symlinked", result.stderr)
            self.assertFalse((target / "agent-marketplace").exists())

    def test_concurrent_identical_setup_converges(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            command = [
                sys.executable, str(SETUP), "--project-root", str(project), "--json"
            ]
            processes = [
                subprocess.Popen(
                    command, cwd=ROOT, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True,
                )
                for _ in range(2)
            ]
            results = [process.communicate(timeout=60) for process in processes]
            for process, (stdout, stderr) in zip(processes, results):
                self.assertEqual(process.returncode, 0, stdout + stderr)
            checked = self.run_script(
                CHECK, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == "__main__":
    unittest.main()
