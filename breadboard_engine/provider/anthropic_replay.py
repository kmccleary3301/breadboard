"""Lossless native Messages tool continuation, bound to canonical call results."""
from __future__ import annotations

from typing import Any

from .contract_wire import ProviderContractError

TOOL_RESULT_SCHEMA = "anthropic.messages.tool_result.v1"


def is_tool_result_replay(block: dict) -> bool:
    return (block.get("type") == "provider_replay" and block.get("provider_id") == "anthropic"
            and block.get("schema_version") == TOOL_RESULT_SCHEMA
            and block.get("replay_scope") == "same_provider")


def replay_messages(blocks: list[dict]) -> list[dict]:
    from .contract_messages import canonical_json

    replays = [block for block in blocks if is_tool_result_replay(block)]
    results = [block for block in blocks if block.get("type") == "tool_result"]
    if len(replays) != 1 or not results or len(replays) + len(results) != len(blocks):
        raise ProviderContractError("native tool continuation requires one replay and correlated results")
    payload = replays[0]["payload"]
    if set(payload) != {"messages"} or not isinstance(payload["messages"], list) or not payload["messages"]:
        raise ProviderContractError("native tool continuation requires nonempty messages")
    native_results = []
    for message in payload["messages"]:
        if not isinstance(message, dict) or set(message) != {"role", "content"} or message["role"] != "user":
            raise ProviderContractError("native tool continuation requires user messages")
        if not isinstance(message["content"], list):
            raise ProviderContractError("native tool continuation requires block content")
        for block in message["content"]:
            if not isinstance(block, dict) or not isinstance(block.get("type"), str):
                raise ProviderContractError("invalid native tool continuation block")
            if block["type"] == "tool_result":
                native_results.append(block)
    if len(native_results) != len(results):
        raise ProviderContractError("native tool continuation result count mismatch")
    for native, semantic in zip(native_results, results):
        content: Any = native.get("content")
        if not isinstance(content, str):
            content = canonical_json(content)
        if (native.get("tool_use_id") != semantic["call_id"] or content != semantic["content"]
                or native.get("is_error", False) != semantic.get("is_error", False)):
            raise ProviderContractError("native tool continuation disagrees with canonical result")
    canonical_json(payload["messages"])
    return payload["messages"]
