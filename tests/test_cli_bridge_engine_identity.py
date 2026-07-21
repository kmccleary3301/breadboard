from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from fastapi.routing import APIRoute
from pydantic import TypeAdapter, ValidationError

from agentic_coder_prototype.api.cli_bridge.events import (
    SessionEvent,
    replay_configuration_digest,
)
from agentic_coder_prototype.api.cli_bridge.registry import SessionRecord
from agentic_coder_prototype.api.cli_bridge.app import (
    _compute_engine_provenance,
    _p30_session_contract_descriptor,
    create_app,
)
from agentic_coder_prototype.api.cli_bridge.engine_identity_config import (
    ENGINE_LAUNCH_ID_ENV,
    P30_SESSION_CONTRACT_ID,
    P30_SESSION_SCHEMA_SHA256,
    engine_source_artifact_sha256,
    get_engine_process_identity,
    p30_session_schema_sha256,
)
from agentic_coder_prototype.api.cli_bridge.models import EngineIdentityReadinessResponse
from agentic_coder_prototype.api.cli_bridge.service import SessionService


def _session_route(app, method: str, path: str) -> APIRoute:
    return next(
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == path and method in route.methods
    )


async def _get_identity(app, *, headers: dict[str, str] | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.get("/v1/engine/identity", headers=headers)


def _identity_subprocess(
    *,
    launch_id: str | None = None,
    malformed_launch_id: str | None = None,
) -> subprocess.CompletedProcess[str]:
    repo_root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.pop(ENGINE_LAUNCH_ID_ENV, None)
    if launch_id is not None:
        environment[ENGINE_LAUNCH_ID_ENV] = launch_id
    if malformed_launch_id is not None:
        environment[ENGINE_LAUNCH_ID_ENV] = malformed_launch_id
    program = (
        "import json; "
        "from agentic_coder_prototype.api.cli_bridge.engine_identity_config import "
        "get_engine_process_identity; "
        "identity = get_engine_process_identity(); "
        "print(json.dumps({'pid': identity.pid, "
        "'engine_instance_id': identity.engine_instance_id, "
        "'engine_boot_id': identity.engine_boot_id, "
        "'launch_id': identity.launch_id, "
        "'launch_source': identity.launch_source, "
        "'started_at_unix': identity.started_at_unix, "
        "'os_process_start_token': identity.os_process_start_token, "
        "'engine_artifact_sha256': identity.engine_artifact_sha256}))"
    )
    return subprocess.run(
        [sys.executable, "-c", program],
        cwd=repo_root,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.asyncio
async def test_identity_is_stable_and_exact_within_one_process(tmp_path: Path) -> None:
    first_app = create_app(SessionService(state_root=tmp_path / "first"))
    second_app = create_app(SessionService(state_root=tmp_path / "second"))

    first = await _get_identity(first_app)
    second = await _get_identity(second_app)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()

    payload = first.json()
    assert set(payload) == {
        "schema_version",
        "liveness",
        "process",
        "launch",
        "artifact_revision",
        "protocol",
        "session_contract",
        "session_readiness",
    }
    assert set(payload["process"]) == {
        "engine_instance_id",
        "engine_boot_id",
        "started_at",
        "started_at_unix",
        "pid",
        "os_process_start_token",
    }
    assert set(payload["launch"]) == {"launch_id", "source"}
    assert payload["launch"]["source"] in {"supervisor", "external_unmanaged"}
    assert payload["liveness"] == {"status": "live"}
    assert payload["protocol"] == {"protocol_version": "1.0"}
    assert payload["session_contract"] == {
        "contract_id": P30_SESSION_CONTRACT_ID,
        "schema_sha256": P30_SESSION_SCHEMA_SHA256,
        "compatibility": "compatible",
        "sessionReplayContractDigest": replay_configuration_digest(),
    }
    assert payload["session_readiness"] == {"ready": True, "reason": "ready"}
    EngineIdentityReadinessResponse.model_validate(payload)


def test_module_reload_preserves_process_identity() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    program = """
import importlib
import json
from agentic_coder_prototype.api.cli_bridge import engine_identity_config

def snapshot():
    identity = engine_identity_config.get_engine_process_identity()
    return {
        "pid": identity.pid,
        "engine_instance_id": identity.engine_instance_id,
        "engine_boot_id": identity.engine_boot_id,
        "launch_id": identity.launch_id,
        "started_at_unix": identity.started_at_unix,
        "os_process_start_token": identity.os_process_start_token,
        "engine_artifact_sha256": identity.engine_artifact_sha256,
    }

before = snapshot()
importlib.reload(engine_identity_config)
print(json.dumps({"before": before, "after": snapshot()}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    identities = json.loads(completed.stdout)

    assert identities["after"] == identities["before"]


def test_preloaded_fork_rotates_process_identity() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    program = """
import json
import os
from agentic_coder_prototype.api.cli_bridge.engine_identity_config import get_engine_process_identity

parent = get_engine_process_identity()
read_fd, write_fd = os.pipe()
child_pid = os.fork()
if child_pid == 0:
    os.close(read_fd)
    child = get_engine_process_identity()
    payload = {
        "pid": child.pid,
        "engine_instance_id": child.engine_instance_id,
        "engine_boot_id": child.engine_boot_id,
        "started_at_unix": child.started_at_unix,
        "os_process_start_token": child.os_process_start_token,
    }
    os.write(write_fd, json.dumps(payload).encode("utf-8"))
    os.close(write_fd)
    os._exit(0)
os.close(write_fd)
child_payload = json.loads(os.read(read_fd, 8192).decode("utf-8"))
os.close(read_fd)
os.waitpid(child_pid, 0)
print(json.dumps({
    "parent": {
        "pid": parent.pid,
        "engine_instance_id": parent.engine_instance_id,
        "engine_boot_id": parent.engine_boot_id,
        "started_at_unix": parent.started_at_unix,
        "os_process_start_token": parent.os_process_start_token,
    },
    "child": child_payload,
}))
"""
    environment = os.environ.copy()
    environment.pop(ENGINE_LAUNCH_ID_ENV, None)
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repo_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    identities = json.loads(completed.stdout)

    assert identities["parent"]["pid"] != identities["child"]["pid"]
    assert identities["parent"]["engine_instance_id"] != identities["child"]["engine_instance_id"]
    assert identities["parent"]["engine_boot_id"] != identities["child"]["engine_boot_id"]
    assert identities["parent"]["started_at_unix"] != identities["child"]["started_at_unix"]
    assert (
        identities["parent"]["os_process_start_token"]
        != identities["child"]["os_process_start_token"]
    )


@pytest.mark.parametrize(
    "identity_key",
    ["engine_instance_id", "engine_boot_id", "os_process_start_token"],
)
def test_identity_rotates_on_fresh_process_restart(identity_key: str) -> None:
    first_result = _identity_subprocess()
    restarted_result = _identity_subprocess()

    assert first_result.returncode == 0
    assert restarted_result.returncode == 0
    first = json.loads(first_result.stdout)
    restarted = json.loads(restarted_result.stdout)
    assert first[identity_key] != restarted[identity_key]
    assert first["launch_id"] != restarted["launch_id"]
    assert first["launch_source"] == "external_unmanaged"
    assert restarted["launch_source"] == "external_unmanaged"


def test_supervisor_launch_id_is_validated_and_returned_unchanged() -> None:
    supplied_launch_id = "A" * 43
    completed = _identity_subprocess(launch_id=supplied_launch_id)

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["launch_id"] == supplied_launch_id
    assert payload["launch_source"] == "supervisor"


def test_missing_launch_id_uses_generated_external_unmanaged_fallback() -> None:
    completed = _identity_subprocess()

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert len(payload["launch_id"]) == 43
    assert payload["launch_source"] == "external_unmanaged"


def test_malformed_supervisor_launch_id_fails_closed_without_echo() -> None:
    malformed = "malformed-launch-id-secret-value"
    completed = _identity_subprocess(malformed_launch_id=malformed)

    assert completed.returncode != 0
    assert "43-character URL-safe identifier" in completed.stderr
    assert malformed not in completed.stderr
    assert completed.stdout == ""


def test_engine_artifact_digest_tracks_actual_source_not_python(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "engine.py").write_text("ENGINE_REVISION = 'first'\n", encoding="utf-8")
    (second / "engine.py").write_text("ENGINE_REVISION = 'second'\n", encoding="utf-8")

    first_digest = engine_source_artifact_sha256(first)
    second_digest = engine_source_artifact_sha256(second)

    assert first_digest.startswith("sha256:")
    assert second_digest.startswith("sha256:")
    assert first_digest != second_digest
    assert get_engine_process_identity().engine_artifact_sha256 != (
        "sha256:" + hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest()
    )


@pytest.mark.asyncio
async def test_missing_required_session_route_is_not_ready(tmp_path: Path) -> None:
    app = create_app(SessionService(state_root=tmp_path))
    app.router.routes[:] = [
        route
        for route in app.router.routes
        if not (
            isinstance(route, APIRoute)
            and route.path == "/v1/sessions/{session_id}/events"
            and "GET" in route.methods
        )
    ]

    response = await _get_identity(app)

    assert response.status_code == 200
    payload = response.json()
    assert payload["liveness"] == {"status": "live"}
    assert payload["session_contract"]["compatibility"] == "incompatible"
    assert payload["session_readiness"] == {
        "ready": False,
        "reason": "session_contract_missing",
    }


def test_fixed_digest_is_exact_and_excludes_lifecycle_operations(tmp_path: Path) -> None:
    service = SessionService(state_root=tmp_path)
    contract = _p30_session_contract_descriptor(create_app(service), service)
    assert contract["contract_id"] == "p30-e4-session-v1"
    assert [
        (operation["method"], operation["path"])
        for operation in contract["http"]["operations"]
    ] == [
        ("POST", "/v1/sessions"),
        ("GET", "/v1/sessions/{session_id}"),
        ("POST", "/v1/sessions/{session_id}/input"),
        ("POST", "/v1/sessions/{session_id}/turns/{turn_id}/cancel"),
        ("GET", "/v1/sessions/{session_id}/events"),
        ("DELETE", "/v1/sessions/{session_id}"),
    ]
    assert p30_session_schema_sha256(contract) == (
        "sha256:5757652c22d6aa2eb7a1cc8be1a40021d3f6a15df18d69ca22dc1916a400dbd4"
    )
    assert p30_session_schema_sha256(contract) == P30_SESSION_SCHEMA_SHA256
    assert contract["event_stream"]["envelope_schema"]["properties"]["payload"] == {
        "type": "object"
    }

    encoded = json.dumps(contract, sort_keys=True, separators=(",", ":")).lower()
    for excluded in ("registration", "owner", "drain", "control", "capability"):
        assert excluded not in encoded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "drift",
    [
        "request_model",
        "response_model",
        "success_status",
        "query_schema",
        "sse_schema",
        "service_binding",
        "prepared_stream_binding",
        "sse_encoder_binding",
        "event_asdict_binding",
        "session_summary_binding",
    ],
)
async def test_same_routes_with_contract_drift_are_not_ready(
    drift: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from agentic_coder_prototype.api.cli_bridge import engine_identity_config

    service = SessionService(state_root=tmp_path)
    app = create_app(service)
    if drift == "request_model":
        route = _session_route(app, "POST", "/v1/sessions/{session_id}/input")
        body_parameter = route.dependant.body_params[0]
        body_parameter._type_adapter = TypeAdapter(dict[str, str])
        body_parameter.field_info.annotation = dict[str, str]
    elif drift == "response_model":
        route = _session_route(app, "POST", "/v1/sessions/{session_id}/input")
        route.response_model = dict[str, str]
        route.response_field = None
    elif drift == "success_status":
        _session_route(app, "POST", "/v1/sessions/{session_id}/input").status_code = 200
    elif drift == "query_schema":
        route = _session_route(app, "GET", "/v1/sessions/{session_id}/events")
        route.dependant.query_params = route.dependant.query_params[1:]
    elif drift == "sse_schema":
        changed = deepcopy(engine_identity_config.P30_SESSION_EVENT_STREAM_CONTRACT)
        changed["framing"]["id"] = "drifted"
        monkeypatch.setattr(engine_identity_config, "P30_SESSION_EVENT_STREAM_CONTRACT", changed)
    elif drift == "service_binding":
        monkeypatch.setattr(service, "send_input", lambda *_args, **_kwargs: None)
    elif drift == "prepared_stream_binding":
        monkeypatch.setattr(
            service,
            "prepared_event_stream",
            lambda *_args, **_kwargs: None,
        )
    elif drift == "sse_encoder_binding":
        monkeypatch.setattr(
            "agentic_coder_prototype.api.cli_bridge.app._encode_sse_event",
            lambda _event: b"invalid",
        )
    elif drift == "event_asdict_binding":
        monkeypatch.setattr(
            SessionEvent,
            "asdict",
            lambda _event: {"invalid": True},
        )
    else:
        monkeypatch.setattr(
            SessionRecord,
            "to_summary",
            lambda _record: {"invalid": True},
        )

    response = await _get_identity(app)

    assert response.status_code == 200
    assert response.json()["session_contract"]["compatibility"] == "incompatible"
    assert response.json()["session_readiness"] == {
        "ready": False,
        "reason": "session_contract_mismatch",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("environment_name", "value"),
    [
        ("BREADBOARD_CLI_DROP_RATE", "1"),
        ("BREADBOARD_CLI_LATENCY_MS", "1"),
        ("BREADBOARD_CLI_JITTER_MS", "1"),
    ],
)
async def test_session_delivery_chaos_is_not_contract_ready(
    environment_name: str,
    value: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(environment_name, value)
    app = create_app(SessionService(state_root=tmp_path))

    response = await _get_identity(app)

    assert response.status_code == 200
    assert response.json()["session_contract"]["compatibility"] == "incompatible"
    assert response.json()["session_readiness"] == {
        "ready": False,
        "reason": "session_contract_mismatch",
    }


@pytest.mark.parametrize(
    ("git_status", "expected_dirty"),
    [("", False), (" M app.py", True), (None, None)],
)
def test_git_provenance_distinguishes_clean_dirty_and_failure(
    git_status: str | None,
    expected_dirty: bool | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_git(args, _cwd, *, allow_empty=False):
        if args == ["rev-parse", "--show-toplevel"]:
            return str(tmp_path)
        if args == ["rev-parse", "HEAD"]:
            return "a" * 40
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "main"
        if args == ["status", "--porcelain"]:
            return git_status
        raise AssertionError(args)

    monkeypatch.setattr(
        "agentic_coder_prototype.api.cli_bridge.app._run_git_command",
        fake_git,
    )

    assert _compute_engine_provenance(tmp_path)["dirty"] is expected_dirty


@pytest.mark.asyncio
async def test_identity_route_enforces_auth_and_redacts_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token = "identity-test-token-that-must-never-appear"
    provider_secret = "provider-secret-that-must-never-appear"
    monkeypatch.setenv("BREADBOARD_API_TOKEN", token)
    monkeypatch.setenv("BREADBOARD_OPENAI_AUTH_HEADERS_JSON", provider_secret)
    app = create_app(SessionService(state_root=tmp_path))

    unauthorized = await _get_identity(app)
    authorized = await _get_identity(app, headers={"authorization": f"Bearer {token}"})

    assert unauthorized.status_code == 401
    assert unauthorized.json() == {
        "error": "unauthorized",
        "detail": "unauthorized",
        "path": None,
    }
    assert authorized.status_code == 200
    rendered = authorized.text
    assert token not in unauthorized.text
    assert token not in rendered
    assert provider_secret not in rendered
    for forbidden_field in ("repo_root", "branch", "authorization", "environment"):
        assert forbidden_field not in rendered.lower()


def test_default_app_imports_without_optional_dotenv() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    program = (
        "import sys; "
        "sys.modules['dotenv'] = None; "
        "from agentic_coder_prototype.api.cli_bridge.app import app; "
        "print(app.title)"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "BreadBoard CLI Bridge"


@pytest.mark.asyncio
async def test_pid_health_and_port_do_not_grant_ownership_or_registration(tmp_path: Path) -> None:
    app = create_app(SessionService(state_root=tmp_path))
    identity = await _get_identity(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        health = await client.get("/health")

    assert identity.status_code == 200
    assert health.status_code == 200
    assert isinstance(identity.json()["process"]["pid"], int)
    combined = json.dumps(
        {"identity": identity.json(), "health": health.json()},
        sort_keys=True,
    ).lower()
    for forbidden in ("ownership", "owner_generation", "registration_id", "control_token"):
        assert forbidden not in combined
    assert "port" not in json.dumps(identity.json(), sort_keys=True).lower()

    paths = set(app.openapi()["paths"])
    for forbidden_path in (
        "/v1/owner",
        "/v1/register",
        "/v1/control",
        "/v1/drain",
        "/v1/capabilities",
    ):
        assert not any(
            path == forbidden_path or path.startswith(forbidden_path + "/")
            for path in paths
        )


@pytest.mark.asyncio
async def test_identity_model_rejects_malformed_backend_payloads(tmp_path: Path) -> None:
    response = await _get_identity(create_app(SessionService(state_root=tmp_path)))
    payload = response.json()

    malformed_payloads: list[dict[str, object]] = []
    extra = deepcopy(payload)
    extra["capabilities"] = []
    malformed_payloads.append(extra)
    bad_instance = deepcopy(payload)
    bad_instance["process"]["engine_instance_id"] = "guessable"
    malformed_payloads.append(bad_instance)
    wrong_contract = deepcopy(payload)
    wrong_contract["session_contract"]["contract_id"] = "other-contract"
    malformed_payloads.append(wrong_contract)
    wrong_digest = deepcopy(payload)
    wrong_digest["session_contract"]["schema_sha256"] = "sha256:" + "0" * 64
    malformed_payloads.append(wrong_digest)

    contradictory_states = (
        ("compatible", False, "ready"),
        ("compatible", False, "session_contract_missing"),
        ("compatible", False, "session_contract_mismatch"),
        ("incompatible", True, "ready"),
        ("incompatible", True, "session_contract_missing"),
        ("incompatible", True, "session_contract_mismatch"),
    )
    for compatibility, ready, reason in contradictory_states:
        contradictory = deepcopy(payload)
        contradictory["session_contract"]["compatibility"] = compatibility
        contradictory["session_readiness"] = {"ready": ready, "reason": reason}
        malformed_payloads.append(contradictory)

    for malformed in malformed_payloads:
        with pytest.raises(ValidationError):
            EngineIdentityReadinessResponse.model_validate(malformed)
