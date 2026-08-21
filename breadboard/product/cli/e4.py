from __future__ import annotations

import json
import os
from pathlib import Path

from ..evidence import (
    BreadBoardWorkspace,
    LaneLockError,
    LaneResolutionError,
    StageReport,
    author_lane,
    build_lane_lock,
    init_lane,
    load_lane,
    lock_lane,
)
from ..evidence.lanes import LANE_SCHEMA_VERSION, MANIFEST_SCHEMA_VERSION, REF_NAMES
from .result import CliResult, from_exception, portable_ref


def _workspace(args):
    return Path(getattr(args, "workspace", None) or Path.cwd()).expanduser().absolute()


def _lane_source(value, workspace=None, *, by_id=False):
    path = Path(value).expanduser()
    if by_id or (workspace is not None and path.parent == Path(".") and path.suffix not in (".json", ".yaml", ".yml")):
        root = workspace.lanes_root
        candidates = tuple(
            candidate
            for candidate in sorted((*root.glob("*.manifest.json"), *root.glob("*.manifest.yaml"), *root.glob("*.manifest.yml")))
            if load_lane(candidate)["lane_id"] == str(value)
        )
        if len(candidates) > 1:
            raise ValueError(f"multiple lane manifests found for {value}")
        path = candidates[0] if candidates else (workspace.lane_manifest_path(value) if by_id else path)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.absolute().parents):
        raise ValueError(f"lane source contains a symlink: {path}")
    if not path.exists():
        raise FileNotFoundError(path)
    if not path.is_file():
        raise IsADirectoryError(path)
    if not path.stat().st_mode & 0o444:
        raise PermissionError(path)
    return path


def _unsupported(args):
    return CliResult.failure(
        getattr(args, "_command", []), 6, "unsupported_operation",
        "operation is not available in this installation", "command",
        next_actions=["breadboard system describe"], status="blocked",
    )


def _lane_init(args):
    workspace = _workspace(args)
    references = {name: f"refs/{name}.json" for name in REF_NAMES}
    try:
        root = Path(args.out).expanduser().absolute() if args.out else workspace
        path = init_lane(BreadBoardWorkspace(root), args.lane_id, references=references, execute=["capture"])
        return CliResult.success(["lane", "init"], {"path": portable_ref(path, root), "lane_id": args.lane_id}, [portable_ref(path, root)], stage="lane.init")
    except Exception as exc:
        return from_exception(["lane", "init"], exc, "lane.init")


def _lane_validate(args):
    workspace = _workspace(args)
    path = _lane_source(args.PATH, BreadBoardWorkspace(workspace))
    try:
        lane = load_lane(path)
        if lane.get("_authoring_default") is False:
            from scripts.authoring.validate_lane import load_lane_manifest
            from scripts.e4_parity.lane_definitions import load_lane_def
            lane = load_lane_manifest(path) if lane.get("schema_version") == "bb.e4.lane_manifest.v1" else load_lane_def(path)
        return CliResult.success(["lane", "validate"], {"path": portable_ref(path, workspace), "lane_id": lane["lane_id"]}, [portable_ref(path, workspace)], stage="lane.validate")
    except ValueError as exc:
        return CliResult.failure(["lane", "validate"], 2, "invalid_lane", str(exc), "lane.validate")
    except Exception as exc:
        return from_exception(["lane", "validate"], exc, "lane.validate")


