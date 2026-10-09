"""Compaction controller: request projection, triggers, overflow recovery.

The agent config's ``compaction`` block selects a preset (``presets/``); the
preset's recipe supplies the triggers, per-reason targets, overflow budget,
request-view steps and the stage pipeline. The controller owns when those
run, per-turn pass counting, lifecycle events and snapshot persistence.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .methods import (
    CompactionCancelled,
    CompactionContext,
    CompactionReason,
    RemoteCompactionPort,
    SummaryModel as SummaryModelProtocol,
)
from .images import drop_images
from .overflow import is_context_overflow
from .pipeline import CompactionOutcome, StageResult
from .presets import CompactionConfig, Recipe, build_recipe, load_compaction_config
from .primitives.accounting import OccupancyInput, normalize_usage
from .primitives.triggers import TriggerInput
from .pruning import prune_tool_results
from .snapcompact.inline import apply_inline_snapcompact
from .state import CompactionRecord, CompactionState, ProjectionTarget, strip_native_markers
from .summary_model import ConductorSummaryModel

logger = logging.getLogger(__name__)

# Window assumed when neither settings nor provider metadata name one.
_FALLBACK_CONTEXT_WINDOW = 128_000


class CompactionController:
    """Coordinates context compaction for BreadBoard conductors."""

    def __init__(
        self,
        config: Optional[Mapping[str, Any]] = None,
        *,
        summary_model: Optional[SummaryModelProtocol] = None,
    ) -> None:
        self.config: CompactionConfig = load_compaction_config((config or {}).get("compaction"))
        self.settings = self.config.settings
        self.recipe: Recipe = build_recipe(self.config)
        self.summary_model = summary_model
        self._turn_passes: Dict[int, int] = {}
        # Provider attempt number for the request being built; bumped after
        # each successful overflow recovery so retried requests are recorded
        # as attempt 1, 2, ... instead of overwriting attempt 0.
        self.request_attempt = 0
        self._messages_len_at_last_record: Optional[int] = None

    @property
    def active(self) -> bool:
        return self.recipe.active

    def begin_request(self) -> None:
        self.request_attempt = 0

    def passes_this_turn(self, turn_index: int) -> int:
        return self._turn_passes.get(turn_index, 0)

    def record_pass(self, turn_index: int) -> None:
        self._turn_passes[turn_index] = self.passes_this_turn(turn_index) + 1

    def can_pass(self, turn_index: int) -> bool:
        return self.passes_this_turn(turn_index) < self.recipe.max_attempts_per_turn

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

    def resolve_model_limits(
        self, session_state: Any, conductor: Optional[Any] = None, model: Optional[str] = None,
    ) -> tuple[Optional[int], Optional[int]]:
        """Resolve current route input/output limits without inventing an input cap."""
        get_metadata = getattr(session_state, "get_provider_metadata", None)
        config = getattr(conductor, "config", None) or {}
        entries = (config.get("providers") or {}).get("models") or []
        entry = next(
            (item for item in entries if isinstance(item, Mapping) and item.get("model_id") == model), {},
        )
        limits = []
        for key in ("max_input_tokens", "max_output_tokens"):
            value = get_metadata(key) if callable(get_metadata) else None
            if value is None and callable(get_metadata):
                value = get_metadata("model_" + key)
            if value is None:
                value = entry.get(key)
            limits.append(value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None)
        return limits[0], limits[1]


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
        max_input, max_output = self.resolve_model_limits(session_state, conductor, model)
        if self.should_trigger_threshold(
            session_state,
            context_window=context_window,
            last_usage=session_state.get_provider_metadata("usage"),
            max_input_tokens=max_input,
            max_output_tokens=max_output,
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
            conductor=conductor,
            client=client,
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
        conductor: Optional[Any] = None,
        client: Optional[Any] = None,
    ) -> List[Dict[str, Any]]:
        """Build request view with optional pruning, projection, and inline imaging."""
        messages = session_state.provider_messages
        compaction_state: Optional[CompactionState] = getattr(session_state, "compaction_state", None)
        if not self.active and (compaction_state is None or not compaction_state.records):
            # Disabled: the exact pre-compaction request view.
            return copy.deepcopy(messages)
        if compaction_state is None:
            compaction_state = CompactionState()
            session_state.compaction_state = compaction_state

        max_input, max_output = self.resolve_model_limits(session_state, conductor, target.model)
        steps = self.recipe.request_view if self.active else ()
        stage_ids = [step for step in steps if step in self.recipe.pipeline.stages]
        outcomes: List[CompactionOutcome] = []
        tokens_before = self.recipe.count(compaction_state.project(messages, target, coalesce=False))
        request_error = None
        index = 0
        try:
            while index < len(steps):
                step = steps[index]
                index += 1
                if step in self.recipe.pipeline.stages:
                    order = [step]
                    while index < len(steps) and steps[index] in self.recipe.pipeline.stages:
                        order.append(steps[index])
                        index += 1
                    self.compact(
                        session_state, reason="request", target=target, conductor=conductor,
                        runtime=runtime, client=client, context_window=context_window,
                        turn_index=turn_index, supports_images=supports_images, order=order,
                        _request_outcomes=outcomes,
                    )
                elif step == "omp_prune" and self.settings.prune.enabled:
                    context = CompactionContext(
                        messages=messages,
                        state=compaction_state,
                        settings=self.settings,
                        reason="threshold",
                        target=target,
                        context_window=context_window or self.settings.context_window or _FALLBACK_CONTEXT_WINDOW,
                        tokens_before=self.recipe.count(compaction_state.project(messages, target)),
                        max_input_tokens=max_input,
                        max_output_tokens=max_output,
                    )
                    prune_record = prune_tool_results(context)
                    if prune_record is not None:
                        compaction_state.append(prune_record, messages)
                        self._after_record_appended(session_state, prune_record, target, turn_index)
            view = compaction_state.project(messages, target)
            if not callable(getattr(runtime, "compaction_port", None)):
                view = strip_native_markers(view)
            if supports_images and steps and steps[-1] == "omp_inline_snapcompact":
                view = apply_inline_snapcompact(
                    view, self.settings, supports_images=supports_images,
                    provider=target.provider, api=target.api, model_id=target.model,
                )
        except Exception as exc:
            request_error = exc
            raise
        finally:
            if stage_ids:
                results = [stage for outcome in outcomes for stage in outcome.stages]
                results.extend(StageResult(stage_id, "noop", "request pass interrupted")
                               for stage_id in stage_ids[len(results):])
                records = tuple(record for outcome in outcomes for record in outcome.records)
                tokens_after = self.recipe.count(compaction_state.project(messages, target, coalesce=False))
                target_tokens = self.recipe.target_tokens("request", context_window or self.settings.context_window or _FALLBACK_CONTEXT_WINDOW, max_output)
                aggregate = CompactionOutcome(
                    records=records,
                    stages=tuple(results),
                    tokens_before=tokens_before,
                    tokens_after=tokens_after,
                    target_tokens=target_tokens,
                    reached_target=bool(records) and tokens_after <= target_tokens,
                )
                if request_error is not None:
                    request_error.compaction_outcome = aggregate
                self._record_finished(session_state, aggregate, turn_index, "request", request_error)
        return view

    def should_trigger_threshold(
        self,
        session_state: Any,
        *,
        context_window: Optional[int],
        last_usage: Optional[Any] = None,
        max_input_tokens: Optional[int] = None,
        max_output_tokens: Optional[int] = None,
    ) -> bool:
        """Whether any preset trigger fires for the current history and last usage."""
        if not self.active:
            return False

        messages = getattr(session_state, "provider_messages", None) or []
        if not messages:
            return False

        compaction_state: Optional[CompactionState] = getattr(session_state, "compaction_state", None)
        view = compaction_state.project(messages, None, coalesce=False) if compaction_state is not None else messages
        # Provider usage describes the last request sent. If a record was
        # appended since (no new message arrived), that request predates the
        # compaction and its usage would retrigger it.
        usage_fresh = len(messages) != self._messages_len_at_last_record
        metadata_input, metadata_output = self.resolve_model_limits(session_state)
        data = TriggerInput(
            OccupancyInput(view, normalize_usage(last_usage), usage_fresh), context_window,
            max_input_tokens=max_input_tokens if max_input_tokens is not None else metadata_input,
            max_output_tokens=max_output_tokens if max_output_tokens is not None else metadata_output,
        )
        pressure = self.recipe.pressure(data, messages)
        return pressure is not None and pressure.fires

    @staticmethod
    def _record_event(session_state: Any, event_type: str, payload: Dict[str, Any], turn_index: Optional[int]) -> None:
        recorder = getattr(session_state, "record_lifecycle_event", None)
        if not callable(recorder):
            return
        try:
            recorder(event_type, payload, turn=turn_index)
        except Exception:  # events are observability; a recorder failure never fails compaction
            logger.debug("lifecycle event %s not recorded", event_type, exc_info=True)

    def _after_record_appended(
        self,
        session_state: Any,
        record: CompactionRecord,
        target: Optional[ProjectionTarget] = None,
        turn_index: Optional[int] = None,
    ) -> None:
        """Record lifecycle event and persist Product snapshot."""
        self._messages_len_at_last_record = len(session_state.provider_messages)
        self._record_event(
            session_state,
            "compaction_record_appended",
            {
                "record_id": record.record_id,
                "method": record.method,
                "reason": record.reason,
                "tokens_before": record.tokens_before,
                "tokens_after": record.tokens_after,
                "first_kept_index": record.first_kept_index,
            },
            turn_index,
        )

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
        _request_outcomes: Optional[List[CompactionOutcome]] = None,
    ) -> CompactionOutcome:
        """Run the preset pipeline for ``reason``; record events and persist."""
        if reason not in self.recipe.targets:
            raise ValueError(f"compaction reason {reason!r} has no target; expected one of {sorted(self.recipe.targets)}")
        messages = session_state.provider_messages
        compaction_state: CompactionState = getattr(session_state, "compaction_state", None)
        if compaction_state is None:
            compaction_state = CompactionState()
            session_state.compaction_state = compaction_state

        resolved_target = target or ProjectionTarget("unknown", "unknown", "unknown")
        resolved_window = context_window or self.settings.context_window or _FALLBACK_CONTEXT_WINDOW
        max_input, max_output = self.resolve_model_limits(session_state, conductor, resolved_target.model)
        if remote_ports is None:
            remote_ports = self.remote_ports_for(runtime, client, resolved_target.model)

        projected = compaction_state.project(messages, resolved_target, coalesce=False)
        tokens_before = self.recipe.count(projected)

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
            max_input_tokens=max_input,
            max_output_tokens=max_output,
            **kwargs,
        )
        pressure = self.recipe.pressure(
            TriggerInput(OccupancyInput(projected, None, False), context_window, max_output, reason, max_input),
            messages,
        )
        context.severity = "hard" if reason in {"manual", "overflow"} else pressure.severity if pressure and pressure.severity else "soft"

        target_tokens = self.recipe.target_tokens(reason, resolved_window, max_output)
        self._record_event(
            session_state,
            "compaction_started",
            {
                "preset": self.recipe.preset_id,
                "reason": reason,
                "tokens_before": tokens_before,
                "target_tokens": target_tokens,
            },
            turn_index,
        )
        records_before = len(compaction_state.records)
        try:
            outcome = self.recipe.pipeline.run(
                context,
                target_tokens=target_tokens,
                order=order if order is not None else self.recipe.order,
            )
        except CompactionCancelled as exc:
            if _request_outcomes is not None:
                self._finish_outcome(session_state, exc.compaction_outcome, resolved_target, turn_index, reason, _request_outcomes)
            else:
                for record in compaction_state.records[records_before:]:
                    self._after_record_appended(session_state, record, resolved_target, turn_index)
                self._record_event(
                    session_state,
                    "compaction_finished",
                    {"preset": self.recipe.preset_id, "reason": reason, "status": "cancelled", "detail": str(exc) or None},
                    turn_index,
                )
            raise
        except Exception as exc:
            failed_outcome = getattr(exc, "compaction_outcome", None)
            if failed_outcome is not None:
                self._finish_outcome(session_state, failed_outcome, resolved_target, turn_index, reason, _request_outcomes)
            raise

        self._finish_outcome(session_state, outcome, resolved_target, turn_index, reason, _request_outcomes)
        return outcome

    def _finish_outcome(
        self, session_state: Any, outcome: CompactionOutcome,
        target: ProjectionTarget, turn_index: Optional[int], reason: CompactionReason,
        request_outcomes: Optional[List[CompactionOutcome]] = None,
    ) -> None:
        for record in outcome.records:
            self._after_record_appended(session_state, record, target, turn_index)
        if request_outcomes is not None:
            request_outcomes.append(outcome)
            return
        self._record_finished(session_state, outcome, turn_index, reason)

    def _record_finished(
        self, session_state: Any, outcome: CompactionOutcome, turn_index: Optional[int],
        reason: CompactionReason, error: Optional[Exception] = None,
    ) -> None:
        self._record_event(
            session_state, "compaction_finished",
            {
                "preset": self.recipe.preset_id,
                "reason": reason,
                "status": "cancelled" if isinstance(error, CompactionCancelled) else outcome.status,
                "tokens_before": outcome.tokens_before,
                "tokens_after": outcome.tokens_after,
                "target_tokens": outcome.target_tokens,
                "reached_target": outcome.reached_target,
                "stages": [stage.to_dict() for stage in outcome.stages],
                **({"detail": str(error) or None} if isinstance(error, CompactionCancelled) else {}),
            },
            turn_index,
        )

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
            tokens_before=self.recipe.count(state.project(messages, target)),
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
        overflow policy ``terminal``, pass budget spent, or no method made
        progress) means the caller re-raises ``exc`` unchanged.
        """
        if (
            not self.active
            or self.recipe.overflow_policy != "compact"
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
