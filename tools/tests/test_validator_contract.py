"""Mutation tests for the repository validator's active architecture rules."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
try:
    from tools.tests.levels import integration
except ModuleNotFoundError:  # run as a script from tools/tests
    from levels import integration
from pathlib import Path
from unittest import mock


TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS.parent))

import fixtures  # noqa: E402
import validate  # noqa: E402
from git_fixture import init_repository  # noqa: E402

# The checks that read tools/data/models.json, directly or through the declared tiers.
MODEL_CONFIG_READERS = ("model_config_shape", "frontmatter_shape", "execution_profiles")

PLUGIN_ROOT = "plugins/software-engineering-team"
MODEL_CONFIG = "tools/data/models.json"
HOST_CLI_PINS = "tools/data/host-cli-versions.json"

# The repository paths a check reads. A mutation test copies only these, runs
# only its check, and first sees the copy clean, so no finding comes from a
# file the copy left out.
PROFILE_READS = ("product.json", "platforms", MODEL_CONFIG)
HOST_CLI_READS = ("product.json", "platforms", HOST_CLI_PINS)
PACKAGE_READS = (PLUGIN_ROOT, MODEL_CONFIG)
INSTRUCTION_READS = (PLUGIN_ROOT, "platforms")

_VALID_ROOT: list = []


def partial_root(root: Path, reads) -> Path:
    """``root`` holding only the ``reads`` paths of the repository."""
    for relative in reads:
        fixtures.copy(relative, root)
    return root


def valid_root(root: Path) -> Path:
    """A copy at ``root`` of the full valid fixture, built once per process,
    for a claim about the whole validator run."""
    if not _VALID_ROOT:
        _VALID_ROOT.append(tempfile.TemporaryDirectory())
        fixtures.make_valid_root(Path(_VALID_ROOT[0].name) / "valid")
    shutil.copytree(Path(_VALID_ROOT[0].name) / "valid", root, symlinks=True)
    return root


def tearDownModule() -> None:
    for temporary in _VALID_ROOT:
        temporary.cleanup()
    _VALID_ROOT.clear()


def catalog_models(host: str) -> dict:
    """The models a host's shipped catalog pins, by model ID."""
    return json.loads((fixtures.REAL_REPOSITORY / "platforms" / host / "model-catalog.json")
                      .read_text(encoding="utf-8"))["models"]


def release(version: str) -> tuple:
    return tuple(int(part) for part in version.split("."))


def just_below(version: str) -> str:
    """The closest X.Y.Z release below ``version``."""
    major, minor, patch = release(version)
    if patch:
        return f"{major}.{minor}.{patch - 1}"
    return f"{major}.{minor - 1}.999" if minor else f"{major - 1}.999.999"


def just_above(version: str) -> str:
    major, minor, patch = release(version)
    return f"{major}.{minor}.{patch + 1}"


