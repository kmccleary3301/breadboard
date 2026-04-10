from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Sequence

from .export import SearchTrajectoryExport, export_search_trajectory
from .fidelity import AssessmentLineagePacket, ReplayExportIntegrityPacket
from .schema import SearchRun


def _sorted_unique_texts(values: Sequence[Any]) -> tuple[str, ...]:
    texts = {str(item or "").strip() for item in values}
    return tuple(sorted(text for text in texts if text))


def _coerce_run(source: SearchRun | Mapping[str, Any]) -> SearchRun:
    if isinstance(source, SearchRun):
        return source
    return SearchRun.from_dict(source)


def _coerce_trajectory_export(
    source: SearchRun | SearchTrajectoryExport | Mapping[str, Any],
) -> SearchTrajectoryExport:
    if isinstance(source, SearchTrajectoryExport):
        return source
    if isinstance(source, SearchRun):
        return export_search_trajectory(source)
    if "steps" in source and "search_id" in source and "recipe_kind" in source:
        return SearchTrajectoryExport.from_dict(source)
    return export_search_trajectory(_coerce_run(source))


def _coerce_assessment_lineage_packet(
    packet: AssessmentLineagePacket | Mapping[str, Any],
) -> AssessmentLineagePacket:
    if isinstance(packet, AssessmentLineagePacket):
        return packet
    return AssessmentLineagePacket.from_dict(packet)


def _coerce_replay_export_integrity_packet(
    packet: ReplayExportIntegrityPacket | Mapping[str, Any],
) -> ReplayExportIntegrityPacket:
    if isinstance(packet, ReplayExportIntegrityPacket):
        return packet
    return ReplayExportIntegrityPacket.from_dict(packet)


@dataclass(frozen=True)
class SearchLineageView:
    source_kind: str
    source_id: str
    recipe_kind: str
    event_count: int
    operator_kinds: tuple[str, ...]
    frontier_ids: tuple[str, ...]
    branch_ids: tuple[str, ...]
    carry_state_ids: tuple[str, ...]
    assessment_ids: tuple[str, ...]
    selected_candidate_id: str | None
    first_event_id: str
    last_event_id: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "recipe_kind": self.recipe_kind,
            "event_count": self.event_count,
            "operator_kinds": list(self.operator_kinds),
            "frontier_ids": list(self.frontier_ids),
            "branch_ids": list(self.branch_ids),
            "carry_state_ids": list(self.carry_state_ids),
            "assessment_ids": list(self.assessment_ids),
            "selected_candidate_id": self.selected_candidate_id,
            "first_event_id": self.first_event_id,
            "last_event_id": self.last_event_id,
        }


@dataclass(frozen=True)
class SearchAssessmentChainView:
    packet_id: str
    target_family: str
    topology_class: str
    assessment_kinds: tuple[str, ...]
    linked_assessment_ids: tuple[str, ...]
    linked_action_ids: tuple[str, ...]
    action_link_count: int
    mixed_chain_reconstructable: bool

    def to_dict(self) -> Dict[str, object]:
        return {
            "packet_id": self.packet_id,
            "target_family": self.target_family,
            "topology_class": self.topology_class,
            "assessment_kinds": list(self.assessment_kinds),
            "linked_assessment_ids": list(self.linked_assessment_ids),
            "linked_action_ids": list(self.linked_action_ids),
            "action_link_count": self.action_link_count,
            "mixed_chain_reconstructable": self.mixed_chain_reconstructable,
        }


@dataclass(frozen=True)
class SearchReplayExportSummary:
    source_label: str
    source_kind: str
    search_id: str
    recipe_kind: str
    target_family: str | None
    topology_class: str | None
    export_modes: tuple[str, ...]
    preserved_semantics: tuple[str, ...]
    lost_semantics: tuple[str, ...]
    shadow_assumptions_required: bool
    step_count: int
    reward_signal_count: int
    selected_candidate_id: str | None
    first_event_id: str
    last_event_id: str

    def to_dict(self) -> Dict[str, object]:
        return {
            "source_label": self.source_label,
            "source_kind": self.source_kind,
            "search_id": self.search_id,
            "recipe_kind": self.recipe_kind,
            "target_family": self.target_family,
            "topology_class": self.topology_class,
            "export_modes": list(self.export_modes),
            "preserved_semantics": list(self.preserved_semantics),
            "lost_semantics": list(self.lost_semantics),
            "shadow_assumptions_required": self.shadow_assumptions_required,
            "step_count": self.step_count,
            "reward_signal_count": self.reward_signal_count,
            "selected_candidate_id": self.selected_candidate_id,
            "first_event_id": self.first_event_id,
            "last_event_id": self.last_event_id,
        }


@dataclass(frozen=True)
class SearchReplayExportDiff:
    left_label: str
    right_label: str
    shared_preserved_semantics: tuple[str, ...]
    left_only_preserved_semantics: tuple[str, ...]
    right_only_preserved_semantics: tuple[str, ...]
    shared_lost_semantics: tuple[str, ...]
    left_only_lost_semantics: tuple[str, ...]
    right_only_lost_semantics: tuple[str, ...]
    step_count_delta: int
    reward_signal_delta: int
    selected_candidate_changed: bool
    topology_class_changed: bool
    shadow_assumptions_changed: bool

    def to_dict(self) -> Dict[str, object]:
        return {
            "left_label": self.left_label,
            "right_label": self.right_label,
            "shared_preserved_semantics": list(self.shared_preserved_semantics),
            "left_only_preserved_semantics": list(self.left_only_preserved_semantics),
            "right_only_preserved_semantics": list(self.right_only_preserved_semantics),
            "shared_lost_semantics": list(self.shared_lost_semantics),
            "left_only_lost_semantics": list(self.left_only_lost_semantics),
            "right_only_lost_semantics": list(self.right_only_lost_semantics),
            "step_count_delta": self.step_count_delta,
            "reward_signal_delta": self.reward_signal_delta,
            "selected_candidate_changed": self.selected_candidate_changed,
            "topology_class_changed": self.topology_class_changed,
            "shadow_assumptions_changed": self.shadow_assumptions_changed,
        }


