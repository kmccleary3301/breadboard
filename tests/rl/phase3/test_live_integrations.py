from __future__ import annotations

import json
import hashlib
import http.server
import socket
import socketserver
import threading
from pathlib import Path

from breadboard.rl.phase3.integrations import call_ors_openreward, run_benchflow_harbor_attestation, run_harbor_service_proof, run_live_verifier_campaign

TARGET = "20260623T000000Z-slurm-234555"


class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        self.rfile.read(length)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *args):
        pass


def server():
    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv


def test_provider_env_absence_blocks(monkeypatch) -> None:
    monkeypatch.delenv("BREADBOARD_ORS_BASE_URL", raising=False)
    monkeypatch.delenv("BREADBOARD_ORS_TOKEN", raising=False)
    monkeypatch.delenv("BREADBOARD_OPENREWARD_BASE_URL", raising=False)
    monkeypatch.delenv("BREADBOARD_OPENREWARD_TOKEN", raising=False)
    report = run_live_verifier_campaign([{"id": 1}], target_run_id=TARGET)
    assert report["passed"] is False
    assert report["blocked_reason"] == "missing_live_provider_credentials"


def test_campaign_preserves_unsafe_endpoint_reason(monkeypatch) -> None:
    monkeypatch.setenv("BREADBOARD_ORS_BASE_URL", "https://127.0.0.1:8443")
    monkeypatch.setenv("BREADBOARD_ORS_TOKEN", "token")
    monkeypatch.setenv("BREADBOARD_OPENREWARD_BASE_URL", "https://127.0.0.1:9443")
    monkeypatch.setenv("BREADBOARD_OPENREWARD_TOKEN", "token")
    report = run_live_verifier_campaign([{"id": 1}], target_run_id=TARGET)
    assert report["passed"] is False
    assert report["blocked_reason"] == "provider_endpoint_must_not_be_loopback_or_private"

def test_campaign_rejects_dns_resolved_loopback(monkeypatch) -> None:
    monkeypatch.setenv("BREADBOARD_ORS_BASE_URL", "https://provider.example.test")
    monkeypatch.setenv("BREADBOARD_ORS_TOKEN", "token")
    monkeypatch.setenv("BREADBOARD_OPENREWARD_BASE_URL", "https://provider.example.test")
    monkeypatch.setenv("BREADBOARD_OPENREWARD_TOKEN", "token")
    monkeypatch.setattr(
        "breadboard.rl.phase3.integrations.socket.getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 443))],
    )
    report = run_live_verifier_campaign([{"id": 1}], target_run_id=TARGET)
    assert report["passed"] is False
    assert report["blocked_reason"] == "provider_endpoint_must_not_be_loopback_or_private"

def test_provider_calls_disable_proxy_handlers(monkeypatch) -> None:
    seen = {}

    class FakeOpener:
        def open(self, request, timeout):  # noqa: ANN001
            raise RuntimeError("stop-before-network")

    def fake_build_opener(*handlers):  # noqa: ANN001
        seen["proxies"] = getattr(handlers[0], "proxies", None)
        return FakeOpener()

    monkeypatch.setenv("BREADBOARD_ORS_BASE_URL", "https://provider.example.test")
    monkeypatch.setenv("BREADBOARD_ORS_TOKEN", "token")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9999")
    monkeypatch.setattr(
        "breadboard.rl.phase3.integrations.socket.getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443))],
    )
    monkeypatch.setattr("breadboard.rl.phase3.integrations.urllib.request.build_opener", fake_build_opener)
    evidence = call_ors_openreward({"id": 1}, provider="ors", timeout_s=1)
    assert evidence.blocked_reason == "RuntimeError"
    assert seen["proxies"] == {}

def test_loopback_http_provider_is_blocked(monkeypatch) -> None:
    srv = server()
    monkeypatch.setenv("BREADBOARD_ORS_BASE_URL", f"http://127.0.0.1:{srv.server_address[1]}")
    monkeypatch.setenv("BREADBOARD_ORS_TOKEN", "token")
    evidence = call_ors_openreward({"x": 1}, provider="ors", timeout_s=2)
    srv.shutdown()
    assert evidence.passed is False
    assert evidence.blocked_reason == "provider_endpoint_must_be_https"


