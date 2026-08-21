from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from breadboard.product.evidence.lanes import resolve_reference
from breadboard.product.evidence.replay import (
    ReplayCoordinator,
    ReplayJournal,
    ReplayManifest,
    ReplayManifestEntry,
    ReplayPlan,
    TapeReplayWorker,
)
from breadboard.product.evidence.workspace import BreadBoardWorkspace
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime.artifacts import ArtifactRef, ArtifactStore
from breadboard.product.runtime.events import Session
from scripts.e4_parity.validators.hash_utils import sha256_file, sha256_json

_STAGES = ("capture", "normalize", "replay", "compare", "claim")


class CandidateJourneyError(RuntimeError):
    pass


class _FixedClock:
    def __init__(self, value: str) -> None:
        self.value = value

    def now(self) -> str:
        return self.value


def _digest(value: Any) -> str:
    return sha256_json(value)


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CandidateJourneyError(f"{label} must be an object")
    return dict(value)


def _load_descriptor(root: Path, path: str, label: str) -> dict[str, Any]:
    resolved = resolve_reference(path, root=root)
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CandidateJourneyError(f"cannot load {label} descriptor: {error}") from error
    return _object(value, f"{label} descriptor")


def _descriptors(lane: Mapping[str, Any], root: Path) -> dict[str, dict[str, Any]]:
    references = _object(lane.get("references"), "lane references")
    required = {"harness", "target", "adapter", "source", "comparator", "policy"}
    if set(references) != required or any(
        not isinstance(value, str) for value in references.values()
    ):
        raise CandidateJourneyError("candidate lane references are incomplete")
    return {
        name: _load_descriptor(root, references[name], name) for name in sorted(required)
    }


def _relative(lane_id: str, name: str) -> Path:
    return Path(".breadboard") / "e4" / lane_id / name


def _read(workspace: BreadBoardWorkspace, lane_id: str, name: str) -> dict[str, Any]:
    try:
        return workspace.read_json(_relative(lane_id, name))
    except FileNotFoundError as error:
        raise CandidateJourneyError(
            f"candidate lane {lane_id!r} has no durable {name}"
        ) from error


def _publish(
    workspace: BreadBoardWorkspace,
    lane_id: str,
    name: str,
    payload: Mapping[str, Any],
) -> Path:
    relative = _relative(lane_id, name)
    destination = workspace.path(relative)
    if destination.exists():
        if workspace.read_json(relative) != dict(payload):
            raise CandidateJourneyError(
                f"candidate lane {lane_id!r} durable {name} differs from this run"
            )
        return destination
    return workspace.write_json(relative, dict(payload))


def _artifact_ref(value: Any, label: str) -> ArtifactRef:
    row = _object(value, label)
    if set(row) != {"digest", "size_bytes", "media_type"}:
        raise CandidateJourneyError(f"{label} must be an exact artifact reference")
    return ArtifactRef(
        digest=row["digest"],
        size_bytes=row["size_bytes"],
        media_type=row["media_type"],
    )


def _store(workspace: BreadBoardWorkspace) -> ArtifactStore:
    return ArtifactStore(workspace.path(Path(".breadboard") / "artifacts"))


def _capture_session(source: Mapping[str, Any]) -> tuple[Session, dict[str, Any]]:
    if source.get("schema_version") != "bb.e4.product_session_capture.v1":
        raise CandidateJourneyError("source descriptor has an unsupported schema_version")
    session_id = source.get("session_id")
    task = source.get("task")
    accepted_input = source.get("input")
    occurred_at = source.get("occurred_at")
    graph = _object(source.get("effective_lock"), "source effective_lock")
    if not all(
        isinstance(value, str) and value
        for value in (session_id, task, accepted_input, occurred_at)
    ):
        raise CandidateJourneyError("source session fields must be populated strings")
    session = Session.start(
        EffectiveHarnessLock._from_record(graph),
        task,
        session_id=session_id,
        clock=_FixedClock(occurred_at),
    )
    session.input(accepted_input)
    outcome = _object(source.get("outcome"), "source outcome")
    status = outcome.get("status")
    if status == "completed":
        session.complete(str(outcome.get("summary") or "completed"))
    elif status == "failed":
        error_code = outcome.get("error_code")
        detail = outcome.get("detail")
        if not all(isinstance(value, str) and value for value in (error_code, detail)):
            raise CandidateJourneyError("failed source outcome requires error_code and detail")
        session.fail(error_code, detail)
    else:
        raise CandidateJourneyError("source outcome.status must be completed or failed")
    return session, session.read_model.as_dict()


