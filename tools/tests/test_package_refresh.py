"""Phase 0 proof for a real packaged N to N+1 project refresh."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR))
sys.path.insert(0, str(TESTS_DIR.parent))

import build_distributions  # noqa: E402
import fixtures  # noqa: E402


class PackageRefreshAcceptanceTests(unittest.TestCase):
    def run_json(
        self, script: Path, *args: str, expected: int = 0
    ) -> dict:
        result = subprocess.run(
            [sys.executable, str(script), *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        self.assertEqual(
            result.returncode,
            expected,
            result.stdout + result.stderr,
        )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            self.fail(f"{script.name} did not emit JSON: {exc}: {result.stdout}")

    def initialize_project(self, package: Path, project: Path, host: str) -> None:
        subprocess.run(
            ["git", "init", "--initial-branch=main", str(project)],
            capture_output=True,
            text=True,
            check=True,
        )
        setup = package / "scripts" / "setup_project.py"
        initialized = self.run_json(
            setup,
            "apply",
            "--project-root",
            str(project),
            "--json",
        )
        self.assertEqual(initialized["next_entry"], "requirement")
        generator = package / "scripts" / f"generate_{host}_project.py"
        generated = self.run_json(
            generator,
            "apply",
            "--project-root",
            str(project),
            "--seed-user-files",
            "--scope",
            "all",
        )
        self.assertEqual(generated["status"], "ok")

    @staticmethod
    def tree_snapshot(root: Path, excluded: set[str]) -> dict[str, tuple]:
        snapshot: dict[str, tuple] = {}
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if relative.parts and relative.parts[0] in excluded:
                continue
            key = relative.as_posix()
            if path.is_symlink():
                snapshot[key] = ("symlink", os.readlink(path))
            elif path.is_dir():
                snapshot[key] = ("directory",)
            elif path.is_file():
                snapshot[key] = (
                    "file",
                    hashlib.sha256(path.read_bytes()).hexdigest(),
                )
        return snapshot

    def customize_n_project(self, project: Path) -> dict[Path, bytes]:
        config_path = project / "workspace/config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        # Closed config drops unknown project-owned fields during refresh.
        config["consumer_refresh_data"] = {"owner": "project"}
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

        authored = project / "workspace/docs/project-notes/refresh-proof.md"
        authored.parent.mkdir(parents=True)
        authored.write_text("# Project-owned refresh proof\n", encoding="utf-8")
        user_files = {
            project / "AGENTS.user.md": "# Codex project choices\n",
            project / "CLAUDE.user.md": "# Claude project choices\n",
            project / "workspace/memory/me.md": "# Project working preferences\n",
            project / "workspace/memory/profile.md": "# Project profile\n",
        }
        for path, content in user_files.items():
            path.write_text(content, encoding="utf-8")

        app_path = project / "workspace/docs/.obsidian/app.json"
        app = json.loads(app_path.read_text(encoding="utf-8"))
        app["consumer_refresh_knob"] = "preserve"
        app_path.write_text(json.dumps(app, indent=2) + "\n", encoding="utf-8")

        appearance_path = project / "workspace/docs/.obsidian/appearance.json"
        appearance = json.loads(appearance_path.read_text(encoding="utf-8"))
        appearance["accentColor"] = "#ABCDEF"
        appearance["enabledCssSnippets"] = ["project-custom"]
        appearance_path.write_text(
            json.dumps(appearance, indent=2) + "\n", encoding="utf-8"
        )

        unrelated = (
            project / "workspace/docs/.obsidian/plugins/project-plugin/keep.js"
        )
        unrelated.parent.mkdir(parents=True)
        unrelated.write_text("project-owned plugin\n", encoding="utf-8")
        protected = [
            config_path, authored, app_path, appearance_path, unrelated,
            *user_files,
        ]
        return {path: path.read_bytes() for path in protected}

    def refresh_host(
        self,
        n_root: Path,
        next_root: Path,
        state: Path,
        host: str,
    ) -> dict[str, tuple]:
        install_root = state / "installed" / host
        package = fixtures.install_fixture_package(n_root, host, install_root)
        project = state / f"{host}-project"
        self.initialize_project(package, project, host)

        projected = (
            project / "workspace/docs/.obsidian/plugins/"
            "obsidian-front-matter-title-plugin"
        )
        self.assertTrue((projected / "LICENSE").is_dir())
        self.assertTrue((projected / "retired-after-n.js").is_file())
        if host == "codex":
            self.assertTrue(
                (project / ".codex/agents/refresh-retired-probe.toml").is_file()
            )
        protected = self.customize_n_project(project)

        package = fixtures.install_fixture_package(next_root, host, install_root)
        setup = package / "scripts/setup_project.py"
        inspected = self.run_json(
            setup,
            "inspect",
            "--project-root",
            str(project),
            "--json",
        )
        operations = inspected["operations"]
        self.assertTrue(any(
            item["surface"] == "workspace_config"
            and "consumer_refresh_data" in item.get("removed_fields", [])
            for item in operations
        ))
        self.assertTrue(any(
            item["action"] == "delete"
            and item["path"].endswith(
                "/obsidian-front-matter-title-plugin/retired-after-n.js"
            )
            for item in operations
        ))
        self.assertTrue(any(
            item["action"] == "update"
            and item["path"].endswith("/.obsidian/snippets/brand.css")
            for item in operations
        ))
        self.assertTrue(any(
            item["action"] == "update"
            and item["path"].endswith("/.obsidian/appearance.json")
            for item in operations
        ))
        self.assertTrue(any(
            item["action"] == "delete"
            and item["path"].endswith(
                "/obsidian-front-matter-title-plugin/LICENSE"
            )
            for item in operations
        ))

        applied = self.run_json(
            setup,
            "apply",
            "--project-root",
            str(project),
            "--json",
        )
        self.assertFalse(applied["rolled_back"])

        generator = package / "scripts" / f"generate_{host}_project.py"
        generator_plan = self.run_json(
            generator,
            "check",
            "--project-root",
            str(project),
            "--scope",
            "all",
        )
        self.assertIn("AGENTS.md", generator_plan["changes"])
        self.assertIn("CLAUDE.md", generator_plan["changes"])
        if host == "codex":
            self.assertIn(
                ".codex/agents/refresh-retired-probe.toml",
                generator_plan["changes"],
            )
        generator_apply = self.run_json(
            generator,
            "apply",
            "--project-root",
            str(project),
            "--scope",
            "all",
        )
        self.assertEqual(generator_apply["status"], "ok")

        config = json.loads(
            (project / "workspace/config.json").read_text(encoding="utf-8")
        )
        self.assertEqual(config["schema_version"], 2)
        self.assertEqual(set(config), {
            "schema_version", "team_id", "output_language",
            "terminology_language",
        })
        self.assertNotIn("consumer_refresh_data", config)
        self.assertTrue((projected / "LICENSE").is_file())
        self.assertEqual(
            (projected / "LICENSE").read_bytes(),
            (
                package / "templates/vault/.obsidian/plugins/"
                "obsidian-front-matter-title-plugin/LICENSE"
            ).read_bytes(),
        )
        self.assertFalse((projected / "retired-after-n.js").exists())
        brand = project / "workspace/docs/.obsidian/snippets/brand.css"
        self.assertEqual(
            brand.read_bytes(),
            (package / "templates/vault/.obsidian/snippets/brand.css").read_bytes(),
        )
        appearance = json.loads((
            project / "workspace/docs/.obsidian/appearance.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(appearance["accentColor"], "#ABCDEF")
        self.assertEqual(
            appearance["enabledCssSnippets"], ["project-custom", "brand"]
        )
        for instructions in (project / "AGENTS.md", project / "CLAUDE.md"):
            self.assertNotIn(
                "N-only managed project instruction",
                instructions.read_text(encoding="utf-8"),
            )
        self.assertEqual(
            (
                project
                / "workspace/docs/.obsidian/plugins/project-plugin/keep.js"
            ).read_text(encoding="utf-8"),
            "project-owned plugin\n",
        )
        if host == "codex":
            self.assertFalse(
                (project / ".codex/agents/refresh-retired-probe.toml").exists()
            )
        for path, expected in protected.items():
            if path.name == "config.json":
                continue
            if path.name == "app.json":
                self.assertEqual(
                    json.loads(path.read_text(encoding="utf-8"))[
                        "consumer_refresh_knob"
                    ],
                    "preserve",
                )
                continue
            if path.name == "appearance.json":
                continue
            self.assertEqual(path.read_bytes(), expected, path)

        checked = self.run_json(
            setup,
            "check",
            "--project-root",
            str(project),
            "--json",
        )
        self.assertTrue(checked["ok"])
        generator_checked = self.run_json(
            generator,
            "check",
            "--project-root",
            str(project),
            "--scope",
            "all",
        )
        self.assertEqual(generator_checked["changes"], [])
        second_plan = self.run_json(
            setup,
            "inspect",
            "--project-root",
            str(project),
            "--json",
        )
        self.assertEqual(second_plan["operations"], [])

        before_second = self.tree_snapshot(project, {".git"})
        second_apply = self.run_json(
            setup,
            "apply",
            "--project-root",
            str(project),
            "--json",
        )
        self.assertEqual(second_apply["applied_operations"], [])
        second_generator = self.run_json(
            generator,
            "apply",
            "--project-root",
            str(project),
            "--scope",
            "all",
        )
        self.assertEqual(second_generator["written"], [])
        self.assertEqual(self.tree_snapshot(project, {".git"}), before_second)
        return self.tree_snapshot(project, {".git", ".agentrof", ".codex"})

    def test_real_n_to_next_refresh_converges_on_native_marketplace_hosts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            n_root = root / "marketplace-n"
            next_root = root / "marketplace-next"
            fixtures.make_refresh_pair(n_root, next_root)

            n_identity = set()
            next_identity = set()
            adapters = build_distributions.load_adapters(n_root)
            native_hosts = [
                host for host, adapter in adapters.items()
                if adapter.metadata["artifact_kind"] == "native_marketplace"
            ]
            for host in native_hosts:
                n_provenance = json.loads((
                    n_root / "dist" / host / fixtures.PLUGIN
                    / build_distributions.PROVENANCE
                ).read_text(encoding="utf-8"))
                next_provenance = json.loads((
                    next_root / "dist" / host / fixtures.PLUGIN
                    / build_distributions.PROVENANCE
                ).read_text(encoding="utf-8"))
                self.assertEqual(n_provenance["version"], fixtures.REFRESH_N_VERSION)
                self.assertEqual(
                    next_provenance["version"], fixtures.REFRESH_NEXT_VERSION
                )
                n_identity.add(n_provenance["build_id"])
                next_identity.add(next_provenance["build_id"])
            self.assertEqual(len(n_identity), 1)
            self.assertEqual(len(next_identity), 1)
            self.assertNotEqual(n_identity, next_identity)

            with tempfile.TemporaryDirectory() as first, \
                    tempfile.TemporaryDirectory() as second:
                first_dist = Path(first) / "dist"
                second_dist = Path(second) / "dist"
                build_distributions.build(n_root, first_dist)
                build_distributions.build(n_root, second_dist)
                self.assertEqual(
                    build_distributions.compare_dirs(first_dist, second_dist),
                    [],
                )

            results = {
                host: self.refresh_host(n_root, next_root, root / "runs", host)
                for host in native_hosts
            }
            self.assertEqual(results["claude"], results["codex"])


if __name__ == "__main__":
    unittest.main()
