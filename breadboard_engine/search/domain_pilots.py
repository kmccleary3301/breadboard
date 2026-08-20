from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence

from .examples import (
    build_dag_v4_codetree_v2_packet,
    build_dag_v4_team_of_thoughts_packet,
    build_dag_v4_tot_v2_packet,
    build_post_v2_study_17_repair_loop_after_reducer,
)


def _copy_mapping(value: Mapping[str, Any] | None) -> Dict[str, Any]:
    return dict(value or {})


def _copy_text_list(values: Sequence[Any] | None) -> List[str]:
    copied: List[str] = []
    for item in values or []:
        text = str(item or "").strip()
        if text:
            copied.append(text)
    return copied


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


@dataclass(frozen=True)
class SearchDomainBoundaryControlPacket:
    packet_id: str
    domain_kind: str
    source_study_keys: List[str]
    source_search_ids: List[str]
    benchmark_manifest_refs: List[str]
    external_surface_refs: List[str]
    preserved_dag_truth: List[str]
    forbidden_domain_semantics: List[str]
    control_requirements: List[str]
    ontology_blending_forbidden: bool
    notes: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "packet_id", _require_text(self.packet_id, "packet_id"))
        object.__setattr__(self, "domain_kind", _require_text(self.domain_kind, "domain_kind"))
        object.__setattr__(self, "source_study_keys", _copy_text_list(self.source_study_keys))
        object.__setattr__(self, "source_search_ids", _copy_text_list(self.source_search_ids))
        object.__setattr__(self, "benchmark_manifest_refs", _copy_text_list(self.benchmark_manifest_refs))
        object.__setattr__(self, "external_surface_refs", _copy_text_list(self.external_surface_refs))
        object.__setattr__(self, "preserved_dag_truth", _copy_text_list(self.preserved_dag_truth))
        object.__setattr__(self, "forbidden_domain_semantics", _copy_text_list(self.forbidden_domain_semantics))
        object.__setattr__(self, "control_requirements", _copy_text_list(self.control_requirements))
        object.__setattr__(self, "ontology_blending_forbidden", bool(self.ontology_blending_forbidden))
        object.__setattr__(self, "notes", _copy_mapping(self.notes))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "domain_kind": self.domain_kind,
            "source_study_keys": list(self.source_study_keys),
            "source_search_ids": list(self.source_search_ids),
            "benchmark_manifest_refs": list(self.benchmark_manifest_refs),
            "external_surface_refs": list(self.external_surface_refs),
            "preserved_dag_truth": list(self.preserved_dag_truth),
            "forbidden_domain_semantics": list(self.forbidden_domain_semantics),
            "control_requirements": list(self.control_requirements),
            "ontology_blending_forbidden": self.ontology_blending_forbidden,
            "notes": dict(self.notes),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SearchDomainPilotPacket:
    pilot_id: str
    domain_kind: str
    source_study_keys: List[str]
    source_search_ids: List[str]
    benchmark_packet_refs: List[str]
    boundary_control_id: str
    adapter_surface_refs: List[str]
    expected_artifact_refs: List[str]
    success_criteria: List[str]
    friction_locus: str
    ontology_blending_detected: bool
    kernel_change_required: bool
    notes: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "pilot_id", _require_text(self.pilot_id, "pilot_id"))
        object.__setattr__(self, "domain_kind", _require_text(self.domain_kind, "domain_kind"))
        object.__setattr__(self, "source_study_keys", _copy_text_list(self.source_study_keys))
        object.__setattr__(self, "source_search_ids", _copy_text_list(self.source_search_ids))
        object.__setattr__(self, "benchmark_packet_refs", _copy_text_list(self.benchmark_packet_refs))
        object.__setattr__(self, "boundary_control_id", _require_text(self.boundary_control_id, "boundary_control_id"))
        object.__setattr__(self, "adapter_surface_refs", _copy_text_list(self.adapter_surface_refs))
        object.__setattr__(self, "expected_artifact_refs", _copy_text_list(self.expected_artifact_refs))
        object.__setattr__(self, "success_criteria", _copy_text_list(self.success_criteria))
        object.__setattr__(self, "friction_locus", _require_text(self.friction_locus, "friction_locus"))
        object.__setattr__(self, "ontology_blending_detected", bool(self.ontology_blending_detected))
        object.__setattr__(self, "kernel_change_required", bool(self.kernel_change_required))
        object.__setattr__(self, "notes", _copy_mapping(self.notes))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pilot_id": self.pilot_id,
            "domain_kind": self.domain_kind,
            "source_study_keys": list(self.source_study_keys),
            "source_search_ids": list(self.source_search_ids),
            "benchmark_packet_refs": list(self.benchmark_packet_refs),
            "boundary_control_id": self.boundary_control_id,
            "adapter_surface_refs": list(self.adapter_surface_refs),
            "expected_artifact_refs": list(self.expected_artifact_refs),
            "success_criteria": list(self.success_criteria),
            "friction_locus": self.friction_locus,
            "ontology_blending_detected": self.ontology_blending_detected,
            "kernel_change_required": self.kernel_change_required,
            "notes": dict(self.notes),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SearchDomainFrictionSummary:
    summary_id: str
    domain_kinds: List[str]
    friction_loci: List[str]
    ontology_blending_detected: bool
    repeated_shape_gap_detected: bool
    next_decision: str
    notes: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "summary_id", _require_text(self.summary_id, "summary_id"))
        object.__setattr__(self, "domain_kinds", _copy_text_list(self.domain_kinds))
        object.__setattr__(self, "friction_loci", _copy_text_list(self.friction_loci))
        object.__setattr__(self, "ontology_blending_detected", bool(self.ontology_blending_detected))
        object.__setattr__(self, "repeated_shape_gap_detected", bool(self.repeated_shape_gap_detected))
        object.__setattr__(self, "next_decision", _require_text(self.next_decision, "next_decision"))
        object.__setattr__(self, "notes", _copy_mapping(self.notes))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary_id": self.summary_id,
            "domain_kinds": list(self.domain_kinds),
            "friction_loci": list(self.friction_loci),
            "ontology_blending_detected": self.ontology_blending_detected,
            "repeated_shape_gap_detected": self.repeated_shape_gap_detected,
            "next_decision": self.next_decision,
            "notes": dict(self.notes),
            "metadata": dict(self.metadata),
        }


