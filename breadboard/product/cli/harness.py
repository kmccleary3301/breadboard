from __future__ import annotations
import contextlib
import json
import os
import shlex
import socket
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
import yaml
from jsonschema.exceptions import SchemaError
from breadboard.product.harness.compile import HarnessCompilation,HarnessReferenceMissingError,compile_harness_definition
from breadboard.product.harness.lock import EffectiveHarnessLock,sha256_json
from breadboard.product.harness.templates import (
    DAILY_DRIVER_MODEL_ROLES_NAME,
    DAILY_DRIVER_PROMPT_BUNDLE_PATH,
    DAILY_DRIVER_TEMPLATE_NAME,
    daily_driver_model_roles_path,
    daily_driver_prompt_path,
    daily_driver_template_path,
    load_daily_driver_model_roles,
)
from breadboard.product.harness.validate import HarnessDefinitionValidationError,load_harness_definition,parse_harness_definition,validate_harness_document_domain
from .result import CliResult,from_exception,portable_ref
DEFAULT_PROFILE_ID = Path(DAILY_DRIVER_TEMPLATE_NAME).stem
DEFAULT_PROFILE_DEFINITION_REF = (
    Path("agent_configs/templates") / DAILY_DRIVER_TEMPLATE_NAME
).as_posix()


class HarnessContainmentError(PermissionError):
    """Raised when a harness source or resource escapes its allowed root."""

class HarnessResourceInvalidError(ValueError):
    """Raised when a declared prompt resource is not usable text."""


class DefaultProfileResolutionError(RuntimeError):
    error_code = "default_profile_invalid"
    exit_code = 2
    hint = (
        "Reinstall BreadBoard from a complete trusted distribution, "
        "then retry."
    )


class DefaultProfileUnavailableError(DefaultProfileResolutionError):
    error_code = "default_profile_unavailable"
    exit_code = 3


class DefaultProfileInvalidError(DefaultProfileResolutionError):
    pass

def _contained_target(candidate:Path,root:Path,label:str,*,strict:bool)->Path:
    canonical_root=root.resolve()
    try:
        relative=candidate.relative_to(root)
    except ValueError as error:
        raise HarnessContainmentError(
            f"{label} must remain within workspace"
        ) from error
    if any(
        root.joinpath(*relative.parts[:index]).is_symlink()
        for index in range(1,len(relative.parts)+1)
    ):
        raise HarnessContainmentError(
            f"{label} cannot traverse a symlink"
        )
    try:
        target=candidate.resolve(strict=strict)
    except RuntimeError as error:
        raise HarnessContainmentError(
            f"{label} cannot traverse a symlink"
        ) from error
    try:target.relative_to(canonical_root)
    except ValueError as error:
        raise HarnessContainmentError(
            f"{label} must remain within workspace"
        ) from error
    return target

def _w(a):return Path(getattr(a,"workspace",None) or Path.cwd()).expanduser().resolve()
def _p(a):return Path(a.PATH).expanduser().resolve()
def _ref(p,w):return portable_ref(p,w)
def _doc(p):
    x=yaml.safe_load(p.read_text())
    if not isinstance(x,dict):raise ValueError("harness definition must be a mapping")
    if findings:=validate_harness_document_domain(x):raise HarnessDefinitionValidationError(findings)
    return x
def _prompt_resources(c,paths,w,contained):
    sources={
        layer["layer_id"]:layer.get("source_ref")
        for layer in c.lock["source_layers"]
    }
    resources={}
    for row in c.lock["effective_values"]:
        if not row["path"].startswith("prompts.packs."):continue
        declared=row["value"]
        if not isinstance(declared,str):continue
        source_ref=sources.get(row["source_layer_id"])
        source_path=paths.get(source_ref)
        if source_path is None:raise ValueError(f"prompt resource source is unavailable: {source_ref}")
        candidate=Path(declared).expanduser()
        if contained and candidate.is_absolute():
            raise HarnessContainmentError(
                "harness resource reference must be relative"
            )
        unresolved=candidate if candidate.is_absolute() else source_path.parent/candidate
        target=(
            _contained_target(
                unresolved,w,"harness resource",strict=True
            )
            if contained
            else unresolved.resolve(strict=True)
        )
        if not target.is_file():
            raise HarnessResourceInvalidError(
                f"harness resource is not a file: {declared}"
            )
        resource_ref=f"{source_ref}::{declared}"
        content=target.read_bytes()
        try:content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise HarnessResourceInvalidError(
                f"harness prompt resource is not UTF-8: {declared}"
            ) from error
        prior=resources.setdefault(resource_ref,content)
        if prior!=content:raise ValueError(f"resource identity collision: {resource_ref}")
    return resources
