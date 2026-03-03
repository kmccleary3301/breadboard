# ATP Repair Operator Library v1

Date: 2026-02-24

## Purpose

Define the initial operator catalog and selection constraints used by ATP repair-policy logging.

## Operator Catalog

| Operator ID | Intended use | Preconditions |
|---|---|---|
| `repair_minimal_patch` | minimal local proof/tactic edit | narrow failure context available |
| `repair_local_rewrite` | rewrite subproof around failing step | tactic/goal context present |
| `repair_goal_decompose` | split into subgoals/lemmas | complex goal or mismatch detected |
| `repair_retrieve_more` | increase retrieval breadth/depth | retrieval signal appears insufficient |
| `repair_change_strategy` | swap tactic/policy family | repeated failure under same strategy |
| `repair_off` | ablation path (no repair) | explicit ablation enabled |

## Logging Contract

Schema:

- `docs/contracts/atp/schemas/atp_repair_policy_decision_v1.schema.json`

Required decision features:

- `attempt_index`
- `budget_remaining_steps`
- `goals_estimate`
- `goal_complexity_estimate`
- `prior_failures`
- `fallback_used_previous`

## Notes

- This operator set is extension-level ATP behavior and does not alter kernel ABI.
- New operators must be added via schema enum update + policy doc update in the same change.
