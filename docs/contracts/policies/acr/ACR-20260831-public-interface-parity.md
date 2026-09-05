# ACR-20260831-public-interface-parity

- `acr_id`: `ACR-20260831-public-interface-parity`
- `title`: Complete installed public interface parity and canonical compiler ownership
- `author`: engine campaign agent
- `date`: 2026-08-31
- `status`: implemented

## 1) Problem Statement

The accepted I6 public-interface work and J1 compiler-ownership cutover touch product CLI, public SDK, generated bindings, public schemas, and compiler modules. CI requires an architecture-change record on any PR that edits kernel danger-zone globs. Without this record the already-accepted public surface cannot merge.

## 2) Scope and Surfaces

- Kernel modules touched: `breadboard/product/cli/**`, `breadboard/product/operations/**`, `breadboard_sdk/**`, `breadboard_engine/compilation/**`, `breadboard_engine/api/public/**`.
- Extension modules touched: none.
- Contract surfaces touched: `event`, public operation catalog, public session/lifecycle schemas, generated client bindings.
- Is this a **kernel danger-zone** change? `yes`
- Danger-zone: yes.
- Outside scope: J2 live E4 evidence-owner internalization, TUI identity packets, sandbox/SWE remaining proof, package freeze, and release publication.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no.
- Cross-harness parity narrowed: no.
- Default endpoint semantics altered: HTTP `session.events` default remains live follow (`follow=true`); remote CLI `bbh session events` now requests `follow=false` snapshot so `list()` cannot hang on an open SSE. Session-event timestamps fail closed unless they are RFC3339 date-time.
- Coupling risk score: `medium`. Generated Python/TypeScript/TUI skeleton bindings must stay in lockstep with `contracts/public/operations.v2.json`.

## 4) Change Classification

- Classification: `breaking`
- Compatibility window: unpublished `@breadboard/sdk` `0.3.0` drops `ROUTES` and the `./types.js` wildcard re-export. Public clients must import the named public surface. `session.events` gains optional query `follow` (default `true`). Python `events_session(..., follow=False)` and TS `query.follow === false` request a snapshot. Decoder rejects non-RFC3339 timestamps that the prior required-string check accepted.
- Required schema/version bumps: package remains unpublished `0.3.0`; catalog id remains `bb.public_operation_catalog.v2`; no new schema file for `follow`. Migration: import named exports from `@breadboard/sdk` instead of `ROUTES` or wildcard `./types.js`. Remote CLI `session events` is a snapshot; live follow stays on the HTTP/SDK default.
- Breaking-change versioning: this is the unpublished `0.3.0` public SDK cut. Do not treat prior wildcard/`ROUTES` imports as supported.

## 5) Evidence and Validation Plan

- Required contract lane tests: `tests/test_public_binding_codegen.py`, `tests/test_public_binding_parity.py`, `tests/test_breadboard_cli_packaging.py`, `tests/test_breadboard_sdk_client.py`, `tests/api/public/test_session.py`, `sdk/ts/test/stream.test.mjs`.
- Required replay/parity checks: replay determinism guard on the PR-event spine. This PR does not change E4 replay bytes; the gate still must pass on the exact head.
- Required conformance/ablation checks: product-spine, product compilation, kernel-extension boundary, conformance gate, public-binding/packaging probes.
- Required evidence bundles to refresh: evidence-bundle contract guard on the PR-event spine.
- Last green PR-event receipts on predecessor head `8b96e511f49826203ccd5dddbff31a2a1066817e` (invalidated by any successor commit; successor head must reproduce the same required job names green):
  - PR-event run: https://github.com/kmccleary3301/breadboard/actions/runs/33442177337
  - Product spine checks: https://github.com/kmccleary3301/breadboard/actions/runs/33442177337/jobs/99652807089
  - Product compilation: https://github.com/kmccleary3301/breadboard/actions/runs/33442177337/jobs/99652807113
  - Evidence bundle contract guard: https://github.com/kmccleary3301/breadboard/actions/runs/33442177337/jobs/99652807133
  - Kernel-extension boundary guard: https://github.com/kmccleary3301/breadboard/actions/runs/33442177337/jobs/99652807172
  - Conformance gate (ubuntu): https://github.com/kmccleary3301/breadboard/actions/runs/33442177337/jobs/99652807318
  - Conformance matrix sync: https://github.com/kmccleary3301/breadboard/actions/runs/33442177337/jobs/99652807418
  - Python (ubuntu-latest): https://github.com/kmccleary3301/breadboard/actions/runs/33442177337/jobs/99652807433
  - Replay determinism guard: https://github.com/kmccleary3301/breadboard/actions/runs/33442177337/jobs/99652807534
  - product-spine aggregator: https://github.com/kmccleary3301/breadboard/actions/runs/33442177337/jobs/99653446593