def _capture(
    lane: Mapping[str, Any],
    lock: Mapping[str, Any],
    descriptors: Mapping[str, Mapping[str, Any]],
    workspace: BreadBoardWorkspace,
) -> dict[str, Any]:
    lane_id = str(lane["lane_id"])
    adapter = descriptors["adapter"]
    if adapter.get("adapter_id") != "product-session-tape-v1":
        raise CandidateJourneyError(
            "candidate journey requires adapter_id product-session-tape-v1"
        )
    session, view = _capture_session(descriptors["source"])
    events = [event.as_dict() for event in session.events]
    steps = [
        {
            "kind": event["kind"],
            "span_id": f"session-event-{event['sequence']}",
            "parent_span_id": (
                None
                if event["sequence"] == 1
                else f"session-event-{event['sequence'] - 1}"
            ),
            "payload": event,
        }
        for event in events
    ]
    tape = {
        "schema_version": "bb.replay_tape.v1",
        "steps": steps,
        "outputs": {"events.json": events, "session.json": view},
    }
    store = _store(workspace)
    source_ref = store.put_json(tape)
    payload = {
        "schema_version": "bb.e4.candidate_capture.v1",
        "lane_id": lane_id,
        "lane_lock_sha256": lock["lock_sha256"],
        "source_session_id": view["session_id"],
        "product_pass": view["status"] == "completed",
        "session": view,
        "events_sha256": _digest(events),
        "tape_artifact": source_ref.as_dict(),
    }
    path = _publish(workspace, lane_id, "capture.json", payload)
    return {
        "stage": "capture",
        "outcome": "executed_pass",
        "returncode": 0,
        "report_ref": path.relative_to(workspace.root).as_posix(),
        "product_pass": payload["product_pass"],
    }


def _normalize(
    lane_id: str,
    capture: Mapping[str, Any],
    workspace: BreadBoardWorkspace,
) -> dict[str, Any]:
    payload = {
        "schema_version": "bb.e4.candidate_normalization.v1",
        "lane_id": lane_id,
        "mode": "identity",
        "capture_sha256": _digest(capture),
    }
    path = _publish(workspace, lane_id, "normalization.json", payload)
    return {
        "stage": "normalize",
        "outcome": "executed_pass",
        "returncode": 0,
        "report_ref": path.relative_to(workspace.root).as_posix(),
    }


def _plan(capture: Mapping[str, Any], descriptors: Mapping[str, Mapping[str, Any]]) -> ReplayPlan:
    source_ref = _artifact_ref(capture.get("tape_artifact"), "capture tape_artifact")
    return ReplayPlan(
        source_session_id=str(capture["source_session_id"]),
        input_artifact=source_ref,
        worker_id=TapeReplayWorker.worker_id,
        manifest=ReplayManifest(
            (
                ReplayManifestEntry("events.json", "application/json"),
                ReplayManifestEntry("session.json", "application/json"),
                ReplayManifestEntry("transcript.json", "application/json"),
            )
        ),
        options={
            "adapter_id": descriptors["adapter"].get("adapter_id"),
            "comparator_id": descriptors["comparator"].get("comparator_id"),
            "lane_lock_sha256": capture.get("lane_lock_sha256"),
        },
    )


