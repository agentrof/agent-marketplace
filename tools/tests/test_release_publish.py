"""Behavioral contracts for the recoverable stable publication transaction."""

from __future__ import annotations

import json
import io
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Optional, Sequence


TOOLS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TOOLS))

import release_publish  # noqa: E402
from tools.tests.git_fixture import init_repository, temporary_directory  # noqa: E402


CANDIDATE = "c" * 40
PRIOR = "a" * 40
TAG_OBJECT = "d" * 40


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
        main_contains_candidate: bool = False,
        main_after_atomic_push: Optional[str] = None,
        immutable: bool = True,
    ):
        self.main = main
        self.stable = stable
        self.tag_target = tag_target
        self.tag_object = tag_object
        self.release = release
        self.main_contains_candidate = main_contains_candidate
        self.main_after_atomic_push = main_after_atomic_push
        self.local_tag = tag_object is not None
        self.commands: list[tuple[str, ...]] = []
        self.create_returncode = 0
        self.create_effect: Optional[str] = "exists"
        self.release_json: Optional[dict] = None
        self.immutable = immutable

    def _release_json(self) -> str:
        return json.dumps({
            "tagName": "v1.2.3",
            "name": "v1.2.3",
            "isDraft": False,
            "isPrerelease": False,
            "isImmutable": self.immutable,
        })

    def __call__(self, argv: Sequence[str]) -> subprocess.CompletedProcess:
        command = tuple(argv)
        self.commands.append(command)
        if command[:2] == ("git", "ls-remote"):
            lines = [f"{self.main}\trefs/heads/main"]
            if self.stable is not None:
                lines.append(f"{self.stable}\trefs/heads/stable")
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
            "name": "v1.2.3",
            "isDraft": True,
            "isPrerelease": False,
            "isImmutable": True,
        }
        with self.assertRaisesRegex(
            release_publish.PublishError, "non-draft"
        ):
            release_publish.Publisher(fake).finalize(
                spec(), "release-notes.md"
            )

    def test_create_failure_reconciles_the_observed_release_without_pushing(self):
        fake = FakeCommands(
            stable=CANDIDATE, tag_target=CANDIDATE, tag_object=TAG_OBJECT,
        )
        fake.create_returncode = 1
        fake.create_effect = "exists"
        result = release_publish.Publisher(fake).finalize(
            spec(), "release-notes.md"
        )
        self.assertEqual(result["action"], "reconciled-after-create-failure")
        self.assertNotIn("release_branch_cleanup", result)
        self.assertNotIn("release_stable", result["refs"])
        self.assertFalse(any(command[:2] == ("git", "push")
                             for command in fake.commands))

    def test_create_failure_with_absent_release_preserves_candidate_refs(self):
        fake = FakeCommands(
            stable=CANDIDATE, tag_target=CANDIDATE, tag_object=TAG_OBJECT,
        )
        fake.create_returncode = 1
        fake.create_effect = "absent"
        with self.assertRaisesRegex(
            release_publish.PublishError, "candidate refs were preserved"
        ):
            release_publish.Publisher(fake).finalize(
                spec(), "release-notes.md"
            )
        self.assertEqual(fake.stable, CANDIDATE)
        self.assertEqual(fake.tag_target, CANDIDATE)
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

    def test_published_release_reconciles_after_a_later_main_advance(self):
        fake = FakeCommands(
            stable=CANDIDATE,
            tag_target=CANDIDATE,
            tag_object=TAG_OBJECT,
            release="exists",
            main="b" * 40,
            main_contains_candidate=True,
        )
        staged = release_publish.Publisher(fake).stage(spec())
        self.assertEqual(staged["phase"], "published")
        result = release_publish.Publisher(fake).finalize(
            spec(), "release-notes.md"
        )
        self.assertEqual(result["action"], "reconciled")
        self.assertFalse(any(command[:2] == ("git", "push")
                             for command in fake.commands))

    def test_remote_observation_reads_only_main_stable_and_the_version_tag(self):
        fake = FakeCommands(stable=PRIOR)
        release_publish.Publisher(fake).stage(spec())
        observed = {
            ref for command in fake.commands
            if command[:2] == ("git", "ls-remote") for ref in command[3:]
        }
        self.assertEqual(observed, {
            "refs/heads/main", "refs/heads/stable",
            "refs/tags/v1.2.3", "refs/tags/v1.2.3^{}",
        })


