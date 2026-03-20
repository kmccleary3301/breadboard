from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .schema import SearchRun


def _copy_mapping(value: Mapping[str, Any] | None) -> Dict[str, Any]:
    return dict(value or {})


def _copy_text_list(values: Sequence[Any] | None) -> List[str]:
    copied: List[str] = []
    for item in values or []:
        text = str(item or "").strip()
        if text:
            copied.append(text)
    return copied


@dataclass(frozen=True)
class SearchRewardSignal:
    signal_id: str
    search_id: str
    event_id: str
    operator_kind: str
    scope: str
    channel: str
    value: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        signal_id = str(self.signal_id or "").strip()
        search_id = str(self.search_id or "").strip()
        event_id = str(self.event_id or "").strip()
        operator_kind = str(self.operator_kind or "").strip()
        scope = str(self.scope or "").strip().lower()
        channel = str(self.channel or "").strip()
        if not signal_id:
            raise ValueError("signal_id must be non-empty")
        if not search_id:
            raise ValueError("search_id must be non-empty")
        if not event_id:
            raise ValueError("event_id must be non-empty")
        if not operator_kind:
            raise ValueError("operator_kind must be non-empty")
        if scope not in {"local", "global"}:
            raise ValueError("scope must be one of: ['global', 'local']")
        if not channel:
            raise ValueError("channel must be non-empty")
        object.__setattr__(self, "signal_id", signal_id)
        object.__setattr__(self, "search_id", search_id)
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "operator_kind", operator_kind)
        object.__setattr__(self, "scope", scope)
        object.__setattr__(self, "channel", channel)
        object.__setattr__(self, "value", float(self.value))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "search_id": self.search_id,
            "event_id": self.event_id,
            "operator_kind": self.operator_kind,
            "scope": self.scope,
            "channel": self.channel,
            "value": self.value,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "SearchRewardSignal":
        return SearchRewardSignal(
            signal_id=data.get("signal_id") or "",
            search_id=data.get("search_id") or "",
            event_id=data.get("event_id") or "",
            operator_kind=data.get("operator_kind") or "",
            scope=data.get("scope") or "",
            channel=data.get("channel") or "",
            value=float(data.get("value") or 0.0),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class SearchTrajectoryStep:
    event_id: str
    operator_kind: str
    frontier_id: str
    round_index: int
    input_candidate_ids: List[str]
    output_candidate_ids: List[str]
    message_ids: List[str] = field(default_factory=list)
    assessment_ids: List[str] = field(default_factory=list)
    carry_state_ids: List[str] = field(default_factory=list)
    branch_ids: List[str] = field(default_factory=list)
    reward_signal_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        event_id = str(self.event_id or "").strip()
        operator_kind = str(self.operator_kind or "").strip()
        frontier_id = str(self.frontier_id or "").strip()
        round_index = int(self.round_index)
        if not event_id:
            raise ValueError("event_id must be non-empty")
        if not operator_kind:
            raise ValueError("operator_kind must be non-empty")
        if not frontier_id:
            raise ValueError("frontier_id must be non-empty")
        if round_index < 0:
            raise ValueError("round_index must be >= 0")
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "operator_kind", operator_kind)
        object.__setattr__(self, "frontier_id", frontier_id)
        object.__setattr__(self, "round_index", round_index)
        object.__setattr__(self, "input_candidate_ids", _copy_text_list(self.input_candidate_ids))
        object.__setattr__(self, "output_candidate_ids", _copy_text_list(self.output_candidate_ids))
        object.__setattr__(self, "message_ids", _copy_text_list(self.message_ids))
        object.__setattr__(self, "assessment_ids", _copy_text_list(self.assessment_ids))
        object.__setattr__(self, "carry_state_ids", _copy_text_list(self.carry_state_ids))
        object.__setattr__(self, "branch_ids", _copy_text_list(self.branch_ids))
        object.__setattr__(self, "reward_signal_ids", _copy_text_list(self.reward_signal_ids))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "operator_kind": self.operator_kind,
            "frontier_id": self.frontier_id,
            "round_index": self.round_index,
            "input_candidate_ids": list(self.input_candidate_ids),
            "output_candidate_ids": list(self.output_candidate_ids),
            "message_ids": list(self.message_ids),
            "assessment_ids": list(self.assessment_ids),
            "carry_state_ids": list(self.carry_state_ids),
            "branch_ids": list(self.branch_ids),
            "reward_signal_ids": list(self.reward_signal_ids),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "SearchTrajectoryStep":
        return SearchTrajectoryStep(
            event_id=data.get("event_id") or "",
            operator_kind=data.get("operator_kind") or "",
            frontier_id=data.get("frontier_id") or "",
            round_index=int(data.get("round_index") or 0),
            input_candidate_ids=list(data.get("input_candidate_ids") or []),
            output_candidate_ids=list(data.get("output_candidate_ids") or []),
            message_ids=list(data.get("message_ids") or []),
            assessment_ids=list(data.get("assessment_ids") or []),
            carry_state_ids=list(data.get("carry_state_ids") or []),
            branch_ids=list(data.get("branch_ids") or []),
            reward_signal_ids=list(data.get("reward_signal_ids") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class SearchTrajectoryExport:
    search_id: str
    recipe_kind: str
    steps: List[SearchTrajectoryStep]
    reward_signals: List[SearchRewardSignal]
    selected_candidate_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        search_id = str(self.search_id or "").strip()
        recipe_kind = str(self.recipe_kind or "").strip()
        if not search_id:
            raise ValueError("search_id must be non-empty")
        if not recipe_kind:
            raise ValueError("recipe_kind must be non-empty")
        object.__setattr__(self, "search_id", search_id)
        object.__setattr__(self, "recipe_kind", recipe_kind)
        object.__setattr__(
            self,
            "steps",
            [item if isinstance(item, SearchTrajectoryStep) else SearchTrajectoryStep.from_dict(item) for item in self.steps],
        )
        object.__setattr__(
            self,
            "reward_signals",
            [
                item if isinstance(item, SearchRewardSignal) else SearchRewardSignal.from_dict(item)
                for item in self.reward_signals
            ],
        )
        object.__setattr__(
            self,
            "selected_candidate_id",
            str(self.selected_candidate_id).strip() if self.selected_candidate_id else None,
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.steps:
            raise ValueError("steps must contain at least one trajectory step")

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "search_id": self.search_id,
            "recipe_kind": self.recipe_kind,
            "steps": [item.to_dict() for item in self.steps],
            "reward_signals": [item.to_dict() for item in self.reward_signals],
            "metadata": dict(self.metadata),
        }
        if self.selected_candidate_id:
            payload["selected_candidate_id"] = self.selected_candidate_id
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "SearchTrajectoryExport":
        return SearchTrajectoryExport(
            search_id=data.get("search_id") or "",
            recipe_kind=data.get("recipe_kind") or "",
            steps=[SearchTrajectoryStep.from_dict(item) for item in data.get("steps") or []],
            reward_signals=[SearchRewardSignal.from_dict(item) for item in data.get("reward_signals") or []],
            selected_candidate_id=data.get("selected_candidate_id"),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class SearchOfflineDataset:
    dataset_id: str
    trajectories: List[SearchTrajectoryExport]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        dataset_id = str(self.dataset_id or "").strip()
        if not dataset_id:
            raise ValueError("dataset_id must be non-empty")
        object.__setattr__(self, "dataset_id", dataset_id)
        object.__setattr__(
            self,
            "trajectories",
            [
                item if isinstance(item, SearchTrajectoryExport) else SearchTrajectoryExport.from_dict(item)
                for item in self.trajectories
            ],
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.trajectories:
            raise ValueError("trajectories must contain at least one export")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "trajectories": [item.to_dict() for item in self.trajectories],
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "SearchOfflineDataset":
        return SearchOfflineDataset(
            dataset_id=data.get("dataset_id") or "",
            trajectories=[SearchTrajectoryExport.from_dict(item) for item in data.get("trajectories") or []],
            metadata=dict(data.get("metadata") or {}),
        )


def build_default_reward_signals(run: SearchRun) -> List[SearchRewardSignal]:
    signals: List[SearchRewardSignal] = []
    for index, event in enumerate(run.events, start=1):
        if event.operator_kind == "discard":
            value = -0.15
            channel = "branch_discard_penalty"
        elif event.operator_kind == "merge":
            value = 0.2
            channel = "branch_merge_gain"
        elif event.operator_kind == "compact":
            value = 0.05
            channel = "carry_efficiency"
        elif event.operator_kind == "select":
            value = 0.25
            channel = "selection_gain"
        else:
            value = 0.1
            channel = "operator_progress"
        signals.append(
            SearchRewardSignal(
                signal_id=f"{run.search_id}.reward.{index}",
                search_id=run.search_id,
                event_id=event.event_id,
                operator_kind=event.operator_kind,
                scope="local",
                channel=channel,
                value=value,
                metadata={"round_index": event.round_index},
            )
        )
    signals.append(
        SearchRewardSignal(
            signal_id=f"{run.search_id}.reward.global.final",
            search_id=run.search_id,
            event_id=run.events[-1].event_id,
            operator_kind=run.events[-1].operator_kind,
            scope="global",
            channel="final_search_outcome",
            value=0.4 if run.selected_candidate_id else 0.0,
            metadata={"selected_candidate_id": run.selected_candidate_id},
        )
    )
    return signals


def export_search_trajectory(
    run: SearchRun,
    *,
    reward_signals: Optional[Sequence[SearchRewardSignal]] = None,
    metadata: Mapping[str, Any] | None = None,
) -> SearchTrajectoryExport:
    signals = list(reward_signals) if reward_signals is not None else build_default_reward_signals(run)
    reward_ids_by_event: Dict[str, List[str]] = {}
    for signal in signals:
        reward_ids_by_event.setdefault(signal.event_id, []).append(signal.signal_id)

    steps: List[SearchTrajectoryStep] = []
    for event in run.events:
        branch_ids = event.metadata.get("branch_ids") or []
        if not branch_ids and event.metadata.get("branch_id"):
            branch_ids = [event.metadata["branch_id"]]
        carry_state_ids = []
        if event.metadata.get("carry_state_id"):
            carry_state_ids.append(str(event.metadata["carry_state_id"]))
        steps.append(
            SearchTrajectoryStep(
                event_id=event.event_id,
                operator_kind=event.operator_kind,
                frontier_id=event.frontier_id,
                round_index=event.round_index,
                input_candidate_ids=list(event.input_candidate_ids),
                output_candidate_ids=list(event.output_candidate_ids),
                message_ids=list(event.message_ids),
                assessment_ids=list(event.assessment_ids),
                carry_state_ids=carry_state_ids,
                branch_ids=list(branch_ids),
                reward_signal_ids=reward_ids_by_event.get(event.event_id, []),
                metadata=dict(event.metadata),
            )
        )

    return SearchTrajectoryExport(
        search_id=run.search_id,
        recipe_kind=run.recipe_kind,
        steps=steps,
        reward_signals=signals,
        selected_candidate_id=run.selected_candidate_id,
        metadata={
            "trajectory_kind": "search_runtime_v1",
            "operator_conditioned": True,
            **dict(run.metadata),
            **dict(metadata or {}),
        },
    )


def build_search_offline_dataset(
    trajectories: Sequence[SearchTrajectoryExport],
    *,
    dataset_id: str,
    metadata: Mapping[str, Any] | None = None,
) -> SearchOfflineDataset:
    return SearchOfflineDataset(
        dataset_id=dataset_id,
        trajectories=list(trajectories),
        metadata={"trajectory_count": len(trajectories), **dict(metadata or {})},
    )
