"""Stable release math, registry, and cross-host transaction contracts."""

from __future__ import annotations

import json
import os
import stat
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

    def test_derived_provenance_only_change_is_release_free(self):
        self.write_changeset("ci-hardening", {})
        provenance = build_distributions.packaging_names(self.root)[1]
        with mock.patch.object(release, "changed_paths", return_value=[
            ("A", ".changes/ci-hardening.json"),
            ("M", f"dist/claude/{fixtures.PLUGIN}/{provenance}"),
            ("M", f"dist/codex/{fixtures.PLUGIN}/{provenance}"),
        ]):
            release.check_pr_changeset(self.root, "origin/main")

    def test_non_provenance_distribution_change_requires_component(self):
        self.write_changeset("ci-hardening", {})
        with mock.patch.object(release, "changed_paths", return_value=[
            ("A", ".changes/ci-hardening.json"),
            ("M", f"dist/claude/{fixtures.PLUGIN}/constitution.md"),
        ]), self.assertRaisesRegex(release.ReleaseError, fixtures.PLUGIN):
            release.check_pr_changeset(self.root, "origin/main")

    def test_normal_pr_rejects_a_registry_reset(self):
        self.write_changeset("new", {})
        with mock.patch.object(release, "changed_paths", return_value=[
            ("A", ".changes/new.json"),
            ("M", "versions.json"),
            ("M", "CHANGELOG.md"),
            ("D", ".release/stable.json"),
            ("D", ".changes/historical-release.json"),
        ]):
            with self.assertRaises(release.ReleaseError):
                release.check_pr_changeset(self.root, "origin/main")

    def test_bootstrap_reset_still_rejects_changeset_rewrites(self):
        self.write_changeset("new", {})
        with mock.patch.object(release, "changed_paths", return_value=[
            ("A", ".changes/new.json"),
            ("M", ".changes/historical-release.json"),
        ]):
            with self.assertRaisesRegex(release.ReleaseError, "existing changesets"):
                release.check_pr_changeset(
                    self.root, "origin/main"
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

    def test_verify_bootstrap_rejects_pending_release_impact(self):
        self.write_changeset("pending-patch", {fixtures.PLUGIN: "patch"})
        with self.assertRaisesRegex(
            release.ReleaseError, "release-impact changesets"
        ):
            release.verify_bootstrap(self.root)

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


class BootstrapCandidatePolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repository"
        self.root.mkdir()
        fixtures.make_valid_root(self.root)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Bootstrap Policy Test")
        self.git("config", "user.email", "bootstrap-policy@example.test")
        self.git("add", "--all")
        self.git("commit", "-m", "bootstrap candidate")
        self.candidate_sha = self.git("rev-parse", "HEAD")
        constitution = (
            self.root / "plugins" / fixtures.PLUGIN / "constitution.md"
        )
        constitution.write_bytes(
            constitution.read_bytes() + b"\nRelease-free bootstrap replay probe.\n"
        )
        build_distributions.replace_generated(self.root, self.root / "dist")
        fixtures.write(self.root / "README.md", "release-free main advance\n")
        self.git("add", "--all")
        self.git("commit", "-m", "test: advance package and main")
        self.main_sha = self.git("rev-parse", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True,
            check=True,
        )
        return completed.stdout.strip()

    def verify(self, candidate: str | None = None) -> dict:
        return release.verify_bootstrap_candidate(
            self.root,
            candidate or self.candidate_sha,
            self.git("rev-parse", "HEAD"),
        )

    def test_release_free_main_advance_keeps_exact_candidate_valid(self):
        result = self.verify()
        self.assertEqual(result["candidate"], self.candidate_sha)
        self.assertEqual(result["main"], self.main_sha)

    def test_candidate_with_prior_stable_metadata_is_rejected(self):
        fixtures.write(
            self.root / ".release" / "stable.json",
            json.dumps({"version": "0.0.1"}) + "\n",
        )
        self.git("add", ".release/stable.json")
        self.git("commit", "-m", "inject prior stable metadata")
        candidate = self.git("rev-parse", "HEAD")
        (self.root / ".release" / "stable.json").unlink()
        self.git("add", "--all")
        self.git("commit", "-m", "remove prior stable metadata")
        with self.assertRaisesRegex(release.ReleaseError, "prior stable"):
            self.verify(candidate)

    def test_candidate_with_wrong_package_surface_is_rejected(self):
        versions_path = self.root / "versions.json"
        original = versions_path.read_bytes()
        versions = json.loads(original)
        versions["marketplace"] = "9.9.9"
        versions_path.write_bytes((json.dumps(versions, indent=2) + "\n").encode())
        self.git("add", "versions.json")
        self.git("commit", "-m", "inject wrong bootstrap version")
        candidate = self.git("rev-parse", "HEAD")
        versions_path.write_bytes(original)
        self.git("add", "versions.json")
        self.git("commit", "-m", "restore bootstrap version")
        with self.assertRaisesRegex(release.ReleaseError, "first stable release"):
            self.verify(candidate)

    def test_candidate_with_release_impact_changeset_is_rejected(self):
        self.write_changeset = self.root / ".changes" / "stranded.json"
        fixtures.write(self.write_changeset, json.dumps({
            "summary": "Stranded impact.",
            "components": {fixtures.PLUGIN: "patch"},
        }, indent=2) + "\n")
        self.git("add", str(self.write_changeset.relative_to(self.root)))
        self.git("commit", "-m", "inject stranded changeset")
        candidate = self.git("rev-parse", "HEAD")
        self.write_changeset.unlink()
        self.git("add", "--all")
        self.git("commit", "-m", "remove stranded changeset")
        with self.assertRaisesRegex(release.ReleaseError, "release-impact changeset"):
            self.verify(candidate)

    def test_candidate_adapter_code_is_never_executed(self):
        sentinel = Path(self.tmp.name) / "candidate-code-executed"
        adapter = self.root / "platforms" / "claude" / "adapter.py"
        original = adapter.read_bytes()
        adapter.write_bytes(original + (
            "\nfrom pathlib import Path as _CandidatePath\n"
            f"_CandidatePath({str(sentinel)!r}).write_text('executed')\n"
        ).encode("utf-8"))
        self.git("add", str(adapter.relative_to(self.root)))
        self.git("commit", "-m", "inject candidate adapter side effect")
        candidate = self.git("rev-parse", "HEAD")
        adapter.write_bytes(original)
        self.git("add", str(adapter.relative_to(self.root)))
        self.git("commit", "-m", "restore trusted adapter")

        with self.assertRaisesRegex(release.ReleaseError, "trusted replay"):
            self.verify(candidate)
        self.assertFalse(sentinel.exists())

    def test_candidate_distribution_rejects_force_tracked_python_cache(self):
        cache = (
            self.root / "dist" / "claude" / fixtures.PLUGIN
            / "__pycache__" / "payload.cpython-39.pyc"
        )
        cache.parent.mkdir()
        cache.write_bytes(b"unattested candidate bytecode")
        relative = cache.relative_to(self.root).as_posix()
        self.git("add", "--force", relative)
        self.git("commit", "-m", "inject candidate bytecode")
        candidate = self.git("rev-parse", "HEAD")
        self.git("rm", "--force", relative)
        self.git("commit", "-m", "remove candidate bytecode")

        with self.assertRaisesRegex(release.ReleaseError, "trusted replay"):
            self.verify(candidate)

    def test_candidate_clone_uses_file_transport_not_local_object_copy(self):
        calls: list[list[str]] = []
        real_run = subprocess.run

        def record(command, *args, **kwargs):
            calls.append(command)
            return real_run(command, *args, **kwargs)

        with mock.patch.object(release.subprocess, "run", side_effect=record):
            self.verify()

        clone_calls = [
            command for command in calls
            if command[:2] == ["git", "clone"]
        ]
        self.assertEqual(len(clone_calls), 1)
        self.assertIn("--no-local", clone_calls[0])
        self.assertNotIn("--local", clone_calls[0])
        self.assertNotIn("--no-hardlinks", clone_calls[0])


class ReleasePullRequestPolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repository"
        self.root.mkdir()
        fixtures.make_valid_root(self.root)
        fixtures.copy("tools/release.py", self.root)
        fixtures.copy("tools/build_distributions.py", self.root)
        self.git("init", "-b", "main")
        # Hosted runners may materialize text with a different checkout EOL.
        self.git("config", "core.autocrlf", "true")
        self.git("config", "user.name", "Release Policy Test")
        self.git("config", "user.email", "release-policy@example.test")
        self.git("add", "--all")
        self.git("commit", "-m", "baseline")
        self.stable_sha = self.git("rev-parse", "HEAD")
        self.git("branch", "stable")

        fixtures.write(
            self.root / ".changes" / "candidate-patch.json",
            json.dumps({
                "summary": "Ship the candidate patch.",
                "components": {fixtures.PLUGIN: "patch"},
            }, indent=2) + "\n",
        )
        self.git("add", "--all")
        self.git("commit", "-m", "feat: candidate change")
        self.base_sha = self.git("rev-parse", "HEAD")
        released = release.changeset_paths_at_ref(self.root, self.stable_sha)
        metadata = release.prepare(
            self.root,
            self.stable_sha,
            self.base_sha,
            released_paths=released,
        )
        build_distributions.replace_generated(self.root, self.root / "dist")
        self.git("add", "--all")
        self.git("commit", "-m", f"chore: prepare stable v{metadata['version']}")
        self.head_sha = self.git("rev-parse", "HEAD")
        self.git("checkout", "--detach", self.base_sha)

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=True,
        )
        return completed.stdout.strip()

    def verify(self, head_sha: str | None = None) -> dict:
        return release.verify_release_pr(
            self.root,
            base_sha=self.base_sha,
            head_sha=head_sha or self.head_sha,
            stable_sha=self.stable_sha,
        )

    def test_exact_deterministic_release_commit_is_accepted(self):
        result = self.verify()
        self.assertEqual(result["head"], self.head_sha)
        self.assertEqual(result["expected_tree"], result["head_tree"])

    def test_replay_ignores_global_excludes(self):
        excludes = Path(self.tmp.name) / "global-excludes"
        fixtures.write(excludes, ".release/stable.json\n")
        config = Path(self.tmp.name) / "global-gitconfig"
        self.git("config", "--file", str(config), "core.excludesFile", str(excludes))
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}):
            self.verify()

    def test_replay_ignores_global_attributes_and_filters(self):
        attributes = Path(self.tmp.name) / "global-attributes"
        fixtures.write(
            attributes, ".release/stable.json filter=release-replay-poison\n",
        )
        config = Path(self.tmp.name) / "global-gitconfig"
        self.git(
            "config", "--file", str(config), "core.attributesFile",
            str(attributes),
        )
        self.git(
            "config", "--file", str(config),
            "filter.release-replay-poison.clean", "git hash-object --stdin",
        )
        self.git(
            "config", "--file", str(config),
            "filter.release-replay-poison.required", "true",
        )
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}):
            self.verify()

    def test_replay_ignores_filesystem_execute_bit_loss(self):
        replace_generated = build_distributions.replace_generated

        def replace_without_execute_bits(root: Path, output: Path) -> None:
            replace_generated(root, output)
            for path in output.rglob("*"):
                if path.is_file():
                    path.chmod(path.stat().st_mode & ~(
                        stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
                    ))

        with mock.patch.object(
            build_distributions,
            "replace_generated",
            side_effect=replace_without_execute_bits,
        ):
            self.verify()

    def test_replay_is_independent_of_windows_text_translation(self):
        self.git("config", "core.autocrlf", "false")
        self.git("checkout-index", "--all", "--force")

        def windows_write_text(path: Path, value: str, *args, **kwargs) -> int:
            encoding = kwargs.get("encoding") or (args[0] if args else "utf-8")
            normalized = value.replace("\r\n", "\n").replace("\r", "\n")
            return path.write_bytes(
                normalized.replace("\n", "\r\n").encode(encoding)
            )

        released = release.changeset_paths_at_ref(self.root, self.stable_sha)
        with mock.patch.object(Path, "write_text", new=windows_write_text):
            metadata = release.prepare(
                self.root,
                self.stable_sha,
                self.base_sha,
                released_paths=released,
            )
            build_distributions.replace_generated(self.root, self.root / "dist")
        self.git("add", "--all")
        self.git("commit", "-m", f"chore: prepare stable v{metadata['version']}")
        translated_head = self.git("rev-parse", "HEAD")
        self.git("checkout", "--detach", self.base_sha)

        result = self.verify(translated_head)
        self.assertEqual(result["head"], translated_head)

    def test_replacement_ref_cannot_substitute_the_candidate_commit(self):
        self.git("checkout", "--detach", self.head_sha)
        fixtures.write(self.root / "extra.txt", "not deterministic\n")
        self.git("add", "extra.txt")
        self.git("commit", "--amend", "--no-edit")
        tampered = self.git("rev-parse", "HEAD")
        self.git("checkout", "--detach", self.base_sha)
        self.git("replace", tampered, self.head_sha)
        with self.assertRaisesRegex(release.ReleaseError, "tree differs"):
            self.verify(tampered)

    def test_legacy_graft_overlay_is_rejected(self):
        graft_value = self.git("rev-parse", "--git-path", "info/grafts")
        graft_path = Path(graft_value)
        if not graft_path.is_absolute():
            graft_path = self.root / graft_path
        fixtures.write(graft_path, f"{self.head_sha} {self.stable_sha}\n")
        with self.assertRaisesRegex(release.ReleaseError, "graft overlays"):
            self.verify()

    def test_shallow_repository_is_rejected(self):
        shallow_value = self.git("rev-parse", "--git-path", "shallow")
        shallow_path = Path(shallow_value)
        if not shallow_path.is_absolute():
            shallow_path = self.root / shallow_path
        fixtures.write(shallow_path, f"{self.stable_sha}\n")
        with self.assertRaisesRegex(release.ReleaseError, "complete Git history"):
            self.verify()

    def test_ambient_repository_environment_is_scrubbed(self):
        poisoned = {
            "GIT_ATTR_SOURCE": self.head_sha,
            "GIT_CONFIG": str(Path(self.tmp.name) / "poisoned-config"),
            "GIT_DIR": str(Path(self.tmp.name) / "poisoned-git-dir"),
            "GIT_REPLACE_REF_BASE": "refs/poisoned/",
        }
        with mock.patch.dict(os.environ, poisoned):
            environment = release.hermetic_git_environment()
        for name in poisoned:
            self.assertNotIn(name, environment)
        self.assertEqual(environment["GIT_NO_REPLACE_OBJECTS"], "1")

    def test_extra_release_commit_is_rejected(self):
        self.git("checkout", "--detach", self.head_sha)
        fixtures.write(self.root / "extra.txt", "not deterministic\n")
        self.git("add", "extra.txt")
        self.git("commit", "-m", "tamper")
        tampered = self.git("rev-parse", "HEAD")
        self.git("checkout", "--detach", self.base_sha)
        with self.assertRaisesRegex(release.ReleaseError, "exactly one"):
            self.verify(tampered)

    def test_extra_tree_content_is_rejected(self):
        self.git("checkout", "--detach", self.head_sha)
        fixtures.write(self.root / "extra.txt", "not deterministic\n")
        self.git("add", "extra.txt")
        self.git("commit", "--amend", "--no-edit")
        tampered = self.git("rev-parse", "HEAD")
        self.git("checkout", "--detach", self.base_sha)
        with self.assertRaisesRegex(release.ReleaseError, "tree differs"):
            self.verify(tampered)

    def test_abbreviated_sha_and_wrong_checkout_are_rejected(self):
        with self.assertRaisesRegex(release.ReleaseError, "40-hex"):
            release.verify_release_pr(
                self.root,
                base_sha=self.base_sha[:12],
                head_sha=self.head_sha,
                stable_sha=self.stable_sha,
            )
        self.git("checkout", "--detach", self.head_sha)
        with self.assertRaisesRegex(release.ReleaseError, "exact base SHA"):
            self.verify()


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

    def test_cleanup_accepts_a_published_release_behind_newer_main(self):
        self.git_run("git", "switch", "main")
        (self.root / "after-release.txt").write_text(
            "new main work\n", encoding="utf-8"
        )
        self.git_run("git", "add", "after-release.txt")
        self.git_run("git", "commit", "-m", "work after release")
        self.git_run("git", "push", "origin", "main")
        self.git_run("git", "fetch", "origin", "--prune", "--tags")
        self.git_run("git", "switch", self.FEATURE)

        result = release.finalize_local_release(
            self.root,
            self.VERSION,
            [self.FEATURE, "release/stable"],
            apply=True,
        )

        local_main = release.git(self.root, "rev-parse", "refs/heads/main")
        local_stable = release.git(self.root, "rev-parse", "refs/heads/stable")
        tag = release.git(self.root, "rev-list", "-n", "1", f"v{self.VERSION}")
        self.assertEqual(local_main, result["release"]["main"])
        self.assertEqual(local_stable, tag)
        self.assertNotEqual(local_main, local_stable)

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
        release.validate_finalize_branch("codex/maintainer-operations-protocol")
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


class ReleaseBranchPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        temporary = Path(self.tmp.name)
        self.remote = temporary / "remote.git"
        self.root = temporary / "work"
        self.git_run("git", "init", "--bare", str(self.remote), cwd=temporary)
        self.git_run("git", "init", "-b", "main", str(self.root), cwd=temporary)
        self.git_run("git", "config", "user.name", "Release Test")
        self.git_run("git", "config", "user.email", "release@example.test")
        self.git_run("git", "commit", "--allow-empty", "-m", "main")
        self.git_run("git", "remote", "add", "origin", str(self.remote))
        self.git_run("git", "push", "-u", "origin", "main")
        self.main_sha = release.git(self.root, "rev-parse", "HEAD")
        self.git_run("git", "switch", "-c", "release/stable")
        self.git_run("git", "commit", "--allow-empty", "-m", "release")
        self.release_sha = release.git(self.root, "rev-parse", "HEAD")

    def tearDown(self):
        self.tmp.cleanup()

    def git_run(self, *args: str, cwd=None) -> subprocess.CompletedProcess:
        return subprocess.run(
            list(args), cwd=cwd or self.root,
            capture_output=True, text=True, check=True,
        )

    def test_exact_release_branch_is_created(self):
        result = release.publish_release_branch(
            self.root, self.main_sha, self.release_sha
        )
        self.assertEqual(result["action"], "created")
        self.assertEqual(
            release.remote_release_branch_refs(self.root)["release_stable"],
            self.release_sha,
        )

    def test_main_race_exact_lease_removes_only_the_created_branch(self):
        peer = Path(self.tmp.name) / "peer"

        def advance_main() -> None:
            self.git_run(
                "git", "clone", "--branch", "main", str(self.remote),
                str(peer), cwd=Path(self.tmp.name),
            )
            self.git_run("git", "config", "user.name", "Peer", cwd=peer)
            self.git_run(
                "git", "config", "user.email", "peer@example.test", cwd=peer
            )
            self.git_run(
                "git", "commit", "--allow-empty", "-m", "advance main",
                cwd=peer,
            )
            self.git_run("git", "push", "origin", "main", cwd=peer)

        with self.assertRaisesRegex(release.ReleaseError, "rolled back"):
            release.publish_release_branch(
                self.root,
                self.main_sha,
                self.release_sha,
                after_push=advance_main,
            )
        refs = release.remote_release_branch_refs(self.root)
        self.assertIsNone(refs["release_stable"])
        self.assertNotEqual(refs["main"], self.main_sha)


