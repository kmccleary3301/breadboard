from __future__ import annotations

import json
from pathlib import Path

from breadboard.vsock_protocol import (
    REPL_UNSUPPORTED_ERROR,
    REPL_VSOCK_PROTOCOL_VERSION,
    build_envelope_repl_request,
    build_hello_request,
    build_legacy_repl_request,
    build_repl_request,
    classify_repl_response,
)


def test_build_repl_request_includes_protocol() -> None:
    payload = build_repl_request("#check Nat", 12.5)
    assert payload["mode"] == "repl"
    assert payload["command"] == "#check Nat"
    assert payload["timeout"] == 12.5
    assert payload["protocol"] == REPL_VSOCK_PROTOCOL_VERSION


def test_build_envelope_repl_request() -> None:
    payload = build_envelope_repl_request("#check Nat", 9.0)
    assert payload["type"] == "repl"
    assert payload["version"] == REPL_VSOCK_PROTOCOL_VERSION
    assert payload["payload"]["command"] == "#check Nat"
    assert payload["payload"]["timeout"] == 9.0


def test_build_hello_request() -> None:
    payload = build_hello_request()
    assert payload["type"] == "hello"
    assert payload["version"] == REPL_VSOCK_PROTOCOL_VERSION


def test_hello_fixture_roundtrip() -> None:
    fixture = _load_fixture("hello.json")
    payload = build_hello_request(fixture.get("capabilities"))
    assert payload["type"] == fixture["type"]
    assert payload["version"] == fixture["version"]
    assert payload.get("capabilities") == fixture.get("capabilities")


def test_hello_ack_fixture_shape() -> None:
    fixture = _load_fixture("hello_ack.json")
    assert fixture["type"] == "hello_ack"
    assert fixture["version"] == REPL_VSOCK_PROTOCOL_VERSION
    assert isinstance(fixture.get("capabilities"), dict)
    assert "repl" in fixture["capabilities"]


def test_build_legacy_repl_request() -> None:
    payload = build_legacy_repl_request("#check Nat")
    assert payload == {"cmd": "#check Nat"}


def test_classify_envelope_response() -> None:
    raw = _load_fixture("envelope_response.json")
    kind, payload = classify_repl_response(raw)
    assert kind == "envelope"
    assert payload == raw["response"]


def test_classify_legacy_response() -> None:
    raw = _load_fixture("legacy_response.json")
    kind, payload = classify_repl_response(raw)
    assert kind == "legacy"
    assert payload == raw


def test_classify_unsupported_response() -> None:
    raw = _load_fixture("unsupported_response.json")
    assert raw.get("error_code") == REPL_UNSUPPORTED_ERROR
    kind, payload = classify_repl_response(raw)
    assert kind == "unsupported"
    assert payload is None


def test_classify_envelope_error_response() -> None:
    raw = _load_fixture("envelope_error.json")
    kind, payload = classify_repl_response(raw)
    assert kind == "unsupported"
    assert payload is None


def test_classify_repl_response_envelope() -> None:
    raw = _load_fixture("repl_response.json")
    kind, payload = classify_repl_response(raw)
    assert kind == "envelope"
    assert payload == raw["response"]


def test_classify_command_agent_response() -> None:
    raw = {"exit": 0, "stdout": "", "stderr": ""}
    kind, payload = classify_repl_response(raw)
    assert kind == "command_agent"
    assert payload is None


def _load_fixture(name: str) -> dict:
    root = Path(__file__).resolve().parents[1]
    path = root / "tests" / "fixtures" / "vsock_protocol" / name
    return json.loads(path.read_text(encoding="utf-8"))
