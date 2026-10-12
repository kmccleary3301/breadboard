"""Loopback HTTP capture for real provider SDK wire contracts."""
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest


@dataclass(frozen=True)
class WireRequest:
    content: bytes
    path: str
    headers: dict[str, str]


@pytest.fixture
def sdk_wire_server():
    servers = []
    errors = []

    def start(reply):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = WireRequest(
                    self.rfile.read(int(self.headers["Content-Length"])),
                    self.path,
                    dict(self.headers),
                )
                try:
                    status, content_type, content = reply(request)
                except BaseException as exc:
                    errors.append(exc)
                    status, content_type, content = 500, "text/plain", b"handler failed"
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        servers.append((server, thread))
        return f"http://127.0.0.1:{server.server_address[1]}"

    yield start
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    if errors:
        raise errors[0]
