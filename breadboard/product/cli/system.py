from __future__ import annotations
import json,sysconfig
from pathlib import Path
from typing import Any
from .result import CliResult,from_exception,portable_ref
def resource_path(relative:str)->Path:
    root=Path(__file__).resolve().parents[3]; p=root/relative
    return p if p.exists() else Path(sysconfig.get_path("data"))/relative
def candidate_operations()->list[dict[str,Any]]:
    ops=json.loads(resource_path("contracts/public/operations.v1.json").read_text()) .get("operations",[])
    if not isinstance(ops,list):raise ValueError("operation catalog does not contain an operations list")
    return [x for x in ops if isinstance(x,dict)]
def _rows():
    out=[]
    for x in candidate_operations():
        b=x.get("bindings",{}).get("bbh",{})
        if isinstance(x.get("operation_id"),str) and isinstance(b,dict) and isinstance(b.get("command"),str):out.append({"operation_id":x["operation_id"],"command":b["command"],"status":str(x.get("status") or "candidate")})
    return sorted(out,key=lambda x:x["operation_id"])
def describe(command:list[str],workspace:Path):
    try:return CliResult.success(command,{"system":"breadboard","operation_count":len(_rows()),"operations":_rows(),"result_schema":"bb.cli.result.v1","workspace":"."},next_actions=["breadboard system health"],stage="system.describe")
    except Exception as e:return from_exception(command,e,"system.describe")
def health(command:list[str],workspace:Path):
    try:
        root=workspace.expanduser().resolve()
        if not root.exists():return CliResult.failure(command,3,"workspace_unavailable",f"workspace does not exist: {portable_ref(root,root)}","system.health")
        m=root/".breadboard"; return CliResult.success(command,{"workspace":".","workspace_exists":True,"metadata_dir":portable_ref(m,root),"metadata_exists":m.is_dir(),"python":sysconfig.get_platform()},stage="system.health")
    except Exception as e:return from_exception(command,e,"system.health")
def schemas(command:list[str],workspace:Path):
    try:
        names=sorted(p.name for p in resource_path("contracts/public/schemas").glob("*.schema.json")); return CliResult.success(command,{"schema_count":len(names),"schemas":names},stage="system.schemas")
    except Exception as e:return from_exception(command,e,"system.schemas")
