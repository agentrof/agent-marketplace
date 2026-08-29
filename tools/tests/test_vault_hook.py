from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins/software-engineering-team/scripts"
HOOK = ROOT / "platforms/shared/software-engineering-team/overlay/scripts/vault_hook.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import experience_application_check
import experience_compile


def load_hook():
    spec = importlib.util.spec_from_file_location("opaque_vault_hook", HOOK)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class VaultHookPrototypeTests(unittest.TestCase):
    def setUp(self):
        self.hook = load_hook()

    def test_hook_has_no_application_surface_or_content_guard(self):
        source = HOOK.read_text(encoding="utf-8")
        self.assertNotIn("application.html", source)
        self.assertNotIn("application-map", source)
        self.assertNotIn("experience-application-runtime", source)

    def test_recovery_excludes_author_owned_prototype_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "workspace/docs"
            application = docs / "experience-design/artifacts/application.html"
            application.parent.mkdir(parents=True)
            application.write_text("arbitrary\n", encoding="utf-8")
            before = self.hook.experience_tree_snapshot(docs)
            application.write_text("changed\n", encoding="utf-8")
            after = self.hook.experience_tree_snapshot(docs)
            relative = "experience-design/artifacts/application.html"
            self.assertNotIn(relative, before)
            self.assertNotIn(relative, after)
            self.assertNotIn(relative, self.hook.vault_inventory(docs))

    def test_recovery_snapshot_rejects_cross_platform_path_aliases(self):
        row = {"kind": "directory", "mode": 0o755}
        invalid = (
            r"experience-design/C:\outside",
            r"experience-design/\\server\share",
            "experience-design/../outside",
            "experience-design//double",
            "experience-design/NUL",
            "experience-design/trailing.",
            "experience-design/control\nname",
        )
        for relative in invalid:
            with self.subTest(relative=relative):
                problem = self.hook.experience_tree_snapshot_safety_problem({
                    relative: row,
                })
                self.assertTrue(problem)
        self.assertEqual(
            self.hook.experience_tree_snapshot_safety_problem({
                "experience-design": row,
                "experience-design/demo": row,
            }),
            "",
        )

    def test_recovery_target_is_lexically_contained(self):
        with tempfile.TemporaryDirectory() as temporary:
            vault = Path(temporary) / "workspace/docs"
            target = self.hook.experience_recovery_target(
                vault, "experience-design/demo/_generated/state.json",
            )
            self.assertEqual(
                target,
                Path(os.path.abspath(
                    vault / "experience-design/demo/_generated/state.json"
                )),
            )
            with self.assertRaises(ValueError):
                self.hook.experience_recovery_target(
                    vault, r"experience-design/C:\outside",
                )


