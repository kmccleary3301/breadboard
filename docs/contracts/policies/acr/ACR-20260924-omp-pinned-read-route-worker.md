# ACR-20260924-omp-pinned-read-route-worker

- `acr_id`: `ACR-20260924-omp-pinned-read-route-worker`
- `title`: Oh My Pi 18.1.17 native worker and pinned read-route admission
- `author`: BreadBoard E4 Oh My Pi lane
- `date`: 2026-09-24
- `status`: implemented; first-request pinned composition awaits installed DO-2 replay and independent review

## 1) Problem Statement

The `oh-my-pi@18.1.17` target needs a compiled provider binding and a tool worker that uses the pinned supplier SDK. Its ReadTool has several path-dependent routes. A separate regex approximation can admit a URL, SSH path, archive member, database selector, or other excluded read as a local file. The changed worker observes the verified supplier `read.ts` branches before effects, admits only its local-file route, and fails closed when classification or the denial policy is unavailable. This decision records the shared compiler, Conductor, provider, and sandbox changes needed to run that target without treating a local Linux ARM result as an installed Linux x64 qualification.

The first-request correction removes the independently authored 4,110-byte OMP prompt. The worker now obtains `session.agent.state.systemPrompt` from pinned OMP `createAgentSession` (`sdk.ts:3171-3228,3372-3378`) and applies pinned `DateCwdReminderInjector` to the user message before pinned OpenAI Completions conversion. Bounded tool descriptions start with pinned SDK tool descriptions and apply declared, hash-checked removal spans. The target asset is exactly the supplier template (`system-prompt.md`, SHA-256 `6f4854f0e80a3a0931c9b34bda8a8d7e6f90d15ec4dfa7567c5f7019b02feca7`), not the literal provider message. This correction covers issue 32 items 1–2 only; items 3–7 and installed parity remain outside its claim.

## 2) Scope and Surfaces

The PR delta from `ba63c4b3a6ce9f6ff088119b7bf0ca2e6400f517` to `966f091e417eea02161c293a5ddaa78e23b92abf` changes these eleven protected paths. This ACR is a twelfth protected path in the resulting PR delta:

- `breadboard/product/harness/targets.py`: accepts the pinned OMP v2 target recipe, constructs its native advertisement, and preserves a required-parameter order that differs from property order.
- `breadboard/rl/harness/native_stream_profiles.py`: registers the OMP consumer, `chat_completions` variant, worker-backed state, tool order, and turn/time limits in the native-stream profile registry.
- `breadboard/rl/harness/omp_native_tools.py`: pins the source, lock, Bun, installed worker entrypoint, route-policy inputs, and framed native-worker phases; its test override checks source-module digests.
- `breadboard/rl/harness/policy_provider.py`: binds OMP to its compiled target and native provider profile, projects its model compatibility and tool order, and checks the source-native request against the admitted prompt and tools.
- `breadboard/rl/harness/runners/conductor.py`: admits the profile-selected API variant and bounded tool schemas, then records native tool calls while dispatching only calls admitted by the OMP state.
- `breadboard/rl/harness/runners/omp_native_tool_worker.ts`: composes the four pinned SDK tools, verifies route-module and `read.ts` hashes, observes supplier ReadTool branches before effects, denies non-file routes, runs framed tool phases, and reaps descendants on close.
- `breadboard/rl/harness/runners/omp_semantics.py`: supplies OMP request/response state, stop and length behavior, denied-call results, tool-result ordering, and replay-trace facts to the shared Conductor.
- `breadboard/rl/harness/sandbox.py`: registers the OMP local adapter identity and its `bash`, `edit`, `read`, `write` tool set.
- `breadboard_engine/compilation/provider_response.py`: admits OMP as a streaming native-response consumer only with its compiled target identity and source-profile constraints.
- `breadboard_engine/compilation/server_compiler.py`: validates and retains an optional tool `required_order` against the declared required parameters.
- `breadboard_engine/provider/runtimes/openai/chat.py`: keeps OMP's source-shaped messages and tools, omits `n`, and disables request storage for that bound consumer.
- `docs/contracts/policies/acr/ACR-20260924-omp-pinned-read-route-worker.md`: records the decision required for those danger-zone changes.

