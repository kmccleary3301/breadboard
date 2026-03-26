from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


_RESTORE_REPO_ROOT = Path(
    "/shared_folders/querylake_server/ray_testing/ray_SCE/breadboard_repo_ctrees_restore_20260310"
)
_RUN_DIR = _RESTORE_REPO_ROOT / (
    "logs/phase8_stage2b_transition_event_recording_v2_canary_r2_clean_20260310/"
    "ct_fc0_hidden_constraint_probe_AE_guard_openai_h1_selection_focus_cg1_toolbudget_v1_stage2b_transition_event_recording_v2_canary-"
    "ctrees.task_tree_toolbudget_v1-20260311-013102-r1"
)


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


def load_prompt_centric_historical_fixture() -> Dict[str, Any]:
    config_path = _RUN_DIR / "config.yaml"
    run_summary_path = _RUN_DIR / "ctrees_lab_bundle/meta/run_summary.json"
    selection_deltas_path = _RUN_DIR / "ctrees_lab_bundle/ctrees/selection_deltas.json"
    frozen_tree_path = _RUN_DIR / "ctrees_lab_bundle/ctrees/tree/frozen.json"

    run_summary = _safe_json_load(run_summary_path)
    context_engine = _first_nested_key(run_summary, "ctrees_context_engine")
    replace_stats = _first_nested_key(run_summary, "ctrees_context_engine_replace_stats")

    return {
        "fixture_id": "prompt_centric_historical_ctrees_task_tree_toolbudget_v1",
        "status": "frozen_external_historical_not_integrated",
        "source_commit": "477c850",
        "repo_root": str(_RESTORE_REPO_ROOT),
        "run_dir": str(_RUN_DIR),
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
            "context_engine_mode": str((context_engine or {}).get("mode") or ""),
            "render_mode": str((context_engine or {}).get("render_mode") or ""),
            "replace_stats_present": isinstance(replace_stats, dict),
        },
    }

