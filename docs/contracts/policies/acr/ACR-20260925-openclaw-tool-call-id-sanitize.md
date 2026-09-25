# ACR-20260925-openclaw-tool-call-id-sanitize

- `acr_id`: `ACR-20260925-openclaw-tool-call-id-sanitize`
- `title`: Apply the supplier's replay tool-call-id sanitization in the OpenClaw request projection
- `author`: BreadBoard E4 OpenClaw implementation
- `date`: 2026-09-25
- `status`: implemented (local scoped proof; independent exact-head review and installed Linux replay required)

## 1) Problem Statement

In DO-2 overlay job 1309, BreadBoard's replay wire carried truncated tool-call ids, for example `ocl-capture-streaming_fragmented_write-0` for the streamed id `ocl-capture-streaming_fragmented_write-00-00`. The supplier capture carried `oclcapturestreamingfragmentedwrite0000`.

- **What the receiver sends:** the id is emitted only in the first delta chunk, and BreadBoard's stream decoder keeps the full id.
- **Where truncation happens:** `buildOpenAICompletionsParams` (`@openclaw/ai` openai-completions-stream `normalizeToolCallId`) truncates ids longer than 40 characters for the `openai` provider.
- **Why the supplier is unaffected:** `builtin-openclaw-B-H-7lKk.mjs:15267-15286` wraps the stream function with `sanitizeReplayToolCallIdsForStream`, which calls `sanitizeToolCallIdsForCloudCodeAssist(messages, "strict", ...)`. This happens whenever `shouldApplyReplayToolCallIdSanitizer` (`:13915`) holds for the resolved transcript policy. `openclaw_tool_worker.mjs` skipped that step.

## 2) Scope and Surfaces

- **Implementation:** `breadboard/rl/harness/openclaw_tool_worker.mjs`.
  - `projectSourceRequest` resolves the transcript policy with the supplier's `resolveAttemptTranscriptPolicy` (`history-image-prune-BCKEHO6_.mjs`, export `s`), using the worker's model config, provider, source config and workspace.
  - When the policy asks for it, the history is passed through `sanitizeToolUseResultPairing` (`session-transcript-repair-BqMz_6TX.mjs`, export `i`) and then through `sanitizeToolCallIdsForCloudCodeAssist` (`tool-call-id-CnwowhSs.mjs`, export `o`).
  - The sanitizer options come from the supplier's `shouldAllowProviderOwnedThinkingReplay` (`helpers-C__iuzW9.mjs`, export `S`) and `collectAllowedToolNames` (`builtin-openclaw-B-H-7lKk.mjs`, export `s`).
  - All four modules are added to `MODULE_DIGESTS`, so they are loaded only when their sha256 matches.
  - Two non-exported supplier expressions are restated, each with a dist `file:line` citation: the one-line `shouldApplyReplayToolCallIdSanitizer` predicate (`:13915`) and the `isOpenAIResponsesApi` expression (`:18442`).
- **Tests:** `tests/rl/harness/test_openclaw_native_stream_conductor.py`.
  - `test_openclaw_replay_tool_call_id_sanitizes_long_id_on_wire` checks that a history id longer than 40 characters reaches the wire, in both the assistant `tool_calls[].id` and the tool `tool_call_id`, as the id the pinned supplier sanitizer produces. The test computes that expected id by calling the dist function.
  - `test_openclaw_conductor_commits_poll_before_ack` now matches on the supplier-sanitized wire ids.
- **Kernel danger-zone:** yes (`breadboard/**`).

## 3) Coupling and Generalization Impact

- Only the wire projection is affected. BreadBoard's own history, ledger and execution keep the original ids, matching the supplier, whose stream wrapper also transforms only the outgoing messages.
- The resolved policy for the E4 target is `sanitizeToolCallIds: true`, `toolCallIdMode: "strict"` and `repairToolUseResultPairing: true`.
- No conductor or policy-provider change was made, and no profile names were added outside the OpenClaw worker.

## 4) Change Classification

- Classification: `behavioral-change`. The OpenClaw replay wire ids now equal the supplier's sanitized ids.
- Compatibility window: none.
- Schema bump: none.

## 5) Evidence and Validation Plan

- Reverse-apply: against the base `openclaw_tool_worker.mjs` (3ada0f6a), both tests fail (2 failed). With the change, `test_openclaw_native_stream_conductor.py` passes 17/17.
- Installed Linux replay of all six OpenClaw cases is required after PR #145 merges and the SIF is rebuilt.
