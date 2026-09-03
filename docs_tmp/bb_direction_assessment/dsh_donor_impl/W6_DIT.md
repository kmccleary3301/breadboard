# W6.3 — Annotation-sidecar designs

## Scope and evidence

This report compares three radically different ways to add the W6 annotation sidecar under §5.2. It is a design comparison only: no Definition schema, public event schema, or production code is changed here. The current W6 characterization is decisive about the boundary: annotation is **INEXPRESSIBLE** in the closed Harness Definition/compiler contract ([`W6_FT-05.md:22-33`](W6_FT-05.md)), so the sidecar must be a runtime/persistence contract and must not be smuggled through `dossier` or raw resource inputs.

The recommendation below favors the deepest existing owner, not the smallest diff. Current durable session state already has immutable `KernelEvent`/`SessionView` values and an append-only `JsonlEventSink` ([`breadboard/product/runtime/events.py:28-188`](../../../breadboard/product/runtime/events.py)); durable mutation already loads and persists one `Session` under a session guard ([`breadboard/product/runtime/session_store.py:1604-1653`](../../../breadboard/product/runtime/session_store.py)). The public projection is a separate, explicit adapter ([`breadboard/product/runtime/public_event_projection.py:11-65`](../../../breadboard/product/runtime/public_event_projection.py)).

A prerequisite shared by all three designs is a canonical target identity. An annotation record must name the exact assistant/message or trajectory item it labels; a free-form turn number or array offset is not sufficient. Existing provider messages can carry an optional `message_id` ([`breadboard_engine/provider/contract_messages.py:139-153`](../../../breadboard_engine/provider/contract_messages.py)), but bridge turns currently persist input/turn IDs and attachments rather than a complete message/trajectory ledger ([`breadboard_engine/api/cli_bridge/registry/records.py:158-238`](../../../breadboard_engine/api/cli_bridge/registry/records.py)). The chosen design therefore cannot claim that identity is already solved.

## Design A — Product Session owns immutable annotation events (recommended)

### Interface and owner

The annotation is a first-class immutable kernel event, appended by the existing Product Session owner:

```python
@dataclass(frozen=True, slots=True)
class AnnotationRecord:
    annotation_id: str
    message_id: str
    trajectory_id: str
    label: str
    author: str
    generation: str

class Session:
    def annotate(self, record: AnnotationRecord) -> SessionView: ...
```

`Session.annotate` validates that the target identity belongs to this session/trajectory, rejects duplicate `annotation_id` and malformed labels before mutation, then appends one `annotation` `KernelEvent`. The event payload is immutable and contains the stable target IDs, label, author, and generation; it does not contain mutable transcript positions. The existing `Session.events`/`Session.restore` replay surface remains the read interface.

This is a real-owner route, not a proposed service name: `SessionRuntime` already sends mutations through `SessionMutationPort` ([`breadboard/product/operations/session.py:123-165`](../../../breadboard/product/operations/session.py)), `_DurableSessionMutationAdapter` already translates command mutations into `Session` calls and durable-store operations ([`breadboard/product/cli/session.py:434-548`](../../../breadboard/product/cli/session.py)), and `JsonlEventSink` plus `NullEventSink` are existing durable/no-op event-sink adapters ([`breadboard/product/runtime/events.py:28-90`](../../../breadboard/product/runtime/events.py)). This exceeds the two-adapter/caller threshold. The existing event projection can be extended by W9.2 rather than introducing a second public stream.

### Ordering and failure behavior

1. **Setup:** load or start the session through the existing mutation path; resolve the canonical message/trajectory identity; validate the record and authorization while holding the session mutation guard.
2. **Publication:** append the annotation event through `Session` and its sink; persist the session projection; only after the durable event is committed, translate it through the public projection and fan it out. The public event must carry the same session sequence/event identity, not a newly allocated annotation cursor.
3. **Replay:** `Session.restore` rebuilds the immutable view from the event log. `read_session_event_batch` already reads by sequence and `list_session_events` projects those events ([`breadboard/product/operations/session.py:394-473`](../../../breadboard/product/operations/session.py)); annotation replay follows that same cursor and ordering.
4. **Failure:** target/label/duplicate validation fails before append. A sink or session-projection failure yields no public annotation event. A crash after an immutable sink write is recovered by the existing JSONL replay path; a public projection failure is retried from the durable kernel event rather than re-appending the annotation.

