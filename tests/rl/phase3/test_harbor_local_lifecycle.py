from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from scripts.rl_phase3.run_phase3_harbor_local_lifecycle import build_lifecycle_report


def _passing_transport(url: str, method: str, payload: Any, timeout_s: float) -> tuple[int, Any]:
    path = urlparse(url).path
    if path == "/health":
        return 200, {"ok": True, "n_tasks": 1}
    if path == "/metrics.json":
        return 200, {"requests_total": 1}
    if path == "/list_tasks":
        return 200, ["task.alpha"]
    if path == "/score" and method == "POST":
        return 200, {"reward": 1.0, "raw": {"reward": 1.0}, "error": None}
    if path == "/trial/create" and method == "POST":
        name = "trial-delete" if payload and payload.get("task_name") == "task.delete" else "trial-main"
        return 200, {"trial_id": name, "task_name": payload["task_name"], "instruction": "solve", "expires_at_ts": 1.0}
    if path == "/trial/trial-main/exec" and method == "POST":
        return 200, {"stdout": "", "stderr": "", "return_code": 0, "stdout_truncated": False, "stderr_truncated": False, "duration_sec": 0.01}
    if path == "/trial/trial-main" and method == "GET":
        return 200, {"trial_id": "trial-main", "task_name": "task.alpha", "n_exec_calls": 1, "finalized": False}
    if path == "/trial_stats":
        return 200, {"n_active": 1}
    if path == "/trial/trial-main/finalize" and method == "POST":
        return 200, {"reward": 1.0, "raw": {"reward": 1.0}, "error": None}
    if path == "/trial/trial-delete" and method == "DELETE":
        return 200, {"ok": True}
    return 404, {"error": f"unexpected {method} {path}"}


def test_harbor_local_lifecycle_passes_as_non_promotional_local_report() -> None:
    calls: list[tuple[str, str, Any]] = []

    def transport(url: str, method: str, payload: Any, timeout_s: float) -> tuple[int, Any]:
        calls.append((method, urlparse(url).path, payload))
        if urlparse(url).path == "/trial/create" and len([call for call in calls if call[1] == "/trial/create"]) == 2:
            return 200, {"trial_id": "trial-delete", "task_name": payload["task_name"], "instruction": "solve", "expires_at_ts": 1.0}
        return _passing_transport(url, method, payload, timeout_s)

    report = build_lifecycle_report(
        base_url="http://127.0.0.1:5050",
        target_run_id="local-diagnostic",
        task_name=None,
        answer="ok",
        exec_cmd="true",
        timeout_s=1.0,
        transport=transport,
    )

    assert report["passed"] is True
    assert report["promotional"] is False
    assert report["scorecard_update_allowed"] is False
    assert report["claim_boundary"] == "local_harbor_api_lifecycle_only"
    assert report["target_endpoint_proven"] is False
    assert report["auth_proven"] is False
    assert report["docker_execution_proven"] is True
    assert report["base_url_identity"]["is_loopback"] is True
    assert report["selected_task"] == "task.alpha"
    assert report["route_statuses"]["trial_delete"] == "passed"
    assert all(route.get("response") is None for route in report["routes"].values() if route)


def test_harbor_local_lifecycle_blocks_when_health_fails() -> None:
    def transport(url: str, method: str, payload: Any, timeout_s: float) -> tuple[int, Any]:
        if urlparse(url).path == "/health":
            return 503, {"ok": False}
        return _passing_transport(url, method, payload, timeout_s)

    report = build_lifecycle_report(
        base_url="http://127.0.0.1:5050",
        target_run_id="local-diagnostic",
        task_name="task.alpha",
        answer="ok",
        exec_cmd="true",
        timeout_s=1.0,
        transport=transport,
    )

    assert report["passed"] is False
    assert report["route_statuses"]["health"] == "failed"
    assert "health" in report["blocked_reason"]
    assert report["scorecard_update_allowed"] is False


def test_harbor_local_lifecycle_blocks_without_tasks() -> None:
    def transport(url: str, method: str, payload: Any, timeout_s: float) -> tuple[int, Any]:
        if urlparse(url).path == "/list_tasks":
            return 200, []
        return _passing_transport(url, method, payload, timeout_s)

    report = build_lifecycle_report(
        base_url="http://127.0.0.1:5050",
        target_run_id="local-diagnostic",
        task_name=None,
        answer="ok",
        exec_cmd="true",
        timeout_s=1.0,
        transport=transport,
    )

    assert report["passed"] is False
    assert report["selected_task"] == ""
    assert report["route_statuses"]["score"] == "skipped"
    assert report["route_statuses"]["trial_create"] == "skipped"
    assert "score" in report["blocked_reason"]


def test_harbor_local_lifecycle_blocks_on_trial_create_failure() -> None:
    def transport(url: str, method: str, payload: Any, timeout_s: float) -> tuple[int, Any]:
        if urlparse(url).path == "/trial/create":
            return 500, {"error": "trial open failed"}
        return _passing_transport(url, method, payload, timeout_s)

    report = build_lifecycle_report(
        base_url="http://127.0.0.1:5050",
        target_run_id="local-diagnostic",
        task_name="task.alpha",
        answer="ok",
        exec_cmd="true",
        timeout_s=1.0,
        transport=transport,
    )

    assert report["passed"] is False
    assert report["route_statuses"]["trial_create"] == "failed"
    assert report["route_statuses"]["trial_exec"] == "skipped"
    assert report["route_statuses"]["trial_delete"] == "skipped"
    assert report["target_endpoint_proven"] is False
