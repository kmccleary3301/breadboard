from __future__ import annotations

from typing import Any

import pytest

from breadboard_sdk.generated.public_bindings import (
    PUBLIC_BINDINGS_BY_OPERATION_ID,
    PUBLIC_OPERATION_BINDINGS,
)

import breadboard_sdk
import breadboard_sdk.client as client_module
from breadboard_sdk.client import BreadBoardClient
from breadboard_sdk.compat import CompatibilityBreadboardClient


class _JsonResponse:
    ok = True
    status_code = 200
    content = b"{}"
    headers = {"content-type": "application/json"}
    text = "{}"

    def __init__(self, payload: Any | None = None) -> None:
        self._payload = {} if payload is None else payload

    def json(self) -> Any:
        return self._payload


def test_candidate_product_bindings_exist_on_python_sdk() -> None:
    bindings = tuple(PUBLIC_OPERATION_BINDINGS)
    methods = {
        binding.python_method for binding in bindings if binding.status == "candidate"
    }
    public_methods = {
        name
        for name, value in vars(BreadBoardClient).items()
        if callable(value) and not name.startswith("_")
    }
    assert len(bindings) == len(PUBLIC_BINDINGS_BY_OPERATION_ID) == 26
    assert public_methods == methods
    assert {binding.operation_id for binding in bindings} == set(
        PUBLIC_BINDINGS_BY_OPERATION_ID
    )
    assert all(
        PUBLIC_BINDINGS_BY_OPERATION_ID[binding.operation_id] is binding
        for binding in bindings
    )


def test_legacy_python_client_surface_requires_explicit_compatibility_import() -> None:
    assert not hasattr(breadboard_sdk, "BreadboardClient")
    assert hasattr(CompatibilityBreadboardClient, "health")
    assert not hasattr(BreadBoardClient, "health")


def test_candidate_python_sdk_preserves_public_result_and_idempotency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = {
        "schema_version": "bb.cli.result.v1",
        "ok": True,
        "status": "ok",
        "command": [],
        "record_refs": [],
        "hashes": {},
        "stage_outcomes": [],
        "warnings": [],
        "next_actions": [],
        "error": None,
        "exit_code": 0,
        "data": {},
    }
    requests: list[dict[str, Any]] = []

    def fake_request(**kwargs: Any) -> _JsonResponse:
        requests.append(kwargs)
        return _JsonResponse(result)

    monkeypatch.setattr(client_module.requests, "request", fake_request)
    client = BreadBoardClient(base_url="https://breadboard.test/")
    assert (
        client.start_session(
            {"lock_id": "lock.json", "task": "run"}, idempotency_key="start-key"
        )
        == result
    )
    assert client.get_artifact("sha256:abc") == result
    start_binding = PUBLIC_BINDINGS_BY_OPERATION_ID["session.start"]
    artifact_binding = PUBLIC_BINDINGS_BY_OPERATION_ID["artifact.get"]
    assert requests[0]["method"] == start_binding.http_method
    assert requests[0]["headers"]["Idempotency-Key"] == "start-key"
    assert requests[1]["method"] == artifact_binding.http_method
    assert requests[1]["url"] == (
        "https://breadboard.test/"
        + artifact_binding.path.format(artifact_id="sha256%3Aabc").lstrip("/")
    )


def test_candidate_python_sdk_streams_generated_session_events_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _StreamResponse:
        ok = True
        status_code = 200
        text = ""

        @staticmethod
        def iter_lines(*, decode_unicode: bool) -> list[str]:
            assert decode_unicode is True
            return ['data: {"id": "evt-1", "type": "turn"}', ""]

    requests: list[dict[str, Any]] = []

    def fake_request(**kwargs: Any) -> _StreamResponse:
        requests.append(kwargs)
        return _StreamResponse()

    monkeypatch.setattr(client_module.requests, "request", fake_request)
    client = BreadBoardClient(base_url="https://breadboard.test/", auth_token="secret")
    events = list(
        client.events_session("session id", resume_token=3, last_event_id=7, limit=2)
    )

    binding = PUBLIC_BINDINGS_BY_OPERATION_ID["session.events"]
    assert events == [{"id": "evt-1", "type": "turn"}]
    assert requests == [
        {
            "method": binding.http_method,
            "url": (
                "https://breadboard.test/"
                + binding.path.format(session_id="session%20id").lstrip("/")
                + "?resume_token=3&limit=2"
            ),
            "headers": {
                "Authorization": "Bearer secret",
                "Last-Event-ID": "7",
            },
            "stream": True,
            "timeout": 30.0,
        }
    ]


def test_compatibility_create_session_omits_only_an_absent_config_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[tuple[str, str, dict[str, Any]]] = []

    def fake_request(
        _self: CompatibilityBreadboardClient,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        requests.append((method, path, kwargs))
        return {}

    monkeypatch.setattr(CompatibilityBreadboardClient, "_request", fake_request)
    client = CompatibilityBreadboardClient()

    client.create_session(workspace="/workspace")
    client.create_session(config_path="/custom.yaml", task="run")

    assert requests == [
        (
            "POST",
            "/v1/sessions",
            {"body": {"task": "", "stream": True, "workspace": "/workspace"}},
        ),
        (
            "POST",
            "/v1/sessions",
            {
                "body": {
                    "config_path": "/custom.yaml",
                    "task": "run",
                    "stream": True,
                }
            },
        ),
    ]