def build_search_atp_boundary_control_packet() -> SearchDomainBoundaryControlPacket:
    tot = build_dag_v4_tot_v2_packet()
    team = build_dag_v4_team_of_thoughts_packet()
    return SearchDomainBoundaryControlPacket(
        packet_id="search.domain.atp.boundary_control.v1",
        domain_kind="atp",
        source_study_keys=["dag_replication_v1_tot_game24", "dag_v4_team_of_thoughts"],
        source_search_ids=[tot["run"].search_id, team["run"].search_id],
        benchmark_manifest_refs=[
            "artifacts/benchmarks/hilbert_comparison_packs_v1/*/cross_system_manifest.json",
            "artifacts/benchmarks/hilbert_comparison_packs_v1/*/bb_task_inputs.json",
        ],
        external_surface_refs=[
            "scripts/run_bb_atp_adapter_slice_v1.py",
            "scripts/build_hilbert_bb_comparison_bundle_v1.py",
            "agentic_coder_prototype/api/cli_bridge/atp_router.py",
        ],
        preserved_dag_truth=[
            "frontier_and_reopen_visibility",
            "assessment_lineage_visibility",
            "selected_candidate_identity",
            "compute_budget_visibility",
        ],
        forbidden_domain_semantics=[
            "proof_state_kernel_truth_in_search",
            "theorem_language_specific_nouns_in_search_kernel",
            "lean_specific_scheduler_fields_in_search",
        ],
        control_requirements=[
            "exact_verifier_ground_truth",
            "benchmark_manifest_honesty",
            "adapter_boundary_explicit",
            "no_shadow_proof_state_requirement",
        ],
        ontology_blending_forbidden=True,
        notes={
            "pilot_scope": "bounded_hilbert_slice_only",
            "why_atp_first": "exact_verification plus branch/adjudication pressure without ontology import",
        },
        metadata={"phase": "dag_v5_phase5"},
    )


def build_search_atp_domain_pilot() -> Dict[str, object]:
    boundary = build_search_atp_boundary_control_packet()
    tot = build_dag_v4_tot_v2_packet()
    team = build_dag_v4_team_of_thoughts_packet()
    pilot = SearchDomainPilotPacket(
        pilot_id="search.domain.atp.pilot.v1",
        domain_kind="atp",
        source_study_keys=list(boundary.source_study_keys),
        source_search_ids=list(boundary.source_search_ids),
        benchmark_packet_refs=[
            tot["recipe_manifest"].benchmark_packet,
            team["recipe_manifest"].benchmark_packet,
        ],
        boundary_control_id=boundary.packet_id,
        adapter_surface_refs=list(boundary.external_surface_refs),
        expected_artifact_refs=[
            "artifacts/benchmarks/hilbert_comparison_packs_v1/*/cross_system_manifest.json",
            "artifacts/benchmarks/hilbert_comparison_packs_v1/*/bb_task_inputs.json",
            "artifacts/benchmarks/cross_system/bb_atp/proofs/*.json",
        ],
        success_criteria=[
            "branch_and_reopen_visibility_stays_in_dag_truth",
            "exact_verifier_result_stays_adapter_local",
            "no_atp_semantic_import_into_search_kernel",
        ],
        friction_locus="adapter_and_harness_local_only",
        ontology_blending_detected=False,
        kernel_change_required=False,
        notes={
            "bounded_domain": "hilbert_slice_comparison",
            "source_recipes": [tot["run"].recipe_kind, team["run"].recipe_kind],
        },
        metadata={"phase": "dag_v5_phase5"},
    )
    return {
        "boundary_control_packet": boundary,
        "pilot_packet": pilot,
        "study_note": {
            "packet_kind": "atp_domain_pilot",
            "pain_classification": pilot.friction_locus,
            "kernel_change_required": pilot.kernel_change_required,
        },
    }


