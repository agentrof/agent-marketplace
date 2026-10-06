"""Stable release math, the release commit and tag-only publication contracts."""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from tools.tests.levels import integration
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

import build_distributions  # noqa: E402
import fixtures  # noqa: E402
import git_fixture  # noqa: E402
import release  # noqa: E402
import scaffold  # noqa: E402
import validate  # noqa: E402

# Release commits of the fixtures are dated here, so their versions hold in
# every month the suite runs; the date is past, so no check finds it ahead.
OCTOBER = datetime.datetime(2026, 10, 2, 12, tzinfo=datetime.timezone.utc)
VERSION = "2026.10.1"


def at(*moment: int, offset: int = 0) -> datetime.datetime:
    """A moment of the release clock, in UTC unless ``offset`` hours apart."""
    zone = datetime.timezone(datetime.timedelta(hours=offset))
    return datetime.datetime(*moment, tzinfo=zone)


def changeset(path: Path, summary: str, components: dict[str, str]) -> release.Changeset:
    return release.Changeset(path, summary, components)


class CalendarVersionTests(unittest.TestCase):
    """bump names a release YYYY.M.N by the UTC month it runs in."""

    def test_the_same_month_counts_on(self):
        self.assertEqual(release.next_version("2026.10.1", at(2026, 10, 2)), "2026.10.2")
        self.assertEqual(
            release.next_version("2026.10.9", at(2026, 10, 31, 23, 59, 59)), "2026.10.10",
        )

    def test_a_new_month_starts_at_one(self):
        for latest, moment, expected in (
            ("2026.10.3", (2026, 11, 1), "2026.11.1"),
            ("2026.9.14", (2026, 10, 3), "2026.10.1"),
            ("2026.10.2", (2027, 3, 9), "2027.3.1"),
            # The numbering before calendar versions starts the month too.
            ("0.0.3", (2026, 10, 3), "2026.10.1"),
        ):
            with self.subTest(latest=latest):
                self.assertEqual(release.next_version(latest, at(*moment)), expected)

    def test_a_new_year_starts_at_one(self):
        self.assertEqual(
            release.next_version("2026.12.4", at(2026, 12, 31, 23, 59, 59)), "2026.12.5",
        )
        self.assertEqual(release.next_version("2026.12.4", at(2027, 1, 1)), "2027.1.1")

    def test_the_month_is_the_utc_month(self):
        # 22:30 at UTC-3 on 31 October is 1 November in UTC, and 01:30 at
        # UTC+5 on 1 November is still 31 October.
        self.assertEqual(
            release.next_version("2026.10.2", at(2026, 10, 31, 22, 30, offset=-3)),
            "2026.11.1",
        )
        self.assertEqual(
            release.next_version("2026.10.2", at(2026, 11, 1, 1, 30, offset=5)),
            "2026.10.3",
        )

    def test_a_clock_behind_the_latest_release_or_without_a_zone_is_refused(self):
        with self.assertRaisesRegex(
            release.ReleaseError,
            "latest release 2026.11.1 is newer than 2026-10, the UTC month of the"
            " release clock",
        ):
            release.next_version("2026.11.1", at(2026, 10, 31, 23, 59, 59))
        with self.assertRaisesRegex(release.ReleaseError, "must carry its timezone"):
            release.next_version("2026.10.1", datetime.datetime(2026, 10, 3))

    def test_calendar_names_are_strict_semver(self):
        self.assertEqual(release.parse_semver("2026.10.1"), (2026, 10, 1))
        self.assertEqual(release.parse_semver("2027.1.12"), (2027, 1, 12))
        ordered = [
            "0.0.3", "2026.9.4", "2026.10.1", "2026.10.2", "2026.10.10",
            "2026.11.1", "2027.1.1",
        ]
        self.assertEqual(sorted(reversed(ordered), key=release.parse_semver), ordered)
        for value in (
            "2026.09.1", "2026.10.01", "v2026.10.1", "2026.10", "2026.10.1-rc.1",
            "v1.2.3", "1.2.3-beta", "01.2.3",
        ):
            with self.subTest(value=value), \
                    self.assertRaisesRegex(release.ReleaseError, "strict SemVer"):
                release.parse_semver(value)

    def test_every_component_takes_the_one_version_and_every_summary_stays(self):
        versions = {
            "marketplace": "2026.10.1",
            "plugins": {"alpha-team": "2026.10.1", "beta-team": "0.0.1"},
        }
        plan = release.release_plan(versions, [
            changeset(Path("a.json"), "patch alpha", {"alpha-team": "patch"}),
            changeset(Path("b.json"), "minor alpha", {"alpha-team": "minor"}),
            changeset(Path("c.json"), "major catalog", {
                release.MARKETPLACE_COMPONENT: "major"
            }),
            changeset(Path("d.json"), "docs", {}),
        ], at(2026, 10, 20))
        # A major impact no longer moves the number.
        self.assertEqual((plan["has_release"], plan["marketplace"]), (True, "2026.10.2"))
        self.assertEqual(plan["plugins"], {
            "alpha-team": "2026.10.2", "beta-team": "2026.10.2",
        })
        self.assertEqual(plan["impacts"], {
            "alpha-team": "minor", release.MARKETPLACE_COMPONENT: "major",
        })
        self.assertEqual(
            plan["summaries"], ["patch alpha", "minor alpha", "major catalog", "docs"],
        )

    def test_empty_components_do_not_create_a_release(self):
        versions = {"marketplace": "0.0.1", "plugins": {"team": "0.0.1"}}
        plan = release.release_plan(versions, [
            changeset(Path("docs.json"), "docs", {})
        ], OCTOBER)
        self.assertFalse(plan["has_release"])
        self.assertEqual(plan["marketplace"], "0.0.1")
        self.assertEqual(plan["plugins"]["team"], "0.0.1")


