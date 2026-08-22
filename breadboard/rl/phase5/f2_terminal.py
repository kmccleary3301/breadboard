from __future__ import annotations

import base64
import hashlib
import json
import io
import os
import re
import shutil
import subprocess
import stat
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

MARKER_PREFIX = "F2_ARTIFACT="
RESULT_PREFIX = "F2_RESULT_ARCHIVE="
MARKER_SCHEMA = "bb.rl.f2.artifact-marker.v1"
REPORT_SCHEMA = "bb.rl.f2.ibm-terminal-episode-report.v1"
CLAIM = "one observed IBM terminal episode through canonical generate/eval"
NON_CLAIMS = (
    "no training-quality, campaign, point-award, promotion, or bead-closure claim",
    "F1 is a prerequisite only and is not terminal-episode evidence",
    "exact one-node campaign only",
)
TARGET_ALIAS = "ZYPHRA_IBM_AMD_1"
PARTITION = "gpu"
F1_PREREQUISITE_ID = "20260711T203833Z-slurm-263537"
F1_PREREQUISITE_ROOT = f"docs_tmp/ZYPHRA/RL_PHASE_5/evidence/target/F1/{F1_PREREQUISITE_ID}"
F1_PREREQUISITE_REF = "sha256:eaa3a09e8c396946fe82036f3bbf0d778503a647e190627b2ad7f944a2f16f59"


TARGET_ARTIFACTS: Mapping[str, str] = {
    "source": "source.json",
    "prerequisite": "prerequisite.json",
    "terminal_package": "terminal_package.json",
    "eval_row": "eval_row.json",
    "eval_summary": "eval_summary.json",
    "selection": "selection.json",
    "effective_plan": "effective_plan.json",
    "policy": "policy.json",
    "tool": "tool.json",
    "sandbox": "sandbox.json",
    "verifier": "verifier.json",
    "reward": "reward.json",
    "artifact_graph": "artifact_graph.json",
    "completed": "completed.json",
    "closed": "closed.json",
    "cleanup": "cleanup.json",
}
RUNNER_ARTIFACTS: Mapping[str, str] = {
    "invocation": "invocation.json",
    "stdout": "stdout.bin",
    "stderr": "stderr.bin",
    "exit": "exit.json",
    "callback_observation_journal": "callback-observation-journal.jsonl",
    "callback_observation_snapshot": "callback-observation-snapshot.json",
    "callback_verification_authority": "callback-verification-authority.json",
    "callback_verification_public_key": "callback-verification-public-key.pem",
    "callback_verification_receipt": "callback-verification-receipt.json",
    "callback_verification_signature": "callback-verification-signature.json",
}
OUTER_ARTIFACTS: Mapping[str, str] = {
    "phase3_invocation": "phase3_invocation.json",
    "transport": "transport.json",
    "result_archive": "result_archive.json",
    "phase3_manifest": "phase3-command-log-manifest.json",
    "phase3_log": "phase3-command.log",
}
ROOT_ARTIFACTS = ("attempt.json", "target.stdout", "target.stderr", "exit_code", "result.tar.gz", "runner-result.tar.gz")

_SHA_REF = re.compile(r"^sha256:[0-9a-f]{64}$")
_IMAGE_REF = re.compile(r"^[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}$")
_ATTEMPT = re.compile(r"^f2-[a-z0-9]+(?:-[a-z0-9]+)*$")
_SECRET_KEY = re.compile(r"(?:authorization|api[_-]?key|password|secret|token|bearer|credential|environment|argv)", re.I)
_OPENSSL_VERIFY = (
    "/opt/homebrew/opt/openssl@3/bin/openssl"
    if sys.platform == "darwin"
    else "/usr/bin/openssl"
)
_TARGET_RUN = re.compile(r"^\d{8}T\d{6}Z-slurm-[1-9][0-9]*$")
_SECRET_TEXT = re.compile(rb"(?i)(?:authorization\s*[:=]|bearer\s+[A-Za-z0-9._~+/=-]{8,}|(?:api[_-]?key|password|secret|token)\s*[:=]\s*[^\s,}\]]{4,})")
_FIXTURE = re.compile(r"(?:production-fixture-|trusted[-_]process|(?:test|fixture)[-_](?:policy|verifier)|(?:policy|verifier)[-_](?:test|fixture))", re.I)
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_EXPANDED_BYTES = 256 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 256
_MAX_ARCHIVE_DEPTH = 8
_MAX_COMPRESSION_RATIO = 100
_F1_SCHEMA = re.compile(r"^bb\.rl\.f1\.")


class F2ValidationError(ValueError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise F2ValidationError("value is not canonical JSON") from exc


def sha256_ref(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _json(raw: bytes, where: str) -> Any:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise F2ValidationError(f"{where}: invalid JSON") from exc
    if canonical_json_bytes(value) != raw:
        raise F2ValidationError(f"{where}: non-canonical JSON")
    return value


def _object(value: object, required: set[str], where: str, *, exact: bool = False) -> dict[str, Any]:
    if type(value) is not dict:
        raise F2ValidationError(f"{where}: object required")
    keys = set(value)
    if not required <= keys or (exact and keys != required):
        raise F2ValidationError(f"{where}: keys mismatch")
    return value


def _string(value: object, where: str) -> str:
    if type(value) is not str or not value:
        raise F2ValidationError(f"{where}: non-empty string required")
    return value
def build_f2_artifact_graph(
    *,
    raw_objects: Mapping[str, bytes],
    parents: Mapping[str, tuple[str, ...]],
    roots: tuple[str, ...],
    producers: Mapping[str, str],
) -> bytes:
    """Build the F2 graph only from supplied raw bytes and exact lineage."""
    if not raw_objects or set(raw_objects) != set(parents) or set(raw_objects) != set(producers):
        raise F2ValidationError("raw CAS object/lineage/producer inventories differ")
    objects = []
    for digest, raw in sorted(raw_objects.items()):
        if _ref(digest, "raw CAS ref") != sha256_ref(raw):
            raise F2ValidationError("raw CAS ref does not bind bytes")
        objects.append({
            "ref": digest,
            "size": len(raw),
            "media_type": "application/octet-stream",
            "bytes_b64": base64.b64encode(raw).decode("ascii"),
            "parents": list(parents[digest]),
            "producer": _string(producers[digest], "CAS producer"),
        })
    graph = {"schema_version": "bb.rl.f2.artifact-graph.v1", "roots": list(roots), "objects": objects}
    _validate_artifact_graph(graph)
    return canonical_json_bytes(graph)


def export_f2_artifacts_from_raw(
    *,
    records: Mapping[str, bytes],
    raw_objects: Mapping[str, bytes],
    parents: Mapping[str, tuple[str, ...]],
    roots: tuple[str, ...],
    producers: Mapping[str, str],
) -> Mapping[str, bytes]:
    """Emit the exact 16 projections without inventing authority.

    The fifteen input records must be canonical production records collected
    from eval, authenticated HTTP, native CAS, callback journals, daemon
    attestations, and cleanup probes. The exporter only verifies and packages
    those bytes; all digest refs except the independently canonical F1 prerequisite must resolve to supplied raw objects.
    """
    expected = set(TARGET_ARTIFACTS) - {"artifact_graph"}
    if set(records) != expected:
        raise F2ValidationError("raw production record inventory is not exact")
    parsed = {name: _json(raw, f"raw export {name}") for name, raw in records.items()}
    for name, value in parsed.items():
        _reject_structural_secrets_and_fixtures(value, f"raw export {name}", allow_f1_prerequisite=name in {"prerequisite", "terminal_package"})
    exported_refs = set(raw_objects)
    for name, value in parsed.items():
        for location, child in _walk(value, f"raw export {name}"):
            if type(child) is str and _SHA_REF.fullmatch(child) and child != F1_PREREQUISITE_REF and not location.endswith(".socket_plan_id") and child not in exported_refs:
                raise F2ValidationError(f"{location}: ref lacks raw exported bytes")
    graph = build_f2_artifact_graph(raw_objects=raw_objects, parents=parents, roots=roots, producers=producers)
    return {**{TARGET_ARTIFACTS[name]: records[name] for name in expected}, TARGET_ARTIFACTS["artifact_graph"]: graph}




def _ref(value: object, where: str) -> str:
    result = _string(value, where)
    if not _SHA_REF.fullmatch(result):
        raise F2ValidationError(f"{where}: canonical lowercase sha256 ref required")
    return result


def _walk(value: object, where: str = "$") -> Iterable[tuple[str, object]]:
    yield where, value
    if type(value) is dict:
        for key, child in value.items():
            yield from _walk(child, f"{where}.{key}")
    elif type(value) is list:
        for index, child in enumerate(value):
            yield from _walk(child, f"{where}[{index}]")


def _reject_structural_secrets_and_fixtures(value: object, where: str, *, allow_f1_prerequisite: bool = False) -> None:
    for location, child in _walk(value, where):
        if type(child) is dict:
            for key, field_value in child.items():
                if (
                    _SECRET_KEY.search(str(key))
                    and field_value not in (None, "", [], {})
                    and not (
                        (
                            key == "private_key_secret_handle_id"
                            and type(field_value) is str
                            and re.fullmatch(r"[a-z0-9][a-z0-9-]{2,127}", field_value)
                        )
                        or (
                            key in {
                                "bearer_authentication_required",
                                "bearer_authenticated",
                            }
                            and type(field_value) is bool
                        )
                    )
                ):
                    raise F2ValidationError(f"{location}: secret-bearing field {key!r}")
        if type(child) is str:
            if _FIXTURE.search(child):
                raise F2ValidationError(f"{location}: fixture or trusted-process identity forbidden")
            if _F1_SCHEMA.match(child) and not allow_f1_prerequisite:
                raise F2ValidationError(f"{location}: F1 schema/artifact forbidden in F2 evidence")
            public_authority_ref_path = location.endswith((
                ".tls_callback_runtime_input.ca_certificate_ref.path",
                ".tls_callback_runtime_input.leaf_certificate_ref.path",
                ".evidence_receipt_signing_authority.public_key_ref.path",
                ".policy_tls_trust_authority.ca_bundle_ref.path",
            ))
            if child != "/v1/responses" and not public_authority_ref_path and (child.startswith(("/", "~/")) or re.match(r"^[A-Za-z]:[\\/]", child)):
                raise F2ValidationError(f"{location}: absolute path forbidden")


def parse_artifact_markers(stdout: bytes, attempt_id: str) -> list[dict[str, Any]]:
    if not _ATTEMPT.fullmatch(attempt_id):
        raise F2ValidationError("invalid F2 attempt id")
    markers: list[dict[str, Any]] = []
    prefix = MARKER_PREFIX.encode()
    for line in stdout.splitlines():
        if not line.startswith(prefix):
            continue
        marker = _json(line[len(prefix):], "artifact marker")
        marker = _object(marker, {"schema_version", "attempt_id", "name", "path", "sha256", "size"}, "artifact marker", exact=True)
        if marker["schema_version"] != MARKER_SCHEMA or marker["attempt_id"] != attempt_id:
            raise F2ValidationError("artifact marker authority mismatch")
        name = _string(marker["name"], "artifact marker name")
        path = _string(marker["path"], "artifact marker path")
        if name not in TARGET_ARTIFACTS or path != "artifacts/" + TARGET_ARTIFACTS[name]:
            raise F2ValidationError("artifact marker inventory mismatch")
        _ref(marker["sha256"], "artifact marker sha256")
        if type(marker["size"]) is not int or marker["size"] < 0:
            raise F2ValidationError("artifact marker size invalid")
        markers.append(marker)
    if len(markers) != len(TARGET_ARTIFACTS) or {m["name"] for m in markers} != set(TARGET_ARTIFACTS):
        raise F2ValidationError("artifact marker set is not exact")
    return markers


def safe_extract_archive(archive: Path, destination: Path) -> None:
    files = _archive_files(Path(archive).read_bytes(), "result archive")
    destination = Path(destination)
    destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    try:
        for name, raw in files.items():
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as output:
                output.write(raw)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _archive_files(raw: bytes, where: str) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    if not raw or len(raw) > _MAX_ARCHIVE_BYTES:
        raise F2ValidationError(f"{where}: encoded archive budget exceeded")
    expanded = 0
    try:
        with tarfile.open(fileobj=__import__("io").BytesIO(raw), mode="r:*") as archive:
            members = archive.getmembers()
            if not members or len(members) > _MAX_ARCHIVE_MEMBERS:
                raise F2ValidationError(f"{where}: archive member budget exceeded")
            for member in members:
                pure = PurePosixPath(member.name)
                expanded += member.size
                sparse = getattr(member, "sparse", None)
                if len(member.name.encode()) > 512 or len(pure.parts) > _MAX_ARCHIVE_DEPTH or member.size > _MAX_EXPANDED_BYTES or expanded > _MAX_EXPANDED_BYTES or sparse:
                    raise F2ValidationError(f"{where}: expanded archive budget exceeded")
                if expanded > max(len(raw), 1) * _MAX_COMPRESSION_RATIO:
                    raise F2ValidationError(f"{where}: compression ratio exceeded")
                if not member.isreg() or pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts) or member.name != pure.as_posix() or member.name in files:
                    raise F2ValidationError(f"{where}: unsafe archive inventory")
                source = archive.extractfile(member)
                if source is None:
                    raise F2ValidationError(f"{where}: unreadable member")
                files[member.name] = source.read()
    except (tarfile.TarError, OSError) as exc:
        raise F2ValidationError(f"{where}: invalid archive") from exc
    return files


def _read_tree_file(root: Path, relative: str) -> bytes:
    path = root / relative
    try:
        status = path.lstat()
    except OSError as exc:
        raise F2ValidationError(f"missing evidence file {relative}") from exc
    if not stat.S_ISREG(status.st_mode) or path.is_symlink():
        raise F2ValidationError(f"evidence file is not a regular file: {relative}")
    return path.read_bytes()


def _bounded_gzip_bytes(raw: bytes) -> bytes:
    import gzip
    budget = min(_MAX_EXPANDED_BYTES, len(raw) * _MAX_COMPRESSION_RATIO)
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw), mode="rb") as stream:
            expanded = stream.read(budget + 1)
            surplus = stream.read(1) if len(expanded) <= budget else b"x"
    except (OSError, EOFError) as exc:
        raise F2ValidationError("nested gzip evidence is malformed") from exc
    if len(expanded) > budget or surplus:
        raise F2ValidationError("nested compressed evidence budget exceeded")
    return expanded


