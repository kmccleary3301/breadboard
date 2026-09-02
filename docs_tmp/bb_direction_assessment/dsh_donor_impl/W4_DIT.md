# W4.2 / DIT-PROJECTION — three designs for W10 as-of/lineage

## Decision frame

This note follows `DSH_DONOR_IMPLEMENTATION_PLAN.md#5-design-doctrine-deep-modules` and the W4.1 evidence in [`W4_CHAR.md`](W4_CHAR.md). W10's concrete consumer is:

```console
bb research compare --definition E --world W --generation G --projection P --compare E,E_PRIME
```

W10 needs a historical fold of the named projection, exact source sequence/range, projector state version, typed out-of-range failure, replay-versus-durable comparison, and a cache-delete/rebuild proof. Current owners do not share input types or output lineage: `Session.restore` folds `KernelEvent`; `rebuild_work_item` folds `WorkItemEvent`; `CoordinationProjector.rebuild` accepts `WorkItem` handles; the TODO bridge maps a mutable `TodoStore.snapshot()` to an envelope. W4.1 found no current as-of API and no existing caller forced to branch across two projection adapters.

The designs below are alternatives, not an implementation proposal. No design has Kyle's selection.

## Three radically different designs

### A. Deepen each existing owner; share only a result vocabulary (recommended default)

**Interface.** Keep each domain entry point and its input authority. Each returns a common immutable result vocabulary, conceptually `Projected { value, projector_version, source: {stream, first_sequence, last_sequence}, as_of }`; each owner remains responsible for slicing/validating its own events. `as_of` beyond the stream is a typed owner error. W10 dispatches by the declared projection kind, not through a registry.

**Hidden implementation and seam.** `Session.restore`/`rebuild` continues to validate Session transitions; `rebuild_work_item` continues to validate Work Item policy; coordination continues to validate reciprocal graph edges. The shared value records only evidence needed by W10; it does not flatten domain rules or create a universal fold. Category-1 computations stay in their owners. Existing `_view`, `_snapshot`, and coordination `_view` remain disposable caches; W10 compares rebuilt values, never caches.

**Adapters, callers, deletion test.** There are three producers, but they are not interchangeable adapters: their source authority and semantics differ. The only named caller is future W10. No current caller can be migrated or deleted today. Once W10 exists, the owner-specific lineage assembly in its dispatch branch can be deleted in favor of the result vocabulary; that is a bounded deletion, not proof that a registry pays. Implementing this design before that caller exists would be a pass-through.

**Acceptance.** Strongest fit with W4.1: every owner can report exact sequence/range and version without inventing a common source model; W10 can reject a stale/out-of-range result, compare a hand-written expected value, and prove rebuild after deleting in-process caches. It preserves E3.2 because semantic validation remains at the emitter. Cost is touching each owner and the future W10 dispatch, with no migration of existing callers.

### B. One common projection interface over typed source streams

**Interface.** Introduce a projection port such as `fold(source: TypedSourceStream, as_of: SourceSeq | None) -> Projected`, plus a registry keyed by projection ID. Adapters would expose Session events, one or more Work Item streams, and a TODO source to the port; the registry supplies projector version and lineage uniformly.

**Hidden implementation and seam.** The port hides stream slicing, source-range bookkeeping, version mismatch, cache invalidation, and typed errors. It must also encode whether the source is one stream, a coordinated set of streams, or a mutable snapshot. That makes the interface learn all the facts W4.1 found to be different, or pushes domain rules into adapters and risks a second authority.

**Adapters, callers, deletion test.** This has at least three apparent adapters (Session, Work Item/coordination, TODO), satisfying adapter count only superficially. They are not two interchangeable production implementations of one behavior: coordination needs a set of repository-backed handles and TODO has no event sequence. No existing common caller or concrete deletion payoff is present. A real implementation would migrate W10 and every projection caller, add registry/adapter tests, and delete only dispatch code; it would not delete the domain folds or their authority checks. The migration cost and authority surface exceed the demonstrated leverage.

**Acceptance.** A registry could make W10's as-of/version checks uniform, but it cannot honestly guarantee E3.2/E3.6 without domain-specific escape hatches. It would need new source adapters, cache contracts, and unknown-projection errors before W10 can prove replay equality. This is precisely the E3.12 hypothesis, not an admitted seam.

### C. W10-owned composition over unchanged projector APIs

**Interface.** Add no projection seam. W10 reads durable sources, slices them itself, calls `Session.restore` and `rebuild_work_item` directly, invokes `CoordinationProjector.rebuild` for current handles, and wraps each result with command-local lineage/version metadata.

**Hidden implementation and seam.** W10 owns source discovery, as-of validation, projection selection, lineage wrapping, and report identity. Existing owners remain unchanged. The command must know that coordination takes live `WorkItem` handles rather than event streams; obtaining a historical coordination view would require temporary repository-backed handles or a second hand-coded coordination fold.

**Adapters, callers, deletion test.** There are zero new adapters and no existing caller to migrate. The deletion test fails: deleting the W10 composition would remove the command, but it would not simplify any existing caller; historical coordination would either be unsupported or duplicate `CoordinationProjector` logic. That duplication is a maintenance and authority cost, not a payoff.

**Acceptance.** This can prove Session/Work Item as-of only where their APIs can be called with sliced streams. It cannot prove one consistent coordination as-of without changing an owner or duplicating its graph rules, and command-local wrappers remain easy to forget for a new projection. It therefore fails the full W10 acceptance shape unless it grows into A or B.

## E3.12 resolution and recommendation

**E3.12 is resolved as REJECTED for a universal projection registry at this point.** W4.1 names multiple computations, but no common caller, two genuinely interchangeable adapters, or concrete deletion payoff. B's apparent adapter count is source-shape variety, not the §5.2 threshold; C has no seam. A shares a result vocabulary while retaining domain seams and therefore does not admit a registry or universal fold.

**Recommended default (not Kyle-selected): A.** It is the boring existing-owner cutover supported by the evidence: add only the lineage/version/result facts W10 actually consumes, keep each emitting domain authoritative, and avoid a generic registry until a real caller can show deletions. The recommendation is conditional on W10 being concrete enough to delete command-local result assembly; absent that caller, W4 should deepen owners only where a named acceptance case fails, not add a vocabulary for its own sake.

**Bounded W4.3 handoff.** If Kyle selects A, define the smallest owner-specific result records and typed `as_of` errors; migrate only W10; delete its duplicate lineage/version wrapping; then prove hand-written expected state, exact source range, version mismatch, replay equality, and cache deletion/rebuild equality. Do not add a Generation Runtime abstraction, alter Work Item/Session/Todo authority, or treat bridge/E4 projections as product truth. If Kyle selects another design, its implementation must first supply the missing adapter/deletion evidence and preserve the same W10 acceptance checks.
