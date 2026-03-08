from __future__ import annotations

import json
from pathlib import Path

from scripts.build_python_reference_contract_fixtures import build_python_reference_contract_fixtures


def test_python_reference_contract_fixtures_match_tracked_files() -> None:
    root = Path(__file__).resolve().parents[1]
    fixture_root = root / "conformance" / "engine_fixtures"
    generated = build_python_reference_contract_fixtures()
    for rel, payload in generated.items():
        tracked = json.loads((fixture_root / rel).read_text(encoding="utf-8"))
        assert tracked == payload
