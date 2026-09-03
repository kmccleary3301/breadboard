#!/usr/bin/env python3
"""Pinned network-free capture boundary for FT-03.

The product runtime remains OpenAIResponsesRuntime. This fixture replaces only
its SDK client's responses.stream transport and stops immediately after the
fully effective keyword arguments cross that boundary.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ADAPTER_ID = "breadboard_engine.provider.runtimes.openai.OpenAIResponsesRuntime"
RUNTIME_ID = "openai_responses"
PROVIDER_ID = "openai"
CALL_ID = "openai.responses.stream.v1"
RESPONSES_EXTRA = {"max_output_tokens": 64, "temperature": 0}
PROVIDER_TOOLS = {
    "openai": {"include_reasoning": False, "responses_stateful": False}
}


class FT03CaptureStop(RuntimeError):
    """Expected stop after capture and before network I/O."""


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class _ResponsesCapture:
    def __init__(self, output: Path) -> None:
        self._output = output
        self.calls = 0

    def stream(self, **kwargs: Any) -> None:
        self.calls += 1
        if self.calls != 1:
            raise AssertionError("FT-03 permits exactly one adapter dispatch")
        effective_call = {
            "call": CALL_ID,
            "kwargs": kwargs,
            "stream": True,
        }
        self._output.write_bytes(canonical_bytes(effective_call))
        raise FT03CaptureStop("FT-03 capture complete; network forbidden")


class FT03CaptureClient:
    """SDK-client double accepted by OpenAIResponsesRuntime.invoke."""

    def __init__(self, output: Path) -> None:
        self.responses = _ResponsesCapture(output)
