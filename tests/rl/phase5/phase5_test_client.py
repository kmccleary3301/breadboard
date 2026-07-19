from __future__ import annotations

from multiprocessing.connection import Connection
import socket
import threading

import breadboard.rl.phase5.server_authority as authority_client


def connect_test_authority(
    *,
    deployment_id: str,
    endpoint: str,
    public_key_digest: str,
    public_key_hex: str,
) -> authority_client.Phase5ProductionServer:
    """Connect focused tests without changing production bootstrap configuration."""
    client_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client_socket.settimeout(15.0)
    try:
        client_socket.connect(endpoint)
        client_socket.settimeout(None)
    except Exception:
        client_socket.close()
        raise
    connection = Connection(client_socket.detach())
    if not connection.poll(15.0):
        connection.close()
        raise TimeoutError("test authority handshake timed out")
    try:
        raw = connection.recv_bytes(1_048_576)
        handshake = authority_client._verified_envelope(
            raw,
            expected_deployment_id=deployment_id,
            expected_public_key_digest=public_key_digest,
            expected_public_key_hex=public_key_hex,
        )
    except Exception:
        connection.close()
        raise
    if (
        handshake.get("schema") != authority_client.IPC_SCHEMA
        or handshake.get("op") != "handshake"
        or handshake.get("sequence") != 0
        or handshake.get("request_sha256") is not None
        or not isinstance(handshake.get("session_nonce"), str)
    ):
        connection.close()
        raise RuntimeError("test authority handshake binding mismatch")

    client = object.__new__(authority_client.Phase5ProductionServer)
    client._Phase5ProductionServer__closed = False
    client._Phase5ProductionServer__connection = connection
    client._Phase5ProductionServer__deployment_id = deployment_id
    client._Phase5ProductionServer__endpoint = endpoint
    client._Phase5ProductionServer__lock = threading.Lock()
    client._Phase5ProductionServer__public_key_digest = public_key_digest
    client._Phase5ProductionServer__public_key_hex = public_key_hex
    client._Phase5ProductionServer__registry = authority_client._client_registry()
    client._Phase5ProductionServer__sequence = 1
    client._Phase5ProductionServer__session_nonce = handshake["session_nonce"]
    client._Phase5ProductionServer__timeout = 15.0
    return client
