from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.rl.export import (  # noqa: E402
    build_verl_probe_rows_from_m6_summary,
    smoke_consume_verl_probe_jsonl,
    smoke_consume_verl_probe_parquet,
    write_verl_probe_jsonl,
    write_verl_probe_parquet,
    write_verl_probe_projection_manifest,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export M6 controlled SWE run to VeRL-shaped JSONL/Parquet probe rows.")
    parser.add_argument(
        "--m6-summary",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m6_controlled_swe_toy/run_summary.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m7_verl_probe"),
    )
    args = parser.parse_args()

    summary = json.loads(args.m6_summary.read_text(encoding="utf-8"))
    rows = build_verl_probe_rows_from_m6_summary(summary)
    jsonl_path = args.output_dir / "verl_probe_rows.jsonl"
    parquet_path = args.output_dir / "verl_probe_rows.parquet"
    projection_manifest_path = args.output_dir / "projection_manifest.json"
    write_verl_probe_jsonl(rows, jsonl_path)
    write_verl_probe_parquet(rows, parquet_path)
    projection_manifest = write_verl_probe_projection_manifest(
        rows,
        projection_manifest_path,
        target_formats=["jsonl", "parquet"],
    )
    jsonl_smoke = smoke_consume_verl_probe_jsonl(jsonl_path)
    parquet_smoke = smoke_consume_verl_probe_parquet(parquet_path)
    smoke = {
        "target_run_id": summary.get("target_run_id") or os.environ.get("M12_TARGET_RUN_ID"),
        "row_count": jsonl_smoke["row_count"],
        "trainable_candidate_count": jsonl_smoke["trainable_candidate_count"],
        "tensorizable": jsonl_smoke["tensorizable"] and parquet_smoke["tensorizable"],
        "errors": {
            "jsonl": jsonl_smoke["errors"],
            "parquet": parquet_smoke["errors"],
        },
        "formats": {
            "jsonl": jsonl_smoke,
            "parquet": parquet_smoke,
        },
        "projection_manifest_id": projection_manifest["projection_id"],
        "compatibility_target": "VeRL JSONL/Parquet probe v1alpha; not DataProto or trainer execution",
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "smoke_consumer_report.json").write_text(
        json.dumps(smoke, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"rows={smoke['row_count']} trainable_candidates={smoke['trainable_candidate_count']} "
        f"tensorizable={smoke['tensorizable']}"
    )


if __name__ == "__main__":
    main()
