"""BreadBoard harness authoring command-line front door."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import socket
import sys
import threading
import time
from collections.abc import Iterator, Sequence
from pathlib import Path

EXIT_OK = 0
EXIT_VALIDATION_FAILURE = 2
EXIT_RESOLUTION_FAILURE = 3
EXIT_RUNTIME_FAILURE = 4
EXIT_LOCK_DRIFT = 5


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _print_error(message: str) -> None:
    print(f"bbh: {message}", file=sys.stderr)
def _not_implemented(command: str, item: str) -> int:
    _print_error(f"{command} is not implemented yet; see Phase 20 item {item}")
    return EXIT_VALIDATION_FAILURE




def _copy_bundle(files: Sequence[tuple[Path, Path]]) -> int:
    conflicts = [destination for _, destination in files if destination.exists()]
    if conflicts:
        _print_error(
            "refusing to overwrite existing path(s): "
            + ", ".join(str(path) for path in conflicts)
        )
        return EXIT_VALIDATION_FAILURE
    try:
        for source, destination in files:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    except OSError as exc:
        _print_error(str(exc))
        return EXIT_RESOLUTION_FAILURE
    return EXIT_OK


def _harness_init(args: argparse.Namespace) -> int:
    root = _repo_root()
    out_dir = Path(args.out or ".").expanduser()
    source_dir = root / "agent_configs" / "templates"
    files = (
        (
            source_dir / "minimal_harness.v2.yaml",
            out_dir / "minimal_harness.v2.yaml",
        ),
        (
            source_dir / "prompts" / "minimal_system.md",
            out_dir / "prompts" / "minimal_system.md",
        ),
    )
    result = _copy_bundle(files)
    if result == EXIT_OK and not args.quiet:
        print(f"Created {files[0][1]}")
        print(f"Next: bbh harness validate {files[0][1]}")
    return result


def _harness_validate(args: argparse.Namespace) -> int:
    from agentic_coder_prototype.compilation.v2_loader import load_agent_config_view

    try:
        load_agent_config_view(args.PATH)
    except (OSError, ValueError) as exc:
        _print_error(str(exc))
        return EXIT_VALIDATION_FAILURE
    if args.json:
        print(json.dumps({"ok": True, "path": args.PATH}, sort_keys=True))
    elif not args.quiet:
        print(f"Valid harness config: {args.PATH}")
    return EXIT_OK


def _harness_explain(args: argparse.Namespace) -> int:
    from scripts.authoring.explain_agent_config import main as explain_main

    argv = ["--config", args.PATH]
    if args.strict:
        argv.append("--strict")
    return explain_main(argv)


@contextlib.contextmanager
def _local_server() -> Iterator[str]:
    import uvicorn

    from agentic_coder_prototype.api.cli_bridge.app import create_app

    previous_legacy_routes = os.environ.pop("BREADBOARD_LEGACY_ROUTES", None)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        if previous_legacy_routes is not None:
            os.environ["BREADBOARD_LEGACY_ROUTES"] = previous_legacy_routes
        raise RuntimeError("local create_app server did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        if previous_legacy_routes is not None:
            os.environ["BREADBOARD_LEGACY_ROUTES"] = previous_legacy_routes
        if thread.is_alive():
            raise RuntimeError("local create_app server did not stop")

def _poll_records(client: object, session_id: str) -> object:
    deadline = time.monotonic() + 5
    while True:
        response = client.read_session_records(session_id)
        if _record_count(response) > 0 or time.monotonic() >= deadline:
            return response
        time.sleep(0.1)




def _record_count(response: object) -> int:
    if not isinstance(response, dict):
        return 0
    records = response.get("records")
    if isinstance(records, list):
        return len(records)
    total = response.get("total")
    return int(total) if isinstance(total, int) else 0

def _run_session(base_url: str, args: argparse.Namespace) -> int:
    import breadboard_sdk

    task = args.task or "List files"
    client = breadboard_sdk.BreadboardClient(base_url)
    session = client.create_session(config_path=args.PATH, task=task)
    session_id = str(session["session_id"])
    client.post_input(session_id, content=task)
    records = _poll_records(client, session_id)
    for event in client.stream_events(session_id, query={"replay": "true"}):
        event_type = str(event.get("type") or "") if isinstance(event, dict) else ""
        if event_type == "error":
            payload = event.get("payload") if isinstance(event, dict) else None
            raise RuntimeError(f"session event stream failed: {payload}")
        if event_type in {"completion", "run_finished"}:
            break
    result = {"ok": True, "session_id": session_id, "record_count": _record_count(records)}
    if args.json:
        print(json.dumps(result, sort_keys=True))
    elif not args.quiet:
        print(f"Session {session_id} completed; record count: {result['record_count']}")
    return EXIT_OK


def _harness_run(args: argparse.Namespace) -> int:
    try:
        if args.local:
            with _local_server() as base_url:
                return _run_session(base_url, args)
        return _run_session(args.server, args)
    except Exception as exc:
        _print_error(str(exc))
        return EXIT_RUNTIME_FAILURE


LANE_MANIFEST_SKELETON = """\
schema_version: bb.e4.lane_manifest.v1
lane_id: new_lane
config_id: new_lane
title: New E4 lane
agent_config_ref: null
kind: probe
status: draft
target:
  family: new_target
  version: "0"
  package_ref: null
  source_freeze_ref: null
