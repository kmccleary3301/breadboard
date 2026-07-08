from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COOKBOOK = ROOT / "docs" / "conformance" / "E4_COOKBOOK_V2.md"


def test_cookbook_v2_names_lane_def_and_safe_commands() -> None:
    text = COOKBOOK.read_text(encoding="utf-8")

    assert "scaffold_e4_target_lane.py" in text
    assert "--emit-lane-def" in text
    assert "generate_lane_inventory.py" in text
    assert "run_lane.py" in text
    assert "validate_e4_c4_chain.py" in text
    assert "evidence_roots.py" in text
    assert "../docs_tmp/phase_15/lane_scaffolds" in text


def test_cookbook_v2_has_no_placeholders_or_accepted_root_write_instruction() -> None:
    text = COOKBOOK.read_text(encoding="utf-8")

    forbidden = ["TODO", "INSERT_", "PASTE_", "write directly to accepted", "Overwrite support claims"]
    assert [token for token in forbidden if token in text] == []
    assert "scratch" in text
    assert "Do not overwrite support claims" in text
