# ACR-20260923-e4-native-stream-seam

- `acr_id`: `ACR-20260923-e4-native-stream-seam`
- `title`: E4 native-stream seam and profile-registry integration
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-23
- `status`: implemented

## 1) Problem Statement

The E4 native-stream profiles need one registry-driven Conductor loop that preserves admitted source phase order, typed lease authority, native tool behavior, and replay evidence across the Pi and OpenHands integrations. The seam crosses the runner, provider, sandbox, compiler, target assets, comparators, and contract tests. Without an explicit architecture decision, profile-specific behavior can leak into the generic loop, cleanup can lose the primary failure, or parity tests can accept a weakened trace.

The measurable outcome is an exact-head implementation whose generic loop is reused by the profile registry, whose native phases are bounded and failure-safe, and whose independent supplier comparators reject unsupported or undeclared normalization. If this decision is not recorded, future profile work may add bespoke runners or broaden normalization and silently weaken cross-harness parity.

## 2) Scope and Surfaces

- Kernel modules touched:
  - `breadboard/product/harness/targets.py`
  - `breadboard/rl/harness/composition.py`
  - `breadboard/rl/harness/contracts.py`
  - `breadboard/rl/harness/native_session.py`
  - `breadboard/rl/harness/native_stream_consumers.py`
  - `breadboard/rl/harness/native_stream_profiles.py`
  - `breadboard/rl/harness/native_worker.py`
  - `breadboard/rl/harness/openhands_worker.py`
  - `breadboard/rl/harness/pi_native_tools.py`
  - `breadboard/rl/harness/pi_tools_0_73_1.mjs`
  - `breadboard/rl/harness/policy_provider.py`
  - `breadboard/rl/harness/runners/base.py`
  - `breadboard/rl/harness/runners/conductor.py`
  - `breadboard/rl/harness/runners/pi_semantics.py`
  - `breadboard/rl/harness/sandbox.py`
  - `breadboard/rl/harness/service.py`
  - `breadboard_engine/compilation/provider_response.py`
  - `breadboard_engine/compilation/server_compiler.py`
  - `breadboard_engine/provider/native_response.py`
  - `breadboard_engine/provider/runtimes/openai/chat.py`
  - `breadboard_engine/provider/runtimes/openai/chat_stream_decoder.py`
- Extension modules and profile assets:
  - `config/e4_targets/` target descriptors and native assets for OpenHands 1.47.0 and Pi 0.73.1.
  - `conformance/comparators/` registered supplier/replay comparators.
  - `tests/e4_parity/` independent replay fixtures and parity tests.
- Contract surfaces touched: native phase protocol, provider-request projection, streamed tool and observation events, session cleanup/quarantine facts, target/profile registry, replay trace and comparator contracts.
- Kernel danger-zone change? yes

## 3) Coupling and Generalization Impact

- Does this add any core -> extension dependency? no. The generic native-stream loop consumes the existing profile registry; profiles provide declared schemas, tool order, limits, and state factories rather than bespoke runner control flow.
- Does this narrow cross-harness parity behavior? yes. Native requests, source observations, cleanup facts, and comparator normalizations are admitted only through typed, profile-bound contracts.
- Does this alter default endpoint semantics? no. Non-native sessions retain the existing generic policy/runtime behavior; native behavior is selected by the compiled consumer identity and registry entry.
- Coupling risk score (`medium`) and rationale: the seam joins compiler-produced target identity, provider bindings, sandbox-owned native phases, session lifecycle, service publication, and independent comparators. The registry boundary keeps source-specific semantics local, while the generic loop and typed cleanup path remain shared.

The generic native-stream loop is the lifecycle owner. `NATIVE_STREAM_PROFILES` is the profile registry and the only admission point for profile-specific phase behavior. OpenHands native HTTP is separately selected by its compiled consumer identity and does not become a stream-profile cleanup publisher.

## 4) Change Classification

- Classification: `behavioral-change`
- Compatibility window: existing non-native and accepted `pi@0.57.1` behavior remains admitted; the new `pi@0.73.1` and OpenHands profiles are versioned siblings.
- Required schema/version bumps: none beyond the existing versioned target, phase, replay-trace, and comparator contracts.

## 5) Evidence and Validation Plan

- Required contract lane tests: native phase admission, Conductor lifecycle and cancellation, sandbox cleanup/quarantine, target identity, provider projection, and service failure publication tests.
- Required replay/parity checks: independent Pi and OpenHands supplier/replay comparators, exact tool/request/observation/effect projections, declared normalization and negative-placeholder controls, and target asset tests.
- Required conformance/ablation checks: run the focused E4 suites and the broad suite against baseline `ef7e5be2`; accept only zero new broad-suite failures.
- Required evidence bundles to refresh: exact-head review records, focused test output, broad-suite comparison, target/profile manifests, supplier fixtures, replay reports, and cleanup/quarantine evidence.
- Acceptance criteria:
  - review rounds 1 through 6 are recorded in `docs_tmp/wayfinder/e4-campaign-local-production/issues/12-pi-profile.md`, including the round-2 pool `e4-review-pi-seam-r2`, round-3 pools `e4-review-pi-seam-r3-a` and `e4-review-pi-seam-r3-b`, and round-6 exact-head pools `e4-review-pi-seam-r6-a` and `e4-review-pi-seam-r6-b`;
  - the final exact head is `21700fc4` and both independent round-6 reviews ACCEPT;
  - 423 focused tests pass and the broad suite has zero new failures against `ef7e5be2`;
  - profile-specific behavior remains behind the registry and native cleanup preserves the primary failure while publishing typed evidence.

## 6) Rollout Plan

- Rollout phases: validate the exact head and target assets; run independent supplier/replay parity; admit the versioned profile only after the required CI and exact-head reviews pass.
- Flags/toggles: no runtime fallback or compatibility alias; profile selection is compiled and registry-driven.
- Blast radius constraints: keep the generic loop's shared controls stable; limit native behavior to the admitted consumer/profile identity; preserve existing target assets and historical captures.
- Monitoring hooks: retain structured phase events, request/response digests, cleanup/quarantine facts, replay reports, and exact-head review evidence.

## 7) Rollback Plan

- Trigger conditions: any regression in native phase order, provider projection, tool/effect history, cleanup proof, quarantine publication, target identity, replay determinism, or contract validation; any new broad-suite failure against `ef7e5be2`; or failure of an exact-head review.
- Exact rollback commands: revert the PR merge commit with `git revert -m 1 <pr-merge-commit>` in a new rollback branch. Do not reset protected main or discard unrelated changes.
- Artifact/state restoration steps: restore the prior accepted profile registry and target activation, preserve older target assets and supplier custody, and quarantine the rejected exact-head artifacts. Do not delete retained evidence.
- Post-rollback verification: rerun the focused lifecycle/provider/comparator suites, the broad-suite baseline comparison, target and contract checks, and the danger-zone ACR check on the rollback candidate.

## 8) Approvals

- Kernel reviewer: Main (campaign orchestrator) under Kyle McCleary's standing approval
- Contracts reviewer: independent exact-head reviews `e4-review-pi-seam-r6-a` and `e4-review-pi-seam-r6-b`, both ACCEPT at `21700fc4`
- Ops reviewer: Main (campaign orchestrator) under Kyle McCleary's standing approval
- Final decision: Main (campaign orchestrator) under Kyle McCleary's standing approval; independent exact-head reviews `e4-review-pi-seam-r6-a/-b` ACCEPT at `21700fc4`
