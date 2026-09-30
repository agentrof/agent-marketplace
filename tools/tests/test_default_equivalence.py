"""Golden all-default runs: with no Process Policy, every compiler output is
byte-identical to the program branch base on the same inputs.

The golden digests below were produced by running the same harness against
the base tree's own scripts (see ``--harness`` at the end of this file). A
change that alters an output on the default path fails here; a deliberate
change updates the digest with its reason in the same commit.

"The same inputs" is literal. The Delivery run reads its approved backlog,
upstream notes and Verification Contract from the frozen
``default_equivalence_inputs.json`` instead of rebuilding them with the
backlog and Operation compilers, whose own fixes would otherwise move every
Delivery hash that covers them; the Operation run checks the same contract.
``--harness inputs`` rebuilds that file. No golden hides a field: no Delivery
record or Operation receipt carries a package script or instruction hash, and
the manifest run hashes a package the harness writes byte for byte, so
release changes to scripts and contracts reach none of them.
"""

from __future__ import annotations

import argparse
import contextlib
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

# Taken on frozen inputs at program tip 95298e5; 679de01, the tip before the
# process switches, and the base e56acfb produce the same digests.
DELIVERY_GOLDEN = {
    "files": {
        "delivery/definition-of-done.md":
            "sha256:7b766655dd95e17b5b8a8a72dacad577524c8d2010bca777e5eaf85325cde7e0",
        "delivery/deliveries/dlv-001-auth/delivery-review.md":
            "sha256:35f3bc263ef98d690ad8ca80d40c5e34a6d36c8698e9d8e764ea0011afb1e37d",
        "delivery/deliveries/dlv-001-auth/delivery.md":
            "sha256:9cb7bac61dcc6a118207143170212f7f073cb3e5e983ffa02bc6f907256e44c5",
        "delivery/deliveries/dlv-001-auth/execution-plan.md":
            "sha256:735c5ce870037268e919d5c45726bd9651417dbe2d336601b8354eb5e1ce7d39",
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

# Taken on frozen inputs at program tip 18a7a35; 038daef and the base e56acfb
# produce the same digests.
OPERATION_GOLDEN = {
    "environment": "sha256:9997854f6d8c6eac9c2d5e72eb1162b7104d59cfbd4322acba98df123b62336b",
    "verification": "sha256:f12c13e4555da039f3b6c446795949178c3c2379895ab39949ddecf47830d251",
}

# Taken at program tip 95298e5; 679de01 and e56acfb produce the same digests.
# The implementer digest was taken at program tip 0ffe0fb and matches e56acfb.
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
        self.assertEqual(run_harness("delivery"), DELIVERY_GOLDEN,
                         "run this file with --harness delivery --raw to read the outputs")

    def test_operation_compiler_checks_match_the_base_on_frozen_inputs(self):
        # A contract without an Accepted Minor Findings section checks as released.
        self.assertEqual(run_harness("operation"), OPERATION_GOLDEN,
                         "run this file with --harness operation --raw to read the outputs")

    def test_task_manifests_match_the_base_without_a_policy(self):
        actual = run_harness("manifests")
        self.assertEqual({name: value for name, value in actual.items()
                          if name.endswith(":without_switch_files")}, MANIFEST_GOLDEN)
        # Switch references of values no policy chose are neither read nor hashed.
        self.assertEqual({name.replace(":with_", ":without_"): value
                          for name, value in actual.items()
                          if name.endswith(":with_switch_files")}, MANIFEST_GOLDEN)


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


def _delivery_harness(root: Path, raw_output: bool = False) -> dict:
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


FIXTURE_SWITCHES = {"schema_version": 1, "switches": {"fixture_mode": {
    "summary": "How the fixture step runs.", "flows": ["fixture-flow"],
    "values": [{"id": "current", "tradeoffs": "Today's behaviour."},
               {"id": "fast", "tradeoffs": "Fewer passes, unmeasured recall."}],
    "default": "current", "metric": "Minutes per fixture step.",
    "promotion": {"unit": "3 Deliveries", "threshold": "Half the baseline minutes."}}}}
SWITCH_FILES = ("skill-content/fixture-entry/references/switch-fixture_mode-fast.md",
                "skill-content/fixture-method/references/switch-fixture_mode-fast.md")
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


HARNESSES = {"delivery": _delivery_harness, "inputs": _input_harness,
             "manifests": _manifest_harness, "operation": _operation_harness}


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
