# North-Star recovery architecture specification

Status: approved planning direction; implementation remains subject to the gates in `EXECUTION_PLAN.md` and `LOOP_SPEC.yaml`.

Date: 2026-07-24

## 1. Purpose

BreadBoard will have one product path:

```text
Harness Definition
  -> validation
  -> deterministic compilation
  -> immutable Harness Lock
  -> product Session
  -> provider/tool/host/resource ports
  -> kernel events, work items, and artifacts
  -> CLI, HTTP API, Python SDK, TypeScript SDK, and TUI
```

E4 remains a separate internal evidence backend:

```text
Harness Lock + Lane Lock + frozen target inputs
  -> capture
  -> replay
  -> compare
  -> exact-scope support claim
  -> exported dossier or report
```

The evidence path may consume product locks, events, artifacts, and ports. Product runtime code may not import evidence campaign machinery. Campaign scorecards and progress ledgers never determine product behavior or support claims.

This specification owns domain boundaries, dependency direction, invariants, and target module ownership. `EXECUTION_PLAN.md` owns packet order and budgets. `LOOP_SPEC.yaml` owns execution state. `WORKER_CONTRACTS.md` owns worker inputs and outputs. `STATE.json` reports observed state. `GOAL_PROMPT.txt` is the fresh-session entry point.

## 2. Authority

Use current evidence in this order:

1. merged source at an exact commit;
2. executed tests and raw logs tied to that commit and environment;
3. frozen target captures with command, inputs, environment, and hashes;
4. validated replay, comparison, and claim artifacts;
5. generated inventories and locks reproduced from current authored inputs;
6. GitHub and Beads state;
7. exact-base/exact-head packet reports;
8. historical scorecards and progress ledgers;
9. prose reports and transcripts.

Historical evidence is immutable. A successor record may supersede its current disposition, but it cannot alter what an earlier artifact proved at its own head and time.

Primary references:

- `docs/DIRECTION_CHARTER.md`
- `docs/plans/phase_20_right_shape/FREEZE_POLICY.md`
- `contracts/public/operations.v1.json`
- `docs/conformance/e4_lane_inventory.json`
- `../docs_tmp/phase_19/pro_request_omp_pi_cleanup/BB_OMP_PI_NORTH_STAR_PRO_REVIEW_RESPONSE.md`
- `../docs_tmp/phase_25/BB_NS11A_PROGRESS.json`
- `../docs_tmp/phase_25/BB_NS11A_SCORECARD.json`

## 3. Current structural defects

The merged candidate product API has a useful product boundary, but it is not the default execution authority. The current checkout has these conflicts:

- `BREADBOARD_ENABLE_PUBLIC_API` defaults off.
- `BREADBOARD_ENABLE_E4_API` is effectively on unless explicitly set to `0`, `false`, or `no`.
- `contracts/public/operations.v1.json` calls all 45 candidate operations public.
- The mounted candidate public router exposes only the 26 product operations.
- `bbh harness run --local` clears the public and legacy flags and uses the compatibility `/v1/sessions` path.
- ordinary Python, TypeScript, and TUI clients mix product, compatibility, and E4 methods.
- the blocked replay branch couples records, serialization, isolation, workspace handling, adapters, publication, redaction, deadlines, and orchestration in one runner.
- packet budgets, head invalidation, review limits, and test derivation are documented but not mechanically enforced.
- current status is copied across tracker records, PRs, scorecards, and prose.

The recovery fixes ownership and execution order. It does not launch a broad repository cleanup.

## 4. Domain boundaries

### 4.1 Product

The product owns:

- Harness Definition and validation;
- deterministic compilation and Harness Lock;
- context and resource assembly;
- Session lifecycle and kernel events;
- provider, tool, host, resource, permission, and policy ports;
- work items and artifacts;
- the product CLI, `/v1` API, Python SDK, TypeScript SDK, and TUI.

The product path must run without E4, `docs_tmp`, campaign records, target captures, scorecards, or claim ledgers.

### 4.2 Internal evidence backend

The internal evidence backend owns:

- lane definitions and lane locks;
- replay plan and execution records;
- capture adapters and normalized observations;
- replay adapters;
- comparators;
- support claims and evidence manifests;
- redaction reports;
- target version freezes;
- claim reverification.

The current package location may remain `breadboard.product.evidence` during recovery to avoid a broad package migration. Its public status is determined by contracts and exports, not the directory name. A later package extraction requires a second real consumer.

### 4.3 Campaign control plane

Campaign tooling owns:

- work-packet state;
- review and CI routing;
- scorecards and progress ledgers retained as historical accounting;
- stale-pin and regeneration audits;
- tracker synchronization;
- bulk evidence regeneration.

Runtime and evidence libraries may emit evidence consumed by campaign tooling. They may not import campaign code.

### 4.4 Compatibility surface

Compatibility routes and client methods exist only to migrate active callers. They are absent from the product operation catalog and carry an owner, removal condition, and expiry packet. New product behavior cannot be added through compatibility modules.

