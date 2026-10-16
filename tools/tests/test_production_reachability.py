"""Test-only callers and absent record writers must be visible before review."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "plugins/software-engineering-team/scripts"))
import production_reachability as wiring


class ProductionReachabilityTests(unittest.TestCase):
    def test_new_behavior_needs_a_production_caller_and_record_writer(self):
        sources = {"app.py": 'from pathlib import Path\ndef compute():\n    return 1\nPath("result.json").read_text()\n',
                   "tests/test_app.py": 'from app import compute\ncompute()\nPath("result.json").write_text("1")\n'}
        report = wiring.inspect_sources({}, sources)
        self.assertEqual({row["kind"] for row in report["findings"]}, {"no_production_caller", "no_record_writer"})
        sources["entry.py"] = 'from app import compute\nfrom pathlib import Path\ncompute()\nPath("result.json").write_text("1")\n'
        self.assertEqual(wiring.inspect_sources({}, sources)["findings"], [])
        self.assertEqual(wiring.inspect_sources(sources, sources)["findings"], [])
        self.assertEqual(wiring.inspect_sources({}, {"app.js": "export function run() {}"})["unassessed"], ["app.js"])

    def test_literal_command_without_dispatch_is_a_review_question(self):
        sources = {"cli.py": 'parser.add_parser("start")\n'}
        self.assertEqual(wiring.inspect_sources({}, sources)["findings"][0]["kind"], "check_command_dispatch")
        sources["cli.py"] += 'if args.command == "start":\n    run()\n'
        self.assertEqual(wiring.inspect_sources({}, sources)["findings"], [])
