from __future__ import annotations

import base64
import hashlib
import json
import stat
from types import SimpleNamespace

import pytest

from breadboard.rl.harness.composition import (
    DockerNetworkLabelV1,
    HmacSha256ReceiptAuthenticator,
    OuterBridgeLifecycle,
    OuterBridgePlanV1,
    PreboundServiceSocketPlanV1,
)
from breadboard.rl.harness.sandbox_docker import DockerCommandResult


_NETWORK_ID = "a" * 64
_GATEWAY = "172.30.44.1"


def _plan() -> OuterBridgePlanV1:
    return OuterBridgePlanV1(
        schema_version="bb.rl.harness-outer-bridge-plan.v1",
        network_name="bb-f2-outer",
        driver="bridge",
        subnet="172.30.44.0/24",
        gateway=_GATEWAY,
        internal=True,
        labels=(DockerNetworkLabelV1(key="bb.rl.run", value="attempt-1"),),
        cleanup_owner="f2_outer_orchestrator",
        cleanup_ref="attempt-1:bb-f2-outer",
    )


def _observed(*, inode: int = 42, port: int = 43123) -> dict[str, object]:
    return {
        "gateway": _GATEWAY,
        "observed_port": port,
        "family": "AF_INET",
        "socket_type": "SOCK_STREAM",
        "protocol": "IPPROTO_TCP",
        "socket_device": 8,
        "socket_inode": inode,
        "socket_mode": stat.S_IFSOCK | 0o600,
        "socket_owner_uid": 0,
        "getsockname_host": _GATEWAY,
        "getsockname_port": port,
        "ip_freebind": True,
    }


def _socket_plan(
    *, role: str = "harness", inode: int = 42, port: int = 43123
) -> PreboundServiceSocketPlanV1:
    values = {
        "schema_version": "bb.rl.harness-prebound-service-socket-plan.v1",
        "role": role,
        **_observed(inode=inode, port=port),
    }
    payload = json.dumps(
        values, sort_keys=True, separators=(",", ":")
    ).encode()
    return PreboundServiceSocketPlanV1(
        **values,
        socket_plan_id="sha256:" + hashlib.sha256(payload).hexdigest(),
    )


