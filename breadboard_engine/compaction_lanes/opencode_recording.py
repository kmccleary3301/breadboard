"""opencode recording responsibility owner."""
from __future__ import annotations
import json
import threading
from typing import Any
from breadboard_engine.compaction_lanes.mock_provider import MockProvider, MockRequestHandler, MockThreadingServer, MockScriptEngine

class SessionScriptEngine(MockScriptEngine):
    """Keep the background request off the root's scripted response queue."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = threading.local()
        self.child_script = None

    def set_child_script(self, root_session_id: str, script: list[dict[str, Any]]):
        """Use the same auxiliary FIFO for direct stock and native forwarding.

        Descriptors are ordinary responses_sse results, or delegated_pending
        to hold a real generation until delegated_release is set.
        """
        with self._lock:
            self.delegated_root = root_session_id
            self.child_script = list(script)
        self.delegated_arrived = threading.Event()
        self.delegated_release = threading.Event()
        self.delegated_finished = threading.Event()

    def record_request(self, *, method, path, headers, raw_body, parsed_json):
        self.request.body = parsed_json
        root = getattr(self, "delegated_root", None)
        if root is not None and parsed_json.get("prompt_cache_key") == root and any(
            item.get("type") == "function_call_output" and item.get("call_id") == "call_delegated"
            for item in parsed_json.get("input", [])
        ):
            # Both generations are real stock requests. Admit the first root
            # continuation only after the child's request has been recorded.
            if not self.delegated_arrived.wait(10):
                raise TimeoutError("The real delegated model request did not arrive")
        return super().record_request(method=method, path=path, headers=headers,
                                      raw_body=raw_body, parsed_json=parsed_json)

    def next_response(self):
        root = getattr(self, "delegated_root", None)
        if root is not None and self.request.body.get("prompt_cache_key") != root:
            if self.child_script is None:
                return {"type": "delegated_pending"}
            with self._lock:
                if not self.child_script:
                    raise RuntimeError("Mock auxiliary script exhausted")
                return self.child_script.pop(0)
        return super().next_response()

class ResponsesHandler(MockRequestHandler):
    """Serve the same scripted completion in streaming and unary transports."""

    def do_GET(self):
        if self.path not in {"/v1/lane/script-progress", "/v1/lane/release-child"}:
            return super().do_GET()
        engine = self.server.engine
        if self.path == "/v1/lane/release-child":
            engine.delegated_release.set()
        with engine._lock:
            body = json.dumps({"remaining": len(engine.script)}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_openai_responses(self, item: dict[str, Any], request_json: Any) -> None:
        engine = self.server.engine
        root = getattr(engine, "delegated_root", None)
        if root is not None and request_json.get("prompt_cache_key") != root:
            engine.delegated_arrived.set()
        if item["type"] == "delegated_pending":
            # Real background generation remains active while the root compacts.
            # Both native processes cancel it when this scripted episode closes.
            if not engine.delegated_release.wait(360):
                raise TimeoutError("The delegated generation was not cleaned up")
            self.close_connection = True
            engine.delegated_finished.set()
            return
        if item["type"] == "delegated_complete":
            if not engine.delegated_release.wait(360):
                raise TimeoutError("The delegated completion was not released")
            try:
                self._handle_openai_responses(item["response"], request_json)
            finally:
                engine.delegated_finished.set()
            return
        if item["type"] == "raw" and request_json.get("stream") is not True:
            event = json.loads(item["chunks"][-1].split("data: ", 1)[1])
            self._send_raw_response({"type": "raw", "headers": {"content-type": "application/json"}, "body": json.dumps(event["response"])})
            return
        super()._handle_openai_responses(item, request_json)

class ResponsesProvider(MockProvider):
    def __init__(self, script=None, record_path=None, host="127.0.0.1", port=0):
        super().__init__(script=script, record_path=None, host=host, port=port)
        self.engine = SessionScriptEngine(script=script, record_path=record_path)

    def start(self) -> str:
        self.server = MockThreadingServer((self.host, self.port), ResponsesHandler, engine=self.engine)
        self.port = self.server.server_port
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url
