# DAG Runtime V3 Fidelity Helper Layer

`DAG V3` is not a kernel-expansion round.

The DAG runtime surface from `V2` remains frozen. The next justified layer is a non-kernel fidelity/helper layer that turns stable DAG runtime artifacts into serious paper-replication and experiment packets.

## Core doctrine

Keep runtime truth unchanged:

- `SearchRun`
- `SearchCandidate`
- `SearchMessage`
- `SearchCarryState`
- `SearchAssessment`
- `SearchEvent`
- `SearchTrajectoryExport`

Add only helper artifacts outside kernel truth:

- `PaperRecipeManifest`
- `FidelityScorecard`
- `ComputeBudgetLedger`
- `BaselineComparisonPacket`
- `ReplicationDeviationLedger`

These artifacts exist to:

- define paper-faithful recipe profiles
- label fidelity honestly
- normalize compute and baseline comparisons
- record explicit deviations
- support downstream optimize and RL-facing experiment consumers

## Why this is the right V3 layer

The post-`DAG V2` studies did not produce repeated DAG-local public-shape pressure.

What did appear was a replication-quality bottleneck:

- exact RSA `N / K / T` study profiles
- exact PaCoRe round/message/compaction profiles
- compute normalization
- fidelity labeling
- deviation tracking
- downstream-ready comparison packets

That bottleneck is helper/tooling work, not runtime-kernel work.

## First tranche

The first `DAG V3` tranche should:

1. lock the frozen-kernel rule
2. add the helper artifact types
3. define one RSA paper profile
4. define one PaCoRe paper profile
5. run one Mini-first smoke packet for each

No kernel edits are required for that tranche.

## Fidelity labels

The initial helper layer should distinguish at least:

- `paper_inspired_only`
- `medium_fidelity`
- `high_fidelity`
- `training_aware_partial_replication`

For the initial RSA and PaCoRe smoke packets, the correct claim form is:

- `algorithm-faithful, model-substituted, inference-only`

That is strong enough to be useful and honest enough to avoid overclaiming.

## Shared metrics

The helper layer should compute reusable replication metrics from existing DAG artifacts, such as:

- aggregability gap
- diversity / mixing
- aggregation gain
- emergent correctness
- message efficiency
- verifier yield

These are helper-level metrics computed from stable DAG runtime outputs, not new runtime truth.

## Boundaries

This layer should not introduce:

- paper-mode runtime menus
- public async experiment schedulers
- public search-policy ontology
- DARWIN-lite campaign semantics
- RL frameworkization inside DAG

It is a fidelity-and-experiment layer over a frozen runtime.
