# DARWIN Lane Registry V0

This registry defines the initial Phase-1 lane portfolio and the launch rubric.

## Launch rubric

Each lane must declare:

- `lane_id`
- objective class
- evaluator type
- contamination risk class
- launch phase
- readiness prerequisites
- whether the lane is claim-eligible in Phase-1

## Initial portfolio

- `lane.atp`
- `lane.harness`
- `lane.systems`
- `lane.repo_swe`
- `lane.scheduling`
- `lane.research`

The first-wave launch priority follows the planner:

1. `lane.harness`
2. `lane.systems`
3. `lane.atp`
4. `lane.repo_swe`
5. `lane.scheduling`
6. `lane.research`

The machine-readable source of truth is `registries/lane_registry_v0.json`.
