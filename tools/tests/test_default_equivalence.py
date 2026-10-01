"""Golden all-default runs: with no Process Policy, every compiler output and
every shipped task binding is e47dbe0's on the same inputs, apart from the
differences EXPECTED_DIFFERENCES lists, each tied to the issue whose fix made
it.

Every ``*_GOLDEN`` digest except DELIVERY_POLICY_GOLDEN's is e47dbe0's own
output: this file's harness run with ``--root`` on an export of that commit
(``git archive e47dbe0``), so it runs that tree's scripts (see ``--harness``
at the end of this file). A test applies the listed differences to its golden
and compares the result with this tree's run, so an unlisted difference fails,
and so does a listed one that no longer differs. A deliberate default-path
change adds its entry, with its issue, in the same commit.

"The same inputs" is literal. The Delivery run reads its approved backlog,
upstream notes and Verification Contract from the frozen
``default_equivalence_inputs.json`` instead of rebuilding them with the
backlog and Operation compilers, whose own fixes would otherwise move every
Delivery hash that covers them; the Operation run checks the same contract,
the backlog run checks the same backlog and derives its review manifests, and
the backlog task run derives an epic's reader and writer tasks and the root's
writer task from it with ``task_inputs.py --epic``. ``--harness inputs``
rebuilds that file. The approval run builds and approves the fixture backlog
with ``root``'s own fixture and compiler, as the input harness does, and seals
every backlog file it writes. Only the two backlog goldens hide fields: a
review manifest's contract_hash hashes the package scripts, and its
source_hash covers that hash; a backlog task also hashes its instructions,
whose paths the shipped run seals. No Delivery record or Operation receipt
carries a package script or instruction hash, and the task manifest run hashes
a package the harness writes byte for byte, so release changes to scripts and
contracts reach no other golden. No golden covers a rendered agent file.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PLUGIN = "plugins/software-engineering-team"
FROZEN = "2026-01-01T00:00:00Z"
GIT_ENV = {"GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
           "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
           "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
           "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00"}

INPUTS = Path(__file__).resolve().with_name("default_equivalence_inputs.json")

DELIVERY_GOLDEN = {
    "files": {
        "delivery/definition-of-done.md":
            "sha256:7b766655dd95e17b5b8a8a72dacad577524c8d2010bca777e5eaf85325cde7e0",
        "delivery/deliveries/dlv-001-auth/delivery-review.md":
            "sha256:35f3bc263ef98d690ad8ca80d40c5e34a6d36c8698e9d8e764ea0011afb1e37d",
        "delivery/deliveries/dlv-001-auth/delivery.md":
            "sha256:9cb7bac61dcc6a118207143170212f7f073cb3e5e983ffa02bc6f907256e44c5",
        "delivery/deliveries/dlv-001-auth/execution-plan.md":
            "sha256:b076320a0a9bd7134269c07b96ce35090cd06525a88298d22b20fb6fc2e67fd0",
        "delivery/deliveries/dlv-001-auth/items/auth-01/code-review.md":
            "sha256:1bab717a4131ecf98e4471801c1329db7cb418b1a5e527589795cba84f1b96ab",
        "delivery/deliveries/dlv-001-auth/items/auth-01/item.md":
            "sha256:5c9a8f0191600cd81c41cb50649818a9e6e8398752e242a3026e179807ee2810",
        "delivery/deliveries/dlv-001-auth/items/auth-01/verification.md":
            "sha256:0d7ce7622e3a05777ed8c461e8a40042c4e9b5fd4cf361d87a3238913fc74231",
        "maps/delivery.md":
            "sha256:20dbcbb97fdc51571a7e1408bb24f7cab9e097958b0139060d2172267c5af1cb",
    },
    "outputs": [
        "sha256:41ae602c5e6815cc3f4d31cd2e1c1cd0e458ce43bc3973c050b4921a4bc1c9b7",
        "sha256:57f15c73e2bad9e1c7647fbc2e0cb680b7cb6352dfca5d72c1738d907fadfc9d",
        "sha256:2a569505e9e63ce2543b513ce0341738f5952152c30fb3b2e1fa8ddc72aa3b29",
        "sha256:d87f2a99c3040d4ffe46ee5e1c6b81cacb5b8956ccb5c62316447138c02fdd2f",
        "sha256:9da6603ffa4ef4b6eb8cc9f02f2e26ce14422b81cb749e26dd71ef1595cf82e5",
        "sha256:6770154716253effbf45b90f5c665fd03fc2b1421c5c1dc5edb1a5558a260e11",
        "sha256:6770154716253effbf45b90f5c665fd03fc2b1421c5c1dc5edb1a5558a260e11",
        "sha256:9fac5f29db46fe2668b73a4507b0c976eae80286547f4e3108b8c5db16799cb8",
        "sha256:241a3ca0c7944c139fa41f35293a402856d29e05317dfa8229076d6cbfda384b",
        "sha256:d33f66c22f28bf98021cf733ea28cbc2234a4fca34e19a05bd647462107d7393",
        "sha256:738ab1650128715429b5c087efee1484d23ac7168b9435f0bea287291827524b",
        "sha256:3817d097b8d4f6463446b0d46ab75656dffb9da0bea7eb68731433c31d5fe0aa",
    ],
}

# The same run under an approved Process Policy that sets only review_panels,
# so delivery_path stays at standard and every Delivery switch at its default.
# e47dbe0 has no Process Policy, so this golden is this program's own output,
# taken on frozen inputs with the scripts of program tip 6305138, before
# delivery_path existed. The pins name the policy, so the Delivery records
# differ from DELIVERY_GOLDEN's.
DELIVERY_POLICY_GOLDEN = {
    "files": {
        "delivery/definition-of-done.md":
            "sha256:7b766655dd95e17b5b8a8a72dacad577524c8d2010bca777e5eaf85325cde7e0",
        "delivery/deliveries/dlv-001-auth/delivery-review.md":
            "sha256:7d76c5b8085a6bff8c09736e9354366df2f816e49be252e8809c1bac47501511",
        "delivery/deliveries/dlv-001-auth/delivery.md":
            "sha256:29ad2e649d6276ac859abb0dd26e4793a294ccee9b37bae5410923f09b18490f",
        "delivery/deliveries/dlv-001-auth/execution-plan.md":
            "sha256:f2919d5b003f95b28e6e56d5685ef4e52b6cc7e464462668ebffe2d1143c7d00",
        "delivery/deliveries/dlv-001-auth/items/auth-01/code-review.md":
            "sha256:1bab717a4131ecf98e4471801c1329db7cb418b1a5e527589795cba84f1b96ab",
        "delivery/deliveries/dlv-001-auth/items/auth-01/item.md":
            "sha256:5c9a8f0191600cd81c41cb50649818a9e6e8398752e242a3026e179807ee2810",
        "delivery/deliveries/dlv-001-auth/items/auth-01/verification.md":
            "sha256:0d7ce7622e3a05777ed8c461e8a40042c4e9b5fd4cf361d87a3238913fc74231",
        "delivery/process-policy.md":
            "sha256:0771354cfcefd7ac40197d1695d91ab54f23d1022a116f8b1ed046297aa09175",
        "maps/delivery.md":
            "sha256:43b7fe7e8c051729679a605a71913509f9424bbb925135c3a4c8a0ee74330796",
    },
    "outputs": [
        "sha256:41ae602c5e6815cc3f4d31cd2e1c1cd0e458ce43bc3973c050b4921a4bc1c9b7",
        "sha256:57f15c73e2bad9e1c7647fbc2e0cb680b7cb6352dfca5d72c1738d907fadfc9d",
        "sha256:2a569505e9e63ce2543b513ce0341738f5952152c30fb3b2e1fa8ddc72aa3b29",
        "sha256:d87f2a99c3040d4ffe46ee5e1c6b81cacb5b8956ccb5c62316447138c02fdd2f",
        "sha256:63f038986e1379180c8fbbcc0dbcd995782805d64e4bb1b3cd3f427ac42c54e5",
        "sha256:959eb46a2bfcd24a9862e85ca051ff11950cf7a3cb232ee7c82b3a661eeb0e42",
        "sha256:959eb46a2bfcd24a9862e85ca051ff11950cf7a3cb232ee7c82b3a661eeb0e42",
        "sha256:9fac5f29db46fe2668b73a4507b0c976eae80286547f4e3108b8c5db16799cb8",
        "sha256:241a3ca0c7944c139fa41f35293a402856d29e05317dfa8229076d6cbfda384b",
        "sha256:04f01a3fa02b803bfb40851827e36a2ffe2a4f169e6dcb6fa8289a97976d9d34",
        "sha256:738ab1650128715429b5c087efee1484d23ac7168b9435f0bea287291827524b",
        "sha256:3817d097b8d4f6463446b0d46ab75656dffb9da0bea7eb68731433c31d5fe0aa",
    ],
}

OPERATION_GOLDEN = {
    "environment": "sha256:9997854f6d8c6eac9c2d5e72eb1162b7104d59cfbd4322acba98df123b62336b",
    "verification": "sha256:f12c13e4555da039f3b6c446795949178c3c2379895ab39949ddecf47830d251",
}

BACKLOG_GOLDEN = {
    "check": "sha256:2f97d3071160f3ea4d75fba6b954b0d895156f1e1ab0fd310309d2620330c385",
    "check_approved": "sha256:2f97d3071160f3ea4d75fba6b954b0d895156f1e1ab0fd310309d2620330c385",
    "epic_manifest": "sha256:15725abe32259457139fb3419218742be8fcb272a2d287182f026f80527f3609",
    "root_manifest": "sha256:a75772637fce2c1d2d6f8521887fda8b3c2467b17602fafcca429b88f96d7cdb",
}

# Every backlog file the fixture's approval writes without a Process Policy.
APPROVAL_GOLDEN = {
    "backlog/_generated/board.md":
        "sha256:ff176d94f2480cab35a308d85d448fc30f1d359df2d1c0cc1ced15bbca8bd20b",
    "backlog/_generated/dependency-map.md":
        "sha256:8d612f10319a41282d6e5f2bd220f0a7c69e194b5dfb7beb591d94fbe03684a3",
    "backlog/_generated/registry.json":
        "sha256:52a4a9d573986fb5c7e71e0d3aea83c66f00c1227377a2d9518b902b13b7d4b6",
    "backlog/_generated/test-coverage.md":
        "sha256:33b9e6db44f8b6c5eb1a55144e86fb45f8cd5f3de7c0c9281726462f14eeabe9",
    "backlog/backlog.md":
        "sha256:e5a31a6dee03701ed38f4f7faa06b98eb50215d45ed1d2cde2eb2d5a5565f466",
    "backlog/epics/delivery-fixture/epic.md":
        "sha256:fb807f9a658b8726e0802aae30d79b145f2302246ccbd12449d76458dc240095",
    "backlog/epics/delivery-fixture/reviews/round-1-epic-review.md":
        "sha256:bd8f795acddc4dbdc9c7439d9150d1e0032b7d94efc6fed2ef715d84479cfbc9",
    "backlog/epics/delivery-fixture/stories/auth-01/story.md":
        "sha256:cd64c339adde066db9741778b028da4f979945f1bfeadeaaa6b9be26e34a87dd",
    "backlog/epics/delivery-fixture/stories/auth-01/test-plan.md":
        "sha256:de120945533d021dc49d5f2f12598a460d37235e4d4d6244baa867ed866a656b",
    "backlog/reviews/round-1-backlog-review.md":
        "sha256:ad38462e118bc1181066a5af8cd4bc9621f5efdb5bb290c63868827f2bec89a6",
}

BACKLOG_TASK_GOLDEN = {
    "epic_reader": {
        "backlog_scope": "sha256:f9b45d773fd3b467a84ee8b3676119acb8583c1c2572dc36dc7bc2cdb9283663",
        "canonical_source_inventory":
            "sha256:11227584170b32c54464b3316068efcd374964bbc77ba9103f9c2630a5fb882c",
        "structure_hash": "sha256:536a05972e329c5d5c75beebd1d63a08e48625c5ba3fba37052e3319ea528cf3",
        "task": "sha256:fba91d9ea2df62536e8bd4b491f44882722cf8466612ff526dff023f0a4b443f",
    },
    "epic_writer": {
        "backlog_scope": "sha256:f9b45d773fd3b467a84ee8b3676119acb8583c1c2572dc36dc7bc2cdb9283663",
        "canonical_source_inventory":
            "sha256:11227584170b32c54464b3316068efcd374964bbc77ba9103f9c2630a5fb882c",
        "structure_hash": "sha256:536a05972e329c5d5c75beebd1d63a08e48625c5ba3fba37052e3319ea528cf3",
        "task": "sha256:c4d1165f344d7526c1f14f6062ac27ff135997b882d37869183180ce572b9c6a",
    },
    "root_writer": {
        "backlog_scope": "sha256:66c7e2c341c3330d45264cf27f029fa964a506ff62bd16956e8148bcb3d8b342",
        "canonical_source_inventory":
            "sha256:11227584170b32c54464b3316068efcd374964bbc77ba9103f9c2630a5fb882c",
        "structure_hash": "sha256:536a05972e329c5d5c75beebd1d63a08e48625c5ba3fba37052e3319ea528cf3",
        "task": "sha256:cc2328e34e5ecde844cf8b18d83d244f0a1abe7f4431f078bac0a8c1100cc931",
    },
}

MANIFEST_GOLDEN = {
    "entry:without_switch_files":
        "sha256:e0ca7b6f1cf42fd533887f93ac2591a4ba5de7ee44756e95eaadbe69614fc7de",
    "implementer:without_switch_files":
        "sha256:ee2fe615cda5cb36cd17a2c6f9d77a2140da5ab00dda16f202aba89f2387f9db",
    "package_only:without_switch_files":
        "sha256:28680561f56b2f8dce1a5787c527d68f53a466e561da5e25622ff06e6d9b51be",
    "reader:without_switch_files":
        "sha256:6943efcb97ebf69f5786c63dba2d9269ca2c7a85f4f1e16e45f27ed2a74bed84",
    "writer:without_switch_files":
        "sha256:73d21f2c1c38e51cde995c0d6640e71687f4b9c48bdf13c1d633db9166fb4b22",
}

# The read, conditional and instruction paths and the write scopes of all 72
# shipped tasks without a project, apart from the SHIPPED_ADDITIONS paths,
# which the harness counts instead.
SHIPPED_GOLDEN = {
    "additions": {},
    "tasks": "sha256:d0b71b7ea3c7ef304458f1dbe07ef56af9ee543eecd360e3e065ee62320613ed",
}

# Instruction paths that shipped tasks hash on the default path although no
# e47dbe0 task did: the number of tasks that bind each and why they must. A
# task hashes every package script and every file of the skills it selects,
# because a tool or data file it can run shapes its result.
SHIPPED_ADDITIONS = {
    "scripts/process_policy.py": (
        72, "#332: the Process Policy lifecycle compiler. task_inputs.py and the backlog"
            " and Delivery compilers resolve every switch through it, the default path"
            " included, so every task binds it with the other package scripts."),
    "skill-content/configure/data/process-switches.json": (
        6, "#332: the process switch registry. /configure process lists and changes the"
           " switches from it and process_policy.py resolves each default from it, so every"
           " configure task binds it with the rest of the configure skill."),
    "skill-content/configure/references/process-policy.md": (
        6, "#332: the /configure process procedure, the configure entry's new process"
           " target, which every configure task binds with the rest of its skill."),
}

# Every default-path output that differs from e47dbe0, by harness and by the
# key path of its golden entry: the value this tree produces and the issue
# whose fix changed it.
EXPECTED_DIFFERENCES = {
    "delivery": {
        ("files", "delivery/deliveries/dlv-001-auth/execution-plan.md"): (
            "sha256:735c5ce870037268e919d5c45726bd9651417dbe2d336601b8354eb5e1ce7d39",
            "#322, a deliberate default-path change: execution approval records in"
            " superseded_plan_approvals the source_hash of every earlier approval of the plan,"
            " so publication refuses an approval the Integration has moved past instead of"
            " rolling the Integration back. The run approves execution twice, so the second"
            " approval lists the first, and its source_hash covers the list."),
    },
    "backlog": {
        ("epic_manifest",): (
            "sha256:a3cd4d5d032d7c19d28d8c22c32f4516304eebdb0ab6944413b6d57defe0022e",
            "#340: an epic manifest's structure_hash binds the notes it reads and the story"
            " identities and dependency edges that reach them, no longer every backlog byte,"
            " so another epic's writer leaves the review fresh."),
    },
    "backlog_tasks": {
        **{(task, "canonical_source_inventory"): (
            "sha256:feac275c39338cdf361de164a4cedc2b771b33d7c6032ff3099f3f3d570dbcdc",
            "#341: an exact epic's task lists only the canonical sources its closure reads,"
            " so another epic's writer leaves the task fresh.")
           for task in ("epic_reader", "epic_writer")},
        **{(task, "structure_hash"): (
            "sha256:690b0e1849e64908bc574d2f58185ec48809f187d5aabd31b932a3af7011783c",
            "#340: the epic closure the task binds is the epic manifest, whose structure_hash"
            " binds the notes it reads and the story identities and dependency edges that"
            " reach them.") for task in ("epic_reader", "epic_writer")},
    },
    "approval": {
        ("backlog/epics/delivery-fixture/stories/auth-01/story.md",): (
            "sha256:c579d6ff5a1e1ab0d167f2828397f9cc2a8a770e2f3d8ea3e347793ea05419f0",
            "#306: stub-story no longer writes an empty origin_mode into a legacy backlog's"
            " story, so the story and its source_hash change."),
        ("backlog/backlog.md",): (
            "sha256:1101f9fdf12d5758a2ec64691b222bdf63c1e8852761bcb1d49c3bbf7fc15278",
            "#306: the package hash covers the story's changed source_hash."),
        ("backlog/_generated/registry.json",): (
            "sha256:7d63be2c3e3a97e2836ee9e2ed8611b9ba5e4f94793b9b7a78258b8b4b267e55",
            "#306: the generated registry repeats the package hash."),
    },
    "shipped": {
        ("additions",): ({path: count for path, (count, _reason) in SHIPPED_ADDITIONS.items()},
                         "The files SHIPPED_ADDITIONS lists, each with its issue."),
    },
}


def expected(kind: str, golden: dict) -> dict:
    """Return e47dbe0's golden with this tree's listed differences in place."""
    result = copy.deepcopy(golden)
    for path, (value, _issue) in EXPECTED_DIFFERENCES.get(kind, {}).items():
        target = result
        for key in path[:-1]:
            target = target[key]
        if path[-1] not in target or target[path[-1]] == value:
            raise AssertionError(f"{kind} {path} lists no difference from e47dbe0")
        target[path[-1]] = value
    return result


def digest(value) -> str:
    data = value if isinstance(value, bytes) else value.encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def run_harness(kind: str, root: Path = ROOT) -> dict:
    """Run one harness in a fresh interpreter bound to ``root``'s scripts."""
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--harness", kind, "--root", str(root)],
        capture_output=True, text=True, check=False, timeout=600,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", **GIT_ENV})
    if result.returncode:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout)


