from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from breadboard_engine.compilation.provider_response import HERMES_RESPONSE_CONSUMER_ID
from breadboard.rl.harness.runners import conductor as conductor_module
from breadboard.rl.harness.runners.base import (
    RunnerDependencyError,
    RunnerProtocolError,
    RunnerTerminationEvent,
    RunnerTurn,
)

_TOOL_ORDER = (
    "patch", "read_file", "search_files", "skill_view",
    "skills_list", "terminal", "write_file",
)
_DIGEST = conductor_module.canonical_sha256(())
_EPISODE_TOOLS = tuple(
    {"type": "function", "function": {"name": name, "parameters": {"type": "object"}}}
    for name in _TOOL_ORDER
)


class CompactionFakeNativePort:
    def __init__(
        self,
        *,
        mode: str = "threshold",
        summary_count: int = 1,
    ) -> None:
        self.operations: list[str] = []
        self.log: list[tuple[str, str]] = []
        self.init_count = 0
        self.mode = mode
        self.summary_count = summary_count
        self.sample_calls = 0
        self.schema_overlay = None
        self.history: list[dict[str, Any]] = [
            {"role": "system", "content": "You are Hermes, an autonomous AI assistant."},
            {"role": "user", "content": "Please analyze workspace files."},
        ]
        self.summary_published: list[Mapping[str, Any]] = []
        self.overflow_attempts = 0
        self.last_provider_status = 200

    @property
    def tool_bindings(self) -> tuple[Any, ...]:
        return ()

    @property
    def declared_workspace(self) -> str:
        return "/workspace"

    async def begin_native_workspace_effects(self) -> None:
        self.log.append(("effects", "begin"))

    async def close_native_runtime(self) -> dict[str, Any]:
        self.log.append(("effects", "close"))
        return {"kind": "closed", "cleanup": {"all_dead": True}}

    async def measure_workspace_effects(self) -> dict[str, Any]:
        self.log.append(("effects", "measure"))
        return {}

    async def invoke_native_phase(
        self, operation: str, payload: Mapping[str, Any], *, timeout_ms: int,
    ) -> Mapping[str, Any]:
        self.operations.append(operation)
        self.log.append(("operation", operation))
        if operation == "initialize":
            self.init_count += 1
            self.schema_overlay = payload.get("schema_overlay")
            return {
                "schema_version": "bb.hermes-native.v1", "kind": "history_checkpoint",
                "event_delta": (), "history_digest": _DIGEST, "status": "RUNNING",
                "iteration": 0,
            }
        if operation == "history_ack":
            return {
                "schema_version": "bb.hermes-native.v1", "kind": "initialized",
                "event_delta": (), "history_digest": _DIGEST, "status": "RUNNING",
                "iteration": 0, "tool_schemas": _EPISODE_TOOLS,
                "source_runtime": {"workspace": "/workspace"},
            }
        if operation == "provider_response":
            resp = payload.get("http_response", {})
            self.last_provider_status = resp.get("status_code", 200)
            if self.mode == "threshold" and len(self.summary_published) == 1 and not any("[CONTEXT COMPACTION" in m.get("content", "") for m in self.history):
                summary_text = "Prior workspace search found helper.py and main.py."
                compacted_entry = {
                    "role": "user",
                    "content": f"[CONTEXT COMPACTION - The following is a summary of the conversation so far, not a user message]\n{summary_text}",
                }
                self.history = [self.history[0], compacted_entry, self.history[-1]]
            elif (self.mode == "overflow" or self.mode == "repeated_overflow") and self.last_provider_status == 200 and self.overflow_attempts > 0:
                summary_text = f"Compacted history after overflow recovery attempt {self.overflow_attempts}."
                compacted_entry = {
                    "role": "user",
                    "content": f"[CONTEXT COMPACTION - The following is a summary of the conversation so far, not a user message]\n{summary_text}",
                }
                self.history = [self.history[0], compacted_entry, self.history[-1]]

            return {
                "schema_version": "bb.hermes-native.v1",
                "kind": "sample_ready",
                "raw_response_b64": base64.b64encode(b"{}").decode(),
                "event_delta": (),
                "history_digest": _DIGEST,
                "status": "RUNNING",
                "iteration": 0,
            }
        if operation == "sample":
            self.sample_calls += 1
            if self.mode == "bound_overflow":
                if self.sample_calls <= self.summary_count:
                    body = json.dumps({"messages": [{"role": "user", "content": f"summary-{self.sample_calls}"}]}).encode()
                    return {
                        "schema_version": "bb.hermes-native.v1",
                        "kind": "provider_request",
                        "purpose": "compaction_summary",
                        "stock_body_sha256": f"sha256:summary-{self.sample_calls}",
                        "event_delta": (), "history_digest": _DIGEST, "status": "RUNNING", "iteration": 0,
                        "http_request": {
                            "method": "POST",
                            "url": "https://api.openai.com/v1/chat/completions",
                            "headers": [["content-type", "application/json"]],
                            "body_b64": base64.b64encode(body).decode(),
                        },
                    }
                body = json.dumps({"messages": [{"role": "user", "content": "turn-1-request"}]}).encode()
                return {
                    "schema_version": "bb.hermes-native.v1",
                    "kind": "provider_request",
                    "event_delta": (), "history_digest": _DIGEST, "status": "RUNNING", "iteration": 0,
                    "http_request": {
                        "method": "POST",
                        "url": "https://api.openai.com/v1/chat/completions",
                        "headers": [["content-type", "application/json"]],
                        "body_b64": base64.b64encode(body).decode(),
                    },
                }

            if self.mode == "threshold":
                if self.sample_calls == 1:
                    stock_body = json.dumps({
                        "model": "model",
                        "messages": [{"role": "user", "content": "Hermes compaction summary request"}],
                    }).encode()
                    stock_sha = hashlib.sha256(stock_body).hexdigest()
                    wire_body = json.dumps({
                        "model": "model",
                        "messages": [{"role": "user", "content": "Hermes compaction summary request"}],
                        "tools": list(_EPISODE_TOOLS),
                        "max_tokens": 2048,
                    }).encode()
                    req = {
                        "schema_version": "bb.hermes-native.v1",
                        "kind": "provider_request",
                        "purpose": "compaction_summary",
                        "stock_body_sha256": stock_sha,
                        "event_delta": (), "history_digest": _DIGEST, "status": "RUNNING", "iteration": 0,
                        "http_request": {
                            "method": "POST",
                            "url": "https://api.openai.com/v1/chat/completions",
                            "headers": [["content-type", "application/json"]],
                            "body_b64": base64.b64encode(wire_body).decode(),
                        },
                    }
                    self.summary_published.append(req)
                    return req
                elif self.sample_calls == 2:
                    wire_body = json.dumps({
                        "model": "model",
                        "messages": self.history,
                        "tools": list(_EPISODE_TOOLS),
                        "max_tokens": 2048,
                    }).encode()
                    return {
                        "schema_version": "bb.hermes-native.v1",
                        "kind": "provider_request",
                        "event_delta": (), "history_digest": _DIGEST, "status": "RUNNING", "iteration": 0,
                        "http_request": {
                            "method": "POST",
                            "url": "https://api.openai.com/v1/chat/completions",
                            "headers": [["content-type", "application/json"]],
                            "body_b64": base64.b64encode(wire_body).decode(),
                        },
                    }
                else:
                    return {
                        "schema_version": "bb.hermes-native.v1",
                        "kind": "sample_ready",
                        "event_delta": (), "history_digest": _DIGEST, "status": "FINISHED",
                        "iteration": 1, "public_stop": "completed",
                    }

            if self.mode == "overflow":
                if self.sample_calls == 1:
                    wire_body = json.dumps({
                        "model": "model",
                        "messages": self.history,
                        "tools": list(_EPISODE_TOOLS),
                        "max_tokens": 2048,
                    }).encode()
                    return {
                        "schema_version": "bb.hermes-native.v1",
                        "kind": "provider_request",
                        "event_delta": (), "history_digest": _DIGEST, "status": "RUNNING", "iteration": 0,
                        "http_request": {
                            "method": "POST",
                            "url": "https://api.openai.com/v1/chat/completions",
                            "headers": [["content-type", "application/json"]],
                            "body_b64": base64.b64encode(wire_body).decode(),
                        },
                    }
                elif self.sample_calls == 2:
                    self.overflow_attempts += 1
                    stock_body = json.dumps({
                        "model": "model",
                        "messages": [{"role": "user", "content": "Hermes overflow summary prompt"}],
                    }).encode()
                    stock_sha = hashlib.sha256(stock_body).hexdigest()
                    wire_body = json.dumps({
                        "model": "model",
                        "messages": [{"role": "user", "content": "Hermes overflow summary prompt"}],
                        "tools": list(_EPISODE_TOOLS),
                        "max_tokens": 2048,
                    }).encode()
                    req = {
                        "schema_version": "bb.hermes-native.v1",
                        "kind": "provider_request",
                        "purpose": "compaction_summary",
                        "stock_body_sha256": stock_sha,
                        "event_delta": (), "history_digest": _DIGEST, "status": "RUNNING", "iteration": 0,
                        "http_request": {
                            "method": "POST",
                            "url": "https://api.openai.com/v1/chat/completions",
                            "headers": [["content-type", "application/json"]],
                            "body_b64": base64.b64encode(wire_body).decode(),
                        },
                    }
                    self.summary_published.append(req)
                    return req
                elif self.sample_calls == 3:
                    wire_body = json.dumps({
                        "model": "model",
                        "messages": self.history,
                        "tools": list(_EPISODE_TOOLS),
                        "max_tokens": 2048,
                    }).encode()
                    return {
                        "schema_version": "bb.hermes-native.v1",
                        "kind": "provider_request",
                        "event_delta": (), "history_digest": _DIGEST, "status": "RUNNING", "iteration": 0,
                        "http_request": {
                            "method": "POST",
                            "url": "https://api.openai.com/v1/chat/completions",
                            "headers": [["content-type", "application/json"]],
                            "body_b64": base64.b64encode(wire_body).decode(),
                        },
                    }
                else:
                    return {
                        "schema_version": "bb.hermes-native.v1",
                        "kind": "sample_ready",
                        "event_delta": (), "history_digest": _DIGEST, "status": "FINISHED",
                        "iteration": 1, "public_stop": "completed",
                    }

            if self.mode == "repeated_overflow":
                if self.sample_calls in (1, 3, 5):
                    wire_body = json.dumps({
                        "model": "model",
                        "messages": self.history,
                        "tools": list(_EPISODE_TOOLS),
                        "max_tokens": 2048,
                    }).encode()
                    return {
                        "schema_version": "bb.hermes-native.v1",
                        "kind": "provider_request",
                        "event_delta": (), "history_digest": _DIGEST, "status": "RUNNING", "iteration": 0,
                        "http_request": {
                            "method": "POST",
                            "url": "https://api.openai.com/v1/chat/completions",
                            "headers": [["content-type", "application/json"]],
                            "body_b64": base64.b64encode(wire_body).decode(),
                        },
                    }
                elif self.sample_calls in (2, 4, 6):
                    self.overflow_attempts += 1
                    stock_body = json.dumps({
                        "model": "model",
                        "messages": [{"role": "user", "content": f"overflow-summary-{self.overflow_attempts}"}],
                    }).encode()
                    stock_sha = hashlib.sha256(stock_body).hexdigest()
                    wire_body = json.dumps({
                        "model": "model",
                        "messages": [{"role": "user", "content": f"overflow-summary-{self.overflow_attempts}"}],
                        "tools": list(_EPISODE_TOOLS),
                        "max_tokens": 2048,
                    }).encode()
                    req = {
                        "schema_version": "bb.hermes-native.v1",
                        "kind": "provider_request",
                        "purpose": "compaction_summary",
                        "stock_body_sha256": stock_sha,
                        "event_delta": (), "history_digest": _DIGEST, "status": "RUNNING", "iteration": 0,
                        "http_request": {
                            "method": "POST",
                            "url": "https://api.openai.com/v1/chat/completions",
                            "headers": [["content-type", "application/json"]],
                            "body_b64": base64.b64encode(wire_body).decode(),
                        },
                    }
                    self.summary_published.append(req)
                    return req
                else:
                    return {
                        "schema_version": "bb.hermes-native.v1",
                        "kind": "sample_ready",
                        "event_delta": (),
                        "history_digest": _DIGEST,
                        "status": "ERROR",
                        "iteration": 0,
                    }
            if self.mode == "compaction_off":
                if self.sample_calls == 1:
                    wire_body = json.dumps({
                        "model": "model",
                        "messages": self.history,
                        "tools": list(_EPISODE_TOOLS),
                        "max_tokens": 2048,
                    }).encode()
                    return {
                        "schema_version": "bb.hermes-native.v1",
                        "kind": "provider_request",
                        "event_delta": (), "history_digest": _DIGEST, "status": "RUNNING", "iteration": 0,
                        "http_request": {
                            "method": "POST",
                            "url": "https://api.openai.com/v1/chat/completions",
                            "headers": [["content-type", "application/json"]],
                            "body_b64": base64.b64encode(wire_body).decode(),
                        },
                    }
                else:
                    return {
                        "schema_version": "bb.hermes-native.v1",
                        "kind": "sample_ready",
                        "event_delta": (), "history_digest": _DIGEST, "status": "FINISHED",
                        "iteration": 1, "public_stop": "completed",
                    }

        if operation == "prepare":
            return {
                "schema_version": "bb.hermes-native.v1",
                "kind": "prepared",
                "event_delta": (), "history_digest": _DIGEST, "status": "FINISHED",
                "iteration": 1, "public_stop": "completed",
                "actions": (), "segments": (),
            }
        raise AssertionError(operation)


