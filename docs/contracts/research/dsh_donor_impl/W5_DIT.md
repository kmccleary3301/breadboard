# W5.2 DSH execution-world ownership designs

Status: Design 2 selected, implemented, and verified.

**Decision: SELECTED DESIGN 2 (Kyle McCleary)**

Owner: `@breadboard/execution-drivers`
## Question and decision invariant

W5.1 compares four execution-world labels (local process, Docker, Ray actor, and Slurm job) while keeping Product Session truth provider-neutral. W5.2 asks where the execution-world implementation should own capability/placement selection, adapter dispatch, liveness, timeout, cleanup, and failure normalization.

The invariant for all three designs is the existing `bb.sandbox_request.v1` / `bb.sandbox_result.v1` pair. A provider name, Ray actor handle, container ID, Slurm job ID, node name, or scheduler state MUST remain adapter evidence, not Product Session payload or public schema. The common caller receives one provider-neutral result and the same kernel/session semantics regardless of world.

## Current execution graph (observed)

There are two real execution families today:

1. **TypeScript driver-mediated turns.** `sdk/ts-host-bridges/src/index.ts:runOpenClawEmbeddedViaBreadboard` (the tool branch at lines 567-611) and `sdk/ts-backbone/src/backbone.ts:BackboneSession.runToolTurn` (lines 92-117) are provider-neutral callers. Both ultimately call `sdk/ts-kernel-core/src/turns.ts:executeDriverMediatedToolTurn`. That function builds capability and placement, constructs the ordered driver list, calls `buildPlannedExecution`, chooses an execution adapter, validates `SandboxResultV1`, and turns it into tool call/outcome/render plus transcript events.
2. **Python Product Session and historical world pilots.** `breadboard/product/cli/harness.py:run` validates Definition/Lock, then `_server` (lines 145-225) starts a session through `BreadBoardClient`, consumes ordered events, and returns an `OperationResult`. `breadboard/product/runtime/events.py:Session` owns the ordered lifecycle (`start`, `input`, `tool_called`, `tool_completed`, `complete`, `fail`) and `JsonlEventSink` durability. The W5.1 fixture caller `docs_tmp/bb_direction_assessment/dsh_donor_impl/W5_EVIDENCE/run_world.py:run` creates this Session directly and invokes a selected world.

World-specific adapters already exist; these are not hypothetical:

- Local process: `sdk/ts-execution-driver-local/src/index.ts:executeLocalProcessSandboxRequest`, `makeTrustedLocalExecutionDriver`, and `trustedLocalExecutionDriver`; terminal lifecycle is `LocalTerminalSessionManager` in `sdk/ts-execution-driver-local/src/terminals.ts`.
- OCI/container: `sdk/ts-execution-driver-oci/src/index.ts:executeOciSandboxRequest`, `ociExecutionDriver`, and `makeOciExecutionDriver`; terminal lifecycle is `OciTerminalSessionManager` / `makeOciTerminalSessionDriver` in `sdk/ts-execution-driver-oci/src/terminals.ts`.
- Remote worker: `sdk/ts-execution-driver-remote/src/index.ts:executeRemoteSandboxRequest` and `makeRemoteExecutionDriver`; HTTP terminal lifecycle is `RemoteTerminalSessionManager` / `makeRemoteTerminalSessionDriver` in `sdk/ts-execution-driver-remote/src/terminals.ts`.
- Ray-backed Python actors: `breadboard/sandbox.py:DevSandboxV2` (`@ray.remote`) and `breadboard/sandbox_docker.py:DockerSandboxV2` (`@ray.remote`), selected by `breadboard/sandbox_driver.py:create_sandbox` and constructed by `breadboard/sandbox.py:new_dev_sandbox_v2`.
- Slurm: `breadboard/rl/phase3/scheduler.py:SlurmRunSpec`, `submit_slurm_run`, and `collect_slurm_run`; the stricter target episode path is `scripts/rl_phase5/run_f3_target_episode.py:run_f3_target_episode` and `_run_f3_target_episode_under_lease`.

