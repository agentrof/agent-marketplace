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
import git_fixture  # noqa: E402


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
        first, second, added = next(
            (left, right, left[:-len(".py")] + "_queued.py")
            for left, right in zip(inventory, inventory[1:])
            if left.startswith("scripts/") and left.endswith(".py")
            and left < left[:-len(".py")] + "_queued.py" < right
            and all(
                (source / name).is_file()
                and (package / name).read_bytes() == (source / name).read_bytes()
                for name in (left, right)
            )
        )
        for left, right in ((first, second), (first, added), (second, added)):
            with self.subTest(left=left, right=right):
                queued = self.commit_source_change(base, right)
                self.commit_source_change(base, left)
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

        with git_fixture.temporary_directory() as temporary:
            for index, (name, left, right, conflicts) in enumerate(cases):
                root = Path(temporary) / str(index)
                git_fixture.init_repository(root, initial_branch="main")

                def git(*args: str) -> subprocess.CompletedProcess:
                    return subprocess.run(
                        ["git", "-c", "user.name=Fixture", "-c", "user.email=f@example.invalid",
                         "-c", "commit.gpgsign=false", *args],
                        cwd=root, capture_output=True, text=True, check=False)

                path = root / build_distributions.PROVENANCE
                for branch, files in (("main", base), ("left", left(base)), ("right", right(base))):
                    if branch != "main":
                        git("checkout", "-q", "-b", branch, "main")
                    path.write_text(provenance(files), encoding="utf-8")
                    git("add", "--all")
                    self.assertEqual(git("commit", "-qm", branch).returncode, 0, name)
                merged = git("merge", "-q", "--no-edit", "left")
                with self.subTest(case=name):
                    self.assertEqual(bool(merged.returncode), conflicts, merged.stdout)
                    if not conflicts:
                        self.assertEqual(path.read_text(encoding="utf-8"),
                                         provenance(right(left(base))))
        documents = {
            "render_provenance": build_distributions.render_provenance.__doc__,
            **{relative: (fixtures.REAL_REPOSITORY / relative).read_text(encoding="utf-8")
               for relative in ("docs/maintainer-operations-protocol.md",
                                "docs/upgrade-protocol.md")},
            "changeset": json.loads((fixtures.REAL_REPOSITORY
                                     / ".changes/uncommitted-build-identity.json")
                                    .read_text(encoding="utf-8"))["summary"],
        }
        for where, text in documents.items():
            text = " ".join(text.split())
            with self.subTest(document=where):
                for conflict in ("adds at one sort position", "an add after a changed last entry",
                                 "a removal next to another removal or an add",
                                 "a removal of the last entry beside a change to the entry"
                                 " before it"):
                    self.assertIn(conflict, text)

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
            self.assertNotIn("reasoning:", claude)
            self.assertNotIn("reasoning:", codex)


# The reviewed default profiles: tier to model class and effort, None for no
# effort. A model bump edits only the catalog, so model IDs are read from it.
AUTO_CLASSES = {
    "claude": {
        "high": ("frontier", None), "medium": ("strong", None), "low": ("fast", None),
        "lens": ("strong", "high"), "mechanical": ("strong", "high"),
    },
    "codex": {
        "high": ("strong", "xhigh"), "medium": ("strong", "medium"), "low": ("fast", "high"),
        "lens": ("strong", "high"), "mechanical": ("fast", "high"),
    },
}
# Generated lens-tier variant: its canonical agent.
LENS_VARIANTS = {
    "backlog-reviewer-lens": "backlog-reviewer",
    "design-system-reviewer-lens": "design-system-reviewer",
    "solution-reviewer-lens": "solution-reviewer",
}
# Generated mechanical-tier writer variant: its canonical agent.
MECHANICAL_VARIANTS = {
    "devops-engineer-mechanical": "devops-engineer",
    "product-owner-mechanical": "product-owner",
    "qa-engineer-mechanical": "qa-engineer",
    "solution-architect-mechanical": "solution-architect",
}
REGISTRY = "skill-content/configure/data/process-switches.json"


def catalog_ids(root: Path, host: str) -> dict[str, str]:
    catalog = json.loads(build_distributions.model_catalog_path(root, host).read_text(
        encoding="utf-8"))
    return {name: entry["id"] for name, entry in catalog["classes"].items()}


def setting_lines(root: Path, host: str, tier: str) -> list[str]:
    """The model and effort frontmatter lines a dist agent on the tier carries."""
    if tier == "inherit":
        return ["model: inherit"] if host == "claude" else []
    name, effort = AUTO_CLASSES[host][tier]
    key = "effort" if host == "claude" else "model_reasoning_effort"
    return [f"model: {catalog_ids(root, host)[name]}"] + ([f"{key}: {effort}"] if effort else [])