class BootstrapFinalizeTests(unittest.TestCase):
    def test_bootstrap_release_reaches_the_clean_main_terminal_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            temporary_path = Path(temporary)
            remote = temporary_path / "remote.git"
            root = temporary_path / "work"

            def run(*args: str) -> subprocess.CompletedProcess:
                return subprocess.run(
                    list(args), cwd=root,
                    capture_output=True, text=True, check=True,
                )

            subprocess.run(
                ["git", "init", "--bare", str(remote)],
                cwd=temporary_path, capture_output=True, text=True, check=True,
            )
            subprocess.run(
                ["git", "init", "-b", "main", str(root)],
                cwd=temporary_path, capture_output=True, text=True, check=True,
            )
            run("git", "config", "user.name", "Bootstrap Test")
            run("git", "config", "user.email", "bootstrap@example.test")
            (root / "versions.json").write_text(json.dumps({
                "schema_version": 1,
                "marketplace": "0.0.1",
                "plugins": {"fixture": "0.0.1"},
            }), encoding="utf-8")
            run("git", "add", "versions.json")
            run("git", "commit", "-m", "bootstrap")
            run("git", "tag", "-a", "v0.0.1", "-m", "bootstrap")
            run("git", "branch", "stable")
            run("git", "branch", "codex/bootstrap-fixture")
            run("git", "branch", "release/stable")
            run("git", "remote", "add", "origin", str(remote))
            run(
                "git", "push", "origin", "main", "stable",
                "codex/bootstrap-fixture", "v0.0.1",
            )
            run("git", "fetch", "origin", "--prune", "--tags")
            run("git", "switch", "codex/bootstrap-fixture")

            result = release.finalize_local_release(
                root,
                "0.0.1",
                ["codex/bootstrap-fixture", "release/stable"],
                apply=True,
            )

            self.assertEqual(result["release"]["version"], "0.0.1")
            self.assertEqual(run("git", "branch", "--show-current").stdout.strip(), "main")
            self.assertEqual(run("git", "status", "--porcelain").stdout, "")


if __name__ == "__main__":
    unittest.main()