The shared TypeScript seam is already real: `sdk/ts-execution-drivers/src/index.ts` owns `ExecutionDriverV1`, `TerminalSessionDriverV1`, `selectExecutionDriver`, `selectTerminalSessionDriver`, `buildPlannedExecution`, placement compatibility, side-effect expectations, evidence expectations, and unsupported-case construction. Its README explicitly says it does not own kernel truth, host bridge logic, or runtime implementation details, while the intended layering puts selection/planning there and backend request construction in driver packages.

## Design 1 — Kernel-core execution world

### Shape and common caller

Make `@breadboard/kernel-core` the complete execution-world owner. Keep `executeDriverMediatedToolTurn` as the provider-neutral common caller for one-shot tool work. Extend the same owner to the terminal path so `BackboneSession.terminals` calls a kernel-core world operation rather than selecting adapters itself.

Owner ordering:

`ts-host-bridges:runOpenClawEmbeddedViaBreadboard` or `ts-backbone:runToolTurn` -> `ts-kernel-core:executeDriverMediatedToolTurn` -> capability and placement (`buildExecutionCapabilityFromRunRequest`, `buildExecutionPlacement`) -> driver plan (`buildPlannedExecution`) -> selected adapter -> `SandboxResultV1` -> kernel tool outcome/render/transcript -> host callbacks/session projection.

For persistent sessions, `ts-backbone:BackboneTerminalApi.start/interact/snapshot/cleanup` remains the host-shaped entrypoint, but dispatch and terminal registry authority move beneath the kernel-core world owner.

### Real adapters

The existing local and OCI adapters are direct implementations: `executeLocalProcessSandboxRequest` / `trustedLocalExecutionDriver` at `sdk/ts-execution-driver-local/src/index.ts:94-167`, and `executeOciSandboxRequest` / `ociExecutionDriver` at `sdk/ts-execution-driver-oci/src/index.ts:135-200`. Remote is a third real adapter at `sdk/ts-execution-driver-remote/src/index.ts:130-215`. This design does not invent a fake adapter; it makes kernel-core the dispatcher that already imports these packages.

### Owner ordering, liveness, and timeout

The kernel owns capability-to-placement choice and driver ordering. Today the ordering is explicit in `turns.ts:220-225`: remote hint gives `[remote, oci, local]`, OCI hint gives `[oci, local, remote]`, otherwise `[local, oci, remote]`. This is preference order, not retry: `buildPlannedExecution` finds one compatible driver and there is no automatic second attempt after execution failure.

The kernel cannot currently promise a common timeout. `buildLocalProcessSandboxRequest`, `buildOciSandboxRequest`, and `buildRemoteSandboxRequest` all emit `timeout_seconds: null`; `DriverMediatedToolTurnOptions` has no timeout field. Local and OCI default executors wait for child exit without a timer. Remote alone has `RemoteExecutionHttpOptions.timeoutMs`, implemented with `AbortController` in `executeRemoteSandboxRequest`; an abort becomes a thrown timeout error. Terminal `settleMs` is only a post-interaction wait, not an execution deadline. A kernel owner would therefore need to define one timeout/cancellation policy before claiming parity.

Liveness is adapter-observed: local/OCI child or runtime exit produces a result; remote HTTP response/abort produces a result or thrown transport error; terminal drivers maintain their own registry and emit end records. Kernel validation is the result boundary. A returned non-completed result becomes a failed/timed-out tool state in `turns.ts:60-100` and `269-326`. A missing driver/backend throws an unsupported error before a sandbox result exists. Terminal interaction and cleanup catch adapter exceptions in `ts-backbone/src/terminals.ts:641-684` and `821-842`; start and snapshot failures currently propagate.

### Failure boundaries and concrete deletion/avoidance

This gives one obvious failure boundary at `executeDriverMediatedToolTurn`: adapter transport/runtime failures are converted into validated `SandboxResultV1` when possible; unsupported placement/backend is an explicit kernel error; transcript semantics remain kernel truth.

