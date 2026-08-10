"""Stable release math, registry, and cross-host transaction contracts."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

import build_distributions  # noqa: E402
import fixtures  # noqa: E402
import release  # noqa: E402


def changeset(path: Path, summary: str, components: dict[str, str]) -> release.Changeset:
    return release.Changeset(path, summary, components)


class SemVerTests(unittest.TestCase):
    def test_patch_minor_and_major(self):
        self.assertEqual(release.bump("0.0.1", "patch"), "0.0.2")
        self.assertEqual(release.bump("0.0.1", "minor"), "0.1.0")
        self.assertEqual(release.bump("1.1.0", "patch"), "1.1.1")
        self.assertEqual(release.bump("1.1.0", "minor"), "1.2.0")
        self.assertEqual(release.bump("1.1.0", "major"), "2.0.0")

    def test_strict_semver_rejects_prefix_prerelease_and_leading_zero(self):
        for value in ("v1.2.3", "1.2.3-beta", "01.2.3", "1.2"):
            with self.subTest(value=value), self.assertRaises(release.ReleaseError):
                release.parse_semver(value)

    def test_highest_effect_wins_and_plugins_remain_independent(self):
        versions = {
            "marketplace": "1.1.0",
            "plugins": {"alpha-team": "2.0.0", "beta-team": "3.4.5"},
        }
        plan = release.release_plan(versions, [
            changeset(Path("a.json"), "patch alpha", {"alpha-team": "patch"}),
            changeset(Path("b.json"), "minor alpha", {"alpha-team": "minor"}),
            changeset(Path("c.json"), "major catalog", {
                release.MARKETPLACE_COMPONENT: "major"
            }),
        ])
        self.assertEqual(plan["marketplace"], "2.0.0")
        self.assertEqual(plan["plugins"], {
            "alpha-team": "2.1.0", "beta-team": "3.4.5"
        })

    def test_empty_components_do_not_create_a_release(self):
        versions = {"marketplace": "0.0.1", "plugins": {"team": "0.0.1"}}
        plan = release.release_plan(versions, [
            changeset(Path("docs.json"), "docs", {})
        ])
        self.assertFalse(plan["has_release"])
        self.assertEqual(plan["marketplace"], "0.0.1")
        self.assertEqual(plan["plugins"]["team"], "0.0.1")


class ReleaseRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        fixtures.make_valid_root(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def write_changeset(self, name: str, components: dict[str, str]) -> None:
        fixtures.write(self.root / ".changes" / f"{name}.json", json.dumps({
            "summary": f"Apply {name}.",
            "components": components,
        }, indent=2))

    def test_fixture_has_one_canonical_version_on_every_host_surface(self):
        self.assertEqual(release.validate_version_surfaces(self.root), [])

    def test_marketplace_sources_stay_inside_the_selected_channel(self):
        claude = release.read_json(
            self.root / ".claude-plugin" / "marketplace.json"
        )
        codex = release.read_json(
            self.root / ".agents" / "plugins" / "marketplace.json"
        )
        self.assertEqual(
            claude["plugins"][0]["source"],
            release.channel_source("claude", fixtures.PLUGIN),
        )
        self.assertEqual(
            codex["plugins"][0]["source"],
            release.channel_source("codex", fixtures.PLUGIN),
        )

    def test_channel_source_rejects_unknown_host(self):
        with self.assertRaisesRegex(
            release.ReleaseError, "unknown marketplace host"
        ):
            release.channel_source("other", fixtures.PLUGIN)

    def test_claude_only_or_codex_only_drift_fails(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host):
                path = self.root / "platforms" / host / fixtures.PLUGIN / "manifest.json"
                original = path.read_bytes()
                data = json.loads(path.read_text(encoding="utf-8"))
                data["version"] = "0.0.2"
                path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                problems = release.validate_version_surfaces(self.root)
                self.assertTrue(any(host in problem and "version drift" in problem
                                    for problem in problems), problems)
                path.write_bytes(original)

    def test_prepare_consumes_changesets_and_updates_both_hosts(self):
        self.write_changeset("patch-team", {fixtures.PLUGIN: "patch"})
        self.write_changeset("minor-team", {fixtures.PLUGIN: "minor"})
        metadata = release.prepare(self.root, "a" * 40, "b" * 40)
        build_distributions.replace_generated(self.root, self.root / "dist")
        versions = release.load_versions(self.root)
        self.assertEqual(versions["marketplace"], "0.1.0")
        self.assertEqual(versions["plugins"][fixtures.PLUGIN], "0.1.0")
        self.assertEqual(metadata["stable_base"], "a" * 40)
        self.assertEqual(list((self.root / ".changes").glob("*.json")), [])
        self.assertEqual(release.validate_version_surfaces(self.root), [])
        for host in ("claude", "codex"):
            manifest = json.loads((
                self.root / "dist" / host / fixtures.PLUGIN
                / f".{host}-plugin" / "plugin.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], "0.1.0")

    def test_prepare_refuses_release_free_changesets_without_writes(self):
        versions_before = (self.root / "versions.json").read_bytes()
        with self.assertRaisesRegex(release.ReleaseError, "no pending"):
            release.prepare(self.root, "a" * 40, "b" * 40)
        self.assertEqual((self.root / "versions.json").read_bytes(), versions_before)

    def test_changeset_rejects_unknown_component_and_impact(self):
        self.write_changeset("unknown-component", {"ghost-team": "patch"})
        with self.assertRaisesRegex(release.ReleaseError, "unknown component"):
            release.load_changesets(self.root)
        (self.root / ".changes" / "unknown-component.json").unlink()
        self.write_changeset("unknown-impact", {fixtures.PLUGIN: "tiny"})
        with self.assertRaisesRegex(release.ReleaseError, "invalid impact"):
            release.load_changesets(self.root)

    def test_changed_plugin_requires_the_same_component(self):
        with mock.patch.object(release, "changed_paths", return_value=[
            ("A", ".changes/docs.json"),
            ("M", f"plugins/{fixtures.PLUGIN}/flows/develop.md"),
        ]):
            self.write_changeset("docs", {})
            with self.assertRaisesRegex(release.ReleaseError, fixtures.PLUGIN):
                release.check_pr_changeset(self.root, "origin/main")

    def test_bootstrap_exception_is_narrowly_baselined(self):
        with mock.patch.object(release, "changed_paths", return_value=[
            ("A", ".changes/fixture.json"),
            ("A", "versions.json"),
            ("M", f"plugins/{fixtures.PLUGIN}/flows/develop.md"),
        ]):
            release.check_pr_changeset(
                self.root, "origin/main", allow_bootstrap=True
            )
            versions = release.load_versions(self.root)
            versions["marketplace"] = "0.0.2"
            release.write_json(self.root / "versions.json", versions)
            with self.assertRaises(release.ReleaseError):
                release.check_pr_changeset(
                    self.root, "origin/main", allow_bootstrap=True
                )

    def test_bootstrap_reset_may_replace_release_owned_state(self):
        with mock.patch.object(release, "changed_paths", return_value=[
            ("A", ".changes/fixture.json"),
            ("M", "versions.json"),
            ("M", "CHANGELOG.md"),
            ("D", ".release/stable.json"),
        ]):
            release.check_pr_changeset(
                self.root, "origin/main", allow_bootstrap=True
            )

    def test_verify_bootstrap_rejects_prior_stable_provenance(self):
        fixtures.write(
            self.root / ".release" / "stable.json",
            json.dumps({"version": "0.1.2"}),
        )
        with self.assertRaisesRegex(
            release.ReleaseError, "prior stable provenance"
        ):
            release.verify_bootstrap(self.root)
        (self.root / ".release" / "stable.json").unlink()
        self.assertEqual(
            release.verify_bootstrap(self.root)["marketplace"], "0.0.1"
        )

    def test_normal_pr_cannot_edit_release_owned_state(self):
        self.write_changeset("plugin-patch", {fixtures.PLUGIN: "patch"})
        with mock.patch.object(release, "changed_paths", return_value=[
            ("A", ".changes/plugin-patch.json"),
            ("M", "versions.json"),
        ]):
            with self.assertRaisesRegex(release.ReleaseError, "release-owned"):
                release.check_pr_changeset(self.root, "origin/main")

    def test_changesets_already_on_stable_do_not_enter_next_release(self):
        self.write_changeset("new-patch", {fixtures.PLUGIN: "patch"})
        metadata = release.prepare(
            self.root, "a" * 40, "b" * 40,
            released_paths={".changes/fixture.json"},
        )
        self.assertEqual(metadata["summaries"], ["Apply new-patch."])

    def test_main_build_identity_is_unique_without_semver_change(self):
        full_sha = "abcdef0123456789abcdef0123456789abcdef01"

        def fake_git(_root, *args):
            return full_sha if args[:1] == ("rev-parse",) else "42"

        with mock.patch.object(release, "git", side_effect=fake_git):
            identity = release.build_identity(self.root)
        self.assertEqual(identity["build_id"], "main.42.gabcdef0")
        self.assertEqual(identity["stable_versions"]["marketplace"], "0.0.1")


if __name__ == "__main__":
    unittest.main()
