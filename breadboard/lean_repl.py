from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ErrorSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class LeanError:
    severity: ErrorSeverity
    message: str
    pos_line: int | None = None
    pos_col: int | None = None
    signature: str | None = None


@dataclass
class Sorry:
    pos_line: int | None = None
    pos_col: int | None = None
    goal: str | None = None


@dataclass
class FirecrackerReplMetrics:
    repl_ms: float | None = None
    restore_ms: float | None = None


@dataclass
class CheckRequest:
    commands: list[str]
    state_ref: str | None = None
    timeout_s: float | None = None
    memory_mb: int | None = None
    max_heartbeats: int | None = None
    want_state: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckResult:
    request_id: str
    success: bool
    messages: list[str] = field(default_factory=list)
    errors: list[LeanError] = field(default_factory=list)
    sorries: list[Sorry] = field(default_factory=list)
    new_state_ref: str | None = None
    error_code: str | None = None
    error_detail: dict[str, Any] | None = None
    header_cache_hit: bool = False
    header_cache_miss: bool = False


class FirecrackerReplService:
    def submit_request_with_metrics(self, request: CheckRequest):
        raise NotImplementedError("FirecrackerReplService backend is not configured in this environment")

    def submit_batch_requests(self, requests: list[CheckRequest]):
        raise NotImplementedError("FirecrackerReplService backend is not configured in this environment")
