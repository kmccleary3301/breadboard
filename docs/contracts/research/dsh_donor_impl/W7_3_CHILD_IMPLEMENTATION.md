# W7.3 durable Product Session children

Status: implemented on the isolated W7 branch. Design A (Session-centered child factory) is selected. This internal implementation does not amend public schemas, generated bindings, SDKs, TUIs, or public registries.

## Module card

- **Boundary:** `breadboard/product/runtime/children.py:DurableChildFactory`.
- **Existing owners:** Product `Session`/`session_store` own child event truth and replay; engine `SessionRecord`/`SessionRegistry` retain child identity, recovery, cancellation, settlement and join metadata; `WorkItem`/`WorkItemRepository` own assignment, attempts, placement, parent/child edges and retry/cancellation policy; `ArtifactStore` owns result bytes.
- **Caller:** `start`, `reconcile`, `cancel`, `cancel_tree`, `prepare_result`, and `settle` are one provider-neutral internal caller. The locked `ChildSpec` persists only hashes and policy/config metadata, not raw task/message content or credentials.
- **Recovery identity:** retained `child_session_id`, `child_work_item_id`, `attempt_id`, immutable parent/root Session IDs, parent Work Item ID, recovery reference, adapter family and authenticated execution-target metadata are stored in the child `SessionRecord` metadata by `SessionRegistry`. Child Product Session restore uses `load_session`.

## Adapter coverage

`RayJobAdapter` adapts the existing `MultiAgentOrchestrator.spawn_subagent`/`JobManager` family. Job results are retained by the existing `JobRef` owner and the adapter normalizes accepted/running/completed/failed/killed observations, including completed result payloads. `ProcessExecutionAdapter` adapts an execution-world process and retains PID, platform process-start token and process-group identity; it validates those facts before observation or signaling, so a recycled PID cannot be treated as the child. Both use the same factory boundary and no fallback after activation.

The process adapter is a trusted POSIX process-group lifecycle boundary, not a sandbox or arbitrary OS-descendant containment boundary. Ordinary shell/background descendants in the authenticated group remain owned until they exit; a command that creates a detached session leaves that boundary and is not covered by cancellation or completion observations. `cancel_tree` traverses durable Session/WorkItem lineage, not arbitrary native process ancestry. A caller requiring containment must choose a world that provides it, rather than infer it from acceptance of an argv vector. The bounded detached-session probe observed group completion before the detached child exited naturally; no stronger guarantee is claimed.

## Ordering laws

1. Child `SessionRecord` identity/recovery metadata is retained before Work Item delegation. A crash before delegation leaves a discoverable, unlaunched retained child rather than an orphan target.
2. Placement and reserved execution-target metadata are persisted before adapter launch. The post-launch authenticated target observation is then CAS-updated in the same retained record.
3. Result/artifact bytes are written or verified through `ArtifactStore`; only verified digest references are retained. Completed settlement rejects an unprepared result.
4. Settlement reservation is CAS-committed on `SessionRecord` before Product Session or Work Item terminal mutation. Fresh `reconcile` completes that pending reservation and cannot append a second terminal outcome.
5. Cancellation intent is CAS-committed before signaling the adapter. `cancel_tree` persists the parent Session intent and every retained descendant intent before invoking any descendant signal. A late non-canceled result loses the revision/intent race.
6. Session terminal mutation, Work Item terminal mutation, and retained parent join/result reference are replayed through their existing owners. No private child lifecycle journal or parallel supervisor is introduced.
7. Completed results are acknowledged at the adapter before the terminal child record is published; execution-owner release follows that durable publication. Reconciliation, repeated settlement, and parent cancellation repair terminal owners before reporting success or clearing cancellation intent. A transient release failure remains retryable without a second settlement or parent join.
8. Process-control directory entries and their ancestors are durable before user-command release, including after adapter reconstruction. A failed durability barrier prevents launch. Concurrent equal job completions publish one terminal event and preserve the same replayed outcome.
- **Retry behavior:** A retryable absent execution target closes the current Work Item attempt and allocates a new attempt and placement before relaunch; the child Session identity remains stable across attempts while each attempt gets a new recovery reference. Retry policy, not the adapter, decides whether retry occurs.

## Deletion / non-deletion decision

- **Deleted:** the earlier private child event journal and parallel durable Work Item repository were removed. No duplicate child supervisor, provider-specific lifecycle policy, or direct parent-owned completion path remains in this boundary.
- **Not deleted:** existing `Session`, `session_store`, `SessionRecord`, `SessionRegistry`, `WorkItem`, `WorkItemRepository`, `ArtifactStore`, `MultiAgentOrchestrator`, `JobManager`, and process execution foundations remain authoritative in their existing domains. No public registry/schema/generated path was changed.
- **Owner-side additions:** `SessionRegistry.update_durable_child` is a small CAS operation on retained `SessionRecord` metadata, and persistence serializes/deserializes the whitelisted durable-child object. `WorkItemRepository` accepts an optional JSONL path while preserving its existing owner/reducer interface, so Work Item event replay survives process restart. `JobRef` retains the existing completion result payload for the Ray adapter; it is not a new team journal.
- **Retained summaries:** reward summaries round-trip with Session records; ordinary reads and registry replacement preserve them.

## Exact verification

Focused red was observed before implementation:

```text
uv run --no-project --with-requirements requirements.txt -- python -m pytest tests/product/runtime/test_durable_children.py::test_process_death_restarts_child_and_rejects_late_result -q
# ModuleNotFoundError: No module named 'breadboard.product.runtime.children'
# pytest: 1 error during collection
```

Final provider-free green after review corrections:

```text
uv run --no-project --python 3.11 --with pytest --with pytest-asyncio --with pydantic --with pyyaml --with jsonschema --with fastapi --with httpx python -m pytest tests/product/runtime/test_durable_children.py tests/test_cli_bridge_managed_state.py -q
# pytest: 242 passed, 11 warnings in 23.01s
```

`py_compile` passed for the touched Python modules. The focused tests cover kill/restart with a fresh retained `SessionRegistry` and Work Item repository, stable child Session lineage and per-attempt recovery identities, authenticated absent-target settlement and late-result rejection; cancellation ordering; prepared artifact/result before join; exactly one terminal outcome; retry/new attempt/recovery identity; startup abort/resume; settlement/status crash repair; provider-owned process launch identity recovery; checksummed Work Item torn-tail recovery; Ray late-completion immutability; service restart routing through the durable-child reconciler; and both Ray/job and execution-world process adapter families.

Against pre-fix source `52b624670f31035afae58d7ff1f29d54938fc1a4`, five retry/durability regressions fail and concurrent equal completion emits two events instead of one. The final gate covers recovery-token rotation across all retry branches, stale/malformed cancellation rejection, retained attempt/token consistency, fresh-adapter durability recovery, and atomic terminal publication. The initial full-dependency run stalled in live Ray invocation; an isolated unchanged-source diagnostic reproduced that Ray/uv environment failure. The command above deliberately exercises the provider-free component contract and does not claim a live Ray run.

## Remaining risks

The provider-free gate covers both logical adapter families, real POSIX process children, and Ray/job control semantics. It does not prove a live actor restart across a fresh Ray cluster; W5 owns measured execution-world acceptance. The trusted process-group boundary excludes detached descendants as stated above.
