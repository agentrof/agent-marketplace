"""Run Python entry-point rules in process; real process smokes stay in CI."""

from __future__ import annotations

import io
import os
from pathlib import Path
import runpy
import subprocess
import sys
import sysconfig
import tempfile


def run(argv, *, cwd=None, env=None, input="", version=None):
    """Execute the actual entry point with its argv, streams and import roots.

    This has no process, timeout or signal emulation. Callers exercising those
    boundaries must use subprocess and remain integration tests.
    """
    args = [os.fspath(value) for value in argv]
    script = Path(args[0]).absolute()
    import_root = script.resolve().parent
    isolation_root = import_root.parent if import_root.name == "scripts" else import_root
    saved_argv, saved_path = sys.argv, sys.path
    saved_streams = sys.stdin, sys.stdout, sys.stderr
    saved_version, saved_temp = sys.version_info, tempfile.tempdir
    saved_cwd, saved_env = Path.cwd(), dict(os.environ)
    saved_modules = dict(sys.modules)
    environment = dict(saved_env if env is None else env)
    # A new interpreter cannot reuse a package module imported by another case.
    # Stdlib/runner modules stay shared so the process-wide unit guard stays live.
    library_roots = {Path(sysconfig.get_path(key)).resolve()
                     for key in ("stdlib", "platstdlib", "purelib", "platlib")}

    def library_path(path):
        resolved = Path(path).resolve()
        return any(resolved.is_relative_to(root) for root in library_roots)

    def local_module(module):
        filename = getattr(module, "__file__", None)
        return filename and not library_path(filename) and (
            Path(filename).resolve().is_relative_to(isolation_root)
            or any(part in {"plugins", "platforms", "dist"} for part in Path(filename).parts))

    streams = [io.TextIOWrapper(io.BytesIO(value), encoding="utf-8")
               for value in (input.encode("utf-8"), b"", b"")]
    code = 0
    try:
        for name, module in saved_modules.items():
            if local_module(module):
                sys.modules.pop(name, None)
        os.environ.clear()
        os.environ.update(environment)
        tempfile.tempdir = None
        if cwd is not None:
            os.chdir(cwd)
        sys.argv = args
        sys.path = [str(script.parent if os.name == "nt" else import_root),
                    *[os.path.abspath(path) for path in environment.get("PYTHONPATH", "").split(os.pathsep)
                      if path],
                    *[path for path in saved_path if path and library_path(path)]]
        sys.stdin, sys.stdout, sys.stderr = streams
        if version is not None:
            sys.version_info = tuple(map(int, version.split("."))) + ("final", 0)
        try:
            runpy.run_path(str(script), run_name="__main__")
        except SystemExit as error:
            if error.code is None:
                code = 0
            elif isinstance(error.code, int):
                code = error.code
            else:
                code = 1
                print(error.code, file=sys.stderr)
        for stream in streams:
            stream.flush()
        # Match subprocess.run(text=True), including native Windows output.
        stdout, stderr = (stream.buffer.getvalue().decode("utf-8")
                          .replace("\r\n", "\n").replace("\r", "\n") for stream in streams[1:])
        return subprocess.CompletedProcess(args, code, stdout, stderr)
    finally:
        sys.argv, sys.path = saved_argv, saved_path
        sys.stdin, sys.stdout, sys.stderr = saved_streams
        sys.version_info, tempfile.tempdir = saved_version, saved_temp
        os.chdir(saved_cwd)
        os.environ.clear()
        os.environ.update(saved_env)
        for name, module in list(sys.modules.items()):
            if name not in saved_modules and local_module(module):
                del sys.modules[name]
        for name, module in saved_modules.items():
            if local_module(module):
                sys.modules[name] = module
        for stream in streams:
            stream.close()