def _daily_driver_role_resources(p,w,contained):
    temporary_prefix=f".{DAILY_DRIVER_TEMPLATE_NAME}."
    if p.name!=DAILY_DRIVER_TEMPLATE_NAME and not p.name.startswith(temporary_prefix):return {}
    unresolved=p.parent/DAILY_DRIVER_MODEL_ROLES_NAME
    target=(
        _contained_target(unresolved,w,"harness resource",strict=True)
        if contained
        else unresolved.resolve(strict=True)
    )
    if not target.is_file():raise IsADirectoryError(f"harness resource is not a file: {DAILY_DRIVER_MODEL_ROLES_NAME}")
    load_daily_driver_model_roles(target)
    return {f"{_ref(p,w)}::{DAILY_DRIVER_MODEL_ROLES_NAME}":target.read_bytes()}
def _compile(p,w,contained=False):
    p=Path(p); w=Path(w).resolve()
    if contained:
        if p.is_symlink():
            raise HarnessContainmentError(
                "harness source cannot be a symlink"
            )
        try:resolved_root=p.resolve(strict=True)
        except RuntimeError as error:
            raise HarnessContainmentError(
                "harness source cannot traverse a symlink"
            ) from error
        try:resolved_root.relative_to(w)
        except ValueError as error:
            raise HarnessContainmentError(
                "harness source must remain within workspace"
            ) from error
        p=resolved_root
    source_ref=_ref(p,w); paths={source_ref:p}
    def load_ref(parent,decl):
        declared=Path(decl)
        if contained and declared.is_absolute():
            raise HarnessContainmentError(
                "harness reference must be relative"
            )
        unresolved=declared if declared.is_absolute() else paths[parent].parent/declared
        target=(
            _contained_target(
                unresolved,w,"harness reference",strict=True
            )
            if contained
            else unresolved.resolve()
        )
        resolved=_ref(target,w)
        if resolved in paths and paths[resolved]!=target:raise ValueError(f"reference identity collision: {resolved}")
        paths[resolved]=target
        return resolved,_doc(target)
    document=_doc(p)
    compiled=compile_harness_definition(document,source_ref=source_ref,load_ref=load_ref)
    parse_harness_definition(compiled.resolved_author_dict())
    resources=_prompt_resources(compiled,paths,w,contained)
    roles=_daily_driver_role_resources(p,w,contained)
    if overlap:=resources.keys()&roles.keys():raise ValueError(f"resource identity collision: {sorted(overlap)[0]}")
    resources.update(roles)
    return compiled.with_resource_inputs(resources)
def _default_profile_source()->Path:
    profile_path=Path(daily_driver_template_path())
    expected=Path(DEFAULT_PROFILE_DEFINITION_REF)
    if (
        len(profile_path.parts)<len(expected.parts)
        or profile_path.parts[-len(expected.parts):]!=expected.parts
    ):
        raise HarnessContainmentError(
            "bundled daily-driver profile path is not canonical"
        )
    package_root=profile_path.parents[len(expected.parts)-1]
    return _contained_target(
        profile_path,
        package_root,
        "bundled daily-driver profile",
        strict=True,
    )


@dataclass(frozen=True, slots=True)
class DefaultProfileResolution:
    """Immutable internal and portable authority for the packaged profile."""

    source_path: Path
    compilation: HarnessCompilation
    identity: Mapping[str, object]

    def public_identity(self) -> dict[str, object]:
        return _thaw_identity(self.identity)  # type: ignore[return-value]


