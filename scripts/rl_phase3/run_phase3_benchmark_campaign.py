from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.rl.phase2.benchmark import build_fixture_benchmark_source_pin
from breadboard.rl.phase3.benchmark_campaign import BenchmarkCampaignSpec, build_benchmark_campaign_report
from breadboard.rl.phase3.evidence import sha256_file, write_phase3_json


def _sha256_text(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode()).hexdigest()



def _blocked_report(*, target_run_id: str, output_dir: Path, blocked_reason: str, extra: dict | None = None) -> dict:
    blocker_path = output_dir / "benchmark_blocker_evidence.json"
    blocker_payload = {"target_run_id": target_run_id, "blocked_reason": blocked_reason}
    if extra:
        blocker_payload["extra"] = extra
    write_phase3_json(blocker_path, blocker_payload)
    report = {
        "schema_version": "bb.rl.phase3.component_report.v1",
        "report_id": "p3-m7_benchmark_campaign",
        "milestone_id": "P3-M7",
        "component": "benchmark_campaign",
        "claim_boundary": "phase3_benchmark_campaign_blocked_scope",
        "target_run_id": target_run_id,
        "points": 80,
        "passed": False,
        "blocked_reason": blocked_reason,
        "input_hashes": {"blocker_evidence": sha256_file(blocker_path)},
        "artifact_paths": {"blocker_evidence": str(blocker_path)},
        "required_artifact_keys": ["blocker_evidence"],
        "scorecard_update_allowed": False,
    }
    if extra:
        report.update(extra)
    write_phase3_json(output_dir / "p3-m7_benchmark_campaign.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase-dir", required=True, type=Path)
    parser.add_argument("--target-run-id", required=True)
    parser.add_argument("--command-manifest", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    phase_dir = args.phase_dir
    output_dir = args.output_dir or phase_dir / "runs" / "benchmark_campaign"
    output_dir.mkdir(parents=True, exist_ok=True)
    benchmark_jsonl = os.environ.get("PHASE3_BENCHMARK_JSONL", "")
    fixture_pin = None
    if not benchmark_jsonl:
        fixture_pin = build_fixture_benchmark_source_pin()
        fixture_dir = output_dir / "locked_fixture_inputs"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        source_path = fixture_dir / "slice-001.jsonl"
        source_payload = "swe-rebench-v2.fixture.slice.001\n"
        source_path.write_text(source_payload)
        run_summary_path = fixture_dir / "run_summary.json"
        contamination_path = fixture_dir / "contamination.json"
        replay_dir = fixture_dir / "replays"
        if replay_dir.exists():
            shutil.rmtree(replay_dir)
        replay_dir.mkdir()
        (replay_dir / "fixture-rejected-task.json").write_text(json.dumps({"task_id": "fixture-rejected-task", "reason": "locked_fixture_replay"}))
        write_phase3_json(run_summary_path, {
            "source_payload": source_payload,
            "target_run_id": args.target_run_id,
            "failed_tasks": ["fixture-rejected-task"],
            "quarantined_tasks": [],
            "metrics": {
                "attempted": 1,
                "accepted": 0,
                "rejected": 1,
                "quarantined": 0,
                "pass_at_1": 0.0,
                "mean_reward": 0.0,
                "p50_latency_seconds": 0.0,
                "p95_latency_seconds": 0.0,
            },
        })
        write_phase3_json(contamination_path, {
            "controls": ["source_hash_pin", "train_overlap_manifest", "prompt_solution_leakage_scan"],
            "train_overlap_manifest": "locked_fixture_no_training_overlap",
            "prompt_solution_leakage_scan": "locked_fixture_no_prompt_solution_leakage",
        })
    else:
        source_path = Path(benchmark_jsonl)
        if not source_path.is_file():
            report = _blocked_report(target_run_id=args.target_run_id, output_dir=output_dir, blocked_reason="PHASE3_BENCHMARK_JSONL file missing", extra={"source_path": str(source_path)})
            print(json.dumps({"report": str(output_dir / "p3-m7_benchmark_campaign.json"), "passed": False, "blocked_reason": report["blocked_reason"]}, sort_keys=True))
            return 2
        run_summary_env = os.environ.get("PHASE3_BENCHMARK_RUN_SUMMARY_JSON", "")
        if not run_summary_env:
            source_copy = output_dir / "benchmark_source.jsonl"
            shutil.copyfile(source_path, source_copy)
            report = _blocked_report(
                target_run_id=args.target_run_id,
                output_dir=output_dir,
                blocked_reason="PHASE3_BENCHMARK_RUN_SUMMARY_JSON absent; refusing to fabricate benchmark outcomes",
                extra={
                    "source_path": str(source_path),
                    "source_sha256": sha256_file(source_copy),
                    "artifact_paths": {"benchmark_source": str(source_copy)},
                    "required_artifact_keys": ["benchmark_source"],
                    "input_hashes": {"benchmark_source": sha256_file(source_copy)},
                },
            )
            print(json.dumps({"report": str(output_dir / "p3-m7_benchmark_campaign.json"), "passed": False, "blocked_reason": report["blocked_reason"]}, sort_keys=True))
            return 2
        run_summary_path = Path(run_summary_env)
        contamination_path = Path(os.environ.get("PHASE3_BENCHMARK_CONTAMINATION_JSON", ""))
        replay_dir = Path(os.environ.get("PHASE3_BENCHMARK_REPLAY_DIR", ""))
        missing = [
            name
            for name, path, predicate in (
                ("PHASE3_BENCHMARK_RUN_SUMMARY_JSON", run_summary_path, Path.is_file),
                ("PHASE3_BENCHMARK_CONTAMINATION_JSON", contamination_path, Path.is_file),
                ("PHASE3_BENCHMARK_REPLAY_DIR", replay_dir, Path.is_dir),
            )
            if not predicate(path)
        ]
        if missing:
            report = _blocked_report(target_run_id=args.target_run_id, output_dir=output_dir, blocked_reason="missing benchmark evidence inputs: " + ",".join(missing))
            print(json.dumps({"report": str(output_dir / "p3-m7_benchmark_campaign.json"), "passed": False, "blocked_reason": report["blocked_reason"]}, sort_keys=True))
            return 2
    source_copy = output_dir / "benchmark_source.jsonl"
    summary_copy = output_dir / "benchmark_run_summary.json"
    contamination_copy = output_dir / "benchmark_contamination.json"
    replay_copy = output_dir / "replays"
    shutil.copyfile(source_path, source_copy)
    shutil.copyfile(run_summary_path, summary_copy)
    shutil.copyfile(contamination_path, contamination_copy)
    if replay_copy.exists():
        shutil.rmtree(replay_copy)
    shutil.copytree(replay_dir, replay_copy)
    replay_manifest = output_dir / "replay_manifest.json"
    replay_entries = []
    for path in sorted(replay_copy.rglob("*")):
        if path.is_file():
            replay_entries.append({"path": str(path.relative_to(replay_copy)), "sha256": sha256_file(path), "bytes": path.stat().st_size})
    write_phase3_json(replay_manifest, {"replay_dir": str(replay_copy), "files": replay_entries})
    manifest_path = args.command_manifest or phase_dir / "runs" / "phase3_command_log_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    source_payload = source_copy.read_text()
    expected_hash = hashlib.sha256(source_payload.encode()).hexdigest()
    spec = BenchmarkCampaignSpec(
        benchmark_id=fixture_pin.benchmark_id if fixture_pin else os.environ.get("PHASE3_BENCHMARK_ID", source_path.stem),
        benchmark_version=fixture_pin.benchmark_version if fixture_pin else os.environ.get("PHASE3_BENCHMARK_VERSION", "external-jsonl"),
        source_uri=fixture_pin.source_uri if fixture_pin else os.environ.get("PHASE3_BENCHMARK_SOURCE_URI", str(source_copy)),
        expected_source_sha256=fixture_pin.expected_source_sha256 if fixture_pin else os.environ.get("PHASE3_BENCHMARK_SOURCE_SHA256", expected_hash),
        split_id=fixture_pin.slice_id if fixture_pin else os.environ.get("PHASE3_BENCHMARK_SPLIT", "validation"),
        max_tasks=int(os.environ.get("PHASE3_BENCHMARK_MAX_TASKS", "1" if fixture_pin else "0") or "0"),
        contamination_manifest_path=contamination_copy,
        output_dir=output_dir,
        fixture_scope=fixture_pin is not None,
    )
    report = build_benchmark_campaign_report(spec, run_summary_path=summary_copy, replay_dir=replay_copy, command_log_manifest=manifest)
    write_phase3_json(output_dir / "p3-m7_benchmark_campaign.json", {
        "schema_version": "bb.rl.phase3.component_report.v1",
        "report_id": "p3-m7_benchmark_campaign",
        "milestone_id": "P3-M7",
        "component": "benchmark_campaign",
        "claim_boundary": "phase3_named_benchmark_campaign_scope",
        "target_run_id": args.target_run_id,
        "points": 80,
        "passed": report.get("passed") is True,
        "blocked_reason": ";".join(report.get("errors", [])),
        "benchmark_report": report,
        "input_hashes": {
            "benchmark_report": sha256_file(output_dir / "phase3_benchmark_campaign_report.json"),
            "benchmark_source": sha256_file(source_copy),
            "run_summary": sha256_file(summary_copy),
            "contamination": sha256_file(contamination_copy),
            "replay_manifest": sha256_file(replay_manifest),
        },
        "artifact_paths": {
            "benchmark_report": str(output_dir / "phase3_benchmark_campaign_report.json"),
            "benchmark_source": str(source_copy),
            "run_summary": str(summary_copy),
            "contamination": str(contamination_copy),
            "replay_manifest": str(replay_manifest),
        },
        "required_artifact_keys": ["benchmark_report", "benchmark_source", "run_summary", "contamination", "replay_manifest"],
        "scorecard_update_allowed": False,
    })
    print(json.dumps({"report": str(output_dir / "p3-m7_benchmark_campaign.json"), "passed": report.get("passed") is True, "errors": report.get("errors", [])}, sort_keys=True))
    return 0 if report.get("passed") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
