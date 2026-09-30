"""Release test narrowing must preserve every runtime byte and mode."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

import build_distributions  # noqa: E402
import ci_release  # noqa: E402
import fixtures  # noqa: E402
import git_fixture  # noqa: E402
import release  # noqa: E402


class ReleaseScopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        fixtures.make_valid_root(self.root)
        git_fixture.init_repository(self.root)
        self.git("config", "user.name", "CI tests")
        self.git("config", "user.email", "ci@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.git("add", "--all")
        for metadata in (self.root / "dist").glob("*/*/.agent-marketplace-package.json"):
            value = json.loads(metadata.read_text(encoding="utf-8"))
            for executable in value["executables"]:
                path = (metadata.parent / executable).relative_to(self.root).as_posix()
                self.git("update-index", "--chmod=+x", path)
        self.git("commit", "-qm", "stable")
        self.stable = self.git("rev-parse", "HEAD")
        self.write_json(".changes/release.json", {
            "summary": "Exercise metadata-only release classification.",
            "components": {fixtures.PLUGIN: "patch"},
        })
        self.git("add", "--all")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD")
        release.prepare(
            self.root, self.stable, self.base,
            released_paths=release.changeset_paths_at_ref(self.root, self.stable),
        )
        build_distributions.replace_generated(self.root, self.root / "dist")
        self.git("add", "--all")
        self.git("commit", "-qm", "chore: prepare stable v0.0.2")
        self.head = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "--detach", self.base)

    def tearDown(self):
        git_fixture.remove_temporary(self.temporary)

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def write_json(self, path, value):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def mutate(self, action):
        self.git("checkout", "-q", "--detach", self.head)
        action()
        self.git("add", "--all")
        self.git("commit", "-qm", "candidate mutation")
        self.head = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "--detach", self.base)

    def mutate_json(self, path, change):
        def apply():
            data = json.loads((self.root / path).read_text(encoding="utf-8"))
            change(data)
            self.write_json(path, data)
        self.mutate(apply)

    def assert_full(self, reason):
        result = ci_release.classify(self.root, self.base, self.head)
        self.assertFalse(result["release_only"], result)
        self.assertIn(reason, result["reason"])

    def test_verified_release_preserves_runtime_bytes_and_modes_on_every_host(self):
        release.verify_release_pr(
            self.root, base_sha=self.base, head_sha=self.head, stable_sha=self.stable,
        )
        result = ci_release.classify(self.root, self.base, self.head)
        self.assertTrue(result["release_only"], result)

    def test_changed_runtime_byte_falls_back_even_when_provenance_is_rebuilt(self):
        def change():
            path = self.root / "plugins" / fixtures.PLUGIN / "scripts" / "atomic_file.py"
            path.write_bytes(path.read_bytes() + b"\n# changed runtime\n")
            build_distributions.replace_generated(self.root, self.root / "dist")
        self.mutate(change)
        self.assert_full("runtime or an unknown path")

    def test_unknown_added_path_falls_back(self):
        self.mutate(lambda: fixtures.write(self.root / "new-policy.txt", "changed\n"))
        self.assert_full("runtime file inventory")

    def test_deleted_runtime_path_falls_back(self):
        self.mutate(lambda: (self.root / "plugins" / fixtures.PLUGIN / "constitution.md").unlink())
        self.assert_full("runtime file inventory")

    def test_manifest_field_change_is_not_hidden_by_version_normalization(self):
        self.mutate_json(
            f"platforms/codex/{fixtures.PLUGIN}/manifest.json",
            lambda data: data.update(description="different behavior"),
        )
        self.assert_full("beyond version")

    def test_catalog_source_change_is_not_hidden_by_adapter_rewrite(self):
        self.mutate_json(
            ".claude-plugin/marketplace.json",
            lambda data: data["plugins"][0].update(source="./elsewhere"),
        )
        self.assert_full("catalog content beyond version")

    def test_executable_mode_change_falls_back(self):
        def change():
            self.git("update-index", "--chmod=+x", "CHANGELOG.md")
            self.git("commit", "-qm", "mode mutation")
            self.git("checkout-index", "--all", "--force")
            self.head = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "--detach", self.head)
        change()
        self.git("checkout", "-q", "--detach", self.base)
        self.assert_full("executable mode")

    def test_forged_provenance_inventory_falls_back(self):
        self.mutate_json(
            f"dist/claude/{fixtures.PLUGIN}/.agent-marketplace-package.json",
            lambda data: data["files"].update({"constitution.md": "0" * 64}),
        )
        self.assert_full("inventory or modes differ")

    def test_provenance_runtime_contract_change_falls_back(self):
        self.mutate_json(
            f"dist/codex/{fixtures.PLUGIN}/.agent-marketplace-package.json",
            lambda data: data.update(runtime_contracts={"unknown": 2}),
        )
        self.assert_full("runtime contracts")

    def test_new_provenance_field_requires_full_coverage(self):
        self.mutate_json(
            f"dist/codex/{fixtures.PLUGIN}/.agent-marketplace-package.json",
            lambda data: data.update(future_behavior=True),
        )
        self.assert_full("unsupported package provenance")

    def test_retired_build_identity_requires_full_coverage(self):
        self.mutate_json(
            f"dist/claude/{fixtures.PLUGIN}/.agent-marketplace-package.json",
            lambda data: data.update(build_id="snapshot." + "0" * 64),
        )
        self.assert_full("unsupported package provenance")

    def test_added_changeset_is_not_a_release_only_transformation(self):
        self.mutate(lambda: self.write_json(".changes/new.json", {
            "summary": "new input", "components": {},
        }))
        self.assert_full("adds or modifies a changeset")

    def test_duplicate_manifest_keys_fail_closed(self):
        self.mutate(lambda: fixtures.write(
            self.root / "platforms" / "codex" / fixtures.PLUGIN / "manifest.json",
            '{"version":"0.0.2","version":"0.0.2"}\n',
        ))
        self.assert_full("duplicate JSON key")

    def test_untrusted_checkout_and_invalid_refs_fail_closed(self):
        result = ci_release.classify(self.root, self.head, self.base)
        self.assertFalse(result["release_only"])
        self.assertIn("trusted base", result["reason"])
        result = ci_release.classify(self.root, "HEAD", self.head)
        self.assertFalse(result["release_only"])

    def test_identical_tree_requires_no_release_narrowing(self):
        result = ci_release.classify(self.root, self.base, self.base)
        self.assertFalse(result["release_only"])


if __name__ == "__main__":
    unittest.main()