class HarnessCompactionSession(conductor_module._ConductorSession):
    def __init__(self, log: list[tuple[str, str]]) -> None:
        self.log = log
        self.sequence = 0
        self.provider_invocations: list[Mapping[str, Any]] = []
        self.overflow_on_non_summary: bool = False
        self.overflow_count: int = 1

    async def _emit(self, event: Any) -> None:
        self.log.append(("event", type(event).__name__))
        self._events.append(replace(event, sequence=self.sequence))
        self.sequence += 1

    async def _checkpoint(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def _commit_termination(self, termination: Any) -> None:
        await self._emit(
            RunnerTerminationEvent(
                0,
                self._open_request.episode_id,
                self._open_request.effective_plan_digest,
                len(self._turns),
                termination,
            )
        )

    def _context(self) -> dict[str, Any]:
        return {}

    async def _raise_error(self, error: BaseException, **kwargs: Any) -> None:
        raise error

    async def _invoke_native_policy(
        self, http_request: Mapping[str, Any], *, model: Any, turn: int,
        verify_staged_body: bool = False,
        compaction_summary: bool = False,
    ) -> Mapping[str, Any]:
        self.provider_invocations.append({
            "request": http_request,
            "compaction_summary": compaction_summary,
        })
        if self.overflow_on_non_summary and not compaction_summary:
            non_summary_calls = [inv for inv in self.provider_invocations if not inv["compaction_summary"]]
            if len(non_summary_calls) <= self.overflow_count:
                error_body = json.dumps({
                    "error": {
                        "message": "context_length_exceeded",
                        "type": "invalid_request_error",
                        "code": "context_length_exceeded",
                    }
                }).encode()
                return {
                    "status_code": 400,
                    "headers": [["content-type", "application/json"]],
                    "body_b64": base64.b64encode(error_body).decode(),
                }
        response_body = json.dumps({
            "id": f"resp-{len(self.provider_invocations)}",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        }).encode()
        return {
            "status_code": 200,
            "headers": [["content-type", "application/json"]],
            "body_b64": base64.b64encode(response_body).decode(),
        }


class FakeCompactionBinding:
    source_model_config = {"model_name": "model", "max_input_tokens": 131072}

    def bind_native_tools(self, tools: tuple[Mapping[str, Any], ...]) -> None:
        self.bound_tools = tools


def _configure_compaction_session(
    session: HarnessCompactionSession,
    tools: CompactionFakeNativePort,
    *,
    compaction: bool = True,
) -> None:
    limits = SimpleNamespace(max_turns=8, action_timeout_ms=40_000, transcript_bytes=1_000_000)
    session._open_request = SimpleNamespace(
        episode_id="hermes-compaction-test",
        effective_plan_digest="sha256:" + "a" * 64,
        effective_plan=SimpleNamespace(effective_capabilities=SimpleNamespace(limits=limits)),
    )
    session._projection = SimpleNamespace(
        source_consumer_id=HERMES_RESPONSE_CONSUMER_ID,
        source_profile={
            "compaction": compaction,
            "advertisement": {},
            "schema_overlay": {"read_file": {}, "terminal": {}},
        },
        models=(SimpleNamespace(params={}, policy_slot_id="slot"),),
        modes=(SimpleNamespace(tool_ids=_TOOL_ORDER),),
    )
    session._binding = FakeCompactionBinding()
    session._tools = tools
    session._turns = []
    session._events = []
    session._native_cleanup_outcome = conductor_module.NativeCleanupOutcome(False, None, None, None)


@pytest.mark.asyncio
async def test_compaction_threshold_preflight_summary_exchange_and_history() -> None:
    """Change 6 (a): threshold preflight fires with a small context window:
    - Exactly one summary exchange published with purpose='compaction_summary'
    - Wire body has episode tools + episode max_tokens
    - stock_body_sha256 equals sha256 of Hermes's original body
    - History after compaction contains Hermes's '[CONTEXT COMPACTION' summary
    - Episode completes (ASSISTANT_COMPLETE)
    - Request cap / turn cap not consumed by the summary
    """
    tools = CompactionFakeNativePort(mode="threshold")
    session = HarnessCompactionSession(tools.log)
    _configure_compaction_session(session, tools, compaction=True)

    profile = conductor_module.NATIVE_STREAM_PROFILES[HERMES_RESPONSE_CONSUMER_ID]
    result = await session._loop_native_stream(
        conductor_module.ConductorRunRequest({"prompt": "test"}),
        profile,
    )

    # Episode completed cleanly
    assert result.termination == conductor_module.RunnerTermination.ASSISTANT_COMPLETE
    assert result.episode_id == "hermes-compaction-test"

    # Exactly one summary exchange published
    assert len(tools.summary_published) == 1
    summary_req = tools.summary_published[0]
    assert summary_req["purpose"] == "compaction_summary"

    # Wire body has episode tools and episode max_tokens
    wire_body = json.loads(base64.b64decode(summary_req["http_request"]["body_b64"]).decode())
    assert wire_body["tools"] == list(_EPISODE_TOOLS)
    assert wire_body["max_tokens"] == 2048

    # stock_body_sha256 equals sha256 of original stock body
    stock_body = json.dumps({
        "model": "model",
        "messages": [{"role": "user", "content": "Hermes compaction summary request"}],
    }).encode()
    assert summary_req["stock_body_sha256"] == hashlib.sha256(stock_body).hexdigest()

    # History after compaction contains Hermes's [CONTEXT COMPACTION summary
    assert any("[CONTEXT COMPACTION" in m.get("content", "") for m in tools.history)

    # Provider was invoked twice: summary exchange, then turn 1 request
    assert len(session.provider_invocations) == 2
    assert session.provider_invocations[0]["compaction_summary"] is True
    assert session.provider_invocations[1]["compaction_summary"] is False

    # Exactly one RunnerTurn appended (summary did NOT consume a turn)
    assert len(session._turns) == 1

    # Replay trace captures both exchanges, first tagged with _compaction_summary
    replay_trace = result.response["replay_trace"]
    trace_requests = replay_trace["requests"]
    assert len(trace_requests) == 2
    assert trace_requests[0].get("_compaction_summary") is True
    assert trace_requests[0].get("stock_body_sha256") == summary_req["stock_body_sha256"]
    assert "_compaction_summary" not in trace_requests[1]


@pytest.mark.asyncio
async def test_compaction_overflow_error_recovery_retry_succeeds() -> None:
    """Change 6 (b): overflow error -> compress -> retry succeeds.
    - Initial request receives 400 context_length_exceeded
    - Hermes enters overflow recovery and emits compaction summary exchange
    - Summary exchange is processed within the same step
    - Retried request with compacted history succeeds
    - Episode completes cleanly
    """
    tools = CompactionFakeNativePort(mode="overflow")
    session = HarnessCompactionSession(tools.log)
    session.overflow_on_non_summary = True
    session.overflow_count = 1
    _configure_compaction_session(session, tools, compaction=True)

    profile = conductor_module.NATIVE_STREAM_PROFILES[HERMES_RESPONSE_CONSUMER_ID]
    result = await session._loop_native_stream(
        conductor_module.ConductorRunRequest({"prompt": "test"}),
        profile,
    )

    # Episode completed cleanly after retry
    assert result.termination == conductor_module.RunnerTermination.ASSISTANT_COMPLETE

    # Summary exchange was published during overflow recovery
    assert len(tools.summary_published) == 1
    assert tools.summary_published[0]["purpose"] == "compaction_summary"

    # History was compacted
    assert any("[CONTEXT COMPACTION" in m.get("content", "") for m in tools.history)

    # Invocations: 1 (overflow) + 1 (summary) + 1 (successful retry) = 3
    assert len(session.provider_invocations) == 3
    assert session.provider_invocations[1]["compaction_summary"] is True

    # Exactly one RunnerTurn produced
    assert len(session._turns) == 1


@pytest.mark.asyncio
async def test_compaction_repeated_overflow_stops_after_3_attempts() -> None:
    """Change 6 (c): repeated overflow -> stops after 3 attempts with Hermes's own terminal behavior."""
    tools = CompactionFakeNativePort(mode="repeated_overflow")
    session = HarnessCompactionSession(tools.log)
    session.overflow_on_non_summary = True
    session.overflow_count = 10  # always overflow
    _configure_compaction_session(session, tools, compaction=True)

    profile = conductor_module.NATIVE_STREAM_PROFILES[HERMES_RESPONSE_CONSUMER_ID]
    with pytest.raises(RunnerDependencyError) as exc_info:
        await session._loop_native_stream(
            conductor_module.ConductorRunRequest({"prompt": "test"}),
            profile,
        )

    assert exc_info.value.code == "native_source_failed"
    assert tools.overflow_attempts == 3


@pytest.mark.asyncio
async def test_compaction_off_no_summary_exchange_identical_to_before() -> None:
    """Change 6 (d): compaction off -> no summary exchange, identical to before."""
    tools = CompactionFakeNativePort(mode="compaction_off")
    session = HarnessCompactionSession(tools.log)
    _configure_compaction_session(session, tools, compaction=False)

    profile = conductor_module.NATIVE_STREAM_PROFILES[HERMES_RESPONSE_CONSUMER_ID]
    result = await session._loop_native_stream(
        conductor_module.ConductorRunRequest({"prompt": "test"}),
        profile,
    )

    assert result.termination == conductor_module.RunnerTermination.ASSISTANT_COMPLETE
    assert len(tools.summary_published) == 0
    assert len(session.provider_invocations) == 1
    assert session.provider_invocations[0]["compaction_summary"] is False
    assert len(session._turns) == 1

    replay_trace = result.response["replay_trace"]
    assert len(replay_trace["requests"]) == 1
    assert "_compaction_summary" not in replay_trace["requests"][0]


@pytest.mark.asyncio
async def test_checkpointed_compaction_summary_overflow_bound() -> None:
    """When summary exchanges exceed profile.compaction_overflow_attempts + 1 (3 + 1 = 4),
    conductor raises invalid (RunnerProtocolError).
    """
    tools = CompactionFakeNativePort(mode="bound_overflow", summary_count=5)
    session = HarnessCompactionSession(tools.log)
    _configure_compaction_session(session, tools)

    profile = conductor_module.NATIVE_STREAM_PROFILES[HERMES_RESPONSE_CONSUMER_ID]
    with pytest.raises(RunnerProtocolError) as exc_info:
        await session._loop_native_stream(
            conductor_module.ConductorRunRequest({"prompt": "test"}),
            profile,
        )

    assert "too many compaction summary exchanges in one step" in str(exc_info.value)
    assert exc_info.value.code == "native_response_invalid"