@integration
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

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True,
            check=True,
        ).stdout.strip()

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
        metadata = release.prepare_release(self.root, OCTOBER)
        versions = release.load_versions(self.root)
        self.assertEqual(versions["marketplace"], VERSION)
        self.assertEqual(versions["plugins"][fixtures.PLUGIN], VERSION)
        self.assertEqual(sorted(metadata), sorted(release.METADATA_KEYS))
        self.assertEqual(
            (metadata["schema_version"], metadata["version"], metadata["impacts"]),
            (2, VERSION, {fixtures.PLUGIN: "minor"}),
        )
        changelog = (self.root / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn(f"\n## {VERSION}\n", changelog)
        self.assertEqual(
            release.changelog_section(changelog, VERSION),
            "- Fixture baseline.\n- Apply minor-team.\n- Apply patch-team.\n",
        )
        self.assertEqual(list((self.root / ".changes").glob("*.json")), [])
        self.assertEqual(release.validate_version_surfaces(self.root), [])
        for host in ("claude", "codex"):
            manifest = json.loads((
                self.root / "dist" / host / fixtures.PLUGIN
                / f".{host}-plugin" / "plugin.json"
            ).read_text(encoding="utf-8"))
            self.assertEqual(manifest["version"], VERSION)

    def test_prepare_refuses_release_free_changesets_without_writes(self):
        versions_before = (self.root / "versions.json").read_bytes()
        with self.assertRaisesRegex(release.ReleaseError, "no pending"):
            release.prepare_release(self.root, OCTOBER)
        self.assertEqual((self.root / "versions.json").read_bytes(), versions_before)

    def test_one_version_reaches_every_version_surface(self):
        # A second plugin at another version, without an impact of its own,
        # takes the release version on every surface too.
        with git_fixture.temporary_directory() as temporary:
            root = Path(temporary)
            fixtures.make_valid_root(root, "0.0.3")
            with contextlib.redirect_stdout(io.StringIO()):
                scaffold.new_plugin(root, "sample-team")
            plugins = (fixtures.PLUGIN, "sample-team")
            self.assertEqual(release.load_versions(root)["plugins"], {
                fixtures.PLUGIN: "0.0.3", "sample-team": "0.0.1",
            })
            fixtures.write(root / ".changes" / "team-patch.json", json.dumps({
                "summary": "Patch the fixture team.",
                "components": {fixtures.PLUGIN: "patch"},
            }))
            release.prepare_release(root, OCTOBER)

            def version_of(relative: str, *keys: str) -> str:
                value = release.read_json(root / relative)
                for key in keys:
                    value = value[key]
                return value

            versions = release.load_versions(root)
            surfaces = {"versions.json marketplace": versions["marketplace"]}
            surfaces.update({
                f"versions.json {plugin}": value
                for plugin, value in versions["plugins"].items()
            })
            claude = release.read_json(root / ".claude-plugin" / "marketplace.json")
            surfaces["claude catalog"] = claude["metadata"]["version"]
            surfaces.update({
                f"claude catalog {entry['name']}": entry["version"]
                for entry in claude["plugins"]
            })
            provenance = build_distributions.packaging_names(root)[1]
            for host in ("claude", "codex"):
                for plugin in plugins:
                    package = f"dist/{host}/{plugin}"
                    surfaces.update({
                        f"{host} {plugin} source manifest": version_of(
                            f"platforms/{host}/{plugin}/manifest.json", "version",
                        ),
                        f"{host} {plugin} manifest": version_of(
                            f"{package}/.{host}-plugin/plugin.json", "version",
                        ),
                        f"{host} {plugin} provenance": version_of(
                            f"{package}/{provenance}", "version",
                        ),
                        f"{host} {plugin} provenance release": version_of(
                            f"{package}/{provenance}", "marketplace_release",
                        ),
                    })
            surfaces["release metadata"] = version_of(release.STABLE_METADATA, "version")
            self.assertEqual(len(surfaces), 23, surfaces)
            self.assertEqual(set(surfaces.values()), {VERSION}, surfaces)
            self.assertEqual(release.verify_release(root)["version"], VERSION)
            # A release whose plugin kept another version is no release.
            versions["plugins"]["sample-team"] = "0.0.1"
            release.write_json(root / "versions.json", versions)
            release.sync_version_surfaces(root, versions)
            build_distributions.replace_generated(root, root / "dist")
            self.assertEqual(release.validate_version_surfaces(root), [])
            with self.assertRaisesRegex(
                release.ReleaseError,
                f"every plugin carries the release version {VERSION}; sample-team does not",
            ):
                release.verify_release(root)

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

    def test_runtime_contract_change_without_its_component_is_refused(self):
        # A host runtime contract changes only the package provenance, yet it
        # changes what the package declares, so it needs its impact (#343).
        before = file_map(self.root / "dist")
        adapter = self.root / "platforms" / "claude" / "adapter.py"
        current = 'return ["in_use_pid_marker_v1"]'
        self.assertIn(current, adapter.read_text(encoding="utf-8"))
        adapter.write_text(adapter.read_text(encoding="utf-8").replace(
            current, 'return ["in_use_pid_marker_v1", "future_marker_v2"]',
        ), encoding="utf-8")
        build_distributions.replace_generated(self.root, self.root / "dist")
        after = file_map(self.root / "dist")
        provenance = build_distributions.packaging_names(self.root)[1]
        distribution_changes = sorted(
            f"dist/{path}" for path in before.keys() | after.keys()
            if before.get(path) != after.get(path)
        )
        self.assertEqual(distribution_changes, [f"dist/claude/{fixtures.PLUGIN}/{provenance}"])
        changeset_path = ".changes/runtime-contract.json"
        changed = [
            ("A", changeset_path), ("M", "platforms/claude/adapter.py"),
            *(("M", path) for path in distribution_changes),
        ]
        plugins = release.load_versions(self.root)["plugins"]
        hosts = build_distributions.load_adapters(self.root)
        with self.assertRaisesRegex(
            release.ReleaseError, f"omits changed release components: {fixtures.PLUGIN}",
        ):
            release.changeset_components_rule(
                changed, plugins, hosts, [changeset_path], set(),
            )
        self.assertEqual(release.changeset_components_rule(
            changed, plugins, hosts, [changeset_path], {fixtures.PLUGIN},
        ), {"mode": "changeset"})

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


RESET_RETIRED = ["0.0.1", "0.1.0", "0.2.0"]
RETIRED_CHANGELOG = (
    "# Changelog\n\n"
    "## 0.0.1\n\n- Establish the retired baseline of the reset fixture.\n\n"
    "## 0.1.0\n\n- Ship the first retired feature of the reset fixture.\n\n"
    "## 0.2.0\n\n- Ship the second retired feature of the reset fixture.\n"
)
BOOTSTRAP_CHANGELOG = (
    "# Changelog\n\n## 0.0.1\n\n"
    "- Establish the first stable Agent Marketplace baseline for all supported hosts.\n"
)


def reset_marker(**changes: object) -> dict:
    return {
        "schema_version": 1,
        "reason": "Restart stable numbering at the bootstrap version.",
        "date": "2026-10-01",
        "retired_versions": list(RESET_RETIRED),
        **changes,
    }


def set_every_version(root: Path, version: str) -> None:
    plugins = release.load_versions(root)["plugins"]
    versions = {
        "schema_version": 1,
        "marketplace": version,
        "plugins": {name: version for name in plugins},
    }
    release.write_json(root / "versions.json", versions)
    release.sync_version_surfaces(root, versions)
    build_distributions.replace_generated(root, root / "dist")


def apply_release_reset(root: Path) -> None:
    """Make every edit of the complete one-time reset."""
    set_every_version(root, release.BOOTSTRAP_VERSION)
    (root / ".release" / "stable.json").unlink()
    for path in (root / ".changes").glob("*.json"):
        path.unlink()
    fixtures.write(root / "CHANGELOG.md", BOOTSTRAP_CHANGELOG)
    release.write_json(root / ".release" / "reset.json", reset_marker())


@integration
class ReleaseResetPolicyTests(unittest.TestCase):
    """The one-time reset marker, once merged, is part of every later base and never changes."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "repository"
        cls.root.mkdir()
        fixtures.make_valid_root(cls.root, "0.2.0")
        fixtures.write(cls.root / ".changes" / "README.md", "# Changesets\n")
        fixtures.write(cls.root / ".changes" / "pending-feature.json", json.dumps({
            "summary": "Ship a pending feature.",
            "components": {fixtures.PLUGIN: "minor"},
        }, indent=2) + "\n")
        fixtures.write(cls.root / "CHANGELOG.md", RETIRED_CHANGELOG)
        release.write_json(cls.root / ".release" / "stable.json", {
            "schema_version": 1,
            "version": "0.2.0",
            "stable_base": "a" * 40,
            "main_source": "b" * 40,
            "impacts": {fixtures.PLUGIN: "minor"},
            "summaries": ["Ship the second retired feature of the reset fixture."],
        })
        git_fixture.init_repository(cls.root, initial_branch="main")
        cls.git("config", "user.name", "Release Reset Test")
        cls.git("config", "user.email", "release-reset@example.test")
        cls.git("add", "--all")
        cls.git("commit", "-qm", "a released line at 0.2.0")
        cls.base_sha = cls.git("rev-parse", "HEAD")
        apply_release_reset(cls.root)
        cls.git("add", "--all")
        cls.git("commit", "-qm", "chore(release): restart stable numbering")
        cls.reset_sha = cls.git("rev-parse", "HEAD")

    @classmethod
    def tearDownClass(cls):
        git_fixture.remove_temporary(cls.tmp)

    @classmethod
    def git(cls, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=cls.root, capture_output=True, text=True,
            check=True,
        ).stdout.strip()

    def setUp(self):
        self.start_from(self.reset_sha)

    def start_from(self, revision: str) -> None:
        self.git("checkout", "-q", "--detach", "--force", revision)
        self.git("clean", "-qfdx")

    def commit(self, message: str) -> str:
        self.git("add", "--all")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def check(self, base: str | None = None) -> dict:
        return release.check_pr_changeset(self.root, base or self.base_sha)

    def assert_refused(self, cases: dict, base: str | None = None) -> None:
        for name, (edit, message) in cases.items():
            with self.subTest(name):
                self.start_from(self.reset_sha)
                edit()
                self.commit(name)
                with self.assertRaisesRegex(release.ReleaseError, message):
                    self.check(base)

    def test_the_marker_is_added_once_and_never_changed(self):
        # Once merged, the marker is part of every later base.
        self.assert_refused({
            "edited": (
                lambda: release.write_json(
                    self.root / ".release" / "reset.json",
                    reset_marker(reason="Restart once more."),
                ),
                "may only add it",
            ),
            "deleted": (
                lambda: (self.root / ".release" / "reset.json").unlink(),
                "may only add it",
            ),
            "renamed": (
                lambda: (self.root / ".release" / "reset.json").rename(
                    self.root / ".release" / "earlier-reset.json"
                ),
                "may only add it",
            ),
        }, base=self.reset_sha)


class ReleaseResetMarkerRuleTests(unittest.TestCase):
    """The merged reset marker may only be added, decided without Git."""

    def test_the_marker_is_added_once_and_never_changed(self):
        merged = (json.dumps(reset_marker(), indent=2) + "\n").encode("utf-8")
        # An edit keeps the path; a deletion or a rename leaves it empty.
        for name, present in (("edited", True), ("deleted", False), ("renamed", False)):
            with self.subTest(name), \
                    self.assertRaisesRegex(release.ReleaseError, "may only add it"):
                release.require_reset_marker_added(merged, present)
        release.require_reset_marker_added(None, True)


def file_map(root: Path) -> dict[str, bytes]:
    """Every file below ``root`` by its POSIX path, with its exact bytes."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def file_digests(root: Path) -> dict[str, str]:
    """Every file below ``root`` by its POSIX path, with its SHA-256."""
    return {path: hashlib.sha256(data).hexdigest() for path, data in file_map(root).items()}


def write_changeset(
    root: Path, name: str, components: dict[str, str], summary: str | None = None,
) -> None:
    fixtures.write(root / ".changes" / f"{name}.json", json.dumps({
        "summary": summary or f"Apply {name}.",
        "components": components,
    }, indent=2) + "\n")


def change_package(root: Path, note: str) -> None:
    constitution = root / "plugins" / fixtures.PLUGIN / "constitution.md"
    constitution.write_bytes(constitution.read_bytes() + f"\n{note}\n".encode("utf-8"))
    build_distributions.replace_generated(root, root / "dist")


def bump_at(root: Path, when: datetime.datetime = OCTOBER) -> str:
    """Make the release commit as `bump` does, with the release clock at ``when``."""
    return release.commit_release(root, now=lambda: when)["commit"]


def commit_release_fixture(case, when: datetime.datetime = OCTOBER) -> None:
    """Commit a main baseline, one feature and its release commit on top."""
    case.tmp = tempfile.TemporaryDirectory()
    case.root = Path(case.tmp.name) / "repository"
    case.root.mkdir()
    fixtures.make_valid_root(case.root)
    fixtures.write(case.root / ".changes" / "README.md", "# Changesets\n")
    fixtures.copy("tools/release.py", case.root)
    fixtures.copy("tools/build_distributions.py", case.root)
    git_fixture.init_repository(case.root, initial_branch="main")
    # Hosted runners may materialize text with a different checkout EOL.
    case.git("config", "core.autocrlf", "true")
    case.git("config", "user.name", "Release Policy Test")
    case.git("config", "user.email", "release-policy@example.test")
    case.git("add", "--all")
    case.git("commit", "-qm", "baseline")
    case.base_sha = case.git("rev-parse", "HEAD")
    case.git("switch", "-qc", "feature")
    write_changeset(
        case.root, "candidate-patch", {fixtures.PLUGIN: "patch"},
        "Ship the candidate patch.",
    )
    change_package(case.root, "Release commit probe.")
    case.git("add", "--all")
    case.git("commit", "-qm", "feat: candidate change")
    case.feature_sha = case.git("rev-parse", "HEAD")
    case.head_sha = bump_at(case.root, when)


@integration
class ReleaseCommitPolicyTests(unittest.TestCase):
    """check-pr accepts release-owned changes only as the deterministic bump."""

    @classmethod
    def setUpClass(cls):
        commit_release_fixture(cls)

    @classmethod
    def tearDownClass(cls):
        git_fixture.remove_temporary(cls.tmp)

    @classmethod
    def git(cls, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=cls.root, capture_output=True, text=True,
            check=True,
        )
        return completed.stdout.strip()

    def setUp(self):
        # Every test starts from the release commit on the shared fixture.
        self.git("checkout", "-q", "--detach", "--force", self.head_sha)
        self.git("clean", "-qfdx")
        self.git("branch", "-f", "main", self.base_sha)
        self.git("branch", "-f", "feature", self.head_sha)
        self.git("config", "core.autocrlf", "true")

    def tearDown(self):
        for name in ("info/grafts", "shallow"):
            path = Path(self.git("rev-parse", "--git-path", name))
            if not path.is_absolute():
                path = self.root / path
            if path.exists():
                path.unlink()
        replacements = self.git("replace", "--list").split()
        if replacements:
            self.git("replace", "-d", *replacements)

    def check(self, base: str | None = None, head: str = "HEAD") -> dict:
        return release.check_pr_changeset(self.root, base or self.base_sha, head)

    def amend(self, edit) -> str:
        """Replace the release commit by an edited variant and return it."""
        self.git("checkout", "-q", "--detach", self.head_sha)
        edit()
        self.git("add", "--all")
        self.git("commit", "-q", "--amend", "--no-edit")
        return self.git("rev-parse", "HEAD")

    def test_the_exact_release_commit_is_accepted(self):
        self.assertEqual(self.check(), {"mode": "release", "version": VERSION})
        self.assertEqual(
            self.git("log", "-1", "--format=%s", self.head_sha),
            f"chore: release v{VERSION}",
        )

    def test_a_pull_request_of_the_release_commit_alone_is_accepted(self):
        # The feature merged first; the pull request holds only the bump.
        self.assertEqual(
            self.check(self.feature_sha), {"mode": "release", "version": VERSION},
        )

    def test_ci_checks_the_pull_request_head_beside_its_merge_commit(self):
        self.git("checkout", "-q", "--detach", self.base_sha)
        self.git("merge", "-q", "--no-ff", "-m", "Merge pull request #9", self.head_sha)
        self.assertEqual(self.check(head=self.head_sha)["mode"], "release")
        with self.assertRaisesRegex(release.ReleaseError, "2 parents, not one"):
            self.check()

    def test_every_edit_of_the_release_commit_is_refused(self):
        metadata_path = self.root / release.STABLE_METADATA

        def forge_build_identity() -> None:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            release.write_json(
                metadata_path, dict(metadata, build_id="snapshot." + "0" * 64),
            )

        def rewrite_notes() -> None:
            changelog = self.root / "CHANGELOG.md"
            changelog.write_text(changelog.read_text(encoding="utf-8").replace(
                "Ship the candidate patch.", "Ship another patch.",
            ), encoding="utf-8")

        def keep_a_changeset() -> None:
            self.git("checkout", self.feature_sha, "--", ".changes/candidate-patch.json")

        def add_a_file() -> None:
            fixtures.write(self.root / "extra.txt", "not deterministic\n")

        def drop_an_execute_bit() -> None:
            path = self.root / "dist" / "claude" / fixtures.PLUGIN / "scripts" / "backlog_compile.py"
            path.chmod(path.stat().st_mode & ~(stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
            self.git("update-index", "--chmod=-x", path.relative_to(self.root).as_posix())

        edits = {
            "build identity": forge_build_identity, "notes": rewrite_notes,
            "kept changeset": keep_a_changeset, "extra file": add_a_file,
        }
        if os.name != "nt":
            edits["execute bit"] = drop_an_execute_bit
        for name, edit in edits.items():
            with self.subTest(name):
                self.amend(edit)
                with self.assertRaisesRegex(
                    release.ReleaseError,
                    "differs from the deterministic bump of its parent",
                ):
                    self.check()

    def test_a_hand_edit_of_a_release_owned_file_is_refused(self):
        self.git("checkout", "-q", "--detach", self.feature_sha)
        versions = release.load_versions(self.root)
        versions["marketplace"] = VERSION
        release.write_json(self.root / "versions.json", versions)
        self.git("add", "--all")
        self.git("commit", "-qm", "chore: edit the marketplace version by hand")
        with self.assertRaisesRegex(
            release.ReleaseError,
            r"cannot edit release-owned files: versions.json\. Only a release commit",
        ):
            self.check()

    def test_commits_before_the_release_commit_keep_the_changeset_rules(self):
        self.git("checkout", "-q", "--detach", self.base_sha)
        write_changeset(
            self.root, "wrong-component", {release.MARKETPLACE_COMPONENT: "patch"},
        )
        change_package(self.root, "An undeclared package change.")
        self.git("add", "--all")
        self.git("commit", "-qm", "feat: undeclared package change")
        bump_at(self.root)
        with self.assertRaisesRegex(
            release.ReleaseError,
            "break the changeset rules: changeset omits changed release"
            f" components: {fixtures.PLUGIN}",
        ):
            self.check()

    @unittest.skipIf(os.name == "nt", "the filesystem keeps no execute bits")
    def test_a_new_package_executable_gets_its_declared_mode(self):
        self.git("checkout", "-q", "--detach", self.base_sha)
        script = self.root / "plugins" / fixtures.PLUGIN / "scripts" / "release_probe.py"
        script.write_text('"""Release commit mode probe."""\n', encoding="utf-8")
        modes_path = self.root / "package-modes.json"
        modes = json.loads(modes_path.read_text(encoding="utf-8"))
        executables = modes["packages"][fixtures.PLUGIN]["executables"]
        executables.append("scripts/release_probe.py")
        executables.sort()
        modes_path.write_text(json.dumps(modes, indent=2) + "\n", encoding="utf-8")
        write_changeset(self.root, "probe", {fixtures.PLUGIN: "patch"})
        self.git("add", "--all")
        self.git("commit", "-qm", "feat: a new executable before its distributions")
        bump_at(self.root)
        staged = self.git(
            "ls-files", "-s", f"dist/codex/{fixtures.PLUGIN}/scripts/release_probe.py",
        )
        self.assertTrue(staged.startswith("100755 "), staged)
        self.assertEqual(self.check()["mode"], "release")
        # The replay ignores filesystem modes; the provenance supplies them.
        with mock.patch.object(release, "apply_package_index_modes"), \
                self.assertRaisesRegex(release.ReleaseError, "deterministic bump"):
            self.check()

    def test_replay_ignores_global_excludes(self):
        excludes = Path(self.tmp.name) / "global-excludes"
        fixtures.write(excludes, ".release/stable.json\n")
        config = Path(self.tmp.name) / "global-gitconfig"
        self.git("config", "--file", str(config), "core.excludesFile", str(excludes))
        with mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config)}):
            self.assertEqual(self.check()["mode"], "release")

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
            self.assertEqual(self.check()["mode"], "release")

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
            self.assertEqual(self.check()["mode"], "release")

    def test_replay_is_independent_of_windows_text_translation(self):
        self.git("checkout", "-q", "--detach", self.feature_sha)
        self.git("config", "core.autocrlf", "false")
        self.git("checkout-index", "--all", "--force")

        def windows_write_text(path: Path, value: str, *args, **kwargs) -> int:
            encoding = kwargs.get("encoding") or (args[0] if args else "utf-8")
            normalized = value.replace("\r\n", "\n").replace("\r", "\n")
            return path.write_bytes(
                normalized.replace("\n", "\r\n").encode(encoding)
            )

        with mock.patch.object(Path, "write_text", new=windows_write_text):
            bump_at(self.root)
        self.assertEqual(self.check()["mode"], "release")

    def test_replacement_ref_cannot_substitute_the_release_commit(self):
        tampered = self.amend(
            lambda: fixtures.write(self.root / "extra.txt", "not deterministic\n"),
        )
        self.git("checkout", "-q", "--detach", self.base_sha)
        self.git("replace", tampered, self.head_sha)
        with self.assertRaisesRegex(release.ReleaseError, "tree differs"):
            release.verify_release_commit(self.root, self.base_sha, tampered)

    def test_legacy_graft_overlay_is_rejected(self):
        graft_value = self.git("rev-parse", "--git-path", "info/grafts")
        graft_path = Path(graft_value)
        if not graft_path.is_absolute():
            graft_path = self.root / graft_path
        fixtures.write(graft_path, f"{self.head_sha} {self.base_sha}\n")
        with self.assertRaisesRegex(release.ReleaseError, "graft overlays"):
            self.check()

    def test_shallow_repository_is_rejected(self):
        shallow_value = self.git("rev-parse", "--git-path", "shallow")
        shallow_path = Path(shallow_value)
        if not shallow_path.is_absolute():
            shallow_path = self.root / shallow_path
        fixtures.write(shallow_path, f"{self.base_sha}\n")
        with self.assertRaisesRegex(release.ReleaseError, "complete Git history"):
            self.check()

    def test_a_release_commit_made_before_the_base_advanced_is_refused(self):
        self.git("checkout", "-q", "main")
        write_changeset(self.root, "later-fix", {fixtures.PLUGIN: "patch"})
        change_package(self.root, "A later fix on main.")
        self.git("add", "--all")
        self.git("commit", "-qm", "fix: a later fix on main")
        advanced = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "--detach", self.head_sha)
        with self.assertRaisesRegex(
            release.ReleaseError, "base advanced after the release commit was made",
        ):
            self.check(advanced)


