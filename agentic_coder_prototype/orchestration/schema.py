"""Schema definitions for multi-agent orchestration (Phase 8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
            workspace=workspace,
            budgets=budgets,
        )
