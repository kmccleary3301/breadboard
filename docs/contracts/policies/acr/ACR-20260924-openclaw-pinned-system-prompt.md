# ACR-20260924-openclaw-pinned-system-prompt

- `acr_id`: `ACR-20260924-openclaw-pinned-system-prompt`
- `title`: Materialize the OpenClaw system prompt through pinned supplier code
- `author`: BreadBoard E4 OpenClaw implementation
- `date`: 2026-09-24
- `status`: implemented (local scoped proof; independent exact-head review required)

## 1) Problem Statement

The native worker previously substituted a short hand-authored advertisement for OpenClaw's actual system prompt. This changes model-visible instructions, skills, workspace context, and request bytes. The comparator also inferred a budget refusal from a declared cap rather than the supplier's recorded refusal.

## 2) Scope and Surfaces

- Extension: `breadboard/rl/harness/openclaw_tool_worker.mjs` and its sha-verified classifier loader.
- Comparator: `conformance/comparators/openclaw_2026_9_4.py`; OpenClaw semantics for malformed terminal tool calls.
- Contract surfaces: pinned source prompt construction, wire request comparison, supplier receipt controls, and 429 refusal.
- Kernel danger-zone: yes, because the model-visible native worker and typed lease runtime inputs change. The generic Conductor and other profiles remain unchanged.

## 3) Coupling and Generalization Impact

- The worker imports the pinned `buildAttemptSystemPrompt` through a sha-verified loader export. It invokes pinned runtime, skill, bootstrap and provider helpers rather than restating prompt text.
- The comparator uses one static grammar from the pinned prompt builder for both supplier and replay wire messages. Only the single `Current date:` line, declared workspace/package/home roots, and host, OS-release and explicit-session-id spans of the relocated `Runtime:` user line are normalized symmetrically; node, model, architecture, session-key shape and skill catalog remain exact.
- The pinned OpenAI transport removes the cache/relocatable boundary from the system message and relocates its runtime line into a user message (`@openclaw/ai/dist/openai-completions-stream-Da2vvl-S.mjs:459-469,615-639`). A leaked boundary or `Runtime:` system line fails comparison.
- The capture receipt has no independent host, OS or date fact record. Supplier values therefore come from captured wire; the replay date and session are checked against typed lease inputs, and replay host/OS/node/architecture/date against pinned worker facts. This supplier evidence limit is explicit, not candidate authority. The capture kit invokes `openclaw agent --session-id capture-…` (`openclaw_capture_supplier.py:122`); the replay uses the same explicit-session key shape with a lease-derived session id.

## 4) Change Classification

- Classification: `behavioral-change` (breaking the prior synthetic OpenClaw model-visible prompt intentionally).
- Compatibility window: none; only the pinned OpenClaw 2026.9.4 target is affected.
- Schema bump: none. The existing native phase and trace fields carry the source prompt and refusal controls.

## 5) Evidence and Validation Plan

- A pre-fix focused test rejects the synthetic prompt and verifies the pinned attempt sections, skills and bootstrap context after repair.
- Comparator tests reject altered prompt bytes, missing/duplicate dates, runtime boundary leakage, mismatched pinned runtime facts and missing budget/refusal controls; only declared symmetric date/root/runtime spans pass.
- Malformed-call test verifies the pinned finalizer's error stop and absence of replayable tool calls.
- Run focused OpenClaw tests and `scripts/check_danger_zone_acr.py` against explicit changed paths. Exact-head installed replay and independent review are required before promotion.

## 6) Rollout Plan

Review source-prompt construction, comparator normalization/refusal, and malformed-tool-call parity at the exact candidate head. Main owns installed replay and promotion.

## 7) Rollback Plan

Revert the reviewed OpenClaw-specific commits on a new branch if exact-head supplier/replay comparison fails; preserve raw evidence and rerun focused validation before another promotion attempt.

## 8) Approvals

- Kernel reviewer: independent exact-head review required.
- Contracts reviewer: independent exact-head review required.
- Ops reviewer: required before installed promotion.
- Final decision: Main retains promotion authority; this ACR does not authorize merge.
