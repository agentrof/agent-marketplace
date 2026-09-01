"""Standalone-team and cross-host distribution contracts."""

from __future__ import annotations

import hashlib
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


class SingleTeamDistributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        fixtures.make_valid_root(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_session_marker_publishes_exact_writer_invocation_binding(self):
        repository = TESTS_DIR.parents[1]
        for host in build_distributions.HOSTS:
            with self.subTest(host=host):
                script = (
                    repository / "dist" / host / "software-engineering-team"
                    / "scripts" / "team_guard.py"
                )
                result = subprocess.run(
                    [sys.executable, str(script), "register"],
                    capture_output=True, text=True, check=False,
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
            snapshots.append({
                key: provenance[key] for key in (
                    "build_id",
                    "marketplace_release",
                    "source_channel",
                    "source_ref",
                    "source_commit",
                )
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

    def test_snapshot_normalizes_checkout_only_eol_drift(self):
        # Exercise the snapshot normalizer independently of the repository's
        # fail-closed LF checkout policy.
        (self.root / ".gitattributes").write_bytes(
            b"*.csv -text\ndist/** -text\n"
        )
        subprocess.run(
            ["git", "init", "-b", "main"], cwd=self.root, check=True,
            capture_output=True, text=True,
        )
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
        subprocess.run(
            ["git", "init", "-b", "main"], cwd=self.root, check=True,
            capture_output=True, text=True,
        )
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

    def test_snapshot_paths_use_case_sensitive_posix_order(self):
        paths = build_distributions.snapshot_files(self.root, "plugins")
        relative = [path.relative_to(self.root).as_posix() for path in paths]
        self.assertEqual(relative, sorted(relative))
        self.assertNotEqual(relative, sorted(relative, key=str.casefold))

    def test_snapshot_framing_separates_binary_file_boundaries(self):
        with tempfile.TemporaryDirectory() as other_dir:
            other = Path(other_dir) / "repository"
            fixtures.make_valid_root(other)
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
            first_path = self.root / first_relative
            second_first_path = other / first_relative
            second_path = other / second_relative
            first_path.write_bytes(first_payload)
            second_first_path.write_bytes(b"prefix")
            second_path.write_bytes(b"suffix")

            self.assertNotEqual(
                build_distributions.marketplace_snapshot(self.root)["build_id"],
                build_distributions.marketplace_snapshot(other)["build_id"],
            )

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
        subprocess.run(
            ["git", "init", "-b", "main"], cwd=self.root, check=True,
            capture_output=True, text=True,
        )
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

    def test_snapshot_and_provenance_bind_the_package_mode_contract(self):
        relative = "scripts/backlog_compile.py"
        source = self.root / "plugins" / fixtures.PLUGIN / relative
        original_mode = source.stat().st_mode
        if not original_mode & 0o111:
            self.skipTest("fixture filesystem has no executable mode")
        before = build_distributions.marketplace_snapshot(self.root)["build_id"]
        baseline = json.loads((
            self.root / "dist" / "claude" / fixtures.PLUGIN
            / build_distributions.PROVENANCE
        ).read_text(encoding="utf-8"))
        self.assertIn(relative, baseline["executables"])

        source.chmod(original_mode & ~stat.S_IXUSR)
        checkout_mode_only = build_distributions.marketplace_snapshot(
            self.root
        )["build_id"]
        self.assertEqual(before, checkout_mode_only)
        mode_contract = self.root / "package-modes.json"
        mode_contract.write_bytes((json.dumps({
            "schema_version": 1,
            "packages": {fixtures.PLUGIN: {"executables": []}},
        }, indent=2) + "\n").encode("utf-8"))
        after = build_distributions.marketplace_snapshot(self.root)["build_id"]
        self.assertNotEqual(before, after)
        with tempfile.TemporaryDirectory() as output_dir:
            output = Path(output_dir) / "dist"
            build_distributions.build(self.root, output)
            changed = json.loads((
                output / "claude" / fixtures.PLUGIN
                / build_distributions.PROVENANCE
            ).read_text(encoding="utf-8"))
        self.assertNotIn(relative, changed["executables"])

    def test_distribution_check_binds_executable_modes(self):
        source = (
            self.root / "dist" / "claude" / fixtures.PLUGIN
            / "scripts/backlog_compile.py"
        )
        original_mode = source.stat().st_mode
        if not original_mode & 0o111:
            self.skipTest("fixture filesystem has no executable mode")
        source.chmod(original_mode & ~stat.S_IXUSR)
        problems = build_distributions.check(self.root, self.root / "dist")
        self.assertTrue(
            any("out of sync executable mode" in problem for problem in problems),
            problems,
        )

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

    def test_distribution_check_rejects_python_runtime_cache(self):
        target = (
            self.root / "dist" / "claude" / fixtures.PLUGIN
            / "__pycache__" / "payload.cpython-39.pyc"
        )
        target.parent.mkdir()
        target.write_bytes(b"unattested runtime cache")

        problems = build_distributions.check(self.root, self.root / "dist")
        self.assertTrue(
            any(str(target.parent) in problem and "stale" in problem
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

    def test_canonical_source_rejects_symlinked_surface_roots(self):
        for surface_name in ("plugins", "platforms"):
            with self.subTest(surface=surface_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "repository"
                fixtures.make_valid_root(root)
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

    def test_python_runtime_caches_never_enter_distributions(self):
        cache = self.root / "plugins" / fixtures.PLUGIN / "scripts/__pycache__"
        cache.mkdir(exist_ok=True)
        (cache / "probe.cpython-39.pyc").write_bytes(b"cache")
        output = self.root / "cache-build"
        build_distributions.build(self.root, output)
        self.assertEqual(list(output.rglob("__pycache__")), [])
        self.assertEqual(list(output.rglob("*.pyc")), [])

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
                self.assertFalse((package / "scripts/issue_compile.py").exists())
                self.assertFalse(
                    (package / "templates/vault/maps/issues.md").exists()
                )

                policy = json.loads((
                    package
                    / "skill-content/obsidian-vault/data/vault-policy.json"
                ).read_text(encoding="utf-8"))
                self.assertNotIn("issues", policy["subtrees"])
                self.assertNotIn("issue-report", policy["extra_doc_types"])
                self.assertNotIn("issue_report", policy["type_path_patterns"])
                self.assertNotIn("issue_report", policy["status_values"])
                self.assertNotIn(
                    "issue-report", policy["fragment_graph_groups"]["backlog"]
                )
                self.assertNotIn(
                    "issue-report",
                    {group["id"] for group in policy["graph_color_groups"]},
                )

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
            self.assertIn("reasoning:", codex)


if __name__ == "__main__":
    unittest.main()
