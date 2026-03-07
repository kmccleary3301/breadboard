from __future__ import annotations

import asyncio
from pathlib import Path

from breadboard_ext.atp.aristotle_adapter import (
    AristotleProjectAdapter,
    AristotleRunConfig,
    AristotleTaskInput,
)


class _FakeResponse:
    def __init__(self, payload=None, *, content: bytes = b"") -> None:
        self._payload = payload if payload is not None else {}
        self.content = content

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, script) -> None:
        self._script = script

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        return None

    async def post(self, endpoint: str, params=None, files=None):
        return self._script("POST", endpoint, params=params, files=files)

    async def get(self, endpoint: str, params=None):
        return self._script("GET", endpoint, params=params)


def _run(coro):
    return asyncio.run(coro)


async def _noop_sleep(_seconds: float) -> None:
    return None


def test_aristotle_adapter_maps_complete_to_solved(tmp_path: Path) -> None:
    poll_count = {"value": 0}

    def _script(method: str, endpoint: str, **kwargs):
        if method == "GET" and endpoint == "/project":
            return _FakeResponse({"projects": []})
        if method == "POST" and endpoint.startswith("/project?project_type="):
            return _FakeResponse({"project_id": "p-success", "status": "NOT_STARTED"})
        if method == "POST" and endpoint == "/project/p-success/solve":
            assert kwargs["params"]["input_text"]
            return _FakeResponse({"project_id": "p-success", "status": "IN_PROGRESS"})
        if method == "GET" and endpoint == "/project/p-success":
            poll_count["value"] += 1
            if poll_count["value"] == 1:
                return _FakeResponse({"project_id": "p-success", "status": "IN_PROGRESS"})
            return _FakeResponse({"project_id": "p-success", "status": "COMPLETE"})
        if method == "GET" and endpoint == "/project/p-success/result":
            return _FakeResponse(content=b"theorem t : 1 + 1 = 2 := by decide\n")
        raise AssertionError(f"unexpected call: {method} {endpoint}")

    class _ClientFactory(_FakeClient):
        def __init__(self) -> None:
            super().__init__(_script)

    adapter = AristotleProjectAdapter(
        api_key="test-key",
        request_client_cls=_ClientFactory,
        sleep_fn=_noop_sleep,
    )
    run = AristotleRunConfig(
        run_id="run-demo",
        prover_system="aristotle_api",
        toolchain_id="lean4.12_mathlib.deadbeef",
        budget_class="B",
        wall_clock_cap_s=60,
        poll_interval_s=0.01,
    )
    task = AristotleTaskInput(task_id="t1", input_text="prove 1+1=2")
    row = _run(adapter.run_task(task=task, run=run, proof_output_dir=tmp_path))

    assert row["task_id"] == "t1"
    assert row["status"] == "SOLVED"
    assert row["prover_system"] == "aristotle_api"
    assert row["budget_class"] == "B"
    assert row["run_id"] == "run-demo"
    assert row["proof_artifact_ref"].endswith("t1.lean")
    assert row["verification_log_digest"]


def test_aristotle_adapter_marks_sorry_output_unsolved() -> None:
    def _script(method: str, endpoint: str, **kwargs):
        if method == "GET" and endpoint == "/project":
            return _FakeResponse({"projects": []})
        if method == "POST" and endpoint.startswith("/project?project_type="):
            return _FakeResponse({"project_id": "p-unsolved", "status": "NOT_STARTED"})
        if method == "POST" and endpoint == "/project/p-unsolved/solve":
            return _FakeResponse({"project_id": "p-unsolved", "status": "IN_PROGRESS"})
        if method == "GET" and endpoint == "/project/p-unsolved":
            return _FakeResponse({"project_id": "p-unsolved", "status": "COMPLETE"})
        if method == "GET" and endpoint == "/project/p-unsolved/result":
            return _FakeResponse(content=b"theorem t : True := by sorry\n")
        raise AssertionError(f"unexpected call: {method} {endpoint}")

    class _ClientFactory(_FakeClient):
        def __init__(self) -> None:
            super().__init__(_script)

    adapter = AristotleProjectAdapter(
        api_key="test-key",
        request_client_cls=_ClientFactory,
        sleep_fn=_noop_sleep,
    )
    run = AristotleRunConfig(
        run_id="run-demo",
        prover_system="aristotle_api",
        toolchain_id="lean4.12_mathlib.deadbeef",
        budget_class="B",
        wall_clock_cap_s=60,
    )
    row = _run(
        adapter.run_task(
            task=AristotleTaskInput(task_id="t2", input_text="prove something"),
            run=run,
            proof_output_dir=None,
        )
    )
    assert row["status"] == "UNSOLVED"


