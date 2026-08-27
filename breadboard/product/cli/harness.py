from __future__ import annotations
import contextlib
import json
import os
import shlex
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import yaml

from breadboard.product.harness.lock import (
    load_lock,
    lock_metadata_path,
    lock_path,
    sha256_json,
)
from breadboard.product.harness.resolution import compile_harness_source
from breadboard.product.harness.templates import (
    DAILY_DRIVER_MODEL_ROLES_NAME,
    DAILY_DRIVER_PROMPT_BUNDLE_PATH,
    DAILY_DRIVER_TEMPLATE_NAME,
    daily_driver_model_roles_path,
    daily_driver_prompt_path,
    daily_driver_template_path,
)
from breadboard.product.operations.harness import (
    ExplainHarnessRequest,
    GetHarnessLockRequest,
    GetHarnessRequest,
    ListHarnessesRequest,
    ValidateHarnessRequest,
    explain_harness,
    get_harness,
    get_harness_lock,
    list_harnesses as list_harnesses_operation,
    validate_harness,
)
from breadboard.product.operations.model import (
    OperationContext,
    OperationResult,
    from_exception,
    portable_ref,
)


def _w(a):
    return Path(getattr(a, "workspace", None) or Path.cwd()).expanduser().resolve()


def _p(a):
    return Path(a.PATH).expanduser().resolve()


def _ref(p, w):
    return portable_ref(p, w)


def _write(p, x):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(x, sort_keys=True, indent=2) + "\n")


_INIT_LOCK = threading.RLock()


def _path_identity(p):
    stat = os.lstat(p)
    return stat.st_dev, stat.st_ino


def _remove_published(p, identity):
    try:
        if _path_identity(p) == identity:
            p.unlink()
    except FileNotFoundError:
        pass


def _rollback_published(published):
    for p, identity in reversed(published):
        _remove_published(p, identity)


def _publish_seed(p, content):
    temporary = p.with_name(f".{p.name}.{os.urandom(8).hex()}.tmp")
    descriptor = None
    published = None
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        identity = _path_identity(temporary)
        try:
            os.link(temporary, p)
        except FileExistsError:
            return None
        published = identity
        return identity
    except BaseException:
        if published is not None:
            _remove_published(p, published)
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except BaseException:
            if published is not None:
                _remove_published(p, published)
            raise


def _seed_mismatch(p, content):
    return (
        p.is_symlink() or p.exists() and (not p.is_file() or p.read_bytes() != content)
    )


def _init_result(h, q, r, w):
    refs = [_ref(h, w), _ref(q, w), _ref(r, w)]
    return OperationResult.success(
        ["harness", "init"],
        {"path": refs[0], "prompt_path": refs[1], "model_roles_path": refs[2]},
        refs,
        stage="harness.init",
    )


def daily_driver_bundle_paths(directory):
    d = Path(directory)
    return (
        d / DAILY_DRIVER_TEMPLATE_NAME,
        d / DAILY_DRIVER_PROMPT_BUNDLE_PATH,
        d / DAILY_DRIVER_MODEL_ROLES_NAME,
    )


def init(a):
    w = _w(a)
    d = Path(a.out or ".").expanduser()
    try:
        profile_source = daily_driver_template_path()
        prompt_source = daily_driver_prompt_path()
        roles_source = daily_driver_model_roles_path()
        h, q, r = daily_driver_bundle_paths(d)
        seeds = (
            (h, profile_source.read_bytes()),
            (q, prompt_source.read_bytes()),
            (r, roles_source.read_bytes()),
        )
        d.mkdir(parents=True, exist_ok=True)
        with _INIT_LOCK:
            if any(_seed_mismatch(p, content) for p, content in seeds):
                return OperationResult.failure(
                    ["harness", "init"],
                    2,
                    "path_exists",
                    "refusing to overwrite existing harness bundle",
                    "harness.init",
                )
            published = []
            try:
                for p, content in seeds:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    if not p.exists():
                        if identity := _publish_seed(p, content):
                            published.append((p, identity))
                if any(_seed_mismatch(p, content) for p, content in seeds):
                    _rollback_published(published)
                    return OperationResult.failure(
                        ["harness", "init"],
                        2,
                        "path_exists",
                        "refusing to overwrite existing harness bundle",
                        "harness.init",
                    )
            except BaseException:
                _rollback_published(published)
                raise
        return _init_result(h, q, r, w)
    except Exception as e:
        return from_exception(["harness", "init"], e, "harness.init")


