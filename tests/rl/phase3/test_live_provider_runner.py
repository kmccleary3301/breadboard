from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from pathlib import Path

import io
from breadboard.rl.phase3 import integrations
from scripts.rl_phase3.run_phase3_live_provider_reports import _component
TARGET = "20260623T000000Z-slurm-234555"


def test_live_provider_runner_blocks_without_credentials(tmp_path: Path, monkeypatch) -> None:
    for key in [
        "BREADBOARD_ORS_BASE_URL",
        "BREADBOARD_ORS_TOKEN",
        "BREADBOARD_OPENREWARD_BASE_URL",
        "BREADBOARD_OPENREWARD_TOKEN",
        "BREADBOARD_BENCHFLOW_BASE_URL",
        "BREADBOARD_BENCHFLOW_TOKEN",
        "BREADBOARD_HARBOR_BASE_URL",
        "BREADBOARD_HARBOR_TOKEN",
    ]:
        monkeypatch.delenv(key, raising=False)
    phase_dir = tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_3"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase3/run_phase3_live_provider_reports.py",
            "--phase-dir",
            str(phase_dir),
            "--target-run-id",
            TARGET,
            "--rows-jsonl",
            str(tmp_path / "missing_rows.jsonl"),
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 2
    out = phase_dir / "runs" / "live_provider_reports"
    p3m8 = json.loads((out / "P3-M8_retired_provider_milestone.json").read_text())
    p3m9 = json.loads((out / "P3-M9_harbor_nemo_gym.json").read_text())
    assert p3m8["passed"] is False
    assert p3m8["required_artifact_keys"] == ["provider_report"]
    assert Path(p3m8["artifact_paths"]["provider_report"]).exists()
    assert p3m8["component"] == "retired_provider_milestone"
    assert p3m8["provider_kind"] == "none"
    assert p3m8["blocked_reason"] == "retired_provider_milestone_pending_accepted_rubric_change"
    assert not (out / "live_verifier_campaign.json").exists()
    assert p3m9["passed"] is False
    assert p3m9["required_artifact_keys"] == ["provider_report"]
    assert Path(p3m9["artifact_paths"]["provider_report"]).exists()
    assert not (out / "benchflow_env_package_placeholder.txt").exists()


def test_live_provider_runner_ignores_native_benchflow_without_harbor(tmp_path: Path, monkeypatch) -> None:
    for key in [
        "BREADBOARD_ORS_BASE_URL",
        "BREADBOARD_ORS_TOKEN",
        "BREADBOARD_OPENREWARD_BASE_URL",
        "BREADBOARD_OPENREWARD_TOKEN",
        "BREADBOARD_HARBOR_BASE_URL",
        "BREADBOARD_HARBOR_TOKEN",
    ]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BREADBOARD_BENCHFLOW_BASE_URL", "https://1.1.1.1/benchflow")
    monkeypatch.setenv("BREADBOARD_BENCHFLOW_TOKEN", "benchflow-token")
    phase_dir = tmp_path / "docs_tmp" / "ZYPHRA" / "RL_PHASE_3"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase3/run_phase3_live_provider_reports.py",
            "--phase-dir",
            str(phase_dir),
            "--target-run-id",
            TARGET,
        ],
        cwd=Path(__file__).resolve().parents[3],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 2
    out = phase_dir / "runs" / "live_provider_reports"
    harbor = json.loads((out / "harbor_service_proof.json").read_text())
    p3m9 = json.loads((out / "P3-M9_harbor_nemo_gym.json").read_text())
    assert harbor["blocked_reason"] == "missing_harbor_credentials"
    assert p3m9["blocked_reason"] == "missing_harbor_credentials"
    assert not (out / "harbor_env_package_required.tar").exists()