class _Broker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.observation = SimpleNamespace(
            pid=100, starttime="111", mount_namespace="mnt:[100]"
        )
        self.daemon_binding = SimpleNamespace(
            daemon_instance_id="daemon-1",
            daemon_pid=200,
            daemon_starttime="222",
            daemon_pid_namespace="pid:[200]",
        )

    def execute_docker(self, tail, *, timeout_ms, output_limit):
        tail = tuple(tail)
        self.calls.append(tail)
        returncode = 0
        stdout = b""
        stderr = b""
        if tail[:2] == ("network", "create"):
            stdout = (_NETWORK_ID + "\n").encode()
        elif tail == ("network", "inspect", _NETWORK_ID) and len(self.calls) <= 2:
            stdout = json.dumps(
                [
                    {
                        "Id": _NETWORK_ID,
                        "Name": "bb-f2-outer",
                        "Created": "2026-07-12T03:30:00Z",
                        "Driver": "bridge",
                        "Internal": True,
                        "IPAM": {
                            "Config": [
                                {"Subnet": "172.30.44.0/24", "Gateway": _GATEWAY}
                            ]
                        },
                        "Labels": {"bb.rl.run": "attempt-1"},
                        "Containers": {},
                    }
                ],
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
        elif tail[:2] == ("network", "inspect"):
            returncode = 1
            stderr = b"Error: No such network\n"
        return DockerCommandResult(
            ("docker", *tail), returncode, stdout, stderr,
            timed_out=False, output_limited=False,
        )


def _lifecycle(broker: _Broker) -> OuterBridgeLifecycle:
    return OuterBridgeLifecycle(
        broker=broker,  # type: ignore[arg-type]
        composition_digest="sha256:" + "c" * 64,
        plan=_plan(),
        authenticator=HmacSha256ReceiptAuthenticator(
            key_id="receipt-key", key=b"k" * 32
        ),
        lease_ttl_seconds=300,
        prebound_service_socket_plans=(_socket_plan(),),
        prebound_service_socket_fds={"harness": 9},
    )


def test_static_bridge_and_socket_plans_roundtrip_canonical_json_strictly() -> None:
    bridge = _plan()
    socket_plan = _socket_plan()
    assert OuterBridgePlanV1.model_validate_json(
        bridge.canonical_bytes(), strict=True
    ) == bridge
    assert PreboundServiceSocketPlanV1.model_validate_json(
        socket_plan.canonical_bytes(), strict=True
    ) == socket_plan


def test_lifecycle_creates_exact_bridge_emits_leases_and_proves_cleanup(monkeypatch) -> None:
    monkeypatch.setattr(
        "breadboard.rl.harness.composition._observed_socket",
        lambda fd, plan: _observed(),
    )
    broker = _Broker()
    lifecycle = _lifecycle(broker)

    lease = lifecycle.start()

    assert broker.calls[0] == (
        "network", "create", "--driver", "bridge", "--internal",
        "--subnet", "172.30.44.0/24", "--gateway", _GATEWAY,
        "--label", "bb.rl.run=attempt-1", "bb-f2-outer",
    )
    assert lease.composition_digest == "sha256:" + "c" * 64
    assert lease.plan_digest == _plan().canonical_digest()
    assert lease.network_id == _NETWORK_ID
    inspect_bytes = base64.b64decode(lease.inspect_bytes_base64, validate=True)
    assert lease.inspect_sha256 == "sha256:" + hashlib.sha256(inspect_bytes).hexdigest()
    socket_lease = lifecycle.service_sockets["harness"]
    assert socket_lease.bridge_lease_id == lease.lease_id
    assert socket_lease.bridge_lease_digest == lease.canonical_digest()
    assert socket_lease.pre_create_observation_digest == socket_lease.post_create_observation_digest
    authenticator = HmacSha256ReceiptAuthenticator(
        key_id="receipt-key", key=b"k" * 32
    )
    assert lease.verify_authenticator(authenticator) is True
    bad_lease = lease.model_dump(mode="json")
    bad_lease["auth_digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="authentication digest"):
        type(lease).model_validate(bad_lease, strict=True)
    bad_socket = socket_lease.model_dump(mode="json")
    bad_socket["post_create_observation_bytes_base64"] = base64.b64encode(
        b"{}"
    ).decode()
    with pytest.raises(ValueError, match="observations are not exact"):
        type(socket_lease).model_validate(bad_socket, strict=True)

    lifecycle.close()

    receipt = lifecycle.cleanup_receipt
    assert receipt is not None
    assert receipt.id_absent is True and receipt.name_absent is True
    assert receipt.lease_id == lease.lease_id
    assert receipt.lease_digest == lease.canonical_digest()
    assert ("network", "rm", _NETWORK_ID) in broker.calls
    assert ("network", "inspect", "bb-f2-outer") in broker.calls
    assert receipt.verify_authenticator(authenticator) is True
    bad_receipt = receipt.model_dump(mode="json")
    bad_receipt["post_list_bytes_base64"] = base64.b64encode(b"synthetic").decode()
    with pytest.raises(ValueError, match="receipt is not exact"):
        type(receipt).model_validate(bad_receipt, strict=True)
    bad_signature = receipt.model_copy(update={"signature": "0" * 64})
    assert bad_signature.verify_authenticator(authenticator) is False


def test_lifecycle_leases_callback_tls_gateway_socket(monkeypatch) -> None:
    monkeypatch.setattr(
        "breadboard.rl.harness.composition._observed_socket",
        lambda fd, plan: (
            _observed() if fd == 9 else _observed(inode=43, port=44443)
        ),
    )
    broker = _Broker()
    callback_plan = _socket_plan(
        role="callback_tls", inode=43, port=44443
    )
    lifecycle = OuterBridgeLifecycle(
        broker=broker,  # type: ignore[arg-type]
        composition_digest="sha256:" + "c" * 64,
        plan=_plan(),
        authenticator=HmacSha256ReceiptAuthenticator(
            key_id="receipt-key", key=b"k" * 32
        ),
        lease_ttl_seconds=300,
        prebound_service_socket_plans=(callback_plan, _socket_plan()),
        prebound_service_socket_fds={"callback_tls": 10, "harness": 9},
    )
    lease = lifecycle.start()
    callback_lease = lifecycle.service_sockets["callback_tls"]
    assert callback_lease.socket_plan_id == callback_plan.socket_plan_id
    assert callback_lease.bridge_lease_id == lease.lease_id
    lifecycle.close()


def test_lifecycle_rejects_socket_drift_and_removes_partial_bridge(monkeypatch) -> None:
    observations = iter((_observed(), _observed(inode=43)))
    monkeypatch.setattr(
        "breadboard.rl.harness.composition._observed_socket",
        lambda fd, plan: next(observations),
    )
    broker = _Broker()
    lifecycle = _lifecycle(broker)

    with pytest.raises(ValueError, match="changed during bridge creation"):
        lifecycle.start()

    assert broker.calls[-1] == ("network", "rm", _NETWORK_ID)


def test_lifecycle_rejects_missing_or_extra_socket_descriptor_mapping() -> None:
    with pytest.raises(ValueError, match="descriptors are not exact"):
        OuterBridgeLifecycle(
            broker=_Broker(),  # type: ignore[arg-type]
            composition_digest="sha256:" + "c" * 64,
            plan=_plan(),
            authenticator=HmacSha256ReceiptAuthenticator(
                key_id="receipt-key", key=b"k" * 32
            ),
            lease_ttl_seconds=300,
            prebound_service_socket_plans=(_socket_plan(),),
            prebound_service_socket_fds={},
        )
