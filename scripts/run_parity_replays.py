#!/usr/bin/env python3
"""
Run parity checks across replayed OpenCode sessions and live-task goldens.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import yaml
import tempfile
import time
import socket
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agentic_coder_prototype.parity import (
    build_expected_run_ir,
    build_run_ir_from_run_dir,
    compare_run_ir,
)
from agentic_coder_prototype.parity_manifest import load_parity_scenarios

REPLAY_SCRIPT = ROOT_DIR / "scripts" / "replay_opencode_session.py"
MAIN_ENTRY = ROOT_DIR / "main.py"
CLI_DIR = ROOT_DIR / "tui_skeleton"
CLI_ENTRY = CLI_DIR / "dist" / "main.js"
_CLI_BUNDLE_BUILT = False


def _workspace_root() -> tuple[Path, tempfile.TemporaryDirectory | None]:
    base = os.environ.get("REPLAY_WORKSPACE_BASE")
    if base:
        path = Path(base).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path, None
    tmp_dir = tempfile.TemporaryDirectory(prefix="kc_parity.")
    return Path(tmp_dir.name), tmp_dir


def _prune_seeded_workspace(workspace: Path) -> None:
    """Remove stateful directories from seeded workspaces to keep runs deterministic."""
    for name in [".kyle"]:
        target = workspace / name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


def _ensure_cli_bundle() -> None:
    global _CLI_BUNDLE_BUILT
    if _CLI_BUNDLE_BUILT:
        return
    if not CLI_DIR.exists():
        raise RuntimeError(f"[parity] Missing CLI workspace at {CLI_DIR}")
    subprocess.run(["npm", "run", "build"], cwd=CLI_DIR, check=True)
    _CLI_BUNDLE_BUILT = True


def _pick_free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def _wait_for_port(host: str, port: int, *, timeout_s: float = 10.0) -> None:
    deadline = time.time() + timeout_s
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return
        except OSError as exc:
            last_err = exc
            time.sleep(0.05)
    raise RuntimeError(f"[parity] CLI bridge did not start on {host}:{port}: {last_err}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run parity scenarios against goldens.")
    parser.add_argument(
        "--manifest",
        help="Override path to misc/opencode_runs/parity_scenarios.yaml",
    )
    parser.add_argument(
        "--scenario",
        dest="scenarios",
        action="append",
        help="Filter to specific scenario name (repeatable).",
    )
    parser.add_argument(
        "--tag",
        dest="tags",
        action="append",
        help="Filter scenarios by tag (repeatable).",
    )
    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Include scenarios marked disabled in the manifest.",
    )
    parser.add_argument(
        "--list",
        dest="list_only",
        action="store_true",
        help="List the selected scenarios and exit.",
    )
    parser.add_argument(
        "--parity-run-id",
        help="Override the timestamped run id used under artifacts/parity_runs/.",
    )
    return parser.parse_args()


def _collect_logging_dirs(root: Path) -> Set[str]:
    if not root.exists():
        return set()
    return {child.name for child in root.iterdir() if child.is_dir()}


def _latest_logging_dir(root: Path, before: Set[str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    after = _collect_logging_dirs(root)
    new_entries = sorted(after - before)
    if new_entries:
        return root / new_entries[-1]
    candidates = [p for p in root.iterdir() if p.is_dir()]
    if not candidates:
        raise RuntimeError(f"No logging directories detected under {root}")
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _run_task_scenario(scenario, *, workspace: Path, result_dir: Path) -> Dict[str, Any]:
    logging_root = scenario.logging_root or (ROOT_DIR / "logging")
    before = _collect_logging_dirs(logging_root)
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    # Seed workspaces with the golden contents to stabilize manifest comparisons.
    golden_ws = Path(scenario.golden_workspace) if scenario.golden_workspace else None
    if golden_ws and golden_ws.exists():
        shutil.copytree(golden_ws, workspace, dirs_exist_ok=True)
        _prune_seeded_workspace(workspace)
        _prune_seeded_workspace(workspace)
        try:
            cfg = yaml.safe_load(Path(scenario.config).read_text(encoding="utf-8"))
            cfg_ws = Path(cfg.get("workspace", {}).get("root", "./agent_ws_claude"))
            if not cfg_ws.is_absolute():
                cfg_ws = (ROOT_DIR / cfg_ws).resolve()
            if cfg_ws.exists():
                shutil.rmtree(cfg_ws)
            cfg_ws.mkdir(parents=True, exist_ok=True)
            shutil.copytree(golden_ws, cfg_ws, dirs_exist_ok=True)
            _prune_seeded_workspace(cfg_ws)
        except Exception:
            pass
    cmd = [
        sys.executable,
        str(MAIN_ENTRY),
        str(scenario.config),
        "--workspace",
        str(workspace),
        "--task",
        str(scenario.task),
    ]
    if scenario.max_steps:
        cmd += ["--max-iterations", str(scenario.max_steps)]
    env = os.environ.copy()
    env.setdefault("RAY_SCE_SKIP_LSP", "1")
    env.setdefault("MOCK_API_KEY", "kc_parity_mock_key")
    env.setdefault("PRESERVE_SEEDED_WORKSPACE", "1")
    subprocess.run(cmd, cwd=ROOT_DIR, env=env, check=True)
    run_dir = _latest_logging_dir(logging_root, before)
    actual_ir = build_run_ir_from_run_dir(run_dir)
    expected_ir = build_expected_run_ir(
        Path(scenario.golden_workspace),
        summary_path=scenario.golden_meta,
    )
    mismatches = compare_run_ir(actual_ir, expected_ir, scenario.equivalence)
    status = "passed" if not mismatches else "failed"
    payload: Dict[str, object] = {
        "status": status,
        "run_dir": str(run_dir),
        "scenario": scenario.name,
        "golden_workspace": str(scenario.golden_workspace),
        "equivalence": scenario.equivalence.value,
        "mismatches": mismatches,
    }
    # Record multi-agent event logs if configured
    try:
        cfg = yaml.safe_load(Path(scenario.config).read_text(encoding="utf-8"))
        multi_cfg = (cfg.get("multi_agent") or {}) if isinstance(cfg, dict) else {}
        event_log_path = multi_cfg.get("event_log_path")
        if isinstance(event_log_path, str) and event_log_path:
            event_path = Path(event_log_path)
            if not event_path.is_absolute():
                event_path = (ROOT_DIR / event_path).resolve()
            if event_path.exists():
                dest = result_dir / f"{scenario.name}_event_log.jsonl"
                shutil.copyfile(event_path, dest)
                payload["event_log"] = str(dest)
    except Exception:
        pass
    output_path = result_dir / f"{scenario.name}_result.json"
    payload["result_path"] = str(output_path)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if mismatches and scenario.fail_mode == "fail":
        raise RuntimeError(f"[parity] {scenario.name} failed ({len(mismatches)} mismatches)")
    return payload


def _run_cli_guard_scenario(scenario, *, workspace: Path, result_dir: Path) -> Dict[str, Any]:
    if not scenario.script:
        raise RuntimeError(f"[parity] Scenario {scenario.name} missing script definition")
    if not scenario.golden_workspace or not scenario.golden_meta:
        raise RuntimeError(f"[parity] Scenario {scenario.name} missing golden workspace/meta")
    _ensure_cli_bundle()
    logging_root = scenario.logging_root or (ROOT_DIR / "logging")
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    golden_ws = Path(scenario.golden_workspace) if scenario.golden_workspace else None
    if golden_ws and golden_ws.exists():
        shutil.copytree(golden_ws, workspace, dirs_exist_ok=True)
    host = "127.0.0.1"
    port = _pick_free_port(host)
    base_url = f"http://{host}:{port}"
    server_log_path = result_dir / f"{scenario.name}_cli_bridge.log"
    server_env = os.environ.copy()
    server_env["BREADBOARD_CLI_HOST"] = host
    server_env["BREADBOARD_CLI_PORT"] = str(port)
    server_env.setdefault("RAY_SCE_SKIP_LSP", "1")
    server_env.setdefault("MOCK_API_KEY", "kc_parity_mock_key")
    server_env.setdefault("PRESERVE_SEEDED_WORKSPACE", "1")

    server_cmd = [
        sys.executable,
        "-m",
        "agentic_coder_prototype.api.cli_bridge.server",
    ]
    server_proc = None
    before = _collect_logging_dirs(logging_root)
    try:
        with server_log_path.open("w", encoding="utf-8") as handle:
            server_proc = subprocess.Popen(
                server_cmd,
                cwd=ROOT_DIR,
                env=server_env,
                stdout=handle,
                stderr=subprocess.STDOUT,
            )
        _wait_for_port(host, port, timeout_s=15.0)
        cmd = [
            "node",
            str(CLI_ENTRY),
            "repl",
            "--config",
            str(scenario.config),
            "--workspace",
            str(workspace),
            "--script",
            str(scenario.script),
            "--script-final-only",
        ]
        if scenario.script_output:
            cmd += ["--script-output", str(scenario.script_output)]
        env = os.environ.copy()
        env["BREADBOARD_API_URL"] = base_url
        env.setdefault("BREADBOARD_API_TIMEOUT_MS", "180000")
        env.setdefault("MOCK_API_KEY", "kc_parity_mock_key")
        subprocess.run(cmd, cwd=CLI_DIR, env=env, check=True)
    finally:
        if server_proc is not None:
            try:
                server_proc.terminate()
                server_proc.wait(timeout=5)
            except Exception:
                try:
                    server_proc.kill()
                except Exception:
                    pass
    run_dir = _latest_logging_dir(logging_root, before)
    actual_ir = build_run_ir_from_run_dir(run_dir)
    expected_ir = build_expected_run_ir(
        Path(scenario.golden_workspace),
        summary_path=scenario.golden_meta,
    )
    mismatches = compare_run_ir(actual_ir, expected_ir, scenario.equivalence)
    status = "passed" if not mismatches else "failed"
    payload: Dict[str, object] = {
        "status": status,
        "run_dir": str(run_dir),
        "scenario": scenario.name,
        "golden_workspace": str(scenario.golden_workspace),
        "equivalence": scenario.equivalence.value,
        "mismatches": mismatches,
    }
    output_path = result_dir / f"{scenario.name}_result.json"
    payload["result_path"] = str(output_path)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if mismatches and scenario.fail_mode == "fail":
        raise RuntimeError(f"[parity] {scenario.name} failed ({len(mismatches)} mismatches)")
    return payload


def _run_replay_scenario(scenario, *, workspace: Path, result_dir: Path) -> Dict[str, Any]:
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    # Seed workspace with golden files if provided so manifest comparisons align.
    golden_ws = Path(scenario.golden_workspace) if scenario.golden_workspace else None
    if golden_ws and golden_ws.exists():
        shutil.copytree(golden_ws, workspace, dirs_exist_ok=True)
        _prune_seeded_workspace(workspace)
        # Also seed the configured workspace root if the agent ignores --workspace.
        try:
            cfg = yaml.safe_load(Path(scenario.config).read_text(encoding="utf-8"))
            cfg_ws = Path(cfg.get("workspace", {}).get("root", "./agent_ws_claude"))
            if not cfg_ws.is_absolute():
                cfg_ws = (ROOT_DIR / cfg_ws).resolve()
            if cfg_ws.exists():
                shutil.rmtree(cfg_ws)
            cfg_ws.mkdir(parents=True, exist_ok=True)
            shutil.copytree(golden_ws, cfg_ws, dirs_exist_ok=True)
            _prune_seeded_workspace(cfg_ws)
        except Exception:
            pass
        # Also seed the configured workspace root if the agent ignores --workspace.
        try:
            import yaml  # noqa: PLC0415

            cfg = yaml.safe_load(Path(scenario.config).read_text(encoding="utf-8"))
            cfg_ws = Path(cfg.get("workspace", {}).get("root", "./agent_ws_claude"))
            if not cfg_ws.is_absolute():
                cfg_ws = (ROOT_DIR / cfg_ws).resolve()
            if cfg_ws.exists():
                shutil.rmtree(cfg_ws)
            cfg_ws.mkdir(parents=True, exist_ok=True)
            shutil.copytree(golden_ws, cfg_ws, dirs_exist_ok=True)
        except Exception:
            # Best effort; continue even if config parse fails.
            pass
    result_json = result_dir / f"{scenario.name}_result.json"
    cmd = [
        sys.executable,
        str(REPLAY_SCRIPT),
        "--config",
        str(scenario.config),
        "--session",
        str(scenario.session),
        "--workspace",
        str(workspace),
        "--golden-workspace",
        str(scenario.golden_workspace),
        "--result-json",
        str(result_json),
        "--parity-fail-mode",
        scenario.fail_mode,
        "--parity-level",
        scenario.equivalence.value,
    ]
    if getattr(scenario, "max_steps", None):
        cmd += ["--limit", str(scenario.max_steps)]
    if scenario.todo_expected:
        cmd += ["--todo-expected", str(scenario.todo_expected)]
    if scenario.guardrails_expected:
        cmd += ["--guardrail-expected", str(scenario.guardrails_expected)]
    if scenario.golden_meta:
        cmd += ["--parity-summary", str(scenario.golden_meta)]
    env = os.environ.copy()
    env.setdefault("RAY_SCE_SKIP_LSP", "1")
    env.setdefault("MOCK_API_KEY", "kc_parity_mock_key")
    env.setdefault("PRESERVE_SEEDED_WORKSPACE", "1")
    subprocess.run(cmd, cwd=ROOT_DIR, env=env, check=True)
    payload: Dict[str, Any] = {
        "result_path": str(result_json),
    }
    if result_json.exists():
        try:
            data = json.loads(result_json.read_text(encoding="utf-8"))
            parity_payload = data.get("parity") or {}
            payload["parity"] = parity_payload
            status = parity_payload.get("status") or data.get("status") or "unknown"
            payload["status"] = status
            if parity_payload.get("mismatches"):
                payload["mismatches"] = parity_payload["mismatches"]
        except Exception as exc:  # pragma: no cover - defensive read
            payload["status"] = "unknown"
            payload["error"] = f"unable to parse result json: {exc}"
    else:
        payload["status"] = "unknown"
        payload["error"] = "result_json_missing"
    return payload


def _augment_record(scenario, record: Dict[str, Any], *, status_override: Optional[str] = None) -> Dict[str, Any]:
    enriched = dict(record or {})
    enriched.setdefault("scenario", scenario.name)
    enriched.setdefault("mode", scenario.mode)
    enriched.setdefault("status", "unknown")
    if status_override:
        enriched["status"] = status_override
    enriched["description"] = scenario.description
    enriched["tags"] = list(scenario.tags)
    enriched["fail_mode"] = scenario.fail_mode
    enriched["equivalence"] = scenario.equivalence.value
    enriched["enabled"] = scenario.enabled
    enriched["gate_tier"] = scenario.gate_tier
    if scenario.trophy_tier:
        enriched["trophy_tier"] = scenario.trophy_tier
    if scenario.golden_workspace:
        enriched.setdefault("golden_workspace", str(scenario.golden_workspace))
    if scenario.golden_meta:
        enriched.setdefault("golden_meta", str(scenario.golden_meta))
    if scenario.task:
        enriched.setdefault("task", str(scenario.task))
    if scenario.session:
        enriched.setdefault("session", str(scenario.session))
    if scenario.script:
        enriched.setdefault("script", str(scenario.script))
    if scenario.script_output:
        enriched.setdefault("script_output", str(scenario.script_output))
    if scenario.guard_fixture:
        enriched.setdefault("guard_fixture", str(scenario.guard_fixture))
    return enriched


def _write_summary(result_dir: Path, records: List[Dict[str, Any]]) -> None:
    result_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = result_dir / "parity_results.jsonl"
    summary_path = result_dir / "parity_summary.json"
    lines: List[str] = []
    counts: Dict[str, int] = {}

    def _effective_status(record: Dict[str, Any]) -> str:
        status = str(record.get("status") or "unknown").lower()
        if status == "failed" and record.get("fail_mode") == "warn":
            return "warn"
        return status

    failed_names: List[str] = []
    warned_names: List[str] = []
    for record in records:
        status = _effective_status(record)
        counts[status] = counts.get(status, 0) + 1
        if status == "failed":
            failed_names.append(record.get("scenario", "unknown"))
        elif status == "warn":
            warned_names.append(record.get("scenario", "unknown"))
        lines.append(json.dumps(record))
    if lines:
        jsonl_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        jsonl_path.write_text("", encoding="utf-8")
    summary_payload = {
        "total": len(records),
        "status_counts": counts,
        "failed": failed_names,
        "warned": warned_names,
        "results_file": str(jsonl_path),
    }
    summary_path.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
    print(f"[parity] Wrote summary to {summary_path}")


def _publish_parity_status(parity_root: Path, run_dir: Path, records: List[Dict[str, Any]]) -> None:
    summary_path = run_dir / "parity_summary.json"
    jsonl_path = run_dir / "parity_results.jsonl"
    parity_root.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(summary_path, parity_root / "latest_summary.json")
        shutil.copy2(jsonl_path, parity_root / "latest_results.jsonl")
    except Exception:
        pass
    dashboard_dir = parity_root / "dashboard"
    dashboard_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "summary_path": str(summary_path),
        "results_path": str(jsonl_path),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "scenarios": [
            {
                "scenario": record.get("scenario"),
                "status": record.get("status"),
                "mode": record.get("mode"),
                "gate_tier": record.get("gate_tier"),
                "trophy_tier": record.get("trophy_tier"),
                "equivalence": record.get("equivalence"),
                "fail_mode": record.get("fail_mode"),
                "enabled": record.get("enabled"),
            }
            for record in records
        ],
    }
    dashboard_path = dashboard_dir / "parity_dashboard.json"
    dashboard_path.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    print(f"[parity] Updated dashboard snapshot at {dashboard_path}")


def main() -> None:
    args = _parse_args()
    manifest_path = Path(args.manifest).resolve() if args.manifest else None
    all_scenarios = load_parity_scenarios(manifest_path)
    if not all_scenarios:
        print("[parity] No scenarios defined; exiting.")
        return
    name_filter = {name.strip().lower() for name in (args.scenarios or []) if name and name.strip()}
    tag_filter = {tag.strip().lower() for tag in (args.tags or []) if tag and tag.strip()}
    scenarios: List[Any] = []
    for scenario in all_scenarios:
        if not args.include_disabled and not scenario.enabled:
            continue
        if name_filter and scenario.name.lower() not in name_filter:
            continue
        if tag_filter:
            scenario_tags = {str(tag).lower() for tag in scenario.tags}
            if not any(tag in scenario_tags for tag in tag_filter):
                continue
        scenarios.append(scenario)
    if not scenarios:
        print("[parity] No scenarios matched the provided filters.")
        return
    if args.list_only:
        print("[parity] Selected scenarios:")
        for scenario in scenarios:
            tag_text = ", ".join(scenario.tags) if scenario.tags else "-"
            enabled_flag = "enabled" if scenario.enabled else "disabled"
            print(f"  - {scenario.name} [{enabled_flag}] tags={tag_text}")
        return

    work_root, tmp_handle = _workspace_root()
    artifact_root = Path(os.environ.get("REPLAY_RESULT_DIR", ROOT_DIR / "artifacts")).resolve()
    parity_root = artifact_root / "parity_runs"
    run_id = (
        args.parity_run_id
        or os.environ.get("PARITY_RUN_ID")
        or datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    )
    result_dir = parity_root / run_id
    result_dir.mkdir(parents=True, exist_ok=True)
    records: List[Dict[str, Any]] = []

    try:
        for scenario in scenarios:
            workspace = work_root / scenario.name
            print(f"[parity] Running {scenario.name} ({scenario.mode})")
            if scenario.mode not in {"task", "replay", "cli_guard"}:
                print(f"[parity] Unsupported mode '{scenario.mode}' for {scenario.name}; marking pending.")
                records.append(_augment_record(scenario, {"status": "pending"}, status_override="pending"))
                continue
            try:
                if scenario.mode == "task":
                    record = _run_task_scenario(scenario, workspace=workspace, result_dir=result_dir)
                elif scenario.mode == "replay":
                    record = _run_replay_scenario(scenario, workspace=workspace, result_dir=result_dir)
                else:
                    record = _run_cli_guard_scenario(scenario, workspace=workspace, result_dir=result_dir)
                records.append(_augment_record(scenario, record))
                print(f"[parity] Completed {scenario.name}")
            except Exception as exc:
                error_record = _augment_record(scenario, {"status": "failed", "error": str(exc)})
                records.append(error_record)
                if scenario.fail_mode == "fail":
                    raise
                print(f"[parity] Scenario {scenario.name} failed but fail_mode=warn; continuing.")
    finally:
        if tmp_handle:
            tmp_handle.cleanup()
        _write_summary(result_dir, records)
        _publish_parity_status(parity_root, result_dir, records)
    print("[parity] Completed all scenarios")


if __name__ == "__main__":
    main()
