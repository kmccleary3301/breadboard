#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Tuple


HTML_FIXTURE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <title>OpenCode WebFetch Fixture</title>
    <link rel="stylesheet" href="/style.css" />
    <style>.hidden{display:none}</style>
    <script>ignored_script()</script>
  </head>
  <body>
    <h1>Fixture Heading</h1>
    <p>Hello <em>world</em>.</p>
    <hr />
    <ul>
      <li>one</li>
      <li>two</li>
    </ul>
    <noscript>ignored_noscript</noscript>
  </body>
</html>
"""

TEXT_FIXTURE = "Plain text fixture.\nSecond line.\n"
MD_FIXTURE = "# Markdown fixture\n\nHello markdown.\n"
LARGE_BYTES = (5 * 1024 * 1024) + 1


def _split_path(raw: str) -> str:
    return (raw or "").split("?", 1)[0].split("#", 1)[0]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status: int, content_type: str, body: bytes, *, extra_headers: Tuple[Tuple[str, str], ...] = ()) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra_headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = _split_path(self.path)
        if path == "/text.txt":
            self._send(200, "text/plain", TEXT_FIXTURE.encode("utf-8"))
            return
        if path == "/md.md":
            self._send(200, "text/markdown", MD_FIXTURE.encode("utf-8"))
            return
        if path == "/html.html":
            self._send(200, "text/html", HTML_FIXTURE.encode("utf-8"))
            return
        if path == "/status/404":
            self._send(404, "text/plain", b"not found\n")
            return
        if path == "/large":
            body = b"a" * LARGE_BYTES
            self._send(200, "text/plain", body)
            return
        if path == "/slow":
            time.sleep(0.25)
            self._send(200, "text/plain", b"slow ok\n")
            return
        self._send(404, "text/plain", b"unknown path\n")

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministic fixture server for OpenCode webfetch parity.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    host, port = server.server_address
    print(port, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

