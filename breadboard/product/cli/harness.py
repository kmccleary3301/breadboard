from __future__ import annotations
import json
import shlex
from pathlib import Path

from breadboard.product.harness.lock import load_lock, lock_path, sha256_json
from breadboard.product.harness.resolution import compile_harness_source
from breadboard.product.operations.harness import (
    CreateHarnessRequest,
    ExplainHarnessRequest,
    GetHarnessLockRequest,
    GetHarnessRequest,
    ListHarnessesRequest,
    LockHarnessRequest,
    UpdateHarnessRequest,
    ValidateHarnessRequest,
    create_harness,
    explain_harness,
    get_harness,
    get_harness_lock,
    list_harnesses as list_harnesses_operation,
    lock_harness,
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


def run(a):
    p, w = _p(a), _w(a)
    try:
        lock_argument = getattr(a, "lock", None)
        requested_lock_path = (
            Path(lock_argument).expanduser().resolve() if lock_argument else p
        )
        effective_lock_path = (
            requested_lock_path
            if lock_argument or requested_lock_path.name.endswith(".lock.json")
            else lock_path(requested_lock_path)
        )
        lock, mp = load_lock(requested_lock_path, w, explicit=bool(lock_argument))
        c = compile_harness_source(p, w, getattr(a, "contained", False))
        m = json.loads(mp.read_text())
        lock_action = f"breadboard harness lock {shlex.quote(str(p))}"
        if lock_argument:
            lock_action += f" --out {shlex.quote(str(requested_lock_path))}"
        if (
            m.get("source_sha256") != sha256_json(c.resolved_author_dict())
            or m.get("graph_hash") != lock["graph_hash"]
            or c.lock.as_dict() != lock.as_dict()
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
        a._workspace = w
        a._lock_id = _ref(effective_lock_path, w)
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

        task = str(getattr(a, "task", None) or "List files")
        c = breadboard_sdk.BreadBoardClient(a.server, timeout_s=120)
        started = c.start_session(
            {"lock_id": a._lock_id, "task": task},
            idempotency_key=sha256_json({"lock_id": a._lock_id, "task": task}),
        )
        if not isinstance(started, dict) or not started.get("ok"):
            raise RuntimeError(f"session.start failed: {started!r}")
        session = started.get("data", {}).get("session", {})
        sid = str(session.get("session_id") or "")
        if not sid:
            raise RuntimeError("session.start returned no session identity")
        terminal = False
        for event in c.events_session(sid):
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
