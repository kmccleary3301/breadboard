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
        "schema_version": "bb.kernel_event.v2",
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
        "payload": {},
        "payload_schema_version": "bb.payload.session.started.v1",
    }

    class _StreamResponse:
        ok = True
        status_code = 200
        text = ""

        @staticmethod
        def iter_lines(*, decode_unicode: bool) -> list[str]:
            assert decode_unicode is True
            return ["id: 1", f"data: {json.dumps(expected)}", ""]

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
