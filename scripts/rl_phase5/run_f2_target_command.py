from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import platform
import re
import shutil
import shlex
import socket
import subprocess
import tarfile
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from breadboard.rl.harness.composition import PreboundServiceSocketPlanV1

from breadboard.rl.phase5.f2_terminal import F1_PREREQUISITE_ID, F1_PREREQUISITE_REF, F1_PREREQUISITE_ROOT, PARTITION, TARGET_ALIAS, TARGET_ARTIFACTS, parse_artifact_markers

RUNNER_PREFIX = b"F2_RUNNER_ARCHIVE="
RESULT_PREFIX = b"F2_RESULT_ARCHIVE="
RUNNER_OBSERVATION_PREFIX = b"F2_RUNNER_OBSERVATION="
COMPONENT_PREFIX = b"PHASE3_COMPONENT_REPORT_JSON="
_ATTEMPT = re.compile(r"^f2-[a-z0-9]+(?:-[a-z0-9]+)*$")
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_OPENSSL_ENV = {"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}
HOST_RUNTIME_ROOT = Path("/shared/breadboard-f2/host-runtime/07730b5d200c38171ae905345f1d21f9615ecc67fd565065bd88a69c42f14d91/runtime")
HOST_RUNTIME_REPORT = HOST_RUNTIME_ROOT.parent / "build-report.json"
HOST_RUNTIME_REPORT_REF = "sha256:e6428360047ed4d3c94cb4910e6ea4cfa6ebbf0e4fcd18eb2e2e679162b56431"
HOST_RUNTIME_PYTHON_REF = "sha256:202c17d1671602a4ef1d43e9b2fdbef0769443f37bf5e51f6b603e0b2c27d9d8"


_IMAGE = re.compile(r"^[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}$")
WRAPPER_BASE_IMAGE_REF = "python@sha256:b81b4bec9aa047850f17862ce34a3ac99463920ef1e9df0434fd7ccdfe2ca691"
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 128


def canon(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _archive(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, compresslevel=9, mtime=0) as compressed, tarfile.open(fileobj=compressed, mode="w") as archive:
        for name, raw in sorted(entries.items()):
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or name != path.as_posix():
                raise ValueError("unsafe archive member")
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(raw), 0o600, 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(raw))
    if output.tell() > _MAX_ARCHIVE_BYTES:
        raise ValueError("runner archive exceeds byte budget")
    return output.getvalue()


def _envelope(prefix: bytes, raw: bytes) -> bytes:
    return prefix + canon({"encoding": "base64", "size_bytes": len(raw), "sha256": sha(raw), "payload": base64.b64encode(raw).decode("ascii")}) + b"\n"


def _decode_envelope(stdout: bytes, prefix: bytes) -> bytes:
    lines = [line[len(prefix):] for line in stdout.splitlines() if line.startswith(prefix)]
    if len(lines) != 1:
        raise ValueError(f"exactly one {prefix.decode()} envelope required")
    value = json.loads(lines[0])
    if not isinstance(value, dict) or set(value) != {"encoding", "size_bytes", "sha256", "payload"} or value["encoding"] != "base64" or type(value["size_bytes"]) is not int or not 0 <= value["size_bytes"] <= _MAX_ARCHIVE_BYTES or not _SHA.fullmatch(str(value["sha256"])):
        raise ValueError("invalid archive envelope")
    payload = value["payload"]
    if not isinstance(payload, str) or len(payload) > ((_MAX_ARCHIVE_BYTES + 2) // 3) * 4:
        raise ValueError("archive envelope exceeds byte budget")
    raw = base64.b64decode(payload, validate=True)
    if base64.b64encode(raw).decode("ascii") != payload:
        raise ValueError("archive envelope payload is not canonical base64")
    if len(raw) != value["size_bytes"] or sha(raw) != value["sha256"]:
        raise ValueError("archive envelope size/hash mismatch")
    return raw


def _safe_extract(raw: bytes, destination: Path, *, max_expanded_bytes: int = _MAX_ARCHIVE_BYTES, max_members: int = _MAX_ARCHIVE_MEMBERS) -> None:
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            seen: set[str] = set()
            members = archive.getmembers()
            if len(members) > max_members or sum(member.size for member in members) > max_expanded_bytes:
                raise ValueError("result archive exceeds extraction budget")
            for member in members:
                path = PurePosixPath(member.name)
                if not member.isfile() or path.is_absolute() or ".." in path.parts or path.as_posix() != member.name or member.name in seen:
                    raise ValueError("unsafe result archive")
                seen.add(member.name)
            archive.extractall(destination, filter="data")
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def decode_runner_archive(stdout: bytes, attempt: Path) -> None:
    raw = _decode_envelope(stdout, RUNNER_PREFIX)
    (attempt / "runner-result.tar.gz").write_bytes(raw)
    extracted = attempt / ".runner-extracted"
    _safe_extract(raw, extracted)
    for stream in ("target.stdout", "target.stderr"):
        if not (extracted / stream).is_file():
            raise ValueError("runner archive lacks target streams")
        os.replace(extracted / stream, attempt / stream)
    if (extracted / "runner").is_dir():
        os.replace(extracted / "runner", attempt / "runner")
    shutil.rmtree(extracted)


def decode_result_archive(stdout: bytes, attempt: Path) -> None:
    raw = _decode_envelope(stdout, RESULT_PREFIX)
    (attempt / "result.tar.gz").write_bytes(raw)
    extracted = attempt / ".artifacts-extracted"
    _safe_extract(raw, extracted)
    if (attempt / "artifacts").exists():
        raise FileExistsError(attempt / "artifacts")
    os.replace(extracted, attempt / "artifacts")


def _run(argv: list[str], *, input_: bytes | None = None) -> dict[str, object]:
    try:
        result = subprocess.run(argv, input=input_, capture_output=True, check=False)
    except OSError as exc:
        return {"argv": argv, "exit_code": 127, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}
    return {"argv": argv, "exit_code": result.returncode, "stdout": result.stdout.decode("utf-8", "replace"), "stderr": result.stderr.decode("utf-8", "replace")}


def _docker_identity() -> dict[str, object]:
    version = _run(["docker", "version", "--format", "{{json .}}"])
    info = _run(["docker", "info", "--format", "{{json .}}"])
    return {
        "schema_version": "bb.rl.f2.docker-identity.v1",
        "version": version,
        "info": info,
    }


def _validate_target_result(attempt_id: str, target_run_id: str, stdout: bytes, stderr: bytes) -> dict[str, object]:
    del target_run_id, stderr
    result_raw = _decode_envelope(stdout, RESULT_PREFIX)
    with tempfile.TemporaryDirectory(prefix="f2-target-validation-") as temporary:
        root = Path(temporary)
        artifacts = root / "artifacts"
        _safe_extract(result_raw, artifacts)
        markers = parse_artifact_markers(stdout, attempt_id)
        actual = {path.name for path in artifacts.iterdir()}
        if actual != set(TARGET_ARTIFACTS.values()):
            raise ValueError("target result inventory is not exact")
        refs: dict[str, str] = {}
        for marker in markers:
            raw = (root / marker["path"]).read_bytes()
            if len(raw) != marker["size"] or sha(raw) != marker["sha256"]:
                raise ValueError(f"target artifact marker mismatch: {marker['name']}")
            refs[marker["name"]] = marker["sha256"]
        cleanup = json.loads((artifacts / TARGET_ARTIFACTS["cleanup"]).read_bytes())
        if cleanup.get("released") is not True or any(cleanup.get(key) != [] for key in ("processes", "containers", "leases", "workspaces", "caches", "secrets")):
            raise ValueError("target cleanup is not authoritative zero-residue")
        return {"result_archive_ref": sha(result_raw), "artifact_refs": refs}


def _component(attempt_id: str, target_run_id: str, *, passed: bool, blocked_reason: str, observation_hashes: dict[str, str]) -> dict[str, object]:
    return {
        "schema_version": "bb.rl.f2.phase3-component.v1",
        "report_id": attempt_id,
        "component": "F2",
        "claim_boundary": "f2_ibm_single_terminal_episode_only",
        "target_run_id": target_run_id,
        "passed": passed,
        "scorecard_update_allowed": False,
        "blocked_reason": blocked_reason,
        "artifact_paths": {},
        "input_hashes": observation_hashes,
        "promotion_allowed": False,
        "point_award_allowed": False,
        "bead_closure_allowed": False,
        "non_claims": [
            "no training-quality, campaign, point-award, promotion, or bead-closure claim",
            "F1 is a prerequisite only and is not terminal-episode evidence",
        ],
        "cleanup_observation": "runner/post-cleanup.json",
    }


def _parse_runner_observation(stdout: bytes) -> dict[str, object]:
    lines = [line[len(RUNNER_OBSERVATION_PREFIX):] for line in stdout.splitlines() if line.startswith(RUNNER_OBSERVATION_PREFIX)]
    if len(lines) != 1:
        raise ValueError("exactly one production runner observation required")
    value = json.loads(lines[0])
    if type(value) is not dict or set(value) != {"image_inspect", "container_inspect", "post_cleanup"} or canon(value) != lines[0]:
        raise ValueError("production runner observation is not exact canonical JSON")
    if any(type(value[key]) is not dict for key in value):
        raise ValueError("production runner observations must be objects")
    return value

def _prebind_gateway_socket(gateway: str, *, role: str) -> tuple[socket.socket, PreboundServiceSocketPlanV1]:
    import ipaddress
    address = ipaddress.ip_address(gateway)
    if address.version != 4 or not address.is_private or address.is_loopback or address.is_link_local:
        raise RuntimeError("gateway service socket requires an exact private non-loopback IPv4 address")
    candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP)
    try:
        ip_freebind = getattr(socket, "IP_FREEBIND", 15)
        candidate.setsockopt(socket.IPPROTO_IP, ip_freebind, 1)
        if candidate.getsockopt(socket.IPPROTO_IP, ip_freebind) != 1:
            raise RuntimeError("IP_FREEBIND could not be verified")
        candidate.bind((gateway, 0))
        candidate.listen()
        local = candidate.getsockname()
        if type(local) is not tuple or len(local) < 2 or local[0] != gateway or type(local[1]) is not int or not 1 <= local[1] <= 65535:
            raise RuntimeError("prebound gateway socket identity mismatch")
        metadata = os.fstat(candidate.fileno())
        unsigned = {
            "schema_version": "bb.rl.harness-prebound-service-socket-plan.v1",
            "role": role,
            "gateway": gateway,
            "observed_port": local[1],
            "family": "AF_INET",
            "socket_type": "SOCK_STREAM",
            "protocol": "IPPROTO_TCP",
            "socket_device": metadata.st_dev,
            "socket_inode": metadata.st_ino,
            "socket_mode": metadata.st_mode,
            "socket_owner_uid": metadata.st_uid,
            "getsockname_host": local[0],
            "getsockname_port": local[1],
            "ip_freebind": True,
        }
        plan = PreboundServiceSocketPlanV1.model_validate({**unsigned, "socket_plan_id": sha(canon(unsigned))}, strict=True)
        return candidate, plan
    except BaseException:
        candidate.close()
        raise


def _prebind_gateway_services(gateway: str) -> tuple[dict[str, socket.socket], tuple[PreboundServiceSocketPlanV1, ...]]:
    sockets: dict[str, socket.socket] = {}
    plans: list[PreboundServiceSocketPlanV1] = []
    try:
        for role in ("fixed_policy", "harness"):
            owned, plan = _prebind_gateway_socket(gateway, role=role)
            sockets[role] = owned
            plans.append(plan)
        return sockets, tuple(plans)
    except BaseException:
        for owned in sockets.values():
            owned.close()
        raise



def _prepare_live_dynamic(
    static_fragment: Path,
    packet_path: Path,
    private_root: Path,
):
    import ipaddress
    import ssl

    from breadboard.rl.harness.composition import (
        ArtifactFileRefV1,
        EvidenceReceiptSigningAuthorityV1,
        EvidenceReceiptSigningHandoff,
        TlsCallbackPolicyV1,
        TlsCallbackRuntimeInputV1,
    )
    from breadboard.rl.phase5.f2_authority_authoring import (
        CallbackObservationSigningKeyHandoffV1,
        EvidenceReceiptSigningKeyRuntimeHandoffV1,
        F2C4StaticAuthorityFragment,
        F2C4TargetDynamicObservations,
        F2C4TargetDynamicPlanInput,
        TlsCallbackLiveHandoffV1,
        TlsCallbackSocketRuntimeHandoffV1,
        TlsPrivateKeyRuntimeHandoffV1,
        author_f2_target_dynamic_authority,
    )
    from breadboard.rl.phase5.f2_composition import SourceArtifact, TlsAuthorityInput

    packet_raw = packet_path.read_bytes()
    packet = json.loads(packet_raw)
    if canon(packet) != packet_raw or set(packet) != {"plan", "observations"}:
        raise ValueError("target dynamic authoring packet is not exact canonical JSON")
    plan = F2C4TargetDynamicPlanInput.model_validate_json(
        canon(packet["plan"]), strict=True
    )
    target_ip = ipaddress.ip_address(plan.outer_bridge_plan.gateway)
    if (
        target_ip.version != 4
        or not target_ip.is_private
        or target_ip.is_loopback
        or target_ip.is_unspecified
        or target_ip.is_multicast
        or target_ip.is_link_local
        or str(target_ip) != plan.outer_bridge_plan.gateway
    ):
        raise ValueError("callback host must be the exact private bridge gateway")
    signing_fd = -1
    receipt_signing_fd = -1
    callback_socket: socket.socket | None = None
    key_fd = -1
    try:
        callback_socket, callback_plan = _prebind_gateway_socket(str(target_ip), role="callback_tls")
        observed_host, observed_port = callback_socket.getsockname()
        if observed_host != str(target_ip):
            raise RuntimeError("callback socket did not bind exact bridge gateway")
        private_root.mkdir(mode=0o700, parents=False)
        static = F2C4StaticAuthorityFragment.model_validate_json(static_fragment.read_bytes(), strict=True)
        openssl = static.authority.openssl.path
        ca_key = private_root / "ca.key"
        ca_pem = private_root / "ca.pem"
        leaf_key = private_root / "leaf.key"
        leaf_csr = private_root / "leaf.csr"
        leaf_pem = private_root / "leaf.pem"
        leaf_der = private_root / "leaf.der"
        public_der = private_root / "leaf-public.der"
        extensions = private_root / "leaf.ext"
        extensions.write_text(
            f"subjectAltName=IP:{target_ip}\nbasicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
        )
        commands = (
            [openssl, "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(ca_key)],
            [openssl, "req", "-x509", "-new", "-sha256", "-days", "1", "-key", str(ca_key), "-subj", f"/CN=f2-ca-{plan.attempt_id}", "-out", str(ca_pem)],
            [openssl, "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(leaf_key)],
            [openssl, "req", "-new", "-sha256", "-key", str(leaf_key), "-subj", f"/CN={target_ip}", "-addext", f"subjectAltName=IP:{target_ip}", "-out", str(leaf_csr)],
            [openssl, "x509", "-req", "-sha256", "-days", "1", "-in", str(leaf_csr), "-CA", str(ca_pem), "-CAkey", str(ca_key), "-CAcreateserial", "-extfile", str(extensions), "-out", str(leaf_pem)],
            [openssl, "x509", "-in", str(leaf_pem), "-outform", "DER", "-out", str(leaf_der)],
            [openssl, "pkey", "-in", str(leaf_key), "-pubout", "-outform", "DER", "-out", str(public_der)],
        )
        for command in commands:
            completed = subprocess.run(command, capture_output=True, check=False, timeout=30, env=_OPENSSL_ENV)
            if completed.returncode != 0:
                raise RuntimeError("pinned OpenSSL TLS authority generation failed")
        ca_key.unlink()
        leaf_csr.unlink()
        extensions.unlink()
        serial = private_root / "ca.srl"
        if serial.exists():
            serial.unlink()
        leaf_key.chmod(0o400)
        ca_raw, leaf_raw = ca_pem.read_bytes(), leaf_pem.read_bytes()
        leaf_public_ref = sha(public_der.read_bytes())
        ca_source = SourceArtifact(path=str(ca_pem), sha256=sha(ca_raw), media_type="application/x-pem-file")
        leaf_source = SourceArtifact(path=str(leaf_pem), sha256=sha(leaf_raw), media_type="application/x-pem-file")
        tls = TlsAuthorityInput(
            route_id="f2-fixed-policy-callback",
            target_ip=str(target_ip),
            ca_certificate=ca_source,
            leaf_certificate=leaf_source,
            expected_leaf_der_sha256=sha(leaf_der.read_bytes()),
            minimum_tls_version="TLSv1.3",
            cipher_suite="TLS_AES_256_GCM_SHA384",
            dedicated_single_leaf_ca=True,
        )
        observation_value = dict(packet["observations"])
        tls_handles = [
            item["handle_id"]
            for item in observation_value["secret_handles"]["records"]
            if item["purpose"] == "callback_tls_private_key"
        ]
        if len(tls_handles) != 1:
            raise ValueError("target dynamic packet requires one TLS private-key handle")
        observation_signing_handles = [
            item["handle_id"]
            for item in observation_value["secret_handles"]["records"]
            if item["purpose"] == "callback_observation_signing_key"
        ]
        if len(observation_signing_handles) != 1:
            raise ValueError("target dynamic packet requires one callback observation signing handle")
        observation_value["callback_observation_signing_key_handle_id"] = observation_signing_handles[0]
        receipt_signing_handles = [
            item["handle_id"]
            for item in observation_value["secret_handles"]["records"]
            if item["purpose"] == "evidence_receipt_signing_key"
        ]
        if len(receipt_signing_handles) != 1:
            raise ValueError("target dynamic packet requires one evidence receipt signing handle")
        receipt_private = private_root / "evidence-receipt.key"
        receipt_public = private_root / "evidence-receipt.pub.pem"
        receipt_public_der = private_root / "evidence-receipt.pub.der"
        for command in (
            [openssl, "genpkey", "-algorithm", "ED25519", "-out", str(receipt_private)],
            [openssl, "pkey", "-in", str(receipt_private), "-pubout", "-out", str(receipt_public)],
            [openssl, "pkey", "-in", str(receipt_private), "-pubout", "-outform", "DER", "-out", str(receipt_public_der)],
        ):
            completed = subprocess.run(command, capture_output=True, check=False, timeout=30, env=_OPENSSL_ENV)
            if completed.returncode != 0:
                raise RuntimeError("pinned OpenSSL evidence receipt key generation failed")
        receipt_private.chmod(0o400)
        receipt_public_raw = receipt_public.read_bytes()
        authority_template = observation_value["evidence_receipt_signing_authority"]
        receipt_authority = EvidenceReceiptSigningAuthorityV1(
            schema_version="bb.rl.harness-evidence-receipt-signing-authority.v1",
            attempt_id=plan.attempt_id,
            composition_digest=authority_template["composition_digest"],
            evidence_policy_digest=observation_value["callback_observation_evidence_policy_revision_digest"],
            algorithm="Ed25519",
            public_key_ref=ArtifactFileRefV1(path=str(receipt_public), sha256=sha(receipt_public_raw), size_bytes=len(receipt_public_raw), media_type="application/x-pem-file"),
            public_key_sha256=sha(receipt_public_raw),
            public_key_spki_sha256=sha(receipt_public_der.read_bytes()),
            private_key_secret_handle_id=receipt_signing_handles[0],
            openssl_authority_digest=sha(canon(static.authority.openssl.model_dump(mode="json"))),
        )
        observation_value["evidence_receipt_signing_authority"] = receipt_authority.model_dump(mode="json")
        existing_plans = [
            item for item in observation_value["prebound_service_socket_plans"]
            if item["role"] != "callback_tls"
        ]
        observation_value["prebound_service_socket_plans"] = sorted(
            existing_plans + [callback_plan.model_dump(mode="json")],
            key=lambda item: item["role"],
        )
        observation_value.update(
            callback_observed_port=observed_port,
            tls_private_key_secret_handle_id=tls_handles[0],
            tls_leaf_public_key_sha256=leaf_public_ref,
            tls=tls.model_dump(mode="json"),
        )
        observations = F2C4TargetDynamicObservations.model_validate_json(
            canon(observation_value), strict=True
        )
        dynamic = author_f2_target_dynamic_authority(plan, observations)
        signing_key = private_root / "callback-observation.key"
        signing_descriptor = os.open(signing_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), 0o400)
        try:
            os.write(signing_descriptor, os.urandom(32))
            os.fsync(signing_descriptor)
        finally:
            os.close(signing_descriptor)
        signing_fd = os.open(signing_key, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        signing_stat = os.fstat(signing_fd)
        signing_handoff = CallbackObservationSigningKeyHandoffV1(
            handle_id=observation_signing_handles[0],
            path=str(signing_key),
            descriptor_fd=signing_fd,
            device=signing_stat.st_dev,
            inode=signing_stat.st_ino,
            ctime_ns=signing_stat.st_ctime_ns,
            size_bytes=signing_stat.st_size,
            mode=stat.S_IMODE(signing_stat.st_mode),
            owner_uid=signing_stat.st_uid,
            key_sha256=sha(signing_key.read_bytes()),
        )
        signing_handoff.validate_against(dynamic)
        receipt_signing_fd = os.open(receipt_private, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        receipt_signing_handoff = EvidenceReceiptSigningKeyRuntimeHandoffV1(
            path=str(receipt_private),
            handoff=EvidenceReceiptSigningHandoff(
                authority=receipt_authority,
                private_key_fd=receipt_signing_fd,
            ),
            openssl=static.authority.openssl,
        )
        receipt_signing_handoff.validate_against(
            dynamic,
            composition_digest=receipt_authority.composition_digest,
            evidence_policy_digest=receipt_authority.evidence_policy_digest,
            openssl_authority_digest=receipt_authority.openssl_authority_digest,
        )
        socket_stat = os.fstat(callback_socket.fileno())
        key_fd = os.open(leaf_key, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        key_stat = os.fstat(key_fd)
        runtime = TlsCallbackRuntimeInputV1(
            schema_version="bb.rl.harness-tls-callback-runtime-input.v1",
            route_id=tls.route_id,
            host=str(target_ip),
            observed_port=observed_port,
            socket_role="callback_tls",
            socket_plan_id=callback_plan.socket_plan_id,
            ca_certificate_ref=ArtifactFileRefV1(path=str(ca_pem), sha256=sha(ca_raw), size_bytes=len(ca_raw), media_type="application/x-pem-file"),
            leaf_certificate_ref=ArtifactFileRefV1(path=str(leaf_pem), sha256=sha(leaf_raw), size_bytes=len(leaf_raw), media_type="application/x-pem-file"),
            ca_certificate_sha256=sha(ca_raw),
            leaf_certificate_sha256=sha(leaf_raw),
            leaf_public_key_sha256=leaf_public_ref,
            private_key_secret_handle_id=tls_handles[0],
            tls_policy=TlsCallbackPolicyV1(
                minimum_tls_version="TLSv1.3",
                maximum_tls_version="TLSv1.3",
                server_certificate_verification_required=True,
                hostname_verification_required=True,
                bearer_authentication_required=True,
                mutual_tls_required=False,
            ),
        )
        key_handoff = TlsPrivateKeyRuntimeHandoffV1(
            path=str(leaf_key),
            descriptor_fd=key_fd,
            device=key_stat.st_dev,
            inode=key_stat.st_ino,
            ctime_ns=key_stat.st_ctime_ns,
            size_bytes=key_stat.st_size,
            mode=stat.S_IMODE(key_stat.st_mode),
            owner_uid=key_stat.st_uid,
            private_key_sha256=sha(leaf_key.read_bytes()),
            leaf_certificate_sha256=sha(leaf_raw),
            leaf_public_key_sha256=leaf_public_ref,
        )
        socket_handoff = TlsCallbackSocketRuntimeHandoffV1(
            descriptor_fd=callback_socket.fileno(),
            gateway=str(target_ip),
            observed_port=observed_port,
            socket_device=socket_stat.st_dev,
            socket_inode=socket_stat.st_ino,
            socket_mode=socket_stat.st_mode,
            socket_owner_uid=socket_stat.st_uid,
        )
        live = TlsCallbackLiveHandoffV1(runtime_input=runtime, tls_private_key=key_handoff, callback_socket=socket_handoff)
        live.validate_against(dynamic)
        return dynamic, live, signing_handoff, receipt_signing_handoff, callback_socket, key_fd, signing_fd, receipt_signing_fd
    except BaseException:
        if callback_socket is not None:
            callback_socket.close()
        if key_fd >= 0:
            os.close(key_fd)
        if signing_fd >= 0:
            os.close(signing_fd)
        if receipt_signing_fd >= 0:
            os.close(receipt_signing_fd)
        raise


def _measure_leaf_der_digest(certificate: Path, openssl_path: str) -> str:
    measured = subprocess.run(
        [openssl_path, "x509", "-in", str(certificate), "-outform", "DER"],
        capture_output=True,
        check=False,
        timeout=30,
        env=_OPENSSL_ENV,
    )
    if measured.returncode != 0 or not measured.stdout:
        raise ValueError("pinned OpenSSL could not measure leaf certificate DER")
    return sha(measured.stdout)

def _verify_leaf_der_digest(certificate: Path, expected: str, openssl_path: str) -> str:
    measured = _measure_leaf_der_digest(certificate, openssl_path)
    if measured != expected:
        raise RuntimeError("callback leaf DER digest does not match pinned TLS trust authority")
    return measured



def verify_callback_journal_receipt(
    *,
    journal: bytes,
    snapshot: bytes,
    receipt_raw: bytes,
    signature_raw: bytes,
    authority_raw: bytes,
    public_key: bytes,
    openssl_path: str = "/usr/bin/openssl",
) -> dict[str, object]:
    from breadboard.rl.harness.composition import (
        CallbackJournalVerificationReceiptV1,
        EvidenceReceiptSignatureV1,
        EvidenceReceiptSigningAuthorityV1,
    )

    authority = EvidenceReceiptSigningAuthorityV1.model_validate_json(authority_raw, strict=True)
    receipt = CallbackJournalVerificationReceiptV1.model_validate_json(receipt_raw, strict=True)
    signature = EvidenceReceiptSignatureV1.model_validate_json(signature_raw, strict=True)
    if (
        canon(json.loads(authority_raw)) != authority_raw
        or canon(json.loads(receipt_raw)) != receipt_raw
        or canon(json.loads(signature_raw)) != signature_raw
        or receipt.journal_ref.sha256 != sha(journal)
        or receipt.journal_ref.size_bytes != len(journal)
        or receipt.snapshot_ref.sha256 != sha(snapshot)
        or receipt.snapshot_ref.size_bytes != len(snapshot)
        or authority.public_key_sha256 != sha(public_key)
        or receipt.signer_authority_digest != authority.canonical_digest()
        or receipt.signer_public_key_spki_sha256 != authority.public_key_spki_sha256
        or signature.signer_authority_digest != authority.canonical_digest()
        or signature.receipt_digest != receipt.canonical_digest()
    ):
        raise ValueError("callback verification receipt join is invalid")
    with tempfile.TemporaryDirectory(prefix="f2-receipt-verify-") as temporary:
        root = Path(temporary)
        receipt_path = root / "receipt.json"
        public_path = root / "public.pem"
        signature_path = root / "signature.bin"
        receipt_path.write_bytes(receipt_raw)
        public_path.write_bytes(public_key)
        signature_path.write_bytes(base64.b64decode(signature.signature_base64, validate=True))
        verified = subprocess.run(
            [
                openssl_path, "pkeyutl", "-verify", "-pubin", "-rawin",
                "-inkey", str(public_path),
                "-in", str(receipt_path),
                "-sigfile", str(signature_path),
            ],
            capture_output=True,
            check=False,
            timeout=30,
            env=_OPENSSL_ENV,
        )
    if verified.returncode != 0:
        raise ValueError("callback verification receipt Ed25519 signature is invalid")
    return {
        "authority_digest": authority.canonical_digest(),
        "receipt_digest": receipt.canonical_digest(),
        "event_count": receipt.event_count,
    }


def _project_prebound_socket_leases(session: object) -> list[dict[str, object]]:
    leases = session.prebound_service_sockets
    roles = tuple(sorted(leases))
    expected = ("callback_tls", "fixed_policy", "harness")
    if roles != expected:
        raise RuntimeError("retained composition socket leases are not exact")
    projected = []
    for role in roles:
        lease = leases[role]
        if getattr(lease, "role", None) != role:
            raise RuntimeError("retained composition socket lease role mismatch")
        projected.append(lease.model_dump(mode="json"))
    return projected


def _open_f2_production_session(
    static_fragment: Path,
    dynamic_value: dict[str, object],
    root: Path,
    live_callback_runtime: object,
    observation_signing_handoff: object,
    evidence_receipt_signing_handoff: object,
    input_hashes: dict[str, str],
):
    from breadboard.rl.harness.composition import OuterBridgePlanV1
    from breadboard.rl.phase5.f2_authority_authoring import (
        F2C4DynamicAuthorityInput,
        author_f2_operator_input,
        materialize_f2_c4_semantic_input,
    )
    from breadboard.rl.phase5.f2_composition import (
        F2ProductionCompositionInput,
        open_f2_production_composition,
    )
    static_fragment = static_fragment.resolve(strict=True)
    root = root.absolute()
    if root.exists():
        raise FileExistsError(root)
    value = json.loads(canon(dynamic_value))
    bridge_plan = OuterBridgePlanV1.model_validate(value.get("outer_bridge_plan"), strict=True)
    sockets, plans = _prebind_gateway_services(bridge_plan.gateway)
    callback_plans = [
        PreboundServiceSocketPlanV1.model_validate(item, strict=True)
        for item in value.get("prebound_service_socket_plans", [])
        if item.get("role") == "callback_tls"
    ]
    if len(callback_plans) != 1:
        for owned in sockets.values():
            owned.close()
        raise RuntimeError("dynamic authority requires one callback TLS socket plan")
    sockets["callback_tls"] = socket.fromfd(
        live_callback_runtime.callback_socket.descriptor_fd,
        socket.AF_INET,
        socket.SOCK_STREAM,
    )
    plans = tuple(sorted((*plans, callback_plans[0]), key=lambda item: item.role))
    session = None
    try:
        value["prebound_service_socket_plans"] = [plan.model_dump(mode="json") for plan in plans]
        dynamic = F2C4DynamicAuthorityInput.model_validate_json(
            canon(value), strict=True
        )
        live_key = live_callback_runtime.tls_private_key
        callback_runtime = live_callback_runtime.runtime_input
        semantic_path = root / "semantic" / "semantic-input.json"
        materialize_f2_c4_semantic_input(str(static_fragment), dynamic, str(semantic_path))
        input_hashes["semantic_input"] = sha(semantic_path.read_bytes())
        operator_path = Path(author_f2_operator_input(str(semantic_path), str(root / "authored")))
        operator_raw = operator_path.read_bytes()
        operator = json.loads(operator_raw)
        if canon(operator) != operator_raw:
            raise RuntimeError("authored production input is not canonical")
        spec = F2ProductionCompositionInput.model_validate_json(
            operator_raw, strict=True
        )
        session = open_f2_production_composition(
            spec,
            str(root / "composition"),
            prebound_service_socket_fds={role: owned.fileno() for role, owned in sockets.items()},
            callback_tls_runtime=callback_runtime,
            callback_tls_private_key_fd=live_key.descriptor_fd,
            live_secret_files={
                live_callback_runtime.runtime_input.private_key_secret_handle_id: live_key.path,
                observation_signing_handoff.handle_id: observation_signing_handoff.path,
                evidence_receipt_signing_handoff.handoff.authority.private_key_secret_handle_id: evidence_receipt_signing_handoff.path,
            },
        )
        return session, sockets, semantic_path, operator_path
    except BaseException:
        if session is not None:
            import asyncio
            asyncio.run(session.close())
        for owned in sockets.values():
            owned.close()
        raise


def _verify_pinned_host_runtime() -> Path:
    report_raw = HOST_RUNTIME_REPORT.read_bytes()
    if sha(report_raw) != HOST_RUNTIME_REPORT_REF:
        raise RuntimeError("pinned host runtime report authority mismatch")
    report = json.loads(report_raw)
    expected = report.get("file_inventory")
    if type(expected) is not list or report.get("runtime_python", {}).get("sha256") != HOST_RUNTIME_PYTHON_REF or report.get("sealed_read_only") is not True:
        raise RuntimeError("pinned host runtime report is invalid")
    observed = []
    for path in sorted(HOST_RUNTIME_ROOT.rglob("*")):
        relative = path.relative_to(HOST_RUNTIME_ROOT).as_posix()
        if path.is_symlink():
            target = os.readlink(path)
            if Path(target).is_absolute() or ".." in Path(target).parts:
                raise RuntimeError("pinned host runtime contains unsafe symlink")
            observed.append({"path": relative, "type": "symlink", "target": target})
        elif path.is_file():
            raw = path.read_bytes()
            observed.append({"path": relative, "type": "file", "size": len(raw), "sha256": sha(raw), "mode": stat.S_IMODE(path.stat().st_mode)})
        elif path.is_dir() and stat.S_IMODE(path.stat().st_mode) != 0o555:
            raise RuntimeError("pinned host runtime directory seal changed")
    if observed != expected or stat.S_IMODE(HOST_RUNTIME_ROOT.stat().st_mode) != 0o555:
        raise RuntimeError("pinned host runtime inventory changed")
    python = HOST_RUNTIME_ROOT / "bin/python"
    if sha(python.read_bytes()) != HOST_RUNTIME_PYTHON_REF:
        raise RuntimeError("pinned host runtime Python changed")
    return python


def _execute_retained_episode(
    *,
    session: object,
    sockets: dict[str, socket.socket],
    semantic: dict[str, object],
    bundle_root: Path,
    credential_file: Path,
    attempt_id: str,
    target_run_id: str,
    wrapper_image_ref: str,
    authorities: dict[str, str],
    live_callback_runtime: object,
    observation_signing_handoff: object,
    evidence_receipt_signing_handoff: object,
) -> dict[str, object]:
    import asyncio
    import ssl
    import threading
    import time

    import uvicorn

    from breadboard.rl.harness.__main__ import _LifecycleServer
    from breadboard.rl.harness.composition import (
        ArtifactFileRefV1,
        CallbackJournalVerificationReceiptV1,
        EvidenceReceiptSignatureV1,
    )
    from recipe.nemo_async.bridge.production_callback import (
        CallbackApplication,
        CallbackAuthority,
        FixedPolicyHttpApplication,
        LivePolicyClient,
        make_http_server,
    )
    from recipe.nemo_async.bridge.production_contract import CanonicalObservationSink, canonical_sha256
    from recipe.nemo_async.tools.policy.fixed_real_policy import FixedPolicyAuthority, FixedRealPolicy
    from recipe.nemo_async.tools.policy.fixed_real_policy_server import make_prebound_fixed_policy_http_server

    del live_callback_runtime
    composition = session.composition
    lease = session.outer_bridge_lease
    plan = semantic["outer_bridge_plan"]
    fixed_socket = sockets["fixed_policy"]
    harness_socket = sockets["harness"]
    records = semantic["secret_handles"]["records"]
    api_handles = [record["handle_id"] for record in records if record["purpose"] == "api_bearer"]
    callback_handles = [record["handle_id"] for record in records if record["purpose"] == "policy_callback"]
    if len(api_handles) != 1 or len(callback_handles) != 1:
        raise RuntimeError("exactly one API bearer and policy callback handle are required")
    token_file = Path(semantic["secret_files"][api_handles[0]])
    callback_token_file = Path(semantic["secret_files"][callback_handles[0]])
    policy_authority = FixedPolicyAuthority(
        model_label=semantic["model"]["model_digest"],
        instance_id=attempt_id,
        script_id=semantic["tool_implementation_digest"],
        shell_command=semantic["shell_command"],
        completion=semantic["completion"],
    )
    policy_server = make_prebound_fixed_policy_http_server(
        FixedPolicyHttpApplication(FixedRealPolicy(policy_authority), token_file),
        fixed_socket,
        owns_socket=False,
    )
    harness_server = _LifecycleServer(
        uvicorn.Config(composition.app, host=composition.server.host, port=composition.server.port, proxy_headers=composition.server.proxy_headers, log_config=None),
        composition.app.state.episode_service.close,
    )
    harness_owned = harness_socket.dup()
    route = composition.manifest.authority.policy_http.routes[0]
    callback_runtime = session.callback_tls_runtime
    expected_leaf_der = composition.manifest.authority.tls_trust[0].expected_leaf_certificate_sha256
    _verify_leaf_der_digest(
        Path(callback_runtime.leaf_certificate_ref.path),
        expected_leaf_der,
        evidence_receipt_signing_handoff.openssl.path,
    )
    observation_root = Path(session.build.composition_manifest_path).parent / "callback-observations"
    observation_root.mkdir(mode=0o700)
    tls_trust_path = observation_root / "policy-tls-trust-authority.json"
    tls_trust_raw = canon(
        composition.manifest.authority.tls_trust[0].model_dump(mode="json")
    )
    tls_trust_fd = os.open(tls_trust_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), 0o600)
    try:
        os.write(tls_trust_fd, tls_trust_raw)
        os.fsync(tls_trust_fd)
    finally:
        os.close(tls_trust_fd)
    observation_journal = observation_root / "journal.jsonl"
    observation_snapshot = observation_root / "snapshot.json"
    journal_fd = os.open(observation_journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), 0o600)
    os.close(journal_fd)
    observation_sink = CanonicalObservationSink(
        observation_journal,
        observation_snapshot,
        schema_version="bb.wrapper.callback-observation-snapshot.v1",
        max_records=3,
        signing_key_file=observation_signing_handoff.path,
    )
    route_observation = {
        "schema_version": "bb.wrapper.callback-tls-route-observation.v1",
        "route_id": route.grant.route_id,
        "route_revision_digest": route.grant.route_revision_digest,
        "dns_policy_digest": route.dns_policy_digest,
        "ip_policy_digest": route.ip_policy_digest,
        "bind_address": callback_runtime.host,
        "bind_port": callback_runtime.observed_port,
        "server_hostname": callback_runtime.host,
        "minimum_tls_version": "TLSv1.3",
        "cipher_suite": "TLS_AES_256_GCM_SHA384",
        "ca_bundle_sha256": callback_runtime.ca_certificate_sha256,
        "ca_certificate_pem": Path(callback_runtime.ca_certificate_ref.path).read_text(),
        "leaf_certificate_sha256": callback_runtime.leaf_certificate_sha256,
        "leaf_certificate_pem": Path(callback_runtime.leaf_certificate_ref.path).read_text(),
    }
    observation_sink.append(
        route_observation,
        idempotency_key=canonical_sha256({
            "schema_version": "bb.wrapper.callback-tls-route-idempotency.v1",
            "route_id": route.grant.route_id,
            "route_digest": route.grant.route_revision_digest,
            "bind_address": callback_runtime.host,
            "bind_port": callback_runtime.observed_port,
        }),
    )
    callback_authority = CallbackAuthority(
        callback_path=route.paths[0],
        callback_token_file=callback_token_file,
        policy_url=f"http://{plan['gateway']}:{fixed_socket.getsockname()[1]}/v1/responses",
        policy_token_file=token_file,
        model_label=policy_authority.model_label,
        shell_command=policy_authority.shell_command,
        completion=policy_authority.completion,
        code_digest=policy_authority.code_digest,
        script_digest=policy_authority.script_digest,
        model_label_digest=policy_authority.model_label_digest,
        instance_digest=policy_authority.instance_digest,
    )
    tls_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls_context.minimum_version = ssl.TLSVersion.TLSv1_3
    tls_context.maximum_version = ssl.TLSVersion.TLSv1_3
    tls_context.verify_mode = ssl.CERT_NONE
    tls_context.load_cert_chain(callback_runtime.leaf_certificate_ref.path, f"/proc/self/fd/{session.callback_tls_private_key_fd}")
    callback_application = CallbackApplication(
        callback_authority,
        LivePolicyClient(callback_authority),
        observation_sink=observation_sink,
        tls_observation_authority={
            "leaf_der_digest": callback_runtime.leaf_certificate_sha256,
            "ca_authority_digest": callback_runtime.ca_certificate_sha256,
            "server_name": callback_runtime.host,
            "leaf_der_digest": composition.manifest.authority.tls_trust[0].expected_leaf_certificate_sha256,
            "route_digest": route.grant.route_revision_digest,
        },
    )
    callback_server = make_http_server(
        callback_application,
        callback_runtime.host,
        callback_runtime.observed_port,
        tls_context=tls_context,
        required_tls_cipher="TLS_AES_256_GCM_SHA384",
        prebound_socket=socket.socket(fileno=os.dup(session.callback_tls_socket_fd)),
    )
    policy_thread = threading.Thread(target=policy_server.serve_forever, name="f2-fixed-policy", daemon=False)
    harness_thread = threading.Thread(target=lambda: harness_server.run(sockets=[harness_owned]), name="f2-harness", daemon=False)
    callback_thread = threading.Thread(target=callback_server.serve_forever, name="f2-tls-callback", daemon=False)
    policy_thread.start()
    callback_thread.start()
    harness_thread.start()
    result: dict[str, object] | None = None
    try:
        deadline = time.monotonic() + 30.0
        while not harness_server.started:
            if not harness_thread.is_alive() or not callback_thread.is_alive() or not policy_thread.is_alive() or time.monotonic() >= deadline:
                raise RuntimeError("retained production service did not start")
            time.sleep(0.01)
        socket_plans = {item["role"]: item for item in semantic["prebound_service_socket_plans"]}
        labels = plan["labels"]
        argv = [
            str(_verify_pinned_host_runtime()),
            str(bundle_root / "scripts/rl_phase5/f2_container_entry.py"),
            "--attempt-id", attempt_id,
            "--target-run-id", target_run_id,
            "--source-root", str(bundle_root),
            "--credential-file", str(credential_file),
            "--wrapper-image-ref", wrapper_image_ref,
            "--bridge-network-id", lease.network_id,
            "--bridge-name", lease.network_name,
            "--bridge-label", labels[0]["key"] + "=" + labels[0]["value"],
            "--bridge-subnet", plan["subnet"],
            "--bridge-gateway", plan["gateway"],
            "--bridge-harness-port", str(socket_plans["harness"]["observed_port"]),
            "--bridge-fixed-policy-port", str(socket_plans["fixed_policy"]["observed_port"]),
            "--bridge-callback-port", str(callback_runtime.observed_port),
            "--callback-ca-file", callback_runtime.ca_certificate_ref.path,
            "--policy-tls-trust-authority", str(tls_trust_path),
        ]
        for key, value in authorities.items():
            argv += ["--" + key.replace("_", "-"), value]
        result = _run(argv)
    finally:
        callback_server.shutdown()
        callback_server.server_close()
        policy_server.shutdown()
        policy_server.server_close()
        harness_server.should_exit = True
        callback_thread.join(timeout=30)
        policy_thread.join(timeout=30)
        harness_thread.join(timeout=30)
        observation_sink.close()
        harness_owned.close()
        asyncio.run(session.close())
        for owned in sockets.values():
            owned.close()
        if callback_thread.is_alive() or policy_thread.is_alive() or harness_thread.is_alive():
            raise RuntimeError("production services did not terminate")
    verified_sink = CanonicalObservationSink(
        observation_journal,
        observation_snapshot,
        schema_version="bb.wrapper.callback-observation-snapshot.v1",
        max_records=3,
        signing_key_file=observation_signing_handoff.path,
    )
    try:
        verified_snapshot = verified_sink.snapshot()
    finally:
        verified_sink.close()
    if (
        result is not None
        and int(result["exit_code"]) == 0
        and (
            len(callback_application.observations()) != 2
            or verified_snapshot["entry_count"] != 3
            or verified_snapshot["records"][0] != route_observation
        )
    ):
        raise RuntimeError("callback journal did not close with one route and two authenticated turns")
    if result is None:
        raise RuntimeError("production episode did not execute")
    result["callback_observation_journal"] = observation_journal.read_bytes()
    journal_raw = observation_journal.read_bytes()
    snapshot_raw = observation_snapshot.read_bytes()
    receipt_authority = evidence_receipt_signing_handoff.handoff.authority
    receipt = CallbackJournalVerificationReceiptV1(
        schema_version="bb.rl.callback-journal-verification-receipt.v1",
        attempt_id=attempt_id,
        composition_digest=receipt_authority.composition_digest,
        route_id=route.grant.route_id,
        journal_ref=ArtifactFileRefV1(path=str(observation_journal), sha256=sha(journal_raw), size_bytes=len(journal_raw), media_type="application/x-ndjson"),
        snapshot_ref=ArtifactFileRefV1(path=str(observation_snapshot), sha256=sha(snapshot_raw), size_bytes=len(snapshot_raw), media_type="application/json"),
        head_mac=verified_snapshot["head_entry_mac"].removeprefix("hmac-sha256:"),
        event_count=verified_snapshot["entry_count"],
        chain_verified=True,
        snapshot_verified=True,
        evidence_policy_digest=receipt_authority.evidence_policy_digest,
        signer_public_key_spki_sha256=receipt_authority.public_key_spki_sha256,
        signer_authority_digest=receipt_authority.canonical_digest(),
    )
    receipt_path = observation_root / "verification-receipt.json"
    receipt_raw = receipt.canonical_bytes()
    receipt_fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0), 0o600)
    try:
        os.write(receipt_fd, receipt_raw)
        os.fsync(receipt_fd)
    finally:
        os.close(receipt_fd)
    signature_path = observation_root / "verification-receipt.sig"
    signed = subprocess.run(
        [
            evidence_receipt_signing_handoff.openssl.path,
            "pkeyutl", "-sign", "-rawin",
            "-inkey", f"/proc/self/fd/{evidence_receipt_signing_handoff.handoff.private_key_fd}",
            "-in", str(receipt_path),
            "-out", str(signature_path),
        ],
        capture_output=True,
        check=False,
        pass_fds=(evidence_receipt_signing_handoff.handoff.private_key_fd,),
        timeout=30,
        env=_OPENSSL_ENV,
    )
    if signed.returncode != 0:
        raise RuntimeError("pinned OpenSSL evidence receipt signing failed")
    signature = EvidenceReceiptSignatureV1(
        schema_version="bb.rl.evidence-receipt-signature.v1",
        algorithm="Ed25519",
        signer_authority_digest=receipt_authority.canonical_digest(),
        receipt_digest=receipt.canonical_digest(),
        signature_base64=base64.b64encode(signature_path.read_bytes()).decode("ascii"),
    )
    result["callback_verification_receipt"] = receipt_raw
    result["callback_verification_signature"] = signature.canonical_bytes()
    result["callback_verification_authority"] = receipt_authority.canonical_bytes()
    result["callback_verification_public_key"] = Path(receipt_authority.public_key_ref.path).read_bytes()
    result["callback_observation_snapshot"] = observation_snapshot.read_bytes()
    raw_runner = _parse_runner_observation(str(result["stdout"]).encode())
    raw_runner["container_inspect"]["outer_bridge_lease"] = lease.model_dump(mode="json")
    raw_runner["container_inspect"]["prebound_service_socket_leases"] = (
        _project_prebound_socket_leases(session)
    )
    raw_runner["post_cleanup"]["outer_bridge_cleanup_receipt"] = session.outer_bridge_cleanup_receipt.model_dump(mode="json")
    result["runner_observation"] = raw_runner
    return result


def remote_run(
    bundle_root: Path,
    attempt_id: str,
    credential_file: Path | None = None,
    static_authority_fragment: Path | None = None,
    dynamic_authority_input: Path | None = None,
    *,
    f1_prerequisite_ref: str,
    config_ref: str,
    task_ref: str,
    verifier_ref: str,
    policy_ref: str,
    live_callback_runtime: object | None = None,
    observation_signing_handoff: object | None = None,
    evidence_receipt_signing_handoff: object | None = None,
) -> int:
    if not _ATTEMPT.fullmatch(attempt_id):
        raise ValueError("invalid F2 attempt id")
    authorities = {
        "f1_prerequisite_ref": f1_prerequisite_ref,
        "config_ref": config_ref,
        "task_ref": task_ref,
        "verifier_ref": verifier_ref,
        "policy_ref": policy_ref,
    }
    if any(not _SHA.fullmatch(value) for value in authorities.values()):
        raise ValueError("all F2 authorities must be canonical lowercase sha256 refs")
    input_hashes = dict(authorities)
    if f1_prerequisite_ref != F1_PREREQUISITE_REF:
        raise ValueError("F2 requires the independently approved canonical F1 prerequisite")
    bundle_root = bundle_root.resolve(strict=True)
    if credential_file is not None:
        credential_file = credential_file.resolve(strict=True)
        if bundle_root == credential_file or bundle_root in credential_file.parents:
            raise ValueError("credential file must remain outside source/evidence")
        if (credential_file.stat().st_mode & 0o777) != 0o400:
            raise PermissionError("credential file mode must be 0400")
    if (static_authority_fragment is None) != (dynamic_authority_input is None):
        raise ValueError("static and dynamic composition authorities must be supplied together")
    if static_authority_fragment is not None and dynamic_authority_input is not None:
        static_authority_fragment = static_authority_fragment.resolve(strict=True)
        dynamic_authority_input = dynamic_authority_input.resolve(strict=True)
        if bundle_root not in static_authority_fragment.parents:
            raise ValueError("static authority fragment must be payload-bound")
        if bundle_root == dynamic_authority_input or bundle_root in dynamic_authority_input.parents:
            raise ValueError("same-job dynamic authority must remain outside source/evidence")
        if (dynamic_authority_input.stat().st_mode & 0o777) != 0o600:
            raise PermissionError("same-job dynamic authority mode must be 0600")
        input_hashes["static_authority"] = sha(static_authority_fragment.read_bytes())
        input_hashes["dynamic_authority"] = sha(dynamic_authority_input.read_bytes())
    requested_target_run_id = os.environ.get("PHASE3_TARGET_RUN_ID", "")
    slurm_job_id = os.environ.get("SLURM_JOB_ID", "")
    if not requested_target_run_id.endswith("-slurm-pending") or not slurm_job_id.isdigit():
        raise ValueError("F2 requires pending Phase3 target run id and numeric SLURM_JOB_ID")
    target_run_id = requested_target_run_id.removesuffix("pending") + slurm_job_id
    entries: dict[str, bytes] = {"target.stdout": b"", "target.stderr": b""}
    name, label = "bb-" + attempt_id, "bb.rl.f2.attempt=" + attempt_id
    container_id = ""
    rc = 125
    session_parent: Path | None = None
    prepared_callback_socket: socket.socket | None = None
    prepared_callback_key_fd = -1
    prepared_observation_signing_fd = -1
    prepared_evidence_receipt_signing_fd = -1
    report: dict[str, object] | None = None
    try:
        scheduler = {
            "schema_version": "bb.rl.f2.scheduler-observation.v1",
            "target_alias": TARGET_ALIAS,
            "requested": {"partition": PARTITION, "nodes": 1, "tasks": 1, "gpus": 1},
            "observed": {"job_id": os.environ.get("SLURM_JOB_ID", ""), "partition": os.environ.get("SLURM_JOB_PARTITION", ""), "node_list": os.environ.get("SLURM_JOB_NODELIST", os.environ.get("SLURM_NODELIST", "")), "node_count": int(os.environ.get("SLURM_JOB_NUM_NODES", "0")), "task_count": int(os.environ.get("SLURM_NTASKS", "0")), "gpus_on_node": os.environ.get("SLURM_GPUS_ON_NODE", ""), "hostname": socket.gethostname()},
            "os": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
            "started_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "scontrol": _run(["scontrol", "show", "job", "-o", os.environ.get("SLURM_JOB_ID", "")]),
        }
        entries["runner/scheduler.json"] = canon(scheduler)
        docker_identity = _docker_identity()
        entries["runner/docker-identity.json"] = canon(docker_identity)
        if static_authority_fragment is None or dynamic_authority_input is None:
            report = _component(attempt_id, target_run_id, passed=False, blocked_reason="runtime_unsupported", observation_hashes={**input_hashes, "docker_identity": sha(entries["runner/docker-identity.json"])})
            report["blocker"] = {"schema_version": "bb.rl.f2.runtime-blocker.v1", "code": "runtime_unsupported", "reason": "attested_composition_authorities_unavailable", "stage": "pre_create", "container_count": 0, "lease_count": 0, "reward_count": 0, "promotion_allowed": False}
            entries["target.stderr"] = b"runtime_unsupported: attested static and same-job dynamic composition authorities are required; container create was not attempted\n"
            return rc
        if credential_file is None:
            report = _component(attempt_id, target_run_id, passed=False, blocked_reason="credentials_unavailable", observation_hashes={**input_hashes, "docker_identity": sha(entries["runner/docker-identity.json"])})
            entries["target.stderr"] = b"credentials_unavailable: a 0400 credential file outside evidence is required; container create was not attempted\n"
            return rc
        session_parent = Path(tempfile.mkdtemp(prefix="f2-production-session-parent-"))
        if live_callback_runtime is None:
            dynamic, live_callback_runtime, observation_signing_handoff, evidence_receipt_signing_handoff, prepared_callback_socket, prepared_callback_key_fd, prepared_observation_signing_fd, prepared_evidence_receipt_signing_fd = _prepare_live_dynamic(
                static_authority_fragment,
                dynamic_authority_input,
                session_parent / "callback-private",
            )
            dynamic_value = dynamic.model_dump(mode="json")
        else:
            dynamic_raw = dynamic_authority_input.read_bytes()
            dynamic_value = json.loads(dynamic_raw)
            if canon(dynamic_value) != dynamic_raw:
                raise RuntimeError("same-job dynamic authority is not canonical")
            from breadboard.rl.phase5.f2_authority_authoring import (
                F2C4DynamicAuthorityInput,
                TlsCallbackLiveHandoffV1,
            )
            if not isinstance(live_callback_runtime, TlsCallbackLiveHandoffV1):
                raise TypeError("live callback runtime handoff must be exact")
            live_callback_runtime.validate_against(
                F2C4DynamicAuthorityInput.model_validate_json(
                    canon(dynamic_value), strict=True
                )
            )
            if observation_signing_handoff is None:
                raise TypeError("live callback observation signing handoff is required")
            if evidence_receipt_signing_handoff is None:
                raise TypeError("live evidence receipt signing handoff is required")
        _verify_pinned_host_runtime()
        session, sockets, semantic_path, _operator_path = _open_f2_production_session(
            static_authority_fragment,
            dynamic_value,
            session_parent / "session",
            live_callback_runtime,
            observation_signing_handoff,
            evidence_receipt_signing_handoff,
            input_hashes,
        )
        semantic = json.loads(semantic_path.read_bytes())
        authorization = json.loads((Path(session.build.composition_manifest_path).parent / "wrapper-image-operator-authorization.json").read_bytes())
        installed_images = [item for item in session.composition.manifest.installed.images if item.image_digest == authorization["image_id"]]
        if len(installed_images) != 1:
            raise RuntimeError("authorized wrapper image is not exact in composition")
        wrapper_image = installed_images[0]
        image_authority = {
            "binding": "composition-owned",
            "immutable_reference": wrapper_image.immutable_reference,
            "image_id": authorization["image_id"],
            "composition_digest": session.outer_bridge_lease.composition_digest,
            "outer_bridge_plan": semantic["outer_bridge_plan"],
        }
        entries["runner/image-inspect.json"] = canon({"schema_version": "bb.rl.f2.image-observation.v1", "requested_ref": wrapper_image.immutable_reference, "measured_image_id": authorization["image_id"], "admission": "composition_private_daemon_offline_authority", "authority": image_authority})
        started = _execute_retained_episode(
            session=session,
            sockets=sockets,
            semantic=semantic,
            bundle_root=bundle_root,
            credential_file=credential_file,
            attempt_id=attempt_id,
            target_run_id=target_run_id,
            wrapper_image_ref=wrapper_image.immutable_reference,
            authorities=authorities,
            live_callback_runtime=live_callback_runtime,
            observation_signing_handoff=observation_signing_handoff,
            evidence_receipt_signing_handoff=evidence_receipt_signing_handoff,
        )
        entries["target.stdout"], entries["target.stderr"] = str(started["stdout"]).encode(), str(started["stderr"]).encode()
        _verify_pinned_host_runtime()
        rc = int(started["exit_code"])
        if rc == 0:
            raw_runner = started["runner_observation"]
            entries["runner/image-inspect.json"] = canon(raw_runner["image_inspect"])
            entries["runner/container-inspect.json"] = canon(raw_runner["container_inspect"])
            entries["runner/post-cleanup.json"] = canon(raw_runner["post_cleanup"])
            entries["runner/callback-observation-journal.jsonl"] = started["callback_observation_journal"]
            entries["runner/callback-observation-snapshot.json"] = started["callback_observation_snapshot"]
            entries["runner/callback-verification-receipt.json"] = started["callback_verification_receipt"]
            entries["runner/callback-verification-signature.json"] = started["callback_verification_signature"]
            entries["runner/callback-verification-authority.json"] = started["callback_verification_authority"]
            entries["runner/callback-verification-public-key.pem"] = started["callback_verification_public_key"]
            try:
                validated = _validate_target_result(attempt_id, target_run_id, entries["target.stdout"], entries["target.stderr"])
            except Exception as exc:
                rc = 1
                report = _component(attempt_id, target_run_id, passed=False, blocked_reason="local_f2_validation_failed", observation_hashes={**input_hashes, "target_stdout": sha(entries["target.stdout"])})
                entries["target.stderr"] += f"local F2 validation failed: {type(exc).__name__}: {exc}\n".encode()
            else:
                report = _component(attempt_id, target_run_id, passed=True, blocked_reason="", observation_hashes={**input_hashes, "f2_report": sha(canon(validated)), "target_stdout": sha(entries["target.stdout"])})
    except Exception as exc:
        entries["target.stderr"] += f"runner error: {type(exc).__name__}: {exc}\n".encode()
        if report is None:
            report = _component(attempt_id, target_run_id, passed=False, blocked_reason="runner_failed", observation_hashes=input_hashes)
    finally:
        if prepared_callback_socket is not None:
            prepared_callback_socket.close()
        if prepared_callback_key_fd >= 0:
            os.close(prepared_callback_key_fd)
        if prepared_observation_signing_fd >= 0:
            os.close(prepared_observation_signing_fd)
        if prepared_evidence_receipt_signing_fd >= 0:
            os.close(prepared_evidence_receipt_signing_fd)
        session_root_absent = True
        if session_parent is not None:
            shutil.rmtree(session_parent, ignore_errors=True)
            session_root_absent = not session_parent.exists()
        if "runner/post-cleanup.json" not in entries:
            entries["runner/post-cleanup.json"] = canon({
                "schema_version": "bb.rl.f2.cleanup-observation.v1",
                "remove": {"exit_code": 0},
                "name_matches": [],
                "label_matches": [],
                "container_create_attempted": False,
                "outer_bridge_cleanup_receipt": {},
            })
        cleanup = json.loads(entries["runner/post-cleanup.json"])
        bridge_cleanup = cleanup.get("outer_bridge_cleanup_receipt", {})
        cleanup_ok = (
            cleanup.get("remove", {}).get("exit_code") == 0
            and cleanup.get("name_matches") == []
            and cleanup.get("label_matches") == []
            and cleanup.get("container_create_attempted") is True
            and bridge_cleanup.get("id_absent") is True
            and bridge_cleanup.get("name_absent") is True
            and bool(_SHA.fullmatch(str(bridge_cleanup.get("lease_id") or "")))
            and bool(_SHA.fullmatch(str(bridge_cleanup.get("lease_digest") or "")))
        )
        if report is None:
            report = _component(attempt_id, target_run_id, passed=False, blocked_reason="runner_failed", observation_hashes=input_hashes)
        if report.get("passed") is True and (not cleanup_ok or not session_root_absent):
            report = _component(attempt_id, target_run_id, passed=False, blocked_reason="cleanup_failed", observation_hashes={**input_hashes, "target_stdout": sha(entries["target.stdout"])})
            rc = 1
        entries["runner/component-report.json"] = canon(report)
        os.write(1, COMPONENT_PREFIX + canon(report) + b"\n")
        os.write(1, _envelope(RUNNER_PREFIX, _archive(entries)))
    return rc


_TARGET_RECORD_KEYS = {
    "schema_version", "ssh_alias", "host_key_fingerprint", "cluster_name",
    "controller", "partition", "account", "qos", "reservation", "owner",
}


def _bounded(argv: list[str], *, input_: bytes | None = None, timeout: int = 30) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(argv, input=input_, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"bounded command timed out: {argv[0]}") from exc


def precheck_and_submit(*, target_record_path: Path, known_hosts: Path, phase3_args: list[str]) -> int:
    target_raw = target_record_path.resolve(strict=True).read_bytes()
    target = json.loads(target_raw)
    if type(target) is not dict or set(target) != _TARGET_RECORD_KEYS or target["schema_version"] != "bb.rl.f2.ibm-target-record.v1":
        raise ValueError("exact operator IBM target record required")
    if target["ssh_alias"] != TARGET_ALIAS or target["partition"] != PARTITION:
        raise ValueError("operator target alias/partition mismatch")
    if not all(type(target[key]) is str and target[key] for key in _TARGET_RECORD_KEYS - {"schema_version"}):
        raise ValueError("operator target record fields must be non-empty strings")
    resolved = _bounded(["ssh", "-G", target["ssh_alias"]])
    if resolved.returncode != 0:
        raise RuntimeError("ssh alias resolution failed")
    ssh_config: dict[str, str] = {}
    for line in resolved.stdout.decode("utf-8", "strict").splitlines():
        key, separator, value = line.partition(" ")
        if separator and key not in ssh_config:
            ssh_config[key] = value.strip()
    hostname = ssh_config.get("hostname", "")
    if not hostname:
        raise ValueError("ssh -G did not resolve hostname")
    hosts = _bounded(["ssh-keygen", "-F", hostname, "-f", str(known_hosts.resolve(strict=True))])
    if hosts.returncode != 0:
        raise ValueError("resolved host absent from operator known_hosts")
    matched_lines, fingerprints = [], set()
    for line in hosts.stdout.splitlines():
        if not line or line.startswith(b"#"):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            key_blob = base64.b64decode(parts[2], validate=True)
        except ValueError:
            continue
        matched_lines.append(line)
        fingerprints.add("SHA256:" + base64.b64encode(hashlib.sha256(key_blob).digest()).decode("ascii").rstrip("="))
    if fingerprints != {target["host_key_fingerprint"]}:
        raise ValueError("known_hosts fingerprint does not exactly match operator target record")
    remote_probe = (
        "set -eu; "
        "printf 'F2_OWNER=%s\\n' \"$(id -un)\"; "
        "scontrol show config; "
        f"scontrol show partition {target['partition']} -o; "
        "sacctmgr -nP show assoc user=\"$(id -un)\" format=User,Account,QOS; "
        "scontrol show reservation -o"
    )
    probe = _bounded(["ssh", "-o", "BatchMode=yes", target["ssh_alias"], "bash", "-lc", remote_probe], timeout=60)
    if probe.returncode != 0:
        raise RuntimeError("bounded target identity probe failed")
    probe_text = probe.stdout.decode("utf-8", "strict")
    lines = [line.strip() for line in probe_text.splitlines() if line.strip()]
    owner_ok = f"F2_OWNER={target['owner']}" in lines
    cluster_ok = any(line.partition("=")[0].strip() == "ClusterName" and line.partition("=")[2].strip() == target["cluster_name"] for line in lines)
    controller_ok = target["controller"] in lines
    partition_ok = False
    reservation_ok = False
    for line in lines:
        fields = {key: value for token in shlex.split(line) if "=" in token for key, value in (token.split("=", 1),)}
        if fields.get("PartitionName") == target["partition"]:
            partition_ok = True
        if target["reservation"] in shlex.split(line):
            reservation_ok = True
    assoc_ok = any(
        len(parts := line.split("|")) >= 3
        and parts[0] == target["owner"]
        and parts[1] == target["account"]
        and target["qos"] in parts[2].split(",")
        for line in lines
    )
    if not all((owner_ok, cluster_ok, controller_ok, partition_ok, assoc_ok, reservation_ok)):
        raise ValueError("live target identity differs from frozen operator record")
    script = Path(__file__).resolve().parents[1] / "rl_phase3/run_phase3_target_command.py"
    if not script.is_file():
        raise FileNotFoundError(script)
    reservation_name = target["reservation"].removeprefix("ReservationName=")
    required_options = {"--ssh-alias": target["ssh_alias"], "--partition": target["partition"], "--gres": "gpu:1", "--nodes": "1", "--ntasks": "1", "--qos": target["qos"], "--reservation": reservation_name}
    if "--output-dir" not in phase3_args:
        raise ValueError("Phase3 submission arguments are incomplete")
    for option, expected_value in required_options.items():
        positions = [index for index, value in enumerate(phase3_args) if value == option]
        if len(positions) != 1 or positions[0] + 1 >= len(phase3_args) or phase3_args[positions[0] + 1] != expected_value:
            raise ValueError(f"Phase3 {option} differs from prechecked target")
    output_dir = Path(phase3_args[phase3_args.index("--output-dir") + 1]).absolute()
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw_probe = resolved.stdout + b"\n--KNOWN-HOSTS--\n" + b"\n".join(matched_lines) + b"\n--TARGET-PROBE--\n" + probe.stdout
    precheck = {
        "schema_version": "bb.rl.f2.target-precheck.v1",
        "target_record_ref": sha(target_raw),
        "ssh_config_ref": sha(resolved.stdout),
        "known_hosts_match_ref": sha(b"\n".join(matched_lines)),
        "probe_ref": sha(probe.stdout),
        "raw_ref": sha(raw_probe),
        "ssh_alias": target["ssh_alias"],
        "hostname": hostname,
        "f1_prerequisite_id": F1_PREREQUISITE_ID,
        "f1_prerequisite_ref": F1_PREREQUISITE_REF,
        "f1_prerequisite_root": F1_PREREQUISITE_ROOT,
        "passed": True,
    }
    raw_path, report_path = output_dir / "f2_target_precheck.raw", output_dir / "f2_target_precheck.json"
    for path, raw in ((raw_path, raw_probe), (report_path, canon(precheck))):
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, raw); os.fsync(descriptor)
        finally:
            os.close(descriptor)
    submitted = subprocess.run([sys.executable, str(script), *phase3_args], check=False)
    return submitted.returncode


