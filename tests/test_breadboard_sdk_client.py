from __future__ import annotations

import json

from typing import Any, get_type_hints

import pytest

from breadboard_sdk.generated.public_bindings import (
    PUBLIC_BINDINGS_BY_OPERATION_ID,
    PUBLIC_OPERATION_BINDINGS,
)

import breadboard_sdk
import breadboard_sdk.client as client_module
import breadboard_sdk.types as types_module
from breadboard_sdk.client import BreadBoardClient
from breadboard_sdk.compat import CompatibilityBreadboardClient

from breadboard_sdk.types import (
    ArtifactRefV1,
    Problem,
    PublicHarnessCreateRequest,
    PublicHarnessUpdateRequest,
    PublicResult,
    PublicSessionApprovalRequest,
    PublicSessionCancelRequest,
    PublicSessionDecision,
    PublicSessionInputRequest,
    PublicSessionStartRequest,
    SessionEvent,
    StageOutcome,
)


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


@pytest.mark.parametrize(
    "base_url",
    [
        "http://breadboard.test:9099",
        "http://192.0.2.1:9099",
    ],
)
def test_python_sdk_rejects_bearer_token_over_remote_plaintext_http(
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
) -> None:
    requests: list[dict[str, Any]] = []

    def fake_request(**kwargs: Any) -> _JsonResponse:
        requests.append(kwargs)
        return _JsonResponse({})

    monkeypatch.setattr(client_module.requests, "request", fake_request)
    client = BreadBoardClient(base_url=base_url, auth_token="secret-token")

    with pytest.raises(ValueError, match="HTTPS"):
        client.list_session()

    assert requests == []


def test_python_sdk_rejects_bearer_token_over_remote_plaintext_sse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict[str, Any]] = []

    def fake_request(**kwargs: Any) -> _JsonResponse:
        requests.append(kwargs)
        return _JsonResponse({})

    monkeypatch.setattr(client_module.requests, "request", fake_request)
    client = BreadBoardClient(
        base_url="http://breadboard.test:9099",
        auth_token="secret-token",
    )

    with pytest.raises(ValueError, match="HTTPS"):
        next(client.events_session("session-id"))

    assert requests == []


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:9099",
        "http://127.0.0.2:9099",
        "http://[::1]:9099",
        "https://breadboard.test:9099",
    ],
)
def test_python_sdk_allows_bearer_token_over_protected_origins(
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
) -> None:
    requests: list[dict[str, Any]] = []

    def fake_request(**kwargs: Any) -> _JsonResponse:
        requests.append(kwargs)
        return _JsonResponse({"data": {"sessions": []}})

    monkeypatch.setattr(client_module.requests, "request", fake_request)
    client = BreadBoardClient(base_url=base_url, auth_token="secret-token")

    assert client.list_session() == {"data": {"sessions": []}}
    assert requests[0]["headers"]["Authorization"] == "Bearer secret-token"


def test_python_sdk_authored_types_and_client_hints_match_public_contract() -> None:
    assert not hasattr(types_module, "NotRequired")
    assert ArtifactRefV1.__required_keys__ == {
        "schema_version",
        "id",
        "kind",
        "mime",
        "size_bytes",
        "sha256",
        "storage",
        "path",
    }
    assert ArtifactRefV1.__optional_keys__ == {"preview"}
    assert Problem.__required_keys__ == {"error_code", "message"}
    assert Problem.__optional_keys__ == {
        "schema_version",
        "record_refs",
        "failed_stage",
        "hint",
        "next_actions",
    }
    assert StageOutcome.__required_keys__ == {"stage", "status"}
    assert StageOutcome.__optional_keys__ == {"report_ref", "next_action"}
    assert PublicResult.__required_keys__ == {
        "schema_version",
        "ok",
        "status",
        "command",
        "record_refs",
        "hashes",
        "stage_outcomes",
        "warnings",
        "next_actions",
        "error",
        "exit_code",
        "data",
    }
    assert PublicResult.__optional_keys__ == set()
    assert PublicHarnessCreateRequest.__required_keys__ == set()
    assert PublicHarnessCreateRequest.__optional_keys__ == {"directory"}
    assert PublicHarnessUpdateRequest.__required_keys__ == {"definition"}
    assert PublicHarnessUpdateRequest.__optional_keys__ == set()
    assert PublicSessionStartRequest.__required_keys__ == {"lock_id", "task"}
    assert PublicSessionStartRequest.__optional_keys__ == {"session_id"}
    assert PublicSessionInputRequest.__required_keys__ == {"content"}
    assert PublicSessionInputRequest.__optional_keys__ == set()
    assert PublicSessionApprovalRequest.__required_keys__ == {
        "request_id",
        "decision",
    }
    assert PublicSessionApprovalRequest.__optional_keys__ == set()
    assert PublicSessionCancelRequest.__required_keys__ == set()
    assert PublicSessionCancelRequest.__optional_keys__ == {"reason"}
    assert SessionEvent.__required_keys__ == {
        "schema_version",
        "event_id",
        "seq",
        "timestamp",
        "work_item_id",
        "parent_work_item_id",
        "attempt_id",
        "session_id",
        "span_id",
        "visibility",
        "kind",
        "payload",
        "payload_schema_version",
    }
    assert SessionEvent.__optional_keys__ == set()

    json_methods = (
        "describe_system",
        "health_system",
        "schemas_system",
        "create_harness",
        "list_harness",
        "get_harness",
        "update_harness",
        "validate_harness",
        "explain_harness",
        "lock_harness",
        "get_harness_lock",
        "list_integration",
        "get_integration",
        "probe_integration",
        "list_artifact",
        "get_artifact",
        "verify_artifact",
        "start_session",
        "list_session",
        "get_session",
        "send_input_session",
        "approve_session",
        "resume_session",
        "cancel_session",
        "artifacts_session",
    )
    assert len(json_methods) == 25
    assert all(
        get_type_hints(getattr(BreadBoardClient, name))["return"] is PublicResult
        for name in json_methods
    )
    start_hints = get_type_hints(BreadBoardClient.start_session)
    assert start_hints["payload"] is PublicSessionStartRequest
    approval_hints = get_type_hints(BreadBoardClient.approve_session)
    assert approval_hints["decision"] is PublicSessionDecision


