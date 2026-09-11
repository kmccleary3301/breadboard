from __future__ import annotations

import threading
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from breadboard.modules.author import (
    ChildFailed, ChildHandle, ChildOutput, ChildSucceeded, ChildTarget,
    InputEnvelope, ModuleInput, OutputEnvelope,
)
from breadboard.modules.authority import AdmissionGrant, AuthorityDeclaration
from breadboard.modules.transport import RequestKey
from breadboard_engine.api.cli_bridge.author_children import AuthorChildren, _Backend, _ChildRun
from breadboard_engine.api.cli_bridge.author_runtime import ModuleDisposal, ModuleExecutionError
from breadboard_engine.api.cli_bridge.models import SessionStatus
from breadboard_engine.api.cli_bridge.registry import SessionRecord


class _Runtime:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.ready = threading.Event()
        self.closed = False
        self.emit = None

    def prepare(self):
        pass

    def execute(self, envelope, *, input_id, turn_id):
        output = next(self.outputs)
        self.ready.set()
        if output is not None and self.emit is not None:
            self.emit(output, None, "child-module", 0, envelope.final)
        return output

    def close(self, reason="closed"):
        self.closed = True
        return ModuleDisposal("confirmed_absent", (), ())


def _run(runtime, initial):
    handle = ChildHandle(
        child_work_id="child-work", child_generation_id="generation",
        child_instance_id="child-instance", child_attempt_id="attempt",
        parent_work_id="parent-work", child_label="child",
    )
    target = ChildTarget(
        label="child", target="child-module", contract_id="only-a",
        input_schema_ids=("input.a",), output_schema_ids=("output.a",),
    )
    run = _ChildRun(
        runtime=runtime, handle=handle, target=target,
        execution_target_ref="child-ref", initial_input=initial,
    )
    runtime.emit = run.emit_output
    return run


def test_follow_up_input_respects_edge_before_admission_and_execution():
    initial = InputEnvelope("input.a", 0, b"{}", False)
    runtime = _Runtime([None, OutputEnvelope("output.a", b"accepted")])
    run = _run(runtime, initial)
    children = AuthorChildren(SimpleNamespace(require_live=lambda: None), SimpleNamespace())
    backend = children._backend

    class AdmissionBoundary:
        def submit_input(self, *args, **kwargs):
            raise AssertionError("unadmitted schema reached durable admission")

    children._factory = AdmissionBoundary()
    with pytest.raises(ModuleExecutionError) as refusal:
        children._submit_input({
            "handle": asdict(run.handle),
            "input": ModuleInput("input.a", b"{}", False).to_dict(),
        })
    assert refusal.value.code == "child_denied"
    backend._runs["child-ref"] = run
    backend._runs_by_handle[run.handle] = run
    run.thread.start()
    try:
        assert runtime.ready.wait(timeout=2)
        with pytest.raises(ModuleExecutionError) as refusal:
            children._submit_input({
                "handle": asdict(run.handle),
                "input": ModuleInput("input.b", b"{}", False).to_dict(),
            })
        assert refusal.value.code == "schema_mismatch"
        with pytest.raises(ModuleExecutionError) as refusal:
            backend.submit_input(
                {"ref": "child-ref"}, InputEnvelope("input.b", 1, b"{}", False),
                scope_fence=None,
            )
        assert refusal.value.code == "schema_mismatch"
        backend.submit_input(
            {"ref": "child-ref"}, InputEnvelope("input.a", 1, b"{}", True),
            scope_fence=None,
        )
        item = run.items.get(timeout=2)
        assert isinstance(item, ChildSucceeded)
        assert item.output.body == b"accepted"
    finally:
        run.inputs.put(None)
        run.thread.join(timeout=2)
    assert not run.thread.is_alive()


@pytest.mark.parametrize("final", [False, True])
@pytest.mark.parametrize("schema", ["output.a", "output.b"])
def test_child_outputs_respect_edge_before_stream_delivery(final, schema):
    initial = InputEnvelope("input.a", 0, b"{}", final)
    runtime = _Runtime([OutputEnvelope(schema, b"result")])
    run = _run(runtime, initial)
    run.inputs.put(None)
    run._drive(initial)
    item = run.items.get_nowait()
    if schema == "output.b":
        assert isinstance(item, ChildFailed)
        assert item.code == "schema_mismatch"
        assert runtime.closed
    else:
        assert isinstance(item, ChildSucceeded if final else ChildOutput)
        assert item.output.body == b"result"
    assert run.items.empty()


def test_child_stream_excludes_nested_dependency_outputs():
    root_key = RequestKey(
        worker_session_id="child-instance", request_id="step",
        generation_id="sha256:" + "a" * 64, instance_id="root-instance",
        work_id="child-work", attempt_id="attempt", authority_epoch=1,
    )
    dependency_key = replace(root_key, instance_id="dependency-instance")
    output = OutputEnvelope("output.a", b"root result")

    class NestedRuntime(_Runtime):
        def worker(self, binding):
            return SimpleNamespace(key=root_key)

        def execute(self, envelope, *, input_id, turn_id):
            for schema in ("output.a", "output.b"):
                self.emit(
                    OutputEnvelope(schema, b"dependency result"),
                    dependency_key, "same-package", 0, False,
                )
            self.emit(output, root_key, "same-package", 0, True)
            return output

    runtime = NestedRuntime([])
    record = SessionRecord(session_id="child-instance", status=SessionStatus.RUNNING)
    owner = SimpleNamespace(
        registry_call=lambda operation, session_id: {"child-instance": record}[session_id],
        captured=SimpleNamespace(materialization=SimpleNamespace(
            lock=SimpleNamespace(generation_id=root_key.generation_id),
        )),
        grant=AdmissionGrant(
            grant_id="grant", authority_epoch=1, declaration=AuthorityDeclaration(),
        ),
        record=SimpleNamespace(session_id="parent-instance"),
    )

    def child_runtime(record, *, emit_output, parent_fence):
        runtime.emit = emit_output
        return runtime

    backend = _Backend(SimpleNamespace(owner=owner, _child_runtime=child_runtime))
    handle = ChildHandle(
        child_work_id="child-work", child_generation_id=root_key.generation_id,
        child_instance_id="child-instance", child_attempt_id="attempt",
        parent_work_id="parent-work", child_label="child",
    )
    backend.start(
        target=ChildTarget(
            label="child", target="child-module", contract_id="only-a",
            input_schema_ids=("input.a",), output_schema_ids=("output.a",),
        ),
        module_binding="root-binding", handle=handle,
        initial_input=InputEnvelope("input.a", 0, b"{}", True),
        execution_target_ref="child-ref", scope_fence=lambda: None,
    )
    run = backend._runs["child-ref"]
    run.thread.join(timeout=2)
    assert not run.thread.is_alive()
    item = run.items.get_nowait()
    assert isinstance(item, ChildSucceeded)
    assert item.output.body == b"root result"
    assert run.items.empty()
