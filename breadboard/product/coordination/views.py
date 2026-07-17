"""Disposable coordination projections rebuilt from authoritative Work Item events."""
from collections.abc import Iterable
from dataclasses import dataclass
from threading import Lock
from .placement import WorkPlacement
from .work_items import WorkItem, rebuild_work_item
@dataclass(frozen=True, slots=True)
class CoordinationItem:
    work_item_id: str; title: str; status: str; parent_work_item_id: str | None; child_work_item_ids: tuple[str, ...]; active_worker_id: str | None; current_attempt_id: str | None; current_session_ref: str | None; event_count: int
@dataclass(frozen=True, slots=True)
class DelegationEdge:
    parent_work_item_id: str; child_work_item_id: str
@dataclass(frozen=True, slots=True)
class CoordinationView:
    items: tuple[CoordinationItem, ...]; delegation_edges: tuple[DelegationEdge, ...]; placements: tuple[WorkPlacement, ...]; source_event_count: int
class CoordinationProjector:
    def __init__(self) -> None:
        self._lock = Lock()
        self._view: CoordinationView | None = None
    @property
    def view(self) -> CoordinationView | None:
        with self._lock: return self._view
    def clear(self) -> None:
        with self._lock: self._view = None
    def rebuild(self, work_items: Iterable[WorkItem]) -> CoordinationView:
        snapshots = tuple(rebuild_work_item(item.events) for item in work_items)
        if not snapshots: raise ValueError("coordination projection requires at least one Work Item")
        by_id = {item.work_item_id: item for item in snapshots}
        if len(by_id) != len(snapshots): raise ValueError("coordination projection contains duplicate Work Items")
        edges: list[DelegationEdge] = []
        for parent in snapshots:
            for child_id in parent.child_work_item_ids:
                if (child := by_id.get(child_id)) is None or child.parent_work_item_id != parent.work_item_id: raise ValueError("delegation edge is not reciprocal")
                edges.append(DelegationEdge(parent.work_item_id, child_id))
        for child in snapshots:
            if child.parent_work_item_id is not None:
                if (parent := by_id.get(child.parent_work_item_id)) is None or child.work_item_id not in parent.child_work_item_ids: raise ValueError("child parent reference is not reciprocal")
        ordered = tuple(sorted(snapshots, key=lambda item: item.work_item_id))
        view = CoordinationView(tuple(CoordinationItem(item.work_item_id, item.title, item.status, item.parent_work_item_id, item.child_work_item_ids, item.active_lease.worker_id if item.active_lease else None, attempt.attempt_id if (attempt := item.current_attempt) else None, attempt.session_ref if attempt else None, item.event_count) for item in ordered), tuple(sorted(edges, key=lambda edge: (edge.parent_work_item_id, edge.child_work_item_id))), tuple(sorted((placement for item in ordered for placement in item.placements), key=lambda row: row.placement_id)), sum(item.event_count for item in ordered))
        with self._lock: self._view = view
        return view