class ReleaseImmutabilityTests(unittest.TestCase):
    """Publication never changes an existing Release and can require immutability."""

    MUTATIONS = (
        ("gh", "release", "edit"), ("gh", "release", "delete"),
        ("gh", "release", "upload"), ("gh", "release", "delete-asset"),
    )

    def staged(self, **options) -> FakeCommands:
        return FakeCommands(
            stable=CANDIDATE, tag_target=CANDIDATE, tag_object=TAG_OBJECT,
            **options,
        )

    def assert_release_untouched(self, fake: FakeCommands) -> None:
        created = [index for index, command in enumerate(fake.commands)
                   if command[:3] == ("gh", "release", "create")]
        for command in fake.commands[created[0] if created else 0:]:
            self.assertNotIn(command[:3], self.MUTATIONS)
            if command[:2] == ("git", "push"):
                self.assertFalse(any("refs/tags/" in value for value in command), command)

    def test_observation_reads_github_immutability(self):
        for immutable in (True, False):
            with self.subTest(immutable=immutable):
                fake = self.staged(release="exists", immutable=immutable)
                observed = release_publish.Publisher(fake).observe_release(spec())
                self.assertEqual(observed.immutable, immutable)
                view = next(command for command in fake.commands
                            if command[:3] == ("gh", "release", "view"))
                self.assertIn("isImmutable", view[-1].split(","))

    def test_observation_requires_a_boolean_immutability_field(self):
        for value, message in (
            (None, "unknown or missing fields"),
            ("true", "must be a boolean"),
        ):
            with self.subTest(value=value):
                fake = self.staged(release="exists")
                release_json = json.loads(fake._release_json())
                if value is None:
                    del release_json["isImmutable"]
                else:
                    release_json["isImmutable"] = value
                fake.release_json = release_json
                with self.assertRaisesRegex(release_publish.PublishError, message):
                    release_publish.Publisher(fake).observe_release(spec())

    def test_created_immutable_release_finalizes_and_is_reported(self):
        fake = self.staged()
        result = release_publish.Publisher(fake).finalize(
            spec(), "release-notes.md", require_immutable=True,
        )
        self.assertEqual(result["action"], "created")
        self.assertIs(result["github_release_immutable"], True)
        self.assert_release_untouched(fake)

    def test_mutable_release_fails_and_keeps_the_published_refs(self):
        fake = self.staged(immutable=False)
        with self.assertRaisesRegex(
            release_publish.PublishError, "does not report it immutable"
        ) as raised:
            release_publish.Publisher(fake).finalize(
                spec(), "release-notes.md", require_immutable=True,
            )
        # Enabling the setting never locks a Release published before it, so
        # the remedy replaces the Release and keeps its tag.
        message = str(raised.exception)
        for remedy in ("enable release immutability for the repository",
                       f"delete this mutable Release (never its tag {spec().tag})",
                       "re-run finalize"):
            self.assertIn(remedy, message)
        self.assertLess(message.index("enable release immutability"),
                        message.index("delete this mutable Release"))
        self.assertIn("Candidate refs were preserved", message)
        self.assertEqual(fake.release, "exists")
        self.assertEqual((fake.stable, fake.tag_target), (CANDIDATE, CANDIDATE))
        self.assertFalse(any(command[:2] == ("git", "push")
                             for command in fake.commands))
        self.assert_release_untouched(fake)

    def test_uncertain_create_response_reconciles_the_immutable_release(self):
        fake = self.staged()
        fake.create_returncode = 1
        result = release_publish.Publisher(fake).finalize(
            spec(), "release-notes.md", require_immutable=True,
        )
        self.assertEqual(result["action"], "reconciled-after-create-failure")
        self.assertIs(result["github_release_immutable"], True)
        self.assert_release_untouched(fake)

    def test_resumed_publication_reconciles_without_changing_the_release(self):
        fake = self.staged(release="exists")
        staged = release_publish.Publisher(fake).stage(spec())
        self.assertEqual(staged["phase"], "published")
        self.assertIs(staged["github_release_immutable"], True)
        with self.assertRaisesRegex(release_publish.PublishError, "forbidden"):
            release_publish.Publisher(fake).rollback(spec())
        result = release_publish.Publisher(fake).finalize(
            spec(), "release-notes.md", require_immutable=True,
        )
        self.assertEqual(result["action"], "reconciled")
        self.assertFalse(any(command[:3] == ("gh", "release", "create")
                             for command in fake.commands))
        self.assert_release_untouched(fake)
        again = release_publish.Publisher(fake).finalize(
            spec(), "release-notes.md", require_immutable=True,
        )
        self.assertEqual(again["action"], "reconciled")

    def test_without_the_requirement_a_mutable_release_is_reported(self):
        fake = self.staged(immutable=False)
        result = release_publish.Publisher(fake).finalize(
            spec(), "release-notes.md",
        )
        self.assertIs(result["github_release_immutable"], False)
        self.assertEqual(result["action"], "created")

    def test_a_kept_mutable_release_completes_without_the_requirement(self):
        # The maintainer protocol's "keep it" path: finalize without
        # --require-immutable reconciles the existing Release unchanged.
        fake = self.staged(release="exists", immutable=False)
        with self.assertRaisesRegex(release_publish.PublishError, "does not report it immutable"):
            release_publish.Publisher(fake).finalize(
                spec(), "release-notes.md", require_immutable=True,
            )
        result = release_publish.Publisher(fake).finalize(
            spec(), "release-notes.md",
        )
        self.assertEqual(result["action"], "reconciled")
        self.assertIs(result["github_release_immutable"], False)
        self.assertFalse(any(command[:3] == ("gh", "release", "create")
                             for command in fake.commands))
        self.assert_release_untouched(fake)

    def test_cli_passes_the_immutability_requirement(self):
        args = release_publish.build_parser().parse_args([
            "finalize", "--version", "1.2.3", "--candidate-sha", CANDIDATE,
            "--prior-stable-sha", PRIOR, "--notes-file", "notes.md",
            "--require-immutable",
        ])
        self.assertTrue(args.require_immutable)
        args = release_publish.build_parser().parse_args([
            "finalize", "--version", "1.2.3", "--candidate-sha", CANDIDATE,
            "--prior-stable-sha", PRIOR, "--notes-file", "notes.md",
        ])
        self.assertFalse(args.require_immutable)
        with self.assertRaises(release_publish.PublishError):
            release_publish.build_parser().parse_args([
                "finalize", "--version", "1.2.3", "--candidate-sha", CANDIDATE,
                "--prior-stable-sha", PRIOR, "--notes-file", "notes.md",
                "--release-branch-sha", "e" * 40,
            ])


