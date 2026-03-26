# DAG Runtime V3 PaCoRe Replication Packet

The second replication tranche in `DAG V3` is `PaCoRe`.

This tranche is stricter than the RSA tranche in one important way:

- the main risk is not runtime fit
- the main risk is replication slippage

It is easy to build something that is `PaCoRe-like` without being faithful to PaCoRe's actual round geometry, message passing, compaction discipline, and comparison packet structure.

## What Phase 3 adds

Phase 3 adds helper-level PaCoRe replication artifacts:

- low / medium / high round profiles
- explicit conclusion-only compaction baseline
- with / without message-passing ablation packet
- parallel vs sequential comparison packet
- bounded coding-transfer slice runner
- a synthesized PaCoRe replication packet tying those together

## What counts as success here

Success means:

- exact round-profile helpers exist
- compaction behavior is explicit and auditable
- message-passing effects can be compared against a bounded no-message baseline
- parallel vs sequential structure is explicit
- coding-transfer follow-on is defined, but still bounded
- the packet is honestly labeled `inference-only`

## Claim discipline

Phase 3 PaCoRe claims must remain labeled as:

- `algorithm-faithful`
- `model-substituted`
- `inference-only`

This tranche does not claim training-aware PaCoRe replication.

## Boundaries

This packet should not introduce:

- new DAG kernel nouns
- a new public compaction runtime family
- paper-mode runtime menus
- public async experiment scheduling
- RL or DARWIN frameworkization inside DAG

It remains a helper-layer replication tranche over frozen DAG V2 runtime truth.
