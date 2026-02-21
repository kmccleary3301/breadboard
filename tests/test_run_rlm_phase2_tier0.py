from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "run_rlm_phase2_tier0.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("run_rlm_phase2_tier0", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_run_tier0_emits_expected_schema_and_matrix(tmp_path: Path) -> None:
    module = _load_module()
    payload = module.run_tier0(seeds=1, out_root=tmp_path / "runs", model_route="mock/no_tools")
    assert payload["schema_version"] == "rlm_phase2_tier0_v1"
    assert len(payload["arms"]) == 4
    assert len(payload["scenarios"]) == 4
    assert len(payload["runs"]) == 16
    assert len(payload["aggregates"]) == 4


def test_render_and_memo_outputs(tmp_path: Path) -> None:
    module = _load_module()
    payload = module.run_tier0(seeds=1, out_root=tmp_path / "runs", model_route="mock/no_tools")
    md = module.render_markdown(payload)
    assert "Arm Summary" in md
    memo_path = tmp_path / "memo.md"
    module.write_go_no_go_memo(payload, memo_path)
    assert memo_path.exists()
    text = memo_path.read_text(encoding="utf-8")
    assert "Decision" in text
    # Ensure payload remains JSON-serializable for output artifact writing.
    json.dumps(payload)


def test_run_tier0_honors_token_cap(tmp_path: Path) -> None:
    module = _load_module()
    original = module._run_arm_scenario
    try:
        def _fake_run(*args, **kwargs):
            row = original(*args, **kwargs)
            row["total_tokens"] = 10
            return row
        module._run_arm_scenario = _fake_run
        payload = module.run_tier0(
            seeds=1,
            out_root=tmp_path / "runs",
            model_route="mock/no_tools",
            token_cap=1,
        )
    finally:
        module._run_arm_scenario = original
    assert payload["aborted_for_token_cap"] is True
    assert int(payload["total_tokens"]) > 1
