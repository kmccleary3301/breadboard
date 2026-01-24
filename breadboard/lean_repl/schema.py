"""Lean REPL Service Schema Definitions.

This module defines the core data structures for the Lean4 REPL service,
including request/response schemas for check operations and state management.

Based on the EvoLake ATP Master Specification and the leanprover-community/repl API.

Reference: https://github.com/leanprover-community/repl
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional


# -----------------------------------------------------------------------------
# Enums
# -----------------------------------------------------------------------------


class CheckMode(str, Enum):
    """Mode for Lean REPL execution."""
    COMMAND = "command"
    TACTIC = "tactic"


class ErrorSeverity(str, Enum):
    """Severity levels for Lean errors/warnings."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


# -----------------------------------------------------------------------------
# Request Schemas
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckLimits:
    """Resource limits for a Lean check operation."""
    timeout_s: float = 30.0
    memory_mb: int = 4096
    max_heartbeats: int = 100_000  # Lean's internal elaboration limit


@dataclass(frozen=True)
class CheckRequest:
    """Request for executing Lean commands/tactics.

    This is the primary interface for the Lean REPL service. Requests can be:
    - Fresh (state_ref=None): Start from the base environment for header_hash
    - Continued (state_ref=CAS key): Unpickle state and continue from there

    Commands are batched for efficiency (amortize routing overhead).

    Attributes:
        request_id: Unique identifier for this request (for tracing/dedup)
        header_hash: Hash of canonical imports (e.g., sha256 of "import Mathlib")
        state_ref: CAS reference to pickled env/proofState, or None for fresh
        commands: List of Lean commands to execute sequentially
        mode: Whether executing commands or tactics
        limits: Resource limits (timeout, memory, heartbeats)
        want: Set of outputs to include in response
        trace_level: Verbosity of trace output
        metadata: Arbitrary metadata for logging/debugging
    """
    header_hash: str
    commands: List[str]
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    state_ref: Optional[str] = None
    mode: CheckMode = CheckMode.COMMAND
    limits: CheckLimits = field(default_factory=CheckLimits)
    want: frozenset[str] = field(default_factory=lambda: frozenset({
        "errors", "messages", "state"
    }))
    trace_level: Literal["minimal", "full"] = "minimal"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # Ensure want is a frozenset for hashability
        if not isinstance(self.want, frozenset):
            object.__setattr__(self, 'want', frozenset(self.want))


# -----------------------------------------------------------------------------
# Response Schemas
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class LeanError:
    """A single error/warning from Lean elaboration.

    The signature field provides a normalized identifier for error deduplication
    and pattern matching (e.g., for APOLLO repair).
    """
    severity: ErrorSeverity
    message: str
    pos_line: int
    pos_col: int
    end_line: Optional[int] = None
    end_col: Optional[int] = None
    signature: str = ""

    @staticmethod
    def compute_signature(severity: str, message: str) -> str:
        """Compute a normalized signature for error deduplication.

        Strips line numbers and specific identifiers to group similar errors.
        """
        # Normalize: lowercase, strip whitespace, hash first 100 chars of message
        normalized = f"{severity}:{message[:100].lower().strip()}"
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Sorry:
    """A sorry (proof hole) in the Lean code."""
    pos_line: int
    pos_col: int
    goal: Optional[str] = None


@dataclass(frozen=True)
class ResourceUsage:
    """Resource consumption metrics for a check operation."""
    wall_time_s: float
    cpu_time_s: Optional[float] = None
    memory_peak_mb: Optional[int] = None
    heartbeats: Optional[int] = None
    pickle_time_s: Optional[float] = None
    unpickle_time_s: Optional[float] = None


@dataclass
class CheckResult:
    """Result of executing Lean commands/tactics.

    Attributes:
        request_id: Echoed from request for correlation
        success: True if all commands executed without errors
        messages: All messages from Lean (info, warnings, errors)
        errors: Extracted errors with positions and signatures
        sorries: Any sorry/admit holes found
        goals: Current goal state (if in tactic mode)
        new_state_ref: CAS reference to pickled state (if "state" in want)
        new_env_id: Lean REPL env id (for debugging)
        resource_usage: Timing and memory metrics
        crashed: True if the REPL process crashed
        timeout: True if the operation timed out
        oom: True if out-of-memory killed the operation
    """
    request_id: str
    success: bool
    messages: List[str] = field(default_factory=list)
    errors: List[LeanError] = field(default_factory=list)
    sorries: List[Sorry] = field(default_factory=list)
    goals: Optional[str] = None
    new_state_ref: Optional[str] = None
    new_env_id: Optional[int] = None
    resource_usage: Optional[ResourceUsage] = None
    crashed: bool = False
    timeout: bool = False
    oom: bool = False

    @property
    def error_signatures(self) -> frozenset[str]:
        """Get unique error signatures for deduplication."""
        return frozenset(e.signature for e in self.errors if e.signature)

    @property
    def is_retriable(self) -> bool:
        """Check if this failure is potentially retriable."""
        return self.timeout or self.oom or self.crashed


