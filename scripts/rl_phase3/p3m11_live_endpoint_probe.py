from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

TARGET_RUN_ID = os.environ.get("PHASE3_TARGET_RUN_ID", "20260624T040000Z-slurm-243958")
COMMAND_ID = os.environ.get("PHASE3_COMMAND_ID", "phase3_p3m11_live_endpoint_probe")
OUT = Path("p3m11_live_endpoint_probe_output").resolve()
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("PHASE3_P3M11_REQUEST_TIMEOUT_SECONDS", "10"))
LOCAL_OBJECT_STORE_BACKENDS = frozenset(
    {"LocalObjectStore", "target_workspace_local_object_store", "local_object_store"}
)


def run(argv: list[str]) -> dict[str, Any]:
    try:
        proc = subprocess.run(argv, text=True, capture_output=True, timeout=30, check=False)
        return {
            "argv": argv,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except Exception as exc:  # noqa: BLE001 - target probe captures tool availability.
        return {
            "argv": argv,
            "returncode": None,
            "stdout": "",
            "stderr": repr(exc),
            "exception": exc.__class__.__name__,
        }


def present(name: str) -> bool:
    return bool(os.environ.get(name))


def _source_sha256() -> str:
    try:
        return "sha256:" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        return "sha256:unavailable"


def _redacted_url(value: str | None) -> str:
    if not value:
        return ""
    parsed = urllib.parse.urlsplit(value)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path, "", ""))

def _endpoint_is_local(value: str | None) -> bool:
    if not value:
        return False
    text = value.strip()
    parsed = urllib.parse.urlsplit(text)
    if not parsed.hostname and "://" not in text:
        parsed = urllib.parse.urlsplit(f"//{text}")
    host = parsed.hostname
    if not host:
        return False
    normalized = host.lower().rstrip(".")
    if normalized in {"localhost", "localhost.localdomain"}:
        return True
    local_names = {
        str(os.environ.get("HOSTNAME") or "").lower().rstrip("."),
        str(os.environ.get("SLURMD_NODENAME") or "").lower().rstrip("."),
    }
    if normalized in local_names - {""}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _join_url(base: str, path: str) -> str:
    return urllib.parse.urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def _configured_probe_url(prefix: str) -> tuple[str | None, list[str]]:
    full = os.environ.get(f"{prefix}_PROBE_URL")
    if full:
        return full, []
    base = os.environ.get(f"{prefix}_BASE_URL")
    path = os.environ.get(f"{prefix}_PROBE_PATH")
    missing: list[str] = []
    if not base:
        missing.append(f"{prefix}_BASE_URL")
    if not path:
        missing.append(f"{prefix}_PROBE_PATH")
    if missing:
        return None, missing
    return _join_url(base or "", path or ""), []


def _parse_ok_status(prefix: str) -> set[int]:
    raw = os.environ.get(f"{prefix}_OK_STATUS", "200-299")
    statuses: set[int] = set()
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item:
            continue
        if "-" in item:
            low, high = item.split("-", 1)
            statuses.update(range(int(low), int(high) + 1))
        else:
            statuses.add(int(item))
    return statuses or set(range(200, 300))


def _token_headers(prefix: str) -> dict[str, str]:
    token = os.environ.get(f"{prefix}_TOKEN") or ""
    if not token:
        return {}
    header = os.environ.get(f"{prefix}_TOKEN_HEADER", "Authorization")
    scheme = os.environ.get(f"{prefix}_TOKEN_SCHEME", "Bearer")
    value = f"{scheme} {token}" if scheme else token
    return {header: value}


def _body(prefix: str) -> bytes | None:
    if f"{prefix}_PROBE_BODY_JSON" in os.environ:
        return os.environ[f"{prefix}_PROBE_BODY_JSON"].encode("utf-8")
    if f"{prefix}_PROBE_BODY" in os.environ:
        return os.environ[f"{prefix}_PROBE_BODY"].encode("utf-8")
    return None