def _lane_lock(args):
    try:
        path = _lane_source(args.PATH, BreadBoardWorkspace(_workspace(args)))
        lane = load_lane(path)
        source_root = path.parents[2] if path.parent.name == "lanes" and path.parent.parent.name == ".breadboard" else Path(__file__).resolve().parents[3]
        if lane.get("_authoring_default") is False:
            from scripts.authoring.validate_lane import load_lane_manifest
            from scripts.e4_parity.compile_lane_lock import main as compile_lock
            lane = load_lane_manifest(path)
            argv = ["compile", str(path)]
            if args.out:
                argv += ["--lock", str(Path(args.out) / f"{lane['lane_id']}.lock.json"), "--sidecar", str(Path(args.out) / f"{lane['lane_id']}.packet_constants.v1.json")]
            if args.check:
                argv.append("--check")
            return compile_lock(argv)
        output_root = Path(args.out).expanduser() if args.out else source_root
        if output_root.resolve() != source_root.resolve():
            raise LaneLockError("candidate lane locks must be written beside their manifest")
        workspace = BreadBoardWorkspace(output_root)
        destination = workspace.lane_lock_path(lane["lane_id"])
        if args.check:
            expected = build_lane_lock(lane, root=source_root, manifest_path=path)
            content = (json.dumps(expected, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
            if destination.is_file() and not destination.is_symlink() and destination.read_bytes() == content:
                return CliResult.success(["lane", "lock"], {"path": str(destination.relative_to(workspace.root)), "checked": True}, stage="lane.lock")
            return CliResult.failure(["lane", "lock"], 5, "lock_drift", "lane lock is missing or differs from deterministic resolution", "lane.lock")
        destination = lock_lane(lane, workspace, root=source_root, manifest_path=path)
        return CliResult.success(["lane", "lock"], {"path": portable_ref(destination, workspace.root), "lane_id": lane["lane_id"]}, [portable_ref(destination, workspace.root)], stage="lane.lock")
    except LaneResolutionError as exc:
        return CliResult.failure(["lane", "lock"], 3, "path_unavailable", str(exc), "lane.lock", "Check the workspace-relative reference path.", next_actions=["breadboard system health"])
    except Exception as exc:
        return from_exception(["lane", "lock"], exc, "lane.lock")


def _lane_create(args):
    try:
        lane = load_lane(_lane_source(args.PATH))
        if lane.get("schema_version") not in (MANIFEST_SCHEMA_VERSION, LANE_SCHEMA_VERSION):
            raise ValueError("legacy lanes are read-only")
        path = author_lane(lane, BreadBoardWorkspace(_workspace(args)))
        return CliResult.success(["lane", "create"], {"path": portable_ref(path, _workspace(args)), "lane_id": lane["lane_id"]}, [portable_ref(path, _workspace(args))], stage="lane.create")
    except Exception as exc:
        return from_exception(["lane", "create"], exc, "lane.create")


def _lane_get(args):
    try:
        lane = load_lane(_lane_source(args.PATH, BreadBoardWorkspace(_workspace(args)), by_id=True))
        if lane["lane_id"] != args.PATH:
            raise ValueError(f"lane manifest identity differs from requested lane_id: {args.PATH}")
        return CliResult.success(["lane", "get"], {"lane": lane}, stage="lane.get")
    except Exception as exc:
        return from_exception(["lane", "get"], exc, "lane.get")


def _lane_list(args):
    from ..evidence.lanes import iter_authoring_lanes
    return CliResult.success(["lane", "list"], {"lanes": [lane["lane_id"] for lane in iter_authoring_lanes(BreadBoardWorkspace(_workspace(args)))]}, stage="lane.list")


def _lane_stage_report(args):
    try:
        report = StageReport.from_dict(json.loads(_lane_source(args.PATH).read_text(encoding="utf-8")))
        return CliResult.success(["lane", "stage-report"], {"report": report.as_dict()}, stage="lane.stage_report")
    except Exception as exc:
        return from_exception(["lane", "stage-report"], exc, "lane.stage_report")


def _lane_capture(args):
    previous_workspace_root = os.environ.get("BB_WORKSPACE_ROOT")
    os.environ["BB_WORKSPACE_ROOT"] = str(_workspace(args))
    try:
        path = _lane_source(args.MANIFEST, BreadBoardWorkspace(_workspace(args)))
        lane = load_lane(path)
        if lane.get("schema_version") in (MANIFEST_SCHEMA_VERSION, LANE_SCHEMA_VERSION):
            from scripts.e4_parity.run_lane import LaneLockDriftError, LaneRunError, run_lane
            try:
                run_lane(str(lane["lane_id"]), stage="capture", out_dir=None, lane_def_dir=path.parent)
            except LaneLockDriftError as exc:
                return CliResult.failure(["lane", "capture"], 5, "lock_drift", str(exc), "lane.capture")
            except LaneRunError as exc:
                if "execution is inactive" in str(exc):
                    return CliResult.failure(["lane", "capture"], 6, "candidate_lane_inactive", str(exc), "lane.capture", status="blocked")
                raise
        from scripts.authoring.validate_lane import load_lane_manifest
        from scripts.e4_parity.run_lane import main as run_main
        lane = load_lane_manifest(path)
        output = Path(args.out) if args.out else _workspace(args) / "docs_tmp" / "bbh_capture" / str(lane["lane_id"])
        argv = ["--lane", str(lane["lane_id"]), "--stage", "capture", "--out", str(output), "--lane-def-dir", str(path.parent)]
        if args.json:
            argv.append("--json")
        return run_main(argv)
    except Exception as exc:
        return from_exception(["lane", "capture"], exc, "lane.capture")
    finally:
        if previous_workspace_root is None:
            os.environ.pop("BB_WORKSPACE_ROOT", None)
        else:
            os.environ["BB_WORKSPACE_ROOT"] = previous_workspace_root


def _common(parser):
    parser.add_argument("--workspace", metavar="DIR")


def register_lane(subparsers, *, include_internal: bool = False) -> None:
    lane = subparsers.add_parser("lane", help="operate lanes")
    _common(lane)
    commands = lane.add_subparsers(dest="command", required=True)
    if include_internal:
        command = commands.add_parser("init"); command.add_argument("--out"); command.add_argument("--lane-id", default="new_lane"); command.set_defaults(handler=_lane_init)
        command = commands.add_parser("validate"); command.add_argument("PATH"); command.set_defaults(handler=_lane_validate)
    command = commands.add_parser("lock"); command.add_argument("PATH"); command.add_argument("--out"); command.add_argument("--check", action="store_true"); command.set_defaults(handler=_lane_lock)
    command = commands.add_parser("capture"); command.add_argument("MANIFEST"); command.add_argument("--out"); command.set_defaults(handler=_lane_capture)
    if include_internal:
        for name, handler, argument_count in (("create", _lane_create, 1), ("get", _lane_get, 1), ("list", _lane_list, 0), ("stage-report", _lane_stage_report, 1)):
            command = commands.add_parser(name)
            if argument_count:
                command.add_argument("PATH")
            command.set_defaults(handler=handler)
        for name in ("claim", "compare", "normalize", "replay", "run"):
            command = commands.add_parser(name); command.add_argument("PATH", nargs="?"); command.set_defaults(handler=_unsupported, _command=["lane", name])


def register_internal(subparsers) -> None:
    for root, names in (("claim", ("evidence", "get", "list", "reverify")), ("lane-execution", ("cancel", "get")), ("lane-lock", ("get",))):
        parser = subparsers.add_parser(root)
        _common(parser)
        commands = parser.add_subparsers(dest="command", required=True)
        for name in names:
            command = commands.add_parser(name); command.add_argument("PATH", nargs="?"); command.set_defaults(handler=_unsupported, _command=[root, name])


def register(subparsers) -> None:
    register_lane(subparsers, include_internal=True)
    register_internal(subparsers)
