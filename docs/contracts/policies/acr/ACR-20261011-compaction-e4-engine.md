# ACR-20261011-compaction-e4-engine

- `acr_id`: `ACR-20261011-compaction-e4-engine`
- `title`: Context compaction subsystem, E4 native compaction presets, compaction-enabled RL target revisions and differential lanes
- `author`: BreadBoard compaction implementation
- `date`: 2026-10-11
- `status`: implemented

## 1) Problem Statement

The compaction subsystem (step 1, `breadboard_engine/compaction/`) is off by default: with no `compaction` block, request views and session snapshots are unchanged. Steps 2–4 of `COMPACTION_E4_SCOPE_REQUEST.md` need native context compaction that matches each stock harness:

- the RL targets: Pi 0.73.1, Pi 0.57.1, OMP 16.2.13, OMP 18.1.17, Hermes and OpenClaw 2026.9.4;
- the dated engine profiles: Codex 0.139.0, Claude Code 2.1.63, OpenCode 1.2.17 and oh-my-opencode 3.10.0.

The proof is a differential lane that compares raw request bytes against the pinned stock harness.

## 2) Scope and Surfaces

- Kernel danger-zone: yes. The change touches `breadboard/**` and `breadboard_engine/**`: agent loop, session state, CLI bridge, provider runtime and permission broker.
- Default-path behavior change: a provider `context_length_exceeded` overflow no longer records a route-health failure, so it cannot open the route circuit before a retry.
- New compaction presets, engine profiles (`agent_configs/*_e4_10-9-2026.yaml`) and RL target revisions:
  - `pi-r3@0.73.1`
  - `pi-r4@0.57.1`
  - `oh-my-pi-r3@16.2.13`
  - `oh-my-pi-r2@18.1.17`
  - `openclaw-r2@2026.9.4`
  - `hermes-agent-r2@2026.9.11`
- Unchanged:
  - the dated `*_9-10-2026` profiles;
  - the baseline `config/e4_targets/**` revisions (the E4 target-freeze manifest guard passes);
  - `tests/fixtures/compat/**`.
- New runtime behavior is opt-in through capability flags, never harness identity:
  - `tools.native_local_only`
  - `tools.native_concurrent_prompts`
  - `tools.pty`
  - `session.failed_turn_continues`
  - `product_session.concurrent_approvals`
- Contract schemas are additive. No field is removed, and every previously valid document still validates. The changes:
  - `bb.agent_config_surface.v2` gains optional `compaction`, prompt-builder, OpenAI Responses and resume members.
  - `bb.e4.target_config.v2` gains optional compaction members.
  - `bb.provider_exchange.v2` adds an optional `raw_arguments` alternative for tool calls, typed replay-payload dispatch and native request-body members.
  - `bb.payload.product_session.lifecycle.v1` gains optional `concurrent_approvals`.
- Native workers carry the pinned SDK's serialized request body. The provider admits it only when it matches the compiled request by JSON value.

## 3) Coupling and Generalization Impact

- There is one immutable stock execution driver per target, selected at setup. Controllers use explicit execution, lifecycle and summary-catalog ports, not traversal of vendor-private objects.
- OpenCode and OMO share one tool-definition catalog and HTTP proxy.
- Codex process and child-registry ownership stays outside the generic sandbox.
- Readers pinned to an older copy of `bb.provider_exchange.v2` would reject `raw_arguments`. Only native stock targets emit it, and the in-repo schema is the single source.
- Hermes deviates from the scope's worker gate. The scope requires `implements_compaction_phases` before a non-Pi target turns compaction on. Stock Hermes compacts inside its own worker, so `hermes-agent-r2@2026.9.11` sets `compaction_in_source` instead, and the conductor admits either flag. Its summary requests carry the episode's tool schemas and output cap, recorded as deviations in its `harness.yaml`.
- mini-swe-agent and the bare OpenHands SDK have no preset. Both are cloned-as-none, as the scope requires.

## 4) Change Classification