def _scan_secret_bytes(raw: bytes, seeds: tuple[bytes, ...], *, depth: int = 0) -> None:
    if _SECRET_TEXT.search(raw):
        raise F2ValidationError("secret-like material found in evidence")
    for seed in seeds:
        standard_base64 = base64.b64encode(seed)
        urlsafe_base64 = base64.urlsafe_b64encode(seed)
        base32 = base64.b32encode(seed)
        forms = {
            seed,
            standard_base64,
            standard_base64.rstrip(b"="),
            urlsafe_base64,
            urlsafe_base64.rstrip(b"="),
            seed.hex().encode(),
            seed.hex().upper().encode(),
            base32,
            base32.rstrip(b"="),
            base32.lower(),
            base32.lower().rstrip(b"="),
        }
        if any(form and form in raw for form in forms):
            raise F2ValidationError("seeded secret representation found in evidence")
    if depth >= 3:
        return
    is_tar = (len(raw) > 262 and raw[257:262] == b"ustar") or raw.startswith(b"\x1f\x8b")
    if is_tar:
        try:
            members = _archive_files(raw, "nested evidence archive")
        except F2ValidationError as archive_error:
            if not raw.startswith(b"\x1f\x8b"):
                return
            expanded = _bounded_gzip_bytes(raw)
            if len(expanded) > 262 and expanded[257:262] == b"ustar":
                raise archive_error
            _scan_secret_bytes(expanded, seeds, depth=depth + 1)
            return
        for member in members.values():
            _scan_secret_bytes(member, seeds, depth=depth + 1)


def _scan_tree(root: Path, secret_material: Iterable[bytes]) -> None:
    seeds = tuple(seed for seed in secret_material if seed)
    for path in root.rglob("*"):
        status = path.lstat()
        if stat.S_ISLNK(status.st_mode):
            raise F2ValidationError("symlink forbidden in evidence")
        if stat.S_ISREG(status.st_mode):
            raw = path.read_bytes()
            _scan_secret_bytes(raw, seeds)


