from __future__ import annotations

import copy
import hashlib
import os
import stat
from pathlib import Path

import pytest

from breadboard.rl.phase5.migration_transaction import (
    FileMigrationGate,
    GateOwnershipError,
    MigrationInProgress,
    build_event,
    canonical_bytes,
    capture_store_image,
    sha256_bytes,
    sha256_file,
    verify_event_chain,
)


STORE_IDS = (
    "root_active_selector",
    "beads_projection",
    "v2_event_log",
)


def _hashing_must_not_start(_value: bytes) -> str:
    raise AssertionError("hashing started before strict JSON validation")


def test_capture_store_image_is_deterministic_and_complete(tmp_path: Path) -> None:
    store = tmp_path / "store.json"
    store.write_bytes(canonical_bytes({"z": "é", "a": [2, 1]}))
    store.chmod(0o640)
    rollback_command = {
        "argv": [
            "restore",
            "--store",
            "root_active_selector",
            "--from",
            "before.json",
        ]
    }

    first = capture_store_image(
        "root_active_selector",
        "revision-7",
        store,
        rollback_command,
        True,
        "normal read must reproduce the captured bytes",
    )
    second = capture_store_image(
        "root_active_selector",
        "revision-7",
        store,
        rollback_command,
        True,
        "normal read must reproduce the captured bytes",
    )

    assert first == second
    assert first.as_dict() == {
        "bytes_sha256": "sha256:" + hashlib.sha256(store.read_bytes()).hexdigest(),
        "path": str(store),
        "reversible": True,
        "revision": "revision-7",
        "rollback_command_sha256": sha256_bytes(canonical_bytes(rollback_command)),
        "rollback_invariant": "normal read must reproduce the captured bytes",
        "size": len(store.read_bytes()),
        "store_id": "root_active_selector",
    }
    assert sha256_file(store) == first.bytes_sha256
    assert stat.S_IMODE(os.stat(store).st_mode) == 0o640
    assert canonical_bytes({"z": "é", "a": [2, 1]}).endswith(b"\n")
    assert canonical_bytes({"z": "é", "a": [2, 1]}) == canonical_bytes(
        {"a": [2, 1], "z": "é"}
    )
    assert canonical_bytes({"z": "é", "a": [2, 1]}) == (
        b'{\n  "a": [\n    2,\n    1\n  ],\n  "z": "\xc3\xa9"\n}\n'
    )