# The compilers print native absolute paths, which POSIX digests cannot match.
@unittest.skipIf(os.name == "nt", "golden digests are taken on POSIX hosts")
class DefaultEquivalenceTests(unittest.TestCase):
    maxDiff = None

    def test_delivery_compiler_outputs_match_the_base_on_frozen_inputs(self):
        self.assertEqual(run_harness("delivery"), expected("delivery", DELIVERY_GOLDEN),
                         "run this file with --harness delivery --raw to read the outputs")

    def test_delivery_compiler_outputs_match_the_base_under_a_policy_at_the_defaults(self):
        # A policy that exists but sets only review_panels leaves delivery_path at standard.
        self.assertEqual(run_harness("delivery_policy"), DELIVERY_POLICY_GOLDEN,
                         "run this file with --harness delivery_policy --raw to read the outputs")

    def test_operation_compiler_checks_match_the_base_on_frozen_inputs(self):
        # A contract without an Accepted Minor Findings section checks as released.
        self.assertEqual(run_harness("operation"), expected("operation", OPERATION_GOLDEN),
                         "run this file with --harness operation --raw to read the outputs")

    def test_backlog_compiler_outputs_match_the_base_on_frozen_inputs(self):
        actual = run_harness("backlog")
        golden = expected("backlog", BACKLOG_GOLDEN)
        self.assertEqual({name: value for name, value in actual.items() if ":" not in name},
                         golden, "run this file with --harness backlog --raw to read the outputs")
        # An approved policy that leaves every switch the backlog tools read at
        # its default reads the same.
        self.assertEqual({name.split(":", 1)[1]: value for name, value in actual.items()
                          if name.startswith("policy:")}, golden)

    def test_backlog_tasks_match_the_base_on_frozen_inputs(self):
        # A writer's closure carries a check only for the placeholders it lists.
        self.assertEqual(run_harness("backlog_tasks"),
                         expected("backlog_tasks", BACKLOG_TASK_GOLDEN),
                         "run this file with --harness backlog_tasks --raw to read the tasks")

    def test_backlog_approval_without_a_policy_writes_the_base_bytes(self):
        # An approval records a Process Policy pin only when a policy exists.
        self.assertEqual(run_harness("approval"), expected("approval", APPROVAL_GOLDEN),
                         "run this file with --harness approval --raw to read the files")

    def test_task_manifests_match_the_base_without_a_policy(self):
        actual = run_harness("manifests")
        golden = expected("manifests", MANIFEST_GOLDEN)
        self.assertEqual({name: value for name, value in actual.items()
                          if name.endswith(":without_switch_files")}, golden)
        # Switch references of values no policy chose are neither read nor hashed.
        self.assertEqual({name.replace(":with_", ":without_"): value
                          for name, value in actual.items()
                          if name.endswith(":with_switch_files")}, golden)

    def test_shipped_tasks_bind_the_base_paths_without_a_policy(self):
        # Switch references and switch value data stay out of every default task.
        self.assertEqual(run_harness("shipped"), expected("shipped", SHIPPED_GOLDEN),
                         "run this file with --harness shipped --raw to read the tasks")

    def test_the_invariant_names_the_listed_differences(self):
        # Architecture invariant 30 states the rule these goldens prove,
        # differences included, so it names both lists.
        text = " ".join((ROOT / "docs/architecture.md").read_text(encoding="utf-8").split())
        invariant = text.split(" 30. ", 1)[1].split(" 31. ", 1)[0]
        self.assertIn("apart from the defect fixes that `EXPECTED_DIFFERENCES` in"
                      " `tools/tests/test_default_equivalence.py` lists", invariant)
        self.assertIn("the instruction files its `SHIPPED_ADDITIONS` lists", invariant)

    def test_every_listed_difference_names_its_issue(self):
        for kind, entries in EXPECTED_DIFFERENCES.items():
            for path, (_value, reason) in entries.items():
                with self.subTest(kind=kind, path=path):
                    self.assertRegex(reason, r"#[0-9]+|SHIPPED_ADDITIONS")
        for path, (_count, reason) in SHIPPED_ADDITIONS.items():
            with self.subTest(path=path):
                self.assertRegex(reason, r"^#[0-9]+: ")


