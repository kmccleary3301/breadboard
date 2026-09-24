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
def test_forwarded_framing_preserves_body_verbatim(
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
    forwarded_body = base64.b64decode(forwarded["body_b64"], validate=True)
    assert forwarded_body == original_body
    assert all(name.casefold() != "transfer-encoding" for name, _ in forwarded["headers"])
    content_lengths = [
        value for name, value in forwarded["headers"] if name.casefold() == "content-length"
    ]
    assert content_lengths == [str(len(original_body))]
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


def test_worker_built_llm_select_chat_options_keeps_temperature_and_transport_is_verbatim() -> None:
    import os
    import shutil
    import subprocess

    def _check_sdk() -> None:
        try:
            from openhands.sdk import LLM
            from openhands.sdk.llm.options.chat_options import select_chat_options

            worker_llm = LLM(
                model="openai/gpt-4o-mini",
                api_key="secret",
                base_url="https://api.test/v1",
                num_retries=0,
                timeout=45,
                max_output_tokens=2048,
                temperature=0,
                reasoning_effort="none",
                disable_vision=True,
                drop_params=True,
                capability_overrides={
                    "supports_reasoning_effort": False,
                    "supports_vision": False,
                    "supports_responses_api": False,
                    "supports_sampling_params": True,
                },
            )
            opts = select_chat_options(worker_llm, {}, has_tools=True)
            assert opts.get("temperature") == 0.0, f"Expected 0.0, got {opts.get('temperature')}"
            return
        except Exception:
            pass

        py312 = (
            shutil.which("python3.12")
            or "/opt/breadboard-native-tools/python/bin/python3.12"
            or "/Users/kylemccleary/.local/share/uv/python/cpython-3.12-macos-aarch64-none/bin/python3.12"
        )
        env = dict(os.environ)
        archive_pkg = "/Users/kylemccleary/.cache/uv/archive-v0/EmOGkXXkN6m3sPSJ/lib/python3.12/site-packages"
        if os.path.isdir(archive_pkg):
            env["PYTHONPATH"] = archive_pkg
        env["OPENHANDS_SUPPRESS_BANNER"] = "1"
        code = """
import os, sys
from openhands.sdk import LLM
from openhands.sdk.llm.options.chat_options import select_chat_options

worker_llm = LLM(
    model="openai/gpt-4o-mini",
    api_key="secret",
    base_url="https://api.test/v1",
    num_retries=0,
    timeout=45,
    max_output_tokens=2048,
    temperature=0,
    reasoning_effort="none",
    disable_vision=True,
    drop_params=True,
    capability_overrides={
        "supports_reasoning_effort": False,
        "supports_vision": False,
        "supports_responses_api": False,
        "supports_sampling_params": True,
    },
)
opts = select_chat_options(worker_llm, {}, has_tools=True)
assert opts.get("temperature") == 0.0, f"Expected 0.0, got {opts.get('temperature')}"
print("OK")
"""
        res = subprocess.run([py312, "-c", code], env=env, capture_output=True, text=True, check=True)
        assert res.stdout.strip() == "OK"

    _check_sdk()

    # 2. _IPCTransport forwards the SDK body byte-for-byte without rewriting
    channel = _Channel()
    transport = _IPCTransport(channel, "credential", lambda _request: {})
    sdk_body = b'{"messages":[{"content":"hi","role":"user"}],"model":"openai/gpt-4o-mini","temperature":0.0}'
    request = httpx.Request(
        "POST",
        "https://provider.test/v1/chat/completions",
        headers=[("Authorization", "Bearer credential")],
        content=sdk_body,
    )
    transport.handle_request(request)
    assert channel.response is not None
    forwarded = channel.response["http_request"]
    assert base64.b64decode(forwarded["body_b64"], validate=True) == sdk_body
