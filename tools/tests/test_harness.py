"""Generator test suite: determinism, escaping, routing and drift.

Runs against the REAL repository tree (read-only renders) and against
temporary roots for write/orphan behavior, so the production code path and
the config file are both exercised.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent))

import counts  # noqa: E402
import harness  # noqa: E402

REPO_ROOT = TESTS_DIR.parent.parent


def make_mini_root(root: Path) -> None:
    """A minimal two-harness source tree exercising every emitter."""
    (root / "tools" / "data").mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / "tools" / "data" / "harnesses.json",
                    root / "tools" / "data" / "harnesses.json")
    (root / ".claude-plugin").mkdir()
    (root / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "name": "mini-market",
        "owner": {"name": "Owner", "url": "https://example.invalid/owner"},
        "metadata": {"description": "mini", "version": "0.0.1"},
        "plugins": [{"name": "mini-team", "source": "./plugins/mini-team",
                     "description": "mini plugin", "version": "0.0.1",
                     "license": "MIT"}],
    }, indent=2), encoding="utf-8")
    plugin = root / "plugins" / "mini-team"
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin" / "plugin.json").write_text(json.dumps({
        "name": "mini-team", "version": "0.0.1",
        "description": "mini plugin",
        "author": {"name": "Owner", "url": "https://example.invalid/owner"},
        "license": "MIT",
    }, indent=2), encoding="utf-8")
    (plugin / "agents").mkdir()
    (plugin / "agents" / "worker.md").write_text(
        "---\n"
        "name: mini-team-worker\n"
        "description: Worker role. Dispatched by mini-team flows.\n"
        "model: opus\n"
        "---\n\n# Worker\n\nBody with a \"quoted\" phrase.\n",
        encoding="utf-8")
    (plugin / "agents" / "auditor.md").write_text(
        "---\n"
        "name: mini-team-auditor\n"
        "description: Auditor role. Dispatched by mini-team flows.\n"
        "model: sonnet\n"
        "tools: Read, Grep, Glob\n"
        "---\n\n# Auditor\n\nRead-only body.\n",
        encoding="utf-8")
    entry = plugin / "skills" / "front-door"
    entry.mkdir(parents=True)
    (entry / "SKILL.md").write_text(
        "---\nname: front-door\ndescription: Entry point.\n"
        "disable-model-invocation: true\n---\n\n# Front Door\n\n"
        "## When to Use\n- Explicitly.\n",
        encoding="utf-8")
    hidden = plugin / "skills" / "lore"
    hidden.mkdir(parents=True)
    (hidden / "SKILL.md").write_text(
        "---\nname: lore\ndescription: Knowledge skill.\n"
        "user-invocable: false\n---\n\n# Lore\n\n## When to Use\n- Loaded.\n",
        encoding="utf-8")
    (plugin / "hooks").mkdir()
    (plugin / "hooks" / "hooks.json").write_text(json.dumps({
        "hooks": {
            "SessionStart": [{"hooks": [{
                "type": "command",
                "command": "python3 \"${CLAUDE_PLUGIN_ROOT}\"/scripts/hook_session_start.py"}]}],
            "SessionEnd": [{"hooks": [{
                "type": "command",
                "command": "python3 \"${CLAUDE_PLUGIN_ROOT}\"/scripts/hook_session_end.py"}]}],
            "PreToolUse": [{"matcher": "Write|Edit|Bash", "hooks": [{
                "type": "command",
                "command": "python3 \"${CLAUDE_PLUGIN_ROOT}\"/scripts/hook_guard_db.py"}]}],
            "PostToolUse": [{"matcher": "Write|Edit", "hooks": [{
                "type": "command",
                "command": "python3 \"${CLAUDE_PLUGIN_ROOT}\"/scripts/vault_hook.py post"}]}],
        },
    }, indent=2), encoding="utf-8")
    (plugin / "templates").mkdir()
    (plugin / "templates" / "CLAUDE.md").write_text(
        "# Load\n\n@workspace/memory/me.md\n\n## Rules\n\n"
        "workspace/memory/me.md. Read and follow.\n",
        encoding="utf-8")


class HarnessRenderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        make_mini_root(self.root)
        self.config = harness.load_config(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_render_is_deterministic(self):
        first = harness.render_all(self.root, self.config)
        second = harness.render_all(self.root, self.config)
        self.assertEqual(first, second)

    def test_write_all_is_idempotent(self):
        harness.write_all(self.root, self.config)
        changed = harness.write_all(self.root, self.config)
        self.assertEqual(changed, [], "second write must be a no-op")
        self.assertEqual(harness.diff(self.root, self.config), [])

    def test_cursor_agent_transform(self):
        rendered = harness.render_all(self.root, self.config)
        auditor = rendered["plugins/mini-team/cursor/agents/auditor.md"]
        self.assertIn("readonly: true", auditor)
        self.assertNotIn("tools:", auditor)
        self.assertIn("model: inherit", auditor)
        self.assertNotIn("opus", rendered["plugins/mini-team/cursor/agents/worker.md"])
        self.assertIn("Read-only body.", auditor)

    def test_codex_agent_toml(self):
        rendered = harness.render_all(self.root, self.config)
        auditor = rendered["plugins/mini-team/codex/agents/mini-team-auditor.toml"]
        self.assertIn('name = "mini-team-auditor"', auditor)
        self.assertIn('sandbox_mode = "read-only"', auditor)
        self.assertNotIn("model =", auditor)
        worker = rendered["plugins/mini-team/codex/agents/mini-team-worker.toml"]
        self.assertIn('developer_instructions = """', worker)
        self.assertIn('Body with a "quoted" phrase.', worker)
        self.assertNotIn("model =", worker)

    def test_cursor_hooks_routing(self):
        rendered = harness.render_all(self.root, self.config)
        hooks = json.loads(rendered["plugins/mini-team/cursor/hooks/hooks.json"])
        self.assertEqual(hooks["version"], 1)
        events = hooks["hooks"]
        self.assertIn("sessionStart", events)
        self.assertIn("sessionEnd", events)
        self.assertIn("beforeShellExecution", events)
        self.assertIn("preToolUse", events)
        self.assertIn("postToolUse", events)
        self.assertIn("afterFileEdit", events)
        pre = events["preToolUse"][0]
        self.assertNotIn("Bash", pre["matcher"])
        self.assertIn("search_replace", pre["matcher"])
        self.assertTrue(pre["failClosed"], "guard must be fail-closed")
        self.assertIn("timeout", pre)
        shell = events["beforeShellExecution"][0]
        self.assertNotIn("matcher", shell)
        self.assertTrue(shell["failClosed"])
        post = events["postToolUse"][0]
        self.assertNotIn("failClosed", post, "bookkeeping hooks stay fail-open")
        self.assertIn("${CURSOR_PLUGIN_ROOT:-.}/scripts/hook_guard_db.py", pre["command"])
        self.assertTrue(events["afterFileEdit"][0]["command"].endswith('" post'))

    def test_codex_hooks_routing(self):
        rendered = harness.render_all(self.root, self.config)
        hooks = json.loads(rendered["plugins/mini-team/codex/hooks/hooks.json"])
        events = hooks["hooks"]
        self.assertNotIn("SessionEnd", events, "codex has no SessionEnd")
        self.assertIn("SessionStart", events)
        pre = events["PreToolUse"][0]
        self.assertEqual(pre["matcher"], "Write|apply_patch|Edit|Bash")
        self.assertIn("${CLAUDE_PLUGIN_ROOT}", pre["hooks"][0]["command"])

    def test_unrouted_event_raises(self):
        hooks_path = self.root / "plugins" / "mini-team" / "hooks" / "hooks.json"
        data = json.loads(hooks_path.read_text(encoding="utf-8"))
        data["hooks"]["PreCompact"] = [{"hooks": [{"type": "command", "command": "true"}]}]
        hooks_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        with self.assertRaises(harness.HarnessConfigError):
            harness.render_all(self.root, self.config)

    def test_skill_policy_files(self):
        rendered = harness.render_all(self.root, self.config)
        entry = rendered["plugins/mini-team/skills/front-door/agents/openai.yaml"]
        self.assertIn("interface:", entry)
        self.assertIn('display_name: "Front Door"', entry)
        self.assertIn("policy:", entry)
        self.assertIn("allow_implicit_invocation: false", entry)
        hidden = rendered["plugins/mini-team/skills/lore/agents/openai.yaml"]
        self.assertIn("allow_implicit_invocation: true", hidden)
        self.assertNotIn(
            "plugins/mini-team/skills/front-door/agents/openai.yaml".replace(
                "codex", "cursor"),
            [k for k in rendered if "cursor" in k and "openai.yaml" in k],
            "only codex declares a skill policy file")

    def test_agents_md_derivation(self):
        rendered = harness.render_all(self.root, self.config)
        agents_md = rendered["plugins/mini-team/templates/AGENTS.md"]
        self.assertNotIn("@workspace/memory/me.md", agents_md)
        self.assertIn("workspace/memory/me.md. Read and follow.", agents_md)

    def test_marketplaces(self):
        rendered = harness.render_all(self.root, self.config)
        cursor = json.loads(rendered[".cursor-plugin/marketplace.json"])
        self.assertEqual(cursor["owner"], {"name": "Owner"},
                         "owner carries documented keys only")
        self.assertEqual(cursor["plugins"][0]["source"], "./plugins/mini-team")
        codex = json.loads(rendered[".agents/plugins/marketplace.json"])
        self.assertEqual(codex["plugins"][0]["source"],
                         {"source": "local", "path": "./plugins/mini-team"})
        self.assertEqual([p["name"] for p in cursor["plugins"]],
                         [p["name"] for p in codex["plugins"]],
                         "the two marketplace plugin lists stay consistent")

    def test_plugin_manifests(self):
        rendered = harness.render_all(self.root, self.config)
        cursor = json.loads(rendered["plugins/mini-team/.cursor-plugin/plugin.json"])
        self.assertEqual(cursor["agents"], "./cursor/agents")
        self.assertEqual(cursor["hooks"], "./cursor/hooks/hooks.json")
        self.assertEqual(cursor["homepage"], "https://example.invalid/owner")
        self.assertNotIn("url", cursor.get("author", {}))
        codex = json.loads(rendered["plugins/mini-team/.codex-plugin/plugin.json"])
        self.assertEqual(codex["skills"], "./skills/")
        self.assertEqual(codex["hooks"], "./codex/hooks/hooks.json")

    def test_drift_detects_stale_missing_orphan(self):
        harness.write_all(self.root, self.config)
        self.assertEqual(harness.diff(self.root, self.config), [])
        # stale
        manifest = self.root / "plugins/mini-team/.cursor-plugin/plugin.json"
        manifest.write_text(manifest.read_text(encoding="utf-8") + "\n",
                            encoding="utf-8")
        problems = dict(harness.diff(self.root, self.config))
        self.assertEqual(problems.get("plugins/mini-team/.cursor-plugin/plugin.json"),
                         "stale")
        harness.write_all(self.root, self.config)
        # missing
        toml = self.root / "plugins/mini-team/codex/agents/mini-team-worker.toml"
        toml.unlink()
        problems = dict(harness.diff(self.root, self.config))
        self.assertEqual(
            problems.get("plugins/mini-team/codex/agents/mini-team-worker.toml"),
            "missing")
        harness.write_all(self.root, self.config)
        # orphan: delete a source agent, its artifact must be flagged then removed
        (self.root / "plugins/mini-team/agents/worker.md").unlink()
        problems = dict(harness.diff(self.root, self.config))
        self.assertEqual(
            problems.get("plugins/mini-team/codex/agents/mini-team-worker.toml"),
            "orphan")
        self.assertEqual(
            problems.get("plugins/mini-team/cursor/agents/worker.md"), "orphan")
        harness.write_all(self.root, self.config)
        self.assertEqual(harness.diff(self.root, self.config), [])

    def test_matrix_block_injection(self):
        harness.write_all(self.root, self.config)
        doc = self.root / "docs" / "harnesses.md"
        self.assertTrue(doc.is_file())
        text = doc.read_text(encoding="utf-8")
        self.assertIn(harness.MATRIX_START, text)
        # hand prose around the block survives a re-render
        doc.write_text("Intro prose.\n\n" + text + "\nOutro prose.\n",
                       encoding="utf-8")
        harness.write_all(self.root, self.config)
        final = doc.read_text(encoding="utf-8")
        self.assertIn("Intro prose.", final)
        self.assertIn("Outro prose.", final)
        # a hand-edited block is stale
        doc.write_text(final.replace("| Capability |", "| Capabilities |"),
                       encoding="utf-8")
        problems = dict(harness.diff(self.root, self.config))
        self.assertEqual(problems.get("docs/harnesses.md"), "stale")

    def test_counts_unaffected_by_generation(self):
        before = counts.compute(self.root)
        harness.write_all(self.root, self.config)
        after = counts.compute(self.root)
        self.assertEqual(before, after,
                         "generated artifacts must never move the counts")

    def test_is_generated_predicate(self):
        harness.write_all(self.root, self.config)
        cfg = self.config
        gen = [
            self.root / ".cursor-plugin/marketplace.json",
            self.root / ".agents/plugins/marketplace.json",
            self.root / "plugins/mini-team/.codex-plugin/plugin.json",
            self.root / "plugins/mini-team/cursor/agents/auditor.md",
            self.root / "plugins/mini-team/codex/hooks/hooks.json",
            self.root / "plugins/mini-team/skills/lore/agents/openai.yaml",
            self.root / "plugins/mini-team/templates/AGENTS.md",
        ]
        for path in gen:
            self.assertTrue(harness.is_generated(self.root, cfg, path), path)
        src = [
            self.root / "plugins/mini-team/agents/auditor.md",
            self.root / "plugins/mini-team/skills/lore/SKILL.md",
            self.root / "plugins/mini-team/hooks/hooks.json",
            self.root / "plugins/mini-team/templates/CLAUDE.md",
            self.root / ".claude-plugin/marketplace.json",
        ]
        for path in src:
            self.assertFalse(harness.is_generated(self.root, cfg, path), path)