def _freeze_identity(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_identity(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_identity(item) for item in value)
    return value


def _thaw_identity(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_identity(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_identity(item) for item in value]
    return value


def _default_profile_identity(compilation: HarnessCompilation) -> dict[str, object]:
    lock = compilation.lock
    resources = []
    for layer in lock["source_layers"]:
        if layer["scope"] != "resource":
            continue
        _, separator, declared = str(layer["source_ref"]).partition("::")
        declared_path = Path(declared)
        if (
            not separator
            or not declared
            or declared_path.is_absolute()
            or ".." in declared_path.parts
        ):
            raise DefaultProfileInvalidError(
                "bundled daily-driver profile identity is corrupt"
            )
        resource_ref = Path(
            "agent_configs/templates",
            declared_path,
        ).as_posix()
        resources.append({
            "ref": resource_ref,
            "sha256": str(layer["layer_hash"]),
        })
    explanation = compilation.explanation.as_dict()
    definition = compilation.resolved_author_dict()
    return {
        "profile_id": DEFAULT_PROFILE_ID,
        "definition_ref": DEFAULT_PROFILE_DEFINITION_REF,
        "schema_version": str(definition["schema_version"]),
        "source_sha256": str(explanation["config_sha256"]),
        "effective_lock_schema_version": str(lock["schema_version"]),
        "effective_lock_hash": str(lock["graph_hash"]),
        "resources": sorted(resources, key=lambda row: row["ref"]),
    }


@lru_cache(maxsize=1)
def resolve_default_profile() -> DefaultProfileResolution:
    """Resolve one immutable package-local daily-driver authority."""
    try:
        profile_path = _default_profile_source()
        compilation = _compile(
            profile_path,
            profile_path.parent,
            contained=True,
        )
        identity = _default_profile_identity(compilation)
        return DefaultProfileResolution(
            source_path=profile_path,
            compilation=compilation,
            identity=_freeze_identity(identity),  # type: ignore[arg-type]
        )
    except (
        HarnessContainmentError,
        HarnessResourceInvalidError,
        IsADirectoryError,
    ) as error:
        raise DefaultProfileInvalidError(
            "bundled daily-driver profile is corrupt"
        ) from error
    except HarnessReferenceMissingError as error:
        raise DefaultProfileUnavailableError(
            "bundled daily-driver profile resources are unavailable"
        ) from error
    except OSError as error:
        raise DefaultProfileUnavailableError(
            "bundled daily-driver profile resources are unavailable"
        ) from error
    except (
        HarnessDefinitionValidationError,
        SchemaError,
        KeyError,
        RuntimeError,
        TypeError,
        ValueError,
        yaml.YAMLError,
    ) as error:
        raise DefaultProfileInvalidError(
            "bundled daily-driver profile is corrupt"
        ) from error


def default_profile_identity() -> dict[str, object]:
    """Return a fresh public identity of the packaged default profile."""
    return resolve_default_profile().public_identity()
def lock_path(p,out=None):
    if out:
        q=Path(out).expanduser(); return q if q.is_file() or q.suffix==".json" else q/f"{p.stem}.lock.json"
    return p.with_name(p.stem+".lock.json")
def lock_metadata_path(p):return p.with_name("."+p.name+".meta.json")
def _write(p,x):p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(x,sort_keys=True,indent=2)+"\n")
_INIT_LOCK=threading.RLock()
def _path_identity(p):
    stat=os.lstat(p)
    return stat.st_dev,stat.st_ino
def _remove_published(p,identity):
    try:
        if _path_identity(p)==identity:p.unlink()
    except FileNotFoundError:pass
def _rollback_published(published):
    for p,identity in reversed(published):_remove_published(p,identity)
def _publish_seed(p,content):
    temporary=p.with_name(f".{p.name}.{os.urandom(8).hex()}.tmp"); descriptor=None; published=None
    try:
        descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(descriptor,"wb") as stream:descriptor=None; stream.write(content); stream.flush(); os.fsync(stream.fileno())
        identity=_path_identity(temporary)
        try:os.link(temporary,p)
        except FileExistsError:return None
        published=identity
        return identity
    except BaseException:
        if published is not None:_remove_published(p,published)
        raise
    finally:
        if descriptor is not None:os.close(descriptor)
        try:temporary.unlink(missing_ok=True)
        except BaseException:
            if published is not None:_remove_published(p,published)
            raise
def _seed_mismatch(p,content):
    return p.is_symlink() or p.exists() and (not p.is_file() or p.read_bytes()!=content)
def _init_result(h,q,r,w):
    refs=[_ref(h,w),_ref(q,w),_ref(r,w)]
    return CliResult.success(["harness","init"],{"path":refs[0],"prompt_path":refs[1],"model_roles_path":refs[2]},refs,stage="harness.init")
def daily_driver_bundle_paths(directory):
    d=Path(directory)
    return (
        d/DAILY_DRIVER_TEMPLATE_NAME,
        d/DAILY_DRIVER_PROMPT_BUNDLE_PATH,
        d/DAILY_DRIVER_MODEL_ROLES_NAME,
    )
def init(a):
    w=_w(a); d=Path(a.out or ".").expanduser()
    try:
        profile_source=daily_driver_template_path()
        prompt_source=daily_driver_prompt_path()
        roles_source=daily_driver_model_roles_path()
        h,q,r=daily_driver_bundle_paths(d)
        seeds=((h,profile_source.read_bytes()),(q,prompt_source.read_bytes()),(r,roles_source.read_bytes()))
        d.mkdir(parents=True,exist_ok=True)
        with _INIT_LOCK:
            if any(_seed_mismatch(p,content) for p,content in seeds):return CliResult.failure(["harness","init"],2,"path_exists","refusing to overwrite existing harness bundle","harness.init")
            published=[]
            try:
                for p,content in seeds:
                    p.parent.mkdir(parents=True,exist_ok=True)
                    if not p.exists():
                        if identity:=_publish_seed(p,content):published.append((p,identity))
                if any(_seed_mismatch(p,content) for p,content in seeds):
                    _rollback_published(published)
                    return CliResult.failure(["harness","init"],2,"path_exists","refusing to overwrite existing harness bundle","harness.init")
            except BaseException:
                _rollback_published(published)
                raise
        return _init_result(h,q,r,w)
    except Exception as e:return from_exception(["harness","init"],e,"harness.init")
def validate(a,command_name="validate"):
    p,w=_p(a),_w(a); command=["harness",command_name]; stage=f"harness.{command_name}"
    try:d=load_harness_definition(p); return CliResult.success(command,{"path":_ref(p,w),"schema_version":d["schema_version"]},[_ref(p,w)],stage=stage)
    except HarnessDefinitionValidationError as e:return CliResult.failure(command,2,"invalid_harness",str(e),stage,refs=[_ref(p,w)])
    except Exception as e:return from_exception(command,e,stage)
def explain(a):
    p,w=_p(a),_w(a)
    try:
        x=_compile(p,w,getattr(a,"contained",False)).explanation.as_dict(); x["config_path"]=_ref(p,w); return CliResult.success(["harness","explain"],x,[_ref(p,w)],{"config":str(x.get("config_sha256",""))},stage="harness.explain")
    except Exception as e:return from_exception(["harness","explain"],e,"harness.explain")
def lock(a):
    p,w=_p(a),_w(a); target=lock_path(p,getattr(a,"out",None))
    try:
        c=_compile(p,w,getattr(a,"contained",False)); meta={"schema_version":"bb.harness_lock_metadata.v1","source_ref":_ref(p,w),"source_sha256":sha256_json(c.resolved_author_dict()),"graph_hash":c.lock["graph_hash"]}
        if getattr(a,"check",False):
            if not target.exists() or not lock_metadata_path(target).exists():return CliResult.failure(["harness","lock"],5,"lock_missing","lock is missing","harness.lock")
            if json.loads(target.read_text())!=c.lock.as_dict() or json.loads(lock_metadata_path(target).read_text())!=meta:return CliResult.failure(["harness","lock"],5,"lock_drift","harness definition changed after lock","harness.lock",next_actions=[f"breadboard harness lock {_ref(p,w)}"])
            return CliResult.success(["harness","lock"],{"path":_ref(target,w),"graph_hash":meta["graph_hash"],"checked":True},[_ref(target,w)],{"graph":meta["graph_hash"]},stage="harness.lock")
        _write(target,c.lock.as_dict()); _write(lock_metadata_path(target),meta)
        next_action=f"breadboard harness run {shlex.quote(str(p))} --local"
        if target.resolve()!=lock_path(p).resolve():next_action+=f" --lock {shlex.quote(str(target.resolve()))}"
        return CliResult.success(["harness","lock"],{"path":_ref(target,w),"graph_hash":meta["graph_hash"]},[_ref(target,w)],{"graph":meta["graph_hash"],"source":meta["source_sha256"]},[next_action],"harness.lock")
    except Exception as e:return from_exception(["harness","lock"],e,"harness.lock")
def load_lock(p,w,*,explicit=False):
    t=p if explicit or p.name.endswith(".lock.json") else lock_path(p)
    if not t.exists():raise FileNotFoundError(f"lock is missing: {_ref(t,w)}")
    if not lock_metadata_path(t).exists():raise ValueError("lock metadata is missing; lock must be regenerated")
    return EffectiveHarnessLock._from_record(json.loads(t.read_text())),lock_metadata_path(t)
@contextlib.contextmanager
def _local_server(workspace:Path)->Iterator[str]:
    import uvicorn
    from breadboard_engine.api.cli_bridge.app import create_app
    settings={
        "BREADBOARD_LEGACY_ROUTES":"0",
        "BREADBOARD_ENABLE_PUBLIC_API":"1",
        "BREADBOARD_ENABLE_E4_API":"0",
        "BREADBOARD_PUBLIC_WORKSPACE":str(workspace),
        "RAY_SCE_LOCAL_MODE":"1",
    }
    previous={name:os.environ.get(name) for name in settings}
    os.environ.update(settings);listener=None
    def restore_environment():
        for name,value in previous.items():
            if value is None:os.environ.pop(name,None)
            else:os.environ[name]=value
    try:
        listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM);listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);listener.bind(("127.0.0.1",0));listener.listen(128)
        server=uvicorn.Server(uvicorn.Config(create_app(),host="127.0.0.1",port=int(listener.getsockname()[1]),log_level="critical",access_log=False))
    except BaseException:
        if listener is not None:listener.close()
        restore_environment()
        raise
    def serve():
        server.run(sockets=[listener])
    thread=threading.Thread(target=serve,daemon=True);thread.start();deadline=time.monotonic()+10
    while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(0.01)
    if not server.started:
        server.should_exit=True;thread.join(timeout=5);listener.close();restore_environment()
        raise RuntimeError("local create_app server did not start")
    try:yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit=True;thread.join(timeout=10);listener.close();restore_environment()
        if thread.is_alive():raise RuntimeError("local create_app server did not stop")
