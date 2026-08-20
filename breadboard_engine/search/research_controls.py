from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence

from .domain_pilots import (
    build_search_atp_domain_pilot,
    build_search_repair_loop_domain_pilot,
)
from .deployment_readiness import build_search_ctrees_boundary_canary
from .examples import build_dag_v4_tot_v2_packet


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
class SearchSelectiveResearchControlPacket:
    packet_id: str
    control_family: str
    source_study_keys: List[str]
    source_search_ids: List[str]
    external_surface_refs: List[str]
    preserved_dag_truth: List[str]
    control_question: str
    expected_value: str
    anti_goals: List[str]
    decision: str
    kernel_change_required: bool
    notes: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "packet_id", _require_text(self.packet_id, "packet_id"))
        object.__setattr__(self, "control_family", _require_text(self.control_family, "control_family"))
        object.__setattr__(self, "source_study_keys", _copy_text_list(self.source_study_keys))
        object.__setattr__(self, "source_search_ids", _copy_text_list(self.source_search_ids))
        object.__setattr__(self, "external_surface_refs", _copy_text_list(self.external_surface_refs))
        object.__setattr__(self, "preserved_dag_truth", _copy_text_list(self.preserved_dag_truth))
        object.__setattr__(self, "control_question", _require_text(self.control_question, "control_question"))
        object.__setattr__(self, "expected_value", _require_text(self.expected_value, "expected_value"))
        object.__setattr__(self, "anti_goals", _copy_text_list(self.anti_goals))
        object.__setattr__(self, "decision", _require_text(self.decision, "decision"))
        object.__setattr__(self, "kernel_change_required", bool(self.kernel_change_required))
        object.__setattr__(self, "notes", _copy_mapping(self.notes))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "control_family": self.control_family,
            "source_study_keys": list(self.source_study_keys),
            "source_search_ids": list(self.source_search_ids),
            "external_surface_refs": list(self.external_surface_refs),
            "preserved_dag_truth": list(self.preserved_dag_truth),
            "control_question": self.control_question,
            "expected_value": self.expected_value,
            "anti_goals": list(self.anti_goals),
            "decision": self.decision,
            "kernel_change_required": self.kernel_change_required,
            "notes": dict(self.notes),
            "metadata": dict(self.metadata),
        }


def build_search_tool_planning_tree_control_packet() -> Dict[str, object]:
    tot = build_dag_v4_tot_v2_packet()
    repair = build_search_repair_loop_domain_pilot()
    packet = SearchSelectiveResearchControlPacket(
        packet_id="search.research_control.tool_planning_tree.v1",
        control_family="tool_planning_tree",
        source_study_keys=[
            "dag_replication_v1_tot_game24",
            "dag_v5_repair_loop_domain_pilot",
        ],
        source_search_ids=[
            tot["run"].search_id,
            repair["pilot_packet"].pilot_id,
        ],
        external_surface_refs=[
            "agentic_coder_prototype/conductor/patching.py",
            "agentic_coder_prototype/dialects/opencode_patch.py",
            "agentic_coder_prototype/api/cli_bridge/session_runner.py",
        ],
        preserved_dag_truth=[
            "frontier_visibility",
            "branch_reopen_visibility",
            "selected_candidate_identity",
            "assessment_lineage_visibility",
        ],
        control_question=(
            "Can tool-planning tree pressure be expressed as a bounded control family "
            "without importing tool semantics into the DAG kernel?"
        ),
        expected_value=(
            "Adds a real tool-use planning control lens while preserving DAG as a substrate "
            "for branching, adjudication, and repair-loop orchestration only."
        ),
        anti_goals=[
            "tool_catalog_nouns_in_search_kernel",
            "executor_specific_scheduler_fields_in_search",
            "patching_semantics_as_kernel_truth",
        ],
        decision="bounded_control_family_only",
        kernel_change_required=False,
        notes={
            "why_now": "phase_7_selective_value_after_domain_pilots",
            "consumer_value": "bridges reasoning-tree pressure to repair-loop planning without new DAG nouns",
        },
        metadata={"phase": "dag_v5_phase7"},
    )
    return {
        "control_packet": packet,
        "decision": packet.decision,
        "artifact_refs": [
            "artifacts/research_controls/tool_planning_tree/packet_v1.md",
            "artifacts/research_controls/tool_planning_tree/control_summary_v1.json",
        ],
        "notes": {
            "recommended_next_step": "keep_as_control_family",
            "kernel_review_opened": False,
        },
    }


def build_search_tool_planning_tree_control_packet_payload() -> Dict[str, object]:
    example = build_search_tool_planning_tree_control_packet()
    return {
        "control_packet": example["control_packet"].to_dict(),
        "decision": example["decision"],
        "artifact_refs": list(example["artifact_refs"]),
        "notes": dict(example["notes"]),
    }


