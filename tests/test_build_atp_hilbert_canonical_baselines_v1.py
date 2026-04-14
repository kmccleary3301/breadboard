from __future__ import annotations

import json
import importlib.util
import sys
from pathlib import Path

from atp_hilbert_fixture_utils import install_canonical_baseline_fixture

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_atp_hilbert_canonical_baselines_v1.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("build_atp_hilbert_canonical_baselines_v1", MODULE_PATH)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_canonical_baseline_payload_has_expected_entries(tmp_path: Path) -> None:
    install_canonical_baseline_fixture(module, tmp_path)
    payload = module.build_payload()
    entries = payload["entries"]
    assert payload["schema"] == "breadboard.atp_hilbert_canonical_baselines.v1"
    assert payload["candidate_system"] == "bb_hilbert_like"
    assert payload["baseline_system"] == "hilbert_roselab"
    assert payload["entry_count"] == len(entries)
    assert payload["entry_count"] >= 10
    pack_ids = {entry["pack_id"] for entry in entries}
    assert "pack_b_core_noimo_minif2f_v1" in pack_ids
    assert "pack_j_residue_gcd_mix_minif2f_v1" in pack_ids


def test_canonical_baseline_payload_entries_reference_existing_files(tmp_path: Path) -> None:
    repo_root = tmp_path
    install_canonical_baseline_fixture(module, repo_root)
    payload = module.build_payload()
    for entry in payload["entries"]:
        assert (repo_root / entry["status_doc"]).exists()
        assert (repo_root / entry["report"]).exists()
        assert (repo_root / entry["validation"]).exists()
        assert entry["candidate_solved"] >= entry["candidate_only"]
        assert entry["baseline_solved"] >= entry["baseline_only"]


def test_canonical_baseline_preflight_payload_classifies_missing_paths(tmp_path: Path) -> None:
    module.REPO_ROOT = tmp_path
    payload = module.build_preflight_payload()

    assert payload["schema"] == "breadboard.atp_hilbert_canonical_baselines_preflight.v1"
    assert payload["entry_count"] == len(module.CANONICAL_BASELINES)
    assert payload["missing_count"] == payload["entry_count"]
    assert payload["ready_count"] == 0
    assert all(entry["missing_paths"] for entry in payload["entries"])


def test_canonical_baseline_payload_can_skip_missing_entries(tmp_path: Path) -> None:
    install_canonical_baseline_fixture(module, tmp_path)
    first = module.CANONICAL_BASELINES[0]
    missing_report = tmp_path / first["report"]
    missing_report.unlink()

    payload = module.build_payload(allow_missing=True)

    pack_ids = {entry["pack_id"] for entry in payload["entries"]}
    assert first["pack_id"] not in pack_ids
    assert payload["entry_count"] == len(payload["entries"])
    assert payload["entry_count"] == len(module.CANONICAL_BASELINES) - 1


def test_bounded_live_publication_payload_writes_one_pack_slice(tmp_path: Path) -> None:
    module.REPO_ROOT = tmp_path
    pack_manifest = tmp_path / "pack_metadata.json"
    cross_system_manifest = tmp_path / "cross_system_manifest.json"
    bundle_summary = tmp_path / "bundle_summary.json"
    status_doc = tmp_path / "published" / "status.md"
    report_out = tmp_path / "published" / "report.json"
    validation_out = tmp_path / "published" / "validation.json"

    pack_manifest.write_text(
        '{"pack_name": "pack_b_core_noimo_minif2f_v1", "included_task_ids": ["t1", "t2"], "excluded_tasks": [{"task_id": "bad"}]}'
    )
    cross_system_manifest.write_text(
        '{"run_id": "bounded-live", "systems": [{"system_id": "bb_hilbert_like"}, {"system_id": "hilbert_roselab"}], "benchmark": {"slice": {"n_tasks": 2}}}'
    )
    bundle_summary.write_text(
        json.dumps(
            {
                "generated": [
                    {
                        "pack_manifest_path": str(pack_manifest.resolve()),
                        "bundle_mode": "prebuilt_v2_compat",
                    }
                ]
            }
        )
    )

    payload = module.build_bounded_live_publication_payload(
        pack_manifest_path=pack_manifest,
        cross_system_manifest_path=cross_system_manifest,
        bundle_summary_path=bundle_summary,
        status_doc_out=status_doc,
        report_out=report_out,
        validation_out=validation_out,
    )

    assert payload["schema"] == "breadboard.atp_hilbert_canonical_baselines_bounded_live.v1"
    assert payload["entry_count"] == 1
    entry = payload["entries"][0]
    assert entry["pack_id"] == "pack_b_core_noimo_minif2f_v1"
    assert entry["task_count"] == 2
    assert entry["metrics_status"] == "artifact_only_live_publication"
    assert status_doc.exists()
    assert report_out.exists()
    assert validation_out.exists()
