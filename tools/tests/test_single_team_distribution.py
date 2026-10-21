"""Standalone-team and cross-host distribution contracts."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration
from pathlib import Path


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
            "scripts/task_lane.py",
            "scripts/project_config.py",
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

    def test_snapshot_paths_use_case_sensitive_posix_order(self):
        paths = build_distributions.snapshot_files(self.root, "plugins")
        relative = [path.relative_to(self.root).as_posix() for path in paths]
        self.assertEqual(relative, sorted(relative))
        self.assertNotEqual(relative, sorted(relative, key=str.casefold))

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
        # Committed packages of changes to different files merge into the
        # rebuild of the merged sources, without rebuilding dist/.
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

    def test_binary_payload_survives_autocrlf_checkout_and_build(self):
        relative = Path(
            "skill-content/deliver/data/binary-contract.pdf"
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
        relative = "scripts/task_lane.py"
        root = copy_root(shared_root(), self.base / "marketplace", with_dist=False)
        source = root / "plugins" / fixtures.PLUGIN / relative
        before = build_distributions.marketplace_snapshot(root)["build_id"]
        baseline = json.loads((
            shared_root() / "dist" / "claude" / fixtures.PLUGIN
            / build_distributions.PROVENANCE
        ).read_text(encoding="utf-8"))
        self.assertNotIn(relative, baseline["executables"])

        source.chmod(source.stat().st_mode | stat.S_IXUSR)
        checkout_mode_only = build_distributions.marketplace_snapshot(
            root
        )["build_id"]
        self.assertEqual(before, checkout_mode_only)
        mode_contract = root / "package-modes.json"
        mode_contract.write_bytes((json.dumps({
            "schema_version": 1,
            "packages": {fixtures.PLUGIN: {"executables": [relative]}},
        }, indent=2) + "\n").encode("utf-8"))
        after = build_distributions.marketplace_snapshot(root)["build_id"]
        self.assertNotEqual(before, after)
        # A package's provenance lists exactly the executables this contract loads.
        changed = build_distributions.load_package_executables(root, fixtures.PLUGIN)
        self.assertIn(relative, changed)

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

def shipped_models(host: str) -> dict:
    """The models a host's shipped catalog pins, by model ID."""
    return json.loads(build_distributions.model_catalog_path(
        fixtures.REAL_REPOSITORY, host).read_text(encoding="utf-8"))["models"]


def shipped_profile(host: str) -> dict:
    """A host's shipped default profile: class slot to model ID and effort, None for no effort.
    By the owner's decision of 1 Oct 2026 on #349 a profile names its model directly."""
    auto = json.loads(build_distributions.execution_profile_path(
        fixtures.REAL_REPOSITORY, host).read_text(encoding="utf-8"))["profiles"]["auto"]
    return {slot: (setting["model"], setting.get("effort")) for slot, setting in auto.items()}


# Read from the shipped tables, so a model bump changes no test.
AUTO_MODELS = {host: shipped_profile(host) for host in ("claude", "codex")}


def catalog_model(host: str, *, besides: tuple = (), effort: str | None = None,
                  unused: bool = False, effortless: bool = False) -> str:
    """The first shipped catalog model of ``host`` with the traits a case needs."""
    used = {model for model, _effort in AUTO_MODELS[host].values()}
    for model, entry in sorted(shipped_models(host).items()):
        if model in besides or (unused and model in used) or (effortless and entry["efforts"]) \
                or (effort is not None and effort not in entry["efforts"]):
            continue
        return model
    raise AssertionError(f"the {host} catalog pins no model this case needs")
# The owner's decisions on #349 of 1 Oct 2026: no role defaults to these efforts.
UNPINNED_EFFORTS = {"max", "ultra"}
DECLARED_CLASSES = json.loads((fixtures.REAL_REPOSITORY / "tools/data/models.json").read_text(
    encoding="utf-8"))["model_classes"]


def setting_lines(root: Path, host: str, slot: str) -> list[str]:
    """The model and effort frontmatter lines a dist agent on the slot carries."""
    model, effort = AUTO_MODELS[host][slot]
    key = "effort" if host == "claude" else "model_reasoning_effort"
    return [f"model: {model}"] + ([f"{key}: {effort}"] if effort else [])


def role_lines(root: Path, slot: str) -> list[str]:
    """The settings lines of a generated Codex role file on the slot."""
    return [f'{key} = "{value}"' for key, value in (
        line.split(": ", 1) for line in setting_lines(root, "codex", slot))]


