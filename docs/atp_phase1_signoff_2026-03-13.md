# ATP Phase 1 Signoff — 2026-03-13

## Scope

This signoff closes ATP Phase 1 for the BreadBoard versus maintained-Hilbert comparison program.

Phase 1 scope:

- build and stabilize the comparison lane
- compare against the maintained Hilbert fork
- canonicalize and validate extracted theorem slices
- separate baseline versus focused-repaired evidence
- backfill BreadBoard spend estimates
- complete at least one easy and one medium replay audit
- produce a canonical scoreboard and evaluation memo

Comparator identity:

- baseline system: `hilbert_roselab`
- upstream comparator repo: `Rose-STL-Lab/ml-hilbert`
- this is not the stale Apple-hosted Hilbert repository

Candidate identity:

- BreadBoard direct formal runner with the maintained-Hilbert-style ATP arm

## Phase 1 gate result

The Phase 1 completion gate is fully met.

Reference:

- `docs/atp_phase1_completion_gate_2026-03-13.md`

Covered headline packs:

- `pack_b_core_noimo_minif2f_v1`
- `pack_c_imo1977_p6_stress_minif2f_v1`
- `pack_d_mixed_induction_numbertheory_minif2f_v1`
- `pack_e_algebra_core_minif2f_v1`
- `pack_f_discrete_arithmetic_mix_minif2f_v1`
- `pack_g_arithmetic_sanity_minif2f_v1`
- `pack_h_modular_closedform_minif2f_v1`
- `pack_i_divisors_modmix_minif2f_v1`
- `pack_j_residue_gcd_mix_minif2f_v1`
- `pack_k_moddigit_closedform_minif2f_v1`
- `pack_l_algebra_linear_equiv_minif2f_v1`
- `pack_m_boundary_olympiad_mix_minif2f_v1`

## What Phase 1 establishes

Phase 1 establishes that:

- the BreadBoard versus maintained-Hilbert comparison lane is operationally stable
- tranche construction, invalid-extract handling, and canonical promotion are governed rather than ad hoc
- BreadBoard is consistently competitive on valid bounded miniF2F-style slices
- across the canonical Phase 1 headline packs, BreadBoard is ahead on most packs and not behind on any
- a genuine boundary region exists where both systems fail under bounded caps
- replay audit did not overturn the program-level interpretation

## What Phase 1 does not establish

Phase 1 does not establish that:

- BreadBoard dominates all of miniF2F
- BreadBoard dominates harder olympiad or Putnam-style ATP tasks
- BreadBoard has a provider-authoritative cost advantage, because current BreadBoard spend is estimated from runner telemetry
- theorem-local repair is unnecessary for all advantageous BreadBoard results

## Replay audit interpretation

Replay audit covered one easy and one medium canonical pack.

Result:

- BreadBoard solve counts were stable on both audited packs
- maintained Hilbert improved by one task on each replay
- BreadBoard still remained ahead on both replayed packs

Reference:

- `docs/atp_hilbert_replay_audit_status_2026-03-13.md`

## Program source of truth

Program-level interpretation should be taken from:

- `docs/atp_hilbert_phase1_eval_memo_2026-03-13.md`
- `docs/atp_hilbert_scoreboard_status_2026-03-13.md`
- `docs/atp_hilbert_canonical_baselines_2026-03-13.md`
- `docs/ATP_HILBERT_MAINTAINED_RUNBOOK_V1_2026-03-13.md`

## Phase 2 entrypoint

Phase 2 starts from:

- `breadboard_repo_atp_followup_20260310-jbk` — define the next ATP tranche beyond Pack M

That issue is future-phase work, not unresolved Phase 1 debt.

## Signoff

ATP Phase 1 is complete and ready to merge as a closed evaluation phase.
