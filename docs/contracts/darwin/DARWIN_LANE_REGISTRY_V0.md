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

The first-wave launch priority followed the planner:

1. `lane.harness`
2. `lane.systems`
3. `lane.atp`
4. `lane.repo_swe`
5. `lane.scheduling`
6. `lane.research`

Current live-launch state now includes all six listed lanes. Future work is no longer about launching omitted lanes; it is about comparative depth, transfer rigor, and Phase-1 exit evidence.

The machine-readable source of truth is `registries/lane_registry_v0.json`.
