#!/usr/bin/env python3
"""File one approved stdin payload to the fixed Agent Marketplace repository."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request


MARKETPLACE_REPO = "agentrof/agent-marketplace"
MARKETPLACE_URL_RE = re.compile(
    r"^https://github\.com/agentrof/agent-marketplace/issues/([0-9]+)/?$"
)


class NotOpened(RuntimeError):
    """The request was rejected before an issue could be created."""


class OutcomeUnknown(RuntimeError):
    """A remote request was attempted but its outcome cannot be confirmed."""


def token() -> str:
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        if os.environ.get(name, "").strip():
            return os.environ[name].strip()
    return ""


def canonical_url(value: str) -> tuple[str, str]:
    url = value.strip()
    match = MARKETPLACE_URL_RE.fullmatch(url)
    if match is None:
        raise OutcomeUnknown(
            "GitHub returned no canonical marketplace issue URL"
        )
    return url.rstrip("/"), match.group(1)


def create_with_gh(title: str, body: str) -> str:
    try:
        auth = subprocess.run(
            ["gh", "auth", "status", "--hostname", "github.com"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise NotOpened("GitHub CLI authentication check timed out") from exc
    except OSError as exc:
        raise NotOpened(f"GitHub CLI authentication check failed: {exc}") from exc
    if auth.returncode:
        detail = auth.stderr.strip() or auth.stdout.strip()
        raise NotOpened(detail or "GitHub CLI is not authenticated")

    payload = json.dumps({"title": title, "body": body}, ensure_ascii=False)
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                "--method",
                "POST",
                f"repos/{MARKETPLACE_REPO}/issues",
                "--input",
                "-",
                "--jq",
                ".html_url",
            ],
            input=payload,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise OutcomeUnknown("GitHub CLI request timed out") from exc
    except OSError as exc:
        raise NotOpened(f"GitHub CLI request could not start: {exc}") from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise OutcomeUnknown(detail or "GitHub CLI request failed")
    return canonical_url(result.stdout)[0]


def create_with_api(title: str, body: str, auth: str) -> str:
    payload = json.dumps(
        {"title": title, "body": body}, ensure_ascii=False
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.github.com/repos/{MARKETPLACE_REPO}/issues",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {auth}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "agent-marketplace",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace").strip()
        message = f"GitHub API {exc.code}" + (f": {detail}" if detail else "")
        if 400 <= exc.code < 500:
            raise NotOpened(message) from exc
        raise OutcomeUnknown(message) from exc
    except (TimeoutError, urllib.error.URLError) as exc:
        reason = getattr(exc, "reason", exc)
        raise OutcomeUnknown(f"network error: {reason}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise OutcomeUnknown("GitHub returned an unreadable response") from exc
    if not isinstance(raw, dict):
        raise OutcomeUnknown("GitHub returned an unexpected response")
    return canonical_url(str(raw.get("html_url", "")))[0]


def create_issue(title: str, body: str) -> str:
    if shutil.which("gh"):
        return create_with_gh(title, body)
    auth = token()
    if not auth:
        raise NotOpened("no authenticated gh CLI or GH_TOKEN/GITHUB_TOKEN")
    return create_with_api(title, body, auth)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True)
    args = parser.parse_args(argv)
    title = args.title.strip()
    body = sys.stdin.read().strip()
    if not title:
        print("file_issue: Not opened: title is empty", file=sys.stderr)
        return 2
    if not body:
        print("file_issue: Not opened: stdin body is empty", file=sys.stderr)
        return 2
    try:
        url = create_issue(title, body)
        url, number = canonical_url(url)
    except NotOpened as exc:
        print(f"file_issue: Not opened: {exc}", file=sys.stderr)
        return 2
    except (OSError, OutcomeUnknown) as exc:
        print(
            "file_issue: Outcome unknown, do not retry automatically: "
            f"{exc}",
            file=sys.stderr,
        )
        return 3
    print(f"Opened #{number}: {url}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
