from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from breadboard.product.runtime.events import KernelEvent, Session, SessionView
from breadboard.product.runtime.artifacts import AnchoredStorage

from breadboard.product.operations.model import OperationResult, from_exception, portable_ref


def _workspace(a=None,w=None):return w.expanduser().resolve() if w else Path(getattr(a,"workspace",None) or Path.cwd()).expanduser().resolve()
def session_directory(w):return w/".breadboard"/"sessions"
def session_event_path(w,s):return session_directory(w)/s/"session_events.jsonl"
def session_metadata_path(w,s):return session_directory(w)/s/"session.json"
def legacy_session_event_path(w,s):return session_directory(w)/f"{s}.events.jsonl"
def legacy_session_metadata_path(w,s):return session_directory(w)/f"{s}.json"
def event_from_record(x):return KernelEvent(session_id=str(x["session_id"]),sequence=int(x["sequence"]),kind=str(x["kind"]),occurred_at=str(x["occurred_at"]),payload=x.get("payload",{}),schema_version=str(x.get("schema_version","bb.session_event.v1")))
def _view(v:SessionView):return v.as_dict()
def _event_bytes(s):
    return b"".join((json.dumps(event.as_dict(),sort_keys=True)+"\n").encode() for event in s.events)
def _metadata_bytes(s):
    return (json.dumps({"schema_version":"bb.session.v1",**s.read_model.as_dict()},sort_keys=True,indent=2)+"\n").encode()
def _write_windows_file(path,content):
    try:descriptor=AnchoredStorage.windows_file_descriptor(path,create=False)
    except FileNotFoundError:descriptor=AnchoredStorage.windows_file_descriptor(path,create=True)
    with os.fdopen(descriptor,"r+b",buffering=0) as stream:
        stream.seek(0);stream.truncate();stream.write(content);stream.flush();os.fsync(stream.fileno())
def persist_session(w,s,event_path=None):
    legacy=event_path==legacy_session_event_path(w,s.read_model.session_id)
    if os.name=="nt":
        handles=[]
        try:
            for path in (w,w/".breadboard",session_directory(w)):handles.append(AnchoredStorage.windows_handle(path,directory=True,create=False))
            parent=session_directory(w)
            if not legacy:
                parent=session_event_path(w,s.read_model.session_id).parent
                handles.append(AnchoredStorage.windows_handle(parent,directory=True,create=False))
            _write_windows_file(parent/(f"{s.read_model.session_id}.events.jsonl" if legacy else "session_events.jsonl"),_event_bytes(s))
            _write_windows_file(parent/(f"{s.read_model.session_id}.json" if legacy else "session.json"),_metadata_bytes(s))
        finally:
            for handle in reversed(handles):AnchoredStorage.close_windows_handle(handle)
        return
    descriptors=[os.open(w,os.O_RDONLY|getattr(os,"O_DIRECTORY",0)|getattr(os,"O_NOFOLLOW",0))]
    try:
        for name in (".breadboard","sessions"):descriptors.append(AnchoredStorage.open_directory(descriptors[-1],name,create=False))
        parent=descriptors[-1]
        if not legacy:
            descriptors.append(AnchoredStorage.open_directory(parent,s.read_model.session_id,create=False));parent=descriptors[-1]
        AnchoredStorage.write_at(parent,f"{s.read_model.session_id}.events.jsonl" if legacy else "session_events.jsonl",_event_bytes(s))
        AnchoredStorage.write_at(parent,f"{s.read_model.session_id}.json" if legacy else "session.json",_metadata_bytes(s))
    finally:
        for descriptor in reversed(descriptors):os.close(descriptor)
def _load_anchored(w,s):
    if not s or s in {".",".."} or Path(s).name!=s:raise ValueError("session_id must be a portable identifier")
    if os.name=="nt":
        handles=[]; descriptor=None
        try:
            for path in (w,w/".breadboard",session_directory(w)):handles.append(AnchoredStorage.windows_handle(path,directory=True,create=False))
            event_path=session_event_path(w,s)
            try:handles.append(AnchoredStorage.windows_handle(event_path.parent,directory=True,create=False)); descriptor=AnchoredStorage.windows_file_descriptor(event_path,create=False)
            except OSError:event_path=legacy_session_event_path(w,s); descriptor=AnchoredStorage.windows_file_descriptor(event_path,create=False)
            with os.fdopen(descriptor,"rb") as stream:descriptor=None; body=stream.read()
        finally:
            if descriptor is not None:os.close(descriptor)
            for handle in reversed(handles):AnchoredStorage.close_windows_handle(handle)
    else:
        descriptors=[os.open(w,os.O_RDONLY|getattr(os,"O_DIRECTORY",0)|getattr(os,"O_NOFOLLOW",0))]; session_descriptor=None
        try:
            for name in (".breadboard","sessions"):descriptors.append(AnchoredStorage.open_directory(descriptors[-1],name,create=False))
            try:
                session_descriptor=AnchoredStorage.open_directory(descriptors[-1],s,create=False)
                body=AnchoredStorage.read_at(session_descriptor,"session_events.jsonl"); event_path=session_event_path(w,s)
            except FileNotFoundError:
                body=AnchoredStorage.read_at(descriptors[-1],f"{s}.events.jsonl"); event_path=legacy_session_event_path(w,s)
        finally:
            if session_descriptor is not None:os.close(session_descriptor)
            for descriptor in reversed(descriptors):os.close(descriptor)
    return Session.restore([event_from_record(json.loads(line)) for line in body.decode().splitlines() if line.strip()]),event_path
def load_session(workspace,session_id):
    try:return _load_anchored(_workspace(w=Path(workspace)),session_id)
    except FileNotFoundError:raise
    except OSError as error:raise FileNotFoundError(f"session not found: {session_id}") from error
