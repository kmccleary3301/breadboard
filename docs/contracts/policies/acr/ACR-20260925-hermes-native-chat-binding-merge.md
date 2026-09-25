# ACR-20260925-hermes-native-chat-binding-merge

- `acr_id`: `ACR-20260925-hermes-native-chat-binding-merge`
- `title`: Per-consumer conversation key in the shared native Chat binding gate after merging main 7f89107d
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-25
- `status`: implemented

## 1) Problem Statement

Main 7f89107d (PR #139, ACR-20260924-openhands-prompt-cache-key-admission) added `profile.request_policy.conversation_key_field != "prompt_cache_key"` to the OpenHands native-response binding gate. On this lane OpenHands and Hermes share one gate, keyed by `NATIVE_CHAT_RESPONSE_TARGETS`. A textual merge applied the OpenHands requirement to Hermes as well. The pinned Hermes Chat Completions body is exactly `{model, messages, tools, max_tokens}` and carries no conversation key, so every real Hermes binding would fail. A Hermes profile that wrongly declared `prompt_cache_key` would have been admitted.

The lane also carried a second, weaker copy of the same gate straight after the first. Its conditions were a strict subset of the first block's, so it could never raise on its own.

## 2) Scope and Surfaces

- Kernel modules touched: `breadboard_engine/compilation/provider_response.py` (native Chat binding gate), `breadboard/rl/harness/policy_provider.py` (native HTTP body admission, merge only).
- Tests: `tests/rl/harness/test_policy_provider.py` (target-parametric native Chat client helper; Hermes admission and rejection).
- Contract surfaces touched: Hermes and OpenHands native-response binding.
- Kernel danger-zone: yes, the native provider binding gate shared by the OpenHands and Hermes consumers.

## 3) Coupling and Generalization Impact

- Core -> extension dependency: no.
- The gate requires `conversation_key_field == "prompt_cache_key"` for OpenHands, as main does. For Hermes it requires `None`. Mini and Pi gates are unchanged.
- Native HTTP body admission keeps main's `admitted_fields` (the base set plus the declared conversation key). The lane's Hermes exact-body check (`{model, messages, tools, max_tokens}`) still applies on top.
- The redundant duplicate gate is removed. The single remaining gate is a superset of both copies' conditions.

## 4) Change Classification

- Classification: `bugfix` (merge resolution).
- Required schema/version bumps: none; profile identity digests are unchanged.

## 5) Evidence and Validation Plan

- Classification: `internal` (merge resolution; restores each consumer's source binding).
  - Compiles the real `hermes-agent@2026.9.11` target.
  - A source Hermes profile (`max_tokens`, no sampling overrides, no conversation key) binds.
  - A Hermes profile declaring `prompt_cache_key` fails at binding.
  - Both tests fail against the unconditional merge line.
- The OpenHands tests are unchanged apart from the gate message, which on this lane is the shared "native Chat response requires its compiled source profile".
- Hermes installed replay at the pre-merge head 5df37811 on DO-2 (job 1357, kernel 6.8.0-142) is 6/6 comparator_ok. The merged head needs the same replay before live qualification.

## 6) Rollout Plan

- Rollout phases: merge commit on `e4/hermes-20260924`, PR #138.
- Flags/toggles: none.
- Blast radius constraints: native Chat binding for the Hermes and OpenHands consumers only.
- Monitoring hooks: binding admission tests and the Hermes and OpenHands installed replays.

## 7) Rollback Plan

- Trigger conditions: a valid OpenHands or Hermes source profile fails binding, or an invalid one binds.
- Exact rollback commands: `git revert -m 1 <merge commit>`.
- Artifact/state restoration steps: none.
- Post-rollback verification: rerun `tests/rl/harness/test_policy_provider.py`, `tests/providers/test_native_response.py` and both contract guards.

## 8) Approvals

- Kernel reviewer: Main (campaign orchestrator) under standing approval
- Contracts reviewer: Main (campaign orchestrator)
- Ops reviewer: Main (campaign orchestrator)
- Final decision: Main (campaign orchestrator)
