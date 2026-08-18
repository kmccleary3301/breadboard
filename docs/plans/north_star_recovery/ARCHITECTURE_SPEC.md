# North-Star recovery architecture specification

Status: revised campaign direction; implementation remains gated by `EXECUTION_PLAN.md` and `LOOP_SPEC.yaml`.

Date: 2026-08-18

## 1. North star

An author can define and lock a harness once, run it through the default BreadBoard product surface, inspect durable results, restart or replay it safely, and—when needed—use a separate internal E4 path to make one exact-scope support claim.

```text
Harness Definition
  -> validate and compile
  -> immutable Harness Lock
  -> product Session
  -> provider/tool/host/resource ports
  -> durable events and artifacts
  -> CLI, HTTP API, Python SDK, TypeScript SDK, and TUI
```

E4 is a consumer of product records, not a second product runtime:

```text
Harness Lock + Lane Lock + frozen target inputs
  -> capture
  -> replay
  -> compare
  -> exact-scope claim
```

The product must run without E4, campaign state, `docs_tmp`, scorecards, or claim ledgers.

## 2. Authority

Use the smallest authority that answers the question:

1. merged source and authored contracts;
2. observed behavior from a command or test tied to that source;
3. a real target/provider run when the claim names that environment;
4. immutable lock, capture, comparison, claim, and release artifacts at their publication boundaries;
5. tracker and generated campaign state for coordination only.

Historical evidence remains immutable. `STATE.json` is a generated summary, not an authoring input or readiness gate. Hashes prove identity only; they do not prove behavior. Scorecards describe campaign accounting only; they do not certify the product.

Primary references:

- `docs/DIRECTION_CHARTER.md`
- `docs/plans/phase_20_right_shape/FREEZE_POLICY.md`
- `contracts/public/operations.v1.json`

## 3. Ownership boundaries

### 3.1 Product

The product owns:

- Harness Definition validation and deterministic compilation;
- Harness Lock creation and loading;
- Session lifecycle and kernel events;
- provider, tool, host, resource, permission, and policy ports;
- work items and artifacts;
- ordinary CLI, `/v1` API, Python SDK, TypeScript SDK, and TUI behavior.

### 3.2 Internal evidence backend

E4 owns:

- lane definitions and Lane Locks;
- captures and normalized observations;
- replay plans and execution records;
- comparators;
- exact-scope support claims and reverification;
- target freezes and redaction reports.

E4 is off by default and absent from ordinary OpenAPI, generated clients, TUI, and product docs. Its current package location may remain `breadboard.product.evidence`; public status is controlled by contracts and exports, not directory names.

### 3.3 Campaign control plane

Campaign tooling owns work selection, review and CI routing, external-run approval, and a concise status view. Runtime and evidence libraries may emit records consumed by campaign tooling; they may not import campaign code.

The control plane must not become a second product. It does not need a universal event registry, replicated source-digest graph, proof-of-proof chain, or a bespoke validator for facts already decided by tests, Git, CI, or the package manager.

### 3.4 Compatibility

Compatibility surfaces exist only to migrate known callers. They are excluded from the ordinary product catalog, have an owner and removal condition, and receive no new product behavior.

## 4. Operation catalogs and route defaults

### 4.1 Ordinary product catalog: 26 operations

| Family | Count | Operations |
| --- | ---: | --- |
| `artifact` | 3 | `get`, `list`, `verify` |
| `harness` | 7 | `create`, `explain`, `get`, `list`, `lock`, `update`, `validate` |
| `harness_lock` | 1 | `get` |
| `integration` | 3 | `get`, `list`, `probe` |
| `session` | 9 | `approve`, `artifacts`, `cancel`, `events`, `get`, `list`, `resume`, `send_input`, `start` |
| `system` | 3 | `describe`, `health`, `schemas` |

`contracts/public/operations.v1.json` remains immutable historical evidence. `contracts/public/operations.v2.json` is the authored current product authority.

### 4.2 Internal evidence catalog: 19 operations

| Family | Count | Operations |
| --- | ---: | --- |
| `claim` | 4 | `evidence`, `get`, `list`, `reverify` |
| `lane` | 12 | `capture`, `claim`, `compare`, `create`, `get`, `list`, `lock`, `normalize`, `replay`, `run`, `stage_report`, `validate` |
| `lane_execution` | 2 | `cancel`, `get` |
| `lane_lock` | 1 | `get` |

`contracts/internal/evidence/operations.v1.json` drives maintainer surfaces only. Both current catalogs ship as package data, load from an installed wheel, and never widen one another.

### 4.3 Defaults

