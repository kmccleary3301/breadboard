from __future__ import annotations

from pathlib import Path

import scripts.e4_hermes_native_replay as replay
from scripts.e4_hermes_native_replay import CASES, do2_job_spec


PACKET = Path("/packet/hermes-supplier-capture-packet-rerun3.tar.gz")


def test_do2_spec_pins_the_public_hermes_replay_inputs_and_outputs(
    tmp_path: Path, monkeypatch,
) -> None:
    kit = tmp_path / "kit"
    operators = kit / "do2-20260923" / "hermes" / "kit"
    operators.mkdir(parents=True)
    (kit / "hermes_sif_compose.py").touch()
    for name in ("hermes_capture_breadboard.py", "hermes_capture_probe.py", "hermes_capture_receiver.py", "hermes_capture_cases.json"):
        (operators / name).touch()
    monkeypatch.setattr(replay, "_checkout_commit", lambda: "a" * 40)
    spec = do2_job_spec(PACKET, Path("/output/hermes"), Path("/tmp/wheelhouse"), kit_root=kit)
    assert spec["profile"] == "hermes"
    assert spec["packet"]["sha256"].startswith("sha256:870fc991ddf7")
    assert spec["source"]["native_source_commit"] == "939e45c91d751fadd94dcd1b873ac3cb44846213"
    assert spec["resources"] == {"cpus": 16, "memory_gb": 64, "time_minutes": 240}
    assert any(entry["local"] == str(operators / "hermes_capture_breadboard.py") for entry in spec["puts"])
    assert any(entry["local"] == str(Path(replay.__file__).resolve().parents[1] / "breadboard") for entry in spec["puts"])
    assert "apptainer exec --containall --cleanenv --net --network none" in spec["sbatch_script"]
    assert 'SLURM_JOB_ID="$SLURM_JOB_ID"' in spec["sbatch_script"]
    assert "/opt/breadboard-public/venv/bin/python -I -B" in spec["sbatch_script"]
    assert set(spec["expected_outputs"]["cases"]) == set(CASES)
    assert spec["expected_outputs"]["H-06"] == "exactly 8 requests and native_stop_reason=tool_calls"
