"""Lean REPL Service Module.

This module provides the infrastructure for running Lean4 checks at scale,
including:
- CheckRequest/CheckResult schemas for the API
- State blob management for stateless routing
- Worker pool management with LRU import caching
- Router for dispatching requests to warm workers

See EVOLAKE_ATP_MASTER_SPEC.md for the full design specification.
"""

from .schema import (
    # Enums
    CheckMode,
    ErrorSeverity,
    # Request/Response
    CheckLimits,
    CheckRequest,
    CheckResult,
    LeanError,
    Sorry,
    ResourceUsage,
    # State management
    StateBlob,
    # Utilities
    compute_header_hash,
    CANONICAL_MATHLIB_HEADER,
    CANONICAL_MATHLIB_HEADER_HASH,
    # REPL protocol
    REPLCommand,
    REPLResponse,
    # Version
    LEAN_REPL_SCHEMA_VERSION,
)

from .worker import LeanWorker, create_lean_worker
from .router import LeanRouter, create_lean_router
from .state_store import StateStore, get_state_store, reset_state_store

__all__ = [
    # Schema
    "CheckMode",
    "ErrorSeverity",
    "CheckLimits",
    "CheckRequest",
    "CheckResult",
    "LeanError",
    "Sorry",
    "ResourceUsage",
    "StateBlob",
    "compute_header_hash",
    "CANONICAL_MATHLIB_HEADER",
    "CANONICAL_MATHLIB_HEADER_HASH",
    "REPLCommand",
    "REPLResponse",
    "LEAN_REPL_SCHEMA_VERSION",
    # Worker & Router
    "LeanWorker",
    "create_lean_worker",
    "LeanRouter",
    "create_lean_router",
    # State Store
    "StateStore",
    "get_state_store",
    "reset_state_store",
]
