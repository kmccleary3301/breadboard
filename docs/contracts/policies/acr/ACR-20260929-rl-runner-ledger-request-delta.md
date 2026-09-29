# ACR-20260929-rl-runner-ledger-request-delta

- `acr_id`: `ACR-20260929-rl-runner-ledger-request-delta`
- `title`: Record policy requests in the runner ledger as a digest plus a prefix delta
- `author`: BreadBoard RL implementation
- `date`: 2026-09-29
- `status`: implemented

## 1) Problem Statement

BMoE 74B BreadBoard Pi one-step train 121825 completed its optimizer step. However, 4 of its 260 episodes (2uvayfh1, b6sp28_x, nmr_hqhv, v4vztgef) failed publication with `EvidenceValidationError: evidence object exceeds repository bound`. One `bb.rl.runner-event-ledger.v2` object reached 80,099,390 bytes, above `MAX_OBJECT_BYTES` (64 MiB).

The cause is `PolicyRequestEvent`, the only runner event that carried a request payload. It stored the full cumulative request on every turn. With a growing `input`/`messages` history, the ledger and the `runner_result` evidence object that serializes the same events grew with the square of the turn count: about 530 KB per request event by turn 200.

## 2) Scope and Surfaces

- Kernel danger-zone: yes. This branch changes `breadboard/rl/harness/runners/{base,conductor,terminal}.py`.
- `PolicyRequestEvent.request_payload` is replaced by two fields:
  - `request_digest`: the canonical sha256 of the exact request the policy receives.
  - `request_delta`: a delta against the previous request of the same runner session, with keys `base_request_digest`, `set`, `extend` (`keep` prefix length plus appended `items`) and `remove`.
- The first request has a null base and carries the full request. The delta base advances only after the event is emitted.
- Pure helpers:
  - `policy_request_delta` and `apply_policy_request_delta` build and apply deltas.
  - `reconstruct_policy_requests` replays a ledger's deltas and rejects any reconstructed request whose digest differs from `request_digest`.
- The request sent to the policy, `PolicyRuntimeRequestEvent`, `PolicyRuntimeInvokeRequest` and every repository bound are unchanged.

## 3) Coupling and Generalization Impact

- No production consumer reads `request_payload` back from a persisted ledger:
  - The pi@0.57.1 and oh-my-pi 16.2.13 conformance comparators read source commits, tool observations and the transcript.
  - `headless.py` digests the ledger bytes.
  - Episode recovery compares journaled events with ledger events, and both carry the same new shape.
- Offline tools that need a full request can rebuild it with `reconstruct_policy_requests`. The digest makes the rebuilt request verifiable.
- The trainer still owns token IDs, logprobs, masks, advantages and policy versions. There is no `verl_wrapper` change.
- Conductor's responses/chat and native-stream paths and the terminal adapter all use the same constructor, so every profile gets the same ledger shape.

## 4) Change Classification

- Classification: `breaking` for readers of `PolicyRequestEvent.request_payload` in `bb.rl.runner-event-ledger.v2` objects. No in-repository reader exists.
- The terminal parity fixture was regenerated from the adapter's own output. Its pinned fixture and provenance digests changed with it.

## 5) Evidence and Validation Plan

- New `tests/rl/harness/test_runner_policy_request_delta.py` has 15 tests:
  - A 50-turn growing request round-trips exactly. The serialized request events total less than 3× the final request, where the old encoding was more than 20×.
  - Tampering, key change and removal, `keep=0` list replacement, delta shape validation, and digest mismatch are each covered.
- Linux Slurm job 122269 ran the same suites on `main` 5f44a794 and on this branch:
  - The new test file fails collection on `main` and passes 15/15 here.
  - terminal passes 166 here, versus 165 on `main`.
  - These pass on both trees: conductor 258, evidence 162, v2 service 156, oh-my-pi 16.2.13 comparator 69, pi@0.57.1 comparator 43.
  - These fail identically on both trees because of the test host, not this change: the provenance check (the source tree has no Git objects), 5 native-stream conductor cases (missing pinned Node package), 3 protocol-integration cases (`containment_receipt_invalid`) and 6 headless-runner cases.
- Required before merge: CI on the PR head.
- Required before any BMoE readiness claim: a runtime built from this source, plus an installed smoke with a long multi-turn episode below the 64 MiB bound.

## 6) Rollout Plan

- Merge into `kmccleary3301/breadboard` after CI. Build a sibling BreadBoard agent runtime (v9) from the merge commit; keep v8 immutable for the runs it proved.
- Rerun a BMoE BreadBoard Pi train on v9 and confirm there are zero `evidence object exceeds repository bound` failures before recording the BMoE envelope.

## 7) Rollback Plan

- Revert the PR through a normal revert PR on protected main. Ledgers already written with deltas stay readable through `reconstruct_policy_requests` in this commit's history.
- Do not roll back by raising `MAX_OBJECT_BYTES`.

## 8) Approvals

- This artifact records the implementation decision and its evidence. It does not assert an independent reviewer, Rob's acceptance, E4 final-cell acceptance, or BMoE promotion.
