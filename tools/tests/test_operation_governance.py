"""Operation Contract and Delivery Governance lifecycle checks."""

from __future__ import annotations

import json
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


if __name__ == "__main__":
    unittest.main()
