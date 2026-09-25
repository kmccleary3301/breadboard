from __future__ import annotations

import asyncio
import hashlib
import json
import os
import socket
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from breadboard_engine.compilation.contracts import canonical_json_bytes as jcs_bytes
from breadboard.rl.phase5.f2_composition import (
    F2CompositionError,
    ExecutableObservationInput,
    OpenF2Composition,
    RequestTemplateInput,
    SourceArtifact,
    TlsAuthorityInput,
    _read_pinned,
    _f1_prerequisite_bytes,
    _validate_secret_files,
    _write_exclusive,
    _prepare_callback_tls_runtime,
    _verify_executable_observation,
    canonical_json_bytes,
    sha256_bytes,
)
from breadboard.rl.harness.composition import (
    SecretHandleSpecV1,
    SecretHandlesV1,
    TlsCallbackRuntimeInputV1,
)
from breadboard.rl.phase5.f2_terminal import (
    F1_PREREQUISITE_ID,
    F1_PREREQUISITE_REF,
    F1_PREREQUISITE_ROOT,
)
from breadboard.rl.phase5.f2_composition import SecretValidationInput


def test_canonical_encoding_and_digest_are_deterministic() -> None:
    first = canonical_json_bytes({"z": [2, 1], "a": {"b": True}})
    second = canonical_json_bytes({"a": {"b": True}, "z": [2, 1]})
    assert first == second == b'{"a":{"b":true},"z":[2,1]}'
    assert sha256_bytes(first) == "sha256:" + hashlib.sha256(first).hexdigest()


def test_composition_prerequisite_matches_f2_export_contract() -> None:
    document = json.loads(
        _f1_prerequisite_bytes(F1_PREREQUISITE_REF, F1_PREREQUISITE_ROOT)
    )
    assert document == {
        "schema_version": "bb.rl.f2.f1-prerequisite.v1",
        "canonical_id": F1_PREREQUISITE_ID,
        "report_schema": "bb.rl.f1.ibm-exact-container-preflight-report.v3",
        "report_ref": F1_PREREQUISITE_REF,
        "canonical_root": F1_PREREQUISITE_ROOT,
    }


def test_source_artifact_rejects_mismatched_authority(tmp_path: Path) -> None:
    source = tmp_path / "authority.json"
    source.write_bytes(b"{}")
    authority = SourceArtifact(
        path=str(source), sha256="sha256:" + "0" * 64, media_type="application/json"
    )
    with pytest.raises(F2CompositionError, match="artifact digest mismatch"):
        _read_pinned(authority, canonical_json=True)


def test_secret_handles_require_distinct_0400_files_without_reading_them(tmp_path: Path) -> None:
    specs = SecretHandlesV1(records=(
        SecretHandleSpecV1(handle_id="api", purpose="api_bearer"),
        SecretHandleSpecV1(handle_id="callback", purpose="policy_callback", route_ids=("route",)),
        SecretHandleSpecV1(handle_id="receipt", purpose="receipt_signer"),
    ))
    paths: dict[str, str] = {}
    secret_values = {"api": b"api-secret-do-not-copy", "callback": b"callback-secret-do-not-copy", "receipt": b"r" * 32}
    for handle_id, value in secret_values.items():
        path = tmp_path / handle_id
        path.write_bytes(value)
        path.chmod(0o400)
        paths[handle_id] = str(path)
    validated = SecretValidationInput(handles=specs, files=paths)
    _validate_secret_files(validated)
    assert all(value not in canonical_json_bytes(validated.model_dump(mode="json")) for value in secret_values.values())
    Path(paths["api"]).chmod(0o600)
    with pytest.raises(F2CompositionError, match="0400 regular file"):
        _validate_secret_files(validated)