def test_aristotle_adapter_marks_timeout() -> None:
    calls = {"cancel": 0}

    def _script(method: str, endpoint: str, **kwargs):
        if method == "GET" and endpoint == "/project":
            return _FakeResponse({"projects": []})
        if method == "POST" and endpoint.startswith("/project?project_type="):
            return _FakeResponse({"project_id": "p-timeout", "status": "NOT_STARTED"})
        if method == "POST" and endpoint == "/project/p-timeout/solve":
            return _FakeResponse({"project_id": "p-timeout", "status": "IN_PROGRESS"})
        if method == "GET" and endpoint == "/project/p-timeout":
            return _FakeResponse({"project_id": "p-timeout", "status": "IN_PROGRESS"})
        if method == "POST" and endpoint == "/project/p-timeout/cancel":
            calls["cancel"] += 1
            return _FakeResponse({"project_id": "p-timeout", "status": "CANCELED"})
        raise AssertionError(f"unexpected call: {method} {endpoint}")

    class _ClientFactory(_FakeClient):
        def __init__(self) -> None:
            super().__init__(_script)

    times = iter([0.0, 0.4, 1.2, 1.3])

    def _time_monotonic() -> float:
        try:
            return next(times)
        except StopIteration:
            return 2.0

    adapter = AristotleProjectAdapter(
        api_key="test-key",
        request_client_cls=_ClientFactory,
        sleep_fn=_noop_sleep,
        time_monotonic_fn=_time_monotonic,
    )
    run = AristotleRunConfig(
        run_id="run-demo",
        prover_system="aristotle_api",
        toolchain_id="lean4.12_mathlib.deadbeef",
        budget_class="B",
        wall_clock_cap_s=1,
        poll_interval_s=0.01,
    )
    row = _run(
        adapter.run_task(
            task=AristotleTaskInput(task_id="t3", input_text="prove timeout"),
            run=run,
            proof_output_dir=None,
        )
    )
    assert row["status"] == "TIMEOUT"
    assert calls["cancel"] == 1


def test_aristotle_adapter_retries_capacity_error(monkeypatch) -> None:
    calls = {"create": 0}

    def _script(method: str, endpoint: str, **kwargs):
        if method == "GET" and endpoint == "/project":
            return _FakeResponse({"projects": []})
        if method == "POST" and endpoint.startswith("/project?project_type="):
            calls["create"] += 1
            if calls["create"] <= 2:
                raise RuntimeError("You currently already have 5 projects in progress.")
            return _FakeResponse({"project_id": "p-capacity", "status": "NOT_STARTED"})
        if method == "POST" and endpoint == "/project/p-capacity/solve":
            return _FakeResponse({"project_id": "p-capacity", "status": "IN_PROGRESS"})
        if method == "GET" and endpoint == "/project/p-capacity":
            return _FakeResponse({"project_id": "p-capacity", "status": "COMPLETE"})
        if method == "GET" and endpoint == "/project/p-capacity/result":
            return _FakeResponse(content=b"theorem t : True := by exact True.intro\n")
        raise AssertionError(f"unexpected call: {method} {endpoint}")

    class _ClientFactory(_FakeClient):
        def __init__(self) -> None:
            super().__init__(_script)

    monkeypatch.setenv("ARISTOTLE_CAPACITY_RETRY_ATTEMPTS", "5")
    monkeypatch.setenv("ARISTOTLE_CAPACITY_RETRY_SLEEP_SECONDS", "0.001")
    adapter = AristotleProjectAdapter(
        api_key="test-key",
        request_client_cls=_ClientFactory,
        sleep_fn=_noop_sleep,
    )
    run = AristotleRunConfig(
        run_id="run-capacity",
        prover_system="aristotle_api",
        toolchain_id="lean4.12_mathlib.deadbeef",
        budget_class="B",
        wall_clock_cap_s=30,
        poll_interval_s=0.01,
    )
    row = _run(
        adapter.run_task(
            task=AristotleTaskInput(task_id="t-capacity", input_text="prove true"),
            run=run,
            proof_output_dir=None,
        )
    )
    assert row["status"] == "SOLVED"
    assert calls["create"] >= 3


def test_capacity_wait_ignores_stale_not_started_backlog(monkeypatch) -> None:
    calls = {"list": 0, "create": 0}

    def _script(method: str, endpoint: str, **kwargs):
        if method == "GET" and endpoint == "/project":
            calls["list"] += 1
            return _FakeResponse(
                {
                    "projects": [
                        {"id": "stale-1", "status": "NOT_STARTED", "updated_at": "2026-02-20T00:00:00"},
                        {"id": "stale-2", "status": "NOT_STARTED", "updated_at": "2026-02-20T00:00:00"},
                        {"id": "done-1", "status": "COMPLETE", "updated_at": "2026-02-26T00:00:00"},
                    ]
                }
            )
        if method == "POST" and endpoint.startswith("/project?project_type="):
            calls["create"] += 1
            return _FakeResponse({"project_id": "p-stale-ok", "status": "NOT_STARTED"})
        if method == "POST" and endpoint == "/project/p-stale-ok/solve":
            return _FakeResponse({"project_id": "p-stale-ok", "status": "IN_PROGRESS"})
        if method == "GET" and endpoint == "/project/p-stale-ok":
            return _FakeResponse({"project_id": "p-stale-ok", "status": "COMPLETE"})
        if method == "GET" and endpoint == "/project/p-stale-ok/result":
            return _FakeResponse(content=b"theorem t : True := by exact True.intro\n")
        raise AssertionError(f"unexpected call: {method} {endpoint}")

    class _ClientFactory(_FakeClient):
        def __init__(self) -> None:
            super().__init__(_script)

    monkeypatch.setenv("ARISTOTLE_ENABLE_CAPACITY_WAIT", "1")
    monkeypatch.setenv("ARISTOTLE_MAX_IN_PROGRESS", "1")
    monkeypatch.setenv("ARISTOTLE_CAPACITY_WAIT_STRICT", "1")
    monkeypatch.setenv("ARISTOTLE_CAPACITY_WAIT_MAX_SECONDS", "1")
    adapter = AristotleProjectAdapter(
        api_key="test-key",
        request_client_cls=_ClientFactory,
        sleep_fn=_noop_sleep,
    )
    run = AristotleRunConfig(
        run_id="run-capacity-stale",
        prover_system="aristotle_api",
        toolchain_id="lean4.12_mathlib.deadbeef",
        budget_class="B",
        wall_clock_cap_s=30,
        poll_interval_s=0.01,
    )
    row = _run(
        adapter.run_task(
            task=AristotleTaskInput(task_id="t-stale", input_text="prove true"),
            run=run,
            proof_output_dir=None,
        )
    )
    assert row["status"] == "SOLVED"
    assert calls["list"] >= 1
    assert calls["create"] == 1


