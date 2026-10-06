"""Standalone-team and cross-host distribution contracts."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
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


_SHARED_ROOTS: dict[str, Path] = {}
_SHARED_DIRECTORIES: list[tempfile.TemporaryDirectory] = []


def tearDownModule() -> None:
    for temporary in _SHARED_DIRECTORIES:
        temporary.cleanup()
    _SHARED_DIRECTORIES.clear()
    _SHARED_ROOTS.clear()


def copy_root(source: Path, target: Path, *, with_dist: bool = True) -> Path:
    """An independent copy of a built root, modes kept; without dist/ it is sources only."""
    def ignore(directory: str, names: list[str]) -> set[str]:
        return {"dist"} if not with_dist and Path(directory) == source else set()

    shutil.copytree(source, target, symlinks=True, ignore=ignore)
    return target


def shared_root(variant: str = "pristine") -> Path:
    """A built marketplace root made once per process. Tests only read it;
    a test that changes a root works on its own ``copy_root``."""
    if variant not in _SHARED_ROOTS:
        temporary = tempfile.TemporaryDirectory()
        _SHARED_DIRECTORIES.append(temporary)
        root = Path(temporary.name) / "marketplace"
        if variant == "pristine":
            fixtures.make_valid_root(root)
        else:
            copy_root(shared_root(), root, with_dist=False)
            SHARED_VARIANTS[variant](root)
            build_distributions.replace_generated(root, root / "dist")
        _SHARED_ROOTS[variant] = root
    return _SHARED_ROOTS[variant]


@integration
class SingleTeamDistributionTests(unittest.TestCase):
    """Contracts read from the shared build, which no test here changes."""

    def setUp(self) -> None:
        self.root = shared_root()

    def test_session_marker_publishes_exact_writer_invocation_binding(self):
        repository = TESTS_DIR.parents[1]
        for host in build_distributions.HOSTS:
            with self.subTest(host=host):
                script = (
                    repository / "dist" / host / "software-engineering-team"
                    / "scripts" / "team_guard.py"
                )
                result = subprocess.run(
                    [sys.executable, str(script.with_name("hook_launcher.py")),
                     "scripts/team_guard.py", "register"],
                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                    check=False,
                )
                self.assertEqual(
                    result.returncode, 0, result.stdout + result.stderr,
                )
                payload = json.loads(result.stdout)
                context = payload["hookSpecificOutput"]["additionalContext"]
                self.assertIn(
                    "AGENT_MARKETPLACE_HOOKS_ACTIVE: software-engineering-team",
                    context,
                )
                self.assertIn(
                    "AGENT_MARKETPLACE_PYTHON: "
                    f"{Path(os.path.abspath(sys.executable))}",
                    context,
                )
                self.assertIn(
                    f"AGENT_MARKETPLACE_SCRIPTS: {script.resolve().parent}",
                    context,
                )

    def test_catalogs_versions_and_packages_name_one_standalone_team(self):
        versions = json.loads(
            (self.root / "versions.json").read_text(encoding="utf-8")
        )
        self.assertEqual(set(versions["plugins"]), {fixtures.PLUGIN})

        claude = json.loads(
            (self.root / ".claude-plugin/marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        codex = json.loads(
            (self.root / ".agents/plugins/marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            [entry["name"] for entry in claude["plugins"]],
            [fixtures.PLUGIN],
        )
        self.assertEqual(
            [entry["name"] for entry in codex["plugins"]],
            [fixtures.PLUGIN],
        )

        for host in build_distributions.HOSTS:
            with self.subTest(host=host):
                self.assertEqual(
                    sorted(path.name for path in (self.root / "dist" / host).iterdir()),
                    [fixtures.PLUGIN],
                )
                adapter = build_distributions.load_adapters(self.root)[host]
                manifest_path = (
                    self.root / "platforms" / host / fixtures.PLUGIN / "manifest.json"
                )
                if adapter.metadata["artifact_kind"] == "native_marketplace":
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    self.assertIn(manifest.get("dependencies"), (None, []))
                else:
                    self.assertFalse(manifest_path.exists())

    def test_hosts_share_snapshot_and_host_neutral_canonical_payload(self):
        snapshots = []
        for host in build_distributions.HOSTS:
            package = self.root / "dist" / host / fixtures.PLUGIN
            adapter = build_distributions.load_adapters(self.root)[host]
            manifest_dir = adapter.module.native_manifest_directory(host)
            if manifest_dir is not None:
                manifest = json.loads(
                    (package / manifest_dir / "plugin.json").read_text(encoding="utf-8")
                )
                self.assertNotIn("agent_marketplace", manifest)
            else:
                self.assertFalse((package / f".{host}-plugin").exists())
            provenance = json.loads(
                (package / ".agent-marketplace-package.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                provenance["delivery_protocol"],
                build_distributions.DELIVERY_PROTOCOL_CAPABILITY,
            )
            self.assertEqual(provenance["schema_version"], 4)
            self.assertEqual(set(provenance), {
                "schema_version", "component", "host", "version",
                "marketplace_release", "files", "executables",
                "runtime_contracts", "delivery_protocol",
            })
            snapshots.append({
                key: provenance[key] for key in ("marketplace_release", "version")
            })
            self.assertEqual(
                json.loads((package / "product.json").read_text(encoding="utf-8")),
                build_distributions.load_product_contract(self.root),
            )
        self.assertTrue(all(snapshot == snapshots[0] for snapshot in snapshots))

        for relative in (
            "scripts/experience_application_check.py",
            "skill-content/experience-modeling/data/experience-schema.json",
        ):
            expected = (self.root / "plugins" / fixtures.PLUGIN / relative).read_bytes()
            for host in build_distributions.HOSTS:
                packaged = self.root / "dist" / host / fixtures.PLUGIN / relative
                self.assertEqual(packaged.read_bytes(), expected, packaged)

        source = self.root / "plugins" / fixtures.PLUGIN
        for relative in ("constitution.md", "flows", "skill-content"):
            canonical = source / relative
            for host in build_distributions.HOSTS:
                packaged = self.root / "dist" / host / fixtures.PLUGIN / relative
                if canonical.is_file():
                    expected = canonical.read_bytes().replace(b"\r\n", b"\n").replace(
                        b"\r", b"\n"
                    )
                    self.assertEqual(packaged.read_bytes(), expected)
                    continue
                for path in sorted(
                    candidate
                    for candidate in canonical.rglob("*")
                    if candidate.is_file()
                    and not build_distributions.is_python_cache(candidate)
                ):
                    expected = path.read_bytes()
                    if b"\0" not in expected:
                        expected = expected.replace(b"\r\n", b"\n").replace(
                            b"\r", b"\n"
                        )
                    target = packaged / path.relative_to(canonical)
                    self.assertEqual(target.read_bytes(), expected, target)

    def test_provenance_hashes_and_rebuild_are_deterministic(self):
        for host in build_distributions.HOSTS:
            with self.subTest(host=host):
                package = self.root / "dist" / host / fixtures.PLUGIN
                provenance = json.loads(
                    (package / build_distributions.PROVENANCE).read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(provenance["component"], fixtures.PLUGIN)
                self.assertEqual(provenance["host"], host)
                for relative, expected in provenance["files"].items():
                    actual = hashlib.sha256((package / relative).read_bytes()).hexdigest()
                    self.assertEqual(actual, expected, relative)

        self.assertEqual(
            build_distributions.check(self.root, self.root / "dist"),
            [],
        )

    def test_independent_builds_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as first_dir, \
                tempfile.TemporaryDirectory() as second_dir:
            first = Path(first_dir) / "dist"
            second = Path(second_dir) / "dist"
            build_distributions.build(self.root, first)
            build_distributions.build(self.root, second)
            self.assertEqual(build_distributions.compare_dirs(first, second), [])

    def test_snapshot_paths_use_case_sensitive_posix_order(self):
        paths = build_distributions.snapshot_files(self.root, "plugins")
        relative = [path.relative_to(self.root).as_posix() for path in paths]
        self.assertEqual(relative, sorted(relative))
        self.assertNotEqual(relative, sorted(relative, key=str.casefold))

    def test_canonical_source_rejects_symlinked_surface_roots(self):
        for surface_name in ("plugins", "platforms"):
            with self.subTest(surface=surface_name), tempfile.TemporaryDirectory() as tmp:
                root = copy_root(self.root, Path(tmp) / "repository", with_dist=False)
                surface = root / surface_name
                moved = root / "assets" / f"moved-{surface_name}"
                moved.parent.mkdir()
                surface.rename(moved)
                try:
                    surface.symlink_to(moved, target_is_directory=True)
                except OSError as exc:
                    self.skipTest(f"fixture filesystem cannot create symlinks: {exc}")
                with self.assertRaisesRegex(ValueError, "real directory"):
                    build_distributions.validate_canonical(root)

    def test_issue_reporting_is_external_and_has_no_project_artifacts(self):
        for host in build_distributions.HOSTS:
            with self.subTest(host=host):
                package = self.root / "dist" / host / fixtures.PLUGIN
                wrapper = (
                    package / "skills/issue-report/SKILL.md"
                ).read_text(encoding="utf-8")
                setup_wrapper = (
                    package / "skills/setup/SKILL.md"
                ).read_text(encoding="utf-8")
                canonical = (
                    package / "skill-content/issue-report/SKILL.md"
                ).read_text(encoding="utf-8")
                self.assertIn("project_scope: external", canonical)
                self.assertNotIn("workspace config", wrapper)
                self.assertIn("workspace config", setup_wrapper)
                self.assertTrue((package / "scripts/file_issue.py").is_file())

    def test_fixture_copy_ignores_python_runtime_caches(self):
        with tempfile.TemporaryDirectory() as source_dir, \
                tempfile.TemporaryDirectory() as target_dir:
            source_root = Path(source_dir)
            plugin_root = source_root / "plugins" / fixtures.PLUGIN
            script = plugin_root / "scripts" / "runner.py"
            script.parent.mkdir(parents=True)
            script.write_text("print('fixture')\n", encoding="utf-8")
            cache = script.parent / "__pycache__"
            cache.mkdir()
            (cache / "runner.cpython-39.pyc").write_bytes(b"cache")

            with mock.patch.object(fixtures, "REAL_REPOSITORY", source_root):
                fixtures.copy(f"plugins/{fixtures.PLUGIN}", Path(target_dir))

            copied = Path(target_dir) / "plugins" / fixtures.PLUGIN
            self.assertTrue((copied / "scripts/runner.py").is_file())
            self.assertFalse((copied / "scripts/__pycache__").exists())

    def test_agent_metadata_is_projected_for_each_host(self):
        canonical = self.root / "plugins" / fixtures.PLUGIN / "agents"
        for source in canonical.glob("*.md"):
            name = source.stem
            claude = (
                self.root / "dist/claude" / fixtures.PLUGIN / "agents" / source.name
            ).read_text(encoding="utf-8")
            codex = (
                self.root / "dist/codex" / fixtures.PLUGIN / "agents" / source.name
            ).read_text(encoding="utf-8")
            self.assertIn(f"name: {name}", claude)
            self.assertIn(f"name: {name}", codex)
            self.assertIn("model:", claude)
            self.assertNotIn("reasoning:", claude)
            self.assertNotIn("reasoning:", codex)


@integration
class CopiedDistributionTests(unittest.TestCase):
    """Contracts that change a built root, each on its own copy of the shared build."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = copy_root(shared_root(), Path(self.temporary.name) / "marketplace")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.root, check=True,
            capture_output=True, text=True,
        ).stdout.strip()

    def commit_source_change(self, base: str, relative: str) -> str:
        """Commit one canonical file change with its regenerated distributions."""
        self.git("checkout", "-q", "--detach", base)
        source = self.root / "plugins" / fixtures.PLUGIN / relative
        existing = source.read_bytes() if source.is_file() else b""
        source.write_bytes(existing + b"\n# queued change\n")
        build_distributions.replace_generated(self.root, self.root / "dist")
        self.git("add", "--all")
        self.git("commit", "-qm", f"change {relative}")
        return self.git("rev-parse", "HEAD")

    def test_changes_to_neighbouring_package_files_merge_without_a_conflict(self):
        # A merge queue merges each queued pull request onto the ones ahead of
        # it without rebuilding dist/ (#311): committed packages of changes to
        # different files must merge into the rebuild of the merged sources.
        # One real merge of a change beside an added file; the provenance merge
        # of the other neighbour pairs is a case of the documented conflicts test.
        git_fixture.init_repository(self.root, initial_branch="main")
        self.git("config", "user.name", "Distribution Test")
        self.git("config", "user.email", "distribution@example.test")
        self.git("add", "--all")
        self.git("commit", "-qm", "base")
        base = self.git("rev-parse", "HEAD")
        package = self.root / "dist" / "claude" / fixtures.PLUGIN
        source = self.root / "plugins" / fixtures.PLUGIN
        inventory = sorted(json.loads(
            (package / build_distributions.PROVENANCE).read_text(encoding="utf-8")
        )["files"])
        first, added = next(
            (left, left[:-len(".py")] + "_queued.py")
            for left, right in zip(inventory, inventory[1:])
            if left.startswith("scripts/") and left.endswith(".py")
            and left < left[:-len(".py")] + "_queued.py" < right
            and all(
                (source / name).is_file()
                and (package / name).read_bytes() == (source / name).read_bytes()
                for name in (left, right)
            )
        )
        queued = self.commit_source_change(base, added)
        self.commit_source_change(base, first)
        merged = subprocess.run(
            ["git", "merge", "--no-ff", "-q", "-m", "queued merge", queued],
            cwd=self.root, capture_output=True, text=True, check=False,
        )
        if merged.returncode:
            self.git("merge", "--abort")
        self.assertEqual(merged.returncode, 0, merged.stdout + merged.stderr)
        self.assertEqual(
            build_distributions.check(self.root, self.root / "dist"), [],
        )

    def test_snapshot_normalizes_checkout_only_eol_drift(self):
        # Exercise the snapshot normalizer independently of the repository's
        # fail-closed LF checkout policy.
        (self.root / ".gitattributes").write_bytes(
            b"*.csv -text\ndist/** -text\n"
        )
        git_fixture.init_repository(self.root, initial_branch="main")
        subprocess.run(
            ["git", "config", "core.autocrlf", "true"],
            cwd=self.root, check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "add", "--all"], cwd=self.root, check=True,
            capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "checkout-index", "--all", "--force"],
            cwd=self.root, check=True, capture_output=True, text=True,
        )
        checkout_identity = build_distributions.marketplace_snapshot(
            self.root
        )["build_id"]

        subprocess.run(
            ["git", "config", "core.autocrlf", "false"],
            cwd=self.root, check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "checkout-index", "--all", "--force"],
            cwd=self.root, check=True, capture_output=True, text=True,
        )
        blob_identity = build_distributions.marketplace_snapshot(
            self.root
        )["build_id"]
        self.assertEqual(checkout_identity, blob_identity)

    def test_snapshot_is_stable_across_staging_a_crlf_text_edit(self):
        git_fixture.init_repository(self.root, initial_branch="main")
        subprocess.run(
            ["git", "config", "core.autocrlf", "true"],
            cwd=self.root, check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "add", "--all"], cwd=self.root, check=True,
            capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "checkout-index", "--all", "--force"],
            cwd=self.root, check=True, capture_output=True, text=True,
        )
        source = self.root / "plugins" / fixtures.PLUGIN / "constitution.md"
        source.write_bytes(source.read_bytes() + b"\r\nSubstantive edit\r\n")

        before_staging = build_distributions.marketplace_snapshot(self.root)
        with tempfile.TemporaryDirectory() as first_dir:
            first = Path(first_dir) / "dist"
            build_distributions.build(self.root, first)
            subprocess.run(
                ["git", "add", "--all"], cwd=self.root, check=True,
                capture_output=True, text=True,
            )
            after_staging = build_distributions.marketplace_snapshot(self.root)
            with tempfile.TemporaryDirectory() as second_dir:
                second = Path(second_dir) / "dist"
                build_distributions.build(self.root, second)
                self.assertEqual(
                    build_distributions.compare_dirs(first, second), []
                )
        self.assertEqual(before_staging, after_staging)

    def test_binary_payload_survives_autocrlf_checkout_and_build(self):
        relative = Path(
            "skill-content/ui-ux-design/data/binary-contract.pdf"
        )
        payload = (
            b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
            b"xref\n0 1\n0000000000 65535 f\n%%EOF\n"
        )
        source = self.root / "plugins" / fixtures.PLUGIN / relative
        source.write_bytes(payload)
        git_fixture.init_repository(self.root, initial_branch="main")
        subprocess.run(
            ["git", "config", "core.autocrlf", "true"],
            cwd=self.root, check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "add", "--all"], cwd=self.root, check=True,
            capture_output=True, text=True,
        )
        source.unlink()
        subprocess.run(
            ["git", "checkout-index", "--force", "--", source.relative_to(self.root)],
            cwd=self.root, check=True, capture_output=True, text=True,
        )
        self.assertEqual(source.read_bytes(), payload)
        with tempfile.TemporaryDirectory() as output_dir:
            output = Path(output_dir) / "dist"
            build_distributions.build(self.root, output)
            for host in build_distributions.HOSTS:
                packaged = output / host / fixtures.PLUGIN / relative
                self.assertEqual(packaged.read_bytes(), payload)

    def test_distribution_check_reads_bytes_not_shallow_metadata(self):
        target = (
            self.root / "dist" / "claude" / fixtures.PLUGIN
            / "constitution.md"
        )
        original = target.read_bytes()
        metadata = target.stat()
        replacement = bytes([original[0] ^ 1]) + original[1:]
        target.write_bytes(replacement)
        os.utime(target, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))

        problems = build_distributions.check(self.root, self.root / "dist")
        self.assertTrue(
            any(str(target) in problem and "out of sync" in problem
                for problem in problems),
            problems,
        )

    def test_distribution_check_rejects_symlinked_generated_file(self):
        target = (
            self.root / "dist" / "claude" / fixtures.PLUGIN
            / "constitution.md"
        )
        canonical = (
            self.root / "plugins" / fixtures.PLUGIN / "constitution.md"
        )
        target.unlink()
        try:
            target.symlink_to(canonical)
        except OSError as exc:
            self.skipTest(f"fixture filesystem cannot create symlinks: {exc}")

        problems = build_distributions.check(self.root, self.root / "dist")
        self.assertTrue(
            any(str(target) in problem and "symbolic link" in problem
                for problem in problems),
            problems,
        )

    def test_distribution_check_rejects_symlinked_dist_root(self):
        moved = self.root / "assets" / "moved-dist"
        moved.parent.mkdir()
        (self.root / "dist").rename(moved)
        try:
            (self.root / "dist").symlink_to(moved, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"fixture filesystem cannot create symlinks: {exc}")

        problems = build_distributions.check(self.root, self.root / "dist")
        self.assertTrue(
            any("not a real directory" in problem for problem in problems),
            problems,
        )

    def test_canonical_source_rejects_unknown_component_and_symlink(self):
        plugin = self.root / "plugins" / fixtures.PLUGIN
        unknown = plugin / "runtime-state"
        unknown.mkdir()
        with self.assertRaisesRegex(ValueError, "unsupported canonical top-level"):
            build_distributions.validate_canonical(self.root)
        unknown.rmdir()
        target = plugin / "constitution.md"
        link = plugin / "flows" / "linked.md"
        link.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            build_distributions.validate_canonical(self.root)


