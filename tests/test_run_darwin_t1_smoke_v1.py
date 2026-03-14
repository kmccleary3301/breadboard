from __future__ import annotations

from scripts.run_darwin_t1_smoke_v1 import run_smoke


def test_run_darwin_t1_smoke_executes_full_pipeline() -> None:
    summary = run_smoke()
    assert summary["readiness"]["overall_ok"] is True
    assert summary["rollup"]["bootstrap_spec_count"] == 5