@integration
class ReleaseMonthBoundaryTests(unittest.TestCase):
    """check-pr replays a release commit at its own date: it stays valid for
    the month it was made in, whenever its pull request merges."""

    LAST_SECOND = at(2026, 10, 31, 23, 59, 59) + datetime.timedelta(microseconds=999999)

    @classmethod
    def setUpClass(cls):
        commit_release_fixture(cls, cls.LAST_SECOND)

    @classmethod
    def tearDownClass(cls):
        git_fixture.remove_temporary(cls.tmp)

    @classmethod
    def git(cls, *args: str, **environment: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=cls.root, capture_output=True, text=True,
            check=True, env={**os.environ, **environment},
        )
        return completed.stdout.strip()

    def setUp(self):
        self.git("checkout", "-q", "--detach", "--force", self.head_sha)
        self.git("clean", "-qfdx")

    def check(self, now: datetime.datetime) -> dict:
        return release.check_pr_changeset(self.root, self.base_sha, now=lambda: now)

    def test_bump_dates_its_commit_at_the_instant_it_named_the_version(self):
        # Truncated to whole seconds, the month's last second stays in it.
        second = str(int(at(2026, 10, 31, 23, 59, 59).timestamp()))
        self.assertEqual(self.git("log", "-1", "--format=%ct %at").split(), [second, second])
        self.assertEqual(release.load_versions(self.root)["marketplace"], VERSION)

    def test_the_committer_date_decides_the_month(self):
        def redate(author: datetime.datetime, committer: datetime.datetime) -> None:
            self.git("checkout", "-q", "--detach", self.head_sha)
            self.git(
                "commit", "-q", "--amend", "--no-edit", f"--date={author.isoformat()}",
                GIT_COMMITTER_DATE=committer.isoformat(),
            )

        redate(at(2026, 10, 31, 23, 59, 59), at(2026, 11, 1))
        with self.assertRaisesRegex(
            release.ReleaseError, "differs from the deterministic bump of its parent",
        ):
            self.check(at(2026, 11, 2))
        redate(at(2026, 11, 5), at(2026, 10, 31, 23, 59, 59))
        self.assertEqual(self.check(at(2026, 11, 6)), {"mode": "release", "version": VERSION})

    def test_a_release_commit_of_a_month_not_begun_is_refused(self):
        # Only a clock that runs ahead dates a commit in a later month.
        self.git("checkout", "-q", "--detach", self.feature_sha)
        bump_at(self.root, at(2026, 11, 1, 0, 0, 1))
        with self.assertRaisesRegex(
            release.ReleaseError,
            "it is dated 2026-11-01, in a month that has not begun; fix the clock",
        ):
            self.check(at(2026, 10, 31, 23, 59, 59))
        self.assertEqual(
            self.check(at(2026, 11, 1, 0, 0, 2)), {"mode": "release", "version": "2026.11.1"},
        )


