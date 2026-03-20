# DARWIN Stage-3 Cost and Routing Readiness

Date: 2026-03-19
Status: tranche-1 readiness note

## Default routing

- routine Stage-3 worker inference: `gpt-5.4-mini`
- bulk/filter/repetition work: `gpt-5.4-nano`
- stronger tier: exception-only

## Required telemetry before bounded real-inference entry

- evaluator-pack presence on claim-bearing runs
- budget envelope with:
  - wall-clock
  - token counts
  - cost estimate
  - cost classification
  - comparison class
  - reserve fractions

## Not yet claimed

- no broad economics result
- no provider ranking
- no model superiority claim

## Read

The routing doctrine is ready for bounded real-inference entry once tranche-1 hard-gate conditions are met.
