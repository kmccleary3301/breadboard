#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml


CODEx_SCENARIO = "nightly_provider/codex_e2e_compact_semantic_v1"
CLAUDE_SCENARIO = "nightly_provider/claude_e2e_compact_semantic_v1"
OPENCODE_SCENARIOS = [
    "opencode_mvi_bash_write_replay",
    "opencode_protofs_gpt5nano_toolio_replay",
    "opencode_patch_todo_sentinel_replay",
    "opencode_glob_grep_sentinel_replay",
    "opencode_toolcall_repair_sentinel_replay",
    "opencode_webfetch_sentinel_replay",
]


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    printable = " ".join(subprocess.list2cmdline([part]) for part in cmd)
    print(f"$ {printable}")
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        capture_output=capture,
        check=False,
    )
    if capture and proc.stdout:
        print(proc.stdout.rstrip())
    if capture and proc.stderr:
        print(proc.stderr.rstrip())
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed rc={proc.returncode}: {cmd}")
    return proc


def _iso_utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _date_label(iso: str) -> str:
    return iso[:10]


def _git_show_commit(repo: Path, ref: str) -> tuple[str, str]:
    commit = _run(["git", "-C", str(repo), "rev-parse", ref], cwd=repo, capture=True).stdout.strip()
    commit_date = _run(
        ["git", "-C", str(repo), "show", "-s", "--format=%cI", ref], cwd=repo, capture=True
    ).stdout.strip()
    return commit, commit_date


def _extract_run_dir(output: str) -> Path | None:
    match = re.search(r"^\[scenario\]\s+run_dir=(.+)$", output, flags=re.MULTILINE)
    if not match:
        return None
    return Path(match.group(1).strip()).resolve()


def _copy_capture_evidence(
    *,
    external_run_dir: Path,
    repo_root: Path,
) -> Path:
    scenario_dir = external_run_dir.parent
    run_id = external_run_dir.name
    rel_scenario = scenario_dir.relative_to(repo_root.parent / "docs_tmp" / "tmux_captures" / "scenarios")
    dest_dir = repo_root / "docs_tmp" / "tmux_captures" / "scenarios" / rel_scenario / run_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name in ("meta.json", "scenario_manifest.json", "run_summary.json"):
        shutil.copy2(external_run_dir / name, dest_dir / name)
    return dest_dir


