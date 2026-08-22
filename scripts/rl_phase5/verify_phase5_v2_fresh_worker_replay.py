from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

PROGRAM_ID = "bb-zyphra-rl-phase5-v2"
REVISION_ID = "v2.0.0-rc5-20260717"
ARTIFACT_MANIFEST_SHA256 = (
    "sha256:0feeafccb4f17be777fd815824844cb65173abb64d75203aed79bf83f09bd5bf"
)
ALLOWED_INPUTS = (
    "ARTIFACT_MANIFEST.json",
    "FRESH_WORKER_HANDOFF_CONTRACT.json",
    "PROGRAM_SPEC.yaml",
    "WORK_PACKET_DAG.yaml",
    "RUN_QUEUE.json",
    "DRAFT_STATUS.json",
    "SOURCE_MANIFEST.json",
    "MIGRATION_PLAN.json",
    "MIGRATION_TRANSACTION.json",
    "QUIESCENCE_CONTRACT.json",
    "SESSION_HANDOFF_CONTRACT.json",
    "MIGRATION_REPLAY_CONTRACT.json",
)
STORE_ORDER = ("v2_event_log", "beads_projection", "root_active_selector")
RECEIPT_PRODUCTION_ORDER = (
    "pre_replay_inputs_complete",
    "migration_and_fresh_worker_replay_receipts_complete",
    "quiescence_release_intent_receipt_complete",
    "lease_released_and_file_descriptor_closed",
    "quiescence_post_release_receipt_complete",
    "session_post_handoff_receipt_complete",
    "migration_transaction_receipt_complete",
)
MIGRATION_CONTRACT_REFS = {
    "fresh_worker_program_replay": "FRESH_WORKER_HANDOFF_CONTRACT.json",
    "migration_replay": "MIGRATION_REPLAY_CONTRACT.json",
    "quiescence": "QUIESCENCE_CONTRACT.json",
    "session_handoff": "SESSION_HANDOFF_CONTRACT.json",
    "transaction": "MIGRATION_TRANSACTION.json",
}
WORKER_FIELDS = (
    "pid",
    "input_hashes",
    "derived_action",
    "execution_frontier",
    "target_execution_allowed",
    "ambient_inputs_used",
)
WORKER_SEMANTIC_FIELDS = WORKER_FIELDS[1:]
REPORT_FIELDS = (
    "artifact_manifest_sha256",
    "contract_sha256",
    "worker_count",
    "worker_semantic_sha256",
    "result",
)
RECEIPT_CONTRACT_FIELDS = (
    "additional_fields_allowed",
    "each_worker_fields",
    "pass",
    "top_level_fields",
)
TRANSACTION_FIELDS = (
    "commit_order",
    "failure_contract",
    "locking",
    "mode",
    "nonclaims",
    "prepare",
    "program_id",
    "receipt_production_order",
    "receipt_required",
    "revision_id",
    "schema_version",
    "stores",
    "verify",
)
TRANSACTION_LOCKING_FIELDS = (
    "client_policy",
    "journal",
    "lease_scope",
    "owner",
    "release",
    "rule",
)
QUIESCENCE_FIELDS = (
    "adapter_discovery",
    "client_behavior",
    "descriptor_discovery",
    "lease_contract",
    "mode",
    "native_observations",
    "nonclaims",
    "ordered_protocol",
    "program_id",
    "receipt_contract",
    "revision_id",
    "schema_version",
)
QUIESCENCE_CLIENT_FIELDS = (
    "claim",
    "domain_error_claimed",
    "forbidden_claim",
    "new_clients",
)
WORKER_RECEIPT_NAME = "worker-receipt.json"
WORKER_TIMEOUT_SECONDS = 15
MAX_INPUT_BYTES = 16 * 1024 * 1024
MAX_RECEIPT_BYTES = 64 * 1024
MAX_DIAGNOSTIC_BYTES = 64 * 1024
READ_CHUNK_BYTES = 64 * 1024


class ReplayError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()


