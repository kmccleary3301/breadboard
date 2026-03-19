from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
LIVE_SUMMARY = ROOT / "artifacts" / "darwin" / "live_baselines" / "live_baseline_summary_v1.json"
SEARCH_DIR = ROOT / "artifacts" / "darwin" / "search"
SEARCH_SUMMARY = SEARCH_DIR / "search_smoke_summary_v1.json"
ARCHIVE_SNAPSHOT = SEARCH_DIR / "archive_snapshot_v1.json"
PROMOTION_DECISIONS = SEARCH_DIR / "promotion_decisions_v1.json"
PROMOTION_HISTORY = SEARCH_DIR / "promotion_history_v1.json"
TRANSFER_LEDGER = SEARCH_DIR / "transfer_ledger_v1.json"
REPLAY_AUDIT = SEARCH_DIR / "phase1_replay_audit_v1.json"
INVALID_LEDGER = SEARCH_DIR / "invalid_comparison_ledger_v1.json"
MUTATION_REGISTRY = SEARCH_DIR / "mutation_operator_registry_v1.json"
LANE_REGISTRY = ROOT / "docs" / "contracts" / "darwin" / "registries" / "lane_registry_v0.json"
POLICY_REGISTRY = ROOT / "docs" / "contracts" / "darwin" / "registries" / "policy_registry_v0.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ref_with_anchor(path: Path, anchor: str) -> str:
    return f"{path.relative_to(ROOT)}#{anchor}"


def _live_rows_by_lane() -> dict[str, dict[str, Any]]:
    payload = _load_json(LIVE_SUMMARY)
    return {row["lane_id"]: row for row in payload.get("lanes") or []}


def _mutation_operator_lookup() -> dict[str, dict[str, Any]]:
    payload = _load_json(MUTATION_REGISTRY)
    return {row["operator_id"]: row for row in payload.get("operators") or []}


def _lane_registry_lookup() -> dict[str, dict[str, Any]]:
    payload = _load_json(LANE_REGISTRY)
    return {row["lane_id"]: row for row in payload.get("lanes") or []}


def _policy_registry_lookup() -> dict[str, dict[str, Any]]:
    payload = _load_json(POLICY_REGISTRY)
    return {row["policy_bundle_id"]: row for row in payload.get("bundles") or []}


def _component_ref(
    *,
    component_kind: str,
    component_id: str,
    lane_scope: list[str],
    source_ref: str,
    source_path: Path,
    status: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "schema": "breadboard.darwin.component_ref.v0",
        "component_kind": component_kind,
        "component_id": component_id,
        "lane_scope": lane_scope,
        "source_ref": source_ref,
        "source_digest": _sha256_file(source_path),
        "status": status,
    }
    if metadata:
        payload["metadata"] = metadata
    return payload


