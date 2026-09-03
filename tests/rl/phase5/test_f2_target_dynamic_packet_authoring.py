from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from breadboard_engine.compilation.contracts import canonical_json_bytes
from breadboard.rl.harness.composition import InstalledV1
from breadboard.rl.harness.sandbox import SandboxNetworkPolicy, SandboxSecurityPolicy
from breadboard.rl.phase5.f2_authority_authoring import (
    F2C4TargetDynamicObservations,
    F2C4TargetDynamicPlanInput,
)
from scripts.rl_phase5 import author_f2_target_dynamic_packet as author


def _digest(value: bytes | str) -> str:
    raw = value.encode() if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _file_authority(path: Path) -> dict[str, object]:
    observed = path.stat()
    mode = stat.S_IMODE(observed.st_mode)
    return {
        "path": os.fspath(path),
        "digest": _digest(path.read_bytes()),
        "owner_uid": observed.st_uid,
        "mode": mode,
        "executable": bool(mode & 0o111),
    }


def _security(uid: int, profile: str) -> dict[str, object]:
    seccomp = {"defaultAction": "SCMP_ACT_ERRNO", "syscalls": []}
    seccomp_digest = _digest(canonical_json_bytes(seccomp))
    projection = {
        "uid": uid,
        "gid": uid,
        "read_only_root": True,
        "drop_all_capabilities": True,
        "no_new_privileges": True,
        "seccomp_digest": seccomp_digest,
        "apparmor_profile": profile,
        "selinux_label": None,
        "namespace_flags": ["pid", "mount", "ipc", "uts"],
        "privileged": False,
        "devices": [],
        "docker_socket_forbidden": True,
        "tmpfs_mounts": [["/tmp", "rw,noexec,nosuid,size=1048576"]],
        "snapshot_max_depth": 8,
        "snapshot_max_files": 64,
        "snapshot_max_inodes": 128,
    }
    return {
        "expected_policy_digest": SandboxSecurityPolicy.derive_digest(projection),
        "uid": uid,
        "gid": uid,
        "read_only_root": True,
        "drop_all_capabilities": True,
        "no_new_privileges": True,
        "seccomp_document": seccomp,
        "expected_seccomp_digest": seccomp_digest,
        "apparmor_profile": profile,
        "selinux_label": None,
        "namespace_flags": ["pid", "mount", "ipc", "uts"],
        "privileged": False,
        "devices": [],
        "docker_socket_forbidden": True,
        "tmpfs_mounts": [["/tmp", "rw,noexec,nosuid,size=1048576"]],
        "snapshot_max_depth": 8,
        "snapshot_max_files": 64,
        "snapshot_max_inodes": 128,
    }


def _socket_plan(role: str, port: int) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "bb.rl.harness-prebound-service-socket-plan.v1",
        "role": role,
        "gateway": "10.91.0.1",
        "observed_port": port,
        "family": "AF_INET",
        "socket_type": "SOCK_STREAM",
        "protocol": "IPPROTO_TCP",
        "socket_device": 1,
        "socket_inode": port,
        "socket_mode": stat.S_IFSOCK | 0o600,
        "socket_owner_uid": os.getuid(),
        "getsockname_host": "10.91.0.1",
        "getsockname_port": port,
        "ip_freebind": True,
    }
    value["socket_plan_id"] = _digest(canonical_json_bytes(value))
    return value


