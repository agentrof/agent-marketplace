"""Proof reuse binds observed inputs and refuses missing or corrupt evidence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from tools.tests.git_fixture import init_repository
from tools.tests.levels import integration

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
import proof_cache


class ProofCacheTests(unittest.TestCase):
    def test_only_complete_matching_unexpired_sealed_success_is_reused(self):
        inputs = {"schema_version": 1, "sha256": "a" * 64}
        commands = ["first", "second"]
        record = {"proof_inputs": inputs, "ok": True, "proof_state": "observed",
                  "tree_clean": True, "finished_at": datetime.now(timezone.utc).isoformat(),
                  "commands": [{"command": command, "exit_code": 0} for command in commands]}
        sealed = proof_cache.seal(record)
        self.assertNotIn("proof_digest", record)
        self.assertTrue(proof_cache.reusable(sealed, inputs, commands))
        self.assertFalse(proof_cache.reusable(record, inputs, commands))
        for changes in ({"ok": False}, {"tree_clean": False}, {"proof_state": "pending"},
                        {"status": "running"}, {"proof_inputs": None}, {"commands": []},
                        {"commands": [{"command": "first", "exit_code": 0}]},
                        {"commands": [{"command": "second", "exit_code": 0},
                                      {"command": "first", "exit_code": 0}]},
                        {"finished_at": "invalid"}, {"finished_at": "2026-01-01T00:00:00"},
                        {"finished_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()},
                        {"finished_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()}):
            with self.subTest(changes=changes):
                self.assertFalse(proof_cache.reusable(proof_cache.seal({**record, **changes}), inputs, commands))
        for changes in ({"exit_code": 1}, {"exit_code": False}, {"timed_out": True},
                        {"leftover_children": True}):
            modified = {**record, "commands": [{**record["commands"][0], **changes}, record["commands"][1]]}
            self.assertFalse(proof_cache.reusable(proof_cache.seal(modified), inputs, commands))
        self.assertFalse(proof_cache.reusable({**sealed, "task": "modified"}, inputs, commands))
        self.assertFalse(proof_cache.reusable(sealed, None, commands))
        self.assertFalse(proof_cache.reusable(sealed, {**inputs, "sha256": "b" * 64}, commands))
        for invalid in ({"schema_version": 1}, {"schema_version": True, "sha256": "a" * 64},
                        {"schema_version": 1, "sha256": "z" * 64}):
            self.assertFalse(proof_cache.reusable(proof_cache.seal({**record, "proof_inputs": invalid}), invalid, commands))
        for age in (0, True, 86401):
            self.assertFalse(proof_cache.reusable(sealed, inputs, commands, age))
        empty = proof_cache.seal({**record, "selection": "impact", "commands": []})
        self.assertTrue(proof_cache.reusable(empty, inputs, []))

    @integration
    def test_snapshot_binds_source_dependencies_runtime_scope_and_private_environment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            init_repository(root)
            source = root / "app.py"
            source.write_text("value = 1\n", encoding="utf-8")
            (root / ".gitignore").write_text(".runtime/\nprobe\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            commit = ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                      "commit", "-qm", "fixture"]
            subprocess.run(commit, cwd=root, check=True)
            runtime = root / ".runtime"
            runtime.mkdir()
            dependency = runtime / "dependency"
            dependency.write_text("generation-one", encoding="utf-8")
            package = runtime / "runner.py"
            package.write_text("pass\n", encoding="utf-8")
            config = {"proof_reuse": {"identity_command": [sys.executable, "-c",
                      "from pathlib import Path; print(Path('.runtime/dependency').read_text())"]}}

            def snapshot(**changes):
                values = {"root": root, "commands": ["test"], "runner": "", "scope": {"task": "TK-001"},
                          "config": config, "runtime_files": [package]}
                return proof_cache.snapshot(**{**values, **changes})

            with mock.patch.dict(os.environ, {"PROOF_TEST_SECRET": "private-first-value"}):
                first = snapshot()
                self.assertIsNotNone(first)
                self.assertEqual(snapshot(), first)
                self.assertNotIn("private-first-value", json.dumps(first))
                key = root / ".git/agentrof/runtime/proof-key"
                self.assertEqual(len(key.read_bytes()), 32)
                if os.name == "posix":
                    self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
                with mock.patch.dict(os.environ, {"_": "other", "SHLVL": "99", "PWD": "other", "OLDPWD": "other"}):
                    self.assertEqual(snapshot(), first)
                with mock.patch.dict(os.environ, {"PROOF_TEST_SECRET": "private-second-value"}):
                    self.assertNotEqual(snapshot(), first)
                for changes in ({"commands": ["other"]}, {"runner": "other"},
                                {"scope": {"task": "TK-002"}}, {"config": {**config, "other": True}}):
                    self.assertNotEqual(snapshot(**changes), first)
                dependency.write_text("generation-two", encoding="utf-8")
                self.assertNotEqual(snapshot(), first)
                second = snapshot()
                package.write_text("changed\n", encoding="utf-8")
                self.assertNotEqual(snapshot(), second)
                source.write_text("value = 2\n", encoding="utf-8")
                self.assertIsNone(snapshot())
                subprocess.run(["git", "add", "app.py"], cwd=root, check=True)
                subprocess.run(commit, cwd=root, check=True)
                self.assertIsNotNone(snapshot())
                self.assertIsNone(snapshot(runtime_files=[runtime / "missing"]))
                self.assertIsNone(snapshot(config={}))
                if os.name == "posix":
                    (root / "probe").symlink_to(Path(sys.executable).resolve())
                    self.assertIsNotNone(snapshot(config={"proof_reuse": {
                        "identity_command": ["./probe", "-c", "print('identity')"]}}))
                    key.unlink()
                    key.symlink_to(dependency)
                    self.assertIsNone(snapshot())
                    self.assertEqual(dependency.read_text(), "generation-two")

    @integration
    def test_bounded_probe_failures_and_restored_source_changes_disable_reuse(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            init_repository(root)
            (root / "app.py").write_text("original\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                            "commit", "-qm", "fixture"], cwd=root, check=True)

            def snapshot(script, *arguments):
                config = {"proof_reuse": {"identity_command": [sys.executable, "-c", script, *arguments]}}
                return proof_cache.snapshot(root, ["test"], "", {}, config, [])

            literal = "$(touch not-executed) | secret"
            self.assertIsNotNone(snapshot("import sys; print(sys.argv[1])", literal))
            self.assertFalse((root / "not-executed").exists())
            for script in ("pass", "print(' ')", "raise SystemExit(7)", "print('x' * 65537)",
                           "from pathlib import Path; p=Path('app.py'); old=p.read_bytes(); "
                           "p.write_bytes(b'changed'); p.write_bytes(old); print('identity')"):
                with self.subTest(script=script):
                    self.assertIsNone(snapshot(script))
            started = time.monotonic()
            with mock.patch.object(proof_cache, "PROBE_SECONDS", 0.1):
                self.assertIsNone(snapshot("import time; time.sleep(30)"))
            self.assertLess(time.monotonic() - started, 5)

    @integration
    def test_unconfirmed_probe_cleanup_blocks_further_proof(self):
        import task_lane

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            init_repository(root)
            (root / "app.py").write_text("original\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                            "commit", "-qm", "fixture"], cwd=root, check=True)
            command = [sys.executable, "-c", "import sys, time; "
                       "sys.stdout.write('x' * 65537); sys.stdout.flush(); time.sleep(30)"]
            config = {"proof_reuse": {"identity_command": command}}
            native_stop = proof_cache._stop
            processes = []

            def failed_stop(process):
                processes.append(process)
                raise PermissionError("PRIVATE_CLEANUP_ERROR")

            try:
                with mock.patch.object(proof_cache, "_stop", side_effect=failed_stop):
                    with self.assertRaises(proof_cache.ProbeCleanupUnconfirmed) as failure:
                        proof_cache.snapshot(root, ["test"], "", {}, config, [])
                self.assertEqual(len(processes), 1)
                self.assertIsNone(processes[0].poll())
                self.assertEqual(failure.exception.pid, processes[0].pid)
                self.assertNotIn("PRIVATE_", str(failure.exception))
                blocked = json.loads((root / ".git/agentrof/runtime/proof.blocked.json").read_text())
                self.assertEqual(blocked["pid"], processes[0].pid)
                self.assertEqual(blocked["reason"], "identity_probe_cleanup_unconfirmed")
                self.assertNotIn("PRIVATE_", json.dumps(blocked))
                with self.assertRaisesRegex(task_lane.Refused, "proof cleanup needs reconciliation"):
                    with task_lane.proof_admission(root):
                        self.fail("unconfirmed probe cleanup admitted another proof")
            finally:
                for process in processes:
                    native_stop(process)


if __name__ == "__main__":
    unittest.main()
