"""Lossless native Messages tool continuation, bound to canonical call results."""
from __future__ import annotations

from typing import Any

from .contract_wire import ProviderContractError, canonical_json

TOOL_RESULT_SCHEMA = "anthropic.messages.tool_result.v1"


def is_tool_result_replay(block: dict) -> bool:
    return (block.get("type") == "provider_replay" and block.get("provider_id") == "anthropic"
            and block.get("schema_version") == TOOL_RESULT_SCHEMA
            and block.get("replay_scope") == "same_provider")


def replay_messages(blocks: list[dict]) -> list[dict]:
    replays = [block for block in blocks if is_tool_result_replay(block)]
    results = [block for block in blocks if block.get("type") == "tool_result"]
    if len(replays) != 1 or not results or len(replays) + len(results) != len(blocks):
        raise ProviderContractError("native tool continuation requires one replay and correlated results")
    payload = replays[0].get("payload")
    if (not isinstance(payload, dict) or set(payload) != {"messages"}
            or not isinstance(payload["messages"], list) or not payload["messages"]):
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


def native_tool_results(blocks: list[dict]) -> list[dict]:
    """A validated continuation's native ``tool_result`` blocks, in global ordinal order."""
    return [block for message in replay_messages(blocks) for block in message["content"]
            if block["type"] == "tool_result"]


def replace_tool_result(blocks: list[dict], ordinal: int, content: Any) -> list[dict]:
    """Return a validated continuation with native result ``ordinal`` set to native ``content``.

    The ordinal counts results across every native message (IDs may repeat); the canonical
    peer at the same ordinal is resynchronized. ``blocks`` is never mutated.
    """
    if not 0 <= ordinal < len(native_tool_results(blocks)):
        raise ProviderContractError("native tool continuation result ordinal out of range")
    canonical = content if isinstance(content, str) else canonical_json(content)
    replaced: list[dict] = []
    canonical_ordinal = 0
    for block in blocks:
        if block["type"] == "tool_result":
            if canonical_ordinal == ordinal:
                block = {**block, "content": canonical}
            canonical_ordinal += 1
        else:
            native_ordinal = 0
            messages = []
            for message in block["payload"]["messages"]:
                message_content = []
                for part in message["content"]:
                    if part["type"] == "tool_result":
                        if native_ordinal == ordinal:
                            part = {**part, "content": content}
                        native_ordinal += 1
                    message_content.append(part)
                messages.append({**message, "content": message_content})
            block = {**block, "payload": {**block["payload"], "messages": messages}}
        replaced.append(block)
    replay_messages(replaced)
    return replaced


def portable_tool_continuation(blocks: list[dict]) -> list[dict[str, Any]] | None:
    """Expand a validated native continuation into results, then user observations."""
    if not any(is_tool_result_replay(block) for block in blocks):
        return None

    results = []
    observations = []
    for message in replay_messages(blocks):
        user_content = []
        for block in message["content"]:
            if block["type"] == "tool_result":
                results.append({
                    "role": "tool",
                    "content": [{
                        "type": "tool_result",
                        "call_id": block["tool_use_id"],
                        "content": block.get("content"),
                        "is_error": block.get("is_error", False),
                    }],
                })
            elif block["type"] == "text":
                text = block.get("text")
                if not isinstance(text, str):
                    raise ProviderContractError(
                        "native continuation observation text must be text"
                    )
                user_content.append({"type": "text", "text": text})
            elif block["type"] == "image":
                user_content.append(dict(block))
            else:
                raise ProviderContractError(
                    "unsupported native continuation observation block"
                )
        if user_content:
            observations.append({"role": "user", "content": user_content})
    return results + observations
