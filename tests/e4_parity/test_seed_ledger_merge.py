from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from scripts.e4_parity import seed_atomic_feature_ledger as seed


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


def test_seed_write_replaces_ambient_rows_and_second_run_is_byte_stable(tmp_path: Path) -> None:
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

    assert summary["row_count"] == len(seeded["rows"])
    assert json.loads(first_bytes) == seeded
    assert foreign["feature_id"] not in {
        row["feature_id"] for row in seeded["rows"]
    }

    seed.write_ledger(out_path, index_path)
    assert out_path.read_bytes() == first_bytes


def test_seed_spec_change_updates_seed_rows_without_preserving_ambient_rows(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    index_path = _index_path(tmp_path)
    original = seed.build_ledger(index_path)
    foreign = _foreign_row(original["rows"][0])
    out_path = tmp_path / "ledger.json"
    _write_json(
        out_path,
        {
            **original,
            "row_count": len(original["rows"]) + 1,
            "rows": original["rows"] + [foreign],
        },
    )

    changed_specs = copy.deepcopy(seed.SEED_SPECS)
    for spec in changed_specs:
        if seed._dedupe_key(spec) == original["rows"][0]["dedupe_key"]:
            spec["source_refs"] = ["source:changed_seed_spec.py:10-20"]
            break
    else:  # pragma: no cover - protects the fixture from drifting silently
        raise AssertionError("unable to find first generated seed spec")
    monkeypatch.setattr(seed, "SEED_SPECS", changed_specs)

    seed.write_ledger(out_path, index_path)
    rewritten = json.loads(out_path.read_text(encoding="utf-8"))

    assert rewritten["rows"][0]["feature_id"] == original["rows"][0]["feature_id"]
    assert rewritten["rows"][0]["source_refs"] == ["source:changed_seed_spec.py:10-20"]
    assert foreign["feature_id"] not in {
        row["feature_id"] for row in rewritten["rows"]
    }
    assert rewritten["row_count"] == len(original["rows"])


def test_promoted_lane_rows_come_from_tracked_extension_fixture(tmp_path: Path) -> None:
    seeded = seed.build_ledger(_index_path(tmp_path))
    extensions = seed._load_lane_seed_extensions()
    refreshed_extensions = [
        {
            **row,
            "fixture_refs": seed._refresh_c4_fixture_refs(
                str(row["e4_row_ref"]),
                list(row["fixture_refs"]),
            ),
        }
        if row.get("evidence_tier") == "C4"
        else row
        for row in extensions
    ]

    assert len(extensions) == 13
    assert seeded["rows"][-len(extensions) :] == refreshed_extensions
    assert seeded["row_count"] == len(seed.SEED_SPECS) + len(extensions)
    assert "feat_b3bcb7697f3ea258" in {
        row["feature_id"] for row in extensions
    }


def test_c4_extension_fixture_hashes_are_refreshed_from_current_files(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    config_id = "fixture_config_v1"
    freeze_path = tmp_path / "config/e4_target_freeze_manifest.yaml"
    capture_path = tmp_path / "docs/capture.json"
    freeze_path.parent.mkdir(parents=True)
    capture_path.parent.mkdir(parents=True)
    freeze_path.write_text(
        "e4_configs:\n  fixture_config_v1:\n    target: omp\n",
        encoding="utf-8",
    )
    capture_path.write_text('{"fresh":true}\n', encoding="utf-8")
    validator_path = tmp_path / "docs/validator.json"
    validator_path.write_text('{"claim_derived":true}\n', encoding="utf-8")
    monkeypatch.setattr(seed, "REPO_ROOT", tmp_path)

    refs = seed._refresh_c4_fixture_refs(
        config_id,
        [
            f"freeze:config/e4_target_freeze_manifest.yaml#{config_id}#sha256:{'0' * 64}",
            f"capture:docs/capture.json#sha256:{'1' * 64}",
            "support_claim:docs/support_claim.json",
            f"validator_output:docs/validator.json#sha256:{'2' * 64}",
        ],
    )

    assert refs[0] != f"freeze:config/e4_target_freeze_manifest.yaml#{config_id}#sha256:{'0' * 64}"
    assert refs[0].startswith(f"freeze:config/e4_target_freeze_manifest.yaml#{config_id}#sha256:")
    assert refs[1] == f"capture:docs/capture.json#{seed._hash_utils.sha256_path(capture_path)}"
    assert refs[2] == "support_claim:docs/support_claim.json"
    assert refs[3] == "validator_output:docs/validator.json"


def test_c4_seed_specs_are_derived_from_lane_defs() -> None:
    specs = seed._lane_def_c4_seed_specs()
    refs = {spec["e4_row_ref"] for spec in specs}

    assert "oh_my_pi_p6_0_l2_tool_execution_v1" in refs
    assert "codex_cli_gpt55_e4_capture_probe_v1" in refs
    assert all(spec["evidence_tier"] == "C4" for spec in specs)
    assert all(spec["promotion_state"] == "ready" for spec in specs)


def test_default_seed_write_matches_declared_sources(tmp_path: Path) -> None:
    out_path = tmp_path / "ledger.json"
    out_path.write_text('{"ambient": true}\n', encoding="utf-8")

    seed.write_ledger(out_path, seed.DEFAULT_INDEX_OUT)

    assert json.loads(out_path.read_text(encoding="utf-8")) == seed.build_ledger(
        seed.DEFAULT_INDEX_OUT
    )
