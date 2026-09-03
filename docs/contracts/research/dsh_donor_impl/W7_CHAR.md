# W7.1 durable child-session characterization

Status: characterization only. Baseline is `b3cacc7356244253305f8a6f84308a993485bfe2` (the isolated worktree was created from this commit). No public schema/API or runtime implementation is changed here.

## Verdict

**PARTIAL.** FT-05 (`docs_tmp/bb_direction_assessment/dsh_donor_impl/W6_FT-05.md`) correctly found durable child support partial: the accepted `multi_agent` Definition can name team configuration and an event-log path, but compilation does not create a child Session identity. The current runtime can create a `JobRef`, append agent events, and optionally write those events as JSONL; it cannot make that job a durable child of a parent `SessionRecord`/product `Session`, recover its live activation after process death, or settle parent/child outcomes exactly once.

This is not `EXPRESSIBLE`: the required runtime facts are absent. It is not `INEXPRESSIBLE`: agent/job configuration, job identity, event-log persistence, Work Item lineage fields, and product-session durable storage are individually present. The missing composition is the result, not an invitation to invent a new public shape in W7.1.

## Current owning APIs and observed facts

| Concern | Current owner and evidence | Characterized boundary |
|---|---|---|
| Logical product Session | `breadboard/product/runtime/events.py:197-237`, `Session.start`, `Session.restore`, `rebuild` | A Session is one contiguous `KernelEvent` stream. Its lifecycle has no child-session, parent-session, job, worker, process, artifact-result, or recovery-reference field. |
| Product Session durability | `breadboard/product/runtime/session_store.py:1618-1653`, `load_session` at `1766-1787` | Event JSONL plus `session.json` can durably reconstruct a product Session when a caller explicitly calls `create_session`/`load_session`. This store is keyed by one `session_id`; it does not discover or attach orchestrator children. |
| Bridge record and restart | `breadboard_engine/api/cli_bridge/registry/records.py:200-238`; `registry/persistence.py:384-459,552-710`; `service.py:1181-1278` | `SessionRegistry` retains summary/status, turn IDs/digests, cancellation records, and terminal turn envelopes. It does not retain a product Session event stream, child IDs, JobRefs, process handles, child artifact ownership, or parent/root lineage. On reload, `_resume_retained_session` creates a new `SessionRunner`; unresolved turns are failed or canceled, queues are cleared, and no child is resumed. |
| Parent/child Work Item | `breadboard/product/coordination/work_items.py:153-160,195-215,275-359` | `WorkItem` has `parent_work_item_id`, `child_work_item_ids`, `Attempt.session_ref`, `WorkPlacement`, retry/resume/cancellation policy, and event-sourced delegation. `WorkItemRepository` at `138-152` is an in-memory repository; no bridge to `SessionRegistry`, product `Session`, JobManager, or durable disk restore is present. |
| Start-time Work Item evidence | `breadboard_engine/api/cli_bridge/runtime_emission.py:214-262,275-356` | Session-start records emit one synthetic Work Item whose parent is `None`, with lease/attempt `session_ref == session_id`; they are materialized runtime records, not a live WorkItem aggregate or child-session authority. |
| Agent owner | `breadboard_engine/orchestration/agent_session.py:18-96` (`OpenCodeAgent`) | Ray actor creates a random UUID `session_id`, LSP actor, and sandbox actor. Messages are memory-resident unless `enable_storage`/`persist_message` is explicitly used; that storage is keyed by the actor UUID, not a parent Session lineage. |
| Job/process owner | `breadboard_engine/orchestration/job_manager.py:11-70`; `orchestrator.py:27-116`; `breadboard/sandbox.py:310-458` | `JobManager` is in memory. `spawn_subagent` creates `JobRef` and `agent.spawned`/optional `agent.job_accepted`; `mark_job_completed` appends `agent.job_completed`. `DevSandboxV2.run_shell` owns a subprocess/process group only for the call and returns exit/stdout/stderr; no durable child-session binding or process recovery record is produced. |
| Orchestration replay | `breadboard_engine/orchestration/event_log.py:28-134`; `replay.py:10-42`; `orchestrator.py:909-956` | `set_event_log_path` plus `persist_event_log` can write JSONL; `load_event_log`/`EventLogReplay` iterate it and `_rebuild_runtime_state` restores in-memory `JobRef`s and cursors. Replay does not restore a product Session, child activation, Work Item aggregate, process, result owner, or exactly-once settlement. |
| Cancellation | `Session.cancel` (`events.py:225`), `SessionRunner._request_stop` (`session_runner.py:282-318`), `SessionService.cancel_turn` (`service.py:1045-1179`), `WorkItem.cancel` (`work_items.py:331-344`) | Parent bridge cancellation signals the parent runner/process and terminalizes admitted parent turns. Work Item cancellation can atomically append parent/descendant `work.canceled` events when the Work Item graph is present. Neither operation resolves a JobRef/OpenCodeAgent child or proves that a child process stopped and its result was not later committed. |
| Child navigation | `breadboard_engine/api/cli_bridge/session_control.py:715-767` | `session_child_next`/`session_parent` accepts caller-supplied IDs and returns a registry lookup. It does not validate a parent-child edge or create one; it is navigation, not lineage authority. |
| Artifacts/results | `breadboard_engine/api/cli_bridge/session_artifacts.py:88-131,174-187,337-459`; `api/public/session.py:120-155` | Live attachments belong to `SessionArtifactStore` in the runner; durable manifests/CAS are published only through the explicit session projection. After restart, a missing live runner yields an empty live artifact list, while mutations fail with `409 session runtime state is unavailable after service restart`. Job result payloads are only event payloads; no child result/artifact owner or parent join record exists. |