@integration
class BumpCommandTests(unittest.TestCase):
    """The maintainer's one command for the release commit."""

    def setUp(self):
        commit_release_fixture(self)
        self.git("checkout", "-q", "--detach", self.feature_sha)

    def tearDown(self):
        git_fixture.remove_temporary(self.tmp)

    def git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=self.root, capture_output=True, text=True,
            check=True,
        )
        return completed.stdout.strip()

    def cli(self, *arguments: str) -> subprocess.CompletedProcess:
        environment = {key: value for key, value in os.environ.items()
                       if key != release.PRIVATE_TERMS_VARIABLE}
        return subprocess.run(
            [sys.executable, str(TESTS_DIR.parent / "release.py"),
             "--root", str(self.root), *arguments],
            capture_output=True, text=True, check=False, env=environment,
        )

    def test_bump_makes_the_release_commit_check_pr_accepts(self):
        made = self.cli("bump")
        self.assertEqual(made.returncode, 0, made.stderr)
        result = json.loads(made.stdout)
        # The command reads the real clock; its commit records the instant.
        committed, authored = self.git("log", "-1", "--format=%ct %at").split()
        self.assertEqual(committed, authored)
        version = release.next_version("0.0.1", datetime.datetime.fromtimestamp(
            int(committed), datetime.timezone.utc,
        ))
        self.assertEqual(
            (result["version"], result["message"]),
            (version, f"chore: release v{version}"),
        )
        self.assertEqual(result["commit"], self.git("rev-parse", "HEAD"))
        self.assertEqual(self.git("rev-parse", "HEAD^"), self.feature_sha)
        self.assertEqual(self.git("status", "--porcelain"), "")
        checked = self.cli("check-pr", "--base", self.base_sha)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(checked.stdout.splitlines()[0], (
            "release: release commit valid; it is the deterministic bump of its"
            f" parent to v{version}"
        ))

class ReleaseCommitRuleTests(unittest.TestCase):
    """check-pr's release commit rules, decided in process on a bumped tree."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "repository"
        cls.root.mkdir()
        fixtures.make_valid_root(cls.root)
        fixtures.write(cls.root / ".changes" / "README.md", "# Changesets\n")
        fixtures.copy("tools/release.py", cls.root)
        fixtures.copy("tools/build_distributions.py", cls.root)
        write_changeset(
            cls.root, "candidate-patch", {fixtures.PLUGIN: "patch"},
            "Ship the candidate patch.",
        )
        change_package(cls.root, "Release commit probe.")
        cls.parent = file_map(cls.root)
        cls.metadata = release.bump_parent(cls.root, OCTOBER)
        cls.bumped = file_map(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_a_commit_after_the_release_commit_is_refused(self):
        # The commits before the last one already hold a release or a reset.
        for earlier in ({"mode": "release", "version": VERSION},
                        {"mode": "reset", "version": release.BOOTSTRAP_VERSION}):
            with self.subTest(earlier["mode"]), \
                    self.assertRaisesRegex(release.ReleaseError, "already make a release"):
                release.require_changeset_commits(lambda: earlier)
        release.require_changeset_commits(lambda: {"mode": "changeset"})

    def test_commits_that_break_the_changeset_rules_refuse_the_release_commit(self):
        changeset_path = ".changes/wrong-component.json"
        changed = [
            ("A", changeset_path),
            ("M", f"plugins/{fixtures.PLUGIN}/constitution.md"),
            ("M", f"dist/claude/{fixtures.PLUGIN}/constitution.md"),
        ]
        plugins = release.load_versions(self.root)["plugins"]
        hosts = build_distributions.load_adapters(self.root)
        with self.assertRaisesRegex(
            release.ReleaseError,
            "break the changeset rules: changeset omits changed release"
            f" components: {fixtures.PLUGIN}",
        ):
            release.require_changeset_commits(lambda: release.changeset_components_rule(
                changed, plugins, hosts, [changeset_path],
                {release.MARKETPLACE_COMPONENT},
            ))

    def test_a_release_commit_made_before_the_base_advanced_is_refused(self):
        asked: list[str] = []

        def base_is_ancestor(parent: str) -> bool:
            asked.append(parent)
            return False

        with self.assertRaisesRegex(
            release.ReleaseError, "base advanced after the release commit was made",
        ):
            release.release_commit_parent(["a" * 40], base_is_ancestor)
        self.assertEqual(asked, ["a" * 40])
        self.assertEqual(release.release_commit_parent(["a" * 40], lambda _p: True), "a" * 40)

    def test_a_merge_of_the_base_as_the_last_commit_is_refused(self):
        def never_asked(parent: str) -> bool:
            raise AssertionError(parent)

        with self.assertRaisesRegex(release.ReleaseError, "2 parents, not one"):
            release.release_commit_parent(["a" * 40, "b" * 40], never_asked)

    @integration
    def test_a_parent_without_release_impact_cannot_be_bumped(self):
        with git_fixture.temporary_directory() as temporary:
            root = Path(temporary)
            fixtures.make_valid_root(root)
            before = file_digests(root)
            with self.assertRaisesRegex(
                release.ReleaseError, "cannot be bumped: no pending stable release impact",
            ):
                release.bump_parent(root, OCTOBER)
            self.assertEqual(file_digests(root), before)

    def test_ambient_repository_environment_is_scrubbed(self):
        poisoned = {
            "GIT_ATTR_SOURCE": "a" * 40,
            "GIT_CONFIG": str(Path(self.tmp.name) / "poisoned-config"),
            "GIT_DIR": str(Path(self.tmp.name) / "poisoned-git-dir"),
            "GIT_REPLACE_REF_BASE": "refs/poisoned/",
        }
        with mock.patch.dict(os.environ, poisoned):
            environment = release.hermetic_git_environment()
        for name in poisoned:
            self.assertNotIn(name, environment)
        self.assertEqual(environment["GIT_NO_REPLACE_OBJECTS"], "1")

    def test_release_records_the_build_identity_of_its_sources(self):
        # Packages carry no shared build identity (#311); the release metadata
        # records it and release verification recomputes it.
        path = self.root / release.STABLE_METADATA
        self.addCleanup(path.write_bytes, path.read_bytes())
        metadata = release.read_json(path)
        self.assertEqual(sorted(metadata), sorted(release.METADATA_KEYS))
        self.assertEqual(
            metadata["build_id"],
            build_distributions.marketplace_snapshot(self.root)["build_id"],
        )
        self.assertEqual(release.verify_release(self.root), metadata)
        release.write_json(path, dict(metadata, build_id="snapshot." + "0" * 64))
        with self.assertRaisesRegex(release.ReleaseError, "build identity"):
            release.verify_release(self.root)
        release.write_json(path, dict(
            metadata, schema_version=1, stable_base="a" * 40, main_source="b" * 40,
        ))
        with self.assertRaisesRegex(release.ReleaseError, "schema_version 2"):
            release.verify_release(self.root)


class ReleaseMonthRuleTests(unittest.TestCase):
    """check-pr replays a release commit at its own date, decided without Git."""

    LAST_SECOND = ReleaseMonthBoundaryTests.LAST_SECOND

    def test_a_release_commit_merged_in_a_later_month_keeps_its_month(self):
        made = release.release_instant(self.LAST_SECOND)
        for now in (at(2026, 11, 1, 0, 0, 5), at(2026, 12, 15), at(2027, 1, 2)):
            with self.subTest(now=now):
                when = release.release_replay_instant(made, now)
                self.assertEqual(release.next_version("0.0.1", when), VERSION)
        # Once it is released, the next release commit starts November at 1.
        self.assertEqual(release.next_version(VERSION, at(2026, 11, 1, 9)), "2026.11.1")

    def test_a_release_commit_of_a_month_not_begun_is_refused(self):
        # Only a clock that runs ahead dates a commit in a later month.
        made = at(2026, 11, 1, 0, 0, 1)
        with self.assertRaisesRegex(
            release.ReleaseError,
            "it is dated 2026-11-01, in a month that has not begun; fix the clock",
        ):
            release.release_replay_instant(made, at(2026, 10, 31, 23, 59, 59))
        when = release.release_replay_instant(made, at(2026, 11, 1, 0, 0, 2))
        self.assertEqual(release.next_version("0.0.1", when), "2026.11.1")


@integration
class BumpRuleTests(unittest.TestCase):
    """bump refuses before it writes, decided without Git."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        fixtures.make_valid_root(self.root)
        self.calls: list[tuple[str, ...]] = []

    def bump(self, status: str) -> None:
        def git(_root: Path, *args: str, environment=None) -> str:
            self.calls.append(args)
            if args[0] == "status":
                return status
            raise AssertionError(args)

        with mock.patch.object(release, "git", side_effect=git):
            release.commit_release(self.root, now=lambda: OCTOBER)

    def test_bump_refuses_a_dirty_worktree_without_writing(self):
        write_changeset(self.root, "candidate-patch", {fixtures.PLUGIN: "patch"})
        before = file_digests(self.root)
        with self.assertRaisesRegex(release.ReleaseError, "bump needs a clean worktree"):
            self.bump("?? scratch.txt")
        self.assertEqual(self.calls, [("status", "--porcelain", "--untracked-files=all")])
        self.assertEqual(file_digests(self.root), before)

    def test_bump_refuses_when_no_changeset_carries_an_impact(self):
        before = file_digests(self.root)
        with self.assertRaisesRegex(release.ReleaseError, "no pending stable release impact"):
            self.bump("")
        self.assertEqual(self.calls, [("status", "--porcelain", "--untracked-files=all")])
        self.assertEqual(file_digests(self.root), before)


