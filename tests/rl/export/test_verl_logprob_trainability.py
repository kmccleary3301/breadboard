from __future__ import annotations

import json
from pathlib import Path

from breadboard.rl.export import build_verl_probe_rows_from_m6_summary, validate_verl_probe_row
from breadboard.rl.export.schema import VerlProbeRow


WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
M6_SUMMARY = WORKSPACE_ROOT / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m6_controlled_swe_toy" / "run_summary.json"


def accepted_row() -> VerlProbeRow:
    summary = json.loads(M6_SUMMARY.read_text(encoding="utf-8"))
    return next(row for row in build_verl_probe_rows_from_m6_summary(summary) if row.trainable_candidate)


def test_trainable_candidate_requires_completion_logprobs() -> None:
    payload = accepted_row().to_dict()
    payload.pop("completion_logprobs")

    errors = validate_verl_probe_row(VerlProbeRow.from_dict(payload))
    assert "trainable_candidate requires completion_logprobs" in errors


def test_trainable_candidate_requires_passed_hardening_and_replay() -> None:
    payload = accepted_row().to_dict()
    payload["admission"]["hardening_status"] = "quarantined"

    errors = validate_verl_probe_row(VerlProbeRow.from_dict(payload))
    assert "trainable_candidate requires hardening_status=passed" in errors