def test_tls_authority_requires_exact_non_loopback_literal_ip(tmp_path: Path) -> None:
    pem = tmp_path / "cert.pem"
    pem.write_text("public certificate observation", encoding="ascii")
    source = SourceArtifact(path=str(pem), sha256=sha256_bytes(pem.read_bytes()), media_type="application/x-pem-file")
    common = {
        "route_id": "policy-route",
        "ca_certificate": source,
        "leaf_certificate": source,
        "expected_leaf_der_sha256": "sha256:" + "1" * 64,
        "minimum_tls_version": "TLSv1.3",
        "cipher_suite": "TLS_AES_256_GCM_SHA384",
        "dedicated_single_leaf_ca": True,
    }
    with pytest.raises(ValidationError, match="non-loopback literal IP"):
        TlsAuthorityInput(target_ip="127.0.0.1", **common)
    with pytest.raises(ValidationError):
        TlsAuthorityInput(target_ip="policy.example", **common)
    assert TlsAuthorityInput(target_ip="10.2.3.4", **common).target_ip == "10.2.3.4"


@pytest.mark.parametrize(
    "payload",
    [
        {"selector": "row-choice"},
        {"nested": {"runtime_digest": "sha256:" + "0" * 64}},
        {"items": [{"policy": "row-policy"}]},
        {"config_ref": "sha256:" + "0" * 64},
    ],
)
def test_generic_request_row_cannot_override_authority(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="may not carry authority"):
        RequestTemplateInput(task_input=payload)


def test_wrapper_executable_authority_rejects_writable_or_relative_binary() -> None:
    base = {
        "path": "/usr/bin/env",
        "sha256": "sha256:" + "1" * 64,
        "device": "1",
        "inode": "2",
        "ctime_ns": "1700000000000000000",
        "size_bytes": 4,
        "mode": 0o755,
        "owner_uid": 0,
    }
    authority = ExecutableObservationInput(**base)
    assert authority.path == "/usr/bin/env"
    assert b'"ctime_ns":"1700000000000000000"' in jcs_bytes(
        authority.model_dump(mode="json")
    )
    with pytest.raises(ValidationError, match="string"):
        ExecutableObservationInput(**{**base, "ctime_ns": 3})
    with pytest.raises(ValidationError, match="not exact and sealed"):
        ExecutableObservationInput(**{**base, "mode": 0o775})
    with pytest.raises(ValidationError, match="not exact and sealed"):
        ExecutableObservationInput(**{**base, "path": "usr/bin/env"})


def test_open_composition_retains_lease_until_cleanup_receipt() -> None:
    lifecycle = SimpleNamespace(
        outer_bridge_lease={"lease_id": "lease"},
        prebound_service_sockets={"harness": {"observed_port": 1234}},
        outer_bridge_cleanup_receipt=None,
    )

    async def close() -> None:
        lifecycle.outer_bridge_cleanup_receipt = {"lease_id": "lease"}

    lifecycle.close = close
    callback_socket_fd = os.open("/dev/null", os.O_RDONLY)
    callback_key_fd = os.open("/dev/null", os.O_RDONLY)
    session = OpenF2Composition(
        None,  # type: ignore[arg-type]
        lifecycle,
        SimpleNamespace(),
        callback_socket_fd,
        callback_key_fd,
        {"handle_id": "callback-tls-key"},
    )
    assert session.outer_bridge_lease["lease_id"] == "lease"
    assert session.prebound_service_sockets["harness"]["observed_port"] == 1234
    asyncio.run(session.close())
    assert session.outer_bridge_cleanup_receipt["lease_id"] == "lease"
    with pytest.raises(OSError):
        os.fstat(callback_socket_fd)
    with pytest.raises(OSError):
        os.fstat(callback_key_fd)