The same PR delta adds the versioned OMP target assets, comparator, read-route oracle and corpus, and focused tests under `config/e4_targets/oh_my_pi/18.1.17/`, `conformance/comparators/`, `tests/e4_parity/`, and `tests/rl/harness/`. Those are not additional paths matched by the danger-zone manifest.

- Danger-zone: yes.

## 3) Coupling and Generalization Impact

The OMP behavior is selected by the exact target descriptor and `breadboard.oh-my-pi.v18.1.17` consumer identity, not by a generic tool name. The Conductor uses its existing native-stream profile registry and adds bounded schema admission; compiler and provider changes carry OMP's required tool ordering and native prompt through that boundary. Only `read`, `bash`, `edit`, and `write` are advertised. The worker verifies its source and lock inputs, observes the pinned ReadTool rather than maintaining a second route parser, and rejects unknown routes before tool execution. Declared remote and structured routes are also denied before dispatch by the OMP state. Shared compiler, provider, and Conductor edits make regressions in other targets a coupling risk; the explicit consumer checks and optional `required_order` constrain when the new behavior applies.

## 4) Change Classification

- Classification: `additive`.

This adds a versioned target and streaming consumer. Within that target, the final read-route changes are corrective and fail closed: a non-file or unclassified supplier route no longer gains local-file admission. The optional required-order field changes compiled tool handling only when a target declares it. No installed x64 qualification, new general read capability, or merger authorization follows from this classification.

## 5) Evidence and Validation Plan

- Independent exact-code-head review at `966f091e417eea02161c293a5ddaa78e23b92abf`: `/tmp/bbe4-omp-review-sol-w5-2-966f091e-review.md` (SHA-256 `24a11395e063cba37e630550296b7866471a31fdce5a4a0b458a1f843325d837`). It accepts the local-vm-linux read-route fix with an explicit platform limit.
- That review records an isolated Linux aarch64 VM run of six focused files, `329 passed, 4 warnings`: `/tmp/bbe4-omp-review-sol-w5-2-966f091e-vm-focused-isolated.txt` (SHA-256 `bc2cffc5050de54aed8be7d834945cd67e327dac4c73a010af54e10563bf6492`). It also records `313 passed, 13 skipped` on macOS and zero route mismatches or non-file admissions across its pinned-source corpora. These are results for the code head, not an installed x64 result or a review of this ACR commit.
- The danger-zone ACR guard must accept the complete PR changed-file list; the kernel contract-pack checker must accept its manifest. DO-2 installed SIF replay on Linux x64 is **PENDING**. No DO-2 result or production acceptance is asserted here.
- At this corrective head, the removed supplier-content overrides in `test_omp_native_stream_conductor.py` leave its local comparison environment-gated. Bun successfully bundled the worker TypeScript locally, but the macOS export lacks `@oh-my-pi/pi-ai/dialect` and native darwin leaves: direct source rendering and six-case installed request parity cannot be asserted from this host. The sealed supplier's first system message is 9,831 bytes, SHA-256 `975caab28fb212c4ff1642ce2ab5a46936205f9b04a9b96938cd04a971030491`. Independent review and the Linux x64 installed replay must compare the actual BB-produced system and reminder bytes without overrides.

## 6) Rollout Plan

Keep the OMP target bound to its pinned source, consumer identity, declared capability denials, and native worker. Resolve the ACR guard and contract-pack checks for this documentation-only commit. Require applicable CI and the separate installed DO-2 Linux x64 replay before any qualification or promotion. The existing local-vm-linux evidence cannot substitute for that replay.

## 7) Rollback Plan

If OMP route admission, native binding, or shared-target behavior regresses, revert the OMP lane changes through the normal protected process along with this decision artifact. Do not bypass a digest or denial mismatch to keep the target running. Retain the exact-head review and local-vm-linux receipts for diagnosis; this change adds no persistence migration.

## 8) Approvals

The independent review accepts only the code head's local-vm-linux read-route result. A review of this ACR commit, applicable CI, installed DO-2 replay, and the campaign's existing promotion decision remain separate. This document grants no merge authority or external acceptance.