Adopted literally, it should delete the private `chooseDriverMediatedPlacement` and `buildDriverExecutionAdapter` split in `sdk/ts-kernel-core/src/turns.ts`, because the world owner would expose one operation. It should also remove direct local/OCI/remote runtime imports from kernel-core and avoid a second terminal dispatcher in `sdk/ts-backbone/src/terminals.ts`. Until that cutover, adding another dispatcher would be duplication, not progress.

### Migration cost and platform effects

Migration is low-to-medium for current TypeScript callers: preserve the `executeDriverMediatedToolTurn` call shape, move its internals, then migrate terminal dispatch. It is high for Ray/Slurm: each would need a TypeScript-facing driver that owns external identity, polling, cancellation, cleanup, and evidence, while kernel-core would remain free of `ray`/`sbatch` imports. `breadboard/sandbox_driver.py:create_sandbox` and `breadboard/rl/phase3/scheduler.py:submit_slurm_run` could stay as separate Python pilots, but they would not automatically participate in the same world owner.

For future W10 composition, the good property is a single kernel call and one result contract. The risk is that kernel-core grows orchestration, backend lifecycle, terminal registry, and scheduler policy together; each new world increases kernel-core's dependency surface and makes provider-neutral kernel code less deep.

## Design 2 — Shared execution-drivers world (recommended for Kyle's consideration)

### Shape and common caller

Make `@breadboard/execution-drivers` the deep execution-world owner. It already owns the real selection/planning seam; deepen that seam to a small world operation that accepts a validated capability, placement, command/session operation, and adapter registry, then returns a validated result or a typed unsupported/failure outcome. Keep `ts-kernel-core:executeDriverMediatedToolTurn` as the provider-neutral common caller, but reduce it to kernel truth and transcript construction. Keep `ts-backbone:BackboneSession.terminals` as the host-shaped caller, but route start/interact/snapshot/cleanup through the same world owner.

Owner ordering:

`ts-host-bridges:runOpenClawEmbeddedViaBreadboard` or `ts-backbone:runToolTurn` -> thin `ts-kernel-core:executeDriverMediatedToolTurn` -> `@breadboard/execution-drivers` world operation (capability/placement compatibility, owner ordering, lifecycle, timeout/cancellation, cleanup, evidence expectations) -> concrete adapter -> `SandboxResultV1` or terminal result -> kernel transcript/events -> host projection.

This preserves the README's intended direction: kernel decides required capability; execution-drivers chooses a compatible driver and planned execution; driver package owns backend-shaped work; kernel interprets the result without backend-private lifecycle knowledge.

### Real adapters

The same three real adapters prove this seam is not hypothetical: `trustedLocalExecutionDriver` and `executeLocalProcessSandboxRequest` (`sdk/ts-execution-driver-local/src/index.ts:94-167`), `ociExecutionDriver` and `executeOciSandboxRequest` (`sdk/ts-execution-driver-oci/src/index.ts:135-200`), and `makeRemoteExecutionDriver` / `executeRemoteSandboxRequest` (`sdk/ts-execution-driver-remote/src/index.ts:130-215`). Persistent adapters are also real: `LocalTerminalSessionManager`, `OciTerminalSessionManager`, and `RemoteTerminalSessionManager` implement the shared `TerminalSessionDriverV1` shape.

### Owner ordering, liveness, and timeout

The world owner owns a declared, deterministic adapter order. For ordinary calls it can preserve current preference (`local -> OCI -> remote`), and honor explicit driver hints without treating a failed execution as permission to rerun elsewhere. A registry entry is selected once by `selectExecutionDriver` / `selectTerminalSessionDriver`; retries, if ever allowed, must be an explicit world policy rather than accidental fallback.

The world owner should normalize the currently uneven semantics without changing public schemas: carry an internal deadline from the caller into adapter execution, retain `SandboxRequestV1.timeout_seconds` as the existing nullable field, and distinguish deadline expiry from non-zero process exit and transport failure. Local/OCI adapters would enforce the deadline around their child/runtime process and perform bounded termination; remote would retain `AbortController` and map abort to the same timed-out result category. Terminal `settleMs` remains a settle interval; world-level deadline and signal cleanup remain separate operations.

