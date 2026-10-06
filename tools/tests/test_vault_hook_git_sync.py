from __future__ import annotations

import io
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import unittest
from tools.tests.levels import integration
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from tools.tests import test_vault_hook as hook_tests
from tools.tests.git_fixture import init_repository
import delivery_git


@integration
class VaultHookGitSyncTests(unittest.TestCase):
    def setUp(self):
        self.hook = hook_tests.load_hook()
        hook_tests.isolate_recovery_root(self)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve() / "consumer"
        self.root.mkdir()
        self.docs, _ = hook_tests.VaultHookShellContractTests.project(self.root)
        init_repository(self.root)
        self.git_path = str(Path(shutil.which("git")).absolute())
        self.git("config", "core.autocrlf", "false")
        self.git("config", "user.name", "Synthetic fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.path = self.docs / "experience-design/_ledger/application-revisions.json"
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'{"revision": 1}\n')
        self.commit("Initial synthetic source")
        self.old = self.git("rev-parse", "HEAD")
        self.path.write_bytes(b'{"revision": 2}\n')
        self.commit("Approved synthetic source")
        self.target = self.git("rev-parse", "HEAD")
        self.expected = self.path.read_bytes()
        remote = Path(self.temporary.name) / "remote.git"
        init_repository(remote, bare=True)
        self.git("remote", "add", "origin", str(remote))
        self.git("push", "origin", "HEAD:refs/heads/main")
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        self.set_fence(self.target)
        self.compiler = mock.patch.object(
            self.hook.experience_application_check, "compile_application",
            return_value=({}, []),
        )
        self.compiler.start()
        self.addCleanup(self.compiler.stop)
        self.vault = mock.patch.object(self.hook.vault_check, "main", return_value=0)
        self.vault.start()
        self.addCleanup(self.vault.stop)

    def git(self, *args, input=None):
        result = subprocess.run(
            [self.git_path if hasattr(self, "git_path") else "git", *args],
            cwd=self.root, input=input, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, message):
        self.git("add", ".")
        self.git("commit", "-m", message)

    def set_fence(self, target):
        values = {key: "none" for key in delivery_git.FENCE_CANONICAL_KEYS}
        values.update(Mode="open", Epoch=delivery_git.epoch_token(), Target=target)
        fence = delivery_git._fence_child(self.root, target, values, "Synthetic handoff")
        self.git("push", "origin", fence + ":refs/heads/agentrof/fence")

    def payload(self, *args, event="git-sync"):
        tokens = [self.git_path, *args]
        command = subprocess.list2cmdline(tokens) if os.name == "nt" else shlex.join(tokens)
        payload = hook_tests.VaultHookShellContractTests.attested_writer_payload(self.root, command)
        payload["tool_use_id"] = event
        return self.hook.normalize(payload)

    def restore_payload(self, source="HEAD"):
        return self.payload("restore", "--source=" + source, "--worktree", "--",
                            "workspace/docs/experience-design")

    def verify(self, payload):
        output = io.StringIO()
        with redirect_stderr(output):
            result = self.hook.shell_verify(payload)
        return result, output.getvalue()

    def test_restore_replays_the_approved_target_and_publishes_its_postimage(self):
        self.path.write_bytes(b'{"revision": 1}\n')
        payload = self.restore_payload()
        self.assertEqual(self.hook.shell_snapshot(payload), 0)
        self.git("restore", "--source=HEAD", "--worktree", "--", "workspace/docs/experience-design")
        code, error = self.verify(payload)
        self.assertEqual(code, 0, error)
        self.assertEqual(self.path.read_bytes(), self.expected)
        state, _, error = self.hook.load_authorized_experience_state(self.root)
        self.assertFalse(error)
        self.assertIsNotNone(state)

    def test_merge_preserves_unpublished_history_without_restoring_old_receipts(self):
        self.git("checkout", "-b", "local-owner", self.old)
        (self.root / "owner.txt").write_text("local owner content\n", encoding="utf-8")
        self.commit("Preserve local owner content")
        owner = self.git("rev-parse", "HEAD")
        payload = self.payload("merge", "--no-edit", self.target)
        self.assertEqual(self.hook.shell_snapshot(payload), 0)
        self.git("merge", "--no-edit", self.target)
        code, error = self.verify(payload)
        self.assertEqual(code, 0, error)
        self.assertEqual(self.path.read_bytes(), self.expected)
        self.git("merge-base", "--is-ancestor", owner, "HEAD")

    def test_stale_reader_keeps_the_accepted_git_postimage(self):
        self.path.write_bytes(b'{"revision": 1}\n')
        reader = self.payload("status", "--porcelain", event="stale-reader")
        writer = self.restore_payload()
        self.assertEqual(self.hook.shell_snapshot(reader), 0)
        self.assertEqual(self.hook.shell_snapshot(writer), 0)
        self.git("restore", "--source=HEAD", "--worktree", "--", "workspace/docs/experience-design")
        code, error = self.verify(writer)
        self.assertEqual(code, 0, error)
        code, error = self.verify(reader)
        self.assertEqual(code, 0, error)
        self.assertEqual(self.path.read_bytes(), self.expected)

    def test_local_commit_alone_does_not_authorize_a_replay(self):
        self.path.write_bytes(b'{"revision": 3}\n')
        self.commit("Unapproved local source")
        payload = self.restore_payload()
        output = io.StringIO()
        with redirect_stderr(output):
            self.assertEqual(self.hook.shell_snapshot(payload), 2)
        self.assertIn("approved Git", output.getvalue())

    def test_modified_postimage_is_rejected_and_restored(self):
        before = b'{"revision": 1}\n'
        self.path.write_bytes(before)
        payload = self.restore_payload()
        self.assertEqual(self.hook.shell_snapshot(payload), 0)
        self.git("restore", "--source=HEAD", "--worktree", "--", "workspace/docs/experience-design")
        self.path.write_bytes(b'{"revision": 99}\n')
        code, _ = self.verify(payload)
        self.assertEqual(code, 2)
        self.assertEqual(self.path.read_bytes(), before)

    def test_owning_compiler_failure_revokes_the_replay(self):
        before = b'{"revision": 1}\n'
        self.path.write_bytes(before)
        payload = self.restore_payload()
        self.assertEqual(self.hook.shell_snapshot(payload), 0)
        self.git("restore", "--source=HEAD", "--worktree", "--", "workspace/docs/experience-design")
        with mock.patch.object(self.hook.experience_application_check, "compile_application",
                               return_value=({}, ["invalid synthetic receipt"])):
            code, _ = self.verify(payload)
        self.assertEqual(code, 2)
        self.assertEqual(self.path.read_bytes(), before)

    def test_missing_recovery_capsule_revokes_git_authority(self):
        before = b'{"revision": 1}\n'
        self.path.write_bytes(before)
        payload = self.restore_payload()
        self.assertEqual(self.hook.shell_snapshot(payload), 0)
        self.hook.recovery_path(payload).unlink()
        self.git("restore", "--source=HEAD", "--worktree", "--", "workspace/docs/experience-design")
        code, _ = self.verify(payload)
        self.assertEqual(code, 2)
        self.assertEqual(self.path.read_bytes(), before)

    def test_changed_command_binding_revokes_git_authority(self):
        before = b'{"revision": 1}\n'
        self.path.write_bytes(before)
        payload = self.restore_payload()
        self.assertEqual(self.hook.shell_snapshot(payload), 0)
        self.git("restore", "--source=HEAD", "--worktree", "--", "workspace/docs/experience-design")
        payload["tool_input"] = {"command": "unrelated command"}
        code, _ = self.verify(payload)
        self.assertEqual(code, 2)
        self.assertEqual(self.path.read_bytes(), before)

    def test_shell_composition_and_untrusted_executables_receive_no_grant(self):
        payload = self.restore_payload()
        command = payload["tool_input"]["command"]
        for value in [command + " && echo unsafe", command + "; echo unsafe",
                      command.replace(self.git_path, "git", 1)]:
            with self.subTest(command=value):
                payload["tool_input"] = {"command": value}
                self.assertIsNone(self.hook.git_experience_sync_spec(payload, self.root))
        payload["tool_input"] = {"command": command}
        payload["shell_family"] = "unknown"
        self.assertIsNone(self.hook.git_experience_sync_spec(payload, self.root))

    def test_active_lifecycle_writer_blocks_git_sync_before_execution(self):
        active = self.payload("status", event="active-lifecycle")
        self.hook.prepare_recovery_root()
        self.assertFalse(self.hook.acquire_experience_writer_lock(self.root, active))
        self.addCleanup(self.hook.release_experience_writer_lock, self.root, active)
        output = io.StringIO()
        with redirect_stderr(output):
            self.assertEqual(self.hook.shell_snapshot(self.restore_payload()), 2)
        self.assertIn("already in progress", output.getvalue())

    def test_unrelated_staged_content_is_preserved_by_restore(self):
        owner = self.root / "owner.txt"
        owner.write_text("staged owner content\n", encoding="utf-8")
        self.git("add", "owner.txt")
        index = self.git("ls-files", "--stage")
        self.path.write_bytes(b'{"revision": 1}\n')
        payload = self.restore_payload()
        self.assertEqual(self.hook.shell_snapshot(payload), 0)
        self.git("restore", "--source=HEAD", "--worktree", "--", "workspace/docs/experience-design")
        code, error = self.verify(payload)
        self.assertEqual(code, 0, error)
        self.assertEqual(self.git("ls-files", "--stage"), index)
        self.assertEqual(owner.read_text(encoding="utf-8"), "staged owner content\n")

    def test_index_mutation_during_restore_revokes_git_authority(self):
        before = b'{"revision": 1}\n'
        self.path.write_bytes(before)
        payload = self.restore_payload()
        self.assertEqual(self.hook.shell_snapshot(payload), 0)
        self.git("restore", "--source=HEAD", "--worktree", "--", "workspace/docs/experience-design")
        (self.root / "extra.txt").write_text("unexpected\n", encoding="utf-8")
        self.git("add", "extra.txt")
        code, _ = self.verify(payload)
        self.assertEqual(code, 2)
        self.assertEqual(self.path.read_bytes(), before)

    def test_additional_untracked_receipt_revokes_git_authority(self):
        self.path.write_bytes(b'{"revision": 1}\n')
        payload = self.restore_payload()
        self.assertEqual(self.hook.shell_snapshot(payload), 0)
        self.git("restore", "--source=HEAD", "--worktree", "--", "workspace/docs/experience-design")
        extra = self.path.parent / "extra.json"
        extra.write_text("{}\n", encoding="utf-8")
        code, _ = self.verify(payload)
        self.assertEqual(code, 2)
        self.assertFalse(extra.exists())

    def test_remote_target_drift_revokes_git_authority(self):
        before = b'{"revision": 1}\n'
        self.path.write_bytes(before)
        payload = self.restore_payload()
        self.assertEqual(self.hook.shell_snapshot(payload), 0)
        self.git("restore", "--source=HEAD", "--worktree", "--", "workspace/docs/experience-design")
        with mock.patch.object(self.hook, "approved_git_sync_authority", return_value=("changed", self.target)):
            code, _ = self.verify(payload)
        self.assertEqual(code, 2)
        self.assertEqual(self.path.read_bytes(), before)

    def restore_command(self):
        """The attested restore the guard names, with this host's quoting of Git's path."""
        git = subprocess.list2cmdline([self.git_path]) if os.name == "nt" else shlex.quote(self.git_path)
        return f"{git} restore --source=HEAD --worktree -- workspace/docs/experience-design"

    def unanswered_remote(self):
        """Make every ls-remote the hook runs time out, recording the bound it set."""
        real_run = subprocess.run
        self.remote_timeouts = []

        def run(command, *args, **kwargs):
            if "ls-remote" in command:
                self.remote_timeouts.append(kwargs.get("timeout"))
                raise subprocess.TimeoutExpired(command, kwargs.get("timeout") or 0)
            return real_run(command, *args, **kwargs)
        return mock.patch.object(self.hook.subprocess, "run", side_effect=run)

    def test_a_plain_merge_is_restored_with_the_attested_restore_named(self):
        self.git("checkout", "-b", "local-owner", self.old)
        (self.root / "owner.txt").write_text("local owner content\n", encoding="utf-8")
        self.commit("Preserve local owner content")
        before = self.path.read_bytes()
        payload = self.payload("merge", "--no-edit", self.target)
        payload["tool_input"] = {"command": payload["tool_input"]["command"].replace(self.git_path, "git", 1)}
        self.assertEqual(self.hook.shell_snapshot(payload), 0)
        self.git("merge", "--no-edit", self.target)
        code, error = self.verify(payload)
        self.assertEqual(code, 2, error)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn("original Experience tree was restored", error)
        self.assertIn(f"if a Git merge or pull of an approved handoff made this change, run Git by its "
                      f"absolute path, as a call of its own from {self.root}: `{self.restore_command()}`", error)
        # The command the guard names, as this host quotes it, completes the synchronization.
        named = re.search(r"`([^`]* restore --source=HEAD [^`]*)`", error).group(1)
        restore = hook_tests.VaultHookShellContractTests.attested_writer_payload(self.root, named)
        restore["tool_use_id"] = "named-restore"
        restore = self.hook.normalize(restore)
        self.assertIsNotNone(self.hook.git_experience_sync_spec(restore, self.root))
        self.assertEqual(self.hook.shell_snapshot(restore), 0)
        self.git("restore", "--source=HEAD", "--worktree", "--", "workspace/docs/experience-design")
        code, error = self.verify(restore)
        self.assertEqual(code, 0, error)
        self.assertEqual(self.path.read_bytes(), self.expected)

    def test_an_unanswered_remote_refuses_the_sync_before_it_runs(self):
        self.path.write_bytes(b'{"revision": 1}\n')
        output = io.StringIO()
        with self.unanswered_remote(), redirect_stderr(output):
            self.assertEqual(self.hook.shell_snapshot(self.restore_payload()), 2)
        message = output.getvalue()
        self.assertIn("did not answer `git ls-remote origin refs/heads/agentrof/fence` within", message)
        self.assertIn("nothing changed", message)
        self.assertIn(self.restore_command(), message)
        self.assertEqual(self.remote_timeouts, [self.hook.GIT_SYNC_REMOTE_TIMEOUT_SECONDS])
        self.assertFalse(self.hook.experience_writer_lock_path(self.root).exists())

    def test_an_unanswered_remote_after_the_sync_restores_and_says_why(self):
        before = b'{"revision": 1}\n'
        self.path.write_bytes(before)
        payload = self.restore_payload()
        self.assertEqual(self.hook.shell_snapshot(payload), 0)
        self.git("restore", "--source=HEAD", "--worktree", "--", "workspace/docs/experience-design")
        with self.unanswered_remote():
            code, error = self.verify(payload)
        self.assertEqual(code, 2, error)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn("did not answer `git ls-remote origin refs/heads/agentrof/fence`", error)
        self.assertIn(self.restore_command(), error)
        self.assertEqual(self.remote_timeouts, [self.hook.GIT_SYNC_REMOTE_TIMEOUT_SECONDS])

    def test_an_unanswered_remote_after_a_noop_sync_publishes_nothing_and_says_why(self):
        payload = self.restore_payload()
        self.assertEqual(self.hook.shell_snapshot(payload), 0)
        self.git("restore", "--source=HEAD", "--worktree", "--", "workspace/docs/experience-design")
        with self.unanswered_remote():
            code, error = self.verify(payload)
        self.assertEqual(code, 2, error)
        self.assertIn("left no attested postimage; the remote did not answer "
                      "`git ls-remote origin refs/heads/agentrof/fence`", error)
        self.assertIn("once the remote answers, run Git by its absolute path", error)
        self.assertIn(self.restore_command(), error)
        state, _, load_error = self.hook.load_authorized_experience_state(self.root)
        self.assertFalse(load_error)
        self.assertIsNone(state)

    def test_application_code_merge_keeps_existing_guard_behavior(self):
        self.git("push", "origin", ":refs/heads/agentrof/fence")
        payload = self.payload("merge", "--no-edit", "HEAD")
        self.assertEqual(self.hook.shell_snapshot(payload), 0)
        self.git("merge", "--no-edit", "HEAD")
        code, error = self.verify(payload)
        self.assertEqual(code, 0, error)

    def test_noop_restore_still_publishes_verified_authority(self):
        payload = self.restore_payload()
        self.assertEqual(self.hook.shell_snapshot(payload), 0)
        self.git("restore", "--source=HEAD", "--worktree", "--", "workspace/docs/experience-design")
        code, error = self.verify(payload)
        self.assertEqual(code, 0, error)
        state, _, error = self.hook.load_authorized_experience_state(self.root)
        self.assertFalse(error)
        self.assertIsNotNone(state)

    def test_git_capture_locks_before_reading_the_tree_or_authority(self):
        self.path.write_bytes(b'{"revision": 1}\n')
        payload = self.restore_payload()
        other = self.payload("status", event="competing-writer")
        snapshot = self.hook.experience_tree_snapshot
        observed = []
        def capture(vault):
            observed.append(self.hook.acquire_experience_writer_lock(self.root, other))
            return snapshot(vault)
        with mock.patch.object(self.hook, "experience_tree_snapshot", side_effect=capture):
            self.assertEqual(self.hook.shell_snapshot(payload), 0)
        self.assertTrue(observed)
        self.assertTrue(all("already in progress" in result for result in observed))
        self.git("restore", "--source=HEAD", "--worktree", "--", "workspace/docs/experience-design")
        code, error = self.verify(payload)
        self.assertEqual(code, 0, error)
        self.assertEqual(self.path.read_bytes(), self.expected)

    def test_reader_rejects_a_transition_during_snapshot_capture_without_restoring_it(self):
        self.git("checkout", "--detach", self.old)
        reader = self.payload("status", event="capturing-reader")
        snapshot = self.hook.experience_tree_snapshot
        injected = []
        def capture(vault):
            before = snapshot(vault)
            if not injected:
                injected.append(True)
                self.git("checkout", "--detach", self.target)
                self.hook.publish_authorized_experience_state(self.root, self.docs)
            return before
        output = io.StringIO()
        with mock.patch.object(self.hook, "experience_tree_snapshot", side_effect=capture), redirect_stderr(output):
            self.assertEqual(self.hook.shell_snapshot(reader), 2)
        self.assertIn("changed during snapshot capture", output.getvalue())
        self.assertEqual(self.path.read_bytes(), self.expected)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.target)
        self.assertFalse(self.hook.recovery_path(reader).exists())

    def test_reader_detects_a_noop_publication_generation_during_capture(self):
        self.hook.publish_authorized_experience_state(self.root, self.docs)
        reader = self.payload("status", event="noop-publication-reader")
        snapshot = self.hook.experience_tree_snapshot
        injected = []
        def capture(vault):
            before = snapshot(vault)
            if not injected:
                injected.append(True)
                self.hook.publish_authorized_experience_state(self.root, self.docs)
            return before
        with mock.patch.object(self.hook, "experience_tree_snapshot", side_effect=capture), redirect_stderr(io.StringIO()):
            self.assertEqual(self.hook.shell_snapshot(reader), 2)
        self.assertEqual(self.path.read_bytes(), self.expected)

    def test_failed_git_preflight_releases_the_capture_lock(self):
        self.path.write_bytes(b'{"revision": 3}\n')
        self.commit("Unapproved local source")
        payload = self.restore_payload()
        with redirect_stderr(io.StringIO()):
            self.assertEqual(self.hook.shell_snapshot(payload), 2)
        self.assertFalse(self.hook.experience_writer_lock_path(self.root).exists())

    def test_safe_os_metadata_is_preserved_without_entering_the_committed_postimage(self):
        metadata = self.docs / "experience-design/.DS_Store"
        metadata.write_bytes(b"synthetic operating-system metadata\x00")
        if hasattr(os, "chflags"):
            os.chflags(metadata, getattr(__import__("stat"), "UF_HIDDEN", 0))
        self.path.write_bytes(b'{"revision": 1}\n')
        payload = self.restore_payload()
        self.assertEqual(self.hook.shell_snapshot(payload), 0)
        self.git("restore", "--source=HEAD", "--worktree", "--", "workspace/docs/experience-design")
        code, error = self.verify(payload)
        self.assertEqual(code, 0, error)
        self.assertEqual(self.path.read_bytes(), self.expected)
        self.assertEqual(metadata.read_bytes(), b"synthetic operating-system metadata\x00")

    def test_metadata_symlink_never_receives_a_safety_exemption(self):
        metadata = self.docs / "experience-design/.DS_Store"
        try:
            metadata.symlink_to(self.path)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(str(exc))
        with redirect_stderr(io.StringIO()):
            self.assertEqual(self.hook.shell_snapshot(self.restore_payload()), 2)


class GitSyncInstructionTests(unittest.TestCase):
    """The agent learns the two attested forms from what both hosts ship, not only
    from the maintainer docs."""

    def test_the_host_contracts_and_the_handoff_name_both_forms(self):
        root = Path(__file__).resolve().parents[2]
        forms = ("merge --no-edit <source>",
                 "restore --source=<source> --worktree -- workspace/docs/experience-design")
        for host in ("claude", "codex"):
            for relative in ("host-contract.md", "flows/requirement.md"):
                text = " ".join((root / "dist" / host / "software-engineering-team" / relative)
                                .read_text(encoding="utf-8").split())
                for form in forms:
                    with self.subTest(host=host, file=relative, form=form):
                        self.assertIn(form, text)
                        self.assertIn("absolute path", text)