REPOSITORY = "owner/project"
VALIDATED = "c" * 40


def validation_run(**changes: object) -> dict:
    run = {
        "id": 7, "path": release.VALIDATION_WORKFLOW, "event": "push",
        "head_branch": "main", "head_sha": VALIDATED, "status": "completed",
        "conclusion": "success", "created_at": "2026-10-02T11:18:06Z",
        "html_url": f"https://github.com/{REPOSITORY}/actions/runs/7",
        "repository": {"full_name": REPOSITORY},
        "head_repository": {"full_name": REPOSITORY},
    }
    run.update(changes)
    return run


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeRuns:
    """Answer each validation listing in turn; the last answer repeats."""

    def __init__(self, *answers: object) -> None:
        self.answers = list(answers)
        self.endpoints: list[str] = []

    def __call__(self, endpoint: str) -> dict:
        self.endpoints.append(endpoint)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return {"total_count": len(answer), "workflow_runs": answer}


class MainValidationTests(unittest.TestCase):
    """A release waits for main's push validation and never tests again."""

    def wait(self, api: FakeRuns, **options: float) -> tuple[dict, FakeClock]:
        clock = FakeClock()
        run = release.main_validation(
            api, REPOSITORY, VALIDATED, clock=clock, sleep=clock.sleep, **options,
        )
        return run, clock

    def test_a_finished_success_is_accepted_at_once(self):
        api = FakeRuns([validation_run()])
        run, clock = self.wait(api)
        self.assertEqual(run["id"], 7)
        self.assertEqual(clock.sleeps, [])
        self.assertEqual(api.endpoints, [
            f"repos/{REPOSITORY}/actions/workflows/validate.yml/runs"
            f"?event=push&branch=main&head_sha={VALIDATED}&per_page=100",
        ])

    def test_a_running_validation_is_awaited(self):
        running = validation_run(status="in_progress", conclusion=None)
        api = FakeRuns([], [running], [running], [validation_run()])
        run, clock = self.wait(api)
        self.assertEqual(run["conclusion"], "success")
        self.assertEqual(clock.sleeps, [release.VALIDATION_POLL_SECONDS] * 3)

    def test_a_failed_or_cancelled_validation_refuses(self):
        for conclusion in ("failure", "cancelled", "skipped"):
            with self.subTest(conclusion=conclusion), self.assertRaisesRegex(
                release.ReleaseError, f"concluded {conclusion}: .*/actions/runs/7; rerun it",
            ):
                self.wait(FakeRuns([validation_run(conclusion=conclusion)]))

    def test_the_wait_is_bounded(self):
        running = validation_run(status="queued", conclusion=None)
        with self.assertRaisesRegex(release.ReleaseError, "still queued after 60 seconds"):
            self.wait(FakeRuns([running]), wait_seconds=60)

    def test_a_commit_main_never_validated_refuses_after_a_short_grace(self):
        clock = FakeClock()
        with self.assertRaisesRegex(release.ReleaseError, "main has no push validation run"):
            release.main_validation(
                FakeRuns([]), REPOSITORY, VALIDATED, clock=clock, sleep=clock.sleep,
            )
        self.assertEqual(clock.now, release.VALIDATION_APPEAR_SECONDS)

    def test_only_this_repository_push_validation_of_the_commit_counts(self):
        others = [
            validation_run(path=".github/workflows/codeql.yml"),
            validation_run(event="pull_request"),
            validation_run(event="workflow_dispatch"),
            validation_run(head_branch="feature"),
            validation_run(head_sha="d" * 40),
            validation_run(repository={"full_name": "fork/project"}),
            validation_run(head_repository={"full_name": "fork/project"}),
        ]
        with self.assertRaisesRegex(release.ReleaseError, "no push validation run"):
            self.wait(FakeRuns(others))

    def test_the_latest_run_decides(self):
        older = dict(validation_run(id=5, created_at="2026-10-02T10:00:00Z"))
        newer = dict(validation_run(id=6, created_at="2026-10-02T11:00:00Z"))
        with self.assertRaisesRegex(release.ReleaseError, "concluded failure"):
            self.wait(FakeRuns([older, dict(newer, conclusion="failure")]))
        run, _clock = self.wait(FakeRuns([dict(older, conclusion="failure"), newer]))
        self.assertEqual(run["id"], 6)

    def test_api_failures_are_retried_three_times(self):
        failure = release.ReleaseError("HTTP 502")
        run, clock = self.wait(FakeRuns(failure, failure, [validation_run()]))
        self.assertEqual((run["id"], len(clock.sleeps)), (7, 2))
        with self.assertRaisesRegex(release.ReleaseError, "cannot read main's validation runs"):
            self.wait(FakeRuns(failure))


