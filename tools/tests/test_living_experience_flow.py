import contextlib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from tools.tests import backlog_fixture
from tools.tests.test_ba_compile import make_valid_space, write as write_ba_note
from tools.tests.experience_fixture import (
    application_token_markdown, tree_snapshot, write_application,
    write_empty_application,
)


ROOT = Path(__file__).resolve().parents[2]
COMPILER = ROOT / "plugins/software-engineering-team/scripts/experience_compile.py"
STAGES = ROOT / "plugins/software-engineering-team/scripts/stage_package.py"
BACKLOG = ROOT / "plugins/software-engineering-team/scripts/backlog_compile.py"
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
import experience_compile
import experience_application_check
import backlog_compile
import delivery_compile
import requirement_compile
import requirement_route
import stage_package


class LivingExperienceFlowTests(unittest.TestCase):
    def run_cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(COMPILER), *map(str, args)], cwd=ROOT,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def run_backlog_cli(self, *args, expected=0):
        result = subprocess.run(
            [sys.executable, str(BACKLOG), *map(str, args)], cwd=ROOT,
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def stage_candidates(self, docs, stage):
        result = subprocess.run([sys.executable, str(STAGES), "candidates", "--docs", str(docs), "--stage", stage, "--json"],
                                cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)["candidates"]

    def stage_candidate(self, docs, stage):
        return self.stage_candidates(docs, stage)[0]

    def stage_receipt(self, docs, stage):
        return self.stage_candidate(docs, stage)["result_ref"]

    def write_review_attestation(
        self,
        root,
        proposal_hash,
        *,
        reviewer_role="experience-reviewer",
        blockers=None,
        attested_proposal_hash=None,
        source_hash=None,
        package_set_hash=None,
        coverage_hash=None,
        application_hash=None,
        application_revision=None,
        application_status=None,
        reviewed_at_utc=None,
    ):
        registry, _findings = experience_application_check.compile_application(root)
        self.assertIn("source_hash", registry)
        self.assertIn("coverage_hash", registry)
        payload = {
            "schema_version": 2,
            "proposal_hash": attested_proposal_hash or proposal_hash,
            "application_source_hash": source_hash or registry["source_hash"],
            "application_package_set_hash": (
                package_set_hash or registry["package_set_hash"]
            ),
            "application_coverage_hash": coverage_hash or registry["coverage_hash"],
            "application_hash": application_hash or registry["application_hash"],
            "application_revision": (
                registry["application_revision"]
                if application_revision is None
                else application_revision
            ),
            "application_status": (
                experience_compile.application_metadata(root).get(
                    "experience-application-status", ""
                )
                if application_status is None
                else application_status
            ),
            "reviewed_at_utc": reviewed_at_utc or datetime.now(
                timezone.utc
            ).replace(microsecond=0).isoformat(),
            "reviewer_role": reviewer_role,
            "blockers": [] if blockers is None else blockers,
        }
        path = root.parents[2] / ".experience-review-attestation.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path

    def rewrite_application_contract(self, root, mutate):
        application = root / "artifacts/application.html"
        text = application.read_text(encoding="utf-8")
        pattern = re.compile(
            r'(<script type="application/json" '
            r'id="experience-application-contract">)(.*?)(</script>)',
            re.S,
        )
        match = pattern.search(text)
        self.assertIsNotNone(match)
        contract = json.loads(match.group(2))
        mutate(contract)
        body = "\n" + json.dumps(
            contract, ensure_ascii=False, indent=2, sort_keys=True,
        ) + "\n  "
        application.write_text(
            text[:match.start()] + match.group(1) + body + match.group(3)
            + text[match.end():],
            encoding="utf-8",
        )
        return application

    def approve_experience_set(
        self, root, plan, proposal_hash, *experiences, expected=0,
        review_attestation=None,
    ):
        attestation = review_attestation or self.write_review_attestation(
            root, proposal_hash,
        )
        args = [
            "approve-set", "--root", root,
            "--scope-plan", plan, "--proposal-hash", proposal_hash,
            "--review-attestation", attestation,
        ]
        for experience in experiences:
            args.extend(["--experience", experience])
        return self.run_cli(*args, expected=expected)

    def commit_docs(self, root, message):
        project = root.parents[2]
        subprocess.run(["git", "-C", str(project), "add", "workspace/docs"], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-qm", message], check=True)

    def prepare_inputs(self, docs):
        """Create strict-current inputs through their real package gates."""
        ba = docs / "business-analysis/erp"
        make_valid_space(ba)
        process_text = """---
type: process
title: {title}
status: approved
approved_at: 2026-07-12
owner_role: business_analyst
---

# {title}

## Actors <!-- sec: actors -->

Customer performs the business process.

## Trigger <!-- sec: trigger -->

The customer starts {slug}.

## Main Flow <!-- sec: main_flow -->

The customer completes {slug}.

## Exception Flows <!-- sec: exception_flows -->

The customer corrects a recoverable failure.
"""
        process_links = []
        for slug, title in (("checkout", "Checkout"), ("returns", "Returns")):
            write_ba_note(ba / "processes" / f"{slug}-process.md",
                          process_text.format(slug=slug, title=title))
            process_links.append(
                f"- [[business-analysis/erp/processes/{slug}-process|{title}]]"
            )
        space = ba / "space.md"
        space.write_text(
            space.read_text(encoding="utf-8").replace(
                "- [[business-analysis/erp/domains/inventory/domain|Inventory]]",
                "- [[business-analysis/erp/domains/inventory/domain|Inventory]]\n"
                + "\n".join(process_links),
            ), encoding="utf-8",
        )
        ba_compiler = ROOT / "plugins/software-engineering-team/scripts/ba_compile.py"
        approved_ba = subprocess.run([
            sys.executable, str(ba_compiler), "approve-package", "--space", str(ba),
            "--vault-root", str(docs),
        ], cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(approved_ba.returncode, 0, approved_ba.stdout + approved_ba.stderr)

        solution = docs / "solution-design"
        solution.mkdir(parents=True)
        component = solution / "components/orders-api/component.md"
        component.parent.mkdir(parents=True)
        component.write_text(
            "---\ntype: solution-component\ncomponent_id: orders-api\n"
            "component_class: application\nsourcing: build\napp_kind: backend-api\n"
            "code_path: workspace/apps/orders-api\nowned_ba_refs:\n"
            "  - business-analysis/erp/processes/checkout-process\n"
            "  - business-analysis/erp/processes/returns-process\n"
            "  - business-analysis/erp/domains/inventory/processes/goods-receipt-process\n"
            "technology_bindings:\n"
            "  - solution-design/decisions/python-decision\n"
            "  - solution-design/decisions/container-decision\n"
            "data_store_disposition: not_applicable\n---\n# Orders API\n",
            encoding="utf-8",
        )
        decisions = solution / "decisions"
        decisions.mkdir()
        for slug, identifier, kind, technology, skill in (
            ("python", "SD-001", "technology-selection", "python-fastapi", "python-fastapi"),
            ("container", "SD-002", "environment", "docker", "docker-compose"),
        ):
            (decisions / f"{slug}-decision.md").write_text(
                f"---\ntype: decision\nstatus: accepted\naliases:\n  - {identifier}\n"
                f"decision_kind: {kind}\napplies_to:\n  - orders-api\n"
                f"selected_technology: {technology}\nmethod_skills:\n  - {skill}\n"
                f"---\n# {slug.title()} Decision\n",
                encoding="utf-8",
            )
        (solution / "decision-log.md").write_text("<!-- generated by fixture -->\n", encoding="utf-8")
        landscape = solution / "landscape.md"
        checker = ROOT / "plugins/software-engineering-team/scripts/landscape_check.py"
        landscape.write_text(
            "---\ntype: landscape\nstatus: approved\npackage_status: draft\ntopology_selected: true\n"
            "---\n# Landscape\n\n## Target\n\nOrders API is selected through SD-001 and SD-002.\n"
            "\n## Transition\n\nAdopt the accepted topology.\n\n## Components\n\n"
            "| component | decision | verdict |\n|---|---|---|\n"
            "| orders-api | [[solution-design/decisions/python-decision|SD-001]] and "
            "[[solution-design/decisions/container-decision|SD-002]] | accepted |\n",
            encoding="utf-8",
        )
        for command in ("confirm-topology", "approve"):
            result = subprocess.run([sys.executable, str(checker), command, "--tree", str(solution)], cwd=ROOT,
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        design = docs / "design-system"
        design.mkdir(parents=True)
        master = design / "MASTER.md"
        master.write_text(
            "---\ntype: design_master\ntitle: Master\nstatus: draft\nrevision: 1\n"
            "contract_version: 3\nderives_from:\n  - \"[[business-analysis/erp/space|ERP]]\"\n"
            "constrained_by:\n  - \"[[solution-design/landscape|Landscape]]\"\n"
            "tags:\n  - doc/design-master\n  - status/draft\n---\n# Master\n\n"
            "## Product position\n\nCheckout-first product.\n\n"
            "## Brand and asset fidelity\n\nNo supplied identity asset.\n\n"
            "## Global rules\n\n### Catalog tokens\n\n"
            + application_token_markdown()
            + "\n"
            "## Component specs\n\nButtons use the current catalog token.\n\n"
            "## Style guidelines\n\nKeep checkout labels concise.\n\n"
            "## Anti-patterns\n\nAvoid ambiguous confirmation states.\n\n"
            "## Pre-delivery checklist\n\nCheck the current receipt.\n\n"
            "## Navigation\n\n[[maps/design-system|Design System]]\n",
            encoding="utf-8",
        )
        compiler = ROOT / "plugins/software-engineering-team/scripts/design_system_compile.py"
        initialized = subprocess.run(
            [sys.executable, str(compiler), "init-catalog", "--root", str(design)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
        catalog = design / "artifacts/standalone.html"
        catalog.write_text(
            catalog.read_text(encoding="utf-8").replace("AUTHOR_REQUIRED", "Filled"),
            encoding="utf-8",
        )
        synchronized = subprocess.run(
            [sys.executable, str(compiler), "sync-catalog", "--root", str(design)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(synchronized.returncode, 0, synchronized.stdout + synchronized.stderr)
        result = subprocess.run([sys.executable, str(compiler), "approve", "--root", str(design)],
                                cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        project = docs.parents[1]
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        subprocess.run(["git", "-C", str(project), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(project), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(project), "add", "workspace/docs"], check=True)
        subprocess.run(["git", "-C", str(project), "commit", "-qm", "approved inputs"], check=True)

    def propose_manual(self, root, process="business-analysis/erp/processes/checkout-process", **extra):
        docs = root.parent
        args = [sys.executable, str(COMPILER), "propose", "--root", str(root),
                "--process-ref", process, "--origin-mode", "manual",
                "--ba-ref", self.stage_receipt(docs, "business-analysis"),
                "--solution-ref", self.stage_receipt(docs, "solution-design"),
                "--design-ref", self.stage_receipt(docs, "design-system")]
        for key, value in extra.items():
            args.extend(["--" + key.replace("_", "-"), value])
        proposal = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(proposal.returncode, 0, proposal.stdout + proposal.stderr)
        payload = json.loads(proposal.stdout)
        self.assertEqual(payload["schema_version"], 2)
        plan = root.parent / ".scope-plan.json"
        plan.write_text(proposal.stdout, encoding="utf-8")
        return plan, payload["proposal_hash"]

    def init_manual(self, root, experience="checkout", process="business-analysis/erp/processes/checkout-process"):
        docs = root.parent
        plan, proposal_hash = self.propose_manual(root, process, experience=experience)
        args = ["init", "--root", root, "--experience", experience, "--origin-mode", "manual",
                "--primary-process-ref", process, "--ba-ref", self.stage_receipt(docs, "business-analysis"),
                "--solution-ref", self.stage_receipt(docs, "solution-design"), "--design-ref", self.stage_receipt(docs, "design-system"),
                "--scope-plan", plan, "--proposal-hash", proposal_hash]
        payload = json.loads(self.run_cli(*args).stdout)
        return Path(payload["path"]), plan, proposal_hash

    def enter_reviews(self, root, packages, plan, proposal_hash, **application):
        write_application(root, **application)
        for package in packages:
            self.run_cli("enter-review", "--experience-root", package)
        rendered = json.loads(
            self.run_cli("render-application", "--root", root, "--json").stdout
        )
        self.assertTrue(rendered["ok"])
        self.run_cli(
            "enter-application-review", "--root", root,
            "--scope-plan", plan, "--proposal-hash", proposal_hash,
        )

    def create_authored_single(self, root):
        package, plan, proposal_hash = self.init_manual(root)
        self.run_cli(
            "stub", "--experience-root", package, "--kind", "journey",
            "--id", "JRN-001", "--slug", "checkout",
        )
        write_application(root)
        return package, plan, proposal_hash

    def approve_single(self, root):
        package, plan, proposal_hash = self.create_authored_single(root)
        self.run_cli("enter-review", "--experience-root", package)
        self.run_cli(
            "enter-application-review", "--root", root,
            "--scope-plan", plan, "--proposal-hash", proposal_hash,
        )
        approved = self.approve_experience_set(
            root, plan, proposal_hash, package.name,
        )
        return package, json.loads(approved.stdout)["receipts"]

    def approve_interaction_experience(self, root):
        package, plan, proposal_hash = self.init_manual(root)
        self.run_cli(
            "stub", "--experience-root", package, "--kind", "journey",
            "--id", "JRN-001", "--slug", "checkout",
        )
        self.run_cli(
            "stub", "--experience-root", package, "--kind", "screen",
            "--id", "SCR-001", "--slug", "checkout",
            "--uses-design", "design-system/MASTER",
        )
        self.run_cli(
            "stub", "--experience-root", package, "--kind", "state",
            "--id", "STA-001", "--slug", "checkout",
            "--state-class", "ordinary",
        )
        self.enter_reviews(root, [package], plan, proposal_hash)
        approved = self.approve_experience_set(
            root, plan, proposal_hash, package.name,
        )
        return package, json.loads(approved.stdout)["receipts"]

    def approve_manual_backlog(
        self, docs, application_ref, package_ref, experience_refs,
    ):
        universe = backlog_compile.approved_ba_universe(docs)
        criteria = [
            f"[[business-analysis/{entry['space']}/"
            f"{str(entry['doc']).removesuffix('.md')}|{key}]]"
            for key, entry in sorted(universe.items())
        ]
        self.assertTrue(criteria)
        input_refs = [
            self.stage_receipt(docs, "business-analysis"),
            self.stage_receipt(docs, "solution-design"),
            self.stage_receipt(docs, "design-system"),
            application_ref,
            package_ref,
        ]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            initialized = backlog_compile.init(SimpleNamespace(
                docs=str(docs), planning_mode="manual", requirement_ref="",
                input_ref=input_refs,
            ))
            self.assertEqual(initialized, 0, output.getvalue())
            self.assertEqual(backlog_compile.stub_epic(SimpleNamespace(
                docs=str(docs), slug="checkout", id="EP-001", title=None,
                goal="Deliver the approved checkout interaction safely.",
            )), 0)
            self.assertEqual(backlog_compile.stub_story(SimpleNamespace(
                docs=str(docs), epic="checkout", slug="confirm-checkout",
                id="CHK-01", title=None,
                scope="Deliver the observable checkout confirmation outcome.",
                work_kind="feature", criterion_ref=criteria,
                experience_ref=list(experience_refs), evidence_ref=[],
                uses_design=["[[design-system/MASTER|Design Master]]"],
                constrained_by=["[[solution-design/landscape|Landscape]]"],
                implements=[],
            )), 0)

        story_root = docs / "backlog/epics/checkout/stories/confirm-checkout"
        backlog_fixture._author_story(
            story_root / "story.md", story_root / "test-plan.md", "CHK-01",
        )
        root_review = docs / "backlog/reviews/round-1-backlog-review.md"
        root_props, _root_body = backlog_compile.parse_front_matter(root_review)
        root_props.update({
            "verdict": "approved",
            "related_to": ["[[backlog/epics/checkout/epic|EP-001]]"],
            "dependency_refs": [],
        })
        root_review.write_text(backlog_compile.front_matter(
            root_props,
            backlog_fixture._complete_review_body(
                str(root_props["title"]),
                backlog_compile.backlog_contract()[
                    "required_backlog_review_sections"
                ],
            ),
        ), encoding="utf-8")
        epic_review = (
            docs / "backlog/epics/checkout/reviews/round-1-epic-review.md"
        )
        epic_props, _epic_body = backlog_compile.parse_front_matter(epic_review)
        epic_props.update({
            "verdict": "approved",
            "verifies": [
                "[[backlog/epics/checkout/stories/confirm-checkout/story|CHK-01]]",
                "[[backlog/epics/checkout/stories/confirm-checkout/test-plan|CHK-01-TP]]",
            ],
            "scenario_refs": ["CHK-01-TS-001"],
            "dependency_refs": [],
        })
        epic_review.write_text(backlog_compile.front_matter(
            epic_props,
            backlog_fixture._complete_review_body(
                str(epic_props["title"]),
                backlog_compile.backlog_contract()[
                    "required_epic_review_sections"
                ],
            ),
        ), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            approved = backlog_compile.approve(SimpleNamespace(docs=str(docs)))
        self.assertEqual(approved, 0, output.getvalue())
        return story_root / "story.md"

    def approve_journey_revision(self, root, package, revision):
        plan, proposal_hash = self.propose_manual(
            root, experience=package.name, action="update",
        )
        self.run_cli(
            "begin-revision", "--experience-root", package,
            "--scope-plan", plan, "--proposal-hash", proposal_hash,
        )
        journey = package / "journeys/checkout-journey.md"
        data, body = experience_compile.fm(journey)
        prior_revision = revision - 1
        data["revision"] = revision
        data["supersedes"] = f"{package.name}:JRN-001@r{prior_revision}"
        experience_compile.rewrite(
            journey, data, body.rstrip() + f"\n\nRevision {revision}.\n",
        )
        self.enter_reviews(root, [package], plan, proposal_hash)
        approved = self.approve_experience_set(
            root, plan, proposal_hash, package.name,
        )
        return json.loads(approved.stdout)["receipts"]

    def propose_application_only(self, root):
        docs = root.parent
        proposal = subprocess.run(
            [
                sys.executable, str(COMPILER), "propose", "--root", str(root),
                "--origin-mode", "manual", "--application-action", "update",
                "--ba-ref", self.stage_receipt(docs, "business-analysis"),
                "--solution-ref", self.stage_receipt(docs, "solution-design"),
                "--design-ref", self.stage_receipt(docs, "design-system"),
            ],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(proposal.returncode, 0, proposal.stdout + proposal.stderr)
        payload = json.loads(proposal.stdout)
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["application_action"], "update")
        self.assertTrue(all(row["action"] == "reuse" for row in payload["actions"]))
        plan = docs / ".application-scope-plan.json"
        plan.write_text(proposal.stdout, encoding="utf-8")
        return plan, payload["proposal_hash"]

    def approve_v3_design_revision(self, root):
        docs = root.parent
        design = docs / "design-system"
        compiler = ROOT / "plugins/software-engineering-team/scripts/design_system_compile.py"
        revision = subprocess.run(
            [sys.executable, str(compiler), "begin-revision", "--root", str(design)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(revision.returncode, 0, revision.stdout + revision.stderr)
        master = design / "MASTER.md"
        supersedes_hash = next(
            line.split(": ", 1)[1]
            for line in master.read_text(encoding="utf-8").splitlines()
            if line.startswith("supersedes_hash: ")
        )
        master.write_text(
            "---\ntype: design_master\ntitle: Master\nstatus: draft\nrevision: 2\n"
            f"contract_version: 3\nsupersedes_hash: {supersedes_hash}\nderives_from:\n"
            "  - \"[[business-analysis/erp/space|ERP]]\"\nconstrained_by:\n"
            "  - \"[[solution-design/landscape|Landscape]]\"\ntags:\n"
            "  - doc/design-master\n  - status/draft\n---\n# Master\n\n"
            "## Product position\n\nCheckout-first product.\n\n"
            "## Brand and asset fidelity\n\nNo supplied identity asset.\n\n"
            "## Global rules\n\n### Catalog tokens\n\n"
            + application_token_markdown("#ffffff")
            + "\n"
            "## Component specs\n\nButtons use the current catalog token.\n\n"
            "## Style guidelines\n\nKeep checkout labels concise.\n\n"
            "## Anti-patterns\n\nAvoid ambiguous confirmation states.\n\n"
            "## Pre-delivery checklist\n\nCheck the updated receipt.\n\n"
            "## Navigation\n\n[[maps/design-system|Design System]]\n",
            encoding="utf-8",
        )
        catalog = design / "artifacts/standalone.html"
        synchronized = subprocess.run(
            [sys.executable, str(compiler), "sync-catalog", "--root", str(design)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(synchronized.returncode, 0, synchronized.stdout + synchronized.stderr)
        approved = subprocess.run(
            [sys.executable, str(compiler), "approve", "--root", str(design)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.commit_docs(root, "approve revised design system")

    def test_manual_living_experience_approves_without_requirement(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            self.assertEqual(
                [self.stage_candidate(root.parent, stage)["verification_profile"] for stage in
                 ("business-analysis", "solution-design", "design-system")],
                ["strict-current", "strict-current", "strict-current"],
            )
            package, create_plan, proposal_hash = self.init_manual(root)
            self.assertTrue((root / "artifacts/application.html").is_file())
            self.assertTrue((package / "artifacts/application-map.json").is_file())
            self.assertFalse(any(package.glob("artifacts/*-artifact.md")))
            self.run_cli("stub", "--experience-root", package, "--kind", "journey", "--id", "JRN-001", "--slug", "checkout")
            self.enter_reviews(root, [package], create_plan, proposal_hash)
            approved = self.approve_experience_set(
                root, create_plan, proposal_hash, "checkout",
            )
            receipts = json.loads(approved.stdout)["receipts"]
            self.assertEqual(
                [receipt["result_ref"] for receipt in receipts],
                ["application@r1", "checkout@r1"],
            )
            check = self.run_cli("check", "--experience-root", package, "--gate", "--json")
            self.assertTrue(json.loads(check.stdout)["ok"])
            text = (package / "experience.md").read_text(encoding="utf-8")
            self.assertIn("origin_mode: manual", text)
            self.assertNotIn("implements:", text)
            self.commit_docs(root, "approve manual experience")
            self.assertEqual(
                [
                    candidate["result_ref"]
                    for candidate in self.stage_candidates(
                        root.parent, "experience-design"
                    )
                ],
                ["application@r1", "checkout@r1"],
            )
            backlog = ROOT / "plugins/software-engineering-team/scripts/backlog_compile.py"
            missing_application = subprocess.run([
                sys.executable, str(backlog), "init", "--docs", str(root.parent),
                "--planning-mode", "manual",
                "--input-ref", self.stage_receipt(root.parent, "business-analysis"),
                "--input-ref", self.stage_receipt(root.parent, "solution-design"),
                "--input-ref", self.stage_receipt(root.parent, "design-system"),
                "--input-ref", "checkout@r1",
            ], cwd=ROOT, capture_output=True, text=True, check=False)
            self.assertNotEqual(missing_application.returncode, 0)
            initialized = subprocess.run([
                sys.executable, str(backlog), "init", "--docs", str(root.parent),
                "--planning-mode", "manual",
                "--input-ref", self.stage_receipt(root.parent, "business-analysis"),
                "--input-ref", self.stage_receipt(root.parent, "solution-design"),
                "--input-ref", self.stage_receipt(root.parent, "design-system"),
                "--input-ref", "application@r1", "--input-ref", "checkout@r1",
            ], cwd=ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)

    def test_application_is_the_only_visual_implementation_and_old_preview_is_denied(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, _plan, _proposal_hash = self.create_authored_single(root)
            legacy = package / "artifacts/checkout-preview.html"
            legacy.write_text("<!doctype html><title>Legacy preview</title>", encoding="utf-8")
            stylesheet = package / "artifacts/checkout-preview.css"
            stylesheet.write_text("body { color: red; }\n", encoding="utf-8")

            result = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            messages = "\n".join(
                row["message"] for row in json.loads(result.stdout)["findings"]
            )
            self.assertIn("only artifacts/application.html", messages)
            self.assertIn(
                "experiences/checkout/artifacts/checkout-preview.html", messages
            )
            self.assertIn(
                "experiences/checkout/artifacts/checkout-preview.css", messages
            )

    def test_application_map_and_dom_bind_each_state_record_to_its_exact_state(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, _plan, _proposal_hash = self.create_authored_single(root)
            self.run_cli(
                "stub", "--experience-root", package, "--kind", "state",
                "--id", "STA-001", "--slug", "payment-failed",
                "--state-class", "failure",
            )
            application = write_application(root)
            self.run_cli("check-application", "--root", root, "--json")

            map_path = package / "artifacts/application-map.json"
            mapping = json.loads(map_path.read_text(encoding="utf-8"))
            state_binding = next(
                row
                for row in mapping["bindings"]
                if row["record_ref"] == "checkout:STA-001@r1"
            )
            state_binding["entries"][0]["state_class"] = "ordinary"
            map_path.write_text(
                json.dumps(mapping, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            stale_map = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            stale_messages = "\n".join(
                row["message"]
                for row in json.loads(stale_map.stdout)["findings"]
            )
            self.assertIn("has a stale state_class", stale_messages)
            self.assertIn(
                "state record checkout:STA-001@r1 is not rendered as failure",
                stale_messages,
            )

            write_application(root)
            original = application.read_text(encoding="utf-8")
            application.write_text(
                original.replace(
                    'data-application-state="failure"',
                    'data-application-state="ordinary"',
                    1,
                ),
                encoding="utf-8",
            )
            stale_dom = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            dom_messages = "\n".join(
                row["message"]
                for row in json.loads(stale_dom.stdout)["findings"]
            )
            self.assertIn(
                "must render its exact contract state_class",
                dom_messages,
            )

    def test_application_rejects_changed_runtime_and_network_dependencies(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            _package, _plan, _proposal_hash = self.create_authored_single(root)
            application = root / "artifacts/application.html"
            original = application.read_text(encoding="utf-8")
            self.run_cli("check-application", "--root", root, "--json")

            application.write_text(
                original.replace(
                    '"use strict";',
                    '"use strict";\nfetch("https://example.invalid/data");',
                    1,
                ),
                encoding="utf-8",
            )
            runtime = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            runtime_messages = "\n".join(
                row["message"] for row in json.loads(runtime.stdout)["findings"]
            )
            self.assertIn("runtime differs from the shipped fixed runtime", runtime_messages)

            application.write_text(
                original.replace(
                    "</section>",
                    '<img src="https://example.invalid/track.png" alt="">\n    </section>',
                    1,
                ),
                encoding="utf-8",
            )
            network = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            network_messages = "\n".join(
                row["message"] for row in json.loads(network.stdout)["findings"]
            )
            self.assertIn("dependency or form target is forbidden", network_messages)

            application.write_text(
                original.replace(
                    'href="#application-main"',
                    'href="#application-main" ping="https://example.invalid/collect"',
                    1,
                ),
                encoding="utf-8",
            )
            ping = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            ping_messages = "\n".join(
                row["message"] for row in json.loads(ping.stdout)["findings"]
            )
            self.assertIn("dependency or form target is forbidden", ping_messages)

            application.write_text(
                original.replace(
                    "/* application:author-styles:end */",
                    r".leak { background: u\72l(https://example.invalid/x) }"
                    "\n/* application:author-styles:end */",
                    1,
                ),
                encoding="utf-8",
            )
            escaped_css = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            css_messages = "\n".join(
                row["message"]
                for row in json.loads(escaped_css.stdout)["findings"]
            )
            self.assertIn(
                "CSS must not import, execute or reference external resources",
                css_messages,
            )

            application.write_text(
                original.replace(
                    "default-src 'none'",
                    "default-src https://example.invalid",
                    1,
                ),
                encoding="utf-8",
            )
            csp = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            csp_messages = "\n".join(
                row["message"] for row in json.loads(csp.stdout)["findings"]
            )
            self.assertIn("network-denying Content Security Policy", csp_messages)

    def test_application_control_topology_is_enforced_by_full_compile(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            self.create_authored_single(root)
            application = root / "artifacts/application.html"
            text = application.read_text(encoding="utf-8")
            anchor = (
                "<p data-private>This content is privacy-sensitive.</p>"
            )
            malformed = (
                anchor
                + '\n<button type="button" data-application-action="open-modal" '
                'aria-controls="not-a-dialog">Open</button>'
                + '\n<div id="not-a-dialog">Not a dialog</div>'
                + '\n<button type="button" data-application-action="close-modal">'
                'Close</button>'
                + '\n<button type="button" role="option" aria-selected="false" '
                'data-value="one" data-application-action="select-option">'
                'One</button>'
                + '\n<button type="button" data-application-search>Search</button>'
            )
            self.assertIn(anchor, text)
            application.write_text(
                text.replace(anchor, malformed, 1), encoding="utf-8",
            )

            checked = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            messages = "\n".join(
                row["message"] for row in json.loads(checked.stdout)["findings"]
            )
            for expected in (
                "open-modal must target one initially closed, visible dialog",
                "close-modal must be inside one reachable dialog in its same route",
                "select-option must be inside one reachable listbox in its same route",
                "application search needs one enabled input[type=search] and same-route search items",
            ):
                with self.subTest(expected=expected):
                    self.assertIn(expected, messages)

    def test_missing_state_simulations_block_gate_and_atomic_approval(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, plan, proposal_hash = self.create_authored_single(root)
            self.run_cli("enter-review", "--experience-root", package)
            self.run_cli(
                "enter-application-review", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            self.rewrite_application_contract(
                root, lambda contract: contract.__setitem__("simulations", []),
            )

            gate = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            expected = (
                "application deterministic simulations must cover every "
                "non-ordinary state_class"
            )
            messages = "\n".join(
                row["message"] for row in json.loads(gate.stdout)["findings"]
            )
            self.assertIn(expected, messages)
            before = tree_snapshot(root)
            rejected = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", expected=1,
            )
            self.assertIn(expected, rejected.stderr)
            self.assertEqual(tree_snapshot(root), before)
            self.assertEqual(
                experience_compile.read_open_application_state(root)["phase"],
                "in_review",
            )

    def test_application_design_system_binding_and_tokens_are_exact(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            _package, _plan, _proposal_hash = self.create_authored_single(root)
            application = root / "artifacts/application.html"
            original = application.read_text(encoding="utf-8")
            design_hash = self.stage_candidate(
                root.parent, "design-system"
            )["package_hash"]

            application.write_text(
                original.replace(design_hash, "sha256:" + "0" * 64, 1),
                encoding="utf-8",
            )
            metadata = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            metadata_messages = "\n".join(
                row["message"] for row in json.loads(metadata.stdout)["findings"]
            )
            self.assertIn("design-system-package-hash is missing or stale", metadata_messages)

            application.write_text(
                original.replace(
                    "--catalog-background: #fff", "--catalog-background: #000", 1
                ),
                encoding="utf-8",
            )
            tokens = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            token_messages = "\n".join(
                row["message"] for row in json.loads(tokens.stdout)["findings"]
            )
            self.assertIn("Design System token block is missing or stale", token_messages)

            application.write_text(
                original.replace(
                    "/* application:author-styles:start */",
                    "/* application:author-styles:start */\n"
                    ":root { --catalog-background: hotpink; }",
                    1,
                ),
                encoding="utf-8",
            )
            override = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            override_messages = "\n".join(
                row["message"]
                for row in json.loads(override.stdout)["findings"]
            )
            self.assertIn("cannot redefine Design System tokens", override_messages)

            application.write_text(
                original.replace(
                    "/* application:author-styles:start */",
                    "/* application:author-styles:start */\n"
                    ".card { color: #ff0000; padding: 19px; }",
                    1,
                ),
                encoding="utf-8",
            )
            hard_coded = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            hard_coded_messages = "\n".join(
                row["message"]
                for row in json.loads(hard_coded.stdout)["findings"]
            )
            self.assertIn(
                "author styles hard-code Design System values",
                hard_coded_messages,
            )

            application.write_text(
                original.replace(
                    "/* application:author-styles:start */",
                    "/* application:author-styles:start */\n"
                    "#author-route-title::before { content: 'Spoof '; }",
                    1,
                ),
                encoding="utf-8",
            )
            generated_content = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            generated_content_messages = "\n".join(
                row["message"]
                for row in json.loads(generated_content.stdout)["findings"]
            )
            self.assertIn(
                "author styles hard-code Design System values for: content",
                generated_content_messages,
            )

            application.write_text(
                original.replace(
                    "/* application:author-styles:start */",
                    "/* application:author-styles:start */\n"
                    ":root, body, .application-shell { "
                    "forced-color-adjust: none; }\n"
                    "h1 { direction: rtl; unicode-bidi: bidi-override; }\n"
                    ".application-actions { flex-direction: row-reverse; }\n"
                    ".cards { grid-auto-flow: dense; grid-column: 2; }",
                    1,
                ),
                encoding="utf-8",
            )
            forced_colors = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            forced_color_messages = "\n".join(
                row["message"]
                for row in json.loads(forced_colors.stdout)["findings"]
            )
            self.assertIn(
                "author styles hard-code Design System values for:",
                forced_color_messages,
            )
            self.assertIn("forced-color-adjust", forced_color_messages)
            self.assertIn("direction", forced_color_messages)
            self.assertIn("flex-direction", forced_color_messages)
            self.assertIn("grid-auto-flow", forced_color_messages)
            self.assertIn("grid-column", forced_color_messages)
            self.assertIn("unicode-bidi", forced_color_messages)

            split_radio_owners = original.replace(
                '<label>Simulation note <input required name="simulation-note" '
                'aria-label="Simulation note"></label>',
                '<label>Simulation note <input required name="simulation-note" '
                'aria-label="Simulation note"></label>'
                '<input type="radio" name="choice" aria-label="Choice A" '
                'data-context-key="choice" value="a">',
                1,
            ).replace(
                '<button type="button" data-route-target="#/checkout/state-empty" '
                'data-simulation-id="simulate-checkout-empty-2">'
                'Simulate empty</button>',
                '<form data-route-target="#/checkout/state-empty" '
                'data-simulation-id="simulate-checkout-empty-2">'
                '<input type="radio" name="choice" aria-label="Choice B" '
                'data-context-key="choice" value="b">'
                '<button type="submit">Simulate empty</button></form>',
                1,
            )
            self.assertNotEqual(split_radio_owners, original)
            application.write_text(split_radio_owners, encoding="utf-8")
            form_owner_check = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            form_owner_messages = "\n".join(
                row["message"]
                for row in json.loads(form_owner_check.stdout)["findings"]
            )
            self.assertIn(
                "radio context groups must share one native form owner",
                form_owner_messages,
            )

            application.write_text(
                original.replace(
                    '<dialog id="fixture-onboarding" '
                    'data-application-onboarding ',
                    '<dialog id="fixture-onboarding" '
                    'data-application-onboarding aria-modal="false" ',
                    1,
                ),
                encoding="utf-8",
            )
            dialog_aria = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            dialog_aria_messages = "\n".join(
                row["message"]
                for row in json.loads(dialog_aria.stdout)["findings"]
            )
            self.assertIn(
                "dialogs allow passive naming/description ARIA and optional aria-modal=true only",
                dialog_aria_messages,
            )

            application.write_text(
                original.replace(
                    '<dialog id="fixture-onboarding" ',
                    '<dialog id="fixture-onboarding" closedby="none" ',
                    1,
                ),
                encoding="utf-8",
            )
            dialog_close_policy = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            close_policy_messages = "\n".join(
                row["message"]
                for row in json.loads(dialog_close_policy.stdout)["findings"]
            )
            self.assertIn(
                "application cannot use unmanaged native invocation behavior: dialog[closedby]",
                close_policy_messages,
            )

            clobbered_collection = original.replace(
                '<form data-route-target="#/checkout/state-conflict" ',
                '<form data-search-item data-route-target="#/checkout/state-conflict" ',
                1,
            ).replace(
                'required name="simulation-note" aria-label="Simulation note"',
                'required name="innerText" aria-label="Simulation note"',
                1,
            )
            self.assertNotEqual(clobbered_collection, original)
            application.write_text(clobbered_collection, encoding="utf-8")
            collection_check = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            collection_messages = "\n".join(
                row["message"]
                for row in json.loads(collection_check.stdout)["findings"]
            )
            self.assertIn(
                "application collection items must use a non-form browser-stable semantic container",
                collection_messages,
            )

            nested_collection = original.replace(
                '<div data-search-item data-filter-item data-filter-value="open">'
                'Open item</div><div data-search-item data-filter-item '
                'data-filter-value="closed">Closed item</div>',
                '<div data-search-item data-filter-item data-filter-value="open">'
                'Open item<div data-search-item data-filter-item '
                'data-filter-value="closed">Closed item</div></div>',
                1,
            )
            self.assertNotEqual(nested_collection, original)
            application.write_text(nested_collection, encoding="utf-8")
            nested_collection_check = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            nested_collection_messages = "\n".join(
                row["message"]
                for row in json.loads(nested_collection_check.stdout)["findings"]
            )
            self.assertIn(
                "same-route application collection items cannot contain one another",
                nested_collection_messages,
            )

            orphan_collection = original.replace(
                '<h1 id="fixture-route-3-title">Checkout empty</h1>',
                '<h1 id="fixture-route-3-title">Checkout empty</h1>'
                '<div data-search-item>Orphan search</div>'
                '<div data-filter-item data-filter-value="orphan">'
                'Orphan filter</div>',
                1,
            )
            self.assertNotEqual(orphan_collection, original)
            application.write_text(orphan_collection, encoding="utf-8")
            orphan_collection_check = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            orphan_messages = "\n".join(
                row["message"]
                for row in json.loads(orphan_collection_check.stdout)["findings"]
            )
            self.assertIn(
                "each route with search items must own exactly one application search control",
                orphan_messages,
            )
            self.assertIn(
                "each route with filter items must own exactly one application filter control",
                orphan_messages,
            )

            disclosure_collection = original.replace(
                '<aside id="fixture-settings" hidden data-application-settings ',
                '<aside id="fixture-settings" hidden data-search-item '
                'data-application-settings ',
                1,
            )
            self.assertNotEqual(disclosure_collection, original)
            application.write_text(disclosure_collection, encoding="utf-8")
            disclosure_collection_check = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            disclosure_messages = "\n".join(
                row["message"]
                for row in json.loads(disclosure_collection_check.stdout)["findings"]
            )
            self.assertIn(
                "application collection items cannot also be fixed-runtime disclosure targets",
                disclosure_messages,
            )

            invalid_extra_submit = original.replace(
                '<button type="submit">Submit simulation</button></form>',
                '<button type="submit">Submit simulation</button>'
                '<button type="submit" tabindex="-1" aria-label="&#x200b;" '
                'aria-pressed="true"></button></form>',
                1,
            )
            self.assertNotEqual(invalid_extra_submit, original)
            application.write_text(invalid_extra_submit, encoding="utf-8")
            submit_check = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            submit_messages = "\n".join(
                row["message"]
                for row in json.loads(submit_check.stdout)["findings"]
            )
            self.assertIn(
                "routed forms need only named, enabled, reachable descendant submit controls in sequential keyboard navigation with passive ARIA",
                submit_messages,
            )

            adversarial_runtime_model = (
                original
                .replace(
                    '<div role="listbox" aria-labelledby="fixture-priority-label">',
                    '<div role="listbox" aria-labelledby="fixture-priority-label">'
                    '<button type="button" data-application-action="toggle-pressed" '
                    'aria-pressed="false">Unrelated toggle</button>',
                    1,
                )
                .replace(
                    'data-application-action="toggle-theme"',
                    'accesskey="t" aria-describedby="missing-description" '
                    'data-application-action="toggle-theme"',
                    1,
                )
                .replace(
                    '<nav id="fixture-menu" hidden ',
                    '<nav id="fixture-menu" hidden="until-found" ',
                    1,
                )
                .replace(
                    '<form data-route-target=',
                    '<form tabindex="0" data-route-target=',
                    1,
                )
                .replace(
                    '<label>Simulation note <input required name="simulation-note" '
                    'aria-label="Simulation note"></label>',
                    '<label>Simulation note <input required name="simulation-note" '
                    'aria-label="Simulation note"></label>'
                    '<input type="number" required min="10" max="1" '
                    'aria-label="Impossible number">',
                    1,
                )
                .replace(
                    '<button type="button" data-application-action="return-route">Return</button>',
                    '<button type="button" data-application-action="return-route">Return</button>'
                    '<button type="button" data-application-action="return-route">Duplicate return</button>',
                    1,
                )
                .replace(
                    '<p data-private>This content is privacy-sensitive.</p>',
                    '<p data-private>This content is privacy-sensitive.</p>'
                    '<div role="status" aria-live="assertive">Unmanaged status</div>'
                    '<li data-search-item>Orphan list item</li>'
                    '<progress value="0.5" max="1"></progress>',
                    1,
                )
            )
            self.assertNotEqual(adversarial_runtime_model, original)
            application.write_text(adversarial_runtime_model, encoding="utf-8")
            runtime_model_check = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            runtime_model_messages = "\n".join(
                row["message"]
                for row in json.loads(runtime_model_check.stdout)["findings"]
            )
            for expected_message in (
                "listboxes may contain only direct, text-only canonical option controls",
                "application cannot use unmanaged native invocation behavior: button[accesskey]",
                "application hidden attributes must use only the canonical empty or hidden boolean value",
                "needs exactly one return-route state owner",
                "routed form owners cannot declare tabindex",
                "routed form fields need a type-appropriate, mechanically satisfiable native constraint domain",
                "outside the fixed application-announcer",
                "application collection items must use a non-form browser-stable semantic container",
                "native widgets outside the fixed control model",
                "passive ARIA descriptions need visible scalar text",
            ):
                self.assertIn(expected_message, runtime_model_messages)

            application.write_text(original, encoding="utf-8")
            master = root.parent / "design-system/MASTER.md"
            master_original = master.read_text(encoding="utf-8")
            master.write_text(
                master_original.replace(
                    "```\n<!-- catalog:tokens:end -->",
                    "body { display: none; }\n```\n"
                    "<!-- catalog:tokens:end -->",
                    1,
                ),
                encoding="utf-8",
            )
            injected_token_css = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            injected_token_messages = "\n".join(
                row["message"]
                for row in json.loads(injected_token_css.stdout)["findings"]
            )
            self.assertIn(
                "Design System application token block must contain only exact root, dark-theme and canonical responsive token declarations",
                injected_token_messages,
            )
            master.write_text(master_original, encoding="utf-8")
            master.write_text(
                master_original
                .replace("--catalog-error: #b91c1c;", "--catalog-error: #fff;", 1)
                .replace(
                    "--catalog-motion-easing: ease-out;",
                    "--catalog-motion-easing: potato;",
                    1,
                ),
                encoding="utf-8",
            )
            invalid_token_semantics = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            invalid_token_messages = "\n".join(
                row["message"]
                for row in json.loads(invalid_token_semantics.stdout)["findings"]
            )
            self.assertIn(
                "Design System application root token contrast is below 4.5: --catalog-error / --catalog-background",
                invalid_token_messages,
            )
            self.assertIn(
                "Design System application root token values violate canonical semantic constraints: --catalog-motion-easing",
                invalid_token_messages,
            )
            master.write_text(master_original, encoding="utf-8")
            master.write_text(
                master.read_text(encoding="utf-8").replace(
                    "contract_version: 3", "contract_version: 2", 1
                ),
                encoding="utf-8",
            )
            legacy_design = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            legacy_messages = "\n".join(
                row["message"] for row in json.loads(legacy_design.stdout)["findings"]
            )
            self.assertIn(
                "Experience application requires Design System contract_version 3",
                legacy_messages,
            )

    def test_application_only_update_keeps_process_receipt_unchanged(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            _package, initial_receipts = self.approve_single(root)
            initial_process = next(
                receipt for receipt in initial_receipts
                if receipt["result_ref"] == "checkout@r1"
            )
            self.commit_docs(root, "approve initial application")

            plan, proposal_hash = self.propose_application_only(root)
            self.run_cli(
                "begin-application-revision", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            application = root / "artifacts/application.html"
            application.write_text(
                application.read_text(encoding="utf-8").replace(
                    "This content is privacy-sensitive.",
                    "This revised content is privacy-sensitive.",
                    1,
                ),
                encoding="utf-8",
            )
            self.run_cli(
                "enter-application-review", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            approved = self.approve_experience_set(root, plan, proposal_hash)
            receipts = json.loads(approved.stdout)["receipts"]
            self.assertEqual(
                [receipt["result_ref"] for receipt in receipts],
                ["application@r2", "checkout@r1"],
            )
            current_process = next(
                receipt for receipt in receipts if receipt["result_ref"] == "checkout@r1"
            )
            self.assertEqual(current_process["package_hash"], initial_process["package_hash"])
            status = json.loads(
                self.run_cli("application-status", "--root", root).stdout
            )
            self.assertEqual(status["result_ref"], "application@r2")

    def test_application_revision_rejects_a_stale_scope_proposal(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            self.approve_single(root)
            self.commit_docs(root, "approve initial application")
            plan, proposal_hash = self.propose_application_only(root)
            application = root / "artifacts/application.html"
            application.write_text(
                application.read_text(encoding="utf-8").replace(
                    "This content is privacy-sensitive.", "Out-of-band change", 1
                ),
                encoding="utf-8",
            )

            stale = self.run_cli(
                "begin-application-revision", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
                expected=2,
            )
            self.assertIn("application changed after the scope proposal", stale.stderr)

    def test_application_open_state_rejects_predecessor_revision_replay(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            self.approve_single(root)
            self.commit_docs(root, "approve application r1")
            ledger = root / "_ledger/application-revisions.json"
            original_ledger = ledger.read_bytes()

            plan, proposal_hash = self.propose_application_only(root)
            self.run_cli(
                "begin-application-revision", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            application = root / "artifacts/application.html"
            text = application.read_text(encoding="utf-8").replace(
                "This content is privacy-sensitive.",
                "Replacement using the predecessor revision number",
                1,
            )
            application.write_text(
                experience_application_check.replace_meta(
                    text, "experience-application-revision", "1",
                ),
                encoding="utf-8",
            )

            rejected = self.run_cli(
                "enter-application-review", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
                expected=2,
            )
            self.assertIn("successor revision drifted", rejected.stderr)
            self.assertEqual(ledger.read_bytes(), original_ledger)

    def test_lifecycle_json_rejects_duplicate_scope_state_attestation_and_journal_members(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            docs = root.parent
            self.prepare_inputs(docs)
            plan, proposal_hash = self.propose_manual(
                root, experience="checkout", action="create",
            )
            original_plan = plan.read_text(encoding="utf-8")
            duplicate_plan = original_plan.replace(
                '"application_action": "create",',
                '"application_action": "reuse",\n  '
                '"application_action": "create",',
                1,
            )
            self.assertNotEqual(duplicate_plan, original_plan)
            plan.write_text(duplicate_plan, encoding="utf-8")
            init_args = [
                "init", "--root", root, "--experience", "checkout",
                "--origin-mode", "manual", "--primary-process-ref",
                "business-analysis/erp/processes/checkout-process",
                "--ba-ref", self.stage_receipt(docs, "business-analysis"),
                "--solution-ref", self.stage_receipt(docs, "solution-design"),
                "--design-ref", self.stage_receipt(docs, "design-system"),
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            ]
            rejected_plan = self.run_cli(*init_args, expected=2)
            self.assertIn("duplicate JSON member", rejected_plan.stderr)
            plan.write_text(original_plan, encoding="utf-8")
            package = Path(json.loads(self.run_cli(*init_args).stdout)["path"])
            self.run_cli(
                "stub", "--experience-root", package, "--kind", "journey",
                "--id", "JRN-001", "--slug", "checkout",
            )
            write_application(root)

            state_path = experience_compile.open_application_state_path(root)
            original_state = state_path.read_text(encoding="utf-8")
            duplicate_state = original_state.replace(
                '"phase":"draft"',
                '"phase":"in_review","phase":"draft"',
                1,
            )
            self.assertNotEqual(duplicate_state, original_state)
            state_path.write_text(duplicate_state, encoding="utf-8")
            rejected_state = self.run_cli(
                "render-application", "--root", root, expected=2,
            )
            self.assertIn("duplicate JSON member", rejected_state.stderr)
            state_path.write_text(original_state, encoding="utf-8")

            self.run_cli("enter-review", "--experience-root", package)
            self.run_cli(
                "enter-application-review", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            attestation = self.write_review_attestation(root, proposal_hash)
            original_attestation = attestation.read_text(encoding="utf-8")
            duplicate_attestation = original_attestation.replace(
                '"reviewer_role": "experience-reviewer",',
                '"reviewer_role": "ux-designer",\n  '
                '"reviewer_role": "experience-reviewer",',
                1,
            )
            self.assertNotEqual(duplicate_attestation, original_attestation)
            attestation.write_text(duplicate_attestation, encoding="utf-8")
            rejected_attestation = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", expected=2,
                review_attestation=attestation,
            )
            self.assertIn("duplicate JSON member", rejected_attestation.stderr)

            transaction_id = experience_compile.begin_transaction(
                root, "render-application",
            )
            journal = experience_compile.transaction_journal(root)
            original_journal = journal.read_text(encoding="utf-8")
            duplicate_journal = original_journal.replace(
                '"phase":"prepared"',
                '"phase":"committed","phase":"prepared"',
                1,
            )
            self.assertNotEqual(duplicate_journal, original_journal)
            journal.write_text(duplicate_journal, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON member"):
                experience_compile.read_transaction_journal(root)
            journal.write_text(original_journal, encoding="utf-8")
            experience_compile.rollback_transaction(root, transaction_id)

    def test_historical_application_resolve_fails_closed_on_tampered_ledger(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            self.approve_single(root)
            self.commit_docs(root, "approve application r1")

            plan, proposal_hash = self.propose_application_only(root)
            self.run_cli(
                "begin-application-revision", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            application = root / "artifacts/application.html"
            application.write_text(
                application.read_text(encoding="utf-8").replace(
                    "This content is privacy-sensitive.",
                    "This content is privacy-sensitive in revision two.",
                    1,
                ),
                encoding="utf-8",
            )
            self.run_cli(
                "enter-application-review", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            self.approve_experience_set(root, plan, proposal_hash)

            ledger_path = root / "_ledger/application-revisions.json"
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            ledger["revisions"][0]["application_hash"] = "sha256:" + "0" * 64
            ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

            rejected = self.run_cli(
                "resolve", "--root", root, "--ref", "application@r1",
                expected=1,
            )
            self.assertIn("not resolvable", rejected.stderr)

    def test_generated_application_registry_rejects_duplicate_json_members(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            self.approve_single(root)

            registry = root / "_generated/application-registry.json"
            original = registry.read_text(encoding="utf-8")
            duplicate = original.replace(
                '"schema_version":2',
                '"schema_version":999,"schema_version":2',
                1,
            )
            self.assertNotEqual(duplicate, original)
            registry.write_text(duplicate, encoding="utf-8")

            checked = self.run_cli(
                "check-application", "--root", root, "--gate", "--json",
                expected=1,
            )
            messages = "\n".join(
                row["message"] for row in json.loads(checked.stdout)["findings"]
            )
            self.assertIn(
                "_generated/application-registry.json is missing or unreadable",
                messages,
            )
            _snapshot, snapshot_findings = (
                experience_application_check.approved_snapshot(root)
            )
            self.assertTrue(
                any("duplicate JSON field" in row for row in snapshot_findings),
                snapshot_findings,
            )

    def test_process_ledger_rejects_duplicate_json_members(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, _receipts = self.approve_single(root)
            self.commit_docs(root, "approve process before duplicate ledger")
            plan, proposal_hash = self.propose_manual(
                root, experience="checkout", action="update",
            )
            self.run_cli(
                "begin-revision", "--experience-root", package,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            ledger = package / "_ledger/package-revisions.json"
            original = ledger.read_text(encoding="utf-8")
            duplicate = original.replace(
                '"schema_version":5',
                '"schema_version":4,"schema_version":5',
                1,
            )
            self.assertNotEqual(duplicate, original)
            ledger.write_text(duplicate, encoding="utf-8")

            checked = self.run_cli(
                "check", "--experience-root", package, "--json", expected=1,
            )
            messages = "\n".join(
                row["message"] for row in json.loads(checked.stdout)["findings"]
            )
            self.assertIn("duplicate JSON member", messages)

    def test_application_review_phase_cannot_be_authored_in_html(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, plan, proposal_hash = self.create_authored_single(root)
            self.run_cli("enter-review", "--experience-root", package)
            application = root / "artifacts/application.html"
            application.write_text(
                experience_application_check.replace_meta(
                    application.read_text(encoding="utf-8"),
                    "experience-application-status", "in_review",
                ),
                encoding="utf-8",
            )

            rejected = self.run_cli(
                "approve-set", "--root", root, "--experience", "checkout",
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
                expected=2,
            )
            self.assertIn("open revision", rejected.stderr)

    def test_review_attestation_binds_the_exact_process_package_set(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, _receipts = self.approve_single(root)
            self.commit_docs(root, "approve package r1")
            plan, proposal_hash = self.propose_manual(
                root, experience="checkout", action="update",
            )
            self.run_cli(
                "begin-revision", "--experience-root", package,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            journey = package / "journeys/checkout-journey.md"
            data, body = experience_compile.fm(journey)
            data["revision"] = 2
            data["supersedes"] = "checkout:JRN-001@r1"
            experience_compile.rewrite(
                journey, data, body.rstrip() + "\n\nReviewed package wording.\n",
            )
            self.enter_reviews(root, [package], plan, proposal_hash)
            attestation = self.write_review_attestation(root, proposal_hash)

            data, body = experience_compile.fm(journey)
            experience_compile.rewrite(
                journey, data,
                body.rstrip() + "\n\nChanged after reviewer attestation.\n",
            )
            self.run_cli("render", "--experience-root", package)
            self.run_cli("render-application", "--root", root)
            rejected = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", expected=2,
                review_attestation=attestation,
            )
            self.assertIn("stale for the current application", rejected.stderr)

    def test_reuse_returns_current_receipts_without_review_attestation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            self.approve_single(root)
            self.commit_docs(root, "approve reusable application")
            plan, proposal_hash = self.propose_manual(
                root, experience="checkout", action="reuse",
            )

            reused = self.run_cli(
                "approve-set", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            self.assertEqual(
                [row["result_ref"] for row in json.loads(reused.stdout)["receipts"]],
                ["application@r1", "checkout@r1"],
            )

    def test_process_death_recovers_the_exact_pre_transaction_tree(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, _receipts = self.approve_single(root)
            self.commit_docs(root, "approve package before crash")
            plan, proposal_hash = self.propose_manual(
                root, experience="checkout", action="update",
            )
            before = tree_snapshot(root)
            map_path = root.parent / "maps/experience-design.md"
            before_map = map_path.read_bytes() if map_path.is_file() else None
            crash_program = (
                "import os, sys\n"
                "from pathlib import Path\n"
                "sys.path.insert(0, str(Path.cwd() / "
                "'plugins/software-engineering-team/scripts'))\n"
                "import experience_compile\n"
                "original = experience_compile.archive_process_registry\n"
                "def crash_after_archive(*args, **kwargs):\n"
                "    original(*args, **kwargs)\n"
                "    os._exit(91)\n"
                "experience_compile.archive_process_registry = crash_after_archive\n"
                "raise SystemExit(experience_compile.main(sys.argv[1:]))\n"
            )
            crashed = subprocess.run(
                [
                    sys.executable, "-c", crash_program,
                    "begin-revision", "--experience-root", str(package),
                    "--scope-plan", str(plan),
                    "--proposal-hash", proposal_hash,
                ],
                cwd=ROOT, capture_output=True, text=True, check=False,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            )
            self.assertEqual(
                crashed.returncode, 91, crashed.stdout + crashed.stderr,
            )
            journal = experience_compile.transaction_journal(root)
            self.assertTrue(journal.is_file())
            self.assertNotEqual(tree_snapshot(root), before)

            recovered = self.run_cli(
                "application-status", "--root", root,
            )
            self.assertEqual(json.loads(recovered.stdout)["result_ref"], "application@r1")
            self.assertFalse(journal.exists())
            self.assertEqual(tree_snapshot(root), before)
            if before_map is None:
                self.assertFalse(map_path.exists())
            else:
                self.assertEqual(map_path.read_bytes(), before_map)

    def test_concurrent_mutations_serialize_without_erasing_the_winner(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, _receipts = self.approve_single(root)
            self.commit_docs(root, "approve package before concurrent mutation")
            plan, proposal_hash = self.propose_manual(
                root, experience="checkout", action="update",
            )
            signal = Path(raw) / "first-writer-holds-lock"
            delayed_program = (
                "import os, sys, time\n"
                "from pathlib import Path\n"
                "sys.path.insert(0, str(Path.cwd() / "
                "'plugins/software-engineering-team/scripts'))\n"
                "import experience_compile\n"
                "original = experience_compile.archive_process_registry\n"
                "def delayed_archive(*args, **kwargs):\n"
                "    result = original(*args, **kwargs)\n"
                "    Path(os.environ['EXPERIENCE_LOCK_SIGNAL']).write_text('ready')\n"
                "    time.sleep(1.5)\n"
                "    return result\n"
                "experience_compile.archive_process_registry = delayed_archive\n"
                "raise SystemExit(experience_compile.main(sys.argv[1:]))\n"
            )
            first = subprocess.Popen(
                [
                    sys.executable, "-c", delayed_program,
                    "begin-revision", "--experience-root", str(package),
                    "--scope-plan", str(plan),
                    "--proposal-hash", proposal_hash,
                ],
                cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True,
                env={
                    **os.environ,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "EXPERIENCE_LOCK_SIGNAL": str(signal),
                },
            )
            try:
                deadline = time.monotonic() + 10
                while (
                    not signal.is_file()
                    and first.poll() is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.05)
                if not signal.is_file():
                    stdout, stderr = first.communicate(timeout=5)
                    self.fail(
                        "first writer did not enter its locked mutation: "
                        + stdout + stderr
                    )
                self.assertIsNone(first.poll())
                second = self.run_cli(
                    "begin-revision", "--experience-root", package,
                    "--scope-plan", plan, "--proposal-hash", proposal_hash,
                    expected=2,
                )
                stdout, stderr = first.communicate(timeout=10)
                self.assertEqual(first.returncode, 0, stdout + stderr)
            finally:
                if first.poll() is None:
                    first.kill()
                    first.communicate()

            self.assertIn("only an approved Experience", second.stderr)
            package_data = experience_compile.fields(package)
            self.assertEqual(
                (package_data["status"], package_data["revision"]),
                ("draft", 2),
            )
            application_meta = experience_compile.application_metadata(root)
            self.assertEqual(
                (
                    application_meta["experience-application-status"],
                    application_meta["experience-application-revision"],
                ),
                ("draft", "2"),
            )
            open_state = experience_compile.read_open_application_state(root)
            self.assertEqual(open_state["phase"], "draft")
            self.assertEqual(open_state["proposal_hash"], proposal_hash)
            self.assertFalse(experience_compile.transaction_journal(root).exists())

    def test_committed_root_symlink_is_rejected_before_resolution(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            self.approve_single(root)
            self.commit_docs(root, "approve application before root symlink")
            real_root = root.with_name("experience-design-real")
            root.rename(real_root)
            try:
                root.symlink_to(real_root.name, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")
            self.commit_docs(root, "commit lexical Experience root symlink")

            with self.assertRaisesRegex(ValueError, "non-symlink"):
                experience_compile.root_for(root)
            rejected = self.run_cli(
                "application-status", "--root", root, expected=2,
            )
            self.assertIn("non-symlink", rejected.stderr)

    def test_committed_package_symlink_is_rejected_before_resolution(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, _receipts = self.approve_single(root)
            self.commit_docs(root, "approve package before package symlink")
            real_package = package.with_name("checkout-real")
            package.rename(real_package)
            try:
                package.symlink_to(real_package.name, target_is_directory=True)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"directory symlinks are unavailable: {exc}")
            self.commit_docs(root, "commit lexical Experience package symlink")

            with self.assertRaisesRegex(ValueError, "non-symlink"):
                experience_compile.package_for(package)
            rejected = self.run_cli(
                "status", "--experience-root", package, expected=2,
            )
            self.assertIn("non-symlink", rejected.stderr)

    def test_stage_candidates_reject_symlinked_experience_owner_chain(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            workspace = project / "workspace"
            docs = workspace / "docs"
            root = docs / "experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(docs)
            self.approve_single(root)
            self.commit_docs(root, "approve application before selector checks")
            self.assertIn(
                "application@r1",
                [
                    row["result_ref"]
                    for row in self.stage_candidates(docs, "experience-design")
                ],
            )

            def rejected_candidates(selector):
                result = subprocess.run(
                    [
                        sys.executable, str(STAGES), "candidates",
                        "--docs", str(selector),
                        "--stage", "experience-design", "--json",
                    ],
                    cwd=ROOT, capture_output=True, text=True, check=False,
                )
                self.assertIn(
                    result.returncode, (0, 1), result.stdout + result.stderr,
                )
                payload = json.loads(result.stdout)
                self.assertEqual(payload.get("candidates", []), [])
                if result.returncode:
                    self.assertTrue(payload.get("errors"), payload)

            cases = (
                ("workspace", project, workspace),
                ("docs", workspace, docs),
                ("experience-design", docs, root),
            )
            for label, selector, target in cases:
                with self.subTest(owner=label):
                    real = target.with_name(target.name + "-real")
                    target.rename(real)
                    try:
                        target.symlink_to(real.name, target_is_directory=True)
                    except (NotImplementedError, OSError) as exc:
                        real.rename(target)
                        self.skipTest(f"directory symlinks are unavailable: {exc}")
                    try:
                        rejected_candidates(selector)
                        if label == "workspace":
                            rejected_candidates(target.resolve() / "docs")
                        elif label == "docs":
                            rejected_candidates(target.resolve())
                    finally:
                        target.unlink()
                        real.rename(target)

    def test_historical_delivery_survives_an_open_application_revision(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            docs = root.parent
            self.prepare_inputs(docs)
            _package, receipts = self.approve_interaction_experience(root)
            self.assertEqual(
                [row["result_ref"] for row in receipts],
                ["application@r1", "checkout@r1"],
            )
            self.commit_docs(root, "approve interaction application revision one")
            self.approve_manual_backlog(
                docs, "application@r1", "checkout@r1",
                ["checkout:SCR-001@r1", "checkout:STA-001@r1"],
            )
            self.commit_docs(root, "approve backlog pinned to application revision one")

            plan, proposal_hash = self.propose_application_only(root)
            self.run_cli(
                "begin-application-revision", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )

            for ref in ("application@r1", "checkout@r1"):
                with self.subTest(ref=ref):
                    candidate, errors = stage_package.verify(
                        docs, "experience-design", ref,
                        allow_historical=True, require_committed=True,
                    )
                    self.assertEqual(errors, [])
                    self.assertEqual(candidate["result_ref"], ref)
                    self.assertEqual(candidate["verification_profile"], "historical")
            self.assertEqual(
                stage_package.experience_application_process_refs(
                    docs, "application@r1", allow_historical=True,
                ),
                ["checkout@r1"],
            )
            strict_candidate, strict_errors = stage_package.verify(
                docs, "experience-design", "application@r1",
            )
            self.assertIsNone(strict_candidate)
            self.assertTrue(strict_errors)

            record_errors = []
            backlog_compile.validate_experience_ref(
                docs, "checkout:SCR-001@r1", "frozen story",
                record_errors, require_current=False,
            )
            self.assertEqual(record_errors, [])
            _historical_record, historical_findings = backlog_compile.collect(
                docs, historical_inputs=True,
            )
            self.assertEqual(historical_findings, [])
            _strict_record, strict_findings = backlog_compile.collect(docs)
            self.assertTrue(strict_findings)

            selected, _backlog, delivery_errors = (
                delivery_compile.approved_backlog_sources(
                    docs, ["CHK-01"], historical_inputs=True,
                )
            )
            self.assertEqual(delivery_errors, [])
            self.assertEqual(set(selected), {"CHK-01"})
            _selected, _backlog, new_scope_errors = (
                delivery_compile.approved_backlog_sources(
                    docs, ["CHK-01"], historical_inputs=False,
                )
            )
            self.assertTrue(new_scope_errors)

    def test_requirement_revision_survives_upstream_design_drift(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            docs = root.parent
            self.prepare_inputs(docs)
            self.approve_interaction_experience(root)
            self.commit_docs(root, "approve reusable application revision one")

            requirement = requirement_compile.create_requirement(
                docs, "checkout-reuse", "Checkout reuse", "feature", "normal",
                "REQ-001", [],
            )
            props, body = requirement_compile.split_note(requirement)
            for old, new in {
                "TODO: state the requested change and who needs it.":
                    "Customers need the approved checkout interaction reused.",
                "TODO: state the observable outcome and acceptance boundary.":
                    "The Requirement binds the exact application process set.",
                "TODO: define included and excluded behavior.":
                    "Reuse checkout while excluding unrelated interaction changes.",
                "TODO: record evidence, constraints and urgency rationale.":
                    "The committed revision-one ledgers are immutable evidence.",
                "TODO: explain why this stage must change.":
                    "The stage preserves its approved handoff boundary.",
            }.items():
                body = body.replace(old, new)
            body = body.replace(
                "| experience-design | required |  |",
                "| experience-design | reuse | application@r1, checkout@r1 |",
                1,
            )
            requirement.write_text(
                requirement_compile.render_note(props, body), encoding="utf-8",
            )
            requirement_compile.approve_requirement(requirement)
            self.commit_docs(root, "approve Requirement pinned to revision one")

            self.approve_v3_design_revision(root)
            strict = requirement_compile.requirement_findings(
                requirement, require_approved=True,
            )
            self.assertTrue(
                any("experience-design reuse" in finding for finding in strict),
                strict,
            )
            for ref in ("application@r1", "checkout@r1"):
                candidate, errors = stage_package.verify(
                    docs, "experience-design", ref, allow_historical=True,
                )
                self.assertEqual(errors, [])
                self.assertIsNotNone(candidate)
            record_errors = []
            backlog_compile.validate_experience_ref(
                docs, "checkout:SCR-001@r1", "historical story",
                record_errors, require_current=False,
            )
            self.assertEqual(record_errors, [])

            requirement_compile.begin_revision(requirement)
            revised, revised_body = requirement_compile.split_note(requirement)
            self.assertEqual((revised["status"], revised["revision"]), ("draft", 2))
            self.assertEqual(requirement_compile.stage_results(revised_body), {})

    def test_hard_linked_draft_source_cannot_escape_enter_review(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, _plan, _proposal_hash = self.create_authored_single(root)
            note = package / "experience.md"
            external = Path(raw) / "external-experience.md"
            external.write_bytes(note.read_bytes())
            note.unlink()
            try:
                os.link(external, note)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"hard links are unavailable: {exc}")
            before = external.read_bytes()

            rejected = self.run_cli(
                "enter-review", "--experience-root", package, expected=2,
            )
            self.assertIn("non-hard-linked", rejected.stderr)
            self.assertEqual(external.read_bytes(), before)
            self.assertEqual(note.read_bytes(), before)
            self.assertEqual(os.lstat(note).st_nlink, 2)

    def test_hard_linked_approved_source_cannot_escape_begin_revision(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, _receipts = self.approve_single(root)
            self.commit_docs(root, "approve before hard-link revision attempt")
            plan, proposal_hash = self.propose_manual(
                root, experience="checkout", action="update",
            )
            note = package / "experience.md"
            external = Path(raw) / "external-approved-experience.md"
            external.write_bytes(note.read_bytes())
            note.unlink()
            try:
                os.link(external, note)
            except (NotImplementedError, OSError) as exc:
                self.skipTest(f"hard links are unavailable: {exc}")
            before = external.read_bytes()

            rejected = self.run_cli(
                "begin-revision", "--experience-root", package,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
                expected=2,
            )
            self.assertIn("non-hard-linked", rejected.stderr)
            self.assertEqual(external.read_bytes(), before)
            self.assertEqual(note.read_bytes(), before)
            self.assertEqual(os.lstat(note).st_nlink, 2)

    def test_approval_rolls_back_package_and_application_atomically(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, plan, proposal_hash = self.create_authored_single(root)
            self.run_cli("enter-review", "--experience-root", package)
            self.run_cli(
                "enter-application-review", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            application = root / "artifacts/application.html"
            application.write_text(
                application.read_text(encoding="utf-8").replace(
                    "</section>",
                    '<img src="https://example.invalid/late.png" alt="">\n    </section>',
                    1,
                ),
                encoding="utf-8",
            )
            before = tree_snapshot(root)
            map_path = root.parent / "maps/experience-design.md"
            before_map = map_path.read_bytes() if map_path.is_file() else None

            rejected = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", expected=1,
            )
            self.assertIn("approve-set rolled back", rejected.stderr)
            self.assertEqual(tree_snapshot(root), before)
            if before_map is None:
                self.assertFalse(map_path.exists())
            else:
                self.assertEqual(map_path.read_bytes(), before_map)
            self.assertIn(
                "status: in_review",
                (package / "experience.md").read_text(encoding="utf-8"),
            )

    def test_approval_requires_the_application_review_gate(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, plan, proposal_hash = self.create_authored_single(root)
            self.run_cli("enter-review", "--experience-root", package)
            before = tree_snapshot(root)

            rejected = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", expected=2,
            )
            self.assertIn("application must be in_review", rejected.stderr)
            self.assertEqual(tree_snapshot(root), before)

    def test_approval_requires_current_reviewer_attestation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, plan, proposal_hash = self.create_authored_single(root)
            self.run_cli("enter-review", "--experience-root", package)
            self.run_cli(
                "enter-application-review", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )

            missing = self.run_cli(
                "approve-set", "--root", root, "--experience", "checkout",
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
                expected=2,
            )
            self.assertIn("--review-attestation", missing.stderr)

            wrong_role = self.write_review_attestation(
                root, proposal_hash, reviewer_role="ux-designer",
            )
            rejected_role = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", expected=2,
                review_attestation=wrong_role,
            )
            self.assertIn("must come from experience-reviewer", rejected_role.stderr)

            blocked = self.write_review_attestation(
                root, proposal_hash, blockers=["Rendered fidelity is incomplete."],
            )
            rejected_blocker = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", expected=2,
                review_attestation=blocked,
            )
            self.assertIn("zero blockers", rejected_blocker.stderr)

            stale = self.write_review_attestation(
                root, proposal_hash, source_hash="sha256:" + "0" * 64,
            )
            rejected_stale = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", expected=2,
                review_attestation=stale,
            )
            self.assertIn("stale for the current application", rejected_stale.stderr)

            stale_application = self.write_review_attestation(
                root, proposal_hash, application_hash="sha256:" + "0" * 64,
            )
            rejected_application = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", expected=2,
                review_attestation=stale_application,
            )
            self.assertIn(
                "stale for the current application",
                rejected_application.stderr,
            )

            stale_revision = self.write_review_attestation(
                root, proposal_hash, application_revision=99,
            )
            rejected_revision = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", expected=2,
                review_attestation=stale_revision,
            )
            self.assertIn("current application revision", rejected_revision.stderr)

            wrong_status = self.write_review_attestation(
                root, proposal_hash, application_status="approved",
            )
            rejected_status = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", expected=2,
                review_attestation=wrong_status,
            )
            self.assertIn("must be in_review", rejected_status.stderr)

            old_review = self.write_review_attestation(
                root, proposal_hash,
                reviewed_at_utc=(datetime.now(timezone.utc) - timedelta(hours=25))
                .isoformat(),
            )
            rejected_old = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", expected=2,
                review_attestation=old_review,
            )
            self.assertIn("older than 24 hours", rejected_old.stderr)

            future_review = self.write_review_attestation(
                root, proposal_hash,
                reviewed_at_utc=(datetime.now(timezone.utc) + timedelta(minutes=1))
                .isoformat(),
            )
            rejected_future = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", expected=2,
                review_attestation=future_review,
            )
            self.assertIn("cannot be in the future", rejected_future.stderr)

            wrong_proposal = self.write_review_attestation(
                root, proposal_hash,
                attested_proposal_hash="sha256:" + "1" * 64,
            )
            rejected_proposal = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", expected=2,
                review_attestation=wrong_proposal,
            )
            self.assertIn("another scope proposal", rejected_proposal.stderr)

            self.approve_experience_set(root, plan, proposal_hash, "checkout")

    def test_approval_rejects_open_action_identity_and_revision_drift(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, plan, proposal_hash = self.create_authored_single(root)
            self.run_cli("enter-review", "--experience-root", package)
            self.run_cli(
                "enter-application-review", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )

            state_path = package / "_generated/open-revision.json"
            original_state = state_path.read_bytes()
            state = json.loads(original_state)
            state["target_experience"] = "returns"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            tampered_open = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", expected=1,
            )
            self.assertIn("open revision", tampered_open.stderr)
            state_path.write_bytes(original_state)

            data, body = experience_compile.fm(package / "experience.md")
            data["primary_process_ref"] = (
                "business-analysis/erp/processes/returns-process"
            )
            data["origin_mode"] = "requirement"
            data["implements"] = ["REQ-999"]
            data["upstream_stage_receipts_hash"] = "sha256:" + "2" * 64
            data["revision"] = 7
            experience_compile.rewrite(package / "experience.md", data, body)
            drifted = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", expected=1,
            )
            self.assertIn("lifecycle identity, phase or successor revision", drifted.stderr)

    def test_begin_revision_rebinds_to_a_newly_approved_design_receipt(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, create_plan, proposal_hash = self.init_manual(root)
            self.run_cli("stub", "--experience-root", package, "--kind", "journey",
                         "--id", "JRN-001", "--slug", "checkout")
            self.enter_reviews(root, [package], create_plan, proposal_hash)
            self.approve_experience_set(
                root, create_plan, proposal_hash, "checkout",
            )
            self.commit_docs(root, "approve initial experience")

            old_design = self.stage_candidate(root.parent, "design-system")["package_hash"]
            self.approve_v3_design_revision(root)
            new_design = self.stage_candidate(root.parent, "design-system")["package_hash"]
            self.assertNotEqual(old_design, new_design)
            plan, proposal_hash = self.propose_manual(root, experience="checkout", action="update")

            self.run_cli("begin-revision", "--experience-root", package,
                         "--scope-plan", plan, "--proposal-hash", proposal_hash)
            self.run_cli("begin-application-revision", "--root", root,
                         "--scope-plan", plan, "--proposal-hash", proposal_hash)
            revised = (package / "experience.md").read_text(encoding="utf-8")
            self.assertIn("status: draft", revised)
            self.assertIn("revision: 2", revised)
            self.assertIn(f"design-system|design-system/MASTER|{new_design}", revised)
            self.assertNotIn(f"design-system|design-system/MASTER|{old_design}", revised)

            self.enter_reviews(root, [package], plan, proposal_hash)
            self.approve_experience_set(root, plan, proposal_hash, "checkout")
            checked = self.run_cli("check", "--experience-root", package, "--gate", "--json")
            self.assertTrue(json.loads(checked.stdout)["ok"])

    def test_generated_relations_preserve_approved_stage_receipts(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, create_plan, proposal_hash = self.init_manual(root)
            self.run_cli("stub", "--experience-root", package, "--kind", "journey",
                         "--id", "JRN-001", "--slug", "checkout")
            self.enter_reviews(root, [package], create_plan, proposal_hash)
            self.approve_experience_set(
                root, create_plan, proposal_hash, "checkout",
            )

            stages = ("business-analysis", "solution-design", "design-system",
                      "experience-design")
            before = {
                stage: self.stage_candidate(root.parent, stage)["package_hash"]
                for stage in stages
            }
            relation = (
                "\n\n## Related knowledge <!-- sec: relations:generated:start -->\n\n"
                "- Used by: [[experience-design/experiences/checkout/experience|Checkout]]\n\n"
                "<!-- sec: relations:generated:end -->\n"
            )
            for path in (
                root.parent / "business-analysis/erp/processes/checkout-process.md",
                root.parent / "solution-design/landscape.md",
                root.parent / "design-system/MASTER.md",
                package / "experience.md",
            ):
                path.write_text(path.read_text(encoding="utf-8").rstrip() + relation,
                                encoding="utf-8")

            check = self.run_cli("check", "--experience-root", package, "--gate", "--json")
            self.assertTrue(json.loads(check.stdout)["ok"])
            for stage in stages:
                candidate = self.stage_candidate(root.parent, stage)
                self.assertTrue(candidate["current"])
                self.assertEqual(candidate["package_hash"], before[stage])

    def test_nested_domain_primary_and_related_processes_use_the_same_resolver(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            nested = "business-analysis/erp/domains/inventory/processes/goods-receipt-process.md"
            related = "business-analysis/erp/processes/checkout-process.md"
            plan, proposal_hash = self.propose_manual(
                root, nested, experience="goods-receipt")
            docs = root.parent
            payload = json.loads(self.run_cli(
                "init", "--root", root, "--experience", "goods-receipt",
                "--origin-mode", "manual", "--primary-process-ref", nested,
                "--related-process-ref", related,
                "--ba-ref", self.stage_receipt(docs, "business-analysis"),
                "--solution-ref", self.stage_receipt(docs, "solution-design"),
                "--design-ref", self.stage_receipt(docs, "design-system"),
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            ).stdout)
            experience = Path(payload["path"])
            fields = experience.joinpath("experience.md").read_text(encoding="utf-8")
            self.assertIn(
                "primary_process_ref: business-analysis/erp/domains/inventory/processes/goods-receipt-process",
                fields,
            )
            self.assertIn("related_process_refs:", fields)
            self.assertIn("business-analysis/erp/processes/checkout-process", fields)
            checked = self.run_cli("check", "--experience-root", experience, "--json")
            self.assertTrue(json.loads(checked.stdout)["ok"])

    def test_second_active_experience_for_process_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, create_plan, create_hash = self.init_manual(root)
            self.run_cli("stub", "--experience-root", package, "--kind", "journey",
                         "--id", "JRN-001", "--slug", "checkout")
            self.enter_reviews(root, [package], create_plan, create_hash)
            self.approve_experience_set(
                root, create_plan, create_hash, "checkout",
            )
            self.commit_docs(root, "approve first Experience owner")
            proposal = subprocess.run(
                [sys.executable, str(COMPILER), "propose", "--root", str(root),
                 "--process-ref", "business-analysis/erp/processes/checkout-process", "--experience", "checkout-v2",
                 "--action", "create", "--origin-mode", "manual",
                 "--ba-ref", self.stage_receipt(root.parent, "business-analysis"),
                 "--solution-ref", self.stage_receipt(root.parent, "solution-design"),
                 "--design-ref", self.stage_receipt(root.parent, "design-system")],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertEqual(proposal.returncode, 0, proposal.stdout + proposal.stderr)
            plan = root.parent / ".scope-plan-second.json"
            plan.write_text(proposal.stdout, encoding="utf-8")
            result = self.run_cli("init", "--root", root, "--experience", "checkout-v2", "--origin-mode", "manual",
                                  "--primary-process-ref", "business-analysis/erp/processes/checkout-process", "--ba-ref", self.stage_receipt(root.parent, "business-analysis"),
                                  "--solution-ref", self.stage_receipt(root.parent, "solution-design"), "--design-ref", self.stage_receipt(root.parent, "design-system"),
                                  "--scope-plan", plan, "--proposal-hash", json.loads(proposal.stdout)["proposal_hash"], expected=2)
            self.assertIn("already owns", result.stderr)

    def test_create_rejects_exp_prefix_and_missing_scope_proposal(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            common = ["init", "--root", root, "--origin-mode", "manual",
                      "--primary-process-ref", "business-analysis/erp/processes/checkout-process",
                      "--ba-ref", self.stage_receipt(root.parent, "business-analysis"),
                      "--solution-ref", self.stage_receipt(root.parent, "solution-design"),
                      "--design-ref", self.stage_receipt(root.parent, "design-system"),
                      "--scope-plan", root.parent / ".missing-scope-plan.json"]
            legacy = self.run_cli(*common, "--experience", "exp-checkout",
                                  "--proposal-hash", "sha256:" + "0" * 64,
                                  expected=2)
            self.assertIn("must not use exp-", legacy.stderr)
            missing = self.run_cli(*common, "--experience", "checkout",
                                   "--proposal-hash", "sha256:" + "0" * 64,
                                   expected=2)
            self.assertIn("scope plan", missing.stderr)

    def test_rename_uses_a_confirmed_proposal_and_preserves_alias(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, create_plan, create_hash = self.init_manual(root)
            self.run_cli("stub", "--experience-root", package, "--kind", "journey",
                         "--id", "JRN-001", "--slug", "checkout",
                         "--supersedes", "checkout:JRN-001@r1")
            self.enter_reviews(root, [package], create_plan, create_hash)
            self.approve_experience_set(
                root, create_plan, create_hash, "checkout",
            )
            self.commit_docs(root, "approve Experience before rename")
            plan, proposal_hash = self.propose_manual(root, experience="checkout", action="rename", to="purchase")
            self.run_cli("rename", "--experience-root", package, "--to", "purchase",
                         "--scope-plan", plan, "--proposal-hash", proposal_hash)
            renamed = root / "experiences/purchase"
            self.assertTrue(renamed.is_dir())
            self.assertFalse(package.exists())
            self.assertIn('"checkout"', (renamed / "experience.md").read_text(encoding="utf-8"))
            journey = (renamed / "journeys/checkout-journey.md").read_text(encoding="utf-8")
            self.assertIn("supersedes: checkout:JRN-001@r1", journey)

    def test_rename_opens_package_with_internal_exact_refs(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, create_plan, create_hash = self.init_manual(root)
            self.run_cli(
                "stub", "--experience-root", package, "--kind", "journey",
                "--id", "JRN-001", "--slug", "checkout",
            )
            self.run_cli(
                "stub", "--experience-root", package, "--kind", "flow-set",
                "--id", "FLW-001", "--slug", "checkout",
                "--journey-refs", "checkout:JRN-001@r1",
            )
            self.enter_reviews(root, [package], create_plan, create_hash)
            self.approve_experience_set(
                root, create_plan, create_hash, "checkout",
            )
            self.commit_docs(root, "approve self-linked Experience")

            plan, proposal_hash = self.propose_manual(
                root, experience="checkout", action="rename", to="purchase",
            )
            self.run_cli(
                "rename", "--experience-root", package, "--to", "purchase",
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            renamed = root / "experiences/purchase"
            flow = renamed / "flows/checkout-flow-set.md"
            self.assertIn("purchase:JRN-001@r1", flow.read_text(encoding="utf-8"))
            incomplete = self.run_cli(
                "enter-review", "--experience-root", renamed, expected=1,
            )
            self.assertIn("changed record must increment revision", incomplete.stdout)

            flow_data, flow_body = experience_compile.fm(flow)
            flow_data["revision"] = 2
            flow_data["supersedes"] = "checkout:FLW-001@r1"
            experience_compile.rewrite(flow, flow_data, flow_body)
            self.enter_reviews(root, [renamed], plan, proposal_hash)
            self.approve_experience_set(
                root, plan, proposal_hash, "purchase",
            )
            historic = json.loads(self.run_cli(
                "resolve", "--root", root, "--ref", "checkout@r1",
            ).stdout)
            self.assertFalse(historic["current"])

    def test_process_ledger_tamper_blocks_gates_and_historical_resolve(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, _receipts = self.approve_single(root)
            self.commit_docs(root, "approve Experience r1")
            self.approve_journey_revision(root, package, 2)
            self.commit_docs(root, "approve Experience r2")
            self.approve_journey_revision(root, package, 3)

            ledger_path = package / "_ledger/package-revisions.json"
            original = json.loads(ledger_path.read_text(encoding="utf-8"))
            mutations = {}
            schema = json.loads(json.dumps(original))
            schema["revisions"][0]["schema_version"] = 4
            mutations["schema"] = schema
            digest = json.loads(json.dumps(original))
            digest["revisions"][0]["package_hash"] = "sha256:" + "f" * 64
            mutations["hash"] = digest
            order = json.loads(json.dumps(original))
            order["revisions"].reverse()
            mutations["order"] = order
            gap = json.loads(json.dumps(original))
            gap["revisions"] = gap["revisions"][1:]
            mutations["contiguity"] = gap
            self_consistent = json.loads(json.dumps(original))
            forged = self_consistent["revisions"][0]
            forged["source_hash"] = "sha256:" + "e" * 64
            forged["package_hash"] = experience_compile.sha(
                experience_compile.canonical({
                    "source_hash": forged["source_hash"],
                    "registry_hash": forged["registry_hash"],
                })
            )
            mutations["self-consistent-unpublished-hash"] = self_consistent

            for label, value in mutations.items():
                with self.subTest(label=label):
                    ledger_path.write_text(json.dumps(value), encoding="utf-8")
                    self.run_cli(
                        "check", "--experience-root", package, "--gate", "--json",
                        expected=1,
                    )
                    self.run_cli(
                        "check-application", "--root", root, "--gate", "--json",
                        expected=1,
                    )
                    self.run_cli(
                        "resolve", "--root", root, "--ref", "checkout@r1",
                        expected=1,
                    )
            ledger_path.write_text(json.dumps(original), encoding="utf-8")
            current = json.loads(self.run_cli(
                "check", "--experience-root", package, "--gate", "--json",
            ).stdout)
            self.assertTrue(current["ok"])
            historical = json.loads(self.run_cli(
                "resolve", "--root", root, "--ref", "checkout@r1",
            ).stdout)
            self.assertFalse(historical["current"])

    def test_resolve_does_not_promote_open_or_retirement_revisions(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, plan, proposal_hash = self.init_manual(root)
            self.run_cli(
                "stub", "--experience-root", package, "--kind", "journey",
                "--id", "JRN-001", "--slug", "checkout",
            )
            self.run_cli("render", "--experience-root", package)
            self.run_cli(
                "resolve", "--root", root, "--ref", "checkout@r1", expected=1,
            )
            self.run_cli("enter-review", "--experience-root", package)
            self.run_cli(
                "resolve", "--root", root, "--ref", "checkout@r1", expected=1,
            )
            write_application(root)
            self.run_cli(
                "enter-application-review", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            self.approve_experience_set(root, plan, proposal_hash, "checkout")
            self.commit_docs(root, "approve before retirement preparation")

            retire_plan, retire_hash = self.propose_manual(
                root, experience="checkout", action="retire",
            )
            self.run_cli(
                "retire", "--experience-root", package,
                "--scope-plan", retire_plan, "--proposal-hash", retire_hash,
            )
            self.run_cli(
                "resolve", "--root", root, "--ref", "checkout@r2", expected=1,
            )
            historical = json.loads(self.run_cli(
                "resolve", "--root", root, "--ref", "checkout@r1",
            ).stdout)
            self.assertFalse(historical["current"])

    def test_scope_plan_rejects_upstream_drift_before_create(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            plan, proposal_hash = self.propose_manual(root, experience="checkout")
            process = root.parent / "business-analysis/erp/processes/checkout-process.md"
            process.write_text(process.read_text(encoding="utf-8") + "\nChanged\n", encoding="utf-8")
            result = self.run_cli(
                "init", "--root", root, "--experience", "checkout", "--origin-mode", "manual",
                "--primary-process-ref", "business-analysis/erp/processes/checkout-process",
                "--ba-ref", self.stage_receipt(root.parent, "business-analysis"),
                "--solution-ref", self.stage_receipt(root.parent, "solution-design"),
                "--design-ref", self.stage_receipt(root.parent, "design-system"),
                "--scope-plan", plan, "--proposal-hash", proposal_hash, expected=2,
            )
            self.assertIn("business-analysis", result.stderr)

    def test_multi_experience_approval_requires_the_complete_scope_set(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            docs = root.parent
            plan = root.parent / ".scope-plan.json"
            # Construct the true multi-action proposal directly.
            proposal = subprocess.run([
                sys.executable, str(COMPILER), "propose", "--root", str(root),
                "--process-ref", "business-analysis/erp/processes/checkout-process",
                "--process-ref", "business-analysis/erp/processes/returns-process",
                "--origin-mode", "manual", "--ba-ref", self.stage_receipt(docs, "business-analysis"),
                "--solution-ref", self.stage_receipt(docs, "solution-design"),
                "--design-ref", self.stage_receipt(docs, "design-system"),
            ], cwd=ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(proposal.returncode, 0, proposal.stdout + proposal.stderr)
            plan.write_text(proposal.stdout, encoding="utf-8")
            proposal_hash = json.loads(proposal.stdout)["proposal_hash"]
            for experience, process in (("checkout", "checkout"), ("returns", "returns")):
                related = "returns" if experience == "checkout" else "checkout"
                self.run_cli(
                    "init", "--root", root, "--experience", experience, "--origin-mode", "manual",
                    "--primary-process-ref", f"business-analysis/erp/processes/{process}-process",
                    "--related-process-ref", f"business-analysis/erp/processes/{related}-process",
                    "--ba-ref", self.stage_receipt(docs, "business-analysis"),
                    "--solution-ref", self.stage_receipt(docs, "solution-design"),
                    "--design-ref", self.stage_receipt(docs, "design-system"),
                    "--scope-plan", plan, "--proposal-hash", proposal_hash,
                )
                package = root / "experiences" / experience
                self.run_cli("stub", "--experience-root", package, "--kind", "journey",
                             "--id", "JRN-001", "--slug", experience,
                             "--related-to", f"{related}:JRN-001@r1")
                if experience == "checkout":
                    self.run_cli("stub", "--experience-root", package,
                                 "--kind", "transition", "--id", "TRN-001",
                                 "--slug", "open-returns")
            checkout = root / "experiences/checkout"
            returns = root / "experiences/returns"
            self.enter_reviews(
                root, [checkout, returns], plan, proposal_hash,
                transitions={
                    "checkout:TRN-001@r1": {
                        "target": "#/returns",
                        "preserve_context": ["cart_id"],
                        "return_route": "#/checkout",
                    },
                },
            )
            application = json.loads(
                self.run_cli("check-application", "--root", root, "--json").stdout
            )["application"]
            self.assertEqual(
                application["coverage"]["record_refs"],
                [
                    "checkout:JRN-001@r1",
                    "checkout:TRN-001@r1",
                    "returns:JRN-001@r1",
                ],
            )
            application_path = root / "artifacts/application.html"
            original_application = application_path.read_text(encoding="utf-8")
            application_path.write_text(
                original_application.replace(
                    'data-experience-ref="returns:JRN-001@r1"',
                    'data-experience-ref="checkout:JRN-001@r1"',
                    1,
                ),
                encoding="utf-8",
            )
            collision = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            collision_messages = "\n".join(
                row["message"] for row in json.loads(collision.stdout)["findings"]
            )
            self.assertIn(
                "application map binding returns:JRN-001@r1 -> #/returns has no rendered DOM record",
                collision_messages,
            )
            application_path.write_text(original_application, encoding="utf-8")
            unreachable_application = original_application.replace(
                'data-route-target="#/returns"',
                'data-route-target="#/checkout"',
            ).replace(
                '"target": "#/returns"',
                '"target": "#/checkout"',
            )
            unreachable_application = unreachable_application.replace(
                '<h1 id="fixture-route-1-title">',
                '<button type="button" data-application-action="return-route">'
                'Return</button><h1 id="fixture-route-1-title">',
                1,
            )
            application_path.write_text(unreachable_application, encoding="utf-8")
            unreachable = self.run_cli(
                "check-application", "--root", root, "--json", expected=1,
            )
            unreachable_messages = "\n".join(
                row["message"] for row in json.loads(unreachable.stdout)["findings"]
            )
            self.assertIn(
                "application route is unreachable from entry_route: #/returns",
                unreachable_messages,
            )
            application_path.write_text(original_application, encoding="utf-8")
            rejected = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", expected=2,
            )
            self.assertIn("exactly match", rejected.stderr)
            approved = self.approve_experience_set(
                root, plan, proposal_hash, "checkout", "returns",
            )
            self.assertEqual(
                [row["result_ref"] for row in json.loads(approved.stdout)["receipts"]],
                ["application@r1", "checkout@r1", "returns@r1"],
            )
            self.commit_docs(root, "approve mutually linked experiences")
            rename_plan, rename_hash = self.propose_manual(
                root, experience="checkout", action="rename", to="purchase",
            )
            rename_actions = {
                (row["experience"], row["action"])
                for row in json.loads(rename_plan.read_text(encoding="utf-8"))["actions"]
            }
            self.assertEqual(
                rename_actions, {("checkout", "rename"), ("returns", "update")},
            )
            premature = self.run_cli(
                "rename", "--experience-root", checkout, "--to", "purchase",
                "--scope-plan", rename_plan, "--proposal-hash", rename_hash,
                expected=2,
            )
            self.assertIn("dependent returns must open", premature.stderr)
            self.run_cli(
                "begin-revision", "--experience-root", returns,
                "--scope-plan", rename_plan, "--proposal-hash", rename_hash,
            )
            self.run_cli(
                "rename", "--experience-root", checkout, "--to", "purchase",
                "--scope-plan", rename_plan, "--proposal-hash", rename_hash,
            )
            purchase = root / "experiences/purchase"
            returns_journey = returns / "journeys/returns-journey.md"
            journey_data, journey_body = experience_compile.fm(returns_journey)
            journey_data["revision"] = 2
            journey_data["supersedes"] = "returns:JRN-001@r1"
            journey_data["related_to"] = ["purchase:JRN-001@r2"]
            experience_compile.rewrite(returns_journey, journey_data, journey_body)
            purchase_journey = purchase / "journeys/checkout-journey.md"
            purchase_data, purchase_body = experience_compile.fm(purchase_journey)
            purchase_data["revision"] = 2
            purchase_data["supersedes"] = "checkout:JRN-001@r1"
            purchase_data["related_to"] = ["returns:JRN-001@r2"]
            experience_compile.rewrite(
                purchase_journey, purchase_data, purchase_body,
            )
            self.enter_reviews(
                root, [purchase, returns], rename_plan, rename_hash,
                transitions={
                    "purchase:TRN-001@r1": {
                        "target": "#/returns",
                        "preserve_context": ["cart_id"],
                        "return_route": "#/purchase",
                    },
                },
            )
            renamed = self.approve_experience_set(
                root, rename_plan, rename_hash, "purchase", "returns",
            )
            self.assertEqual(
                [row["result_ref"] for row in json.loads(renamed.stdout)["receipts"]],
                ["application@r2", "purchase@r2", "returns@r2"],
            )
            self.commit_docs(root, "approve linked rename")
            retire_plan, retire_hash = self.propose_manual(
                root, process="business-analysis/erp/processes/checkout-process",
                experience="purchase", action="retire",
            )
            retire_actions = {
                (row["experience"], row["action"])
                for row in json.loads(retire_plan.read_text(encoding="utf-8"))["actions"]
            }
            self.assertEqual(
                retire_actions, {("purchase", "retire"), ("returns", "update")},
            )
            self.run_cli(
                "begin-revision", "--experience-root", returns,
                "--scope-plan", retire_plan, "--proposal-hash", retire_hash,
            )
            self.run_cli(
                "retire", "--experience-root", purchase,
                "--scope-plan", retire_plan, "--proposal-hash", retire_hash,
            )
            journey_data, journey_body = experience_compile.fm(returns_journey)
            journey_data["revision"] = 3
            journey_data["supersedes"] = "returns:JRN-001@r2"
            journey_data.pop("related_to", None)
            experience_compile.rewrite(returns_journey, journey_data, journey_body)
            self.enter_reviews(root, [returns], retire_plan, retire_hash)
            retired = self.approve_experience_set(
                root, retire_plan, retire_hash, "purchase", "returns",
            )
            self.assertEqual(
                [row["result_ref"] for row in json.loads(retired.stdout)["receipts"]],
                ["application@r3", "returns@r3"],
            )
            self.assertEqual(experience_compile.fields(purchase)["status"], "retired")

    def test_requirement_chain_reaches_backlog_after_a_bound_experience_receipt(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            docs = root.parent
            self.prepare_inputs(docs)
            requirement = requirement_compile.create_requirement(
                docs, "checkout-copy", "Checkout copy", "feature", "normal", "REQ-001", [],
            )
            props, body = requirement_compile.split_note(requirement)
            for old, new in {
                "TODO: state the requested change and who needs it.": "Customers need clearer checkout confirmation content.",
                "TODO: state the observable outcome and acceptance boundary.": "The confirmation screen exposes the selected purchase state.",
                "TODO: define included and excluded behavior.": "Update checkout content only; payment rules remain unchanged.",
                "TODO: record evidence, constraints and urgency rationale.": "Support evidence shows confirmation ambiguity after purchase.",
                "TODO: explain why this stage must change.": "This stage changes the approved checkout outcome.",
            }.items():
                body = body.replace(old, new)
            requirement.write_text(requirement_compile.render_note(props, body), encoding="utf-8")
            requirement_compile.approve_requirement(requirement)
            self.commit_docs(root, "approve requirement")
            for stage in ("business-analysis", "solution-design", "design-system"):
                requirement_compile.bind_stage(requirement, stage, self.stage_receipt(docs, stage))
                self.commit_docs(root, f"bind {stage}")
            self.assertEqual(requirement_route.route(docs, "REQ-001")["action"], "author")
            proposal = subprocess.run([
                sys.executable, str(COMPILER), "propose", "--root", str(root),
                "--origin-mode", "requirement", "--requirement", "REQ-001",
                "--process-ref", "business-analysis/erp/processes/checkout-process",
            ], cwd=ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(proposal.returncode, 0, proposal.stdout + proposal.stderr)
            plan = docs / ".requirement-scope-plan.json"
            plan.write_text(proposal.stdout, encoding="utf-8")
            proposal_hash = json.loads(proposal.stdout)["proposal_hash"]
            self.run_cli(
                "init", "--root", root, "--experience", "checkout", "--origin-mode", "requirement",
                "--requirement", "REQ-001", "--primary-process-ref",
                "business-analysis/erp/processes/checkout-process", "--scope-plan", plan,
                "--proposal-hash", proposal_hash,
            )
            package = root / "experiences/checkout"
            self.run_cli("stub", "--experience-root", package, "--kind", "journey",
                         "--id", "JRN-001", "--slug", "checkout")
            self.enter_reviews(root, [package], plan, proposal_hash)
            self.approve_experience_set(root, plan, proposal_hash, "checkout")
            self.commit_docs(root, "approve requirement experience")
            with self.assertRaisesRegex(ValueError, "exactly one application"):
                requirement_compile.bind_stage(
                    requirement, "experience-design", "checkout@r1"
                )
            with self.assertRaisesRegex(ValueError, "zero process receipts"):
                requirement_compile.bind_stage(
                    requirement, "experience-design", "application@r1"
                )
            requirement_compile.bind_stage(
                requirement, "experience-design", ["application@r1", "checkout@r1"]
            )
            self.commit_docs(root, "bind experience")
            route = requirement_route.route(docs, "REQ-001")
            self.assertEqual((route["stage"], route["action"]), ("backlog-plan", "backlog"))
            backlog = ROOT / "plugins/software-engineering-team/scripts/backlog_compile.py"
            initialized = subprocess.run([
                sys.executable, str(backlog), "init", "--docs", str(docs),
                "--planning-mode", "requirement", "--requirement-ref", "REQ-001",
            ], cwd=ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)

    def test_requirement_revision_replaces_historical_application_reuse_with_current(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            docs = root.parent
            self.prepare_inputs(docs)
            _package, receipts = self.approve_single(root)
            self.assertEqual(
                [row["result_ref"] for row in receipts],
                ["application@r1", "checkout@r1"],
            )
            self.commit_docs(root, "approve application revision one")

            requirement = requirement_compile.create_requirement(
                docs, "checkout-reuse", "Checkout reuse", "feature", "normal",
                "REQ-001", [],
            )
            props, body = requirement_compile.split_note(requirement)
            for old, new in {
                "TODO: state the requested change and who needs it.":
                    "Customers need the approved checkout interaction reused.",
                "TODO: state the observable outcome and acceptance boundary.":
                    "The Requirement binds the exact application and process set.",
                "TODO: define included and excluded behavior.":
                    "Reuse checkout as approved; unrelated process changes are excluded.",
                "TODO: record evidence, constraints and urgency rationale.":
                    "The committed application revision is the acceptance evidence.",
                "TODO: explain why this stage must change.":
                    "The stage records the exact approved handoff.",
            }.items():
                body = body.replace(old, new)
            body = body.replace(
                "| experience-design | required |  |",
                "| experience-design | reuse | application@r1, checkout@r1 |",
                1,
            )
            requirement.write_text(
                requirement_compile.render_note(props, body), encoding="utf-8",
            )
            requirement_compile.approve_requirement(requirement)
            self.commit_docs(root, "approve requirement with application r1 reuse")
            for stage in ("business-analysis", "solution-design", "design-system"):
                requirement_compile.bind_stage(
                    requirement, stage, self.stage_receipt(docs, stage),
                )
                self.commit_docs(root, f"bind requirement {stage}")
            requirement_compile.bind_stage(
                requirement, "experience-design",
                ["application@r1", "checkout@r1"],
            )
            self.commit_docs(root, "bind requirement application revision one")
            self.assertEqual(
                requirement_route.route(docs, "REQ-001")["action"], "backlog",
            )

            plan, proposal_hash = self.propose_application_only(root)
            self.run_cli(
                "begin-application-revision", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            application = root / "artifacts/application.html"
            application.write_text(
                application.read_text(encoding="utf-8").replace(
                    "This content is privacy-sensitive.",
                    "This content is privacy-sensitive in revision two.",
                    1,
                ),
                encoding="utf-8",
            )
            self.run_cli(
                "enter-application-review", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            revised = self.approve_experience_set(root, plan, proposal_hash)
            self.assertEqual(
                [row["result_ref"] for row in json.loads(revised.stdout)["receipts"]],
                ["application@r2", "checkout@r1"],
            )
            self.commit_docs(root, "approve application revision two")

            route = requirement_route.route(docs, "REQ-001")
            self.assertEqual(
                (route["stage"], route["action"]),
                ("experience-design", "repair"),
            )
            requirement_compile.begin_revision(requirement)
            draft_props, draft_body = requirement_compile.split_note(requirement)
            self.assertEqual(
                (draft_props["status"], draft_props["revision"]), ("draft", 2),
            )
            self.assertEqual(requirement_compile.stage_results(draft_body), {})
            with self.assertRaisesRegex(ValueError, "experience-design reuse"):
                requirement_compile.approve_requirement(requirement)

            draft_props, draft_body = requirement_compile.split_note(requirement)
            draft_body = draft_body.replace(
                "application@r1, checkout@r1",
                "application@r2, checkout@r1",
                1,
            )
            requirement.write_text(
                requirement_compile.render_note(draft_props, draft_body),
                encoding="utf-8",
            )
            requirement_compile.approve_requirement(requirement)
            self.commit_docs(root, "approve replacement Requirement binding")
            for stage in ("business-analysis", "solution-design", "design-system"):
                requirement_compile.bind_stage(
                    requirement, stage, self.stage_receipt(docs, stage),
                )
                self.commit_docs(root, f"rebind requirement {stage}")
            with self.assertRaisesRegex(ValueError, "reuse receipt set"):
                requirement_compile.bind_stage(
                    requirement, "experience-design",
                    ["application@r1", "checkout@r1"],
                )
            requirement_compile.bind_stage(
                requirement, "experience-design",
                ["application@r2", "checkout@r1"],
            )
            self.commit_docs(root, "rebind requirement application revision two")
            final_route = requirement_route.route(docs, "REQ-001")
            self.assertEqual(final_route["action"], "backlog")
            _final_props, final_body = requirement_compile.split_note(requirement)
            self.assertEqual(
                [row[0] for row in requirement_compile.stage_results(
                    final_body,
                )["experience-design"]],
                ["application@r2", "checkout@r1"],
            )

    def test_backlog_revision_rebinds_after_an_application_only_revision(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            docs = root.parent
            self.prepare_inputs(docs)
            package, receipts = self.approve_interaction_experience(root)
            self.assertEqual(
                [row["result_ref"] for row in receipts],
                ["application@r1", "checkout@r1"],
            )
            self.commit_docs(root, "approve interaction experience")
            frozen_story = self.approve_manual_backlog(
                docs, "application@r1", "checkout@r1",
                ["checkout:SCR-001@r1", "checkout:STA-001@r1"],
            )
            self.commit_docs(root, "approve backlog revision one")
            frozen_bytes = frozen_story.read_bytes()

            plan, proposal_hash = self.propose_application_only(root)
            self.run_cli(
                "begin-application-revision", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            application = root / "artifacts/application.html"
            application.write_text(
                application.read_text(encoding="utf-8").replace(
                    "This content is privacy-sensitive.",
                    "This content remains privacy-sensitive.",
                    1,
                ),
                encoding="utf-8",
            )
            self.run_cli(
                "enter-application-review", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            revised = self.approve_experience_set(root, plan, proposal_hash)
            self.assertEqual(
                [row["result_ref"] for row in json.loads(revised.stdout)["receipts"]],
                ["application@r2", "checkout@r1"],
            )
            self.commit_docs(root, "approve application revision two")

            common = [
                "begin-revision", "--docs", docs, "--planning-mode", "manual",
                "--input-ref", self.stage_receipt(docs, "business-analysis"),
                "--input-ref", self.stage_receipt(docs, "solution-design"),
                "--input-ref", self.stage_receipt(docs, "design-system"),
            ]
            self.run_backlog_cli(
                *common, "--input-ref", "application@r1",
                "--input-ref", "checkout@r1", expected=1,
            )
            before, _body = backlog_compile.parse_front_matter(
                docs / "backlog/backlog.md"
            )
            self.assertEqual((before["status"], before["revision"]), ("approved", 1))

            self.run_backlog_cli(
                *common, "--input-ref", "application@r2",
                "--input-ref", "checkout@r1",
            )
            after, _body = backlog_compile.parse_front_matter(
                docs / "backlog/backlog.md"
            )
            self.assertEqual((after["status"], after["revision"]), ("draft", 2))
            self.assertTrue(any(
                row.startswith("experience-design|application@r2|sha256:")
                for row in after["input_bindings"]
            ))
            self.assertFalse(any(
                "|application@r1|" in row for row in after["input_bindings"]
            ))
            self.assertEqual(frozen_story.read_bytes(), frozen_bytes)

    def test_backlog_revision_freezes_superseded_and_retired_record_refs(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            docs = root.parent
            self.prepare_inputs(docs)
            package, _receipts = self.approve_interaction_experience(root)
            self.commit_docs(root, "approve interaction experience")
            frozen_story = self.approve_manual_backlog(
                docs, "application@r1", "checkout@r1",
                ["checkout:SCR-001@r1", "checkout:STA-001@r1"],
            )
            self.commit_docs(root, "approve backlog with exact record refs")
            frozen_bytes = frozen_story.read_bytes()

            plan, proposal_hash = self.propose_manual(
                root, experience="checkout", action="update",
            )
            self.run_cli(
                "begin-revision", "--experience-root", package,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            screen = package / "screens/checkout-screen.md"
            screen_data, screen_body = experience_compile.fm(screen)
            screen_data["revision"] = 2
            screen_data["supersedes"] = "checkout:SCR-001@r1"
            experience_compile.rewrite(screen, screen_data, screen_body)
            state = package / "states/checkout-state.md"
            state_data, state_body = experience_compile.fm(state)
            state_data["revision"] = 2
            state_data["supersedes"] = "checkout:STA-001@r1"
            state_data["record_state"] = "retired"
            experience_compile.rewrite(state, state_data, state_body)
            self.enter_reviews(root, [package], plan, proposal_hash)
            revised = self.approve_experience_set(
                root, plan, proposal_hash, "checkout",
            )
            self.assertEqual(
                [row["result_ref"] for row in json.loads(revised.stdout)["receipts"]],
                ["application@r2", "checkout@r2"],
            )
            self.commit_docs(root, "approve superseding interaction records")

            common = [
                "begin-revision", "--docs", docs, "--planning-mode", "manual",
                "--input-ref", self.stage_receipt(docs, "business-analysis"),
                "--input-ref", self.stage_receipt(docs, "solution-design"),
                "--input-ref", self.stage_receipt(docs, "design-system"),
                "--input-ref", "application@r2",
            ]
            self.run_backlog_cli(
                *common, "--input-ref", "checkout@r1", expected=1,
            )
            self.run_backlog_cli(
                *common, "--input-ref", "checkout@r2",
            )
            self.assertEqual(frozen_story.read_bytes(), frozen_bytes)

            new_story_root = docs / "backlog/epics/checkout/stories/follow-up"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(backlog_compile.stub_story(SimpleNamespace(
                    docs=str(docs), epic="checkout", slug="follow-up",
                    id="CHK-02", title=None,
                    scope="Deliver a new checkout follow-up outcome.",
                    work_kind="feature", criterion_ref=[
                        next(iter(
                            backlog_compile.parse_front_matter(frozen_story)[0][
                                "criterion_refs"
                            ]
                        ))
                    ],
                    experience_ref=["checkout:SCR-001@r1"], evidence_ref=[],
                    uses_design=["[[design-system/MASTER|Design Master]]"],
                    constrained_by=["[[solution-design/landscape|Landscape]]"],
                    implements=[],
                )), 0)
            _record, findings = backlog_compile.collect(docs)
            self.assertTrue(any(
                "follow-up/story.md experience_ref does not resolve to one active effective record"
                in finding
                for finding in findings
            ), findings)
            current_errors = []
            backlog_compile.validate_experience_ref(
                docs, "checkout:SCR-001@r2", "new story", current_errors,
            )
            self.assertEqual(current_errors, [])
            self.assertTrue(new_story_root.is_dir())

    def test_last_process_retirement_keeps_an_empty_application_receipt(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            package, _receipts = self.approve_single(root)
            self.commit_docs(root, "approve initial application")

            plan, proposal_hash = self.propose_manual(
                root, experience="checkout", action="retire",
            )
            self.run_cli(
                "retire", "--experience-root", package,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            write_empty_application(root)
            self.run_cli(
                "enter-application-review", "--root", root,
                "--scope-plan", plan, "--proposal-hash", proposal_hash,
            )
            retired = self.approve_experience_set(
                root, plan, proposal_hash, "checkout",
            )
            self.assertEqual(
                [row["result_ref"] for row in json.loads(retired.stdout)["receipts"]],
                ["application@r2"],
            )
            self.assertEqual(experience_compile.fields(package)["status"], "retired")
            self.run_cli("check-application", "--root", root, "--gate", "--json")
            retired_gate = json.loads(self.run_cli(
                "check", "--experience-root", package, "--gate", "--json",
            ).stdout)
            self.assertTrue(retired_gate["ok"])
            historic = json.loads(self.run_cli(
                "resolve", "--root", root, "--ref", "checkout@r1",
            ).stdout)
            self.assertFalse(historic["current"])
            self.run_cli(
                "status", "--experience-root", package, expected=1,
            )

            journey = package / "journeys/checkout-journey.md"
            original_journey = journey.read_bytes()
            journey.write_text(
                journey.read_text(encoding="utf-8") + "\nTampered after retirement.\n",
                encoding="utf-8",
            )
            tampered = self.run_cli(
                "check", "--experience-root", package, "--gate", "--json",
                expected=1,
            )
            self.assertIn(
                "retired package source changed",
                "\n".join(
                    row["message"] for row in json.loads(tampered.stdout)["findings"]
                ),
            )
            journey.write_bytes(original_journey)

            self.commit_docs(root, "retire final process")
            self.assertEqual(
                [row["result_ref"] for row in self.stage_candidates(
                    root.parent, "experience-design"
                )],
                ["application@r2"],
            )

            docs = root.parent
            requirement = requirement_compile.create_requirement(
                docs, "empty-experience", "Empty experience", "feature",
                "normal", "REQ-002", [],
            )
            props, body = requirement_compile.split_note(requirement)
            for old, new in {
                "TODO: state the requested change and who needs it.":
                    "Operators need a verified empty application boundary.",
                "TODO: state the observable outcome and acceptance boundary.":
                    "The empty application remains a valid downstream input.",
                "TODO: define included and excluded behavior.":
                    "Preserve the empty state; new process experiences are excluded.",
                "TODO: record evidence, constraints and urgency rationale.":
                    "The final process retirement is approved and committed.",
                "TODO: explain why this stage must change.":
                    "Each stage records the approved empty application handoff.",
            }.items():
                body = body.replace(old, new)
            requirement.write_text(
                requirement_compile.render_note(props, body), encoding="utf-8",
            )
            requirement_compile.approve_requirement(requirement)
            for stage in ("business-analysis", "solution-design", "design-system"):
                requirement_compile.bind_stage(
                    requirement, stage, self.stage_receipt(docs, stage),
                )
            requirement_compile.bind_stage(
                requirement, "experience-design", "application@r2",
            )
            self.commit_docs(root, "bind verified empty application")
            self.assertEqual(
                requirement_route.route(docs, "REQ-002")["action"], "backlog",
            )
            self.run_backlog_cli(
                "init", "--docs", docs, "--planning-mode", "requirement",
                "--requirement-ref", "REQ-002",
            )

            app_plan, app_hash = self.propose_application_only(root)
            self.run_cli(
                "begin-application-revision", "--root", root,
                "--scope-plan", app_plan, "--proposal-hash", app_hash,
            )
            application = root / "artifacts/application.html"
            application.write_text(
                application.read_text(encoding="utf-8").replace(
                    ">No active experiences</h1>",
                    ">No active process experiences</h1>", 1,
                ),
                encoding="utf-8",
            )
            self.rewrite_application_contract(
                root,
                lambda contract: contract["routes"][0].update(
                    label="No active process experiences"
                ),
            )
            self.run_cli(
                "enter-application-review", "--root", root,
                "--scope-plan", app_plan, "--proposal-hash", app_hash,
            )
            revised = self.approve_experience_set(root, app_plan, app_hash)
            self.assertEqual(
                [row["result_ref"] for row in json.loads(revised.stdout)["receipts"]],
                ["application@r3"],
            )


if __name__ == "__main__":
    unittest.main()
