# W7.2 durable-child design-it-twice comparison

Status: design comparison only. Baseline: `b3cacc7356244253305f8a6f84308a993485bfe2`. This report follows `W7_CHILD_CHAR.md` (`PARTIAL`) and FT-05 (`W6_FT-05.md`, durable child `PARTIAL`). It proposes no public schema/API, generated binding, implementation, or new durable team-truth store.

## Decision frame

E4.12 requires durable children to be ordinary durable Sessions with stable parent/root lineage and exactly one terminal settlement. W7.1 found that the current `JobRef`/agent event log, bridge `SessionRecord`, product `Session`, and Work Item aggregate do not share those facts after process death. The comparison therefore treats the existing owners as the composition seam:

- Product `Session` (`breadboard/product/runtime/events.py:197-237`) owns one immutable logical event stream and terminal outcome.
- `session_store.create_session`/`load_session` (`breadboard/product/runtime/session_store.py:1618-1653,1766-1787`) owns durable product event/metadata projections.
- `SessionRegistry`/`SessionRecord` (`breadboard_engine/api/cli_bridge/registry/records.py:200-238`; `registry/persistence.py:164-212,384-459,552-710`) owns bridge admission, turns, cancellation receipts, and retained summary state.
- `WorkItem`/`WorkItemRepository` (`breadboard/product/coordination/work_items.py:138-160,195-215,275-359`) owns assignment, attempts, leases, parent/child Work Item IDs, placements, retry/resume/cancellation policy, and event replay. The current repository is in memory.
- Agent/job/process owners are `OpenCodeAgent` (`breadboard_engine/orchestration/agent_session.py:18-96`), `MultiAgentOrchestrator` (`breadboard_engine/orchestration/orchestrator.py:27-116`), `JobManager` (`breadboard_engine/orchestration/job_manager.py:11-70`), and `DevSandboxV2.run_shell` (`breadboard/sandbox.py:310-458`).

### Common provider-neutral caller

All three designs use the same *internal* caller, shown here as a design contract rather than a public type:

```text
DurableChildCaller
  start(parent_session_id, root_session_id, parent_work_item_id, child_spec)
    -> ChildActivation(child_session_id, child_work_item_id, attempt_id,
                       recovery_ref, execution_target_ref)
  reconcile(recovery_ref) -> ChildState
  cancel(child_session_id, expected_revision, reason) -> ChildState
  settle(child_session_id, expected_revision, outcome, result_refs) -> ChildState
```

`child_spec` contains the locked config/task and explicit result/artifact policy; it never contains a provider credential or ambient actor/process handle. The caller must perform the same ordering and idempotency checks regardless of adapter. It is an internal test seam, not a public contract; a new durable supervisor seam remains pending Kyle selection.

### Two real adapter/foundation pairs required by every candidate

Each design is viable against both pairs below; neither pair is a fake provider abstraction.

| Pair | Existing foundation | Adapter responsibility in the design |
|---|---|---|
| Ray agent/job | `MultiAgentOrchestrator.spawn_subagent` + `JobManager` (`orchestrator.py:58-116`, `job_manager.py:28-70`) and `OpenCodeAgent` Ray actor (`agent_session.py:18-96`) | Map returned `JobRef` and actor/sandbox launch to the canonical child identity/recovery reference; turn `agent.job_completed` into the common `settle`, never into an unowned parent result. |
| Execution-world process | `sandbox_driver.create_sandbox` (`sandbox_driver.py:53-107`) with `driver="process"` / `DevSandboxV2` or `driver="docker"` / `DockerSandboxV2`; process execution is `DevSandboxV2.run_shell` (`sandbox.py:310-458`) | Record the execution target and process-group/container observation in the child placement/recovery facts; reconcile exit/absence after restart and submit only one terminal settlement. |

The process pair may be exercised as local process and Docker variants; Docker retains `network="none"`. The Ray pair and process pair are independently viable candidates for each design, not claims that today's code already satisfies the caller.

## Design A — Session-centered child factory

**Shape.** Keep child lifecycle at the existing Session/bridge owners. A private child factory called by `SessionService`/`SessionRunner` allocates a child `SessionRecord` and ordinary product `Session`, then asks the common caller to activate it. `WorkItem.delegate` supplies assignment and parent Work Item lineage; it does not become a second child truth.

**Authoritative facts and ordering.**

1. Allocate stable `child_session_id`, `root_session_id`, `child_work_item_id`, and `attempt_id`; persist parent/child registry metadata with immutable parent/root IDs and a recovery reference.
2. Append `work_item.created` with `parent_work_item_id`, parent `child.delegated`, child lease/attempt, and `placement.attached` using `Attempt.session_ref == child_session_id` and an explicit execution target. Start the child product `Session` through `session_store`.
3. Invoke either adapter pair. The adapter may report live actor/process IDs only as volatile observations; the durable recovery reference is the child session/work-item/attempt identity plus locked config/workspace references.
4. Store result bytes/artifact manifests before settlement. Append the child Session terminal event and Work Item terminal event under expected revisions; then append the parent join/child-result reference. A repeated settle returns the existing terminal outcome and cannot append a second one.

