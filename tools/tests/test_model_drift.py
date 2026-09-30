"""Model drift check: pinned classes against a host's own model catalog.

Every host catalog here is a local fixture in the shape the host publishes;
no test reaches the network or GitHub.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "tests"))

import build_distributions  # noqa: E402
import fixtures  # noqa: E402
import model_drift  # noqa: E402

CLAUDE_LEVELS = ("low", "medium", "high", "xhigh", "max")
TODAY = "2026-10-01"


def codex_catalog(*models: tuple) -> dict:
    """The JSON `codex debug models` prints: (slug, efforts, visibility)."""
    return {"models": [
        {"slug": slug, "display_name": slug, "visibility": visibility, "priority": index,
         "supported_reasoning_levels": [
             {"effort": effort, "description": effort} for effort in efforts]}
        for index, (slug, efforts, visibility) in enumerate(models)
    ]}


def models_api(*models: tuple) -> dict:
    """The JSON of the Models API list: (id, efforts)."""
    return {"data": [
        {"type": "model", "id": model_id, "display_name": model_id,
         "created_at": "2026-09-01T00:00:00Z",
         "capabilities": {"effort": {
             "supported": bool(efforts),
             **{level: {"supported": level in efforts} for level in CLAUDE_LEVELS}}}}
        for model_id, efforts in models
    ], "has_more": False, "first_id": None, "last_id": None}


def reconstruct(diff: str) -> tuple[str, str]:
    """Both sides of a unified diff taken with full context."""
    old, new = [], []
    for line in diff.splitlines(keepends=True)[2:]:
        if line.startswith("@@"):
            continue
        if line[0] in " -":
            old.append(line[1:])
        if line[0] in " +":
            new.append(line[1:])
    return "".join(old), "".join(new)


class ModelDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "marketplace"
        fixtures.make_valid_root(self.root, build=False)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def catalog(self, host: str) -> dict:
        return json.loads(build_distributions.model_catalog_path(self.root, host).read_text(
            encoding="utf-8"))

    def pin(self, host: str, name: str, **fields) -> None:
        catalog = self.catalog(host)
        catalog["classes"][name].update(fields)
        build_distributions.model_catalog_path(self.root, host).write_text(
            json.dumps(catalog, indent=2) + "\n", encoding="utf-8")

    def write(self, name: str, value) -> Path:
        path = self.base / name
        path.write_text(value if isinstance(value, str) else json.dumps(value),
                        encoding="utf-8")
        return path

    def run_drift(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = model_drift.main(["--root", str(self.root), *args])
        return code, out.getvalue(), err.getvalue()

    def report(self, *args: str) -> tuple[int, dict]:
        code, out, err = self.run_drift(*args)
        self.assertIn(code, (0, 1), err)
        return code, json.loads(out)

    def codex_host(self, strong_levels: list[str] | None = None) -> Path:
        """The 0.159.1 shape: GPT-6.1 Sol newest, a hidden newer Sol, older models."""
        sol = ["low", "medium", "high", "xhigh", "max", "ultra"]
        return self.write("codex-models.json", codex_catalog(
            ("gpt-6.2-sol", sol, "hide"),
            ("gpt-6.1-sol", strong_levels or sol, "list"),
            ("gpt-6-sol", sol, "list"),
            ("gpt-6-luna", ["low", "medium", "high", "xhigh", "max"], "list"),
            ("gpt-5.6-sol", sol, "list"),
            ("gpt-5.5", ["low", "medium", "high", "xhigh"], "list"),
            ("codex-auto-review", ["low", "medium"], "hide"),
        ))

    def claude_host(self) -> Path:
        """A newer Opus, the pinned Sonnet and no Haiku at all."""
        return self.write("claude-models.json", models_api(
            ("claude-opus-5-6", CLAUDE_LEVELS),
            ("claude-opus-5-5", CLAUDE_LEVELS),
            ("claude-sonnet-5-5", CLAUDE_LEVELS),
            ("claude-sonnet-5", CLAUDE_LEVELS),
        ))

    def pin_scenario(self) -> None:
        """Pins that the fixture host catalogs overtake, whatever ships today."""
        self.pin("codex", "strong", id="gpt-6-sol", verified="2026-09-20",
                 efforts=["low", "medium", "high", "xhigh", "max", "ultra"])
        self.pin("codex", "fast", id="gpt-6-luna",
                 efforts=["low", "medium", "high", "xhigh", "max"])
        self.pin("claude", "frontier", id="claude-opus-5-5", efforts=list(CLAUDE_LEVELS))
        self.pin("claude", "strong", id="claude-sonnet-5-5", efforts=list(CLAUDE_LEVELS))
        self.pin("claude", "fast", id="claude-haiku-4-5-20251001", efforts=[])

    def listed_pins(self, host: str) -> Path:
        """A host catalog that lists exactly the pinned models and efforts."""
        classes = self.catalog(host)["classes"].values()
        if host == "codex":
            return self.write("codex-pins.json", codex_catalog(
                *((entry["id"], entry["efforts"], "list") for entry in classes)))
        return self.write("claude-pins.json", models_api(
            *((entry["id"], entry["efforts"]) for entry in classes)))

    def test_a_newer_model_of_a_pinned_family_is_drift(self):
        self.pin_scenario()
        code, report = self.report("--catalog", f"codex={self.codex_host()}")
        self.assertEqual(code, model_drift.EXIT_DRIFT)
        self.assertTrue(report["drift"])
        self.assertEqual(report["unchecked"], ["claude"])
        codex = report["hosts"]["codex"]
        self.assertEqual(codex["catalog"]["format"], "codex_models")
        # Hidden entries and slugs without a family are not listed models.
        self.assertEqual(codex["catalog"]["models"], 4)
        strong = codex["classes"]["strong"]
        self.assertEqual(strong["family"], "sol")
        self.assertTrue(strong["listed"])
        self.assertEqual(strong["newer"], [{
            "id": "gpt-6.1-sol",
            "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"],
        }])
        self.assertEqual(strong["tiers"], ["high", "medium", "lens"])
        self.assertIn("code-reviewer", strong["roles"])
        self.assertIn("backlog-reviewer-lens", strong["roles"])
        self.assertNotIn("product-owner-mechanical", strong["roles"])
        self.assertNotIn("efforts", strong)
        fast = codex["classes"]["fast"]
        self.assertEqual((fast["newer"], fast["listed"], fast["drift"]), ([], True, False))
        self.assertEqual(fast["tiers"], ["low", "mechanical"])

    def test_a_pinned_model_the_host_no_longer_lists_is_drift(self):
        self.pin_scenario()
        code, report = self.report("--catalog", f"claude={self.claude_host()}")
        self.assertEqual(code, model_drift.EXIT_DRIFT)
        claude = report["hosts"]["claude"]
        self.assertEqual(claude["catalog"]["format"], "anthropic_models")
        frontier = claude["classes"]["frontier"]
        self.assertEqual(frontier["newer"], [{"id": "claude-opus-5-6", "efforts": list(CLAUDE_LEVELS)}])
        fast = claude["classes"]["fast"]
        self.assertEqual((fast["listed"], fast["newer"], fast["drift"]), (False, [], True))
        self.assertEqual(fast["roles"], [])
        # An older Sonnet beside the pinned one is no drift.
        self.assertFalse(claude["classes"]["strong"]["drift"])

    def test_changed_effort_support_is_drift(self):
        self.pin("codex", "strong", id="gpt-6.1-sol",
                 efforts=["low", "medium", "high", "xhigh", "max", "ultra"])
        code, report = self.report(
            "--catalog", f"codex={self.codex_host(['low', 'medium', 'high', 'max', 'ultra'])}")
        self.assertEqual(code, model_drift.EXIT_DRIFT)
        strong = report["hosts"]["codex"]["classes"]["strong"]
        self.assertEqual(strong["newer"], [])
        self.assertEqual(strong["efforts"], {
            "pinned": ["low", "medium", "high", "xhigh", "max", "ultra"],
            "listed": ["low", "medium", "high", "max", "ultra"],
        })
        self.assertTrue(strong["drift"])

    def test_host_catalogs_that_list_the_pins_report_no_drift(self):
        pins = {host: self.catalog(host)["classes"] for host in build_distributions.HOSTS}
        codex = self.write("codex.json", codex_catalog(
            *((entry["id"], entry["efforts"], "list") for entry in pins["codex"].values()),
            ("gpt-5.6-sol", ["low"], "list"),
        ))
        ids = [entry["id"] for entry in pins["claude"].values()]
        claude = self.write("overview.md", "\n".join((
            "| Claude API ID | " + " | ".join(f"`{model_id}`" for model_id in ids) + " |",
            "| Amazon Bedrock ID | " + " | ".join(f"`anthropic.{model_id}`" for model_id in ids) + " |",
            "Aliases such as `claude-haiku-4-5` and `claude-haiku-4-5@20251001` point to a"
            " snapshot, and `/model claude-opus-4-8[1m]` selects the 1M window.",
            f"Legacy: `claude-opus-4-20250514`. Start with {ids[0]}.",
        )))
        code, report = self.report("--catalog", f"codex={codex}", "--catalog", f"claude={claude}")
        self.assertEqual(code, model_drift.EXIT_CLEAN)
        self.assertFalse(report["drift"])
        self.assertEqual(report["unchecked"], [])
        self.assertEqual(report["hosts"]["claude"]["catalog"]["format"], "text")
        self.assertEqual(report["hosts"]["claude"]["catalog"]["models"], len(set(ids)) + 2)
        for host, data in report["hosts"].items():
            for name, item in data["classes"].items():
                with self.subTest(host=host, name=name):
                    self.assertEqual((item["listed"], item["newer"], item["drift"]),
                                     (True, [], False))
                    self.assertEqual(item["pinned"], pins[host][name]["id"])
        code, out, err = self.run_drift("--catalog", f"codex={codex}", "--issue-body")
        self.assertEqual((code, out), (model_drift.EXIT_CLEAN, ""))
        self.assertIn("every pinned class matches", err)

    def test_invalid_input_fails_closed(self):
        codex = self.codex_host()
        cases = (
            (["--catalog", f"gemini={codex}"], "unknown host gemini"),
            (["--catalog", "codex"], "HOST=PATH"),
            (["--catalog", f"codex={codex}", "--catalog", f"codex={codex}"], "names codex twice"),
            (["--catalog", f"codex={self.base / 'absent.json'}"], "unreadable host catalog"),
            (["--catalog", f"codex={self.write('list.json', [])}"], "neither a Codex models"),
            (["--catalog", f"codex={self.write('entry.json', {'models': [{'id': 'x'}]})}"],
             "has no slug"),
            (["--catalog", f"claude={codex}"], "lists no claude model ID"),
            (["--catalog", f"codex={self.write('prose.md', 'no model here')}"],
             "lists no codex model ID"),
            (["--catalog", f"codex={codex}", "--date", "2026-13-01"], "month must be"),
        )
        for args, message in cases:
            with self.subTest(args=args):
                code, out, err = self.run_drift(*args)
                self.assertEqual((code, out), (model_drift.EXIT_INVALID, ""))
                self.assertIn(message, err)
        self.pin("codex", "strong", id="gpt-6.1-sol-2026-09-01")
        code, _out, err = self.run_drift("--catalog", f"codex={codex}")
        self.assertEqual(code, model_drift.EXIT_INVALID)
        self.assertIn("is not a pinned model ID", err)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            model_drift.main(["--root", str(self.root)])
        self.assertEqual(raised.exception.code, 2)

    def test_issue_body_renders_the_bump_diff_and_the_frozen_task_ab(self):
        self.pin_scenario()
        codex = self.codex_host(["low", "medium", "high", "max", "ultra"])
        args = ("--catalog", f"codex={codex}", "--catalog", f"claude={self.claude_host()}")
        code, out, err = self.run_drift(*args, "--issue-body", "--date", TODAY)
        self.assertEqual(code, model_drift.EXIT_DRIFT, err)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("# Model catalog drift: "))
        for finding in ("claude frontier claude-opus-5-5 to claude-opus-5-6",
                        "claude fast claude-haiku-4-5-20251001 not listed",
                        "codex strong gpt-6-sol to gpt-6.1-sol"):
            self.assertIn(finding, lines[0])
        self.assertIn("no newer `haiku` model is listed. The owner decides", out)
        self.assertIn('-      "id": "gpt-6-sol",', lines)
        self.assertIn('+      "id": "gpt-6.1-sol",', lines)
        self.assertIn(f'+      "verified": "{TODAY}"', lines)
        self.assertIn('+      "id": "claude-opus-5-6",', lines)
        # The candidate lists no xhigh, so the high tier must change with it.
        self.assertIn("- Tier `high` runs effort `xhigh`, which `gpt-6.1-sol` does not list",
                      out)
        self.assertIn(f"`{model_drift.AB_REFERENCE}`", out)
        self.assertIn("## Measurement", (ROOT / model_drift.AB_REFERENCE).read_text(
            encoding="utf-8"))
        self.assertIn("`CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` for the session", out)
        self.assertIn("--execution-profile inherit`, then start the session", out)
        for row in ("| codex | high | | `gpt-6-sol` | | | |",
                    "| codex | high | | `gpt-6.1-sol` | | | |",
                    "| codex | lens | | `gpt-6.1-sol` | | | |",
                    "| claude | high | | `claude-opus-5-6` | | | |"):
            self.assertIn(row, lines)
        self.assertNotIn("| claude | low |", out)
        self.assertIn("Quality decides", out)
        self.assertIn("Flow B of `docs/maintainer-operations-protocol.md`", out)
        self.assertIn("at `minor` for `software-engineering-team`", out)
        # A new model can need a newer host CLI than the class records.
        self.assertIn("its `min_cli_version`", out)
        self.assertIn("`tools/data/host-cli-versions.json`", out)
        self.assertNotIn(str(self.base), out)

        report = model_drift.drift_report(self.root, {"codex": codex})
        classes = report["hosts"]["codex"]["classes"]
        path = build_distributions.model_catalog_path(self.root, "codex")
        old, new = reconstruct(model_drift.catalog_diff(
            self.root, "codex", classes, TODAY, context=10 ** 6))
        self.assertEqual(old, path.read_text(encoding="utf-8"))
        expected = self.catalog("codex")
        expected["classes"]["strong"].update(
            id="gpt-6.1-sol", efforts=["low", "medium", "high", "max", "ultra"], verified=TODAY)
        self.assertEqual(json.loads(new), expected)

    def test_a_missing_model_without_successor_proposes_no_bump(self):
        self.pin("claude", "fast", id="claude-haiku-4-5-20251001", efforts=[])
        host = self.write("claude.json", models_api(
            *((entry["id"], entry["efforts"])
              for name, entry in self.catalog("claude")["classes"].items() if name != "fast")))
        code, out, _err = self.run_drift("--catalog", f"claude={host}", "--issue-body",
                                         "--date", TODAY)
        self.assertEqual(code, model_drift.EXIT_DRIFT)
        self.assertNotIn("```diff", out)
        self.assertIn("the owner's decision above comes before any pull request", out)
        self.assertIn("No class moves to another model, so no A/B is due.", out)

    def test_the_command_line_entry_runs(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "model_drift.py"), "--root", str(self.root),
             "--catalog", f"codex={self.listed_pins('codex')}",
             "--catalog", f"claude={self.listed_pins('claude')}"],
            capture_output=True, text=True, check=False, timeout=60,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
        self.assertEqual(result.returncode, model_drift.EXIT_CLEAN, result.stderr)
        self.assertFalse(json.loads(result.stdout)["drift"])

    def test_the_committed_catalogs_are_canonical_json(self):
        # The bump diff re-serializes a catalog, so it applies only to this form.
        for host in build_distributions.HOSTS:
            with self.subTest(host=host):
                text = build_distributions.model_catalog_path(ROOT, host).read_text(
                    encoding="utf-8")
                self.assertEqual(text, model_drift.catalog_text(json.loads(text)))


if __name__ == "__main__":
    unittest.main()
