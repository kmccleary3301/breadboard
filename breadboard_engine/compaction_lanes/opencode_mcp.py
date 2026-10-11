"""opencode mcp responsibility owner."""
from __future__ import annotations
import json
import threading
import urllib.request
import urllib.parse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

class MCPRelay:
    """Forward real MCP bytes; serialize native create().listTools readiness.

    Stock index.ts:192-213 inserts clients after create(), whose final operation
    is listTools (:503-529). No tool schemas/results or LLM arrays are rewritten.
    """
    UPSTREAMS = {
        "websearch": "https://mcp.exa.ai/mcp?tools=web_search_exa",
        "context7": "https://mcp.context7.com/mcp",
        "grep_app": "https://mcp.grep.app",
    }
    def __init__(self):
        self.condition = threading.Condition()
        self.completed: set[str] = set()
        self.first_lists: set[str] = set()
        self.failures: list[str] = []

    def reset(self) -> None:
        with self.condition:
            self.completed.clear()
            self.first_lists.clear()
            self.condition.notify_all()

    def __enter__(self):
        owner = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            def log_message(self, *_args):
                pass
            def forward(self):
                import httpx
                if self.path == "/reset":
                    owner.reset()
                    self.send_response(204)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                name = self.path.removeprefix("/mcp/")
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                method = json.loads(body).get("method") if body else None
                ordered = False
                if method == "tools/list":
                    with owner.condition:
                        ordered = name not in owner.first_lists
                        owner.first_lists.add(name)
                headers = {key: value for key, value in self.headers.items()
                           if key.lower() not in {"host", "content-length", "connection"}}
                try:
                    with httpx.Client(timeout=60) as client:
                        request = client.build_request(self.command, owner.UPSTREAMS[name],
                                                       headers=headers, content=body)
                        response = client.send(request, stream=True)
                        if ordered:
                            previous = list(owner.UPSTREAMS)[:list(owner.UPSTREAMS).index(name)]
                            with owner.condition:
                                if not owner.condition.wait_for(lambda: all(key in owner.completed for key in previous), 30):
                                    raise TimeoutError(f"MCP readiness stalled before {name}")
                        self.send_response(response.status_code)
                        for key, value in response.headers.items():
                            if key.lower() not in {"transfer-encoding", "connection"}:
                                self.send_header(key, value)
                        self.send_header("Connection", "close")
                        self.end_headers()
                        for chunk in response.iter_raw():
                            self.wfile.write(chunk)
                            self.wfile.flush()
                        response.close()
                        if ordered:
                            # Scheduling only, not persisted/model data. Let the
                            # source event-loop settle before releasing the next.
                            time.sleep(1)
                            with owner.condition:
                                owner.completed.add(name)
                                owner.condition.notify_all()
                except Exception as exc:
                    owner.failures.append(f"{name}: {exc}")
                    self.close_connection = True
            do_POST = forward
            do_GET = forward
            do_DELETE = forward
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

def mcp_configuration(relay_url: str | None) -> dict[str, Any]:
    return {"mcp": {name: {"type": "remote", "url": relay_url + "/mcp/" + name,
                           "enabled": True, "oauth": False}
                    for name in MCPRelay.UPSTREAMS}} if relay_url is not None else {}

def reset_mcp_relay(relay_url: str | None) -> None:
    if relay_url is not None:
        with urllib.request.urlopen(urllib.request.Request(relay_url + "/reset", data=b"", method="POST"), timeout=10):
            pass
