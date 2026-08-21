from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from scripts.e4_parity import build_e4_final_readiness_packet as builder


MutationFunction = Callable[..., Any]


MUTATION_FUNCTIONS = (
    "write_notes",
    "update_score_subledger",
    "update_accepted_report",
    "update_baseline",
    "update_progress",
    "write_primitive_readiness",
    "update_validation_report",
    "write_final_report",
    "update_terminal_manifest",
    "update_scorecard",
    "write_final_manifest",
)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _use_synthetic_score_subledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    score_subledger = tmp_path / "BB_E4_SCORE_SUBLEDGER.json"
    _write_json(score_subledger, {"score_rows": []})
    monkeypatch.setattr(builder, "SCORE_SUBLEDGER_PATH", score_subledger)


def _blocked_ct_artifacts(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "result": tmp_path / "ct_scenarios_result_e4_1000.json",
        "rows": tmp_path / "ct_scenarios_rows_e4_1000.json",
        "summary": tmp_path / "conformance_matrix_sync_summary_v1.json",
    }
    _write_json(
        paths["result"],
        {
            "ok": False,
            "status": "fail",
            "planned_count": 1,
            "not_implemented_count": 2,
            "blocking_not_implemented_count": 1,
            "failing_count": 0,
        },
    )
    _write_json(
        paths["rows"],
        [
            {
                "test_id": "ct.synthetic.not_implemented",
                "status": "not_implemented",
            }
        ],
    )
    _write_json(
        paths["summary"],
        {
            "ok": False,
            "failing_rows": 0,
            "planned_rows": 1,
            "not_implemented_rows": 2,
            "blocking_not_implemented_rows": 1,
        },
    )
    return paths


def _passing_ct_artifacts(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "result": tmp_path / "ct_scenarios_result_e4_1000.json",
        "rows": tmp_path / "ct_scenarios_rows_e4_1000.json",
        "summary": tmp_path / "conformance_matrix_sync_summary_v1.json",
    }
    _write_json(
        paths["result"],
        {
            "ok": True,
            "status": "pass",
            "planned_count": 0,
            "not_implemented_count": 0,
            "blocking_not_implemented_count": 0,
            "failing_count": 0,
        },
    )
    _write_json(
        paths["rows"],
        [
            {
                "test_id": "ct.synthetic.pass",
                "status": "pass",
            }
        ],
    )
    _write_json(
        paths["summary"],
        {
            "ok": True,
            "failing_rows": 0,
            "planned_rows": 0,
            "not_implemented_rows": 0,
            "blocking_not_implemented_rows": 0,
        },
    )
    return paths



def _patch_ct_artifacts(monkeypatch: Any, paths: dict[str, Path]) -> None:
    monkeypatch.setattr(builder, "CT_SCENARIOS_RESULT_PATH", paths["result"])
    monkeypatch.setattr(builder, "CT_SCENARIOS_ROWS_PATH", paths["rows"])
    monkeypatch.setattr(builder, "CT_MATRIX_SYNC_SUMMARY_PATH", paths["summary"])


def test_managed_score_rows_include_retired_inventory_producers(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        builder,
        "_inventory",
        lambda: {
            "lanes": [
                {"status": "accepted", "score_row_id": "score_current"},
                {"status": "superseded", "score_row_id": "score_retired"},
            ]
        },
    )

    assert builder.managed_score_row_ids() == {
        "score_current",
        "score_retired",
        builder.P8_SCORE_ROW_ID,
    }


