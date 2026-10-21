#!/usr/bin/env python3
"""Resolve the main worktree of a project checkout and its runtime anchor."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, encoding="utf-8",
                            capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def runtime_root(main_worktree: Path) -> Path:
    """Return the project-local disposable runtime anchor."""
    return main_worktree / ".agentrof" / "agent-marketplace" / ".runtime"


def main_worktree(root: Path) -> Path:
    common = Path(run_git(root, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (root / common).resolve()
    output = run_git(root, "worktree", "list", "--porcelain")
    records = output.split("\n\n") if output else []
    candidates = []
    for record in records:
        line = next((line for line in record.splitlines() if line.startswith("worktree ")), "")
        if line:
            candidate = Path(line.removeprefix("worktree ")).resolve()
            candidate_common = (candidate / ".git").resolve() if (candidate / ".git").is_file() else candidate / ".git"
            if candidate_common.exists() or candidate == root.resolve():
                candidates.append(candidate)
    if not candidates:
        raise RuntimeError("main worktree cannot be resolved")
    for candidate in candidates:
        if (candidate / "workspace" / "config.json").exists():
            return candidate
    return candidates[0]


def runtime_anchor(root: Path) -> Path:
    return main_worktree(root) if (root / ".git").is_file() else root


def safe_runtime_path(root: Path, path: Path, *, file_only: bool = False) -> Path:
    """Reject aliases before accessing runtime, including native Windows junctions."""
    anchor = runtime_anchor(root.resolve()).resolve()
    if path != anchor and anchor not in path.parents:
        raise RuntimeError("runtime path must remain inside its main worktree")
    for parent in (path, *path.parents):
        if parent == anchor:
            break
        try:
            metadata = parent.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise RuntimeError("runtime path must not contain symlinks or junctions")
        if parent == path and file_only and (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1):
            raise RuntimeError("runtime files must be regular and unshared")
    return path