def _verify_callback_observation_packet(
    *,
    attempt_id: str,
    composition_digest: str,
    route_id: str,
    journal: bytes,
    snapshot_raw: bytes,
    authority_raw: bytes,
    public_key: bytes,
    receipt_raw: bytes,
    signature_raw: bytes,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from breadboard.rl.harness.composition import (
        CallbackJournalVerificationReceiptV1,
        EvidenceReceiptSignatureV1,
        EvidenceReceiptSigningAuthorityV1,
    )

    try:
        authority = EvidenceReceiptSigningAuthorityV1.model_validate_json(
            authority_raw, strict=True
        )
        receipt = CallbackJournalVerificationReceiptV1.model_validate_json(
            receipt_raw, strict=True
        )
        signature = EvidenceReceiptSignatureV1.model_validate_json(
            signature_raw, strict=True
        )
    except Exception as exc:
        raise F2ValidationError("callback verification envelope is invalid") from exc
    for raw, model in (
        (authority_raw, authority),
        (receipt_raw, receipt),
        (signature_raw, signature),
    ):
        if canonical_json_bytes(model.model_dump(mode="json")) != raw:
            raise F2ValidationError("callback verification envelope is not canonical")
    if not journal.endswith(b"\n"):
        raise F2ValidationError("callback journal is not complete")
    entries: list[dict[str, Any]] = []
    previous = "hmac-sha256:" + "0" * 64
    idempotency: set[str] = set()
    for sequence, line in enumerate(journal.splitlines(), start=1):
        entry = _json(line, f"callback journal entry {sequence}")
        entry = _object(
            entry,
            {
                "schema_version",
                "sequence",
                "idempotency_key",
                "previous_entry_mac",
                "record_digest",
                "record",
                "committed",
                "entry_mac",
            },
            f"callback journal entry {sequence}",
            exact=True,
        )
        if (
            entry["schema_version"] != "bb.wrapper.observation-journal-entry.v1"
            or entry["sequence"] != sequence
            or entry["committed"] is not True
            or entry["previous_entry_mac"] != previous
            or entry["record_digest"]
            != sha256_ref(canonical_json_bytes(entry["record"]))
            or re.fullmatch(r"hmac-sha256:[0-9a-f]{64}", entry["entry_mac"]) is None
            or _SHA_REF.fullmatch(entry["idempotency_key"]) is None
            or entry["idempotency_key"] in idempotency
        ):
            raise F2ValidationError("callback journal chain/idempotency is invalid")
        idempotency.add(entry["idempotency_key"])
        previous = entry["entry_mac"]
        entries.append(entry)
    snapshot = _object(
        _json(snapshot_raw, "callback observation snapshot"),
        {"schema_version", "entry_count", "head_entry_mac", "records"},
        "callback observation snapshot",
        exact=True,
    )
    records = [entry["record"] for entry in entries]
    if (
        snapshot["schema_version"] != "bb.wrapper.callback-observation-snapshot.v1"
        or snapshot["entry_count"] != len(entries)
        or snapshot["head_entry_mac"] != previous
        or snapshot["records"] != records
        or len(entries) != 3
    ):
        raise F2ValidationError("callback journal/snapshot sequence mismatch")
    authority_digest = authority.canonical_digest()
    if (
        authority.attempt_id != attempt_id
        or authority.composition_digest != composition_digest
        or authority.algorithm != "Ed25519"
        or authority.public_key_sha256 != sha256_ref(public_key)
        or authority.public_key_ref.size_bytes != len(public_key)
        or receipt.attempt_id != attempt_id
        or receipt.composition_digest != composition_digest
        or receipt.route_id != route_id
        or receipt.journal_ref.sha256 != sha256_ref(journal)
        or receipt.journal_ref.size_bytes != len(journal)
        or receipt.snapshot_ref.sha256 != sha256_ref(snapshot_raw)
        or receipt.snapshot_ref.size_bytes != len(snapshot_raw)
        or receipt.head_mac != previous.removeprefix("hmac-sha256:")
        or receipt.event_count != len(entries)
        or receipt.evidence_policy_digest != authority.evidence_policy_digest
        or receipt.signer_public_key_spki_sha256
        != authority.public_key_spki_sha256
        or receipt.signer_authority_digest != authority_digest
        or signature.signer_authority_digest != authority_digest
        or signature.receipt_digest != receipt.canonical_digest()
    ):
        raise F2ValidationError("callback verification receipt join is invalid")
    with tempfile.TemporaryDirectory(prefix="f2-callback-verify-") as temporary:
        root = Path(temporary)
        public_path = root / "public.pem"
        receipt_path = root / "receipt.json"
        signature_path = root / "signature.bin"
        spki_path = root / "public.der"
        public_path.write_bytes(public_key)
        receipt_path.write_bytes(receipt_raw)
        signature_path.write_bytes(
            base64.b64decode(signature.signature_base64, validate=True)
        )
        convert = subprocess.run(
            [
                _OPENSSL_VERIFY,
                "pkey",
                "-pubin",
                "-in",
                str(public_path),
                "-outform",
                "DER",
                "-out",
                str(spki_path),
            ],
            capture_output=True,
            check=False,
            timeout=30,
        )
        verify = subprocess.run(
            [
                _OPENSSL_VERIFY,
                "pkeyutl",
                "-verify",
                "-pubin",
                "-rawin",
                "-inkey",
                str(public_path),
                "-in",
                str(receipt_path),
                "-sigfile",
                str(signature_path),
            ],
            capture_output=True,
            check=False,
            timeout=30,
        )
        if (
            convert.returncode != 0
            or verify.returncode != 0
            or sha256_ref(spki_path.read_bytes()) != authority.public_key_spki_sha256
        ):
            raise F2ValidationError(
                "callback verification Ed25519 signature/public authority is invalid"
            )
    return authority.model_dump(mode="json"), records


def _load_inventory(attempt: Path, inventory: Mapping[str, str], directory: str) -> dict[str, bytes]:
    root = attempt / directory
    if not root.is_dir() or root.is_symlink():
        raise F2ValidationError(f"missing {directory} directory")
    actual = {p.name for p in root.iterdir()}
    if actual != set(inventory.values()):
        raise F2ValidationError(f"{directory} inventory is not exact")
    return {name: _read_tree_file(root, filename) for name, filename in inventory.items()}


def _validate_artifact_graph(value: object) -> tuple[dict[str, bytes], tuple[str, ...]]:
    graph = _object(value, {"schema_version", "roots", "objects"}, "artifact graph", exact=True)
    if graph["schema_version"] != "bb.rl.f2.artifact-graph.v1" or type(graph["roots"]) is not list or type(graph["objects"]) is not list:
        raise F2ValidationError("artifact graph schema invalid")
    objects: dict[str, bytes] = {}
    parents: dict[str, tuple[str, ...]] = {}
    for index, item in enumerate(graph["objects"]):
        obj = _object(item, {"ref", "size", "media_type", "bytes_b64", "parents", "producer"}, f"artifact object {index}", exact=True)
        digest = _ref(obj["ref"], "artifact ref")
        if digest in objects or type(obj["bytes_b64"]) is not str:
            raise F2ValidationError("duplicate artifact or invalid bytes")
        try:
            raw = base64.b64decode(obj["bytes_b64"], validate=True)
        except ValueError as exc:
            raise F2ValidationError("artifact bytes are not canonical base64") from exc
        if sha256_ref(raw) != digest or type(obj["size"]) is not int or obj["size"] != len(raw):
            raise F2ValidationError("artifact ref/size does not bind exported bytes")
        _string(obj["media_type"], "artifact media type")
        _string(obj["producer"], "artifact producer")
        if type(obj["parents"]) is not list or len(obj["parents"]) != len(set(obj["parents"])):
            raise F2ValidationError("artifact parents invalid")
        parents[digest] = tuple(_ref(parent, "artifact parent") for parent in obj["parents"])
        objects[digest] = raw
    roots = tuple(_ref(root, "artifact root") for root in graph["roots"])
    if not roots or len(roots) != len(set(roots)) or any(root not in objects for root in roots):
        raise F2ValidationError("artifact roots invalid")
    if any(parent not in objects for values in parents.values() for parent in values):
        raise F2ValidationError("artifact graph parent missing")
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(node: str) -> None:
        if node in visiting:
            raise F2ValidationError("artifact graph cycle")
        if node in visited:
            return
        visiting.add(node)
        for parent in parents[node]: visit(parent)
        visiting.remove(node); visited.add(node)
    for root in roots: visit(root)
    if visited != set(objects):
        raise F2ValidationError("orphan artifact object")
    return objects, roots


def _schema(value: object, expected: str, where: str, required: set[str], *, allow_f1_prerequisite: bool = False, exact: bool = False) -> dict[str, Any]:
    obj = _object(value, required | {"schema_version"}, where, exact=exact)
    if obj["schema_version"] != expected:
        raise F2ValidationError(f"{where}: schema mismatch")
    _reject_structural_secrets_and_fixtures(obj, where, allow_f1_prerequisite=allow_f1_prerequisite)
    return obj


def _same(field: str, expected: object, values: Iterable[tuple[str, Mapping[str, Any]]]) -> None:
    for where, value in values:
        if value.get(field) != expected:
            raise F2ValidationError(f"{where}: {field} join mismatch")


def _validate_fixed_policy(policy: Mapping[str, Any], tool: Mapping[str, Any], episode_id: str, effective_plan_ref: str) -> None:
    authority = _object(policy["authority"], {"code_digest", "script_digest", "model_label_digest", "instance_digest", "model_label", "shell_command", "completion"}, "fixed policy authority", exact=True)
    for field in ("code_digest", "script_digest", "model_label_digest", "instance_digest"):
        _ref(authority[field], f"fixed policy {field}")
    turns, observations = policy["turns"], policy["callback_observations"]
    routes = policy["tls_route_observations"]
    if type(routes) is not list or len(routes) != 2:
        raise F2ValidationError("exactly two TLS callback route observations required")
    if type(turns) is not list or len(turns) != 2 or type(observations) is not list or len(observations) != 2:
        raise F2ValidationError("fixed policy requires exactly two callback turns")
    first_request = first_tool = first_binding = first_slot = None
    for number, (turn, observation) in enumerate(zip(turns, observations, strict=True), 1):
        outer = _object(turn, {"schema_version", "episode_id", "effective_plan_digest", "binding_digest", "policy_slot_id", "request_digest", "request_payload", "turn", "attempt", "response_digest", "response_payload"}, f"policy turn {number}", exact=True)
        if outer["schema_version"] != "bb.rl.policy-http-request.v1" or outer["episode_id"] != episode_id or outer["turn"] != number or outer["attempt"] != 1 or outer["effective_plan_digest"] != effective_plan_ref:
            raise F2ValidationError("fixed policy request carrier/effective-plan mismatch")
        for field in ("effective_plan_digest", "binding_digest", "request_digest", "response_digest"):
            _ref(outer[field], f"policy turn {number} {field}")
        if number == 1:
            first_binding, first_slot = outer["binding_digest"], outer["policy_slot_id"]
        elif outer["binding_digest"] != first_binding or outer["policy_slot_id"] != first_slot:
            raise F2ValidationError("fixed policy binding/slot changed between turns")
        if outer["request_digest"] != sha256_ref(canonical_json_bytes(outer["request_payload"])):
            raise F2ValidationError("fixed policy request digest mismatch")
        response = _object(outer["response_payload"], {"id", "object", "model", "output", "metadata"}, f"policy response {number}", exact=True)
        if outer["response_digest"] != sha256_ref(canonical_json_bytes(response)) or response["object"] != "response" or response["model"] != authority["model_label"]:
            raise F2ValidationError("fixed policy response carrier mismatch")
        if type(response["output"]) is not list or len(response["output"]) != 1:
            raise F2ValidationError("fixed policy output is not exact")
        call = _object(response["output"][0], {"type", "call_id", "name", "arguments"}, "fixed policy call", exact=True)
        metadata = _object(_object(response["metadata"], {"breadboard_fixed_policy"}, "fixed policy metadata", exact=True)["breadboard_fixed_policy"], {"schema_version", "intelligence_claim", "turn", "code_digest", "script_digest", "model_label_digest", "instance_digest", "request_digest", "tool_call_digest", "observation_digest", "prior_request_digest", "prior_tool_call_digest", "response_content_digest"}, "fixed policy binding", exact=True)
        route = _object(routes[number - 1], {"connected_peer_ip", "tls_version", "cipher", "leaf_der_digest", "ca_authority_digest", "server_name", "server_certificate_verified", "hostname_verified", "bearer_authenticated", "mutual_tls", "network_grant_ref", "route_digest", "request_digest", "response_digest"}, "TLS callback route observation", exact=True)
        import ipaddress
        try:
            peer = ipaddress.ip_address(route["connected_peer_ip"])
        except ValueError as exc:
            raise F2ValidationError("TLS callback peer IP invalid") from exc
        if (
            peer.is_loopback
            or not peer.is_private
            or route["tls_version"] != "TLSv1.3"
            or route["cipher"] != "TLS_AES_256_GCM_SHA384"
            or not route["server_name"]
            or route["server_certificate_verified"] is not True
            or route["hostname_verified"] is not True
            or route["bearer_authenticated"] is not True
            or route["mutual_tls"] is not False
            or route["request_digest"] != outer["request_digest"]
            or route["response_digest"] != outer["response_digest"]
        ):
            raise F2ValidationError("private non-loopback server-authenticated TLS plus bearer route required")
        for field in ("leaf_der_digest", "ca_authority_digest", "network_grant_ref", "route_digest"):
            _ref(route[field], f"TLS route {field}")
        if metadata["schema_version"] != "bb.wrapper.fixed-policy-response-binding.v1" or metadata["intelligence_claim"] is not None or metadata["turn"] != number or metadata["request_digest"] != outer["request_digest"]:
            raise F2ValidationError("fixed policy response binding mismatch")
        for field in ("code_digest", "script_digest", "model_label_digest", "instance_digest"):
            if metadata[field] != authority[field]:
                raise F2ValidationError("fixed policy authority join mismatch")
        tool_digest = _ref(metadata["tool_call_digest"], "fixed policy tool call digest")
        content_digest = _ref(metadata["response_content_digest"], "fixed policy content digest")
        if call["call_id"] != "call_" + tool_digest[7:31] or response["id"] != "resp_" + content_digest[7:31]:
            raise F2ValidationError("fixed policy deterministic id mismatch")
        content_binding = dict(metadata)
        content_binding.pop("response_content_digest")
        if content_digest != sha256_ref(canonical_json_bytes({"output": response["output"], "binding": content_binding})):
            raise F2ValidationError("fixed policy response content join mismatch")
        observed = _object(observation, {"path", "request_body_sha256", "request_digest", "response_body_sha256", "response_digest"}, "callback observation", exact=True)
        if observed["path"] != "/v1/responses" or observed["request_digest"] != outer["request_digest"] or observed["response_digest"] != outer["response_digest"]:
            raise F2ValidationError("fixed policy callback observation mismatch")
        request_body = {key: outer[key] for key in ("schema_version", "episode_id", "effective_plan_digest", "binding_digest", "policy_slot_id", "request_digest", "request_payload", "turn", "attempt")}
        response_body = {"response_digest": outer["response_digest"], "response_payload": outer["response_payload"]}
        if observed["request_body_sha256"] != sha256_ref(canonical_json_bytes(request_body)) or observed["response_body_sha256"] != sha256_ref(canonical_json_bytes(response_body)):
            raise F2ValidationError("callback raw request/response body hash mismatch")
        for field in ("request_body_sha256", "response_body_sha256"):
            _ref(observed[field], f"callback {field}")
        if number == 1:
            if policy["request_ref"] != outer["request_digest"] or call["name"] != "shell" or call["arguments"] != canonical_json_bytes({"command": authority["shell_command"]}).decode() or any(metadata[field] is not None for field in ("observation_digest", "prior_request_digest", "prior_tool_call_digest")) or call["call_id"] != tool["tool_call_id"]:
                raise F2ValidationError("fixed policy first turn/request/tool mismatch")
            first_request, first_tool = outer["request_digest"], tool_digest
        else:
            if policy["response_ref"] != outer["response_digest"] or call["name"] != "submit" or call["arguments"] != canonical_json_bytes({"result": authority["completion"]}).decode() or metadata["prior_request_digest"] != first_request or metadata["prior_tool_call_digest"] != first_tool or metadata["observation_digest"] != tool["observation_ref"]:
                raise F2ValidationError("fixed policy second turn/response/tool observation mismatch")


def validate_scratch(attempt_dir: Path, *, secret_material: Iterable[bytes] = ()) -> dict[str, Any]:
    attempt = Path(attempt_dir)
    if not _ATTEMPT.fullmatch(attempt.name) or not attempt.is_dir() or attempt.is_symlink():
        raise F2ValidationError("scratch attempt path/id invalid")
    _scan_tree(attempt, secret_material)
    target_raw = _load_inventory(attempt, TARGET_ARTIFACTS, "artifacts")
    runner_raw = _load_inventory(attempt, RUNNER_ARTIFACTS, "runner")
    outer_raw = _load_inventory(attempt, OUTER_ARTIFACTS, "outer")
    target = {name: _json(raw, f"artifacts/{filename}") for name, filename in TARGET_ARTIFACTS.items() for raw in (target_raw[name],)}
    runner_json = {name: _json(runner_raw[name], f"runner/{RUNNER_ARTIFACTS[name]}") for name in ("invocation", "exit")}
    outer = {name: _json(outer_raw[name], f"outer/{OUTER_ARTIFACTS[name]}") for name in ("phase3_invocation", "transport", "result_archive", "phase3_manifest")}
    root_raw = {name: _read_tree_file(attempt, name) for name in ROOT_ARTIFACTS}
    attempt_record = _json(root_raw["attempt.json"], "attempt.json")
    attempt_record = _object(attempt_record, {"schema_version", "attempt_id", "target_run_id", "command_id", "payload_ref"}, "attempt record", exact=True)
    if attempt_record["schema_version"] != "bb.rl.f2.attempt.v1" or attempt_record["attempt_id"] != attempt.name or not _TARGET_RUN.fullmatch(_string(attempt_record["target_run_id"], "target run id")):
        raise F2ValidationError("attempt/target-run authority mismatch")
    command_id = _string(attempt_record["command_id"], "command id")
    _ref(attempt_record["payload_ref"], "attempt payload ref")
    if root_raw["exit_code"].strip() != b"0":
        raise F2ValidationError("outer Phase3 command failed")
    if root_raw["target.stdout"] != runner_raw["stdout"] or root_raw["target.stderr"] != runner_raw["stderr"]:
        raise F2ValidationError("raw target stream join mismatch")
    markers = parse_artifact_markers(root_raw["target.stdout"], attempt.name)
    for marker in markers:
        raw = target_raw[marker["name"]]
        if marker["sha256"] != sha256_ref(raw) or marker["size"] != len(raw):
            raise F2ValidationError("target artifact marker bytes mismatch")
    result_members = _archive_files(root_raw["result.tar.gz"], "target result archive")
    if result_members != {filename: target_raw[name] for name, filename in TARGET_ARTIFACTS.items()}:
        raise F2ValidationError("target artifacts are not the exact result archive")
    runner_members = _archive_files(root_raw["runner-result.tar.gz"], "runner result archive")
    callback_runner_members = {
        "runner/callback-observation-journal.jsonl": "callback_observation_journal",
        "runner/callback-observation-snapshot.json": "callback_observation_snapshot",
        "runner/callback-verification-authority.json": "callback_verification_authority",
        "runner/callback-verification-public-key.pem": "callback_verification_public_key",
        "runner/callback-verification-receipt.json": "callback_verification_receipt",
        "runner/callback-verification-signature.json": "callback_verification_signature",
    }
    required_runner_members = {
        "target.stdout",
        "target.stderr",
        "runner/scheduler.json",
        "runner/docker-identity.json",
        "runner/image-inspect.json",
        "runner/container-inspect.json",
        "runner/component-report.json",
        "runner/post-cleanup.json",
        *callback_runner_members,
    }
    if (
        set(runner_members) != required_runner_members
        or runner_members["target.stdout"] != root_raw["target.stdout"]
        or runner_members["target.stderr"] != root_raw["target.stderr"]
        or any(
            runner_members[archive_name] != runner_raw[inventory_name]
            for archive_name, inventory_name in callback_runner_members.items()
        )
    ):
        raise F2ValidationError("runner archive inventory/stream join mismatch")
    scheduler = _json(runner_members["runner/scheduler.json"], "raw scheduler")
    component = _json(runner_members["runner/component-report.json"], "raw component report")
    docker_identity = _json(runner_members["runner/docker-identity.json"], "raw Docker identity")
    image_observation = _json(runner_members["runner/image-inspect.json"], "raw image observation")
    container_observation = _json(runner_members["runner/container-inspect.json"], "raw container observation")
    post_cleanup = _json(runner_members["runner/post-cleanup.json"], "raw post-cleanup")
    invocation = _object(runner_json["invocation"], {"schema_version", "attempt_id", "target_run_id", "job_id", "node", "payload_ref"}, "runner invocation", exact=True)
    if invocation["schema_version"] != "bb.rl.f2.runner-invocation.v1" or invocation["attempt_id"] != attempt.name or invocation["target_run_id"] != attempt_record["target_run_id"] or invocation["payload_ref"] != attempt_record["payload_ref"]:
        raise F2ValidationError("runner invocation join mismatch")
    _ref(invocation["payload_ref"], "runner payload ref")
    observed = _object(scheduler.get("observed"), {"job_id", "partition", "node_list", "node_count", "task_count", "gpus_on_node", "hostname"}, "scheduler observed", exact=True)
    requested = _object(scheduler.get("requested"), {"partition", "nodes", "tasks", "gpus"}, "scheduler requested", exact=True)
    scontrol = _object(
        scheduler.get("scontrol"),
        {"argv", "exit_code", "stderr", "stdout"},
        "scheduler scontrol",
        exact=True,
    )
    scontrol_tres = [
        token
        for token in str(scontrol["stdout"]).split()
        if token.startswith("TresPerNode=")
    ]
    if (
        requested != {"partition": PARTITION, "nodes": 1, "tasks": 1, "gpus": 1}
        or observed["node_count"] != 1
        or observed["task_count"] != 1
        or observed["gpus_on_node"] != "1"
        or scontrol["argv"]
        != ["scontrol", "show", "job", "-o", observed["job_id"]]
        or scontrol["exit_code"] != 0
        or scontrol_tres != ["TresPerNode=gres:gpu:1"]
    ):
        raise F2ValidationError(
            "exact one-node/task/GPU scheduler request-observation-allocation required"
        )
    if scheduler.get("target_alias") != TARGET_ALIAS or observed["job_id"] != invocation["job_id"] or observed["hostname"] != invocation["node"] or observed["partition"] != PARTITION:
        raise F2ValidationError("IBM scheduler node/job join mismatch")
    if component.get("report_id") != attempt.name or component.get("target_run_id") != attempt_record["target_run_id"] or component.get("passed") is not True or component.get("blocked_reason") not in ("", None) or component.get("promotion_allowed") is not False or component.get("point_award_allowed") is not False or component.get("bead_closure_allowed") is not False:
        raise F2ValidationError("runner component claim boundary mismatch")
    docker = _object(docker_identity, {"schema_version", "version", "info"}, "Docker identity", exact=True)
    if docker["schema_version"] != "bb.rl.f2.docker-identity.v1" or docker["version"].get("exit_code") != 0 or docker["info"].get("exit_code") != 0:
        raise F2ValidationError("Docker runtime identity observation failed")
    image = _object(image_observation, {"schema_version", "requested_ref", "measured_image_id", "admission", "authority"}, "image observation", exact=True)
    image_authority = _object(image["authority"], {"binding", "immutable_reference", "image_id", "composition_digest", "outer_bridge_plan"}, "reviewed image authority", exact=True)
    if image["schema_version"] != "bb.rl.f2.image-observation.v1" or image["admission"] != "composition_private_daemon_offline_authority" or image["requested_ref"] != image_authority["immutable_reference"] or image["measured_image_id"] != image_authority["image_id"] or image_authority["binding"] != "composition-owned" or _ref(image["measured_image_id"], "measured image id") != image["measured_image_id"]:
        raise F2ValidationError("reviewed offline immutable image authority mismatch")
    container = _object(container_observation, {"schema_version", "container_id", "create_exit_code", "inspect_exit_code", "runtime_authority", "outer_bridge_lease", "prebound_service_socket_leases", "callback_tls_host", "outer_wrapper", "attachment_inspect_bytes_base64", "attachment_inspect_sha256"}, "container observation", exact=True)
    if container["create_exit_code"] != 0 or container["inspect_exit_code"] != 0 or not container["container_id"]:
        raise F2ValidationError("container runtime observation failed")
    from breadboard.rl.harness.composition import (
        OuterBridgeCleanupReceiptV1,
        OuterBridgeLeaseV1,
        OuterBridgePlanV1,
        PreboundServiceSocketLeaseV1,
        PreboundServiceSocketPlanV1,
        TlsCallbackRuntimeInputV1,
    )
    try:
        bridge_lease_model = OuterBridgeLeaseV1.model_validate(container["outer_bridge_lease"])
        socket_lease_models = tuple(PreboundServiceSocketLeaseV1.model_validate(value) for value in container["prebound_service_socket_leases"])
        attachment_bytes = base64.b64decode(container["attachment_inspect_bytes_base64"], validate=True)
    except Exception as exc:
        raise F2ValidationError("measured bridge lease/socket observation invalid") from exc
    bridge_lease = bridge_lease_model.model_dump(mode="json")
    bridge_lease_digest = bridge_lease_model.canonical_digest()
    outer_wrapper = _object(container["outer_wrapper"], {"container_id", "network_id", "network_name", "lease_id"}, "outer wrapper bridge attachment", exact=True)
    if not outer_wrapper["container_id"] or {key: outer_wrapper[key] for key in ("network_id", "network_name", "lease_id")} != {"network_id": bridge_lease["network_id"], "network_name": bridge_lease["network_name"], "lease_id": bridge_lease["lease_id"]} or sha256_ref(attachment_bytes) != container["attachment_inspect_sha256"]:
        raise F2ValidationError("outer wrapper bridge lease/attachment mismatch")
    callback_tls_host = _string(container["callback_tls_host"], "callback TLS host")
    import ipaddress
    try:
        callback_address = ipaddress.ip_address(callback_tls_host)
    except ValueError as exc:
        raise F2ValidationError("callback TLS address invalid") from exc
    cleanup_observation = _object(post_cleanup, {"schema_version", "remove", "name_matches", "label_matches", "container_create_attempted", "outer_bridge_cleanup_receipt", "bridge_authentication_verification"}, "post cleanup", exact=True)
    if cleanup_observation["remove"].get("exit_code") != 0 or cleanup_observation["name_matches"] != [] or cleanup_observation["label_matches"] != [] or cleanup_observation["container_create_attempted"] is not True:
        raise F2ValidationError("raw Docker cleanup has residue")
    try:
        bridge_cleanup_model = OuterBridgeCleanupReceiptV1.model_validate(cleanup_observation["outer_bridge_cleanup_receipt"])
    except Exception as exc:
        raise F2ValidationError("signed outer bridge cleanup receipt invalid") from exc
    bridge_cleanup = bridge_cleanup_model.model_dump(mode="json")
    if bridge_cleanup["lease_id"] != bridge_lease["lease_id"] or bridge_cleanup["lease_digest"] != bridge_lease_digest or bridge_cleanup["network_id"] != bridge_lease["network_id"] or bridge_cleanup["network_name"] != bridge_lease["network_name"] or bridge_cleanup["id_absent"] is not True or bridge_cleanup["name_absent"] is not True:
        raise F2ValidationError("outer bridge cleanup lease/residue mismatch")
    authentication_verification = _object(
        cleanup_observation["bridge_authentication_verification"],
        {
            "schema_version",
            "lease_id",
            "lease_digest",
            "cleanup_digest",
            "signer_key_id",
            "lease_verified",
            "cleanup_verified",
            "collector_ref",
            "verified_at",
        },
        "bridge authentication verification",
        exact=True,
    )
    if authentication_verification != {
        "schema_version": "bb.rl.f2.bridge-authentication-verification.v1",
        "lease_id": bridge_lease["lease_id"],
        "lease_digest": bridge_lease_digest,
        "cleanup_digest": sha256_ref(canonical_json_bytes(bridge_cleanup)),
        "signer_key_id": bridge_lease["signer_key_id"],
        "lease_verified": True,
        "cleanup_verified": True,
        "collector_ref": authentication_verification["collector_ref"],
        "verified_at": authentication_verification["verified_at"],
    } or bridge_cleanup["signer_key_id"] != bridge_lease["signer_key_id"]:
        raise F2ValidationError("bridge receipt authenticator verification join mismatch")
    _ref(authentication_verification["collector_ref"], "bridge collector ref")
    _string(authentication_verification["verified_at"], "bridge verification timestamp")
    phase3 = _object(outer["phase3_invocation"], {"schema_version", "argv", "target_alias", "partition", "command_id", "target_run_id", "job_id", "node", "payload_ref", "target_precheck", "target_precheck_raw_b64"}, "outer Phase3 invocation", exact=True)
    if phase3["schema_version"] != "bb.rl.f2.phase3-invocation.v1" or phase3["target_alias"] != TARGET_ALIAS or phase3["partition"] != PARTITION or phase3["command_id"] != command_id or phase3["target_run_id"] != attempt_record["target_run_id"] or phase3["job_id"] != invocation["job_id"] or phase3["node"] != invocation["node"] or phase3["payload_ref"] != invocation["payload_ref"]:
        raise F2ValidationError("outer Phase3/runner join mismatch")
    phase3_argv = phase3["argv"]
    if (
        type(phase3_argv) is not list
        or not phase3_argv
        or "scripts/rl_phase3/run_phase3_target_command.py" not in phase3_argv
        or phase3_argv.count("--gres") != 1
        or phase3_argv[phase3_argv.index("--gres") + 1 :] [:1] != ["gpu:1"]
    ):
        raise F2ValidationError("canonical one-GPU Phase3 runner argv required")
    precheck = _object(phase3["target_precheck"], {"schema_version", "target_record_ref", "ssh_config_ref", "known_hosts_match_ref", "probe_ref", "raw_ref", "ssh_alias", "hostname", "f1_prerequisite_id", "f1_prerequisite_ref", "f1_prerequisite_root", "passed"}, "target precheck", exact=True)
    if precheck["schema_version"] != "bb.rl.f2.target-precheck.v1" or precheck["passed"] is not True or precheck["ssh_alias"] != TARGET_ALIAS or precheck["hostname"] != invocation["node"]:
        raise F2ValidationError("authenticated target precheck identity mismatch")
    if (precheck["f1_prerequisite_id"], precheck["f1_prerequisite_ref"], precheck["f1_prerequisite_root"]) != (F1_PREREQUISITE_ID, F1_PREREQUISITE_REF, F1_PREREQUISITE_ROOT):
        raise F2ValidationError("target precheck canonical F1 prerequisite mismatch")
    for field in ("target_record_ref", "ssh_config_ref", "known_hosts_match_ref", "probe_ref", "raw_ref"):
        _ref(precheck[field], f"target precheck {field}")
    try:
        precheck_raw = base64.b64decode(phase3["target_precheck_raw_b64"], validate=True)
    except (TypeError, ValueError) as exc:
        raise F2ValidationError("target precheck raw bytes invalid") from exc
    if precheck["raw_ref"] != sha256_ref(precheck_raw):
        raise F2ValidationError("target precheck raw byte join mismatch")
    transport = _object(outer["transport"], {"schema_version", "raw_log_ref", "manifest_ref", "runner_archive_ref", "precheck_raw_ref", "precheck_report_ref", "component_failed_count"}, "Phase3 transport", exact=True)
    if transport["schema_version"] != "bb.rl.f2.phase3-transport.v1" or transport["component_failed_count"] != 0 or "local_validation" in transport:
        raise F2ValidationError("non-authoritative or local transport forbidden")
    if transport["raw_log_ref"] != sha256_ref(outer_raw["phase3_log"]) or transport["manifest_ref"] != sha256_ref(outer_raw["phase3_manifest"]) or transport["runner_archive_ref"] != sha256_ref(root_raw["runner-result.tar.gz"]):
        raise F2ValidationError("Phase3 raw log/manifest/runner archive join mismatch")
    if transport["precheck_raw_ref"] != sha256_ref(precheck_raw) or transport["precheck_report_ref"] != sha256_ref(canonical_json_bytes(precheck)):
        raise F2ValidationError("target precheck raw/report transport join mismatch")
    archive_record = _object(outer["result_archive"], {"schema_version", "attempt_id", "sha256", "size_bytes"}, "result archive record", exact=True)
    if archive_record["schema_version"] != "bb.rl.f2.result-archive.v1" or archive_record["attempt_id"] != attempt.name or archive_record["sha256"] != sha256_ref(root_raw["result.tar.gz"]) or archive_record["size_bytes"] != len(root_raw["result.tar.gz"]):
        raise F2ValidationError("result archive byte join mismatch")

    source = _schema(target["source"], "bb.rl.f2.source.v1", "source", {"breadboard_head", "wrapper_head", "tree_ref", "payload_ref"})
    for field in ("breadboard_head", "wrapper_head", "tree_ref", "payload_ref"):
        _ref(source[field], f"source {field}")
    prerequisite = _object(target["prerequisite"], {"schema_version", "canonical_id", "report_schema", "report_ref", "canonical_root"}, "prerequisite", exact=True)
    if source["payload_ref"] != invocation["payload_ref"]:
        raise F2ValidationError("source inventory/payload manifest join mismatch")
    if prerequisite != {"schema_version": "bb.rl.f2.f1-prerequisite.v1", "canonical_id": F1_PREREQUISITE_ID, "report_schema": "bb.rl.f1.ibm-exact-container-preflight-report.v3", "report_ref": F1_PREREQUISITE_REF, "canonical_root": F1_PREREQUISITE_ROOT}:
        raise F2ValidationError("exact approved canonical F1 prerequisite required")
    canonical_root = prerequisite["canonical_root"]
    if ".." in PurePosixPath(canonical_root).parts:
        raise F2ValidationError("F1 prerequisite canonical root invalid")
    package = _schema(target["terminal_package"], "bb.rl.f2.terminal-package.v1", "terminal package", {"package_ref", "selector_kind", "overlay_refs", "adapter_id", "runtime_class", "image_ref", "task_image_ref", "verifier_image_ref", "composition_digest", "outer_bridge_plan", "prebound_service_socket_plans", "tls_callback_runtime_input", "policy_tls_trust_authority", "evidence_receipt_signing_authority", "f1_prerequisite", "config_ref", "policy_ref", "policy_authority", "tool_ref", "verifier_ref", "reward_ref", "task_ref", "evidence_ref", "operator_authority_ref", "execution_path"}, allow_f1_prerequisite=True)
    if package["selector_kind"] != "direct" or package["overlay_refs"] != [] or package["adapter_id"] != "breadboard.terminal-responses.v1" or package["runtime_class"] != "hardened_docker":
        raise F2ValidationError("terminal package must use direct/no-overlay/generic-terminal/hardened-Docker")
    if package["execution_path"] != ["launch/generate_nemo.sh", "launch/eval_nemo.sh", "recipe.nemo_async.evals.run"]:
        raise F2ValidationError("canonical generate/eval execution path required")
    if package["f1_prerequisite"] != prerequisite:
        raise F2ValidationError("terminal package/F1 prerequisite join mismatch")
    package_refs = {key: _ref(package[key], f"terminal package {key}") for key in ("package_ref", "config_ref", "policy_ref", "tool_ref", "verifier_ref", "reward_ref", "task_ref", "evidence_ref", "operator_authority_ref")}
    image_ref = _string(package["image_ref"], "terminal package outer image_ref")
    task_image_ref = _string(package["task_image_ref"], "terminal package task image_ref")
    verifier_image_ref = _string(package["verifier_image_ref"], "terminal package verifier image_ref")
    if any(_IMAGE_REF.fullmatch(value) is None for value in (image_ref, task_image_ref, verifier_image_ref)) or image_ref in {task_image_ref, verifier_image_ref} or image["requested_ref"] != image_ref:
        raise F2ValidationError("immutable outer and primary/verifier image authorities required")
    try:
        from breadboard.rl.harness.composition import (
            EvidenceReceiptSigningAuthorityV1,
            PolicyTlsTrustAuthorityV1,
        )
        bridge_plan_model = OuterBridgePlanV1.model_validate(package["outer_bridge_plan"], strict=True)
        socket_plan_models = tuple(PreboundServiceSocketPlanV1.model_validate(value, strict=True) for value in package["prebound_service_socket_plans"])
        tls_callback_model = TlsCallbackRuntimeInputV1.model_validate(package["tls_callback_runtime_input"], strict=True)
        package_receipt_authority = EvidenceReceiptSigningAuthorityV1.model_validate(
            package["evidence_receipt_signing_authority"], strict=True
        )
        package_tls_trust = PolicyTlsTrustAuthorityV1.model_validate(
            package["policy_tls_trust_authority"], strict=True
        )
    except Exception as exc:
        raise F2ValidationError("immutable bridge/socket/TLS/receipt authority invalid") from exc
    bridge_plan = bridge_plan_model.model_dump(mode="json")
    if _ref(package["composition_digest"], "composition digest") != bridge_lease["composition_digest"] or bridge_lease["plan_digest"] != bridge_plan_model.canonical_digest() or bridge_lease["network_name"] != bridge_plan["network_name"] or image_authority["composition_digest"] != package["composition_digest"] or image_authority["outer_bridge_plan"] != bridge_plan:
        raise F2ValidationError("composition bridge plan/lease authority mismatch")
    lease_inspect_bytes = base64.b64decode(bridge_lease["inspect_bytes_base64"], validate=True)
    lease_inspect = _json(lease_inspect_bytes, "bridge lease inspect bytes")
    lease_strings = {value for _, value in _walk(lease_inspect, "bridge lease inspect") if type(value) is str} | {str(key) for _, value in _walk(lease_inspect, "bridge lease inspect") if type(value) is dict for key in value}
    required_lease_strings = {bridge_lease["network_id"], bridge_plan["network_name"], bridge_plan["subnet"], bridge_plan["gateway"], *(label["key"] for label in bridge_plan["labels"]), *(label["value"] for label in bridge_plan["labels"])}
    if not required_lease_strings <= lease_strings:
        raise F2ValidationError("bridge lease inspect/settings binding mismatch")
    expected_socket_roles = ["callback_tls", "fixed_policy", "harness"]
    if (
        [model.role for model in socket_plan_models] != expected_socket_roles
        or len(socket_lease_models) != 3
        or [model.role for model in socket_lease_models] != expected_socket_roles
    ):
        raise F2ValidationError("exact sorted gateway service socket roles required")
    for plan_model, lease_model in zip(socket_plan_models, socket_lease_models, strict=True):
        if plan_model.gateway != bridge_plan["gateway"] or lease_model.socket_plan_digest != plan_model.canonical_digest() or lease_model.socket_plan_id != plan_model.socket_plan_id or lease_model.bridge_lease_id != bridge_lease["lease_id"] or lease_model.bridge_lease_digest != bridge_lease_digest:
            raise F2ValidationError("prebound service socket plan/lease join mismatch")
    callback_plan = socket_plan_models[0]
    if callback_address.is_loopback or str(callback_address) != bridge_plan["gateway"]:
        raise F2ValidationError("callback TLS must use the exact private bridge gateway")
    if (
        tls_callback_model.route_id != "f2-fixed-policy-callback"
        or tls_callback_model.host != callback_tls_host
        or tls_callback_model.host != bridge_plan["gateway"]
        or tls_callback_model.socket_role != "callback_tls"
        or tls_callback_model.socket_plan_id != callback_plan.socket_plan_id
        or tls_callback_model.observed_port != callback_plan.observed_port
    ):
        raise F2ValidationError("TLS callback runtime/package/socket join mismatch")
    if (
        package_tls_trust.route_id != tls_callback_model.route_id
        or package_tls_trust.server_name != tls_callback_model.host
        or package_tls_trust.ca_bundle_ref.sha256
        != tls_callback_model.ca_certificate_sha256
        or package_tls_trust.minimum_tls_version != "TLSv1.3"
        or package_tls_trust.cipher_suite != "TLS_AES_256_GCM_SHA384"
        or package_tls_trust.dedicated_single_leaf_ca is not True
        or package_tls_trust.expected_leaf_certificate_sha256
        == tls_callback_model.leaf_certificate_sha256
    ):
        raise F2ValidationError("policy TLS DER/PEM trust authority mismatch")
    callback_receipt_authority, callback_records = _verify_callback_observation_packet(
        attempt_id=attempt.name,
        composition_digest=package["composition_digest"],
        route_id=tls_callback_model.route_id,
        journal=runner_raw["callback_observation_journal"],
        snapshot_raw=runner_raw["callback_observation_snapshot"],
        authority_raw=runner_raw["callback_verification_authority"],
        public_key=runner_raw["callback_verification_public_key"],
        receipt_raw=runner_raw["callback_verification_receipt"],
        signature_raw=runner_raw["callback_verification_signature"],
    )
    if (
        callback_receipt_authority
        != package_receipt_authority.model_dump(mode="json")
        or package_receipt_authority.composition_digest
        != package["composition_digest"]
    ):
        raise F2ValidationError("package/callback receipt signing authority mismatch")
    expected_tls_policy = {
        "minimum_tls_version": "TLSv1.3",
        "maximum_tls_version": "TLSv1.3",
        "server_certificate_verification_required": True,
        "hostname_verification_required": True,
        "bearer_authentication_required": True,
        "mutual_tls_required": False,
    }
    if tls_callback_model.tls_policy.model_dump(mode="json") != expected_tls_policy:
        raise F2ValidationError("exact server-authenticated TLS plus bearer policy required")
    route_fields = {
        "connected_peer_ip",
        "tls_version",
        "cipher",
        "leaf_der_digest",
        "ca_authority_digest",
        "server_name",
        "server_certificate_verified",
        "hostname_verified",
        "bearer_authenticated",
        "mutual_tls",
        "network_grant_ref",
        "route_digest",
        "request_digest",
        "response_digest",
    }
    for route_value in target["policy"]["tls_route_observations"]:
        route = _object(route_value, route_fields, "TLS route observation", exact=True)
        if (
            route["tls_version"] != "TLSv1.3"
            or route["leaf_der_digest"] != package_tls_trust.expected_leaf_certificate_sha256
            or route["ca_authority_digest"] != tls_callback_model.ca_certificate_sha256
            or route["server_certificate_verified"] is not True
            or route["hostname_verified"] is not True
            or route["bearer_authenticated"] is not True
            or route["mutual_tls"] is not False
            or route["route_digest"] != callback_records[0]["route_revision_digest"]
            or route["network_grant_ref"] != bridge_lease_digest
            or route["server_name"] != package_tls_trust.server_name
        ):
            raise F2ValidationError("TLS route authentication semantics mismatch")

    row = _schema(target["eval_row"], "bb.rl.f2.eval-row.v1", "eval row", {"row_id", "rollout_id", "episode_id", "status", "package_ref", "selection_ref", "effective_plan_ref", "policy_ref", "tool_ref", "sandbox_ref", "verifier_ref", "reward_ref", "artifact_roots", "completed_envelope_ref", "closed_envelope_ref"})
    summary = _schema(target["eval_summary"], "bb.rl.f2.eval-summary.v1", "eval summary", {"row_count", "rollout_count", "episode_count", "row_ids", "episode_ids", "status"})
    episode_id = _string(row["episode_id"], "episode id")
    if row["status"] != "closed" or summary != {"schema_version":"bb.rl.f2.eval-summary.v1", "row_count":1, "rollout_count":1, "episode_count":1, "row_ids":[row["row_id"]], "episode_ids":[episode_id], "status":"closed"}:
        raise F2ValidationError("exactly one closed row/rollout/episode required")

    selection = _schema(target["selection"], "bb.rl.f2.selection.v1", "selection", {"ref", "episode_id", "selector_kind", "overlay_refs", "config_ref", "effective_plan_ref"})
    plan = _schema(target["effective_plan"], "bb.rl.f2.effective-plan.v1", "effective plan", {"ref", "episode_id", "selection_ref", "config_ref", "policy_ref", "tool_ref", "sandbox_ref", "verifier_ref", "reward_ref", "artifact_ref"})
    policy = _schema(target["policy"], "bb.rl.f2.policy-observation.v1", "policy", {"ref", "episode_id", "provenance", "request_ref", "response_ref", "order", "tool_call_id", "authority", "turns", "callback_observations", "tls_route_observations"}, exact=True)
    package_policy_authority = _object(package["policy_authority"], {"code_digest", "script_digest", "model_label_digest", "instance_digest"}, "package policy authority", exact=True)
    if package_policy_authority != {field: policy["authority"][field] for field in package_policy_authority}:
        raise F2ValidationError("package/fixed-policy authority digest join mismatch")
    route_record = _object(
        callback_records[0],
        {
            "schema_version",
            "route_id",
            "route_revision_digest",
            "dns_policy_digest",
            "ip_policy_digest",
            "bind_address",
            "bind_port",
            "server_hostname",
            "minimum_tls_version",
            "cipher_suite",
            "ca_bundle_sha256",
            "ca_certificate_pem",
            "leaf_certificate_sha256",
            "leaf_certificate_pem",
        },
        "callback journal TLS route",
        exact=True,
    )
    if (
        route_record["schema_version"]
        != "bb.wrapper.callback-tls-route-observation.v1"
        or route_record["route_id"] != tls_callback_model.route_id
        or route_record["bind_address"] != tls_callback_model.host
        or route_record["bind_port"] != tls_callback_model.observed_port
        or route_record["server_hostname"] != tls_callback_model.host
        or route_record["minimum_tls_version"] != "TLSv1.3"
        or route_record["cipher_suite"] != "TLS_AES_256_GCM_SHA384"
        or route_record["ca_bundle_sha256"] != tls_callback_model.ca_certificate_sha256
        or route_record["leaf_certificate_sha256"] != tls_callback_model.leaf_certificate_sha256
        or sha256_ref(route_record["ca_certificate_pem"].encode())
        != tls_callback_model.ca_certificate_sha256
        or sha256_ref(route_record["leaf_certificate_pem"].encode())
        != tls_callback_model.leaf_certificate_sha256
    ):
        raise F2ValidationError("callback journal TLS route authority mismatch")
    for index, record_value in enumerate(callback_records[1:], start=1):
        record = _object(
            record_value,
            {
                "schema_version",
                "episode_id",
                "effective_plan_digest",
                "binding_digest",
                "policy_slot_id",
                "turn",
                "attempt",
                "path",
                "request_body_sha256",
                "request_digest",
                "response_body_sha256",
                "response_digest",
                "response_payload",
                "transport",
            },
            f"callback journal turn {index}",
            exact=True,
        )
        turn = policy["turns"][index - 1]
        if (
            record["schema_version"] != "bb.wrapper.callback-turn-observation.v1"
            or record["episode_id"] != episode_id
            or record["turn"] != index
            or record["attempt"] != 1
            or {field: record[field] for field in ("path", "request_body_sha256", "request_digest", "response_body_sha256", "response_digest")}
            != policy["callback_observations"][index - 1]
            or record["response_payload"] != turn["response_payload"]
            or record["transport"] != policy["tls_route_observations"][index - 1]
        ):
            raise F2ValidationError("callback journal turn/policy/TLS join mismatch")
    tool = _schema(target["tool"], "bb.rl.f2.tool-observation.v1", "tool", {"ref", "episode_id", "tool_id", "tool_call_id", "command_ref", "observation_ref", "order"})
    sandbox = _schema(target["sandbox"], "bb.rl.f2.sandbox-attestation.v1", "sandbox", {"ref", "episode_id", "runtime_class", "image_ref", "runtime_ref", "security_ref", "network_ref", "task_ref", "container_id", "lease_id", "workspace_id", "started_at"})
    verifier = _schema(target["verifier"], "bb.rl.f2.verifier-attestation.v1", "verifier", {"ref", "episode_id", "provenance", "image_ref", "verifier_ref", "tool_observation_ref", "artifact_ref", "reward_ref", "container_id", "lease_id", "snapshot_ref", "credential_refs", "finished_at"})
    reward = _schema(target["reward"], "bb.rl.f2.reward.v1", "reward", {"ref", "episode_id", "value", "components"})
    completed = _schema(target["completed"], "bb.rl.f2.completed-envelope.v1", "completed", {"ref", "episode_id", "status", "observed_at", "artifact_ref", "reward_ref", "resource_receipts", "cleanup"})
    closed = _schema(target["closed"], "bb.rl.f2.closed-envelope.v1", "closed", {"ref", "episode_id", "status", "observed_at", "completed_ref", "resource_receipts", "cleanup_ref"})
    cleanup = _schema(target["cleanup"], "bb.rl.f2.cleanup.v1", "cleanup", {"ref", "episode_id", "released", "processes", "containers", "leases", "workspaces", "caches", "secrets"})
    objects, roots = _validate_artifact_graph(target["artifact_graph"])

    joined = (("selection", selection), ("plan", plan), ("policy", policy), ("tool", tool), ("sandbox", sandbox), ("verifier", verifier), ("reward", reward), ("completed", completed), ("closed", closed), ("cleanup", cleanup))
    _same("episode_id", episode_id, joined)
    if selection["selector_kind"] != "direct" or selection["overlay_refs"] != [] or selection["config_ref"] != package_refs["config_ref"]:
        raise F2ValidationError("selection/config/overlay join mismatch")
    refs = {name: _ref(value["ref"], f"{name} ref") for name, value in joined}
    required_cas_refs = {
        *(_ref(source[field], f"source {field}") for field in ("breadboard_head", "wrapper_head", "tree_ref", "payload_ref")),
        *package_refs.values(),
        *refs.values(),
        _ref(policy["request_ref"], "policy request ref"),
        _ref(policy["response_ref"], "policy response ref"),
        _ref(tool["command_ref"], "tool command ref"),
        *(_ref(route[field], f"TLS route {field}") for route in policy["tls_route_observations"] for field in ("leaf_der_digest", "ca_authority_digest", "network_grant_ref", "route_digest")),
        _ref(tool["observation_ref"], "tool observation ref"),
        *(_ref(sandbox[field], f"sandbox {field}") for field in ("runtime_ref", "security_ref", "network_ref", "task_ref")),
        _ref(verifier["snapshot_ref"], "verifier sealed snapshot ref"),
    }
    if not required_cas_refs <= set(objects):
        raise F2ValidationError("content-addressed record ref lacks exported CAS bytes")
    expected_row = {"selection_ref": refs["selection"], "effective_plan_ref": refs["plan"], "policy_ref": refs["policy"], "tool_ref": refs["tool"], "sandbox_ref": refs["sandbox"], "verifier_ref": refs["verifier"], "reward_ref": refs["reward"], "completed_envelope_ref": refs["completed"], "closed_envelope_ref": refs["closed"]}
    _same("package_ref", package_refs["package_ref"], (("eval row", row),))
    for field, expected in expected_row.items(): _same(field, expected, (("eval row", row),))
    for field, expected in (("selection_ref", refs["selection"]), ("config_ref", package_refs["config_ref"]), ("policy_ref", refs["policy"]), ("tool_ref", refs["tool"]), ("sandbox_ref", refs["sandbox"]), ("verifier_ref", refs["verifier"]), ("reward_ref", refs["reward"]), ("artifact_ref", package_refs["evidence_ref"])):
        _same(field, expected, (("effective plan", plan),))
    if policy["provenance"] != "production-fixed-real-policy" or policy["tool_call_id"] != tool["tool_call_id"] or policy["order"] != 0 or tool["order"] != 1 or tool["tool_id"] != "shell":
        raise F2ValidationError("fixed-real-policy/shell ordering join mismatch")
    _validate_fixed_policy(policy, tool, episode_id, refs["plan"])
    if sandbox["runtime_class"] != "hardened_docker" or sandbox["image_ref"] != task_image_ref or sandbox["task_ref"] != package_refs["task_ref"] or sandbox["container_id"] != container["container_id"]:
        raise F2ValidationError("sandbox immutable authority join mismatch")
    for key in ("runtime_ref", "security_ref", "network_ref"): _ref(sandbox[key], f"sandbox {key}")
    if verifier["provenance"] != "production" or verifier["verifier_ref"] != package_refs["verifier_ref"] or verifier["tool_observation_ref"] != refs["tool"] or verifier["reward_ref"] != refs["reward"]:
        raise F2ValidationError("verifier join mismatch")
    if verifier["image_ref"] != verifier_image_ref:
        raise F2ValidationError("verifier image authority join mismatch")
    if verifier["container_id"] == sandbox["container_id"] or verifier["lease_id"] == sandbox["lease_id"] or verifier["credential_refs"] != []:
        raise F2ValidationError("verifier must use distinct container/lease and no credentials")
    attachment_observation = _json(attachment_bytes, "bridge attachment inspect bytes")
    attachment_strings = {value for _, value in _walk(attachment_observation, "bridge attachments") if type(value) is str}
    if not {outer_wrapper["container_id"], sandbox["container_id"], verifier["container_id"], bridge_lease["network_id"], bridge_lease["network_name"]} <= attachment_strings:
        raise F2ValidationError("outer/primary/verifier bridge attachments not observed")
    value = reward["value"]
    if type(value) not in (int, float) or not __import__("math").isfinite(value) or type(reward["components"]) is not dict or not reward["components"]:
        raise F2ValidationError("finite reward with canonical components required")
    component_values = list(reward["components"].values())
    if any(type(v) not in (int, float) or not __import__("math").isfinite(v) for v in component_values) or sum(component_values) != value:
        raise F2ValidationError("reward components do not canonically sum")
    artifact_roots = tuple(row["artifact_roots"]) if type(row["artifact_roots"]) is list else ()
    if artifact_roots != roots or verifier["artifact_ref"] not in objects or package_refs["evidence_ref"] not in objects:
        raise F2ValidationError("artifact root/evidence/verifier traversal mismatch")
    if completed["status"] != "completed" or completed["cleanup"] != "pending" or closed["status"] != "closed" or cleanup["released"] is not True:
        raise F2ValidationError("completed/closed cleanup transition invalid")
    if not (completed["observed_at"] < closed["observed_at"]) or closed["completed_ref"] != refs["completed"] or closed["cleanup_ref"] != refs["cleanup"]:
        raise F2ValidationError("envelope ordering/join mismatch")
    receipts = {"container_id": sandbox["container_id"], "lease_id": sandbox["lease_id"], "workspace_id": sandbox["workspace_id"]}
    if completed["resource_receipts"] != receipts or closed["resource_receipts"] != receipts:
        raise F2ValidationError("resource receipt join mismatch")
    if any(cleanup[key] != [] for key in ("processes", "containers", "leases", "workspaces", "caches", "secrets")):
        raise F2ValidationError("closed cleanup contains orphan resources")
    for name, raw in target_raw.items():
        if name not in {"prerequisite", "terminal_package"} and b"bb.rl.f1." in raw:
            raise F2ValidationError("F1 artifact/schema embedded in F2 evidence")
    exit_record = _object(runner_json["exit"], {"schema_version", "returncode"}, "runner exit")
    if exit_record["returncode"] != 0:
        raise F2ValidationError("target runner did not succeed")
    if outer["result_archive"].get("attempt_id") != attempt.name:
        raise F2ValidationError("outer archive attempt join mismatch")
    report = {
        "schema_version": REPORT_SCHEMA,
        "attempt_id": attempt.name,
        "target_run_id": attempt_record["target_run_id"],
        "status": "passed",
        "claim": CLAIM,
        "non_claims": list(NON_CLAIMS),
        "episode_id": episode_id,
        "row_id": row["row_id"],
        "prerequisite_id": prerequisite["canonical_id"],
        "prerequisite_root": prerequisite["canonical_root"],
        "prerequisite_ref": prerequisite["report_ref"],
        "package_ref": package_refs["package_ref"],
        "artifact_roots": list(roots),
        "completed_ref": refs["completed"],
        "closed_ref": refs["closed"],
        "raw_artifacts": {name: {"sha256": sha256_ref(raw), "size": len(raw)} for name, raw in target_raw.items()},
        "packet_hashes": {
            **{f"artifacts/{TARGET_ARTIFACTS[name]}": {"sha256": sha256_ref(raw), "size": len(raw)} for name, raw in target_raw.items()},
            **{f"runner/{RUNNER_ARTIFACTS[name]}": {"sha256": sha256_ref(raw), "size": len(raw)} for name, raw in runner_raw.items()},
            **{f"outer/{OUTER_ARTIFACTS[name]}": {"sha256": sha256_ref(raw), "size": len(raw)} for name, raw in outer_raw.items()},
            **{name: {"sha256": sha256_ref(raw), "size": len(raw)} for name, raw in root_raw.items()},
        },
    }
    return report


def _fsync_tree(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file():
            with path.open("rb") as stream: os.fsync(stream.fileno())
    descriptor = os.open(root, os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def _verify_report_tree(
    tree: Path,
    stored: Mapping[str, Any],
    *,
    secret_material: Iterable[bytes],
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="f2-verify-") as temporary:
        scratch = Path(temporary) / _string(stored.get("attempt_id"), "stored attempt id")
        shutil.copytree(tree, scratch)
        (scratch / "f2_report.json").unlink()
        observed = validate_scratch(scratch, secret_material=secret_material)
    if (
        stored != observed
        or stored.get("status") != "passed"
        or not stored.get("raw_artifacts")
        or not stored.get("packet_hashes")
    ):
        raise F2ValidationError("canonical report/raw artifact re-verification failed")
    return observed


def verify_canonical(path: Path, *, secret_material: Iterable[bytes] = ()) -> dict[str, Any]:
    canonical = Path(path)
    expected_root = Path.cwd().resolve() / "docs_tmp" / "ZYPHRA" / "RL_PHASE_5" / "evidence" / "target" / "F2"
    if canonical.parent.resolve() != expected_root or canonical.name.startswith("."):
        raise F2ValidationError("canonical F2 path is renamed or staging")
    stored = _json(_read_tree_file(canonical, "f2_report.json"), "canonical report")
    if canonical.name != stored.get("target_run_id") or not _TARGET_RUN.fullmatch(canonical.name):
        raise F2ValidationError("canonical directory does not bind target run id")
    return _verify_report_tree(
        canonical,
        stored,
        secret_material=secret_material,
    )


def _rename_noreplace(source: Path, destination: Path) -> None:
    import ctypes
    import errno
    import sys
    libc = ctypes.CDLL(None, use_errno=True)
    source_raw = os.fsencode(source)
    destination_raw = os.fsencode(destination)
    if sys.platform == "darwin":
        result = libc.renamex_np(source_raw, destination_raw, 0x00000004)
    elif sys.platform.startswith("linux"):
        result = libc.renameat2(-100, source_raw, -100, destination_raw, 1)
    else:
        raise F2ValidationError("atomic no-replace directory promotion unsupported")
    if result != 0:
        code = ctypes.get_errno()
        if code in {errno.EEXIST, errno.ENOTEMPTY}:
            raise F2ValidationError("canonical destination already exists")
        raise OSError(code, os.strerror(code), str(destination))


def promote(attempt_dir: Path, canonical_root: Path, *, secret_material: Iterable[bytes] = ()) -> Path:
    attempt = Path(attempt_dir)
    report = validate_scratch(attempt, secret_material=secret_material)
    canonical_root = Path(canonical_root)
    expected_root = Path.cwd().resolve() / "docs_tmp" / "ZYPHRA" / "RL_PHASE_5" / "evidence" / "target" / "F2"
    if canonical_root.resolve() != expected_root:
        raise F2ValidationError("canonical root must be docs_tmp/ZYPHRA/RL_PHASE_5/evidence/target/F2")
    destination = canonical_root / report["target_run_id"]
    canonical_root.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise F2ValidationError("canonical destination already exists")
    staging = canonical_root / ("." + report["target_run_id"] + ".staging")
    if staging.exists():
        raise F2ValidationError("promotion staging path already exists")
    try:
        shutil.copytree(attempt, staging, symlinks=True)
        _scan_tree(staging, secret_material)
        (staging / "f2_report.json").write_bytes(canonical_json_bytes(report))
        _fsync_tree(staging)
        _verify_report_tree(staging, report, secret_material=secret_material)
        _rename_noreplace(staging, destination)
        parent_fd = os.open(canonical_root, os.O_RDONLY)
        try: os.fsync(parent_fd)
        finally: os.close(parent_fd)
        verify_canonical(destination, secret_material=secret_material)
    except Exception:
        if staging.exists(): shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination
