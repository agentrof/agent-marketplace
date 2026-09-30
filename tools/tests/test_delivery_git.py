"""Gate 4 naming and read-only Git preflight tests."""

from __future__ import annotations

import contextlib
import errno
import io
import os
import sys
import json
import pathlib
import hashlib
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "software-engineering-team" / "scripts"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))
import backlog_compile  # noqa: E402
import delivery_git  # noqa: E402
import delivery_compile  # noqa: E402
import delivery_governance  # noqa: E402
import delivery_provider  # noqa: E402
import delivery_result  # noqa: E402
import file_lock  # noqa: E402
import operation_compile  # noqa: E402
import architecture_compile  # noqa: E402
import setup_check  # noqa: E402
import stage_package  # noqa: E402
import vault_check  # noqa: E402
from backlog_fixture import make_approved_backlog  # noqa: E402
from git_fixture import disable_automatic_maintenance, init_repository, remove_temporary, temporary_directory  # noqa: E402
from fixture_cache import RepositorySeedCache  # noqa: E402


def write_pull_request_workflow(project: Path) -> None:
    """Give a fixture repository the pull request workflow that execution approval requires."""
    workflow = project / ".github" / "workflows" / "tests.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("on:\n  pull_request:\n", encoding="utf-8")


class WindowsVaultPath(type(Path())):
    """A local path whose path relative to another path of this type renders as on native Windows."""

    def relative_to(self, *other):
        relative = super().relative_to(*other)
        if other and isinstance(other[0], WindowsVaultPath):
            return PureWindowsPath(*relative.parts)
        return relative


def windows_vault_paths():
    """Render paths inside the Delivery vault as native Windows renders them.

    The files stay real. A path relative to the Git checkout keeps its
    separator: its Windows handling is #236.
    """
    docs_root = delivery_compile.docs_root
    return mock.patch.object(delivery_compile, "docs_root", lambda value: WindowsVaultPath(docs_root(value)))


def windows_checkout_paths():
    """Render a path relative to a Git checkout as native Windows renders it.

    The files stay real, and a path relative to any other directory, such as
    the vault, keeps this host's separator: its Windows handling is #228. The
    patch is on the method every path inherits, because the coordinator builds
    its checkout and package paths with Path calls of its own, which a path
    subclass handed to it would not reach.
    """
    relative_to = pathlib.PurePath.relative_to

    def windows_relative_to(self, *other, **options):
        relative = relative_to(self, *other, **options)
        base = os.fspath(other[0]) if other else ""
        if os.path.isabs(base) and os.path.lexists(os.path.join(base, ".git")):
            return pathlib.PureWindowsPath(*relative.parts)
        return relative

    return mock.patch.object(pathlib.PurePath, "relative_to", windows_relative_to)


@contextlib.contextmanager
def git_path_arguments():
    """Record the arguments of every Git call, leaving out absolute local paths."""
    arguments: list[str] = []
    run = subprocess.run

    def recording_run(command, *args, **kwargs):
        if isinstance(command, list) and command[:1] == ["git"]:
            arguments.extend(value for value in map(str, command[1:]) if not os.path.isabs(value))
        return run(command, *args, **kwargs)

    with mock.patch.object(subprocess, "run", recording_run):
        yield arguments


_NATIVE_SUBPROCESS_RUN = subprocess.run
_WINDOWS_PIPE_FIXTURE_CONTEXT = None


@contextlib.contextmanager
def windows_text_pipes(code_page: str = "cp1252"):
    """Give every text-mode subprocess pipe the behaviour CPython gives it on native Windows.

    There stdin goes through a TextIOWrapper whose newline=None writes os.linesep,
    "\\r\\n", for every "\\n" whatever the encoding, and a pipe without an explicit
    encoding encodes and decodes in the ANSI code page, cp1252 on the runner.
    Output line endings fold as on every host, and bytes-mode calls pass untouched.
    """
    run = subprocess.run

    def windows_run(*args, **kwargs):
        text, universal = kwargs.pop("text", None), kwargs.pop("universal_newlines", None)
        encoding, errors = kwargs.pop("encoding", None), kwargs.pop("errors", None)
        if not (text or universal or encoding or errors):
            return run(*args, **kwargs)
        # TextIOWrapper's "locale" token selects the host code page, not a codec.
        encoding = code_page if not encoding or encoding == "locale" else encoding
        errors = errors or "strict"
        check = kwargs.pop("check", False)
        if isinstance(kwargs.get("input"), str):
            kwargs["input"] = kwargs["input"].replace("\n", "\r\n").encode(encoding, errors)
        result = run(*args, **kwargs)
        for stream in ("stdout", "stderr"):
            value = getattr(result, stream)
            if isinstance(value, bytes):
                setattr(result, stream, value.decode(encoding, errors).replace("\r\n", "\n").replace("\r", "\n"))
        if check:
            result.check_returncode()
        return result

    global _WINDOWS_PIPE_FIXTURE_CONTEXT
    previous = _WINDOWS_PIPE_FIXTURE_CONTEXT
    context = {"base_run": run, "runner": windows_run, "caches": {}, "receipts": {}}
    with mock.patch.object(subprocess, "run", windows_run):
        _WINDOWS_PIPE_FIXTURE_CONTEXT = context
        try:
            yield
        finally:
            _WINDOWS_PIPE_FIXTURE_CONTEXT = previous
            for cache in context["caches"].values():
                cache.close()


@contextlib.contextmanager
def approved_fixture_shell_commands(commands: set[str]):
    """Stub only the fixture's declared commands, preserving native host probes."""
    original_run = subprocess.run

    def fixture_command(command, *args, **kwargs):
        if not (isinstance(command, str) and command in commands and kwargs.get("shell")):
            return original_run(command, *args, **kwargs)
        text_mode = any(kwargs.get(key) for key in ("text", "universal_newlines", "encoding", "errors"))
        output = "Fixture command passed\n" if text_mode else b"Fixture command passed\n"
        empty = "" if text_mode else b""
        stdout = output if kwargs.get("capture_output") or kwargs.get("stdout") == subprocess.PIPE else None
        stderr = empty if kwargs.get("capture_output") or kwargs.get("stderr") == subprocess.PIPE else None
        return subprocess.CompletedProcess(command, 0, stdout, stderr)

    with mock.patch.object(subprocess, "run", side_effect=fixture_command):
        yield


class PreStartFixtureCache(RepositorySeedCache):
    """Delivery fixtures share the same pre-runtime isolation boundary."""


_PR_FIXTURE_CACHE = PreStartFixtureCache()
_EXECUTION_FIXTURE_CACHES = {}
_EXECUTION_FIXTURE_RECEIPTS = {}


class DeliveryGitTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        _PR_FIXTURE_CACHE.close()
        for cache in _EXECUTION_FIXTURE_CACHES.values():
            cache.close()
        _EXECUTION_FIXTURE_CACHES.clear()
        _EXECUTION_FIXTURE_RECEIPTS.clear()

    def fixture_cache_context_unchanged(self, subprocess_run=None):
        if dict(os.environ) != _PR_FIXTURE_ENVIRONMENT or os.getcwd() != _PR_FIXTURE_CWD:
            return False
        for owner, name, original in _PR_FIXTURE_BINDINGS:
            expected = subprocess_run if owner is subprocess and name == "run" and subprocess_run is not None else original
            if getattr(owner, name, None) is not expected:
                return False
        return all(getattr(getattr(self, name), "__func__", None) is original
                   for name, original in _PR_FIXTURE_METHODS.items())

    def symlink_or_skip(self, link: Path, target) -> None:
        """Create a symlink, or skip the current test or subtest on a host that cannot."""
        try:
            link.symlink_to(target)
        except OSError as exc:
            self.skipTest(f"symlinks unavailable: {exc}")

    def approve_governance(self, docs: Path, max_parallel: int = 1) -> None:
        initialized = type("Args", (), {"docs": str(docs), "max_parallel": max_parallel})
        self.assertEqual(delivery_governance.init(initialized), 0)
        approved = type("Args", (), {"docs": str(docs)})
        self.assertEqual(delivery_governance.approve(approved), 0)

    def author_execution_topology(self, docs: Path, delivery: str = "DLV-001") -> None:
        self.approve_verification_contract(docs)
        root = delivery_compile.find_delivery(docs, delivery)
        self.assertIsNotNone(root)
        item = root / "items" / "auth-01" / "item.md"
        props, body = delivery_compile.split_note(item)
        props["path_claims"] = ["src/auth.py"]
        props["contract_claims"] = ["auth:session"]
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))

    def approve_verification_contract(self, docs: Path) -> None:
        path = docs / "operation" / "verification-contract.md"
        if path.exists():
            return
        args = type("Args", (), {
            "docs": str(docs), "kind": "verification",
            "constrained_by": ["[[solution-design/decisions/fixture-api|Fixture API]]"],
        })
        self.assertEqual(operation_compile.init(args), 0)
        props, body = operation_compile.parse(path)
        props["test_command"] = "make test"
        operation_compile.atomic_text(path, operation_compile.render(props, body))
        self.assertEqual(operation_compile.approve(args), 0)

    def commit_item_product_change(self, worktree: str, content: str) -> str:
        path = Path(worktree) / "src" / "auth.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", worktree, "add", "src/auth.py"], check=True)
        subprocess.run(["git", "-C", worktree, "commit", "-qm", "Implement authentication"], check=True)
        return subprocess.run(
            ["git", "-C", worktree, "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def approve_item_evidence(self, worktree: str, delivery: str = "DLV-001",
                              story: str = "AUTH-01") -> int:
        import delivery_verification
        args = type("Args", (), {
            "docs": ".", "worktree": worktree, "delivery": delivery, "story": story,
        })
        try:
            frozen = delivery_verification.freeze(Path(worktree), delivery, story, fresh=True)
            raw = {}
            contract, _ = delivery_compile.split_note(Path(worktree) / "workspace/docs/operation/verification-contract.md")
            commands = {contract[kind + "_command"] for kind in ("test", "mutation", "dependency_audit")
                        if isinstance(contract.get(kind + "_command"), str)}
            runtime_commands = []
            if frozen["candidate"]["runtime_required"]:
                environment, _ = delivery_compile.split_note(Path(worktree) / "workspace/docs/operation/environment-contract.md")
                runtime_commands = [("down", None), ("up", None), ("seed", environment["scenarios"][0]), ("logs", None), ("down", None)]
                commands.update(environment["env_command"] + " " + verb + (" " + argument if argument else "")
                                for verb, argument in runtime_commands)
            with approved_fixture_shell_commands(commands):
                for kind in ("test", "mutation", "dependency_audit"):
                    if kind == "test" or contract.get(kind + "_disposition") == "required":
                        raw[kind] = delivery_verification.run_check(Path(worktree), kind)
                runtime_events = []
                for verb, argument in runtime_commands:
                    runtime_events.append(delivery_verification.run_environment(Path(worktree), verb, argument)["evidence_hash"])
            for role, mode in (("code_reviewer", "review_initial"), ("qa_engineer", "qa_final")):
                candidate = frozen["candidate"]
                checks = {key: {"passed": True, "evidence": "Explicit fixture gate result"}
                          for key in delivery_verification.required_checks(Path(worktree), candidate, role)}
                if role == "qa_engineer":
                    contract, _ = delivery_compile.split_note(Path(worktree) / "workspace/docs/operation/verification-contract.md")
                    checks["full_test_suite"].update(command=contract["test_command"], exit_code=0, environment=raw["test"]["identity"]["environment_hash"], raw_evidence_hash=raw["test"]["evidence_hash"])
                    if "mutation_whole_changed_files" in checks:
                        checks["mutation_whole_changed_files"].update(files=candidate["mutation_files"], raw_evidence_hash=raw["mutation"]["evidence_hash"])
                    if "fresh_runtime" in checks:
                        checks["fresh_runtime"]["event_hashes"] = runtime_events
                    if "dependency_audit" in checks:
                        checks["dependency_audit"]["raw_evidence_hash"] = raw["dependency_audit"]["evidence_hash"]
                delivery_verification.register_result(Path(worktree), {
                    "role": role, "mode": mode, "verdict": "passed",
                    "candidate_hash": candidate["candidate_hash"], "session_id": frozen["session_id"],
                    "report": delivery_compile.split_note(Path(worktree) / candidate["report_paths"][0 if role == "code_reviewer" else 1])[1], "checks": checks,
                })
        except (RuntimeError, ValueError, KeyError):
            # Invalid candidates are exercised by approval rejection tests.
            pass
        return delivery_compile.approve_item_evidence(args)

    def make_project(self):
        temporary = tempfile.TemporaryDirectory()
        project = Path(temporary.name)
        init_repository(project, initial_branch="main")
        subprocess.run(["git", "-C", str(project), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
        (project / "workspace" / "docs").mkdir(parents=True)
        (project / "workspace" / "config.json").write_text(
            json.dumps({"schema_version": 2, "team_id": "software-engineering-team",
                        "output_language": "English", "terminology_language": "English"}),
            encoding="utf-8",
        )
        self.approve_governance(project / "workspace" / "docs")
        (project / "README.md").write_text("fixture\n", encoding="utf-8")
        write_pull_request_workflow(project)
        subprocess.run(["git", "-C", str(project), "add", "."], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-qm", "init"], check=True)
        remote = project / "remote.git"
        init_repository(remote, bare=True)
        subprocess.run(["git", "-C", str(project), "remote", "add", "origin", str(remote)], check=True)
        subprocess.run(["git", "-C", str(project), "push", "-q", "-u", "origin", "main"], check=True)
        subprocess.run(["git", "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"], check=True)
        return temporary, project

    def test_pre_start_fixture_copies_isolate_files_objects_and_remote_refs(self):
        cache = PreStartFixtureCache()
        self.addCleanup(cache.close)
        built = []

        def builder():
            built.append(True)
            return self.build_pre_start_fixture()

        first_temporary, first, _docs = cache.copy(builder)
        self.addCleanup(remove_temporary, first_temporary)
        second_temporary, second, _docs = cache.copy(builder)
        self.addCleanup(remove_temporary, second_temporary)
        self.assertEqual(len(built), 1)
        self.assertEqual(cache.snapshot(cache.root), cache.fingerprint)
        expected = delivery_git.run_git(second / "remote.git", "rev-parse", "refs/heads/main")
        for project in (first, second):
            self.assertEqual(delivery_git.run_git(project, "remote", "get-url", "origin"), str(project / "remote.git"))
            self.assertFalse((project / ".git" / "FETCH_HEAD").exists())
            cache.require_pre_start(project)
        object_path = next(path.relative_to(cache.root) for path in (cache.root / ".git" / "objects").rglob("*")
                           if path.is_file() and path.parent.name not in {"info", "pack"})
        for relative in (Path("README.md"), object_path):
            self.assertFalse(os.path.samefile(first / relative, second / relative))
            self.assertFalse(os.path.samefile(first / relative, cache.root / relative))
        (first / "README.md").write_text("Only the first test changes this file.\n", encoding="utf-8")
        delivery_git.run_git(first, "commit", "-qam", "Advance isolated fixture")
        delivery_git.run_git(first, "push", "-q", "origin", "main")
        self.assertNotEqual(delivery_git.run_git(first / "remote.git", "rev-parse", "refs/heads/main"), expected)
        self.assertEqual(delivery_git.run_git(second / "remote.git", "rev-parse", "refs/heads/main"), expected)
        self.assertEqual((second / "README.md").read_text(encoding="utf-8"), "fixture\n")
        self.assertEqual(cache.snapshot(cache.root), cache.fingerprint)

    def test_pre_start_fixture_rejects_seed_mutation(self):
        cache = PreStartFixtureCache()
        self.addCleanup(cache.close)

        def builder():
            temporary, project = self.make_project()
            return temporary, project, project / "workspace" / "docs"

        temporary, _root, _docs = cache.copy(builder)
        self.addCleanup(remove_temporary, temporary)
        (cache.root / "README.md").write_text("accidental seed mutation\n", encoding="utf-8")
        with self.assertRaisesRegex(AssertionError, "immutable fixture seed changed"):
            cache.copy(builder)

    def test_pre_start_fixture_rejects_worktrees_receipts_and_shared_object_stores(self):
        for relative, message in (
            (".git/worktrees/active/gitdir", "linked worktrees"),
            (".agentrof/agent-marketplace/.runtime/receipts/item.json", "machine-local runtime state"),
            (".git/objects/info/alternates", "share another object store"),
            ("remote.git/objects/info/alternates", "share another object store"),
        ):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as raw:
                project = Path(raw)
                path = project / relative
                path.parent.mkdir(parents=True)
                path.write_text("unexpected state\n", encoding="utf-8")
                with self.assertRaisesRegex(AssertionError, message):
                    PreStartFixtureCache.require_pre_start(project)

    def test_pre_start_fixture_bypasses_changed_environment_and_setup_callables(self):
        self.assertTrue(self.fixture_cache_context_unchanged())
        with mock.patch.dict(os.environ, {"AGENTROF_FIXTURE_CONTEXT": "changed"}):
            self.assertFalse(self.fixture_cache_context_unchanged())
        with mock.patch.object(subprocess, "run", wraps=subprocess.run):
            self.assertFalse(self.fixture_cache_context_unchanged())
        with windows_checkout_paths():
            self.assertFalse(self.fixture_cache_context_unchanged())
        with mock.patch.object(delivery_compile, "approve_execution", wraps=delivery_compile.approve_execution):
            self.assertFalse(self.fixture_cache_context_unchanged())
        with mock.patch.object(self, "build_pre_start_fixture", side_effect=RuntimeError("uncached builder called")), \
                mock.patch.object(_PR_FIXTURE_CACHE, "copy", side_effect=AssertionError("cache was used")):
            with self.assertRaisesRegex(RuntimeError, "uncached builder called"):
                self.prepare_pr_intent()
            with self.assertRaisesRegex(RuntimeError, "uncached builder called"):
                self.prepare_pr_intent(use_cache=False)

    def test_project_fixture_disables_automatic_git_maintenance(self):
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
        remote = project / "remote.git"
        local_auto_gc = subprocess.run(
            ["git", "-C", str(project), "config", "--get", "gc.auto"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        remote_auto_gc = subprocess.run(
            ["git", "--git-dir", str(remote), "config", "--get", "gc.auto"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(local_auto_gc, "0")
        self.assertEqual(remote_auto_gc, "0")

    def reserve_scope(self):
        """Build one real remote Delivery reserved at its scope approval, before any Item or Review."""
        temporary, project = self.make_project()
        docs = project / "workspace" / "docs"
        (docs / "maps").mkdir(parents=True, exist_ok=True)
        make_approved_backlog(docs)
        subprocess.run(["git", "-C", str(project), "add", "workspace"], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-qm", "approved backlog"], check=True)
        subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)

        dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(dod), 0)
        self.assertEqual(delivery_compile.approve_dod(dod), 0)
        init = type("Args", (), {
            "docs": str(docs), "id": None, "slug": None,
            "goal": "SAML authentication", "outcome": None,
            "target_branch": "main", "story": ["AUTH-01"],
        })
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_scope(scope), 0)
        subprocess.run(["git", "-C", str(project), "add", "workspace/docs"], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-qm", "scope"], check=True)
        subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
        delivery_git.reserve_delivery(project, "DLV-001")
        return temporary, project, docs

    def build_pre_start_fixture(self):
        """Exercise real scope, execution publication and claiming before any writer exists."""
        temporary, project, docs = self.reserve_scope()
        try:
            scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
            self.author_execution_topology(docs)
            self.assertEqual(delivery_compile.approve_execution(scope), 0)
            delivery_git.publish_execution_plan(project, "DLV-001")
            delivery_git.refresh_target(project, "DLV-001")
            delivery_git.claim_items(project, "DLV-001")
            return temporary, project, docs
        except BaseException:
            remove_temporary(temporary)
            raise

    def prepare_pr_intent(self, author_review=None, *, use_cache=True):
        """Keep each mutable repository isolated; altered setup contexts use the real builder."""
        if use_cache and self.fixture_cache_context_unchanged():
            temporary, project, docs = _PR_FIXTURE_CACHE.copy(self.build_pre_start_fixture)
        else:
            temporary, project, docs = self.build_pre_start_fixture()
        try:
            active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
            product_tip = self.commit_item_product_change(
                active["worktree"], "def authenticate():\n    return 'v1'\n",
            )
            self.assertEqual(self.approve_item_evidence(active["worktree"]), 0)
            delivery_git.push_item(project, "DLV-001", "AUTH-01")
            integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
            if author_review is not None:
                author_review(docs)
            review = type("Args", (), {
                "docs": str(docs), "delivery": "DLV-001",
                "reviewed_commit": integrated["integration"],
                "reviewed_integration_commit": integrated["integration"],
            })
            self.assertEqual(delivery_compile.approve_review(review), 0)
            delivery_git.publish_delivery_review(project, "DLV-001")
            intent = delivery_git.prepare_pr_creation(project, "DLV-001")
            return temporary, project, docs, product_tip, intent
        except BaseException:
            remove_temporary(temporary)
            raise

    @staticmethod
    def fake_provider_type(state: dict):
        """Provider double that performs the final merge on the bare test remote."""
        class FakeProvider:
            def __init__(self, root: Path, remote: str = "origin"):
                self.root = root
                self.remote = remote
                self.repository = "agentrof/example"

            def _head(self) -> str:
                # GitHub keeps a merged PR's head after the merge drops its branch.
                ref = delivery_git.canonical_refs("DLV-001")["integration"]
                return delivery_git.remote_ref_oids(self.root, self.remote, [ref])[ref] or state["head"]

            def _record(self, head: str, base: str) -> dict:
                return {
                    "number": 17,
                    "url": "https://github.com/agentrof/example/pull/17",
                    "state": "MERGED" if state.get("merged") else "OPEN",
                    "isDraft": state.get("draft", True),
                    "headRefName": head,
                    "headRefOid": self._head(),
                    "baseRefName": base,
                    "mergeCommit": {"oid": state["merge"]} if state.get("merged") else None,
                    "statusCheckRollup": [{"name": "checks", "status": "COMPLETED", "conclusion": "SUCCESS"}],
                }

            def exact_unmerged(self, head: str, base: str) -> list[dict]:
                return [self._record(head, base)] if state.get("created") and not state.get("merged") else []

            def list_pull_requests(self, head: str, base: str) -> list[dict]:
                return [self._record(head, base)] if state.get("created") else []

            def create_draft(self, head: str, base: str, title: str, body: str) -> dict:
                state["created"] = True
                state["draft"] = True
                state["title"] = title
                state["body"] = body
                return {"url": "https://github.com/agentrof/example/pull/17"}

            def ensure_draft(self, url: str) -> dict:
                state["draft"] = True
                return {"url": url, "draft": True}

            def update_body(self, url: str, body: str) -> dict:
                state["body"] = body
                return {"url": url}

            def make_ready(self, url: str) -> dict:
                state["draft"] = False
                return {"url": url, "draft": False}

            def inspect_pull_request(self, url: str) -> dict:
                return self._record("agentrof/deliveries/dlv-001", "main")

            def require_green_checks(self, pull_request: dict) -> None:
                if not pull_request.get("statusCheckRollup"):
                    raise AssertionError("green checks are required")

            def merge_commit(self, url: str, head_oid: str) -> dict:
                target_ref = "refs/heads/main"
                target = delivery_git.remote_oid(self.root, self.remote, target_ref)
                merge = delivery_git.merge_candidate(
                    self.root, target, head_oid, "Merge Delivery PR", {},
                )
                delivery_git.atomic_push(self.root, self.remote, [(target_ref, target, merge)])
                state["merged"] = True
                state["merge"] = merge
                state["head"] = head_oid
                return {"url": url, "head": head_oid, "merge_commit": merge}

        return FakeProvider

    def test_refs_are_deterministic_and_slug_free(self):
        refs = delivery_git.short_refs("DLV-001", "AUTH-01", 1)
        self.assertEqual(refs, {
            "fence": "agentrof/fence",
            "integration": "agentrof/deliveries/dlv-001",
            "item": "agentrof/items/auth-01",
            "slot": "agentrof/slots/001",
        })

    def test_no_delivery_slug_or_story_title_enters_ref(self):
        self.assertEqual(
            delivery_git.short_refs("DLV-1042", "PAYMENT-204")["integration"],
            "agentrof/deliveries/dlv-1042",
        )
        self.assertEqual(
            delivery_git.short_refs("DLV-1042", "PAYMENT-204")["item"],
            "agentrof/items/payment-204",
        )

    def test_target_impact_hash_is_order_invariant_and_closed(self):
        items = {
            "AUTH-02": {"action": "replan", "contracts": [], "descendants": [], "merge": "clean", "paths": [], "phase": "unintegrated"},
            "AUTH-01": {"action": "reopen", "contracts": ["api:v1"], "descendants": ["AUTH-02"], "merge": "textual_conflict", "paths": ["src/auth.py"], "phase": "integrated"},
        }
        reversed_items = {"AUTH-01": items["AUTH-01"], "AUTH-02": items["AUTH-02"]}
        first = delivery_git.target_impact_hash("DLV-001", "1" * 40, "2" * 40, items, "sha256:" + "a" * 64, "none")
        second = delivery_git.target_impact_hash("DLV-001", "1" * 40, "2" * 40, reversed_items, "sha256:" + "a" * 64, "none")
        self.assertEqual(first, second)
        self.assertRegex(first, r"^sha256:[0-9a-f]{64}$")

    def test_invalid_zero_slot_and_noninjective_story_are_rejected(self):
        with self.assertRaises(ValueError):
            delivery_git.short_refs("DLV-001", "AUTH-01", 0)
        with self.assertRaises(ValueError):
            delivery_git.short_refs("DLV-001", "AUTH/01")

    def test_coordinator_refusals_report_their_finding_codes(self):
        """A coordinator refusal reaches the result envelope under its own code, with its words intact."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        root, remote = Path(temporary.name) / "project", Path(temporary.name) / "remote.git"
        init_repository(root, initial_branch="main")
        init_repository(remote, bare=True)
        delivery_git.run_git(root, "config", "user.email", "test@example.com")
        delivery_git.run_git(root, "config", "user.name", "Test")
        for name in ("base", "target"):
            (root / f"{name}.txt").write_text(name + "\n", encoding="utf-8")
            delivery_git.run_git(root, "add", f"{name}.txt")
            delivery_git.run_git(root, "commit", "-qm", name)
        base, target = delivery_git.run_git(root, "rev-parse", "HEAD^", "HEAD").split()
        delivery_git.run_git(root, "remote", "add", "origin", str(remote))
        delivery_git.run_git(root, "push", "-q", "origin", "main")
        refs = delivery_git.canonical_refs("DLV-001", "AUTH-01", 1)
        missing = Path(temporary.name) / "missing-worktree"

        def fence(mode: str, fence_target: str) -> str:
            return ("Fence project\n\nAgentrof-Record: project-fence-v2\n"
                    f"Agentrof-Mode: {mode}\nAgentrof-Target: {fence_target}\n")

        for code, message, refusal in (
            ("DELIVERY_SLOT_INVALID", "slot must be a positive number rendered with at least three digits",
             lambda: delivery_git.slot_key(0)),
            ("DELIVERY_CANCELLATION_INVALID", "unsupported cancellation disposition",
             lambda: delivery_git.cancellation_projection(
                 "DLV-001", "none", "Stop", {"AUTH-01": {"disposition": "paused", "tip": "none"}}, target)),
            ("DELIVERY_TARGET_IMPACT_INVALID", "target impact requires exact previous and current target OIDs",
             lambda: delivery_git.target_impact_hash("DLV-001", "none", target, {})),
            ("DELIVERY_FENCE_MISSING", "remote ref is absent: refs/heads/agentrof/fence",
             lambda: delivery_git.remote_oid(root, "origin", refs["fence"])),
            ("DELIVERY_ITEM_REF_MISSING", "remote ref is absent: refs/heads/agentrof/items/auth-01",
             lambda: delivery_git.remote_oid(root, "origin", refs["item"])),
            ("DELIVERY_ITEM_SLOT_MISSING", "remote ref is absent: refs/heads/agentrof/slots/001",
             lambda: delivery_git.remote_oid(root, "origin", refs["slot"])),
            ("DELIVERY_FENCE_MODE", "writer readiness requires an open Fence",
             lambda: delivery_git.require_target_ancestry(root, "origin", fence("upgrade", target), target)),
            ("DELIVERY_TARGET_DRIFT", "target advanced; refresh the Delivery before Item activation",
             lambda: delivery_git.require_target_ancestry(root, "origin", fence("open", base), target)),
            ("DELIVERY_TARGET_CONVERGENCE_REQUIRED", "Integration does not contain the current target",
             lambda: delivery_git.require_target_ancestry(root, "origin", fence("open", target), base)),
            ("DELIVERY_WORKTREE_UNSAFE", f"Item worktree is missing: {missing}",
             lambda: delivery_git.worktree_is_clean_and_at(root, missing, target)),
            ("DELIVERY_LOCAL_REF_DIVERGED", "Item worktree HEAD differs from the remote Item tip",
             lambda: delivery_git.worktree_is_clean_and_at(root, root, base)),
            ("DELIVERY_WRITER_RECEIPT_MISSING", "push-item requires this machine's verified Item writer receipt",
             lambda: delivery_git.active_writer_receipt(root, "DLV-001", "AUTH-01", target, refs["slot"])),
        ):
            with self.subTest(code=code):
                with self.assertRaises((RuntimeError, ValueError)) as refused:
                    refusal()
                result = delivery_result.from_raw("refusal", {"ok": False, "errors": [str(refused.exception)]})
                self.assertEqual([(finding["code"], finding["message"]) for finding in result["findings"]],
                                 [(code, message)])

    def test_worktree_paths_have_no_branch_or_worktree_for_fence_slot(self):
        paths = delivery_git.worktree_paths(Path("/project"), "DLV-001", "AUTH-01")
        self.assertEqual(paths["integration"].as_posix(), "/project/.agentrof/agent-marketplace/.runtime/worktrees/dlv-001/integration")
        self.assertEqual(paths["item"].as_posix(), "/project/.agentrof/agent-marketplace/.runtime/worktrees/dlv-001/items/auth-01")

    def test_writer_receipt_is_exact_and_same_candidate_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = "a" * 40
            epoch = delivery_git.epoch_token()
            first = delivery_git.create_writer_receipt(
                root, "DLV-001", "AUTH-01", "001", epoch,
                "refs/heads/agentrof/items/auth-01",
                "refs/heads/agentrof/slots/001", candidate,
            )
            second = delivery_git.create_writer_receipt(
                root, "DLV-001", "AUTH-01", "001", epoch,
                "refs/heads/agentrof/items/auth-01",
                "refs/heads/agentrof/slots/001", candidate,
            )
            self.assertEqual(first, second)
            self.assertEqual(first["state"], "pending")
            promoted = delivery_git.promote_writer_receipt(root, "DLV-001", "AUTH-01", candidate)
            self.assertEqual(promoted["state"], "verified")
            self.assertEqual(delivery_git.read_writer_receipt(root, "DLV-001", "AUTH-01"), promoted)
            with self.assertRaises(RuntimeError):
                delivery_git.create_writer_receipt(
                    root, "DLV-001", "AUTH-01", "001", delivery_git.epoch_token(),
                    "refs/heads/agentrof/items/auth-01",
                    "refs/heads/agentrof/slots/001", "b" * 40,
                )

    def test_receipt_lock_takes_the_host_lock_around_the_transition(self):
        """A host with fcntl blocks on an flock. Native Windows has no fcntl, so there the
        receipt lock locks its lock file's first byte through msvcrt, waits while another
        process holds that byte and releases it before closing the file, also when the
        guarded transition fails (#246). An msvcrt error that is not contention raises
        instead of waiting forever."""
        events = []
        outcomes = []

        class Fcntl:
            LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8

            @staticmethod
            def flock(descriptor, operation):
                events.append(("flock", operation))

        class Msvcrt:
            LK_UNLCK, LK_NBLCK = 0, 2

            @staticmethod
            def locking(descriptor, mode, size):
                # msvcrt locks from the descriptor's position: record it, then move it
                # off the first byte so every later call must seek back.
                events.append(("locking", mode, size, os.lseek(descriptor, 0, os.SEEK_CUR)))
                os.lseek(descriptor, 1, os.SEEK_SET)
                if outcomes:
                    raise outcomes.pop(0)

        close = os.close

        def record_close(descriptor):
            events.append(("close",))
            close(descriptor)

        busy = PermissionError(errno.EACCES, "Permission denied")
        failed = (RuntimeError, "^transition failed$")
        lock_byte, unlock_byte = ("locking", Msvcrt.LK_NBLCK, 1, 0), ("locking", Msvcrt.LK_UNLCK, 1, 0)
        wait = ("wait", file_lock.POLL_SECONDS)
        for host, modules, raised, error, expected in (
            ("fcntl", {"fcntl": Fcntl, "msvcrt": None}, [], failed,
             [("flock", Fcntl.LOCK_EX), ("transition",), ("flock", Fcntl.LOCK_UN), ("close",)]),
            ("native Windows", {"fcntl": None, "msvcrt": Msvcrt}, [busy, busy], failed,
             [lock_byte, wait, lock_byte, wait, lock_byte, ("transition",), unlock_byte, ("close",)]),
            ("native Windows without contention", {"fcntl": None, "msvcrt": Msvcrt},
             [OSError(errno.EBADF, "Bad file descriptor")], (OSError, "Bad file descriptor"),
             [lock_byte, ("close",)]),
        ):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary:
                events.clear()
                outcomes[:] = raised
                path = Path(temporary) / "receipts" / "item-dlv-001-auth-01.json.lock"
                with mock.patch.dict(sys.modules, modules), \
                        mock.patch.object(file_lock.time, "sleep", side_effect=lambda seconds: events.append(("wait", seconds))), \
                        mock.patch.object(delivery_git.os, "close", side_effect=record_close):
                    with self.assertRaisesRegex(*error):
                        with delivery_git.receipt_lock(path):
                            events.append(("transition",))
                            raise RuntimeError("transition failed")
                self.assertEqual(events, expected)

    def test_receipt_lock_is_released_when_its_holder_dies(self):
        """The receipt lock belongs to the process that holds it: once that process is killed,
        the next writer takes the lock with nothing stale to clear (#246). The host's own lock
        runs here, msvcrt on native Windows."""
        holder_script = (
            "import sys, time\n"
            "from pathlib import Path\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import delivery_git\n"
            "with delivery_git.receipt_lock(Path(sys.argv[2])):\n"
            "    print('held', flush=True)\n"
            "    time.sleep(600)\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "receipts" / "item-dlv-001-auth-01.json.lock"
            holder = subprocess.Popen(
                [sys.executable, "-c", holder_script,
                 str(ROOT / "plugins" / "software-engineering-team" / "scripts"), str(path)],
                stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(holder.stdout.readline(), "held\n")
                descriptor = os.open(path, os.O_RDWR)
                try:
                    self.assertFalse(file_lock.try_lock(descriptor))
                    holder.kill()
                    holder.wait()
                    deadline = time.monotonic() + 30
                    while not file_lock.try_lock(descriptor):
                        self.assertLess(time.monotonic(), deadline, "the killed holder still holds the receipt lock")
                        time.sleep(file_lock.POLL_SECONDS)
                    file_lock.unlock(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                holder.kill()
                holder.wait()
                holder.stdout.close()

    def test_earlier_provider_receipt_gives_way_unless_it_guards_a_pr_the_provider_does_not_show(self):
        """A new PR intent takes over the receipt an earlier intent left, which then can no longer
        elect a call. A receipt whose call started with no exact PR in sight, or that names another
        PR, refuses the new intent and stays."""
        url, other = "https://github.com/agentrof/example/pull/17", "https://github.com/agentrof/example/pull/18"
        earlier, current = ("a" * 40, "A" * 22), ("b" * 40, "B" * 22)
        started = ("a different provider receipt already exists: "
                   "its provider call started and no exact Delivery PR is visible")
        named = f"a different provider receipt already exists: it names {url}, which is not the exact Delivery PR"
        for state, shown, refusal in (
            ("prepared", None, None),
            ("call_started", url, None),
            ("call_started", None, started),
            ("verified", url, None),
            ("verified", other, named),
            ("verified", None, named),
        ):
            with self.subTest(state=state, shown=shown), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                delivery_git.create_provider_receipt(root, "DLV-001", *earlier)
                if state != "prepared":
                    delivery_git.mark_provider_call_started(root, "DLV-001", *earlier)
                if state == "verified":
                    delivery_git.mark_provider_verified(root, "DLV-001", *earlier, url)
                if refusal is None:
                    receipt = delivery_git.create_provider_receipt(root, "DLV-001", *current, exact_pr_url=shown)
                    self.assertEqual([receipt[key] for key in ("intent_oid", "attempt", "state", "url")],
                                     [*current, "prepared", "none"])
                    with self.assertRaisesRegex(RuntimeError, "^DELIVERY_PR_UNCERTAIN: provider receipt preimage"):
                        delivery_git.mark_provider_call_started(root, "DLV-001", *earlier)
                    continue
                with self.assertRaises(RuntimeError) as refused:
                    delivery_git.create_provider_receipt(root, "DLV-001", *current, exact_pr_url=shown)
                result = delivery_result.from_raw("open-pr", {"ok": False, "errors": [str(refused.exception)]})
                self.assertEqual([(finding["code"], finding["message"]) for finding in result["findings"]],
                                 [("DELIVERY_PR_UNCERTAIN", refusal)])
                path, _lock = delivery_git.provider_receipt_paths(root, "DLV-001")
                kept = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual([kept[key] for key in ("intent_oid", "attempt", "state")], [*earlier, state])

    def test_source_handoff_intent_is_durable_and_abort_after_intent_is_blocked(self):
        temporary, project = self.make_project()
        try:
            acquired = delivery_git.begin_source_handoff(project, "sha256:" + "a" * 64)
            self.assertEqual(acquired["mode"], "source_handoff")
            head = subprocess.run(
                ["git", "-C", str(project), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            subprocess.run(["git", "-C", str(project), "branch", "handoff-source", head], check=True)
            subprocess.run(["git", "-C", str(project), "push", "-q", "origin",
                            "handoff-source:refs/heads/handoff-source"], check=True)
            authorized = delivery_git.authorize_target_update(
                project, "source_handoff", "sha256:" + "b" * 64, "origin",
                "direct_target", "refs/heads/handoff-source", "direct",
                head, head, "upstream",
            )
            self.assertEqual(authorized["target_update_intent"], "sha256:" + "b" * 64)
            self.assertEqual(authorized["receipt"]["state"], "prepared")
            self.assertEqual(
                delivery_git.mark_target_call_started(project, "source_handoff",
                                                      authorized["attempt"])["state"],
                "call_started",
            )
            with self.assertRaises(RuntimeError):
                delivery_git.abort_source_handoff(project)
            applied = delivery_git.apply_target_update(project, "source_handoff")
            self.assertEqual(applied["receipt"]["state"], "verified")
            finished = delivery_git.finish_source_handoff(project)
            self.assertEqual(finished["mode"], "open")
            _ref, fence_oid, values = delivery_git._fence_context(project, "origin")
            self.assertEqual(values["Mode"], "open")
            self.assertEqual(values["Target-Update-Intent"], "none")
        finally:
            remove_temporary(temporary)
    def push_protocol_1_fence(self, project: Path, replacing: str = "") -> str:
        """Push the open Fence a protocol-1 project left on the target tip, in place of the
        Fence *replacing* names, and return it."""
        target = delivery_git.remote_oid(project, "origin", "refs/heads/main")
        v1 = delivery_git.commit_tree(
            project, target, [], "Open legacy Agentrof Fence", {
                "Record": "project-fence-v1", "Protocol": "1", "Mode": "open",
                "Epoch": delivery_git.epoch_token(), "Target": target,
                "Config-Hash": "none", "Source-Kind": "none", "Source-Intent": "none",
                "Target-Update-Intent": "none", "Target-Update-Attempt": "none",
                "Target-Repository": "none", "Target-Carrier-Kind": "none",
                "Target-Carrier-Ref": "none", "Target-Carrier-Object": "none",
                "Target-Carrier-Head": "none", "Target-Carrier-Base": "none",
                "Upgrade-Phase": "none", "Upgrade-Contract": "none", "Handoff-Target": "none",
                "Barrier-Kind": "none", "Barrier-Epoch": "none",
            },
        )
        delivery_git.atomic_push(project, "origin", [(delivery_git.canonical_refs("DLV-000")["fence"], replacing, v1)])
        return v1

    def test_a_protocol_1_fence_points_each_reader_to_upgrade_fence_v1(self):
        """A protocol-1 Fence is neither corrupt nor closed: each reader names its migration (#318)."""
        project, docs = self.two_story_project()
        self.scope_delivery(docs, "DLV-001", "auth", "AUTH-01")
        fence = delivery_git.canonical_refs("DLV-000")["fence"]
        v1 = self.push_protocol_1_fence(project)
        target = delivery_git.remote_oid(project, "origin", "refs/heads/main")
        before = delivery_git.run_git(project, "ls-remote", "origin")
        for reader, refusal in (
            ("reserve-delivery", lambda: delivery_git.reserve_delivery(project, "DLV-001")),
            ("apply-governance", lambda: delivery_git.apply_governance(project)),
            ("begin-source-handoff", lambda: delivery_git.begin_source_handoff(project, "sha256:" + "a" * 64)),
            ("writer readiness", lambda: delivery_git.require_target_ancestry(
                project, "origin", delivery_git.commit_message(project, v1), target)),
        ):
            with self.subTest(reader=reader):
                self.assertEqual(self.refused_finding(refusal), (
                    "DELIVERY_PROTOCOL_UNSUPPORTED",
                    "the Fence is protocol 1; migrate it with upgrade-fence-v1 before new mutations"))
                self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), before)

        # A record of neither protocol stays corrupt, for the Fence context and for writer readiness.
        corrupt = delivery_git.commit_tree(project, target, [], "Unknown Fence", {"Record": "project-fence-v3"})
        delivery_git.atomic_push(project, "origin", [(fence, v1, corrupt)])
        for reader, refusal in (
            ("apply-governance", lambda: delivery_git.apply_governance(project)),
            ("writer readiness", lambda: delivery_git.require_target_ancestry(
                project, "origin", delivery_git.commit_message(project, corrupt), target)),
        ):
            with self.subTest(reader=reader, record="project-fence-v3"):
                self.assertEqual(self.refused_finding(refusal),
                                 ("DELIVERY_FENCE_CORRUPT", "current Fence record is unsupported"))
        delivery_git.atomic_push(project, "origin", [(fence, corrupt, v1)])

        # The remedy the refusal names converts the Fence, and the reservation then takes it over.
        delivery_git.upgrade_fence_v1(project)
        reserved = delivery_git.reserve_delivery(project, "DLV-001")
        self.assertEqual(delivery_git.trailer(delivery_git.commit_message(project, reserved["fence"]), "Record"),
                         "project-fence-v2")

    PROTOCOL_1_REFUSAL = ("DELIVERY_PROTOCOL_UNSUPPORTED",
                          "the Fence is protocol 1; migrate it with upgrade-fence-v1 before new mutations")

    def test_delivery_verbs_never_write_a_protocol_2_fence_over_a_protocol_1_one(self):
        """Scope, cancellation, review and PR verbs refuse a protocol-1 Fence before any write (#333)."""
        project, docs = self.two_story_project()
        self.scope_delivery(docs, "DLV-001", "auth", "AUTH-01")
        reserved = delivery_git.reserve_delivery(project, "DLV-001")
        v1 = self.push_protocol_1_fence(project, reserved["fence"])
        before = delivery_git.run_git(project, "ls-remote", "origin")
        provider = mock.Mock()
        with mock.patch("delivery_provider.GitHubProvider", provider):
            for verb, refusal in (
                ("revise-unclaimed-scope", lambda: delivery_git.revise_unclaimed_scope(project, "DLV-001")),
                ("cancel-delivery", lambda: delivery_git.cancel_delivery(project, "DLV-001", "Stop")),
                ("prepare-pr-creation", lambda: delivery_git.prepare_pr_creation(project, "DLV-001")),
                ("invalidate-delivery-review", lambda: delivery_git.invalidate_delivery_review(
                    project, "DLV-001", "REVIEW_FINDING", "sha256:" + "0" * 64)),
                ("record-pr-remote", lambda: delivery_git.record_pr_remote(
                    project, "DLV-001", "https://github.com/agentrof/example/pull/17")),
                ("open-pr", lambda: delivery_git.open_pr(project, "DLV-001")),
            ):
                with self.subTest(verb=verb):
                    self.assertEqual(self.refused_finding(refusal), self.PROTOCOL_1_REFUSAL)
                    self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), before)
        provider.assert_not_called()

        # After the migration the scope revision writes a Fence that carries the approved Governance.
        migrated = delivery_git.upgrade_fence_v1(project)
        self.assertEqual(delivery_git.run_git(project, "rev-parse", migrated["fence"] + "^"), v1)
        revised = delivery_git.revise_unclaimed_scope(project, "DLV-001")
        message = delivery_git.commit_message(project, revised["fence"])
        self.assertEqual([delivery_git.trailer(message, key) for key in ("Record", "Governance-Hash")],
                         ["project-fence-v2", delivery_git.governed_governance_hash(project)])

    def test_item_verbs_never_write_a_protocol_2_fence_over_a_protocol_1_one(self):
        """Pause and reopen refuse a protocol-1 Fence before any write, leaving the writer's state (#333)."""
        project, _docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        refs = delivery_git.canonical_refs("DLV-001", "AUTH-01")
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        fence = delivery_git.remote_oid(project, "origin", refs["fence"])
        v1 = self.push_protocol_1_fence(project, fence)
        before = delivery_git.run_git(project, "ls-remote", "origin")
        self.assertEqual(self.refused_finding(lambda: delivery_git.pause_item(project, "DLV-001", "AUTH-01")),
                         self.PROTOCOL_1_REFUSAL)
        self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), before)
        self.assertTrue(Path(active["worktree"]).is_dir())
        self.assertEqual(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01")["state"], "verified")

        delivery_git.atomic_push(project, "origin", [(refs["fence"], v1, fence)])
        self.commit_item_product_change(active["worktree"], "def authenticate():\n    return True\n")
        self.assertEqual(self.approve_item_evidence(active["worktree"]), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        self.push_protocol_1_fence(project, fence)
        before = delivery_git.run_git(project, "ls-remote", "origin")
        self.assertEqual(self.refused_finding(lambda: delivery_git.reopen_item(project, "DLV-001", "AUTH-01")),
                         self.PROTOCOL_1_REFUSAL)
        self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), before)
        self.assertIsNone(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"))

    def test_quiescent_v1_fence_upgrades_to_governed_v2(self):
        temporary, project = self.make_project()
        try:
            self.push_protocol_1_fence(project)
            preview = delivery_git.upgrade_fence_v1(project, dry_run=True)
            self.assertTrue(preview["changed"])
            result = delivery_git.upgrade_fence_v1(project)
            message = delivery_git.commit_message(project, result["fence"])
            self.assertEqual(delivery_git.trailer(message, "Record"), "project-fence-v2")
            self.assertEqual(delivery_git.trailer(message, "Governance-Hash"), delivery_git.governed_governance_hash(project))
        finally:
            remove_temporary(temporary)
    def test_target_reauthorization_is_fail_closed_without_zero_effect_proof(self):
        # A directory that exists on every host and that no checkout encloses.
        with tempfile.TemporaryDirectory() as outside, \
                mock.patch.dict("os.environ", {"GIT_CEILING_DIRECTORIES": outside}):
            project = Path(outside) / "project"
            project.mkdir()
            with self.assertRaises(RuntimeError):
                delivery_git.reauthorize_target_update(project)

    def test_prepared_target_update_reauthorizes_atomically_after_target_drift(self):
        temporary, project = self.make_project()
        try:
            acquired = delivery_git.begin_source_handoff(project, "sha256:" + "a" * 64)
            head = subprocess.run(
                ["git", "-C", str(project), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            subprocess.run(["git", "-C", str(project), "branch", "handoff-carrier", head], check=True)
            subprocess.run(["git", "-C", str(project), "push", "-q", "origin",
                            "handoff-carrier:refs/heads/handoff-carrier"], check=True)
            authorized = delivery_git.authorize_target_update(
                project, "source_handoff", "sha256:" + "b" * 64, "origin",
                "direct_target", "refs/heads/handoff-carrier", "direct",
                head, head, "upstream",
            )
            (project / "target-drift.txt").write_text("target moved\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(project), "add", "target-drift.txt"], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "advance target"], check=True)
            subprocess.run(["git", "-C", str(project), "push", "-q", "origin", "main"], check=True)
            reauthorized = delivery_git.reauthorize_target_update(project, "source_handoff", "none", "origin")
            self.assertNotEqual(reauthorized["attempt"], authorized["attempt"])
            self.assertEqual(reauthorized["receipt"]["state"], "prepared")
            applied = delivery_git.apply_target_update(project, "source_handoff")
            self.assertEqual(applied["receipt"]["state"], "verified")
            self.assertEqual(delivery_git.finish_source_handoff(project)["mode"], "open")
        finally:
            remove_temporary(temporary)
    def test_direct_target_response_loss_recovers_when_target_equals_candidate(self):
        temporary, project = self.make_project()
        try:
            delivery_git.begin_source_handoff(project, "sha256:" + "a" * 64)
            base = subprocess.run(
                ["git", "-C", str(project), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            subprocess.run(["git", "-C", str(project), "switch", "-q", "-c", "handoff-response-loss"], check=True)
            (project / "response-loss.txt").write_text("candidate\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(project), "add", "response-loss.txt"], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "target candidate"], check=True)
            candidate = subprocess.run(
                ["git", "-C", str(project), "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True,
            ).stdout.strip()
            subprocess.run(["git", "-C", str(project), "push", "-q", "origin",
                            f"{candidate}:refs/heads/handoff-response-loss"], check=True)
            subprocess.run(["git", "-C", str(project), "switch", "-q", "main"], check=True)
            authorized = delivery_git.authorize_target_update(
                project, "source_handoff", "sha256:" + "b" * 64, "origin",
                "direct_target", "refs/heads/handoff-response-loss", "direct",
                candidate, base, "upstream",
            )
            delivery_git.mark_target_call_started(
                project, "source_handoff", authorized["attempt"],
            )
            subprocess.run(["git", "-C", str(project), "push", "-q", "origin",
                            f"{candidate}:refs/heads/main"], check=True)
            recovered = delivery_git.apply_target_update(project, "source_handoff")
            self.assertTrue(recovered["recovered"])
            self.assertEqual(recovered["target_oid"], candidate)
            self.assertEqual(recovered["receipt"]["state"], "verified")
            self.assertEqual(delivery_git.finish_source_handoff(project)["mode"], "open")
        finally:
            remove_temporary(temporary)

    def lose_direct_target_update(self, onto_candidate: bool = False) -> tuple[Path, str, str, str, tuple[str, str]]:
        """Elect a direct target update, move the target before its push lands, and return the refusal.

        The concurrent target commit builds on the base, or on the candidate as
        if the update had landed and its response were lost.
        """
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
        delivery_git.begin_source_handoff(project, "sha256:" + "a" * 64)
        base = delivery_git.run_git(project, "rev-parse", "HEAD")
        (project / "handoff.txt").write_text("target candidate\n", encoding="utf-8")
        candidate = delivery_git.commit_tree(project, base, ["handoff.txt"], "Target candidate", {})
        carrier = "refs/heads/handoff-carrier"
        delivery_git.atomic_push(project, "origin", [(carrier, "", candidate)])
        delivery_git.authorize_target_update(project, "source_handoff", "sha256:" + "b" * 64, "origin",
                                             "direct_target", carrier, "direct", candidate, base, "upstream")
        (project / "concurrent.txt").write_text("concurrent target change\n", encoding="utf-8")
        moved = delivery_git.commit_tree(project, candidate if onto_candidate else base, ["concurrent.txt"],
                                         "Concurrent target change", {})
        mark_call_started = delivery_git.mark_target_call_started

        def move_target_after_election(root, mode, attempt):
            started = mark_call_started(root, mode, attempt)
            delivery_git.atomic_push(root, "origin", [("refs/heads/main", base, moved)])
            return started

        with mock.patch.object(delivery_git, "mark_target_call_started", side_effect=move_target_after_election):
            refusal = self.refused_finding(lambda: delivery_git.apply_target_update(project, "source_handoff"))
        self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), moved)
        return project, base, candidate, moved, refusal

    @staticmethod
    def target_update_state(project: Path) -> str:
        path, _lock = delivery_git.target_receipt_paths(project, "source_handoff")
        return json.loads(path.read_text(encoding="utf-8"))["state"] if path.exists() else "absent"

    def test_direct_target_update_names_the_lease_it_lost(self):
        """A target that moves after the update call was elected is named from the refetched ref."""
        _project, base, _candidate, moved, refusal = self.lose_direct_target_update()
        self.assertEqual(refusal, ("DELIVERY_LEASE_LOST", "a leased ref moved, so the atomic push changed no ref: "
                                   f"refs/heads/main is {moved}, leased as {base}; the target does not contain "
                                   "the update, so its call was released for a fresh attempt or an abort"))

    def test_direct_target_update_that_took_no_effect_releases_its_call(self):
        """A target without the candidate proves the push changed nothing, so the call is released for a fresh
        attempt or an abort; a target that holds the candidate proves nothing and keeps the call elected."""
        for follow_up in ("reauthorize", "abort"):
            with self.subTest(follow_up=follow_up):
                project, _base, candidate, moved, _refusal = self.lose_direct_target_update()
                self.assertEqual(self.target_update_state(project), "prepared")
                if follow_up == "reauthorize":
                    delivery_git.reauthorize_target_update(project, "source_handoff")
                    self.assertEqual(delivery_git.apply_target_update(project, "source_handoff")["receipt"]["state"],
                                     "verified")
                    target = delivery_git.finish_source_handoff(project)["target"]
                    self.assertTrue(delivery_git.is_ancestor(project, candidate, target))
                    self.assertTrue(delivery_git.is_ancestor(project, moved, target))
                else:
                    self.assertEqual(delivery_git.abort_source_handoff(project)["mode"], "open")
                    _ref, _fence, values = delivery_git._fence_context(project, "origin")
                    self.assertEqual(values["Target-Update-Intent"], "none")
                    self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), moved)
                self.assertEqual(self.target_update_state(project), "absent")
        with self.subTest(follow_up="none without the proof"):
            project, _base, _candidate, _moved, _refusal = self.lose_direct_target_update(onto_candidate=True)
            self.assertEqual(self.target_update_state(project), "call_started")
            for verb, message in (
                (delivery_git.apply_target_update, "target moved after target mutation election"),
                (delivery_git.reauthorize_target_update, "only an unspent prepared attempt can be reauthorized"),
                (delivery_git.abort_source_handoff, "abort after a target-update intent requires this host's "
                                                    "prepared direct attempt, absent from the target"),
            ):
                self.assertEqual(self.refused_finding(lambda: verb(project)), ("DELIVERY_TARGET_UPDATE_UNCERTAIN", message))
            self.assertEqual(self.target_update_state(project), "call_started")

    def test_authorization_whose_fence_push_may_have_landed_keeps_its_receipt(self):
        """A Fence that holds the authorized candidate, or moved on from it, may carry the intent, so the
        prepared receipt stays and the handoff goes on; a Fence that never took it lets the receipt go."""
        for case in ("landed", "moved on", "never taken"):
            with self.subTest(case=case):
                temporary, project = self.make_project()
                self.addCleanup(remove_temporary, temporary)
                delivery_git.begin_source_handoff(project, "sha256:" + "a" * 64)
                base = delivery_git.run_git(project, "rev-parse", "HEAD")
                (project / "handoff.txt").write_text("target candidate\n", encoding="utf-8")
                candidate = delivery_git.commit_tree(project, base, ["handoff.txt"], "Target candidate", {})
                carrier = "refs/heads/handoff-carrier"
                delivery_git.atomic_push(project, "origin", [(carrier, "", candidate)])

                def authorize():
                    return delivery_git.authorize_target_update(
                        project, "source_handoff", "sha256:" + "b" * 64, "origin",
                        "direct_target", carrier, "direct", candidate, base, "upstream")

                def advance_fence():
                    ref, fence, values = delivery_git._fence_context(project, "origin")
                    child = delivery_git._fence_child(project, fence, values, "Another coordinator")
                    delivery_git.run_git(project, "push", "-q", "origin", f"--force-with-lease={ref}:{fence}",
                                         f"{child}:{ref}")

                if case == "never taken":
                    code, _message = self.refused_under_concurrent_coordinator(authorize)
                    self.assertEqual(code, "DELIVERY_FENCE_LEASE_LOST")
                    self.assertEqual(self.target_update_state(project), "absent")
                    authorize()
                else:
                    with self.lost_push_response(advance_fence if case == "moved on" else None):
                        code, _message = self.refused_finding(authorize)
                    self.assertEqual(code, "DELIVERY_TRANSACTION_UNCERTAIN")
                    self.assertEqual(self.target_update_state(project), "prepared")
                self.assertEqual(delivery_git.apply_target_update(project, "source_handoff")["receipt"]["state"],
                                 "verified")
                self.assertEqual(delivery_git.finish_source_handoff(project)["target"], candidate)

    def test_reauthorization_that_landed_reports_the_refetched_uncertain_result(self):
        """A reauthorization whose Fence and carrier both took their candidates landed; only a pair that
        disagrees afterwards is mixed."""
        for case in ("landed", "mixed"):
            with self.subTest(case=case):
                temporary, project = self.make_project()
                self.addCleanup(remove_temporary, temporary)
                delivery_git.begin_source_handoff(project, "sha256:" + "a" * 64)
                head = delivery_git.run_git(project, "rev-parse", "HEAD")
                carrier = "refs/heads/handoff-carrier"
                delivery_git.atomic_push(project, "origin", [(carrier, "", head)])
                delivery_git.authorize_target_update(project, "source_handoff", "sha256:" + "b" * 64, "origin",
                                                     "direct_target", carrier, "direct", head, head, "upstream")
                (project / "target-drift.txt").write_text("target moved\n", encoding="utf-8")
                moved = delivery_git.commit_tree(project, head, ["target-drift.txt"], "Advance target", {})
                delivery_git.atomic_push(project, "origin", [("refs/heads/main", head, moved)])

                def rewind_carrier():
                    delivery_git.run_git(project, "push", "-q", "--force", "origin", f"{head}:{carrier}")

                with self.lost_push_response(rewind_carrier if case == "mixed" else None):
                    finding = self.refused_finding(lambda: delivery_git.reauthorize_target_update(project, "source_handoff"))
                fence = delivery_git.canonical_refs("DLV-000")["fence"]
                refs = delivery_git.remote_ref_oids(project, "origin", [fence, carrier])
                if case == "mixed":
                    self.assertEqual(finding, ("DELIVERY_TARGET_UPDATE_UNCERTAIN", "Fence/carrier pair is mixed"))
                    self.assertEqual(refs[carrier], head)
                    continue
                self.assertEqual(finding, ("DELIVERY_TRANSACTION_UNCERTAIN",
                                           "the remote may have taken the atomic push before its response was lost, "
                                           f"so read the refs again before any retry: {fence} holds the pushed "
                                           f"{refs[fence]}; {carrier} holds the pushed {refs[carrier]}"))
                self.assertEqual(self.target_update_state(project), "prepared")
                self.assertEqual(delivery_git.apply_target_update(project, "source_handoff")["receipt"]["state"],
                                 "verified")
                self.assertEqual(delivery_git.finish_source_handoff(project)["target"], refs[carrier])

    def test_open_and_merge_pr_use_the_exact_reviewed_integration_head(self):
        temporary, project, _docs, product_tip, intent = self.prepare_pr_intent(use_cache=False)
        try:
            state: dict = {}
            with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type(state)):
                opened = delivery_git.open_pr(project, "DLV-001")
                self.assertTrue(opened["provider_call"])
                self.assertEqual(opened["pull_request_url"], "https://github.com/agentrof/example/pull/17")
                self.assertEqual(state["title"], "SAML authentication")
                merged = delivery_git.merge_pr(project, "DLV-001")
            self.assertEqual(intent["provider"], "github")
            self.assertTrue(state["body"].strip())
            self.assertEqual(merged["status"], "merged")
            self.assertTrue(delivery_git.is_ancestor(project, product_tip, merged["target_after"]))
            parents = delivery_git.run_git(project, "show", "-s", "--format=%P", merged["merge_commit"]).split()
            self.assertEqual(parents[1], merged["reviewed_integration"])
        finally:
            remove_temporary(temporary)

    def open_pr_command(self, project: Path, state: dict) -> tuple[int, dict]:
        """Run open-pr through its command boundary and parse its whole stdout as one JSON document."""
        output = io.StringIO()
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type(state)), \
                contextlib.redirect_stdout(output):
            exit_code = delivery_git.main(["open-pr", "--project-root", str(project), "--delivery", "DLV-001"])
        return exit_code, json.loads(output.getvalue())

    def republish_review(self, project: Path, docs: Path, authored: dict | None = None) -> None:
        """Invalidate the published Review, approve it again on the new Integration head, from a
        draft with the *authored* sections when given, and publish it."""
        delivery_git.invalidate_delivery_review(project, "DLV-001", "REVIEW_FINDING", "sha256:" + "0" * 64)
        head = delivery_git.remote_oid(project, "origin", delivery_git.canonical_refs("DLV-001")["integration"])
        if authored is not None:
            path = delivery_compile.find_delivery(docs, "DLV-001") / "delivery-review.md"
            path.write_text(delivery_compile.frontmatter(
                {"type": "delivery-review", "status": "draft"},
                delivery_compile.body_for("delivery-review", "Draft review", authored)), encoding="utf-8")
        self.assertEqual(delivery_compile.approve_review(type("Args", (), {
            "docs": str(docs), "delivery": "DLV-001",
            "reviewed_commit": head, "reviewed_integration_commit": head,
        })), 0)
        delivery_git.publish_delivery_review(project, "DLV-001")

    def test_open_pr_prints_only_its_result_envelope(self):
        """open-pr records the PR in the local Review on both of its paths, creating the PR and
        adopting the one a republished Review already has, and prints only its result envelope."""
        temporary, project, docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        url = "https://github.com/agentrof/example/pull/17"
        review = delivery_compile.find_delivery(docs, "DLV-001") / "delivery-review.md"
        state: dict = {}
        exit_code, created = self.open_pr_command(project, state)
        self.assertEqual((exit_code, created["ok"], created["operation"]), (0, True, "open-pr"))
        self.assertIn({"kind": "provider", "target": "pull_request_url", "value": url}, created["observations"])
        self.assertEqual(delivery_compile.split_note(review)[0]["pull_request_url"], url)
        self.republish_review(project, docs)
        self.assertNotIn("pull_request_url", delivery_compile.split_note(review)[0])
        exit_code, adopted = self.open_pr_command(project, state)
        self.assertEqual((exit_code, adopted["ok"], adopted["operation"]), (0, True, "open-pr"))
        self.assertIn({"kind": "provider", "target": "pull_request_url", "value": url}, adopted["observations"])
        self.assertEqual(delivery_compile.split_note(review)[0]["pull_request_url"], url)
        record = next(item["value"] for item in adopted["observations"] if item["target"] == "integration")
        self.assertEqual(delivery_git.trailer(delivery_git.commit_message(project, record + "^"), "Record"),
                         "pr-adoption-intent-v1")

    def test_merge_pr_reports_a_red_check_as_a_required_check_failure(self):
        """The provider's own green-check rule refuses before the merge call, under its finding code."""
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        try:
            state: dict = {}

            class RedCheckProvider(self.fake_provider_type(state), delivery_provider.GitHubProvider):
                require_green_checks = delivery_provider.GitHubProvider.require_green_checks

                def _record(self, head: str, base: str) -> dict:
                    record = super()._record(head, base)
                    record["statusCheckRollup"] = [
                        {"__typename": "CheckRun", "name": "checks", "status": "COMPLETED", "conclusion": "SUCCESS"},
                        {"__typename": "StatusContext", "context": "ci/deploy", "state": "PENDING"},
                    ]
                    return record

            with mock.patch("delivery_provider.GitHubProvider", RedCheckProvider):
                delivery_git.open_pr(project, "DLV-001")
                target = delivery_git.remote_oid(project, "origin", "refs/heads/main")
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    exit_code = delivery_git.main(["merge-pr", "--project-root", str(project), "--delivery", "DLV-001"])
            findings = json.loads(output.getvalue())["findings"]
            self.assertEqual(exit_code, 1)
            self.assertEqual([(finding["code"], finding["message"]) for finding in findings], [
                ("DELIVERY_REQUIRED_CHECK_FAILED", "GitHub required check is not green: ci/deploy (PENDING)"),
            ])
            self.assertNotIn("merged", state)
            self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), target)
        finally:
            remove_temporary(temporary)

    def merge_pr_findings(self, project: Path, provider_type) -> list[tuple[str, str]]:
        """Run merge-pr through its command boundary and return the refusal's findings."""
        output = io.StringIO()
        with mock.patch("delivery_provider.GitHubProvider", provider_type), contextlib.redirect_stdout(output):
            exit_code = delivery_git.main(["merge-pr", "--project-root", str(project), "--delivery", "DLV-001"])
        envelope = json.loads(output.getvalue())
        self.assertEqual((exit_code, envelope["ok"]), (1, False))
        return [(finding["code"], finding["message"]) for finding in envelope["findings"]]

    def test_merge_pr_refusals_report_their_finding_codes(self):
        """A PR that cannot close the Delivery is refused under the code that says why, before any merge."""
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        try:
            with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type({})):
                delivery_git.open_pr(project, "DLV-001")
            integration = delivery_git.remote_oid(
                project, "origin", delivery_git.canonical_refs("DLV-001")["integration"])
            target = delivery_git.remote_oid(project, "origin", "refs/heads/main")
            fake_provider_type = self.fake_provider_type

            def provider(state, listed=None, viewed=None):
                """The provider double whose listed PR, and its view before the merge call, differ as given."""
                class Variant(fake_provider_type(state)):
                    def _record(self, head: str, base: str) -> dict:
                        return {**super()._record(head, base), **(listed or {})}

                    def inspect_pull_request(self, url: str) -> dict:
                        return {**super().inspect_pull_request(url), **(viewed or {})}
                return Variant

            merged = {"created": True, "merged": True}
            for code, message, provider_type in (
                ("DELIVERY_PR_HEAD_BASE_MISMATCH", "exactly one lifecycle PR is required", provider({})),
                ("DELIVERY_PR_STATE_INVALID", "Delivery PR head/base/state is not mergeable",
                 provider({"created": True}, listed={"state": "CLOSED"})),
                ("DELIVERY_PR_HEAD_BASE_MISMATCH", "Delivery PR head/base/state is not mergeable",
                 provider({"created": True}, listed={"baseRefName": "release"})),
                ("DELIVERY_PR_STATE_INVALID", "Delivery PR changed before the merge call",
                 provider({"created": True}, viewed={"isDraft": True})),
                ("DELIVERY_PR_HEAD_BASE_MISMATCH", "Delivery PR changed before the merge call",
                 provider({"created": True}, viewed={"headRefOid": "0" * 40})),
                ("DELIVERY_MERGE_PROOF_INVALID", "provider did not return an exact merge commit",
                 provider({**merged, "merge": None})),
                ("DELIVERY_MERGE_PROOF_INVALID", "provider merge is not present in the exact target ancestry",
                 provider({**merged, "merge": integration})),
            ):
                with self.subTest(code=code, message=message):
                    self.assertEqual(self.merge_pr_findings(project, provider_type), [(code, message)])
                    self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), target)
            # A fast-forward puts the reviewed head itself on the target: no merge commit binds it.
            delivery_git.atomic_push(project, "origin", [("refs/heads/main", target, integration)])
            self.assertEqual(self.merge_pr_findings(project, provider({**merged, "merge": integration})), [
                ("DELIVERY_MERGE_POLICY_INVALID",
                 "provider merge is not an exact two-parent merge of the reviewed Integration"),
            ])
        finally:
            remove_temporary(temporary)

    def test_verify_merge_reports_a_merge_and_never_merges(self):
        """verify-merge only reports a merge. On an open PR it refuses with
        DELIVERY_MERGE_PROOF_INVALID, makes no provider call that changes the PR and leaves the
        target; on the PR merge-pr merged it reports the same merge evidence."""
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        state: dict = {}
        provider = self.fake_provider_type(state)
        with mock.patch("delivery_provider.GitHubProvider", provider):
            delivery_git.open_pr(project, "DLV-001")

        def verify() -> tuple[int, dict]:
            output = io.StringIO()
            with mock.patch("delivery_provider.GitHubProvider", provider), contextlib.redirect_stdout(output):
                exit_code = delivery_git.main(["verify-merge", "--project-root", str(project), "--delivery", "DLV-001"])
            return exit_code, json.loads(output.getvalue())

        target = delivery_git.remote_oid(project, "origin", "refs/heads/main")
        exit_code, refused = verify()
        self.assertEqual((exit_code, [(finding["code"], finding["message"]) for finding in refused["findings"]]),
                         (1, [("DELIVERY_MERGE_PROOF_INVALID", "provider PR is not merged")]))
        self.assertEqual((state.get("merged"), state["draft"]), (None, True))
        self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), target)
        with mock.patch("delivery_provider.GitHubProvider", provider):
            merged = delivery_git.merge_pr(project, "DLV-001")
        target = delivery_git.remote_oid(project, "origin", "refs/heads/main")
        exit_code, verified = verify()
        self.assertEqual((exit_code, verified["ok"], verified["operation"]), (0, True, "verify-merge"))
        self.assertIn({"kind": "ref", "target": "merge_commit", "value": merged["merge_commit"]}, verified["observations"])
        self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), target)

    def test_review_publication_regenerates_the_delivery_projections(self):
        """The published Review is a new note: the Integration's map and relation
        projections are derived from the published tree, never taken from a local
        map that lags the Integration."""
        marker = "<!-- delivery_compile.py: generated deliveries -->"

        def lagging_map(docs):
            path = docs / "maps" / "delivery.md"
            path.write_text(path.read_text().split(marker)[0] + marker + "\n", encoding="utf-8")

        temporary, project, docs, _product_tip, _intent = self.prepare_pr_intent(author_review=lagging_map)
        try:
            integration = delivery_git.remote_oid(project, "origin", delivery_git.canonical_refs("DLV-001")["integration"])
            published = delivery_git.run_git(project, "show", f"{integration}:workspace/docs/maps/delivery.md")
            self.assertIn("|DLV-001]] — `review`", published)
            tree = delivery_git.run_git(project, "rev-parse", integration + "^{tree}")
            self.assertEqual(delivery_git.delivery_projection_changes(project, tree), {})
        finally:
            remove_temporary(temporary)

    def test_invalidated_review_mirrors_its_status_in_its_tags(self):
        temporary, project, docs, _product_tip, _intent = self.prepare_pr_intent()
        try:
            with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type({})):
                delivery_git.open_pr(project, "DLV-001")
            delivery_git.invalidate_delivery_review(project, "DLV-001", "REVIEW_FINDING", "sha256:" + "0" * 64)
            review_path = delivery_compile.find_delivery(docs, "DLV-001") / "delivery-review.md"
            invalidated = delivery_git.remote_oid(project, "origin", delivery_git.canonical_refs("DLV-001")["integration"])
            props, body = delivery_git.split_remote_note(
                project, invalidated, review_path.relative_to(project).as_posix(), delivery_compile.split_note)
            self.assertEqual(props["status"], "changes_requested")
            self.assertEqual(set(props["tags"]), {"doc/delivery-review", "status/changes-requested"})
            self.assertEqual(props["source_hash"], delivery_compile.content_hash(props, body))
        finally:
            remove_temporary(temporary)

    def test_republished_review_opens_its_pr_again_on_the_same_machine(self):
        """A Review invalidated after open-pr and published again gets a new PR intent. On the
        machine that opened the PR, that intent takes over the earlier verified receipt, which names
        the PR the provider still shows, and records that PR again without a provider call."""
        temporary, project, docs, _product_tip, first_intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        receipt_path, _lock = delivery_git.provider_receipt_paths(project, "DLV-001")

        def receipt() -> list[str]:
            value = json.loads(receipt_path.read_text(encoding="utf-8"))
            return [value[key] for key in ("intent_oid", "attempt", "state", "url")]

        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type({})):
            opened = delivery_git.open_pr(project, "DLV-001")
            url = opened["pull_request_url"]
            self.assertEqual(receipt(), [first_intent["intent"], first_intent["attempt"], "verified", url])
            self.republish_review(project, docs)
            intent = delivery_git.prepare_pr_creation(project, "DLV-001")
            reopened = delivery_git.open_pr(project, "DLV-001")
        self.assertEqual((reopened["pull_request_url"], reopened["provider_call"]), (url, False))
        record = delivery_git.commit_message(project, reopened["integration"])
        self.assertEqual([delivery_git.trailer(record, key) for key in ("Record", "Intent", "Pull-Request")],
                         ["pr-url-recorded-v1", intent["intent"], "17"])
        self.assertEqual(receipt(), [intent["intent"], intent["attempt"], "verified", url])

    def test_open_pr_resumes_an_adoption_that_stopped_before_its_record(self):
        """open-pr that stopped after pushing its adoption intent, before the PR record, resumes on
        the next run for the one PR that intent names. A provider that shows another PR is refused
        and the intent stays; no run creates a PR."""
        temporary, project, docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        state: dict = {}
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type(state)):
            url = delivery_git.open_pr(project, "DLV-001")["pull_request_url"]
        self.republish_review(project, docs)

        class AdoptingProvider(self.fake_provider_type(state)):
            def create_draft(self, head: str, base: str, title: str, body: str) -> dict:
                raise AssertionError("an adoption never creates a PR")

        class OtherPrProvider(AdoptingProvider):
            def _record(self, head: str, base: str) -> dict:
                return {**super()._record(head, base), "number": 18,
                        "url": "https://github.com/agentrof/example/pull/18"}

        class Interrupted(Exception):
            """open-pr stops once its adoption intent is on the remote."""

        integration = delivery_git.canonical_refs("DLV-001")["integration"]
        with mock.patch("delivery_provider.GitHubProvider", AdoptingProvider), \
                mock.patch("delivery_compile.record_pr_url", side_effect=Interrupted), \
                self.assertRaises(Interrupted):
            delivery_git.open_pr(project, "DLV-001")
        intent = delivery_git.remote_oid(project, "origin", integration)
        self.assertEqual(delivery_git.trailer(delivery_git.commit_message(project, intent), "Record"),
                         "pr-adoption-intent-v1")
        with mock.patch("delivery_provider.GitHubProvider", OtherPrProvider), \
                self.assertRaises(RuntimeError) as refused:
            delivery_git.open_pr(project, "DLV-001")
        result = delivery_result.from_raw("open-pr", {"ok": False, "errors": [str(refused.exception)]})
        self.assertEqual([(finding["code"], finding["message"]) for finding in result["findings"]],
                         [("DELIVERY_PR_UNCERTAIN", "the exact Delivery PR is not the PR the adoption intent names")])
        self.assertEqual(delivery_git.remote_oid(project, "origin", integration), intent)
        with mock.patch("delivery_provider.GitHubProvider", AdoptingProvider):
            resumed = delivery_git.open_pr(project, "DLV-001")
        self.assertEqual((resumed["pull_request_url"], resumed["provider_call"], resumed["adopted"]), (url, False, True))
        record = delivery_git.commit_message(project, resumed["integration"])
        self.assertEqual([delivery_git.trailer(record, key) for key in ("Record", "Intent", "Pull-Request")],
                         ["pr-url-recorded-v1", intent, "17"])
        self.assertEqual(delivery_git.remote_oid(project, "origin", integration), resumed["integration"])

    def test_published_review_and_pr_carry_the_authored_delivery_review(self):
        authored = {"Scope Disposition": "AUTH-01 delivered as planned.",
                    "Deviations": "The owner added session expiry on 2026-01-01.",
                    "Lessons and Follow-up": "Rotate the fixture keys."}

        def author(docs):
            path = delivery_compile.find_delivery(docs, "DLV-001") / "delivery-review.md"
            path.write_text(delivery_compile.frontmatter(
                {"type": "delivery-review", "status": "draft"},
                delivery_compile.body_for("delivery-review", "Draft review", authored)), encoding="utf-8")

        temporary, project, docs, _product_tip, _intent = self.prepare_pr_intent(author_review=author)
        try:
            review_path = delivery_compile.find_delivery(docs, "DLV-001") / "delivery-review.md"
            published = delivery_git.remote_oid(project, "origin", delivery_git.canonical_refs("DLV-001")["integration"])
            props, body = delivery_git.split_remote_note(
                project, published, review_path.relative_to(project).as_posix(), delivery_compile.split_note)
            for title, text in authored.items():
                self.assertEqual(delivery_compile.section_bodies(body)[title], text)
            self.assertEqual(props["approval_hash"], delivery_compile.content_hash(
                props, body, exclude=delivery_compile.MUTABLE | {"approval_hash"}))
            state: dict = {}
            with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type(state)):
                delivery_git.open_pr(project, "DLV-001")
            for text in authored.values():
                self.assertIn(text, state["body"])
        finally:
            remove_temporary(temporary)

    @staticmethod
    def reported(docs: Path) -> tuple[int, dict]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = delivery_compile.status(type("Args", (), {"docs": str(docs), "delivery": "DLV-001"}))
        return code, json.loads(output.getvalue())

    def reported_status(self, docs: Path) -> str:
        code, reported = self.reported(docs)
        self.assertEqual((code, reported["ok"]), (0, True), reported)
        return reported["status"]

    def merge_and_integration_checkouts(self, project: Path) -> tuple[Path, Path]:
        """Clone the remote, merge the PR head into its main with --no-ff and keep the Integration beside it."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        merged = Path(temporary.name) / "main"
        integration = Path(temporary.name) / "integration"
        head = "origin/" + delivery_git.short_refs("DLV-001")["integration"]
        subprocess.run(["git", "clone", "-q", "-c", "gc.auto=0", str(project / "remote.git"), str(merged)], check=True)
        subprocess.run(["git", "-C", str(merged), "worktree", "add", "-q", "--detach", str(integration), head], check=True)
        subprocess.run(["git", "-C", str(merged), "-c", "user.email=test@example.com", "-c", "user.name=Test",
                        "merge", "-q", "--no-ff", "-m", "Merge pull request #17", head], check=True)
        return merged, integration

    def revise_selected_story(self, docs: Path) -> None:
        """Approve a later backlog revision that changes the selected Story's bytes."""
        story = docs / "backlog/epics/delivery-fixture/stories/auth-01/story.md"
        props, body = backlog_compile.parse_front_matter(story)
        revised = body.replace(
            "Preserve the approved API boundary and avoid delivery-state metadata.",
            "Preserve the approved API boundary, cover the session scenario and avoid delivery-state metadata.")
        self.assertNotEqual(revised, body)
        story.write_text(backlog_compile.front_matter(props, revised), encoding="utf-8")
        props["source_hash"] = backlog_compile.digest(story)
        story.write_text(backlog_compile.front_matter(props, revised), encoding="utf-8")
        record, errors = backlog_compile.collect(docs)
        self.assertEqual(errors, [])
        backlog = docs / "backlog" / "backlog.md"
        backlog_props, backlog_body = backlog_compile.parse_front_matter(backlog)
        backlog_props["package_hash"] = backlog_compile.package_digest(
            docs, backlog_compile.package_paths(record, docs))
        backlog.write_text(backlog_compile.front_matter(backlog_props, backlog_body), encoding="utf-8")

    def test_recorded_pr_moves_the_delivery_to_awaiting_merge_in_the_pr_head(self):
        temporary, project, docs, _product_tip, intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        package = delivery_compile.find_delivery(docs, "DLV-001")
        relative = package.relative_to(project).as_posix()
        provider = self.fake_provider_type({})
        with mock.patch("delivery_provider.GitHubProvider", provider):
            opened = delivery_git.open_pr(project, "DLV-001")
        head = opened["integration"]
        local = delivery_compile.split_note(package / "delivery.md")
        published = delivery_git.split_remote_note(project, head, relative + "/delivery.md", delivery_compile.split_note)
        for props, body in (local, published):
            self.assertEqual(props["status"], "awaiting_merge")
            self.assertEqual(set(props["tags"]), {"doc/delivery", "status/awaiting-merge"})
            self.assertEqual(props["source_hash"], delivery_compile.content_hash(props, body))
        review, review_body = delivery_git.split_remote_note(
            project, head, relative + "/delivery-review.md", delivery_compile.split_note)
        self.assertEqual(review["pull_request_url"], opened["pull_request_url"])
        self.assertEqual(review["approval_hash"], delivery_compile.content_hash(
            review, review_body, exclude=delivery_compile.MUTABLE | {"approval_hash"}))
        # The PR head changes only the Review, the Delivery status and the map that mirrors it.
        self.assertEqual(set(delivery_git.run_git(project, "diff", "--name-only", intent["intent"], head).splitlines()),
                         {relative + "/delivery-review.md", relative + "/delivery.md", "workspace/docs/maps/delivery.md"})
        self.assertIn("|DLV-001]] — `awaiting_merge`",
                      delivery_git.run_git(project, "show", head + ":workspace/docs/maps/delivery.md"))
        self.assertEqual(delivery_git.delivery_projection_changes(project, head), {})
        self.assertEqual(self.reported_status(docs), "awaiting_merge")
        recorded = (package / "delivery.md").read_bytes()
        with mock.patch("delivery_provider.GitHubProvider", provider):
            again = delivery_git.open_pr(project, "DLV-001")
        self.assertTrue(again["reused"])
        self.assertFalse(again["provider_call"])
        self.assertEqual(again["pull_request_url"], opened["pull_request_url"])
        self.assertEqual(delivery_git.remote_oid(project, "origin", delivery_git.canonical_refs("DLV-001")["integration"]), head)
        self.assertEqual((package / "delivery.md").read_bytes(), recorded)

    def test_merging_the_pr_head_reports_merged_and_keeps_the_generated_map(self):
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type({})):
            head = delivery_git.open_pr(project, "DLV-001")["integration"]
        merged, integration = self.merge_and_integration_checkouts(project)
        self.assertEqual(delivery_git.run_git(merged, "rev-parse", "HEAD^2"), head)
        self.assertEqual(self.reported_status(merged / "workspace/docs"), "merged")
        self.assertEqual(self.reported_status(integration / "workspace/docs"), "awaiting_merge")
        # The map renders tracked bytes only, so the target branch keeps the
        # Integration's map and a fresh render there changes nothing.
        map_path = merged / "workspace/docs/maps/delivery.md"
        self.assertIn("|DLV-001]] — `awaiting_merge`", map_path.read_text(encoding="utf-8"))
        self.assertEqual(delivery_git.delivery_projection_changes(merged, "HEAD"), {})
        delivery_compile.render_map(merged / "workspace/docs")
        self.assertEqual(delivery_git.run_git(merged, "status", "--porcelain"), "")

    def test_merged_delivery_keeps_its_pinned_baseline_after_a_story_revision(self):
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type({})):
            delivery_git.open_pr(project, "DLV-001")
        merged, integration = (checkout / "workspace/docs" for checkout in self.merge_and_integration_checkouts(project))
        for docs in (merged, integration):
            self.assertEqual(delivery_compile.delivery_findings(docs, "DLV-001")[1], [])
            self.revise_selected_story(docs)
        self.assertEqual(delivery_compile.delivery_findings(merged, "DLV-001")[1], [])
        _root, stale = delivery_compile.delivery_findings(integration, "DLV-001")
        self.assertTrue(any("story_source_hash is stale" in finding for finding in stale), stale)
        self.assertIn("Delivery backlog_package_hash is stale against the approved backlog", stale)

    def test_merge_reached_through_a_second_parent_proves_the_delivery_merged(self):
        """A branch that later merges the target, as refresh-target does for the next
        Delivery's Integration, reaches the PR merge only through a second parent."""
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type({})):
            delivery_git.open_pr(project, "DLV-001")
        merged, _integration = self.merge_and_integration_checkouts(project)
        refreshed = merged.parent / "refreshed"
        identity = ["-c", "user.email=test@example.com", "-c", "user.name=Test"]
        subprocess.run(["git", "-C", str(merged), "worktree", "add", "-q", "-b", "next", str(refreshed), "HEAD^1"], check=True)
        (refreshed / "next.txt").write_text("next Delivery work\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(refreshed), "add", "next.txt"], check=True)
        subprocess.run(["git", "-C", str(refreshed), *identity, "commit", "-qm", "Next Delivery work"], check=True)
        subprocess.run(["git", "-C", str(refreshed), *identity, "merge", "-q", "--no-ff", "-m", "Refresh target", "main"], check=True)
        proof = delivery_git.run_git(merged, "rev-parse", "HEAD")
        self.assertNotIn(proof, delivery_git.run_git(refreshed, "rev-list", "--first-parent", "HEAD").split())
        self.assertIn(proof, delivery_git.run_git(refreshed, "rev-list", "HEAD").split())
        docs = refreshed / "workspace/docs"
        self.assertEqual(self.reported_status(docs), "merged")
        self.revise_selected_story(docs)
        self.assertEqual(delivery_compile.delivery_findings(docs, "DLV-001")[1], [])

    def test_delivery_recorded_while_in_review_is_merged_by_its_pr_merge(self):
        """A PR recorded before the record moved the Delivery to awaiting_merge left
        it in review; a merge of that recorded head closes it all the same."""
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type({})), \
                mock.patch("delivery_compile.pr_recorded_props", return_value=None):
            delivery_git.open_pr(project, "DLV-001")
        merged, integration = (checkout / "workspace/docs" for checkout in self.merge_and_integration_checkouts(project))
        package = delivery_compile.find_delivery(merged, "DLV-001")
        self.assertEqual(delivery_compile.split_note(package / "delivery.md")[0]["status"], "review")
        self.assertEqual(self.reported_status(merged), "merged")
        self.assertEqual(self.reported_status(integration), "review")
        for docs in (merged, integration):
            self.revise_selected_story(docs)
        self.assertEqual(delivery_compile.delivery_findings(merged, "DLV-001")[1], [])
        self.assertIn("Delivery backlog_package_hash is stale against the approved backlog",
                      delivery_compile.delivery_findings(integration, "DLV-001")[1])

    def test_reopen_after_the_pr_record_does_not_prove_a_merge(self):
        """reopen-item writes a two-parent control commit whose second parent is the
        recorded PR head. Only the provider's merge of the re-recorded head closes
        the Delivery."""
        temporary, project, docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        state: dict = {}
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type(state)):
            record = delivery_git.open_pr(project, "DLV-001")["integration"]
        reopened = delivery_git.reopen_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(delivery_git.run_git(project, "rev-parse", reopened["item"] + "^2"), record)
        worktree = Path(reopened["worktree"])
        self.assertEqual(self.reported_status(worktree / "workspace/docs"), "awaiting_merge")
        self.commit_item_product_change(str(worktree), "def authenticate():\n    return 'v2'\n")
        self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")["integration"]
        view = Path(temporary.name) / "integration-view"
        subprocess.run(["git", "-C", str(project), "worktree", "add", "-q", "--detach", str(view), integrated], check=True)
        self.assertEqual(self.reported_status(view / "workspace/docs"), "awaiting_merge")
        review = type("Args", (), {"docs": str(docs), "delivery": "DLV-001",
                                   "reviewed_commit": integrated, "reviewed_integration_commit": integrated})
        self.assertEqual(delivery_compile.approve_review(review), 0)
        delivery_git.publish_delivery_review(project, "DLV-001")
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type(state)):
            rerecorded = delivery_git.open_pr(project, "DLV-001")
            delivery_git.merge_pr(project, "DLV-001")
        self.assertTrue(rerecorded["adopted"])
        checkout = Path(temporary.name) / "main-after-merge"
        subprocess.run(["git", "clone", "-q", "-c", "gc.auto=0", str(project / "remote.git"), str(checkout)], check=True)
        self.assertEqual(delivery_git.run_git(checkout, "rev-parse", "HEAD^2"), rerecorded["integration"])
        self.assertEqual(self.reported_status(checkout / "workspace/docs"), "merged")
        self.revise_selected_story(view / "workspace/docs")
        self.assertIn("Delivery backlog_package_hash is stale against the approved backlog",
                      delivery_compile.delivery_findings(view / "workspace/docs", "DLV-001")[1])

    def test_merge_state_git_cannot_evaluate_is_reported(self):
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, outside)
        unrecorded = Path(outside.name) / "unrecorded"
        shutil.copytree(project / "workspace", unrecorded / "workspace")
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type({})):
            delivery_git.open_pr(project, "DLV-001")
        # A Review that records no PR leaves nothing to prove, so no history is needed.
        with mock.patch.dict("os.environ", {"GIT_CEILING_DIRECTORIES": outside.name}):
            self.assertEqual(self.reported_status(unrecorded / "workspace/docs"), "review")
        merged, _integration = self.merge_and_integration_checkouts(project)
        shallow = merged.parent / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1", "-c", "gc.auto=0", merged.as_uri(), str(shallow)], check=True)
        exported = merged.parent / "exported"
        shutil.copytree(merged / "workspace", exported / "workspace")
        run = subprocess.run

        def failing_walk(command, *args, **kwargs):
            if "--merges" in command:
                return subprocess.CompletedProcess(command, 128, "", "fatal: simulated walk failure\n")
            return run(command, *args, **kwargs)

        cases = (
            (shallow, contextlib.nullcontext(),
             "Delivery merge state cannot be evaluated in a shallow clone; fetch the full history,"
             " for example with git fetch --unshallow"),
            (exported, mock.patch.dict("os.environ", {"GIT_CEILING_DIRECTORIES": str(merged.parent)}),
             "Delivery merge state cannot be evaluated: "),
            (merged, mock.patch("delivery_compile.subprocess.run", side_effect=failing_walk),
             "Delivery merge state cannot be evaluated: fatal: simulated walk failure"),
        )
        for checkout, context, finding in cases:
            with self.subTest(checkout=checkout.name), context:
                docs = checkout / "workspace/docs"
                code, reported = self.reported(docs)
                self.assertEqual((code, reported["ok"], reported["status"]), (1, False, "awaiting_merge"))
                self.assertEqual(len(reported["errors"]), 1, reported)
                self.assertTrue(reported["errors"][0].startswith(finding), reported)
                self.assertEqual(delivery_compile.delivery_findings(docs, "DLV-001")[1], reported["errors"])
        self.assertEqual(self.reported_status(merged / "workspace/docs"), "merged")

    def test_cancelled_delivery_stays_cancelled_through_its_pr_record_and_merge(self):
        temporary, project, docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        package = delivery_compile.find_delivery(docs, "DLV-001").relative_to(project).as_posix()
        relative, review = package + "/delivery.md", package + "/delivery-review.md"

        def published_status() -> str:
            head = delivery_git.remote_oid(project, "origin", delivery_git.canonical_refs("DLV-001")["integration"])
            return delivery_git.split_remote_note(project, head, relative, delivery_compile.split_note)[0]["status"]

        state: dict = {}
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type(state)):
            delivery_git.open_pr(project, "DLV-001")
            self.assertEqual(published_status(), "awaiting_merge")
            cancelled = delivery_git.cancel_delivery(project, "DLV-001", "The owner withdrew the request")
            self.assertEqual(published_status(), "cancelled")
            rerecorded = delivery_git.open_pr(project, "DLV-001")
            self.assertTrue(rerecorded["adopted"])
            self.assertEqual(published_status(), "cancelled")
            merged = delivery_git.merge_pr(project, "DLV-001")
        # The merge drops the Integration ref; the cancelled Story keeps its Item ref, its claim lock.
        self.assertEqual(merged["observations"], [{"kind": "ref", "target": "agentrof/deliveries/dlv-001", "value": "absent"}])
        self.assertEqual(self.coordination_branches(project), ["agentrof/fence", "agentrof/items/auth-01"])
        # The PR head keeps the cancellation Review and adds only the PR URL. The
        # local Review still holds the approval that the cancellation replaced.
        props, body = delivery_git.split_remote_note(
            project, rerecorded["integration"], review, delivery_compile.split_note)
        cancellation, cancellation_body = delivery_git.split_remote_note(
            project, cancelled["review"], review, delivery_compile.split_note)
        self.assertEqual(body, cancellation_body)
        self.assertEqual(props.pop("pull_request_url"), rerecorded["pull_request_url"])
        for fields in (props, cancellation):
            fields.pop("source_hash")
        self.assertEqual(props, cancellation)
        checkout = Path(temporary.name) / "main-after-merge"
        subprocess.run(["git", "clone", "-q", "-c", "gc.auto=0", str(project / "remote.git"), str(checkout)], check=True)
        self.assertEqual(delivery_git.run_git(checkout, "rev-parse", "HEAD^2"), rerecorded["integration"])
        self.assertEqual(self.reported_status(checkout / "workspace/docs"), "cancelled")
        self.assertEqual(delivery_compile.split_note(checkout / review)[1], cancellation_body)

    def test_pr_opened_after_a_cancellation_has_the_cancellation_review_as_its_body(self):
        """A Delivery cancelled after its Review approval, before any PR existed,
        opens its PR with the published cancellation Review, while the local
        Review still holds the approval that the cancellation replaced."""
        temporary, project, docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        review = delivery_compile.find_delivery(docs, "DLV-001").relative_to(project).as_posix() + "/delivery-review.md"
        cancelled = delivery_git.cancel_delivery(project, "DLV-001", "The owner withdrew the request")
        delivery_git.prepare_pr_creation(project, "DLV-001")
        state: dict = {}
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type(state)):
            self.assertTrue(delivery_git.open_pr(project, "DLV-001")["provider_call"])
        self.assertEqual(state["body"], delivery_git.split_remote_note(
            project, cancelled["review"], review, delivery_compile.split_note)[1])

    def test_cancelling_after_the_pr_opened_gives_the_pr_the_cancellation_review_as_its_body(self):
        """A Delivery cancelled after its PR was opened replaces that PR's approval body with the
        published cancellation Review, whether open-pr adopts the PR or a new PR intent finds it."""
        for prepared in (False, True):
            with self.subTest(prepared=prepared):
                temporary, project, docs, _product_tip, _intent = self.prepare_pr_intent()
                self.addCleanup(remove_temporary, temporary)
                review = delivery_compile.find_delivery(docs, "DLV-001").relative_to(project).as_posix() + "/delivery-review.md"
                state: dict = {}
                with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type(state)):
                    delivery_git.open_pr(project, "DLV-001")
                    approval = state["body"]
                    cancelled = delivery_git.cancel_delivery(project, "DLV-001", "The owner withdrew the request")
                    if prepared:
                        delivery_git.prepare_pr_creation(project, "DLV-001")
                    self.assertFalse(delivery_git.open_pr(project, "DLV-001")["provider_call"])
                cancellation = delivery_git.split_remote_note(
                    project, cancelled["review"], review, delivery_compile.split_note)[1]
                self.assertNotEqual(approval, cancellation)
                self.assertEqual(state["body"], cancellation)

    def test_a_pr_that_already_exists_gets_the_review_at_its_intent_as_its_body(self):
        """A Review published again with new text becomes the body of the PR that already exists,
        whether open-pr adopts that PR or a new PR intent finds it, instead of the earlier Review."""
        deviations = "Session expiry was dropped after the finding."
        for prepared in (False, True):
            with self.subTest(prepared=prepared):
                temporary, project, docs, _product_tip, _intent = self.prepare_pr_intent()
                self.addCleanup(remove_temporary, temporary)
                review = delivery_compile.find_delivery(docs, "DLV-001").relative_to(project).as_posix() + "/delivery-review.md"
                state: dict = {}
                with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type(state)):
                    delivery_git.open_pr(project, "DLV-001")
                    earlier = state["body"]
                    self.republish_review(project, docs, {"Deviations": deviations})
                    if prepared:
                        delivery_git.prepare_pr_creation(project, "DLV-001")
                    reopened = delivery_git.open_pr(project, "DLV-001")
                self.assertFalse(reopened["provider_call"])
                republished = delivery_git.split_remote_note(
                    project, reopened["integration"], review, delivery_compile.split_note)[1]
                self.assertNotIn(deviations, earlier)
                self.assertIn(deviations, republished)
                self.assertEqual(state["body"], republished)

    def test_delivery_cancelled_before_its_review_reaches_the_target_through_its_pr(self):
        """A Delivery cancelled at its scope reservation never has a local Review. open-pr opens,
        records and finds its PR again, and merge-pr merges it, from the PR record alone; the target
        shows the cancelled Delivery with its cancellation Review, and no local Review is written."""
        temporary, project, docs = self.reserve_scope()
        self.addCleanup(remove_temporary, temporary)
        package = delivery_compile.find_delivery(docs, "DLV-001")
        review = package.relative_to(project).as_posix() + "/delivery-review.md"
        cancelled = delivery_git.cancel_delivery(project, "DLV-001", "The owner withdrew the request")
        delivery_git.prepare_pr_creation(project, "DLV-001")
        state: dict = {}
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type(state)):
            opened = delivery_git.open_pr(project, "DLV-001")
            again = delivery_git.open_pr(project, "DLV-001")
            merged = delivery_git.merge_pr(project, "DLV-001")
        url = opened["pull_request_url"]
        cancellation = delivery_git.split_remote_note(project, cancelled["review"], review, delivery_compile.split_note)[1]
        self.assertEqual((opened["provider_call"], state["title"], state["body"]), (True, "SAML authentication", cancellation))
        self.assertEqual((again["pull_request_url"], again["reused"]), (url, True))
        self.assertEqual((merged["status"], merged["pull_request_url"], merged["reviewed_integration"]),
                         ("merged", url, opened["integration"]))
        self.assertFalse((package / "delivery-review.md").exists())
        checkout = Path(temporary.name) / "main-after-merge"
        subprocess.run(["git", "clone", "-q", "-c", "gc.auto=0", str(project / "remote.git"), str(checkout)], check=True)
        self.assertEqual(delivery_git.run_git(checkout, "rev-parse", "HEAD^2"), opened["integration"])
        self.assertEqual(self.reported_status(checkout / "workspace/docs"), "cancelled")
        props, body = delivery_compile.split_note(checkout / review)
        self.assertEqual((props["pull_request_url"], body), (url, cancellation))
        self.assertEqual(delivery_compile.delivery_findings(checkout / "workspace/docs", "DLV-001")[1], [])

    def test_a_cancellation_review_cannot_be_invalidated(self):
        """A cancellation is final. Its Items are cancelled and a second cancellation is refused, so
        after an invalidation of its Review nothing could publish a Review again and the Delivery
        could never reach its PR. The invalidation is refused, changes no ref, and the cancellation
        still goes on to its PR intent."""
        temporary, project, _docs = self.reserve_scope()
        self.addCleanup(remove_temporary, temporary)
        delivery_git.cancel_delivery(project, "DLV-001", "The owner withdrew the request")
        refs = delivery_git.canonical_refs("DLV-001")
        before = [delivery_git.remote_oid(project, "origin", refs[name]) for name in ("fence", "integration")]
        self.assertEqual(self.refused_finding(lambda: delivery_git.invalidate_delivery_review(
            project, "DLV-001", "REVIEW_FINDING", "sha256:" + "0" * 64)), (
            "DELIVERY_CANCELLATION_INVALID",
            "the cancellation Review of a cancelled Delivery is final and cannot be invalidated"))
        self.assertEqual([delivery_git.remote_oid(project, "origin", refs[name]) for name in ("fence", "integration")],
                         before)
        intent = delivery_git.prepare_pr_creation(project, "DLV-001")["intent"]
        self.assertEqual(delivery_git.trailer(delivery_git.commit_message(project, intent), "Record"),
                         "pr-creation-intent-v1")

    def test_a_merged_delivery_refuses_every_change(self):
        """Once the target holds a merge of the recorded PR head, the Delivery is closed: a verb
        that would change it refuses with DELIVERY_POST_MERGE_TRANSITION and moves no ref, while
        open-pr and merge-pr still report the PR and its merge. The merge dropped the Integration
        and Item refs, so the target history alone answers."""
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        provider = self.fake_provider_type({})
        with mock.patch("delivery_provider.GitHubProvider", provider):
            url = delivery_git.open_pr(project, "DLV-001")["pull_request_url"]
            merged = delivery_git.merge_pr(project, "DLV-001")
        refs = [delivery_git.canonical_refs("DLV-001", "AUTH-01")[name] for name in ("fence", "integration", "item")]
        before = delivery_git.remote_ref_oids(project, "origin", refs)
        self.assertEqual([bool(before[ref]) for ref in refs], [True, False, False])
        for verb, change in (
            ("invalidate-delivery-review", lambda: delivery_git.invalidate_delivery_review(
                project, "DLV-001", "REVIEW_FINDING", "sha256:" + "0" * 64)),
            ("cancel-delivery", lambda: delivery_git.cancel_delivery(project, "DLV-001", "Withdrawn after the merge")),
            ("reopen-item", lambda: delivery_git.reopen_item(project, "DLV-001", "AUTH-01")),
            ("begin-plan-revision", lambda: delivery_git.begin_plan_revision(project, "DLV-001")),
            ("refresh-target", lambda: delivery_git.refresh_target(project, "DLV-001")),
        ):
            with self.subTest(verb=verb):
                self.assertEqual(self.refused_finding(change), (
                    "DELIVERY_POST_MERGE_TRANSITION",
                    "the target has merged the PR of DLV-001, so the Delivery is closed"))
                self.assertEqual(delivery_git.remote_ref_oids(project, "origin", refs), before)
        with mock.patch("delivery_provider.GitHubProvider", provider):
            self.assertEqual(delivery_git.open_pr(project, "DLV-001")["pull_request_url"], url)
            self.assertEqual(delivery_git.merge_pr(project, "DLV-001")["merge_commit"], merged["merge_commit"])

    @staticmethod
    def coordination_branches(project: Path) -> list[str]:
        """The coordinator branches the test remote holds."""
        listed = delivery_git.run_git(project, "ls-remote", "origin", "refs/heads/agentrof/*")
        return sorted(line.split("\t")[1].removeprefix("refs/heads/") for line in listed.splitlines())

    def verify_merge_command(self, project: Path, provider) -> tuple[int, dict]:
        output = io.StringIO()
        with mock.patch("delivery_provider.GitHubProvider", provider), contextlib.redirect_stdout(output):
            exit_code = delivery_git.main(["verify-merge", "--project-root", str(project), "--delivery", "DLV-001"])
        return exit_code, json.loads(output.getvalue())

    def test_merge_leaves_only_the_fence_and_verify_merge_still_proves_it(self):
        """merge-pr deletes the Integration ref and the integrated Item ref once it proves the
        merge and reports both absent, so only the Fence stays. verify-merge then finds the PR
        record in the target history, reports the same merge and has nothing left to drop (#286)."""
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        provider = self.fake_provider_type({})
        with mock.patch("delivery_provider.GitHubProvider", provider):
            delivery_git.open_pr(project, "DLV-001")
            merged = delivery_git.merge_pr(project, "DLV-001")
        self.assertEqual(merged["observations"], [{"kind": "ref", "target": ref, "value": "absent"}
                                                  for ref in ("agentrof/deliveries/dlv-001", "agentrof/items/auth-01")])
        self.assertEqual(self.coordination_branches(project), ["agentrof/fence"])
        exit_code, verified = self.verify_merge_command(project, provider)
        self.assertEqual((exit_code, verified["ok"]), (0, True))
        self.assertIn({"kind": "ref", "target": "merge_commit", "value": merged["merge_commit"]}, verified["observations"])
        self.assertIn({"kind": "ref", "target": "reviewed_integration", "value": merged["reviewed_integration"]},
                      verified["observations"])
        self.assertEqual([item for item in verified["observations"] if item["value"] == "absent"], [])
        self.assertEqual(self.coordination_branches(project), ["agentrof/fence"])

    def test_verify_merge_drops_the_refs_an_earlier_merge_left(self):
        """A PR that merged while the coordinator still kept a merged Delivery's refs, as every
        release before #286 did, loses its Integration and integrated Item refs to verify-merge."""
        temporary, project, _docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        provider = self.fake_provider_type({})
        with mock.patch("delivery_provider.GitHubProvider", provider):
            url = delivery_git.open_pr(project, "DLV-001")["pull_request_url"]
        head = delivery_git.remote_oid(project, "origin", delivery_git.canonical_refs("DLV-001")["integration"])
        provider(project).merge_commit(url, head)
        self.assertEqual(self.coordination_branches(project),
                         ["agentrof/deliveries/dlv-001", "agentrof/fence", "agentrof/items/auth-01"])
        exit_code, verified = self.verify_merge_command(project, provider)
        self.assertEqual((exit_code, verified["ok"]), (0, True))
        self.assertEqual([item["target"] for item in verified["observations"] if item["value"] == "absent"],
                         ["agentrof/deliveries/dlv-001", "agentrof/items/auth-01"])
        self.assertEqual(self.coordination_branches(project), ["agentrof/fence"])

    def test_a_published_cancellation_refuses_a_second_cancellation(self):
        """cancel-delivery judges a Delivery by its published status: the local delivery.md keeps
        its scope status after a cancellation, and a Delivery without Item refs has no cancelled
        Item to refuse on, so a second cancellation used to publish a second cancellation Review."""
        temporary, project, docs = self.reserve_scope()
        self.addCleanup(remove_temporary, temporary)
        delivery_git.cancel_delivery(project, "DLV-001", "The owner withdrew the request")
        package = delivery_compile.find_delivery(docs, "DLV-001")
        self.assertEqual(delivery_compile.split_note(package / "delivery.md")[0]["status"], "scope_approved")
        refs = delivery_git.canonical_refs("DLV-001")
        before = [delivery_git.remote_oid(project, "origin", refs[name]) for name in ("fence", "integration")]
        self.assertEqual(self.refused_finding(lambda: delivery_git.cancel_delivery(project, "DLV-001", "Withdrawn again")),
                         ("DELIVERY_CANCELLATION_INVALID", "the published Delivery is already cancelled"))
        self.assertEqual([delivery_git.remote_oid(project, "origin", refs[name]) for name in ("fence", "integration")],
                         before)

    def test_record_pr_remote_checks_the_local_mirror_and_the_adoption_intent(self):
        """record-pr-remote refuses a URL that an existing local Review does not mirror, as it did
        before the PR record became the only source of the URL, and a PR other than the one an
        adoption intent names; it records the named PR once the mirror carries it."""
        temporary, project, docs, _product_tip, _intent = self.prepare_pr_intent()
        self.addCleanup(remove_temporary, temporary)
        url, other = "https://github.com/agentrof/example/pull/17", "https://github.com/agentrof/example/pull/18"
        integration = delivery_git.canonical_refs("DLV-001")["integration"]
        creation_intent = delivery_git.remote_oid(project, "origin", integration)
        self.assertEqual(self.refused_finding(lambda: delivery_git.record_pr_remote(project, "DLV-001", url)),
                         ("DELIVERY_INPUT_INVALID", "local Delivery Review URL does not match the requested PR"))
        self.assertEqual(delivery_git.remote_oid(project, "origin", integration), creation_intent)
        state: dict = {}
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type(state)):
            delivery_git.open_pr(project, "DLV-001")
            self.republish_review(project, docs)
            with mock.patch("delivery_compile.record_pr_url", side_effect=RuntimeError("stopped")), \
                    self.assertRaises(RuntimeError):
                delivery_git.open_pr(project, "DLV-001")
        adoption_intent = delivery_git.remote_oid(project, "origin", integration)
        self.assertEqual(delivery_git.trailer(delivery_git.commit_message(project, adoption_intent), "Record"),
                         "pr-adoption-intent-v1")
        delivery_compile.record_pr_url(docs, "DLV-001", other)
        self.assertEqual(self.refused_finding(lambda: delivery_git.record_pr_remote(project, "DLV-001", other)),
                         ("DELIVERY_PR_UNCERTAIN", "the requested PR is not the PR the adoption intent names"))
        self.assertEqual(delivery_git.remote_oid(project, "origin", integration), adoption_intent)
        delivery_compile.record_pr_url(docs, "DLV-001", url)
        recorded = delivery_git.record_pr_remote(project, "DLV-001", url)
        self.assertEqual(delivery_git.trailer(delivery_git.commit_message(project, recorded["integration"]), "Intent"),
                         adoption_intent)

    def test_scope_cancellation_projection_is_sorted_and_closed(self):
        stories = {
            "AUTH-02": {"disposition": "not_started", "tip": "none"},
            "AUTH-01": {"disposition": "not_started", "tip": "none"},
        }
        projection, digest = delivery_git.cancellation_projection(
            "DLV-001", "sha256:" + "a" * 64,
            "Request withdrawn before execution", stories, "1" * 40,
        )
        self.assertEqual(list(projection["stories"]), ["AUTH-01", "AUTH-02"])
        self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")
        with self.assertRaises(ValueError):
            delivery_git.cancellation_projection(
                "DLV-001", "sha256:" + "a" * 64, "", stories, "1" * 40,
            )
        projection_hash = delivery_git.cancellation_projection_hash(
            "DLV-001", digest, stories, "1" * 40, delivery_git.epoch_token(),
        )
        self.assertRegex(projection_hash, r"^sha256:[0-9a-f]{64}$")
        executed = {"AUTH-01": {"disposition": "unintegrated_discarded", "tip": "2" * 40}}
        _, executed_hash = delivery_git.cancellation_projection(
            "DLV-001", "sha256:" + "a" * 64, "Stopped after activation", executed, "1" * 40,
        )
        self.assertNotEqual(digest, executed_hash)

    @windows_text_pipes()
    def test_active_delivery_cancellation_releases_slot_and_publishes_terminal_item(self):
        # The published map is read back through the runner's text pipes, whose ANSI
        # code page would turn the row's em dash into mojibake (#247).
        with temporary_directory() as temporary:
            project = Path(temporary)
            init_repository(project, initial_branch="main")
            subprocess.run(["git", "-C", str(project), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
            docs = project / "workspace" / "docs"; (docs / "maps").mkdir(parents=True)
            (project / "workspace" / "config.json").write_text(json.dumps({"schema_version": 2, "team_id": "software-engineering-team", "output_language": "English", "terminology_language": "English"}), encoding="utf-8")
            self.approve_governance(docs)
            make_approved_backlog(docs)
            write_pull_request_workflow(project)
            subprocess.run(["git", "-C", str(project), "add", "."], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "init"], check=True)
            remote = project / "remote.git"; init_repository(remote, bare=True)
            subprocess.run(["git", "-C", str(project), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(project), "push", "-q", "-u", "origin", "main"], check=True)
            dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
            delivery_compile.init_dod(dod); delivery_compile.approve_dod(dod)
            init = type("Args", (), {"docs": str(docs), "id": None, "slug": None, "goal": "Cancel active work", "outcome": None, "target_branch": "main", "story": ["AUTH-01"]})
            delivery_compile.init_delivery(init)
            scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"}); delivery_compile.approve_scope(scope)
            subprocess.run(["git", "-C", str(project), "add", "workspace/docs"], check=True); subprocess.run(["git", "-C", str(project), "commit", "-qm", "scope"], check=True); subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
            delivery_git.reserve_delivery(project, "DLV-001")
            self.author_execution_topology(docs)
            delivery_compile.approve_execution(scope)
            delivery_git.publish_execution_plan(project, "DLV-001")
            delivery_git.refresh_target(project, "DLV-001")
            delivery_git.claim_items(project, "DLV-001")
            started = delivery_git.start_item(project, "DLV-001", "AUTH-01")
            cancelled = delivery_git.cancel_delivery(project, "DLV-001", "User stopped the Delivery")
            self.assertTrue(cancelled["ok"])
            self.assertEqual(cancelled["status"], "cancelled")
            self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})
            item_ref = delivery_git.canonical_refs("DLV-001", "AUTH-01")["item"]
            item_oid = delivery_git.remote_oid(project, "origin", item_ref)
            item_message = delivery_git.commit_message(project, item_oid)
            self.assertEqual(delivery_git.trailer(item_message, "Record"), "item-cancelled-v1")
            self.assertEqual(delivery_git.trailer(item_message, "Disposition"), "unintegrated_discarded")
            self.assertEqual(delivery_git.trailer(item_message, "Previous-Tip"), started["item"])
            integration_ref = delivery_git.canonical_refs("DLV-001")["integration"]
            integration = delivery_git.remote_oid(project, "origin", integration_ref)
            integration_message = delivery_git.commit_message(project, integration)
            self.assertEqual(delivery_git.trailer(integration_message, "Record"), "delivery-review-published-v1")
            # The cancellation Review is published like any other: the map and the
            # relation projections come from the published tree.
            published_map = delivery_git.run_git(project, "show", f"{integration}:workspace/docs/maps/delivery.md")
            self.assertIn("|DLV-001]] — `cancelled`", published_map)
            tree = delivery_git.run_git(project, "rev-parse", integration + "^{tree}")
            self.assertEqual(delivery_git.delivery_projection_changes(project, tree), {})

    def claim_item_on_target(self, target_files: dict[str, str], claims: list[str]):
        """Claim AUTH-01 with *claims* once the target holds *target_files* and the Delivery refreshed onto it."""
        temporary, project, docs = self.reserve_scope()
        self.addCleanup(remove_temporary, temporary)
        for relative, text in target_files.items():
            (project / relative).parent.mkdir(parents=True, exist_ok=True)
            (project / relative).write_text(text, encoding="utf-8")
        delivery_git.run_git(project, "add", "--", *target_files)
        delivery_git.run_git(project, "commit", "-qm", "Add the product files")
        delivery_git.run_git(project, "push", "-q")
        self.author_execution_topology(docs)
        item = delivery_compile.find_delivery(docs, "DLV-001") / "items/auth-01/item.md"
        props, body = delivery_compile.split_note(item)
        props["path_claims"] = claims
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
        self.assertEqual(delivery_compile.approve_execution(type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})), 0)
        delivery_git.publish_execution_plan(project, "DLV-001")
        refreshed = delivery_git.refresh_target(project, "DLV-001")
        return project, refreshed, delivery_git.claim_items(project, "DLV-001")

    def integrate_item_change(self, target_files: dict[str, str], claims: list[str], change):
        """Integrate AUTH-01 after *change* stages its product change in the Item worktree."""
        project, refreshed, _claimed = self.claim_item_on_target(target_files, claims)
        worktree = Path(delivery_git.start_item(project, "DLV-001", "AUTH-01")["worktree"])
        change(worktree)
        delivery_git.run_git(worktree, "commit", "-qm", "Change the product")
        self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        return project, refreshed, delivery_git.integrate_item(project, "DLV-001", "AUTH-01")["integration"]

    @staticmethod
    def product_files(project: Path, commit: str) -> dict[str, str]:
        """Each file under src/ in one commit, by its exact path."""
        listing = subprocess.run(["git", "ls-tree", "-r", "-z", "--name-only", commit, "--", "src/"],
                                 cwd=project, capture_output=True, check=True).stdout
        return {path: delivery_git.run_git(project, "show", f"{commit}:{path}")
                for path in (raw.decode("utf-8") for raw in listing.split(b"\0") if raw)}

    def test_cancellation_reverts_integrated_item_paths_outside_ascii(self):
        """The revert takes the Item merge's paths from a NUL-separated listing. A quoted name
        matched no tree entry, so the revert kept what the cancelled Item added or changed (#276)."""
        changed, added = "src/giriş/kayıt.py", "src/giriş/doğrula.py"

        def change(worktree: Path) -> None:
            (worktree / changed).write_text("kayıt = 'sonra'\n", encoding="utf-8")
            (worktree / added).write_text("def doğrula():\n    return 'ş'\n", encoding="utf-8")
            delivery_git.run_git(worktree, "add", "src")

        project, refreshed, integrated = self.integrate_item_change(
            {changed: "kayıt = 'önce'\n"}, ["src/giriş"], change)
        self.assertEqual(refreshed["paths"], [changed])
        cancelled = delivery_git.cancel_delivery(project, "DLV-001", "The owner withdrew the request")
        self.assertEqual(self.product_files(project, integrated),
                         {changed: "kayıt = 'sonra'", added: "def doğrula():\n    return 'ş'"})
        self.assertEqual(len(cancelled["reverts"]), 1)
        for commit in (cancelled["reverts"][0], cancelled["review"]):
            self.assertEqual(self.product_files(project, commit), {changed: "kayıt = 'önce'"})

    def test_cancellation_reverts_an_item_rename_to_its_old_path(self):
        """Git diff detects renames by default and then lists only the new path, so the revert
        removed src/login.py, restored nothing, and left neither name in the Integration (#277)."""
        original = "def authenticate():\n    return 'v0'"

        def rename(worktree: Path) -> None:
            delivery_git.run_git(worktree, "mv", "src/auth.py", "src/login.py")

        project, _refreshed, integrated = self.integrate_item_change(
            {"src/auth.py": original + "\n"}, ["src"], rename)
        self.assertEqual(self.product_files(project, integrated), {"src/login.py": original})
        cancelled = delivery_git.cancel_delivery(project, "DLV-001", "The owner withdrew the request")
        for commit in (cancelled["reverts"][0], cancelled["review"]):
            self.assertEqual(self.product_files(project, commit), {"src/auth.py": original})

    def test_cancellation_removes_an_item_addition_the_main_worktree_holds_untracked(self):
        """update-index --remove keeps a path whose file the working tree holds and stages that
        file, so the revert published the main worktree's untracked copy of a path the cancelled
        Item added (#278)."""
        original = "def authenticate():\n    return 'v0'"

        def add(worktree: Path) -> None:
            (worktree / "src/session.py").write_text("SESSION = 'item'\n", encoding="utf-8")
            delivery_git.run_git(worktree, "add", "src/session.py")

        project, _refreshed, integrated = self.integrate_item_change(
            {"src/auth.py": original + "\n"}, ["src"], add)
        self.assertEqual(self.product_files(project, integrated),
                         {"src/auth.py": original, "src/session.py": "SESSION = 'item'"})
        local = project / "src/session.py"
        local.write_text("SESSION = 'local scratch'\n", encoding="utf-8")
        cancelled = delivery_git.cancel_delivery(project, "DLV-001", "The owner withdrew the request")
        for commit in (cancelled["reverts"][0], cancelled["review"]):
            self.assertEqual(self.product_files(project, commit), {"src/auth.py": original})
        self.assertEqual(local.read_text(encoding="utf-8"), "SESSION = 'local scratch'\n")
        self.assertEqual(delivery_git.run_git(project, "status", "--porcelain", "--", "src/session.py"),
                         "?? src/session.py")

    def test_cancellation_review_links_stay_posix_on_a_host_with_backslash_separators(self):
        """The cancellation Review links its Delivery with forward slashes on every host (#228)."""
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
        docs = project / "workspace/docs"
        make_approved_backlog(docs)
        dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(dod), 0)
        self.assertEqual(delivery_compile.approve_dod(dod), 0)
        init = type("Args", (), {"docs": str(docs), "id": None, "slug": "auth",
                                 "goal": "Authenticate", "outcome": None,
                                 "target_branch": "main", "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        self.assertEqual(delivery_compile.approve_scope(
            type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})), 0)
        delivery_git.run_git(project, "add", "workspace")
        delivery_git.run_git(project, "commit", "-qm", "Approve scope")
        delivery_git.run_git(project, "push", "-q")
        delivery_git.reserve_delivery(project, "DLV-001")
        with windows_vault_paths():
            cancelled = delivery_git.cancel_delivery(project, "DLV-001", "Request withdrawn")
        review = delivery_git.run_git(project, "show", cancelled["review"]
                                      + ":workspace/docs/delivery/deliveries/dlv-001-auth/delivery-review.md")
        self.assertNotIn("\\", review)
        # derives_from and the Navigation section
        self.assertEqual(review.count("[[delivery/deliveries/dlv-001-auth/delivery|DLV-001]]"), 2, review)

    def test_ref_free_reservation_pushes_fence_and_integration_atomically(self):
        with temporary_directory() as temporary:
            project = Path(temporary)
            init_repository(project, initial_branch="main")
            subprocess.run(["git", "-C", str(project), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
            docs = project / "workspace" / "docs"
            (docs / "maps").mkdir(parents=True)
            (project / "workspace" / "config.json").write_text(json.dumps({"schema_version": 2, "team_id": "software-engineering-team", "output_language": "English", "terminology_language": "English"}), encoding="utf-8")
            self.approve_governance(docs)
            make_approved_backlog(docs)
            subprocess.run(["git", "-C", str(project), "add", "."], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "init"], check=True)
            remote = project / "remote.git"
            init_repository(remote, bare=True)
            subprocess.run(["git", "-C", str(project), "remote", "add", "origin", str(remote)], check=True)
            subprocess.run(["git", "-C", str(project), "push", "-q", "-u", "origin", "main"], check=True)
            args = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
            delivery_compile.init_dod(args); delivery_compile.approve_dod(args)
            init = type("Args", (), {"docs": str(docs), "id": None, "slug": None,
                                      "goal": "SAML authentication", "outcome": None, "target_branch": "main",
                                      "story": ["AUTH-01"]})
            delivery_compile.init_delivery(init)
            scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
            delivery_compile.approve_scope(scope)
            subprocess.run(["git", "-C", str(project), "add", "workspace/docs"], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "scope"], check=True)
            subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
            result = delivery_git.reserve_delivery(project, "DLV-001")
            self.assertTrue(result["ok"])
            # A project without a Fence gets a new one on the target, carrying the approved Governance.
            _ref, fence, values = delivery_git._fence_context(project, "origin")
            self.assertEqual((fence, delivery_git.run_git(project, "rev-parse", fence + "^@")),
                             (result["fence"], result["target"]))
            self.assertEqual(values, {**dict.fromkeys(values, "none"), "Mode": "open", "Epoch": values["Epoch"],
                                      "Target": result["target"],
                                      "Governance-Hash": delivery_git.governed_governance_hash(project)})
            (project / "README.md").write_text("target moved\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(project), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(project), "commit", "-qm", "target advance"], check=True)
            subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
            refreshed = delivery_git.refresh_target(project, "DLV-001")
            self.assertTrue(refreshed["changed"])
            self.assertFalse(refreshed["plan_invalidated"])
            integration_message = delivery_git.commit_message(project, refreshed["integration"])
            self.assertEqual(delivery_git.trailer(integration_message, "Record"), "target-refresh-v1")
            revised = delivery_git.revise_unclaimed_scope(project, "DLV-001")
            revised_message = delivery_git.commit_message(project, revised["integration"])
            self.assertEqual(delivery_git.trailer(revised_message, "Record"), "delivery-scope-revised-v1")
            refs = subprocess.run(["git", "--git-dir", str(remote), "show-ref"], check=True, text=True, capture_output=True).stdout
            self.assertIn("refs/heads/agentrof/fence", refs)
            self.assertIn("refs/heads/agentrof/deliveries/dlv-001", refs)
            with self.assertRaises(RuntimeError):
                delivery_git.reserve_delivery(project, "DLV-001")

    def test_reservation_names_the_lease_a_concurrent_reservation_took(self):
        """A Fence another host opens after the absence check is named from the refetched refs."""
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
        docs = project / "workspace" / "docs"
        (docs / "maps").mkdir(parents=True, exist_ok=True)
        make_approved_backlog(docs)
        dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(dod), 0)
        self.assertEqual(delivery_compile.approve_dod(dod), 0)
        delivery_git.run_git(project, "add", "workspace")
        delivery_git.run_git(project, "commit", "-qm", "approved backlog")
        delivery_git.run_git(project, "push", "-q")
        init = type("Args", (), {"docs": str(docs), "id": None, "slug": "auth", "goal": "Authenticate",
                                 "outcome": None, "target_branch": "main", "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        self.assertEqual(delivery_compile.approve_scope(type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})), 0)
        refs = delivery_git.canonical_refs("DLV-001")
        package_paths = delivery_git.package_paths
        opened = []

        def open_fence_concurrently(*args, **kwargs):
            if not opened:
                opened.append(delivery_git.remote_oid(project, "origin", "refs/heads/main"))
                delivery_git.atomic_push(project, "origin", [(refs["fence"], "", opened[0])])
            return package_paths(*args, **kwargs)

        with mock.patch.object(delivery_git, "package_paths", side_effect=open_fence_concurrently):
            finding = self.refused_finding(lambda: delivery_git.reserve_delivery(project, "DLV-001"))
        self.assertEqual(finding, ("DELIVERY_FENCE_LEASE_LOST", "the project Fence moved, so the atomic push changed no ref: "
                                   f"{refs['fence']} is {opened[0]}, leased as absent"))
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), opened[0])
        self.assertFalse(delivery_git.remote_has_ref(project, "origin", refs["integration"]))

    def two_story_project(self) -> tuple[Path, Path]:
        """A pushed project whose approved backlog holds AUTH-01 and AUTH-02 and whose Definition of Done is approved."""
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
        docs = project / "workspace" / "docs"
        (docs / "maps").mkdir(parents=True, exist_ok=True)
        make_approved_backlog(docs, "AUTH-01", "AUTH-02")
        dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(dod), 0)
        self.assertEqual(delivery_compile.approve_dod(dod), 0)
        delivery_git.run_git(project, "add", "workspace")
        delivery_git.run_git(project, "commit", "-qm", "approved backlog")
        delivery_git.run_git(project, "push", "-q")
        return project, docs

    def scope_delivery(self, docs: Path, delivery: str, slug: str, story: str) -> None:
        """Create *delivery* for one Story and approve its scope."""
        init = type("Args", (), {"docs": str(docs), "id": delivery, "slug": slug, "goal": f"Deliver {story}",
                                 "outcome": None, "target_branch": "main", "story": [story]})
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        self.assertEqual(delivery_compile.approve_scope(type("Args", (), {"docs": str(docs), "delivery": delivery})), 0)

    def test_the_next_delivery_reserves_on_a_child_of_the_fence_a_merged_one_left(self):
        """A merged Delivery drops its refs and leaves the idle project Fence, which the next
        reservation takes over: a Fence child with a new Epoch and the target tip as its Target,
        pushed with the new Integration under the Fence lease. That Delivery claims without a
        target refresh and is not reserved twice, and a checkout that still holds the merged
        Delivery at its scope approval cannot reserve it again (#315)."""
        project, docs = self.two_story_project()
        self.scope_delivery(docs, "DLV-001", "auth", "AUTH-01")
        delivery_git.run_git(project, "add", "workspace/docs")
        delivery_git.run_git(project, "commit", "-qm", "scope")
        delivery_git.run_git(project, "push", "-q")
        scope = delivery_git.run_git(project, "rev-parse", "HEAD")
        delivery_git.reserve_delivery(project, "DLV-001")
        self.author_execution_topology(docs)
        self.assertEqual(delivery_compile.approve_execution(type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})), 0)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        self.merge_waited_for_delivery(project)
        self.assertEqual(self.coordination_branches(project), ["agentrof/fence"])

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        stale = Path(temporary.name) / "stale"
        subprocess.run(["git", "clone", "-q", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
                        str(project / "remote.git"), str(stale)], check=True)
        delivery_git.run_git(stale, "checkout", "-q", "--detach", scope)
        before = delivery_git.run_git(project, "ls-remote", "origin")
        self.assertEqual(self.refused_finding(lambda: delivery_git.reserve_delivery(stale, "DLV-001")), (
            "DELIVERY_POST_MERGE_TRANSITION", "the target has merged the PR of DLV-001, so the Delivery is closed"))
        self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), before)

        self.scope_delivery(docs, "DLV-002", "session", "AUTH-02")
        _ref, left, left_values = delivery_git._fence_context(project, "origin")
        target = delivery_git.remote_oid(project, "origin", "refs/heads/main")
        self.assertNotEqual(left_values["Target"], target)
        reserved = delivery_git.reserve_delivery(project, "DLV-002")
        _ref, fence, values = delivery_git._fence_context(project, "origin")
        self.assertEqual((fence, delivery_git.run_git(project, "rev-parse", fence + "^@")), (reserved["fence"], left))
        self.assertNotEqual(values["Epoch"], left_values["Epoch"])
        self.assertEqual(values, {**left_values, "Epoch": values["Epoch"], "Target": target})
        self.assertEqual(delivery_git.run_git(project, "rev-parse", reserved["integration"] + "^@"), target)
        message = delivery_git.commit_message(project, reserved["integration"])
        self.assertEqual([delivery_git.trailer(message, key) for key in ("Record", "Delivery", "Target")],
                         ["delivery-reservation-v1", "DLV-002", target])
        self.assertEqual(self.coordination_branches(project), ["agentrof/deliveries/dlv-002", "agentrof/fence"])

        before = delivery_git.run_git(project, "ls-remote", "origin")
        self.assertEqual(self.refused_finding(lambda: delivery_git.reserve_delivery(project, "DLV-002")), (
            "DELIVERY_REF_COLLISION", "reservation requires an absent Integration ref"))
        self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), before)

        # The Fence names the target the new Integration is built on, so claiming needs no refresh first.
        waiting = delivery_compile.find_delivery(docs, "DLV-002") / "items" / "auth-02" / "item.md"
        props, body = delivery_compile.split_note(waiting)
        props["path_claims"] = ["src/session.py"]
        delivery_compile.atomic_text(waiting, delivery_compile.frontmatter(props, body))
        self.assertEqual(delivery_compile.approve_execution(type("Args", (), {"docs": str(docs), "delivery": "DLV-002"})), 0)
        delivery_git.publish_execution_plan(project, "DLV-002")
        self.assertEqual(delivery_git.claim_items(project, "DLV-002")["claims"], ["AUTH-02"])

    def test_reservation_refuses_a_fence_it_cannot_take_over(self):
        """A reservation takes over only an idle open Fence that carries the approved Governance
        while no other Delivery or Slot holds it, and never while its own Integration ref exists.
        Each other state refuses with its reason before any ref moves (#315)."""
        project, docs = self.two_story_project()
        for delivery, slug, story in (("DLV-001", "auth", "AUTH-01"), ("DLV-002", "session", "AUTH-02")):
            self.scope_delivery(docs, delivery, slug, story)
        first = delivery_git.reserve_delivery(project, "DLV-001")
        refs = delivery_git.canonical_refs("DLV-002")
        other = delivery_git.canonical_refs("DLV-001")["integration"]
        # A merged Delivery drops its Integration ref and leaves the idle Fence.
        delivery_git.atomic_push(project, "origin", [(other, first["integration"], "")])
        _ref, idle, values = delivery_git._fence_context(project, "origin")
        target, intent = first["target"], "sha256:" + "1" * 64
        busy = "reservation requires an idle open Fence, not one with "
        carrier = {"Target-Update-Attempt": delivery_git.epoch_token(), "Target-Repository": "upstream",
                   "Target-Carrier-Kind": "direct_target", "Target-Carrier-Ref": "refs/heads/main",
                   "Target-Carrier-Object": "direct", "Target-Carrier-Head": target, "Target-Carrier-Base": target}
        for fence, held, finding in (
            ({"Mode": "governance"}, {}, ("DELIVERY_REF_COLLISION", busy + "Mode governance")),
            ({"Barrier-Kind": "plan-revision", "Barrier-Epoch": delivery_git.epoch_token()}, {},
             ("DELIVERY_REF_COLLISION", busy + "Barrier-Kind plan-revision")),
            ({"Source-Intent": intent}, {}, ("DELIVERY_REF_COLLISION", busy + "Source-Intent " + intent)),
            ({"Target-Update-Intent": intent, **carrier}, {},
             ("DELIVERY_REF_COLLISION", busy + "Target-Update-Intent " + intent)),
            ({}, {other: first["integration"]},
             ("DELIVERY_REF_COLLISION", "another Delivery or Slot holds the Fence: agentrof/deliveries/dlv-001")),
            ({}, {"refs/heads/agentrof/slots/001": target},
             ("DELIVERY_REF_COLLISION", "another Delivery or Slot holds the Fence: agentrof/slots/001")),
            ({"Governance-Hash": "sha256:" + "2" * 64}, {},
             ("DELIVERY_FENCE_GOVERNANCE", "the Fence does not carry the approved Governance; "
                                           "apply it with apply-governance before reserving")),
            ({}, {refs["integration"]: target}, ("DELIVERY_REF_COLLISION", "reservation requires an absent Integration ref")),
        ):
            with self.subTest(finding=finding[1]):
                held = dict(held)
                if fence:
                    held[refs["fence"]] = delivery_git._fence_child(project, idle, {**values, **fence}, "Hold the Fence")
                # Each held ref goes back to what it held before: the idle Fence, or absent.
                resting = {ref: idle if ref == refs["fence"] else "" for ref in held}
                delivery_git.atomic_push(project, "origin", [(ref, resting[ref], oid) for ref, oid in held.items()])
                try:
                    before = delivery_git.run_git(project, "ls-remote", "origin")
                    self.assertEqual(self.refused_finding(lambda: delivery_git.reserve_delivery(project, "DLV-002")),
                                     finding)
                    self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), before)
                finally:
                    delivery_git.atomic_push(project, "origin", [(ref, oid, resting[ref]) for ref, oid in held.items()])

    def test_item_start_names_apply_governance_after_a_governance_revision(self):
        """An approved Governance the Fence does not carry yet refuses activation with its remedy (#317)."""
        project, docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        args = type("Args", (), {"docs": str(docs)})
        self.assertEqual(delivery_governance.begin_revision(args), 0)
        governance = delivery_governance.path_for(docs)
        props, body = delivery_governance.read(governance)
        props["max_parallel"] += 1
        governance.write_text(delivery_governance.render(props, body), encoding="utf-8")
        self.assertEqual(delivery_governance.approve(args), 0)
        _ref, _fence, values = delivery_git._fence_context(project, "origin")
        self.assertNotEqual(values["Governance-Hash"], delivery_git.governed_governance_hash(project))
        before = delivery_git.run_git(project, "ls-remote", "origin")
        self.assertEqual(self.refused_finding(lambda: delivery_git.start_item(project, "DLV-001", "AUTH-01")), (
            "DELIVERY_FENCE_GOVERNANCE", "the Fence does not carry the approved Governance; "
                                         "apply it with apply-governance before Item activation"))
        self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), before)
        self.assertIsNone(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"))
        self.assertFalse(delivery_git.worktree_paths(project, "DLV-001", "AUTH-01")["item"].exists())

    def test_candidate_map_excludes_unpublished_local_governance(self):
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
        docs = project / "workspace/docs"
        governance = delivery_governance.path_for(docs)
        relative_governance = governance.relative_to(project).as_posix()
        original = governance.read_bytes()
        head = delivery_git.run_git(project, "rev-parse", "HEAD")
        index = (project / ".git/index").read_bytes()
        governance.unlink()
        base = delivery_git.commit_tree(project, head, [relative_governance], "Candidate before Governance", {})
        governance.write_bytes(original)
        args = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(args), 0)
        dod = docs / "delivery/definition-of-done.md"
        delivery_compile.render_map(docs)
        self.assertIn("delivery/governance/governance|Governance", (docs / "maps/delivery.md").read_text(encoding="utf-8"))
        candidate = delivery_git.commit_tree(project, base, [dod.relative_to(project).as_posix()],
            "Publish candidate Definition of Done", {}, delivery_projections=True)
        candidate_map = delivery_git.run_git(project, "show", candidate + ":workspace/docs/maps/delivery.md")
        self.assertIn("[[delivery/definition-of-done|Definition of Done]]", candidate_map)
        self.assertNotIn("delivery/governance/governance", candidate_map)
        self.assertEqual(delivery_git.run_git(project, "ls-tree", candidate, "--", relative_governance), "")
        self.assertEqual(delivery_git.delivery_projection_changes(project, candidate), {})
        self.assertEqual(governance.read_bytes(), original)
        self.assertEqual(delivery_git.run_git(project, "rev-parse", "HEAD"), head)
        self.assertEqual((project / ".git/index").read_bytes(), index)
        self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), head)

    def test_integration_publication_projects_the_direct_map_render(self):
        """The Integration carries the map its own tree renders, ending with one newline."""
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
        docs = project / "workspace/docs"
        make_approved_backlog(docs)
        dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(dod), 0)
        self.assertEqual(delivery_compile.approve_dod(dod), 0)
        init = type("Args", (), {"docs": str(docs), "id": None, "slug": None,
                                 "goal": "SAML authentication", "outcome": None,
                                 "target_branch": "main", "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        self.assertEqual(delivery_compile.approve_scope(
            type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})), 0)
        delivery_git.run_git(project, "add", "workspace")
        delivery_git.run_git(project, "commit", "-qm", "Approve scope")
        delivery_git.run_git(project, "push", "-q")
        integration = delivery_git.reserve_delivery(project, "DLV-001")["integration"]
        with tempfile.TemporaryDirectory() as clone_root:
            clone = Path(clone_root) / "checkout"
            delivery_git.run_git(project, "clone", "-q", str(project / "remote.git"), str(clone))
            delivery_git.run_git(clone, "checkout", "-q", "--detach", integration)
            map_path = clone / "workspace/docs/maps/delivery.md"
            # Text mode folds native CRLF from checkout or render, so the ending check holds on every OS.
            published = map_path.read_text(encoding="utf-8")
            delivery_compile.render_map(clone / "workspace/docs")
            self.assertEqual(map_path.read_text(encoding="utf-8"), published)
        self.assertIn("|DLV-001]]", published)
        self.assertTrue(published.endswith("\n") and not published.endswith("\n\n"), published[-60:])

    def test_publications_render_exact_candidate_without_local_sibling_or_dirty_note(self):
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
        docs = project / "workspace/docs"
        make_approved_backlog(docs)
        dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(dod), 0)
        self.assertEqual(delivery_compile.approve_dod(dod), 0)
        self.approve_verification_contract(docs)
        dirty = docs / "research/notes/local-note.md"
        dirty.parent.mkdir(parents=True)
        dirty.write_text("# Local note\n\nCommitted content.\n", encoding="utf-8")
        stale_catalog = docs / "maps/_relations/obsolete/relations-001.md"
        stale_catalog.parent.mkdir(parents=True)
        stale_catalog.write_text(vault_check.RELATION_CATALOG_MARKER + "\nOld catalog.\n", encoding="utf-8")
        delivery_git.run_git(project, "add", "workspace")
        delivery_git.run_git(project, "commit", "-qm", "Approve source inputs")
        delivery_git.run_git(project, "push", "-q")
        target = delivery_git.run_git(project, "rev-parse", "HEAD")
        story = next(docs.glob("backlog/epics/*/stories/*/story.md"))
        original_story = story.read_text(encoding="utf-8")
        init = type("Args", (), {
            "docs": str(docs), "id": None, "slug": None, "goal": "SAML authentication",
            "outcome": None, "target_branch": "main", "story": ["AUTH-01"],
        })
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_scope(scope), 0)
        directory = delivery_compile.find_delivery(docs, "DLV-001")
        approved_props, _ = delivery_compile.split_note(directory / "delivery.md")
        (directory / ".DS_Store").write_bytes(b"local operating system metadata")
        sibling = docs / "delivery/deliveries/dlv-002-unpublished/delivery.md"
        sibling.parent.mkdir(parents=True)
        sibling.write_text(delivery_compile.frontmatter({
            "type": "delivery", "id": "DLV-002", "title": "Unpublished delivery",
            "status": "scope_proposed", "derives_from": [delivery_compile.link(
                story.relative_to(docs).as_posix(), "AUTH-01")],
        }, "# Unpublished delivery\n"), encoding="utf-8")
        dirty.write_text("# Local note\n\nUnpublished edit.\n", encoding="utf-8")
        delivery_git.run_git(project, "add", dirty.relative_to(project).as_posix())
        delivery_compile.render_map(docs)
        policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
        self.assertEqual(vault_check.cmd_render_relations(
            type("Args", (), {"vault": docs}), policy), 0)

        def verify_publication(result):
            oid = result["integration"]
            self.assertEqual(delivery_git.remote_oid(
                project, "origin", delivery_git.canonical_refs("DLV-001")["integration"]), oid)
            with tempfile.TemporaryDirectory() as clone_root:
                clone = Path(clone_root) / "checkout"
                delivery_git.run_git(project, "clone", "-q", str(project / "remote.git"), str(clone))
                delivery_git.run_git(clone, "checkout", "-q", "--detach", oid)
                published = clone / "workspace/docs"
                findings = []
                vault_check.check_relation_projections(vault_check.build_vault(published, policy), findings)
                self.assertEqual(findings, [])
                self.assertFalse((published / sibling.relative_to(docs)).exists())
                self.assertFalse((published / stale_catalog.relative_to(docs)).exists())
                self.assertFalse((published / directory.relative_to(docs) / ".DS_Store").exists())
                self.assertNotIn("DLV-002", (published / "maps/delivery.md").read_text(encoding="utf-8"))
                self.assertNotIn("Unpublished delivery", (published / "maps/_generated/cross-subtree-matrix.md").read_text(encoding="utf-8"))
                self.assertEqual((published / dirty.relative_to(docs)).read_text(encoding="utf-8"),
                                 "# Local note\n\nCommitted content.\n")
                published_story = (published / story.relative_to(docs)).read_text(encoding="utf-8")
                self.assertIn(directory.name, vault_check.relation_block(published_story))
                self.assertEqual(delivery_compile.without_generated_relations(published_story),
                                 delivery_compile.without_generated_relations(original_story))
                props, _ = delivery_compile.split_note(published / directory.relative_to(docs) / "delivery.md")
                self.assertEqual(props["scope_hash"], approved_props["scope_hash"])
                for local_note in directory.rglob("*.md"):
                    published_note = published / local_note.relative_to(docs)
                    self.assertEqual(
                        delivery_compile.without_generated_relations(published_note.read_text(encoding="utf-8")),
                        delivery_compile.without_generated_relations(local_note.read_text(encoding="utf-8")),
                    )
                self.assertEqual(delivery_git.delivery_projection_changes(clone, oid), {})

        for verb in (delivery_git.reserve_delivery, delivery_git.revise_unclaimed_scope,
                     delivery_git.publish_execution_plan):
            with self.subTest(verb=verb.__name__):
                if verb is delivery_git.publish_execution_plan:
                    self.author_execution_topology(docs)
                    self.assertEqual(delivery_compile.approve_execution(scope), 0)
                local_before = {path: path.read_bytes() for path in docs.rglob("*") if path.is_file()}
                index_before = (project / ".git/index").read_bytes()
                result = verb(project, "DLV-001")
                verify_publication(result)
                self.assertEqual({path: path.read_bytes() for path in docs.rglob("*") if path.is_file()}, local_before)
                self.assertEqual((project / ".git/index").read_bytes(), index_before)
                self.assertEqual(delivery_git.run_git(project, "rev-parse", "HEAD"), target)

    def prepare_execution_with_draft_reserved_contracts(self, runtime=True, path_claim="src/auth.py", architecture=False, legacy_operation_receipts=False,
                                                        extra_path_claims=(), *, use_cache=True):
        key = (runtime, path_claim, architecture, legacy_operation_receipts, tuple(extra_path_claims))
        caches = receipts = None
        if use_cache and self.fixture_cache_context_unchanged():
            caches, receipts = _EXECUTION_FIXTURE_CACHES, _EXECUTION_FIXTURE_RECEIPTS
        elif use_cache and _WINDOWS_PIPE_FIXTURE_CONTEXT is not None:
            context = _WINDOWS_PIPE_FIXTURE_CONTEXT
            if context["base_run"] is _NATIVE_SUBPROCESS_RUN and self.fixture_cache_context_unchanged(context["runner"]):
                caches, receipts = context["caches"], context["receipts"]
        if caches is not None:
            cache = caches.setdefault(key, PreStartFixtureCache())

            def builder():
                temporary, project, docs, _directory, _item, reserved = self.build_execution_fixture(*key)
                receipts[key] = json.dumps(reserved)
                return temporary, project, docs

            temporary, project, docs = cache.copy(builder)
            directory = delivery_compile.find_delivery(docs, "DLV-001")
            item = directory / "items/auth-01/item.md"
            reserved = json.loads(receipts[key])
        else:
            temporary, project, docs, directory, item, reserved = self.build_execution_fixture(*key)
        self.addCleanup(remove_temporary, temporary)
        return project, docs, directory, item, reserved

    def build_execution_fixture(self, runtime=True, path_claim="src/auth.py", architecture=False, legacy_operation_receipts=False,
                                extra_path_claims=()):
        temporary, project = self.make_project()
        try:
            docs = project / "workspace/docs"
            make_approved_backlog(docs)
            if architecture:
                catalog = docs / "solution-design/_generated/component-catalog.json"
                catalog.parent.mkdir(parents=True, exist_ok=True)
                catalog.write_text(json.dumps({"components": [
                    {"component_id": "api", "sourcing": "build", "code_path": "src/auth.py"},
                    {"component_id": "other", "sourcing": "build", "code_path": "src/other.py"},
                ]}), encoding="utf-8")
                # The architecture stub derives from these components by canonical alias;
                # the relation contract can only resolve an alias that a solution-component
                # note owns, and integration now regenerates the projections that check it.
                for component_id in ("api", "other"):
                    note = docs / "solution-design/components" / component_id / "component.md"
                    note.parent.mkdir(parents=True, exist_ok=True)
                    note.write_text(delivery_compile.frontmatter(
                        {"type": "solution-component", "title": component_id.title() + " component",
                         "component_id": component_id, "component_class": "application", "sourcing": "build",
                         "derives_from": ["[[solution-design/landscape|Solution Landscape]]"],
                         "tags": ["doc/solution-component"]},
                        f"# {component_id.title()} component\n\nFixture component.\n"), encoding="utf-8")
                import landscape_check
                landscape = docs / "solution-design/landscape.md"
                props, body = delivery_compile.split_note(landscape)
                landscape.write_text(delivery_compile.frontmatter(props, body), encoding="utf-8")
                props["package_hash"] = landscape_check.package_hash(landscape.parent)
                landscape.write_text(delivery_compile.frontmatter(props, body), encoding="utf-8")
            dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
            self.assertEqual(delivery_compile.init_dod(dod), 0)
            self.assertEqual(delivery_compile.approve_dod(dod), 0)
            for kind in ("verification", "environment"):
                args = type("Args", (), {"docs": str(docs), "kind": kind,
                                        "constrained_by": ["[[solution-design/decisions/fixture-api|Fixture API]]"]})
                self.assertEqual(operation_compile.init(args), 0)
            delivery_git.run_git(project, "add", "workspace")
            delivery_git.run_git(project, "commit", "-qm", "Approve sources with draft Operation contracts")
            delivery_git.run_git(project, "push", "-q")
            init = type("Args", (), {"docs": str(docs), "id": None, "slug": "auth", "goal": "Authenticate",
                                     "outcome": None, "target_branch": "main", "story": ["AUTH-01"]})
            self.assertEqual(delivery_compile.init_delivery(init), 0)
            args = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
            self.assertEqual(delivery_compile.approve_scope(args), 0)
            reserved = delivery_git.reserve_delivery(project, "DLV-001")
            # Approve only what the Item pins: publication refuses an approved contract no
            # Item pins while the Integration holds another revision of it.
            pinned = [("verification", "test_command")] + ([("environment", "env_command")] if runtime else [])
            for kind, command in pinned:
                path = operation_compile.contract_path(docs, kind)
                props, body = operation_compile.parse(path)
                props[command] = "make test" if kind == "verification" else "make env"
                operation_compile.atomic_text(path, operation_compile.render(props, body))
                self.assertEqual(operation_compile.approve(type("Args", (), {"docs": str(docs), "kind": kind})), 0)
                if legacy_operation_receipts:
                    props, authored = operation_compile.parse(path)
                    view = {key: value for key, value in props.items()
                            if key not in {"source_hash", "approved_at_utc"}}
                    props["source_hash"] = "sha256:" + hashlib.sha256(json.dumps(
                        {"frontmatter": view, "body": authored + "\n"}, ensure_ascii=False,
                        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                    block = vault_check.RELATION_START + "\n\nHistorical generated inverse\n\n" + vault_check.RELATION_END
                    path.write_text(vault_check.replace_relation_block(operation_compile.render(props, authored), block), encoding="utf-8")
            self.author_execution_topology(docs)
            directory = delivery_compile.find_delivery(docs, "DLV-001")
            item = directory / "items/auth-01/item.md"
            props, body = delivery_compile.split_note(item)
            props["runtime_required"] = runtime
            props["path_claims"] = [path_claim, *extra_path_claims]
            if architecture:
                props.update({"architecture_impact": "required", "architecture_components": ["api"],
                              "architecture_record_kinds": ["system-architecture", "architecture-component", "interface-contract"],
                              "architecture_reason": "Define the authentication interface."})
                sources, _snapshot, errors = delivery_compile.approved_backlog_sources(docs, ["AUTH-01"])
                self.assertEqual(errors, [])
                props["role_sequence"] = delivery_compile.execution_roles(sources["AUTH-01"], True)
            delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
            self.assertEqual(delivery_compile.approve_execution(args), 0)
            return temporary, project, docs, directory, item, reserved
        except BaseException:
            remove_temporary(temporary)
            raise

    def prepare_stamped_architecture_item(self, before_publish=None, extra_path_claims=()):
        project, docs, directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(
            runtime=False, architecture=True, extra_path_claims=extra_path_claims)
        if before_publish is not None:
            before_publish(project)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        worktree = Path(active["worktree"])
        item = worktree / directory.relative_to(project) / "items/auth-01/item.md"
        # Text mode folds native CRLF from checkout or render, so the body check holds on every OS.
        original = item.read_text(encoding="utf-8")
        before, before_body = delivery_compile.split_note(item)
        active_docs = worktree / "workspace/docs"
        self.assertEqual(architecture_compile.init_root(active_docs, "AUTH-01"), 0)
        self.assertEqual(architecture_compile.init_component(active_docs, "api", "AUTH-01"), 0)
        self.assertEqual(architecture_compile.stub(active_docs, "interface", "api", "IFC-001", "auth", "AUTH-01"), 0)
        self.assertEqual(architecture_compile.stamp_item(active_docs, "AUTH-01"), 0)
        props, body = delivery_compile.split_note(item)
        self.assertEqual(props["item_plan_hash"], before["item_plan_hash"])
        self.assertEqual(props["source_hash"], delivery_compile.content_hash(props, body))
        self.assertEqual(body, before_body)
        self.assertEqual(item.read_text(encoding="utf-8").split("\n---\n", 1)[1], original.split("\n---\n", 1)[1])
        self.assertEqual({key: value for key, value in props.items() if key not in {"source_hash", "architecture_delta_hash"}},
                         {key: value for key, value in before.items() if key not in {"source_hash", "architecture_delta_hash"}})
        (worktree / "src").mkdir()
        (worktree / "src/auth.py").write_text("def authenticate():\n    return 'approved'\n", encoding="utf-8")
        delivery_git.run_git(worktree, "add", "workspace/docs", "src")
        delivery_git.run_git(worktree, "commit", "-qm", "Implement authentication with sealed Architecture")
        return project, worktree, item, active

    @windows_text_pipes()
    def test_architecture_stamp_authored_evidence_push_and_integration(self):
        # Under the runner's text pipes the activated Item record must keep the LF the
        # stamp check splits its frontmatter at (#247).
        project, worktree, item, active = self.prepare_stamped_architecture_item()
        product = delivery_git.run_git(worktree, "rev-parse", "HEAD")
        authored = {}
        for name, report in (("code-review.md", "Reviewed authentication interface and rejected unauthorized input."),
                             ("verification.md", "Executed authentication success and missing-credential tests; both passed.")):
            path = item.parent / name
            props, body = delivery_compile.split_note(path)
            body += "\n\n" + report + "\n"
            path.write_text(delivery_compile.frontmatter(props, body), encoding="utf-8")
            authored[name] = body.rstrip()
        self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
        pushed = delivery_git.push_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(pushed["product_tip"], product)
        self.assertEqual(delivery_git.run_git(project, "rev-parse", pushed["item"] + "^"), product)
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        for name, body in authored.items():
            relative = (item.parent / name).relative_to(worktree).as_posix()
            props, actual = delivery_git.split_remote_note(project, integrated["integration"], relative, delivery_compile.split_note)
            self.assertEqual(actual, body)
            self.assertEqual(props["source_hash"], delivery_compile.content_hash(props, actual))
            self.assertEqual(props["reviewed_commit" if name == "code-review.md" else "verified_commit"], product)
        props, body = delivery_git.split_remote_note(project, integrated["integration"], item.relative_to(worktree).as_posix(), delivery_compile.split_note)
        self.assertEqual(props["status"], "integrated")
        self.assertTrue(props["architecture_delta_hash"].startswith("sha256:"))
        self.assertEqual(props["source_hash"], delivery_compile.content_hash(props, body))
        self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})
        self.assertFalse(worktree.exists())

    def test_integration_merges_item_deletions_and_republished_evidence_drafts(self):
        integration_ref = "refs/heads/agentrof/deliveries/dlv-001"

        def carry_legacy_file(project):
            (project / "notes").mkdir()
            (project / "notes/legacy.txt").write_text("carried from the target\n", encoding="utf-8")
            base = delivery_git.remote_oid(project, "origin", integration_ref)
            candidate = delivery_git.commit_tree(project, base, ["notes/legacy.txt"], "Carry a legacy file", {})
            delivery_git.atomic_push(project, "origin", [(integration_ref, base, candidate)])

        project, worktree, item, active = self.prepare_stamped_architecture_item(
            before_publish=carry_legacy_file, extra_path_claims=("notes/legacy.txt",))
        # The Item removes a file the base carried, and claims it: a trivial resolution
        # any merge makes, which a plain three-way read left unmerged.
        self.assertTrue((worktree / "notes/legacy.txt").is_file())
        delivery_git.run_git(worktree, "rm", "-q", "notes/legacy.txt")
        delivery_git.run_git(worktree, "commit", "-qm", "Retire the legacy file")
        product = delivery_git.run_git(worktree, "rev-parse", "HEAD")
        # Meanwhile the Integration re-projected the Item's evidence drafts, as a plan
        # publication does; the sealed Item's own records must win that conflict.
        relative_reports = [(item.parent / name).relative_to(worktree).as_posix()
                            for name in ("code-review.md", "verification.md")]
        for relative in relative_reports:
            path = project / relative
            props, body = delivery_compile.split_note(path)
            path.write_text(delivery_compile.frontmatter(props, body + "\nRe-projected draft.\n"), encoding="utf-8")
        base = delivery_git.remote_oid(project, "origin", integration_ref)
        republished = delivery_git.commit_tree(project, base, relative_reports, "Republish evidence drafts", {},
                                               delivery_projections=True)
        delivery_git.atomic_push(project, "origin", [(integration_ref, base, republished)])
        authored = {}
        for name, report in (("code-review.md", "Reviewed the retirement of the legacy file."),
                             ("verification.md", "Executed the suite without the legacy file; it passed.")):
            path = item.parent / name
            props, body = delivery_compile.split_note(path)
            body += "\n\n" + report + "\n"
            path.write_text(delivery_compile.frontmatter(props, body), encoding="utf-8")
            authored[name] = body.rstrip()
        self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        tree = delivery_git.run_git(project, "ls-tree", "-r", "--name-only", integrated["integration"]).splitlines()
        self.assertNotIn("notes/legacy.txt", tree)
        self.assertIn("src/auth.py", tree)
        for name, body in authored.items():
            relative = (item.parent / name).relative_to(worktree).as_posix()
            props, actual = delivery_git.split_remote_note(project, integrated["integration"], relative, delivery_compile.split_note)
            self.assertEqual(actual, body)
            self.assertEqual(props["reviewed_commit" if name == "code-review.md" else "verified_commit"], product)
        props, body = delivery_git.split_remote_note(project, integrated["integration"], item.relative_to(worktree).as_posix(), delivery_compile.split_note)
        self.assertEqual(props["status"], "integrated")
        self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})

    def test_architecture_push_rejects_control_and_receipt_tampering(self):
        project, worktree, item, active = self.prepare_stamped_architecture_item()
        clean = delivery_git.run_git(worktree, "rev-parse", "HEAD")
        baseline = delivery_git.run_git(project, "ls-remote", "origin")
        package = item.parents[2]
        def mutate_note(path, key=None, value=None):
            props, body = delivery_compile.split_note(path)
            if key:
                props[key] = value
            else:
                body += "\nUnapproved change.\n"
            props["source_hash"] = delivery_compile.content_hash(props, body)
            path.write_text(delivery_compile.frontmatter(props, body), encoding="utf-8")
        mutations = {
            "body": lambda: mutate_note(item),
            "status": lambda: mutate_note(item, "status", "paused"),
            "owner": lambda: mutate_note(item, "owner_role", "frontend_developer"),
            "path_claim": lambda: mutate_note(item, "path_claims", ["src/other.py"]),
            "item_plan": lambda: mutate_note(item, "item_plan_hash", "sha256:" + "0" * 64),
            "story_pin": lambda: mutate_note(item, "story_source_hash", "sha256:" + "0" * 64),
            "operation_pin": lambda: mutate_note(item, "verification_contract_hash", "sha256:" + "0" * 64),
            "scope": lambda: mutate_note(package / "delivery.md"),
            "plan": lambda: mutate_note(package / "execution-plan.md"),
            "committed_evidence": lambda: mutate_note(item.parent / "code-review.md"),
            "new_item": lambda: (package / "items/extra").mkdir(),
            "deleted_evidence": lambda: (item.parent / "verification.md").unlink(),
            "non_markdown": lambda: (package / "extra\ncontrol.json").write_text("{}"),
            # Native Windows files carry no executable bit, so the index records the
            # mode there; a POSIX checkout records it from the file as well.
            "mode": lambda: (item.chmod(0o755), delivery_git.run_git(
                worktree, "update-index", "--chmod=+x", item.relative_to(worktree).as_posix())),
            "symlink": lambda: (item.unlink(), self.symlink_or_skip(item, "code-review.md")),
            "missing_hash": lambda: mutate_note(item, "architecture_delta_hash", "none"),
            "wrong_hash": lambda: mutate_note(item, "architecture_delta_hash", "sha256:" + "0" * 64),
            "source_hash": lambda: item.write_text(item.read_text().replace("source_hash: sha256:", "source_hash: broken:")),
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label):
                if label == "non_markdown" and os.name == "nt":
                    self.skipTest("POSIX opaque filename contract: native Windows refuses a newline in a file name")
                delivery_git.run_git(worktree, "reset", "--hard", clean)
                delivery_git.run_git(worktree, "clean", "-fd")
                mutation()
                if label == "new_item":
                    (package / "items/extra/item.md").write_bytes(item.read_bytes())
                delivery_git.run_git(worktree, "add", "workspace/docs")
                delivery_git.run_git(worktree, "commit", "-qm", "Tamper with control")
                # Independent report writers can produce receipts, but publication
                # must still reject changes to the authoritative Item controls.
                if label not in {"status", "deleted_evidence", "symlink"}:
                    self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
                with self.assertRaises(RuntimeError):
                    delivery_git.push_item(project, "DLV-001", "AUTH-01")
                self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), baseline)

    def test_evidence_authoring_rejects_product_index_drift_and_unsafe_reports(self):
        import os
        project, worktree, item, active = self.prepare_stamped_architecture_item()
        clean = delivery_git.run_git(worktree, "rev-parse", "HEAD")
        review = item.parent / "code-review.md"
        for label in ("product", "staged_product", "sibling", "leading_space", "symlink", "hardlink", "mode"):
            with self.subTest(label=label):
                delivery_git.run_git(worktree, "reset", "--hard", clean)
                delivery_git.run_git(worktree, "clean", "-fd")
                review.write_text(review.read_text() + "\nReviewed the authenticated entrypoint.\n")
                if label in {"product", "staged_product"}:
                    product = worktree / "src/auth.py"
                    content = product.read_bytes()
                    product.write_text("unreviewed change\n")
                    if label == "staged_product":
                        delivery_git.run_git(worktree, "add", "src/auth.py")
                        product.write_bytes(content)
                elif label == "sibling":
                    (item.parents[2] / "execution-plan.md").write_text("unapproved plan\n")
                elif label == "leading_space":
                    lookalike = worktree / (" " + review.relative_to(worktree).as_posix())
                    lookalike.parent.mkdir(parents=True)
                    lookalike.write_text("untracked report lookalike\n")
                elif label == "symlink":
                    review.unlink(); self.symlink_or_skip(review, worktree / "src/auth.py")
                elif label == "hardlink":
                    os.link(review, project / "report-hardlink.md")
                else:
                    review.chmod(0o755)
                    if not review.stat().st_mode & 0o111:
                        self.skipTest("fixture filesystem has no executable mode")
                before = review.read_bytes()
                self.assertNotEqual(self.approve_item_evidence(str(worktree)), 0)
                self.assertEqual(review.read_bytes(), before)
        delivery_git.run_git(worktree, "reset", "--hard", clean)
        delivery_git.run_git(worktree, "clean", "-fd")
        self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
        review.write_text(review.read_text() + "\nChanged after approval.\n")
        with self.assertRaisesRegex(RuntimeError, "source_hash is stale"):
            delivery_git.push_item(project, "DLV-001", "AUTH-01")
        for flag, target in ((flag, target) for flag in ("assume-unchanged", "skip-worktree")
                             for target in ("item", "product")):
            with self.subTest(index_flag=flag, target=target):
                delivery_git.run_git(worktree, "reset", "--hard", clean)
                path = item if target == "item" else worktree / "src/auth.py"
                relative = path.relative_to(worktree).as_posix()
                delivery_git.run_git(worktree, "update-index", "--" + flag, relative)
                if target == "item":
                    props, body = delivery_compile.split_note(item)
                    props["architecture_impact"] = "not_applicable"
                    props["owner_role"] = "frontend_developer"
                    item.write_text(delivery_compile.frontmatter(props, body))
                else:
                    path.write_text("def authenticate():\n    return 'untested'\n")
                review.write_text(review.read_text() + "\nReviewed the authentication interface.\n")
                before_reports = {name: (item.parent / name).read_bytes()
                                  for name in ("code-review.md", "verification.md")}
                before_index = delivery_git.run_git(worktree, "ls-files", "-v", "-z")
                self.assertNotEqual(self.approve_item_evidence(str(worktree)), 0)
                baseline = delivery_git.run_git(project, "ls-remote", "origin")
                with self.assertRaisesRegex(RuntimeError, "index flags hide tracked paths"):
                    delivery_git.push_item(project, "DLV-001", "AUTH-01")
                self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), baseline)
                self.assertEqual(delivery_git.run_git(worktree, "ls-files", "-v", "-z"), before_index)
                self.assertEqual({name: (item.parent / name).read_bytes() for name in before_reports}, before_reports)
                delivery_git.run_git(worktree, "update-index", "--no-" + flag, relative)

    def test_architecture_push_rejects_unsealed_stale_and_outside_claim_delta(self):
        project, worktree, item, active = self.prepare_stamped_architecture_item()
        clean = delivery_git.run_git(worktree, "rev-parse", "HEAD")
        baseline = delivery_git.run_git(project, "ls-remote", "origin")
        architecture = worktree / "workspace/docs/system-architecture"
        delta_path = architecture / "_ledger/item-deltas/AUTH-01.json"
        record = architecture / "components/api/interfaces/auth/interface.md"
        for label in ("missing_delta", "stale_record", "missing_seal", "unsealed_forgery", "unclaimed_component", "unclaimed_kind", "symlink_record"):
            with self.subTest(label=label):
                delivery_git.run_git(worktree, "reset", "--hard", clean)
                delivery_git.run_git(worktree, "clean", "-fd")
                if label == "missing_delta":
                    delta_path.unlink()
                elif label == "missing_seal":
                    (architecture / "_ledger/records/IFC-001/r1.json").unlink()
                elif label == "symlink_record":
                    record.unlink(); self.symlink_or_skip(record, "../../../component.md")
                elif label == "stale_record":
                    record.write_text(record.read_text() + "\nChanged after seal.\n")
                else:
                    if label == "unclaimed_component":
                        self.assertEqual(architecture_compile.init_component(worktree / "workspace/docs", "other", "AUTH-01"), 0)
                        architecture_compile.seal_record(architecture, architecture / "components/other/component.md", "AUTH-01")
                    elif label == "unclaimed_kind":
                        self.assertEqual(architecture_compile.stub(worktree / "workspace/docs", "runtime", "api", "RUN-001", "runtime", "AUTH-01"), 0)
                        architecture_compile.seal_record(architecture, architecture / "components/api/runtime/runtime/runtime.md", "AUTH-01")
                    else:
                        record.write_text(record.read_text().replace("revision_state: sealed", "revision_state: draft"))
                    delta = architecture_compile.item_delta(architecture, "AUTH-01")
                    digest = architecture_compile.item_delta_hash(delta)
                    delta_path.write_text(json.dumps({**delta, "architecture_delta_hash": digest}))
                    props, body = delivery_compile.split_note(item)
                    props["architecture_delta_hash"] = digest
                    props["source_hash"] = delivery_compile.content_hash(props, body)
                    item.write_text(delivery_compile.frontmatter(props, body))
                delivery_git.run_git(worktree, "add", "workspace/docs")
                delivery_git.run_git(worktree, "commit", "-qm", "Tamper with Architecture receipt")
                if label == "symlink_record":
                    before_reports = {name: (item.parent / name).read_bytes()
                                      for name in ("code-review.md", "verification.md")}
                    self.assertNotEqual(self.approve_item_evidence(str(worktree)), 0)
                    self.assertEqual({name: (item.parent / name).read_bytes() for name in before_reports}, before_reports)
                else:
                    self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
                    with self.assertRaisesRegex(RuntimeError, "[Aa]rchitecture"):
                        delivery_git.push_item(project, "DLV-001", "AUTH-01")
                self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), baseline)

    def republish_integration_plan(self, project, worktree, item):
        """Republish the active Item's plan on the Integration, as a plan revision does."""
        integration_ref = "refs/heads/agentrof/deliveries/dlv-001"
        package = item.parents[2]
        relative = {name: path.relative_to(worktree).as_posix() for name, path in (
            ("plan", package / "execution-plan.md"), ("scope", package / "delivery.md"), ("item", item))}
        base = delivery_git.remote_oid(project, "origin", integration_ref)
        for name, path in relative.items():
            props, body = delivery_git.split_remote_note(project, base, path, delivery_compile.split_note)
            body += "\n\nRepublished for the revised plan.\n"
            props["source_hash"] = delivery_compile.content_hash(props, body)
            (project / path).write_text(delivery_compile.frontmatter(props, body), encoding="utf-8")
        republished = delivery_git.commit_tree(project, base, list(relative.values()), "Publish execution plan", {},
                                               delivery_projections=True)
        delivery_git.atomic_push(project, "origin", [(integration_ref, base, republished)])
        return republished, relative

    def converge_on_integration(self, worktree, item, integration, relative, *, merge=True, base=None):
        """Take the Integration into the Item as its writer does: its controls, its plan, a new base."""
        mine, _ = delivery_compile.split_note(item)
        if merge:
            subprocess.run(["git", "-C", str(worktree), "merge", "-q", "--no-ff", "--no-commit", integration],
                           capture_output=True, check=False)
        for name in ("plan", "scope"):
            (worktree / relative[name]).write_bytes(subprocess.run(
                ["git", "-C", str(worktree), "show", f"{integration}:{relative[name]}"],
                check=True, capture_output=True).stdout)
        published, body = delivery_git.split_remote_note(worktree, integration, relative["item"], delivery_compile.split_note)
        converged = dict(published)
        for key in ("status", "tags", "architecture_delta_hash"):
            converged[key] = mine[key]
        converged["integration_base_commit"] = base or integration
        converged["source_hash"] = delivery_compile.content_hash(converged, body)
        # LF on every host, as the Delivery writers write a record: under setup's -text rule a
        # text-mode write on native Windows would commit CRLF, which push-item refuses.
        item.write_bytes(delivery_compile.frontmatter(converged, body).encode("utf-8"))
        delivery_git.run_git(worktree, "add", "-A", "workspace/docs")
        delivery_git.run_git(worktree, "commit", "-qm", "Take the republished Integration")
        return delivery_git.run_git(worktree, "rev-parse", "HEAD")

    def test_push_accepts_an_item_converged_on_its_republished_integration(self):
        """A target refresh leaves an Item that carries work to its writer. The
        writer takes the Integration, its republished plan and the Item's new base;
        publication accepts exactly that and integration seals it."""
        project, worktree, item, active = self.prepare_stamped_architecture_item()
        integration, relative = self.republish_integration_plan(project, worktree, item)
        product = self.converge_on_integration(worktree, item, integration, relative)
        self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
        pushed = delivery_git.push_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(pushed["product_tip"], product)
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        for name in ("plan", "scope"):
            _props, body = delivery_git.split_remote_note(project, integrated["integration"], relative[name],
                                                          delivery_compile.split_note)
            self.assertIn("Republished for the revised plan.", body)
        props, body = delivery_git.split_remote_note(project, integrated["integration"], relative["item"],
                                                     delivery_compile.split_note)
        self.assertEqual(props["status"], "integrated")
        self.assertIn("Republished for the revised plan.", body)
        self.assertEqual(props["source_hash"], delivery_compile.content_hash(props, body))

    def test_push_item_refuses_before_any_ref_moves_when_its_candidate_would_change_the_worktree(self):
        """push-item republishes the Item record with LF and moves the Item worktree to that
        candidate after the push. A converged record committed with CRLF, as a text-mode write on
        native Windows commits it under setup's -text rule, differs from the candidate, so
        push-item refuses before the atomic push: no ref moves, and the worktree keeps its commit
        and its evidence (#247)."""
        integration_ref = delivery_git.canonical_refs("DLV-001")["integration"]

        def carry_managed_rule(project):
            (project / ".gitattributes").write_bytes(
                (setup_check.managed_attributes_block("workspace") + "\n").encode("utf-8"))
            base = delivery_git.remote_oid(project, "origin", integration_ref)
            carried = delivery_git.commit_tree(project, base, [".gitattributes"], "Carry the managed checkout rule", {})
            delivery_git.atomic_push(project, "origin", [(integration_ref, base, carried)])

        project, worktree, item, _active = self.prepare_stamped_architecture_item(before_publish=carry_managed_rule)
        integration, relative = self.republish_integration_plan(project, worktree, item)
        self.converge_on_integration(worktree, item, integration, relative)
        item.write_bytes(item.read_bytes().replace(b"\n", b"\r\n"))
        delivery_git.run_git(worktree, "add", relative["item"])
        delivery_git.run_git(worktree, "commit", "-qm", "Save the converged record with CRLF")
        self.assertIn(b"\r\n", subprocess.run(["git", "-C", str(worktree), "cat-file", "blob", "HEAD:" + relative["item"]],
                                              capture_output=True, check=True).stdout)
        self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
        head = delivery_git.run_git(worktree, "rev-parse", "HEAD")
        pending = delivery_git.worktree_pending_paths(project, worktree)
        before = delivery_git.run_git(project, "ls-remote", "origin")
        with self.assertRaises(RuntimeError) as refused:
            delivery_git.push_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), before)
        self.assertEqual(delivery_git.run_git(worktree, "rev-parse", "HEAD"), head)
        self.assertEqual(delivery_git.worktree_pending_paths(project, worktree), pending)
        self.assertEqual(str(refused.exception), "DELIVERY_WORKTREE_UNSAFE: the Item candidate does not contain the "
                                                 "current worktree bytes of " + relative["item"])

    def test_push_refuses_what_a_converged_item_does_not_carry(self):
        project, worktree, item, active = self.prepare_stamped_architecture_item()
        clean = delivery_git.run_git(worktree, "rev-parse", "HEAD")
        integration, relative = self.republish_integration_plan(project, worktree, item)
        baseline = delivery_git.run_git(project, "ls-remote", "origin")

        def edit_note(path, key=None, value=None):
            props, body = delivery_compile.split_note(path)
            if key:
                props[key] = value
            else:
                body += "\nUnapproved change.\n"
            props["source_hash"] = delivery_compile.content_hash(props, body)
            path.write_text(delivery_compile.frontmatter(props, body), encoding="utf-8")
            delivery_git.run_git(worktree, "commit", "-qam", "Change a control after converging")

        beyond = "beyond its Architecture stamp and its converged Integration"
        base_rule = "integration base only forward"
        variants = {
            "plan_beyond_integration": (lambda: edit_note(worktree / relative["plan"]), "may not edit Delivery control"),
            "item_field_beyond_integration": (lambda: edit_note(item, "owner_role", "frontend_developer"), beyond),
            "item_body_beyond_integration": (lambda: edit_note(item), beyond),
            "lifecycle": (lambda: edit_note(item, "status", "paused"), beyond),
            "base_not_taken": (None, base_rule),
            "base_off_integration": (None, base_rule),
            "base_kept": (None, "may not edit Delivery control"),
        }
        for label, (tamper, refusal) in variants.items():
            with self.subTest(label=label):
                delivery_git.run_git(worktree, "reset", "--hard", clean)
                delivery_git.run_git(worktree, "clean", "-fd")
                if label == "base_not_taken":
                    self.converge_on_integration(worktree, item, integration, relative, merge=False)
                elif label == "base_off_integration":
                    self.converge_on_integration(worktree, item, integration, relative, base=clean)
                elif label == "base_kept":
                    previous, _ = delivery_compile.split_note(item)
                    self.converge_on_integration(worktree, item, integration, relative,
                                                 base=previous["integration_base_commit"])
                else:
                    self.converge_on_integration(worktree, item, integration, relative)
                    tamper()
                if label != "lifecycle":
                    self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
                with self.assertRaisesRegex(RuntimeError, refusal):
                    delivery_git.push_item(project, "DLV-001", "AUTH-01")
                self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), baseline)

    def governance_target_handoff(self, project, docs, extra_paths=()):
        args = type("Args", (), {"docs": str(docs)})
        self.assertEqual(delivery_governance.begin_revision(args), 0)
        governance = delivery_governance.path_for(docs)
        props, body = delivery_governance.read(governance)
        props["max_parallel"] += 1
        governance.write_text(delivery_governance.render(props, body), encoding="utf-8")
        self.assertEqual(delivery_governance.approve(args), 0)
        desired = delivery_governance.status(docs)[0]["governance_hash"]
        delivery_git.begin_applying_governance(project, desired)
        _branch, baseline = delivery_git.resolve_target(project, "origin")
        candidate = delivery_git.commit_tree(project, baseline,
            [governance.relative_to(project).as_posix(), *extra_paths], "Publish Governance", {},
            delivery_projections=True)
        carrier = "refs/heads/governance-input"
        delivery_git.atomic_push(project, "origin", [(carrier, "", candidate)])
        delivery_git.authorize_target_update(project, "governance", desired, "origin",
            "direct_target", carrier, "direct", candidate, baseline, "upstream")
        delivery_git.apply_target_update(project, "governance")
        finished = delivery_git.finish_source_handoff(project)
        self.assertEqual(finished["target"], candidate)
        return candidate, finished["fence"]

    def test_governance_handoff_refreshes_each_integration_and_preserves_published_contracts(self):
        project, docs, directory, _item, reserved = self.prepare_execution_with_draft_reserved_contracts()
        first = delivery_git.publish_execution_plan(project, "DLV-001")
        init = type("Args", (), {"docs": str(docs), "id": "DLV-002", "slug": "second", "goal": "Second delivery",
                                 "outcome": None, "target_branch": "main", "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-002"})
        self.assertEqual(delivery_compile.approve_scope(scope), 0)
        second_dir = delivery_compile.find_delivery(docs, "DLV-002")
        second_ref = delivery_git.canonical_refs("DLV-002")["integration"]
        second_base = delivery_git.commit_tree(project, reserved["target"],
            delivery_git.package_paths(project, second_dir, docs, include_map=False),
            "Reserve second Delivery", {"Record": "delivery-reservation-v1", "Protocol": "1", "Delivery": "DLV-002"},
            delivery_projections=True)
        delivery_git.atomic_push(project, "origin", [(second_ref, "", second_base)])
        self.author_execution_topology(docs, "DLV-002")
        # This Integration also holds the target's draft, so its Item pins the approved
        # Environment Contract as DLV-001's does; publication carries no other revision.
        second_item = second_dir / "items/auth-01/item.md"
        props, body = delivery_compile.split_note(second_item)
        delivery_compile.atomic_text(second_item, delivery_compile.frontmatter({**props, "runtime_required": True}, body))
        self.assertEqual(delivery_compile.approve_execution(scope), 0)
        second = delivery_git.publish_execution_plan(project, "DLV-002")
        target, fence = self.governance_target_handoff(project, docs)
        self.assertFalse(delivery_git.is_ancestor(project, target, first["integration"]))
        self.assertFalse(delivery_git.is_ancestor(project, target, second["integration"]))
        with self.assertRaisesRegex(RuntimeError, "Integration does not contain"):
            delivery_git.claim_items(project, "DLV-001")
        self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})
        self.assertIsNone(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"))
        self.assertFalse(delivery_git.remote_has_ref(project, "origin", delivery_git.canonical_refs("DLV-001", "AUTH-01")["item"]))
        for ident, before, package in (("DLV-001", first["integration"], directory),
                                        ("DLV-002", second["integration"], second_dir)):
            refreshed = delivery_git.refresh_target(project, ident)
            self.assertTrue(refreshed["changed"])
            self.assertFalse(refreshed["plan_invalidated"])
            self.assertTrue(delivery_git.is_ancestor(project, target, refreshed["integration"]))
            self.assertEqual(refreshed["previous_target"], reserved["target"])
            for relative in ((package / "delivery.md").relative_to(project).as_posix(),
                             "workspace/docs/operation/verification-contract.md"):
                self.assertEqual(delivery_git.run_git(project, "show", before + ":" + relative),
                                 delivery_git.run_git(project, "show", refreshed["integration"] + ":" + relative))
            with tempfile.TemporaryDirectory() as temporary:
                clone = Path(temporary) / "checkout"
                delivery_git.run_git(project, "clone", "-q", str(project / "remote.git"), str(clone))
                delivery_git.run_git(clone, "checkout", "-q", "--detach", refreshed["integration"])
                candidate_docs = clone / "workspace/docs"
                candidate_package = candidate_docs / package.relative_to(docs)
                self.assertEqual(delivery_compile.delivery_findings(candidate_docs, ident)[1], [])
                for path in candidate_package.rglob("*.md"):
                    relative = path.relative_to(clone).as_posix()
                    previous = delivery_git.run_git(project, "show", before + ":" + relative) + "\n"
                    self.assertEqual(delivery_compile.without_generated_relations(path.read_text(encoding="utf-8")),
                                     delivery_compile.without_generated_relations(previous))
                map_text = (candidate_docs / "maps/delivery.md").read_text(encoding="utf-8")
                self.assertIn("[[delivery/governance/governance|Governance]]", map_text)
                self.assertIn(ident, map_text)
                self.assertEqual(delivery_git.delivery_projection_changes(clone, refreshed["integration"]), {})
                vault = vault_check.build_vault(candidate_docs, vault_check.load_policy(vault_check.DEFAULT_POLICY))
                findings = []
                for check in (vault_check.check_frontmatter_props, vault_check.check_nav_footer,
                              vault_check.check_wikilink_resolution, vault_check.check_orphans,
                              vault_check.check_map_coverage, vault_check.check_relation_contract,
                              vault_check.check_relation_projections):
                    check(vault, findings)
                self.assertEqual([finding for finding in findings if finding.path.startswith("delivery/")
                                  or finding.path == "maps/delivery.md" or finding.check == "generated_views"], [])
                self.assertEqual(delivery_git.run_git(clone, "status", "--porcelain"), "")
            again = delivery_git.refresh_target(project, ident)
            self.assertFalse(again["changed"])
        self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), target)

    def test_target_refresh_rejects_conflicting_authored_delivery_content(self):
        project, docs, directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        published = delivery_git.publish_execution_plan(project, "DLV-001")
        delivery = directory / "delivery.md"
        props, body = delivery_compile.split_note(delivery)
        body += "\nUnapproved target scope change.\n"
        props["source_hash"] = delivery_compile.content_hash(props, body)
        delivery.write_text(delivery_compile.frontmatter(props, body), encoding="utf-8")
        _target, fence = self.governance_target_handoff(project, docs,
            delivery_git.package_paths(project, directory, docs, include_map=False))
        with self.assertRaisesRegex(RuntimeError, "unmerged"):
            delivery_git.refresh_target(project, "DLV-001")
        refs = delivery_git.canonical_refs("DLV-001")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), published["integration"])
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), fence)

    @windows_text_pipes()
    def test_refresh_preserves_approved_control_content_and_path_set(self):
        # The runner's text pipes must leave the Integration's projection-only change
        # a projection-only change, or the merge cannot reconcile it (#247).
        for control in ("delivery.md", "execution-plan.md", "items/auth-01/item.md", "injected_item", "deleted_item"):
            with self.subTest(control=control):
                project, _docs, directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
                published = delivery_git.publish_execution_plan(project, "DLV-001")
                base = published["integration"]
                # Both branches descend from the same already-approved package.
                previous = delivery_git.remote_oid(project, "origin", "refs/heads/main")
                delivery_git.atomic_push(project, "origin", [("refs/heads/main", previous, base)])
                delivery_git.refresh_target(project, "DLV-001")
                refs = delivery_git.canonical_refs("DLV-001")
                integration = delivery_git.remote_oid(project, "origin", refs["integration"])
                path = directory / control if control.endswith(".md") else item
                relative = path.relative_to(project).as_posix()
                original = delivery_git.run_git(project, "show", base + ":" + relative) + "\n"
                ours = vault_check.replace_relation_block(original,
                    vault_check.RELATION_START + "\n\nIntegration view\n\n" + vault_check.RELATION_END)
                integration = delivery_git.commit_replacements(project, integration, {relative: ours}, "Integration projection", {})
                delivery_git.atomic_push(project, "origin", [(refs["integration"],
                    delivery_git.remote_oid(project, "origin", refs["integration"]), integration)])
                if control == "injected_item":
                    path = directory / "items/auth-02/item.md"
                    path.parent.mkdir()
                    path.write_text("# Injected Item\n", encoding="utf-8")
                elif control == "deleted_item":
                    path.unlink()
                else:
                    theirs = vault_check.replace_relation_block(original,
                        vault_check.RELATION_START + "\n\nTarget view\n\n" + vault_check.RELATION_END)
                    path.write_text(theirs + "\nTarget-only authored control change.\n", encoding="utf-8")
                target = delivery_git.commit_tree(project, base, [path.relative_to(project).as_posix()], "Target control drift", {})
                delivery_git.atomic_push(project, "origin", [("refs/heads/main", base, target)])
                before = delivery_git.run_git(project, "ls-remote", "origin")
                with self.assertRaisesRegex(RuntimeError, "selected Delivery control content or path set"):
                    delivery_git.refresh_target(project, "DLV-001")
                self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), before)
                self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})
                self.assertIsNone(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"))

    def test_projection_merge_rejects_structural_conflicts_at_owned_generated_paths(self):
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
        head = delivery_git.run_git(project, "rev-parse", "HEAD")
        for relative in ("workspace/docs/maps/delivery.md", "workspace/docs/maps/_relations/test/relations-001.md"):
            with self.subTest(path=relative):
                path = project / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Base generated view\n", encoding="utf-8")
                base = delivery_git.commit_tree(project, head, [relative], "Base projection", {})
                path.write_text("Integration generated view\n", encoding="utf-8")
                ours = delivery_git.commit_tree(project, base, [relative], "Integration projection", {})
                path.unlink()
                self.symlink_or_skip(path, "unrelated-target")
                theirs = delivery_git.commit_tree(project, base, [relative], "Target symlink", {})
                with self.assertRaisesRegex(RuntimeError, "unmerged"):
                    delivery_git.merge_candidate(project, ours, theirs, "Reject structural conflict", {}, delivery_projections=True)
                self.assertTrue(path.is_symlink())
        self.assertEqual(delivery_git.run_git(project, "rev-parse", "HEAD"), head)
        self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), head)

    def test_projection_merge_resolves_only_owned_inverse_blocks(self):
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
        head = delivery_git.run_git(project, "rev-parse", "HEAD")
        index = (project / ".git/index").read_bytes()
        note = project / "workspace/docs/research/notes/merge.md"
        note.parent.mkdir(parents=True)
        authored = "# Authored note\n\nStable content.\n"

        def content(projection, body=authored):
            return body + "\n" + vault_check.RELATION_START + "\n\n" + projection + "\n\n" + vault_check.RELATION_END + "\n"

        relative = note.relative_to(project).as_posix()
        note.write_text(content("Base view"), encoding="utf-8")
        base = delivery_git.commit_tree(project, head, [relative], "Base note", {})
        note.write_text(content("Integration view"), encoding="utf-8")
        ours = delivery_git.commit_tree(project, base, [relative], "Integration view", {})
        note.write_text(content("Target view"), encoding="utf-8")
        theirs = delivery_git.commit_tree(project, base, [relative], "Target view", {})
        with self.assertRaisesRegex(RuntimeError, "unmerged"):
            delivery_git.merge_candidate(project, ours, theirs, "Ordinary merge", {})
        merged = delivery_git.merge_candidate(project, ours, theirs, "Projection merge", {}, delivery_projections=True)
        self.assertEqual(delivery_git.run_git(project, "show", merged + ":" + relative) + "\n", authored)
        self.assertEqual(delivery_git.delivery_projection_changes(project, merged), {})
        note.write_text(content("Integration view", "# Authored note\n\nIntegration edit.\n"), encoding="utf-8")
        authored_ours = delivery_git.commit_tree(project, base, [relative], "Integration authored edit", {})
        note.write_text(content("Target view", "# Authored note\n\nTarget edit.\n"), encoding="utf-8")
        authored_theirs = delivery_git.commit_tree(project, base, [relative], "Target authored edit", {})
        with self.assertRaisesRegex(RuntimeError, "unmerged"):
            delivery_git.merge_candidate(project, authored_ours, authored_theirs, "Reject authored conflict", {}, delivery_projections=True)
        for malformed in (content("Target view").replace(vault_check.RELATION_END, ""),
                          content("Target view") + vault_check.RELATION_START + "\n"):
            note.write_text(malformed, encoding="utf-8")
            bad = delivery_git.commit_tree(project, base, [relative], "Malformed projection markers", {})
            with self.assertRaisesRegex(RuntimeError, "unmerged"):
                delivery_git.merge_candidate(project, ours, bad, "Reject malformed markers", {}, delivery_projections=True)
        foreign = lambda text: text.replace("relations:generated", "structural:generated")
        note.write_text(foreign(content("Base contents")), encoding="utf-8")
        foreign_base = delivery_git.commit_tree(project, head, [relative], "Base structural contents", {})
        note.write_text(foreign(content("Integration contents")), encoding="utf-8")
        foreign_ours = delivery_git.commit_tree(project, foreign_base, [relative], "Integration structural contents", {})
        note.write_text(foreign(content("Target contents")), encoding="utf-8")
        foreign_theirs = delivery_git.commit_tree(project, foreign_base, [relative], "Target structural contents", {})
        with self.assertRaisesRegex(RuntimeError, "unmerged"):
            delivery_git.merge_candidate(project, foreign_ours, foreign_theirs, "Reject unowned markers", {}, delivery_projections=True)
        artifact = project / "workspace/docs/experience-design/artifacts/prototype.md"
        artifact.parent.mkdir(parents=True)
        relative_artifact = artifact.relative_to(project).as_posix()
        artifact.write_text(content("Base prototype content"), encoding="utf-8")
        artifact_base = delivery_git.commit_tree(project, head, [relative_artifact], "Base prototype", {})
        artifact.write_text(content("Integration prototype content"), encoding="utf-8")
        artifact_ours = delivery_git.commit_tree(project, artifact_base, [relative_artifact], "Integration prototype", {})
        artifact.write_text(content("Target prototype content"), encoding="utf-8")
        artifact_theirs = delivery_git.commit_tree(project, artifact_base, [relative_artifact], "Target prototype", {})
        with self.assertRaisesRegex(RuntimeError, "unmerged"):
            delivery_git.merge_candidate(project, artifact_ours, artifact_theirs, "Reject prototype conflict", {}, delivery_projections=True)
        self.assertEqual(delivery_git.run_git(project, "rev-parse", "HEAD"), head)
        self.assertEqual((project / ".git/index").read_bytes(), index)
        self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), head)

    def test_target_refresh_reissues_an_untouched_claim_against_the_new_integration(self):
        """A claim carrying no work must not strand its Item behind the target."""
        project, docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        refs = delivery_git.canonical_refs("DLV-001", "AUTH-01")
        before = delivery_git.remote_oid(project, "origin", refs["item"])
        self.governance_target_handoff(project, docs)
        delivery_git.refresh_target(project, "DLV-001")
        after = delivery_git.remote_oid(project, "origin", refs["item"])
        self.assertNotEqual(after, before)
        self.assertEqual(
            delivery_git.trailer(delivery_git.commit_message(project, after), "Record"),
            "item-claim-v1",
        )
        integration = delivery_git.remote_oid(
            project, "origin", delivery_git.canonical_refs("DLV-001")["integration"])
        self.assertTrue(delivery_git.is_ancestor(project, integration, after))
        started = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        self.assertTrue(Path(started["worktree"]).is_dir())

    def test_target_refresh_recovers_a_claim_stranded_by_an_earlier_refresh(self):
        """Convergence must not depend on the target having moved this time."""
        project, docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        refs = delivery_git.canonical_refs("DLV-001", "AUTH-01")
        stranded = delivery_git.remote_oid(project, "origin", refs["item"])
        self.governance_target_handoff(project, docs)
        delivery_git.refresh_target(project, "DLV-001")
        refreshed = delivery_git.remote_oid(project, "origin", refs["item"])

        # Put the claim back where a pre-repair refresh would have left it.
        delivery_git.atomic_push(project, "origin", [(refs["item"], refreshed, stranded)])
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["item"]), stranded)
        again = delivery_git.refresh_target(project, "DLV-001")
        self.assertTrue(again["changed"])
        self.assertEqual(again["claims_refreshed"], [refs["item"]])
        recovered = delivery_git.remote_oid(project, "origin", refs["item"])
        self.assertNotEqual(recovered, stranded)
        started = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        self.assertTrue(Path(started["worktree"]).is_dir())

        # A Delivery with nothing left behind reports no change.
        settled = delivery_git.refresh_target(project, "DLV-001")
        self.assertFalse(settled["changed"])

    def test_activation_carries_the_currently_published_plan_into_the_item(self):
        project, docs, _directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        started = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        relative_item = str(item.relative_to(project))
        contract = docs / "operation/verification-contract.md"
        relative_contract = str(contract.relative_to(project))
        first, _body = delivery_compile.split_note(Path(started["worktree"]) / relative_item)
        self.assertEqual(first["path_claims"], ["src/auth.py"])
        self.assertEqual(operation_compile.parse(Path(started["worktree"]) / relative_contract)[0]["revision"], 1)
        delivery_git.pause_item(project, "DLV-001", "AUTH-01")

        kind = type("Args", (), {"docs": str(docs), "kind": "verification"})
        self.assertEqual(operation_compile.revise(kind), 0)
        props, body = operation_compile.parse(contract)
        operation_compile.atomic_text(contract, operation_compile.render(props, body + "\n\nA later approved revision.\n"))
        self.assertEqual(operation_compile.approve(kind), 0)
        revised = operation_compile.parse(contract)[0]
        props, body = delivery_compile.split_note(item)
        props["path_claims"] = ["src/auth.py", "src/session.py"]
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
        args = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_execution(args), 0)
        delivery_git.publish_execution_plan(project, "DLV-001")

        worktree = Path(delivery_git.resume_item(project, "DLV-001", "AUTH-01")["worktree"])
        current, _body = delivery_compile.split_note(worktree / relative_item)
        self.assertEqual(current["path_claims"], ["src/auth.py", "src/session.py"])
        self.assertEqual(current["verification_contract_hash"], revised["source_hash"])
        self.assertEqual(current["status"], "active")
        published = operation_compile.parse(worktree / relative_contract)[0]
        self.assertEqual(published["revision"], 2)
        self.assertEqual(published["source_hash"], revised["source_hash"])

    def test_stale_paused_item_cannot_activate_after_integration_refresh(self):
        project, docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        delivery_git.start_item(project, "DLV-001", "AUTH-01")
        paused = delivery_git.pause_item(project, "DLV-001", "AUTH-01")
        self.governance_target_handoff(project, docs)
        with self.assertRaisesRegex(RuntimeError, "Integration does not contain"):
            delivery_git.resume_item(project, "DLV-001", "AUTH-01")
        delivery_git.refresh_target(project, "DLV-001")
        for action in (delivery_git.start_item, delivery_git.resume_item):
            with self.assertRaisesRegex(RuntimeError, "Item does not contain"):
                action(project, "DLV-001", "AUTH-01")
        refs = delivery_git.canonical_refs("DLV-001", "AUTH-01")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["item"]), paused["item"])
        self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})
        self.assertIsNone(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"))

    def test_stale_active_item_takeover_preserves_existing_slot_and_receipt(self):
        project, docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        receipt = delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01")
        slots = delivery_git.remote_slot_oids(project, "origin")
        self.governance_target_handoff(project, docs)
        with self.assertRaisesRegex(RuntimeError, "Integration does not contain"):
            delivery_git.takeover_item(project, "DLV-001", "AUTH-01", confirm=True)
        delivery_git.refresh_target(project, "DLV-001")
        with self.assertRaisesRegex(RuntimeError, "Item does not contain"):
            delivery_git.takeover_item(project, "DLV-001", "AUTH-01", confirm=True)
        self.assertEqual(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"), receipt)
        self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), slots)
        self.assertTrue(Path(active["worktree"]).is_dir())

    def test_target_refresh_rejects_changed_descendant_of_claimed_directory(self):
        project, docs, _directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False, "src")
        delivery_git.publish_execution_plan(project, "DLV-001")
        claimed = delivery_git.claim_items(project, "DLV-001")
        source = project / "src/new.py"
        source.parent.mkdir()
        source.write_text("new_target_code = True\n", encoding="utf-8")
        _target, fence = self.governance_target_handoff(project, docs, ["src/new.py"])
        item.unlink()  # Local cache loss must not hide the authoritative remote claim.
        with self.assertRaisesRegex(RuntimeError, r"^DELIVERY_TARGET_SOURCE_VIOLATION: target changed claimed paths src/new\.py$"):
            delivery_git.refresh_target(project, "DLV-001")
        refs = delivery_git.canonical_refs("DLV-001")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), claimed["integration"])
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), fence)

    def test_target_refresh_rejects_a_changed_claimed_path_outside_ascii(self):
        """A Git listing without -z quotes a name outside ASCII, and the quoted name overlaps no
        claim, so the refresh merged a target change to a claimed path unseen (#276)."""
        project, docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(
            False, "src/giriş")
        delivery_git.publish_execution_plan(project, "DLV-001")
        claimed = delivery_git.claim_items(project, "DLV-001")
        source = project / "src/giriş/kayıt_doğrula.py"
        source.parent.mkdir(parents=True)
        source.write_text("parola = 'şğı'\n", encoding="utf-8")
        _target, fence = self.governance_target_handoff(project, docs, ["src/giriş/kayıt_doğrula.py"])
        with self.assertRaisesRegex(
                RuntimeError, r"^DELIVERY_TARGET_SOURCE_VIOLATION: target changed claimed paths src/giriş/kayıt_doğrula\.py$"):
            delivery_git.refresh_target(project, "DLV-001")
        refs = delivery_git.canonical_refs("DLV-001")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), claimed["integration"])
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), fence)

    def test_target_refresh_rejects_a_target_rename_of_a_claimed_path(self):
        """Git diff detects renames by default and then lists only the new path, so a target that
        moved a claimed src/auth.py to src/login.py refreshed without the refusal (#277)."""
        project, _refreshed, claimed = self.claim_item_on_target(
            {"src/auth.py": "def authenticate():\n    return 'v0'\n"}, ["src/auth.py"])
        refs = delivery_git.canonical_refs("DLV-001")
        fence = delivery_git.remote_oid(project, "origin", refs["fence"])
        delivery_git.run_git(project, "mv", "src/auth.py", "src/login.py")
        delivery_git.run_git(project, "commit", "-qm", "Rename the authentication module")
        delivery_git.run_git(project, "push", "-q")
        with self.assertRaisesRegex(RuntimeError, r"^DELIVERY_TARGET_SOURCE_VIOLATION: target changed claimed paths src/auth\.py$"):
            delivery_git.refresh_target(project, "DLV-001")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), claimed["integration"])
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), fence)

    def test_target_refresh_accepts_planned_story_generated_only_changes(self):
        project, docs, _directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        published = delivery_git.publish_execution_plan(project, "DLV-001")
        item_props, _body = delivery_compile.split_note(item)
        story = docs / item_props["story_path"]
        relative = story.relative_to(project).as_posix()
        published_story = delivery_git.run_git(project, "show", published["integration"] + ":" + relative) + "\n"
        self.assertIn("status: planned", published_story)
        self.assertNotEqual(story.read_text(encoding="utf-8"), published_story)
        story.write_text(published_story, encoding="utf-8")
        target, _fence = self.governance_target_handoff(project, docs, [relative])
        refreshed = delivery_git.refresh_target(project, "DLV-001")
        self.assertFalse(refreshed["plan_invalidated"])
        self.assertTrue(delivery_git.is_ancestor(project, target, refreshed["integration"]))
        self.assertEqual(delivery_git.run_git(project, "show", refreshed["integration"] + ":" + relative) + "\n", published_story)

    def test_target_refresh_rejects_changed_pinned_source_and_operation_receipts(self):
        for kind in ("story", "operation", "dod", "missing_dod"):
            with self.subTest(kind=kind):
                project, docs, _directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
                published = delivery_git.publish_execution_plan(project, "DLV-001")
                props, _body = delivery_compile.split_note(item)
                path = (docs / props["story_path"] if kind == "story" else
                        docs / "delivery/definition-of-done.md" if kind in {"dod", "missing_dod"} else
                        operation_compile.contract_path(docs, "verification"))
                if kind == "missing_dod":
                    path.unlink()
                else:
                    path.write_text(path.read_text(encoding="utf-8") + "\nChanged approved input.\n", encoding="utf-8")
                _target, fence = self.governance_target_handoff(project, docs, [path.relative_to(project).as_posix()])
                item.unlink()  # The remote pin must remain enforced without local Item files.
                with self.assertRaises(RuntimeError) as failure:
                    delivery_git.refresh_target(project, "DLV-001")
                if kind != "missing_dod":
                    self.assertIn("changed a pinned source or Operation receipt", str(failure.exception))
                refs = delivery_git.canonical_refs("DLV-001")
                self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), published["integration"])
                self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), fence)

    def test_target_refresh_preserves_legacy_operation_pins_after_relation_rendering(self):
        project, docs, directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(
            runtime=True, legacy_operation_receipts=True)
        approved = {kind: operation_compile.parse(operation_compile.contract_path(docs, kind))[0]
                    for kind in ("verification", "environment")}
        pinned_item, _body = delivery_compile.split_note(item)
        published = delivery_git.publish_execution_plan(project, "DLV-001")
        paths = [operation_compile.contract_path(docs, kind).relative_to(project).as_posix()
                 for kind in approved]
        # Owning candidate rendering removes the old generated block and regenerates
        # projections; the target consumer must still verify each original pin.
        target, _fence = self.governance_target_handoff(project, docs, paths)
        refreshed = delivery_git.refresh_target(project, "DLV-001")
        self.assertFalse(refreshed["plan_invalidated"])
        self.assertTrue(delivery_git.is_ancestor(project, target, refreshed["integration"]))
        with tempfile.TemporaryDirectory() as temporary:
            clone = Path(temporary) / "checkout"
            delivery_git.run_git(project, "clone", "-q", str(project / "remote.git"), str(clone))
            delivery_git.run_git(clone, "checkout", "-q", "--detach", refreshed["integration"])
            candidate_docs = clone / "workspace/docs"
            self.assertEqual(delivery_compile.item_operation_findings(candidate_docs, pinned_item), [])
            for kind, expected in approved.items():
                receipt, errors = operation_compile.check_contract(candidate_docs, kind)
                self.assertEqual(errors, [])
                self.assertTrue(receipt["current"])
                self.assertEqual(receipt["source_hash"], expected["source_hash"])
                actual, _body = operation_compile.parse(operation_compile.contract_path(candidate_docs, kind))
                self.assertEqual((actual["revision"], actual["approved_at_utc"]),
                                 (expected["revision"], expected["approved_at_utc"]))
                self.assertEqual(pinned_item[kind + "_contract_hash"], expected["source_hash"])
        for kind in approved:
            path = operation_compile.contract_path(docs, kind)
            path.write_text(path.read_text() + "\nChanged authored operation behavior.\n", encoding="utf-8")
        carrier = "refs/heads/governance-input"
        delivery_git.atomic_push(project, "origin", [(carrier, delivery_git.remote_oid(project, "origin", carrier), "")])
        self.governance_target_handoff(project, docs, paths)
        refs_before = delivery_git.run_git(project, "ls-remote", "origin")
        with self.assertRaisesRegex(RuntimeError, "changed a pinned source or Operation receipt"):
            delivery_git.refresh_target(project, "DLV-001")
        self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), refs_before)

    def test_stale_integrated_item_reopens_on_the_refreshed_integration(self):
        project, docs, _directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        self.commit_item_product_change(active["worktree"], "def authenticate():\n    return True\n")
        self.assertEqual(self.approve_item_evidence(active["worktree"]), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        target, _fence = self.governance_target_handoff(project, docs)
        refs = delivery_git.canonical_refs("DLV-001", "AUTH-01")
        with self.assertRaisesRegex(RuntimeError, "Integration does not contain"):
            delivery_git.reopen_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["item"]), integrated["item"])
        self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})
        self.assertIsNone(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"))
        delivery_git.refresh_target(project, "DLV-001")
        integration_oid = delivery_git.remote_oid(project, "origin", refs["integration"])
        self.assertFalse(delivery_git.is_ancestor(project, target, integrated["item"]))
        reopened = delivery_git.reopen_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(reopened["status"], "active")
        self.assertTrue(delivery_git.is_ancestor(project, target, reopened["item"]))
        self.assertEqual(
            delivery_git.run_git(project, "rev-list", "--parents", "-n", "1", reopened["item"]).split()[1:],
            [integrated["item"], integration_oid],
        )
        message = delivery_git.commit_message(project, reopened["item"])
        self.assertEqual(delivery_git.trailer(message, "Record"), "item-reopen-v1")
        self.assertEqual(delivery_git.trailer(message, "Previous-Tip"), integrated["item"])
        self.assertEqual(delivery_git.trailer(message, "Integration-Base"), integration_oid)
        self.assertEqual(
            delivery_git.run_git(project, "diff", "--name-only", integration_oid, reopened["item"]).splitlines(),
            [Path(item).relative_to(Path(project)).as_posix()],
        )
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["item"]), reopened["item"])
        self.assertEqual(set(delivery_git.remote_slot_oids(project, "origin").values()), {reopened["item"]})
        self.assertTrue(Path(reopened["worktree"]).is_dir())
        second_product = self.commit_item_product_change(reopened["worktree"], "def authenticate():\n    return 'v2'\n")
        self.assertEqual(self.approve_item_evidence(reopened["worktree"]), 0)
        pushed = delivery_git.push_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(pushed["product_tip"], second_product)
        integrated_again = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(
            subprocess.run(
                ["git", "show", f"{integrated_again['integration']}:src/auth.py"],
                cwd=project, check=True, capture_output=True, text=True,
            ).stdout,
            "def authenticate():\n    return 'v2'\n",
        )

    def test_fence_writers_carry_an_active_plan_revision_barrier(self):
        project, _docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        fence_ref = delivery_git.canonical_refs("DLV-001")["fence"]

        begun = delivery_git.begin_plan_revision(project, "DLV-001")
        self.assertEqual(begun["barrier_kind"], "plan-revision")

        for writer in (
            lambda: delivery_git.publish_execution_plan(project, "DLV-001"),
            lambda: delivery_git.claim_items(project, "DLV-001"),
        ):
            writer()
            carried = delivery_git.commit_message(
                project, delivery_git.remote_oid(project, "origin", fence_ref))
            self.assertEqual(delivery_git.trailer(carried, "Barrier-Kind"), "plan-revision")
            self.assertEqual(delivery_git.trailer(carried, "Barrier-Epoch"), begun["barrier_epoch"])

        delivery_git.finish_plan_revision(project, "DLV-001")
        released = delivery_git.commit_message(
            project, delivery_git.remote_oid(project, "origin", fence_ref))
        self.assertEqual(delivery_git.trailer(released, "Barrier-Kind"), "none")
        self.assertEqual(delivery_git.trailer(released, "Barrier-Epoch"), "none")

    def test_sealed_item_reopens_after_reapproval_names_it_for_rebinding(self):
        project, docs, directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        self.commit_item_product_change(active["worktree"], "def authenticate():\n    return True\n")
        self.assertEqual(self.approve_item_evidence(active["worktree"]), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        # The plan revision compiles the tracked package as the Integration sealed it.
        for name in ("item.md", "code-review.md", "verification.md"):
            relative = (directory / "items/auth-01" / name).relative_to(project).as_posix()
            props, body = delivery_git.split_remote_note(project, integrated["integration"], relative, delivery_compile.split_note)
            delivery_compile.atomic_text(project / relative, delivery_compile.frontmatter(props, body))
        sealed, _body = delivery_compile.split_note(item)
        self.assertEqual(sealed["status"], "integrated")
        first = sealed["verification_contract_hash"]

        kind = type("Args", (), {"docs": str(docs), "kind": "verification"})
        self.assertEqual(operation_compile.revise(kind), 0)
        contract = docs / "operation/verification-contract.md"
        props, body = operation_compile.parse(contract)
        operation_compile.atomic_text(contract, operation_compile.render(props, body + "\n\nA later approved revision.\n"))
        self.assertEqual(operation_compile.approve(kind), 0)
        second = operation_compile.parse(contract)[0]["source_hash"]
        self.assertNotEqual(second, first)
        with self.assertRaisesRegex(RuntimeError, "Operation Contract bindings are invalid"):
            delivery_git.reopen_item(project, "DLV-001", "AUTH-01")

        # Re-approval alone keeps the sealed binding, so the drift still blocks reopen.
        args = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_execution(args), 0)
        self.assertEqual(delivery_compile.split_note(item)[0]["verification_contract_hash"], first)
        delivery_git.publish_execution_plan(project, "DLV-001")
        with self.assertRaisesRegex(RuntimeError, "Operation Contract bindings are invalid"):
            delivery_git.reopen_item(project, "DLV-001", "AUTH-01")

        named = type("Args", (), {"docs": str(docs), "delivery": "DLV-001", "reopen": ["AUTH-01"]})
        self.assertEqual(delivery_compile.approve_execution(named), 0)
        rebound, _body = delivery_compile.split_note(item)
        self.assertEqual(rebound["status"], "integrated")
        self.assertEqual(rebound["verification_contract_hash"], second)
        published = delivery_git.publish_execution_plan(project, "DLV-001")
        relative_item = item.relative_to(project).as_posix()
        remote, _body = delivery_git.split_remote_note(project, published["integration"], relative_item, delivery_compile.split_note)
        self.assertEqual(remote["status"], "integrated")
        self.assertEqual(remote["verification_contract_hash"], second)

        reopened = delivery_git.reopen_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(reopened["status"], "active")
        current, _body = delivery_compile.split_note(Path(reopened["worktree"]) / relative_item)
        self.assertEqual(current["verification_contract_hash"], second)
        self.assertEqual(current["item_plan_hash"], rebound["item_plan_hash"])

    def test_activation_target_race_quiesces_before_writer_receipt_or_worktree(self):
        for action in ("start", "resume", "reopen", "takeover"):
            with self.subTest(action=action):
                project, _docs, _directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
                delivery_git.publish_execution_plan(project, "DLV-001")
                delivery_git.claim_items(project, "DLV-001")
                if action != "start":
                    active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
                    if action == "resume":
                        delivery_git.pause_item(project, "DLV-001", "AUTH-01")
                    elif action == "reopen":
                        self.commit_item_product_change(active["worktree"], "def authenticate():\n    return True\n")
                        self.assertEqual(self.approve_item_evidence(active["worktree"]), 0)
                        delivery_git.push_item(project, "DLV-001", "AUTH-01")
                        delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
                original_push = delivery_git.atomic_push
                advanced = []

                def race_target(root, remote, updates):
                    if not advanced:
                        previous = delivery_git.remote_oid(root, remote, "refs/heads/main")
                        (root / "README.md").write_text("Concurrent target movement\n", encoding="utf-8")
                        target = delivery_git.commit_tree(root, previous, ["README.md"], "Advance target during activation", {})
                        original_push(root, remote, [("refs/heads/main", previous, target)])
                        advanced.append(target)
                    return original_push(root, remote, updates)

                verb = getattr(delivery_git, action + "_item")
                with mock.patch.object(delivery_git, "atomic_push", side_effect=race_target):
                    with self.assertRaisesRegex(RuntimeError, "target advanced after Item activation"):
                        verb(project, "DLV-001", "AUTH-01", **({"confirm": True} if action == "takeover" else {}))
                self.assertEqual(len(advanced), 1)
                refs = delivery_git.canonical_refs("DLV-001", "AUTH-01")
                current = delivery_git.remote_oid(project, "origin", refs["item"])
                props, _body = delivery_git.split_remote_note(project, current,
                    item.relative_to(project).as_posix(), delivery_compile.split_note)
                self.assertEqual(props["status"], "paused")
                self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})
                self.assertIsNone(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"))
                self.assertFalse(delivery_git.worktree_paths(project, "DLV-001", "AUTH-01")["item"].exists())

    def refused_under_concurrent_coordinator(self, verb, taken_slot: str = "") -> tuple[str, str]:
        """Refuse *verb* after another coordinator moves the Fence first and, when named, takes *taken_slot*."""
        original_push = delivery_git.atomic_push
        raced = []

        def race(root, remote, updates):
            if not raced:
                ref, fence, values = delivery_git._fence_context(root, remote)
                raced.append(delivery_git._fence_child(root, fence, values, "Concurrent coordinator update"))
                taken = [(taken_slot, "", raced[0])] if taken_slot else []
                original_push(root, remote, [(ref, fence, raced[0]), *taken])
            return original_push(root, remote, updates)

        with mock.patch.object(delivery_git, "atomic_push", side_effect=race):
            return self.refused_finding(verb)

    def test_rejected_activation_drops_its_pending_receipt_so_a_retry_starts(self):
        """The activation is atomic, so an unchanged Item ref proves it changed no ref, whatever the Slot holds."""
        for label, taken in (("lost Fence lease", False), ("Slot taken by a concurrent start", True)):
            with self.subTest(label=label):
                project, _docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
                delivery_git.publish_execution_plan(project, "DLV-001")
                delivery_git.claim_items(project, "DLV-001")
                refs = delivery_git.canonical_refs("DLV-001", "AUTH-01", 1)
                claimed = delivery_git.remote_oid(project, "origin", refs["item"])
                code, _message = self.refused_under_concurrent_coordinator(
                    lambda: delivery_git.start_item(project, "DLV-001", "AUTH-01"), refs["slot"] if taken else "")
                self.assertEqual(code, "DELIVERY_FENCE_LEASE_LOST")
                self.assertEqual(delivery_git.remote_oid(project, "origin", refs["item"]), claimed)
                self.assertIsNone(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"))
                if taken:
                    # The concurrent Item releases the Slot, as its pause or integration would.
                    occupant = delivery_git.remote_oid(project, "origin", refs["slot"])
                    delivery_git.atomic_push(project, "origin", [(refs["slot"], occupant, "")])
                self.assertEqual(delivery_git.remote_slot_oids(project, "origin"), {})
                started = delivery_git.start_item(project, "DLV-001", "AUTH-01")
                self.assertEqual((started["slot"], started["receipt"]["state"]), ("001", "verified"))

    def test_rejected_reopen_and_takeover_leave_the_writer_state_as_it_was(self):
        """Neither changed a ref, so the pending receipt goes; takeover gives back the receipt and worktree it replaced."""
        for action in ("reopen", "takeover"):
            with self.subTest(action=action):
                project, _docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
                delivery_git.publish_execution_plan(project, "DLV-001")
                delivery_git.claim_items(project, "DLV-001")
                active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
                if action == "reopen":
                    self.commit_item_product_change(active["worktree"], "def authenticate():\n    return True\n")
                    self.assertEqual(self.approve_item_evidence(active["worktree"]), 0)
                    delivery_git.push_item(project, "DLV-001", "AUTH-01")
                    delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
                    verb = lambda: delivery_git.reopen_item(project, "DLV-001", "AUTH-01")  # noqa: E731
                else:
                    verb = lambda: delivery_git.takeover_item(project, "DLV-001", "AUTH-01", confirm=True)  # noqa: E731
                item_ref = delivery_git.canonical_refs("DLV-001", "AUTH-01")["item"]
                tip = delivery_git.remote_oid(project, "origin", item_ref)
                receipt = delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01")
                code, _message = self.refused_under_concurrent_coordinator(verb)
                self.assertEqual(code, "DELIVERY_FENCE_LEASE_LOST")
                self.assertEqual(delivery_git.remote_oid(project, "origin", item_ref), tip)
                self.assertEqual(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"), receipt)
                worktree = delivery_git.worktree_paths(project, "DLV-001", "AUTH-01")["item"]
                if action == "takeover":
                    self.assertEqual(receipt["state"], "verified")
                    self.assertEqual(delivery_git.worktree_head(project, worktree), tip)
                else:
                    self.assertIsNone(receipt)
                    self.assertFalse(worktree.exists())
                self.assertEqual(verb()["receipt"]["state"], "verified")

    @contextlib.contextmanager
    def lost_push_response(self, after_landing=None):
        """Let the next atomic push land on the remote while its response is lost on the way back.

        *after_landing* runs once the push landed, before the lost response is reported.
        """
        run = subprocess.run
        lost = []

        def landed_without_response(command, *args, **kwargs):
            result = run(command, *args, **kwargs)
            if (not lost and isinstance(command, list) and command[:3] == ["git", "push", "--atomic"]
                    and result.returncode == 0):
                lost.append(command)
                if after_landing is not None:
                    after_landing()
                return subprocess.CompletedProcess(command, 128, "", "fatal: the remote end hung up unexpectedly")
            return result

        with mock.patch.object(subprocess, "run", side_effect=landed_without_response):
            yield lost

    def test_activation_that_landed_despite_a_lost_response_promotes_its_receipt(self):
        """The Item and Slot hold the candidate, so the activation took effect: the host keeps a verified
        receipt, and a takeover can then give it a worktree."""
        for action in ("start", "reopen", "takeover"):
            with self.subTest(action=action):
                project, _docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
                delivery_git.publish_execution_plan(project, "DLV-001")
                delivery_git.claim_items(project, "DLV-001")
                if action != "start":
                    active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
                if action == "reopen":
                    self.commit_item_product_change(active["worktree"], "def authenticate():\n    return True\n")
                    self.assertEqual(self.approve_item_evidence(active["worktree"]), 0)
                    delivery_git.push_item(project, "DLV-001", "AUTH-01")
                    delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
                verb = {"start": delivery_git.start_item, "reopen": delivery_git.reopen_item,
                        "takeover": lambda *args: delivery_git.takeover_item(*args, confirm=True)}[action]
                with self.lost_push_response() as lost:
                    with self.assertRaises(RuntimeError):
                        verb(project, "DLV-001", "AUTH-01")
                self.assertEqual(len(lost), 1)
                receipt = delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01")
                self.assertEqual(receipt["state"], "verified")
                item_ref = delivery_git.canonical_refs("DLV-001", "AUTH-01")["item"]
                self.assertEqual(delivery_git.remote_ref_oids(project, "origin", [item_ref, receipt["slot_ref"]]),
                                 {item_ref: receipt["candidate_oid"], receipt["slot_ref"]: receipt["candidate_oid"]})
                taken = delivery_git.takeover_item(project, "DLV-001", "AUTH-01", confirm=True)
                self.assertEqual(taken["receipt"]["state"], "verified")

    def test_merge_candidate_preserves_disjoint_additions_and_rejects_conflicts(self):
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
        base = delivery_git.run_git(project, "rev-parse", "HEAD")
        (project / "left.txt").write_text("Integration-only content\n", encoding="utf-8")
        left = delivery_git.commit_tree(project, base, ["left.txt"], "Left addition", {})
        (project / "right.txt").write_text("Target-only content\n", encoding="utf-8")
        right = delivery_git.commit_tree(project, base, ["right.txt"], "Right addition", {})
        index = (project / ".git/index").read_bytes()
        merged = delivery_git.merge_candidate(project, left, right, "Merge disjoint additions", {})
        self.assertEqual(delivery_git.run_git(project, "show", merged + ":left.txt"), "Integration-only content")
        self.assertEqual(delivery_git.run_git(project, "show", merged + ":right.txt"), "Target-only content")
        self.assertEqual(delivery_git.run_git(project, "show", "-s", "--format=%P", merged), left + " " + right)
        (project / "README.md").write_text("Left edit\n", encoding="utf-8")
        left_conflict = delivery_git.commit_tree(project, base, ["README.md"], "Left edit", {})
        (project / "README.md").write_text("Right edit\n", encoding="utf-8")
        right_conflict = delivery_git.commit_tree(project, base, ["README.md"], "Right edit", {})
        with self.assertRaisesRegex(RuntimeError, "unmerged"):
            delivery_git.merge_candidate(project, left_conflict, right_conflict, "Reject conflict", {})
        tree = delivery_git.run_git(project, "rev-parse", base + "^{tree}")
        orphan = delivery_git.run_git(project, "commit-tree", tree, "-m", "Unrelated history")
        with self.assertRaisesRegex(RuntimeError, "one unambiguous common base"):
            delivery_git.merge_candidate(project, left, orphan, "Reject unrelated history", {})
        self.assertEqual(delivery_git.run_git(project, "rev-parse", "HEAD"), base)
        self.assertEqual((project / ".git/index").read_bytes(), index)
        self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), base)

    def test_target_refresh_rejects_ambiguous_merge_bases_before_ref_updates(self):
        project, _docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        published = delivery_git.publish_execution_plan(project, "DLV-001")
        base = published["integration"]
        left = delivery_git.commit_tree(project, base, [], "Left history", {})
        right = delivery_git.commit_tree(project, base, [], "Right history", {})
        integration = delivery_git.merge_candidate(project, left, right, "Integration history", {})
        target = delivery_git.merge_candidate(project, right, left, "Target history", {})
        refs = delivery_git.canonical_refs("DLV-001")
        old_target = delivery_git.remote_oid(project, "origin", "refs/heads/main")
        delivery_git.atomic_push(project, "origin", [(refs["integration"], base, integration),
                                                    ("refs/heads/main", old_target, target)])
        before = delivery_git.run_git(project, "ls-remote", "origin")
        with self.assertRaisesRegex(RuntimeError, "one unambiguous"):
            delivery_git.refresh_target(project, "DLV-001")
        self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), before)

    def test_governance_refresh_keeps_integration_on_exact_fence_lease_failure(self):
        project, docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        published = delivery_git.publish_execution_plan(project, "DLV-001")
        self.governance_target_handoff(project, docs)
        original_push = delivery_git.atomic_push
        advanced = {}

        def race_fence(root, remote, updates):
            ref, fence, values = delivery_git._fence_context(root, remote)
            child = delivery_git._fence_child(root, fence, values, "Concurrent coordinator update")
            original_push(root, remote, [(ref, fence, child)])
            advanced["fence"] = child
            return original_push(root, remote, updates)

        with mock.patch.object(delivery_git, "atomic_push", side_effect=race_fence):
            with self.assertRaises(RuntimeError):
                delivery_git.refresh_target(project, "DLV-001")
        refs = delivery_git.canonical_refs("DLV-001")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), published["integration"])
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), advanced["fence"])

    def assert_git_paths_are_posix(self, arguments: list[str], project: Path, commits: list[str]) -> None:
        """No path reached Git, or a tree Git wrote, with a Windows separator."""
        self.assertEqual([value for value in arguments if "\\" in value], [])
        for commit in commits:
            names = delivery_git.run_git(project, "ls-tree", "-r", "-z", "--name-only", commit).split("\0")
            self.assertEqual([name for name in names if "\\" in name], [], commit)

    def test_coordinator_hands_git_posix_paths_on_a_host_with_backslash_separators(self):
        """Every path the coordinator hands Git, or compares with what Git
        returns, uses forward slashes on every host (#236)."""
        with windows_checkout_paths(), git_path_arguments() as arguments:
            project, docs, _directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
            # The simulation is live: a path relative to the checkout renders as on Windows.
            self.assertEqual(str(docs.relative_to(project)), "workspace\\docs")
            delivery_git.publish_execution_plan(project, "DLV-001")
            delivery_git.claim_items(project, "DLV-001")
            self.governance_target_handoff(project, docs)
            self.assertTrue(delivery_git.refresh_target(project, "DLV-001")["changed"])
            delivery_git.start_item(project, "DLV-001", "AUTH-01")
            delivery_git.block_item(project, "DLV-001", "AUTH-01")
            delivery_git.unblock_item(project, "DLV-001", "AUTH-01")
            delivery_git.pause_item(project, "DLV-001", "AUTH-01")
            delivery_git.resume_item(project, "DLV-001", "AUTH-01")
            active = delivery_git.takeover_item(project, "DLV-001", "AUTH-01", confirm=True)
            self.commit_item_product_change(active["worktree"], "def authenticate():\n    return 'v1'\n")
            self.assertEqual(self.approve_item_evidence(active["worktree"]), 0)
            delivery_git.push_item(project, "DLV-001", "AUTH-01")
            delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
            reopened = delivery_git.reopen_item(project, "DLV-001", "AUTH-01")
            self.commit_item_product_change(reopened["worktree"], "def authenticate():\n    return 'v2'\n")
            self.assertEqual(self.approve_item_evidence(reopened["worktree"]), 0)
            delivery_git.push_item(project, "DLV-001", "AUTH-01")
            integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
            review = type("Args", (), {"docs": str(docs), "delivery": "DLV-001",
                                       "reviewed_commit": integrated["integration"],
                                       "reviewed_integration_commit": integrated["integration"]})
            self.assertEqual(delivery_compile.approve_review(review), 0)
            delivery_git.publish_delivery_review(project, "DLV-001")
            delivery_git.prepare_pr_creation(project, "DLV-001")
            pr_url = "https://github.com/agentrof/example/pull/17"
            delivery_compile.record_pr(type("Args", (), {"docs": str(docs), "delivery": "DLV-001", "url": pr_url}))
            delivery_git.record_pr_remote(project, "DLV-001", pr_url)
            delivery_git.invalidate_delivery_review(project, "DLV-001", "REVIEW_FINDING", "sha256:" + "0" * 64)
            cancelled = delivery_git.cancel_delivery(project, "DLV-001", "Request withdrawn")
        self.assertTrue(cancelled["reverts"])
        relative_item = item.relative_to(project).as_posix()
        self.assertIn(relative_item, arguments)
        self.assertTrue(any(value.endswith(":" + relative_item) for value in arguments))
        item_tip = delivery_git.remote_oid(project, "origin", delivery_git.canonical_refs("DLV-001", "AUTH-01")["item"])
        self.assert_git_paths_are_posix(arguments, project, [cancelled["review"], item_tip])

    def test_package_commit_check_hands_git_posix_paths_on_a_host_with_backslash_separators(self):
        """The commit check behind the stage receipts a Delivery reads names its
        package to Git with forward slashes on every host (#236)."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        project = Path(temporary.name)
        init_repository(project)
        delivery_git.run_git(project, "config", "user.email", "test@example.com")
        delivery_git.run_git(project, "config", "user.name", "Test")
        package = project / "workspace" / "docs" / "solution-design"
        note = package / "landscape.md"
        metadata = package / "decisions" / ".DS_Store"
        metadata.parent.mkdir(parents=True)
        note.write_text("# Landscape\n", encoding="utf-8")
        metadata.write_bytes(b"operating-system metadata")
        delivery_git.run_git(project, "add", "--force", "workspace")
        delivery_git.run_git(project, "commit", "-qm", "Commit the package")
        with windows_checkout_paths(), git_path_arguments() as arguments:
            self.assertEqual(str(package.relative_to(project)), "workspace\\docs\\solution-design")
            # Committed metadata that is gone is looked up in HEAD by its path.
            metadata.unlink()
            self.assertTrue(stage_package.is_committed(package))
            note.write_text("# Landscape\n\nAuthored drift.\n", encoding="utf-8")
            self.assertFalse(stage_package.is_committed(package))
            note.write_text("# Landscape\n", encoding="utf-8")
            (package / "draft.md").write_text("# Draft\n", encoding="utf-8")
            self.assertFalse(stage_package.is_committed(package))
        self.assertIn(package.relative_to(project).as_posix(), arguments)
        self.assert_git_paths_are_posix(arguments, project, ["HEAD"])

    def test_execution_publication_includes_only_exact_bound_operation_contracts(self):
        for runtime in (True, False):
            with self.subTest(runtime=runtime):
                project, docs, directory, item, reserved = self.prepare_execution_with_draft_reserved_contracts(runtime)
                target = delivery_git.run_git(project, "rev-parse", "HEAD")
                scope_hash = delivery_compile.split_note(directory / "delivery.md")[0]["scope_hash"]
                unrelated = docs / "operation/local-notes.md"
                unrelated.write_text("# Unpublished local notes\n", encoding="utf-8")
                draft_environment = delivery_git.run_git(project, "show", reserved["integration"] + ":workspace/docs/operation/environment-contract.md")
                self.assertIn("status: draft", draft_environment)
                published = delivery_git.publish_execution_plan(project, "DLV-001")
                # A non-runtime plan leaves the Environment Contract the draft the Integration
                # holds, and an identical copy is neither refused nor reported.
                self.assertEqual(published["operation_not_carried"], [])
                with tempfile.TemporaryDirectory() as temporary:
                    clone = Path(temporary) / "checkout"
                    delivery_git.run_git(project, "clone", "-q", str(project / "remote.git"), str(clone))
                    delivery_git.run_git(clone, "checkout", "-q", "--detach", published["integration"])
                    candidate_docs = clone / "workspace/docs"
                    candidate_item = candidate_docs / item.relative_to(docs)
                    item_props, _body = delivery_compile.split_note(candidate_item)
                    self.assertEqual(delivery_compile.item_operation_findings(candidate_docs, item_props), [])
                    self.assertEqual(delivery_compile.delivery_findings(candidate_docs, "DLV-001")[1], [])
                    for kind in ("verification", "environment") if runtime else ("verification",):
                        receipt, errors = operation_compile.check_contract(candidate_docs, kind)
                        self.assertEqual(errors, [])
                        self.assertTrue(receipt["current"])
                        self.assertEqual(item_props[kind + "_contract_hash"], receipt["source_hash"])
                        actual = operation_compile.contract_path(candidate_docs, kind).read_text(encoding="utf-8")
                        expected = operation_compile.contract_path(docs, kind).read_text(encoding="utf-8")
                        self.assertEqual(delivery_compile.without_generated_relations(actual),
                                         delivery_compile.without_generated_relations(expected))
                    if not runtime:
                        self.assertNotIn("environment_contract_ref", item_props)
                        self.assertEqual(operation_compile.contract_path(candidate_docs, "environment").read_text(encoding="utf-8").strip(),
                                         draft_environment)
                    self.assertFalse((candidate_docs / unrelated.relative_to(docs)).exists())
                    self.assertEqual(delivery_compile.split_note(candidate_docs / directory.relative_to(docs) / "delivery.md")[0]["scope_hash"], scope_hash)
                    findings = []
                    vault_check.check_relation_projections(vault_check.build_vault(candidate_docs, vault_check.load_policy(vault_check.DEFAULT_POLICY)), findings)
                    self.assertEqual(findings, [])
                    self.assertEqual(delivery_git.run_git(clone, "status", "--porcelain"), "")
                self.assertEqual(delivery_git.run_git(project, "rev-parse", "HEAD"), target)
                self.assertEqual(delivery_git.remote_oid(project, "origin", "refs/heads/main"), target)
                self.assertEqual(unrelated.read_text(encoding="utf-8"), "# Unpublished local notes\n")

    def approve_environment_revision(self, docs: Path) -> dict:
        """Approve a local Environment Contract revision, as execution planning may, and return its receipt."""
        path = operation_compile.contract_path(docs, "environment")
        args = type("Args", (), {"docs": str(docs), "kind": "environment",
                                 "constrained_by": ["[[solution-design/decisions/fixture-api|Fixture API]]"]})
        if not path.exists():
            self.assertEqual(operation_compile.init(args), 0)
        props, body = operation_compile.parse(path)
        props["env_command"] = "make env"
        operation_compile.atomic_text(path, operation_compile.render(props, body))
        self.assertEqual(operation_compile.approve(args), 0)
        receipt, errors = operation_compile.check_contract(docs, "environment")
        self.assertEqual(errors, [])
        return receipt

    def test_execution_publication_refuses_an_approved_operation_revision_no_item_pins(self):
        """A non-runtime Item pins no Environment Contract, so publication cannot carry its approved revision (#320)."""
        contract = "workspace/docs/operation/environment-contract.md"
        refs = delivery_git.canonical_refs("DLV-001")
        for held in ("draft revision 1", "no copy"):
            with self.subTest(integration_holds=held):
                if held != "no copy":
                    project, docs, _directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
                else:
                    temporary, project, docs = self.reserve_scope()
                    self.addCleanup(remove_temporary, temporary)
                    self.author_execution_topology(docs)
                    self.assertEqual(delivery_compile.approve_execution(
                        type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})), 0)
                    item = delivery_compile.find_delivery(docs, "DLV-001") / "items/auth-01/item.md"
                self.assertFalse(delivery_compile.split_note(item)[0]["runtime_required"])
                before = delivery_git.remote_ref_oids(project, "origin", [refs["fence"], refs["integration"]])
                held_copy = delivery_git.published_plan_blobs(project, before[refs["integration"]], [contract]).get(contract)
                if held == "no copy":
                    self.assertIsNone(held_copy)
                else:
                    self.assertIn("status: draft", held_copy)
                receipt = self.approve_environment_revision(docs)
                self.assertEqual(receipt["revision"], 1)
                self.assertEqual(self.refused_finding(lambda: delivery_git.publish_execution_plan(project, "DLV-001")), (
                    "DELIVERY_OPERATION_UNCARRIED",
                    f"no Item pins the approved Environment Contract revision 1, and the Integration holds {held} "
                    f"at {contract}, so publication would leave revision 1 out; record that revision on the target "
                    "branch and run refresh-target, or pin it with runtime_required: true on an Item that needs a "
                    "live service environment"))
                self.assertEqual(delivery_git.remote_ref_oids(project, "origin", [refs["fence"], refs["integration"]]), before)

    def test_an_unpinned_operation_revision_reaches_the_delivery_through_the_target(self):
        """The refusal's first route: record the revision on the target, refresh, then publish."""
        project, docs, _directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        receipt = self.approve_environment_revision(docs)
        with self.assertRaisesRegex(RuntimeError, "^DELIVERY_OPERATION_UNCARRIED: "):
            delivery_git.publish_execution_plan(project, "DLV-001")
        contract = "workspace/docs/operation/environment-contract.md"
        delivery_git.run_git(project, "add", "--", contract)
        delivery_git.run_git(project, "commit", "-qm", "Record the approved Environment Contract")
        delivery_git.run_git(project, "push", "-q", "origin", "main")
        refreshed = delivery_git.refresh_target(project, "DLV-001")
        self.assertIn(contract, refreshed["paths"])
        published = delivery_git.publish_execution_plan(project, "DLV-001")
        self.assertEqual(published["operation_not_carried"], [])
        carried = delivery_git.published_plan_blobs(project, published["integration"], [contract])[contract]
        self.assertEqual(operation_compile.check_contract(docs, "environment", carried), (receipt, []))

    def test_execution_publication_reports_an_operation_contract_it_cannot_carry(self):
        """A differing local copy that is not approved and current is left out and named, not refused."""
        contract = "workspace/docs/operation/environment-contract.md"
        for local in ("draft", "stale approval"):
            with self.subTest(local=local):
                project, docs, _directory, _item, reserved = self.prepare_execution_with_draft_reserved_contracts(False)
                path = operation_compile.contract_path(docs, "environment")
                if local == "draft":
                    props, body = operation_compile.parse(path)
                    props["env_command"] = "make env"
                    operation_compile.atomic_text(path, operation_compile.render(props, body))
                else:
                    self.approve_environment_revision(docs)
                    path.write_text(path.read_text(encoding="utf-8") + "\nEdited after approval.\n", encoding="utf-8")
                self.assertFalse(operation_compile.check_contract(docs, "environment")[0]["current"])
                published = delivery_git.publish_execution_plan(project, "DLV-001")
                self.assertEqual(published["operation_not_carried"], [contract])
                self.assertIn({"kind": "file", "target": contract, "value": "not_carried"},
                              delivery_result.from_raw("publish-execution-plan", published)["observations"])
                self.assertEqual(delivery_git.run_git(project, "show", published["integration"] + ":" + contract),
                                 delivery_git.run_git(project, "show", reserved["integration"] + ":" + contract))

    SUPERSEDED_REMEDY = ("take the Delivery package and the Operation contracts from the Integration, "
                         "then revise inside begin-plan-revision")

    def second_checkout(self, project: Path, directory: Path, publication: str) -> Path:
        """Clone the project as a second host and take the package and Operation contracts of one publication."""
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, holder)
        clone = Path(holder.name) / "checkout"
        delivery_git.run_git(project, "clone", "-q", str(project / "remote.git"), str(clone))
        disable_automatic_maintenance(clone / ".git")
        delivery_git.run_git(clone, "config", "user.email", "second@example.com")
        delivery_git.run_git(clone, "config", "user.name", "Second host")
        delivery_git.run_git(clone, "checkout", publication, "--",
                             directory.relative_to(project).as_posix(), "workspace/docs/operation")
        delivery_git.run_git(clone, "reset", "-q")
        return clone

    def revise_verification_contract(self, docs: Path, note: str) -> dict:
        """Approve the next Verification Contract revision and return its receipt."""
        kind = type("Args", (), {"docs": str(docs), "kind": "verification"})
        self.assertEqual(operation_compile.revise(kind), 0)
        path = operation_compile.contract_path(docs, "verification")
        props, body = operation_compile.parse(path)
        operation_compile.atomic_text(path, operation_compile.render(props, body + "\n\n" + note + "\n"))
        self.assertEqual(operation_compile.approve(kind), 0)
        receipt, errors = operation_compile.check_contract(docs, "verification")
        self.assertEqual(errors, [])
        return receipt

    def test_a_checkout_holding_an_earlier_approval_cannot_publish_over_a_revised_plan(self):
        """An earlier approval, or one re-approved from it, never replaces the plan that revised it (#322)."""
        project, docs, directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
        refs = delivery_git.canonical_refs("DLV-001")
        first = delivery_git.publish_execution_plan(project, "DLV-001")
        second = self.second_checkout(project, directory, first["integration"])
        second_docs = second / "workspace/docs"
        second_delivery = second_docs / directory.relative_to(docs) / "delivery.md"
        earlier = delivery_compile.split_note(second_delivery)[0]["plan_hash"]
        delivery_git.begin_plan_revision(project, "DLV-001")
        self.assertEqual(self.revise_verification_contract(docs, "Revision 2 is the newer approved contract.")["revision"], 2)
        self.assertEqual(delivery_compile.approve_execution(scope), 0)
        delivery_git.publish_execution_plan(project, "DLV-001")
        revised = delivery_compile.split_note(directory / "delivery.md")[0]["plan_hash"]
        self.assertNotEqual(revised, earlier)

        def refused(plan_hash: str) -> tuple[str, str]:
            return ("DELIVERY_PLAN_SUPERSEDED",
                    f"the Integration holds execution plan {revised} and the Verification Contract approved at "
                    f"revision 2, which this checkout's approval of execution plan {plan_hash} does not supersede; "
                    + self.SUPERSEDED_REMEDY)

        for moment in ("inside the plan revision", "after the plan revision"):
            with self.subTest(moment=moment):
                if moment == "after the plan revision":
                    delivery_git.finish_plan_revision(project, "DLV-001")
                delivery_git.run_git(second, "fetch", "-q", "origin")
                before = delivery_git.remote_ref_oids(project, "origin", [refs["fence"], refs["integration"]])
                self.assertEqual(self.refused_finding(lambda: delivery_git.publish_execution_plan(second, "DLV-001")),
                                 refused(earlier))
                self.assertEqual(delivery_git.remote_ref_oids(project, "origin", [refs["fence"], refs["integration"]]), before)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = delivery_git.main(["publish-execution-plan", "--project-root", str(second), "--delivery", "DLV-001"])
        envelope = json.loads(output.getvalue())
        self.assertEqual((exit_code, envelope["ok"], envelope["mutation_state"]), (1, False, "none"))
        self.assertEqual([finding["code"] for finding in envelope["findings"]], ["DELIVERY_PLAN_SUPERSEDED"])

        # Re-approving the earlier package supersedes only that approval, not the revision.
        second_plan = second_delivery.parent / "execution-plan.md"
        earlier_approval = delivery_compile.split_note(second_plan)[0]["source_hash"]
        self.assertEqual(delivery_compile.approve_execution(type("Args", (), {"docs": str(second_docs), "delivery": "DLV-001"})), 0)
        self.assertEqual(delivery_compile.split_note(second_plan)[0]["superseded_plan_approvals"], [earlier_approval])
        reapproved = delivery_compile.split_note(second_delivery)[0]["plan_hash"]
        before = delivery_git.remote_ref_oids(project, "origin", [refs["fence"], refs["integration"]])
        self.assertEqual(self.refused_finding(lambda: delivery_git.publish_execution_plan(second, "DLV-001")),
                         refused(reapproved))
        self.assertEqual(delivery_git.remote_ref_oids(project, "origin", [refs["fence"], refs["integration"]]), before)

    def test_a_checkout_holding_an_earlier_contract_cannot_publish_it_under_a_sealed_plan(self):
        """A sealed Item keeps its bindings and so the plan hash, which cannot show the older contract (#322)."""
        project, docs, directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
        refs = delivery_git.canonical_refs("DLV-001")
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        self.commit_item_product_change(active["worktree"], "def authenticate():\n    return True\n")
        self.assertEqual(self.approve_item_evidence(active["worktree"]), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        # The plan revision compiles the tracked package as the Integration sealed it.
        for name in ("item.md", "code-review.md", "verification.md"):
            relative = (directory / "items/auth-01" / name).relative_to(project).as_posix()
            props, body = delivery_git.split_remote_note(project, integrated["integration"], relative, delivery_compile.split_note)
            delivery_compile.atomic_text(project / relative, delivery_compile.frontmatter(props, body))
        self.assertEqual(delivery_compile.approve_execution(scope), 0)
        sealed = delivery_git.publish_execution_plan(project, "DLV-001")
        second = self.second_checkout(project, directory, sealed["integration"])
        second_docs = second / "workspace/docs"
        plan_hash = delivery_compile.split_note(directory / "delivery.md")[0]["plan_hash"]
        delivery_git.begin_plan_revision(project, "DLV-001")
        self.revise_verification_contract(docs, "Revision 2 is the newer approved contract.")
        self.assertEqual(delivery_compile.approve_execution(scope), 0)
        self.assertEqual(delivery_compile.split_note(directory / "delivery.md")[0]["plan_hash"], plan_hash)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.finish_plan_revision(project, "DLV-001")
        delivery_git.run_git(second, "fetch", "-q", "origin")
        self.assertEqual(delivery_compile.split_note(second_docs / directory.relative_to(docs) / "delivery.md")[0]["plan_hash"],
                         plan_hash)
        for local in ("revision 1", "another revision 2"):
            with self.subTest(local=local):
                if local == "another revision 2":
                    self.revise_verification_contract(second_docs, "Revision 2 as the second host approved it.")
                replacement = "its approved revision 1" if local == "revision 1" else "a different approved revision 2"
                before = delivery_git.remote_ref_oids(project, "origin", [refs["fence"], refs["integration"]])
                self.assertEqual(self.refused_finding(lambda: delivery_git.publish_execution_plan(second, "DLV-001")), (
                    "DELIVERY_PLAN_SUPERSEDED",
                    f"the Integration holds execution plan {plan_hash} and the Verification Contract approved at "
                    f"revision 2, which this checkout would replace with {replacement}; " + self.SUPERSEDED_REMEDY))
                self.assertEqual(delivery_git.remote_ref_oids(project, "origin", [refs["fence"], refs["integration"]]), before)

    def test_publication_takes_a_first_an_identical_and_a_superseding_approval(self):
        """Only an approval that revises the Integration's, directly or through unpublished ones, replaces it."""
        project, docs, directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
        plan = directory / "execution-plan.md"
        refs = delivery_git.canonical_refs("DLV-001")
        first = delivery_compile.split_note(plan)[0]
        self.assertEqual(first["superseded_plan_approvals"], [])
        published = delivery_git.publish_execution_plan(project, "DLV-001")
        republished = delivery_git.publish_execution_plan(project, "DLV-001")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), republished["integration"])
        # A second host holding the same approval publishes it again, as before.
        second = self.second_checkout(project, directory, published["integration"])
        delivery_git.run_git(second, "fetch", "-q", "origin")
        delivery_git.publish_execution_plan(second, "DLV-001")
        delivery_git.run_git(project, "fetch", "-q", "origin")

        delivery_git.begin_plan_revision(project, "DLV-001")
        self.revise_verification_contract(docs, "Revision 2 is the newer approved contract.")
        self.assertEqual(delivery_compile.approve_execution(scope), 0)
        revision = delivery_compile.split_note(plan)[0]
        self.assertEqual(revision["superseded_plan_approvals"], [first["source_hash"]])
        # Approval is offline, so one approved again before it is published still names the published one.
        self.assertEqual(delivery_compile.approve_execution(scope), 0)
        again = delivery_compile.split_note(plan)[0]
        self.assertEqual(again["plan_hash"], revision["plan_hash"])
        self.assertEqual(again["superseded_plan_approvals"], [revision["source_hash"], first["source_hash"]])
        revised = delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.finish_plan_revision(project, "DLV-001")
        carried = delivery_git.split_remote_note(project, revised["integration"], plan.relative_to(project).as_posix(),
                                                 delivery_compile.split_note)[0]
        self.assertEqual((carried["source_hash"], carried["superseded_plan_approvals"]),
                         (again["source_hash"], again["superseded_plan_approvals"]))

    def test_republication_keeps_the_records_of_an_item_the_integration_sealed(self):
        """Publication never puts a sealed Item's record or evidence back to a checkout's earlier copy."""
        project, docs, directory, _item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        first = delivery_git.publish_execution_plan(project, "DLV-001")
        second = self.second_checkout(project, directory, first["integration"])
        delivery_git.claim_items(project, "DLV-001")
        active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        self.commit_item_product_change(active["worktree"], "def authenticate():\n    return True\n")
        self.assertEqual(self.approve_item_evidence(active["worktree"]), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        records = [(directory / "items/auth-01" / name).relative_to(project).as_posix()
                   for name in ("item.md", "code-review.md", "verification.md")]
        sealed = delivery_git.published_plan_blobs(project, integrated["integration"], records)

        def statuses(checkout: Path, oid: str) -> list:
            return [delivery_git.split_remote_note(checkout, oid, path, delivery_compile.split_note)[0]["status"]
                    for path in records]

        self.assertEqual(statuses(project, integrated["integration"]), ["integrated", "approved", "passed"])
        for checkout, label in ((project, "integrating checkout"), (second, "second checkout")):
            with self.subTest(checkout=label):
                self.assertEqual([delivery_compile.split_note(checkout / path)[0]["status"] for path in records],
                                 ["in_scope", "draft", "draft"])
                delivery_git.run_git(checkout, "fetch", "-q", "origin")
                republished = delivery_git.publish_execution_plan(checkout, "DLV-001")
                self.assertEqual(delivery_git.published_plan_blobs(checkout, republished["integration"], records), sealed)

        # The sealed record an approval starts from is published, and the evidence stays sealed.
        delivery_git.run_git(project, "fetch", "-q", "origin")
        record = project / records[0]
        record.write_text(sealed[records[0]], encoding="utf-8")
        self.assertEqual(delivery_compile.approve_execution(type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})), 0)
        revised = delivery_git.publish_execution_plan(project, "DLV-001")
        carried = delivery_git.published_plan_blobs(project, revised["integration"], records)
        self.assertEqual(carried[records[0]], record.read_text(encoding="utf-8"))
        self.assertEqual((carried[records[1]], carried[records[2]]), (sealed[records[1]], sealed[records[2]]))
        self.assertEqual(statuses(project, revised["integration"]), ["integrated", "approved", "passed"])

    def test_execution_publication_keeps_a_terminal_item_on_its_verified_revision(self):
        """A closed Item's binding names history, not a stale current receipt."""
        project, _docs, _directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts()
        props, body = delivery_compile.split_note(item)
        superseded = dict(props)
        superseded["verification_contract_hash"] = "sha256:" + "0" * 64

        def write(status):
            value = dict(superseded, status=status)
            value["tags"] = [tag for tag in props.get("tags", [])
                             if not str(tag).startswith("status/")] + [f"status/{status}"]
            delivery_compile.atomic_text(item, delivery_compile.frontmatter(value, body))

        write("integrated")
        with mock.patch.object(delivery_git, "atomic_push") as push:
            delivery_git.publish_execution_plan(project, "DLV-001")
            push.assert_called()

        # The same binding on an open Item is genuine staleness and still fails.
        write("active")
        with mock.patch.object(delivery_git, "atomic_push") as push:
            with self.assertRaisesRegex(RuntimeError, "binding is stale or missing"):
                delivery_git.publish_execution_plan(project, "DLV-001")
            push.assert_not_called()

    def test_execution_publication_rejects_invalid_operation_bindings_before_ref_changes(self):
        project, docs, _directory, item, reserved = self.prepare_execution_with_draft_reserved_contracts()
        original = item.read_bytes()
        props, body = delivery_compile.split_note(item)
        refs = delivery_git.canonical_refs("DLV-001")
        for field, value in (("verification_contract_ref", "../../README"),
                             ("verification_contract_hash", "sha256:" + "0" * 64),
                             ("verification_contract_ref", None),
                             ("environment_contract_hash", None)):
            with self.subTest(field=field, value=value):
                invalid = dict(props)
                if value is None:
                    invalid.pop(field)
                else:
                    invalid[field] = value
                delivery_compile.atomic_text(item, delivery_compile.frontmatter(invalid, body))
                with mock.patch.object(delivery_git, "atomic_push") as push:
                    with self.assertRaisesRegex(RuntimeError, "binding is stale or missing"):
                        delivery_git.publish_execution_plan(project, "DLV-001")
                    push.assert_not_called()
                self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), reserved["integration"])
                self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), reserved["fence"])
        item.write_bytes(original)
        contract = operation_compile.contract_path(docs, "verification")
        saved_contract = contract.read_bytes()
        for missing in (True, False):
            with self.subTest(missing_contract=missing):
                if missing:
                    contract.unlink()
                else:
                    contract.write_bytes(saved_contract + b"\nChanged approved contract.\n")
                with mock.patch.object(delivery_git, "atomic_push") as push:
                    with self.assertRaisesRegex(RuntimeError, "approved current verification contract"):
                        delivery_git.publish_execution_plan(project, "DLV-001")
                    push.assert_not_called()
                contract.write_bytes(saved_contract)
                self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), reserved["integration"])
                self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), reserved["fence"])

    def test_execution_publication_validates_staged_contract_after_local_check(self):
        project, docs, _directory, _item, reserved = self.prepare_execution_with_draft_reserved_contracts()
        contract = operation_compile.contract_path(docs, "verification")
        original = contract.read_bytes()
        original_commit = delivery_git.commit_tree

        def change_contract_before_staging(*args, **kwargs):
            if kwargs.get("operation_bindings"):
                contract.write_bytes(original.replace(b"test_command: make test", b"test_command: make changed"))
            return original_commit(*args, **kwargs)

        with mock.patch.object(delivery_git, "commit_tree", side_effect=change_contract_before_staging), \
                mock.patch.object(delivery_git, "atomic_push") as push:
            with self.assertRaisesRegex(RuntimeError, "Delivery candidate Operation bindings are invalid"):
                delivery_git.publish_execution_plan(project, "DLV-001")
            push.assert_not_called()
        contract.write_bytes(original)
        refs = delivery_git.canonical_refs("DLV-001")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), reserved["integration"])
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), reserved["fence"])

    def test_execution_publication_rejects_candidate_solution_drift_with_current_local_receipts(self):
        project, docs, _directory, item, reserved = self.prepare_execution_with_draft_reserved_contracts()
        decision = "workspace/docs/solution-design/decisions/fixture-api.md"
        previous = delivery_git.run_git(project, "show", reserved["integration"] + ":" + decision)
        drifted = delivery_git.commit_replacements(project, reserved["integration"],
            {decision: previous + "\n\nChanged remote Solution input.\n"}, "Change candidate Solution input", {})
        refs = delivery_git.canonical_refs("DLV-001")
        delivery_git.atomic_push(project, "origin", [(refs["integration"], reserved["integration"], drifted)])
        self.assertEqual(delivery_compile.delivery_findings(docs, "DLV-001")[1], [])
        self.assertEqual(delivery_compile.item_operation_findings(docs, delivery_compile.split_note(item)[0]), [])
        with self.assertRaisesRegex(RuntimeError, "Delivery candidate Operation bindings are invalid"):
            delivery_git.publish_execution_plan(project, "DLV-001")
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["integration"]), drifted)
        self.assertEqual(delivery_git.remote_oid(project, "origin", refs["fence"]), reserved["fence"])

    def test_execution_publication_claim_and_start_use_global_slot(self):
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
        docs = project / "workspace" / "docs"
        (docs / "maps").mkdir(parents=True, exist_ok=True)
        make_approved_backlog(docs)
        subprocess.run(["git", "-C", str(project), "add", "workspace"], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-qm", "approved backlog"], check=True)
        subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
        remote = project / "remote.git"
        dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        delivery_compile.init_dod(dod); delivery_compile.approve_dod(dod)
        init = type("Args", (), {"docs": str(docs), "id": None, "slug": None, "goal": "SAML authentication", "outcome": None, "target_branch": "main", "story": ["AUTH-01"]})
        delivery_compile.init_delivery(init)
        scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"}); delivery_compile.approve_scope(scope)
        subprocess.run(["git", "-C", str(project), "add", "workspace/docs"], check=True); subprocess.run(["git", "-C", str(project), "commit", "-qm", "scope"], check=True); subprocess.run(["git", "-C", str(project), "push", "-q"], check=True)
        delivery_git.reserve_delivery(project, "DLV-001")
        self.author_execution_topology(docs)
        delivery_compile.approve_execution(scope)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.refresh_target(project, "DLV-001")
        result = delivery_git.claim_items(project, "DLV-001")
        self.assertEqual(result["claims"], ["AUTH-01"])
        governance = docs / "delivery" / "governance" / "governance.md"
        before_governance = governance.read_text(encoding="utf-8")
        governance.write_text(before_governance.replace("max_parallel: 1", "max_parallel: 2"), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "(?i)governance"):
            delivery_git.start_item(project, "DLV-001", "AUTH-01")
        governance.write_text(before_governance, encoding="utf-8")
        receipt, errors = delivery_governance.status(docs)
        self.assertEqual(errors, [])
        self.assertTrue(receipt.get("current"), receipt)
        activation = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(activation["slot"], "001")
        self.assertEqual(activation["receipt"]["state"], "verified")
        self.assertTrue(Path(activation["worktree"]).is_dir())
        self.assertEqual(
            subprocess.run(
                ["git", "-C", activation["worktree"], "rev-parse", "HEAD"],
                check=True, capture_output=True, text=True,
            ).stdout.strip(),
            activation["item"],
        )
        delivery_git.clear_verified_writer_receipt(project, "DLV-001", "AUTH-01")
        delivery_git.remove_item_worktree(project, "DLV-001", "AUTH-01")
        takeover = delivery_git.takeover_item(project, "DLV-001", "AUTH-01", confirm=True)
        self.assertNotEqual(takeover["writer_epoch"], activation["writer_epoch"])
        self.assertEqual(takeover["receipt"]["state"], "verified")
        blocked = delivery_git.block_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(blocked["status"], "blocked")
        unblocked = delivery_git.unblock_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(unblocked["status"], "active")
        paused = delivery_git.pause_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(paused["status"], "paused")
        self.assertFalse(Path(activation["worktree"]).exists())
        self.assertIsNone(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-01"))
        resumed = delivery_git.resume_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(resumed["receipt"]["state"], "verified")
        self.assertNotEqual(resumed["writer_epoch"], activation["writer_epoch"])
        first_product = self.commit_item_product_change(resumed["worktree"], "def authenticate():\n    return 'v1'\n")
        self.assertEqual(self.approve_item_evidence(resumed["worktree"]), 0)
        pushed = delivery_git.push_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(pushed["product_tip"], first_product)
        first_evidence = delivery_git.commit_message(project, pushed["item"])
        self.assertEqual(delivery_git.trailer(first_evidence, "Record"), "item-evidence-v1")
        self.assertEqual(delivery_git.trailer(first_evidence, "Product-Tip"), first_product)
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        self.assertTrue(integrated["ok"])
        self.assertEqual(
            subprocess.run(
                ["git", "show", f"{integrated['integration']}:src/auth.py"],
                cwd=project, check=True, capture_output=True, text=True,
            ).stdout,
            "def authenticate():\n    return 'v1'\n",
        )
        reopened = delivery_git.reopen_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(reopened["status"], "active")
        self.assertEqual(delivery_git.trailer(delivery_git.commit_message(project, reopened["item"]), "Record"), "item-reopen-v1")
        second_product = self.commit_item_product_change(reopened["worktree"], "def authenticate():\n    return 'v2'\n")
        self.assertEqual(self.approve_item_evidence(reopened["worktree"]), 0)
        pushed = delivery_git.push_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(pushed["product_tip"], second_product)
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        integration_oid = delivery_git.remote_oid(
            project, "origin", delivery_git.canonical_refs("DLV-001")["integration"]
        )
        review_args = type("Args", (), {
            "docs": str(docs), "delivery": "DLV-001",
            "reviewed_commit": integrated["integration"],
            "reviewed_integration_commit": integration_oid,
        })
        delivery_compile.approve_review(review_args)
        published = delivery_git.publish_delivery_review(project, "DLV-001")
        self.assertTrue(published["ok"])
        intent = delivery_git.prepare_pr_creation(project, "DLV-001")
        self.assertEqual(intent["provider"], "github")
        pr_url = "https://github.com/agentrof/example/pull/17"
        record_args = type("Args", (), {"docs": str(docs), "delivery": "DLV-001", "url": pr_url})
        delivery_compile.record_pr(record_args)
        recorded = delivery_git.record_pr_remote(project, "DLV-001", pr_url)
        self.assertEqual(recorded["pull_request"], "17")
        refs = subprocess.run(["git", "--git-dir", str(remote), "show-ref"], check=True, text=True, capture_output=True).stdout
        self.assertIn("refs/heads/agentrof/items/auth-01", refs)
        self.assertNotIn("refs/heads/agentrof/slots/001", refs)
        cancelled = delivery_git.cancel_delivery(
            project, "DLV-001", "Target no longer requires this Delivery"
        )
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertTrue(cancelled["reverts"])
        self.assertEqual(
            delivery_git.trailer(
                delivery_git.commit_message(project, cancelled["finalization"]),
                "Record",
            ),
            "cancellation-finalized-v1",
        )

    def refused_finding(self, refusal) -> tuple[str, str]:
        """The code and message a coordinator refusal reaches the result envelope with."""
        with self.assertRaises(RuntimeError) as refused:
            refusal()
        finding = delivery_result.from_raw("refusal", {"ok": False, "errors": [str(refused.exception)]})["findings"][0]
        return finding["code"], finding["message"]

    def claim_ordered_items(self) -> Path:
        """Claim one Delivery whose AUTH-02 executes after AUTH-01."""
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
        docs = project / "workspace" / "docs"
        (docs / "maps").mkdir(parents=True, exist_ok=True)
        make_approved_backlog(docs, "AUTH-01", "AUTH-02")
        delivery_git.run_git(project, "add", "workspace")
        delivery_git.run_git(project, "commit", "-qm", "approved backlog")
        delivery_git.run_git(project, "push", "-q")
        dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(dod), 0)
        self.assertEqual(delivery_compile.approve_dod(dod), 0)
        init = type("Args", (), {"docs": str(docs), "id": None, "slug": None, "goal": "SAML authentication",
                                 "outcome": None, "target_branch": "main", "story": ["AUTH-01", "AUTH-02"]})
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        scope = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
        self.assertEqual(delivery_compile.approve_scope(scope), 0)
        delivery_git.run_git(project, "add", "workspace/docs")
        delivery_git.run_git(project, "commit", "-qm", "scope")
        delivery_git.run_git(project, "push", "-q")
        delivery_git.reserve_delivery(project, "DLV-001")
        self.author_execution_topology(docs)
        later = delivery_compile.find_delivery(docs, "DLV-001") / "items" / "auth-02" / "item.md"
        props, body = delivery_compile.split_note(later)
        props["path_claims"] = ["src/session.py"]
        props["execution_after"] = ["AUTH-01"]
        delivery_compile.atomic_text(later, delivery_compile.frontmatter(props, body))
        self.assertEqual(delivery_compile.approve_execution(scope), 0)
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.refresh_target(project, "DLV-001")
        self.assertEqual(delivery_git.claim_items(project, "DLV-001")["claims"], ["AUTH-01", "AUTH-02"])
        return project

    def test_start_item_waits_until_the_items_it_executes_after_are_integrated(self):
        """An Item starts only once each Item its plan orders it after is integrated."""
        project = self.claim_ordered_items()

        def refuse_later_start():
            before = delivery_git.run_git(project, "ls-remote", "origin")
            code, message = self.refused_finding(lambda: delivery_git.start_item(project, "DLV-001", "AUTH-02"))
            self.assertEqual((code, message), ("DELIVERY_DEPENDENCY_UNMET",
                                               "AUTH-02 starts only after these Items are integrated: AUTH-01"))
            self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), before)
            self.assertIsNone(delivery_git.read_writer_receipt(project, "DLV-001", "AUTH-02"))

        refuse_later_start()
        delivery_git.start_item(project, "DLV-001", "AUTH-01")
        delivery_git.pause_item(project, "DLV-001", "AUTH-01")
        refuse_later_start()
        resumed = delivery_git.resume_item(project, "DLV-001", "AUTH-01")
        self.commit_item_product_change(resumed["worktree"], "def authenticate():\n    return 'v1'\n")
        self.assertEqual(self.approve_item_evidence(resumed["worktree"]), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        started = delivery_git.start_item(project, "DLV-001", "AUTH-02")
        self.assertEqual((started["story"], started["slot"]), ("AUTH-02", "001"))

    def claim_waiting_deliveries(self, claim_dependency: bool = True) -> Path:
        """Claim DLV-002, whose AUTH-02 waits for AUTH-01 of DLV-001, and DLV-001 unless told not to."""
        temporary, project = self.make_project()
        self.addCleanup(remove_temporary, temporary)
        docs = project / "workspace" / "docs"
        (docs / "maps").mkdir(parents=True, exist_ok=True)
        make_approved_backlog(docs, "AUTH-01", "AUTH-02")
        dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        self.assertEqual(delivery_compile.init_dod(dod), 0)
        self.assertEqual(delivery_compile.approve_dod(dod), 0)
        delivery_git.run_git(project, "add", "workspace")
        delivery_git.run_git(project, "commit", "-qm", "approved backlog")
        delivery_git.run_git(project, "push", "-q")
        for delivery, slug, story in (("DLV-001", "auth", "AUTH-01"), ("DLV-002", "session", "AUTH-02")):
            init = type("Args", (), {"docs": str(docs), "id": delivery, "slug": slug, "goal": f"Deliver {story}",
                                     "outcome": None, "target_branch": "main", "story": [story]})
            self.assertEqual(delivery_compile.init_delivery(init), 0)
            self.assertEqual(delivery_compile.approve_scope(type("Args", (), {"docs": str(docs), "delivery": delivery})), 0)
        reserved = delivery_git.reserve_delivery(project, "DLV-001")
        # reserve-delivery refuses while DLV-001 holds the Fence, so the second Integration is created directly.
        second = delivery_compile.find_delivery(docs, "DLV-002")
        reservation = delivery_git.commit_tree(
            project, reserved["target"], delivery_git.package_paths(project, second, docs, include_map=False),
            "Reserve Delivery DLV-002", {"Record": "delivery-reservation-v1", "Protocol": "1", "Delivery": "DLV-002",
                                         "Slug": "session", "Target": reserved["target"]},
            delivery_projections=True)
        delivery_git.atomic_push(project, "origin", [(delivery_git.canonical_refs("DLV-002")["integration"], "", reservation)])
        self.author_execution_topology(docs)
        waiting = second / "items" / "auth-02" / "item.md"
        props, body = delivery_compile.split_note(waiting)
        props["path_claims"] = ["src/session.py"]
        props["waits_for"] = ["AUTH-01"]
        delivery_compile.atomic_text(waiting, delivery_compile.frontmatter(props, body))
        for delivery in ("DLV-001", "DLV-002"):
            self.assertEqual(delivery_compile.approve_execution(type("Args", (), {"docs": str(docs), "delivery": delivery})), 0)
            delivery_git.publish_execution_plan(project, delivery)
        for delivery in ("DLV-001", "DLV-002") if claim_dependency else ("DLV-002",):
            delivery_git.claim_items(project, delivery)
        return project

    def refuse_waiting_start(self, project: Path, checkout: Path, expected: tuple[str, str]) -> None:
        """start-item of DLV-002's AUTH-02 refuses as expected and changes no ref or receipt."""
        before = delivery_git.run_git(project, "ls-remote", "origin")
        self.assertEqual(self.refused_finding(lambda: delivery_git.start_item(checkout, "DLV-002", "AUTH-02")), expected)
        self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), before)
        self.assertIsNone(delivery_git.read_writer_receipt(checkout, "DLV-002", "AUTH-02"))

    def test_start_item_waits_until_its_integration_holds_the_integrated_waits_for_story(self):
        """A waits_for Story is met once its Delivery merged into the target and this Delivery refreshed onto it."""
        project = self.claim_waiting_deliveries()
        waiting = ("DELIVERY_DEPENDENCY_UNMET",
                   "AUTH-02 starts only after this Integration holds these Stories integrated: AUTH-01 from DLV-001; "
                   "merge their Deliveries into the target, then refresh this one")
        self.refuse_waiting_start(project, project, waiting)
        active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        # Another host that fetched after this activation: integration moves no Fence, so that
        # host still reads the current Fence but lacks AUTH-01's integrated tip.
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        other = Path(temporary.name) / "other"
        subprocess.run(["git", "clone", "-q", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
                        str(project / "remote.git"), str(other)], check=True)
        delivery_git.run_git(other, "checkout", "-q", "--detach", "origin/agentrof/deliveries/dlv-002")
        self.commit_item_product_change(active["worktree"], "def authenticate():\n    return 'v1'\n")
        self.assertEqual(self.approve_item_evidence(active["worktree"]), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        self.refuse_waiting_start(project, project, waiting)
        self.refuse_waiting_start(project, other, waiting)
        target = delivery_git.remote_oid(project, "origin", "refs/heads/main")
        merged = delivery_git.merge_candidate(project, target, integrated["integration"], "Merge pull request #1", {})
        delivery_git.atomic_push(project, "origin", [("refs/heads/main", target, merged)])
        self.refuse_waiting_start(project, project, ("DELIVERY_TARGET_DRIFT",
                                                     "target advanced; refresh the Delivery before Item activation"))
        integration_ref = delivery_git.canonical_refs("DLV-002")["integration"]
        before_refresh = delivery_git.remote_oid(project, "origin", integration_ref)
        self.assertEqual(delivery_git.unmet_waits_for(project.resolve(), "origin", "DLV-002", before_refresh, ["AUTH-01"]),
                         (["AUTH-01 from DLV-001"], []))
        delivery_git.refresh_target(project, "DLV-002")
        started = delivery_git.start_item(project, "DLV-002", "AUTH-02")
        self.assertEqual((started["story"], started["slot"]), ("AUTH-02", "001"))

    def test_start_item_refuses_a_waits_for_story_no_delivery_is_delivering(self):
        """A Story no Delivery claimed, or one its Delivery cancelled, names a backlog revision as the way out."""
        project = self.claim_waiting_deliveries(claim_dependency=False)

        def undeliverable(reason: str) -> tuple[str, str]:
            return ("DELIVERY_DEPENDENCY_UNMET", f"AUTH-02 waits for Stories no Delivery is delivering: {reason}; "
                                                 "revise the backlog so AUTH-02 no longer depends on them")

        self.refuse_waiting_start(project, project, undeliverable("AUTH-01 was never claimed"))
        delivery_git.claim_items(project, "DLV-001")
        delivery_git.cancel_delivery(project, "DLV-001", "Authentication moves to a later Delivery")
        self.refuse_waiting_start(project, project, undeliverable("AUTH-01 was cancelled with DLV-001"))

    def merge_waited_for_delivery(self, project: Path) -> dict:
        """Deliver AUTH-01 of a claimed DLV-001 and merge its PR through merge-pr."""
        docs = project / "workspace" / "docs"
        active = delivery_git.start_item(project, "DLV-001", "AUTH-01")
        self.commit_item_product_change(active["worktree"], "def authenticate():\n    return 'v1'\n")
        self.assertEqual(self.approve_item_evidence(active["worktree"]), 0)
        delivery_git.push_item(project, "DLV-001", "AUTH-01")
        integrated = delivery_git.integrate_item(project, "DLV-001", "AUTH-01")
        self.assertEqual(delivery_compile.approve_review(type("Args", (), {
            "docs": str(docs), "delivery": "DLV-001", "reviewed_commit": integrated["integration"],
            "reviewed_integration_commit": integrated["integration"]})), 0)
        delivery_git.publish_delivery_review(project, "DLV-001")
        delivery_git.prepare_pr_creation(project, "DLV-001")
        with mock.patch("delivery_provider.GitHubProvider", self.fake_provider_type({})):
            delivery_git.open_pr(project, "DLV-001")
            return delivery_git.merge_pr(project, "DLV-001")

    def test_a_waits_for_story_whose_delivery_merged_is_met_from_its_package(self):
        """DLV-001 merged and dropped the Item ref of AUTH-01, so its package answers for AUTH-02
        of DLV-002: on its way while only the target holds the merge, met once DLV-002 refreshed
        onto it. The PR head alone holds the package but not the merge, so it proves nothing (#286)."""
        project = self.claim_waiting_deliveries()
        merged = self.merge_waited_for_delivery(project)
        self.assertEqual(self.coordination_branches(project),
                         ["agentrof/deliveries/dlv-002", "agentrof/fence", "agentrof/items/auth-02"])
        self.assertEqual(delivery_git.merged_story_owners(project.resolve(), merged["reviewed_integration"], ["AUTH-01"]), {})
        integration_ref = delivery_git.canonical_refs("DLV-002")["integration"]
        before_refresh = delivery_git.remote_oid(project, "origin", integration_ref)
        self.assertEqual(delivery_git.unmet_waits_for(project.resolve(), "origin", "DLV-002", before_refresh, ["AUTH-01"]),
                         (["AUTH-01 from DLV-001"], []))
        delivery_git.refresh_target(project, "DLV-002")
        refreshed = delivery_git.remote_oid(project, "origin", integration_ref)
        self.assertEqual(delivery_git.unmet_waits_for(project.resolve(), "origin", "DLV-002", refreshed, ["AUTH-01"]),
                         ([], []))
        started = delivery_git.start_item(project, "DLV-002", "AUTH-02")
        self.assertEqual((started["story"], started["slot"]), ("AUTH-02", "001"))

    def advance_target(self, project: Path) -> str:
        """Move the target one commit past its tip without changing a file."""
        target = delivery_git.remote_oid(project, "origin", "refs/heads/main")
        advanced = delivery_git.run_git(project, "commit-tree", target + "^{tree}", "-p", target,
                                        "-m", "Unrelated target change")
        delivery_git.atomic_push(project, "origin", [("refs/heads/main", target, advanced)])
        return advanced

    def reserve_overlapping_delivery(self, project: Path, plan: bool = True) -> None:
        """Reserve DLV-003 for AUTH-01, the Story of DLV-001, on the current target, and publish its
        plan unless *plan* is false."""
        docs = project / "workspace" / "docs"
        init = type("Args", (), {"docs": str(docs), "id": "DLV-003", "slug": "again", "goal": "Deliver AUTH-01 again",
                                 "outcome": None, "target_branch": "main", "story": ["AUTH-01"]})
        self.assertEqual(delivery_compile.init_delivery(init), 0)
        self.assertEqual(delivery_compile.approve_scope(type("Args", (), {"docs": str(docs), "delivery": "DLV-003"})), 0)
        target = delivery_git.remote_oid(project, "origin", "refs/heads/main")
        package = delivery_compile.find_delivery(docs, "DLV-003")
        reservation = delivery_git.commit_tree(
            project, target, delivery_git.package_paths(project, package, docs, include_map=False),
            "Reserve Delivery DLV-003", {"Record": "delivery-reservation-v1", "Protocol": "1", "Delivery": "DLV-003",
                                         "Slug": "again", "Target": target},
            delivery_projections=True)
        delivery_git.atomic_push(project, "origin", [(delivery_git.canonical_refs("DLV-003")["integration"], "", reservation)])
        if not plan:
            return
        self.author_execution_topology(docs, "DLV-003")
        self.assertEqual(delivery_compile.approve_execution(type("Args", (), {"docs": str(docs), "delivery": "DLV-003"})), 0)
        delivery_git.publish_execution_plan(project, "DLV-003")

    def test_claim_refuses_a_story_a_merged_delivery_delivered(self):
        """A merged Delivery keeps no Item ref, so claim-items finds AUTH-01 integrated in the
        merged package of DLV-001 and refuses it to a later Delivery instead of claiming it again (#286)."""
        project = self.claim_waiting_deliveries()
        self.merge_waited_for_delivery(project)
        self.reserve_overlapping_delivery(project)
        delivery_git.refresh_target(project, "DLV-003")
        before = delivery_git.run_git(project, "ls-remote", "origin")
        self.assertEqual(self.refused_finding(lambda: delivery_git.claim_items(project, "DLV-003")),
                         ("DELIVERY_CLAIM_CONFLICT", "story is already delivered by DLV-001: AUTH-01"))
        self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), before)

    def test_refresh_leaves_another_deliverys_claim_of_the_same_story_alone(self):
        """DLV-003 holds AUTH-01, which DLV-001 claimed. Its refresh of a converged Integration used
        to re-issue that claim under DLV-003 and take the Story over; it now leaves the claim to
        DLV-001, and claim-items refuses AUTH-01 naming DLV-001 (#288)."""
        project = self.claim_waiting_deliveries()
        claim_ref = delivery_git.canonical_refs("DLV-001", "AUTH-01")["item"]
        claimed = delivery_git.remote_oid(project, "origin", claim_ref)
        self.advance_target(project)
        delivery_git.refresh_target(project, "DLV-002")
        self.reserve_overlapping_delivery(project)
        refreshed = delivery_git.refresh_target(project, "DLV-003")
        self.assertEqual((refreshed["changed"], refreshed["claims_refreshed"]), (False, []))
        self.assertEqual(delivery_git.remote_oid(project, "origin", claim_ref), claimed)
        self.assertEqual(self.refused_finding(lambda: delivery_git.claim_items(project, "DLV-003")),
                         ("DELIVERY_CLAIM_CONFLICT", "story is already claimed by DLV-001: AUTH-01"))
        self.assertEqual(delivery_git.remote_oid(project, "origin", claim_ref), claimed)

    def test_refresh_after_a_target_advance_passes_over_another_deliverys_claim(self):
        """After a target advance, the refresh of DLV-003 used to read its own package from the
        Item tip of DLV-001 and fail with a raw Git error. Only DLV-003's own claims guard its
        paths now, so the refresh lands and DLV-001's claim stays where it was (#288)."""
        project = self.claim_waiting_deliveries()
        claim_ref = delivery_git.canonical_refs("DLV-001", "AUTH-01")["item"]
        claimed = delivery_git.remote_oid(project, "origin", claim_ref)
        self.reserve_overlapping_delivery(project)
        self.advance_target(project)
        self.assertTrue(delivery_git.refresh_target(project, "DLV-003")["changed"])
        self.assertEqual(delivery_git.remote_oid(project, "origin", claim_ref), claimed)

    def test_scope_revision_stays_open_beside_another_deliverys_claim(self):
        """DLV-003 claimed nothing, so revising its scope, the way to hand AUTH-01 back to DLV-001,
        used to be refused as a revision after an Item claim (#288)."""
        project = self.claim_waiting_deliveries()
        self.reserve_overlapping_delivery(project, plan=False)
        revised = delivery_git.revise_unclaimed_scope(project, "DLV-003")
        self.assertEqual(delivery_git.trailer(delivery_git.commit_message(project, revised["integration"]), "Record"),
                         "delivery-scope-revised-v1")

    def test_cancellation_passes_over_another_deliverys_claim(self):
        """Cancelling DLV-003 used to read its package from the Item tip of DLV-001 and fail with a
        raw Git error. AUTH-01 is now a Story DLV-003 never started, and DLV-001 keeps its claim (#288)."""
        project = self.claim_waiting_deliveries()
        claim_ref = delivery_git.canonical_refs("DLV-001", "AUTH-01")["item"]
        claimed = delivery_git.remote_oid(project, "origin", claim_ref)
        self.reserve_overlapping_delivery(project)
        cancelled = delivery_git.cancel_delivery(project, "DLV-003", "AUTH-01 stays with DLV-001")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(delivery_git.remote_oid(project, "origin", claim_ref), claimed)

    def test_worktree_file_holds_its_blob_as_git_stores_it(self):
        """A worktree file holds a committed blob when Git would store it as that blob. A checkout
        that converted line endings still holds it; the same bytes with the conversion off, a
        changed, missing or linked file and a directory do not (#257)."""
        committed = b"---\nstatus: active\n---\n\n# Item\n"
        converted = committed.replace(b"\n", b"\r\n")
        with temporary_directory() as temporary:
            root = Path(temporary)
            init_repository(root)
            for key, value in (("user.email", "test@example.com"), ("user.name", "Test"),
                               ("core.autocrlf", "true")):
                delivery_git.run_git(root, "config", key, value)
            note, twin = root / "notes" / "item.md", root / "notes" / "twin.md"
            note.parent.mkdir()
            note.write_bytes(committed)
            delivery_git.run_git(root, "add", "notes/item.md")
            delivery_git.run_git(root, "commit", "-qm", "Item")
            oid = delivery_git.run_git(root, "rev-parse", "HEAD:notes/item.md")
            note.unlink()
            delivery_git.run_git(root, "checkout", "--", "notes/item.md")
            self.assertEqual(note.read_bytes(), converted)
            self.assertTrue(delivery_git.worktree_holds_blob(root, "notes/item.md", oid))
            delivery_git.run_git(root, "config", "core.autocrlf", "false")
            self.assertFalse(delivery_git.worktree_holds_blob(root, "notes/item.md", oid))
            delivery_git.run_git(root, "config", "core.autocrlf", "true")
            note.write_bytes(converted.replace(b"active", b"paused"))
            self.assertFalse(delivery_git.worktree_holds_blob(root, "notes/item.md", oid))
            note.unlink()
            self.assertFalse(delivery_git.worktree_holds_blob(root, "notes/item.md", oid))
            note.mkdir()
            self.assertFalse(delivery_git.worktree_holds_blob(root, "notes/item.md", oid))
            note.rmdir()
            twin.write_bytes(committed)
            self.symlink_or_skip(note, "twin.md")
            self.assertFalse(delivery_git.worktree_holds_blob(root, "notes/item.md", oid))

    def test_worktree_file_holds_a_crlf_blob_that_git_add_keeps(self):
        """Under core.autocrlf=true a blob committed with CRLF checks out unchanged, and git add
        keeps the file as it is because its blob already holds CRLF, where the clean filter alone
        would store LF. The unchanged checkout holds its blob; the same content rewritten with LF
        does not, since git add then stores an LF blob (#247)."""
        record = b"---\r\nstatus: active\r\n---\r\n\r\n# Item\r\n"
        with temporary_directory() as temporary:
            root = Path(temporary)
            init_repository(root)
            for key, value in (("user.email", "test@example.com"), ("user.name", "Test"),
                               ("core.autocrlf", "false")):
                delivery_git.run_git(root, "config", key, value)
            note = root / "notes" / "item.md"
            note.parent.mkdir()
            note.write_bytes(record)
            delivery_git.run_git(root, "add", "notes/item.md")
            delivery_git.run_git(root, "commit", "-qm", "Item with CRLF")
            oid = delivery_git.run_git(root, "rev-parse", "HEAD:notes/item.md")
            delivery_git.run_git(root, "config", "core.autocrlf", "true")
            note.unlink()
            delivery_git.run_git(root, "checkout", "--", "notes/item.md")
            self.assertEqual(note.read_bytes(), record)

            def stored() -> str:
                delivery_git.run_git(root, "add", "notes/item.md")
                return delivery_git.run_git(root, "ls-files", "--stage", "--", "notes/item.md").split()[1]

            self.assertTrue(delivery_git.worktree_holds_blob(root, "notes/item.md", oid))
            self.assertEqual(stored(), oid)
            note.write_bytes(record.replace(b"\r\n", b"\n"))
            self.assertFalse(delivery_git.worktree_holds_blob(root, "notes/item.md", oid))
            self.assertNotEqual(stored(), oid)

    def test_delivery_path_readers_keep_a_name_that_holds_a_carriage_return(self):
        """A text-mode pipe turns a carriage return into a newline, so the pending-path and
        index-flag readers reported an untracked macOS "Icon\\r" as "Icon\\n". They read Git's
        NUL-separated bytes instead (#279)."""
        if os.name == "nt":
            self.skipTest("POSIX file names: native Windows refuses a carriage return in a file name")
        with temporary_directory() as temporary:
            root = Path(temporary)
            init_repository(root, initial_branch="main")
            for key, value in (("user.email", "test@example.com"), ("user.name", "Test")):
                delivery_git.run_git(root, "config", key, value)
            tracked = "notes/Icon\r"
            (root / "notes").mkdir()
            (root / tracked).write_bytes(b"note\n")
            delivery_git.run_git(root, "add", "--", tracked)
            delivery_git.run_git(root, "commit", "-qm", "Record the note")
            (root / "Icon\r").write_bytes(b"")
            self.assertEqual(delivery_git.worktree_pending_paths(root, root), {"Icon\r"})
            delivery_git.run_git(root, "update-index", "--skip-worktree", "--", tracked)
            with self.assertRaises(RuntimeError) as hidden:
                delivery_git.require_visible_item_index(root)
            self.assertEqual(str(hidden.exception), "DELIVERY_WORKTREE_UNSAFE: Item index flags hide tracked paths "
                                                    "from verification: " + json.dumps([tracked]))

    def test_cancellation_revert_restores_a_path_that_starts_with_a_colon(self):
        """revert_merge_candidate looks each changed path up with git ls-tree, where a leading ":"
        starts pathspec magic: ":x.py" named "x.py", the lookup found nothing, and the revert
        deleted the file instead of restoring it. Its lookups take every path literally (#279)."""
        if os.name == "nt":
            self.skipTest("POSIX file names: native Windows refuses a colon in a file name")
        with temporary_directory() as temporary:
            root = Path(temporary)
            init_repository(root, initial_branch="main")
            for key, value in (("user.email", "test@example.com"), ("user.name", "Test")):
                delivery_git.run_git(root, "config", key, value)
            commits = []
            for text in ("X = 'before'\n", "X = 'item'\n"):
                (root / ":x.py").write_text(text, encoding="utf-8")
                delivery_git.run_git(root, "--literal-pathspecs", "add", "--", ":x.py")
                delivery_git.run_git(root, "commit", "-qm", "Change :x.py")
                commits.append(delivery_git.run_git(root, "rev-parse", "HEAD"))
            before, item = commits
            # The coordinator reverts in the main worktree, which need not hold the Item's files.
            (root / ":x.py").unlink()
            merge = delivery_git.run_git(root, "commit-tree", item + "^{tree}", "-p", before, "-p", item,
                                         "-m", "Integrate the Item")
            reverted = delivery_git.revert_merge_candidate(root, merge, merge, "Revert the Item", {"Record": "fixture-v1"})
            self.assertEqual(delivery_git.git_paths(root, "ls-tree", "-z", "--name-only", reverted), [":x.py"])
            self.assertEqual(delivery_git.run_git(root, "cat-file", "blob", reverted + "::x.py"), "X = 'before'")

    @windows_text_pipes()
    def test_push_item_publishes_an_item_whose_checkout_converted_line_endings(self):
        """Git for Windows converts line endings on checkout by default (core.autocrlf=true), so
        the started Item's record ends its lines with CRLF while the commit holds LF. push-item
        reads the record as Git stores it and publishes the Item (#257). The record is committed
        through the runner's text pipes, which must not give the commit CRLF of its own (#247)."""
        project, _docs, _directory, item, _reserved = self.prepare_execution_with_draft_reserved_contracts(False)
        delivery_git.run_git(project, "config", "core.autocrlf", "true")
        delivery_git.publish_execution_plan(project, "DLV-001")
        delivery_git.claim_items(project, "DLV-001")
        worktree = Path(delivery_git.start_item(project, "DLV-001", "AUTH-01")["worktree"])
        self.assertIn(b"\r\n", (worktree / item.relative_to(project)).read_bytes())
        product = self.commit_item_product_change(str(worktree), "def authenticate():\n    return True\n")
        self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
        self.assertEqual(delivery_git.push_item(project, "DLV-001", "AUTH-01")["product_tip"], product)

    def test_delivery_blobs_and_commit_messages_keep_their_bytes_through_windows_text_pipes(self):
        """Delivery hands Git every blob and commit message as exact UTF-8 bytes. Through a
        text-mode pipe native Windows would store CRLF for each newline and refuse a character
        its ANSI code page lacks, so its records would differ from every other host's (#247)."""
        record = "---\ntitle: Oturum açma, şifre\n---\n\n# Item\n"
        subject = "Record the Item, şifre"
        trailers = {"Record": "fixture-v1"}
        with temporary_directory() as temporary:
            root = Path(temporary)
            init_repository(root, initial_branch="main")
            for key, value in (("user.email", "test@example.com"), ("user.name", "Test")):
                delivery_git.run_git(root, "config", key, value)
            (root / "README.md").write_bytes(b"fixture\n")
            delivery_git.run_git(root, "add", "README.md")
            delivery_git.run_git(root, "commit", "-qm", "Start")
            base = delivery_git.run_git(root, "rev-parse", "HEAD")
            with windows_text_pipes():
                replaced = delivery_git.commit_replacements(root, base, {"notes/item.md": record}, subject, trailers)
                empty = delivery_git.commit_tree(root, base, [], subject, trailers)
                merged = delivery_git.merge_candidate(root, replaced, empty, subject, trailers)
                reverted = delivery_git.revert_merge_candidate(root, merged, merged, subject, trailers)

            def raw(*args: str) -> bytes:
                return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True).stdout

            self.assertEqual(raw("cat-file", "blob", replaced + ":notes/item.md"), record.encode("utf-8"))
            for commit in (replaced, empty, merged, reverted):
                self.assertEqual(raw("cat-file", "commit", commit).split(b"\n\n", 1)[1],
                                 (subject + "\n\nAgentrof-Record: fixture-v1\n").encode("utf-8"))

    def test_stamp_check_compares_an_item_record_committed_with_crlf_byte_for_byte(self):
        """The stamp check keeps every other Item byte. A record whose lines end with CRLF, as a
        text-mode write on native Windows commits it under setup's workspace/docs/** -text rule,
        is refused as a change beyond the stamp, and a record without a closing delimiter line
        as invalid frontmatter, where both used to stop the check with a ValueError (#247)."""
        project, worktree, item, active = self.prepare_stamped_architecture_item()
        stamped = delivery_git.run_git(worktree, "rev-parse", "HEAD")
        relative_delivery, relative_item = (path.relative_to(worktree).as_posix() for path in (item.parents[2], item))
        record = item.read_text(encoding="utf-8")
        props, _body = delivery_compile.split_note(item)
        bare = delivery_compile.frontmatter(dict(props, source_hash=delivery_compile.content_hash(props, "")), "")
        for label, text, refusal in (
            ("stamp", record, None),
            ("stamp with CRLF", record.replace("\n", "\r\n"), "beyond its Architecture stamp"),
            ("no closing delimiter line", bare.rstrip("\n"), "no closing delimiter line"),
        ):
            with self.subTest(label=label):
                after = delivery_git.commit_replacements(project, stamped, {relative_item: text}, "Commit the record", {})
                self.assertEqual(subprocess.run(["git", "cat-file", "blob", f"{after}:{relative_item}"], cwd=project,
                                                capture_output=True, check=True).stdout, text.encode("utf-8"))

                def check():
                    delivery_git.require_item_publication_controls(project, active["item"], after,
                                                                   relative_delivery, relative_item)

                if refusal is None:
                    check()
                else:
                    with self.assertRaisesRegex(RuntimeError, refusal):
                        check()

    def test_delivery_reads_git_output_as_utf8_through_windows_text_pipes(self):
        """Git prints paths and blobs in UTF-8. A text-mode pipe without an encoding reads
        them in the ANSI code page on native Windows, which turned the em dash of a Delivery
        map row into mojibake there, so every Delivery reader names UTF-8 (#247)."""
        note = "---\ntitle: Oturum açma, güvenlik\n---\n\nŞifre ve ğ, ı, ö harfleri.\n"
        with temporary_directory() as temporary:
            root = Path(temporary)
            init_repository(root, initial_branch="main")
            for key, value in (("user.email", "test@example.com"), ("user.name", "Test")):
                delivery_git.run_git(root, "config", key, value)
            (root / "notes").mkdir()
            (root / "notes" / "oturum-açma.md").write_bytes(note.encode("utf-8"))
            delivery_git.run_git(root, "add", "notes")
            delivery_git.run_git(root, "commit", "-qm", "Record the note")
            (root / "notes" / "şifre.md").write_bytes(note.encode("utf-8"))
            with windows_text_pipes():
                shown = delivery_git.run_git(root, "show", "HEAD:notes/oturum-açma.md")
                provider_shown = delivery_provider.run_git(root, "show", "HEAD:notes/oturum-açma.md")
                props, body = delivery_git.split_remote_note(root, "HEAD", "notes/oturum-açma.md",
                                                             delivery_compile.split_note)
                pending = delivery_git.worktree_pending_paths(root, root)
            self.assertEqual((shown, provider_shown), (note.strip(), note.strip()))
            self.assertEqual((props["title"], body), ("Oturum açma, güvenlik", "Şifre ve ğ, ı, ö harfleri."))
            self.assertEqual(pending, {"notes/şifre.md"})

    def test_fixture_command_pipes_preserve_text_aliases_and_host_version_probes(self):
        import platform
        aliases = ({"text": True}, {"universal_newlines": True}, {"encoding": "utf-8"},
                   {"encoding": "locale"}, {"errors": "replace"})
        command = "fixture-test-command"
        with windows_text_pipes(), approved_fixture_shell_commands({command}):
            for options in aliases:
                with self.subTest(options=options):
                    result = subprocess.run(command, shell=True, capture_output=True, **options)
                    self.assertEqual((result.stdout, result.stderr), ("Fixture command passed\n", ""))
                    self.assertEqual(subprocess.check_output(command, shell=True, **options), "Fixture command passed\n")
                    actual = subprocess.check_output("echo Actual host probe", shell=True, **options)
                    self.assertEqual(actual.strip(), "Actual host probe")
            self.assertEqual(subprocess.check_output(command, shell=True), b"Fixture command passed\n")

        for code_page, content in (("cp1252", "café €\n"), ("cp1254", "şifre ı\n")):
            with self.subTest(code_page=code_page):
                encoded = content.replace("\n", "\r\n").encode(code_page)

                def locale_probe(command, *args, **kwargs):
                    self.assertEqual(command, ["fixture-locale-probe"])
                    self.assertFalse(any(kwargs.get(key) for key in ("text", "universal_newlines", "encoding", "errors")))
                    self.assertEqual(kwargs["input"], encoded)
                    return subprocess.CompletedProcess(command, 0, encoded, "é\r\n".encode(code_page))

                with mock.patch.object(subprocess, "run", side_effect=locale_probe), windows_text_pipes(code_page):
                    result = subprocess.run(["fixture-locale-probe"], input=content,
                                            capture_output=True, encoding="locale")
                    self.assertEqual((result.stdout, result.stderr), (content, "é\n"))
                    with self.assertRaises(LookupError):
                        subprocess.run(["fixture-locale-probe"], input=content,
                                       capture_output=True, encoding="fixture-unknown-codec")

        def version_probe(command, *args, **kwargs):
            self.assertEqual(command, "ver")
            self.assertTrue(kwargs["shell"])
            # The pipe emulator delegates a binary call to its native backend.
            self.assertFalse(any(kwargs.get(key) for key in ("text", "universal_newlines", "encoding", "errors")))
            return subprocess.CompletedProcess(command, 0, b"Microsoft Windows [Version 10.0.20348]\r\n")
        with mock.patch.object(subprocess, "run", side_effect=version_probe), windows_text_pipes(), \
                approved_fixture_shell_commands({command}):
            # Platform probes use universal_newlines, text, or encoding="locale"
            # across supported Python versions and must receive strings.
            self.assertEqual(platform._syscmd_ver(supported_platforms=(sys.platform,)),
                             ("Microsoft", "Windows", "10.0.20348"))

    def test_command_results_reach_a_windows_code_page_stdout_as_utf8(self):
        """A redirected stdout on native Windows encodes in the ANSI code page, cp1252 on the
        runner, which lacks ş, ğ and ı. The coordinator's result envelope and the compiler's
        JSON results go out as UTF-8 bytes, so a result that names such a letter reaches its
        reader instead of raising after the command ran (#247)."""
        def windows_stdout() -> io.TextIOWrapper:
            return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")

        with temporary_directory() as temporary:
            docs = Path(temporary) / "şifre" / "workspace" / "docs"
            item = docs / "delivery" / "deliveries" / "dlv-001-auth" / "items" / "auth-01" / "item.md"
            item.parent.mkdir(parents=True)
            (item.parents[2] / "delivery.md").write_bytes(b"---\ntype: delivery\nid: DLV-001\n---\n\n# Delivery\n")
            item.write_bytes(b"---\ntype: note\n---\n\n# Item\n")
            stdout = windows_stdout()
            with mock.patch.object(sys, "stdout", stdout):
                code = delivery_compile.check_delivery(type("Args", (), {"docs": str(docs), "delivery": "DLV-001"}))
            result = json.loads(stdout.buffer.getvalue().decode("utf-8"))
            self.assertEqual(code, 1)
            self.assertIn(f"{item.resolve()} type must be delivery-item", result["errors"])
        stdout = windows_stdout()
        with mock.patch.object(delivery_git, "preflight",
                               return_value={"ok": False, "errors": ["DELIVERY_INPUT_INVALID: şifre, ğ, ı"]}), \
                mock.patch.object(sys, "stdout", stdout):
            code = delivery_git.main(["preflight", "--delivery", "DLV-001"])
        envelope = json.loads(stdout.buffer.getvalue().decode("utf-8"))
        self.assertEqual((code, [finding["message"] for finding in envelope["findings"]]), (1, ["şifre, ğ, ı"]))

    def test_delivery_scripts_name_utf8_for_every_process_pipe(self):
        """Every process pipe in the Delivery scripts that carries text names UTF-8, and none takes
        text on stdin. Without an encoding a pipe reads and writes the locale's code page, the
        ANSI code page on native Windows, and a text-mode stdin there writes CRLF for every
        newline, so input goes over as UTF-8 bytes. This covers the pipes no fixture reaches,
        such as the gh calls (#247)."""
        import ast
        scripts = ROOT / "plugins" / "software-engineering-team" / "scripts"
        unsafe = []
        for name in ("delivery_compile.py", "delivery_git.py", "delivery_governance.py", "delivery_provider.py"):
            for call in ast.walk(ast.parse((scripts / name).read_text(encoding="utf-8"))):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and isinstance(call.func.value, ast.Name) and call.func.value.id == "subprocess"):
                    continue
                keywords = {keyword.arg: keyword.value for keyword in call.keywords}
                if not keywords.keys() & {"text", "universal_newlines", "encoding", "errors"}:
                    continue
                encoding = keywords.get("encoding")
                if "input" in keywords or not (isinstance(encoding, ast.Constant) and encoding.value == "utf-8"):
                    unsafe.append(f"{name}:{call.lineno}")
        self.assertEqual(unsafe, [])

    def test_push_item_refuses_product_paths_outside_the_item_path_claims(self):
        """A claim covers its path and every path below it. The vault keeps its own rules,
        and what the Item's integration base carries is not the Item's change."""
        project, worktree, item, active = self.prepare_stamped_architecture_item()
        clean = delivery_git.run_git(worktree, "rev-parse", "HEAD")
        baseline = delivery_git.run_git(project, "ls-remote", "origin")

        def write(relative):
            path = worktree / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("unclaimed\n", encoding="utf-8")

        for label, change, outside in (
            ("added", lambda: write("src/session.py"), "src/session.py"),
            ("beside the claim", lambda: write("src/auth.py.orig"), "src/auth.py.orig"),
            ("deleted", lambda: (worktree / "README.md").unlink(), "README.md"),
            # A text-mode pipe read this name back with a newline for its carriage return (#279).
            ("carriage return", lambda: write("src/Icon\r.txt"), "src/Icon\r.txt"),
        ):
            with self.subTest(label=label):
                if label == "carriage return" and os.name == "nt":
                    self.skipTest("POSIX file names: native Windows refuses a carriage return in a file name")
                delivery_git.run_git(worktree, "reset", "--hard", clean)
                delivery_git.run_git(worktree, "clean", "-fd")
                change()
                delivery_git.run_git(worktree, "add", "-A")
                delivery_git.run_git(worktree, "commit", "-qm", "Change a path the Item does not claim")
                self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
                code, message = self.refused_finding(lambda: delivery_git.push_item(project, "DLV-001", "AUTH-01"))
                self.assertEqual((code, message), ("DELIVERY_PATH_CLAIM_EXCEEDED",
                                                   "the Item's product change lies outside its path claims: " + outside))
                self.assertEqual(delivery_git.run_git(project, "ls-remote", "origin"), baseline)

        # A refreshed Integration brings product content the Item never claimed; its
        # writer takes that Integration as the Item's new base and still publishes.
        delivery_git.run_git(worktree, "reset", "--hard", clean)
        delivery_git.run_git(worktree, "clean", "-fd")
        integration_ref = delivery_git.canonical_refs("DLV-001")["integration"]
        base = delivery_git.remote_oid(project, "origin", integration_ref)
        (project / "notes").mkdir()
        (project / "notes/target.txt").write_text("carried by the target\n", encoding="utf-8")
        carried = delivery_git.commit_tree(project, base, ["notes/target.txt"], "Carry target content", {})
        delivery_git.atomic_push(project, "origin", [(integration_ref, base, carried)])
        package = item.parents[2]
        relative = {name: path.relative_to(worktree).as_posix() for name, path in (
            ("plan", package / "execution-plan.md"), ("scope", package / "delivery.md"), ("item", item))}
        product = self.converge_on_integration(worktree, item, carried, relative)
        self.assertEqual(delivery_git.run_git(worktree, "show", product + ":notes/target.txt"), "carried by the target")
        self.assertEqual(self.approve_item_evidence(str(worktree)), 0)
        self.assertEqual(delivery_git.push_item(project, "DLV-001", "AUTH-01")["product_tip"], product)

    def lease_remote(self) -> tuple[Path, Path, list[str]]:
        """A checkout with three commits and a bare remote that holds its main branch."""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(remove_temporary, temporary)
        root, remote = Path(temporary.name) / "project", Path(temporary.name) / "remote.git"
        init_repository(root, initial_branch="main")
        init_repository(remote, bare=True)
        delivery_git.run_git(root, "config", "user.email", "test@example.com")
        delivery_git.run_git(root, "config", "user.name", "Test")
        oids = []
        for name in ("first", "second", "third"):
            (root / f"{name}.txt").write_text(name + "\n", encoding="utf-8")
            delivery_git.run_git(root, "add", f"{name}.txt")
            delivery_git.run_git(root, "commit", "-qm", name)
            oids.append(delivery_git.run_git(root, "rev-parse", "HEAD"))
        delivery_git.run_git(root, "remote", "add", "origin", str(remote))
        delivery_git.run_git(root, "push", "-q", "origin", "main")
        return root, remote, oids

    def test_refused_atomic_push_names_the_lease_it_lost(self):
        """The refetched refs, never Git's translated words, name the lease that no longer holds."""
        root, _remote, (first, second, third) = self.lease_remote()
        refs = delivery_git.canonical_refs("DLV-001", "AUTH-01", 1)
        released = delivery_git.canonical_refs("DLV-001", "AUTH-01", 2)["slot"]
        delivery_git.atomic_push(root, "origin", [(refs["fence"], "", first), (refs["item"], "", first),
                                                  (refs["slot"], "", first)])
        before = delivery_git.run_git(root, "ls-remote", "origin")
        for label, code, updates, moved in (
            ("fence", "DELIVERY_FENCE_LEASE_LOST",
             [(refs["fence"], second, third), (refs["item"], first, third)],
             f"{refs['fence']} is {first}, leased as {second}"),
            ("item", "DELIVERY_LEASE_LOST",
             [(refs["fence"], first, third), (refs["item"], second, third), (refs["slot"], first, third)],
             f"{refs['item']} is {first}, leased as {second}"),
            ("slot taken", "DELIVERY_LEASE_LOST",
             [(refs["fence"], first, third), (refs["slot"], "", third)],
             f"{refs['slot']} is {first}, leased as absent"),
            ("slot released", "DELIVERY_LEASE_LOST",
             [(refs["fence"], first, third), (released, first, "")],
             f"{released} is absent, leased as {first}"),
        ):
            with self.subTest(label=label):
                found, message = self.refused_finding(lambda: delivery_git.atomic_push(root, "origin", updates))
                self.assertEqual(found, code)
                self.assertIn(moved, message)
                self.assertEqual(delivery_git.run_git(root, "ls-remote", "origin"), before)

    def test_atomic_push_that_may_have_landed_is_uncertain(self):
        """A leased ref that holds the pushed candidate, or moved on from a history that holds it, shows
        the push may have landed before its response was lost, never a lease that changed no ref."""
        root, remote, (first, second, third) = self.lease_remote()
        fence = delivery_git.canonical_refs("DLV-001")["fence"]
        delivery_git.atomic_push(root, "origin", [(fence, "", first)])
        uncertain = ("the remote may have taken the atomic push before its response was lost, "
                     "so read the refs again before any retry: ")
        with self.lost_push_response():
            finding = self.refused_finding(lambda: delivery_git.atomic_push(root, "origin", [(fence, first, second)]))
        self.assertEqual(finding, ("DELIVERY_TRANSACTION_UNCERTAIN", uncertain + f"{fence} holds the pushed {second}"))
        # Another host moves the Fence on from the landed candidate, to a commit this checkout lacks.
        other = root.parent / "other"
        subprocess.run(["git", "clone", "-q", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
                        str(remote), str(other)], check=True)
        identity = ["-c", "user.email=test@example.com", "-c", "user.name=Test"]
        moved = []

        def advance_from_another_host():
            delivery_git.run_git(other, "fetch", "-q", "origin", fence)
            moved.append(delivery_git.run_git(other, *identity, "commit-tree", third + "^{tree}", "-p", third,
                                              "-m", "Another coordinator"))
            delivery_git.run_git(other, "push", "-q", "origin", f"--force-with-lease={fence}:{third}",
                                 f"{moved[0]}:{fence}")

        with self.lost_push_response(after_landing=advance_from_another_host):
            finding = self.refused_finding(lambda: delivery_git.atomic_push(root, "origin", [(fence, second, third)]))
        self.assertEqual(finding, ("DELIVERY_TRANSACTION_UNCERTAIN",
                                   uncertain + f"{fence} is {moved[0]}, whose history holds the pushed {third}"))

    def test_remote_without_atomic_push_support_is_named_and_other_refusals_keep_their_words(self):
        root, remote, (first, second, _third) = self.lease_remote()
        fence = delivery_git.canonical_refs("DLV-001")["fence"]
        delivery_git.atomic_push(root, "origin", [(fence, "", first)])
        before = delivery_git.run_git(root, "ls-remote", "origin")
        delivery_git.run_git(remote, "config", "receive.advertiseAtomic", "false")
        code, _message = self.refused_finding(lambda: delivery_git.atomic_push(root, "origin", [(fence, first, second)]))
        self.assertEqual(code, "DELIVERY_REMOTE_ATOMIC_UNSUPPORTED")
        self.assertEqual(delivery_git.run_git(root, "ls-remote", "origin"), before)
        # With every lease holding on a remote that pushes atomically, an unproven cause keeps Git's report.
        delivery_git.run_git(remote, "config", "receive.advertiseAtomic", "true")
        hook = remote / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        code, _message = self.refused_finding(lambda: delivery_git.atomic_push(root, "origin", [(fence, first, second)]))
        self.assertEqual(code, "DELIVERY_INPUT_INVALID")
        self.assertEqual(delivery_git.run_git(root, "ls-remote", "origin"), before)


