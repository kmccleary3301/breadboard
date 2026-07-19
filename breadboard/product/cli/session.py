from __future__ import annotations
import json,uuid
from pathlib import Path
from typing import Any
from breadboard.product.harness.lock import EffectiveHarnessLock
from breadboard.product.runtime.events import JsonlEventSink,KernelEvent,Session,SessionView
from .result import CliResult,EXIT_BLOCKED,from_exception,portable_ref
def _workspace(a=None,w=None):return w.expanduser().resolve() if w else Path(getattr(a,"workspace",None) or Path.cwd()).expanduser().resolve()
def _dir(w):return w/".breadboard"/"sessions"
def _ep(w,s):return _dir(w)/f"{s}.events.jsonl"
def _meta(w,s):return _dir(w)/f"{s}.json"
def _event(x):return KernelEvent(session_id=str(x["session_id"]),sequence=int(x["sequence"]),kind=str(x["kind"]),occurred_at=str(x["occurred_at"]),payload=x.get("payload",{}),schema_version=str(x.get("schema_version","bb.session_event.v1")))
def _events(p):return [_event(json.loads(x)) for x in p.read_text().splitlines() if x.strip()]
def _view(v:SessionView):return v.as_dict()
def _persist(w,s):
    d=_dir(w); d.mkdir(parents=True,exist_ok=True); v=s.read_model; _meta(w,v.session_id).write_text(json.dumps({"schema_version":"bb.session.v1",**v.as_dict()},sort_keys=True,indent=2)+"\n")
def _load(w,s):
    if not s or Path(s).name!=s:raise ValueError("session_id must be a portable identifier")
    p=_ep(w,s)
    if not p.exists():raise FileNotFoundError(f"session not found: {s}")
    return Session.restore(_events(p),sink=JsonlEventSink(p)),p
def _provider_ready(lock):
    rows=lock.as_dict().get("effective_values",[]); paths={str(x.get("path")):x.get("value") for x in rows if isinstance(x,dict)}; models=paths.get("providers.models")
    if not isinstance(models,list) or not models:return False,"harness has no provider declaration"
    if not [x.get("adapter") for x in models if isinstance(x,dict) and isinstance(x.get("adapter"),str) and x.get("adapter")]:return False,"harness provider has no adapter"
    return True,None
def start_local(command,lock,task,workspace,adapter_required=True):
    if adapter_required:
        ok,reason=_provider_ready(lock)
        if not ok:return CliResult.failure(command,EXIT_BLOCKED,"missing_provider_adapter",reason,"session.start",hint="Declare a provider model and adapter, then relock the harness.",next_actions=["breadboard integration list","breadboard harness lock <harness>"])
    if not isinstance(task,str) or not task.strip():return CliResult.failure(command,2,"invalid_task","task must be non-empty","session.start")
    w=workspace.expanduser().resolve(); sid="s-"+uuid.uuid4().hex; p=_ep(w,sid)
    try:
        s=Session.start(lock,task,session_id=sid,sink=JsonlEventSink(p)); s.input(task); s.complete("completed"); _persist(w,s); v=s.read_model; return CliResult.success(command,{"session_id":v.session_id,"status":v.status,"event_count":v.event_count},[portable_ref(p,w)],{"lock":v.effective_lock_hash,"task":v.task_hash},[f"breadboard session show {sid}"],"session.start")
    except Exception as e:return from_exception(command,e,"session.start")
def list_sessions(a):
    w=_workspace(a)
    try:
        rows=[]
        for p in sorted(_dir(w).glob("*.events.jsonl")):
            try:v=Session.restore(_events(p)).read_model; rows.append({"session_id":v.session_id,"status":v.status,"event_count":v.event_count})
            except Exception:pass
        return CliResult.success(["session","list"],{"sessions":rows,"count":len(rows)},[portable_ref(_ep(w,x["session_id"]),w) for x in rows],stage="session.list")
    except Exception as e:return from_exception(["session","list"],e,"session.list")
def get(a,command_name="get"):
    w=_workspace(a)
    try:s,p=_load(w,a.SESSION_ID); v=s.read_model; return CliResult.success(["session",command_name],{"session":_view(v)},[portable_ref(p,w)],{"lock":v.effective_lock_hash,"task":v.task_hash},stage=f"session.{command_name}")
    except Exception as e:return from_exception(["session",command_name],e,f"session.{command_name}")
def _mutate(a,name,fn):
    w=_workspace(a)
    try:s,p=_load(w,a.SESSION_ID); v=fn(s); _persist(w,s); return CliResult.success(["session",name],{"session":_view(v)},[portable_ref(p,w)],stage=f"session.{name}")
    except Exception as e:return from_exception(["session",name],e,f"session.{name}")
def send_input(a):return _mutate(a,"send-input",lambda s:s.input(a.content if getattr(a,"content",None) is not None else a.TEXT))
def approve(a):return _mutate(a,"approve",lambda s:s.resolve_approval(a.request_id,a.decision))
def resume(a):return _mutate(a,"resume",lambda s:s.resume())
def cancel(a):return _mutate(a,"cancel",lambda s:s.cancel(getattr(a,"reason",None) or "operator request"))
def events(a):
    w=_workspace(a)
    try:s,p=_load(w,a.SESSION_ID); return CliResult.success(["session","events"],{"session_id":a.SESSION_ID,"events":[x.as_dict() for x in s.events]},[portable_ref(p,w)],stage="session.events")
    except Exception as e:return from_exception(["session","events"],e,"session.events")
def artifacts(a):
    w=_workspace(a)
    try:s,p=_load(w,a.SESSION_ID); rows=[]
    except Exception as e:return from_exception(["session","artifacts"],e,"session.artifacts")
    for m in sorted(_dir(w).glob(f"{a.SESSION_ID}*.manifest.json")):
        try:rows+=json.loads(m.read_text()).get("artifacts",[])
        except Exception:pass
    return CliResult.success(["session","artifacts"],{"session_id":a.SESSION_ID,"artifacts":rows},[portable_ref(p,w)],stage="session.artifacts")
