from __future__ import annotations

import json
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
from git_fixture import init_repository  # noqa: E402

TEAM = ROOT / "plugins" / "software-engineering-team"
OVERLAY = ROOT / "platforms" / "shared" / "software-engineering-team" / "overlay"
REGISTRY = json.loads((OVERLAY / "skill-content" / "setup" / "data" / "companions.json").read_text(encoding="utf-8"))


class CompanionAvailabilityTests(unittest.TestCase):
    def setUp(self):
        sys.path[:0] = [str(OVERLAY / "scripts"), str(TEAM / "scripts")]
        import setup_companions
        self.module = setup_companions

    def test_plugin_record_must_be_installed_enabled_and_in_the_selected_catalog(self):
        verify = REGISTRY["companions"][0]["hosts"]["codex"]["verify"]
        self.assertEqual(verify["plugin_id"], "superpowers@openai-curated-remote")
        cases = [
            ({"pluginId": "superpowers@openai-curated", "installed": True, "enabled": True}, False),
            ({"pluginId": verify["plugin_id"], "installed": False, "enabled": True}, False),
            ({"pluginId": verify["plugin_id"], "installed": True, "enabled": False}, False),
            ({"pluginId": verify["plugin_id"], "installed": True, "enabled": True}, True),
        ]
        for record, expected in cases:
            with self.subTest(record=record), \
                    mock.patch.object(self.module.shutil, "which", return_value="codex"), \
                    mock.patch.object(self.module.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], 0, json.dumps({"installed": [record]}), "")):
                self.assertEqual(self.module.installed(verify, ROOT), expected)

    def test_claude_plugin_record_must_be_user_scoped_enabled_and_installed(self):
        verify = REGISTRY["companions"][0]["hosts"]["claude"]["verify"]
        with tempfile.TemporaryDirectory() as raw:
            good = {"id": verify["plugin_id"], "scope": "user", "enabled": True, "installPath": raw}
            cases = [
                ({**good, "scope": "project"}, False),
                ({**good, "enabled": False}, False),
                ({**good, "installPath": raw + "/missing"}, False),
                ({**good, "id": "other@market"}, False),
                (good, True),
            ]
            for record, expected in cases:
                with self.subTest(record=record), \
                        mock.patch.object(self.module.shutil, "which", return_value="claude"), \
                        mock.patch.object(self.module.subprocess, "run", return_value=subprocess.CompletedProcess(
                            [], 0, json.dumps([record]), "")):
                    self.assertEqual(self.module.installed(verify, ROOT), expected)

    def test_every_companion_installs_at_user_level_except_the_git_hook(self):
        specs = [(host["scope"], " ".join(host["install"])) for companion in REGISTRY["companions"]
                 for host in companion["hosts"].values()]
        self.assertEqual({scope for scope, _ in specs}, {"user"})
        self.assertFalse([command for _, command in specs if "--project" in command or "--scope project" in command])

    def test_claude_session_probe_reads_the_init_event_and_reports_a_dead_host(self):
        events = [{"type": "system", "subtype": "hook_started"}, {"type": "system", "subtype": "init", "skills": ["a:b"]}]
        process = mock.Mock(stdin=None, stdout=io.StringIO("".join(json.dumps(e) + "\n" for e in events)))
        with mock.patch.object(self.module.subprocess, "Popen", return_value=process):
            self.assertEqual(self.module.claude_skills(ROOT), ({"a:b"}, []))
        with mock.patch.object(self.module.subprocess, "Popen", side_effect=OSError):
            skills, findings = self.module.claude_skills(ROOT)
        self.assertIsNone(skills)
        self.assertIn("unverified", findings[0])

    def test_uninstall_runs_every_command_removes_paths_and_reports_what_is_left(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            (home / ".claude" / "skills" / "impeccable").mkdir(parents=True)
            (home / ".claude" / "skills" / "impeccable" / "SKILL.md").write_text("x", encoding="utf-8")
            ran, wanted = [], [c for companion in REGISTRY["companions"] for spec in
                               (*companion.get("steps", []), *companion["hosts"].values())
                               for c in spec.get("uninstall", [])]
            with mock.patch.object(self.module.Path, "home", return_value=home), \
                    mock.patch.object(self.module.shutil, "which", return_value="x"), \
                    mock.patch.object(self.module.subprocess, "run",
                                      side_effect=lambda argv, **_k: ran.append(" ".join(argv))
                                      or subprocess.CompletedProcess(argv, 0, "[]", "")), \
                    mock.patch.object(self.module, "present", side_effect=lambda verify, _root: verify.get("plugin_id") == "ponytail@ponytail"):
                rows = self.module.uninstall(REGISTRY, ROOT, ("claude", "codex"))
            self.assertEqual(sorted(ran), sorted(wanted))
            self.assertFalse((home / ".claude" / "skills" / "impeccable").exists())
            self.assertEqual({row["id"] for row in rows if not row["removed"]}, {"ponytail"})
            self.assertEqual([row["host"] for row in rows if row["id"] == "graphify"], ["git-hook", "claude", "codex", "cli"])

    def test_settings_cleanup_removes_only_what_apply_wrote(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / ".claude" / "settings.json"
            path.parent.mkdir()
            path.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"], "deny": ["Skill(superpowers:brainstorming)", "Skill(mine)"]},
                                        "env": {"SUPERPOWERS_DISABLE_TELEMETRY": "1", "OWN": "1"}, "model": "x"}), encoding="utf-8")
            self.module.remove_claude_settings(REGISTRY, root)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                             {"permissions": {"allow": ["Bash(ls)"], "deny": ["Skill(mine)"]}, "env": {"OWN": "1"}, "model": "x"})

    def test_host_skill_probe_checks_local_loading_and_reads_remote_contents(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            skill = root / "SKILL.md"
            skill.write_text("skill instructions", encoding="utf-8")
            remote = {"pluginId": "example@openai-curated-remote", "marketplaceName": "openai-curated-remote",
                      "source": {"source": "remote", "id": "opaque-plugin-id"}}
            registry = {"companions": [{"hosts": {"codex": {"verify": {
                "plugin_id": remote["pluginId"], "skills": ["example:review"]}}}}]}
            for content, expected in [(None, False), ("review instructions", True)]:
                responses = [{"id": 1, "result": {}}, {"id": 2, "result": {"data": [{"skills": [
                    {"name": "local", "enabled": True, "path": str(skill)},
                    {"name": "disabled", "enabled": False, "path": str(skill)}]}]}},
                    {"id": 3, "result": {"plugin": {"summary": {"installed": True, "enabled": True},
                        "skills": [{"name": "review", "enabled": True}]}}},
                    {"id": 4, "result": {"contents": content}}]
                process = mock.Mock(stdin=io.StringIO(), stdout=io.StringIO(
                    "".join(json.dumps(r) + "\n" for r in responses)))
                with mock.patch.object(self.module.subprocess, "Popen", return_value=process), \
                        mock.patch.object(self.module.subprocess, "run", return_value=subprocess.CompletedProcess(
                            [], 0, json.dumps({"installed": [remote]}), "")):
                    skills, findings = self.module.codex_skills(registry, root)
                self.assertEqual(findings, [])
                self.assertTrue(skills["local"])
                self.assertFalse(skills["disabled"])
                self.assertEqual(skills["example:review"], expected)

    def test_installed_plugin_with_missing_host_skills_fails_the_check(self):
        with mock.patch.object(self.module, "codex_skills", return_value=({}, [])), \
                mock.patch.object(self.module, "claude_skills", return_value=(set(), [])), \
                mock.patch.object(self.module, "installed", return_value=True), \
                mock.patch.object(self.module, "plugin_state", return_value=(True, True)), \
                mock.patch.object(self.module, "file_present", return_value=True), \
                mock.patch.object(self.module, "hook_missing", return_value=[]):
            rows, _ = self.module.status(REGISTRY, ROOT, ("codex",))
        self.assertTrue(all(row["installed"] for row in rows))
        self.assertTrue(all(row["skills_available"] is False for row in rows if row["host"] == "codex"))

    def test_codex_cli_inside_the_desktop_app_is_found_without_path(self):
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin"}), \
                mock.patch.object(self.module.shutil, "which", return_value=None), \
                mock.patch.object(self.module.Path, "is_file", return_value=True):
            self.module.find_codex_cli()
            self.assertTrue(os.environ["PATH"].startswith("/Applications/ChatGPT.app/"))

    def test_unavailable_host_never_reports_verified_skills(self):
        with mock.patch.object(self.module.subprocess, "run", side_effect=OSError):
            skills, findings = self.module.codex_skills(REGISTRY, ROOT)
        self.assertFalse(skills)
        self.assertIn("unverified", findings[0])


class GraphifyHookEnvTests(unittest.TestCase):
    def test_apply_puts_the_output_folder_into_the_hook_once_and_check_reports_a_bare_hook(self):
        sys.path[:0] = [str(OVERLAY / "scripts"), str(TEAM / "scripts")]
        import setup_companions

        def git(argv, **_kwargs):
            # Only the hooks folder lookup answers; every other probe finds nothing installed.
            found = argv[:3] == ["git", "rev-parse", "--git-path"]
            return subprocess.CompletedProcess(argv, 0 if found else 1, ".git/hooks\n" if found else "", "")
        with tempfile.TemporaryDirectory() as raw, \
                mock.patch.object(setup_companions.subprocess, "run", side_effect=git), \
                mock.patch.object(setup_companions.shutil, "which", return_value="/bin/git"):
            root = Path(raw)
            (root / ".git" / "hooks").mkdir(parents=True)
            (root / ".gitattributes").write_text("graph.json merge=graphify\n", encoding="utf-8")
            hook = root / ".git" / "hooks" / "post-commit"
            hook.write_text("#!/bin/sh\n# graphify-hook-start\ngraphify update .\n", encoding="utf-8")
            line = "export GRAPHIFY_OUT=.agentrof/graphify"
            _rows, findings = setup_companions.status(REGISTRY, root, ())
            self.assertEqual([item for item in findings if "post-commit" in item],
                             [f"graphify: hook post-commit lacks `{line}`; rerun companions apply"])
            for _ in range(2):
                setup_companions.write_hook_env(REGISTRY, root)
            self.assertEqual(hook.read_text(encoding="utf-8").splitlines()[:2], ["#!/bin/sh", line])
            self.assertEqual(hook.read_text(encoding="utf-8").count(line), 1)
            self.assertEqual(setup_companions.status(REGISTRY, root, ())[1], [])


@integration
class CompanionCheckTests(unittest.TestCase):
    def test_check_and_apply_against_fake_host_clis(self):
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw)
            project, fake, codex_home = temporary / "project", temporary / "bin", temporary / "codex"
            for path in (project, fake, codex_home):
                path.mkdir()
            ids = [spec["verify"]["plugin_id"] for companion in REGISTRY["companions"]
                   for spec in companion["hosts"].values() if "plugin_id" in spec["verify"]]
            listings = {"claude": json.dumps([{"id": value, "scope": "user", "enabled": True, "installPath": str(temporary)} for value in ids]),
                        "codex": json.dumps({"installed": [{"pluginId": value, "installed": True, "enabled": True} for value in ids]})}
            listings["graphify"] = "post-commit: installed"
            session_skills = [name for companion in REGISTRY["companions"]
                              for name in companion["hosts"]["codex"]["verify"].get("skills", [])]
            init = json.dumps({"type": "system", "subtype": "init", "skills": session_skills})
            for name, listing in listings.items():
                tool = fake / name
                body = (f"if [ \"$1\" = plugin ]; then echo '{listings['claude']}'; else echo '{init}'; fi"
                        if name == "claude" else f"echo '{listing}'")
                tool.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
                tool.chmod(0o755)
            loaded = project / "loaded.md"
            loaded.write_text("instructions", encoding="utf-8")
            skills = [{"name": name, "enabled": True, "path": str(loaded)}
                      for companion in REGISTRY["companions"]
                      for name in companion["hosts"]["codex"]["verify"]["skills"]]
            (fake / "codex").write_text(
                "#!/usr/bin/env python3\nimport json, sys\n"
                "if sys.argv[1] != 'app-server':\n"
                + " print(" + repr(listings["codex"]) + ")\n"
                + "else:\n for line in sys.stdin:\n  request=json.loads(line)\n"
                + "  if 'id' in request:\n"
                + "   result=" + repr({"data": [{"skills": skills}]}) + " if request['method']=='skills/list' else {}\n"
                + "   print(json.dumps({'id':request['id'],'result':result}),flush=True)\n", encoding="utf-8")
            env = {**os.environ, "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}",
                   "CODEX_HOME": str(codex_home), "HOME": str(temporary),
                   "PYTHONPATH": str(TEAM / "scripts")}

            def run(*args: str) -> subprocess.CompletedProcess:
                return subprocess.run(
                    [sys.executable, str(OVERLAY / "scripts" / "setup_companions.py"), *args,
                     "--project-root", str(project)],
                    env=env, capture_output=True, text=True, check=False)

            first = run("check")
            self.assertEqual(first.returncode, 1, first.stdout)
            self.assertIn("superpowers | plugin | claude | yes | yes | 4/4 | NO | NO | -", first.stdout)

            for companion in REGISTRY["companions"]:
                for spec in companion["hosts"].values():
                    if spec["verify"]["kind"] == "path_exists":
                        skill = (temporary if spec["verify"].get("base") else project) / spec["verify"]["path"]
                        skill.parent.mkdir(parents=True, exist_ok=True)
                        skill.write_text("x\n", encoding="utf-8")
            settings = project / ".claude" / "settings.json"
            settings.parent.mkdir(exist_ok=True)
            settings.write_text('{"permissions": {"allow": ["Bash(ls)"]}}\n', encoding="utf-8")
            ignore = project / "workspace" / "docs" / ".graphifyignore"
            ignore.parent.mkdir(parents=True)
            ignore.write_text("own\n", encoding="utf-8")
            self.assertEqual(run("apply").returncode, 0)
            self.assertEqual(ignore.read_text(encoding="utf-8"), "own\n")
            ignore.unlink()
            self.assertEqual(run("apply").returncode, 0)
            written = {path: path.read_bytes() for path in (
                settings, codex_home / "config.toml", project / ".diagram-design",
                project / ".impeccable" / "config.json", ignore)}
            self.assertEqual(run("apply").returncode, 0)
            self.assertEqual(written, {path: path.read_bytes() for path in written})
            merged = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(merged["permissions"]["allow"], ["Bash(ls)"])
            self.assertIn("Skill(superpowers:brainstorming)", merged["permissions"]["deny"])
            self.assertEqual(merged["env"]["IMPECCABLE_CONTEXT_DIR"], "workspace/experience-design")
            self.assertIn("graphify | hook | git-hook | yes", run("check").stdout)

            (project / "DESIGN.md").write_text("x\n", encoding="utf-8")
            last = run("check", "--json")
            self.assertEqual(last.returncode, 1)
            self.assertEqual(len(json.loads(last.stdout)["findings"]), 1)

