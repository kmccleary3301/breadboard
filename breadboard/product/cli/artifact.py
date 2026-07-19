from __future__ import annotations
import hashlib
from pathlib import Path
from typing import Any
from breadboard.product.runtime.artifacts import ArtifactRef,ArtifactStore
from .result import CliResult,from_exception,portable_ref
def _workspace(a):return Path(getattr(a,"workspace",None) or Path.cwd()).expanduser().resolve()
def _store(w):return ArtifactStore(w/".breadboard"/"artifacts")
def _ref(v,size=None,media=None):
    if not isinstance(v,str) or not v.startswith("sha256:") or len(v)!=71 or any(c not in "0123456789abcdef" for c in v[7:]):raise ValueError("artifact ref must be sha256:<64 lowercase hex characters>")
    return ArtifactRef(v,int(size or 0),media or "application/octet-stream")
def list_artifacts(a):
    w=_workspace(a); root=w/".breadboard"/"artifacts"/"sha256"
    try:
        rows=[]
        if root.is_dir():
            for d in sorted(root.iterdir()):
                if d.is_dir():
                    rows += [{"digest":f"sha256:{p.name}","size_bytes":p.stat().st_size,"media_type":"application/octet-stream"} for p in sorted(d.iterdir()) if p.is_file() and len(p.name)==64]
        return CliResult.success(["artifact","list"],{"artifacts":rows,"count":len(rows)},refs=[portable_ref(root,w)] if root.exists() else [],stage="artifact.list")
    except Exception as e:return from_exception(["artifact","list"],e,"artifact.list")
def get(a,command_name="get"):
    w=_workspace(a)
    try:
        r=_ref(a.REF,getattr(a,"size",None),getattr(a,"media_type",None)); body=_store(w).read(r); return CliResult.success(["artifact",command_name],{"artifact":r.as_dict(),"bytes":len(body)},hashes={"artifact":r.digest},stage=f"artifact.{command_name}")
    except Exception as e:return from_exception(["artifact",command_name],e,f"artifact.{command_name}")
def verify(a):
    w=_workspace(a)
    try:
        r=_ref(a.REF,getattr(a,"size",None),getattr(a,"media_type",None)); body=_store(w).read(r); digest="sha256:"+hashlib.sha256(body).hexdigest(); return CliResult.success(["artifact","verify"],{"artifact":r.as_dict(),"verified":digest==r.digest},hashes={"artifact":digest},stage="artifact.verify")
    except Exception as e:return from_exception(["artifact","verify"],e,"artifact.verify")
