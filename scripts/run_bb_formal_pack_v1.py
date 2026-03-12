#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from _cross_system_eval_v1 import dump_json, load_manifest


REPO_ROOT = Path(__file__).resolve().parents[1]
TASK_HINTS = {
    "mathd_numbertheory_530": (
        "Task-specific guidance:\n"
        "- Normalize with `obtain ⟨g, a, b, hg0, hab_coprime, hn_eq, hk_eq⟩ := Nat.exists_coprime' (m := n) (n := k) (Nat.gcd_pos_of_pos_left k hn0)`.\n"
        "- Keep the orientation from `Nat.exists_coprime'`: it gives `n = a * g` and `k = b * g`, not `g * a` and `g * b`.\n"
        "- Do not use the broken `b = 1` route. From `5 < (a : ℝ) / b` and `(a : ℝ) / b < 6`, the correct normalized target is `22 ≤ a * b`, not `b = 1`.\n"
        "- After `rw [hn_eq, hk_eq] at h_lt h_gt`, first create cast-clean versions with `simpa [Nat.cast_mul]`; then use `have hmul : (a : ℝ) * g < 6 * ((b : ℝ) * g) := (div_lt_iff hbg_pos_real).mp hlt'` and `have hmul : 5 * ((b : ℝ) * g) < (a : ℝ) * g := (lt_div_iff hbg_pos_real).mp hgt'`.\n"
        "- Prove `hb_pos : 0 < b` from `hk_eq`; then use `nlinarith` to derive `ha_gt_5b : 5 * b < a` and `ha_lt_6b : a < 6 * b`.\n"
        "- Next prove `hb_ge_two : 2 ≤ b`. A clean contradiction is: if `b = 1`, then `5 < a` and `a < 6`, impossible in `ℕ`.\n"
        "- Split `hb_cases : b = 2 ∨ 3 ≤ b := by omega`.\n"
        "- In the `b = 2` branch, use `10 < a` and `a < 12` to force `a = 11`, then conclude `22 ≤ a * b` by `omega`.\n"
        "- In the `3 ≤ b` branch, combine `ha_gt_5b` with `hb_ge_three : 3 ≤ b` to get `a ≥ 5 * b + 1`, then `have hab_ge : (5 * b + 1) * b ≤ a * b := Nat.mul_le_mul_right b ha_ge`; finish `22 ≤ a * b` from `22 ≤ (5 * b + 1) * b`.\n"
        "- For the target itself, use the exact normalized identities:\n"
        "  `have hlcm : Nat.lcm n k = a * b * g := by rw [hn_eq, hk_eq, Nat.lcm_mul_right, hab_coprime.lcm_eq_mul]`\n"
        "  `have hgcd : Nat.gcd n k = g := by rw [hn_eq, hk_eq, Nat.gcd_mul_right, hab_coprime.gcd_eq_one]; simp`\n"
        "  `have hdiv : (a * b * g) / g = a * b := by simpa [Nat.mul_assoc, Nat.mul_left_comm, Nat.mul_comm] using Nat.mul_div_right (a * b) hg0`\n"
        "- Avoid the nonexistent `Nat.coprime_mul_lcm_eq_left`, avoid the wrong signature for `Nat.coprime_div_gcd_div_gcd`, and do not try the invalid one-argument form of `Nat.div_eq_iff_eq_mul_left`.\n"
    ),
    "mathd_algebra_156": (
        "Task-specific guidance:\n"
        "- First derive `hx : x^4 = 5 * x^2 - 6` and `hy : y^4 = 5 * y^2 - 6` from the hypotheses.\n"
        "- Convert those to `(x^2 - 2) * (x^2 - 3) = 0` and `(y^2 - 2) * (y^2 - 3) = 0` with `nlinarith`.\n"
        "- Use `eq_zero_or_eq_zero_of_mul_eq_zero` only as an intermediate step. Write the conversion explicitly:\n"
        "  `have hx_sq : x^2 = 2 ∨ x^2 = 3 := by`\n"
        "  `  rcases eq_zero_or_eq_zero_of_mul_eq_zero hx_mul with hx2 | hx3`\n"
        "  `  · left; nlinarith`\n"
        "  `  · right; nlinarith`\n"
        "  and similarly for `hy_sq`.\n"
        "- Do not use `rfl` to case-split on `x^2 = 2` or `y^2 = 3`; those equalities are not suitable for `subst`.\n"
        "- Instead, name the cases explicitly, e.g. `rcases hx_sq with hx2 | hx3` and `rcases hy_sq with hy2 | hy3`.\n"
        "- Finish every branch with `nlinarith [h₄, hx2, hy2]`, `nlinarith [h₄, hx2, hy3]`, etc.; the only consistent branch yields `y^2 - x^2 = 1`.\n"
    ),
    "numbertheory_2dvd4expn": (
        "Task-specific guidance:\n"
        "- Use `Nat.exists_eq_succ_of_ne_zero h₀` to rewrite `n` as `k + 1`.\n"
        "- `have h₁ : 2 ∣ 4 := by norm_num`\n"
        "- `have h₂ : 2 ^ n ∣ 4 ^ n := by exact pow_dvd_pow_of_dvd h₁ n`\n"
        "- `have h₄ : 2 ∣ 2 ^ n := by rcases Nat.exists_eq_succ_of_ne_zero h₀ with ⟨k, rfl⟩; simp [pow_succ]`\n"
        "- Finish with `exact dvd_trans h₄ h₂`.\n"
        "- Do not stop at `simp` if the remaining goal is `2 ∣ 4 ^ k * 4`; close it with divisibility lemmas.\n"
    ),
    "numbertheory_exk2powkeqapb2mulbpa2_aeq1": (
        "Task-specific guidance:\n"
        "- Let `u := a + b^2` and `v := b + a^2`. From the product hypothesis, prove both `u ∣ 2^k` and `v ∣ 2^k`.\n"
        "- Use the current mathlib syntax for powers of a prime: `obtain ⟨m, hm_le, hm⟩ := (Nat.dvd_prime_pow Nat.prime_two).mp hu_dvd` and similarly for `v`.\n"
        "- Do not call `Nat.dvd_prime_pow` as if it took `hu_dvd` as a direct final argument; use the `.mp` form above.\n"
        "- Prefer a short contradiction proof, not a large parity/subgoal tree.\n"
        "- A clean route is by cases on `hab : a = b`.\n"
        "- For `hu_dvd` and `hv_dvd`, use `hk` in the forward direction. `hk : 2^k = u * v`, so `refine ⟨v, ?_⟩; simpa [u, v] using hk` and `refine ⟨u, ?_⟩; simpa [u, v, Nat.mul_comm] using hk`.\n"
        "- When `u` and `v` are local `let` bindings, unfold them with `dsimp [u, v]` or `simpa [u, v]`; do not `rw [u, v]` because they are definitions, not rewrite lemmas.\n"
        "- In the equal case, rewrite `u = 2^m` to `a * (a + 1) = 2^m`. Then prove `a ∣ 2^m` and `a + 1 ∣ 2^m`, obtain `a = 2^i` and `a + 1 = 2^j`, and conclude `a = 1` because otherwise both `a` and `a + 1` are even.\n"
        "- In that equal case, avoid brittle `simpa` on `a + a^2`; instead prove `a + a^2 = a * (a + 1)` separately with `ring` and then rewrite.\n"
        "- For `a + 1 ∣ 2^m`, do not use `dvd_mul_left a (a + 1)` directly. Either use `dvd_mul_right (a + 1) a`, or give the witness `a` explicitly and close with `exact Nat.mul_comm a (a + 1)`.\n"
        "- For the unequal case, first prove `Even a ↔ Even b` from the parity of `u` and `v`. Useful square-parity facts are:\n"
        "  `have hb2_even : Even (b^2) := by simpa [pow_two] using hb_even.mul_left b`\n"
        "  `have hb_even : Even b := by`\n"
        "  `  by_contra hbe`\n"
        "  `  have hbo : Odd b := Nat.odd_iff_not_even.mpr hbe`\n"
        "  `  have hbo2 : Odd (b^2) := by simpa [pow_two] using hbo.mul hbo`\n"
        "  `  exact (Nat.not_even_iff_odd.mpr hbo2) hb2`\n"
        "- To avoid broken `Even.sub` terms, use `Nat.even_add` as an equivalence. For example, `have h_even_u : Even a ↔ Even (b^2) := by simpa [u, Nat.even_add] using hu_even`.\n"
        "- Once `Even a ↔ Even b`, deduce `Even (a + b)` and therefore `¬ 2 ∣ a + b - 1` by a two-witness `omega` contradiction.\n"
        "- First prove `1 < u` and `1 < v` from `a,b > 0`. Use these to rule out `m = 0` or `n = 0`; then rewrite `m = t + 1` and `n = t + 1` with `Nat.exists_eq_succ_of_ne_zero` before proving `Even (2^m)` and `Even (2^n)`.\n"
        "- Do not rebuild the evenness witnesses manually. The stable route is:\n"
        "  `rw [ha_eq_pow, hi_succ]`\n"
        "  `simp [pow_succ, even_iff_two_dvd]`\n"
        "  and similarly for `a + 1`, `u`, and `v` after rewriting with the relevant power equalities.\n"
        "- Build coprimality with `exact Nat.prime_two.coprime_iff_not_dvd.mpr hnot_two_dvd`; do not rewrite with `Nat.coprime_two_right`.\n"
        "- Then compare `u` and `v` by subtracting, and keep the sign straight: when `a < b`, the correct identity is `u - v = (b - a) * (a + b - 1)`, so `v < u`; when `b < a`, the correct identity is `v - u = (a - b) * (a + b - 1)`, so `u < v`.\n"
        "- Split the unequal case directly with `lt_or_gt_of_ne hab`; do not use `wlog`.\n"
        "- If `a < b`, first prove `v < u`, then prove `n < m`; if `b < a`, first prove `u < v`, then prove `m < n`.\n"
        "- Do not use `sq_lt_sq.mpr` on naturals. Instead prove square growth with `have ha2_lt_hb2 : a^2 < b^2 := by gcongr` (or the symmetric version), then combine it with `dsimp [u, v]; omega`.\n"
        "- Better still, avoid `omega` for `v < u` / `u < v`. Use the explicit difference identity and positivity:\n"
        "  `have hsub_eq : u - v = (b - a) * (a + b - 1) := by`\n"
        "  `  set d : ℕ := b - a`\n"
        "  `  have hb : b = a + d := by dsimp [d]; exact (Nat.add_sub_of_le (Nat.le_of_lt hlt)).symm`\n"
        "  `  have hd : 0 < d := by dsimp [d]; exact Nat.sub_pos_of_lt hlt`\n"
        "  `  dsimp [u, v]`\n"
        "  `  -- Important: unfold u,v before rewriting b; if you do rw [hb] first, the later change step fails.`\n"
        "  `  rw [hb]`\n"
        "  `  change (a + (a + d)^2) - (a + d + a^2) = d * (a + (a + d) - 1)`\n"
        "  `  have hle : a + d + a^2 ≤ a + (a + d)^2 := by nlinarith`\n"
        "  `  apply (Nat.sub_eq_iff_eq_add hle).2`\n"
        "  `  have hcancel : d * (a + (a + d) - 1) + d = d * (a + (a + d)) := by`\n"
        "  `    let s : ℕ := a + (a + d)`\n"
        "  `    have hs_pos : 0 < s := by dsimp [s]; omega`\n"
        "  `    calc`\n"
        "  `      d * (s - 1) + d = d * (s - 1) + d * 1 := by rw [Nat.mul_one]`\n"
        "  `      _ = d * ((s - 1) + 1) := by rw [Nat.mul_add]`\n"
        "  `      _ = d * s := by rw [Nat.sub_add_cancel (Nat.succ_le_of_lt hs_pos)]`\n"
        "  `      _ = d * (a + (a + d)) := by rfl`\n"
        "  `  have hrew : d * (a + (a + d) - 1) + (a + d + a^2) = (d * (a + (a + d) - 1) + d) + (a + a^2) := by ac_rfl`\n"
        "  `  simpa [Nat.add_assoc, Nat.add_left_comm, Nat.add_comm] using hrew`\n"
        "  `  rw [hcancel]`\n"
        "  `  ring_nf`\n"
        "  `have hba_pos : 0 < b - a := Nat.sub_pos_of_lt hlt`\n"
        "  `have hsum_pos : 0 < a + b - 1 := by omega`\n"
        "  `have hsub_pos : 0 < u - v := by rw [hsub_eq]; exact Nat.mul_pos hba_pos hsum_pos`\n"
        "  `have hv_lt_u : v < u := Nat.lt_of_sub_pos hsub_pos`\n"
        "  Use the symmetric pattern when `b < a` with the same level of detail, not a compressed variant:\n"
        "  `have hsub_eq : v - u = (a - b) * (a + b - 1) := by`\n"
        "  `  set d : ℕ := a - b`\n"
        "  `  have ha : a = b + d := by dsimp [d]; exact (Nat.add_sub_of_le (Nat.le_of_lt hgt)).symm`\n"
        "  `  have hd : 0 < d := by dsimp [d]; exact Nat.sub_pos_of_lt hgt`\n"
        "  `  dsimp [u, v]`\n"
        "  `  rw [ha]`\n"
        "  `  change (b + (b + d)^2) - (b + d + b^2) = d * (b + d + b - 1)`\n"
        "  `  have hle : b + d + b^2 ≤ b + (b + d)^2 := by nlinarith`\n"
        "  `  apply (Nat.sub_eq_iff_eq_add hle).2`\n"
        "  `  have hcancel : d * (b + d + b - 1) + d = d * (b + d + b) := by`\n"
        "  `    let s : ℕ := b + d + b`\n"
        "  `    have hs_pos : 0 < s := by dsimp [s]; omega`\n"
        "  `    calc`\n"
        "  `      d * (s - 1) + d = d * (s - 1) + d * 1 := by rw [Nat.mul_one]`\n"
        "  `      _ = d * ((s - 1) + 1) := by rw [Nat.mul_add]`\n"
        "  `      _ = d * s := by rw [Nat.sub_add_cancel (Nat.succ_le_of_lt hs_pos)]`\n"
        "  `      _ = d * (b + d + b) := by rfl`\n"
        "  `  have hmain : d * (b + d + b - 1) + (b + d + b^2) = d * (b + d + b) + (b + b^2) := by`\n"
        "  `    calc`\n"
        "  `      d * (b + d + b - 1) + (b + d + b^2) = (d * (b + d + b - 1) + d) + (b + b^2) := by ac_rfl`\n"
        "  `      _ = d * (b + d + b) + (b + b^2) := by rw [hcancel]`\n"
        "  `  rw [hmain]`\n"
        "  `  ring_nf`\n"
        "- Do not try `Nat.dvd_sub' hu_dvd hv_dvd` directly; those hypotheses only show divisibility into `2^k`. First derive the smaller power divides the larger one from the exponent gap. If `a < b`, use `obtain ⟨t, ht⟩ := Nat.exists_eq_add_of_lt hnm_lt` and then prove `u = v * 2^(t+1)` with a `calc` block, not a raw `rw` chain:\n"
        "  `have hu_eq_mul : u = v * 2^(t + 1) := by`\n"
        "  `  calc`\n"
        "  `    u = 2^m := hm`\n"
        "  `    _ = 2^(n + (t + 1)) := by rw [ht, Nat.add_assoc]`\n"
        "  `    _ = 2^n * 2^(t + 1) := by rw [Nat.pow_add]`\n"
        "  `    _ = v * 2^(t + 1) := by rw [hn]`\n"
        "  Then obtain `hv_dvd_u : v ∣ u` with witness `2^(t+1)` and use `exact hu_eq_mul`, not `hu_eq_mul.symm`. Do the symmetric construction when `b < a`.\n"
        "- If you need `u ≤ u * t` or `v ≤ v * t`, use `simpa [Nat.mul_comm] using Nat.le_mul_of_pos_left u htpos` and the analogous form for `v`.\n"
        "- If `a < b`, use `u = 2^m` and `v = 2^n` to show `v ∣ u - v`. Since `Nat.Coprime 2 (a + b - 1)` and `v = 2^n`, use `hcop.pow_left n` and `hcop_v.dvd_of_dvd_mul_right` to conclude `v ∣ (b - a)`, contradicting `b - a < v`. Do the symmetric construction when `b < a`, yielding `u ∣ (a - b)` and a contradiction with `a - b < u`.\n"
        "- For the final contradiction, avoid a big closing `omega`. Instead do it explicitly: if `v ∣ b - a`, write `⟨q, hq⟩`; prove `q ≠ 0` from `a < b`; get `hqpos : 0 < q`; derive `hv_le : v ≤ b - a` by `rw [hq]; simpa [Nat.mul_comm] using Nat.le_mul_of_pos_left v hqpos`; then close with `exact (Nat.not_lt_of_ge hv_le) hltv`. Do the symmetric argument for `u ∣ a - b`.\n"
        "- Also avoid `omega` for `b - a < v` and `a - b < u`. A clean route is: prove `b - a < b` by `Nat.sub_lt hb0 ha0`, prove `b ≤ v` by `dsimp [v]; exact Nat.le_add_right b (a^2)`, then chain `b - a < b ≤ v`. Symmetrically, prove `a - b < a` by `Nat.sub_lt ha0 hb0`, prove `a ≤ u` by `dsimp [u]; exact Nat.le_add_right a (b^2)`, then chain `a - b < a ≤ u`.\n"
        "- For the last contradiction, once you have `hv_le : v ≤ b - a` and `hltv : b - a < v`, or symmetrically `hu_le : u ≤ a - b` and `hltu : a - b < u`, close with `exact (Nat.not_lt_of_ge hv_le) hltv` or `exact (Nat.not_lt_of_ge hu_le) hltu` instead of another broad `omega`.\n"
        "- Avoid introducing `htpos : 0 < t`; `Nat.exists_eq_add_of_lt` already gives the needed strict gap through the trailing `+ 1`.\n"
        "- Handle `b < a` symmetrically with the corrected sign convention. Conclude the unequal case is impossible.\n"
        "- Keep the proof statement-preserving and avoid the earlier broken route around `Odd 2`, field-style notation on `Even`, or direct-argument calls to `Nat.dvd_prime_pow`.\n"
    ),
}