def build_wrapper_image(source_bundle: Path, source_inventory: Path, output_tar: Path, report_path: Path) -> dict[str, object]:
    """Build one scratch image for independent review; never authorizes canonical F2."""
    source_bundle = source_bundle.resolve(strict=True)
    source_inventory = source_inventory.resolve(strict=True)
    output_tar, report_path = output_tar.absolute(), report_path.absolute()
    if output_tar.exists() or report_path.exists():
        raise FileExistsError(output_tar if output_tar.exists() else report_path)
    output_tar.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    report_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    bundle_raw, inventory_raw = source_bundle.read_bytes(), source_inventory.read_bytes()
    inventory = json.loads(inventory_raw)
    lock_members = [
        member for member in inventory.get("members", [])
        if isinstance(member, dict) and (str(member.get("path", "")).endswith(".lock") or "requirements" in PurePosixPath(str(member.get("path", ""))).name)
    ]
    dockerfile = (
        f"FROM {WRAPPER_BASE_IMAGE_REF}\n"
        "COPY f2-source-bundle.tar.gz /tmp/f2-source-bundle.tar.gz\n"
        "RUN mkdir -p /opt/f2 && tar -xzf /tmp/f2-source-bundle.tar.gz -C /opt/f2 && rm /tmp/f2-source-bundle.tar.gz && mkdir -p /opt/f2/third_party/nemo-gym/cache\n"
        "RUN python -m pip install --disable-pip-version-check --no-cache-dir --report /opt/f2-install-report.json /opt/f2/third_party/nemo-gym\n"
        "ENV PYTHONPATH=/opt/f2\n"
        "WORKDIR /opt/f2\n"
    ).encode()
    with tempfile.TemporaryDirectory(prefix="f2-wrapper-image-build-") as temporary:
        context = Path(temporary)
        (context / "Dockerfile").write_bytes(dockerfile)
        shutil.copyfile(source_bundle, context / "f2-source-bundle.tar.gz")
        tag = "bb-f2-scratch:" + hashlib.sha256(bundle_raw).hexdigest()[:24]
        built = _run(["docker", "build", "--network", "host", "--pull=false", "--file", str(context / "Dockerfile"), "--tag", tag, str(context)])
        if built["exit_code"] != 0:
            raise RuntimeError("scratch wrapper image build failed: " + str(built["stderr"]))
        inspected = _run(["docker", "image", "inspect", tag])
        if inspected["exit_code"] != 0:
            raise RuntimeError("scratch wrapper image inspect failed")
        image_rows = json.loads(str(inspected["stdout"]))
        if not isinstance(image_rows, list) or len(image_rows) != 1:
            raise RuntimeError("scratch wrapper image inspect malformed")
        image_id = str(image_rows[0].get("Id") or "")
        if not _SHA.fullmatch(image_id):
            raise RuntimeError("scratch wrapper image ID is not canonical")
        freeze = _run(["docker", "run", "--rm", "--network", "none", image_id, "python", "-m", "pip", "freeze", "--all"])
        install = _run(["docker", "run", "--rm", "--network", "none", image_id, "cat", "/opt/f2-install-report.json"])
        smoke = _run(["docker", "run", "--rm", "--network", "none", image_id, "python", "-c", "import nemo_gym,recipe.nemo_async.evals.run,responses_api_agents.breadboard_agent"])
        history = _run(["docker", "history", "--no-trunc", "--format", "{{json .}}", image_id])
        if any(result["exit_code"] != 0 for result in (freeze, install, smoke, history)):
            raise RuntimeError("scratch wrapper image inspection/import smoke failed")
        install_value = json.loads(str(install["stdout"]))
        resolved = []
        for item in install_value.get("install", []):
            metadata, download = item.get("metadata", {}), item.get("download_info", {})
            resolved.append({"name": metadata.get("name"), "version": metadata.get("version"), "archive_hashes": download.get("archive_info", {}).get("hashes", {})})
        saved = subprocess.run(["docker", "save", "--output", str(output_tar), image_id], capture_output=True, check=False)
        if saved.returncode != 0:
            output_tar.unlink(missing_ok=True)
            raise RuntimeError("scratch wrapper image export failed")
    tar_raw = output_tar.read_bytes()
    report = {
        "schema_version": "bb.rl.f2.scratch-wrapper-image-build.v1",
        "claim_boundary": "scratch_build_observation_only_not_canonical_f2_authority",
        "canonical_episode_allowed": False,
        "independent_review_required": True,
        "base_image_ref": WRAPPER_BASE_IMAGE_REF,
        "source_bundle_ref": sha(bundle_raw),
        "source_inventory_ref": sha(inventory_raw),
        "lock_members": lock_members,
        "dockerfile_ref": sha(dockerfile),
        "image_id": image_id,
        "image_tar_ref": sha(tar_raw),
        "image_tar_size": len(tar_raw),
        "resolved_packages": sorted(resolved, key=lambda item: (str(item["name"]), str(item["version"]))),
        "pip_freeze": sorted(line for line in str(freeze["stdout"]).splitlines() if line),
        "image_config": image_rows[0],
        "image_history_jsonl": str(history["stdout"]).splitlines(),
        "import_smoke": {"returncode": smoke["exit_code"], "stdout_ref": sha(str(smoke["stdout"]).encode()), "stderr_ref": sha(str(smoke["stderr"]).encode())},
    }
    report_raw = canon(report) + b"\n"
    descriptor = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, report_raw); os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return report

