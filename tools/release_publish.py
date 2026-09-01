#!/usr/bin/env python3
"""Recoverable stable-channel publication transaction.

This module deliberately owns only remote publication state.  Release content
verification and public-channel smoke tests remain separate gates.  The three
commands make it safe for a workflow to stage refs, roll them back before a
GitHub Release exists, or finalize/reconcile the immutable Release afterwards.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence


SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REMOTE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
RELEASE_PREFIX = "Agent Marketplace v"


class PublishError(RuntimeError):
    """Publication state is unsafe, conflicting, or cannot be established."""


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess]


def subprocess_runner(argv: Sequence[str]) -> subprocess.CompletedProcess:
    """Run one argv-only command without a shell."""
    return subprocess.run(
        list(argv), check=False, capture_output=True, text=True
    )


def _stdout(completed: subprocess.CompletedProcess) -> str:
    value = completed.stdout
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _stderr(completed: subprocess.CompletedProcess) -> str:
    value = completed.stderr
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def strict_semver(value: str) -> str:
    if SEMVER_RE.fullmatch(value) is None:
        raise PublishError(
            f"version must be strict SemVer X.Y.Z, got {value!r}"
        )
    return value


def strict_sha(value: str, label: str) -> str:
    if SHA_RE.fullmatch(value) is None:
        raise PublishError(
            f"{label} must be a lowercase 40-character commit SHA, got {value!r}"
        )
    return value


def strict_remote(value: str) -> str:
    if (
        REMOTE_RE.fullmatch(value) is None
        or ".." in value
        or value.endswith("/")
    ):
        raise PublishError(f"remote name is invalid: {value!r}")
    return value


@dataclass(frozen=True)
class ReleaseSpec:
    version: str
    candidate_sha: str
    prior_stable_sha: Optional[str]
    remote: str = "origin"

    def __post_init__(self) -> None:
        strict_semver(self.version)
        strict_sha(self.candidate_sha, "candidate SHA")
        if self.prior_stable_sha is not None:
            strict_sha(self.prior_stable_sha, "prior stable SHA")
            if self.prior_stable_sha == self.candidate_sha:
                raise PublishError(
                    "prior stable SHA must differ from the candidate SHA"
                )
        strict_remote(self.remote)

    @property
    def tag(self) -> str:
        return f"v{self.version}"

    @property
    def title(self) -> str:
        return f"{RELEASE_PREFIX}{self.version}"

    @property
    def bootstrap(self) -> bool:
        return self.prior_stable_sha is None


@dataclass(frozen=True)
class RemoteRefs:
    main: str
    stable: Optional[str]
    release_branch: Optional[str]
    tag_object: Optional[str]
    tag_target: Optional[str]

    def json_value(self) -> dict:
        return {
            "main": self.main,
            "stable": self.stable,
            "release_stable": self.release_branch,
            "tag_object": self.tag_object,
            "tag_target": self.tag_target,
        }


@dataclass(frozen=True)
class ReleaseObservation:
    state: str
    detail: str = ""


class Publisher:
    """Stable publication state machine with an injectable command runner."""

    def __init__(self, runner: Runner = subprocess_runner):
        self.runner = runner

    def _run(self, argv: Sequence[str]) -> subprocess.CompletedProcess:
        try:
            return self.runner(tuple(argv))
        except OSError as exc:
            raise PublishError(
                f"could not execute {argv[0]!r}: {exc}"
            ) from exc

    def _checked(self, argv: Sequence[str], label: str) -> str:
        completed = self._run(argv)
        if completed.returncode != 0:
            detail = _stderr(completed).strip() or _stdout(completed).strip()
            suffix = f": {detail}" if detail else ""
            raise PublishError(f"{label} failed{suffix}")
        return _stdout(completed)

    def read_remote_refs(
        self, spec: ReleaseSpec, *, require_candidate_main: bool = True,
    ) -> RemoteRefs:
        main_ref = "refs/heads/main"
        stable_ref = "refs/heads/stable"
        release_ref = "refs/heads/release/stable"
        tag_ref = f"refs/tags/{spec.tag}"
        peeled_ref = f"{tag_ref}^{{}}"
        output = self._checked(
            [
                "git", "ls-remote", spec.remote, main_ref, stable_ref,
                release_ref, tag_ref, peeled_ref,
            ],
            "remote ref observation",
        )
        expected = {main_ref, stable_ref, release_ref, tag_ref, peeled_ref}
        values: dict[str, str] = {}
        for raw_line in output.splitlines():
            if not raw_line.strip():
                continue
            fields = raw_line.split()
            if len(fields) != 2:
                raise PublishError(
                    f"remote ref observation returned an invalid line: {raw_line!r}"
                )
            oid, ref = fields
            if ref not in expected:
                raise PublishError(
                    f"remote ref observation returned an unexpected ref: {ref}"
                )
            strict_sha(oid, f"remote object ID for {ref}")
            if ref in values and values[ref] != oid:
                raise PublishError(f"remote ref {ref} returned conflicting values")
            values[ref] = oid

        main = values.get(main_ref)
        if main is None:
            raise PublishError("remote main is missing")
        if require_candidate_main and main != spec.candidate_sha:
            raise PublishError(
                "remote main does not equal the candidate SHA: "
                f"expected {spec.candidate_sha}, got {main}"
            )

        tag_object = values.get(tag_ref)
        tag_target = values.get(peeled_ref)
        if (tag_object is None) != (tag_target is None):
            if tag_object is not None:
                raise PublishError(
                    f"remote tag {spec.tag} is lightweight or not a commit tag; "
                    "an annotated tag is required"
                )
            raise PublishError(
                f"remote tag {spec.tag} has a peeled ref without a tag object"
            )

        return RemoteRefs(
            main=main,
            stable=values.get(stable_ref),
            release_branch=values.get(release_ref),
            tag_object=tag_object,
            tag_target=tag_target,
        )

    @staticmethod
    def classify_refs(spec: ReleaseSpec, refs: RemoteRefs) -> str:
        expected_initial_stable = spec.prior_stable_sha
        tag_absent = refs.tag_object is None and refs.tag_target is None
        if refs.stable == expected_initial_stable and tag_absent:
            return "initial"
        if (
            refs.stable == spec.candidate_sha
            and refs.tag_object is not None
            and refs.tag_target == spec.candidate_sha
        ):
            return "staged"

        if refs.tag_object is not None and refs.tag_target != spec.candidate_sha:
            raise PublishError(
                f"remote tag {spec.tag} targets {refs.tag_target}, not the candidate"
            )
        if refs.stable not in (expected_initial_stable, spec.candidate_sha):
            expected = "absent" if expected_initial_stable is None else expected_initial_stable
            raise PublishError(
                "remote stable is conflicting: expected initial "
                f"{expected} or candidate {spec.candidate_sha}, got {refs.stable}"
            )
        raise PublishError(
            "remote stable and tag are in a mixed publication state; refusing "
            "to infer or overwrite either ref"
        )

    def observe_release(self, spec: ReleaseSpec) -> ReleaseObservation:
        completed = self._run([
            "gh", "release", "view", spec.tag,
            "--json", "tagName,name,isDraft,isPrerelease",
        ])
        if completed.returncode != 0:
            detail = (_stderr(completed).strip() or _stdout(completed).strip())
            normalized = detail.lower()
            absent_markers = (
                "release not found", "http 404",
                "could not resolve to a release",
            )
            if any(marker in normalized for marker in absent_markers):
                return ReleaseObservation("absent", detail)
            return ReleaseObservation("unknown", detail)

        try:
            value = json.loads(_stdout(completed))
        except json.JSONDecodeError as exc:
            raise PublishError(
                "GitHub Release observation returned invalid JSON"
            ) from exc
        if not isinstance(value, dict):
            raise PublishError("GitHub Release observation must be a JSON object")
        expected_keys = {"tagName", "name", "isDraft", "isPrerelease"}
        if set(value) != expected_keys:
            raise PublishError(
                "GitHub Release observation has unknown or missing fields"
            )
        if value["tagName"] != spec.tag:
            raise PublishError(
                f"GitHub Release tag mismatch: expected {spec.tag!r}, "
                f"got {value['tagName']!r}"
            )
        if value["name"] != spec.title:
            raise PublishError(
                f"GitHub Release name mismatch: expected {spec.title!r}, "
                f"got {value['name']!r}"
            )
        if value["isDraft"] is not False or value["isPrerelease"] is not False:
            raise PublishError(
                "GitHub Release must be published, non-draft, and non-prerelease"
            )
        return ReleaseObservation("exists")

    def _require_known_release(
        self, observation: ReleaseObservation, operation: str
    ) -> None:
        if observation.state == "unknown":
            suffix = f": {observation.detail}" if observation.detail else ""
            raise PublishError(
                f"GitHub Release state is uncertain during {operation}{suffix}"
            )

    def _local_tag(self, spec: ReleaseSpec) -> None:
        tag_ref = f"refs/tags/{spec.tag}"
        present = self._run([
            "git", "rev-parse", "--verify", "--quiet", tag_ref,
        ])
        if present.returncode == 1 \
                and not _stdout(present).strip() \
                and not _stderr(present).strip():
            self._checked(
                ["git", "tag", "-a", spec.tag, spec.candidate_sha,
                 "-m", spec.title],
                f"create local annotated tag {spec.tag}",
            )
        elif present.returncode != 0:
            detail = _stderr(present).strip() or _stdout(present).strip()
            suffix = f": {detail}" if detail else ""
            raise PublishError(f"local tag observation failed{suffix}")

        tag_object = self._checked(
            ["git", "rev-parse", "--verify", f"{tag_ref}^{{tag}}"],
            f"verify local annotated tag {spec.tag}",
        ).strip()
        strict_sha(tag_object, f"local tag object ID for {spec.tag}")
        target = self._checked(
            ["git", "rev-parse", "--verify", f"{tag_ref}^{{commit}}"],
            f"verify local tag target {spec.tag}",
        ).strip()
        strict_sha(target, f"local tag target for {spec.tag}")
        if target != spec.candidate_sha:
            raise PublishError(
                f"local tag {spec.tag} targets {target}, not the candidate"
            )

    def _stage_push(self, spec: ReleaseSpec) -> subprocess.CompletedProcess:
        stable_ref = "refs/heads/stable"
        tag_ref = f"refs/tags/{spec.tag}"
        stable_expectation = spec.prior_stable_sha or ""
        return self._run([
            "git", "push", "--atomic",
            f"--force-with-lease={stable_ref}:{stable_expectation}",
            f"--force-with-lease={tag_ref}:",
            spec.remote,
            f"{spec.candidate_sha}:{stable_ref}",
            f"{tag_ref}:{tag_ref}",
        ])

    def _candidate_is_ancestor_of_main(
        self, spec: ReleaseSpec, observed_main: str
    ) -> bool:
        if observed_main == spec.candidate_sha:
            return True
        tracking_ref = f"refs/remotes/{spec.remote}/release-publish-main"
        self._checked(
            [
                "git", "fetch", "--no-tags", spec.remote,
                f"+refs/heads/main:{tracking_ref}",
            ],
            "fetch exact remote main for release ancestry",
        )
        fetched_main = self._checked(
            ["git", "rev-parse", "--verify", tracking_ref],
            "resolve fetched remote main for release ancestry",
        ).strip()
        strict_sha(fetched_main, "fetched remote main SHA")
        if fetched_main != observed_main:
            raise PublishError(
                "remote main changed during release ancestry verification"
            )
        ancestry = self._run([
            "git", "merge-base", "--is-ancestor",
            spec.candidate_sha, fetched_main,
        ])
        if ancestry.returncode == 0:
            return True
        if ancestry.returncode == 1:
            return False
        detail = _stderr(ancestry).strip() or _stdout(ancestry).strip()
        suffix = f": {detail}" if detail else ""
        raise PublishError(f"release candidate ancestry check failed{suffix}")

    def _require_candidate_main_ancestry(
        self,
        spec: ReleaseSpec,
        refs: RemoteRefs,
        *,
        rollback_staged: bool,
    ) -> None:
        if self._candidate_is_ancestor_of_main(spec, refs.main):
            return
        if rollback_staged:
            rolled_back = self.rollback(spec)
            if rolled_back.get("phase") != "initial":
                raise PublishError(
                    "release candidate diverged from remote main and exact "
                    "staged ref rollback did not reach the initial state"
                )
            raise PublishError(
                "release candidate diverged from remote main; exact staged "
                "candidate refs were rolled back"
            )
        raise PublishError(
            "release candidate is not an ancestor of remote main: "
            f"candidate {spec.candidate_sha}, main {refs.main}"
        )

    def stage(self, spec: ReleaseSpec) -> dict:
        refs = self.read_remote_refs(spec, require_candidate_main=False)
        phase = self.classify_refs(spec, refs)
        release = self.observe_release(spec)
        self._require_known_release(release, "stage")
        if phase == "initial" and release.state == "exists":
            raise PublishError(
                "GitHub Release exists while candidate refs are not staged"
        )
        if phase == "staged":
            self._require_candidate_main_ancestry(
                spec,
                refs,
                rollback_staged=release.state != "exists",
            )
            return self._result(
                "stage", "published" if release.state == "exists" else "resumed",
                "published" if release.state == "exists" else phase,
                spec, refs, release,
            )

        self._require_candidate_main_ancestry(
            spec, refs, rollback_staged=False
        )

        self._local_tag(spec)
        pushed = self._stage_push(spec)
        if pushed.returncode != 0:
            # A transport failure can hide a successful atomic update.  Observe
            # the complete state once and accept only the exact staged result.
            observed = self.read_remote_refs(
                spec, require_candidate_main=False
            )
            observed_phase = self.classify_refs(spec, observed)
            if observed_phase != "staged":
                detail = _stderr(pushed).strip() or _stdout(pushed).strip()
                suffix = f": {detail}" if detail else ""
                raise PublishError(f"atomic candidate ref staging failed{suffix}")
            self._require_candidate_main_ancestry(
                spec, observed, rollback_staged=True
            )
            refs = observed
            action = "reconciled"
        else:
            refs = self.read_remote_refs(
                spec, require_candidate_main=False
            )
            if self.classify_refs(spec, refs) != "staged":
                raise PublishError(
                    "atomic push returned success but candidate refs are not staged"
                )
            self._require_candidate_main_ancestry(
                spec, refs, rollback_staged=True
            )
            action = "staged"
        return self._result(
            "stage", action, "staged", spec, refs,
            ReleaseObservation("absent"),
        )

    def _rollback_push(
        self, spec: ReleaseSpec, refs: RemoteRefs
    ) -> subprocess.CompletedProcess:
        stable_ref = "refs/heads/stable"
        tag_ref = f"refs/tags/{spec.tag}"
        assert refs.tag_object is not None
        stable_destination = (
            f":{stable_ref}" if spec.bootstrap
            else f"{spec.prior_stable_sha}:{stable_ref}"
        )
        return self._run([
            "git", "push", "--atomic",
            f"--force-with-lease={stable_ref}:{spec.candidate_sha}",
            f"--force-with-lease={tag_ref}:{refs.tag_object}",
            spec.remote, stable_destination, f":{tag_ref}",
        ])

    def rollback(self, spec: ReleaseSpec) -> dict:
        # A concurrent main advance must not strand unpublished candidate refs.
        # Exact leases on stable and the tag remain the rollback authority.
        refs = self.read_remote_refs(spec, require_candidate_main=False)
        phase = self.classify_refs(spec, refs)
        release = self.observe_release(spec)
        self._require_known_release(release, "rollback")
        if release.state == "exists":
            raise PublishError(
                "rollback is forbidden after the GitHub Release exists"
            )
        if phase == "initial":
            return self._result(
                "rollback", "already-rolled-back", phase, spec, refs, release
            )

        pushed = self._rollback_push(spec, refs)
        observed = self.read_remote_refs(spec, require_candidate_main=False)
        observed_phase = self.classify_refs(spec, observed)
        if observed_phase != "initial":
            detail = _stderr(pushed).strip() or _stdout(pushed).strip()
            suffix = f": {detail}" if detail else ""
            raise PublishError(
                "exact-lease candidate ref rollback did not reach the initial state"
                f"{suffix}"
            )
        return self._result(
            "rollback", "rolled-back", observed_phase, spec, observed, release
        )

    def _delete_release_branch(
        self, spec: ReleaseSpec, expected_sha: Optional[str]
    ) -> tuple[str, RemoteRefs]:
        refs = self.read_remote_refs(spec, require_candidate_main=False)
        if self.classify_refs(spec, refs) != "staged":
            raise PublishError(
                "candidate refs changed before release/stable cleanup"
            )
        if expected_sha is None:
            return "not-requested", refs
        strict_sha(expected_sha, "release/stable SHA")
        if refs.release_branch is None:
            return "already-absent", refs
        if refs.release_branch != expected_sha:
            raise PublishError(
                "release/stable moved before cleanup: expected "
                f"{expected_sha}, got {refs.release_branch}"
            )
        release_ref = "refs/heads/release/stable"
        deleted = self._run([
            "git", "push",
            f"--force-with-lease={release_ref}:{expected_sha}",
            spec.remote, f":{release_ref}",
        ])
        observed = self.read_remote_refs(
            spec, require_candidate_main=False
        )
        if self.classify_refs(spec, observed) != "staged":
            raise PublishError(
                "candidate refs changed during release/stable cleanup"
            )
        if observed.release_branch is not None:
            detail = _stderr(deleted).strip() or _stdout(deleted).strip()
            suffix = f": {detail}" if detail else ""
            raise PublishError(
                f"exact-lease release/stable cleanup failed{suffix}"
            )
        return "deleted", observed

    def finalize(
        self,
        spec: ReleaseSpec,
        notes_file: str,
        release_branch_sha: Optional[str] = None,
    ) -> dict:
        if release_branch_sha is not None:
            strict_sha(release_branch_sha, "release/stable SHA")
        refs = self.read_remote_refs(spec, require_candidate_main=False)
        phase = self.classify_refs(spec, refs)
        if phase != "staged":
            raise PublishError(
                "candidate refs must be staged before finalization"
            )
        release = self.observe_release(spec)
        self._require_known_release(release, "finalize")
        self._require_candidate_main_ancestry(
            spec,
            refs,
            rollback_staged=release.state != "exists",
        )
        action = "reconciled"
        if release.state == "absent":
            created = self._run([
                "gh", "release", "create", spec.tag, "--verify-tag",
                "--title", spec.title, "--notes-file", notes_file,
            ])
            # Always observe after create, including failures: the command may
            # have committed server-side before its response was lost.
            release = self.observe_release(spec)
            if release.state != "exists":
                detail = _stderr(created).strip() or _stdout(created).strip()
                create_state = (
                    "failed" if created.returncode != 0 else "reported success"
                )
                observed_state = release.state
                suffix = f": {detail}" if detail else ""
                raise PublishError(
                    f"GitHub Release create {create_state}, then observation was "
                    f"{observed_state}; candidate refs were preserved{suffix}"
                )
            action = "reconciled-after-create-failure" if created.returncode else "created"

        cleanup, refs = self._delete_release_branch(
            spec, release_branch_sha
        )
        release = self.observe_release(spec)
        if release.state != "exists":
            raise PublishError(
                "GitHub Release changed during final verification"
            )
        result = self._result(
            "finalize", action, "published", spec, refs,
            release,
        )
        result["release_branch_cleanup"] = cleanup
        return result

    @staticmethod
    def _result(
        command: str,
        action: str,
        phase: str,
        spec: ReleaseSpec,
        refs: RemoteRefs,
        release: ReleaseObservation,
    ) -> dict:
        return {
            "ok": True,
            "command": command,
            "action": action,
            "phase": phase,
            "version": spec.version,
            "tag": spec.tag,
            "candidate_sha": spec.candidate_sha,
            "prior_stable_sha": spec.prior_stable_sha,
            "refs": refs.json_value(),
            "github_release": release.state,
        }


def _add_spec_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--version", required=True)
    parser.add_argument("--candidate-sha", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--bootstrap", action="store_true")
    mode.add_argument("--prior-stable-sha")
    parser.add_argument("--remote", default="origin")


class JsonArgumentParser(argparse.ArgumentParser):
    """Route usage errors through the CLI's JSON error contract."""

    def error(self, message: str) -> None:
        raise PublishError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("stage", "rollback"):
        subparser = subparsers.add_parser(command)
        _add_spec_arguments(subparser)
    finalize = subparsers.add_parser("finalize")
    _add_spec_arguments(finalize)
    finalize.add_argument("--notes-file", required=True)
    finalize.add_argument("--release-branch-sha")
    return parser


def _spec_from_args(args: argparse.Namespace) -> ReleaseSpec:
    return ReleaseSpec(
        version=args.version,
        candidate_sha=args.candidate_sha,
        prior_stable_sha=(
            None if args.bootstrap else args.prior_stable_sha
        ),
        remote=args.remote,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        spec = _spec_from_args(args)
        publisher = Publisher()
        if args.command == "stage":
            result = publisher.stage(spec)
        elif args.command == "rollback":
            result = publisher.rollback(spec)
        else:
            notes = Path(args.notes_file)
            if not notes.is_file():
                raise PublishError(f"release notes file is missing: {notes}")
            result = publisher.finalize(
                spec, str(notes), args.release_branch_sha
            )
    except PublishError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