capture:
  strategy: probe_argv
  argv:
    - echo
    - replace-with-capture-command
  inputs: []
  workspace_template: null
normalize:
  mode: identity
  record_builders: []
  projection_constants: {}
  required_records: []
  required_roles: []
replay:
  mode: stored
  comparator_class: semantic
compare:
  comparator: semantic
  assertions: []
claim:
  scope:
    behaviors:
      - replace-with-proven-behavior
    surfaces: []
  exclusions: []
artifacts_root: docs_tmp/e4/new_lane
notes: Replace placeholder values before capture.
"""


def _lane_init(args: argparse.Namespace) -> int:
    out_dir = Path(args.out or ".").expanduser()
    manifest_path = out_dir / "lane.manifest.yaml"
    if manifest_path.exists():
        _print_error(f"refusing to overwrite existing path: {manifest_path}")
        return EXIT_VALIDATION_FAILURE
    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(LANE_MANIFEST_SKELETON, encoding="utf-8")
    except OSError as exc:
        _print_error(str(exc))
        return EXIT_RESOLUTION_FAILURE
    if not args.quiet:
        print(f"Created {manifest_path}")
        print(f"Next: bbh lane validate {manifest_path}")
    return EXIT_OK


def _lane_validate(args: argparse.Namespace) -> int:
    from scripts.authoring.validate_lane import (
        LaneDefValidationError,
        load_lane_manifest,
    )

    try:
        manifest = load_lane_manifest(Path(args.PATH))
    except (OSError, LaneDefValidationError) as exc:
        _print_error(str(exc))
        return EXIT_VALIDATION_FAILURE
    if args.json:
        print(
            json.dumps(
                {"ok": True, "lane_id": manifest["lane_id"], "path": args.PATH},
                sort_keys=True,
            )
        )
    elif not args.quiet:
        print(f"Valid lane manifest: {args.PATH}")
    return EXIT_OK


def _lane_lock(args: argparse.Namespace) -> int:
    from scripts.authoring.validate_lane import (
        LaneDefValidationError,
        load_lane_manifest,
    )
    from scripts.e4_parity.compile_lane_lock import main as compile_main

    manifest_path = Path(args.PATH)
    argv = ["compile", str(manifest_path)]
    if args.out:
        try:
            lane_id = str(load_lane_manifest(manifest_path)["lane_id"])
        except (OSError, LaneDefValidationError) as exc:
            _print_error(str(exc))
            return EXIT_VALIDATION_FAILURE
        out_dir = Path(args.out)
        argv.extend(
            [
                "--lock",
                str(out_dir / f"{lane_id}.lock.json"),
                "--sidecar",
                str(out_dir / f"{lane_id}.packet_constants.v1.json"),
            ]
        )
    if args.check:
        argv.append("--check")
    return compile_main(argv)


def _lane_capture(args: argparse.Namespace) -> int:
    from scripts.authoring.validate_lane import (
        LaneDefValidationError,
        load_lane_manifest,
    )
    from scripts.e4_parity.run_lane import main as run_lane_main

    manifest_path = Path(args.MANIFEST)
    try:
        lane_id = str(load_lane_manifest(manifest_path)["lane_id"])
    except (OSError, LaneDefValidationError) as exc:
        _print_error(str(exc))
        return EXIT_VALIDATION_FAILURE
    out_dir = Path(args.out) if args.out else _repo_root() / "docs_tmp" / "bbh_capture" / lane_id
    argv = ["--lane", lane_id, "--stage", "capture", "--out", str(out_dir)]
    if args.json:
        argv.append("--json")
    return run_lane_main(argv)


def _add_leaf(
    namespace: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    *,
    help_text: str,
    command: str,
    item: str,
) -> argparse.ArgumentParser:
    parser = namespace.add_parser(name, help=help_text, description=help_text)
    parser.set_defaults(handler=lambda _args: _not_implemented(command, item))
    return parser


def build_parser() -> argparse.ArgumentParser:
    """Build the complete public command tree without importing command backends."""
    parser = argparse.ArgumentParser(
        prog="bbh",
        description="BreadBoard harness and E4 lane authoring front door.",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument("--quiet", action="store_true", help="suppress nonessential output")

    namespaces = parser.add_subparsers(dest="namespace", required=True)

    harness = namespaces.add_parser(
        "harness", help="operate on product harness configurations"
    )
    harness_commands = harness.add_subparsers(dest="command", required=True)

    harness_init = _add_leaf(
        harness_commands,
        "init",
        help_text="create a minimal product harness configuration",
        command="harness init",
        item="G2",
    )
    harness_init.add_argument("--out", metavar="DIR")
    harness_init.set_defaults(handler=_harness_init)

    harness_validate = _add_leaf(
        harness_commands,
        "validate",
        help_text="validate a product harness configuration",
        command="harness validate",
        item="G2",
    )
    harness_validate.add_argument("PATH")
    harness_validate.set_defaults(handler=_harness_validate)

    harness_explain = _add_leaf(
        harness_commands,
        "explain",
        help_text="explain a resolved product harness configuration",
        command="harness explain",
        item="G2",
    )
    harness_explain.add_argument("PATH")
    harness_explain.add_argument("--strict", action="store_true")
    harness_explain.set_defaults(handler=_harness_explain)

    harness_run = _add_leaf(
        harness_commands,
        "run",
        help_text="run a product harness configuration",
        command="harness run",
        item="G3",
    )
    harness_run.add_argument("PATH")
    run_target = harness_run.add_mutually_exclusive_group(required=True)
    run_target.add_argument("--server", metavar="URL")
    run_target.add_argument("--local", action="store_true")
    harness_run.add_argument("--task", metavar="TEXT")
    harness_run.set_defaults(handler=_harness_run)

    lane = namespaces.add_parser(
        "lane", help="operate on E4 conformance lane manifests"
    )
    lane_commands = lane.add_subparsers(dest="command", required=True)

    lane_init = _add_leaf(
        lane_commands,
        "init",
        help_text="create an E4 lane manifest",
        command="lane init",
        item="G2",
    )
    lane_init.add_argument("--out", metavar="DIR")
    lane_init.set_defaults(handler=_lane_init)

    lane_validate = _add_leaf(
        lane_commands,
        "validate",
        help_text="validate an E4 lane manifest",
        command="lane validate",
        item="G2",
    )
    lane_validate.add_argument("PATH")
    lane_validate.set_defaults(handler=_lane_validate)

    lane_lock = _add_leaf(
        lane_commands,
        "lock",
        help_text="compile or check an E4 lane lock",
        command="lane lock",
        item="G4",
    )
    lane_lock.add_argument("PATH")
    lane_lock.add_argument("--check", action="store_true")
    lane_lock.add_argument("--out", metavar="DIR")
    lane_lock.set_defaults(handler=_lane_lock)

    lane_capture = _add_leaf(
        lane_commands,
        "capture",
        help_text="run the capture stage for an E4 lane manifest",
        command="lane capture",
        item="G4",
    )
    lane_capture.add_argument("MANIFEST")
    lane_capture.add_argument("--out", metavar="DIR")
    lane_capture.set_defaults(handler=_lane_capture)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and dispatch the selected command."""
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
