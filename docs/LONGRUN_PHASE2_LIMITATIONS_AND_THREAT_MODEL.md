# LongRun Phase2 Limitations and Threat Model

This document captures known limits and threat surfaces for LongRun phase-2.

## 1) Scope boundaries

1. LongRun remains opt-in and disabled in parity-critical defaults.
2. Deterministic policy matrix is not a substitute for broad live-quality benchmarking.
3. Live pilot is intentionally cap-constrained; results are directional, not publication-final.

## 2) Known limitations

1. Scenario representativeness:
   1. deterministic scenarios are curated and finite,
   2. they do not span all real-world repository topologies.
2. Provider dependence:
   1. quality and latency can vary by provider/model/runtime implementation,
   2. estimated cost may differ from provider billing when full token telemetry is absent.
3. Runtime noise:
   1. subprocess and environment variance can impact timeout/failure distributions,
   2. occasional transient failures are expected in live probes.
4. Contract granularity:
   1. scenario contracts validate stop classes and summaries,
   2. they do not prove semantic correctness of arbitrary code edits.

## 3) Threat model

## T1: Unbounded runtime/cost growth

Mitigations:

1. hard token and estimated-cost caps,
2. per-arm timeout guard,
3. bounded episode/retry budgets.

## T2: Silent policy regressions

Mitigations:

1. deterministic pilot matrix,
2. scenario contract checks,
3. strict go/no-go with trend history.

## T3: Parity surface contamination

Mitigations:

1. parity-disabled audit in strict lanes,
2. longrun/rlm remain explicitly gated and opt-in.

## T4: Opaque runtime failures

Mitigations:

1. per-arm telemetry with failure class and timeout flags,
2. guard-trigger counters in live summaries,
3. artifact-first debugging runbooks.

## 4) What this does not claim

1. Universal model quality superiority for long-horizon tasks.
2. Stability under unlimited run durations or budgets.
3. Immediate production readiness for every provider/model combination.

## 5) Safe operating guidance

1. Run deterministic gates before live probes.
2. Keep live runs spend-capped and timeout-capped.
3. Treat warn-lane drift as an investigation trigger, not a deploy signal.
4. Promote to strict only after sustained stable trend windows.
