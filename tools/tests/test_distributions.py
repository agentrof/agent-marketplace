"""Symmetric host distributions and Codex project-agent behavior."""

from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import build_distributions  # noqa: E402
GENERATOR = (
    REPO / "dist" / "codex" / "software-engineering-team" / "scripts"
    / "generate_codex_project.py"
)


def load_generator():
    spec = importlib.util.spec_from_file_location("generate_codex_project", GENERATOR)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


generate = load_generator()


class DistributionContractTests(unittest.TestCase):
    def test_every_team_receives_shared_runtime_and_pmo_registry(self):
        teams = sorted(
            path.name for path in (REPO / "plugins").iterdir()
            if path.is_dir() and path.name != "project-management-office"
        )
        for team in teams:
            for host in ("claude", "codex"):
                scripts = REPO / "dist" / host / team / "scripts"
                self.assertTrue((scripts / "team_guard.py").is_file())
            self.assertTrue((
                REPO / "dist" / "codex" / team / "scripts"
                / "generate_codex_project.py"
            ).is_file())
        for host in ("claude", "codex"):
            registry = json.loads((
                REPO / "dist" / host / "project-management-office"
                / "team_plugins.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual(registry, {"schema_version": 1, "plugins": teams})

    def test_marketplace_manifest_versions_policies_and_skill_visibility(self):
        versions = json.loads((REPO / "versions.json").read_text(encoding="utf-8"))
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
            claude_archive = REPO / "dist" / "claude" / name
            codex_archive = REPO / "dist" / "codex" / name
            manifests = [
                json.loads((REPO / "platforms" / "claude" / name
                            / "manifest.json").read_text()),
                json.loads((REPO / "platforms" / "codex" / name
                            / "manifest.json").read_text()),
                json.loads((claude_archive / ".claude-plugin"
                            / "plugin.json").read_text()),
                json.loads((codex_archive / ".codex-plugin"
                            / "plugin.json").read_text()),
            ]
            self.assertEqual(
                {m["version"] for m in manifests},
                {versions["plugins"][name]},
            )
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
                for path in (claude_archive / "skills").glob("*/SKILL.md")
            }
            self.assertEqual(claude, canonical)
            codex = {
                path.parent.name for path in (codex_archive / "skills").glob("*/SKILL.md")
            }
            entries_only = {
                path.name for path in (source / "skill-content").iterdir()
                if path.is_dir() and "exposure: entry" in
                (path / "SKILL.md").read_text(encoding="utf-8")
            }
            self.assertEqual(codex, entries_only)
            for skill_name in codex:
                policy = (codex_archive / "skills" / skill_name / "agents"
                          / "openai.yaml")
                metadata = policy.read_text(encoding="utf-8")
                self.assertIn("allow_implicit_invocation: false", metadata)
                self.assertIn(f"${name}:{skill_name}", metadata)

    def test_generated_distributions_are_current(self):
        self.assertEqual(
            build_distributions.check(REPO, REPO / "dist"),
            [],
        )

    def test_python_runtime_caches_never_enter_or_dirty_distributions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            overlay = root / "overlay"
            target = root / "target"
            (source / "scripts" / "__pycache__").mkdir(parents=True)
            (source / "scripts" / "tool.py").write_text("print(1)\n")
            (source / "scripts" / "__pycache__" / "tool.pyc").write_bytes(b"cache")
            build_distributions.copy_canonical(source, target)
            self.assertTrue((target / "scripts" / "tool.py").is_file())
            self.assertFalse((target / "scripts" / "__pycache__").exists())

            (overlay / "__pycache__").mkdir(parents=True)
            (overlay / "hook.py").write_text("print(2)\n")
            (overlay / "__pycache__" / "hook.pyc").write_bytes(b"cache")
            build_distributions.copy_overlay(overlay, target)
            self.assertTrue((target / "hook.py").is_file())
            self.assertFalse((target / "__pycache__").exists())

            expected = root / "expected"
            actual = root / "actual"
            expected.mkdir()
            (actual / "__pycache__").mkdir(parents=True)
            (actual / "__pycache__" / "runtime.pyc").write_bytes(b"cache")
            self.assertEqual(build_distributions.compare_dirs(expected, actual), [])

    def test_canonical_source_rejects_unknown_top_level_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "plugins" / "sample-team"
            plugin.mkdir(parents=True)
            (plugin / "unexpected-surface").mkdir()
            with self.assertRaisesRegex(
                    ValueError, "unsupported canonical top-level entry"):
                build_distributions.validate_canonical(root)

    def test_repository_rejects_unknown_top_level_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "plugins").mkdir()
            (root / "unexpected-product-surface").mkdir()
            with self.assertRaisesRegex(
                    ValueError, "unsupported repository top-level directory"):
                build_distributions.validate_canonical(root)

    def test_packaged_sources_reject_symbolic_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plugin = root / "plugins" / "sample-team"
            plugin.mkdir(parents=True)
            outside = root / "outside.txt"
            outside.write_text("outside\n", encoding="utf-8")
            (plugin / "constitution.md").symlink_to(outside)
            with self.assertRaisesRegex(ValueError, "symbolic links are forbidden"):
                build_distributions.validate_canonical(root)

    def test_independent_distribution_builds_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first"
            second = root / "second"
            build_distributions.build(REPO, first)
            build_distributions.build(REPO, second)
            self.assertEqual(build_distributions.compare_dirs(first, second), [])

    def test_canonical_payload_is_present_on_both_hosts(self):
        for source in sorted((REPO / "plugins").iterdir()):
            if not source.is_dir():
                continue
            for path in sorted(
                candidate for candidate in source.rglob("*")
                if candidate.is_file()
                and not build_distributions.is_python_cache(candidate)
            ):
                relative = path.relative_to(source)
                if relative.parts[0] == "agents":
                    continue  # each host receives its native agent surface
                source_bytes = path.read_bytes()
                for host in ("claude", "codex"):
                    target = REPO / "dist" / host / source.name / relative
                    self.assertTrue(target.is_file(), f"{host} lacks {relative}")
                    if relative.as_posix() == "templates/gitignore" \
                            and host == "claude":
                        self.assertTrue(target.read_bytes().startswith(
                            source_bytes.rstrip(b"\n")))
                        continue
                    if b"\0" not in source_bytes:
                        expected = source_bytes.replace(b"\r\n", b"\n").replace(
                            b"\r", b"\n")
                    else:
                        expected = source_bytes
                    self.assertEqual(target.read_bytes(), expected)

    def test_agent_metadata_is_mapped_per_host(self):
        source = (REPO / "plugins" / "software-engineering-team" / "agents"
                  / "business-analyst.md").read_text(encoding="utf-8")
        claude = (REPO / "dist" / "claude" / "software-engineering-team"
                  / "agents" / "business-analyst.md").read_text(encoding="utf-8")
        codex = (REPO / "dist" / "codex" / "software-engineering-team"
                 / "agents" / "business-analyst.md").read_text(encoding="utf-8")
        self.assertIn("reasoning: high", source)
        self.assertNotIn("model:", source)
        self.assertIn("model: opus", claude)
        self.assertNotIn("reasoning:", claude)
        self.assertIn("reasoning: high", codex)