World liveness is the world owner's responsibility to report, but backend identity stays evidence: local process close, OCI runtime/container close, remote HTTP response or abort, and terminal registry transitions are adapter observations. Cleanup is attempted once at the owner boundary and returns cleaned/failed IDs. A late result cannot rewrite an already-terminal session. The kernel receives only validated result/error data and remains responsible for `ToolExecutionOutcomeV2`, transcript, and event ordering.

### Failure boundaries and concrete deletion/avoidance

The failure boundary is intentionally narrow: driver selection/build errors are typed unsupported cases; adapter execution failures are normalized by the world owner; cleanup failures are retained with the primary failure; kernel-core sees a single result and never catches backend-specific exceptions. This is deeper than a pass-through because it centralizes ordering, timeout, cleanup, evidence, and terminal lifecycle once.

The cutover deletes `buildDriverExecutionAdapter`, `chooseDriverMediatedPlacement`, and the concrete-driver imports/list construction in `sdk/ts-kernel-core/src/turns.ts:133-225`. It removes the repeated concrete-driver list and placement policy from `sdk/ts-backbone/src/terminals.ts:315-417`, while retaining its session-view/projection logic. It avoids adding Ray/Slurm conditionals to kernel-core or backbone; they become new adapters registered at the world seam only when they are real.

### Migration cost and platform effects

Migration is medium: first move existing one-shot dispatch and terminal lifecycle behind the existing `ExecutionDriverV1` / `TerminalSessionDriverV1` seam, then migrate callers without changing `SandboxRequestV1`, `SandboxResultV1`, or public Product Session forms. Existing local, OCI, and remote tests can continue to exercise the adapter interfaces. A Ray adapter can wrap `DevSandboxV2` or `DockerSandboxV2` through a narrow host bridge while preserving actor/node/resource identity as evidence. A Slurm adapter can wrap `SlurmRunSpec`/`submit_slurm_run` plus a real polling/cleanup implementation; `collect_slurm_run` alone is not sufficient liveness because it only checks whether `slurm-{job_id}.out` exists.

This design keeps Ray an execution backend rather than a kernel dependency and keeps Slurm scheduler ownership with its adapter. For W10 composition, a composed operation can ask the world owner for one provider-neutral execution result, while composition-specific policy stays above it. Adding a world changes one registry/adapter seam and its evidence, not every provider caller. That is the strongest locality/depth tradeoff among the three designs.

## Design 3 — Product Session / Python world owner

### Shape and common caller

Make the installed Product Session runtime the execution-world owner. `breadboard/product/cli/harness.py:run` and `_server` are the provider-neutral common caller: they admit the locked Definition/task, start one Session, consume its ordered stream, and return a public `OperationResult`. `breadboard/product/runtime/events.py:Session` remains the durable lifecycle authority. A world coordinator beneath Product Session selects local process, Docker/Ray actor, or Slurm job, then records only provider-neutral tool/session events; runtime IDs and scheduler evidence stay in external evidence files.

Owner ordering:

`breadboard/product/cli/harness.py:run` -> `_server` / `BreadBoardClient.start_session` -> `Session` + `JsonlEventSink` -> Product execution-world coordinator -> Python or scheduler adapter -> command/result and external liveness evidence -> `Session.tool_completed` plus `complete`/`fail` -> ordered event stream/public projection.

The W5 fixture caller `docs_tmp/bb_direction_assessment/dsh_donor_impl/W5_EVIDENCE/run_world.py:run` already demonstrates the provider-neutral shape by creating one Session and swapping a world actor. Production adoption would have to bring that shape under the installed CLI/service path; the fixture itself is not the production owner.

### Real adapters