def _observations(tmp_path: Path, attempt_id: str, evidence_digest: str) -> dict[str, object]:
    digest = "sha256:" + "a" * 64
    handles = [
        {"handle_id": "api", "purpose": "api_bearer", "route_ids": []},
        {"handle_id": "callback", "purpose": "policy_callback", "route_ids": ["f2-fixed-policy-callback"]},
        {"handle_id": "observation-signing", "purpose": "callback_observation_signing_key", "route_ids": []},
        {"handle_id": "receipt", "purpose": "receipt_signer", "route_ids": []},
        {"handle_id": "receipt-evidence", "purpose": "evidence_receipt_signing_key", "route_ids": []},
        {"handle_id": "tls", "purpose": "callback_tls_private_key", "route_ids": []},
    ]
    artifact = {
        "path": os.fspath(tmp_path / "placeholder.pem"),
        "sha256": digest,
        "media_type": "application/x-pem-file",
    }
    public_ref = {
        "path": os.fspath(tmp_path / "receipt-public.pem"),
        "sha256": digest,
        "size_bytes": 1,
        "media_type": "application/x-pem-file",
    }
    return {
        "schema_version": "bb.rl.phase5-f2-c4-target-dynamic-observations.v1",
        "attempt_id": attempt_id,
        "callback_observed_port": 19001,
        "callback_secret_handle_version_digest": _digest("callback-secret"),
        "validity": {
            "issued_at": "2026-07-13T00:00:00Z",
            "not_before": "2026-07-13T00:00:00Z",
            "expires_at": "2026-07-13T01:00:00Z",
        },
        "revocation": {"scope_digest": _digest("scope"), "epoch": 1, "state_digest": _digest("state")},
        "stores": {
            "cas": os.fspath(tmp_path / "cas"),
            "locator": os.fspath(tmp_path / "locator"),
            "materialization_cache": os.fspath(tmp_path / "materialization"),
            "workspace": os.fspath(tmp_path / "workspace"),
            "lease": os.fspath(tmp_path / "lease"),
            "security_profile": os.fspath(tmp_path / "security"),
            "lease_ttl_seconds": 600,
        },
        "prebound_service_socket_plans": [
            _socket_plan("callback_tls", 19001),
            _socket_plan("fixed_policy", 19002),
            _socket_plan("harness", 19003),
        ],
        "secret_handles": {"records": handles},
        "secret_files": {
            "api": os.fspath(tmp_path / "api.key"),
            "callback": os.fspath(tmp_path / "callback.key"),
            "receipt": os.fspath(tmp_path / "receipt.key"),
        },
        "receipt_signer": {
            "key_id": "receipt-key",
            "secret_handle_id": "receipt",
            "secret_path": os.fspath(tmp_path / "receipt.key"),
        },
        "tls_private_key_secret_handle_id": "tls",
        "tls_leaf_public_key_sha256": digest,
        "evidence_bindings": [
            {
                "schema_version": "bb.rl.evidence-role-binding.v2",
                "role": "runner_result",
                "source": "runner_result",
                "producer_id": "primary",
                "producer_implementation_digest": _digest("runner"),
            },
            {
                "schema_version": "bb.rl.evidence-role-binding.v2",
                "role": "verifier_result",
                "source": "verifier_result",
                "producer_id": "exact-output",
                "producer_implementation_digest": _digest("verifier"),
            },
        ],
        "tls": {
            "route_id": "f2-fixed-policy-callback",
            "target_ip": "10.91.0.1",
            "ca_certificate": artifact,
            "leaf_certificate": artifact,
            "expected_leaf_der_sha256": digest,
            "minimum_tls_version": "TLSv1.3",
            "cipher_suite": "TLS_AES_256_GCM_SHA384",
            "dedicated_single_leaf_ca": True,
        },
        "broker_implementation_digest": _digest("broker-reviewed"),
        "callback_observation_signing_key_handle_id": "observation-signing",
        "callback_observation_evidence_policy_revision_digest": evidence_digest,
        "callback_observation_route_id": "f2-fixed-policy-callback",
        "evidence_receipt_signing_authority": {
            "schema_version": "bb.rl.harness-evidence-receipt-signing-authority.v1",
            "attempt_id": attempt_id,
            "composition_digest": _digest("composition"),
            "evidence_policy_digest": evidence_digest,
            "algorithm": "Ed25519",
            "public_key_ref": public_ref,
            "public_key_sha256": digest,
            "public_key_spki_sha256": _digest("spki"),
            "private_key_secret_handle_id": "receipt-evidence",
            "openssl_authority_digest": _digest("openssl"),
        },
    }