def run(a):
    p,w=_p(a),_w(a)
    try:
        lock_argument=getattr(a,"lock",None)
        requested_lock_path=Path(lock_argument).expanduser().resolve() if lock_argument else p
        effective_lock_path=requested_lock_path if lock_argument or requested_lock_path.name.endswith(".lock.json") else lock_path(requested_lock_path)
        lock,mp=load_lock(requested_lock_path,w,explicit=bool(lock_argument)); c=_compile(p,w,getattr(a,"contained",False)); m=json.loads(mp.read_text())
        lock_action=f"breadboard harness lock {shlex.quote(str(p))}"
        if lock_argument:lock_action+=f" --out {shlex.quote(str(requested_lock_path))}"
        if m.get("source_sha256")!=sha256_json(c.resolved_author_dict()) or m.get("graph_hash")!=lock["graph_hash"] or c.lock.as_dict()!=lock.as_dict():return CliResult.failure(["harness","run"],5,"lock_drift","mutable harness definition cannot run without a fresh lock","harness.run",next_actions=[lock_action])
        a._effective_lock=lock;a._workspace=w;a._lock_id=_ref(effective_lock_path,w)
        if getattr(a,"local",False):
            try:
                with _local_server(w) as server:
                    a.server=server
                    return _server(a)
            except ModuleNotFoundError as e:return CliResult.failure(["harness","run"],6,"local_backend_unavailable",str(e),"harness.run",next_actions=["install BreadBoard with local runtime support or use --server"],status="blocked")
        return _server(a)
    except Exception as e:return from_exception(["harness","run"],e,"harness.run")
