from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from breadboard.rl.export.schema import VERL_PROBE_SCHEMA, VerlProbeRow


TOKEN_ALIGNED_FIELDS = [
    "attention_mask",
    "loss_mask",
    "assistant_mask",
    "tool_action_mask",
    "reward_mask",
]

VERL_PROJECTION_MANIFEST_SCHEMA = "bb.verl_probe_projection_manifest.v1alpha"

VERL_PRESERVED_FIELDS = [
    "rollout_id",
    "trajectory_id",
    "episode_id",
    "task_id",
    "split_id",
    "env_package_id",
    "env_package_hash",
    "group_id",
    "policy",
    "prompt_ids",
    "completion_ids",
    "input_ids",
    "attention_mask",
    "loss_mask",
    "assistant_mask",
    "tool_action_mask",
    "reward_mask",
    "completion_logprob_status",
    "completion_logprobs",
    "renderer",
    "reward",
    "runtime",
    "admission",
    "projection_manifest_id",
    "trainable_candidate",
]

VERL_LOST_FIELDS = [
    "full_workspace_bytes",
    "full_runtime_process_tree",
    "raw_filesystem_snapshot_bytes",
    "trainer_dataproto_object",
]

REQUIRED_POLICY_FIELDS = [
    "policy_id",
    "policy_version",
    "checkpoint_ref",
    "model_requested",
    "model_served",
    "provider",
    "engine",
    "sampling_config",
    "policy_staleness",
]

REQUIRED_RENDERER_FIELDS = [
    "renderer_id",
    "renderer_version",
    "renderer_config_hash",
    "tokenizer_hash",
    "chat_template_hash",
    "stop_ids",
    "fidelity_class",
]

REQUIRED_REWARD_FIELDS = [
    "scalar",
    "reward_vector",
    "verifier_id",
    "verifier_version",
    "verifier_hash",
    "evidence_ref",
]

REQUIRED_RUNTIME_FIELDS = [
    "runtime_backend",
    "runtime_signature",
    "image_digest",
    "state_refs",
    "artifact_refs",
    "package_hash",
    "metrics_ms",
]

REQUIRED_ADMISSION_FIELDS = [
    "row_status",
    "hardening_status",
    "replay_status",
    "quarantine_status",
    "trainable",
    "eligible_exports",
    "exportable_debug",
    "blocked_reasons",
]


def _require_mapping_fields(mapping: Mapping[str, Any], mapping_name: str, fields: list[str]) -> list[str]:
    errors: list[str] = []
    for field_name in fields:
        if field_name not in mapping:
            errors.append(f"{mapping_name}.{field_name} must be present")
            continue
        value = mapping[field_name]
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(f"{mapping_name}.{field_name} must be non-empty")
    return errors


