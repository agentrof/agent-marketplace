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


CLAUDE_AUTO_MODELS = {
    "high": "opus", "medium": "sonnet", "low": "haiku", "inherit": "inherit",
}


def frontmatter_lines(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return lines[1:lines.index("---", 1)]


def role_settings(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("model")]


class ExecutionProfileTests(unittest.TestCase):
    """Per-host tier tables drive role model and effort; inherit omits both."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "marketplace"
        fixtures.make_valid_root(self.root)
        self.tables = {
            host: build_distributions.execution_profile_path(self.root, host).read_bytes()
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

    def edit_table(self, host: str, mutate) -> None:
        path = build_distributions.execution_profile_path(self.root, host)
        table = json.loads(self.tables[host])
        mutate(table)
        path.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")

    def set_tier(self, host: str, tier: str, setting: dict) -> None:
        self.edit_table(host, lambda table: table["profiles"]["auto"].update(
            {tier: setting}
        ))

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

    def test_default_tables_reproduce_the_pre_table_role_settings(self):
        tiers = self.tiers()
        for name, tier in tiers.items():
            with self.subTest(agent=name):
                claude = frontmatter_lines(self.dist_agent("claude", name))
                self.assertEqual(claude[2], f"model: {CLAUDE_AUTO_MODELS[tier]}")
                self.assertFalse([line for line in claude if line.startswith("effort")])
                codex = frontmatter_lines(self.dist_agent("codex", name))
                self.assertEqual(
                    [line for line in codex if line.startswith(("model", "reasoning"))],
                    [] if tier == "inherit" else [f"model_reasoning_effort: {tier}"],
                )
        generated = self.codex_json("apply", "--scope", "local")
        self.assertEqual(generated["execution_profile"], "auto")
        files = self.role_files()
        self.assertEqual(set(files), set(tiers))
        for name, text in files.items():
            self.assertEqual(
                role_settings(text),
                [] if tiers[name] == "inherit"
                else [f'model_reasoning_effort = "{tiers[name]}"'],
                name,
            )
        challenger = files["analysis-challenger"].splitlines()
        self.assertEqual(challenger[:2], [
            "# Generated by Agent Marketplace software-engineering-team;"
            " do not edit by hand.",
            'name = "analysis-challenger"',
        ])
        self.assertEqual(challenger[3:5], [
            'model_reasoning_effort = "high"', 'sandbox_mode = "read-only"',
        ])
        self.assertTrue(challenger[5].startswith("developer_instructions = "))

    def test_claude_frontmatter_writes_effort_only_when_the_table_sets_one(self):
        self.set_tier("claude", "high", {"model": "sonnet", "effort": "xhigh"})
        build_distributions.replace_generated(self.root, self.root / "dist")
        tiers = self.tiers()
        for name, tier in tiers.items():
            with self.subTest(agent=name):
                lines = frontmatter_lines(self.dist_agent("claude", name))
                if tier == "high":
                    self.assertEqual(lines[2:4], ["model: sonnet", "effort: xhigh"])
                    self.assertTrue(lines[4].startswith("output_contract:"))
                else:
                    self.assertEqual(lines[2], f"model: {CLAUDE_AUTO_MODELS[tier]}")
                    self.assertFalse([line for line in lines if line.startswith("effort")])

    def test_codex_role_files_carry_a_model_only_when_the_table_sets_one(self):
        self.set_tier("codex", "high", {"model": "gpt-test-1.5", "effort": "max"})
        build_distributions.replace_generated(self.root, self.root / "dist")
        self.assertEqual(
            frontmatter_lines(self.dist_agent("codex", "analysis-challenger"))[2:4],
            ["model: gpt-test-1.5", "model_reasoning_effort: max"],
        )
        self.codex_json("apply", "--scope", "local")
        expected = {
            "high": ['model = "gpt-test-1.5"', 'model_reasoning_effort = "max"'],
            "medium": ['model_reasoning_effort = "medium"'],
            "low": ['model_reasoning_effort = "low"'],
            "inherit": [],
        }
        tiers = self.tiers()
        for name, text in self.role_files().items():
            self.assertEqual(role_settings(text), expected[tiers[name]], name)

    def test_inherit_profile_omits_role_settings_and_survives_refresh(self):
        self.set_tier("codex", "high", {"model": "gpt-test-1.5", "effort": "max"})
        build_distributions.replace_generated(self.root, self.root / "dist")
        self.codex_json("apply", "--scope", "local")
        auto_files = self.role_files()

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
            "model_reasoning_effort: high", "reasoning: high",
        ), encoding="utf-8")
        stale = self.codex("check", "--scope", "local")
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("unresolved reasoning tier", stale.stderr)

    def test_tables_are_build_inputs_and_invalid_tables_fail_the_build(self):
        before = build_distributions.marketplace_snapshot(self.root)["build_id"]
        self.set_tier("claude", "medium", {"model": "sonnet", "effort": "medium"})
        self.assertNotEqual(
            build_distributions.marketplace_snapshot(self.root)["build_id"], before,
        )
        self.assertTrue(any(
            "out of sync" in problem
            for problem in build_distributions.check(self.root, self.root / "dist")
        ))

        cases = (
            ("claude", lambda t: t["profiles"]["auto"]["high"].update(model="gpt-5"),
             "model must be one of"),
            ("claude", lambda t: t["profiles"]["auto"]["low"].update(effort="extreme"),
             "effort must be one of"),
            ("claude", lambda t: t["profiles"]["auto"]["inherit"].update(model="opus"),
             "inherit tier"),
            ("codex", lambda t: t["profiles"]["auto"]["high"].update(effort="extreme"),
             "effort must be one of"),
            ("codex", lambda t: t["profiles"]["auto"]["high"].update(model="two words"),
             "model must be one model name"),
            ("codex", lambda t: t["profiles"]["auto"]["inherit"].update(effort="low"),
             "inherit tier"),
            ("codex", lambda t: t["profiles"]["auto"]["medium"].update(temperature="1"),
             "may hold only"),
            ("codex", lambda t: t["profiles"]["auto"].pop("low"),
             "must map exactly the reasoning tiers"),
            ("claude", lambda t: t["profiles"].update(fast={}),
             "must define exactly 'auto'"),
            ("claude", lambda t: t.update(schema_version=2),
             "schema_version 1"),
        )
        def restore_tables() -> None:
            for name, data in self.tables.items():
                build_distributions.execution_profile_path(
                    self.root, name
                ).write_bytes(data)

        for index, (host, mutate, message) in enumerate(cases):
            with self.subTest(host=host, message=message):
                restore_tables()
                self.edit_table(host, mutate)
                with self.assertRaisesRegex(ValueError, message):
                    build_distributions.build(
                        self.root, Path(self.temporary.name) / f"out-{index}",
                    )
                self.assertFalse((Path(self.temporary.name) / f"out-{index}").exists())

        restore_tables()
        build_distributions.execution_profile_path(self.root, "codex").unlink()
        with self.assertRaisesRegex(ValueError, "missing or invalid execution profile"):
            build_distributions.build(self.root, Path(self.temporary.name) / "missing")


if __name__ == "__main__":
    unittest.main()
