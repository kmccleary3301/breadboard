from __future__ import annotations

import hashlib
import json
from pathlib import Path

from breadboard.product.runtime.events import (
    JsonlEventSink,
    KernelEvent,
    Session,
    SessionView,
)

from .result import CliResult, from_exception, portable_ref


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
    if not s or s in {".",".."} or Path(s).name!=s:raise ValueError("session_id must be a portable identifier")
    p=session_event_path(w,s)
    if not p.exists():p=legacy_session_event_path(w,s)
    if not p.exists():raise FileNotFoundError(f"session not found: {s}")
    return Session.restore(_events(p),sink=JsonlEventSink(p)),p
def load_session(workspace,session_id):return _load(_workspace(w=Path(workspace)),session_id)
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
def _artifact_rows(w,s):
    root=w/".breadboard"/"artifacts"/"manifests"; rows={}
    if not root.exists():return []
    if root.is_symlink():raise ValueError("artifact manifest directory must not be a symlink")
    prefix=f"{s}."
    for m in sorted(root.iterdir()):
        if m.is_symlink() or not m.is_file() or not m.name.startswith(prefix) or not m.name.endswith(".json"):continue
        digest=m.name[len(prefix):-5]; body=m.read_bytes()
        if len(digest)!=64 or any(c not in "0123456789abcdef" for c in digest) or hashlib.sha256(body).hexdigest()!=digest:raise ValueError("artifact manifest digest mismatch")
        document=json.loads(body)
        if document.get("schema_version")!="bb.artifact_manifest.v1" or document.get("session_id")!=s or not isinstance(document.get("artifacts"),list):raise ValueError("invalid artifact manifest")
        for row in document["artifacts"]:
            if not isinstance(row,dict) or not isinstance(row.get("name"),str):raise ValueError("invalid artifact manifest row")
            previous=rows.setdefault(row["name"],row)
            if previous!=row:raise ValueError("conflicting artifact manifest rows")
    return [rows[name] for name in sorted(rows)]
def artifacts(a):
    w=_workspace(a)
    try:s,p=_load(w,a.SESSION_ID); rows=_artifact_rows(w,a.SESSION_ID)
    except Exception as e:return from_exception(["session","artifacts"],e,"session.artifacts")
    return CliResult.success(["session","artifacts"],{"session_id":a.SESSION_ID,"artifacts":rows},[portable_ref(p,w)],stage="session.artifacts")
