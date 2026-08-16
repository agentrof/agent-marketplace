#!/usr/bin/env python3
"""File an explicitly approved issue to the Agent Marketplace repository.

The target is deliberately fixed. Drafts and approval belong in the project
workspace; this command only performs the final external filing step.
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

MARKETPLACE_REPO = "agentrof/agent-marketplace"


def token() -> str:
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(name, "").strip():
            return os.environ[name].strip()
    if shutil.which("gh"):
        result = subprocess.run(["gh", "auth", "token"], capture_output=True,
                                text=True, timeout=15, check=False)
        if result.returncode == 0:
            return result.stdout.strip()
    return ""


def create_issue(title: str, body: str) -> str:
    if shutil.which("gh"):
        result = subprocess.run(
            ["gh", "issue", "create", "--repo", MARKETPLACE_REPO,
             "--title", title, "--body", body], capture_output=True,
            text=True, timeout=60, check=False)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "gh issue create failed")
        return next((line.strip() for line in reversed(result.stdout.splitlines())
                     if line.strip()), "")
    auth = token()
    if not auth:
        raise RuntimeError("no gh CLI or GH_TOKEN/GITHUB_TOKEN; nothing was posted")
    payload = json.dumps({"title": title, "body": body}).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{MARKETPLACE_REPO}/issues", data=payload,
        method="POST", headers={"Authorization": f"Bearer {auth}",
                                  "Accept": "application/vnd.github+json",
                                  "Content-Type": "application/json",
                                  "User-Agent": "agent-marketplace"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return str(json.loads(response.read().decode("utf-8")).get("html_url", ""))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"GitHub API {exc.code}: {exc.read().decode(errors='replace')}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True)
    parser.add_argument("--body", default="")
    parser.add_argument("--body-file", default="")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else args.body
    if args.dry_run:
        print(f"file_issue: would file to {MARKETPLACE_REPO}: {args.title}")
        return 0
    try:
        print(create_issue(args.title, body))
    except (OSError, RuntimeError) as exc:
        print(f"file_issue: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
