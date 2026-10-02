from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import stat
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
import delivery_git
import setup_project as setup_module
import stage_package
from tools.tests import backlog_fixture, fixture_cache
from tools.tests.git_fixture import init_repository, temporary_directory
from unittest import mock

ATTRIBUTES_BLOCK = (
    "# agent-marketplace:software-engineering-team:gitattributes:start\n"
    "workspace/docs/** -text\n"
    "# agent-marketplace:software-engineering-team:gitattributes:end\n"
)


_APPLIED = fixture_cache.AppliedProjectCache()


class SetupProjectTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        _APPLIED.close()

    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    @contextlib.contextmanager
    def applied_project(self):
        """A project right after one setup apply, copied from this process's applied seed."""
        with temporary_directory() as temporary:
            _APPLIED.apply_to(Path(temporary), _APPLIED_CONTEXT)
            yield Path(temporary)

    def test_bootstrap_is_project_local_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            init_repository(project)
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
            init_repository(project)
            result = self.run_script(
                CHECK, "preflight", "--project-root", str(project), "--json"
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(json.loads(result.stdout)["ok"])

    def test_refresh_inspect_check_apply_converges_and_preserves_project_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            init_repository(project)
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

    def test_inspect_surfaces_forbidden_runtime_state_before_apply(self):
        with self.applied_project() as project:
            forbidden = (
                project / ".agentrof/agent-marketplace/.runtime/project.sqlite"
            )
            forbidden.write_text("not a database\n", encoding="utf-8")

            inspected = self.run_script(
                SETUP, "inspect", "--project-root", str(project), "--json"
            )
            self.assertEqual(inspected.returncode, 1, inspected.stdout)
            payload = json.loads(inspected.stdout)
            self.assertFalse(payload["ok"])
            self.assertTrue(any(
                "project.sqlite" in blocker for blocker in payload["blockers"]
            ))

    def test_setup_accepts_process_local_experience_prototype_files(self):
        with self.applied_project() as project:
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
            self.assertEqual(inspected.returncode, 0, inspected.stdout)

    def test_setup_refuses_nested_legacy_experience_registry(self):
        with self.applied_project() as project:
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
        with self.applied_project() as project:
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
        with self.applied_project() as project:
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
        with self.applied_project() as project:
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
        with self.applied_project() as project:
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
        with self.applied_project() as project:
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
        with self.applied_project() as project:
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
        with self.applied_project() as project:
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
            init_repository(project)
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

    def test_setup_check_admits_disposable_tool_databases_in_runtime(self):
        """Scratch is where the required cadence writes its tool output."""
        with self.applied_project() as project:
            runtime = project / ".agentrof" / "agent-marketplace" / ".runtime"
            for relative in ("tools/grype-db/6/vulnerability.db",
                             "verification/mutation/api/mutants.sqlite",
                             "verification/mutation/api/baseline.sqlite3"):
                disposable = runtime / relative
                disposable.parent.mkdir(parents=True, exist_ok=True)
                disposable.write_bytes(b"disposable tool output")
            checked = self.run_script(
                CHECK, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            self.assertEqual(json.loads(checked.stdout)["findings"], [])

            for reserved in ("project.db", "backlog.sqlite", "backlog.json"):
                condemned = runtime / reserved
                condemned.write_bytes(b"canonical state in the wrong place")
                rejected = self.run_script(
                    CHECK, "check", "--project-root", str(project), "--json"
                )
                self.assertEqual(rejected.returncode, 1, reserved)
                self.assertTrue(any("forbidden in runtime" in item
                                    for item in json.loads(rejected.stdout)["findings"]), reserved)
                condemned.unlink()

    def test_setup_check_rejects_state_next_to_the_runtime_directory(self):
        with self.applied_project() as project:
            residue = project / ".agentrof/agent-marketplace/backlog.json"
            residue.write_text("{}\n", encoding="utf-8")
            checked = self.run_script(
                CHECK, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(checked.returncode, 1)
            findings = json.loads(checked.stdout)["findings"]
            self.assertTrue(any("only .runtime" in item for item in findings))

    def test_setup_check_names_tracked_local_files_outside_ascii_exactly(self):
        """The tracked local and plugin file findings name each file from a
        NUL-separated listing; without -z Git quotes a name outside ASCII
        (#276)."""
        with self.applied_project() as project:
            local = ".claude/ayarlar-şğı.json"
            plugin = ("workspace/docs/.obsidian/plugins/"
                      "obsidian-front-matter-title-plugin/çeviri-ğı.json")
            for relative in (local, plugin):
                (project / relative).parent.mkdir(parents=True, exist_ok=True)
                (project / relative).write_text("{}\n", encoding="utf-8")
            subprocess.run(["git", "add", "-f", "--", local, plugin],
                           cwd=project, check=True)
            checked = self.run_script(
                CHECK, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(checked.returncode, 1, checked.stdout)
            findings = json.loads(checked.stdout)["findings"]
            self.assertIn(
                "local runtime or projection files are force-added: " + local,
                findings)
            self.assertIn(
                "package-projected local Obsidian plugin files are tracked: "
                + plugin, findings)

    def test_local_obsidian_plugin_projection_is_recreated_but_not_clone_truth(self):
        with self.applied_project() as project:
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

    def test_runtime_symlink_is_rejected_without_following_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            target = Path(temporary) / "outside"
            project.mkdir()
            target.mkdir()
            init_repository(project)
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
            init_repository(project)
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

    def git(self, project: Path, *args: str) -> bytes:
        return subprocess.run(
            ["git", "-c", "user.name=Fixture",
             "-c", "user.email=fixture@example.invalid", *args],
            cwd=project, capture_output=True, check=True,
        ).stdout

    def test_setup_adds_the_gitattributes_block_once_and_keeps_project_lines(self):
        with tempfile.TemporaryDirectory() as temporary:
            fresh = Path(temporary) / "fresh"
            fresh.mkdir()
            init_repository(fresh)
            inspected = self.run_script(
                SETUP, "inspect", "--project-root", str(fresh), "--json"
            )
            self.assertEqual(
                inspected.returncode, 0, inspected.stdout + inspected.stderr
            )
            planned = {
                item["path"]: item
                for item in json.loads(inspected.stdout)["operations"]
            }
            self.assertIn(".gitattributes", planned)
            self.assertEqual(planned[".gitattributes"]["action"], "create")
            self.assertEqual(
                planned[".gitattributes"]["ownership"], "tracked_managed_block"
            )
            self.assertFalse((fresh / ".gitattributes").exists())
            applied = self.run_script(
                SETUP, "apply", "--project-root", str(fresh), "--json"
            )
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            self.assertEqual(
                (fresh / ".gitattributes").read_bytes(),
                ATTRIBUTES_BLOCK.encode("utf-8"),
            )

            owned = Path(temporary) / "owned"
            owned.mkdir()
            init_repository(owned)
            attributes = owned / ".gitattributes"
            before, after = "* text=auto\n*.png binary\n", "*.sh text eol=lf\n"
            attributes.write_bytes(before.encode("utf-8"))
            applied = self.run_script(
                SETUP, "apply", "--project-root", str(owned), "--json"
            )
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            self.assertEqual(
                attributes.read_bytes(),
                (before + "\n" + ATTRIBUTES_BLOCK).encode("utf-8"),
            )

            stale = ATTRIBUTES_BLOCK.replace("** -text", "** text")
            attributes.write_bytes((before + stale + after).encode("utf-8"))
            original = attributes.read_bytes()
            args = argparse.Namespace(
                project_root=str(owned), workspace="workspace",
                output_language="English", terminology_language="English",
                command="apply", json=True,
            )
            with mock.patch.object(
                setup_module.setup_check, "closing",
                return_value=["forced closing failure"],
            ):
                code, result = setup_module.apply_plan(args)
            self.assertEqual(code, 1)
            self.assertTrue(result["rolled_back"])
            self.assertEqual(attributes.read_bytes(), original)

            applied = self.run_script(
                SETUP, "apply", "--project-root", str(owned), "--json"
            )
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            converged = (before + ATTRIBUTES_BLOCK + after).encode("utf-8")
            self.assertEqual(attributes.read_bytes(), converged)
            repeated = self.run_script(
                SETUP, "apply", "--project-root", str(owned), "--json"
            )
            self.assertEqual(repeated.returncode, 0, repeated.stdout + repeated.stderr)
            self.assertEqual(json.loads(repeated.stdout)["applied_operations"], [])
            self.assertEqual(attributes.read_bytes(), converged)

    def test_setup_check_reports_a_missing_stale_or_overridden_gitattributes_rule(self):
        with self.applied_project() as project:
            attributes = project / ".gitattributes"
            local_attributes = project / ".git" / "info" / "attributes"
            own_lines = "* text=auto\n"
            cases = (
                ("missing", own_lines, "",
                 "managed .gitattributes marker is missing or duplicated", True),
                ("stale", ATTRIBUTES_BLOCK.replace("** -text", "** text"), "",
                 "managed .gitattributes block is stale", True),
                ("later line", ATTRIBUTES_BLOCK + "*.md text\n", "",
                 "workspace/docs/home.md (text: set)", False),
                ("local attributes", ATTRIBUTES_BLOCK, "*.json text\n",
                 "workspace/docs/.obsidian/app.json (text: set)", False),
            )
            for name, text, local, expected, drift in cases:
                with self.subTest(name):
                    attributes.write_bytes(text.encode("utf-8"))
                    local_attributes.parent.mkdir(exist_ok=True)
                    local_attributes.write_bytes(local.encode("utf-8"))
                    checked = self.run_script(
                        SETUP, "check", "--project-root", str(project), "--json"
                    )
                    self.assertEqual(checked.returncode, 1, checked.stdout)
                    findings = json.loads(checked.stdout)["findings"]
                    self.assertTrue(
                        any(expected in item for item in findings), findings
                    )
                    self.assertEqual(
                        "managed refresh drift: .gitattributes" in findings,
                        drift, findings,
                    )
            local_attributes.write_bytes(b"")
            attributes.write_bytes(ATTRIBUTES_BLOCK.encode("utf-8"))
            checked = self.run_script(
                SETUP, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(checked.returncode, 0, checked.stdout)

    def test_managed_rule_checks_governed_markdown_out_byte_identical_under_autocrlf(self):
        with temporary_directory() as temporary:
            project = Path(temporary)
            init_repository(project)
            self.git(project, "config", "core.autocrlf", "true")
            self.git(project, "config", "core.safecrlf", "false")
            relative = "workspace/docs/backlog/reviews/round-1-backlog-review.md"
            review = project / relative
            review.parent.mkdir(parents=True)
            review.write_bytes(
                b"---\ntype: backlog-review\nstatus: approved\n---\n\n"
                b"# Backlog review\n\nApproved.\n"
            )
            self.git(project, "add", "--", relative)
            self.git(project, "commit", "-qm", "Approve the backlog review")
            committed = self.git(project, "cat-file", "blob", f"HEAD:{relative}")
            converted = committed.replace(b"\n", b"\r\n")

            review.unlink()
            self.git(project, "checkout", "--", relative)
            self.assertEqual(review.read_bytes(), converted)
            self.assertFalse(stage_package.paths_are_committed([review]))

            applied = self.run_script(
                SETUP, "apply", "--project-root", str(project), "--json"
            )
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            self.assertTrue((project / ".gitattributes").is_file())
            self.git(project, "add", "--", ".gitattributes")
            self.git(project, "commit", "-qm", "Keep governed bytes exact")
            self.assertEqual(review.read_bytes(), converted)

            self.git(project, "rm", "-r", "--cached", "--quiet", "--",
                     "workspace/docs")
            self.git(project, "checkout", "HEAD", "--", "workspace/docs")
            self.assertEqual(review.read_bytes(), committed)
            self.assertTrue(stage_package.paths_are_committed([review]))
            review.unlink()
            self.git(project, "checkout", "--", relative)
            self.assertEqual(review.read_bytes(), committed)
            self.assertEqual(
                self.git(project, "status", "--porcelain", "--", relative), b""
            )

    @unittest.skipIf(os.name == "nt", "POSIX file mode contract")
    def test_setup_gives_new_files_the_umask_mode_and_keeps_existing_modes(self):
        previous = os.umask(0o022)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary)
                init_repository(project)
                applied = self.run_script(
                    SETUP, "apply", "--project-root", str(project), "--json"
                )
                self.assertEqual(
                    applied.returncode, 0, applied.stdout + applied.stderr
                )
                reports = sorted(
                    (project / "workspace/docs/maps/_generated").glob("*.md")
                )
                self.assertTrue(reports)
                ignore = project / ".gitignore"
                config = project / "workspace/config.json"
                for path in (ignore, project / ".gitattributes", config, reports[0]):
                    with self.subTest(created=path.name):
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

                ignore.write_text(ignore.read_text(encoding="utf-8").replace(
                    "workspace/docs/.trash/", "workspace/docs/.stale-trash/",
                ), encoding="utf-8")
                value = json.loads(config.read_text(encoding="utf-8"))
                value["custom_project_field"] = True
                config.write_text(json.dumps(value) + "\n", encoding="utf-8")
                for path in (ignore, config):
                    path.chmod(0o640)
                applied = self.run_script(
                    SETUP, "apply", "--project-root", str(project), "--json"
                )
                self.assertEqual(
                    applied.returncode, 0, applied.stdout + applied.stderr
                )
                rewritten = {
                    item["path"] for item in json.loads(applied.stdout)["applied_operations"]
                }
                self.assertTrue({".gitignore", "workspace/config.json"} <= rewritten)
                for path in (ignore, config):
                    with self.subTest(existing=path.name):
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        finally:
            os.umask(previous)

    @unittest.skipIf(os.name == "nt", "native Windows keeps no POSIX mode")
    def test_refresh_gives_files_older_writers_left_owner_only_their_read_access_back(self):
        """Writers before v0.4.0 left files at 0600 and writers since keep an existing mode,
        so setup check reports a tracked non-executable file at exactly 0600 as drift and
        apply gives back group and other read. A file narrowed any other way keeps its mode (#285)."""
        previous = os.umask(0o022)
        try:
            with self.applied_project() as project:
                for command in (["add", "-A"], ["-c", "user.email=test@example.com", "-c", "user.name=Test",
                                                "commit", "-qm", "setup"]):
                    subprocess.run(["git", "-C", str(project), *command], check=True)
                reports = sorted((project / "workspace/docs/maps/_generated").glob("*.md"))
                owner_only = [project / "workspace/config.json", project / ".gitignore", reports[0]]
                narrowed = reports[1]
                for path in owner_only:
                    path.chmod(0o600)
                narrowed.chmod(0o640)
                expected = sorted(path.relative_to(project).as_posix() for path in owner_only)

                checked = self.run_script(SETUP, "check", "--project-root", str(project), "--json")
                self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)
                self.assertEqual(sorted(finding.removeprefix("managed refresh drift: ")
                                        for finding in json.loads(checked.stdout)["findings"]), expected)
                inspected = self.run_script(SETUP, "inspect", "--project-root", str(project), "--json")
                repairs = [item for item in json.loads(inspected.stdout)["operations"]]
                self.assertEqual([(item["path"], item["surface"], item["mode_before"], item["mode_after"])
                                  for item in repairs],
                                 [(path, "file_mode", "0600", "0644") for path in expected])

                applied = self.run_script(SETUP, "apply", "--project-root", str(project), "--json")
                self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
                for path in owner_only:
                    with self.subTest(repaired=path.name):
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
                self.assertEqual(stat.S_IMODE(narrowed.stat().st_mode), 0o640)
                checked = self.run_script(SETUP, "check", "--project-root", str(project), "--json")
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
        finally:
            os.umask(previous)

    @unittest.skipIf(os.name == "nt", "native Windows keeps no POSIX mode")
    def test_mode_repair_leaves_untracked_executable_and_outside_files(self):
        """Only tracked non-executable files in setup's scope at exactly 0600 are repaired. They
        gain only the read bits the umask allows, never group write, and a umask that allows no
        read bit repairs nothing (#285)."""
        previous = os.umask(0o022)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary).resolve()
                init_repository(project)
                docs = project / "workspace/docs"
                docs.mkdir(parents=True)
                note, tool, outside = docs / "note.md", docs / "tool.sh", project / "src.py"
                for path in (note, tool, outside):
                    path.write_text("x\n", encoding="utf-8")
                tool.chmod(0o755)
                for command in (["add", "-A"], ["-c", "user.email=test@example.com", "-c", "user.name=Test",
                                                "commit", "-qm", "files"]):
                    subprocess.run(["git", "-C", str(project), *command], check=True)
                draft = docs / "draft.md"
                draft.write_text("x\n", encoding="utf-8")
                for path in (note, outside, draft):
                    path.chmod(0o600)
                tool.chmod(0o700)
                for umask, repairs in ((0o022, {note: 0o644}), (0o002, {note: 0o644}),
                                       (0o027, {note: 0o640}), (0o077, {})):
                    with self.subTest(umask=oct(umask)):
                        os.umask(umask)
                        self.assertEqual(setup_module.owner_only_repairs(project, "workspace"), repairs)
        finally:
            os.umask(previous)


class WindowsLongPathsChoiceTests(unittest.TestCase):
    """Item and Integration worktrees share the project repository's local Git config, and Git
    for Windows leaves out of a checkout that still exits 0 every tracked file whose path reaches
    260 characters, or whose directory reaches 248, unless core.longpaths is on. Native Windows
    setup asks before it turns that on; any other host asks nothing (#358). The platform is
    mocked, so every host runs these."""

    def run_setup(self, project: Path, command: str, *options: str,
              windows: bool = True) -> tuple[int, dict]:
        output = io.StringIO()
        with mock.patch.object(setup_module, "native_windows", return_value=windows), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = setup_module.main([command, "--project-root", str(project), "--json", *options])
        return code, json.loads(output.getvalue())

    def local_long_paths(self, project: Path) -> str | None:
        value = subprocess.run(["git", "-C", str(project), "config", "--local", "--get", "core.longpaths"],
                               capture_output=True, text=True, check=False)
        return value.stdout.strip() if value.returncode == 0 else None

    def stage(self, project: Path, relative: str) -> str:
        (project / relative).parent.mkdir(parents=True, exist_ok=True)
        (project / relative).write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(project), "add", "--", relative], check=True)
        return relative

    def track(self, project: Path, worktree: Path, length: int, folder: str) -> str:
        """Stage a file whose path is exactly *length* characters long inside *worktree*."""
        relative = f"{folder}/" + "x" * (length - len(str(worktree)) - len(folder) - 6) + ".txt"
        self.assertEqual(len(str(worktree / relative)), length)
        return self.stage(project, relative)

    def directory(self, worktree: Path, length: int, folder: str) -> str:
        """Name a directory whose path is exactly *length* characters long inside *worktree*."""
        relative = f"{folder}/" + "d" * (length - len(str(worktree)) - len(folder) - 2)
        self.assertEqual(len(str(worktree / relative)), length)
        return relative

    def test_native_windows_asks_before_turning_long_paths_on(self):
        with temporary_directory() as temporary:
            project = Path(temporary).resolve()
            init_repository(project)
            code, plan = self.run_setup(project, "inspect")
            self.assertEqual(code, 0, plan)
            self.assertEqual(plan["choice_requests"], [{
                "id": "git.core_longpaths", "surface": "core.longpaths",
                "reason": "windows_path_limit", "options": ["set", "leave"],
                "recommended": "set",
                "preview": {
                    "current_value": None,
                    "set_command": "git config --local core.longpaths true",
                    "worktree_root": str(delivery_git.worktree_paths(project, "DLV-001")["integration"]),
                    "long_paths": [],
                },
            }])
            code, refused = self.run_setup(project, "apply")
            self.assertEqual(code, 1, refused)
            self.assertEqual(refused["findings"],
                             ["choice required: pass --choice git.core_longpaths=<set|leave>"])
            self.assertFalse((project / "workspace").exists())
            self.assertIsNone(self.local_long_paths(project))
            code, failed = self.run_setup(project, "apply", "--choice", "git.core_longpaths=maybe")
            self.assertEqual(code, 1, failed)
            self.assertIn("invalid --choice 'git.core_longpaths=maybe'", failed["error"])
            self.assertFalse((project / "workspace").exists())

    def test_answering_set_writes_the_local_config(self):
        with temporary_directory() as temporary:
            project = Path(temporary).resolve()
            init_repository(project)
            code, applied = self.run_setup(project, "apply", "--choice", "git.core_longpaths=set")
            self.assertEqual(code, 0, applied)
            self.assertEqual(self.local_long_paths(project), "true")
            self.assertIn({
                "action": "update", "surface": "git_config", "path": "core.longpaths",
                "ownership": "repository_local_config",
                "changes": [{"key": "core.longpaths", "before": None, "after": "true"}],
            }, applied["applied_operations"])
            code, plan = self.run_setup(project, "inspect")
            self.assertEqual((code, plan["choice_requests"], plan["operations"]), (0, [], []))
            code, checked = self.run_setup(project, "check")
            self.assertEqual(code, 0, checked)

    def test_answering_leave_writes_nothing_and_names_each_long_tracked_path(self):
        with temporary_directory() as temporary:
            project = Path(temporary).resolve()
            init_repository(project)
            integration = delivery_git.worktree_paths(project, "DLV-001")["integration"]
            reaching = self.track(project, integration, 260, "deep")
            short = self.track(project, integration, 259, "near")
            config = (project / ".git" / "config").read_bytes()
            code, plan = self.run_setup(project, "inspect")
            self.assertEqual(code, 0, plan)
            self.assertEqual(plan["choice_requests"][0]["preview"]["long_paths"],
                             [{"path": reaching, "reason": "file_path", "length": 260}])
            code, applied = self.run_setup(project, "apply", "--choice", "git.core_longpaths=leave")
            self.assertEqual(code, 0, applied)
            self.assertEqual((project / ".git" / "config").read_bytes(), config)
            self.assertNotIn("git_config", [item["surface"] for item in applied["applied_operations"]])
            self.assertEqual(applied["warnings"], [
                "core.longpaths stays off, so Git for Windows leaves these tracked paths out of"
                f" Item and Integration worktrees under {integration}: {reaching} (file path 260 >= 260)"
            ])
            self.assertNotIn(short, applied["warnings"][0])
            code, checked = self.run_setup(project, "check")
            self.assertEqual((code, checked["findings"]), (0, []))

    def test_a_directory_reaching_248_characters_leaves_its_short_files_out(self):
        """CreateDirectoryW keeps room for an 8.3 file name, so Git for Windows creates no
        directory whose path reaches 248 characters and leaves out every file in it, even one
        whose own path is under 260. The directory is the reason named, since it fails first."""
        with temporary_directory() as temporary:
            project = Path(temporary).resolve()
            init_repository(project)
            integration = delivery_git.worktree_paths(project, "DLV-001")["integration"]
            deep = self.directory(integration, 248, "deep")
            inside = self.stage(project, f"{deep}/a.txt")
            crossing = self.stage(project, f"{deep}/" + "y" * 20 + ".txt")
            beside = self.stage(project, self.directory(integration, 247, "near") + "/a.txt")
            self.assertLess(len(str(integration / inside)), 260)
            self.assertGreaterEqual(len(str(integration / crossing)), 260)
            code, plan = self.run_setup(project, "inspect")
            self.assertEqual(code, 0, plan)
            self.assertEqual(plan["choice_requests"][0]["preview"]["long_paths"], [
                {"path": inside, "reason": "directory", "length": 248},
                {"path": crossing, "reason": "directory", "length": 248},
            ])
            code, applied = self.run_setup(project, "apply", "--choice", "git.core_longpaths=leave")
            self.assertEqual(code, 0, applied)
            self.assertEqual(applied["warnings"], [
                "core.longpaths stays off, so Git for Windows leaves these tracked paths out of"
                f" Item and Integration worktrees under {integration}: {inside} (directory 248 >= 248),"
                f" {crossing} (directory 248 >= 248)"
            ])
            self.assertNotIn(beside, applied["warnings"][0])

    def test_the_deepest_story_item_worktree_sets_the_limit(self):
        with temporary_directory() as temporary:
            project = Path(temporary).resolve()
            init_repository(project)
            backlog_fixture.make_approved_backlog(project / "workspace" / "docs", "CHECKOUTFLOW-01")
            item = delivery_git.worktree_paths(project, "DLV-001", "CHECKOUTFLOW-01")["item"]
            integration = delivery_git.worktree_paths(project, "DLV-001")["integration"]
            self.assertGreater(len(str(item)), len(str(integration)))
            reaching = self.track(project, item, 260, "deep")
            self.assertLess(len(str(integration / reaching)), 260)
            code, plan = self.run_setup(project, "inspect")
            self.assertEqual(code, 0, plan)
            preview = plan["choice_requests"][0]["preview"]
            self.assertEqual((preview["worktree_root"], preview["long_paths"]),
                             (str(item), [{"path": reaching, "reason": "file_path", "length": 260}]))

    def test_a_value_git_reads_as_true_asks_nothing(self):
        # "yes" is true only to Git's own boolean parsing, which a literal "true" would skip.
        with temporary_directory() as temporary:
            project = Path(temporary).resolve()
            init_repository(project)
            subprocess.run(["git", "-C", str(project), "config", "--local", "core.longpaths", "yes"],
                           check=True)
            code, plan = self.run_setup(project, "inspect")
            self.assertEqual((code, plan["choice_requests"]), (0, []))
            code, applied = self.run_setup(project, "apply")
            self.assertEqual(code, 0, applied)
            self.assertEqual(self.local_long_paths(project), "yes")

    def test_other_hosts_ask_nothing(self):
        with temporary_directory() as temporary:
            project = Path(temporary).resolve()
            init_repository(project)
            code, plan = self.run_setup(project, "inspect", windows=False)
            self.assertEqual((code, plan["choice_requests"], plan["warnings"]), (0, [], []))
            code, applied = self.run_setup(project, "apply", "--choice", "git.core_longpaths=set",
                                       windows=False)
            self.assertEqual(code, 0, applied)
            self.assertNotIn("git_config", [item["surface"] for item in applied["applied_operations"]])
            self.assertIsNone(self.local_long_paths(project))

    def test_a_failed_apply_puts_the_value_back(self):
        for before in (None, "false"):
            with self.subTest(before=before), temporary_directory() as temporary:
                project = Path(temporary).resolve()
                init_repository(project)
                if before is not None:
                    subprocess.run(["git", "-C", str(project), "config", "--local", "core.longpaths", before],
                                   check=True)
                with mock.patch.object(setup_module.setup_check, "closing",
                                       return_value=["forced closing failure"]):
                    code, failed = self.run_setup(project, "apply", "--choice", "git.core_longpaths=set")
                self.assertEqual((code, failed["rolled_back"], failed["rollback_conflicts"]), (1, True, []))
                self.assertEqual(self.local_long_paths(project), before)


# A changed environment, working directory or subprocess binding builds a fresh project.
_APPLIED_CONTEXT = fixture_cache.context_snapshot((subprocess, fixture_cache))


if __name__ == "__main__":
    unittest.main()
