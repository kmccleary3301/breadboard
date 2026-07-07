from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ssh-alias", required=True)
    parser.add_argument("--partition", required=True)
    parser.add_argument("--require-8-mi300x", action="store_true")
    parser.add_argument("--trainer-backends", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "bb.rl.phase3.target_validation.v1",
        "report_id": "phase3_target_validation_blocked",
        "target_run_id": "",
        "ssh_alias": args.ssh_alias,
        "partition": args.partition,
        "trainer_backends": args.trainer_backends.split(","),
        "blocked_reason": "target_validation_requires_live_slurm_submission",
        "scorecard_update_allowed": False,
        "passed": False,
    }
    (args.output_dir / "phase3_target_validation_blocked.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
