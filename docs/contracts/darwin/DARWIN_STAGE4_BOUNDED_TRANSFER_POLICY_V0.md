# DARWIN Stage-4 Bounded Transfer Policy V0

Date: 2026-03-20
Status: active for the Stage-4 multi-family promotion tranche

## Purpose

Define the only authorized transfer behavior for the first Stage-4 family-compounding tranche.

## Allowed transfer modes

- bounded transfer of a promoted family from `lane.repo_swe` to `lane.systems`
- bounded confirmation or failure attempts against `lane.scheduling` when scope and evaluator comparability are explicit

## Prohibited transfer modes

- broad cross-lane transfer sweeps
- evaluator-incomparable transfer
- unmatched-budget transfer
- transfer from non-promoted families unless explicitly marked descriptive-only
- public generalization claims from transfer outcomes

## Transfer outcomes

- `retained`
- `degraded_but_valid`
- `invalid_comparison`
- `failed_transfer`
- `descriptive_only`

## Comparison discipline

Every valid transfer comparison must preserve:

- evaluator semantics
- budget class
- comparison class
- route compatibility when route-aware comparison matters
- replay requirements when promotion-backed transfer is claimed

## Tranche note

This policy exists to produce one retained bounded transfer and one informative failed transfer, not a broad transfer-learning result.