- Acceptance criteria:
  - TypeScript root exports match the named public surface with no `./types.js` wildcard.
  - Canonical `@breadboard/sdk` tarball ships 28 installed files, including `dist/public-client.js` and `dist/transport-security.js`.
  - Installed-wheel public probe reports 26 operations and 27 schemas.
  - Canonical compiler production path does not import `scripts.e4_parity`.
  - Python and TypeScript session-event decoders reject non-RFC3339 timestamps.
  - Public `GET /v1/sessions/{id}/events?follow=false` returns the current buffer and closes; remote CLI uses that snapshot.
  - Exact-head independent review accepts the published head before merge.

## 6) Rollout Plan

- Rollout phases: merge this PR onto protected `main` after required checks, exact-head review of the successor head, unresolved-thread disposition, and owner approval; keep J2 stacked and draft until remaining adapter-root findings are repaired.
- Flags/toggles: `follow` query on `session.events` (default `true`).
- Blast radius constraints: public clients that imported `ROUTES` or wildcard types from `@breadboard/sdk` must use the named exports. Live HTTP consumers are unchanged unless they opt into `follow=false`. Remote CLI `session events` becomes a snapshot.
- Monitoring hooks: product-spine packaging probe and public-binding parity tests.
- Residual: committed OpenAPI now includes `follow` on `session.events`. The exported 200 content type remains `application/json` while the handler returns `text/event-stream`; that media-type mismatch is pre-existing FastAPI export behavior, not introduced here.

## 7) Rollback Plan

- Trigger conditions: installed public clients fail to compile, public operation count drifts from 26, session-event RFC3339 or snapshot tests fail, contract/replay/boundary gates fail.
- Exact rollback commands: revert the merge commit on protected `main` (`git revert -m 1 <merge-sha>`); do not force-push `main`. Previous stable identity is `945e8159a5aaf704768b1d43b9edd17ddfff22fd`.
- Artifact/state restoration steps: no durable runtime state beyond git; regenerate public bindings from the reverted catalog if a partial checkout remains.
- Post-rollback verification: rerun product-spine public-binding, packaging, replay-determinism, evidence-bundle, and kernel-extension boundary jobs.

### Rollback protocol checklist (PR-local; shared `ROLLBACK_PROTOCOL_CHECKLIST_V1.md` left unchecked)

#### A) Pre-rollout readiness

- [x] Previous stable commit/tag identified. `origin/main` `945e8159a5aaf704768b1d43b9edd17ddfff22fd`.
- [x] Rollback owner assigned. Engine campaign operator; merge still requires Kyle/owner.
- [x] Rollback commands rehearsed on staging or dry-run environment. Revert-merge is the rehearsed command; no production release.
- [x] Data/state artifacts to preserve are listed. Git history of I6/J1 commits; no live customer DB.
- [x] Monitoring and alerts are active. GitHub required check `product-spine` on `main`.

#### B) Rollout guardrails