def _session_names(w):
    if os.name=="nt":
        handles=[]
        try:
            for path in (w,w/".breadboard",session_directory(w)):handles.append(AnchoredStorage.windows_handle(path,directory=True,create=False))
            return os.listdir(session_directory(w))
        finally:
            for handle in reversed(handles):AnchoredStorage.close_windows_handle(handle)
    descriptors=[os.open(w,os.O_RDONLY|getattr(os,"O_DIRECTORY",0)|getattr(os,"O_NOFOLLOW",0))]
    try:
        for name in (".breadboard","sessions"):descriptors.append(AnchoredStorage.open_directory(descriptors[-1],name,create=False))
        return os.listdir(descriptors[-1])
    finally:
        for descriptor in reversed(descriptors):os.close(descriptor)
def list_sessions(a):
    w=_workspace(a)
    try:
        suffix=".events.jsonl";names=_session_names(w)
        session_ids={name[:-len(suffix)] if name.endswith(suffix) else name for name in names}
        rows=[];refs=[]
        for session_id in sorted(session_ids):
            try:
                s,p=load_session(w,session_id);v=s.read_model
                rows.append({"session_id":v.session_id,"status":v.status,"event_count":v.event_count});refs.append(portable_ref(p,w))
            except Exception:pass
        return OperationResult.success(["session","list"],{"sessions":rows,"count":len(rows)},refs,stage="session.list")
    except Exception as e:return from_exception(["session","list"],e,"session.list")
def get(a,command_name="get"):
    w=_workspace(a)
    try:s,p=load_session(w,a.SESSION_ID); v=s.read_model; return OperationResult.success(["session",command_name],{"session":_view(v)},[portable_ref(p,w)],{"lock":v.effective_lock_hash,"task":v.task_hash},stage=f"session.{command_name}")
    except Exception as e:return from_exception(["session",command_name],e,f"session.{command_name}")
def _mutate(a,name,fn):
    w=_workspace(a)
    try:s,p=load_session(w,a.SESSION_ID); v=fn(s); persist_session(w,s,p); return OperationResult.success(["session",name],{"session":_view(v)},[portable_ref(p,w)],stage=f"session.{name}")
    except Exception as e:return from_exception(["session",name],e,f"session.{name}")
def send_input(a):return _mutate(a,"send-input",lambda s:s.input(a.content if getattr(a,"content",None) is not None else a.TEXT))
def approve(a):return _mutate(a,"approve",lambda s:s.resolve_approval(a.request_id,a.decision))
def resume(a):return _mutate(a,"resume",lambda s:s.resume())
def cancel(a):return _mutate(a,"cancel",lambda s:s.cancel(getattr(a,"reason",None) or "operator request"))
def events(a):
    w=_workspace(a)
    try:s,p=load_session(w,a.SESSION_ID); return OperationResult.success(["session","events"],{"session_id":a.SESSION_ID,"events":[x.as_dict() for x in s.events]},[portable_ref(p,w)],stage="session.events")
    except Exception as e:return from_exception(["session","events"],e,"session.events")
def _artifact_rows(w,s):
    rows={};prefix=f"{s}.";handles=[];descriptors=[]
    try:
        if os.name=="nt":
            try:
                for path in (w,w/".breadboard",w/".breadboard"/"artifacts",w/".breadboard"/"artifacts"/"manifests"):handles.append(AnchoredStorage.windows_handle(path,directory=True,create=False))
            except FileNotFoundError:return []
            root=w/".breadboard"/"artifacts"/"manifests";names=os.listdir(root)
            def read(name):
                descriptor=AnchoredStorage.windows_file_descriptor(root/name,create=False)
                with os.fdopen(descriptor,"rb") as stream:return stream.read()
        else:
            descriptors=[os.open(w,os.O_RDONLY|getattr(os,"O_DIRECTORY",0)|getattr(os,"O_NOFOLLOW",0))]
            try:
                for name in (".breadboard","artifacts","manifests"):descriptors.append(AnchoredStorage.open_directory(descriptors[-1],name,create=False))
            except FileNotFoundError:return []
            names=os.listdir(descriptors[-1])
            def read(name):return AnchoredStorage.read_at(descriptors[-1],name)
        for name in sorted(names):
            if not name.startswith(prefix) or not name.endswith(".json"):continue
            digest=name[len(prefix):-5];body=read(name)
            if len(digest)!=64 or any(c not in "0123456789abcdef" for c in digest) or hashlib.sha256(body).hexdigest()!=digest:raise ValueError("artifact manifest digest mismatch")
            document=json.loads(body)
            if document.get("schema_version")!="bb.artifact_manifest.v1" or document.get("session_id")!=s or not isinstance(document.get("artifacts"),list):raise ValueError("invalid artifact manifest")
            for row in document["artifacts"]:
                if not isinstance(row,dict) or not isinstance(row.get("name"),str):raise ValueError("invalid artifact manifest row")
                previous=rows.setdefault(row["name"],row)
                if previous!=row:raise ValueError("conflicting artifact manifest rows")
        return [rows[name] for name in sorted(rows)]
    finally:
        for descriptor in reversed(descriptors):os.close(descriptor)
        for handle in reversed(handles):AnchoredStorage.close_windows_handle(handle)
def artifacts(a):
    w=_workspace(a)
    try:s,p=load_session(w,a.SESSION_ID); rows=_artifact_rows(w,a.SESSION_ID)
    except Exception as e:return from_exception(["session","artifacts"],e,"session.artifacts")
    return OperationResult.success(["session","artifacts"],{"session_id":a.SESSION_ID,"artifacts":rows},[portable_ref(p,w)],stage="session.artifacts")
