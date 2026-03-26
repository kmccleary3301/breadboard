# DARWIN Stage-5 Composition Canary Policy V0

Date: 2026-03-25
Status: active

## Purpose

This policy bounds Stage-5 composition work to a single canary decision.

## Composition is allowed only if

- both families are active or explicitly transfer-candidate
- both families are comparison-valid in the current tranche
- the target lane has at least two locally interpretable active family surfaces
- composition does not confound transfer interpretation

## Composition is not allowed if

- only one local active family exists on the target lane
- the candidate pair crosses lanes in a way that collapses transfer and composition into one claim
- a held-back family is required to make the pair

## Allowed outcomes

- `composition_positive`
- `composition_neutral`
- `composition_negative`
- `composition_invalid`
- `composition_not_authorized`

## Current tranche stance

The current Stage-5 tranche permits only one canary decision and does not authorize a broader composition program.
