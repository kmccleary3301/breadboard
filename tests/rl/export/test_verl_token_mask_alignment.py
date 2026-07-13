from __future__ import annotations

import json
from pathlib import Path

from breadboard.rl.export import build_verl_probe_rows_from_m6_summary, validate_verl_probe_row
from breadboard.rl.export.schema import VerlProbeRow


WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
M6_SUMMARY = WORKSPACE_ROOT / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m6_controlled_swe_toy" / "run_summary.json"


def first_row() -> VerlProbeRow:
    summary = json.loads(M6_SUMMARY.read_text(encoding="utf-8"))
    return build_verl_probe_rows_from_m6_summary(summary)[0]


def test_valid_verl_probe_row_token_masks_align() -> None:
    assert validate_verl_probe_row(first_row()) == []


def test_verl_probe_row_rejects_loss_mask_mismatch() -> None:
    payload = first_row().to_dict()
    payload["loss_mask"] = [True]

    errors = validate_verl_probe_row(VerlProbeRow.from_dict(payload))
    assert "loss_mask length must equal input_ids length" in errors


def test_verl_probe_row_rejects_input_ids_mismatch() -> None:
    payload = first_row().to_dict()
    payload["input_ids"] = [1, 2, 3, 999]

    errors = validate_verl_probe_row(VerlProbeRow.from_dict(payload))
    assert "input_ids must equal prompt_ids + completion_ids" in errors
