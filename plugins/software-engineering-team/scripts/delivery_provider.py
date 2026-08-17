#!/usr/bin/env python3
"""Provider-neutral boundary with a small GitHub/gh adapter.

The adapter never stores credentials in the project. It is intentionally
stdlib-only so a host can fail closed before Delivery activation when ``gh``
or an authenticated GitHub session is unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


class ProviderError(RuntimeError):
    """A provider capability, policy or response cannot satisfy the contract."""


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True,
                            capture_output=True, check=False)
    if result.returncode:
        raise ProviderError(result.stderr.strip() or "git command failed")
    return result.stdout.strip()


def run_gh(root: Path, *args: str) -> str:
    if shutil.which("gh") is None:
        raise ProviderError("GitHub provider requires the authenticated gh CLI")
    result = subprocess.run(["gh", *args], cwd=root, text=True,
                            capture_output=True, check=False)
    if result.returncode:
        raise ProviderError(result.stderr.strip() or "GitHub provider command failed")
    return result.stdout.strip()


def repository_from_remote(root: Path, remote: str = "origin") -> str:
    value = run_git(root, "remote", "get-url", remote)
    if value.startswith("git@github.com:"):
        value = "https://github.com/" + value.split(":", 1)[1]
    parsed = urlsplit(value)
    if parsed.netloc != "github.com":
        raise ProviderError("Delivery PR provider requires a GitHub remote")
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or not all(parts):
        raise ProviderError("GitHub remote must identify exactly owner/repository")
    return "/".join(parts)


class GitHubProvider:
    name = "github"

    _PR_JSON_FIELDS = (
        "number,url,state,isDraft,headRefName,headRefOid,baseRefName,baseRefOid,"
        "headRepository,mergeCommit,mergedAt,mergeStateStatus,statusCheckRollup"
    )

    def __init__(self, root: Path, remote: str = "origin"):
        self.root = root
        self.remote = remote
        self.repository = repository_from_remote(root, remote)

    def list_pull_requests(self, head: str, base: str) -> list[dict]:
        raw = run_gh(
            self.root, "pr", "list", "--repo", self.repository, "--state", "all",
            "--head", head, "--base", base,
            "--json", self._PR_JSON_FIELDS,
        )
        try:
            value = json.loads(raw or "[]")
        except json.JSONDecodeError as exc:
            raise ProviderError("GitHub returned invalid PR JSON") from exc
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise ProviderError("GitHub PR response shape is invalid")
        return value

    def inspect_pull_request(self, url: str) -> dict:
        """Return one provider PR object with the fields used by closure.

        The adapter deliberately validates the response shape here rather
        than letting callers treat a missing merge object or malformed
        provider response as a successful handoff.
        """
        raw = run_gh(self.root, "pr", "view", url, "--repo", self.repository,
                     "--json", self._PR_JSON_FIELDS)
        try:
            value = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise ProviderError("GitHub returned invalid PR JSON") from exc
        if not isinstance(value, dict):
            raise ProviderError("GitHub PR response shape is invalid")
        if not isinstance(value.get("url"), str) or not value["url"].startswith("https://github.com/"):
            raise ProviderError("GitHub PR response has no canonical URL")
        return value

    def exact_unmerged(self, head: str, base: str) -> list[dict]:
        return [item for item in self.list_pull_requests(head, base)
                if item.get("headRefName") == head and item.get("baseRefName") == base
                and str(item.get("state", "")).upper() != "MERGED"]

    def create_draft(self, head: str, base: str, title: str, body: str) -> dict:
        result = run_gh(
            self.root, "pr", "create", "--repo", self.repository,
            "--head", head, "--base", base, "--draft", "--title", title,
            "--body", body,
        )
        url = result.strip().splitlines()[-1] if result.strip() else ""
        if not url.startswith("https://github.com/"):
            raise ProviderError("GitHub did not return a canonical PR URL")
        return {"url": url}

    def ensure_draft(self, url: str) -> dict:
        run_gh(self.root, "pr", "ready", url, "--undo")
        return {"url": url, "draft": True}

    def make_ready(self, url: str) -> dict:
        run_gh(self.root, "pr", "ready", url)
        return {"url": url, "draft": False}

    def merge_commit(self, url: str, head_oid: str) -> dict:
        run_gh(
            self.root, "pr", "merge", url, "--merge", "--match-head-commit", head_oid,
            "--delete-branch=false",
        )
        value = self.inspect_pull_request(url)
        if str(value.get("state", "")).upper() != "MERGED":
            raise ProviderError("GitHub PR merge call returned before the PR was merged")
        merge_value = value.get("mergeCommit")
        merge_oid = merge_value.get("oid") if isinstance(merge_value, dict) else merge_value
        if not isinstance(merge_oid, str) or not merge_oid:
            raise ProviderError("GitHub PR has no provider-confirmed merge commit")
        observed_head = value.get("headRefOid")
        if observed_head != head_oid:
            raise ProviderError("GitHub PR head changed during merge")
        return {"url": value["url"], "head": head_oid,
                "merge_commit": merge_oid, "provider": value}
