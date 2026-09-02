# W3.3 / DIT-GENERATION

## Scope and evidence

This note compares three radically different activation-owner designs for the existing config, provider, and plugin activation paths. It does not implement a common module. Current owners are:

- Config/Lock: `SessionService._runtime_lock`, `compile_runtime_effective_config_graph`, and the start staging/publish path in `SessionService._create_session`.
- Provider: `provider_router.get_runtime_descriptor` → `provider_registry.create_runtime` in `AgenticCoder`'s primary run (`agent_llm_openai.py`) and RLM provider subcall.
- Plugin: `discover_plugin_manifests`/`plugin_snapshot` in `AgenticCoder` and `SessionRunner._resolve_skill_catalog`.

The two-adapter/common-caller/deletion-payoff threshold is not met. These owners do not have a common caller: config is activated by SessionService/SessionRunner, provider runtimes by AgenticCoder, and plugins by AgenticCoder plus the skill-catalog owner. They also have different setup, health, and release owners. The repeated descriptor/discovery snippets are local duplication, not evidence of a shared activation interface.

## Design A — In-process GenerationCoordinator

**Interface:** `prepare(candidate) -> prepared`, `health(prepared)`, `publish(prepared) -> immutable pointer`, `pin(pointer)`, `drain(pointer) -> quiescent`. SessionService would call one coordinator and adapters would register config/provider/plugin preparation hooks.

**Deletion payoff:** If the three paths truly shared the caller, this could delete the repeated config graph/hash handoff in `SessionService._runtime_lock` and `runtime_emission.compile_runtime_effective_config_graph`, the repeated provider descriptor/runtime construction in `AgenticCoder._execute_rlm_provider_subcall` and the primary run, and the repeated plugin discovery/snapshot work in `AgenticCoder` and `SessionRunner._resolve_skill_catalog`. Today those snippets do not have identical contracts; deleting them would move provider/plugin release and trust policy into a shallow coordinator. No current exact deletion is safe.

**Dependencies:** `EffectiveHarnessLock`, config graph compiler, provider router/registry, plugin loader, SessionRunner/SessionService, and every adapter's health/dispose protocol. This is a new process-wide ownership seam and would need explicit provider/plugin lifecycle contracts.

**Failure ordering:** Build all candidate pieces hidden → validate Lock and provider/plugin health → publish one pointer → pin new Sessions → close old admission → settle and join old work → release each domain resource. Any failed preparation leaves the old pointer untouched; partial publication requires rollback or a durable recovery record.

**Migration cost:** Highest. Every caller must be migrated, provider and plugin trust/disposal semantics must be made coordinator-compatible, and tests need a two-adapter common caller. It risks violating the current domain-owned release law.

**W10 impact:** Potentially gives W10 one composition command, but only after a real common caller and adapter equivalence are proven. Without that proof it creates the K2 shallow "Generation Runtime" risk and should not be accepted.

## Design B — Lock-owned generation transaction

**Interface:** deepen the existing Lock authority with an internal transaction: `EffectiveHarnessLock.prepare(candidate) -> immutable Lock`, then Session owns `pin(lock)` and `adopt(lock, reason)`; config/provider/plugin remain domain-owned adapters that return validated facts. There is no process-global pointer.

**Deletion payoff:** This can safely delete only the duplicate config identity assembly by making `SessionService._runtime_lock` the sole Lock construction path and removing the parallel graph reconstruction in the start-record emission path. It does not delete provider runtime creation or plugin discovery because those are not called through the Lock owner and have independent disposal/trust rules. The provider and plugin duplication remains intentionally local.

**Dependencies:** existing `EffectiveHarnessLock`, `Session`, SessionService/Runner, and adapter-returned immutable descriptors. No new public schema is required; the durable Session event order remains the replay authority.

**Failure ordering:** Prepare and validate a candidate Lock and all adapter facts before `Session.adopt_generation`; append the durable reconfiguration event only after preparation succeeds; reject active/queued turns with typed refusal; close admission before teardown; join owned work before domain release. A failed append leaves the pinned Lock and event stream unchanged.

**Migration cost:** Medium for config/Session, low for provider/plugin because they remain owners. It requires careful removal of the duplicate config graph handoff and an adapter fact contract, but does not require provider-neutral lifecycle behavior.

**W10 impact:** Composes naturally with the existing Session command and replay checks, but does not prove a single activation owner across all adapters. It is the least risky design if a common caller later appears; current evidence still does not justify selecting it as a shared module.

## Design C — Durable generation manifest and atomic pointer

**Interface:** a filesystem-owned manifest store: `stage(generation)`, `validate(manifest)`, `publish(pointer)`, `read(pointer)`, `drain(pointer)`, `release(manifest)`. The pointer and manifest are atomically recovered across process death; Sessions persist the manifest identity they pin.

**Deletion payoff:** If every adapter produced manifest entries, this could replace the config start staging/publish pair (`_prepare_start_stages` and `_publish_start_bundle`) and remove ad-hoc pointer recovery. It currently cannot delete provider `create_runtime` or plugin discovery: neither produces a durable candidate manifest or shares the filesystem publication owner. Adding manifest writes for them would be new work, not deletion of existing duplication.

**Dependencies:** `session_store`/`JsonlEventSink`, anchored filesystem storage and process locks, config graph records, provider descriptors, plugin snapshots, and recovery logic for orphaned stages. The filesystem would become an activation owner but provider/plugin release would still be domain-owned.

**Failure ordering:** Write candidate records in a hidden stage → fsync and validate all records → atomically publish the pointer → make new Sessions read/pin it → close old admission → drain/join old resources → remove only unreachable stages. Recovery must never expose an incomplete stage or release a generation still pinned by a Session.

**Migration cost:** High operationally. It adds durable files, crash-recovery cases, cleanup rules, and platform-specific filesystem behavior to paths that currently activate in memory. It is justified only by a concrete cross-process activation consumer, which is absent in FT-02.

**W10 impact:** Could offer strong restart evidence, but would add another durable authority beside the logical Session event stream and complicate the W10 oracle. It is not acceptable without an explicit authority/recovery decision.

## Comparison and recommendation

| Design | Real common caller now? | Safe exact deletion now | Main risk | Recommendation |
|---|---|---|---|---|
| A. In-process coordinator | No | None across all three adapters | shallow generic runtime; ownership flattening | Decline on current evidence |
| B. Lock-owned transaction | No, but it deepens an existing Session/Lock seam | Config graph identity duplication only | adapter facts remain unmatched | Keep as a future option, do not select now |
| C. Durable manifest pointer | No | Config staging/publish only, after new manifest work | second durable authority and recovery burden | Decline on current evidence |

The threshold result is **no common activation owner is proven**: no design has two genuinely varying adapters at one caller with a deletion payoff. W3.2's existing Session/Lock implementation is therefore the smallest evidenced solution; the three adapter owners should remain separate pending a named consumer and a later design-it-twice result. Recommendation is to decline GEN-ACTIVATION rather than introduce a new shallow module.

## Decision

**PENDING Kyle.** This note records the comparison and recommendation only. No adapter, source, test, public contract, or schema was changed.