def build_search_lineage_view(
    source: SearchRun | SearchTrajectoryExport | Mapping[str, Any],
) -> SearchLineageView:
    trajectory = _coerce_trajectory_export(source)
    steps = trajectory.steps
    return SearchLineageView(
        source_kind="trajectory_export" if not isinstance(source, SearchRun) else "run",
        source_id=trajectory.search_id,
        recipe_kind=trajectory.recipe_kind,
        event_count=len(steps),
        operator_kinds=_sorted_unique_texts(step.operator_kind for step in steps),
        frontier_ids=_sorted_unique_texts(step.frontier_id for step in steps),
        branch_ids=_sorted_unique_texts(branch_id for step in steps for branch_id in step.branch_ids),
        carry_state_ids=_sorted_unique_texts(
            carry_state_id for step in steps for carry_state_id in step.carry_state_ids
        ),
        assessment_ids=_sorted_unique_texts(
            assessment_id for step in steps for assessment_id in step.assessment_ids
        ),
        selected_candidate_id=trajectory.selected_candidate_id,
        first_event_id=steps[0].event_id,
        last_event_id=steps[-1].event_id,
    )


def build_search_assessment_chain_view(
    packet: AssessmentLineagePacket | Mapping[str, Any],
) -> SearchAssessmentChainView:
    lineage_packet = _coerce_assessment_lineage_packet(packet)
    return SearchAssessmentChainView(
        packet_id=lineage_packet.packet_id,
        target_family=lineage_packet.target_family,
        topology_class=lineage_packet.topology_class,
        assessment_kinds=tuple(lineage_packet.assessment_kinds),
        linked_assessment_ids=_sorted_unique_texts(
            link.get("assessment_id") for link in lineage_packet.action_links
        ),
        linked_action_ids=_sorted_unique_texts(
            link.get("action_id") for link in lineage_packet.action_links
        ),
        action_link_count=len(lineage_packet.action_links),
        mixed_chain_reconstructable=lineage_packet.mixed_chain_reconstructable,
    )


def build_search_replay_export_summary(
    source: SearchRun | SearchTrajectoryExport | Mapping[str, Any],
    *,
    integrity_packet: ReplayExportIntegrityPacket | Mapping[str, Any] | None = None,
) -> SearchReplayExportSummary:
    trajectory = _coerce_trajectory_export(source)
    replay_packet = (
        _coerce_replay_export_integrity_packet(integrity_packet)
        if integrity_packet is not None
        else None
    )
    return SearchReplayExportSummary(
        source_label=replay_packet.packet_id if replay_packet is not None else trajectory.search_id,
        source_kind="replay_export_integrity_packet" if replay_packet is not None else "trajectory_export",
        search_id=trajectory.search_id,
        recipe_kind=trajectory.recipe_kind,
        target_family=replay_packet.target_family if replay_packet is not None else None,
        topology_class=replay_packet.topology_class if replay_packet is not None else None,
        export_modes=tuple(replay_packet.export_modes) if replay_packet is not None else ("trajectory_export",),
        preserved_semantics=tuple(replay_packet.preserved_semantics) if replay_packet is not None else tuple(),
        lost_semantics=tuple(replay_packet.lost_semantics) if replay_packet is not None else tuple(),
        shadow_assumptions_required=replay_packet.shadow_assumptions_required if replay_packet is not None else False,
        step_count=len(trajectory.steps),
        reward_signal_count=len(trajectory.reward_signals),
        selected_candidate_id=trajectory.selected_candidate_id,
        first_event_id=trajectory.steps[0].event_id,
        last_event_id=trajectory.steps[-1].event_id,
    )


def diff_search_replay_export_summaries(
    left: SearchReplayExportSummary,
    right: SearchReplayExportSummary,
) -> SearchReplayExportDiff:
    left_preserved = set(left.preserved_semantics)
    right_preserved = set(right.preserved_semantics)
    left_lost = set(left.lost_semantics)
    right_lost = set(right.lost_semantics)
    return SearchReplayExportDiff(
        left_label=left.source_label,
        right_label=right.source_label,
        shared_preserved_semantics=tuple(sorted(left_preserved & right_preserved)),
        left_only_preserved_semantics=tuple(sorted(left_preserved - right_preserved)),
        right_only_preserved_semantics=tuple(sorted(right_preserved - left_preserved)),
        shared_lost_semantics=tuple(sorted(left_lost & right_lost)),
        left_only_lost_semantics=tuple(sorted(left_lost - right_lost)),
        right_only_lost_semantics=tuple(sorted(right_lost - left_lost)),
        step_count_delta=right.step_count - left.step_count,
        reward_signal_delta=right.reward_signal_count - left.reward_signal_count,
        selected_candidate_changed=left.selected_candidate_id != right.selected_candidate_id,
        topology_class_changed=left.topology_class != right.topology_class,
        shadow_assumptions_changed=left.shadow_assumptions_required != right.shadow_assumptions_required,
    )
