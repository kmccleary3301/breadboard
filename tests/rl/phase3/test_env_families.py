from __future__ import annotations

from breadboard.rl.phase3.env_families import run_lean_console_env_probe

TARGET = "20260623T000000Z-slurm-234555"


def test_lean_runtime_absence_fails_claim(tmp_path) -> None:
    env = tmp_path / "env.tar"; env.write_text("env")
    report = run_lean_console_env_probe(env, target_run_id=TARGET, output_dir=tmp_path / "out")
    if report["passed"] is False:
        assert report["blocked_reason"] in {"lean_runtime_unavailable", "env_package_missing"}
    assert report["scorecard_update_allowed"] is False
