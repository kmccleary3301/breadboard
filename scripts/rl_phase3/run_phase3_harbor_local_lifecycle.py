from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

SCHEMA = "bb.phase3.harbor_local_lifecycle.v1"
REPORT_ID = "phase3_harbor_local_lifecycle"
CLAIM_BOUNDARY = "local_harbor_api_lifecycle_only"

JsonPayload = Mapping[str, Any] | list[Any] | str | int | float | bool | None
Transport = Callable[[str, str, JsonPayload, float], tuple[int, JsonPayload]]


def _sha(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _json_bytes(payload: JsonPayload) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _endpoint_identity(base_url: str) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(base_url)
    return {
        "scheme": parsed.scheme,
        "hostname": parsed.hostname or "",
        "port": parsed.port,
        "is_loopback": (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"},
    }


def _default_transport(url: str, method: str, payload: JsonPayload, timeout_s: float) -> tuple[int, JsonPayload]:
    data = None if payload is None else _json_bytes(payload)
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - operator-provided local Harbor URL.
            raw = response.read()
            if not raw:
                return response.status, None
            return response.status, json.loads(raw.decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        if raw:
            try:
                body: JsonPayload = json.loads(raw.decode())
            except json.JSONDecodeError:
                body = raw.decode(errors="replace")
        else:
            body = str(exc)
        return exc.code, body


@dataclass
class RouteRecorder:
    base_url: str
    timeout_s: float
    transport: Transport

    def call(self, route_key: str, method: str, path: str, payload: JsonPayload = None) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + path
        started = time.monotonic()
        request_payload = payload
        request_sha = _sha(_json_bytes(request_payload)) if request_payload is not None else ""
        try:
            status_code, response_payload = self.transport(url, method, request_payload, self.timeout_s)
            error = ""
        except Exception as exc:  # noqa: BLE001 - report must capture local daemon failures.
            status_code = 0
            response_payload = None
            error = f"{exc.__class__.__name__}: {exc}"
        response_sha = _sha(_json_bytes(response_payload)) if response_payload is not None else ""
        passed = 200 <= int(status_code) < 300 and not error
        return {
            "route": route_key,
            "method": method,
            "path": path,
            "status": "passed" if passed else "failed",
            "status_code": status_code,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "request_sha256": request_sha,
            "response_sha256": response_sha,
            "error": error,
            "response": response_payload,
        }


def _route_status(route: dict[str, Any] | None) -> str:
    if route is None:
        return "skipped"
    return str(route.get("status") or "failed")


def build_lifecycle_report(
    *,
    base_url: str,
    target_run_id: str,
    task_name: str | None,
    answer: str,
    exec_cmd: str,
    timeout_s: float,
    transport: Transport = _default_transport,
) -> dict[str, Any]:
    recorder = RouteRecorder(base_url=base_url, timeout_s=timeout_s, transport=transport)
    route_results: dict[str, dict[str, Any] | None] = {}

    route_results["health"] = recorder.call("health", "GET", "/health")
    route_results["metrics"] = recorder.call("metrics", "GET", "/metrics.json")
    route_results["list_tasks"] = recorder.call("list_tasks", "GET", "/list_tasks")

    selected_task = task_name or ""
    list_response = route_results["list_tasks"].get("response") if route_results["list_tasks"] else None
    if not selected_task and isinstance(list_response, list) and list_response:
        selected_task = str(list_response[0])

    if selected_task:
        route_results["score"] = recorder.call("score", "POST", "/score", {"task_name": selected_task, "answer": answer})
        route_results["trial_create"] = recorder.call("trial_create", "POST", "/trial/create", {"task_name": selected_task, "ttl_sec": 30})
    else:
        route_results["score"] = None
        route_results["trial_create"] = None

    trial_id = ""
    create_response = route_results["trial_create"].get("response") if route_results["trial_create"] else None
    if isinstance(create_response, Mapping):
        trial_id = str(create_response.get("trial_id") or "")

    if trial_id:
        route_results["trial_exec"] = recorder.call("trial_exec", "POST", f"/trial/{trial_id}/exec", {"cmd": exec_cmd, "timeout_sec": 30})
        route_results["trial_get"] = recorder.call("trial_get", "GET", f"/trial/{trial_id}")
        route_results["trial_stats"] = recorder.call("trial_stats", "GET", "/trial_stats")
        route_results["trial_finalize"] = recorder.call("trial_finalize", "POST", f"/trial/{trial_id}/finalize", {"answer": answer})
    else:
        route_results["trial_exec"] = None
        route_results["trial_get"] = None
        route_results["trial_stats"] = None
        route_results["trial_finalize"] = None

    delete_trial_id = ""
    if selected_task:
        route_results["trial_create_for_delete"] = recorder.call("trial_create_for_delete", "POST", "/trial/create", {"task_name": selected_task, "ttl_sec": 30})
        delete_response = route_results["trial_create_for_delete"].get("response") if route_results["trial_create_for_delete"] else None
        if isinstance(delete_response, Mapping):
            delete_trial_id = str(delete_response.get("trial_id") or "")
    else:
        route_results["trial_create_for_delete"] = None

    if delete_trial_id:
        route_results["trial_delete"] = recorder.call("trial_delete", "DELETE", f"/trial/{delete_trial_id}")
    else:
        route_results["trial_delete"] = None

    required_routes = [
        "health",
        "metrics",
        "list_tasks",
        "score",
        "trial_create",
        "trial_exec",
        "trial_get",
        "trial_stats",
        "trial_finalize",
        "trial_create_for_delete",
        "trial_delete",
    ]
    route_statuses = {key: _route_status(route_results.get(key)) for key in required_routes}
    failed_routes = [key for key, status in route_statuses.items() if status != "passed"]
    blocked_reason = "" if not failed_routes else "harbor_local_lifecycle_failed:" + ",".join(failed_routes)
    passed = not failed_routes

    sanitized_routes = {
        key: {field: value for field, value in route.items() if field != "response"} if route else None
        for key, route in route_results.items()
    }
    return {
        "schema_version": SCHEMA,
        "report_id": REPORT_ID,
        "claim_boundary": CLAIM_BOUNDARY,
        "target_run_id": target_run_id,
        "promotional": False,
        "scorecard_update_allowed": False,
        "passed": passed,
        "blocked_reason": blocked_reason,
        "base_url_identity": _endpoint_identity(base_url),
        "target_endpoint_proven": False,
        "auth_proven": False,
        "docker_execution_proven": route_statuses.get("trial_exec") == "passed",
        "selected_task": selected_task,
        "task_source": {
            "HARBOR_DATASET": "operator_environment",
            "HARBOR_EXTRA_TASKS_DIR": "operator_environment",
        },
        "route_statuses": route_statuses,
        "routes": sanitized_routes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase-dir", required=True, type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:5050")
    parser.add_argument("--target-run-id", default="local-harbor-lifecycle-diagnostic")
    parser.add_argument("--task-name", default="")
    parser.add_argument("--answer", default="")
    parser.add_argument("--exec-cmd", default="true")
    parser.add_argument("--timeout-s", type=float, default=30.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--emit-component-json", action="store_true")
    args = parser.parse_args()

    report = build_lifecycle_report(
        base_url=args.base_url,
        target_run_id=args.target_run_id,
        task_name=args.task_name or None,
        answer=args.answer,
        exec_cmd=args.exec_cmd,
        timeout_s=args.timeout_s,
    )
    output = args.output or args.phase_dir / "runs" / "harbor_local_lifecycle" / "phase3_harbor_local_lifecycle.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    if args.emit_component_json:
        component = {
            "schema_version": "bb.rl.phase3.component_report.v1",
            "report_id": "p3_aux_harbor_local_lifecycle",
            "component": "harbor_local_lifecycle",
            "claim_boundary": report["claim_boundary"],
            "target_run_id": args.target_run_id,
            "points": 0,
            "passed": report["passed"],
            "blocked_reason": report["blocked_reason"],
            "lifecycle_report": report,
            "artifact_paths": {"lifecycle_report": str(output)},
            "required_artifact_keys": ["lifecycle_report"],
            "scorecard_update_allowed": False,
        }
        print("PHASE3_COMPONENT_REPORT_JSON=" + json.dumps(component, sort_keys=True))
    print(json.dumps({"report": str(output), "passed": report["passed"], "blocked_reason": report["blocked_reason"]}, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