This design uses real Python adapters, not placeholders. `breadboard/sandbox.py:DevSandboxV2.run_shell` is a Ray-decorated local-process actor, and `breadboard/sandbox_docker.py:DockerSandboxV2.run_shell` is a Ray-decorated Docker actor using `docker run --rm`, `/workspace`, and `network="none"`. `breadboard/sandbox_driver.py:create_sandbox` is the existing explicit selector, while `breadboard/sandbox.py:new_dev_sandbox_v2` is the higher-level constructor. Slurm is a second independent adapter family: `breadboard/rl/phase3/scheduler.py:submit_slurm_run` submits `sbatch --parsable`, and `collect_slurm_run` reads the output path; the target wrapper has stronger lease/cleanup behavior in `scripts/rl_phase5/run_f3_target_episode.py:_run_f3_target_episode_under_lease`.

### Owner ordering, liveness, and timeout

Product Session owns the terminal lifecycle event ordering and durable append. Python local `DevSandboxV2.run_shell` defaults to 30 seconds, uses `subprocess.communicate(timeout=timeout)`, then sends process-group SIGTERM, waits up to two seconds, and escalates to SIGKILL; timeout is reported as exit 124. Docker `DockerSandboxV2.run_shell` also defaults to 30 seconds and reports exit 124, but delegates process cleanup to the Docker CLI/daemon after `subprocess.run` timeout; `--rm` is the cleanup contract. Ray actor creation and each actor method completion must be observed by the caller; actor/node/resource identity is external liveness evidence.

Slurm submission has a different current contract: `submit_slurm_run` fails on missing payload, non-zero SSH/sbatch, or unparsable job ID, writes a submission receipt, and returns. `collect_slurm_run` reports only output-file existence; it does not impose a polling deadline or prove terminal scheduler state. A real Product world owner would need a bounded `squeue` active observation, `sacct` terminal observation, cancellation, and orphan cleanup before treating a job as complete. The W5.1 contract already names those observations; it does not make them part of Product Session payload.

### Failure boundaries and concrete deletion/avoidance

The Product boundary is clear for Session truth: actor construction/command failure becomes `tool_result(error=true)` then `session.failed`; successful command becomes `tool_result(error=false)` then `session.completed`. CLI/service exceptions become `OperationResult.failure`, while malformed event streams remain product errors. However, failures before an actor or job has a durable correlation can be outside the Session stream unless the coordinator admits a failure event first. Slurm scheduler failure and stale output-file observations must not be silently treated as command failure or success.

The cutover would delete direct TypeScript local/OCI/remote backend selection from `ts-kernel-core/src/turns.ts` and replace it with one RPC/bridge adapter into Product Session, and delete the duplicate world preference plumbing in `ts-backbone/src/terminals.ts`. On the Python side it would consolidate `sandbox_driver.py:create_sandbox`, fixture-side actor construction, and Slurm submission under one coordinator instead of letting each caller select a world. It avoids duplicating Product Session terminalization in Ray actors and avoids putting Slurm IDs or Ray handles into public Session events.

### Migration cost and platform effects

Migration is high for current TypeScript callers: `SandboxRequestV1`/`SandboxResultV1` would cross a language/service seam, remote HTTP already has one transport but local and OCI callers would need a Product bridge, and terminal session operations would need durable correlation with the Python Session owner. Existing Product CLI behavior and W5.1 Ray/Docker evidence align naturally, but the currently maintained TypeScript driver packages become clients of a Python owner rather than reusable execution adapters.

Ray and Slurm fit this design most naturally as Python-native worlds. The cost is that Ray's actor lifecycle and Slurm's scheduler lifecycle become part of a Product runtime coordinator that also owns lock/task/session durability. For future W10 composition, Product Session gives strong end-to-end event evidence, but composition code must cross into the Product service for every execution and learn its session/stream protocol. That is a larger interface and weaker locality for TypeScript composition than Design 2.

## Comparison and recommendation

