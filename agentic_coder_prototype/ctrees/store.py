from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

from .events import CTREE_NODE_RECORDED
from .persistence import append_event, load_events, load_snapshot, resolve_ctree_paths, write_snapshot
from .schema import CTreeEvent, CTreeNode, CTREE_SCHEMA_VERSION

_VOLATILE_KEYS = {
    "event_id",
    "id",
    "seq",
    "timestamp",
    "updated_at",
    "created_at",
}
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_CLOSED_STATUSES = {"done", "frozen", "archived", "superseded", "abandoned"}


def _canonical_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _strip_volatiles(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, nested in value.items():
            if key in _VOLATILE_KEYS:
                continue
            cleaned[str(key)] = _strip_volatiles(nested)
        return cleaned
    if isinstance(value, list):
        return [_strip_volatiles(item) for item in value]
    return value


def _unique_strings(values: Any) -> List[str]:
    if values is None:
        return []
    if isinstance(values, str):
        return [values] if values else []
    items: List[str] = []
    if isinstance(values, list):
        iterable = values
    else:
        iterable = [values]
    seen = set()
    for item in iterable:
        if item is None:
            continue
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _normalize_links(values: Any) -> List[Dict[str, Any]]:
    if not isinstance(values, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "type": str(item.get("type") or item.get("link_type") or "relates_to"),
                "target": str(item.get("target") or item.get("target_id") or ""),
            }
        )
    return [link for link in normalized if link["target"]]


def _normalize_constraints(values: Any) -> List[Dict[str, Any]]:
    if values is None:
        return []
    if isinstance(values, dict):
        values = [values]
    if not isinstance(values, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for item in values:
        if isinstance(item, dict):
            normalized.append(
                {
                    "constraint_id": str(item.get("constraint_id") or item.get("id") or ""),
                    "constraint_type": str(item.get("constraint_type") or item.get("type") or "local"),
                    "summary": str(item.get("summary") or item.get("text") or ""),
                    "scope": str(item.get("scope") or "local"),
                    "status": str(item.get("status") or "unresolved"),
                }
            )
        elif item is not None:
            normalized.append(
                {
                    "constraint_id": "",
                    "constraint_type": "local",
                    "summary": str(item),
                    "scope": "local",
                    "status": "unresolved",
                }
            )
    return [item for item in normalized if item["summary"]]


def _infer_node_type(kind: str, payload: Any) -> str:
    if isinstance(payload, dict):
        explicit = payload.get("node_type")
        if explicit:
            return str(explicit)
    lowered = kind.strip().lower()
    if lowered in {"artifact", "file", "patch", "diff"}:
        return "artifact"
    if lowered in {"checkpoint", "collapse", "rehydration"}:
        return "checkpoint"
    if lowered in {"message", "assistant", "user"}:
        return "message"
    if lowered in {"evidence", "evidence_bundle", "result"}:
        return "evidence_bundle"
    if lowered in {"objective", "epic"}:
        return "objective"
    return "task"


def _infer_status(payload: Any) -> str:
    if isinstance(payload, dict):
        raw = payload.get("status")
        if raw:
            return str(raw)
        blocker_refs = payload.get("blocker_refs") or payload.get("blockers")
        if blocker_refs:
            return "blocked"
    return "active"


def _infer_title(kind: str, payload: Any, node_id: str) -> str:
    if isinstance(payload, dict):
        for key in ("title", "header", "summary", "text", "name"):
            value = payload.get(key)
            if value:
                return str(value).strip()[:120]
    return f"{kind}:{node_id}"


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.lower()).strip("-")
    return slug or "node"


def _build_path(parent_path: Optional[str], title: str, node_id: str) -> str:
    tail = f"{_slugify(title)}-{node_id.split('_')[-1]}"
    if parent_path:
        return f"{parent_path}/{tail}"
    return tail