MANAGED_UV_REF = "sha256:1a8423f7d6af28f66920210b05a780665178c0f5650c940b95c4b085a4f284b9"



HOST_RUNTIME_REQUIREMENTS = (
    "fastapi==0.116.1",
    "jinja2==3.1.6",
    "pydantic==2.11.7",
    "python-multipart==0.0.20",
    "pyyaml==6.0.2",
    "uvicorn==0.35.0",
)


def build_host_runtime(*, python: Path, source_bundle: Path, source_inventory: Path, output: Path, report_path: Path) -> dict[str, object]:
    python, source_bundle, source_inventory = python.resolve(strict=True), source_bundle.resolve(strict=True), source_inventory.resolve(strict=True)
    output, report_path = output.absolute(), report_path.absolute()
    if output.exists() or report_path.exists():
        raise FileExistsError(output if output.exists() else report_path)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    report_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    version_probe = _bounded([str(python), "-c", "import json,sys;print(json.dumps({'implementation':sys.implementation.name,'version':list(sys.version_info[:3])},sort_keys=True,separators=(',',':')))"])
    if version_probe.returncode != 0:
        raise RuntimeError("host runtime Python identity probe failed")
    python_identity = json.loads(version_probe.stdout)
    if python_identity.get("implementation") != "cpython" or python_identity.get("version", [])[:2] != [3, 12]:
        raise RuntimeError("dedicated managed CPython 3.12 is required")
    requirements_raw = ("\n".join(HOST_RUNTIME_REQUIREMENTS) + "\n").encode()
    with tempfile.TemporaryDirectory(prefix="f2-host-runtime-source-") as temporary:
        source = Path(temporary) / "source"
        _safe_extract(source_bundle.read_bytes(), source, max_expanded_bytes=512 * 1024 * 1024, max_members=4096)
        created = _bounded([str(python), "-m", "venv", "--copies", str(output)], timeout=120)
        if created.returncode != 0:
            shutil.rmtree(output, ignore_errors=True)
            raise RuntimeError("host runtime venv creation failed")
        requirements = Path(temporary) / "requirements.txt"; requirements.write_bytes(requirements_raw)
        runtime_python = output / "bin/python"
        installed = _bounded([str(runtime_python), "-m", "pip", "install", "--disable-pip-version-check", "--no-cache-dir", "--report", str(Path(temporary) / "install.json"), "-r", str(requirements)], timeout=900)
        if installed.returncode != 0:
            shutil.rmtree(output, ignore_errors=True)
            raise RuntimeError("host runtime dependency install failed")
        freeze = _bounded([str(runtime_python), "-m", "pip", "freeze", "--all"])
        smoke_env = {"PATH": str(output / "bin"), "PYTHONPATH": str(source)}
        smoke = subprocess.run([str(runtime_python), "-c", "import pydantic,fastapi,uvicorn,jinja2,yaml; import breadboard.rl.harness.__main__; import agentic_coder_prototype.compilation.server_compiler"], env=smoke_env, capture_output=True, check=False, timeout=60)
        if freeze.returncode != 0 or smoke.returncode != 0:
            shutil.rmtree(output, ignore_errors=True)
            raise RuntimeError("host runtime import/CLI smoke failed")
        install_value = json.loads((Path(temporary) / "install.json").read_bytes())
        resolved = []
        for item in install_value.get("install", []):
            metadata, download = item.get("metadata", {}), item.get("download_info", {})
            resolved.append({"name": metadata.get("name"), "version": metadata.get("version"), "archive_hashes": download.get("archive_info", {}).get("hashes", {})})
    inventory = []
    for path in sorted(output.rglob("*")):
        if path.is_symlink():
            target = os.readlink(path)
            if Path(target).is_absolute() or ".." in Path(target).parts:
                shutil.rmtree(output, ignore_errors=True)
                raise RuntimeError("host runtime contains unsafe symlink")
            inventory.append({"path": path.relative_to(output).as_posix(), "type": "symlink", "target": target})
            continue
        if path.is_file():
            raw = path.read_bytes()
            executable = bool(path.stat().st_mode & 0o111)
            inventory.append({"path": path.relative_to(output).as_posix(), "type": "file", "size": len(raw), "sha256": sha(raw), "mode": 0o555 if executable else 0o444})
            path.chmod(0o555 if executable else 0o444)
    for directory in sorted((path for path in output.rglob("*") if path.is_dir()), reverse=True):
        directory.chmod(0o555)
    output.chmod(0o555)
    report = {
        "schema_version": "bb.rl.f2.scratch-host-runtime-build.v1",
        "claim_boundary": "scratch_host_runtime_observation_only_not_canonical_f2_authority",
        "canonical_episode_allowed": False,
        "independent_review_required": True,
        "source_bundle_ref": sha(source_bundle.read_bytes()),
        "source_inventory_ref": sha(source_inventory.read_bytes()),
        "requirements_ref": sha(requirements_raw),
        "direct_requirements": list(HOST_RUNTIME_REQUIREMENTS),
        "builder_python": {"path": str(python), "sha256": sha(python.read_bytes())},
        "builder_python_identity": python_identity,
        "runtime_python": {"relative_path": "bin/python", "sha256": next(item["sha256"] for item in inventory if item["path"] == "bin/python")},
        "resolved_packages": sorted(resolved, key=lambda item: (str(item["name"]), str(item["version"]))),
        "pip_freeze": sorted(freeze.stdout.decode("utf-8", "strict").splitlines()),
        "file_inventory": inventory,
        "sealed_read_only": True,
        "import_smoke": {"returncode": smoke.returncode, "stdout_ref": sha(smoke.stdout), "stderr_ref": sha(smoke.stderr)},
    }
    descriptor = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, canon(report) + b"\n"); os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return report


