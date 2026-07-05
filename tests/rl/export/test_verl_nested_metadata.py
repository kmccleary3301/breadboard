from __future__ import annotations

import json
from pathlib import Path

from breadboard.rl.export import build_verl_probe_rows_from_m6_summary, validate_verl_probe_row
from breadboard.rl.export.schema import VerlProbeRow


WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
M6_SUMMARY = WORKSPACE_ROOT / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m6_controlled_swe_toy" / "run_summary.json"


def _rows():
    summary = json.loads(M6_SUMMARY.read_text(encoding="utf-8"))
    return build_verl_probe_rows_from_m6_summary(summary)


def test_verl_probe_rows_include_required_nested_metadata() -> None:
    for row in _rows():
        assert validate_verl_probe_row(row) == []
        assert row.policy["policy_staleness"]["staleness_status"] == "not_stale_offline_probe"
        assert "stop_ids" in row.renderer
        assert row.reward["verifier_hash"].startswith("sha256:")
        assert row.runtime["state_refs"]
        assert row.runtime["artifact_refs"]
        assert row.admission["eligible_exports"]


def test_verl_probe_row_requires_policy_staleness() -> None:
    payload = _rows()[0].to_dict()
    payload["policy"].pop("policy_staleness")

    errors = validate_verl_probe_row(VerlProbeRow.from_dict(payload))
    assert "policy.policy_staleness must be present" in errors


def test_verl_probe_row_requires_renderer_stop_ids() -> None:
    payload = _rows()[0].to_dict()
    payload["renderer"].pop("stop_ids")

    errors = validate_verl_probe_row(VerlProbeRow.from_dict(payload))
    assert "renderer.stop_ids must be present" in errors


def test_verl_probe_row_requires_explicit_logprob_status_for_non_trainable_rows() -> None:
    payload = next(row for row in _rows() if not row.trainable_candidate).to_dict()
    payload.pop("completion_logprob_status")

    errors = validate_verl_probe_row(VerlProbeRow.from_dict(payload))
    assert "missing completion_logprobs requires explicit unavailable status" in errors


def test_verl_probe_row_requires_runtime_artifact_refs() -> None:
    payload = _rows()[0].to_dict()
    payload["runtime"].pop("artifact_refs")

    errors = validate_verl_probe_row(VerlProbeRow.from_dict(payload))
    assert "runtime.artifact_refs must be present" in errors