### Exact deletion payoff

Once cut over, delete the standalone annotation writer, annotation-specific replay cursor, and sidecar-to-session merge/reconciliation code that would otherwise duplicate `Session._append`, `session_store.mutate_session`, and `JsonlEventSink`. Do not create an annotation database, manifest index, or second event sequence. No existing production line is deleted by this report; the payoff is the concrete avoided implementation surface above.

### Dependencies and migration cost

Dependencies are the existing `KernelEvent` kind/payload validator, `Session` mutation/rebuild, `session_store` guard/persistence, and `public_event_projection`. The current event kind allowlist and payload validation must gain one normative annotation shape ([`breadboard/product/runtime/events.py:93-132`](../../../breadboard/product/runtime/events.py)); W9.2 must add its public projection. A message/trajectory identity provider is still required; the design does not fabricate one from array position.

Migration is **medium**. Existing sessions need no fabricated backfill: they remain valid with zero annotation events. New writes need the identity contract and one mutation operation; durable replay must preserve old event logs; public consumers need the W9.2 amendment. The main compatibility risk is that provider IDs are optional, so an explicit “unaddressable target” failure is required instead of silently labeling the wrong message.

### W10 composition impact

W10 composition gets one annotation dependency at the existing session mutation boundary. It must compose the identity resolver and call the existing session mutation adapter, but does not need to provision a second store, cross-store transaction, or replay join. Composition should pass the same `SessionRuntime`/session store used for input and approval mutations; annotation durability then inherits the existing session lifecycle.

### W9.2 public consequence

W9.2 must amend the public session-event kind/payload allowlist with an `annotation` event. The payload must expose `annotation_id`, canonical target IDs, label/author/generation policy, and schema version with the same stable session sequence. Visibility/redaction rules are part of that amendment; the kernel event must not be made public merely because it is durable.

## Design B — ArtifactStore immutable sidecar references

### Interface and owner

The annotation body is a canonical JSON artifact. The session stores only an immutable reference and target identity:

```python
@dataclass(frozen=True, slots=True)
class AnnotationSidecarRef:
    annotation_id: str
    message_id: str
    trajectory_id: str
    artifact: ArtifactRef

class AnnotationSidecarStore(Protocol):
    def put(self, record: AnnotationRecord) -> AnnotationSidecarRef: ...
    def read(self, ref: AnnotationSidecarRef) -> AnnotationRecord: ...
    def bind(self, session_id: str, ref: AnnotationSidecarRef) -> None: ...
```

The natural implementation is Product `ArtifactStore`, whose CAS `put`/`put_json` is immutable and deduplicating ([`breadboard/product/runtime/artifacts.py:76-153`](../../../breadboard/product/runtime/artifacts.py)); `bind` would need a durable session/manifest index because an artifact alone does not belong to a session. This route has real storage adapters/callers: `SessionArtifactStore` owns live session attachment references and materializes manifests ([`breadboard_engine/api/cli_bridge/session_artifacts.py:88-191`](../../../breadboard_engine/api/cli_bridge/session_artifacts.py)), while the generic `InMemoryCAS` and `FilesystemCAS` implement immutable content-addressed storage for replay/durable environments ([`breadboard/artifacts/cas.py:43-57`](../../../breadboard/artifacts/cas.py), [`breadboard/artifacts/cas.py:97-226`](../../../breadboard/artifacts/cas.py)). Existing runtime composition also creates an `ArtifactStore` and writes effective operation policy ([`breadboard_engine/api/cli_bridge/runtime_emission.py:301-316`](../../../breadboard_engine/api/cli_bridge/runtime_emission.py)). These are real owners/adapters, but none currently bind an annotation target to a Product Session event.

### Ordering and failure behavior

1. **Setup:** validate the canonical target and label, serialize the record deterministically, and `put_json` into CAS. Verify digest, size, and media type before returning the sidecar ref.
2. **Publication:** bind the ref to the session/manifest under the session guard; persist that binding; only then emit one public annotation event containing the ref and target IDs. A public event before both CAS and binding commits is forbidden.
3. **Replay:** read the durable session binding, resolve the digest through the selected CAS reader, verify the returned bytes against the ref, then decode and validate the annotation. A missing or corrupt object is an explicit unavailable/error result, never a reconstructed label.
4. **Failure:** invalid records fail before CAS. A CAS collision/integrity failure publishes nothing. If CAS succeeds but binding fails, the immutable object is an orphan eligible for authenticated garbage collection; it must not become visible. If binding succeeds but public projection fails, replay retries publication from the durable binding.

