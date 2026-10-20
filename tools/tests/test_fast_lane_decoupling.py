"""The fast lane, vault tooling, autopilot and setup import no governed compiler.

Each kept module is imported anew while the import system refuses every governed
module, so the governed path can be removed without breaking them.
"""

from __future__ import annotations

import importlib
import importlib.abc
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "plugins" / "software-engineering-team"
SEARCH_PATH = [
    PACKAGE / "scripts",
    PACKAGE / "skill-content" / "autopilot" / "scripts",
    ROOT / "platforms" / "shared" / "software-engineering-team" / "overlay" / "scripts",
    ROOT / "platforms" / "shared" / "_team" / "overlay" / "scripts",
]
GOVERNED = (
    r"^(architecture_compile|ba_compile|backlog_\w+|delivery_(closure|compile|git|governance"
    r"|provider|result|verification)|design_system_compile|experience_compile"
    r"|experience_application_check|landscape_check|operation_compile|requirement_compile"
    r"|requirement_route|stage_package|lane_table|production_reachability"
    r"|verification_reference|artifact_check)$"
)
KEPT = (
    "task_lane", "process_policy", "task_inputs", "model_class", "session_cost",
    "step_timing", "context_pack", "impact_closure", "vault_check", "vault_query",
    "vault_index", "vault_gate", "vault_hook", "autopilot", "setup_project",
    "setup_check", "hook_launcher",
)
# Kept modules that still import a governed one, each with its reason.
KNOWN_EXCEPTIONS: dict[str, str] = {}


class RefuseGoverned(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if re.match(GOVERNED, name):
            raise ImportError("governed module imported: " + name)


class FastLaneDecouplingTests(unittest.TestCase):
    def test_kept_modules_import_without_any_governed_module(self):
        roots = tuple(str(path) for path in SEARCH_PATH)
        saved_modules, saved_path, saved_meta = dict(sys.modules), list(sys.path), list(sys.meta_path)
        # Drop every module loaded from the package, so each one is imported anew here.
        for name, module in saved_modules.items():
            if str(getattr(module, "__file__", "") or "").startswith(roots):
                del sys.modules[name]
        sys.meta_path.insert(0, RefuseGoverned())
        sys.path[:0] = roots
        failed = {}
        try:
            for name in KEPT:
                if name in KNOWN_EXCEPTIONS:
                    continue
                try:
                    importlib.import_module(name)
                except Exception as exc:  # noqa: BLE001 - every failure is reported
                    failed[name] = f"{type(exc).__name__}: {exc}"
        finally:
            sys.modules.clear()
            sys.modules.update(saved_modules)
            sys.path[:], sys.meta_path[:] = saved_path, saved_meta
        self.assertEqual(failed, {})


if __name__ == "__main__":
    unittest.main()