class ValidatorContractTests(unittest.TestCase):
    def fixture(self, temporary: str) -> Path:
        root = Path(temporary)
        fixtures.make_valid_root(root)
        return root

    def partial(self, reads) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return partial_root(Path(temporary.name), reads)

    def whole(self) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return valid_root(Path(temporary.name) / "root")

    def assert_silent(self, root: Path, *checks: str) -> None:
        self.assertEqual(fixtures.validator_findings(root, *checks), [])

    def assert_reported(self, root: Path, check: str) -> None:
        self.assertTrue(fixtures.validator_findings(root, check), f"{check} reported nothing")

    @staticmethod
    def write_json(path: Path, mutate) -> None:
        value = json.loads(path.read_text(encoding="utf-8"))
        mutate(value)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    @integration
    def test_valid_single_team_fixture_is_clean_and_deterministic(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.fixture(temporary)
            first = validate.run(root)
            second = validate.run(root)
            self.assertEqual(first, [])
            self.assertEqual(first, second)

    def test_skill_project_scope_is_closed_and_external_is_entry_only(self):
        for old, new in (("project_scope: external", "project_scope: remote"),
                         ("exposure: entry", "exposure: internal")):
            with self.subTest(change=new):
                root = self.partial(PACKAGE_READS)
                self.assert_silent(root, "frontmatter_shape")
                path = root / PLUGIN_ROOT / "skill-content/issue-report/SKILL.md"
                text = path.read_text(encoding="utf-8").replace(old, new)
                path.write_text(text, encoding="utf-8")
                self.assert_reported(root, "frontmatter_shape")

    def test_execution_profile_tables_are_validated(self):
        # build_distributions owns the table and catalog rules, and
        # test_single_team_distribution proves them one by one; here each
        # file's problem reaches a finding against that file.
        claude, codex = (f"platforms/{host}/execution-profiles.json" for host in ("claude", "codex"))
        codex_model = sorted(catalog_models("codex"))[0]
        # (changed file, mutation or None to delete it, check, files the findings name)
        cases = (
            (claude, lambda value: value["profiles"]["auto"]["critical_decision"].update(
                model="claude-ghost-5-5"), "execution_profiles", {claude}),
            (codex, lambda value: value["profiles"].update(fast={}), "execution_profiles", {codex}),
            ("platforms/codex/model-catalog.json", lambda value: value["models"][codex_model].update(
                efforts=["low", "ultra", "turbo"]), "execution_profiles",
             {"platforms/codex/model-catalog.json"}),
            # A declared class no table maps is each table's problem.
            ("tools/data/models.json", lambda value: value["model_classes"].update(
                extreme={"escalates": False}), "execution_profiles", {claude, codex}),
            ("tools/data/models.json", lambda value: value["model_classes"].update(
                mechanical={"escalates": "yes"}), "model_config_shape", {"tools/data/models.json"}),
            (codex, None, "execution_profiles", {codex}),
            ("platforms/claude/model-catalog.json", None, "execution_profiles",
             {"platforms/claude/model-catalog.json"}),
        )
        root = self.partial(PROFILE_READS)
        self.assert_silent(root, "execution_profiles", "model_config_shape")
        for relative, mutate, check, named in cases:
            path = root / relative
            original = path.read_bytes()
            with self.subTest(path=relative, check=check, missing=mutate is None):
                if mutate is None:
                    path.unlink()
                else:
                    value = json.loads(original)
                    mutate(value)
                    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
                try:
                    findings = fixtures.validator_findings(root, check)
                finally:
                    path.write_bytes(original)
                self.assertLessEqual(named, {finding.path for finding in findings})

    def test_a_model_catalog_keys_its_models_by_model_id(self):
        # The owner's decision of 1 Oct 2026 on #349: model IDs are the only
        # names, so a catalog's model keys are the host's IDs, not snake_case
        # field names; every other key stays snake_case.
        model = sorted(catalog_models("codex"))[0]
        root = self.partial(("platforms",))
        self.assertEqual(fixtures.validator_findings(root, "json_hygiene"), [])
        path = root / "platforms/codex/model-catalog.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["models"][model]["Family"] = value["models"][model].pop("family")
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        self.assertIn(("platforms/codex/model-catalog.json", "json_hygiene"),
                      {(finding.path, finding.check)
                       for finding in fixtures.validator_findings(root, "json_hygiene")})

    def test_ci_host_cli_versions_meet_every_catalog_model_minimum(self):
        pins = HOST_CLI_PINS
        pinned = json.loads((fixtures.REAL_REPOSITORY / pins).read_text(encoding="utf-8"))
        keys = {"claude": "claude_code", "codex": "codex"}
        # Each host's floor: the highest minimum of its catalog, and the models that set it.
        floors = {}
        for host in keys:
            minimums = {model: entry["min_cli_version"] for model, entry in catalog_models(host).items()}
            floor = max(minimums.values(), key=release)
            floors[host] = (floor, sorted(model for model, minimum in minimums.items()
                                          if minimum == floor))
        self.assertEqual(fixtures.validator_findings(self.partial(HOST_CLI_READS),
                                                     "host_cli_versions"), [])

        def run(relative: str, mutate) -> set:
            root = self.partial(HOST_CLI_READS)
            path = root / relative
            if mutate is None:
                path.unlink()
            else:
                self.write_json(path, mutate)
            return {(finding.path, finding.message)
                    for finding in fixtures.validator_findings(root, "host_cli_versions")}

        below = {host: just_below(floor) for host, (floor, _models) in floors.items()}
        found = run(pins, lambda value: value.update({keys[host]: version
                                                     for host, version in below.items()}))
        self.assertEqual({message.split(";")[0] for _path, message in found}, {
            f"{keys[host]} {below[host]} is below {floor}, the minimum of {host} model {model!r}"
            for host, (floor, models) in floors.items() for model in models})
        self.assertEqual({path for path, _message in found}, {pins})
        raised = sorted(catalog_models("claude"))[0]
        above = just_above(pinned["claude_code"])
        for relative, mutate, fragment in (
                (pins, None, "host CLI versions are missing or not valid JSON"),
                (pins, lambda value: value.update(codex="0.159"),
                 "'codex' must pin the exact X.Y.Z codex CLI version CI installs"),
                ("platforms/claude/model-catalog.json",
                 lambda value: value["models"][raised].update(min_cli_version=above),
                 f"claude_code {pinned['claude_code']} is below {above}, the minimum of claude"
                 f" model {raised!r}")):
            with self.subTest(fragment=fragment):
                self.assertTrue(any(fragment in message for _path, message in run(relative, mutate)))
        # A pin at a model's exact minimum, or above it, is clean.
        self.assertEqual(run(pins, lambda value: value.update(claude_code=floors["claude"][0])), set())
        self.assertEqual(run(pins, lambda value: value.update(codex=just_above(floors["codex"][0]))),
                         set())

    @integration
    def test_malformed_model_config_is_a_finding_not_a_crash(self):
        missing = "model config is missing or not valid JSON"
        whole = "must hold schema_version 2, model_classes and risk_flags"
        shape = "model_classes must map snake_case class ids to {escalates: bool}"
        flags = b', "risk_flags": []'
        cases = {
            "missing file": (None, missing),
            "invalid JSON": (b'{"schema_version": 2,', missing),
            "not UTF-8": (b'{"schema_version": 2, "model_classes": {"\xff": {}}}', missing),
            "not an object": (b'["mechanical", "orchestration"]', missing),
            "classes a number": (b'{"schema_version": 2, "model_classes": 5' + flags + b'}', shape),
            "classes a boolean": (b'{"schema_version": 2, "model_classes": true' + flags + b'}',
                                  shape),
            "classes a list": (b'{"schema_version": 2, "model_classes": ["mechanical"]' + flags
                               + b'}', shape),
            "escalates a string": (b'{"schema_version": 2, "model_classes": {"mechanical":'
                                   b' {"escalates": "yes"}}' + flags + b'}', shape),
            "classes objects": (b'{"schema_version": 2, "model_classes": {"Mechanical":'
                                b' {"escalates": true}}' + flags + b'}', shape),
            "classes absent": (b'{"schema_version": 2' + flags + b'}', whole),
        }
        # Two whole runs prove a malformed file stops no later check; every case
        # runs the checks that read the config, on the files they read.
        whole_runs = {"missing file", "classes objects"}
        for case, (content, message) in cases.items():
            with self.subTest(case=case):
                if case in whole_runs:
                    root = self.whole()
                    # An unrelated defect proves the run still reaches every later check.
                    (root / PLUGIN_ROOT / "cache.sqlite").write_bytes(b"fixture")
                else:
                    root = self.partial(PACKAGE_READS + PROFILE_READS)
                path = root / MODEL_CONFIG
                if content is None:
                    path.unlink()
                else:
                    path.write_bytes(content)
                if case in whole_runs:
                    findings = validate.run(root)
                    self.assertIn("packaged_state_files",
                                  {finding.check for finding in findings})
                else:
                    findings = fixtures.validator_findings(root, *MODEL_CONFIG_READERS)
                self.assertTrue(any(
                    finding.path == "tools/data/models.json"
                    and finding.check == "model_config_shape"
                    and message in finding.message
                    for finding in findings), findings)
                # The agent classes are not judged against a malformed
                # declaration; model_config_shape alone reports it.
                self.assertEqual([finding for finding in findings
                                  if finding.check == "frontmatter_shape"], [])

    def test_a_host_that_drops_the_shared_user_context_is_a_finding_not_a_crash(self):
        """Each host fragment renders the one shared user context list on one line."""
        # The check compares the sources with the built dist/, so it reads the whole fixture.
        root = self.whole()
        for relative, entry, named in (
                ("platforms/codex/_team/overlay/templates/project-instructions/host.md",
                 "- `{{user_context}}`\n", "{{user_context}}"),
                ("platforms/shared/_team/overlay/templates/project-instructions/"
                 "user-context.json", '"{{user_companion}}",', "{{user_companion}}")):
            with self.subTest(source=relative):
                path = root / relative
                original = path.read_text(encoding="utf-8")
                self.assertIn(entry, original)
                path.write_text(original.replace(entry, ""), encoding="utf-8")
                try:
                    findings = fixtures.validator_findings(root, "project_instruction_contract")
                finally:
                    path.write_text(original, encoding="utf-8")
                self.assertEqual(len(findings), 1)
                self.assertIn(named, findings[0].message)


# ---------------------------------------------------------------------------
# Builder registry: one deliberately broken fixture per validator check.
# Each builder breaks the valid fixture repository so that its check reports
# it; a check without a builder fails the lockstep test. Builders change files
# only through the helpers below, which remember every original so the test
# puts the fixture back after each builder instead of copying it per check.
# ---------------------------------------------------------------------------

AGENT = f"{PLUGIN_ROOT}/agents/code-reviewer.md"
FLOW = f"{PLUGIN_ROOT}/flows/fast-lane.md"
ORIGINALS: dict = {}


def remember(path: Path) -> Path:
    ORIGINALS.setdefault(path, path.read_bytes() if path.is_file() else None)
    return path


def restore() -> None:
    for path, original in ORIGINALS.items():
        if original is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(original)
    ORIGINALS.clear()


def edit(root: Path, relative: str, old: str, new: str) -> None:
    path = remember(root / relative)
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise AssertionError(f"{relative} no longer holds {old!r}; update its builder")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def append(root: Path, relative: str, text: str) -> None:
    path = remember(root / relative)
    path.write_text(path.read_text(encoding="utf-8") + text, encoding="utf-8")


def write(root: Path, relative: str, text: str) -> None:
    remember(root / relative).write_text(text, encoding="utf-8")


def remove(root: Path, relative: str) -> None:
    remember(root / relative).unlink()


def edit_json(root: Path, relative: str, mutate) -> None:
    path = remember(root / relative)
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class TreeGuard:
    """The paths and file contents under a root, compared after each builder.

    A file whose size, modification time and status-change time are unchanged
    since the last comparison is not read again; every other file is compared
    by content. A write always advances the status-change time, which no
    caller can set back.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.signatures = self.scan()
        self.contents = {relative: self.content(relative)
                         for relative, signature in self.signatures.items() if signature}

    def scan(self) -> dict:
        found = {}
        for directory, folders, files in os.walk(self.root):
            base = Path(directory).relative_to(self.root)
            for name in folders:
                found[(base / name).as_posix()] = None
            for name in files:
                status = os.stat(os.path.join(directory, name))
                found[(base / name).as_posix()] = (
                    status.st_size, status.st_mtime_ns, status.st_ctime_ns, status.st_ino)
        return found

    def content(self, relative: str) -> str:
        return hashlib.sha256((self.root / relative).read_bytes()).hexdigest()

    def changed(self) -> list[str]:
        """The paths added, removed or rewritten with other bytes since the start."""
        current = self.scan()
        changed = sorted(set(current) ^ set(self.signatures))
        for relative, signature in current.items():
            if signature and relative in self.signatures and signature != self.signatures[relative]:
                if self.content(relative) != self.contents[relative]:
                    changed.append(relative)
                else:
                    self.signatures[relative] = signature
        return changed


VALIDATOR_BUILDERS = {
    "frontmatter_shape": lambda root: edit(
        root, AGENT, "output_contract: prose", "output_contract: table"),
    "agent_name": lambda root: edit(root, AGENT, "name: code-reviewer", "name: code-auditor"),
    "skill_name": lambda root: edit(
        root, f"{PLUGIN_ROOT}/skill-content/configure/SKILL.md",
        "name: configure", "name: configuration"),
    "trigger_policy": lambda root: edit(
        root, AGENT, "description: ", "description: Use when planning. "),
    "size_caps": lambda root: append(
        root, f"{PLUGIN_ROOT}/constitution.md", "- Filler principle.\n" * 61),
    "section_contract": lambda root: edit(root, AGENT, "## Output Contract", "## Output"),
    "content_bans": lambda root: append(root, FLOW, "\nOne step \u2014 then another.\n"),
    "home_paths": lambda root: edit_json(
        root, ".changes/fixture.json",
        lambda value: value.update(summary="Measured in /Users/fixture/checkout.")),
    "agent_tech_nouns": lambda root: append(root, AGENT, "- Reads pytest reports.\n"),
    "handwritten_counts": lambda root: append(root, FLOW, "\nThe team ships 12 agents.\n"),
    "dead_links": lambda root: append(root, FLOW, "\nSee [the ghost](ghost.md).\n"),
    "reference_triggers": lambda root: append(
        root, f"{PLUGIN_ROOT}/skill-content/container-environment/SKILL.md",
        "- [triage again](references/triage.md): triage.\n"),
    "registration": lambda root: edit_json(
        root, ".claude-plugin/marketplace.json",
        lambda value: value["plugins"][0].update(source="./elsewhere")),
    "distribution_packaging": lambda root: edit_json(
        root, ".agents/plugins/marketplace.json", lambda value: value.update(name="other")),
    "single_team_contract": lambda root: edit_json(
        root, "platforms/claude/software-engineering-team/manifest.json",
        lambda value: value.update(dependencies=["some-team"])),
    "packaged_state_files": lambda root: write(root, f"{PLUGIN_ROOT}/cache.sqlite", "state"),
    "json_hygiene": lambda root: write(
        root, f"{PLUGIN_ROOT}/skill-content/configure/data/probe.json", '{"camelCase": 1}\n'),
    "orchestrator_integrity": lambda root: edit(
        root, FLOW, "{{constitution}}", "the constitution"),
    "choice_gate": lambda root: append(root, FLOW, "\n\n\n\nThis needs an explicit user choice.\n"),
    "stdlib_only": lambda root: write(
        root, f"{PLUGIN_ROOT}/scripts/probe_import.py", "import requests\n"),
    "naive_clock": lambda root: write(
        root, f"{PLUGIN_ROOT}/scripts/probe_clock.py",
        "from datetime import date\n\nTODAY = date.today()\n"),
    "script_references": lambda root: append(root, FLOW, "\nRun scripts/ghost_tool.py first.\n"),
    "template_placeholders": lambda root: append(
        root, f"{PLUGIN_ROOT}/templates/ci-tests.yml", "\n{{ghost_token}}\n"),
    "project_instruction_contract": lambda root: remove(
        root, f"{PLUGIN_ROOT}/templates/memory/profile.md"),
    "spawn_shape_constitution": lambda root: append(
        root, f"{PLUGIN_ROOT}/skill-content/deliver/references/fast-lane.md",
        "\n## Spawn Shape\n\nSpawn the reviewer with the named files.\n"),
    "ba_schema_shape": lambda root: edit_json(
        root, f"{PLUGIN_ROOT}/skill-content/business-analysis/data/space-schema.json",
        lambda value: value.update(schema_version="one")),
    "wikilink_ban": lambda root: append(root, FLOW, "\nSee [[ghost]].\n"),
    "version_sync": lambda root: edit_json(
        root, "versions.json",
        lambda value: value["plugins"].update({fixtures.PLUGIN: "9.9.9"})),
    "model_config_shape": lambda root: edit_json(
        root, "tools/data/models.json", lambda value: value["model_classes"].update(
            mechanical={"escalates": "yes"})),
    "execution_profiles": lambda root: edit_json(
        root, "platforms/codex/execution-profiles.json",
        lambda value: value["profiles"].update(fast={})),
    "host_cli_versions": lambda root: edit_json(
        root, "tools/data/host-cli-versions.json", lambda value: value.update(codex="0.157.0")),
    "effort_policy": lambda root: edit_json(
        root, "platforms/codex/effort-policy.json",
        lambda value: value["refuse"].update(extreme=value["refuse"]["ultra"])),
    "autopilot_policy": lambda root: edit_json(
        root, f"{PLUGIN_ROOT}/skill-content/autopilot/data/autopilot-policy.json",
        lambda value: value["goal_kinds"].append(dict(value["goal_kinds"][0]))),
    "limits_config_shape": lambda root: edit_json(
        root, "tools/data/limits.json",
        lambda value: value["authoring_caps"].update(ghost_cap=1)),
    "product_namespace": lambda root: append(
        root, f"{PLUGIN_ROOT}/scripts/marketplace_paths.py", "# drift\n"),
}


class HomePathCheckTests(unittest.TestCase):
    """Package text never carries an absolute home directory, which names a
    person and often the project a path was copied from (#357). The check
    knows no project name; a fixture that needs a fake home declares it."""

    @staticmethod
    def findings(root: Path) -> list:
        found: list = []
        validate.check_home_paths(validate.build_tree(root), found)
        return sorted((finding.path, finding.line) for finding in found)

    @staticmethod
    def user(text: str):
        match = validate.HOME_PATH_RE.search(text)
        return match and validate.home_path_user(match)

    @integration
    def test_every_file_git_would_commit_is_read_and_an_ignored_one_is_not(self):
        # Generated, memory and top-level files are public once committed.
        read = ("plugins/team/skill-content/topic/SKILL.md", "platforms/claude/adapter.json",
                "docs/notes.md", ".changes/note.json", "tools/data/policy.json",
                "tools/tests/test_sample.py", "tools/release.py", "dist/claude/team/notes.md",
                "memory/me.md", "memory/profile.md", "README.md", "AGENTS.md",
                ".github/workflows/ci.yml")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in read + ("scratch/notes.md",):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Fixture.\nMeasured in /home/fixture/checkout.\n", encoding="utf-8")
            (root / "tools/data/blob.bin").write_bytes(b"\xff\xfe/home/fixture/\x00")
            # An export holds no Git metadata, so every file in it is read.
            self.assertEqual(self.findings(root),
                             sorted((relative, 2) for relative in read + ("scratch/notes.md",)))
            init_repository(root)
            (root / ".gitignore").write_text("scratch/\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "docs/notes.md", "memory/me.md"],
                           check=True)
            # A checkout reads its tracked files and the untracked ones Git
            # would add, never an ignored one.
            self.assertEqual(self.findings(root), sorted((relative, 2) for relative in read))

    def test_posix_windows_and_url_homes_are_found_and_placeholders_are_not(self):
        for text in ("/Users/fixture/app", "cwd=/home/fixture", "`/home/fixture/`",
                     "(/home/fixture)", "C:/Users/fixture/x", "file:///Users/fixture/x",
                     "/mnt/c/Users/fixture/x", "C:\\Users\\fixture\\AppData",
                     "c:\\users\\fixture", "C:\\\\Users\\\\fixture\\\\AppData"):
            with self.subTest(text=text):
                self.assertEqual(self.user(text), "fixture")
        # Built from parts so that this source line holds no home path itself.
        self.assertEqual(self.user("C:\\Users\\" + "Fixture Name\\Documents"), "Fixture Name")
        for text in ("/Users/<name>/", "/home/<user>/", "C:\\Users\\<name>\\",
                     "C:\\Users\\%USERNAME%\\", "/Users/$USER/", "/home/{user}/", "~/project",
                     "$HOME/project", "https://example.com/home/products/",
                     "src/pages/home/components/", "GET /users/42/orders",
                     validate.ABSOLUTE_PATH_RE.pattern, validate.HOME_PATH_RE.pattern):
            with self.subTest(text=text):
                self.assertIsNone(self.user(text))

    @integration
    def test_the_folder_form_a_claude_code_project_encodes_is_a_home_path_too(self):
        for text in ("~/.claude/projects/-Users-fixture-Projects-app/run.jsonl",
                     "/tmp/claude-501/-home-fixture-work-app/notes.md",
                     "-Users-fixture-app", "C--Users-fixture-app"):
            with self.subTest(text=text):
                self.assertEqual(self.user(text), "fixture")
        for text in ("-Users-<name>-app", "page-home-hero", "--home-dir", "list-Users-admin-page",
                     "-home-", "the home-office-chair"):
            with self.subTest(text=text):
                self.assertIsNone(self.user(text))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for relative in ("docs/notes.md", "tools/tests/test_sample.py"):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("Run log: /tmp/claude-501/-Users-fixture-app/run.jsonl\n",
                                encoding="utf-8")
            declared = {"tools/tests/test_sample.py": frozenset({"fixture"})}
            with mock.patch.dict(validate.HOME_PATH_FIXTURES, declared, clear=True):
                self.assertEqual(self.findings(root), [("docs/notes.md", 1)])

    def test_drive_mounts_and_nested_or_bare_encoded_folders_are_homes_too(self):
        # Git Bash and Cygwin print a Windows home under a drive segment. A
        # session started in a scratch folder encodes an encoded folder again
        # after a dash, and one started in the home itself ends at the name.
        for text in ("cd /c/Users/fixture/code/app", "/cygdrive/c/Users/fixture/app",
                     "c:/users/fixture/notes.txt",
                     "~/.claude/projects/-private-tmp-claude-501--Users-fixture-Projects-app/1.jsonl",
                     "~/.claude/projects/-tmp-claude-1000--home-fixture-app-scratchpad/1.jsonl",
                     "~/.claude/projects/-Users-fixture/1.jsonl",
                     "~/.claude/projects/-home-fixture/1.jsonl",
                     "Folder -Users-fixture", "`-Users-fixture`"):
            with self.subTest(text=text):
                self.assertEqual(self.user(text), "fixture")
        for text in ("--home-dir", "run -home-dir now", "--Users-only", "xy--home-dir-z",
                     "src/a/Users/fixture", "list-Users-admin", "x1-home-page"):
            with self.subTest(text=text):
                self.assertIsNone(self.user(text))

    @integration
    def test_a_system_or_service_home_names_no_person_and_passes(self):
        homes = {"runner": "Actions checks out into /home/runner/work/app/app.",
                 "node": "WORKDIR /home/node/app",
                 "linuxbrew": 'eval "$(/home/linuxbrew/.linuxbrew/bin/brew shellenv)"',
                 "vscode": "The dev container caches in /home/vscode/.cache.",
                 "ubuntu": "cd /home/ubuntu/app",
                 "Shared": "Shared fixtures live in /Users/Shared/fixtures.",
                 "Public": "Installers write to C:\\Users\\Public\\Desktop.",
                 "Default": "New profiles copy C:\\Users\\Default\\AppData."}
        self.assertEqual(set(homes), set(validate.SYSTEM_HOMES))
        for name, text in homes.items():
            with self.subTest(home=name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                path = root / "plugins/team/skill-content/ci/SKILL.md"
                path.parent.mkdir(parents=True)
                path.write_text(f"{text}\nMeasured in /home/fixture/checkout.\n", encoding="utf-8")
                self.assertEqual(self.user(text), name)
                self.assertEqual(self.findings(root), [("plugins/team/skill-content/ci/SKILL.md", 2)])

    @integration
    def test_a_declared_fake_home_passes_only_in_its_own_file_and_for_its_user(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ("test_sample.py", "test_other.py"):
                path = root / "tools/tests" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('CWD = "/home/fixture/app"\n', encoding="utf-8")
            declared = {"tools/tests/test_sample.py": frozenset({"fixture"})}
            with mock.patch.dict(validate.HOME_PATH_FIXTURES, declared, clear=True):
                self.assertEqual(self.findings(root), [("tools/tests/test_other.py", 1)])
            declared = {"tools/tests/test_sample.py": frozenset({"someone"})}
            with mock.patch.dict(validate.HOME_PATH_FIXTURES, declared, clear=True):
                self.assertEqual(self.findings(root), [("tools/tests/test_other.py", 1),
                                                       ("tools/tests/test_sample.py", 1)])

    @integration
    def test_the_repository_holds_only_its_declared_fake_homes(self):
        root = TESTS.parents[1]
        self.assertEqual(self.findings(root), [])
        for relative, users in sorted(validate.HOME_PATH_FIXTURES.items()):
            text = (root / relative).read_text(encoding="utf-8")
            used = {validate.home_path_user(match) for match in validate.refused_home_paths(text)}
            with self.subTest(fixture=relative):
                self.assertEqual(used, set(users))


class StdlibOnlyCheckTests(unittest.TestCase):
    """A runtime script imports only the standard library and its package's
    scripts. The check reads the imports Python runs, so prose that starts
    with "from " or "import " is none (#362)."""

    SCRIPT = "plugins/team/scripts/probe.py"

    def findings(self, text: str) -> list:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / self.SCRIPT
            path.parent.mkdir(parents=True)
            path.write_text(text, encoding="utf-8")
            (path.parent / "sibling.py").write_text("VALUE = 1\n", encoding="utf-8")
            found: list = []
            validate.check_stdlib_only(validate.build_tree(root), found)
            return [(finding.path, finding.line, finding.check, finding.message) for finding in found]

    def test_prose_that_starts_with_from_or_import_is_no_import(self):
        self.assertEqual(self.findings(
            '"""Return the skill ids the package installs,\n'
            "from the folders under skill-content/.\n"
            "import them where a gate needs them.\n"
            '"""\n'
            "# from here on, only the standard library\n"
            "#   import nothing else\n"
            "import json\n"
            "from . import sibling\n"
            "\n"
            "\n"
            "def describe():\n"
            '    """List what the package ships,\n'
            "    from each skill folder.\n"
            '    """\n'
            "    return '''\n"
            "import every skill\n"
            "from the package\n"
            "'''\n"), [])

    def test_a_real_import_outside_the_standard_library_is_found_anywhere(self):
        self.assertEqual(self.findings(
            "import json, requests\n"
            "import sibling\n"
            "\n"
            "\n"
            "def load():\n"
            "    from yaml import safe_load\n"
            "    import numpy.linalg\n"
            "    return safe_load, numpy\n"), [
                (self.SCRIPT, 1, "stdlib_only", "non-stdlib import 'requests'"),
                (self.SCRIPT, 6, "stdlib_only", "non-stdlib import 'yaml'"),
                (self.SCRIPT, 7, "stdlib_only", "non-stdlib import 'numpy'")])

    def test_a_script_that_does_not_parse_is_a_finding(self):
        for text, line in (("import json\n\ndef broken(:\n    pass\n", 3), ("VALUE = 1\0\n", 1)):
            with self.subTest(text=text):
                findings = self.findings(text)
                self.assertEqual([finding[:3] for finding in findings], [(self.SCRIPT, line, "stdlib_only")])
                self.assertIn("script does not parse, so its imports cannot be read", findings[0][3])


class ValidatorBuilderTests(unittest.TestCase):
    """Every validator check fires on its broken fixture and stays silent on
    the valid one; CHECKS and VALIDATOR_BUILDERS name the same checks."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = valid_root(Path(cls.temporary.name) / "valid")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_registry_lockstep_with_checks(self):
        self.assertEqual(sorted(VALIDATOR_BUILDERS), sorted(validate.CHECKS))

    def test_contributing_and_the_validator_name_the_registry(self):
        contributing = (TESTS.parents[1] / "CONTRIBUTING.md").read_text(encoding="utf-8")
        for name, text in (("CONTRIBUTING.md", contributing), ("validate.py", validate.__doc__)):
            with self.subTest(document=name):
                text = " ".join(text.split())
                self.assertTrue("VALIDATOR_BUILDERS" in text
                                and "tools/tests/test_validator_contract.py" in text,
                                f"{name} does not name the builder registry")
                self.assertFalse("tools/tests/fixtures/" in text,
                                 f"{name} names a fixtures folder that does not exist")

    @integration
    def test_each_builder_fires_its_check(self):
        pristine = TreeGuard(self.root)
        for check, builder in sorted(VALIDATOR_BUILDERS.items()):
            with self.subTest(check=check):
                silent: list = []
                validate.CHECKS[check](validate.build_tree(self.root), silent)
                self.assertEqual(silent, [])
                found: list = []
                try:
                    builder(self.root)
                    validate.CHECKS[check](validate.build_tree(self.root), found)
                finally:
                    restore()
                self.assertEqual(pristine.changed(), [],
                                 f"the {check} builder changed a file outside the helpers")
                self.assertTrue([finding for finding in found if finding.check == check],
                                f"{check} reported nothing on its broken fixture")


if __name__ == "__main__":
    unittest.main()
