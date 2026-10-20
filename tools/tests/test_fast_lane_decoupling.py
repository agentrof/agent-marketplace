"""The fast lane, vault tooling, autopilot and setup import no governed compiler.

Each kept module is imported in a fresh interpreter whose import system refuses
every governed module, so the governed path can be removed without breaking them.
"""

from __future__ import annotations

import json
import subprocess
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
PROBE = """
import importlib, importlib.abc, json, re, sys
governed = re.compile(sys.argv[1])
class Refuse(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        if governed.match(name):
            raise ImportError("governed module imported: " + name)
sys.meta_path.insert(0, Refuse())
sys.path[:0] = json.loads(sys.argv[2])
failed = {}
for name in json.loads(sys.argv[3]):
    try:
        importlib.import_module(name)
    except Exception as exc:
        failed[name] = f"{type(exc).__name__}: {exc}"
print(json.dumps(failed))
"""


class FastLaneDecouplingTests(unittest.TestCase):
    def test_kept_modules_import_without_any_governed_module(self):
        names = [name for name in KEPT if name not in KNOWN_EXCEPTIONS]
        result = subprocess.run(
            [sys.executable, "-I", "-c", PROBE, GOVERNED,
             json.dumps([str(path) for path in SEARCH_PATH]), json.dumps(names)],
            capture_output=True, text=True, check=False, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {})


if __name__ == "__main__":
    unittest.main()
