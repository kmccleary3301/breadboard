from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest

import breadboard_sdk
import breadboard_sdk.client as client_module
from breadboard.product.cli.main import main
from breadboard.product.operations.model import OperationResult


@pytest.fixture(autouse=True)
def _isolated_api_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BREADBOARD_API_TOKEN", raising=False)


def _result(command: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "bb.cli.result.v1",
        "ok": True,
        "status": "ok",
        "command": command,
        "record_refs": [],
        "hashes": {},
        "stage_outcomes": [],
        "warnings": [],
        "next_actions": [],
        "error": None,
        "exit_code": 0,
        "data": {},
    }


class _Client:
    calls: ClassVar[list[tuple[Any, ...]]] = []

    def __init__(
        self,
        base_url: str,
        *,
        auth_token: str | None = None,
        timeout_s: float,
    ) -> None:
        self.calls.append(("client", base_url, auth_token, timeout_s))

    def list_session(self) -> dict[str, Any]:
        self.calls.append(("list",))
        return _result(["session", "list"])

    def get_session(self, session_id: str) -> dict[str, Any]:
        self.calls.append(("get", session_id))
        result = _result(["session", "get"])
        result["data"] = {"session": {"event_count": 1}}
        result["stage_outcomes"] = [
            {
                "stage": "session.get",
                "status": "passed",
                "report_ref": None,
            }
        ]
        return result

    def send_input_session(
        self,
        session_id: str,
        content: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("send-input", session_id, content, idempotency_key))
        return _result(["session", "send-input"])

    def approve_session(
        self,
        session_id: str,
        request_id: str,
        decision: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            ("approve", session_id, request_id, decision, idempotency_key)
        )
        return _result(["session", "approve"])

    def resume_session(
        self, session_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(("resume", session_id, idempotency_key))
        return _result(["session", "resume"])

    def cancel_session(
        self,
        session_id: str,
        reason: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("cancel", session_id, reason, idempotency_key))
        return _result(["session", "cancel"])

    def artifacts_session(self, session_id: str) -> dict[str, Any]:
        self.calls.append(("artifacts", session_id))
        return _result(["session", "artifacts"])

    def events_session(
        self,
        session_id: str,
        *,
        resume_token: int | None = None,
        limit: int = 256,
        follow: bool = True,
    ) -> Iterator[dict[str, Any]]:
        self.calls.append(("events", session_id, follow))
        yield {
            "schema_version": "bb.public_session_event.v1",
            "event_id": "session:s:1",
            "seq": 1,
            "timestamp": "2026-08-31T10:00:01Z",
            "work_item_id": None,
            "parent_work_item_id": None,
            "attempt_id": None,
            "session_id": session_id,
            "span_id": None,
            "visibility": {
                "model_visible": True,
                "provider_visible": True,
                "host_visible": True,
                "redaction_state": "none",
            },
            "kind": "session.completed",
            "payload": {"outcome": "completed", "summary": "fixture"},
            "payload_schema_version": "bb.payload.product_session.lifecycle.v1",
        }


def test_session_cli_routes_every_public_operation_through_selected_server(
    monkeypatch, tmp_path, capsys
) -> None:
    _Client.calls = []
    monkeypatch.setattr(breadboard_sdk, "BreadBoardClient", _Client)
    monkeypatch.setenv("BREADBOARD_API_TOKEN", "cli-auth-token")
    monkeypatch.setenv("BREADBOARD_LEGACY_ROUTES", "1")
    invocations = [
        (["list"], ("list",)),
        (["get", "s"], ("get", "s")),
        (["show", "s"], ("get", "s")),
        (
            ["send-input", "s", "continue", "--idempotency-key", "input-key"],
            ("send-input", "s", "continue", "input-key"),
        ),
        (
            [
                "approve",
                "s",
                "request",
                "deny",
                "--idempotency-key",
                "approval-key",
            ],
            ("approve", "s", "request", "deny", "approval-key"),
        ),
        (
            ["resume", "s", "--idempotency-key", "resume-key"],
            ("resume", "s", "resume-key"),
        ),
        (
            [
                "cancel",
                "s",
                "--reason",
                "done",
                "--idempotency-key",
                "cancel-key",
            ],
            ("cancel", "s", "done", "cancel-key"),
        ),
        (["artifacts", "s"], ("artifacts", "s")),
        (["events", "s"], ("events", "s", False)),
    ]

    for arguments, expected_call in invocations:
        assert (
            main(
                [
                    "--json",
                    "session",
                    "--workspace",
                    str(tmp_path),
                    "--server",
                    "http://breadboard.test",
                    *arguments,
                ]
            )
            == 0
        )
        output = json.loads(capsys.readouterr().out)
        assert output["ok"] is True
        assert _Client.calls[-1] == expected_call

        assert output["command"] == ["session", arguments[0]]
        if arguments[0] in {"get", "show"}:
            assert output["stage_outcomes"][0]["stage"] == (
                f"session.{arguments[0]}"
            )
    clients = [call for call in _Client.calls if call[0] == "client"]
    assert clients == [
        ("client", "http://breadboard.test", "cli-auth-token", 120)
    ] * len(invocations)


def test_session_cli_rejects_remote_plaintext_bearer_before_sse_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    requests: list[dict[str, Any]] = []
    monkeypatch.setattr(
        client_module.requests,
        "request",
        lambda **kwargs: requests.append(kwargs),
    )
    monkeypatch.setenv("BREADBOARD_API_TOKEN", "cli-auth-token")

    exit_code = main(
        [
            "--json",
            "session",
            "--workspace",
            str(tmp_path),
            "--server",
            "http://breadboard.test",
            "events",
            "session-id",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert exit_code != 0
    assert "requires HTTPS" in output["error"]["message"]
    assert requests == []


def test_session_cli_bounds_complete_remote_event_snapshot_to_initial_count(
    monkeypatch, tmp_path, capsys
) -> None:
    calls: list[tuple[int | None, int, bool]] = []

    class PagingClient:
        def __init__(self, base_url: str, *, timeout_s: float) -> None:
            pass

        @staticmethod
        def get_session(session_id: str) -> dict[str, Any]:
            result = _result(["session", "get"])
            result["data"] = {
                "session": {
                    "session_id": session_id,
                    "event_count": 257,
                }
            }
            return result

        def events_session(
            self,
            session_id: str,
            *,
            resume_token: int | None = None,
            limit: int = 256,
            follow: bool = True,
        ) -> Iterator[dict[str, Any]]:
            calls.append((resume_token, limit, follow))
            if resume_token is None:
                for sequence in range(1, limit + 1):
                    yield {"seq": sequence, "kind": "assistant_message"}
                return
            assert resume_token == 256
            assert limit == 1
            yield {"seq": 257, "kind": "session.completed"}

    monkeypatch.setattr(breadboard_sdk, "BreadBoardClient", PagingClient)
    assert (
        main(
            [
                "--json",
                "session",
                "--workspace",
                str(tmp_path),
                "--server",
                "http://breadboard.test",
                "events",
                "completed-session",
            ]
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)
    assert len(output["data"]["events"]) == 257
    assert calls == [(None, 256, False), (256, 1, False)]


@pytest.mark.parametrize(
    ("status", "exit_code", "error_code"),
    [
        (401, 2, "unauthorized"),
        (404, 3, "path_unavailable"),
        (409, 6, "idempotency_conflict"),
        (422, 2, "invalid_state"),
    ],
)
def test_session_cli_preserves_remote_typed_errors(
    monkeypatch,
    tmp_path,
    capsys,
    status: int,
    exit_code: int,
    error_code: str,
) -> None:
    if status == 401:
        body: object = {
            "error": "unauthorized",
            "detail": "unauthorized",
            "path": None,
        }
    else:
        body = OperationResult.failure(
            ["session", "get"],
            exit_code,
            error_code,
            "typed remote failure",
            "session.get",
        ).as_dict()

    class FailingClient:
        def __init__(self, base_url: str, *, timeout_s: float) -> None:
            pass

        def get_session(self, session_id: str) -> dict[str, Any]:
            raise breadboard_sdk.ApiError("failed", status, body)

    monkeypatch.setattr(breadboard_sdk, "BreadBoardClient", FailingClient)
    assert (
        main(
            [
                "--json",
                "session",
                "--workspace",
                str(tmp_path),
                "--server",
                "http://breadboard.test",
                "get",
                "missing",
            ]
        )
        == exit_code
    )
    output = json.loads(capsys.readouterr().out)
    assert output["exit_code"] == exit_code
    assert output["error"]["error_code"] == error_code
    assert output["error"]["message"] in {"unauthorized", "typed remote failure"}
    if status != 401:
        assert output == body


def test_session_cli_preserves_event_stream_errors(
    monkeypatch, tmp_path, capsys
) -> None:
    body = OperationResult.failure(
        ["session", "events"],
        3,
        "path_unavailable",
        "missing session",
        "session.events",
    ).as_dict()

    class FailingClient:
        def __init__(self, base_url: str, *, timeout_s: float) -> None:
            pass

        @staticmethod
        def get_session(session_id: str) -> dict[str, Any]:
            result = _result(["session", "get"])
            result["data"] = {
                "session": {
                    "session_id": session_id,
                    "event_count": 1,
                }
            }
            return result

        def events_session(
            self,
            session_id: str,
            *,
            resume_token: int | None = None,
            limit: int = 256,
            follow: bool = True,
        ) -> Iterator[dict[str, Any]]:
            assert follow is False
            raise breadboard_sdk.ApiError("failed", 404, body)

    monkeypatch.setattr(breadboard_sdk, "BreadBoardClient", FailingClient)
    assert (
        main(
            [
                "--json",
                "session",
                "--workspace",
                str(tmp_path),
                "--server",
                "http://breadboard.test",
                "events",
                "missing",
            ]
        )
        == 3
    )
    assert json.loads(capsys.readouterr().out) == body
