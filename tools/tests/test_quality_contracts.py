"""Adversarial contract cases for cross-host packaging and canonical purity.

Each named case breaks one sub-contract. Cases are materialized as individual
tests so the reported suite count reflects the enforced surface, while the
registries make new high-risk branches explicit and reviewable.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

import build_distributions  # noqa: E402
import fixtures  # noqa: E402
import validate  # noqa: E402


PLUGIN = fixtures.PLUGIN


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def codex_marketplace(root: Path) -> tuple[Path, dict]:
    path = root / ".agents" / "plugins" / "marketplace.json"
    return path, read_json(path)


def manifest(root: Path, host: str, distributed: bool = False) -> tuple[Path, dict]:
    if distributed:
        path = root / "dist" / host / PLUGIN / f".{host}-plugin" / "plugin.json"
    else:
        path = root / "platforms" / host / PLUGIN / "manifest.json"
    return path, read_json(path)


def set_codex_marketplace_name(root: Path) -> None:
    path, data = codex_marketplace(root)
    data["name"] = "wrong-marketplace"
    write_json(path, data)


def set_codex_marketplace_display(root: Path) -> None:
    path, data = codex_marketplace(root)
    data["interface"]["displayName"] = "Wrong Marketplace"
    write_json(path, data)


def remove_codex_marketplace_plugin(root: Path) -> None:
    path, data = codex_marketplace(root)
    data["plugins"] = []
    write_json(path, data)


def set_codex_source_path(root: Path) -> None:
    path, data = codex_marketplace(root)
    data["plugins"][0]["source"]["path"] = "./dist/codex/ghost-team"
    write_json(path, data)


def set_codex_install_policy(root: Path) -> None:
    path, data = codex_marketplace(root)
    data["plugins"][0]["policy"]["installation"] = "INSTALLED_BY_DEFAULT"
    write_json(path, data)


def set_codex_auth_policy(root: Path) -> None:
    path, data = codex_marketplace(root)
    data["plugins"][0]["policy"]["authentication"] = "NONE"
    write_json(path, data)


def set_codex_category(root: Path) -> None:
    path, data = codex_marketplace(root)
    data["plugins"][0]["category"] = "Productivity"
    write_json(path, data)


def set_codex_display_name(root: Path) -> None:
    path, data = manifest(root, "codex")
    data["interface"]["displayName"] = "Agentrof Sample Team"
    write_json(path, data)


def remove_generated_marker(root: Path) -> None:
    (root / "dist" / "codex" / PLUGIN
     / build_distributions.MARKER).unlink()


ENTRY_SKILL = """---
name: launch
description: Entry point for a guided team launch.
exposure: entry
---

# Launch

Start the guided team launch.

