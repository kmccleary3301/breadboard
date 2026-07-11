# Gate G-K decision — Manifest/Lock rollout

Decision date: 2026-07-11 · Head: db8a7b900517430a0cd4f62a4bb950af3cf41258 · Protocol: plan §5, item K1.

## Conditions and evidence

| # | Condition | State | Evidence |
|---|---|---|---|
| 1 | WS-F done | **NOT MET** — F1-F4/F4a/F6 done; F5 BLOCKED (env) | ledger F-items; F5 classification blocked_env: external phase-15 atomic-ledger mutability + AM19 run_lane workspace-contract activation parked (docs_tmp/phase_20/evidence F-series; WS-F round-4/5/6 verifier reports) |
| 2 | WS-H done | MET | H1 wave-1; H2-H6 merge 4aea9452 dual-verified; AM18 prerequisite dual PASS (evidence/AM18/implementer.json) |
| 3 | G-J recorded PASS | **NOT MET** — G-J recorded FAIL 2026-07-11 | ledger gates[]; run evidence docs_tmp/phase_20/j2_run/; J3 root_cause.json (protocol_gap_predeclared_unbuildable); AM22a (no rerun this campaign) |
| 4 | F5(c) churn-locality held >=2 weeks of battery runs (or 10 runs) | **NOT MET** — F5 blocked; no accrual window | F5 evidence: churn/fixed-point checks failed only on external phase-15 mutability; no battery-run accrual recorded |

## Decision

**G-K: FAIL.** Three of four conditions unmet. K2 (migrate 20 lanes), K3 (retire legacy loader), K4 (pin-graph audit) remain `na_gated`; their 150 conditional points stay out of the campaign denominator per §2.

## Rationale

The pilot proved the manifest+lock compile path (F3/F4 merged, dual-verified) but the flagship demo proved an outsider cannot yet complete the lane lifecycle through the front door (no adapter-registration/claim/reverify: bb-72h, bb-9c8, bb-ys7), and F5's full-flow proof is environment-blocked. Rolling 20 more lanes onto a lifecycle whose front door and full-flow proof are incomplete would replicate exactly the unearned-surface failure mode this campaign exists to stop.

## Flip conditions (what would reopen K2-K4)

1. F5 unblocked: external phase-15 ledger pinned or classified stable + AM19 activation completed; full-flow parity/churn/fixed-point proof green.
2. bb-72h/bb-9c8/bb-ys7 delivered (own approval/points), then an authorized J2-class rerun records G-J PASS.
3. Churn-locality evidence: >=2 weeks or 10 battery runs with locality held after (1).
