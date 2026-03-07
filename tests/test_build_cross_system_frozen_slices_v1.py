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


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_coerce_formal_statement_to_sorry_appends_stub() -> None:
    module = _load_module("build_cross_system_frozen_slices_v1_coerce", "scripts/build_cross_system_frozen_slices_v1.py")
    value = module._coerce_formal_statement_to_sorry("theorem demo : True := by")
    assert "sorry" in value


def test_build_cross_system_frozen_slices_writes_bundle(tmp_path: Path, monkeypatch) -> None:
    module = _load_module("build_cross_system_frozen_slices_v1_main", "scripts/build_cross_system_frozen_slices_v1.py")

    putnam_root = tmp_path / "putnam"
    _write(putnam_root / "lean4" / "src" / "putnam_2000_a1.lean", "import Mathlib\n\ntheorem putnam_2000_a1 : True := by\n  sorry\n")
    _write(putnam_root / "lean4" / "src" / "putnam_2000_a2.lean", "import Mathlib\n\ntheorem putnam_2000_a2 : True := by\n  sorry\n")
    _write(putnam_root / "lean4" / "src" / "putnam_2000_a3.lean", "import Mathlib\n\ntheorem putnam_2000_a3 : True := by\n  sorry\n")

    minif2f_path = tmp_path / "miniF2F_v2c.json"
    minif2f_payload = [
        {
            "name": "mini_1",
            "split": "valid",
            "header": "import Mathlib",
            "formal_statement": "theorem mini_1 : True := by",
            "informal_statement": "show True",
        },
        {
            "name": "mini_2",
            "split": "valid",
            "header": "import Mathlib",
            "formal_statement": "theorem mini_2 : True := by",
            "informal_statement": "show True",
        },
        {
            "name": "mini_3",
            "split": "test",
            "header": "import Mathlib",
            "formal_statement": "theorem mini_3 : True := by",
            "informal_statement": "show True",
        },
    ]
    minif2f_path.write_text(json.dumps(minif2f_payload, indent=2), encoding="utf-8")

    out_root = tmp_path / "out"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_cross_system_frozen_slices_v1.py",
            "--putnambench-root",
            str(putnam_root),
            "--minif2f-dataset-json",
            str(minif2f_path),
            "--seed",
            "11",
            "--n-tasks",
            "2",
            "--out-root",
            str(out_root),
        ],
    )
    assert module.main() == 0

    summary_path = out_root / "bundle_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["schema"] == "breadboard.cross_system_frozen_slice_bundle.v1"
    assert len(summary["bundles"]) == 2

    for bundle in summary["bundles"]:
        manifest = json.loads(Path(bundle["manifest_path"]).read_text(encoding="utf-8"))
        task_inputs = json.loads(Path(bundle["task_inputs_path"]).read_text(encoding="utf-8"))
        assert manifest["benchmark"]["slice"]["n_tasks"] == 2
        assert manifest["benchmark"]["slice"]["method"] == "seeded_hash_sort_v1"
        assert task_inputs["n_tasks"] == 2
        assert len(task_inputs["tasks"]) == 2
        for row in task_inputs["tasks"]:
            assert len(row["input_hash"]) == 64
