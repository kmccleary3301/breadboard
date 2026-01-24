#!/usr/bin/env python3
"""
Run parity checks across replayed OpenCode sessions and live-task goldens.
"""

from __future__ import annotations

import argparse
import ast
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
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from agentic_coder_prototype.parity import (
    build_expected_run_ir,
    build_run_ir_from_run_dir,
    compare_run_ir,
)
from agentic_coder_prototype.parity_manifest import load_parity_scenarios
from safe_delete import safe_rmtree

REPLAY_SCRIPT = ROOT_DIR / "scripts" / "replay_opencode_session.py"
MAIN_ENTRY = ROOT_DIR / "main.py"
CLI_DIR = ROOT_DIR / "tui_skeleton"
CLI_ENTRY = CLI_DIR / "dist" / "main.js"
_CLI_BUNDLE_BUILT = False


def _workspace_root() -> tuple[Path, tempfile.TemporaryDirectory | None]:
    base = os.environ.get("REPLAY_WORKSPACE_BASE")
    if base:
        path = Path(base).expanduser().resolve()
        if path == ROOT_DIR or path in ROOT_DIR.parents:
            if os.environ.get("BREADBOARD_ALLOW_REPLAY_WORKSPACE_IN_REPO") != "1":
                raise RuntimeError(
                    f"[safety] Refusing REPLAY_WORKSPACE_BASE='{path}' (repo root/ancestor). "
                    "Set BREADBOARD_ALLOW_REPLAY_WORKSPACE_IN_REPO=1 to override."
                )
        if path.exists() and not path.is_dir():
            raise RuntimeError(f"[safety] REPLAY_WORKSPACE_BASE is not a directory: {path}")
        path.mkdir(parents=True, exist_ok=True)
        return path, None
    tmp_dir = tempfile.TemporaryDirectory(prefix="kc_parity.")
    return Path(tmp_dir.name), tmp_dir