def build_managed_host_runtime(*, uv: Path, source_bundle: Path, source_inventory: Path, output: Path, report_path: Path) -> dict[str, object]:
    uv, source_bundle, source_inventory = uv.resolve(strict=True), source_bundle.resolve(strict=True), source_inventory.resolve(strict=True)
    output, report_path = output.absolute(), report_path.absolute()
    if output.exists() or report_path.exists():
        raise FileExistsError(output if output.exists() else report_path)
    if sha(uv.read_bytes()) != MANAGED_UV_REF:
        raise RuntimeError("managed uv installer authority mismatch")
    output.mkdir(mode=0o700, parents=True)
    try:
        managed = output / "managed-python"
        installed = _bounded([str(uv), "python", "install", "3.12", "--install-dir", str(managed), "--no-bin"], timeout=900)
        if installed.returncode != 0:
            raise RuntimeError("managed CPython 3.12 installation failed")
        candidates = []
        for path in managed.rglob("python3.12"):
            if path.is_file() and not path.is_symlink():
                identity = _bounded([str(path), "-c", "import json,sys;print(json.dumps({'implementation':sys.implementation.name,'version':list(sys.version_info[:3])},sort_keys=True,separators=(',',':')))"])
                if identity.returncode == 0 and json.loads(identity.stdout).get("version", [])[:2] == [3, 12]:
                    candidates.append((path, json.loads(identity.stdout)))
        if len(candidates) != 1:
            raise RuntimeError("managed CPython 3.12 executable identity is not exact")
        python, python_identity = candidates[0]
        with tempfile.TemporaryDirectory(prefix="f2-managed-runtime-report-") as temporary:
            inner_path = Path(temporary) / "runtime-report.json"
            runtime_report = build_host_runtime(python=python, source_bundle=source_bundle, source_inventory=source_inventory, output=output / "runtime", report_path=inner_path)
        install_inventory = []
        for path in sorted(managed.rglob("*")):
            relative = path.relative_to(output).as_posix()
            if path.is_symlink():
                target = os.readlink(path)
                if Path(target).is_absolute() or ".." in Path(target).parts:
                    raise RuntimeError("managed CPython installation contains unsafe symlink")
                install_inventory.append({"path": relative, "type": "symlink", "target": target})
            elif path.is_file():
                raw = path.read_bytes()
                install_inventory.append({"path": relative, "type": "file", "size": len(raw), "sha256": sha(raw), "mode": stat.S_IMODE(path.stat().st_mode)})
        report = {"schema_version": "bb.rl.f2.scratch-managed-host-runtime-build.v1", "claim_boundary": "scratch_managed_host_runtime_observation_only_not_canonical_f2_authority", "canonical_episode_allowed": False, "independent_review_required": True, "uv_authority": {"path": str(uv), "sha256": MANAGED_UV_REF}, "uv_install": {"argv": installed.args, "returncode": installed.returncode, "stdout_ref": sha(installed.stdout), "stderr_ref": sha(installed.stderr)}, "managed_python": {"relative_path": python.relative_to(output).as_posix(), "sha256": sha(python.read_bytes()), "identity": python_identity}, "install_inventory": install_inventory, "runtime_report": runtime_report}
        descriptor = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(descriptor, canon(report) + b"\n"); os.fsync(descriptor)
        finally:
            os.close(descriptor)
        for path in sorted(output.rglob("*"), reverse=True):
            if path.is_symlink():
                continue
            path.chmod(0o555 if path.is_dir() or path.stat().st_mode & 0o111 else 0o444)
        output.chmod(0o555)
        return report
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        report_path.unlink(missing_ok=True)
        raise




