"""Model drift check: pinned models against a host's own model catalog.

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
# The Codex capture is the catalog bundled with the CLI that printed it.
CODEX_CLI = ("--cli-version", "codex=codex-cli 0.159.1")

# The pins every scenario starts from, as the shipped tables stood when the
# scenarios were written. setUp writes them over the fixture, so a model bump
# in the shipped catalogs and profiles changes no test here.
FIXTURE_TABLES = {
    "platforms/claude/model-catalog.json": """\
{
  "schema_version": 2,
  "models": {
    "claude-opus-5-5": {
      "family": "opus",
      "efforts": [
        "low",
        "medium",
        "high",
        "xhigh",
        "max"
      ],
      "min_cli_version": "2.1.280",
      "sources": [
        "https://platform.claude.com/docs/en/models/opus-5-5/overview",
        "https://code.claude.com/docs/en/model-config#adjust-effort-level"
      ],
      "verified": "2026-10-01"
    },
    "claude-sonnet-5-5": {
      "family": "sonnet",
      "efforts": [
        "low",
        "medium",
        "high",
        "xhigh",
        "max"
      ],
      "min_cli_version": "2.1.284",
      "sources": [
        "https://platform.claude.com/docs/en/models/sonnet-5-5/overview",
        "https://code.claude.com/docs/en/model-config#adjust-effort-level"
      ],
      "verified": "2026-10-01"
    },
    "claude-haiku-4-5-20251001": {
      "family": "haiku",
      "efforts": [],
      "min_cli_version": "2.1.74",
      "sources": [
        "https://platform.claude.com/docs/en/models/haiku-4-5/overview",
        "https://code.claude.com/docs/en/model-config#adjust-effort-level",
        "https://code.claude.com/docs/en/changelog"
      ],
      "verified": "2026-10-01"
    }
  }
}
""",
    "platforms/claude/execution-profiles.json": """\
{
  "schema_version": 3,
  "profiles": {
    "auto": {
      "high": {"model": "claude-opus-5-5", "effort": "xhigh"},
      "medium": {"model": "claude-opus-5-5", "effort": "medium"},
      "low": {"model": "claude-sonnet-5-5", "effort": "high"},
      "inherit": {}
    }
  }
}
""",
    "platforms/codex/model-catalog.json": """\
{
  "schema_version": 2,
  "models": {
    "gpt-6.1-sol": {
      "family": "sol",
      "efforts": [
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra"
      ],
      "min_cli_version": "0.159.1",
      "sources": [
        "https://learn.chatgpt.com/docs/models",
        "https://github.com/openai/codex/blob/rust-v0.159.1/codex-rs/models-manager/models.json",
        "https://github.com/openai/codex/releases/tag/rust-v0.159.1"
      ],
      "verified": "2026-10-01"
    },
    "gpt-6-luna": {
      "family": "luna",
      "efforts": [
        "low",
        "medium",
        "high",
        "xhigh",
        "max"
      ],
      "min_cli_version": "0.157.0",
      "sources": [
        "https://learn.chatgpt.com/docs/models",
        "https://github.com/openai/codex/blob/rust-v0.159.1/codex-rs/models-manager/models.json",
        "https://github.com/openai/codex/releases/tag/rust-v0.157.0"
      ],
      "verified": "2026-10-01"
    }
  }
}
""",
    "platforms/codex/execution-profiles.json": """\
{
  "schema_version": 3,
  "profiles": {
    "auto": {
      "high": {"model": "gpt-6.1-sol", "effort": "xhigh"},
      "medium": {"model": "gpt-6.1-sol", "effort": "xhigh"},
      "low": {"model": "gpt-6.1-sol", "effort": "xhigh"},
      "inherit": {}
    }
  }
}
""",
}


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
        for relative, text in FIXTURE_TABLES.items():
            (self.root / relative).write_text(text, encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def catalog(self, host: str) -> dict:
        return json.loads(build_distributions.model_catalog_path(self.root, host).read_text(
            encoding="utf-8"))

    def pin(self, host: str, model: str, **fields) -> None:
        """Update the catalog entry of ``model``."""
        catalog = self.catalog(host)
        catalog["models"][model].update(fields)
        build_distributions.model_catalog_path(self.root, host).write_text(
            json.dumps(catalog, indent=2) + "\n", encoding="utf-8")

    def repin(self, host: str, model: str, to: str, **fields) -> None:
        """Pin ``to`` in place of ``model``: its catalog entry and every tier on it."""
        catalog = self.catalog(host)
        catalog["models"] = {
            (to if name == model else name): ({**entry, **fields} if name == model else entry)
            for name, entry in catalog["models"].items()}
        build_distributions.model_catalog_path(self.root, host).write_text(
            json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
        path = build_distributions.execution_profile_path(self.root, host)
        path.write_text(path.read_text(encoding="utf-8").replace(
            f'"model": "{model}"', f'"model": "{to}"'), encoding="utf-8")

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
        self.repin("codex", "gpt-6.1-sol", "gpt-6-sol", verified="2026-09-20",
                   efforts=["low", "medium", "high", "xhigh", "max", "ultra"])
        self.pin("codex", "gpt-6-luna", efforts=["low", "medium", "high", "xhigh", "max"])
        self.pin("claude", "claude-opus-5-5", efforts=list(CLAUDE_LEVELS))
        self.pin("claude", "claude-sonnet-5-5", efforts=list(CLAUDE_LEVELS))
        self.pin("claude", "claude-haiku-4-5-20251001", efforts=[])

    def listed_pins(self, host: str) -> Path:
        """A host catalog that lists exactly the pinned models and efforts."""
        models = self.catalog(host)["models"].items()
        if host == "codex":
            return self.write("codex-pins.json", codex_catalog(
                *((model, entry["efforts"], "list") for model, entry in models)))
        return self.write("claude-pins.json", models_api(
            *((model, entry["efforts"]) for model, entry in models)))

    def test_a_newer_model_of_a_pinned_family_is_drift(self):
        self.pin_scenario()
        code, report = self.report("--catalog", f"codex={self.codex_host()}", *CODEX_CLI)
        self.assertEqual(code, model_drift.EXIT_DRIFT)
        self.assertTrue(report["drift"])
        self.assertEqual(report["unchecked"], ["claude"])
        codex = report["hosts"]["codex"]
        self.assertEqual(codex["catalog"]["format"], "codex_models")
        self.assertEqual(codex["catalog"]["cli_version"], "0.159.1")
        # Hidden entries and slugs without a family are not listed models.
        self.assertEqual(codex["catalog"]["models"], 4)
        strong = codex["models"]["gpt-6-sol"]
        self.assertEqual((strong["family"], strong["pinned"]), ("sol", "gpt-6-sol"))
        self.assertTrue(strong["listed"])
        self.assertEqual(strong["newer"], [{
            "id": "gpt-6.1-sol",
            "efforts": ["low", "medium", "high", "xhigh", "max", "ultra"],
        }])
        # Every tier runs Sol on Codex, the variants' low tier included.
        self.assertEqual(strong["tiers"], ["high", "medium", "low"])
        self.assertIn("code-reviewer", strong["roles"])
        self.assertIn("backlog-reviewer-lens", strong["roles"])
        self.assertIn("product-owner-mechanical", strong["roles"])
        self.assertNotIn("efforts", strong)
        fast = codex["models"]["gpt-6-luna"]
        self.assertEqual((fast["newer"], fast["listed"], fast["drift"]), ([], True, False))
        self.assertEqual((fast["tiers"], fast["roles"]), ([], []))

    def test_a_pinned_model_the_host_no_longer_lists_is_drift(self):
        self.pin_scenario()
        code, report = self.report("--catalog", f"claude={self.claude_host()}")
        self.assertEqual(code, model_drift.EXIT_DRIFT)
        claude = report["hosts"]["claude"]
        self.assertEqual(claude["catalog"]["format"], "anthropic_models")
        frontier = claude["models"]["claude-opus-5-5"]
        self.assertEqual(frontier["newer"], [{"id": "claude-opus-5-6", "efforts": list(CLAUDE_LEVELS)}])
        fast = claude["models"]["claude-haiku-4-5-20251001"]
        self.assertEqual((fast["listed"], fast["newer"], fast["drift"]), (False, [], True))
        self.assertEqual(fast["roles"], [])
        # An older Sonnet beside the pinned one is no drift.
        self.assertFalse(claude["models"]["claude-sonnet-5-5"]["drift"])

    def test_changed_effort_support_is_drift(self):
        self.pin("codex", "gpt-6.1-sol", efforts=["low", "medium", "high", "xhigh", "max", "ultra"])
        code, report = self.report(
            "--catalog", f"codex={self.codex_host(['low', 'medium', 'high', 'max', 'ultra'])}",
            *CODEX_CLI)
        self.assertEqual(code, model_drift.EXIT_DRIFT)
        strong = report["hosts"]["codex"]["models"]["gpt-6.1-sol"]
        self.assertEqual(strong["newer"], [])
        self.assertEqual(strong["efforts"], {
            "pinned": ["low", "medium", "high", "xhigh", "max", "ultra"],
            "listed": ["low", "medium", "high", "max", "ultra"],
        })
        self.assertTrue(strong["drift"])

    def test_host_catalogs_that_list_the_pins_report_no_drift(self):
        pins = {host: self.catalog(host)["models"] for host in build_distributions.HOSTS}
        codex = self.write("codex.json", codex_catalog(
            *((model, entry["efforts"], "list") for model, entry in pins["codex"].items()),
            ("gpt-5.6-sol", ["low"], "list"),
        ))
        ids = list(pins["claude"])
        claude = self.write("overview.md", "\n".join((
            "| Claude API ID | " + " | ".join(f"`{model_id}`" for model_id in ids) + " |",
            "| Amazon Bedrock ID | " + " | ".join(f"`anthropic.{model_id}`" for model_id in ids) + " |",
            "Aliases such as `claude-haiku-4-5` and `claude-haiku-4-5@20251001` point to a"
            " snapshot, and `/model claude-opus-4-8[1m]` selects the 1M window.",
            f"Legacy: `claude-opus-4-20250514`. Start with {ids[0]}.",
        )))
        code, report = self.report("--catalog", f"codex={codex}", "--catalog", f"claude={claude}",
                                   *CODEX_CLI)
        self.assertEqual(code, model_drift.EXIT_CLEAN)
        self.assertFalse(report["drift"])
        self.assertEqual(report["unchecked"], [])
        self.assertEqual(report["hosts"]["claude"]["catalog"]["format"], "text")
        self.assertIsNone(report["hosts"]["claude"]["catalog"]["cli_version"])
        self.assertEqual(report["hosts"]["claude"]["catalog"]["models"], len(set(ids)) + 2)
        for host, data in report["hosts"].items():
            self.assertEqual(set(data["models"]), set(pins[host]))
            for model, item in data["models"].items():
                with self.subTest(host=host, model=model):
                    self.assertEqual((item["listed"], item["newer"], item["drift"]),
                                     (True, [], False))
                    self.assertEqual(item["pinned"], model)
        code, out, err = self.run_drift("--catalog", f"codex={codex}",
                                        "--catalog", f"claude={claude}", "--issue-body", *CODEX_CLI)
        self.assertEqual((code, out), (model_drift.EXIT_CLEAN, ""))
        self.assertEqual(err, "model-drift: every pinned model matches its host catalog\n")

    def test_an_omitted_host_fails_the_run_unless_a_subset_is_asked(self):
        # A run without a registered host's catalog never reads as "every pin is current".
        codex = ("--catalog", f"codex={self.listed_pins('codex')}", *CODEX_CLI)
        code, out, err = self.run_drift(*codex)
        self.assertEqual(code, model_drift.EXIT_UNCHECKED)
        self.assertEqual(json.loads(out)["unchecked"], ["claude"])
        self.assertIn("model-drift: not checked: claude; pass its catalog, or --subset", err)
        code, out, err = self.run_drift(*codex, "--issue-body")
        self.assertEqual((code, out), (model_drift.EXIT_UNCHECKED, ""))
        self.assertIn("not checked: claude", err)
        self.assertNotIn("every pinned model matches its host catalog", err)
        code, out, err = self.run_drift(*codex, "--issue-body", "--subset")
        self.assertEqual((code, out), (model_drift.EXIT_CLEAN, ""))
        self.assertIn("model-drift: not checked: claude\n", err)
        self.assertIn("every pinned model of codex matches its host catalog", err)
        code, report = self.report(*codex, "--subset")
        self.assertEqual((code, report["unchecked"]), (model_drift.EXIT_CLEAN, ["claude"]))
        # Drift still wins, and the issue names the host it did not compare.
        self.pin_scenario()
        code, out, err = self.run_drift("--catalog", f"codex={self.codex_host()}", *CODEX_CLI,
                                        "--issue-body")
        self.assertEqual(code, model_drift.EXIT_DRIFT)
        self.assertIn("Not checked in this run: claude.", out)
        self.assertIn("not checked: claude", err)

    def test_invalid_input_fails_closed(self):
        codex = self.codex_host()
        cases = (
            (["--catalog", f"gemini={codex}"], "unknown host gemini"),
            (["--catalog", "codex"], "HOST=PATH"),
            (["--catalog", f"codex={codex}", "--catalog", f"codex={codex}"], "names codex twice"),
            (["--catalog", f"codex={self.base / 'absent.json'}", *CODEX_CLI],
             "unreadable host catalog"),
            (["--catalog", f"codex={self.write('list.json', [])}", *CODEX_CLI],
             "neither a Codex models"),
            (["--catalog", f"codex={self.write('entry.json', {'models': [{'id': 'x'}]})}",
              *CODEX_CLI], "has no slug"),
            (["--catalog", f"claude={codex}"], "lists no claude model ID"),
            (["--catalog", f"codex={self.write('prose.md', 'no model here')}", *CODEX_CLI],
             "lists no codex model ID"),
            (["--catalog", f"codex={codex}", "--date", "2026-13-01", *CODEX_CLI],
             "month must be"),
        )
        for args, message in cases:
            with self.subTest(args=args):
                code, out, err = self.run_drift(*args)
                self.assertEqual((code, out), (model_drift.EXIT_INVALID, ""))
                self.assertIn(message, err)
        self.repin("codex", "gpt-6.1-sol", "gpt-6.1-sol-2026-09-01")
        code, _out, err = self.run_drift("--catalog", f"codex={codex}", *CODEX_CLI)
        self.assertEqual(code, model_drift.EXIT_INVALID)
        self.assertIn("is not a pinned model ID", err)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            model_drift.main(["--root", str(self.root)])
        self.assertEqual(raised.exception.code, 2)

    def test_the_codex_capture_names_its_cli_and_is_no_older_than_the_sources(self):
        # `codex debug models` prints the bundled catalog of whatever CLI runs
        # it, so the report records that CLI and refuses one older than the
        # release whose bundled catalog the pins were verified against.
        codex = ("--catalog", f"codex={self.listed_pins('codex')}")
        for cli, message in (
                ((), "--cli-version codex=<version> is required"),
                (("--cli-version", "codex=codex-cli 0.159.0"),
                 "the codex catalog comes from CLI 0.159.0, older than 0.159.1, the release"
                 " the pinned sources name"),
                (("--cli-version", "codex=0.159"), "exactly one X.Y.Z"),
                (("--cli-version", "codex=0.159.1 (0.160.0)"), "exactly one X.Y.Z"),
                (("--cli-version", "codex=0.159.1", "--cli-version", "codex=0.159.1"),
                 "names codex twice"),
                (("--cli-version", "codex=0.159.1", "--cli-version", "claude=2.1.284"),
                 "names claude, whose catalog capture comes from no CLI")):
            with self.subTest(cli=cli):
                code, out, err = self.run_drift(*codex, *cli)
                self.assertEqual((code, out), (model_drift.EXIT_INVALID, ""))
                self.assertIn(message, err)
        code, report = self.report(*codex, "--cli-version", "codex=codex-cli 0.160.0", "--subset")
        self.assertEqual(report["hosts"]["codex"]["catalog"]["cli_version"], "0.160.0")
        # The floor follows the newest release a pinned source names.
        sources = self.catalog("codex")["models"]["gpt-6-luna"]["sources"]
        self.pin("codex", "gpt-6-luna",
                 sources=[*sources, sources[1].replace("0.159.1", "0.161.0")])
        code, out, err = self.run_drift(*codex, "--cli-version", "codex=0.160.0")
        self.assertEqual(code, model_drift.EXIT_INVALID)
        self.assertIn("older than 0.161.0", err)

    def test_issue_body_renders_the_bump_diff_and_the_frozen_task_ab(self):
        self.pin_scenario()
        codex = self.codex_host(["low", "medium", "high", "max", "ultra"])
        args = ("--catalog", f"codex={codex}", "--catalog", f"claude={self.claude_host()}",
                *CODEX_CLI)
        code, out, err = self.run_drift(*args, "--issue-body", "--date", TODAY)
        self.assertEqual(code, model_drift.EXIT_DRIFT, err)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("# Model catalog drift: "))
        for finding in ("claude claude-opus-5-5 to claude-opus-5-6",
                        "claude claude-haiku-4-5-20251001 not listed",
                        "codex gpt-6-sol to gpt-6.1-sol"):
            self.assertIn(finding, lines[0])
        self.assertIn("no newer `haiku` model is listed. The owner decides", out)
        # The catalog renames the pinned entry and every tier that names it follows.
        self.assertIn('-    "gpt-6-sol": {', lines)
        self.assertIn('+    "gpt-6.1-sol": {', lines)
        self.assertIn(f'+      "verified": "{TODAY}"', lines)
        self.assertIn('+    "claude-opus-5-6": {', lines)
        self.assertIn('--- a/platforms/codex/execution-profiles.json', lines)
        self.assertIn('+      "high": {"model": "gpt-6.1-sol", "effort": "xhigh"},', lines)
        self.assertIn('+      "high": {"model": "claude-opus-5-6", "effort": "xhigh"},', lines)
        # The candidate lists no `xhigh`, so every Codex tier, all at `xhigh`,
        # must change with it.
        for tier in ("high", "medium", "low"):
            self.assertIn(f"- Tier `{tier}` runs effort `xhigh`, which `gpt-6.1-sol` does not"
                          " list", out)
        self.assertNotIn("runs effort `high`", out)
        self.assertIn(f"`{model_drift.AB_REFERENCE}`", out)
        self.assertIn("## Measurement", (ROOT / model_drift.AB_REFERENCE).read_text(
            encoding="utf-8"))
        self.assertIn("`CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` for the session", out)
        self.assertIn("--execution-profile inherit`, then start the session", out)
        for row in ("| codex | high | | `gpt-6-sol` | | | |",
                    "| codex | high | | `gpt-6.1-sol` | | | |",
                    "| codex | low | | `gpt-6.1-sol` | | | |",
                    "| claude | high | | `claude-opus-5-6` | | | |"):
            self.assertIn(row, lines)
        self.assertNotIn("| claude | low |", out)
        self.assertIn("Quality decides", out)
        self.assertIn("Flow B of `docs/maintainer-operations-protocol.md`", out)
        self.assertIn("at `minor` for `software-engineering-team`", out)
        # A new model can need a newer host CLI than the pin records.
        self.assertIn("its `min_cli_version`", out)
        self.assertIn("`tools/data/host-cli-versions.json`", out)
        self.assertNotIn(str(self.base), out)

        report = model_drift.drift_report(self.root, {"codex": codex}, {"codex": "0.159.1"})
        models = report["hosts"]["codex"]["models"]
        path = build_distributions.model_catalog_path(self.root, "codex")
        old, new = reconstruct(model_drift.catalog_diff(
            self.root, "codex", models, TODAY, context=10 ** 6))
        self.assertEqual(old, path.read_text(encoding="utf-8"))
        expected = {"schema_version": self.catalog("codex")["schema_version"], "models": {
            ("gpt-6.1-sol" if model == "gpt-6-sol" else model): (
                {**entry, "efforts": ["low", "medium", "high", "max", "ultra"],
                 "verified": TODAY} if model == "gpt-6-sol" else entry)
            for model, entry in self.catalog("codex")["models"].items()}}
        self.assertEqual(json.loads(new), expected)
        self.assertEqual(list(json.loads(new)["models"]), list(expected["models"]))
        profile = build_distributions.execution_profile_path(self.root, "codex")
        old, new = reconstruct(model_drift.profile_diff(self.root, "codex", models,
                                                        context=10 ** 6))
        self.assertEqual(old, profile.read_text(encoding="utf-8"))
        self.assertEqual(new, old.replace('"model": "gpt-6-sol"', '"model": "gpt-6.1-sol"'))

    def test_a_missing_model_without_successor_proposes_no_bump(self):
        haiku = "claude-haiku-4-5-20251001"
        self.pin("claude", haiku, efforts=[])
        host = self.write("claude.json", models_api(
            *((model, entry["efforts"])
              for model, entry in self.catalog("claude")["models"].items() if model != haiku)))
        code, out, _err = self.run_drift("--catalog", f"claude={host}", "--issue-body",
                                         "--date", TODAY)
        self.assertEqual(code, model_drift.EXIT_DRIFT)
        self.assertNotIn("```diff", out)
        self.assertIn("the owner's decision above comes before any pull request", out)
        self.assertIn("No pin moves to another model, so no A/B is due.", out)

    def test_the_command_line_entry_runs(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "model_drift.py"), "--root", str(self.root),
             "--catalog", f"codex={self.listed_pins('codex')}",
             "--catalog", f"claude={self.listed_pins('claude')}", *CODEX_CLI],
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
