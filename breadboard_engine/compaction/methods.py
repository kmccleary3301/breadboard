"""Compaction method protocol, collaborator ports, and the pass context.

A method (or a composed selector/reducer/placement step) that cannot run for
the current route or history raises :class:`MethodUnavailable`; one that runs
and fails raises any other exception. :class:`CompactionCancelled` is a
deliberate abort. The pipeline (``pipeline.py``) turns each into a stage
status and routes on it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import datetime as _dt
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Literal,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    runtime_checkable,
)

from .settings import CompactionSettings
from .state import (
    CompactionRecord,
    CompactionState,
    MessageEdit,
    NativeCompaction,
    ProjectionTarget,
    record_id_for,
)

CompactionReason = Literal["threshold", "overflow", "manual", "mid_turn", "idle"]
SummaryPurpose = Literal["summary", "update_summary", "short_summary", "turn_prefix", "handoff", "branch_summary"]


class CompactionError(RuntimeError):
    pass


class MethodUnavailable(CompactionError):
    """The method does not apply to this route or history; try the next."""


class CompactionCancelled(CompactionError):
    """Deliberate abort; the cascade stops."""


class NativeCompactionError(CompactionError):
    """Every provider-native protocol for the route failed."""


@dataclass(frozen=True)
class SummaryRequest:
    system: str
    messages: Tuple[Mapping[str, Any], ...]
    max_tokens: int
    purpose: SummaryPurpose
    model: Optional[str] = None


@dataclass(frozen=True)
class SummaryResponse:
    text: str
    usage: Optional[Mapping[str, Any]] = None
    model: Optional[str] = None


@runtime_checkable
class SummaryModel(Protocol):
    """Tool-free text completion used by LLM-backed methods."""

    def complete(self, request: SummaryRequest) -> SummaryResponse: ...


@runtime_checkable
class RemoteCompactionPort(Protocol):
    """Provider-native compaction for one route, supplied by its runtime."""

    provider: str
    api: str

    def supports(self, model: str) -> bool: ...

    def compact(self, context: "CompactionContext") -> CompactionRecord: ...


@runtime_checkable
class ArtifactSink(Protocol):
    """Stores elided content and returns a model-readable reference."""

    def store(self, name: str, content: str, media_type: str = "text/plain") -> str: ...


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class CompactionContext:
    """Inputs for one compaction pass. ``messages`` is the full history."""

    messages: Sequence[Mapping[str, Any]]
    state: CompactionState
    settings: CompactionSettings
    reason: CompactionReason
    target: ProjectionTarget
    context_window: int
    tokens_before: int
    summarizer: Optional[SummaryModel] = None
    remote_ports: Sequence[RemoteCompactionPort] = ()
    artifacts: Optional[ArtifactSink] = None
    supports_images: bool = False
    custom_instructions: Optional[str] = None
    clock: Callable[[], str] = _utc_now

    def projected(self) -> List[Dict[str, Any]]:
        return self.state.project(self.messages, self.target)

    def new_record(
        self,
        *,
        method: str,
        first_kept_index: Optional[int] = None,
        summary: Optional[str] = None,
        short_summary: Optional[str] = None,
        summary_messages: Sequence[Mapping[str, Any]] = (),
        native: Optional[NativeCompaction] = None,
        edits: Sequence[MessageEdit] = (),
        details: Optional[Mapping[str, Any]] = None,
        warning: Optional[str] = None,
        prefix_end: Optional[int] = None,
    ) -> CompactionRecord:
        sequence = self.state.next_sequence
        draft = CompactionRecord(
            record_id="",
            sequence=sequence,
            method=method,
            reason=self.reason,
            created_at=self.clock(),
            tokens_before=self.tokens_before,
            history_length=len(self.messages),
            first_kept_index=first_kept_index,
            summary=summary,
            short_summary=short_summary,
            summary_messages=tuple(summary_messages),
            native=native,
            edits=tuple(edits),
            details=dict(details or {}),
            warning=warning,
            prefix_end=prefix_end,
        )
        payload = draft.to_dict()
        payload.pop("record_id")
        payload.pop("created_at")
        return replace(draft, record_id=record_id_for(payload))


@runtime_checkable
class CompactionMethod(Protocol):
    name: str

    def run(self, context: CompactionContext) -> CompactionRecord: ...