def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    remote = sub.add_parser("remote")
    remote.add_argument("--bundle-root", type=Path, required=True)
    remote.add_argument("--attempt-id", required=True)
    remote.add_argument("--credential-file", type=Path)
    remote.add_argument("--static-authority-fragment", type=Path)
    remote.add_argument("--dynamic-authority-input", type=Path)
    for name in ("f1-prerequisite-ref", "config-ref", "task-ref", "verifier-ref", "policy-ref"):
        remote.add_argument("--" + name, required=True)
    build = sub.add_parser("build-image")
    build.add_argument("--source-bundle", type=Path, required=True)
    build.add_argument("--source-inventory", type=Path, required=True)
    build.add_argument("--output-tar", type=Path, required=True)
    build.add_argument("--report", type=Path, required=True)
    build.add_argument("--emit-phase3-component", action="store_true")
    host = sub.add_parser("build-host-runtime")
    host.add_argument("--python", type=Path, required=True)
    host.add_argument("--source-bundle", type=Path, required=True)
    host.add_argument("--source-inventory", type=Path, required=True)
    host.add_argument("--output", type=Path, required=True)
    host.add_argument("--report", type=Path, required=True)
    host.add_argument("--emit-phase3-component", action="store_true")
    managed = sub.add_parser("build-managed-host-runtime")
    managed.add_argument("--uv", type=Path, required=True)
    managed.add_argument("--source-bundle", type=Path, required=True)
    managed.add_argument("--source-inventory", type=Path, required=True)
    managed.add_argument("--output", type=Path, required=True)
    managed.add_argument("--report", type=Path, required=True)
    managed.add_argument("--emit-phase3-component", action="store_true")
    submit = sub.add_parser("submit")
    submit.add_argument("--target-record", type=Path, required=True)
    submit.add_argument("--known-hosts", type=Path, required=True)
    submit.add_argument("phase3_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.mode == "build-managed-host-runtime":
        report = build_managed_host_runtime(uv=args.uv, source_bundle=args.source_bundle, source_inventory=args.source_inventory, output=args.output, report_path=args.report)
        print(canon(report).decode())
        if args.emit_phase3_component:
            requested, job_id = os.environ.get("PHASE3_TARGET_RUN_ID", ""), os.environ.get("SLURM_JOB_ID", "")
            if not requested.endswith("-slurm-pending") or not job_id.isdigit():
                raise ValueError("scratch managed runtime component requires concrete Slurm identity")
            target_run_id = requested.removesuffix("pending") + job_id
            component = {"schema_version": "bb.rl.f2.scratch-managed-host-runtime-component.v1", "report_id": "f2-scratch-managed-host-runtime-" + job_id, "claim_boundary": "scratch_managed_host_runtime_observation_only_not_canonical_f2", "target_run_id": target_run_id, "passed": True, "scorecard_update_allowed": False, "promotion_allowed": False, "canonical_episode_allowed": False, "build_report_ref": "sha256:" + hashlib.sha256(args.report.read_bytes()).hexdigest(), "runtime_python_ref": report["runtime_report"]["runtime_python"]["sha256"], "uv_ref": report["uv_authority"]["sha256"], "shared_report_path": str(args.report), "shared_runtime_path": str(args.output)}
            print(COMPONENT_PREFIX.decode() + canon(component).decode())
        return 0
    if args.mode == "build-host-runtime":
        report = build_host_runtime(python=args.python, source_bundle=args.source_bundle, source_inventory=args.source_inventory, output=args.output, report_path=args.report)
        print(canon(report).decode())
        if args.emit_phase3_component:
            requested, job_id = os.environ.get("PHASE3_TARGET_RUN_ID", ""), os.environ.get("SLURM_JOB_ID", "")
            if not requested.endswith("-slurm-pending") or not job_id.isdigit():
                raise ValueError("scratch host runtime component requires concrete Slurm identity")
            component = {"schema_version": "bb.rl.f2.scratch-host-runtime-component.v1", "report_id": "f2-scratch-host-runtime-" + str(report["source_bundle_ref"])[7:23], "claim_boundary": "scratch_host_runtime_observation_only_not_canonical_f2", "target_run_id": requested.removesuffix("pending") + job_id, "passed": True, "scorecard_update_allowed": False, "promotion_allowed": False, "canonical_episode_allowed": False, "build_report_ref": "sha256:" + hashlib.sha256(args.report.read_bytes()).hexdigest(), "runtime_python_ref": report["runtime_python"]["sha256"], "shared_report_path": str(args.report), "shared_runtime_path": str(args.output)}
            print(COMPONENT_PREFIX.decode() + canon(component).decode())
        return 0
    if args.mode == "build-image":
        report = build_wrapper_image(args.source_bundle, args.source_inventory, args.output_tar, args.report)
        print(canon(report).decode())
        if args.emit_phase3_component:
            requested = os.environ.get("PHASE3_TARGET_RUN_ID", "")
            job_id = os.environ.get("SLURM_JOB_ID", "")
            if not requested.endswith("-slurm-pending") or not job_id.isdigit():
                raise ValueError("scratch image component requires concrete Slurm identity")
            target_run_id = requested.removesuffix("pending") + job_id
            component = {
                "schema_version": "bb.rl.f2.scratch-wrapper-image-component.v1",
                "report_id": "f2-scratch-wrapper-image-" + str(report["source_bundle_ref"])[7:23],
                "claim_boundary": "scratch_wrapper_image_observation_only_not_canonical_f2",
                "target_run_id": target_run_id,
                "passed": True,
                "scorecard_update_allowed": False,
                "promotion_allowed": False,
                "canonical_episode_allowed": False,
                "build_report_ref": "sha256:" + hashlib.sha256(args.report.read_bytes()).hexdigest(),
                "image_tar_ref": report["image_tar_ref"],
                "image_id": report["image_id"],
                "shared_report_path": str(args.report),
                "shared_tar_path": str(args.output_tar),
            }
            print(COMPONENT_PREFIX.decode() + canon(component).decode())
        return 0
    if args.mode == "submit":
        phase3_args = args.phase3_args[1:] if args.phase3_args[:1] == ["--"] else args.phase3_args
        return precheck_and_submit(target_record_path=args.target_record, known_hosts=args.known_hosts, phase3_args=phase3_args)
    return remote_run(
        args.bundle_root,
        args.attempt_id,
        args.credential_file,
        static_authority_fragment=args.static_authority_fragment,
        dynamic_authority_input=args.dynamic_authority_input,
        f1_prerequisite_ref=args.f1_prerequisite_ref,
        config_ref=args.config_ref,
        task_ref=args.task_ref,
        verifier_ref=args.verifier_ref,
        policy_ref=args.policy_ref,
    )


if __name__ == "__main__":
    raise SystemExit(main())