@integration
class ReleaseCandidateTests(unittest.TestCase):
    """verify-candidate and auto-release read main, release tags, stable and validation."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        temporary = Path(cls.tmp.name)
        cls.remote = temporary / "remote.git"
        cls.root = temporary / "work"
        git_fixture.init_repository(cls.remote, bare=True)
        cls.root.mkdir()
        fixtures.make_valid_root(cls.root)
        fixtures.write(cls.root / ".changes" / "README.md", "# Changesets\n")
        git_fixture.init_repository(cls.root, initial_branch="main")
        cls.git("config", "user.name", "Release Candidate Test")
        cls.git("config", "user.email", "release-candidate@example.test")
        cls.git("remote", "add", "origin", str(cls.remote))
        cls.git("add", "--all")
        cls.git("commit", "-qm", "the first release")
        cls.first = cls.git("rev-parse", "HEAD")
        cls.git("tag", "-a", "v0.0.1", "-m", "v0.0.1")
        write_changeset(
            cls.root, "candidate-patch", {fixtures.PLUGIN: "patch"},
            "Ship the candidate patch.",
        )
        change_package(cls.root, "Candidate probe.")
        cls.git("add", "--all")
        cls.git("commit", "-qm", "feat: candidate change")
        cls.feature = cls.git("rev-parse", "HEAD")
        cls.candidate = bump_at(cls.root)

    @classmethod
    def tearDownClass(cls):
        git_fixture.remove_temporary(cls.tmp)

    @classmethod
    def git(cls, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args], cwd=cls.root, capture_output=True, text=True,
            check=True,
        )
        return completed.stdout.strip()

    def setUp(self):
        # Every test starts with v0.0.1 published and the release commit on main.
        self.git("checkout", "-q", "--force", "-B", "main", self.candidate)
        self.git("clean", "-qfdx")
        for tag in self.git("tag", "--list").split():
            if tag != "v0.0.1":
                self.git("tag", "-d", tag)
        stale = [
            f":{line.split()[1]}"
            for line in self.git("ls-remote", "--tags", "origin").splitlines()
            if not line.endswith("^{}") and line.split()[1] != "refs/tags/v0.0.1"
        ]
        self.push("main", f"{self.first}:refs/heads/stable", "v0.0.1", *stale)
        self.runs = [validation_run(head_sha=self.candidate)]

    def api(self, _endpoint: str) -> dict:
        return {"total_count": len(self.runs), "workflow_runs": self.runs}

    def verify(self, version: str = VERSION, sha: str | None = None) -> dict:
        clock = FakeClock()
        return release.verify_candidate(
            self.root, version, sha or self.candidate, repository=REPOSITORY,
            api=self.api, clock=clock, sleep=clock.sleep,
        )

    def refused(self, message: str, version: str = VERSION, sha: str | None = None) -> None:
        with self.assertRaisesRegex(release.ReleaseError, message):
            self.verify(version, sha)

    def push(self, *refspecs: str) -> None:
        self.git("push", "-q", "--force", "origin", *refspecs)
        self.git("fetch", "-q", "--prune", "origin")

    def test_the_merged_release_commit_is_accepted_with_its_prior_and_notes(self):
        result = self.verify()
        self.assertEqual(result["candidate_sha"], self.candidate)
        self.assertEqual(
            (result["prior_version"], result["prior_stable_sha"]), ("0.0.1", self.first),
        )
        self.assertEqual(result["notes"], "- Ship the candidate patch.\n- Fixture baseline.\n")
        self.assertEqual(result["validation_run"], self.runs[0]["html_url"])

    def test_an_interrupted_release_resumes_on_its_own_tag_and_stable(self):
        self.git("tag", "-a", f"v{VERSION}", "-m", f"v{VERSION}", self.candidate)
        self.push(f"{self.candidate}:refs/heads/stable", f"v{VERSION}")
        result = self.verify()
        self.assertEqual(result["prior_stable_sha"], self.first)

    def test_without_any_release_tag_stable_is_the_prior_release(self):
        # The tags of a whole line are deleted; stable keeps its last release.
        self.push(":refs/tags/v0.0.1")
        result = self.verify()
        self.assertEqual(
            (result["prior_version"], result["prior_stable_sha"]), ("0.0.1", self.first),
        )
        self.assertEqual(result["notes"], "- Ship the candidate patch.\n- Fixture baseline.\n")

    def test_without_any_release_tag_stable_must_be_an_older_ancestor(self):
        self.push(":refs/tags/v0.0.1")
        self.git("checkout", "-q", "--detach", self.first)
        fixtures.write(self.root / "side.md", "side\n")
        self.git("add", "--all")
        self.git("commit", "-qm", "docs: side")
        side = self.git("rev-parse", "HEAD")
        self.push(f"{side}:refs/heads/stable")
        self.refused(
            f"the untagged stable release v0.0.1 at {side} is not an ancestor of"
            f" {self.candidate}; stable only moves forward"
        )
        self.push(f"{self.candidate}:refs/heads/stable")
        self.refused(f"{self.candidate} is already the stable release")
        self.push(f"{self.first}:refs/heads/stable")
        # The first-release path is only for a repository that never released.
        self.refused(
            f"remote stable at {self.first} names v0.0.1; a release never goes"
            " back to v0.0.1", version="0.0.1",
        )
        unseen = subprocess.run(
            ["git", "--git-dir", str(self.remote), "commit-tree", "-p", self.first,
             "-m", "unseen", f"{self.first}^{{tree}}"],
            capture_output=True, text=True, check=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "Release Candidate Test",
                 "GIT_AUTHOR_EMAIL": "release-candidate@example.test",
                 "GIT_COMMITTER_NAME": "Release Candidate Test",
                 "GIT_COMMITTER_EMAIL": "release-candidate@example.test"},
        ).stdout.strip()
        subprocess.run(
            ["git", "--git-dir", str(self.remote), "update-ref", "refs/heads/stable", unseen],
            check=True,
        )
        self.refused(f"remote stable is at {unseen}, which this checkout lacks")

    def test_an_interrupted_release_without_an_older_tag_resumes_from_its_run(self):
        # Staging moved stable off the untagged prior release, so no ref
        # records the commit a rollback needs; the staging run's outputs do.
        self.git("tag", "-a", f"v{VERSION}", "-m", f"v{VERSION}", self.candidate)
        self.push(":refs/tags/v0.0.1", f"{self.candidate}:refs/heads/stable", f"v{VERSION}")
        self.refused(
            f"v{VERSION} and stable already sit on {self.candidate}, and no older"
            " release tag records the commit stable moved from.*"
            r"`gh run rerun <run-id> --failed`"
        )

    def test_versions_must_name_the_requested_release(self):
        self.refused(f"names {VERSION}, not 2026.10.2", version="2026.10.2")
        self.refused(f"names 0.0.1, not {VERSION}", sha=self.feature)

    def test_a_commit_with_unconsumed_changesets_is_refused(self):
        write_changeset(self.root, "later-docs", {})
        fixtures.write(self.root / "notes.md", "later\n")
        self.git("add", "--all")
        self.git("commit", "-qm", "docs: later")
        later = self.git("rev-parse", "HEAD")
        self.push("main")
        self.runs = [validation_run(head_sha=later)]
        self.refused("holds changesets no release commit consumed: later-docs.json", sha=later)

    def test_a_commit_outside_main_is_refused(self):
        self.git("checkout", "-q", "-b", "side", self.feature)
        fixtures.write(self.root / "side.md", "side\n")
        self.git("add", "--all")
        self.git("commit", "-qm", "docs: side")
        self.refused("is not a commit on main", sha=self.git("rev-parse", "HEAD"))
        self.refused("is not a commit on main", sha="e" * 40)

    def test_a_newer_release_tag_is_refused(self):
        self.git("tag", "-a", "v2026.11.1", "-m", "v2026.11.1", self.first)
        self.push("v2026.11.1")
        self.refused(
            f"v2026.11.1 is already released; a release never goes back to v{VERSION}"
        )

    def test_the_version_tagged_on_another_commit_is_refused(self):
        self.git("tag", "-a", f"v{VERSION}", "-m", f"v{VERSION}", self.feature)
        self.push(f"v{VERSION}")
        self.refused(f"v{VERSION} already tags {self.feature}")

    def test_a_lightweight_release_tag_is_refused(self):
        self.git("tag", f"v{VERSION}", self.candidate)
        self.push(f"v{VERSION}")
        self.refused(f"release tags must be annotated: v{VERSION}")

    def test_stable_must_hold_the_previous_release_or_this_commit(self):
        self.push(f"{self.feature}:refs/heads/stable")
        self.refused(f"remote stable is at {self.feature}")
        self.push(":refs/heads/stable")
        self.refused("remote stable is at nothing")

    def test_main_validation_decides_last(self):
        self.runs = [validation_run(head_sha=self.candidate, conclusion="failure")]
        self.refused("concluded failure")
        self.runs = []
        self.refused("main has no push validation run")

    def test_the_first_release_is_the_bootstrap_state(self):
        # Without a release tag and stable the commit must be the first
        # stable baseline, and resuming it stays on that path.
        self.push(":refs/heads/stable", ":refs/tags/v0.0.1")
        self.refused("the first stable release must use 0.0.1 everywhere")
        self.git("checkout", "-q", "--detach", self.first)
        (self.root / ".changes" / "fixture.json").unlink()
        fixtures.write(self.root / "CHANGELOG.md", release.bootstrap_changelog())
        self.git("add", "--all")
        self.git("commit", "-qm", "the bootstrap state")
        bootstrap = self.git("rev-parse", "HEAD")
        self.push(f"{bootstrap}:refs/heads/main")
        self.runs = [validation_run(head_sha=bootstrap)]
        result = self.verify("0.0.1", bootstrap)
        self.assertEqual((result["prior_version"], result["prior_stable_sha"]), (None, None))
        self.assertEqual(result["notes"], f"- {release.BOOTSTRAP_NOTE}\n")
        self.addCleanup(self.git, "tag", "-f", "-a", "v0.0.1", "-m", "v0.0.1", self.first)
        self.git("tag", "-f", "-a", "v0.0.1", "-m", "v0.0.1", bootstrap)
        self.push(f"{bootstrap}:refs/heads/stable", "v0.0.1")
        result = self.verify("0.0.1", bootstrap)
        self.assertEqual((result["prior_version"], result["prior_stable_sha"]), (None, None))

    def test_a_push_that_moves_the_version_dispatches_its_release(self):
        commands = FakeShipCommands()
        result = release.auto_release(
            self.root, self.feature, self.candidate, commands=commands,
        )
        self.assertEqual(commands.captured, [[
            "gh", "workflow", "run", "release.yml", "--ref", "main",
            "-f", f"version={VERSION}", "-f", f"sha={self.candidate}",
        ]])
        self.assertEqual(commands.streamed, [])
        self.assertEqual(
            (result["release"], result["version"], result["run_id"]), (True, VERSION, "42"),
        )

    def test_a_push_that_keeps_the_version_or_finds_its_tag_starts_nothing(self):
        self.git("commit", "-q", "--allow-empty", "-m", "docs: after the release")
        later = self.git("rev-parse", "HEAD")
        for before, after, version in (
            (self.candidate, later, VERSION),
            (self.first, self.feature, "0.0.1"),
        ):
            with self.subTest(after=after):
                commands = FakeShipCommands()
                result = release.auto_release(self.root, before, after, commands=commands)
                self.assertEqual(result, {
                    "release": False, "version": version,
                    "reason": f"this push keeps v{version}; nothing to release",
                })
                self.assertEqual(commands.captured, [])
        self.git("tag", "-a", f"v{VERSION}", "-m", f"v{VERSION}", self.candidate)
        self.push(f"v{VERSION}")
        commands = FakeShipCommands()
        result = release.auto_release(
            self.root, self.feature, self.candidate, commands=commands,
        )
        self.assertEqual(result["reason"], f"v{VERSION} already tags {self.candidate}")
        self.assertEqual(commands.captured, [])

    def test_an_absent_previous_head_falls_back_to_the_first_parent(self):
        # A new branch reports 40 zeros; a rewritten one names a commit the checkout lacks.
        for before in ("0" * 40, "", "f" * 40):
            with self.subTest(before=before):
                intent = release.release_intent(self.root, before, self.candidate)
                self.assertEqual((intent["release"], intent["version"]), (True, VERSION))
        with self.assertRaisesRegex(release.ReleaseError, "exact lowercase 40-hex"):
            release.release_intent(self.root, "", "HEAD")


class ReleaseNotesTests(unittest.TestCase):
    CHANGELOG = (
        "# Changelog\n\n## 0.0.1\n\n- First.\n\n## 0.0.2\n\n- Second.\n- Third.\n\n"
        "### Detail\n\n- Kept.\n\n## 0.0.3  \n\n- Last.\n"
    )

    def test_a_release_takes_exactly_its_changelog_section(self):
        self.assertEqual(release.changelog_section(self.CHANGELOG, "0.0.1"), "- First.\n")
        self.assertEqual(
            release.changelog_section(self.CHANGELOG, "0.0.2"),
            "- Second.\n- Third.\n\n### Detail\n\n- Kept.\n",
        )
        self.assertEqual(release.changelog_section(self.CHANGELOG, "0.0.3"), "- Last.\n")

    def test_a_missing_repeated_or_empty_section_is_refused(self):
        for text, version, message in (
            (self.CHANGELOG, "0.0.4", "exactly one ## 0.0.4 section, found 0"),
            (self.CHANGELOG + "\n## 0.0.1\n\n- Again.\n", "0.0.1", "found 2"),
            ("# Changelog\n\n## 0.0.5\n\n## 0.0.6\n\n- Next.\n", "0.0.5", "is empty"),
        ):
            with self.subTest(version=version), \
                    self.assertRaisesRegex(release.ReleaseError, message):
                release.changelog_section(text, version)

    @integration
    def test_notes_come_from_the_released_commit_not_the_worktree(self):
        with git_fixture.temporary_directory() as temporary:
            root = Path(temporary)
            git_fixture.init_repository(root, initial_branch="main")
            fixtures.write(root / "CHANGELOG.md", self.CHANGELOG)
            for args in (("add", "--all"),
                         ("-c", "user.name=Notes Test",
                          "-c", "user.email=notes@example.test",
                          "commit", "-qm", "notes")):
                subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
            fixtures.write(root / "CHANGELOG.md", "# Changelog\n\n## 0.0.1\n\n- Edited.\n")
            self.assertEqual(release.release_notes(root, "0.0.1", "HEAD"), "- First.\n")
            self.assertEqual(release.release_notes(root, "0.0.1"), "- Edited.\n")
            with self.assertRaisesRegex(release.ReleaseError, "strict SemVer"):
                release.release_notes(root, "v0.0.1", "HEAD")


class FakeShipCommands:
    """Record ship's gh calls and answer them."""

    URL = f"https://github.com/{REPOSITORY}/actions/runs/42"

    def __init__(self, *, dispatch_output: str = URL + "\n", dispatch_code: int = 0,
                 watch_code: int = 0, release_view: dict | None = None,
                 runs: list | None = None) -> None:
        self.dispatch_output = dispatch_output
        self.dispatch_code = dispatch_code
        self.watch_code = watch_code
        self.release_view = release_view if release_view is not None else {
            "url": "https://github.com/owner/project/releases/tag/v0.0.3",
            "isImmutable": True,
        }
        self.runs = runs or []
        self.captured: list[list[str]] = []
        self.streamed: list[list[str]] = []

    def capture(self, argv: list[str]) -> subprocess.CompletedProcess:
        self.captured.append(list(argv))
        if argv[:3] == ["gh", "workflow", "run"]:
            return subprocess.CompletedProcess(
                argv, self.dispatch_code, self.dispatch_output,
                "HTTP 422" if self.dispatch_code else "",
            )
        if argv[:3] == ["gh", "run", "list"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.runs), "")
        if argv[:3] == ["gh", "release", "view"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.release_view), "")
        raise AssertionError(argv)

    def stream(self, argv: list[str]) -> int:
        self.streamed.append(list(argv))
        return self.watch_code


