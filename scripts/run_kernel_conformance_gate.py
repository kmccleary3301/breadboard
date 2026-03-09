from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_python_reference_contract_fixtures import write_python_reference_contract_fixtures
from scripts.validate_kernel_contract_fixtures import validate_kernel_contract_fixtures


def main() -> int:
    write_python_reference_contract_fixtures()
    errors = validate_kernel_contract_fixtures()
    if errors:
        for error in errors:
            print(error)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