def test_native_benchflow_helper_hashes_real_env_package_as_contract_only(tmp_path: Path, monkeypatch) -> None:
    env_package = tmp_path / "env-package.tar"
    env_package.write_bytes(b"phase3 env package bytes")
    captured: dict[str, object] = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return b'{"attested":true}'

    def fake_open(request, *, timeout_s: float):
        assert isinstance(request.data, bytes)
        captured["url"] = request.full_url
        captured["body_bytes"] = request.data
        captured["timeout_s"] = timeout_s
        return Response()

    monkeypatch.setattr(integrations, "_open_without_proxies", fake_open)

    report = integrations._run_native_benchflow_attestation("https://1.1.1.1/benchflow", "benchflow-token", env_package, target_run_id=TARGET)

    assert report["passed"] is True
    assert report["claim_boundary"] == "phase3_native_benchflow_contract_only_scope"
    assert captured["url"] == "https://1.1.1.1/benchflow/attest"
    response_body = b'{"attested":true}'
    body_bytes = captured["body_bytes"]
    assert isinstance(body_bytes, bytes)
    body = json.loads(body_bytes)
    assert body == {
        "target_run_id": TARGET,
        "env_package_sha256": "sha256:" + hashlib.sha256(env_package.read_bytes()).hexdigest(),
    }
    assert report["request_sha256"] == "sha256:" + hashlib.sha256(body_bytes).hexdigest()
    assert report["response_sha256"] == "sha256:" + hashlib.sha256(response_body).hexdigest()
    assert captured["timeout_s"] == 10.0

def test_p3m9_runner_component_sets_provider_kind_on_success(tmp_path: Path) -> None:
    provider_report_path = tmp_path / "benchflow.json"
    provider_report_path.write_text('{"passed": true}\n')
    report = _component(
        milestone_id="P3-M9",
        component="harbor_nemo_gym",
        points=60,
        claim_boundary="phase3_harbor_nemo_gym_named_endpoint_scope",
        blocked_claim_boundary="phase3_harbor_nemo_gym_blocked_scope",
        target_run_id=TARGET,
        provider_report_path=provider_report_path,
        provider_report={
            "passed": True,
            "attestation_backend": "harbor_facade",
        },
    )

    assert report["provider_kind"] == "harbor_facade"


def _http_error(url: str, status: int, body: bytes) -> integrations.urllib.error.HTTPError:
    return integrations.urllib.error.HTTPError(url, status, "provider error", {}, io.BytesIO(body))


def test_ors_openreward_http_error_keeps_response_hash(monkeypatch) -> None:
    response_body = b'{"ok":false}'
    monkeypatch.setenv("BREADBOARD_OPENREWARD_BASE_URL", "https://1.1.1.1/openreward")
    monkeypatch.setenv("BREADBOARD_OPENREWARD_TOKEN", "openreward-token")

    def fake_open(request, *, timeout_s: float):
        raise _http_error(request.full_url, 503, response_body)

    monkeypatch.setattr(integrations, "_open_without_proxies", fake_open)

    report = integrations.call_ors_openreward({"score": 1}, provider="openreward", timeout_s=3.0)

    assert report.passed is False
    assert report.status_code == 503
    assert report.response_sha256 == "sha256:" + hashlib.sha256(response_body).hexdigest()
    assert report.blocked_reason == ""


def test_native_benchflow_http_error_keeps_response_hash(tmp_path: Path, monkeypatch) -> None:
    env_package = tmp_path / "env-package.tar"
    env_package.write_bytes(b"phase3 env package bytes")
    response_body = b'{"attested":false}'

    def fake_open(request, *, timeout_s: float):
        raise _http_error(request.full_url, 409, response_body)

    monkeypatch.setattr(integrations, "_open_without_proxies", fake_open)

    report = integrations._run_native_benchflow_attestation("https://1.1.1.1/benchflow", "benchflow-token", env_package, target_run_id=TARGET)

    assert report["passed"] is False
    assert report["status_code"] == 409
    assert report["response_sha256"] == "sha256:" + hashlib.sha256(response_body).hexdigest()
    assert report["blocked_reason"] == ""


def test_provider_redirect_handler_rejects_same_origin_redirect_without_dns_resolution(monkeypatch) -> None:
    def fail_getaddrinfo(*args, **kwargs):
        raise AssertionError("same-origin redirect must not trigger DNS resolution")

    monkeypatch.setattr(integrations.socket, "getaddrinfo", fail_getaddrinfo)
    handler = integrations._NoUnsafeRedirectHandler()
    request = integrations.urllib.request.Request(
        "https://provider.example/verify",
        data=b"{}",
        method="POST",
    )

    try:
        handler.redirect_request(request, None, 302, "Found", {}, "https://provider.example/private")
    except integrations.ProviderRedirectBlocked as exc:
        assert exc.block_reason == "provider_redirect_not_followed"
    else:
        raise AssertionError("same-origin provider redirect was followed")

