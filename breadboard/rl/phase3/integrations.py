from __future__ import annotations

import hashlib
import http.client
import json
import os
import ipaddress
import socket
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence
from pathlib import Path

from breadboard.rl.phase3.evidence import sha256_file


@dataclass(frozen=True)
class VerifierCallEvidence:
    provider: str
    url: str
    request_sha256: str
    response_sha256: str
    status_code: int
    latency_seconds: float
    passed: bool
    blocked_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _sha(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _provider_env(provider: Literal["ors", "openreward"]) -> tuple[str, str]:
    if provider == "ors":
        return os.environ.get("BREADBOARD_ORS_BASE_URL", ""), os.environ.get("BREADBOARD_ORS_TOKEN", "")
    return os.environ.get("BREADBOARD_OPENREWARD_BASE_URL", ""), os.environ.get("BREADBOARD_OPENREWARD_TOKEN", "")

def _provider_url_block_reason(base_url: str) -> str:
    block_reason, _addresses = _provider_pinned_addresses(base_url)
    return block_reason


def _provider_pinned_addresses(base_url: str) -> tuple[str, tuple[str, ...]]:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "provider_endpoint_must_be_https", ()
    host = parsed.hostname or ""
    lowered = host.lower().rstrip(".")
    if lowered in {"localhost", "localhost.localdomain"} or lowered.endswith(".localhost"):
        return "provider_endpoint_must_not_be_loopback_or_private", ()
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        try:
            resolved = socket.getaddrinfo(lowered, parsed.port or 443, type=socket.SOCK_STREAM)
        except OSError:
            return "provider_endpoint_dns_resolution_failed", ()
        addresses: list[str] = []
        for result in resolved:
            address = ipaddress.ip_address(result[4][0])
            if not address.is_global:
                return "provider_endpoint_must_not_be_loopback_or_private", ()
            address_text = str(address)
            if address_text not in addresses:
                addresses.append(address_text)
        if not addresses:
            return "provider_endpoint_dns_resolution_failed", ()
        return "", tuple(addresses)
    if not address.is_global:
        return "provider_endpoint_must_not_be_loopback_or_private", ()
    return "", (str(address),)

def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlparse(url)
    port = parsed.port
    if port is None and parsed.scheme == "https":
        port = 443
    elif port is None and parsed.scheme == "http":
        port = 80
    return parsed.scheme, (parsed.hostname or "").lower().rstrip("."), port

def _origin_key(url: str) -> tuple[str, int]:
    _scheme, host, port = _origin(url)
    return host, port or 443


class ProviderRedirectBlocked(RuntimeError):
    def __init__(self, block_reason: str) -> None:
        self.block_reason = block_reason
        super().__init__(block_reason)


class _NoUnsafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, pinned_addresses: dict[tuple[str, int], tuple[str, ...]] | None = None) -> None:
        self._pinned_addresses = pinned_addresses if pinned_addresses is not None else {}

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if _origin(req.full_url) != _origin(newurl):
            raise ProviderRedirectBlocked("provider_redirect_cross_origin_blocked")
        raise ProviderRedirectBlocked("provider_redirect_not_followed")


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, pinned_addresses: Mapping[tuple[str, int], tuple[str, ...]], **kwargs: Any) -> None:
        super().__init__(host, **kwargs)
        self._pinned_addresses = pinned_addresses

    def connect(self) -> None:
        addresses = self._pinned_addresses.get((self.host.lower().rstrip("."), self.port))
        if not addresses:
            raise ProviderRedirectBlocked("provider_endpoint_dns_resolution_failed")
        last_error: OSError | None = None
        for address in addresses:
            try:
                sock = socket.create_connection((address, self.port), self.timeout, self.source_address)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ProviderRedirectBlocked("provider_endpoint_dns_resolution_failed")


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, pinned_addresses: Mapping[tuple[str, int], tuple[str, ...]]) -> None:
        super().__init__()
        self._pinned_addresses = pinned_addresses

    def https_open(self, req):  # type: ignore[no-untyped-def]
        return self.do_open(
            lambda host, **kwargs: _PinnedHTTPSConnection(host, self._pinned_addresses, **kwargs),
            req,
        )


