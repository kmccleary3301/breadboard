from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class SearchLoopSpec:
    loop_id: str
    objective: str
    budget: dict[str, Any]
    policy_id: str
    determinism: str = "replayable"
    run_id: str = "run"
    session_id: str = "session"
    turn_id: str = "turn"


@dataclass
class SearchNode:
    node_id: str
    parent_id: str | None
    state_ref: dict[str, Any]
    node_type: str = "candidate"
    score: dict[str, Any] = field(default_factory=dict)
    status: str = "open"


class SearchLoop:
    _ROOT_NODE_TYPE = "root"
    _EXPANDABLE_NODE_TYPES = {"candidate", "lemma", "subgoal", "repair"}
    _ALL_NODE_TYPES = _EXPANDABLE_NODE_TYPES | {_ROOT_NODE_TYPE}
    _CLOSEABLE_STATUSES = {"closed", "failed", "verified"}
    _ALL_STATUSES = _CLOSEABLE_STATUSES | {"open"}

    def __init__(self, spec: SearchLoopSpec):
        if spec.determinism not in {"replayable", "exploratory"}:
            raise ValueError("spec.determinism must be replayable or exploratory")
        self.spec = spec
        self._seq = 0
        self._node_counter = 0
        self._events: list[dict[str, Any]] = []
        self._nodes: dict[str, SearchNode] = {}
        self._edges: list[dict[str, str]] = []

    def _next_node_id(self) -> str:
        self._node_counter += 1
        if self.spec.determinism == "replayable":
            return f"n{self._node_counter:06d}"
        return f"n{uuid.uuid4().hex[:12]}"

    def _artifact_ref(self, node_id: str, storage_locator: str) -> dict[str, Any]:
        return {
            "schema_id": "breadboard.artifact_ref.v1",
            "artifact_id": f"{self.spec.loop_id}.{node_id}.state_ref",
            "storage_locator": storage_locator,
        }

    def _emit(self, event_type: str, payload: dict[str, Any], artifacts: list[dict[str, Any]] | None = None) -> None:
        self._seq += 1
        self._events.append(
            {
                "schema_id": "breadboard.event.envelope.v1",
                "protocol_version": "1.0",
                "run_id": self.spec.run_id,
                "session_id": self.spec.session_id,
                "turn_id": self.spec.turn_id,
                "seq": self._seq,
                "timestamp": time.time(),
                "type": event_type,
                "payload": payload,
                "artifacts": artifacts or [],
            }
        )

    def start(self, *, state_storage_locator: str) -> str:
        root_id = self._next_node_id()
        state_ref = self._artifact_ref(root_id, state_storage_locator)
        root = SearchNode(
            node_id=root_id,
            parent_id=None,
            state_ref=state_ref,
            node_type=self._ROOT_NODE_TYPE,
            status="open",
        )
        self._nodes[root_id] = root
        self._emit(
            "search_loop.started",
            payload={"loop_id": self.spec.loop_id, "node": asdict(root)},
            artifacts=[state_ref],
        )
        return root_id

    def expand(
        self,
        *,
        parent_id: str,
        state_storage_locator: str,
        score: dict[str, Any] | None = None,
        node_type: str = "candidate",
        status: str = "open",
    ) -> str:
        if parent_id not in self._nodes:
            raise KeyError(f"parent node does not exist: {parent_id}")
        if node_type not in self._EXPANDABLE_NODE_TYPES:
            raise ValueError("node_type must be candidate, lemma, subgoal, or repair")
        if status not in self._ALL_STATUSES:
            raise ValueError("status must be open, closed, failed, or verified")
        node_id = self._next_node_id()
        state_ref = self._artifact_ref(node_id, state_storage_locator)
        node = SearchNode(
            node_id=node_id,
            parent_id=parent_id,
            state_ref=state_ref,
            node_type=node_type,
            score=score or {},
            status=status,
        )
        self._nodes[node_id] = node
        self._edges.append({"parent_id": parent_id, "child_id": node_id})
        self._emit(
            "search_loop.node_expanded",
            payload={"loop_id": self.spec.loop_id, "node": asdict(node)},
            artifacts=[state_ref],
        )
        return node_id

    def close(self, *, node_id: str, status: str = "closed", reason: str | None = None) -> None:
        if node_id not in self._nodes:
            raise KeyError(f"node does not exist: {node_id}")
        if status not in self._CLOSEABLE_STATUSES:
            raise ValueError("status must be closed, failed, or verified")
        node = self._nodes[node_id]
        node.status = status
        payload = {
            "loop_id": self.spec.loop_id,
            "node_id": node_id,
            "status": status,
            "reason": reason,
        }
        self._emit("search_loop.node_closed", payload=payload, artifacts=[node.state_ref])

    def event_log(self) -> list[dict[str, Any]]:
        return list(self._events)

    def lineage_artifact(self) -> dict[str, Any]:
        return {
            "schema_id": "breadboard.search_loop.lineage.v1",
            "loop_id": self.spec.loop_id,
            "determinism": self.spec.determinism,
            "policy_id": self.spec.policy_id,
            "objective": self.spec.objective,
            "budget": dict(self.spec.budget),
            "nodes": [asdict(node) for node in self._nodes.values()],
            "edges": list(self._edges),
            "event_count": len(self._events),
        }

    @classmethod
    def from_event_log(cls, *, spec: SearchLoopSpec, event_log: list[dict[str, Any]]) -> SearchLoop:
        replay = cls(spec=spec)
        replay._events = list(event_log)
        replay._seq = max((int(event.get("seq", 0)) for event in replay._events), default=0)

        for event in replay._events:
            event_type = event.get("type")
            payload = event.get("payload") or {}
            if event_type in {"search_loop.started", "search_loop.node_expanded"}:
                node_payload = payload.get("node") or {}
                fallback_type = cls._ROOT_NODE_TYPE if event_type == "search_loop.started" else "candidate"
                node_type = str(node_payload.get("node_type") or fallback_type)
                if node_type not in cls._ALL_NODE_TYPES:
                    raise ValueError(f"invalid node_type in replay event: {node_type}")
                node_status = str(node_payload.get("status") or "open")
                if node_status not in cls._ALL_STATUSES:
                    raise ValueError(f"invalid node status in replay event: {node_status}")
                node = SearchNode(
                    node_id=str(node_payload.get("node_id")),
                    parent_id=node_payload.get("parent_id"),
                    state_ref=dict(node_payload.get("state_ref") or {}),
                    node_type=node_type,
                    score=dict(node_payload.get("score") or {}),
                    status=node_status,
                )
                replay._nodes[node.node_id] = node
                if node.parent_id:
                    replay._edges.append({"parent_id": str(node.parent_id), "child_id": node.node_id})
            if event_type == "search_loop.node_closed":
                node_id = str(payload.get("node_id"))
                if node_id in replay._nodes:
                    next_status = str(payload.get("status") or replay._nodes[node_id].status)
                    if next_status not in cls._ALL_STATUSES:
                        raise ValueError(f"invalid node status in close event: {next_status}")
                    replay._nodes[node_id].status = next_status

        max_counter = 0
        for node_id in replay._nodes:
            if node_id.startswith("n") and node_id[1:].isdigit():
                max_counter = max(max_counter, int(node_id[1:]))
        replay._node_counter = max_counter
        return replay