class ShipTests(unittest.TestCase):
    """One maintainer command dispatches the Release and follows it."""

    NOW = datetime.datetime(2026, 10, 2, 12, tzinfo=datetime.timezone.utc)

    def ship(self, commands: FakeShipCommands, sha: str | None = None,
             root: Path = Path("."), **options) -> dict:
        return release.ship(
            root, "0.0.3", sha, commands=commands, now=lambda: self.NOW,
            sleep=lambda _seconds: None, **options,
        )

    def test_dispatch_watch_and_the_immutable_release(self):
        commands = FakeShipCommands()
        result = self.ship(commands)
        self.assertEqual(commands.captured[0], [
            "gh", "workflow", "run", "release.yml", "--ref", "main", "-f", "version=0.0.3",
        ])
        self.assertEqual(commands.streamed, [
            ["gh", "run", "watch", "42", "--exit-status", "--compact"],
        ])
        self.assertEqual(result, {
            "version": "0.0.3", "sha": None, "run_id": "42",
            "release_url": "https://github.com/owner/project/releases/tag/v0.0.3",
            "immutable": True,
        })

    @integration
    def test_an_explicit_commit_is_sent_as_its_full_sha(self):
        with git_fixture.temporary_directory() as temporary:
            root = Path(temporary)
            git_fixture.init_repository(root, initial_branch="main")
            subprocess.run(
                ["git", "-c", "user.name=Ship Test", "-c", "user.email=ship@example.test",
                 "commit", "-q", "--allow-empty", "-m", "release commit"],
                cwd=root, check=True,
            )
            full = release.git(root, "rev-parse", "HEAD")
            commands = FakeShipCommands()
            result = self.ship(commands, sha=full[:10], root=root)
            self.assertEqual(commands.captured[0][-2:], ["-f", f"sha={full}"])
            self.assertEqual(result["sha"], full)
            with self.assertRaisesRegex(release.ReleaseError, "unknown commit"):
                self.ship(FakeShipCommands(), sha="f" * 12, root=root)

    def test_the_run_is_found_when_gh_prints_no_url(self):
        commands = FakeShipCommands(dispatch_output="", runs=[
            {"databaseId": 40, "createdAt": "2026-10-02T11:00:00Z"},
            {"databaseId": 43, "createdAt": "2026-10-02T12:00:05Z"},
            {"databaseId": 41, "createdAt": "2026-10-02T11:59:50Z"},
        ])
        self.assertEqual(self.ship(commands, watch=False)["run_id"], "43")
        self.assertEqual(commands.streamed, [])

    def test_each_failure_is_reported_with_its_next_step(self):
        for commands, message in (
            (FakeShipCommands(dispatch_code=1), "release dispatch failed: HTTP 422"),
            (FakeShipCommands(watch_code=1),
             r"Release run 42 failed; read `gh run view 42 --log-failed`"),
            (FakeShipCommands(release_view={"url": "u", "isImmutable": False}),
             "is not an immutable Release"),
        ):
            with self.subTest(message), self.assertRaisesRegex(release.ReleaseError, message):
                self.ship(commands)
        with self.assertRaisesRegex(release.ReleaseError, "strict SemVer"):
            release.ship(Path("."), "v0.0.3", commands=FakeShipCommands())


@integration
class ReleaseFinalizeTests(unittest.TestCase):
    VERSION = "1.2.3"
    FEATURE = "codex/issue-42"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        temporary = Path(self.tmp.name)
        self.remote = temporary / "remote.git"
        self.root = temporary / "work"
        git_fixture.init_repository(self.remote, bare=True)
        git_fixture.init_repository(self.root, initial_branch="main")
        self.git_run("git", "config", "user.name", "Release Test")
        self.git_run("git", "config", "user.email", "release@example.test")
        (self.root / "versions.json").write_text(json.dumps({
            "schema_version": 1,
            "marketplace": self.VERSION,
            "plugins": {"fixture": self.VERSION},
        }), encoding="utf-8")
        (self.root / "README.md").write_text("release fixture\n", encoding="utf-8")
        self.git_run("git", "add", ".")
        self.git_run("git", "commit", "-m", "release fixture")
        self.git_run("git", "tag", "-a", f"v{self.VERSION}", "-m", "release")
        self.git_run("git", "branch", "stable")
        self.git_run("git", "branch", self.FEATURE)
        self.git_run("git", "remote", "add", "origin", str(self.remote))
        self.git_run("git", "push", "origin", "main", "stable", self.FEATURE, f"v{self.VERSION}")
        self.git_run("git", "fetch", "origin", "--prune", "--tags")
        self.git_run("git", "switch", self.FEATURE)

    def tearDown(self):
        git_fixture.remove_temporary(self.tmp)

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
            self.root, self.VERSION, [self.FEATURE]
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
            [self.FEATURE],
            apply=True,
        )
        self.assertTrue(result["apply"])
        self.assertEqual(
            self.git_run("git", "branch", "--show-current").stdout.strip(), "main"
        )
        self.assertEqual(self.git_run("git", "status", "--porcelain").stdout, "")
        for branch in (self.FEATURE,):
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
            [self.FEATURE],
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

    def branch_refs(self, branch: str) -> tuple[bool, bool]:
        """Return whether the local repository and the origin remote hold the branch."""
        return (
            release.git_ok(
                self.root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"
            ),
            release.git_ok(
                self.remote, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"
            ),
        )

    def test_apply_deletes_a_merged_claude_branch_locally_and_on_origin(self):
        branch = "claude/delivery-map-ends-with-newline"
        self.git_run("git", "branch", branch, "main")
        self.git_run("git", "push", "origin", branch)
        self.git_run("git", "fetch", "origin", "--prune", "--tags")
        self.git_run("git", "switch", branch)

        result = release.finalize_local_release(
            self.root, self.VERSION, [branch], apply=True,
        )

        self.assertEqual(
            result["branches"][0], {"branch": branch, "local": True, "remote": True}
        )
        self.assertEqual(self.branch_refs(branch), (False, False))
        self.assertFalse(release.git_ok(
            self.root, "show-ref", "--verify", "--quiet",
            f"refs/remotes/origin/{branch}",
        ))
        self.assertEqual(self.branch_refs(self.FEATURE), (True, True))
        self.assertEqual(
            self.git_run("git", "branch", "--show-current").stdout.strip(), "main"
        )
        self.assertEqual(self.git_run("git", "status", "--porcelain").stdout, "")

    def test_unmerged_claude_branch_is_never_deleted(self):
        branch = "claude/scope-approval-requires-current-bindings"
        self.git_run("git", "switch", "-c", branch, "main")
        (self.root / "unmerged.txt").write_text("not released\n", encoding="utf-8")
        self.git_run("git", "add", "unmerged.txt")
        self.git_run("git", "commit", "-m", "unmerged")
        self.git_run("git", "push", "origin", branch)
        with self.assertRaisesRegex(release.ReleaseError, "unmerged local branch"):
            release.finalize_local_release(
                self.root, self.VERSION, [branch], apply=True
            )
        self.assertEqual(self.branch_refs(branch), (True, True))
        self.assertEqual(
            self.git_run("git", "branch", "--show-current").stdout.strip(), branch
        )

    def test_branch_outside_every_declared_host_prefix_is_refused(self):
        branch = "feature/issue-42"
        self.git_run("git", "branch", branch, "main")
        self.git_run("git", "push", "origin", branch)
        with self.assertRaisesRegex(release.ReleaseError, "bounded"):
            release.finalize_local_release(
                self.root, self.VERSION, [branch], apply=True
            )
        self.assertEqual(self.branch_refs(branch), (True, True))
        for near_miss in (
            "claude-code/issue-42",
            "Claude/issue-42",
            "claude/Issue-42",
            "claude/issue_42",
            "claude/nested/issue-42",
            "claude/",
            "claude/" + "a" * release.MAX_FINALIZE_BRANCH_CHARS,
        ):
            with self.subTest(branch=near_miss), \
                    self.assertRaisesRegex(release.ReleaseError, "bounded"):
                release.validate_finalize_branch(near_miss)

    def test_cleanup_prefixes_are_read_from_the_host_adapter_registry(self):
        self.assertEqual(release.finalize_branch_prefixes(), ("claude/", "codex/"))
        registry = {
            "other": build_distributions.HostAdapter(
                "other", {"feature_branch_prefix": "other-agent/"}, None,
            ),
        }
        with mock.patch.object(
            build_distributions, "load_adapters", return_value=registry,
        ):
            release.validate_finalize_branch("other-agent/issue-42")
            for branch in ("claude/issue-42", "codex/issue-42"):
                with self.subTest(branch=branch), self.assertRaisesRegex(
                    release.ReleaseError, "bounded other-agent/<kebab-name> branch",
                ):
                    release.validate_finalize_branch(branch)


    def test_the_audit_requires_versions_json_to_name_the_release(self):
        self.git_run("git", "tag", "-a", "v9.9.9", "-m", "v9.9.9", "stable")
        self.git_run("git", "push", "origin", "v9.9.9")
        self.git_run("git", "fetch", "origin", "--prune", "--tags")
        with self.assertRaisesRegex(
            release.ReleaseError, "versions.json does not name v9.9.9",
        ):
            release.release_ref_audit(self.root, "9.9.9")


