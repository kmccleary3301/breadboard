# DAG Runtime V3 RSA Replication Packet

The first real replication tranche in `DAG V3` is `RSA`.

This is deliberate:

- RSA is closer to the current DAG runtime surface
- it is cheaper to sweep on `GPT-5.4 Mini`
- it is easier to make structurally faithful before widening into training-aware claims

## What Phase 2 adds

Phase 2 adds helper-level RSA replication artifacts, not DAG runtime nouns:

- a parameterized `N / K / T` sweep packet
- a budget-matched baseline packet
- a synthesized RSA replication packet with:
  - paper profile
  - fidelity scorecard
  - compute ledger
  - deviation ledger
  - qualitative synthesis

## What counts as success here

The goal is not “prove RSA completely.”

The goal is:

- exact control-profile fields are represented
- fixed-seed `N / K / T` sweeps are real
- baseline fairness is explicit
- compute normalization is explicit
- claim limits are explicit

That is enough to reach a serious `medium_fidelity` RSA packet.

## Claim discipline

Phase 2 RSA claims must remain labeled as:

- `algorithm-faithful`
- `model-substituted`
- `inference-only`

This tranche is intentionally not claiming training-aware RSA replication.

## Boundaries

This packet should not introduce:

- new DAG kernel nouns
- paper-mode runtime behavior
- public search-policy expansion
- RL frameworkization inside DAG

It is a helper-layer replication tranche on top of frozen DAG V2 runtime truth.
