# DARWIN Stage-3 Tranche-1 Hard Gate

Date: 2026-03-19

- [x] Stage-3 doctrine and ADRs are frozen
- [x] tranche-1 boundary is explicit
- [x] repo_swe substrate-backed mutation canary is broad enough for bounded proving
- [x] systems mutation canary exists
- [x] `ExecutionPlan` consumption remains narrow and parity-checked
- [x] evaluator packs and budget envelopes exist on Stage-3 canary paths
- [x] routing/cost readiness note exists
- [x] decision-ledger truth remains narrower than archive truth
- [x] secondary confirmation and ATP audit posture are recorded

## Gate decision

The tranche-1 hard gate is **passed for bounded real-inference entry**.

## Authorized next move

Proceed to a bounded Stage-3 real-inference entry tranche with:

- primary lanes: `lane.repo_swe`, `lane.systems`
- watchdog/control lane: `lane.harness`
- secondary lane: `lane.scheduling`
- audit lane: `lane.atp`

## Still not authorized

- broad multi-lane campaigns
- async search
- archive-as-truth changes
- public or superiority packaging
