from __future__ import annotations
import argparse,os
from pathlib import Path
from typing import Sequence
from . import artifact,integration,session,system
from breadboard.product.operations.model import from_exception
from .result import emit
def _w(a):return Path(getattr(a,"workspace",None) or Path.cwd()).expanduser().absolute()
def _enabled(name):return os.environ.get(name,"").strip().lower() in {"1","true","yes","on"}
def _harness_handler(name):
    def invoke(args):
        from . import harness
        return getattr(harness,name)(args)
    return invoke
def _common(p):p.add_argument("--workspace",metavar="DIR")
def _harness(ns):
    p=ns.add_parser("harness",help="author and run product harnesses"); _common(p); s=p.add_subparsers(dest="command",required=True)
    x=s.add_parser("create");x.add_argument("--out");x.set_defaults(handler=_harness_handler("init"))
    if _enabled("BREADBOARD_LEGACY_ROUTES"):
        x=s.add_parser("init");x.add_argument("--out");x.set_defaults(handler=_harness_handler("init"))
    x=s.add_parser("list");x.add_argument("--directory");x.set_defaults(handler=_harness_handler("list_harnesses"))
    commands=("get","update","validate","explain","lock","run")
    if _enabled("BREADBOARD_LEGACY_ROUTES"):commands=("show",)+commands
    for n in commands:
        x=s.add_parser(n);x.add_argument("PATH");
        if n=="update":x.add_argument("--from",dest="source")
        if n=="explain":x.add_argument("--strict",action="store_true")
        if n=="lock":x.add_argument("--out");x.add_argument("--check",action="store_true")
        if n=="run":t=x.add_mutually_exclusive_group(required=True);t.add_argument("--server");t.add_argument("--local",action="store_true");x.add_argument("--task");x.add_argument("--lock")
        x.set_defaults(handler=_harness_handler(n))
def _harness_lock(ns):
    p=ns.add_parser("harness-lock",help="inspect effective harness locks");_common(p);s=p.add_subparsers(dest="command",required=True);x=s.add_parser("get");x.add_argument("PATH");x.set_defaults(handler=_harness_handler("get_lock"))
def _session(ns):
    p=ns.add_parser("session",help="operate Sessions");_common(p);s=p.add_subparsers(dest="command",required=True);s.add_parser("list").set_defaults(handler=session.list_sessions)
    x=s.add_parser("get");x.add_argument("SESSION_ID");x.set_defaults(handler=lambda a:session.get(a,"get"))
    if _enabled("BREADBOARD_ENABLE_LOCAL_MIGRATIONS"):
        x=s.add_parser("bootstrap-local",help="trust one validated local pre-authority session");x.add_argument("SESSION_ID");x.set_defaults(handler=session.bootstrap_local)
    if _enabled("BREADBOARD_LEGACY_ROUTES"):
        x=s.add_parser("show");x.add_argument("SESSION_ID");x.set_defaults(handler=lambda a:session.get(a,"show"))
    for n in ("events","artifacts"):
        x=s.add_parser(n);x.add_argument("SESSION_ID");x.set_defaults(handler=getattr(session,n))
    x=s.add_parser("send-input");x.add_argument("SESSION_ID");x.add_argument("TEXT",nargs="?");x.add_argument("--content");x.set_defaults(handler=session.send_input)
    if _enabled("BREADBOARD_LEGACY_ROUTES"):
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
    if _enabled("BREADBOARD_LEGACY_ROUTES"):
        x=s.add_parser("put");x.add_argument("SOURCE");x.add_argument("--media-type",default="application/octet-stream");x.set_defaults(handler=artifact.put)
    names=("get","verify")+(("show","delete") if _enabled("BREADBOARD_LEGACY_ROUTES") else ())
    for n in names:
        x=s.add_parser(n);x.add_argument("REF");x.add_argument("--size",type=int);x.add_argument("--media-type");x.set_defaults(handler=artifact.delete if n=="delete" else (lambda a,n=n:artifact.get(a,n)) if n!="verify" else artifact.verify)
def _system(ns):
    p=ns.add_parser("system",help="inspect installed product"); _common(p);s=p.add_subparsers(dest="command",required=True)
    s.add_parser("describe").set_defaults(handler=lambda a:system.describe(_w(a)))
    for n,fn in (("health",system.health),("schemas",system.schemas)):s.add_parser(n).set_defaults(handler=lambda a,n=n,fn=fn:fn(["system",n],_w(a)))
def build_parser():
    p=argparse.ArgumentParser(prog="breadboard",description="BreadBoard product system, harness, session, integration, and artifact CLI.");p.add_argument("--json",action="store_true",help="emit bb.cli.result.v1 JSON");p.add_argument("--quiet",action="store_true");ns=p.add_subparsers(dest="namespace",required=True);_system(ns);_harness(ns);_harness_lock(ns);_session(ns);_integration(ns);_artifact(ns)
    if _enabled("BREADBOARD_ENABLE_E4_API"):
        from . import e4
        e4.register(ns)
    return p
def _legacy_explain(a):
    try:
        from scripts.authoring.explain_agent_config import main as f
        return f(["--config",a.PATH]+(["--strict"] if a.strict else []))
    except Exception as e:return emit(from_exception(["harness","explain"],e,"harness.explain"),False,bool(a.quiet))
def main(argv:Sequence[str]|None=None):
    a=build_parser().parse_args(argv)
    if a.namespace=="harness" and a.command=="explain" and not a.json:return _legacy_explain(a)
    try:r=a.handler(a)
    except Exception as e:return emit(from_exception([a.namespace,a.command],e),bool(a.json),bool(a.quiet))
    return r if isinstance(r,int) else emit(r,bool(a.json),bool(a.quiet))
if __name__=="__main__":raise SystemExit(main())
