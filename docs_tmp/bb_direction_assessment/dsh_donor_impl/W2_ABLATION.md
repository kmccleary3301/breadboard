# W2.4 / RQ-ABLATION

## Result

**Exact reconstruction: 7 of 7 named requests across six Sessions.** The run set has six distinct Session IDs: two W1 FT-01 cells (`authority-replacement-before-submit` with `yolo`; `permission-after-dispatch` with `write`) and four W3 FT-02 cases (`invalid-edit-in-flight` with `yolo`; `reconfigure-mid-turn` with `write`; `reconfigure-across-restart` with `yolo`; `provider-swap-between-turns` with `write`). The final Session emits two actual requests, preserving exactly one provider swap, `mock/provider-a` → `mock/provider-b`; the A/B profiles have distinct identities and effective wire temperatures (0.1/0.2).

| Request | Session fixture | Approval | Provider route/profile | Reconstruction | Miss cause |
|---|---|---|---|---|---|
| W1 FT-01 authority replacement | `w24-session-01-yolo` | `yolo` | `cli_mock/reference` | exact | none |
| W1 FT-01 permission settlement | `w24-session-02-write` | `write` | `cli_mock/reference` | exact | none |
| W3 FT-02 invalid in-flight edit | `w24-session-03-yolo` | `yolo` | `cli_mock/reference` | exact | none |
| W3 FT-02 mid-turn reconfigure | `w24-session-04-write` | `write` | `cli_mock/reference` | exact | none |
| W3 FT-02 restart durability | `w24-session-05-yolo` | `yolo` | `cli_mock/reference` | exact | none |
| W3 FT-02 provider A leg | `w24-session-06-write` | `write` | `mock/provider-a` / `base_url:8111`, `temperature:0.1` | exact | none |
| W3 FT-02 provider B leg | `w24-session-06-write` | `write` | `mock/provider-b` / `base_url:8222`, `temperature:0.2` | exact | none |

## Method and limits

The permanent parameterized test is `tests/providers/test_w2_request_reconstruction.py::test_records_only_ablation_reconstructs_each_request`. Each case builds a records-only Session fixture and dispatches through the existing `_HeldOutRuntime`; the adapter materializes held-out canonical bytes only after dispatch into an in-memory field, never into a retained capture. Reconstruction uses the W2.2 `OpenAIChatRuntime.profile_chat_request` interface from the corresponding Session/prompt/tool/Lock records, compares bytes, then clears the held-out field. No raw capture is retained.

**Generalization risk — provider variety:** medium-high. Exactness covers the OpenAI Chat profile and fake post-dispatch adapter only. The provider-swap Session now exercises two actual effective requests with distinct profile identities and wire fields, but both sides intentionally use the same W2.2 OpenAI profile contract; this does not establish parity for Anthropic, Responses, or provider-specific payloads.

**Generalization risk — mode variety:** medium. Both `yolo` and `write` are represented, and each run has a distinct mode-specific prompt contribution. The records-only check does not claim permission UI behavior, a non-OpenAI mode adapter, or modes with additional runtime-owned request fields. No miss was observed, so no W2.2 work item or W9 amendment is opened by this set.
