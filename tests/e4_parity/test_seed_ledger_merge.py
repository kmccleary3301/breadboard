from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from scripts.e4_parity import seed_atomic_feature_ledger as seed
from scripts.e4_parity.lane_runtime import upsert_ledger_row


def _write_json(path: Path, payload: dict[str, Any]) -> bytes:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")
    return text.encode("utf-8")


def _index_path(tmp_path: Path) -> Path:
    path = tmp_path / "source_index.json"
    _write_json(path, {"schema_version": "bb.e4.source_index.v1", "entries": []})
    return path


def _foreign_row(seed_row: dict[str, Any], feature_id: str = "feat_foreign_lane_row") -> dict[str, Any]:
    row = copy.deepcopy(seed_row)
    row["feature_id"] = feature_id
    row["dedupe_key"] = f"foreign/lane/{feature_id}/capture/model_yes/stateful_yes"
    row["target"] = "breadboard"
    row["family"] = "task"
    row["claim_type"] = "capture"
    row["source_refs"] = ["source:foreign/lane.py:1-2"]
    row["breadboard_mapping"] = {"primitive": "bb.work_item.v1", "support": "supported"}
    return row


def test_seed_merge_preserves_foreign_rows_and_second_run_is_byte_stable(tmp_path: Path) -> None:
    index_path = _index_path(tmp_path)
    seeded = seed.build_ledger(index_path)
    foreign = _foreign_row(seeded["rows"][0])
    out_path = tmp_path / "ledger.json"
    _write_json(
        out_path,
        {
            **seeded,
            "row_count": 3,
            "rows": [seeded["rows"][0], seeded["rows"][1], foreign],
        },
    )

    summary = seed.write_ledger(out_path, index_path)
    first_bytes = out_path.read_bytes()
    merged = json.loads(first_bytes)
    seed_feature_ids = [row["feature_id"] for row in seeded["rows"]]

    assert summary["row_count"] == len(seeded["rows"]) + 1
    assert [row["feature_id"] for row in merged["rows"][: len(seeded["rows"])] ] == seed_feature_ids
    assert merged["rows"][-1] == foreign

    seed.write_ledger(out_path, index_path)
    assert out_path.read_bytes() == first_bytes


def test_seed_spec_change_updates_seed_rows_and_keeps_foreign_row(tmp_path: Path, monkeypatch: Any) -> None:
    index_path = _index_path(tmp_path)
    original = seed.build_ledger(index_path)
    foreign = _foreign_row(original["rows"][0])
    out_path = tmp_path / "ledger.json"
    _write_json(out_path, {**original, "row_count": len(original["rows"]) + 1, "rows": original["rows"] + [foreign]})

    changed_specs = copy.deepcopy(seed.SEED_SPECS)
    for spec in changed_specs:
        if seed._dedupe_key(spec) == original["rows"][0]["dedupe_key"]:
            spec["source_refs"] = ["source:changed_seed_spec.py:10-20"]
            break
    else:  # pragma: no cover - protects the fixture from drifting silently
        raise AssertionError("unable to find first generated seed spec")
    monkeypatch.setattr(seed, "SEED_SPECS", changed_specs)

    seed.write_ledger(out_path, index_path)
    merged = json.loads(out_path.read_text(encoding="utf-8"))

    assert merged["rows"][0]["feature_id"] == original["rows"][0]["feature_id"]
    assert merged["rows"][0]["source_refs"] == ["source:changed_seed_spec.py:10-20"]
    assert merged["rows"][-1] == foreign
    assert merged["row_count"] == len(original["rows"]) + 1


def test_seed_after_lane_upsert_is_byte_identical_to_post_upsert_state(tmp_path: Path) -> None:
    index_path = _index_path(tmp_path)
    out_path = tmp_path / "ledger.json"

    seed.write_ledger(out_path, index_path)
    seeded = json.loads(out_path.read_text(encoding="utf-8"))
    foreign = _foreign_row(seeded["rows"][0])
    upsert_ledger_row(out_path, foreign, feature_id=foreign["feature_id"])
    post_upsert_bytes = out_path.read_bytes()

    seed.write_ledger(out_path, index_path)

    assert out_path.read_bytes() == post_upsert_bytes


def test_c4_seed_specs_are_derived_from_lane_defs() -> None:
    specs = seed._lane_def_c4_seed_specs()
    refs = {spec["e4_row_ref"] for spec in specs}

    assert "oh_my_pi_p6_0_l2_tool_execution_v1" in refs
    assert "codex_cli_gpt55_e4_capture_probe_v1" in refs
    assert all(spec["evidence_tier"] == "C4" for spec in specs)
    assert all(spec["promotion_state"] == "ready" for spec in specs)


def test_seed_write_preserves_existing_lane_upserted_l2_rows_byte_for_byte(tmp_path: Path) -> None:
    out_path = tmp_path / "ledger.json"
    out_path.write_bytes(seed.DEFAULT_OUT.read_bytes())
    before = out_path.read_bytes()

    seed.write_ledger(out_path, seed.DEFAULT_INDEX_OUT)

    assert out_path.read_bytes() == before
