from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import build_distributions  # noqa: E402
import counts  # noqa: E402

TEAM = ROOT / "plugins" / "software-engineering-team"
OVERLAY = ROOT / "platforms" / "shared" / "software-engineering-team" / "overlay"
REGISTRY = json.loads((OVERLAY / "skill-content" / "setup" / "data" / "companions.json").read_text(encoding="utf-8"))


class CompanionRegistryTests(unittest.TestCase):
    def test_registry_template_table_and_readme_agree(self):
        section = "\n".join(build_distributions.companion_section(TEAM))
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(counts.render_companions(ROOT), readme)
        for host, name in (("claude", "CLAUDE.md"), ("codex", "AGENTS.override.md")):
            generated = (ROOT / "dist" / host / TEAM.name / "templates" / name).read_text(encoding="utf-8")
            self.assertIn(section, generated)
        self.assertIn(REGISTRY["precedence"], section)
        for companion in REGISTRY["companions"]:
            self.assertIn(f"### {companion['id']}", readme)
            for row in companion["routing"]:
                self.assertIn(f"| {row['step']} | {row['role']} | {row['skill']} | {row['when']} |", section)
            for skill in companion.get("blocked_skills", []):
                self.assertIn(f"`{skill}`", readme)
            for spec in companion["hosts"].values():
                for command in spec["install"]:
                    self.assertIn(f"`{command}`", readme)


@integration
class GraphifyHookEnvTests(unittest.TestCase):
    def test_apply_puts_the_output_folder_into_the_hook_once_and_check_reports_a_bare_hook(self):
        sys.path[:0] = [str(OVERLAY / "scripts"), str(TEAM / "scripts")]
        import setup_companions
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
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
            listings = {"claude": json.dumps([{"id": value} for value in ids]),
                        "codex": json.dumps({"installed": [{"pluginId": value} for value in ids]})}
            listings["graphify"] = "post-commit: installed"
            for name, listing in listings.items():
                tool = fake / name
                tool.write_text(f"#!/bin/sh\necho '{listing}'\n", encoding="utf-8")
                tool.chmod(0o755)
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
            self.assertIn("superpowers | claude | yes | NO | NO | -", first.stdout)

            for companion in REGISTRY["companions"]:
                for spec in companion["hosts"].values():
                    if spec["verify"]["kind"] == "path_exists":
                        skill = (temporary if spec["verify"].get("base") else project) / spec["verify"]["path"]
                        skill.parent.mkdir(parents=True, exist_ok=True)
                        skill.write_text("x\n", encoding="utf-8")
            settings = project / ".claude" / "settings.json"
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
            self.assertIn("graphify | git-hook | yes", run("check").stdout)

            (project / "DESIGN.md").write_text("x\n", encoding="utf-8")
            last = run("check", "--json")
            self.assertEqual(last.returncode, 1)
            self.assertEqual(len(json.loads(last.stdout)["findings"]), 1)

    def test_apply_without_codex_cli_names_the_app_step(self):
        with tempfile.TemporaryDirectory() as raw:
            project, empty = Path(raw) / "project", Path(raw) / "bin"
            project.mkdir()
            empty.mkdir()
            for companion in REGISTRY["companions"]:
                verify = companion["hosts"]["codex"]["verify"]
                if verify["kind"] == "path_exists":
                    skill = (Path(raw) if verify.get("base") else project) / verify["path"]
                    skill.parent.mkdir(parents=True, exist_ok=True)
                    skill.write_text("x\n", encoding="utf-8")
            env = {**os.environ, "PATH": str(empty), "CODEX_HOME": str(Path(raw) / "codex"), "HOME": raw,
                   "PYTHONPATH": str(TEAM / "scripts")}
            script = str(OVERLAY / "scripts" / "setup_companions.py")
            applied = subprocess.run(
                [sys.executable, script, "apply", "--host", "codex", "--project-root", str(project)],
                env=env, capture_output=True, text=True, check=False)
            self.assertEqual(applied.returncode, 1)
            self.assertNotIn("RUN   codex", applied.stdout)
            self.assertIn("APP   uv not found", applied.stdout)
            self.assertIn("NOTE  Codex runs the ponytail hooks only after", applied.stdout)
            self.assertIn("APP   Codex CLI not found: install plugin superpowers from marketplace"
                          " openai-curated in the Codex app via /plugins", applied.stdout)
            checked = subprocess.run(
                [sys.executable, script, "check", "--host", "codex", "--project-root", str(project)],
                env=env, capture_output=True, text=True, check=False)
            self.assertIn("superpowers | codex | NO", checked.stdout)


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

            git("init", "-q")
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
