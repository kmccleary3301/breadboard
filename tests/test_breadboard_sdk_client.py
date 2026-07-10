from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

import breadboard_sdk.client as client_module
from breadboard_sdk.client import BreadboardClient


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


@pytest.mark.parametrize(
    ("invoke", "expected_url"),
    [
        pytest.param(
            lambda client: client.create_session(config_path="configs/agent.yaml", task="repair"),
            "https://breadboard.test/v1/sessions",
            id="create-session",
        ),
        pytest.param(
            lambda client: client.list_sessions(),
            "https://breadboard.test/v1/sessions",
            id="list-sessions",
        ),
        pytest.param(
            lambda client: client.get_session("session-123"),
            "https://breadboard.test/v1/sessions/session-123",
            id="get-session",
        ),
        pytest.param(
            lambda client: client.delete_session("session-123"),
            "https://breadboard.test/v1/sessions/session-123",
            id="delete-session",
        ),
        pytest.param(
            lambda client: client.post_input("session-123", content="continue"),
            "https://breadboard.test/v1/sessions/session-123/input",
            id="post-input",
        ),
        pytest.param(
            lambda client: client.post_command("session-123", command="stop"),
            "https://breadboard.test/v1/sessions/session-123/command",
            id="post-command",
        ),
        pytest.param(
            lambda client: client.get_skills("session-123"),
            "https://breadboard.test/v1/sessions/session-123/skills",
            id="get-skills",
        ),
        pytest.param(
            lambda client: client.get_ctree_snapshot("session-123"),
            "https://breadboard.test/v1/sessions/session-123/ctrees",
            id="get-ctree-snapshot",
        ),
        pytest.param(
            lambda client: client.list_session_files("session-123", path_prefix="reports"),
            "https://breadboard.test/v1/sessions/session-123/files?path=reports",
            id="list-session-files",
        ),
        pytest.param(
            lambda client: client.read_session_file(
                "session-123",
                file_path="reports/result.txt",
                mode="tail",
                head_lines=2,
                tail_lines=3,
                max_bytes=512,
            ),
            "https://breadboard.test/v1/sessions/session-123/files?"
            "path=reports%2Fresult.txt&mode=tail&head_lines=2&tail_lines=3&max_bytes=512",
            id="read-session-file-a4-intermediate-path",
        ),
        pytest.param(
            lambda client: client.download_artifact("session-123", artifact="logs/run 1.txt"),
            "https://breadboard.test/v1/sessions/session-123/download?artifact=logs%2Frun+1.txt",
            id="download-artifact",
        ),
        pytest.param(
            lambda client: client.get_models(config_path="configs/team model.yaml"),
            "https://breadboard.test/v1/models?config_path=configs%2Fteam+model.yaml",
            id="get-models",
        ),
    ],
)
def test_request_backed_session_and_model_operations_use_v1_urls(
    monkeypatch: pytest.MonkeyPatch,
    invoke: Callable[[BreadboardClient], object],
    expected_url: str,
) -> None:
    requested_urls: list[str] = []

    def fake_request(**kwargs: Any) -> _JsonResponse:
        requested_urls.append(kwargs["url"])
        return _JsonResponse()

    monkeypatch.setattr(client_module.requests, "request", fake_request)

    invoke(BreadboardClient(base_url="https://breadboard.test/"))

    assert requested_urls == [expected_url]


@pytest.mark.parametrize(
    ("invoke", "expected_url"),
    [
        pytest.param(
            lambda client: client.health(),
            "https://breadboard.test/health",
            id="health-remains-unversioned",
        ),
        pytest.param(
            lambda client: client.attach_provider_auth(provider_id="openai", api_key="secret"),
            "https://breadboard.test/v1/provider-auth/attach",
            id="attach-provider-auth-has-one-v1-prefix",
        ),
        pytest.param(
            lambda client: client.detach_provider_auth(provider_id="openai"),
            "https://breadboard.test/v1/provider-auth/detach",
            id="detach-provider-auth-has-one-v1-prefix",
        ),
        pytest.param(
            lambda client: client.provider_auth_status(),
            "https://breadboard.test/v1/provider-auth/status",
            id="provider-auth-status-has-one-v1-prefix",
        ),
    ],
)
def test_special_routes_keep_their_exact_prefixes(
    monkeypatch: pytest.MonkeyPatch,
    invoke: Callable[[BreadboardClient], object],
    expected_url: str,
) -> None:
    requested_urls: list[str] = []

    def fake_request(**kwargs: Any) -> _JsonResponse:
        requested_urls.append(kwargs["url"])
        return _JsonResponse()

    monkeypatch.setattr(client_module.requests, "request", fake_request)

    invoke(BreadboardClient(base_url="https://breadboard.test"))

    assert requested_urls == [expected_url]


def test_upload_attachments_posts_multipart_to_exact_v1_session_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted_urls: list[str] = []

    def fake_post(url: str, **kwargs: Any) -> _JsonResponse:
        posted_urls.append(url)
        return _JsonResponse({"attachments": ["attachment-1"]})

    monkeypatch.setattr(client_module.requests, "post", fake_post)
    client = BreadboardClient(base_url="https://breadboard.test/api-root")

    response = client.upload_attachments(
        "session-123",
        files=[("note.txt", b"hello", "text/plain")],
        metadata={"purpose": "evidence"},
    )

    assert posted_urls == ["https://breadboard.test/api-root/v1/sessions/session-123/attachments"]
    assert response == {"attachments": ["attachment-1"]}


def test_stream_events_gets_exact_v1_session_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    streamed_urls: list[str] = []
    event = {
        "id": "event-1",
        "type": "message",
        "session_id": "session-123",
        "turn": 1,
        "timestamp": 10,
        "payload": {"content": "done"},
    }

    class _StreamResponse(_JsonResponse):
        def iter_lines(self, *, decode_unicode: bool) -> list[str]:
            assert decode_unicode is True
            return [f"data: {client_module.json.dumps(event)}", ""]

    def fake_get(url: str, **kwargs: Any) -> _StreamResponse:
        streamed_urls.append(url)
        return _StreamResponse()

    monkeypatch.setattr(client_module.requests, "get", fake_get)
    client = BreadboardClient(base_url="https://breadboard.test/api-root/")

    received = list(client.stream_events("session-123"))

    assert streamed_urls == ["https://breadboard.test/api-root/v1/sessions/session-123/events"]
    assert received == [event]
