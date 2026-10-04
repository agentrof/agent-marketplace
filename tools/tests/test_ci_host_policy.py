"""The real host lifecycle job reads its runtime from one validated policy."""

from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ci_host_policy as host


POLICY = {"schema_version": 1, "runner_os": "macos-latest", "python": "3.14", "node": "24"}


class HostPolicyTests(unittest.TestCase):
    def root(self, policy: object) -> Path:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        path = root / host.POLICY
        path.parent.mkdir(parents=True)
        path.write_text(policy if isinstance(policy, str) else json.dumps(policy),
                        encoding="utf-8")
        return root

    def test_each_invalid_policy_shape_is_refused(self):
        cases = {
            "not JSON": ("{", "cannot read"),
            "extra key": (dict(POLICY, cache=True), "unsupported host runtime policy"),
            "schema 2": (dict(POLICY, schema_version=2), "unsupported host runtime policy"),
            "other runner": (dict(POLICY, runner_os="self-hosted"), "operating system"),
            "patch Python": (dict(POLICY, python="3.14.1"), "major.minor"),
            "named Node": (dict(POLICY, node="lts"), "Node policy"),
        }
        for name, (policy, message) in cases.items():
            with self.subTest(name), self.assertRaisesRegex(host.PolicyError, message):
                host.host_policy(self.root(policy))

    def test_cli_writes_exactly_the_job_outputs(self):
        root = self.root(POLICY)
        output = root / "github-output"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(host.main(["--root", str(root), "--github-output", str(output)]), 0)
        self.assertEqual(output.read_text(encoding="utf-8"),
                         "runner_os=macos-latest\npython=3.14\nnode=24\n")

    def test_cli_refuses_an_invalid_policy_without_writing_outputs(self):
        root = self.root(dict(POLICY, node="lts"))
        output = root / "github-output"
        with contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertEqual(host.main(["--root", str(root), "--github-output", str(output)]), 1)
        self.assertIn("Node policy", error.getvalue())
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
