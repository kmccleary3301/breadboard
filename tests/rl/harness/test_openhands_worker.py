from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

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


@pytest.mark.parametrize(
    ("framing_headers", "expected_header_names"),
    [
        (
            [("Transfer-Encoding", "chunked"), ("X-Test", "preserve")],
            ["X-Test", "Content-Length"],
        ),
        (
            [
                ("Transfer-Encoding", "chunked"),
                ("content-length", "stale"),
                ("X-Test", "preserve"),
            ],
            ["content-length", "X-Test"],
        ),
        (
            [
                ("Content-Length", "stale"),
                ("content-length", "duplicate"),
                ("X-Test", "preserve"),
            ],
            ["Content-Length", "X-Test"],
        ),
    ],
)
def test_temperature_rewrite_canonicalizes_forwarded_framing(
    framing_headers: list[tuple[str, str]], expected_header_names: list[str]
) -> None:
    document = {"model": "fixture-model", "messages": [{"role": "user", "content": "hello"}]}
    original_body = json.dumps(document, separators=(",", ":")).encode("utf-8")
    channel = _Channel()
    transport = _IPCTransport(channel, "credential", lambda _request: {})
    request = httpx.Request(
        "POST",
        "https://provider.test/v1/chat/completions",
        headers=[("Authorization", "Bearer credential"), *framing_headers],
        content=original_body,
    )
    if expected_header_names == ["X-Test", "Content-Length"]:
        request.headers.pop("content-length", None)

    transport.handle_request(request)

    assert channel.response is not None
    forwarded = channel.response["http_request"]
    rewritten_body = base64.b64decode(forwarded["body_b64"], validate=True)
    assert json.loads(rewritten_body)["temperature"] == 0
    assert all(name.casefold() != "transfer-encoding" for name, _ in forwarded["headers"])
    content_lengths = [
        value for name, value in forwarded["headers"] if name.casefold() == "content-length"
    ]
    assert content_lengths == [str(len(rewritten_body))]
    forwarded_names = [
        name for name, _ in forwarded["headers"] if name.casefold() != "host"
    ]
    assert forwarded_names == expected_header_names


def test_forwarded_framing_is_canonical_without_body_rewrite() -> None:
    document = {
        "model": "fixture-model",
        "messages": [{"role": "user", "content": "hello"}],
        "temperature": 0,
    }
    original_body = json.dumps(document, separators=(",", ":")).encode("utf-8")
    channel = _Channel()
    transport = _IPCTransport(channel, "credential", lambda _request: {})
    request = httpx.Request(
        "POST",
        "https://provider.test/v1/chat/completions",
        headers=[
            ("Authorization", "Bearer credential"),
            ("Transfer-Encoding", "chunked"),
            ("Content-Length", "stale"),
            ("content-length", "duplicate"),
            ("X-Test", "preserve"),
        ],
        content=original_body,
    )

    transport.handle_request(request)

    assert channel.response is not None
    forwarded = channel.response["http_request"]
    assert base64.b64decode(forwarded["body_b64"], validate=True) == original_body
    assert all(name.casefold() != "transfer-encoding" for name, _ in forwarded["headers"])
    assert [
        (name, value)
        for name, value in forwarded["headers"]
        if name.casefold() == "content-length"
    ] == [("Content-Length", str(len(original_body)))]
