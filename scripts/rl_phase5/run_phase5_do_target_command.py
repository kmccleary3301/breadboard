from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import shlex
import stat
import subprocess
import sys
import time
import tarfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agentic_coder_prototype.compilation.contracts import canonical_json_bytes, canonical_json_loads

_MANIFEST_SCHEMA = "bb.rl.phase5-do-target-command-manifest.v1"
_METADATA_SCHEMA = "bb.rl.phase5-do-metadata.v1"
_RUNTIME_INPUT_SCHEMA = "bb.rl.phase5-do-runtime-input.v1"
_SECRET_SCAN_SCHEMA = "bb.rl.phase5-artifact-secret-scan-receipt.v1"
_TARGET_RUN_RE = re.compile(r"^\d{8}T\d{6}Z-do-[a-z0-9][a-z0-9._-]{0,95}$")
_SSH_ALIAS_RE = re.compile(r"^(?!-)[A-Za-z0-9][A-Za-z0-9_.-]*(?:@[A-Za-z0-9][A-Za-z0-9_.-]*)?$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

_REMOTE_METADATA_PY = r'''import json, shlex, sys
raw_path, safe_path, expected_id, expected_region, expected_hostname = sys.argv[1:]
with open(raw_path, "rb") as handle:
    raw = json.load(handle)
def require_text(value, label):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(label)
    text = str(value)
    if not text or len(text) > 256 or any(ch in text for ch in "\r\n\x00"):
        raise ValueError(label)
    return text
def ips(scope):
    values = []
    interfaces = raw.get("interfaces", {})
    rows = interfaces.get(scope, []) if isinstance(interfaces, dict) else []
    if not isinstance(rows, list):
        raise ValueError("interfaces")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("interface")
        for version in ("ipv4", "ipv6"):
            value = row.get(version)
            if isinstance(value, dict) and value.get("ip_address") is not None:
                values.append({"type": scope, "version": version, "ip_address": require_text(value["ip_address"], "ip")})
    return values
features = raw.get("features", {})
if not isinstance(features, dict) or any(not isinstance(k, str) for k in features):
    raise ValueError("features")
tags = raw.get("tags", [])
if not isinstance(tags, list) or any(not isinstance(item, str) for item in tags):
    raise ValueError("tags")
safe = {"schema_version": "bb.rl.phase5-do-metadata.v1", "provider": "digitalocean", "droplet_id": require_text(raw.get("droplet_id"), "droplet_id"), "hostname": require_text(raw.get("hostname"), "hostname"), "region": require_text(raw.get("region"), "region"), "ip_addresses": sorted(ips("private") + ips("public"), key=lambda item: (item["type"], item["version"], item["ip_address"])), "features": features, "tags": sorted(set(tags))}
if (safe["droplet_id"], safe["region"], safe["hostname"]) != (expected_id, expected_region, expected_hostname):
    print("PHASE5_DO_METADATA_MISMATCH=provider_identity", file=sys.stderr)
    raise SystemExit(42)
encoded = json.dumps(safe, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
with open(safe_path, "w", encoding="utf-8") as handle:
    handle.write(encoded)
for name, value in (("DO_DROPLET_ID", safe["droplet_id"]), ("DO_REGION", safe["region"]), ("DO_HOSTNAME", safe["hostname"]), ("BREADBOARD_DO_DROPLET_ID", safe["droplet_id"]), ("BREADBOARD_DO_REGION", safe["region"]), ("BREADBOARD_DO_HOSTNAME", safe["hostname"])):
    print("export " + name + "=" + shlex.quote(value))
'''

_REMOTE_MODE_PY = r'''import json, os, stat, sys
base = sys.argv[1]
with open(os.path.join(base, "payload_manifest.json"), "rb") as handle:
    manifest = json.load(handle)
rows = manifest.get("members")
if not isinstance(rows, list) or manifest.get("member_count") != len(rows):
    raise ValueError("payload manifest inventory mismatch")
for row in rows:
    path = row.get("path")
    expected = "0500" if path == "run.sh" else "0400"
    if not isinstance(path, str) or row.get("mode") != expected:
        raise ValueError("payload declared mode is unsafe")
    target = os.path.join(base, path)
    metadata = os.lstat(target)
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("payload member is not regular")
    os.chmod(target, int(expected, 8), follow_symlinks=False)
os.chmod(os.path.join(base, "payload_manifest.json"), 0o400, follow_symlinks=False)
'''


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def _safe_name(value: str, fallback: str) -> str:
    stem = "".join(character if character.isalnum() or character in "_-" else "_" for character in value)
    stem = stem.strip("_-") or fallback
    if stem == value:
        return stem
    return f"{stem}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"


def _validate_text(value: str, label: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{label} must be a bounded canonical identifier")
    return value


def _validate_reference(value: str, label: str) -> str:
    if (
        type(value) is not str
        or not 1 <= len(value) <= 2048
        or value != value.strip()
        or any(character in "\r\n\x00" for character in value)
    ):
        raise ValueError(f"{label} must be one bounded normalized reference")
    return value


def _canonical_source_manifest_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
    )


def _sha256_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _canonical_json_mapping(raw: bytes, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid JSON") from exc
    if not isinstance(value, Mapping) or canonical_json_bytes(value) != raw:
        raise ValueError(f"{label} must be one canonical JSON object")
    return value


def _load_runtime_input(path: Path) -> dict[str, Any]:
    value = dict(_canonical_json_mapping(path.read_bytes(), "DO runtime input"))
    expected_keys = {
        "schema_version",
        "command_id",
        "target_run_id",
        "ssh_alias",
        "provider",
        "expected_provider_identity",
        "expected_image",
        "payload_sha256",
        "secret_scan_receipt_sha256",
    }
    if set(value) != expected_keys or value.get("schema_version") != _RUNTIME_INPUT_SCHEMA:
        raise ValueError("DO runtime input schema mismatch")
    if value.get("provider") != "digitalocean":
        raise ValueError("DO runtime input provider mismatch")
    for key in ("command_id", "target_run_id", "ssh_alias"):
        item = value.get(key)
        if type(item) is not str:
            raise ValueError(f"DO runtime input {key} is invalid")
    _validate_text(value["command_id"], "runtime command_id")
    if not _TARGET_RUN_RE.fullmatch(value["target_run_id"]):
        raise ValueError("DO runtime input target_run_id is invalid")
    if not _SSH_ALIAS_RE.fullmatch(value["ssh_alias"]):
        raise ValueError("DO runtime input ssh_alias is invalid")
    identity = value.get("expected_provider_identity")
    if not isinstance(identity, Mapping) or set(identity) != {
        "droplet_id",
        "region",
        "hostname",
    }:
        raise ValueError("DO runtime input provider identity is invalid")
    for key in ("droplet_id", "region", "hostname"):
        if type(identity.get(key)) is not str:
            raise ValueError(f"DO runtime input provider {key} is invalid")
        _validate_text(identity[key], f"runtime provider {key}")
    image = value.get("expected_image")
    if not isinstance(image, Mapping) or set(image) != {"id", "reference"}:
        raise ValueError("DO runtime input image is invalid")
    if type(image.get("id")) is not str or not _DIGEST_RE.fullmatch(image["id"]):
        raise ValueError("DO runtime input image id is invalid")
    if type(image.get("reference")) is not str:
        raise ValueError("DO runtime input image reference is invalid")
    _validate_reference(image["reference"], "runtime image reference")
    for key in ("payload_sha256", "secret_scan_receipt_sha256"):
        if type(value.get(key)) is not str or not _DIGEST_RE.fullmatch(value[key]):
            raise ValueError(f"DO runtime input {key} is invalid")
    return value


def _validate_secret_scan_receipt(
    path: Path, payload: Path, expected_receipt_sha256: str
) -> dict[str, Any]:
    raw = path.read_bytes()
    if _sha256_bytes(raw) != expected_receipt_sha256:
        raise ValueError("secret-scan receipt digest mismatch")
    value = dict(_canonical_json_mapping(raw, "secret-scan receipt"))
    artifact = value.get("artifact")
    inventory = value.get("inventory")
    if (
        value.get("schema_version") != _SECRET_SCAN_SCHEMA
        or value.get("passed") is not True
        or value.get("finding_count") != 0
        or value.get("findings") != []
        or not isinstance(artifact, Mapping)
        or artifact.get("sha256") != _sha256_file(payload)
        or artifact.get("size_bytes") != payload.stat().st_size
        or not isinstance(inventory, Mapping)
        or inventory.get("zip_raw_bytes_scanned") is not True
        or inventory.get("filenames_scanned") is not True
        or inventory.get("contents_scanned") is not True
        or inventory.get("canonical_payload_manifest_verified") is not True
        or not isinstance(inventory.get("total_scopes"), int)
        or inventory["total_scopes"] < 2
    ):
        raise ValueError("secret-scan receipt does not authorize transfer")
    return value


def _validate_transitive_source_closure(
    archive: zipfile.ZipFile,
    infos: list[zipfile.ZipInfo],
    payload_manifest: Mapping[str, Any],
    source_manifest: Mapping[str, Any],
    source_raw: bytes,
    member_rows: list[Any],
) -> None:
    dependent_names = {
        "execution_environment_manifest.json",
        "import_preflight.py",
        "run.sh",
        "source_manifest.json",
    }
    by_name = {info.filename: info for info in infos}
    row_by_name = {
        row.get("path"): row
        for row in member_rows
        if isinstance(row, Mapping) and type(row.get("path")) is str
    }
    if (
        len(row_by_name) != len(member_rows)
        or set(by_name) != set(row_by_name) | {"payload_manifest.json"}
    ):
        raise ValueError("payload manifest member inventory is incomplete")
    for name, row in row_by_name.items():
        raw = archive.read(by_name[name])
        if (
            row.get("sha256") != _sha256_bytes(raw)
            or row.get("size_bytes") != len(raw)
            or row.get("mode") != f"{stat.S_IMODE(by_name[name].external_attr >> 16):04o}"
        ):
            raise ValueError(f"payload member digest/size/mode mismatch: {name}")

    source_digest = _sha256_bytes(source_raw)
    runtime_pins = source_manifest.get("runtime_source_pins")
    build_pins = source_manifest.get("build_time_local_validation_pins")
    if not isinstance(runtime_pins, list) or len(runtime_pins) != 6:
        raise ValueError("source manifest must contain exactly six runtime pins")
    if not isinstance(build_pins, list) or len(build_pins) != 13:
        raise ValueError("source manifest must contain exactly thirteen build-time pins")

    source_archive_record = source_manifest.get("archive")
    if not isinstance(source_archive_record, Mapping):
        raise ValueError("source archive binding is missing")
    source_archive_name = source_archive_record.get("path")
    if type(source_archive_name) is not str or source_archive_name not in by_name:
        raise ValueError("source archive member is missing")
    source_archive_raw = archive.read(by_name[source_archive_name])
    if (
        source_archive_record.get("sha256") != _sha256_bytes(source_archive_raw)
        or source_archive_record.get("size_bytes") != len(source_archive_raw)
        or payload_manifest.get("source_archive_sha256")
        != _sha256_bytes(source_archive_raw)
    ):
        raise ValueError("source archive digest/size binding mismatch")
    source_rows = source_manifest.get("members")
    if not isinstance(source_rows, list):
        raise ValueError("source archive member bindings are missing")
    source_row_by_name = {
        row.get("path"): row
        for row in source_rows
        if isinstance(row, Mapping) and type(row.get("path")) is str
    }
    if len(source_row_by_name) != len(source_rows):
        raise ValueError("source archive member bindings are duplicated")
    extracted: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(source_archive_raw), mode="r:gz") as source_tar:
            tar_infos = source_tar.getmembers()
            if {info.name for info in tar_infos} != set(source_row_by_name):
                raise ValueError("source archive member inventory mismatch")
            for info in tar_infos:
                stream = source_tar.extractfile(info)
                raw = None if stream is None else stream.read()
                row = source_row_by_name[info.name]
                if (
                    not info.isfile()
                    or raw is None
                    or row.get("sha256") != _sha256_bytes(raw)
                    or row.get("size_bytes") != len(raw)
                ):
                    raise ValueError(f"source archive member mismatch: {info.name}")
                extracted[info.name] = raw
    except tarfile.TarError as exc:
        raise ValueError("source archive is invalid") from exc
    for pin in runtime_pins:
        if not isinstance(pin, Mapping) or type(pin.get("path")) is not str:
            raise ValueError("runtime source pin is invalid")
        raw = extracted.get(pin["path"])
        if (
            raw is None
            or pin.get("sha256") != _sha256_bytes(raw)
            or pin.get("line_count") != len(raw.splitlines())
        ):
            raise ValueError(f"runtime source pin mismatch: {pin['path']}")
    if any(
        isinstance(pin, Mapping) and pin.get("path") in extracted for pin in build_pins
    ):
        raise ValueError("build-time pins overlap runtime source archive")

    environment_raw = archive.read("execution_environment_manifest.json")
    environment = _canonical_json_mapping(
        environment_raw, "execution environment manifest"
    )
    if (
        not isinstance(environment.get("invariants"), Mapping)
        or environment["invariants"].get("F3_SOURCE_MANIFEST_SHA256")
        != source_digest
    ):
        raise ValueError("stale run environment source-manifest digest")
    run_matches = re.findall(
        rb"(?m)^source_manifest_sha256=(sha256:[0-9a-f]{64})$",
        archive.read("run.sh"),
    )
    if run_matches != [source_digest.encode()]:
        raise ValueError("stale run.sh source-manifest digest")
    import_matches = re.findall(
        rb'EXPECTED_SOURCE_MANIFEST\s*=\s*\(\s*"(sha256:[0-9a-f]{64})"\s*\)',
        archive.read("import_preflight.py"),
    )
    if import_matches != [source_digest.encode()]:
        raise ValueError("stale import preflight source-manifest digest")

    recipe_raw = archive.read("input_builder_recipe.json")
    recipe = _canonical_json_mapping(recipe_raw, "input builder recipe")
    if (
        not isinstance(recipe.get("approved_source"), Mapping)
        or recipe["approved_source"].get("source_manifest_sha256") != source_digest
    ):
        raise ValueError("stale recipe source-manifest digest")
    expected_dependents = {
        "execution_environment_manifest.json": _sha256_bytes(environment_raw),
        "import_preflight.py": _sha256_bytes(archive.read("import_preflight.py")),
        "run.sh": _sha256_bytes(archive.read("run.sh")),
        "source_manifest.json": source_digest,
    }
    recipe_dependents = recipe.get("source_manifest_dependents")
    payload_dependents = payload_manifest.get("source_manifest_dependents")
    if (
        not isinstance(recipe_dependents, Mapping)
        or set(recipe_dependents) != dependent_names
        or dict(recipe_dependents) != expected_dependents
    ):
        raise ValueError("recipe source-manifest dependent graph mismatch")
    if (
        not isinstance(payload_dependents, Mapping)
        or set(payload_dependents) != dependent_names
        or dict(payload_dependents) != expected_dependents
    ):
        raise ValueError("payload source-manifest dependent graph mismatch")
    if (
        payload_manifest.get("execution_environment_manifest_sha256")
        != _sha256_bytes(environment_raw)
        or payload_manifest.get("input_builder_recipe_sha256")
        != _sha256_bytes(recipe_raw)
    ):
        raise ValueError("payload transitive digest binding mismatch")


def _validate_payload(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise ValueError("payload zip contains duplicate paths")
            for name in names:
                pure = PurePosixPath(name)
                if pure.is_absolute() or ".." in pure.parts or "\x00" in name:
                    raise ValueError("payload zip contains an unsafe path")
            if any(stat.S_ISLNK(info.external_attr >> 16) for info in infos):
                raise ValueError("payload zip contains a symbolic link")
            if any(
                info.is_dir() or not stat.S_ISREG(info.external_attr >> 16)
                for info in infos
            ):
                raise ValueError("payload zip members must be regular files")
            run_info = next((info for info in infos if info.filename == "run.sh"), None)
            if run_info is None:
                raise ValueError("payload zip must contain root executable run.sh")
            payload_info = next(
                (info for info in infos if info.filename == "payload_manifest.json"),
                None,
            )
            if payload_info is None:
                raise ValueError("payload manifest is required")
            try:
                candidate = canonical_json_loads(archive.read(payload_info))
            except (TypeError, ValueError) as exc:
                raise ValueError("payload manifest is invalid JSON") from exc
            if not isinstance(candidate, Mapping):
                raise ValueError("payload manifest must be an object")
            payload_manifest: Mapping[str, Any] = candidate
            members = candidate.get("members")
            if (
                not isinstance(members, list)
                or candidate.get("member_count") != len(members)
            ):
                raise ValueError("payload manifest member inventory is incomplete")
            row_by_name = {
                row.get("path"): row
                for row in members
                if isinstance(row, Mapping) and type(row.get("path")) is str
            }
            if (
                len(row_by_name) != len(members)
                or set(names) != set(row_by_name) | {"payload_manifest.json"}
            ):
                raise ValueError("payload manifest member inventory is incomplete")
            for info in infos:
                expected_mode = 0o500 if info.filename == "run.sh" else 0o400
                actual_mode = stat.S_IMODE(info.external_attr >> 16)
                if actual_mode != expected_mode:
                    raise ValueError(
                        f"payload member mode is unsafe: {info.filename}"
                    )
                if info.filename == "payload_manifest.json":
                    continue
                row = row_by_name[info.filename]
                raw = archive.read(info)
                if (
                    row.get("mode") != f"{expected_mode:04o}"
                    or row.get("sha256") != _sha256_bytes(raw)
                    or row.get("size_bytes") != len(raw)
                ):
                    raise ValueError(
                        f"payload member digest/size/manifest-mode mismatch: {info.filename}"
                    )
            source_info = next(
                (info for info in infos if info.filename == "source_manifest.json"),
                None,
            )
            source_rows = [
                row
                for row in members
                if isinstance(row, Mapping)
                and row.get("path") == "source_manifest.json"
            ]
            source_declared = bool(source_rows) or (
                payload_manifest is not None
                and "source_manifest_sha256" in payload_manifest
            )
            if source_info is not None or source_declared:
                if (
                    source_info is None
                    or source_info.is_dir()
                    or not stat.S_ISREG(source_info.external_attr >> 16)
                    or payload_manifest is None
                    or len(source_rows) != 1
                ):
                    raise ValueError("source manifest binding is incomplete")
                source_raw = archive.read(source_info)
                if source_raw.count(b"\n") != 1 or not source_raw.endswith(b"\n"):
                    raise ValueError(
                        "source manifest must contain exactly one final LF"
                    )
                try:
                    source_value = json.loads(source_raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError("source manifest is invalid JSON") from exc
                if _canonical_source_manifest_bytes(source_value) != source_raw:
                    raise ValueError("source manifest bytes are not canonical")
                if not isinstance(source_value, Mapping):
                    raise ValueError("source manifest must be an object")
                runtime_pins = source_value.get("runtime_source_pins")
                if runtime_pins is not None:
                    source_members = source_value.get("members")
                    if not isinstance(runtime_pins, list) or not isinstance(
                        source_members, list
                    ):
                        raise ValueError("source manifest runtime pins are invalid")
                    member_by_path = {
                        row.get("path"): row
                        for row in source_members
                        if isinstance(row, Mapping)
                        and type(row.get("path")) is str
                    }
                    runtime_paths: list[str] = []
                    for pin in runtime_pins:
                        if (
                            not isinstance(pin, Mapping)
                            or type(pin.get("path")) is not str
                        ):
                            raise ValueError(
                                "source manifest runtime pins are invalid"
                            )
                        runtime_paths.append(pin["path"])
                        member = member_by_path.get(pin["path"])
                        if (
                            member is None
                            or pin.get("sha256") != member.get("sha256")
                        ):
                            raise ValueError(
                                "source manifest runtime pin is missing from members"
                            )
                    if len(runtime_paths) != len(set(runtime_paths)):
                        raise ValueError(
                            "source manifest runtime pins are duplicated"
                        )
                source_sha256 = "sha256:" + hashlib.sha256(source_raw).hexdigest()
                source_row = source_rows[0]
                if (
                    payload_manifest.get("source_manifest_sha256") != source_sha256
                    or source_row.get("sha256") != source_sha256
                    or source_row.get("size_bytes") != len(source_raw)
                ):
                    raise ValueError("source manifest digest binding mismatch")
                if "source_manifest_dependents" in payload_manifest:
                    _validate_transitive_source_closure(
                        archive,
                        infos,
                        payload_manifest,
                        source_value,
                        source_raw,
                        members,
                    )
    except zipfile.BadZipFile as exc:
        raise ValueError("payload is not a valid zip") from exc


def _build_remote_command(
    *,
    target_run_id: str,
    command_id: str,
    remote_zip: str,
    expected_droplet_id: str,
    expected_region: str,
    expected_hostname: str,
    expected_image_id: str,
    expected_image_reference: str,
    secret_scan_receipt_sha256: str,
) -> str:
    work_template = f"/tmp/bb-p5-do-{command_id}.XXXXXX"
    return " ".join(
        (
            "set -euo pipefail;",
            "WORK='';",
            f"REMOTE_ZIP={shlex.quote(remote_zip)};",
            "cleanup(){ test -z \"$WORK\" || rm -rf -- \"$WORK\"; rm -f -- \"$REMOTE_ZIP\"; };",
            "trap cleanup EXIT;",
            f"WORK=$(mktemp -d {shlex.quote(work_template)});",
            "python3 -m zipfile -e \"$REMOTE_ZIP\" \"$WORK\";",
            "python3 -c "
            + shlex.quote(_REMOTE_MODE_PY)
            + " \"$WORK\";",
            "cd \"$WORK\";",
            "test -f ./run.sh;",
            "curl -fsS --max-time 10 http://169.254.169.254/metadata/v1.json -o .metadata.raw.json;",
            "python3 -c "
            + shlex.quote(_REMOTE_METADATA_PY)
            + " .metadata.raw.json .metadata.safe.json "
            + " ".join(
                shlex.quote(value)
                for value in (expected_droplet_id, expected_region, expected_hostname)
            )
            + " > .metadata.env;",
            ". ./.metadata.env;",
            "test \"$(docker image inspect --format '{{.Id}}' "
            + shlex.quote(expected_image_reference)
            + ")\" = "
            + shlex.quote(expected_image_id)
            + " || { echo PHASE5_DO_METADATA_MISMATCH=image_identity >&2; exit 43; };",
            "printf 'PHASE5_DO_METADATA_JSON=%s\\n' \"$(cat .metadata.safe.json)\";",
            f"export PHASE3_TARGET_RUN_ID={shlex.quote(target_run_id)};",
            f"export BREADBOARD_TARGET_RUN_ID={shlex.quote(target_run_id)};",
            f"export PHASE3_COMMAND_ID={shlex.quote(command_id)};",
            f"export PHASE5_DO_IMAGE_ID={shlex.quote(expected_image_id)};",
            f"export PHASE5_DO_IMAGE_REFERENCE={shlex.quote(expected_image_reference)};",
            f"export PHASE5_SECRET_SCAN_RECEIPT_SHA256={shlex.quote(secret_scan_receipt_sha256)};",
            "./run.sh",
        )
    )


def _build_ssh_command(ssh_alias: str, remote_command: str) -> list[str]:
    return ["ssh", "--", ssh_alias, f"bash -lc {shlex.quote(remote_command)}"]


def _sanitize_metadata(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("DigitalOcean metadata must be one object")
    expected = {
        "schema_version",
        "provider",
        "droplet_id",
        "hostname",
        "region",
        "ip_addresses",
        "features",
        "tags",
    }
    if set(value) != expected:
        raise ValueError("DigitalOcean metadata contains a non-whitelisted field")
    if value.get("schema_version") != _METADATA_SCHEMA or value.get("provider") != "digitalocean":
        raise ValueError("DigitalOcean metadata schema/provider mismatch")
    sanitized: dict[str, Any] = {
        "schema_version": _METADATA_SCHEMA,
        "provider": "digitalocean",
    }
    for key in ("droplet_id", "hostname", "region"):
        item = value.get(key)
        if type(item) is not str or not item or len(item) > 256 or any(ch in item for ch in "\r\n\x00"):
            raise ValueError(f"DigitalOcean metadata {key} is invalid")
        sanitized[key] = item
    addresses = value.get("ip_addresses")
    if not isinstance(addresses, list):
        raise ValueError("DigitalOcean metadata IP list is invalid")
    clean_addresses: list[dict[str, str]] = []
    for address in addresses:
        if not isinstance(address, dict) or set(address) != {"type", "version", "ip_address"}:
            raise ValueError("DigitalOcean metadata IP entry is invalid")
        if address.get("type") not in {"private", "public"} or address.get("version") not in {"ipv4", "ipv6"}:
            raise ValueError("DigitalOcean metadata IP type is invalid")
        ip_address = address.get("ip_address")
        if type(ip_address) is not str or not ip_address or len(ip_address) > 128:
            raise ValueError("DigitalOcean metadata IP address is invalid")
        clean_addresses.append(dict(address))
    sanitized["ip_addresses"] = sorted(
        clean_addresses,
        key=lambda item: (item["type"], item["version"], item["ip_address"]),
    )
    features = value.get("features")
    tags = value.get("tags")
    if not isinstance(features, dict) or any(type(key) is not str for key in features):
        raise ValueError("DigitalOcean metadata features are invalid")
    canonical_json_bytes(features)
    if not isinstance(tags, list) or any(type(tag) is not str for tag in tags):
        raise ValueError("DigitalOcean metadata tags are invalid")
    sanitized["features"] = features
    sanitized["tags"] = sorted(set(tags))
    return sanitized


def _parse_stdout(stdout: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str]:
    metadata: dict[str, Any] | None = None
    reports: list[dict[str, Any]] = []
    blocked = ""
    for line in stdout.splitlines():
        if line.startswith("PHASE5_DO_METADATA_JSON="):
            try:
                if metadata is not None:
                    raise ValueError("duplicate provider metadata")
                raw = line.split("=", 1)[1].encode("utf-8")
                candidate = canonical_json_loads(raw)
                if canonical_json_bytes(candidate) != raw:
                    raise ValueError("provider metadata is not canonical JSON")
                metadata = _sanitize_metadata(candidate)
            except Exception:
                blocked = "invalid_provider_metadata"
        elif line.startswith("PHASE3_COMPONENT_REPORT_JSON="):
            try:
                raw = line.split("=", 1)[1].encode("utf-8")
                candidate = canonical_json_loads(raw)
                if canonical_json_bytes(candidate) != raw:
                    raise ValueError("component report is not canonical JSON")
                if not isinstance(candidate, dict) or not candidate:
                    raise ValueError("component report must be one nonempty object")
                reports.append(candidate)
            except Exception:
                blocked = "invalid_component_report"
    return metadata, reports, blocked


def _component_passed(report: Mapping[str, Any]) -> bool:
    schema = report.get("schema_version")
    if type(schema) is not str or not schema:
        return False
    if "passed" in report and report["passed"] is not True:
        return False
    if "promotion_authority" in report and report["promotion_authority"] is not False:
        return False
    if "scorecard_authority" in report and report["scorecard_authority"] is not False:
        return False
    return True


def _write_canonical(path: Path, value: object) -> None:
    raw = canonical_json_bytes(value)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
        0o640,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short canonical manifest write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": _MANIFEST_SCHEMA,
            "provider": "digitalocean",
            "commands": [],
            "promotion_authority": False,
            "scorecard_authority": False,
        }
    raw = path.read_bytes()
    value = canonical_json_loads(raw)
    if canonical_json_bytes(value) != raw:
        raise ValueError("existing DO command manifest is not canonical JSON")
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != _MANIFEST_SCHEMA
        or value.get("provider") != "digitalocean"
        or value.get("promotion_authority") is not False
        or value.get("scorecard_authority") is not False
        or not isinstance(value.get("commands"), list)
    ):
        raise ValueError("existing DO command manifest has the wrong contract")
    return value


def _remove_component_reports(output_dir: Path, safe_command_id: str) -> None:
    component_dir = output_dir / "component_reports"
    if not component_dir.exists():
        return
    for path in component_dir.glob(f"{safe_command_id}.*.json"):
        if path.is_file():
            path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run a Phase 5 scratch workload directly on one preconfigured DigitalOcean host"
    )
    parser.add_argument("--runtime-input", required=True, type=Path)
    parser.add_argument("--secret-scan-receipt", required=True, type=Path)
    parser.add_argument("--payload-zip", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--scp-timeout-seconds", type=int, default=30)
    parser.add_argument("--run-timeout-seconds", type=int, default=3600)
    parser.add_argument("--maximum-cost-usd", type=float)
    parser.add_argument("--ttl-seconds", type=int)
    parser.add_argument("--teardown-authority-ref")
    args = parser.parse_args(argv)

    try:
        runtime_input = _load_runtime_input(args.runtime_input)
        if runtime_input["payload_sha256"] != _sha256_file(args.payload_zip):
            raise ValueError("DO runtime input payload digest mismatch")
        secret_scan = _validate_secret_scan_receipt(
            args.secret_scan_receipt,
            args.payload_zip,
            runtime_input["secret_scan_receipt_sha256"],
        )
        if args.teardown_authority_ref is not None:
            _validate_reference(args.teardown_authority_ref, "--teardown-authority-ref")
        _validate_payload(args.payload_zip)
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))
    command_id = runtime_input["command_id"]
    target_run_id = runtime_input["target_run_id"]
    ssh_alias = runtime_input["ssh_alias"]
    provider_identity = runtime_input["expected_provider_identity"]
    expected_image = runtime_input["expected_image"]
    if args.scp_timeout_seconds < 1 or args.run_timeout_seconds < 1:
        parser.error("timeouts must be positive")
    if args.maximum_cost_usd is not None and (
        not math.isfinite(args.maximum_cost_usd) or args.maximum_cost_usd <= 0
    ):
        parser.error("--maximum-cost-usd must be a positive finite value")
    if args.ttl_seconds is not None and args.ttl_seconds <= 0:
        parser.error("--ttl-seconds must be positive")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    log_dir = args.output_dir / "command_logs"
    log_dir.mkdir(exist_ok=True)
    component_dir = args.output_dir / "component_reports"
    component_dir.mkdir(exist_ok=True)
    safe_command_id = _safe_name(command_id, "do_command")
    raw_log_path = log_dir / f"{safe_command_id}.log"
    remote_zip = f"/tmp/bb-p5-do-{safe_command_id}-{uuid.uuid4().hex}.zip"
    remote_command = _build_remote_command(
        target_run_id=target_run_id,
        command_id=safe_command_id,
        remote_zip=remote_zip,
        expected_droplet_id=provider_identity["droplet_id"],
        expected_region=provider_identity["region"],
        expected_hostname=provider_identity["hostname"],
        expected_image_id=expected_image["id"],
        expected_image_reference=expected_image["reference"],
        secret_scan_receipt_sha256=runtime_input["secret_scan_receipt_sha256"],
    )
    ssh_command = _build_ssh_command(ssh_alias, remote_command)
    started_at = _iso()
    monotonic_started = time.monotonic()
    exit_code = 1
    blocked_reason = ""
    timed_out = False
    stdout = ""
    stderr = ""
    try:
        scp = subprocess.run(
            ["scp", "--", str(args.payload_zip), f"{ssh_alias}:{remote_zip}"],
            check=False,
            text=True,
            capture_output=True,
            timeout=args.scp_timeout_seconds,
        )
        if scp.returncode != 0:
            exit_code = scp.returncode
            blocked_reason = "payload_transfer_failed"
            stdout, stderr = scp.stdout or "", scp.stderr or ""
        else:
            result = subprocess.run(
                ssh_command,
                check=False,
                text=True,
                capture_output=True,
                timeout=args.run_timeout_seconds,
            )
            exit_code = result.returncode
            stdout, stderr = result.stdout or "", result.stderr or ""
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        timed_out = True
        blocked_reason = "target_timeout"
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    except Exception as exc:
        exit_code = 1
        blocked_reason = exc.__class__.__name__
        stderr = f"{exc.__class__.__name__}: {exc}\n"

    raw_log_path.write_text(stdout + stderr)
    metadata, reports, parse_blocked = _parse_stdout(stdout)
    if parse_blocked:
        blocked_reason = blocked_reason or parse_blocked
    combined = stdout + stderr
    if "PHASE5_DO_METADATA_MISMATCH=image_identity" in combined:
        blocked_reason = "image_metadata_mismatch"
    elif "PHASE5_DO_METADATA_MISMATCH=provider_identity" in combined:
        blocked_reason = "provider_metadata_mismatch"
    if metadata is None and not blocked_reason:
        blocked_reason = "provider_metadata_missing"
    if metadata is not None and (
        metadata["droplet_id"], metadata["region"], metadata["hostname"]
    ) != (
        provider_identity["droplet_id"],
        provider_identity["region"],
        provider_identity["hostname"],
    ):
        blocked_reason = "provider_metadata_mismatch"
    if not reports and not blocked_reason:
        blocked_reason = "component_report_missing"
    if reports and not all(_component_passed(report) for report in reports) and not blocked_reason:
        blocked_reason = "component_report_failed"
    if exit_code != 0 and not blocked_reason:
        blocked_reason = "remote_command_failed"
    passed = exit_code == 0 and not blocked_reason and metadata is not None and bool(reports)

    _remove_component_reports(args.output_dir, safe_command_id)
    component_paths: list[str] = []
    component_hashes: list[str] = []
    if passed:
        for index, report in enumerate(reports, start=1):
            path = component_dir / f"{safe_command_id}.{index}.json"
            _write_canonical(path, report)
            component_paths.append(str(path.relative_to(args.output_dir)))
            component_hashes.append(_sha256_file(path))

    missing_provider_authorities = tuple(
        label
        for label, value in (
            ("maximum_cost_usd", args.maximum_cost_usd),
            ("ttl_seconds", args.ttl_seconds),
            ("teardown_authority_ref", args.teardown_authority_ref),
        )
        if value is None
    )
    completed_at = _iso()
    row = {
        "command_id": command_id,
        "provider": "digitalocean",
        "ssh_alias": ssh_alias,
        "target_run_id": target_run_id,
        "payload_sha256": runtime_input["payload_sha256"],
        "runtime_input_sha256": _sha256_file(args.runtime_input),
        "secret_scan_receipt_sha256": runtime_input[
            "secret_scan_receipt_sha256"
        ],
        "secret_scan_inventory_sha256": secret_scan["inventory"][
            "inventory_sha256"
        ],
        "expected_provider_identity": dict(provider_identity),
        "expected_image": dict(expected_image),
        "observed_provider_metadata": metadata,
        "started_at": started_at,
        "completed_at": completed_at,
        "duration_seconds": max(0.0, time.monotonic() - monotonic_started),
        "run_timeout_seconds": args.run_timeout_seconds,
        "timed_out": timed_out,
        "raw_log_path": str(raw_log_path.relative_to(args.output_dir)),
        "raw_log_sha256": _sha256_file(raw_log_path),
        "component_report_paths": component_paths,
        "component_report_sha256": component_hashes,
        "component_report_count": len(component_paths),
        "exit_code": exit_code,
        "status": "passed" if passed else "failed",
        "blocked_reason": blocked_reason,
        "provider_controls": {
            "maximum_cost_usd": args.maximum_cost_usd,
            "ttl_seconds": args.ttl_seconds,
            "teardown_authority_ref": args.teardown_authority_ref,
            "missing_authorities": list(missing_provider_authorities),
            "provider_promotion_prerequisites_complete": not missing_provider_authorities,
            "provider_promotion_blocked": True,
        },
        "scratch_workload_pass_independent_of_provider_promotion": passed,
        "digitalocean_does_not_substitute_for_ibm_or_slurm": True,
        "promotion_authority": False,
        "scorecard_authority": False,
    }
    manifest_path = args.output_dir / "phase5_do_target_command_manifest.json"
    try:
        manifest = _load_manifest(manifest_path)
    except Exception as exc:
        parser.error(str(exc))
    manifest["commands"] = [
        existing
        for existing in manifest["commands"]
        if isinstance(existing, dict) and existing.get("command_id") != command_id
    ]
    manifest["commands"].append(row)
    manifest["commands"].sort(key=lambda item: str(item.get("command_id", "")))
    _write_canonical(manifest_path, manifest)
    return 0 if passed else (exit_code or 1)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "_build_remote_command",
    "_sanitize_metadata",
    "main",
]
