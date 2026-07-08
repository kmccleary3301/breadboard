from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from breadboard.rl.phase2.benchmark import BenchmarkSourcePin, build_benchmark_slice_report, source_sha256
from breadboard.rl.phase3.evidence import validate_phase3_command_log_manifest

PHASE3_BENCHMARK_SCHEMA = "bb.rl.phase3.benchmark_campaign.v1"
PHASE3_BENCHMARK_CLAIM_BOUNDARY = "phase3_named_benchmark_campaign_scope"


@dataclass(frozen=True)
class BenchmarkCampaignSpec:
    benchmark_id: str
    benchmark_version: str
    source_uri: str
    expected_source_sha256: str
    split_id: str
    max_tasks: int
    contamination_manifest_path: Path
    output_dir: Path
    fixture_scope: bool = False


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())



def _reject_non_model_candidate(contamination: Mapping[str, Any]) -> str | None:
    prompt_scan = contamination.get("prompt_solution_leakage_scan")
    if not isinstance(prompt_scan, Mapping):
        return None
    candidate_source = str(prompt_scan.get("candidate_source") or "").lower()
    non_model_markers = ("hand_written", "hand-written", "target_payload", "probe_only", "synthetic_baseline")
    if any(marker in candidate_source for marker in non_model_markers):
        return "benchmark_candidate_source_not_phase3_model_pipeline"
    return None


def _safe_replay_path(replay_dir: Path, task_id: str) -> Path | None:
    if not task_id or task_id in {".", ".."} or "\\" in task_id:
        return None
    candidate = (replay_dir / f"{task_id}.json").resolve()
    try:
        candidate.relative_to(replay_dir.resolve())
    except ValueError:
        return None
    return candidate


def _logical_replay_id(path: Path, payload: Mapping[str, Any], replay_dir: Path) -> str:
    task_id = payload.get("task_id")
    if isinstance(task_id, str) and task_id:
        return task_id
    relative = path.relative_to(replay_dir)
    stem = str(relative.with_suffix(""))
    if "/" not in stem and "_" in stem:
        prefix, suffix = stem.rsplit("_", 1)
        if suffix.isdigit():
            return f"{prefix}/{suffix}"
    return stem


def _scan_replay_duplicates(replay_dir: Path) -> list[str]:
    errors: list[str] = []
    if not replay_dir.is_dir():
        return errors
    seen: dict[str, str] = {}
    for path in sorted(replay_dir.rglob("*.json")):
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        payload = payload if isinstance(payload, Mapping) else {}
        logical_id = _logical_replay_id(path, payload, replay_dir)
        rel = str(path.relative_to(replay_dir))
        if logical_id in seen:
            errors.append(f"duplicate replay artifact for {logical_id}:{seen[logical_id]}:{rel}")
        else:
            seen[logical_id] = rel
    return errors


