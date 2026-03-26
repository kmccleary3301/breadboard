# DAG Runtime V3 Cross-Paper Composition

`DAG V3` Phase 4 turns the RSA and PaCoRe replication tranches into downstream-usable artifacts without changing the DAG kernel.

## What Phase 4 adds

Phase 4 adds only composition-layer packets:

- optimize-ready comparison packet
- bounded RL-facing export-slice packet
- updated DARWIN boundary packet
- cross-paper synthesis packet

These packets are helper-level products of the replication tranches, not new runtime truth.

## Optimize consumption

Optimize should consume:

- paper recipe manifests
- fidelity scorecards
- compute ledgers
- baseline comparison packets
- deviation ledgers

Optimize should **not** require new DAG nouns to do so.

## RL-facing export slices

Phase 4 may define bounded RL-facing export opportunities, but it must not turn DAG into an RL framework.

That means:

- slice definitions are allowed
- explicit training opportunities are allowed
- public RL control surfaces are not added
- training ownership remains outside DAG

## DARWIN boundary

Phase 4 should tighten, not blur, the DARWIN boundary.

Still DAG-local:

- recipe manifests
- fidelity scorecards
- replication helpers
- bounded replication packets

Still outside DAG:

- campaign orchestration
- archive/island semantics
- persistent outer-loop search
- novelty/diversity population management

## Success condition

Phase 4 succeeds when:

- optimize can consume the replication packets without DAG changes
- RL-facing opportunities are concrete but still bounded
- DARWIN ownership remains explicit
- the cross-paper synthesis makes it easier to classify remaining work

without introducing any new DAG kernel surface.
