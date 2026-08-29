from __future__ import annotations

import contextlib
import hashlib
import json
import os
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from fastapi.testclient import TestClient

from breadboard.rl.harness.runners.base import freeze_json_object
from breadboard.rl.harness.runners.conductor import _supported_output_item
from breadboard.rl.harness.composition import load_production_composition
from breadboard.rl.harness.qualification import (
    MaterializedProductionCompositionFixture,
    materialize_production_composition_fixture,
)


_SEALED_EXEC_SUPPORTED = sys.platform.startswith("linux")
_REQUIRES_SEALED_EXEC = pytest.mark.skipif(
    not _SEALED_EXEC_SUPPORTED,
    reason="trusted-process sealed executable snapshots require Linux",
)


_POLICY_HTTP_VERSION = "HTTP/1.1"
_TERMINAL_STATES = {"completed", "failed", "cancelled", "closed"}
_CONDUCTOR_COMPLETION_PAYLOAD = {
    "output": [
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "loopback-complete"}],
        }
    ]
}


def _fd_identities() -> set[tuple[int, int, str]]:
    identities: set[tuple[int, int, str]] = set()
    for entry in Path("/dev/fd").iterdir():
        try:
            current = entry.stat()
            target = os.readlink(entry)
        except OSError:
            continue
        identities.add((current.st_dev, current.st_ino, target))
    return identities


def _runtime_children(
    fixture: MaterializedProductionCompositionFixture,
) -> set[Path]:
    return {
        child
        for name in ("workspace", "lease", "security_profile")
        for child in fixture.installed_roots[name].iterdir()
    }


def _recorded_processes(paths: set[Path]) -> set[tuple[int, str]]:
    recorded: set[tuple[int, str]] = set()
    for path in paths:
        candidates = (path,) if path.is_file() else tuple(path.rglob("*"))
        for candidate in candidates:
            if not candidate.is_file():
                continue
            try:
                payload = json.loads(candidate.read_bytes())
            except (OSError, ValueError):
                continue
            stack = [payload]
            while stack:
                value = stack.pop()
                if isinstance(value, dict):
                    pid = value.get("pid")
                    start = value.get("process_start_identity") or value.get(
                        "process_start_time"
                    )
                    if isinstance(pid, int) and start is not None:
                        recorded.add((pid, str(start)))
                    stack.extend(value.values())
                elif isinstance(value, list):
                    stack.extend(value)
    return recorded


def _descendant_processes(root_pid: int) -> set[tuple[int, str]]:
    records: dict[int, tuple[int, str]] = {}
    for stat_path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat_path.read_text().split()
            pid = int(fields[0])
            records[pid] = (int(fields[3]), fields[21])
        except (OSError, ValueError, IndexError):
            continue
    descendants: set[int] = set()
    changed = True
    while changed:
        changed = False
        for pid, (parent, _) in records.items():
            if pid not in descendants and (parent == root_pid or parent in descendants):
                descendants.add(pid)
                changed = True
    return {(pid, records[pid][1]) for pid in descendants}


def _assert_processes_dead(processes: set[tuple[int, str]]) -> None:
    for pid, _ in processes:
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


