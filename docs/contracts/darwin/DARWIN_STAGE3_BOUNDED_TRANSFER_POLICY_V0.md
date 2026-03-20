# DARWIN Stage-3 Bounded Transfer Policy V0

Date: 2026-03-19
Status: active for the Stage-3 component-promotion tranche

## Purpose

Define the only authorized transfer behavior for the first Stage-3 component-promotion tranche.

## Allowed transfer modes

- bounded transfer of a promoted component family from `lane.repo_swe` to `lane.systems`
- bounded confirmation attempts against `lane.scheduling` when policy scope and evaluator comparability are explicit

## Prohibited transfer modes

- broad cross-lane transfer sweeps
- evaluator-incomparable transfer
- unmatched-budget transfer
- transfer based on unreplayed or weakly supported component families
- public generalization claims from transfer outcomes

## Transfer outcomes

- `retained`
- `degraded_but_valid`
- `invalid_comparison`
- `failed_transfer`
- `descriptive_only`

## Comparison discipline

Every valid transfer comparison must preserve:

- lane-aware evaluator semantics
- budget class
- comparison class
- route compatibility when route-aware comparison is relevant

## Tranche note

This policy exists to produce one bounded retained transfer and one informative failed transfer, not a broad transfer-learning result.
