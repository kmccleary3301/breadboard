from __future__ import annotations
import argparse
import os
from pathlib import Path
from typing import Sequence
from . import artifact, integration, session, system
from breadboard.product.operations.model import from_exception
from .result import emit


def _w(a):
    return Path(getattr(a, "workspace", None) or Path.cwd()).expanduser().absolute()


def _enabled(name):
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _harness_handler(name):
    def invoke(args):
        from . import harness

        return getattr(harness, name)(args)

    return invoke


def _common(p):
    p.add_argument("--workspace", metavar="DIR")


def _harness(ns):
    p = ns.add_parser("harness", help="author and run product harnesses")
    _common(p)
    s = p.add_subparsers(dest="command", required=True)
    x = s.add_parser("create")
    x.add_argument("--out")
    x.set_defaults(handler=_harness_handler("init"))
    x = s.add_parser("list")
    x.add_argument("--directory")
    x.set_defaults(handler=_harness_handler("list_harnesses"))
    x = s.add_parser(
        "package", help="capture an immutable module package without executing it"
    )
    x.add_argument("PATH")
    x.add_argument("--out", required=True)
    x.set_defaults(handler=_harness_handler("package"))
    x = s.add_parser(
        "publish", help="publish a retained harness Lock to a generation target"
    )
    x.add_argument("TARGET")
    x.add_argument("--lock", required=True)
    x.add_argument("--expected-revision", type=int, required=True)
    x.add_argument("--request-id", required=True)
    x.set_defaults(handler=_harness_handler("publish"))
    commands = ("get", "update", "validate", "explain", "lock", "run")
    for n in commands:
        x = s.add_parser(n)
        x.add_argument("PATH", nargs="?" if n == "run" else None)
        if n == "update":
            x.add_argument("--from", dest="source")
        if n == "explain":
            x.add_argument("--strict", action="store_true")
        if n == "lock":
            x.add_argument("--out")
            x.add_argument("--check", action="store_true")
        if n == "run":
            t = x.add_mutually_exclusive_group(required=True)
            t.add_argument("--server")
            t.add_argument("--local", action="store_true")
            s2 = x.add_mutually_exclusive_group()
            s2.add_argument("--task")
            s2.add_argument("--module-input", metavar="PATH")
            x.add_argument("--module-authority", metavar="PATH")
            selectors = x.add_mutually_exclusive_group()
            selectors.add_argument("--lock")
            selectors.add_argument("--target")
        x.set_defaults(handler=_harness_handler(n))


def _harness_lock(ns):
    p = ns.add_parser("harness-lock", help="inspect effective harness locks")
    _common(p)
    s = p.add_subparsers(dest="command", required=True)
    x = s.add_parser("get")
    x.add_argument("PATH")
    x.set_defaults(handler=_harness_handler("get_lock"))


def _session(ns):
    p = ns.add_parser("session", help="operate Sessions")
    _common(p)
    p.add_argument("--server")
    s = p.add_subparsers(dest="command", required=True)
    s.add_parser("list").set_defaults(handler=session.list_sessions)
    x = s.add_parser("get")
    x.add_argument("SESSION_ID")
    x.set_defaults(handler=lambda a: session.get(a, "get"))
    for n in ("events", "artifacts"):
        x = s.add_parser(n)
        x.add_argument("SESSION_ID")
        x.set_defaults(handler=getattr(session, n))
    x = s.add_parser("send-input")
    x.add_argument("SESSION_ID")
    x.add_argument("TEXT", nargs="?")
    i = x.add_mutually_exclusive_group()
    i.add_argument("--content")
    i.add_argument("--module-input", metavar="PATH")
    x.add_argument("--idempotency-key")
    x.set_defaults(handler=session.send_input)
    x = s.add_parser("approve")
    x.add_argument("SESSION_ID")
    x.add_argument("request_id")
    x.add_argument("decision")
    x.add_argument("--idempotency-key")
    x.set_defaults(handler=session.approve)
    for n in ("resume", "cancel"):
        x = s.add_parser(n)
        x.add_argument("SESSION_ID")
        x.add_argument("--idempotency-key")
        if n == "cancel":
            x.add_argument("--reason")
        x.set_defaults(handler=getattr(session, n))
    x = s.add_parser("checkpoint")
    x.add_argument("SESSION_ID")
    x.add_argument("--reason", required=True)
    x.add_argument("--request-id", dest="request_id", required=True)
    x.set_defaults(handler=session.checkpoint)
    x = s.add_parser("adopt")
    x.add_argument("SESSION_ID")
    x.add_argument("--checkpoint", required=True)
    x.add_argument("--lock", required=True)
    x.add_argument("--request-id", dest="request_id", required=True)
    x.set_defaults(handler=session.adopt)


