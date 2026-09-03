"""Disposable coordination projections rebuilt from authoritative Work Item events."""
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from threading import Lock
from breadboard.product.projection import Projected, ProjectionAsOf, ProjectionCursor, ProjectionSource
from .placement import WorkPlacement
from .work_items import WorkItem, WorkItemEvent, WorkItemSnapshot, rebuild_work_item
@dataclass(frozen=True, slots=True)
class CoordinationItem:
    work_item_id: str; title: str; status: str; parent_work_item_id: str | None; child_work_item_ids: tuple[str, ...]; active_worker_id: str | None; current_attempt_id: str | None; current_session_ref: str | None; event_count: int
@dataclass(frozen=True, slots=True)
class DelegationEdge:
    parent_work_item_id: str; child_work_item_id: str
@dataclass(frozen=True, slots=True)
class CoordinationView:
    items: tuple[CoordinationItem, ...]; delegation_edges: tuple[DelegationEdge, ...]; placements: tuple[WorkPlacement, ...]; source_event_count: int
COORDINATION_PROJECTOR_VERSION = "bb.coordination.projector.v1"
class CoordinationProjectionError(ValueError):
    """A coordination projection request cannot be satisfied."""
class CoordinationProjectionAsOfError(CoordinationProjectionError):
    """A requested source sequence is outside one Work Item stream."""
    def __init__(self, as_of: object, available: int) -> None:
        super().__init__(f"coordination as_of {as_of!r} is outside source range 1..{available}")
        self.as_of, self.available = as_of, available
class CoordinationProjectionVersionError(CoordinationProjectionError):
    """A caller requested a projector version this owner does not provide."""
    def __init__(self, expected: str) -> None:
        super().__init__(f"unsupported coordination projector version {expected!r}")
        self.expected = expected
def _check_coordination_projection_version(expected: str | None) -> None:
    if expected is not None and expected != COORDINATION_PROJECTOR_VERSION:
        raise CoordinationProjectionVersionError(expected)
def _coordination_view(snapshots: tuple[WorkItemSnapshot, ...]) -> CoordinationView:
    if not snapshots:
        raise ValueError("coordination projection requires at least one Work Item")
    by_id = {item.work_item_id: item for item in snapshots}
    if len(by_id) != len(snapshots):
        raise ValueError("coordination projection contains duplicate Work Items")
    edges: list[DelegationEdge] = []
    for parent in snapshots:
        for child_id in parent.child_work_item_ids:
            if (child := by_id.get(child_id)) is None or child.parent_work_item_id != parent.work_item_id:
                raise ValueError("delegation edge is not reciprocal")
            edges.append(DelegationEdge(parent.work_item_id, child_id))
    for child in snapshots:
        if child.parent_work_item_id is not None:
            if (parent := by_id.get(child.parent_work_item_id)) is None or child.work_item_id not in parent.child_work_item_ids:
                raise ValueError("child parent reference is not reciprocal")
    ordered = tuple(sorted(snapshots, key=lambda item: item.work_item_id))
    return CoordinationView(tuple(CoordinationItem(item.work_item_id, item.title, item.status, item.parent_work_item_id, item.child_work_item_ids, item.active_lease.worker_id if item.active_lease else None, attempt.attempt_id if (attempt := item.current_attempt) else None, attempt.session_ref if attempt else None, item.event_count) for item in ordered), tuple(sorted(edges, key=lambda edge: (edge.parent_work_item_id, edge.child_work_item_id))), tuple(sorted((placement for item in ordered for placement in item.placements), key=lambda row: row.placement_id)), sum(item.event_count for item in ordered))
def _coordination_limit(rows: tuple[WorkItemEvent, ...], as_of: object) -> int:
    if not rows:
        raise ValueError("event stream must begin with work_item.created")
    limit = len(rows) if as_of is None else as_of
    if type(limit) is not int or limit < 1 or limit > len(rows):
        raise CoordinationProjectionAsOfError(limit, len(rows))
    return limit
def _coordination_limits(rows_by_id: Mapping[str, tuple[WorkItemEvent, ...]], as_of: ProjectionAsOf | None) -> dict[str, int]:
    if as_of is None:
        return {work_item_id: len(rows) for work_item_id, rows in rows_by_id.items()}
    if type(as_of) is int:
        return {work_item_id: _coordination_limit(rows, as_of) for work_item_id, rows in rows_by_id.items()}
    if type(as_of) is not tuple or not as_of or any(not isinstance(cursor, ProjectionCursor) for cursor in as_of):
        raise CoordinationProjectionAsOfError(as_of, 0)
    requested = {cursor.stream: cursor.sequence for cursor in as_of}
    if len(requested) != len(as_of):
        raise CoordinationProjectionAsOfError(as_of, max(map(len, rows_by_id.values()), default=0))
    expected = {f"work_item:{work_item_id}" for work_item_id in rows_by_id}
    if set(requested) != expected:
        raise CoordinationProjectionAsOfError(as_of, max(map(len, rows_by_id.values()), default=0))
    return {_id: _coordination_limit(rows, requested[f"work_item:{_id}"]) for _id, rows in rows_by_id.items()}
