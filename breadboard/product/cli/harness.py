from __future__ import annotations
import contextlib,json,os,shlex,shutil,socket,sys,threading,time
from collections.abc import Iterator
from pathlib import Path
from typing import Any,Mapping
import yaml
from breadboard.product.harness.compile import HarnessCompilation,compile_harness_definition
from breadboard.product.harness.lock import EffectiveHarnessLock,sha256_json
from breadboard.product.harness.validate import HarnessDefinitionValidationError,load_harness_definition
from .result import CliResult,EXIT_LOCK_DRIFT,from_exception,portable_ref
from .system import resource_path
FALLBACK="""schema_version: bb.agent_config_surface.v2
version: 2
workspace:\n  root: .
providers:\n  default_model: mock/reference\n  models:\n    - id: mock/reference\n      adapter: mock_chat\nprompts:\n  packs:\n    base:\n      system: prompts/minimal_system.md\nmodes:\n  - name: respond\n    prompt: '@pack(base).system'\n    tools_enabled: []
loop:\n  sequence:\n    - mode: respond\n"""
def _w(a):return Path(getattr(a,"workspace",None) or Path.cwd()).expanduser().resolve()
def _p(a):return Path(a.PATH).expanduser().resolve()
def _ref(p,w):return portable_ref(p,w)
def _template(n):
    p=resource_path("agent_configs/templates/"+n); return p if p.exists() else None
def _doc(p):
    x=yaml.safe_load(p.read_text())
    if not isinstance(x,dict):raise ValueError("harness definition must be a mapping")
    return x
def _loadref(parent,decl,w):
    p=Path(parent); base=p if p.is_absolute() else w/p; target=(base.parent/decl).resolve(); return _ref(target,w),_doc(target)
def _compile(p,w):return compile_harness_definition(load_harness_definition(p),source_ref=_ref(p,w),load_ref=lambda parent,decl:_loadref(parent,decl,w))
def _lockpath(p,out=None):
    if out:
        q=Path(out).expanduser(); return q if q.suffix==".json" else q/f"{p.stem}.lock.json"
    return p.with_name(p.stem+".lock.json")
def _meta(p):return p.with_name("."+p.name+".meta.json")
def _write(p,x):p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(x,sort_keys=True,indent=2)+"\n")
def init(a):
    w=_w(a); d=Path(a.out or ".").expanduser(); h=d/"minimal_harness.v2.yaml"; q=d/"prompts/minimal_system.md"
    if h.exists() or q.exists():return CliResult.failure(["harness","init"],2,"path_exists","refusing to overwrite existing harness bundle","harness.init")
    try:
        d.mkdir(parents=True,exist_ok=True); t=_template("minimal_harness.v2.yaml"); shutil.copyfile(t,h) if t else h.write_text(FALLBACK); q.parent.mkdir(parents=True,exist_ok=True); t=_template("prompts/minimal_system.md"); shutil.copyfile(t,q) if t else q.write_text("You are a BreadBoard reference harness.\n"); return CliResult.success(["harness","init"],{"path":_ref(h,w),"prompt_path":_ref(q,w)},[_ref(h,w),_ref(q,w)],stage="harness.init")
    except Exception as e:return from_exception(["harness","init"],e,"harness.init")
def validate(a,command_name="validate"):
    p,w=_p(a),_w(a); command=["harness",command_name]; stage=f"harness.{command_name}"
    try:d=load_harness_definition(p); return CliResult.success(command,{"path":_ref(p,w),"schema_version":d["schema_version"]},[_ref(p,w)],stage=stage)
    except HarnessDefinitionValidationError as e:return CliResult.failure(command,2,"invalid_harness",str(e),stage,refs=[_ref(p,w)])
    except Exception as e:return from_exception(command,e,stage)
def explain(a):
    p,w=_p(a),_w(a)
    try:
        x=_compile(p,w).explanation.as_dict(); x["config_path"]=_ref(p,w); return CliResult.success(["harness","explain"],x,[_ref(p,w)],{"config":str(x.get("config_sha256",""))},stage="harness.explain")
    except Exception as e:return from_exception(["harness","explain"],e,"harness.explain")
