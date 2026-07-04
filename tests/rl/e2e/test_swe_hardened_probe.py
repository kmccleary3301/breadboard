from __future__ import annotations

from pathlib import Path

from breadboard.rl.e2e import run_controlled_swe_probe


REPO_ROOT = Path(__file__).resolve().parents[3]
SWE_TOY = REPO_ROOT / "examples" / "rl_env_packages" / "swe_toy_patch" / "env_package.yaml"


def test_accepted_rows_have_hardening_replay_and_projection_evidence(tmp_path) -> None:
    run = run_controlled_swe_probe(
        package_path=SWE_TOY,
        output_dir=tmp_path,
        run_id="test_m6",
        limit=10,
    )
    accepted = [row for row in run.rows if row.row_status == "accepted"]

    assert accepted
    for row in accepted:
        assert row.hardening_status == "passed"
        assert row.replay_status == "passed"
        assert row.exportable_debug is True
        assert row.projection_id
        assert (tmp_path / "row_evidence" / f"{row.task_id}.json").exists()