def _operation_context(a):
    workspace = _w(a)
    return OperationContext(
        workspace=workspace,
        reference_root=Path.cwd(),
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
    p, w = _p(a), _w(a)
    target = lock_path(p, getattr(a, "out", None))
    try:
        c = compile_harness_source(p, w, getattr(a, "contained", False))
        meta = {
            "schema_version": "bb.harness_lock_metadata.v1",
            "source_ref": _ref(p, w),
            "source_sha256": sha256_json(c.resolved_author_dict()),
            "graph_hash": c.lock["graph_hash"],
        }
        if getattr(a, "check", False):
            if not target.exists() or not lock_metadata_path(target).exists():
                return OperationResult.failure(
                    ["harness", "lock"],
                    5,
                    "lock_missing",
                    "lock is missing",
                    "harness.lock",
                )
            if (
                json.loads(target.read_text()) != c.lock.as_dict()
                or json.loads(lock_metadata_path(target).read_text()) != meta
            ):
                return OperationResult.failure(
                    ["harness", "lock"],
                    5,
                    "lock_drift",
                    "harness definition changed after lock",
                    "harness.lock",
                    next_actions=[f"breadboard harness lock {_ref(p, w)}"],
                )
            return OperationResult.success(
                ["harness", "lock"],
                {
                    "path": _ref(target, w),
                    "graph_hash": meta["graph_hash"],
                    "checked": True,
                },
                [_ref(target, w)],
                {"graph": meta["graph_hash"]},
                stage="harness.lock",
            )
        _write(target, c.lock.as_dict())
        _write(lock_metadata_path(target), meta)
        next_action = f"breadboard harness run {shlex.quote(str(p))} --local"
        if target.resolve() != lock_path(p).resolve():
            next_action += f" --lock {shlex.quote(str(target.resolve()))}"
        return OperationResult.success(
            ["harness", "lock"],
            {"path": _ref(target, w), "graph_hash": meta["graph_hash"]},
            [_ref(target, w)],
            {"graph": meta["graph_hash"], "source": meta["source_sha256"]},
            [next_action],
            "harness.lock",
        )
    except Exception as e:
        return from_exception(["harness", "lock"], e, "harness.lock")


@contextlib.contextmanager
def _local_server(workspace: Path) -> Iterator[str]:
    import uvicorn
    from breadboard_engine.api.cli_bridge.app import create_app

    settings = {
        "BREADBOARD_LEGACY_ROUTES": "0",
        "BREADBOARD_ENABLE_PUBLIC_API": "1",
        "BREADBOARD_ENABLE_E4_API": "0",
        "BREADBOARD_PUBLIC_WORKSPACE": str(workspace),
        "RAY_SCE_LOCAL_MODE": "1",
    }
    previous = {name: os.environ.get(name) for name in settings}
    os.environ.update(settings)
    listener = None

    def restore_environment():
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    try:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(),
                host="127.0.0.1",
                port=int(listener.getsockname()[1]),
                log_level="critical",
                access_log=False,
            )
        )
    except BaseException:
        if listener is not None:
            listener.close()
        restore_environment()
        raise

    def serve():
        server.run(sockets=[listener])

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        restore_environment()
        raise RuntimeError("local create_app server did not start")
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        restore_environment()
        if thread.is_alive():
            raise RuntimeError("local create_app server did not stop")


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
                with _local_server(w) as server:
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
    p, w = _p(a), _w(a)
    temporary = None
    try:
        document = getattr(a, "document", None)
        source = getattr(a, "source", None)
        if document is None:
            if not source:
                return OperationResult.failure(
                    ["harness", "update"],
                    2,
                    "update_input_required",
                    "harness update requires --from or a definition",
                    "harness.update",
                )
            document = yaml.safe_load(Path(source).expanduser().read_text())
        if not isinstance(document, dict):
            raise ValueError("harness definition must be a mapping")
        temporary = p.with_name(f".{p.name}.{os.urandom(8).hex()}.tmp")
        if not p.is_file():
            raise FileNotFoundError(f"harness definition not found: {_ref(p, w)}")
        temporary.write_text(yaml.safe_dump(document, sort_keys=False))
        compile_harness_source(temporary, w, getattr(a, "contained", False))
        os.replace(temporary, p)
        return validate(a, "update")
    except Exception as e:
        return from_exception(["harness", "update"], e, "harness.update")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def get_lock(a):
    return get_harness_lock(
        GetHarnessLockRequest(a.PATH),
        _operation_context(a),
    )
