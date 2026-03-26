from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_atp_invalid_extract_ledger_v1.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("build_atp_invalid_extract_ledger_v1", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_invalid_extract_ledger_includes_known_unsound_tasks() -> None:
    payload = module.build_payload()
    entries = {entry["task_id"]: entry for entry in payload["entries"]}
    assert payload["schema"] == "breadboard.atp_invalid_extract_ledger.v1"
    assert "mathd_numbertheory_780" in entries
    assert "aime_1984_p5" in entries
    assert "amc12a_2019_p12" in entries
    assert "mathd_algebra_77" in entries
    assert entries["mathd_numbertheory_780"]["affected_pack_count"] >= 1
