"""Project-level Obsidian title, taxonomy, and graph palette contracts."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "software-engineering-team"
SETUP = PLUGIN / "scripts" / "setup_project.py"
VAULT_CHECK = PLUGIN / "scripts" / "vault_check.py"
POLICY = (
    PLUGIN
    / "skill-content"
    / "obsidian-vault"
    / "data"
    / "vault-policy.json"
)

BACKLOG_COLORS = {
    "backlog": 11032055,
    "backlog-review": 16007006,
    "epic": 3900150,
    "epic-review": 16096779,
    "story": 1357990,
    "test-plan": 2278750,
}


class ProjectVaultContractTests(unittest.TestCase):
    def setup_project(self, root: Path) -> Path:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        result = subprocess.run(
            [sys.executable, str(SETUP), "--project-root", str(root)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return root / "workspace"

    def check_vault(self, workspace: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(VAULT_CHECK),
                "check",
                "--vault",
                str(workspace / "docs"),
                "--json",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def run_vault(
        self, *args: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VAULT_CHECK), *args],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    @staticmethod
    def decision_note(title: str, ident: str, relation: str = "") -> str:
        relation_row = f"related_to:\n  - \"{relation}\"\n" if relation else ""
        return (
            "---\ntype: decision\n"
            f"title: {title}\nstatus: proposed\n{relation_row}"
            "tags:\n  - doc/decision\n  - status/proposed\n"
            f"aliases:\n  - {ident}\n---\n\n# {title}\n"
        )

    def test_setup_writes_small_config_and_fixed_colors(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            config = json.loads(
                (workspace / "config.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                config,
                {
                    "schema_version": 2,
                    "team_id": "software-engineering-team",
                    "output_language": "English",
                    "terminology_language": "English",
                },
            )

            policy = json.loads(POLICY.read_text(encoding="utf-8"))
            policy_groups = {
                group["id"]: (group["query"], group["rgb"])
                for group in policy["graph_color_groups"]
                if group["id"] in BACKLOG_COLORS
            }
            self.assertEqual(
                policy_groups,
                {
                    doc_type: (f"tag:#doc/{doc_type}", color)
                    for doc_type, color in BACKLOG_COLORS.items()
                },
            )

            graph = json.loads(
                (workspace / "docs/.obsidian/graph.json").read_text(
                    encoding="utf-8"
                )
            )
            rendered = {
                group["query"]: group["color"]["rgb"]
                for group in graph["colorGroups"]
            }
            for doc_type, color in BACKLOG_COLORS.items():
                self.assertEqual(rendered[f"tag:#doc/{doc_type}"], color)

    def test_vault_check_rejects_a_backlog_type_outside_its_nested_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            orphan = workspace / "docs/backlog/orphan-story.md"
            orphan.write_text(
                "---\n"
                "type: story\n"
                "title: Orphan story\n"
                "status: planned\n"
                "owner_role: backend-developer\n"
                "tags:\n"
                "  - doc/story\n"
                "  - status/planned\n"
                "aliases:\n"
                "  - ST-999\n"
                "---\n\n"
                "# Orphan story\n",
                encoding="utf-8",
            )
            result = self.check_vault(workspace)
            self.assertEqual(result.returncode, 1)
            self.assertIn("type 'story' is not legal at this path", result.stdout)

    def test_vault_check_rejects_project_property_type_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            path = workspace / "docs/.obsidian/types.json"
            types = json.loads(path.read_text(encoding="utf-8"))
            types["types"]["owner_role"] = "number"
            path.write_text(json.dumps(types, indent=2) + "\n", encoding="utf-8")
            result = self.check_vault(workspace)
            self.assertEqual(result.returncode, 1)
            self.assertIn("property 'owner_role' must be typed 'text'", result.stdout)

    def test_vault_check_rejects_project_backlog_color_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            path = workspace / "docs/.obsidian/graph.json"
            graph = json.loads(path.read_text(encoding="utf-8"))
            story = next(
                group
                for group in graph["colorGroups"]
                if group["query"] == "tag:#doc/story"
            )
            story["color"]["rgb"] += 1
            path.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
            result = self.check_vault(workspace)
            self.assertEqual(result.returncode, 1)
            self.assertIn(
                "graph.json colorGroups do not match policy",
                result.stdout,
            )

    def test_policy_closes_the_nested_backlog_markdown_paths(self):
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        self.assertEqual(
            {
                key: policy["type_path_patterns"][key]
                for key in (
                    "backlog",
                    "backlog_review",
                    "epic",
                    "epic_review",
                    "story",
                    "test_plan",
                )
            },
            {
                "backlog": [r"^backlog/backlog\.md$"],
                "backlog_review": [
                    r"^backlog/reviews/round-[0-9]+-backlog-review\.md$"
                ],
                "epic": [
                    r"^backlog/epics/[a-z0-9]+(?:-[a-z0-9]+)*/epic\.md$"
                ],
                "epic_review": [
                    r"^backlog/epics/[a-z0-9]+(?:-[a-z0-9]+)*/reviews/round-[0-9]+-epic-review\.md$"
                ],
                "story": [
                    r"^backlog/epics/[a-z0-9]+(?:-[a-z0-9]+)*/stories/[a-z0-9]+(?:-[a-z0-9]+)*/story\.md$"
                ],
                "test_plan": [
                    r"^backlog/epics/[a-z0-9]+(?:-[a-z0-9]+)*/stories/[a-z0-9]+(?:-[a-z0-9]+)*/test-plan\.md$"
                ],
            },
        )

    def test_normalize_dry_run_and_second_apply_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            note = workspace / "docs/solution-design/decisions/problem-decision.md"
            note.parent.mkdir(parents=True)
            note.write_text(
                self.decision_note("Problem", "DEC-001"), encoding="utf-8"
            )
            before = note.read_bytes()
            dry = self.run_vault(
                "normalize", "--vault", str(workspace / "docs"),
                "--dry-run", "--json",
            )
            self.assertEqual(dry.returncode, 0, dry.stdout + dry.stderr)
            self.assertEqual(note.read_bytes(), before)
            first = self.run_vault(
                "normalize", "--vault", str(workspace / "docs"), "--json"
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            normalized = note.read_bytes()
            self.assertIn(b"title: Problem", normalized)
            second = self.run_vault(
                "normalize", "--vault", str(workspace / "docs"), "--json"
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(note.read_bytes(), normalized)

    def test_title_shape_rejects_generic_and_duplicate_graph_labels(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            decisions = workspace / "docs/solution-design/decisions"
            decisions.mkdir(parents=True)
            (decisions / "overview-decision.md").write_text(
                self.decision_note("Overview", "DEC-001"), encoding="utf-8"
            )
            (decisions / "overview-copy-decision.md").write_text(
                self.decision_note("overview", "DEC-002"), encoding="utf-8"
            )
            result = self.check_vault(workspace)
            self.assertEqual(result.returncode, 1)
            self.assertIn("generic title 'Overview'", result.stdout)
            self.assertIn("also used by", result.stdout)

    def test_relation_render_is_deterministic_and_materializes_inverse(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            decisions = workspace / "docs/solution-design/decisions"
            decisions.mkdir(parents=True)
            target = decisions / "target-decision.md"
            source = decisions / "source-decision.md"
            target.write_text(
                self.decision_note("Target decision", "DEC-001"),
                encoding="utf-8",
            )
            source.write_text(
                self.decision_note(
                    "Source decision", "DEC-002",
                    "[[solution-design/decisions/target-decision|Target decision]]",
                ),
                encoding="utf-8",
            )
            first = self.run_vault(
                "render-relations", "--vault", str(workspace / "docs")
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            rendered = target.read_bytes()
            self.assertIn(b"Related from", rendered)
            self.assertIn(
                b"[[solution-design/decisions/source-decision|Source decision]]",
                rendered,
            )
            second = self.run_vault(
                "render-relations", "--vault", str(workspace / "docs")
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(target.read_bytes(), rendered)

    def test_relation_contract_rejects_wrong_target_type(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            decisions = workspace / "docs/solution-design/decisions"
            decisions.mkdir(parents=True)
            note = decisions / "source-decision.md"
            note.write_text(
                self.decision_note(
                    "Source decision", "DEC-002",
                    "[[home|Home]]",
                ).replace("related_to:", "verifies:"),
                encoding="utf-8",
            )
            result = self.check_vault(workspace)
            self.assertEqual(result.returncode, 1)
            self.assertIn("relation 'verifies' cannot target type 'home'", result.stdout)

    def test_materialize_payload_is_idempotent_and_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "docs"
            first = self.run_vault(
                "materialize-payload", "--vault", str(docs)
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            graph_path = docs / ".obsidian/graph.json"
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            graph["scale"] = 2
            graph_path.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
            before = graph_path.read_bytes()
            second = self.run_vault(
                "materialize-payload", "--vault", str(docs)
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(graph_path.read_bytes(), before)

    def test_brand_payload_and_enablement_converge_without_losing_appearance(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            workspace = self.setup_project(project)
            docs = workspace / "docs"
            brand = docs / ".obsidian/snippets/brand.css"
            brand.write_text("stale brand\n", encoding="utf-8")
            appearance_path = docs / ".obsidian/appearance.json"
            appearance = json.loads(appearance_path.read_text(encoding="utf-8"))
            appearance["accentColor"] = "#ABCDEF"
            appearance["enabledCssSnippets"] = ["project-custom"]
            appearance_path.write_text(
                json.dumps(appearance, indent=2) + "\n", encoding="utf-8"
            )

            failed = self.check_vault(workspace)
            self.assertEqual(failed.returncode, 1)
            self.assertIn("brand.css does not match", failed.stdout)
            self.assertIn("must include brand", failed.stdout)

            applied = subprocess.run(
                [sys.executable, str(SETUP), "apply", "--project-root",
                 str(project), "--json"], cwd=ROOT, capture_output=True,
                text=True, check=False,
            )
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            self.assertEqual(
                brand.read_bytes(),
                (PLUGIN / "templates/vault/.obsidian/snippets/brand.css").read_bytes(),
            )
            repaired = json.loads(appearance_path.read_text(encoding="utf-8"))
            self.assertEqual(repaired["accentColor"], "#ABCDEF")
            self.assertEqual(
                repaired["enabledCssSnippets"], ["project-custom", "brand"]
            )

    def test_standardize_graph_colors_preserves_unowned_knobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            graph_path = workspace / "docs/.obsidian/graph.json"
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            graph["colorGroups"][0]["color"]["rgb"] = 7
            graph["scale"] = 2
            graph_path.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
            first = self.run_vault(
                "standardize-graph-colors", "--vault", str(workspace / "docs")
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            repaired = json.loads(graph_path.read_text(encoding="utf-8"))
            policy = json.loads(POLICY.read_text(encoding="utf-8"))
            expected = {
                group["query"]: group["rgb"]
                for group in policy["graph_color_groups"]
            }
            actual = {
                group["query"]: group["color"]["rgb"]
                for group in repaired["colorGroups"]
            }
            self.assertEqual(actual, expected)
            self.assertEqual(repaired["scale"], 2)
            second = self.run_vault(
                "standardize-graph-colors", "--vault", str(workspace / "docs")
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn("already standard", second.stdout)

if __name__ == "__main__":
    unittest.main()
