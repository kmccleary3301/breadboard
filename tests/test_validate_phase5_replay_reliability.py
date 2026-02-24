from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "validate_phase5_replay_reliability.py"
    scripts_dir = str((repo_root / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("validate_phase5_replay_reliability", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_contract(path: Path) -> None:
    payload = {
        "scenario_sets": {
            "hard_gate": ["phase4_replay/foo_v1"],
            "nightly": ["phase4_replay/bar_v1"],
        }
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_run(
    run_root: Path,
    *,
    scenario: str,
    run_id: str,
    first_frame_seconds: float = 1.0,
    replay_first_frame_seconds: float = 0.5,
    duration_seconds: float = 10.0,
    row_parity_violation: bool = False,
    render_profile: str = "phase4_locked_v5",
    line_gap: int = 0,
) -> Path:
    short = scenario.replace("phase4_replay/", "", 1)
    run_dir = run_root / short / run_id
    frames_dir = run_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    started_at = 1000.0
    replay_send_end = 1001.2
    index = [
        {
            "frame": 1,
            "timestamp": started_at + first_frame_seconds,
            "text": "frames/frame_0001.txt",
            "ansi": "frames/frame_0001.ansi",
            "render_lock": "frames/frame_0001.render_lock.json",
            "render_parity_summary": "frames/frame_0001.row_parity.json",
        },
        {
            "frame": 2,
            "timestamp": replay_send_end + replay_first_frame_seconds,
            "text": "frames/frame_0002.txt",
            "ansi": "frames/frame_0002.ansi",
            "render_lock": "frames/frame_0002.render_lock.json",
            "render_parity_summary": "frames/frame_0002.row_parity.json",
        },
    ]
    (run_dir / "index.jsonl").write_text(
        "\n".join(json.dumps(row) for row in index) + "\n",
        encoding="utf-8",
    )

    for frame in ("frame_0001", "frame_0002"):
        (frames_dir / f"{frame}.txt").write_text("x\n", encoding="utf-8")
        (frames_dir / f"{frame}.ansi").write_text("x\n", encoding="utf-8")
        parity = {
            "schema_version": "tmux_row_parity_summary_v1",
            "parity": {
                "missing_count": 1 if row_parity_violation else 0,
                "extra_count": 0,
                "row_span_delta": 0,
                "mismatch_rows": [],
            },
        }
        (frames_dir / f"{frame}.row_parity.json").write_text(
            json.dumps(parity, indent=2) + "\n",
            encoding="utf-8",
        )
        lock = {
            "render_profile": render_profile,
            "geometry": {"line_gap": line_gap},
            "row_occupancy": {
                "declared_rows": 45,
                "edge_row_diagnostics": [
                    {"row": 20, "edge_ratio": 0.0},
                    {"row": 21, "edge_ratio": 0.0},
                    {"row": 22, "edge_ratio": 0.0},
                ],
            },
        }
        (frames_dir / f"{frame}.render_lock.json").write_text(
            json.dumps(lock, indent=2) + "\n",
            encoding="utf-8",
        )

    manifest = {
        "scenario": scenario,
        "run_id": run_id,
        "started_at": started_at,
        "duration_seconds": duration_seconds,
        "executed_actions": [
            {
                "action": {"type": "send", "text": "replay:fixture.jsonl"},
                "ended_at": replay_send_end,
            }
        ],
        "frame_validation": {
            "max_unchanged_streak_seconds_estimate": 1.0,
            "missing_artifacts": [],
        },
    }
    (run_dir / "scenario_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    return run_dir


def test_validate_phase5_replay_reliability_passes(tmp_path: Path):
    module = _load_module()
    run_root = tmp_path / "runs"
    contract = tmp_path / "contract.json"
    _write_contract(contract)
    _write_run(run_root, scenario="phase4_replay/foo_v1", run_id="20260224-000001")

    result = module.validate_reliability(
        run_root=run_root,
        scenario_set_name="hard_gate",
        contract_file=contract,
        expected_render_profile="phase4_locked_v5",
        max_first_frame_seconds=3.0,
        max_replay_first_frame_seconds=2.0,
        max_duration_seconds=25.0,
        max_stall_seconds=3.0,
        max_isolated_low_edge_rows_total=0,
        fail_on_missing_scenarios=True,
    )
    assert result.ok is True
    assert result.report["overall_ok"] is True
    assert len(result.report["records"]) == 1


def test_validate_phase5_replay_reliability_fails_on_latency_and_parity(tmp_path: Path):
    module = _load_module()
    run_root = tmp_path / "runs"
    contract = tmp_path / "contract.json"
    _write_contract(contract)
    _write_run(
        run_root,
        scenario="phase4_replay/foo_v1",
        run_id="20260224-000002",
        first_frame_seconds=5.5,
        replay_first_frame_seconds=2.5,
        row_parity_violation=True,
    )

    result = module.validate_reliability(
        run_root=run_root,
        scenario_set_name="hard_gate",
        contract_file=contract,
        expected_render_profile="phase4_locked_v5",
        max_first_frame_seconds=3.0,
        max_replay_first_frame_seconds=2.0,
        max_duration_seconds=25.0,
        max_stall_seconds=3.0,
        max_isolated_low_edge_rows_total=0,
        fail_on_missing_scenarios=True,
    )
    assert result.ok is False
    assert any("phase4_replay/foo_v1: reliability checks failed" in err for err in result.errors)
    record = result.report["records"][0]
    checks = {row["name"]: row["pass"] for row in record["checks"]}
    assert checks["first_frame_seconds"] is False
    assert checks["replay_first_frame_seconds"] is False
    assert checks["row_parity_violations"] is False


def test_validate_phase5_replay_reliability_fails_on_missing_selected_scenario(tmp_path: Path):
    module = _load_module()
    run_root = tmp_path / "runs"
    contract = tmp_path / "contract.json"
    _write_contract(contract)
    # Only write nightly scenario while checking hard_gate.
    _write_run(run_root, scenario="phase4_replay/bar_v1", run_id="20260224-000003")

    result = module.validate_reliability(
        run_root=run_root,
        scenario_set_name="hard_gate",
        contract_file=contract,
        expected_render_profile="phase4_locked_v5",
        max_first_frame_seconds=3.0,
        max_replay_first_frame_seconds=2.0,
        max_duration_seconds=25.0,
        max_stall_seconds=3.0,
        max_isolated_low_edge_rows_total=0,
        fail_on_missing_scenarios=True,
    )
    assert result.ok is False
    assert "phase4_replay/foo_v1" in result.report["missing_scenarios"]