def _completed_execution(
    workspace: BreadBoardWorkspace,
    plan: ReplayPlan,
) -> tuple[Any, ArtifactStore]:
    execution = ReplayJournal(workspace.root).try_read(plan.plan_id)
    if execution is None or not execution.claimable:
        raise CandidateJourneyError("candidate claim requires a claimable completed replay")
    store = _store(workspace)
    plan.manifest.validate_artifacts(execution.artifacts)
    for ref in execution.artifacts.values():
        store.read(ref)
    return execution, store


def _replay(
    lane_id: str,
    capture: Mapping[str, Any],
    descriptors: Mapping[str, Mapping[str, Any]],
    workspace: BreadBoardWorkspace,
) -> dict[str, Any]:
    plan = _plan(capture, descriptors)
    result = ReplayCoordinator(
        _store(workspace),
        TapeReplayWorker(),
        journal=ReplayJournal(workspace.root),
    ).run(plan)
    execution = result.execution
    if execution is None or not result.claimable:
        raise CandidateJourneyError(
            f"candidate replay is not claimable: {result.error or result.disposition}"
        )
    return {
        "stage": "replay",
        "outcome": "executed_pass",
        "returncode": 0,
        "report_ref": ReplayJournal(workspace.root)
        .run_path(plan.plan_id)
        .relative_to(workspace.root)
        .as_posix(),
        "plan_id": plan.plan_id,
        "disposition": result.disposition,
        "claimable": True,
    }


def _comparison(
    lane_id: str,
    capture: Mapping[str, Any],
    descriptors: Mapping[str, Mapping[str, Any]],
    workspace: BreadBoardWorkspace,
    *,
    publish: bool,
) -> dict[str, Any]:
    comparator = descriptors["comparator"]
    if comparator.get("comparator_id") != "exact-session-json-v1":
        raise CandidateJourneyError(
            "candidate journey requires comparator_id exact-session-json-v1"
        )
    plan = _plan(capture, descriptors)
    execution, store = _completed_execution(workspace, plan)
    observed = json.loads(store.read(execution.artifacts["session.json"]))
    expected = capture.get("session")
    expected_status = comparator.get("expected_status")
    if not isinstance(expected_status, str) or not expected_status:
        raise CandidateJourneyError("comparator expected_status must be populated")
    passed = observed == expected and observed.get("status") == expected_status
    payload = {
        "schema_version": "bb.e4.candidate_comparison.v1",
        "lane_id": lane_id,
        "plan_id": plan.plan_id,
        "comparator_id": comparator["comparator_id"],
        "capture_sha256": _digest(expected),
        "replay_sha256": _digest(observed),
        "product_pass": capture.get("product_pass") is True,
        "e4_pass": passed,
    }
    if publish:
        _publish(workspace, lane_id, "comparison.json", payload)
    return payload


def _compare(
    lane_id: str,
    capture: Mapping[str, Any],
    descriptors: Mapping[str, Mapping[str, Any]],
    workspace: BreadBoardWorkspace,
) -> dict[str, Any]:
    payload = _comparison(
        lane_id, capture, descriptors, workspace, publish=True
    )
    return {
        "stage": "compare",
        "outcome": "executed_pass" if payload["e4_pass"] else "executed_fail",
        "returncode": 0 if payload["e4_pass"] else 1,
        "report_ref": _relative(lane_id, "comparison.json").as_posix(),
        "product_pass": payload["product_pass"],
        "e4_pass": payload["e4_pass"],
        **({} if payload["e4_pass"] else {"detail": "exact session comparison failed"}),
    }


def _scope(
    lane: Mapping[str, Any],
    lock: Mapping[str, Any],
    descriptors: Mapping[str, Mapping[str, Any]],
    plan: ReplayPlan,
) -> dict[str, Any]:
    target = descriptors["target"]
    harness = descriptors["harness"]
    values = {
        "lane_id": lane["lane_id"],
        "lane_lock_sha256": lock["lock_sha256"],
        "plan_id": plan.plan_id,
        "source_session_id": plan.source_session_id,
        "target_family": target.get("family"),
        "target_version": target.get("version"),
        "runtime_id": harness.get("runtime_id"),
        "adapter_id": descriptors["adapter"].get("adapter_id"),
        "comparator_id": descriptors["comparator"].get("comparator_id"),
    }
    if any(not isinstance(value, str) or not value for value in values.values()):
        raise CandidateJourneyError("claim scope identity fields must be populated strings")
    return values


