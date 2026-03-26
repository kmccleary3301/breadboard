"""Schema definitions for multi-agent orchestration (Phase 8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .coordination import BLOCKED_RECOMMENDED_ACTIONS, DIRECTIVE_CODES, REVIEWER_ROLES


_COORDINATION_ALLOWED_KEYS = frozenset(
    {
        "mission_owner_role",
        "legacy_completion_sources",
        "preserve_legacy_wake_conditions",
        "done",
        "review",
        "merge",
        "intervention",
    }
)
_COORDINATION_DONE_ALLOWED_KEYS = frozenset(
    {
        "require_deliverable_refs",
        "require_all_required_refs",
        "require_no_open_required_children",
    }
)
_COORDINATION_REVIEW_ALLOWED_KEYS = frozenset(
    {
        "explicit_verdicts",
        "allowed_reviewer_roles",
        "allowed_blocked_actions",
        "no_progress_action",
        "retryable_failure_action",
        "verification_result_contract",
    }
)
_COORDINATION_MERGE_ALLOWED_KEYS = frozenset({"reducer_result_contract"})
_COORDINATION_INTERVENTION_ALLOWED_KEYS = frozenset(
    {
        "host_allowed_actions",
        "require_evidence_refs",
        "require_supervisor_escalate",
        "support_claim_limited_actions",
    }
)


def _reject_unknown_keys(raw: Dict[str, Any], *, section: str, allowed: frozenset[str]) -> None:
    unknown = sorted(str(key) for key in raw.keys() if str(key) not in allowed)
    if unknown:
        raise ValueError(f"{section} contains unsupported keys: {', '.join(unknown)}")


@dataclass(frozen=True)
class AgentConfigRef:
    role: str
    config_ref: str
    entrypoint: bool = False
    capabilities: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TopologyEdge:
    source: str
    target: str
    modes: List[str] = field(default_factory=lambda: ["sync"])


@dataclass(frozen=True)
class TopologyConfig:
    edges: List[TopologyEdge] = field(default_factory=list)


@dataclass(frozen=True)
class SchedulerConfig:
    kind: str = "deterministic_event_loop"
    max_concurrent_agents: int = 1
    ordering: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WakeupConfig:
    inject_to: str = "main"
    inject_as: str = "system_message"
    template_ref: Optional[str] = None


@dataclass(frozen=True)
class SpawnProtocolConfig:
    tool_name: str = "Task"
    args_schema_ref: Optional[str] = None
    result_schema_ref: Optional[str] = None
    async_enabled: bool = False
    ack_fields: List[str] = field(default_factory=list)
    wakeup: WakeupConfig = field(default_factory=WakeupConfig)


@dataclass(frozen=True)
class OrchestrationConfig:
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    spawn_protocol: SpawnProtocolConfig = field(default_factory=SpawnProtocolConfig)
    join_policy: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BusConfig:
    model_visible_topics: List[str] = field(default_factory=list)
    retention: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CoordinationConfig:
    @dataclass(frozen=True)
    class DoneConfig:
        require_deliverable_refs: bool = False
        require_all_required_refs: bool = True
        require_no_open_required_children: bool = False

    @dataclass(frozen=True)
    class ReviewConfig:
        explicit_verdicts: bool = True
        allowed_reviewer_roles: List[str] = field(default_factory=lambda: ["supervisor", "system"])
        allowed_blocked_actions: List[str] = field(
            default_factory=lambda: ["retry", "checkpoint", "escalate", "human_required"]
        )
        no_progress_action: str = "checkpoint"
        retryable_failure_action: str = "retry"
        verification_result_contract: Optional[str] = None

    @dataclass(frozen=True)
    class MergeConfig:
        reducer_result_contract: Optional[str] = None

    @dataclass(frozen=True)
    class InterventionConfig:
        host_allowed_actions: List[str] = field(default_factory=lambda: ["continue", "checkpoint", "terminate"])
        require_evidence_refs: bool = False
        require_supervisor_escalate: bool = True
        support_claim_limited_actions: List[str] = field(default_factory=lambda: ["checkpoint", "terminate"])

    mission_owner_role: str = "supervisor"
    legacy_completion_sources: List[str] = field(
        default_factory=lambda: ["text_sentinel", "tool_call", "provider_finish"]
    )
    preserve_legacy_wake_conditions: bool = True
    done: DoneConfig = field(default_factory=DoneConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    merge: MergeConfig = field(default_factory=MergeConfig)
    intervention: InterventionConfig = field(default_factory=InterventionConfig)


@dataclass(frozen=True)
class WorkspaceConfig:
    sharing: Dict[str, Any] = field(default_factory=dict)
    isolation: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BudgetConfig:
    cost_usd_max: Optional[float] = None
    wall_clock_s_max: Optional[int] = None
    per_agent: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TeamConfig:
    team_id: str
    version: int = 1
    agents: Dict[str, AgentConfigRef] = field(default_factory=dict)
    topology: TopologyConfig = field(default_factory=TopologyConfig)
    orchestration: OrchestrationConfig = field(default_factory=OrchestrationConfig)
    bus: BusConfig = field(default_factory=BusConfig)
    coordination: CoordinationConfig = field(default_factory=CoordinationConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    budgets: BudgetConfig = field(default_factory=BudgetConfig)

    @staticmethod
    def from_dict(payload: Dict[str, Any]) -> "TeamConfig":
        team = payload.get("team", payload)
        team_id = str(team.get("id") or team.get("team_id") or "team")
        version = int(team.get("version") or 1)

        agents_raw = team.get("agents") or {}
        agents: Dict[str, AgentConfigRef] = {}
        for key, value in agents_raw.items():
            if not isinstance(value, dict):
                continue
            agents[key] = AgentConfigRef(
                role=str(value.get("role") or key),
                config_ref=str(value.get("config_ref") or ""),
                entrypoint=bool(value.get("entrypoint") or False),
                capabilities=dict(value.get("capabilities") or {}),
            )

        topo_raw = team.get("topology") or {}
        edges_raw = topo_raw.get("edges") or []
        edges: List[TopologyEdge] = []
        for edge in edges_raw:
            if not isinstance(edge, dict):
                continue
            edges.append(
                TopologyEdge(
                    source=str(edge.get("from") or edge.get("source") or ""),
                    target=str(edge.get("to") or edge.get("target") or ""),
                    modes=list(edge.get("mode") or edge.get("modes") or ["sync"]),
                )
            )

        orchestration_raw = team.get("orchestration") or {}
        scheduler_raw = orchestration_raw.get("scheduler") or {}
        spawn_raw = orchestration_raw.get("spawn_protocol") or {}
        async_raw = spawn_raw.get("async") or {}
        wakeup_raw = async_raw.get("wakeup") or {}

        scheduler = SchedulerConfig(
            kind=str(scheduler_raw.get("kind") or "deterministic_event_loop"),
            max_concurrent_agents=int(scheduler_raw.get("max_concurrent_agents") or 1),
            ordering=dict(scheduler_raw.get("ordering") or {}),
        )

        wakeup = WakeupConfig(
            inject_to=str(wakeup_raw.get("inject_to") or "main"),
            inject_as=str(wakeup_raw.get("inject_as") or "system_message"),
            template_ref=wakeup_raw.get("template_ref"),
        )

        spawn = SpawnProtocolConfig(
            tool_name=str(spawn_raw.get("tool_name") or "Task"),
            args_schema_ref=spawn_raw.get("args_schema_ref"),
            result_schema_ref=spawn_raw.get("result_schema_ref"),
            async_enabled=bool(async_raw.get("enabled") or False),
            ack_fields=list(async_raw.get("ack_fields") or []),
            wakeup=wakeup,
        )

        orchestration = OrchestrationConfig(
            scheduler=scheduler,
            spawn_protocol=spawn,
            join_policy=dict(orchestration_raw.get("join_policy") or {}),
        )

        bus_raw = team.get("bus") or {}
        bus = BusConfig(
            model_visible_topics=list(bus_raw.get("model_visible_topics") or []),
            retention=dict(bus_raw.get("retention") or {}),
        )

        coordination_raw = team.get("coordination") or {}
        coordination_raw = coordination_raw if isinstance(coordination_raw, dict) else {}
        _reject_unknown_keys(
            coordination_raw,
            section="coordination",
            allowed=_COORDINATION_ALLOWED_KEYS,
        )
        done_raw = coordination_raw.get("done") or {}
        done_raw = done_raw if isinstance(done_raw, dict) else {}
        _reject_unknown_keys(
            done_raw,
            section="coordination.done",
            allowed=_COORDINATION_DONE_ALLOWED_KEYS,
        )
        review_raw = coordination_raw.get("review") or {}
        review_raw = review_raw if isinstance(review_raw, dict) else {}
        _reject_unknown_keys(
            review_raw,
            section="coordination.review",
            allowed=_COORDINATION_REVIEW_ALLOWED_KEYS,
        )
        merge_raw = coordination_raw.get("merge") or {}
        merge_raw = merge_raw if isinstance(merge_raw, dict) else {}
        _reject_unknown_keys(
            merge_raw,
            section="coordination.merge",
            allowed=_COORDINATION_MERGE_ALLOWED_KEYS,
        )
        intervention_raw = coordination_raw.get("intervention") or {}
        intervention_raw = intervention_raw if isinstance(intervention_raw, dict) else {}
        _reject_unknown_keys(
            intervention_raw,
            section="coordination.intervention",
            allowed=_COORDINATION_INTERVENTION_ALLOWED_KEYS,
        )
        coordination = CoordinationConfig(
            mission_owner_role=str(coordination_raw.get("mission_owner_role") or "supervisor"),
            legacy_completion_sources=[
                str(item)
                for item in (coordination_raw.get("legacy_completion_sources") or [])
                if str(item).strip()
            ]
            or ["text_sentinel", "tool_call", "provider_finish"],
            preserve_legacy_wake_conditions=bool(
                coordination_raw.get("preserve_legacy_wake_conditions", True)
            ),
            done=CoordinationConfig.DoneConfig(
                require_deliverable_refs=bool(done_raw.get("require_deliverable_refs", False)),
                require_all_required_refs=bool(done_raw.get("require_all_required_refs", True)),
                require_no_open_required_children=bool(
                    done_raw.get("require_no_open_required_children", False)
                ),
            ),
            review=CoordinationConfig.ReviewConfig(
                explicit_verdicts=bool(review_raw.get("explicit_verdicts", True)),
                allowed_reviewer_roles=[
                    str(item)
                    for item in (review_raw.get("allowed_reviewer_roles") or [])
                    if str(item).strip()
                ]
                or ["supervisor", "system"],
                allowed_blocked_actions=[
                    str(item)
                    for item in (review_raw.get("allowed_blocked_actions") or [])
                    if str(item).strip()
                ]
                or ["retry", "checkpoint", "escalate", "human_required"],
                no_progress_action=str(review_raw.get("no_progress_action") or "checkpoint"),
                retryable_failure_action=str(review_raw.get("retryable_failure_action") or "retry"),
                verification_result_contract=(
                    str(review_raw.get("verification_result_contract")).strip()
                    if review_raw.get("verification_result_contract") is not None
                    and str(review_raw.get("verification_result_contract")).strip()
                    else None
                ),
            ),
            merge=CoordinationConfig.MergeConfig(
                reducer_result_contract=(
                    str(merge_raw.get("reducer_result_contract")).strip()
                    if merge_raw.get("reducer_result_contract") is not None
                    and str(merge_raw.get("reducer_result_contract")).strip()
                    else None
                )
            ),
            intervention=CoordinationConfig.InterventionConfig(
                host_allowed_actions=[
                    str(item)
                    for item in (intervention_raw.get("host_allowed_actions") or [])
                    if str(item).strip()
                ]
                or ["continue", "checkpoint", "terminate"],
                require_evidence_refs=bool(intervention_raw.get("require_evidence_refs", False)),
                require_supervisor_escalate=bool(intervention_raw.get("require_supervisor_escalate", True)),
                support_claim_limited_actions=[
                    str(item)
                    for item in (intervention_raw.get("support_claim_limited_actions") or [])
                    if str(item).strip()
                ]
                or ["checkpoint", "terminate"],
            ),
        )

        mission_owner_role = str(coordination.mission_owner_role or "").strip() or "supervisor"
        allowed_reviewer_roles = {
            str(item).strip()
            for item in (coordination.review.allowed_reviewer_roles or [])
            if str(item).strip()
        }
        if mission_owner_role not in allowed_reviewer_roles:
            raise ValueError(
                "coordination.mission_owner_role must be included in coordination.review.allowed_reviewer_roles"
            )
        invalid_reviewer_roles = sorted(role for role in allowed_reviewer_roles if role not in REVIEWER_ROLES)
        if invalid_reviewer_roles:
            raise ValueError(
                "coordination.review.allowed_reviewer_roles contains unsupported roles: "
                + ", ".join(invalid_reviewer_roles)
            )
        allowed_blocked_actions = {
            str(item).strip()
            for item in (coordination.review.allowed_blocked_actions or [])
            if str(item).strip()
        }
        invalid_blocked_actions = sorted(
            action for action in allowed_blocked_actions if action not in BLOCKED_RECOMMENDED_ACTIONS
        )
        if invalid_blocked_actions:
            raise ValueError(
                "coordination.review.allowed_blocked_actions contains unsupported actions: "
                + ", ".join(invalid_blocked_actions)
            )
        for field_name, field_value in (
            ("coordination.review.no_progress_action", coordination.review.no_progress_action),
            ("coordination.review.retryable_failure_action", coordination.review.retryable_failure_action),
        ):
            action = str(field_value or "").strip()
            if action and action not in BLOCKED_RECOMMENDED_ACTIONS:
                raise ValueError(f"{field_name} must stay within the narrow blocked-action vocabulary")
        for field_name, actions in (
            ("coordination.intervention.host_allowed_actions", coordination.intervention.host_allowed_actions),
            (
                "coordination.intervention.support_claim_limited_actions",
                coordination.intervention.support_claim_limited_actions,
            ),
        ):
            invalid_directives = sorted(
                action
                for action in {str(item).strip() for item in actions if str(item).strip()}
                if action not in DIRECTIVE_CODES
            )
            if invalid_directives:
                raise ValueError(
                    f"{field_name} contains unsupported directive codes: {', '.join(invalid_directives)}"
                )

        workspace_raw = team.get("workspace") or {}
        workspace = WorkspaceConfig(
            sharing=dict(workspace_raw.get("sharing") or {}),
            isolation=dict(workspace_raw.get("isolation") or {}),
        )

        budgets_raw = team.get("budgets") or {}
        budgets = BudgetConfig(
            cost_usd_max=budgets_raw.get("cost_usd_max"),
            wall_clock_s_max=budgets_raw.get("wall_clock_s_max"),
            per_agent=dict(budgets_raw.get("per_agent") or {}),
        )

        return TeamConfig(
            team_id=team_id,
            version=version,
            agents=agents,
            topology=TopologyConfig(edges=edges),
            orchestration=orchestration,
            bus=bus,
            coordination=coordination,
            workspace=workspace,
            budgets=budgets,
        )
