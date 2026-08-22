from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


_RESTORE_REPO_ROOT = Path(
    "/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_ctrees_restore_20260310"
)
_RUNNER_PATH = _RESTORE_REPO_ROOT / "scripts/ctlab_e2e_runner.py"
_SCENARIO_ROOT = Path(
    "/shared_folders/querylake_server/ray_testing/ray_SCE/docs_tmp/c_trees/phases/fc0_live_recovered/scenarios_nonanthro"
)
_DEFAULT_SCENARIO_PATHS = (
    _SCENARIO_ROOT / "ct_fc0_hidden_constraint_probe_AE_guard_openai_h1_selection_focus_cg1_toolbudget_v1.json",
    _SCENARIO_ROOT / "ct_fc0_lost_constraint_AE_guard_openai_l1_stability_cg1_toolbudget_v1.json",
    _SCENARIO_ROOT
    / "ct_fc0_merge_policy_path_clarifier_noexec_guard_listdir_guard_runshell_first_workdir_bootstrap_openai_m1_selection_focus_toolbudget_v1.json",
)
_DEFAULT_OUT_DIR = Path(
    "/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_ctrees_restore_20260310/logs/phase10_prompt_centric_historical_bridge_v1"
)
_DEFAULT_CONDITION_ID = "ctrees.task_tree_toolbudget_v1"


def _first_nested_key(payload: Any, target_key: str) -> Optional[Any]:
    if isinstance(payload, dict):
        if target_key in payload:
            return payload.get(target_key)
        for value in payload.values():
            match = _first_nested_key(value, target_key)
            if match is not None:
                return match
    if isinstance(payload, list):
        for value in payload:
            match = _first_nested_key(value, target_key)
            if match is not None:
                return match
    return None


def _safe_json_load(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_prompt_centric_historical_runner_manifest(
    *,
    scenario_paths: Optional[Iterable[str | Path]] = None,
    repeat: int = 1,
    out_dir: Optional[str | Path] = None,
    condition_id: str = _DEFAULT_CONDITION_ID,
) -> Dict[str, Any]:
    resolved_paths: List[str] = []
    for path in list(scenario_paths or _DEFAULT_SCENARIO_PATHS):
        resolved = Path(path)
        resolved_paths.append(str(resolved))
    return {
        "schema_version": "ctree_historical_runner_manifest_v1",
        "status": "frozen_external_historical_not_integrated",
        "repo_root": str(_RESTORE_REPO_ROOT),
        "runner_path": str(_RUNNER_PATH),
        "condition_id": str(condition_id),
        "scenario_paths": resolved_paths,
        "repeat": max(int(repeat), 1),
        "out_dir": str(Path(out_dir) if out_dir is not None else _DEFAULT_OUT_DIR),
    }


def render_prompt_centric_historical_runner_commands(manifest: Dict[str, Any]) -> List[str]:
    runner_path = Path(str(manifest.get("runner_path") or _RUNNER_PATH))
    out_dir = str(manifest.get("out_dir") or _DEFAULT_OUT_DIR)
    repeat = max(int(manifest.get("repeat") or 1), 1)
    condition_id = str(manifest.get("condition_id") or _DEFAULT_CONDITION_ID)
    commands: List[str] = []
    for scenario_path in list(manifest.get("scenario_paths") or []):
        scenario = Path(str(scenario_path))
        commands.append(
            " ".join(
                [
                    "python",
                    shlex.quote(str(runner_path)),
                    shlex.quote(str(scenario)),
                    "--out",
                    shlex.quote(out_dir),
                    "--conditions",
                    shlex.quote(condition_id),
                    "--repeat",
                    str(repeat),
                    "--stop-on-provider-hard-limit",
                    "--reset-results",
                ]
            )
        )
    return commands


def load_historical_runner_result(run_dir: str | Path) -> Dict[str, Any]:
    run_path = Path(run_dir)
    config_path = run_path / "config.yaml"
    run_summary_path = run_path / "ctrees_lab_bundle/meta/run_summary.json"
    selection_deltas_path = run_path / "ctrees_lab_bundle/ctrees/selection_deltas.json"
    frozen_tree_path = run_path / "ctrees_lab_bundle/ctrees/tree/frozen.json"

    run_summary = _safe_json_load(run_summary_path)
    selection_deltas = _safe_json_load(selection_deltas_path)
    context_engine = _first_nested_key(run_summary, "ctrees_context_engine") or {}
    replace_stats = _first_nested_key(run_summary, "ctrees_context_engine_replace_stats") or {}

    return {
        "schema_version": "ctree_historical_runner_result_v1",
        "run_dir": str(run_path),
        "paths": {
            "config": str(config_path),
            "run_summary": str(run_summary_path),
            "selection_deltas": str(selection_deltas_path),
            "frozen_tree": str(frozen_tree_path),
        },
        "exists": {
            "config": config_path.exists(),
            "run_summary": run_summary_path.exists(),
            "selection_deltas": selection_deltas_path.exists(),
            "frozen_tree": frozen_tree_path.exists(),
        },
        "observed_semantics": {
            "context_engine_mode": str(context_engine.get("mode") or ""),
            "render_mode": str(context_engine.get("render_mode") or ""),
            "replace_stats_present": isinstance(replace_stats, dict) and bool(replace_stats),
        },
        "selection_delta_totals": dict(selection_deltas.get("totals") or {}),
        "selection_delta_stage": str(selection_deltas.get("stage") or ""),
    }

