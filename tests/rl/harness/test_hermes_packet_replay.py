from __future__ import annotations

from pathlib import Path

from scripts.e4_hermes_native_replay import CASES, do2_job_spec


PACKET = Path("/packet/hermes-supplier-capture-packet-rerun3.tar.gz")


def test_do2_spec_pins_the_public_hermes_replay_inputs_and_outputs() -> None:
    spec = do2_job_spec(PACKET, Path("/output/hermes"), Path("/tmp/wheelhouse"))
    assert spec["profile"] == "hermes"
    assert spec["packet"]["sha256"].startswith("sha256:870fc991ddf7")
    assert spec["source"]["commit"].startswith("4f565c1f")
    assert spec["source"]["native_source_commit"] == "939e45c91d751fadd94dcd1b873ac3cb44846213"
    assert spec["resources"] == {"cpus": 16, "memory_gb": 64, "time_minutes": 240}
    assert len(spec["puts"]) >= 10
    assert "apptainer exec --containall --cleanenv --net --network none" in spec["sbatch_script"]
    assert "/opt/breadboard-public/venv/bin/python -I -B" in spec["sbatch_script"]
    assert set(spec["expected_outputs"]["cases"]) == set(CASES)
    assert spec["expected_outputs"]["H-06"] == "exactly 8 requests and native_stop_reason=tool_calls"
