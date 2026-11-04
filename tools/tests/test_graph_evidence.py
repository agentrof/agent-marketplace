from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins" / "software-engineering-team" / "scripts"))
import graph_evidence  # noqa: E402
from tools.tests.levels import integration  # noqa: E402


@integration
class GraphEvidenceTests(unittest.TestCase):
    def test_native_query_bounds_failures_and_current_evidence(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "project"
            root.mkdir()
            graph = root / "graph.json"
            graph.write_text('{"nodes": []}')
            executable = Path(sys.executable).resolve()
            fake = root / "fake_graphify.py"
            fake.write_text(
                "import json, os, sys, time\n"
                "from pathlib import Path\n"
                "question = sys.argv[2]\n"
                "if question == 'timeout': time.sleep(30)\n"
                "if question == 'large': print('x' * 10000)\n"
                "elif question == 'unicode': sys.stdout.buffer.write((chr(0x1F642) * 65).encode('utf-8'))\n"
                "elif question == 'truncated': print('[!] TRUNCATED: showing 1 of 9 nodes')\n"
                "elif question == 'empty': pass\n"
                "elif question.startswith('exit'):\n"
                "    print('native failure', file=sys.stderr)\n"
                "    sys.exit(int(question[4:]))\n"
                "elif question == 'mutate':\n"
                "    Path(sys.argv[sys.argv.index('--graph') + 1]).write_text('{}')\n"
                "    print('changed graph')\n"
                "else: print(json.dumps({'question': question, 'args': sys.argv[3:],\n"
                "    'refresh': os.environ.get('GRAPHIFY_NO_AUTO_REFRESH'),\n"
                "    'query_log': os.environ.get('GRAPHIFY_QUERY_LOG_DISABLE')}))\n")
            native_popen = subprocess.Popen
            launched = []

            def launch(argv, **kwargs):
                if len(argv) > 1 and argv[1] == "query":
                    launched.append((argv, kwargs))
                    return native_popen([str(executable), str(fake), *argv[1:]], **kwargs)
                return native_popen(argv, **kwargs)

            def query(question, **kwargs):
                return graph_evidence.run_query(
                    root, graph=graph, question=question, output_dir=root / "evidence",
                    executable=str(executable), **kwargs)

            with mock.patch.object(graph_evidence.subprocess, "Popen", side_effect=launch):
                question = "$(touch not-executed) & value; 'quoted'"
                first = query(question, seed="app.py", context="call")
                self.assertTrue(first["ok"])
                self.assertTrue(graph_evidence.validate(first))
                output = json.loads(first["text"])
                self.assertEqual(output["question"], question)
                self.assertEqual(first["seed"], "app.py")
                self.assertEqual(first["context"], "call")
                self.assertEqual(output["refresh"], "1")
                self.assertEqual(output["query_log"], "1")
                self.assertEqual(launched[0][0][2], question)
                self.assertFalse(launched[0][1]["shell"])
                self.assertFalse((root / "not-executed").exists())
                self.assertFalse(first["skill_invoked"])
                self.assertEqual(first["executable_hash_scope"], "launcher_file")
                self.assertTrue((Path(first["stdout_path"]).parent / "receipt.json").is_file())
                for text in ("large", "truncated"):
                    result = query(text)
                    self.assertFalse(result["ok"])
                    self.assertTrue(result["truncated"])
                    self.assertLessEqual(len(result["text"].encode()), 4096)
                    self.assertFalse(graph_evidence.validate(result))
                with self.assertRaisesRegex(ValueError, "at least 256"):
                    query("large", max_output_bytes=1)
                unicode_result = query("unicode", max_output_bytes=257)
                self.assertFalse(unicode_result["ok"])
                self.assertTrue(unicode_result["truncated"])
                self.assertLessEqual(len(unicode_result["text"].encode()), 257)
                for code in (1, 3, 4):
                    result = query("exit" + str(code))
                    self.assertEqual(result["exit_code"], code)
                    self.assertFalse(result["ok"])
                    self.assertEqual(result["ambiguous"], code == 3)
                    self.assertEqual(result["not_found"], code == 4)
                self.assertFalse(query("empty")["ok"])
                timed_out = query("timeout", timeout_seconds=0.1)
                self.assertTrue(timed_out["timed_out"])
                self.assertTrue(timed_out["cleanup_confirmed"])
                self.assertFalse(timed_out["ok"])
                self.assertIsNotNone(timed_out["exit_code"])
                changed = query("mutate")
                self.assertTrue(changed["inputs_changed"])
                self.assertFalse(changed["ok"])
                self.assertFalse(graph_evidence.validate(first))
                graph.write_text('{"nodes": []}')
                self.assertTrue(graph_evidence.validate(first))
                Path(first["stdout_path"]).write_text("tampered")
                self.assertFalse(graph_evidence.validate(first))
                with self.assertRaises(ValueError):
                    graph_evidence.run_query(root, graph=graph, question="test",
                                             executable=str(executable), output_dir=Path(raw))
                if os.name == "posix":
                    (root / "redirect").symlink_to(Path(raw), target_is_directory=True)
                    with self.assertRaises(ValueError):
                        graph_evidence.run_query(root, graph=graph, question="test",
                                                 executable=str(executable), output_dir=root / "redirect")


if __name__ == "__main__":
    unittest.main()