**Restart/replay and cancellation.** On a fresh process, `SessionRegistry` loads parent and child records, `load_session` reconstructs both product Sessions, and `WorkItem` replays both streams. `reconcile` observes the execution target: a live target is reattached, an absent target is converted to one failed/canceled settlement according to durable policy. Cancellation persists the parent request first, calls `WorkItem.cancel` for propagated descendants, then signals the adapter. A late adapter completion loses the expected-revision race and cannot publish a result after cancellation.

**Owners and adapter viability.** `Session` owns child logical truth; `SessionRegistry` owns child bridge identity/admission and retained recovery metadata; `WorkItem` owns assignment/attempt/placement and parent-child edges; `OpenCodeAgent`/`MultiAgentOrchestrator`/`JobManager` or `DevSandboxV2`/`DockerSandboxV2` are execution adapters only. Both adapter pairs can use the same identity, result, and settlement ordering.

**Migration, deletion, and impacts.** Lowest migration risk: wrap the existing `SessionService` creation and `SessionRunner` transition paths, add child metadata to retained state, and replace direct orchestrator completion with `settle`. The current parent-only start-time Work Item records and unbound `JobRef` completion path become removable once callers use the factory. No W10 workflow service is needed; W10 can project child Sessions and Work Items. Internal use has no public impact. Any child navigation/events exposed to clients require the normal `PUBLIC-AMEND -> schema -> generated bindings -> SDK/TUI -> E4` chain.

**Failure risk.** Two durable streams (child Session and Work Item) need an explicit commit protocol. Without expected revisions and a recovery reference, a crash between artifact publication and terminal events can duplicate results. This candidate is viable only if the process-death fixture proves the ordering and exactly-once oracle.

## Design B — WorkItem-centered delegation aggregate

**Shape.** Make the Work Item stream the lifecycle coordinator for assignment, activation, cancellation, retry, and parent join. Every delegated Work Item gets an ordinary durable child Session, but `SessionRegistry` becomes a bridge/index reconstructed from Work Item facts rather than the owner of child coordination. The current `WorkItemRepository` is replaced behind its existing read/append/restore interface with an authenticated durable adapter; no separate team journal is introduced.

**Authoritative facts and ordering.**

1. `WorkItem.delegate` creates the child Work Item and `child.delegated`; lease, `attempt.started`, and `placement.attached` reserve the canonical child Session/recovery identity.
2. The durable Work Item adapter commits the child assignment before adapter launch. The child factory creates `SessionRecord` and product Session using the IDs already present in the attempt/placement; mismatch is a hard error.
3. Adapter results are written to content-addressed storage, then a `child.result.recorded`-equivalent Work Item event (or existing terminal payload extension selected during implementation) commits the result reference, followed by child Session terminalization and parent join projection.
4. Retry creates a new attempt and recovery reference; it never reuses a lease, attempt, or child Session identity. Settlement is keyed by child attempt/revision.

**Restart/replay and cancellation.** Recovery starts from durable Work Item streams, rebuilds parent/child edges, and loads each child Session by `Attempt.session_ref`. `SessionRegistry` rehydrates bridge records from those canonical facts and retains only admission/cancellation receipts. A canceled Work Item atomically includes descendants under its cancellation policy; the caller then signals each adapter. A child terminal event whose attempt is no longer current is rejected, so a late process cannot win after retry or cancellation.

**Owners and adapter viability.** `WorkItem` owns lineage, attempt state, cancellation/retry, and join readiness; product `Session` owns child conversation/event truth; `SessionRegistry` owns bridge-facing record and client lease state; agent/job/process owners remain adapters. Ray/JobManager/OpenCodeAgent can emit placement and settlement observations; process/Docker sandbox can emit target liveness and exit observations. Both pairs satisfy the same Work Item expected-revision protocol.

**Migration, deletion, and impacts.** Strongest deletion payoff for workflow work: W10 can consume one replayed Work Item projection for scheduling, retry, join, and open-child checks. It requires a durable Work Item adapter and migration of existing in-memory repository callers/tests; old synthetic runtime Work Item snapshots and parent-only registry assumptions can then be removed. No public change is necessary for an internal pilot. W10 must not add a workflow service until Work Item replay and child Session replay agree; public workflow state follows the public chain.

**Failure risk.** This candidate risks making `WorkItem` a shallow pass-through to Session and process state, or duplicating terminal truth between Work Item and Session. It is viable only if the comparison oracle proves that Work Item is the deepest existing owner and that Session remains the ordinary durable child event stream, not a second scheduler journal.

## Design C — Durable-child supervisor composition

**Shape.** Add a narrowly scoped internal `DurableChildSupervisor` as a coordinator over the existing Session, SessionRegistry, Work Item, and execution owners. It owns no independent durable team truth: durable facts remain child Session projection plus Work Item stream; supervisor memory is rebuildable. This candidate is the explicit supervisor option from `DIT-CHILD`, and must be rejected if process-death evidence does not prove a shared lifecycle needs it.

**Authoritative facts and ordering.**

