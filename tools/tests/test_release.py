"""Stable release math, registry, and cross-host transaction contracts."""

from __future__ import annotations

import json
import subprocess
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
        claude_entry = next(
            entry for entry in claude["plugins"]
            if entry["name"] == fixtures.PLUGIN
        )
        codex_entry = next(
            entry for entry in codex["plugins"]
            if entry["name"] == fixtures.PLUGIN
        )
        self.assertEqual(
            claude_entry["source"],
            release.channel_source("claude", fixtures.PLUGIN),
        )
        self.assertEqual(
            codex_entry["source"],
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
            ("M", f"plugins/{fixtures.PLUGIN}/flows/backlog-planning.md"),
        ]):
            self.write_changeset("docs", {})
            with self.assertRaisesRegex(release.ReleaseError, fixtures.PLUGIN):
                release.check_pr_changeset(self.root, "origin/main")

    def test_bootstrap_exception_is_narrowly_baselined(self):
        with mock.patch.object(release, "changed_paths", return_value=[
            ("A", ".changes/fixture.json"),
            ("A", "versions.json"),
            ("M", f"plugins/{fixtures.PLUGIN}/flows/backlog-planning.md"),
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
            ("M", "versions.json"),
            ("M", "CHANGELOG.md"),
            ("D", ".release/stable.json"),
            ("D", ".changes/historical-release.json"),
        ]):
            release.check_pr_changeset(
                self.root, "origin/main", allow_bootstrap=True
            )

    def test_bootstrap_reset_still_rejects_changeset_rewrites(self):
        with mock.patch.object(release, "changed_paths", return_value=[
            ("M", "versions.json"),
            ("M", ".changes/historical-release.json"),
        ]):
            with self.assertRaisesRegex(release.ReleaseError, "existing changesets"):
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

    def test_plugin_retirement_may_only_prune_release_registries(self):
        current_versions = release.load_versions(self.root)
        base_versions = json.loads(json.dumps(current_versions))
        base_versions["plugins"]["retired-team"] = "1.2.3"
        base_stable = {
            "schema_version": 1,
            "version": current_versions["marketplace"],
            "stable_base": "a" * 40,
            "main_source": "b" * 40,
            "impacts": {
                fixtures.PLUGIN: "patch",
                "retired-team": "minor",
            },
            "summaries": ["Prior release."],
        }
        current_stable = json.loads(json.dumps(base_stable))
        del current_stable["impacts"]["retired-team"]
        release.write_json(self.root / ".release" / "stable.json", current_stable)
        self.write_changeset("retire-team", {
            release.MARKETPLACE_COMPONENT: "minor",
        })

        def at_ref(_root, _ref, path):
            return base_versions if path == "versions.json" else base_stable

        changed = [
            ("A", ".changes/retire-team.json"),
            ("M", "versions.json"),
            ("M", ".release/stable.json"),
            ("D", "plugins/retired-team/constitution.md"),
        ]
        with mock.patch.object(release, "changed_paths", return_value=changed), \
                mock.patch.object(release, "json_at_ref", side_effect=at_ref):
            release.check_pr_changeset(self.root, "origin/main")

        tampered = json.loads(json.dumps(current_versions))
        tampered["plugins"][fixtures.PLUGIN] = "9.9.9"
        self.assertEqual(
            release.retirement_registry_delta(base_versions, tampered), set()
        )
        current_stable["summaries"] = ["Rewritten history."]
        self.assertFalse(release.stable_retirement_cleanup(
            base_stable, current_stable, {"retired-team"}
        ))

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


