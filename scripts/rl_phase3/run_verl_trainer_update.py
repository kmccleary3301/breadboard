from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[2] if len(Path(__file__).resolve().parents) > 2 else Path.cwd()
sys.path.insert(0, str(_repo_root))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real Phase 3 VeRL smoke trainer update on the target runtime.")
    parser.add_argument("--backend", required=True, choices=["verl_ppo", "verl_grpo"])
    parser.add_argument("--target-run-id", required=True)
    parser.add_argument("--introspection-report", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model-ref", default=os.environ.get("PHASE3_TRAINER_MODEL_PATH", "Qwen/Qwen2.5-0.5B-Instruct"))
    args = parser.parse_args()
    report_text = args.introspection_report.read_text()
    if '"passed": true' not in report_text and '"passed":true' not in report_text:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / f"{args.backend}_trainer_update_blocked.json").write_text(
            '{"schema_version":"bb.rl.phase3.verl_trainer_update.v1","blocked_reason":"introspection_not_passed","scorecard_update_allowed":false,"passed":false}\n'
        )
        return 2
    os.environ["PHASE3_TRAINER_BACKEND"] = args.backend
    os.environ["PHASE3_TARGET_RUN_ID"] = args.target_run_id
    os.environ["PHASE3_TRAINER_MODEL_PATH"] = args.model_ref
    try:
        from scripts.rl_phase3.target_verl_smoke_train import main as smoke_main
    except ModuleNotFoundError:
        from target_verl_smoke_train import main as smoke_main

    return smoke_main()


if __name__ == "__main__":
    raise SystemExit(main())
