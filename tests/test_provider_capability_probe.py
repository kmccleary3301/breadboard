import types

import pytest

from agentic_coder_prototype.provider_capability_probe import ProviderCapabilityProbeRunner
from agentic_coder_prototype.provider_runtime import ProviderResult, ProviderMessage
from agentic_coder_prototype.state.session_state import SessionState


class _StubLogger:
    def __init__(self):
        self.run_dir = None

    def append_text(self, *args, **kwargs):
        pass

    def write_json(self, *args, **kwargs):
        pass


def test_capability_probe_skips_without_api_key(monkeypatch):
    router = types.SimpleNamespace()

    def fake_get_runtime_descriptor(model):
        descriptor = types.SimpleNamespace(provider_id="stub", runtime_id="stub")
        return descriptor, model

    def fake_create_client_config(model):
        return {"api_key": ""}

    router.get_runtime_descriptor = fake_get_runtime_descriptor
    router.create_client_config = fake_create_client_config

    registry = types.SimpleNamespace(create_runtime=lambda descriptor: None)
    session_state = SessionState(workspace=".", image="img")
    runner = ProviderCapabilityProbeRunner(router, registry, _StubLogger(), None)

    config = {"provider_probes": {"enabled": True}, "providers": {"models": [{"id": "stub/model"}]}}
    results = runner.run(config, session_state)
    assert results[0].skipped_reason == "missing_api_key"


def test_capability_probe_records_results(monkeypatch):
    router = types.SimpleNamespace()

    def fake_get_runtime_descriptor(model):
        descriptor = types.SimpleNamespace(provider_id="stub", runtime_id="stub")
        return descriptor, model

    router.get_runtime_descriptor = fake_get_runtime_descriptor

    def fake_create_client_config(model):
        return {"api_key": "token"}

    router.create_client_config = fake_create_client_config

    class StubRuntime:
        def __init__(self):
            self.descriptor = types.SimpleNamespace(provider_id="stub", runtime_id="stub")

        def create_client(self, *args, **kwargs):
            return object()

        def invoke(self, *, client, model, messages, tools, stream, context):
            return ProviderResult(
                messages=[ProviderMessage(role="assistant", content="PING")],
                raw_response={},
                metadata={},
            )

    registry = types.SimpleNamespace(create_runtime=lambda descriptor: StubRuntime())
    session_state = SessionState(workspace=".", image="img")
    logging_stub = _StubLogger()
    logging_stub.run_dir = "dummy"

    runner = ProviderCapabilityProbeRunner(router, registry, logging_stub, None)
    config = {
        "provider_probes": {"enabled": True},
        "providers": {"models": [{"id": "stub/model"}]},
    }

    results = runner.run(config, session_state)
    assert results[0].attempted is True
    assert results[0].stream_success is True
    stored = session_state.get_provider_metadata("capability_probes")
    assert isinstance(stored, list) and stored[0]["model_id"] == "stub/model"