def _http_json(
    method: str,
    url: str,
    *,
    token: str,
    body: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    payload = None if body is None else json.dumps(dict(body)).encode()
    request = Request(
        url,
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            **({"Content-Type": "application/json"} if payload is not None else {}),
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            assert response.status == 200
            return json.loads(response.read())
    except HTTPError as exc:
        pytest.fail(f"{method} {url} returned {exc.code}: {exc.read()!r}")


def _secret_arguments(fixture: MaterializedProductionCompositionFixture) -> list[str]:
    return [
        argument
        for handle, path in sorted(fixture.secret_files.items())
        for argument in ("--secret-file", f"{handle}={path}")
    ]


def _cli_command(
    fixture: MaterializedProductionCompositionFixture, command: str
) -> list[str]:
    return [
        sys.executable,
        "-m",
        "breadboard.rl.harness",
        command,
        "--composition-ref",
        str(fixture.composition_ref_path),
        *_secret_arguments(fixture),
    ]


def _assert_no_authority_leak(
    fixture: MaterializedProductionCompositionFixture, value: object
) -> None:
    serialized = json.dumps(value, sort_keys=True, default=str)
    forbidden = [
        *(secret.decode("utf-8") for secret in fixture.secret_seed_bytes.values()),
        *(str(path) for path in fixture.secret_paths.values()),
        *(str(path) for path in fixture.installed_roots.values()),
        str(fixture.composition_ref_path),
        str(fixture.composition_manifest_path),
        str(fixture.object_cas_root),
        str(fixture.expected_executable_identity.path),
    ]
    assert not [item for item in forbidden if item and item in serialized]


def _reserve_loopback_port() -> int:
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        return int(reserved.getsockname()[1])


class _PolicyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


@contextlib.contextmanager
def _policy_https_server(
    fixture: MaterializedProductionCompositionFixture,
) -> Iterator[tuple[str, int, list[dict[str, Any]]]]:
    requests: list[dict[str, Any]] = []
    expected_authorization = f"Bearer {fixture.policy_callback_secret}"
    completion_payload = _CONDUCTOR_COMPLETION_PAYLOAD
    completion_bytes = json.dumps(
        completion_payload, sort_keys=True, separators=(",", ":")
    ).encode()
    responses = (
        dict(fixture.policy_response_body),
        {
            "response_digest": "sha256:" + hashlib.sha256(completion_bytes).hexdigest(),
            "response_payload": completion_payload,
        },
    )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = _POLICY_HTTP_VERSION

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            requests.append(
                {
                    "path": self.path,
                    "authorization": self.headers.get("Authorization"),
                    "body": json.loads(body),
                }
            )
            if self.headers.get("Authorization") != expected_authorization:
                self.send_response(401)
                self.send_header("Connection", "close")
                self.send_header("Content-Length", "0")
                self.close_connection = True
                self.end_headers()
                return
            selected = responses[min(len(requests) - 1, len(responses) - 1)]
            response = json.dumps(
                selected, sort_keys=True, separators=(",", ":")
            ).encode()
            self.send_response(200)
            self.send_header("Connection", "close")
            self.close_connection = True
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = _PolicyServer(
        (fixture.policy_server_host, fixture.policy_server_port), Handler
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(
        fixture.tls_server_certificate_path, fixture.tls_server_key_path
    )
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield str(host), int(port), requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_policy_callback_completion_is_exact_conductor_wire() -> None:
    item = freeze_json_object(
        _CONDUCTOR_COMPLETION_PAYLOAD["output"][0],
        field_name="policy output",
    )
    assert _supported_output_item(item)
    assert not _supported_output_item(
        freeze_json_object(
            {"type": "message", "text": "terminal-style-is-invalid"},
            field_name="policy output",
        )
    )
    canonical = json.dumps(
        _CONDUCTOR_COMPLETION_PAYLOAD,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    envelope = {
        "response_digest": "sha256:" + hashlib.sha256(canonical).hexdigest(),
        "response_payload": _CONDUCTOR_COMPLETION_PAYLOAD,
    }
    assert envelope["response_digest"] == (
        "sha256:" + hashlib.sha256(canonical).hexdigest()
    )


def test_policy_callback_status_line_is_exact_http11_and_closes() -> None:
    class StatusHandler(BaseHTTPRequestHandler):
        protocol_version = _POLICY_HTTP_VERSION

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Connection", "close")
            self.send_header("Content-Length", "0")
            self.close_connection = True
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = _PolicyServer(("127.0.0.1", 0), StatusHandler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    try:
        with socket.create_connection(server.server_address, timeout=5) as client:
            client.sendall(
                b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
            )
            response = b""
            while chunk := client.recv(4096):
                response += chunk
        assert response.startswith(b"HTTP/1.1 200 ")
        assert b"\r\nConnection: close\r\n" in response
    finally:
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _wait_for_status(
    client: TestClient, episode_id: str, headers: Mapping[str, str]
) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    while True:
        response = client.get(f"/v2/episodes/{episode_id}", headers=dict(headers))
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["state"] in _TERMINAL_STATES:
            return payload
        assert time.monotonic() < deadline, payload
        time.sleep(0.02)


def _exercise_episode(
    fixture: MaterializedProductionCompositionFixture,
    *,
    close_composition: bool = True,
) -> tuple[str, dict[str, Any]]:
    composition = load_production_composition(
        str(fixture.composition_ref_path), fixture.secret_files
    )
    headers = {"Authorization": f"Bearer {fixture.api_bearer}"}
    episode_id = str(fixture.create_body["resolution"]["episode_id"])  # type: ignore[index]
    try:
        with TestClient(composition.app) as client:
            created = client.post(
                "/v2/episodes", json=dict(fixture.create_body), headers=headers
            )
            assert created.status_code == 200, created.text
            create_payload = created.json()
            run = client.post(
                f"/v2/episodes/{episode_id}:run",
                json={
                    "schema_version": "bb.rl.episode.v2",
                    "create_fingerprint": create_payload["create_fingerprint"],
                    "task_input": {"prompt": "public production lifecycle"},
                    "context": {"acceptance": "production-composition"},
                },
                headers=headers,
            )
            assert run.status_code == 200, run.text
            terminal = _wait_for_status(client, episode_id, headers)
            assert terminal["state"] == "closed"
            assert run.json()["completed_envelope_ref"]["sha256"].startswith("sha256:")
            assert run.json()["closed_envelope_ref"]["sha256"].startswith("sha256:")
            completed_envelope = client.get(
                f"/v2/episodes/{episode_id}/envelopes/completed", headers=headers
            )
            assert completed_envelope.status_code == 200, completed_envelope.text
            assert run.json()["primary_disposition"] == "succeeded", run.text
            assert completed_envelope.json()["cleanup_disposition"] == "pending"
            closed = client.delete(f"/v2/episodes/{episode_id}", headers=headers)
            assert closed.status_code == 200, closed.text
            closed_payload = closed.json()
            assert closed_payload["closed_envelope_ref"]["sha256"].startswith("sha256:")
            assert closed_payload["cleanup_disposition"] == "released"
            closed_envelope = client.get(
                f"/v2/episodes/{episode_id}/envelopes/closed", headers=headers
            )
            assert closed_envelope.status_code == 200, closed_envelope.text
            assert closed_envelope.json()["cleanup_disposition"] == "released"
            assert not _runtime_children(fixture)
        _assert_no_authority_leak(
            fixture,
            [
                create_payload,
                run.json(),
                terminal,
                completed_envelope.json(),
                closed_payload,
                closed_envelope.json(),
            ],
        )
        return episode_id, closed_payload
    finally:
        if close_composition:
            import asyncio

            asyncio.run(composition.close())


@pytest.mark.skipif(
    _SEALED_EXEC_SUPPORTED,
    reason="fail-closed assertion applies when sealed executable snapshots are unavailable",
)
def test_public_loader_fails_closed_before_allocation_without_sealed_exec(
    tmp_path: Path,
) -> None:
    fixture = materialize_production_composition_fixture(tmp_path)
    before = _fd_identities()
    composition = load_production_composition(
        str(fixture.composition_ref_path), fixture.secret_files
    )
    import asyncio

    try:
        with TestClient(composition.app) as client:
            rejected = client.post(
                "/v2/episodes",
                json=dict(fixture.create_body),
                headers={"Authorization": f"Bearer {fixture.api_bearer}"},
            )
            assert rejected.status_code == 503, rejected.text
            assert rejected.json()["code"] == "runtime_unsupported"
            _assert_no_authority_leak(fixture, rejected.json())
            assert not _runtime_children(fixture)
    finally:
        asyncio.run(composition.close())
        asyncio.run(composition.close())
    assert _fd_identities() == before


@_REQUIRES_SEALED_EXEC
def test_public_loader_app_restart_reconcile_and_double_close(tmp_path: Path) -> None:
    fixture = materialize_production_composition_fixture(
        tmp_path, policy_server_port=_reserve_loopback_port()
    )
    with _policy_https_server(fixture) as (_, _, policy_requests):
        episode_id, first_closed = _exercise_episode(fixture)
        composition = load_production_composition(
            str(fixture.composition_ref_path), fixture.secret_files
        )
        headers = {"Authorization": f"Bearer {fixture.api_bearer}"}
        try:
            with TestClient(composition.app) as client:
                reconciled = client.get(f"/v2/episodes/{episode_id}", headers=headers)
                assert reconciled.status_code == 200, reconciled.text
                assert reconciled.json()["state"] == "closed"
                assert reconciled.json()["cleanup_disposition"] == "released"
                repeated = client.delete(f"/v2/episodes/{episode_id}", headers=headers)
                assert repeated.status_code == 200, repeated.text
                assert repeated.json() == first_closed
        finally:
            import asyncio

            asyncio.run(composition.close())
            asyncio.run(composition.close())
    assert policy_requests
    assert all(
        request["authorization"] == f"Bearer {fixture.policy_callback_secret}"
        for request in policy_requests
    )
    assert all(not path.exists() for path in fixture.cleanup_paths)


@_REQUIRES_SEALED_EXEC
def test_restart_readiness_reconciles_a_ready_episode_before_serving(
    tmp_path: Path,
) -> None:
    fixture = materialize_production_composition_fixture(tmp_path)
    headers = {"Authorization": f"Bearer {fixture.api_bearer}"}
    first = load_production_composition(
        str(fixture.composition_ref_path), fixture.secret_files
    )
    import asyncio

    try:
        with TestClient(first.app) as client:
            created = client.post(
                "/v2/episodes", json=dict(fixture.create_body), headers=headers
            )
            assert created.status_code == 200, created.text
            episode_id = created.json()["episode_id"]
            assert created.json()["state"] == "ready"
    finally:
        asyncio.run(first.close())

    restarted = load_production_composition(
        str(fixture.composition_ref_path), fixture.secret_files
    )
    try:
        with TestClient(restarted.app) as client:
            ready = client.get("/healthz")
            assert ready.status_code == 200
            reconciled = client.get(f"/v2/episodes/{episode_id}", headers=headers)
            assert reconciled.status_code == 200, reconciled.text
            assert reconciled.json()["state"] == "quarantined"
            assert reconciled.json()["cleanup_disposition"] == "quarantined"
            assert not _runtime_children(fixture)
    finally:
        asyncio.run(restarted.close())
        asyncio.run(restarted.close())


def test_materialized_unknown_candidate_name_is_absent_from_production_sources(
    tmp_path: Path,
) -> None:
    fixture = materialize_production_composition_fixture(tmp_path)
    production_roots = tuple(
        root
        for root in (
            Path("agent_configs"),
            Path("agentic_coder_prototype"),
            Path("breadboard"),
            Path("breadboard_ext"),
            Path("breadboard_sdk"),
            Path("config"),
            Path("conformance"),
            Path("container_templates"),
            Path("contracts"),
            Path("examples"),
            Path("implementations"),
            Path("scripts"),
            Path("sdk"),
            Path("tool_calling"),
            Path("tools"),
        )
        if root.is_dir()
    )
    suffixes = {".json", ".py", ".sh", ".toml", ".yaml", ".yml"}
    occurrences = [
        path
        for root in production_roots
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in suffixes
        and fixture.generated_candidate_name.encode() in path.read_bytes()
    ]
    assert occurrences == []


@pytest.mark.parametrize("stage", ["secret", "security-profile", "executable"])
def test_public_loader_staged_bootstrap_failures_release_everything(
    tmp_path: Path, stage: str
) -> None:
    fixture = materialize_production_composition_fixture(tmp_path)
    before = _fd_identities()
    if stage == "secret":
        secret = next(iter(fixture.secret_paths.values()))
        secret.chmod(0o600)
    elif stage == "security-profile":
        (fixture.installed_roots["security_profile"] / "unexpected").write_text("x")
    else:
        executable = fixture.expected_executable_identity.path
        executable.chmod(0o700)
        executable.write_bytes(
            executable.read_bytes() + b"\n# changed after admission\n"
        )
        executable.chmod(0o500)
    with pytest.raises((OSError, ValueError)) as caught:
        load_production_composition(
            str(fixture.composition_ref_path), fixture.secret_files
        )
    _assert_no_authority_leak(fixture, str(caught.value))
    assert _fd_identities() == before
    assert all(not path.exists() for path in fixture.cleanup_paths)


def test_cli_inspect_is_cwd_env_independent_canonical_and_secret_free(
    tmp_path: Path,
) -> None:
    fixture = materialize_production_composition_fixture(tmp_path)
    command = _cli_command(fixture, "inspect")
    clean_env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(Path.cwd()),
        "HOME": str(tmp_path / "home-a"),
        "TMPDIR": str(tmp_path / "tmp-a"),
    }
    Path(clean_env["HOME"]).mkdir()
    Path(clean_env["TMPDIR"]).mkdir()
    first = subprocess.run(
        command,
        cwd=Path.cwd(),
        env=clean_env,
        check=True,
        capture_output=True,
        timeout=30,
    )
    second_env = {
        **clean_env,
        "HOME": str(tmp_path / "home-b"),
        "TMPDIR": str(tmp_path / "tmp-b"),
        "BREADBOARD_UNRELATED": "must-not-affect-authority",
    }
    Path(second_env["HOME"]).mkdir()
    Path(second_env["TMPDIR"]).mkdir()
    second = subprocess.run(
        command,
        cwd=tmp_path,
        env=second_env,
        check=True,
        capture_output=True,
        timeout=30,
    )
    assert first.stdout == second.stdout
    assert first.stderr == second.stderr == b""
    document = json.loads(first.stdout)
    assert (
        first.stdout
        == json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    )
    assert document["schema_version"] == "bb.rl.harness-composed.v1"
    _assert_no_authority_leak(fixture, document)


def _port_accepts(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.1):
            return True
    except OSError:
        return False


@_REQUIRES_SEALED_EXEC
def test_cli_serve_sigterm_leaves_no_socket_child_lease_or_secret_residue(
    tmp_path: Path,
) -> None:
    policy_port = _reserve_loopback_port()
    server_port = _reserve_loopback_port()
    assert policy_port != server_port
    fixture = materialize_production_composition_fixture(
        tmp_path,
        policy_server_port=policy_port,
        server_port=server_port,
        long_running=True,
    )
    host = fixture.server_host
    port = fixture.server_port
    assert (host, port) != (
        fixture.policy_server_host,
        fixture.policy_server_port,
    )
    with _policy_https_server(fixture):
        process = subprocess.Popen(
            _cli_command(fixture, "serve"),
            cwd=Path.cwd(),
            env={
                "HOME": str(tmp_path / "serve-home"),
                "PATH": os.environ["PATH"],
                "PYTHONPATH": str(Path.cwd()),
                "TMPDIR": str(tmp_path),
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            deadline = time.monotonic() + 15
            while process.poll() is None and not _port_accepts(host, port):
                assert time.monotonic() < deadline
                time.sleep(0.02)
            assert process.poll() is None
            base = f"http://{host}:{port}"
            created = _http_json(
                "POST",
                f"{base}/v2/episodes",
                token=fixture.api_bearer,
                body=fixture.create_body,
            )
            episode_id = str(created["episode_id"])
            run_finished = threading.Event()

            def run_episode() -> None:
                try:
                    _http_json(
                        "POST",
                        f"{base}/v2/episodes/{episode_id}:run",
                        token=fixture.api_bearer,
                        body={
                            "schema_version": "bb.rl.episode.v2",
                            "create_fingerprint": created["create_fingerprint"],
                            "task_input": {"prompt": "serve SIGTERM lifecycle"},
                            "context": {"acceptance": "sigterm"},
                        },
                    )
                except (OSError, AssertionError):
                    pass
                finally:
                    run_finished.set()

            run_thread = threading.Thread(target=run_episode)
            run_thread.start()
            child_deadline = time.monotonic() + 10
            owned_children: set[Path] = set()
            owned_processes: set[tuple[int, str]] = set()
            while not owned_processes:
                assert process.poll() is None
                assert time.monotonic() < child_deadline
                time.sleep(0.02)
                owned_children |= _runtime_children(fixture)
                owned_processes = _recorded_processes(
                    owned_children
                ) | _descendant_processes(process.pid)
            os.killpg(process.pid, signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=15)
            run_thread.join(timeout=5)
            assert run_finished.is_set()
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
    assert process.returncode == 0, (stdout, stderr)
    with pytest.raises(ProcessLookupError):
        os.killpg(process.pid, 0)
    restarted = load_production_composition(
        str(fixture.composition_ref_path), fixture.secret_files
    )
    import asyncio

    try:
        with TestClient(restarted.app) as client:
            ready = client.get("/healthz")
            assert ready.status_code == 200
            recovered = client.get(
                f"/v2/episodes/{episode_id}",
                headers={"Authorization": f"Bearer {fixture.api_bearer}"},
            )
            assert recovered.status_code == 200, recovered.text
            recovered_payload = recovered.json()
            assert recovered_payload["state"] == "closed"
            assert recovered_payload["primary_disposition"] == "cancelled"
            assert recovered_payload["cleanup_disposition"] == "released"
            coordinator = restarted.app.state.episode_service._coordinators[episode_id]
            assert coordinator.last_event.cancel_reason == "service shutdown"
            assert coordinator.last_event.primary_fact is None
            closed_envelope = client.get(
                f"/v2/episodes/{episode_id}/envelopes/closed",
                headers={"Authorization": f"Bearer {fixture.api_bearer}"},
            )
            assert closed_envelope.status_code == 200, closed_envelope.text
            closed_payload = closed_envelope.json()
            assert closed_payload["primary_outcome"] == "cancelled"
            assert closed_payload["cleanup_required_resources"] == [
                "child_verifier",
                "runtime",
                "workspace",
                "cache_holder",
                "lease_record",
            ]
            assert [
                (step["resource"], step["state"], step["detail"])
                for step in closed_payload["cleanup_receipt"]["steps"]
            ] == [
                ("child_verifier", "already_released", ""),
                ("runtime", "released", ""),
                ("workspace", "released", ""),
                ("cache_holder", "released", ""),
                ("lease_record", "released", ""),
            ]
            closed = client.delete(
                f"/v2/episodes/{episode_id}",
                headers={"Authorization": f"Bearer {fixture.api_bearer}"},
            )
            assert closed.status_code == 200, closed.text
            assert closed.json()["cleanup_disposition"] == "released"
    finally:
        asyncio.run(restarted.close())
    _assert_processes_dead(owned_processes)
    assert all(not path.exists() for path in owned_children)
    assert not _runtime_children(fixture)
    assert not _port_accepts(host, port)
    _assert_no_authority_leak(
        fixture,
        {
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
        },
    )