class VaultHookShellContractTests(unittest.TestCase):
    def setUp(self):
        self.hook = load_hook()

    @staticmethod
    def project(root: Path) -> tuple[Path, Path]:
        docs = root / "workspace" / "docs"
        docs.mkdir(parents=True)
        config = root / "workspace" / "config.json"
        config.write_text(json.dumps({
            "schema_version": 2,
            "team_id": "software-engineering-team",
            "output_language": "English",
            "terminology_language": "English",
        }, indent=2) + "\n", encoding="utf-8")
        return docs, config

    @staticmethod
    def payload(root: Path, command: str, field: str = "command") -> dict:
        return {
            "tool_name": "Bash",
            "tool_input": {field: command},
            "cwd": str(root),
            "session_id": "shell-contract",
            "tool_use_id": "shell-contract-event",
        }

    @classmethod
    def attested_writer_payload(
        cls, root: Path, command: str, field: str = "command",
    ) -> dict:
        payload = cls.payload(root, command, field)
        if os.name == "nt":
            payload["shell_family"] = "cmd"
        return payload

    @staticmethod
    def run_hook(mode: str, payload: dict) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(SCRIPTS)
        return subprocess.run(
            [sys.executable, str(HOOK), mode],
            input=json.dumps(payload), capture_output=True, text=True,
            check=False, env=environment, timeout=10,
        )

    @staticmethod
    def run_composed_hook(
        hook: Path, mode: str, payload: dict,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(hook), mode],
            input=json.dumps(payload), capture_output=True, text=True,
            check=False,
        )

    @staticmethod
    def create_directory_alias(alias: Path, target: Path) -> None:
        if os.name == "nt":
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(alias), str(target)],
                capture_output=True, text=True, check=False,
            )
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)
            return
        alias.symlink_to(target, target_is_directory=True)

    def config_command(self, config: Path, interpreter: str | None = None) -> str:
        argv = [
            interpreter or sys.executable,
            str(SCRIPTS / "project_config.py"), "set",
            "--config", str(config), "--field", "output_language",
            "--value", "Turkish",
        ]
        return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)

    def application_command(self, docs: Path, interpreter: str | None = None) -> str:
        argv = [
            interpreter or sys.executable,
            str(SCRIPTS / "experience_compile.py"),
            "begin-application-revision",
            "--root", str(docs / "experience-design"),
            "--scope-plan", str(docs / "scope-plan.json"),
            "--proposal-hash", "sha256:" + "0" * 64,
        ]
        return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)

    def application_draft(
        self, root: Path, package: Path,
    ) -> tuple[Path, Path]:
        docs, _config = self.project(root)
        repository = subprocess.run(
            ["git", "init", str(root)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(
            repository.returncode, 0,
            repository.stdout + repository.stderr,
        )
        setup = subprocess.run(
            [
                sys.executable,
                str(package / "scripts" / "setup_project.py"),
                "apply", "--project-root", str(root), "--json",
            ],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
        experience_root = docs / "experience-design"
        artifact = experience_root / "artifacts" / "prototype.html"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("<main>draft</main>\n", encoding="utf-8")
        experience_compile.write_open_application_state(
            experience_root,
            {
                "application_action": "create",
                "expected_application": {"exists": False},
                "actions": [],
            },
            "sha256:" + "0" * 64,
            phase="draft",
        )
        return docs, experience_root

    def run_distribution_script(
        self, root: Path, package: Path, script: str, *args: str,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["python3", str(package / "scripts" / script), *map(str, args)],
            cwd=root, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    @staticmethod
    def add_vault_note_contract(path: Path, owner: str) -> None:
        text = path.read_text(encoding="utf-8")
        data, _body = experience_compile.fm(path)
        lines = text.splitlines()
        end = lines.index("---", 1)
        if "tags" not in data:
            note_type = str(data["type"]).replace("_", "-")
            tags = ["tags:", f"  - doc/{note_type}"]
            if data.get("status"):
                tags.append(
                    "  - status/"
                    + str(data["status"]).replace("_", "-")
                )
            lines[end:end] = tags
        updated = "\n".join(lines).rstrip() + "\n"
        if "<!-- sec: nav -->" not in updated:
            updated += (
                "\n## Navigation <!-- sec: nav -->\n\n"
                f"[[{owner}|Up]]\n"
            )
        path.write_text(updated, encoding="utf-8")

    def prepare_committed_manual_experience_inputs(
        self, root: Path, docs: Path, package: Path,
    ) -> tuple[dict, dict[str, str]]:
        from tools.tests.test_ba_compile import make_valid_space

        space = docs / "business-analysis" / "erp"
        make_valid_space(space)
        acceptance = (
            space / "domains/inventory/acceptance/"
            "goods-receipt-acceptance.md"
        )
        acceptance.write_text(
            acceptance.read_text(encoding="utf-8").replace(
                "[[business-analysis/erp/domains/inventory/processes/"
                "goods-receipt-process]]",
                "[[business-analysis/erp/domains/inventory/processes/"
                "goods-receipt-process|Goods Receipt]]",
            ),
            encoding="utf-8",
        )
        for note in sorted(space.rglob("*.md")):
            if "_generated" in note.parts:
                continue
            relative = note.relative_to(space).as_posix()
            if relative in {"space.md", "domains/inventory/domain.md"}:
                owner = "maps/business-analysis"
            elif relative.startswith("domains/inventory/"):
                owner = "business-analysis/erp/domains/inventory/domain"
            else:
                owner = "business-analysis/erp/space"
            self.add_vault_note_contract(note, owner)
        self.run_distribution_script(
            root, package, "ba_compile.py", "approve-package",
            "--space", str(space), "--vault-root", str(docs),
        )

        process_ref = (
            "business-analysis/erp/domains/inventory/processes/"
            "goods-receipt-process"
        )
        solution = docs / "solution-design"
        (solution / "components" / "inventory-api").mkdir(parents=True)
        (solution / "decisions").mkdir()
        (solution / "components" / "inventory-api" / "component.md").write_text(
            "---\n"
            "type: solution_component\n"
            "title: Inventory API component\n"
            "component_id: inventory-api\n"
            "component_class: application\n"
            "sourcing: build\n"
            "app_kind: backend-api\n"
            "code_path: workspace/apps/inventory-api\n"
            "owned_ba_refs:\n"
            f"  - {process_ref}\n"
            "technology_bindings:\n"
            "  - solution-design/decisions/runtime-decision\n"
            "  - solution-design/decisions/environment-decision\n"
            "data_store_disposition: not_applicable\n"
            "tags:\n"
            "  - doc/solution-component\n"
            "---\n\n# Inventory API component\n\n"
            "## Navigation <!-- sec: nav -->\n\n"
            "[[maps/solution-design|Solution Design]]\n",
            encoding="utf-8",
        )
        decisions = (
            (
                "runtime", "SD-001", "technology-selection",
                "python-fastapi", "python-fastapi",
            ),
            (
                "environment", "SD-002", "environment",
                "docker", "docker-compose",
            ),
        )
        for slug, identifier, kind, technology, skill in decisions:
            (solution / "decisions" / f"{slug}-decision.md").write_text(
                "---\n"
                "type: decision\n"
                f"title: {slug.title()} decision\n"
                "status: accepted\n"
                "aliases:\n"
                f"  - {identifier}\n"
                f"decision_kind: {kind}\n"
                "applies_to:\n"
                "  - inventory-api\n"
                f"selected_technology: {technology}\n"
                "method_skills:\n"
                f"  - {skill}\n"
                "tags:\n"
                "  - doc/decision\n"
                "  - status/accepted\n"
                "---\n\n"
                f"# {slug.title()} decision\n\n"
                "## Navigation <!-- sec: nav -->\n\n"
                "[[solution-design/landscape|Solution landscape]]\n",
                encoding="utf-8",
            )
        (solution / "landscape.md").write_text(
            "---\n"
            "type: landscape\n"
            "title: Inventory solution\n"
            "status: approved\n"
            "package_status: draft\n"
            "topology_selected: true\n"
            "derives_from:\n"
            '  - "[[business-analysis/erp/space|erp]]"\n'
            "tags:\n"
            "  - doc/landscape\n"
            "  - status/approved\n"
            "---\n\n"
            "# Inventory solution\n\n"
            "## Target\n\n"
            "SD-001 selects the application runtime.\n\n"
            "## Transition\n\n"
            "Introduce the inventory API.\n\n"
            "## Components\n\n"
            "| component | decision | verdict |\n"
            "|---|---|---|\n"
            "| inventory-api | "
            "[[solution-design/decisions/runtime-decision\\|SD-001]] and "
            "[[solution-design/decisions/environment-decision\\|SD-002]] "
            "| accepted |\n\n"
            "## Navigation <!-- sec: nav -->\n\n"
            "[[maps/solution-design|Solution Design]]\n",
            encoding="utf-8",
        )
        self.run_distribution_script(
            root, package, "vault_check.py", "render-decisions",
            "--vault", str(docs),
        )
        self.run_distribution_script(
            root, package, "landscape_check.py", "confirm-topology",
            "--tree", str(solution),
        )
        self.run_distribution_script(
            root, package, "landscape_check.py", "approve",
            "--tree", str(solution),
        )

        design = docs / "design-system"
        design.mkdir(exist_ok=True)
        (design / "MASTER.md").write_text(
            "---\n"
            "type: design_master\n"
            "title: Product design system\n"
            "status: draft\n"
            "revision: 1\n"
            "contract_version: 3\n"
            "derives_from:\n"
            '  - "[[business-analysis/erp/space|erp]]"\n'
            "constrained_by:\n"
            '  - "[[solution-design/landscape|Solution landscape]]"\n'
            "tags:\n"
            "  - doc/design-master\n"
            "  - status/draft\n"
            "---\n\n"
            "# Product design system\n\n"
            "## Product position\n\nPosition.\n\n"
            "## Brand and asset fidelity\n\nNo supplied identity asset.\n\n"
            "## Global rules\n\n### Catalog tokens\n\n"
            "<!-- catalog:tokens:start -->\n"
            "```css\n:root { --catalog-background: #fff; }\n```\n"
            "<!-- catalog:tokens:end -->\n\n"
            "## Component specs\n\nSpecs.\n\n"
            "## Style guidelines\n\nRules.\n\n"
            "## Anti-patterns\n\nAvoid.\n\n"
            "## Pre-delivery checklist\n\nCheck.\n\n"
            "## Navigation\n\n<!-- sec: nav -->\n\n"
            "[[maps/design-system|Design System]]\n",
            encoding="utf-8",
        )
        self.run_distribution_script(
            root, package, "design_system_compile.py", "init-catalog",
            "--root", str(design),
        )
        catalog = design / "artifacts" / "standalone.html"
        catalog.write_text(
            catalog.read_text(encoding="utf-8").replace(
                "AUTHOR_REQUIRED", "Issue 77 fixture",
            ),
            encoding="utf-8",
        )
        self.run_distribution_script(
            root, package, "design_system_compile.py", "approve",
            "--root", str(design),
        )

        (docs / "home.md").write_text(
            "---\n"
            "type: home\n"
            "title: Knowledge Base\n"
            "tags:\n"
            "  - doc/home\n"
            "---\n\n"
            "# Knowledge Base\n\n"
            "- [[maps/business-analysis|Business Analysis]]\n"
            "- [[maps/solution-design|Solution Design]]\n"
            "- [[maps/design-system|Design System]]\n"
            "- [[maps/experience-design|Experience Design]]\n",
            encoding="utf-8",
        )
        map_links = {
            "business-analysis": [
                note.relative_to(docs).with_suffix("").as_posix()
                for note in sorted(space.rglob("*.md"))
                if "_generated" not in note.parts
            ],
            "solution-design": [
                "solution-design/landscape",
                "solution-design/components/inventory-api/component",
                "solution-design/decisions/runtime-decision",
                "solution-design/decisions/environment-decision",
            ],
            "design-system": ["design-system/MASTER"],
            "experience-design": [
                "experience-design/experiences/checkout/experience",
            ],
        }
        for subtree, links in map_links.items():
            title = subtree.replace("-", " ").title()
            (docs / "maps" / f"{subtree}.md").write_text(
                "---\n"
                "type: moc\n"
                f"title: {title}\n"
                "tags:\n"
                "  - doc/moc\n"
                "---\n\n"
                f"# {title}\n\n"
                + "\n".join(
                    f"- [[{link}|{Path(link).name.replace('-', ' ').title()}]]"
                    for link in links
                )
                + "\n",
                encoding="utf-8",
            )
        self.run_distribution_script(
            root, package, "vault_check.py", "render-relations",
            "--vault", str(docs),
        )

        staged = subprocess.run(
            ["git", "add", "workspace"], cwd=root,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(staged.returncode, 0, staged.stdout + staged.stderr)
        committed = subprocess.run(
            [
                "git", "-c", "user.name=Issue 77 Fixture", "-c",
                "user.email=issue-77@example.invalid", "commit", "-m",
                "Prepare committed Experience inputs",
            ],
            cwd=root, capture_output=True, text=True, check=False,
        )
        self.assertEqual(
            committed.returncode, 0, committed.stdout + committed.stderr,
        )

        refs = {
            "business-analysis": "business-analysis/erp/space",
            "solution-design": "solution-design/landscape",
            "design-system": "design-system/MASTER",
        }
        for stage, reference in refs.items():
            verified = self.run_distribution_script(
                root, package, "stage_package.py", "verify",
                "--docs", str(docs), "--stage", stage, "--ref", reference,
                "--require-committed", "--strict-current", "--json",
            )
            receipt = json.loads(verified.stdout)["receipt"]
            self.assertTrue(receipt["committed"])
            self.assertEqual(receipt["verification_profile"], "strict-current")

        experience_root = docs / "experience-design"
        proposed = self.run_distribution_script(
            root, package, "experience_compile.py", "propose",
            "--root", str(experience_root), "--process-ref", process_ref,
            "--experience", "checkout", "--action", "create",
            "--origin-mode", "manual",
            "--ba-ref", refs["business-analysis"],
            "--solution-ref", refs["solution-design"],
            "--design-ref", refs["design-system"],
        )
        plan = json.loads(proposed.stdout)
        scope_plan = root / "issue-77-scope-plan.json"
        scope_plan.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        staged = subprocess.run(
            ["git", "add", str(scope_plan)], cwd=root,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(staged.returncode, 0, staged.stdout + staged.stderr)
        committed = subprocess.run(
            [
                "git", "-c", "user.name=Issue 77 Fixture", "-c",
                "user.email=issue-77@example.invalid", "commit", "-m",
                "Record Experience scope plan",
            ],
            cwd=root, capture_output=True, text=True, check=False,
        )
        self.assertEqual(
            committed.returncode, 0, committed.stdout + committed.stderr,
        )
        return plan, {
            **refs, "process": process_ref, "scope_plan": str(scope_plan),
        }

    def command_tokens(self, payload: dict) -> list[str]:
        parsed = self.hook.direct_shell_tokens(payload)
        self.assertIsNotNone(parsed)
        return parsed[0]

    def test_command_and_defensive_cmd_alias_normalize_identically(self):
        command = "python3 compiler.py check"
        command_payload = self.hook.normalize({
            "tool_name": "Bash", "tool_input": {"command": command},
        })
        cmd_payload = self.hook.normalize({
            "tool_name": "Bash", "tool_input": {"cmd": command},
        })
        identical = self.hook.normalize({
            "tool_name": "Bash",
            "tool_input": {"command": command, "cmd": command},
        })
        self.assertEqual(command_payload["tool_input"]["command"], command)
        self.assertEqual(cmd_payload["tool_input"]["command"], command)
        self.assertEqual(identical["tool_input"]["command"], command)
        self.assertNotIn("shell_command_error", identical)

    def test_conflicting_shell_fields_fail_closed(self):
        normalized = self.hook.normalize({
            "tool_name": "Bash",
            "tool_input": {"command": "safe", "cmd": "different"},
        })
        self.assertIn("disagree", normalized["shell_command_error"])
        self.assertIsNone(self.hook.direct_shell_tokens(normalized))

    def test_cmd_alias_and_command_share_guard_binding(self):
        command = "python3 compiler.py check"
        base = {"tool_name": "Bash", "cwd": str(ROOT), "session_id": "same"}
        command_payload = self.hook.normalize({
            **base, "tool_input": {"command": command},
        })
        cmd_payload = self.hook.normalize({
            **base, "tool_input": {"cmd": command},
        })
        self.assertEqual(
            self.hook.guard_binding(command_payload),
            self.hook.guard_binding(cmd_payload),
        )

    def test_powershell_uses_the_shell_guard_contract(self):
        normalized = self.hook.normalize({
            "tool_name": "PowerShell",
            "tool_input": {"command": "python compiler.py check"},
        })
        self.assertEqual(normalized["raw_tool_name"], "PowerShell")
        self.assertEqual(normalized["tool_name"], "Bash")
        self.assertEqual(
            normalized["tool_input"]["command"], "python compiler.py check",
        )
        with mock.patch.object(self.hook.sys, "platform", "win32"):
            self.assertIsNone(self.hook.direct_shell_tokens(normalized))

    def test_shell_snapshot_requires_a_stable_tool_call_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.project(root)
            payload = self.payload(root, "python3 unrelated.py")
            payload.pop("tool_use_id")
            result = self.run_hook("pre", payload)
            self.assertEqual(result.returncode, 2)
            self.assertIn("stable tool-call id", result.stderr)

    def test_writer_authorization_requires_the_exact_runtime_token(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.project(root)
            self.assertTrue(
                self.hook.trusted_python_command(sys.executable, root)
            )
            self.assertFalse(
                self.hook.trusted_python_command("python3", root)
            )

    @unittest.skipIf(os.name == "nt", "POSIX runtime alias contract")
    def test_bare_runtime_accepts_a_same_directory_trusted_alias(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "project"
            self.project(root)
            runtime = base / "runtime" / "python3.9"
            runtime.parent.mkdir(parents=True)
            runtime.write_bytes(b"runtime")
            runtime.chmod(0o755)
            alias = runtime.parent / "python3"
            try:
                alias.symlink_to(runtime.name)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            with mock.patch.object(
                self.hook.sys, "executable", str(runtime),
            ), mock.patch.object(
                self.hook.sys, "_base_executable", str(runtime), create=True,
            ), mock.patch.dict(self.hook.os.environ, {
                "PATH": str(runtime.parent),
            }, clear=False):
                self.assertTrue(
                    self.hook.trusted_python_command(
                        "python3", root, allow_bare=True,
                    )
                )

    def test_project_venv_alias_is_rejected_before_realpath_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.project(root)
            alias = root / ".venv" / "bin" / "python3"
            alias.parent.mkdir(parents=True)
            try:
                alias.symlink_to(sys.executable)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            self.assertEqual(alias.resolve(), Path(sys.executable).resolve())
            self.assertFalse(
                self.hook.trusted_python_command(str(alias), root)
            )
            with mock.patch.dict(os.environ, {
                "PATH": str(alias.parent) + os.pathsep + os.environ.get("PATH", ""),
            }):
                self.assertFalse(
                    self.hook.trusted_python_command("python3", root)
                )

    def test_exact_project_local_hook_runtime_is_accepted_but_alias_is_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.project(root)
            runtime = root / ".venv" / "bin" / "python3"
            runtime.parent.mkdir(parents=True)
            runtime.write_bytes(b"runtime")
            alias = root / "other" / "python3"
            alias.parent.mkdir()
            os.link(runtime, alias)
            with mock.patch.object(
                self.hook.sys, "executable", str(runtime),
            ), mock.patch.object(
                self.hook.sys, "_base_executable", str(runtime), create=True,
            ):
                self.assertTrue(
                    self.hook.trusted_python_command(str(runtime), root)
                )
                self.assertFalse(
                    self.hook.trusted_python_command(str(alias), root)
                )

    def test_project_hardlink_to_runtime_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "project"
            self.project(root)
            runtime = base / "runtime" / "python3"
            runtime.parent.mkdir()
            runtime.write_bytes(b"runtime")
            alias = root / "python3"
            os.link(runtime, alias)
            with mock.patch.object(self.hook.sys, "executable", str(runtime)), \
                    mock.patch.object(
                        self.hook.sys, "_base_executable", str(runtime), create=True,
                    ):
                self.assertFalse(
                    self.hook.trusted_python_command(str(alias), root)
                )

    def test_external_symlink_alias_to_runtime_is_rejected(self):
        with tempfile.TemporaryDirectory() as project_temporary, \
                tempfile.TemporaryDirectory() as alias_temporary:
            root = Path(project_temporary)
            self.project(root)
            alias = Path(alias_temporary) / "python3"
            try:
                alias.symlink_to(sys.executable)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            self.assertEqual(alias.resolve(), Path(sys.executable).resolve())
            self.assertFalse(
                self.hook.trusted_python_command(str(alias), root)
            )

    def test_packaged_writer_content_must_match_its_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            package = Path(temporary) / "package"
            scripts = package / "scripts"
            scripts.mkdir(parents=True)
            hook_path = scripts / "vault_hook.py"
            hook_path.write_text("hook\n", encoding="utf-8")
            vault_check_path = scripts / "vault_check.py"
            vault_check_path.write_text("check\n", encoding="utf-8")
            writer = scripts / "project_config.py"
            writer.write_text("writer\n", encoding="utf-8")
            digest = self.hook.hashlib.sha256(writer.read_bytes()).hexdigest()
            (package / ".agent-marketplace-package.json").write_text(
                json.dumps({"files": {"scripts/project_config.py": digest}}),
                encoding="utf-8",
            )
            with mock.patch.object(self.hook, "__file__", str(hook_path)), \
                    mock.patch.object(
                        self.hook.vault_check, "__file__", str(vault_check_path),
                    ):
                self.assertEqual(
                    self.hook._installed_script_path(
                        str(writer), package, "project_config.py",
                    ),
                    writer.resolve(),
                )
                (package / ".agent-marketplace-package.json").unlink()
                self.assertIsNone(self.hook._installed_script_path(
                    str(writer), package, "project_config.py",
                ))
                (package / ".agent-marketplace-package.json").write_text(
                    json.dumps({"files": {"scripts/project_config.py": digest}}),
                    encoding="utf-8",
                )
                writer.write_text("tampered\n", encoding="utf-8")
                self.assertIsNone(self.hook._installed_script_path(
                    str(writer), package, "project_config.py",
                ))

    def test_packaged_writer_directory_substitution_is_rejected(self):
        for name in (
            "project_config.py", "setup_project.py", "experience_compile.py",
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                package = Path(temporary) / "package"
                scripts = package / "scripts"
                scripts.mkdir(parents=True)
                hook_path = scripts / "vault_hook.py"
                hook_path.write_text("hook\n", encoding="utf-8")
                vault_check_path = scripts / "vault_check.py"
                vault_check_path.write_text("check\n", encoding="utf-8")
                writer = scripts / name
                writer.mkdir()
                (writer / "__main__.py").write_text(
                    "raise SystemExit(0)\n", encoding="utf-8",
                )
                (package / ".agent-marketplace-package.json").write_text(
                    json.dumps({"files": {f"scripts/{name}": "0" * 64}}),
                    encoding="utf-8",
                )
                with mock.patch.object(self.hook, "__file__", str(hook_path)), \
                        mock.patch.object(
                            self.hook.vault_check, "__file__",
                            str(vault_check_path),
                        ):
                    self.assertIsNone(self.hook._installed_script_path(
                        str(writer), package, name,
                    ))

    def test_project_script_alias_to_packaged_writer_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.project(root)
            alias = root / "project_config.py"
            try:
                alias.symlink_to(SCRIPTS / "project_config.py")
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            self.assertIsNone(self.hook._installed_script_path(
                str(alias), root, "project_config.py",
            ))

    def test_windows_python_executable_names_are_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.project(root)
            with mock.patch.object(self.hook.sys, "platform", "win32"), \
                    mock.patch.object(
                        self.hook, "_lexical_executable_path",
                        return_value=Path(sys.executable),
                    ):
                self.assertTrue(self.hook.trusted_python_command(
                    sys.executable, root,
                ))
                self.assertFalse(
                    self.hook.trusted_python_command("PYTHON3.EXE", root)
                )
                self.assertFalse(
                    self.hook.trusted_python_command("py.exe", root)
                )

    def test_windows_direct_command_parser_preserves_backslash_paths(self):
        argv = [
            r"C:\Program Files (x86)\Python\python.exe",
            r"C:\repo\Project @team #1,+\project_config.py", "set",
            "--config", r"C:\repo\Project @team #1,+\workspace\config.json",
        ]
        command = subprocess.list2cmdline(argv)
        with mock.patch.object(self.hook.sys, "platform", "win32"):
            parsed = self.hook.direct_shell_tokens({
                "tool_input": {"command": command}, "cwd": str(ROOT),
                "shell_family": "cmd",
            })
        self.assertIsNotNone(parsed)
        tokens, _cwd = parsed
        self.assertEqual(tokens, argv)

    def test_windows_writer_parser_requires_attested_cmd_family(self):
        command = subprocess.list2cmdline([
            r"C:\Python\python.exe", r"C:\repo\project_config.py", "set",
            "--config", r"C:\repo\workspace\config.json",
        ])
        with mock.patch.object(self.hook.sys, "platform", "win32"):
            self.assertIsNone(self.hook.direct_shell_tokens({
                "tool_input": {"command": command}, "cwd": str(ROOT),
            }))
            self.assertIsNone(self.hook.direct_shell_tokens({
                "tool_input": {"command": command}, "cwd": str(ROOT),
                "shell_family": "powershell",
            }))

    @unittest.skipIf(os.name == "nt", "POSIX shell-family contract")
    def test_explicit_unknown_posix_shell_is_guard_only(self):
        command = shlex.join([
            sys.executable, str(SCRIPTS / "project_config.py"), "set",
            "--config", str(ROOT / "workspace" / "config.json"),
        ])
        self.assertIsNone(self.hook.direct_shell_tokens({
            "tool_input": {"command": command}, "cwd": str(ROOT),
            "shell_family": "unknown",
        }))
        self.assertIsNotNone(self.hook.direct_shell_tokens({
            "tool_input": {"command": command}, "cwd": str(ROOT),
            "shell_family": "posix",
        }))

    def test_windows_shell_metacharacters_never_receive_writer_tokens(self):
        argv = [
            r"C:\Python\python.exe", r"C:\repo\project_config.py", "set",
            "--config", r"C:\repo\workspace\config.json",
            "--field", "output_language", "--value", "Turkish",
        ]
        base = subprocess.list2cmdline(argv)
        attacks = [
            base + r" & calc", base + r" \& calc",
            base.replace("Turkish", "'Turkish&calc'"),
            base.replace("Turkish", "%COMSPEC%"),
            base.replace("Turkish", "$(calc)"),
            "PYTHONDONTWRITEBYTECODE=1 " + base,
            base + "\r\ncalc",
        ]
        with mock.patch.object(self.hook.sys, "platform", "win32"):
            for command in attacks:
                with self.subTest(command=command):
                    payload = {
                        "tool_input": {"command": command},
                        "cwd": str(ROOT),
                    }
                    self.assertIsNone(self.hook.direct_shell_tokens(payload))
                    self.assertFalse(self.hook.sanctioned_config_writer(
                        payload, ROOT / "workspace" / "config.json",
                    ))

    @unittest.skipIf(os.name == "nt", "POSIX Apple path topology")
    def test_apple_launcher_requires_the_current_developer_runtime(self):
        root = Path("/tmp/project")
        runtime = Path("/Library/Developer/CommandLineTools/usr/bin/python3")
        completed = subprocess.CompletedProcess(
            ["/usr/bin/xcrun"], 0, str(runtime) + "\n", "",
        )
        with mock.patch.object(self.hook.sys, "platform", "darwin"), \
                mock.patch.object(self.hook.sys, "executable", str(runtime)), \
                mock.patch.object(self.hook.sys, "_base_executable", str(runtime), create=True), \
                mock.patch.object(self.hook.Path, "stat", return_value=SimpleNamespace(
                    st_uid=0, st_mode=0o100755,
                )), mock.patch.object(self.hook.subprocess, "run", return_value=completed), \
                mock.patch.dict(self.hook.os.environ, {}, clear=True):
            self.assertTrue(
                self.hook._apple_python_launcher_matches(
                    Path("/usr/bin/python3"), root,
                )
            )
            with mock.patch.dict(self.hook.os.environ, {
                "DEVELOPER_DIR": "/tmp/fake-developer",
            }):
                self.assertFalse(
                    self.hook._apple_python_launcher_matches(
                        Path("/usr/bin/python3"), root,
                    )
                )

    @unittest.skipIf(os.name == "nt", "POSIX Apple path topology")
    def test_apple_launcher_accepts_xcrun_alias_of_framework_runtime(self):
        with tempfile.TemporaryDirectory() as runtime_temporary, \
                tempfile.TemporaryDirectory() as project_temporary:
            runtime = Path(runtime_temporary) / "python3.9"
            runtime.write_bytes(b"runtime")
            selected = Path(runtime_temporary) / "python3"
            try:
                selected.symlink_to(runtime)
            except OSError as exc:
                self.skipTest(f"symlinks unavailable: {exc}")
            completed = subprocess.CompletedProcess(
                ["/usr/bin/xcrun"], 0, str(selected) + "\n", "",
            )
            with mock.patch.object(self.hook.sys, "platform", "darwin"), \
                    mock.patch.object(self.hook.sys, "executable", str(runtime)), \
                    mock.patch.object(
                        self.hook.sys, "_base_executable", str(runtime), create=True,
                    ), mock.patch.object(
                        self.hook, "_apple_developer_root",
                        return_value=Path("/Library/Developer/CommandLineTools"),
                    ), mock.patch.object(
                        self.hook.Path, "stat", return_value=SimpleNamespace(
                            st_uid=0, st_mode=0o100755,
                        ),
                    ), mock.patch.object(
                        self.hook.subprocess, "run", return_value=completed,
                    ), mock.patch.dict(self.hook.os.environ, {}, clear=True):
                self.assertTrue(self.hook._apple_python_launcher_matches(
                    Path("/usr/bin/python3"), Path(project_temporary),
                ))

    @unittest.skipUnless(sys.platform == "darwin", "Apple launcher topology")
    def test_system_macos_python3_launcher_is_accepted(self):
        if shutil.which("python3") != "/usr/bin/python3":
            if os.environ.get("AGENT_MARKETPLACE_REQUIRE_APPLE_PYTHON3") == "1":
                self.fail("CI did not place the Apple python3 launcher first")
            self.skipTest("python3 is not the Apple system launcher")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, config = self.project(root)
            (docs / "experience-design").mkdir()
            self.assertTrue(
                self.hook.trusted_python_command("/usr/bin/python3", root)
            )
            guard = (
                ROOT / "dist" / "claude" / "software-engineering-team"
                / "scripts" / "team_guard.py"
            )
            marker = subprocess.run(
                ["/usr/bin/python3", str(guard), "register"],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(marker.returncode, 0, marker.stdout + marker.stderr)
            context = json.loads(marker.stdout)["hookSpecificOutput"][
                "additionalContext"
            ]
            announced = next(
                line.split(": ", 1)[1] for line in context.splitlines()
                if line.startswith("AGENT_MARKETPLACE_PYTHON: ")
            )
            self.assertTrue(self.hook.trusted_python_command(announced, root))
            self.assertTrue(self.hook.sanctioned_application_writer(
                self.payload(
                    root, self.application_command(
                        docs, interpreter="/usr/bin/python3",
                    ),
                ),
                docs,
            ))
            command = self.config_command(
                config, interpreter="/usr/bin/python3",
            )
            payload = self.payload(root, command)
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            mutation = subprocess.run(
                self.command_tokens(payload), cwd=root,
                capture_output=True, text=True,
                check=False,
            )
            self.assertEqual(
                mutation.returncode, 0, mutation.stdout + mutation.stderr,
            )
            after = self.run_hook("post", payload)
            self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
            self.assertEqual(
                json.loads(config.read_text(encoding="utf-8"))["output_language"],
                "Turkish",
            )

    def test_all_writer_consumers_accept_cmd_alias(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, config = self.project(root)
            (docs / "experience-design").mkdir()
            config_payload = self.payload(
                root, self.config_command(config), field="cmd",
            )
            application_payload = self.attested_writer_payload(
                root, self.application_command(docs), field="cmd",
            )
            if os.name == "nt":
                config_payload["shell_family"] = "cmd"
            self.assertTrue(
                self.hook.sanctioned_config_writer(config_payload, config)
            )
            self.assertTrue(
                self.hook.sanctioned_application_writer(
                    application_payload, docs,
                )
            )

    def test_cmd_pre_and_command_post_preserve_official_config_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _docs, config = self.project(root)
            command = self.config_command(config)
            pre_payload = self.attested_writer_payload(
                root, command, field="cmd",
            )
            before = self.run_hook("pre", pre_payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            mutation = subprocess.run(
                self.command_tokens(pre_payload), cwd=root,
                capture_output=True, text=True,
                check=False,
            )
            self.assertEqual(
                mutation.returncode, 0, mutation.stdout + mutation.stderr,
            )
            post_payload = self.attested_writer_payload(
                root, command, field="command",
            )
            after = self.run_hook("post", post_payload)
            self.assertEqual(after.returncode, 0, after.stdout + after.stderr)
            self.assertEqual(
                json.loads(config.read_text(encoding="utf-8"))["output_language"],
                "Turkish",
            )

    def test_config_directory_replacement_is_removed_and_restored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _docs, config = self.project(root)
            original = config.read_text(encoding="utf-8")
            payload = {
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "config-directory-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            config.unlink()
            config.mkdir()
            (config / "child.json").write_text("tampered\n", encoding="utf-8")

            after = self.run_hook("post", payload)
            self.assertEqual(after.returncode, 2)
            self.assertTrue(config.is_file())
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_byte_identical_config_hardlink_is_broken_by_restore(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _docs, config = self.project(root)
            original = config.read_text(encoding="utf-8")
            external = root / "external-config.json"
            external.write_text(original, encoding="utf-8")
            payload = {
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "config-hardlink-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            config.unlink()
            os.link(external, config)
            self.assertTrue(os.path.samefile(external, config))

            after = self.run_hook("post", payload)
            self.assertEqual(after.returncode, 2)
            self.assertFalse(os.path.samefile(external, config))
            self.assertEqual(config.stat().st_nlink, 1)
            self.assertEqual(config.read_text(encoding="utf-8"), original)
            self.assertEqual(external.read_text(encoding="utf-8"), original)

    def test_failed_restore_retains_both_recovery_snapshots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _docs, config = self.project(root)
            payload = self.hook.normalize({
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "retained-recovery-event",
            })
            recovery = self.hook.recovery_path(payload)
            primary = self.hook.inventory_path(payload)
            try:
                self.assertEqual(self.hook.shell_snapshot(payload), 0)
                config.write_text("tampered\n", encoding="utf-8")
                with mock.patch.object(
                    self.hook, "restore_config", return_value="injected failure",
                ):
                    self.assertEqual(self.hook.shell_verify(payload), 2)
                self.assertTrue(recovery.is_file())
                self.assertTrue(primary.is_file())
            finally:
                self.hook.cleanup_guard_state(primary, recovery)

    def test_missing_recovery_capsule_revokes_primary_writer_grant(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, _config = self.project(root)
            (docs / "experience-design").mkdir()
            payload = self.hook.normalize({
                **self.payload(root, self.application_command(docs)),
                "tool_use_id": "missing-recovery-writer-event",
            })
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            recovery = self.hook.recovery_path(payload)
            recovery.unlink()
            generated = (
                docs / "experience-design" / "demo" / "_generated"
                / "state.json"
            )
            generated.parent.mkdir(parents=True)
            generated.write_text("unauthorized\n", encoding="utf-8")

            after = self.run_hook("post", payload)
            self.assertEqual(after.returncode, 2)
            self.assertIn("recovery capsule is missing", after.stderr)
            self.assertFalse(generated.exists())

    def test_equal_event_ids_in_different_projects_do_not_collide(self):
        with tempfile.TemporaryDirectory() as first, \
                tempfile.TemporaryDirectory() as second:
            roots = [Path(first), Path(second)]
            payloads = []
            for root in roots:
                self.project(root)
                payload = self.payload(root, "python3 unrelated.py")
                payloads.append(payload)
                before = self.run_hook("pre", payload)
                self.assertEqual(
                    before.returncode, 0, before.stdout + before.stderr,
                )
            for payload in payloads:
                after = self.run_hook("post", payload)
                self.assertEqual(
                    after.returncode, 0, after.stdout + after.stderr,
                )

    def test_conflicting_fields_are_denied_before_shell_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.project(root)
            payload = self.payload(root, "safe", field="command")
            payload["tool_input"]["cmd"] = "different"
            result = self.run_hook("pre", payload)
            self.assertEqual(result.returncode, 2)
            self.assertIn("ambiguous", result.stderr)

    def test_post_command_drift_revokes_writer_authorization_and_restores(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _docs, config = self.project(root)
            command = self.config_command(config)
            payload = {
                **self.payload(root, command),
                "tool_use_id": "stable-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            value = json.loads(config.read_text(encoding="utf-8"))
            value["output_language"] = "Turkish"
            config.write_text(
                json.dumps(value, indent=2) + "\n", encoding="utf-8",
            )
            drifted = {
                **payload,
                "tool_input": {"command": "python3 unrelated.py"},
            }
            after = self.run_hook("post", drifted)
            self.assertEqual(after.returncode, 2)
            self.assertIn("binding changed", after.stderr)
            self.assertEqual(
                json.loads(config.read_text(encoding="utf-8"))["output_language"],
                "English",
            )

    def test_post_command_drift_restores_compiler_owned_experience_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, _config = self.project(root)
            (docs / "experience-design").mkdir()
            payload = {
                **self.payload(root, self.application_command(docs)),
                "tool_use_id": "application-drift-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            generated = (
                docs / "experience-design" / "demo" / "_generated" / "out.json"
            )
            generated.parent.mkdir(parents=True)
            generated.write_text("tampered\n", encoding="utf-8")
            drifted = {
                **payload,
                "tool_input": {"command": "python3 unrelated.py"},
            }
            after = self.run_hook("post", drifted)
            self.assertEqual(after.returncode, 2)
            self.assertIn("original Experience tree was restored", after.stderr)
            self.assertFalse(generated.exists())

    def test_post_diagnostic_drift_cannot_bypass_existing_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, config = self.project(root)
            generated = (
                docs / "experience-design" / "demo" / "_generated" / "out.json"
            )
            payload = {
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "diagnostic-drift-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            value = json.loads(config.read_text(encoding="utf-8"))
            value["output_language"] = "Turkish"
            config.write_text(json.dumps(value) + "\n", encoding="utf-8")
            generated.parent.mkdir(parents=True)
            generated.write_text("tampered\n", encoding="utf-8")
            drifted = {
                **payload,
                "tool_input": {"command": "git status --porcelain"},
            }
            after = self.run_hook("post", drifted)
            self.assertEqual(after.returncode, 2)
            self.assertEqual(
                json.loads(config.read_text(encoding="utf-8"))["output_language"],
                "English",
            )
            self.assertFalse(generated.exists())

    def test_shell_diagnostics_are_snapshotted_and_cannot_mutate_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _docs, config = self.project(root)
            payload = {
                **self.payload(root, "git status --porcelain"),
                "tool_use_id": "diagnostic-snapshot-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            config.write_text("hijacked\n", encoding="utf-8")
            after = self.run_hook("post", payload)
            self.assertEqual(after.returncode, 2)
            self.assertEqual(
                json.loads(config.read_text(encoding="utf-8"))["output_language"],
                "English",
            )

    @unittest.skipIf(os.name == "nt", "POSIX FIFO contract")
    def test_config_fifo_is_restored_without_opening_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _docs, config = self.project(root)
            original = config.read_text(encoding="utf-8")
            payload = {
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "config-fifo-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            config.unlink()
            os.mkfifo(config)
            after = self.run_hook("post", payload)
            self.assertEqual(after.returncode, 2)
            self.assertTrue(config.is_file())
            self.assertEqual(config.read_text(encoding="utf-8"), original)

    @unittest.skipIf(os.name == "nt", "POSIX FIFO contract")
    def test_experience_fifo_is_rejected_before_shell_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, _config = self.project(root)
            special = docs / "experience-design" / "opaque-pipe"
            special.parent.mkdir(parents=True)
            os.mkfifo(special)
            payload = {
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "experience-fifo-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 2)
            self.assertIn("not a regular file", before.stderr)

    def test_post_without_event_id_recovers_one_unambiguous_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, config = self.project(root)
            generated = (
                docs / "experience-design" / "demo" / "_generated" / "out.json"
            )
            payload = {
                **self.payload(root, "python3 unrelated.py"),
                "session_id": "missing-post-event-session",
                "tool_use_id": "present-only-in-pre",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            value = json.loads(config.read_text(encoding="utf-8"))
            value["output_language"] = "Turkish"
            config.write_text(json.dumps(value) + "\n", encoding="utf-8")
            generated.parent.mkdir(parents=True)
            generated.write_text("tampered\n", encoding="utf-8")
            post_payload = dict(payload)
            post_payload.pop("tool_use_id")
            after = self.run_hook("post", post_payload)
            self.assertEqual(after.returncode, 2)
            self.assertEqual(
                json.loads(config.read_text(encoding="utf-8"))["output_language"],
                "English",
            )
            self.assertFalse(generated.exists())

    def test_workspace_symlink_swap_restores_local_protected_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, config = self.project(root)
            generated = (
                docs / "experience-design" / "demo" / "_generated" / "out.json"
            )
            generated.parent.mkdir(parents=True)
            generated.write_text("before\n", encoding="utf-8")
            payload = {
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "workspace-alias-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            moved = root / "moved-workspace"
            (root / "workspace").rename(moved)
            self.create_directory_alias(root / "workspace", moved)
            if os.name == "nt":
                self.assertFalse((root / "workspace").is_symlink())
                self.assertTrue(self.hook.path_is_alias(root / "workspace"))
            (moved / "docs" / "experience-design" / "demo"
             / "_generated" / "out.json").write_text(
                 "tampered\n", encoding="utf-8",
             )
            after = self.run_hook("post", payload)
            self.assertEqual(after.returncode, 2)
            self.assertFalse(self.hook.path_is_alias(root / "workspace"))
            self.assertEqual(
                json.loads(config.read_text(encoding="utf-8"))["output_language"],
                "English",
            )
            self.assertEqual(generated.read_text(encoding="utf-8"), "before\n")

    def test_nested_experience_alias_is_removed_without_touching_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, _config = self.project(root)
            generated = (
                docs / "experience-design" / "demo" / "_generated" / "out.json"
            )
            generated.parent.mkdir(parents=True)
            generated.write_text("before\n", encoding="utf-8")
            payload = {
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "nested-alias-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)

            local_demo = docs / "experience-design" / "demo"
            external_demo = root / "external-demo"
            local_demo.rename(external_demo)
            self.create_directory_alias(local_demo, external_demo)
            if os.name == "nt":
                self.assertFalse(local_demo.is_symlink())
                self.assertTrue(self.hook.path_is_alias(local_demo))
            external_generated = external_demo / "_generated" / "out.json"
            external_generated.write_text("external-tamper\n", encoding="utf-8")

            after = self.run_hook("post", payload)
            self.assertEqual(after.returncode, 2)
            self.assertIn("unsafe path topology", after.stderr)
            self.assertFalse(self.hook.path_is_alias(local_demo))
            self.assertEqual(generated.read_text(encoding="utf-8"), "before\n")
            self.assertEqual(
                external_generated.read_text(encoding="utf-8"),
                "external-tamper\n",
            )

    def test_empty_compiler_directory_is_detected_and_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, _config = self.project(root)
            (docs / "experience-design").mkdir()
            payload = {
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "empty-machine-directory-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            generated = docs / "experience-design" / "demo" / "_generated"
            generated.mkdir(parents=True)

            after = self.run_hook("post", payload)
            self.assertEqual(after.returncode, 2)
            self.assertFalse(generated.exists())

    def test_machine_restore_preserves_author_owned_artifact_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, _config = self.project(root)
            artifact = docs / "experience-design" / "artifacts" / "prototype.bin"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"before")
            payload = {
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "artifact-preservation-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            artifact.write_bytes(b"author-change")
            generated = docs / "experience-design" / "demo" / "_generated"
            generated.mkdir(parents=True)
            (generated / "state.json").write_text("tampered\n", encoding="utf-8")

            after = self.run_hook("post", payload)
            self.assertEqual(after.returncode, 2)
            self.assertEqual(artifact.read_bytes(), b"author-change")
            self.assertFalse(generated.exists())

    @unittest.skipIf(os.name == "nt", "POSIX directory permissions")
    def test_restore_prunes_unreadable_author_owned_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, _config = self.project(root)
            artifacts = docs / "experience-design" / "artifacts"
            artifact = artifacts / "dependencies" / "opaque.bin"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"opaque")
            os.chmod(artifacts, 0)
            try:
                payload = {
                    **self.payload(root, "python3 unrelated.py"),
                    "tool_use_id": "unreadable-artifact-event",
                }
                before = self.run_hook("pre", payload)
                self.assertEqual(
                    before.returncode, 0, before.stdout + before.stderr,
                )
                generated = (
                    docs / "experience-design" / "demo" / "_generated"
                    / "state.json"
                )
                generated.parent.mkdir(parents=True)
                generated.write_text("tampered\n", encoding="utf-8")
                after = self.run_hook("post", payload)
                self.assertEqual(after.returncode, 2)
                self.assertFalse(generated.exists())
                self.assertEqual(artifacts.stat().st_mode & 0o777, 0)
            finally:
                os.chmod(artifacts, 0o700)
            self.assertEqual(artifact.read_bytes(), b"opaque")

    def test_noncanonical_artifact_case_is_rejected_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, _config = self.project(root)
            artifact = (
                docs / "experience-design" / "Artifacts" / "opaque.bin"
            )
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"author-owned")
            payload = {
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "artifact-case-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 2)
            self.assertIn("artifact root spelling is non-canonical", before.stderr)
            self.assertEqual(artifact.read_bytes(), b"author-owned")

    def test_restore_never_descends_into_case_changed_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, _config = self.project(root)
            canonical = docs / "experience-design" / "artifacts"
            artifact = canonical / "opaque.bin"
            artifact.parent.mkdir(parents=True)
            artifact.write_bytes(b"before")
            payload = self.hook.normalize({
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "artifact-case-restore-event",
            })
            recovery = self.hook.recovery_path(payload)
            primary = self.hook.inventory_path(payload)
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            variant = canonical.with_name("Artifacts")
            canonical.rename(variant)
            changed_artifact = variant / "opaque.bin"
            changed_artifact.write_bytes(b"author-change")
            generated = (
                docs / "experience-design" / "demo" / "_generated"
                / "state.json"
            )
            generated.parent.mkdir(parents=True)
            generated.write_text("tampered\n", encoding="utf-8")
            try:
                after = self.run_hook("post", payload)
                self.assertEqual(after.returncode, 2)
                self.assertIn("restore failed", after.stderr)
                self.assertEqual(changed_artifact.read_bytes(), b"author-change")
                self.assertTrue(recovery.exists())
                self.assertTrue(primary.exists())
            finally:
                self.hook.cleanup_guard_state(primary, recovery)

    @unittest.skipIf(os.name == "nt", "POSIX FIFO contract")
    def test_artifact_root_fifo_is_rejected_without_opening_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, _config = self.project(root)
            artifact_root = docs / "experience-design" / "artifacts"
            artifact_root.parent.mkdir(parents=True)
            os.mkfifo(artifact_root)
            payload = {
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "artifact-fifo-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 2)
            self.assertIn("artifact root is not a directory", before.stderr)

    @unittest.skipIf(os.name == "nt", "POSIX directory permissions")
    def test_restore_writes_children_before_reapplying_readonly_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, _config = self.project(root)
            generated = (
                docs / "experience-design" / "demo" / "_generated"
                / "state.json"
            )
            generated.parent.mkdir(parents=True)
            generated.write_text("before\n", encoding="utf-8")
            demo = docs / "experience-design" / "demo"
            os.chmod(demo, 0o555)
            payload = {
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "readonly-restore-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            os.chmod(demo, 0o755)
            generated.write_text("tampered\n", encoding="utf-8")
            os.chmod(demo, 0o555)

            after = self.run_hook("post", payload)
            self.assertEqual(after.returncode, 2)
            self.assertEqual(generated.read_text(encoding="utf-8"), "before\n")
            self.assertEqual(demo.stat().st_mode & 0o777, 0o555)

    @unittest.skipIf(os.name == "nt", "POSIX config permissions")
    def test_config_mode_change_is_detected_and_restored(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _docs, config = self.project(root)
            os.chmod(config, 0o640)
            payload = {
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "config-mode-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            os.chmod(config, 0o600)

            after = self.run_hook("post", payload)
            self.assertEqual(after.returncode, 2)
            self.assertEqual(config.stat().st_mode & 0o777, 0o640)

    @unittest.skipUnless(os.name == "nt", "native Windows junction contract")
    def test_dangling_windows_junction_is_removed_before_restore(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, _config = self.project(root)
            generated = (
                docs / "experience-design" / "demo" / "_generated" / "out.json"
            )
            generated.parent.mkdir(parents=True)
            generated.write_text("before\n", encoding="utf-8")
            payload = {
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "dangling-junction-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            local_demo = docs / "experience-design" / "demo"
            external_demo = root / "external-demo"
            local_demo.rename(external_demo)
            self.create_directory_alias(local_demo, external_demo)
            self.assertTrue(self.hook.path_is_alias(local_demo))
            shutil.rmtree(external_demo)

            after = self.run_hook("post", payload)
            self.assertEqual(after.returncode, 2)
            self.assertFalse(self.hook.path_is_alias(local_demo))
            self.assertEqual(generated.read_text(encoding="utf-8"), "before\n")

    @unittest.skipUnless(os.name == "nt", "native Windows READONLY contract")
    def test_windows_readonly_files_are_cleared_before_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, config = self.project(root)
            generated = (
                docs / "experience-design" / "demo" / "_generated"
                / "state.json"
            )
            generated.parent.mkdir(parents=True)
            generated.write_text("before\n", encoding="utf-8")
            payload = {
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "windows-readonly-recovery-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)

            config_value = json.loads(config.read_text(encoding="utf-8"))
            config_value["output_language"] = "Turkish"
            config.write_text(
                json.dumps(config_value, indent=2) + "\n", encoding="utf-8",
            )
            generated.write_text("tampered\n", encoding="utf-8")
            unexpected = generated.parent / "unexpected.json"
            unexpected.write_text("tampered\n", encoding="utf-8")
            for path in (config, generated, unexpected):
                os.chmod(path, stat.S_IREAD)

            after = self.run_hook("post", payload)
            self.assertEqual(after.returncode, 2, after.stdout + after.stderr)
            self.assertEqual(
                json.loads(config.read_text(encoding="utf-8"))[
                    "output_language"
                ],
                "English",
            )
            self.assertEqual(generated.read_text(encoding="utf-8"), "before\n")
            self.assertFalse(unexpected.exists())

    @unittest.skipUnless(os.name == "nt", "native Windows READONLY contract")
    def test_windows_readonly_parent_file_is_replaced_during_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, _config = self.project(root)
            generated = (
                docs / "experience-design" / "demo" / "_generated"
                / "state.json"
            )
            generated.parent.mkdir(parents=True)
            generated.write_text("before\n", encoding="utf-8")
            payload = {
                **self.payload(root, "python3 unrelated.py"),
                "tool_use_id": "windows-readonly-parent-recovery-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)

            shutil.rmtree(docs)
            docs.write_text("not a directory\n", encoding="utf-8")
            os.chmod(docs, stat.S_IREAD)

            after = self.run_hook("post", payload)
            self.assertEqual(after.returncode, 2, after.stdout + after.stderr)
            self.assertTrue(docs.is_dir())
            self.assertEqual(generated.read_text(encoding="utf-8"), "before\n")

    def test_recovery_uses_pre_command_project_after_workspace_deletion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, config = self.project(root)
            generated = (
                docs / "experience-design" / "demo" / "_generated" / "out.json"
            )
            generated.parent.mkdir(parents=True)
            generated.write_text("before\n", encoding="utf-8")
            nested = root / "nested"
            nested.mkdir()
            payload = {
                **self.payload(nested, "python3 unrelated.py"),
                "tool_use_id": "project-deletion-event",
            }
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            shutil.rmtree(root / "workspace")
            after = self.run_hook("post", payload)
            self.assertEqual(after.returncode, 2)
            self.assertEqual(
                json.loads(config.read_text(encoding="utf-8"))["output_language"],
                "English",
            )
            self.assertEqual(generated.read_text(encoding="utf-8"), "before\n")

    def test_composed_host_hooks_enforce_writer_support_boundaries(self):
        for host in ("claude", "codex", "opencode"):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                _docs, config = self.project(root)
                package = ROOT / "dist" / host / "software-engineering-team"
                hook = package / "scripts" / "vault_hook.py"
                argv = [
                    sys.executable,
                    str(package / "scripts" / "project_config.py"), "set",
                    "--config", str(config), "--field", "output_language",
                    "--value", "Turkish",
                ]
                command = (
                    subprocess.list2cmdline(argv)
                    if os.name == "nt" else shlex.join(argv)
                )
                pre_payload = self.payload(root, command, field="cmd")
                if os.name == "nt" and host == "opencode":
                    pre_payload["shell_family"] = "cmd"
                before = self.run_composed_hook(hook, "pre", pre_payload)
                self.assertEqual(
                    before.returncode, 0, before.stdout + before.stderr,
                )
                mutation = subprocess.run(
                    argv, cwd=root,
                    capture_output=True, text=True,
                    check=False,
                )
                self.assertEqual(
                    mutation.returncode, 0, mutation.stdout + mutation.stderr,
                )
                post_payload = self.payload(root, command, field="command")
                if os.name == "nt" and host == "opencode":
                    post_payload["shell_family"] = "cmd"
                after = self.run_composed_hook(hook, "post", post_payload)
                expected = 0 if os.name != "nt" or host == "opencode" else 2
                self.assertEqual(
                    after.returncode, expected, after.stdout + after.stderr,
                )
                self.assertEqual(
                    json.loads(config.read_text(encoding="utf-8"))[
                        "output_language"
                    ],
                    "Turkish" if expected == 0 else "English",
                )

    def test_composed_hosts_enforce_experience_support_boundaries(self):
        for host in ("claude", "codex", "opencode"):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                docs, _config = self.project(root)
                repository = subprocess.run(
                    ["git", "init", str(root)],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(
                    repository.returncode, 0,
                    repository.stdout + repository.stderr,
                )
                package = ROOT / "dist" / host / "software-engineering-team"
                setup = subprocess.run(
                    [
                        sys.executable,
                        str(package / "scripts" / "setup_project.py"),
                        "apply", "--project-root", str(root), "--json",
                    ],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(
                    setup.returncode, 0, setup.stdout + setup.stderr,
                )
                (docs / "experience-design").mkdir(exist_ok=True)
                hook = package / "scripts" / "vault_hook.py"
                command = self.application_command(
                    docs, interpreter=sys.executable,
                ).replace(
                    str(SCRIPTS / "experience_compile.py"),
                    str(package / "scripts" / "experience_compile.py"),
                )
                pre_payload = self.payload(root, command, field="cmd")
                if os.name == "nt" and host == "opencode":
                    pre_payload["shell_family"] = "cmd"
                before = self.run_composed_hook(hook, "pre", pre_payload)
                self.assertEqual(
                    before.returncode, 0, before.stdout + before.stderr,
                )
                generated = (
                    docs / "experience-design" / "demo" / "_generated"
                )
                generated.mkdir(parents=True)
                post_payload = self.payload(root, command, field="command")
                if os.name == "nt" and host == "opencode":
                    post_payload["shell_family"] = "cmd"
                after = self.run_composed_hook(hook, "post", post_payload)
                expected = 0 if os.name != "nt" or host == "opencode" else 2
                self.assertEqual(
                    after.returncode, expected, after.stdout + after.stderr,
                )
                self.assertEqual(generated.is_dir(), expected == 0)

    @unittest.skipUnless(sys.platform == "darwin", "issue #77 is macOS")
    def test_issue_77_bare_python_cmd_preserves_attested_codex_result(self):
        if shutil.which("python3") is None:
            self.skipTest("python3 is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = ROOT / "dist" / "codex" / "software-engineering-team"
            _docs, experience_root = self.application_draft(root, package)

            hook = package / "scripts" / "vault_hook.py"
            command = shlex.join([
                "python3",
                str(package / "scripts" / "experience_compile.py"),
                "render-application", "--root", str(experience_root),
            ])
            pre_payload = self.payload(root, command, field="cmd")
            before = self.run_composed_hook(hook, "pre", pre_payload)
            self.assertEqual(
                before.returncode, 0, before.stdout + before.stderr,
            )
            mutation = subprocess.run(
                [
                    "python3",
                    str(package / "scripts" / "experience_compile.py"),
                    "render-application", "--root", str(experience_root),
                ],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(
                mutation.returncode, 0, mutation.stdout + mutation.stderr,
            )

            post_payload = self.payload(root, command, field="command")
            after = self.run_composed_hook(hook, "post", post_payload)
            self.assertEqual(
                after.returncode, 0, after.stdout + after.stderr,
            )
            self.assertTrue(
                (experience_root / "_generated/application-registry.json").is_file()
            )

    @unittest.skipUnless(sys.platform == "darwin", "bare Python fallback")
    def test_bare_render_cannot_publish_a_different_valid_transition(self):
        if shutil.which("python3") is None:
            self.skipTest("python3 is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = ROOT / "dist" / "codex" / "software-engineering-team"
            _docs, experience_root = self.application_draft(root, package)
            hook = package / "scripts" / "vault_hook.py"
            command = shlex.join([
                "python3",
                str(package / "scripts" / "experience_compile.py"),
                "render-application", "--root", str(experience_root),
            ])
            pre_payload = self.payload(root, command, field="cmd")
            before = self.run_composed_hook(hook, "pre", pre_payload)
            self.assertEqual(
                before.returncode, 0, before.stdout + before.stderr,
            )
            open_state = (
                experience_root / "_generated/open-application-revision.json"
            )
            original_open_state = open_state.read_bytes()
            registry, findings = experience_application_check.compile_application(
                experience_root,
            )
            self.assertEqual(findings, [])
            experience_application_check.write_registry_and_ledger(
                experience_root, registry,
            )
            open_state.unlink()

            after = self.run_composed_hook(
                hook, "post",
                self.payload(root, command, field="command"),
            )
            self.assertEqual(after.returncode, 2)
            self.assertFalse(
                (experience_root / "_ledger/application-revisions.json").exists()
            )
            self.assertEqual(open_state.read_bytes(), original_open_state)

    @unittest.skipUnless(sys.platform == "darwin", "bare Python fallback")
    def test_bare_render_with_forged_registry_is_restored(self):
        if shutil.which("python3") is None:
            self.skipTest("python3 is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = ROOT / "dist" / "codex" / "software-engineering-team"
            _docs, experience_root = self.application_draft(root, package)
            hook = package / "scripts" / "vault_hook.py"
            command = shlex.join([
                "python3",
                str(package / "scripts" / "experience_compile.py"),
                "render-application", "--root", str(experience_root),
            ])
            before = self.run_composed_hook(
                hook, "pre", self.payload(root, command, field="cmd"),
            )
            self.assertEqual(
                before.returncode, 0, before.stdout + before.stderr,
            )
            open_state = (
                experience_root / "_generated/open-application-revision.json"
            )
            original_open_state = open_state.read_bytes()
            registry = (
                experience_root / "_generated/application-registry.json"
            )
            forged, findings = experience_application_check.compile_application(
                experience_root,
            )
            self.assertEqual(findings, [])
            forged["application_revision"] = True
            registry.write_bytes(experience_application_check.canonical(forged))

            after = self.run_composed_hook(
                hook, "post",
                self.payload(root, command, field="command"),
            )
            self.assertEqual(after.returncode, 2)
            self.assertFalse(registry.exists())
            self.assertEqual(open_state.read_bytes(), original_open_state)

    @unittest.skipUnless(sys.platform == "darwin", "issue #77 is macOS")
    def test_issue_77_bare_init_has_an_exact_attested_delta(self):
        if shutil.which("python3") is None:
            self.skipTest("python3 is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, _config = self.project(root)
            experience_root = docs / "experience-design"
            experience_root.mkdir()
            proposal_hash = "sha256:" + "1" * 64
            command = shlex.join([
                "python3", str(SCRIPTS / "experience_compile.py"), "init",
                "--root", str(experience_root),
                "--experience", "checkout",
                "--origin-mode", "manual",
                "--primary-process-ref",
                "business-analysis/commerce/processes/checkout",
                "--scope-plan", str(docs / "scope-plan.json"),
                "--proposal-hash", proposal_hash,
                "--ba-ref", "business-analysis/commerce/space@sha256:ba",
                "--solution-ref", "solution-design/landscape@sha256:solution",
                "--design-ref", "design-system/MASTER@sha256:design",
            ])
            payload = self.payload(root, command, field="cmd")
            self.assertFalse(
                self.hook.sanctioned_application_writer(payload, docs)
            )
            self.assertTrue(self.hook.sanctioned_application_writer(
                payload, docs, allow_bare_runtime=True,
            ))
            package_relative = "experience-design/experiences/checkout"
            changed = [
                "experience-design/_generated",
                "experience-design/_generated/open-application-revision.json",
                "experience-design/experiences",
                package_relative,
                f"{package_relative}/experience.md",
                f"{package_relative}/journeys",
                f"{package_relative}/flows",
                f"{package_relative}/screens",
                f"{package_relative}/states",
                f"{package_relative}/transitions",
                f"{package_relative}/artifacts",
                f"{package_relative}/_generated",
                f"{package_relative}/_generated/open-revision.json",
                f"{package_relative}/_ledger",
            ]
            package_state = {
                "action": "create",
                "source_experience": "checkout",
                "target_experience": "checkout",
                "proposal_hash": proposal_hash,
            }
            primary_process = (
                "business-analysis/commerce/processes/checkout"
            )
            receipts = [
                {
                    "stage": stage,
                    "result_ref": reference,
                    "package_hash": "sha256:" + character * 64,
                }
                for stage, reference, character in (
                    (
                        "business-analysis",
                        "business-analysis/commerce/space", "a",
                    ),
                    (
                        "solution-design",
                        "solution-design/landscape", "b",
                    ),
                    (
                        "design-system", "design-system/MASTER", "c",
                    ),
                )
            ]
            plan = {"origin_mode": "manual", "actions": [package_state]}
            package_fields = {
                "experience_id": "checkout",
                "origin_mode": "manual",
                "status": "draft",
                "revision": 1,
                "title": "Checkout Experience",
                "primary_process_ref": primary_process,
                "input_bindings": experience_compile.binding_rows(receipts),
            }
            with mock.patch.object(
                self.hook.experience_compile, "fields",
                return_value=package_fields,
            ), mock.patch.object(
                self.hook.experience_compile, "load_scope_plan",
                return_value=plan,
            ), mock.patch.object(
                self.hook.experience_compile, "verify_scope_inputs",
                return_value=[],
            ), mock.patch.object(
                self.hook.experience_compile, "selected_inputs",
                return_value=(receipts, [], {}),
            ), mock.patch.object(
                self.hook.experience_compile, "process_from_inputs",
                return_value=(primary_process, []),
            ), mock.patch.object(
                self.hook.experience_compile, "action_for_plan",
                return_value=package_state,
            ), mock.patch.object(
                self.hook.experience_compile, "validate_open_revision",
            ), mock.patch.object(
                self.hook.experience_compile,
                "validate_open_application_state",
            ), mock.patch.object(
                self.hook.experience_application_check,
                "compile_application", return_value=({}, []),
            ):
                self.assertTrue(self.hook.valid_application_writer_result(
                    payload, docs, changed,
                ))
                self.assertFalse(self.hook.valid_application_writer_result(
                    payload, docs,
                    changed + [
                        "experience-design/_ledger/application-revisions.json"
                    ],
                ))
                package_fields["title"] = "Another Experience"
                self.assertFalse(self.hook.valid_application_writer_result(
                    payload, docs, changed,
                ))

            other = self.payload(
                root,
                self.application_command(docs, interpreter="python3"),
                field="cmd",
            )
            self.assertFalse(self.hook.sanctioned_application_writer(
                other, docs, allow_bare_runtime=True,
            ))

    @unittest.skipUnless(sys.platform == "darwin", "issue #77 is macOS")
    def test_issue_77_bare_init_preserves_real_codex_draft(self):
        if shutil.which("python3") is None:
            self.skipTest("python3 is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "Issue 77 (bare init)"
            root.mkdir()
            repository = subprocess.run(
                ["git", "init", str(root)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(
                repository.returncode, 0,
                repository.stdout + repository.stderr,
            )
            package = ROOT / "dist" / "codex" / "software-engineering-team"
            setup = subprocess.run(
                [
                    "python3", str(package / "scripts" / "setup_project.py"),
                    "apply", "--project-root", str(root), "--json",
                ],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(setup.returncode, 0, setup.stdout + setup.stderr)
            docs = root / "workspace" / "docs"

            plan, refs = self.prepare_committed_manual_experience_inputs(
                root, docs, package,
            )
            experience_root = docs / "experience-design"
            scope_plan = Path(refs["scope_plan"])
            argv = [
                "python3", str(package / "scripts" / "experience_compile.py"),
                "init", "--root", str(experience_root),
                "--experience", "checkout", "--origin-mode", "manual",
                "--primary-process-ref", refs["process"],
                "--scope-plan", str(scope_plan),
                "--proposal-hash", plan["proposal_hash"],
                "--title", "Checkout Operations",
                "--ba-ref", refs["business-analysis"],
                "--solution-ref", refs["solution-design"],
                "--design-ref", refs["design-system"],
            ]
            command = shlex.join(argv)
            hook = package / "scripts" / "vault_hook.py"
            before = self.run_composed_hook(
                hook, "pre", self.payload(root, command, field="cmd"),
            )
            self.assertEqual(
                before.returncode, 0, before.stdout + before.stderr,
            )
            mutation = subprocess.run(
                argv, cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(
                mutation.returncode, 0, mutation.stdout + mutation.stderr,
            )
            after = self.run_composed_hook(
                hook, "post", self.payload(root, command, field="command"),
            )
            self.assertEqual(
                after.returncode, 0, after.stdout + after.stderr,
            )

            checkout = experience_root / "experiences" / "checkout"
            fields = experience_compile.fields(checkout)
            self.assertEqual(fields["status"], "draft")
            self.assertEqual(fields["title"], "Checkout Operations")
            self.assertEqual(fields["primary_process_ref"], refs["process"])
            self.assertEqual(
                experience_compile.read_open_revision(checkout)[
                    "proposal_hash"
                ],
                plan["proposal_hash"],
            )
            application_state = experience_compile.read_open_application_state(
                experience_root,
            )
            self.assertEqual(application_state["phase"], "draft")
            self.assertEqual(
                application_state["proposal_hash"], plan["proposal_hash"],
            )
            self.assertTrue((checkout / "experience.md").is_file())

    @unittest.skipUnless(sys.platform == "darwin", "bare Python fallback")
    def test_bare_python_candidate_with_invalid_result_is_restored(self):
        if shutil.which("python3") is None:
            self.skipTest("python3 is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            docs, _config = self.project(root)
            (docs / "experience-design").mkdir()
            command = shlex.join([
                "python3", str(SCRIPTS / "experience_compile.py"),
                "render-application", "--root",
                str(docs / "experience-design"),
            ])
            payload = self.payload(root, command, field="cmd")
            before = self.run_hook("pre", payload)
            self.assertEqual(before.returncode, 0, before.stdout + before.stderr)
            generated = docs / "experience-design" / "demo" / "_generated"
            generated.mkdir(parents=True)

            after = self.run_hook(
                "post", self.payload(root, command, field="command"),
            )
            self.assertEqual(after.returncode, 2)
            self.assertIn("left compiler validation red", after.stderr)
            self.assertFalse(generated.exists())


if __name__ == "__main__":
    unittest.main()