Compatibility currently includes the legacy `/v1/sessions` bridge and related file, record, ctree, model, skill, command, attachment, and download routes; legacy health/status aliases; RL run routes; provider-auth routes; registry routes; and matching client methods. Local artifact put/delete and old CLI aliases are also compatibility-only.

## 5. Operation catalogs

### 5.1 Ordinary product catalog: 26 operations

| Family | Count | Operations |
| --- | ---: | --- |
| `artifact` | 3 | `get`, `list`, `verify` |
| `harness` | 7 | `create`, `explain`, `get`, `list`, `lock`, `update`, `validate` |
| `harness_lock` | 1 | `get` |
| `integration` | 3 | `get`, `list`, `probe` |
| `session` | 9 | `approve`, `artifacts`, `cancel`, `events`, `get`, `list`, `resume`, `send_input`, `start` |
| `system` | 3 | `describe`, `health`, `schemas` |

`integration.probe` stays in the product catalog, but product API activation requires an exact-head review of its capability checks, secret-reference handling, filesystem and network access, side effects, effect reporting, and denial behavior.

The current `contracts/public/operations.v1.json` remains the immutable historical 45-operation candidate catalog. The product cutover authors `contracts/public/operations.v2.json` as the 26-operation authority.

### 5.2 Internal evidence catalog: 19 operations

| Family | Count | Operations |
| --- | ---: | --- |
| `claim` | 4 | `evidence`, `get`, `list`, `reverify` |
| `lane` | 12 | `capture`, `claim`, `compare`, `create`, `get`, `list`, `lock`, `normalize`, `replay`, `run`, `stage_report`, `validate` |
| `lane_execution` | 2 | `cancel`, `get` |
| `lane_lock` | 1 | `get` |

The internal catalog lives at `contracts/internal/evidence/operations.v1.json`. It is not a public contract and is not an input to ordinary client generation.

`contracts/public/operations.v2.json` drives ordinary OpenAPI, SDK, TUI, CLI, and docs generation. `contracts/internal/evidence/operations.v1.json` drives only maintainer library/CLI surfaces and an explicitly enabled internal HTTP service.
Both current catalogs must be included in package data, load through installed-wheel resource APIs, and pass clean-install checks. Product loaders cannot fall back to the internal catalog, and internal loaders cannot widen the product catalog.

The existing 45-operation v1 catalog and its frozen hash remain historical evidence. The cutover creates the two current catalogs and a supersession record; it does not rewrite accepted historical artifacts.

### 5.3 Route defaults

The target defaults are:

- product API enabled;
- legacy compatibility disabled unless explicitly enabled;
- E4 HTTP API disabled unless explicitly enabled;
- E4 enablement accepts only exact documented true values (`1`, `true`, or `yes`, case-insensitive); unset and documented false values disable it, while unknown values fail closed and never mount routes;
- default OpenAPI excludes `/v1/e4/*`;
- ordinary SDK/TUI clients expose only the 26 product operations;
- evidence commands live under an explicit maintainer namespace;
- `system.describe` reports product operations and separately names enabled internal extensions.

The default product application must not import E4 router, lane, claim, catalog, ledger, or campaign modules at module load or app creation. E4 imports are lazy and occur only after true-valued internal enablement. Import-boundary negatives and default OpenAPI checks enforce this dependency rule.

The product API default changes only after the lock-first CLI, API, Python SDK, TypeScript SDK, and TUI journey passes at one exact head.

## 6. Authored and generated state

The direction is one-way:

```text
authored Harness Definition -> generated Harness Lock -> runtime execution -> evidence references
```

For E4:

```text
authored lane definition -> generated Lane Lock -> capture/replay/compare -> support claim
```

Rules:

1. generated locks cannot become authoring inputs;
2. evidence cannot rewrite product or lane locks;
3. scorecards cannot rewrite claims;
4. claims cannot rewrite captures;
5. generated clients and inventories are rebuilt from one authored catalog;
6. every stage reports `executed_pass`, `executed_fail`, `reused_stored_result`, `disabled_by_manifest`, or `not_applicable`;
7. reuse names the source artifact and validates every bound input digest;
8. `STATE.json` is regenerated from external authorities and is never edited as an authority.

## 7. Replay architecture

### 7.1 Retained behavior

Retain these ideas from the blocked replay branch:

- immutable replay records;
- canonical, content-derived plan identity;
- binding of every behavior-affecting input;
- mutation invalidates reuse;
- explicit binding verification;
- portable canonical paths and references;
- candidate and executed states are distinct;
- failed, canceled, timed-out, and integrity-failed executions are nonclaimable;
- stored artifacts cannot acquire executed status;
- only completed, integrity-verified executions may enter comparison;
- artifact manifests reject absolute and traversal paths;
- publication is staged, no-replace, rollback-safe, and redacted.

Treat the blocked test file as a behavior inventory. Port a test only when the new packet owns that behavior.

Discard the blocked runner, replay-specific production-provider expansion, opaque object reconstruction, monolithic worker/sandbox code, and production deadline races.

### 7.2 Target ownership

