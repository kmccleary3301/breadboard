"""Compaction of a native-worker history after a policy context overflow.

The source worker owns the compaction semantics: ``prepare_compaction`` builds
the summary request(s) from its history, and ``finalize_compaction`` builds the
compaction message from the policy's summaries. The Conductor only routes the
summary requests through the policy and replaces the history. Only workers
whose native stream profile sets ``implements_compaction_phases`` reach here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from breadboard.rl.harness.runners.base import FrozenJsonObject, freeze_json_object, thaw_json

WorkerPhase = Callable[[str, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]
# Sends one frozen summary request to the policy; returns (response, receipt).
SummaryExchange = Callable[[FrozenJsonObject], Awaitable[tuple[Mapping[str, Any], Any]]]


@dataclass(frozen=True, slots=True)
class CompactedHistory:
    messages: list[Any]
    event: dict[str, Any]


def compaction_settings(source_profile: Mapping[str, Any]) -> Any:
    """Source-native compaction settings forwarded to ``prepare_compaction``."""
    agent = source_profile.get("agent")
    return source_profile.get("compaction_settings") or (
        agent.get("compaction_settings") if isinstance(agent, Mapping) else None
    )


async def compact_history(
    messages: list[Any],
    *,
    settings: Any,
    model_id: str,
    phase: WorkerPhase,
    exchange: SummaryExchange,
    trace_requests: list[dict[str, Any]],
) -> CompactedHistory | None:
    """Run the worker's compaction phases; None when the worker declines."""
    payload: dict[str, Any] = {"messages": messages}
    if settings:
        payload["settings"] = settings
    prepared = await phase("prepare_compaction", payload)
    if prepared.get("kind") != "compaction_prepared":
        return None

    summary = await _summarize(
        prepared["summary_request"], "native summary request",
        model_id=model_id, exchange=exchange, trace_requests=trace_requests,
    )
    turn_prefix_summary = None
    if prepared.get("turn_prefix_request") is not None:
        turn_prefix_summary = await _summarize(
            prepared["turn_prefix_request"], "native turn prefix summary request",
            model_id=model_id, exchange=exchange, trace_requests=trace_requests,
        )

    finalized = await phase("finalize_compaction", {
        "summary": summary,
        "turn_prefix_summary": turn_prefix_summary,
        "preparation": prepared["preparation"],
    })
    if finalized.get("kind") != "compaction_finalized":
        return None
    first_kept_index = finalized["first_kept_index"]
    return CompactedHistory(
        messages=[finalized["compaction_message"], *messages[first_kept_index:]],
        event={
            "kind": "compaction",
            "summary": finalized["summary"],
            "first_kept_index": first_kept_index,
            "tokens_before": prepared["preparation"].get("tokensBefore", 0),
        },
    )


async def _summarize(
    request: Mapping[str, Any],
    field_name: str,
    *,
    model_id: str,
    exchange: SummaryExchange,
    trace_requests: list[dict[str, Any]],
) -> str:
    frozen = freeze_json_object(
        {"model": model_id, "messages": request["messages"], "tools": []},
        field_name=field_name,
    )
    response, receipt = await exchange(frozen)
    receipt_body = thaw_json(receipt)
    trace_body = dict(receipt_body) if isinstance(receipt_body, Mapping) else {"request": receipt}
    trace_body["_compaction_summary"] = True
    trace_requests.append(trace_body)
    return thaw_json(response["native_response"]).get("content") or ""
