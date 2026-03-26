from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def install_minif2f_fixture(module: Any, root: Path) -> None:
    task_ids = sorted(
        {
            task_id
            for pack_task_ids in module.PACKS.values()
            for task_id in pack_task_ids
        }
        | set(module.EXCLUDED_TASKS.keys())
    )
    lines = []
    for task_id in task_ids:
        lines.extend(
            [
                f"theorem {task_id} : True := by",
                "  trivial",
                "end",
                "",
            ]
        )
    midpoint = len(lines) // 2
    test_path = root / "test.lean"
    valid_path = root / "valid.lean"
    test_path.write_text("\n".join(lines[:midpoint]) + "\n", encoding="utf-8")
    valid_path.write_text("\n".join(lines[midpoint:]) + "\n", encoding="utf-8")
    module.MINIF2F_FILES = [test_path, valid_path]


def make_cross_system_report(
    *,
    task_count: int,
    candidate_solved: int,
    baseline_solved: int,
    candidate_only: int | None = None,
    baseline_only: int | None = None,
) -> dict[str, Any]:
    both_solved = min(candidate_solved, baseline_solved)
    if candidate_only is None:
        candidate_only = max(0, candidate_solved - both_solved)
    if baseline_only is None:
        baseline_only = max(0, baseline_solved - both_solved)
    both_unsolved = max(0, task_count - both_solved - candidate_only - baseline_only)
    return {
        "task_count": task_count,
        "candidate_system": "bb_hilbert_like",
        "baseline_system": "hilbert_roselab",
        "system_metrics": {
            "bb_hilbert_like": {"solved_count": candidate_solved},
            "hilbert_roselab": {"solved_count": baseline_solved},
        },
        "paired_outcomes": {
            "n11_both_solved": both_solved,
            "n10_candidate_only": candidate_only,
            "n01_baseline_only": baseline_only,
            "n00_both_unsolved": both_unsolved,
        },
        "directional_summary": {
            "candidate_minus_baseline_solve_rate": round(
                (candidate_solved - baseline_solved) / task_count if task_count else 0.0,
                6,
            )
        },
    }


def install_canonical_baseline_fixture(module: Any, repo_root: Path) -> None:
    module.REPO_ROOT = repo_root
    for index, spec in enumerate(module.CANONICAL_BASELINES):
        task_count = 4 if spec["role"] != "boundary_stress" else 2
        candidate_solved = 3 if spec["role"] != "boundary_stress" else 0
        baseline_solved = 2 if spec["role"] != "boundary_stress" else 0
        report = make_cross_system_report(
            task_count=task_count,
            candidate_solved=candidate_solved,
            baseline_solved=baseline_solved,
        )
        validation = {"ok": True}
        status_doc = repo_root / spec["status_doc"]
        status_doc.parent.mkdir(parents=True, exist_ok=True)
        status_doc.write_text(
            f"# {spec['pack_id']}\n\n- maintained Hilbert spend: ~$0.{index + 1:06d}\n",
            encoding="utf-8",
        )
        write_json(repo_root / spec["report"], report)
        write_json(repo_root / spec["validation"], validation)
