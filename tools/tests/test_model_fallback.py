"""Preserve requested model pins when a host cannot verify or serve them.

Setup on both hosts: the project generators judge each pinned model against
the host's own model list, read by `host_models.py` from fake `claude` and
`codex` executables that the environment pins, so no real host binary or
models cache is reached. No test starts a model.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration
from datetime import datetime, timedelta, timezone
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))
sys.path.insert(0, str(TESTS_DIR.parents[1] / "plugins" / "software-engineering-team" / "scripts"))

import build_distributions  # noqa: E402
import fixtures  # noqa: E402
import git_fixture  # noqa: E402
import model_class  # noqa: E402

RENDERED = f"{fixtures.PLUGIN}-"


def role_settings(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("model")]


def codex_catalog(*slugs: str, efforts: tuple = ("low", "medium", "high", "xhigh")) -> list:
    """Catalog entries in the shape `codex debug models` prints and Codex caches."""
    return [{"slug": slug, "display_name": slug, "visibility": "list", "priority": index,
             "supported_reasoning_levels": [{"effort": effort, "description": effort}
                                            for effort in efforts]}
            for index, slug in enumerate(slugs)]


# A version no Codex release has, so the model check takes only the fake.
FAKE_CODEX_VERSION = "0.159.2-test"


class AttemptResolutionTests(unittest.TestCase):
    def test_explicit_attempt_resolution_does_not_rewrite_default_pins(self):
        root = TESTS_DIR.parents[1]
        mapping = build_distributions.class_map(
            root, root / "plugins" / fixtures.PLUGIN, build_distributions.load_adapters(root))
        original = copy.deepcopy(mapping)
        config = {}
        for host in mapping["hosts"]:
            before = model_class.resolve(mapping, config, host, "critical", access="read")
            # The caller supplies a separately authorized attempt override.
            attempt = {"class_models": {host: {"critical": {"model": "session"}}}}
            alternative = model_class.resolve(mapping, attempt, host, "critical", access="read")
            self.assertEqual(alternative["requested_model"], "session")
            self.assertEqual(alternative["requested_effort"], before["requested_effort"])
            self.assertEqual((alternative["profile"], alternative["access"], alternative["agent"]),
                             ("critical", "read", "critical-reviewer"))
            self.assertEqual(alternative["spawn"], {})
            self.assertIsNone(alternative["observed_model"])
            self.assertEqual(model_class.resolve(mapping, config, host, "critical", access="read"),
                             before)
        self.assertEqual(mapping, original)
        self.assertEqual(config, {})


@integration
class CodexModelCheckTests(unittest.TestCase):
    """Apply judges each model a role is pinned to against Codex's own model list."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "marketplace"
        fixtures.make_valid_root(cls.root, build=False)
        # Two pinned models whatever the package defaults: the review
        # profile runs another model of the catalog.
        table_path = build_distributions.execution_profile_path(cls.root, "codex")
        table = json.loads(table_path.read_text(encoding="utf-8"))
        catalog = json.loads(build_distributions.model_catalog_path(cls.root, "codex").read_text(
            encoding="utf-8"))["models"]
        cls.pinned = table["profiles"]["auto"]["critical"]["model"]
        cls.second = sorted(model for model in catalog if model != cls.pinned)[0]
        efforts = catalog[cls.second]["efforts"]
        table["profiles"]["auto"]["review"] = {"model": cls.second,
                                            "effort": "high" if "high" in efforts else efforts[0]}
        table_path.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
        build_distributions.replace_generated(cls.root, cls.root / "dist")
        package = cls.root / "dist/codex" / fixtures.PLUGIN
        cls.generator = package / "scripts/generate_codex_project.py"
        # Every generated role: its pinned model and its class's effort.
        cls.pins = {}
        for path in sorted((package / "agents").glob("*.md")):
            fields = build_distributions.parse_frontmatter(path)[0]
            cls.pins[path.stem] = (fields["model"], fields["model_reasoning_effort"])
        cls.on_pinned = sorted(role for role, (model, _effort) in cls.pins.items()
                            if model == cls.pinned)
        cls.on_second = sorted(role for role, (model, _effort) in cls.pins.items()
                             if model == cls.second)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def setUp(self) -> None:
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        base = Path(self.work.name)
        self.project = base / "project"
        git_fixture.init_repository(self.project, initial_branch="main")
        self.home = base / "codex-home"
        self.home.mkdir()
        self.isolation = base / "isolation"
        self.codex = fixtures.FakeHost(base / "bin", "codex")
        self.codex.answer("--version", stdout=f"codex-cli {FAKE_CODEX_VERSION}\n")
        self.listing(self.pinned, self.second, "gpt-5.5")

    def listing(self, *slugs: str, bundled: bool = False) -> None:
        """What `codex debug models` prints, and the bundled catalog it equals or not."""
        printed = json.dumps({"models": codex_catalog(*slugs)}) + "\n"
        self.codex.answer("debug models", stdout=printed)
        self.codex.answer("debug models --bundled", stdout=printed if bundled else json.dumps(
            {"models": codex_catalog(self.pinned, self.second, "gpt-6-astra")}) + "\n")

    def cache(self, *slugs: str, efforts: tuple = ("low", "medium", "high", "xhigh")) -> None:
        """The account catalog Codex caches, fetched an hour ago by the fake's version."""
        fetched = datetime.now(timezone.utc) - timedelta(hours=1)
        (self.home / "models_cache.json").write_text(json.dumps({
            "fetched_at": fetched.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "client_version": FAKE_CODEX_VERSION,
            "models": codex_catalog(*slugs, efforts=efforts)}), encoding="utf-8")

    def generated(self, *args: str) -> tuple[dict, str]:
        env = fixtures.isolated_hosts(os.environ, self.isolation)
        # The fake catalog declares its own account context.
        env = {key: value for key, value in env.items() if not key.startswith("CODEX_SANDBOX")}
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "CODEX_HOME": str(self.home),
                    "PATH": str(self.codex.path.parent), "CODEX_VERSION": FAKE_CODEX_VERSION})
        result = subprocess.run(
            [sys.executable, str(self.generator), *args, "--project-root", str(self.project)],
            capture_output=True, text=True, check=False, timeout=120, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout), " ".join(result.stderr.split())

    def commands(self) -> list:
        return [call["argv"] for call in self.codex.calls()]

    def role_files(self) -> dict[str, str]:
        return {path.stem: path.read_text(encoding="utf-8")
                for path in sorted((self.project / ".codex/agents").glob("*.toml"))}

    def assert_pinned(self, files: dict[str, str], roles, *, unavailable=frozenset()) -> None:
        for role in roles:
            model, effort = self.pins[role]
            with self.subTest(role=role):
                self.assertEqual(role_settings(files[role]), [
                    f'model = "{model}"', f'model_reasoning_effort = "{effort}"'])
                if model in unavailable:
                    self.assertIn(f"# Model unavailable: {model}", files[role].splitlines())
                else:
                    self.assertNotIn("# Model unavailable:", files[role])

    def assert_no_config_rewrite(self, before: bytes | None) -> None:
        path = self.project / "workspace/config.json"
        self.assertEqual(path.read_bytes() if path.exists() else None, before)

    def test_apply_keeps_unavailable_critical_pins_and_reports_access_gap(self):
        self.assertTrue(self.on_pinned and self.on_second)
        config = self.project / "workspace/config.json"
        before = config.read_bytes() if config.exists() else None
        self.cache(self.second, "gpt-5.5")
        result, warning = self.generated("apply", "--scope", "local")
        self.assertEqual(result["execution_profile"], "auto")
        check = result["model_check"]
        self.assertEqual({key: check[key] for key in ("status", "binary", "version", "view")},
                         {"status": "ok", "binary": str(self.codex.path),
                          "version": FAKE_CODEX_VERSION, "view": "account"})
        self.assertIn("models_cache.json", check["detail"])
        self.assertEqual(check["verdicts"], {self.pinned: "unavailable", self.second: "available"})
        self.assertEqual(result["model_fallbacks"], {})
        self.assertEqual(result["unavailable_models"], {self.pinned: self.on_pinned})
        files = self.role_files()
        self.assertEqual(set(files), set(self.pins))
        self.assert_pinned(files, self.pins, unavailable={self.pinned})
        self.assertIn(f"codex-project: warning: {self.pinned} is unavailable", warning)
        self.assertEqual({role: result["roles"][role]["model_source"] for role in self.on_pinned},
                         {role: "package" for role in self.on_pinned})
        self.assert_no_config_rewrite(before)
        self.assertEqual(self.commands(), [["--version"]])
        for action in ("check", "inspect"):
            report, _ = self.generated(action, "--scope", "local")
            self.assertEqual(report["changes"], [])
            self.assertEqual(report["model_fallbacks"], {})
            self.assertEqual(report["unavailable_models"], {self.pinned: self.on_pinned})
            self.assertEqual(report["model_check"], {"status": "not_run"})
        self.assertEqual(self.commands(), [["--version"]])
        self.assertEqual(self.role_files(), files)
        self.assert_no_config_rewrite(before)

    def test_refresh_updates_verdicts_without_substituting_models(self):
        self.cache(self.second)
        self.generated("apply", "--scope", "local")
        self.assert_pinned(self.role_files(), self.pins, unavailable={self.pinned})
        self.cache(self.pinned, self.second)
        available, _ = self.generated("apply", "--scope", "all", "--seed-user-files")
        self.assertEqual(available["model_check"]["verdicts"][self.pinned], "available")
        self.assertEqual(available["model_fallbacks"], {})
        self.assertEqual(available["unavailable_models"], {})
        self.assert_pinned(self.role_files(), self.pins)
        self.cache(self.second)
        self.generated("apply", "--scope", "local")
        (self.home / "models_cache.json").unlink()
        self.codex.answer("debug models", exit=3, stdout="access denied")
        unverified, note = self.generated("apply", "--scope", "local")
        self.assertEqual(unverified["model_check"]["status"], "failed")
        self.assertEqual(unverified["model_check"]["verdicts"],
                         {self.pinned: "unverified", self.second: "unverified"})
        self.assertEqual(unverified["model_fallbacks"], {})
        self.assertEqual(unverified["unavailable_models"], {})
        self.assert_pinned(self.role_files(), self.pins)
        self.assertIn("keep their pins unverified", note)

    def test_a_list_that_reflects_no_account_keeps_every_listed_pin_with_a_note(self):
        self.listing(self.pinned, self.second, bundled=True)
        result, note = self.generated("apply", "--scope", "local")
        self.assertEqual((result["model_check"]["status"], result["model_check"]["view"]),
                         ("ok", "generic"))
        self.assertEqual(result["model_check"]["verdicts"],
                         {self.pinned: "unverified", self.second: "unverified"})
        self.assertEqual(result["model_fallbacks"], {})
        self.assert_pinned(self.role_files(), self.pins)
        self.assertIn("keep their pins unverified", note)
        # A catalog listing only: no command starts a model.
        self.assertEqual(self.commands(), [["--version"], ["debug", "models"],
                                           ["debug", "models", "--bundled"]])

    def test_retired_inherit_profile_cannot_silently_downgrade_existing_pins(self):
        self.cache(self.pinned, self.second)
        self.generated("apply", "--scope", "local")
        before = self.role_files()
        calls = self.commands()
        refused = subprocess.run(
            [sys.executable, str(self.generator), "apply", "--scope", "local",
             "--execution-profile", "inherit", "--project-root", str(self.project)],
            capture_output=True, text=True, check=False, timeout=120,
            env=fixtures.isolated_hosts(os.environ, self.isolation))
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("inherit", refused.stderr)
        self.assertEqual(self.role_files(), before)
        self.assertEqual(self.commands(), calls)