class DistributionRuleTests(unittest.TestCase):
    """Builder rules proven by calling the deciding function on minimal input."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def generated_pair(self) -> tuple[Path, Path]:
        """Two equal generated trees: the expected rebuild and the committed one."""
        trees = []
        for name in ("expected", "actual"):
            package = self.base / name / "claude" / fixtures.PLUGIN
            (package / "scripts").mkdir(parents=True)
            (package / "constitution.md").write_bytes(b"# Constitution\n")
            script = package / "scripts" / "backlog_compile.py"
            script.write_bytes(b"print('compile')\n")
            script.chmod(0o755)
            trees.append(self.base / name)
        self.assertEqual(build_distributions.compare_dirs(*trees), [])
        return trees[0], trees[1]

    def test_distribution_check_binds_executable_modes(self):
        expected, actual = self.generated_pair()
        source = actual / "claude" / fixtures.PLUGIN / "scripts/backlog_compile.py"
        original_mode = source.stat().st_mode
        if not original_mode & 0o111:
            self.skipTest("fixture filesystem has no executable mode")
        source.chmod(original_mode & ~stat.S_IXUSR)
        problems = build_distributions.compare_dirs(expected, actual)
        self.assertTrue(
            any("out of sync executable mode" in problem for problem in problems),
            problems,
        )

    def test_distribution_check_rejects_python_runtime_cache(self):
        expected, actual = self.generated_pair()
        target = (
            actual / "claude" / fixtures.PLUGIN
            / "__pycache__" / "payload.cpython-39.pyc"
        )
        target.parent.mkdir()
        target.write_bytes(b"unattested runtime cache")

        problems = build_distributions.compare_dirs(expected, actual)
        self.assertTrue(
            any(str(target.parent) in problem and "stale" in problem
                for problem in problems),
            problems,
        )

    def snapshot_root(self, name: str, files: dict[str, bytes]) -> Path:
        """A root holding only the surfaces ``marketplace_snapshot`` hashes."""
        root = self.base / name
        for relative, content in {
            "package-modes.json": b"{}\n", "product.json": b"{}\n",
            "versions.json": b"{}\n", **files,
        }.items():
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_bytes(content)
        (root / "platforms").mkdir(exist_ok=True)
        return root

    def test_snapshot_framing_separates_binary_file_boundaries(self):
        first_relative = Path(
            f"plugins/{fixtures.PLUGIN}/skill-content/zz-collision-a.bin"
        )
        second_relative = Path(
            f"plugins/{fixtures.PLUGIN}/skill-content/zz-collision-b.bin"
        )
        first_payload = (
            b"prefix\0" + second_relative.as_posix().encode("utf-8")
            + b"\0suffix"
        )
        first = self.snapshot_root("repository", {first_relative.as_posix(): first_payload})
        other = self.snapshot_root("other", {
            first_relative.as_posix(): b"prefix", second_relative.as_posix(): b"suffix",
        })

        self.assertNotEqual(
            build_distributions.marketplace_snapshot(first)["build_id"],
            build_distributions.marketplace_snapshot(other)["build_id"],
        )

    @integration
    def test_snapshot_and_provenance_bind_the_package_mode_contract(self):
        relative = "scripts/backlog_compile.py"
        root = copy_root(shared_root(), self.base / "marketplace", with_dist=False)
        source = root / "plugins" / fixtures.PLUGIN / relative
        original_mode = source.stat().st_mode
        if not original_mode & 0o111:
            self.skipTest("fixture filesystem has no executable mode")
        before = build_distributions.marketplace_snapshot(root)["build_id"]
        baseline = json.loads((
            shared_root() / "dist" / "claude" / fixtures.PLUGIN
            / build_distributions.PROVENANCE
        ).read_text(encoding="utf-8"))
        self.assertIn(relative, baseline["executables"])

        source.chmod(original_mode & ~stat.S_IXUSR)
        checkout_mode_only = build_distributions.marketplace_snapshot(
            root
        )["build_id"]
        self.assertEqual(before, checkout_mode_only)
        mode_contract = root / "package-modes.json"
        mode_contract.write_bytes((json.dumps({
            "schema_version": 1,
            "packages": {fixtures.PLUGIN: {"executables": []}},
        }, indent=2) + "\n").encode("utf-8"))
        after = build_distributions.marketplace_snapshot(root)["build_id"]
        self.assertNotEqual(before, after)
        # A package's provenance lists exactly the executables this contract loads.
        changed = build_distributions.load_package_executables(root, fixtures.PLUGIN)
        self.assertNotIn(relative, changed)

    def test_python_runtime_caches_never_enter_distributions(self):
        source = self.base / "plugins" / fixtures.PLUGIN
        (source / "scripts").mkdir(parents=True)
        (source / "scripts" / "probe.py").write_bytes(b"print('probe')\n")
        cache = source / "scripts/__pycache__"
        cache.mkdir(exist_ok=True)
        (cache / "probe.cpython-39.pyc").write_bytes(b"cache")
        (source / "scripts" / "probe.pyc").write_bytes(b"cache")
        output = self.base / "cache-build"
        build_distributions.copy_canonical(source, output)
        self.assertTrue((output / "scripts" / "probe.py").is_file())
        self.assertEqual(list(output.rglob("__pycache__")), [])
        self.assertEqual(list(output.rglob("*.pyc")), [])

    @integration
    def test_documented_provenance_conflicts_are_the_ones_git_reports(self):
        # #311: the provenance serialization and its four descriptions must
        # name every case in which two queued PRs conflict in dist/.
        base = {name: "0" * 64 for name in "abcdef"}

        def put(name: str, digest: str):
            return lambda files: {**files, name: digest * 64}

        def drop(name: str):
            return lambda files: {key: value for key, value in files.items() if key != name}

        cases = (
            ("change neighbours", put("b", "1"), put("c", "2"), False),
            ("change beside an add after it", put("b", "1"), put("bb", "2"), False),
            ("change beside an add before it", put("c", "1"), put("bb", "2"), False),
            ("removal beside a change", drop("c"), put("b", "2"), False),
            ("removal before a change", drop("c"), put("d", "2"), False),
            ("removals one entry apart", drop("b"), drop("d"), False),
            ("last removed, earlier change", drop("f"), put("d", "2"), False),
            ("adds at different positions", put("bb", "1"), put("cc", "2"), False),
            ("add after the last, earlier change", put("g", "1"), put("e", "2"), False),
            ("change one file twice", put("c", "1"), put("c", "2"), True),
            ("adds at one sort position", put("bb", "1"), put("bc", "2"), True),
            ("add after a changed last entry", put("g", "1"), put("f", "2"), True),
            ("removal next to a removal", drop("b"), drop("c"), True),
            ("removal next to an add before it", drop("c"), put("bb", "2"), True),
            ("removal next to an add after it", drop("c"), put("cc", "2"), True),
            ("last removed, entry before changed", drop("f"), put("e", "2"), True),
        )

        def provenance(files: dict) -> str:
            return build_distributions.render_provenance({"component": "t", "files": files,
                                                          "schema_version": 4})

        # git merge-file runs the three-way text merge a branch merge runs on
        # the one provenance file both branches change.
        for index, (name, left, right, conflicts) in enumerate(cases):
            current, ancestor, other = (self.base / f"{index}-{side}" for side in
                                        ("right", "base", "left"))
            current.write_text(provenance(right(base)), encoding="utf-8")
            ancestor.write_text(provenance(base), encoding="utf-8")
            other.write_text(provenance(left(base)), encoding="utf-8")
            merged = subprocess.run(
                ["git", "merge-file", "-q", str(current), str(ancestor), str(other)],
                capture_output=True, text=True, check=False)
            with self.subTest(case=name):
                self.assertGreaterEqual(merged.returncode, 0, merged.stderr)
                self.assertLess(merged.returncode, 128, merged.stderr)
                self.assertEqual(bool(merged.returncode), conflicts, merged.stdout)
                if not conflicts:
                    self.assertEqual(current.read_text(encoding="utf-8"),
                                     provenance(right(left(base))))
        documents = {
            "render_provenance": build_distributions.render_provenance.__doc__,
            **{relative: (fixtures.REAL_REPOSITORY / relative).read_text(encoding="utf-8")
               for relative in ("docs/maintainer-operations-protocol.md",
                                "docs/upgrade-protocol.md")},
        }
        for where, text in documents.items():
            text = " ".join(text.split())
            with self.subTest(document=where):
                for conflict in ("adds at one sort position", "an add after a changed last entry",
                                 "a removal next to another removal or an add",
                                 "a removal of the last entry beside a change to the entry"
                                 " before it"):
                    self.assertIn(conflict, text)


def shipped_models(host: str) -> dict:
    """The models a host's shipped catalog pins, by model ID."""
    return json.loads(build_distributions.model_catalog_path(
        fixtures.REAL_REPOSITORY, host).read_text(encoding="utf-8"))["models"]