def test_refresh_managed_lane_score_rows_replaces_retired_producer_refs(
    monkeypatch: Any,
) -> None:
    lane = {"lane_id": "retired_lane", "score_row_id": "score_retired"}
    stale_row = {
        "score_row_id": "score_retired",
        "live_validator_ref": "artifacts/conformance/node_gate/retired.json#sha256:stale",
    }
    p8_row = {"score_row_id": builder.P8_SCORE_ROW_ID, "points": 35}
    subledger = {
        "score_rows": [
            {"score_row_id": "score_unmanaged", "points": 10},
            stale_row,
            p8_row,
        ]
    }
    monkeypatch.setattr(builder, "_accounted_lanes", lambda: [lane])
    monkeypatch.setattr(
        builder,
        "build_lane_score_row",
        lambda candidate, existing: {
            "score_row_id": candidate["score_row_id"],
            "live_validator_ref": "docs/conformance/e4_target_support/retired/frozen_c4_validation_report.json#sha256:fresh",
            "previous_ref": existing["live_validator_ref"],
        },
    )

    builder._refresh_managed_lane_score_rows(subledger)

    assert subledger["score_rows"] == [
        {"score_row_id": "score_unmanaged", "points": 10},
        {
            "score_row_id": "score_retired",
            "live_validator_ref": "docs/conformance/e4_target_support/retired/frozen_c4_validation_report.json#sha256:fresh",
            "previous_ref": stale_row["live_validator_ref"],
        },
        p8_row,
    ]


def test_retired_scored_lanes_use_hash_bound_frozen_validators() -> None:
    retired_lanes = [
        lane
        for lane in builder._scored_lanes()
        if lane.get("status") != "accepted"
    ]

    assert retired_lanes
    for lane in retired_lanes:
        validator_path = builder._lane_node_gate_path(lane)
        validator = json.loads(validator_path.read_text(encoding="utf-8"))

        assert validator_path.name == "frozen_c4_validation_report.json"
        assert validator["ok"] is True
        assert validator["accepted"] is True
        assert validator["hashes"]["support_claim"] == builder.sha256_path(
            builder._lane_support_claim_path(lane)
        )
        assert validator["hashes"]["evidence_manifest"] == builder.sha256_path(
            builder._lane_evidence_manifest_path(lane)
        )


