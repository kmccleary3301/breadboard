from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


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
    assert "v - u = (b - a) * (a + b - 1)" in prompt
    assert "Prefer a short contradiction proof" in prompt


def test_other_tasks_do_not_get_numbertheory_specific_hint() -> None:
    prompt = runner._build_prompt(
        "mathd_algebra_171",
        "import Mathlib\n\ntheorem mathd_algebra_171 : True := by\n  trivial\n",
    )

    assert "pow_dvd_pow_of_dvd" not in prompt
    assert "Do not use `rfl` to case-split" not in prompt


def test_workspace_root_is_forced_under_tmp() -> None:
    workspace = runner._workspace_root("hilbert-compare/pack:b", "mathd_algebra_156")

    assert str(workspace).startswith(str(runner.REPO_ROOT / "tmp"))
    assert workspace.parts[-2] == "hilbert-compare_pack_b"
    assert workspace.parts[-1] == "mathd_algebra_156"