def test_candidate_python_sdk_streams_generated_session_events_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected: SessionEvent = {
        "schema_version": "bb.public_session_event.v1",
        "event_id": "session:session id:1",
        "seq": 1,
        "timestamp": "2026-08-31T10:00:01Z",
        "work_item_id": None,
        "parent_work_item_id": None,
        "attempt_id": None,
        "session_id": "session id",
        "span_id": None,
        "visibility": {
            "model_visible": True,
            "provider_visible": True,
            "host_visible": True,
            "redaction_state": "none",
        },
        "kind": "session.started",
        "payload": {
            "effective_lock_hash": "sha256:" + "a" * 64,
            "task_hash": "sha256:" + "b" * 64,
        },
        "payload_schema_version": "bb.payload.product_session.lifecycle.v1",
    }

    class _StreamResponse:
        ok = True
        status_code = 200
        text = ""
        closed = False

        def close(self) -> None:
            self.closed = True

        @staticmethod
        def iter_lines(*, decode_unicode: bool) -> list[str]:
            assert decode_unicode is True
            return ["id: 1", f"data: {json.dumps(expected)}", ""]

    response = _StreamResponse()

    requests: list[dict[str, Any]] = []

    def fake_request(**kwargs: Any) -> _StreamResponse:
        requests.append(kwargs)
        return response

    monkeypatch.setattr(client_module.requests, "request", fake_request)
    client = BreadBoardClient(base_url="https://breadboard.test/", auth_token="secret")
    events = list(
        client.events_session("session id", resume_token=3, last_event_id=7, limit=2)
    )

    binding = PUBLIC_BINDINGS_BY_OPERATION_ID["session.events"]
    assert events == [expected]
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
    assert response.closed is True
    snapshot = list(
        client.events_session("session id", resume_token=3, limit=2, follow=False)
    )
    assert snapshot == [expected]
    assert requests[-1]["url"].endswith("?resume_token=3&limit=2&follow=false")
    forged = {**expected, "kind": "session.completed", "payload": {}}
    with pytest.raises(ValueError, match="lifecycle payload fields"):
        client_module._session_event(json.dumps(forged), "session id", "1")
    nanosecond_timestamp = {
        **expected,
        "timestamp": "2026-08-31T10:00:01.123456789Z",
    }
    assert (
        client_module._session_event(
            json.dumps(nanosecond_timestamp),
            "session id",
            "1",
        )["timestamp"]
        == nanosecond_timestamp["timestamp"]
    )
    for valid_timestamp in (
        "2016-12-31T23:59:60Z",
        "2016-12-31T15:59:60-08:00",
        "2026-08-31t10:00:01.123z",
    ):
        valid_timestamp_event = {**expected, "timestamp": valid_timestamp}
        assert (
            client_module._session_event(
                json.dumps(valid_timestamp_event),
                "session id",
                "1",
            )["timestamp"]
            == valid_timestamp
        )
    invalid_leap_second = {
        **expected,
        "timestamp": "2016-11-30T23:59:60Z",
    }
    with pytest.raises(ValueError, match="timestamp"):
        client_module._session_event(
            json.dumps(invalid_leap_second),
            "session id",
            "1",
        )
    overflow_leap_second = {
        **expected,
        "timestamp": "0001-01-01T00:00:60+23:59",
    }
    with pytest.raises(ValueError, match="timestamp"):
        client_module._session_event(
            json.dumps(overflow_leap_second),
            "session id",
            "1",
        )
    bad_timestamp = {**expected, "timestamp": "not-a-time"}
    with pytest.raises(ValueError, match="timestamp"):
        client_module._session_event(json.dumps(bad_timestamp), "session id", "1")


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
            "/v1/internal/sessions",
            {"body": {"task": "", "stream": True, "workspace": "/workspace"}},
        ),
        (
            "POST",
            "/v1/internal/sessions",
            {
                "body": {
                    "config_path": "/custom.yaml",
                    "task": "run",
                    "stream": True,
                }
            },
        ),
    ]
