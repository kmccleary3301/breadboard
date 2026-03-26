from __future__ import annotations

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
