# DARWIN Claim Ladder and Evidence Gates V0

## Claim tiers

- `t0_debug`
- `t1_controlled_internal`
- `t2_internal_comparative`
- `t3_external_safe_comparative`
- `t4_frontier_claim`

## Minimum bars

### `t0_debug`

- complete logs
- no comparative or frontier language

### `t1_controlled_internal`

- replay-complete artifacts
- ablations present
- exploratory or internal-only interpretation

### `t2_internal_comparative`

- paired design
- drift checks
- contamination checks
- all-runs retained

### `t3_external_safe_comparative`

- replication across two slices, families, or lanes
- all-runs disclosure
- matched protocol language

### `t4_frontier_claim`

- compute-normalized comparison
- replicated evidence
- adversarial stress tests
- reviewer signoff

## No-go rules

- no single-run claims
- no ATP-only DARWIN completion story
- no frontier language without a complete `EvidenceBundle`
- no claim-bearing exclusion without explicit annotation
