"""Plugin runtime tests: hook payload normalization, the DB guard, the
integrity tripwire, CLI-side lifecycle inference, the plugin-root
dispatcher and the dashboard catalog."""

from __future__ import annotations

import hashlib
import json
import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import re
from pathlib import Path
from unittest import mock

from tools.tests.project_contract import CURRENT_PROJECT_CONTRACT_VERSION

REPO = Path(__file__).resolve().parents[2]
PMO_SCRIPTS = REPO / "dist" / "claude" / "project-management-office" / "scripts"
SET_SCRIPTS = REPO / "dist" / "claude" / "software-engineering-team" / "scripts"

sys.path.insert(0, str(PMO_SCRIPTS))

import hook_common  # noqa: E402

PATCH_CORPUS = json.loads((
    REPO / "tools" / "tests" / "data" / "hook_payloads.json"
).read_text(encoding="utf-8"))


def initialize_contract_repo(root: Path, project_key: str = "shop") -> None:
    (root / "workspace").mkdir(parents=True, exist_ok=True)
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "runtime@example.test"],
        ["git", "config", "user.name", "Runtime Tests"],
    ):
        subprocess.run(command, cwd=root, check=True)
    contract = {
        "schema_version": 1,
        "contract_version": CURRENT_PROJECT_CONTRACT_VERSION,
        "project_id": "runtime-project",
        "team_id": "software-engineering-team",
        "workspace": "workspace",
        "repository_fingerprint": "test",
        "delivery": {"requires_pull_request": False,
                     "target_branch": "master"},
        "marketplace_release": "0.1.0",
        "source_channel": "stable",
        "source_ref": "v0.1.0",
        "source_commit": "test",
        "components": {},
        "managed_surfaces": {},
        "vault": {},
        "upgrade_provenance": {},
    }
    contract["contract_sha256"] = hashlib.sha256(json.dumps(
        contract, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    (root / "workspace" / "config.json").write_text(json.dumps({
        "project_key": project_key,
        "agent_marketplace": contract,
    }), encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "baseline"], cwd=root, check=True
    )


def load_vault_hook():
    if str(SET_SCRIPTS) not in sys.path:
        sys.path.append(str(SET_SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "agent_marketplace_vault_hook", SET_SCRIPTS / "vault_hook.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_script(script_path: Path, payload: dict | None, env: dict,
               args: list[str] | None = None):
    proc = subprocess.run(
        [sys.executable, str(script_path), *(args or [])],
        input=json.dumps(payload) if payload is not None else "",
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )
    return proc.returncode, proc.stdout, proc.stderr


def run_cli(argv: list[str], env: dict):
    return run_script(PMO_SCRIPTS / "pmo_cli.py", None, env, argv)


class NormalizePayloadTests(unittest.TestCase):
    def assert_valid_patch_case(self, case):
        payload = {
            "tool_name": "apply_patch",
            "tool_input": {"patch": case["patch"]},
        }
        pmo = hook_common.normalize_payload(payload)
        vault = load_vault_hook().normalize(payload)
        self.assertEqual(vault["file_targets"], pmo["file_targets"])
        self.assertEqual(
            [[item["operation"], item["file_path"]]
             for item in pmo["file_targets"]],
            case["operations"],
        )

    def assert_invalid_patch_case(self, case):
        payload = {
            "tool_name": "apply_patch",
            "tool_input": {"patch": case["patch"]},
        }
        pmo = hook_common.normalize_payload(payload)
        vault = load_vault_hook().normalize(payload)
        self.assertEqual(pmo["file_targets"], [])
        self.assertEqual(vault["file_targets"], [])
        self.assertIn("patch_parse_error", pmo)
        self.assertEqual(
            vault.get("patch_parse_error"), pmo.get("patch_parse_error")
        )

    def test_shared_golden_patch_corpus_stays_in_parity(self):
        for case in PATCH_CORPUS["valid"]:
            with self.subTest(case=case["name"]):
                self.assert_valid_patch_case(case)
        for case in PATCH_CORPUS["invalid"]:
            with self.subTest(invalid=case["name"]):
                self.assert_invalid_patch_case(case)

    def test_claude_code_shape_passes_through(self):
        out = hook_common.normalize_payload({
            "hook_event_name": "PreToolUse", "tool_name": "Write",
            "cwd": "/proj",
            "tool_input": {"file_path": "/proj/a.md", "content": "x"},
        })
        self.assertEqual(out["tool_name"], "Write")
        self.assertEqual(out["file_targets"],
                         [{"file_path": "/proj/a.md", "content": "x"}])

    def test_multiedit_maps_to_edit(self):
        out = hook_common.normalize_payload({
            "hook_event_name": "PreToolUse", "tool_name": "MultiEdit",
            "cwd": "/proj",
            "tool_input": {"file_path": "/proj/a.md", "old_string": "a",
                           "new_string": "b"},
        })
        self.assertEqual(out["tool_name"], "Edit")
        self.assertEqual(out["file_targets"][0]["new_string"], "b")
        self.assertNotIn("content", out["file_targets"][0])

    def test_codex_apply_patch_expands_every_operation(self):
        patch = """*** Begin Patch
*** Add File: /proj/new.md
+new text
*** Update File: /proj/edit.md
@@
-old text
+newer text
*** Update File: /proj/source.md
*** Move to: /proj/moved.md
@@
-before
+after
*** Delete File: /proj/deleted.md
-gone
*** End Patch"""
        out = hook_common.normalize_payload({
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "cwd": "/proj",
            "tool_input": {"patch": patch},
        })
        self.assertEqual(out["tool_name"], "Edit")
        self.assertNotIn("patch_parse_error", out)
        self.assertEqual(
            [(target["operation"], target["file_path"])
             for target in out["file_targets"]],
            [("add", "/proj/new.md"), ("update", "/proj/edit.md"),
             ("update", "/proj/source.md"),
             ("move-target", "/proj/moved.md"),
             ("delete", "/proj/deleted.md")],
        )
        self.assertEqual(out["file_targets"][0]["content"], "new text")
        self.assertEqual(out["file_targets"][1]["old_string"], "old text")
        self.assertEqual(out["file_targets"][1]["new_string"], "newer text")

    def test_codex_apply_patch_parse_failure_is_explicit(self):
        out = hook_common.normalize_payload({
            "tool_name": "apply_patch", "tool_input": {"patch": "not a patch"}
        })
        self.assertEqual(out["file_targets"], [])
        self.assertIn("patch_parse_error", out)


def patch_case_test(case, valid: bool):
    def test(self):
        if valid:
            self.assert_valid_patch_case(case)
        else:
            self.assert_invalid_patch_case(case)
    return test


for _valid, _cases in ((True, PATCH_CORPUS["valid"]),
                       (False, PATCH_CORPUS["invalid"])):
    for _index, _case in enumerate(_cases):
        _slug = re.sub(r"[^a-z0-9]+", "_", _case["name"].lower()).strip("_")
        _kind = "valid" if _valid else "invalid"
        setattr(
            NormalizePayloadTests,
            f"test_patch_{_kind}_{_index:02d}_{_slug}",
            patch_case_test(_case, _valid),
        )


class DbGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "agentrof"
        self.env = {"AGENT_MARKETPLACE_HOME": str(self.home)}
        run_cli(["init-db"], self.env)

    def tearDown(self):
        self.tmp.cleanup()

    def test_direct_db_write_denied(self):
        code, _, err = run_script(PMO_SCRIPTS / "hook_guard_db.py", {
            "hook_event_name": "PreToolUse", "tool_name": "Write",
            "cwd": self.tmp.name,
            "tool_input": {"file_path": str(self.home / "pmo.db"),
                           "content": "x"},
        }, self.env)
        self.assertEqual(code, 2)
        self.assertIn("PMO CLI", err)

    def test_shell_command_naming_db_file_denied(self):
        code, _, err = run_script(PMO_SCRIPTS / "hook_guard_db.py", {
            "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "cwd": self.tmp.name,
            "tool_input": {"command":
                           f"sqlite3 {self.home}/pmo.db"
                           " 'DELETE FROM events'"},
        }, self.env)
        self.assertEqual(code, 2)
        self.assertIn("database", err)

    def test_ordinary_shell_command_passes(self):
        code, _, _ = run_script(PMO_SCRIPTS / "hook_guard_db.py", {
            "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "cwd": self.tmp.name,
            "tool_input": {"command": "ls -la"},
        }, self.env)
        self.assertEqual(code, 0)

    def test_apply_patch_db_write_and_unparseable_patch_fail_closed(self):
        db = self.home / "pmo.db"
        valid = "\n".join([
            "*** Begin Patch", f"*** Update File: {db}", "@@", "-old",
            "+new", "*** End Patch",
        ])
        for patch, expected in ((valid, "PMO CLI"), ("broken", "fails closed")):
            code, _, err = run_script(PMO_SCRIPTS / "hook_guard_db.py", {
                "hook_event_name": "PreToolUse", "tool_name": "apply_patch",
                "cwd": self.tmp.name, "tool_input": {"patch": patch},
            }, self.env)
            self.assertEqual(code, 2)
            self.assertIn(expected, err)


class RuntimePathBoundaryTests(unittest.TestCase):
    def test_hook_log_is_nested_under_product_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp) / "product"
            with mock.patch.dict(
                os.environ, {"AGENT_MARKETPLACE_HOME": str(product)}
            ):
                hook_common.log("namespace probe")
            self.assertTrue((product / "logs" / "hooks.log").is_file())
            self.assertFalse((product / "hooks.log").exists())

class VaultHookCodexTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"AGENT_MARKETPLACE_HOME": str(Path(self.tmp.name) / "agentrof")}

    def tearDown(self):
        self.tmp.cleanup()

    def test_register_emits_hook_sentinel_as_json(self):
        code, out, err = run_script(
            SET_SCRIPTS / "vault_hook.py",
            {"hook_event_name": "SessionStart"}, self.env, ["register"])
        self.assertEqual(code, 0, err)
        context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("AGENT_MARKETPLACE_HOOKS_ACTIVE: software-engineering-team", context)

    def test_apply_patch_multifile_vault_violation_is_denied(self):
        project = Path(self.tmp.name) / "project"
        (project / "workspace" / "docs").mkdir(parents=True)
        patch = "\n".join([
            "*** Begin Patch",
            f"*** Add File: {project / 'ordinary.txt'}", "+safe",
            f"*** Add File: {project / 'workspace/docs/a.md'}",
            "+[relative](b.md)", "*** End Patch",
        ])
        code, _, err = run_script(SET_SCRIPTS / "vault_hook.py", {
            "hook_event_name": "PreToolUse", "tool_name": "apply_patch",
            "cwd": str(project), "tool_input": {"patch": patch},
        }, self.env, ["pre"])
        self.assertEqual(code, 2)
        self.assertIn("vault-absolute wikilink", err)

    def test_unparseable_apply_patch_fails_closed(self):
        code, _, err = run_script(SET_SCRIPTS / "vault_hook.py", {
            "hook_event_name": "PreToolUse", "tool_name": "apply_patch",
            "cwd": self.tmp.name, "tool_input": {"patch": "broken"},
        }, self.env, ["pre"])
        self.assertEqual(code, 2)
        self.assertIn("fails closed", err)

    def experience_write(self, relative, content="safe"):
        project = Path(self.tmp.name) / "project"
        docs = project / "workspace" / "docs"
        docs.mkdir(parents=True, exist_ok=True)
        (project / "workspace" / "config.json").write_text(
            json.dumps({"team_id": "software-engineering-team"}),
            encoding="utf-8",
        )
        return run_script(SET_SCRIPTS / "vault_hook.py", {
            "hook_event_name": "PreToolUse", "tool_name": "Write",
            "cwd": str(project),
            "tool_input": {"file_path": str(docs / relative),
                           "content": content},
        }, self.env, ["pre"])

    def test_experience_generated_artifact_and_machine_fields_are_denied(self):
        cases = (
            ("experience-design/programs/prg-001/releases/rel-001/_generated/status.md", "safe", "compiler-owned"),
            ("experience-design/programs/prg-001/releases/rel-001/release.md", "registry_hash: sha256:x", "machine-managed"),
            ("experience-design/programs/prg-001/releases/rel-001/journeys/wrong.md", "safe", "invalid Experience Design"),
        )
        for relative, content, expected in cases:
            with self.subTest(relative=relative):
                code, _, err = self.experience_write(relative, content)
                self.assertEqual(code, 2)
                self.assertIn(expected, err)

    def test_design_system_approved_tree_requires_begin_revision(self):
        project = Path(self.tmp.name) / "project"
        docs = project / "workspace" / "docs"
        master = docs / "design-system" / "MASTER.md"
        master.parent.mkdir(parents=True)
        master.write_text(
            "---\ntype: design-master\nstatus: approved\nrevision: 1\n---\n",
            encoding="utf-8",
        )
        code, _, err = self.experience_write(
            "design-system/pages/buttons.md", "# Buttons\n"
        )
        self.assertEqual(code, 2)
        self.assertIn("begin-revision", err)

    def test_design_system_draft_tree_accepts_authored_pages(self):
        project = Path(self.tmp.name) / "project"
        master = (project / "workspace" / "docs" / "design-system"
                  / "MASTER.md")
        master.parent.mkdir(parents=True)
        master.write_text(
            "---\ntype: design-master\nstatus: draft\nrevision: 2\n---\n",
            encoding="utf-8",
        )
        code, _, err = self.experience_write(
            "design-system/pages/buttons.md", "# Buttons\n"
        )
        self.assertEqual(code, 0, err)

    def test_experience_artifact_is_editable_only_while_manifest_is_draft(self):
        project = Path(self.tmp.name) / "project"
        artifacts = (project / "workspace" / "docs" / "experience-design"
                     / "programs" / "prg-001" / "releases" / "rel-001"
                     / "artifacts")
        artifacts.mkdir(parents=True)
        manifest = artifacts / "catalog-artifact.md"
        manifest.write_text(
            "---\ntype: artifact-manifest\nstatus: draft\n---\n",
            encoding="utf-8",
        )
        html = artifacts / "catalog-preview.html"
        html.write_text("<p>draft</p>\n", encoding="utf-8")
        code, _, err = self.experience_write(
            "experience-design/programs/prg-001/releases/rel-001/"
            "artifacts/catalog-preview.html", "<p>changed</p>\n"
        )
        self.assertEqual(code, 0, err)

        manifest.write_text(
            "---\ntype: artifact-manifest\nstatus: approved\n---\n",
            encoding="utf-8",
        )
        code, _, err = self.experience_write(
            "experience-design/programs/prg-001/releases/rel-001/"
            "artifacts/catalog-preview.html", "<p>changed again</p>\n"
        )
        self.assertEqual(code, 2)
        self.assertIn("immutable", err)

    def test_approved_experience_release_denies_authored_content_changes(self):
        project = Path(self.tmp.name) / "project"
        release = (project / "workspace" / "docs" / "experience-design"
                   / "programs" / "prg-001" / "releases" / "rel-001"
                   / "release.md")
        release.parent.mkdir(parents=True)
        release.write_text(
            "---\ntype: release\nstatus: approved\n---\n",
            encoding="utf-8",
        )
        code, _, err = self.experience_write(
            "experience-design/programs/prg-001/releases/rel-001/"
            "spaces/marketplace/domains/catalog/screens/catalog-screen.md",
            "---\ntype: screen\n---\n",
        )
        self.assertEqual(code, 2)
        self.assertIn("next release", err)

    def test_valid_experience_path_passes_pre_write(self):
        code, _, err = self.experience_write(
            "experience-design/programs/prg-001/releases/rel-001/"
            "spaces/marketplace/domains/catalog/screens/catalog-screen.md",
            "---\ntype: screen\n---\n",
        )
        self.assertEqual(code, 0, err)

    def test_multifile_overlay_resolves_targets_added_in_same_patch(self):
        project = Path(self.tmp.name) / "project"
        docs = project / "workspace" / "docs"
        docs.mkdir(parents=True)
        (project / "workspace" / "config.json").write_text(
            json.dumps({"team_id": "software-engineering-team"}),
            encoding="utf-8")
        landscape = docs / "solution-design" / "landscape.md"
        engagement = docs / "solution-design" / "engagements" / "search.md"
        patch = "\n".join([
            "*** Begin Patch", f"*** Add File: {landscape}",
            "+---", "+type: landscape", "+title: Search landscape",
            "+status: draft", "+related_to:",
            "+  - \"[[solution-design/engagements/search|Search engagement]]\"",
            "+tags:", "+  - doc/landscape", "+  - status/draft", "+---",
            "+# Search landscape", f"*** Add File: {engagement}",
            "+---", "+type: engagement", "+title: Search engagement",
            "+tags:", "+  - doc/engagement", "+---",
            "+# Search engagement", "*** End Patch",
        ])
        code, _, err = run_script(SET_SCRIPTS / "vault_hook.py", {
            "hook_event_name": "PreToolUse", "tool_name": "apply_patch",
            "cwd": str(project), "tool_input": {"patch": patch},
        }, self.env, ["pre"])
        self.assertEqual(code, 0, err)

    def test_generated_relation_block_direct_edit_is_denied(self):
        project = Path(self.tmp.name) / "project"
        note = project / "workspace" / "docs" / "solution-design" / "landscape.md"
        note.parent.mkdir(parents=True)
        (project / "workspace" / "config.json").write_text("{}", encoding="utf-8")
        note.write_text(
            "# Landscape\n\n## Related knowledge "
            "<!-- sec: relations:generated:start -->\n\n- old\n\n"
            "<!-- sec: relations:generated:end -->\n", encoding="utf-8")
        code, _, err = run_script(SET_SCRIPTS / "vault_hook.py", {
            "hook_event_name": "PreToolUse", "tool_name": "Edit",
            "cwd": str(project), "tool_input": {
                "file_path": str(note), "old_string": "- old",
                "new_string": "- changed",
            },
        }, self.env, ["pre"])
        self.assertEqual(code, 2)
        self.assertIn("machine-owned inverse relation", err)

    def test_bash_merkle_inventory_detects_vault_move_or_delete(self):
        project = Path(self.tmp.name) / "project"
        docs = project / "workspace" / "docs"
        docs.mkdir(parents=True)
        note = docs / "home.md"
        note.write_text("# Home\n", encoding="utf-8")
        payload = {
            "session_id": "bash-vault", "cwd": str(project),
            "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": "mv workspace/docs/home.md x"},
        }
        self.assertEqual(run_script(
            SET_SCRIPTS / "vault_hook.py", payload, self.env, ["pre"])[0], 0)
        note.rename(docs / "moved.md")
        payload["hook_event_name"] = "PostToolUse"
        code, _, err = run_script(
            SET_SCRIPTS / "vault_hook.py", payload, self.env, ["post"])
        self.assertEqual(code, 2)
        self.assertIn("vault inventory", err)


class TeamPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "agentrof"
        self.project = root / "project"
        (self.project / ".git").mkdir(parents=True)
        (self.project / "workspace").mkdir()
        self.env = {"AGENT_MARKETPLACE_HOME": str(self.home)}

    def tearDown(self):
        self.tmp.cleanup()

    def payload(self, tool="Write", **extra):
        payload = {
            "session_id": "session-one",
            "cwd": str(self.project),
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "permission_mode": "default",
            "tool_input": {"file_path": str(self.project / "change.txt"),
                           "content": "change"},
        }
        payload.update(extra)
        return payload

    def guard(self, payload):
        return run_script(
            SET_SCRIPTS / "team_guard.py", payload, self.env, ["pre"]
        )

    def mark_ready(self):
        code, out, err = run_script(
            PMO_SCRIPTS / "hook_session_start.py",
            {"session_id": "session-one", "cwd": str(self.project),
             "hook_event_name": "SessionStart", "source": "startup"},
            self.env,
        )
        self.assertEqual(code, 0, err)
        self.assertIn("AGENT_MARKETPLACE_PMO_READY", out)

    def test_missing_pmo_state_denies_every_local_mutation_surface(self):
        before = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }
        for tool in ("Write", "Edit", "MultiEdit", "apply_patch", "Bash",
                     "exec_command", "shell"):
            with self.subTest(tool=tool):
                code, _, err = self.guard(self.payload(tool))
                self.assertEqual(code, 2)
                self.assertIn("did not mark this session ready", err)
                self.assertIn("No files or project state were changed", err)
        after = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }
        self.assertEqual(after, before)

    def test_nonmutation_tool_passes_without_pmo(self):
        code, _, err = self.guard(self.payload("Read"))
        self.assertEqual(code, 0, err)

    def test_exact_read_only_plugin_diagnostics_pass_without_pmo(self):
        for tool, key, command in (
            ("Bash", "command", "claude plugin list --json"),
            ("exec_command", "cmd", "codex plugin list --json"),
            ("shell", "command", "codex plugin list --json"),
        ):
            with self.subTest(tool=tool, command=command):
                payload = self.payload(tool)
                payload["tool_input"] = {key: command}
                code, _, err = self.guard(payload)
                self.assertEqual(code, 0, err)

    def test_shell_diagnostic_variants_fail_closed_without_pmo(self):
        for command in (
            "codex plugin list --json && touch change.txt",
            "codex plugin list --json > inventory.json",
            "codex plugin list --available --json",
            "AGENT_MARKETPLACE_HOME=/tmp/example codex plugin list --json",
            "codex plugin add project-management-office@agent-marketplace",
        ):
            with self.subTest(command=command):
                payload = self.payload("exec_command")
                payload["tool_input"] = {"cmd": command}
                code, _, err = self.guard(payload)
                self.assertEqual(code, 2)
                self.assertIn("did not mark this session ready", err)

    def test_only_the_pmo_launcher_receives_the_upgrade_exception(self):
        launcher = self.home / "bin" / "pmo_cli.py"
        for command in (
            f"{launcher} upgrade status --project-root {self.project} --json",
            f"{launcher} upgrade prepare-branch --project-root {self.project}",
            f"python3 {launcher} upgrade plan --project-root {self.project}",
        ):
            with self.subTest(command=command):
                payload = self.payload("Bash")
                payload["tool_input"] = {"command": command}
                code, _, err = self.guard(payload)
                self.assertEqual(code, 0, err)
        for command in (
            "touch upgrade status",
            "/tmp/pmo_cli.py upgrade status",
            f"{launcher} issue upgrade status",
            f"{launcher} upgrade unknown",
            f"{launcher} upgrade status && touch change.txt",
        ):
            with self.subTest(command=command):
                payload = self.payload("Bash")
                payload["tool_input"] = {"command": command}
                code, _, err = self.guard(payload)
                self.assertEqual(code, 2)
                self.assertIn("did not mark this session ready", err)

    def test_ready_session_passes_and_registers_team(self):
        self.mark_ready()
        code, _, err = self.guard(self.payload())
        self.assertEqual(code, 0, err)
        registry = json.loads(
            (self.home / "plugin_roots.json").read_text(encoding="utf-8")
        )
        self.assertIn("software-engineering-team", registry["plugins"])

    def test_whole_file_managed_root_denies_resolvable_direct_writers(self):
        agents = self.project / "AGENTS.md"
        agents.write_text(
            "<!-- generated by agent-marketplace software-engineering-team "
            "for codex; do not edit by hand -->\n",
            encoding="utf-8",
        )
        payloads = []
        for tool in ("Write", "Edit", "MultiEdit"):
            payload = self.payload(tool)
            payload["tool_input"] = {
                "file_path": str(agents), "content": "replacement"
            }
            payloads.append(payload)
        patch = self.payload("apply_patch")
        patch["tool_input"] = {
            "patch": "*** Begin Patch\n"
                     f"*** Update File: {agents}\n"
                     "@@\n-old\n+new\n*** End Patch"
        }
        payloads.append(patch)
        shell = self.payload("Bash")
        shell["tool_input"] = {"command": f"touch {agents}"}
        payloads.append(shell)
        for payload in payloads:
            with self.subTest(tool=payload["tool_name"]):
                code, _, err = self.guard(payload)
                self.assertEqual(code, 2)
                self.assertIn("whole-file managed surface", err)

    def test_user_companion_is_not_a_managed_direct_target(self):
        self.mark_ready()
        payload = self.payload("Write")
        payload["tool_input"] = {
            "file_path": str(self.project / "AGENTS.user.md"),
            "content": "# User rules\n",
        }
        code, _, err = self.guard(payload)
        self.assertEqual(code, 0, err)

    def test_postflight_locks_session_when_status_reports_surface_drift(self):
        self.mark_ready()
        launcher = self.home / "bin" / "pmo_cli.py"
        launcher.write_text(
            "import json\n"
            "print(json.dumps({\"status\": "
            "\"AGENT_MARKETPLACE_UPGRADE_REQUIRED_BLOCKED\", "
            "\"reasons\": [\"PROJECT_INSTRUCTION_DRIFT:codex:AGENTS.md\"], "
            "\"blockers\": []}))\n",
            encoding="utf-8",
        )
        payload = self.payload("Bash")
        payload["hook_event_name"] = "PostToolUse"
        payload["tool_input"] = {"command": "custom-writer"}
        code, _, err = run_script(
            SET_SCRIPTS / "team_guard.py", payload, self.env, ["post"]
        )
        self.assertEqual(code, 2)
        self.assertIn("subsequent marketplace mutations are locked", err)
        state = json.loads(
            (self.home / "sessions" / (
                hashlib.sha256(b"session-one").hexdigest() + ".json"
            )).read_text(encoding="utf-8")
        )
        self.assertFalse(state["pmo_ready"])

    def test_first_setup_config_window_does_not_self_lock(self):
        self.mark_ready()
        config = self.project / "workspace" / "config.json"
        config.write_text(json.dumps({
            "team_id": "software-engineering-team",
            "output_language": "english",
        }), encoding="utf-8")
        (self.project / "app").mkdir()
        (self.project / "app" / "config.json").write_text(
            json.dumps({"framework": "user-owned"}), encoding="utf-8"
        )
        code, _, err = self.guard(self.payload())
        self.assertEqual(code, 0, err)
        code, out, err = run_cli([
            "upgrade", "status", "--project-root", str(self.project), "--json",
        ], self.env)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["status"], "AGENT_MARKETPLACE_CURRENT")

        config.write_text(json.dumps({
            "team_id": "software-engineering-team",
            "project_key": "registered-without-contract",
        }), encoding="utf-8")
        code, _, err = self.guard(self.payload())
        self.assertEqual(code, 2)
        self.assertIn("AGENT_MARKETPLACE_UPGRADE_REQUIRED_BLOCKED", err)

    def test_unreadable_upgrade_status_fails_closed(self):
        self.mark_ready()
        launcher = self.home / "bin" / "pmo_cli.py"
        launcher.write_text("print('not-json')\n", encoding="utf-8")
        code, _, err = self.guard(self.payload())
        self.assertEqual(code, 2)
        self.assertIn("UPGRADE_STATUS_UNAVAILABLE", err)

    def test_plan_mode_and_foreign_team_fail_closed(self):
        self.mark_ready()
        code, _, err = self.guard(self.payload(permission_mode="plan"))
        self.assertEqual(code, 2)
        self.assertIn("Plan mode", err)
        (self.project / "workspace" / "config.json").write_text(
            json.dumps({"managed_by": "another-team"}), encoding="utf-8"
        )
        code, _, err = self.guard(self.payload())
        self.assertEqual(code, 2)
        self.assertIn("another-team", err)

    def test_session_end_revokes_readiness(self):
        self.mark_ready()
        code, _, err = self.guard(self.payload())
        self.assertEqual(code, 0, err)
        code, _, err = run_script(
            PMO_SCRIPTS / "hook_session_end.py",
            {"session_id": "session-one", "cwd": str(self.project),
             "hook_event_name": "SessionEnd", "reason": "other"},
            self.env,
        )
        self.assertEqual(code, 0, err)
        code, _, err = self.guard(self.payload())
        self.assertEqual(code, 2)
        self.assertIn("did not mark this session ready", err)


class IntegrityTripwireTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "agentrof"
        self.env = {"AGENT_MARKETPLACE_HOME": str(self.home)}
        run_cli(["init-db"], self.env)
        run_cli(["project", "register", "--key", "shop"], self.env)

    def tearDown(self):
        self.tmp.cleanup()

    def test_verify_clean_after_cli_mutations(self):
        code, out, _ = run_cli(["verify"], self.env)
        self.assertEqual(code, 0, out)

    def test_foreign_write_detected_by_verify_and_wo_validate(self):
        con = sqlite3.connect(self.home / "pmo.db")
        with self.assertRaisesRegex(sqlite3.OperationalError,
                                    "agent_marketplace_writer_epoch"):
            con.execute("UPDATE projects SET name = 'tampered'")
        con.close()
        code, _, _ = run_cli(["verify"], self.env)
        self.assertEqual(code, 0)

    def test_verify_json_shape(self):
        code, out, _ = run_cli(["verify", "--json"], self.env)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"ok": True, "problem": ""})

    def test_ensure_bootstraps_and_reports(self):
        code, out, _ = run_cli(["ensure"], self.env)
        self.assertEqual(code, 0, out)
        self.assertIn("ensure complete", out)
        self.assertTrue((self.home / "bin" / "pmo_cli.py").is_file())
        self.assertTrue((self.home / "bin" / "marketplace_run.py").is_file())


class LifecycleInferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "agentrof"
        self.env = {"AGENT_MARKETPLACE_HOME": str(self.home)}
        self.project_root = root / "proj"
        self.project_root.mkdir()
        initialize_contract_repo(self.project_root)
        run_cli(["init-db"], self.env)
        run_cli(["project", "register", "--key", "shop"], self.env)
        backlog = root / "backlog.json"
        backlog.write_text(json.dumps({
            "epics": [{"external_id": "EP-01", "title": "Core"}],
            "stories": [{
                "external_id": "WP-01", "epic": "EP-01", "title": "Slice",
                "scope": "s", "excludes": "x", "dor": "d", "dod": "d",
            }],
        }), encoding="utf-8")
        run_cli(["item", "import", "--project-key", "shop",
                 "--json-file", str(backlog)], self.env)
        run_cli(["work-order", "init", "--project-key", "shop",
                 "--work-order-key", "wo1", "--request", "build",
                 "--worktree", str(self.project_root),
                 "--story", "WP-01"], self.env)

    def tearDown(self):
        self.tmp.cleanup()

    def cli_in_project(self, argv):
        proc = subprocess.run(
            [sys.executable, str(PMO_SCRIPTS / "pmo_cli.py"), *argv],
            capture_output=True, text=True, cwd=str(self.project_root),
            env={**os.environ, **self.env},
        )
        return proc.returncode, proc.stdout, proc.stderr

    def attempts(self):
        con = sqlite3.connect(self.home / "pmo.db")
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM task_attempts ORDER BY id")]
        con.close()
        return rows

    def test_task_close_synthesizes_attempt_without_hooks(self):
        code, out, err = self.cli_in_project(
            ["task", "open", "--work-order-key", "wo1",
             "--role", "backend_developer", "--step", "2",
             "--title", "implement"])
        self.assertEqual(code, 0, err)
        code, _, err = self.cli_in_project(
            ["task", "close", "--work-order-key", "wo1",
             "--role", "backend_developer", "--outcome", "done"])
        self.assertEqual(code, 0, err)
        rows = self.attempts()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "cli_inferred")
        self.assertEqual(rows[0]["outcome"], "done")
        self.assertTrue(rows[0]["finished_at"])

    def test_session_reconcile_appends_once(self):
        code, out, _ = run_cli(
            ["session-reconcile", "--project-key", "shop",
             "--worktree", str(self.project_root)], self.env)
        self.assertEqual(code, 0)
        self.assertIn("appended 1", out)
        code, out, _ = run_cli(
            ["session-reconcile", "--project-key", "shop",
             "--worktree", str(self.project_root)], self.env)
        self.assertEqual(code, 0)
        self.assertIn("appended 0", out, "dedup on the newest marker event")


class DispatcherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "agentrof"
        self.env = {"AGENT_MARKETPLACE_HOME": str(self.home)}
        self.plugin_root = Path(self.tmp.name) / "install" / "sample-team"
        (self.plugin_root / ".claude-plugin").mkdir(parents=True)
        (self.plugin_root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "sample-team", "version": "1.0.0"}),
            encoding="utf-8")
        (self.plugin_root / "scripts").mkdir()
        (self.plugin_root / "scripts" / "hello.py").write_text(
            "import sys\nprint('hello', sys.argv[1])\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def dispatch(self, argv):
        return run_script(PMO_SCRIPTS / "marketplace_run.py", None, self.env,
                          argv)

    def test_register_path_run(self):
        code, out, err = self.dispatch(
            ["register", "--plugin", "sample-team",
             "--root", str(self.plugin_root)])
        self.assertEqual(code, 0, err)
        code, out, _ = self.dispatch(["path", "sample-team", "scripts/hello.py"])
        self.assertEqual(code, 0)
        self.assertEqual(
            out.strip(),
            str((self.plugin_root / "scripts" / "hello.py").resolve()))
        code, out, _ = self.dispatch(
            ["run", "sample-team", "scripts/hello.py", "world"])
        self.assertEqual(code, 0)
        self.assertIn("hello world", out)
        registry = json.loads((self.home / "plugin_roots.json").read_text())
        self.assertEqual(registry["schema_version"], 2)
        self.assertEqual(
            set(registry["plugins"]["sample-team"]["hosts"]), {"claude"}
        )

    def test_dual_host_registry_requires_host_for_different_runtime_files(self):
        codex_root = Path(self.tmp.name) / "install" / "codex-sample-team"
        (codex_root / ".codex-plugin").mkdir(parents=True)
        (codex_root / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"name": "sample-team", "version": "1.0.0"}),
            encoding="utf-8",
        )
        (codex_root / "scripts").mkdir()
        (codex_root / "scripts" / "hello.py").write_text(
            "print('codex-host')\n", encoding="utf-8"
        )
        for root in (self.plugin_root, codex_root):
            code, _, err = self.dispatch([
                "register", "--plugin", "sample-team", "--root", str(root)
            ])
            self.assertEqual(code, 0, err)
        registry = json.loads((self.home / "plugin_roots.json").read_text())
        self.assertEqual(
            set(registry["plugins"]["sample-team"]["hosts"]),
            {"claude", "codex"},
        )
        code, _, err = self.dispatch([
            "run", "sample-team", "scripts/hello.py", "world"
        ])
        self.assertEqual(code, 1)
        self.assertIn("no usable install root", err)
        code, out, err = self.dispatch([
            "run", "--host", "codex", "sample-team", "scripts/hello.py"
        ])
        self.assertEqual(code, 0, err)
        self.assertIn("codex-host", out)

    def test_unregistered_plugin_names_the_remedy(self):
        code, _, err = self.dispatch(["run", "ghost-team", "scripts/x.py"])
        self.assertEqual(code, 1)
        self.assertIn("setup entry", err)

    def test_missing_root_after_update_errors(self):
        self.dispatch(["register", "--plugin", "sample-team",
                       "--root", str(self.plugin_root)])
        import shutil
        shutil.rmtree(self.plugin_root)
        code, _, err = self.dispatch(["path", "sample-team", "scripts/hello.py"])
        self.assertEqual(code, 1)
        self.assertIn("setup entry", err)

    def test_hook_registration_feeds_dispatcher(self):
        run_cli(["sync-launcher"], self.env)
        code, _, err = run_script(
            SET_SCRIPTS / "team_guard.py",
            {"hook_event_name": "SessionStart"},
            self.env, ["register"])
        self.assertEqual(code, 0, err)
        registry = json.loads(
            (self.home / "plugin_roots.json").read_text(encoding="utf-8"))
        self.assertIn("software-engineering-team", registry["plugins"])

    def test_concurrent_registration_keeps_every_plugin(self):
        processes = []
        for index in range(20):
            processes.append(subprocess.Popen(
                [sys.executable, str(PMO_SCRIPTS / "marketplace_run.py"),
                 "register", "--plugin", f"team-{index}",
                 "--root", str(self.plugin_root)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, **self.env},
            ))
        for process in processes:
            _, err = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, err)
        registry = json.loads(
            (self.home / "plugin_roots.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            set(registry["plugins"]), {f"team-{index}" for index in range(20)}
        )

    def test_ambiguous_dual_manifest_root_is_rejected(self):
        (self.plugin_root / ".codex-plugin").mkdir()
        (self.plugin_root / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"name": "sample-team", "version": "2.0.0"}),
            encoding="utf-8")
        code, _, err = self.dispatch(
            ["register", "--plugin", "sample-team", "--root", str(self.plugin_root)])
        self.assertEqual(code, 1)
        self.assertIn("unambiguous host manifest", err)


