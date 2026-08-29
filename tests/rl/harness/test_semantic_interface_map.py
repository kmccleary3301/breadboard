from __future__ import annotations

from dataclasses import dataclass
import json
import importlib
from typing import Any, Mapping

from breadboard_engine.provider.contracts import (
    ProviderCorrelation,
    ProviderDone,
    ProviderEvent,
    ProviderExchangeV2,
    ProviderIdentity,
    ProviderMessage,
    ProviderRequest,
    ProviderToolCall,
    canonical_json,
    encode_provider_exchange,
)
from breadboard.rl.harness.evidence import canonical_digest
from breadboard.rl.harness.semantic_interface_map import (
    SEMANTIC_INTERFACE_MAP,
    SEMANTIC_INTERFACE_MAP_SCHEMA_VERSION,
)


@dataclass(frozen=True, slots=True)
class _ReplayProof:
    semantic_history: tuple[dict[str, Any], ...]
    event_log: bytes
    exchange: dict[str, Any]
    workspace_digest: str


def _workspace_digest(workspace: Mapping[str, str]) -> str:
    return canonical_digest(
        {
            "schema_version": "bb.rl.workspace-snapshot.v1",
            "files": dict(workspace),
        }
    )


def _append_message(
    history: tuple[dict[str, Any], ...], message: ProviderMessage
) -> tuple[dict[str, Any], ...]:
    """Test-only append operation models the controller's immutable history view."""
    return (*history, message.as_dict())


def _fake_policy_replay(workspace: Mapping[str, str]) -> _ReplayProof:
    workspace_digest = _workspace_digest(workspace)
    valid_call = ProviderToolCall(
        id="call-read-1",
        name="read_file",
        arguments={"path": "README.md"},
    )
    valid_message = ProviderMessage(
        role="assistant", content=None, tool_calls=[valid_call]
    )
    tool_message = ProviderMessage(
        role="tool",
        content=None,
        tool_results=[
            {
                "call_id": "call-read-1",
                "result": {
                    "path": "README.md",
                    "content": workspace.get("README.md", ""),
                    "workspace_digest": workspace_digest,
                },
            }
        ],
    )
    invalid_message = ProviderMessage(
        role="assistant",
        content=None,
        tool_calls=[
            ProviderToolCall(
                id="call-unknown-1",
                name="not_admitted",
                arguments={"path": "README.md"},
            )
        ],
    )
    user_message = ProviderMessage(role="user", content="Inspect README.md")
    terminal_message = ProviderMessage(role="assistant", content="complete")

    semantic_history = ()
    for message in (
        user_message,
        valid_message,
        tool_message,
        invalid_message,
    ):
        semantic_history = _append_message(semantic_history, message)

    request = ProviderRequest(
        stream=False,
        messages=list(semantic_history),
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read one workspace file.",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                        "additionalProperties": False,
                    },
                },
            }
        ],
    )
    exchange = ProviderExchangeV2(
        schema_version="bb.provider_exchange.v2",
        exchange_id="semantic-replay-1",
        correlation=ProviderCorrelation(
            session_id="semantic-session-1",
            input_id="semantic-input-1",
            turn_id="semantic-turn-1",
        ),
        provider=ProviderIdentity(
            provider_id="fixed-policy",
            runtime_id="fixed-policy-runtime",
            route_id=None,
            model="fixed-policy-model",
        ),
        request=request,
        events=[
            ProviderEvent(sequence=0, kind="response_start"),
            ProviderEvent(
                sequence=1,
                kind="tool_call_start",
                content_index=0,
                message_id="assistant-1",
                call_id=valid_call.id,
                name=valid_call.name,
            ),
            ProviderEvent(
                sequence=2,
                kind="tool_call_end",
                content_index=0,
                message_id="assistant-1",
                call_id=valid_call.id,
                arguments_json=valid_call.arguments_json,
                arguments=valid_call.parsed_arguments,
            ),
        ],
        terminal=ProviderDone(
            output_emitted=True,
            finish_reason="stop",
            assistant_messages=[terminal_message.as_dict()],
        ),
    )
    document = encode_provider_exchange(exchange)
    event_log = canonical_json(document).encode("utf-8")
    return _ReplayProof(
        semantic_history=semantic_history,
        exchange=document,
        workspace_digest=workspace_digest,
        event_log=event_log,
    )


def _replay_fake_policy_event_log(event_log: bytes) -> _ReplayProof:
    document = json.loads(event_log)
    exchange = ProviderExchangeV2.from_dict(document)
    semantic_history = tuple(dict(message) for message in exchange.request.messages)
    workspace_digests = {
        json.loads(block["content"])["workspace_digest"]
        for message in semantic_history
        if message["role"] == "tool_result"
        for block in message["content"]
        if block["type"] == "tool_result"
    }
    if len(workspace_digests) != 1:
        raise AssertionError("event log does not bind one workspace digest")
    return _ReplayProof(
        semantic_history=semantic_history,
        exchange=exchange.as_dict(),
        workspace_digest=workspace_digests.pop(),
        event_log=event_log,
    )


