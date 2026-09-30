"""Golden all-default runs: with no Process Policy, every compiler output is
byte-identical to the program branch base on the same inputs.

The golden digests below were produced by running the same harness against
the base tree's own scripts (see ``--harness`` at the end of this file). A
change that alters an output on the default path fails here; a deliberate
change updates the digest with its reason in the same commit.
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

# Produced from the program branch base e56acfb; see the module docstring.
DELIVERY_GOLDEN = {
    "files": {
        "delivery/definition-of-done.md":
            "sha256:7b766655dd95e17b5b8a8a72dacad577524c8d2010bca777e5eaf85325cde7e0",
        "delivery/deliveries/dlv-001-auth/delivery-review.md":
            "sha256:b0a333c2ade13d09ec57af9a021cc56b262cf6eb6c5835f5306945672e58fdc2",
        "delivery/deliveries/dlv-001-auth/delivery.md":
            "sha256:1f5a115eb6d59522f9e063ed330186ee08bfbbdf5af112ea9f51e4a222f29fe7",
        "delivery/deliveries/dlv-001-auth/execution-plan.md":
            "sha256:22d150db0d113bb33be7055161746be6a5f8c902009f2dd261ff667f977b97a2",
        "delivery/deliveries/dlv-001-auth/items/auth-01/code-review.md":
            "sha256:086ddf31ed148d2c1841b67bcca8fa6a14e5203d1d76d15df56878998c573fdc",
        "delivery/deliveries/dlv-001-auth/items/auth-01/item.md":
            "sha256:1aa0e33f0c1b540b3b07e3bff6c81bb853287539f52298c96dabef7697ba8b8f",
        "delivery/deliveries/dlv-001-auth/items/auth-01/verification.md":
            "sha256:5e5383fbd92e87158c7bb76a3cb1c149a30a21e80fe218d7cac18a17c5e08ae5",
        "maps/delivery.md":
            "sha256:20dbcbb97fdc51571a7e1408bb24f7cab9e097958b0139060d2172267c5af1cb",
        "operation/verification-contract.md":
            "sha256:f3263098db23cedb76ec109061ff218e47c9b81cbea5496f10ac20d9edf6794c",
    },
    "outputs": [
        "sha256:285cbd8ab03e626c299061e827b4f5d381785ef807e7f95344f406a57a0e1e8c",
        "sha256:4d0c5c68d47c288dfa2df4b660aee9c69bcb5ac9b4737de15468075a7b33259f",
        "sha256:41ae602c5e6815cc3f4d31cd2e1c1cd0e458ce43bc3973c050b4921a4bc1c9b7",
        "sha256:57f15c73e2bad9e1c7647fbc2e0cb680b7cb6352dfca5d72c1738d907fadfc9d",
        "sha256:2a569505e9e63ce2543b513ce0341738f5952152c30fb3b2e1fa8ddc72aa3b29",
        "sha256:d87f2a99c3040d4ffe46ee5e1c6b81cacb5b8956ccb5c62316447138c02fdd2f",
        "sha256:0836a78aff995e1f889b263c51db6b8a88feae3c9e57fb7a335df5818d837cb7",
        "sha256:1180f236d23b08f6dd1778b0b57edaf5dcf25c04cfd25f6c4aae56c3e5d4d5c3",
        "sha256:1180f236d23b08f6dd1778b0b57edaf5dcf25c04cfd25f6c4aae56c3e5d4d5c3",
        "sha256:9fac5f29db46fe2668b73a4507b0c976eae80286547f4e3108b8c5db16799cb8",
        "sha256:241a3ca0c7944c139fa41f35293a402856d29e05317dfa8229076d6cbfda384b",
        "sha256:da282d8ad787e098212fafd91cdf68d1c1770c094140beb07af0797e887bb037",
        "sha256:738ab1650128715429b5c087efee1484d23ac7168b9435f0bea287291827524b",
        "sha256:3817d097b8d4f6463446b0d46ab75656dffb9da0bea7eb68731433c31d5fe0aa",
    ],
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


# The shared backlog fixture writes its notes in text mode, so native Windows
# fixtures hold CRLF bytes that POSIX digests cannot match.
@unittest.skipIf(os.name == "nt", "golden digests are taken on POSIX hosts")
class DefaultEquivalenceTests(unittest.TestCase):
    maxDiff = None

    def test_delivery_compiler_outputs_match_the_base_without_a_policy(self):
        self.assertEqual(run_harness("delivery"), DELIVERY_GOLDEN)


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


def _delivery_harness(root: Path, raw_output: bool = False) -> dict:
    sys.path[:0] = [str(root / PLUGIN / "scripts"), str(root / "tools/tests")]
    import backlog_compile
    import delivery_compile
    import operation_compile
    from backlog_fixture import make_approved_backlog
    from git_fixture import init_repository

    _freeze_clocks(backlog_compile, delivery_compile, operation_compile)
    outputs: list[str] = []
    with tempfile.TemporaryDirectory() as raw:
        project = Path(raw).resolve()
        docs = project / "workspace" / "docs"
        (docs / "maps").mkdir(parents=True)
        (project / "workspace" / "config.json").write_text(json.dumps({
            "schema_version": 2, "team_id": "software-engineering-team",
            "output_language": "English", "terminology_language": "English"}), encoding="utf-8")
        make_approved_backlog(docs)
        workflows = project / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "tests.yml").write_text(
            "on:\n  pull_request:\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - run: make test\n", encoding="utf-8")
        init_repository(project, initial_branch="main")
        for args in (("add", "--all"), ("commit", "-q", "-m", "fixture")):
            subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)

        # The backlog fixture already accepts a Solution decision the contract can cite.
        decision = "solution-design/decisions/fixture-api"
        if not (docs / f"{decision}.md").is_file():
            raise AssertionError("the backlog fixture no longer ships its accepted decision")
        contract = type("Args", (), {"docs": str(docs), "kind": "verification",
                                     "constrained_by": [decision]})
        outputs.append(_quiet(operation_compile.init, contract)[1])
        dod = type("Args", (), {"docs": str(docs), "title": "Project", "file": None})
        scope = type("Args", (), {"docs": str(docs), "id": None, "slug": "auth",
                                  "goal": "Authenticate", "outcome": "Users sign in",
                                  "target_branch": "main", "story": ["AUTH-01"]})
        plan = type("Args", (), {"docs": str(docs), "delivery": "DLV-001"})
        review = type("Args", (), {"docs": str(docs), "delivery": "DLV-001",
                                   "reviewed_commit": "1" * 40,
                                   "reviewed_integration_commit": "2" * 40})
        path = docs / "operation" / "verification-contract.md"
        props, body = operation_compile.parse(path)
        props["test_command"] = "make test"
        operation_compile.atomic_text(path, operation_compile.render(props, body))
        for call, args in ((operation_compile.approve, contract),
                           (delivery_compile.init_dod, dod), (delivery_compile.approve_dod, dod),
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
        files = {path.relative_to(docs).as_posix(): seal(path.read_bytes())
                 for path in sorted(docs.rglob("*")) if path.is_file() and (
                     path.relative_to(docs).parts[0] in {"delivery", "operation"}
                     or path.relative_to(docs).as_posix() == "maps/delivery.md")}
        # Paths printed by the compilers name the temporary root.
        normalized = [text.replace(str(project), "<project>").replace(raw, "<project>")
                      for text in outputs]
    return {"files": files, "outputs": normalized if raw_output else [digest(text) for text in normalized]}


HARNESSES = {"delivery": _delivery_harness}


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
