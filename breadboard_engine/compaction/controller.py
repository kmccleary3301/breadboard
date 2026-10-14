"""Compaction controller: request projection, triggers, overflow recovery.

The agent config's ``compaction`` block selects a preset (``presets/``); the
preset's recipe supplies the triggers, per-reason targets, overflow budget,
request-view steps and the stage pipeline. The controller owns when those
run, per-turn pass counting, lifecycle events and snapshot persistence.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass
from typing import Any, Callable, ContextManager, Dict, List, Mapping, Optional, Sequence
from breadboard.product.runtime.artifacts import put_workspace_artifact
from ..provider.input_media import parse_provider_image_url
from ..execution.stock_driver import StockDriver
from ..provider.contract_runtime import ProviderRuntimeContext
from ..provider.history_projection import portable_provider_messages

from .methods import (
    CompactionCancelled,
    CompactionContext,
    CompactionReason,
    RemoteCompactionPort,
    SummaryModel as SummaryModelProtocol,
    _utc_now,
)
from .images import drop_images
from .overflow import is_context_overflow, overflow_token_bounds
from .pipeline import CompactionOutcome, StageResult
from .presets import CompactionConfig, Recipe, build_recipe, load_compaction_config
from .primitives.accounting import OccupancyInput, normalize_usage
from .primitives.triggers import TriggerInput
from .pruning import prune_tool_results
from .snapcompact.inline import apply_inline_snapcompact
from .state import NATIVE_MARKER_KEY, CompactionRecord, CompactionState, ProjectionTarget, strip_native_markers
from .summary_model import ConductorSummaryModel, routed_runtime
from .transcript import leading_system_count

logger = logging.getLogger(__name__)

# Window assumed when neither settings nor provider metadata name one.
_FALLBACK_CONTEXT_WINDOW = 128_000


@dataclass(frozen=True)
class CompactionRequestContext:
    """The sampling boundary of one request, never controller lifetime state."""

    sampling_phase: Optional[str] = None
    pending_user_index: Optional[int] = None


def _model_entry(conductor: Optional[Any], model: Optional[str]) -> Mapping[str, Any]:
    """The ``providers.models`` entry for the turn, keyed by API model or by its routed ID."""
    config = getattr(conductor, "config", None)
    providers = config.get("providers") if isinstance(config, Mapping) else None
    entries = providers.get("models") or [] if isinstance(providers, Mapping) else []
    # The conductor resolves a route such as ``openai/gpt-5.1-codex-mini`` to its API model before compaction runs.
    for wanted in (model or getattr(conductor, "model", None), getattr(conductor, "_current_route_id", None)):
        for entry in entries:
            if wanted and isinstance(entry, Mapping) and entry.get("id") == wanted:
                return entry
    return {}


@dataclass(frozen=True)
class _LeasedPort:
    """A native port whose client is leased from the broker for each compaction call."""

    probe: RemoteCompactionPort
    factory: Callable[..., RemoteCompactionPort]
    lease: Callable[[str, Any], ContextManager[Any]]
    route_id: str
    runtime: Any
    model: str

    @property
    def provider(self) -> str:
        return self.probe.provider

    @property
    def api(self) -> str:
        return self.probe.api

    def supports(self, model: str) -> bool:
        return self.probe.supports(model)

    def compact(self, context: CompactionContext) -> CompactionRecord:
        with self.lease(self.route_id, self.runtime) as client:
            return self.factory(client=client, model=self.model).compact(context)


class CompactionController:
    """Coordinates compaction; named summary tools require an explicit catalog port."""

    def __init__(
        self,
        config: Optional[Mapping[str, Any]] = None,
        *,
        summary_model: Optional[SummaryModelProtocol] = None,
        stock_driver: Optional[StockDriver] = None,
        tool_schema_provider: Optional[Callable[[str], Sequence[Mapping[str, Any]]]] = None,
    ) -> None:
        self.config: CompactionConfig = load_compaction_config((config or {}).get("compaction"))
        self.settings = self.config.settings
        self.recipe: Recipe = build_recipe(self.config)
        self.summary_model = summary_model
        # Provider attempt number for the request being built; bumped after
        # each successful overflow recovery so retried requests are recorded
        # as attempt 1, 2, ... instead of overwriting attempt 0.
        self.request_attempt = 0
        # (turn index, previous turn index) of attempt 0, which the retries reuse.
        self.request_turn: Optional[tuple[int, Any]] = None
        self.stock_driver = stock_driver
        self.tool_schema_provider = tool_schema_provider

    @property
    def active(self) -> bool:
        return self.recipe.active

    def begin_request(self) -> None:
        self.request_attempt = 0

    def begin_run(self) -> None:
        """Drop recovery state an earlier run armed; the conductor outlives its sessions."""
        for trigger in self.recipe.triggers:
            clear = getattr(trigger, "clear", None)
            if callable(clear):
                clear()

    @staticmethod
    def _ledger(session_state: Any) -> CompactionState:
        state = getattr(session_state, "compaction_state", None)
        if state is None:
            state = CompactionState()
            session_state.compaction_state = state
        return state

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

        entry = _model_entry(conductor, model)
        cw = entry.get("context_window") or entry.get("context_length")
        if isinstance(cw, int) and cw > 0:
            return cw

        return None

    def resolve_model_limits(
        self, session_state: Any, conductor: Optional[Any] = None, model: Optional[str] = None,
    ) -> tuple[Optional[int], Optional[int]]:
        """Resolve current route input/output limits without inventing an input cap."""
        get_metadata = getattr(session_state, "get_provider_metadata", None)
        profile = getattr(session_state, "_episode_provider_profile", None)
        entry = _model_entry(conductor, model)
        limits = []
        for key in ("max_input_tokens", "max_output_tokens"):
            value = getattr(profile, key, None)
            if value is None:
                value = get_metadata(key) if callable(get_metadata) else None
            if value is None and callable(get_metadata):
                value = get_metadata("model_" + key)
            if value is None:
                value = entry.get(key)
            limits.append(value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None)
        return limits[0], limits[1]

    def remote_ports_for(
        self,
        runtime: Optional[Any],
        client: Optional[Any],
        model: str,
        conductor: Optional[Any] = None,
    ) -> List[RemoteCompactionPort]:
        factory = getattr(runtime, "compaction_port", None)
        # Production clients are scoped to a broker lease per provider call.
        lease = getattr(conductor, "_provider_client_lease", None) if client is None else None
        if not callable(factory) or (client is None and lease is None):
            return []
        try:
            port = factory(client=client, model=model)
        except Exception as exc:  # capability probe; remote method then reports unavailable
            logger.debug("compaction_port unavailable: %s", exc)
            return []
        if port is None:
            return []
        if lease is None:
            return [port]
        route_id = getattr(conductor, "_current_route_id", None) or model
        return [_LeasedPort(port, factory, lease, route_id, runtime, model)]

    def prepare_request(
        self,
        session_state: Any,
        *,
        conductor: Optional[Any],
        runtime: Optional[Any],
        client: Optional[Any],
        model: str,
        turn_index: Optional[int],
        request_context: Optional[CompactionRequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """Threshold-compact if due, then run the ordered request-view pass."""
        target = projection_target_for(runtime, model)
        context_window = self.resolve_context_window(
            conductor=conductor, session_state=session_state, model=model
        )
        max_input, max_output = self.resolve_model_limits(session_state, conductor, model)
        entry = _model_entry(conductor, model)
        supports_images = entry.get("supports_images") is True or (
            isinstance(entry.get("input"), (list, tuple, set)) and "image" in entry.get("input")
        )
        if self.should_trigger_threshold(
            session_state,
            context_window=context_window,
            last_usage=session_state.get_provider_metadata("usage"),
            max_input_tokens=max_input,
            max_output_tokens=max_output,
            checkpoint="every_request",
            target=target,
        ):
            self.compact(
                session_state, request_context=request_context, reason="threshold", target=target,
                conductor=conductor, runtime=runtime, client=client,
                context_window=context_window, turn_index=turn_index,
                max_output_tokens=max_output, supports_images=supports_images,
            )
        self._serve_native_summary_requests(
            session_state, request_context=request_context, conductor=conductor,
            runtime=runtime, client=client, model=model, turn_index=turn_index,
        )
        return self.build_request_view(
            session_state, request_context=request_context, target=target, runtime=runtime,
            turn_index=turn_index,
            context_window=context_window, conductor=conductor, client=client,
            supports_images=supports_images,
        )

    def _serve_native_summary_requests(
        self, session_state: Any, *, conductor: Any, runtime: Any, client: Any,
        model: str, turn_index: Optional[int],
        request_context: Optional[CompactionRequestContext] = None,
    ) -> bool:
        """Commit genuine BB boundaries for each summary actually queued by stock."""
        driver = self.stock_driver
        if driver is None or driver.pending_compaction is None:
            return False
        served = False
        while (pending := driver.pending_compaction(session_state)) is not None:
            _, max_output = self.resolve_model_limits(session_state, conductor, model)
            outcome = self.compact(
                session_state, request_context=request_context, reason="threshold",
                target=projection_target_for(runtime, model),
                conductor=conductor, runtime=runtime, client=client,
                context_window=self.resolve_context_window(
                    conductor=conductor, session_state=session_state, model=model),
                turn_index=turn_index,
                max_output_tokens=max_output,
            )
            if not outcome.compacted or driver.pending_compaction(session_state) is pending:
                raise driver.compaction_error("Genuine BB compaction did not consume the queued stock summary")
            served = True
        return served

    def finish_assistant_step(
        self, session_state: Any, *, conductor: Any, runtime: Any, client: Any,
        model: str, turn_index: Optional[int],
        request_context: Optional[CompactionRequestContext] = None,
    ) -> bool:
        """Run a declared post-response checkpoint, including terminal answers."""
        source_compacted = self._serve_native_summary_requests(
            session_state, request_context=request_context, conductor=conductor,
            runtime=runtime, client=client, model=model, turn_index=turn_index,
        )
        if not any(getattr(trigger, "phase", None) == "assistant_step_end" for trigger in self.recipe.triggers):
            return source_compacted
        window = self.resolve_context_window(conductor=conductor, session_state=session_state, model=model)
        target = projection_target_for(runtime, model)
        max_input, max_output = self.resolve_model_limits(session_state, conductor, model)
        if not self.should_trigger_threshold(
            session_state, context_window=window,
            last_usage=session_state.get_provider_metadata("usage"),
            max_input_tokens=max_input, max_output_tokens=max_output,
            checkpoint="assistant_step_end", target=target,
        ):
            return source_compacted
        pending = None
        for trigger in self.recipe.triggers:
            consume = getattr(trigger, "consume", None)
            if callable(consume):
                parsed = consume()
                if parsed is not None:
                    pending = parsed
        outcome = self.compact(
            session_state, request_context=request_context, reason="threshold",
            target=target,
            conductor=conductor, runtime=runtime, client=client,
            context_window=window, turn_index=turn_index,
            max_output_tokens=max_output,
            overflow_tokens=pending.get("overflow_tokens") if pending is not None else None,
            overflow_limit=pending.get("overflow_limit") if pending is not None else None,
        )
        if pending is not None and any(record.details.get("recovered") for record in outcome.records):
            for trigger in self.recipe.triggers:
                clear = getattr(trigger, "clear", None)
                if callable(clear):
                    clear()
        additional_compacted = self._serve_native_summary_requests(
            session_state, request_context=request_context, conductor=conductor,
            runtime=runtime, client=client, model=model, turn_index=turn_index,
        )
        return outcome.compacted or source_compacted or additional_compacted

    def finish_user_turn(
        self, session_state: Any, *, conductor: Any, runtime: Any, client: Any,
        model: str, turn_index: Optional[int],
        request_context: Optional[CompactionRequestContext] = None,
    ) -> bool:
        """Run turn-end maintenance and drain source summaries before terminalizing."""
        stages = [
            stage.id for stage in self.recipe.pipeline.stages.values()
            if stage.phase == "user_turn_end"
        ] if self.active else []
        if stages:
            self.compact(
                session_state, request_context=request_context, reason="request",
                target=projection_target_for(runtime, model),
                conductor=conductor, runtime=runtime, client=client,
                context_window=self.resolve_context_window(conductor=conductor, session_state=session_state, model=model),
                turn_index=turn_index, order=stages,
            )
        return self._serve_native_summary_requests(
            session_state, conductor=conductor, runtime=runtime, client=client,
            model=model, turn_index=turn_index, request_context=request_context,
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
        request_context: Optional[CompactionRequestContext] = None,
    ) -> List[Dict[str, Any]]:
        """Build request view with optional pruning, projection, and inline imaging."""
        messages = session_state.provider_messages
        compaction_state: Optional[CompactionState] = getattr(session_state, "compaction_state", None)
        if not self.active and (compaction_state is None or not compaction_state.records):
            # Disabled: the exact pre-compaction request view.
            return copy.deepcopy(messages)
        compaction_state = self._ledger(session_state)
    
        max_input, max_output = self.resolve_model_limits(session_state, conductor, target.model)
        steps = self.recipe.request_view if self.active else ()
        stage_ids = [step for step in steps if step in self.recipe.pipeline.stages]
        outcomes: List[CompactionOutcome] = []
        tokens_before = self.recipe.count(compaction_state.project(messages, target))
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
                        session_state, request_context=request_context, reason="request",
                        target=target, conductor=conductor, runtime=runtime, client=client,
                        context_window=context_window, turn_index=turn_index,
                        supports_images=supports_images, order=order, _request_outcomes=outcomes,
                    )
                elif step == "omp_prune" and self.settings.prune.enabled:
                    context = self._context_for_pass(
                        session_state, request_context=request_context, reason="threshold",
                        target=target, conductor=conductor, runtime=runtime, client=client,
                        context_window=context_window, turn_index=turn_index,
                        supports_images=supports_images,
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
            view = self._materialize_snapcompact_frames(session_state, view)
        except Exception as exc:
            request_error = exc
            raise
        finally:
            if stage_ids:
                results = [stage for outcome in outcomes for stage in outcome.stages]
                results.extend(StageResult(stage_id, "noop", "request pass interrupted")
                               for stage_id in stage_ids[len(results):])
                records = tuple(record for outcome in outcomes for record in outcome.records)
                tokens_after = self.recipe.count(compaction_state.project(messages, target))
                target_tokens = self.recipe.target_tokens("request", context_window or self.settings.context_window or _FALLBACK_CONTEXT_WINDOW, max_output, max_input)
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

    def _materialize_snapcompact_frames(
        self, session_state: Any, view: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        has_snapcompact_frame = any(
            NATIVE_MARKER_KEY not in message
            and isinstance(message.get("content"), list)
            and any(
                isinstance(block, Mapping)
                and block.get("type") == "image_url"
                and isinstance(block.get("image_url"), Mapping)
                and isinstance(block["image_url"].get("url"), str)
                and block["image_url"]["url"].startswith("data:image/png;base64,")
                for block in message["content"]
            )
            for message in view
        )
        if not has_snapcompact_frame:
            return view

        capabilities = dict(session_state.get_provider_metadata("attachment_capabilities") or {})
        materialized_view: List[Dict[str, Any]] = []
        for message in view:
            if NATIVE_MARKER_KEY in message:
                materialized_view.append(message)
                continue
            content = message.get("content")
            if not isinstance(content, list):
                materialized_view.append(message)
                continue
            new_blocks: List[Dict[str, Any]] = []
            for block in content:
                if (
                    not isinstance(block, Mapping)
                    or block.get("type") != "image_url"
                    or not isinstance(block.get("image_url"), Mapping)
                    or not isinstance(block["image_url"].get("url"), str)
                    or not block["image_url"]["url"].startswith("data:image/png;base64,")
                ):
                    new_blocks.append(block)
                    continue
                url = block["image_url"]["url"]
                resolved = parse_provider_image_url(url)
                ref = put_workspace_artifact(
                    session_state.workspace, resolved.content, media_type="image/png"
                )
                uri = f"attachment://{ref.digest}"
                capabilities[uri] = ref.as_dict()
                new_blocks.append(
                    {"type": "media", "kind": "image", "uri": uri, "mime": "image/png"}
                )
            materialized_message = dict(message)
            materialized_message["content"] = new_blocks
            materialized_view.append(materialized_message)
        session_state.set_provider_metadata("attachment_capabilities", capabilities)
        return materialized_view

    def should_trigger_threshold(
        self,
        session_state: Any,
        *,
        context_window: Optional[int],
        last_usage: Optional[Any] = None,
        max_input_tokens: Optional[int] = None,
        max_output_tokens: Optional[int] = None,
        checkpoint: Optional[str] = None,
        target: Optional[ProjectionTarget] = None,
    ) -> bool:
        """Whether any preset trigger fires for the current history and last usage."""
        if not self.active:
            return False

        messages = getattr(session_state, "provider_messages", None) or []
        if not messages:
            return False

        compaction_state: Optional[CompactionState] = getattr(session_state, "compaction_state", None)
        # Count the view the provider receives, including a native boundary usable only for this target.
        view = compaction_state.project(messages, target) if compaction_state is not None else messages
        # Provider usage describes the last request sent. A committed compaction
        # record makes that usage stale until a request sends the new projection.
        usage_fresh = compaction_state is None or compaction_state.provider_usage_fresh
        metadata_input, metadata_output = self.resolve_model_limits(session_state)
        summary_last = False
        if checkpoint == "assistant_step_end" and any(
            getattr(trigger, "kind", None) == "pending_error" for trigger in self.recipe.triggers
        ) and compaction_state is not None:
            for record in reversed(compaction_state.records):
                if record.is_boundary and record.details.get("summary"):
                    summary_last = not any(
                        message.get("role") == "assistant"
                        for message in messages[record.history_length:]
                    )
                    break
        data = TriggerInput(
            OccupancyInput(view, normalize_usage(last_usage), usage_fresh), context_window,
            max_input_tokens=max_input_tokens if max_input_tokens is not None else metadata_input,
            max_output_tokens=max_output_tokens if max_output_tokens is not None else metadata_output,
            checkpoint=checkpoint,
            last_assistant_is_summary=summary_last,
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
        if record.is_boundary and target is not None and target.api == "responses":
            # The boundary replaces server-side history; the next request starts a new chain.
            session_state.set_provider_metadata("conversation_id", None)
            session_state.set_provider_metadata("previous_response_id", None)
        driver = self.stock_driver
        if driver is not None and driver.synchronize_record is not None:
            driver.synchronize_record(session_state, record)
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
                    # Persist a route-independent view: a native boundary is valid only for the route that made it.
                    projected = compaction_state.project(session_state.provider_messages)
                    session_state.persist_compaction_snapshot(projected)
            except Exception as exc:
                logger.warning("Failed to persist compaction snapshot: %s", exc)

    def _context_for_pass(
        self,
        session_state: Any,
        *,
        reason: CompactionReason,
        target: ProjectionTarget,
        conductor: Optional[Any] = None,
        runtime: Optional[Any] = None,
        client: Optional[Any] = None,
        context_window: Optional[int] = None,
        turn_index: Optional[int] = None,
        custom_instructions: Optional[str] = None,
        remote_ports: Optional[Sequence[RemoteCompactionPort]] = None,
        supports_images: bool = False,
        clock: Optional[Callable[[], str]] = None,
        max_output_tokens: Optional[int] = None,
        overflow_tokens: Optional[int] = None,
        overflow_limit: Optional[int] = None,
        request_context: Optional[CompactionRequestContext] = None,
    ) -> CompactionContext:
        """Populate the production dependencies for every ledger-producing pass."""
        messages = session_state.provider_messages
        state = self._ledger(session_state)
        max_input, max_output = self.resolve_model_limits(session_state, conductor, target.model)
        if max_output_tokens is not None:
            max_output = max_output_tokens
        if remote_ports is None:
            remote_ports = self.remote_ports_for(runtime, client, target.model, conductor)
        summarizer = self.summary_model
        if summarizer is None and callable(getattr(runtime, "invoke", None)):
            lease = getattr(conductor, "_provider_client_lease", None)
            summarizer = ConductorSummaryModel(
                runtime=runtime, client=client, model=target.model,
                session_state=session_state, agent_config=getattr(conductor, "config", None) or {},
                turn_index=turn_index, recorder=getattr(conductor, "structured_request_recorder", None),
                client_lease=lease,
                route_id=getattr(conductor, "_current_route_id", None) or target.model,
                tool_schema_provider=self.tool_schema_provider,
                # Leased production clients come from the router, so a routed summaryModel gets its own runtime.
                route_resolver=routed_runtime if lease is not None else None,
            )
        projected = state.project(messages, target)
        window = context_window or self.settings.context_window or _FALLBACK_CONTEXT_WINDOW
        pressure = self.recipe.pressure(
            TriggerInput(OccupancyInput(projected, None, False), context_window,
                         max_output_tokens=max_output, reason=reason, max_input_tokens=max_input),
            messages,
        )
        return CompactionContext(
            messages=messages, state=state, settings=self.settings, reason=reason, target=target,
            context_window=window, tokens_before=self.recipe.count(projected),
            summarizer=summarizer, remote_ports=remote_ports, artifacts=None,
            supports_images=supports_images,
            custom_instructions=custom_instructions or self.settings.custom_instructions,
            max_input_tokens=max_input, max_output_tokens=max_output,
            overflow_tokens=overflow_tokens, overflow_limit=overflow_limit,
            last_usage=session_state.get_provider_metadata("usage") if callable(getattr(session_state, "get_provider_metadata", None)) else None,
            usage_fresh=state.provider_usage_fresh,
            native_retention=self.recipe.native_retention,
            prior_compactions=sum(record.is_boundary for record in state.records),
            sampling_phase=request_context.sampling_phase if request_context is not None else None,
            pending_user_index=request_context.pending_user_index if request_context is not None else None,
            severity="hard" if reason in {"manual", "overflow"} else pressure.severity if pressure and pressure.severity else "soft",
            clock=clock or _utc_now,
            runtime_context=ProviderRuntimeContext(session_state, dict(getattr(conductor, "config", None) or {})),
        )

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
        max_output_tokens: Optional[int] = None,
        _request_outcomes: Optional[List[CompactionOutcome]] = None,
        overflow_tokens: Optional[int] = None,
        overflow_limit: Optional[int] = None,
        request_context: Optional[CompactionRequestContext] = None,
    ) -> CompactionOutcome:
        """Run the preset pipeline for ``reason``; record events and persist."""
        target_reason = "threshold" if reason == "request" else reason
        if target_reason not in self.recipe.targets:
            raise ValueError(f"compaction reason {reason!r} has no target; expected one of {sorted(self.recipe.targets)}")
    
        resolved_target = target or ProjectionTarget("unknown", "unknown", "unknown")
        context = self._context_for_pass(
            session_state, request_context=request_context, reason=reason,
            target=resolved_target, conductor=conductor, runtime=runtime, client=client,
            context_window=context_window, turn_index=turn_index,
            custom_instructions=custom_instructions, remote_ports=remote_ports,
            supports_images=supports_images, clock=clock, max_output_tokens=max_output_tokens,
            overflow_tokens=overflow_tokens, overflow_limit=overflow_limit,
        )
        compaction_state = context.state
        tokens_before = context.tokens_before
        target_tokens = self.recipe.target_tokens(
            reason, context.context_window, context.max_output_tokens, context.max_input_tokens,
        )
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
        return self.compact(session_state, reason="manual", custom_instructions=instructions, **kwargs)

    def drop_images_now(
        self,
        session_state: Any,
        *,
        runtime: Optional[Any] = None,
        model: str = "unknown",
        turn_index: Optional[int] = None,
        request_context: Optional[CompactionRequestContext] = None,
    ) -> Optional[CompactionRecord]:
        """Manual OMP ``dropImages``: replace every image with a placeholder.
    
        Appends an edit record (history itself is untouched) and returns it,
        or returns None when the history carries no images.
        """
        messages = session_state.provider_messages
        state: CompactionState = session_state.compaction_state
        target = projection_target_for(runtime, model)
        context = self._context_for_pass(
            session_state, request_context=request_context, reason="manual",
            target=target, runtime=runtime, turn_index=turn_index,
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
        request_context: Optional[CompactionRequestContext] = None,
    ) -> bool:
        """Recover through native overflow or a declared pending-error checkpoint.
    
        True means a record was appended and the caller should retry the
        request once more. False (no eligible recovery signal, compaction
        inactive, terminal policy, spent budget, or no progress) means the
        caller re-raises ``exc`` unchanged.
        """
        driver = self.stock_driver
        source_outcome = (
            driver.recovery_outcome(session_state)
            if driver is not None and driver.recovery_outcome is not None else None
        )
        if source_outcome in {"handled", "error"}:
            for trigger in self.recipe.triggers:
                if getattr(trigger, "kind", None) == "pending_error":
                    trigger.clear()
            # The failed user remains an error outcome. A native handled
            # successor is a distinct stock-origin turn, not a retry.
            return False
        if self.active:
            for trigger in self.recipe.triggers:
                arm = getattr(trigger, "arm", None)
                if callable(arm):
                    arm(exc)
        if not self.active or self.recipe.overflow_policy != "compact":
            return False
        classifier = self.recipe.error_classifier or is_context_overflow
        if not classifier(exc):
            # A source-specific error parser can arm recovery independently
            # of native overflow. Run its existing assistant-end checkpoint
            # before the caller terminalizes this failed provider step.
            if not any(getattr(trigger, "pending", None) is not None
                       for trigger in self.recipe.triggers):
                return False
            recovered = self.finish_assistant_step(
                session_state, request_context=request_context, conductor=conductor,
                runtime=runtime, client=client, model=model, turn_index=turn_index,
            )
            if recovered:
                self.request_attempt += 1
            return recovered
        passes = self._ledger(session_state).turn_passes
        if passes.get(turn_index, 0) >= self.recipe.max_attempts_per_turn:
            return False
        passes[turn_index] = passes.get(turn_index, 0) + 1
        overflow_tokens, overflow_limit = overflow_token_bounds(exc)
        outcome = self.compact(
            session_state, request_context=request_context, reason="overflow",
            target=projection_target_for(runtime, model),
            conductor=conductor, runtime=runtime, client=client,
            context_window=self.resolve_context_window(
                conductor=conductor, session_state=session_state, model=model,
            ),
            turn_index=turn_index,
            overflow_tokens=overflow_tokens, overflow_limit=overflow_limit,
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


def reproject_for_route(
    messages: List[Dict[str, Any]],
    session_state: Any,
    *,
    source_runtime: Any,
    source_model: str,
    runtime: Any,
    model: str,
) -> List[Dict[str, Any]]:
    """Select the destination compaction view, then make foreign replay portable."""
    source = projection_target_for(source_runtime, source_model)
    target = projection_target_for(runtime, model)
    rebuilt = messages
    marker = next((index for index, message in enumerate(messages) if NATIVE_MARKER_KEY in message), None)
    if marker is not None:
        state: CompactionState = session_state.compaction_state
        record_id = messages[marker][NATIVE_MARKER_KEY]["record_id"]
        native = next(record for record in state.records if record.record_id == record_id)
        if not (
            source == target
            and native.native is not None
            and native.native.usable_for(target)
        ):
            # Rebuild from append-only history so a fallback can recover facts
            # covered by a native boundary that only the source route reads.
            history = session_state.provider_messages
            start = int(native.first_kept_index)
            keep = max(start, state.kept_start(history, target))
            view = state.project(history, target)
            systems = leading_system_count(view)
            head = messages[:leading_system_count(messages)] or view[:systems]
            rebuilt = head + view[systems:len(view) - (len(history) - keep)] + messages[
                marker + 1 + keep - start:
            ]
            if not callable(getattr(runtime, "compaction_port", None)):
                rebuilt = strip_native_markers(rebuilt)
    if source == target:
        return rebuilt
    return portable_provider_messages(
        rebuilt,
        source_provider=source.provider,
        source_api=source.api,
        target_provider=target.provider,
        target_api=target.api,
        session_state=session_state,
    )
