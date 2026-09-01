"""Behavioral contracts for the recoverable stable publication transaction."""

from __future__ import annotations

import json
import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Optional, Sequence


TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))

import release_publish  # noqa: E402


CANDIDATE = "c" * 40
PRIOR = "a" * 40
TAG_OBJECT = "d" * 40
RELEASE_BRANCH = "e" * 40


def completed(
    argv: Sequence[str],
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(tuple(argv), returncode, stdout, stderr)


class FakeCommands:
    """Small command-level remote used to test state transitions and leases."""

    def __init__(
        self,
        *,
        stable: Optional[str],
        tag_target: Optional[str] = None,
        tag_object: Optional[str] = None,
        main: str = CANDIDATE,
        release: str = "absent",
        release_branch: Optional[str] = None,
        main_contains_candidate: bool = False,
        main_after_atomic_push: Optional[str] = None,
    ):
        self.main = main
        self.stable = stable
        self.tag_target = tag_target
        self.tag_object = tag_object
        self.release = release
        self.release_branch = release_branch
        self.main_contains_candidate = main_contains_candidate
        self.main_after_atomic_push = main_after_atomic_push
        self.local_tag = tag_object is not None
        self.commands: list[tuple[str, ...]] = []
        self.create_returncode = 0
        self.create_effect: Optional[str] = "exists"
        self.release_json: Optional[dict] = None

    @staticmethod
    def _release_json() -> str:
        return json.dumps({
            "tagName": "v1.2.3",
            "name": "Agent Marketplace v1.2.3",
            "isDraft": False,
            "isPrerelease": False,
        })

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess:
        command = tuple(argv)
        self.commands.append(command)
        if command[:2] == ("git", "ls-remote"):
            lines = [f"{self.main}\trefs/heads/main"]
            if self.stable is not None:
                lines.append(f"{self.stable}\trefs/heads/stable")
            if self.release_branch is not None:
                lines.append(
                    f"{self.release_branch}\trefs/heads/release/stable"
                )
            if self.tag_object is not None:
                lines.append(f"{self.tag_object}\trefs/tags/v1.2.3")
            if self.tag_target is not None:
                lines.append(f"{self.tag_target}\trefs/tags/v1.2.3^{{}}")
            return completed(command, stdout="\n".join(lines) + "\n")
        if command[:4] == (
            "git", "rev-parse", "--verify", "--quiet"
        ):
            if self.local_tag:
                return completed(command, stdout=f"{TAG_OBJECT}\n")
            return completed(command, returncode=1)
        if command[:3] == ("git", "tag", "-a"):
            self.local_tag = True
            return completed(command)
        if command[:3] == ("git", "rev-parse", "--verify"):
            if command[-1].endswith("^{tag}"):
                return completed(command, stdout=f"{TAG_OBJECT}\n")
            if command[-1].endswith("release-publish-main"):
                return completed(command, stdout=f"{self.main}\n")
            return completed(command, stdout=f"{CANDIDATE}\n")
        if command[:2] == ("git", "fetch"):
            return completed(command)
        if command[:3] == ("git", "merge-base", "--is-ancestor"):
            return completed(
                command, returncode=0 if self.main_contains_candidate else 1
            )
        if command[:3] == ("git", "push", "--atomic"):
            stable_lease = next(
                value for value in command
                if value.startswith("--force-with-lease=refs/heads/stable:")
            ).split(":", 1)[1]
            tag_lease = next(
                value for value in command
                if value.startswith("--force-with-lease=refs/tags/v1.2.3:")
            ).split(":", 1)[1]
            if stable_lease != (self.stable or ""):
                return completed(command, 1, stderr="stale stable lease")
            if tag_lease != (self.tag_object or ""):
                return completed(command, 1, stderr="stale tag lease")
            stable_refspec = command[-2]
            tag_refspec = command[-1]
            self.stable = (
                None if stable_refspec.startswith(":")
                else stable_refspec.split(":", 1)[0]
            )
            if tag_refspec.startswith(":"):
                self.tag_object = None
                self.tag_target = None
            else:
                self.tag_object = TAG_OBJECT
                self.tag_target = CANDIDATE
            if self.main_after_atomic_push is not None:
                self.main = self.main_after_atomic_push
            return completed(command)
        if command[:2] == ("git", "push"):
            lease = next(
                value for value in command
                if value.startswith(
                    "--force-with-lease=refs/heads/release/stable:"
                )
            ).split(":", 1)[1]
            if lease != (self.release_branch or ""):
                return completed(command, 1, stderr="stale release branch lease")
            self.release_branch = None
            return completed(command)
        if command[:3] == ("gh", "release", "view"):
            if self.release == "exists":
                value = (
                    self.release_json if self.release_json is not None
                    else json.loads(self._release_json())
                )
                return completed(command, stdout=json.dumps(value))
            if self.release == "absent":
                return completed(command, 1, stderr="release not found")
            return completed(command, 1, stderr="network unavailable")
        if command[:3] == ("gh", "release", "create"):
            if self.create_effect is not None:
                self.release = self.create_effect
            return completed(
                command, self.create_returncode,
                stderr="response lost" if self.create_returncode else "",
            )
        raise AssertionError(f"unexpected command: {command}")


def spec(*, bootstrap: bool = False) -> release_publish.ReleaseSpec:
    return release_publish.ReleaseSpec(
        version="1.2.3",
        candidate_sha=CANDIDATE,
        prior_stable_sha=None if bootstrap else PRIOR,
    )


class PublicationStateMachineTests(unittest.TestCase):
    def test_initial_normal_state_stages_candidate_atomically_with_exact_leases(self):
        fake = FakeCommands(stable=PRIOR)
        result = release_publish.Publisher(fake).stage(spec())
        self.assertEqual(result["action"], "staged")
        self.assertEqual(fake.stable, CANDIDATE)
        self.assertEqual(fake.tag_target, CANDIDATE)
        push = next(command for command in fake.commands
                    if command[:3] == ("git", "push", "--atomic"))
        self.assertIn(
            f"--force-with-lease=refs/heads/stable:{PRIOR}", push
        )
        self.assertIn("--force-with-lease=refs/tags/v1.2.3:", push)
        self.assertIn(f"{CANDIDATE}:refs/heads/stable", push)

    def test_bootstrap_requires_absent_stable_and_leases_that_absence(self):
        fake = FakeCommands(stable=None)
        release_publish.Publisher(fake).stage(spec(bootstrap=True))
        push = next(command for command in fake.commands
                    if command[:3] == ("git", "push", "--atomic"))
        self.assertIn("--force-with-lease=refs/heads/stable:", push)

    def test_exact_staged_refs_resume_without_mutation(self):
        fake = FakeCommands(
            stable=CANDIDATE, tag_target=CANDIDATE, tag_object=TAG_OBJECT
        )
        result = release_publish.Publisher(fake).stage(spec())
        self.assertEqual(result["action"], "resumed")
        self.assertFalse(any(command[:2] == ("git", "push")
                             for command in fake.commands))

    def test_mixed_stable_and_tag_state_is_rejected_without_mutation(self):
        fake = FakeCommands(stable=CANDIDATE)
        with self.assertRaisesRegex(
            release_publish.PublishError, "mixed publication state"
        ):
            release_publish.Publisher(fake).stage(spec())
        self.assertFalse(any(command[:2] == ("git", "push")
                             for command in fake.commands))

    def test_lightweight_remote_tag_is_rejected(self):
        fake = FakeCommands(
            stable=CANDIDATE, tag_object=TAG_OBJECT, tag_target=None
        )
        with self.assertRaisesRegex(
            release_publish.PublishError, "annotated tag is required"
        ):
            release_publish.Publisher(fake).stage(spec())

    def test_diverged_remote_main_is_rejected_before_any_mutation(self):
        fake = FakeCommands(stable=PRIOR, main="b" * 40)
        with self.assertRaisesRegex(
            release_publish.PublishError, "not an ancestor"
        ):
            release_publish.Publisher(fake).stage(spec())
        self.assertFalse(any(command[:2] == ("git", "push")
                             for command in fake.commands))
        self.assertFalse(any(command[:3] == ("gh", "release", "create")
                             for command in fake.commands))

    def test_initial_stage_accepts_candidate_behind_descendant_main(self):
        fake = FakeCommands(
            stable=PRIOR,
            main="b" * 40,
            main_contains_candidate=True,
        )
        result = release_publish.Publisher(fake).stage(spec())
        self.assertEqual(result["phase"], "staged")
        self.assertEqual(fake.stable, CANDIDATE)
        self.assertEqual(fake.tag_target, CANDIDATE)

    def test_post_push_main_divergence_rolls_back_exact_staged_refs(self):
        fake = FakeCommands(
            stable=PRIOR,
            main_after_atomic_push="b" * 40,
        )
        with self.assertRaisesRegex(
            release_publish.PublishError, "staged candidate refs were rolled back"
        ):
            release_publish.Publisher(fake).stage(spec())
        self.assertEqual(fake.stable, PRIOR)
        self.assertIsNone(fake.tag_object)

    def test_staged_refs_rollback_atomically_before_release_exists(self):
        fake = FakeCommands(
            stable=CANDIDATE, tag_target=CANDIDATE, tag_object=TAG_OBJECT
        )
        result = release_publish.Publisher(fake).rollback(spec())
        self.assertEqual(result["action"], "rolled-back")
        self.assertEqual(fake.stable, PRIOR)
        self.assertIsNone(fake.tag_object)
        push = next(command for command in fake.commands
                    if command[:3] == ("git", "push", "--atomic"))
        self.assertIn(
            f"--force-with-lease=refs/heads/stable:{CANDIDATE}", push
        )
        self.assertIn(
            f"--force-with-lease=refs/tags/v1.2.3:{TAG_OBJECT}", push
        )

    def test_rollback_remains_available_after_main_advances(self):
        fake = FakeCommands(
            stable=CANDIDATE,
            tag_target=CANDIDATE,
            tag_object=TAG_OBJECT,
            main="b" * 40,
        )
        result = release_publish.Publisher(fake).rollback(spec())
        self.assertEqual(result["action"], "rolled-back")
        self.assertEqual(fake.stable, PRIOR)
        self.assertIsNone(fake.tag_object)

    def test_stage_rerun_resumes_if_main_advanced_after_exact_stage(self):
        fake = FakeCommands(
            stable=CANDIDATE,
            tag_target=CANDIDATE,
            tag_object=TAG_OBJECT,
            main="b" * 40,
            main_contains_candidate=True,
        )
        result = release_publish.Publisher(fake).stage(spec())
        self.assertEqual(result["action"], "resumed")
        self.assertEqual(fake.stable, CANDIDATE)
        self.assertEqual(fake.tag_target, CANDIDATE)
        self.assertFalse(any(
            command[:2] == ("git", "push") for command in fake.commands
        ))

    def test_finalize_publishes_if_main_advanced_after_exact_stage(self):
        fake = FakeCommands(
            stable=CANDIDATE,
            tag_target=CANDIDATE,
            tag_object=TAG_OBJECT,
            main="b" * 40,
            main_contains_candidate=True,
        )
        result = release_publish.Publisher(fake).finalize(
            spec(), "release-notes.md"
        )
        self.assertEqual(result["action"], "created")
        self.assertEqual(fake.release, "exists")
        self.assertEqual(fake.stable, CANDIDATE)
        self.assertEqual(fake.tag_target, CANDIDATE)

    def test_rollback_is_forbidden_after_matching_release_exists(self):
        fake = FakeCommands(
            stable=CANDIDATE, tag_target=CANDIDATE,
            tag_object=TAG_OBJECT, release="exists",
        )
        with self.assertRaisesRegex(
            release_publish.PublishError, "forbidden"
        ):
            release_publish.Publisher(fake).rollback(spec())
        self.assertFalse(any(command[:2] == ("git", "push")
                             for command in fake.commands))

    def test_release_metadata_must_match_and_be_public_stable(self):
        fake = FakeCommands(
            stable=CANDIDATE, tag_target=CANDIDATE,
            tag_object=TAG_OBJECT, release="exists",
        )
        fake.release_json = {
            "tagName": "v1.2.3",
            "name": "Agent Marketplace v1.2.3",
            "isDraft": True,
            "isPrerelease": False,
        }
        with self.assertRaisesRegex(
            release_publish.PublishError, "non-draft"
        ):
            release_publish.Publisher(fake).finalize(
                spec(), "release-notes.md"
            )

    def test_create_failure_reconciles_observed_release_and_cleans_branch(self):
        fake = FakeCommands(
            stable=CANDIDATE, tag_target=CANDIDATE,
            tag_object=TAG_OBJECT, release_branch=RELEASE_BRANCH,
        )
        fake.create_returncode = 1
        fake.create_effect = "exists"
        result = release_publish.Publisher(fake).finalize(
            spec(), "release-notes.md", RELEASE_BRANCH
        )
        self.assertEqual(result["action"], "reconciled-after-create-failure")
        self.assertEqual(result["release_branch_cleanup"], "deleted")
        self.assertIsNone(fake.release_branch)
        delete = [command for command in fake.commands
                  if command[:2] == ("git", "push")][-1]
        self.assertIn(
            f"--force-with-lease=refs/heads/release/stable:{RELEASE_BRANCH}",
            delete,
        )

    def test_create_failure_with_absent_release_preserves_candidate_refs(self):
        fake = FakeCommands(
            stable=CANDIDATE, tag_target=CANDIDATE,
            tag_object=TAG_OBJECT, release_branch=RELEASE_BRANCH,
        )
        fake.create_returncode = 1
        fake.create_effect = "absent"
        with self.assertRaisesRegex(
            release_publish.PublishError, "candidate refs were preserved"
        ):
            release_publish.Publisher(fake).finalize(
                spec(), "release-notes.md", RELEASE_BRANCH
            )
        self.assertEqual(fake.stable, CANDIDATE)
        self.assertEqual(fake.tag_target, CANDIDATE)
        self.assertEqual(fake.release_branch, RELEASE_BRANCH)
        self.assertFalse(any(command[:2] == ("git", "push")
                             for command in fake.commands))

    def test_create_failure_with_uncertain_release_preserves_candidate_refs(self):
        fake = FakeCommands(
            stable=CANDIDATE, tag_target=CANDIDATE,
            tag_object=TAG_OBJECT,
        )
        fake.create_returncode = 1
        fake.create_effect = "unknown"
        with self.assertRaisesRegex(
            release_publish.PublishError, "observation was unknown"
        ):
            release_publish.Publisher(fake).finalize(
                spec(), "release-notes.md"
            )
        self.assertEqual(fake.stable, CANDIDATE)
        self.assertEqual(fake.tag_target, CANDIDATE)
        self.assertFalse(any(command[:2] == ("git", "push")
                             for command in fake.commands))

    def test_matching_existing_release_is_reconciled_without_create(self):
        fake = FakeCommands(
            stable=CANDIDATE, tag_target=CANDIDATE,
            tag_object=TAG_OBJECT, release="exists",
        )
        result = release_publish.Publisher(fake).finalize(
            spec(), "release-notes.md"
        )
        self.assertEqual(result["action"], "reconciled")
        self.assertFalse(any(command[:3] == ("gh", "release", "create")
                             for command in fake.commands))

    def test_published_release_cleanup_survives_a_later_main_advance(self):
        fake = FakeCommands(
            stable=CANDIDATE,
            tag_target=CANDIDATE,
            tag_object=TAG_OBJECT,
            release="exists",
            release_branch=RELEASE_BRANCH,
            main="b" * 40,
            main_contains_candidate=True,
        )
        staged = release_publish.Publisher(fake).stage(spec())
        self.assertEqual(staged["phase"], "published")
        result = release_publish.Publisher(fake).finalize(
            spec(), "release-notes.md", RELEASE_BRANCH
        )
        self.assertEqual(result["release_branch_cleanup"], "deleted")


class ValidationTests(unittest.TestCase):
    def test_cli_contract_rejects_non_strict_version_and_sha(self):
        with self.assertRaises(release_publish.PublishError):
            release_publish.ReleaseSpec("v1.2.3", CANDIDATE, PRIOR)
        with self.assertRaises(release_publish.PublishError):
            release_publish.ReleaseSpec("1.2.3", "ABC", PRIOR)

    def test_cli_requires_explicit_bootstrap_or_prior_stable_mode(self):
        parser = release_publish.build_parser()
        with self.assertRaises(release_publish.PublishError):
            parser.parse_args([
                "stage", "--version", "1.2.3",
                "--candidate-sha", CANDIDATE,
            ])

    def test_cli_validation_failure_is_json(self):
        output = io.StringIO()
        with redirect_stdout(output):
            status = release_publish.main([
                "stage", "--version", "v1.2.3",
                "--candidate-sha", CANDIDATE, "--bootstrap",
            ])
        self.assertEqual(status, 1)
        self.assertEqual(json.loads(output.getvalue())["ok"], False)


class GitTransactionIntegrationTests(unittest.TestCase):
    def test_real_bare_remote_stages_and_rolls_back_atomically(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            remote = root / "remote.git"
            work = root / "work"
            subprocess.run(
                ["git", "init", "--bare", str(remote)],
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "init", "-b", "main", str(work)],
                check=True, capture_output=True, text=True,
            )

            def git(*args: str) -> str:
                completed = subprocess.run(
                    ["git", *args], cwd=work, check=True,
                    capture_output=True, text=True,
                )
                return completed.stdout.strip()

            git("config", "user.name", "Publication Test")
            git("config", "user.email", "publication@example.test")
            (work / "state.txt").write_text("stable\n", encoding="utf-8")
            git("add", "state.txt")
            git("commit", "-m", "stable")
            prior = git("rev-parse", "HEAD")
            git("branch", "stable")
            (work / "state.txt").write_text("candidate\n", encoding="utf-8")
            git("commit", "-am", "candidate")
            candidate = git("rev-parse", "HEAD")
            git("remote", "add", "origin", str(remote))
            git("push", "origin", "main", "stable")

            def runner(argv: Sequence[str]) -> subprocess.CompletedProcess:
                if tuple(argv[:3]) == ("gh", "release", "view"):
                    return completed(argv, 1, stderr="release not found")
                return subprocess.run(
                    list(argv), cwd=work, check=False,
                    capture_output=True, text=True,
                )

            transaction = release_publish.Publisher(runner)
            release_spec = release_publish.ReleaseSpec(
                "1.2.3", candidate, prior
            )
            staged = transaction.stage(release_spec)
            self.assertEqual(staged["phase"], "staged")
            refs = git(
                "ls-remote", "origin", "refs/heads/stable",
                "refs/tags/v1.2.3^{}",
            )
            self.assertEqual(refs.count(candidate), 2)
            rolled_back = transaction.rollback(release_spec)
            self.assertEqual(rolled_back["phase"], "initial")
            refs = git(
                "ls-remote", "origin", "refs/heads/stable",
                "refs/tags/v1.2.3", "refs/tags/v1.2.3^{}",
            )
            self.assertEqual(refs, f"{prior}\trefs/heads/stable")


if __name__ == "__main__":
    unittest.main()
