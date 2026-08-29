from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "plugins/software-engineering-team/scripts"
HOOK = ROOT / "platforms/shared/software-engineering-team/overlay/scripts/vault_hook.py"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def load_hook():
    spec = importlib.util.spec_from_file_location("opaque_vault_hook", HOOK)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class VaultHookPrototypeTests(unittest.TestCase):
    def setUp(self):
        self.hook = load_hook()

    def test_hook_has_no_application_surface_or_content_guard(self):
        source = HOOK.read_text(encoding="utf-8")
        self.assertNotIn("application.html", source)
        self.assertNotIn("application-map", source)
        self.assertNotIn("experience-application-runtime", source)

    def test_recovery_tracks_any_prototype_file_as_tree_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            docs = Path(temporary) / "workspace/docs"
            application = docs / "experience-design/artifacts/application.html"
            application.parent.mkdir(parents=True)
            application.write_text("arbitrary\n", encoding="utf-8")
            before = self.hook.experience_tree_snapshot(docs)
            application.write_text("changed\n", encoding="utf-8")
            after = self.hook.experience_tree_snapshot(docs)
            relative = "experience-design/artifacts/application.html"
            self.assertIn(relative, before)
            self.assertIn(relative, after)
            self.assertNotEqual(
                before[relative]["content_sha256"], after[relative]["content_sha256"],
            )


if __name__ == "__main__":
    unittest.main()
