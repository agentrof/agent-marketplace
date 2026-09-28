"""Independent copies of immutable, pre-runtime repository fixtures."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path

if __package__:
    from .git_fixture import remove_temporary
else:
    from git_fixture import remove_temporary


_PHASE_TOTALS = {"seed_build": 0.0, "seed_validate": 0.0, "seed_copy": 0.0}


def phase_totals() -> dict[str, float]:
    return dict(_PHASE_TOTALS)


class RepositorySeedCache:
    """A seed never contains live worktrees, local receipts or shared objects."""

    def __init__(self):
        self.temporary = None
        self.root = None
        self.fingerprint = None

    @staticmethod
    def snapshot(root: Path) -> str:
        root_mode = root.lstat()
        if root.is_symlink() or getattr(root_mode, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise AssertionError("fixture seed cannot contain links or junctions")
        if not stat.S_ISDIR(root_mode.st_mode):
            raise AssertionError("fixture seed root must be a directory")
        records = [("", "directory", stat.S_IMODE(root_mode.st_mode))]
        pending = [root]
        while pending:
            directory = pending.pop()
            for path in sorted(directory.iterdir()):
                mode = path.lstat()
                if path.is_symlink() or getattr(mode, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                    raise AssertionError("fixture seed cannot contain links or junctions")
                relative = path.relative_to(root).as_posix()
                if stat.S_ISREG(mode.st_mode):
                    records.append((relative, "file", stat.S_IMODE(mode.st_mode), hashlib.sha256(path.read_bytes()).hexdigest()))
                elif stat.S_ISDIR(mode.st_mode):
                    records.append((relative, "directory", stat.S_IMODE(mode.st_mode)))
                    pending.append(path)
                else:
                    raise AssertionError("fixture seed cannot contain special files")
        records.sort()
        return hashlib.sha256(json.dumps(records, separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def require_pre_start(root: Path) -> None:
        if (root / ".git").is_file() or (root / ".git" / "commondir").exists():
            raise AssertionError("fixture seed cannot point to a shared Git directory")
        if (root / ".git" / "worktrees").exists():
            raise AssertionError("fixture seed cannot contain linked worktrees")
        if (root / ".git" / "config").is_file():
            result = subprocess.run(["git", "--git-dir", str(root / ".git"), "config", "--get", "core.worktree"],
                                    capture_output=True, check=False)
            if result.returncode != 1:
                raise AssertionError("fixture seed cannot override its Git worktree")
        runtime = root / ".agentrof" / "agent-marketplace" / ".runtime"
        if runtime.exists() and any(runtime.rglob("*")):
            raise AssertionError("fixture seed cannot contain machine-local runtime state")
        for object_store in (root / ".git" / "objects", root / "remote.git" / "objects"):
            if (object_store / "info" / "alternates").exists():
                raise AssertionError("fixture seed cannot share another object store")

    def copy(self, builder):
        if self.temporary is None:
            started = time.perf_counter()
            temporary, root, _docs = builder()
            _PHASE_TOTALS["seed_build"] += time.perf_counter() - started
            try:
                self.require_pre_start(root)
                fingerprint = self.snapshot(root)
            except BaseException:
                remove_temporary(temporary)
                raise
            self.temporary, self.root, self.fingerprint = temporary, root, fingerprint
        started = time.perf_counter()
        if self.snapshot(self.root) != self.fingerprint:
            raise AssertionError("immutable fixture seed changed between tests")
        _PHASE_TOTALS["seed_validate"] += time.perf_counter() - started
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        started = time.perf_counter()
        try:
            shutil.copytree(self.root, root, dirs_exist_ok=True, copy_function=shutil.copy2)
            if (root / "remote.git").is_dir():
                subprocess.run(["git", "-C", str(root), "remote", "set-url", "origin", str(root / "remote.git")],
                               check=True, capture_output=True)
            (root / ".git" / "FETCH_HEAD").unlink(missing_ok=True)
            return temporary, root, root / "workspace" / "docs"
        except BaseException:
            remove_temporary(temporary)
            raise
        finally:
            _PHASE_TOTALS["seed_copy"] += time.perf_counter() - started

    def close(self):
        if self.temporary is not None:
            remove_temporary(self.temporary)
            self.temporary, self.root, self.fingerprint = None, None, None


def context_snapshot(modules, methods=()):
    return (dict(os.environ), os.getcwd(), tuple(
        (module, name, value) for module in modules
        for name, value in vars(module).items() if callable(value)
    ) + tuple(methods))


def context_unchanged(snapshot) -> bool:
    environment, cwd, bindings = snapshot
    return (dict(os.environ) == environment and os.getcwd() == cwd
            and all(getattr(owner, name, None) is original for owner, name, original in bindings))