CLAUDE_EFFORTS = ["low", "medium", "high", "xhigh", "max"]


def claude_rows(*models: str, efforts: list = CLAUDE_EFFORTS) -> list:
    """`initialize` model rows in Claude Code 2.1.284's shape."""
    return [{"value": model, "resolvedModel": model, "displayName": model,
             "supportsEffort": True, "supportedEffortLevels": list(efforts)} for model in models]


@integration
class ClaudeModelCheckTests(unittest.TestCase):
    """Apply judges each model a rendered role runs against Claude Code's own list."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name) / "marketplace"
        fixtures.make_valid_root(cls.root, build=False)
        # Exercise the light model from both a writer and an independent reviewer.
        table_path = build_distributions.execution_profile_path(cls.root, "claude")
        table = json.loads(table_path.read_text(encoding="utf-8"))
        table["profiles"]["auto"]["review"] = table["profiles"]["auto"]["light"]
        table_path.write_text(json.dumps(table, indent=2) + "\n", encoding="utf-8")
        build_distributions.replace_generated(cls.root, cls.root / "dist")
        cls.package = cls.root / "dist/claude" / fixtures.PLUGIN
        cls.generator = cls.package / "scripts/generate_claude_project.py"
        class_map = json.loads((cls.package / "templates/model-classes.json").read_text(
            encoding="utf-8"))
        cls.map, cls.roles = class_map["hosts"]["claude"], class_map["roles"]
        # Three models run the package classes: one decides, one writes, one is mechanical.
        cls.opus, cls.sonnet, cls.haiku = (cls.map["classes"][slot]["model"] for slot in (
            "critical", "work", "light"))
        assert len({cls.opus, cls.sonnet, cls.haiku}) == 3

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def setUp(self) -> None:
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        base = Path(self.work.name)
        self.project = base / "project"
        git_fixture.init_repository(self.project, initial_branch="main")
        self.config({})
        self.isolation = base / "isolation"
        self.claude = fixtures.FakeHost(base / "session", "claude")
        self.claude.answer("--version", stdout="2.1.284 (Claude Code)\n")
        self.reply(self.sonnet, self.haiku, "claude-haiku-4-5-20251001")

    def config(self, extra: dict) -> None:
        path = self.project / "workspace/config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema_version": 2, "team_id": fixtures.PLUGIN, "output_language": "English",
            "terminology_language": "English", **extra}), encoding="utf-8")

    def reply(self, *models: str, source: str = "claude.ai", efforts: list = CLAUDE_EFFORTS):
        self.claude.answer("*", reply={"models": claude_rows(*models, efforts=efforts),
                                       "account": {"tokenSource": source}})

    def environment(self, **extra) -> dict:
        env = fixtures.isolated_hosts(os.environ, self.isolation)
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "CLAUDE_CODE_EXECPATH": str(self.claude.path),
                    **extra})
        return {key: value for key, value in env.items() if value is not None}

    def generated(self, *args: str, **extra) -> tuple[dict, str]:
        result = subprocess.run(
            [sys.executable, str(self.generator), *args, "--project-root", str(self.project)],
            capture_output=True, text=True, check=False, timeout=120,
            env=self.environment(**extra))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout), " ".join(result.stderr.split())

    def rendered(self) -> dict[str, list[str]]:
        """Each rendered role's frontmatter lines."""
        found = {}
        for path in sorted((self.project / ".claude/agents").glob(f"{RENDERED}*.md")):
            text = path.read_text(encoding="utf-8")
            found[path.stem[len(RENDERED):]] = text[4:text.index("\n---\n", 4)].splitlines()
        return found

    def roles_on(self, *slots: str) -> list[str]:
        return sorted(role for slot in slots for role in self.roles[slot])

    def roles_running(self, model: str) -> list[str]:
        return self.roles_on(*(slot for slot in self.roles
                               if self.map["classes"][slot]["model"] == model))

    def test_apply_keeps_unavailable_critical_pins_and_reports_access_gap(self):
        config = self.project / "workspace/config.json"
        before = config.read_bytes()
        result, warning = self.generated("apply", "--scope", "local")
        check = result["model_check"]
        self.assertEqual({key: check[key] for key in ("status", "binary", "version", "view")},
                         {"status": "ok", "binary": str(self.claude.path), "version": "2.1.284",
                          "view": "account"})
        self.assertEqual(check["verdicts"], {self.opus: "unavailable", self.sonnet: "available",
                                             self.haiku: "available"})
        on_opus = self.roles_running(self.opus)
        self.assertEqual(result["model_fallbacks"], {})
        self.assertEqual(result["unavailable_models"], {self.opus: on_opus})
        rendered = self.rendered()
        for role in self.roles_on("critical"):
            with self.subTest(role=role):
                slot = "critical"
                lines = rendered[role]
                # The stamp names the settings the config resolves, so the
                # session start check finds the render current.
                self.assertIn(f" settings sha256:{settings_digest(self.opus, slot, self.map)}",
                              lines[1])
                self.assertEqual(lines[2], f"# Model unavailable: {self.opus}")
                self.assertIn(f"model: {self.opus}", lines)
                self.assertNotIn("model: inherit", lines)
                self.assertIn(f"effort: {self.map['classes'][slot]['effort']}", lines)
                self.assertEqual(result["roles"][role]["model_source"], "package")
        for role in self.roles_running(self.sonnet):
            with self.subTest(role=role):
                self.assertIn(f"model: {self.sonnet}", rendered[role])
                self.assertNotIn(f"# Model unavailable: {self.opus}", rendered[role])
        self.assertIn(f"claude-project: warning: {self.opus} is unavailable", warning)
        # The probe sent its one `initialize` request, and no user message.
        probes = [call for call in self.claude.calls() if call["argv"] != ["--version"]]
        self.assertEqual(len(probes), 1)
        self.assertEqual([json.loads(line)["type"] for line in probes[0]["stdin"].splitlines()],
                         ["control_request"])
        checked, _ = self.generated("check", "--scope", "local")
        self.assertEqual((checked["changes"], checked["model_fallbacks"]), ([], {}))
        self.assertEqual(checked["unavailable_models"], {self.opus: on_opus})
        self.assertEqual(checked["model_check"], {"status": "not_run"})
        inspected, _ = self.generated("inspect", "--scope", "local")
        self.assertEqual(inspected["model_fallbacks"], {})
        self.assertEqual(inspected["unavailable_models"], {self.opus: on_opus})
        self.assertEqual(config.read_bytes(), before)
        self.assertEqual(len([call for call in self.claude.calls()
                              if call["argv"] != ["--version"]]), 1)
        guard = subprocess.run(
            [sys.executable, str(self.package / "scripts/hook_launcher.py"), "scripts/team_guard.py", "register"],
            input=json.dumps({"hook_event_name": "SessionStart", "source": "startup",
                              "session_id": "fallback", "cwd": str(self.project)}),
            capture_output=True, text=True, check=False, timeout=60,
            env=self.environment(CLAUDE_PROJECT_DIR=str(self.project)))
        self.assertEqual((guard.returncode, guard.stderr), (0, ""))
        self.assertNotIn("AGENT_MARKETPLACE_RENDERED_AGENTS: stale", guard.stdout)

    def test_refresh_updates_verdicts_without_substituting_models(self):
        self.generated("apply", "--scope", "local")
        self.reply(self.opus, self.sonnet, self.haiku)
        restored, _ = self.generated("apply", "--scope", "local")
        self.assertEqual(restored["model_fallbacks"], {})
        self.assertEqual(restored["unavailable_models"], {})
        self.assertTrue(all(f"model: {self.opus}" in lines
                            for role, lines in self.rendered().items()
                            if role in self.roles_running(self.opus)))
        # A list that reflects no account judges nothing unavailable it holds.
        self.reply(self.sonnet, self.haiku, source="apiKeyHelper")
        generic, note = self.generated("apply", "--scope", "local")
        self.assertEqual((generic["model_check"]["view"], generic["model_check"]["verdicts"]),
                         ("generic", {self.opus: "unavailable", self.sonnet: "unverified",
                                      self.haiku: "unverified"}))
        self.assertIn("keep their pins unverified", note)
        # A failed probe judges nothing, so every pin stays.
        self.claude.answer("*", exit=1)
        failed, note = self.generated("apply", "--scope", "local")
        self.assertEqual(failed["model_check"]["status"], "failed")
        self.assertEqual(failed["model_fallbacks"], {})
        self.assertEqual(failed["unavailable_models"], {})
        for slot, roles in self.roles.items():
            for role in roles:
                self.assertIn(f"model: {self.map['classes'][slot]['model']}", self.rendered()[role])
                self.assertIn(f"effort: {self.map['classes'][slot]['effort']}", self.rendered()[role])
        self.assertIn("keep their pins unverified", note)

    def test_the_cli_floor_is_the_highest_minimum_of_the_models_the_classes_run(self):
        floors = {model: entry["min_cli_version"] for model, entry in self.map["models"].items()}
        floor = max(floors[self.opus], floors[self.sonnet], floors[self.haiku], key=release)
        major, minor, patch = release(floor)
        older = f"{major}.{minor}.{patch - 1}" if patch else f"{major}.{minor - 1}.999"
        # The case needs the decision model to run on a CLI another class's cannot.
        self.assertLess(release(floors[self.opus]), release(older))
        on_path = fixtures.FakeHost(Path(self.work.name) / "path", "claude")
        on_path.answer("--version", stdout=f"{older} (Claude Code)\n")
        on_path.answer("*", reply={"models": claude_rows(self.opus, self.sonnet),
                                   "account": {"tokenSource": "claude.ai"}})
        outside = {"CLAUDE_CODE_EXECPATH": None, "PATH": str(on_path.path.parent)}
        result, note = self.generated("apply", "--scope", "local", **outside)
        self.assertEqual(result["model_check"]["status"], "no_binary")
        self.assertIn(f"`claude` on PATH ({on_path.path}) is {older}, below {floor}", note)
        # With the decision model alone on every class the floor is that model's own minimum.
        self.config({"class_models": {"claude": {
            slot: {"model": self.opus} for slot, setting in self.map["classes"].items()
            if setting["model"] != self.opus}}})
        result, _ = self.generated("apply", "--scope", "local", **outside)
        self.assertEqual((result["model_check"]["status"], result["model_check"]["binary"]),
                         ("ok", str(on_path.path)))

    def test_a_project_model_is_judged_and_its_effort_checked_against_the_list(self):
        fable, ghost = "claude-fable-5-1", "claude-ghost-9-9"
        self.assertNotIn(fable, self.map["models"])
        self.config({"class_models": {"claude": {
            "critical": {"model": fable, "effort": "max"},
            "review": {"model": ghost}}}})
        self.reply(self.opus, self.sonnet, self.haiku, fable, efforts=["low", "medium", "high"])
        result, warning = self.generated("apply", "--scope", "local")
        self.assertEqual(result["model_check"]["verdicts"],
                         {fable: "available", ghost: "unavailable", self.sonnet: "available",
                          self.haiku: "available"})
        self.assertEqual(result["model_fallbacks"], {})
        self.assertEqual(result["unavailable_models"], {ghost: self.roles_on("review")})
        self.assertIn(f"the critical class runs {fable} at effort max", warning)
        for role in self.roles_on("review"):
            self.assertIn(f"model: {ghost}", self.rendered()[role])
            self.assertEqual(result["roles"][role]["model_source"], "config")
        high = self.roles_on("critical")[0]
        self.assertIn(f"model: {fable}", self.rendered()[high])
        self.assertIn("effort: max", self.rendered()[high])


def release(version: str) -> tuple:
    return tuple(int(part) for part in version.split("."))


def settings_digest(model: str, slot: str, host_map: dict) -> str:
    """The settings digest the stamp of a role on ``slot`` carries for ``model``."""
    effort = host_map["classes"][slot].get("effort")
    return hashlib.sha256(json.dumps({"model": model, "effort": effort},
                                     sort_keys=True).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    unittest.main()