def _open_without_proxies(request: urllib.request.Request, *, timeout_s: float):
    block_reason, addresses = _provider_pinned_addresses(request.full_url)
    if block_reason:
        raise ProviderRedirectBlocked(block_reason)
    pinned_addresses = {_origin_key(request.full_url): addresses}
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoUnsafeRedirectHandler(pinned_addresses),
        _PinnedHTTPSHandler(pinned_addresses),
    )
    return opener.open(request, timeout=timeout_s)



def call_ors_openreward(payload: Mapping[str, Any], *, provider: Literal["ors", "openreward"], timeout_s: float) -> VerifierCallEvidence:
    base_url, token = _provider_env(provider)
    body = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode()
    if not base_url or not token:
        return VerifierCallEvidence(provider, base_url, _sha(body), "", 0, 0.0, False, "missing_live_provider_credentials")
    block_reason = _provider_url_block_reason(base_url)
    if block_reason:
        return VerifierCallEvidence(provider, base_url, _sha(body), "", 0, 0.0, False, block_reason)
    request = urllib.request.Request(base_url.rstrip("/") + "/verify", data=body, method="POST", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    start = time.monotonic()
    try:
        with _open_without_proxies(request, timeout_s=timeout_s) as response:  # noqa: S310 - URL is operator-configured provider endpoint.
            response_body = response.read()
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        response_body = exc.read()
        status = int(exc.code)
        return VerifierCallEvidence(provider, base_url, _sha(body), _sha(response_body), status, time.monotonic() - start, False)
    except ProviderRedirectBlocked as exc:
        return VerifierCallEvidence(provider, base_url, _sha(body), "", 0, time.monotonic() - start, False, exc.block_reason)
    except Exception as exc:  # noqa: BLE001
        return VerifierCallEvidence(provider, base_url, _sha(body), "", 0, time.monotonic() - start, False, exc.__class__.__name__)
    return VerifierCallEvidence(provider, base_url, _sha(body), _sha(response_body), status, time.monotonic() - start, 200 <= status < 300)


def run_live_verifier_campaign(rows: Sequence[Mapping[str, Any]], *, target_run_id: str) -> dict[str, Any]:
    calls = []
    for provider in ("ors", "openreward"):
        for row in rows:
            calls.append(call_ors_openreward({"target_run_id": target_run_id, "row": dict(row)}, provider=provider, timeout_s=10.0).to_dict())
    blocked = [call for call in calls if call.get("blocked_reason")]
    return {
        "schema_version": "bb.rl.phase3.live_verifier_campaign.v1",
        "report_id": "phase3_live_verifier_campaign",
        "claim_boundary": "phase3_live_provider_campaign_scope",
        "target_run_id": target_run_id,
        "calls": calls,
        "blocked_reason": ";".join(sorted({str(call.get("blocked_reason")) for call in blocked if call.get("blocked_reason")})),
        "scorecard_update_allowed": False,
        "passed": bool(calls) and all(call["passed"] for call in calls),
    }


HARBOR_SERVICE_PROOF_SCHEMA = "bb.rl.phase3.harbor_service_proof.v1"
HARBOR_SERVICE_PROOF_ID = "phase3_harbor_service_proof"
HARBOR_CLAIM_BOUNDARY = "phase3_harbor_nemo_gym_named_endpoint_scope"
HARBOR_BLOCKED_CLAIM_BOUNDARY = "phase3_harbor_nemo_gym_blocked_scope"
HARBOR_ROUTES = (
    "GET /health",
    "GET /metrics.json",
    "GET /list_tasks",
    "POST /score",
    "POST /trial/create",
    "POST /trial/{trial_id}/exec",
    "GET /trial/{trial_id}",
    "POST /trial/{trial_id}/finalize",
)


def _redacted_endpoint(base_url: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "", "", "", ""))


