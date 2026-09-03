import types


from breadboard_engine.provider.capability_probe import ProviderCapabilityProbeRunner
from breadboard_engine.provider.runtime import ProviderResult, ProviderMessage
from breadboard_engine.state.session_state import SessionState


class _StubLogger:
    def __init__(self):
        self.run_dir = None

    def append_text(self, *args, **kwargs):
        pass

    def write_json(self, *args, **kwargs):
        pass

def _seed_correlation(session_state: SessionState) -> None:
    session_state.set_provider_metadata("session_id", "probe-session")
    session_state.set_turn_context(
        input_id="probe-input",
        turn_id="probe-turn",
        turn_index=None,
    )


class _StubInvoker:
    def __init__(self) -> None:
        self.calls = []

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        context = kwargs["runtime_context"]
        result = kwargs["runtime"].invoke(
            client=None,
            model=kwargs["model"],
            messages=kwargs["send_messages"],
            tools=kwargs["tools_schema"],
            stream=kwargs["stream_responses"],
            context=context,
        )
        probe_kind = context.extra["probe_kind"]
        exchange = {
            "schema_version": "bb.provider_exchange.v2",
            "exchange_id": f"px-probe-{probe_kind}",
            "correlation": {
                "session_id": context.session_id,
                "input_id": context.input_id,
                "turn_id": context.turn_id,
            },
            "provider": {
                "provider_id": "stub",
                "runtime_id": "stub",
                "route_id": kwargs["route_id"],
                "model": kwargs["model"],
            },
            "request": {
                "stream": kwargs["stream_responses"],
                "messages": kwargs["send_messages"],
                "tools": kwargs["tools_schema"] or [],
            },
            "events": [{"sequence": 0, "kind": "response_start"}],
            "terminal": {
                "kind": "done",
                "output_emitted": True,
                "finish_reason": "stop",
                "assistant_messages": [
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "PING"}],
                    }
                ],
                "provider_replay": [],
                "evidence_refs": [],
            },
        }
        kwargs["session_state"].record_provider_exchange(exchange)
        result.metadata["provider_exchange"] = exchange
        return result, False


def test_capability_probe_skips_without_api_key(monkeypatch):
    router = types.SimpleNamespace()

    def fake_get_runtime_descriptor(model):
        descriptor = types.SimpleNamespace(provider_id="stub", runtime_id="stub")
        return descriptor, model

    router.get_runtime_descriptor = fake_get_runtime_descriptor
    router.get_credential_origin = lambda *_args, **_kwargs: None

    registry = types.SimpleNamespace(create_runtime=lambda descriptor: None)
    session_state = SessionState(workspace=".", image="img")
    _seed_correlation(session_state)
    runner = ProviderCapabilityProbeRunner(
        router,
        registry,
        _StubLogger(),
        None,
        provider_invoker=_StubInvoker(),
    )

    config = {"provider_probes": {"enabled": True}, "providers": {"models": [{"id": "stub/model"}]}}
    results = runner.run(config, session_state)
    assert results[0].skipped_reason == "missing_api_key"


def test_capability_probe_records_exact_correlated_exchanges():
    router = types.SimpleNamespace()

    def fake_get_runtime_descriptor(model):
        descriptor = types.SimpleNamespace(provider_id="stub", runtime_id="stub")
        return descriptor, model

    router.get_runtime_descriptor = fake_get_runtime_descriptor
    router.get_credential_origin = (
        lambda *_args, **_kwargs: {"kind": "synthetic", "source": "test"}
    )

    class StubRuntime:
        def __init__(self):
            self.descriptor = types.SimpleNamespace(provider_id="stub", runtime_id="stub")


        def invoke(self, *, client, model, messages, tools, stream, context):
            return ProviderResult(
                messages=[ProviderMessage(role="assistant", content="PING")],
                raw_response={},
                metadata={},
            )

    registry = types.SimpleNamespace(create_runtime=lambda descriptor: StubRuntime())
    session_state = SessionState(workspace=".", image="img")
    _seed_correlation(session_state)
    logging_stub = _StubLogger()
    logging_stub.run_dir = "dummy"

    invoker = _StubInvoker()
    runner = ProviderCapabilityProbeRunner(
        router,
        registry,
        logging_stub,
        None,
        provider_invoker=invoker,
    )
    config = {
        "provider_probes": {"enabled": True},
        "providers": {"models": [{"id": "stub/model"}]},
    }

    results = runner.run(config, session_state)
    assert results[0].attempted is True
    assert results[0].stream_success is True
    assert len(invoker.calls) == 3
    history = session_state.get_provider_metadata("provider_exchange_history")
    assert [item["exchange_id"] for item in history] == [
        "px-probe-stream",
        "px-probe-tool",
        "px-probe-json",
    ]
    assert all(
        call["runtime_context"].extra["diagnostic_probe"]
        for call in invoker.calls
    )
    stored = session_state.get_provider_metadata("capability_probes")
    assert isinstance(stored, list) and stored[0]["model_id"] == "stub/model"
