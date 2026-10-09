from __future__ import annotations

import pytest

from breadboard_engine.compaction import (
    CompactionContext,
    CompactionRecord,
    CompactionState,
    Compactor,
    MethodUnavailable,
    NativeCompaction,
    ProjectionTarget,
    estimate_messages_tokens,
    settings_from_config,
)
from breadboard_engine.compaction.remote import RemoteCompaction


class DummyPort:
    def __init__(self, provider: str, api: str, supported_models: list[str]) -> None:
        self.provider = provider
        self.api = api
        self.supported_models = set(supported_models)
        self.called = False

    def supports(self, model: str) -> bool:
        return model in self.supported_models

    def compact(self, context: CompactionContext) -> CompactionRecord:
        self.called = True
        return context.new_record(
            method="remote",
            first_kept_index=len(context.messages),
            summary="Port summary",
            short_summary="Remote compaction",
            summary_messages=(),
            native=NativeCompaction(
                provider=self.provider,
                api=self.api,
                model=context.target.model,
                items=({"type": "compaction", "encrypted_content": "enc_data"},),
            ),
        )


class FailingPort(DummyPort):
    def compact(self, context: CompactionContext) -> CompactionRecord:
        self.called = True
        raise RuntimeError("Port service temporarily unavailable")


def _make_context(messages, *, remote_ports=(), target=None, window=8000, **config) -> CompactionContext:
    settings = settings_from_config({"enabled": True, "method_order": ["remote", "soft"], **config})
    tgt = target or ProjectionTarget("openai", "responses", "gpt-5")
    return CompactionContext(
        messages=messages,
        state=CompactionState(),
        settings=settings,
        reason="threshold",
        target=tgt,
        context_window=window,
        tokens_before=estimate_messages_tokens(messages),
        remote_ports=remote_ports,
    )


def test_dispatcher_selects_first_matching_port():
    messages = [
        {"role": "system", "content": "You are assistant"},
        {"role": "user", "content": "Hello world"},
        {"role": "assistant", "content": "Hi there"},
    ]
    port_other = DummyPort("anthropic", "messages", ["claude-3"])
    port_match = DummyPort("openai", "responses", ["gpt-5"])
    port_second_match = DummyPort("openai", "responses", ["gpt-5"])

    ctx = _make_context(messages, remote_ports=(port_other, port_match, port_second_match))
    dispatcher = RemoteCompaction()
    record = dispatcher.run(ctx)

    assert port_match.called
    assert not port_other.called
    assert not port_second_match.called
    assert record.method == "remote"
    assert record.native is not None
    assert record.native.model == "gpt-5"


def test_dispatcher_raises_method_unavailable_when_no_matching_port():
    messages = [
        {"role": "system", "content": "You are assistant"},
        {"role": "user", "content": "Hello world"},
    ]
    port_other = DummyPort("anthropic", "messages", ["claude-3"])
    ctx = _make_context(messages, remote_ports=(port_other,))
    dispatcher = RemoteCompaction()

    with pytest.raises(MethodUnavailable, match="No remote compaction port registered"):
        dispatcher.run(ctx)


def test_dispatcher_cascades_on_port_error_in_compactor():
    messages = [
        {"role": "system", "content": "You are assistant"},
        {"role": "user", "content": "Long task" * 500},
        {"role": "assistant", "content": "Long response" * 500},
        {"role": "user", "content": "Next turn" * 500},
    ]
    failing_port = FailingPort("openai", "responses", ["gpt-5"])

    class FallbackSoft:
        name = "soft"

        def run(self, context: CompactionContext) -> CompactionRecord:
            return context.new_record(
                method="soft",
                first_kept_index=3,
                summary="Fallback soft summary",
                short_summary="Soft summary",
                summary_messages=({"role": "user", "content": "<summary>Fallback</summary>"},),
            )

    compactor = Compactor(
        settings_from_config({"enabled": True, "method_order": ["remote", "soft"]}),
        {"remote": RemoteCompaction(), "soft": FallbackSoft()},
    )
    ctx = _make_context(messages, remote_ports=(failing_port,), window=1000)
    outcome = compactor.run(ctx)

    assert failing_port.called
    assert len(outcome.records) == 1
    assert outcome.records[0].method == "soft"
    attempt_statuses = [a.status for a in outcome.attempts]
    assert attempt_statuses == ["failed", "applied"]


def test_remote_endpoint_chat_completions_posting():
    messages = [
        {"role": "system", "content": "system instructions"},
        {"role": "user", "content": "Turn 1" * 100},
        {"role": "assistant", "content": "Answer 1" * 100},
        {"role": "user", "content": "Turn 2" * 100},
        {"role": "assistant", "content": "Answer 2" * 100},
    ]
    posted_calls = []

    def fake_poster(url, payload, headers):
        posted_calls.append({"url": url, "payload": payload, "headers": headers})
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "## Goal\nComplete task\n## Progress\nDone",
                    }
                }
            ]
        }

    ctx = _make_context(
        messages,
        remote_endpoint="https://my-summarizer.internal/v1/chat/completions",
        keep_recent_tokens=50,
    )
    dispatcher = RemoteCompaction(http_poster=fake_poster)
    record = dispatcher.run(ctx)

    assert len(posted_calls) == 1
    call = posted_calls[0]
    assert call["url"] == "https://my-summarizer.internal/v1/chat/completions"
    assert call["payload"]["model"] == "gpt-5"
    assert len(call["payload"]["messages"]) == 2
    assert call["payload"]["messages"][0]["role"] == "system"
    assert call["payload"]["messages"][1]["role"] == "user"
    assert "<conversation>" in call["payload"]["messages"][1]["content"]

    assert record.method == "remote"
    assert record.summary == "## Goal\nComplete task\n## Progress\nDone"
    assert record.readable
    assert len(record.summary_messages) == 1
    assert "<summary>" in record.summary_messages[0]["content"]


def test_remote_endpoint_generic_summarizer_posting():
    messages = [
        {"role": "system", "content": "system instructions"},
        {"role": "user", "content": "Step 1" * 100},
        {"role": "assistant", "content": "Result 1" * 100},
        {"role": "user", "content": "Step 2" * 100},
    ]
    posted_calls = []

    def fake_poster(url, payload, headers):
        posted_calls.append({"url": url, "payload": payload, "headers": headers})
        return {
            "summary": "Custom generic endpoint summary",
            "shortSummary": "Generic short summary",
        }

    ctx = _make_context(
        messages,
        remote_endpoint="https://omp-summarizer.internal/api/compact",
        keep_recent_tokens=50,
    )
    dispatcher = RemoteCompaction(http_poster=fake_poster)
    record = dispatcher.run(ctx)

    assert len(posted_calls) == 1
    call = posted_calls[0]
    assert call["url"] == "https://omp-summarizer.internal/api/compact"
    assert "systemPrompt" in call["payload"]
    assert "prompt" in call["payload"]
    assert record.summary == "Custom generic endpoint summary"
    assert record.short_summary == "Generic short summary"
    assert record.readable
