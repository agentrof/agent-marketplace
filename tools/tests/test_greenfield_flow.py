"""End-to-end contract for a greenfield project reaching delivery readiness."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.tests.test_ba_compile import make_valid_space


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins" / "software-engineering-team" / "scripts"
SETUP = SCRIPTS / "setup_project.py"
BA = SCRIPTS / "ba_compile.py"
SOLUTION = SCRIPTS / "landscape_check.py"
DESIGN_SYSTEM = SCRIPTS / "design_system_compile.py"
EXPERIENCE = SCRIPTS / "experience_compile.py"
BACKLOG = SCRIPTS / "backlog_compile.py"
VAULT = SCRIPTS / "vault_check.py"
PREPARATION = SCRIPTS / "preparation_check.py"


class GreenfieldFlowTests(unittest.TestCase):
    def run_cli(
        self,
        script: Path,
        *args: str,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(script), *args],
            cwd=cwd or ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"{' '.join([script.name, *args])}\n{result.stdout}{result.stderr}",
        )
        return result

    @staticmethod
    def write(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    @staticmethod
    def extend_frontmatter(path: Path, rows: str) -> None:
        text = path.read_text(encoding="utf-8")
        marker = text.find("\n---", 4)
        if marker < 0:
            raise AssertionError(f"unterminated frontmatter: {path}")
        path.write_text(
            text[:marker] + "\n" + rows.rstrip() + text[marker:],
            encoding="utf-8",
        )

    def reconcile_fragment(self, docs: Path, fragment: str) -> None:
        self.run_cli(
            VAULT,
            "reconcile-payload-fragment",
            "--vault",
            str(docs),
            "--fragment",
            fragment,
        )

    @staticmethod
    def ensure_home_map(docs: Path, subtree: str, label: str) -> None:
        home = docs / "home.md"
        link = f"[[maps/{subtree}|{label}]]"
        text = home.read_text(encoding="utf-8")
        if link not in text:
            home.write_text(text.rstrip() + f"\n\n- {link}\n", encoding="utf-8")

    def seed_business_analysis(self, docs: Path) -> Path:
        self.reconcile_fragment(docs, "business-analysis")
        self.ensure_home_map(docs, "business-analysis", "Business Analysis")
        space = docs / "business-analysis" / "erp"
        make_valid_space(space)
        acceptance = (
            space
            / "domains/inventory/acceptance/goods-receipt-acceptance.md"
        )
        acceptance.write_text(
            acceptance.read_text(encoding="utf-8").replace(
                "processes/goods-receipt-process]]\"",
                "processes/goods-receipt-process|Goods Receipt]]\"",
            ),
            encoding="utf-8",
        )

        authored = sorted(
            path for path in space.rglob("*.md") if "_generated" not in path.parts
        )
        for path in authored:
            rel = path.relative_to(space).as_posix()
            if path.name in {"space.md", "domain.md"}:
                owner = "maps/business-analysis"
            elif rel.startswith("domains/inventory/"):
                owner = "business-analysis/erp/domains/inventory/domain"
            else:
                owner = "business-analysis/erp/space"
            path.write_text(
                path.read_text(encoding="utf-8").rstrip()
                + "\n\n## Navigation <!-- sec: nav -->\n\n"
                + f"[[{owner}|Business Analysis]]\n",
                encoding="utf-8",
            )

        links = "\n".join(
            f"- [[{path.relative_to(docs).with_suffix('').as_posix()}|"
            f"{path.stem.replace('-', ' ').title()}]]"
            for path in authored
        )
        self.write(
            docs / "maps" / "business-analysis.md",
            "---\ntype: moc\ntitle: Business Analysis\ntags:\n"
            "  - doc/moc\n---\n\n# Business Analysis\n\n"
            f"{links}\n",
        )
        self.run_cli(
            VAULT,
            "normalize",
            "--vault",
            str(docs),
            "--scope",
            "business-analysis",
        )
        self.run_cli(VAULT, "render-relations", "--vault", str(docs))
        self.run_cli(BA, "render", "--space", str(space), "--vault-root", str(docs))
        self.run_cli(
            BA,
            "check",
            "--space",
            str(space),
            "--vault-root",
            str(docs),
            "--gate",
            "approval",
            "--json",
        )
        return space

    def seed_solution_design(self, docs: Path) -> None:
        self.reconcile_fragment(docs, "solution-design")
        self.ensure_home_map(docs, "solution-design", "Solution Design")
        tree = docs / "solution-design"
        landscape = tree / "landscape.md"
        engagement = tree / "engagements" / "inventory-platform.md"
        self.write(
            landscape,
            """---
type: landscape
title: ERP Solution Landscape
status: approved
owner_role: solution_architect
approved_at: 2025-01-01
derives_from:
  - "[[business-analysis/erp/space|erp]]"
