# W7.4 Workflow control characterization

## Verdict

**PARTIAL.** The immutable workflow definition can name the DAG. `WorkItemRepository` durably owns lifecycle, delegation, attempt, cancellation, and child-join facts. `DurableChildFactory` durably owns ordinary child Session identity, execution observation, recovery, result preparation, and exactly-once settlement. No current owner folds those retained facts into the next workflow action after controller process death.

## Existing owners

| Fact | Owner | Current behavior |
| --- | --- | --- |
| Dependency and parent/child Work Item graph | `WorkItem` / `WorkItemRepository` | Append-only events rebuild the same `WorkItemSnapshot`; `child.joined` is idempotent and conflicting outcomes fail. |
| Child Session and execution lifecycle | `DurableChildFactory` / `SessionRegistry` | A retained recovery reference reconstructs and reconciles one ordinary child Session without a second child journal. |
| Result bytes | `ArtifactStore` | Prepared result identities are retained before terminal settlement. |
| Product truth | Product `Session` / `session_store` | Session events remain the only logical runtime truth. |

The missing behavior is a deterministic rule-table reader and executor. Today a caller must remember which workflow step a child represented, inspect dependencies, and decide whether to start, wait, fail, cancel, or finish. That memory is lost on restart.

## Rules

Use a two-step DAG: `inspect` has no dependencies; `verify` depends on `inspect`. Both steps are ordinary durable children of one running parent Work Item and Product Session.

1. Admit the definition and start `inspect`.
2. Prepare and settle `inspect` exactly once.
3. Kill the controller before `verify` starts.
4. Reopen `SessionRegistry`, `WorkItemRepository`, and `DurableChildFactory` from disk.
5. Re-evaluate the same immutable definition.

The independent expected table is:

| Retained state | Decision |
| --- | --- |
| No child for a step; every dependency completed | start that step |
| Any matching child nonterminal | wait after reconciling it |
| Every step completed | complete |
| Any step failed | fail |
| No failure and any step canceled | cancel |

For the retained state after step 3, both the pre-restart and post-restart decision must be `start verify`. After `verify` settles, both must be `complete`. Step ordering is lexical for simultaneously ready steps. A duplicate durable step binding, unknown dependency, cycle, definition mismatch, or conflicting child outcome fails closed.

## Boundary

A controller may cache nothing authoritative. Its definition is caller input; its only retained bindings are written atomically with the child record before launch. Decisions are recomputed from `WorkItemRepository` and retained child Session state. A second workflow event journal, scheduler status in Product Session payloads, or inferred child identity is rejected.
