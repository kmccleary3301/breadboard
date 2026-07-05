from __future__ import annotations

import json
from pathlib import Path

import pytest

from breadboard.rl.export import build_verl_probe_rows_from_m6_summary
from breadboard.rl.export.schema import VerlProbeRow


WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
M6_SUMMARY = WORKSPACE_ROOT / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m6_controlled_swe_toy" / "run_summary.json"


def load_rows():
    summary = json.loads(M6_SUMMARY.read_text(encoding="utf-8"))
    return build_verl_probe_rows_from_m6_summary(summary)


def test_verl_probe_rows_include_required_identity_and_policy_fields() -> None:
    rows = load_rows()

    assert rows
    for row in rows:
        assert row.rollout_id
        assert row.trajectory_id
        assert row.episode_id
        assert row.task_id
        assert row.env_package_hash.startswith("sha256:")
        assert row.policy["policy_id"]


def test_missing_required_field_fails_from_dict() -> None:
    payload = load_rows()[0].to_dict()
    payload.pop("task_id")

    with pytest.raises(ValueError, match="task_id"):
        VerlProbeRow.from_dict(payload)