def agent(name: str, reasoning: str, tools: str = "") -> str:
    tools_line = f"tools: {tools}\n" if tools else ""
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {name} role.\n"
        f"reasoning: {reasoning}\n"
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
        (self.plugin / ".codex-plugin").mkdir()
        (self.plugin / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"name": "sample-team"}), encoding="utf-8"
        )
        (self.plugin / "templates" / "AGENTS.md").write_text(
            "# Team\n\nRead {{workspace}}/memory/me.md.\n", encoding="utf-8")
        for name, reasoning, tools in (
            ("architect", "high", "Read, Grep, Glob"),
            ("developer", "medium", ""),
            ("scanner", "low", "Read"),
            ("delegate", "inherit", ""),
        ):
            (self.plugin / "agents" / f"{name}.md").write_text(
                agent(name, reasoning, tools), encoding="utf-8")
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
        stale = agents / "stale-owned-agent.toml"
        stale.write_text(generate.owner("sample-team")
                         + "\nname = \"stale-owned-agent\"\n",
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

    def snapshot(self):
        return {
            path.relative_to(self.project).as_posix(): (
                "dir" if path.is_dir() else path.read_bytes()
            )
            for path in sorted(self.project.rglob("*"))
        }

    def test_incomplete_agents_block_aborts_before_any_write(self):
        (self.project / "AGENTS.md").write_text(
            "# User\n\n<!-- agentrof:sample-team:codex:start -->\nbroken\n",
            encoding="utf-8",
        )
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "incomplete Agentrof managed block"):
            generate.materialize(self.project, self.plugin, "workspace")
        self.assertEqual(self.snapshot(), before)

    def test_writer_failure_rolls_back_every_change(self):
        before = self.snapshot()
        calls = 0

        def fail_second(path, content):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected write failure")
            generate.atomic_write(path, content)

        with self.assertRaisesRegex(OSError, "injected write failure"):
            generate.materialize(
                self.project, self.plugin, "workspace", writer=fail_second
            )
        self.assertEqual(self.snapshot(), before)

    def test_other_team_ownership_fails_closed(self):
        workspace = self.project / "workspace"
        workspace.mkdir()
        (workspace / "config.json").write_text(
            json.dumps({"managed_by": "another-team"}), encoding="utf-8"
        )
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "another-team"):
            generate.materialize(self.project, self.plugin, "workspace")
        self.assertEqual(self.snapshot(), before)

    def test_concurrent_identical_setup_converges_without_partial_files(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(
                    generate.materialize,
                    self.project,
                    self.plugin,
                    "workspace",
                )
                for _ in range(16)
            ]
            for future in futures:
                future.result()
        self.assertEqual(generate.materialize(
            self.project, self.plugin, "workspace"), [])
        agents = self.project / ".codex" / "agents"
        self.assertEqual(
            {path.name for path in agents.glob("*.toml")},
            {"architect.toml", "developer.toml", "scanner.toml", "delegate.toml"},
        )
        self.assertEqual(
            [path for path in self.project.rglob(".*") if path.is_file()
             and path.name not in {".git"}],
            [],
        )


if __name__ == "__main__":
    unittest.main()