# ---------------------------------------------------------------------------
# Harnesses. They import only from the tree named by --root, so the same code
# produces the golden from the base tree and the check from this tree.
# ---------------------------------------------------------------------------


def _freeze_clocks(*modules) -> None:
    from datetime import datetime, timezone

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 1, tzinfo=timezone.utc)

    for module in modules:
        if hasattr(module, "datetime"):
            module.datetime = Frozen
        if hasattr(module, "utc_now"):
            module.utc_now = lambda: FROZEN


def _quiet(call, *args):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = call(*args)
    return code, output.getvalue()


def _policy(process_policy, docs: Path, *argv: str) -> None:
    code, text = _quiet(process_policy.main, [argv[0], "--docs", str(docs), *argv[1:]])
    if code:
        raise AssertionError(text)


def _input_harness(root: Path, raw_output: bool = False) -> dict:
    """Build the Delivery run's inputs with ``root``'s fixture and Operation compiler."""
    sys.path[:0] = [str(root / PLUGIN / "scripts"), str(root / "tools/tests")]
    import backlog_compile
    import operation_compile
    from backlog_fixture import make_approved_backlog

    _freeze_clocks(backlog_compile, operation_compile)
    with tempfile.TemporaryDirectory() as raw:
        project = Path(raw).resolve()
        docs = project / "workspace" / "docs"
        (docs / "maps").mkdir(parents=True)
        make_approved_backlog(docs)
        # The backlog fixture already accepts a Solution decision the contract can cite.
        contract = type("Args", (), {"docs": str(docs), "kind": "verification",
                                     "constrained_by": ["solution-design/decisions/fixture-api"]})
        code, text = _quiet(operation_compile.init, contract)
        if code:
            raise AssertionError(text)
        path = docs / "operation" / "verification-contract.md"
        props, body = operation_compile.parse(path)
        props["test_command"] = "make test"
        operation_compile.atomic_text(path, operation_compile.render(props, body))
        code, text = _quiet(operation_compile.approve, contract)
        if code:
            raise AssertionError(text)
        files = {path.relative_to(project).as_posix(): path.read_bytes().decode("utf-8")
                 for path in sorted(docs.rglob("*")) if path.is_file()}
    return {"schema_version": 1, "files": files}


