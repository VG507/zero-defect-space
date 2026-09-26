import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.verify_contracts import (
    verify_backwards_compatibility,
    verify_generated_code_up_to_date,
)


class ContractSyncTests(unittest.TestCase):
    def test_generated_contracts_in_sync(self):
        self.assertTrue(
            verify_generated_code_up_to_date(),
            "Generated files (qc/contracts.py, web/contracts.d.ts) are out of date. Run scripts/generate_contracts.py",
        )

    def test_backwards_compatibility_v1_to_v2(self):
        self.assertTrue(
            verify_backwards_compatibility(),
            "Breaking changes detected between V1 and V2 contracts.",
        )


if __name__ == "__main__":
    unittest.main()