tags:
  - doc/landscape
  - status/approved
---

# ERP Solution Landscape

## Summary

The approved target starts from the inventory analysis.

## Current

Nothing built yet.

## Target

## Transition

## Components

| component | verdict | decision | engagement | status |
|---|---|---|---|---|

## Navigation <!-- sec: nav -->

[[maps/solution-design|Solution Design]]
""",
        )
        self.write(
            engagement,
            """---
type: engagement
title: Inventory Platform Engagement
status: approved
owner_role: solution_architect
approved_at: 2025-01-01
tags:
  - doc/engagement
  - status/approved
---

# Inventory Platform Engagement

## Summary

Status: approved 2025-01-01

The greenfield target needs no additional structural decision.

## Framing

Inventory receipt behavior is bounded by the approved analysis.

## Options

No independent platform choice is required for this fixture.

## Verdict

Proceed with the documented target and preserve the analysis constraints.

## Navigation <!-- sec: nav -->

[[maps/solution-design|Solution Design]]
""",
        )
        map_path = docs / "maps" / "solution-design.md"
        self.write(
            map_path,
            "---\ntype: moc\ntitle: Solution Design\ntags:\n"
            "  - doc/moc\n---\n\n# Solution Design\n\n"
            "- [[solution-design/landscape|ERP Solution Landscape]]\n"
            "- [[solution-design/engagements/inventory-platform|"
            "Inventory Platform Engagement]]\n",
        )
        self.run_cli(SOLUTION, "--tree", str(tree))

    def seed_design_system(self, docs: Path) -> None:
        self.reconcile_fragment(docs, "design-system")
        self.ensure_home_map(docs, "design-system", "Design System")
        root = docs / "design-system"
        master = root / "MASTER.md"
        self.write(
            master,
            """---
type: design_master
title: ERP Design Master
status: draft
revision: 1
tags:
  - doc/design-master
  - status/draft
---

# ERP Design Master

The baseline defines a restrained, accessible interface for inventory work.

## Navigation <!-- sec: nav -->

[[maps/design-system|Design System]]
""",
        )
        map_path = docs / "maps" / "design-system.md"
        self.write(
            map_path,
            "---\ntype: moc\ntitle: Design System\ntags:\n"
            "  - doc/moc\n---\n\n# Design System\n\n"
            "- [[design-system/MASTER|ERP Design Master]]\n",
        )
        self.run_cli(DESIGN_SYSTEM, "approve", "--root", str(root))
        self.run_cli(DESIGN_SYSTEM, "check", "--root", str(root))

    def seed_experience_design(self, docs: Path, ba_space: Path) -> None:
        self.reconcile_fragment(docs, "experience-design")
        self.ensure_home_map(docs, "experience-design", "Experience Design")
        root = docs / "experience-design"
        constraint = "[[solution-design/landscape|ERP Solution Landscape]]"
        self.run_cli(
            EXPERIENCE,
            "init-program",
            "--root",
            str(root),
            "--program",
            "PRG-001",
            "--title",
            "Inventory Experience Program",
            "--scope",
            "erp",
            "--constrained-by",
            constraint,
        )
        self.run_cli(
            EXPERIENCE,
            "init-release",
            "--root",
            str(root),
            "--program",
            "PRG-001",
            "--release",
            "REL-001",
            "--title",
            "Inventory Experience Release",
            "--scope",
            "erp#domains/inventory",
            "--constrained-by",
            constraint,
        )
        release = root / "programs" / "prg-001" / "releases" / "rel-001"
        registry = ba_space / "_generated" / "registry.json"
        analysis_hash = "sha256:" + hashlib.sha256(registry.read_bytes()).hexdigest()
        self.run_cli(
            EXPERIENCE,
            "stub",
            "--release-root",
            str(release),
            "--kind",
            "journey",
            "--id",
            "JRN-001",
            "--slug",
            "receive-goods",
            "--title",
            "Receive Goods Journey",
            "--scope",
            "erp#domains/inventory",
            "--analysis-hash",
            analysis_hash,
            "--criterion-set",
            "erp:AC-INV-001",
        )
        challenge_hash = "sha256:" + hashlib.sha256(
            b"greenfield experience challenge"
        ).hexdigest()
        self.run_cli(EXPERIENCE, "render", "--release-root", str(release))
        self.run_cli(
            EXPERIENCE,
            "stamp",
            "--release-root",
            str(release),
            "--challenge-hash",
            challenge_hash,
        )
        self.run_cli(
            EXPERIENCE,
            "render",
            "--root",
            str(root),
            "--program",
            "PRG-001",
        )
        self.run_cli(
            EXPERIENCE,
            "stamp",
            "--root",
            str(root),
            "--program",
            "PRG-001",
            "--challenge-hash",
            challenge_hash,
        )
        self.run_cli(
            EXPERIENCE,
            "check",
            "--root",
            str(root),
            "--program",
            "PRG-001",
            "--gate",
            "--json",
        )

    def seed_backlog(self, docs: Path) -> None:
        criterion = (
            "[[business-analysis/erp/domains/inventory/acceptance/"
            "goods-receipt-acceptance|erp:AC-INV-001]]"
        )
        experience = (
            "[[experience-design/programs/prg-001/releases/rel-001/release|"
            "Inventory Experience Release]]"
        )
        self.run_cli(BACKLOG, "init", "--docs", str(docs))
        self.run_cli(
            BACKLOG,
            "stub-epic",
            "inventory-receiving",
            "--id",
            "EP-001",
            "--docs",
            str(docs),
        )
        self.run_cli(
            BACKLOG,
            "stub-story",
            "inventory-receiving",
            "receive-goods",
            "--id",
            "ST-001",
            "--criterion-ref",
            criterion,
            "--experience-ref",
            experience,
            "--docs",
            str(docs),
        )
        epic_review = (
            docs
            / "backlog/epics/inventory-receiving/reviews/round-1-epic-review.md"
        )
        self.extend_frontmatter(
            epic_review,
            """verdict: approved
