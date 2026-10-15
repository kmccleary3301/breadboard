"""Context compaction for BreadBoard model histories.

Core types (settings, token accounting, overflow classification, cut points,
append-only records, projection, the stage pipeline and preset loading) are
exported here. Primitive kinds live in ``primitives/``; whole-method
algorithms and provider adapters live in sibling modules; harness presets
live in ``presets/``.
"""

from .methods import (
    ArtifactSink,
    CompactionCancelled,
    CompactionContext,
    CompactionError,
    CompactionMethod,
    MethodUnavailable,
    NativeCompactionError,
    RemoteCompactionPort,
    SummaryModel,
    SummaryRequest,
    SummaryResponse,
)
from .overflow import (
    OVERFLOW_ERROR_CODE,
    is_context_overflow,
    overflow_http_details,
    provider_overflow_details,
    text_indicates_context_overflow,
)
from .params import PresetError
from .pipeline import CompactionOutcome, Pipeline, Stage, StageResult
from .presets import CompactionConfig, available_presets, load_compaction_config
from .settings import (
    COMPACTION_METHODS,
    DEFAULT_METHOD_ORDER,
    CompactionSettings,
    PruneSettings,
    ShakeSettings,
    SnapcompactSettings,
    resolve_threshold_tokens,
    settings_from_config,
    should_compact,
    summary_max_tokens,
)
from .state import (
    NATIVE_MARKER_KEY,
    CompactionRecord,
    CompactionState,
    CompactionStateError,
    MessageEdit,
    NativeCompaction,
    ProjectionTarget,
    strip_native_markers,
)
from .tokens import (
    compaction_context_tokens,
    context_tokens_from_usage,
    estimate_message_tokens,
    estimate_messages_tokens,
)
from .transcript import CutPoint, check_tool_pairing, find_cut_point

__all__ = [
    "ArtifactSink",
    "COMPACTION_METHODS",
    "CompactionCancelled",
    "CompactionConfig",
    "CompactionContext",
    "CompactionError",
    "CompactionMethod",
    "CompactionOutcome",
    "CompactionRecord",
    "CompactionSettings",
    "CompactionState",
    "CompactionStateError",
    "CutPoint",
    "DEFAULT_METHOD_ORDER",
    "MessageEdit",
    "MethodUnavailable",
    "NATIVE_MARKER_KEY",
    "OVERFLOW_ERROR_CODE",
    "Pipeline",
    "PresetError",
    "NativeCompaction",
    "NativeCompactionError",
    "ProjectionTarget",
    "PruneSettings",
    "RemoteCompactionPort",
    "ShakeSettings",
    "SnapcompactSettings",
    "Stage",
    "StageResult",
    "SummaryModel",
    "SummaryRequest",
    "SummaryResponse",
    "available_presets",
    "check_tool_pairing",
    "compaction_context_tokens",
    "context_tokens_from_usage",
    "estimate_message_tokens",
    "estimate_messages_tokens",
    "find_cut_point",
    "is_context_overflow",
    "load_compaction_config",
    "overflow_http_details",
    "provider_overflow_details",
    "resolve_threshold_tokens",
    "settings_from_config",
    "should_compact",
    "strip_native_markers",
    "summary_max_tokens",
    "text_indicates_context_overflow",
]
