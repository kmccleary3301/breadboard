"""ProviderAccess owned by the canonical provider runtime.

The author-facing object contains only typed requests, owner-minted identity,
and canonical provider outcomes.  Runtime clients, credentials, routing, and
exchange recording remain on the trusted conductor side.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from breadboard.modules.authority import AdmissionGrant
from breadboard.modules.provider import (
    ProviderCallRequest,
    ProviderCancelled,
    ProviderCorrelation,
    ProviderDone,
    ProviderErrorTerminal,
    ProviderEvent,
    ProviderExchangeHandle,
    ProviderRoute,
    ProviderStreamItem,
    ProviderUnknown,
)
from breadboard.modules.transport import RequestKey
from breadboard_engine.messaging.markdown_logger import MarkdownLogger

from .contract_exchange import ProviderExchangeV2
from .contract_messages import ProviderIdentity
from .contract_runtime import (
    ProviderRuntime,
    ProviderRuntimeContext,
    ProviderRuntimeError,
)
from .invoker import ProviderInvoker
from .registry import provider_registry
from .routing import ProviderRouteError, provider_router


class ProviderAccessError(RuntimeError):
    """A typed refusal at the provider owner boundary."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class ProviderRuntimeConfig:
    """Frozen references to one already-prepared canonical invocation.

    ``client`` is an owner-internal value and is normally ``None``: the
    existing ``ProviderInvoker`` owns the credential lease.  It is never
    serialized or returned through the author facade.
    """

    invoker: ProviderInvoker
    runtime: ProviderRuntime
    client: Any
    runtime_context: ProviderRuntimeContext
    session_state: Any
    markdown_logger: MarkdownLogger
    turn_index: int
    route_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.invoker, ProviderInvoker):
            raise TypeError("invoker must be ProviderInvoker")
        if not isinstance(self.runtime, ProviderRuntime):
            raise TypeError("runtime must be ProviderRuntime")
        if not isinstance(self.runtime_context, ProviderRuntimeContext):
            raise TypeError("runtime_context must be ProviderRuntimeContext")
        if type(self.turn_index) is not int or self.turn_index < 0:
            raise ValueError("turn_index must be a non-negative integer")
        if self.route_id is not None and (
            not isinstance(self.route_id, str) or not self.route_id.strip()
        ):
            raise ValueError("route_id must be a non-empty string or None")


class ProviderRuntimePreparer(Protocol):
    """Narrow host seam for a conductor-owned prepared runtime."""

    def prepare(
        self,
        call: ProviderCallRequest,
        correlation: ProviderCorrelation,
        route: ProviderRoute,
        grant: AdmissionGrant,
        cancel_requested: Callable[[], bool],
    ) -> ProviderRuntimeConfig:
        """Resolve routing/runtime and return one frozen invocation config."""
        ...


class ConductorProviderRuntimePreparer:
    """Resolve a call through the existing router, registry, and invoker.

    This is an owner-side preparation seam, not an author-visible client or a
    second provider implementation.  The invoker retains the configured
    credential lease; preparation itself performs no provider effect.
    """

    def __init__(
        self,
        *,
        conductor: Any,
        config: Mapping[str, Any],
        session_state: Any,
        markdown_logger: MarkdownLogger,
        turn_index: Callable[[], int],
    ) -> None:
        self._conductor = conductor
        self._config = dict(config)
        self._session_state = session_state
        self._markdown_logger = markdown_logger
        self._turn_index = turn_index

    def prepare(
        self,
        call: ProviderCallRequest,
        correlation: ProviderCorrelation,
        route: ProviderRoute,
        grant: AdmissionGrant,
        cancel_requested: Callable[[], bool],
    ) -> ProviderRuntimeConfig:
        del route, grant
        invoker = getattr(self._conductor, "provider_invoker", None)
        if not isinstance(invoker, ProviderInvoker):
            raise ProviderAccessError(
                "provider_unavailable", "the admitted conductor has no ProviderInvoker"
            )
        requested_route = call.provider.route_id or call.provider.model
        try:
            descriptor, resolved_model = provider_router.get_runtime_descriptor(
                requested_route
            )
            runtime = provider_registry.create_runtime(descriptor)
        except (ProviderRouteError, ProviderRuntimeError) as error:
            raise ProviderAccessError("provider_route", str(error)) from error
        if descriptor.provider_id != call.provider.provider_id:
            raise ProviderAccessError(
                "stale_route", "provider identity does not match the selected route"
            )
        if descriptor.runtime_id != call.provider.runtime_id:
            raise ProviderAccessError(
                "stale_route", "runtime identity does not match the selected route"
            )
        try:
            turn_index = self._turn_index()
        except Exception as error:
            raise ProviderAccessError(
                "provider_unavailable", "the admitted turn index is unavailable"
            ) from error
        if type(turn_index) is not int or turn_index < 0:
            raise ProviderAccessError(
                "provider_unavailable", "the admitted turn index is invalid"
            )
        context = ProviderRuntimeContext(
            session_state=self._session_state,
            agent_config=dict(self._config),
            stream=call.request.stream,
            extra={
                "route_id": requested_route,
                "turn_index": turn_index,
                "model": resolved_model,
            },
            session_id=correlation.session_id,
            input_id=correlation.input_id,
            turn_id=correlation.turn_id,
            cancel_requested=cancel_requested,
        )
        return ProviderRuntimeConfig(
            invoker=invoker,
            runtime=runtime,
            client=None,
            runtime_context=context,
            session_state=self._session_state,
            markdown_logger=self._markdown_logger,
            turn_index=turn_index,
            route_id=requested_route,
        )


