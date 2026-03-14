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


def test_mathd_numbertheory_728_unsound_description_is_present() -> None:
    reason = packs.EXCLUDED_TASKS["mathd_numbertheory_728"]
    assert "(29^13 - 5^13) % 7 = 3" in reason
    assert "not 0" in reason


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


def test_pack_d_induction_core_tasks(tmp_path: Path) -> None:
    summary = packs.build_pack("pack_d_induction_core_minif2f_v1", tmp_path)

    assert summary["task_count"] == 2
    assert summary["task_ids"] == [
        "induction_sumkexp3eqsumksq",
        "induction_12dvd4expnp1p20",
    ]
    assert summary["excluded_tasks"] == []

    metadata = json.loads((tmp_path / "pack_d_induction_core_minif2f_v1" / "pack_metadata.json").read_text())
    assert metadata["requested_task_ids"] == summary["task_ids"]
    assert metadata["included_task_ids"] == summary["task_ids"]


def test_pack_d_numbertheory_core_tasks(tmp_path: Path) -> None:
    summary = packs.build_pack("pack_d_numbertheory_core_minif2f_v1", tmp_path)

    assert summary["task_count"] == 4
    assert summary["task_ids"] == [
        "imo_1959_p1",
        "numbertheory_2pownm1prime_nprime",
        "mathd_numbertheory_427",
        "mathd_algebra_452",
    ]
    assert summary["excluded_tasks"] == []

    metadata = json.loads((tmp_path / "pack_d_numbertheory_core_minif2f_v1" / "pack_metadata.json").read_text())
    assert metadata["requested_task_ids"] == summary["task_ids"]
    assert metadata["included_task_ids"] == summary["task_ids"]


def test_pack_e_algebra_core_tasks(tmp_path: Path) -> None:
    summary = packs.build_pack("pack_e_algebra_core_minif2f_v1", tmp_path)

    assert summary["task_count"] == 5
    assert summary["task_ids"] == [
        "mathd_algebra_48",
        "mathd_algebra_101",
        "mathd_algebra_410",
        "mathd_algebra_73",
        "mathd_algebra_131",
    ]
    assert [item["task_id"] for item in summary["excluded_tasks"]] == ["mathd_algebra_77"]

    metadata = json.loads((tmp_path / "pack_e_algebra_core_minif2f_v1" / "pack_metadata.json").read_text())
    assert metadata["requested_task_ids"] == [
        "mathd_algebra_48",
        "mathd_algebra_101",
        "mathd_algebra_410",
        "mathd_algebra_73",
        "mathd_algebra_77",
        "mathd_algebra_131",
    ]
    assert metadata["included_task_ids"] == summary["task_ids"]


def test_pack_e_algebra_focus_tasks(tmp_path: Path) -> None:
    summary = packs.build_pack("pack_e_algebra_focus_minif2f_v1", tmp_path)

    assert summary["task_count"] == 3
    assert summary["task_ids"] == [
        "mathd_algebra_48",
        "mathd_algebra_73",
        "mathd_algebra_131",
    ]
    assert [item["task_id"] for item in summary["excluded_tasks"]] == ["mathd_algebra_77"]

    metadata = json.loads((tmp_path / "pack_e_algebra_focus_minif2f_v1" / "pack_metadata.json").read_text())
    assert metadata["requested_task_ids"] == [
        "mathd_algebra_48",
        "mathd_algebra_73",
        "mathd_algebra_77",
        "mathd_algebra_131",
    ]
    assert metadata["included_task_ids"] == summary["task_ids"]


def test_pack_f_discrete_arithmetic_mix_tasks(tmp_path: Path) -> None:
    summary = packs.build_pack("pack_f_discrete_arithmetic_mix_minif2f_v1", tmp_path)

    assert summary["task_count"] == 6
    assert summary["task_ids"] == [
        "amc12a_2015_p10",
        "aime_1991_p1",
        "amc12a_2008_p4",
        "amc12_2001_p9",
        "mathd_numbertheory_48",
        "mathd_numbertheory_33",
    ]
    assert summary["excluded_tasks"] == []

    metadata = json.loads((tmp_path / "pack_f_discrete_arithmetic_mix_minif2f_v1" / "pack_metadata.json").read_text())
    assert metadata["requested_task_ids"] == summary["task_ids"]
    assert metadata["included_task_ids"] == summary["task_ids"]


