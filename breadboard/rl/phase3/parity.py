from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from breadboard.rl.phase3.evidence import sha256_file

PHASE3_PARITY_SCHEMA = "bb.rl.phase3.parity_report.v1"
PHASE3_PARITY_REPORT_ID = "phase3_parity_report"
PHASE3_PARITY_CLAIM_BOUNDARY = "phase3_ppo_grpo_closed_loop_parity_named_scope"
REQUIRED_PARITY_SECTIONS = ("scorer", "rollout", "token_logprob", "checkpoint", "model_merge", "infra", "dataproto", "limitations", "checklist")


def _sha(path: Path | None) -> str:
    return sha256_file(path) if path and path.exists() and path.is_file() else ""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _artifact_path(evidence_root: Path, report: Mapping[str, Any], key: str) -> Path | None:
    raw = _mapping(report.get("artifact_paths")).get(key)
    if not raw:
        return None
    candidate = Path(str(raw))
    if not candidate.is_absolute():
        candidate = evidence_root / candidate
    try:
        resolved = candidate.resolve()
        resolved.relative_to(evidence_root.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _without_parity_hash(report: Mapping[str, Any]) -> dict[str, Any]:
    hashes = _mapping(report.get("input_hashes"))
    hashes.pop("parity_report", None)
    return hashes


def _checkpoint_item(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "report_id": report.get("report_id"),
        "trainer_backend": report.get("trainer_backend"),
        "entrypoint": report.get("entrypoint"),
        "rollout_name": report.get("rollout_name"),
        "model_ref": report.get("model_ref"),
        "n_gpus_per_node": report.get("n_gpus_per_node"),
        "device_count": report.get("device_count"),
        "optimizer_step_count": report.get("optimizer_step_count"),
        "checkpoint_before_sha256": report.get("checkpoint_before_sha256"),
        "checkpoint_after_sha256": report.get("checkpoint_after_sha256"),
        "checkpoint_changed": report.get("checkpoint_changed"),
    }


def _validate_checkpoint_items(items: Mapping[str, Mapping[str, Any]]) -> list[str]:
    errors: list[str] = []
    for name, item in items.items():
        if int(item.get("optimizer_step_count") or 0) < 1:
            errors.append(f"{name}.optimizer_step_count must be >= 1")
        if item.get("checkpoint_changed") is not True:
            errors.append(f"{name}.checkpoint_changed must be true")
        if item.get("checkpoint_before_sha256") == item.get("checkpoint_after_sha256"):
            errors.append(f"{name}.checkpoint hashes must differ")
        if int(item.get("device_count") or 0) != 8 or int(item.get("n_gpus_per_node") or 0) != 8:
            errors.append(f"{name}.device_count and n_gpus_per_node must be 8")
    return errors

EMPTY_TREE_SHA256 = "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _read_text(path: Path | None) -> str:
    if not path or not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _load_json(path: Path | None) -> dict[str, Any]:
    text = _read_text(path)
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _first_digest(text: str) -> str:
    match = re.search(r"Digest:\s*(sha256:[0-9a-f]{64})", text)
    return match.group(1) if match else ""


def _checkpoint_checklist(evidence_root: Path, closed_loop_report: Mapping[str, Any], checkpoint: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    manifest_path = _artifact_path(evidence_root, closed_loop_report, "evidence_manifest")
    stdout_path = _artifact_path(evidence_root, closed_loop_report, "trainer_stdout")
    manifest = _load_json(manifest_path)
    stdout = _read_text(stdout_path)
    checkpoint_dir = str(manifest.get("checkpoint_dir") or "")
    closed_loop_after = _mapping(checkpoint.get("closed_loop")).get("checkpoint_after_sha256")
    before_hashes = [_mapping(checkpoint.get(name)).get("checkpoint_before_sha256") for name in ("ppo", "grpo", "closed_loop")]
    files = _mapping(manifest.get("files"))
    file_sizes_ok = bool(files) and all(int(_mapping(meta).get("bytes") or 0) > 0 for meta in files.values())
    evidence = {
        "checkpoint_dir": checkpoint_dir,
        "manifest_checkpoint_tree_sha256": manifest.get("checkpoint_tree_sha256"),
        "closed_loop_after_sha256": closed_loop_after,
        "all_before_hashes_empty_tree": all(value == EMPTY_TREE_SHA256 for value in before_hashes),
        "after_hash_matches_manifest_tree": bool(closed_loop_after) and manifest.get("checkpoint_tree_sha256") == closed_loop_after,
        "trainer_default_local_dir_matches_manifest": bool(checkpoint_dir) and f"'default_local_dir': '{checkpoint_dir}'" in stdout,
        "manifest_files_nonzero": file_sizes_ok,
        "source_artifacts": {
            "closed_loop_report": closed_loop_report.get("report_id"),
            "evidence_manifest": str(_mapping(closed_loop_report.get("artifact_paths")).get("evidence_manifest", "")),
            "trainer_stdout": str(_mapping(closed_loop_report.get("artifact_paths")).get("trainer_stdout", "")),
        },
    }
    satisfied = all(
        bool(evidence[key])
        for key in (
            "checkpoint_dir",
            "manifest_checkpoint_tree_sha256",
            "closed_loop_after_sha256",
            "all_before_hashes_empty_tree",
            "after_hash_matches_manifest_tree",
            "trainer_default_local_dir_matches_manifest",
            "manifest_files_nonzero",
        )
    )
    return {"status": "satisfied" if satisfied else "open", "evidence": evidence}


def _infra_checklist(
    evidence_root: Path,
    ppo_report: Mapping[str, Any],
    grpo_report: Mapping[str, Any],
    closed_loop_report: Mapping[str, Any],
    introspection_report: Mapping[str, Any],
    runtime_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    stdout = _read_text(_artifact_path(evidence_root, closed_loop_report, "trainer_stdout"))
    stderr = _read_text(_artifact_path(evidence_root, closed_loop_report, "trainer_stderr"))
    ppo_log = _read_text(_artifact_path(evidence_root, ppo_report, "target_command_log"))
    grpo_log = _read_text(_artifact_path(evidence_root, grpo_report, "target_command_log"))
    torch_info = _mapping(introspection_report.get("torch"))
    symbols = _mapping(introspection_report.get("symbols"))
    trainer_runtime_path = str(runtime_evidence.get("runtime_path") or "")
    runtime_install_path = str(runtime_evidence.get("runtime_install_report_path") or "")
    runtime_install_runtime = str(runtime_evidence.get("runtime_install_runtime") or "")
    introspection_path = str(runtime_evidence.get("introspection_report_path") or "")
    runtime_install_is_scratch = "/scratch_runs/" in runtime_install_path.replace("\\", "/")
    trainer_runtime_root = str(Path(trainer_runtime_path).resolve()) if trainer_runtime_path else ""
    install_runtime_root = str(Path(runtime_install_runtime).resolve()) if runtime_install_runtime else ""
    split_scope = bool(trainer_runtime_root and install_runtime_root and trainer_runtime_root != install_runtime_root)
    evidence = {
        "trainer_runtime_path": trainer_runtime_path,
        "introspection_report_path": introspection_path,
        "split_scope": split_scope,
        "container_image": runtime_evidence.get("container_image"),
        "ppo_image_digest": _first_digest(ppo_log),
        "grpo_image_digest": _first_digest(grpo_log),
        "verl_version": symbols.get("verl.__version__"),
        "torch_version": torch_info.get("version"),
        "device_count": torch_info.get("device_count"),
        "devices": torch_info.get("devices") if isinstance(torch_info.get("devices"), list) else [],
        "ray_started": "Started a local Ray instance" in stderr,
        "tensor_model_parallel_size": "'tensor_model_parallel_size': 2" in stdout,
        "sandbox_memory_limit_mb": "'memory_limit_mb': 1024" in stdout,
        "checkpoint_default_local_dir": "'default_local_dir':" in stdout,
        "transfer_queue_simple_storage": "'storage_backend': 'SimpleStorage'" in stdout,
        "runtime_install_report_path": runtime_install_path,
        "runtime_install_passed": runtime_evidence.get("runtime_install_passed") is True,
        "runtime_install_runtime": runtime_install_runtime,
        "vllm_version": runtime_evidence.get("vllm_version") or "",
        "runtime_install_is_scratch": runtime_install_is_scratch,
    }
    required = ("trainer_runtime_path", "container_image", "ppo_image_digest", "grpo_image_digest", "runtime_install_report_path", "runtime_install_passed", "runtime_install_runtime", "vllm_version")
    missing = [key for key in required if not evidence.get(key)]
    if split_scope:
        missing.append("single_runtime_install_for_trainer_runtime")
    if runtime_install_is_scratch:
        missing.append("target_run_bound_runtime_install")
    reason = "" if not missing else "runtime install evidence must be target-run-bound and prove the same trainer runtime, container image, and vllm.__version__ used by the canonical trainer artifacts"
    return {
        "status": "satisfied" if not missing else "open",
        "missing": missing,
        "evidence": evidence,
        "reason": reason,
    }


def build_phase3_parity_report(
    *,
    target_run_id: str,
    ppo_report: Mapping[str, Any],
    grpo_report: Mapping[str, Any],
    closed_loop_report: Mapping[str, Any],
    introspection_report: Mapping[str, Any],
    runtime_evidence: Mapping[str, Any],
    evidence_root: Path,
) -> dict[str, Any]:
    reward_path = _artifact_path(evidence_root, closed_loop_report, "reward_function")
    projection_path = _artifact_path(evidence_root, closed_loop_report, "accepted_projection_rows")
    evidence_manifest_path = _artifact_path(evidence_root, closed_loop_report, "evidence_manifest")
    metrics_path = _artifact_path(evidence_root, closed_loop_report, "metrics")
    checkpoint = {
        "ppo": _checkpoint_item(ppo_report),
        "grpo": _checkpoint_item(grpo_report),
        "closed_loop": {
            **_checkpoint_item(closed_loop_report),
            "accepted_count": closed_loop_report.get("accepted_count"),
            "quarantined_count": closed_loop_report.get("quarantined_count"),
            "rejected_count": closed_loop_report.get("rejected_count"),
            "dataproto_ok": closed_loop_report.get("dataproto_ok"),
        },
    }
    checkpoint_checklist = _checkpoint_checklist(evidence_root, closed_loop_report, checkpoint)
    errors = _validate_checkpoint_items(checkpoint)
    if closed_loop_report.get("dataproto_ok") is not True:
        errors.append("closed_loop.dataproto_ok must be true")
    if not reward_path or not reward_path.exists():
        errors.append("scorer.reward_function artifact must exist")
    if not projection_path or not projection_path.exists():
        errors.append("rollout.accepted_projection_rows artifact must exist")
    if not evidence_manifest_path or not evidence_manifest_path.exists():
        errors.append("dataproto.evidence_manifest artifact must exist")
    torch_info = _mapping(introspection_report.get("torch"))
    symbols = _mapping(introspection_report.get("symbols"))
    devices = torch_info.get("devices") if isinstance(torch_info.get("devices"), list) else []
    if torch_info.get("device_count") != 8:
        errors.append("infra.introspection device_count must be 8")
    if not devices or any(device != "AMD Instinct MI300X" for device in devices):
        errors.append("infra.introspection devices must be AMD Instinct MI300X")
    if symbols.get("verl.__version__") != "0.8.0":
        errors.append("infra.introspection VeRL version must be 0.8.0")
    if not runtime_evidence.get("container_image") or not runtime_evidence.get("runtime_path"):
        errors.append("infra.runtime evidence must include container_image and runtime_path")
    infra_checklist = _infra_checklist(evidence_root, ppo_report, grpo_report, closed_loop_report, introspection_report, runtime_evidence)
    if checkpoint_checklist.get("status") != "satisfied":
        errors.append("checklist.C7 checkpoint parity evidence must be satisfied")
    if infra_checklist.get("status") != "satisfied":
        errors.append("checklist.C10 infrastructure parity evidence must be satisfied")
    return {
        "schema_version": PHASE3_PARITY_SCHEMA,
        "report_id": PHASE3_PARITY_REPORT_ID,
        "claim_boundary": PHASE3_PARITY_CLAIM_BOUNDARY,
        "target_run_id": target_run_id,
        "scorecard_update_allowed": False,
        "passed": not errors,
        "scorer": {
            "reward_function_sha256": _sha(reward_path),
            "live_provider_parity": "blocked_missing_p3_m8_provider_credentials",
            "scope": "closed_loop_reward_function_only_not_live_provider",
        },
        "rollout": {
            "accepted_projection_rows_sha256": _sha(projection_path),
            "accepted_count": closed_loop_report.get("accepted_count"),
            "quarantined_count": closed_loop_report.get("quarantined_count"),
            "rejected_count": closed_loop_report.get("rejected_count"),
            "rollout_name": closed_loop_report.get("rollout_name"),
        },
        "token_logprob": {"available": False, "reason": "canonical artifacts preserve accepted projection and DataProto evidence but do not expose per-token logprob parity arrays"},
        "checkpoint": checkpoint,
        "checklist": {
            "C7_checkpoint_parity": checkpoint_checklist,
            "C10_infrastructure_parity": infra_checklist,
        },
        "model_merge": {"required": False, "reason": "current exact-scope evidence uses HF Qwen/Qwen2.5-0.5B-Instruct with no Megatron merge artifact"},
        "infra": {
            "runtime_evidence": dict(runtime_evidence),
            "introspection": {
                "scope": "api_introspection_runtime_not_trainer_runtime" if infra_checklist.get("evidence", {}).get("split_scope") else "same_runtime",
                "verl_version": symbols.get("verl.__version__"),
                "torch_version": torch_info.get("version"),
                "cuda_available": torch_info.get("cuda_available"),
                "device_count": torch_info.get("device_count"),
                "devices": devices,
            },
        },
        "dataproto": {"dataproto_ok": closed_loop_report.get("dataproto_ok"), "evidence_manifest_sha256": _sha(evidence_manifest_path), "metrics_sha256": _sha(metrics_path)},
        "limitations": [
            "No live ORS/OpenReward provider parity without P3-M8 credentials.",
            "No native BenchFlow/Harbor parity without P3-M9 endpoint/token.",
            "No HF/Megatron merge parity because current evidence does not use Megatron merge.",
            "Per-token logprob parity arrays are not exposed by canonical artifacts.",
        ],
        "input_hashes": {
            "p3_m2_report": _without_parity_hash(ppo_report),
            "p3_m3_report": _without_parity_hash(grpo_report),
            "p3_m4_report": _without_parity_hash(closed_loop_report),
            "introspection_report": _sha(Path(str(runtime_evidence.get("introspection_report_path", ""))) if runtime_evidence.get("introspection_report_path") else None),
            "runtime_evidence": _mapping(runtime_evidence.get("input_hashes")),
        },
        "artifact_paths": {
            "reward_function": str(_mapping(closed_loop_report.get("artifact_paths")).get("reward_function", "")),
            "accepted_projection_rows": str(_mapping(closed_loop_report.get("artifact_paths")).get("accepted_projection_rows", "")),
            "evidence_manifest": str(_mapping(closed_loop_report.get("artifact_paths")).get("evidence_manifest", "")),
            "metrics": str(_mapping(closed_loop_report.get("artifact_paths")).get("metrics", "")),
            "introspection_report": str(runtime_evidence.get("introspection_report_artifact", "")),
            "runtime_ppo_script": str(runtime_evidence.get("ppo_script_artifact", "")),
            "runtime_grpo_script": str(runtime_evidence.get("grpo_script_artifact", "")),
            "runtime_closed_loop_script": str(runtime_evidence.get("closed_loop_script_artifact", "")),
            "runtime_install_report": str(runtime_evidence.get("runtime_install_report_artifact", "")),
        },
        "errors": errors,
    }


def validate_phase3_parity_report(report: Mapping[str, Any], *, target_run_id: str, evidence_root: Path) -> list[str]:
    errors: list[str] = []
    if report.get("schema_version") != PHASE3_PARITY_SCHEMA:
        errors.append("schema_version must be Phase 3 parity report schema")
    if report.get("report_id") != PHASE3_PARITY_REPORT_ID:
        errors.append("report_id must be Phase 3 parity report id")
    if report.get("claim_boundary") != PHASE3_PARITY_CLAIM_BOUNDARY:
        errors.append("claim_boundary must be Phase 3 parity boundary")
    if report.get("target_run_id") != target_run_id:
        errors.append("target_run_id must match expected target run")
    if report.get("scorecard_update_allowed") is not False:
        errors.append("scorecard_update_allowed must be false")
    if report.get("passed") is not True:
        errors.append("passed must be true")
    for section in REQUIRED_PARITY_SECTIONS:
        if section not in report:
            errors.append(f"{section} section must be present")
    checklist = _mapping(report.get("checklist"))
    c7 = _mapping(checklist.get("C7_checkpoint_parity"))
    c10 = _mapping(checklist.get("C10_infrastructure_parity"))
    if c7.get("status") != "satisfied":
        errors.append("checklist.C7_checkpoint_parity.status must be satisfied")
    if c10.get("status") != "satisfied":
        errors.append("checklist.C10_infrastructure_parity.status must be satisfied")
    checkpoint = report.get("checkpoint") if isinstance(report.get("checkpoint"), Mapping) else {}
    dataproto = _mapping(report.get("dataproto"))
    if dataproto.get("dataproto_ok") is not True:
        errors.append("dataproto.dataproto_ok must be true")
    infra = _mapping(report.get("infra"))
    runtime_evidence = _mapping(infra.get("runtime_evidence"))
    if not runtime_evidence.get("container_image"):
        errors.append("infra.runtime_evidence.container_image must be present")
    if not runtime_evidence.get("runtime_path"):
        errors.append("infra.runtime_evidence.runtime_path must be present")
    introspection = _mapping(infra.get("introspection"))
    if introspection.get("verl_version") != "0.8.0":
        errors.append("infra.introspection.verl_version must be 0.8.0")
    if introspection.get("device_count") != 8:
        errors.append("infra.introspection.device_count must be 8")
    devices = introspection.get("devices") if isinstance(introspection.get("devices"), list) else []
    if len(devices) != 8 or any(device != "AMD Instinct MI300X" for device in devices):
        errors.append("infra.introspection.devices must list 8 AMD Instinct MI300X devices")
    if introspection.get("cuda_available") is not True:
        errors.append("infra.introspection.cuda_available must be true")
    rollout = _mapping(report.get("rollout"))
    if rollout.get("rollout_name") != "vllm":
        errors.append("rollout.rollout_name must be vllm")
    errors.extend(f"checkpoint.{error}" for error in _validate_checkpoint_items({k: _mapping(checkpoint.get(k)) for k in ("ppo", "grpo", "closed_loop")}))
    artifact_paths = report.get("artifact_paths") if isinstance(report.get("artifact_paths"), Mapping) else {}
    root = evidence_root.resolve()
    for key in ("reward_function", "accepted_projection_rows", "evidence_manifest", "metrics", "introspection_report", "runtime_ppo_script", "runtime_grpo_script", "runtime_closed_loop_script", "runtime_install_report"):
        raw = artifact_paths.get(key)
        if not raw:
            errors.append(f"artifact_paths.{key} must be present")
            continue
        candidate = (root / str(raw)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            errors.append(f"artifact_paths.{key} must stay under evidence_root")
            continue
        if not candidate.exists():
            errors.append(f"artifact_paths.{key} must exist")
    if report.get("errors") not in ([], None):
        errors.append("errors must be empty")
    return errors