def lock(a):
    p,w=_p(a),_w(a); target=_lockpath(p,getattr(a,"out",None))
    try:
        c=_compile(p,w); meta={"schema_version":"bb.harness_lock_metadata.v1","source_ref":_ref(p,w),"source_sha256":sha256_json(c.resolved_author_dict()),"graph_hash":c.lock["graph_hash"]}
        if getattr(a,"check",False):
            if not target.exists() or not _meta(target).exists():return CliResult.failure(["harness","lock"],5,"lock_missing","lock is missing","harness.lock")
            if json.loads(target.read_text())!=c.lock.as_dict() or json.loads(_meta(target).read_text())!=meta:return CliResult.failure(["harness","lock"],5,"lock_drift","harness definition changed after lock","harness.lock",next_actions=[f"breadboard harness lock {_ref(p,w)}"])
            return CliResult.success(["harness","lock"],{"path":_ref(target,w),"graph_hash":meta["graph_hash"],"checked":True},[_ref(target,w)],{"graph":meta["graph_hash"]},stage="harness.lock")
        _write(target,c.lock.as_dict()); _write(_meta(target),meta); return CliResult.success(["harness","lock"],{"path":_ref(target,w),"graph_hash":meta["graph_hash"]},[_ref(target,w)],{"graph":meta["graph_hash"],"source":meta["source_sha256"]},[f"breadboard harness run {_ref(p,w)} --local"],"harness.lock")
    except Exception as e:return from_exception(["harness","lock"],e,"harness.lock")
def load_lock(p,w):
    t=p if p.name.endswith(".lock.json") else _lockpath(p)
    if not t.exists():raise FileNotFoundError(f"lock is missing: {_ref(t,w)}")
    if not _meta(t).exists():raise ValueError("lock metadata is missing; lock must be regenerated")
    return EffectiveHarnessLock._from_record(json.loads(t.read_text())),_meta(t)
def _record_count(response):
    if not isinstance(response,dict):return 0
    total=response.get("total")
    if isinstance(total,int):return total
    records=response.get("records")
    return len(records) if isinstance(records,list) else 0
def _poll_records(client,session_id):
    deadline=time.monotonic()+0.5
    while True:
        response=client.read_session_records(session_id)
        if _record_count(response)>0 or time.monotonic()>=deadline:return response
        time.sleep(0.1)
@contextlib.contextmanager
def _local_server()->Iterator[str]:
    import uvicorn
    from agentic_coder_prototype.api.cli_bridge.app import create_app
    previous=os.environ.pop("BREADBOARD_LEGACY_ROUTES",None);listener=None
    def restore_environment():
        if previous is None:os.environ.pop("BREADBOARD_LEGACY_ROUTES",None)
        else:os.environ["BREADBOARD_LEGACY_ROUTES"]=previous
    try:
        listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM);listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);listener.bind(("127.0.0.1",0));listener.listen(128)
        server=uvicorn.Server(uvicorn.Config(create_app(),host="127.0.0.1",port=int(listener.getsockname()[1]),log_config=None,access_log=False))
    except BaseException:
        if listener is not None:listener.close()
        restore_environment()
        raise
    real_out,real_err=sys.stdout,sys.stderr
    def serve():
        with open(os.devnull,"w") as sink,contextlib.redirect_stdout(sink),contextlib.redirect_stderr(sink):server.run(sockets=[listener])
    thread=threading.Thread(target=serve,daemon=True);thread.start();deadline=time.monotonic()+10
    while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(0.01)
    if not server.started:
        server.should_exit=True;thread.join(timeout=5);listener.close();restore_environment()
        if thread.is_alive():sys.stdout,sys.stderr=real_out,real_err
        raise RuntimeError("local create_app server did not start")
    try:yield f"http://127.0.0.1:{listener.getsockname()[1]}"
    finally:
        server.should_exit=True;thread.join(timeout=10);listener.close();restore_environment()
        if thread.is_alive():
            sys.stdout,sys.stderr=real_out,real_err
            raise RuntimeError("local create_app server did not stop")
