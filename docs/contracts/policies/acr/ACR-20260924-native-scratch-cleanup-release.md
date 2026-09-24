# ACR-20260924-native-scratch-cleanup-release

- `acr_id`: `ACR-20260924-native-scratch-cleanup-release`
- `title`: Admit the released native scratch step in primary cleanup evaluation
- `author`: BreadBoard E4 campaign
- `date`: 2026-09-24
- `status`: implemented

## 1) Problem Statement

`SandboxRuntime._close_lease` adds a `native_scratch` cleanup step to every lease that created per-lease native scratch (`_create_native_scratch` during the `initialize` native phase, since e311a07). `service._cleanup_released` admits only `runtime`, `workspace`, `cache_holder`, and `lease_record`, plus `child_verifier`, for primary leases. So a lease whose scratch was fully released still produced `resource_set <= allowed_resources == False`. The service then quarantined the episode with `cleanup_not_released` (service.py `_v2_failure("cleanup", "cleanup_not_released", ...)`). The OpenHands capture 1113 quarantine tombstone recorded exactly this, and every native-worker session (Pi, OMP, OpenHands, Hermes, OpenClaw) takes the same path.

## 2) Scope and Surfaces

- Kernel module touched: `breadboard/rl/harness/service.py` (`_cleanup_released`, primary-lease default only).
- Extension modules touched: none.
- Contract surfaces touched: episode cleanup disposition; no schema, event, or receipt shape changes.
- Is this a **kernel danger-zone** change? `yes`.

## 3) Coupling and Generalization Impact
- Danger-zone: yes.
- Does this add any core -> extension dependency? `no`.
- Does this narrow cross-harness parity behavior? `no`.
- Does this alter default endpoint semantics? `no`.
- Coupling risk: `low`. `native_scratch` becomes an allowed optional resource for primary leases. The four required resources are unchanged, every present step must still be `released` or `already_released`, duplicates are still rejected, and verifier leases (explicit `required=` sets) are unchanged. A `native_scratch` step in `failed` or `quarantined` state still quarantines.

## 4) Change Classification

- Classification: `internal` (bug fix to kernel cleanup evaluation; no public contract change).
- Compatibility window: none; receipts without `native_scratch` evaluate identically.
- Required schema/version bumps: none.

## 5) Evidence and Validation Plan

- Required contract lane tests: `tests/rl/harness/test_v2_service.py::test_native_scratch_cleanup_receipt_released_avoids_quarantine_and_failed_quarantines`. It fails at `1a1668bf` (`_cleanup_released` returns False for a released receipt that includes `native_scratch`) and passes after the fix. It also asserts that a failed `native_scratch` step still quarantines.
- Required replay/parity checks: the installed native replays of the E4 profiles must end without `cleanup_not_released` quarantine.
- Required conformance/ablation checks: `scripts/check_danger_zone_acr.py` on the changed-file list.
- Required evidence bundles to refresh: none.
- Acceptance criteria: a released native-scratch lease is not quarantined; a non-released native-scratch step is.

## 6) Rollout Plan

1. Exact-head independent review.
2. Danger-zone ACR check and the focused service test.
3. Merge with `--match-head-commit`; native replays rebased onto the merge.

- Flags/toggles: none.
- Blast radius constraints: primary-lease cleanup evaluation only.
- Monitoring hooks: quarantine tombstones carry `cleanup_fact.code`.

## 7) Rollback Plan

- Trigger conditions: a lease with a non-released scratch step is accepted as released, or the focused service test regresses.
- Exact rollback commands: revert the commit touching `breadboard/rl/harness/service.py`, the focused test, and this ACR as one reviewed change.
- Artifact/state restoration steps: none; no persisted state format changes.
- Post-rollback verification: rerun `tests/rl/harness/test_v2_service.py` and the danger-zone ACR check.

## 8) Approvals

- Kernel reviewer: required on the exact candidate head.
- Contracts reviewer: required on the exact candidate head.
- Ops reviewer: not required.
- Final decision: pending exact-head review; this ACR does not authorize merge by itself.
