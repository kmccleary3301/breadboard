from __future__ import annotations

from typing import Any

from breadboard.product.evidence.replay import ReplayPlan, ReplayWorkerResult
from breadboard.product.integrations.provider import ProviderPort

from ._common import json_value, load_request, worker_result


class ProviderReplayWorker:
    """Replay one recorded request through an existing product provider port."""

    def __init__(
        self,
        provider: ProviderPort,
        *,
        client: Any,
        context: Any,
        output_path: str = "provider_result.json",
    ) -> None:
        if not isinstance(provider, ProviderPort):
            raise TypeError("provider replay requires a product ProviderPort")
        self.provider = provider
        self.client = client
        self.context = context
        self.output_path = output_path
        self.worker_id = f"provider/{provider.provider_id}/{provider.runtime_id}/replay-v1"

    def execute(self, plan: ReplayPlan, input_bytes: bytes) -> ReplayWorkerResult:
        if plan.worker_id != self.worker_id:
            raise ValueError("replay plan worker_id does not select this provider")
        request = load_request(input_bytes, "bb.provider_replay_input.v1")
        model, messages = request.get("model"), request.get("messages")
        tools, stream = request.get("tools"), request.get("stream", False)
        if not isinstance(model, str) or not model or not isinstance(messages, list):
            raise ValueError("provider replay input requires model and messages")
        if tools is not None and not isinstance(tools, list):
            raise ValueError("provider replay tools must be a list or null")
        if not isinstance(stream, bool):
            raise ValueError("provider replay stream must be a boolean")
        result = self.provider.invoke(
            client=self.client,
            model=model,
            messages=messages,
            tools=tools,
            stream=stream,
            context=self.context,
        )
        normalized_messages = [
            {
                "role": message.role,
                "content": message.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "type": call.type,
                    }
                    for call in message.tool_calls
                ],
                "finish_reason": message.finish_reason,
                "index": message.index,
                "reasoning": json_value(message.reasoning),
                "annotations": json_value(message.annotations),
            }
            for message in result.messages
        ]
        output = {
            "schema_version": "bb.provider_replay_result.v1",
            "provider_id": self.provider.provider_id,
            "runtime_id": self.provider.runtime_id,
            "model": result.model,
            "messages": normalized_messages,
            "usage": json_value(result.usage),
            "encrypted_reasoning": json_value(result.encrypted_reasoning),
            "reasoning_summaries": json_value(result.reasoning_summaries),
            "metadata": json_value(result.metadata),
        }
        return worker_result(
            plan,
            output_path=self.output_path,
            output=output,
            port_kind="provider.invoke",
            request={"model": model, "messages": messages, "tools": tools, "stream": stream},
            response={"model": result.model, "message_count": len(normalized_messages)},
        )
