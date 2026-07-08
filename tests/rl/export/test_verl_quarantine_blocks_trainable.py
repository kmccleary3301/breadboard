from __future__ import annotations

import json
from pathlib import Path

from breadboard.rl.export import build_verl_probe_rows_from_m6_summary


WORKSPACE_ROOT = Path(__file__).resolve().parents[4]
M6_SUMMARY = WORKSPACE_ROOT / "docs_tmp" / "ZYPHRA" / "RL_PHASE_1" / "runs" / "m6_controlled_swe_toy" / "run_summary.json"


def test_quarantined_and_rejected_m6_rows_are_not_trainable_candidates() -> None:
    summary = json.loads(M6_SUMMARY.read_text(encoding="utf-8"))
    rows = build_verl_probe_rows_from_m6_summary(summary)

    for row in rows:
        if row.admission["row_status"] in {"quarantined", "rejected"}:
            assert row.trainable_candidate is False
            assert row.completion_logprobs is None