```text
breadboard/product/evidence/replay/
  plan.py             immutable identity record
  execution.py        execution state and claimability
  manifest.py         artifact graph and path safety
  admission.py        preflight and reuse admission
  errors.py           stable replay error taxonomy
  ports.py            replay-facing protocols
  coordinator.py      deterministic turn sequencing only
  publication.py      transactional handoff
  redaction.py        secret/path redaction report

breadboard/product/runtime/isolation/
  protocol.py         JSON request/result contract
  worker.py           bounded child lifecycle
  import_policy.py    frozen import allowlist
  limits.py           startup/runtime/resource limits
  os_sandbox.py       adapter to approved sandbox driver

breadboard/product/runtime/workspace_snapshot/
  materialize.py
  snapshot.py
  diff.py

breadboard/product/integrations/replay/
  provider.py
  tool.py
  host.py
```

The existing process and Docker sandbox drivers are implementation candidates behind `os_sandbox.py`. R1 first defines one narrow deterministic worker. It does not create a second platform-complete sandbox tree. Deprecated aliases and the legacy gVisor driver are handled only after behavior locks show whether they have active consumers.

R2 reuses the content-addressed, symlink-contained, staged publication behavior already proven by the product artifact/session path. It does not introduce an independent artifact store.

### 7.3 Dependency direction

```text
records and schemas
  -> replay ports and admission
  -> isolation and workspace snapshot
  -> provider/tool/host replay adapters
  -> coordinator
  -> transactional publication
  -> comparison and exact-scope claim
```

Forbidden dependencies:

- production provider runtime importing replay;
- isolation importing E4 lanes or campaign code;
- workspace snapshot importing provider or tool adapters;
- replay records importing FastAPI, SDK, TUI, or tracker code;
- coordinator selecting an OS sandbox implementation;
- execution code creating support claims;
- provider workers receiving a writable workspace;
- runtime code reading `docs_tmp`.

### 7.4 Replay state

A replay plan proves identity only. A replay execution proves what the worker did. Comparison proves a stated relation between an execution and a reference. A claim exports only the exact comparison scope.

Required transitions:

```text
planned -> admitted -> running -> completed
                    \-> failed
                    \-> canceled
                    \-> timed_out
                    \-> integrity_failed
```

Only `completed` with manifest integrity may advance to comparison. Reuse produces a provenance-bound reused result and cannot manufacture `running` or `completed` history.

### 7.5 Isolation invariants

- canonical JSON IPC only;
- no pickle or callable/object deserialization;
- explicit environment allowlist with no inherited secrets;
- inherited file descriptors closed;
- bounded startup, shutdown, and cancellation;
- frozen import binding enforced;
- default network and host-filesystem deny;
- workspace capability scoped per tool/host operation;
- child termination leaves no orphan process;
- capability and approval references recorded with side effects.

Production timeout categories, fallback policy, retry policy, cancellation races, and real transport conformance belong to the post-R4 provider-conformance packet.

## 8. Product activation

Activation requires one lock-first session journey through the same server and semantics:

1. `bbh harness validate`;
2. `bbh harness lock`;
3. `bbh harness run --local` using the product `session.start` contract;
4. session input, events, approval, cancellation, artifacts, and resume;
5. the same session type through the Python SDK;
6. the same session type through the TypeScript SDK;
7. TUI operation through the same generated client semantics;
8. default OpenAPI and `system.describe` agree with the 26-operation catalog;
9. E4 routes are absent by default and work when explicitly enabled;
10. compatibility mode is isolated and has a removal owner.

SessionRunner seam extraction precedes the default flip. The migration preserves current session behavior with exact-head tests; it does not rewrite the runtime in one packet.

## 9. Product and evidence falsifiers

### 9.1 Product falsifier

A clean-room participant receives the published package, public docs, and target harness docs/source. They receive no repository internals, private scripts, campaign transcript, or E4 requirement.

The product thesis passes only if the participant can create a session-running harness with:

- at most 150 lines of authored Harness Definition;
- at most 300 lines of adapter code;
- zero BreadBoard core edits;
- one CLI session and one Python SDK session;
- inspectable events and artifacts.

### 9.2 Evidence falsifier

A different clean-room participant receives the product result plus the documented maintainer evidence interface. They must freeze one target behavior, capture it, replay/compare it, produce one narrow claim, and reverify that claim without editing product runtime code.

A product pass does not imply an evidence pass. An evidence pass does not certify the ordinary product journey.

## 10. Completion conditions

The architecture is operational when all of these are observed at current exact heads:

- one product catalog drives the default product clients;
- E4 is internal and off by default;
- a default product import and app creation load no E4 runtime or campaign module;
- replay R0-R4 produces one completed deterministic execution and complete artifact graph;
- production provider conformance passes on named real routes before production replay claims;
- one exact-scope claim is generated from a real execution;
- the lock-first product path is the default across CLI/API/SDK/TUI;
- both outsider falsifiers have independent evidence;
- no unresolved P0/P1 correctness or security finding remains;
- compatibility paths have explicit removal packets;
- completion reporting states product readiness, active exact-scope claims, and packet state separately.
