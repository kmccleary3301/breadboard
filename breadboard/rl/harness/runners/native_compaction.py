"""Compaction of a native-worker history at the source's own checkpoints.

The source worker owns the compaction semantics: ``prepare_compaction``
evaluates the source's trigger and builds the summary request(s) from its
history, and ``finalize_compaction`` builds the compacted history from the
policy's summaries. The Conductor only routes the summary requests through the
policy and replaces the history. Only workers whose native stream profile sets
``implements_compaction_phases`` reach here.

Summary requests carry the episode's tools (deviation
``compaction_summary_episode_tools``): the RL policy endpoint rejects a request
whose tool schema differs from the episode's.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from breadboard.rl.harness.runners.base import FrozenJsonObject, freeze_json_object, thaw_json

WorkerPhase = Callable[[str, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]
# Sends one frozen summary request to the policy; returns (response, receipt).
SummaryExchange = Callable[[FrozenJsonObject], Awaitable[tuple[Mapping[str, Any], Any]]]
# Dependent summary requests a source issues after its first summaries.
MAX_FOLLOWUP_REQUESTS = 2


@dataclass(frozen=True, slots=True)
class CompactedHistory:
    messages: list[Any]
    event: dict[str, Any]
    # False when the source keeps the compaction but, judging the retry still
    # too large, does not retry the overflowed request (OMP's retry-fit check).
    retry: bool = True
    continuation: list[Any] | None = None
    reason: Literal["overflow", "threshold"] = "threshold"
def compaction_settings(source_profile: Mapping[str, Any]) -> Any:
    """Source-native compaction settings forwarded to ``prepare_compaction``."""
    agent = source_profile.get("agent")
    return source_profile.get("compaction_settings") or (
        agent.get("compaction_settings") if isinstance(agent, Mapping) else None
    )


def compaction_context_window(source_profile: Mapping[str, Any]) -> int | None:
    """The target's declared model context window.

    Native configs with a ``model`` section declare it there; flat configs
    (OMP 18.1.17) declare a top-level ``context_window``.
    """
    model = source_profile.get("model")
    window = (
        model.get("context_window") if isinstance(model, Mapping)
        else source_profile.get("context_window")
    )
    return window if type(window) is int and window > 0 else None


async def compact_history(
    messages: list[Any],
    *,
    reason: Literal["overflow", "threshold"],
    checkpoint: Literal["overflow", "before_request", "agent_end"],
    usage: Any,
    context_window: int | None,
    settings: Any,
    model_id: str,
    tools: Any,
    phase: WorkerPhase,
    exchange: SummaryExchange,
    trace_requests: list[dict[str, Any]],
) -> CompactedHistory | None:
    """Run native maintenance; retain reported rewrites even without a summary."""
    payload: dict[str, Any] = {
        "messages": thaw_json(messages),
        "reason": reason,
        "checkpoint": checkpoint,
        "usage": thaw_json(usage) if usage is not None else None,
        "context_window": context_window,
    }
    if settings:
        payload["settings"] = thaw_json(settings)
    prepared = await phase("prepare_compaction", payload)
    if prepared.get("kind") == "compaction_unavailable" and prepared.get("history_rewritten") is True:
        rewritten = prepared.get("messages")
        if not isinstance(rewritten, list):
            raise ValueError("native history rewrite requires a list of messages")
        # The source reports progress in its journal, not a successful
        # compaction or permission to retry a failed provider request.
        return CompactedHistory(
            messages=rewritten,
            event={
                "kind": "history_rewrite", "reason": reason, "checkpoint": checkpoint,
                "compaction_skipped": True,
            },
            retry=False, reason=reason,
        )
    if prepared.get("kind") != "compaction_prepared":
        return None

    # A source method that drops content without summarizing (OMP shake)
    # prepares no summary request.
    summary = None
    if prepared.get("summary_request") is not None:
        summary = await _summarize(
            prepared["summary_request"], "native summary request",
            model_id=model_id, tools=tools, exchange=exchange, trace_requests=trace_requests,
        )
    turn_prefix_summary = None
    if prepared.get("turn_prefix_request") is not None:
        turn_prefix_summary = await _summarize(
            prepared["turn_prefix_request"], "native turn prefix summary request",
            model_id=model_id, tools=tools, exchange=exchange, trace_requests=trace_requests,
        )

    finalize_payload: dict[str, Any] = {
        "summary": summary,
        "turn_prefix_summary": turn_prefix_summary,
        "preparation": prepared["preparation"],
    }
    finalized = await phase("finalize_compaction", finalize_payload)
    # A source request that depends on an earlier summary (OMP's short summary
    # takes the history summary as input) comes back from finalize; its answer
    # is appended to ``followup_summaries`` and finalize runs again.
    followups: list[str] = []
    while finalized.get("kind") == "compaction_followup_request":
        if len(followups) >= MAX_FOLLOWUP_REQUESTS:
            raise ValueError("native compaction requested too many follow-up summaries")
        followups.append(await _summarize(
            finalized["request"], "native follow-up summary request",
            model_id=model_id, tools=tools, exchange=exchange, trace_requests=trace_requests,
        ))
        finalized = await phase("finalize_compaction", {
            **finalize_payload, "followup_summaries": list(followups),
        })
    if finalized.get("kind") != "compaction_finalized":
        return None
    retry = finalized.get("retry", True)
    if type(retry) is not bool:
        raise ValueError("native compaction retry decision must be a boolean")
    finalized_reason = finalized.get("reason", reason)
    if finalized_reason not in {"overflow", "threshold"}:
        raise ValueError("native compaction reason must be overflow or threshold")
    event: dict[str, Any] = {
        "kind": "compaction",
        "reason": finalized_reason,
        "checkpoint": checkpoint,
        "summary": finalized.get("summary"),
        "tokens_before": prepared["preparation"].get("tokensBefore", 0),
    }
    if not retry:
        event["retry"] = False
    if "messages" in finalized:
        # Sources that rebuild the whole history (Hermes) return it in full.
        compacted = list(finalized["messages"])
    else:
        first_kept_index = finalized["first_kept_index"]
        compacted = [finalized["compaction_message"], *messages[first_kept_index:]]
        event["first_kept_index"] = first_kept_index
    continuation_raw = finalized.get("continuation")
    continuation: list[Any] | None = None
    if continuation_raw is not None:
        if not isinstance(continuation_raw, list) or not continuation_raw:
            raise WorkerPhaseError("compaction continuation must be a non-empty list of messages")
        continuation = list(continuation_raw)
    return CompactedHistory(
        messages=compacted, event=event, retry=retry,
        continuation=continuation, reason=finalized_reason,
    )


async def _summarize(
    request: Mapping[str, Any],
    field_name: str,
    *,
    model_id: str,
    tools: Any,
    exchange: SummaryExchange,
    trace_requests: list[dict[str, Any]],
) -> str:
    # The output cap stays the episode's per-request cap; the source's own
    # summary budget (``max_tokens``) is recorded on the trace only
    # (deviation ``compaction_summary_max_tokens``).
    req_dict: dict[str, Any] = {"model": model_id, "messages": request["messages"], "tools": tools}
    if "tool_choice" in request:
        req_dict["tool_choice"] = request["tool_choice"]
    frozen = freeze_json_object(
        req_dict,
        field_name=field_name,
    )
    response, receipt = await exchange(frozen)
    receipt_body = thaw_json(receipt)
    trace_body = dict(receipt_body) if isinstance(receipt_body, Mapping) else {"request": receipt}
    trace_body["_compaction_summary"] = True
    trace_requests.append(trace_body)
    return thaw_json(response["native_response"]).get("content") or ""