@dataclass(slots=True)
class _ProviderOperation:
    call: ProviderCallRequest
    exchange_id: str
    correlation: ProviderCorrelation
    route: ProviderRoute
    runtime_config: ProviderRuntimeConfig
    cancel_event: threading.Event
    condition: threading.Condition
    events: deque[ProviderEvent] = field(default_factory=deque)
    seen_sequences: set[int] = field(default_factory=set)
    exchange: ProviderExchangeV2 | None = None
    terminal: ProviderDone | ProviderErrorTerminal | ProviderCancelled | None = None
    done: bool = False
    terminal_sent: bool = False

    @property
    def output_emitted(self) -> bool:
        if self.exchange is not None:
            return bool(self.exchange.terminal.output_emitted)
        return any(
            event.kind
            in {
                "text_start",
                "text_delta",
                "text_end",
                "thinking_start",
                "thinking_delta",
                "thinking_end",
                "tool_call_start",
                "tool_call_delta",
                "tool_call_end",
            }
            for event in self.events
        )

    @property
    def last_sequence(self) -> int | None:
        if not self.seen_sequences:
            return None
        return max(self.seen_sequences)


class ProviderAccess:
    """Asynchronous, bounded author provider facade over ``ProviderInvoker``."""

    def __init__(
        self,
        *,
        preparer: ProviderRuntimePreparer,
        worker_key: RequestKey,
        session_id: str,
        current_turn_id: Callable[[], str],
        current_input_id: Callable[[], str],
        route: ProviderRoute,
        grant: AdmissionGrant,
        fence: Callable[[], None] | None = None,
        max_active_operations: int = 1,
        max_buffered_events: int = 64,
    ) -> None:
        if not isinstance(worker_key, RequestKey):
            raise TypeError("worker_key must be RequestKey")
        if not isinstance(session_id, str) or not session_id.strip():
            raise TypeError("session_id must be a non-empty string")
        if not callable(current_turn_id):
            raise TypeError("current_turn_id must be callable")
        if not callable(current_input_id):
            raise TypeError("current_input_id must be callable")
        if not isinstance(route, ProviderRoute):
            raise TypeError("route must be ProviderRoute")
        if not isinstance(grant, AdmissionGrant):
            raise TypeError("grant must be AdmissionGrant")
        if not callable(getattr(preparer, "prepare", None)):
            raise TypeError("preparer must implement prepare")
        if type(max_active_operations) is not int or max_active_operations < 1:
            raise ValueError("max_active_operations must be a positive integer")
        if type(max_buffered_events) is not int or max_buffered_events < 1:
            raise ValueError("max_buffered_events must be a positive integer")
        self._preparer = preparer
        self._session_id = session_id
        self._worker_key = worker_key
        self._current_turn_id = current_turn_id
        self._current_input_id = current_input_id
        self._route = route
        self._grant = grant
        self._fence = fence
        self._max_active_operations = max_active_operations
        self._max_buffered_events = max_buffered_events
        self._operations: dict[str, _ProviderOperation] = {}
        self._lock = threading.RLock()
        self._closed = False

    @classmethod
    def from_runtime_preparer(
        cls,
        *,
        preparer: ProviderRuntimePreparer,
        worker_key: RequestKey,
        session_id: str,
        current_turn_id: Callable[[], str],
        current_input_id: Callable[[], str],
        route: ProviderRoute,
        grant: AdmissionGrant,
        fence: Callable[[], None] | None = None,
        max_active_operations: int = 1,
        max_buffered_events: int = 64,
    ) -> "ProviderAccess":
        return cls(
            preparer=preparer,
            worker_key=worker_key,
            session_id=session_id,
            current_turn_id=current_turn_id,
            current_input_id=current_input_id,
            route=route,
            grant=grant,
            fence=fence,
            max_active_operations=max_active_operations,
            max_buffered_events=max_buffered_events,
        )

    def start(self, call: ProviderCallRequest) -> ProviderExchangeHandle:
        if not isinstance(call, ProviderCallRequest):
            raise TypeError("provider start requires ProviderCallRequest")
        self._check_fence()
        self._check_grant(call)
        with self._lock:
            if self._closed:
                raise ProviderAccessError("owner_closed", "provider owner is closed")
            active = sum(not operation.done for operation in self._operations.values())
            if active >= self._max_active_operations:
                raise ProviderAccessError(
                    "capacity_exhausted", "provider active-operation limit reached"
                )
            try:
                input_id = self._current_input_id()
                turn_id = self._current_turn_id()
                if (
                    not isinstance(input_id, str)
                    or not input_id.strip()
                    or not isinstance(turn_id, str)
                    or not turn_id.strip()
                ):
                    raise ProviderAccessError(
                        "provider_unavailable",
                        "current input or turn identity is unavailable",
                    )
                correlation = ProviderCorrelation(
                    session_id=self._session_id,
                    input_id=input_id,
                    turn_id=turn_id,
                )
            except ProviderAccessError:
                raise
            except (TypeError, ValueError) as error:
                raise ProviderAccessError(
                    "provider_unavailable", "current turn identity is invalid"
                ) from error
            exchange_id = f"px_{uuid.uuid4().hex}"
            cancel_event = threading.Event()
            try:
                runtime_config = self._preparer.prepare(
                    call,
                    correlation,
                    self._route,
                    self._grant,
                    cancel_event.is_set,
                )
                if not isinstance(runtime_config, ProviderRuntimeConfig):
                    raise TypeError("provider preparer returned an invalid runtime config")
                self._check_runtime_identity(call.provider, runtime_config)
            except ProviderAccessError:
                raise
            except Exception as error:
                raise ProviderAccessError(
                    "provider_unavailable",
                    f"provider preparation failed: {type(error).__name__}",
                ) from error
            state = _ProviderOperation(
                call=call,
                exchange_id=exchange_id,
                correlation=correlation,
                route=self._route,
                runtime_config=runtime_config,
                cancel_event=cancel_event,
                condition=threading.Condition(self._lock),
            )
            self._operations[exchange_id] = state
            thread = threading.Thread(
                target=self._run,
                args=(state,),
                name=f"provider-exchange-{exchange_id}",
                daemon=True,
            )
            thread.start()
        return ProviderExchangeHandle(
            exchange_id=exchange_id,
            stream_id=f"stream:{exchange_id}",
            route=self._route,
        )

    def next_event(self, handle: ProviderExchangeHandle) -> ProviderStreamItem:
        if not isinstance(handle, ProviderExchangeHandle):
            raise TypeError("provider next_event requires ProviderExchangeHandle")
        self._check_fence()
        state = self._state_for(handle)
        with state.condition:
            if state.events:
                item = state.events.popleft()
                state.condition.notify_all()
                return item
            if state.done:
                if not state.terminal_sent and state.terminal is not None:
                    state.terminal_sent = True
                    return state.terminal
                return self._unknown(
                    state,
                    "provider stream is already closed"
                    if state.terminal is not None
                    else "provider exchange settlement is unknown",
                )
            return self._unknown(state, "provider exchange is still running")

    def cancel(
        self, handle: ProviderExchangeHandle, reason: str = "caller_cancelled"
    ) -> ProviderCancelled | ProviderUnknown:
        if not isinstance(handle, ProviderExchangeHandle):
            raise TypeError("provider cancel requires ProviderExchangeHandle")
        self._check_fence()
        state = self._state_for(handle)
        return self._cancel_owned(state, reason)

    def route_for(self, exchange_id: str) -> ProviderRoute:
        state = self._operations.get(exchange_id)
        if state is None:
            raise ProviderAccessError("unknown_request", "provider exchange is not owned")
        return state.route

    def quiescence(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                f"provider:{exchange_id}"
                for exchange_id, state in sorted(self._operations.items())
                if not state.done
            )

    def close(self, reason: str = "owner_closed") -> tuple[str, ...]:
        with self._lock:
            self._closed = True
            states = tuple(state for state in self._operations.values() if not state.done)
        for state in states:
            self._cancel_owned(state, reason)
        return self.quiescence()

    def _run(self, state: _ProviderOperation) -> None:
        runtime_config = state.runtime_config
        context = runtime_config.runtime_context
        context.session_id = state.correlation.session_id
        context.input_id = state.correlation.input_id
        context.turn_id = state.correlation.turn_id
        context.cancel_requested = state.cancel_event.is_set
        context.extra["session_id"] = state.correlation.session_id
        context.extra["input_id"] = state.correlation.input_id
        context.extra["turn_id"] = state.correlation.turn_id
        try:
            result, _ = runtime_config.invoker.invoke(
                runtime=runtime_config.runtime,
                client=runtime_config.client,
                model=state.call.provider.model,
                send_messages=list(state.call.request.messages),
                tools_schema=list(state.call.request.tools),
                stream_responses=state.call.request.stream,
                runtime_context=context,
                session_state=runtime_config.session_state,
                markdown_logger=runtime_config.markdown_logger,
                turn_index=runtime_config.turn_index,
                route_id=runtime_config.route_id,
                exchange_id=state.exchange_id,
            )
            payload = (
                result.metadata.get("provider_exchange")
                if isinstance(result.metadata, Mapping)
                else None
            )
            exchange = self._decode_exchange(
                state, payload or context.extra.get("provider_exchange")
            )
        except ProviderRuntimeError as error:
            try:
                exchange = self._decode_exchange(
                    state, context.extra.get("provider_exchange"), error=error
                )
            except Exception:
                self._finish_unknown(state)
                return
        except Exception as error:
            try:
                exchange = self._decode_exchange(
                    state, context.extra.get("provider_exchange"), error=error
                )
            except Exception:
                self._finish_unknown(state)
                return
        self._finish(state, exchange)

    def _decode_exchange(
        self,
        state: _ProviderOperation,
        payload: object,
        *,
        error: BaseException | None = None,
    ) -> ProviderExchangeV2:
        if isinstance(payload, Mapping):
            exchange = ProviderExchangeV2.from_dict(payload)
            if exchange.exchange_id != state.exchange_id:
                raise ProviderAccessError(
                    "provider_identity_mismatch", "provider owner returned another exchange"
                )
            if exchange.correlation != state.correlation:
                raise ProviderAccessError(
                    "provider_identity_mismatch", "provider owner returned another correlation"
                )
            if exchange.provider.provider_id != state.call.provider.provider_id:
                raise ProviderAccessError(
                    "provider_identity_mismatch", "provider owner returned another provider"
                )
            return exchange.validate()
        recorder = state.runtime_config.runtime_context.exchange_recorder
        if recorder is not None:
            terminal = self._terminal_for_error(error, bool(recorder.output_emitted))
            return ProviderExchangeV2(
                schema_version="bb.provider_exchange.v2",
                exchange_id=state.exchange_id,
                correlation=state.correlation,
                provider=recorder.provider,
                request=recorder.request,
                events=list(recorder.events),
                terminal=terminal,
            ).validate()
        terminal = self._terminal_for_error(error, False)
        raise ProviderAccessError(
            "provider_unknown", "provider exchange settlement is unknown"
        ) from error

    @staticmethod
    def _terminal_for_error(
        error: BaseException | None, output_emitted: bool
    ) -> ProviderErrorTerminal | ProviderCancelled:
        if isinstance(error, ProviderRuntimeError):
            details = error.details if isinstance(error.details, Mapping) else {}
            if details.get("cancelled") is True:
                owner = details.get("cancel_owner")
                if owner not in {"caller", "provider", "transport", "engine"}:
                    owner = "engine"
                return ProviderCancelled(
                    output_emitted=output_emitted or error.output_emitted,
                    owner=owner,
                    reason_code=str(details.get("reason_code") or "cancelled"),
                )
            category = (
                error.kind
                if error.kind in {"adapter", "provider", "transport", "protocol", "configuration"}
                else "adapter"
            )
            status = details.get("status_code")
            if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
                status = None
            return ProviderErrorTerminal(
                output_emitted=output_emitted or error.output_emitted,
                code=error.safe_code,
                category=category,
                retryable=error.replay_safe and not output_emitted,
                http_status=status,
            )
        return ProviderErrorTerminal(
            output_emitted=output_emitted,
            code="provider_runtime_error",
            category="adapter",
            retryable=False,
        )

    def _publish_event(self, state: _ProviderOperation, event: ProviderEvent) -> None:
        with state.condition:
            while len(state.events) >= self._max_buffered_events:
                state.condition.wait()
            if event.sequence in state.seen_sequences:
                return
            state.events.append(event)
            state.seen_sequences.add(event.sequence)
            state.condition.notify_all()

    def _finish(self, state: _ProviderOperation, exchange: ProviderExchangeV2) -> None:
        with state.condition:
            for event in exchange.events:
                if event.sequence in state.seen_sequences:
                    continue
                while len(state.events) >= self._max_buffered_events:
                    state.condition.wait()
                state.events.append(event)
                state.seen_sequences.add(event.sequence)
            state.exchange = exchange
            state.terminal = exchange.terminal
            state.done = True
            state.condition.notify_all()

    def _finish_unknown(self, state: _ProviderOperation) -> None:
        with state.condition:
            state.done = True
            state.condition.notify_all()

    def _state_for(self, handle: ProviderExchangeHandle) -> _ProviderOperation:
        state = self._operations.get(handle.exchange_id)
        if (
            state is None
            or state.route != handle.route
            or handle.stream_id != f"stream:{handle.exchange_id}"
        ):
            raise ProviderAccessError("unknown_request", "provider handle is not owned")
        return state

    def _unknown(self, state: _ProviderOperation, reason: str) -> ProviderUnknown:
        return ProviderUnknown(
            exchange_id=state.exchange_id,
            correlation=state.correlation,
            route=state.route,
            reason=reason,
            output_emitted=state.output_emitted,
            last_sequence=state.last_sequence,
            evidence_refs=(f"exchange:{state.exchange_id}",),
        )

    def _cancel_owned(
        self, state: _ProviderOperation, reason: str
    ) -> ProviderCancelled | ProviderUnknown:
        with state.condition:
            if state.done:
                if isinstance(state.terminal, ProviderCancelled):
                    return state.terminal
                return self._unknown(state, "provider exchange already settled")
            state.cancel_event.set()
            state.runtime_config.runtime_context.cancel_requested = state.cancel_event.is_set
            state.condition.notify_all()
            return self._unknown(state, reason or "provider cancellation requested")

    def _check_fence(self) -> None:
        if self._fence is not None:
            self._fence()

    def _check_grant(self, call: ProviderCallRequest) -> None:
        expires_at_ms = self._grant.expires_at_ms
        if expires_at_ms is not None and time.time_ns() // 1_000_000 >= expires_at_ms:
            raise ProviderAccessError("authority_expired", "provider admission grant expired")
        if call.provider.provider_id not in self._grant.declaration.provider_ids:
            raise ProviderAccessError("authority_denied", "provider is outside the admission grant")

    @staticmethod
    def _check_runtime_identity(
        provider: ProviderIdentity, runtime_config: ProviderRuntimeConfig
    ) -> None:
        descriptor = getattr(runtime_config.runtime, "descriptor", None)
        provider_id = getattr(descriptor, "provider_id", None)
        runtime_id = getattr(descriptor, "runtime_id", None)
        if isinstance(provider_id, str) and provider_id != provider.provider_id:
            raise ProviderAccessError(
                "stale_route", "provider runtime does not match the admitted identity"
            )
        if isinstance(runtime_id, str) and runtime_id != provider.runtime_id:
            raise ProviderAccessError(
                "stale_route", "provider runtime does not match the admitted identity"
            )
        if provider.route_id is not None and provider.route_id != runtime_config.route_id:
            raise ProviderAccessError(
                "stale_route", "provider call supplied a different route"
            )


__all__ = [
    "ConductorProviderRuntimePreparer",
    "ProviderAccess",
    "ProviderAccessError",
    "ProviderRuntimeConfig",
    "ProviderRuntimePreparer",
]
