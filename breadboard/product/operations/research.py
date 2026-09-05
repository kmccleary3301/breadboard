"""Compare recorded Sessions through the durable child and execution-world owners."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
import zlib
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from breadboard.product.coordination.work_items import WorkItem, WorkItemRepository
from breadboard.product.harness.compile import HarnessCompilation
from breadboard.product.harness.lock import EffectiveHarnessLock, load_lock
from breadboard.product.harness.resolution import compile_harness_source
from breadboard.product.operations.harness import LockHarnessRequest, lock_harness
from breadboard.product.operations.model import (
    OperationContext,
    OperationResult,
    from_exception,
)
from breadboard.product.runtime.artifacts import (
    ArtifactRef,
    put_workspace_artifact,
    read_workspace_artifact,
    workspace_artifact_ref,
)
from breadboard.product.runtime.children import (
    RESEARCH_WORLD_WORKER_COMMAND,
    ChildSpec,
    DurableChildFactory,
    ProcessExecutionAdapter,
)
from breadboard.product.runtime.events import (
    AnnotationRecord,
    ProcessLock,
    Session,
    project_session_replay,
)
from breadboard.product.runtime.session_store import (
    create_session,
    load_session,
    mutate_session,
)
from breadboard.product.runtime.workflows import (
    ReplayableWorkflowController,
    WorkflowDefinition,
    WorkflowStep,
)
from breadboard_engine.api.cli_bridge.registry.registry_impl import SessionRegistry
from breadboard_engine.provider.contract_exchange import ProviderExchangeV2
from breadboard_engine.provider.contract_runtime import ProviderRuntimeContext
from breadboard_engine.provider.contract_wire import canonical_json
from breadboard_engine.provider.routing import provider_router
from breadboard_engine.provider.runtimes.openai.chat import OpenAIChatRuntime
from breadboard_engine.state.session_state import SessionState

_COMMAND = ("research", "compare")
_STAGE = "research.compare"
_MASK = ["/occurred_at", "/timestamp"]
_MAX_INPUT_BYTES = 8 * 1024 * 1024
_MAX_COMMAND_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class CompareResearchRequest:
    definition: str
    world: str
    generation: str
    projection: str
    compare: tuple[str, str]


class _Recording(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    definition: str = Field(min_length=1)
    workspace: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    request_ref: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class _Projection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    projector_version: str = Field(min_length=1)
    as_of: int | None = Field(default=None, ge=1)


class _WorldProblem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


class _WorldResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["completed", "failed", "unsupported"]
    exit_code: int | None
    stdout: str
    stderr: str
    problem: _WorldProblem | None
    execution_evidence: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class _PreparedComparison:
    definition: EffectiveHarnessLock
    generation: EffectiveHarnessLock
    world: dict[str, Any]
    task: str
    task_hash: str
    run_id: str
    command: tuple[str, ...]


# This program runs inside the selected world. It depends only on Python's
# standard library, not on a controller checkout or an installed BreadBoard.
_COMPARE_PROGRAM = r"""
import base64, json, sys, zlib
compressed = base64.b64decode(sys.argv[1], validate=True)
decoder = zlib.decompressobj()
raw = decoder.decompress(compressed, 8 * 1024 * 1024 + 1)
if len(raw) > 8 * 1024 * 1024 or not decoder.eof or decoder.unused_data:
    raise ValueError("invalid or oversized comparison input")
data = json.loads(raw)
if data["field_mask"] != ["/occurred_at", "/timestamp"]:
    raise ValueError("unsupported comparison field mask")
records = data["records"]
for record in records:
    for event in record["events"]:
        event.pop("occurred_at", None)
        event.pop("timestamp", None)
differences = []
def compare(left, right, path=""):
    if type(left) is not type(right):
        differences.append(path or "/")
    elif isinstance(left, dict):
        for key in sorted(left.keys() | right.keys()):
            child = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key not in left or key not in right:
                differences.append(child)
            else:
                compare(left[key], right[key], child)
    elif isinstance(left, list):
        if len(left) != len(right):
            differences.append(path)
        for index, (a, b) in enumerate(zip(left, right)):
            compare(a, b, path + "/" + str(index))
    elif left != right:
        differences.append(path or "/")
