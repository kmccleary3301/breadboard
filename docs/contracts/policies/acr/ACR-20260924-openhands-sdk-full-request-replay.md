# ACR-20260924-openhands-sdk-full-request-replay

- `acr_id`: `ACR-20260924-openhands-sdk-full-request-replay`
- `title`: OpenHands SDK 1.47.0 native request replay and compiled iteration budget
- `author`: BreadBoard E4 OpenHands lane
- `date`: 2026-09-24
- `status`: implemented; installed DO-2 replay pending

## 1) Problem Statement

The OpenHands worker must pass the pinned SDK's provider request without inserting a missing `temperature` field. It must also build the model from the sealed target configuration and use the compiled iteration budget instead of a second worker-owned limit. Otherwise a supplier replay can pass while the provider body or stopping point differs. Request comparison must check each captured conversation's own ID before normalizing its prompt-cache key; accepting an arbitrary UUID from either side would hide a real request mismatch.

## 2) Scope and Surfaces

The changed-file guard identifies three protected code paths, plus this ACR itself, in the PR delta from `ba63c4b3a6ce9f6ff088119b7bf0ca2e6400f517` through the current OpenHands lane head:

- `breadboard/product/harness/targets.py`: updates the admitted OpenHands target descriptor digest to the resealed `openhands-sdk@1.47.0` target asset.
- `breadboard/rl/harness/openhands_worker.py`: stops adding `temperature` to the SDK transport body, loads model settings from the native configuration, checks the positive initialized iteration limit, and uses that limit for the SDK conversation, sample cutoff, and max-iterations error.
- `breadboard/rl/harness/runners/conductor.py`: admits a positive compiled turn limit, sends it and the source profile to worker initialization, bounds reported iterations against it, and drives the loop for that many turns rather than a literal 16.

Danger-zone: yes. The same delta reseals the versioned target and native configuration, records six supplier stderr captures and their replay fixtures, and changes the OpenHands comparator and focused tests. Those code and fixture files are outside the three listed protected code paths; this ACR is the fourth protected changed file. The registered comparator now fails closed when the supplier's recorded conversation ID or BB's independently observed conversation ID is absent, ambiguous, or does not bind every request's prompt-cache key. This ACR is the decision artifact for the resulting changed-file list.

## 3) Coupling and Generalization Impact

The Conductor passes the already compiled OpenHands source profile and `limits.max_turns` into the existing native worker phase. The worker still requires its admitted OpenAI-compatible model route; it constructs the SDK model from the sealed native configuration, including the declared capability overrides and pinned dependency versions. Its IPC transport forwards `request.content` as base64 without rewriting JSON; it omits authorization from public evidence and adjusts transport headers for the unchanged body length. The registered comparator reads the supplier ID from the captured `state.py:592` stderr line and BB's independently recorded `conversation_id` from the replay trace; it binds each raw request key to that side's ID before UUID normalization. Merely supplying a different UUID no longer passes. That key rule applies to these default captured conversations, not every possible SDK sub-agent override. Shared target lowering and Conductor code make non-OpenHands regression possible, but the changed loop is `_loop_openhands` and the target recipe remains digest-bound.

The replay trace now projects ordered `ActionEvent` tool calls from the worker's committed event deltas, including SDK-rejected calls. Only the separately returned validated `actions` are dispatched. This keeps the rejected call and its `AgentErrorEvent` visible without treating a validation failure as an executable tool call.

The sealed supplier capture sets `num_retries` to zero, as does the native config for both the SDK LLM and its OpenAI HTTP client. A second `provider_request` after one `provider_response` in a sample is not replayed: the Conductor reports `native_retry_refused` with that request's method and URL instead of a generic malformed-response error. No retry setting or provider body is rewritten.

## 4) Change Classification

- Classification: `additive`.

The versioned OpenHands target gains sealed model-capability declarations, supplier request fixtures, and a request comparator that rejects invalid cache keys. Its provider body and turn cutoff change deliberately to match the source and compiled budget. The patch does not add a generic provider request rewrite, a new model-visible tool, or a persistence migration. It makes no claim of installed x64 readiness.

## 5) Evidence and Validation Plan

- Independent exact-code-head ACCEPT at `67944ea9a2b9eae406ee740882f62bb0e1c91766`: `/tmp/bbe4-openhands-rereview3-67944ea9-review.md` (SHA-256 `91d8d6702f4a4dcfd371ab7794048bc7f10d31e7f47947a8345cf6692cbc5e4f`). The review covers source and local-vm-linux, not this ACR commit or installed DO-2.
- That review records a fresh Linux VM Python 3.12 replay of six cases and 15 ordered request bodies, a separate strict JSON comparison with each side's workspace and independently checked cache key, and 13 rejected mutations. Its four-file VM focused run reports `299 passed, zero skipped` in `/tmp/bbe4-openhands-rereview3-67944ea9-vm-focused.log` (SHA-256 `0c094166e8e579d23a7efc3ad16b5398a796b269116f7ec3f2561078289eb4fe`). The review also records `296 passed, 3 skipped` on macOS. This is JSON-value request parity, not a raw-wire byte-equality claim.
- The registered-comparator binding change has a before/after OH-01 foreign-UUID replay: `/Users/kylemccleary/projects/breadboard/.tmp/bbe4/openhands-cache-before.json` (SHA-256 `2ca6dfe791c03aa9f9bd7a62e93d99a6c3ae5d328ea40ed89c07a97537381bea`) records a false pass; `/Users/kylemccleary/projects/breadboard/.tmp/bbe4/openhands-cache-after.json` (SHA-256 `e0c38a95b9157506aa4d34f6d03839118e8e95165654c8dc8cb19ac55ee5d82d`) records a failed binding assertion and a passing control. Scoped comparator and registry tests report 33 passed (`/Users/kylemccleary/projects/breadboard/.tmp/bbe4/openhands-cache-tests-comparator.log`, SHA-256 `8603052f849937ede3d3129e03eb8a16cf399f0f48913a0112075e8315d33d06`); worker tests report 7 passed and 2 skipped because `BB_OPENHANDS_PY312` was unset (`/Users/kylemccleary/projects/breadboard/.tmp/bbe4/openhands-cache-tests-worker.log`, SHA-256 `33a55e812400942cb228429990acc64bbea8ba4fad033685d2508464bd7843fc`). The prior independent review does not cover this binding change; a new exact-head review remains required.
- The danger-zone ACR guard and kernel contract-pack manifest checker must pass for the committed ACR. DO-2 installed SIF replay on Linux x64 is **PENDING**. The local VM result does not replace it.

## 6) Rollout Plan

Keep the OpenHands source profile, target descriptor digest, supplier captures, and per-conversation request checks pinned together. Land this ACR only after the changed-file and contract-pack guards pass. Applicable CI and a separate installed DO-2 Linux x64 replay remain gates before qualification or promotion.

## 7) Rollback Plan

If request parity or compiled-budget behavior regresses, revert the OpenHands lane change through protected review and suspend its qualification claim. Do not treat a restored transport-body rewrite or an unverified cache-key normalization as acceptable supplier evidence. Keep the captured packet and exact-head review receipts for diagnosis; no data migration is involved.

## 8) Approvals

The independent review accepts the earlier code head for source plus local-vm-linux only. The registered-comparator change requires a new exact-head independent review; applicable CI, installed DO-2 replay, and any campaign promotion decision remain separate. This ACR grants no merge authority or external acceptance.
