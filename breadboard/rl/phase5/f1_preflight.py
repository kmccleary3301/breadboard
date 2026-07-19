from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

MARKER_PREFIX = "F1_ARTIFACT="
RESULT_PREFIX = "F1_RESULT_ARCHIVE="
MARKER_SCHEMA = "bb.rl.f1.artifact-marker.v1"
REPORT_SCHEMA = "bb.rl.f1.ibm-exact-container-preflight-report.v3"
TARGET_ALIAS = "ZYPHRA_IBM_AMD_1"
PARTITION = "gpu"
IMAGE_REF = "python@sha256:b81b4bec9aa047850f17862ce34a3ac99463920ef1e9df0434fd7ccdfe2ca691"
CLAIM_BOUNDARY = "authenticated IBM exact-container preflight only; no provider, benchmark, episode-campaign, rollout, reward-quality, or training claim"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_HEAD = re.compile(r"^[0-9a-f]{40}$")
_ATTEMPT = re.compile(r"^f1-[a-z0-9-]{8,80}$")
_ABSOLUTE = re.compile(r"^(?:/|~/|[A-Za-z]:[\\/])")
_TARGET_RUN_ID = re.compile(r"^(\d{8}T\d{6}Z)-slurm-(\d+)$")

TARGET_ARTIFACTS: Mapping[str, str] = {
    "source": "source-inventory.json",
    "dependencies_lock": "dependency-lock.txt",
    "dependencies_freeze": "pip-freeze.txt",
    "dependencies_check": "pip-check.json",
    "resolver": "resolved-env.json",
    "config": "resolved-config.yaml",
    "composition_ref": "composition-reference-observation.json",
    "composition_manifest": "composition-manifest-observation.json",
    "composition_inspect": "composition-inspect-observation.json",
    "composition_inspect_stderr": "inspect.stderr",
    "unauthenticated": "unauthenticated-response.json",
    "wrapper_request": "wrapper-request.json",
    "wrapper_response": "wrapper-response.json",
    "status_response": "status-response.json",
    "completed_response": "completed-response.json",
    "closed_response": "closed-response.json",
    "callback": "callback-observations.json",
    "harness_stdout": "harness.stdout",
    "harness_stderr": "harness.stderr",
    "policy_stdout": "policy.stdout",
    "policy_stderr": "policy.stderr",
    "process_before": "process-before.json",
    "process_after": "process-after.json",
    "private_cleanup": "private-cleanup.json",
}
RUNNER_ARTIFACTS = {
    "scheduler.json",
    "image-inspect.json",
    "container-inspect.json",
    "post-cleanup.json",
}
OUTER_ARTIFACTS = {
    "phase3-command-log-manifest.json",
    "phase3-command.log",
}

class F1ValidationError(ValueError):
    pass


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validation_code_inventory() -> list[dict[str, Any]]:
    repository_root = Path(__file__).resolve().parents[3]
    relative_paths = (
        "breadboard/rl/phase5/f1_preflight.py",
        "scripts/rl_phase3/run_phase3_target_command.py",
        "scripts/rl_phase5/build_f1_phase3_payload.py",
        "scripts/rl_phase5/ingest_f1_phase3_attempt.py",
        "scripts/rl_phase5/validate_f1_preflight.py",
    )
    inventory: list[dict[str, Any]] = []
    for relative in relative_paths:
        path = repository_root / relative
        if not path.is_file() or path.is_symlink():
            raise F1ValidationError(f"validation code is missing or unsafe: {relative}")
        raw = path.read_bytes()
        inventory.append(
            {
                "path": relative,
                "size_bytes": len(raw),
                "sha256": sha256_bytes(raw),
            }
        )
    return inventory


def _exact(obj: object, keys: set[str], where: str) -> Mapping[str, Any]:
    if not isinstance(obj, dict) or set(obj) != keys:
        got = set(obj) if isinstance(obj, dict) else type(obj).__name__
        raise F1ValidationError(f"{where}: exact keys required {sorted(keys)!r}; got {got!r}")
    return obj


