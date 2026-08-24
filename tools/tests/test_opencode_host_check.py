"""Deterministic boundary tests for the OpenCode release gate."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "platforms" / "opencode" / "host_check.py"


class OpenCodeHostCheckTests(unittest.TestCase):
    def test_static_projection_gate_is_green(self):
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--static-only"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["scope"], "static")

    def test_real_host_gate_fails_closed_without_a_real_probe(self):
        result = subprocess.run(
            [sys.executable, "-B", str(SCRIPT)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload["ok"])
        self.assertIn(
            payload["code"],
            {"runtime_unbound", "unsupported_opencode_version", "hook_contract_incompatible"},
        )

    def test_tui_flag_is_forwarded_only_to_an_explicit_real_probe(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('parser.add_argument(\n        "--tui"', text)
        self.assertIn('probe_args.append("--tui")', text)


if __name__ == "__main__":
    unittest.main()