def _delivery_harness(root: Path, raw_output: bool = False, policy: bool = False) -> dict:
    """Run the Delivery compiler on the frozen inputs, under an approved Process
    Policy that sets only review_panels when *policy* is true."""
    sys.path[:0] = [str(root / PLUGIN / "scripts"), str(root / "tools/tests")]
    import delivery_compile
    from git_fixture import init_repository

    _freeze_clocks(delivery_compile)
    inputs = json.loads(INPUTS.read_text(encoding="utf-8"))
    if inputs.get("schema_version") != 1:
        raise AssertionError(f"{INPUTS.name} has an unsupported schema")
    outputs: list[str] = []
    with tempfile.TemporaryDirectory() as raw:
        project = Path(raw).resolve()
        docs = project / "workspace" / "docs"
        for relative, text in inputs["files"].items():
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
        (project / "workspace" / "config.json").write_bytes(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English"}).encode("utf-8"))
        workflows = project / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "tests.yml").write_bytes(
            b"on:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
            b"    steps:\n      - run: make test\n")
        if policy:
            import process_policy

            _freeze_clocks(process_policy)
            _policy(process_policy, docs, "init")
            _policy(process_policy, docs, "set", "--switch", "review_panels", "--value", "lens_panel")
            _policy(process_policy, docs, "approve")
        init_repository(project, initial_branch="main")
        for args in (("add", "--all"), ("commit", "-q", "-m", "fixture")):
            subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)

        dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        scope = type("Args", (), {"docs": str(docs), "id": None, "slug": "auth",
                                  "goal": "Authenticate", "outcome": "Users sign in",
                                  "target_branch": "main", "story": ["AUTH-01"]})
        plan = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
        review = type("Args", (), {"docs": str(docs), "delivery": "DLV-001",
                                   "reviewed_commit": "1" * 40,
                                   "reviewed_integration_commit": "2" * 40})
        for call, args in ((delivery_compile.init_dod, dod), (delivery_compile.approve_dod, dod),
                           (delivery_compile.init_delivery, scope),
                           (delivery_compile.check_delivery, plan),
                           (delivery_compile.approve_scope, plan)):
            code, text = _quiet(call, args)
            outputs.append(f"{code}\n{text}")
        item = delivery_compile.find_delivery(docs, "DLV-001") / "items" / "auth-01" / "item.md"
        props, body = delivery_compile.split_note(item)
        props["path_claims"] = ["src/auth.py"]
        props["contract_claims"] = ["auth:session"]
        delivery_compile.atomic_text(item, delivery_compile.frontmatter(props, body))
        for call, args in ((delivery_compile.approve_execution, plan),
                           (delivery_compile.approve_execution, plan),
                           (delivery_compile.check_delivery, plan),
                           (delivery_compile.status, plan),
                           (delivery_compile.approve_review, review),
                           (delivery_compile.check_delivery, plan),
                           (delivery_compile.render, plan)):
            code, text = _quiet(call, args)
            outputs.append(f"{code}\n{text}")
        seal = (lambda data: data.decode("utf-8")) if raw_output else digest
        # Only what the Delivery compiler writes; the frozen inputs are not outputs.
        files = {path.relative_to(docs).as_posix(): seal(path.read_bytes())
                 for path in sorted(docs.rglob("*")) if path.is_file() and (
                     path.relative_to(docs).parts[0] == "delivery"
                     or path.relative_to(docs).as_posix() == "maps/delivery.md")}
        # Paths printed by the compilers name the temporary root.
        normalized = [text.replace(str(project), "<project>").replace(raw, "<project>")
                      for text in outputs]
    return {"files": files, "outputs": normalized if raw_output else [digest(text) for text in normalized]}