class RealRepoRenderTests(unittest.TestCase):
    """Read-only renders against the actual repository."""

    def test_real_repo_renders_without_error(self):
        config = harness.load_config(REPO_ROOT)
        rendered = harness.render_all(REPO_ROOT, config)
        self.assertIn(".cursor-plugin/marketplace.json", rendered)
        self.assertIn(".agents/plugins/marketplace.json", rendered)
        set_agents = [k for k in rendered
                      if k.startswith("plugins/software-engineering-team/cursor/agents/")]
        self.assertEqual(len(set_agents), 12)
        tomls = [k for k in rendered
                 if k.startswith("plugins/software-engineering-team/codex/agents/")]
        self.assertEqual(len(tomls), 12)

    def test_real_repo_model_policy(self):
        config = harness.load_config(REPO_ROOT)
        rendered = harness.render_all(REPO_ROOT, config)
        for key, content in rendered.items():
            if "/cursor/agents/" in key:
                self.assertIn("model: inherit", content, key)
            if "/codex/agents/" in key:
                self.assertNotIn("model =", content, key)

    def test_real_repo_runtime_data(self):
        config = harness.load_config(REPO_ROOT)
        rendered = harness.render_all(REPO_ROOT, config)
        runtime_key = "plugins/project-management-office/scripts/harness_runtime.json"
        self.assertIn(runtime_key, rendered)
        payload = json.loads(rendered[runtime_key])
        self.assertIn("harness_signals", payload)
        self.assertIn("sandbox_stanzas", payload)


if __name__ == "__main__":
    unittest.main()