# Changing an environment or any setup callable cannot silently reuse a seed
# produced outside that context. Tests can also request use_cache=False.
_PR_FIXTURE_ENVIRONMENT = dict(os.environ)
_PR_FIXTURE_CWD = os.getcwd()
_PR_FIXTURE_BINDINGS = [
    (module, name, value)
    for module in (delivery_git, delivery_compile, delivery_governance, backlog_compile,
                   operation_compile, architecture_compile, setup_check, stage_package, vault_check)
    for name, value in vars(module).items() if callable(value)
]
_PR_FIXTURE_BINDINGS.extend([
    (subprocess, "run", subprocess.run),
    (pathlib.PurePath, "relative_to", pathlib.PurePath.relative_to),
    (sys.modules[__name__], "make_approved_backlog", make_approved_backlog),
    (sys.modules[__name__], "write_pull_request_workflow", write_pull_request_workflow),
    (sys.modules[__name__], "init_repository", init_repository),
])
_PR_FIXTURE_METHODS = {name: getattr(DeliveryGitTests, name) for name in (
    "build_pre_start_fixture", "make_project", "reserve_scope", "author_execution_topology",
    "approve_verification_contract", "approve_governance",
    "build_execution_fixture", "prepare_execution_with_draft_reserved_contracts",
)}


if __name__ == "__main__":
    unittest.main()
