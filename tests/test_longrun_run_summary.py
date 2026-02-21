from __future__ import annotations

from pathlib import Path

from agentic_coder_prototype.conductor_loop import build_longrun_parity_audit


def test_build_longrun_parity_audit_reports_enabled_and_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    (run_dir / "meta" / "checkpoints").mkdir(parents=True)
    (run_dir / "meta" / "longrun_summary.json").write_text("{}", encoding="utf-8")
    (run_dir / "meta" / "longrun_state.json").write_text("{}", encoding="utf-8")
    (run_dir / "meta" / "checkpoints" / "latest_checkpoint.json").write_text("{}", encoding="utf-8")

    audit = build_longrun_parity_audit(
        {"long_running": {"enabled": True, "controller": "longrun_v1"}},
        {"longrun_summary": {"episodes_run": 3}},
        str(run_dir),
    )

    assert audit["schema_version"] == "longrun_parity_audit_v1"
    assert audit["enabled"] is True
    assert audit["episodes_run"] == 3
    assert isinstance(audit["effective_config_hash"], str)
    assert len(audit["effective_config_hash"]) == 64
    assert "meta/longrun_summary.json" in audit["artifact_list"]
    assert "meta/longrun_state.json" in audit["artifact_list"]
    assert "meta/checkpoints/latest_checkpoint.json" in audit["artifact_list"]


def test_build_longrun_parity_audit_reports_disabled_defaults() -> None:
    audit = build_longrun_parity_audit({}, {}, None)
    assert audit["enabled"] is False
    assert audit["episodes_run"] == 0
    assert audit["artifact_list"] == []
    assert isinstance(audit["effective_config_hash"], str)