def _operation_harness(root: Path, raw_output: bool = False) -> dict:
    """Check both Operation contracts of the frozen inputs with ``root``'s compiler."""
    sys.path[:0] = [str(root / PLUGIN / "scripts")]
    import operation_compile

    inputs = json.loads(INPUTS.read_text(encoding="utf-8"))
    outputs = {}
    with tempfile.TemporaryDirectory() as raw:
        project = Path(raw).resolve()
        for relative, text in inputs["files"].items():
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
        docs = project / "workspace" / "docs"
        for kind in ("verification", "environment"):
            code, text = _quiet(operation_compile.main,
                                ["check", "--kind", kind, "--docs", str(docs), "--json"])
            text = f"{code}\n" + text.replace(str(project), "<project>").replace(raw, "<project>")
            outputs[kind] = text if raw_output else digest(text)
    return outputs


def _approval_harness(root: Path, raw_output: bool = False) -> dict:
    """Approve the fixture backlog with ``root``'s compiler and no Process Policy."""
    sys.path[:0] = [str(root / PLUGIN / "scripts"), str(root / "tools/tests")]
    import backlog_compile
    from backlog_fixture import make_approved_backlog

    _freeze_clocks(backlog_compile)
    seal = (lambda data: data.decode("utf-8")) if raw_output else digest
    with tempfile.TemporaryDirectory() as raw:
        docs = Path(raw).resolve() / "workspace" / "docs"
        (docs / "maps").mkdir(parents=True)
        make_approved_backlog(docs)
        return {path.relative_to(docs).as_posix(): seal(path.read_bytes())
                for path in sorted((docs / "backlog").rglob("*")) if path.is_file()}


