from __future__ import annotations
import json
import os
import shlex
import uuid
from pathlib import Path

from breadboard.product.harness.lock import load_lock, lock_path, sha256_json
from breadboard.product.harness.resolution import compile_harness_source
from breadboard.product.operations.harness import (
    CreateHarnessRequest,
    ExplainHarnessRequest,
    GenerationPublicationPort,
    GetHarnessLockRequest,
    GetHarnessRequest,
    ListHarnessesRequest,
    LockHarnessRequest,
    PackageHarnessRequest,
    PublishHarnessOutcome,
    PublishHarnessRequest,
    UpdateHarnessRequest,
    ValidateHarnessRequest,
    create_harness,
    explain_harness,
    get_harness,
    get_harness_lock,
    list_harnesses as list_harnesses_operation,
    lock_harness,
    package_harness,
    publish_harness,
    update_harness,
    validate_harness,
)
from breadboard.product.operations.model import (
    OperationContext,
    OperationResult,
    from_exception,
    portable_ref,
)
from breadboard_engine.api.local_server import local_server
from .session import load_module_authority, load_module_input


def _w(a):
    return Path(getattr(a, "workspace", None) or Path.cwd()).expanduser().resolve()


def _p(a):
    return Path(a.PATH).expanduser().resolve()


def _ref(p, w):
    return portable_ref(p, w)


def _operation_context(a):
    workspace = _w(a)
    return OperationContext(
        workspace=workspace,
        reference_root=Path.cwd(),
    )


def init(a):
    return create_harness(
        CreateHarnessRequest(getattr(a, "out", None) or "."),
        _operation_context(a),
    )


def package(a):
    return package_harness(
        PackageHarnessRequest(a.PATH, a.out),
        _operation_context(a),
    )


def validate(a, command_name="validate"):
    return validate_harness(
        ValidateHarnessRequest(a.PATH),
        _operation_context(a),
        command_name=command_name,
    )


def explain(a):
    return explain_harness(
        ExplainHarnessRequest(a.PATH),
        _operation_context(a),
    )


def lock(a):
    return lock_harness(
        LockHarnessRequest(
            a.PATH,
            out=getattr(a, "out", None),
            check=getattr(a, "check", False),
        ),
        _operation_context(a),
    )


class _LocalGenerationPublicationAdapter:
    def publish(
        self,
        request: PublishHarnessRequest,
        context: OperationContext,
        effective_lock,
        source_path,
    ) -> PublishHarnessOutcome:
        from breadboard.product.runtime.generations import GenerationLifecycle

        publication = GenerationLifecycle(context.workspace).prepare_and_publish(
            request.target,
            effective_lock,
            portable_ref(source_path, context.workspace),
            request.expected_revision,
            request.request_id,
        )
        return PublishHarnessOutcome(
            target=publication.target,
            revision=publication.revision,
            generation_id=publication.generation_id,
            preparation_id=publication.preparation_id,
            request_id=publication.request_id,
        )


def publish(a):
    return publish_harness(
        PublishHarnessRequest(
            target=a.TARGET,
            lock_id=a.lock,
            expected_revision=a.expected_revision,
            request_id=a.request_id,
        ),
        _operation_context(a),
        _LocalGenerationPublicationAdapter(),
    )


def run(a):
    w = _w(a)
    target = getattr(a, "target", None)
    try:
        if target and getattr(a, "PATH", None):
            raise ValueError("harness run accepts either PATH or --target, not both")
        if target:
            a._effective_lock = None
            a._workspace = w
            a._lock_id = None
        else:
            if not getattr(a, "PATH", None):
                raise ValueError("harness run requires PATH unless --target is supplied")
            p = _p(a)
            lock_argument = getattr(a, "lock", None)
            requested_lock_path = (
                Path(lock_argument).expanduser().resolve() if lock_argument else p
            )
            effective_lock_path = (
                requested_lock_path
                if lock_argument or requested_lock_path.name.endswith(".lock.json")
                else lock_path(requested_lock_path)
            )
            explicit = bool(lock_argument or p.name.endswith(".lock.json"))
            lock, mp = load_lock(requested_lock_path, w, explicit=explicit)
            m = json.loads(mp.read_text())
            if (
                m.get("schema_version") != "bb.harness_lock_metadata.v2"
                or m.get("lock_id") != lock.generation_id
                or m.get("graph_hash") != lock.configuration_graph["graph_hash"]
            ):
                return OperationResult.failure(
                    ["harness", "run"],
                    5,
                    "lock_identity_mismatch",
                    "the retained Lock lacks matching complete-identity metadata",
                    "harness.run",
                )
            lock_action = f"breadboard harness lock {shlex.quote(str(p))}"
            if lock_argument:
                lock_action += f" --out {shlex.quote(str(requested_lock_path))}"
            if not explicit:
                c = compile_harness_source(p, w, getattr(a, "contained", False))
                if (
                    m.get("source_sha256") != sha256_json(c.resolved_author_dict())
                    or c.lock.generation_id != lock.generation_id
                ):
                    return OperationResult.failure(
                        ["harness", "run"],
                        5,
                        "lock_drift",
                        "mutable harness definition cannot run without a fresh lock",
                        "harness.run",
                        next_actions=[lock_action],
                    )
            a._effective_lock = lock
            a._lock_id = _ref(effective_lock_path, w)
        a._publication_target = target
        a._workspace = w
        if getattr(a, "local", False):
            try:
                with local_server(w) as server:
                    a.server = server
                    return _server(a)
            except ModuleNotFoundError as e:
                return OperationResult.failure(
                    ["harness", "run"],
                    6,
                    "local_backend_unavailable",
                    str(e),
                    "harness.run",
                    next_actions=[
                        "install BreadBoard with local runtime support or use --server"
                    ],
                    status="blocked",
                )
        return _server(a)
    except Exception as e:
        return from_exception(["harness", "run"], e, "harness.run")


