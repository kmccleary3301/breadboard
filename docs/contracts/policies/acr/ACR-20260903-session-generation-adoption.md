# ACR-20260903-session-generation-adoption

- `acr_id`: `ACR-20260903-session-generation-adoption`
- `title`: Keep generation adoption inside existing runtime owners
- `author`: DSH donor implementation campaign
- `date`: 2026-09-03
- `status`: implemented

## 1) Problem Statement

A Product Session must retain the exact effective Lock identity that governed each trajectory segment. Provider or model-role reconfiguration must adopt a compatible generation only while the Session is quiescent, without creating a second generation authority.

## 2) Scope and Surfaces

- Kernel modules touched: Product Session events, CLI bridge Session control, model-role runtime, prompt compilation, and provider request metadata.
- Extension modules touched: none.
- Contract surfaces touched: internal generation identity, adoption history, and trajectory segments.
- Is this a **kernel danger-zone** change? `yes`
- Danger-zone: yes.
- Outside scope: a shared `Generation` abstraction, public schemas, SDKs, TUI rendering, deployment, and publication.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no.
- Cross-harness parity narrowed: no.
- Default endpoint semantics altered: no. Existing provider and config owners remain authoritative.
- Coupling risk score: `medium`. Session reconfiguration now coordinates existing Lock, provider, and model-role owners under the admission lock.

## 4) Change Classification

- Classification: `breaking` for the live-evolution integration below; the original internal change was non-breaking.
- Compatibility window: clean cutover; no public wire contract changes.
- Required schema/version bumps: none.
- Design decision: Kyle declined the proposed shared Generation seam. `GEN-ACTIVATION` is `NOT-TAKEN`.

## 5) Evidence and Validation Plan

- Required contract lane tests: Product Session replay, model-role reconfiguration, quiescence, rollback, stop/reconfigure serialization, and service publication.
- Required security tests: exact lowercase SHA-256 Lock identity validation and no provider-owned authority duplication.
- Required evidence: focused tests, exact-head independent review, and protected PR checks.
- Acceptance criteria:
  - Every Session exposes its pinned generation and ordered trajectory segments.
  - Compatible quiescent adoption appends one durable reconfiguration event.
  - Active-turn or malformed Lock adoption returns a typed refusal without mutation.
  - Failed live reconfiguration restores the prior provider/model role.
  - Racing input and reconfiguration cannot deadlock or cross the admission boundary.

## 6) Rollout Plan

- Rollout phases: merge internal Session/Lock adoption through protected checks; keep the shared Generation activation route closed.
- Flags/toggles: none.
- Blast radius constraints: existing Product Session and CLI bridge runtime owners only.
- Monitoring hooks: replay, role-switch, rollback, and request reconstruction evidence.

## 7) Rollback Plan

- Trigger conditions: replay divergence, admission deadlock, provider authority drift, untyped adoption failure, or protected check failure.
- Exact rollback commands: revert the protected-main merge with `git revert -m 1 <merge-sha>`; do not force-push `main`.
- Artifact/state restoration steps: no schema or data migration exists; restore the preceding runtime package artifacts.
- Post-rollback verification: rerun Product Session replay and model-role runtime tests.

## Retained permission recovery — 2026-09-05

Retained startup keeps `metadata.permission_mode` as the resolved public
projection, not as a new authored directive. It first rebuilds the implicit
request without feeding that projection back into permission defaults, then
tries the bounded projected mode only when the pinned generation requires it.
Generation mismatch refusal remains authoritative; no effective configuration
or provider-specific exception is persisted.

## 8) Approvals

- Kernel reviewer: independent exact-head review required.
- Contracts reviewer: protected compatibility and danger-zone gates required.
- Ops reviewer: protected branch checks required.
- Final decision: protected branch checks and repository merge policy control merge.

## W9 packaged-engine correction

The runtime graph hash recipe remains unchanged. Private Session state retains the original generation source reference separately from the current configuration-loading path. Recovery and explicit generation adoption compile with that retained input, then require exact agreement with the durable generation. The retained absolute reference is a hash input, not a file opened during recovery.

Older records without that input use the existing current-path reconstruction. The source reference is retained only after the full graph matches the pinned generation. Already-relocated historical records whose original source was never recorded still fail typed; no identity is guessed and no generation is silently adopted.

The regression covers new state and the old absolute-source recipe with the new private field absent. It first recovers old state at the same path, removes the original package directory, recovers under a replacement root, adopts a model generation, and recovers again. Each recovered state is compared with the preceding durable owner's snapshot. Real configuration drift remains rejected. No public schema or SDK signature changes.

## Live-evolution integration, 2026-09-10

AM31 in `docs/plans/phase_20_right_shape/SPEC_AMENDMENTS.md` supersedes the
original internal-only scope above. The accepted live-evolution campaign adds
compiler-owned executable packages and complete v2 Locks, durable publication
and admission, and public checkpoint/adoption operations. Sessions retain
history and adoption authority; domain and execution-world owners retain
effects and disposal. Python/TypeScript clients are generated from the public
operation catalog.

Kyle authorized review, PR creation and merge on 2026-09-10. This integration
also relocates old top-level harness profiles under `agent_configs/deprecated/`
and publishes a dated catalog. This is a versioned contract and config-path
cutover, not a non-breaking rename. Historical capture dates and support scopes
must not change merely because the catalog date changes.

Pre-merge review identified retained-Lock admission incorrectly consulting
mutable source, and separate CLI invocations sharing an admission request.
Explicit Lock execution now relies on verified captured bytes; implicit
Definition execution still refuses drift. Each CLI run has a fresh request
identity, while retries within that SDK request retain it.

Checkpoint transfers preserve the 256 KiB physical-frame bound and 1 MiB state
bound. Each binding retains its own input frontier through migration, durable
storage and resume; only the root binding is compared with the Session's input
frontier. Missing or malformed retained frontiers are refused, not inferred.
Compatibility reasons may be empty text. Terminal recovery uses the existing
durable Session persistence owner; raw terminal journals do not acquire private
authority merely by being replayed.

Required validation extends the existing campaign evidence with public
admission regressions, worker checkpoint roundtrips, binding-local frontier
recovery, config loading, generated-contract parity and protected PR checks.
The pre-fix checkout is the regression oracle; passing source-only construction
does not establish a successful transition.

The original no-migration rollback instructions do not apply to v2 records.
Preserve recorded history and executable closures. A rollback binary must
understand records already written; otherwise fix forward. Do not downgrade
Session storage or force-push main.
