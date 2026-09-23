from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from breadboard.rl.harness.openhands_worker import _IPCTransport


class _Channel:
    def __init__(self) -> None:
        self.response: dict[str, Any] | None = None

    def respond(self, value: dict[str, Any]) -> None:
        self.response = value

    def receive(self) -> dict[str, Any]:
        return {
            "operation": "provider_response",
            "payload": {
                "status_code": 200,
                "headers": [["content-type", "application/json"]],
                "body_b64": base64.b64encode(b'{"ok":true}').decode("ascii"),
            },
        }


def test_temperature_rewrite_updates_forwarded_content_length() -> None:
    document = {"model": "fixture-model", "messages": [{"role": "user", "content": "hello"}]}
    original_body = json.dumps(document, separators=(",", ":")).encode("utf-8")
    channel = _Channel()
    transport = _IPCTransport(channel, "credential", lambda _request: {})
    request = httpx.Request(
        "POST",
        "https://provider.test/v1/chat/completions",
        headers=[
            ("Authorization", "Bearer credential"),
            ("Content-Length", str(len(original_body))),
            ("content-length", str(len(original_body))),
            ("X-Test", "preserve"),
        ],
        content=original_body,
    )

    transport.handle_request(request)

    assert channel.response is not None
    forwarded = channel.response["http_request"]
    rewritten_body = base64.b64decode(forwarded["body_b64"], validate=True)
    assert json.loads(rewritten_body)["temperature"] == 0
    content_lengths = [
        value for name, value in forwarded["headers"] if name.casefold() == "content-length"
    ]
    assert content_lengths == [str(len(rewritten_body))]
    header_names = [name for name, _ in forwarded["headers"]]
    content_length_index = header_names.index("Content-Length")
    assert content_length_index < header_names.index("X-Test")
    assert forwarded["headers"][content_length_index] == [
        "Content-Length",
        str(len(rewritten_body)),
    ]