def _server(a):
    try:
        import breadboard_sdk

        module_input_path = getattr(a, "module_input", None)
        module_input = (
            load_module_input(str(module_input_path))
            if module_input_path is not None
            else None
        )
        if module_input is not None and not module_input.final and getattr(a, "local", False):
            raise ValueError("non-final module input requires a persistent --server, not --local")
        task = None if module_input is not None else str(getattr(a, "task", None) or "List files")
        authority_path = getattr(a, "module_authority", None)
        module_authority = (
            load_module_authority(str(authority_path))
            if authority_path is not None
            else None
        )
        publication_target = getattr(a, "_publication_target", None)
        payload = (
            {"publication_target": publication_target}
            if publication_target is not None
            else {"lock_id": a._lock_id}
        )
        if module_input is not None:
            payload["module_input"] = module_input.to_dict()
        else:
            payload["task"] = task
        if module_authority is not None:
            payload["module_authority"] = module_authority.to_dict()
        auth_token = os.environ.get("BREADBOARD_API_TOKEN")
        if auth_token:
            c = breadboard_sdk.BreadBoardClient(
                a.server, auth_token=auth_token, timeout_s=120
            )
        else:
            c = breadboard_sdk.BreadBoardClient(a.server, timeout_s=120)
        started = c.start_session(
            payload,
            idempotency_key=uuid.uuid4().hex,
        )
        if not isinstance(started, dict) or not started.get("ok"):
            raise RuntimeError(f"session.start failed: {started!r}")
        session = started.get("data", {}).get("session", {})
        sid = str(session.get("session_id") or "")
        if not sid:
            raise RuntimeError("session.start returned no session identity")
        if module_input is not None and not module_input.final:
            return OperationResult.success(
                ["harness", "run"],
                {"session_id": sid},
                next_actions=[
                    f"breadboard session --server {shlex.quote(a.server)} get {shlex.quote(sid)}"
                ],
                stage="harness.run",
            )
        terminal = False
        for event in c.events_session(sid, follow=True):
            kind = (
                str(event.get("kind") or event.get("type") or "")
                if isinstance(event, dict)
                else ""
            )
            if kind in {"session.failed", "session.canceled", "error"}:
                payload = event.get("payload") if isinstance(event, dict) else event
                return OperationResult.failure(
                    ["harness", "run"],
                    4,
                    "session_execution_failed",
                    f"session execution failed: {payload}",
                    "harness.run",
                )
            if kind == "session.completed":
                terminal = True
                break
        if not terminal:
            return OperationResult.failure(
                ["harness", "run"],
                4,
                "session_stream_eof",
                "session event stream ended before a terminal event",
                "harness.run",
            )
        current = c.get_session(sid)
        view = (
            current.get("data", {}).get("session", {})
            if isinstance(current, dict)
            else {}
        )
        event_count = int(view.get("event_count") or 0)
        refs = []
        next_actions = []
        hashes = {
            "lock": str(view.get("effective_lock_hash") or ""),
            "task": str(view.get("task_hash") or ""),
        }
        hashes = {name: value for name, value in hashes.items() if value}
        if getattr(a, "local", False):
            from breadboard.product.runtime.session_store import session_event_path

            workspace_arg = shlex.quote(str(getattr(a, "workspace", None) or "."))
            refs = [_ref(session_event_path(a._workspace, sid), a._workspace)]
            next_actions = [f"breadboard session --workspace {workspace_arg} get {sid}"]
        return OperationResult.success(
            ["harness", "run"],
            {
                "session_id": sid,
                "record_count": event_count,
                "event_count": event_count,
            },
            refs=refs,
            hashes=hashes,
            next_actions=next_actions,
            stage="harness.run",
        )
    except ModuleNotFoundError as e:
        return OperationResult.failure(
            ["harness", "run"],
            6,
            "client_backend_unavailable",
            str(e),
            "harness.run",
            next_actions=["install BreadBoard SDK support"],
            status="blocked",
        )
    except Exception as e:
        return from_exception(["harness", "run"], e, "harness.run")


def list_harnesses(a):
    return list_harnesses_operation(
        ListHarnessesRequest(getattr(a, "directory", None)),
        _operation_context(a),
    )


def show(a, command_name="show"):
    return get_harness(
        GetHarnessRequest(a.PATH),
        _operation_context(a),
        command_name=command_name,
    )


def get(a):
    return get_harness(
        GetHarnessRequest(a.PATH),
        _operation_context(a),
    )


def update(a):
    return update_harness(
        UpdateHarnessRequest(
            a.PATH,
            definition=getattr(a, "document", None),
            source=getattr(a, "source", None),
        ),
        _operation_context(a),
    )


def get_lock(a):
    return get_harness_lock(
        GetHarnessLockRequest(a.PATH),
        _operation_context(a),
    )
