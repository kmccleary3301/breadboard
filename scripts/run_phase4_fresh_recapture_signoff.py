#!/usr/bin/env python3
"""
Run a fresh-capture phase4 signoff sweep (new capture runs per iteration).

This is the "true recapture" counterpart to frozen latest-run validators:
- hard_gate scenario set: N iterations (default 5)
- nightly scenario set: M iterations (default 3)

Each iteration replays every scenario in the set through tmux targets, records
new run IDs, runs strict text-contract validation, and emits a consolidated
JSON/Markdown signoff report.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tmux_capture_render_profile import DEFAULT_RENDER_PROFILE_ID


REPO_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_ROOT = REPO_ROOT / "docs_tmp" / "tmux_captures" / "scenarios"
CONTRACT_FILE = REPO_ROOT / "config" / "text_contracts" / "phase4_text_contract_v1.json"
SUITE_SCRIPT = REPO_ROOT / "scripts" / "validate_phase4_text_contract_suite.py"
START_TARGET_SCRIPT = REPO_ROOT / "scripts" / "start_tmux_phase4_replay_target.sh"
RUN_SCENARIO_SCRIPT = REPO_ROOT / "scripts" / "run_tmux_capture_scenario.py"


TMUX_SOCKET = "bbcap_phase4_fresh_signoff"
PROTECTED = "bb_tui_codex_dev,bb_engine_codex_dev,bb_atp"
RETRY_SCENARIOS = {"phase4_replay/subagents_concurrency_20_v1"}


@dataclass(frozen=True)
class TargetConfig:
    session: str
    port: int
    alt_buffer_viewer: bool = False


TARGETS: dict[str, TargetConfig] = {
    "streaming": TargetConfig("breadboard_test_ci_phase4_fresh_streaming", 9211),
    "todo": TargetConfig("breadboard_test_ci_phase4_fresh_todo", 9212, alt_buffer_viewer=True),
    "todo_alt": TargetConfig("breadboard_test_ci_phase4_fresh_todo_alt", 9216, alt_buffer_viewer=True),
    "subagents": TargetConfig("breadboard_test_ci_phase4_fresh_subagents", 9213),
    "everything": TargetConfig("breadboard_test_ci_phase4_fresh_everything", 9214),
    "concurrency": TargetConfig("breadboard_test_ci_phase4_fresh_concurrency", 9215),
}


HARD_PLAN: list[dict[str, str]] = [
    {
        "scenario": "phase4_replay/streaming_v1_fullpane_v8",
        "action": "config/tmux_scenario_actions/ci/phase4_replay/streaming_v1.json",
        "target": "streaming",
    },
    {
        "scenario": "phase4_replay/todo_preview_v1_fullpane_v7",
        "action": "config/tmux_scenario_actions/ci/phase4_replay/todo_preview_v1.json",
        "target": "todo",
    },
    {
        "scenario": "phase4_replay/subagents_v1_fullpane_v7",
        "action": "config/tmux_scenario_actions/ci/phase4_replay/subagents_v1.json",
        "target": "subagents",
    },
    {
        "scenario": "phase4_replay/everything_showcase_v1_fullpane_v1",
        "action": "config/tmux_scenario_actions/ci/phase4_replay/everything_showcase_v1.json",
        "target": "everything",
    },
]


NIGHTLY_PLAN: list[dict[str, str]] = [
    {
        "scenario": "phase4_replay/subagents_strip_churn_v1",
        "action": "config/tmux_scenario_actions/ci/phase4_replay/subagents_strip_churn_v1.json",
        "target": "subagents",
    },
    {
        "scenario": "phase4_replay/resize_overlay_interaction_v1",
        "action": "config/tmux_scenario_actions/ci/phase4_replay/resize_overlay_interaction_v1.json",
        "target": "everything",
    },
    {
        "scenario": "phase4_replay/thinking_lifecycle_expiration_v1",
        "action": "config/tmux_scenario_actions/ci/phase4_replay/thinking_lifecycle_expiration_v1.json",
        "target": "streaming",
    },
    {
        "scenario": "phase4_replay/alt_buffer_enter_exit_v1",
        "action": "config/tmux_scenario_actions/ci/phase4_replay/alt_buffer_enter_exit_v1.json",
        "target": "todo_alt",
    },
    {
        "scenario": "phase4_replay/thinking_preview_v1",
        "action": "config/tmux_scenario_actions/ci/phase4_replay/thinking_preview_v1.json",
        "target": "streaming",
    },
    {
        "scenario": "phase4_replay/thinking_reasoning_only_v1",
        "action": "config/tmux_scenario_actions/ci/phase4_replay/thinking_reasoning_only_v1.json",
        "target": "streaming",
    },
    {
        "scenario": "phase4_replay/thinking_tool_interleaved_v1",
        "action": "config/tmux_scenario_actions/ci/phase4_replay/thinking_tool_interleaved_v1.json",
        "target": "streaming",
    },
    {
        "scenario": "phase4_replay/thinking_multiturn_lifecycle_v1",
        "action": "config/tmux_scenario_actions/ci/phase4_replay/thinking_multiturn_lifecycle_v1.json",
        "target": "streaming",
    },
    {
        "scenario": "phase4_replay/large_output_artifact_v1",
        "action": "config/tmux_scenario_actions/ci/phase4_replay/large_output_artifact_v1.json",
        "target": "everything",
    },
    {
        "scenario": "phase4_replay/large_diff_artifact_v1",
        "action": "config/tmux_scenario_actions/ci/phase4_replay/large_diff_artifact_v1.json",
        "target": "everything",
    },
    {
        "scenario": "phase4_replay/subagents_concurrency_20_v1",
        "action": "config/tmux_scenario_actions/nightly_stress/subagents_concurrency_20_v1.json",
        "target": "concurrency",
    },
]


def _run(cmd: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = None
    if env is not None:
        merged_env = os.environ.copy()
        merged_env.update(env)
    return subprocess.run(cmd, cwd=str(REPO_ROOT), env=merged_env, capture_output=True, text=True, check=False)


def _assert_ok(proc: subprocess.CompletedProcess[str], label: str) -> None:
    if proc.returncode == 0:
        return
    stdout = (proc.stdout or "").strip()
    stderr = (proc.stderr or "").strip()
    raise RuntimeError(
        f"{label} failed (rc={proc.returncode})\n"
        f"stdout:\n{stdout}\n\nstderr:\n{stderr}"
    )


def _load_contract_scenarios(set_name: str) -> list[str]:
    payload = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))
    rows = payload.get("scenario_sets", {}).get(set_name, [])
    if not isinstance(rows, list):
        raise ValueError(f"scenario set missing in contract: {set_name}")
    return [str(x) for x in rows if str(x).strip()]


def _latest_run_dir_for_scenario(scenario: str) -> Path | None:
    scenario_key = scenario.replace("phase4_replay/", "", 1)
    base = SCENARIO_ROOT / "phase4_replay" / scenario_key
    if not base.exists():
        return None
    manifests = sorted(base.rglob("scenario_manifest.json"))
    if not manifests:
        return None
    return manifests[-1].parent


def _start_targets() -> None:
    for name, cfg in TARGETS.items():
        env = None
        if cfg.alt_buffer_viewer:
            env = {"BREADBOARD_TUI_ALT_BUFFER_VIEWER": "1"}
        started = False
        last_proc: subprocess.CompletedProcess[str] | None = None
        for attempt in range(0, 25):
            port = int(cfg.port) + attempt
            cmd = [
                "bash",
                str(START_TARGET_SCRIPT),
                "--session",
                cfg.session,
                "--tmux-socket",
                TMUX_SOCKET,
                "--cols",
                "160",
                "--rows",
                "45",
                "--port",
                str(port),
                "--scrollback-mode",
                "scrollback",
                "--landing-always",
                "1",
                "--use-dist",
            ]
            proc = _run(cmd, env=env)
            if proc.returncode == 0:
                started = True
                break
            last_proc = proc
            stderr = (proc.stderr or "").lower()
            if "already in use" not in stderr:
                break
        if not started:
            if last_proc is None:
                raise RuntimeError(f"start target {name} failed before invocation")
            _assert_ok(last_proc, f"start target {name}")


def _stop_targets() -> None:
    subprocess.run(["tmux", "-L", TMUX_SOCKET, "kill-server"], cwd=str(REPO_ROOT), check=False, capture_output=True)


def _run_scenario(plan_row: dict[str, str], *, iteration: int, lane: str, duration: int) -> Path:
    target_key = plan_row["target"]
    cfg = TARGETS[target_key]
    scenario = plan_row["scenario"]
    action = REPO_ROOT / plan_row["action"]
    label = f"fresh_{lane}_iter{iteration}_{scenario.split('/')[-1]}"
    cmd = [
        "python",
        str(RUN_SCENARIO_SCRIPT),
        "--target",
        f"{cfg.session}:0.0",
        "--tmux-socket",
        TMUX_SOCKET,
        "--scenario",
        scenario,
        "--actions",
        str(action),
        "--out-root",
        str(SCENARIO_ROOT),
        "--duration",
        str(duration),
        "--interval",
        "0.4",
        "--capture-mode",
        "fullpane",
        "--tail-lines",
        "120",
        "--final-tail-lines",
        "0",
        "--fullpane-start-markers",
        "BreadBoard v,No conversation yet",
        "--fullpane-max-lines",
        "0",
        "--fullpane-render-max-rows",
        "0",
        "--render-profile",
        DEFAULT_RENDER_PROFILE_ID,
        "--settle-ms",
        "140",
        "--settle-attempts",
        "5",
        "--session-prefix-guard",
        "breadboard_test_",
        "--protected-sessions",
        PROTECTED,
        "--capture-label",
        label,
    ]
    attempts = 3 if scenario in RETRY_SCENARIOS else 1
    proc: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, attempts + 1):
        proc = _run(cmd)
        if proc.returncode == 0:
            break
        if attempt < attempts:
            time.sleep(0.8)
            continue
    if proc is None:
        raise RuntimeError(f"run scenario {scenario} (iter={iteration}, lane={lane}) did not execute")
    _assert_ok(proc, f"run scenario {scenario} (iter={iteration}, lane={lane})")
    run_dir = _latest_run_dir_for_scenario(scenario)
    if run_dir is None:
        raise RuntimeError(f"no run dir discovered after scenario: {scenario}")
    return run_dir


def _run_suite(*, set_name: str, out_json: Path) -> subprocess.CompletedProcess[str]:
    cmd = [
        "python",
        str(SUITE_SCRIPT),
        "--run-root",
        str(SCENARIO_ROOT / "phase4_replay"),
        "--scenario-set",
        set_name,
        "--strict",
        "--fail-on-unmapped",
        "--output-json",
        str(out_json),
    ]
    return _run(cmd)


def _render_md(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Phase4 Fresh Recapture Signoff (OpenTUI P1)")
    lines.append("")
    lines.append(f"- Generated at (UTC): `{report['generated_at_utc']}`")
    lines.append(f"- Overall pass: `{report['overall_pass']}`")
    lines.append(f"- tmux socket: `{report['tmux_socket']}`")
    lines.append(f"- Render profile: `{report['render_profile']}`")
    lines.append(f"- Contract file: `{report['contract_file']}`")
    lines.append("")
    for lane in ("hard_gate", "nightly"):
        lane_payload = report["lanes"][lane]
        lines.append(f"## {lane}")
        lines.append("")
        lines.append(f"- iterations requested: `{lane_payload['iterations_requested']}`")
        lines.append(f"- iterations completed: `{lane_payload['iterations_completed']}`")
        lines.append("")
        lines.append("| Iter | Suite RC | Suite Summary | Suite Report |")
        lines.append("|---|---:|---|---|")
        for row in lane_payload["iterations"]:
            summary = str(row.get("suite_stdout") or "").replace("|", "\\|")
            lines.append(
                f"| {row['iteration']} | {row['suite_rc']} | {summary} | `{row['suite_report_json']}` |"
            )
        lines.append("")
        lines.append("### Frozen run IDs by iteration")
        lines.append("")
        lines.append("| Iter | Scenario | Run ID | Run Dir |")
        lines.append("|---|---|---|---|")
        for row in lane_payload["iterations"]:
            for srow in row["scenario_runs"]:
                lines.append(
                    f"| {row['iteration']} | `{srow['scenario']}` | `{srow['run_id']}` | `{srow['run_dir']}` |"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run fresh phase4 recapture signoff")
    p.add_argument("--hard-iters", type=int, default=5)
    p.add_argument("--nightly-iters", type=int, default=3)
    p.add_argument("--duration-seconds", type=int, default=45)
    p.add_argument(
        "--out-json",
        default=str(REPO_ROOT / "docs_tmp" / "cli_phase_5" / "phase4_text_contract_fresh_recapture_signoff_v1.json"),
    )
    p.add_argument(
        "--out-md",
        default=str(REPO_ROOT / "docs_tmp" / "cli_phase_5" / "phase4_text_contract_fresh_recapture_signoff_v1.md"),
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    out_json = Path(args.out_json).expanduser().resolve()
    out_md = Path(args.out_md).expanduser().resolve()

    hard_contract = set(_load_contract_scenarios("hard_gate"))
    nightly_contract = set(_load_contract_scenarios("nightly"))
    hard_plan_scenarios = {row["scenario"] for row in HARD_PLAN}
    nightly_plan_scenarios = {row["scenario"] for row in NIGHTLY_PLAN}
    if hard_contract != hard_plan_scenarios:
        raise SystemExit(
            f"hard_gate plan mismatch.\ncontract={sorted(hard_contract)}\nplan={sorted(hard_plan_scenarios)}"
        )
    if nightly_contract != nightly_plan_scenarios:
        raise SystemExit(
            f"nightly plan mismatch.\ncontract={sorted(nightly_contract)}\nplan={sorted(nightly_plan_scenarios)}"
        )

    report: dict[str, Any] = {
        "schema_version": "phase4_text_contract_fresh_recapture_signoff_v1",
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "tmux_socket": TMUX_SOCKET,
        "render_profile": DEFAULT_RENDER_PROFILE_ID,
        "contract_file": str(CONTRACT_FILE),
        "overall_pass": True,
        "lanes": {
            "hard_gate": {
                "iterations_requested": int(args.hard_iters),
                "iterations_completed": 0,
                "iterations": [],
            },
            "nightly": {
                "iterations_requested": int(args.nightly_iters),
                "iterations_completed": 0,
                "iterations": [],
            },
        },
    }

    _start_targets()
    try:
        time.sleep(1.2)
        for lane_name, iters, plan_rows in (
            ("hard_gate", int(args.hard_iters), HARD_PLAN),
            ("nightly", int(args.nightly_iters), NIGHTLY_PLAN),
        ):
            for iteration in range(1, iters + 1):
                scenario_rows: list[dict[str, str]] = []
                for row in plan_rows:
                    run_dir = _run_scenario(
                        row,
                        iteration=iteration,
                        lane=lane_name,
                        duration=int(args.duration_seconds),
                    )
                    scenario_rows.append(
                        {
                            "scenario": row["scenario"],
                            "run_id": run_dir.name,
                            "run_dir": str(run_dir),
                        }
                    )
                suite_out = (
                    REPO_ROOT
                    / "docs_tmp"
                    / "cli_phase_5"
                    / f"phase4_text_contract_fresh_{lane_name}_iter_{iteration}.json"
                )
                suite_proc = _run_suite(set_name=lane_name, out_json=suite_out)
                lane_payload = report["lanes"][lane_name]
                lane_payload["iterations"].append(
                    {
                        "iteration": iteration,
                        "scenario_runs": scenario_rows,
                        "suite_rc": int(suite_proc.returncode),
                        "suite_stdout": (suite_proc.stdout or suite_proc.stderr or "").strip(),
                        "suite_report_json": str(suite_out),
                    }
                )
                lane_payload["iterations_completed"] += 1
                if suite_proc.returncode != 0:
                    report["overall_pass"] = False
                    break
            if not report["overall_pass"]:
                break
    finally:
        _stop_targets()

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(_render_md(report), encoding="utf-8")

    print(f"[fresh-signoff] wrote {out_json}")
    print(f"[fresh-signoff] wrote {out_md}")
    if report["overall_pass"]:
        print("[fresh-signoff] pass")
        return 0
    print("[fresh-signoff] fail")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
