from __future__ import annotations

from pathlib import Path

from breadboard.rl.e2e import run_controlled_swe_probe


REPO_ROOT = Path(__file__).resolve().parents[3]
SWE_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "swe_toy_patch" / "env_package.yaml"


def test_quarantined_and_rejected_rows_do_not_become_trainable_or_debug_exportable() -> None:
    run = run_controlled_swe_probe(package_path=SWE_TOY, run_id="test_m6", limit=10)

    for row in run.rows:
        assert row.trainable is False
        if row.row_status in {"quarantined", "rejected"}:
            assert row.exportable_debug is False
            assert row.blocked_reasons or row.findings