def test_timed_out_project_cleanup_allows_following_task(monkeypatch) -> None:
    state = {"task_index": 0, "task1_status": ""}

    def _script(method: str, endpoint: str, **kwargs):
        if method == "GET" and endpoint == "/project":
            if state["task1_status"] in {"IN_PROGRESS", "QUEUED"}:
                return _FakeResponse(
                    {
                        "projects": [
                            {"project_id": "p-task1", "status": state["task1_status"], "last_updated_at": "2026-03-06T00:00:00Z"}
                        ]
                    }
                )
            return _FakeResponse({"projects": []})
        if method == "POST" and endpoint.startswith("/project?project_type="):
            state["task_index"] += 1
            project_id = f"p-task{state['task_index']}"
            return _FakeResponse({"project_id": project_id, "status": "NOT_STARTED"})
        if method == "POST" and endpoint == "/project/p-task1/solve":
            state["task1_status"] = "IN_PROGRESS"
            return _FakeResponse({"project_id": "p-task1", "status": "IN_PROGRESS"})
        if method == "GET" and endpoint == "/project/p-task1":
            return _FakeResponse({"project_id": "p-task1", "status": state["task1_status"]})
        if method == "POST" and endpoint == "/project/p-task1/cancel":
            state["task1_status"] = "CANCELED"
            return _FakeResponse({"project_id": "p-task1", "status": "CANCELED"})
        if method == "POST" and endpoint == "/project/p-task2/solve":
            return _FakeResponse({"project_id": "p-task2", "status": "IN_PROGRESS"})
        if method == "GET" and endpoint == "/project/p-task2":
            return _FakeResponse({"project_id": "p-task2", "status": "COMPLETE"})
        if method == "GET" and endpoint == "/project/p-task2/result":
            return _FakeResponse(content=b"theorem t : True := by exact True.intro\n")
        raise AssertionError(f"unexpected call: {method} {endpoint}")

    class _ClientFactory(_FakeClient):
        def __init__(self) -> None:
            super().__init__(_script)

    times = iter([0.0, 0.4, 1.2, 1.3, 1.4, 1.5, 2.0, 2.1, 2.2, 2.3, 2.4])

    def _time_monotonic() -> float:
        try:
            return next(times)
        except StopIteration:
            return 3.0

    monkeypatch.setenv("ARISTOTLE_ENABLE_CAPACITY_WAIT", "1")
    monkeypatch.setenv("ARISTOTLE_MAX_IN_PROGRESS", "1")
    monkeypatch.setenv("ARISTOTLE_CAPACITY_WAIT_STRICT", "1")
    monkeypatch.setenv("ARISTOTLE_CAPACITY_WAIT_MAX_SECONDS", "5")
    monkeypatch.setenv("ARISTOTLE_TIMEOUT_CLEANUP_WAIT_SECONDS", "5")
    adapter = AristotleProjectAdapter(
        api_key="test-key",
        request_client_cls=_ClientFactory,
        sleep_fn=_noop_sleep,
        time_monotonic_fn=_time_monotonic,
    )
    run = AristotleRunConfig(
        run_id="run-timeout-cleanup",
        prover_system="aristotle_api",
        toolchain_id="lean4.12_mathlib.deadbeef",
        budget_class="B",
        wall_clock_cap_s=1,
        poll_interval_s=0.01,
    )
    rows = _run(
        adapter.run_tasks(
            tasks=[
                AristotleTaskInput(task_id="t-timeout", input_text="prove timeout"),
                AristotleTaskInput(task_id="t-followup", input_text="prove true"),
            ],
            run=run,
            max_concurrency=1,
            proof_output_dir=None,
            fail_fast=False,
        )
    )

    assert [row["status"] for row in rows] == ["TIMEOUT", "SOLVED"]
