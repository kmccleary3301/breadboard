from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from scripts.e4_parity import run_lane
from scripts.e4_parity.stage_contracts import STAGES_BY_KIND, check_stage_report


_VALID_SHA256 = "sha256:" + "a" * 64
_STAGE_REPORT_BASE: dict[str, Any] = {
    "stage": "capture",
    "outcome": "executed_pass",
    "manifest_rule": None,
    "reused_inputs": None,
    "report_ref": "artifacts/stages/capture.json",
    "lock_sha256": None,
    "detail": "",
}


def _report(**overrides: Any) -> dict[str, Any]:
    return {**_STAGE_REPORT_BASE, **overrides}


@pytest.mark.parametrize(
    ("case", "report", "manifest"),
    [
        (
            "executed pass names its report",
            _report(),
            {"kind": "target_support"},
        ),
        (
            "executed failure names both report and failure detail",
            _report(
                outcome="executed_fail",
                report_ref="artifacts/stages/capture-failure.json",
                detail="capture process exited 2",
            ),
            {"kind": "target_support"},
        ),
        (
            "stored replay resolves its author declaration",
            _report(
                stage="replay",
                outcome="reused_stored_result",
                manifest_rule="/replay/mode",
                reused_inputs=[{"path": "artifacts/replay.json", "sha256": _VALID_SHA256}],
                report_ref=None,
            ),
            {"kind": "target_support", "replay": {"mode": "stored"}},
        ),
        (
            "replay-dump capture resolves its author declaration",
            _report(
                outcome="reused_stored_result",
                manifest_rule="/capture/strategy",
                reused_inputs=[{"path": "artifacts/capture.json", "sha256": _VALID_SHA256}],
                report_ref=None,
            ),
            {"kind": "target_support", "capture": {"strategy": "replay_dump"}},
        ),
        (
            "runtime-records capture resolves its author declaration",
            _report(
                outcome="reused_stored_result",
                manifest_rule="/capture/strategy",
                reused_inputs=[{"path": "artifacts/runtime-records.json", "sha256": _VALID_SHA256}],
                report_ref=None,
            ),
            {"kind": "self_runtime", "capture": {"strategy": "runtime_records"}},
        ),
        (
            "boolean false disables a stage through an escaped JSON pointer",
            _report(
                stage="compare",
                outcome="disabled_by_manifest",
                manifest_rule="/stage~1switches/compare~0enabled",
                report_ref=None,
            ),
            {"kind": "target_support", "stage/switches": {"compare~enabled": False}},
        ),
        (
            "off disables a stage",
            _report(
                stage="claim",
                outcome="disabled_by_manifest",
                manifest_rule="/claim/mode",
                report_ref=None,
            ),
            {"kind": "target_support", "claim": {"mode": "off"}},
        ),
        (
            "probe normalize is structurally absent",
            _report(
                stage="normalize",
                outcome="not_applicable",
                manifest_rule="/kind",
                report_ref=None,
            ),
            {"kind": "probe"},
        ),
        (
            "probe replay is structurally absent",
            _report(
                stage="replay",
                outcome="not_applicable",
                manifest_rule="/kind",
                report_ref=None,
            ),
            {"kind": "probe"},
        ),
        (
            "probe compare is structurally absent",
            _report(
                stage="compare",
                outcome="not_applicable",
                manifest_rule="/kind",
                report_ref=None,
            ),
            {"kind": "probe"},
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_check_stage_report_accepts_every_authorized_outcome(
    case: str, report: dict[str, Any], manifest: dict[str, Any]
) -> None:
    assert check_stage_report(report, manifest) == [], case


@pytest.mark.parametrize(
    ("case", "report", "manifest", "error_fragment"),
    [
        (
            "executed pass without report provenance",
            _report(report_ref=None),
            {"kind": "target_support"},
            "report_ref",
        ),
        (
            "executed failure without report provenance",
            _report(outcome="executed_fail", report_ref=None, detail="failed"),
            {"kind": "target_support"},
            "report_ref",
        ),
        (
            "executed failure without a useful detail",
            _report(outcome="executed_fail", detail="  "),
            {"kind": "target_support"},
            "detail",
        ),
        (
            "reuse pointer does not resolve",
            _report(
                stage="replay",
                outcome="reused_stored_result",
                manifest_rule="/replay/mode",
                reused_inputs=[{"path": "artifacts/replay.json", "sha256": _VALID_SHA256}],
                report_ref=None,
            ),
            {"kind": "target_support", "replay": {}},
            "manifest_rule",
        ),
        (
            "stored value at an unauthorized pointer",
            _report(
                stage="replay",
                outcome="reused_stored_result",
                manifest_rule="/metadata/mode",
                reused_inputs=[{"path": "artifacts/replay.json", "sha256": _VALID_SHA256}],
                report_ref=None,
            ),
            {"kind": "target_support", "metadata": {"mode": "stored"}},
            "manifest_rule",
        ),
        (
            "capture reuse uses an unauthorized strategy",
            _report(
                outcome="reused_stored_result",
                manifest_rule="/capture/strategy",
                reused_inputs=[{"path": "artifacts/capture.json", "sha256": _VALID_SHA256}],
                report_ref=None,
            ),
            {"kind": "target_support", "capture": {"strategy": "adapter"}},
            "manifest_rule",
        ),
        (
            "normalize may never reuse a stored result",
            _report(
                stage="normalize",
                outcome="reused_stored_result",
                manifest_rule="/replay/mode",
                reused_inputs=[{"path": "artifacts/normalized.json", "sha256": _VALID_SHA256}],
                report_ref=None,
            ),
            {"kind": "target_support", "replay": {"mode": "stored"}},
            "normalize",
        ),
        (
            "stored reuse has no input provenance",
            _report(
                stage="replay",
                outcome="reused_stored_result",
                manifest_rule="/replay/mode",
                reused_inputs=[],
                report_ref=None,
            ),
            {"kind": "target_support", "replay": {"mode": "stored"}},
            "reused_inputs",
        ),
        (
            "disable pointer does not resolve",
            _report(
                stage="compare",
                outcome="disabled_by_manifest",
                manifest_rule="/compare/enabled",
                report_ref=None,
            ),
            {"kind": "target_support", "compare": {}},
            "manifest_rule",
        ),
        (
            "true does not disable",
            _report(
                stage="compare",
                outcome="disabled_by_manifest",
                manifest_rule="/compare/enabled",
                report_ref=None,
            ),
            {"kind": "target_support", "compare": {"enabled": True}},
            "false",
        ),
        (
            "numeric zero is not JSON false",
            _report(
                stage="compare",
                outcome="disabled_by_manifest",
                manifest_rule="/compare/enabled",
                report_ref=None,
            ),
            {"kind": "target_support", "compare": {"enabled": 0}},
            "false",
        ),
        (
            "not-applicable must point to kind",
            _report(
                stage="replay",
                outcome="not_applicable",
                manifest_rule="/replay/mode",
                report_ref=None,
            ),
            {"kind": "probe", "replay": {"mode": "off"}},
            "/kind",
        ),
        (
            "probe capture is applicable",
            _report(
                outcome="not_applicable",
                manifest_rule="/kind",
                report_ref=None,
            ),
            {"kind": "probe"},
            "capture",
        ),
        (
            "unknown lane kind cannot authorize absence",
            _report(
                stage="replay",
                outcome="not_applicable",
                manifest_rule="/kind",
                report_ref=None,
            ),
            {"kind": "future_kind"},
            "future_kind",
        ),
        (
            "unknown outcome is dishonest",
            _report(outcome="skipped", report_ref=None),
            {"kind": "target_support"},
            "skipped",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_check_stage_report_rejects_dishonest_outcomes(
    case: str,
    report: dict[str, Any],
    manifest: dict[str, Any],
    error_fragment: str,
) -> None:
    errors = check_stage_report(report, manifest)

    assert errors, case
    assert error_fragment in " ".join(errors), (case, errors)


def test_non_target_accounting_requires_all_five_stages() -> None:
    expected_stages = ("capture", "normalize", "replay", "compare", "claim")

    assert STAGES_BY_KIND["non_target_accounting"] == expected_stages
    for stage_name in expected_stages:
        report = _report(
            stage=stage_name,
            outcome="not_applicable",
            manifest_rule="/kind",
            report_ref=None,
        )
        errors = check_stage_report(report, {"kind": "non_target_accounting"})

        assert any(
            error == f"{stage_name} is applicable to lane kind 'non_target_accounting'"
            for error in errors
        ), (stage_name, errors)


@pytest.mark.parametrize(
    ("digest", "case"),
    [
        ("a" * 64, "missing sha256 prefix"),
        ("sha256:" + "A" * 64, "uppercase hexadecimal"),
        ("sha256:" + "a" * 63, "short digest"),
        ("sha256:" + "a" * 65, "long digest"),
        ("sha256:" + "g" * 64, "non-hexadecimal digest"),
    ],
    ids=lambda value: value if isinstance(value, str) and " " in value else None,
)
def test_reused_input_requires_exact_lowercase_prefixed_sha256(digest: str, case: str) -> None:
    report = _report(
        stage="replay",
        outcome="reused_stored_result",
        manifest_rule="/replay/mode",
        reused_inputs=[{"path": "artifacts/replay.json", "sha256": digest}],
        report_ref=None,
    )

    errors = check_stage_report(report, {"kind": "target_support", "replay": {"mode": "stored"}})

    assert errors, case
    assert "sha256" in " ".join(errors), (case, errors)


def _legacy_normalized_lane(*, replay: dict[str, Any], normalize: dict[str, Any] | None = None) -> dict[str, Any]:
    """Match load_lane_defs' legacy normalized-dict boundary, rather than a source YAML shape."""
    return {
        "schema_version": "bb.e4.lane_def.v1",
        "_lane_def_version": 1,
        "lane_id": "honest_stage_fixture",
        "config_id": "honest_stage_fixture",
        "kind": "target_support",
        "status": "claimed",
        "capture": {"strategy": "adapter", "argv": None, "inputs": []},
        "normalize": normalize or {"translator": "identity", "config": {}},
        "replay": replay,
        "compare": {"comparator": "fixture", "config": {}},
        "claim": {"scope": {"behaviors": ["fixture"], "surfaces": ["fixture"]}, "exclusions": []},
        "artifacts_root": "artifacts/fixture",
        "reverify_command": None,
        "run": None,
        "provenance": None,
        "acceptance": {"behavior_family": None, "semantic_key": None, "target": None, "assertions": []},
    }


def _patch_lane_loading(
    monkeypatch: pytest.MonkeyPatch, lane_def: dict[str, Any]
) -> None:
    monkeypatch.setattr(run_lane, "load_lane_defs", lambda _directory: {lane_def["lane_id"]: lane_def})
    monkeypatch.setattr(run_lane, "_inventory_lane", lambda _lane_id, _inventory_path: None)


def test_run_lane_reports_declared_stored_replay_with_input_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay_path = tmp_path / "artifacts" / "stored-replay.json"
    replay_path.parent.mkdir(parents=True)
    replay_bytes = b'{"events":[{"kind":"tool_result"}]}\n'
    replay_path.write_bytes(replay_bytes)
    lane_def = _legacy_normalized_lane(
        replay={
            "mode": "stored",
            "session": "artifacts/stored-replay.json",
            "comparator_class": "semantic",
        }
    )
    _patch_lane_loading(monkeypatch, lane_def)
    monkeypatch.setattr(run_lane, "ROOT", tmp_path)

    result = run_lane.run_lane(
        lane_def["lane_id"],
        stage="replay",
        out_dir=tmp_path / "scratch",
        lane_def_dir=tmp_path / "unused-lane-defs",
        inventory_path=tmp_path / "unused-inventory.json",
    )

    expected_digest = "sha256:" + hashlib.sha256(replay_bytes).hexdigest()
    assert result["ok"] is True
    assert len(result["stages"]) == 1
    stage = result["stages"][0]
    assert _STAGE_REPORT_BASE.keys() <= stage.keys()
    assert stage["stage"] == "replay"
    assert stage["lane_id"] == lane_def["lane_id"]
    assert stage["returncode"] == 0
    assert stage["outcome"] == "reused_stored_result"
    assert stage["manifest_rule"] == "/replay/mode"
    assert stage["reused_inputs"] == [
        {"path": "artifacts/stored-replay.json", "sha256": expected_digest}
    ]
    assert stage["report_ref"] is None
    assert stage["lock_sha256"] is None
    assert stage["honesty_errors"] == []


def test_run_lane_fails_an_undeclared_metadata_skip_as_executed_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lane_def = _legacy_normalized_lane(
        replay={"mode": "stored", "session": "unused.json", "comparator_class": "semantic"},
        normalize={"translator": "identity", "config": {}},
    )
    _patch_lane_loading(monkeypatch, lane_def)
    monkeypatch.setattr(run_lane, "ROOT", tmp_path)

    result = run_lane.run_lane(
        lane_def["lane_id"],
        stage="normalize",
        out_dir=tmp_path / "scratch",
        lane_def_dir=tmp_path / "unused-lane-defs",
        inventory_path=tmp_path / "unused-inventory.json",
    )

    assert result["ok"] is False
    assert len(result["stages"]) == 1
    stage = result["stages"][0]
    assert _STAGE_REPORT_BASE.keys() <= stage.keys()
    assert stage["stage"] == "normalize"
    assert stage["returncode"] != 0
    assert stage["outcome"] == "executed_fail"
    assert "normalize.mode" in stage["detail"]
    assert stage["honesty_errors"] == []
    assert isinstance(stage["report_ref"], str) and stage["report_ref"]
    report_path = tmp_path / stage["report_ref"]
    assert report_path.is_file()