def _request(
    *,
    prefix: str,
    url: str,
    method: str,
    body: bytes | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    headers = {"User-Agent": "breadboard-phase3-p3m11-probe"}
    headers.update(_token_headers(prefix))
    headers.update(extra_headers or {})
    if body is not None:
        headers.setdefault("Content-Type", "application/json")
    started = time.perf_counter()
    try:
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310 - explicit target endpoint probe.
            payload = response.read()
            status = int(response.status)
        latency = time.perf_counter() - started
        return {
            "ok": status in _parse_ok_status(prefix),
            "status": status,
            "latency_seconds": latency,
            "body_sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "body_bytes": len(payload),
            "body": payload,
            "endpoint": _redacted_url(url),
        }
    except urllib.error.HTTPError as exc:
        latency = time.perf_counter() - started
        return {
            "ok": False,
            "status": int(exc.code),
            "latency_seconds": latency,
            "body_sha256": "sha256:" + hashlib.sha256(exc.read()).hexdigest(),
            "endpoint": _redacted_url(url),
            "error": "HTTPError",
        }
    except Exception as exc:  # noqa: BLE001 - report blocked target behavior without secrets.
        latency = time.perf_counter() - started
        return {
            "ok": False,
            "status": None,
            "latency_seconds": latency,
            "endpoint": _redacted_url(url),
            "error": exc.__class__.__name__,
        }


def collect_verifier_metrics() -> tuple[dict[str, Any], list[str]]:
    prefix = "BREADBOARD_VERIFIER"
    url, missing = _configured_probe_url(prefix)
    token_present = present("BREADBOARD_VERIFIER_TOKEN")
    errors = list(missing)
    if not token_present:
        errors.append("BREADBOARD_VERIFIER_TOKEN")
    result: dict[str, Any] = {}
    endpoint_is_local = _endpoint_is_local(url)
    if endpoint_is_local:
        errors.append("verifier_endpoint_is_local")
    if url and token_present and not endpoint_is_local:
        method = os.environ.get("BREADBOARD_VERIFIER_PROBE_METHOD", "GET").upper()
        result = _request(prefix=prefix, url=url, method=method, body=_body(prefix))
        if not result.get("ok"):
            errors.append("verifier_probe_http_not_ready")
    elif not endpoint_is_local:
        errors.append("verifier_probe_not_configured")
    latency = [result["latency_seconds"]] if result.get("ok") else None
    return {
        "source": "verifier_client",
        "verifier_latency_seconds": latency,
        "env_presence": {
            "BREADBOARD_VERIFIER_BASE_URL": present("BREADBOARD_VERIFIER_BASE_URL"),
            "BREADBOARD_VERIFIER_TOKEN": token_present,
            "BREADBOARD_VERIFIER_PROBE_URL": present("BREADBOARD_VERIFIER_PROBE_URL"),
            "BREADBOARD_VERIFIER_PROBE_PATH": present("BREADBOARD_VERIFIER_PROBE_PATH"),
        },
        "endpoint": result.get("endpoint") or _redacted_url(url),
        "http_status": result.get("status"),
        "latency_seconds": result.get("latency_seconds"),
        "errors": errors,
    }, errors


def _object_url_from_template(env_name: str, *, bucket: str, key: str) -> tuple[str | None, list[str]]:
    template = os.environ.get(env_name)
    if not template:
        return None, [env_name]
    quoted_bucket = urllib.parse.quote(bucket, safe="")
    quoted_key = urllib.parse.quote(key, safe="/")
    try:
        return template.format(bucket=quoted_bucket, key=quoted_key), []
    except KeyError as exc:
        return None, [f"{env_name}_missing_placeholder_{exc.args[0]}"]


def collect_object_store_metrics() -> tuple[dict[str, Any], list[str]]:
    prefix = "BREADBOARD_OBJECT_STORE"
    bucket = os.environ.get("BREADBOARD_OBJECT_STORE_BUCKET") or ""
    token_present = present("BREADBOARD_OBJECT_STORE_TOKEN")
    errors: list[str] = []
    for key in ("BREADBOARD_OBJECT_STORE_BASE_URL", "BREADBOARD_OBJECT_STORE_BUCKET", "BREADBOARD_OBJECT_STORE_TOKEN"):
        if not present(key):
            errors.append(key)
    base_url = os.environ.get("BREADBOARD_OBJECT_STORE_BASE_URL")
    object_key = f"phase3/p3m11/{TARGET_RUN_ID}/{COMMAND_ID}/{os.environ.get('SLURM_JOB_ID', 'no-slurm')}.txt"
    put_url, put_missing = _object_url_from_template("BREADBOARD_OBJECT_STORE_PUT_URL_TEMPLATE", bucket=bucket, key=object_key)
    get_url, get_missing = _object_url_from_template("BREADBOARD_OBJECT_STORE_GET_URL_TEMPLATE", bucket=bucket, key=object_key)
    delete_template = os.environ.get("BREADBOARD_OBJECT_STORE_DELETE_URL_TEMPLATE")
    delete_url: str | None = None
    if delete_template:
        delete_url, delete_missing = _object_url_from_template("BREADBOARD_OBJECT_STORE_DELETE_URL_TEMPLATE", bucket=bucket, key=object_key)
        errors.extend(delete_missing)
    errors.extend(put_missing)
    errors.extend(get_missing)
    local_endpoint_errors = [
        f"object_store_{name}_endpoint_is_local"
        for name, url in (("base", base_url), ("put", put_url), ("get", get_url), ("delete", delete_url))
        if _endpoint_is_local(url)
    ]
    errors.extend(local_endpoint_errors)
    payload = f"breadboard-p3m11-object-store-probe\n{TARGET_RUN_ID}\n{COMMAND_ID}\n".encode("utf-8")
    payload_sha256 = "sha256:" + hashlib.sha256(payload).hexdigest()
    readback_body_sha256 = ""
    put_result: dict[str, Any] = {}
    get_result: dict[str, Any] = {}
    delete_result: dict[str, Any] = {}
    verified = False
    if put_url and get_url and bucket and token_present and not local_endpoint_errors:
        put_method = os.environ.get("BREADBOARD_OBJECT_STORE_PUT_METHOD", "PUT").upper()
        get_method = os.environ.get("BREADBOARD_OBJECT_STORE_GET_METHOD", "GET").upper()
        put_result = _request(prefix=prefix, url=put_url, method=put_method, body=payload, extra_headers={"Content-Type": "text/plain"})
        if put_result.get("ok"):
            get_result = _request(prefix=prefix, url=get_url, method=get_method)
            readback_body = get_result.get("body")
            if isinstance(readback_body, bytes):
                readback_body_sha256 = "sha256:" + hashlib.sha256(readback_body).hexdigest()
            verified = bool(get_result.get("ok") and readback_body == payload)
        if not put_result.get("ok"):
            errors.append("object_store_put_failed")
        elif not verified:
            errors.append("object_store_readback_mismatch")
        if delete_url:
            delete_method = os.environ.get("BREADBOARD_OBJECT_STORE_DELETE_METHOD", "DELETE").upper()
            delete_result = _request(prefix=prefix, url=delete_url, method=delete_method)
    elif not local_endpoint_errors:
        errors.append("object_store_probe_not_configured")
    backend = os.environ.get("BREADBOARD_OBJECT_STORE_BACKEND", "configured_http_object_store") if verified else "target_workspace_local_object_store"
    if backend in LOCAL_OBJECT_STORE_BACKENDS:
        errors.append("object_store_backend_is_local")
    return {
        "source": "object_store",
        "object_store": backend,
        "object_store_writes": 1 if verified else 0,
        "artifact_bytes": len(payload) if verified else 0,
        "artifact_sha256": payload_sha256 if verified else "",
        "written_sha256": payload_sha256 if verified else "",
        "readback_sha256": readback_body_sha256 if verified else "",
        "env_presence": {
            "BREADBOARD_OBJECT_STORE_BASE_URL": present("BREADBOARD_OBJECT_STORE_BASE_URL"),
            "BREADBOARD_OBJECT_STORE_BUCKET": bool(bucket),
            "BREADBOARD_OBJECT_STORE_TOKEN": token_present,
            "BREADBOARD_OBJECT_STORE_PUT_URL_TEMPLATE": present("BREADBOARD_OBJECT_STORE_PUT_URL_TEMPLATE"),
            "BREADBOARD_OBJECT_STORE_GET_URL_TEMPLATE": present("BREADBOARD_OBJECT_STORE_GET_URL_TEMPLATE"),
            "BREADBOARD_OBJECT_STORE_DELETE_URL_TEMPLATE": present("BREADBOARD_OBJECT_STORE_DELETE_URL_TEMPLATE"),
        },
        "endpoint": _redacted_url(base_url),
        "put_endpoint": _redacted_url(put_url),
        "get_endpoint": _redacted_url(get_url),
        "delete_endpoint": _redacted_url(delete_url),
        "object_key_sha256": "sha256:" + hashlib.sha256(object_key.encode("utf-8")).hexdigest(),
        "put_status": put_result.get("status"),
        "get_status": get_result.get("status"),
        "delete_status": delete_result.get("status"),
        "write_read_verified": verified,
        "errors": errors,
    }, errors


def collect_scheduler_metrics() -> tuple[dict[str, Any], list[str]]:
    prefix = "BREADBOARD_SCHEDULER"
    url, missing = _configured_probe_url(prefix)
    token_present = present("BREADBOARD_SCHEDULER_TOKEN")
    errors = list(missing)
    if not token_present:
        errors.append("BREADBOARD_SCHEDULER_TOKEN")
    result: dict[str, Any] = {}
    endpoint_is_local = _endpoint_is_local(url)
    if endpoint_is_local:
        errors.append("scheduler_endpoint_is_local")
    if url and token_present and not endpoint_is_local:
        method = os.environ.get("BREADBOARD_SCHEDULER_PROBE_METHOD", "GET").upper()
        result = _request(prefix=prefix, url=url, method=method, body=_body(prefix))
        if not result.get("ok"):
            errors.append("scheduler_probe_http_not_ready")
    elif not endpoint_is_local:
        errors.append("scheduler_probe_not_configured")
    ready = bool(result.get("ok"))
    return {
        "source": "scheduler_control",
        "scheduler_control": {
            "endpoint_present": bool(url),
            "token_present": token_present,
            "status": "ready" if ready else "blocked_missing_endpoint_or_token",
            "endpoint": result.get("endpoint") or _redacted_url(url),
            "http_status": result.get("status"),
            "latency_seconds": result.get("latency_seconds"),
        },
        "env_presence": {
            "BREADBOARD_SCHEDULER_BASE_URL": present("BREADBOARD_SCHEDULER_BASE_URL"),
            "BREADBOARD_SCHEDULER_TOKEN": token_present,
            "BREADBOARD_SCHEDULER_PROBE_URL": present("BREADBOARD_SCHEDULER_PROBE_URL"),
            "BREADBOARD_SCHEDULER_PROBE_PATH": present("BREADBOARD_SCHEDULER_PROBE_PATH"),
        },
        "errors": errors,
    }, errors


def _write_artifacts(artifacts: dict[str, dict[str, Any]]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, payload in artifacts.items():
        path = OUT / name
        safe_payload = dict(payload)
        safe_payload.pop("body", None)
        path.write_text(json.dumps(safe_payload, sort_keys=True, indent=2) + "\n")


def main() -> int:
    slurm_job_id = os.environ.get("SLURM_JOB_ID", "")
    node = os.environ.get("SLURMD_NODENAME") or os.environ.get("HOSTNAME", "")
    slurm_metrics: dict[str, Any] = {
        "source": "slurm_sacct",
        "job_id": slurm_job_id,
        "node": node,
        "sacct_stdout": run([
            "sacct",
            "-j",
            slurm_job_id,
            "--parsable2",
            "--noheader",
            "--format=JobID,JobName,Partition,AllocTRES,State,Elapsed,NodeList",
        ]) if slurm_job_id else {},
        "scheduler_retry_count": 0,
        "queue_wait_seconds": 0.0,
    }
    gpu_metrics: dict[str, Any] = {
        "source": "rocm_smi",
        "node": node,
        "rocm_smi_stdout": run(["/opt/rocm/bin/rocm-smi", "--showuse", "--showproductname"]),
        "gpu_utilization": {f"card{index}": 0 for index in range(8)},
    }
    verifier_metrics, verifier_errors = collect_verifier_metrics()
    object_store_metrics, object_store_errors = collect_object_store_metrics()
    scheduler_metrics, scheduler_errors = collect_scheduler_metrics()
    service_metrics: dict[str, Any] = {
        "source": "service_event_log",
        "events": ["p3m11_live_endpoint_probe"],
        "task_throughput": 1,
        "failure_taxonomy": {
            "verifier": verifier_errors,
            "object_store": object_store_errors,
            "scheduler": scheduler_errors,
        },
    }
    budget_caps: dict[str, Any] = {"source": "target_probe", "remaining_usd": 0.0}
    artifacts: dict[str, dict[str, Any]] = {
        "slurm_metrics.json": slurm_metrics,
        "gpu_metrics.json": gpu_metrics,
        "verifier_metrics.json": verifier_metrics,
        "service_metrics.json": service_metrics,
        "object_store_metrics.json": object_store_metrics,
        "scheduler_metrics.json": scheduler_metrics,
        "budget_caps.json": budget_caps,
    }
    _write_artifacts(artifacts)
    blockers: list[str] = []
    if verifier_errors:
        blockers.append("verifier_latency_unavailable")
    if object_store_errors:
        blockers.append("production_object_store_write_read_unavailable")
    if scheduler_errors:
        blockers.append("scheduler_control_unavailable")
    passed = not blockers
    report = {
        "schema_version": "bb.rl.phase3.component_report.v1",
        "report_id": "p3m11_live_endpoint_probe",
        "milestone_id": "P3-M11",
        "component": "observability_scheduler_store",
        "claim_boundary": "phase3_live_observability_object_store_scheduler_scope" if passed else "phase3_observability_scheduler_store_blocked_scope",
        "target_run_id": TARGET_RUN_ID,
        "command_id": COMMAND_ID,
        "points": 80,
        "passed": passed,
        "blocked_reason": ";".join(blockers),
        "artifact_paths": {key.removesuffix(".json"): str(OUT / key) for key in artifacts},
        "artifact_payloads": {key.removesuffix(".json"): {k: v for k, v in payload.items() if k != "body"} for key, payload in artifacts.items()},
        "input_hashes": {"probe_source": _source_sha256()},
        "required_artifact_keys": [key.removesuffix(".json") for key in artifacts],
        "scorecard_update_allowed": False,
    }
    print("PHASE3_COMPONENT_REPORT_JSON=" + json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
