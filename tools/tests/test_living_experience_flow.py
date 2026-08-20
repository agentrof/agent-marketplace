import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.tests.test_ba_compile import make_valid_space, write as write_ba_note


ROOT = Path(__file__).resolve().parents[2]
COMPILER = ROOT / "plugins/software-engineering-team/scripts/experience_compile.py"
STAGES = ROOT / "plugins/software-engineering-team/scripts/stage_package.py"
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
import requirement_compile
import requirement_route


class LivingExperienceFlowTests(unittest.TestCase):
    def run_cli(self, *args, expected=0):
        result = subprocess.run([sys.executable, str(COMPILER), *map(str, args)], cwd=ROOT,
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def stage_candidate(self, docs, stage):
        result = subprocess.run([sys.executable, str(STAGES), "candidates", "--docs", str(docs), "--stage", stage, "--json"],
                                cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)["candidates"][0]

    def stage_receipt(self, docs, stage):
        return self.stage_candidate(docs, stage)["result_ref"]

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
            "---\ntype: landscape\npackage_status: draft\ntopology_selected: true\n"
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
            "contract_version: 1\nderives_from:\n  - \"[[business-analysis/erp/space|ERP]]\"\n"
            "constrained_by:\n  - \"[[solution-design/landscape|Landscape]]\"\n"
            "tags:\n  - status/draft\n---\n# Master\n\n"
            "semantic palette light dark typography spacing radius shadows motion reduced "
            "breakpoints icon component accessibility focus anti-pattern pre-delivery checklist\n",
            encoding="utf-8",
        )
        compiler = ROOT / "plugins/software-engineering-team/scripts/design_system_compile.py"
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
        plan = root.parent / ".scope-plan.json"
        plan.write_text(proposal.stdout, encoding="utf-8")
        return plan, json.loads(proposal.stdout)["proposal_hash"]

    def init_manual(self, root, experience="checkout", process="business-analysis/erp/processes/checkout-process"):
        docs = root.parent
        plan, proposal_hash = self.propose_manual(root, process, experience=experience)
        args = ["init", "--root", root, "--experience", experience, "--origin-mode", "manual",
                "--primary-process-ref", process, "--ba-ref", self.stage_receipt(docs, "business-analysis"),
                "--solution-ref", self.stage_receipt(docs, "solution-design"), "--design-ref", self.stage_receipt(docs, "design-system"),
                "--scope-plan", plan, "--proposal-hash", proposal_hash]
        payload = json.loads(self.run_cli(*args).stdout)
        return Path(payload["path"]), plan, proposal_hash

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
            self.run_cli("stub", "--experience-root", package, "--kind", "journey", "--id", "JRN-001", "--slug", "checkout")
            self.run_cli("enter-review", "--experience-root", package)
            self.run_cli("approve-set", "--root", root, "--experience", "checkout", "--scope-plan", create_plan, "--proposal-hash", proposal_hash)
            check = self.run_cli("check", "--experience-root", package, "--gate", "--json")
            self.assertTrue(json.loads(check.stdout)["ok"])
            text = (package / "experience.md").read_text(encoding="utf-8")
            self.assertIn("origin_mode: manual", text)
            self.assertNotIn("implements:", text)
            self.commit_docs(root, "approve manual experience")
            backlog = ROOT / "plugins/software-engineering-team/scripts/backlog_compile.py"
            initialized = subprocess.run([
                sys.executable, str(backlog), "init", "--docs", str(root.parent),
                "--planning-mode", "manual",
                "--input-ref", self.stage_receipt(root.parent, "business-analysis"),
                "--input-ref", self.stage_receipt(root.parent, "solution-design"),
                "--input-ref", self.stage_receipt(root.parent, "design-system"),
                "--input-ref", "checkout@r1",
            ], cwd=ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)

    def test_second_active_experience_for_process_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "workspace/docs/experience-design"
            root.mkdir(parents=True)
            self.prepare_inputs(root.parent)
            self.init_manual(root)
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
            package, _create_plan, _create_hash = self.init_manual(root)
            self.run_cli("stub", "--experience-root", package, "--kind", "journey",
                         "--id", "JRN-001", "--slug", "checkout",
                         "--supersedes", "checkout:JRN-001@r1")
            plan, proposal_hash = self.propose_manual(root, experience="checkout", action="rename", to="purchase")
            self.run_cli("rename", "--experience-root", package, "--to", "purchase",
                         "--scope-plan", plan, "--proposal-hash", proposal_hash)
            renamed = root / "experiences/purchase"
            self.assertTrue(renamed.is_dir())
            self.assertFalse(package.exists())
            self.assertIn('"checkout"', (renamed / "experience.md").read_text(encoding="utf-8"))
            journey = (renamed / "journeys/checkout-journey.md").read_text(encoding="utf-8")
            self.assertIn("supersedes: checkout:JRN-001@r1", journey)

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
                self.run_cli(
                    "init", "--root", root, "--experience", experience, "--origin-mode", "manual",
                    "--primary-process-ref", f"business-analysis/erp/processes/{process}-process",
                    "--ba-ref", self.stage_receipt(docs, "business-analysis"),
                    "--solution-ref", self.stage_receipt(docs, "solution-design"),
                    "--design-ref", self.stage_receipt(docs, "design-system"),
                    "--scope-plan", plan, "--proposal-hash", proposal_hash,
                )
                package = root / "experiences" / experience
                self.run_cli("stub", "--experience-root", package, "--kind", "journey",
                             "--id", f"JRN-00{1 if experience == 'checkout' else 2}", "--slug", experience)
                self.run_cli("enter-review", "--experience-root", package)
            rejected = self.run_cli("approve-set", "--root", root, "--experience", "checkout",
                                    "--scope-plan", plan, "--proposal-hash", proposal_hash, expected=2)
            self.assertIn("exactly match", rejected.stderr)

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
            self.run_cli("enter-review", "--experience-root", package)
            self.run_cli("approve-set", "--root", root, "--experience", "checkout",
                         "--scope-plan", plan, "--proposal-hash", proposal_hash)
            self.commit_docs(root, "approve requirement experience")
            requirement_compile.bind_stage(requirement, "experience-design", "checkout@r1")
            self.commit_docs(root, "bind experience")
            route = requirement_route.route(docs, "REQ-001")
            self.assertEqual((route["stage"], route["action"]), ("backlog-plan", "backlog"))
            backlog = ROOT / "plugins/software-engineering-team/scripts/backlog_compile.py"
            initialized = subprocess.run([
                sys.executable, str(backlog), "init", "--docs", str(docs),
                "--planning-mode", "requirement", "--requirement-ref", "REQ-001",
            ], cwd=ROOT, capture_output=True, text=True, check=False)
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)


if __name__ == "__main__":
    unittest.main()
