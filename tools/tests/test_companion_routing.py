from __future__ import annotations

import json
import io
from contextlib import redirect_stdout, redirect_stderr
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TEAM = ROOT / "plugins/software-engineering-team"
SHARED = ROOT / "platforms/shared/software-engineering-team/overlay"
sys.path[:0] = [str(TEAM / "scripts"), str(SHARED / "scripts")]
import companion_routing as routing
import setup_companions
import task_lane
import review_execution


class CompanionRoutingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.root, self.home, self.package = [self.directory / name for name in ("project", "home", "package")]
        (self.root / "workspace").mkdir(parents=True)
        (self.root / "workspace/config.json").write_text('{"team_id":"software-engineering-team"}')
        (self.package / "templates").mkdir(parents=True)
        (self.package / routing.POLICY).write_bytes((SHARED / routing.POLICY).read_bytes())
        (self.package / ".agent-marketplace-package.json").write_text('{"host":"claude"}')
        for rule in routing.policy(self.package)["routes"]:
            for relative in rule["source_paths"]:
                path = self.home / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("---\nname: graphify\n---\nUse the current graph.\n")
        home = mock.patch.object(Path, "home", return_value=self.home)
        home.start()
        self.addCleanup(home.stop)

    def test_project_only_routing_preserves_settings_and_uses_only_bundle_host(self):
        target = self.root / ".claude/settings.local.json"
        target.parent.mkdir()
        original = {"permissions": {"deny": ["Bash(secret)"]}, "skillOverrides": {"other": "off", "graphify": "on"}}
        target.write_text(json.dumps(original))
        global_settings = self.home / ".claude/settings.json"
        global_settings.write_text('{"untouched":true}')
        global_before = global_settings.read_bytes()
        proposed = routing.plan(self.root, "claude", self.package)
        after = json.loads(proposed["changes"][target])
        self.assertEqual(after["permissions"], original["permissions"])
        self.assertEqual(after["skillOverrides"], {"other": "off", "graphify": "user-invocable-only"})
        self.assertEqual(json.loads(target.read_text()), original)
        self.assertEqual(routing.apply(self.root, self.package), [".claude/settings.local.json"])
        self.assertTrue(routing.active(self.root, "claude", self.package))
        self.assertEqual(routing.apply(self.root, self.package), [])
        self.assertFalse((self.root / ".codex/config.toml").exists())
        self.assertEqual(global_before, global_settings.read_bytes())
        after["skillOverrides"]["graphify"] = "off"
        target.write_text(json.dumps(after))
        before = target.read_bytes()
        self.assertTrue(routing.active(self.root, "claude", self.package))
        self.assertEqual(routing.apply(self.root, self.package), [])
        self.assertEqual(before, target.read_bytes())
        (self.root / "workspace/config.json").write_text('{"team_id":"other"}')
        self.assertEqual(routing.plan(self.root, "claude", self.package)["changes"], {})

    def test_unsupported_native_catalog_routing_never_mutates_project_or_global_settings(self):
        (self.package / ".agent-marketplace-package.json").write_text('{"host":"codex"}')
        target = self.root / ".codex/config.toml"
        target.parent.mkdir()
        target.write_text('[[skills.config]]\nname="unrelated"\nenabled=false\n')
        before = target.read_bytes()
        self.assertEqual(routing.plan(self.root, "codex", self.package)["changes"], {})
        self.assertFalse(routing.active(self.root, "codex", self.package))
        self.assertEqual(routing.apply(self.root, self.package), [])
        self.assertEqual(target.read_bytes(), before)
        self.assertTrue(routing.source("codex", self.package).is_file())
        self.assertFalse((self.home / ".codex/skills/graphify/agents/openai.yaml").exists())

    def test_routing_rejects_alias_target_and_preserves_invalid_settings(self):
        folder = self.root / ".claude"
        folder.mkdir()
        target = folder / "settings.local.json"
        target.write_text('{"skillOverrides":[] }')
        before = target.read_bytes()
        with self.assertRaises(ValueError):
            routing.apply(self.root, self.package)
        self.assertEqual(before, target.read_bytes())
        target.unlink()
        external = self.directory / "external.json"
        external.write_text("{}")
        target.symlink_to(external)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            routing.plan(self.root, "claude", self.package)
        self.assertEqual(external.read_text(), "{}")

    def test_routed_graphify_is_available_without_native_skill_autoload(self):
        registry = setup_companions.load_registry(SHARED / "skill-content/setup/data/companions.json")
        companion = next(row for row in registry["companions"] if row["id"] == "graphify")
        registry = {"companions": [{**companion, "steps": [], "files": [], "env": []}]}
        with mock.patch.object(routing, "active", return_value=True), \
                mock.patch.object(setup_companions, "installed", return_value=True), \
                mock.patch.object(setup_companions, "codex_skills", return_value=({"graphify": False}, [])), \
                mock.patch.object(setup_companions.shutil, "which", return_value="/installed/graphify"):
            rows, findings = setup_companions.status(registry, self.root, ("codex",), verify_load=True)
        self.assertEqual(findings, [])
        self.assertEqual(rows[0]["availability_state"], "available")
        self.assertEqual(rows[0]["native_catalog_state"], "suppression_configured")
        self.assertTrue(rows[0]["capability_available"])
        self.assertIsNone(rows[0]["skills_available"])
        self.assertIsNone(rows[0]["enabled"])
        self.assertEqual(rows[0]["verification"], "local_routing_config_and_executable")

    def test_explicit_user_metadata_configuration_creates_only_graphify_policy_and_repeats_safely(self):
        with mock.patch.object(routing, "PACKAGE", self.package):
            result = setup_companions.configure_explicit_only()
            self.assertTrue(result["ok"])
            self.assertEqual(len(result["rows"]), 2)
            for row in result["rows"]:
                self.assertIsNone(row["before_sha256"])
                self.assertEqual(row["status"], "created")
                self.assertEqual(Path(row["path"]).read_bytes(), b"policy:\n  allow_implicit_invocation: false\n")
            repeated = setup_companions.configure_explicit_only()
            self.assertTrue(all(row["status"] == "unchanged" and row["before_sha256"] == row["after_sha256"]
                                for row in repeated["rows"]))
        self.assertFalse((self.home / ".codex/config.toml").exists())
        self.assertFalse((self.home / ".claude/skills/graphify/agents").exists())

    def test_explicit_configuration_preserves_existing_yaml_and_concurrent_author_files(self):
        with mock.patch.object(routing, "PACKAGE", self.package):
            sources = routing.sources("codex")
            target = sources[0].parent / "agents/openai.yaml"
            target.parent.mkdir()
            custom = b"interface:\n  display_name: Authored Graphify\n"
            target.write_bytes(custom)
            with self.assertRaisesRegex(ValueError, "preserved"):
                setup_companions.configure_explicit_only()
            self.assertEqual(target.read_bytes(), custom)
            self.assertFalse((sources[1].parent / "agents/openai.yaml").exists())

            expected = b"policy:\n  allow_implicit_invocation: false\n"
            authored = self.home / "authored.yaml"
            authored.write_bytes(expected)
            read_bytes = Path.read_bytes
            for replace_with_link in (False, True):
                target.unlink()
                target.write_bytes(expected)
                reads = 0

                def replace_after_preflight(path):
                    nonlocal reads
                    if path == target:
                        reads += 1
                        if reads == 2:
                            if replace_with_link:
                                path.unlink()
                                path.symlink_to(authored)
                            else:
                                path.write_bytes(custom)
                    return read_bytes(path)

                with self.subTest(symlink=replace_with_link), \
                        mock.patch.object(Path, "read_bytes", replace_after_preflight), \
                        self.assertRaisesRegex(ValueError, "changed concurrently"):
                    setup_companions.configure_explicit_only()
                self.assertEqual(target.is_symlink(), replace_with_link)
                self.assertEqual(target.read_bytes(), expected if replace_with_link else custom)
                self.assertFalse((sources[1].parent / "agents/openai.yaml").exists())
            target.unlink()
            original = setup_companions.os.link

            def race(source, destination):
                destination.write_bytes(custom)
                original(source, destination)

            with mock.patch.object(setup_companions.os, "link", side_effect=race), self.assertRaises(FileExistsError):
                setup_companions.configure_explicit_only()
            self.assertEqual(target.read_bytes(), custom)
            self.assertFalse((sources[1].parent / "agents/openai.yaml").exists())

    def test_user_metadata_writer_requires_exact_explicit_cli_selection(self):
        with mock.patch.object(setup_companions, "configure_explicit_only", return_value={"ok": True}) as writer, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            base = ["--project-root", str(self.root)]
            for args in (["check", "--explicit-only"], ["configure", "--all", "--explicit-only"],
                         ["configure", "--companion", "graphify", "--host", "claude", "--explicit-only"]):
                with self.subTest(args=args), self.assertRaises(SystemExit):
                    setup_companions.main([*args, *base])
            writer.assert_not_called()
            self.assertEqual(setup_companions.main(["configure", "--companion", "graphify", "--host", "codex",
                                                   "--explicit-only", *base]), 0)
            writer.assert_called_once_with()

    def test_session_registration_refreshes_only_catalog_policy_and_reports_restart(self):
        path = ROOT / "platforms/shared/_team/overlay/scripts/team_guard.py"
        spec = importlib.util.spec_from_file_location("routing_team_guard", path)
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        (self.root / ".git").mkdir()
        nested = self.root / "src"
        nested.mkdir()
        with mock.patch.object(guard, "__file__", str(self.package / "scripts/team_guard.py")), \
                mock.patch.dict(guard.os.environ, {"CLAUDE_PROJECT_DIR": str(self.directory / "other-project")}, clear=True), \
                mock.patch.object(routing, "apply", return_value=["local-settings"]) as apply:
            notice = guard.refresh_companion_routing({"cwd": str(nested)})
            apply.assert_called_once_with(self.root)
            self.assertIn("Start a new host session", notice["message"])
            apply.return_value = []
            self.assertIsNone(guard.refresh_companion_routing({"cwd": str(self.root)}))
            apply.side_effect = ValueError("invalid local settings")
            notice = guard.refresh_companion_routing({"cwd": str(self.root)})
            self.assertIn("pending", notice["context"])
            self.assertIn("invalid local settings", notice["message"])

    def test_graphify_short_entry_resolves_source_and_keeps_other_companions_explicit(self):
        task = self.root / task_lane.TASKS / "tk-001-change.md"
        task.parent.mkdir(parents=True)
        task.write_text("---\ntype: task\nid: TK-001\n---\n")
        args = SimpleNamespace(task="TK-001", name="graphify", host="claude", source=None,
                               unavailable=None, evidence=None, query=None)
        with mock.patch.object(task_lane, "SKILLS", SHARED / "skill-content"), \
                mock.patch.object(routing, "PACKAGE", self.package):
            result = task_lane.cmd_companion(args, self.root)
            self.assertIn("Use the current graph.", result["content"])
            self.assertEqual(result["source_path"], str(routing.source("claude", self.package).resolve()))
            args.seed, args.context = "quantity_label", "call"
            with self.assertRaisesRegex(task_lane.Refused, "require a Graphify --query"):
                task_lane.cmd_companion(args, self.root)
            args.query = "related callers"
            with mock.patch.object(review_execution, "run_graph", return_value={"ok": True}) as query:
                self.assertTrue(task_lane.cmd_companion(args, self.root)["ok"])
                self.assertEqual(query.call_args.kwargs, {"seed": "quantity_label", "context": "call"})
            args.name = "ponytail"
            with self.assertRaisesRegex(task_lane.Refused, "local SKILL.md"):
                task_lane.cmd_companion(args, self.root)


if __name__ == "__main__":
    unittest.main()
