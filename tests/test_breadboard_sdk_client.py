from __future__ import annotations

import json

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

from breadboard_sdk.types import SessionEvent


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

        lines = ["id: 1", f"data: {json.dumps(expected)}", ""]

        @classmethod
        def iter_lines(cls, *, decode_unicode: bool) -> list[str]:
            assert decode_unicode is True
            return cls.lines

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
    _StreamResponse.lines = [
        "id: 1",
        f"data: {json.dumps(expected)}",
        "",
        "id: 2",
        "",
    ]
    cursor_stream = client.events_session(
        "session id", resume_token=0, limit=2, follow=False
    )
    assert next(cursor_stream) == expected
    with pytest.raises(StopIteration) as stopped:
        next(cursor_stream)
    assert stopped.value.value == 2
    for truncated in (
        ["id: 2"],
        ["id: 2", f"data: {json.dumps(expected)}"],
    ):
        _StreamResponse.lines = truncated
        with pytest.raises(ValueError, match="incomplete SSE frame"):
            list(
                client.events_session(
                    "session id",
                    resume_token=0,
                    limit=2,
                    follow=False,
                )
            )
    _StreamResponse.lines = [f"data: {json.dumps(expected)}", ""]
    with pytest.raises(ValueError, match="missing an SSE id"):
        list(
            client.events_session(
                "session id",
                resume_token=0,
                limit=2,
                follow=False,
            )
        )
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
    annotation_payload = {
        "annotation_id": "annotation-1",
        "message_id": "message-1",
        "trajectory_id": "trajectory-1",
        "label": "accepted",
        "author": "operator",
        "generation": "generation-1",
    }
    annotation = {
        **expected,
        "visibility": {
            "model_visible": False,
            "provider_visible": False,
            "host_visible": True,
            "redaction_state": "none",
        },
        "kind": "annotation",
        "payload": annotation_payload,
        "payload_schema_version": "bb.payload.product_session.annotation.v1",
    }
    assert (
        client_module._session_event(json.dumps(annotation), "session id", "1")[
            "payload"
        ]
        == annotation_payload
    )
    lineage: SessionEventLineage = {
        "parent_session_id": "parent-session",
        "root_session_id": "root-session",
        "parent_work_item_id": "parent-work-item",
        "child_work_item_id": "child-work-item",
    }
    lineage_event = {
        **expected,
        "work_item_id": "child-work-item",
        "parent_work_item_id": "parent-work-item",
        "payload": {**expected["payload"], "lineage": lineage},
    }
    decoded_lineage = client_module._session_event(
        json.dumps(lineage_event), "session id", "1"
    )
    contradictory_lineage = {**lineage_event, "work_item_id": "other-child"}
    with pytest.raises(ValueError, match="lineage correlations"):
        client_module._session_event(
            json.dumps(contradictory_lineage), "session id", "1"
        )
    assistant = {
        **expected,
        "kind": "assistant_message",
        "payload": {
            "text": "answer",
            "message_id": "message-1",
            "trajectory_id": "trajectory-1",
        },
        "payload_schema_version": "bb.payload.message.assistant.v1",
    }
    assert client_module._session_event(
        json.dumps(assistant), "session id", "1"
    )["payload"] == assistant["payload"]
    partial_assistant_identity = {
        **expected,
        "kind": "assistant_message",
        "payload": {"text": "answer", "message_id": "message-1"},
        "payload_schema_version": "bb.payload.message.assistant.v1",
    }
    with pytest.raises(ValueError, match="assistant identity"):
        client_module._session_event(
            json.dumps(partial_assistant_identity), "session id", "1"
        )


def test_snapshot_reader_retains_annotations_after_session_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from breadboard.product.harness.lock import EffectiveHarnessLock
    from breadboard.product.runtime.events import AnnotationRecord, Session
    from breadboard.product.runtime.public_event_projection import public_session_events

    generation = "sha256:" + "a" * 64
    session = Session.start(
        EffectiveHarnessLock._from_record({"graph_hash": generation}),
        "label archive",
        session_id="archive",
    )
    session.assistant_message("candidate", message_id="message-a", trajectory_id="trajectory-a")
    session.complete("done")
    session.annotate(AnnotationRecord("label-a", "message-a", "trajectory-a", "preferred", "reviewer", generation))
    rows = public_session_events(session.events)

    class StreamResponse:
        ok = True
        closed = False

        def close(self) -> None:
            self.closed = True

        def iter_lines(self, *, decode_unicode: bool):
            for row in rows:
                if self.closed:
                    return
                yield f"id: {row['seq']}"
                yield f"data: {json.dumps(row)}"
                yield ""

    monkeypatch.setattr(client_module.requests, "request", lambda **kwargs: StreamResponse())
    events = list(BreadBoardClient("http://127.0.0.1").events_session("archive", follow=False))
    assert [event["kind"] for event in events] == [
        "session.started", "assistant_message", "session.completed", "annotation",
    ]
    assert events[-1]["payload"]["annotation_id"] == "label-a"


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