def _response_json(response: bytes) -> Any:
    if not response:
        return {}
    try:
        decoded = json.loads(response.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return decoded


def _json_request_body(payload: Mapping[str, Any] | None) -> bytes:
    return b"" if payload is None else json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def run_benchflow_harbor_attestation(env_package_path, *, target_run_id: str) -> dict[str, Any]:
    return run_harbor_service_proof(env_package_path, target_run_id=target_run_id)


def run_harbor_service_proof(env_package_path, *, target_run_id: str, task_name: str | None = None) -> dict[str, Any]:
    harbor_base_url = os.environ.get("BREADBOARD_HARBOR_BASE_URL", "")
    harbor_token = os.environ.get("BREADBOARD_HARBOR_TOKEN", "")
    harbor_task_name = task_name or os.environ.get("BREADBOARD_HARBOR_TASK_NAME", "phase3-harbor-smoke")
    if not harbor_base_url or not harbor_token:
        return {
            "schema_version": HARBOR_SERVICE_PROOF_SCHEMA,
            "report_id": HARBOR_SERVICE_PROOF_ID,
            "claim_boundary": HARBOR_BLOCKED_CLAIM_BOUNDARY,
            "target_run_id": target_run_id,
            "attestation_backend": "harbor_facade",
            "provider_kind": "harbor_facade",
            "harbor_routes": list(HARBOR_ROUTES),
            "missing_provider_env": [
                "BREADBOARD_HARBOR_BASE_URL",
                "BREADBOARD_HARBOR_TOKEN",
            ],
            "blocked_reason": "missing_harbor_credentials",
            "scorecard_update_allowed": False,
            "passed": False,
        }
    return _run_harbor_facade_attestation(
        harbor_base_url,
        harbor_token,
        env_package_path,
        target_run_id=target_run_id,
        task_name=harbor_task_name,
    )


def _run_native_benchflow_attestation(base_url: str, token: str, env_package_path, *, target_run_id: str) -> dict[str, Any]:
    block_reason = _provider_url_block_reason(base_url)
    if block_reason:
        return {
            "schema_version": "bb.rl.phase3.native_benchflow_attestation.v1",
            "report_id": "phase3_native_benchflow_attestation",
            "claim_boundary": "phase3_native_benchflow_contract_only_scope",
            "target_run_id": target_run_id,
            "blocked_reason": block_reason,
            "attestation_backend": "native_benchflow",
            "scorecard_update_allowed": False,
            "passed": False,
        }
    if not os.path.isfile(env_package_path):
        return {
            "schema_version": "bb.rl.phase3.native_benchflow_attestation.v1",
            "report_id": "phase3_native_benchflow_attestation",
            "claim_boundary": "phase3_native_benchflow_contract_only_scope",
            "target_run_id": target_run_id,
            "blocked_reason": "missing_benchflow_env_package",
            "attestation_backend": "native_benchflow",
            "scorecard_update_allowed": False,
            "passed": False,
        }
    env_package_sha256 = sha256_file(Path(env_package_path))
    body = json.dumps({"target_run_id": target_run_id, "env_package_sha256": env_package_sha256}, sort_keys=True, separators=(",", ":")).encode()
    request = urllib.request.Request(base_url.rstrip("/") + "/attest", data=body, method="POST", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    start = time.monotonic()
    try:
        with _open_without_proxies(request, timeout_s=10.0) as response:  # noqa: S310 - operator-configured provider endpoint.
            response_body = response.read()
            status = int(response.status)
    except urllib.error.HTTPError as exc:
        response_body = exc.read()
        status = int(exc.code)
        return {
            "schema_version": "bb.rl.phase3.native_benchflow_attestation.v1",
            "report_id": "phase3_native_benchflow_attestation",
            "claim_boundary": "phase3_native_benchflow_contract_only_scope",
            "target_run_id": target_run_id,
            "attestation_backend": "native_benchflow",
            "endpoint_path": "/attest",
            "request_sha256": _sha(body),
            "response_sha256": _sha(response_body),
            "status_code": status,
            "latency_seconds": time.monotonic() - start,
            "blocked_reason": "",
            "scorecard_update_allowed": False,
            "passed": False,
        }
    except ProviderRedirectBlocked as exc:
        return {
            "schema_version": "bb.rl.phase3.native_benchflow_attestation.v1",
            "report_id": "phase3_native_benchflow_attestation",
            "claim_boundary": "phase3_native_benchflow_contract_only_scope",
            "target_run_id": target_run_id,
            "attestation_backend": "native_benchflow",
            "endpoint_path": "/attest",
            "request_sha256": _sha(body),
            "response_sha256": "",
            "status_code": 0,
            "latency_seconds": time.monotonic() - start,
            "blocked_reason": exc.block_reason,
            "scorecard_update_allowed": False,
            "passed": False,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "schema_version": "bb.rl.phase3.native_benchflow_attestation.v1",
            "report_id": "phase3_native_benchflow_attestation",
            "claim_boundary": "phase3_native_benchflow_contract_only_scope",
            "target_run_id": target_run_id,
            "attestation_backend": "native_benchflow",
            "endpoint_path": "/attest",
            "request_sha256": _sha(body),
            "response_sha256": "",
            "status_code": 0,
            "latency_seconds": time.monotonic() - start,
            "blocked_reason": exc.__class__.__name__,
            "scorecard_update_allowed": False,
            "passed": False,
        }
    return {
        "schema_version": "bb.rl.phase3.native_benchflow_attestation.v1",
        "report_id": "phase3_native_benchflow_attestation",
        "claim_boundary": "phase3_native_benchflow_contract_only_scope",
        "target_run_id": target_run_id,
        "attestation_backend": "native_benchflow",
        "endpoint_path": "/attest",
        "request_sha256": _sha(body),
        "response_sha256": _sha(response_body),
        "status_code": status,
        "latency_seconds": time.monotonic() - start,
        "blocked_reason": "",
        "scorecard_update_allowed": False,
        "passed": 200 <= status < 300,
    }


def _harbor_json_request(base_url: str, token: str, method: str, path: str, payload: Mapping[str, Any] | None = None, *, allow_local: bool = False) -> tuple[int, bytes, bytes, float]:
    body = _json_request_body(payload)
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(base_url.rstrip("/") + path, data=body if payload is not None else None, method=method, headers=headers)
    start = time.monotonic()
    if allow_local:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=10.0) as response:  # noqa: S310 - self-hosted Harbor is operator-started inside the target job.
            return int(response.status), body, response.read(), time.monotonic() - start
    with _open_without_proxies(request, timeout_s=10.0) as response:  # noqa: S310 - operator-configured provider endpoint.
        return int(response.status), body, response.read(), time.monotonic() - start


def _harbor_call_row(
    *,
    method: str,
    route_template: str,
    path: str,
    status_code: int,
    request_body: bytes,
    response_body: bytes,
    latency_seconds: float,
    blocked_reason: str = "",
) -> dict[str, Any]:
    return {
        "method": method,
        "route_template": route_template,
        "path": path,
        "status_code": status_code,
        "request_sha256": _sha(request_body),
        "response_sha256": _sha(response_body),
        "latency_seconds": latency_seconds,
        "passed": 200 <= status_code < 300 and not blocked_reason,
        "blocked_reason": blocked_reason,
    }


def _blocked_harbor_report(
    *,
    target_run_id: str,
    base_url: str,
    env_package_sha256: str,
    task_name: str,
    calls: Sequence[Mapping[str, Any]],
    blocked_reason: str,
    total_latency_seconds: float,
) -> dict[str, Any]:
    return {
        "schema_version": HARBOR_SERVICE_PROOF_SCHEMA,
        "report_id": HARBOR_SERVICE_PROOF_ID,
        "claim_boundary": HARBOR_BLOCKED_CLAIM_BOUNDARY,
        "target_run_id": target_run_id,
        "attestation_backend": "harbor_facade",
        "provider_kind": "harbor_facade",
        "endpoint_identity": _redacted_endpoint(base_url),
        "env_package_sha256": env_package_sha256,
        "task_name": task_name,
        "harbor_routes": list(HARBOR_ROUTES),
        "harbor_calls": list(calls),
        "blocked_reason": blocked_reason,
        "latency_seconds": total_latency_seconds,
        "scorecard_update_allowed": False,
        "passed": False,
    }


def _harbor_score_passed(response: Mapping[str, Any]) -> bool:
    if response.get("passed") is True:
        return True
    for key in ("score", "reward"):
        value = response.get(key)
        if isinstance(value, (int, float)) and float(value) >= 1.0:
            return True
    raw = response.get("raw")
    if isinstance(raw, Mapping):
        reward = raw.get("reward")
        return isinstance(reward, (int, float)) and float(reward) >= 1.0
    return False


def _harbor_exec_passed(response: Mapping[str, Any]) -> bool:
    return_code = response.get("return_code", response.get("returncode"))
    return (
        isinstance(return_code, int)
        and return_code == 0
        and str(response.get("stdout") or "") == "phase3-harbor-proof"
    )


def _harbor_trial_passed(response: Mapping[str, Any], *, task_name: str, trial_id: str) -> bool:
    n_exec_calls = response.get("n_exec_calls")
    return (
        str(response.get("trial_id") or "") == trial_id
        and str(response.get("task_name") or "") == task_name
        and isinstance(n_exec_calls, int)
        and n_exec_calls >= 1
    )


def _harbor_list_has_task(response: Any, *, task_name: str) -> bool:
    if isinstance(response, list):
        return task_name in {str(item) for item in response}
    if isinstance(response, Mapping):
        tasks = response.get("tasks", response.get("items"))
        if isinstance(tasks, list):
            return task_name in {str(item.get("name") if isinstance(item, Mapping) else item) for item in tasks}
    return False


def _harbor_response_semantics_passed(responses: Mapping[str, Any], *, task_name: str, trial_id: str) -> bool:
    create_response = responses.get("POST /trial/create", {})
    if not isinstance(create_response, Mapping) or str(create_response.get("trial_id") or "") != trial_id:
        return False
    if create_response.get("task_name") not in (None, task_name):
        return False
    return (
        _harbor_list_has_task(responses.get("GET /list_tasks"), task_name=task_name)
        and isinstance(responses.get("POST /score"), Mapping)
        and _harbor_score_passed(responses["POST /score"])
        and isinstance(responses.get("POST /trial/{trial_id}/exec"), Mapping)
        and _harbor_exec_passed(responses["POST /trial/{trial_id}/exec"])
        and isinstance(responses.get("GET /trial/{trial_id}"), Mapping)
        and _harbor_trial_passed(responses["GET /trial/{trial_id}"], task_name=task_name, trial_id=trial_id)
        and isinstance(responses.get("POST /trial/{trial_id}/finalize"), Mapping)
        and _harbor_score_passed(responses["POST /trial/{trial_id}/finalize"])
    )


def _run_harbor_facade_attestation(base_url: str, token: str, env_package_path, *, target_run_id: str, task_name: str) -> dict[str, Any]:
    allow_local = os.environ.get("BREADBOARD_HARBOR_ALLOW_LOCAL", "").lower() in {"1", "true", "yes"}
    block_reason = "" if allow_local else _provider_url_block_reason(base_url)
    if block_reason:
        return _blocked_harbor_report(
            target_run_id=target_run_id,
            base_url=base_url,
            env_package_sha256="",
            task_name=task_name,
            calls=[],
            blocked_reason=block_reason,
            total_latency_seconds=0.0,
        )
    if not os.path.isfile(env_package_path):
        return _blocked_harbor_report(
            target_run_id=target_run_id,
            base_url=base_url,
            env_package_sha256="",
            task_name=task_name,
            calls=[],
            blocked_reason="missing_harbor_env_package",
            total_latency_seconds=0.0,
        )
    env_package_sha256 = sha256_file(Path(env_package_path))
    calls: list[dict[str, Any]] = []
    responses: dict[str, Mapping[str, Any]] = {}
    start = time.monotonic()
    trial_id = ""
    try:
        for method, route, path, payload in (
            ("GET", "GET /health", "/health", None),
            ("GET", "GET /metrics.json", "/metrics.json", None),
            ("GET", "GET /list_tasks", "/list_tasks", None),
            ("POST", "POST /score", "/score", {"task_name": task_name, "answer": "phase3 harbor service proof"}),
        ):
            status, body, response, latency = _harbor_json_request(base_url, token, method, path, payload, allow_local=allow_local)
            calls.append(_harbor_call_row(method=method, route_template=route, path=path, status_code=status, request_body=body, response_body=response, latency_seconds=latency))
            responses[route] = _response_json(response)
        create_payload = {"task_name": task_name}
        status, body, response, latency = _harbor_json_request(base_url, token, "POST", "/trial/create", create_payload, allow_local=allow_local)
        calls.append(_harbor_call_row(method="POST", route_template="POST /trial/create", path="/trial/create", status_code=status, request_body=body, response_body=response, latency_seconds=latency))
        create_response = _response_json(response)
        responses["POST /trial/create"] = create_response
        trial_id = str(create_response.get("trial_id") or "")
        if not trial_id:
            raise ValueError("harbor_trial_id_missing")
        quoted_trial = urllib.parse.quote(trial_id, safe="")
        exec_payload = {"cmd": "printf phase3-harbor-proof", "timeout_sec": 30}
        status, body, response, latency = _harbor_json_request(base_url, token, "POST", f"/trial/{quoted_trial}/exec", exec_payload, allow_local=allow_local)
        calls.append(_harbor_call_row(method="POST", route_template="POST /trial/{trial_id}/exec", path=f"/trial/{quoted_trial}/exec", status_code=status, request_body=body, response_body=response, latency_seconds=latency))
        responses["POST /trial/{trial_id}/exec"] = _response_json(response)
        status, body, response, latency = _harbor_json_request(base_url, token, "GET", f"/trial/{quoted_trial}", None, allow_local=allow_local)
        calls.append(_harbor_call_row(method="GET", route_template="GET /trial/{trial_id}", path=f"/trial/{quoted_trial}", status_code=status, request_body=body, response_body=response, latency_seconds=latency))
        responses["GET /trial/{trial_id}"] = _response_json(response)
        finalize_payload = {"answer": "phase3 harbor service proof"}
        status, body, response, latency = _harbor_json_request(base_url, token, "POST", f"/trial/{quoted_trial}/finalize", finalize_payload, allow_local=allow_local)
        calls.append(_harbor_call_row(method="POST", route_template="POST /trial/{trial_id}/finalize", path=f"/trial/{quoted_trial}/finalize", status_code=status, request_body=body, response_body=response, latency_seconds=latency))
        responses["POST /trial/{trial_id}/finalize"] = _response_json(response)
    except urllib.error.HTTPError as exc:
        response_body = exc.read()
        calls.append(_harbor_call_row(method=getattr(exc, "method", "HTTP"), route_template="http_error", path=exc.url, status_code=int(exc.code), request_body=b"", response_body=response_body, latency_seconds=time.monotonic() - start, blocked_reason="http_error"))
    except ProviderRedirectBlocked as exc:
        return _blocked_harbor_report(
            target_run_id=target_run_id,
            base_url=base_url,
            env_package_sha256=env_package_sha256,
            task_name=task_name,
            calls=calls,
            blocked_reason=exc.block_reason,
            total_latency_seconds=time.monotonic() - start,
        )
    except Exception as exc:  # noqa: BLE001
        return _blocked_harbor_report(
            target_run_id=target_run_id,
            base_url=base_url,
            env_package_sha256=env_package_sha256,
            task_name=task_name,
            calls=calls,
            blocked_reason=exc.__class__.__name__,
            total_latency_seconds=time.monotonic() - start,
        )
    route_templates = [str(call.get("route_template") or "") for call in calls]
    backend_identity = ""
    health = responses.get("GET /health", {})
    if isinstance(health.get("dataset"), str):
        backend_identity = str(health["dataset"])
    semantics_passed = _harbor_response_semantics_passed(responses, task_name=task_name, trial_id=trial_id)
    passed = (
        list(HARBOR_ROUTES) == route_templates
        and all(call.get("passed") is True for call in calls)
        and bool(trial_id)
        and bool(env_package_sha256)
        and semantics_passed
    )
    return {
        "schema_version": HARBOR_SERVICE_PROOF_SCHEMA,
        "report_id": HARBOR_SERVICE_PROOF_ID,
        "claim_boundary": HARBOR_CLAIM_BOUNDARY if passed else HARBOR_BLOCKED_CLAIM_BOUNDARY,
        "target_run_id": target_run_id,
        "attestation_backend": "harbor_facade",
        "provider_kind": "harbor_facade",
        "endpoint_identity": _redacted_endpoint(base_url),
        "backend_identity": backend_identity,
        "env_package_sha256": env_package_sha256,
        "task_name": task_name,
        "trial_id_sha256": _sha(trial_id.encode()) if trial_id else "",
        "harbor_routes": list(HARBOR_ROUTES),
        "harbor_calls": calls,
        "status_codes": {str(call.get("route_template")): int(call.get("status_code") or 0) for call in calls},
        "latency_seconds": time.monotonic() - start,
        "blocked_reason": "" if passed else ("harbor_response_semantics_failed" if route_templates == list(HARBOR_ROUTES) and all(call.get("passed") is True for call in calls) else "harbor_route_sequence_failed"),
        "scorecard_update_allowed": False,
        "passed": passed,
    }