@pytest.mark.parametrize(
    "non_finite",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_canonical_bytes_rejects_non_finite_numbers(non_finite: float) -> None:
    with pytest.raises(ValueError, match="^value is not valid strict JSON$"):
        canonical_bytes({"nested": {"number": non_finite}})


@pytest.mark.parametrize(
    "non_finite",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_build_event_rejects_non_finite_payload_before_hashing(
    non_finite: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "breadboard.rl.phase5.migration_transaction.sha256_bytes",
        _hashing_must_not_start,
    )

    with pytest.raises(ValueError, match="^value is not valid strict JSON$"):
        build_event("MIGRATION_PREPARED", {"nested": {"number": non_finite}})


@pytest.mark.parametrize(
    "non_finite",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_verify_event_chain_rejects_non_finite_payload_before_hashing(
    non_finite: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "breadboard.rl.phase5.migration_transaction.sha256_bytes",
        _hashing_must_not_start,
    )
    event = {
        "predecessor_sha256": None,
        "event_type": "MIGRATION_PREPARED",
        "payload": {"nested": {"number": non_finite}},
        "event_sha256": "sha256:" + "0" * 64,
    }

    with pytest.raises(ValueError, match="^value is not valid strict JSON$"):
        verify_event_chain([event])


@pytest.mark.parametrize(
    "non_finite",
    [float("nan"), float("inf"), float("-inf")],
    ids=["nan", "positive-infinity", "negative-infinity"],
)
def test_capture_store_image_rejects_non_finite_rollback_before_hashing(
    non_finite: float,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = tmp_path / "store.json"
    store.write_bytes(b"exact before-image bytes")
    monkeypatch.setattr(
        "breadboard.rl.phase5.migration_transaction.sha256_bytes",
        _hashing_must_not_start,
    )

    with pytest.raises(ValueError, match="^value is not valid strict JSON$"):
        capture_store_image(
            "root_active_selector",
            "revision-7",
            store,
            {"argv": ["restore"], "metadata": {"number": non_finite}},
            True,
            "normal read must reproduce the captured bytes",
        )


def test_event_chain_rejects_payload_mutation_and_wrong_predecessor() -> None:
    first = build_event(
        "V1_LINEAGE_IMPORTED",
        {"migration_id": "migration-1", "store_id": "v2_event_log"},
    )
    second = build_event(
        "V2_ACTIVATED",
        {"migration_id": "migration-1", "execution_frontier": ["AT0"]},
        predecessor_sha256=first["event_sha256"],
    )
    verify_event_chain([first, second])

    mutated = copy.deepcopy([first, second])
    mutated[1]["payload"]["execution_frontier"] = ["AT1"]
    with pytest.raises(ValueError):
        verify_event_chain(mutated)

    wrong_predecessor = build_event(
        "V2_ACTIVATED",
        {"migration_id": "migration-1", "execution_frontier": ["AT0"]},
        predecessor_sha256="sha256:" + "f" * 64,
    )
    with pytest.raises(ValueError):
        verify_event_chain([first, wrong_predecessor])


def test_event_chain_honors_an_external_initial_predecessor() -> None:
    before_head = "sha256:" + "1" * 64
    event = build_event(
        "MIGRATION_PREPARED",
        {"migration_id": "migration-1"},
        predecessor_sha256=before_head,
    )

    verify_event_chain([event], initial_predecessor_sha256=before_head)
    with pytest.raises(ValueError):
        verify_event_chain(
            [event], initial_predecessor_sha256="sha256:" + "2" * 64
        )


def _acquire(gate: FileMigrationGate) -> dict[str, object]:
    return gate.acquire(
        "migration-1",
        STORE_IDS,
        "owner-token",
        "verifier-token",
    )


def test_gate_is_exclusive_and_binds_exactly_three_stores(tmp_path: Path) -> None:
    gate_path = tmp_path / "migration.gate"
    gate = FileMigrationGate(gate_path)
    acquired = _acquire(gate)

    assert gate_path.exists()
    assert acquired["migration_id"] == "migration-1"
    assert acquired["owner_token"] == "owner-token"
    assert acquired["verifier_token"] == "verifier-token"
    assert acquired["store_ids"] == list(STORE_IDS)
    assert FileMigrationGate(gate_path).load() == acquired

    with pytest.raises(FileExistsError):
        _acquire(FileMigrationGate(gate_path))

    for invalid_scope in (STORE_IDS[:2], (*STORE_IDS, "extra"), (*STORE_IDS[:2], STORE_IDS[0])):
        invalid_gate = FileMigrationGate(tmp_path / f"invalid-{len(invalid_scope)}-{invalid_scope[-1]}.gate")
        with pytest.raises(ValueError):
            invalid_gate.acquire(
                "migration-1",
                invalid_scope,
                "owner-token",
                "verifier-token",
            )


def test_gate_rejects_wrong_owner_and_wrong_verifier(tmp_path: Path) -> None:
    gate = FileMigrationGate(tmp_path / "migration.gate")
    acquired = _acquire(gate)

    assert gate.assert_owner("migration-1", "owner-token") == acquired
    assert gate.assert_verifier("migration-1", "verifier-token") == acquired
    for migration_id, token in (
        ("other-migration", "owner-token"),
        ("migration-1", "wrong-owner"),
    ):
        with pytest.raises(GateOwnershipError):
            gate.assert_owner(migration_id, token)
    for migration_id, token in (
        ("other-migration", "verifier-token"),
        ("migration-1", "wrong-verifier"),
    ):
        with pytest.raises(GateOwnershipError):
            gate.assert_verifier(migration_id, token)


def test_gate_blocks_scoped_ordinary_reads_with_typed_error(tmp_path: Path) -> None:
    gate = FileMigrationGate(tmp_path / "migration.gate")
    _acquire(gate)

    for store_id in STORE_IDS:
        with pytest.raises(MigrationInProgress, match="MIGRATION_IN_PROGRESS"):
            gate.ordinary_read(store_id)
    assert gate.ordinary_read("unrelated_store") is None


def test_gate_renew_and_release_require_current_owner(tmp_path: Path) -> None:
    gate_path = tmp_path / "migration.gate"
    gate = FileMigrationGate(gate_path)
    acquired = _acquire(gate)

    with pytest.raises(GateOwnershipError):
        gate.renew("migration-1", "wrong-owner")
    renewed = gate.renew("migration-1", "owner-token")
    assert renewed != acquired
    assert gate.load() == renewed
    assert gate.status() == {
        "acquired": True,
        "path": str(gate_path),
        **renewed,
    }
    assert renewed["migration_id"] == acquired["migration_id"]
    assert renewed["store_ids"] == acquired["store_ids"]

    with pytest.raises(GateOwnershipError):
        gate.release("other-migration", "owner-token")
    released = gate.release("migration-1", "owner-token")
    assert released["migration_id"] == "migration-1"
    assert not gate_path.exists()
    assert gate.status() == {"acquired": False, "path": str(gate_path)}
