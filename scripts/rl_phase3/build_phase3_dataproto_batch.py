from __future__ import annotations

import argparse
import json
import torch
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


from breadboard.rl.phase2.bridge import build_verl_batch_from_projection_rows
from breadboard.rl.phase3.trainer_live import PHASE3_DATAPROTO_SCHEMA, build_phase3_dataproto


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projection-rows", required=True, type=Path)
    parser.add_argument("--target-run-id", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--grpo", action="store_true")
    args = parser.parse_args()
    rows = json.loads(args.projection_rows.read_text()).get("rows", [])
    batch = build_verl_batch_from_projection_rows(rows, target_run_id=args.target_run_id).to_dict()
    dataproto = build_phase3_dataproto(batch, device="cpu", require_grpo_uid=args.grpo)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    payload_path = args.output_dir / "phase3_dataproto_payload.pt"
    torch.save(dataproto, payload_path)
    report = {"schema_version": PHASE3_DATAPROTO_SCHEMA, "report_id": "phase3_dataproto_batch", "target_run_id": args.target_run_id, "payload_path": str(payload_path), "row_count": len(rows), "scorecard_update_allowed": False, "passed": True}
    (args.output_dir / "phase3_dataproto_report.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
