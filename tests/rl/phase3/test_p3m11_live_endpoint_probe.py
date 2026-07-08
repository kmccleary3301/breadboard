from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from scripts.rl_phase3 import p3m11_live_endpoint_probe as probe


class _ProbeHandler(BaseHTTPRequestHandler):
    store: ClassVar[dict[str, bytes]] = {}

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib hook
        if self.path == "/verifier/ready" or self.path == "/scheduler/ready":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if self.path.startswith("/objects/"):
            payload = self.store.get(self.path)
            if payload is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(404)
        self.end_headers()

    def do_PUT(self) -> None:  # noqa: N802 - stdlib hook
        if not self.path.startswith("/objects/"):
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        self.store[self.path] = self.rfile.read(length)
        self.send_response(201)
        self.end_headers()

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib hook
        self.store.pop(self.path, None)
        self.send_response(204)
        self.end_headers()


@pytest.fixture()
def probe_server() -> str:
    _ProbeHandler.store = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProbeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_probe_requires_explicit_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BREADBOARD_VERIFIER_BASE_URL", "https://verifier.example")
    monkeypatch.setenv("BREADBOARD_VERIFIER_TOKEN", "redacted")

    metrics, errors = probe.collect_verifier_metrics()

    assert metrics["verifier_latency_seconds"] is None
    assert "BREADBOARD_VERIFIER_PROBE_PATH" in errors
    assert "verifier_probe_not_configured" in errors


def test_probe_redacts_query_and_userinfo() -> None:
    assert probe._redacted_url("https://user:secret@example.test:9443/ready?token=secret#frag") == "https://example.test:9443/ready"


def test_probe_rejects_schemeless_local_host_port() -> None:
    assert probe._endpoint_is_local("localhost:8080")
    assert probe._endpoint_is_local("127.0.0.1:9000")


def test_probe_collects_live_endpoint_metrics(monkeypatch: pytest.MonkeyPatch, probe_server: str) -> None:
    monkeypatch.setenv("PHASE3_TARGET_RUN_ID", "20260624T040000Z-slurm-243958")
    monkeypatch.setenv("PHASE3_COMMAND_ID", "phase3_p3m11_live_endpoint_probe_test")
    monkeypatch.setenv("BREADBOARD_VERIFIER_BASE_URL", probe_server)
    monkeypatch.setenv("BREADBOARD_VERIFIER_PROBE_PATH", "/verifier/ready")
    monkeypatch.setenv("BREADBOARD_VERIFIER_TOKEN", "redacted")
    monkeypatch.setenv("BREADBOARD_VERIFIER_TOKEN_SCHEME", "")
    monkeypatch.setenv("BREADBOARD_OBJECT_STORE_BASE_URL", probe_server)
    monkeypatch.setenv("BREADBOARD_OBJECT_STORE_BUCKET", "phase3-test")
    monkeypatch.setenv("BREADBOARD_OBJECT_STORE_TOKEN", "redacted")
    monkeypatch.setenv("BREADBOARD_OBJECT_STORE_TOKEN_SCHEME", "")
    monkeypatch.setenv("BREADBOARD_OBJECT_STORE_PUT_URL_TEMPLATE", f"{probe_server}/objects/{{bucket}}/{{key}}")
    monkeypatch.setenv("BREADBOARD_OBJECT_STORE_GET_URL_TEMPLATE", f"{probe_server}/objects/{{bucket}}/{{key}}")
    monkeypatch.setenv("BREADBOARD_OBJECT_STORE_DELETE_URL_TEMPLATE", f"{probe_server}/objects/{{bucket}}/{{key}}")
    monkeypatch.setenv("BREADBOARD_SCHEDULER_BASE_URL", probe_server)
    monkeypatch.setenv("BREADBOARD_SCHEDULER_PROBE_PATH", "/scheduler/ready")
    monkeypatch.setenv("BREADBOARD_SCHEDULER_TOKEN", "redacted")
    monkeypatch.setenv("BREADBOARD_SCHEDULER_TOKEN_SCHEME", "")

    monkeypatch.setattr(probe, "_endpoint_is_local", lambda url: False)
    verifier, verifier_errors = probe.collect_verifier_metrics()
    object_store, object_store_errors = probe.collect_object_store_metrics()
    scheduler, scheduler_errors = probe.collect_scheduler_metrics()

    assert verifier_errors == []
    assert object_store_errors == []
    assert scheduler_errors == []
    assert verifier["verifier_latency_seconds"] and verifier["verifier_latency_seconds"][0] >= 0
    assert verifier["endpoint"].endswith("/verifier/ready")
    assert object_store["object_store"] == "configured_http_object_store"
    assert object_store["object_store_writes"] == 1
    assert object_store["artifact_bytes"] > 0
    assert object_store["write_read_verified"] is True
    assert object_store["written_sha256"] == object_store["artifact_sha256"]
    assert object_store["readback_sha256"] == object_store["artifact_sha256"]
    assert scheduler["scheduler_control"]["endpoint_present"] is True
    assert scheduler["scheduler_control"]["token_present"] is True
    assert scheduler["scheduler_control"]["status"] == "ready"


def test_probe_rejects_local_object_store_urls(monkeypatch: pytest.MonkeyPatch, probe_server: str) -> None:
    monkeypatch.setenv("BREADBOARD_OBJECT_STORE_BASE_URL", probe_server)
    monkeypatch.setenv("BREADBOARD_OBJECT_STORE_BUCKET", "phase3-test")
    monkeypatch.setenv("BREADBOARD_OBJECT_STORE_TOKEN", "redacted")
    monkeypatch.setenv("BREADBOARD_OBJECT_STORE_PUT_URL_TEMPLATE", f"{probe_server}/objects/{{bucket}}/{{key}}")
    monkeypatch.setenv("BREADBOARD_OBJECT_STORE_GET_URL_TEMPLATE", f"{probe_server}/objects/{{bucket}}/{{key}}")
    monkeypatch.setenv("BREADBOARD_OBJECT_STORE_DELETE_URL_TEMPLATE", f"{probe_server}/objects/{{bucket}}/{{key}}")

    object_store, errors = probe.collect_object_store_metrics()

    assert {
        "object_store_base_endpoint_is_local",
        "object_store_put_endpoint_is_local",
        "object_store_get_endpoint_is_local",
        "object_store_delete_endpoint_is_local",
    }.issubset(errors)
    assert "object_store_probe_not_configured" not in errors
    assert object_store["write_read_verified"] is False
    assert object_store["object_store_writes"] == 0
    assert _ProbeHandler.store == {}


def test_probe_main_reports_blocked_without_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for key in list(probe.os.environ):
        if key.startswith("BREADBOARD_VERIFIER") or key.startswith("BREADBOARD_OBJECT_STORE") or key.startswith("BREADBOARD_SCHEDULER"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(probe, "OUT", tmp_path / "out")

    assert probe.main() == 0
    out = capsys.readouterr().out

    assert "PHASE3_COMPONENT_REPORT_JSON=" in out
    assert "verifier_latency_unavailable" in out
    assert "production_object_store_write_read_unavailable" in out
    assert "scheduler_control_unavailable" in out
