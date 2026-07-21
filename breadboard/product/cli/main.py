from __future__ import annotations
import argparse,json
from pathlib import Path
from typing import Sequence
from ..evidence import BreadBoardWorkspace, LaneLockError, LaneResolutionError, StageReport, author_lane, build_lane_lock, init_lane, load_lane, lock_lane, validate_lane
from ..evidence.lanes import LANE_SCHEMA_VERSION, MANIFEST_SCHEMA_VERSION, REF_NAMES
from . import artifact,harness,integration,session,system
from .result import CliResult,emit,from_exception
def _w(a):return Path(getattr(a,"workspace",None) or Path.cwd()).expanduser().absolute()
def _lane_source(value,workspace=None,*,by_id=False):
    p=Path(value).expanduser()
    if by_id or (workspace is not None and p.parent==Path(".") and p.suffix not in (".json",".yaml",".yml")):
        root=workspace.lanes_root;candidates=tuple(path for path in sorted((*root.glob("*.manifest.json"),*root.glob("*.manifest.yaml"),*root.glob("*.manifest.yml"))) if load_lane(path)["lane_id"]==str(value))
        if len(candidates)>1:raise ValueError(f"multiple lane manifests found for {value}")
        p=candidates[0] if candidates else (workspace.lane_manifest_path(value) if by_id else p)
    if p.is_symlink() or any(parent.is_symlink() for parent in p.absolute().parents):raise ValueError(f"lane source contains a symlink: {p}")
    if not p.exists():raise FileNotFoundError(p)
    if not p.is_file():raise IsADirectoryError(p)
    if not p.stat().st_mode&0o444:raise PermissionError(p)
    return p
def _unsupported(a):return CliResult.failure(getattr(a,"_command",[]),6,"unsupported_operation","operation is not available in this installation","command",next_actions=["breadboard system describe"],status="blocked")
def _lane_init(a):
    w=_w(a);refs={name:f"refs/{name}.json" for name in REF_NAMES}
    try:
        root=Path(a.out).expanduser().absolute() if a.out else w
        p=init_lane(BreadBoardWorkspace(root),a.lane_id,references=refs,execute=["capture"])
        from .result import portable_ref
        return CliResult.success(["lane","init"],{"path":portable_ref(p,root),"lane_id":a.lane_id},[portable_ref(p,root)],stage="lane.init")
    except Exception as e:return from_exception(["lane","init"],e,"lane.init")
def _lane_validate(a):
    w=_w(a);p=_lane_source(a.PATH,BreadBoardWorkspace(w))
    try:
        x=load_lane(p)
        if x.get("_authoring_default") is False:
            from scripts.authoring.validate_lane import load_lane_manifest
            from scripts.e4_parity.lane_definitions import load_lane_def
            x=load_lane_manifest(p) if x.get("schema_version")=="bb.e4.lane_manifest.v1" else load_lane_def(p)
        from .result import portable_ref
        return CliResult.success(["lane","validate"],{"path":portable_ref(p,w),"lane_id":x["lane_id"]},[portable_ref(p,w)],stage="lane.validate")
    except ValueError as e:return CliResult.failure(["lane","validate"],2,"invalid_lane",str(e),"lane.validate")
    except Exception as e:return from_exception(["lane","validate"],e,"lane.validate")