1. Supervisor reserves a child identity and writes the parent/child SessionRegistry metadata, child Session start projection, and Work Item delegation/attempt/placement facts.
2. It records a recovery checkpoint/reference in the child Session metadata and Work Item placement, then invokes the selected adapter. The checkpoint names exact Lock/config/workspace/result policy and an expected revision, never a PID as authority.
3. It drives a two-phase settle: adapter observation/result manifest is prepared first; child Session + Work Item terminal facts commit with one settlement key; the supervisor then emits the parent join projection. If the supervisor dies between phases, a fresh supervisor replays the two durable owners and finishes or aborts the one pending settlement.
4. It exposes `reconcile` and `cancel` through the common caller, serializing parent cancellation, child signal, adapter observation, and terminal commit.

**Restart/replay and cancellation.** On restart, the supervisor scans retained child Session and Work Item identities, checks the expected revision and recovery reference, and asks the adapter pair to report whether its target is live. It reattaches a live target or commits one deterministic failure/cancellation for an absent target. Parent cancellation writes the durable cancellation intent before signaling; any completion after the intent is a conflict, not a second result. Parent terminalization waits for child settlement or records an explicit unresolved-child outcome selected by the later contract; it never silently declares the child complete.

**Owners and adapter viability.** Supervisor owns orchestration ordering only; `Session` owns logical child events/outcome; `SessionRegistry` owns bridge admission and retained metadata; `WorkItem` owns assignment/lineage/attempt/placement; `MultiAgentOrchestrator`/`JobManager`/`OpenCodeAgent` and `sandbox_driver` process/Docker foundations provide execution observations. Both Ray and process-world adapter pairs can satisfy the supervisor's recovery/settle interface without becoming authorities.

**Migration, deletion, and impacts.** Highest short-term leverage if the process-death fixture exposes a real cross-owner transaction gap: direct calls from `SessionRunner`, `MultiAgentOrchestrator`, and process launch code become one coordinator call, and ad hoc completion/cancellation branches can be deleted after migration. Highest seam cost and maintenance risk: a supervisor can become a fourth source of truth unless every durable field maps to Session or Work Item. W10 consumes the same projections and remains blocked until child replay is proved. No public impact for internal use; any public supervisor status or child event requires amendment, schema generation, both SDKs/TUIs, and E4.

**Failure risk.** A supervisor is unjustified if A or B can pass the process-death oracle by composing existing owners. Hidden supervisor state, a second journal, orphan process, or duplicate settlement kills this candidate. The literal pending Kyle selection must remain until that evidence exists.

## Comparative scorecard

| Criterion | A: Session-centered factory | B: WorkItem aggregate | C: Supervisor composition |
|---|---|---|---|
| Deepest existing owner | Session + explicit Work Item coordination | Work Item for lifecycle; Session for child events | Coordinator over both; depth depends on strict projection-only design |
| Interface size/depth | Small factory/reconcile/settle interface; moderate internal commit logic | Same caller, but durable Work Item adapter adds persistence details | Same caller, but coordinator owns hardest ordering and recovery |
| Ray + process/Docker viability | Viable with one child identity protocol | Viable with Work Item expected revisions | Viable, but only if liveness/recovery adapter facts are complete |
| Restart/replay | Load child Session + Work Item; reconcile target | Replay Work Item first, then child Session | Replay both through supervisor and continue pending settlement |
| Cancellation/late result | Registry intent + Work Item propagation + Session terminal | Work Item revision is primary conflict fence | Supervisor serializes intent/signal/settle; most failure modes |
| Artifact/result owner | Child Session artifact manifest; parent gets immutable ref | Work Item records result ref; child Session retains event payload | Child Session/Work Item only; supervisor stores no durable result truth |
| Migration/deletion payoff | Lowest; fixes child composition first | Highest for W10 scheduling/join | Highest only when a proven cross-owner transaction needs it |
| New seam risk | Low to medium | Medium (durable Work Item adapter) | High; must preserve literal pending Kyle selection |
| W10 impact | W10 waits for replayed child projections | W10 has the clearest Work Item projection | W10 still waits; supervisor is not a workflow service |

## Recommendation and gate

**Recommendation: Design A — Session-centered child factory**, because E4.12 explicitly makes child Sessions ordinary durable Sessions, and it adds the least new ownership while allowing Work Item to retain assignment/lineage. Design B is the fallback if a process-death fixture proves Work Item must own settlement conflict resolution. Design C should be admitted only if that fixture proves that A/B cannot close the cross-owner ordering without a coordinator. These are recommendations, not approval.

**Decision: PENDING KYLE**

Before implementation or a public amendment, run one deterministic kill/restart fixture against both adapter pairs and require byte/digest equality for child/root lineage, replay head, Work Item attempt/placement, terminal outcome, cancellation, and artifact/result references. Reject any design with a second durable team truth, orphan process, ambient PID/actor authority, missing recovery reference, duplicate settlement, or late result after cancellation/replacement. W10.1 remains closed until this gate passes; public rollout remains closed until the normal amendment and generated-consumer chain passes.