def reverify_candidate_claim(
    lane: Mapping[str, Any],
    lock: Mapping[str, Any],
    *,
    root: str | Path,
) -> dict[str, Any]:
    root_path = Path(root).expanduser().resolve()
    workspace = BreadBoardWorkspace(root_path)
    lane_id = str(lane["lane_id"])
    descriptors = _descriptors(lane, root_path)
    capture = _read(workspace, lane_id, "capture.json")
    claim = _read(workspace, lane_id, "claim.json")
    plan = _plan(capture, descriptors)
    execution, _store_instance = _completed_execution(workspace, plan)
    comparison = _comparison(
        lane_id, capture, descriptors, workspace, publish=False
    )
    expected_scope = _scope(lane, lock, descriptors, plan)
    valid = (
        claim.get("schema_version") == "bb.e4.candidate_exact_scope_claim.v1"
        and claim.get("accepted") is True
        and claim.get("scope") == expected_scope
        and claim.get("artifacts")
        == {path: ref.as_dict() for path, ref in execution.artifacts.items()}
        and claim.get("comparison_sha256") == _digest(comparison)
        and comparison["e4_pass"] is True
        and comparison["product_pass"] is True
    )
    if not valid:
        raise CandidateJourneyError("candidate exact-scope claim failed reverify")
    return {
        "ok": True,
        "lane_id": lane_id,
        "plan_id": plan.plan_id,
        "claim_ref": _relative(lane_id, "claim.json").as_posix(),
    }


def _claim(
    lane: Mapping[str, Any],
    lock: Mapping[str, Any],
    descriptors: Mapping[str, Mapping[str, Any]],
    workspace: BreadBoardWorkspace,
) -> dict[str, Any]:
    lane_id = str(lane["lane_id"])
    capture = _read(workspace, lane_id, "capture.json")
    comparison = _read(workspace, lane_id, "comparison.json")
    if capture.get("product_pass") is not True or comparison.get("e4_pass") is not True:
        raise CandidateJourneyError(
            "candidate claim requires independent product and E4 passes"
        )
    policy = descriptors["policy"]
    if policy.get("claim_enabled") is not True:
        raise CandidateJourneyError("candidate claim is disabled by policy")
    exclusions = policy.get("exclusions")
    if (
        not isinstance(exclusions, list)
        or not exclusions
        or any(not isinstance(value, str) or not value for value in exclusions)
    ):
        raise CandidateJourneyError("candidate claim requires explicit exclusions")
    plan = _plan(capture, descriptors)
    execution, _store_instance = _completed_execution(workspace, plan)
    payload = {
        "schema_version": "bb.e4.candidate_exact_scope_claim.v1",
        "accepted": True,
        "scope": _scope(lane, lock, descriptors, plan),
        "artifacts": {
            path: ref.as_dict() for path, ref in execution.artifacts.items()
        },
        "comparison_sha256": _digest(comparison),
        "exclusions": list(exclusions),
    }
    path = _publish(workspace, lane_id, "claim.json", payload)
    reverified = reverify_candidate_claim(lane, lock, root=workspace.root)
    return {
        "stage": "claim",
        "outcome": "executed_pass",
        "returncode": 0,
        "report_ref": path.relative_to(workspace.root).as_posix(),
        "claimable": True,
        "reverified": reverified["ok"],
    }


