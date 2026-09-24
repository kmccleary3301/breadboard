# ACR-20260924-openclaw-pinned-system-prompt

- `acr_id`: `ACR-20260924-openclaw-pinned-system-prompt`
- `title`: Materialize the OpenClaw system prompt through pinned supplier code
- `author`: BreadBoard E4 OpenClaw implementation
- `date`: 2026-09-24
- `status`: implemented (local scoped proof; independent exact-head review required)

## 1) Problem Statement

The OpenClaw worker emitted a pinned system prompt but the sealed target still advertised a duplicate hand-authored prompt asset. Its model wire omitted the pinned per-message user timestamp and internal runtime-context carrier. A malformed terminal tool call retained raw provider text as a successful-looking visible payload instead of the pinned source error presentation. The comparator accepted zero relocated runtime lines and normalized path literals in user instructions.

## 2) Scope and Surfaces

- Extension: `breadboard/rl/harness/openclaw_tool_worker.mjs`, its sha-verified classifier loader, and the OpenClaw semantics state.
- Comparator: `conformance/comparators/openclaw_2026_9_4.py`.
- Contract surfaces: source-owned prompt declaration, sealed target/asset table, typed lease timestamp, wire request comparison, and malformed terminal classification.
- Kernel danger-zone: yes, because the source worker, typed runtime inputs, and v2 target prompt declaration change. The generic Conductor and other profiles remain unchanged.

## 3) Coupling and Generalization Impact

- The source-owned `buildAttemptSystemPrompt` binding replaces the duplicate prompt asset and advertisement. The canonical `serialize_e4_target` output reseals the target descriptor and index; the classifier loader and required bootstrap asset are packaged.
- The worker invokes sha-verified pinned runtime/skill/bootstrap/provider helpers, the LLM-boundary user timestamp normalizer, the runtime-facts context producer, and the source transcript carrier conversion. The malformed error copy is rendered by pinned source code before the pinned classifier projects its envelope.
- The comparator uses one grammar on supplier and replay. Only the date value, pinned system-builder path fields, the leading user timestamp, and host/OS-release/explicit-session spans of exactly one relocated `Runtime:` line per request are normalized. Literal user paths, model, node, architecture, session-key shape, skill catalog and tool schemas remain exact. Replay timestamp and runtime line must agree with pinned worker facts; the declared timestamp comes from a typed lease input.
- The pinned transport relocates `Runtime:` into a user message. The capture receipt has no independent host, OS or date fact record, so supplier values come from captured wire; replay facts are checked against typed inputs and the pinned worker. This limit is explicit, not a candidate exemption.

## 4) Change Classification

- Classification: `behavioral-change` (removes the synthetic prompt and repairs model-wire and terminal-error parity).
- Compatibility window: none; only the pinned OpenClaw 2026.9.4 target uses the source-owned prompt.
- Schema bump: none. The v2 descriptor/config schemas now require exactly one prompt asset or pinned prompt source; the other target packages retain their asset declarations.

## 5) Evidence and Validation Plan

- Pre-fix comparator tests failed when the relocated Runtime line was omitted and when different literal user paths were incorrectly erased; frozen exact-head review found missing timestamp/carrier, extra prompt asset, and malformed classification mismatch.
- Focused tests exercise pinned prompt construction, per-message timestamp and structured internal context, source malformed-error projection, asymmetric prompt mutations, target resealing/asset inclusion, and request-cap controls.
- Local six-case request projection records index 0 per case; macOS skill eligibility and architecture intentionally differ from the Linux supplier capture. Higher request indices and installed acceptance require the independent Linux replay. `scripts/check_danger_zone_acr.py` checks the explicit changed-file set.

## 6) Rollout Plan

Independent review must inspect source-prompt construction, comparator normalization, malformed classification, and exact-head asset packaging. Main owns installed Linux replay and promotion; local projections do not establish acceptance.

## 7) Rollback Plan

Revert the reviewed OpenClaw-specific commits on a new branch if exact-head supplier/replay comparison fails; preserve raw evidence and rerun focused validation before another promotion attempt.

## 8) Approvals

- Kernel reviewer: independent exact-head review required.
- Contracts reviewer: independent exact-head review required.
- Ops reviewer: required before installed promotion.
- Final decision: Main retains promotion authority; this ACR does not authorize merge.