def shipped_profile(host: str) -> dict:
    """A host's shipped default profile: tier to model ID and effort, None for no effort.
    By the owner's decision of 1 Oct 2026 on #349 a profile names its model directly."""
    auto = json.loads(build_distributions.execution_profile_path(
        fixtures.REAL_REPOSITORY, host).read_text(encoding="utf-8"))["profiles"]["auto"]
    return {tier: (setting["model"], setting.get("effort"))
            for tier, setting in auto.items() if "model" in setting}


# Read from the shipped tables, so a model bump changes no test.
AUTO_MODELS = {host: shipped_profile(host) for host in ("claude", "codex")}


def catalog_model(host: str, *, besides: tuple = (), effort: str | None = None,
                  untiered: bool = False, effortless: bool = False,
                  lacking: str | None = None) -> str:
    """The first shipped catalog model of ``host`` with the traits a case needs."""
    tiered = {model for model, _effort in AUTO_MODELS[host].values()}
    for model, entry in sorted(shipped_models(host).items()):
        if model in besides or (untiered and model in tiered) or (effortless and entry["efforts"]) \
                or (effort is not None and effort not in entry["efforts"]) \
                or (lacking is not None and lacking in entry["efforts"]):
            continue
        return model
    raise AssertionError(f"the {host} catalog pins no model this case needs")
