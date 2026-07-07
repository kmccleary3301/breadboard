from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any


def run(command: list[str], *, timeout: int = 30) -> dict[str, Any]:
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
        return {
            "argv": command,
            "returncode": result.returncode,
            "stdout": result.stdout[-12000:],
            "stderr": result.stderr[-12000:],
        }
    except Exception as exc:  # noqa: BLE001 - discovery records availability failures.
        return {"argv": command, "returncode": 999, "error": exc.__class__.__name__, "message": str(exc)}


def which_many(names: list[str]) -> dict[str, str | None]:
    return {name: shutil.which(name) for name in names}


def main() -> int:
    target_run_id = os.environ.get("PHASE3_TARGET_RUN_ID", "")
    commands = {
        "python3_version": run(["python3", "--version"]),
        "python3_executable": run(["python3", "-c", "import sys,json; print(json.dumps({'executable':sys.executable,'version':sys.version,'prefix':sys.prefix}))"]),
        "pip_version": run(["python3", "-m", "pip", "--version"]),
        "pip_index_torch": run(["python3", "-m", "pip", "index", "versions", "torch"], timeout=60),
        "rocm_smi": run(["rocm-smi"], timeout=60),
        "rocminfo": run(["rocminfo"], timeout=60),
        "module_avail": run(["bash", "-lc", "module avail"], timeout=60),
        "shared_write": run(["bash", "-lc", "mkdir -p /shared/bb-p3-probe && touch /shared/bb-p3-probe/write-test && python3 - <<'PY'\nfrom pathlib import Path\np=Path('/shared/bb-p3-probe/write-test')\nprint(p.exists(), p.stat().st_size)\nPY"]),
        "network_pypi": run(["python3", "-c", "import urllib.request; r=urllib.request.urlopen('https://pypi.org/simple/torch/', timeout=10); print(r.status)"], timeout=20),
    }
    binaries = which_many(["python3", "pip3", "rocm-smi", "rocminfo", "module", "srun", "sbatch", "apptainer", "singularity", "docker", "podman", "conda", "micromamba", "curl", "wget", "git"])
    devices = []
    rocm_output = commands["rocm_smi"].get("stdout", "") + commands["rocm_smi"].get("stderr", "")
    for line in rocm_output.splitlines():
        if "MI300" in line or "GPU" in line:
            devices.append(line.strip())
    report = {
        "schema_version": "bb.rl.phase3.target_environment_discovery.v1",
        "report_id": "phase3_target_environment_discovery",
        "component": "target_environment_discovery",
        "claim_boundary": "phase3_target_environment_discovery_only_not_readiness",
        "target_run_id": target_run_id,
        "hostname": socket.gethostname(),
        "cwd": str(Path.cwd()),
        "binaries": binaries,
        "commands": commands,
        "mi300x_evidence_lines": devices,
        "shared_workspace_writable": commands["shared_write"].get("returncode") == 0,
        "pypi_reachable": commands["network_pypi"].get("returncode") == 0,
        "container_tools": {name: binaries.get(name) for name in ("apptainer", "singularity", "docker", "podman")},
        "scorecard_update_allowed": False,
        "passed": True,
    }
    print("PHASE3_COMPONENT_REPORT_JSON=" + json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
