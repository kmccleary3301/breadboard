"""Multi-agent orchestration primitives (Phase 8 scaffolding)."""

from __future__ import annotations

import importlib

from .schema import (
    AgentConfigRef,
    BudgetConfig,
    BusConfig,
    OrchestrationConfig,
    SchedulerConfig,
    SpawnProtocolConfig,
    TeamConfig,
    TopologyConfig,
    TopologyEdge,
    WakeupConfig,
    WorkspaceConfig,
)
from .event_log import Event, EventLog
from .job_manager import JobManager, JobRef
from .bus_adapter import BusAdapter
from .orchestrator import MultiAgentOrchestrator
from .replay import EventLogReplay, load_event_log, write_event_log

__all__ = [
    "AgentConfigRef",
    "BudgetConfig",
    "BusConfig",
    "OrchestrationConfig",
    "SchedulerConfig",
    "SpawnProtocolConfig",
    "TeamConfig",
    "TopologyConfig",
    "TopologyEdge",
    "WakeupConfig",
    "WorkspaceConfig",
    "Event",
    "EventLog",
    "JobManager",
    "JobRef",
    "BusAdapter",
    "MultiAgentOrchestrator",
    "EventLogReplay",
    "load_event_log",
    "write_event_log",
    "OpenCodeAgent",
]


def __getattr__(name: str):
    if name != "OpenCodeAgent":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    value = getattr(importlib.import_module(".agent_session", __name__), name)
    globals()[name] = value
    return value
