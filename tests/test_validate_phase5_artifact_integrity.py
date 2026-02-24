from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "validate_phase5_artifact_integrity.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("validate_phase5_artifact_integrity", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_contract(path: Path) -> None:
    payload = {"scenario_sets": {"hard_gate": ["phase4_replay/foo_v1"], "nightly": []}}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_run(
    run_root: Path,
    *,
    scenario: str,
    run_id: str,
    include_render_lock: bool = True,
    isolated_row: int | None = 22,
) -> None:
    slug = scenario.replace("phase4_replay/", "", 1)
    run_dir = run_root / slug / run_id
    frames = run_dir / "frames"
    frames.mkdir(parents=True, exist_ok=True)

    rows = []
    for idx in (1, 2):
        frame = f"frame_{idx:04d}"
        (frames / f"{frame}.txt").write_text("x\n", encoding="utf-8")
        (frames / f"{frame}.ansi").write_text("x\n", encoding="utf-8")
        (frames / f"{frame}.png").write_text("png\n", encoding="utf-8")
        parity = {
            "parity": {
                "missing_count": 0,
                "extra_count": 0,
                "row_span_delta": 0,
                "mismatch_rows": [],
            }
        }
        (frames / f"{frame}.row_parity.json").write_text(json.dumps(parity, indent=2) + "\n", encoding="utf-8")
        if include_render_lock:
            diagnostics = [{"row": 21, "edge_ratio": 0.0}, {"row": 22, "edge_ratio": 0.0}, {"row": 23, "edge_ratio": 0.0}]
            if isolated_row is not None:
                diagnostics = [{"row": isolated_row - 1, "edge_ratio": 0.0}, {"row": isolated_row, "edge_ratio": 0.01}, {"row": isolated_row + 1, "edge_ratio": 0.0}]
            lock = {
                "render_profile": "phase4_locked_v5",
                "geometry": {"line_gap": 0},
                "row_occupancy": {"declared_rows": 45, "edge_row_diagnostics": diagnostics},
            }
            (frames / f"{frame}.render_lock.json").write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        rows.append(
            {
                "frame": idx,
                "timestamp": 1000.0 + idx,
                "text": f"frames/{frame}.txt",
                "ansi": f"frames/{frame}.ansi",
                "png": f"frames/{frame}.png",
            }
        )
    (run_dir / "index.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    manifest = {"scenario": scenario, "run_id": run_id}
    (run_dir / "scenario_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def test_validate_phase5_artifact_integrity_pass(tmp_path: Path):
    module = _load_module()
    run_root = tmp_path / "runs"
    contract = tmp_path / "contract.json"
    _write_contract(contract)
    _write_run(run_root, scenario="phase4_replay/foo_v1", run_id="20260224-000001", include_render_lock=True, isolated_row=22)

    result = module.validate_artifact_integrity(
        run_root=run_root,
        scenario_set="hard_gate",
        contract_file=contract,
        expected_render_profile="phase4_locked_v5",
        default_max_isolated_edge_rows_total=2,
        scenario_max_isolated_edge_rows_overrides={},
        max_line_gap_nonzero_frames=0,
        fail_on_missing_scenarios=True,
    )
    assert result.ok is True
    assert result.report["overall_ok"] is True


def test_validate_phase5_artifact_integrity_override_budget_failure(tmp_path: Path):
    module = _load_module()
    run_root = tmp_path / "runs"
    contract = tmp_path / "contract.json"
    _write_contract(contract)
    _write_run(run_root, scenario="phase4_replay/foo_v1", run_id="20260224-000002", include_render_lock=True, isolated_row=22)

    result = module.validate_artifact_integrity(
        run_root=run_root,
        scenario_set="hard_gate",
        contract_file=contract,
        expected_render_profile="phase4_locked_v5",
        default_max_isolated_edge_rows_total=2,
        scenario_max_isolated_edge_rows_overrides={"phase4_replay/foo_v1": 0},
        max_line_gap_nonzero_frames=0,
        fail_on_missing_scenarios=True,
    )
    assert result.ok is False
    assert any("artifact integrity checks failed" in err for err in result.errors)


def test_validate_phase5_artifact_integrity_missing_render_lock(tmp_path: Path):
    module = _load_module()
    run_root = tmp_path / "runs"
    contract = tmp_path / "contract.json"
    _write_contract(contract)
    _write_run(run_root, scenario="phase4_replay/foo_v1", run_id="20260224-000003", include_render_lock=False, isolated_row=None)

    result = module.validate_artifact_integrity(
        run_root=run_root,
        scenario_set="hard_gate",
        contract_file=contract,
        expected_render_profile="phase4_locked_v5",
        default_max_isolated_edge_rows_total=1,
        scenario_max_isolated_edge_rows_overrides={},
        max_line_gap_nonzero_frames=0,
        fail_on_missing_scenarios=True,
    )
    assert result.ok is False
    record = result.report["records"][0]
    missing = record["localization"]["missing_artifacts"]
    assert any(row["kind"] == "render_lock" for row in missing)
