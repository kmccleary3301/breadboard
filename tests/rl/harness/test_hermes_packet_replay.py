from __future__ import annotations

import hashlib
import os
import json
import subprocess
import sys
from pathlib import Path

import scripts.e4_hermes_native_replay as replay
from scripts.e4_hermes_native_replay import do2_job_spec


SEALED_CASES = Path(
    "/Users/kylemccleary/projects/breadboard/docs_tmp/bb_direction_assessment/"
    "engine_pr_handoff_20260827/e4_admission_20260914T221653Z/"
    "do2-20260923/hermes/kit/hermes_capture_cases.json"
)
PACKET = SEALED_CASES.parent.parent / "packet" / "hermes-supplier-capture-packet-rerun3.tar.gz"


def test_do2_spec_pins_the_public_hermes_replay_inputs_and_outputs(
    tmp_path: Path, monkeypatch,
) -> None:
    kit = tmp_path / "kit"
    operators = kit / "do2-20260923" / "hermes" / "kit"
    operators.mkdir(parents=True)
    (kit / "hermes_sif_compose.py").touch()
    for name in ("hermes_capture_breadboard.py", "hermes_capture_probe.py", "hermes_capture_receiver.py", "hermes_capture_cases.json"):
        (operators / name).touch()
    (operators / "hermes_capture_cases.json").write_bytes(SEALED_CASES.read_bytes())
    head = subprocess.run(["git", "-C", str(Path(replay.__file__).resolve().parents[1]), "rev-parse", "HEAD"],
                          check=True, capture_output=True, text=True).stdout.strip()
    monkeypatch.setattr(replay, "_checkout_commit", lambda: head)
    bundle = tmp_path / "head.bundle"
    spec = do2_job_spec(PACKET, Path("/output/hermes"), Path("/tmp/wheelhouse"), kit_root=kit, bundle=bundle)
    assert spec["profile"] == "hermes"
    assert spec["packet"]["sha256"].startswith("sha256:870fc991ddf7")
    assert spec["source"]["native_source_commit"] == "939e45c91d751fadd94dcd1b873ac3cb44846213"
    assert spec["resources"] == {"cpus": 16, "memory_gb": 64, "time_minutes": 240}
    assert any(entry["local"] == str(operators / "hermes_capture_breadboard.py") for entry in spec["puts"])
    assert spec["source"]["bundle_sha256"] == "sha256:" + hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert any(entry["local"] == str(bundle) for entry in spec["puts"])
    assert "apptainer exec --containall --cleanenv --net --network none" in spec["sbatch_script"]
    assert 'SLURM_JOB_ID="$SLURM_JOB_ID"' in spec["sbatch_script"]
    assert "/opt/breadboard-public/venv/bin/python -I -B" in spec["sbatch_script"]
    assert set(spec["expected_outputs"]["cases"]) == set(json.loads(SEALED_CASES.read_bytes())["cases"])
    assert spec["expected_outputs"]["H-06"] == "exactly 8 requests and native_stop_reason=tool_calls"
    composer_sha = hashlib.sha256((kit / "hermes_sif_compose.py").read_bytes()).hexdigest()
    assert spec["sif"]["sidecar"].endswith(f"hermes-public-{head[:12]}-{composer_sha[:12]}.sif.json")
    verifier = spec["sbatch_script"].split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    verifier = verifier.replace("/root/bbe4-do2-20260923/hermes/images", str(image_dir))
    composer = operators.parent.parent.parent / "hermes_sif_compose.py"
    operator_dir = tmp_path / "operators"
    operator_dir.mkdir()
    (operator_dir / "hermes_sif_compose.py").write_bytes(composer.read_bytes())
    composer_sha = hashlib.sha256(composer.read_bytes()).hexdigest()
    image = image_dir / f"hermes-public-{head[:12]}-{composer_sha[:12]}.sif"
    image.write_bytes(b"sealed image")
    sidecar = tmp_path / "public.sif.json"
    record = {
        "schema_version": "bb.e4.hermes-public-sif.v1",
        "head_commit": head,
        "supplier_sha256": replay.SUPPLIER_SIF_SHA256,
        "composer_sha256": composer_sha,
        "sif_path": str(image),
        "sif_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
    }
    def check_sidecar() -> subprocess.CompletedProcess[str]:
        sidecar.write_text(json.dumps(record))
        return subprocess.run(
            [sys.executable, "-c", verifier, str(sidecar), head, replay.SUPPLIER_SIF_SHA256],
            text=True,
            capture_output=True,
            env={**os.environ, "REPLAY": str(tmp_path)},
        )
    assert check_sidecar().returncode == 0
    image.write_bytes(b"tampered image")
    tampered_image = check_sidecar()
    assert tampered_image.returncode != 0
    assert "public SIF bytes differ from sidecar" in tampered_image.stderr
    image.write_bytes(b"sealed image")
    record["head_commit"] = "0" * len(head)
    tampered_sidecar = check_sidecar()
    assert tampered_sidecar.returncode != 0
    assert "public SIF provenance differs" in tampered_sidecar.stderr
    assert 'git -C /bb rev-parse HEAD' in spec["sbatch_script"]
    assert "hermes-public.sif" not in spec["sbatch_script"]


