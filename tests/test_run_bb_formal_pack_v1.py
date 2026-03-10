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


def test_other_tasks_do_not_get_numbertheory_specific_hint() -> None:
    prompt = runner._build_prompt(
        "mathd_algebra_171",
        "import Mathlib\n\ntheorem mathd_algebra_171 : True := by\n  trivial\n",
    )

    assert "pow_dvd_pow_of_dvd" not in prompt
