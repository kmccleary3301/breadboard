from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

PROGRAM_ID = "bb-zyphra-rl-phase5-v2"
REVISION_ID = "v2.0.0-rc5-20260717"
ARCHIVE_ID = "v1-bootstrap-20260709-sealed-rc3"
SUPERSEDED_REVISION_ID = "v2.0.0-rc4-20260715"
SUPERSEDED_ARTIFACT_MANIFEST_SHA256 = "sha256:b5897c0465bfb0cdf4b3aa79427c55e85b8a1d0b600c40e6d6eb62b579e9cbfd"
V1_ACTIVE_SHA256 = "sha256:bec45628402972644a24f1c11f80024e8780eb2c6817d90a45d3cd19a94928b6"
V1_SCORECARD_SHA256 = "sha256:df8e69a610b7ba69237642ff7a49d42fb1819ae919be224e4a1399b246542a23"
PARENT_REPO = Path("/Users/kylemccleary/projects/breadboard/breadboard_rl_phase3_finalization_20260708")
WRAPPER_REPO = Path("/Users/kylemccleary/projects/breadboard/verl_wrapper_breadboard_integration_20260709")
EXECUTION_ROOT = Path("/Users/kylemccleary/projects/breadboard/docs_tmp/ZYPHRA/RL_PHASE_5/execution")
EVIDENCE_ROOT = Path("/Users/kylemccleary/projects/breadboard/docs_tmp/ZYPHRA/RL_PHASE_5")


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_canonical(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value))
    path.chmod(0o444)


def run_git(repo: Path, *args: str, text: bool = True) -> str | bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=text,
    )
    return completed.stdout.rstrip("\n") if text else completed.stdout


def source_inventory(repo: Path, name: str) -> dict[str, Any]:
    raw = run_git(
        repo,
        "-c",
        "core.quotePath=false",
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
        text=False,
    )
    assert isinstance(raw, bytes)
    fields = raw.rstrip(b"\0").split(b"\0") if raw else []
    rows: list[dict[str, Any]] = []
    index = 0
    while index < len(fields):
        record = fields[index].decode()
        status_code = record[:2]
        relative_path = record[3:]
        original_path = None
        if "R" in status_code or "C" in status_code:
            index += 1
            original_path = fields[index].decode()
        path = repo / relative_path
        row: dict[str, Any] = {
            "adoption_state": "paused_unadmitted",
            "original_path": original_path,
            "path": relative_path,
            "status": status_code,
        }
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            row["missing"] = True
        else:
            row["mode"] = f"{stat.S_IMODE(metadata.st_mode):04o}"
            row["size"] = metadata.st_size
            if path.is_symlink():
                row["symlink_target"] = os.readlink(path)
            elif path.is_file():
                row["sha256"] = sha256_file(path)
            elif path.is_dir():
                row["kind"] = "directory"
            else:
                row["kind"] = "other"
        rows.append(row)
        index += 1
    head = run_git(repo, "rev-parse", "HEAD")
    branch = run_git(repo, "branch", "--show-current")
    assert isinstance(head, str) and isinstance(branch, str)
    return {
        "branch": branch,
        "dirty_entries": len(rows),
        "entries": rows,
        "head": head,
        "path": str(repo),
        "repository": name,
    }