def validate_verl_probe_row(row: VerlProbeRow) -> list[str]:
    errors: list[str] = []
    if row.schema_version != VERL_PROBE_SCHEMA:
        errors.append(f"schema_version must be {VERL_PROBE_SCHEMA!r}")
    if row.input_ids != [*row.prompt_ids, *row.completion_ids]:
        errors.append("input_ids must equal prompt_ids + completion_ids")
    if not row.completion_ids:
        errors.append("completion_ids must be non-empty")
    for field_name in TOKEN_ALIGNED_FIELDS:
        if len(getattr(row, field_name)) != len(row.input_ids):
            errors.append(f"{field_name} length must equal input_ids length")
    if row.completion_logprobs is not None and len(row.completion_logprobs) != len(row.completion_ids):
        errors.append("completion_logprobs length must equal completion_ids length")
    if row.completion_logprobs is None and row.completion_logprob_status not in {"unavailable_non_trainable", "posthoc_unavailable"}:
        errors.append("missing completion_logprobs requires explicit unavailable status")
    if row.completion_logprobs is not None and row.completion_logprob_status not in {"native_available", "posthoc_available"}:
        errors.append("completion_logprobs require available logprob status")
    if row.trainable_candidate and row.completion_logprobs is None:
        errors.append("trainable_candidate requires completion_logprobs")
    if row.trainable_candidate and row.completion_logprob_status != "native_available":
        errors.append("trainable_candidate requires native_available completion_logprob_status")
    if row.trainable_candidate and row.admission.get("row_status") != "accepted":
        errors.append("trainable_candidate requires accepted row_status")
    if row.trainable_candidate and row.admission.get("hardening_status") != "passed":
        errors.append("trainable_candidate requires hardening_status=passed")
    if row.trainable_candidate and row.admission.get("replay_status") != "passed":
        errors.append("trainable_candidate requires replay_status=passed")
    if row.claim_boundary != "verl_shaped_probe_not_trainer_ready":
        errors.append("claim_boundary must remain verl_shaped_probe_not_trainer_ready")
    for field_name in ["policy", "renderer", "reward", "runtime", "admission"]:
        if not getattr(row, field_name):
            errors.append(f"{field_name} must be non-empty")
    errors.extend(_require_mapping_fields(row.policy, "policy", REQUIRED_POLICY_FIELDS))
    errors.extend(_require_mapping_fields(row.renderer, "renderer", REQUIRED_RENDERER_FIELDS))
    errors.extend(_require_mapping_fields(row.reward, "reward", REQUIRED_REWARD_FIELDS))
    errors.extend(_require_mapping_fields(row.runtime, "runtime", REQUIRED_RUNTIME_FIELDS))
    errors.extend(_require_mapping_fields(row.admission, "admission", REQUIRED_ADMISSION_FIELDS))
    if row.policy.get("policy_staleness") and not isinstance(row.policy.get("policy_staleness"), Mapping):
        errors.append("policy.policy_staleness must be a mapping")
    if row.renderer.get("stop_ids") is not None and not isinstance(row.renderer.get("stop_ids"), list):
        errors.append("renderer.stop_ids must be a list")
    if row.runtime.get("state_refs") is not None and not isinstance(row.runtime.get("state_refs"), list):
        errors.append("runtime.state_refs must be a list")
    if row.runtime.get("artifact_refs") is not None and not isinstance(row.runtime.get("artifact_refs"), list):
        errors.append("runtime.artifact_refs must be a list")
    if row.admission.get("eligible_exports") is not None and not isinstance(row.admission.get("eligible_exports"), list):
        errors.append("admission.eligible_exports must be a list")
    return errors