## When to Use
- The user explicitly starts this flow.
"""


def add_entry_skill(root: Path) -> Path:
    fixtures.write(
        root / "plugins" / PLUGIN / "skill-content" / "launch" / "SKILL.md",
        ENTRY_SKILL,
    )
    build_distributions.replace_generated(root, root / "dist")
    return (root / "dist" / "codex" / PLUGIN / "skills" / "launch"
            / "agents" / "openai.yaml")


def remove_codex_explicit_policy(root: Path) -> None:
    path = add_entry_skill(root)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "  allow_implicit_invocation: false\n", ""
        ),
        encoding="utf-8",
    )


def break_codex_namespaced_prompt(root: Path) -> None:
    path = add_entry_skill(root)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "$sample-team:launch", "$wrong-team:launch"
        ),
        encoding="utf-8",
    )


def remove_claude_dependency(root: Path) -> None:
    path, data = manifest(root, "claude")
    data.pop("dependencies")
    write_json(path, data)


def add_codex_dependency(root: Path) -> None:
    path, data = manifest(root, "codex")
    data["dependencies"] = ["project-management-office"]
    write_json(path, data)


def remove_codex_visible_requirement(root: Path) -> None:
    path, data = manifest(root, "codex")
    data["interface"]["longDescription"] = "Fixture team"
    write_json(path, data)


def remove_claude_diagnostic(root: Path) -> None:
    path = root / "platforms" / "claude" / PLUGIN / "host-contract.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "claude plugin list --json", "claude plugin list"
        ),
        encoding="utf-8",
    )


def remove_codex_generator_contract(root: Path) -> None:
    path = root / "platforms" / "codex" / PLUGIN / "host-contract.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "generate_codex_project.py", "project generator"
        ),
        encoding="utf-8",
    )


def remove_claude_hook_surface(root: Path) -> None:
    path = (root / "platforms" / "claude" / PLUGIN / "overlay" / "hooks"
            / "hooks.json")
    data = read_json(path)
    data["hooks"]["PreToolUse"][0]["matcher"] = "Write|Edit"
    write_json(path, data)


def remove_codex_hook_surface(root: Path) -> None:
    path = (root / "platforms" / "codex" / PLUGIN / "overlay" / "hooks"
            / "hooks.json")
    data = read_json(path)
    data["hooks"]["PreToolUse"][0]["matcher"] = "Write|Edit|Bash"
    write_json(path, data)


def drift_version(host: str, distributed: bool):
    def mutate(root: Path) -> None:
        path, data = manifest(root, host, distributed)
        data["version"] = "9.9.9"
        write_json(path, data)
    return mutate


CONTRACT_CASES = {
    "codex_marketplace_technical_name": (
        set_codex_marketplace_name, "distribution_packaging", "marketplace name"),
    "codex_marketplace_visible_name": (
        set_codex_marketplace_display, "distribution_packaging", "display name"),
    "codex_marketplace_registration": (
        remove_codex_marketplace_plugin, "distribution_packaging", "does not register"),
    "codex_marketplace_source": (
        set_codex_source_path, "distribution_packaging", "Codex source"),
    "codex_marketplace_install_policy": (
        set_codex_install_policy, "distribution_packaging", "incorrect Codex policy"),
    "codex_marketplace_auth_policy": (
        set_codex_auth_policy, "distribution_packaging", "incorrect Codex policy"),
    "codex_marketplace_category": (
        set_codex_category, "distribution_packaging", "incorrect Codex policy"),
    "codex_manifest_visible_name": (
        set_codex_display_name, "distribution_packaging", "display name"),
    "generated_distribution_marker": (
        remove_generated_marker, "distribution_packaging", "ownership marker"),
    "codex_explicit_skill_policy": (
        remove_codex_explicit_policy, "distribution_packaging", "explicit-only"),
    "codex_namespaced_skill_prompt": (
        break_codex_namespaced_prompt, "distribution_packaging", "namespaced Codex prompt"),
    "claude_native_pmo_dependency": (
        remove_claude_dependency, "team_pmo_contract", "Claude manifest lacks"),
    "codex_rejects_dependency_field": (
        add_codex_dependency, "team_pmo_contract", "unsupported plugin dependencies"),
    "codex_visible_pmo_requirement": (
        remove_codex_visible_requirement, "team_pmo_contract", "visible description"),
    "claude_read_only_diagnostic": (
        remove_claude_diagnostic, "team_pmo_contract", "claude plugin list --json"),
    "codex_project_generator_contract": (
        remove_codex_generator_contract, "team_pmo_contract", "generate_codex_project.py"),
    "claude_mutation_hook_surface": (
        remove_claude_hook_surface, "team_pmo_contract", "Write|Edit|Bash"),
    "codex_mutation_hook_surface": (
        remove_codex_hook_surface, "team_pmo_contract", "apply_patch"),
    "claude_platform_version": (
        drift_version("claude", False), "version_sync", "version drift"),
    "codex_platform_version": (
        drift_version("codex", False), "version_sync", "version drift"),
    "claude_distribution_version": (
        drift_version("claude", True), "version_sync", "version drift"),
    "codex_distribution_version": (
        drift_version("codex", True), "version_sync", "version drift"),
}


class CrossHostContractCases(unittest.TestCase):
    def run_case(self, mutate, expected_check: str, message: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.make_valid_root(root)
            mutate(root)
            findings = validate.run(root)
            matching = [
                finding for finding in findings
                if finding.check == expected_check and message in finding.message
            ]
            self.assertTrue(matching, findings)


def contract_test(case_name: str, case):
    def test(self):
        self.run_case(*case)
    test.__name__ = f"test_{case_name}"
    return test


for _name, _case in CONTRACT_CASES.items():
    setattr(CrossHostContractCases, f"test_{_name}", contract_test(_name, _case))


class CanonicalHostNeutralityCases(unittest.TestCase):
    def test_required_host_token_registry(self):
        self.assertTrue({
            "Claude", "Codex", "AskUserQuestion", "CLAUDE.md", "AGENTS.md",
            "CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT", ".claude", ".codex",
        } <= set(build_distributions.CANONICAL_HOST_TOKENS))

    def assert_token_rejected(self, token: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixtures.write(
                root / "plugins" / "sample-team" / "flows" / "develop.md",
                f"# Develop\n\nUse {token} here.\n",
            )
            with self.assertRaisesRegex(ValueError, "canonical content contains"):
                build_distributions.validate_canonical(root)


def token_test(token: str):
    def test(self):
        self.assert_token_rejected(token)
    return test


for _index, _token in enumerate(build_distributions.CANONICAL_HOST_TOKENS):
    _slug = re.sub(r"[^a-z0-9]+", "_", _token.lower()).strip("_")
    setattr(
        CanonicalHostNeutralityCases,
        f"test_rejects_{_index:02d}_{_slug}",
        token_test(_token),
    )


if __name__ == "__main__":
    unittest.main()
