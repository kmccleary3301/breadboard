from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from breadboard.product.runtime.events import JsonlEventSink,KernelEvent,Session,SessionView
from .result import CliResult,from_exception,portable_ref
def _workspace(a=None,w=None):return w.expanduser().resolve() if w else Path(getattr(a,"workspace",None) or Path.cwd()).expanduser().resolve()
def session_directory(w):return w/".breadboard"/"sessions"
def session_event_path(w,s):return session_directory(w)/s/"session_events.jsonl"
def session_metadata_path(w,s):return session_directory(w)/s/"session.json"
def legacy_session_event_path(w,s):return session_directory(w)/f"{s}.events.jsonl"
def legacy_session_metadata_path(w,s):return session_directory(w)/f"{s}.json"
def event_from_record(x):return KernelEvent(session_id=str(x["session_id"]),sequence=int(x["sequence"]),kind=str(x["kind"]),occurred_at=str(x["occurred_at"]),payload=x.get("payload",{}),schema_version=str(x.get("schema_version","bb.session_event.v1")))
def _events(p):return [event_from_record(json.loads(x)) for x in p.read_text().splitlines() if x.strip()]
def _view(v:SessionView):return v.as_dict()
def persist_session(w,s,event_path=None):
    d=session_directory(w); d.mkdir(parents=True,exist_ok=True); v=s.read_model
    metadata_path=legacy_session_metadata_path(w,v.session_id) if event_path==legacy_session_event_path(w,v.session_id) else session_metadata_path(w,v.session_id)
    metadata_path.parent.mkdir(parents=True,exist_ok=True)
    metadata_path.write_text(json.dumps({"schema_version":"bb.session.v1",**v.as_dict()},sort_keys=True,indent=2)+"\n")
def _load(w,s):
    if not s or Path(s).name!=s:raise ValueError("session_id must be a portable identifier")
    p=session_event_path(w,s)
    if not p.exists():p=legacy_session_event_path(w,s)
    if not p.exists():raise FileNotFoundError(f"session not found: {s}")
    return Session.restore(_events(p),sink=JsonlEventSink(p)),p
def list_sessions(a):
    w=_workspace(a)
    try:
        paths={p.parent.name:p for p in session_directory(w).glob("*/session_events.jsonl")}
        suffix=".events.jsonl"
        for p in session_directory(w).glob(f"*{suffix}"):
            paths.setdefault(p.name[:-len(suffix)],p)
        rows=[];refs=[]
        for p in sorted(paths.values()):
            try:
                v=Session.restore(_events(p)).read_model
                rows.append({"session_id":v.session_id,"status":v.status,"event_count":v.event_count});refs.append(portable_ref(p,w))
            except Exception:pass
        return CliResult.success(["session","list"],{"sessions":rows,"count":len(rows)},refs,stage="session.list")
    except Exception as e:return from_exception(["session","list"],e,"session.list")
def get(a,command_name="get"):
    w=_workspace(a)
    try:s,p=_load(w,a.SESSION_ID); v=s.read_model; return CliResult.success(["session",command_name],{"session":_view(v)},[portable_ref(p,w)],{"lock":v.effective_lock_hash,"task":v.task_hash},stage=f"session.{command_name}")
    except Exception as e:return from_exception(["session",command_name],e,f"session.{command_name}")
def _mutate(a,name,fn):
    w=_workspace(a)
    try:s,p=_load(w,a.SESSION_ID); v=fn(s); persist_session(w,s,p); return CliResult.success(["session",name],{"session":_view(v)},[portable_ref(p,w)],stage=f"session.{name}")
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
    for m in sorted(session_directory(w).glob(f"{a.SESSION_ID}*.manifest.json")):
        try:rows+=json.loads(m.read_text()).get("artifacts",[])
        except Exception:pass
    return CliResult.success(["session","artifacts"],{"session_id":a.SESSION_ID,"artifacts":rows},[portable_ref(p,w)],stage="session.artifacts")