def build_verl_probe_rows_from_m6_summary(summary: Mapping[str, Any]) -> list[VerlProbeRow]:
    rows: list[VerlProbeRow] = []
    for index, source_row in enumerate(summary.get("rows") or [], start=1):
        row_status = source_row["row_status"]
        prompt_ids = [100, index]
        completion_ids = [200 + index, 300 + index]
        input_ids = [*prompt_ids, *completion_ids]
        accepted = row_status == "accepted"
        rows.append(
            VerlProbeRow(
                rollout_id=summary["run_id"],
                trajectory_id=f"{summary['run_id']}.{source_row['task_id']}.trajectory",
                episode_id=f"{summary['run_id']}.{source_row['task_id']}.episode",
                task_id=source_row["task_id"],
                split_id="train_probe",
                env_package_id=summary["package_id"],
                env_package_hash=summary["package_hash"],
                group_id=f"{summary['run_id']}.group.controlled_swe_toy",
                policy={
                    "policy_id": "m7_probe_policy",
                    "policy_version": "v1alpha",
                    "checkpoint_ref": "none_probe",
                    "model_requested": "synthetic_token_probe",
                    "model_served": "synthetic_token_probe",
                    "provider": "local_probe",
                    "engine": "synthetic_token_probe",
                    "sampling_config": {"temperature": 1.0},
                    "policy_staleness": {
                        "policy_age_steps": 0,
                        "actor_version_matches_rollout": True,
                        "staleness_status": "not_stale_offline_probe",
                    },
                },
                prompt_ids=prompt_ids,
                completion_ids=completion_ids,
                input_ids=input_ids,
                attention_mask=[1] * len(input_ids),
                loss_mask=[False, False, True, True],
                assistant_mask=[False, False, True, True],
                tool_action_mask=[False, False, True, False],
                reward_mask=[False, False, False, True],
                completion_logprobs=[-0.1, -0.2] if accepted else None,
                completion_logprob_status="native_available" if accepted else "unavailable_non_trainable",
                renderer={
                    "renderer_id": "m7_synthetic_renderer",
                    "renderer_version": "v1alpha",
                    "renderer_config_hash": "sha256:m7-renderer",
                    "tokenizer_hash": "sha256:m7-tokenizer",
                    "chat_template_hash": "sha256:m7-chat-template",
                    "stop_ids": [],
                    "fidelity_class": "F3" if accepted else "F1",
                },
                reward={
                    "scalar": source_row["reward"],
                    "reward_vector": {"unit_test_reward": source_row["reward"]},
                    "verifier_id": "swe_toy_patch_pytest",
                    "verifier_version": "v1alpha",
                    "verifier_hash": "sha256:swe-toy-patch-pytest",
                    "evidence_ref": f"row_evidence/{source_row['task_id']}.json",
                },
                runtime={
                    "runtime_backend": "controlled_swe_toy_probe",
                    "runtime_signature": "controlled_swe_toy_probe.v1alpha",
                    "image_digest": "local_process_no_container_probe",
                    "state_refs": [f"state://{summary['run_id']}/{source_row['task_id']}"],
                    "artifact_refs": [f"row_evidence/{source_row['task_id']}.json"],
                    "package_hash": summary["package_hash"],
                    "metrics_ms": dict(source_row["metrics_ms"]),
                },
                admission={
                    "row_status": row_status,
                    "hardening_status": source_row["hardening_status"],
                    "replay_status": source_row["replay_status"],
                    "quarantine_status": "quarantined" if row_status == "quarantined" else "clear",
                    "trainable": accepted,
                    "eligible_exports": ["debug_jsonl", "verl_jsonl", "verl_parquet"] if accepted else ["debug_jsonl"],
                    "exportable_debug": source_row["exportable_debug"],
                    "blocked_reasons": list(source_row.get("blocked_reasons") or []),
                },
                projection_manifest_id=source_row["projection_id"],
                trainable_candidate=accepted,
                metadata={"source_claim": summary.get("source_claim")},
            )
        )
    return rows


