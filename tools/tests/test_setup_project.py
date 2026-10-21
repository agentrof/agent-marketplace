from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import types
import unittest
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "plugins" / "software-engineering-team" / "scripts" / "setup_project.py"
CHECK = ROOT / "plugins" / "software-engineering-team" / "scripts" / "setup_check.py"
SCRIPTS = SETUP.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import setup_project as setup_module
import setup_check as check_module
from tools.tests import fixture_cache
from tools.tests.git_fixture import init_repository, temporary_directory
from unittest import mock

ATTRIBUTES_BLOCK = (
    "# agent-marketplace:software-engineering-team:gitattributes:start\n"
    "workspace/docs/** -text\n"
    "# agent-marketplace:software-engineering-team:gitattributes:end\n"
)


_APPLIED = fixture_cache.AppliedProjectCache()


class SetupProjectTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls) -> None:
        _APPLIED.close()

    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    @contextlib.contextmanager
    def applied_project(self):
        """A project right after one setup apply, copied from this process's applied seed."""
        with temporary_directory() as temporary:
            _APPLIED.apply_to(Path(temporary), _APPLIED_CONTEXT)
            yield Path(temporary)

    @integration
    def test_preflight_allows_an_unconfigured_git_project(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            init_repository(project)
            result = self.run_script(
                CHECK, "preflight", "--project-root", str(project), "--json"
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(json.loads(result.stdout)["ok"])

    @integration
    def test_setup_then_refresh_converges_and_preserves_project_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            init_repository(project)
            first = self.run_script(
                SETUP, "apply", "--project-root", str(project), "--json",
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            payload = json.loads(first.stdout)
            self.assertEqual(payload["next_entry"], "deliver")
            runtime = project / ".agentrof" / "agent-marketplace" / ".runtime"
            self.assertEqual(Path(payload["runtime_root"]), runtime.resolve())
            self.assertEqual({path.name for path in runtime.iterdir()}, {"setup-apply.guard"})
            ignored = subprocess.run(
                ["git", "check-ignore", "--no-index", "-q",
                 ".agentrof/agent-marketplace/.runtime/probe"],
                cwd=project, check=False,
            )
            self.assertEqual(ignored.returncode, 0)
            tracked = subprocess.run(
                ["git", "check-ignore", "--no-index", "-q", ".agentrof/local-proof.json"],
                cwd=project, check=False,
            )
            self.assertEqual(tracked.returncode, 1)
            runtime_sentinel = runtime / "cache.txt"
            runtime_sentinel.write_text("disposable\n", encoding="utf-8")
            workspace = project / "workspace"
            for relative in ("tests", "project", "memory", "docs/backlog/tasks",
                             "docs/backlog/retrospectives", "docs/operation"):
                self.assertTrue((workspace / relative).is_dir(), relative)
            self.assertTrue((workspace / "experience-design/DESIGN.md").is_file())

            config_path = project / "workspace/config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["custom_project_field"] = {"owner": "consumer"}
            config_path.write_text(
                json.dumps(config, indent=2) + "\n", encoding="utf-8"
            )
            authored = project / "workspace/docs/user-notes/upgrade-sentinel.md"
            authored.parent.mkdir(parents=True)
            authored.write_text("# Consumer authored\n", encoding="utf-8")
            authored_before = authored.read_bytes()

            inspected = self.run_script(
                SETUP, "inspect", "--project-root", str(project), "--json"
            )
            self.assertEqual(
                inspected.returncode, 0, inspected.stdout + inspected.stderr
            )
            plan = json.loads(inspected.stdout)
            planned = {item["path"] for item in plan["operations"]}
            self.assertIn("workspace/config.json", planned)
            self.assertEqual(authored.read_bytes(), authored_before)
            rejected = self.run_script(
                SETUP, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("managed refresh drift", rejected.stdout)

            applied = self.run_script(
                SETUP, "apply", "--project-root", str(project), "--json"
            )
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            payload = json.loads(applied.stdout)
            self.assertEqual(payload["next_entry"], "deliver")
            self.assertEqual(authored.read_bytes(), authored_before)
            self.assertEqual(runtime_sentinel.read_text(encoding="utf-8"), "disposable\n")
            refreshed_config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertNotIn("custom_project_field", refreshed_config)
            for script in (SETUP, CHECK):
                checked = self.run_script(
                    script, "check", "--project-root", str(project), "--json"
                )
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            second = self.run_script(
                SETUP, "inspect", "--project-root", str(project), "--json"
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(json.loads(second.stdout)["operations"], [])

    @integration
    def test_setup_refuses_symlink_anywhere_in_experience_subtree(self):
        with self.applied_project() as project:
            external = project / "external-experience"
            external.mkdir()
            (external / "sentinel.md").write_text(
                "# Outside\n", encoding="utf-8"
            )
            link = (
                project / "workspace/docs/experience-design/experiences/linked"
            )
            link.parent.mkdir(parents=True, exist_ok=True)
            try:
                link.symlink_to(external, target_is_directory=True)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symlinks are unavailable: {exc}")

            inspected = self.run_script(
                SETUP, "inspect", "--project-root", str(project), "--json"
            )
            self.assertEqual(inspected.returncode, 1, inspected.stdout)
            blockers = json.loads(inspected.stdout)["blockers"]
            self.assertTrue(any(
                "Experience subtree symlink" in blocker
                and "experience-design/experiences/linked" in blocker
                for blocker in blockers
            ))
            checked = self.run_script(
                CHECK, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(checked.returncode, 1, checked.stdout)
            self.assertIn("Experience subtree symlink", checked.stdout)
            self.assertTrue((external / "sentinel.md").is_file())

    @integration
    def test_setup_and_check_refuse_hardlinks_in_experience_subtree(self):
        with self.applied_project() as project:
            ledger = (
                project / "workspace/docs/experience-design/_ledger/"
                "application-revisions.json"
            )
            ledger.parent.mkdir(parents=True, exist_ok=True)
            ledger.write_text(
                '{"schema_version":2,"revisions":[]}\n', encoding="utf-8",
            )
            alias = project / "application-ledger-alias.json"
            try:
                os.link(ledger, alias)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"hard links are unavailable: {exc}")

            inspected = self.run_script(
                SETUP, "inspect", "--project-root", str(project), "--json"
            )
            self.assertEqual(inspected.returncode, 1, inspected.stdout)
            blockers = json.loads(inspected.stdout)["blockers"]
            self.assertTrue(any(
                "hard-link alias" in blocker
                and "application-revisions.json" in blocker
                for blocker in blockers
            ))
            checked = self.run_script(
                CHECK, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(checked.returncode, 1, checked.stdout)
            self.assertIn("hard-link alias", checked.stdout)

    @integration
    def test_rollback_preserves_concurrent_authored_markdown(self):
        with self.applied_project() as project:
            config_path = (project / "workspace/config.json").resolve()
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["custom_project_field"] = "pre-refresh-drift"
            config_path.write_text(
                json.dumps(config, indent=2) + "\n", encoding="utf-8"
            )
            config_before = config_path.read_bytes()
            authored = project / "workspace/docs/project-notes/concurrent.md"

            args = argparse.Namespace(
                project_root=str(project), workspace="workspace",
                scale="small", output_language="English",
                terminology_language="English", command="apply", json=True,
            )
            plan = setup_module.build_plan(args)

            def closing_failure(_root: Path, _workspace: str) -> list[str]:
                authored.parent.mkdir(parents=True, exist_ok=True)
                authored.write_text(
                    "# Concurrent user-authored note\n", encoding="utf-8"
                )
                return ["forced closing failure"]

            with mock.patch.object(
                setup_module.setup_check, "closing", side_effect=closing_failure
            ):
                code, result = setup_module.apply_plan(args, plan)
            self.assertEqual(code, 1)
            self.assertTrue(result["rolled_back"])
            self.assertEqual(result["rollback_conflicts"], [])
            self.assertEqual(config_path.read_bytes(), config_before)
            self.assertEqual(
                authored.read_text(encoding="utf-8"),
                "# Concurrent user-authored note\n",
            )

    @integration
    def test_rollback_preserves_concurrent_edit_to_unchanged_managed_note(self):
        with self.applied_project() as project:
            config_path = (project / "workspace/config.json").resolve()
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["custom_project_field"] = "pre-refresh-drift"
            config_path.write_text(
                json.dumps(config, indent=2) + "\n", encoding="utf-8"
            )
            config_before = config_path.read_bytes()
            home = project / "workspace/docs/home.md"
            home.parent.mkdir(parents=True, exist_ok=True)
            home.write_text("# Home\n", encoding="utf-8")
            concurrent_home = "# Home\n\nConcurrent project note.\n"
            args = argparse.Namespace(
                project_root=str(project), workspace="workspace", origin=None,
                scale="small", output_language="English",
                terminology_language="English", command="apply", json=True,
            )
            plan = setup_module.build_plan(args)
            original_write = setup_module.RefreshSnapshot.write_text
            edited = False

            def write_then_edit(snapshot, path, content):
                nonlocal edited
                original_write(snapshot, path, content)
                if not edited:
                    home.write_text(concurrent_home, encoding="utf-8")
                    edited = True

            with mock.patch.object(
                setup_module.RefreshSnapshot, "write_text",
                new=write_then_edit,
            ), mock.patch.object(
                setup_module.setup_check, "closing",
                return_value=["forced closing failure"],
            ):
                code, result = setup_module.apply_plan(args, plan)
            self.assertEqual(code, 1)
            self.assertTrue(result["rolled_back"])
            self.assertEqual(result["rollback_conflicts"], [])
            self.assertEqual(home.read_text(encoding="utf-8"), concurrent_home)
            self.assertEqual(config_path.read_bytes(), config_before)

    @integration
    def test_rollback_reports_concurrent_edit_to_written_target(self):
        with self.applied_project() as project:
            config_path = (project / "workspace/config.json").resolve()
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["custom_project_field"] = "pre-refresh-drift"
            config_path.write_text(
                json.dumps(config, indent=2) + "\n", encoding="utf-8"
            )
            args = argparse.Namespace(
                project_root=str(project), workspace="workspace", origin=None,
                scale="small", output_language="English",
                terminology_language="English", command="apply", json=True,
            )
            plan = setup_module.build_plan(args)
            original_write = setup_module.RefreshSnapshot.write_text

            def write_then_conflict(snapshot, path, content):
                original_write(snapshot, path, content)
                if path == config_path:
                    concurrent = json.loads(path.read_text(encoding="utf-8"))
                    concurrent["consumer_zoom"] = 1.25
                    path.write_text(
                        json.dumps(concurrent, indent=2) + "\n",
                        encoding="utf-8",
                    )

            with mock.patch.object(
                setup_module.RefreshSnapshot, "write_text",
                new=write_then_conflict,
            ), mock.patch.object(
                setup_module.setup_check, "closing",
                return_value=["forced closing failure"],
            ):
                code, result = setup_module.apply_plan(args, plan)
            self.assertEqual(code, 1)
            self.assertTrue(result["rolled_back"])
            relative = "workspace/config.json"
            self.assertIn(relative, result["rollback_conflicts"])
            self.assertEqual(
                json.loads(config_path.read_text(encoding="utf-8"))[
                    "consumer_zoom"
                ],
                1.25,
            )

    @integration
    def test_pre_replace_recheck_preserves_racing_target_edit(self):
        with self.applied_project() as project:
            config_path = (project / "workspace/config.json").resolve()
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["custom_project_field"] = "pre-refresh-drift"
            config_path.write_text(
                json.dumps(config, indent=2) + "\n", encoding="utf-8"
            )
            args = argparse.Namespace(
                project_root=str(project), workspace="workspace", origin=None,
                scale="small", output_language="English",
                terminology_language="English", command="apply", json=True,
            )
            plan = setup_module.build_plan(args)
            original_atomic = setup_module.atomic_text
            injected = False

            def atomic_with_race(path, content, before_replace=None):
                nonlocal injected
                if path == config_path and not injected:
                    concurrent = json.loads(path.read_text(encoding="utf-8"))
                    concurrent["consumer_zoom"] = 1.5
                    path.write_text(
                        json.dumps(concurrent, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    injected = True
                return original_atomic(
                    path, content, before_replace=before_replace
                )

            with mock.patch.object(
                setup_module, "atomic_text", new=atomic_with_race
            ):
                code, result = setup_module.apply_plan(args, plan)
            self.assertEqual(code, 1)
            self.assertIn("concurrent edit changed refresh target",
                          result["error"])
            self.assertIn(
                "workspace/config.json",
                result["rollback_conflicts"],
            )
            self.assertEqual(
                json.loads(config_path.read_text(encoding="utf-8"))[
                    "consumer_zoom"
                ],
                1.5,
            )

    @integration
    def test_setup_check_names_tracked_local_files_outside_ascii_exactly(self):
        """The tracked local file findings name each file from a
        NUL-separated listing; without -z Git quotes a name outside ASCII
        (#276)."""
        with self.applied_project() as project:
            local = ".claude/ayarlar-şğı.json"
            (project / local).parent.mkdir(parents=True, exist_ok=True)
            (project / local).write_text("{}\n", encoding="utf-8")
            subprocess.run(["git", "add", "-f", "--", local],
                           cwd=project, check=True)
            checked = self.run_script(
                CHECK, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(checked.returncode, 1, checked.stdout)
            findings = json.loads(checked.stdout)["findings"]
            self.assertIn(
                "local runtime or projection files are force-added: " + local,
                findings)

    @integration
    def test_runtime_symlink_is_rejected_without_following_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            target = Path(temporary) / "outside"
            project.mkdir()
            target.mkdir()
            init_repository(project)
            (project / ".agentrof").symlink_to(target, target_is_directory=True)
            result = self.run_script(
                SETUP, "--project-root", str(project), "--json"
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("runtime path is symlinked", result.stderr)
            self.assertFalse((target / "agent-marketplace").exists())

    @integration
    def test_apply_refuses_after_three_seconds_while_the_setup_guard_is_held(self):
        """Setup keeps its own bounded wait (#388): an apply that finds the guard
        taken polls it for 3 seconds of monotonic time, then refuses with
        maintenance_busy and writes nothing."""
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            init_repository(project)
            clock = [0.0]

            def sleep(seconds: float) -> None:
                clock[0] += seconds

            output = io.StringIO()
            with setup_module.refresh_guard(project), \
                    mock.patch.object(setup_module, "time", types.SimpleNamespace(
                        monotonic=lambda: clock[0], sleep=sleep)), \
                    contextlib.redirect_stdout(output), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = setup_module.main(
                    ["apply", "--project-root", str(project), "--json"]
                )
            self.assertEqual(code, 1)
            self.assertEqual(
                json.loads(output.getvalue())["error"],
                "maintenance_busy: setup/projector maintenance lock is busy",
            )
            self.assertTrue(3.0 <= clock[0] < 3.1, clock[0])
            self.assertEqual(
                sorted(path.name for path in project.iterdir()),
                [".agentrof", ".git"],
            )

    @staticmethod
    def merged_attributes(current):
        return setup_module.merged_managed_text(
            current, ".gitattributes", setup_module.setup_check.ATTRIBUTES_START,
            setup_module.setup_check.ATTRIBUTES_END,
            setup_module.setup_check.managed_attributes_block("workspace"),
        )

    def test_setup_plans_the_gitattributes_block_as_a_created_managed_block(self):
        with tempfile.TemporaryDirectory() as temporary:
            current, target = setup_module.proposed_gitattributes(
                Path(temporary), "workspace",
            )
            self.assertEqual(list(Path(temporary).iterdir()), [])
        self.assertEqual((current, target), ("", ATTRIBUTES_BLOCK))
        [operation] = setup_module.managed_block_operations(
            "gitattributes", ".gitattributes", False, current, target,
        )
        self.assertEqual(operation["path"], ".gitattributes")
        self.assertEqual(operation["action"], "create")
        self.assertEqual(operation["ownership"], "tracked_managed_block")

    def test_setup_adds_the_gitattributes_block_once_after_project_lines(self):
        before = "* text=auto\n*.png binary\n"
        self.assertEqual(
            self.merged_attributes(before), before + "\n" + ATTRIBUTES_BLOCK,
        )

    def test_setup_replaces_a_stale_gitattributes_block_in_place(self):
        before, after = "* text=auto\n*.png binary\n", "*.sh text eol=lf\n"
        stale = ATTRIBUTES_BLOCK.replace("** -text", "** text")
        self.assertEqual(
            self.merged_attributes(before + stale + after),
            before + ATTRIBUTES_BLOCK + after,
        )

    def test_setup_leaves_a_converged_gitattributes_file_unchanged(self):
        converged = "* text=auto\n*.png binary\n" + ATTRIBUTES_BLOCK + "*.sh text eol=lf\n"
        self.assertEqual(self.merged_attributes(converged), converged)
        self.assertEqual(setup_module.managed_block_operations(
            "gitattributes", ".gitattributes", True, converged,
            self.merged_attributes(converged),
        ), [])

    @integration
    def test_setup_check_reports_a_missing_stale_or_overridden_gitattributes_rule(self):
        with self.applied_project() as project:
            attributes = project / ".gitattributes"
            local_attributes = project / ".git" / "info" / "attributes"
            own_lines = "* text=auto\n"
            for relative in ("workspace/docs/home.md", "workspace/docs/data.json"):
                (project / relative).parent.mkdir(parents=True, exist_ok=True)
                (project / relative).write_text("x\n", encoding="utf-8")
            cases = (
                ("missing", own_lines, "",
                 "managed .gitattributes marker is missing or duplicated", True),
                ("stale", ATTRIBUTES_BLOCK.replace("** -text", "** text"), "",
                 "managed .gitattributes block is stale", True),
                ("later line", ATTRIBUTES_BLOCK + "*.md text\n", "",
                 "workspace/docs/home.md (text: set)", False),
                ("local attributes", ATTRIBUTES_BLOCK, "*.json text\n",
                 "workspace/docs/data.json (text: set)", False),
            )
            for name, text, local, expected, drift in cases:
                with self.subTest(name):
                    attributes.write_bytes(text.encode("utf-8"))
                    local_attributes.parent.mkdir(exist_ok=True)
                    local_attributes.write_bytes(local.encode("utf-8"))
                    checked = self.run_script(
                        SETUP, "check", "--project-root", str(project), "--json"
                    )
                    self.assertEqual(checked.returncode, 1, checked.stdout)
                    findings = json.loads(checked.stdout)["findings"]
                    self.assertTrue(
                        any(expected in item for item in findings), findings
                    )
                    self.assertEqual(
                        "managed refresh drift: .gitattributes" in findings,
                        drift, findings,
                    )
            local_attributes.write_bytes(b"")
            attributes.write_bytes(ATTRIBUTES_BLOCK.encode("utf-8"))
            checked = self.run_script(
                SETUP, "check", "--project-root", str(project), "--json"
            )
            self.assertEqual(checked.returncode, 0, checked.stdout)

    @integration
    @unittest.skipIf(os.name == "nt", "POSIX file mode contract")
    def test_setup_gives_new_files_the_umask_mode_and_keeps_existing_modes(self):
        previous = os.umask(0o022)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary)
                init_repository(project)
                applied = self.run_script(
                    SETUP, "apply", "--project-root", str(project), "--json"
                )
                self.assertEqual(
                    applied.returncode, 0, applied.stdout + applied.stderr
                )
                ignore = project / ".gitignore"
                config = project / "workspace/config.json"
                for path in (ignore, project / ".gitattributes", config):
                    with self.subTest(created=path.name):
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

                ignore.write_text(ignore.read_text(encoding="utf-8").replace(
                    "workspace/junit-*.xml", "workspace/stale-*.xml",
                ), encoding="utf-8")
                value = json.loads(config.read_text(encoding="utf-8"))
                value["custom_project_field"] = True
                config.write_text(json.dumps(value) + "\n", encoding="utf-8")
                for path in (ignore, config):
                    path.chmod(0o640)
                applied = self.run_script(
                    SETUP, "apply", "--project-root", str(project), "--json"
                )
                self.assertEqual(
                    applied.returncode, 0, applied.stdout + applied.stderr
                )
                rewritten = {
                    item["path"] for item in json.loads(applied.stdout)["applied_operations"]
                }
                self.assertTrue({".gitignore", "workspace/config.json"} <= rewritten)
                for path in (ignore, config):
                    with self.subTest(existing=path.name):
                        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)
        finally:
            os.umask(previous)

    @integration
    @unittest.skipIf(os.name == "nt", "native Windows keeps no POSIX mode")
    def test_mode_repair_leaves_untracked_executable_and_outside_files(self):
        """Only tracked non-executable files in setup's scope at exactly 0600 are repaired. They
        gain only the read bits the umask allows, never group write, and a umask that allows no
        read bit repairs nothing (#285)."""
        previous = os.umask(0o022)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary).resolve()
                init_repository(project)
                docs = project / "workspace/docs"
                docs.mkdir(parents=True)
                note, tool, outside = docs / "note.md", docs / "tool.sh", project / "src.py"
                for path in (note, tool, outside):
                    path.write_text("x\n", encoding="utf-8")
                tool.chmod(0o755)
                for command in (["add", "-A"], ["-c", "user.email=test@example.com", "-c", "user.name=Test",
                                                "commit", "-qm", "files"]):
                    subprocess.run(["git", "-C", str(project), *command], check=True)
                draft = docs / "draft.md"
                draft.write_text("x\n", encoding="utf-8")
                for path in (note, outside, draft):
                    path.chmod(0o600)
                tool.chmod(0o700)
                for umask, repairs in ((0o022, {note: 0o644}), (0o002, {note: 0o644}),
                                       (0o027, {note: 0o640}), (0o077, {})):
                    with self.subTest(umask=oct(umask)):
                        os.umask(umask)
                        self.assertEqual(setup_module.owner_only_repairs(project, "workspace"), repairs)
        finally:
            os.umask(previous)


@integration
class WindowsLongPathsChoiceTests(unittest.TestCase):
    """Git for Windows leaves out of a checkout that still exits 0 every tracked file whose path reaches
    260 characters, or whose directory reaches 248, unless core.longpaths is on. Native Windows
    setup asks before it turns that on; any other host asks nothing (#358). The platform is
    mocked, so every host runs these."""

    def run_setup(self, project: Path, command: str, *options: str,
              windows: bool = True) -> tuple[int, dict]:
        output = io.StringIO()
        with mock.patch.object(setup_module, "native_windows", return_value=windows), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = setup_module.main([command, "--project-root", str(project), "--json", *options])
        return code, json.loads(output.getvalue())

    def local_long_paths(self, project: Path) -> str | None:
        value = subprocess.run(["git", "-C", str(project), "config", "--local", "--get", "core.longpaths"],
                               capture_output=True, text=True, check=False)
        return value.stdout.strip() if value.returncode == 0 else None

    def stage(self, project: Path, relative: str) -> str:
        (project / relative).parent.mkdir(parents=True, exist_ok=True)
        (project / relative).write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(project), "add", "--", relative], check=True)
        return relative

    def track(self, project: Path, worktree: Path, length: int, folder: str) -> str:
        """Stage a file whose path is exactly *length* characters long inside *worktree*."""
        relative = f"{folder}/" + "x" * (length - len(str(worktree)) - len(folder) - 6) + ".txt"
        self.assertEqual(len(str(worktree / relative)), length)
        return self.stage(project, relative)

    def directory(self, worktree: Path, length: int, folder: str) -> str:
        """Name a directory whose path is exactly *length* characters long inside *worktree*."""
        relative = f"{folder}/" + "d" * (length - len(str(worktree)) - len(folder) - 2)
        self.assertEqual(len(str(worktree / relative)), length)
        return relative

    def test_native_windows_asks_before_turning_long_paths_on(self):
        with temporary_directory() as temporary:
            project = Path(temporary).resolve()
            init_repository(project)
            code, plan = self.run_setup(project, "inspect")
            self.assertEqual(code, 0, plan)
            self.assertEqual(plan["choice_requests"], [{
                "id": "git.core_longpaths", "surface": "core.longpaths",
                "reason": "windows_path_limit", "options": ["set", "leave"],
                "recommended": "set",
                "preview": {
                    "current_value": None,
                    "set_command": "git config --local core.longpaths true",
                    "worktree_root": str(project),
                    "long_paths": [],
                },
            }])
            code, refused = self.run_setup(project, "apply")
            self.assertEqual(code, 1, refused)
            self.assertEqual(refused["findings"],
                             ["choice required: pass --choice git.core_longpaths=<set|leave>"])
            self.assertFalse((project / "workspace").exists())
            self.assertIsNone(self.local_long_paths(project))
            code, failed = self.run_setup(project, "apply", "--choice", "git.core_longpaths=maybe")
            self.assertEqual(code, 1, failed)
            self.assertIn("invalid --choice 'git.core_longpaths=maybe'", failed["error"])
            self.assertFalse((project / "workspace").exists())

    def test_answering_set_writes_the_local_config(self):
        with temporary_directory() as temporary:
            project = Path(temporary).resolve()
            init_repository(project)
            code, applied = self.run_setup(project, "apply", "--choice", "git.core_longpaths=set")
            self.assertEqual(code, 0, applied)
            self.assertEqual(self.local_long_paths(project), "true")
            self.assertIn({
                "action": "update", "surface": "git_config", "path": "core.longpaths",
                "ownership": "repository_local_config",
                "changes": [{"key": "core.longpaths", "before": None, "after": "true"}],
            }, applied["applied_operations"])
            code, plan = self.run_setup(project, "inspect")
            self.assertEqual((code, plan["choice_requests"], plan["operations"]), (0, [], []))
            code, checked = self.run_setup(project, "check")
            self.assertEqual(code, 0, checked)

    def test_answering_leave_writes_nothing_and_names_each_long_tracked_path(self):
        with temporary_directory() as temporary:
            project = Path(temporary).resolve()
            init_repository(project)
            integration = project
            reaching = self.track(project, integration, 260, "deep")
            short = self.track(project, integration, 259, "near")
            config = (project / ".git" / "config").read_bytes()
            code, plan = self.run_setup(project, "inspect")
            self.assertEqual(code, 0, plan)
            self.assertEqual(plan["choice_requests"][0]["preview"]["long_paths"],
                             [{"path": reaching, "reason": "file_path", "length": 260}])
            code, applied = self.run_setup(project, "apply", "--choice", "git.core_longpaths=leave")
            self.assertEqual(code, 0, applied)
            self.assertEqual((project / ".git" / "config").read_bytes(), config)
            self.assertNotIn("git_config", [item["surface"] for item in applied["applied_operations"]])
            self.assertEqual(applied["warnings"], [
                "core.longpaths stays off, so Git for Windows leaves these tracked paths out of"
                f" the checkout under {integration}: {reaching} (file path 260 >= 260)"
            ])
            self.assertNotIn(short, applied["warnings"][0])
            code, checked = self.run_setup(project, "check")
            self.assertEqual((code, checked["findings"]), (0, []))

    def test_a_directory_reaching_248_characters_leaves_its_short_files_out(self):
        """CreateDirectoryW keeps room for an 8.3 file name, so Git for Windows creates no
        directory whose path reaches 248 characters and leaves out every file in it, even one
        whose own path is under 260. The directory is the reason named, since it fails first."""
        with temporary_directory() as temporary:
            project = Path(temporary).resolve()
            init_repository(project)
            integration = project
            deep = self.directory(integration, 248, "deep")
            inside = self.stage(project, f"{deep}/a.txt")
            crossing = self.stage(project, f"{deep}/" + "y" * 20 + ".txt")
            beside = self.stage(project, self.directory(integration, 247, "near") + "/a.txt")
            self.assertLess(len(str(integration / inside)), 260)
            self.assertGreaterEqual(len(str(integration / crossing)), 260)
            code, plan = self.run_setup(project, "inspect")
            self.assertEqual(code, 0, plan)
            self.assertEqual(plan["choice_requests"][0]["preview"]["long_paths"], [
                {"path": inside, "reason": "directory", "length": 248},
                {"path": crossing, "reason": "directory", "length": 248},
            ])
            code, applied = self.run_setup(project, "apply", "--choice", "git.core_longpaths=leave")
            self.assertEqual(code, 0, applied)
            self.assertEqual(applied["warnings"], [
                "core.longpaths stays off, so Git for Windows leaves these tracked paths out of"
                f" the checkout under {integration}: {inside} (directory 248 >= 248),"
                f" {crossing} (directory 248 >= 248)"
            ])
            self.assertNotIn(beside, applied["warnings"][0])

    def test_a_value_git_reads_as_true_asks_nothing(self):
        # "yes" is true only to Git's own boolean parsing, which a literal "true" would skip.
        with temporary_directory() as temporary:
            project = Path(temporary).resolve()
            init_repository(project)
            subprocess.run(["git", "-C", str(project), "config", "--local", "core.longpaths", "yes"],
                           check=True)
            code, plan = self.run_setup(project, "inspect")
            self.assertEqual((code, plan["choice_requests"]), (0, []))
            code, applied = self.run_setup(project, "apply")
            self.assertEqual(code, 0, applied)
            self.assertEqual(self.local_long_paths(project), "yes")

    def test_other_hosts_ask_nothing(self):
        with temporary_directory() as temporary:
            project = Path(temporary).resolve()
            init_repository(project)
            code, plan = self.run_setup(project, "inspect", windows=False)
            self.assertEqual((code, plan["choice_requests"], plan["warnings"]), (0, [], []))
            code, applied = self.run_setup(project, "apply", "--choice", "git.core_longpaths=set",
                                       windows=False)
            self.assertEqual(code, 0, applied)
            self.assertNotIn("git_config", [item["surface"] for item in applied["applied_operations"]])
            self.assertIsNone(self.local_long_paths(project))


# A changed environment, working directory or subprocess binding builds a fresh project.
_APPLIED_CONTEXT = fixture_cache.context_snapshot((subprocess, fixture_cache))



class SetupInputRulesTests(unittest.TestCase):
    """Preflight inputs independent of the real apply, Git and rollback smokes."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.docs = self.root / "workspace/docs"
        self.docs.mkdir(parents=True)
        self.config = self.root / "workspace/config.json"
        self.args = argparse.Namespace(output_language="English", terminology_language="English")

    def test_fresh_config_uses_only_the_closed_schema(self):
        value, changed = setup_module.desired_config(self.args, self.config)
        self.assertEqual(value, {"schema_version": 2, "team_id": "software-engineering-team",
                                 "output_language": "English", "terminology_language": "English",
                                 **setup_module.project_config.DEFAULTS})
        self.assertEqual(changed, sorted(value))
        self.assertFalse(self.config.exists())

    def test_migration_keeps_languages_and_removes_retired_fields_without_writing(self):
        self.config.write_text(json.dumps({"schema_version": 1, "team_id": "software-engineering-team",
                                          "output_language": "Turkish", "terminology_language": "English",
                                          "scale": "large"}), encoding="utf-8")
        before = self.config.read_bytes()
        value, changed = setup_module.desired_config(self.args, self.config)
        self.assertEqual(value["schema_version"], 2)
        self.assertEqual(value["output_language"], "Turkish")
        self.assertNotIn("scale", value)
        self.assertIn("scale", changed)
        self.assertEqual(self.config.read_bytes(), before)

    def test_foreign_owner_and_future_schema_refuse_without_rewriting(self):
        for value, error in (({"team_id": "other-team"}, "owned by other-team"),
                             ({"team_id": "software-engineering-team", "schema_version": 99},
                              "future schema_version")):
            with self.subTest(value=value):
                self.config.write_text(json.dumps(value), encoding="utf-8")
                before = self.config.read_bytes()
                with self.assertRaisesRegex(setup_module.SetupError, error):
                    setup_module.desired_config(self.args, self.config)
                self.assertEqual(self.config.read_bytes(), before)

    def test_alternate_workspace_ownership_is_shared_with_check_preflight(self):
        alternate = self.root / "alternate"
        alternate.mkdir()
        (alternate / "config.json").write_text('{"team_id":"software-engineering-team"}', encoding="utf-8")
        self.assertEqual(setup_module.alternate_workspaces(self.root), ["alternate"])
        self.assertEqual(check_module.preflight(self.root, "workspace"),
                         ["non-canonical managed workspace: alternate"])

    def test_disposable_databases_pass_but_canonical_runtime_state_refuses(self):
        runtime = self.root / ".agentrof/agent-marketplace/.runtime"
        runtime.mkdir(parents=True)
        for relative in ("tools/grype-db/6/vulnerability.db", "verification/mutation/api/mutants.sqlite",
                         "verification/mutation/api/baseline.sqlite3"):
            path = runtime / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"scratch")
        self.assertEqual(check_module.runtime_findings(self.root), [])
        for reserved in ("project.db", "backlog.sqlite", "backlog.json"):
            path = runtime / reserved
            path.write_bytes(b"durable truth in the wrong place")
            self.assertEqual(check_module.runtime_findings(self.root),
                             ["canonical state is forbidden in runtime: "
                              + path.relative_to(self.root).as_posix()])
            path.unlink()

    def test_state_outside_runtime_is_rejected(self):
        owned = self.root / ".agentrof/agent-marketplace"
        (owned / ".runtime").mkdir(parents=True)
        (owned / "backlog.json").write_text("{}", encoding="utf-8")
        self.assertEqual(check_module.runtime_findings(self.root),
                         ["only .runtime may exist in the owned local tree: "
                          ".agentrof/agent-marketplace/backlog.json"])

if __name__ == "__main__":
    unittest.main()