def test_pack_g_arithmetic_sanity_tasks(tmp_path: Path) -> None:
    summary = packs.build_pack("pack_g_arithmetic_sanity_minif2f_v1", tmp_path)

    assert summary["task_count"] == 6
    assert summary["task_ids"] == [
        "mathd_numbertheory_3",
        "mathd_numbertheory_12",
        "mathd_numbertheory_237",
        "mathd_numbertheory_299",
        "mathd_numbertheory_353",
        "mathd_numbertheory_430",
    ]
    assert summary["excluded_tasks"] == []

    metadata = json.loads((tmp_path / "pack_g_arithmetic_sanity_minif2f_v1" / "pack_metadata.json").read_text())
    assert metadata["requested_task_ids"] == summary["task_ids"]
    assert metadata["included_task_ids"] == summary["task_ids"]


def test_pack_h_modular_closedform_tasks(tmp_path: Path) -> None:
    summary = packs.build_pack("pack_h_modular_closedform_minif2f_v1", tmp_path)

    assert summary["task_count"] == 6
    assert summary["task_ids"] == [
        "mathd_numbertheory_5",
        "mathd_numbertheory_24",
        "mathd_numbertheory_45",
        "mathd_numbertheory_66",
        "mathd_numbertheory_99",
        "mathd_numbertheory_109",
    ]
    assert summary["excluded_tasks"] == []

    metadata = json.loads((tmp_path / "pack_h_modular_closedform_minif2f_v1" / "pack_metadata.json").read_text())
    assert metadata["requested_task_ids"] == summary["task_ids"]
    assert metadata["included_task_ids"] == summary["task_ids"]


def test_pack_i_divisors_modmix_tasks(tmp_path: Path) -> None:
    summary = packs.build_pack("pack_i_divisors_modmix_minif2f_v1", tmp_path)

    assert summary["task_count"] == 6
    assert summary["task_ids"] == [
        "mathd_numbertheory_127",
        "mathd_numbertheory_149",
        "mathd_numbertheory_169",
        "mathd_numbertheory_185",
        "mathd_numbertheory_221",
        "mathd_numbertheory_233",
    ]
    assert summary["excluded_tasks"] == []

    metadata = json.loads((tmp_path / "pack_i_divisors_modmix_minif2f_v1" / "pack_metadata.json").read_text())
    assert metadata["requested_task_ids"] == summary["task_ids"]
    assert metadata["included_task_ids"] == summary["task_ids"]


def test_pack_j_residue_gcd_mix_tasks(tmp_path: Path) -> None:
    summary = packs.build_pack("pack_j_residue_gcd_mix_minif2f_v1", tmp_path)

    assert summary["task_count"] == 6
    assert summary["task_ids"] == [
        "mathd_numbertheory_34",
        "mathd_numbertheory_100",
        "mathd_numbertheory_212",
        "mathd_numbertheory_239",
        "mathd_numbertheory_254",
        "mathd_numbertheory_320",
    ]
    assert summary["excluded_tasks"] == []

    metadata = json.loads((tmp_path / "pack_j_residue_gcd_mix_minif2f_v1" / "pack_metadata.json").read_text())
    assert metadata["requested_task_ids"] == summary["task_ids"]
    assert metadata["included_task_ids"] == summary["task_ids"]


def test_pack_m_boundary_olympiad_mix_tasks(tmp_path: Path) -> None:
    summary = packs.build_pack("pack_m_boundary_olympiad_mix_minif2f_v1", tmp_path)

    assert summary["task_count"] == 4
    assert summary["task_ids"] == [
        "imo_1960_p2",
        "imo_1963_p5",
        "induction_nfactltnexpnm1ngt3",
        "numbertheory_fxeq4powxp6powxp9powx_f2powmdvdf2pown",
    ]
    assert summary["excluded_tasks"] == []

    metadata = json.loads((tmp_path / "pack_m_boundary_olympiad_mix_minif2f_v1" / "pack_metadata.json").read_text())
    assert metadata["requested_task_ids"] == summary["task_ids"]
    assert metadata["included_task_ids"] == summary["task_ids"]


