from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_module(module_name: str, rel_path: str):
    module_path = _repo_root() / rel_path
    scripts_dir = str((_repo_root() / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def test_build_atp_n100_ablation_report_v1_with_signal(tmp_path: Path) -> None:
    mod = _load_module(
        "build_atp_n100_ablation_report_v1",
        "scripts/build_atp_n100_ablation_report_v1.py",
    )
    root = tmp_path / "slices"
    _write_jsonl(
        root / "slice_a" / "bb_atp_full_n100.jsonl",
        [
            {"task_id": "a1", "status": "SOLVED"},
            {"task_id": "a2", "status": "UNSOLVED"},
        ],
    )
    _write_jsonl(
        root / "slice_b" / "bb_atp_full_n100.jsonl",
        [
            {"task_id": "b1", "status": "SOLVED"},
            {"task_id": "b2", "status": "SOLVED"},
        ],
    )
    payload = mod._build_report(root=root, result_name="bb_atp_full_n100.jsonl")
    assert payload["slice_count"] == 2
    aggregate = payload["aggregate"]
    assert abs(float(aggregate["baseline"]["solve_rate"]) - 0.75) < 1e-9
    assert float(aggregate["retrieval_off"]["solve_rate"]) == 0.0
    assert float(aggregate["retrieval_off"]["delta_vs_baseline"]) == -0.75
    assert payload["interpretation"]["baseline_signal_floor"] is False


def test_build_atp_n100_ablation_report_v1_zero_floor(tmp_path: Path) -> None:
    mod = _load_module(
        "build_atp_n100_ablation_report_v1_zero_floor",
        "scripts/build_atp_n100_ablation_report_v1.py",
    )
    root = tmp_path / "slices"
    _write_jsonl(
        root / "slice_a" / "bb_atp_full_n100.jsonl",
        [
            {"task_id": "a1", "status": "UNSOLVED"},
            {"task_id": "a2", "status": "UNSOLVED"},
        ],
    )
    payload = mod._build_report(root=root, result_name="bb_atp_full_n100.jsonl")
    assert payload["slice_count"] == 1
    assert payload["interpretation"]["baseline_signal_floor"] is True
    assert float(payload["aggregate"]["baseline"]["solve_rate"]) == 0.0
    assert float(payload["aggregate"]["repair_off"]["delta_vs_baseline"]) == 0.0