def require_exact_keys(value: Any, fields: tuple[str, ...], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError(f"{label} fields changed")
    return value


def open_nofollow_directory(path: Path) -> tuple[int, Path]:
    absolute = Path(os.path.abspath(os.fspath(path)))
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    current = os.open("/", directory_flags)
    try:
        for component in absolute.parts[1:]:
            next_fd = os.open(
                component,
                directory_flags | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = next_fd
        if not stat.S_ISDIR(os.fstat(current).st_mode):
            raise ValueError("revision is not a directory")
        return current, absolute
    except Exception:
        os.close(current)
        raise


def read_regular_leaf_once(
    directory_fd: int,
    name: str,
    *,
    maximum_bytes: int,
) -> bytes:
    if name in {"", ".", ".."} or Path(name).name != name:
        raise ValueError("invalid input leaf name")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    file_fd = os.open(name, flags, dir_fd=directory_fd)
    try:
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("input leaf is not a regular file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(file_fd, READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum_bytes:
                raise ValueError("input leaf exceeds size limit")
            chunks.append(chunk)
        data = b"".join(chunks)
        after = os.fstat(file_fd)
        stable_before = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        stable_after = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if stable_before != stable_after or len(data) != after.st_size:
            raise ValueError("input leaf changed while being read")
        return data
    finally:
        os.close(file_fd)


def read_input_buffers(revision: Path) -> tuple[Path, dict[str, bytes]]:
    directory_fd, absolute = open_nofollow_directory(revision)
    try:
        buffers = {
            name: read_regular_leaf_once(
                directory_fd,
                name,
                maximum_bytes=MAX_INPUT_BYTES,
            )
            for name in ALLOWED_INPUTS
        }
    finally:
        os.close(directory_fd)
    return absolute, buffers


def parse_object(name: str, data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid JSON object: {name}") from error
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {name}")
    return value


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def validate_receipt_contract(handoff: dict[str, Any]) -> None:
    receipt = require_exact_keys(
        handoff.get("receipt"),
        RECEIPT_CONTRACT_FIELDS,
        "fresh-worker receipt contract",
    )
    if (
        receipt.get("additional_fields_allowed") is not False
        or tuple(receipt.get("each_worker_fields", ())) != WORKER_FIELDS
        or tuple(receipt.get("top_level_fields", ())) != REPORT_FIELDS
        or receipt.get("pass")
        != "all workers use exactly allowed inputs and produce byte-identical semantic outputs"
    ):
        raise ValueError("fresh-worker receipt contract changed")


def validate_input_buffers(
    buffers: dict[str, bytes],
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    if tuple(buffers) != ALLOWED_INPUTS:
        raise ValueError("input buffer set changed")

    input_hashes = {name: digest_bytes(buffers[name]) for name in ALLOWED_INPUTS}
    if input_hashes["ARTIFACT_MANIFEST.json"] != ARTIFACT_MANIFEST_SHA256:
        raise ValueError("wrong rc5 artifact manifest")

    manifest = parse_object("ARTIFACT_MANIFEST.json", buffers["ARTIFACT_MANIFEST.json"])
    if (
        manifest.get("program_id") != PROGRAM_ID
        or manifest.get("revision_id") != REVISION_ID
    ):
        raise ValueError("wrong program or revision")

    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, list):
        raise ValueError("artifact manifest files are invalid")
    manifest_rows: dict[str, dict[str, Any]] = {}
    for row in manifest_files:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("path"), str)
            or row["path"] in manifest_rows
        ):
            raise ValueError("artifact manifest contains an invalid or duplicate path")
        manifest_rows[row["path"]] = row

    loaded: dict[str, dict[str, Any]] = {"ARTIFACT_MANIFEST.json": manifest}
    for name in ALLOWED_INPUTS[1:]:
        row = manifest_rows.get(name)
        if (
            row is None
            or row.get("sha256") != input_hashes[name]
            or row.get("size") != len(buffers[name])
        ):
            raise ValueError(f"handoff input is not bound by manifest: {name}")
        loaded[name] = parse_object(name, buffers[name])

    handoff = loaded["FRESH_WORKER_HANDOFF_CONTRACT.json"]
    allowed = handoff.get("allowed_inputs")
    if not isinstance(allowed, list) or tuple(allowed) != ALLOWED_INPUTS:
        raise ValueError("wrong rc5 allowed handoff inputs")
    validate_receipt_contract(handoff)
    return loaded, input_hashes


def reject_rc3_barrier_claims(value: Any, path: tuple[str, ...] = ()) -> None:
    allowed_negative = ("client_behavior", "forbidden_claim")
    allowed_domain_flag = ("client_behavior", "domain_error_claimed")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("claim field name is invalid")
            child_path = (*path, key)
            normalized = key.lower().replace("-", "_")
            if "barrier" in normalized or normalized == "consumer_read_policy":
                raise ValueError("positive rc3 consumer barrier field is forbidden")
            if "domain_error" in normalized:
                if child_path != allowed_domain_flag or child is not False:
                    raise ValueError("positive reader domain-error claim is forbidden")
            reject_rc3_barrier_claims(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_rc3_barrier_claims(child, (*path, str(index)))
    elif isinstance(value, str) and "MIGRATION_IN_PROGRESS" in value:
        if path != allowed_negative or value != "ordinary readers receive MIGRATION_IN_PROGRESS":
            raise ValueError("positive MIGRATION_IN_PROGRESS claim is forbidden")


def derive(
    loaded: dict[str, dict[str, Any]],
    input_hashes: dict[str, str],
) -> dict[str, Any]:
    spec = loaded["PROGRAM_SPEC.yaml"]
    graph = loaded["WORK_PACKET_DAG.yaml"]
    queue = loaded["RUN_QUEUE.json"]
    status = loaded["DRAFT_STATUS.json"]
    handoff = loaded["FRESH_WORKER_HANDOFF_CONTRACT.json"]
    migration = loaded["MIGRATION_PLAN.json"]
    transaction = loaded["MIGRATION_TRANSACTION.json"]
    quiescence = loaded["QUIESCENCE_CONTRACT.json"]
    session_handoff = loaded["SESSION_HANDOFF_CONTRACT.json"]
    migration_replay = loaded["MIGRATION_REPLAY_CONTRACT.json"]

    candidate_contracts = (
        handoff,
        migration,
        transaction,
        quiescence,
        session_handoff,
        migration_replay,
    )
    if any(
        contract.get("program_id") != PROGRAM_ID
        or contract.get("revision_id", REVISION_ID) != REVISION_ID
        for contract in candidate_contracts
    ):
        raise ValueError("rc5 handoff contract identity changed")

    if (
        spec.get("status") != "draft_waiting_rc5_spec_freeze"
        or status.get("program_state") != "DRAFT_WAITING_RC5_SPEC_FREEZE"
    ):
        raise ValueError("program is not waiting for the rc5 SPEC_FREEZE")
    if (
        status.get("active") is not False
        or status.get("active_attempt") is not None
        or status.get("active_packet") is not None
        or status.get("target_lease") is not None
    ):
        raise ValueError("draft has active or leased work")
    if queue.get("eligible") != [] or queue.get("target_lease") is not None:
        raise ValueError("draft queue is executable")
    tracks = status.get("tracks")
    if not isinstance(tracks, dict):
        raise ValueError("draft tracks are invalid")
    assurance = tracks.get("assurance")
    training_proof = tracks.get("training_proof")
    if not isinstance(assurance, dict) or not isinstance(training_proof, dict):
        raise ValueError("draft proof tracks are invalid")
    if (
        assurance.get("awarded_items") != []
        or assurance.get("current_verified_points") != 0
        or training_proof.get("score_field_present") is not False
        or status.get("checkpoint_disposition") != "unclaimed"
    ):
        raise ValueError("draft carries score or checkpoint authority")

    if spec.get("migration_contracts") != MIGRATION_CONTRACT_REFS:
        raise ValueError("program spec migration contract references changed")
    migration_refs = {
        "fresh_worker_program_replay": migration.get("fresh_worker_contract"),
        "migration_replay": migration.get("migration_replay_contract"),
        "quiescence": migration.get("quiescence_contract"),
        "session_handoff": migration.get("session_handoff_contract"),
        "transaction": migration.get("transaction"),
    }
    if migration_refs != MIGRATION_CONTRACT_REFS:
        raise ValueError("migration plan contract references changed")
    if (
        handoff.get("distinct_from", {}).get("migration_replay")
        != "MIGRATION_REPLAY_CONTRACT.json proves native migration and rollback mechanics"
        or handoff.get("distinct_from", {}).get("session_handoff")
        != "SESSION_HANDOFF_CONTRACT.json moves immutable closed-session context into a distinct fresh session"
        or migration_replay.get("distinct_from", {}).get("contract")
        != "FRESH_WORKER_HANDOFF_CONTRACT.json"
    ):
        raise ValueError("frozen replay is not isolated from migration or session replay")

    require_exact_keys(transaction, TRANSACTION_FIELDS, "migration transaction")
    locking = require_exact_keys(
        transaction.get("locking"),
        TRANSACTION_LOCKING_FIELDS,
        "migration transaction locking",
    )
    require_exact_keys(quiescence, QUIESCENCE_FIELDS, "quiescence contract")
    client_behavior = require_exact_keys(
        quiescence.get("client_behavior"),
        QUIESCENCE_CLIENT_FIELDS,
        "quiescence client behavior",
    )
    reject_rc3_barrier_claims(transaction)
    reject_rc3_barrier_claims(quiescence)

    store_rows = transaction.get("stores")
    store_ids = (
        [store.get("id") for store in store_rows]
        if isinstance(store_rows, list)
        and all(isinstance(store, dict) for store in store_rows)
        else []
    )
    if (
        store_ids != list(STORE_ORDER)
        or transaction.get("commit_order") != list(STORE_ORDER)
        or transaction.get("receipt_production_order")
        != list(RECEIPT_PRODUCTION_ORDER)
        or transaction.get("mode")
        != "stop_the_world_three_store_compensating_transaction"
    ):
        raise ValueError("migration is not the exact selector-last three-store transaction")
    receipt_required = transaction.get("receipt_required")
    if not isinstance(receipt_required, dict):
        raise ValueError("migration receipt schema is invalid")
    session_fields = receipt_required.get("session_fields")
    if not isinstance(session_fields, dict):
        raise ValueError("migration session receipt schema is invalid")
    handoff_model = session_handoff.get("handoff_model")
    if not isinstance(handoff_model, dict):
        raise ValueError("session handoff model is invalid")
    if (
        session_fields.get("location") != "outside stores and commit_order"
        or "outside transaction stores"
        not in handoff_model.get("session_store_role", "")
        or "session queue/todos are a transaction store"
        not in transaction.get("nonclaims", [])
    ):
        raise ValueError("session queue/todos are not outside transaction stores")

    if (
        locking.get("client_policy")
        != "BreadBoard, bd/Dolt, and OMP/RPC clients remain stopped; no domain-error behavior is claimed"
        or quiescence.get("mode")
        != "out_of_band_supervisor_owned_stop_the_world"
        or client_behavior.get("claim")
        != "clients are paused outside the migration window and restarted only by the supervisor"
        or client_behavior.get("domain_error_claimed") is not False
        or client_behavior.get("forbidden_claim")
        != "ordinary readers receive MIGRATION_IN_PROGRESS"
        or client_behavior.get("new_clients")
        != "the out-of-band supervisor freezes intake and refuses or stops new BreadBoard, bd/Dolt, and OMP/RPC clients while the lease is held"
    ):
        raise ValueError(
            "clients are not stopped out of band or an rc3 reader barrier was claimed"
        )

    zero_authority_invariants = {
        "new_session_id differs from prior_session_id",
        "capabilities is empty",
        "active_authority is false",
        "score_authority is false",
        "checkpoint_authority is false",
        "target_execution_allowed is false",
        "ambient_inputs_used is empty",
    }
    post_handoff = session_handoff.get("post_handoff_receipt")
    pre_handoff = session_handoff.get("pre_handoff_receipt")
    if not isinstance(post_handoff, dict) or not isinstance(pre_handoff, dict):
        raise ValueError("session handoff receipt schema is invalid")
    authority_fields = {
        "active_authority",
        "score_authority",
        "checkpoint_authority",
        "target_execution_allowed",
    }
    if (
        set(post_handoff.get("invariants", [])) != zero_authority_invariants
        or not authority_fields.issubset(post_handoff.get("required_fields", []))
        or not authority_fields.issubset(
            pre_handoff.get("derived_handoff_fields", [])
        )
    ):
        raise ValueError("session handoff zero-authority fields changed")

    isolation = handoff.get("isolation")
    if not isinstance(isolation, dict) or (
        isolation.get("cwd") != "new empty temporary directory"
        or isolation.get("environment") != "allowlist only"
        or isolation.get("minimum_processes", 0) < 2
    ):
        raise ValueError("fresh-worker isolation contract changed")

    nodes = graph.get("nodes")
    if not isinstance(nodes, list) or not all(
        isinstance(node, dict)
        and isinstance(node.get("id"), str)
        and isinstance(node.get("depends_on"), list)
        for node in nodes
    ):
        raise ValueError("work-packet DAG is invalid")
    roots = sorted(node["id"] for node in nodes if not node["depends_on"])
    expected = handoff.get("derivation")
    post_cutover = migration.get("post_cutover")
    if not isinstance(expected, dict) or not isinstance(post_cutover, dict):
        raise ValueError("rc5 replay derivation is invalid")
    expected_preparation = [
        "author a new SHARED_TRANSPORT repair packet without submission authority"
    ]
    if (
        roots != ["AT0"]
        or expected.get("post_cutover_execution_frontier") != roots
        or post_cutover.get("execution_frontier") != roots
        or expected.get("post_cutover_nonexecuting_preparation")
        != expected_preparation
        or post_cutover.get("nonexecuting_preparation") != expected_preparation
        or expected.get("target_execution_allowed") is not False
        or post_cutover.get("target_execution_allowed") is not False
        or "exact rc5 artifact manifest"
        not in expected.get("current_inactive_action", "")
        or "rc4 decision has no rc5 authority"
        not in expected.get("current_inactive_action", "")
    ):
        raise ValueError("rc5 replay frontier, preparation, or target authority changed")

    semantic = {
        "input_hashes": input_hashes,
        "derived_action": expected["current_inactive_action"],
        "execution_frontier": [],
        "target_execution_allowed": False,
        "ambient_inputs_used": [],
    }
    require_exact_keys(semantic, WORKER_SEMANTIC_FIELDS, "worker semantic receipt")
    return semantic


def write_exclusive_file(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
    file_fd = os.open(os.fspath(path), flags, 0o600)
    try:
        offset = 0
        while offset < len(data):
            written = os.write(file_fd, data[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(file_fd)
    finally:
        os.close(file_fd)


def materialize_snapshot(snapshot: Path, buffers: dict[str, bytes]) -> None:
    if any(snapshot.iterdir()):
        raise ValueError("snapshot directory is not empty")
    for name in ALLOWED_INPUTS:
        destination = snapshot / name
        write_exclusive_file(destination, buffers[name])
        os.chmod(destination, 0o400, follow_symlinks=False)
    os.chmod(snapshot, 0o500)


def run_worker(args: argparse.Namespace) -> int:
    cwd = Path(os.path.realpath(os.getcwd()))
    if any(cwd.iterdir()):
        raise ValueError("fresh-worker cwd is not empty")
    output = Path(os.path.abspath(os.fspath(args.worker_output)))
    if output.parent != cwd or output.name != WORKER_RECEIPT_NAME:
        raise ValueError("worker receipt is outside its private cwd")

    _, buffers = read_input_buffers(args.revision)
    loaded, input_hashes = validate_input_buffers(buffers)
    semantic = derive(loaded, input_hashes)
    receipt = {"pid": os.getpid(), **semantic}
    require_exact_keys(receipt, WORKER_FIELDS, "worker receipt")
    write_exclusive_file(output, canonical_bytes(receipt))
    return 0


def scheme_string(value: Path | str) -> str:
    return json.dumps(os.fspath(value), ensure_ascii=True)


def sandbox_profile(
    *,
    interpreter: Path,
    app_executable: Path,
    script: Path,
    snapshot: Path,
    cwd: Path,
    live_revision: Path,
) -> str:
    project_root = script.parents[2]
    interpreter_roots = {
        Path(os.path.realpath(sys.base_prefix)),
        Path(os.path.realpath(sys.exec_prefix)),
        Path(os.path.realpath(os.__file__)).parent,
    }
    system_roots = (Path("/System/Library"), Path("/usr/lib"))
    read_ancestors = set(snapshot.parents)
    for target in (*interpreter_roots, interpreter, app_executable, script):
        read_ancestors.update(target.parents)

    lines = [
        "(version 1)",
        "(deny default)",
        "(deny network*)",
        f"(deny file-write* (subpath {scheme_string(project_root)}))",
        f"(deny file-write* (subpath {scheme_string(live_revision)}))",
        "(allow sysctl-read)",
        "(allow process-fork)",
        f"(allow process-exec (literal {scheme_string(interpreter)}))",
        f"(allow process-exec (literal {scheme_string(app_executable)}))",
    ]
    for root in system_roots:
        lines.append(f"(allow file-read* (subpath {scheme_string(root)}))")
    for root in sorted(interpreter_roots, key=os.fspath):
        lines.append(f"(allow file-read* (subpath {scheme_string(root)}))")
    for ancestor in sorted(read_ancestors, key=os.fspath):
        lines.append(f"(allow file-read* (literal {scheme_string(ancestor)}))")
    lines.extend(
        [
            f"(allow file-read* (literal {scheme_string(interpreter)}))",
            f"(allow file-read* (literal {scheme_string(app_executable)}))",
            f"(allow file-read* (literal {scheme_string(script)}))",
            f"(allow file-read* (subpath {scheme_string(snapshot)}))",
            f"(allow file-read* (subpath {scheme_string(cwd)}))",
            f"(allow file-write* (subpath {scheme_string(cwd)}))",
            f"(allow file-read* (literal {scheme_string('/dev/null')}))",
        ]
    )
    return "\n".join(lines) + "\n"


def resolved_runtime() -> tuple[Path, Path, Path, Path]:
    if sys.platform != "darwin":
        raise ReplayError("sandbox-unsupported-platform")
    sandbox = Path("/usr/bin/sandbox-exec")
    try:
        sandbox_stat = os.lstat(sandbox)
    except OSError as error:
        raise ReplayError("sandbox-unavailable") from error
    if (
        not stat.S_ISREG(sandbox_stat.st_mode)
        or stat.S_ISLNK(sandbox_stat.st_mode)
        or not os.access(sandbox, os.X_OK)
    ):
        raise ReplayError("sandbox-unavailable")

    try:
        interpreter = Path(sys.executable).resolve(strict=True)
        script = Path(__file__).resolve(strict=True)
    except OSError as error:
        raise ReplayError("fixed-runtime-unavailable") from error
    interpreter_stat = os.lstat(interpreter)
    if (
        not stat.S_ISREG(interpreter_stat.st_mode)
        or stat.S_ISLNK(interpreter_stat.st_mode)
        or not os.access(interpreter, os.X_OK)
    ):
        raise ReplayError("fixed-runtime-unavailable")
    script_stat = os.lstat(script)
    if not stat.S_ISREG(script_stat.st_mode) or stat.S_ISLNK(script_stat.st_mode):
        raise ReplayError("verifier-script-unavailable")

    version_root = Path(os.path.realpath(sys.base_prefix))
    app_executable = (
        version_root / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    )
    try:
        app_stat = os.lstat(app_executable)
    except OSError as error:
        raise ReplayError("fixed-app-runtime-unavailable") from error
    if (
        app_executable.parents[4] != version_root
        or not stat.S_ISREG(app_stat.st_mode)
        or stat.S_ISLNK(app_stat.st_mode)
        or not os.access(app_executable, os.X_OK)
    ):
        raise ReplayError("fixed-app-runtime-unavailable")
    return sandbox, interpreter, app_executable, script


def limit_worker_output() -> None:
    resource.setrlimit(
        resource.RLIMIT_FSIZE,
        (MAX_DIAGNOSTIC_BYTES, MAX_DIAGNOSTIC_BYTES),
    )


def open_unlinked_capture(directory: Path, name: str) -> Any:
    path = directory / name
    file_fd = os.open(
        os.fspath(path),
        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
        0o600,
    )
    try:
        os.unlink(path)
    except Exception:
        os.close(file_fd)
        raise
    return os.fdopen(file_fd, "w+b", buffering=0)


def read_capture(capture: Any) -> bytes:
    capture.seek(0)
    data = capture.read(MAX_DIAGNOSTIC_BYTES + 1)
    if len(data) > MAX_DIAGNOSTIC_BYTES:
        raise ReplayError("worker-diagnostic-limit")
    return data


def terminate_worker(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        process.wait()


def validate_worker_receipt(receipt: Any) -> dict[str, Any]:
    worker = require_exact_keys(receipt, WORKER_FIELDS, "worker receipt")
    pid = worker.get("pid")
    input_hashes = worker.get("input_hashes")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ReplayError("worker-receipt-malformed")
    if (
        not isinstance(input_hashes, dict)
        or set(input_hashes) != set(ALLOWED_INPUTS)
        or any(not isinstance(value, str) for value in input_hashes.values())
        or not isinstance(worker.get("derived_action"), str)
        or worker.get("execution_frontier") != []
        or worker.get("target_execution_allowed") is not False
        or worker.get("ambient_inputs_used") != []
    ):
        raise ReplayError("worker-receipt-malformed")
    return worker


def load_worker_receipt(directory: Path) -> dict[str, Any]:
    directory_fd, _ = open_nofollow_directory(directory)
    try:
        try:
            data = read_regular_leaf_once(
                directory_fd,
                WORKER_RECEIPT_NAME,
                maximum_bytes=MAX_RECEIPT_BYTES,
            )
        except FileNotFoundError as error:
            raise ReplayError("worker-receipt-missing") from error
        except (OSError, ValueError) as error:
            raise ReplayError("worker-receipt-malformed") from error
    finally:
        os.close(directory_fd)
    try:
        receipt = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReplayError("worker-receipt-malformed") from error
    try:
        return validate_worker_receipt(receipt)
    except ValueError as error:
        raise ReplayError("worker-receipt-fields") from error


def launch_worker(
    *,
    sandbox: Path,
    interpreter: Path,
    app_executable: Path,
    script: Path,
    snapshot: Path,
    cwd: Path,
    live_revision: Path,
    environment: dict[str, str],
) -> dict[str, Any]:
    output = cwd / WORKER_RECEIPT_NAME
    profile = sandbox_profile(
        interpreter=interpreter,
        app_executable=app_executable,
        script=script,
        snapshot=snapshot,
        cwd=cwd,
        live_revision=live_revision,
    )
    command = [
        os.fspath(sandbox),
        "-p",
        profile,
        os.fspath(interpreter),
        "-I",
        "-S",
        os.fspath(script),
        "--revision",
        os.fspath(snapshot),
        "--worker-output",
        os.fspath(output),
    ]
    stdout_capture = open_unlinked_capture(cwd, ".worker-stdout")
    stderr_capture = open_unlinked_capture(cwd, ".worker-stderr")
    try:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout_capture,
                stderr=stderr_capture,
                start_new_session=True,
                preexec_fn=limit_worker_output,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ReplayError("worker-launch") from error
        try:
            process.wait(timeout=WORKER_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            terminate_worker(process)
            raise ReplayError("worker-timeout") from error

        stdout = read_capture(stdout_capture)
        stderr = read_capture(stderr_capture)
        if process.returncode is None:
            raise ReplayError("worker-state")
        if process.returncode < 0:
            raise ReplayError("worker-signal")
        if process.returncode != 0:
            raise ReplayError("worker-exit")
        if stdout or stderr:
            raise ReplayError("worker-console-output")
        return load_worker_receipt(cwd)
    finally:
        stdout_capture.close()
        stderr_capture.close()


def run_parent(args: argparse.Namespace) -> int:
    live_revision, source_buffers = read_input_buffers(args.revision)
    loaded, source_input_hashes = validate_input_buffers(source_buffers)
    expected_semantic = derive(loaded, source_input_hashes)
    sandbox, interpreter, app_executable, script = resolved_runtime()
    environment = {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
    }

    workers: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(
        prefix="phase5-v2-frozen-snapshot-"
    ) as snapshot_text:
        snapshot = Path(os.path.realpath(snapshot_text))
        materialize_snapshot(snapshot, source_buffers)
        try:
            with tempfile.TemporaryDirectory(
                prefix="phase5-v2-fresh-worker-a-"
            ) as first_text, tempfile.TemporaryDirectory(
                prefix="phase5-v2-fresh-worker-b-"
            ) as second_text:
                for directory_text in (first_text, second_text):
                    directory = Path(os.path.realpath(directory_text))
                    workers.append(
                        launch_worker(
                            sandbox=sandbox,
                            interpreter=interpreter,
                            app_executable=app_executable,
                            script=script,
                            snapshot=snapshot,
                            cwd=directory,
                            live_revision=live_revision,
                            environment=environment,
                        )
                    )
        finally:
            os.chmod(snapshot, 0o700)

    if len(workers) != 2 or workers[0]["pid"] == workers[1]["pid"]:
        raise ReplayError("worker-process-identity")
    first_hashes = workers[0]["input_hashes"]
    second_hashes = workers[1]["input_hashes"]
    if first_hashes != second_hashes or first_hashes != source_input_hashes:
        raise ReplayError("worker-input-hashes")

    semantic_bytes: list[bytes] = []
    for worker in workers:
        semantic = {field: worker[field] for field in WORKER_SEMANTIC_FIELDS}
        require_exact_keys(semantic, WORKER_SEMANTIC_FIELDS, "worker semantic receipt")
        semantic_bytes.append(canonical_bytes(semantic))
    expected_semantic_bytes = canonical_bytes(expected_semantic)
    if (
        semantic_bytes[0] != semantic_bytes[1]
        or semantic_bytes[0] != expected_semantic_bytes
    ):
        raise ReplayError("worker-semantic-mismatch")

    semantic_digest = digest_bytes(semantic_bytes[0])
    report = {
        "artifact_manifest_sha256": first_hashes["ARTIFACT_MANIFEST.json"],
        "contract_sha256": first_hashes["FRESH_WORKER_HANDOFF_CONTRACT.json"],
        "worker_count": 2,
        "worker_semantic_sha256": semantic_digest,
        "result": "pass",
    }
    require_exact_keys(report, REPORT_FIELDS, "canonical replay report")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_bytes(canonical_bytes(report))
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--revision", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--report", type=Path)
    group.add_argument("--worker-output", type=Path)
    args = parser.parse_args()
    try:
        if args.worker_output is not None:
            return run_worker(args)
        return run_parent(args)
    except ReplayError as error:
        print(f"fresh-worker-replay:{error.code}", file=sys.stderr)
        return 1
    except Exception:
        print("fresh-worker-replay:invalid-input", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