def test_provider_redirect_handler_rejects_public_cross_origin_redirect(monkeypatch) -> None:
    monkeypatch.setattr(
        integrations.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(None, None, None, None, ("1.1.1.1", 443))],
    )
    handler = integrations._NoUnsafeRedirectHandler()
    request = integrations.urllib.request.Request(
        "https://provider.example/verify",
        data=b"{}",
        method="POST",
        headers={"Authorization": "Bearer secret-token"},
    )

    try:
        handler.redirect_request(request, None, 302, "Found", {}, "https://other.example/verify")
    except integrations.ProviderRedirectBlocked as exc:
        assert exc.block_reason == "provider_redirect_cross_origin_blocked"
    else:
        raise AssertionError("public cross-origin provider redirect was allowed")

def test_provider_redirect_handler_rejects_same_origin_redirect_before_post_rewrite() -> None:
    handler = integrations._NoUnsafeRedirectHandler()
    request = integrations.urllib.request.Request(
        "https://provider.example/verify",
        data=b'{"score":1}',
        method="POST",
    )

    try:
        handler.redirect_request(request, None, 302, "Found", {}, "https://provider.example/verify-v2")
    except integrations.ProviderRedirectBlocked as exc:
        assert exc.block_reason == "provider_redirect_not_followed"
    else:
        raise AssertionError("same-origin provider redirect was followed")

def test_hostname_provider_connect_pins_tcp_ip_but_keeps_sni(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_create_connection(endpoint, timeout, source_address=None):
        captured["endpoint"] = endpoint
        captured["timeout"] = timeout
        captured["source_address"] = source_address
        return object()

    class Context:
        def wrap_socket(self, sock, *, server_hostname: str):
            captured["sock"] = sock
            captured["server_hostname"] = server_hostname
            return "tls-socket"

    monkeypatch.setattr(integrations.socket, "create_connection", fake_create_connection)
    connection = integrations._PinnedHTTPSConnection(
        "provider.example",
        {("provider.example", 443): ("1.1.1.1",)},
        timeout=2.0,
    )
    connection._context = Context()

    connection.connect()

    assert captured["endpoint"] == ("1.1.1.1", 443)
    assert captured["server_hostname"] == "provider.example"
    assert connection.sock == "tls-socket"



def test_ors_openreward_redirect_block_reason_is_canonical(monkeypatch) -> None:
    monkeypatch.setenv("BREADBOARD_ORS_BASE_URL", "https://1.1.1.1/provider")
    monkeypatch.setenv("BREADBOARD_ORS_TOKEN", "ors-token")

    def fake_open(_request, *, timeout_s: float):
        raise integrations.ProviderRedirectBlocked("provider_endpoint_must_not_be_loopback_or_private")

    monkeypatch.setattr(integrations, "_open_without_proxies", fake_open)

    report = integrations.call_ors_openreward({"x": 1}, provider="ors", timeout_s=3.0)

    assert report.passed is False
    assert report.blocked_reason == "provider_endpoint_must_not_be_loopback_or_private"


def test_native_benchflow_redirect_block_reason_is_canonical(tmp_path: Path, monkeypatch) -> None:
    env_package = tmp_path / "env-package.tar"
    env_package.write_bytes(b"phase3 env package bytes")

    def fake_open(_request, *, timeout_s: float):
        raise integrations.ProviderRedirectBlocked("provider_endpoint_must_not_be_loopback_or_private")

    monkeypatch.setattr(integrations, "_open_without_proxies", fake_open)

    report = integrations._run_native_benchflow_attestation("https://1.1.1.1/benchflow", "benchflow-token", env_package, target_run_id=TARGET)

    assert report["passed"] is False
    assert report["blocked_reason"] == "provider_endpoint_must_not_be_loopback_or_private"