def _server(a):
    try:
        import breadboard_sdk
        task=str(getattr(a,"task",None) or "List files");c=breadboard_sdk.BreadBoardClient(a.server,timeout_s=120)
        started=c.start_session({"lock_id":a._lock_id,"task":task},idempotency_key=sha256_json({"lock_id":a._lock_id,"task":task}))
        if not isinstance(started,dict) or not started.get("ok"):
            raise RuntimeError(f"session.start failed: {started!r}")
        session=started.get("data",{}).get("session",{})
        sid=str(session.get("session_id") or "")
        if not sid:raise RuntimeError("session.start returned no session identity")
        terminal=False
        for event in c.events_session(sid):
            kind=str(event.get("kind") or event.get("type") or "") if isinstance(event,dict) else ""
            if kind in {"session.failed","session.canceled","error"}:
                payload=event.get("payload") if isinstance(event,dict) else event
                return CliResult.failure(["harness","run"],4,"session_execution_failed",f"session execution failed: {payload}","harness.run")
            if kind=="session.completed":terminal=True;break
        if not terminal:return CliResult.failure(["harness","run"],4,"session_stream_eof","session event stream ended before a terminal event","harness.run")
        current=c.get_session(sid)
        view=current.get("data",{}).get("session",{}) if isinstance(current,dict) else {}
        event_count=int(view.get("event_count") or 0)
        refs=[];next_actions=[]
        hashes={"lock":str(view.get("effective_lock_hash") or ""),"task":str(view.get("task_hash") or "")}
        hashes={name:value for name,value in hashes.items() if value}
        if getattr(a,"local",False):
            from .session import session_event_path
            workspace_arg=shlex.quote(str(getattr(a,"workspace",None) or "."))
            refs=[_ref(session_event_path(a._workspace,sid),a._workspace)]
            next_actions=[f"breadboard session --workspace {workspace_arg} get {sid}"]
        return CliResult.success(["harness","run"],{"session_id":sid,"record_count":event_count,"event_count":event_count},refs=refs,hashes=hashes,next_actions=next_actions,stage="harness.run")
    except ModuleNotFoundError as e:return CliResult.failure(["harness","run"],6,"client_backend_unavailable",str(e),"harness.run",next_actions=["install BreadBoard SDK support"],status="blocked")
    except Exception as e:return from_exception(["harness","run"],e,"harness.run")
