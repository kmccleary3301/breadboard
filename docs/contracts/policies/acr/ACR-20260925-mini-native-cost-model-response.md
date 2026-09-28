# ACR-20260925-mini-native-cost-model-response

- `acr_id`: `ACR-20260925-mini-native-cost-model-response`
- `title`: Mini's cost hook prices the converted LiteLLM response again
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-25
- `status`: implemented

## 1) Problem Statement

Mini's DO-2 installed replay at 032ce7ba (job 1403) reported `counters.model_cost = 0` in every priced case. The admitted supplier capture mci023 has non-zero costs, for example abc_submit at 2.925e-05. This produced 12 `counters_equal` failures.

81076d1c (#139) changed the pricing closure to `native_cost(raw)`, which calls `litellm.ModelResponse(**thaw_json(raw))`. Its only caller still passes the converted `ModelResponse` returned by `_mini_model_response`. `thaw_json` returns non-mapping objects unchanged, so the `**` unpacking raises `TypeError: argument after ** must be a mapping, not ModelResponse`. The source's `cost_tracking=ignore_errors` handler then records 0.0. Mini's last installed replay (IBM, 56cea652) predates 81076d1c, which is why this went unnoticed.

Reproduction with LiteLLM 1.101.0 on the mci023 abc_submit response:
- the 56cea652 path gives 2.925e-05;
- the 032ce7ba path raises the TypeError above.

## 2) Scope and Surfaces

- Kernel modules touched: `breadboard/rl/harness/policy_provider.py`.
  - New module-level `_mini_native_cost(response, *, model)` calls `litellm.cost_calculator.completion_cost(response, model=model)`. The call, the positive-cost check and the ignore-errors fallback are the same as the pinned source and as 56cea652.
  - The Mini client binds that function with `model="openai/<profile model>"`.
  - The sealed local-catalog admission checks are unchanged.
- Contract surfaces touched: none.
- Kernel danger-zone: yes, the provider policy client under `breadboard/**`.

## 3) Coupling and Generalization Impact

- Core -> extension dependency: no. The code runs only on the Mini renderer branch.
- Other profiles: none. The hook is bound only on the Mini renderer branch and is `None` for every other target. In `invoke`, Pi, OMP and OpenClaw return before the hook is reached.

## 4) Change Classification

- Classification: `internal`.
- Required schema/version bumps: none.

## 5) Evidence and Validation Plan

- New test `test_native_cost_prices_the_converted_response_like_supplier_abc`. It uses LiteLLM and the committed probe fixture (71/31 tokens) and expects 2.925e-05, which is supplier abc_submit's cost.
- `tests/rl/harness/test_mini_semantics.py`: 13 passed. `tests/rl/harness/test_policy_provider.py`: 25 passed (both with LiteLLM 1.101.0).
- Required after merge: Mini's installed replay under the envelope at the new head, plus the other five profiles' replays at that head.

## 6) Rollout Plan

- Rollout phases: branch `fix/mini-native-cost-model-response`.
- Flags/toggles: none.
- Blast radius constraints: the Mini provider cost counter only.
- Monitoring hooks: the test above and the Mini installed replay's `counters_equal`.

## 7) Rollback Plan

- Trigger conditions: a Mini cost counter that differs from its supplier capture.
- Exact rollback commands: `git revert <commit>`.
- Artifact/state restoration steps: none.
- Post-rollback verification: rerun the Mini semantics and policy provider suites.

## 8) Approvals

- Kernel reviewer: Main (campaign orchestrator) under standing approval
- Contracts reviewer: Main (campaign orchestrator)
- Ops reviewer: Main (campaign orchestrator)
- Final decision: Main (campaign orchestrator)
