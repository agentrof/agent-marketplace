"""Operation Contract and Delivery Governance lifecycle checks."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins" / "software-engineering-team" / "scripts"
OPERATION = SCRIPTS / "operation_compile.py"
GOVERNANCE = SCRIPTS / "delivery_governance.py"


def frontmatter(props: dict, body: str = "# Note\n") -> str:
    lines = ["---"]
    for key, value in props.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {entry}" for entry in value)
        else:
            lines.append(f"{key}: {value}")
    return "\n".join(lines + ["---", "", body])


class OperationGovernanceTests(unittest.TestCase):
    def invoke(self, script: Path, *args: str):
        return subprocess.run([sys.executable, str(script), *args], cwd=ROOT,
                              capture_output=True, text=True, check=False)

    def approved_solution(self, docs: Path) -> str:
        sys.path.insert(0, str(SCRIPTS))
        import stage_package
        tree = docs / "solution-design"
        decisions = tree / "decisions"
        decisions.mkdir(parents=True)
        (decisions / "api-decision.md").write_text(frontmatter({
            "type": "decision", "status": "accepted", "aliases": ["SD-001"],
            "decision_kind": "technology-selection", "applies_to": ["api"],
            "selected_technology": "python-fastapi", "method_skills": ["python-fastapi"],
        }), encoding="utf-8")
        landscape = tree / "landscape.md"
        landscape.write_text(frontmatter({"type": "landscape", "package_status": "draft"}), encoding="utf-8")
        digest = stage_package.tree_hash(tree, {"package_hash", "package_status", "package_approved_at_utc"})
        landscape.write_text(frontmatter({
            "type": "landscape", "package_status": "approved", "package_hash": digest,
        }), encoding="utf-8")
        return "solution-design/decisions/api-decision"

    def test_verification_contract_approval_and_ci_render(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "workspace" / "docs"
            ref = self.approved_solution(docs)
            initialized = self.invoke(OPERATION, "init", "--docs", str(docs),
                                   "--kind", "verification", "--constrained-by", ref)
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
            contract = docs / "operation" / "verification-contract.md"
            text = contract.read_text(encoding="utf-8")
            text = text.replace("test_command: \n", "test_command: make test\n")
            text = text.replace("dependency_audit_disposition: not_applicable", "dependency_audit_disposition: required")
            text = text.replace("dependency_audit_command: \n", "dependency_audit_command: make audit\n")
            contract.write_text(text, encoding="utf-8")
            approved = self.invoke(OPERATION, "approve", "--docs", str(docs), "--kind", "verification")
            self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
            output = docs.parent.parent / ".github" / "workflows" / "tests.yml"
            rendered = self.invoke(OPERATION, "render-ci", "--docs", str(docs), "--output", str(output))
            self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
            ci = output.read_text(encoding="utf-8")
            self.assertIn("run: make test", ci)
            self.assertIn("run: make audit", ci)
            self.assertNotIn("{{", ci)

    def test_environment_and_governance_require_lifecycle_revisions(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "workspace" / "docs"
            ref = self.approved_solution(docs)
            initialized = self.invoke(OPERATION, "init", "--docs", str(docs),
                                   "--kind", "environment", "--constrained-by", ref)
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
            contract = docs / "operation" / "environment-contract.md"
            contract.write_text(contract.read_text(encoding="utf-8").replace(
                "env_command: \n", "env_command: ./tools/env\n"), encoding="utf-8")
            approved = self.invoke(OPERATION, "approve", "--docs", str(docs), "--kind", "environment")
            self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
            revised = self.invoke(OPERATION, "begin-revision", "--docs", str(docs), "--kind", "environment")
            self.assertEqual(revised.returncode, 0, revised.stdout + revised.stderr)
            self.assertIn("revision: 2", contract.read_text(encoding="utf-8"))

            created = self.invoke(GOVERNANCE, "init", "--docs", str(docs), "--max-parallel", "3")
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            governance = self.invoke(GOVERNANCE, "approve", "--docs", str(docs))
            self.assertEqual(governance.returncode, 0, governance.stdout + governance.stderr)
            value = json.loads(governance.stdout)
            self.assertTrue(value["current"])
            revised = self.invoke(GOVERNANCE, "begin-revision", "--docs", str(docs))
            self.assertEqual(revised.returncode, 0, revised.stdout + revised.stderr)

    def test_begin_revision_leaves_no_empty_hash_for_the_vault_to_reject(self):
        """An unset hash is absent, not present and empty."""
        sys.path.insert(0, str(SCRIPTS))
        import operation_compile
        import vault_check

        for kind, command_field in (("verification", "test_command"),
                                    ("environment", "env_command")):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                docs = Path(temporary) / "workspace/docs"
                ref = self.approved_solution(docs)
                args = ("--docs", str(docs), "--kind", kind)
                initialized = self.invoke(
                    OPERATION, "init", *args,
                    "--constrained-by", f"[[{ref}|SD-001]]",
                )
                self.assertEqual(
                    initialized.returncode, 0,
                    initialized.stdout + initialized.stderr,
                )
                path = operation_compile.contract_path(docs, kind)
                path.write_text(path.read_text(encoding="utf-8").replace(
                    f"{command_field}: \n", f"{command_field}: make {kind}\n",
                ), encoding="utf-8")
                approved = self.invoke(OPERATION, "approve", *args)
                self.assertEqual(
                    approved.returncode, 0, approved.stdout + approved.stderr,
                )
                revised = self.invoke(OPERATION, "begin-revision", *args)
                self.assertEqual(
                    revised.returncode, 0, revised.stdout + revised.stderr,
                )
                self.assertNotIn(
                    "source_hash: \n", path.read_text(encoding="utf-8"),
                )
                draft, _body = operation_compile.parse(path)
                self.assertNotIn("source_hash", draft)
                policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
                vault = vault_check.build_vault(docs, policy)
                findings = []
                vault_check.check_frontmatter_props(vault, findings)
                self.assertEqual(
                    [finding for finding in findings
                     if finding.path.startswith("operation/")
                     and "source_hash" in finding.message], [],
                )

    def test_operation_lifecycle_quotes_wikilinks_without_changing_body_or_receipt(self):
        sys.path.insert(0, str(SCRIPTS))
        import operation_compile
        import vault_check

        for kind, command_field in (("verification", "test_command"), ("environment", "env_command")):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                docs = Path(temporary) / "workspace/docs"
                ref = self.approved_solution(docs)
                wikilink = f"[[{ref}|SD-001]]"
                args = ("--docs", str(docs), "--kind", kind)
                initialized = self.invoke(OPERATION, "init", *args, "--constrained-by", wikilink)
                self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
                path = operation_compile.contract_path(docs, kind)
                policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)

                def assert_quoted_relation():
                    text = path.read_text(encoding="utf-8")
                    self.assertIn(f'  - "{wikilink}"\n', text)
                    props, body = operation_compile.parse(path)
                    self.assertEqual(props["constrained_by"], [wikilink])
                    vault = vault_check.build_vault(docs, policy)
                    findings = []
                    vault_check.check_frontmatter_props(vault, findings)
                    self.assertEqual([finding for finding in findings
                                      if finding.path.startswith("operation/")
                                      and "wikilink" in finding.message], [])
                    return props, body

                _props, initial_body = assert_quoted_relation()
                path.write_text(path.read_text(encoding="utf-8").replace(
                    f"{command_field}: \n", f"{command_field}: make {kind}\n"), encoding="utf-8")
                for revision in (1, 2):
                    approved = self.invoke(OPERATION, "approve", *args)
                    self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
                    props, body = assert_quoted_relation()
                    self.assertEqual(body, initial_body)
                    self.assertEqual(props["revision"], revision)
                    receipt, findings = operation_compile.check_contract(docs, kind)
                    self.assertEqual(findings, [])
                    self.assertTrue(receipt["current"])
                    self.assertEqual(props["source_hash"], operation_compile.source_hash(props, body))
                    raw = path.read_text(encoding="utf-8")
                    legacy = raw.replace(f'  - "{wikilink}"', f"  - {wikilink}")
                    path.write_text(legacy, encoding="utf-8")
                    legacy_props, legacy_body = operation_compile.parse(path)
                    self.assertEqual(legacy_props, props)
                    self.assertEqual(legacy_body, body)
                    path.write_text(operation_compile.render(legacy_props, legacy_body), encoding="utf-8")
                    self.assertEqual(path.read_text(encoding="utf-8"), raw)
                    self.assertEqual(operation_compile.check_contract(docs, kind), (receipt, []))
                    revised = self.invoke(OPERATION, "begin-revision", *args)
                    self.assertEqual(revised.returncode, 0, revised.stdout + revised.stderr)
                    draft, revised_body = assert_quoted_relation()
                    self.assertEqual(revised_body, initial_body)
                    self.assertEqual(draft["status"], "draft")
                    self.assertEqual(draft["revision"], revision + 1)
                    self.assertNotIn("approved_at_utc", draft)

    def test_governance_lifecycle_omits_unset_hashes_and_preserves_approved_receipt(self):
        sys.path.insert(0, str(SCRIPTS))
        import delivery_governance
        import vault_check

        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "workspace/docs"
            initialized = self.invoke(GOVERNANCE, "init", "--docs", str(docs), "--max-parallel", "2")
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
            path = delivery_governance.path_for(docs)
            _props, original_body = delivery_governance.read(path)
            for verb, expected_status in ((None, "draft"), ("approve", "approved"), ("begin-revision", "draft")):
                if verb:
                    result = self.invoke(GOVERNANCE, verb, "--docs", str(docs))
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                props, body = delivery_governance.read(path)
                self.assertEqual(props["status"], expected_status)
                self.assertEqual(body, original_body)
                policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
                findings = []
                vault_check.check_frontmatter_props(vault_check.build_vault(docs, policy), findings)
                self.assertEqual([finding for finding in findings
                                  if finding.path == "delivery/governance/governance.md"], [])
                receipt, errors = delivery_governance.status(docs)
                self.assertEqual(errors, [])
                self.assertEqual(receipt["current"], expected_status == "approved")
                if expected_status == "approved":
                    digest = delivery_governance.governance_hash(props, body)
                    self.assertEqual(props["governance_hash"], digest)
                    self.assertEqual(props["source_hash"], digest)
                    self.assertIn("approved_at_utc", props)
                else:
                    self.assertNotIn("governance_hash", props)
                    self.assertNotIn("source_hash", props)
                    self.assertNotIn("approved_at_utc", props)
            self.assertEqual(props["revision"], 2)

    def test_operation_receipts_survive_only_generated_relation_changes(self):
        sys.path.insert(0, str(SCRIPTS))
        import delivery_compile
        import operation_compile
        import vault_check

        def historical_digest(props, exact_body):
            view = {key: value for key, value in props.items()
                    if key not in {"source_hash", "approved_at_utc"}}
            return "sha256:" + hashlib.sha256(json.dumps(
                {"frontmatter": view, "body": exact_body}, ensure_ascii=False,
                sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

        def block(label):
            return vault_check.RELATION_START + "\n\n" + label + "\n\n" + vault_check.RELATION_END

        for kind in ("verification", "environment"):
            for originally_projected in (False, True):
                with self.subTest(kind=kind, originally_projected=originally_projected), tempfile.TemporaryDirectory() as temporary:
                    docs = Path(temporary) / "workspace/docs"
                    ref = self.approved_solution(docs)
                    args = ("--docs", str(docs), "--kind", kind)
                    self.assertEqual(self.invoke(OPERATION, "init", *args, "--constrained-by", ref).returncode, 0)
                    path = operation_compile.contract_path(docs, kind)
                    props, authored = operation_compile.parse(path)
                    command = "test_command" if kind == "verification" else "env_command"
                    workdir = "test_workdir" if kind == "verification" else "env_workdir"
                    props[command] = "make " + kind
                    text = operation_compile.render(props, authored)
                    if originally_projected:
                        text = vault_check.replace_relation_block(text, block("Initial generated inverse"))
                    path.write_text(text, encoding="utf-8")
                    approved = self.invoke(OPERATION, "approve", *args)
                    self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
                    props, body = operation_compile.parse(path)
                    canonical = historical_digest(props, authored)
                    self.assertEqual(props["source_hash"], canonical, "new approvals always use canonical ending")
                    # Reproduce the old issuer independently, including its block-dependent newline.
                    pin = historical_digest(props, authored + ("\n" if originally_projected else ""))
                    props["source_hash"] = pin
                    path.write_text(operation_compile.render(props, body), encoding="utf-8")
                    approved_at, revision = props["approved_at_utc"], props["revision"]
                    for projection in ("", block("New generated inverse"), block("Rerendered generated inverse"), ""):
                        path.write_text(vault_check.replace_relation_block(path.read_text(), projection), encoding="utf-8")
                        before = path.read_bytes()
                        receipt, errors = operation_compile.check_contract(docs, kind)
                        self.assertEqual(errors, [])
                        self.assertTrue(receipt["current"])
                        self.assertEqual(receipt["source_hash"], pin)
                        current, current_body = operation_compile.parse(path)
                        self.assertEqual((current["source_hash"], current["approved_at_utc"], current["revision"]),
                                         (pin, approved_at, revision))
                        self.assertEqual(operation_compile.source_hash(current, current_body), canonical)
                        snapshot, errors = delivery_compile.operation_contract_snapshot(docs, kind)
                        self.assertEqual(errors, [])
                        self.assertEqual(snapshot[kind + "_contract_hash"], pin)
                        self.assertEqual(path.read_bytes(), before, "consumption must not rewrite approval")
                    baseline = path.read_text()
                    for mutation in ("digest", "prose", "command", "workdir", "revision", "relation", "missing_start", "missing_end", "altered_marker"):
                        with self.subTest(mutation=mutation):
                            current, current_body = operation_compile.parse(path)
                            if mutation == "digest": current["source_hash"] = "sha256:" + "0" * 64
                            elif mutation == "prose": current_body += "\nAuthored command semantics changed."
                            elif mutation == "command": current[command] += " changed"
                            elif mutation == "workdir": current[workdir] = "other"
                            elif mutation == "revision": current["revision"] += 1
                            elif mutation == "relation": current["constrained_by"] = [f"[[{ref}|Different authored relation]]"]
                            text = operation_compile.render(current, current_body)
                            if mutation in {"missing_start", "missing_end", "altered_marker"}:
                                text = vault_check.replace_relation_block(text, block("Generated inverse"))
                                marker = vault_check.RELATION_END if mutation == "missing_end" else vault_check.RELATION_START
                                text = text.replace(marker, "<!-- changed generated marker -->" if mutation == "altered_marker" else "")
                            path.write_text(text, encoding="utf-8")
                            receipt, errors = operation_compile.check_contract(docs, kind)
                            self.assertIn("approved contract source_hash is stale", errors)
                            self.assertFalse(receipt["current"])
                            path.write_text(baseline, encoding="utf-8")
                    draft, draft_body = operation_compile.parse(path)
                    draft["status"] = "draft"
                    draft["source_hash"] = historical_digest(draft, draft_body + "\n")
                    self.assertEqual(operation_compile.receipt_hash(draft, draft_body), historical_digest(draft, draft_body))
                    self.assertNotEqual(operation_compile.receipt_hash(draft, draft_body), draft["source_hash"])


if __name__ == "__main__":
    unittest.main()