class ReleaseTitleTests(unittest.TestCase):
    """A Release is titled with its version tag alone."""

    def test_the_title_is_the_version_tag(self):
        self.assertEqual(spec().title, "v1.2.3")
        self.assertEqual(spec(bootstrap=True).title, "v1.2.3")

    def test_the_created_release_and_its_tag_message_carry_only_the_version(self):
        fake = FakeCommands(stable=PRIOR)
        publisher = release_publish.Publisher(fake)
        publisher.stage(spec())
        publisher.finalize(spec(), "release-notes.md", require_immutable=True)
        tag = next(command for command in fake.commands
                   if command[:3] == ("git", "tag", "-a"))
        self.assertEqual(tag[tag.index("-m") + 1], "v1.2.3")
        create = next(command for command in fake.commands
                      if command[:3] == ("gh", "release", "create"))
        self.assertEqual(create[create.index("--title") + 1], "v1.2.3")

    def test_a_release_with_the_retired_product_title_is_never_adopted(self):
        # Releases published before the title change named the product too.
        for operation in ("stage", "finalize"):
            with self.subTest(operation=operation):
                fake = FakeCommands(
                    stable=CANDIDATE, tag_target=CANDIDATE,
                    tag_object=TAG_OBJECT, release="exists",
                )
                fake.release_json = {
                    **json.loads(fake._release_json()),
                    "name": "Agent Marketplace v1.2.3",
                }
                publisher = release_publish.Publisher(fake)
                with self.assertRaisesRegex(
                    release_publish.PublishError,
                    "name mismatch: expected 'v1.2.3', "
                    "got 'Agent Marketplace v1.2.3'",
                ):
                    if operation == "stage":
                        publisher.stage(spec())
                    else:
                        publisher.finalize(
                            spec(), "release-notes.md", require_immutable=True,
                        )
                self.assertFalse(any(command[:2] == ("git", "push")
                                     for command in fake.commands))


class ValidationTests(unittest.TestCase):
    def test_cli_contract_rejects_non_strict_version_and_sha(self):
        with self.assertRaises(release_publish.PublishError):
            release_publish.ReleaseSpec("v1.2.3", CANDIDATE, PRIOR)
        with self.assertRaises(release_publish.PublishError):
            release_publish.ReleaseSpec("1.2.3", "ABC", PRIOR)

    def test_calendar_release_names_are_strict_semver(self):
        calendar = release_publish.ReleaseSpec("2026.10.1", CANDIDATE, PRIOR)
        self.assertEqual((calendar.tag, calendar.title), ("v2026.10.1", "v2026.10.1"))
        self.assertEqual(release_publish.strict_semver("2027.1.12"), "2027.1.12")
        for value in ("2026.09.1", "2026.10.01", "v2026.10.1", "2026.10"):
            with self.subTest(value=value), self.assertRaises(release_publish.PublishError):
                release_publish.strict_semver(value)

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
        with temporary_directory() as temporary:
            root = Path(temporary)
            remote = root / "remote.git"
            work = root / "work"
            init_repository(remote, bare=True)
            init_repository(work, initial_branch="main")

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