def build_component_refs() -> list[dict[str, Any]]:
    live_rows = _live_rows_by_lane()
    lane_registry = _lane_registry_lookup()
    policy_registry = _policy_registry_lookup()
    mutation_registry = _mutation_operator_lookup()

    rows: list[dict[str, Any]] = []

    for topology_id in ["policy.topology.single_v0", "policy.topology.pev_v0"]:
        bundle = policy_registry[topology_id]
        rows.append(
            _component_ref(
                component_kind="topology",
                component_id=topology_id,
                lane_scope=bundle["applies_to_lanes"],
                source_ref=_ref_with_anchor(POLICY_REGISTRY, topology_id),
                source_path=POLICY_REGISTRY,
                status=bundle["status"],
                metadata={"policy_class": bundle["policy_class"]},
            )
        )

    for policy_id in ["policy.memory.flat_v0", "policy.budget.class_a_v0"]:
        bundle = policy_registry[policy_id]
        rows.append(
            _component_ref(
                component_kind="policy",
                component_id=policy_id,
                lane_scope=bundle["applies_to_lanes"],
                source_ref=_ref_with_anchor(POLICY_REGISTRY, policy_id),
                source_path=POLICY_REGISTRY,
                status=bundle["status"],
                metadata={"policy_class": bundle["policy_class"]},
            )
        )

    for operator_id in [
        "mut.topology.single_to_pev_v1",
        "mut.budget.class_a_to_class_b_v1",
        "mut.scheduler.strategy_value_density_v1",
        "mut.scheduler.strategy_hybrid_v1",
        "mut.prompt.tighten_acceptance_v1",
    ]:
        operator = mutation_registry[operator_id]
        rows.append(
            _component_ref(
                component_kind="operator",
                component_id=operator_id,
                lane_scope=sorted(set((operator.get("supported_lanes") or []) + (operator.get("experimental_lanes") or []))),
                source_ref=_ref_with_anchor(MUTATION_REGISTRY, operator_id),
                source_path=MUTATION_REGISTRY,
                status="approved",
                metadata={"operator_class": operator["operator_class"]},
            )
        )

    evaluator_sources = {
        "lane.harness": live_rows["lane.harness"]["shadow_artifact_refs"]["evaluator_pack"],
        "lane.repo_swe": live_rows["lane.repo_swe"]["shadow_artifact_refs"]["evaluator_pack"],
    }
    for lane_id, source_ref in evaluator_sources.items():
        source_path = ROOT / source_ref
        rows.append(
            _component_ref(
                component_kind="evaluator_pack",
                component_id=f"evaluator.{lane_id}.shadow.v0",
                lane_scope=[lane_id],
                source_ref=source_ref,
                source_path=source_path,
                status="derived",
                metadata={"evaluator_type": lane_registry[lane_id]["evaluator_type"]},
            )
        )

    for lane_id in ["lane.scheduling", "lane.research"]:
        rows.append(
            _component_ref(
                component_kind="evaluator_pack",
                component_id=f"evaluator.{lane_id}.registry.v0",
                lane_scope=[lane_id],
                source_ref=_ref_with_anchor(LANE_REGISTRY, lane_id),
                source_path=LANE_REGISTRY,
                status="derived",
                metadata={"evaluator_type": lane_registry[lane_id]["evaluator_type"]},
            )
        )

    return rows