def _find_claude_repo(harness_root: Path) -> Path:
    candidates = [
        harness_root / "sdk_competitors" / "claude_code",
        harness_root / "claude-code",
        harness_root / "claude_code",
    ]
    for candidate in candidates:
        if (candidate / ".git").exists():
            return candidate
    raise FileNotFoundError("Could not locate claude-code git clone under harness root")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _load_manifest(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("manifest root must be mapping")
    cfgs = payload.get("e4_configs")
    if not isinstance(cfgs, dict):
        raise ValueError("manifest.e4_configs must be mapping")
    return payload


def _update_manifest(
    *,
    manifest_path: Path,
    codex_commit: str,
    codex_date: str,
    codex_run_id: str,
    claude_commit: str,
    claude_date: str,
    claude_run_id: str,
    opencode_commit: str,
    opencode_date: str,
    opencode_batch_run_id: str,
) -> None:
    payload = _load_manifest(manifest_path)
    cfgs: dict[str, Any] = payload["e4_configs"]
    payload["manifest_updated_utc"] = _iso_utc_now()

    codex = cfgs["codex_cli_gpt51mini_e4_live"]
    codex["harness"]["upstream_commit"] = codex_commit
    codex["harness"]["upstream_commit_date"] = codex_date
    codex["harness"]["upstream_release_label"] = f"codex-cli@snapshot-{_date_label(codex_date)}-main"
    codex["calibration_anchor"]["run_id"] = codex_run_id
    codex["calibration_anchor"]["evidence_paths"] = [
        f"docs_tmp/tmux_captures/scenarios/{CODEx_SCENARIO}/{codex_run_id}/meta.json",
        f"docs_tmp/tmux_captures/scenarios/{CODEx_SCENARIO}/{codex_run_id}/scenario_manifest.json",
        f"docs_tmp/tmux_captures/scenarios/{CODEx_SCENARIO}/{codex_run_id}/run_summary.json",
    ]

    claude = cfgs["claude_code_haiku45_e4_replay"]
    claude["harness"]["upstream_commit"] = claude_commit
    claude["harness"]["upstream_commit_date"] = claude_date
    claude["harness"]["upstream_release_label"] = f"claude-code@snapshot-{_date_label(claude_date)}"
    claude["calibration_anchor"]["run_id"] = claude_run_id
    claude["calibration_anchor"]["evidence_paths"] = [
        f"docs_tmp/tmux_captures/scenarios/{CLAUDE_SCENARIO}/{claude_run_id}/meta.json",
        f"docs_tmp/tmux_captures/scenarios/{CLAUDE_SCENARIO}/{claude_run_id}/scenario_manifest.json",
        f"docs_tmp/tmux_captures/scenarios/{CLAUDE_SCENARIO}/{claude_run_id}/run_summary.json",
    ]

    opencode_result_files = {
        "opencode_mvi_bash_write_replay": "opencode_mvi_bash_write_replay_result.json",
        "opencode_glob_grep_sentinel_replay": "opencode_glob_grep_sentinel_replay_result.json",
        "opencode_patch_todo_sentinel_replay": "opencode_patch_todo_sentinel_replay_result.json",
        "opencode_toolcall_repair_sentinel_replay": "opencode_toolcall_repair_sentinel_replay_result.json",
        "opencode_webfetch_sentinel_replay": "opencode_webfetch_sentinel_replay_result.json",
        "opencode_protofs_gpt5nano_toolio_replay": "opencode_protofs_gpt5nano_toolio_replay_result.json",
    }
    opencode_keys = [
        ("opencode_e4_mvi_replay", "opencode_mvi_bash_write_replay"),
        ("opencode_e4_glob_grep_sentinel_replay", "opencode_glob_grep_sentinel_replay"),
        ("opencode_e4_patch_todo_sentinel_replay", "opencode_patch_todo_sentinel_replay"),
        ("opencode_e4_toolcall_repair_sentinel_replay", "opencode_toolcall_repair_sentinel_replay"),
        ("opencode_e4_webfetch_sentinel_replay", "opencode_webfetch_sentinel_replay"),
        ("opencode_e4_oc_protofs_gpt5nano_replay", "opencode_protofs_gpt5nano_toolio_replay"),
    ]
    for key, scenario_id in opencode_keys:
        row = cfgs[key]
        row["harness"]["upstream_commit"] = opencode_commit
        row["harness"]["upstream_commit_date"] = opencode_date
        row["harness"]["upstream_release_label"] = f"opencode@dev-snapshot-{_date_label(opencode_date)}"
        evidence = row["calibration_anchor"]["evidence_paths"]
        if isinstance(evidence, list):
            # Preserve fixture paths and only rewrite result bundle paths.
            rewritten: list[str] = []
            for path in evidence:
                if "docs/conformance/e4_recalibration_evidence/" in path:
                    if path.endswith("parity_summary.json"):
                        rewritten.append(
                            f"docs/conformance/e4_recalibration_evidence/{opencode_batch_run_id}/parity_summary.json"
                        )
                    elif path.endswith("_result.json"):
                        rewritten.append(
                            f"docs/conformance/e4_recalibration_evidence/{opencode_batch_run_id}/{opencode_result_files[scenario_id]}"
                        )
                    else:
                        rewritten.append(path)
                else:
                    rewritten.append(path)
            row["calibration_anchor"]["evidence_paths"] = rewritten

    manifest_path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one-command E4 snapshot recalibration")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--harness-root", default=None, help="defaults to <repo-root>/../other_harness_refs")
    parser.add_argument("--tmux-socket", default="bb_e4_snapshot")
    parser.add_argument("--codex-session", default="breadboard_test_e4_codex")
    parser.add_argument("--claude-session", default="breadboard_test_e4_claude")
    parser.add_argument("--codex-port", type=int, default=9461)
    parser.add_argument("--claude-port", type=int, default=9462)
    parser.add_argument("--snapshot-id", default=None)
    parser.add_argument("--manifest", default="config/e4_target_freeze_manifest.yaml")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    harness_root = (
        Path(args.harness_root).resolve() if args.harness_root else (repo_root.parent / "other_harness_refs").resolve()
    )
    manifest_path = (repo_root / args.manifest).resolve()

    codex_repo = harness_root / "codex"
    opencode_repo = harness_root / "opencode"
    claude_repo = _find_claude_repo(harness_root)
    if not (codex_repo / ".git").exists():
        raise FileNotFoundError(f"Missing codex clone: {codex_repo}")
    if not (opencode_repo / ".git").exists():
        raise FileNotFoundError(f"Missing opencode clone: {opencode_repo}")

    for repo in (codex_repo, claude_repo, opencode_repo):
        _run(["git", "-C", str(repo), "fetch", "origin"], cwd=repo_root)

    codex_commit, codex_date = _git_show_commit(codex_repo, "origin/main")
    claude_commit, claude_date = _git_show_commit(claude_repo, "origin/main")
    opencode_commit, opencode_date = _git_show_commit(opencode_repo, "origin/HEAD")

    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    snapshot_id = args.snapshot_id or f"e4_refsnapshot_{ts}"
    snapshot_payload = {
        "snapshot_id": snapshot_id,
        "captured_at_utc": _iso_utc_now(),
        "entries": {
            "codex": {
                "repo_url": "https://github.com/openai/codex",
                "local_repo": str(codex_repo),
                "ref": "origin/main",
                "commit": codex_commit,
                "commit_date": codex_date,
            },
            "claude_code": {
                "repo_url": "https://github.com/anthropics/claude-code.git",
                "local_repo": str(claude_repo),
                "ref": "origin/main",
                "commit": claude_commit,
                "commit_date": claude_date,
            },
            "opencode": {
                "repo_url": "https://github.com/anomalyco/opencode",
                "local_repo": str(opencode_repo),
                "ref": "origin/HEAD",
                "commit": opencode_commit,
                "commit_date": opencode_date,
            },
        },
    }
    artifacts_snapshot = repo_root / "artifacts" / "conformance" / f"{snapshot_id}.json"
    tracked_snapshot = repo_root / "docs" / "conformance" / "e4_recalibration_evidence" / f"{snapshot_id}.json"
    _write_json(artifacts_snapshot, snapshot_payload)
    _write_json(tracked_snapshot, snapshot_payload)

    try:
        _run(
            [
                "bash",
                "scripts/start_tmux_phase4_replay_target.sh",
                "--session",
                args.codex_session,
                "--tmux-socket",
                args.tmux_socket,
                "--port",
                str(args.codex_port),
                "--tui-preset",
                "codex_cli_like",
                "--config",
                "agent_configs/codex_0-107-0_e4_3-6-2026.yaml",
            ],
            cwd=repo_root,
        )
        _run(
            [
                "bash",
                "scripts/start_tmux_phase4_replay_target.sh",
                "--session",
                args.claude_session,
                "--tmux-socket",
                args.tmux_socket,
                "--port",
                str(args.claude_port),
                "--tui-preset",
                "claude_code_like",
                "--config",
                "agent_configs/claude_code_2-1-63_e4_3-6-2026.yaml",
            ],
            cwd=repo_root,
        )

        captures_root = repo_root.parent / "docs_tmp" / "tmux_captures" / "scenarios"

        codex_proc = _run(
            [
                "python",
                "scripts/run_tmux_capture_scenario.py",
                "--target",
                f"{args.codex_session}:0.0",
                "--tmux-socket",
                args.tmux_socket,
                "--scenario",
                CODEx_SCENARIO,
                "--actions",
                "config/tmux_scenario_actions/nightly_provider/codex_e2e_compact_semantic_v1.json",
                "--out-root",
                str(captures_root),
                "--duration",
                "20",
                "--interval",
                "0.5",
                "--capture-mode",
                "fullpane",
                "--tail-lines",
                "120",
                "--final-tail-lines",
                "0",
                "--fullpane-start-markers",
                "Config:,/tmp/",
                "--fullpane-max-lines",
                "500",
                "--fullpane-render-max-rows",
                "300",
                "--render-profile",
                "phase4_locked_v5",
                "--settle-ms",
                "140",
                "--settle-attempts",
                "5",
                "--session-prefix-guard",
                "breadboard_test_",
                "--protected-sessions",
                "bb_tui_codex_dev,bb_engine_codex_dev,bb_atp",
            ],
            cwd=repo_root,
            capture=True,
        )
        claude_proc = _run(
            [
                "python",
                "scripts/run_tmux_capture_scenario.py",
                "--target",
                f"{args.claude_session}:0.0",
                "--tmux-socket",
                args.tmux_socket,
                "--scenario",
                CLAUDE_SCENARIO,
                "--actions",
                "config/tmux_scenario_actions/nightly_provider/claude_e2e_compact_semantic_v1.json",
                "--out-root",
                str(captures_root),
                "--duration",
                "20",
                "--interval",
                "0.5",
                "--capture-mode",
                "fullpane",
                "--tail-lines",
                "120",
                "--final-tail-lines",
                "0",
                "--fullpane-start-markers",
                "Config:,/tmp/",
                "--fullpane-max-lines",
                "500",
                "--fullpane-render-max-rows",
                "300",
                "--render-profile",
                "phase4_locked_v5",
                "--settle-ms",
                "140",
                "--settle-attempts",
                "5",
                "--session-prefix-guard",
                "breadboard_test_",
                "--protected-sessions",
                "bb_tui_codex_dev,bb_engine_codex_dev,bb_atp",
            ],
            cwd=repo_root,
            capture=True,
        )
    finally:
        _run(["tmux", "-L", args.tmux_socket, "kill-server"], cwd=repo_root, check=False)

    codex_run_dir = _extract_run_dir(codex_proc.stdout)
    claude_run_dir = _extract_run_dir(claude_proc.stdout)
    if codex_run_dir is None or claude_run_dir is None:
        raise RuntimeError("Could not parse run_dir from scenario output")
    _copy_capture_evidence(external_run_dir=codex_run_dir, repo_root=repo_root)
    _copy_capture_evidence(external_run_dir=claude_run_dir, repo_root=repo_root)
    codex_run_id = codex_run_dir.name
    claude_run_id = claude_run_dir.name

    opencode_run_id = f"e4_batchA_serial_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    _run(
        [
            "python",
            "scripts/run_parity_replays.py",
            "--include-disabled",
            "--parity-run-id",
            opencode_run_id,
            *sum([["--scenario", s] for s in OPENCODE_SCENARIOS], []),
        ],
        cwd=repo_root,
        env={**dict(), **{}},
    )
    parity_dir = repo_root / "artifacts" / "parity_runs" / opencode_run_id
    evidence_dir = repo_root / "docs" / "conformance" / "e4_recalibration_evidence" / opencode_run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    for src in parity_dir.glob("*.json"):
        shutil.copy2(src, evidence_dir / src.name)

    _update_manifest(
        manifest_path=manifest_path,
        codex_commit=codex_commit,
        codex_date=codex_date,
        codex_run_id=codex_run_id,
        claude_commit=claude_commit,
        claude_date=claude_date,
        claude_run_id=claude_run_id,
        opencode_commit=opencode_commit,
        opencode_date=opencode_date,
        opencode_batch_run_id=opencode_run_id,
    )

    strict_json = _run(
        ["python", "scripts/check_e4_target_freeze_manifest.py", "--strict-evidence", "--json"],
        cwd=repo_root,
        capture=True,
    ).stdout
    strict45_json = _run(
        [
            "python",
            "scripts/check_e4_target_freeze_manifest.py",
            "--strict-evidence",
            "--max-evidence-age-days",
            "45",
            "--json",
        ],
        cwd=repo_root,
        capture=True,
    ).stdout

    drift_snapshot_out = repo_root / "artifacts" / "conformance" / "e4_target_drift_snapshot_report.json"
    drift_live_out = repo_root / "artifacts" / "conformance" / "e4_target_drift_live_head_report.json"
    _run(
        [
            "python",
            "scripts/research/parity/audit_e4_target_drift.py",
            "--snapshot-json",
            str(tracked_snapshot),
            "--json-out",
            str(drift_snapshot_out),
        ],
        cwd=repo_root,
    )
    _run(
        [
            "python",
            "scripts/research/parity/audit_e4_target_drift.py",
            "--json-out",
            str(drift_live_out),
        ],
        cwd=repo_root,
    )

    summary = {
        "snapshot_id": snapshot_id,
        "snapshot_tracked_json": str(tracked_snapshot),
        "snapshot_artifact_json": str(artifacts_snapshot),
        "codex_run_id": codex_run_id,
        "claude_run_id": claude_run_id,
        "opencode_batch_run_id": opencode_run_id,
        "strict_check": json.loads(strict_json),
        "strict_45d_check": json.loads(strict45_json),
        "drift_snapshot_report": str(drift_snapshot_out),
        "drift_live_report": str(drift_live_out),
    }
    summary_path = repo_root / "artifacts" / "conformance" / "e4_snapshot_recalibration_summary.json"
    _write_json(summary_path, summary)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
