"""Compaction method protocol, collaborator ports, and the method cascade.

Methods run in ``CompactionSettings.method_order`` (OMP
``DEFAULT_COMPACTION_METHOD_ORDER``). A method that cannot run for the
current route raises :class:`MethodUnavailable`; one that runs and fails
raises any other exception. Either way the cascade moves to the next method.
:class:`CompactionCancelled` stops the cascade.
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
    Tuple,
    runtime_checkable,
)

from .settings import CompactionSettings, resolve_threshold_tokens, resolve_budget_reserve_tokens
from .state import (
    CompactionRecord,
    CompactionState,
    MessageEdit,
    NativeCompaction,
    ProjectionTarget,
    record_id_for,
)
from .tokens import estimate_messages_tokens

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

    @property
    def threshold_tokens(self) -> int:
        return resolve_threshold_tokens(self.context_window, self.settings)

    @property
    def target_tokens(self) -> int:
        """Size a pass must reach: under threshold, or under window minus reserve on overflow."""
        if self.reason == "overflow":
            return max(1, self.context_window - resolve_budget_reserve_tokens(self.context_window, self.settings))
        return self.threshold_tokens

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
        )
        payload = draft.to_dict()
        payload.pop("record_id")
        payload.pop("created_at")
        return replace(draft, record_id=record_id_for(payload))


@runtime_checkable
class CompactionMethod(Protocol):
    name: str

    def run(self, context: CompactionContext) -> CompactionRecord: ...


@dataclass(frozen=True)
class MethodAttempt:
    method: str
    status: Literal["applied", "unavailable", "failed", "no_progress", "not_installed"]
    detail: Optional[str] = None
    tokens_after: Optional[int] = None


@dataclass(frozen=True)
class CompactionOutcome:
    records: Tuple[CompactionRecord, ...]
    attempts: Tuple[MethodAttempt, ...]
    tokens_before: int
    tokens_after: int
    reached_target: bool

    @property
    def compacted(self) -> bool:
        return bool(self.records)


class Compactor:
    """Runs the configured method cascade against a :class:`CompactionState`."""

    def __init__(
        self,
        settings: CompactionSettings,
        methods: Mapping[str, CompactionMethod],
        *,
        count_view_tokens: Callable[[Sequence[Mapping[str, Any]]], int] = estimate_messages_tokens,
    ) -> None:
        self.settings = settings
        self.methods = dict(methods)
        self._count = count_view_tokens

    def run(
        self,
        context: CompactionContext,
        *,
        order: Optional[Sequence[str]] = None,
    ) -> CompactionOutcome:
        """Apply methods until the view fits ``context.target_tokens``.

        Edit-only methods (shake) that shrink the view but not enough are kept
        and the cascade continues; a boundary method that reaches the target
        ends it. A method that does not shrink the view is discarded.
        """
        attempts: List[MethodAttempt] = []
        records: List[CompactionRecord] = []
        current = self._count(context.projected())
        target = context.target_tokens
        for name in order if order is not None else self.settings.method_order:
            if current <= target and records:
                break
            method = self.methods.get(name)
            if method is None:
                attempts.append(MethodAttempt(name, "not_installed"))
                continue
            try:
                record = method.run(context)
                context.state.validate(record, context.messages)
            except CompactionCancelled:
                raise
            except MethodUnavailable as exc:
                attempts.append(MethodAttempt(name, "unavailable", str(exc) or None))
                continue
            except Exception as exc:  # cascade to the next method
                attempts.append(MethodAttempt(name, "failed", f"{type(exc).__name__}: {exc}"))
                continue
            probe = CompactionState([*context.state.records, record])
            after = self._count(probe.project(context.messages, context.target))
            if after >= current:
                attempts.append(MethodAttempt(name, "no_progress", None, after))
                continue
            record = replace(record, tokens_after=after)
            context.state.append(record, context.messages)
            records.append(record)
            current = after
            attempts.append(MethodAttempt(name, "applied", None, after))
        return CompactionOutcome(
            tuple(records), tuple(attempts), context.tokens_before, current, bool(records) and current <= target
        )
