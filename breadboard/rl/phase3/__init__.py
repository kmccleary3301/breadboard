"""Phase 3 production-readiness gates and live RL surfaces."""

from .evidence import (
    PHASE3_COMMAND_LOG_MANIFEST_SCHEMA,
    PHASE3_COMPONENT_REPORT_SCHEMA,
    PHASE3_TARGET_RUN_ID_PATTERN,
    REQUIRED_COMMAND_LOG_FIELDS,
    validate_phase3_command_log_manifest,
    validate_phase3_component_report,
)

__all__ = [
    "PHASE3_COMMAND_LOG_MANIFEST_SCHEMA",
    "PHASE3_COMPONENT_REPORT_SCHEMA",
    "PHASE3_TARGET_RUN_ID_PATTERN",
    "REQUIRED_COMMAND_LOG_FIELDS",
    "validate_phase3_command_log_manifest",
    "validate_phase3_component_report",
]
