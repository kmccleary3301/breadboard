# Gate G-L decision — Evidence-core extraction

Decision date: 2026-07-11 · Head: db8a7b900517430a0cd4f62a4bb950af3cf41258 · Protocol: plan §5, item L1 (same protocol as K1).

## Conditions and evidence

| # | Condition | State | Evidence |
|---|---|---|---|
| 1 | G-K PASSED | **NOT MET** — G-K decided FAIL 2026-07-11 | GATE_K_DECISION.md (this head) |
| 2 | Second external consumer for evidence machinery OR J3 retro identifies reuse demand | **NOT MET** — no second consumer exists; J3 retro (docs_tmp/phase_20/evidence/J3/retro.md) names CLI/docs gaps only, no reuse demand | E-series consumer inventory (contract_tiers registry: evidence machinery consumers are internal governance only); J3 retro |

## Decision

**G-L: FAIL/defer** — the plan's own expected outcome ("no second consumer today"). L2 (extract libs/evidence_core, 70) and L3 (migrate scripts, 30) remain `na_gated`, out of the denominator.

## Flip conditions

1. G-K flips to PASS (see GATE_K_DECISION.md flip conditions), AND
2. A concrete second consumer materializes (external repo/tool importing the claim/C4/comparator/fixed-point machinery) or a retro/demo run documents named reuse demand with an owner.
