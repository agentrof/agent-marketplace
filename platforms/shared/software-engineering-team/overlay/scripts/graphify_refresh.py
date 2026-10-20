#!/usr/bin/env python3
"""Refresh the docs knowledge graph after a merged task, in the background.

Given the merged commit range, it re-extracts the docs folder with the
``claude-cli`` backend only when Markdown under the docs folder changed;
graphify's own manifest re-reads only the changed files. The graph lives in
``$GRAPHIFY_OUT/docs`` (default ``.agentrof/graphify/docs``), apart from the
code graph the graphify git hook keeps. The first full docs build is the
owner's call, so nothing runs before that graph exists. It never blocks the
caller, logs to ``.agentrof/graphify/refresh.log`` and exits 0 when graphify
is missing.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import file_lock


def log(out: Path, message: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "refresh.log").open("a", encoding="utf-8") as handle:
        handle.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {message}\n")


def changed_docs(root: Path, commit_range: str, docs: str) -> list[str]:
    result = subprocess.run(["git", "diff", "--name-only", commit_range, "--", docs],
                            cwd=root, capture_output=True, text=True, check=False)
    return [line for line in result.stdout.splitlines() if line.endswith(".md")]


def refresh(root: Path, commit_range: str, docs: str) -> None:
    out = root / os.environ.get("GRAPHIFY_OUT", ".agentrof/graphify")
    if shutil.which("graphify") is None:
        log(out, "skip: graphify not on PATH")
        return
    changed = changed_docs(root, commit_range, docs)
    if not changed:
        log(out, f"skip: no Markdown under {docs} changed in {commit_range}")
        return
    graph = out / "docs"
    if not (graph / "graph.json").is_file():
        log(out, f"skip: no docs graph yet; build it once with GRAPHIFY_OUT={graph}"
                 f" graphify extract {docs} --backend claude-cli")
        return
    with (out / "refresh.lock").open("w") as lock:
        if not file_lock.try_lock(lock.fileno()):
            # The running refresh or the next one picks these up from the manifest.
            log(out, f"skip: another refresh is running; {len(changed)} file(s) wait for the next one")
            return
        log(out, f"start: {len(changed)} changed file(s) in {commit_range}")
        with (out / "refresh.log").open("a", encoding="utf-8") as handle:
            result = subprocess.run(
                ["graphify", "extract", docs, "--backend", "claude-cli"], cwd=root,
                env={**os.environ, "GRAPHIFY_OUT": str(graph.resolve())},
                stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT, check=False)
        log(out, f"done: graphify exit {result.returncode}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--range", required=True, dest="commit_range", help="BASE..HEAD of the merge")
    parser.add_argument("--docs", default="workspace/docs")
    parser.add_argument("--wait", action="store_true", help="run in the foreground")
    args = parser.parse_args(argv)
    root = Path(args.project_root).resolve()
    if not args.wait:
        subprocess.Popen([sys.executable, __file__, "--project-root", str(root), "--range",
                          args.commit_range, "--docs", args.docs, "--wait"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return 0
    try:
        refresh(root, args.commit_range, args.docs)
    except OSError as error:
        print(f"graphify_refresh: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
