"""Deterministic contracts behind the real-host installation smoke."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import build_distributions
from tools import smoke_plugin_installs as smoke


ROOT = Path(__file__).resolve().parents[2]


class HostSmokeContracts(unittest.TestCase):
    def test_checkout_is_channel_closed_and_contains_one_team(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = smoke.checkout_marketplace(ROOT, Path(temporary) / "catalog")
            self.assertTrue((target / ".claude-plugin/marketplace.json").is_file())
            self.assertTrue((target / ".agents/plugins/marketplace.json").is_file())
            for host in build_distributions.HOSTS:
                packages = sorted(path.name for path in (target / "dist" / host).iterdir()
                                  if path.is_dir())
                self.assertEqual(packages, [smoke.TEAM])

    def test_native_packages_and_opencode_projection_execute_fresh_setup(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary) / "project"
                smoke.init_project(project, os.environ.copy())
                smoke.exercise_package(
                    ROOT / "dist" / host / smoke.TEAM,
                    project,
                    os.environ.copy(),
                )
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            with mock.patch.object(
                smoke, "exercise_application_resources"
            ) as exercised:
                smoke.exercise_project_projection(
                    ROOT / "dist" / "opencode" / smoke.TEAM,
                    project,
                    os.environ.copy(),
                    "scripts/project_opencode.py",
                    ".opencode",
                )
            private = project / ".opencode/agentrof/agent-marketplace"
            installation = json.loads(
                (private / "installation.json").read_text(encoding="utf-8")
            )
            expected = (
                private / "packages" / installation["active_build_key"] / smoke.TEAM
            ).resolve()
            exercised.assert_called_once()
            self.assertEqual(exercised.call_args.args[0], expected)

    def test_installed_package_smoke_requires_product_and_delivery_entrypoints(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "package"
            root.mkdir()
            for relative in (
                "scripts/setup_project.py", "scripts/setup_check.py",
                "scripts/requirement_route.py", "scripts/backlog_compile.py",
                "scripts/stage_package.py", "scripts/ba_compile.py",
                "scripts/landscape_check.py", "scripts/design_system_compile.py",
                "scripts/experience_compile.py",
                "scripts/experience_application_check.py",
                "scripts/architecture_compile.py",
                "scripts/delivery_compile.py", "scripts/delivery_git.py",
                "scripts/delivery_provider.py",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(smoke.SmokeFailure, "delivery_compile.py"):
                (root / "scripts/delivery_compile.py").unlink()
                smoke.exercise_package(root, Path(temporary) / "project", os.environ.copy())

    def test_opencode_projection_self_checks_the_resolved_active_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            source = state / "source"
            projector = source / "scripts/project_opencode.py"
            projector.parent.mkdir(parents=True)
            projector.write_text("# fixture\n", encoding="utf-8")
            project = state / "project"
            private = project / ".opencode/agentrof/agent-marketplace"
            installed = private / "packages/build-123" / smoke.TEAM
            installed.mkdir(parents=True)
            (private / "manage.py").write_text("# fixture\n", encoding="utf-8")
            (project / ".opencode/plugins").mkdir(parents=True)
            (private / "installation.json").write_text(json.dumps({
                "schema_version": 1,
                "active_build_key": "build-123",
            }), encoding="utf-8")
            with mock.patch.object(smoke, "run", return_value="{}"), \
                    mock.patch.object(
                        smoke, "exercise_application_resources"
                    ) as exercised:
                smoke.exercise_project_projection(
                    source,
                    project,
                    os.environ.copy(),
                    "scripts/project_opencode.py",
                    ".opencode",
                )
            exercised.assert_called_once_with(installed.resolve(), os.environ.copy())

            (private / "installation.json").write_text(json.dumps({
                "schema_version": 1,
                "active_build_key": "../escape",
            }), encoding="utf-8")
            with mock.patch.object(smoke, "run", return_value="{}"), \
                    self.assertRaisesRegex(smoke.SmokeFailure, "unsafe active build key"):
                smoke.exercise_project_projection(
                    source,
                    project,
                    os.environ.copy(),
                    "scripts/project_opencode.py",
                    ".opencode",
                )

    def test_real_host_workflow_runs_the_install_smoke(self):
        workflow = (ROOT / ".github/workflows/release-hosts.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("pull_request:", workflow)
        self.assertIn("tools/smoke_plugin_installs.py --channel checkout", workflow)


if __name__ == "__main__":
    unittest.main()