def build_decision_records(*, component_index: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    promotion = _load_json(PROMOTION_DECISIONS)
    promotion_history = _load_json(PROMOTION_HISTORY)
    transfer = _load_json(TRANSFER_LEDGER)
    replay = _load_json(REPLAY_AUDIT)
    invalid = _load_json(INVALID_LEDGER)
    invalid_rows = {row.get("candidate_id"): row for row in invalid.get("rows") or [] if row.get("candidate_id")}
    replay_rows = {row["candidate_id"]: row for row in replay.get("audits") or []}

    rows: list[dict[str, Any]] = []

    harness_history = next(row for row in promotion_history.get("lanes") or [] if row["lane_id"] == "lane.harness")
    harness_cycle = harness_history["cycle_records"][0]
    rows.append(
        {
            "schema": "breadboard.darwin.decision_record.v0",
            "decision_id": "decision.retain.lane.harness.cycle1.v1",
            "decision_type": "retained_baseline",
            "lane_id": "lane.harness",
            "candidate_ids": [harness_cycle["baseline_candidate_id"], *harness_cycle["candidate_ids"]],
            "component_ids": [
                "mut.topology.single_to_pev_v1",
                "mut.prompt.tighten_acceptance_v1",
                "evaluator.lane.harness.shadow.v0",
            ],
            "evidence_refs": [
                str(PROMOTION_HISTORY.relative_to(ROOT)),
                str(ARCHIVE_SNAPSHOT.relative_to(ROOT)),
            ],
            "replay_refs": [],
            "decision_basis": {
                "reason_code": "score_saturated_retain_baseline",
                "lineage_state": "retained_baseline",
                "rollback_candidate_id": harness_cycle["rollback_candidate_id"],
                "rejected_candidate_ids": harness_cycle["candidate_ids"],
            },
            "decided_at": _now(),
        }
    )

    repo_swe_history = next(row for row in promotion_history.get("lanes") or [] if row["lane_id"] == "lane.repo_swe")
    repo_swe_cycle = repo_swe_history["cycle_records"][0]
    rows.append(
        {
            "schema": "breadboard.darwin.decision_record.v0",
            "decision_id": "decision.retain.lane.repo_swe.cycle1.v1",
            "decision_type": "retained_baseline",
            "lane_id": "lane.repo_swe",
            "candidate_ids": [repo_swe_cycle["baseline_candidate_id"], *repo_swe_cycle["candidate_ids"]],
            "component_ids": [
                "mut.topology.single_to_pev_v1",
                "mut.budget.class_a_to_class_b_v1",
                "evaluator.lane.repo_swe.shadow.v0",
            ],
            "evidence_refs": [
                str(PROMOTION_HISTORY.relative_to(ROOT)),
                str(INVALID_LEDGER.relative_to(ROOT)),
                str(ARCHIVE_SNAPSHOT.relative_to(ROOT)),
            ],
            "replay_refs": [str(REPLAY_AUDIT.relative_to(ROOT))],
            "decision_basis": {
                "reason_code": "valid_baseline_retained_with_invalid_trial_pressure",
                "lineage_state": "retained_baseline",
                "rollback_candidate_id": repo_swe_cycle["rollback_candidate_id"],
                "invalid_candidate_ids": ["cand.lane.repo_swe.mut.class_b.v1"],
                "rejected_candidate_ids": repo_swe_cycle["candidate_ids"],
            },
            "decided_at": _now(),
        }
    )

    scheduling_cycle1 = next(
        row for row in promotion["decisions"] if row["lane_id"] == "lane.scheduling" and row["cycle_index"] == 1
    )
    rows.append(
        {
            "schema": "breadboard.darwin.decision_record.v0",
            "decision_id": "decision.promote.lane.scheduling.cycle1.v1",
            "decision_type": "promotion",
            "lane_id": "lane.scheduling",
            "candidate_ids": [scheduling_cycle1["baseline_candidate_id"], scheduling_cycle1["winner_candidate_id"]],
            "component_ids": [
                "mut.scheduler.strategy_value_density_v1",
                "evaluator.lane.scheduling.registry.v0",
            ],
            "evidence_refs": [
                str(PROMOTION_DECISIONS.relative_to(ROOT)),
                str(PROMOTION_HISTORY.relative_to(ROOT)),
            ],
            "replay_refs": [],
            "decision_basis": {
                "reason_code": "positive_retention_gain",
                "lineage_state": "promoted",
                "improvement": scheduling_cycle1["improvement"],
                "rollback_candidate_id": scheduling_cycle1["rollback_candidate_id"],
                "supersedes_candidate_ids": [scheduling_cycle1["baseline_candidate_id"]],
            },
            "decided_at": _now(),
        }
    )

    scheduling_cycle2 = next(
        row for row in promotion["decisions"] if row["lane_id"] == "lane.scheduling" and row["cycle_index"] == 2
    )
    rows.append(
        {
            "schema": "breadboard.darwin.decision_record.v0",
            "decision_id": "decision.promote.lane.scheduling.cycle2.v1",
            "decision_type": "promotion",
            "lane_id": "lane.scheduling",
            "candidate_ids": [scheduling_cycle2["baseline_candidate_id"], scheduling_cycle2["winner_candidate_id"]],
            "component_ids": [
                "mut.scheduler.strategy_hybrid_v1",
                "policy.topology.pev_v0",
                "evaluator.lane.scheduling.registry.v0",
            ],
            "evidence_refs": [
                str(PROMOTION_DECISIONS.relative_to(ROOT)),
                str(PROMOTION_HISTORY.relative_to(ROOT)),
            ],
            "replay_refs": [str(REPLAY_AUDIT.relative_to(ROOT))],
            "decision_basis": {
                "reason_code": "positive_retention_gain",
                "lineage_state": "promoted",
                "improvement": scheduling_cycle2["improvement"],
                "rollback_candidate_id": scheduling_cycle2["rollback_candidate_id"],
                "supersedes_candidate_ids": [scheduling_cycle2["baseline_candidate_id"]],
            },
            "decided_at": _now(),
        }
    )

    transfer_row = (transfer.get("attempts") or [])[0]
    rows.append(
        {
            "schema": "breadboard.darwin.decision_record.v0",
            "decision_id": "decision.transfer.harness.prompt_to_research.v1",
            "decision_type": "transfer",
            "lane_id": "lane.research",
            "candidate_ids": [transfer_row["source_candidate_id"], transfer_row["target_candidate_id"]],
            "component_ids": [
                "mut.prompt.tighten_acceptance_v1",
                "policy.budget.class_a_v0",
                "policy.topology.single_v0",
                "evaluator.lane.research.registry.v0",
            ],
            "evidence_refs": [str(TRANSFER_LEDGER.relative_to(ROOT))],
            "replay_refs": [str(REPLAY_AUDIT.relative_to(ROOT))],
            "decision_basis": {
                "reason_code": transfer_row["validity_reason"],
                "lineage_state": "promoted",
                "transfer_family": "cross_lane_prompt_family_transfer",
                "promotion_status": transfer_row["promotion_status"],
                "result": transfer_row["result"],
                "result_class": "valid_improved",
                "descriptive_only": True,
                "replay_required": transfer_row["replay_required"],
                "replay_stable": transfer_row["replay_stable"],
            },
            "decided_at": _now(),
        }
    )

    rows.append(
        {
            "schema": "breadboard.darwin.decision_record.v0",
            "decision_id": "decision.deprecate.lane.repo_swe.mut.class_b.v1",
            "decision_type": "deprecation",
            "lane_id": "lane.repo_swe",
            "candidate_ids": ["cand.lane.repo_swe.mut.class_b.v1"],
            "component_ids": [
                "mut.budget.class_a_to_class_b_v1",
                "policy.budget.class_a_v0",
                "evaluator.lane.repo_swe.shadow.v0",
            ],
            "evidence_refs": [
                str(PROMOTION_DECISIONS.relative_to(ROOT)),
                str(INVALID_LEDGER.relative_to(ROOT)),
            ],
            "replay_refs": [],
            "decision_basis": {
                "reason_code": invalid_rows["cand.lane.repo_swe.mut.class_b.v1"]["reason"],
                "lineage_state": "deprecated",
                "transfer_reuse_allowed": False,
            },
            "decided_at": _now(),
        }
    )

    rows.append(
        {
            "schema": "breadboard.darwin.decision_record.v0",
            "decision_id": "decision.deprecate.lane.repo_swe.mut.pev.v1",
            "decision_type": "deprecation",
            "lane_id": "lane.repo_swe",
            "candidate_ids": ["cand.lane.repo_swe.mut.pev.v1"],
            "component_ids": [
                "mut.topology.single_to_pev_v1",
                "policy.topology.pev_v0",
                "evaluator.lane.repo_swe.shadow.v0",
            ],
            "evidence_refs": [str(PROMOTION_DECISIONS.relative_to(ROOT))],
            "replay_refs": [str(REPLAY_AUDIT.relative_to(ROOT))],
            "decision_basis": {
                "reason_code": "no_improvement_over_baseline",
                "lineage_state": "deprecated",
                "replay_stable": replay_rows["cand.lane.repo_swe.mut.pev.v1"]["stable"],
                "transfer_reuse_allowed": False,
            },
            "decided_at": _now(),
        }
    )

    return rows


def build_reconstructed_cases() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "case.promotion.lane.scheduling.v1",
            "lane_id": "lane.scheduling",
            "case_kind": "promotion_history",
            "component_ids": [
                "mut.scheduler.strategy_value_density_v1",
                "mut.scheduler.strategy_hybrid_v1",
                "evaluator.lane.scheduling.registry.v0",
            ],
            "decision_ids": [
                "decision.promote.lane.scheduling.cycle1.v1",
                "decision.promote.lane.scheduling.cycle2.v1",
            ],
            "source_refs": [
                str(PROMOTION_DECISIONS.relative_to(ROOT)),
                str(PROMOTION_HISTORY.relative_to(ROOT)),
                str(REPLAY_AUDIT.relative_to(ROOT)),
            ],
        },
        {
            "case_id": "case.transfer.harness_to_research.v1",
            "lane_id": "lane.research",
            "case_kind": "transfer",
            "component_ids": [
                "mut.prompt.tighten_acceptance_v1",
                "policy.topology.single_v0",
                "policy.budget.class_a_v0",
                "evaluator.lane.research.registry.v0",
            ],
            "decision_ids": ["decision.transfer.harness.prompt_to_research.v1"],
            "source_refs": [
                str(TRANSFER_LEDGER.relative_to(ROOT)),
                str(REPLAY_AUDIT.relative_to(ROOT)),
            ],
        },
        {
            "case_id": "case.search_review.lane.repo_swe.v1",
            "lane_id": "lane.repo_swe",
            "case_kind": "search_review",
            "component_ids": [
                "mut.topology.single_to_pev_v1",
                "mut.budget.class_a_to_class_b_v1",
                "evaluator.lane.repo_swe.shadow.v0",
            ],
            "decision_ids": [
                "decision.retain.lane.repo_swe.cycle1.v1",
                "decision.deprecate.lane.repo_swe.mut.class_b.v1",
                "decision.deprecate.lane.repo_swe.mut.pev.v1",
            ],
            "source_refs": [
                str(PROMOTION_DECISIONS.relative_to(ROOT)),
                str(INVALID_LEDGER.relative_to(ROOT)),
                str(REPLAY_AUDIT.relative_to(ROOT)),
            ],
        },
    ]