def _is_harness_path(p,w,contained):
    try:
        if "harness" not in p.name and p.name!=DAILY_DRIVER_TEMPLATE_NAME:return False
        if not p.is_file() or p.is_symlink():return False
        resolved=p.resolve(strict=True)
        if contained and not resolved.is_relative_to(w):return False
        load_harness_definition(resolved)
        return True
    except (OSError,TypeError,ValueError,yaml.YAMLError):
        return False
def list_harnesses(a):
    w=_w(a); root=Path(getattr(a,"directory",None) or w)
    try:
        contained=getattr(a,"contained",False)
        paths=(
            p for p in sorted(root.rglob("*.yaml"))
            if _is_harness_path(p,w,contained)
        )
        r=[_ref(p,w) for p in paths]
        return CliResult.success(["harness","list"],{"harnesses":r,"count":len(r)},r,stage="harness.list")
    except Exception as e:return from_exception(["harness","list"],e,"harness.list")
def show(a,command_name="show"):
    p,w=_p(a),_w(a); command=["harness",command_name]; stage=f"harness.{command_name}"
    try:return CliResult.success(command,{"path":_ref(p,w),"definition":_doc(p)},[_ref(p,w)],stage=stage)
    except Exception as e:return from_exception(command,e,stage)
def get(a):
    return show(a,"get")
def update(a):
    p,w=_p(a),_w(a); temporary=None
    try:
        document=getattr(a,"document",None); source=getattr(a,"source",None)
        if document is None:
            if not source:return CliResult.failure(["harness","update"],2,"update_input_required","harness update requires --from or a definition","harness.update")
            document=yaml.safe_load(Path(source).expanduser().read_text())
        if not isinstance(document,dict):raise ValueError("harness definition must be a mapping")
        temporary=p.with_name(f".{p.name}.{os.urandom(8).hex()}.tmp")
        if not p.is_file():raise FileNotFoundError(f"harness definition not found: {_ref(p,w)}")
        temporary.write_text(yaml.safe_dump(document,sort_keys=False)); _compile(temporary,w,getattr(a,"contained",False)); os.replace(temporary,p)
        return validate(a,"update")
    except Exception as e:return from_exception(["harness","update"],e,"harness.update")
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
def get_lock(a):
    p,w=_p(a),_w(a); t=p if p.name.endswith(".lock.json") else lock_path(p)
    try:x=json.loads(t.read_text()); return CliResult.success(["harness-lock","get"],{"path":_ref(t,w),"lock":x},[_ref(t,w)],{"graph":str(x.get("graph_hash",""))},stage="harness-lock.get")
    except Exception as e:return from_exception(["harness-lock","get"],e,"harness-lock.get")
