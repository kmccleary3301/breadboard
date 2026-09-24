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


def test_worker_built_llm_uses_sealed_config_and_mutating_sampling_drops_temperature(tmp_path: Path) -> None:
    import json
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    py312 = (
        shutil.which("python3.12")
        or "/opt/breadboard-native-tools/python/bin/python3.12"
        or "/Users/kylemccleary/.local/share/uv/python/cpython-3.12-macos-aarch64-none/bin/python3.12"
    )
    if not Path(py312).is_file():
        pytest.skip("python3.12 not found")

    env = dict(os.environ)
    repo_root = str(Path(__file__).resolve().parents[3])
    uv_pkg = "/Users/kylemccleary/.cache/uv/archive-v0/EmOGkXXkN6m3sPSJ/lib/python3.12/site-packages"
    pythonpaths = [repo_root]
    if os.path.isdir(uv_pkg):
        pythonpaths.append(uv_pkg)
    for p in sys.path:
        if "site-packages" in p and p not in pythonpaths:
            pythonpaths.append(p)
    env["PYTHONPATH"] = ":".join(pythonpaths)
    env["OPENHANDS_SUPPRESS_BANNER"] = "1"

    worker_test_code = """
import os, sys, json, base64, pathlib
from breadboard.rl.harness.openhands_worker import OpenHandsActor

class SingleRequestChannel:
    def __init__(self):
        self.response = None
    def respond(self, value):
        self.response = value
    def receive(self):
        return {
            "operation": "provider_response",
            "payload": {
                "status_code": 200,
                "headers": [["content-type", "application/json"]],
                "body_b64": base64.b64encode(b'{"choices":[{"index":0,"message":{"role":"assistant","content":"done"},"finish_reason":"stop"}]}').decode(),
            },
        }

workspace = sys.argv[1]
scratch = sys.argv[2]
config_path = sys.argv[3] if len(sys.argv) > 3 else None

channel = SingleRequestChannel()
actor = OpenHandsActor(channel)
payload = {
    "task": "respond with done",
    "model_config": {
        "model_name": "openai/gpt-4o-mini",
        "model_canonical_name": None,
        "max_input_tokens": 131072,
        "base_url": "http://127.0.0.1:1234/v1",
    },
    "workspace": workspace,
    "scratch": scratch,
    "max_iteration_per_run": 2,
}
if config_path:
    payload["native_config_path"] = config_path

actor.dispatch("initialize", payload)
actor.dispatch("sample", {})
actor.close()

assert channel.response is not None, "Worker never issued HTTP request"
body = json.loads(base64.b64decode(channel.response["http_request"]["body_b64"]))
print(json.dumps({"has_temperature": "temperature" in body, "temperature": body.get("temperature")}))
"""
    # 1. Sealed config: supports_sampling_params is True, so temperature is present
    ws1 = str(tmp_path / "ws1")
    sc1 = str(tmp_path / "sc1")
    Path(ws1).mkdir(parents=True)
    Path(sc1).mkdir(parents=True)
    res1 = subprocess.run([py312, "-c", worker_test_code, ws1, sc1], env=env, capture_output=True, text=True, check=True)
    out1 = json.loads(res1.stdout.strip().splitlines()[-1])
    assert out1["has_temperature"] is True
    assert out1["temperature"] == 0

    # 2. Mutated config: copy sealed config and set supports_sampling_params to False
    sealed_config_path = Path(repo_root) / "config/e4_targets/openhands_sdk/1.47.0/native-config.json"
    mutated_config = json.loads(sealed_config_path.read_text(encoding="utf-8"))
    mutated_config["model"]["capability_overrides"]["supports_sampling_params"] = False
    mutated_path = tmp_path / "mutated-native-config.json"
    mutated_path.write_text(json.dumps(mutated_config), encoding="utf-8")

    ws2 = str(tmp_path / "ws2")
    sc2 = str(tmp_path / "sc2")
    Path(ws2).mkdir(parents=True)
    Path(sc2).mkdir(parents=True)
    res2 = subprocess.run([py312, "-c", worker_test_code, ws2, sc2, str(mutated_path)], env=env, capture_output=True, text=True, check=True)
    out2 = json.loads(res2.stdout.strip().splitlines()[-1])
    assert out2["has_temperature"] is False, f"Expected temperature to be dropped with supports_sampling_params=False, got {out2}"


def test_ipctransport_forwards_sdk_body_verbatim() -> None:
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