def build_evolution_ledger() -> dict[str, Any]:
    components = build_component_refs()
    component_index = {row["component_id"]: row for row in components}
    decisions = build_decision_records(component_index=component_index)
    return {
        "schema": "breadboard.darwin.evolution_ledger.v0",
        "generated_at": _now(),
        "decision_truth_scope": {
            "canonical_decision_types": ["promotion", "rollback", "transfer", "deprecation", "retained_baseline"],
            "runtime_truth_owned_by": "breadboard_runtime",
            "archive_is_derived": True,
            "derived_view_ids": [
                "archive_snapshot_v1",
                "compute_normalized_view_v2",
                "comparative_dossier_v1",
                "external_safe_packet_v0",
            ],
        },
        "source_artifact_refs": {
            "live_summary_ref": str(LIVE_SUMMARY.relative_to(ROOT)),
            "search_summary_ref": str(SEARCH_SUMMARY.relative_to(ROOT)),
            "archive_snapshot_ref": str(ARCHIVE_SNAPSHOT.relative_to(ROOT)),
            "promotion_decisions_ref": str(PROMOTION_DECISIONS.relative_to(ROOT)),
            "promotion_history_ref": str(PROMOTION_HISTORY.relative_to(ROOT)),
            "transfer_ledger_ref": str(TRANSFER_LEDGER.relative_to(ROOT)),
            "replay_audit_ref": str(REPLAY_AUDIT.relative_to(ROOT)),
            "invalid_comparison_ledger_ref": str(INVALID_LEDGER.relative_to(ROOT)),
        },
        "archive_snapshot_is_derived": True,
        "component_refs": components,
        "decision_records": decisions,
        "reconstructed_cases": build_reconstructed_cases(),
    }