compare(records[0], records[1])
print(json.dumps({"equivalent": not differences, "differences": differences,
                  "projection": data["projection"], "records": records},
                 allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
"""


def _read_input(path: Path) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(_MAX_INPUT_BYTES + 1)
    if len(content) > _MAX_INPUT_BYTES:
        raise ValueError("research input exceeds 8 MiB")
    return content


def _generation(reference: str, context: OperationContext) -> EffectiveHarnessLock:
    path = context.resolve_path(reference)
    lock, metadata_path = load_lock(path, context.workspace, explicit=True)
    metadata_reference = (
        metadata_path.relative_to(context.workspace)
        if context.contained
        else metadata_path
    )
    metadata = json.loads(_read_input(context.resolve_path(metadata_reference)))
    source_ref = metadata["source_ref"]
    source = source_ref if context.contained else context.workspace / source_ref
    checked = lock_harness(
        LockHarnessRequest(
            source,
            path.relative_to(context.workspace) if context.contained else path,
            check=True,
        ),
        context,
    )
    if not checked.ok:
        raise ValueError("generation Lock differs from its authored source")
    return lock


def _recording_snapshot(
    reference: str,
    context: OperationContext,
    definition: HarnessCompilation,
    projection: _Projection,
    compilations: dict[Path, HarnessCompilation],
) -> dict[str, Any]:
    recording = _Recording.model_validate_json(
        _read_input(context.resolve_path(reference))
    )
    source = context.resolve_path(recording.definition)
    if source not in compilations:
        compilations[source] = compile_harness_source(
            source, context.workspace, context.contained
        )
    recorded_definition = compilations[source]
    if recorded_definition.lock != definition.lock:
        raise ValueError("recorded Definitions do not compile to the selected Lock")
    workspace = context.resolve_path(recording.workspace)
    session, _ = load_session(workspace, recording.session_id)
    if session.generation_sequence[0] != definition.lock["graph_hash"]:
        raise ValueError("recorded Session did not start under the selected Definition")
    projected = project_session_replay(
        session.events,
        as_of=projection.as_of,
        expected_projector_version=projection.projector_version,
    )
    selected_events = session.events[: projected.source.last_sequence]
    selected = Session.restore(selected_events, task=session.task)
    request_ref = workspace_artifact_ref(
        workspace, recording.request_ref, media_type="application/json"
    )
    if request_ref.size_bytes > _MAX_INPUT_BYTES:
        raise ValueError("recorded provider exchange exceeds 8 MiB")
    request_generation = session.generation_sequence[0]
    attached = False
    for event in selected_events:
        if event.kind == "session.reconfigured":
            request_generation = event.payload["effective_lock_hash"]
        if event.kind == "input.accepted" and any(
            row["digest"] == request_ref.digest for row in event.payload["attachments"]
        ):
            attached = True
            break
    if not attached:
        raise ValueError(
            "provider exchange is not attached to the selected Session prefix"
        )
    if request_generation != definition.lock["graph_hash"]:
        raise ValueError("recorded request requires its governing Definition")
    exchange = ProviderExchangeV2.from_dict(
        json.loads(read_workspace_artifact(workspace, request_ref))
    )
    if exchange.correlation.session_id != recording.session_id:
        raise ValueError("provider exchange belongs to a different Session")
    effective = definition.as_dict()
    descriptor, model = provider_router.get_runtime_descriptor(
        effective["providers"]["default_model"]
    )
    if (
        descriptor.runtime_id != "openai_chat"
        or exchange.provider.runtime_id != descriptor.runtime_id
        or exchange.provider.provider_id != descriptor.provider_id
        or exchange.provider.model != model
    ):
        raise ValueError(
            "recorded provider route has no matching exact request projector"
        )
    runtime_context = ProviderRuntimeContext(
        session_state=SessionState(str(workspace), "", effective),
        agent_config=effective,
        stream=exchange.request.stream,
        session_id=recording.session_id,
        input_id=exchange.correlation.input_id,
        turn_id=exchange.correlation.turn_id,
    )
    body = OpenAIChatRuntime(descriptor).project_request_body(
        model=model,
        messages=exchange.request.messages,
        tools=exchange.request.tools,
        stream=exchange.request.stream,
        context=runtime_context,
    )
    return {
        "events": [event.as_dict() for event in selected_events],
        "projection": {
            "value": projected.value.as_dict(),
            "projector_version": projected.projector_version,
            "source": asdict(projected.source),
            "as_of": projected.as_of,
        },
        "request_body": canonical_json(body),
        "generation_sequence": list(selected.generation_sequence),
        "trajectory_segments": [
            dict(segment) for segment in selected.trajectory_segments
        ],
        "effective_context": (
            None
            if selected.effective_context is None
            else selected.effective_context.decode("utf-8")
        ),
        "raw_fact_ids": list(selected.raw_fact_ids),
    }


def _prepare(
    request: CompareResearchRequest, context: OperationContext
) -> _PreparedComparison:
    if len(request.compare) != 2 or any(
        type(ref) is not str or not ref for ref in request.compare
    ):
        raise ValueError("compare requires an ordered pair of recorded-run references")
    source = context.resolve_path(request.definition)
    definition = compile_harness_source(source, context.workspace, context.contained)
    generation = _generation(request.generation, context)
    projection = _Projection.model_validate_json(
        _read_input(context.resolve_path(request.projection))
    )
    world = json.loads(_read_input(context.resolve_path(request.world)))
    if not isinstance(world, dict) or world.get("field_mask") != _MASK:
        raise ValueError("world must declare the exact occurred_at/timestamp mask")
    python = world.get("python")
    if type(python) is not str or not python:
        raise ValueError("world must declare its Python executable")
    compilations = {source: definition}
    records = [
        _recording_snapshot(reference, context, definition, projection, compilations)
        for reference in request.compare
    ]
    payload = canonical_json(
        {"field_mask": _MASK, "projection": projection.model_dump(), "records": records}
    ).encode()
    if len(payload) > _MAX_INPUT_BYTES:
        raise ValueError("recorded comparison exceeds 8 MiB")
    encoded = base64.b64encode(zlib.compress(payload)).decode("ascii")
    if len(encoded) > _MAX_COMMAND_BYTES:
        raise ValueError("compressed comparison exceeds the 64 KiB command bound")
    task = canonical_json(
        {
            "definition": definition.lock["graph_hash"],
            "generation": generation["graph_hash"],
            "world": world,
            "comparison": json.loads(payload),
        }
    )
    digest = hashlib.sha256(task.encode()).hexdigest()
    return _PreparedComparison(
        definition=definition.lock,
        generation=generation,
        world=world,
        task=task,
        task_hash="sha256:" + digest,
        run_id="research-" + digest,
        command=(python, "-c", _COMPARE_PROGRAM, encoded),
    )


def _parent_work(
    repository: WorkItemRepository, run_id: str, *, create: bool
) -> WorkItem:
    work_id = run_id + ":work"
    if not repository.read(work_id):
        if not create:
            raise ValueError("research run is missing its durable Work Item")
        work = WorkItem.create(
            "recorded research comparison", work_item_id=work_id, repository=repository
        )
    else:
        work = WorkItem.restore(repository, work_id)
    if work.read_model.status == "ready":
        work.acquire_lease("research.compare", lease_id=run_id + ":lease")
    if work.read_model.status == "leased":
        work.start_attempt(
            run_id, lease_id=run_id + ":lease", attempt_id=run_id + ":attempt"
        )
    return work


def _completed_report(workspace: Path, session: Session) -> ArtifactRef:
    outcome = session.read_model.terminal_outcome
    if session.read_model.status != "completed" or outcome is None:
        raise ValueError("research run has no completed report")
    ref = workspace_artifact_ref(
        workspace, outcome["summary"], media_type="application/json"
    )
    read_workspace_artifact(workspace, ref)
    return ref


def _result(run_id: str, report: ArtifactRef) -> OperationResult:
    return OperationResult.success(
        _COMMAND,
        {"run_id": run_id, "report_id": report.digest},
        refs=(report.digest,),
        stage=_STAGE,
    )


def _run_comparison(
    prepared: _PreparedComparison,
    context: OperationContext,
    factory: DurableChildFactory,
) -> OperationResult:
    internal = replace(context, path_policy="contained-public")
    lock_path = internal.resolve_path(f".breadboard/{prepared.run_id}.lock")
    with ProcessLock(lock_path):
        try:
            parent, _ = load_session(context.workspace, prepared.run_id)
        except FileNotFoundError:
            parent, _ = create_session(
                context.workspace,
                Session.start(
                    prepared.definition, prepared.task, session_id=prepared.run_id
                ),
            )
        if parent.read_model.task_hash != prepared.task_hash:
            raise ValueError("research run identity conflicts with its retained task")
        work = _parent_work(
            factory.repository,
            prepared.run_id,
            create=parent.read_model.status == "running",
        )
        if parent.read_model.status == "completed":
            report = _completed_report(context.workspace, parent)
            if work.read_model.status != "completed":
                work.complete(report.digest, attempt_id=prepared.run_id + ":attempt")
            return _result(prepared.run_id, report)
        if parent.read_model.status in {"failed", "canceled"}:
            return OperationResult.failure(
                _COMMAND,
                4,
                "research_run_terminal",
                "the retained research run did not complete",
                _STAGE,
                data={"run_id": prepared.run_id},
            )

        def prepare_parent(session: Session) -> None:
            if session.read_model.status == "paused":
                session.resume()
            if session.pinned_generation_id != prepared.generation["graph_hash"]:
                if factory.child_states(
                    parent_work_item_id=work.read_model.work_item_id
                ):
                    raise ValueError(
                        "cannot adopt a research generation with active child work"
                    )
                session.adopt_generation(
                    prepared.generation, "research comparison generation"
                )

        mutate_session(context.workspace, prepared.run_id, prepare_parent)
        task = canonical_json(
            {
                "world": prepared.world,
                "request_id": prepared.run_id,
                "workspace": str(context.workspace),
                "command": list(prepared.command),
            }
        )
        controller = ReplayableWorkflowController(
            factory,
            workflow_id=prepared.run_id + ":workflow",
            parent_session_id=prepared.run_id,
            root_session_id=prepared.run_id,
            parent_work_item_id=work.read_model.work_item_id,
            definition=WorkflowDefinition(
                (
                    WorkflowStep(
                        "compare",
                        ChildSpec(
                            "compare recorded Sessions",
                            task,
                            prepared.generation,
                            "research.compare",
                            ProcessExecutionAdapter.family,
                        ),
                    ),
                )
            ),
        )
        while True:
            decision = controller.advance()
            if decision.action in {"complete", "fail", "cancel"}:
                break
            time.sleep(0.05)
        children = factory.child_states(
            parent_work_item_id=work.read_model.work_item_id
        )
        if len(children) != 1 or children[0].terminal_count != 1:
            raise ValueError("research workflow has no unique settled child")
        state = children[0]
        if len(state.result_refs) != 1:
            return OperationResult.failure(
                _COMMAND,
                4,
                "research_world_result_unavailable",
                "the world child settled without one durable result",
                _STAGE,
                data={"run_id": prepared.run_id},
            )
        retained = workspace_artifact_ref(context.workspace, state.result_refs[0])
        result = _WorldResult.model_validate_json(
            read_workspace_artifact(context.workspace, retained)
        )
        if (
            decision.action != "complete"
            or result.status != "completed"
            or result.exit_code != 0
        ):
            code = (
                result.problem.code
                if result.problem is not None
                else "research_world_failed"
            )
            message = (
                result.problem.message
                if result.problem is not None
                else "the selected world did not complete the comparison"
            )

            def fail(session: Session) -> None:
                if session.read_model.status not in {"completed", "failed", "canceled"}:
                    session.fail(code, message)

            mutate_session(context.workspace, prepared.run_id, fail)
            if work.read_model.status not in {"completed", "failed", "canceled"}:
                work.fail(code, message)
            return OperationResult.failure(
                _COMMAND, 4, code, message, _STAGE, data={"run_id": prepared.run_id}
            )
        report_body = result.stdout.encode("utf-8")
        report_value = json.loads(report_body)
        if (
            not isinstance(report_value, dict)
            or set(report_value)
            != {"equivalent", "differences", "projection", "records"}
            or type(report_value["equivalent"]) is not bool
            or not isinstance(report_value["differences"], list)
            or not isinstance(report_value["records"], list)
            or len(report_value["records"]) != 2
        ):
            raise ValueError("world returned an invalid comparison report")
        report = put_workspace_artifact(
            context.workspace, report_body, media_type="application/json"
        )

        def finish(session: Session) -> None:
            if session.read_model.status == "paused":
                session.resume()
            message_id = prepared.run_id + ":report"
            trajectory_id = session.read_model.trajectory_segment_id
            session.assistant_message(
                result.stdout, message_id=message_id, trajectory_id=trajectory_id
            )
            session.annotate(
                AnnotationRecord(
                    annotation_id=prepared.run_id + ":comparison",
                    message_id=message_id,
                    trajectory_id=trajectory_id,
                    label="equivalent" if report_value["equivalent"] else "different",
                    author="research.compare",
                    generation=session.pinned_generation_id,
                )
            )
            session.complete(report.digest)

        mutate_session(context.workspace, prepared.run_id, finish)
        work.complete(report.digest, attempt_id=prepared.run_id + ":attempt")
        return _result(prepared.run_id, report)


async def compare_research(
    request: CompareResearchRequest,
    context: OperationContext,
    *,
    registry: SessionRegistry | None = None,
) -> OperationResult:
    try:
        prepared = await asyncio.to_thread(_prepare, request, context)
        internal = replace(context, path_policy="contained-public")
        repository = WorkItemRepository(
            internal.resolve_path(".breadboard/work_items.jsonl")
        )
        retained_registry = (
            registry
            if registry is not None
            else SessionRegistry(
                state_root=internal.resolve_path(".breadboard/session_state")
            )
        )
        factory = DurableChildFactory.with_async_registry(
            context.workspace,
            registry=retained_registry,
            repository=repository,
            adapters=(ProcessExecutionAdapter(command=RESEARCH_WORLD_WORKER_COMMAND),),
        )
        return await asyncio.to_thread(_run_comparison, prepared, context, factory)
    except Exception as error:
        return from_exception(_COMMAND, error, _STAGE)