def test_private_key_handles_are_runtime_only() -> None:
    handles = SecretHandlesV1(records=(
        SecretHandleSpecV1(handle_id="api", purpose="api_bearer"),
        SecretHandleSpecV1(
            handle_id="evidence-key",
            purpose="evidence_receipt_signing_key",
        ),
        SecretHandleSpecV1(
            handle_id="observation-key",
            purpose="callback_observation_signing_key",
        ),
        SecretHandleSpecV1(handle_id="receipt", purpose="receipt_signer"),
        SecretHandleSpecV1(
            handle_id="tls-key",
            purpose="callback_tls_private_key",
        ),
    ))
    persisted = SecretValidationInput(
        handles=handles,
        files={"api": "/run/secrets/api", "receipt": "/run/secrets/receipt"},
    )
    assert "tls-key" not in persisted.files
    assert "evidence-key" not in persisted.files
    assert "observation-key" not in persisted.files
    with pytest.raises(
        ValidationError,
        match="persisted secret files must exactly cover non-live handle IDs",
    ):
        SecretValidationInput(
            handles=handles,
            files={
                "api": "/run/secrets/api",
                "receipt": "/run/secrets/receipt",
                "tls-key": "/run/secrets/tls-key",
            },
        )


def test_callback_tls_policy_is_server_auth_plus_bearer_not_mutual_tls() -> None:
    properties = TlsCallbackRuntimeInputV1.model_json_schema()["$defs"][
        "TlsCallbackPolicyV1"
    ]["properties"]
    assert set(properties) == {
        "minimum_tls_version",
        "maximum_tls_version",
        "server_certificate_verification_required",
        "hostname_verification_required",
        "bearer_authentication_required",
        "mutual_tls_required",
    }
    assert properties["server_certificate_verification_required"]["const"] is True
    assert properties["hostname_verification_required"]["const"] is True
    assert properties["bearer_authentication_required"]["const"] is True
    assert properties["mutual_tls_required"]["const"] is False


def test_generic_request_row_allows_only_task_and_context_data() -> None:
    row = RequestTemplateInput(
        task_input={"task_id": "ibm-terminal", "prompt": "Implement the task."},
        context={"attempt": 1},
    )
    encoded = canonical_json_bytes({"breadboard_v2": row.model_dump(mode="json")}) + b"\n"
    assert encoded.count(b"\n") == 1
    assert not any(key.encode() in encoded for key in ("selector", "authority_bundle", "secret", "tls"))


def test_exclusive_writer_refuses_overwrite_and_fsyncs_complete_bytes(tmp_path: Path) -> None:
    target = tmp_path / "packet.json"
    _write_exclusive(target, b'{"packet":1}')
    assert target.read_bytes() == b'{"packet":1}'
    with pytest.raises(FileExistsError):
        _write_exclusive(target, b'{"packet":2}')
    assert target.read_bytes() == b'{"packet":1}'


def test_production_module_has_no_fixture_dependency() -> None:
    module_path = Path(__file__).parents[3] / "breadboard" / "rl" / "phase5" / "f2_composition.py"
    source = module_path.read_text(encoding="utf-8")
    assert "production_composition_fixture" not in source
    assert "tests.rl" not in source


def test_verify_executable_observation_accepts_canonical_decimal_strings(
    tmp_path: Path,
) -> None:
    executable_file = tmp_path / "helper_bin"
    executable_file.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable_file.chmod(0o555)
    st = executable_file.stat()
    digest = "sha256:" + hashlib.sha256(executable_file.read_bytes()).hexdigest()
    observation = ExecutableObservationInput(
        path=str(executable_file),
        sha256=digest,
        device=str(st.st_dev),
        inode=str(st.st_ino),
        ctime_ns=str(st.st_ctime_ns),
        size_bytes=st.st_size,
        mode=stat.S_IMODE(st.st_mode),
        owner_uid=st.st_uid,
    )
    _verify_executable_observation(observation)

    mismatched = ExecutableObservationInput(
        path=str(executable_file),
        sha256=digest,
        device=str(st.st_dev),
        inode=str(st.st_ino + 1),
        ctime_ns=str(st.st_ctime_ns),
        size_bytes=st.st_size,
        mode=stat.S_IMODE(st.st_mode),
        owner_uid=st.st_uid,
    )
    with pytest.raises(
        F2CompositionError, match="wrapper executable runtime observation mismatch"
    ):
        _verify_executable_observation(mismatched)


