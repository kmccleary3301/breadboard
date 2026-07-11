"""Reproduce the regen DAG's candidate-mode stage group, retaining the tree.

Continue-on-failure variant: runs every candidate stage regardless of earlier
failures so ALL stale-pin frontiers surface in one pass. Diagnostic tool only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/Users/kylemccleary/projects/breadboard/breadboard_repo_integration_main_20260326")
sys.path.insert(0, str(ROOT))

from scripts.e4_parity import regenerate_evidence as regen  # noqa: E402

KEEP = Path("/Users/kylemccleary/projects/breadboard/.e4-candidate-repro-keep")
PIN_RE = re.compile(
    r"\[PIN_STALE\] artifact_hash_mismatch label=(\S+?),path=(\S+) "
    r"expected='(sha256:[0-9a-f]+)' got='(sha256:[0-9a-f]+)'"
)


def main() -> int:
    if KEEP.exists():
        shutil.rmtree(KEEP)
    KEEP.mkdir()
    candidate_root = KEEP / "repo"
    candidate_stages, _, _ = regen._canonical_transaction_groups(regen.STAGES)
    regen._prepare_candidate_root(candidate_root, candidate_stages)
    stages_out = []
    pins = []
    for stage in candidate_stages:
        argv = stage.expanded_argv(sys.executable)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(candidate_root)
        env["BB_WORKSPACE_ROOT"] = str(candidate_root.parent)
        t0 = time.perf_counter()
        cp = subprocess.run(argv, cwd=candidate_root, text=True, capture_output=True, env=env)
        dt = round(time.perf_counter() - t0, 1)
        blob = (cp.stdout or "") + (cp.stderr or "")
        stage_pins = sorted(set(PIN_RE.findall(blob)))
        pins.extend((stage.stage_id, *hit) for hit in stage_pins)
        stages_out.append({"stage_id": stage.stage_id, "returncode": cp.returncode, "duration": dt})
        status = "ok" if cp.returncode == 0 else f"EXIT {cp.returncode}, {len(stage_pins)} stale pins"
        print(f"== {stage.stage_id}: {status} ({dt}s)", flush=True)
        if cp.returncode != 0 and not stage_pins:
            print("   TAIL:", blob[-400:].replace("\n", " | "), flush=True)
    report = {
        "stages": stages_out,
        "stale_pins": [
            {"stage": s, "label": l, "path": p, "expected": e, "got": g}
            for (s, l, p, e, g) in pins
        ],
        "candidate_root": str(candidate_root),
    }
    out_path = Path("/Users/kylemccleary/projects/breadboard/docs_tmp/phase_20/evidence/M1/candidate_stale_pin_census.json")
    out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out_path} | stale pin rows: {len(pins)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
