"""Purpose classes: the model and effort of each spawn.

`scripts/model_class.py resolve` picks the class slot of one spawn: the
role's own class, or the class the step names, one level up for a task of
size `l`, at rigor `high` or on a risk flag when the class escalates. The
slot's model and effort come from the package class map and the project's
overrides. Claude takes the family alias and effort at spawn time; Codex
runs each role's own pins. Rendered Claude roles leave
the CLAUDE.md chain out and deny the tools a role never needs.
"""

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

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parents[1]
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))
sys.path.insert(0, str(ROOT / "plugins" / "software-engineering-team" / "scripts"))

import build_distributions  # noqa: E402
import fixtures  # noqa: E402
import model_class as resolver  # noqa: E402
import role_settings  # noqa: E402
import task_lane  # noqa: E402

PLUGIN = fixtures.PLUGIN
SOURCE = ROOT / "plugins" / PLUGIN
TRIGGERS = ({"size": "l"}, {"rigor": "high"}, {"risks": ["auth"]})
AGENT_TOOLS = json.loads((ROOT / "platforms/claude/agent-tools.json").read_text(encoding="utf-8"))


class ResolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.map = build_distributions.class_map(ROOT, SOURCE, build_distributions.load_adapters(ROOT))

    def resolve(self, host: str, model_class: str | None = None, config: dict | None = None,
                **facts) -> dict:
        return resolver.resolve(self.map, config or {}, host, model_class, **facts)

    def test_each_class_escalates_one_level_up_and_a_fixed_class_never_moves(self):
        spec = self.map["hosts"]["claude"]
        for name, declared in self.map["model_classes"].items():
            calm = self.resolve("claude", name, size="s", rigor="normal")
            self.assertEqual((calm["slot"], calm["escalated"]), (name, False), name)
            for facts in TRIGGERS:
                with self.subTest(model_class=name, facts=facts):
                    result = self.resolve("claude", name, **facts)
                    slot = f"{name}_escalated" if declared["escalates"] else name
                    self.assertEqual((result["class"], result["slot"], result["escalated"]),
                                     (name, slot, declared["escalates"]))
                    setting = spec["classes"][slot]
                    self.assertEqual((result["model"], result["effort"]),
                                     (setting["model"], setting["effort"]))
                    # Claude sets both at spawn time, the model by its family alias.
                    self.assertEqual(result["spawn"], {
                        "model": spec["models"][setting["model"]]["family"],
                        "effort": setting["effort"]})

    def test_a_role_runs_its_own_class_unless_the_step_names_another(self):
        own = self.resolve("claude", role="code-reviewer")
        self.assertEqual((own["class"], own["agent"]), ("routine_review", "code-reviewer"))
        security = self.resolve("claude", "security_review", role="code-reviewer", size="l")
        self.assertEqual((security["slot"], security["spawn"]),
                         ("security_review", {"model": "opus", "effort": "high"}))

    def test_project_overrides_take_precedence_over_the_package(self):
        config = {"class_models": {"claude": {"routine_review": {"effort": "low"},
                                              "critical_decision_escalated": {"effort": "max"}}},
                  "role_classes": {"business-analyst": "critical_decision"}}
        self.assertEqual(role_settings.config_errors(config, self.map), [])
        reviewer = self.resolve("claude", role="code-reviewer", config=config)
        self.assertEqual((reviewer["model"], reviewer["effort"]), ("claude-sonnet-5-5", "low"))
        moved = self.resolve("claude", role="business-analyst", config=config)
        self.assertEqual((moved["class"], moved["spawn"]),
                         ("critical_decision", {"model": "opus", "effort": "high"}))
        escalated = self.resolve("claude", role="business-analyst", config=config, rigor="high")
        self.assertEqual((escalated["slot"], escalated["effort"]),
                         ("critical_decision_escalated", "max"))
        # A step's own class beats the role's moved class.
        named = self.resolve("claude", "mechanical", role="business-analyst", config=config)
        self.assertEqual(named["slot"], "mechanical")
        # `session` leaves the model to the session and keeps the effort.
        session = {"class_models": {"claude": {"mechanical": {"model": "session"}}}}
        self.assertEqual(self.resolve("claude", "mechanical", config=session)["spawn"],
                         {"effort": "high"})

    def test_unknown_inputs_are_refused(self):
        for kwargs, fragment in (
                ({"model_class": "frontier"}, "unknown class 'frontier'"),
                ({"role": "ghost"}, "unknown role 'ghost'"),
                ({"role": "code-reviewer", "risks": ["weather"]}, "unknown risk flag weather"),
                ({"role": "code-reviewer", "size": "xl"}, "size must be one of"),
                ({}, "name a --class, a --role or both")):
            with self.subTest(fragment=fragment):
                facts = dict(kwargs)
                name = facts.pop("model_class", None)
                with self.assertRaisesRegex(ValueError, fragment):
                    resolver.resolve(self.map, {}, "claude", name, **facts)


