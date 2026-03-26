# DARWIN Transfer Protocol V1

This document defines when a cross-lane transfer attempt is valid in the current Phase-1 Darwin program.

## A transfer attempt is valid only when

1. the source operator or policy is supported on the source lane
2. the target lane explicitly allows the operator as supported or experimental
3. the source and target evaluations are run under comparable budget classes
4. topology compatibility is declared for the target lane
5. the target candidate passes its lane evaluator
6. the attempt is recorded in the transfer ledger

## Transfer claims

Phase-1 allows only internal operational transfer claims:

- transfer protocol operational
- transfer attempt valid / invalid
- transfer attempt improved / did not improve the target lane

Phase-1 does not allow:

- broad cross-lane generalization claims
- superiority claims from one transfer
- publication-grade transfer claims

## Replay

If a transferred candidate is promoted, replay audit is required before any claim above pure execution status.