### Exact deletion payoff

Once cut over, delete a separate annotation-row store and annotation-body serialization from the session registry; all large/structured label bodies use the existing CAS and manifest machinery. The exact avoided code is a second label database, second blob dedupe implementation, and second artifact reader. This does **not** delete the required session binding/index, orphan GC, or public event projection; those are new obligations of B.

### Dependencies and migration cost

B depends on Product `ArtifactStore` or a precisely selected CAS implementation, manifest/binding persistence, authenticated garbage collection, and a session/public-event pointer. It must not confuse Product `ArtifactRef` with the generic CAS `CASReader` contract: the two existing reference types and stores have different owners and APIs. The sidecar also needs authorization and visibility semantics; `SessionArtifactStore` currently handles attachment capability/manifest authorization, not arbitrary annotation labels ([`breadboard_engine/api/cli_bridge/session_artifacts.py:133-191`](../../../breadboard_engine/api/cli_bridge/session_artifacts.py)).

Migration is **medium-high**. Existing artifact manifests and sessions remain readable, but each new annotation requires a dual durable write (CAS plus binding), orphan cleanup, and a backfill/repair policy for partially committed bindings. Readers and exports must dereference refs, and all deployment modes must provision the same CAS namespace. A direct “put artifact and emit event” shortcut is not a valid migration because it loses session ownership and replay ordering.

### W10 composition impact

W10 composition must provision an artifact store/CAS for every annotation-capable session, configure its authorization and GC lifecycle, and compose a binding transaction with session persistence. Composition and replay now cross the session and artifact stores, so startup/recovery and test fixtures must supply both. The route is viable where artifacts are already the composition root, but it adds a cross-store dependency to the ordinary session path.

### W9.2 public consequence

W9.2 must add an annotation event whose payload carries a stable sidecar ref, canonical target IDs, and a dereference/authorization contract. The schema must define whether label contents are public or require a capability; public replay cannot assume that every consumer can read the CAS. Event ordering must document that the ref is durable before publication.

## Design C — Separate annotation ledger keyed by canonical identities

### Interface and owner

A new ledger owns annotation lifecycle and indexes records by canonical IDs rather than by session event order:

```python
@dataclass(frozen=True, slots=True)
class AnnotationReceipt:
    annotation_id: str
    message_id: str
    trajectory_id: str
    ledger_sequence: int

class AnnotationLedger(Protocol):
    def append(self, record: AnnotationRecord) -> AnnotationReceipt: ...
    def get_for_target(self, *, message_id: str, trajectory_id: str) -> tuple[AnnotationRecord, ...]: ...
    def replay(self, *, after: int = 0) -> tuple[AnnotationReceipt, ...]: ...
```

The ledger would be a new durable owner (JSONL/SQLite/service-backed), with an outbox or event adapter to publish session/public events. It **fails the current two-real-adapter/caller threshold (0/2)**: no existing annotation ledger or ledger adapter is present. `task_execution` and RL export are plausible future callers, but today the task fallback only publishes assistant messages ([`breadboard_engine/api/cli_bridge/task_execution.py:778-815`](../../../breadboard_engine/api/cli_bridge/task_execution.py)) and the RL exporter synthesizes trajectory IDs from run/task rows ([`breadboard/rl/export/verl.py:170-187`](../../../breadboard/rl/export/verl.py)); neither calls an annotation ledger. Creating both callers would be part of the design, not evidence of existing depth.

### Ordering and failure behavior

1. **Setup:** establish and persist the canonical message/trajectory identity registry, open a ledger transaction, and validate that the target belongs to the session/run. The ledger must define its own monotonic cursor and idempotency key.
2. **Publication:** append the ledger record, durably enqueue an outbox event, then publish the projected session/public event from the outbox. A direct ledger write plus independent event publish is not sufficient.
3. **Replay:** read the ledger cursor, resolve records by `message_id`/`trajectory_id`, and join them to session/trajectory events. Consumers must handle ledger events that arrive after the base trajectory and preserve the ledger cursor separately from the session cursor.
4. **Failure:** reject invalid targets before append. A ledger commit followed by outbox failure is recovered by outbox replay. Without an outbox/transaction, the two independent writes create an annotation-without-event or event-without-annotation split and are unacceptable.

