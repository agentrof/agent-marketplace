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
            for name, listing in listings.items():
                tool = fake / name
                tool.write_text(f"#!/bin/sh\necho '{listing}'\n", encoding="utf-8")
                tool.chmod(0o755)
            env = {**os.environ, "PATH": f"{fake}{os.pathsep}{os.environ['PATH']}",
                   "CODEX_HOME": str(codex_home),
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
                        skill = project / spec["verify"]["path"]
                        skill.parent.mkdir(parents=True, exist_ok=True)
                        skill.write_text("x\n", encoding="utf-8")
            settings = project / ".claude" / "settings.json"
            settings.write_text('{"permissions": {"allow": ["Bash(ls)"]}}\n', encoding="utf-8")
            self.assertEqual(run("apply").returncode, 0)
            written = {path: path.read_bytes() for path in (
                settings, codex_home / "config.toml", project / ".diagram-design",
                project / ".impeccable" / "config.json")}
            self.assertEqual(run("apply").returncode, 0)
            self.assertEqual(written, {path: path.read_bytes() for path in written})
            merged = json.loads(settings.read_text(encoding="utf-8"))
            self.assertEqual(merged["permissions"]["allow"], ["Bash(ls)"])
            self.assertIn("Skill(superpowers:brainstorming)", merged["permissions"]["deny"])
            self.assertEqual(merged["env"]["IMPECCABLE_CONTEXT_DIR"], "workspace/experience-design")

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
                    (project / verify["path"]).parent.mkdir(parents=True, exist_ok=True)
                    (project / verify["path"]).write_text("x\n", encoding="utf-8")
            env = {**os.environ, "PATH": str(empty), "CODEX_HOME": str(Path(raw) / "codex"),
                   "PYTHONPATH": str(TEAM / "scripts")}
            script = str(OVERLAY / "scripts" / "setup_companions.py")
            applied = subprocess.run(
                [sys.executable, script, "apply", "--host", "codex", "--project-root", str(project)],
                env=env, capture_output=True, text=True, check=False)
            self.assertEqual(applied.returncode, 1)
            self.assertNotIn("RUN", applied.stdout)
            self.assertIn("APP   Codex CLI not found: install plugin superpowers from marketplace"
                          " openai-curated in the Codex app via /plugins", applied.stdout)
            checked = subprocess.run(
                [sys.executable, script, "check", "--host", "codex", "--project-root", str(project)],
                env=env, capture_output=True, text=True, check=False)
            self.assertIn("superpowers | codex | NO", checked.stdout)


if __name__ == "__main__":
    unittest.main()
