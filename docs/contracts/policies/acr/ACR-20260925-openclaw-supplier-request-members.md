# ACR-20260925-openclaw-supplier-request-members

- `acr_id`: `ACR-20260925-openclaw-supplier-request-members`
- `title`: Make the OpenClaw replay request carry exactly the pinned supplier's request members
- `author`: BreadBoard E4 OpenClaw implementation
- `date`: 2026-09-25
- `status`: implemented (local scoped proof; independent exact-head review and installed Linux replay required)

## 1) Problem Statement

In DO-2 overlay job 1309, the members of BreadBoard's OpenClaw request body differed from the supplier capture.

| Side | Request members |
|---|---|
| Supplier (`buildOpenAICompletionsParams`) | `max_completion_tokens`, `messages`, `model`, `stream`, `stream_options`, `tool_choice`, `tools` |
| BreadBoard | `max_tokens`, `messages`, `model`, `store`, `stream`, `stream_options`, `tools` |

There were two causes:

- **BreadBoard emitted `store: false` for OpenClaw unconditionally.** The shared Pi/OpenClaw branch in `OpenAIChatRuntime.profile_chat_request` did this. The provider profile also had no way to express `tool_choice`.
- **The worker gave the supplier an invented model.** `openclaw_tool_worker.mjs` passed `buildOpenAICompletionsParams` a model config carrying a Pi-shaped `compat` override (`supportsStore: true`, `maxTokensField: "max_tokens"`). OpenClaw builds its model from a `models.providers` entry, and the supplier capture's entry declares no `compat`. As a result, `getCompat(model)` should derive the request compat from the provider and `baseUrl`. For the capture's local endpoint, that derivation gives `max_completion_tokens` and no `store`.

Nothing compared the member set that BreadBoard sent with the member set the supplier builder produces. The divergence surfaced only in the offline comparator.

## 2) Scope and Surfaces

- **Provider profile** (`breadboard_engine/provider/profiles.py`): `OpenAICompletionsRequestPolicy` gains a typed `tool_choice: Literal["auto"] | None = None`.
  - When set, it is validated, serialized and included in profile identity.
  - It requires `capabilities.supports_tools`.
  - It is emitted only on requests that carry tools, is declared as the `tool_choice` request feature, and appears in provenance.
- **Chat runtime** (`breadboard_engine/provider/runtimes/openai/chat.py`): `store: false` is now emitted only for the Pi consumer (Pi's `buildParams`). The OpenClaw consumer emits no `store`. Pi's wire is unchanged.
- **OpenClaw worker** (`breadboard/rl/harness/openclaw_tool_worker.mjs`):
  - `initialize` resolves the supplier model with the pinned `buildInlineProviderModels` (`model.inline-provider-BOrD-NlO.mjs`, export `t`). That module is added to `MODULE_DIGESTS` with sha256 `8feef4dd…46c2`.
  - The resolver receives only the provider entry members that OpenClaw's config declares (`baseUrl`, `api`, `models[{id, name, contextWindow, maxTokens, input}]`). No `compat` override reaches it.
  - `projectSourceRequest` returns `request_members`, the sorted keys of the pinned `buildOpenAICompletionsParams` result.
- **Tool-only client** (`breadboard/rl/harness/openclaw_native_tools.py`): the model config no longer carries the unused, invented `compat` block.
- **Conductor** (`breadboard/rl/harness/runners/conductor.py`): a generic oracle is added.
  - When a native worker's `project_request` result carries `request_members`, the sent request body must have exactly that member set.
  - Otherwise it raises `RunnerProtocolError` with code `native_request_members_mismatch` before the request is recorded or any tool effect runs.
  - The oracle names no profile.
- **Target** (`config/e4_targets/openclaw/2026.9.4/native-config.json`): the declared `model` block now states `max_completion_tokens: 2048` and `tool_choice: "auto"`, and drops `max_tokens` and `store`.
  - `target.json`, `index.json` and the pin in `breadboard/product/harness/targets.py` were regenerated through `serialize_e4_target`.
  - The new descriptor sha256 is `8cd597424ae5ab817574b7f6f57887fa1422c56e65383116e0d37190a42d3f6a`.
- **Tests** (`tests/rl/harness/test_openclaw_native_stream_conductor.py`):
  - The episode fixture uses the supplier wire policy (`max_completion_tokens`, `tool_choice: "auto"`).
  - `test_openclaw_native_stream_cap_refuses_ninth_http_request` pins the exact supplier member set and values on every request.
  - `test_openclaw_request_members_must_match_pinned_source_builder` checks that a divergent profile (`max_tokens`, `store`, no `tool_choice`) fails with `native_request_members_mismatch` and that no tool effect occurs.
- Kernel danger-zone: yes, `breadboard/rl/harness/openclaw_tool_worker.mjs`, `breadboard/rl/harness/runners/conductor.py`, `breadboard/rl/harness/openclaw_native_tools.py` and `breadboard/product/harness/targets.py` are under the kernel protected surface (`breadboard/**`).

## 3) Coupling and Generalization Impact

- The `request_members` oracle is generic. Other native workers are unaffected until they report the field.
- The policy provider still passes its shared model config to both the Pi and OpenClaw workers. The OpenClaw worker gives the supplier resolver only the members that OpenClaw's provider config declares. The Pi path, its profile identity and its target descriptor are unchanged.
- The OpenClaw run profile must now carry `max_token_field: "max_completion_tokens"` and `tool_choice: "auto"`. Otherwise the conductor fails closed.

## 4) Change Classification

- Classification: `behavioral-change`. The OpenClaw replay wire members equal the pinned supplier builder's members, and a mismatch fails closed.
- Compatibility window: none.
- Schema bump: none. The new request-policy field is optional and defaults to absent.

## 5) Evidence and Validation Plan

- **Supplier probe:** calling the pinned `buildInlineProviderModels` and then `buildOpenAICompletionsParams` for a local `openai` provider entry gives the keys `max_completion_tokens, messages, model, stream, stream_options, tool_choice, tools`, with `max_completion_tokens` 2048 and `tool_choice` "auto".
- **Local tests:**
  - `test_openclaw_native_stream_conductor.py`: 18 passed
  - `test_pi_native_stream_conductor.py`: 25
  - `test_openclaw_2026_9_4_native_tools.py`: 12 passed, 1 skipped
  - OpenClaw semantics: 10
  - `test_openai_profile.py`: 24
  - `test_e4_targets.py`: 29
  - OpenClaw comparator: 42 passed, 7 skipped
  - Pi comparator: 20
  - Pi prompt materialization: 8
  - native response: 12
  - Pi semantics: 12
  - rerun semantics: 5
- **Reverse-apply:**
  - Restoring the base product files makes `test_openclaw_native_stream_conductor.py` fail (7 failed).
  - Restoring only `conductor.py` makes the member-mismatch test fail.
- **Pending:** an installed Linux replay of all six OpenClaw cases.

## 6) Rollout Plan

1. An independent exact-head review checks the imported supplier resolver, its digest, the Pi wire invariance and the generic oracle.
2. The lane is rebuilt into a versioned SIF.
3. The six OpenClaw cases are replayed on DO-2 against the supplier capture.

## 7) Rollback Plan

Revert the commit. The OpenClaw wire then reverts to `max_tokens` and `store` without `tool_choice`, which is the known divergent state.

## 8) Approvals

- Kernel reviewer: independent exact-head review required.
- Contracts reviewer: independent exact-head review required.
- Final decision: Main retains promotion authority; this ACR does not authorize merge.
