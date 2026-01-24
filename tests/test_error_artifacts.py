import types

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor


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
