import json
from pathlib import Path

from scripts.build_hilbert_bb_comparison_bundle_v1 import build_bundle, write_summary


def test_build_bundle_creates_subset_manifest(tmp_path: Path) -> None:
    frozen = tmp_path / "frozen"
    (frozen / "mini" ).mkdir(parents=True)
    source_manifest = {
        "acceptance": {"determinism_reruns": 2, "required_fields": ["toolchain_id"]},
        "artifacts": {"root_dir": str(frozen / "mini")},
        "benchmark": {
            "name": "minif2f_v2",
            "slice": {"seed": 1337},
            "version": {"benchmark_git_sha": "abc", "dataset_sha256": "d" * 64},
        },
        "budget": {"class": "B"},
        "owner": "atp-team",
        "toolchain": {"lean_version": "4.12.0", "mathlib_commit": "unknown", "docker_image_digest": "local"},
        "systems": [{"system_id": "bb_atp", "config_ref": "agent_configs/demo.yaml"}],
    }
    task_inputs = {
        "benchmark": "minif2f_v2",
        "seed": 1337,
        "tasks": [
            {"task_id": "t1", "input_text": "theorem t1 : True := by\n  sorry\n", "input_mode": "formal_lean", "input_hash": "h1"},
            {"task_id": "t2", "input_text": "theorem t2 : True := by\n  sorry\n", "input_mode": "formal_lean", "input_hash": "h2"},
        ],
    }
    source_manifest_path = frozen / "mini" / "cross_system_manifest.json"
    source_task_inputs_path = frozen / "mini" / "aristotle_task_inputs.json"
    source_manifest_path.write_text(json.dumps(source_manifest), encoding="utf-8")
    source_task_inputs_path.write_text(json.dumps(task_inputs), encoding="utf-8")

    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    pack_manifest_path = pack_dir / "manifest.json"
    pack_manifest = {
        "pack_name": "demo_pack",
        "benchmark_slug": "mini",
        "source_task_inputs": str(source_task_inputs_path),
        "tasks": [{"task_id": "t2"}],
    }
    pack_manifest_path.write_text(json.dumps(pack_manifest), encoding="utf-8")

    import scripts.build_hilbert_bb_comparison_bundle_v1 as mod
    mod.FROZEN_ROOT = frozen
    payload = build_bundle(pack_manifest_path)
    bb_tasks = json.loads((pack_dir / "bb_task_inputs.json").read_text())
    manifest = json.loads((pack_dir / "cross_system_manifest.json").read_text())

    assert payload["bb_task_inputs_path"].endswith("bb_task_inputs.json")
    assert bb_tasks["n_tasks"] == 1
    assert bb_tasks["tasks"][0]["task_id"] == "t2"
    assert manifest["systems"][0]["system_id"] == "bb_atp"
    assert manifest["systems"][1]["system_id"] == "hilbert"


def test_build_bundle_applies_canonical_task_override(tmp_path: Path) -> None:
    frozen = tmp_path / "frozen"
    (frozen / "mini").mkdir(parents=True)
    source_manifest = {
        "acceptance": {"determinism_reruns": 2, "required_fields": ["toolchain_id"]},
        "artifacts": {"root_dir": str(frozen / "mini")},
        "benchmark": {
            "name": "minif2f_v2",
            "slice": {"seed": 1337},
            "version": {"benchmark_git_sha": "abc", "dataset_sha256": "d" * 64},
        },
        "budget": {"class": "B"},
        "owner": "atp-team",
        "toolchain": {"lean_version": "4.12.0", "mathlib_commit": "unknown", "docker_image_digest": "local"},
        "systems": [{"system_id": "bb_atp", "config_ref": "agent_configs/demo.yaml"}],
    }
    task_inputs = {
        "benchmark": "minif2f_v2",
        "seed": 1337,
        "tasks": [
            {
                "task_id": "mathd_numbertheory_780",
                "input_text": (
                    "theorem mathd_numbertheory_780\n"
                    "  (m : ℕ)\n"
                    "  (h₂ : ∃ (x:ZMod m), x = 6⁻¹)\n"
                    "  (h₃ : x ≡ 6^2 [MOD m]) :\n"
                    "  m = 43 := by\n  sorry\n"
                ),
                "input_mode": "formal_lean",
                "input_hash": "h2",
            }
        ],
    }
    source_manifest_path = frozen / "mini" / "cross_system_manifest.json"
    source_task_inputs_path = frozen / "mini" / "aristotle_task_inputs.json"
    source_manifest_path.write_text(json.dumps(source_manifest), encoding="utf-8")
    source_task_inputs_path.write_text(json.dumps(task_inputs), encoding="utf-8")

    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    pack_manifest_path = pack_dir / "manifest.json"
    pack_manifest = {
        "pack_name": "demo_pack",
        "benchmark_slug": "mini",
        "source_task_inputs": str(source_task_inputs_path),
        "tasks": [{"task_id": "mathd_numbertheory_780"}],
    }
    pack_manifest_path.write_text(json.dumps(pack_manifest), encoding="utf-8")

    import scripts.build_hilbert_bb_comparison_bundle_v1 as mod
    mod.FROZEN_ROOT = frozen
    build_bundle(pack_manifest_path)
    bb_tasks = json.loads((pack_dir / "bb_task_inputs.json").read_text())
    assert "(m x : ℤ)" in bb_tasks["tasks"][0]["input_text"]


def test_build_bundle_accepts_prebuilt_v2_pack_dir(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack_v2"
    pack_dir.mkdir()
    pack_manifest_path = pack_dir / "pack_metadata.json"
    pack_manifest_path.write_text(json.dumps({"pack_name": "pack_b_core_noimo_minif2f_v1"}), encoding="utf-8")
    bb_task_inputs_path = pack_dir / "bb_task_inputs.json"
    bundle_manifest_path = pack_dir / "cross_system_manifest.json"
    bb_task_inputs_path.write_text(json.dumps({"tasks": []}), encoding="utf-8")
    bundle_manifest_path.write_text(json.dumps({"systems": []}), encoding="utf-8")

    payload = build_bundle(pack_manifest_path)

    assert payload["pack_manifest_path"].endswith("pack_metadata.json")
    assert payload["bb_task_inputs_path"].endswith("bb_task_inputs.json")
    assert payload["cross_system_manifest_path"].endswith("cross_system_manifest.json")
    assert payload["bundle_mode"] == "prebuilt_v2_compat"


def test_write_summary_allows_caller_owned_output_path(tmp_path: Path) -> None:
    pack_a = tmp_path / "a" / "pack_metadata.json"
    pack_b = tmp_path / "b" / "pack_metadata.json"
    for p, name in ((pack_a, "pack_b_core_noimo_minif2f_v1"), (pack_b, "pack_b_medium_noimo530_minif2f_v1")):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"pack_name": name}), encoding="utf-8")
        (p.parent / "bb_task_inputs.json").write_text(json.dumps({"tasks": []}), encoding="utf-8")
        (p.parent / "cross_system_manifest.json").write_text(json.dumps({"systems": []}), encoding="utf-8")

    out_summary = tmp_path / "owned" / "summary.json"
    result = write_summary([pack_a, pack_b], out_summary)
    payload = json.loads(out_summary.read_text(encoding="utf-8"))

    assert result == out_summary
    assert payload["schema"] == "breadboard.hilbert_bb_comparison_bundle_summary.v1"
    assert len(payload["generated"]) == 2
    assert payload["generated"][0]["bundle_mode"] == "prebuilt_v2_compat"
