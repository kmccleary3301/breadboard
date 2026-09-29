# ACR-20260929-policy-output-verbatim

- `acr_id`: `ACR-20260929-policy-output-verbatim`
- `title`: Keep policy-authored provider output verbatim through result sanitization
- `author`: BreadBoard RL implementation
- `date`: 2026-09-29
- `status`: implemented

## 1) Problem Statement

`sanitize_provider_result` (`breadboard_engine/provider/contract_runtime.py`) runs before the provider lease is released. It sent every string in the result through `redaction.scrub_text`, which replaces both the registered operation secrets and every `SECRET_VALUE_PATTERNS` credential shape (`Bearer <16+>`, `sk-…`, `ghp_…`, JWT, and others) with `***REDACTED***`. That included the model-authored fields: assistant text, reasoning, tool-call names and arguments. The scrubbed arguments became `call.arguments`/`arguments_json`. On the RL path they reach the policy transcript through `_provider_result_to_responses` and the tool executor through the Conductor, so a policy that *wrote* a credential-shaped literal had it rewritten in two places: the action it took, and the assistant turn replayed to it next.

Measured defect on IBM Slurm, 2026-09-29, runtime v10, MiMo code train, episode flal73j7:
- The policy's `edit` call had `newText` containing `// Format: "Bearer tumblr-token-here"`.
- BreadBoard's next request replayed it as `// Format: "***REDACTED***"`.
- The NeMo Gym relay rejected the request (HTTP 400, "Continuous Token messages must be append-only"), and the episode failed.
- Of 888 BreadBoard episodes after 05:33Z, 1 had a replayed assistant message carrying `***REDACTED***` that the served output lacked.

## 2) Scope and Surfaces

- Kernel danger-zone: yes. This change touches `breadboard_engine/provider/contract_runtime.py`, `breadboard_engine/provider/contract_messages.py` and `breadboard_engine/security/redaction.py`.
- `redaction.scrub_text(..., credential_shapes=True)` gains a keyword that defaults to true. Passing false keeps exact registered-secret redaction and skips the shape patterns.
- `sanitize_provider_result` passes `credential_shapes=False` only for the model's own words:
  - Model text: plain strings, and the `text` of `text`/`thinking` blocks, in `message.content`, `message.reasoning`, `result.reasoning_blocks`, `result.reasoning_summaries`, and the reasoning annotations `annotations["reasoning_content" | "reasoning" | "reasoning_details"]`.
    - `ProviderMessage.as_dict()` merges those annotations with `reasoning` into thinking blocks, deduplicating only identical blocks. So they get exactly `reasoning`'s treatment; otherwise the turn would carry a verbatim block and a redacted block.
    - The keys are `contract_messages.REASONING_ANNOTATION_KEYS`, shared with that merge.
  - Tool-call `name` and `parsed_arguments` (and therefore `arguments`/`arguments_json`).
- Provider-owned material keeps full shape plus registered redaction:
  - inside those fields: `redacted_thinking` data, `provider_replay` envelopes, media URIs, and any non-text block or provider object inside content or reasoning;
  - separate fields: `raw_message`, `raw_choice`, `raw_response`, tool-call `id` and `raw`, `finish_reason`, `message_id`, non-reasoning `annotations`, `tool_results`, `usage`, `encrypted_reasoning`, `provider_replay`.
- Registered operation secrets (the profile's scoped credential and caller header values) are still removed from model-authored fields.
- Evidence and log sinks keep their own full redaction: `logging/provider_dump.py`, request evidence in `conductor/modes.py`, and `conductor/implementation_receipts.py`.
- Not changed: `OpenAIChatRuntime.invoke_native` (source-native `runtime_profile` targets) still fails closed with `native_response_redaction_required` when a native response contains a credential shape.

## 3) Coupling and Generalization Impact

- Provider- and harness-neutral. Every runtime whose `invoke` wraps `sanitize_provider_result` now hands the model's own output to execution and replay unchanged, and replay stays append-only.
- A credential-shaped literal authored by the model is content the model will act on, not a secret the runtime owns. Treating it as a secret silently changed tool actions: a `write` of such a literal would write `***REDACTED***` to disk.

## 4) Change Classification

- Classification: `behavioral-change`. Model-authored provider output keeps credential-shaped literals. There is no schema or wire-format change.

## 5) Evidence and Validation Plan

- Regression test `tests/rl/harness/test_policy_provider.py::test_profile_client_replays_credential_shaped_policy_output_verbatim`, run through the real `OpenAIChatRuntime.invoke` sanitization:
  - Assistant text and the arguments of a known tool (`edit`) and an unknown tool (`rule`) keep their Bearer, `sk-`, `ghp_` and JWT literals.
  - The registered scoped credential is still redacted.
  - The test fails on the previous code and passes with the change.
- Boundary test `tests/providers/test_provider_message_contracts.py::test_sanitize_keeps_model_authored_output_and_redacts_everything_else`:
  - These keep their credential-shaped literals: text-block content, thinking text (in `reasoning`, the `reasoning_content` annotation and `result.reasoning_blocks`), and tool arguments. The turn yields exactly one thinking block.
  - The registered operation secret is removed.
  - These stay fully redacted: `redacted_thinking` data (in both `reasoning` and `reasoning_blocks`), a non-reasoning annotation, and `raw_message`.
  - Without the annotation handling the test fails with a second, redacted thinking block. With a blanket reasoning exemption it fails with unredacted `redacted_thinking` data.
- Neighbouring suites (providers, security, RL policy runtime, request delta, production policy HTTP, conductor, evidence, v2 service/protocol) have the same results before and after, on macOS and on IBM Linux (job 124907).
- Installed-runtime smoke on the real flal73j7 bytes:
  - Runtime v10 reproduces the rejected replay exactly.
  - Runtime v11 maps the served arguments verbatim.

## 6) Rollout Plan

- RL runtime lineage: `e4src/rl-runtime-v11` (v10 `7cb30f6d` plus this change) builds `bbagent-runtime/v11`.
- Main: this PR.

## 7) Rollback Plan

- Revert the commit. There is no configuration surface.

## 8) Approvals

- This artifact records the implementation decision and its evidence. It does not assert external PR acceptance or BMoE promotion.