def build_search_general_agent_control_packet() -> Dict[str, object]:
    atp = build_search_atp_domain_pilot()
    repair = build_search_repair_loop_domain_pilot()
    packet = SearchSelectiveResearchControlPacket(
        packet_id="search.research_control.general_agent.v1",
        control_family="general_agent_control",
        source_study_keys=[
            "dag_v5_atp_domain_pilot",
            "dag_v5_repair_loop_domain_pilot",
        ],
        source_search_ids=[
            atp["pilot_packet"].pilot_id,
            repair["pilot_packet"].pilot_id,
        ],
        external_surface_refs=[
            "scripts/run_bb_atp_adapter_slice_v1.py",
            "agentic_coder_prototype/api/cli_bridge/atp_router.py",
            "agentic_coder_prototype/conductor/patching.py",
        ],
        preserved_dag_truth=[
            "consumer_handoff_visibility",
            "replay_export_integrity",
            "branch_and_workspace_provenance",
            "benchmark_control_honesty",
        ],
        control_question=(
            "Can a general-agent control lens separate evaluator or adapter-local failures "
            "from DAG-local failures across distinct domain pilots?"
        ),
        expected_value=(
            "Adds a cross-domain control lens that improves attribution without inflating "
            "DAG semantics or widening the packet universe."
        ),
        anti_goals=[
            "general_agentbench_semantics_in_kernel",
            "consumer_local_pain_misclassified_as_dag_truth",
            "benchmark_sprawl_as_value_proxy",
        ],
        decision="control_lens_only",
        kernel_change_required=False,
        notes={
            "why_now": "phase_7_selective_value_after_atp_and_repair_pilots",
            "consumer_value": "improves locus classification across domain pilots",
        },
        metadata={"phase": "dag_v5_phase7"},
    )
    return {
        "control_packet": packet,
        "decision": packet.decision,
        "artifact_refs": [
            "artifacts/research_controls/general_agent/packet_v1.md",
            "artifacts/research_controls/general_agent/control_matrix_v1.json",
        ],
        "notes": {
            "recommended_next_step": "use_as_control_lens_only",
            "kernel_review_opened": False,
        },
    }


def build_search_general_agent_control_packet_payload() -> Dict[str, object]:
    example = build_search_general_agent_control_packet()
    return {
        "control_packet": example["control_packet"].to_dict(),
        "decision": example["decision"],
        "artifact_refs": list(example["artifact_refs"]),
        "notes": dict(example["notes"]),
    }


def build_search_ctrees_boundary_attribution_control_packet() -> Dict[str, object]:
    canary = build_search_ctrees_boundary_canary()
    packet = SearchSelectiveResearchControlPacket(
        packet_id="search.research_control.ctrees_boundary_attribution.v1",
        control_family="ctrees_boundary_attribution",
        source_study_keys=[
            "dag_v6_ctrees_boundary_canary",
            "dag_v6_atp_deployment_readiness",
            "dag_v6_repair_loop_deployment_readiness",
        ],
        source_search_ids=[
            canary.packet_id,
            "search.domain.atp.pilot.v1",
            "search.domain.repair_loop.pilot.v1",
        ],
        external_surface_refs=[
            "agentic_coder_prototype/ctrees/phase_machine.py",
            "agentic_coder_prototype/ctrees/branch_receipt_contract.py",
            "agentic_coder_prototype/ctrees/finish_closure_contract.py",
            "agentic_coder_prototype/ctrees/helper_rehydration.py",
            "agentic_coder_prototype/ctrees/downstream_task_eval.py",
        ],
        preserved_dag_truth=list(canary.preserved_dag_truth),
        control_question=(
            "Can C-Trees-facing pressure be attributed cleanly to C-Trees-local contract/closure needs "
            "rather than misclassified as missing DAG truth?"
        ),
        expected_value=(
            "Adds a bounded attribution lens for DAG→C-Trees composition without importing branch receipts, "
            "finish closure, or helper rehydration semantics into DAG."
        ),
        anti_goals=[
            "ctrees_branch_receipts_as_dag_truth",
            "ctrees_finish_closure_nouns_in_dag_kernel",
            "ctrees_helper_rehydration_contracts_in_dag",
        ],
        decision="ctrees_boundary_control_only",
        kernel_change_required=False,
        notes={
            "why_now": "phase_6_bounded_ctrees_canary_following_atp_and_repair_deployment",
            "canary_packet_id": canary.packet_id,
            "expected_locus": canary.dominant_locus,
        },
        metadata={"phase": "dag_v6_phase5"},
    )
    return {
        "control_packet": packet,
        "decision": packet.decision,
        "artifact_refs": [
            "artifacts/research_controls/ctrees_boundary_attribution/packet_v1.md",
            "artifacts/research_controls/ctrees_boundary_attribution/control_matrix_v1.json",
        ],
        "notes": {
            "recommended_next_step": "keep_ctrees_as_boundary_canary",
            "kernel_review_opened": False,
        },
    }


def build_search_ctrees_boundary_attribution_control_packet_payload() -> Dict[str, object]:
    example = build_search_ctrees_boundary_attribution_control_packet()
    return {
        "control_packet": example["control_packet"].to_dict(),
        "decision": example["decision"],
        "artifact_refs": list(example["artifact_refs"]),
        "notes": dict(example["notes"]),
    }