def run(a):
    p,w=_p(a),_w(a)
    try:
        lock,mp=load_lock(p,w); c=_compile(p,w); m=json.loads(mp.read_text())
        if m.get("source_sha256")!=sha256_json(c.resolved_author_dict()) or m.get("graph_hash")!=lock["graph_hash"] or c.lock.as_dict()!=lock.as_dict():return CliResult.failure(["harness","run"],5,"lock_drift","mutable harness definition cannot run without a fresh lock","harness.run",next_actions=[f"breadboard harness lock {_ref(p,w)}"])
        a._effective_lock=lock;a._workspace=w
        if getattr(a,"local",False):
            try:
                with _local_server() as server:
                    a.server=server
                    return _server(a)
            except ModuleNotFoundError as e:return CliResult.failure(["harness","run"],6,"local_backend_unavailable",str(e),"harness.run",next_actions=["install BreadBoard with local runtime support or use --server"],status="blocked")
        return _server(a)
    except Exception as e:return from_exception(["harness","run"],e,"harness.run")
def _server(a):
    try:
        import breadboard_sdk
        task=str(getattr(a,"task",None) or "List files");c=breadboard_sdk.BreadboardClient(a.server);s=c.create_session(config_path=str(a.PATH),task="");sid=str(s["session_id"]);c.post_input(sid,content=task);terminal=False
        for e in c.stream_events(sid,query={"replay":"true"}):
            kind=str(e.get("type") or "") if isinstance(e,dict) else ""
            if kind=="error":return CliResult.failure(["harness","run"],4,"session_stream_error",f"session event stream failed: {e.get('payload')}","harness.run")
            if kind in {"completion","run_finished"}:terminal=True;break
        if not terminal:return CliResult.failure(["harness","run"],4,"session_stream_eof","session event stream ended before a terminal event","harness.run")
        records=_poll_records(c,sid);record_count=_record_count(records);refs=sorted({_ref(Path(str(row["path"])),_w(a)) for row in records.get("records",[]) if isinstance(row,dict) and row.get("path")}) if isinstance(records,dict) else [];hashes={};next_actions=[]
        if getattr(a,"local",False):
            from .session import persist_completed_run
            workspace_arg=shlex.quote(str(getattr(a,"workspace",None) or "."));local_ref,view=persist_completed_run(a._effective_lock,task,sid,a._workspace);record_count=max(record_count,view.event_count);refs=sorted({*refs,local_ref});hashes={"lock":view.effective_lock_hash,"task":view.task_hash};next_actions=[f"breadboard session --workspace {workspace_arg} show {sid}"]
        result=CliResult.success(["harness","run"],{"session_id":sid,"record_count":record_count},refs=refs,hashes=hashes,next_actions=next_actions,stage="harness.run")
        if not record_count:result.warnings.append("session completed before bridge runtime records became visible")
        return result
    except ModuleNotFoundError as e:return CliResult.failure(["harness","run"],6,"client_backend_unavailable",str(e),"harness.run",next_actions=["install BreadBoard SDK support"],status="blocked")
    except Exception as e:return from_exception(["harness","run"],e,"harness.run")
def list_harnesses(a):
    w=_w(a); root=Path(getattr(a,"directory",None) or w)
    try:
        r=[_ref(p,w) for p in sorted(root.rglob("*.yaml")) if p.is_file() and "harness" in p.name]; return CliResult.success(["harness","list"],{"harnesses":r,"count":len(r)},r,stage="harness.list")
    except Exception as e:return from_exception(["harness","list"],e,"harness.list")
def show(a,command_name="show"):
    p,w=_p(a),_w(a); command=["harness",command_name]; stage=f"harness.{command_name}"
    try:return CliResult.success(command,{"path":_ref(p,w),"definition":_doc(p)},[_ref(p,w)],stage=stage)
    except Exception as e:return from_exception(command,e,stage)
def get(a):
    return show(a,"get")
def update(a):
    return validate(a,"update")
def get_lock(a):
    p,w=_p(a),_w(a); t=p if p.name.endswith(".lock.json") else _lockpath(p)
    try:x=json.loads(t.read_text()); return CliResult.success(["harness-lock","get"],{"path":_ref(t,w),"lock":x},[_ref(t,w)],{"graph":str(x.get("graph_hash",""))},stage="harness-lock.get")
    except Exception as e:return from_exception(["harness-lock","get"],e,"harness-lock.get")