def _backlog_harness(root: Path, raw_output: bool = False) -> dict:
    """Check the frozen approved backlog and derive its review manifests.

    A manifest's contract_hash hashes the package scripts, which change with
    any release, and its source_hash covers that field, so both are left out.
    Where ``root`` has a Process Policy, the run repeats under an approved
    policy whose only row, mechanical_pass_tier, is a backlog-planning switch
    that moves agent tiers and no compiler output, so story_size_budget,
    review_panels and review_manifest_scope stay at their defaults.
    """
    sys.path[:0] = [str(root / PLUGIN / "scripts")]
    import backlog_compile
    import backlog_review_inputs

    inputs = json.loads(INPUTS.read_text(encoding="utf-8"))
    outputs = {}
    with tempfile.TemporaryDirectory() as raw:
        project = Path(raw).resolve()
        for relative, text in inputs["files"].items():
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
        docs = project / "workspace" / "docs"
        variants = [""]
        if (root / PLUGIN / "scripts/process_policy.py").is_file():
            variants.append("policy:")
        for variant in variants:
            if variant:
                import process_policy

                _freeze_clocks(process_policy)
                _policy(process_policy, docs, "init")
                _policy(process_policy, docs, "set", "--switch", "mechanical_pass_tier",
                        "--value", "mechanical")
                _policy(process_policy, docs, "approve")
            for name, flags in (("check", []), ("check_approved", ["--approved"])):
                code, text = _quiet(backlog_compile.main,
                                    ["check", "--docs", str(docs), "--json", *flags])
                outputs[variant + name] = f"{code}\n" + text.replace(str(project), "<project>")
            for name, epic in (("epic_manifest", "EP-001"), ("root_manifest", None)):
                value = backlog_review_inputs.manifest(docs, epic=epic)
                value = {key: item for key, item in value.items()
                         if key not in {"contract_hash", "source_hash"}}
                outputs[variant + name] = json.dumps(value, indent=2, sort_keys=True)
    return {name: text if raw_output else digest(text) for name, text in sorted(outputs.items())}


FIXTURE_SWITCHES = {"schema_version": 1, "switches": {"fixture_mode": {
    "summary": "How the fixture step runs.", "flows": ["fixture-flow"],
    "values": [{"id": "current", "tradeoffs": "Today's behaviour."},
               {"id": "fast", "tradeoffs": "Fewer passes, unmeasured recall."}],
    "default": "current", "metric": "Minutes per fixture step.",
    "promotion": {"unit": "3 Deliveries", "threshold": "Half the baseline minutes."}}}}
SWITCH_FILES = ("skill-content/fixture-entry/references/switch-fixture_mode-fast.md",
                "skill-content/fixture-method/references/switch-fixture_mode-fast.md")
