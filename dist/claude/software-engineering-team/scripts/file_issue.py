#!/usr/bin/env python3
"""File one approved stdin payload to the fixed Agent Marketplace repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


MARKETPLACE_REPO = "agentrof/agent-marketplace"
MARKETPLACE_URL_RE = re.compile(
    r"^https://github\.com/agentrof/agent-marketplace/issues/([0-9]+)/?$"
)
# The home-directory paths that the repository validator refuses in package
# text: /Users/<name>, /home/<name> and C:\Users\<name>, under a WSL, Cygwin
# or Git Bash drive segment too, and the -Users-<name> forms a host's
# project folder name encodes one as. A test keeps both patterns equal.
HOME_PATH_RE = re.compile(
    r"(?:(?<![\w.~$/-])|(?<=file://)|(?<=/mnt/[a-z])|(?<=/cygdrive/[a-z])"
    r"|(?<=(?<![\w.~$/-])/[a-z]))/(?:Users|home)/(?P<posix>[A-Za-z0-9._-]+)"
    r"|(?<![A-Za-z0-9])[A-Za-z]:[\\/]+(?i:users)[\\/]+"
    r"(?P<windows>[^\\/:*?\"<>|\s%${}]+(?: [^\\/:*?\"<>|\s%${}]+)*)"
    r"|(?:(?<![A-Za-z0-9-])|(?<![A-Za-z0-9])[A-Za-z]-|(?<=[0-9-]-))"
    r"-(?:Users-(?P<encoded>[A-Za-z0-9]+)|home-(?P<encoded_home>[A-Za-z0-9]+)-)"
    r"|(?<=projects[/\\])-home-(?P<projects_home>[A-Za-z0-9]+)"
)
# The homes of system and service accounts name no person, so a CI or
# container path passes; the validator passes the same.
SYSTEM_HOMES = frozenset({"runner", "node", "linuxbrew", "vscode", "ubuntu",
                          "Shared", "Public", "Default"})
# The scp-like remote Git accepts, with or without a user; a one-letter host
# is a Windows drive.
SCP_REMOTE_RE = re.compile(r"^(?:[^@/\s]+@)?[^@:/\s]{2,}:(?P<path>\S+)$")
# A payload is also checked decoded: percent-encoding, a JSON-escaped slash
# and an editor or file URL prefix before the path it opens.
DECODINGS = (
    (re.compile(r"(?:%[0-9A-Fa-f]{2})+"), lambda match: urllib.parse.unquote(match.group(0))),
    (re.compile(r"\\/"), lambda match: "/"),
    (re.compile(r"(?:vscode://file|file://[^/\s]*)(?=/)", re.IGNORECASE), lambda match: ""),
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


def git_lines(cwd: str, *args: str) -> list[str]:
    """What a read-only git command prints in cwd; nothing outside a checkout."""
    try:
        result = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return result.stdout.splitlines() if result.returncode == 0 else []


def repository_name(url: str) -> str:
    """The owner/repo path a hosted remote names, or "" for a local remote."""
    if "://" in url:
        parts = urllib.parse.urlsplit(url)
        if not parts.netloc or parts.scheme == "file":
            return ""
        path = parts.path
    else:
        scp = SCP_REMOTE_RE.match(url)
        if scp is None:
            return ""
        path = scp.group("path")
    path = path.strip("/")
    path = path[:-4] if path.endswith(".git") else path
    return path if "/" in path else ""


def remote_urls(top: str) -> list[str]:
    """Each URL of each remote, as configured and as Git rewrites it through
    insteadOf and pushInsteadOf."""
    urls = [line.partition(" ")[2].strip() for line in git_lines(
        top, "config", "--get-regexp", r"^remote\..*\.(url|pushurl)$")]
    for name in git_lines(top, "remote"):
        urls += git_lines(top, "remote", "get-url", "--all", name)
        urls += git_lines(top, "remote", "get-url", "--push", "--all", name)
    return [url for url in dict.fromkeys(urls) if url]


def home_relative_forms(path: str) -> list[str]:
    """path as shells and hosts abbreviate it from the home directory."""
    forms = []
    home = os.path.expanduser("~")
    written = path.replace("\\", "/")
    for base in {home, os.path.realpath(home)}:
        prefix = base.replace("\\", "/").rstrip("/") + "/"
        if len(prefix) > 2 and written.startswith(prefix) and written != prefix:
            rest = written[len(prefix):]
            forms += [f"~/{rest}", f"$HOME/{rest}", f"${{HOME}}/{rest}",
                      "%USERPROFILE%\\" + rest.replace("/", "\\")]
    return forms


def project_fragments(top: str) -> list[tuple[str, str]]:
    """The reporting checkout's path in each written form, its remote URLs,
    their owner/repo and the project's name, by kind. The checkout folder and
    each remote's repository name the project, unless it is this
    marketplace."""
    fragments = []
    for path in {top, os.path.realpath(top)}:
        for form in {path, os.path.normpath(path), path.replace("\\", "/")}:
            if len(os.path.normpath(form).strip("/\\").replace("\\", "/").split("/")) > 1:
                fragments.append(("checkout path", form))
        fragments += [("checkout path", form) for form in home_relative_forms(path)]
    names = {os.path.basename(path.rstrip("/\\")) for path in (top, os.path.realpath(top))}
    for url in remote_urls(top):
        name = repository_name(url)
        if name.lower() == MARKETPLACE_REPO:
            continue
        fragments.append(("remote URL", url))
        if url.endswith(".git"):
            fragments.append(("remote URL", url[:-4]))
        if name:
            fragments.append(("remote repository name", name))
            names.add(name.rsplit("/", 1)[-1])
    marketplace = MARKETPLACE_REPO.rsplit("/", 1)[-1]
    fragments += [("project name", name) for name in sorted(names)
                  if name and name.lower() != marketplace]
    return fragments


def home_path_user(match: re.Match) -> str:
    """The user name a HOME_PATH_RE match writes out, whichever form it has."""
    return next((value for value in match.groupdict().values() if value), "")


def decoded(text: str) -> tuple[str, list[int]]:
    """text after DECODINGS, with the index each of its characters has in
    text, so a fragment found decoded is reported where it was written."""
    origin = list(range(len(text)))
    for pattern, replace in DECODINGS:
        pieces: list[str] = []
        mapped: list[int] = []
        last = 0
        for match in pattern.finditer(text):
            replacement = replace(match)
            pieces += [text[last:match.start()], replacement]
            mapped += origin[last:match.start()] + [origin[match.start()]] * len(replacement)
            last = match.end()
        text, origin = "".join(pieces) + text[last:], mapped + origin[last:]
    return text, origin


def fragment_spans(text: str, fragments: list[tuple[str, str]]) -> set[tuple[int, int, str]]:
    """Where text holds a home-directory path or one of fragments, by kind. A
    project name counts as a word in any case."""
    spans = {(match.start(), match.end(), "home-directory path")
             for match in HOME_PATH_RE.finditer(text)
             if home_path_user(match) not in SYSTEM_HOMES}
    for kind, value in fragments:
        if kind == "project name":
            pattern = rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])"
        else:
            pattern = rf"(?<![A-Za-z0-9._-]){re.escape(value)}(?![A-Za-z0-9_-])"
        spans |= {(match.start(), match.end(), kind)
                  for match in re.finditer(pattern, text, re.IGNORECASE)}
    return spans


def identifying_fragments(field: str, text: str, fragments: list[tuple[str, str]]) -> list[str]:
    """Each fragment kind found in text, as written or decoded, with its
    position, never its value; a match inside a longer one, such as the home
    inside a checkout path, is reported once."""
    spans = fragment_spans(text, fragments)
    plain, origin = decoded(text)
    if plain != text:
        spans |= {(origin[start], origin[end - 1] + 1, kind)
                  for start, end, kind in fragment_spans(plain, fragments)}
    kept: list[tuple[int, int, str]] = []
    for start, end, kind in sorted(spans, key=lambda span: (span[0], span[0] - span[1], span[2])):
        if not any(start >= low and end <= high for low, high, _kind in kept):
            kept.append((start, end, kind))
    found = []
    for start, _end, kind in kept:
        line = text.count("\n", 0, start) + 1
        column = start - text.rfind("\n", 0, start)
        found.append(f"{kind} at {field} line {line}, column {column}")
    return found


def payload_hash(title: str, body: str) -> str:
    payload = json.dumps({"repository": MARKETPLACE_REPO, "title": title.strip(),
                          "body": body.strip()}, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", required=True)
    parser.add_argument("--preview", action="store_true", help="validate and print the exact payload without network access")
    parser.add_argument("--approved-payload-sha256", help="hash of the exact preview the user explicitly approved")
    parser.add_argument(
        "--project-root",
        help="the project the report comes from; the current directory by default",
    )
    args = parser.parse_args(argv)
    title = args.title.strip()
    body = sys.stdin.read().strip()
    if not title:
        print("file_issue: Not opened: title is empty", file=sys.stderr)
        return 2
    if not body:
        print("file_issue: Not opened: stdin body is empty", file=sys.stderr)
        return 2
    root = args.project_root or os.getcwd()
    if not os.path.isdir(root):
        print("file_issue: Not opened: --project-root is not a directory", file=sys.stderr)
        return 2
    top = git_lines(root, "rev-parse", "--show-toplevel")
    if not top:
        print(
            "file_issue: notice: no Git checkout at the project root, so only"
            " home-directory paths are checked",
            file=sys.stderr,
        )
    fragments = project_fragments(top[0]) if top else []
    found = (identifying_fragments("title", title, fragments)
             + identifying_fragments("body", body, fragments))
    if found:
        print(
            "file_issue: Not opened: the title or body identifies the reporting"
            " project: " + "; ".join(found) + ". Reword each one, then preview"
            " and approve the payload again.",
            file=sys.stderr,
        )
        return 2
    expected = payload_hash(title, body)
    if args.preview:
        print(json.dumps({"repository": MARKETPLACE_REPO, "title": title, "body": body,
                          "payload_sha256": expected}, ensure_ascii=False))
        return 0
    if args.approved_payload_sha256 != expected:
        print("file_issue: Not opened: preview and obtain explicit user approval of this exact payload; "
              "the approved payload hash is missing or does not match", file=sys.stderr)
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