class DashboardCatalogTests(unittest.TestCase):
    def test_plugin_roots_registry_feeds_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "agentrof"
            home.mkdir(parents=True)
            install = Path(tmp) / "cache" / "project-management-office"
            (install / ".claude-plugin").mkdir(parents=True)
            (install / ".claude-plugin" / "plugin.json").write_text(
                json.dumps({"name": "project-management-office",
                            "version": "1.2.0",
                            "description": "backbone"}), encoding="utf-8")
            (home / "plugin_roots.json").write_text(json.dumps({
                "schema_version": 1,
                "plugins": {"project-management-office": {
                    "root": str(install), "version": "1.2.0",
                    "registered_at": "2026-07-18T00:00:00+00:00"}},
            }), encoding="utf-8")
            env = {**os.environ, "AGENT_MARKETPLACE_HOME": str(home),
                   "AGENT_MARKETPLACE_CLAUDE_PLUGINS_DIR": str(Path(tmp) / "no-claude"),
                   "AGENT_MARKETPLACE_CODEX_PLUGINS_DIR": str(Path(tmp) / "no-codex")}
            proc = subprocess.run(
                [sys.executable, "-c",
                 "import sys, json;"
                 f"sys.path.insert(0, {str(PMO_SCRIPTS)!r});"
                 "import pmo_dashboard;"
                 "print(json.dumps(pmo_dashboard.scan_catalog()))"],
                capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            catalog = json.loads(proc.stdout)
            team = catalog["teams"]["project-management-office"]
            self.assertEqual(team["kind"], "backbone")
            self.assertEqual(team["installs"][0]["version"], "1.2.0")
            self.assertEqual(team["installs"][0]["scope"], "local")

    def test_codex_cache_is_discovered_and_deduplicated_by_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "agentrof"
            home.mkdir()
            cache = Path(tmp) / "codex-cache"
            install = cache / "market" / "software-engineering-team" / "9.2.0"
            (install / ".codex-plugin").mkdir(parents=True)
            (install / ".codex-plugin" / "plugin.json").write_text(json.dumps({
                "name": "software-engineering-team", "version": "9.2.0",
                "description": "team", "author": {"name": "Agentrof"},
            }), encoding="utf-8")
            (install / "skills" / "setup").mkdir(parents=True)
            (install / "skills" / "setup" / "SKILL.md").write_text(
                "---\nname: setup\ndescription: Setup.\n---\n", encoding="utf-8")
            env = {**os.environ, "AGENT_MARKETPLACE_HOME": str(home),
                   "AGENT_MARKETPLACE_CLAUDE_PLUGINS_DIR": str(Path(tmp) / "no-claude"),
                   "AGENT_MARKETPLACE_CODEX_PLUGINS_DIR": str(cache)}
            proc = subprocess.run(
                [sys.executable, "-c",
                 "import sys,json;"
                 f"sys.path.insert(0,{str(PMO_SCRIPTS)!r});"
                 "import pmo_dashboard;"
                 "print(json.dumps(pmo_dashboard.scan_catalog()))"],
                capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            teams = json.loads(proc.stdout)["teams"]
            self.assertEqual(list(teams), ["software-engineering-team"])
            self.assertEqual(teams["software-engineering-team"]["installs"], [{
                "version": "9.2.0", "scope": "codex", "project_path": "",
                "last_updated": "",
            }])

    def test_claude_custom_skill_surface_is_scanned_from_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "agentrof"
            home.mkdir()
            plugins = root / "claude-plugins"
            install = root / "install" / "software-engineering-team"
            (install / ".claude-plugin").mkdir(parents=True)
            (install / ".claude-plugin" / "plugin.json").write_text(json.dumps({
                "name": "software-engineering-team", "version": "9.2.0",
                "description": "team", "dependencies": ["project-management-office"],
                "skills": "./skills/",
            }), encoding="utf-8")
            (install / "skills" / "setup").mkdir(parents=True)
            (install / "skills" / "setup" / "SKILL.md").write_text(
                "---\nname: setup\ndescription: Setup.\n"
                "disable-model-invocation: true\n---\n", encoding="utf-8")
            plugins.mkdir()
            (plugins / "installed_plugins.json").write_text(json.dumps({
                "plugins": {"software-engineering-team@agent-marketplace": [{
                    "installPath": str(install), "version": "9.2.0",
                    "scope": "user", "lastUpdated": "2026-08-03T00:00:00Z",
                }]},
            }), encoding="utf-8")
            env = {**os.environ, "AGENT_MARKETPLACE_HOME": str(home),
                   "AGENT_MARKETPLACE_CLAUDE_PLUGINS_DIR": str(plugins),
                   "AGENT_MARKETPLACE_CODEX_PLUGINS_DIR": str(root / "no-codex")}
            proc = subprocess.run(
                [sys.executable, "-c",
                 "import sys,json;"
                 f"sys.path.insert(0,{str(PMO_SCRIPTS)!r});"
                 "import pmo_dashboard;"
                 "print(json.dumps(pmo_dashboard.scan_catalog()))"],
                capture_output=True, text=True, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            team = json.loads(proc.stdout)["teams"]["software-engineering-team"]
            self.assertEqual([skill["name"] for skill in team["skills"]], ["setup"])


if __name__ == "__main__":
    unittest.main()
