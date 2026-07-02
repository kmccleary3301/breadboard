from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from breadboard.rl.trace.graph import TrajectoryGraph


@dataclass(frozen=True)
class ProjectionManifest:
    projection_id: str
    source_graph_id: str
    target_format: str
    preserved_fields: list[str]
    lost_fields: list[str] = field(default_factory=list)
    included_node_ids: list[str] = field(default_factory=list)
    excluded_node_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "projection_id": self.projection_id,
            "source_graph_id": self.source_graph_id,
            "target_format": self.target_format,
            "preserved_fields": list(self.preserved_fields),
            "lost_fields": list(self.lost_fields),
            "included_node_ids": list(self.included_node_ids),
            "excluded_node_ids": list(self.excluded_node_ids),
            "metadata": dict(self.metadata),
        }


def build_projection_manifest(
    *,
    graph: TrajectoryGraph,
    target_format: str,
    preserved_fields: list[str],
    lost_fields: list[str] | None = None,
    included_node_kinds: set[str] | None = None,
) -> ProjectionManifest:
    included_kinds = included_node_kinds or {node.node_kind for node in graph.nodes}
    included_node_ids = [node.node_id for node in graph.nodes if node.node_kind in included_kinds]
    excluded_node_ids = [node.node_id for node in graph.nodes if node.node_kind not in included_kinds]
    return ProjectionManifest(
        projection_id=f"{graph.graph_id}.projection.{target_format}",
        source_graph_id=graph.graph_id,
        target_format=target_format,
        preserved_fields=list(preserved_fields),
        lost_fields=list(lost_fields or []),
        included_node_ids=included_node_ids,
        excluded_node_ids=excluded_node_ids,
        metadata={"canonical_truth": "breadboard_graph_replay_runtime"},
    )
