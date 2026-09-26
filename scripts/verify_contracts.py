"""CI / pre-commit verification gate for contract synchronization.
Ensures that generated code matches the specification and detects breaking changes.
Exits with 0 on clean sync, non-zero if code is stale or contracts are violated.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.generate_contracts import (
    CONTRACTS_DIR,
    PY_OUT,
    TS_OUT,
    generate_python,
    generate_typescript,
)


def verify_generated_code_up_to_date() -> bool:
    expected_py = generate_python()
    expected_ts = generate_typescript()

    current_py = PY_OUT.read_text(encoding="utf-8") if PY_OUT.exists() else ""
    current_ts = TS_OUT.read_text(encoding="utf-8") if TS_OUT.exists() else ""

    if current_py != expected_py:
        print("[ERROR] qc/contracts.py is OUT OF SYNC with contracts/*.schema.json!", file=sys.stderr)
        print("Run: python scripts/generate_contracts.py to update generated code.", file=sys.stderr)
        return False

    if current_ts != expected_ts:
        print("[ERROR] web/contracts.d.ts is OUT OF SYNC with contracts/*.schema.json!", file=sys.stderr)
        print("Run: python scripts/generate_contracts.py to update generated code.", file=sys.stderr)
        return False

    return True


def verify_backwards_compatibility() -> bool:
    """Ensure V2 schema does not remove or alter mandatory fields from V1 (breaking change check)."""
    v1 = json.loads((CONTRACTS_DIR / "event-v1.schema.json").read_text(encoding="utf-8"))
    v2 = json.loads((CONTRACTS_DIR / "event-v2.schema.json").read_text(encoding="utf-8"))

    v1_req = set(v1.get("required", []))
    v2_req = set(v2.get("required", []))

    # All required fields in V1 must still be required in V2
    missing_in_v2 = v1_req - v2_req
    if missing_in_v2:
        print(f"[ERROR] Breaking change detected: V2 dropped required fields {missing_in_v2}!", file=sys.stderr)
        return False

    # Event types in V1 must remain present in V2
    v1_enums = set(v1["properties"]["event_type"]["enum"])
    v2_enums = set(v2["properties"]["event_type"]["enum"])
    removed_enums = v1_enums - v2_enums
    if removed_enums:
        print(f"[ERROR] Breaking change detected: V2 removed event_types {removed_enums}!", file=sys.stderr)
        return False

    return True


def main() -> int:
    print("Verifying contract synchronization and backwards compatibility...")
    sync_ok = verify_generated_code_up_to_date()
    compat_ok = verify_backwards_compatibility()

    if not sync_ok or not compat_ok:
        print("\nVerification FAILED.", file=sys.stderr)
        return 1

    print("SUCCESS: Contracts and generated code are in sync and backwards-compatible.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
