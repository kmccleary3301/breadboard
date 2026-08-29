import types

from breadboard_engine.agent_llm_openai import OpenAIConductor
from breadboard_engine.error_handling.error_handler import (
    ErrorHandler,
    public_error_projection,
)
from breadboard_engine.provider.contracts import ProviderRuntimeError


def _make_conductor_with_logger():
    cls = OpenAIConductor.__ray_metadata__.modified_class
    inst = object.__new__(cls)

    class LoggerStub:
        def __init__(self):
            self.run_dir = "dummy"
            self.json_calls = []
            self.text_calls = []

        def write_json(self, path, data):
            self.json_calls.append((path, data))
            return path

        def write_text(self, path, content):
            self.text_calls.append((path, content))
            return path

    inst.logger_v2 = LoggerStub()
    inst.md_writer = types.SimpleNamespace(system=lambda text: text)
    inst.config = {}
    return inst, inst.logger_v2


def test_public_provider_error_projection_never_copies_exception_text() -> None:
    provider_error = ProviderRuntimeError(
        "secret provider response",
        details={"code": "provider_denied", "body": "secret body"},
        kind="provider",
    )

    assert ErrorHandler().handle_provider_error(
        provider_error,
        messages=[],
        transcript=[],
    ) == {
        "error": "provider_denied",
        "error_type": "provider",
        "hint": None,
    }
    assert public_error_projection(
        RuntimeError("secret runtime exception"),
        default_code="run_loop_error",
    ) == {
        "error": "run_loop_error",
        "error_type": "runtime",
        "hint": None,
    }


def test_persist_error_artifacts_writes_expected_files():
    conductor, logger_stub = _make_conductor_with_logger()
    payload = {
        "error": "Failed",
        "details": {
            "response_headers": {"content-type": "text/html"},
            "raw_body_b64": "YmFzZTY0",
            "raw_excerpt": "raw excerpt",
            "html_excerpt": "<html>snippet</html>",
        },
    }

    conductor._persist_error_artifacts(3, payload)

    json_paths = [path for path, _ in logger_stub.json_calls]
    text_paths = [path for path, _ in logger_stub.text_calls]

    assert "errors/turn_3.json" in json_paths
    assert "raw/responses/turn_3.headers.json" in json_paths
    assert "raw/responses/turn_3.body.b64" in text_paths
    assert "raw/responses/turn_3.raw_excerpt.txt" in text_paths
    assert "raw/responses/turn_3.html_excerpt.txt" in text_paths
