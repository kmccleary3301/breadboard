from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_bb_formal_pack_v1.py"
sys.path.insert(0, str(MODULE_PATH.parent))
spec = importlib.util.spec_from_file_location("run_bb_formal_pack_v1", MODULE_PATH)
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_numbertheory_2dvd4expn_prompt_includes_task_guidance() -> None:
    prompt = runner._build_prompt(
        "numbertheory_2dvd4expn",
        "import Mathlib\n\ntheorem numbertheory_2dvd4expn (n : ℕ) (h₀ : n ≠ 0) : 2 ∣ 4^n := by\n  sorry\n",
    )

    assert "pow_dvd_pow_of_dvd" in prompt
    assert "dvd_trans h₄ h₂" in prompt
    assert "Do not stop at `simp`" in prompt


def test_mathd_numbertheory_530_prompt_includes_normalization_guidance() -> None:
    prompt = runner._build_prompt(
        "mathd_numbertheory_530",
        "import Mathlib\n\ntheorem mathd_numbertheory_530 : True := by\n  trivial\n",
    )

    assert "Nat.exists_coprime'" in prompt
    assert "Do not use the broken `b = 1` route" in prompt
    assert "it gives `n = a * g` and `k = b * g`" in prompt
    assert "hb_ge_two : 2 ≤ b" in prompt
    assert "b = 2 ∨ 3 ≤ b" in prompt
    assert "22 ≤ a * b" in prompt
    assert "simpa [Nat.cast_mul]" in prompt
    assert "(lt_div_iff hbg_pos_real).mp hgt'" in prompt
    assert "Nat.lcm_mul_right" in prompt
    assert "Nat.gcd_mul_right" in prompt
    assert "Nat.mul_div_right (a * b) hg0" in prompt
    assert "Nat.coprime_mul_lcm_eq_left" in prompt


def test_mathd_algebra_156_prompt_includes_case_split_guidance() -> None:
    prompt = runner._build_prompt(
        "mathd_algebra_156",
        "import Mathlib\n\ntheorem mathd_algebra_156 : True := by\n  trivial\n",
    )

    assert "Do not use `rfl` to case-split" in prompt
    assert "rcases hx_sq with hx2 | hx3" in prompt
    assert "Finish every branch with `nlinarith" in prompt


def test_numbertheory_exk2pow_prompt_includes_prime_power_guidance() -> None:
    prompt = runner._build_prompt(
        "numbertheory_exk2powkeqapb2mulbpa2_aeq1",
        "import Mathlib\n\ntheorem numbertheory_exk2powkeqapb2mulbpa2_aeq1 : True := by\n  trivial\n",
    )

    assert "(Nat.dvd_prime_pow Nat.prime_two).mp hu_dvd" in prompt
    assert "simpa [u, v] using hk" in prompt
    assert "u - v = (b - a) * (a + b - 1)" in prompt
    assert "obtain ⟨t, ht⟩ := Nat.exists_eq_add_of_lt hnm_lt" in prompt
    assert "have hu_eq_mul : u = v * 2^(t + 1) := by" in prompt
    assert "use `exact hu_eq_mul`, not `hu_eq_mul.symm`" in prompt
    assert "exact (Nat.not_lt_of_ge hv_le) hltv" in prompt
    assert "Do not use `sq_lt_sq.mpr` on naturals" in prompt
    assert "have ha2_lt_hb2 : a^2 < b^2 := by gcongr" in prompt
    assert "set d : ℕ := b - a" in prompt
    assert "dsimp [u, v]" in prompt
    assert "Important: unfold u,v before rewriting b" in prompt
    assert "rw [hb]" in prompt
    assert "change (a + (a + d)^2) - (a + d + a^2) = d * (a + (a + d) - 1)" in prompt
    assert "have hcancel : d * (a + (a + d) - 1) + d = d * (a + (a + d)) := by" in prompt
    assert "have hrew : d * (a + (a + d) - 1) + (a + d + a^2) =" in prompt
    assert "simpa [Nat.add_assoc, Nat.add_left_comm, Nat.add_comm] using hrew" in prompt
    assert "have hsub_pos : 0 < u - v := by rw [hsub_eq]; exact Nat.mul_pos hba_pos hsum_pos" in prompt
    assert "have hv_lt_u : v < u := Nat.lt_of_sub_pos hsub_pos" in prompt
    assert "have hsub_eq : v - u = (a - b) * (a + b - 1) := by" in prompt
    assert "set d : ℕ := a - b" in prompt
    assert "have ha : a = b + d := by dsimp [d]; exact (Nat.add_sub_of_le (Nat.le_of_lt hgt)).symm" in prompt
    assert "change (b + (b + d)^2) - (b + d + b^2) = d * (b + d + b - 1)" in prompt
    assert "have hcancel : d * (b + d + b - 1) + d = d * (b + d + b) := by" in prompt
    assert "have hmain : d * (b + d + b - 1) + (b + d + b^2) = d * (b + d + b) + (b + b^2) := by" in prompt
    assert "rw [hmain]" in prompt
    assert "exact Nat.mul_comm a (a + 1)" in prompt
    assert "Do not rebuild the evenness witnesses manually" in prompt
    assert "simp [pow_succ, even_iff_two_dvd]" in prompt
    assert "exact Nat.le_add_right b (a^2)" in prompt
    assert "prove `b - a < b` by `Nat.sub_lt hb0 ha0`" in prompt
    assert "Handle `b < a` symmetrically with the corrected sign convention" in prompt
    assert "Avoid introducing `htpos : 0 < t`" in prompt
    assert "Prefer a short contradiction proof" in prompt


def test_other_tasks_do_not_get_numbertheory_specific_hint() -> None:
    prompt = runner._build_prompt(
        "mathd_algebra_171",
        "import Mathlib\n\ntheorem mathd_algebra_171 : True := by\n  trivial\n",
    )

    assert "pow_dvd_pow_of_dvd" not in prompt
    assert "Do not use `rfl` to case-split" not in prompt


def test_prompt_includes_repair_seed_and_error_context() -> None:
    prompt = runner._build_prompt(
        "mathd_algebra_171",
        "import Mathlib\n\ntheorem mathd_algebra_171 : True := by\n  trivial\n",
        prior_candidate="theorem mathd_algebra_171 : True := by\n  simp",
        prior_error="unsolved goals\nx : ℕ\n⊢ True",
    )

    assert "Previous near-miss proof to repair instead of restarting from scratch" in prompt
    assert "Most relevant Lean errors from the previous attempt" in prompt
    assert "theorem mathd_algebra_171 : True := by" in prompt
    assert "unsolved goals" in prompt


def test_load_repair_seed_handles_missing_and_present_files() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        seed_dir = Path(tmpdir)
        assert runner._load_repair_seed(seed_dir, "missing_task", ".lean") is None

        proof_path = seed_dir / "mathd_algebra_171.lean"
        proof_path.write_text("theorem mathd_algebra_171 : True := by\n  trivial\n", encoding="utf-8")

        loaded = runner._load_repair_seed(seed_dir, "mathd_algebra_171", ".lean")
        assert loaded is not None
        assert "trivial" in loaded


def test_workspace_root_is_forced_under_tmp() -> None:
    workspace = runner._workspace_root("hilbert-compare/pack:b", "mathd_algebra_156")

    assert str(workspace).startswith(str(runner.REPO_ROOT / "tmp"))
    assert workspace.parts[-2] == "hilbert-compare_pack_b"
    assert workspace.parts[-1] == "mathd_algebra_156"
