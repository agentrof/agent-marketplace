"""Project-level Obsidian backlog designation and palette contracts."""

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
PROJECT_CONFIG = PLUGIN / "scripts" / "project_config.py"
POLICY = (
    PLUGIN
    / "skill-content"
    / "obsidian-vault"
    / "data"
    / "vault-policy.json"
)

BACKLOG_DESIGNATIONS = {
    "backlog": "backlog",
    "backlog-review": "backlog review",
    "epic": "epic",
    "epic-review": "epic review",
    "story": "story",
    "test-plan": "test plan",
    "issue-report": "issue report",
}
BACKLOG_COLORS = {
    "backlog": 11032055,
    "backlog-review": 16007006,
    "epic": 3900150,
    "epic-review": 16096779,
    "story": 1357990,
    "test-plan": 2278750,
    "issue-report": 14513808,
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
    def issue_note(title: str, ident: str, relation: str = "") -> str:
        relation_row = f"related_to:\n  - \"{relation}\"\n" if relation else ""
        return (
            "---\ntype: issue-report\n"
            f"title: {title}\nstatus: draft\n{relation_row}"
            "tags:\n  - doc/issue-report\n  - status/draft\n"
            f"aliases:\n  - {ident}\n---\n\n# {title}\n"
        )

    def test_setup_materializes_default_designations_and_fixed_colors(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            config = json.loads(
                (workspace / "config.json").read_text(encoding="utf-8")
            )
            for doc_type, designation in BACKLOG_DESIGNATIONS.items():
                self.assertEqual(
                    config["doc_type_designations"][doc_type], designation
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

    def test_localized_designation_is_reconciled_recorded_and_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            workspace = self.setup_project(project)
            config_path = workspace / "config.json"
            language = subprocess.run(
                [sys.executable, str(PROJECT_CONFIG), "set", "--config",
                 str(config_path), "--field", "output_language", "--value",
                 "Turkish"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(language.returncode, 0, language.stderr)
            issues = workspace / "docs" / "issues"
            issues.mkdir()
            (issues / "problem.md").write_text(
                "---\ntype: issue-report\ntitle: Problem issue report\n"
                "status: draft\ntags:\n  - doc/issue-report\n"
                "  - status/draft\naliases:\n  - ISSUE-001\n---\n\n"
                "# Problem issue report\n",
                encoding="utf-8",
            )
            (issues / "reference.md").write_text(
                "---\ntype: issue-report\ntitle: Reference issue report\n"
                "status: draft\ntags:\n  - doc/issue-report\n"
                "  - status/draft\naliases:\n  - ISSUE-002\n---\n\n"
                "# Reference issue report\n\n"
                "[[issues/problem|Problem issue report]]\n",
                encoding="utf-8",
            )
            reconciled = subprocess.run(
                [sys.executable, str(VAULT_CHECK), "reconcile-designations",
                 "--vault", str(workspace / "docs"), "--set",
                 "issue-report=sorun kaydı", "--json"],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(
                reconciled.returncode, 0,
                reconciled.stdout + reconciled.stderr,
            )
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                config["doc_type_designations"]["issue-report"],
                "sorun kaydı",
            )
            history = config["doc_type_designation_history"]["issue-report"]
            self.assertEqual(history[-1]["value"], "issue report")
            problem = (issues / "problem.md").read_text(encoding="utf-8")
            reference = (issues / "reference.md").read_text(encoding="utf-8")
            self.assertIn("title: Problem sorun kaydı", problem)
            self.assertIn("# Problem sorun kaydı", problem)
            self.assertIn("[[issues/problem|Problem sorun kaydı]]", reference)
            rerun = subprocess.run(
                [sys.executable, str(SETUP), "--project-root", str(project)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(rerun.returncode, 0, rerun.stderr)
            after = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                after["doc_type_designations"]["issue-report"],
                "sorun kaydı",
            )
            self.assertEqual(
                after["doc_type_designation_history"]["issue-report"],
                history,
            )

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
                    "issue_report",
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
                "issue_report": [r"^issues/[a-z0-9]+(?:-[a-z0-9]+)*\.md$"],
            },
        )

    def test_designation_dry_run_is_byte_preserving(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            config = workspace / "config.json"
            before = config.read_bytes()
            result = self.run_vault(
                "reconcile-designations", "--vault", str(workspace / "docs"),
                "--set", "story=kullanıcı hikayesi", "--dry-run", "--json",
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(config.read_bytes(), before)
            payload = json.loads(result.stdout)
            self.assertIn("story", json.dumps(payload, sort_keys=True))
            self.assertIn("kullanıcı hikayesi", json.dumps(payload, ensure_ascii=False))

    def test_designation_rejects_unknown_and_duplicate_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            for assignment, expected in (
                ("unknown=value", "names no known doc type"),
                ("story=epic", "share one fold-equal designation"),
            ):
                with self.subTest(assignment=assignment):
                    result = self.run_vault(
                        "reconcile-designations", "--vault",
                        str(workspace / "docs"), "--set", assignment, "--json",
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stdout + result.stderr)

    def test_normalize_dry_run_and_second_apply_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            note = workspace / "docs/issues/problem.md"
            note.parent.mkdir()
            note.write_text(
                self.issue_note("Problem", "ISSUE-001"), encoding="utf-8"
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
            self.assertIn(b"title: Problem issue report", normalized)
            second = self.run_vault(
                "normalize", "--vault", str(workspace / "docs"), "--json"
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(note.read_bytes(), normalized)

    def test_relation_render_is_deterministic_and_materializes_inverse(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            issues = workspace / "docs/issues"
            issues.mkdir()
            target = issues / "target.md"
            source = issues / "source.md"
            target.write_text(
                self.issue_note("Target issue report", "ISSUE-001"),
                encoding="utf-8",
            )
            source.write_text(
                self.issue_note(
                    "Source issue report", "ISSUE-002",
                    "[[issues/target|Target issue report]]",
                ),
                encoding="utf-8",
            )
            first = self.run_vault(
                "render-relations", "--vault", str(workspace / "docs")
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            rendered = target.read_bytes()
            self.assertIn(b"Related from", rendered)
            self.assertIn(b"[[issues/source|Source issue report]]", rendered)
            second = self.run_vault(
                "render-relations", "--vault", str(workspace / "docs")
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(target.read_bytes(), rendered)

    def test_relation_contract_rejects_wrong_target_type(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            issues = workspace / "docs/issues"
            issues.mkdir()
            note = issues / "source.md"
            note.write_text(
                self.issue_note(
                    "Source issue report", "ISSUE-002",
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

    def test_locked_designation_change_requires_explicit_include(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            review = (
                workspace / "docs/business-analysis/erp/reviews/space-round-2-review.md"
            )
            review.parent.mkdir(parents=True)
            config = json.loads(
                (workspace / "config.json").read_text(encoding="utf-8")
            )
            current = config["doc_type_designations"]["challenge-record"]
            old_title = f"ERP {current} 2"
            review.write_text(
                f"---\ntype: challenge-record\ntitle: {old_title}\n"
                "status: approved\nowner_role: business_analyst\nround: 2\n"
                "review_scope: space\nverdict: continue\nlocked: true\n"
                "approved_at: 2026-07-01\ntags:\n  - doc/challenge-record\n"
                f"  - status/approved\n---\n\n# {old_title}\n",
                encoding="utf-8",
            )
            before = review.read_bytes()
            skipped = self.run_vault(
                "reconcile-designations", "--vault", str(workspace / "docs"),
                "--set", "challenge-record=assessment round",
            )
            self.assertEqual(skipped.returncode, 0, skipped.stdout + skipped.stderr)
            self.assertEqual(review.read_bytes(), before)
            self.assertIn("LOCKED skipped", skipped.stdout)
            included = self.run_vault(
                "reconcile-designations", "--vault", str(workspace / "docs"),
                "--set", "challenge-record=assessment round", "--include-locked",
            )
            self.assertEqual(included.returncode, 0, included.stdout + included.stderr)
            text = review.read_text(encoding="utf-8")
            self.assertIn("title: ERP assessment round 2", text)
            self.assertIn("# ERP assessment round 2", text)
            self.assertIn("locked: true", text)

    def test_designation_history_rejects_unknown_and_current_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = self.setup_project(Path(temporary))
            config_path = workspace / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["doc_type_designation_history"] = {
                "ghost": [{"value": "specter"}],
                "story": [{"value": config["doc_type_designations"]["story"]}],
            }
            config_path.write_text(
                json.dumps(config, indent=2) + "\n", encoding="utf-8"
            )
            result = self.check_vault(workspace)
            self.assertEqual(result.returncode, 1)
            self.assertIn("history key 'ghost'", result.stdout)
            self.assertIn("repeats the current designation", result.stdout)


if __name__ == "__main__":
    unittest.main()