def test_harbor_missing_credentials(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("BREADBOARD_BENCHFLOW_BASE_URL", raising=False)
    monkeypatch.delenv("BREADBOARD_BENCHFLOW_TOKEN", raising=False)
    monkeypatch.delenv("BREADBOARD_HARBOR_BASE_URL", raising=False)
    monkeypatch.delenv("BREADBOARD_HARBOR_TOKEN", raising=False)
    report = run_harbor_service_proof(tmp_path / "env.tar", target_run_id=TARGET)
    assert report["blocked_reason"] == "missing_harbor_credentials"

def test_harbor_loopback_provider_is_blocked(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BREADBOARD_HARBOR_BASE_URL", "https://127.0.0.1:8443")
    monkeypatch.setenv("BREADBOARD_HARBOR_TOKEN", "token")
    report = run_harbor_service_proof(tmp_path / "env.tar", target_run_id=TARGET)
    assert report["passed"] is False
    assert report["blocked_reason"] == "provider_endpoint_must_not_be_loopback_or_private"


def test_harbor_facade_attestation_reports_routes(monkeypatch, tmp_path: Path) -> None:
    env_package = tmp_path / "env.tar"
    env_package.write_bytes(b"harbor env package")
    seen_requests: list[tuple[str, str]] = []
    expected_routes = [
        "GET /health",
        "GET /metrics.json",
        "GET /list_tasks",
        "POST /score",
        "POST /trial/create",
        "POST /trial/{trial_id}/exec",
        "GET /trial/{trial_id}",
        "POST /trial/{trial_id}/finalize",
    ]

    class Response:
        status = 200

        def __init__(self, body: bytes = b'{"ok":true}') -> None:
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return self._body

    def fake_open(request, *, timeout_s: float):  # noqa: ANN001
        assert timeout_s == 10.0
        seen_requests.append((request.get_method(), request.full_url))
        if request.full_url.endswith("/list_tasks"):
            return Response(b'["phase3-harbor-smoke"]')
        if request.full_url.endswith("/score"):
            body = json.loads(request.data)
            assert body == {"answer": "phase3 harbor service proof", "task_name": "phase3-harbor-smoke"}
            return Response(b'{"reward":1.0,"raw":{"reward":1.0},"error":null}')
        if request.full_url.endswith("/trial/create"):
            body = json.loads(request.data)
            assert body == {"task_name": "phase3-harbor-smoke"}
            return Response(b'{"trial_id":"trial-1","task_name":"phase3-harbor-smoke","instruction":"solve","expires_at_ts":1.0}')
        if request.full_url.endswith("/trial/trial-1/exec"):
            body = json.loads(request.data)
            assert body == {
                "cmd": "printf phase3-harbor-proof",
                "timeout_sec": 30,
            }
            return Response(b'{"stdout":"phase3-harbor-proof","stderr":"","return_code":0,"stdout_truncated":false,"stderr_truncated":false,"duration_sec":0.1}')
        if request.full_url.endswith("/trial/trial-1/finalize"):
            body = json.loads(request.data)
            assert body == {"answer": "phase3 harbor service proof"}
            return Response(b'{"reward":1.0,"raw":{"reward":1.0},"error":null}')
        if request.full_url.endswith("/trial/trial-1"):
            return Response(b'{"trial_id":"trial-1","task_name":"phase3-harbor-smoke","created_at_ts":0.0,"expires_at_ts":1.0,"n_exec_calls":1,"finalized":false}')
        return Response(b'{"dataset":"harbor-test","ok":true}')

    monkeypatch.setenv("BREADBOARD_HARBOR_BASE_URL", "https://1.1.1.1/harbor")
    monkeypatch.setenv("BREADBOARD_HARBOR_TOKEN", "harbor-token")
    monkeypatch.delenv("BREADBOARD_BENCHFLOW_BASE_URL", raising=False)
    monkeypatch.delenv("BREADBOARD_BENCHFLOW_TOKEN", raising=False)
    monkeypatch.setattr("breadboard.rl.phase3.integrations._open_without_proxies", fake_open)

    report = run_benchflow_harbor_attestation(env_package, target_run_id=TARGET)

    assert report["passed"] is True
    assert report["attestation_backend"] == "harbor_facade"
    assert report["provider_kind"] == "harbor_facade"
    assert report["env_package_sha256"] == "sha256:" + hashlib.sha256(env_package.read_bytes()).hexdigest()
    assert report["harbor_routes"] == expected_routes
    assert report["status_codes"] == {route: 200 for route in expected_routes}
    assert seen_requests == [
        ("GET", "https://1.1.1.1/harbor/health"),
        ("GET", "https://1.1.1.1/harbor/metrics.json"),
        ("GET", "https://1.1.1.1/harbor/list_tasks"),
        ("POST", "https://1.1.1.1/harbor/score"),
        ("POST", "https://1.1.1.1/harbor/trial/create"),
        ("POST", "https://1.1.1.1/harbor/trial/trial-1/exec"),
        ("GET", "https://1.1.1.1/harbor/trial/trial-1"),
        ("POST", "https://1.1.1.1/harbor/trial/trial-1/finalize"),
    ]


def test_harbor_facade_attestation_rejects_200_status_failed_semantics(monkeypatch, tmp_path: Path) -> None:
    env_package = tmp_path / "env.tar"
    env_package.write_bytes(b"harbor env package")

    class Response:
        status = 200

        def __init__(self, body: bytes = b'{"ok":true}') -> None:
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return self._body

    def fake_open(request, *, timeout_s: float):  # noqa: ANN001, ARG001
        if request.full_url.endswith("/list_tasks"):
            return Response(b'["phase3-harbor-smoke"]')
        if request.full_url.endswith("/score"):
            return Response(b'{"score":0.0,"passed":false}')
        if request.full_url.endswith("/trial/create"):
            return Response(b'{"trial_id":"trial-1","task_name":"phase3-harbor-smoke"}')
        if request.full_url.endswith("/trial/trial-1/exec"):
            return Response(b'{"stdout":"phase3-harbor-proof","return_code":0}')
        if request.full_url.endswith("/trial/trial-1/finalize"):
            return Response(b'{"reward":1.0}')
        if request.full_url.endswith("/trial/trial-1"):
            return Response(b'{"trial_id":"trial-1","task_name":"phase3-harbor-smoke","n_exec_calls":1}')
        return Response(b'{"dataset":"harbor-test","ok":true}')

    monkeypatch.setenv("BREADBOARD_HARBOR_BASE_URL", "https://1.1.1.1/harbor")
    monkeypatch.setenv("BREADBOARD_HARBOR_TOKEN", "harbor-token")
    monkeypatch.setattr("breadboard.rl.phase3.integrations._open_without_proxies", fake_open)

    report = run_benchflow_harbor_attestation(env_package, target_run_id=TARGET)

    assert report["passed"] is False
    assert report["blocked_reason"] == "harbor_response_semantics_failed"


def test_harbor_facade_attestation_rejects_missing_exec_count(monkeypatch, tmp_path: Path) -> None:
    env_package = tmp_path / "env.tar"
    env_package.write_bytes(b"harbor env package")

    class Response:
        status = 200

        def __init__(self, body: bytes = b'{"ok":true}') -> None:
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return self._body

    def fake_open(request, *, timeout_s: float):  # noqa: ANN001, ARG001
        if request.full_url.endswith("/list_tasks"):
            return Response(b'["phase3-harbor-smoke"]')
        if request.full_url.endswith("/score"):
            return Response(b'{"score":1.0,"passed":true}')
        if request.full_url.endswith("/trial/create"):
            return Response(b'{"trial_id":"trial-1","task_name":"phase3-harbor-smoke"}')
        if request.full_url.endswith("/trial/trial-1/exec"):
            return Response(b'{"stdout":"phase3-harbor-proof","return_code":0}')
        if request.full_url.endswith("/trial/trial-1/finalize"):
            return Response(b'{"reward":1.0}')
        if request.full_url.endswith("/trial/trial-1"):
            return Response(b'{"trial_id":"trial-1","task_name":"phase3-harbor-smoke"}')
        return Response(b'{"dataset":"harbor-test","ok":true}')

    monkeypatch.setenv("BREADBOARD_HARBOR_BASE_URL", "https://1.1.1.1/harbor")
    monkeypatch.setenv("BREADBOARD_HARBOR_TOKEN", "harbor-token")
    monkeypatch.setattr("breadboard.rl.phase3.integrations._open_without_proxies", fake_open)

    report = run_benchflow_harbor_attestation(env_package, target_run_id=TARGET)

    assert report["passed"] is False
    assert report["blocked_reason"] == "harbor_response_semantics_failed"