def build_search_atp_domain_pilot_payload() -> Dict[str, object]:
    example = build_search_atp_domain_pilot()
    return {
        "boundary_control_packet": example["boundary_control_packet"].to_dict(),
        "pilot_packet": example["pilot_packet"].to_dict(),
        "study_note": dict(example["study_note"]),
    }


def build_search_repair_loop_domain_pilot() -> Dict[str, object]:
    codetree = build_dag_v4_codetree_v2_packet()
    repair_loop = build_post_v2_study_17_repair_loop_after_reducer()
    boundary = SearchDomainBoundaryControlPacket(
        packet_id="search.domain.repair_loop.boundary_control.v1",
        domain_kind="repair_loop",
        source_study_keys=["dag_v4_codetree_v2", "post_v2_study_17_repair_loop_after_reducer"],
        source_search_ids=[codetree["run"].search_id, repair_loop["run"].search_id],
        benchmark_manifest_refs=[codetree["recipe_manifest"].benchmark_packet],
        external_surface_refs=[
            "agentic_coder_prototype/conductor/patching.py",
            "agentic_coder_prototype/dialects/opencode_patch.py",
        ],
        preserved_dag_truth=[
            "workspace_ref_lineage",
            "assessment_to_action_chain",
            "selected_candidate_identity",
            "carry_state_visibility",
        ],
        forbidden_domain_semantics=[
            "patch_dialect_truth_in_search_kernel",
            "workspace_mutation_engine_nouns_in_search",
            "conductor_specific_patching_fields_in_search_kernel",
        ],
        control_requirements=[
            "workspace_snapshot_boundary_explicit",
            "patch_application_surface_explicit",
            "verifier_gate_result_stays_helper_local",
        ],
        ontology_blending_forbidden=True,
        notes={"bounded_domain": "toy_patch_pair_and_reducer_repair_loop"},
        metadata={"phase": "dag_v5_phase5"},
    )
    pilot = SearchDomainPilotPacket(
        pilot_id="search.domain.repair_loop.pilot.v1",
        domain_kind="repair_loop",
        source_study_keys=list(boundary.source_study_keys),
        source_search_ids=list(boundary.source_search_ids),
        benchmark_packet_refs=[codetree["recipe_manifest"].benchmark_packet],
        boundary_control_id=boundary.packet_id,
        adapter_surface_refs=list(boundary.external_surface_refs),
        expected_artifact_refs=[
            "artifacts/search/search.replication_v1.codetree_patch/final.json",
            "artifacts/search/search.replication_v1.codetree_patch/debugger.json",
        ],
        success_criteria=[
            "workspace_lineage_stays_reconstructable",
            "patch_surface_stays_adapter_local",
            "no_patch_semantics_import_into_search_kernel",
        ],
        friction_locus="patching_and_workspace_local_only",
        ontology_blending_detected=False,
        kernel_change_required=False,
        notes={
            "repair_gate_selected_candidate_id": repair_loop["outcome"].selected_candidate_id,
            "source_recipe": codetree["run"].recipe_kind,
        },
        metadata={"phase": "dag_v5_phase5"},
    )
    return {
        "boundary_control_packet": boundary,
        "pilot_packet": pilot,
        "study_note": {
            "packet_kind": "repair_loop_domain_pilot",
            "pain_classification": pilot.friction_locus,
            "kernel_change_required": pilot.kernel_change_required,
        },
    }


def build_search_repair_loop_domain_pilot_payload() -> Dict[str, object]:
    example = build_search_repair_loop_domain_pilot()
    return {
        "boundary_control_packet": example["boundary_control_packet"].to_dict(),
        "pilot_packet": example["pilot_packet"].to_dict(),
        "study_note": dict(example["study_note"]),
    }


def build_search_domain_pilot_friction_summary() -> SearchDomainFrictionSummary:
    atp = build_search_atp_domain_pilot()
    repair = build_search_repair_loop_domain_pilot()
    return SearchDomainFrictionSummary(
        summary_id="search.domain.pilot.friction_summary.v1",
        domain_kinds=["atp", "repair_loop"],
        friction_loci=[
            atp["pilot_packet"].friction_locus,
            repair["pilot_packet"].friction_locus,
        ],
        ontology_blending_detected=False,
        repeated_shape_gap_detected=False,
        next_decision="continue_domain_pilots_without_kernel_review",
        notes={
            "atp_boundary_control_id": atp["boundary_control_packet"].packet_id,
            "repair_boundary_control_id": repair["boundary_control_packet"].packet_id,
        },
        metadata={"phase": "dag_v5_phase5"},
    )


def build_search_domain_pilot_friction_summary_payload() -> Dict[str, object]:
    summary = build_search_domain_pilot_friction_summary()
    return summary.to_dict()