- product API enabled;
- compatibility disabled unless explicitly enabled;
- E4 HTTP API disabled unless explicitly enabled;
- unknown E4 enablement values fail closed;
- ordinary generated clients expose only the 26 product operations;
- default imports and app creation load no E4 or campaign module;
- `system.describe` names product operations and any explicitly enabled internal extension separately.

The product default flips only after the installed lock-first journey passes through every ordinary client at one integrated candidate.

## 5. Authored and generated state

```text
authored Harness Definition -> generated Harness Lock -> execution -> evidence reference
authored lane definition -> generated Lane Lock -> capture/replay/compare -> exact-scope claim
```

Invariants:

1. generated locks never become authoring inputs;
2. evidence never rewrites product or lane locks;
3. claims never rewrite captures or comparisons;
4. generated clients and inventories have one authored catalog input;
5. reuse names and validates the behavior-affecting inputs it depends on;
6. failed, canceled, timed-out, integrity-failed, and stored-only results are not claimable;
7. historical artifacts are superseded by new records, never hand-edited.

## 6. Replay architecture

Replay is rebuilt as vertical behavior, not as one monolithic runner or a matrix of proof records.

Target ownership:

```text
breadboard/product/evidence/replay/
  plan.py          immutable identity
  execution.py     state and claimability
  manifest.py      artifact graph and path safety
  admission.py     preflight and reuse
  ports.py         replay-facing protocols
  coordinator.py   deterministic sequencing
  publication.py   transactional publication
  redaction.py     secret/path redaction

breadboard/product/runtime/isolation/
  protocol.py      canonical request/result IPC
  worker.py        bounded child lifecycle
  os_sandbox.py    adapter to an existing sandbox driver

breadboard/product/runtime/workspace_snapshot/
  materialize.py
  snapshot.py
  diff.py

breadboard/product/integrations/replay/
  provider.py
  tool.py
  host.py
```

Required execution transitions:

```text
planned -> admitted -> running -> completed
                    |-> failed
                    |-> canceled
                    |-> timed_out
                    `-> integrity_failed
```

Only an integrity-verified `completed` execution may be compared or claimed. Reuse preserves provenance and cannot manufacture execution history.

Isolation invariants:

- canonical JSON IPC; no pickle or callable deserialization;
- explicit environment allowlist with no inherited secrets;
- inherited file descriptors closed;
- bounded startup, cancellation, shutdown, and child cleanup;
- default network and host-filesystem deny;
- workspace capability scoped per operation;
- artifact publication rejects absolute, traversal, and escaping-symlink paths;
- publication is staged, no-replace, rollback-safe, and redacted.

Use existing process, Docker, sandbox, artifact, and session primitives behind these seams. Do not build a second platform-complete sandbox or artifact store.

## 7. Product journey contract

The same Harness Lock and Session semantics must work through:

1. `bbh harness validate`, `lock`, and `run`;
2. `/v1` harness, session, event, approval, cancellation, resume, and artifact routes;
3. the Python SDK;
4. the TypeScript SDK;
5. the TUI;
6. installed-wheel and packaged-client use outside the source checkout.

The journey must exercise a real provider when credentials are available and a deterministic local fixture otherwise. A local fixture proves product behavior, not production-provider support.

## 8. Outcome track and assurance track

### 8.1 Outcome track: blocking

The campaign is not complete until these observable behaviors work:

- one default product path and one current product catalog;
- one installed lock-first session journey across ordinary clients;
- durable events and artifacts survive restart and can be read through the public surface;
- replay completes one deterministic provider/tool/host journey with safe cancellation and publication;
- E4 captures, replays, compares, and reverifies one representative lane while remaining off by default;
- one named real provider/model/runtime passes conformance and supports one exact-scope claim;
- compatibility callers are migrated or have a concrete removal owner.

### 8.2 Assurance track: risk-driven

Additional provider matrices, target sweeps, soak tests, exhaustive lane coverage, independent outsider exercises, and extra provenance analysis are run only when they protect a named claim, release risk, or known failure mode. They may strengthen confidence or broaden a claim, but they do not replace a missing product journey and do not block unrelated product value.

## 9. Completion conditions

Completion is a decision, not a score. At one integrated candidate:

- every outcome-track behavior above is observed;
- focused security checks cover the isolation, secret, path, cancellation, and publication boundaries;
- relevant CI and one broad compatibility run pass;
- required generated clients are current;
- no unresolved P0/P1 product, security, or contract defect remains;
- live claims name exactly the provider, model, target, runtime, comparator, and exclusions exercised;
- remaining assurance work is either tied to a broader unmade claim or recorded as nonblocking follow-up.

Report product readiness, active exact-scope claims, compatibility removal work, and external limitations separately. Do not report a cumulative readiness score.