def test_prune_stale_current_artifacts_drops_missing_repo_relative_path(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(builder, "ROOT", tmp_path)
    existing_path = tmp_path / "artifacts" / "existing.json"
    _write_json(existing_path, {"status": "current"})
    existing_artifact = {"path": "artifacts/existing.json", "role": "evidence"}
    payload = {
        "current_artifacts": [
            existing_artifact,
            {"path": "artifacts/missing.json", "role": "evidence"},
        ]
    }

    builder.prune_stale_current_artifacts(payload)

    assert payload["current_artifacts"] == [existing_artifact]


def test_collect_ct_artifact_errors_reports_blocking_not_implemented_artifacts(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    paths = _blocked_ct_artifacts(tmp_path)
    _patch_ct_artifacts(monkeypatch, paths)

    errors = builder.collect_ct_artifact_errors()

    assert f"{builder.display(paths['result'])} ok must be true" in errors
    assert f"{builder.display(paths['result'])} not_implemented_count must be 0" in errors
    assert f"{builder.display(paths['result'])} blocking_not_implemented_count must be 0" in errors
    assert f"{builder.display(paths['summary'])} ok must be true" in errors
    assert f"{builder.display(paths['summary'])} not_implemented_rows must be 0" in errors
    assert f"{builder.display(paths['summary'])} blocking_not_implemented_rows must be 0" in errors
    assert (
        f"{builder.display(paths['rows'])} has 1 non-pass rows: "
        "ct.synthetic.not_implemented='not_implemented'"
    ) in errors


def test_build_returns_blocked_before_mutating_packet_outputs(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    paths = _blocked_ct_artifacts(tmp_path)
    _patch_ct_artifacts(monkeypatch, paths)
    blocked_report = tmp_path / "BB_E4_FINAL_READINESS_REPORT.md"
    blocked_manifest = tmp_path / "BB_E4_FINAL_ARTIFACT_FRESHNESS_MANIFEST.json"
    monkeypatch.setattr(builder, "P8_REPORT_PATH", blocked_report)
    monkeypatch.setattr(builder, "P8_MANIFEST_PATH", blocked_manifest)
    called: list[str] = []
    refresh_calls: list[list[str]] = []

    def record_refresh(errors: list[str]) -> None:
        refresh_calls.append(errors)

    monkeypatch.setattr(builder, "refresh_blocked_score_artifacts", record_refresh)

    def fail_if_called(name: str) -> MutationFunction:
        def _fail(*_args: Any, **_kwargs: Any) -> Any:
            called.append(name)
            raise AssertionError(f"{name} must not run when CT artifacts are blocked")

        return _fail

    for name in MUTATION_FUNCTIONS:
        monkeypatch.setattr(builder, name, fail_if_called(name))

    report = builder.build()

    assert report["ok"] is False
    assert report["preflight_blocked"] is True
    assert report["blocked_points"] == builder.expected_points()
    assert report["artifact_count"] == 5
    assert report["accepted_support_claims"] == 0
    assert called == []
    assert len(refresh_calls) == 1
    assert f"{builder.display(paths['result'])} ok must be true" in refresh_calls[0]
    assert blocked_report.exists()
    assert blocked_manifest.exists()
    assert "Status: blocked; final readiness is not accepted." in blocked_report.read_text(encoding="utf-8")
    manifest = json.loads(blocked_manifest.read_text(encoding="utf-8"))
    assert manifest["status"] == "blocked"
    assert manifest["current_accepted_points"] == 0
    assert manifest["target_support_claims_accepted"] == 0
    assert manifest["blocked_points"] == builder.expected_points()
    assert f"{builder.display(paths['result'])} ok must be true" in report["errors"]
    assert f"{builder.display(paths['summary'])} blocking_not_implemented_rows must be 0" in report["errors"]


def test_build_regenerates_packet_outputs_after_clean_ct_preflight(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    paths = _passing_ct_artifacts(tmp_path)
    _patch_ct_artifacts(monkeypatch, paths)
    node_gate_path = tmp_path / "node_gate.json"
    _write_json(node_gate_path, {"ok": True, "accepted": True})
    monkeypatch.setattr(
        builder,
        "c4_validator_paths",
        lambda: [(node_gate_path, "synthetic_validator")],
    )
    called: list[str] = []

    def record(name: str, result: Any = None) -> MutationFunction:
        def _record(*_args: Any, **_kwargs: Any) -> Any:
            called.append(name)
            return result

        return _record

    monkeypatch.setattr(builder, "write_notes", record("write_notes"))
    monkeypatch.setattr(
        builder,
        "update_score_subledger",
        record("update_score_subledger", {"score_rows": []}),
    )
    monkeypatch.setattr(
        builder,
        "update_accepted_report",
        record("update_accepted_report", {"accepted_support_claims": [{"claim_id": "synthetic", "points": 1}]}),
    )
    monkeypatch.setattr(builder, "update_baseline", record("update_baseline", {}))
    monkeypatch.setattr(builder, "update_progress", record("update_progress", {}))
    monkeypatch.setattr(builder, "write_primitive_readiness", record("write_primitive_readiness", {}))
    monkeypatch.setattr(
        builder,
        "update_validation_report",
        record("update_validation_report", {"ok": True, "errors": []}),
    )
    monkeypatch.setattr(builder, "write_final_report", record("write_final_report"))
    monkeypatch.setattr(builder, "update_terminal_manifest", record("update_terminal_manifest", {}))
    monkeypatch.setattr(builder, "update_scorecard", record("update_scorecard", {}))
    monkeypatch.setattr(
        builder,
        "write_final_manifest",
        record("write_final_manifest", {"artifacts": [{"role": "synthetic"}]}),
    )

    report = builder.build()

    assert called == list(MUTATION_FUNCTIONS)
    assert report["ok"] is True
    assert report["errors"] == []
    assert report["accepted_support_claims"] == 1
    assert report["artifact_count"] == 1


def test_main_returns_two_for_unexpected_builder_exception(
    monkeypatch: Any,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(builder, "assert_score_authority", lambda: None)
    monkeypatch.setattr(builder, "build", lambda: (_ for _ in ()).throw(RuntimeError("node gate broke")))

    code = builder.main(["--json"])

    captured = capsys.readouterr()
    assert code == 2
    assert "node gate broke" in captured.out
    assert '"preflight_blocked": false' in captured.out