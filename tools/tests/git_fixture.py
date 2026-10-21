"""Git fixture repositories whose removal cannot race Git's own writes."""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterator

# maintenance.auto turns off automatic maintenance of every strategy since Git
# 2.30; gc.auto covers older Git, whose only automatic task is gc.
AUTOMATIC_MAINTENANCE_OFF = (("maintenance.auto", "false"), ("gc.auto", "0"))


def disable_automatic_maintenance(git_dir: Path) -> None:
    """Keep every later Git command in this repository from starting maintenance."""
    for key, value in AUTOMATIC_MAINTENANCE_OFF:
        subprocess.run(["git", "--git-dir", str(git_dir), "config", key, value], check=True)


def init_repository(path: Path, bare: bool = False, initial_branch: str | None = None) -> None:
    """Create a fixture repository with automatic maintenance already disabled.

    The maintenance that a commit, merge, rebase, fetch or received push starts
    detaches on POSIX hosts and keeps writing into the repository after that
    command returns, so removing the tree right away fails on a directory that
    refills while it is being emptied. Since Git 2.54 the default geometric
    strategy repacks once a repository holds a few hundred loose objects.
    """
    command = ["git", "init", "-q"]
    if bare:
        command.append("--bare")
    if initial_branch is not None:
        command += ["-b", initial_branch]
    subprocess.run([*command, str(path)], check=True)
    disable_automatic_maintenance(path if bare else path / ".git")


def remove_temporary(temporary: tempfile.TemporaryDirectory, attempts: int = 10) -> None:
    """Remove a fixture tree, retrying while git finishes writes that outlive the call that started them.

    Python 3.9 runs TemporaryDirectory.cleanup() only once, so retries remove
    the tree directly. A tree that survives every attempt raises.
    """
    tree = Path(temporary.name)
    for attempt in range(attempts):
        try:
            if attempt == 0:
                temporary.cleanup()
            else:
                shutil.rmtree(tree)
            return
        except OSError:
            if not tree.exists():
                return
            if attempt == attempts - 1:
                shutil.rmtree(tree, ignore_errors=True)
                if tree.exists():
                    raise
                return
            time.sleep(0.2)


@contextlib.contextmanager
def temporary_directory() -> Iterator[str]:
    """Yield a temporary directory name and remove the tree through remove_temporary."""
    temporary = tempfile.TemporaryDirectory()
    try:
        yield temporary.name
    finally:
        remove_temporary(temporary)