class CTreeStore:
    """Small but real event-sourced store for tranche-1 C-Tree work."""

    def __init__(self) -> None:
        self.schema_version = CTREE_SCHEMA_VERSION
        self.nodes: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []
        self.links: List[Dict[str, Any]] = []
        self._next_node_num = 1
        self._next_event_num = 1
        self._node_by_id: Dict[str, Dict[str, Any]] = {}

    def _next_node_id(self) -> str:
        node_id = f"ctn_{self._next_node_num:06d}"
        self._next_node_num += 1
        return node_id

    def _next_event_id(self) -> str:
        event_id = f"cte_{self._next_event_num:06d}"
        self._next_event_num += 1
        return event_id

    def _register_node(self, node: Dict[str, Any]) -> None:
        self.nodes.append(node)
        self._node_by_id[str(node["id"])] = node
        self._next_node_num = max(self._next_node_num, self._parse_numeric_suffix(str(node["id"])) + 1)

    def _register_event(self, event: Dict[str, Any]) -> None:
        self.events.append(event)
        self._next_event_num = max(self._next_event_num, self._parse_numeric_suffix(str(event.get("event_id") or "")) + 1)

    @staticmethod
    def _parse_numeric_suffix(value: str) -> int:
        try:
            return int(value.split("_")[-1])
        except Exception:
            return 0

    def record(self, kind: str, payload: Any, *, turn: Optional[int] = None) -> str:
        payload_dict = payload if isinstance(payload, dict) else {}
        node_id = self._next_node_id()
        parent_id = payload_dict.get("parent_id")
        parent_path = None
        if parent_id and parent_id in self._node_by_id:
            parent_path = str(self._node_by_id[parent_id].get("path") or "")
        title = _infer_title(kind, payload, node_id)
        node = CTreeNode(
            node_id=node_id,
            kind=str(kind),
            node_type=_infer_node_type(kind, payload),
            title=title,
            status=_infer_status(payload),
            turn=turn,
            payload=payload,
            parent_id=str(parent_id) if parent_id else None,
            path=_build_path(parent_path, title, node_id),
            owner=str(payload_dict.get("owner")) if payload_dict.get("owner") else None,
            constraints=_normalize_constraints(payload_dict.get("constraints")),
            targets=_unique_strings(payload_dict.get("targets") or payload_dict.get("files") or payload_dict.get("paths")),
            workspace_scope=_unique_strings(payload_dict.get("workspace_scope") or payload_dict.get("workspace")),
            artifact_refs=_unique_strings(payload_dict.get("artifact_refs") or payload_dict.get("artifacts")),
            blocker_refs=_unique_strings(payload_dict.get("blocker_refs") or payload_dict.get("blockers")),
            related_links=_normalize_links(payload_dict.get("related_links") or payload_dict.get("graph_links")),
            final_spec=payload_dict.get("final_spec"),
        ).to_dict()
        node["lexical_terms"] = self._extract_lexical_terms(node)
        node["provenance"] = {
            "payload_sha1": hashlib.sha1(_canonical_dumps(_strip_volatiles(payload)).encode("utf-8")).hexdigest(),
            "schema_version": self.schema_version,
        }
        self._register_node(node)

        event = CTreeEvent(
            event_id=self._next_event_id(),
            event_type=CTREE_NODE_RECORDED,
            kind=str(kind),
            payload=_strip_volatiles(payload),
            turn=turn,
            node_id=node_id,
            actor="engine",
        ).to_dict()
        event["node"] = self._node_fingerprint(node)
        self._register_event(event)

        for link in list(node.get("related_links") or []):
            enriched = {
                "source": node_id,
                "target": str(link.get("target")),
                "type": str(link.get("type") or "relates_to"),
            }
            self.links.append(enriched)

        return node_id

    def _extract_lexical_terms(self, node: Dict[str, Any]) -> List[str]:
        tokens: List[str] = []
        for key in ("title", "kind", "node_type", "path"):
            value = node.get(key)
            if value:
                tokens.extend(_unique_strings(re.split(r"[\s/_.:-]+", str(value))))
        for key in ("targets", "artifact_refs", "blocker_refs", "workspace_scope"):
            tokens.extend(_unique_strings(node.get(key)))
        for constraint in list(node.get("constraints") or []):
            if isinstance(constraint, dict):
                tokens.extend(_unique_strings([constraint.get("summary"), constraint.get("scope")]))
        return [token for token in tokens if token]

    def _node_fingerprint(self, node: Dict[str, Any]) -> Dict[str, Any]:
        fingerprint = {
            "id": node.get("id"),
            "kind": node.get("kind"),
            "node_type": node.get("node_type"),
            "title": node.get("title"),
            "status": node.get("status"),
            "turn": node.get("turn"),
            "parent_id": node.get("parent_id"),
            "path": node.get("path"),
            "constraints": _strip_volatiles(node.get("constraints") or []),
            "targets": list(node.get("targets") or []),
            "workspace_scope": list(node.get("workspace_scope") or []),
            "artifact_refs": list(node.get("artifact_refs") or []),
            "blocker_refs": list(node.get("blocker_refs") or []),
            "related_links": _strip_volatiles(node.get("related_links") or []),
            "final_spec": _strip_volatiles(node.get("final_spec")),
            "payload": _strip_volatiles(node.get("payload")),
        }
        return fingerprint

    def top_level_nodes(self) -> List[Dict[str, Any]]:
        return [dict(node) for node in self.nodes if not node.get("parent_id")]

    def children_of(self, parent_id: str) -> List[Dict[str, Any]]:
        return [dict(node) for node in self.nodes if str(node.get("parent_id") or "") == str(parent_id)]

    def ready_nodes(self) -> List[Dict[str, Any]]:
        ready: List[Dict[str, Any]] = []
        for node in self.nodes:
            status = str(node.get("status") or "")
            blockers = list(node.get("blocker_refs") or [])
            if status in _CLOSED_STATUSES or blockers:
                continue
            ready.append(dict(node))
        return ready

    def snapshot(self) -> Dict[str, Any]:
        last_node = dict(self.nodes[-1]) if self.nodes else None
        status_counts = Counter(str(node.get("status") or "unknown") for node in self.nodes)
        return {
            "schema_version": self.schema_version,
            "node_count": len(self.nodes),
            "event_count": len(self.events),
            "link_count": len(self.links),
            "top_level_count": len(self.top_level_nodes()),
            "ready_count": len(self.ready_nodes()),
            "status_counts": dict(status_counts),
            "last_id": last_node.get("id") if last_node else None,
            "last_node": last_node,
        }

    def hashes(self) -> Dict[str, str]:
        node_payload = [_strip_volatiles(self._node_fingerprint(node)) for node in self.nodes]
        event_payload = [_strip_volatiles(event) for event in self.events]
        link_payload = [_strip_volatiles(link) for link in self.links]
        snapshot_state = self.snapshot()
        snapshot_payload = {
            "schema_version": snapshot_state.get("schema_version"),
            "node_count": snapshot_state.get("node_count"),
            "event_count": snapshot_state.get("event_count"),
            "link_count": snapshot_state.get("link_count"),
            "top_level_count": snapshot_state.get("top_level_count"),
            "ready_count": snapshot_state.get("ready_count"),
            "status_counts": snapshot_state.get("status_counts"),
            "last_id": snapshot_state.get("last_id"),
        }
        return {
            "nodes_sha1": hashlib.sha1(_canonical_dumps(node_payload).encode("utf-8")).hexdigest(),
            "events_sha1": hashlib.sha1(_canonical_dumps(event_payload).encode("utf-8")).hexdigest(),
            "links_sha1": hashlib.sha1(_canonical_dumps(link_payload).encode("utf-8")).hexdigest(),
            "snapshot_sha1": hashlib.sha1(_canonical_dumps(snapshot_payload).encode("utf-8")).hexdigest(),
        }

    def persist(self, base_dir: str) -> Dict[str, Any]:
        paths = resolve_ctree_paths(base_dir)
        for event in self.events:
            append_event(paths["events"], event)
        write_snapshot(paths["snapshot"], self.snapshot())
        return {
            "paths": {key: str(value) for key, value in paths.items()},
            "snapshot": self.snapshot(),
        }

    @classmethod
    def from_events(cls, events: Iterable[Dict[str, Any]]) -> "CTreeStore":
        store = cls()
        for raw in events:
            if not isinstance(raw, dict):
                continue
            node = raw.get("node")
            if not isinstance(node, dict):
                kind = str(raw.get("kind") or "message")
                payload = raw.get("payload")
                turn = raw.get("turn")
                store.record(kind, payload, turn=turn)
                continue
            node_copy = dict(node)
            if "id" not in node_copy:
                continue
            node_copy.setdefault("payload", raw.get("payload"))
            node_copy.setdefault("kind", str(raw.get("kind") or "message"))
            node_copy.setdefault("node_type", _infer_node_type(str(node_copy.get("kind") or "message"), node_copy.get("payload")))
            node_copy.setdefault("title", _infer_title(str(node_copy.get("kind") or "message"), node_copy.get("payload"), str(node_copy["id"])))
            node_copy.setdefault("status", _infer_status(node_copy.get("payload")))
            node_copy.setdefault("constraints", _normalize_constraints(node_copy.get("constraints")))
            node_copy.setdefault("targets", _unique_strings(node_copy.get("targets")))
            node_copy.setdefault("workspace_scope", _unique_strings(node_copy.get("workspace_scope")))
            node_copy.setdefault("artifact_refs", _unique_strings(node_copy.get("artifact_refs")))
            node_copy.setdefault("blocker_refs", _unique_strings(node_copy.get("blocker_refs")))
            node_copy.setdefault("related_links", _normalize_links(node_copy.get("related_links")))
            node_copy["lexical_terms"] = store._extract_lexical_terms(node_copy)
            store._register_node(node_copy)

            event = CTreeEvent.from_dict(raw).to_dict()
            event["node"] = store._node_fingerprint(node_copy)
            store._register_event(event)
            for link in list(node_copy.get("related_links") or []):
                enriched = {
                    "source": str(node_copy["id"]),
                    "target": str(link.get("target")),
                    "type": str(link.get("type") or "relates_to"),
                }
                store.links.append(enriched)
        return store

    @classmethod
    def from_dir(cls, base_dir: str) -> "CTreeStore":
        paths = resolve_ctree_paths(base_dir)
        events = load_events(paths["events"])
        store = cls.from_events(events)
        snapshot = load_snapshot(paths["snapshot"])
        if isinstance(snapshot, dict):
            store.schema_version = str(snapshot.get("schema_version") or store.schema_version)
        return store
