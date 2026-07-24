from __future__ import annotations
import hashlib
from pathlib import Path
from breadboard.product.runtime.artifacts import ArtifactRef,list_workspace_artifacts,read_workspace_artifact,workspace_artifact_ref
from .result import CliResult,from_exception,portable_ref
def _workspace(a):return Path(getattr(a,"workspace",None) or Path.cwd()).expanduser().resolve()
def _ref(v,w,size=None,media=None):
    if size is not None:return ArtifactRef(v,int(size),media or "application/octet-stream")
    try:return workspace_artifact_ref(w,v,media_type=media or "application/octet-stream")
    except OSError as e:raise PermissionError("artifact path is unavailable") from e
def _read(w,r):
    try:return read_workspace_artifact(w,r)
    except OSError as e:raise PermissionError("artifact path is unavailable") from e
def list_artifacts(a):
    w=_workspace(a); root=w/".breadboard"/"artifacts"/"sha256"
    try:
        rows=[ref.as_dict() for ref in list_workspace_artifacts(w)]
        return CliResult.success(["artifact","list"],{"artifacts":rows,"count":len(rows)},refs=[portable_ref(root,w)] if rows else [],stage="artifact.list")
    except OSError as e:return from_exception(["artifact","list"],PermissionError("artifact store is unavailable"),"artifact.list")
    except Exception as e:return from_exception(["artifact","list"],e,"artifact.list")
def get(a,command_name="get"):
    w=_workspace(a)
    try:
        r=_ref(a.REF,w,getattr(a,"size",None),getattr(a,"media_type",None)); body=_read(w,r); return CliResult.success(["artifact",command_name],{"artifact":r.as_dict(),"bytes":len(body)},hashes={"artifact":r.digest},stage=f"artifact.{command_name}")
    except Exception as e:return from_exception(["artifact",command_name],e,f"artifact.{command_name}")
def verify(a):
    w=_workspace(a)
    try:
        r=_ref(a.REF,w,getattr(a,"size",None),getattr(a,"media_type",None)); body=_read(w,r); digest="sha256:"+hashlib.sha256(body).hexdigest(); return CliResult.success(["artifact","verify"],{"artifact":r.as_dict(),"verified":digest==r.digest},hashes={"artifact":digest},stage="artifact.verify")
    except Exception as e:return from_exception(["artifact","verify"],e,"artifact.verify")
