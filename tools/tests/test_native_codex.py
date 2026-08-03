"""Native Codex packaging and project-agent behavior."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GENERATOR = (
    REPO / "plugins" / "software-engineering-team" / "scripts"
    / "generate_codex_project.py"
)


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_codex_project", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


generate = load_generator()


class CodexPackageContractTests(unittest.TestCase):
    def test_marketplace_manifest_versions_policies_and_skill_visibility(self):
        marketplace = json.loads(
            (REPO / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"))
        entries = {entry["name"]: entry for entry in marketplace["plugins"]}
        self.assertEqual(marketplace["interface"]["displayName"],
                         "Agent Marketplace")
        self.assertEqual(
            entries["project-management-office"]["policy"],
            {"installation": "INSTALLED_BY_DEFAULT", "authentication": "ON_INSTALL"})
        self.assertEqual(
            entries["software-engineering-team"]["policy"],
            {"installation": "AVAILABLE", "authentication": "ON_INSTALL"})
        for name, entry in entries.items():
            self.assertEqual(entry["category"], "Engineering")
            source = REPO / "plugins" / name
            archive = REPO / "codex-plugins" / name
            manifests = [
                json.loads((source / ".claude-plugin" / "plugin.json").read_text()),
                json.loads((source / "codex" / "plugin.json").read_text()),
                json.loads((archive / ".codex-plugin" / "plugin.json").read_text()),
            ]
            self.assertEqual({m["version"] for m in manifests}, {manifests[0]["version"]})
            self.assertEqual({m["name"] for m in manifests}, {name})
            interface = manifests[1]["interface"]
            self.assertEqual(
                interface["displayName"], name.replace("-", " ").title())
            self.assertEqual(interface["developerName"], "Agentrof")
            self.assertEqual(interface["category"], "Engineering")
            self.assertEqual(interface["capabilities"], ["Read", "Write", "Interactive"])
            canonical = {
                path.name for path in (source / "skill-content").iterdir()
                if path.is_dir()
            }
            claude = {
                path.parent.name
                for path in (source / "claude-skills").glob("*/SKILL.md")
            }
            self.assertEqual(claude, canonical)
            codex = {
                path.parent.name for path in (source / "codex-skills").glob("*/SKILL.md")
            }
            archived = {
                path.parent.name for path in (archive / "skills").glob("*/SKILL.md")
            }
            self.assertEqual(codex, archived)
            for skill_name in codex:
                policy = source / "codex-skills" / skill_name / "agents" / "openai.yaml"
                metadata = policy.read_text(encoding="utf-8")
                self.assertIn("allow_implicit_invocation: false", metadata)
                self.assertIn(f"${name}:{skill_name}", metadata)


def agent(name: str, model: str, tools: str = "") -> str:
    tools_line = f"tools: {tools}\n" if tools else ""
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {name} role.\n"
        f"model: {model}\n"
        "output_contract: prose\n"
        f"{tools_line}"
        "---\n\n"
        f"# {name}\n\n## Constitution\nKeep the contract.\n\n"
        "## Output Contract\nReturn evidence.\n"
    )


class CodexProjectGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.plugin = root / "plugin"
        (self.plugin / "agents").mkdir(parents=True)
        (self.plugin / "templates").mkdir()
        (self.plugin / "templates" / "AGENTS.md").write_text(
            "# Team\n\nRead {{workspace}}/memory/me.md.\n", encoding="utf-8")
        for name, model, tools in (
            ("architect", "opus", "Read, Grep, Glob"),
            ("developer", "sonnet", ""),
            ("scanner", "haiku", "Read"),
            ("delegate", "inherit", ""),
        ):
            (self.plugin / "agents" / f"{name}.md").write_text(
                agent(name, model, tools), encoding="utf-8")
        self.project = root / "project"
        (self.project / ".git").mkdir(parents=True)
        (self.project / "AGENTS.md").write_text(
            "# User instructions\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_reasoning_sandbox_contract_and_idempotency(self):
        written = generate.materialize(self.project, self.plugin, "work")
        self.assertEqual(len(written), 5)
        agents = self.project / ".codex" / "agents"
        architect = (agents / "architect.toml").read_text(encoding="utf-8")
        developer = (agents / "developer.toml").read_text(encoding="utf-8")
        scanner = (agents / "scanner.toml").read_text(encoding="utf-8")
        delegate = (agents / "delegate.toml").read_text(encoding="utf-8")
        self.assertIn('model_reasoning_effort = "high"', architect)
        self.assertIn('sandbox_mode = "read-only"', architect)
        self.assertIn('model_reasoning_effort = "medium"', developer)
        self.assertNotIn("sandbox_mode", developer)
        self.assertIn('model_reasoning_effort = "low"', scanner)
        self.assertNotIn("model_reasoning_effort", delegate)
        self.assertNotIn("model = ", "\n".join((architect, developer, scanner, delegate)))
        self.assertIn("## Constitution", architect)
        self.assertIn("## Output Contract", architect)
        agents_md = (self.project / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("# User instructions", agents_md)
        self.assertIn("Read work/memory/me.md", agents_md)
        self.assertEqual(generate.materialize(self.project, self.plugin, "work"), [])

    def test_only_owned_stale_files_are_removed(self):
        generate.materialize(self.project, self.plugin, "workspace")
        agents = self.project / ".codex" / "agents"
        stale = agents / "team-architect.toml"
        stale.write_text(generate.OWNER + "\nname = \"team-architect\"\n",
                         encoding="utf-8")
        foreign = agents / "foreign.toml"
        foreign.write_text('name = "foreign"\n', encoding="utf-8")
        changed = generate.materialize(self.project, self.plugin, "workspace")
        self.assertIn(stale, changed)
        self.assertFalse(stale.exists())
        self.assertTrue(foreign.exists())

    def test_unmanaged_collision_aborts_before_any_write(self):
        agents = self.project / ".codex" / "agents"
        agents.mkdir(parents=True)
        collision = agents / "architect.toml"
        collision.write_text('name = "mine"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unmanaged Codex agent collision"):
            generate.materialize(self.project, self.plugin, "workspace")
        self.assertEqual(collision.read_text(encoding="utf-8"), 'name = "mine"\n')
        self.assertFalse((agents / "developer.toml").exists())


if __name__ == "__main__":
    unittest.main()
