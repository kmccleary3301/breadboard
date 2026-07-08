from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SlurmRunSpec:
    ssh_alias: str
    partition: str
    job_name: str
    command_id: str
    payload_zip: Path
    output_dir: Path
    nodes: int = 1
    ntasks: int = 1
    gres: str = "gpu:8"
    time_limit: str = "02:00:00"


def parse_sbatch_job_id(output: str) -> str:
    for token in output.replace("\n", " ").split():
        job_id = token.split(";", 1)[0]
        if job_id.isdigit():
            return job_id
    raise ValueError("sbatch output did not contain job id")


def submit_slurm_run(spec: SlurmRunSpec) -> dict[str, Any]:
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    if not spec.payload_zip.exists():
        raise FileNotFoundError(spec.payload_zip)
    command = [
        "ssh",
        spec.ssh_alias,
        "sbatch",
        "--parsable",
        f"--partition={spec.partition}",
        f"--job-name={spec.job_name}",
        f"--nodes={spec.nodes}",
        f"--ntasks={spec.ntasks}",
        f"--gres={spec.gres}",
        f"--time={spec.time_limit}",
    ]
    result = subprocess.run(command, check=False, text=True, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)
    job_id = parse_sbatch_job_id(result.stdout)
    payload = {"job_id": job_id, "command_id": spec.command_id, "argv": command, "scheduler": "slurm"}
    (spec.output_dir / f"{spec.command_id}.submission.json").write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return payload


def collect_slurm_run(job_id: str, *, output_dir: Path) -> dict[str, Any]:
    log_path = output_dir / f"slurm-{job_id}.out"
    return {"job_id": job_id, "raw_log_path": str(log_path), "completed": log_path.exists()}
