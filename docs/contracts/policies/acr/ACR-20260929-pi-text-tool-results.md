# ACR-20260929-pi-text-tool-results

- `acr_id`: `ACR-20260929-pi-text-tool-results`
- `title`: Project Pi-shaped RL tool observations to text function-call outputs
- `author`: BreadBoard RL implementation
- `date`: 2026-09-29
- `status`: implemented

## 1) Problem Statement

BreadBoard's Conductor runner builds each tool result sent to the policy as `{"type":"function_call_output","call_id":…,"output": json.dumps(thaw_json(observation), sort_keys=True, separators=(",",":"))}`. The policy therefore sees the whole Pi tool-result object, for example `{"content":[{"text":"…","type":"text"}],"details":{…truncation…},"isError":false}`, with non-ASCII escaped (`\ufffd` becomes 6 chars and control bytes become `\u0000`).

Measured defect facts on IBM Slurm (2026-09-29):
- In every BreadBoard Pi SWE train episode (BMoE 123168, episode r8uexmh8), each tool message is this JSON wrapper.
- MiMo general 123245: after a Pi `read` of a binary .xlsx, BreadBoard's single tool message was 240,274 chars. Stock Pi (0.57.1) sent 43,024 chars for the byte-identical read, with the same 11,138 U+FFFD. BreadBoard's next request held 138k–145k message tokens (over the 131,072 context) and 4/4 episodes failed. Stock Pi continued for 612 requests.

## 2) Scope and Surfaces

- Kernel danger-zone: yes. This change touches `breadboard/rl/harness/runners/conductor.py`, `breadboard_engine/compilation/server_compiler.py`, and the frozen kernel configuration surface `contracts/kernel/schemas/bb.agent_config_surface.v2.schema.json`, plus its generated TypeScript types/registry (`sdk/ts-kernel-contracts/src/generated/`) and bundled Node validator (`breadboard_engine/execution/node/author-bridge-helper.mjs`).
- Compiled `provider_tools.tool_results_as_text` is an opt-in boolean, default false. The v2 surface schema, generated TypeScript contract and bundled Node validator declare the same field.
- When enabled, every Conductor path building a `function_call_output.output` from a Pi-shaped observation (`{"content":[…], …}`) produces reference Pi text semantics (matching upstream `@mariozechner/pi-ai/dist/providers/openai-responses-shared.js` 0.57.1):
  1. join the `text` of the `content` blocks whose `type == "text"` with `"\n"`;
  2. if that is empty, use `"(see attached image)"`;
  3. apply unpaired surrogate removal (`_sanitize_surrogates`).
  If an observation is not Pi-shaped, it fails closed with runner protocol error `tool_result_invalid`.
- When disabled (false or omitted), the bytes remain exactly the existing JSON encoding.
- The full observation (including `details` and `isError`) remains preserved in `ToolObservationEvent`, ledger, and evidence; only the policy-visible `output` string changes.
- Transcript byte accounting (`_encoded_json_size(output_item)`) continues to apply unchanged.

## 3) Coupling and Generalization Impact

- Trainer and policy interfaces remain decoupled; only the policy request payload's `function_call_output` string is formatted per profile emulation expectations.
- Live, replay, resume, and checkpoint paths produce the identical output string for the same observation, preserving append-only prefix equality across turns. A single shared encoder helper is used across all sites.
- The switch is strictly opt-in and defaults to false; existing non-Pi profiles and profiles without the flag retain unmodified JSON output encoding.

## 4) Change Classification

- Classification: `additive`.
- No schema version bump: `bb.agent_config_surface.v2` admits optional `provider_tools.tool_results_as_text: boolean` defaulting to false, pinned under freeze policy evolution packet `ACR-20260929-pi-text-tool-results` and ref `AM35`.

## 5) Evidence and Validation Plan

- Unit tests in `tests/rl/harness/test_runner_conductor.py`:
  - Flag true gives exact Pi text for multi-block observations, `isError` observations, image-only observations, and lone-surrogate observations.
  - Flag false preserves byte-identical JSON encoding.
  - Malformed observations fail closed under the flag with `tool_result_invalid`.
  - Multi-turn append-only prefix equality is verified across turns with the flag on.
- Unit tests in `tests/compilation/test_config_surface_v2.py`:
  - Flag parses into runtime view for both `true` and `false`.
  - Compiler rejects non-boolean values.
- Freeze verification via `scripts/check_phase20_freeze.py` passes with pinned hash.

## 6) Rollout Plan

- Ship on branch `e4src/pi-text-tool-results`, verify focused conductor and compiler test suites, run freeze check.
- Enable `tool_results_as_text: true` in Pi profiles targeting Responses API to match upstream Pi 0.57.1 behavior and eliminate ballooning JSON token overhead.

## 7) Rollback Plan

- If consumers observe unexpected behavior, disable `provider_tools.tool_results_as_text` in agent configuration to revert immediately to default JSON encoding without code deployment.
- Revert commits on `main` if structural regression occurs.

## 8) Approvals

- This artifact records the implementation decision and evidence; it does not assert external PR acceptance or BMoE promotion.