# Data only the `fast` instructions read; the registry declares it as the value's data.
SWITCH_DATA = "skill-content/fixture-method/data/fast-table.json"
TASKS = {
    "entry": {"entry": "fixture-entry", "role": None, "mode": "review"},
    "writer": {"entry": "fixture-entry", "role": "fixture-writer", "mode": "create",
               "inputs": ["workspace/docs/brief.md"]},
    "reader": {"entry": "fixture-entry", "role": "fixture-reader", "mode": "review",
               "inputs": ["workspace/docs/brief.md"]},
    "package_only": {"entry": "fixture-entry", "role": "fixture-reader", "mode": "review",
                     "project": None},
}
# An implementation role on an approved Item without a lane plan: the
# item_claims resolver grants it every product path the Item claims.
ITEM_RECORD = "workspace/docs/delivery/deliveries/dlv-001-fixture/items/fix-01/item.md"
ITEM_TEXT = ("---\ntype: delivery-item\nstory_id: FIX-01\nstatus: active\nrole_sequence:\n"
             "  - fixture_writer\n  - code_reviewer\n  - qa_engineer\npath_claims:\n  - src\n"
             "  - tests\n  - workspace/docs\n---\n\n# Item\n")
IMPLEMENTER = {"entry": "fixture-entry", "role": "fixture-writer", "mode": "create",
               "inputs": [ITEM_RECORD]}


def build_task_package(package: Path, *, switch_files: bool, resolver: str = "unresolved") -> None:
    """Write the smallest package the task-input catalog accepts."""
    catalog = {
        "schema_version": 1, "modes": ["create", "revise", "consume", "review", "repair"],
        "role_skills": {"fixture_writer": ["fixture-method", "obsidian-vault"],
                        "fixture_reader": ["fixture-method"]},
        "required_role_skills": {"fixture_writer": ["fixture-method"],
                                 "fixture_reader": ["fixture-method"]},
        "entries": {"fixture_entry": {
            "flows": ["fixture-flow"], "roles": ["fixture-writer", "fixture-reader"],
            "scope_kind": "fixture", "project_state": True,
            "write_scope": {"resolver": resolver,
                            "roles": [] if resolver == "unresolved" else ["fixture-writer"]},
            "next_transition": "The fixture compiler approves the exact result."}},
        "read_only_roles": ["fixture-reader"], "required_references": {},
        "stack_reference_by_role": {}, "repair_policy": "Preserve finding ids.",
        "barrier": "Wait for every reader before writing.", "output_contract": "Return the result.",
        "read_only_entry_roles": {}, "technology_method_skills": []}
    files = {
        "constitution.md": "# Constitution\n",
        "templates/task-input-contract.md": "# Derived task inputs\n",
        "templates/task-input-policy.json": json.dumps(catalog, indent=2) + "\n",
        "scripts/task_inputs.py": "# The fixture binds this path; its bytes are fixed.\n",
        "agents/fixture-writer.md": "---\nname: fixture-writer\n---\n\n# Writer\n",
        "agents/fixture-reader.md": "---\nname: fixture-reader\n---\n\n# Reader\n",
        "flows/fixture-flow.md": "# Fixture flow\n\nSwitch `fixture_mode` selects step 2.\n",
        "skill-content/fixture-entry/SKILL.md":
            "---\nname: fixture-entry\nexposure: entry\n---\n\n# Entry\n",
        "skill-content/fixture-method/SKILL.md":
            "---\nname: fixture-method\nexposure: internal\n---\n\n# Method\n\n"
            "- [guide](references/guide.md): the method. Read when reviewing.\n",
        "skill-content/fixture-method/references/guide.md": "# Guide\n",
        "skill-content/fixture-method/data/table.json": "{}\n",
        "skill-content/obsidian-vault/SKILL.md":
            "---\nname: obsidian-vault\nexposure: internal\n---\n\n# Vault\n",
        "skill-content/configure/data/process-switches.json":
            json.dumps(FIXTURE_SWITCHES, indent=2) + "\n",
    }
    if switch_files:
        files.update({path: "# Fast fixture step\n" for path in SWITCH_FILES})
        registry = json.loads(json.dumps(FIXTURE_SWITCHES))
        registry["switches"]["fixture_mode"]["value_data"] = {"fast": [SWITCH_DATA]}
        files["skill-content/configure/data/process-switches.json"] = \
            json.dumps(registry, indent=2) + "\n"
        files[SWITCH_DATA] = "{\"fast\": true}\n"
    for relative, text in files.items():
        path = package / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


def build_task_project(project: Path, extra: dict | None = None) -> None:
    """Write and commit one project whose Git identity is fixed by GIT_ENV."""
    from git_fixture import init_repository

    init_repository(project)
    brief = project / "workspace" / "docs" / "brief.md"
    brief.parent.mkdir(parents=True)
    brief.write_bytes(b"---\ntype: note\n---\n\n# Brief\n")
    for relative, text in (extra or {}).items():
        (project / relative).parent.mkdir(parents=True, exist_ok=True)
        (project / relative).write_bytes(text.encode("utf-8"))
    for args in (("config", "core.autocrlf", "false"), ("add", "--all"),
                 ("commit", "-q", "-m", "fixture")):
        subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True,
                       env={**os.environ, **GIT_ENV})


def task_manifests(task_inputs, package: Path, project: Path) -> dict:
    return {name: task_inputs.manifest(package=package, **{"project": project, **task})
            for name, task in TASKS.items()}