@integration
class BootstrapFinalizeTests(unittest.TestCase):
    def test_bootstrap_release_reaches_the_clean_main_terminal_state(self):
        with git_fixture.temporary_directory() as temporary:
            temporary_path = Path(temporary)
            remote = temporary_path / "remote.git"
            root = temporary_path / "work"

            def run(*args: str) -> subprocess.CompletedProcess:
                return subprocess.run(
                    list(args), cwd=root,
                    capture_output=True, text=True, check=True,
                )

            git_fixture.init_repository(remote, bare=True)
            git_fixture.init_repository(root, initial_branch="main")
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
                ["codex/bootstrap-fixture"],
                apply=True,
            )

            self.assertEqual(result["release"]["version"], "0.0.1")
            self.assertEqual(run("git", "branch", "--show-current").stdout.strip(), "main")
            self.assertEqual(run("git", "status", "--porcelain").stdout, "")


@integration
class PullRequestConfidentialityTests(unittest.TestCase):
    """check-pr reads every commit message and added line of base..HEAD, and
    the PR text it is given, because a merge commit keeps every commit: a
    home-directory path is refused, and so is a term of the owner's local
    private terms file, by kind and position only (#357)."""

    # Built from parts so that this file holds no home path itself.
    HOME = "/" + "home/fixture"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(git_fixture.remove_temporary, temporary)
        self.scratch = Path(temporary.name).resolve()
        self.root = self.scratch / "repository"
        self.root.mkdir()
        git_fixture.init_repository(self.root, initial_branch="main")
        self.git("config", "user.name", "Release Test")
        self.git("config", "user.email", "release@example.test")
        self.commit({"README.md": "Fixture.\n"}, "Base")
        self.base = self.git("rev-parse", "HEAD")
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(release.PRIVATE_TERMS_VARIABLE, None)

    def git(self, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=self.root, capture_output=True,
                              text=True, check=True).stdout.strip()

    def commit(self, files: dict, message: str) -> str:
        for relative, text in files.items():
            fixtures.write(self.root / relative, text)
        self.git("add", "--all")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")[:12]

    def terms(self, *terms: str) -> Path:
        path = self.scratch / "private-terms.txt"
        path.write_text("\n".join(terms) + "\n", encoding="utf-8")
        os.environ[release.PRIVATE_TERMS_VARIABLE] = str(path)
        return path

    def refusal(self, base: str = "", pr_text: Path | None = None) -> str:
        with self.assertRaises(release.ReleaseError) as raised:
            release.check_pr_publishable(self.root, base or self.base, pr_text)
        return str(raised.exception)

    def test_a_home_path_in_any_commit_message_or_added_line_is_refused(self):
        added = self.commit({"docs/notes.md": f"Notes.\nSeen in {self.HOME}/app.\n"}, "Add notes")
        message = self.commit({"docs/other.md": "Other.\n"},
                              f"Add other\n\nMeasured in {self.HOME}/app.")
        self.commit({"docs/notes.md": "Notes.\n"}, "Drop the path again")
        # The final tree is clean, yet the merge commit keeps both commits.
        error = self.refusal()
        self.assertIn(f"home-directory path at commit {added} docs/notes.md line 2, column 9", error)
        self.assertIn(f"home-directory path at commit {message} message line 3, column 13", error)
        self.assertEqual(error.count(" at commit "), 2, error)
        self.assertNotIn(self.HOME, error)

    def test_a_system_home_or_a_declared_fixture_home_passes(self):
        self.commit({"docs/ci.md": "Runs in /home/runner/work/app/app.\n",
                     "tools/tests/test_sample.py": f'CWD = "{self.HOME}/app"\n'}, "Add CI notes")
        declared = {"tools/tests/test_sample.py": frozenset({"fixture"})}
        with mock.patch.dict(validate.HOME_PATH_FIXTURES, declared, clear=True):
            self.assertEqual(release.check_pr_publishable(self.root, self.base),
                             {"commits": 1, "terms_checked": 0})

    def test_private_terms_are_refused_by_kind_and_position_and_never_printed(self):
        self.terms("Fixture Corp", "acme-internal")
        commit = self.commit({"docs/acme-internal-notes.md": "Notes.\nOwned by ACME-INTERNAL.\n"},
                             "Report for fixture corp")
        pr_text = self.scratch / "pr.md"
        pr_text.write_text("Report\n\nFixture Corp asked for it.\n", encoding="utf-8")
        error = self.refusal(pr_text=pr_text)
        for expected in (f"private term at commit {commit} message line 1, column 12",
                         f"private term at commit {commit} file 1 path",
                         f"private term at commit {commit} file 1 line 2, column 10",
                         "private term at PR text line 3, column 1"):
            with self.subTest(expected=expected):
                self.assertIn(expected, error)
        self.assertEqual(error.count(" at "), 4, error)
        for value in ("fixture corp", "acme-internal", "notes.md", str(self.scratch)):
            with self.subTest(value=value):
                self.assertNotIn(value.lower(), error.lower())

    def test_a_term_is_a_whole_word_and_without_a_terms_file_none_is_checked(self):
        commit = self.commit({"docs/a.md": "The fixture corporation.\n"}, "Fixture Corp report")
        self.assertEqual(release.check_pr_publishable(self.root, self.base),
                         {"commits": 1, "terms_checked": 0})
        self.terms("", "Fixture Corp", "  ")
        error = self.refusal()
        self.assertIn(f"private term at commit {commit} message line 1, column 1", error)
        self.assertEqual(error.count(" at "), 1, error)

    def test_a_merge_is_read_for_the_lines_it_adds_itself(self):
        self.terms("Fixture Corp")
        self.git("checkout", "-qb", "feature")
        self.commit({"docs/feature.md": "Feature.\n"}, "Feature")
        self.git("checkout", "-q", "main")
        self.commit({"docs/main.md": "Fixture Corp history already on main.\n"}, "Main work")
        base = self.git("rev-parse", "HEAD")
        self.git("checkout", "-q", "feature")
        self.git("merge", "-q", "--no-ff", "--no-commit", "main")
        merge = self.commit({"docs/feature.md": "Feature.\nFixture Corp in the merge.\n"},
                            "Merge main")
        error = self.refusal(base)
        self.assertIn(f"private term at commit {merge} docs/feature.md line 2, column 1", error)
        self.assertEqual(error.count(" at "), 1, error)

    def test_the_terms_file_must_be_readable_and_stay_out_of_the_repository(self):
        os.environ[release.PRIVATE_TERMS_VARIABLE] = str(self.scratch / "missing.txt")
        with self.assertRaisesRegex(release.ReleaseError, "names a file that cannot be read"):
            release.check_pr_publishable(self.root, self.base)
        inside = self.root / "terms.txt"
        inside.write_text("Fixture Corp\n", encoding="utf-8")
        os.environ[release.PRIVATE_TERMS_VARIABLE] = str(inside)
        error = self.refusal()
        self.assertIn("inside this checkout", error)
        self.assertNotIn(str(self.root), error)
        (self.root / ".git" / "info" / "exclude").write_text("terms.txt\n", encoding="utf-8")
        self.assertEqual(release.check_pr_publishable(self.root, self.base),
                         {"commits": 0, "terms_checked": 1})

    def test_check_pr_scans_after_the_changeset_check_and_reads_the_pr_text(self):
        root = self.scratch / "valid"
        fixtures.make_valid_root(root)
        git_fixture.init_repository(root, initial_branch="main")

        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                                  check=True).stdout.strip()

        git("config", "user.name", "Release Test")
        git("config", "user.email", "release@example.test")
        git("add", "--all")
        git("commit", "-qm", "Base")
        base = git("rev-parse", "HEAD")
        fixtures.write(root / ".changes" / "notes.json",
                       json.dumps({"summary": "Add notes.", "components": {}}))
        fixtures.write(root / "docs" / "notes.md", "Notes.\n")
        git("add", "--all")
        git("commit", "-qm", "Add notes")
        pr_text = self.scratch / "pr.md"
        pr_text.write_text("Add notes\n\nFixture Corp asked for them.\n", encoding="utf-8")
        self.terms("Fixture Corp")

        def cli(*arguments: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, str(TESTS_DIR.parent / "release.py"), "--root", str(root),
                 "check-pr", "--base", base, *arguments],
                capture_output=True, text=True, check=False)

        refused = cli("--pr-text", str(pr_text))
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("private term at PR text line 3, column 1", refused.stderr)
        self.assertNotIn("fixture corp", refused.stderr.lower())
        accepted = cli()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(accepted.stdout.splitlines(), [
            "release: pull request changeset valid",
            "release: 1 commit and its added lines hold no home-directory path or private term"
            " (1 private term checked)",
        ])


if __name__ == "__main__":
    unittest.main()