| Criterion | Design 1: kernel-core | Design 2: execution-drivers | Design 3: Product/Python Session |
| --- | --- | --- | --- |
| Existing deep seam | `executeDriverMediatedToolTurn` already dispatches | `ExecutionDriverV1` + `buildPlannedExecution` already select/plan | `Session` + CLI already durably order product events |
| Real adapters | TS local, OCI, remote | TS local, OCI, remote; Ray/Slurm can be added as adapters | Python Ray local/Docker and Slurm scheduler are already real |
| Timeout truth today | Uneven; local/OCI no timer, remote HTTP timer | One owner can normalize while preserving adapter detail | Python local/Docker timer; Slurm needs real polling deadline |
| Ray effect | Requires TS bridge; Ray stays outside kernel | Add a Ray adapter without kernel dependency | Native fit, but couples all callers to Python lifecycle |
| Slurm effect | Requires TS scheduler adapter and evidence policy | Add scheduler adapter with explicit polling/cleanup | Native fit, but current `collect_slurm_run` is too weak alone |
| W10 composition | Simple call, growing kernel dependency surface | One small world interface; composition stays above it | Durable product stream, but cross-language/session protocol cost |
| Migration | Low-medium for TS, high for Ray/Slurm | Medium and localized | High for TS and terminal transport |

**Decision: SELECTED DESIGN 2 (Kyle McCleary)**

Design 2 (`@breadboard/execution-drivers` shared execution-world owner) is selected and implemented.

## W5.3 Selected Design 2 Module Card

- **Owner Package**: `@breadboard/execution-drivers` (`sdk/ts-execution-drivers/src/world.ts`)
- **Seam Function**: `createExecutionWorld(options): ExecutionWorldV1`
  - Unifies one-shot sandboxes (`execute({ kind: "sandbox", ... })`) and persistent terminals (`terminal_start`, `terminal_interact`, `terminal_snapshot`, `terminal_cleanup`).
  - Deterministic pre-start single world selection (never falls back after execution starts).
  - Common deadline / timeout signal (`AbortController` + timeout notification + bounded adapter cleanup).
  - Provider-neutral equality under timestamp mask; adapter liveness evidence kept as evidence, not public Product payload.
- **Concrete Adapters**:
  - Local process: `makeTrustedLocalExecutionDriver` (`@breadboard/execution-driver-local`)
  - OCI container: `makeConfiguredOciExecutionDriver` (`@breadboard/execution-driver-oci`)
  - Remote worker: `makeRemoteExecutionDriver` (`@breadboard/execution-driver-remote`)
  - Ray actor: `makeRayExecutionDriver` over `ScheduledExecutionBackendV1` narrow host bridge (`@breadboard/execution-driver-remote`)
  - Slurm: `makeSlurmExecutionDriver` + `makeSshSlurmBackend` with durable idempotent request receipts, direct `sbatch --parsable`, `squeue`/`sacct` observation, ambiguous-ack recovery, and `scancel` lifecycle (`@breadboard/execution-driver-remote`)
- **Migrated Callers**:
  - `sdk/ts-kernel-core/src/turns.ts:executeDriverMediatedToolTurn`
  - `sdk/ts-kernel-core/src/default-world.ts:createKernelExecutionWorld`
  - `sdk/ts-backbone/src/terminals.ts:createBackboneTerminalApi`
- **Deletions / Stale Seams Removed**:
  - Deleted duplicate concrete-driver list construction and ad-hoc fallback loops from `sdk/ts-kernel-core/src/turns.ts` and `sdk/ts-backbone/src/terminals.ts`.
  - Replaced manual adapter dispatching with injected `ExecutionWorldV1`.
## Evidence index