def role_lines(root: Path, tier: str) -> list[str]:
    """The settings lines of a generated Codex role file on the tier."""
    return [f'{key} = "{value}"' for key, value in (
        line.split(": ", 1) for line in setting_lines(root, "codex", tier))]


def frontmatter_lines(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return lines[1:lines.index("---", 1)]


def role_settings(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("model")]


class ExecutionProfileTests(unittest.TestCase):
    """Per-host catalogs pin each class; profile tables map tiers to a class
    and effort; inherit omits both."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "marketplace"
        fixtures.make_valid_root(self.root)
        self.tables = {
            host: build_distributions.execution_profile_path(self.root, host).read_bytes()
            for host in build_distributions.HOSTS
        }
        self.catalogs = {
            host: build_distributions.model_catalog_path(self.root, host).read_bytes()
            for host in build_distributions.HOSTS
        }
        self.project = Path(self.temporary.name) / "project"
        git_fixture.init_repository(self.project, initial_branch="main")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def tiers(self) -> dict[str, str]:
        agents = self.root / "plugins" / fixtures.PLUGIN / "agents"
        return {
            path.stem: build_distributions.parse_frontmatter(path)[0]["reasoning"]
            for path in sorted(agents.glob("*.md"))
        }

    def registry(self) -> Path:
        return self.root / "plugins" / fixtures.PLUGIN / REGISTRY

    def edit_variants(self, mutate) -> None:
        data = json.loads(self.registry().read_text(encoding="utf-8"))
        mutate(data["switches"]["review_panels"]["agent_variants"]["lens_panel"], data)
        self.registry().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def rendered_tiers(self) -> dict[str, str]:
        """Every generated agent's tier: canonical agents and their variants."""
        return {**self.tiers(), **{variant: "lens" for variant in LENS_VARIANTS},
                **{variant: "mechanical" for variant in MECHANICAL_VARIANTS}}

    def edit_table(self, host: str, mutate) -> None:
        path = build_distributions.execution_profile_path(self.root, host)
        table = json.loads(self.tables[host])
        mutate(table)
        path.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")

    def set_tier(self, host: str, tier: str, setting: dict) -> None:
        self.edit_table(host, lambda table: table["profiles"]["auto"].update(
            {tier: setting}
        ))

    def edit_catalog(self, host: str, mutate) -> None:
        path = build_distributions.model_catalog_path(self.root, host)
        catalog = json.loads(self.catalogs[host])
        mutate(catalog)
        path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")

    def dist_agent(self, host: str, name: str) -> Path:
        return self.root / "dist" / host / fixtures.PLUGIN / "agents" / f"{name}.md"

    def codex(self, *args: str) -> subprocess.CompletedProcess:
        script = (
            self.root / "dist/codex" / fixtures.PLUGIN
            / "scripts/generate_codex_project.py"
        )
        return subprocess.run(
            [sys.executable, str(script), *args,
             "--project-root", str(self.project)],
            capture_output=True, text=True, check=False, timeout=120,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )

    def codex_json(self, *args: str) -> dict:
        result = self.codex(*args)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def role_files(self) -> dict[str, str]:
        return {
            path.stem: path.read_text(encoding="utf-8")
            for path in sorted((self.project / ".codex/agents").glob("*.toml"))
        }

    def test_default_tables_render_the_declared_role_settings(self):
        for host, classes in AUTO_CLASSES.items():
            self.assertEqual(json.loads(self.tables[host])["profiles"]["auto"], {
                **{tier: {"class": name, **({"effort": effort} if effort else {})}
                   for tier, (name, effort) in classes.items()},
                "inherit": {},
            }, host)
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
        self.assertEqual(challenger[:2], [
            "# Generated by Agent Marketplace software-engineering-team;"
            " do not edit by hand.",
            'name = "analysis-challenger"',
        ])
        self.assertEqual(challenger[3:6],
                         role_lines(self.root, "high") + ['sandbox_mode = "read-only"'])
        self.assertTrue(challenger[6].startswith("developer_instructions = "))

    def test_claude_frontmatter_writes_effort_only_when_the_table_sets_one(self):
        self.set_tier("claude", "high", {"class": "strong", "effort": "xhigh"})
        build_distributions.replace_generated(self.root, self.root / "dist")
        strong = catalog_ids(self.root, "claude")["strong"]
        tiers = self.rendered_tiers()
        for name, tier in tiers.items():
            with self.subTest(agent=name):
                lines = frontmatter_lines(self.dist_agent("claude", name))
                if tier == "high":
                    self.assertEqual(lines[2:4], [f"model: {strong}", "effort: xhigh"])
                    self.assertTrue(lines[4].startswith("output_contract:"))
                else:
                    self.assertEqual(
                        [line for line in lines if line.startswith(("model", "effort"))],
                        setting_lines(self.root, "claude", tier),
                    )

    def test_codex_role_files_carry_the_class_model_and_the_tier_effort(self):
        self.set_tier("codex", "high", {"class": "fast", "effort": "max"})
        build_distributions.replace_generated(self.root, self.root / "dist")
        fast = catalog_ids(self.root, "codex")["fast"]
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

    def test_a_catalog_bump_moves_every_tier_on_the_class(self):
        before = build_distributions.marketplace_snapshot(self.root)["build_id"]
        self.edit_catalog("claude", lambda catalog: catalog["classes"]["strong"].update(
            id="claude-sonnet-5"))
        self.assertNotEqual(build_distributions.marketplace_snapshot(self.root)["build_id"], before)
        self.assertTrue(any("out of sync" in problem for problem in
                            build_distributions.check(self.root, self.root / "dist")))
        build_distributions.replace_generated(self.root, self.root / "dist")
        on_class = {tier for tier, (name, _effort) in AUTO_CLASSES["claude"].items()
                    if name == "strong"}
        self.assertEqual(on_class, {"medium", "lens", "mechanical"})
        for name, tier in self.rendered_tiers().items():
            with self.subTest(agent=name):
                model = frontmatter_lines(self.dist_agent("claude", name))[2]
                if tier in on_class:
                    self.assertEqual(model, "model: claude-sonnet-5")
                else:
                    self.assertEqual(model, setting_lines(self.root, "claude", tier)[0])
                    self.assertNotEqual(model, "model: claude-sonnet-5")

    def test_single_reviewers_keep_their_tier_beside_their_lens_variants(self):
        self.set_tier("codex", "high", {"class": "fast", "effort": "max"})
        build_distributions.replace_generated(self.root, self.root / "dist")
        fast = catalog_ids(self.root, "codex")["fast"]
        self.assertEqual({name for name, tier in self.tiers().items() if tier == "lens"}, set())
        for variant, agent in sorted(LENS_VARIANTS.items()):
            with self.subTest(variant=variant):
                base = frontmatter_lines(self.dist_agent("claude", agent))
                self.assertEqual(base[2], setting_lines(self.root, "claude", "high")[0])
                self.assertFalse([line for line in base if line.startswith("effort")])
                claude = frontmatter_lines(self.dist_agent("claude", variant))
                self.assertEqual(claude[0], f"name: {variant}")
                self.assertEqual(claude[1], f"{base[1]} Lens-tier reader variant for review panels.")
                self.assertEqual(claude[2:4], setting_lines(self.root, "claude", "lens"))
                self.assertEqual(claude[4:], base[3:])
                # The variant keeps the role's body, boundaries and identity.
                body = self.dist_agent("claude", agent).read_text(encoding="utf-8").split("\n---\n", 1)[1]
                self.assertEqual(
                    self.dist_agent("claude", variant).read_text(encoding="utf-8").split("\n---\n", 1)[1],
                    body)
                codex = frontmatter_lines(self.dist_agent("codex", variant))
                self.assertEqual(
                    [line for line in codex if line.startswith(("model", "reasoning"))],
                    setting_lines(self.root, "codex", "lens"),
                )
        self.codex_json("apply", "--scope", "local")
        files = self.role_files()
        for variant, agent in sorted(LENS_VARIANTS.items()):
            with self.subTest(codex=variant):
                self.assertEqual(role_settings(files[variant]), role_lines(self.root, "lens"))
                self.assertEqual(role_settings(files[agent]),
                                 [f'model = "{fast}"', 'model_reasoning_effort = "max"'])
                self.assertIn('sandbox_mode = "read-only"', files[variant])

    def test_builder_refuses_agent_variants_it_cannot_render(self):
        source = self.root / "plugins" / fixtures.PLUGIN
        tiers = set(json.loads((self.root / "tools/data/models.json").read_text(
            encoding="utf-8"))["reasoning_levels"])
        self.assertEqual(
            [name for _agent, name, _tier, _text in build_distributions.agent_variants(source, tiers)],
            sorted(MECHANICAL_VARIANTS) + sorted(LENS_VARIANTS))
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
        before = build_distributions.marketplace_snapshot(self.root)["build_id"]
        self.set_tier("claude", "lens", {"class": "frontier", "effort": "high"})
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
        settings = {host: setting_lines(self.root, host, "mechanical")
                    for host in build_distributions.HOSTS}
        suffix = " Mechanical-tier variant for passes that apply only the fixes a review verdict names."
        tiers = self.tiers()
        for variant, agent in sorted(MECHANICAL_VARIANTS.items()):
            self.assertNotEqual(tiers[agent], "mechanical")
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
                self.assertEqual(role_settings(files[variant]), role_lines(self.root, "mechanical"))
                self.assertEqual(role_settings(files[agent]), role_lines(self.root, tiers[agent]))
                self.assertNotIn("sandbox_mode", files[variant])
                instructions = [line for line in files[variant].splitlines()
                                if line.startswith("developer_instructions")]
                self.assertEqual(instructions, [line for line in files[agent].splitlines()
                                                if line.startswith("developer_instructions")])

    def test_base_agents_and_role_files_do_not_depend_on_the_mechanical_switch(self):
        def agents() -> dict:
            return {host: {path.name: path.read_bytes() for path in sorted(
                (self.root / "dist" / host / fixtures.PLUGIN / "agents").glob("*.md"))}
                for host in build_distributions.HOSTS}

        self.codex_json("apply", "--scope", "local")
        with_switch, role_files = agents(), self.role_files()
        data = json.loads(self.registry().read_text(encoding="utf-8"))
        del data["switches"]["mechanical_pass_tier"]
        self.registry().write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        build_distributions.replace_generated(self.root, self.root / "dist")
        without = agents()
        for host in build_distributions.HOSTS:
            with self.subTest(host=host):
                self.assertEqual(set(with_switch[host]) - set(without[host]),
                                 {f"{variant}.md" for variant in MECHANICAL_VARIANTS})
                self.assertEqual({name: with_switch[host][name] for name in without[host]},
                                 without[host])
        self.codex_json("apply", "--scope", "local")
        after = self.role_files()
        self.assertEqual({name: text for name, text in role_files.items()
                          if name not in MECHANICAL_VARIANTS},
                         {name: after[name] for name in role_files if name not in MECHANICAL_VARIANTS})

    def test_the_mechanical_mapping_is_a_build_input_that_dist_check_catches(self):
        before = build_distributions.marketplace_snapshot(self.root)["build_id"]
        self.assertEqual(build_distributions.check(self.root, self.root / "dist"), [])
        self.set_tier("codex", "mechanical", {"class": "fast", "effort": "low"})
        self.assertNotEqual(build_distributions.marketplace_snapshot(self.root)["build_id"], before)
        self.assertTrue(build_distributions.check(self.root, self.root / "dist"))
        build_distributions.replace_generated(self.root, self.root / "dist")
        self.assertEqual(build_distributions.check(self.root, self.root / "dist"), [])
        self.assertIn("model_reasoning_effort: low",
                      frontmatter_lines(self.dist_agent("codex", "qa-engineer-mechanical")))
        self.assertIn("model_reasoning_effort: medium",
                      frontmatter_lines(self.dist_agent("codex", "qa-engineer")))

    def test_inherit_profile_omits_role_settings_and_survives_refresh(self):
        self.codex_json("apply", "--scope", "local")
        auto_files = self.role_files()
        self.assertTrue(all(role_settings(text) for text in auto_files.values()))

        inherited = self.codex_json(
            "apply", "--scope", "local", "--execution-profile", "inherit",
        )
        self.assertEqual(inherited["execution_profile"], "inherit")
        self.assertEqual(len(inherited["written"]), len(auto_files))
        for name, text in self.role_files().items():
            lines = text.splitlines()
            self.assertEqual(lines[1], "# Execution profile: inherit", name)
            self.assertEqual(role_settings(text), [], name)
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
        before = build_distributions.marketplace_snapshot(self.root)["build_id"]
        self.set_tier("claude", "medium", {"class": "strong", "effort": "medium"})
        self.assertNotEqual(
            build_distributions.marketplace_snapshot(self.root)["build_id"], before,
        )
        self.assertTrue(any(
            "out of sync" in problem
            for problem in build_distributions.check(self.root, self.root / "dist")
        ))

        def tier(name: str, setting: dict):
            return lambda table: table["profiles"]["auto"].update({name: setting})

        def pin(name: str, **fields):
            return lambda catalog: catalog["classes"][name].update(fields)

        profile_cases = (
            ("claude", tier("high", {"class": "ghost"}), "unknown model class 'ghost'"),
            ("claude", tier("high", {"model": "claude-opus-5-5"}),
             "may hold only non-empty string class and effort"),
            ("claude", tier("medium", {"effort": "high"}), "must name a class"),
            ("claude", tier("low", {"class": "fast", "effort": "high"}),
             "effort 'high' is not supported by .*, which takes no effort"),
            ("claude", tier("low", {"class": "fast", "effort": "extreme"}),
             "effort must be one of"),
            ("claude", tier("inherit", {"class": "strong"}), "inherit tier"),
            ("codex", tier("high", {"class": "strong", "effort": "extreme"}),
             "effort must be one of"),
            ("codex", tier("lens", {"class": "fast", "effort": "ultra"}),
             "effort 'ultra' is not supported by"),
            ("codex", tier("inherit", {"effort": "low"}), "inherit tier"),
            ("codex", lambda t: t["profiles"]["auto"]["medium"].update(temperature="1"),
             "may hold only"),
            ("codex", lambda t: t["profiles"]["auto"].pop("low"),
             "must map exactly the reasoning tiers"),
            ("claude", lambda t: t["profiles"].update(fast={}),
             "must define exactly 'auto'"),
            ("claude", lambda t: t.update(schema_version=1),
             "schema_version 2"),
        )
        catalog_cases = (
            ("claude", pin("frontier", id="opus"), "'opus' is not a pinned model ID"),
            ("claude", pin("fast", id="claude-haiku-4-5"),
             "'claude-haiku-4-5' is not a pinned model ID"),
            ("claude", pin("strong", id="claude-sonnet-5-5-20260901"),
             "is not a pinned model ID"),
            ("claude", pin("frontier", id="gpt-6.1-sol"), "is not a pinned model ID"),
            ("claude", pin("frontier", family="sonnet"),
             "belongs to family 'opus', not 'sonnet'"),
            ("codex", pin("strong", id="gpt-5.5"), "'gpt-5.5' is not a pinned model ID"),
            ("codex", pin("strong", id="gpt 6.1 sol"), "is not a pinned model ID"),
            ("codex", lambda c: c["classes"]["fast"].update(
                id=c["classes"]["strong"]["id"], family=c["classes"]["strong"]["family"]),
             "one class per model"),
            ("codex", pin("strong", efforts=["turbo"]), "efforts must list distinct values"),
            ("codex", pin("strong", efforts=["high", "high"]),
             "efforts must list distinct values"),
            ("claude", pin("strong", sources=[]), "sources must list"),
            ("claude", pin("strong", sources=["http://example.com"]), "sources must list"),
            ("claude", pin("strong", verified="01.10.2026"), "verified must be"),
            ("claude", pin("strong", verified="2026-02-30"), "verified must be"),
            ("claude", pin("fast", min_cli_version="2.1"), "min_cli_version must be"),
            ("codex", pin("fast", min_cli_version="v0.157.0"), "min_cli_version must be"),
            ("codex", lambda c: c["classes"]["strong"].pop("min_cli_version"),
             "must hold exactly"),
            # CI installs the pinned CLI and starts no role, so the build refuses
            # a pin below the oldest CLI that runs a class's model.
            ("codex", pin("strong", min_cli_version="9.0.0"),
             "is below 9.0.0, the minimum of codex class 'strong'"),
            ("claude", pin("frontier", min_cli_version="3.0.0"),
             "is below 3.0.0, the minimum of claude class 'frontier'"),
            ("codex", lambda c: c["classes"]["strong"].pop("verified"), "must hold exactly"),
            ("codex", lambda c: c["classes"].update(Strong=c["classes"].pop("strong")),
             "class names are snake_case"),
            ("codex", lambda c: c.update(schema_version=2), "schema_version 1 and classes"),
            ("claude", lambda c: c.update(classes={}), "at least one class"),
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
                    build_distributions.build(self.root, Path(self.temporary.name) / "missing")
        pins.write_bytes(pinned)
        for key, version in (("claude_code", "2.1.283"), ("codex", "0.157.0")):
            with self.subTest(pin=key):
                restore()
                pins.write_text(json.dumps({**json.loads(pinned), key: version}), encoding="utf-8")
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
        summary = json.loads((repository / ".changes/pinned-role-models.json").read_text(
            encoding="utf-8"))["summary"]
        self.assertIn(exception, summary)
        self.assertIn("the Codex high tier runs at `xhigh` instead of `high`", summary)

    def test_host_contracts_state_the_oldest_cli_the_pinned_models_need(self):
        adapters = build_distributions.load_adapters(self.root)
        for host, adapter in adapters.items():
            catalog = json.loads(self.catalogs[host])
            minimums = [entry["min_cli_version"] for entry in catalog["classes"].values()]
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