def build_benchmark_campaign_report(
    spec: BenchmarkCampaignSpec, *, run_summary_path: Path, replay_dir: Path, command_log_manifest: Mapping[str, Any]
) -> dict[str, Any]:
    summary = _load_json(run_summary_path)
    contamination = _load_json(spec.contamination_manifest_path)
    controls = set(contamination.get("controls", []))
    errors: list[str] = []
    for control in ("source_hash_pin", "train_overlap_manifest", "prompt_solution_leakage_scan"):
        if control not in controls:
            errors.append(f"missing contamination control {control}")
    candidate_error = _reject_non_model_candidate(contamination)
    if candidate_error:
        errors.append(candidate_error)
    metrics = summary.get("metrics", {})
    acceptance_error = None
    if not spec.fixture_scope and int(metrics.get("accepted", 0)) <= 0:
        acceptance_error = "benchmark_no_accepted_tasks"
        errors.append(acceptance_error)
    failed = [str(task_id) for task_id in list(summary.get("failed_tasks", [])) + list(summary.get("quarantined_tasks", []))]
    seen_failed: set[str] = set()
    if failed and not replay_dir.is_dir():
        errors.append("benchmark_replay_dir_missing")
    errors.extend(_scan_replay_duplicates(replay_dir))
    replay_refs: list[str] = []
    for task_id in failed:
        if not task_id:
            errors.append("benchmark_replay_task_id_empty")
            continue
        replay_path = _safe_replay_path(replay_dir, task_id)
        if replay_path is None:
            errors.append(f"unsafe replay task_id:{task_id}")
            continue
        if task_id in seen_failed:
            errors.append(f"benchmark_replay_task_id_duplicate:{task_id}")
        seen_failed.add(task_id)
        replay_refs.append(str(replay_path))
        if not replay_path.is_file():
            errors.append(f"missing replay artifact for {task_id}")
            continue
        try:
            replay_payload = json.loads(replay_path.read_text())
        except json.JSONDecodeError:
            errors.append(f"malformed replay artifact for {task_id}")
            continue
        if isinstance(replay_payload, Mapping) and replay_payload.get("task_id") not in (None, task_id):
            errors.append(f"replay artifact task_id mismatch for {task_id}")
    pin = BenchmarkSourcePin(spec.benchmark_id, spec.benchmark_version, spec.split_id, spec.source_uri, spec.expected_source_sha256)
    slice_report = build_benchmark_slice_report(
        pin,
        observed_source_sha256=source_sha256(summary.get("source_payload", "")),
        contamination_controls=sorted(controls),
        failure_replay_refs=replay_refs,
        metrics=metrics,
        target_run_id=str(command_log_manifest.get("target_run_id") or summary.get("target_run_id") or ""),
    ).to_dict()
    if not spec.fixture_scope:
        slice_errors = [error for error in slice_report.get("errors", []) if error != "missing_failure_replay"]
        if candidate_error and candidate_error not in slice_errors:
            slice_errors.append(candidate_error)
        if acceptance_error and acceptance_error not in slice_errors:
            slice_errors.append(acceptance_error)
        slice_report["errors"] = slice_errors
        slice_report["report_id"] = "bb_zyphra_rl_phase3_benchmark_slice_v1"
        slice_report["claim_boundary"] = PHASE3_BENCHMARK_CLAIM_BOUNDARY
        if not slice_errors:
            slice_report["status"] = "external_benchmark_package_accepted"
            slice_report["passed"] = True
            slice_report["accepted_for_claim"] = True
        else:
            slice_report["status"] = "rejected_external_benchmark_controls"
            slice_report["passed"] = False
            slice_report["accepted_for_claim"] = False
        metadata = slice_report.get("metadata")
        metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
        metadata.pop("fixture_scope", None)
        metadata["campaign_scope"] = "external_named_benchmark_package"
        metadata["benchmark_input_kind"] = "external_jsonl"
        slice_report["metadata"] = metadata
    for error in slice_report.get("errors", []):
        if error not in errors:
            errors.append(error)
    target_run_id = slice_report.get("target_run_id") or str(command_log_manifest.get("target_run_id") or "")
    evidence_root = spec.output_dir.parents[3] if len(spec.output_dir.parents) > 3 else spec.output_dir
    manifest_errors = validate_phase3_command_log_manifest(command_log_manifest, target_run_id=target_run_id, repo_root=Path.cwd(), evidence_root=evidence_root)
    errors.extend(manifest_errors)
    metrics = metrics
    report = {
        "schema_version": PHASE3_BENCHMARK_SCHEMA,
        "report_id": "phase3_benchmark_campaign",
        "claim_boundary": PHASE3_BENCHMARK_CLAIM_BOUNDARY,
        "target_run_id": target_run_id,
        "source_pin": pin.to_dict(),
        "train_overlap_manifest": contamination.get("train_overlap_manifest"),
        "prompt_solution_leakage_scan": contamination.get("prompt_solution_leakage_scan"),
        "attempted": int(metrics.get("attempted", summary.get("attempted", 0))),
        "accepted": int(metrics.get("accepted", summary.get("accepted", 0))),
        "rejected": int(metrics.get("rejected", summary.get("rejected", 0))),
        "quarantined": int(metrics.get("quarantined", summary.get("quarantined", 0))),
        "pass_at_1": float(metrics.get("pass_at_1", 0.0)),
        "mean_reward": float(metrics.get("mean_reward", 0.0)),
        "p50_latency_seconds": float(metrics.get("p50_latency_seconds", 0.0)),
        "p95_latency_seconds": float(metrics.get("p95_latency_seconds", 0.0)),
        "cost_ledger_ref": summary.get("cost_ledger_ref", ""),
        "slice_report": slice_report,
        "errors": errors,
        "input_hashes": {"run_summary": run_summary_path.name, "contamination": spec.contamination_manifest_path.name},
        "artifact_paths": {"run_summary": str(run_summary_path)},
        "scorecard_update_allowed": False,
        "passed": not errors,
    }
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    (spec.output_dir / "phase3_benchmark_campaign_report.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    return report