def _authoring_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, object], dict[str, Path]]:
    executable_paths: dict[str, Path] = {}
    for name in ("containerd", "docker", "dockerd", "runc"):
        path = tmp_path / name
        path.write_bytes((name + "-reviewed\n").encode())
        path.chmod(0o555)
        executable_paths[name] = path
    monkeypatch.setattr(author, "_EXECUTABLE_PATHS", {key: os.fspath(value) for key, value in executable_paths.items()})
    archive = tmp_path / "combined-images.tar"
    archive.write_bytes(b"combined-offline-images\n")
    archive.chmod(0o400)
    network_projection = {"mode": "none", "docker_network": "none", "egress_route_ids": [], "default_deny": True}
    network_digest = SandboxNetworkPolicy.derive_digest(network_projection)
    primary_image = "sha256:" + "1" * 64
    verifier_image = "sha256:" + "2" * 64
    primary_security = _security(65534, "breadboard-f2-primary")
    verifier_security = _security(65533, "breadboard-f2-verifier")
    attempt_id = "f2-same-job-001"
    evidence_digest = _digest("dynamic-evidence-policy-reviewed")
    value: dict[str, object] = {
        "schema_version": "bb.rl.phase5-f2-c4-target-dynamic-packet-authoring-input.v1",
        "task_output_path": "/workspace/work/result.txt",
        "task_output_utf8": "breadboard-f2-terminal-ok\n",
        "plan": {
            "schema_version": "bb.rl.phase5-f2-c4-target-dynamic-plan.v1",
            "composition_id": "f2-production-composition",
            "attempt_id": attempt_id,
            "callback_owner_id": "fixed-policy-owner",
            "callback_credential_handle_id": "callback",
            "subject": {"tenant_id": "tenant", "principal_id": "principal", "authority_scope_digest": _digest("scope")},
            "outer_bridge_plan": {
                "schema_version": "bb.rl.harness-outer-bridge-plan.v1",
                "network_name": "f2-private",
                "driver": "bridge",
                "subnet": "10.91.0.0/24",
                "gateway": "10.91.0.1",
                "internal": True,
                "labels": [{"key": "breadboard.attempt", "value": attempt_id}],
                "cleanup_owner": "f2_outer_orchestrator",
                "cleanup_ref": attempt_id,
            },
            "server_request_timeout_seconds": 30.0,
        },
        "observations_template": _observations(tmp_path, attempt_id, evidence_digest),
        "installed": {
            "runner_adapters": [{"adapter_id": "terminal", "runtime_abi": "breadboard.runner.v1", "implementation_digest": _digest("runner-adapter")}],
            "executables": {name: _file_authority(path) for name, path in executable_paths.items()},
            "combined_image_archive": _file_authority(archive),
            "primary_runtime": {
                "runtime_id": "primary",
                "driver_implementation_digest": _digest("primary-driver"),
                "oci_runtime_name": "breadboard-runc",
                "supported_platform_versions": ["linux-amd64"],
                "fixed_environment": [["PATH", "/usr/bin:/bin"]],
                "idle_argv": ["sh", "-lc", "trap : TERM INT; sleep infinity & wait"],
            },
            "verifier_runtime": {
                "runtime_id": "verifier-runtime",
                "driver_implementation_digest": _digest("primary-driver"),
                "oci_runtime_name": "breadboard-runc",
                "supported_platform_versions": ["linux-amd64"],
                "fixed_environment": [["PATH", "/usr/bin:/bin"]],
                "idle_argv": ["sh", "-lc", "trap : TERM INT; sleep infinity & wait"],
            },
            "primary_image": {
                "runtime_id": "primary",
                "observed_image_id": primary_image,
                "immutable_reference": "breadboard/f2-primary@" + primary_image,
            },
            "verifier_image": {
                "runtime_id": "verifier-runtime",
                "observed_image_id": verifier_image,
                "immutable_reference": "breadboard/f2-verifier@" + verifier_image,
            },
            "primary_security_policy": primary_security,
            "verifier_security_policy": verifier_security,
            "network_policy": {
                "expected_policy_digest": network_digest,
                "mode": "none",
                "docker_network": "none",
                "egress_route_ids": [],
                "default_deny": True,
            },
            "verifier": {
                "grant": {
                    "verifier_id": "exact-output",
                    "implementation_digest": _digest("verifier-implementation"),
                    "image_digest": verifier_image,
                    "executable_digest": _digest("verifier-executable"),
                    "code_digest": _digest("verifier-code"),
                    "input_schema_digest": _digest("verifier-input-schema"),
                    "result_schema_digest": _digest("verifier-result-schema"),
                    "network_policy_digest": network_digest,
                    "secret_handle_ids": [],
                },
                "runtime_id": "verifier-runtime",
                "security_policy_digest": verifier_security["expected_policy_digest"],
                "argv": ["/opt/breadboard-f2/verifier"],
                "result_relative_path": "result.txt",
            },
            "private_daemon": {"storage_driver": "vfs", "log_limit_bytes": 65536, "runtime_name": "breadboard-runc"},
        },
    }
    executable_paths["archive"] = archive
    return value, executable_paths


def _publish_input(path: Path, value: dict[str, object]) -> None:
    path.write_bytes(canonical_json_bytes(value))
    path.chmod(0o400)


def _author(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: dict[str, object]) -> Path:
    input_path = tmp_path / "reviewed-input.json"
    output_path = tmp_path / "same-job-packet.json"
    _publish_input(input_path, value)
    return author.author_target_dynamic_packet(
        input_path=input_path,
        output_path=output_path,
        private_root=tmp_path / "private",
    )


def test_authors_canonical_packet_with_exact_installed_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    value, paths = _authoring_value(tmp_path, monkeypatch)
    output = _author(tmp_path, monkeypatch, value)

    raw = output.read_bytes()
    packet = json.loads(raw)
    assert raw == canonical_json_bytes(packet)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    plan = F2C4TargetDynamicPlanInput.model_validate(packet["plan"], strict=True)
    observations = F2C4TargetDynamicObservations.model_validate_json(
        canonical_json_bytes(packet["observations"]), strict=True
    )
    installed: InstalledV1 = observations.installed
    assert plan.attempt_id == observations.attempt_id == "f2-same-job-001"
    assert tuple(runtime.runtime_id for runtime in installed.runtimes) == ("primary", "verifier-runtime")
    assert all(runtime.runtime_class.value == "hardened_docker" for runtime in installed.runtimes)
    assert all(runtime.executable_path == os.fspath(paths["docker"]) for runtime in installed.runtimes)
    assert all(runtime.measured_binary_digest == _digest(paths["docker"].read_bytes()) for runtime in installed.runtimes)
    assert all(runtime.oci_runtime_binary_path == os.fspath(paths["runc"]) for runtime in installed.runtimes)
    assert tuple(image.image_digest for image in installed.images) == tuple(sorted(("sha256:" + "1" * 64, "sha256:" + "2" * 64)))
    assert tuple(policy.policy_digest for policy in installed.security_policies) == tuple(sorted(policy.policy_digest for policy in installed.security_policies))
    assert len({policy.policy_digest for policy in installed.security_policies}) == 2
    assert installed.network_policies[0].mode == installed.network_policies[0].docker_network == "none"
    assert installed.network_policies[0].default_deny is True
    assert installed.network_policies[0].egress_route_ids == ()
    verifier = installed.verifiers[0]
    assert verifier.grant.verifier_id == "exact-output"
    assert verifier.runtime_id == "verifier-runtime"
    assert verifier.argv == ("/opt/breadboard-f2/verifier",)
    assert verifier.result_relative_path == "result.txt"
    assert verifier.grant.image_digest == next(image.image_digest for image in installed.images if image.runtime_id == "verifier-runtime")
    assert verifier.grant.network_policy_digest == installed.network_policies[0].policy_digest
    assert verifier.security_policy_digest in {policy.policy_digest for policy in installed.security_policies}
    daemon = installed.private_docker_daemon
    assert daemon is not None
    assert daemon.images[0].archive.path == daemon.images[1].archive.path == os.fspath(paths["archive"])
    daemon_paths = (
        daemon.config_path,
        daemon.socket_path,
        daemon.pid_file,
        daemon.data_root,
        daemon.exec_root,
        daemon.mount_stage_root,
        daemon.containerd_socket_path,
        daemon.containerd_root,
        daemon.containerd_state,
        daemon.log_root,
    )
    assert len(set(daemon_paths)) == len(daemon_paths)
    assert all(path.startswith(os.fspath(tmp_path / "private" / "f2-docker-f2-same-job-001") + "/") for path in daemon_paths)


@pytest.mark.parametrize("artifact", ["docker", "archive"])
def test_rejects_tampered_observed_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifact: str) -> None:
    value, paths = _authoring_value(tmp_path, monkeypatch)
    paths[artifact].chmod(0o700)
    paths[artifact].write_bytes(b"tampered\n")
    paths[artifact].chmod(0o555 if artifact == "docker" else 0o400)
    input_path = tmp_path / "reviewed-input.json"
    _publish_input(input_path, value)
    with pytest.raises(ValueError, match="authority mismatch"):
        author.author_target_dynamic_packet(
            input_path=input_path,
            output_path=tmp_path / "packet.json",
            private_root=tmp_path / "private",
        )
    assert not (tmp_path / "packet.json").exists()


