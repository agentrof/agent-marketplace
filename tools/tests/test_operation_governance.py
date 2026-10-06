"""Operation Contract and Delivery Governance lifecycle checks."""

from __future__ import annotations

import contextlib
import io
import json
import hashlib
import re
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


def declare(path: Path, **fields: object) -> None:
    """Declare fields in an Operation Contract draft, as its writer does."""
    sys.path.insert(0, str(SCRIPTS))
    import operation_compile

    props, body = operation_compile.parse(path)
    path.write_text(operation_compile.render({**props, **fields}, body), encoding="utf-8")


def set_review_loop(docs: Path, value: str) -> None:
    """Approve a Process Policy revision that sets switch review_loop to ``value``."""
    set_switch(docs, "review_loop", value)


def set_switch(docs: Path, switch: str, value: str) -> None:
    """Approve a Process Policy revision that sets ``switch`` to ``value``."""
    sys.path.insert(0, str(SCRIPTS))
    import process_policy

    first = "begin-revision" if process_policy.path_for(docs).exists() else "init"
    for step in ((first,), ("set", "--switch", switch, "--value", value), ("approve",)):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = process_policy.main([step[0], "--docs", str(docs), *step[1:]])
        if code:
            raise AssertionError(output.getvalue())


def operation_findings(docs: Path) -> list[tuple[str, str]]:
    """The vault's frontmatter findings on the Operation Contracts."""
    sys.path.insert(0, str(SCRIPTS))
    import vault_check

    findings = []
    policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
    vault_check.check_frontmatter_props(vault_check.build_vault(docs, policy), findings)
    return [(finding.path, finding.message) for finding in findings
            if finding.path.startswith("operation/")]


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
            declare(contract, test_command="make test", dependency_audit_disposition="required",
                    dependency_audit_command="make audit")
            approved = self.invoke(OPERATION, "approve", "--docs", str(docs), "--kind", "verification")
            self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
            output = docs.parent.parent / ".github" / "workflows" / "tests.yml"
            rendered = self.invoke(OPERATION, "render-ci", "--docs", str(docs), "--output", str(output))
            self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
            ci = output.read_text(encoding="utf-8")
            self.assertIn("run: make test", ci)
            self.assertIn("run: make audit", ci)
            for job in ("vault_gate", "tests"):
                with self.subTest(job=job):
                    block = re.search(rf"(?ms)^  {job}:\n(.*?)(?=^  \w+:|\Z)", ci).group(1)
                    self.assertIn("      - uses: actions/checkout@v4\n"
                                  "        with:\n          fetch-depth: 0\n", block)
            self.assertNotIn("{{", ci)

    def test_pull_request_check_source_is_validated(self):
        sys.path.insert(0, str(SCRIPTS))
        import operation_compile
        import vault_check

        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "workspace" / "docs"
            ref = self.approved_solution(docs)
            args = ("--docs", str(docs), "--kind", "verification")
            initialized = self.invoke(OPERATION, "init", *args, "--constrained-by", f"[[{ref}|SD-001]]")
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
            path = operation_compile.contract_path(docs, "verification")
            draft, body = operation_compile.parse(path)
            # A new contract states the default rather than leaving it implicit.
            self.assertEqual(draft.pop("pull_request_check_source"), "repository_workflow")
            draft["test_command"] = "make test"
            source_error = "pull_request_check_source must be repository_workflow or external"
            provider_error = "pull_request_check_provider must name the external source"
            stray_error = "pull_request_check_provider is declared only with pull_request_check_source external"
            external = {"pull_request_check_source": "external"}
            cases = {
                "absent, as approved before the field existed": ({}, None),
                "repository workflow": ({"pull_request_check_source": "repository_workflow"}, None),
                "external with its provider": (
                    {**external, "pull_request_check_provider": "Buildkite pipeline acme/web"}, None),
                "unknown source": ({"pull_request_check_source": "github_actions"}, source_error),
                "empty source": ({"pull_request_check_source": ""}, source_error),
                "listed source": ({"pull_request_check_source": ["external"]}, source_error),
                "external without a provider": (external, provider_error),
                "external with an empty provider": ({**external, "pull_request_check_provider": ""}, provider_error),
                "listed provider": ({**external, "pull_request_check_provider": ["Buildkite"]}, provider_error),
                "provider with an unresolved token": (
                    {**external, "pull_request_check_provider": "{{ci_provider}}"}, provider_error),
                "provider with a credential literal": (
                    {**external, "pull_request_check_provider": "Buildkite token=abc123"}, provider_error),
                "provider for a repository workflow": (
                    {"pull_request_check_source": "repository_workflow",
                     "pull_request_check_provider": "Buildkite"}, stray_error),
                "provider without a source": ({"pull_request_check_provider": "Buildkite"}, stray_error),
            }
            policy = vault_check.load_policy(vault_check.DEFAULT_POLICY)
            for name, (fields, error) in cases.items():
                with self.subTest(case=name):
                    path.write_text(operation_compile.render({**draft, **fields}, body), encoding="utf-8")
                    checked = self.invoke(OPERATION, "check", *args, "--json")
                    approved = self.invoke(OPERATION, "approve", *args)
                    if error is None:
                        self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
                        receipt, errors = operation_compile.check_contract(docs, "verification")
                        self.assertEqual((errors, receipt["current"]), ([], True))
                        # The vault property schema is closed, so both fields must be registered in it.
                        findings = []
                        vault_check.check_frontmatter_props(vault_check.build_vault(docs, policy), findings)
                        self.assertEqual([finding for finding in findings
                                          if "pull_request_check" in finding.message], [])
                    else:
                        self.assertEqual(checked.returncode, 1, checked.stdout + checked.stderr)
                        [message] = json.loads(checked.stdout)["errors"]
                        self.assertTrue(message.startswith(error), message)
                        self.assertEqual(approved.returncode, 2, approved.stdout + approved.stderr)
                        self.assertIn(error, approved.stdout)

    def test_optional_diagnostic_adapter_is_validated_without_legacy_defaults(self):
        sys.path.insert(0, str(SCRIPTS))
        import operation_compile
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "workspace/docs"
            ref = self.approved_solution(docs)
            args = ("--docs", str(docs), "--kind", "verification")
            initialized = self.invoke(OPERATION, "init", *args, "--constrained-by", f"[[{ref}|SD-001]]")
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
            path = operation_compile.contract_path(docs, "verification")
            draft, body = operation_compile.parse(path)
            self.assertNotIn("diagnostic_test_command", draft)
            self.assertNotIn("diagnostic_test_workdir", draft)
            draft["test_command"] = "make test"
            path.write_text(operation_compile.render(draft, body), encoding="utf-8")
            approved = self.invoke(OPERATION, "approve", *args)
            self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
            original = path.read_bytes()
            original_props, _ = operation_compile.parse(path)
            receipt, errors = operation_compile.check_contract(docs, "verification")
            self.assertEqual(errors, [])
            self.assertEqual(receipt["source_hash"], original_props["source_hash"])
            self.assertEqual(path.read_bytes(), original)
            self.assertNotIn("diagnostic_test_workdir", original_props)
            command = {"diagnostic_test_command": "python3 tools/diagnostic_tests.py"}
            cases = [({}, None), (command, None),
                     ({**command, "diagnostic_test_workdir": "tools/diagnostics"}, None),
                     ({"diagnostic_test_command": ""}, "diagnostic_test_command"),
                     ({"diagnostic_test_command": "   "}, "diagnostic_test_command"),
                     ({"diagnostic_test_command": []}, "diagnostic_test_command"),
                     ({"diagnostic_test_command": False}, "diagnostic_test_command"),
                     ({"diagnostic_test_command": "runner token=literal"}, "credential literal"),
                     ({"diagnostic_test_command": "runner {{selection}}"}, "unresolved token"),
                     ({"diagnostic_test_workdir": "."}, "requires diagnostic_test_command")]
            cases.extend(({**command, "diagnostic_test_workdir": directory}, "normalized repository-relative")
                         for directory in ("", "../tools", "./tools", "/tools", "tools//tests", "C:/tools", "tools/child.", ["tools"]))
            for fields, error in cases:
                with self.subTest(fields=fields):
                    text = operation_compile.render({**draft, **fields}, body)
                    _receipt, errors = operation_compile.check_contract(docs, "verification", text=text)
                    if error is None:
                        self.assertEqual(errors, [])
                    else:
                        self.assertTrue(any(error in finding for finding in errors), errors)
                    self.assertEqual(path.read_bytes(), original)
            path.write_text(operation_compile.render({**draft, **command}, body), encoding="utf-8")
            approved = self.invoke(OPERATION, "approve", *args)
            self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
            props, _ = operation_compile.parse(path)
            self.assertEqual(props["diagnostic_test_command"], command["diagnostic_test_command"])
            self.assertNotIn("diagnostic_test_workdir", props)
            self.assertEqual(operation_findings(docs), [])

    def test_optional_command_variables_are_validated_and_typed_for_the_vault(self):
        """A Verification Contract may name the variables its commands read, which run evidence then binds (#356)."""
        sys.path.insert(0, str(SCRIPTS))
        import operation_compile
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "workspace/docs"
            ref = self.approved_solution(docs)
            args = ("--docs", str(docs), "--kind", "verification")
            initialized = self.invoke(OPERATION, "init", *args, "--constrained-by", f"[[{ref}|SD-001]]")
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
            path = operation_compile.contract_path(docs, "verification")
            draft, body = operation_compile.parse(path)
            self.assertNotIn("command_variables", draft)
            draft["test_command"] = "make test"
            error = "command_variables must list unique environment variable names"
            cases = [({}, None), ({"command_variables": ["DATABASE_URL"]}, None),
                     ({"command_variables": ["DATABASE_URL", "http_proxy", "_PRIVATE_TOOL_HOME"]}, None),
                     ({"command_variables": "DATABASE_URL"}, error),
                     ({"command_variables": ["DATABASE_URL", "DATABASE_URL"]}, error)]
            cases.extend(({"command_variables": [name]}, error)
                         for name in ("", "1DATABASE", "DATABASE-URL", "DATABASE URL", "DATABASE=URL", "${DATABASE}"))
            for fields, expected in cases:
                with self.subTest(fields=fields):
                    text = operation_compile.render({**draft, **fields}, body)
                    _receipt, errors = operation_compile.check_contract(docs, "verification", text=text)
                    if expected is None:
                        self.assertEqual(errors, [])
                    else:
                        self.assertTrue(any(expected in finding for finding in errors), errors)
            path.write_text(operation_compile.render(
                {**draft, "command_variables": ["DATABASE_URL", "http_proxy"]}, body), encoding="utf-8")
            approved = self.invoke(OPERATION, "approve", *args)
            self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
            props, _ = operation_compile.parse(path)
            self.assertEqual(props["command_variables"], ["DATABASE_URL", "http_proxy"])
            self.assertEqual(operation_compile.check_contract(docs, "verification")[1], [])
            self.assertEqual(operation_findings(docs), [])

    def test_command_variables_never_name_the_runners_own_namespace(self):
        """The runner sets every AGENTROF_ variable and run evidence binds them without a declaration, a
        selection file by its content, so a contract that names one would refuse every run's evidence."""
        sys.path.insert(0, str(SCRIPTS))
        import operation_compile
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "workspace/docs"
            ref = self.approved_solution(docs)
            args = ("--docs", str(docs), "--kind", "verification")
            initialized = self.invoke(OPERATION, "init", *args, "--constrained-by", f"[[{ref}|SD-001]]")
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
            path = operation_compile.contract_path(docs, "verification")
            draft, body = operation_compile.parse(path)
            draft["test_command"] = "make test"
            # Windows matches variable names without case, so the namespace is matched without case too.
            for name in ("AGENTROF_DIAGNOSTIC_TESTS", "AGENTROF_REUSED_TESTS", "AGENTROF_MUTATION_FILES",
                         "AGENTROF_VERIFICATION_SCRATCH", "AGENTROF_SAMPLE", "agentrof_reused_tests"):
                with self.subTest(name=name):
                    text = operation_compile.render({**draft, "command_variables": ["DATABASE_URL", name]}, body)
                    _receipt, errors = operation_compile.check_contract(docs, "verification", text=text)
                    self.assertIn(f"command_variables must not name {name}, a variable of the runner's own"
                                  " AGENTROF_ namespace, which run evidence binds without a declaration", errors)
            path.write_text(operation_compile.render(
                {**draft, "command_variables": ["DATABASE_URL", "AGENTROF_REUSED_TESTS"]}, body), encoding="utf-8")
            refused = self.invoke(OPERATION, "approve", *args)
            self.assertNotEqual(refused.returncode, 0, refused.stdout + refused.stderr)
            self.assertNotEqual(operation_compile.parse(path)[0].get("status"), "approved")

    def test_render_ci_refuses_an_external_pull_request_check_source(self):
        sys.path.insert(0, str(SCRIPTS))
        import operation_compile

        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "workspace" / "docs"
            ref = self.approved_solution(docs)
            args = ("--docs", str(docs), "--kind", "verification")
            initialized = self.invoke(OPERATION, "init", *args, "--constrained-by", ref)
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
            path = operation_compile.contract_path(docs, "verification")
            props, body = operation_compile.parse(path)
            props.update(test_command="make test", pull_request_check_source="external",
                         pull_request_check_provider="Buildkite pipeline acme/web")
            path.write_text(operation_compile.render(props, body), encoding="utf-8")
            approved = self.invoke(OPERATION, "approve", *args)
            self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
            output = docs.parent.parent / ".github" / "workflows" / "tests.yml"
            rendered = self.invoke(OPERATION, "render-ci", "--docs", str(docs), "--output", str(output))
            self.assertEqual(rendered.returncode, 2, rendered.stdout + rendered.stderr)
            [error] = json.loads(rendered.stdout)["errors"]
            self.assertIn("Buildkite pipeline acme/web reports the pull request checks", error)
            self.assertIn("no repository workflow", error)
            self.assertFalse(output.exists())

    def test_verification_approval_projects_the_paired_environment_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "workspace" / "docs"
            ref = self.approved_solution(docs)
            for kind in ("environment", "verification"):
                initialized = self.invoke(OPERATION, "init", "--docs", str(docs), "--kind", kind,
                                          "--constrained-by", ref)
                self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
            environment = docs / "operation" / "environment-contract.md"
            verification = docs / "operation" / "verification-contract.md"
            declare(environment, env_command="./tools/env")
            declare(verification, test_command="make test")
            env_approved = self.invoke(OPERATION, "approve", "--docs", str(docs), "--kind", "environment")
            self.assertEqual(env_approved.returncode, 0, env_approved.stdout + env_approved.stderr)
            env_hash = json.loads(env_approved.stdout)["source_hash"]
            approved = self.invoke(OPERATION, "approve", "--docs", str(docs), "--kind", "verification")
            self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
            text = verification.read_text(encoding="utf-8")
            self.assertIn("paired_environment_revision: 1\n", text)
            self.assertIn(f"paired_environment_source_hash: {env_hash}\n", text)
            self.assertEqual([message for _path, message in operation_findings(docs)
                              if "paired_environment" in message], [])
            checked = json.loads(self.invoke(OPERATION, "check", "--docs", str(docs), "--kind", "verification").stdout)
            self.assertTrue(checked["ok"])
            self.assertEqual(checked["receipt"]["paired_environment"],
                             {"revision": 1, "source_hash": env_hash, "current": True})
            # A later Environment Contract approval shows as advisory drift and
            # the next Verification revision drops the stamp until it is approved.
            self.invoke(OPERATION, "begin-revision", "--docs", str(docs), "--kind", "environment")
            reapproved = self.invoke(OPERATION, "approve", "--docs", str(docs), "--kind", "environment")
            self.assertEqual(reapproved.returncode, 0, reapproved.stdout + reapproved.stderr)
            checked = json.loads(self.invoke(OPERATION, "check", "--docs", str(docs), "--kind", "verification").stdout)
            self.assertTrue(checked["ok"])
            self.assertFalse(checked["receipt"]["paired_environment"]["current"])
            self.invoke(OPERATION, "begin-revision", "--docs", str(docs), "--kind", "verification")
            self.assertNotIn("paired_environment", verification.read_text(encoding="utf-8"))
            approved = self.invoke(OPERATION, "approve", "--docs", str(docs), "--kind", "verification")
            self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
            self.assertIn("paired_environment_revision: 2\n", verification.read_text(encoding="utf-8"))

    def test_environment_and_governance_require_lifecycle_revisions(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "workspace" / "docs"
            ref = self.approved_solution(docs)
            initialized = self.invoke(OPERATION, "init", "--docs", str(docs),
                                   "--kind", "environment", "--constrained-by", ref)
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
            contract = docs / "operation" / "environment-contract.md"
            declare(contract, env_command="./tools/env")
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

    def test_init_leaves_no_empty_text_property_for_the_vault_to_reject(self):
        """An unset command, like an unset hash, is absent, not present and empty."""
        sys.path.insert(0, str(SCRIPTS))
        import operation_compile

        for kind, command_field in (("verification", "test_command"),
                                    ("environment", "env_command")):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                docs = Path(temporary) / "workspace/docs"
                ref = self.approved_solution(docs)
                args = ("--docs", str(docs), "--kind", kind)
                initialized = self.invoke(OPERATION, "init", *args, "--constrained-by", f"[[{ref}|SD-001]]")
                self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
                self.assertEqual(operation_findings(docs), [])
                checked = self.invoke(OPERATION, "check", *args)
                self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
                # Approval keeps absent the commands the writer left unset, such as not-applicable ones.
                declare(operation_compile.contract_path(docs, kind), **{command_field: f"make {kind}"})
                approved = self.invoke(OPERATION, "approve", *args)
                self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
                self.assertEqual(operation_findings(docs), [])

    def test_refused_approval_leaves_the_draft_byte_identical(self):
        """The writer fixes a refused draft and approves it again, without a revision."""
        sys.path.insert(0, str(SCRIPTS))
        import operation_compile

        for kind, command_field in (("verification", "test_command"),
                                    ("environment", "env_command")):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                docs = Path(temporary) / "workspace/docs"
                ref = self.approved_solution(docs)
                args = ("--docs", str(docs), "--kind", kind)
                initialized = self.invoke(OPERATION, "init", *args, "--constrained-by", f"[[{ref}|SD-001]]")
                self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
                path = operation_compile.contract_path(docs, kind)
                draft = path.read_bytes()
                refused = self.invoke(OPERATION, "approve", *args)
                self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
                self.assertEqual(json.loads(refused.stdout)["errors"],
                                 [f"approval check failed: {command_field} is required"])
                self.assertEqual(path.read_bytes(), draft)
                declare(path, **{command_field: f"make {kind}"})
                approved = self.invoke(OPERATION, "approve", *args)
                self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
                receipt = json.loads(approved.stdout)
                self.assertEqual((receipt["status"], receipt["revision"], receipt["current"]),
                                 ("approved", 1, True))

    def test_unbound_draft_omits_constrained_by_and_approval_names_it(self):
        """A draft without a Solution decision has no empty relation; approval names the key to declare."""
        sys.path.insert(0, str(SCRIPTS))
        import operation_compile

        for kind, command_field in (("verification", "test_command"),
                                    ("environment", "env_command")):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                docs = Path(temporary) / "workspace/docs"
                ref = self.approved_solution(docs)
                args = ("--docs", str(docs), "--kind", kind)
                initialized = self.invoke(OPERATION, "init", *args)
                self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
                path = operation_compile.contract_path(docs, kind)
                self.assertNotIn("constrained_by", operation_compile.parse(path)[0])
                self.assertEqual(operation_findings(docs), [])
                declare(path, **{command_field: f"make {kind}"})
                refused = self.invoke(OPERATION, "approve", *args)
                self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
                self.assertEqual(json.loads(refused.stdout)["errors"], [
                    "approval check failed: approved contract must cite at least one accepted "
                    "Solution decision in constrained_by"])
                declare(path, constrained_by=[f"[[{ref}|SD-001]]"])
                approved = self.invoke(OPERATION, "approve", *args)
                self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
                self.assertEqual(operation_findings(docs), [])

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
                declare(path, **{command_field: f"make {kind}"})
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
                declare(path, **{command_field: f"make {kind}"})
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

    def test_refused_governance_approval_leaves_the_draft_byte_identical(self):
        """The writer fixes a refused draft and approves it again, without a revision."""
        sys.path.insert(0, str(SCRIPTS))
        import delivery_governance

        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "workspace/docs"
            initialized = self.invoke(GOVERNANCE, "init", "--docs", str(docs), "--max-parallel", "2")
            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
            path = delivery_governance.path_for(docs)
            props, body = delivery_governance.read(path)
            path.write_text(delivery_governance.render({**props, "max_parallel": 0}, body), encoding="utf-8")
            draft = path.read_bytes()
            refused = self.invoke(GOVERNANCE, "approve", "--docs", str(docs))
            self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
            self.assertEqual(json.loads(refused.stdout)["errors"],
                             ["approval check failed: max_parallel must be a positive integer"])
            self.assertEqual(path.read_bytes(), draft)
            path.write_text(delivery_governance.render({**props, "max_parallel": 3}, body), encoding="utf-8")
            approved = self.invoke(GOVERNANCE, "approve", "--docs", str(docs))
            self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
            receipt = json.loads(approved.stdout)
            self.assertEqual((receipt["status"], receipt["revision"], receipt["max_parallel"], receipt["current"]),
                             ("approved", 1, 3, True))

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


def record_section(title: str, header: str, rows: tuple[str, ...] | list[str]) -> str:
    """One review-record section: its heading and a table of ``rows``."""
    separator = "|" + "---|" * (header.count("|") - 1)
    return "\n".join([f"## {title}", "", header, separator, *rows, "", ""])


class AcceptedMinorFindingsTests(unittest.TestCase):
    """Switch `review_loop` at `blocking_delta` keeps an Operation review's record in
    the contract: the findings the review returned, the calibration rulings and
    the minor findings accepted as written, validated as a backlog review note's."""

    invoke = OperationGovernanceTests.invoke
    approved_solution = OperationGovernanceTests.approved_solution
    HEADER = "| finding | owner_role | reason | revisit_trigger |"
    CITE = "[[operation/verification-contract\\|Verification Contract]]"
    VALID = (f"| OP-2 {CITE} The Contract section states the test workdir twice in different words. "
             "| qa_engineer | Both sentences name one directory, so the test command runs the same "
             "way. | Revisit at the next revision of the Verification Contract. |")
    OTHER = (f"| OP-3 {CITE} The Contract section states the test command origin twice. "
             "| devops_engineer | Both sentences name the approved command, so it runs the same "
             "way. | Revisit at the next revision of the Verification Contract. |")
    RETURNED = (
        f"| OP-1 | major | {CITE} The Contract section never states where the test command runs. |",
        f"| OP-2 | minor | {CITE} The Contract section states the test workdir twice in different words. |",
        f"| OP-3 | minor | {CITE} The Contract section states the test command origin twice. |",
    )
    INVALID = (f"| OP-1 | major | invalid | {CITE} The front matter sets test_workdir to the repository"
               " root, so the command runs from one directory. |")
    LABEL = "operation/verification-contract.md accepted minor finding 1"
    PATH = "operation/verification-contract.md"

    def setUp(self) -> None:
        sys.path.insert(0, str(SCRIPTS))
        import operation_compile

        self.operation = operation_compile
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.docs = Path(temporary.name) / "workspace" / "docs"
        ref = self.approved_solution(self.docs)
        self.args = ("--docs", str(self.docs), "--kind", "verification")
        initialized = self.invoke(OPERATION, "init", *self.args, "--constrained-by", ref)
        self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
        self.path = operation_compile.contract_path(self.docs, "verification")
        declare(self.path, test_command="make test")
        self.draft = self.path.read_text(encoding="utf-8")
        set_review_loop(self.docs, "blocking_delta")

    def record(self, accepted=(VALID,), returned=RETURNED, calibration=(INVALID,),
               header: str = HEADER) -> None:
        sections = []
        if returned is not None:
            sections.append(record_section("Returned Findings", "| finding | severity | description |",
                                           returned))
        if calibration is not None:
            sections.append(record_section(
                "Severity Calibration", "| finding | claimed_severity | calibrated_severity | reason |",
                calibration))
        if accepted is not None:
            sections.append(record_section("Accepted Minor Findings", header, accepted))
        self.write_section("".join(sections))

    def accept(self, *rows: str, header: str = HEADER) -> None:
        self.record(accepted=rows, header=header)

    def write_section(self, section: str) -> None:
        self.path.write_text(self.draft.replace("## Navigation", section + "## Navigation", 1),
                             encoding="utf-8")

    def errors(self) -> list[str]:
        return self.operation.check_contract(self.docs, "verification")[1]

    def test_a_contract_without_the_section_is_unchanged(self):
        self.assertEqual(self.errors(), [])
        approved = self.invoke(OPERATION, "approve", *self.args)
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)

    def test_the_section_is_authored_text_unless_the_loop_is_blocking_delta(self):
        # Only the blocking_delta loop writes the table; before the switch a
        # section of that name was prose that checked and approved.
        prose = "## Accepted Minor Findings\n\nThe owner accepts the repeated workdir sentence.\n\n"
        self.write_section(prose)
        self.assertEqual(self.errors(), [f"{self.PATH} Accepted Minor Findings must be a Markdown table"])
        for policy in ("current", None):
            with self.subTest(policy=policy):
                if policy is None:
                    (self.docs / "delivery/process-policy.md").unlink()
                else:
                    set_review_loop(self.docs, policy)
                self.write_section(prose)
                self.assertEqual(self.errors(), [])
                self.record(accepted=(self.VALID.replace("qa_engineer", "product_owner"),),
                            returned=("| not an id | fatal | no citation |",),
                            calibration=("| OP-1 | minor | critical | no citation |",))
                self.assertEqual(self.errors(), [])
        self.write_section(prose)
        approved = self.invoke(OPERATION, "approve", *self.args)
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        receipt, errors = self.operation.check_contract(self.docs, "verification")
        self.assertEqual((errors, receipt["current"], receipt["source_hash"]),
                         ([], True, json.loads(approved.stdout)["source_hash"]))
        # A policy that cannot be read is refused, never read as the default.
        set_review_loop(self.docs, "blocking_delta")
        begun = self.invoke(OPERATION, "begin-revision", *self.args)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        sys.path.insert(0, str(SCRIPTS))
        import process_policy
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(process_policy.main(["begin-revision", "--docs", str(self.docs)]), 0)
        self.assertEqual(len(self.errors()), 1)
        self.assertIn("Process Policy revision 2 is a draft", self.errors()[0])

    def test_an_approved_contract_without_returned_findings_never_reads_the_policy(self):
        # Approved before its review kept a record, the contract stays as it
        # was, so a draft Process Policy, which no check may read, leaves it valid.
        set_review_loop(self.docs, "current")
        self.record(returned=None, calibration=None)
        approved = self.invoke(OPERATION, "approve", *self.args)
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        import process_policy
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(process_policy.main(["begin-revision", "--docs", str(self.docs)]), 0)
        receipt, errors = self.operation.check_contract(self.docs, "verification")
        self.assertEqual((errors, receipt["current"]), ([], True))

    def test_complete_rows_approve_and_stay_current(self):
        self.accept(self.VALID, self.OTHER)
        self.assertEqual(self.errors(), [])
        approved = self.invoke(OPERATION, "approve", *self.args)
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        receipt, errors = self.operation.check_contract(self.docs, "verification")
        self.assertEqual((errors, receipt["current"]), ([], True))

    def test_incomplete_rows_fail_with_the_missing_follow_up(self):
        cases = {
            "must cite the affected vault note": self.VALID.replace(self.CITE + " ", ""),
            "targets missing note: operation/missing":
                self.VALID.replace("operation/verification-contract", "operation/missing"),
            "needs a concrete finding": self.VALID.replace(
                "The Contract section states the test workdir twice in different words.", "typo"),
            "owner_role must be one of: qa_engineer, devops_engineer":
                self.VALID.replace("qa_engineer", "product_owner"),
            "needs a concrete reason": self.VALID.replace(
                "Both sentences name one directory, so the test command runs the same way.", "TODO"),
            "needs a concrete revisit_trigger": self.VALID.replace(
                "Revisit at the next revision of the Verification Contract.", "later"),
        }
        for expected, row in cases.items():
            with self.subTest(expected=expected):
                self.accept(row)
                self.assertEqual(self.errors(), [f"{self.LABEL} {expected}"])
        self.accept(self.VALID, self.VALID)
        self.assertEqual(self.errors(), [f"{self.PATH} repeats accepted minor finding 2",
                                         f"{self.PATH} accepts finding OP-2 twice"])

    def test_a_blocking_finding_cannot_enter_the_section(self):
        # Each row names a finding the review returned; only a minor one fits.
        blocking = self.VALID.replace("| OP-2 ", "| OP-1 ")
        label = f"{self.LABEL} names OP-1, which"
        cases = (
            ((self.INVALID,), [f"{label} calibration ruled invalid; only a minor finding is accepted"]),
            ((self.INVALID.replace("| invalid |", "| major |"),),
             [f"{label} calibration ruled major; only a minor finding is accepted"]),
            ((), [f"{self.PATH} returned major finding OP-1 has no Severity Calibration row",
                  f"{label} the review returned as major; only a minor finding is accepted"]),
        )
        for calibration, expected in cases:
            with self.subTest(calibration=calibration):
                self.record(accepted=(blocking,), calibration=calibration)
                self.assertEqual(self.errors(), expected)
        # A claim calibration lowered to minor is a minor finding and follows the minor rule.
        self.record(accepted=(blocking,), calibration=(self.INVALID.replace("| invalid |", "| minor |"),))
        self.assertEqual(self.errors(), [])
        self.accept(self.VALID.replace("| OP-2 ", "| OP-9 "))
        self.assertEqual(self.errors(), [f"{self.LABEL} names OP-9, which Returned Findings does not list"])
        self.accept(self.VALID.replace("| OP-2 ", "| "))
        self.assertEqual(self.errors(), [f"{self.LABEL} must start with the id of the finding it accepts"])
        self.accept(self.VALID)
        self.record(returned=None, calibration=None)
        self.assertEqual(self.errors(), [f"{self.LABEL} names OP-2, which Returned Findings does not list"])
        self.accept(self.VALID.replace("| qa_engineer |", "| major | qa_engineer |"),
                    header="| finding | severity | owner_role | reason | revisit_trigger |")
        self.assertEqual(self.errors(), [
            f"{self.PATH} Accepted Minor Findings columns must be: finding,"
            " owner_role, reason, revisit_trigger"])

    def test_single_pass_accepts_the_readers_major_without_calibration(self):
        # At review_rounds single_pass no calibration reader runs: a major
        # finding stands as returned and becomes a follow-up, and only a
        # critical one stays out, whatever the review_loop value.
        set_review_loop(self.docs, "current")
        set_switch(self.docs, "review_rounds", "single_pass")
        major = self.VALID.replace("| OP-2 ", "| OP-1 ")
        self.record(accepted=(major, self.VALID), calibration=None)
        self.assertEqual(self.errors(), [])
        approved = self.invoke(OPERATION, "approve", *self.args)
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        self.path.write_text(self.draft, encoding="utf-8")
        critical = tuple(row.replace("| OP-1 | major |", "| OP-1 | critical |") for row in self.RETURNED)
        self.record(accepted=(major,), returned=critical, calibration=None)
        self.assertEqual(self.errors(), [
            f"{self.LABEL} names OP-1, which the review returned as critical;"
            " only a minor or major finding is accepted"])
        # The blocking_delta record is unchanged: the same major needs calibration.
        set_switch(self.docs, "review_rounds", "current")
        set_review_loop(self.docs, "blocking_delta")
        self.record(accepted=(major,), calibration=None)
        self.assertEqual(self.errors(), [
            f"{self.PATH} returned major finding OP-1 has no Severity Calibration row",
            f"{self.LABEL} names OP-1, which the review returned as major; only a minor finding is accepted"])

    def test_calibration_rows_are_complete_and_rule_returned_claims(self):
        calibration = "operation/verification-contract.md severity calibration 1"
        reason = ("The front matter sets test_workdir to the repository root, so the command runs"
                  " from one directory.")
        cases = {
            f"{calibration} claimed_severity must be critical or major":
                self.INVALID.replace("| major | invalid |", "| minor | invalid |"),
            f"{calibration} calibrated_severity must confirm major or be minor or invalid":
                self.INVALID.replace("| invalid |", "| critical |"),
            f"{calibration} reason must cite a vault note": self.INVALID.replace(self.CITE + " ", ""),
            f"{calibration} targets missing note: operation/missing":
                self.INVALID.replace("operation/verification-contract", "operation/missing"),
            f"{calibration} needs a concrete reason": self.INVALID.replace(reason, "no."),
            f"{calibration} finding must be an id such as F-3: first":
                self.INVALID.replace("| OP-1 |", "| first |"),
            f"{calibration} rules OP-4, which Returned Findings does not list":
                self.INVALID.replace("| OP-1 |", "| OP-4 |"),
            f"{calibration} claimed_severity must be major, the severity OP-1 was returned at":
                self.INVALID.replace("| major | invalid |", "| critical | invalid |"),
        }
        for expected, row in cases.items():
            with self.subTest(expected=expected):
                self.record(calibration=(row,))
                errors = self.errors()
                self.assertIn(expected, errors)
                self.assertTrue(all("calibration" in error or "Severity Calibration" in error
                                    for error in errors), errors)
        self.record(calibration=(self.INVALID, self.INVALID))
        self.assertEqual(self.errors(), [f"{self.PATH} calibrates finding OP-1 twice"])
        self.record(calibration=(self.INVALID,),
                    accepted=None)
        self.assertEqual(self.errors(), [])
        section = record_section("Severity Calibration", "| finding | severity | reason |",
                                 ["| OP-1 | invalid | " + self.CITE + " " + reason + " |"])
        self.write_section(section)
        self.assertEqual(self.errors(), [
            f"{self.PATH} Severity Calibration columns must be: finding, claimed_severity,"
            " calibrated_severity, reason"])

    def test_returned_findings_carry_an_id_a_severity_and_a_citation(self):
        returned = "operation/verification-contract.md returned finding 1"
        statement = "The Contract section states the test workdir twice in different words."
        cases = {
            f"{returned} finding must be an id such as F-3: second": ("| OP-2 |", "| second |"),
            f"{returned} severity must be critical, major or minor": ("| minor |", "| trivial |"),
            f"{returned} description must cite a vault note": (self.CITE + " ", ""),
            f"{returned} needs a concrete description": (statement, "twice."),
        }
        for expected, (old, new) in cases.items():
            with self.subTest(expected=expected):
                self.record(accepted=None, calibration=None,
                            returned=(self.RETURNED[1].replace(old, new),))
                self.assertEqual(self.errors(), [expected])
        self.record(accepted=None, calibration=None, returned=(self.RETURNED[1], self.RETURNED[1]))
        self.assertEqual(self.errors(), [f"{self.PATH} returns finding OP-2 twice"])

    def test_a_contract_approved_before_its_review_kept_a_record_stays_valid(self):
        set_review_loop(self.docs, "current")
        old_style = self.VALID.replace("| OP-2 ", "| ")
        self.record(accepted=(old_style,), returned=None, calibration=None)
        approved = self.invoke(OPERATION, "approve", *self.args)
        self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
        set_review_loop(self.docs, "blocking_delta")
        receipt, errors = self.operation.check_contract(self.docs, "verification")
        self.assertEqual((errors, receipt["current"]), ([], True))
        # Its next revision is reviewed under the record.
        begun = self.invoke(OPERATION, "begin-revision", *self.args)
        self.assertEqual(begun.returncode, 0, begun.stdout + begun.stderr)
        self.assertEqual(self.errors(), [f"{self.LABEL} must start with the id of the finding it accepts"])

    def test_refused_approval_leaves_the_draft_byte_identical(self):
        self.accept(self.VALID.replace("qa_engineer", "product_owner"))
        draft = self.path.read_bytes()
        refused = self.invoke(OPERATION, "approve", *self.args)
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("owner_role must be one of: qa_engineer, devops_engineer", refused.stdout)
        self.assertEqual(self.path.read_bytes(), draft)
        # The approval reads the record of the draft it stamps.
        self.accept(self.VALID.replace("| OP-2 ", "| OP-1 "))
        draft = self.path.read_bytes()
        refused = self.invoke(OPERATION, "approve", *self.args)
        self.assertEqual(refused.returncode, 2, refused.stdout + refused.stderr)
        self.assertIn("names OP-1, which calibration ruled invalid", refused.stdout)
        self.assertEqual(self.path.read_bytes(), draft)


if __name__ == "__main__":
    unittest.main()
