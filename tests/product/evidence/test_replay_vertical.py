from __future__ import annotations

import json
from pathlib import Path

import pytest

from breadboard.product.evidence.replay import (
    ReplayCoordinator,
    ReplayManifest,
    ReplayManifestEntry,
    ReplayPlan,
    ReplayWorkerResult,
    TapeReplayWorker,
)
from breadboard.product.runtime.artifacts import ArtifactStore


def _tape() -> dict:
    return {
        "schema_version": "bb.replay_tape.v1",
        "steps": [
            {
                "kind": "provider.request",
                "span_id": "span-provider-request",
                "parent_span_id": None,
                "payload": {"model": "tape/reference", "prompt_hash": "sha256:" + "1" * 64},
            },
            {
                "kind": "provider.response",
                "span_id": "span-provider-response",
                "parent_span_id": "span-provider-request",
                "payload": {"text": "stable response"},
            },
            {
                "kind": "tool.result",
                "span_id": "span-tool-result",
                "parent_span_id": "span-provider-response",
                "payload": {"exit_code": 0},
            },
        ],
        "outputs": {"result.json": {"ok": True, "answer": "stable response"}},
    }


def _manifest() -> ReplayManifest:
    return ReplayManifest(
        (
            ReplayManifestEntry("result.json", "application/json"),
            ReplayManifestEntry("transcript.json", "application/json"),
        )
    )


def _plan(store: ArtifactStore, *, options: dict | None = None) -> ReplayPlan:
    tape_ref = store.put_json(_tape())
    return ReplayPlan(
        source_session_id="session-fixture",
        input_artifact=tape_ref,
        worker_id=TapeReplayWorker.worker_id,
        manifest=_manifest(),
        options=options or {"temperature": 0, "nested": {"seed": 7}},
    )


class _CountingTapeWorker(TapeReplayWorker):
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, plan: ReplayPlan, input_bytes: bytes) -> ReplayWorkerResult:
        self.calls += 1
        return super().execute(plan, input_bytes)


class _FailingWorker:
    worker_id = TapeReplayWorker.worker_id

    def execute(self, plan: ReplayPlan, input_bytes: bytes) -> ReplayWorkerResult:
        raise OSError("worker unavailable")


def test_completed_replay_has_causal_transcript_immutable_refs_and_one_terminal_outcome(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    plan = _plan(store)
    result = ReplayCoordinator(store, TapeReplayWorker()).run(plan)

    assert result.disposition == "executed"
    assert result.claimable is True
    assert result.execution is not None
    assert [event.state for event in result.execution.events] == ["planned", "admitted", "running", "completed"]
    assert [event.parent_span_id for event in result.execution.events] == [
        None,
        result.execution.events[0].span_id,
        result.execution.events[1].span_id,
        result.execution.events[2].span_id,
    ]
    assert sum(event.state in {"completed", "failed", "canceled", "timed_out", "integrity_failed"} for event in result.execution.events) == 1
    assert set(result.artifacts) == {"result.json", "transcript.json"}
    assert json.loads(store.read(result.artifacts["result.json"])) == {"answer": "stable response", "ok": True}
    transcript = json.loads(store.read(result.artifacts["transcript.json"]))
    assert [event["sequence"] for event in transcript["events"]] == [1, 2, 3]
    assert [event["parent_span_id"] for event in transcript["events"]] == [
        None,
        "span-provider-request",
        "span-provider-response",
    ]
    with pytest.raises(TypeError):
        result.artifacts["late.json"] = result.artifacts["result.json"]  # type: ignore[index]
    with pytest.raises(TypeError):
        plan.options["nested"]["seed"] = 8  # type: ignore[index]


def test_exact_plan_reuses_provenance_but_mutation_forces_execution(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    worker = _CountingTapeWorker()
    coordinator = ReplayCoordinator(store, worker)
    original = _plan(store)
    first = coordinator.run(original)
    reused = coordinator.run(original, reuse_candidate=first)

    assert worker.calls == 1
    assert reused.disposition == "reused"
    assert reused.execution is first.execution
    assert reused.claimable is True

    mutated = _plan(store, options={"temperature": 0, "nested": {"seed": 8}})
    assert mutated.plan_id != original.plan_id
    rerun = coordinator.run(mutated, reuse_candidate=first)
    assert worker.calls == 2
    assert rerun.disposition == "executed"
    assert rerun.plan_id == mutated.plan_id
    assert rerun.execution is not first.execution


def test_failed_and_stored_only_results_cannot_be_claimed(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    plan = _plan(store)
    completed = ReplayCoordinator(store, TapeReplayWorker()).run(plan)
    stored = ReplayCoordinator(store, TapeReplayWorker()).run(
        plan,
        stored_artifacts=completed.artifacts,
        execute=False,
    )
    failed = ReplayCoordinator(store, _FailingWorker()).run(plan)

    assert stored.disposition == "stored" and stored.execution is None and stored.claimable is False
    assert failed.execution is not None and failed.execution.state == "failed" and failed.claimable is False
    with pytest.raises(RuntimeError, match="stored replay result is not claimable"):
        stored.require_claimable()
    with pytest.raises(RuntimeError, match="executed replay result is not claimable"):
        failed.require_claimable()


def test_manifest_escape_and_output_drift_are_integrity_failures(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="contained"):
        ReplayManifest((ReplayManifestEntry("../escape.json", "application/json"),))

    store = ArtifactStore(tmp_path / "artifacts")
    tape = _tape()
    tape["outputs"]["unexpected.json"] = {"unexpected": True}
    plan = ReplayPlan(
        source_session_id="session-fixture",
        input_artifact=store.put_json(tape),
        worker_id=TapeReplayWorker.worker_id,
        manifest=_manifest(),
    )
    result = ReplayCoordinator(store, TapeReplayWorker()).run(plan)
    assert result.execution is not None
    assert result.execution.state == "integrity_failed"
    assert result.claimable is False