# -----------------------------------------------------------------------------
# State Blob Schema
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class StateBlob:
    """Wrapper format for serialized Lean state.

    This provides versioning and compatibility tracking for state blobs
    stored in CAS. A state blob becomes invalid if any of these change:
    - Lean toolchain version
    - Mathlib commit/version
    - REPL version (if pickling format changes)
    - Header hash (import set)

    Attributes:
        format_version: Schema version for this blob format
        lean_toolchain: Lean version string (e.g., "leanprover/lean4:v4.14.0")
        mathlib_rev: Mathlib commit or lake-manifest hash
        repl_rev: REPL version (optional)
        header_hash: Hash of the import header used
        kind: Type of state ("env" or "proofState")
        pickle_bytes_b64: Base64-encoded zstd-compressed pickle bytes
        created_ts: Unix timestamp when this blob was created
    """
    format_version: int
    lean_toolchain: str
    mathlib_rev: str
    header_hash: str
    kind: Literal["env", "proofState"]
    pickle_bytes_b64: str
    repl_rev: Optional[str] = None
    created_ts: float = field(default_factory=time.time)

    def to_json(self) -> str:
        """Serialize to JSON for CAS storage."""
        return json.dumps({
            "format_version": self.format_version,
            "lean_toolchain": self.lean_toolchain,
            "mathlib_rev": self.mathlib_rev,
            "repl_rev": self.repl_rev,
            "header_hash": self.header_hash,
            "kind": self.kind,
            "pickle_bytes_b64": self.pickle_bytes_b64,
            "created_ts": self.created_ts,
        }, sort_keys=True)

    @staticmethod
    def from_json(data: str) -> "StateBlob":
        """Deserialize from JSON."""
        d = json.loads(data)
        return StateBlob(
            format_version=d["format_version"],
            lean_toolchain=d["lean_toolchain"],
            mathlib_rev=d["mathlib_rev"],
            repl_rev=d.get("repl_rev"),
            header_hash=d["header_hash"],
            kind=d["kind"],
            pickle_bytes_b64=d["pickle_bytes_b64"],
            created_ts=d.get("created_ts", 0.0),
        )

    def is_compatible_with(
        self,
        lean_toolchain: str,
        mathlib_rev: str,
        header_hash: str,
    ) -> bool:
        """Check if this blob is compatible with the given environment."""
        return (
            self.lean_toolchain == lean_toolchain
            and self.mathlib_rev == mathlib_rev
            and self.header_hash == header_hash
        )


# -----------------------------------------------------------------------------
# Canonical Header Utilities
# -----------------------------------------------------------------------------


def compute_header_hash(imports: List[str], options: Optional[Dict[str, Any]] = None) -> str:
    """Compute a deterministic hash for a set of imports.

    Args:
        imports: List of import statements (e.g., ["import Mathlib", "import Aesop"])
        options: Optional set_option declarations

    Returns:
        SHA256 hash (first 16 chars) of the canonical header
    """
    parts = sorted(imports)
    if options:
        for k, v in sorted(options.items()):
            parts.append(f"set_option {k} {v}")
    canonical = "\n".join(parts)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# Default canonical header for most proof attempts
CANONICAL_MATHLIB_HEADER = ["import Mathlib"]
CANONICAL_MATHLIB_HEADER_HASH = compute_header_hash(CANONICAL_MATHLIB_HEADER)


# -----------------------------------------------------------------------------
# REPL JSON Protocol (matches leanprover-community/repl)
# -----------------------------------------------------------------------------


@dataclass
class REPLCommand:
    """A command to send to the Lean REPL JSON interface.

    This matches the format expected by leanprover-community/repl.
    """
    cmd: Optional[str] = None
    env: Optional[int] = None
    tactic: Optional[str] = None
    proofState: Optional[int] = None
    pickleTo: Optional[str] = None
    unpickleEnvFrom: Optional[str] = None
    unpickleProofStateFrom: Optional[str] = None

    def to_json(self) -> str:
        """Serialize to JSON, omitting None fields."""
        d = {}
        if self.cmd is not None:
            d["cmd"] = self.cmd
        if self.env is not None:
            d["env"] = self.env
        if self.tactic is not None:
            d["tactic"] = self.tactic
        if self.proofState is not None:
            d["proofState"] = self.proofState
        if self.pickleTo is not None:
            d["pickleTo"] = self.pickleTo
        if self.unpickleEnvFrom is not None:
            d["unpickleEnvFrom"] = self.unpickleEnvFrom
        if self.unpickleProofStateFrom is not None:
            d["unpickleProofStateFrom"] = self.unpickleProofStateFrom
        return json.dumps(d)


@dataclass
class REPLResponse:
    """Response from the Lean REPL.

    This matches the format returned by leanprover-community/repl.
    """
    env: Optional[int] = None
    proofState: Optional[int] = None
    messages: List[Dict[str, Any]] = field(default_factory=list)
    sorries: List[Dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def from_json(data: str) -> "REPLResponse":
        """Parse REPL JSON response."""
        d = json.loads(data)
        return REPLResponse(
            env=d.get("env"),
            proofState=d.get("proofState"),
            messages=d.get("messages", []),
            sorries=d.get("sorries", []),
        )


# -----------------------------------------------------------------------------
# Schema Version
# -----------------------------------------------------------------------------

LEAN_REPL_SCHEMA_VERSION = 1