- `docs_tmp/bb_direction_assessment/dsh_donor_impl/W5_CHAR.md:22-69` — registered fixture, all four world labels, exact launch paths, liveness owners, and ordered event comparison rules.
- `docs_tmp/bb_direction_assessment/dsh_donor_impl/W5_EVIDENCE/run_world.py:20-69` — one Session with swappable local/Docker Ray actor world and terminal success/failure mapping.
- `sdk/ts-kernel-core/src/turns.ts:133-267` — current capability/placement choice, concrete driver ordering, plan, adapter dispatch, and unsupported boundaries.
- `sdk/ts-kernel-core/src/turns.ts:269-326` — provider-neutral tool outcome/render/transcript assembly from `SandboxResultV1`.
- `sdk/ts-execution-drivers/src/index.ts:15-102,129-312` — shared interfaces, selection, compatibility, planned execution, side-effect and evidence expectations.
- `sdk/ts-execution-drivers/README.md:3-36` — intended layering and explicit non-ownership of kernel truth/host/runtime details.
- `sdk/ts-backbone/src/backbone.ts:92-117` — host-shaped provider-neutral `runToolTurn` caller.
- `sdk/ts-backbone/src/terminals.ts:386-432,541-845` — current terminal driver list, lifecycle dispatch, catch boundaries, registry/session-view handling.
- `sdk/ts-host-bridges/src/index.ts:526-611` — provider-neutral OpenClaw embedded caller into `executeDriverMediatedToolTurn`.
- `sdk/ts-execution-driver-local/src/index.ts:94-172` and `src/terminals.ts:73-272` — real local execution and terminal adapter semantics.
- `sdk/ts-execution-driver-oci/src/index.ts:66-211` and `src/terminals.ts:92-217` — real OCI request/runtime and terminal adapter semantics.
- `sdk/ts-execution-driver-remote/src/index.ts:130-215` and `src/terminals.ts:87-292` — real remote HTTP timeout/error and terminal adapter semantics.
- `sdk/ts-execution-driver-remote/src/scheduled.ts` — scheduled-backend lifecycle, Ray/Slurm registration, bounded polling, result validation, and confirmed cancellation.
- `sdk/ts-execution-driver-remote/src/slurm.ts` — DSH-owned SSH Slurm submission, terminal scheduler observation, evidence references, and cancellation.
- `docs/contracts/research/dsh_donor_impl/W5_REMOTE.md` — exact pre-registered world mask; only `/occurred_at` and `/timestamp` may differ.
- `breadboard/product/cli/harness.py:89-237` — installed lock/session caller, ordered event consumption, and `OperationResult` failure boundaries.
- `breadboard/product/runtime/events.py:28-95,143-237` — durable JSONL sink, event transition validation, Session read model, and lifecycle owner.
- `breadboard/sandbox.py:34-75,310-475,698-738` — Ray local actor, timeout/process-group cleanup, and constructor/selector path.
- `breadboard/sandbox_docker.py:28-86,340-509` — Ray Docker actor, network/image/mount policy, timeout, and `--rm` runtime invocation.
- `breadboard/sandbox_driver.py:13-107` — explicit process/Docker actor selection and typed selector failures.

## W5.4 Four-world execution proof

One fixed unrestricted `SandboxRequestV1`
(`req:dsh:world:equivalence-v5`, `network_policy: null`) printed
`breadboard-dsh-world-v1` through the actual local process,
cached `python:3.11-alpine` OCI container, local Ray actor, and remote Slurm
adapters. Explicit network policies take only backends that can enforce them;
the Slurm backend rejects rather than silently weakening one. All four returned
the same provider-neutral `SandboxResultV1`: completed status, exit code zero,
stdout
`sha256:c0041fd90663154e18cf02d322869f9c04d2014c1f9b3b165dbdb5c3a2b1c795`,
empty stderr, and side-effect/evidence digest
`sha256:9ff5cd6be759737fa1ba8d5918b62b3f942c38ad4485970af3a0b29e1cfea24a`.
The user-specific local content-addressed store resolved the stdout and stderr
digests after each run, so these references are retained artifacts rather than
labels.

Provider-specific proof stayed outside that result. The Ray callback recorded
accepted, running, and completed observations for bridge process `75213`,
actor `4e22ed7a696bdacf9608d90701000000`, and node
`4fbaf175b1c0e652fe44839be508996df8e89bba0d3880ab6e07d2e2`. The Slurm
callback recorded job `38510` completing on `cnode-183`, with its owner-only
receipt, exact encoded submission command, launch log, stdout, and stderr
retained under `/shared/breadboard-dsh-world`.