def test_real_namespace_is_canonicalized() -> None:
    original = "theorem t : real.cos x = real.sqrt y := by\n"
    rewritten = packs._canonicalize_formal_statement("dummy", original)
    assert "Real.cos" in rewritten
    assert "Real.sqrt" in rewritten


def test_pack_k_moddigit_closedform_tasks(tmp_path: Path) -> None:
    summary = packs.build_pack("pack_k_moddigit_closedform_minif2f_v1", tmp_path)

    assert summary["task_count"] == 5
    assert summary["task_ids"] == [
        "mathd_numbertheory_1124",
        "mathd_numbertheory_293",
        "mathd_numbertheory_328",
        "mathd_numbertheory_175",
        "mathd_numbertheory_769",
    ]
    assert summary["excluded_tasks"] == [
        {"task_id": "mathd_numbertheory_728", "reason": packs.EXCLUDED_TASKS["mathd_numbertheory_728"]},
    ]

    metadata = json.loads((tmp_path / "pack_k_moddigit_closedform_minif2f_v1" / "pack_metadata.json").read_text())
    assert metadata["requested_task_ids"] == [
        "mathd_numbertheory_1124",
        "mathd_numbertheory_293",
        "mathd_numbertheory_328",
        "mathd_numbertheory_175",
        "mathd_numbertheory_728",
        "mathd_numbertheory_769",
    ]
    assert metadata["included_task_ids"] == summary["task_ids"]


def test_pack_l_algebra_linear_equiv_tasks(tmp_path: Path) -> None:
    summary = packs.build_pack("pack_l_algebra_linear_equiv_minif2f_v1", tmp_path)

    assert summary["task_count"] == 6
    assert summary["task_ids"] == [
        "mathd_algebra_141",
        "mathd_algebra_209",
        "mathd_algebra_33",
        "mathd_algebra_398",
        "mathd_algebra_459",
        "mathd_algebra_137",
    ]
    assert summary["excluded_tasks"] == []

    metadata = json.loads((tmp_path / "pack_l_algebra_linear_equiv_minif2f_v1" / "pack_metadata.json").read_text())
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


def test_complex_namespace_is_canonicalized() -> None:
    statement = (
        "theorem mathd_algebra_48\n"
        "  (q e : ℂ)\n"
        "  (h₀ : q = 9 - 4 * complex.I)\n"
        "  (h₁ : e = -3 - 4 * complex.I) : q - e = 12 := by\n"
    )

    canonical = packs._canonicalize_formal_statement("mathd_algebra_48", statement)

    assert "Complex.I" in canonical
    assert "complex.I" not in canonical


def test_zmod_namespace_is_canonicalized() -> None:
    statement = (
        "theorem sample\n"
        "  (b : zmod (11^2))\n"
        "  (h₀ : b = 24⁻¹) : b = 116 := by\n"
    )

    canonical = packs._canonicalize_formal_statement("sample", statement)

    assert "ZMod" in canonical
    assert "zmod" not in canonical


def test_mathd_numbertheory_169_is_canonicalized_to_factorial_function() -> None:
    statement = (
        "theorem mathd_numbertheory_169 :\n"
        "  Nat.gcd 20! 200000 = 40000 := by\n"
    )

    canonical = packs._canonicalize_formal_statement("mathd_numbertheory_169", statement)

    assert "Nat.factorial 20" in canonical
    assert "20!" not in canonical


def test_standalone_finset_token_is_canonicalized() -> None:
    statement = (
        "theorem mathd_numbertheory_221\n"
        "  (S : finset ℕ)\n"
        "  (h₀ : ∀ (x : ℕ), x ∈ S ↔ 0 < x ∧ x < 1000 ∧ x.divisors.card = 3) :\n"
        "  S.card = 11 := by\n"
    )

    canonical = packs._canonicalize_formal_statement("mathd_numbertheory_221", statement)

    assert "(S : Finset ℕ)" in canonical


def test_equiv_and_rat_denom_are_canonicalized() -> None:
    statement = (
        "theorem sample\n"
        "  (σ : equiv ℝ ℝ)\n"
        "  (d : ℚ) : ↑d.denom + d.num = 28 := by\n"
    )

    canonical = packs._canonicalize_formal_statement("sample", statement)

    assert "(σ : Equiv ℝ ℝ)" in canonical
    assert ".denom" not in canonical
    assert ".den" in canonical