# The owner's decisions on #349 of 1 Oct 2026: no role defaults to these
# efforts, and every generated variant runs on the low tier.
UNPINNED_EFFORTS = {"max", "ultra"}
VARIANT_TIER = "low"
# Generated review panel reader variant: its canonical agent.
LENS_VARIANTS = {
    "backlog-reviewer-lens": "backlog-reviewer",
    "design-system-reviewer-lens": "design-system-reviewer",
    "solution-reviewer-lens": "solution-reviewer",
}
# Generated reader variant of the code review panel: its canonical agent.
CODE_REVIEW_LENS_VARIANTS = {"code-reviewer-lens": "code-reviewer"}
# Generated mechanical-tier writer variant: its canonical agent.
MECHANICAL_VARIANTS = {
    "devops-engineer-mechanical": "devops-engineer",
    "product-owner-mechanical": "product-owner",
    "qa-engineer-mechanical": "qa-engineer",
    "solution-architect-mechanical": "solution-architect",
}
REGISTRY = "skill-content/configure/data/process-switches.json"


def setting_lines(root: Path, host: str, tier: str) -> list[str]:
    """The model and effort frontmatter lines a dist agent on the tier carries."""
    if tier == "inherit":
        return ["model: inherit"] if host == "claude" else []
    model, effort = AUTO_MODELS[host][tier]
    key = "effort" if host == "claude" else "model_reasoning_effort"
    return [f"model: {model}"] + ([f"{key}: {effort}"] if effort else [])


def role_lines(root: Path, tier: str) -> list[str]:
    """The settings lines of a generated Codex role file on the tier."""
    return [f'{key} = "{value}"' for key, value in (
        line.split(": ", 1) for line in setting_lines(root, "codex", tier))]


