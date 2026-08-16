"""Git handoff closes only committed preparation evidence."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins/software-engineering-team/scripts"
sys.path.insert(0, str(SCRIPTS))

import preparation_check  # noqa: E402


class PreparationHandoffTests(unittest.TestCase):
    def fixture(self, root: Path) -> Path:
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        docs = root / "workspace/docs"
        (docs / "backlog").mkdir(parents=True)
        (docs / "maps").mkdir()
        (docs / "issues").mkdir()
        (docs / ".obsidian/snippets").mkdir(parents=True)
        (root / ".github/agentrof").mkdir(parents=True)
        (root / "workspace/config.json").write_text("{}\n", encoding="utf-8")
        (docs / "home.md").write_text("# Home\n", encoding="utf-8")
        (docs / "maps/backlog.md").write_text("# Backlog map\n", encoding="utf-8")
        (docs / "maps/issues.md").write_text(
            "# Issues map\n\n"
            "[[issues/production-defect|ISSUE-001]]\n",
            encoding="utf-8",
        )
        (docs / "backlog/backlog.md").write_text(
            "# Backlog\n\n[[issues/production-defect|ISSUE-001]]\n",
            encoding="utf-8",
        )
        (docs / "issues/production-defect.md").write_text(
            "# Production defect\n", encoding="utf-8"
        )
        for name in (
            "app.json", "appearance.json", "core-plugins.json",
            "graph.json", "types.json",
        ):
            (docs / ".obsidian" / name).write_text("{}\n", encoding="utf-8")
        (docs / ".obsidian/snippets/brand.css").write_text(
            "/* tracked fixture */\n", encoding="utf-8"
        )
        (root / ".github/agentrof/vault-gate.pyz").write_bytes(b"fixture\n")
        subprocess.run(
            ["git", "add", "workspace/config.json", "workspace/docs/home.md",
             "workspace/docs/maps/backlog.md", "workspace/docs/maps/issues.md",
             "workspace/docs/backlog", "workspace/docs/.obsidian",
             ".github/agentrof/vault-gate.pyz"],
            cwd=root, check=True,
        )
        subprocess.run(
            ["git", "-c", "user.name=Agentrof Test",
             "-c", "user.email=agentrof-test@example.invalid",
             "commit", "-qm", "Commit backlog only"],
            cwd=root, check=True,
        )
        return docs

    def test_untracked_linked_evidence_blocks_existing_backlog_handoff(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            self.fixture(project)
            blocked = preparation_check.git_handoff(project, ["backlog"])
            evidence = "workspace/docs/issues/production-defect.md"
            self.assertFalse(blocked["ok"])
            self.assertIn(evidence, blocked["required_paths"])
            self.assertIn(evidence, blocked["untracked_files"])

            subprocess.run(["git", "add", evidence], cwd=project, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Agentrof Test",
                 "-c", "user.email=agentrof-test@example.invalid",
                 "commit", "-qm", "Commit planning evidence"],
                cwd=project, check=True,
            )
            self.assertTrue(
                preparation_check.git_handoff(project, ["backlog"])["ok"]
            )

    def test_symlink_inside_a_handoff_tree_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            docs = self.fixture(project)
            subprocess.run(
                ["git", "add", "workspace/docs/issues/production-defect.md"],
                cwd=project, check=True,
            )
            link = docs / "backlog/linked-evidence.md"
            link.symlink_to(docs / "issues/production-defect.md")
            subprocess.run(["git", "add", str(link)], cwd=project, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Agentrof Test",
                 "-c", "user.email=agentrof-test@example.invalid",
                 "commit", "-qm", "Commit symlink fixture"],
                cwd=project, check=True,
            )
            result = preparation_check.git_handoff(project, ["backlog"])
            self.assertFalse(result["ok"])
            self.assertIn(
                "workspace/docs/backlog/linked-evidence.md",
                result["symlink_paths"],
            )


if __name__ == "__main__":
    unittest.main()