@integration
class ResolverCommandTests(unittest.TestCase):
    """The installed command reads the project config and the task file."""

    def run_resolve(self, project: Path, *args: str) -> subprocess.CompletedProcess:
        script = ROOT / "dist/claude" / PLUGIN / "scripts/model_class.py"
        return subprocess.run(
            [sys.executable, str(script), "resolve", "--project-root", str(project), *args],
            capture_output=True, text=True, check=False, timeout=60,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))

    def test_the_command_resolves_from_the_config_and_the_task(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            config = project / "workspace/config.json"
            config.parent.mkdir(parents=True)
            base = {"schema_version": 2, "team_id": PLUGIN, "output_language": "English",
                    "terminology_language": "English"}
            config.write_text(json.dumps(base), encoding="utf-8")
            result = self.run_resolve(project, "--role", "code-reviewer", "--host", "claude",
                                      "--risk", "migration", "--json")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["spawn"], {"model": "sonnet", "effort": "xhigh"})
            # A fast-lane task's size escalates the spawn.
            task = project / task_lane.TASKS / "tk-001-fixture.md"
            task.parent.mkdir(parents=True)
            task.write_text("---\nid: TK-001\nsize: l\n---\n", encoding="utf-8")
            sized = self.run_resolve(project, "--role", "solution-architect", "--host", "claude",
                                     "--task", "TK-001", "--json")
            self.assertEqual(sized.returncode, 0, sized.stderr)
            self.assertEqual(json.loads(sized.stdout)["slot"], "critical_decision_escalated")
            # So does a risk flag the task file records.
            task.write_text("---\nid: TK-001\nsize: s\nrisks: migration\n---\n", encoding="utf-8")
            flagged = self.run_resolve(project, "--role", "solution-architect", "--host", "claude",
                                       "--task", "TK-001", "--json")
            self.assertEqual(json.loads(flagged.stdout)["slot"], "critical_decision_escalated", flagged.stderr)


class RenderedClaudeAgentTests(unittest.TestCase):
    """Rendered Claude roles carry omitClaudeMd and the tools they never need."""

    @classmethod
    def setUpClass(cls) -> None:
        # The committed build, which `make dist-check` keeps equal to a fresh one.
        cls.agents = {path.stem: build_distributions.parse_frontmatter(path)[0]
                      for path in sorted((ROOT / "dist/claude" / PLUGIN / "agents").glob("*.md"))}

    def test_every_role_leaves_the_claude_md_chain_out(self):
        for name, fields in self.agents.items():
            with self.subTest(agent=name):
                self.assertEqual(str(fields.get("omitClaudeMd")).lower(), "true")
                self.assertNotIn("model_class", fields)

    def test_a_role_without_its_own_tool_list_denies_the_tools_it_never_needs(self):
        listed = {name for name, fields in self.agents.items() if "tools" in fields}
        self.assertTrue(listed, "no read-only role keeps its own tool list")
        browser = set(AGENT_TOOLS["browser_agents"])
        for name, fields in self.agents.items():
            with self.subTest(agent=name):
                if name in listed:
                    self.assertNotIn("disallowedTools", fields)
                    continue
                denied = [tool.strip() for tool in str(fields["disallowedTools"]).split(",")]
                self.assertTrue(set(AGENT_TOOLS["disallowed"]) <= set(denied))
                base = name if name in browser else next(
                    (agent for agent in browser if name.startswith(f"{agent}-")), None)
                if base is not None:
                    # A browser role keeps the preview browser.
                    self.assertNotIn("mcp__*", denied)
                    self.assertEqual(denied[-len(AGENT_TOOLS["browser_disallowed_mcp"]):],
                                     AGENT_TOOLS["browser_disallowed_mcp"])
                else:
                    self.assertEqual(denied[-1], "mcp__*")
                self.assertIn("Agent", denied)


if __name__ == "__main__":
    unittest.main()
