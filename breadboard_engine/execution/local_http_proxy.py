"""Owned loopback HTTP transport shared by stock protocol interceptors."""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import threading


class LocalHTTPProxy(ThreadingHTTPServer):
    """Join the listener and every request after protocol owners cancel waits."""

    daemon_threads = False
    block_on_close = True

    def __init__(self, handler):
        super().__init__(("127.0.0.1", 0), handler)
        self.listener = threading.Thread(target=self.serve_forever, daemon=True)
        self._close_lock = threading.Lock()
        self._closed = False

    def start(self):
        self.listener.start()

    def close(self):
        with self._close_lock:
            if self._closed:
                return
            self.shutdown()
            self.server_close()
            self.listener.join(timeout=5)
            if self.listener.is_alive():
                raise RuntimeError("Stock HTTP listener did not stop")
            self._closed = True


def send_payload(handler, status: int, headers: dict, payload: bytes):
    """Write buffered provider bytes without hop-by-hop or stale length headers."""
    handler.send_response(status)
    for key, value in headers.items():
        if key.lower() not in {"content-length", "transfer-encoding", "connection", "content-encoding"}:
            handler.send_header(key, value)
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)
