"""Compaction controller coordinating request projection, triggers, and recovery.

Owns CompactionSettings, the Compactor cascade, per-turn pass counting,
request view projection, threshold triggers, overflow recovery retries,
and snapshot persistence.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .methods import (
    CompactionContext,
    CompactionOutcome,
    CompactionReason,
    Compactor,
    RemoteCompactionPort,
    SummaryModel as SummaryModelProtocol,
)
from .images import drop_images
from .overflow import is_context_overflow
from .pruning import prune_tool_results
from .registry import build_compactor
from .settings import (
    CompactionSettings,
    resolve_threshold_tokens,
    settings_from_config,
)
from .snapcompact.inline import apply_inline_snapcompact
from .state import CompactionRecord, CompactionState, ProjectionTarget, strip_native_markers
from .summary_model import ConductorSummaryModel
from .tokens import (
    compaction_context_tokens,
    context_tokens_from_usage,
    estimate_messages_tokens,
)
from .transcript import role_of

logger = logging.getLogger(__name__)

# Window assumed when neither settings nor provider metadata name one.
_FALLBACK_CONTEXT_WINDOW = 128_000


class CompactionController:
    """Coordinates context compaction for BreadBoard conductors."""

    def __init__(
        self,
        config: Optional[Mapping[str, Any]] = None,
        *,
        settings: Optional[CompactionSettings] = None,
        compactor: Optional[Compactor] = None,
        summary_model: Optional[SummaryModelProtocol] = None,
        methods: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if settings is not None:
            self.settings = settings
        elif config is not None:
            raw_compaction = config.get("compaction")
            self.settings = settings_from_config(raw_compaction)
        else:
            self.settings = CompactionSettings()

        self.compactor = compactor or build_compactor(self.settings, methods=methods)
        self.summary_model = summary_model
        self._turn_passes: Dict[int, int] = {}
        # Provider attempt number for the request being built; bumped after
        # each successful overflow recovery so retried requests are recorded
        # as attempt 1, 2, ... instead of overwriting attempt 0.
        self.request_attempt = 0
        self._messages_len_at_last_record: Optional[int] = None

    def begin_request(self) -> None:
        self.request_attempt = 0

    def passes_this_turn(self, turn_index: int) -> int:
        return self._turn_passes.get(turn_index, 0)

    def record_pass(self, turn_index: int) -> None:
        self._turn_passes[turn_index] = self.passes_this_turn(turn_index) + 1

    def can_pass(self, turn_index: int) -> bool:
        return self.passes_this_turn(turn_index) < self.settings.max_passes_per_turn

    def resolve_context_window(
        self,
        *,
        conductor: Optional[Any] = None,
        session_state: Optional[Any] = None,
        provider_profile: Optional[Any] = None,
        model: Optional[str] = None,
    ) -> Optional[int]:
        """Resolve model context window from settings or provider/model metadata."""
        if self.settings.context_window and self.settings.context_window > 0:
            return self.settings.context_window

        if provider_profile is not None:
            cw = getattr(provider_profile, "context_window", None)
            if isinstance(cw, int) and cw > 0:
                return cw

        if session_state is not None:
            prof = getattr(session_state, "_episode_provider_profile", None)
            if prof is not None:
                cw = getattr(prof, "context_window", None)
                if isinstance(cw, int) and cw > 0:
                    return cw
            cw_meta = session_state.get_provider_metadata("context_window")
            if isinstance(cw_meta, int) and cw_meta > 0:
                return cw_meta
            cw_meta = session_state.get_provider_metadata("model_context_window")
            if isinstance(cw_meta, int) and cw_meta > 0:
                return cw_meta

        if conductor is not None and getattr(conductor, "config", None):
            providers = conductor.config.get("providers") or {}
            models = providers.get("models") or []
            target_model = model or getattr(conductor, "model", None)
            for m in models:
                if isinstance(m, Mapping) and m.get("model_id") == target_model:
                    cw = m.get("context_window") or m.get("context_length")
                    if isinstance(cw, int) and cw > 0:
                        return cw

        return None

    def resolve_supports_images(self, *, conductor: Optional[Any] = None, model: Optional[str] = None) -> bool:
        """Image input capability from the model's provider config entry.

        Accepts ``supports_images: true`` or an OMP-style ``input`` list
        containing ``"image"``. Unknown models are treated as text-only.
        """
        config = getattr(conductor, "config", None) if conductor is not None else None
        if not isinstance(config, Mapping):
            return False
        providers = config.get("providers") or {}
        models = providers.get("models") or [] if isinstance(providers, Mapping) else []
        target_model = model or getattr(conductor, "model", None)
        for entry in models:
            if not isinstance(entry, Mapping) or entry.get("model_id") != target_model:
                continue
            if entry.get("supports_images") is True:
                return True
            inputs = entry.get("input")
            return isinstance(inputs, (list, tuple)) and "image" in inputs
        return False

    def remote_ports_for(self, runtime: Optional[Any], client: Optional[Any], model: str) -> List[RemoteCompactionPort]:
        factory = getattr(runtime, "compaction_port", None)
        if not callable(factory) or client is None:
            return []
        try:
            port = factory(client=client, model=model)
        except Exception as exc:  # capability probe; remote method then reports unavailable
            logger.debug("compaction_port unavailable: %s", exc)
            return []
        return [port] if port is not None else []

    def prepare_request(
        self,
        session_state: Any,
        *,
        conductor: Optional[Any],
        runtime: Optional[Any],
        client: Optional[Any],
        model: str,
        turn_index: Optional[int],
    ) -> List[Dict[str, Any]]:
        """Threshold-compact if due, then return the provider request view."""
        target = projection_target_for(runtime, model)
        context_window = self.resolve_context_window(
            conductor=conductor, session_state=session_state, model=model
        )
        supports_images = self.resolve_supports_images(conductor=conductor, model=model)
        if self.should_trigger_threshold(
            session_state,
            context_window=context_window,
            last_usage=session_state.get_provider_metadata("usage"),
        ):
            self.compact(
                session_state,
                reason="threshold",
                target=target,
                conductor=conductor,
                runtime=runtime,
                client=client,
                context_window=context_window,
                turn_index=turn_index,
                supports_images=supports_images,
            )
        return self.build_request_view(
            session_state,
            target=target,
            runtime=runtime,
            supports_images=supports_images,
            turn_index=turn_index,
            context_window=context_window,
        )

    def build_request_view(
        self,
        session_state: Any,
        *,
        target: ProjectionTarget,
        runtime: Optional[Any] = None,
        supports_images: bool = False,
        turn_index: Optional[int] = None,
        context_window: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Build request view with optional pruning, projection, and inline imaging."""
        messages = session_state.provider_messages
        compaction_state: Optional[CompactionState] = getattr(session_state, "compaction_state", None)
        if compaction_state is None or (not self.settings.active and not compaction_state.records):
            # Disabled: the exact pre-compaction request view.
            return copy.deepcopy(messages)

        # 1. Prune tool results if enabled
        if self.settings.active and self.settings.prune.enabled:
            context = CompactionContext(
                messages=messages,
                state=compaction_state,
                settings=self.settings,
                reason="threshold",
                target=target,
                context_window=context_window or self.settings.context_window or _FALLBACK_CONTEXT_WINDOW,
                tokens_before=estimate_messages_tokens(compaction_state.project(messages, target)),
            )
            prune_record = prune_tool_results(context)
            if prune_record is not None:
                compaction_state.append(prune_record, messages)
                self._after_record_appended(session_state, prune_record, target, turn_index)

        # 2. Project view
        view = compaction_state.project(messages, target)

        # 3. Native markers replay only through a runtime that owns them.
        if not callable(getattr(runtime, "compaction_port", None)):
            view = strip_native_markers(view)

        # 4. Inline snapcompact if model supports images
        if self.settings.active and supports_images:
            view = apply_inline_snapcompact(
                view,
                self.settings,
                supports_images=supports_images,
                provider=target.provider,
                api=target.api,
                model_id=target.model,
            )

        return view

    def should_trigger_threshold(
        self,
        session_state: Any,
        *,
        context_window: Optional[int],
        last_usage: Optional[Any] = None,
    ) -> bool:
        """Check if history size exceeds the threshold tokens limit."""
        if not self.settings.active or context_window is None or context_window <= 0:
            return False

        messages = getattr(session_state, "provider_messages", None) or []
        if not messages:
            return False

        # Mid-turn gating: gate calls where the last message isn't a user message
        if not self.settings.mid_turn_enabled:
            last_msg = messages[-1]
            if role_of(last_msg) != "user":
                return False

        compaction_state: CompactionState = getattr(session_state, "compaction_state", None)
        if compaction_state is not None:
            # Estimate of active projected view
            stored_estimate = estimate_messages_tokens(
                compaction_state.project(messages, None)
            )
        else:
            stored_estimate = estimate_messages_tokens(messages)

        # Provider usage describes the last request sent. If a record was
        # appended since (no new message arrived), that request predates the
        # compaction and its usage would retrigger it.
        usage_stale = len(messages) == self._messages_len_at_last_record
        provider_tokens = None if usage_stale else context_tokens_from_usage(last_usage)
        current_tokens = compaction_context_tokens(provider_tokens, stored_estimate)

        threshold = resolve_threshold_tokens(context_window, self.settings)
        return current_tokens > threshold

    def _after_record_appended(
        self,
        session_state: Any,
        record: CompactionRecord,
        target: Optional[ProjectionTarget] = None,
        turn_index: Optional[int] = None,
    ) -> None:
        """Record lifecycle event and persist Product snapshot."""
        self._messages_len_at_last_record = len(session_state.provider_messages)
        try:
            session_state.record_lifecycle_event(
                "compaction_record_appended",
                {
                    "record_id": record.record_id,
                    "method": record.method,
                    "reason": record.reason,
                    "tokens_before": record.tokens_before,
                    "tokens_after": record.tokens_after,
                    "first_kept_index": record.first_kept_index,
                },
                turn=turn_index,
            )
        except Exception:
            pass

        if hasattr(session_state, "can_persist_compaction") and session_state.can_persist_compaction():
            try:
                compaction_state: CompactionState = getattr(session_state, "compaction_state", None)
                if compaction_state is not None:
                    projected = compaction_state.project(session_state.provider_messages, target)
                    session_state.persist_compaction_snapshot(projected)
            except Exception as exc:
                logger.warning("Failed to persist compaction snapshot: %s", exc)

    def compact(
        self,
        session_state: Any,
        *,
        reason: CompactionReason = "threshold",
        target: Optional[ProjectionTarget] = None,
        conductor: Optional[Any] = None,
        runtime: Optional[Any] = None,
        client: Optional[Any] = None,
        context_window: Optional[int] = None,
        turn_index: Optional[int] = None,
        custom_instructions: Optional[str] = None,
        remote_ports: Optional[Sequence[RemoteCompactionPort]] = None,
        supports_images: bool = False,
        order: Optional[Sequence[str]] = None,
        clock: Optional[Callable[[], str]] = None,
    ) -> CompactionOutcome:
        """Run the compaction cascade and handle persistence."""
        messages = session_state.provider_messages
        compaction_state: CompactionState = getattr(session_state, "compaction_state", None)
        if compaction_state is None:
            compaction_state = CompactionState()
            session_state.compaction_state = compaction_state

        resolved_target = target or ProjectionTarget("unknown", "unknown", "unknown")
        resolved_window = context_window or self.settings.context_window or _FALLBACK_CONTEXT_WINDOW
        if remote_ports is None:
            remote_ports = self.remote_ports_for(runtime, client, resolved_target.model)

        projected = compaction_state.project(messages, resolved_target)
        tokens_before = estimate_messages_tokens(projected)

        summarizer = self.summary_model
        if summarizer is None and callable(getattr(runtime, "invoke", None)):
            summarizer = ConductorSummaryModel(
                runtime=runtime,
                client=client,
                model=self.settings.summary_model or resolved_target.model,
                session_state=session_state,
                agent_config=getattr(conductor, "config", None) or {},
                turn_index=turn_index,
                recorder=getattr(conductor, "structured_request_recorder", None),
            )

        kwargs = {}
        if clock is not None:
            kwargs["clock"] = clock

        context = CompactionContext(
            messages=messages,
            state=compaction_state,
            settings=self.settings,
            reason=reason,
            target=resolved_target,
            context_window=resolved_window,
            tokens_before=tokens_before,
            summarizer=summarizer,
            remote_ports=remote_ports,
            supports_images=supports_images,
            custom_instructions=custom_instructions or self.settings.custom_instructions,
            **kwargs,
        )

        outcome = self.compactor.run(context, order=order)

        for record in outcome.records:
            self._after_record_appended(session_state, record, resolved_target, turn_index)

        return outcome

    def compact_now(
        self,
        session_state: Any,
        instructions: Optional[str] = None,
        **kwargs: Any,
    ) -> CompactionOutcome:
        """Manual compaction entry point."""
        return self.compact(
            session_state,
            reason="manual",
            custom_instructions=instructions,
            **kwargs,
        )

    def drop_images_now(
        self,
        session_state: Any,
        *,
        runtime: Optional[Any] = None,
        model: str = "unknown",
        turn_index: Optional[int] = None,
    ) -> Optional[CompactionRecord]:
        """Manual OMP ``dropImages``: replace every image with a placeholder.

        Appends an edit record (history itself is untouched) and returns it,
        or returns None when the history carries no images.
        """
        messages = session_state.provider_messages
        state: CompactionState = session_state.compaction_state
        target = projection_target_for(runtime, model)
        context = CompactionContext(
            messages=messages,
            state=state,
            settings=self.settings,
            reason="manual",
            target=target,
            context_window=self.settings.context_window or _FALLBACK_CONTEXT_WINDOW,
            tokens_before=estimate_messages_tokens(state.project(messages, target)),
        )
        record = drop_images(context)
        if record is None:
            return None
        state.append(record, messages)
        self._after_record_appended(session_state, record, target, turn_index)
        return record

    def recover_from_overflow(
        self,
        exc: BaseException,
        session_state: Any,
        *,
        turn_index: int,
        conductor: Optional[Any],
        runtime: Optional[Any],
        client: Optional[Any],
        model: str,
    ) -> bool:
        """Compact after a context-overflow provider error.

        True means a record was appended and the caller should retry the
        request once more. False (not an overflow, compaction inactive,
        ``overflow_policy="terminal"``, pass budget spent, or no method made
        progress) means the caller re-raises ``exc`` unchanged.
        """
        if (
            not self.settings.active
            or self.settings.overflow_policy != "compact"
            or not is_context_overflow(exc)
            or not self.can_pass(turn_index)
        ):
            return False
        self.record_pass(turn_index)
        outcome = self.compact(
            session_state,
            reason="overflow",
            target=projection_target_for(runtime, model),
            conductor=conductor,
            runtime=runtime,
            client=client,
            context_window=self.resolve_context_window(
                conductor=conductor, session_state=session_state, model=model
            ),
            turn_index=turn_index,
            supports_images=self.resolve_supports_images(conductor=conductor, model=model),
        )
        if not outcome.compacted:
            return False
        self.request_attempt += 1
        return True


def projection_target_for(runtime: Optional[Any], model: Any) -> ProjectionTarget:
    """Native-replay identity of the request about to be sent."""
    descriptor = getattr(runtime, "descriptor", None)
    return ProjectionTarget(
        provider=str(getattr(descriptor, "provider_id", None) or "unknown"),
        api=str(getattr(descriptor, "default_api_variant", None) or "unknown"),
        model=str(model),
    )
