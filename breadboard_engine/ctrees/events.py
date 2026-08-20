from __future__ import annotations

from typing import Dict

CTREE_NODE_EVENT = "ctree_node"
CTREE_SNAPSHOT_EVENT = "ctree_snapshot"
CTREE_DELTA_EVENT = "ctree_delta"

CTREE_NODE_RECORDED = "node_recorded"
CTREE_STATUS_CHANGED = "status_changed"
CTREE_CONSTRAINT_ADDED = "constraint_added"
CTREE_DECISION_RECORDED = "decision_recorded"
CTREE_ARTIFACT_ATTACHED = "artifact_attached"
CTREE_GRAPH_LINK_ADDED = "graph_link_added"
CTREE_SUMMARY_REFRESHED = "summary_refreshed"
CTREE_FREEZE_RECORDED = "freeze_recorded"
CTREE_COLLAPSE_RECORDED = "collapse_recorded"
CTREE_REHYDRATION_RECORDED = "rehydration_recorded"

CTREE_EVENT_TYPES = (
    CTREE_NODE_EVENT,
    CTREE_SNAPSHOT_EVENT,
    CTREE_DELTA_EVENT,
    CTREE_NODE_RECORDED,
    CTREE_STATUS_CHANGED,
    CTREE_CONSTRAINT_ADDED,
    CTREE_DECISION_RECORDED,
    CTREE_ARTIFACT_ATTACHED,
    CTREE_GRAPH_LINK_ADDED,
    CTREE_SUMMARY_REFRESHED,
    CTREE_FREEZE_RECORDED,
    CTREE_COLLAPSE_RECORDED,
    CTREE_REHYDRATION_RECORDED,
)


def is_ctree_event_type(value: str) -> bool:
    return value in CTREE_EVENT_TYPES


def build_ctree_event_record(event_type: str, *, kind: str, node_id: str, turn: int | None) -> Dict[str, object]:
    return {
        "event_type": event_type,
        "kind": kind,
        "node_id": node_id,
        "turn": turn,
    }