def _resolve_existing(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except FileNotFoundError:
        return path.expanduser().absolute()


def _is_within(base: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(base)
        return True
    except ValueError:
        return False


def _assert_safe_workspace(path: Path, *, label: str) -> Path:
    resolved = _resolve_existing(path)
    repo_root = _resolve_existing(ROOT_DIR)
    home = _resolve_existing(Path.home())
    tmp_root = _resolve_existing(Path(tempfile.gettempdir()))

    if resolved == Path("/"):
        raise RuntimeError(f"[safety] Refusing to use {label}: '{resolved}'")
    if resolved == repo_root:
        raise RuntimeError(f"[safety] Refusing to use {label}: '{resolved}' (repo root)")
    if resolved in repo_root.parents:
        raise RuntimeError(
            f"[safety] Refusing to use {label}: '{resolved}' (ancestor of repo root '{repo_root}')"
        )
    if resolved == home:
        raise RuntimeError(f"[safety] Refusing to use {label}: '{resolved}' (home dir)")
    if resolved == tmp_root:
        raise RuntimeError(f"[safety] Refusing to use {label}: '{resolved}' (tmp dir root)")
    if (resolved / ".git").exists():
        raise RuntimeError(f"[safety] Refusing to use {label}: '{resolved}' (contains .git)")

    if not (_is_within(repo_root, resolved) or _is_within(tmp_root, resolved)):
        if os.environ.get("BREADBOARD_ALLOW_UNSAFE_RMTREE") != "1":
            raise RuntimeError(
                f"[safety] Refusing to use {label}: '{resolved}' (outside repo/tmp). "
                "Set BREADBOARD_ALLOW_UNSAFE_RMTREE=1 to override."
            )

    if resolved.exists() and not resolved.is_dir():
        raise RuntimeError(f"[safety] Refusing to use {label}: '{resolved}' (not a directory)")

    return resolved


def _resolve_workspace(base: Path, name: str) -> Path:
    candidate = _resolve_existing(base / name)
    base_resolved = _resolve_existing(base)
    if not _is_within(base_resolved, candidate):
        raise RuntimeError(
            f"[safety] Refusing workspace '{candidate}' outside base '{base_resolved}'"
        )
    return _assert_safe_workspace(candidate, label=f"workspace '{candidate}'")


def _safe_reset_dir(path: Path, *, label: str) -> None:
    resolved = _assert_safe_workspace(path, label=label)
    if resolved.exists():
        safe_rmtree(resolved, repo_root=ROOT_DIR, label=label)
    resolved.mkdir(parents=True, exist_ok=True)


def _prune_seeded_workspace(workspace: Path) -> None:
    """Remove stateful directories from seeded workspaces to keep runs deterministic."""
    for name in [".breadboard"]:
        target = workspace / name
        if not target.exists():
            continue
        if target.is_dir():
            safe_rmtree(target, repo_root=ROOT_DIR, label=f"seeded workspace '{target}'", ignore_errors=True)
        else:
            target.unlink()


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
        "--strict",
        action="store_true",
        help="Fail required gate-tier scenarios if fixtures/configs are missing.",
    )
    parser.add_argument(
        "--strict-gate",
        type=int,
        default=None,
        help="Gate tier threshold for strict enforcement (default: env PARITY_STRICT_GATE or 1).",
    )
    parser.add_argument(
        "--strict-live",
        action="store_true",
        help="When strict, fail gate-tier task scenarios if live runs are disabled.",
    )
    parser.add_argument(
        "--manifest",
        help="Override path to misc/opencode_runs/parity_scenarios.yaml",
    )
    parser.add_argument(
        "--fixture-root",
        help="Root directory for parity fixtures (overrides BREADBOARD_FIXTURE_ROOT).",
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


def _tier_rank(value: str) -> int:
    if not value:
        return 99
    normalized = value.strip().upper()
    if normalized.startswith("E") and normalized[1:].isdigit():
        return int(normalized[1:])
    return 99


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


def _load_optional_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _extract_latest_snapshot(summary: Dict[str, Any], key: str) -> Dict[str, Any]:
    surface = summary.get("surface_snapshot") or {}
    entries = surface.get(key) or []
    if isinstance(entries, list) and entries:
        for entry in reversed(entries):
            if isinstance(entry, dict):
                return entry
    return {}


def _compute_surface_hash_deltas(actual_summary: Path, expected_summary: Path) -> Dict[str, Any]:
    actual = _load_optional_json(actual_summary)
    expected = _load_optional_json(expected_summary)
    actual_schema = _extract_latest_snapshot(actual, "tool_schema_snapshots")
    expected_schema = _extract_latest_snapshot(expected, "tool_schema_snapshots")
    actual_allow = _extract_latest_snapshot(actual, "tool_allowlist_snapshots")
    expected_allow = _extract_latest_snapshot(expected, "tool_allowlist_snapshots")

    def _delta(actual_entry: Dict[str, Any], expected_entry: Dict[str, Any], field: str) -> Dict[str, Any]:
        return {
            "actual": actual_entry.get(field),
            "expected": expected_entry.get(field),
            "match": actual_entry.get(field) == expected_entry.get(field),
        }

    return {
        "tool_schema": _delta(actual_schema, expected_schema, "schema_hash"),
        "tool_schema_ordered": _delta(actual_schema, expected_schema, "schema_hash_ordered"),
        "tool_allowlist": _delta(actual_allow, expected_allow, "allowlist_hash"),
        "tool_allowlist_ordered": _delta(actual_allow, expected_allow, "allowlist_hash_ordered"),
    }


def _extract_tool_order_diffs(mismatches: Any) -> List[Dict[str, Any]]:
    diffs: List[Dict[str, Any]] = []
    if not isinstance(mismatches, list):
        return diffs
    for entry in mismatches:
        if not isinstance(entry, str):
            continue
        prefix = "Turn tool order mismatch at turn "
        if not entry.startswith(prefix):
            continue
        try:
            rest = entry[len(prefix) :]
            turn_part, rest = rest.split(": actual=", 1)
            actual_part, expected_part = rest.split(" expected=", 1)
        except ValueError:
            continue
        turn = turn_part.strip()
        actual = actual_part.strip()
        expected = expected_part.strip()
        try:
            actual_parsed = ast.literal_eval(actual)
        except Exception:
            actual_parsed = actual
        try:
            expected_parsed = ast.literal_eval(expected)
        except Exception:
            expected_parsed = expected
        diffs.append(
            {
                "turn": turn,
                "actual": actual_parsed,
                "expected": expected_parsed,
            }
        )
    return diffs


def _run_task_scenario(scenario, *, workspace: Path, result_dir: Path) -> Dict[str, Any]:
    logging_root = scenario.logging_root or (ROOT_DIR / "logging")
    before = _collect_logging_dirs(logging_root)
    _safe_reset_dir(workspace, label=f"scenario workspace '{workspace}'")
    # Seed workspaces with the golden contents to stabilize manifest comparisons.
    golden_ws = Path(scenario.golden_workspace) if scenario.golden_workspace else None
    if golden_ws and golden_ws.exists():
        shutil.copytree(golden_ws, workspace, dirs_exist_ok=True)
        _prune_seeded_workspace(workspace)
        _prune_seeded_workspace(workspace)
        try:
            cfg = yaml.safe_load(Path(scenario.config).read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[parity] WARNING: failed to read config {scenario.config}: {exc}", file=sys.stderr)
            cfg = None
        if isinstance(cfg, dict):
            cfg_ws = Path(cfg.get("workspace", {}).get("root", "./agent_ws_claude"))
            if not cfg_ws.is_absolute():
                cfg_ws = (ROOT_DIR / cfg_ws).resolve()
            _safe_reset_dir(cfg_ws, label=f"config workspace '{cfg_ws}'")
            shutil.copytree(golden_ws, cfg_ws, dirs_exist_ok=True)
            _prune_seeded_workspace(cfg_ws)
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
    _safe_reset_dir(workspace, label=f"scenario workspace '{workspace}'")
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
    _safe_reset_dir(workspace, label=f"scenario workspace '{workspace}'")
    # Seed workspace with golden files if provided so manifest comparisons align.
    golden_ws = Path(scenario.golden_workspace) if scenario.golden_workspace else None
    if golden_ws and golden_ws.exists():
        shutil.copytree(golden_ws, workspace, dirs_exist_ok=True)
        _prune_seeded_workspace(workspace)
        # Also seed the configured workspace root if the agent ignores --workspace.
        try:
            cfg = yaml.safe_load(Path(scenario.config).read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[parity] WARNING: failed to read config {scenario.config}: {exc}", file=sys.stderr)
            cfg = None
        if isinstance(cfg, dict):
            cfg_ws = Path(cfg.get("workspace", {}).get("root", "./agent_ws_claude"))
            if not cfg_ws.is_absolute():
                cfg_ws = (ROOT_DIR / cfg_ws).resolve()
            _safe_reset_dir(cfg_ws, label=f"config workspace '{cfg_ws}'")
            shutil.copytree(golden_ws, cfg_ws, dirs_exist_ok=True)
            _prune_seeded_workspace(cfg_ws)
        # Also seed the configured workspace root if the agent ignores --workspace.
        try:
            import yaml  # noqa: PLC0415

            cfg = yaml.safe_load(Path(scenario.config).read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[parity] WARNING: failed to read config {scenario.config}: {exc}", file=sys.stderr)
            cfg = None
        if isinstance(cfg, dict):
            cfg_ws = Path(cfg.get("workspace", {}).get("root", "./agent_ws_claude"))
            if not cfg_ws.is_absolute():
                cfg_ws = (ROOT_DIR / cfg_ws).resolve()
            _safe_reset_dir(cfg_ws, label=f"config workspace '{cfg_ws}'")
            shutil.copytree(golden_ws, cfg_ws, dirs_exist_ok=True)
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
                tool_order_diffs = _extract_tool_order_diffs(parity_payload.get("mismatches"))
                if tool_order_diffs:
                    payload["tool_order_diffs"] = tool_order_diffs
            # Surface hash deltas (tool schema + allowlist) for summary reporting.
            try:
                actual_run_dir = data.get("result", {}).get("run_dir")
                if isinstance(actual_run_dir, str) and scenario.golden_meta:
                    actual_summary = Path(actual_run_dir) / "meta" / "run_summary.json"
                    expected_summary = Path(scenario.golden_meta)
                    if actual_summary.exists() and expected_summary.exists():
                        payload["surface_hash_deltas"] = _compute_surface_hash_deltas(
                            actual_summary, expected_summary
                        )
            except Exception:
                pass
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


def _write_summary(result_dir: Path, records: List[Dict[str, Any]], *, manifest_path: Optional[Path] = None) -> None:
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
    fixture_root = os.environ.get("BREADBOARD_FIXTURE_ROOT") or os.environ.get("BREADBOARD_PARITY_FIXTURE_ROOT")
    if manifest_path:
        summary_payload["manifest_path"] = str(manifest_path)
    if fixture_root:
        summary_payload["fixture_root"] = fixture_root
    # Surface hash deltas (short summary for tool-schema + allowlist hashes).
    deltas = []
    for record in records:
        delta = record.get("surface_hash_deltas")
        if delta:
            deltas.append(
                {
                    "scenario": record.get("scenario"),
                    "tool_schema": delta.get("tool_schema"),
                    "tool_schema_ordered": delta.get("tool_schema_ordered"),
                    "tool_allowlist": delta.get("tool_allowlist"),
                    "tool_allowlist_ordered": delta.get("tool_allowlist_ordered"),
                }
            )
    if deltas:
        summary_payload["surface_hash_deltas"] = deltas
    tool_order_diffs = []
    for record in records:
        diffs = record.get("tool_order_diffs")
        if diffs:
            tool_order_diffs.append(
                {
                    "scenario": record.get("scenario"),
                    "diffs": diffs,
                }
            )
    if tool_order_diffs:
        summary_payload["tool_order_diffs"] = tool_order_diffs
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
    if args.fixture_root:
        os.environ["BREADBOARD_FIXTURE_ROOT"] = args.fixture_root
    manifest_path = Path(args.manifest).resolve() if args.manifest else None
    all_scenarios = load_parity_scenarios(manifest_path)
    if not all_scenarios:
        print("[parity] No scenarios defined; exiting.")
        return
    name_filter = {name.strip().lower() for name in (args.scenarios or []) if name and name.strip()}
    tag_filter = {tag.strip().lower() for tag in (args.tags or []) if tag and tag.strip()}
    strict = bool(args.strict or os.environ.get("PARITY_STRICT", "").lower() in {"1", "true", "yes"})
    strict_gate = args.strict_gate
    if strict_gate is None:
        strict_gate = int(os.environ.get("PARITY_STRICT_GATE", "1") or "1")
    strict_live = bool(args.strict_live or os.environ.get("PARITY_STRICT_LIVE", "").lower() in {"1", "true", "yes"})
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
            workspace = _resolve_workspace(work_root, scenario.name)
            print(f"[parity] Running {scenario.name} ({scenario.mode})")
            if scenario.mode not in {"task", "replay", "cli_guard"}:
                print(f"[parity] Unsupported mode '{scenario.mode}' for {scenario.name}; marking pending.")
                records.append(_augment_record(scenario, {"status": "pending"}, status_override="pending"))
                continue
            gate_rank = _tier_rank(scenario.gate_tier)
            if scenario.config and not scenario.config.exists():
                reason = "missing_config"
                message = f"[parity] Skipping {scenario.name}: missing config at {scenario.config}"
                if strict and gate_rank <= strict_gate:
                    raise RuntimeError(f"{message} (strict gate)")
                print(message)
                records.append(_augment_record(scenario, {"status": "skipped", "reason": reason}))
                continue
            if scenario.mode == "replay" and scenario.session and not scenario.session.exists():
                reason = "missing_session"
                message = f"[parity] Skipping {scenario.name}: missing replay session at {scenario.session}"
                if strict and gate_rank <= strict_gate:
                    raise RuntimeError(f"{message} (strict gate)")
                print(message)
                records.append(_augment_record(scenario, {"status": "skipped", "reason": reason}))
                continue
            if scenario.mode == "task" and os.environ.get("PARITY_RUN_LIVE", "").lower() not in {"1", "true", "yes"}:
                reason = "live_disabled"
                message = f"[parity] Skipping {scenario.name}: PARITY_RUN_LIVE not enabled."
                if strict and strict_live and gate_rank <= strict_gate:
                    raise RuntimeError(f"{message} (strict live gate)")
                print(message)
                records.append(_augment_record(scenario, {"status": "skipped", "reason": reason}))
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
        _write_summary(result_dir, records, manifest_path=manifest_path)
        _publish_parity_status(parity_root, result_dir, records)
    print("[parity] Completed all scenarios")


if __name__ == "__main__":
    main()