def test_prepare_callback_tls_runtime_accepts_canonical_decimal_socket_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock
    from breadboard.rl.harness.composition import (
        PreboundServiceSocketPlanV1,
        _canonical_bytes,
    )

    sock_file = tmp_path / "mock_socket"
    sock_file.write_bytes(b"")
    st = sock_file.stat()
    sock_mode = stat.S_IFSOCK | 0o600

    plan_dict = {
        "schema_version": "bb.rl.harness-prebound-service-socket-plan.v2",
        "role": "callback_tls",
        "gateway": "10.0.0.1",
        "observed_port": 8443,
        "family": "AF_INET",
        "socket_type": "SOCK_STREAM",
        "protocol": "IPPROTO_TCP",
        "socket_device": str(st.st_dev),
        "socket_inode": str(st.st_ino),
        "socket_mode": sock_mode,
        "socket_owner_uid": st.st_uid,
        "getsockname_host": "10.0.0.1",
        "getsockname_port": 8443,
        "ip_freebind": True,
    }
    plan_dict["socket_plan_id"] = (
        "sha256:" + hashlib.sha256(_canonical_bytes(plan_dict)).hexdigest()
    )
    plan = PreboundServiceSocketPlanV1.model_validate(plan_dict, strict=True)

    spec = MagicMock()
    spec.prebound_service_socket_plans = (plan,)
    spec.authority.policy_http.path = "/nonexistent"
    runtime = MagicMock()
    runtime.socket_role = "callback_tls"
    runtime.host = "10.0.0.1"
    runtime.observed_port = 8443
    runtime.socket_plan_id = plan.socket_plan_id
    runtime.private_key_secret_handle_id = "k1"
    live_secrets = {"k1": str(sock_file)}

    mock_sock = MagicMock()
    mock_sock.family = socket.AF_INET
    mock_sock.type = socket.SOCK_STREAM
    mock_sock.getsockopt.return_value = 1
    mock_sock.getsockname.return_value = ("10.0.0.1", 8443)
    monkeypatch.setattr(socket, "fromfd", MagicMock(return_value=mock_sock))

    sock_file_fd = -1
    orig_fstat = os.fstat

    def custom_fstat(fd: int) -> Any:
        res = orig_fstat(fd)
        if sock_file_fd >= 0 and fd == sock_file_fd:
            m = MagicMock()
            m.st_dev = res.st_dev
            m.st_ino = res.st_ino
            m.st_mode = sock_mode
            m.st_uid = res.st_uid
            return m
        return res

    monkeypatch.setattr(os, "fstat", custom_fstat)

    sock_file_fd = os.open(sock_file, os.O_RDONLY)
    try:
        with pytest.raises(Exception) as excinfo:
            _prepare_callback_tls_runtime(
                spec,
                runtime,
                live_secret_files=live_secrets,
                socket_fd=sock_file_fd,
                private_key_fd=sock_file_fd,
            )
        assert not (
            isinstance(excinfo.value, F2CompositionError)
            and "callback TLS socket plan observation mismatch" in str(excinfo.value)
        )

        mismatched_plan_dict = dict(plan_dict)
        mismatched_plan_dict.pop("socket_plan_id", None)
        mismatched_plan_dict["socket_device"] = str(st.st_dev + 1)
        mismatched_plan_dict["socket_plan_id"] = (
            "sha256:"
            + hashlib.sha256(_canonical_bytes(mismatched_plan_dict)).hexdigest()
        )
        mismatched_plan = PreboundServiceSocketPlanV1.model_validate(
            mismatched_plan_dict, strict=True
        )
        spec.prebound_service_socket_plans = (mismatched_plan,)
        runtime.socket_plan_id = mismatched_plan.socket_plan_id

        with pytest.raises(
            F2CompositionError,
            match="callback TLS socket plan observation mismatch",
        ):
            _prepare_callback_tls_runtime(
                spec,
                runtime,
                live_secret_files=live_secrets,
                socket_fd=sock_file_fd,
                private_key_fd=sock_file_fd,
            )
    finally:
        os.close(sock_file_fd)
