"""Items verified at once never share an environment named after a checkout folder:
the kept runtime checkout is named per Item and each command clone per run."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugins/software-engineering-team/scripts"))
import delivery_verification as verification  # noqa: E402


class RuntimeCheckoutNameTests(unittest.TestCase):
    def test_each_item_keeps_its_own_runtime_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            scratch = Path(temporary)
            first = verification.runtime_checkout_name(scratch, "DLV-001", "AUTH-01")
            second = verification.runtime_checkout_name(scratch, "DLV-001", "AUTH-02")
            self.assertEqual(first, "runtime-dlv-001-auth-01")
            self.assertNotEqual(first, second)
            # A checkout an older package kept under the shared name keeps it until torn down.
            (scratch / verification.RUNTIME_CHECKOUT).mkdir()
            self.assertEqual(verification.runtime_checkout_name(scratch, "DLV-001", "AUTH-01"),
                             verification.RUNTIME_CHECKOUT)


if __name__ == "__main__":
    unittest.main()