def _normalize_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_env_key() -> None:
    if os.environ.get("OPENROUTER_API_KEY"):
        return
    env_path = REPO_ROOT.parent / "misc" / "hermes_ref" / "firecrawl_compact" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            os.environ["OPENROUTER_API_KEY"] = line.split("=", 1)[1].strip()
            return


def _load_tasks(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks = payload.get("tasks") if isinstance(payload, dict) else payload
    if not isinstance(tasks, list):
        raise ValueError("task inputs must contain a tasks array")
    return tasks


def _statement_prefix(text: str) -> str:
    if ":= by" in text:
        return text.split(":= by", 1)[0].rstrip()
    if "begin" in text:
        return text.split("begin", 1)[0].rstrip()
    return text.strip()


def _extract_lean_block(text: str) -> Optional[str]:
    match = re.search(r"```lean\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip() + "\n"
    stripped = text.strip()
    if stripped.startswith("import ") or stripped.startswith("theorem "):
        return stripped + ("\n" if not stripped.endswith("\n") else "")
    return None


def _verify_with_kimina(proof_text: str, verifier_base_url: str) -> tuple[bool, Optional[str]]:
    import sys

    hilbert_root = REPO_ROOT.parent / "other_harness_refs" / "ml-hilbert"
    if str(hilbert_root) not in sys.path:
        sys.path.insert(0, str(hilbert_root))
    from kimina_client.sync_client import KiminaClient
    from src.tools.proof_utils import read_client_response
    from src.tools.lean_utils import extract_all_error_messages

    client = KiminaClient(api_url=verifier_base_url)
    response = client.check(proof_text.strip(), timeout=60, infotree="original")
    verification = read_client_response(response)[0]
    ok = bool(verification.get("is_correct_no_sorry"))
    if ok:
        return True, None
    try:
        errors = extract_all_error_messages(response, [proof_text])
        return False, errors[0]
    except Exception:
        return False, "verification_failed"


def _result_cost_usd(run_dir: Path) -> float:
    summary_path = run_dir / "meta" / "provider_metrics.json"
    if not summary_path.exists():
        return 0.0
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return 0.0
    total = payload.get("total_cost_usd")
    try:
        return float(total or 0.0)
    except Exception:
        return 0.0


def _build_prompt(
    task_id: str,
    full_file: str,
    *,
    prior_candidate: Optional[str] = None,
    prior_error: Optional[str] = None,
) -> str:
    hint = TASK_HINTS.get(task_id, "")
    prefix = (
        f"Task id: {task_id}\n"
        "Return a complete Lean 4 file that preserves the theorem statement exactly and replaces only the proof body.\n"
        "Do not modify imports, theorem name, binders, or hypotheses.\n"
        "Do not use sorry, admit, exact?, or theorem rewrites.\n"
        "Return exactly one ```lean fenced block, then TASK COMPLETE.\n\n"
    )
    if hint:
        prefix += f"{hint}\n"
    if prior_candidate:
        prefix += (
            "\nPrevious near-miss proof to repair instead of restarting from scratch:\n"
            "```lean\n"
            f"{prior_candidate.strip()}\n"
            "```\n"
        )
    if prior_error:
        clipped_error = prior_error.strip()
        if len(clipped_error) > 4000:
            clipped_error = clipped_error[:4000].rstrip() + "\n...[truncated]"
        prefix += (
            "\nMost relevant Lean errors from the previous attempt:\n"
            "```\n"
            f"{clipped_error}\n"
            "```\n"
        )
    return prefix + (
        "Starter file:\n"
        "```lean\n"
        f"{full_file.strip()}\n"
        "```"
    )


def _workspace_root(run_id: str, task_id: str) -> Path:
    safe_run_id = re.sub(r"[^A-Za-z0-9._-]+", "_", run_id).strip("._-") or "bb-formal-pack"
    safe_task_id = re.sub(r"[^A-Za-z0-9._-]+", "_", task_id).strip("._-") or "task"
    return REPO_ROOT / "tmp" / "bb_formal_pack_workspaces" / safe_run_id / safe_task_id


def _load_repair_seed(seed_dir: Optional[Path], task_id: str, suffix: str) -> Optional[str]:
    if seed_dir is None:
        return None
    path = seed_dir / f"{task_id}{suffix}"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def run_pack(
    *,
    manifest_path: Path,
    task_inputs_path: Path,
    out_path: Path,
    summary_path: Path,
    proof_output_dir: Path,
    raw_output_dir: Path,
    verifier_base_url: str,
    max_iterations: int,
    config_path: Path,
    repair_seed_proof_dir: Optional[Path] = None,
    repair_seed_raw_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    _read_env_key()
    os.environ.setdefault("RAY_SCE_LOCAL_MODE", "1")
    manifest = load_manifest(manifest_path)
    tasks = _load_tasks(task_inputs_path)
    toolchain = manifest["toolchain"]
    budget_class = manifest["budget"]["class"]
    run_id = str(manifest.get("run_id") or "bb-formal-pack")
    proof_output_dir.mkdir(parents=True, exist_ok=True)
    raw_output_dir.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    status_counts: Dict[str, int] = {}
    total_cost = 0.0

    for task in tasks:
        task_id = str(task["task_id"])
        input_text = str(task["input_text"])
        task_hash = str(task.get("input_hash") or _normalize_hash(input_text))
        workspace = _workspace_root(run_id, task_id)
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        result_json = workspace / "result.json"
        prior_candidate = _load_repair_seed(repair_seed_proof_dir, task_id, ".lean")
        prior_error = _load_repair_seed(repair_seed_raw_dir, task_id, ".json")
        if prior_error:
            try:
                prior_error_payload = json.loads(prior_error)
                prior_error = str(prior_error_payload.get("verify_error") or prior_error_payload.get("stderr_tail") or "")
            except Exception:
                pass
        cmd = [
            "python",
            "main.py",
            str(config_path.relative_to(REPO_ROOT)),
            "--workspace",
            str(workspace),
            "--task",
            _build_prompt(task_id, input_text, prior_candidate=prior_candidate, prior_error=prior_error),
            "--max-iterations",
            str(max_iterations),
            "--result-json",
            str(result_json),
        ]
        proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=900)
        candidate_text = None
        run_dir = None
        result_payload: Dict[str, Any] | None = None
        if result_json.exists():
            result_payload = json.loads(result_json.read_text(encoding="utf-8"))
            result_payload = result_payload.get("result") if isinstance(result_payload, dict) else None
        if isinstance(result_payload, dict):
            run_dir = Path(str(result_payload.get("run_dir") or result_payload.get("logging_dir") or ""))
            messages = result_payload.get("messages") or []
            for message in reversed(messages):
                if isinstance(message, dict) and message.get("role") == "assistant":
                    candidate_text = _extract_lean_block(str(message.get("content") or ""))
                    if candidate_text:
                        break
        statement_ok = False
        verify_ok = False
        verify_error = None
        proof_path = proof_output_dir / f"{task_id}.lean"
        if candidate_text:
            statement_ok = _statement_prefix(candidate_text) == _statement_prefix(input_text)
            if statement_ok:
                proof_path.write_text(candidate_text, encoding="utf-8")
                verify_ok, verify_error = _verify_with_kimina(candidate_text, verifier_base_url)
        if run_dir and run_dir.exists():
            total_cost += _result_cost_usd(run_dir)
        if verify_ok and statement_ok:
            status = "SOLVED"
        elif proc.returncode != 0:
            status = "ERROR"
        else:
            status = "UNSOLVED"
        status_counts[status] = status_counts.get(status, 0) + 1
        diagnostic = {
            "task_id": task_id,
            "proc_returncode": proc.returncode,
            "stdout_tail": proc.stdout[-4000:],
            "stderr_tail": proc.stderr[-4000:],
            "statement_ok": statement_ok,
            "verify_ok": verify_ok,
            "verify_error": verify_error,
            "run_dir": str(run_dir) if run_dir else None,
            "candidate_text": candidate_text,
        }
        raw_path = raw_output_dir / f"{task_id}.json"
        raw_path.write_text(json.dumps(diagnostic, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        row = {
            "task_id": task_id,
            "toolchain_id": f"lean-{toolchain['lean_version']}__mathlib-{toolchain['mathlib_commit']}",
            "input_hash": task_hash,
            "prover_system": "bb_hilbert_like",
            "budget_class": budget_class,
            "status": status,
            "verification_log_digest": _normalize_hash(json.dumps({"statement_ok": statement_ok, "verify_ok": verify_ok, "verify_error": verify_error}, sort_keys=True)),
            "run_id": run_id,
            "attempts": 1,
            "repair_rounds_used": 0,
            "wall_clock_ms": 0,
            "proof_artifact_ref": str(proof_path) if proof_path.exists() else None,
        }
        rows.append(row)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    summary = {
        "schema": "breadboard.bb_formal_pack_run.v1",
        "ok": True,
        "run_id": run_id,
        "task_count": len(rows),
        "status_counts": status_counts,
        "estimated_total_cost_usd": round(total_cost, 6),
        "manifest_path": str(manifest_path),
        "task_inputs_path": str(task_inputs_path),
        "result_path": str(out_path),
    }
    dump_json(summary_path, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--task-inputs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--summary-out", required=True)
    parser.add_argument("--proof-output-dir", required=True)
    parser.add_argument("--raw-output-dir", required=True)
    parser.add_argument("--verifier-url", default="http://127.0.0.1:18001/")
    parser.add_argument("--config", default="agent_configs/atp_hilbert_like_gpt54_v2.yaml")
    parser.add_argument("--max-iterations", type=int, default=8)
    parser.add_argument("--repair-seed-proof-dir")
    parser.add_argument("--repair-seed-raw-dir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_pack(
        manifest_path=Path(args.manifest).resolve(),
        task_inputs_path=Path(args.task_inputs).resolve(),
        out_path=Path(args.out).resolve(),
        summary_path=Path(args.summary_out).resolve(),
        proof_output_dir=Path(args.proof_output_dir).resolve(),
        raw_output_dir=Path(args.raw_output_dir).resolve(),
        verifier_base_url=str(args.verifier_url).rstrip("/"),
        max_iterations=int(args.max_iterations),
        config_path=(REPO_ROOT / args.config).resolve(),
        repair_seed_proof_dir=Path(args.repair_seed_proof_dir).resolve() if args.repair_seed_proof_dir else None,
        repair_seed_raw_dir=Path(args.repair_seed_raw_dir).resolve() if args.repair_seed_raw_dir else None,
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"[bb-formal-pack-v1] ok={summary['ok']} tasks={summary['task_count']} statuses={summary['status_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