def write_verl_probe_jsonl(rows: Iterable[VerlProbeRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row.to_dict(), sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def build_verl_probe_projection_manifest(rows: Iterable[VerlProbeRow], target_formats: Iterable[str]) -> dict[str, Any]:
    materialized_rows = list(rows)
    rollout_id = materialized_rows[0].rollout_id if materialized_rows else "empty"
    return {
        "schema_version": VERL_PROJECTION_MANIFEST_SCHEMA,
        "projection_id": f"{rollout_id}.projection.verl_probe_v1alpha",
        "claim_boundary": "verl_shaped_probe_not_trainer_ready",
        "canonical_truth": "breadboard_graph_replay_runtime",
        "target_formats": list(target_formats),
        "row_count": len(materialized_rows),
        "trainable_candidate_count": sum(row.trainable_candidate for row in materialized_rows),
        "source_projection_manifest_ids": sorted({row.projection_manifest_id for row in materialized_rows}),
        "preserved_fields": list(VERL_PRESERVED_FIELDS),
        "lost_fields": list(VERL_LOST_FIELDS),
        "metadata": {
            "compatibility_target": "VeRL JSONL/Parquet probe v1alpha; not DataProto or trainer execution",
        },
    }


def validate_verl_probe_projection_manifest(manifest: Mapping[str, Any], rows: Iterable[VerlProbeRow]) -> list[str]:
    materialized_rows = list(rows)
    errors: list[str] = []
    if manifest.get("schema_version") != VERL_PROJECTION_MANIFEST_SCHEMA:
        errors.append(f"schema_version must be {VERL_PROJECTION_MANIFEST_SCHEMA!r}")
    if manifest.get("claim_boundary") != "verl_shaped_probe_not_trainer_ready":
        errors.append("claim_boundary must remain verl_shaped_probe_not_trainer_ready")
    if manifest.get("canonical_truth") != "breadboard_graph_replay_runtime":
        errors.append("canonical_truth must be breadboard_graph_replay_runtime")
    if int(manifest.get("row_count", -1)) != len(materialized_rows):
        errors.append("row_count must match exported rows")
    if int(manifest.get("trainable_candidate_count", -1)) != sum(row.trainable_candidate for row in materialized_rows):
        errors.append("trainable_candidate_count must match exported rows")
    target_formats = set(manifest.get("target_formats") or [])
    if not {"jsonl", "parquet"}.issubset(target_formats):
        errors.append("target_formats must include jsonl and parquet")
    source_projection_ids = set(manifest.get("source_projection_manifest_ids") or [])
    expected_projection_ids = {row.projection_manifest_id for row in materialized_rows}
    if source_projection_ids != expected_projection_ids:
        errors.append("source_projection_manifest_ids must match row projection_manifest_id values")
    if not set(VERL_PRESERVED_FIELDS).issubset(set(manifest.get("preserved_fields") or [])):
        errors.append("preserved_fields must include required VeRL probe fields")
    if "trainer_dataproto_object" not in set(manifest.get("lost_fields") or []):
        errors.append("lost_fields must record deferred trainer_dataproto_object")
    return errors


def write_verl_probe_projection_manifest(rows: Iterable[VerlProbeRow], path: Path, target_formats: Iterable[str]) -> dict[str, Any]:
    manifest = build_verl_probe_projection_manifest(rows, target_formats)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def write_verl_probe_parquet(rows: Iterable[VerlProbeRow], path: Path) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - exercised only on missing optional dependency.
        raise RuntimeError("pyarrow is required for VeRL Parquet probe export") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    payloads = [row.to_dict() for row in rows]
    table = pa.Table.from_pylist(payloads)
    pq.write_table(table, path)


def _smoke_report(rows: list[VerlProbeRow], errors: list[dict[str, Any]], compatibility_target: str) -> dict[str, Any]:
    return {
        "row_count": len(rows),
        "trainable_candidate_count": sum(row.trainable_candidate for row in rows),
        "tensorizable": not errors,
        "errors": errors,
        "compatibility_target": compatibility_target,
    }


def smoke_consume_verl_probe_jsonl(path: Path) -> dict[str, Any]:
    rows = []
    errors: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = VerlProbeRow.from_dict(json.loads(line))
        except Exception as exc:
            errors.append({"line": line_number, "errors": [str(exc)]})
            continue
        row_errors = validate_verl_probe_row(row)
        if row_errors:
            errors.append({"line": line_number, "task_id": row.task_id, "errors": row_errors})
        rows.append(row)
    return _smoke_report(rows, errors, "VeRL JSONL probe v1alpha; not DataProto or trainer execution")


def smoke_consume_verl_probe_parquet(path: Path) -> dict[str, Any]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - exercised only on missing optional dependency.
        raise RuntimeError("pyarrow is required for VeRL Parquet probe smoke consumption") from exc

    rows = []
    errors: list[dict[str, Any]] = []
    for row_number, payload in enumerate(pq.read_table(path).to_pylist(), start=1):
        try:
            row = VerlProbeRow.from_dict(payload)
        except Exception as exc:
            errors.append({"row": row_number, "errors": [str(exc)]})
            continue
        row_errors = validate_verl_probe_row(row)
        if row_errors:
            errors.append({"row": row_number, "task_id": row.task_id, "errors": row_errors})
        rows.append(row)
    return _smoke_report(rows, errors, "VeRL Parquet probe v1alpha; not DataProto or trainer execution")