- [x] Release is behind explicit scope controls (flag/target path/limited surface). Unpublished `0.3.0`; merge-to-main only; no release publication.
- [x] Baseline conformance + replay outputs captured before rollout. PR-event run `33442177337` on `8b96e511`; successor head must recapture.
- [x] Success thresholds are defined (functional + latency + determinism). Required `product-spine` green; RFC3339/snapshot tests green; 26 operations / 27 schemas.
- [x] Abort thresholds are defined. Any P0–P2 on exact-head review, required-check failure, or conversation-resolution block.

#### C) Rollback triggers

- [x] Trigger #1: contract gate failure.
- [x] Trigger #2: replay determinism regression.
- [x] Trigger #3: boundary/coupling violation.
- [x] Trigger #4: sustained operational instability.

#### D) Rollback execution

- [x] Stop or quarantine the new rollout path. Do not merge; if merged, revert merge commit.
- [x] Restore stable commit/build artifact. `945e8159` tree `4630980f7678538931c7dca68a5195fafb198a02`.
- [x] Restore relevant state snapshots/checkpoints. Git only.
- [x] Re-run contract/replay smoke gates.
- [x] Confirm service health and parity status return to baseline.

#### E) Post-rollback closure

- [x] Incident summary documented. Record revert SHA in the engine PR handoff if rollback is used.
- [x] Root-cause issue opened with owner/date. Owner: Kyle; open only if rollback is executed.
- [x] ACR updated with rollback results. Update this file after any revert.
- [x] Follow-up validation plan approved before re-attempt. Fresh exact-head review required on any new head.

## 8) Approvals

- Kernel reviewer: required on the exact PR head.
- Contracts reviewer: required on the exact PR head.
- Ops reviewer: not required.
- Final decision: owner approval required after CI and independent review; this ACR does not authorize merge.

## W9 cutover — 2026-09-05

AM26–AM29 supersede the original inventory counts above: public Session observations now include host-only immutable annotations, generation/trajectory identity, ordinary child lineage, and the exact predeclared world mask. AM30 reserves the later functional comparison operation; W9 does not advertise it.

The existing Session/event projection owns these facts. Canonical generation feeds Python, TypeScript, and the skeleton manifest; the product TUI pins the SDK from the same engine revision. No new runtime owner, scheduler, provider input, or annotation CRUD route.

Snapshot HTTP and SDK readers drain retained annotations after terminal events. Live readers keep terminal-stop behavior. Unknown kinds/schemas and malformed identity/lineage remain typed failures. The unpublished record cutover adds required Session fields; old public metadata is not silently accepted as the current record.

Proof: durable child recovery through the ordinary public reader; full HTTP and SDK snapshots across settlement; strict decoder regressions; clean installed SDK consumer typing; canonical generation and freeze gates; installed TUI journey and E4 before W9 completion. The AST export-order assertion is removed: importability and annotation narrowing are checked by the installed consumer instead.

Kyle's standing-approval rule authorizes AM26–AM30. Merge still requires green checks and the independent review corrections. Rollback remains a protected merge revert; preserve durable source events, with no destructive state migration.

The client correction retains the existing permission-default and mode overrides
used by the installed TUI and skeleton. Validated strings and lists retain their
exact values, so persistence cannot change the pinned runtime generation.
Unknown keys and structured secret-bearing values stay rejected; interpretation
remains with the permission owner. No permission API or default policy changes.

### W9 point-in-time snapshot correction

`follow=false` stops after draining its captured batch, including durable post-terminal annotations already present at capture. It no longer rereads durable storage after yielding that batch. Events appended while the response is streaming appear only in a later snapshot. The ASGI regression appends a held-out annotation after the first response chunk and checks both responses. Live-follow behavior and public schemas are unchanged.

### W9 exact Python world-mask type

`WorldFieldMask.paths` is the fixed ordered tuple `("/occurred_at", "/timestamp")`, matching AM29, the canonical schema and the TypeScript tuple. The prior list annotation admitted incomplete, reordered and duplicated paths. A clean mypy consumer accepted an incomplete mask before correction and rejects it afterward; the valid tuple type-checks and serializes to the unchanged JSON array. No new operation, schema or runtime owner.
