"""Opt-in search runtime primitives for DAG-shaped TTC research."""

from .compaction import (
    CompactionOutput,
    RegisteredCompactionBackend,
    SearchCompactionRegistry,
    build_default_search_compaction_registry,
)
from .examples import (
    build_pacore_search_runtime_example,
    build_pacore_search_runtime_example_payload,
    build_rsa_search_runtime_example,
    build_rsa_search_runtime_example_payload,
    build_typed_compaction_registry_example,
    build_typed_compaction_registry_example_payload,
)
from .runtime import (
    AggregationProposal,
    BarrieredRoundScheduler,
    BarrieredSchedulerConfig,
    BoundedMessagePassingScheduler,
    MessagePassingSchedulerConfig,
)
from .schema import (
    ALLOWED_CANDIDATE_STATUSES,
    ALLOWED_FRONTIER_STATUSES,
    ALLOWED_OPERATOR_KINDS,
    SearchCandidate,
    SearchCarryState,
    SearchEvent,
    SearchFrontier,
    SearchMessage,
    SearchMetrics,
    SearchRun,
)

__all__ = [
    "ALLOWED_CANDIDATE_STATUSES",
    "ALLOWED_FRONTIER_STATUSES",
    "ALLOWED_OPERATOR_KINDS",
    "AggregationProposal",
    "BarrieredRoundScheduler",
    "BarrieredSchedulerConfig",
    "BoundedMessagePassingScheduler",
    "CompactionOutput",
    "MessagePassingSchedulerConfig",
    "RegisteredCompactionBackend",
    "SearchCandidate",
    "SearchCarryState",
    "SearchCompactionRegistry",
    "SearchEvent",
    "SearchFrontier",
    "SearchMessage",
    "SearchMetrics",
    "SearchRun",
    "build_default_search_compaction_registry",
    "build_pacore_search_runtime_example",
    "build_pacore_search_runtime_example_payload",
    "build_rsa_search_runtime_example",
    "build_rsa_search_runtime_example_payload",
    "build_typed_compaction_registry_example",
    "build_typed_compaction_registry_example_payload",
]