def frontmatter_lines(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return lines[1:lines.index("---", 1)]


def role_settings(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("model")]


def codex_fast_model() -> str:
    """The Codex catalog model critical_decision moves to in the codex_decision_fast build."""
    return catalog_model("codex", besides=(AUTO_MODELS["codex"]["critical_decision"][0],),
                         effort="max")


def edit_json(path: Path, mutate) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# Shared builds of one source change each, read by several tests.
SHARED_VARIANTS = {
    "codex_decision_fast": lambda root: edit_json(
        build_distributions.execution_profile_path(root, "codex"),
        lambda table: table["profiles"]["auto"].update(
            critical_decision={"model": codex_fast_model(), "effort": "max"})),
}


@integration
class ExecutionProfileTests(unittest.TestCase):
    """Per-host catalogs pin each model; profile tables map each class slot to
    a model and effort; inherit omits the model and keeps the effort."""

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
            self.root, self.root / "plugins" / fixtures.PLUGIN, target, adapter,
            build_distributions.load_execution_profile(self.root, adapter))
        return target / "agents"

    def load_tables(self) -> None:
        """Load and check every table a build reads before it writes its output."""
        adapters = build_distributions.load_adapters(self.root)
        for adapter in adapters.values():
            build_distributions.load_execution_profile(self.root, adapter)
        build_distributions.require_host_cli_versions(self.root, adapters)

    def classes(self) -> dict[str, str]:
        agents = self.root / "plugins" / fixtures.PLUGIN / "agents"
        return {
            path.stem: build_distributions.parse_frontmatter(path)[0]["model_class"]
            for path in sorted(agents.glob("*.md"))
        }

    def assert_own_root(self) -> None:
        self.assertNotIn(self.root, _SHARED_ROOTS.values(), "change a copy, not a shared build")

    def rendered_classes(self, host: str = "claude") -> dict[str, str]:
        """Every agent ``host`` renders, with its class slot: the canonical agents."""
        return self.classes()

    def edit_table(self, host: str, mutate) -> None:
        self.assert_own_root()
        path = build_distributions.execution_profile_path(self.root, host)
        table = json.loads(self.tables[host])
        mutate(table)
        path.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")

    def set_slot(self, host: str, slot: str, setting: dict) -> None:
        self.edit_table(host, lambda table: table["profiles"]["auto"].update(
            {slot: setting}
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
            rendered = self.rendered_classes(host)
            self.assertEqual({path.stem for path in self.dist_agent(host, "x").parent.glob("*.md")},
                             set(rendered), host)
            for name, slot in rendered.items():
                with self.subTest(host=host, agent=name):
                    lines = frontmatter_lines(self.dist_agent(host, name))
                    self.assertEqual(lines[2], setting_lines(self.root, host, slot)[0])
                    self.assertEqual(
                        [line for line in lines if line.startswith(("model", "effort"))],
                        setting_lines(self.root, host, slot),
                    )
        classes = self.rendered_classes("codex")
        generated = self.codex_json("apply", "--scope", "local")
        self.assertEqual(generated["execution_profile"], "auto")
        files = self.role_files()
        self.assertEqual(set(files), set(classes))
        for name, text in files.items():
            self.assertEqual(role_settings(text), role_lines(self.root, classes[name]), name)
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
                         role_lines(self.root, classes["analysis-challenger"])
                         + ['sandbox_mode = "read-only"'])
        self.assertTrue(challenger[7].startswith("developer_instructions = "))

    def test_claude_frontmatter_writes_effort_only_when_the_table_sets_one(self):
        self.use_copy()
        strong = catalog_model("claude", besides=(AUTO_MODELS["claude"]["critical_decision"][0],),
                               effort="xhigh")
        self.set_slot("claude", "critical_decision", {"model": strong, "effort": "xhigh"})
        agents = self.render_agents("claude")
        for name, slot in self.rendered_classes().items():
            with self.subTest(agent=name):
                lines = frontmatter_lines(agents / f"{name}.md")
                if slot == "critical_decision":
                    self.assertEqual(lines[2:4], [f"model: {strong}", "effort: xhigh"])
                    self.assertTrue(lines[4].startswith("output_contract:"))
                else:
                    self.assertEqual(
                        [line for line in lines if line.startswith(("model", "effort"))],
                        setting_lines(self.root, "claude", slot),
                    )

    def test_codex_role_files_carry_the_class_model_and_effort(self):
        self.root = shared_root("codex_decision_fast")
        fast = codex_fast_model()
        self.assertEqual(
            frontmatter_lines(self.dist_agent("codex", "software-architect"))[2:4],
            [f"model: {fast}", "model_reasoning_effort: max"],
        )
        self.codex_json("apply", "--scope", "local")
        classes = self.rendered_classes("codex")
        for name, text in self.role_files().items():
            self.assertEqual(
                role_settings(text),
                [f'model = "{fast}"', 'model_reasoning_effort = "max"']
                if classes[name] == "critical_decision" else role_lines(self.root, classes[name]),
                name,
            )

    def test_a_model_bump_moves_every_slot_that_names_the_model(self):
        # A bump renames the catalog entry and every slot that names it.
        self.use_copy()
        committed = shared_root() / "dist" / "claude" / fixtures.PLUGIN / "agents"
        self.assertEqual(build_distributions.compare_dirs(committed, self.render_agents("claude")),
                         [])
        pinned = AUTO_MODELS["claude"]["routine_authoring"][0]
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
        on_bumped = {slot for slot, (model, _effort) in AUTO_MODELS["claude"].items()
                     if model == pinned}
        for name, slot in self.rendered_classes().items():
            with self.subTest(agent=name):
                model = frontmatter_lines(agents / f"{name}.md")[2]
                if slot in on_bumped:
                    self.assertEqual(model, f"model: {bumped}")
                else:
                    self.assertEqual(model, setting_lines(self.root, "claude", slot)[0])
                    self.assertNotEqual(model, f"model: {bumped}")

    def test_inherit_profile_omits_the_model_keeps_the_effort_and_survives_refresh(self):
        self.codex_json("apply", "--scope", "local")
        auto_files = self.role_files()
        self.assertTrue(all(role_settings(text) for text in auto_files.values()))

        inherited = self.codex_json(
            "apply", "--scope", "local", "--execution-profile", "inherit",
        )
        self.assertEqual(inherited["execution_profile"], "inherit")
        self.assertEqual(len(inherited["written"]), len(auto_files))
        classes = self.rendered_classes("codex")
        for name, text in self.role_files().items():
            lines = text.splitlines()
            self.assertEqual(lines[1], "# Execution profile: inherit", name)
            # Without its effort a role would run the parent's, `low` by
            # default for Sol in Codex 0.159.
            self.assertEqual(role_settings(text), role_lines(self.root, classes[name])[1:], name)
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
        slot = self.classes()["analysis-challenger"]
        agent.write_text(agent.read_text(encoding="utf-8").replace(
            setting_lines(self.root, "codex", slot)[1], f"model_class: {slot}",
        ), encoding="utf-8")
        stale = self.codex("check", "--scope", "local")
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn("unresolved model class", stale.stderr)

    def test_unreadable_role_target_reports_a_collision_not_a_traceback(self):
        (self.project / ".codex/agents/analysis-challenger.toml").mkdir(parents=True)
        result = self.codex("check", "--scope", "local")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unmanaged Codex agent collision", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_tables_are_build_inputs_and_invalid_tables_fail_the_build(self):
        self.use_copy()
        before = build_distributions.marketplace_snapshot(self.root)["build_id"]
        self.set_slot("claude", "routine_authoring", {
            "model": catalog_model("claude", besides=(AUTO_MODELS["claude"]["routine_authoring"][0],),
                                   effort="medium"), "effort": "medium"})
        self.assertNotEqual(
            build_distributions.marketplace_snapshot(self.root)["build_id"], before,
        )
        # The shared build's dist/ is what this copy's unchanged sources build.
        self.assertTrue(any(
            "out of sync" in problem
            for problem in build_distributions.check(self.root, shared_root() / "dist")
        ))

        def slot(name: str, setting: dict):
            return lambda table: table["profiles"]["auto"].update({name: setting})

        def pin(model: str, **fields):
            return lambda catalog: catalog["models"][model].update(fields)

        def rename(model: str, to: str):
            """A catalog whose entry for ``model`` is keyed ``to``."""
            return lambda catalog: catalog.update(models={
                (to if name == model else name): entry
                for name, entry in catalog["models"].items()})

        # Models of the shipped catalogs with the traits each case needs.
        claude_pinned = AUTO_MODELS["claude"]["critical_decision"][0]
        effortless = catalog_model("claude", unused=True, effortless=True)
        codex_pinned = AUTO_MODELS["codex"]["critical_decision"][0]
        family = shipped_models("claude")[claude_pinned]["family"]
        other_family = next(entry["family"] for _model, entry in sorted(shipped_models("claude").items())
                            if entry["family"] != family)
        profile_cases = (
            ("claude", slot("critical_decision", {"model": "claude-ghost-9"}),
             "unknown model 'claude-ghost-9'; the catalog pins"),
            ("claude", slot("critical_decision", {"class": "frontier"}),
             "may hold only non-empty string model and effort"),
            ("claude", slot("routine_authoring", {"effort": "high"}), "must name a model"),
            ("claude", slot("mechanical", {"model": effortless, "effort": "high"}),
             "effort 'high' is not supported by .*, which takes no effort"),
            ("codex", slot("critical_decision", {"model": codex_pinned, "effort": "extreme"}),
             "effort must be one of"),
            ("codex", lambda t: t["profiles"]["auto"].pop("mechanical"),
             "must map exactly the model class slots"),
            ("claude", lambda t: t["profiles"].update(fast={}),
             "must define exactly 'auto'"),
            ("claude", lambda t: t.update(schema_version=3),
             "schema_version 4"),
        )
        catalog_cases = (
            ("claude", rename(effortless, "opus"), "'opus' is not a pinned model ID"),
            ("claude", pin(claude_pinned, family=other_family),
             f"belongs to family '{family}', not '{other_family}'"),
            ("codex", pin(codex_pinned, efforts=["turbo"]), "efforts must list distinct values"),
            ("claude", pin(claude_pinned, sources=[]), "sources must list"),
            ("claude", pin(claude_pinned, verified="01.10.2026"), "verified must be"),
            ("claude", pin(effortless, min_cli_version="2.1"), "min_cli_version must be"),
            ("codex", lambda c: c["models"][codex_pinned].pop("min_cli_version"), "must hold exactly"),
            # CI installs the pinned CLI and starts no role, so the build refuses
            # a pin below the oldest CLI that runs a catalog model.
            ("codex", pin(codex_pinned, min_cli_version="9.0.0"),
             f"is below 9.0.0, the minimum of codex model '{codex_pinned}'"),
            ("codex", lambda c: c.update(schema_version=1), "schema_version 2 and models"),
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
                 "missing or invalid execution profile"),):
            with self.subTest(missing=path.name):
                restore()
                pins.write_bytes(pinned)
                path.unlink()
                with self.assertRaisesRegex(ValueError, message):
                    self.load_tables()
        pins.write_bytes(pinned)
        # One patch below each host's highest catalog minimum.
        below = {}
        for host, key in (("claude", "claude_code"),):
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

    def test_every_class_pins_the_owners_effort_and_the_docs_say_why(self):
        # A Claude Code role without `effort` follows the session's level,
        # `max` included, so every slot whose model takes an effort pins one.
        # By the owner's decisions of 1 Oct 2026 on #349 none defaults to max
        # or ultra; by the owner's decision of 16 Oct 2026 every purpose class
        # and escalated slot is mapped.
        repository = fixtures.REAL_REPOSITORY
        slots = set(build_distributions.class_slots(DECLARED_CLASSES))
        for host in build_distributions.HOSTS:
            models = json.loads(build_distributions.model_catalog_path(
                repository, host).read_text(encoding="utf-8"))["models"]
            auto = json.loads(build_distributions.execution_profile_path(
                repository, host).read_text(encoding="utf-8"))["profiles"]["auto"]
            self.assertEqual(set(auto), slots, host)
            for slot, setting in auto.items():
                with self.subTest(host=host, slot=slot):
                    self.assertEqual("effort" in setting, bool(models[setting["model"]]["efforts"]))
                    self.assertNotIn(setting.get("effort"), UNPINNED_EFFORTS)

    def test_each_adapter_accepts_only_its_documented_pinned_ids(self):
        adapters = build_distributions.load_adapters(self.root)
        cases = {
            "claude": {
                "claude-opus-5-5": ("opus", (5, 5, 0)),
                "claude-haiku-4-5-20251001": ("haiku", (4, 5, 20251001)),
                "claude-opus-5-5[1m]": None,
            },
            "codex": {
                "gpt-6.1-sol": ("sol", (6, 1)),
                "gpt-5.5": None,
            },
        }
        for host, expected in cases.items():
            for model_id, version in expected.items():
                with self.subTest(host=host, model_id=model_id):
                    self.assertEqual(adapters[host].module.model_version(model_id), version)


if __name__ == "__main__":
    unittest.main()