def _manifest_harness(root: Path, raw_output: bool = False) -> dict:
    sys.path[:0] = [str(root / PLUGIN / "scripts"), str(root / "tools/tests")]
    import task_inputs

    result = {}
    with tempfile.TemporaryDirectory() as raw:
        base = Path(raw).resolve()
        project = base / "project"
        build_task_project(project)
        item_project = base / "item-project"
        build_task_project(item_project, {ITEM_RECORD: ITEM_TEXT})
        # The base binds every file of a selected skill, so its golden is taken
        # without switch references; this tree must match it with them present.
        for switch_files in (False, True) if (root / PLUGIN / "scripts/process_policy.py").is_file() \
                else (False,):
            package = base / f"package-{switch_files}"
            build_task_package(package, switch_files=switch_files)
            manifests = task_manifests(task_inputs, package, project)
            item_package = base / f"item-package-{switch_files}"
            build_task_package(item_package, switch_files=switch_files, resolver="item_claims")
            manifests["implementer"] = task_inputs.manifest(
                package=item_package, project=item_project, **IMPLEMENTER)
            for name, manifest in manifests.items():
                text = json.dumps(manifest, indent=2, sort_keys=True)
                result[f"{name}:{'with' if switch_files else 'without'}_switch_files"] = \
                    text if raw_output else digest(text)
    return result


# The backlog tasks task_inputs.py derives with --epic: an epic's reader, that
# epic's Product Owner writer and the writer of the root, which bare --epic names.
BACKLOG_TASKS = {"epic_reader": ("backlog-reviewer", "review", "EP-001"),
                 "epic_writer": ("product-owner", "revise", "EP-001"),
                 "root_writer": ("product-owner", "revise", "")}


def _backlog_task_harness(root: Path, raw_output: bool = False) -> dict:
    """Derive the BACKLOG_TASKS with ``root``'s task_inputs.py on the frozen inputs.

    The tasks bind ``root``'s own package, so every field that hashes it is
    left out: the instructions, whose paths the shipped harness seals, the
    task's source_hash, and the closure's contract_hash and source_hash, as
    the backlog harness leaves them out. The canonical source inventory and
    the closure's structure_hash are sealed apart from the rest of the task
    and of its closure, so a listed difference names exactly one of them.
    """
    sys.path[:0] = [str(root / PLUGIN / "scripts"), str(root / "tools/tests")]
    import task_inputs
    from git_fixture import init_repository

    def seal(value) -> str:
        text = json.dumps(value, indent=2, sort_keys=True)
        return text if raw_output else digest(text)

    inputs = json.loads(INPUTS.read_text(encoding="utf-8"))
    outputs = {}
    with tempfile.TemporaryDirectory() as raw:
        project = Path(raw).resolve()
        for relative, text in inputs["files"].items():
            path = project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))
        (project / "workspace" / "config.json").write_bytes(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English"}).encode("utf-8"))
        init_repository(project, initial_branch="main")
        for args in (("config", "core.autocrlf", "false"), ("add", "--all"),
                     ("commit", "-q", "-m", "fixture")):
            subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True,
                           env={**os.environ, **GIT_ENV})
        for name, (role, mode, epic) in BACKLOG_TASKS.items():
            task = task_inputs.manifest(entry="backlog-plan", role=role, mode=mode,
                                        project=project, epic=epic)
            closure = task["backlog_scope"]
            outputs[name] = {
                "task": seal({key: value for key, value in task.items() if key not in {
                    "instructions", "source_hash", "canonical_source_inventory", "backlog_scope"}}),
                "canonical_source_inventory": seal(task["canonical_source_inventory"]),
                "backlog_scope": seal({key: value for key, value in closure.items() if key not in {
                    "contract_hash", "source_hash", "structure_hash"}}),
                "structure_hash": closure["structure_hash"]}
    return outputs


def _shipped_harness(root: Path, raw_output: bool = False) -> dict:
    """Bind every shipped task of ``root``'s package without a project.

    Only paths and write scopes are sealed: a release changes the bytes of the
    scripts, flows and contracts a task binds, never which files it reads or
    hashes on the default path. A SHIPPED_ADDITIONS path is counted per task
    that hashes it and left out of the sealed paths, so ``tasks`` compares
    with e47dbe0's and ``additions`` with the list.
    """
    sys.path[:0] = [str(root / PLUGIN / "scripts")]
    import task_inputs

    package = root / PLUGIN
    tasks = {}
    additions: dict[str, int] = {}
    for entry, route in sorted(task_inputs.catalog(package)["entries"].items()):
        for role in route["roles"] or [None]:
            for mode in ("create", "review"):
                manifest = task_inputs.manifest(entry=entry, role=role, mode=mode,
                                                package=package)
                instructions = [item["path"] for item in manifest["instructions"]]
                for path in instructions:
                    if path in SHIPPED_ADDITIONS:
                        additions[path] = additions.get(path, 0) + 1
                tasks[f"{entry}:{role}:{mode}"] = {
                    "required_reads": manifest["required_reads"],
                    "conditional_reads": [item["path"] for item in manifest["conditional_reads"]],
                    "instructions": [path for path in instructions
                                     if path not in SHIPPED_ADDITIONS],
                    "write_scope": manifest["write_scope"]}
    text = json.dumps(tasks, indent=2, sort_keys=True)
    return {"additions": dict(sorted(additions.items())),
            "tasks": text if raw_output else digest(text)}


HARNESSES = {"approval": _approval_harness, "backlog": _backlog_harness,
             "backlog_tasks": _backlog_task_harness, "delivery": _delivery_harness,
             "delivery_policy": lambda root, raw_output=False: _delivery_harness(root, raw_output, True),
             "inputs": _input_harness, "manifests": _manifest_harness,
             "operation": _operation_harness, "shipped": _shipped_harness}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--harness", choices=sorted(HARNESSES))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--raw", action="store_true", help="print contents instead of digests")
    arguments, remaining = parser.parse_known_args()
    if arguments.harness:
        print(json.dumps(HARNESSES[arguments.harness](arguments.root.resolve(), arguments.raw),
                         indent=2, sort_keys=True))
    else:
        unittest.main(argv=[sys.argv[0], *remaining])