def _lane_lock(a):
    try:
        p=_lane_source(a.PATH,BreadBoardWorkspace(_w(a)));x=load_lane(p)
        source_root=p.parents[2] if p.parent.name=="lanes" and p.parent.parent.name==".breadboard" else Path(__file__).resolve().parents[3]
        if x.get("_authoring_default") is False:
            from scripts.authoring.validate_lane import load_lane_manifest
            from scripts.e4_parity.compile_lane_lock import main as f
            x=load_lane_manifest(p);v=["compile",str(p)]
            if a.out:v+=["--lock",str(Path(a.out)/f"{x['lane_id']}.lock.json"),"--sidecar",str(Path(a.out)/f"{x['lane_id']}.packet_constants.v1.json")]
            if a.check:v.append("--check")
            return f(v)
        output_root=Path(a.out).expanduser() if a.out else source_root
        if output_root.resolve()!=source_root.resolve():raise LaneLockError("candidate lane locks must be written beside their manifest")
        workspace=BreadBoardWorkspace(output_root);destination=workspace.lane_lock_path(x["lane_id"])
        if a.check:
            expected=build_lane_lock(x,root=source_root,manifest_path=p)
            if destination.is_file() and not destination.is_symlink() and destination.read_bytes()==(json.dumps(expected,allow_nan=False,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n").encode("utf-8"):return CliResult.success(["lane","lock"],{"path":str(destination.relative_to(workspace.root)),"checked":True},stage="lane.lock")
            return CliResult.failure(["lane","lock"],5,"lock_drift","lane lock is missing or differs from deterministic resolution","lane.lock")
        destination=lock_lane(x,workspace,root=source_root,manifest_path=p)
        from .result import portable_ref
        return CliResult.success(["lane","lock"],{"path":portable_ref(destination,workspace.root),"lane_id":x["lane_id"]},[portable_ref(destination,workspace.root)],stage="lane.lock")
    except LaneResolutionError as e:return CliResult.failure(["lane","lock"],3,"path_unavailable",str(e),"lane.lock","Check the workspace-relative reference path.",next_actions=["breadboard system health"])
    except Exception as e:return from_exception(["lane","lock"],e,"lane.lock")
def _lane_create(a):
    try:
        x=load_lane(_lane_source(a.PATH))
        if x.get("schema_version") not in (MANIFEST_SCHEMA_VERSION,LANE_SCHEMA_VERSION):raise ValueError("legacy lanes are read-only")
        p=author_lane(x,BreadBoardWorkspace(_w(a)))
        from .result import portable_ref
        return CliResult.success(["lane","create"],{"path":portable_ref(p,_w(a)),"lane_id":x["lane_id"]},[portable_ref(p,_w(a))],stage="lane.create")
    except Exception as e:return from_exception(["lane","create"],e,"lane.create")
def _lane_get(a):
    try:x=load_lane(_lane_source(a.PATH,BreadBoardWorkspace(_w(a)),by_id=True));x["lane_id"]==a.PATH or (_ for _ in ()).throw(ValueError(f"lane manifest identity differs from requested lane_id: {a.PATH}"));return CliResult.success(["lane","get"],{"lane":x},stage="lane.get")
    except Exception as e:return from_exception(["lane","get"],e,"lane.get")
def _lane_list(a):
    from ..evidence.lanes import iter_authoring_lanes
    return CliResult.success(["lane","list"],{"lanes":[x["lane_id"] for x in iter_authoring_lanes(BreadBoardWorkspace(_w(a)))]},stage="lane.list")
def _lane_stage_report(a):
    try:x=StageReport.from_dict(json.loads(_lane_source(a.PATH).read_text(encoding="utf-8")));return CliResult.success(["lane","stage-report"],{"report":x.as_dict()},stage="lane.stage_report")
    except Exception as e:return from_exception(["lane","stage-report"],e,"lane.stage_report")
def _lane_capture(a):
    try:
        p=_lane_source(a.MANIFEST,BreadBoardWorkspace(_w(a)));x=load_lane(p)
        if x.get("schema_version") in (MANIFEST_SCHEMA_VERSION,LANE_SCHEMA_VERSION):
            from scripts.e4_parity.run_lane import LaneLockDriftError,LaneRunError,run_lane
            try:run_lane(str(x["lane_id"]),stage="capture",out_dir=None,lane_def_dir=p.parent)
            except LaneLockDriftError as exc:return CliResult.failure(["lane","capture"],5,"lock_drift",str(exc),"lane.capture")
            except LaneRunError as exc:
                if "execution is inactive" in str(exc):return CliResult.failure(["lane","capture"],6,"candidate_lane_inactive",str(exc),"lane.capture",status="blocked")
                raise
        from scripts.authoring.validate_lane import load_lane_manifest
        from scripts.e4_parity.run_lane import main as f,run_lane
        x=load_lane_manifest(p);out=Path(a.out) if a.out else _w(a)/"docs_tmp"/"bbh_capture"/str(x["lane_id"])
        if isinstance(x.get("capture"),dict) and x["capture"].get("strategy")=="replay_dump":
            report=run_lane(str(x["lane_id"]),stage="capture",out_dir=out if x.get("status")=="accepted" else None,lane_def_dir=p.parent)
            if not report.get("ok"):return CliResult.failure(["lane","capture"],4,"stored_capture_invalid","stored capture artifacts did not validate","lane.capture",data={"capture":report})
            result=CliResult.success(["lane","capture"],{"capture":report,"requested_out":str(out)},stage="lane.capture");result.warnings.append("stored replay artifacts validated; no new capture process was executed");return result
        v=["--lane",str(x["lane_id"]),"--stage","capture","--out",str(out),"--lane-def-dir",str(p.parent)]
        if a.json:v.append("--json")
        return f(v)
    except Exception as e:return from_exception(["lane","capture"],e,"lane.capture")
def _lane_place(a):return _unsupported(a)
def _common(p):p.add_argument("--workspace",metavar="DIR")
def _harness(ns):
    p=ns.add_parser("harness",help="author and run product harnesses"); _common(p); s=p.add_subparsers(dest="command",required=True)
    for n,fn in (("init",harness.init),("create",harness.init)):
        x=s.add_parser(n);x.add_argument("--out");x.set_defaults(handler=fn)
    x=s.add_parser("list");x.add_argument("--directory");x.set_defaults(handler=harness.list_harnesses)
    for n,fn in (("show",harness.show),("get",harness.get),("update",harness.update),("validate",harness.validate),("explain",harness.explain),("lock",harness.lock),("run",harness.run)):
        x=s.add_parser(n);x.add_argument("PATH");
        if n=="explain":x.add_argument("--strict",action="store_true")
        if n=="lock":x.add_argument("--out");x.add_argument("--check",action="store_true")
        if n=="run":t=x.add_mutually_exclusive_group(required=True);t.add_argument("--server");t.add_argument("--local",action="store_true");x.add_argument("--task")
        x.set_defaults(handler=fn)
def _harness_lock(ns):
    p=ns.add_parser("harness-lock",help="inspect effective harness locks");_common(p);s=p.add_subparsers(dest="command",required=True);x=s.add_parser("get");x.add_argument("PATH");x.set_defaults(handler=harness.get_lock)
def _session(ns):
    p=ns.add_parser("session",help="operate Sessions");_common(p);s=p.add_subparsers(dest="command",required=True);s.add_parser("list").set_defaults(handler=session.list_sessions)
    for n in ("get","show"):
        x=s.add_parser(n);x.add_argument("SESSION_ID");x.set_defaults(handler=lambda a,n=n:session.get(a,n))
    for n in ("events","artifacts"):
        x=s.add_parser(n);x.add_argument("SESSION_ID");x.set_defaults(handler=getattr(session,n))
    x=s.add_parser("send-input");x.add_argument("SESSION_ID");x.add_argument("TEXT",nargs="?");x.add_argument("--content");x.set_defaults(handler=session.send_input)
    x=s.add_parser("send");x.add_argument("SESSION_ID");x.add_argument("TEXT",nargs="?");x.add_argument("--content");x.set_defaults(handler=session.send_input)
    x=s.add_parser("approve");x.add_argument("SESSION_ID");x.add_argument("request_id");x.add_argument("decision");x.set_defaults(handler=session.approve)
    for n in ("resume","cancel"):
        x=s.add_parser(n);x.add_argument("SESSION_ID");
        if n=="cancel":x.add_argument("--reason")
        x.set_defaults(handler=getattr(session,n))
def _integration(ns):
    p=ns.add_parser("integration",help="discover integrations");_common(p);s=p.add_subparsers(dest="command",required=True);s.add_parser("list").set_defaults(handler=integration.list_integrations);x=s.add_parser("get");x.add_argument("INTEGRATION_ID");x.set_defaults(handler=integration.get);x=s.add_parser("probe");x.add_argument("INTEGRATION_ID",nargs="?");x.set_defaults(handler=integration.probe)
def _artifact(ns):
    p=ns.add_parser("artifact",help="inspect artifacts");_common(p);s=p.add_subparsers(dest="command",required=True);s.add_parser("list").set_defaults(handler=artifact.list_artifacts)
    for n in ("get","show","verify"):
        x=s.add_parser(n);x.add_argument("REF");x.add_argument("--size",type=int);x.add_argument("--media-type");x.set_defaults(handler=(lambda a,n=n:artifact.get(a,n)) if n!="verify" else artifact.verify)
def _system(ns):
    p=ns.add_parser("system",help="inspect installed product");_common(p);s=p.add_subparsers(dest="command",required=True)
    for n,fn in (("describe",system.describe),("health",system.health),("schemas",system.schemas)):s.add_parser(n).set_defaults(handler=lambda a,n=n,fn=fn:fn(["system",n],_w(a)))
def _lane(ns):
    p=ns.add_parser("lane",help="operate E4 lanes");_common(p);s=p.add_subparsers(dest="command",required=True);x=s.add_parser("init");x.add_argument("--out");x.add_argument("--lane-id",default="new_lane");x.set_defaults(handler=_lane_init);x=s.add_parser("validate");x.add_argument("PATH");x.set_defaults(handler=_lane_validate);x=s.add_parser("lock");x.add_argument("PATH");x.add_argument("--out");x.add_argument("--check",action="store_true");x.set_defaults(handler=_lane_lock);x=s.add_parser("capture");x.add_argument("MANIFEST");x.add_argument("--out");x.set_defaults(handler=_lane_capture)
    for n,fn,nargs in (("create",_lane_create,1),("get",_lane_get,1),("list",_lane_list,0),("stage-report",_lane_stage_report,1)):
        x=s.add_parser(n)
        if nargs:x.add_argument("PATH")
        x.set_defaults(handler=fn)
    for n in ("claim","compare","normalize","replay","run"):
        x=s.add_parser(n);x.add_argument("PATH",nargs="?");x.set_defaults(handler=_lane_place,_command=["lane",n])
def _placeholders(ns):
    for root,names in (("claim",("evidence","get","list","reverify")),("lane-execution",("cancel","get")),("lane-lock",("get",))):
        p=ns.add_parser(root);_common(p);s=p.add_subparsers(dest="command",required=True)
        for n in names:x=s.add_parser(n);x.add_argument("PATH",nargs="?");x.set_defaults(handler=_unsupported,_command=[root,n])
def build_parser():
    p=argparse.ArgumentParser(prog="breadboard",description="BreadBoard product system, harness, session, integration, and artifact CLI.");p.add_argument("--json",action="store_true",help="emit bb.cli.result.v1 JSON");p.add_argument("--quiet",action="store_true");ns=p.add_subparsers(dest="namespace",required=True);_system(ns);_harness(ns);_harness_lock(ns);_session(ns);_integration(ns);_artifact(ns);_lane(ns);_placeholders(ns);return p
def _legacy_explain(a):
    try:
        from scripts.authoring.explain_agent_config import main as f
        return f(["--config",a.PATH]+(["--strict"] if a.strict else []))
    except Exception as e:return emit(from_exception(["harness","explain"],e,"harness.explain"),False,bool(a.quiet))
def main(argv:Sequence[str]|None=None):
    a=build_parser().parse_args(argv)
    if a.namespace=="harness" and a.command=="explain" and not a.json:return _legacy_explain(a)
    if a.json and a.namespace=="harness" and a.command=="init":
        r=a.handler(a)
        if isinstance(r,CliResult) and r.ok:
            d=Path(a.out or ".");r.data["path"]=str(d/"minimal_harness.v2.yaml");r.data["prompt_path"]=str(d/"prompts/minimal_system.md")
        return r if isinstance(r,int) else emit(r,True,bool(a.quiet))
    try:r=a.handler(a)
    except Exception as e:return emit(from_exception([a.namespace,a.command],e),bool(a.json),bool(a.quiet))
    return r if isinstance(r,int) else emit(r,bool(a.json),bool(a.quiet))
if __name__=="__main__":raise SystemExit(main())