@integration
class GraphifyRefreshTests(unittest.TestCase):
    def test_refresh_extracts_only_after_a_docs_markdown_change(self):
        with tempfile.TemporaryDirectory() as raw:
            project, fake = Path(raw) / "project", Path(raw) / "bin"
            fake.mkdir()
            (project / "workspace" / "docs").mkdir(parents=True)
            graph = project / ".agentrof" / "graphify" / "docs"
            graph.mkdir(parents=True)
            (graph / "graph.json").write_text("{}\n", encoding="utf-8")
            tool = fake / "graphify"
            tool.write_text('#!/bin/sh\necho "fake $* out=$GRAPHIFY_OUT"\n', encoding="utf-8")
            tool.chmod(0o755)

            def git(*args: str) -> str:
                return subprocess.run(["git", "-C", str(project), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()

            init_repository(project)
            git("-c", "user.name=t", "-c", "user.email=t@example.invalid",
                "commit", "-q", "--allow-empty", "-m", "base")
            (project / "workspace" / "docs" / "a.md").write_text("x\n", encoding="utf-8")
            (project / "README.txt").write_text("x\n", encoding="utf-8")
            git("add", "workspace", "README.txt")
            git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "docs")
            env = {**os.environ, "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}",
                   "PYTHONPATH": str(TEAM / "scripts")}
            env.pop("GRAPHIFY_OUT", None)

            def refresh(commit_range: str) -> str:
                result = subprocess.run(
                    [sys.executable, str(OVERLAY / "scripts" / "graphify_refresh.py"), "--wait",
                     "--project-root", str(project), "--range", commit_range],
                    env=env, capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                return (project / ".agentrof" / "graphify" / "refresh.log").read_text(encoding="utf-8")

            log = refresh("HEAD~1..HEAD")
            self.assertIn(f"fake extract workspace/docs --backend claude-cli out={graph.resolve()}", log)
            self.assertIn("done: graphify exit 0", log)
            self.assertIn("skip: no Markdown", refresh("HEAD..HEAD"))


if __name__ == "__main__":
    unittest.main()