def frontmatter_lines(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return lines[1:lines.index("---", 1)]


def role_settings(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("model")]


def codex_fast_model() -> str:
    """The Codex catalog model the high tier moves to in the codex_high_fast build."""
    return catalog_model("codex", besides=(AUTO_MODELS["codex"]["high"][0],), effort="max")


def edit_json(path: Path, mutate) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# Shared builds of one source change each, read by several tests.
SHARED_VARIANTS = {
    "codex_high_fast": lambda root: edit_json(
        build_distributions.execution_profile_path(root, "codex"),
        lambda table: table["profiles"]["auto"].update(
            high={"model": codex_fast_model(), "effort": "max"})),
    "no_mechanical_switch": lambda root: edit_json(
        root / "plugins" / fixtures.PLUGIN / REGISTRY,
        lambda data: data["switches"].pop("mechanical_pass_tier")),
}


@integration
class ExecutionProfileTests(unittest.TestCase):
    """Per-host catalogs pin each class; profile tables map tiers to a class
    and effort; inherit omits the model and keeps the effort."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = shared_root()
        self.tables = {
            host: build_distributions.execution_profile_path(self.root, host).read_bytes()
            for host in build_distributions.HOSTS
        }
        self.catalogs = {
            host: build_distributions.model_catalog_path(self.root, host).read_bytes()
            for host in build_distributions.HOSTS
        }
        # No codex on PATH: apply keeps the pins it cannot check against the
        # CLI's catalog; tools/tests/test_model_fallback.py runs that check.
        self.no_codex = Path(self.temporary.name) / "no-codex"
        self.no_codex.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @property
    def project(self) -> Path:
        project = Path(self.temporary.name) / "project"
        if not project.exists():
            git_fixture.init_repository(project, initial_branch="main")
        return project

    def use_copy(self, *, with_dist: bool = False) -> None:
        """Point the test at its own copy of the shared root, which it may change."""
        self.root = copy_root(shared_root(), Path(self.temporary.name) / "marketplace",
                              with_dist=with_dist)

    def render_agents(self, host: str) -> Path:
        """The agents directory a build of this root renders for ``host``, rendered alone."""
        adapter = build_distributions.load_adapters(self.root)[host]
        target = Path(self.temporary.name) / f"rendered-{host}"
        shutil.rmtree(target, ignore_errors=True)
        build_distributions.generate_agents(
            self.root / "plugins" / fixtures.PLUGIN, target, adapter,
            build_distributions.load_execution_profile(self.root, adapter))
        return target / "agents"

    def load_tables(self) -> None:
        """Load and check every table a build reads before it writes its output."""
        adapters = build_distributions.load_adapters(self.root)
        for adapter in adapters.values():
            build_distributions.load_execution_profile(self.root, adapter)
        build_distributions.require_host_cli_versions(self.root, adapters)

    def tiers(self) -> dict[str, str]:
        agents = self.root / "plugins" / fixtures.PLUGIN / "agents"
        return {
            path.stem: build_distributions.parse_frontmatter(path)[0]["reasoning"]
            for path in sorted(agents.glob("*.md"))
        }

    def registry(self) -> Path:
        return self.root / "plugins" / fixtures.PLUGIN / REGISTRY

    def assert_own_root(self) -> None:
        self.assertNotIn(self.root, _SHARED_ROOTS.values(), "change a copy, not a shared build")

    def edit_variants(self, mutate) -> None:
        self.assert_own_root()
        data = json.loads(self.registry().read_text(encoding="utf-8"))
        mutate(data["switches"]["review_panels"]["agent_variants"]["lens_panel"], data)
        self.registry().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def rendered_tiers(self) -> dict[str, str]:
        """Every generated agent's tier: canonical agents and their variants."""
        return {**self.tiers(), **{variant: VARIANT_TIER for variant in LENS_VARIANTS},
                **{variant: VARIANT_TIER for variant in CODE_REVIEW_LENS_VARIANTS},
                **{variant: VARIANT_TIER for variant in MECHANICAL_VARIANTS}}

    def edit_table(self, host: str, mutate) -> None:
        self.assert_own_root()
        path = build_distributions.execution_profile_path(self.root, host)
        table = json.loads(self.tables[host])
        mutate(table)
        path.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")

    def set_tier(self, host: str, tier: str, setting: dict) -> None:
        self.edit_table(host, lambda table: table["profiles"]["auto"].update(
            {tier: setting}
        ))

    def edit_catalog(self, host: str, mutate) -> None:
        self.assert_own_root()
        path = build_distributions.model_catalog_path(self.root, host)
        catalog = json.loads(self.catalogs[host])
        mutate(catalog)
        path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")

    def dist_agent(self, host: str, name: str, root: Path | None = None) -> Path:
        return (root or self.root) / "dist" / host / fixtures.PLUGIN / "agents" / f"{name}.md"

    def codex(self, *args: str, root: Path | None = None) -> subprocess.CompletedProcess:
        script = (
            (root or self.root) / "dist/codex" / fixtures.PLUGIN
            / "scripts/generate_codex_project.py"
        )
        return subprocess.run(
            [sys.executable, str(script), *args,
             "--project-root", str(self.project)],
            capture_output=True, text=True, check=False, timeout=120,
            # The model check reaches only a fake that lists nothing.
            env=dict(fixtures.isolated_hosts(os.environ, self.no_codex / "isolation"),
                     PYTHONDONTWRITEBYTECODE="1", PATH=str(self.no_codex)),
        )

    def codex_json(self, *args: str, root: Path | None = None) -> dict:
        result = self.codex(*args, root=root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def role_files(self) -> dict[str, str]:
        return {
            path.stem: path.read_text(encoding="utf-8")
            for path in sorted((self.project / ".codex/agents").glob("*.toml"))
        }

    def test_default_tables_render_the_declared_role_settings(self):
        for host in AUTO_MODELS:
            self.assertEqual(json.loads(self.tables[host])["profiles"]["auto"]["inherit"], {}, host)
        tiers = self.rendered_tiers()
        for name, tier in tiers.items():
            with self.subTest(agent=name):
                claude = frontmatter_lines(self.dist_agent("claude", name))
                self.assertEqual(claude[2], setting_lines(self.root, "claude", tier)[0])
                self.assertEqual(
                    [line for line in claude if line.startswith(("model", "effort"))],
                    setting_lines(self.root, "claude", tier),
                )
                codex = frontmatter_lines(self.dist_agent("codex", name))
                self.assertEqual(
                    [line for line in codex if line.startswith(("model", "reasoning"))],
                    setting_lines(self.root, "codex", tier),
                )
        generated = self.codex_json("apply", "--scope", "local")
        self.assertEqual(generated["execution_profile"], "auto")
        files = self.role_files()
        self.assertEqual(set(files), set(tiers))
        for name, text in files.items():
            self.assertEqual(role_settings(text), role_lines(self.root, tiers[name]), name)
        challenger = files["analysis-challenger"].splitlines()
        self.assertEqual(challenger[0], "# Generated by Agent Marketplace"
                                        " software-engineering-team; do not edit by hand.")
        # The header's last line stamps the package, the source agent and the
        # resolved settings, which the session start check compares.
        self.assertRegex(challenger[1], r"^# Rendered from software-engineering-team \S+"
                                        r" agents/analysis-challenger\.md sha256:[0-9a-f]{64}"
                                        r" settings sha256:[0-9a-f]{64}$")
        self.assertEqual(challenger[2], 'name = "analysis-challenger"')
        self.assertEqual(challenger[4:7],
                         role_lines(self.root, "high") + ['sandbox_mode = "read-only"'])
        self.assertTrue(challenger[7].startswith("developer_instructions = "))

    def test_claude_frontmatter_writes_effort_only_when_the_table_sets_one(self):
        self.use_copy()
        strong = catalog_model("claude", besides=(AUTO_MODELS["claude"]["high"][0],), effort="xhigh")
        self.set_tier("claude", "high", {"model": strong, "effort": "xhigh"})
        agents = self.render_agents("claude")
        tiers = self.rendered_tiers()
        for name, tier in tiers.items():
            with self.subTest(agent=name):
                lines = frontmatter_lines(agents / f"{name}.md")
                if tier == "high":
                    self.assertEqual(lines[2:4], [f"model: {strong}", "effort: xhigh"])
                    self.assertTrue(lines[4].startswith("output_contract:"))
                else:
                    self.assertEqual(
                        [line for line in lines if line.startswith(("model", "effort"))],
                        setting_lines(self.root, "claude", tier),
                    )

    def test_codex_role_files_carry_the_tier_model_and_effort(self):
        self.root = shared_root("codex_high_fast")
        fast = codex_fast_model()
        self.assertEqual(
            frontmatter_lines(self.dist_agent("codex", "code-reviewer"))[2:4],
            [f"model: {fast}", "model_reasoning_effort: max"],
        )
        self.codex_json("apply", "--scope", "local")
        tiers = self.rendered_tiers()
        for name, text in self.role_files().items():
            self.assertEqual(
                role_settings(text),
                [f'model = "{fast}"', 'model_reasoning_effort = "max"']
                if tiers[name] == "high" else role_lines(self.root, tiers[name]),
                name,
            )

    def test_a_model_bump_moves_every_tier_that_names_the_model(self):
        # A bump renames the catalog entry and every tier that names it.
        self.use_copy()
        committed = shared_root() / "dist" / "claude" / fixtures.PLUGIN / "agents"
        self.assertEqual(build_distributions.compare_dirs(committed, self.render_agents("claude")),
                         [])
        pinned = AUTO_MODELS["claude"][VARIANT_TIER][0]
        family, version = build_distributions.load_adapters(self.root)["claude"].module.model_version(
            pinned)
        bumped = f"claude-{family}-{version[0] + 1}"
        self.assertNotIn(bumped, shipped_models("claude"))
        before = build_distributions.marketplace_snapshot(self.root)["build_id"]
        self.edit_catalog("claude", lambda catalog: catalog.update(models={
            (bumped if model == pinned else model): entry
            for model, entry in catalog["models"].items()}))
        self.edit_table("claude", lambda table: [
            setting.update(model=bumped)
            for setting in table["profiles"]["auto"].values()
            if setting.get("model") == pinned])
        self.assertNotEqual(build_distributions.marketplace_snapshot(self.root)["build_id"], before)
        agents = self.render_agents("claude")
        self.assertTrue(any("out of sync" in problem for problem in
                            build_distributions.compare_dirs(committed, agents)))
        on_bumped = {tier for tier, (model, _effort) in AUTO_MODELS["claude"].items()
                     if model == pinned}
        for name, tier in self.rendered_tiers().items():
            with self.subTest(agent=name):
                model = frontmatter_lines(agents / f"{name}.md")[2]
                if tier in on_bumped:
                    self.assertEqual(model, f"model: {bumped}")
                else:
                    self.assertEqual(model, setting_lines(self.root, "claude", tier)[0])
                    self.assertNotEqual(model, f"model: {bumped}")

    def test_single_reviewers_keep_their_tier_beside_their_lens_variants(self):
        self.root = shared_root("codex_high_fast")
        fast = codex_fast_model()
        # No canonical agent runs on the variants' tier by default.
        self.assertEqual({name for name, tier in self.tiers().items() if tier == VARIANT_TIER}, set())
        for variant, agent in sorted({**LENS_VARIANTS, **CODE_REVIEW_LENS_VARIANTS}.items()):
            with self.subTest(variant=variant):
                base = frontmatter_lines(self.dist_agent("claude", agent))
                self.assertEqual(base[2:4], setting_lines(self.root, "claude", "high"))
                claude = frontmatter_lines(self.dist_agent("claude", variant))
                self.assertEqual(claude[0], f"name: {variant}")
                self.assertEqual(claude[1], f"{base[1]} Lens reader variant for review panels.")
                self.assertEqual(claude[2:4], setting_lines(self.root, "claude", VARIANT_TIER))
                self.assertEqual(claude[4:], base[4:])
                # The variant keeps the role's body, boundaries and identity.
                body = self.dist_agent("claude", agent).read_text(encoding="utf-8").split("\n---\n", 1)[1]
                self.assertEqual(
                    self.dist_agent("claude", variant).read_text(encoding="utf-8").split("\n---\n", 1)[1],
                    body)
                codex = frontmatter_lines(self.dist_agent("codex", variant))
                self.assertEqual(
                    [line for line in codex if line.startswith(("model", "reasoning"))],
                    setting_lines(self.root, "codex", VARIANT_TIER),
                )
        self.codex_json("apply", "--scope", "local")
        files = self.role_files()
        for variant, agent in sorted({**LENS_VARIANTS, **CODE_REVIEW_LENS_VARIANTS}.items()):
            with self.subTest(codex=variant):
                self.assertEqual(role_settings(files[variant]), role_lines(self.root, VARIANT_TIER))
                self.assertEqual(role_settings(files[agent]),
                                 [f'model = "{fast}"', 'model_reasoning_effort = "max"'])
                # A variant keeps its base role's sandbox: the document reviewers read only.
                self.assertEqual('sandbox_mode = "read-only"' in files[variant],
                                 'sandbox_mode = "read-only"' in files[agent])
                if variant in LENS_VARIANTS:
                    self.assertIn('sandbox_mode = "read-only"', files[variant])

    def test_builder_refuses_agent_variants_it_cannot_render(self):
        self.use_copy(with_dist=True)
        source = self.root / "plugins" / fixtures.PLUGIN
        tiers = set(json.loads((self.root / "tools/data/models.json").read_text(
            encoding="utf-8"))["reasoning_levels"])
        self.assertEqual(
            [name for _agent, name, _tier, _text in build_distributions.agent_variants(source, tiers)],
            sorted(CODE_REVIEW_LENS_VARIANTS) + sorted(MECHANICAL_VARIANTS) + sorted(LENS_VARIANTS))
        original = self.registry().read_bytes()
        for mutate, fragment in (
                (lambda variant, _data: variant.update(agents=["ghost-reviewer"]),
                 "names unknown agent 'ghost-reviewer'"),
                (lambda variant, _data: variant.update(tier="extreme"), "names unknown tier 'extreme'"),
                (lambda variant, _data: variant.update(description="Lens: reader"),
                 "needs a one-line description without a colon"),
                (lambda variant, _data: variant.update(agents=["backlog-reviewer", "backlog-reviewer"]),
                 "collides with another agent"),
                (lambda variant, _data: variant.pop("suffix"), "agent_variants must declare")):
            with self.subTest(fragment=fragment):
                self.registry().write_bytes(original)
                self.edit_variants(mutate)
                with self.assertRaisesRegex(ValueError, fragment):
                    build_distributions.agent_variants(source, tiers)
        # The build refuses before it removes the generated tree.
        with self.assertRaisesRegex(ValueError, "agent_variants must declare"):
            build_distributions.replace_generated(self.root, self.root / "dist")
        self.assertTrue(self.dist_agent("claude", "backlog-reviewer-lens").is_file())
        self.registry().unlink()
        self.assertEqual(build_distributions.agent_variants(source, tiers), [])

    def test_lens_tier_panel_data_and_variants_are_build_inputs(self):
        self.use_copy()
        before = build_distributions.marketplace_snapshot(self.root)["build_id"]
        self.set_tier("claude", VARIANT_TIER, {
            "model": catalog_model("claude", besides=(AUTO_MODELS["claude"][VARIANT_TIER][0],),
                                   effort="high"), "effort": "high"})
        retiered = build_distributions.marketplace_snapshot(self.root)["build_id"]
        self.assertNotEqual(retiered, before)
        panels = (
            self.root / "plugins" / fixtures.PLUGIN
            / "skill-content/challenge-review/data/review-panels.json"
        )
        data = json.loads(panels.read_text(encoding="utf-8"))
        data["review_steps"]["backlog_epic"]["default_panel"] = [[
            lens for assignment in data["review_steps"]["backlog_epic"]["default_panel"]
            for lens in assignment
        ]]
        panels.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        regrouped = build_distributions.marketplace_snapshot(self.root)["build_id"]
        self.assertNotEqual(regrouped, retiered)
        self.edit_variants(lambda variant, _data: variant["agents"].remove("solution-reviewer"))
        self.assertNotEqual(
            build_distributions.marketplace_snapshot(self.root)["build_id"], regrouped,
        )

    def test_writers_keep_their_tier_beside_their_mechanical_variants(self):
        settings = {host: setting_lines(self.root, host, VARIANT_TIER)
                    for host in build_distributions.HOSTS}
        suffix = " Writer variant for passes that apply only the fixes a review verdict names."
        tiers = self.tiers()
        for variant, agent in sorted(MECHANICAL_VARIANTS.items()):
            self.assertNotEqual(tiers[agent], VARIANT_TIER)
            for host in build_distributions.HOSTS:
                with self.subTest(variant=variant, host=host):
                    base_text = self.dist_agent(host, agent).read_text(encoding="utf-8")
                    text = self.dist_agent(host, variant).read_text(encoding="utf-8")
                    # The variant keeps the writer's body, boundaries and identity.
                    self.assertEqual(text.split("\n---\n", 1)[1], base_text.split("\n---\n", 1)[1])
                    base, lines = (frontmatter_lines(self.dist_agent(host, name))
                                   for name in (agent, variant))
                    self.assertEqual(lines[:2], [f"name: {variant}", base[1] + suffix])
                    self.assertEqual(
                        [line for line in lines[2:] if not line.startswith(("model", "effort"))],
                        [line for line in base[2:] if not line.startswith(("model", "effort"))])
                    self.assertEqual([line for line in lines if line.startswith(("model", "effort"))],
                                     settings[host])
        self.codex_json("apply", "--scope", "local")
        files = self.role_files()
        for variant, agent in sorted(MECHANICAL_VARIANTS.items()):
            with self.subTest(codex=variant):
                self.assertEqual(role_settings(files[variant]), role_lines(self.root, VARIANT_TIER))
                self.assertEqual(role_settings(files[agent]), role_lines(self.root, tiers[agent]))
                self.assertNotIn("sandbox_mode", files[variant])
                instructions = [line for line in files[variant].splitlines()
                                if line.startswith("developer_instructions")]
                self.assertEqual(instructions, [line for line in files[agent].splitlines()
                                                if line.startswith("developer_instructions")])

    def test_base_agents_and_role_files_do_not_depend_on_the_mechanical_switch(self):
        def agents(root: Path) -> dict:
            return {host: {path.name: path.read_bytes() for path in sorted(
                (root / "dist" / host / fixtures.PLUGIN / "agents").glob("*.md"))}
                for host in build_distributions.HOSTS}

        self.codex_json("apply", "--scope", "local")
        with_switch, role_files = agents(self.root), self.role_files()
        # The same sources built without the mechanical_pass_tier switch.
        without_root = shared_root("no_mechanical_switch")
        without = agents(without_root)
        for host in build_distributions.HOSTS:
            with self.subTest(host=host):
                self.assertEqual(set(with_switch[host]) - set(without[host]),
                                 {f"{variant}.md" for variant in MECHANICAL_VARIANTS})
                self.assertEqual({name: with_switch[host][name] for name in without[host]},
                                 without[host])
        self.codex_json("apply", "--scope", "local", root=without_root)
        after = self.role_files()
        self.assertEqual({name: text for name, text in role_files.items()
                          if name not in MECHANICAL_VARIANTS},
                         {name: after[name] for name in role_files if name not in MECHANICAL_VARIANTS})

    def test_inherit_profile_omits_the_model_keeps_the_effort_and_survives_refresh(self):
        self.codex_json("apply", "--scope", "local")
        auto_files = self.role_files()
        self.assertTrue(all(role_settings(text) for text in auto_files.values()))

        inherited = self.codex_json(
            "apply", "--scope", "local", "--execution-profile", "inherit",
        )
        self.assertEqual(inherited["execution_profile"], "inherit")
        self.assertEqual(len(inherited["written"]), len(auto_files))
        tiers = self.rendered_tiers()
        for name, text in self.role_files().items():
            lines = text.splitlines()
            self.assertEqual(lines[1], "# Execution profile: inherit", name)
            # Without its effort a role would run the parent's, `low` by
            # default for Sol in Codex 0.159.
            self.assertEqual(role_settings(text), role_lines(self.root, tiers[name])[1:], name)
            self.assertEqual(
                [line for line in lines if line.startswith("sandbox_mode")],
                [line for line in auto_files[name].splitlines()
                 if line.startswith("sandbox_mode")],
            )

        checked = self.codex_json("check", "--scope", "local")
        self.assertEqual(
            (checked["execution_profile"], checked["changes"]), ("inherit", []),
        )
        refreshed = self.codex_json("apply", "--scope", "all", "--seed-user-files")
        self.assertEqual(refreshed["execution_profile"], "inherit")
        self.assertEqual(
            [path for path in refreshed["changes"] if path.startswith(".codex/")], [],
        )

        restored = self.codex_json(
            "apply", "--scope", "local", "--execution-profile", "auto",
        )
        self.assertEqual(restored["execution_profile"], "auto")
        self.assertEqual(self.role_files(), auto_files)

    def test_profile_selection_fails_closed(self):
        self.use_copy(with_dist=True)
        tracked = self.codex(
            "check", "--scope", "tracked", "--execution-profile", "inherit",
        )
        self.assertNotEqual(tracked.returncode, 0)
        self.assertIn("only to the local agent projection", tracked.stderr)

        self.codex_json("apply", "--scope", "local", "--execution-profile", "inherit")
        inherited = self.role_files()
        agents_dir = self.project / ".codex/agents"
        lines = inherited["analysis-challenger"].splitlines(keepends=True)
        (agents_dir / "analysis-challenger.toml").write_text(
            "".join([lines[0], *lines[2:]]), encoding="utf-8",
        )
        mixed = self.codex("check", "--scope", "local")
        self.assertNotEqual(mixed.returncode, 0)
        self.assertIn("record different execution profiles", mixed.stderr)
        for name, text in inherited.items():
            (agents_dir / f"{name}.toml").write_text(text.replace(
                "# Execution profile: inherit", "# Execution profile: fast",
            ), encoding="utf-8")
        unsupported = self.codex("check", "--scope", "local")
        self.assertNotEqual(unsupported.returncode, 0)
        self.assertIn("unsupported execution profile 'fast'", unsupported.stderr)
        converged = self.codex_json(
            "apply", "--scope", "local", "--execution-profile", "inherit",
        )
        self.assertEqual(len(converged["changes"]), len(inherited))
        self.assertEqual(self.role_files(), inherited)

        agent = self.dist_agent("codex", "analysis-challenger")
        agent.write_text(agent.read_text(encoding="utf-8").replace(
            setting_lines(self.root, "codex", "high")[1], "reasoning: high",
        ), encoding="utf-8")
        stale = self.codex("check", "--scope", "local")
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("unresolved reasoning tier", stale.stderr)

    def test_unreadable_role_target_reports_a_collision_not_a_traceback(self):
        (self.project / ".codex/agents/analysis-challenger.toml").mkdir(parents=True)
        result = self.codex("check", "--scope", "local")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unmanaged Codex agent collision", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_tables_are_build_inputs_and_invalid_tables_fail_the_build(self):
        self.use_copy()
        before = build_distributions.marketplace_snapshot(self.root)["build_id"]
        self.set_tier("claude", "medium", {
            "model": catalog_model("claude", besides=(AUTO_MODELS["claude"]["medium"][0],),
                                   effort="medium"), "effort": "medium"})
        self.assertNotEqual(
            build_distributions.marketplace_snapshot(self.root)["build_id"], before,
        )
        # The shared build's dist/ is what this copy's unchanged sources build.
        self.assertTrue(any(
            "out of sync" in problem
            for problem in build_distributions.check(self.root, shared_root() / "dist")
        ))

        def tier(name: str, setting: dict):
            return lambda table: table["profiles"]["auto"].update({name: setting})

        def pin(model: str, **fields):
            return lambda catalog: catalog["models"][model].update(fields)

        def rename(model: str, to: str):
            """A catalog whose entry for ``model`` is keyed ``to``; no tier names it."""
            return lambda catalog: catalog.update(models={
                (to if name == model else name): entry
                for name, entry in catalog["models"].items()})

        # Models of the shipped catalogs with the traits each case needs.
        claude_pinned = AUTO_MODELS["claude"]["high"][0]
        effortless = catalog_model("claude", untiered=True, effortless=True)
        codex_pinned = AUTO_MODELS["codex"]["high"][0]
        codex_untiered = catalog_model("codex", untiered=True, lacking="ultra")
        family = shipped_models("claude")[claude_pinned]["family"]
        other_family = next(entry["family"] for _model, entry in sorted(shipped_models("claude").items())
                            if entry["family"] != family)
        profile_cases = (
            ("claude", tier("high", {"model": "claude-ghost-9"}),
             "unknown model 'claude-ghost-9'; the catalog pins"),
            ("claude", tier("high", {"class": "frontier"}),
             "may hold only non-empty string model and effort"),
            ("claude", tier("medium", {"effort": "high"}), "must name a model"),
            ("claude", tier("low", {"model": effortless, "effort": "high"}),
             "effort 'high' is not supported by .*, which takes no effort"),
            ("claude", tier("low", {"model": effortless, "effort": "extreme"}),
             "effort must be one of"),
            ("claude", tier("inherit", {"model": claude_pinned}), "inherit tier"),
            ("codex", tier("high", {"model": codex_pinned, "effort": "extreme"}),
             "effort must be one of"),
            ("codex", tier("low", {"model": codex_untiered, "effort": "ultra"}),
             "effort 'ultra' is not supported by"),
            ("codex", tier("inherit", {"effort": "low"}), "inherit tier"),
            ("codex", lambda t: t["profiles"]["auto"]["medium"].update(temperature="1"),
             "may hold only"),
            ("codex", lambda t: t["profiles"]["auto"].pop("low"),
             "must map exactly the reasoning tiers"),
            ("claude", lambda t: t["profiles"].update(fast={}),
             "must define exactly 'auto'"),
            ("claude", lambda t: t.update(schema_version=2),
             "schema_version 3"),
        )
        catalog_cases = (
            ("claude", rename(effortless, "opus"), "'opus' is not a pinned model ID"),
            ("claude", rename(effortless, "claude-haiku-4-5"),
             "'claude-haiku-4-5' is not a pinned model ID"),
            ("claude", rename(effortless, "claude-sonnet-5-5-20260901"), "is not a pinned model ID"),
            ("claude", rename(effortless, "gpt-6.1-sol"), "is not a pinned model ID"),
            ("claude", pin(claude_pinned, family=other_family),
             f"belongs to family '{family}', not '{other_family}'"),
            ("codex", rename(codex_untiered, "gpt-5.5"), "'gpt-5.5' is not a pinned model ID"),
            ("codex", rename(codex_untiered, "gpt 6 luna"), "is not a pinned model ID"),
            ("codex", pin(codex_pinned, efforts=["turbo"]), "efforts must list distinct values"),
            ("codex", pin(codex_pinned, efforts=["high", "high"]), "efforts must list distinct values"),
            ("claude", pin(claude_pinned, sources=[]), "sources must list"),
            ("claude", pin(claude_pinned, sources=["http://example.com"]), "sources must list"),
            ("claude", pin(claude_pinned, verified="01.10.2026"), "verified must be"),
            ("claude", pin(claude_pinned, verified="2026-02-30"), "verified must be"),
            ("claude", pin(effortless, min_cli_version="2.1"), "min_cli_version must be"),
            ("codex", pin(codex_untiered, min_cli_version="v0.157.0"), "min_cli_version must be"),
            ("codex", lambda c: c["models"][codex_pinned].pop("min_cli_version"), "must hold exactly"),
            ("claude", pin(claude_pinned, id=claude_pinned), "must hold exactly"),
            # CI installs the pinned CLI and starts no role, so the build refuses
            # a pin below the oldest CLI that runs a catalog model.
            ("codex", pin(codex_pinned, min_cli_version="9.0.0"),
             f"is below 9.0.0, the minimum of codex model '{codex_pinned}'"),
            ("claude", pin(claude_pinned, min_cli_version="3.0.0"),
             f"is below 3.0.0, the minimum of claude model '{claude_pinned}'"),
            ("codex", lambda c: c["models"][codex_pinned].pop("verified"), "must hold exactly"),
            ("codex", lambda c: c.update(schema_version=1), "schema_version 2 and models"),
            ("codex", lambda c: c.update(classes=c.pop("models")), "schema_version 2 and models"),
            ("claude", lambda c: c.update(models={}), "at least one exact model ID"),
        )

        def restore() -> None:
            for name in build_distributions.HOSTS:
                build_distributions.execution_profile_path(
                    self.root, name).write_bytes(self.tables[name])
                build_distributions.model_catalog_path(
                    self.root, name).write_bytes(self.catalogs[name])

        cases = [(host, self.edit_table, mutate, message)
                 for host, mutate, message in profile_cases]
        cases += [(host, self.edit_catalog, mutate, message)
                  for host, mutate, message in catalog_cases]
        for index, (host, edit, mutate, message) in enumerate(cases):
            with self.subTest(host=host, message=message):
                restore()
                edit(host, mutate)
                with self.assertRaisesRegex(ValueError, message):
                    self.load_tables()
                if index == 0:
                    # One real build: it refuses before it writes any output.
                    with self.assertRaisesRegex(ValueError, message):
                        build_distributions.build(
                            self.root, Path(self.temporary.name) / f"out-{index}",
                        )
                    self.assertFalse((Path(self.temporary.name) / f"out-{index}").exists())

        pins = self.root / build_distributions.HOST_CLI_VERSIONS_RELPATH
        pinned = pins.read_bytes()
        for path, message in (
                (build_distributions.execution_profile_path(self.root, "codex"),
                 "missing or invalid execution profile"),
                (build_distributions.model_catalog_path(self.root, "claude"),
                 "missing or invalid model catalog"),
                (pins, "missing or invalid host CLI versions")):
            with self.subTest(missing=path.name):
                restore()
                pins.write_bytes(pinned)
                path.unlink()
                with self.assertRaisesRegex(ValueError, message):
                    self.load_tables()
        pins.write_bytes(pinned)
        # One patch below each host's highest catalog minimum.
        below = {}
        for host, key in (("claude", "claude_code"), ("codex", "codex")):
            major, minor, patch = max(tuple(int(part) for part in entry["min_cli_version"].split("."))
                                      for entry in shipped_models(host).values())
            below[key] = f"{major}.{minor}.{patch - 1}" if patch else f"{major}.{minor - 1}.999"
        for index, (key, version) in enumerate(below.items()):
            with self.subTest(pin=key):
                restore()
                pins.write_text(json.dumps({**json.loads(pinned), key: version}), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, f"{key} {version} is below"):
                    self.load_tables()
                if index == 0:
                    # One real build: the pin check also precedes any output.
                    with self.assertRaisesRegex(ValueError, f"{key} {version} is below"):
                        build_distributions.build(self.root, Path(self.temporary.name) / key)
                    self.assertFalse((Path(self.temporary.name) / key).exists())
        pins.write_bytes(pinned)

    def test_model_and_effort_lines_are_the_named_exception_to_default_equivalence(self):
        # Pinned models change every role's settings with no Process Policy (#344),
        # so invariant 30 and the release note must say they follow the catalog.
        exception = ("the model and effort lines of rendered agents follow each host's model"
                     " catalog and execution profile, not default equivalence")
        repository = fixtures.REAL_REPOSITORY
        architecture = " ".join((repository / "docs/architecture.md").read_text(
            encoding="utf-8").split())
        invariant = architecture.split(" 30. ", 1)[1].split(" 31. ", 1)[0]
        self.assertIn(exception, invariant)
        # The rule names both of its exceptions in one place.
        self.assertIn("Two exceptions stand outside this rule:", invariant)
        self.assertIn("a user-armed session entry such as autopilot ships without a switch,"
                      " because without an active grant its hooks exit silently, no task binds it",
                      invariant)

    def test_every_tier_pins_the_owners_effort_and_the_docs_say_why(self):
        # A Claude Code role without `effort` follows the session's level,
        # `max` included, so every tier whose model takes an effort pins one.
        # By the owner's decisions of 1 Oct 2026 on #349 none defaults to max
        # or ultra, and only the high, medium and low tiers remain.
        repository = fixtures.REAL_REPOSITORY
        for host in build_distributions.HOSTS:
            models = json.loads(build_distributions.model_catalog_path(
                repository, host).read_text(encoding="utf-8"))["models"]
            auto = json.loads(build_distributions.execution_profile_path(
                repository, host).read_text(encoding="utf-8"))["profiles"]["auto"]
            self.assertEqual(set(auto), {"high", "medium", "low", "inherit"}, host)
            for tier, setting in auto.items():
                if tier == "inherit":
                    continue
                with self.subTest(host=host, tier=tier):
                    self.assertEqual("effort" in setting, bool(models[setting["model"]]["efforts"]))
                    self.assertNotIn(setting.get("effort"), UNPINNED_EFFORTS)
            # Every catalog model stays, the ones no tier runs included.
            self.assertTrue(set(models) - {setting.get("model") for setting in auto.values()},
                            host)
        # Each host contract states the shipped profile.
        contracts = {host: " ".join((repository / "platforms" / host / fixtures.PLUGIN
                                     / "host-contract.md").read_text(encoding="utf-8").split())
                     for host in build_distributions.HOSTS}
        claude, codex = AUTO_MODELS["claude"], AUTO_MODELS["codex"]
        self.assertIn(f"The high tier runs `{claude['high'][0]}` at effort `{claude['high'][1]}`, the"
                      f" medium tier `{claude['medium'][0]}` at effort `{claude['medium'][1]}` and the"
                      f" low tier `{claude['low'][0]}` at effort `{claude['low'][1]}`", contracts["claude"])
        # Every Codex tier stays on one effort; lowering the low tier, which
        # only the generated variants run, waits for the frozen-task A/B (#404).
        self.assertEqual(set(codex.values()), {codex["high"]})
        self.assertIn(f"The high, medium and low tiers all run `{codex['high'][0]}` at effort"
                      f" `{codex['high'][1]}`", contracts["codex"])

    def test_host_contracts_state_the_oldest_cli_the_pinned_models_need(self):
        adapters = build_distributions.load_adapters(self.root)
        for host, adapter in adapters.items():
            catalog = json.loads(self.catalogs[host])
            minimums = [entry["min_cli_version"] for entry in catalog["models"].values()]
            newest = max(minimums, key=build_distributions.cli_version)
            contract = " ".join((self.root / "platforms" / host / fixtures.PLUGIN
                                 / "host-contract.md").read_text(encoding="utf-8").split())
            with self.subTest(host=host):
                self.assertIn(f"The pinned models need {adapter.metadata['display_name']}"
                              f" {newest} or later", contract)
                for minimum in minimums:
                    self.assertIn(minimum, contract)

    def test_each_adapter_accepts_only_its_documented_pinned_ids(self):
        adapters = build_distributions.load_adapters(self.root)
        cases = {
            "claude": {
                "claude-opus-5-5": ("opus", (5, 5, 0)),
                "claude-sonnet-5": ("sonnet", (5, 0, 0)),
                "claude-haiku-4-5-20251001": ("haiku", (4, 5, 20251001)),
                "claude-opus-4-20250514": ("opus", (4, 0, 20250514)),
                "claude-haiku-4-5": None,
                "claude-opus-5-5-20260922": None,
                "claude-opus-5-5[1m]": None,
                "opus": None,
                "inherit": None,
                "gpt-6.1-sol": None,
            },
            "codex": {
                "gpt-6.1-sol": ("sol", (6, 1)),
                "gpt-6-luna": ("luna", (6, 0)),
                "gpt-5.3-codex-spark": ("codex-spark", (5, 3)),
                "gpt-5.5": None,
                "codex-auto-review": None,
                "gpt-6.1-sol ": None,
                "claude-opus-5-5": None,
            },
        }
        for host, expected in cases.items():
            for model_id, version in expected.items():
                with self.subTest(host=host, model_id=model_id):
                    self.assertEqual(adapters[host].module.model_version(model_id), version)


if __name__ == "__main__":
    unittest.main()