- Classification: `additive`. New targets, presets, capability flags and optional contract members; engine compaction remains opt-in and baseline Pi bytes/pins are retained. Default-route corrections also cover overflow health, OpenCode/OMO typed-image and admitted-raw-call history, and destination projection on fallback. These are contract fixes, not a claim of universal default-path byte identity or only one default-path change.

## 5) Evidence and Validation Plan

- Focused replay receipts (2026-10-14): native Anthropic image removal at `0031ee5b` passed 29 image tests and actual runtime/SDK serialization. Structured duplicate pruning at `94117779` passed 27 pruning tests, before/after ordered-content and native-text-equivalence probes, and actual SDK serialization/decode. Local mock transports made no live provider HTTP requests.
- Native OpenAI correction receipts (2026-10-14): 61 remote/Responses consumer tests passed. Both original system-policy/raw-call reproductions passed after the fixes. Eight actual SDK 3.26.1 mock-transport requests exercised V1/V2, custom-policy precedence, exact malformed/legacy arguments, native-boundary continuation and source immutability. A separate reset-boundary SDK smoke retained the complete projected input, kept its user summary out of instructions and did not restore the discarded system head. Source imports resolved to T7; no live provider HTTP.
- The following suite, wheel and CI rows are historical receipts, not qualification of a later head. Final-head verification belongs to the PR's current verification section; no full live-provider, RL, B1 or training qualification is claimed here.
- After merging the RL run-budget ceilings, the focused suite (`tests/compaction tests/providers tests/compaction_lanes tests/rl/harness tests/test_e4_targets.py tests/test_runtime_resource_closure.py tests/test_script_index_guard.py`) had 4699 passed, 271 skipped and 29 failed on a fresh macOS worktree. None is caused by this change:
  - 10 fail identically at base `308b9027`: runner and WP7 source guards, v1 history close, and the V2 containment receipt.
  - 3 fail identically on `main` `0f590af4`: two Docker sandbox tests and the Hermes packet replay, which reads an untracked `docs_tmp` input.
  - 14 native Pi worker tests need the npm package installed. With it installed, those two modules pass (308 passed).
  - 1 OpenRouter test reads a local credential.
  - 1 OpenCode dead-end test is timing-dependent: stock oh-my-opencode 3.10.0 caches its storage-backend check (`isSqliteBackend`) and, if the check runs before `opencode.db` exists, truncates nothing. It passes or fails the same way without this change.
- Long-session lanes compare stock and BB request bytes and reject unadmitted differences (`breadboard_engine/compaction_lanes/README.md`). A passing report establishes only its pinned cases, not whole-agent or live-provider parity.
- Installed-wheel proof in a fresh venv with `-I`, run outside the checkout:
  - the consumer regression passes;
  - all five native workers initialize, read and close with every process dead;
  - the Codex patch, search and image helpers work;
  - process-group cleanup kills leftover descendants;
  - the OpenAI SDK 3.29.0 loopback HTTP smoke sends 8 real requests with no failures.
- All shipped and test Python compiles under Python 3.11.
- CI's Compat core, Conformance gate, Conformance matrix sync and E4 target-freeze manifest guard pass.
- The script index (`scripts/SCRIPT_INDEX.yaml`) registers the 21 new scripts, and the clean-wheel test pins the 23 shipped target revisions.

## 6) Rollout Plan

- Kyle reviews and decides whether to merge `feat/compaction-subsystem`. Engine compaction stays off without a `compaction` block; the default-route protocol corrections in Section 4 are independently applicable.
- Campaign jobs operate under standing Slurm authority, without per-job approval pauses. Resource authority does not award qualification, external acceptance or promotion; those gates still require their evidence.

## 7) Rollback Plan

- Revert the merge commit on `main`. Baseline profiles and target revisions are unchanged, so consumers that never selected the new revisions need no migration.

## 8) Approvals

- This artifact records the implementation decision and its evidence. It does not assert external acceptance or promotion.
