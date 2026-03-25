from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from ..search import SearchAssessment, SearchMessage, SearchRun
from .schema import (
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


def _first_numeric_score(score_vector: Mapping[str, Any]) -> Optional[float]:
    for value in score_vector.values():
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _annotation_from_assessment(assessment: SearchAssessment) -> EvaluationAnnotation:
    subject_kind = "candidate_set" if len(assessment.subject_candidate_ids) > 1 else "candidate"
    text_feedback = None
    summary = assessment.summary_payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        text_feedback = summary.strip()
    return EvaluationAnnotation(
        annotation_id=assessment.assessment_id,
        subject_id=assessment.assessment_id,
        subject_kind=subject_kind,
        channel=assessment.assessment_kind,
        status=assessment.verdict,
        score_value=_first_numeric_score(assessment.score_vector),
        text_feedback=text_feedback,
        artifact_refs=list(assessment.artifact_refs),
        delayed=bool(assessment.metadata.get("delayed")),
        metadata={
            "backend_kind": assessment.backend_kind,
            "schema_kind": assessment.schema_kind,
            "frontier_id": assessment.frontier_id,
            "round_index": assessment.round_index,
            "subject_candidate_ids": list(assessment.subject_candidate_ids),
            **dict(assessment.metadata),
        },
    )


def build_evaluation_annotations_from_search_run(run: SearchRun) -> List[EvaluationAnnotation]:
    return [_annotation_from_assessment(item) for item in run.assessments]


def build_cost_ledger_from_search_run(run: SearchRun) -> CostLedger:
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    total_cost_usd = 0.0

    def _accumulate_usage(usage: Mapping[str, Any]) -> None:
        nonlocal prompt_tokens, completion_tokens, total_tokens, total_cost_usd
        prompt_tokens += int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion_tokens += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        total_tokens += int(
            usage.get("total_tokens")
            or (int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0) + int(usage.get("completion_tokens") or usage.get("output_tokens") or 0))
        )
        total_cost_usd += float(usage.get("cost_usd") or usage.get("total_cost_usd") or 0.0)

    for candidate in run.candidates:
        _accumulate_usage(candidate.usage)
    for message in run.messages:
        _accumulate_usage(message.usage)
    for assessment in run.assessments:
        _accumulate_usage(assessment.usage)

    tool_call_count = sum(1 for item in run.events if item.operator_kind == "execute")
    return CostLedger(
        ledger_id=f"{run.search_id}.rl.cost_ledger.v1",
        token_counts={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
        cost_usd=round(total_cost_usd, 8),
        wallclock_seconds=float(run.metadata.get("wallclock_seconds") or 0.0),
        tool_call_count=tool_call_count,
        metadata={"search_id": run.search_id, "recipe_kind": run.recipe_kind},
    )


def _message_lookup(messages: Sequence[SearchMessage]) -> Dict[str, SearchMessage]:
    return {item.message_id: item for item in messages}


def build_compaction_manifests_from_search_run(run: SearchRun) -> List[CompactionManifest]:
    message_index = _message_lookup(run.messages)
    manifests: List[CompactionManifest] = []
    for carry_state in run.carry_states:
        dropped_fields: List[str] = []
        omitted_artifact_refs: List[str] = []
        for message_id in carry_state.message_ids:
            message = message_index.get(message_id)
            if message is None:
                continue
            dropped_fields.extend(message.dropped_fields)
            omitted_artifact_refs.extend(message.omitted_artifact_refs)
        manifests.append(
            CompactionManifest(
                manifest_id=f"{carry_state.state_id}.compaction_manifest.v1",
                source_message_ids=list(carry_state.message_ids),
                bounded_by=carry_state.bounded_by,
                dropped_fields=dropped_fields,
                omitted_artifact_refs=omitted_artifact_refs,
                fidelity_tier="bounded_summary",
                metadata={
                    "search_id": carry_state.search_id,
                    "token_budget": carry_state.token_budget,
                    **dict(carry_state.metadata),
                },
            )
        )
    return manifests


def _sorted_frontiers(run: SearchRun) -> List[Any]:
    return sorted(run.frontiers, key=lambda item: (item.round_index, item.frontier_id))


def project_search_run_to_trajectory_graph(
    *,
    run: SearchRun,
    rollout_descriptor: RolloutDescriptor,
    environment_descriptor: EnvironmentDescriptor,
    policy_provenance: Sequence[PolicyProvenance],
    evaluation_annotations: Optional[Sequence[EvaluationAnnotation]] = None,
    cost_ledger: Optional[CostLedger] = None,
    compaction_manifests: Optional[Sequence[CompactionManifest]] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> TrajectoryGraph:
    root_track_id = f"{run.search_id}.rl.track.root"
    tracks: List[TrackRecord] = [
        TrackRecord(
            track_id=root_track_id,
            source_kind="search_root",
            source_ref=run.search_id,
            candidate_ids=[item.candidate_id for item in run.candidates],
            metadata={"recipe_kind": run.recipe_kind},
        )
    ]

    frontier_track_ids: Dict[str, str] = {}
    previous_track_id = root_track_id
    for frontier in _sorted_frontiers(run):
        track_id = f"{run.search_id}.rl.track.frontier.{frontier.frontier_id}"
        frontier_track_ids[frontier.frontier_id] = track_id
        tracks.append(
            TrackRecord(
                track_id=track_id,
                source_kind="frontier",
                source_ref=frontier.frontier_id,
                parent_track_id=previous_track_id,
                candidate_ids=list(frontier.candidate_ids),
                metadata={"round_index": frontier.round_index, **dict(frontier.metadata)},
            )
        )
        previous_track_id = track_id

    branch_track_ids: Dict[str, str] = {}
    for branch in run.branch_states:
        track_id = f"{run.search_id}.rl.track.branch.{branch.branch_id}"
        branch_track_ids[branch.candidate_id] = track_id
        tracks.append(
            TrackRecord(
                track_id=track_id,
                source_kind="branch",
                source_ref=branch.branch_id,
                parent_track_id=root_track_id,
                candidate_ids=[branch.candidate_id],
                metadata={
                    "head_snapshot_id": branch.head_snapshot_id,
                    "status": branch.status,
                    **dict(branch.metadata),
                },
            )
        )

    observation_by_candidate_id: Dict[str, str] = {}
    observations: List[ObservationSlice] = []
    for candidate in run.candidates:
        track_id = branch_track_ids.get(candidate.candidate_id) or frontier_track_ids.get(candidate.frontier_id) or root_track_id
        observation_id = f"{run.search_id}.rl.observation.{candidate.candidate_id}"
        visible_refs = [candidate.payload_ref]
        if candidate.workspace_ref:
            visible_refs.append(candidate.workspace_ref)
        if candidate.message_ref:
            visible_refs.append(candidate.message_ref)
        observation_by_candidate_id[candidate.candidate_id] = observation_id
        observations.append(
            ObservationSlice(
                observation_id=observation_id,
                track_id=track_id,
                candidate_id=candidate.candidate_id,
                payload_ref=candidate.payload_ref,
                workspace_ref=candidate.workspace_ref,
                message_ref=candidate.message_ref,
                visible_artifact_refs=visible_refs,
                metadata={
                    "round_index": candidate.round_index,
                    "depth": candidate.depth,
                    "status": candidate.status,
                    **dict(candidate.metadata),
                },
            )
        )

    decisions: List[DecisionRecord] = []
    effects: List[EffectRecord] = []
    causal_edges: List[CausalEdge] = []
    snapshot_ids_by_candidate: Dict[str, List[str]] = {}
    for snapshot in run.workspace_snapshots:
        if snapshot.derived_from_candidate_id:
            snapshot_ids_by_candidate.setdefault(snapshot.derived_from_candidate_id, []).append(snapshot.snapshot_id)

    primary_policy = policy_provenance[0]
    for event in run.events:
        track_id = frontier_track_ids.get(event.frontier_id, root_track_id)
        decision_id = f"{run.search_id}.rl.decision.{event.event_id}"
        effect_id = f"{run.search_id}.rl.effect.{event.event_id}"
        decisions.append(
            DecisionRecord(
                decision_id=decision_id,
                track_id=track_id,
                event_id=event.event_id,
                operator_kind=event.operator_kind,
                policy_id=primary_policy.policy_id,
                input_candidate_ids=list(event.input_candidate_ids),
                output_candidate_ids=list(event.output_candidate_ids),
                assessment_ids=list(event.assessment_ids),
                metadata={"round_index": event.round_index, **dict(event.metadata)},
            )
        )

        artifact_refs: List[str] = []
        workspace_snapshot_ids: List[str] = []
        for candidate_id in event.output_candidate_ids:
            observation_id = observation_by_candidate_id.get(candidate_id)
            if observation_id is not None:
                causal_edges.append(
                    CausalEdge(
                        edge_id=f"{effect_id}.to.{observation_id}",
                        source_id=effect_id,
                        target_id=observation_id,
                        edge_kind="produces_observation",
                    )
                )
            for candidate in run.candidates:
                if candidate.candidate_id == candidate_id:
                    artifact_refs.append(candidate.payload_ref)
                    if candidate.workspace_ref:
                        artifact_refs.append(candidate.workspace_ref)
            workspace_snapshot_ids.extend(snapshot_ids_by_candidate.get(candidate_id, []))

        effects.append(
            EffectRecord(
                effect_id=effect_id,
                track_id=track_id,
                event_id=event.event_id,
                effect_kind=event.operator_kind,
                produced_candidate_ids=list(event.output_candidate_ids),
                artifact_refs=artifact_refs,
                workspace_snapshot_ids=workspace_snapshot_ids,
                metadata={"round_index": event.round_index},
            )
        )

        for candidate_id in event.input_candidate_ids:
            observation_id = observation_by_candidate_id.get(candidate_id)
            if observation_id is None:
                continue
            causal_edges.append(
                CausalEdge(
                    edge_id=f"{observation_id}.to.{decision_id}",
                    source_id=observation_id,
                    target_id=decision_id,
                    edge_kind="observed_before_decision",
                )
            )
        causal_edges.append(
            CausalEdge(
                edge_id=f"{decision_id}.to.{effect_id}",
                source_id=decision_id,
                target_id=effect_id,
                edge_kind="decision_causes_effect",
            )
        )
        for assessment_id in event.assessment_ids:
            causal_edges.append(
                CausalEdge(
                    edge_id=f"{decision_id}.to.annotation.{assessment_id}",
                    source_id=decision_id,
                    target_id=assessment_id,
                    edge_kind="decision_triggers_evaluation",
                )
            )

    return TrajectoryGraph(
        graph_id=f"{run.search_id}.rl.trajectory_graph.v1",
        rollout_descriptor=rollout_descriptor,
        environment_descriptor=environment_descriptor,
        policy_provenance=list(policy_provenance),
        tracks=tracks,
        observations=observations,
        decisions=decisions,
        effects=effects,
        causal_edges=causal_edges,
        evaluation_annotations=list(evaluation_annotations or build_evaluation_annotations_from_search_run(run)),
        cost_ledger=cost_ledger if cost_ledger is not None else build_cost_ledger_from_search_run(run),
        compaction_manifests=list(compaction_manifests or build_compaction_manifests_from_search_run(run)),
        metadata={"search_id": run.search_id, "recipe_kind": run.recipe_kind, **dict(metadata or {})},
    )
