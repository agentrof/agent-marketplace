#!/usr/bin/env python3
"""File one approved issue candidate to the marketplace's own tracker.

This script exists for exactly one purpose: opening issues about Agent
Marketplace on its own repository. The target is locked to a single
repository and cannot be redirected: there is no argument to change it, and if
a reachable marketplace manifest declares a different repository the script
refuses rather than file elsewhere.

Filing prefers the `gh` CLI when present and falls back to the GitHub REST API
with a token (GH_TOKEN / GITHUB_TOKEN, or `gh auth token`). The created issue
URL is printed on stdout so the caller can record it with
`pmo_cli.py issue file --url <url>`.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# The one and only target. This script never files anywhere else.
MARKETPLACE_REPO = "agentrof/agent-marketplace"

# Marketplace catalog location. If reachable above this script, it is
# read to cross-check the locked target.
MANIFEST_RELPATHS = (
    Path(".claude-plugin") / "marketplace.json",
)


def _slug(repo_url: str) -> str:
    """owner/repo from a GitHub URL; '' when it is not derivable."""
    slug = repo_url.strip().rstrip("/")
    for prefix in ("https://github.com/", "http://github.com/",
                   "git@github.com:"):
        if slug.startswith(prefix):
            slug = slug[len(prefix):]
            break
    if slug.endswith(".git"):
        slug = slug[:-4]
    return slug if slug.count("/") == 1 else ""


def _repo_from_manifest() -> str:
    """The repository slug the nearest marketplace manifest declares, or ''
    when no manifest is reachable above this script (e.g. the synced launcher
    copy that lives outside the marketplace tree)."""
    here = Path(__file__).resolve().parent
    for parent in (here, *here.parents):
        for rel in MANIFEST_RELPATHS:
            manifest = parent / rel
            if manifest.is_file():
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return ""
                return _slug(data.get("repository", ""))
    return ""


def resolve_target() -> str:
    """The locked target, cross-checked against the manifest when present."""
    declared = _repo_from_manifest()
    if declared and declared != MARKETPLACE_REPO:
        raise SystemExit(
            f"file_issue: marketplace manifest declares '{declared}', which is"
            f" not the locked target '{MARKETPLACE_REPO}'; refusing to file")
    return MARKETPLACE_REPO


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _token() -> str:
    for var in ("GH_TOKEN", "GITHUB_TOKEN"):
        value = os.environ.get(var, "").strip()
        if value:
            return value
    if _gh_available():
        try:
            out = subprocess.run(["gh", "auth", "token"], capture_output=True,
                                 text=True, timeout=15)
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return ""


def file_via_gh(repo: str, title: str, body: str) -> str:
    out = subprocess.run(
        ["gh", "issue", "create", "--repo", repo, "--title", title,
         "--body", body],
        capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise SystemExit(f"file_issue: gh failed: {out.stderr.strip()}")
    lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def file_via_api(repo: str, title: str, body: str, token: str) -> str:
    payload = json.dumps({"title": title, "body": body}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues",
        data=payload, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "agent-marketplace-issue-desk",
            "Content-Type": "application/json",
        })
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"file_issue: GitHub API {exc.code}: {body}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"file_issue: network error: {exc.reason}")
    return data.get("html_url", "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="File one issue to the marketplace's own repository.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", default="")
    parser.add_argument("--body-file", default="")
    parser.add_argument("--dry-run", action="store_true",
                        help="resolve the target and exit without posting")
    args = parser.parse_args(argv)

    repo = resolve_target()
    body = args.body
    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")

    if args.dry_run:
        print(f"file_issue: would file to {repo}: {args.title}")
        return 0

    if _gh_available():
        print(file_via_gh(repo, args.title, body))
        return 0
    token = _token()
    if not token:
        print("file_issue: no gh CLI and no token (set GH_TOKEN/GITHUB_TOKEN"
              " or run `gh auth login`); nothing was posted", file=sys.stderr)
        return 1
    print(file_via_api(repo, args.title, body, token))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
