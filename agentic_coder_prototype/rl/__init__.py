"""Trainer-neutral RL overlay contracts for BreadBoard runtime truth."""

from .examples import (
    build_rl_v1_boundary_audit_packet,
    build_rl_v1_boundary_audit_packet_payload,
    build_rl_v1_contract_pack_example,
    build_rl_v1_contract_pack_example_payload,
    build_rl_v1_trajectory_graph_shell_example,
    build_rl_v1_trajectory_graph_shell_example_payload,
)
from .graph import (
    build_compaction_manifests_from_search_run,
    build_cost_ledger_from_search_run,
    build_evaluation_annotations_from_search_run,
    project_search_run_to_trajectory_graph,
)
from .schema import (
    AdapterCapabilities,
    CausalEdge,
    CompactionManifest,
    CostLedger,
    DecisionRecord,
    EffectRecord,
    EnvironmentDescriptor,
    EvaluationAnnotation,
    ObservationSlice,
    PolicyProvenance,
    RolloutDescriptor,
    TrackRecord,
    TrajectoryGraph,
)

__all__ = [
    "AdapterCapabilities",
    "CausalEdge",
    "CompactionManifest",
    "CostLedger",
    "DecisionRecord",
    "EffectRecord",
    "EnvironmentDescriptor",
    "EvaluationAnnotation",
    "ObservationSlice",
    "PolicyProvenance",
    "RolloutDescriptor",
    "TrackRecord",
    "TrajectoryGraph",
    "build_compaction_manifests_from_search_run",
    "build_cost_ledger_from_search_run",
    "build_evaluation_annotations_from_search_run",
    "build_rl_v1_boundary_audit_packet",
    "build_rl_v1_boundary_audit_packet_payload",
    "build_rl_v1_contract_pack_example",
    "build_rl_v1_contract_pack_example_payload",
    "build_rl_v1_trajectory_graph_shell_example",
    "build_rl_v1_trajectory_graph_shell_example_payload",
    "project_search_run_to_trajectory_graph",
]