def test_interface_map_is_versioned_and_names_public_contracts() -> None:
    assert SEMANTIC_INTERFACE_MAP.schema_version == (
        SEMANTIC_INTERFACE_MAP_SCHEMA_VERSION
    )
    document = SEMANTIC_INTERFACE_MAP.as_dict()
    assert document["schema_version"] == SEMANTIC_INTERFACE_MAP_SCHEMA_VERSION
    assert document["external_ual_ownership"] == [
        "sampled_token_history",
        "behavior_logprobs",
        "loss_masks",
        "behavior_policy_identity",
        "training_admission",
        "trajectory_join",
    ]
    names = {item["name"] for item in document["interfaces"]}
    assert names == {
        "semantic_message",
        "semantic_history",
        "tool_call",
        "tool_result",
        "provider_event",
        "provider_exchange",
        "policy_runtime",
        "tool_runtime",
        "workspace_runtime",
        "runner_lifecycle",
    }
    for item in document["interfaces"]:
        module_name, symbol_name = item["symbol"].rsplit(".", 1)
        assert getattr(importlib.import_module(module_name), symbol_name)


def test_interface_map_keeps_controller_ual_tool_and_workspace_ownership() -> None:
    assert SEMANTIC_INTERFACE_MAP.binding("semantic_message").owner == "external_ual"
    assert SEMANTIC_INTERFACE_MAP.binding("semantic_history").owner == (
        "breadboard_controller"
    )
    assert SEMANTIC_INTERFACE_MAP.binding("tool_call").owner == "external_ual"
    assert SEMANTIC_INTERFACE_MAP.binding("tool_result").owner == "tool_runtime"
    assert SEMANTIC_INTERFACE_MAP.binding("tool_runtime").owner == "tool_runtime"
    assert SEMANTIC_INTERFACE_MAP.binding("workspace_runtime").owner == "workspace"
    assert SEMANTIC_INTERFACE_MAP.binding("runner_lifecycle").owner == (
        "breadboard_controller"
    )


def test_fake_policy_replay_proves_structured_call_tool_correlation_and_terminal() -> (
    None
):
    proof = _fake_policy_replay({"README.md": "hello"})
    history = proof.semantic_history

    call_message = history[1]
    call_block = call_message["content"][0]
    assert call_message["role"] == "assistant"
    assert call_block["type"] == "tool_call"
    assert call_block["call_id"] == "call-read-1"
    assert call_block["name"] == "read_file"
    assert call_block["arguments"] == {"path": "README.md"}

    result_message = history[2]
    result_block = result_message["content"][0]
    assert result_message["role"] == "tool_result"
    assert result_block["type"] == "tool_result"
    assert result_block["call_id"] == call_block["call_id"]
    assert result_block["is_error"] is False

    invalid_message = history[3]
    invalid_block = invalid_message["content"][0]
    assert invalid_block["type"] == "tool_call"
    assert invalid_block["name"] == "not_admitted"
    assert invalid_block["call_id"] != result_block["call_id"]
    assert all(
        event.get("call_id") != invalid_block["call_id"]
        for event in proof.exchange["events"]
    )

    terminal = proof.exchange["terminal"]
    assert terminal["kind"] == "done"
    assert terminal["finish_reason"] == "stop"
    assert terminal["assistant_messages"][0]["content"][0] == {
        "type": "text",
        "text": "complete",
    }


def test_fake_policy_replay_is_append_only_and_deterministic() -> None:
    workspace = {"README.md": "hello", "src/main.py": "print('ok')"}
    first = _fake_policy_replay(workspace)
    second = _fake_policy_replay(workspace)

    assert first.semantic_history == second.semantic_history
    assert first.exchange == second.exchange
    assert first.workspace_digest == second.workspace_digest
    assert first.event_log == second.event_log
    replayed = _replay_fake_policy_event_log(first.event_log)
    assert replayed.semantic_history == first.semantic_history
    assert replayed.exchange == first.exchange
    assert replayed.workspace_digest == first.workspace_digest
    assert canonical_json(first.semantic_history) == canonical_json(
        second.semantic_history
    )

    extended = _append_message(
        first.semantic_history,
        ProviderMessage(role="assistant", content="done"),
    )
    assert len(first.semantic_history) == 4
    assert len(extended) == 5
    assert first.semantic_history[3]["content"][0]["name"] == "not_admitted"
