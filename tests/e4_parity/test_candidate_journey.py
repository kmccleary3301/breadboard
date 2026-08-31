from __future__ import annotations

import json
from pathlib import Path

import pytest

from breadboard.product.evidence.lane_lock import build_lane_lock
from breadboard.product.evidence.lanes import MANIFEST_SCHEMA_VERSION
from breadboard.product.evidence.workspace import BreadBoardWorkspace
from breadboard.product.evidence.e4 import run_lane
from breadboard.product.evidence.e4.candidate_journey import reverify_candidate_claim
from breadboard.product.evidence.e4.stage_contracts import check_stage_report


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _candidate(
    root: Path,
    *,
    outcome: dict[str, str] | None = None,
    expected_status: str = "completed",
    execute: list[str] | None = None,
) -> tuple[dict[str, object], dict[str, object], Path]:
    lane_id = "product_session_reference"
    references = {
        name: f"refs/{name}.json"
        for name in ("harness", "target", "adapter", "source", "comparator", "policy")
    }
    lane: dict[str, object] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "lane_id": lane_id,
        "status": "candidate",
        "execute": execute or ["capture", "replay", "compare", "claim"],
        "reuse": [],
        "references": references,
        "metadata": {"config_id": "product-session-reference-v1"},
    }
    descriptors = {
        "harness": {"runtime_id": "breadboard-product-session-v1"},
        "target": {
            "family": "breadboard",
            "version": "product-session-v1",
            "config_ids": ["product-session-reference-v1"],
        },
        "adapter": {
            "adapter_id": "product-session-tape-v1",
            "target_families": ["breadboard"],
            "target_versions": ["product-session-v1"],
            "config_ids": ["product-session-reference-v1"],
        },
        "source": {
            "schema_version": "bb.e4.product_session_capture.v1",
            "session_id": "session-e1-reference",
            "task": "complete the deterministic reference task",
            "input": "continue",
            "occurred_at": "2026-08-18T00:00:00Z",
            "effective_lock": {"graph_hash": "sha256:" + "a" * 64},
            "outcome": outcome or {"status": "completed", "summary": "done"},
        },
        "comparator": {
            "comparator_id": "exact-session-json-v1",
            "expected_status": expected_status,
        },
        "policy": {
            "claim_enabled": True,
            "exclusions": [
                "No provider, model, network, browser, or target-family claim is made.",
                "Only the named deterministic product Session lifecycle is covered.",
            ],
        },
    }
    for name, descriptor in descriptors.items():
        _write_json(root / references[name], descriptor)
    lane_dir = root / ".breadboard" / "lanes"
    manifest = lane_dir / f"{lane_id}.manifest.json"
    _write_json(manifest, lane)
    lock = build_lane_lock(lane, root=root, manifest_path=manifest)
    (lane_dir / f"{lane_id}.lock.json").write_text(
        json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return lane, lock, lane_dir


def test_candidate_journey_is_default_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _lane, _lock, lane_dir = _candidate(tmp_path)
    monkeypatch.delenv("BREADBOARD_ENABLE_E4_API", raising=False)

    with pytest.raises(run_lane.LaneRunError, match="explicit BREADBOARD_ENABLE_E4_API=1"):
        run_lane.run_lane(
            "product_session_reference",
            stage="capture",
            out_dir=None,
            lane_def_dir=lane_dir,
        )


def test_candidate_capture_replay_compare_claim_and_restart_reverify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lane, lock, lane_dir = _candidate(tmp_path)
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "1")

    result = run_lane.run_lane(
        "product_session_reference",
        stage="all",
        out_dir=None,
        lane_def_dir=lane_dir,
    )
    restarted = reverify_candidate_claim(lane, lock, root=tmp_path)

    assert result["ok"] is True
    assert [row["stage"] for row in result["stages"]] == [
        "capture",
        "replay",
        "compare",
        "claim",
    ]
    assert result["stages"][0]["product_pass"] is True
    assert result["stages"][2]["e4_pass"] is True
    assert result["stages"][3]["reverified"] is True
    assert all(check_stage_report(row, lane) == [] for row in result["stages"])
    assert restarted == {
        "ok": True,
        "lane_id": "product_session_reference",
        "plan_id": result["stages"][1]["plan_id"],
        "claim_ref": ".breadboard/e4/product_session_reference/claim.json",
    }


def test_claim_refuses_replay_without_claimable_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lane, _lock, lane_dir = _candidate(tmp_path)
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "1")
    run_lane.run_lane(
        "product_session_reference",
        stage="capture",
        out_dir=None,
        lane_def_dir=lane_dir,
    )
    BreadBoardWorkspace(tmp_path).write_json(
        ".breadboard/e4/product_session_reference/comparison.json",
        {"e4_pass": True},
    )

    with pytest.raises(run_lane.LaneRunError, match="claimable completed replay"):
        run_lane.run_lane(
            "product_session_reference",
            stage="claim",
            out_dir=None,
            lane_def_dir=lane_dir,
        )


def test_product_and_e4_outcomes_remain_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lane, _lock, lane_dir = _candidate(
        tmp_path,
        outcome={"status": "failed", "error_code": "fixture", "detail": "expected"},
        expected_status="failed",
        execute=["capture", "replay", "compare"],
    )
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "1")

    result = run_lane.run_lane(
        "product_session_reference",
        stage="all",
        out_dir=None,
        lane_def_dir=lane_dir,
    )

    assert result["ok"] is True
    assert [row["stage"] for row in result["stages"]] == [
        "capture",
        "replay",
        "compare",
    ]
    assert result["stages"][0]["product_pass"] is False
    assert result["stages"][2]["e4_pass"] is True
    assert not (tmp_path / ".breadboard/e4/product_session_reference/claim.json").exists()


def test_product_pass_does_not_override_failed_e4_comparison(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _lane, _lock, lane_dir = _candidate(tmp_path, expected_status="failed")
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "1")

    result = run_lane.run_lane(
        "product_session_reference",
        stage="all",
        out_dir=None,
        lane_def_dir=lane_dir,
    )

    assert result["ok"] is False
    assert result["stages"][0]["product_pass"] is True
    assert result["stages"][2]["e4_pass"] is False
    assert [row["stage"] for row in result["stages"]] == [
        "capture",
        "replay",
        "compare",
    ]
    assert not (tmp_path / ".breadboard/e4/product_session_reference/claim.json").exists()