## Concrete deterministic restart scenario

Use fixed identities `parent-session`, `parent-work`, `child-agent`, `child-job`, `child-session`, and `child-result` and a local `state_root` plus workspace. The sequence is deterministic and provider-free:

1. Start a bridge parent with `SessionService.create_session`. The service creates a product `Session` (`Session.start`) and a `SessionRecord`; when primitive emission is enabled it also emits start-time Work Item records. The Work Item has `parent_work_item_id: null`; the SessionRegistry has only the parent record.
2. While the parent is running, call `MultiAgentOrchestrator.spawn_subagent(owner_agent="parent-agent", agent_id="child-agent", async_mode=True, task_descriptor={"result_ref":"child-result"})`. Observe one in-memory `JobRef` and `agent.spawned`/`agent.job_accepted`. No `SessionRegistry.create(child)` or `ProductSession.start(child)` occurs, and no Work Item `delegate` call is coupled to the spawn.
3. Start the `OpenCodeAgent`/sandbox child, then kill the engine process before `mark_job_completed` and before a parent join. If `persist_event_log` was not explicitly called, the orchestration events disappear; if it was called, only the JobRef/event facts survive.
4. Construct a fresh `SessionRegistry(state_root=state_root)` and `SessionService`. Retained parent lifecycle state loads, but only its bridge summary, turn IDs/digests, cancellation facts, metadata, and any terminal envelopes are present. `ensure_session` enters `_resume_retained_session`; the unresolved parent turn is deterministically `failed` (`runtime_failure`) or `cancelled` when cancellation was already requested, then the queue is cleared and a new runner is scheduled. No child Session/Work Item/Job/process is resumed or terminalized.
5. A parent event/artifact request reaches `api/public/session.py:_require_live_product_session`: it may load the durable parent product projection for validation, then returns `409` with `session runtime state is unavailable after service restart`. A live-artifact lookup has no runner and returns `[]`. Replaying the optional orchestration JSONL restores only in-memory JobRefs/cursors; it cannot recover the child activation or assign `child-result` to parent or child.

The focused existing executable proof for step 4 passes unchanged:

```text
uv run --no-project --with-requirements requirements.txt -- python -m pytest tests/api/public/test_session.py::test_public_session_restart_terminalizes_each_unfinished_turn -q
# pytest: 1 passed, 16 warnings in 11.36s
```

That test exercises the real retained-state/restart path and asserts the public `409 invalid_state` boundary plus failed/canceled unfinished turns. It does not claim child recovery; that missing child behavior is the characterization under test by the scenario above.

## Exact W7.1 gap and failure boundary

The gap begins at **child spawn**: `spawn_subagent` allocates a job/event identity, not a child `Session` identity tied to the parent. It becomes externally visible at **process death/restart**: no shared durable source can answer whether the child was running, paused, canceled, completed, failed, or had committed `child-result`; no API can rebuild the parent/child Work Item and Session lineage from retained state. The existing restart path intentionally fails/cancels unresolved bridge turns rather than replaying them, and public mutation rejects the unavailable runtime with `409`.

The boundary is therefore before any W7.2 design can compare alternatives. Do not count `parent_agent_id`, normalized `parent_session_id`, `child_session_id`, `JobRef.owner_agent`, `Attempt.session_ref`, or a JSONL event payload as proof of durable lineage: these are correlation fields owned by separate in-memory or projection surfaces, and none is a common durable authority.

## W7.2 design-comparison prerequisites

W7.2 must compare composition at the existing owners before proposing a supervisor/new seam. The comparison cannot start until each candidate answers, with one deterministic process-death fixture:

- one canonical child identity and immutable parent/root lineage, with an explicit mapping across `SessionRegistry`, product `Session`, Work Item/Attempt/Placement, JobRef, agent actor, and execution-world process;
- one durable recovery reference that is valid after a fresh engine process and identifies the exact replay head/config/Lock/workspace without ambient process or actor handles;
- explicit owner and schema for child terminal outcome, parent join/settlement, cancellation propagation, retry, and exactly-once conflict handling;
- result/artifact ownership and commit ordering, including partial output policy and proof that a killed child cannot publish a late result after cancellation or replacement;
- a common caller plus at least two real adapters/foundations, deletion payoff, and failure/replay oracle (the campaign's design-it-twice gate), while preserving the literal pending Kyle selection for any new seam;
- a deterministic oracle requiring no child/session event duplication: after kill/restart, the same lineage, terminal outcome, replay head, Work Item state, and artifact/result references must reconstruct exactly once.

Until these prerequisites are observed, durable children/workflows remain `FOG` and no public feature, schema amendment, SDK/TUI surface, or implementation seam is authorized.