def _string(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise F1ValidationError(f"{where}: non-empty string required")
    return value


def _integer(value: object, where: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise F1ValidationError(f"{where}: integer >= {minimum} required")
    return value


def _json(raw: bytes, where: str, *, canonical: bool = True) -> Any:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise F1ValidationError(f"{where}: invalid JSON") from exc
    if canonical and canonical_json_bytes(value) != raw:
        raise F1ValidationError(f"{where}: JSON is not canonical")
    return value


def parse_artifact_markers(stdout: bytes, attempt_id: str) -> list[dict[str, Any]]:
    if not _ATTEMPT.fullmatch(attempt_id):
        raise F1ValidationError("invalid attempt id")
    try:
        lines = stdout.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise F1ValidationError("target stdout is not UTF-8") from exc
    markers: list[dict[str, Any]] = []
    result_count = 0
    for line in lines:
        if line.startswith(RESULT_PREFIX):
            result_count += 1
            continue
        if not line.startswith(MARKER_PREFIX):
            raise F1ValidationError("target stdout contains a non-marker line")
        raw = line[len(MARKER_PREFIX):].encode()
        marker = _json(raw, "marker")
        m = _exact(marker, {"schema_version", "attempt_id", "sequence", "kind", "artifact_path", "size_bytes", "sha256"}, "marker")
        if m["schema_version"] != MARKER_SCHEMA or m["attempt_id"] != attempt_id:
            raise F1ValidationError("marker schema/attempt mismatch")
        _integer(m["sequence"], "marker.sequence")
        _integer(m["size_bytes"], "marker.size_bytes")
        if not _SHA256.fullmatch(_string(m["sha256"], "marker.sha256")):
            raise F1ValidationError("invalid marker sha256")
        kind = _string(m["kind"], "marker.kind")
        p = PurePosixPath(_string(m["artifact_path"], "marker.artifact_path"))
        if p.is_absolute() or ".." in p.parts or str(p) != m["artifact_path"] or len(p.parts) != 1:
            raise F1ValidationError("unsafe/noncanonical marker path")
        if kind not in TARGET_ARTIFACTS or TARGET_ARTIFACTS[kind] != str(p):
            raise F1ValidationError("unknown or mismatched artifact kind/path")
        markers.append(dict(m))
    if result_count != 1:
        raise F1ValidationError("exactly one result archive envelope required")
    if [m["sequence"] for m in markers] != list(range(len(TARGET_ARTIFACTS))):
        raise F1ValidationError("marker sequence is incomplete, duplicate, or unordered")
    if {m["kind"] for m in markers} != set(TARGET_ARTIFACTS):
        raise F1ValidationError("required marker kinds are incomplete or duplicated")
    if len({m["artifact_path"] for m in markers}) != len(markers):
        raise F1ValidationError("duplicate artifact path")
    return markers


def safe_extract_archive(archive: Path, destination: Path) -> None:
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    seen: set[str] = set()
    try:
        with tarfile.open(archive, "r:gz") as tf:
            members = tf.getmembers()
            for member in members:
                name = str(PurePosixPath(member.name))
                p = PurePosixPath(name)
                if member.name != name or p.is_absolute() or ".." in p.parts or name in seen:
                    raise F1ValidationError("archive has duplicate/noncanonical/traversal member")
                seen.add(name)
                if not member.isfile():
                    raise F1ValidationError("archive directories, links, and special members are forbidden")
            tf.extractall(destination, filter="data")
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _walk_json(value: object, where: str = "$") -> Iterable[tuple[str, object]]:
    yield where, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk_json(child, f"{where}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_json(child, f"{where}[{index}]")


def _reject_sensitive_structured(value: object, where: str) -> None:
    sensitive = re.compile(r"^(?:authorization|api[_-]?key|secret(?:[_-]?(?:seed|value|path))?|bearer|token(?:[_-]?value)?|password|env(?:ironment)?|argv)$", re.I)
    for location, child in _walk_json(value, where):
        if isinstance(child, dict):
            for key in child:
                if sensitive.fullmatch(str(key)):
                    raise F1ValidationError(f"sensitive key captured at {location}.{key}")
        if (
            isinstance(child, str)
            and _ABSOLUTE.match(child)
            and not re.fullmatch(
                r"/(?:v1/responses|v2/episodes(?:/[A-Za-z0-9._-]+(?:/envelopes/(?:completed|closed))?)?)",
                child,
            )
        ):
            raise F1ValidationError(f"absolute path captured at {location}")
        if isinstance(child, str) and re.search(r"\bAuthorization\s*:", child, re.I):
            raise F1ValidationError(f"Authorization capture at {location}")


def _reject_absolute_paths(value: object, where: str) -> None:
    for location, child in _walk_json(value, where):
        if isinstance(child, str) and _ABSOLUTE.match(child):
            raise F1ValidationError(f"absolute path captured at {location}")


def _scan_roots(roots: Iterable[Path], secret_material: Iterable[bytes]) -> None:
    try:
        from scripts.rl_phase5.wp10_launcher_evidence import EvidenceError, SeededSecretScanner
    except ImportError as exc:
        raise F1ValidationError("SeededSecretScanner unavailable") from exc
    roots = tuple(Path(root).resolve(strict=True) for root in roots)
    for secret in secret_material:
        try:
            result = SeededSecretScanner(secret).scan(roots)
        except EvidenceError as exc:
            raise F1ValidationError(str(exc)) from exc
        if not result.passed:
            raise F1ValidationError("seeded secret representation found in evidence")


def _artifact_bytes(attempt: Path, markers: list[dict[str, Any]]) -> dict[str, bytes]:
    artifacts = attempt / "artifacts"
    if not artifacts.is_dir() or stat.S_ISLNK(artifacts.lstat().st_mode):
        raise F1ValidationError("artifact directory missing or unsafe")
    actual = {p.name for p in artifacts.iterdir() if p.is_file() and not p.is_symlink()}
    expected = {m["artifact_path"] for m in markers}
    if actual != expected or len(tuple(artifacts.iterdir())) != len(expected):
        raise F1ValidationError("artifact inventory does not exactly match markers")
    result: dict[str, bytes] = {}
    for marker in markers:
        path = artifacts / marker["artifact_path"]
        raw = path.read_bytes()
        if len(raw) != marker["size_bytes"] or sha256_bytes(raw) != marker["sha256"]:
            raise F1ValidationError(f"artifact size/hash mismatch: {marker['artifact_path']}")
        result[marker["kind"]] = raw
    return result


def _runner_json(attempt: Path, name: str) -> Any:
    path = attempt / "runner" / name
    if not path.is_file() or path.is_symlink():
        raise F1ValidationError(f"missing runner artifact {name}")
    return _json(path.read_bytes(), f"runner/{name}")


def _argv_option(argv: list[str], name: str) -> str:
    try:
        value = argv[argv.index(name) + 1]
    except (ValueError, IndexError) as exc:
        raise F1ValidationError(f"outer Phase 3 command lacks {name}") from exc
    return _string(value, f"outer argv {name}")


def _validate_outer_phase3(
    attempt: Path,
    attempt_id: str,
    scheduler: Mapping[str, Any],
    attempt_record: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    outer = attempt / "outer"
    if (
        not outer.is_dir()
        or outer.is_symlink()
        or {path.name for path in outer.iterdir()} != OUTER_ARTIFACTS
        or any(not path.is_file() or path.is_symlink() for path in outer.iterdir())
    ):
        raise F1ValidationError("outer Phase 3 evidence inventory mismatch")
    manifest_raw = (outer / "phase3-command-log-manifest.json").read_bytes()
    raw_log = (outer / "phase3-command.log").read_bytes()
    manifest = _json(manifest_raw, "outer Phase 3 command manifest", canonical=False)
    if (
        not isinstance(manifest, dict)
        or not str(manifest.get("schema_version") or "").startswith(
            "bb.rl.phase3.command_log_manifest."
        )
        or not isinstance(manifest.get("commands"), list)
    ):
        raise F1ValidationError("outer Phase 3 command manifest is invalid")
    matches = [
        row
        for row in manifest["commands"]
        if isinstance(row, dict) and row.get("command_id") == attempt_id
    ]
    if len(matches) != 1:
        raise F1ValidationError("outer Phase 3 command identity is ambiguous")
    row = matches[0]
    if (
        row.get("status") != "passed"
        or row.get("exit_code") != 0
        or row.get("blocked_reason") not in ("", None)
        or row.get("component_failed_count") != 0
        or row.get("slurm_job_id") != scheduler["observed"]["job_id"]
        or row.get("node") != scheduler["observed"]["hostname"]
        or row.get("raw_log_sha256") != "sha256:" + sha256_bytes(raw_log)
    ):
        raise F1ValidationError("outer Phase 3 execution does not bind target facts")
    outer_target_run_id = _string(
        attempt_record["outer_target_run_id"],
        "attempt record outer_target_run_id",
    )
    target_match = _TARGET_RUN_ID.fullmatch(outer_target_run_id)
    if (
        target_match is None
        or target_match.group(2) != scheduler["observed"]["job_id"]
        or row.get("target_run_id") != outer_target_run_id
        or manifest.get("target_run_id") != outer_target_run_id
    ):
        raise F1ValidationError("outer target run identity mismatch")
    argv = row.get("argv")
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise F1ValidationError("outer Phase 3 argv is invalid")
    expected_requested_target_run_id = (
        f"{target_match.group(1)}-slurm-pending"
    )
    if (
        _argv_option(argv, "--ssh-alias") != TARGET_ALIAS
        or _argv_option(argv, "--partition") != PARTITION
        or _argv_option(argv, "--command-id") != attempt_id
        or _argv_option(argv, "--target-run-id")
        != expected_requested_target_run_id
    ):
        raise F1ValidationError("outer Phase 3 target tuple mismatch")
    expected_node = f"PHASE3_NODE={scheduler['observed']['hostname']}".encode()
    expected_job = f"PHASE3_SLURM_JOB_ID={scheduler['observed']['job_id']}".encode()
    if expected_node not in raw_log.splitlines() or expected_job not in raw_log.splitlines():
        raise F1ValidationError("outer raw log lacks scheduler identity")
    return (
        [
            {
                "path": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": sha256_bytes(path.read_bytes()),
            }
            for path in sorted(outer.iterdir())
        ],
        outer_target_run_id,
    )


def _values(value: object, key: str) -> list[object]:
    found: list[object] = []
    if isinstance(value, dict):
        for name, child in value.items():
            if name == key:
                found.append(child)
            found.extend(_values(child, key))
    elif isinstance(value, list):
        for child in value:
            found.extend(_values(child, key))
    return found


def _require_join(value: object, key: str, expected: object, where: str) -> None:
    if expected not in _values(value, key):
        raise F1ValidationError(f"{where}: missing {key} semantic join")


def _validate_scheduler(value: object) -> tuple[str, str]:
    s = _exact(value, {"schema_version", "target_alias", "requested", "observed", "started_utc", "scontrol"}, "scheduler")
    if s["schema_version"] != "bb.rl.f1.scheduler-observation.v1" or s["target_alias"] != TARGET_ALIAS:
        raise F1ValidationError("scheduler identity mismatch")
    req = _exact(s["requested"], {"partition", "nodes", "tasks", "gpus"}, "scheduler.requested")
    if req != {"partition": PARTITION, "nodes": 1, "tasks": 1, "gpus": 1}:
        raise F1ValidationError("requested topology mismatch")
    obs = _exact(s["observed"], {"job_id", "partition", "node_list", "node_count", "task_count", "gpus_on_node", "hostname"}, "scheduler.observed")
    if (
        obs["partition"] != PARTITION
        or _integer(obs["node_count"], "node_count", 1) != 1
        or _integer(obs["task_count"], "task_count", 1) != 1
        or _integer(obs["gpus_on_node"], "gpus_on_node", 1) != 1
    ):
        raise F1ValidationError("observed topology mismatch")
    job_id = _string(obs["job_id"], "job_id")
    node_list = _string(obs["node_list"], "node_list")
    hostname = _string(obs["hostname"], "hostname")
    if node_list != hostname:
        raise F1ValidationError("one-node allocation does not bind the observed hostname")
    started = _string(s["started_utc"], "started_utc")
    try:
        parsed = datetime.strptime(started, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise F1ValidationError("invalid scheduler start UTC") from exc
    command = _exact(s["scontrol"], {"argv", "exit_code", "stdout", "stderr"}, "scontrol")
    if command["exit_code"] != 0 or job_id not in command["stdout"]:
        raise F1ValidationError("scontrol observation mismatch")
    return job_id, parsed.strftime("%Y%m%dT%H%M%SZ")


def _validate_image(image: object, container: object, cleanup: object, attempt_id: str) -> None:
    i = _exact(image, {"schema_version", "requested_ref", "transport", "pull", "inspect"}, "image")
    if (
        i["schema_version"] != "bb.rl.f1.image-observation.v1"
        or i["requested_ref"] != IMAGE_REF
        or i["transport"] != "docker_registry"
    ):
        raise F1ValidationError("image/transport mismatch")
    if _exact(i["pull"], {"exit_code", "stdout", "stderr"}, "image.pull")["exit_code"] != 0:
        raise F1ValidationError("image pull failed")
    inspect = _exact(i["inspect"], {"id", "repo_digests", "os", "architecture"}, "image.inspect")
    digest = IMAGE_REF.split("@", 1)[1]
    if not _SHA256.fullmatch(_string(inspect["id"], "image.id").removeprefix("sha256:")):
        raise F1ValidationError("invalid image id")
    if not isinstance(inspect["repo_digests"], list) or not any(isinstance(x, str) and x.endswith("@" + digest) for x in inspect["repo_digests"]):
        raise F1ValidationError("requested RepoDigest absent")
    if inspect["os"] != "linux" or inspect["architecture"] != "amd64":
        raise F1ValidationError("wrong image platform")
    c = _exact(container, {"schema_version", "container_id", "name", "label", "image_id", "create_exit_code", "start_exit_code"}, "container")
    expected_label = f"bb.rl.f1.attempt={attempt_id}"
    if c["schema_version"] != "bb.rl.f1.container-observation.v1" or c["label"] != expected_label or c["image_id"] != inspect["id"] or c["create_exit_code"] != 0 or c["start_exit_code"] != 0:
        raise F1ValidationError("container identity/execution mismatch")
    if not _string(c["name"], "container.name").endswith(attempt_id):
        raise F1ValidationError("container name mismatch")
    clean = _exact(cleanup, {"schema_version", "remove_exit_code", "name_matches", "label_matches"}, "post-cleanup")
    if clean["schema_version"] != "bb.rl.f1.container-cleanup-observation.v1" or clean["remove_exit_code"] != 0 or clean["name_matches"] != [] or clean["label_matches"] != []:
        raise F1ValidationError("container cleanup residue")


def _validate_source_and_dependencies(raw: Mapping[str, bytes]) -> dict[str, Any]:
    source = _json(raw["source"], "source inventory")
    s = _exact(source, {"schema_version", "breadboard_head", "wrapper_head", "tree_sha256", "members"}, "source")
    if s["schema_version"] != "bb.rl.f1.source-bundle-inventory.v2" or not _GIT_HEAD.fullmatch(s["breadboard_head"]) or not _GIT_HEAD.fullmatch(s["wrapper_head"]):
        raise F1ValidationError("source identity invalid")
    if not isinstance(s["members"], list) or not s["members"]:
        raise F1ValidationError("source members missing")
    members: dict[str, Mapping[str, Any]] = {}
    tree_parts = []
    for index, item in enumerate(s["members"]):
        member = _exact(item, {"path", "size_bytes", "sha256"}, f"source.members[{index}]")
        path = _string(member["path"], "source path")
        if path in members or PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts:
            raise F1ValidationError("source member path invalid/duplicate")
        _integer(member["size_bytes"], "source size")
        if not _SHA256.fullmatch(_string(member["sha256"], "source sha256")):
            raise F1ValidationError("source member hash invalid")
        members[path] = member
        tree_parts.append(path.encode() + b"\0" + bytes.fromhex(member["sha256"]))
    if sha256_bytes(b"".join(tree_parts)) != s["tree_sha256"]:
        raise F1ValidationError("source tree hash mismatch")
    lock_member = members.get("scripts/rl_phase5/f1_requirements.lock")
    if lock_member is None or lock_member["size_bytes"] != len(raw["dependencies_lock"]) or lock_member["sha256"] != sha256_bytes(raw["dependencies_lock"]):
        raise F1ValidationError("dependency lock/source join mismatch")
    lock_text = raw["dependencies_lock"].decode("utf-8")
    requirement_lines = [line for line in lock_text.splitlines() if line and not line[0].isspace() and not line.startswith("#")]
    if not requirement_lines or any("==" not in line for line in requirement_lines) or "--hash=sha256:" not in lock_text:
        raise F1ValidationError("dependency lock is not exact/hash locked")
    check = _exact(_json(raw["dependencies_check"], "pip check"), {"schema_version", "argv", "exit_code", "stdout", "stderr"}, "pip check")
    if check["schema_version"] != "bb.rl.f1.command-observation.v1" or check["exit_code"] != 0:
        raise F1ValidationError("pip check failed")
    frozen = raw["dependencies_freeze"].decode("utf-8")
    if not frozen.strip() or any("==" not in line for line in frozen.splitlines() if line and not line.startswith("#")):
        raise F1ValidationError("pip freeze invalid")
    return dict(s)




def _artifact_ref_matches_body(
    value: object,
    body: object,
    where: str,
) -> Mapping[str, Any]:
    ref = _exact(
        value,
        {"artifact_id", "sha256", "size_bytes", "media_type", "metadata"},
        where,
    )
    payload = canonical_json_bytes(body)
    if (
        ref["sha256"] != "sha256:" + sha256_bytes(payload)
        or ref["size_bytes"] != len(payload)
        or not isinstance(ref["media_type"], str)
        or not ref["media_type"]
        or not isinstance(ref["metadata"], dict)
    ):
        raise F1ValidationError(f"{where}: immutable body identity mismatch")
    return ref

def _sha256_ref(value: object, where: str) -> str:
    digest = _string(value, where)
    if not digest.startswith("sha256:") or not _SHA256.fullmatch(
        digest.removeprefix("sha256:")
    ):
        raise F1ValidationError(f"{where}: invalid sha256 reference")
    return digest


def _validate_composition_inspect(
    value: object,
    manifest: Mapping[str, Any],
    manifest_digest: str,
) -> None:
    inspected = _exact(
        value,
        {
            "schema_version",
            "composition_id",
            "input_manifest_digest",
            "compiler_identity",
            "config_bundle_digest",
            "admitted_set_digest",
            "authority_bundle_digest",
            "admission_policy_digest",
            "receipt_algorithm",
            "receipt_key_id",
            "secret_handle_ids",
            "selector_digests",
            "evidence_authority_digest",
            "installed_authority_digest",
            "registry_snapshot_digest",
            "revocation_state_digests",
            "runner_registry_digest",
            "server_authority_digest",
            "store_authority_digests",
        },
        "composition inspect semantic",
    )

    def manifest_ref(container: object, key: str) -> str:
        if not isinstance(container, Mapping) or not isinstance(
            container.get(key), Mapping
        ):
            raise F1ValidationError(f"composition manifest missing {key}")
        return _sha256_ref(container[key].get("sha256"), key)

    control = manifest.get("control_plane")
    if not isinstance(control, Mapping):
        raise F1ValidationError("composition manifest control plane is invalid")
    receipt = control.get("receipt_authenticator")
    if not isinstance(receipt, Mapping):
        raise F1ValidationError("composition receipt authority is invalid")
    handles = manifest.get("secret_handles")
    records = handles.get("records") if isinstance(handles, Mapping) else None
    if not isinstance(records, list) or any(
        not isinstance(record, Mapping) for record in records
    ):
        raise F1ValidationError("composition secret handles are invalid")
    expected_handle_ids = sorted(
        _string(record.get("handle_id"), "secret handle id") for record in records
    )
    if len(expected_handle_ids) != len(set(expected_handle_ids)):
        raise F1ValidationError("composition secret handle is duplicated")
    catalog = manifest.get("selector_catalog")
    if not isinstance(catalog, Mapping):
        raise F1ValidationError("composition selector catalog is invalid")
    selector_refs: list[object] = []
    for kind in ("direct", "weighted"):
        refs = catalog.get(kind)
        if not isinstance(refs, list):
            raise F1ValidationError("composition selector catalog is invalid")
        selector_refs.extend(refs)
    expected_selector_digests = sorted(
        _sha256_ref(
            ref.get("sha256") if isinstance(ref, Mapping) else None,
            "selector digest",
        )
        for ref in selector_refs
    )
    stores = manifest.get("stores")
    if not isinstance(stores, Mapping):
        raise F1ValidationError("composition stores are invalid")
    expected_store_count = sum(
        isinstance(store, Mapping) and "authority_id" in store
        for store in stores.values()
    )

    if (
        inspected["schema_version"] != "bb.rl.harness-composed.v1"
        or inspected["composition_id"] != manifest.get("composition_id")
        or inspected["input_manifest_digest"] != manifest_digest
        or inspected["compiler_identity"] != control.get("compiler")
        or inspected["config_bundle_digest"]
        != manifest_ref(manifest, "config_bundle_ref")
        or inspected["admitted_set_digest"]
        != manifest_ref(manifest, "admitted_set_ref")
        or inspected["authority_bundle_digest"]
        != manifest_ref(manifest, "authority_bundle_ref")
        or inspected["admission_policy_digest"]
        != manifest_ref(control, "admission_policy_ref")
        or inspected["receipt_algorithm"] != receipt.get("algorithm")
        or inspected["receipt_key_id"] != receipt.get("key_id")
        or inspected["secret_handle_ids"] != expected_handle_ids
        or inspected["selector_digests"] != expected_selector_digests
    ):
        raise F1ValidationError("composition inspect semantic join mismatch")

    for field in (
        "evidence_authority_digest",
        "installed_authority_digest",
        "registry_snapshot_digest",
        "runner_registry_digest",
        "server_authority_digest",
    ):
        _sha256_ref(inspected[field], f"composition inspect {field}")
    for field, minimum, exact_count in (
        ("revocation_state_digests", 1, None),
        ("store_authority_digests", 1, expected_store_count),
    ):
        digests = inspected[field]
        if (
            not isinstance(digests, list)
            or len(digests) < minimum
            or (exact_count is not None and len(digests) != exact_count)
            or len(digests) != len(set(digests))
        ):
            raise F1ValidationError(
                f"composition inspect {field} inventory is invalid"
            )
        for digest in digests:
            _sha256_ref(digest, f"composition inspect {field}")

def _validate_lifecycle(raw: Mapping[str, bytes], source: Mapping[str, Any]) -> dict[str, Any]:
    resolved = _json(raw["resolver"], "resolver")
    r = _exact(resolved, {"schema_version", "requested_name", "resolved"}, "resolver")
    if r["schema_version"] != "bb.rl.f1.env-resolution-observation.v1" or r["requested_name"] != "breadboard":
        raise F1ValidationError("resolver request mismatch")
    result = _exact(
        r["resolved"],
        {
            "env",
            "kind",
            "agent_name",
            "config_paths",
            "required_env",
            "missing_required_env",
            "service_servers",
            "data_override",
        },
        "resolver.resolved",
    )
    if result != {
        "env": "breadboard",
        "kind": "agent",
        "agent_name": "breadboard",
        "config_paths": [
            "responses_api_agents/breadboard_agent/configs/breadboard.yaml"
        ],
        "required_env": ["BREADBOARD_HARNESS_BASE_URL"],
        "missing_required_env": [],
        "service_servers": ["policy_model", "{agent_name}"],
        "data_override": True,
    }:
        raise F1ValidationError("ENV=breadboard was not the sole generic agent config")
    members = {item["path"]: item for item in source["members"]}
    config_path = result["config_paths"][0]
    config_member = members.get(config_path)
    if config_member is None or config_member["size_bytes"] != len(raw["config"]) or config_member["sha256"] != sha256_bytes(raw["config"]):
        raise F1ValidationError("resolved config/source join mismatch")
    ref = _exact(
        _json(raw["composition_ref"], "composition reference observation"),
        {
            "schema_version",
            "composition_ref_sha256",
            "composition_ref_size_bytes",
            "ref_schema_version",
            "manifest_sha256",
            "manifest_size_bytes",
            "manifest_media_type",
            "manifest_path_disposition",
        },
        "composition reference observation",
    )
    manifest = _exact(
        _json(raw["composition_manifest"], "composition manifest observation"),
        {"schema_version", "raw_sha256", "raw_size_bytes", "semantic"},
        "composition manifest observation",
    )
    inspected = _exact(
        _json(raw["composition_inspect"], "composition inspect observation"),
        {
            "schema_version",
            "exit_code",
            "raw_stdout_sha256",
            "raw_stdout_size_bytes",
            "raw_stderr_sha256",
            "raw_stderr_size_bytes",
            "semantic",
        },
        "composition inspect observation",
    )
    if (
        ref["schema_version"]
        != "bb.rl.f1.composition-reference-observation.v1"
        or ref["ref_schema_version"] != "bb.rl.harness-composition-ref.v1"
        or ref["manifest_path_disposition"]
        != "absolute_runtime_path_omitted"
        or ref["manifest_media_type"]
        != "application/vnd.breadboard.harness-composition+json;version=1"
        or not _SHA256.fullmatch(ref["composition_ref_sha256"])
        or _integer(
            ref["composition_ref_size_bytes"],
            "composition reference size",
            1,
        )
        < 1
        or not isinstance(ref["manifest_sha256"], str)
        or not ref["manifest_sha256"].startswith("sha256:")
        or not _SHA256.fullmatch(ref["manifest_sha256"].removeprefix("sha256:"))
        or _integer(ref["manifest_size_bytes"], "composition manifest size", 1)
        < 1
    ):
        raise F1ValidationError("composition reference identity is invalid")
    if (
        manifest["schema_version"]
        != "bb.rl.f1.composition-manifest-observation.v1"
        or not _SHA256.fullmatch(manifest["raw_sha256"])
        or manifest["raw_sha256"]
        != ref["manifest_sha256"].removeprefix("sha256:")
        or manifest["raw_size_bytes"] != ref["manifest_size_bytes"]
        or not isinstance(manifest["semantic"], dict)
    ):
        raise F1ValidationError("composition manifest identity is invalid")
    if (
        inspected["schema_version"]
        != "bb.rl.f1.composition-inspect-observation.v1"
        or inspected["exit_code"] != 0
        or not _SHA256.fullmatch(inspected["raw_stdout_sha256"])
        or _integer(
            inspected["raw_stdout_size_bytes"],
            "composition inspect stdout size",
            1,
        )
        < 1
        or inspected["raw_stderr_sha256"] != sha256_bytes(b"")
        or inspected["raw_stderr_size_bytes"] != 0
        or raw["composition_inspect_stderr"] != b""
    ):
        raise F1ValidationError("composition inspect does not bind the manifest")
    semantic = manifest["semantic"]
    _validate_composition_inspect(
        inspected["semantic"], semantic, ref["manifest_sha256"]
    )
    _reject_absolute_paths(semantic, "composition semantic")
    if (
        "production-fixture-composition"
        not in _values(semantic, "composition_id")
        or {
            "api-auth",
            "policy-callback",
            "receipt-signing",
        }
        != {
            value
            for value in _values(semantic, "handle_id")
            if isinstance(value, str)
        }
    ):
        raise F1ValidationError("composition semantic authority mismatch")
    unauth = _exact(_json(raw["unauthenticated"], "unauthenticated response"), {"schema_version", "request", "status", "body"}, "unauthenticated")
    request = _exact(
        unauth["request"], {"method", "path"}, "unauthenticated.request"
    )
    if (
        unauth["schema_version"] != "bb.rl.f1.http-observation.v1"
        or request != {"method": "POST", "path": "/v2/episodes"}
        or unauth["status"] != 401
    ):
        raise F1ValidationError("unauthenticated admission mismatch")
    wrapper_request = _json(raw["wrapper_request"], "wrapper request")
    wrapper_response = _json(raw["wrapper_response"], "wrapper response")
    episode_values = _values(wrapper_request, "episode_id") + _values(wrapper_request, "request_id")
    episode_id = next((x for x in episode_values if isinstance(x, str) and x), None)
    if episode_id is None:
        raise F1ValidationError("wrapper request lacks episode/request identity")
    for key in ("episode_id", "request_id"):
        values = _values(wrapper_request, key)
        if values and any(v != episode_id for v in values):
            raise F1ValidationError("wrapper request identity mismatch")
    completed_refs = [
        value
        for value in _values(wrapper_response, "completed_envelope_ref")
        if isinstance(value, dict)
    ]
    closed_refs = [
        value
        for value in _values(wrapper_response, "closed_envelope_ref")
        if isinstance(value, dict)
    ]
    fingerprints = [
        value
        for value in _values(wrapper_response, "run_fingerprint")
        if isinstance(value, str) and value
    ]
    primary_dispositions = [
        value
        for value in _values(wrapper_response, "primary_disposition")
        if isinstance(value, str)
    ]
    cleanup_dispositions = [
        value
        for value in _values(wrapper_response, "cleanup_disposition")
        if isinstance(value, str)
    ]
    if (
        len({canonical_json_bytes(value) for value in completed_refs}) != 1
        or len({canonical_json_bytes(value) for value in closed_refs}) != 1
        or len(set(fingerprints)) != 1
        or "succeeded" not in primary_dispositions
        or "released" not in cleanup_dispositions
    ):
        raise F1ValidationError("wrapper carrier/disposition invalid")
    completed_ref = completed_refs[0]
    closed_ref = closed_refs[0]
    fingerprint = fingerprints[0]
    observed_bodies: dict[str, object] = {}
    expected_paths = {
        "status_response": f"/v2/episodes/{episode_id}",
        "completed_response": f"/v2/episodes/{episode_id}/envelopes/completed",
        "closed_response": f"/v2/episodes/{episode_id}/envelopes/closed",
    }
    for kind, expected_path in expected_paths.items():
        envelope = _exact(
            _json(raw[kind], kind),
            {"schema_version", "request", "status", "body"},
            kind,
        )
        request_observation = _exact(
            envelope["request"], {"method", "path"}, f"{kind}.request"
        )
        if (
            envelope["schema_version"] != "bb.rl.f1.http-observation.v1"
            or request_observation != {"method": "GET", "path": expected_path}
            or envelope["status"] != 200
            or not isinstance(envelope["body"], dict)
        ):
            raise F1ValidationError(f"{kind} transport mismatch")
        _require_join(envelope["body"], "episode_id", episode_id, kind)
        observed_bodies[kind] = envelope["body"]
    status_body = observed_bodies["status_response"]
    completed_body = observed_bodies["completed_response"]
    closed_body = observed_bodies["closed_response"]
    if (
        status_body.get("state") != "closed"
        or status_body.get("primary_disposition") != "succeeded"
        or status_body.get("cleanup_disposition") != "released"
        or status_body.get("run_fingerprint") != fingerprint
        or status_body.get("completed_envelope_ref") != completed_ref
        or status_body.get("closed_envelope_ref") != closed_ref
    ):
        raise F1ValidationError("closed status does not bind wrapper outcome")
    _artifact_ref_matches_body(
        completed_ref, completed_body, "completed envelope reference"
    )
    _artifact_ref_matches_body(
        closed_ref, closed_body, "closed envelope reference"
    )
    if (
        completed_body.get("run_fingerprint") != fingerprint
        or completed_body.get("primary_outcome") != "succeeded"
        or completed_body.get("cleanup_disposition") != "pending"
        or closed_body.get("completed_envelope_ref") != completed_ref
        or closed_body.get("primary_outcome") != "succeeded"
        or closed_body.get("cleanup_disposition") != "released"
    ):
        raise F1ValidationError("completed/closed evidence join mismatch")
    callbacks = _exact(
        _json(raw["callback"], "callbacks"),
        {"schema_version", "observations"},
        "callbacks",
    )
    if (
        callbacks["schema_version"]
        != "bb.rl.f1.callback-observations.v1"
        or not isinstance(callbacks["observations"], list)
        or len(callbacks["observations"]) != 2
    ):
        raise F1ValidationError("callback count mismatch")
    routes: list[str] = []
    request_digests: list[str] = []
    for index, item in enumerate(callbacks["observations"]):
        observation = _exact(
            item,
            {"path", "request_body_sha256"},
            f"callback[{index}]",
        )
        if not _SHA256.fullmatch(
            _string(
                observation["request_body_sha256"],
                f"callback[{index}].request_body_sha256",
            )
        ):
            raise F1ValidationError("callback observation digest invalid")
        routes.append(_string(observation["path"], f"callback[{index}].path"))
        request_digests.append(observation["request_body_sha256"])
    if routes != ["/v1/responses", "/v1/responses"] or len(
        set(request_digests)
    ) != 2:
        raise F1ValidationError("callback observations do not prove both legs")
    before = _exact(
        _json(raw["process_before"], "process before"),
        {"schema_version", "processes"},
        "process before",
    )
    if (
        before["schema_version"] != "bb.rl.f1.process-identities.v1"
        or not isinstance(before["processes"], list)
        or len(before["processes"]) != 1
    ):
        raise F1ValidationError("initial process identity is invalid")
    initial = _exact(
        before["processes"][0], {"role", "pid", "ppid"}, "process before[0]"
    )
    pid = _integer(initial["pid"], "harness pid", 1)
    _integer(initial["ppid"], "harness parent pid", 1)
    if initial["role"] != "harness":
        raise F1ValidationError("initial process role mismatch")
    after = _exact(
        _json(raw["process_after"], "process after"),
        {"schema_version", "processes"},
        "process after",
    )
    if (
        after["schema_version"] != "bb.rl.f1.process-probes.v2"
        or not isinstance(after["processes"], list)
        or len(after["processes"]) != 1
    ):
        raise F1ValidationError("final process observation is invalid")
    final = _exact(
        after["processes"][0],
        {"role", "pid", "returncode", "probe_errno"},
        "process after[0]",
    )
    if (
        final["role"] != "harness"
        or final["pid"] != pid
        or final["returncode"] != 0
        or final["probe_errno"] != 3
    ):
        raise F1ValidationError("harness process cleanup mismatch")
    cleanup = _exact(
        _json(raw["private_cleanup"], "private cleanup"),
        {"schema_version", "observations"},
        "private cleanup",
    )
    if (
        cleanup["schema_version"]
        != "bb.rl.f1.private-cleanup-observation.v1"
        or not isinstance(cleanup["observations"], list)
        or not cleanup["observations"]
    ):
        raise F1ValidationError("private cleanup observation is invalid")
    for index, item in enumerate(cleanup["observations"]):
        observation = _exact(
            item,
            {"relative_path", "lstat_errno"},
            f"private cleanup[{index}]",
        )
        relative = _string(
            observation["relative_path"], f"private cleanup[{index}].relative_path"
        )
        if (
            PurePosixPath(relative).is_absolute()
            or ".." in PurePosixPath(relative).parts
            or observation["lstat_errno"] != 2
        ):
            raise F1ValidationError("private cleanup residue")
    return {
        "episode_id": episode_id,
        "completed_envelope_ref": completed_ref,
        "closed_envelope_ref": closed_ref,
        "run_fingerprint": fingerprint,
        "disposition": "succeeded",
        "cleanup_disposition": "released",
        "callback_routes": routes,
        "composition_ref_sha256": ref["composition_ref_sha256"],
        "composition_manifest_sha256": manifest["raw_sha256"],
    }


def validate_scratch(attempt_dir: Path, *, secret_material: Iterable[bytes] = ()) -> dict[str, Any]:
    attempt_path = Path(attempt_dir)
    if attempt_path.is_symlink():
        raise F1ValidationError("invalid attempt directory")
    attempt = attempt_path.resolve(strict=True)
    base_inventory = {
        "attempt.json",
        "target.stdout",
        "target.stderr",
        "exit_code",
        "result.tar.gz",
        "runner-result.tar.gz",
        "artifacts",
        "runner",
        "outer",
    }
    actual_inventory = {path.name for path in attempt.iterdir()}
    if actual_inventory not in (
        base_inventory,
        base_inventory | {"F1_PREFLIGHT_REPORT.json"},
    ) or any(path.is_symlink() for path in attempt.rglob("*")):
        raise F1ValidationError("F1 attempt inventory is not exact")
    attempt_record = _exact(
        _json((attempt / "attempt.json").read_bytes(), "attempt record"),
        {
            "schema_version",
            "attempt_id",
            "outer_target_run_id",
            "outer_slurm_job_id",
            "outer_node",
        },
        "attempt record",
    )
    attempt_id = _string(attempt_record["attempt_id"], "attempt id")
    if (
        attempt_record["schema_version"]
        != "bb.rl.f1.phase3-ingested-attempt.v1"
        or not _ATTEMPT.fullmatch(attempt_id)
    ):
        raise F1ValidationError("attempt record identity mismatch")
    for archive_name in ("result.tar.gz", "runner-result.tar.gz"):
        archive_path = attempt / archive_name
        if not archive_path.is_file() or archive_path.is_symlink():
            raise F1ValidationError(f"missing transport archive: {archive_name}")
    stdout = (attempt / "target.stdout").read_bytes()
    stderr = (attempt / "target.stderr").read_bytes()
    exit_code_text = (attempt / "exit_code").read_text("ascii").strip()
    if exit_code_text != "0":
        raise F1ValidationError("target did not exit successfully")
    markers = parse_artifact_markers(stdout, attempt_id)
    raw = _artifact_bytes(attempt, markers)
    runner_dir = attempt / "runner"
    if {p.name for p in runner_dir.iterdir()} != RUNNER_ARTIFACTS or any(not p.is_file() or p.is_symlink() for p in runner_dir.iterdir()):
        raise F1ValidationError("runner inventory mismatch")
    scheduler = _runner_json(attempt, "scheduler.json")
    image = _runner_json(attempt, "image-inspect.json")
    container = _runner_json(attempt, "container-inspect.json")
    cleanup = _runner_json(attempt, "post-cleanup.json")
    job_id, _ = _validate_scheduler(scheduler)
    if (
        attempt_record["outer_slurm_job_id"] != job_id
        or attempt_record["outer_node"] != scheduler["observed"]["hostname"]
    ):
        raise F1ValidationError("attempt record does not bind scheduler identity")
    outer_inventory, target_run_id = _validate_outer_phase3(
        attempt, attempt_id, scheduler, attempt_record
    )
    _validate_image(image, container, cleanup, attempt_id)
    source = _validate_source_and_dependencies(raw)
    lifecycle = _validate_lifecycle(raw, source)
    for kind in ("source", "unauthenticated", "wrapper_request", "wrapper_response", "status_response", "completed_response", "closed_response", "callback", "process_before", "process_after", "private_cleanup"):
        _reject_sensitive_structured(_json(raw[kind], kind, canonical=kind == "source" or kind not in {"wrapper_request", "wrapper_response"}), kind)
    _scan_roots((attempt,), secret_material)
    artifacts = [{k: m[k] for k in ("kind", "artifact_path", "size_bytes", "sha256")} for m in markers]
    runner_inventory = [{"path": p.name, "size_bytes": p.stat().st_size, "sha256": sha256_bytes(p.read_bytes())} for p in sorted(runner_dir.iterdir())]
    transport_archives = [
        {
            "path": name,
            "size_bytes": (attempt / name).stat().st_size,
            "sha256": sha256_bytes((attempt / name).read_bytes()),
        }
        for name in ("result.tar.gz", "runner-result.tar.gz")
    ]
    return {
        "schema_version": REPORT_SCHEMA,
        "attempt_id": attempt_id,
        "target_run_id": target_run_id,
        "scheduler": {"job_id": job_id, "started_utc": scheduler["started_utc"], "hostname": scheduler["observed"]["hostname"]},
        "image": {"requested_ref": IMAGE_REF, "image_id": image["inspect"]["id"], "os": image["inspect"]["os"], "architecture": image["inspect"]["architecture"]},
        "source": {"breadboard_head": source["breadboard_head"], "wrapper_head": source["wrapper_head"], "tree_sha256": source["tree_sha256"]},
        "lifecycle": lifecycle,
        "artifacts": artifacts,
        "runner_artifacts": runner_inventory,
        "outer_artifacts": outer_inventory,
        "transport_archives": transport_archives,
        "raw_stdout": {"size_bytes": len(stdout), "sha256": sha256_bytes(stdout)},
        "raw_stderr": {"size_bytes": len(stderr), "sha256": sha256_bytes(stderr)},
        "training_executed": False,
        "validation_invariants": [
            "actual_raw_artifact_joins",
            "authenticated_wrapper_lifecycle",
            "exact_immutable_container",
            "one_node_phase3_slurm_execution",
            "orphan_free_cleanup",
            "seeded_secret_target_scan_and_local_structural_scan",
            "sole_generic_config_resolution",
        ],
        "validation_code": _validation_code_inventory(),
        "claim_boundary": CLAIM_BOUNDARY,
    }


def _fsync_tree(root: Path) -> None:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            with path.open("rb") as stream:
                os.fsync(stream.fileno())
    for path in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        fd = os.open(path, os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    fd = os.open(root, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def verify_canonical(path: Path, *, secret_material: Iterable[bytes] = ()) -> dict[str, Any]:
    canonical_path = Path(path)
    if canonical_path.is_symlink():
        raise F1ValidationError("canonical path must not be a symlink")
    root = canonical_path.resolve(strict=True)
    report_raw = (root / "F1_PREFLIGHT_REPORT.json").read_bytes()
    report = _json(report_raw, "canonical report")
    expected_top = {
        "F1_PREFLIGHT_REPORT.json",
        "attempt.json",
        "target.stdout",
        "target.stderr",
        "exit_code",
        "result.tar.gz",
        "runner-result.tar.gz",
        "artifacts",
        "runner",
        "outer",
    }
    if {p.name for p in root.iterdir()} != expected_top or any(p.is_symlink() for p in root.rglob("*")):
        raise F1ValidationError("canonical inventory mismatch")
    scratch_report = validate_scratch(root, secret_material=secret_material)
    if report != scratch_report:
        raise F1ValidationError("canonical report/content mismatch")
    _scan_roots((root,), secret_material)
    return report


def promote(attempt_dir: Path, canonical_root: Path, *, secret_material: Iterable[bytes] = ()) -> Path:
    report = validate_scratch(attempt_dir, secret_material=secret_material)
    canonical_root = Path(canonical_root)
    canonical_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = canonical_root / report["target_run_id"]
    if destination.exists():
        raise FileExistsError(destination)
    staging = Path(tempfile.mkdtemp(prefix=".f1-promote-", dir=canonical_root))
    try:
        for name in (
            "attempt.json",
            "target.stdout",
            "target.stderr",
            "exit_code",
            "result.tar.gz",
            "runner-result.tar.gz",
        ):
            shutil.copyfile(Path(attempt_dir) / name, staging / name)
        shutil.copytree(Path(attempt_dir) / "artifacts", staging / "artifacts")
        shutil.copytree(Path(attempt_dir) / "runner", staging / "runner")
        shutil.copytree(Path(attempt_dir) / "outer", staging / "outer")
        (staging / "F1_PREFLIGHT_REPORT.json").write_bytes(canonical_json_bytes(report))
        _fsync_tree(staging)
        verify_canonical(staging, secret_material=secret_material)
        os.replace(staging, destination)
        parent_fd = os.open(canonical_root, os.O_RDONLY)
        try: os.fsync(parent_fd)
        finally: os.close(parent_fd)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    verify_canonical(destination, secret_material=secret_material)
    return destination