@pytest.mark.parametrize("duplicate", ["image", "security"])
def test_rejects_duplicate_role_authority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, duplicate: str) -> None:
    value, _paths = _authoring_value(tmp_path, monkeypatch)
    installed = value["installed"]
    assert isinstance(installed, dict)
    if duplicate == "image":
        primary = installed["primary_image"]
        verifier = installed["verifier_image"]
        assert isinstance(primary, dict) and isinstance(verifier, dict)
        verifier["observed_image_id"] = primary["observed_image_id"]
        verifier["immutable_reference"] = "breadboard/f2-verifier@" + str(primary["observed_image_id"])
        grant = installed["verifier"]
        assert isinstance(grant, dict) and isinstance(grant["grant"], dict)
        grant["grant"]["image_digest"] = primary["observed_image_id"]
    else:
        installed["verifier_security_policy"] = copy.deepcopy(installed["primary_security_policy"])
        verifier = installed["verifier"]
        assert isinstance(verifier, dict) and isinstance(installed["primary_security_policy"], dict)
        verifier["security_policy_digest"] = installed["primary_security_policy"]["expected_policy_digest"]
    input_path = tmp_path / "reviewed-input.json"
    _publish_input(input_path, value)
    with pytest.raises(ValueError, match="must be distinct"):
        author.author_target_dynamic_packet(
            input_path=input_path,
            output_path=tmp_path / "packet.json",
            private_root=tmp_path / "private",
        )


def test_rejects_noncanonical_socket_authority_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value, _paths = _authoring_value(tmp_path, monkeypatch)
    observations = value["observations_template"]
    assert isinstance(observations, dict)
    sockets = observations["prebound_service_socket_plans"]
    assert isinstance(sockets, list)
    sockets[0], sockets[1] = sockets[1], sockets[0]
    input_path = tmp_path / "reviewed-input.json"
    _publish_input(input_path, value)
    with pytest.raises(ValueError, match="socket plans require exact"):
        author.author_target_dynamic_packet(
            input_path=input_path,
            output_path=tmp_path / "packet.json",
            private_root=tmp_path / "private",
        )


def test_rejects_relative_and_existing_output_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    value, _paths = _authoring_value(tmp_path, monkeypatch)
    input_path = tmp_path / "reviewed-input.json"
    _publish_input(input_path, value)
    with pytest.raises(ValueError, match="absolute normalized"):
        author.author_target_dynamic_packet(
            input_path=input_path,
            output_path=Path("packet.json"),
            private_root=tmp_path / "private",
        )
    existing = tmp_path / "existing.json"
    existing.write_bytes(b"operator-owned")
    existing.chmod(0o644)
    with pytest.raises(FileExistsError, match="already exists"):
        author.author_target_dynamic_packet(
            input_path=input_path,
            output_path=existing,
            private_root=tmp_path / "private",
        )
    assert existing.read_bytes() == b"operator-owned"
    assert stat.S_IMODE(existing.stat().st_mode) == 0o644


def test_rejects_output_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    value, _paths = _authoring_value(tmp_path, monkeypatch)
    input_path = tmp_path / "reviewed-input.json"
    _publish_input(input_path, value)
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"operator-owned")
    output = tmp_path / "packet.json"
    output.symlink_to(sentinel)
    with pytest.raises(FileExistsError, match="already exists"):
        author.author_target_dynamic_packet(
            input_path=input_path,
            output_path=output,
            private_root=tmp_path / "private",
        )
    assert sentinel.read_bytes() == b"operator-owned"


def test_rejects_existing_private_root_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value, _paths = _authoring_value(tmp_path, monkeypatch)
    input_path = tmp_path / "reviewed-input.json"
    _publish_input(input_path, value)
    source_tree = tmp_path / "source-tree"
    source_tree.mkdir()
    private_root = tmp_path / "private"
    private_root.symlink_to(source_tree, target_is_directory=True)
    with pytest.raises(FileExistsError, match="private root already exists"):
        author.author_target_dynamic_packet(
            input_path=input_path,
            output_path=tmp_path / "packet.json",
            private_root=private_root,
        )
    assert list(source_tree.iterdir()) == []


def test_rejects_output_private_root_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    value, _paths = _authoring_value(tmp_path, monkeypatch)
    input_path = tmp_path / "reviewed-input.json"
    _publish_input(input_path, value)
    collision = tmp_path / "collision"
    with pytest.raises(ValueError, match="must be distinct"):
        author.author_target_dynamic_packet(
            input_path=input_path,
            output_path=collision,
            private_root=collision,
        )
    assert not collision.exists()