def _coordination_snapshots(event_streams: Mapping[str, Iterable[WorkItemEvent]], as_of: ProjectionAsOf | None) -> tuple[WorkItemSnapshot, ...]:
    rows_by_id: dict[str, tuple[WorkItemEvent, ...]] = {}
    for work_item_id, events in sorted(event_streams.items()):
        if type(work_item_id) is not str or not work_item_id:
            raise ValueError("coordination source stream ids must be non-empty strings")
        rows = tuple(events)
        if not rows:
            raise ValueError("event stream must begin with work_item.created")
        if rows[0].work_item_id != work_item_id:
            raise ValueError("coordination source stream identity mismatch")
        rows_by_id[work_item_id] = rows
    limits = _coordination_limits(rows_by_id, as_of)
    return tuple(rebuild_work_item(rows_by_id[work_item_id][:limits[work_item_id]]) for work_item_id in sorted(rows_by_id))
def _coordination_source(view: CoordinationView) -> tuple[ProjectionSource, tuple[ProjectionCursor, ...]]:
    components = tuple(ProjectionSource(f"work_item:{item.work_item_id}", 1, item.event_count) for item in view.items)
    cursors = tuple(ProjectionCursor(component.stream, component.last_sequence) for component in components)
    return ProjectionSource("coordination", 1, max(cursor.sequence for cursor in cursors), components), cursors
def project_coordination_replay(event_streams: Mapping[str, Iterable[WorkItemEvent]], *, as_of: ProjectionAsOf | None = None, expected_projector_version: str | None = None) -> Projected[CoordinationView]:
    _check_coordination_projection_version(expected_projector_version)
    if not isinstance(event_streams, Mapping):
        raise TypeError("coordination replay projection requires a mapping of Work Item streams")
    value = _coordination_view(_coordination_snapshots(event_streams, as_of))
    source, effective_as_of = _coordination_source(value)
    return Projected(value, COORDINATION_PROJECTOR_VERSION, source, effective_as_of)
def project_coordination(event_streams: Mapping[str, Iterable[WorkItemEvent]], *, as_of: ProjectionAsOf | None = None, expected_projector_version: str | None = None) -> Projected[CoordinationView]:
    return project_coordination_replay(event_streams, as_of=as_of, expected_projector_version=expected_projector_version)
def project_coordination_snapshot(view: CoordinationView, *, as_of: ProjectionAsOf | None = None, expected_projector_version: str | None = None) -> Projected[CoordinationView]:
    _check_coordination_projection_version(expected_projector_version)
    if not isinstance(view, CoordinationView):
        raise TypeError("coordination snapshot projection requires a CoordinationView")
    source, cursors = _coordination_source(view)
    if as_of is not None:
        maximum = max(item.event_count for item in view.items)
        if type(as_of) is int:
            if any(item.event_count != as_of for item in view.items):
                raise CoordinationProjectionAsOfError(as_of, maximum)
        elif type(as_of) is tuple and as_of and all(isinstance(cursor, ProjectionCursor) for cursor in as_of):
            requested = {cursor.stream: cursor.sequence for cursor in as_of}
            expected = {cursor.stream: cursor.sequence for cursor in cursors}
            if len(requested) != len(as_of) or requested != expected:
                raise CoordinationProjectionAsOfError(as_of, maximum)
        else:
            raise CoordinationProjectionAsOfError(as_of, maximum)
    return Projected(view, COORDINATION_PROJECTOR_VERSION, source, cursors)
def project_coordination_live(work_items: Iterable[WorkItem], *, as_of: ProjectionAsOf | None = None, expected_projector_version: str | None = None) -> Projected[CoordinationView]:
    items = tuple(work_items)
    if any(not isinstance(item, WorkItem) for item in items):
        raise TypeError("live coordination projection requires Work Item values")
    streams: dict[str, tuple[WorkItemEvent, ...]] = {}
    for item in items:
        rows = item.events
        if not rows:
            raise ValueError("event stream must begin with work_item.created")
        if rows[0].work_item_id in streams:
            raise ValueError("coordination projection contains duplicate Work Items")
        streams[rows[0].work_item_id] = rows
    return project_coordination_replay(streams, as_of=as_of, expected_projector_version=expected_projector_version)
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
        view = project_coordination_live(work_items).value
        with self._lock: self._view = view
        return view
    def projected_view(self, work_items: Iterable[WorkItem], *, as_of: ProjectionAsOf | None = None, expected_projector_version: str | None = None) -> Projected[CoordinationView]:
        return project_coordination_live(work_items, as_of=as_of, expected_projector_version=expected_projector_version)
