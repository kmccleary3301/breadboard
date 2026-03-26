# DARWIN Stage-5 Bounded Transfer Policy V0

Date: 2026-03-25
Status: active

## Purpose

This policy bounds Stage-5 transfer work to small, interpretable cases inside the scaled-compounding tranche.

## Allowed transfer modes

- `lane.repo_swe -> lane.systems` for the retained topology family only
- `lane.systems -> lane.scheduling` for policy-family confirmation only

## Prohibited transfer modes

- broad lane-by-lane sweeps
- transfers from held-back families
- transfers with mismatched evaluator scope
- transfers with mismatched budget/comparison class
- composition-dependent transfers

## Transfer outcomes

- `retained`
- `degraded_but_valid`
- `invalid_comparison`
- `failed_transfer`
- `descriptive_only`

## Current tranche stance

The current tranche authorizes one retained case and one or two bounded stress/failure cases. It does not authorize a broad transfer matrix.
