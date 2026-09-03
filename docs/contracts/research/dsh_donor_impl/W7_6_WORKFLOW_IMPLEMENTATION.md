# W7.6 replayable workflow controller

Status: implemented after the W7 DIT selected Design A, a recomputed controller over existing owners.

## Module card

- **Boundary:** `breadboard.product.runtime.workflows.ReplayableWorkflowController`.
- **Durable truth:** `WorkflowDefinition` is immutable caller input and owns the DAG. `WorkItemRepository` event streams own parent and child lifecycle, attempt, placement, and join facts. `DurableChildFactory` owns child identity, recovery, execution-target, cancellation, result, and settlement facts. The controller writes no workflow journal, cursor, or checkpoint.
- **Definition identity:** `WorkflowDefinition.identity(workflow_id)` hashes the canonical ordered step graph and each retained `ChildSpec`. Every child started by a workflow persists the workflow ID, step ID, and definition digest. A changed graph or step contract is rejected after the first child exists.
- **Decision replay:** `project_workflow_decision()` is a pure, versioned multi-stream projection. It folds the parent and child Work Item event streams plus immutable retained child bindings, validates definition and lineage identity, and returns the decision with exact source cursors. `decision()` supplies only those owner facts. `advance()` returns that same replayable decision shape; transient “started now” telemetry is deliberately absent. Controller restart therefore changes no decision input or output.
- **Activation:** `advance()` holds an in-process lock and a workspace process lock, reconciles every retained child so terminal-before-owner-repair crashes close idempotently, then starts the lexically first ready step through `DurableChildFactory`. A nonterminal step forces `wait`, so activation remains serial and replay-safe. No provider path or fallback exists in the controller.

## Rule table

1. Any failed step -> `fail`.
2. Otherwise any canceled step -> `cancel`.
3. Otherwise all steps completed -> `complete`.
4. Otherwise one or more dependency-ready steps -> `start`.
5. Otherwise active or dependency-blocked steps -> `wait`.

The decision reports ready, active, completed, failed, canceled, and blocked step IDs plus durable child Session bindings. Failure and cancellation prevent downstream activation.

## Deletion / non-deletion decision

- **Deleted by design:** no workflow event log, scheduler database, durable program counter, recovery store, or workflow-specific lifecycle adapter was added.
- **Reused:** `WorkItemRepository`, `DurableChildFactory`, `SessionRegistry`, Product Session replay, artifact storage, and existing provider adapters remain authoritative.
- **Narrow owner addition:** `DurableChildFactory.child_states(parent_work_item_id=...)` exposes sorted retained child state through the existing child owner. Optional workflow binding fields were added to `ChildSpec` and are validated both before persistence and during retained-state decoding.
- **Not exported from `breadboard.product.runtime`:** the workflow module depends on coordination types, while coordination imports the runtime event module. Keeping the workflow API at its owning module avoids a package-initialization cycle.

## Exact verification

Red was observed before implementation:

```text
pytest tests/product/runtime/test_durable_children.py::test_workflow_decision_replays_across_controller_restart -q
# ModuleNotFoundError: No module named 'breadboard.product.runtime.workflows'
```

Focused behavior proof:

```text
pytest tests/product/runtime/test_durable_children.py -k 'workflow or malformed_core_identity' -q
# pytest: 25 passed, 145 deselected, 9 warnings
```

The tests cover a real spawned controller process terminated between workflow steps, restart-equivalent and structurally identical decisions from existing owner persistence, unchanged Work Item event counts across that process death, projection version/source lineage, pre-delegation retained-child repair, terminal-child owner repair after an injected crash boundary, dependency activation and terminal completion, serialized lexical activation while another step is active, definition-drift rejection, failed/canceled precedence, concurrent controller activation with one child start, external artifact-store lock layout, canonical retained target/result references, cycle rejection, and malformed retained workflow identity rejection. The full durable-child suite is the integration gate recorded in the PR evidence.
