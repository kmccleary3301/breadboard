# W7.5 Workflow controller design comparison

## Decision frame

W7.4 found two authoritative runtime inputs: the immutable workflow definition supplies the DAG, while replayed Work Item streams supply lifecycle and join state and `DurableChildFactory` supplies child identity, recovery, and settlement. The controller must survive process death, reproduce the hand-written rule table, and add no durable workflow truth.

## Design A — recomputed WorkItem/child controller

Expose one `advance()` operation over an immutable DAG definition plus a pure `project_workflow_decision()` fold. The projection reads parent/child Work Item event streams and matching retained child bindings, returns exact W4-style source cursors, and computes the next rule-table decision. The effect edge reconciles an active child or starts the lexically first ready step. A workflow-definition hash and step ID are retained in the child record before launch. A controller restart supplies the same definition and recomputes the same decision.

- **Hidden:** cycle validation, definition hashing, retained-binding validation, reconciliation, ready-set selection, and per-workflow process locking.
- **Deletion/avoidance:** no caller-side ready-set loop, no workflow journal, no scheduler shadow state.
- **Risk:** the controller must reject conflicting or duplicate retained bindings and serialize start-once across processes.

## Design B — WorkItem decision events

Add `workflow.decision` events to the parent Work Item stream and replay those commands after restart. The event records each ready/start/wait/terminal choice before execution.

- **Benefit:** an explicit decision audit trail.
- **Cost:** decisions become a second durable truth beside dependency, join, and child lifecycle facts; replay must reconcile recorded intent with effects.
- **Deletion:** caller decision loops disappear, but a new intent/effect recovery protocol is required.
- **Risk:** crash gaps and duplicate start commands; wider WorkItem event algebra.

## Design C — SessionRegistry workflow records

Retain a workflow object in `SessionRegistry` containing definition, current step statuses, and pending actions. The controller updates it around child operations.

- **Benefit:** direct lookup and simple UI projection.
- **Cost:** duplicates WorkItem dependencies/joins and child Session state in a third owner.
- **Deletion:** none of the existing owners can be removed.
- **Risk:** three-way drift and restart repair policy; largest public-surface pressure.

## Comparison

| Criterion | A | B | C |
| --- | --- | --- | --- |
| New durable truth | None | Decision intent stream | Full workflow record |
| Restart oracle | Recompute from owners | Reconcile intent/effect | Repair duplicated snapshots |
| Start-once boundary | Workflow lock + atomic child binding | Event/effect transaction | Registry CAS + child transaction |
| Existing-owner depth | Strongest | Medium | Weakest |
| Public impact | None | WorkItem event expansion | Registry/API expansion |
| Deletion payoff | Caller scheduling loop | Caller loop | No authoritative deletion |

## Recommendation

**Select A.** It is the only design whose entire state is reconstructible from the two existing owners. B is justified only if an external consumer requires durable decision intent; none exists. C is rejected because it cannot delete either existing authority.

## Decision

**SELECTED A — recomputed WorkItem/child controller.** Kyle selected the recommendation through the plan-authorized ask-tool auto-selection on 2026-09-03. Designs B and C remain rejected.
