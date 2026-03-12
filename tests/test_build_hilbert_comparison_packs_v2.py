from __future__ import annotations

import importlib.util
import json
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build_hilbert_comparison_packs_v2.py"
import sys

sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("build_hilbert_comparison_packs_v2", MODULE_PATH)
assert spec and spec.loader
packs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packs)


def test_known_unsound_task_is_excluded_from_medium_pack(tmp_path: Path) -> None:
    summary = packs.build_pack("pack_b_medium_noimo530_minif2f_v1", tmp_path)

    assert "mathd_numbertheory_780" not in summary["task_ids"]
    assert "aime_1984_p5" not in summary["task_ids"]
    assert "amc12a_2019_p12" not in summary["task_ids"]
    assert summary["task_count"] == 5
    assert summary["excluded_tasks"] == [
        {"task_id": "mathd_numbertheory_780", "reason": packs.EXCLUDED_TASKS["mathd_numbertheory_780"]},
        {"task_id": "aime_1984_p5", "reason": packs.EXCLUDED_TASKS["aime_1984_p5"]},
        {"task_id": "amc12a_2019_p12", "reason": packs.EXCLUDED_TASKS["amc12a_2019_p12"]},
    ]

    metadata = json.loads((tmp_path / "pack_b_medium_noimo530_minif2f_v1" / "pack_metadata.json").read_text())
    assert metadata["requested_task_ids"][0] == "mathd_numbertheory_780"
    assert metadata["included_task_ids"] == summary["task_ids"]
    assert metadata["excluded_tasks"] == summary["excluded_tasks"]


def test_known_unsound_counterexample_description_is_present() -> None:
    reason = packs.EXCLUDED_TASKS["mathd_numbertheory_780"]
    assert "m=11" in reason
    assert "x=2" in reason
    assert "Nat subtraction truncates" in reason


def test_aime_unsound_counterexample_description_is_present() -> None:
    reason = packs.EXCLUDED_TASKS["aime_1984_p5"]
    assert "a = -64, b = 8" in reason
    assert "a * b = -512" in reason


def test_amc_unsound_counterexample_description_is_present() -> None:
    reason = packs.EXCLUDED_TASKS["amc12a_2019_p12"]
    assert "2^(3 + sqrt 5)" in reason
    assert "2 * sqrt 5" in reason


def test_imo_stress_pack_is_single_valid_task(tmp_path: Path) -> None:
    summary = packs.build_pack("pack_c_imo1977_p6_stress_minif2f_v1", tmp_path)

    assert summary["task_count"] == 1
    assert summary["task_ids"] == ["imo_1977_p6"]
    assert summary["excluded_tasks"] == []

    metadata = json.loads((tmp_path / "pack_c_imo1977_p6_stress_minif2f_v1" / "pack_metadata.json").read_text())
    assert metadata["requested_task_ids"] == ["imo_1977_p6"]
    assert metadata["included_task_ids"] == ["imo_1977_p6"]


def test_pack_d_mixed_induction_numbertheory_tasks(tmp_path: Path) -> None:
    summary = packs.build_pack("pack_d_mixed_induction_numbertheory_minif2f_v1", tmp_path)

    assert summary["task_count"] == 6
    assert summary["task_ids"] == [
        "imo_1959_p1",
        "induction_sumkexp3eqsumksq",
        "induction_12dvd4expnp1p20",
        "numbertheory_2pownm1prime_nprime",
        "mathd_numbertheory_427",
        "mathd_algebra_452",
    ]
    assert summary["excluded_tasks"] == []

    metadata = json.loads((tmp_path / "pack_d_mixed_induction_numbertheory_minif2f_v1" / "pack_metadata.json").read_text())
    assert metadata["requested_task_ids"] == summary["task_ids"]
    assert metadata["included_task_ids"] == summary["task_ids"]


def test_legacy_nat_and_finset_names_are_canonicalized() -> None:
    statement = (
        "theorem sample\n"
        "  (a : ℕ)\n"
        "  (h₀ : a = (∑ k in (nat.divisors 500), k)) :\n"
        "  ∑ k in finset.filter (λ x, nat.prime x) (nat.divisors a) = nat.gcd a 1 := by\n"
    )

    canonical = packs._canonicalize_formal_statement("sample", statement)

    assert "nat.divisors" not in canonical
    assert "nat.prime" not in canonical
    assert "nat.gcd" not in canonical
    assert "finset.filter" not in canonical
    assert "Nat.divisors" in canonical
    assert "Nat.Prime" in canonical
    assert "Nat.gcd" in canonical
    assert "Finset.filter" in canonical
    assert "λ x," not in canonical
    assert "fun x =>" in canonical
