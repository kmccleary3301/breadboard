# W2.4 / RQ-ABLATION preflight

## Pending result

**Not accepted as W2.4 evidence.** The synthetic run set reconstructs 7 of 7 requests across six fixture identities, but it does not consume provider records emitted by the W1.1 and W3.1 characterization Sessions. W2.4 remains pending until those characterized cases are rerun with retained provider records or equivalent retained records are recovered.

| Synthetic request | Fixture identity | Approval | Provider route/profile | Reconstruction | Miss cause |
|---|---|---|---|---|---|
| W1 FT-01 authority replacement | `w24-session-01-yolo` | `yolo` | `cli_mock/reference` | exact | none |
| W1 FT-01 permission settlement | `w24-session-02-write` | `write` | `cli_mock/reference` | exact | none |
| W3 FT-02 invalid in-flight edit | `w24-session-03-yolo` | `yolo` | `cli_mock/reference` | exact | none |
| W3 FT-02 mid-turn reconfigure | `w24-session-04-write` | `write` | `cli_mock/reference` | exact | none |
| W3 FT-02 restart durability | `w24-session-05-yolo` | `yolo` | `cli_mock/reference` | exact | none |
| W3 FT-02 provider A leg | `w24-session-06-write` | `write` | `mock/provider-a` / `base_url:8111`, `temperature:0.1` | exact | none |
| W3 FT-02 provider B leg | `w24-session-06-write` | `write` | `mock/provider-b` / `base_url:8222`, `temperature:0.2` | exact | none |

## Preflight method and limits

`tests/providers/test_w2_request_reconstruction.py::test_records_only_ablation_reconstructs_each_request` builds a records-only fixture for each named case and dispatches through `_HeldOutRuntime`. This proves the reconstructor and fake-adapter oracle can compare canonical bytes without retaining captured request bytes. It does not prove that the inputs came from the characterized W1.1/W3.1 Session owners.

**Generalization risk — provider variety:** medium-high. Exactness covers the OpenAI Chat profile and fake post-dispatch adapter only. The provider-swap Session now exercises two actual effective requests with distinct profile identities and wire fields, but both sides intentionally use the same W2.2 OpenAI profile contract; this does not establish parity for Anthropic, Responses, or provider-specific payloads.

**Generalization risk — mode variety:** medium. Both `yolo` and `write` are represented and each fixture has a distinct mode-specific prompt contribution. The preflight does not exercise the actual permission, authority-replacement, restart, or reconfiguration paths. The accepted run must preserve those Session-owned facts and report each actual request as exact or name its missing source fact.
