"""Unregistered OpenClaw adapter; Main installs it in the shared switch."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from breadboard.rl.harness.runners.base import RunnerAdapterDescriptor
from breadboard.rl.harness.runners.openclaw_semantics import OpenClawSemanticsState

OPENCLAW_ADAPTER_ID = "breadboard.openclaw.native-chat"
OPENCLAW_RUNTIME_ABI = "bb.rl.runner.openclaw.v1"
OPENCLAW_IMPLEMENTATION_DIGEST = "openclaw-2026.9.4-3a9d69db-tool-boundary"


@dataclass
class OpenClawSession:
    episode_id: str
    state: OpenClawSemanticsState
    closed: bool = False

    async def run(self, request: Any) -> dict[str, Any]:
        if self.closed:
            raise RuntimeError("OpenClaw session is closed")
        response = request.get("response", request) if isinstance(request, dict) else request
        result = self.state.consume_native_response(response)
        return {
            "episode_id": self.episode_id,
            "tool_batch": [call.to_dict() for call in result.tool_batch.tool_calls],
            "history_mutations": [dict(item) for item in result.history_mutations],
            "terminal": {"kind": self.state.terminal_kind, "native_stop_reason": result.native_stop_reason},
        }

    async def cancel(self, reason: str) -> dict[str, Any]:
        self.closed = True
        return {"cancelled": True, "reason": reason}

    async def close(self) -> dict[str, Any]:
        already = self.closed
        self.closed = True
        return {"already_closed": already}


class OpenClawAdapter:
    """Adapter boundary; transport and conductor admission remain external."""

    def __init__(self, runtime_abi: str = OPENCLAW_RUNTIME_ABI) -> None:
        if runtime_abi != OPENCLAW_RUNTIME_ABI:
            raise ValueError("OpenClaw adapter accepts only its exact runtime ABI")
        self._descriptor = RunnerAdapterDescriptor(
            adapter_id=OPENCLAW_ADAPTER_ID,
            runtime_abi=runtime_abi,
            implementation_digest=OPENCLAW_IMPLEMENTATION_DIGEST,
        )

    @property
    def descriptor(self) -> RunnerAdapterDescriptor:
        return self._descriptor

    async def open(self, request: Any, **_: Any) -> OpenClawSession:
        episode_id = str(
            getattr(request, "episode_id", None)
            or (request.get("episode_id") if isinstance(request, dict) else "openclaw")
        )
        return OpenClawSession(episode_id, OpenClawSemanticsState())
