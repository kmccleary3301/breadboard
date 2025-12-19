from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from .parity import (
    EquivalenceLevel,
    build_expected_run_ir,
    build_run_ir_from_run_dir,
    compare_run_ir,
)


def run_parity_checks(
    *,
    run_dir: Path,
    golden_workspace: Path,
    guardrail_path: Optional[Path] = None,
    todo_journal_path: Optional[Path] = None,
    summary_path: Optional[Path] = None,
    target_level: EquivalenceLevel = EquivalenceLevel.NORMALIZED_TRACE,
    workspace_override: Optional[Path] = None,
) -> Dict[str, Optional[str]]:
    """
    Evaluate parity between an actual run directory and a golden workspace snapshot.

    Returns a payload containing per-level reports, the first failing level (if any),
    and the detailed mismatches produced at that level.
    """

    actual_ir = build_run_ir_from_run_dir(run_dir, workspace_override=workspace_override)
    expected_ir = build_expected_run_ir(
        golden_workspace,
        guardrail_path=guardrail_path,
        todo_journal_path=todo_journal_path,
        summary_path=summary_path,
    )
    ladder = [
        EquivalenceLevel.SEMANTIC,
        EquivalenceLevel.STRUCTURAL,
        EquivalenceLevel.NORMALIZED_TRACE,
        EquivalenceLevel.BITWISE_TRACE,
    ]
    reports: List[str] = []
    mismatches: List[str] = []
    failure_level: Optional[EquivalenceLevel] = None
    for level in ladder:
        if ladder.index(level) > ladder.index(target_level):
            break
        diff = compare_run_ir(actual_ir, expected_ir, level)
        if diff:
            reports.append(f"{level.value}: FAIL ({len(diff)} issues)")
            mismatches = diff
            failure_level = level
            break
        reports.append(f"{level.value}: PASS")

    status = "passed" if failure_level is None else "failed"
    return {
        "status": status,
        "reports": reports,
        "target_level": target_level.value,
        "failure_level": failure_level.value if failure_level else None,
        "mismatches": mismatches,
    }
