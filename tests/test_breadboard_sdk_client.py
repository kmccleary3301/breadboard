from __future__ import annotations

from typing import Any
import json
from pathlib import Path

import pytest

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
    contract = json.loads((Path(__file__).parents[1] / "contracts/public/operations.v2.json").read_text())
    methods = {
        operation["bindings"]["python_sdk"]["method"]
        for operation in contract["operations"]
        if operation["status"] == "candidate"
    }
    public_methods = {name for name, value in vars(BreadBoardClient).items() if callable(value) and not name.startswith("_")}
    assert public_methods == methods


def test_legacy_python_client_surface_requires_explicit_compatibility_import() -> None:
    assert not hasattr(breadboard_sdk, "BreadboardClient")
    assert hasattr(CompatibilityBreadboardClient, "health")
    assert not hasattr(BreadBoardClient, "health")


def test_candidate_python_sdk_preserves_public_result_and_idempotency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = {
        "schema_version": "bb.cli.result.v1", "ok": True, "status": "ok", "command": [],
        "record_refs": [], "hashes": {}, "stage_outcomes": [], "warnings": [],
        "next_actions": [], "error": None, "exit_code": 0, "data": {},
    }
    requests: list[dict[str, Any]] = []
    def fake_request(**kwargs: Any) -> _JsonResponse:
        requests.append(kwargs)
        return _JsonResponse(result)
    monkeypatch.setattr(client_module.requests, "request", fake_request)
    client = BreadBoardClient(base_url="https://breadboard.test/")
    assert client.start_session({"lock_id": "lock.json", "task": "run"}, idempotency_key="start-key") == result
    assert client.get_artifact("sha256:abc") == result
    assert requests[0]["headers"]["Idempotency-Key"] == "start-key"
    assert requests[1]["url"] == "https://breadboard.test/v1/artifacts/sha256%3Aabc"


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