def _integration(ns):
    p = ns.add_parser("integration", help="discover integrations")
    _common(p)
    s = p.add_subparsers(dest="command", required=True)
    s.add_parser("list").set_defaults(handler=integration.list_integrations)
    x = s.add_parser("get")
    x.add_argument("INTEGRATION_ID")
    x.set_defaults(handler=integration.get)
    x = s.add_parser("probe")
    x.add_argument("INTEGRATION_ID", nargs="?")
    x.set_defaults(handler=integration.probe)


def _artifact(ns):
    p = ns.add_parser("artifact", help="inspect artifacts")
    _common(p)
    s = p.add_subparsers(dest="command", required=True)
    s.add_parser("list").set_defaults(handler=artifact.list_artifacts)
    for n in ("get", "verify"):
        x = s.add_parser(n)
        x.add_argument("REF")
        x.add_argument("--size", type=int)
        x.add_argument("--media-type")
        x.set_defaults(
            handler=(lambda a, n=n: artifact.get(a, n))
            if n != "verify"
            else artifact.verify
        )


def _system(ns):
    p = ns.add_parser("system", help="inspect installed product")
    _common(p)
    s = p.add_subparsers(dest="command", required=True)
    s.add_parser("describe").set_defaults(handler=lambda a: system.describe(_w(a)))
    for n, fn in (("health", system.health), ("schemas", system.schemas)):
        s.add_parser(n).set_defaults(
            handler=lambda a, n=n, fn=fn: fn(["system", n], _w(a))
        )


def _research_compare(arguments):
    import asyncio
    from breadboard.product.operations.model import OperationContext
    from breadboard.product.operations.research import (
        CompareResearchRequest,
        compare_research,
    )

    pair = tuple(arguments.compare.split(","))
    request = CompareResearchRequest(
        arguments.definition,
        arguments.world,
        arguments.generation,
        arguments.projection,
        pair,
    )
    return asyncio.run(
        compare_research(
            request,
            OperationContext(
                workspace=_w(arguments).resolve(), reference_root=Path.cwd()
            ),
        )
    )


def _research(ns):
    parser = ns.add_parser("research", help="compare recorded Sessions")
    _common(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    compare = commands.add_parser("compare")
    for name in ("definition", "world", "generation", "projection", "compare"):
        compare.add_argument("--" + name, required=True)
    compare.set_defaults(handler=_research_compare)


def build_parser():
    p = argparse.ArgumentParser(
        prog="breadboard",
        description="BreadBoard product system, harness, session, integration, and artifact CLI.",
    )
    p.add_argument("--json", action="store_true", help="emit bb.cli.result.v1 JSON")
    p.add_argument("--quiet", action="store_true")
    ns = p.add_subparsers(dest="namespace", required=True)
    _system(ns)
    _harness(ns)
    _harness_lock(ns)
    _session(ns)
    _integration(ns)
    _artifact(ns)
    _research(ns)
    if _enabled("BREADBOARD_ENABLE_E4_API"):
        from . import e4

        e4.register(ns)
    return p


def main(argv: Sequence[str] | None = None):
    a = build_parser().parse_args(argv)
    try:
        r = a.handler(a)
    except Exception as e:
        return emit(
            from_exception([a.namespace, a.command], e), bool(a.json), bool(a.quiet)
        )
    return r if isinstance(r, int) else emit(r, bool(a.json), bool(a.quiet))


if __name__ == "__main__":
    raise SystemExit(main())