verifies:
  - "[[backlog/epics/inventory-receiving/stories/receive-goods/story|ST-001]]"
  - "[[backlog/epics/inventory-receiving/stories/receive-goods/test-plan|ST-001 test plan]]"
scenario_refs:
  - ST-001-TS-001
dependency_refs:
""",
        )
        root_review = docs / "backlog/reviews/round-1-backlog-review.md"
        self.extend_frontmatter(
            root_review,
            """verdict: approved
related_to:
  - "[[backlog/epics/inventory-receiving/epic|EP-001]]"
dependency_refs:
""",
        )
        self.run_cli(BACKLOG, "check", "--docs", str(docs), "--render", "--json")
        self.run_cli(BACKLOG, "approve", "--docs", str(docs))
        self.run_cli(
            BACKLOG,
            "check",
            "--docs",
            str(docs),
            "--approved",
            "--render",
            "--json",
        )

    def test_greenfield_project_routes_to_delivery_from_tracked_documents(self):
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            self.run_cli(SETUP, "--project-root", str(project), "--json")
            docs = project / "workspace" / "docs"

            ba_space = self.seed_business_analysis(docs)
            self.seed_solution_design(docs)
            self.seed_design_system(docs)
            self.seed_experience_design(docs, ba_space)
            self.seed_backlog(docs)

            self.run_cli(VAULT, "render-relations", "--vault", str(docs))
            self.run_cli(VAULT, "check", "--vault", str(docs), "--json")

            subprocess.run(["git", "add", "--all"], cwd=project, check=True)
            tracked = set(subprocess.run(
                ["git", "ls-files"], cwd=project, capture_output=True,
                text=True, check=True,
            ).stdout.splitlines())
            self.assertIn("workspace/docs/backlog/backlog.md", tracked)
            self.assertIn(
                "workspace/docs/backlog/epics/inventory-receiving/stories/"
                "receive-goods/test-plan.md",
                tracked,
            )
            self.assertFalse(any(path.startswith(".agentrof/") for path in tracked))

            portable = project / ".github" / "agentrof" / "vault-gate.pyz"
            gate = self.run_cli(
                portable, "check", "--project-root", str(project), "--json"
            )
            gate_payload = json.loads(gate.stdout)
            self.assertTrue(gate_payload["ok"])
            self.assertEqual(
                {item["name"] for item in gate_payload["results"]},
                {
                    "workspace-contract",
                    "closed-vault-schema-and-relations",
                    "business-analysis:erp",
                    "solution-design",
                    "design-system",
                    "experience-design:prg-001",
                    "backlog:approved",
                },
            )

            before = self.run_cli(
                PREPARATION,
                "route",
                "--project-root",
                str(project),
                "--intent",
                "deliver",
                "--json",
            )
            before_payload = json.loads(before.stdout)
            self.assertTrue(before_payload["ok"])
            self.assertEqual(before_payload["next_entry"], "deliver")
            self.assertTrue(all(
                before_payload["checks"][key]
                for key in (
                    "business_analysis",
                    "solution_design",
                    "design_system",
                    "experience_design",
                    "backlog_present",
                    "backlog_approved",
                )
            ))

            shutil.rmtree(project / ".agentrof")
            after = self.run_cli(
                PREPARATION,
                "route",
                "--project-root",
                str(project),
                "--intent",
                "deliver",
                "--json",
            )
            self.assertEqual(json.loads(after.stdout), before_payload)


if __name__ == "__main__":
    unittest.main()