class ReleaseFinalizeTests(unittest.TestCase):
    VERSION = "1.2.3"
    FEATURE = "codex/issue-42"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        temporary = Path(self.tmp.name)
        self.remote = temporary / "remote.git"
        self.root = temporary / "work"
        self.git_run("git", "init", "--bare", str(self.remote), cwd=temporary)
        self.git_run("git", "init", "-b", "main", str(self.root), cwd=temporary)
        self.git_run("git", "config", "user.name", "Release Test")
        self.git_run("git", "config", "user.email", "release@example.test")
        (self.root / ".release").mkdir()
        (self.root / ".release" / "stable.json").write_text(json.dumps({
            "schema_version": 1,
            "version": self.VERSION,
            "stable_base": "a" * 40,
            "main_source": "b" * 40,
            "impacts": {"fixture": "patch"},
            "summaries": ["Fixture release."],
        }), encoding="utf-8")
        (self.root / "README.md").write_text("release fixture\n", encoding="utf-8")
        self.git_run("git", "add", ".")
        self.git_run("git", "commit", "-m", "release fixture")
        self.git_run("git", "tag", "-a", f"v{self.VERSION}", "-m", "release")
        self.git_run("git", "branch", "stable")
        self.git_run("git", "branch", self.FEATURE)
        self.git_run("git", "branch", "release/stable")
        self.git_run("git", "remote", "add", "origin", str(self.remote))
        self.git_run("git", "push", "origin", "main", "stable", self.FEATURE, f"v{self.VERSION}")
        self.git_run("git", "fetch", "origin", "--prune", "--tags")
        self.git_run("git", "switch", self.FEATURE)

    def tearDown(self):
        self.tmp.cleanup()

    def git_run(self, *args: str, cwd=None) -> subprocess.CompletedProcess:
        return subprocess.run(
            list(args),
            cwd=cwd or self.root,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_dry_run_audits_without_mutating(self):
        result = release.finalize_local_release(
            self.root, self.VERSION, [self.FEATURE, "release/stable"]
        )
        self.assertFalse(result["apply"])
        self.assertEqual(result["release"]["main"], result["release"]["tag"])
        self.assertEqual(
            self.git_run("git", "branch", "--show-current").stdout.strip(), self.FEATURE
        )
        self.assertTrue(release.git_ok(
            self.root, "show-ref", "--verify", "--quiet", f"refs/heads/{self.FEATURE}"
        ))

    def test_apply_deletes_only_selected_merged_refs_and_finishes_clean_main(self):
        result = release.finalize_local_release(
            self.root,
            self.VERSION,
            [self.FEATURE, "release/stable"],
            apply=True,
        )
        self.assertTrue(result["apply"])
        self.assertEqual(
            self.git_run("git", "branch", "--show-current").stdout.strip(), "main"
        )
        self.assertEqual(self.git_run("git", "status", "--porcelain").stdout, "")
        for branch in (self.FEATURE, "release/stable"):
            self.assertFalse(release.git_ok(
                self.root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"
            ))
            self.assertFalse(release.git_ok(
                self.root,
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/remotes/origin/{branch}",
            ))
        main_sha = release.git(self.root, "rev-parse", "refs/heads/main")
        self.assertEqual(
            release.git(self.root, "rev-parse", "refs/heads/stable"), main_sha
        )

    def test_unmerged_branch_is_never_deleted(self):
        (self.root / "unmerged.txt").write_text("not released\n", encoding="utf-8")
        self.git_run("git", "add", "unmerged.txt")
        self.git_run("git", "commit", "-m", "unmerged")
        self.git_run("git", "push", "origin", self.FEATURE)
        with self.assertRaisesRegex(release.ReleaseError, "unmerged"):
            release.finalize_local_release(
                self.root, self.VERSION, [self.FEATURE], apply=True
            )
        self.assertTrue(release.git_ok(
            self.root, "show-ref", "--verify", "--quiet", f"refs/heads/{self.FEATURE}"
        ))

    def test_mismatched_stable_or_unbounded_branch_fails_closed(self):
        release.validate_finalize_branch("codex/maintainer-automation-protocol")
        with self.assertRaisesRegex(release.ReleaseError, "bounded"):
            release.finalize_local_release(
                self.root, self.VERSION, ["feature/anything"]
            )
        with self.assertRaisesRegex(release.ReleaseError, "bounded"):
            release.validate_finalize_branch(
                "codex/" + "a" * release.MAX_FINALIZE_BRANCH_CHARS
            )
        (self.root / "stable-drift.txt").write_text("drift\n", encoding="utf-8")
        self.git_run("git", "add", "stable-drift.txt")
        self.git_run("git", "commit", "-m", "stable drift")
        self.git_run("git", "push", "origin", f"HEAD:refs/heads/stable")
        self.git_run("git", "fetch", "origin", "--prune", "--tags")
        with self.assertRaisesRegex(release.ReleaseError, "release refs differ"):
            release.finalize_local_release(self.root, self.VERSION, [])

    def test_divergent_local_main_is_refused_before_cleanup(self):
        self.git_run("git", "switch", "main")
        (self.root / "local-main-only.txt").write_text("local\n", encoding="utf-8")
        self.git_run("git", "add", "local-main-only.txt")
        self.git_run("git", "commit", "-m", "local main only")
        self.git_run("git", "switch", self.FEATURE)
        with self.assertRaisesRegex(release.ReleaseError, "cannot fast-forward"):
            release.finalize_local_release(
                self.root, self.VERSION, [self.FEATURE], apply=True
            )
        self.assertTrue(release.git_ok(
            self.root,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/remotes/origin/{self.FEATURE}",
        ))


if __name__ == "__main__":
    unittest.main()