### Exact deletion payoff

After migration, annotation fields and lookup branches need not be added to Product `Session` or `ArtifactStore`; those owners remain focused on transcript events and blobs. The exact avoided code is a new `Session` event payload branch and artifact binding index. That payoff is outweighed by the new ledger, identity registry, outbox, cursor, recovery, and W10 join code; it is not a deletion of existing implementation because no annotation path exists today.

### Dependencies and migration cost

C depends on a new durable ledger, canonical identity registry, outbox/transaction mechanism, authorization, cursor/replay API, and an adapter into public events. The current bridge registry persists turn/session metadata and terminal envelopes but no message/trajectory label ledger ([`breadboard_engine/api/cli_bridge/registry/persistence.py:265-459`](../../../breadboard_engine/api/cli_bridge/registry/persistence.py)); adding C is therefore a new persistence subsystem. The RL exporter also needs an identity handoff rather than its current synthesized IDs.

Migration is **high**. New and existing trajectories need stable identity mapping; producers must dual-write or reconcile during rollout; replay must merge two cursors; and recovery must repair ledger/outbox gaps. A backfill cannot safely infer provider message IDs where the provider omitted them.

### W10 composition impact

W10 composition must own a third durable dependency and its startup/recovery lifecycle, compose the identity registry with both session and trajectory producers, and expose a join for consumers. Every composition/test fixture that creates a session or trajectory must decide whether the ledger is required, optional, or unavailable. This is the largest cross-cutting impact and creates a new failure boundary in the normal write path.

### W9.2 public consequence

W9.2 must define a public annotation event sourced from the ledger/outbox, including the ledger cursor or a mapping to the session stable cursor. The schema must document eventual/atomic visibility, deduplication, and replay when the base session event and ledger event have different cursors. This is a materially larger public contract than A or B.

## Comparison and recommendation

| Criterion | A — Product Session events | B — ArtifactStore refs | C — Annotation ledger |
|---|---|---|---|
| Existing depth | **Deep:** Session, sink, store, operation adapter, and public projection already form one path | Medium: excellent CAS depth, but target binding and replay join are new | **Shallow:** no ledger owner or adapter exists (0/2 threshold) |
| Ordering/replay | One immutable session sequence and existing restore/cursor | Two durable stores; binding and CAS must be coordinated | Separate ledger cursor plus outbox and cross-stream join |
| Deletion payoff | Avoids standalone store, writer, cursor, and reconciliation | Avoids blob/row duplication, but still needs binding and GC | Avoids session/artifact annotation branches, but adds a complete new subsystem |
| Migration | Medium; no unsafe historical backfill required | Medium-high; dual-write/orphan/GC policy | High; identity backfill, dual-write, outbox, two cursors |
| W10 composition | One existing session mutation dependency | Session plus CAS/binding lifecycle | New ledger plus identity registry/outbox lifecycle |
| W9.2 public amendment | One annotation kind/payload in existing projection | Annotation kind plus ref authorization/dereference | Annotation kind plus cross-cursor/eventual-consistency contract |

**Recommendation: A.** It places the invariant where the current code already owns event identity, mutation serialization, immutable replay, and durable recovery. B is a reasonable fallback only if label bodies are proven too large or independently governed for session events; C is not the deepest existing owner and currently fails the adapter threshold. This recommendation does not choose the implementation or invent the missing canonical identity contract; it gives W7.1/W9.2 explicit follow-on seams.

## Decision

**Selected by Kyle on 2026-09-02: A — Product Session immutable annotation events.**

Session is the existing owner of event identity, serialization, replay, and recovery. B — ArtifactStore immutable sidecar references — and C — a separate canonical-identity annotation ledger — are not selected because they introduce additional ownership, cross-store ordering, or replay joins instead of deepening that owner.

- [x] **A — Product Session immutable annotation events:** one session sequence, existing durable replay, W9.2 extends the public projection.
- [ ] **B — ArtifactStore immutable sidecar references:** not selected.
- [ ] **C — Separate canonical-identity annotation ledger:** not selected.
