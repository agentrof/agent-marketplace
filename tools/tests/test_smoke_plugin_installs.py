"""Deterministic contracts behind the real-host installation smoke."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
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

    def test_native_packages_execute_fresh_setup(self):
        for host in ("claude", "codex"):
            with self.subTest(host=host), tempfile.TemporaryDirectory() as temporary:
                project = Path(temporary) / "project"
                smoke.init_project(project, os.environ.copy())
                smoke.exercise_package(
                    ROOT / "dist" / host / smoke.TEAM,
                    project,
                    os.environ.copy(),
                )
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

    def test_real_host_workflow_runs_the_install_smoke(self):
        workflow = (ROOT / ".github/workflows/release-hosts.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("pull_request:", workflow)
        self.assertIn("tools/smoke_plugin_installs.py --channel checkout", workflow)


if __name__ == "__main__":
    unittest.main()