def _reuse_stage(
    name: str,
    lane: Mapping[str, Any],
    lock: Mapping[str, Any],
    descriptors: Mapping[str, Mapping[str, Any]],
    workspace: BreadBoardWorkspace,
) -> dict[str, Any]:
    lane_id = str(lane["lane_id"])
    capture = _read(workspace, lane_id, "capture.json")
    if capture.get("lane_lock_sha256") != lock.get("lock_sha256"):
        raise CandidateJourneyError("stored capture does not match the current lane lock")
    if name == "capture":
        relative = _relative(lane_id, "capture.json")
    elif name == "normalize":
        relative = _relative(lane_id, "normalization.json")
        _read(workspace, lane_id, "normalization.json")
    elif name == "replay":
        plan = _plan(capture, descriptors)
        _completed_execution(workspace, plan)
        relative = (
            ReplayJournal(workspace.root)
            .run_path(plan.plan_id)
            .relative_to(workspace.root)
        )
    elif name == "compare":
        persisted = _read(workspace, lane_id, "comparison.json")
        current = _comparison(
            lane_id, capture, descriptors, workspace, publish=False
        )
        if persisted != current or current.get("e4_pass") is not True:
            raise CandidateJourneyError("stored comparison failed current reverify")
        relative = _relative(lane_id, "comparison.json")
    else:
        reverify_candidate_claim(lane, lock, root=workspace.root)
        relative = _relative(lane_id, "claim.json")
        _read(workspace, lane_id, "claim.json")
    reuse_index = list(lane["reuse"]).index(name)
    return {
        "stage": name,
        "outcome": "reused_stored_result",
        "returncode": 0,
        "manifest_rule": f"/reuse/{reuse_index}",
        "reused_inputs": [
            {
                "path": relative.as_posix(),
                "sha256": sha256_file(workspace.path(relative)),
            }
        ],
        "report_ref": relative.as_posix(),
        "lock_sha256": lock["lock_sha256"],
    }


def run_candidate_journey(
    lane: Mapping[str, Any],
    lock: Mapping[str, Any],
    *,
    root: str | Path,
    stage: str,
) -> dict[str, Any]:
    if os.environ.get("BREADBOARD_ENABLE_E4_API") != "1":
        raise CandidateJourneyError(
            "candidate execution requires explicit BREADBOARD_ENABLE_E4_API=1"
        )
    if stage != "all" and stage not in _STAGES:
        raise CandidateJourneyError(f"unsupported candidate stage {stage!r}")
    root_path = Path(root).expanduser().resolve()
    workspace = BreadBoardWorkspace(root_path)
    descriptors = _descriptors(lane, root_path)
    lane_id = str(lane["lane_id"])
    execute = lane.get("execute")
    reuse = lane.get("reuse")
    if not isinstance(execute, (list, tuple)) or not isinstance(reuse, (list, tuple)):
        raise CandidateJourneyError("candidate lane execution declarations are invalid")
    declared = tuple(name for name in _STAGES if name in execute or name in reuse)
    if stage == "all":
        selected = declared
    elif stage not in declared:
        raise CandidateJourneyError(
            f"candidate stage {stage!r} is not declared for execution or reuse"
        )
    else:
        selected = (stage,)
    results: list[dict[str, Any]] = []
    for name in selected:
        if name in reuse:
            result = _reuse_stage(
                name, lane, lock, descriptors, workspace
            )
        else:
            capture = (
                _read(workspace, lane_id, "capture.json")
                if name != "capture"
                else None
            )
            if name == "capture":
                result = _capture(lane, lock, descriptors, workspace)
            elif name == "normalize":
                result = _normalize(lane_id, capture, workspace)
            elif name == "replay":
                result = _replay(lane_id, capture, descriptors, workspace)
            elif name == "compare":
                result = _compare(lane_id, capture, descriptors, workspace)
            else:
                result = _claim(lane, lock, descriptors, workspace)
            result.setdefault("manifest_rule", None)
            result.setdefault("reused_inputs", None)
            result.setdefault("lock_sha256", lock["lock_sha256"])
        results.append(result)
        if result["returncode"] != 0:
            break
    return {
        "ok": bool(results) and all(row["returncode"] == 0 for row in results),
        "lane_id": lane_id,
        "stages": results,
    }
