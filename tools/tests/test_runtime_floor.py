"""The plugin's Python runtime floor: below it only the plugin's own work stops.

Every hook command runs hook_launcher.py, which checks runtime_floor.MINIMUM
before it reads the hook script. A Python below the floor is simulated by a
wrapper that sets sys.version_info and then starts the launcher as
`python3 <launcher>` would; no old interpreter runs.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import build_distributions  # noqa: E402

TEAM_SCRIPTS = ROOT / "platforms/shared/_team/overlay/scripts"
LAUNCHER = TEAM_SCRIPTS / "hook_launcher.py"
FLOOR = TEAM_SCRIPTS / "runtime_floor.py"
PLUGIN = "software-engineering-team"
ENTRY = f"{PLUGIN}:autopilot"
ROOT_VARIABLES = {"claude": '"${CLAUDE_PLUGIN_ROOT}"', "codex": '"${PLUGIN_ROOT}"'}
BELOW = "3.9.6"
# Start the launcher as `python3 <launcher> ...` would, on a simulated version.
SIMULATE = (
    "import os, runpy, sys\n"
    "sys.version_info = tuple(int(part) for part in sys.argv[1].split('.')) + ('final', 0)\n"
    "sys.argv = sys.argv[2:]\n"
    "sys.path[0] = os.path.dirname(os.path.realpath(sys.argv[0]))\n"
    "runpy.run_path(sys.argv[0], run_name='__main__')\n"
)
HOST_VARIABLES = ("CLAUDE_PROJECT_DIR", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID")


def load(name: str, path: Path, *directories: Path):
    for directory in directories:
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def package(host: str) -> Path:
    return ROOT / "dist" / host / PLUGIN


def environment() -> dict:
    return {**{key: value for key, value in os.environ.items() if key not in HOST_VARIABLES},
            "PYTHONDONTWRITEBYTECODE": "1"}


def hook_commands(host: str) -> dict[tuple[str, str | None], list[str]]:
    """Each packaged hook's argv after `python3`, the plugin root substituted, by event and matcher."""
    events = json.loads((package(host) / "hooks/hooks.json").read_text(encoding="utf-8"))["hooks"]
    commands = {}
    for event, groups in events.items():
        for group in groups:
            for handler in group["hooks"]:
                text = handler["command"].replace(ROOT_VARIABLES[host], shlex.quote(str(package(host))))
                argv = shlex.split(text)
                assert argv[0] == "python3", argv
                commands[event, group.get("matcher")] = argv[1:]
    return commands


def vault_pre(host: str) -> list[str]:
    """The packaged PreToolUse vault hook command."""
    return next(argv for (event, _matcher), argv in hook_commands(host).items()
                if event == "PreToolUse" and argv[1] == "scripts/vault_hook.py")


def run(argv: list[str], payload, cwd: Path, version: str | None = None) -> subprocess.CompletedProcess:
    """Run a hook argv (launcher first) on this Python, or on a simulated version."""
    prefix = [sys.executable] if version is None else [sys.executable, "-c", SIMULATE, version]
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(prefix + argv, input=text, cwd=cwd, env=environment(),
                          capture_output=True, text=True, check=False, timeout=120)


def plugin_project(root: Path) -> Path:
    (root / "workspace/docs/maps").mkdir(parents=True)
    (root / "workspace/config.json").write_text('{"schema_version": 1, "team_id": "software-engineering-team"}\n',
                                                encoding="utf-8")
    return root


class RuntimeFloorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.floor = load("runtime_floor", FLOOR)
        cls.launcher = load("hook_launcher_under_test", LAUNCHER, TEAM_SCRIPTS)
        cls.vault_hook = load("floor_vault_hook",
                              ROOT / "platforms/shared/software-engineering-team/overlay/scripts/vault_hook.py",
                              ROOT / "plugins" / PLUGIN / "scripts")
        cls.autopilot = load("floor_autopilot",
                             ROOT / "plugins" / PLUGIN / "skill-content/autopilot/scripts/autopilot.py")

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name).resolve()

    def message(self) -> str:
        return (f"This Agent Marketplace plugin needs Python 3.14 or newer, but `python3` is Python"
                f" {BELOW} at {sys.executable}. Install Python 3.14 or newer (macOS:"
                " `brew install python3`) so that `python3` on PATH runs it, then restart the session.")

    def test_the_floor_and_the_launcher_parse_as_python_3_9_and_import_only_the_stdlib(self):
        allowed = {FLOOR: {"sys", "os"}, LAUNCHER: set(sys.stdlib_module_names) | {"runtime_floor"}}
        for path, modules in allowed.items():
            with self.subTest(path=path.name):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name, feature_version=(3, 9))
                imported = {alias.name.split(".")[0] for node in ast.walk(tree)
                            if isinstance(node, ast.Import) for alias in node.names}
                imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                             if isinstance(node, ast.ImportFrom) and node.module}
                self.assertLessEqual(imported, modules)
                for host in ("claude", "codex"):
                    self.assertEqual((package(host) / "scripts" / path.name).read_bytes(), path.read_bytes())

    def test_the_floor_is_the_one_python_version_ci_tests(self):
        policy = json.loads((ROOT / "tools/data/ci-test-policy.json").read_text(encoding="utf-8"))
        self.assertEqual(self.floor.MINIMUM, tuple(int(part) for part in policy["python"].split(".")))

    def test_every_hook_command_and_scaffolded_hook_runs_through_the_launcher(self):
        for host, variable in ROOT_VARIABLES.items():
            adapter = build_distributions.load_adapters(ROOT)[host].module
            sources = [ROOT / "platforms" / host / PLUGIN / "overlay/hooks/hooks.json",
                       package(host) / "hooks/hooks.json"]
            documents = [json.loads(path.read_text(encoding="utf-8")) for path in sources]
            documents.append(adapter.scaffold_overlay_files()["overlay/hooks/hooks.json"])
            for document in documents:
                commands = [handler["command"] for groups in document["hooks"].values()
                            for group in groups for handler in group["hooks"]]
                self.assertTrue(commands)
                for command in commands:
                    with self.subTest(host=host, command=command):
                        launcher = f"python3 {variable}/scripts/hook_launcher.py "
                        self.assertTrue(command.startswith(launcher))
                        target = command[len(launcher):].split(" ", 1)[0]
                        self.assertTrue((package(host) / target).is_file())

    def test_below_the_floor_session_start_reports_the_runtime_and_never_blocks(self):
        for host in ROOT_VARIABLES:
            with self.subTest(host=host):
                argv = hook_commands(host)["SessionStart", None]
                result = run(argv, {"hook_event_name": "SessionStart", "source": "startup",
                                    "session_id": "floor", "cwd": str(self.work)}, self.work, BELOW)
                self.assertEqual((result.returncode, result.stderr), (0, ""))
                output = json.loads(result.stdout)
                self.assertEqual(list(output), ["hookSpecificOutput"])
                self.assertEqual(output["hookSpecificOutput"], {
                    "hookEventName": "SessionStart", "additionalContext": "\n".join((
                        "AGENT_MARKETPLACE_HOOKS_ACTIVE: none",
                        f"AGENT_MARKETPLACE_PYTHON: unsupported (Python {BELOW} at {sys.executable})",
                        self.message()))})

    def test_below_the_floor_every_other_generic_event_passes_silently(self):
        payloads = {"PostToolUse": {"tool_name": "Write", "tool_input": {"file_path": "workspace/docs/a.md"}},
                    "PostToolUseFailure": {"tool_name": "Bash", "tool_input": {"command": "false"}},
                    "PreToolUse": {"tool_name": "AskUserQuestion", "tool_input": {"questions": []}}}
        project = plugin_project(self.work / "project")
        for host in ROOT_VARIABLES:
            for (event, matcher), argv in hook_commands(host).items():
                if event not in payloads or argv == vault_pre(host):
                    continue
                with self.subTest(host=host, event=event, matcher=matcher):
                    payload = dict(payloads[event], hook_event_name=event, cwd=str(project))
                    result = run(argv, payload, project, BELOW)
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_below_the_floor_a_write_to_a_governed_path_is_denied_and_any_other_passes(self):
        project = plugin_project(self.work / "project")
        outside = self.work / "outside"
        (outside / "workspace/docs").mkdir(parents=True)
        cases = {
            "workspace/docs/maps/home.md": 2, "workspace/config.json": 2,
            ".agentrof/agent-marketplace/.runtime/autopilot/grant.json": 2,
            "README.md": 0, "apps/api/main.py": 0, "workspace/notes.md": 0,
            str(outside / "workspace/docs/maps/home.md"): 0, str(outside / "notes.md"): 0,
            "workspace/docs/bad\x00name.md": 0,
        }
        tools = {"claude": ("Write", "Edit", "MultiEdit"), "codex": ("apply_patch",)}
        for host, names in tools.items():
            argv = vault_pre(host)
            for name in names:
                for target, expected in cases.items():
                    path = target if Path(target).is_absolute() else str(project / target)
                    if name == "apply_patch":
                        tool_input = {"command": f"*** Begin Patch\n*** Add File: {target}\n+x\n*** End Patch"}
                    else:
                        tool_input = {"file_path": path, "content": "x"}
                    with self.subTest(host=host, tool=name, target=target):
                        result = run(argv, {"hook_event_name": "PreToolUse", "tool_name": name,
                                            "tool_input": tool_input, "cwd": str(project)}, project, BELOW)
                        self.assertEqual(result.returncode, expected)
                        self.assertEqual(result.stdout, "")
                        self.assertEqual(result.stderr, self.message() + "\n" if expected else "")
        patch = ("*** Begin Patch\n*** Update File: README.md\n*** Move to: workspace/docs/maps/moved.md\n"
                 "@@\n-x\n+y\n*** End Patch")
        result = run(vault_pre("codex"), {"tool_name": "apply_patch", "tool_input": patch, "cwd": str(project)},
                     project, BELOW)
        self.assertEqual(result.returncode, 2)

    def test_below_the_floor_only_a_command_that_runs_a_plugin_script_is_denied(self):
        for host in ROOT_VARIABLES:
            script = package(host) / "scripts" / "setup_project.py"
            commands = {
                f"python3 {shlex.quote(str(script))} inspect --project-root .": 2,
                f'"{sys.executable}" -B "{script}" apply --json': 2,
                f"cd {shlex.quote(str(self.work))} && python3 {script}": 2,
                "brew install python3": 0, "python3 --version": 0, "ls -la": 0,
                f"python3 {self.work / 'scripts' / 'setup_project.py'}": 0,
                f"cat {package(host) / 'README.md'}": 0,
            }
            for tool in ("Bash", "PowerShell"):
                for command, expected in commands.items():
                    with self.subTest(host=host, tool=tool, command=command):
                        result = run(vault_pre(host), {"hook_event_name": "PreToolUse", "tool_name": tool,
                                                       "tool_input": {"command": command}, "cwd": str(self.work)},
                                     self.work, BELOW)
                        self.assertEqual(result.returncode, expected)
                        self.assertEqual(result.stderr, self.message() + "\n" if expected else "")

    @unittest.skipIf(os.name == "nt", "a directory symlink needs privileges on Windows")
    def test_below_the_floor_the_resolved_scripts_directory_is_the_plugin_too(self):
        alias = self.work / "alias"
        alias.symlink_to(package("claude"), target_is_directory=True)
        argv = [str(alias / "scripts/hook_launcher.py"), "scripts/vault_hook.py", "pre"]
        for name in (alias, package("claude").resolve()):
            with self.subTest(root=str(name)):
                command = f"python3 {name / 'scripts' / 'project_config.py'} show"
                result = run(argv, {"tool_name": "Bash", "tool_input": {"command": command}}, self.work, BELOW)
                self.assertEqual(result.returncode, 2)

    def test_below_the_floor_only_the_autopilot_entry_prompt_is_blocked(self):
        block = {"decision": "block", "reason": self.message()}
        prompts = {
            "claude": [({"command_name": ENTRY, "command_args": "on --for 2h", "expansion_type": "slash_command",
                         "prompt": f"/{ENTRY} on --for 2h"}, block),
                       ({"command_name": ENTRY, "command_args": "status", "prompt": f"/{ENTRY} status"}, block),
                       ({"command_name": f"{PLUGIN}:deliver", "command_args": "", "prompt": "/deliver"}, None)],
            "codex": [({"prompt": f"${ENTRY} on --for 2h"}, block), ({"prompt": f"[${ENTRY}](app://x) off"}, block),
                      ({"prompt": "Plan the next Delivery."}, None), ({"prompt": f"Explain ${ENTRY}."}, None),
                      ("not json", None), ("", None)],
        }
        for host, cases in prompts.items():
            argv = next(argv for (event, _matcher), argv in hook_commands(host).items()
                        if event.startswith("UserPrompt"))
            for payload, expected in cases:
                with self.subTest(host=host, payload=payload):
                    if isinstance(payload, dict):
                        payload = dict(payload, session_id="floor", cwd=str(self.work))
                    result = run(argv, payload, self.work, BELOW)
                    self.assertEqual((result.returncode, result.stderr), (0, ""))
                    self.assertEqual(json.loads(result.stdout) if result.stdout else None, expected)

    def test_below_the_floor_no_hook_script_is_read(self):
        root = self.work / "package"
        (root / "scripts").mkdir(parents=True)
        for path in (LAUNCHER, FLOOR):
            shutil.copy2(path, root / "scripts" / path.name)
        broken = "match = 'this file is not Python' )\n"
        for target in ("scripts/team_guard.py", "scripts/vault_hook.py", "scripts/model_fallback.py",
                       "skill-content/autopilot/scripts/autopilot.py"):
            (root / target).parent.mkdir(parents=True, exist_ok=True)
            (root / target).write_text(broken, encoding="utf-8")
        launcher = str(root / "scripts/hook_launcher.py")
        cases = [(["scripts/team_guard.py", "register"], {}, 0),
                 (["scripts/model_fallback.py"], {"tool_name": "Agent"}, 0),
                 (["scripts/vault_hook.py", "post"], {"tool_name": "Write"}, 0),
                 (["scripts/vault_hook.py", "pre"], {"tool_name": "Bash", "tool_input": {
                     "command": f"python3 {root / 'scripts' / 'vault_hook.py'}"}}, 2),
                 (["skill-content/autopilot/scripts/autopilot.py", "hook", "user-prompt", "--entry", ENTRY],
                  {"prompt": f"${ENTRY} on"}, 0)]
        for arguments, payload, expected in cases:
            with self.subTest(arguments=arguments):
                result = run([launcher, *arguments], payload, self.work, BELOW)
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertNotIn("SyntaxError", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_the_governed_path_copy_agrees_with_the_vault_hook(self):
        hook = self.vault_hook
        project = plugin_project(self.work / "project")

        def vault_rule(path: str) -> bool:
            return hook.in_autopilot_runtime(path) or hook.is_workspace_config(path, {}) \
                or hook.vault_relative(path) is not None

        governed = ["workspace/docs/maps/home.md", "workspace/docs/design-system/MASTER.md",
                    "workspace/docs/experience-design/experiences/checkout/experience.md",
                    "workspace/docs/backlog/_generated/index.md", "workspace/docs/assets/diagram.png",
                    "workspace/config.json", ".agentrof/agent-marketplace/.runtime/autopilot/grant.json",
                    ".agentrof/agent-marketplace/.runtime/autopilot/arming.json"]
        free = ["README.md", "apps/api/main.py", "workspace/notes.md", "workspace/apps.json",
                ".agentrof/agent-marketplace/.runtime/vault-inventory/state.json", "docs/guide.md",
                ".claude/settings.json"]
        for relative, expected in [(path, True) for path in governed] + [(path, False) for path in free]:
            path = str(project / relative)
            with self.subTest(path=relative):
                self.assertEqual((self.launcher.governed(path), vault_rule(path)), (expected, expected))
        # Outside a plugin project the copy is narrower than the vault hook's
        # lexical rules: there is no plugin work there to stop.
        other = self.work / "other"
        (other / "app/docs").mkdir(parents=True)
        (other / "app/config.json").write_text("{}\n", encoding="utf-8")
        for relative in ("workspace/docs/maps/home.md", "workspace/config.json", "app/docs/guide.md"):
            path = str(other / relative)
            with self.subTest(outside=relative):
                self.assertEqual((self.launcher.governed(path), vault_rule(path)), (False, True))
        runtime = str(other / ".agentrof/agent-marketplace/.runtime/autopilot/grant.json")
        self.assertEqual((self.launcher.governed(runtime), vault_rule(runtime)), (True, True))

    def test_the_prompt_rule_agrees_with_the_autopilot_hook(self):
        payloads = [
            {"command_name": ENTRY, "command_args": "on", "expansion_type": "slash_command"},
            {"command_name": ENTRY, "command_args": "status"},
            {"command_name": ENTRY, "command_args": "on", "expansion_type": "skill"},
            {"command_name": ENTRY, "prompt": f"/{ENTRY} off"},
            {"command_name": f"{PLUGIN}:deliver", "command_args": "on"},
            {"prompt": f"${ENTRY} on --for 2h"}, {"prompt": f"  /{ENTRY}\ton"}, {"prompt": f"/{ENTRY}"},
            {"prompt": f"[${ENTRY}](app://plugin) on"}, {"prompt": f"${ENTRY}x on"},
            {"prompt": f"Plan it.\n${ENTRY} on"}, {"prompt": "Plan the next Delivery."}, {"prompt": 7}, {},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                self.assertEqual(self.launcher.invokes(payload, ENTRY),
                                 self.autopilot.typed_arguments(payload, ENTRY) is not None)
        self.assertFalse(self.launcher.invokes({"prompt": f"${ENTRY} on"}, None))

    def test_at_or_above_the_floor_the_launcher_runs_a_script_as_python_would(self):
        root = self.work / "package"
        (root / "scripts").mkdir(parents=True)
        for path in (LAUNCHER, FLOOR):
            shutil.copy2(path, root / "scripts" / path.name)
        nested = root / "skill-content/probe/scripts"
        nested.mkdir(parents=True)
        (nested / "sibling.py").write_text("VALUE = 'sibling'\n", encoding="utf-8")
        (nested / "probe.py").write_text(
            "import json, sys\nimport sibling\n"
            "def crash():\n    raise ValueError('probe failed: ' + sys.argv[-1])\n"
            "if __name__ == '__main__':\n"
            "    data = sys.stdin.read()\n"
            "    if sys.argv[-1] == 'crash':\n        crash()\n"
            "    print(json.dumps({'argv': sys.argv, 'file': __file__, 'name': __name__, 'path0': sys.path[0],\n"
            "                      'sibling': sibling.VALUE, 'stdin': data, 'main': sys.modules['__main__'].__file__}))\n"
            "    sys.stderr.write('to stderr\\n')\n"
            "    raise SystemExit(3)\n", encoding="utf-8")
        launcher = [str(root / "scripts/hook_launcher.py"), "skill-content/probe/scripts/probe.py"]
        direct = [str(nested / "probe.py")]
        for version in (None, "3.14.0", "3.15.2"):
            for last in ("run", "crash"):
                with self.subTest(version=version, mode=last):
                    through = run(launcher + ["one", last], '{"x": 1}', self.work, version)
                    plain = run(direct + ["one", last], '{"x": 1}', self.work)
                    self.assertEqual((through.returncode, through.stdout, through.stderr),
                                     (plain.returncode, plain.stdout, plain.stderr))
                    self.assertEqual(through.returncode, 3 if last == "run" else 1)
        below = run(launcher + ["one", "run"], '{"x": 1}', self.work, "3.13.9")
        self.assertEqual((below.returncode, below.stdout, below.stderr), (0, "", ""))

    def test_each_packaged_hook_answers_through_the_launcher_as_it_did_directly(self):
        project = plugin_project(self.work / "project")
        note = str(project / "workspace/docs/maps/note.md")
        payloads = {
            "SessionStart": {"source": "startup"},
            "UserPromptExpansion": {"prompt": "Plan the next Delivery."},
            "UserPromptSubmit": {"prompt": "Plan the next Delivery."},
            "PreToolUse": {"tool_name": "Write", "tool_input": {"file_path": note, "content": "[a](b.md)\n"}},
            "PostToolUse": {"tool_name": "Write", "tool_input": {"file_path": note}},
            "PostToolUseFailure": {"tool_name": "Agent", "tool_input": {}, "error": "failed"},
        }
        for host in ROOT_VARIABLES:
            for (event, matcher), argv in hook_commands(host).items():
                payload = dict(payloads[event], hook_event_name=event, session_id="floor", cwd=str(project))
                with self.subTest(host=host, event=event, matcher=matcher):
                    through = run(argv, payload, project)
                    plain = run([str(package(host) / argv[1]), *argv[2:]], payload, project)
                    self.assertEqual((through.returncode, through.stdout, through.stderr),
                                     (plain.returncode, plain.stdout, plain.stderr))
                    if event == "SessionStart":
                        self.assertIn(f"AGENT_MARKETPLACE_HOOKS_ACTIVE: {PLUGIN}", through.stdout)
                    if argv == vault_pre(host):
                        self.assertEqual(through.returncode, 2, through.stderr)


if __name__ == "__main__":
    unittest.main()