def load_beads(export_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw = export_path.read_bytes()
    child_ids = {f"bb-auh.{index}" for index in range(1, 68)}
    issues = [json.loads(line) for line in raw.splitlines() if line]
    children = sorted(
        (
            issue
            for issue in issues
            if issue.get("id") in child_ids
            if any(
                edge.get("type") == "parent-child"
                and edge.get("depends_on_id") == "bb-auh"
                for edge in issue.get("dependencies", [])
            )
        ),
        key=lambda issue: int(issue["id"].split(".")[1]),
    )
    decision_ids = {f"bb-6d4.{index}" for index in range(1, 9)}
    decisions = sorted(
        (issue for issue in issues if issue["id"] in decision_ids),
        key=lambda issue: int(issue["id"].split(".")[1]),
    )
    if len(children) != 67:
        raise ValueError(f"expected 67 bb-auh children, found {len(children)}")
    if len(decisions) != 8 or any(issue["status"] != "closed" for issue in decisions):
        raise ValueError("expected eight closed Wayfinder map decisions")
    return children, decisions


def record(path: Path, expected: str | None = None) -> dict[str, Any]:
    actual = sha256_file(path)
    if expected is not None and actual != expected:
        raise ValueError(f"hash changed for {path}: expected {expected}, got {actual}")
    return {"path": str(path), "sha256": actual, "size": path.stat().st_size}


def catalog_definitions() -> dict[str, Any]:
    return {
        "cross_reference_resolution": {
            "Config optimizer acceptance": "config_optimizer_acceptance",
            "L6": "l6_admitted_set_conformance",
            "Training gates": "training_gates",
            "canonical two-hour soak": "f7_performance_and_soak.canonical_soak",
            "control-plane thresholds": "f7_performance_and_soak.control_plane_gates",
            "load-ladder thresholds": "f7_performance_and_soak.load_ladder",
        },
        "config_optimizer_acceptance": {
            "aa_noise_control_required": True,
            "accepted_variant_min_paired_ab_evaluations": 20,
            "accepted_variant_must_repeat_on_held_out_task_set": True,
            "all_non_config_inputs_identical": True,
            "no_regression_dimensions": ["correctness", "security", "isolation", "evidence", "cleanup"],
            "parent_and_rejected_variants_immutable_and_reproducible": True,
            "primary_objective_and_secondary_cost_frozen_before_runs": True,
            "selection_rule": "improvement exceeds the A/A noise interval, or ties while lowering tokens, turns, or latency by the predeclared minimum",
            "zero_acceptance_result": "no_variant_accepted; publish no optimized config set",
        },
        "f7_performance_and_soak": {
            "canonical_soak": {
                "arrival": "closed_loop_immediate_terminal_replacement",
                "attempted_episodes_min": 256,
                "completion_fraction_min": 0.995,
                "f4_config_episode_min_each": 32,
                "f4_config_count": 6,
                "integrity_identity_cleanup_secret_failures_max": 0,
                "measured_minutes": 105,
                "resource_sample_interval_seconds": 15,
                "rss_final_window_p95_max_fraction_of_first_window_p95": 1.05,
                "rss_monotonic_growth_consecutive_five_minute_medians_max": 4,
                "rss_window_minutes": 30,
                "rswe_001_episode_min": 64,
                "terminal_records_min": 256,
                "topology": "freeze after load ladder; default one IBM node at concurrency 8 unless the frozen manifest justifies another tested cell",
                "total_minutes": 120,
                "warmup_minutes": 15,
            },
            "control_plane_gates": {
                "cached_effective_plan_resolution_p95_ms_max": 10,
                "cached_selection_p99_ms_max": 2,
                "cold_compile_p95_ms_max": 500,
                "compiled_bytes_and_digests_repeat_identically": True,
                "config_native_end_to_end_p95_overhead_fraction_max": 0.10,
                "cross_config_identity_mismatch_max": 0,
                "evidence_join_completeness_fraction": 1.0,
                "negative_rejection_at_expected_stage_fraction": 1.0,
                "positive_compiler_admission_correctness_fraction": 1.0,
                "runtime_source_reads_max": 0,
                "throughput_regression_fraction_max": 0.10,
            },
            "episode_gates": {
                "gold_bad_noop_classification_fraction": 1.0,
                "gold_bad_noop_clean_reruns_each": 2,
                "mixed_config_p95": "below the timeout declared and hash-bound for that row at every required concurrency",
                "positive_infrastructure_completion_fraction": 1.0,
                "remaining_runtime_residue_max": 0,
                "requested_effective_measured_runtime_equality_fraction": 1.0,
                "unexplained_reward_quarantine_max": 0,
            },
            "load_ladder": {
                "concurrency_cells": [1, 2, 4, 8, 16, 32],
                "correctness_required": True,
                "hidden_fallback_allowed": False,
                "linear_scale_required": False,
                "metrics": ["counts", "throughput", "p50_p95_p99_latency", "queue", "policy_generation", "host_gpu_ray_resources", "cache_compile_materialization", "fault_cleanup", "selection_oracle"],
                "saturation_point_required": True,
            },
            "threshold_binding": "workload, model/checkpoint, config set, runtime, cache state, repetitions, row timeouts, and thresholds are immutable campaign-manifest fields; any change creates a new lineage",
        },
        "l6_admitted_set_conformance": {
            "claimed_environments": [
                {"environment_id": "local_docker", "required_identity": ["image_digest", "runtime", "kernel", "effective_security", "task_contract"]},
                {"environment_id": "ibm_one_node", "required_identity": ["Slurm_job", "node", "image_digest", "runtime", "model", "task_contract"]},
            ],
            "deeper_representative_campaigns": ["same_task_ab", "mutation", "concurrency", "fault", "replay"],
            "nonclaims": ["local process", "runsc unless F10 activates it", "multi-node topology", "later receipts", "broader environments"],
            "per_receipt_canary_required_on_every_claimed_environment": True,
            "representative_substitution_for_per_receipt_canary_forbidden": True,
            "version_closed_by_admission_set_root": True,
        },
        "training_gates": {
            "aborted_ratio_max": 0.0,
            "actor_gradient_norm": {"exclusive_min": 0.0, "inclusive_max": 100.0},
            "advantage_absolute_max_exclusive_min": 0.0,
            "calibration_generated_samples_min": 64,
            "canonical_checkpoint_tree_roots_must_differ": True,
            "changed_named_parameter_tensor_digest_required": True,
            "dropped_stale_samples_max": 0,
            "failed_output_and_checkpoint_quarantine_required": True,
            "fresh_process_reload_and_bounded_inference_or_preflight_required": True,
            "group_task_and_reward_semantics_identical": True,
            "kl_metrics_required": True,
            "learning_rate_exclusive_min": 0.0,
            "longer_bounded_run": {"generated_samples_min": 256, "optimizer_steps_min": 10, "predeclared_wall_clock_and_step_maxima_required": True},
            "optimizer_step_skipped_allowed": False,
            "optimizer_steps_min": 3,
            "parity_envelope": {"rollout_corr_k3_kl_max": 0.0001, "rollout_corr_kl_max": 0.01},
            "production_profile_generated_samples_min": 40,
            "reward_range_exclusive_min": 0.0,
            "sample_episode_and_carrier_join_fraction": 1.0,
        },
    }


def build_catalog(scorecard: dict[str, Any], scorecard_sha256: str) -> tuple[dict[str, Any], dict[str, Any]]:
    items = [
        {
            "description": item["description"],
            "item_id": item["item_id"],
            "legacy_issue_id": item["issue_id"],
            "legacy_owner_packet": item["owner_packet"],
            "pass_predicate": item["pass_predicate"],
            "points": item["points"],
            "proof_floor": item["proof_floor"],
            "workstream": item["workstream"],
        }
        for item in scorecard["items"]
    ]
    catalog = {
        "catalog_points": 1000,
        "catalog_policy": {
            "award_mode": "all_or_nothing_per_item",
            "compensation_allowed": False,
            "partial_awards_allowed": False,
            "readiness_predicates_score_points": 0,
            "shared_transport_score_points": 0,
            "training_proof_track_score_points": 0,
        },
        "definitions": catalog_definitions(),
        "item_count": 49,
        "items": items,
        "program_id": PROGRAM_ID,
        "schema_version": "bb.rl.phase5.assurance_catalog.v4",
        "workstream_points": scorecard["workstream_points"],
    }
    projection = {
        "catalog_points": 1000,
        "item_count": 49,
        "items": items,
        "schema_version": scorecard["schema_version"],
        "workstream_points": scorecard["workstream_points"],
    }
    checks = {
        "descriptions_exact": all(
            item["description"] == scorecard["items"][index]["description"]
            for index, item in enumerate(items)
        ),
        "ids_order_exact": all(
            item["item_id"] == scorecard["items"][index]["item_id"]
            for index, item in enumerate(items)
        ),
        "item_count_eq_49": len(items) == 49,
        "no_mutable_award_state": all(
            not ({"state", "awarded_points", "evidence_ids"} & set(item)) for item in items
        ),
        "pass_predicates_exact": all(
            item["pass_predicate"] == scorecard["items"][index]["pass_predicate"]
            for index, item in enumerate(items)
        ),
        "points_exact": all(
            item["points"] == scorecard["items"][index]["points"]
            for index, item in enumerate(items)
        ),
        "proof_floors_exact": all(
            item["proof_floor"] == scorecard["items"][index]["proof_floor"]
            for index, item in enumerate(items)
        ),
        "total_points_eq_1000": sum(item["points"] for item in items) == 1000,
        "workstream_totals_exact": catalog["workstream_points"] == scorecard["workstream_points"],
    }
    equivalence = {
        "checks": checks,
        "result": "pass" if all(checks.values()) else "fail",
        "schema_version": "bb.rl.phase5.catalog_equivalence.v1",
        "source_path": "execution/SCORECARD.json",
        "source_projection_sha256": sha256_bytes(canonical_bytes(projection)),
        "source_sha256": scorecard_sha256,
        "v2_catalog_projection_sha256": sha256_bytes(canonical_bytes(projection)),
    }
    return catalog, equivalence


def program_spec() -> dict[str, Any]:
    return {
        "architecture_lock": {
            "breadboard": "Content-addressed control and evidence plane. Compile admitted config bundles to immutable execution plans; enforce operator policy before lease/network/provider action; persist deterministic per-rollout selections and admitted overlays; never redraw or fall back.",
            "clean_cutover": "Delete profile/family execution after migration. No shims, defaults, aliases, or fallback dispatch remain.",
            "episode_service": "Own lifecycle and emit opaque episode-scoped trained-policy runtime references to family-neutral runners.",
            "runtime": "Read no mutable path, CWD, global provider environment, or unadmitted capability. Provider/family execution is a config-selected overlay under the admitted policy binding.",
            "wrapper": "Transport opaque references, task data, carriers, evidence joins, and cleanup state; never compile or dispatch profile/family execution.",
        },
        "budgets": {
            "automatic_retry": False,
            "live_submission_sequences_per_admitted_packet": 1,
            "worker_repair_rounds_per_packet": 2,
            "additional_round_rule": "ESCALATED plus Kyle BUDGET_REVISION naming owner, root cause, changed invariant, revised limit, expiry, nonclaims, and wake condition",
        },
        "decision_lineage": [f"bb-6d4.{index}" for index in range(1, 9)],
        "global_nonclaims": [
            "No v2 item, track, campaign, score, completion, promotion, or external acceptance exists before typed current authority.",
            "Scratch files, issue status, chat, tests, reviews, manifests, and worker reports cannot authorize themselves.",
            "Observation-only reconciliation never resubmits and never converts unadmitted history into a pass.",
            "Training Proof completion never awards Assurance F8 or any other points.",
            "Assurance 1000/1000 is not external Zyphra acceptance.",
            "Target execution cannot run while shared runner/source/schema/evidence prerequisites are changing.",
        ],
        "human_gates": [
            {"scope": "exact program/catalog/DAG/state/migration roots before v2 activation", "type": "SPEC_FREEZE"},
            {"concrete_records": ["TRANSPORT_SMOKE_ADMISSION", "TRANSPORT_ADMISSION", "CAMPAIGN_ADMISSION"], "scope": "harmless transport smoke, shared transport admission, then every IBM campaign; each is a separate exact-hash decision", "type": "CAMPAIGN_ADMISSION"},
            {"scope": "every attempt-budget escalation", "type": "BUDGET_REVISION"},
            {"scope": "final score/internal completion/checkpoint/promotion decisions; external acceptance stays separate", "type": "FINAL_PROMOTION"},
        ],
        "migration_contracts": {
            "fresh_worker_program_replay": "FRESH_WORKER_HANDOFF_CONTRACT.json",
            "migration_replay": "MIGRATION_REPLAY_CONTRACT.json",
            "quiescence": "QUIESCENCE_CONTRACT.json",
            "session_handoff": "SESSION_HANDOFF_CONTRACT.json",
            "transaction": "MIGRATION_TRANSACTION.json",
        },
        "migration_revision": {
            "candidate_revision_id": REVISION_ID,
            "prior_spec_freeze_authority_for_candidate": False,
            "superseded_artifact_manifest_sha256": SUPERSEDED_ARTIFACT_MANIFEST_SHA256,
            "superseded_revision_id": SUPERSEDED_REVISION_ID,
            "supersession_scope": "migration and cutover mechanics only; catalog, program, queue, score, authority, transport, and target semantics are unchanged",
        },
        "mission": "Produce one reproducible IBM-backed finite-step RL training result and separately complete the preserved 49-item, 1000-point Phase 5 Assurance campaign with current evidence.",
        "program_id": PROGRAM_ID,
        "revision_id": REVISION_ID,
        "schema_version": "bb.rl.phase5.program_spec.v4",
        "shared_prerequisites": {
            "authority_and_state": "AT0-AT4 current machine/evidence/claim contracts",
            "evidence_reuse": "typed content-addressed relations only; campaign facts stay attempt-scoped",
            "source_freeze": "every packet binds adopted source paths/digests; dirty-tree proximity has no authority",
            "transport": "durable exact-hash runner, adversarial gate, independent review, Kyle-approved harmless stop/resume IBM smoke, smoke review, and TRANSPORT_ADMISSION",
        },
        "status": "draft_waiting_rc5_spec_freeze",
        "supersedes": {
            "active_status_sha256": V1_ACTIVE_SHA256,
            "disposition": "read_only_historical_lineage",
            "program_id": "bb-zyphra-rl-phase5-v1",
            "scorecard_sha256": V1_SCORECARD_SHA256,
        },
        "target": {
            "evidence_root": str(EVIDENCE_ROOT),
            "hardware": "IBM Zyphra Slurm MI300X",
            "slurm_partition": "gpu",
            "ssh_alias": "ZYPHRA_IBM_AMD_1",
            "target_campaign_concurrency": 1,
        },
        "tracks": {
            "assurance": {
                "catalog_items": 49,
                "catalog_path": "ASSURANCE_CATALOG.json",
                "catalog_points": 1000,
                "completion_rule": "all 49 current proof-floor predicates pass with current reviews and separate Kyle score decisions; no compensation, waiver, partial award, or weaker substitute",
                "dependency_path": "WORK_PACKET_DAG.yaml",
                "f3_blocks_training_proof": False,
                "f3_required": True,
                "scored": True,
            },
            "training_proof": {
                "claim": "On one admitted single-node IBM Zyphra Slurm/MI300X campaign, BreadBoard produced a real verifier reward for live policy rollouts, VeRL consumed the exact joined rewards in a nondegenerate GRPO group, at least one finite non-skipped optimizer step changed a named model parameter, the retained checkpoint reloaded in a fresh process, and BreadBoard closed and cleaned every episode.",
                "completion_authority": "typed Kyle TRAINING_PROOF_COMPLETION decision",
                "contract_path": "TRAINING_PROOF_CONTRACT.json",
                "nonclaims": ["F3", "F8", "multi-node readiness", "convergence", "benchmark improvement", "production readiness", "Phase 5 completion", "Assurance points", "promotion", "external Zyphra acceptance"],
                "scored": False,
            },
        },
    }


def training_contract(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "claim": spec["tracks"]["training_proof"]["claim"],
        "cleanup": "zero active episode/lease/actor/process/container/cgroup/mount/workspace/cache writer/secret/temp checkpoint; terminal scheduler, controller/target cleanup, and zero secret findings",
        "completion_authority": "typed Kyle TRAINING_PROOF_COMPLETION with no score/promotion/external-acceptance fields",
        "data": {
            "episodes": 4,
            "grpo_groups": 1,
            "max_response_tokens": 256,
            "rollout_n": 4,
            "sample_sources": ["live policy rollout only"],
            "seeds": "four distinct immutable seeds recorded before admission",
            "temperature": 0.8,
            "top_p": 0.95,
            "training_rows": 1,
        },
        "failure": ["transport ambiguity", "missing cleanup", "uniform reward", "skipped/nonfinite update", "stale carrier", "checkpoint mismatch", "reload failure", "secret finding", "orphan"],
        "model_runtime": {
            "estimator": "grpo",
            "model": "Qwen/Qwen2.5-0.5B-Instruct",
            "model_identity": "exact model tree, upstream revision, tokenizer tree, chat template, input checkpoint tree",
            "ppo_claimed": False,
            "rollout_backend": "vllm",
            "runtime_identity": "immutable image ID/tree/SBOM and Python/ROCm/Torch/VeRL/vLLM/Ray/TensorDict/Transformers/GPU/driver/job/node/module identities",
            "topology": "exactly one node and complete eight-MI300X allocation",
            "trainer_entrypoint": "verl.trainer.main_ppo",
        },
        "nonclaims": spec["tracks"]["training_proof"]["nonclaims"],
        "predicates": {
            "advantages": "persist exact consumed values; finite positive and negative; max(abs)>0; recompute within frozen tolerance",
            "checkpoint": "complete finite pre/post trees with different roots; new content-addressed output; no input overwrite",
            "joins": "four unique 1:1 episode/attempt/carrier joins; unmatched/duplicate zero; full target/task/config/model/tokenizer/checkpoint/image/runtime/policy/response/verifier/reward/seed binding",
            "optimizer": "at least one non-skipped finite step; LR>0; actor gradient norm finite and 0<norm<=100; zero stale/aborted; finite required KL/K3; named tensor digest changes",
            "reload": "fresh process without trainer state verifies identities and changed tensor, then bounded inference or trainer preflight",
            "rewards": "four live finite rewards in [0,1], max>min, at least one positive",
        },
        "program_id": PROGRAM_ID,
        "reproducibility": [
            "pre-admission archive builds twice identically",
            "record source/payload/input/seeds/argv/resolved config/runtime/output layout",
            "offline content-addressed recipe from input checkpoint; secrets/access stay operator inputs",
            "clean process replays verifier, joins, rewards, advantages, thresholds, checkpoint trees, changed tensor, and reload",
            "independent current-hash review before and after target",
        ],
        "required_artifacts": ["track spec", "task/verifier", "source/runtime manifests", "config closure/compiler/admission/effective plan/policy/selection", "target runner", "four episode/carrier/verifier/reward records", "trainer dataset", "GRPO/optimizer config", "metrics/step receipts", "pre/post checkpoint trees", "changed tensor", "reload", "cleanup/secret scan", "stdout/stderr", "double-build", "independent recomputation", "reviews", "limitations/nonclaims", "Kyle completion decision"],
        "schema_version": "bb.rl.phase5.training_proof_contract.v2",
        "scored": False,
        "task": {
            "expected_fields": {
                "median": 3.5,
                "sha256": "0a1e71f369be04257e22612a8f6b8c56307ae8556e0fa2a1e3f4882569789490",
                "sorted_unique": [1, 2, 3, 4, 5, 6, 9],
                "sum": 31,
            },
            "policy_visibility": "prompt only; verifier source, expected-output bytes, and prior samples hidden",
            "prompt_input": [3, 1, 4, 1, 5, 9, 2, 6],
            "response_contract": "one JSON object, no prose",
            "task_id": "TRP-CAL-001",
            "verifier_reward": "0.25 for each exact field; 0 for malformed or non-object output",
        },
        "track": "training_proof",
    }


def loop_spec() -> dict[str, Any]:
    states = ["DRAFT", "AUTHORING", "LOCAL_VALIDATION", "LOCAL_REPAIR", "LOCAL_APPROVED", "ADMISSION_REVIEW", "ADMITTED", "SUBMIT_STARTED", "IBM_SUBMITTED", "IBM_OBSERVING", "IBM_TERMINAL", "COLLECTED", "CLEANUP_RECONCILED", "EVIDENCE_READY", "REVIEW_PENDING", "REVIEW_CHANGES_REQUIRED", "REVIEW_APPROVED", "DECISION_PENDING", "SATISFIED", "FAILED_CLOSED", "BLOCKED", "WAITING_EXTERNAL", "WAITING_HUMAN", "ESCALATED", "SUPERSEDED", "REVOKED", "QUARANTINED"]
    return {
        "attempt_budget": {
            "historical_import": "preserve observed counters; never admit or replenish",
            "limits": {"live_submission_sequences": 1, "repair_rounds": 2},
            "live_submission_counter": "consume at BEGIN_SUBMIT; transport outcome never restores it",
            "new_revision": "does not reset inherited counters without Kyle BUDGET_REVISION",
            "repair_counter": "increment on implementation change after failed gate or review",
        },
        "authority_model": {
            "beads": "decision/backlog projection only",
            "canonical_state": "hash-chained immutable events plus deterministic snapshot and one active pointer",
            "chat": "non-authoritative except bounded bootstrap SPEC_FREEZE described in AUTHORITY_POLICY",
            "ownership": "one owner per packet; reviewer and authority roles distinct",
            "worker_output": "non-authoritative until admitted evidence and decisions",
        },
        "concurrency": {
            "canonical_promotion_concurrency": 1,
            "checkpoint_promotion_concurrency": 1,
            "independence_required": ["disjoint files", "disjoint packet roots", "disjoint active pointers", "no moving shared prerequisite", "no shared target lease"],
            "workers_max": 4,
        },
        "failure_taxonomy": {
            "CLEANUP_UNRESOLVED": "job/process/artifact/secret residue cannot be proven absent",
            "COMPONENT_FAILURE": "trusted exact component failed or component set incomplete/extra/duplicate",
            "HUMAN_OR_EXTERNAL_BLOCK": "typed approval, credential, endpoint, or external artifact unavailable",
            "LOCAL_CONTRACT_FAILURE": "schema, deterministic build, compile, unit/integration, or local proof failure",
            "QUALITY_PREDICATE_FAILURE": "runtime facts valid but task/reward/training/topology predicate failed",
            "SCHEDULER_FAILURE": "exact job terminal non-success, resource mismatch, deadline, or malformed/conflicting scheduler record",
            "SECURITY_PROVENANCE_FAILURE": "secret, signature, path, ownership, source closure, or artifact integrity failure",
            "STALE_LINEAGE": "request/input/output/review/authority digest or freshness mismatch",
            "SUBMISSION_OUTCOME_UNKNOWN": "submit-capable side effect began but exact job cannot be uniquely bound",
            "TRANSPORT_FAILURE": "SSH/SCP/submit/poll/fetch/cancel/cleanup failure with known job state",
        },
        "freshness": {
            "dependency_identity": ["program spec", "catalog/DAG", "source", "runner/schema/consumer", "packet/payload", "task/config/model/runtime/reward/verifier", "review", "authority"],
            "failed_rerun_invalidates_previous_success": True,
            "review_stales_on_load_bearing_change": True,
            "transitive_revocation": True,
        },
        "observation_only": {
            "allowed_actions": ["query exact squeue/sacct identity", "read exact persisted receipt/state", "fetch already-produced exact artifacts when separately authorized", "cleanup exact terminal owned path when separately authorized"],
            "forbidden_actions": ["submit", "upload new payload", "create new attempt/root", "change command/packet identity", "convert unadmitted history to pass", "restore consumed budget"],
        },
        "packet_states": states,
        "program_id": PROGRAM_ID,
        "schema_version": "bb.rl.phase5.loop_spec.v3",
        "score_and_claim_separation": {
            "assurance_score": "derived from 49 row decisions only",
            "checkpoint_disposition": "separate authority",
            "external_acceptance": "separate Zyphra authority",
            "internal_completion": "separate conditional authority",
            "promotion": "separate authority",
            "training_track_state": "separate unscored field",
        },
        "target_lease": {
            "acquire_guards": ["packet ADMITTED", "transport current", "no live/unknown job", "exact target resources bound"],
            "release_guards": ["exact job terminal", "cleanup reconciled or unresolved cleanup persisted"],
            "scope": "global program singleton",
            "unknown_submission": "holds lease and blocks later target work until reconciled or Kyle disposition",
        },
        "terminal_packet_states": ["SATISFIED", "FAILED_CLOSED", "SUPERSEDED", "REVOKED", "QUARANTINED"],
        "transition_rules": [
            "dependencies, ownership, hashes, reviews, budgets, and authority guard every forward transition",
            "BEGIN_SUBMIT durably consumes the sole live sequence before any submit-capable side effect",
            "response loss moves only to observation-only exact job reconciliation",
            "timeout/cancel states are monotonic and late output cannot become passing",
            "component/evidence/cleanup all pass before EVIDENCE_READY",
            "failed reruns and load-bearing edits revoke descendants transitively",
            "missing external input persists exact owner/input/search/nonclaim/wake condition",
            "budget exhaustion moves to ESCALATED; workers cannot self-extend",
        ],
    }


def dag(catalog: dict[str, Any]) -> dict[str, Any]:
    nodes = [
        {"depends_on": [], "exit": "authority, catalog, taxonomy, active-pointer, and invalidation contracts current", "id": "AT0", "kind": "local", "score_rows": ["A1", "A2", "A3", "A4", "A5"]},
        {"depends_on": ["AT0"], "exit": "bounded CAS ingestion and compiler closure current", "id": "AT1", "kind": "local", "score_rows": ["B1", "B2", "B3", "B4", "B7"]},
        {"depends_on": ["AT1"], "exit": "admission, effective plan, persisted selection, and false-win gates current", "id": "AT2", "kind": "local", "score_rows": ["B5", "B6", "C1", "C2", "C3", "C4", "C6", "D2", "G1"]},
        {"depends_on": ["AT2"], "exit": "family-neutral lifecycle, sandbox, replay, evidence, and cleanup current", "id": "AT3", "kind": "local", "score_rows": ["C5", "D1", "D4", "D6", "E1", "E2", "E4", "E5"]},
        {"depends_on": ["AT3"], "exit": "wrapper cutover, legacy deletion, lineage, redaction/reproduction, and target preflights current", "id": "AT4", "kind": "local", "readiness_closures": ["D5.launch_ready", "F9.estimator_truth_ready", "G2.anti_laundering_preflight", "G3.stale_tamper_preflight"], "score_rows": ["D3", "D7", "E3", "E6"]},
        {"depends_on": ["AT4"], "exit": "runner review, harmless stop/resume smoke, smoke review, and TRANSPORT_ADMISSION current", "id": "SHARED_TRANSPORT", "kind": "shared_non_scoring", "score_rows": []},
        {"depends_on": ["AT4", "SHARED_TRANSPORT"], "exit": "typed Training Proof completion on exact four-episode GRPO/checkpoint/reload/cleanup root", "human_approval": "CAMPAIGN_ADMISSION", "id": "TRAINING_PROOF", "kind": "target_single_node", "score_rows": [], "target_lease": True},
        {"conditional_depends_on": {"SHARED_TRANSPORT": "only if a new IBM run is required"}, "depends_on": ["AT4"], "id": "AT5_F10", "kind": "portability_decision", "score_rows": ["F10"]},
        {"depends_on": ["AT4", "AT5_F10"], "id": "AT5_G4", "kind": "local", "score_rows": ["G4"]},
        {"depends_on": ["AT4", "SHARED_TRANSPORT"], "human_approval": "CAMPAIGN_ADMISSION", "id": "AT6_F1_D5", "kind": "target_single_node", "score_rows": ["F1", "D5"], "target_lease": True},
        {"depends_on": ["AT6_F1_D5"], "human_approval": "CAMPAIGN_ADMISSION", "id": "AT6_F2", "kind": "target_single_node", "score_rows": ["F2"], "target_lease": True},
        {"depends_on": ["AT6_F2"], "extra_guard": "closed real task/source/image/verifier package", "human_approval": "CAMPAIGN_ADMISSION", "id": "AT6_F3", "kind": "target_single_node", "score_rows": ["F3"], "target_lease": True},
        {"depends_on": ["AT6_F3"], "extra_guard": "exact frozen six-receipt admitted-set root", "human_approval": "CAMPAIGN_ADMISSION", "id": "AT6_F4", "kind": "target_single_node", "score_rows": ["F4"], "target_lease": True},
        {"depends_on": ["AT6_F2"], "human_approval": "CAMPAIGN_ADMISSION", "id": "AT6_F6", "kind": "target_single_node", "score_rows": ["F6"], "target_lease": True},
        {"depends_on": ["AT5_G4", "AT6_F4", "AT6_F6"], "human_approval": "CAMPAIGN_ADMISSION", "id": "AT6_F5", "kind": "target_single_node", "score_rows": ["F5"], "target_lease": True},
        {"depends_on": ["AT4", "AT6_F2", "SHARED_TRANSPORT"], "extra_guard": "F9.estimator_truth_ready; F8 minimum 64 samples and 3 optimizer steps", "human_approval": "CAMPAIGN_ADMISSION", "id": "AT7_F8_F9", "kind": "target_training", "score_rows": ["F8", "F9"], "target_lease": True},
        {"depends_on": ["AT6_F1_D5", "AT6_F2", "AT6_F3", "AT6_F4", "AT6_F5", "AT6_F6", "SHARED_TRANSPORT"], "extra_guard": "reviewed topology transport extension; exact two-node qualification only; no F7 score", "human_approval": "CAMPAIGN_ADMISSION", "id": "AT7_F7_TWO_NODE", "kind": "target_multi_node_qualification", "live_submission_sequences": 1, "score_rows": [], "target_lease": True},
        {"depends_on": ["AT7_F7_TWO_NODE"], "extra_guard": "current exact two-node qualification; guarded four-node scoring campaign", "human_approval": "CAMPAIGN_ADMISSION", "id": "AT7_F7_FOUR_NODE", "kind": "target_multi_node_scoring", "live_submission_sequences": 1, "score_rows": ["F7"], "target_lease": True},
        {"depends_on": ["AT5_F10", "AT5_G4", "AT7_F7_FOUR_NODE", "AT7_F8_F9", "TRAINING_PROOF"], "id": "AT8_G2_G3", "kind": "local_final_attack", "score_rows": ["G2", "G3"]},
        {"depends_on": ["AT8_G2_G3"], "human_approval": "FINAL_PROMOTION decision family", "id": "AT8_H3", "kind": "authority", "score_rows": ["H3"]},
        {"depends_on": ["AT8_H3"], "id": "AT8_H1", "kind": "assembly", "score_rows": ["H1"]},
        {"depends_on": ["AT8_H1"], "id": "AT8_H2", "kind": "independent_review", "score_rows": ["H2"]},
        {"depends_on": ["AT8_H2"], "id": "AT8_H4", "kind": "handoff", "score_rows": ["H4"]},
    ]
    readiness = [
        {"closes_item": "D5 only after AT6_F1_D5 target proof", "depends_on": ["AT4"], "id": "D5.launch_ready", "points": 0},
        {"closes_item": "F9 only after AT7_F8_F9 target report", "depends_on": ["AT4"], "id": "F9.estimator_truth_ready", "points": 0},
        {"closes_item": "G2 only at AT8_G2_G3", "depends_on": ["AT4"], "id": "G2.anti_laundering_preflight", "points": 0},
        {"closes_item": "G3 only at AT8_G2_G3", "depends_on": ["AT4"], "id": "G3.stale_tamper_preflight", "points": 0},
    ]
    rows = [row for node in nodes for row in node["score_rows"]]
    expected = [item["item_id"] for item in catalog["items"]]
    if len(rows) != 49 or len(set(rows)) != 49 or set(rows) != set(expected):
        raise ValueError("DAG must contain every catalog row exactly once")
    return {
        "nodes": nodes,
        "policies": {
            "all_catalog_rows_appear_once": True,
            "beads_is_projection": True,
            "downstream_authoring_requires_current_dependencies": True,
            "machine_events_are_authority": True,
            "readiness_points": 0,
            "target_lease_concurrency": 1,
        },
        "program_id": PROGRAM_ID,
        "readiness_predicates": readiness,
        "schema_version": "bb.rl.phase5.work_packet_dag.v3",
    }


def campaign_matrix() -> dict[str, Any]:
    common = {
        "approval": "typed Kyle CAMPAIGN_ADMISSION on exact packet/request/payload/transport/dependency hashes",
        "always_required_outputs": ["transport request/state", "job receipt", "scheduler observations", "terminal metadata", "stdout/stderr", "exact component bundle", "cleanup tombstone", "secret scan", "nonclaims"],
        "live_sequences": 1,
        "required_shared_roots": ["program spec", "loop spec", "DAG", "source manifest", "transport admission", "packet spec", "payload manifest", "review verdict", "attempt budget"],
        "target": {"partition": "gpu", "ssh_alias": "ZYPHRA_IBM_AMD_1"},
        "target_lease": "global singleton",
    }
    rows = [
        {"approval": "typed Kyle TRANSPORT_SMOKE_ADMISSION on exact runner/test/schema/smoke/request/payload/dependency hashes", "components": ["transport_smoke"], "depends_on": ["AT4"], "id": "TRANSPORT_SMOKE", "payload": "standard-library fixed nonce only", "required_shared_roots": ["program spec", "loop spec", "DAG", "source manifest", "runner repair review", "smoke packet", "payload manifest", "attempt budget"], "resources": {"deadline_seconds": 300, "gpus": 0, "nodes": 1, "tasks": 1}, "track": "shared"},
        {"components": ["episode_lifecycle", "verifier_reward", "carrier_join", "trainer_update", "checkpoint_change", "fresh_reload", "cleanup"], "depends_on": ["AT4", "SHARED_TRANSPORT"], "id": "TRAINING_PROOF", "payload": "TRP-CAL-001, four live rollouts, one nondegenerate GRPO update, checkpoint reload", "track": "training_proof"},
        {"components": ["target_readiness", "eval_smoke", "train_smoke", "credential_harness_propagation"], "depends_on": ["AT4", "SHARED_TRANSPORT"], "id": "F1", "score_rows": ["F1", "D5"], "track": "assurance"},
        {"components": ["canonical_terminal_episode", "reward_receipt", "dual_store", "cleanup"], "depends_on": ["F1", "D5"], "id": "F2", "score_rows": ["F2"], "track": "assurance"},
        {"components": ["pinned_swe_episode", "source_closure", "quality_predicate", "cleanup"], "depends_on": ["F2", "real_task_package"], "id": "F3", "score_rows": ["F3"], "track": "assurance"},
        {"components": ["six_config_canaries", "per_receipt_identity", "per_receipt_cleanup"], "depends_on": ["F3", "C6", "G1", "six_receipt_root"], "id": "F4", "score_rows": ["F4"], "track": "assurance"},
        {"components": ["restart_cache_replay", "fresh_live_reexecution", "cleanup"], "depends_on": ["F2", "E4", "E5"], "id": "F6", "score_rows": ["F6"], "track": "assurance"},
        {"components": ["heterogeneous_configs", "timeout", "cancel", "revocation", "egress", "resource", "verifier", "artifact_faults", "cleanup"], "depends_on": ["F4", "F6", "G4", "E5"], "id": "F5", "score_rows": ["F5"], "track": "assurance"},
        {"components": ["config_native_rewards", "carrier_join", "grpo_training", "checkpoint_change", "reload", "cleanup"], "depends_on": ["F1", "F2", "F9.estimator_truth_ready", "E3", "E5", "E6"], "id": "F8", "minimums": {"generated_samples": 64, "optimizer_steps": 3}, "score_rows": ["F8", "F9"], "track": "assurance"},
        {"attempt_budget_key": "AT7_F7_TWO_NODE", "components": ["placement", "topology", "soak", "performance", "resource_samples", "integrity_secret_cleanup"], "depends_on": ["F1", "F2", "F3", "F4", "F5", "F6", "topology_transport_review"], "id": "F7_TWO_NODE", "packet_key": "AT7_F7_TWO_NODE", "resources": {"nodes": 2}, "score_rows": [], "track": "assurance"},
        {"attempt_budget_key": "AT7_F7_FOUR_NODE", "components": ["placement", "topology", "soak", "performance", "resource_samples", "integrity_secret_cleanup"], "depends_on": ["F7_TWO_NODE"], "id": "F7_FOUR_NODE", "packet_key": "AT7_F7_FOUR_NODE", "resources": {"nodes": 4}, "score_rows": ["F7"], "track": "assurance"},
        {"components": ["source_closed_isolation", "gold_bad_noop", "cleanup"], "depends_on": ["AT4", "frozen_portability_branch"], "id": "F10_CONDITIONAL", "track": "assurance"},
    ]
    campaigns = [{**common, **row} for row in rows]
    recovery = [
        {"approval": "RECONCILE_ONLY", "claim_boundary": "historical scheduler/residue facts only", "cleanup_requires": "separate CLEANUP_ONLY", "id": identity, "mode": "observation_only", "new_attempt_allowed": False, "submit_allowed": False, "target": common["target"], "upload_allowed": False}
        for identity in ("RECONCILE_F2_R29", "RECONCILE_F3_R50", "RECONCILE_F4_R9")
    ]
    return {
        "campaigns": campaigns,
        "policies": {"changed_shared_root_revokes_unsent_admission": True, "cleanup_required_for_pass": True, "component_set_exact": True, "every_live_campaign_requires_kyle_admission": True, "no_automatic_retry": True, "no_row_admitted_at_migration": True, "one_target_lease": True},
        "program_id": PROGRAM_ID,
        "recovery_actions": recovery,
        "schema_version": "bb.rl.phase5.campaign_matrix.v4",
    }


def transport_contract() -> dict[str, Any]:
    return {
        "adversarial_gate": {
            "1": "digest sensitivity for every target/resource/component/payload/timeout/cleanup field and stale replay",
            "2": "argv/host/path/shell injection, traversal, pending IDs, resource bounds",
            "3": "separate-process concurrency and restart; one root/dispatch/job/sbatch",
            "4": "crash before/after every durable transition",
            "5": "pre-dispatch timeout, response loss, zero/one/multiple matches, restart without duplicate submit",
            "6": "arbitrary pass, wrong schema/identity/component, output spoof, post-parse rewrite",
            "7": "missing/truncated/oversized/swapped/stale/symlinked/digest-size-nonce artifacts",
            "8": "all scheduler states, lag, malformed/conflicting/step/array rows, wrong resources/cardinality",
            "9": "deadline, timeout restart, failed scancel, cancel/completion race, trap/cleanup failure",
            "10": "real timeout/nonzero for upload/submit/observe/fetch/cancel/cleanup with durable state",
            "11": "active-job cleanup denial, exactly-once terminal cleanup, restart, tombstone, corrupt evidence",
            "12": "seeded secrets in every surface, mode/traversal/link/limit attacks, zero leakage",
            "13": "exact one/two/four-node transport identities and shared-filesystem durability without topology claims",
        },
        "component_authority": {"exact_declared_rows": "(component, report_id, schema_version, component_input_digest)", "outer_pass": "scheduler/exit, terminal capture, exact components, evidence hashes, and cleanup all pass", "stdout_stderr_are_data": True, "trusted_wrapper_writes_bundle": True},
        "final_gate": "Kyle TRANSPORT_ADMISSION names exact runner/test/schema/smoke/evidence hashes, job/node, limitations, expiry/revocation, and permitted packet types; every campaign still needs separate admission",
        "independent_review": {"required": ["all declared tests pass", "compile/Ruff pass", "no P0/P1/P2", "no unsupported pass", "no duplicate submit", "bounded observation", "no post-review byte change", "prior eight blocking classes closed"], "scope": ["runner", "focused tests", "request/state/component schemas", "embedded batch/remote scripts", "all consumers", "smoke builder"]},
        "local_state": {"atomic_replace": True, "cached_replay": "verify complete request and every terminal/component/artifact/cleanup byte; mismatch is STALE_LINEAGE", "create_if_absent": True, "exclusive_controller_lock": True, "fsync_file_and_parent": True, "generation_compare_and_swap": True},
        "program_id": PROGRAM_ID,
        "remote_protocol": {"directory": "new mode-0700 path from random attempt ID and request digest; never reused", "states": ["PREPARED", "DISPATCH_STARTED", "SUBMITTED", "TERMINAL", "COLLECTED", "CLEANED"], "submission": "durably consume live attempt; exclusive dispatch; sbatch at most once; one numeric receipt; response loss becomes observation-only", "terminal": "publish bound terminal metadata last after output/component/trap cleanup close and fsync", "fetch": "bounded unique .part, fsync, verify hash/size, atomic rename", "cleanup": "only after exact scheduler terminal; remove named residue; verify absence; write/fetch external tombstone"},
        "request": {"canonical_digest_required": True, "required_bindings": ["program/track/packet/revision/lineage", "attempt/nonce/command/target-run", "payload and runner digests", "component set", "timeout/cancel/observation policy", "local/remote roots and cleanup", "SSH alias/host-key and scheduler resources", "environment allowlist and tool versions", "admission/budget/source/review/dependencies", "evidence layout"], "schema": "TransportRequestV1"},
        "scheduler": {"active": "fresh machine-formatted squeue", "cancel": "idempotent scancel separate from cancel-observed; bounded observation until terminal", "exact_match": ["JobIDRaw", "name/comment/request", "partition/allocation/nodes/resources", "state/exit", "submit/start/end"], "terminal": "fresh machine-formatted sacct", "timeout": "absolute request deadline; late terminal never passes after timeout/cancel"},
        "schema_version": "bb.rl.phase5.durable_transport_contract.v2",
        "smoke": {"approval": "TRANSPORT_SMOKE_ADMISSION", "claim_boundary": "transport behavior for named hash/job only", "payload": "standard-library fixed nonce; no model/provider/credential/network/task/training/checkpoint/repository mutation", "resources": {"deadline_seconds": 300, "gpus": 0, "nodes": 1, "tasks": 1}, "steps": ["double-build/preflight", "submit once", "stop controller after receipt", "fresh controller resumes same state", "prove one root/job/sbatch", "observe and fetch exact outputs", "cleanup and tombstone", "prove one terminal job/no live job/no residue", "independent validate/review"]},
        "status": "blocked_pending_repair_review_smoke_and_admission",
    }


def evidence_taxonomy() -> dict[str, Any]:
    return {
        "claim_ladder": [
            "observation",
            "local_contract_pass",
            "qualification",
            "target_attempt_fact",
            "target_predicate_pass",
            "track_completion",
            "assurance_item_award",
            "assurance_1000",
            "internal_completion",
            "promotion",
            "external_acceptance",
        ],
        "edge_contract": {
            "axes_are_independent": True,
            "required_fields": [
                "edge_type",
                "lifecycle_state",
                "claim_scope",
                "track",
                "evidence_digest",
            ],
            "scope_upgrade_by_proximity_or_relabel_forbidden": True,
        },
        "edge_types": {
            "contradicts": {
                "meaning": "negative evidence against an exact claim; a current contradiction invalidates active support and dependent decisions",
                "positive_claim_support": False,
            },
            "depends_on": {
                "meaning": "structural prerequisite only; prerequisite presence never proves the dependent claim",
                "positive_claim_support": False,
            },
            "qualifies": {
                "meaning": "narrow foundation or runtime qualification; never establishes a target predicate, track completion, score, promotion, or external acceptance",
                "positive_claim_support": False,
            },
            "supports": {
                "meaning": "positive support for one exact named claim; consumable only while lifecycle_state is admitted_evidence and identity, freshness, review, and authority remain current",
                "positive_claim_support": True,
            },
        },
        "freshness": {
            "failed_later_rerun_invalidates": True,
            "predeclared_dual_scope_required": True,
            "retrospective_scope_upgrade_forbidden": True,
            "review_expires_on_load_bearing_change": True,
        },
        "lifecycle_states": {
            "admitted_evidence": {
                "consumable_for_pass": True,
                "meaning": "typed current edge to exact original bytes, limited to the named predicate",
            },
            "candidate_evidence": {
                "consumable_for_pass": False,
                "meaning": "awaiting current validation, review, and human admission",
            },
            "historical_failure": {
                "consumable_for_pass": False,
                "meaning": "failed or ambiguous diagnostic with explicit nonclaims",
            },
            "observation_only_reconciliation": {
                "consumable_for_pass": False,
                "meaning": "fresh facts about an old attempt; no submit or pass conversion",
            },
            "qualification_support": {
                "consumable_for_pass": False,
                "meaning": "narrow exact foundation or runtime fact",
            },
            "regression_fixture": {
                "consumable_for_pass": False,
                "meaning": "sanitized test behavior with no target or score authority",
            },
            "superseded": {
                "consumable_for_pass": False,
                "meaning": "new lineage replaces active use without deleting history",
            },
        },
        "nonclaim_pairing": "every edge stores unsupported adjacent claims; file proximity never upgrades scope",
        "program_id": PROGRAM_ID,
        "reuse_classes": {
            "attempt_scoped": [
                "scheduler/job/node",
                "components",
                "episodes/carriers/rewards",
                "optimizer/checkpoint/reload",
                "cleanup",
                "campaign review",
            ],
            "immutable_foundations": [
                "spec/catalog/schema",
                "source/blob/model/image manifests",
                "compiler fixtures",
            ],
            "qualification_only": [
                "transport smoke",
                "runtime discovery",
                "local/container probes",
                "historical training",
            ],
            "track_specific": [
                "track completion",
                "item award",
                "score",
                "internal completion",
                "checkpoint disposition",
                "promotion",
                "external acceptance",
            ],
        },
        "schema_version": "bb.rl.phase5.evidence_taxonomy.v4",
        "support_levels": {
            "L0": "untrusted assertion/scratch",
            "L1": "local deterministic contract",
            "L2": "reviewed local integration/container qualification",
            "L3": "exact target scheduler/runtime/component",
            "L4": "exact reward/training/checkpoint",
            "L5": "reviewed current evidence root with typed authority",
        },
    }


def authority_policy() -> dict[str, Any]:
    return {
        "bootstrap_spec_freeze": {"cannot_authorize": ["IBM action", "evidence admission", "score", "track completion", "checkpoint disposition", "promotion", "external acceptance"], "mechanism": "explicit Kyle approval in current human-in-the-loop session plus exact-hash Beads record", "ratification": "AT0 Ed25519 SPEC_FREEZE_RATIFICATION over identical roots before any target campaign", "scope": ["activate local v2 specification", "perform local migration", "create blocked backlog"]},
        "cryptographic_trust": {"algorithm": "Ed25519", "blocking_scope": ["IBM campaign", "budget execution", "evidence admission for score", "track completion", "Assurance score", "internal completion", "checkpoint disposition", "promotion", "revocation"], "provisioning_packet": "AT0 root-owned signing service, public trust manifest, deployment identity, target/training verifier keys, independent G2 review", "public_keys": [], "required_before_first_target_campaign": True, "state": "not_provisioned"},
        "decision_contract": {"private_keys_never_in_repo_or_evidence": True, "required_fields": ["decision ID/type", "actor/role", "scope", "subject digests", "dependency root", "issued/expiry", "limitations/nonclaims", "revocation", "signature or bounded bootstrap proof"], "reviewer_cannot_sign_human_authority": True, "scorer_cannot_sign": True, "self_authorization_forbidden": True, "worker_cannot_sign": True},
        "program_id": PROGRAM_ID,
        "roles": {"independent_integrity_reviewer": {"may_approve_execution": False}, "independent_spec_reviewer": {"may_approve_execution": False}, "kyle_internal_program_authority": {"actor": "Kyle McCleary", "may_issue": ["SPEC_FREEZE", "TRANSPORT_SMOKE_ADMISSION", "TRANSPORT_ADMISSION", "CAMPAIGN_ADMISSION", "BUDGET_REVISION", "HISTORICAL_EVIDENCE_ADMISSION", "RECONCILE_ONLY", "CLEANUP_ONLY", "TRAINING_PROOF_COMPLETION", "ASSURANCE_SCORE_DECISION", "INTERNAL_COMPLETION", "CHECKPOINT_DISPOSITION", "PROMOTION", "ROLLBACK", "REVOCATION"], "may_issue_external_acceptance": False}, "target_execution_signer": {"may_attest": ["TARGET_SLURM_COMMAND"], "may_score": False}, "target_training_signer": {"may_attest": ["TARGET_TRAINING_RUN"], "may_score": False}, "zyphra_external_acceptance_authority": {"actor": "unassigned", "state": "unclaimed"}},
        "schema_version": "bb.rl.phase5.authority_policy.v3",
        "separation": {"checkpoint_disposition_separate": True, "external_acceptance_separate_and_unclaimed": True, "internal_completion_separate": True, "promotion_separate": True, "score_decisions_per_item": True, "track_completion_has_no_score_promotion_external_fields": True},
    }


def packet_dispositions() -> dict[str, Any]:
    parent_scratch = PARENT_REPO / "docs_tmp/ZYPHRA/RL_PHASE_5/scratch_runs"
    canonical_scratch = EVIDENCE_ROOT / "scratch_runs"
    source_checks = [
        ("runner", PARENT_REPO / "scripts/rl_phase3/run_phase3_target_command.py", "sha256:3d734c843a4fc18263685d60c9e51f987c28e49e8795b702dd8dfabb6ef2a148"),
        ("runner_test", PARENT_REPO / "tests/rl/phase3/test_target_command_runner.py", "sha256:0e44d02ccee88fb788e1d4f987f8cf95da79d803496fe67fd78a67e783f6cffb"),
        ("f7_builder", PARENT_REPO / "scripts/rl_phase5/build_f7_target_launch_packet.py", "sha256:65b5a943aa4b0e364778edbae1bbe9fcf0229df1109a6f979f51afb4a4c776bb"),
        ("f7_workload", PARENT_REPO / "scripts/rl_phase5/run_f7_target_workload.py", "sha256:305a4237ab0cca0520a8123b0bd3da40855cb9ce618742e5c7065bf128bde764"),
        ("f7_test", PARENT_REPO / "tests/rl/phase5/test_f7_target_operations.py", "sha256:ed90cc78455586bc3cbb96140b9bd65d521f74fbcb2e5b29cdc9a29d7ee6fcde"),
        ("f7_gate", PARENT_REPO / "scripts/rl_phase5/run_f7_topology_gate.py", "sha256:6c971db43d9acf5cda1ca516001eee21edd86d19b7286ef53c9054967b9a51bd"),
        ("g2_external", PARENT_REPO / "breadboard/rl/phase5/external_proof.py", "sha256:a2c3ff52d4a54019db5be938d7252f802eba026da93144c7ed956db22e291740"),
        ("g2_server", PARENT_REPO / "breadboard/rl/phase5/server_authority.py", "sha256:3e5a2d69eb19f0d1074cf444cd138f3adbc2e29615d102c2374addb9e148f9d6"),
        ("g2_test", PARENT_REPO / "tests/rl/phase5/test_g2_g3_evidence_controls.py", "sha256:7cd37d13fa2e94e39691bec1f5e1a0ed6be60408ef005183aef88e3a71ffd3c9"),
        ("g4_store", PARENT_REPO / "breadboard/rl/phase5/rollback_store.py", "sha256:7259173a2f844500576be42ff8c3327a463ebd1575f28d8681e538650a20e1d6"),
        ("g4_test", PARENT_REPO / "tests/rl/phase5/test_rollback_store.py", "sha256:56907f399d94c698e12cbc544b572c292e30f2ee6516d0a70537d04a02fafdf9"),
    ]
    checked = {name: record(path, expected) for name, path, expected in source_checks}
    f2 = parent_scratch / "F2/r29_build_f2-final-dual-store-admission-20260715t174500z-r29"
    f3 = parent_scratch / "F3/r50"
    f4 = canonical_scratch / "F4"
    f5 = parent_scratch / "F5"
    f10 = canonical_scratch / "F10/target"
    objects = [
        {"allowed_next": "one remaining repair, full suite, review, smoke, smoke review, admission", "consumed_repair_rounds": 1, "disposition": "mid_repair_source", "id": "shared_transport_mid_repair", "nonclaims": ["reviewed", "verified", "IBM-ready"], "records": [checked["runner"], checked["runner_test"]]},
        {"allowed_next": "RECONCILE_ONLY; optional separate CLEANUP_ONLY", "disposition": "historical_failure", "id": "F2-r29", "nonclaims": ["F2 pass", "component/reward success", "cleanup complete", "retry"], "observed": {"cleanup": "unknown", "failure": "scheduler_observation_missing", "job_id": "282978", "node": "cnode-131", "scheduler_exit": "125", "wrapper_exit": 255}, "records": [record(f2 / "f2-final-dual-store-admission-20260715t174500z-r29.zip", "sha256:81dacf7a4f3c3206b41e47a9a3259fd5152d42e78639e1fd065cf1a42ee933f3"), record(f2 / "ibm-exec1/phase3_command_attempts_manifest.json"), record(f2 / "ibm-exec1/command_logs/f2-final-dual-store-admission-20260715t174500z-r29.log", "sha256:dcda317856799c50230cdd9d536db7d8b1adde7de9ee5284dc6dd324fd590a15")]},
        {"allowed_next": "packet-build regression only", "disposition": "qualification_support", "id": "F3-r50-local", "nonclaims": ["F3 pass", "target execution"], "records": [record(f3 / "f3-r-swe-prod-source-closed-20260716t001500z-r50-87c1d5ae.zip", "sha256:40a8d7c1bc76463abc23c1a9c7d3a7740585d2ec3f348d4ec5437caab5f125ac")]},
        {"allowed_next": "reviewed Kyle-approved exact observation-only reconciliation; never resubmit", "disposition": "ambiguous_historical_attempt", "id": "F3-r50-ibm", "nonclaims": ["F3 pass", "quality", "point", "cleanup", "retry"], "observed": {"cleanup": "unknown", "failure": "submission_outcome_unknown", "job_id": None, "node": None, "phase": "submit_started"}, "records": [record(f3 / "ibm_r50_target_result/command_logs/f3-r50-final-ibm-20260716t001500z-87c1d5ae.attempt.json"), record(f3 / "ibm_r50_target_result/command_logs/f3-r50-final-ibm-20260716t001500z-87c1d5ae.log", "sha256:b8ba4c95788b9dffd00db4d60f48155a38862529ce1185498b912ec05a431691")]},
        {"allowed_next": "RECONCILE_ONLY; optional separate CLEANUP_ONLY", "disposition": "historical_failure", "id": "F4-r9", "nonclaims": ["F4 pass", "six-config success", "cleanup", "retry"], "observed": {"cleanup": "unknown", "elapsed": "00:15:08", "job_id": "283087", "node": "cnode-49", "scheduler_state": "FAILED"}, "records": [record(f4 / "f4-six-config-production-20260714t1400z-r9.zip", "sha256:6d5e5dd1210f63b8b05d36221f934426033e75f10b890535cec8f793628842f9"), record(f4 / "target/f4-six-config-production-20260714t1400z-r9/phase3_command_attempts_manifest.json"), record(f4 / "target/f4-six-config-production-20260714t1400z-r9/command_logs/f4-six-config-production-20260714t1400z-r9.log", "sha256:911aaba3fa888abf066a6c11889decd763b5cab07413b6d64fa88af323dfd5dd")]},
        {"allowed_next": "reviewed regression extraction", "disposition": "historical_failure", "id": "F5-r12", "nonclaims": ["F5 pass", "target success", "retry"], "records": [record(f5 / "r12_build/build-a/f5-eight-fault-target-20260715t223000z-r12.zip"), record(f5 / "r12_build/terminal-blocker.json"), record(f5 / "r12_build/do1-raw-receipt.log")]},
        {"allowed_next": "reviewed regression extraction", "disposition": "historical_failure", "id": "F5-r13", "nonclaims": ["F5 pass", "target success", "retry"], "records": [record(f5 / "r13_build/build-a/f5-eight-fault-target-20260715t230000z-r13.zip", "sha256:087c0d0ef64c3e7c17dee9c85b976ae7a294494f3745b90746395edebbc0df06"), record(f5 / "r13_build/r13-seal-receipt.json")]},
        {"allowed_next": "regression extraction; rebuild new F5 after F4/F6/G4/E5", "disposition": "superseded_unadmitted_candidate", "id": "F5-r14", "nonclaims": ["campaign admission", "F5 pass", "submission"], "observed": {"target_invocations": 0}, "records": [record(f5 / "r14_build/build-a/f5-eight-fault-target-20260715t233000z-r14.zip", "sha256:acac5da87e9e7efab3ed050214b92dc8a13b3626179cf24dca5c6d70898279c4"), record(f5 / "r14_build/r14-seal-receipt.json")]},
        {"allowed_next": "port independently reviewed ideas into new F7 after AT6 and topology transport review", "disposition": "superseded_mid_repair_source", "id": "F7-mid-repair", "nonclaims": ["packet", "target", "topology", "soak", "performance", "point"], "records": [checked[name] for name in ("f7_builder", "f7_workload", "f7_test", "f7_gate")]},
        {"allowed_next": "rerun readiness; close only from exact F8 target report", "disposition": "qualification_support", "id": "F9-local-truth-gate", "nonclaims": ["F9 point", "PPO estimator", "target proof"], "records": []},
        {"allowed_next": "current validators, exact review, freshness, Kyle HISTORICAL_EVIDENCE_ADMISSION limited to F10", "disposition": "candidate_evidence", "id": "F10-target-records", "nonclaims": ["gVisor", "multitenancy", "production readiness", "score authority", "promotion"], "observed": {"cases": "2/2 gold pass; 2/2 bad reject; 2/2 no-op reject", "jobs": ["272565", "275014"], "node": "cnode-12", "runsc": "absent/unregistered"}, "records": [record(f10 / "f10-runsc-decision-20260714t022839z/f10-runsc-decision-20260714t022839z/f10-runsc-decision-20260714t022839z.json"), record(f10 / "f10-source-closed-20260714t153349z/rl_phase5_f10_isolation_decision/f10-isolation-decision-20260714T153349Z-slurm-275014.json"), record(f10 / "f10-source-closed-20260714t153349z/phase3_command_log_manifest.json")]},
        {"allowed_next": "one bounded repair after AT0; preflight before transport and final real-record attack later", "disposition": "superseded_or_rebased_draft", "id": "G2-authority-drafts", "nonclaims": ["G2 pass", "admitted authority", "score"], "records": [checked[name] for name in ("g2_external", "g2_server", "g2_test")]},
        {"allowed_next": "author from v2 current manifests after AT4", "disposition": "no_current_packet_or_evidence", "id": "G3", "nonclaims": ["G3 pass", "score"], "records": []},
        {"allowed_next": "integrated current drill plus fresh review", "disposition": "qualification_support", "id": "G4-local-review", "nonclaims": ["G4 point", "final integrated proof", "promotion"], "records": [checked["g4_store"], checked["g4_test"]]},
        {"allowed_next": "archive/history only", "disposition": "read_only_superseded_lineage", "id": "v1-program-state", "nonclaims": ["v2 admission", "current evidence", "score", "completion", "promotion"], "records": [{"path": "execution/ACTIVE_STATUS.json", "sha256": V1_ACTIVE_SHA256}, {"path": "execution/SCORECARD.json", "sha256": V1_SCORECARD_SHA256}]},
    ]
    return {"approval_matrix": {"award": "current proof-floor evidence, review, Kyle score decision", "cleanup_old": "exact terminal/job/path and Kyle CLEANUP_ONLY", "fixture": "owner, provenance/secret scan, nonauthority review", "historical_evidence": "current validators, exact review, identity/freshness, Kyle admission", "read_historical": "read-only", "reconcile": "reviewed recovery plus Kyle RECONCILE_ONLY; zero submit/upload/new attempt", "repair": "claimed v2 packet, frozen inputs, remaining budget, review", "submit": "current dependencies, transport, immutable packet/budget/review, Kyle admission"}, "objects": objects, "policies": {"new_execution_requires_new_packet_revision_budget_review_transport_and_human_admission": True, "observation_only_never_passes": True, "preserve_bytes": True, "retry_old_identity": False}, "program_id": PROGRAM_ID, "schema_version": "bb.rl.phase5.packet_dispositions.v2"}


def beads_migration(
    children: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    catalog: dict[str, Any],
    graph: dict[str, Any],
) -> dict[str, Any]:
    row_packet = {
        row: node["id"] for node in graph["nodes"] for row in node["score_rows"]
    }
    legacy_packets = {
        50: ["AT0"],
        51: ["AT1"],
        52: ["AT1"],
        53: ["AT2"],
        54: ["AT2"],
        55: ["AT3", "AT4"],
        56: ["AT3"],
        57: ["AT3"],
        58: ["AT3", "AT4"],
        59: ["AT4"],
        60: ["AT4"],
        61: ["AT4"],
        62: ["AT6_F3"],
        63: ["AT6_F1_D5", "AT6_F2", "AT6_F3", "AT6_F4", "AT6_F6", "AT6_F5"],
        64: ["AT7_F7_TWO_NODE", "AT7_F7_FOUR_NODE"],
        65: ["AT7_F7_TWO_NODE", "AT7_F7_FOUR_NODE", "AT7_F8_F9"],
        66: ["AT5_F10", "AT5_G4"],
        67: ["AT8_G2_G3", "AT8_H3", "AT8_H1", "AT8_H2", "AT8_H4"],
    }
    by_issue = {item["legacy_issue_id"]: item for item in catalog["items"]}
    mappings = []
    for issue in children:
        number = int(issue["id"].split(".")[1])
        item = by_issue.get(issue["id"])
        successors = [row_packet[item["item_id"]]] if item else legacy_packets[number]
        mappings.append(
            {
                "close_reason": issue.get("close_reason"),
                "dependency_ids": sorted(
                    edge["depends_on_id"]
                    for edge in issue.get("dependencies", [])
                    if edge.get("type") != "parent-child"
                ),
                "disposition": (
                    "historical_issue_closed_no_score_carry"
                    if item
                    else "historical_completed_implementation_candidate"
                )
                if issue["status"] == "closed"
                else "superseded_not_completed",
                "legacy_issue_id": issue["id"],
                "score_item_id": item["item_id"] if item else None,
                "status": issue["status"],
                "successor_issue_resolution": "created after SPEC_FREEZE in derived BEADS_RESOLUTION.json",
                "successor_packet_keys": successors,
                "title": issue["title"],
            }
        )
    counts: dict[str, int] = {}
    for issue in children:
        counts[issue["status"]] = counts.get(issue["status"], 0) + 1
    return {
        "cutover_rules": [
            "resolve stable packet keys to actual issue IDs after SPEC_FREEZE",
            "validate mapping/dependencies before superseding legacy",
            "leave closed legacy issues closed",
            "close open/in-progress as SUPERSEDED BY V2 — NOT COMPLETED",
            "close bb-auh as superseded after all 67 map",
            "Beads cannot admit evidence, consume attempts, award score, or grant authority",
        ],
        "freeze_request_issue_id": "bb-6d4.9",
        "legacy_parent": {
            "child_count": 67,
            "issue_id": "bb-auh",
            "snapshot_scope": "canonical JSON of the 67 legacy child issue records sorted by numeric suffix",
            "snapshot_sha256": sha256_bytes(canonical_bytes(children)),
            "status_counts": counts,
        },
        "legacy_snapshot": children,
        "map_decision_snapshot": decisions,
        "map_decision_snapshot_sha256": sha256_bytes(canonical_bytes(decisions)),
        "map_decisions": [
            {
                "issue_id": issue["id"],
                "record_sha256": sha256_bytes(canonical_bytes(issue)),
            }
            for issue in decisions
        ],
        "mappings": mappings,
        "program_id": PROGRAM_ID,
        "schema_version": "bb.rl.phase5.beads_migration.v3",
        "successor_epic": {
            "creation": "after SPEC_FREEZE; children blocked by machine dependencies",
            "stable_key": "PHASE5_V2_EXECUTION",
            "title": "Execute the Two-Track Zyphra RL Phase 5 Program",
        },
        "successor_packet_keys": [
            {
                "depends_on": node["depends_on"],
                "key": node["id"],
                "kind": node["kind"],
            }
            for node in graph["nodes"]
        ],
    }


def initial_state(catalog: dict[str, Any], graph: dict[str, Any], dispositions: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    rows = []
    for obj in dispositions["objects"]:
        for item in obj.get("records", []):
            digest = item.get("sha256")
            if not digest:
                continue
            lifecycle_state = (
                "candidate_evidence"
                if obj["disposition"] == "candidate_evidence"
                else "qualification_support"
                if "qualification" in obj["disposition"]
                else "historical_failure"
                if "failure" in obj["disposition"] or "ambiguous" in obj["disposition"]
                else "superseded"
            )
            edge_type = {
                "candidate_evidence": "supports",
                "qualification_support": "qualifies",
                "historical_failure": "contradicts",
                "superseded": "depends_on",
            }[lifecycle_state]
            rows.append(
                {
                    "active": False,
                    "admitted": False,
                    "approval_refs": [],
                    "claim_scope": {
                        "nonclaims": obj.get("nonclaims", []),
                        "supported_claim": None,
                    },
                    "cleanup_state": obj.get("observed", {}).get(
                        "cleanup", "not_applicable_or_unresolved"
                    ),
                    "disposition": obj["disposition"],
                    "edge_type": edge_type,
                    "evidence_digest": digest,
                    "evidence_id": "ev:" + digest.removeprefix("sha256:"),
                    "freshness": "not_evaluated_for_v2",
                    "lifecycle_state": lifecycle_state,
                    "packet_or_object_id": obj["id"],
                    "path": item.get("path"),
                    "review_refs": [],
                    "size": item.get("size"),
                    "support_level": "L1",
                    "track": "unassigned_pre_admission",
                }
            )
    index = {"active_relations": 0, "generation": 0, "policies": {"failed_rerun_transitively_invalidates": True, "final_H1_copies_admitted_bytes_to_self_contained_CAS": True, "index_presence_is_not_admission": True, "one_active_relation_per_claim_scope": True, "scratch_remains_noncanonical": True}, "program_id": PROGRAM_ID, "rows": sorted(rows, key=lambda row: row["evidence_id"]), "schema_version": "bb.rl.phase5.evidence_index.v3", "state": "draft_waiting_spec_freeze"}
    queue = {"blocked": [{"depends_on": node["depends_on"], "packet_key": node["id"], "reason": "v2 inactive" if node["id"] == "AT0" else "dependency not current"} for node in graph["nodes"]], "eligible": [], "escalated": [], "generation": 0, "program_id": PROGRAM_ID, "schema_version": "bb.rl.phase5.run_queue.v2", "state": "DRAFT_WAITING_SPEC_FREEZE", "target_lease": None, "waiting_external": [], "waiting_human": [{"packet_key": "V2_ACTIVATION", "reason": "SPEC_FREEZE not issued", "wake_condition": "Kyle approves the reviewed immutable candidate for local-only migration"}]}
    status = {"active": False, "active_attempt": None, "active_packet": None, "allowed_next": "independent rc5 candidate reviews then a new exact-revision Kyle SPEC_FREEZE", "candidate_authority": {"prior_rc4_spec_freeze_applies": False, "required": "new exact rc5 SPEC_FREEZE", "superseded_artifact_manifest_sha256": SUPERSEDED_ARTIFACT_MANIFEST_SHA256, "superseded_revision_id": SUPERSEDED_REVISION_ID}, "checkpoint_disposition": "unclaimed", "event_cursor": 0, "external_acceptance": {"authority": "Zyphra only", "state": "unclaimed"}, "generation": 0, "historical_unresolved": {"F2_r29_cleanup": "unknown", "F3_r50_cleanup": "unknown", "F3_r50_submission": "unknown", "F4_r9_cleanup": "unknown"}, "internal_completion": False, "nonclaims": ["not active v2 state", "no IBM admission", "no score", "no track completion", "no promotion", "no external acceptance", "rc4 SPEC_FREEZE grants no rc5 authority", "quiescence, preparation, migration replay, and selector cutover grant no target execution or score authority"], "program_id": PROGRAM_ID, "program_state": "DRAFT_WAITING_RC5_SPEC_FREEZE", "promotion": {"authorized": False, "state": "unclaimed"}, "revision_id": REVISION_ID, "schema_version": "bb.rl.phase5.active_status.v4", "shared_transport": {"admitted_hash": None, "smoke_job": None, "state": "blocked"}, "target_lease": None, "tracks": {"assurance": {"awarded_items": [], "catalog_points": 1000, "current_verified_points": 0, "evidence_ref_count": 0, "item_count": 49, "pending_items": [item["item_id"] for item in catalog["items"]], "review_ref_count": 0, "state": "PENDING_AT0"}, "training_proof": {"completion_decision": None, "evidence_root": None, "satisfied": False, "score_field_present": False, "state": "BLOCKED_SHARED_TRANSPORT"}}}
    return index, queue, status


def quiescence_contract() -> dict[str, Any]:
    process_fields = [
        "kind",
        "adapter",
        "pid",
        "ppid",
        "pgid",
        "sid",
        "uid",
        "start_identity",
        "executable",
        "argv_sha256",
        "cwd",
        "root_or_datadir",
        "discovered_at",
        "identity_revalidated_at",
        "stop_method",
        "stopped_at",
        "exit_status",
    ]
    receipt_fields = [
        "migration_id",
        "supervisor_identity",
        "platform_adapter",
        "lease",
        "journal",
        "breadboard_processes",
        "bd_dolt_processes",
        "omp_rpc_session",
        "closed_transcript",
        "child_transcript_manifest",
        "prior_todo_projection",
        "dolt_adapter",
        "dolt_snapshot",
        "filesystem_roots",
        "descriptor_scans",
        "inventory_sha256",
        "quiesced_at",
        "result",
    ]
    return {
        "adapter_discovery": {
            "allowed_dolt_adapters": ["embedded_dolt_cli", "sql_server"],
            "execution_boundary": "run discovery only after the OMP session is closed and under the spawn-frozen supervisor; bd context or SQL may connect to or start Dolt, so inventory every discovery child, stop and reap it after capture, and never describe discovery as a pure read-only preflight",
            "embedded_dolt_cli": {
                "discovery": "bd context --json must report direct or embedded mode and a database path; resolve the actual Dolt repository below .beads/embeddeddolt/<database>",
                "head": "run the installed dolt CLI in the resolved repository and capture the full commit and root from dolt log, never a truncated display value",
                "status": "run the installed dolt CLI in the resolved repository and prove clean working and staged roots",
                "transaction": "run one native transaction using the direct dolt sql CLI in the resolved repository, then one Dolt commit",
                "unsupported": "bd sql is not used or claimed to work in embedded/direct mode",
            },
            "fail_closed": "unknown, unsupported, conflicting, or ambiguous mode, repository, database, branch, socket, DSN, or adapter discovery makes migration non-executable",
            "runtime_evidence": ["exact bd context output", "bd version", "dolt version", "resolved store and repository paths", "database and branch", "adapter selection rationale"],
            "sql_server": {
                "discovery": "bd context plus process and descriptor scans must identify an actual server endpoint and bind its socket or DSN before any bd sql operation is legal",
                "head": 'bd sql "SELECT commit_hash FROM dolt_log ORDER BY date DESC LIMIT 1"',
                "status": 'bd sql "SELECT table_name, staged, status FROM dolt_status"',
                "transaction": "one native server transaction followed by one DOLT_COMMIT",
            },
        },
        "client_behavior": {
            "claim": "clients are paused outside the migration window and restarted only by the supervisor",
            "domain_error_claimed": False,
            "forbidden_claim": "ordinary readers receive MIGRATION_IN_PROGRESS",
            "new_clients": "the out-of-band supervisor freezes intake and refuses or stops new BreadBoard, bd/Dolt, and OMP/RPC clients while the lease is held",
        },
        "descriptor_discovery": {
            "darwin": {
                "commands": ["ps -axo pid,ppid,pgid,sid,lstart,command", "proc_pidinfo or an equivalent kernel birth-identity query for every pid", "lsof -nP -p <pid> for every identified pid", "lsof -nP +D <program_root>", "lsof -nP +D <beads_data_directory>"],
                "coverage": "bind kernel-derived process birth identity plus every per-process file, directory, pipe, and socket and every escaped root holder by device, inode, and resolved target",
            },
            "linux": {
                "commands": ["ps -eo pid=,ppid=,pgid=,sid=,lstart=,args=", "read /proc/<pid>/stat starttime as birth identity", "scan /proc/<pid>/fd resolved symlink targets for both roots and every identified pid"],
                "coverage": "bind kernel starttime and every matching process and descriptor target by device, inode, and resolved path",
            },
            "fail_closed": "unsupported OS, permission error, incomplete scan, unknown descriptor, new identity, PID reuse, or changed device/inode/path/socket target fails quiescence",
            "limits": "process and descriptor scans are racy snapshots; they are meaningful only with supervisor-owned process trees and the intake/spawn freeze",
        },
        "lease_contract": {
            "acquire": "provision the stable lock path once with O_CREAT|O_EXCL, nofollow validation, and file/parent fsync if absent; every migration then opens the existing verified inode without following symlinks and continuously holds an exclusive advisory OS flock on that file descriptor",
            "durability": "initialize and fsync the migration journal before spawn/intake freeze, then append and fsync intent/applied/verified records",
            "identity": "bind migration_id, supervisor pid/pgid/start identity, OS, program root device/inode, Beads store root device/inode, lock device/inode, and adapter",
            "release": "while the flock is held, emit and fsync an immutable release-intent receipt; then unlock and close the held file descriptor and emit a distinct immutable post-release receipt that binds the intent and completed release facts; retain the stable lock inode and journal",
            "scope_limit": "the advisory lock cannot stop escaped processes, so repeated process/descriptor identity scans remain mandatory",
        },
        "mode": "out_of_band_supervisor_owned_stop_the_world",
        "native_observations": {
            "beads_dolt": "after ordinary access stops, the supervisor alone runtime-discovers exactly one supported native adapter, captures a clean full Dolt HEAD/root, staged root, working root, schema hash, and canonical logical-row hash, then stops and reaps every observation child before declaring quiescence",
            "omp_rpc": {
                "close_sequence": [
                    "record RPC get_state, active session identity, transcript cursor/final event, and child transcript inventory",
                    "freeze OMP intake and export the prior todo projection bound to that transcript cursor",
                    "send RPC abort if isStreaming is true and wait until get_state reports isStreaming false",
                    "accept either a native close/flush acknowledgement or graceful process exit after idle; RPC shutdown is not claimed",
                    "wait for and reap graceful OMP/RPC process-tree exit, then prove zero open descriptors to every transcript path",
                    "the supervisor fsyncs the session JSONL file and parent directory, proves raw bytes and size stable across two post-exit observations, and hashes the process-closed transcript",
                    "a forced or timeout kill is recorded only in a failed quiescence receipt and prohibits prepare or commit",
                ],
                "limits": "RPC exposes abort and get_state but no shutdown, and the OMP writer does not itself prove fsync; successful closure requires native_ack or graceful_process_exit plus supervisor fsync, stable bytes/size, and zero-open-FD proof, and remains a process-closed snapshot rather than an OMP durable-finalization guarantee",
                "transcript_identity": "bind the fixed-width title-slot bytes and SHA-256 separately, then locate and validate the unique type=session header after that slot to bind session_id and cwd; also bind the exact raw transcript bytes and byte size, final cursor/event, child transcript manifest, and raw final nonempty JSONL-record SHA-256",
            },
        },
        "nonclaims": [
            "quiescence grants no SPEC_FREEZE, selector, target, score, checkpoint, item, completion, or promotion authority",
            "a stopped client received a domain error",
            "process enumeration or lsof alone proves quiescence",
            "a truncated displayed Dolt commit is the full HEAD",
            "a server-backed adapter exists unless runtime discovery proves its endpoint",
            "a forced or timeout-killed OMP/RPC process produced a successful quiescence receipt",
        ],
        "ordered_protocol": [
            "an out-of-band supervisor provisions or verifies the stable lock inode, obtains its exclusive flock, initializes the durable journal, then freezes new spawn and intake before any authoritative observation",
            "enumerate BreadBoard, bd/Dolt, OMP/RPC, and child process trees with kernel-derived birth identity and initial per-process plus root descriptor coverage",
            "drain BreadBoard work, freeze the transcript cursor, and export the prior todo projection bound to that cursor",
            "abort any streaming OMP turn and wait for non-streaming state; require native close/flush acknowledgement or graceful process-tree exit, then prove zero transcript FDs, supervisor-fsync, and stable raw bytes/size before hashing; any forced or timeout kill makes quiescence false and prohibits commit",
            "stop remaining BreadBoard and bd/Dolt trees, revalidating full identity immediately before every signal and after every wait to defeat PID reuse",
            "runtime-discover exactly one Dolt adapter and capture exact clean native HEAD/root, staged/working roots, schema, and canonical logical rows",
            "stop and reap every adapter-discovery or native-observation child and repeat the identity and descriptor scans before declaring the store quiescent",
            "repeat process, identity, descriptor, root, socket, and Dolt scans; any denied, unknown, changed, reopened, or ambiguous state fails closed",
            "seal one immutable acquisition/quiescence receipt while the lock remains held",
            "hold the lease and spawn freeze through prepare, commit or rollback, migration replay, zero-authority verification, and immutable release-intent receipt; unlock and close, then emit the post-release receipt before any fresh session",
        ],
        "program_id": PROGRAM_ID,
        "receipt_contract": {
            "additional_fields_allowed": False,
            "acquisition_receipt_invariants": ["lease is held", "released_at is not a field", "receipt bytes never change after hashing", "result pass requires native_ack or graceful_process_exit", "forced_or_timeout_kill requires result fail and commit_prohibited true"],
            "child_transcript_fields": ["session_id", "parent_session_id", "path", "size", "sha256", "final_cursor"],
            "closed_transcript_fields": ["session_id", "cwd", "path", "size", "sha256", "title_slot_size", "title_slot_sha256", "session_header_sha256", "final_cursor", "final_event_sha256", "final_nonempty_record_sha256", "flush_outcome", "supervisor_fsynced_file", "supervisor_fsynced_parent", "stability_observations_sha256", "open_fd_count", "closed_after_process_exit", "snapshot_kind"],
            "descriptor_scan_fields": ["platform_adapter", "backend", "root", "started_at", "completed_at", "process_snapshot_sha256", "targets", "coverage", "permission_errors", "result"],
            "descriptor_target_fields": ["pid", "start_identity", "descriptor", "kind", "device", "inode", "resolved_path", "socket_target"],
            "dolt_adapter_fields": ["adapter_kind", "discovery_evidence_sha256", "bd_version", "dolt_version", "mode", "store_root", "repository_path", "database", "branch", "server_socket_or_dsn"],
            "dolt_snapshot_fields": ["adapter_kind", "database", "branch", "store_root", "repository_path", "head_commit", "head_root", "staged_root", "working_root", "status_sha256", "schema_sha256", "canonical_rows_sha256", "clean"],
            "filesystem_root_fields": ["kind", "path", "device", "inode", "mode"],
            "journal_fields": ["path", "device", "inode", "opened_at", "sha256", "fsynced_through_sequence"],
            "lease_fields": ["lease_id", "migration_id", "path", "device", "inode", "holder_pid", "holder_pgid", "holder_start_identity", "os", "adapter_kind", "program_root", "beads_data_directory", "provisioned_with_exclusive_create", "flock_held", "acquired_at"],
            "omp_rpc_session_fields": ["session_id", "pid", "ppid", "pgid", "sid", "uid", "start_identity", "cwd", "state_before_abort", "state_after_abort", "abort_sent", "flush_outcome", "forced_or_timeout_kill", "process_exit_status", "reaped", "commit_prohibited"],
            "prior_todo_projection_fields": ["source", "path", "size", "sha256", "transcript_cursor", "transcript_event_sha256", "captured_at", "cache_authority"],
            "process_entry_fields": process_fields,
            "required_fields": receipt_fields,
            "supervisor_identity_fields": ["pid", "ppid", "pgid", "sid", "uid", "start_identity", "executable", "argv_sha256", "os"],
            "flush_outcome_rules": {"failure_only": {"allowed": ["forced_or_timeout_kill_without_flush"], "invariants": ["result is fail", "forced_or_timeout_kill is true", "commit_prohibited is true"]}, "success_only": {"allowed": ["native_ack", "graceful_process_exit"], "invariants": ["result is pass", "forced_or_timeout_kill is false", "commit_prohibited is false"]}},
            "release_receipt_contracts": {
                "post_release_receipt": {
                    "additional_fields_allowed": False,
                    "required_fields": ["migration_id", "release_intent_receipt_sha256", "lease_id", "lease_device", "lease_inode", "flock_released_at", "file_descriptor_closed", "post_release_journal_sha256", "receipt_sha256"],
                    "receipt_sha256_projection": "SHA-256 of canonical post-release receipt with receipt_sha256 omitted",
                },
                "release_intent_receipt": {
                    "additional_fields_allowed": False,
                    "required_fields": ["migration_id", "acquisition_receipt_sha256", "lease_id", "lease_device", "lease_inode", "journal_final_sequence", "journal_sha256", "verified_stores_sha256", "migration_replay_sha256", "zero_authority", "release_intent_at", "flock_held", "receipt_sha256"],
                    "receipt_sha256_projection": "SHA-256 of canonical release-intent receipt with receipt_sha256 omitted",
                },
            },
        },
        "revision_id": REVISION_ID,
        "schema_version": "bb.rl.phase5.quiescence_contract.v1",
    }


def session_handoff_contract() -> dict[str, Any]:
    pre_fields = [
        "migration_id",
        "quiescence_receipt_sha256",
        "prior_session_id",
        "prior_session_cwd",
        "closed_transcript_path",
        "closed_transcript_sha256",
        "closed_transcript_size",
        "closed_transcript_title_slot_sha256",
        "closed_transcript_session_header_sha256",
        "closed_transcript_final_cursor",
        "closed_transcript_final_event_sha256",
        "child_transcript_manifest_sha256",
        "prior_todo_projection_sha256",
        "prior_todo_projection_size",
        "prior_todo_projection_cursor",
        "frozen_program_inputs",
        "derived_handoff",
        "created_at",
        "receipt_sha256",
    ]
    post_fields = [
        "migration_id",
        "handoff_kind",
        "pre_handoff_receipt_sha256",
        "new_session_id",
        "new_session_cwd",
        "new_session_transcript_path",
        "new_session_header_sha256",
        "parent_session_id",
        "started_at",
        "consumed_input_hashes",
        "quiescence_post_release_receipt_sha256",
        "selector_receipt_sha256",
        "event_receipt_sha256",
        "dolt_receipt_sha256",
        "derived_action",
        "execution_frontier",
        "capabilities",
        "active_authority",
        "score_authority",
        "checkpoint_authority",
        "target_execution_allowed",
        "ambient_inputs_used",
        "receipt_sha256",
    ]
    return {
        "handoff_model": {
            "after_commit": "after committed stores, replay, zero-authority verification, release intent, completed lease release, and immutable post-release receipt, start one distinct fresh OMP/RPC session from the immutable pre-handoff and post-release receipts plus committed selector, event, and Dolt receipt hashes",
            "before_commit": "convert the process-closed transcript snapshot plus the cursor-bound prior todo projection into an immutable pre-handoff receipt",
            "rollback": "after selector restoration, one-transaction Beads restoration, event compensation, rollback replay, zero-authority verification, release intent, completed release, and post-release receipt, start a distinct fresh session from the same prior pre-handoff receipt with rollback selector, event, and Dolt receipt hashes",
            "session_store_role": "typed pre/post handoff outside transaction stores; never an in-place queue/todo commit store",
        },
        "nonclaims": [
            "a transcript, todo projection, pre-handoff receipt, or new session grants execution or score authority",
            "a closed session is resumed or mutated",
            "OMP memory, chat context, cache state, or todo state is a transaction store or authority source",
        ],
        "pre_handoff_receipt": {
            "additional_fields_allowed": False,
            "receipt_sha256_projection": "SHA-256 of the canonical pre-handoff receipt with receipt_sha256 omitted",
            "derived_handoff_fields": ["program_state", "allowed_next", "execution_frontier", "capabilities", "active_authority", "score_authority", "checkpoint_authority", "target_execution_allowed", "nonclaims"],
            "frozen_program_input_fields": ["path", "sha256", "size"],
            "required_fields": pre_fields,
        },
        "post_handoff_receipt": {
            "additional_fields_allowed": False,
            "receipt_sha256_projection": "SHA-256 of the canonical post-handoff receipt with receipt_sha256 omitted",
            "allowed_handoff_kinds": ["committed_cutover", "rolled_back"],
            "invariants": ["new_session_id differs from prior_session_id", "capabilities is empty", "active_authority is false", "score_authority is false", "checkpoint_authority is false", "target_execution_allowed is false", "ambient_inputs_used is empty"],
            "required_fields": post_fields,
        },
        "program_id": PROGRAM_ID,
        "protocol": [
            "validate the quiescence receipt and bind its process-closed transcript, child transcript manifest, and cursor-proven prior todo projection into the pre-handoff receipt",
            "derive the handoff only from receipt-bound frozen program inputs; do not import live queue/todos, cache, agent memory, chat history, or a prior process",
            "after commit or rollback validation, emit and fsync the immutable release-intent receipt while the flock is held",
            "release the flock and close its file descriptor, then emit and validate the distinct immutable post-release receipt before creating any new OMP/RPC process",
            "create a new session id distinct from the old id and bind its distinct JSONL transcript to the post-release receipt plus applicable selector, event, and Dolt receipt hashes",
        ],
        "revision_id": REVISION_ID,
        "schema_version": "bb.rl.phase5.session_handoff_contract.v1",
    }


def migration_replay_contract() -> dict[str, Any]:
    allowed_inputs = [
        "ARTIFACT_MANIFEST.json",
        "MIGRATION_PLAN.json",
        "MIGRATION_TRANSACTION.json",
        "QUIESCENCE_CONTRACT.json",
        "SESSION_HANDOFF_CONTRACT.json",
        "QUIESCENCE_RECEIPT.json",
        "SESSION_PRE_HANDOFF_RECEIPT.json",
        "MIGRATION_JOURNAL.jsonl",
        "captured before-images and prepared or committed after-image receipts available before replay",
        "rollback store receipts written before rollback replay",
    ]
    output_fields = [
        "migration_id",
        "mode",
        "input_hashes",
        "quiescence_valid",
        "adapter_kind",
        "journal_valid",
        "commit_order",
        "store_results",
        "selector_committed_last",
        "rollback_results",
        "session_handoff_result",
        "zero_authority",
        "semantic_sha256",
    ]
    return {
        "allowed_inputs": allowed_inputs,
        "crash_recovery": {
            "fixtures": "inject a crash before and after every journal intent, native store durability boundary, applied record, verification, compensation, handoff, and lease-release boundary",
            "idempotency": "recovery identifies each step by migration_id, store_id, operation_id, before digest, intended after digest, and journal sequence; it verifies already-applied effects before continuing and never applies a logical operation twice",
            "journal": "append-only intent/applied/verified records, each hash-linked and fsynced with its parent directory when created; recovery fails closed on a gap, fork, partial record, unknown effect, or digest mismatch",
        },
        "distinct_from": {
            "contract": "FRESH_WORKER_HANDOFF_CONTRACT.json",
            "rule": "the frozen program replay derives the next nonexecuting program action; this contract replays durable migration, compensation, and crash-recovery mechanics only",
        },
        "isolation": {
            "ambient_inputs_forbidden": ["live stores", "live session state", "chat history", "agent memory", "scratch evidence", "target state", "score state"],
            "cwd": "new empty temporary directory",
            "environment": "allowlist only",
            "minimum_processes": 2,
        },
        "nonclaims": [
            "migration replay is not frozen program replay",
            "replay success grants no SPEC_FREEZE, selector, target, score, checkpoint, item, completion, or promotion authority",
            "replay proves a physically atomic cross-store transaction",
        ],
        "program_id": PROGRAM_ID,
        "receipt_contract": {
            "additional_fields_allowed": False,
            "each_worker_fields": ["pid", "input_hashes", "output", "semantic_sha256", "ambient_inputs_used"],
            "journal_step_fields": ["sequence", "migration_id", "operation_id", "store_id", "phase", "intent_sha256", "effect_sha256", "previous_record_sha256", "record_sha256", "fsynced"],
            "required_fields": ["migration_id", "contract_sha256", "worker_count", "worker_semantic_sha256", "workers", "crash_fixture_results", "result"],
            "store_result_fields": ["store_id", "before_presence", "before_sha256", "after_sha256", "adapter_kind", "commit_valid", "rollback_valid", "journal_sequences"],
            "worker_output_fields": output_fields,
        },
        "replay_requirements": [
            "validate every allowed pre-replay input digest and journal link without opening a live store",
            "replay the exact three-store commit with root selector last through the pre-replay receipt-selected embedded_dolt_cli or sql_server adapter semantics",
            "accept an absent event-log before-state only when presence is absent, event count is zero, predecessor is null, parent identity is bound, and exclusive genesis creation plus absence recheck is recorded",
            "replay rollback from store receipts written before replay, then derive the expected fresh-session handoff semantics from the prior pre-handoff contract without consuming release, post-release, post-handoff, or transaction receipts",
            "verify every crash-boundary fixture reaches the unique committed or compensated logical result idempotently",
            "require byte-identical semantic outputs from two isolated processes",
        ],
        "revision_id": REVISION_ID,
        "schema_version": "bb.rl.phase5.migration_replay_contract.v1",
    }


def migration_plan(source_entry_count: int) -> dict[str, Any]:
    return {
        "actions": [
            "stage immutable rc5 beside untouched rc4, rc3, and the sealed v1 archive",
            "the out-of-band supervisor provisions or verifies the stable lock inode, acquires its exclusive flock, initializes the durable journal, and freezes spawn/intake",
            "execute QUIESCENCE_CONTRACT.json: enumerate and stop all BreadBoard readers/writers, bd/Dolt access, and the active OMP/RPC session",
            "flush and hash the process-closed OMP transcript, capture the prior todo projection, and prove a clean native Beads/Dolt HEAD through the discovered adapter",
            "capture exact transaction before-images; the event log may honestly be absent",
            "prepare and validate every after-image and the typed pre-session handoff without changing the active selector",
            "execute MIGRATION_TRANSACTION.json across exactly three stores with root ACTIVE_STATUS last",
            "run MIGRATION_REPLAY_CONTRACT.json and the distinct frozen-program replay while all clients remain stopped",
            "verify zero authority, emit the immutable release-intent receipt while held, release the lease, emit the post-release receipt, then start a distinct fresh OMP/RPC session from SESSION_HANDOFF_CONTRACT.json",
            "remove old unversioned duplicates only after traversal/no-fallback pass",
        ],
        "fresh_worker_contract": "FRESH_WORKER_HANDOFF_CONTRACT.json",
        "migration_replay_contract": "MIGRATION_REPLAY_CONTRACT.json",
        "mode": "supervisor_owned_stop_the_world_compensating_cutover",
        "nonclaims": [
            "the prior rc4 SPEC_FREEZE grants no rc5 authority",
            "rc5 SPEC_FREEZE grants no IBM execution",
            "quiescence, preparation, migration replay, session handoff, and selector cutover grant no item award, score, checkpoint, target, completion, or promotion authority",
            "legacy closure is supersession",
            "no paused campaign resumes",
            "cross-store cutover is not physically atomic",
            "clients are paused and are not claimed to receive MIGRATION_IN_PROGRESS",
        ],
        "post_cutover": {
            "execution_frontier": ["AT0"],
            "nonexecuting_preparation": [
                "author a new SHARED_TRANSPORT repair packet without submission authority"
            ],
            "program_state": "READY_FOR_LOCAL_MIGRATION_WORK",
            "target_execution_allowed": False,
        },
        "preconditions": [
            "the rc5 artifact manifest binds the exact superseded rc4 artifact manifest digest",
            "archive/verify v1 with actual read-only file modes",
            "double-build candidate",
            "validate catalog/DAG/mappings/source/index/authority",
            "two independent reviews",
            "new Kyle local-only SPEC_FREEZE bound to the exact rc5 candidate; the rc4 decision is insufficient",
            "no active target lease",
            "complete supervisor-owned quiescence receipt with closed transcript and clean Beads/Dolt HEAD",
            "exclusive migration lease and durable journal acquired before spawn freeze and held through store/replay/zero-authority verification and immutable release-intent receipt; fresh session waits for the immutable post-release receipt",
            "two isolated migration replay processes and two isolated frozen-program replay processes agree",
        ],
        "program_id": PROGRAM_ID,
        "quiescence_contract": "QUIESCENCE_CONTRACT.json",
        "rollback": {
            "contract": "MIGRATION_TRANSACTION.json",
            "order": ["restore root selector if committed", "restore Beads before-image in one Dolt transaction", "append event compensation", "verify rollback and zero authority", "emit release intent while held", "release lease and emit post-release receipt", "start a fresh session from the prior handoff"],
            "result": "exact before-image hashes restored for reversible stores and append-only event compensation chain plus rollback session handoff verified, or MIGRATION_QUARANTINED",
            "trigger": "any quiescence/prepare/commit/activation/mapping/traversal/queue/handoff/replay/verification mismatch",
        },
        "schema_version": "bb.rl.phase5.migration_plan.v5",
        "session_handoff_contract": "SESSION_HANDOFF_CONTRACT.json",
        "source_entries_captured_at_build": source_entry_count,
        "superseded_rc4": {
            "artifact_manifest_sha256": SUPERSEDED_ARTIFACT_MANIFEST_SHA256,
            "revision_id": SUPERSEDED_REVISION_ID,
            "spec_freeze_grants_rc5_authority": False,
            "supersession_scope": "migration and cutover mechanics only",
        },
        "transaction": "MIGRATION_TRANSACTION.json",
        "validators": [
            "all hashes/sizes/modes",
            "v1 active/score preserved",
            "rc4 artifact manifest digest preserved and bound",
            "49 items/1000 exact",
            "0 points/refs/decisions",
            "DAG acyclic and complete",
            "67 issues mapped",
            "source inventory recaptured before freeze",
            "no campaign/lease",
            "one active selector chain",
            "no v1/scratch fallback",
            "fresh-worker next-action replay",
            "three-store migration replay and typed session handoff",
            "per-store reversible restoration or append-only compensation verification",
        ],
    }


def migration_transaction() -> dict[str, Any]:
    return {
        "commit_order": [
            "v2_event_log",
            "beads_projection",
            "root_active_selector",
        ],
        "receipt_production_order": [
            "pre_replay_inputs_complete",
            "migration_and_fresh_worker_replay_receipts_complete",
            "quiescence_release_intent_receipt_complete",
            "lease_released_and_file_descriptor_closed",
            "quiescence_post_release_receipt_complete",
            "session_post_handoff_receipt_complete",
            "migration_transaction_receipt_complete",
        ],
        "failure_contract": {
            "ordered_rollback": [
                "keep every client stopped and retain the exclusive migration lease",
                "if root_active_selector committed, atomically restore its exact before-image first",
                "restore the exact logical Beads rows and schema in one native transaction selected by the receipt adapter, then make one Dolt commit",
                "append MIGRATION_ROLLED_BACK to the event log, including a genesis-created log, binding every committed event and restored digest",
                "run isolated migration replay over rollback store receipts written before replay plus the durable per-step journal, derive the expected handoff semantics without future receipts, and verify zero authority",
                "emit and fsync the immutable release-intent receipt while held, then release the flock and close its file descriptor",
                "emit the distinct immutable post-release receipt, then start a fresh OMP/RPC session from the prior pre-handoff and post-release receipts with handoff_kind rolled_back",
            ],
            "rollback_failure": [
                "write MIGRATION_QUARANTINED through the independently provisioned emergency journal",
                "keep all clients stopped until typed recovery authority",
                "deny local and target execution",
                "retain all before-images, after-images, command results, native query results, and partial-state hashes",
                "require a new independent integrity review and typed Kyle recovery decision",
            ],
            "triggers": [
                "exclusive migration lease loss",
                "quiescence scope change or restarted client",
                "source digest drift",
                "dirty or changed Beads/Dolt HEAD",
                "prepare validation failure",
                "commit command failure",
                "unexpected intermediate digest",
                "migration replay disagreement",
                "fresh-worker replay disagreement",
                "session handoff failure",
                "post-commit invariant failure",
            ],
        },
        "locking": {
            "client_policy": "BreadBoard, bd/Dolt, and OMP/RPC clients remain stopped; no domain-error behavior is claimed",
            "journal": "durable append-only per-step intent/applied/verified journal is held and fsynced through commit, rollback, replay, handoff, and release",
            "lease_scope": [
                "root_active_selector",
                "beads_projection",
                "v2_event_log",
                "program_root",
                "beads_data_directory",
            ],
            "owner": "external supervisor identified by pid, process group, process-start identity, stable lock inode, and migration_id",
            "release": "after stores, replay, and zero-authority verification, emit and fsync the immutable release-intent receipt while held; release the stable-inode flock and close its file descriptor; then emit the immutable post-release receipt before any fresh OMP/RPC session",
            "rule": "one migration_id and one supervisor-owned verified stable-inode held-flock lease; initial O_CREAT|O_EXCL is provisioning evidence only, and ownership, journal, adapter, or quiescence loss immediately enters failure_contract",
        },
        "mode": "stop_the_world_three_store_compensating_transaction",
        "nonclaims": [
            "the filesystem, Beads/Dolt database, and event log share one physical transaction",
            "session queue/todos are a transaction store",
            "a successful prepare grants execution authority",
            "a selector commit grants target, score, checkpoint, item, completion, or promotion authority",
            "a selector commit waives post-commit verification",
        ],
        "prepare": [
            "validate QUIESCENCE_RECEIPT.json, including the process-closed OMP transcript, cursor-bound prior todo projection, complete process/descriptor inventory, selected Dolt adapter, clean full Dolt HEAD/root, and held exclusive lease",
            "capture immutable before-image presence, bytes, SHA-256, native revision, parent identity, and rollback operation for exactly three stores",
            "for an absent event log record presence absent, bytes SHA-256 null, before head null, and event count zero without fabricating empty-file bytes",
            "construct immutable after-images under migration_id without mutating active state",
            "derive SESSION_PRE_HANDOFF_RECEIPT.json outside transaction stores from the closed transcript plus prior todo projection",
            "validate images, cross-store references, zero awards, zero authority, and v1 selector identity",
            "run two isolated frozen-program projections, two isolated migration replays, and all journaled crash-boundary fixtures against prepared images",
            "issue a new typed Kyle local-only rc5 migration decision bound to the rc5 SPEC_FREEZE, quiescence, adapter, session handoff, journal head, and every before/after digest",
        ],
        "program_id": PROGRAM_ID,
        "receipt_required": {
            "additional_fields_allowed": False,
            "authority": "typed Kyle local-only rc5 migration decision hash; no rc4 authority carries forward",
            "emission": "emit and fsync this final immutable summary only after replay, release-intent, lease release, post-release, and session post-handoff receipts exist",
            "failure_fields": [
                "failed_phase",
                "failed_store",
                "error",
                "rollback_results",
                "rollback_invariant_results",
                "rollback_post_state_hashes",
                "event_compensation_sha256",
                "rollback_session_handoff_sha256",
                "quarantined",
            ],
            "fields": [
                "migration_id",
                "lease_id",
                "quiescence_receipt_sha256",
                "session_pre_handoff_receipt_sha256",
                "dolt_adapter_kind",
                "migration_journal_sha256",
                "migration_journal_final_sequence",
                "authority_decision_sha256",
                "before_images",
                "after_images",
                "prepared_validation_sha256",
                "commit_results",
                "post_commit_hashes",
                "migration_replay_sha256",
                "fresh_worker_replay_sha256",
                "quiescence_release_intent_receipt_sha256",
                "quiescence_post_release_receipt_sha256",
                "session_post_handoff_receipt_sha256",
                "released_lease",
            ],
            "session_fields": {
                "location": "outside stores and commit_order",
                "post_handoff_receipt": "SESSION_HANDOFF_CONTRACT.json post_handoff_receipt",
                "pre_handoff_receipt": "SESSION_HANDOFF_CONTRACT.json pre_handoff_receipt",
            },
            "event_store_fields": ["store_id", "before_presence", "before_head_sha256", "before_event_count", "parent_device", "parent_inode", "absence_rechecked", "genesis_created", "genesis_predecessor_sha256", "creation_device", "creation_inode", "committed_event_sha256s", "after_head_sha256", "compensation_event_sha256", "compensation_head_sha256", "file_fsync_ack", "parent_fsync_ack"],
            "store_fields": [
                "store_id",
                "presence",
                "parent_device",
                "parent_inode",
                "native_adapter",
                "native_revision",
                "bytes_sha256",
                "size",
                "schema_sha256",
                "canonical_rows_sha256",
                "rollback_operation_sha256",
                "reversible",
                "rollback_invariant",
            ],
        },
        "revision_id": REVISION_ID,
        "schema_version": "bb.rl.phase5.migration_transaction.v3",
        "stores": [
            {
                "before_state": {
                    "absent": "presence=absent, bytes_sha256=null, size=null, before_head_sha256=null, before_event_count=0, and exact parent device/inode captured",
                    "present": "presence=present with exact bytes, size, verified chain head, event count, and parent device/inode",
                },
                "commit": "if present, append immutable V1_LINEAGE_IMPORTED and V2_ACTIVATED with the captured predecessor; if absent, recheck absence and parent identity, exclusively create with O_CREAT|O_EXCL and a predecessor-null genesis V1_LINEAGE_IMPORTED event, then append V2_ACTIVATED",
                "genesis_creation": "exclusive create fails if the path appeared or parent identity changed after absence capture; fsync the created file and parent directory before continuing",
                "id": "v2_event_log",
                "prepare": "stage exact canonical event bytes and predecessor hashes; never synthesize an empty before-image for an absent log",
                "rollback": "append MIGRATION_ROLLED_BACK referencing the genesis flag and every committed event hash; events remain historical and non-authoritative",
                "reversible": False,
                "rollback_invariant": "append-only chain ends with MIGRATION_ROLLED_BACK binding before presence, nullable before-head digest, committed event hashes, restored selector and Beads logical digests, and post-compensation head digest; logical authority equals the absent or present before-state while bytes/history advance",
            },
            {
                "commit": "use the receipt-selected native adapter: embedded_dolt_cli runs one direct dolt sql transaction in the resolved repository, while sql_server runs one transaction only over the discovered endpoint; then make one Dolt commit and capture canonical logical rows/schema, clean roots, and full after HEAD/root",
                "id": "beads_projection",
                "prepare": "after ordinary access stops, the supervisor alone runtime-discovers exactly one adapter and captures exact canonical logical issue rows, schema, branch, full HEAD/root, staged root, working root, and clean status; stop every observation child before quiescence",
                "prohibited": "bd backup restore, INSERT IGNORE or per-row/per-table restore loops, bd sql in embedded/direct mode, an inferred server endpoint, and any operation sequence claimed as one transaction",
                "rollback": "through the same selected adapter apply precise logical DELETE/INSERT/schema operations in one explicit native SQL transaction, commit that transaction, export and verify canonical rows/schema, then separately make exactly one Dolt commit",
                "reversible": True,
                "rollback_invariant": "canonical logical Beads rows and schema equal the captured before hashes, status is clean, and the rollback commit records the restored projection; byte identity and restoration to the old HEAD are not claimed",
            },
            {
                "commit": "write and fsync a prepared selector temporary file in the ACTIVE_STATUS parent directory, atomically rename it over root ACTIVE_STATUS as the final store commit, then fsync the parent directory",
                "id": "root_active_selector",
                "path": str(EXECUTION_ROOT / "ACTIVE_STATUS.json"),
                "prepare": f"stage the rc5 selector in the same directory and retain exact v1 bytes {V1_ACTIVE_SHA256}",
                "rollback": f"write and fsync a same-directory temporary file containing exact v1 selector bytes {V1_ACTIVE_SHA256}, atomically rename it over ACTIVE_STATUS, and fsync the parent directory",
                "reversible": True,
                "rollback_invariant": f"normal root selector read returns exact v1 bytes {V1_ACTIVE_SHA256}",
            },
        ],
        "verify": [
            "require exactly three commit results in declared order and prove root selector committed last",
            "verify the selected Dolt adapter, full commits/roots, schema, canonical rows, and durable journal without assuming an embedded or server mode",
            "match every committed after-image or declared append-only head digest through native store reads while all clients remain stopped",
            "prove root selector is the only active version pointer",
            "prove all legacy runnable projections are superseded and no paused packet is eligible",
            "validate typed session pre-handoff while all clients remain stopped; do not mutate or start a prior or new session yet",
            "run two isolated migration replay processes and two distinct frozen-program replay processes with byte-identical semantic outputs",
            "validate zero authority and store/replay receipts, emit and fsync the immutable release-intent receipt while held, then release the flock and close its file descriptor",
            "emit the distinct immutable post-release receipt; only then create the post-cutover session and bind its post-handoff receipt to the post-release receipt, with a crash between release and launch replayable",
            "emit and fsync the final immutable migration transaction receipt only after every replay, release, and session post-handoff receipt named by it exists",
        ],
    }


def fresh_worker_handoff_contract() -> dict[str, Any]:
    return {
        "allowed_inputs": [
            "ARTIFACT_MANIFEST.json",
            "FRESH_WORKER_HANDOFF_CONTRACT.json",
            "PROGRAM_SPEC.yaml",
            "WORK_PACKET_DAG.yaml",
            "RUN_QUEUE.json",
            "DRAFT_STATUS.json",
            "SOURCE_MANIFEST.json",
            "MIGRATION_PLAN.json",
            "MIGRATION_TRANSACTION.json",
            "QUIESCENCE_CONTRACT.json",
            "SESSION_HANDOFF_CONTRACT.json",
            "MIGRATION_REPLAY_CONTRACT.json",
        ],
        "contract_kind": "frozen_program_next_action_replay",
        "derivation": {
            "current_inactive_action": "await a new typed Kyle SPEC_FREEZE bound to the exact rc5 artifact manifest; the rc4 decision has no rc5 authority",
            "post_freeze_pre_cutover_action": "complete supervisor-owned quiescence, prepare and review the three-store migration plus typed session handoff; no execution",
            "post_cutover_execution_frontier": ["AT0"],
            "post_cutover_nonexecuting_preparation": [
                "author a new SHARED_TRANSPORT repair packet without submission authority"
            ],
            "target_execution_allowed": False,
        },
        "distinct_from": {
            "migration_replay": "MIGRATION_REPLAY_CONTRACT.json proves native migration and rollback mechanics",
            "session_handoff": "SESSION_HANDOFF_CONTRACT.json moves immutable closed-session context into a distinct fresh session",
        },
        "isolation": {
            "ambient_inputs_forbidden": [
                "chat history",
                "agent memory",
                "legacy todo state",
                "scratch evidence",
                "unversioned execution-root artifacts",
                "prior worker process state",
                "live session state",
                "live migration stores",
            ],
            "cwd": "new empty temporary directory",
            "environment": "allowlist only",
            "minimum_processes": 2,
        },
        "nonclaims": [
            "frozen program replay is not migration replay or session handoff",
            "replay produces no active, score, checkpoint, target, completion, or promotion state",
        ],
        "program_id": PROGRAM_ID,
        "receipt": {
            "additional_fields_allowed": False,
            "each_worker_fields": [
                "pid",
                "input_hashes",
                "derived_action",
                "execution_frontier",
                "target_execution_allowed",
                "ambient_inputs_used",
            ],
            "pass": "all workers use exactly allowed inputs and produce byte-identical semantic outputs",
            "top_level_fields": [
                "artifact_manifest_sha256",
                "contract_sha256",
                "worker_count",
                "worker_semantic_sha256",
                "result",
            ],
        },
        "revision_id": REVISION_ID,
        "schema_version": "bb.rl.phase5.fresh_worker_handoff_contract.v2",
    }


def archive_v1(destination: Path) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    names = ["ACTIVE_STATUS.json", "ARTIFACT_MANIFEST.json", "FIXTURE_MANIFEST.json", "LOOP_SPEC.yaml", "VARIANT_CATALOG.json", "WORK_PACKET_DAG.yaml", "CAMPAIGN_MATRIX.yaml", "CLAIM_LEDGER.md", "EVIDENCE_TAXONOMY.json", "SCORECARD.json"]
    for name in names:
        shutil.copy2(EXECUTION_ROOT / name, destination / name)
    if (EXECUTION_ROOT / "history").exists():
        shutil.copytree(EXECUTION_ROOT / "history", destination / "history")
    for path in destination.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    rows = []
    for path in sorted(item for item in destination.rglob("*") if item.is_file()):
        rows.append({"mode": f"{stat.S_IMODE(path.stat().st_mode):04o}", "path": path.relative_to(destination).as_posix(), "sha256": sha256_file(path), "size": path.stat().st_size})
    manifest = {"archive_id": ARCHIVE_ID, "files": rows, "original_active_status_sha256": V1_ACTIVE_SHA256, "original_scorecard_sha256": V1_SCORECARD_SHA256, "policy": {"byte_identical": True, "no_v2_authority": True, "read_only_historical": True}, "program_id": "bb-zyphra-rl-phase5-v1", "schema_version": "bb.rl.phase5.v1_archive_manifest.v1", "source_root": str(EXECUTION_ROOT)}
    write_canonical(destination / "ARCHIVE_MANIFEST.json", manifest)
    for path in destination.rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    return manifest


def build_tree(destination: Path, _beads_export: Path) -> dict[str, Any]:
    archive_source = EXECUTION_ROOT / f"versions/{ARCHIVE_ID}"
    predecessor_root = (
        EXECUTION_ROOT / f"versions/v2-two-track/{SUPERSEDED_REVISION_ID}"
    )
    predecessor_manifest_path = predecessor_root / "ARTIFACT_MANIFEST.json"
    if sha256_file(predecessor_manifest_path) != SUPERSEDED_ARTIFACT_MANIFEST_SHA256:
        raise ValueError("superseded rc4 artifact manifest changed")

    archive_root = destination / f"versions/{ARCHIVE_ID}"
    revision_root = destination / f"versions/v2-two-track/{REVISION_ID}"
    shutil.copytree(archive_source, archive_root)
    shutil.copytree(predecessor_root, revision_root)

    catalog = json.loads((revision_root / "ASSURANCE_CATALOG.json").read_text())
    graph = json.loads((revision_root / "WORK_PACKET_DAG.yaml").read_text())
    dispositions = json.loads(
        (revision_root / "PACKET_DISPOSITIONS.json").read_text()
    )
    source = json.loads((revision_root / "SOURCE_MANIFEST.json").read_text())
    source["supersession"] = {
        "candidate_revision_id": REVISION_ID,
        "prior_spec_freeze_grants_candidate_authority": False,
        "scope": "migration and cutover mechanics only",
        "superseded_artifact_manifest_sha256": (
            SUPERSEDED_ARTIFACT_MANIFEST_SHA256
        ),
        "superseded_revision_id": SUPERSEDED_REVISION_ID,
    }
    source_entries = sum(
        repository["dirty_entries"] for repository in source["repositories"]
    )
    _, _, status = initial_state(catalog, graph, dispositions)
    replacements = {
        "DRAFT_STATUS.json": status,
        "FRESH_WORKER_HANDOFF_CONTRACT.json": fresh_worker_handoff_contract(),
        "MIGRATION_PLAN.json": migration_plan(source_entries),
        "MIGRATION_REPLAY_CONTRACT.json": migration_replay_contract(),
        "MIGRATION_TRANSACTION.json": migration_transaction(),
        "PROGRAM_SPEC.yaml": program_spec(),
        "QUIESCENCE_CONTRACT.json": quiescence_contract(),
        "SESSION_HANDOFF_CONTRACT.json": session_handoff_contract(),
        "SOURCE_MANIFEST.json": source,
    }
    for name, value in replacements.items():
        replacement_path = revision_root / name
        replacement_path.chmod(0o600)
        write_canonical(replacement_path, value)

    (revision_root / "ARTIFACT_MANIFEST.json").unlink()
    rows = []
    for path in sorted(item for item in revision_root.iterdir() if item.is_file()):
        rows.append(
            {
                "media_type": (
                    "application/yaml"
                    if path.suffix == ".yaml"
                    else "application/json"
                ),
                "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
                "path": path.name,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    artifact_manifest = {
        "archive_manifest_sha256": sha256_file(
            archive_root / "ARCHIVE_MANIFEST.json"
        ),
        "files": rows,
        "immutable": True,
        "program_id": PROGRAM_ID,
        "revision_id": REVISION_ID,
        "schema_version": "bb.rl.phase5.artifact_manifest.v4",
        "superseded_artifact_manifest_sha256": (
            SUPERSEDED_ARTIFACT_MANIFEST_SHA256
        ),
        "superseded_revision_id": SUPERSEDED_REVISION_ID,
        "supersession_scope": (
            "migration and cutover mechanics only; prior rc4 SPEC_FREEZE "
            "grants no rc5 authority"
        ),
        "v1_active_status_sha256": V1_ACTIVE_SHA256,
        "v1_scorecard_sha256": V1_SCORECARD_SHA256,
    }
    write_canonical(revision_root / "ARTIFACT_MANIFEST.json", artifact_manifest)
    return {
        "archive_manifest_sha256": sha256_file(
            archive_root / "ARCHIVE_MANIFEST.json"
        ),
        "artifact_manifest_sha256": sha256_file(
            revision_root / "ARTIFACT_MANIFEST.json"
        ),
        "catalog_sha256": sha256_file(
            revision_root / "ASSURANCE_CATALOG.json"
        ),
        "equivalence_sha256": sha256_file(
            revision_root / "CATALOG_EQUIVALENCE.json"
        ),
        "revision_root": revision_root.relative_to(destination).as_posix(),
        "source_entries": source_entries,
    }


def tree_hashes(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): sha256_file(path) for path in sorted(item for item in root.rglob("*") if item.is_file())}


def install_tree(source: Path) -> None:
    for relative in (Path(f"versions/{ARCHIVE_ID}"), Path(f"versions/v2-two-track/{REVISION_ID}")):
        source_path = source / relative
        target_path = EXECUTION_ROOT / relative
        if not target_path.exists():
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_path, target_path)
            continue
        source_hashes = tree_hashes(source_path)
        target_hashes = tree_hashes(target_path)
        extra = sorted(set(target_hashes) - set(source_hashes))
        changed = sorted(
            path
            for path in set(target_hashes) & set(source_hashes)
            if target_hashes[path] != source_hashes[path]
        )
        if extra or changed:
            raise ValueError(
                f"existing staged tree differs: {target_path}; extra={extra}; changed={changed}"
            )
        for source_file in sorted(item for item in source_path.rglob("*") if item.is_file()):
            relative_file = source_file.relative_to(source_path)
            target_file = target_path / relative_file
            target_file.parent.mkdir(parents=True, exist_ok=True)
            if not target_file.exists():
                shutil.copy2(source_file, target_file)
            target_file.chmod(stat.S_IMODE(source_file.stat().st_mode))
        if tree_hashes(source_path) != tree_hashes(target_path):
            raise ValueError(f"staged tree incomplete after install: {target_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--beads-export", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="phase5-v2-build-a-") as first, tempfile.TemporaryDirectory(prefix="phase5-v2-build-b-") as second:
        first_path = Path(first)
        second_path = Path(second)
        first_result = build_tree(first_path, args.beads_export)
        build_tree(second_path, args.beads_export)
        first_hashes = tree_hashes(first_path)
        second_hashes = tree_hashes(second_path)
        if first_hashes != second_hashes:
            missing = sorted(set(first_hashes) ^ set(second_hashes))
            changed = sorted(path for path in set(first_hashes) & set(second_hashes) if first_hashes[path] != second_hashes[path])
            raise ValueError(f"nondeterministic build; missing={missing}; changed={changed}")
        if args.install:
            install_tree(first_path)
        report = {"build_a_file_count": len(first_hashes), "build_b_file_count": len(second_hashes), "byte_identical": True, "installed": args.install, "program_id": PROGRAM_ID, "result": "pass", "revision_id": REVISION_ID, "schema_version": "bb.rl.phase5.freeze_build_report.v1", **first_result}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_bytes(canonical_bytes(report))
        print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
