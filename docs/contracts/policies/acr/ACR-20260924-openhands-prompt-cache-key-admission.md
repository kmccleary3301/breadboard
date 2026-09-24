# ACR-20260924-openhands-prompt-cache-key-admission

- `acr_id`: `ACR-20260924-openhands-prompt-cache-key-admission`
- `title`: Admit the pinned OpenHands SDK's per-conversation `prompt_cache_key` through a typed request-policy field
- `author`: BreadBoard E4 campaign
- `date`: 2026-09-24
- `status`: implemented

## 1) Problem Statement

DO-2 job 1206 failed all six OpenHands cases with `runtime/native_http_capability_mismatch` before any provider request. Every one of the 30 supplier request bodies in the sealed packet has exactly the keys `max_completion_tokens`, `messages`, `model`, `prompt_cache_key`, `temperature` and `tools`. `EpisodeOpenAICompletionsPolicyClient.stage_native_http_request` admitted only `model`, `messages`, `tools`, `stream`, `temperature`, `max_tokens`, `max_completion_tokens` and `reasoning_effort`, so the SDK's `prompt_cache_key` (its conversation UUID) was rejected. On base `82c87bcc` the supplier body raises there, and the same body without the key is admitted and forwarded byte-for-byte.

## 2) Scope and Surfaces

- Kernel modules touched: `breadboard_engine/provider/profiles.py` (`OpenAICompletionsRequestPolicy.conversation_key_field`), `breadboard_engine/compilation/provider_response.py` (OpenHands binding gate), `breadboard/rl/harness/policy_provider.py` (native HTTP body admission).
- Extension modules touched: none.
- Contract surfaces touched: provider request policy (new optional closed field), OpenHands native-response binding, OpenHands native HTTP admission.
- Is this a **kernel danger-zone** change? `yes`.

## 3) Coupling and Generalization Impact

- Danger-zone: yes.
- Core -> extension dependency: no. Admission reads only `profile.request_policy.conversation_key_field`; `policy_provider.py` and `conductor.py` name no profile.
- `conversation_key_field` is `Literal["prompt_cache_key"] | None`, default `None`; any other value fails policy construction. `as_dict` emits it only when set, so every existing profile identity digest is unchanged.
- When declared, the key must be a canonical lowercase UUID string (`8-4-4-4-12`). Its value, or its absence, is pinned per episode client under the state lock just before a request is staged. Any later change of value or presence fails closed with `native_http_capability_mismatch`. The body is forwarded unchanged.
- The OpenHands native-response binding requires `conversation_key_field == "prompt_cache_key"`, so an OpenHands profile without it fails at binding. Mini and Pi binding gates are unchanged. A profile without the field still rejects `prompt_cache_key` at staging.
- Coupling risk: low. One closed, typed field; no new inference from model names or caller flags.

## 4) Change Classification

- Classification: `additive` (new optional closed field) plus a fail-closed OpenHands binding requirement.
- Compatibility window: none. OpenHands composition inputs must declare the field, and their `provider_profile_digest` must be recomputed with `profile_identity_digest`.
- Required schema/version bumps: none; `bb.openai_chat_request_policy.v1` gains an optional field that is omitted when unset.

## 5) Evidence and Validation Plan

- Contract tests in `tests/rl/harness/test_policy_provider.py` compile the real `openhands-sdk@1.47.0` target and replay the `OH-01` supplier bodies through a real client. The supplier bodies are admitted and forwarded byte-for-byte. The client fails closed when the key goes missing after being present, appears after being absent, changes value, is not a UUID, is an uppercase UUID or is not a string. An OpenHands profile without the field fails at binding. A profile without the field, bound through the recording consumer, still rejects the key at staging. The admission tests failed on `82c87bcc` (7 failed, 1 passed) and pass after the change.
- Identity: a before/after script shows that the request-policy, profile-identity and headless-identity digests are byte-identical for the Pi, OpenClaw, OMP, Mini, Hermes, recorded-OpenHands and legacy-default profiles.
- Required conformance checks: `scripts/check_danger_zone_acr.py`, `scripts/check_kernel_contract_pack_v1.py`, and the focused harness/provider tests.
- Required replay: Main owns the installed OpenHands replay; this change alone does not assert its success.

## 6) Rollout Plan

1. Run the focused tests and both contract guards.
2. Obtain independent review of the exact commit; Main handles push, the OpenHands composition input update (`"conversation_key_field": "prompt_cache_key"` plus a recomputed profile digest) and the installed replay.

- Flags/toggles: none.
- Blast radius: OpenHands native HTTP admission and binding; other profiles unchanged by identity.
- Monitoring hooks: rejections keep `native_http_capability_mismatch`; binding failures keep `OpenHands native response requires its compiled source profile`.

## 7) Rollback Plan

- Trigger: regression in native HTTP admission, profile identity or OpenHands binding.
- Rollback: revert the three source changes, their tests, the docs sentence and this ACR together. OpenHands then fails before any request again.
- Artifact/state restoration: none; no persisted schema changes. Composition inputs that declare the field must drop it.
- Post-rollback verification: rerun `tests/rl/harness/test_policy_provider.py`, `tests/providers/test_openai_profile.py` and both contract guards.

## 8) Approvals

- Kernel reviewer: required on the exact candidate head.
- Contracts reviewer: required on the exact candidate head.
- Ops reviewer: Main handles the installed replay.
- Final decision: pending independent review; this ACR does not authorize push or merge.
