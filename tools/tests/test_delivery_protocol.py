"""Cross-host protocol inventory and command-surface contracts."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / "plugins" / "software-engineering-team"
sys.path.insert(0, str(PLUGIN / "scripts"))
import delivery_result  # noqa: E402

# Declared finding codes that no refusal carries yet, each with why it has no emitter.
# A code leaves this map in the change that makes a refusal emit it.
RESERVED_FINDING_CODES = {
    **dict.fromkeys((
        "REQUIREMENT_ID_COLLISION", "REQUIREMENT_NOT_CURRENT", "REQUIREMENT_STAGE_ORDER",
        "REQUIREMENT_STAGE_IMPACT_INVALID", "REQUIREMENT_NOT_INCORPORATED",
    ), "the Requirement Flow compilers check Requirement records and report plain errors, not this envelope"),
    "BACKLOG_REVISION_STALE": "a stale backlog pin reaches the coordinator only inside the Delivery package findings",
    "BACKLOG_SOURCE_CLAIMED": "backlog_compile checks backlog revisions and reports plain errors, not this envelope",
    "BACKLOG_COVERAGE_MISMATCH": "backlog_compile checks backlog coverage and reports plain errors, not this envelope",
    "DELIVERY_SCOPE_STALE": "no verb compares the local scope_hash with the published Scope-Hash; stale sources surface in the package findings",
    "DELIVERY_ITEM_ALREADY_INTEGRATED": "a repeated integrate-item is refused at the Slot check, before the Item status is read",
    "DELIVERY_CONTRACT_CLAIM_EXCEEDED": "a contract claim names a contract, not a path, and the protocol maps no product change to the contracts it touches",
    "DELIVERY_CANCELLATION_FINALIZATION_STALE": "cancel-delivery finalizes in one atomic push and has no resume path that could meet a stale finalization",
    "DELIVERY_SOURCE_HANDOFF_STALE": "no verb compares a held handoff's Source-Intent with the current sources yet",
    "DELIVERY_SLOT_DUPLICATE": "no verb refuses two Slot refs that hold the same Item tip yet",
    "DELIVERY_PR_INTENT_STRANDED": "an elected PR call that left no PR is reported as DELIVERY_PR_UNCERTAIN; no verb declares the intent stranded",
    "DELIVERY_POST_MERGE_TRANSITION": "merge-pr returns the merged evidence and no verb refuses a post-merge transition yet",
    "DELIVERY_UPGRADE_CONTRACT_MISMATCH": "no verb compares the Fence Upgrade-Contract with a Delivery's upgrade barrier yet",
    "DELIVERY_UPGRADE_HANDOFF_COLLISION": "no verb detects a colliding upgrade target handoff yet",
}
COORDINATOR_COMMANDS = {
    "names",
    "preflight",
    "reserve-delivery",
    "apply-governance",
    "upgrade-fence-v1",
    "begin-source-handoff",
    "authorize-target-update",
    "apply-target-update",
    "reauthorize-target-update",
    "finish-source-handoff",
    "abort-source-handoff",
    "publish-execution-plan",
    "refresh-target",
    "revise-unclaimed-scope",
    "claim-items",
    "begin-plan-revision",
    "quiesce-delivery",
    "finish-plan-revision",
    "abort-plan-revision",
    "quiesce-upgrade",
    "upgrade-target-merge",
    "finish-upgrade",
    "abort-upgrade",
    "start-item",
    "block-item",
    "unblock-item",
    "reopen-item",
    "pause-item",
    "resume-item",
    "takeover-item",
    "push-item",
    "integrate-item",
    "publish-delivery-review",
    "prepare-pr-creation",
    "record-pr-remote",
    "open-pr",
    "merge-pr",
    "invalidate-delivery-review",
    "cancel-delivery",
    "verify-merge",
    "reconcile",
    "board",
    "locate",
}


class DeliveryProtocolTests(unittest.TestCase):
    DATA = PLUGIN / "skill-content/deliver/data"

    @classmethod
    def load_contract(cls, name: str) -> dict:
        return json.loads((cls.DATA / name).read_text(encoding="utf-8"))

    def test_result_and_record_registries_are_closed_and_unique(self):
        result = self.load_contract("delivery-result-contract.json")
        def no_duplicates(pairs):
            out = {}
            for key, value in pairs:
                if key in out:
                    raise AssertionError(f"duplicate JSON key: {key}")
                out[key] = value
            return out
        records = json.loads(
            (self.DATA / "delivery-control-record-contract.json").read_text(),
            object_pairs_hook=no_duplicates,
        )
        codes = result["finding_codes"]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertTrue(all(re.fullmatch(r"[A-Z][A-Z0-9_.-]{2,63}", code) for code in codes))
        self.assertEqual(records["schema_version"], 1)
        self.assertEqual(len(records["records"]), len(set(records["records"])))
        self.assertEqual(records["unknown_record_policy"], "fail_closed")
        self.assertEqual(set(records["records"]), set(records["subjects"]))

    def test_every_declared_finding_code_is_emitted_or_reserved(self):
        """A declared code is carried by a shipped refusal, or reserved with the reason it is not.

        A refusal carries its code as the ``CODE: detail`` prefix the result envelope
        parses, or as the code of a structured finding. A prefix in a declared family
        that the contract does not declare is refused too, because the envelope would
        silently report it as DELIVERY_INPUT_INVALID.
        """
        declared = set(self.load_contract("delivery-result-contract.json")["finding_codes"])
        self.assertEqual(set(delivery_result.FINDING_CODES), declared)
        families = "|".join(sorted({code.split("_", 1)[0] for code in declared}))
        prefix = re.compile(rf"^((?:{families})_[A-Z0-9_]+):")

        def envelope_code(message: str) -> str:
            return delivery_result.from_raw("scan", {"ok": False, "errors": [message]})["findings"][0]["code"]

        emitted = {envelope_code("an unclassified refusal")}
        undeclared = []
        for script in sorted((PLUGIN / "scripts").glob("*.py")):
            if script.name == "delivery_result.py":
                continue
            for node in ast.walk(ast.parse(script.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Dict):
                    emitted.update(
                        value.value for key, value in zip(node.keys, node.values)
                        if isinstance(key, ast.Constant) and key.value == "code"
                        and isinstance(value, ast.Constant) and value.value in declared)
                    continue
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue
                match = prefix.match(node.value)
                if match is None:
                    continue
                code = match.group(1)
                if code not in declared:
                    undeclared.append(f"{script.name}:{node.lineno}: {code}")
                    continue
                self.assertEqual(envelope_code(node.value), code, f"{script.name}:{node.lineno}")
                emitted.add(code)

        reserved = set(RESERVED_FINDING_CODES)
        self.assertEqual(undeclared, [], "a refusal prefix names a code the contract does not declare")
        self.assertEqual(sorted(declared - emitted - reserved), [],
                         "emit the code from its refusal or reserve it with the reason it has none")
        self.assertEqual(sorted(emitted & reserved), [], "an emitted code leaves the reserved map")
        self.assertEqual(sorted(reserved - declared), [], "only a declared code can be reserved")
        for code, reason in RESERVED_FINDING_CODES.items():
            self.assertRegex(reason, r"^\S[^\n]*$", code)

    def test_runtime_record_emitters_match_the_closed_registry(self):
        source = (PLUGIN / "scripts/delivery_git.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        emitted: dict[str, list[set[str]]] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            values = {
                key.value: value
                for key, value in zip(node.keys, node.values)
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
            record = values.get("Record")
            if not isinstance(record, ast.Constant) or not isinstance(record.value, str):
                continue
            fields = set(values) - {"Record", "Protocol"}
            emitted.setdefault(record.value.replace("-", "_"), []).append(fields)

        contract = self.load_contract("delivery-control-record-contract.json")
        registered = contract["records"]
        self.assertEqual(set(emitted), set(registered))
        for record, variants in emitted.items():
            allowed = set(registered[record])
            for fields in variants:
                self.assertLessEqual(fields, allowed, record)

    def test_every_coordinator_commit_carries_a_control_record(self):
        """The Delivery compiler never takes a merge that carries a control record
        as proof that a PR merged, so every commit the coordinator writes carries one."""
        tree = ast.parse((PLUGIN / "scripts/delivery_git.py").read_text(encoding="utf-8"))
        functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        writers = {function.name for function in functions
                   if any(isinstance(node, ast.Constant) and node.value == "commit-tree"
                          for node in ast.walk(function))}
        self.assertEqual(writers, {"commit_tree", "commit_replacements", "merge_candidate", "revert_merge_candidate"})

        def carries_record(node: ast.AST | None, scope: ast.FunctionDef) -> bool:
            if isinstance(node, ast.Name):
                assigned = [assign.value for assign in ast.walk(scope) if isinstance(assign, ast.Assign)
                            and any(isinstance(target, ast.Name) and target.id == node.id for target in assign.targets)]
                return len(assigned) == 1 and carries_record(assigned[0], scope)
            return isinstance(node, ast.Dict) and any(
                isinstance(key, ast.Constant) and key.value == "Record"
                and isinstance(value, ast.Constant) and bool(value.value)
                for key, value in zip(node.keys, node.values))

        unmarked = sorted({call.lineno for scope in functions for call in ast.walk(scope)
                           if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                           and call.func.id in writers
                           and not carries_record(call.args[4] if len(call.args) > 4 else next(
                               (keyword.value for keyword in call.keywords if keyword.arg == "trailers"), None), scope)})
        self.assertEqual(unmarked, [], "coordinator commits without an Agentrof-Record trailer")

    def test_provider_and_receipt_contracts_match_runtime_surface(self):
        provider = self.load_contract("delivery-provider-contract.json")
        receipt = self.load_contract("delivery-receipt-contract.json")
        provider_source = (PLUGIN / "scripts/delivery_provider.py").read_text(
            encoding="utf-8"
        )
        coordinator_source = (PLUGIN / "scripts/delivery_git.py").read_text(
            encoding="utf-8"
        )

        self.assertEqual(provider["provider"], "github")
        self.assertEqual(provider["adapter"], "delivery_provider.GitHubProvider")
        self.assertTrue(provider["required_capabilities"]["merge_commit"])
        self.assertTrue(provider["required_capabilities"]["required_checks_green"])
        self.assertFalse(provider["required_capabilities"]["squash_merge"])
        self.assertFalse(provider["required_capabilities"]["rebase_merge"])
        self.assertFalse(provider["branch_lifecycle"]["request_head_deletion"])
        self.assertIn("class GitHubProvider", provider_source)
        self.assertIn('"--delete-branch=false"', provider_source)
        self.assertIn("def require_green_checks", provider_source)
        self.assertIn("provider.require_green_checks(current)", coordinator_source)

        self.assertEqual(receipt["kind"], "item-writer-v1")
        self.assertEqual(receipt["states"], ["pending", "verified"])
        self.assertEqual(receipt["provider_receipt"]["kind"], "pr-create-v1")
        self.assertEqual(
            receipt["target_update_receipt"]["kind"], "target-update-v1"
        )
        for literal in ("item-writer-v1", "pr-create-v1", "target-update-v1"):
            self.assertIn(literal, coordinator_source)

    def test_protocol_contract_is_closed(self):
        protocol = self.load_contract("delivery-protocol-1.json")
        self.assertEqual(protocol["protocol_version"], "delivery-protocol-1")
        self.assertEqual(
            protocol["merge_policy"],
            "merge-commit-only; squash and rebase fail closed",
        )

    def test_public_protocol_entries_equal_canonical_entry_skills(self):
        protocol = (ROOT / "docs/requirement-delivery-protocol.md").read_text(
            encoding="utf-8"
        )
        public_block = protocol.split("## Public entry surface", 1)[1].split(
            "```text", 1
        )[1].split("```", 1)[0]
        documented = {
            line.strip().removeprefix("/")
            for line in public_block.splitlines()
            if line.strip().startswith("/")
        }
        canonical = set()
        for path in (PLUGIN / "skill-content").glob("*/SKILL.md"):
            text = path.read_text(encoding="utf-8")
            header = text.split("---", 2)[1]
            if re.search(r"(?m)^exposure:\s*entry\s*$", header):
                canonical.add(path.parent.name)
        self.assertEqual(documented, canonical)

    def test_every_canonical_flow_has_an_explicit_skill_reader(self):
        skill_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((PLUGIN / "skill-content").glob("*/SKILL.md"))
        )
        for flow in sorted((PLUGIN / "flows").glob("*.md")):
            self.assertIn(f"flows/{flow.name}", skill_text, flow.name)

    @staticmethod
    def split_dict(node: ast.Dict) -> tuple[dict, list]:
        literal, spreads = {}, []
        for key, value in zip(node.keys, node.values):
            if key is None:
                spreads.append(value)
            elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                literal[key.value] = value
        return literal, spreads

    @classmethod
    def states_barrier(cls, function: ast.FunctionDef, node: ast.Dict) -> bool:
        literal, spreads = cls.split_dict(node)
        if "Barrier-Kind" in literal:
            return True
        for spread in spreads:
            if isinstance(spread, ast.Call) and getattr(spread.func, "id", "") == "carried_fence_barrier":
                return True
            if not isinstance(spread, ast.Name):
                continue
            for inner in ast.walk(function):
                filled = None
                if isinstance(inner, ast.Assign) and any(
                        isinstance(target, ast.Name) and target.id == spread.id
                        for target in inner.targets):
                    filled = inner.value
                elif (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute)
                      and inner.func.attr == "update" and inner.args
                      and isinstance(inner.func.value, ast.Name)
                      and inner.func.value.id == spread.id):
                    filled = inner.args[0]
                if isinstance(filled, ast.Dict) and "Barrier-Kind" in cls.split_dict(filled)[0]:
                    return True
        return False

    def test_every_fence_writer_states_the_barrier_it_inherits(self):
        """A Fence child that omits the two trailers silently clears the barrier."""
        tree = ast.parse((PLUGIN / "scripts/delivery_git.py").read_text(encoding="utf-8"))
        silent = []
        writers = 0
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.Dict):
                    continue
                record = self.split_dict(node)[0].get("Record")
                if not (isinstance(record, ast.Constant) and record.value == "project-fence-v2"):
                    continue
                writers += 1
                if not self.states_barrier(function, node):
                    silent.append(f"{function.name}:{node.lineno}")
        self.assertEqual(silent, [])
        self.assertGreaterEqual(writers, 15)

    def test_coordinator_exposes_closed_internal_verb_set(self):
        source = (PLUGIN / "scripts/delivery_git.py").read_text(encoding="utf-8")
        declared = set(re.findall(r'sub\.add_parser\("([^"]+)"\)', source))
        self.assertEqual(declared, COORDINATOR_COMMANDS)

        parser_functions = set(re.findall(r'set_defaults\(func="([^"]+)"\)', source))
        dispatched = set(re.findall(r'args\.func == "([^"]+)"', source))
        self.assertTrue(parser_functions <= dispatched | {"names", "preflight"})

    def test_every_internal_verb_renders_help_without_project_mutation(self):
        script = PLUGIN / "scripts/delivery_git.py"
        for command in sorted(COORDINATOR_COMMANDS):
            with self.subTest(command=command):
                completed = subprocess.run(
                    [sys.executable, str(script), command, "--help"],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=20,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn(command, completed.stdout)

    def test_both_host_distributions_contain_the_new_canonical_flow_and_contract(self):
        for host in ("claude", "codex"):
            root = ROOT / "dist" / host / "software-engineering-team"
            self.assertTrue((root / "flows/requirement.md").is_file())
            self.assertTrue((root / "scripts/delivery_result.py").is_file())
            for name in (
                "delivery-result-contract.json",
                "delivery-provider-contract.json",
                "delivery-receipt-contract.json",
            ):
                self.assertTrue((root / "skill-content/deliver/data" / name).is_file())


if __name__ == "__main__":
    unittest.main()
