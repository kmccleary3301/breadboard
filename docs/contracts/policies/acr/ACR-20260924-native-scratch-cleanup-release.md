# ACR-20260924-native-scratch-cleanup-release

- `acr_id`: `ACR-20260924-native-scratch-cleanup-release`
- `title`: Admit the released native scratch step in primary cleanup evaluation
- `author`: BreadBoard E4 campaign
- `date`: 2026-09-24
- `status`: implemented

## 1) Problem Statement

`SandboxRuntime._close_lease` adds a `native_scratch` cleanup step to every lease that created per-lease native scratch (`_create_native_scratch` during the `initialize` native phase, since e311a07). `service._cleanup_released` admits only `runtime`, `workspace`, `cache_holder`, and `lease_record`, plus `child_verifier`, for primary leases. So a lease whose scratch was fully released still produced `resource_set <= allowed_resources == False`. The service then quarantined the episode with `cleanup_not_released` (service.py `_v2_failure("cleanup", "cleanup_not_released", ...)`). The OpenHands capture 1113 quarantine tombstone recorded exactly this, and every native-worker session (Pi, OMP, OpenHands, Hermes, OpenClaw) takes the same path. The closed-publication validator `evidence._validate_cleanup_projection` required the exact primary resource set, so it would also have rejected the same receipt at publication and recovery.

## 2) Scope and Surfaces

- Kernel modules touched: `breadboard/rl/harness/service.py` (`_cleanup_released`, primary-lease default only) and `breadboard/rl/harness/evidence.py` (`_validate_cleanup_projection`, which gains `_PRIMARY_OPTIONAL_CLEANUP_RESOURCES = ("native_scratch",)` for the primary required set only).
- Extension modules touched: none.
- Contract surfaces touched: episode cleanup disposition and closed-evidence cleanup validation. Closed envelopes for native-scratch leases can now carry a sixth released `native_scratch` step. `cleanup_required_resources` stays the exact primary tuple, and the receipt and envelope schemas are unchanged.
- Is this a **kernel danger-zone** change? `yes`.

## 3) Coupling and Generalization Impact
- Danger-zone: yes.
- Does this add any core -> extension dependency? `no`.
- Does this narrow cross-harness parity behavior? `no`.
- Does this alter default endpoint semantics? `no`.
- Coupling risk: `low`. `native_scratch` becomes an allowed optional resource for primary leases, in both `service._cleanup_released` and `evidence._validate_cleanup_projection` (closed publication, closed-envelope construction, recovery). The required resources are unchanged, every present step must still be `released` or `already_released`, duplicates are still rejected, and verifier leases (explicit required sets) stay strict. A `native_scratch` step in `failed` or `quarantined` state still quarantines.

## 4) Change Classification

- Classification: `internal` (bug fix to kernel cleanup evaluation; no public contract change).
- Compatibility window: none; receipts without `native_scratch` evaluate identically.
- Required schema/version bumps: none.

## 5) Evidence and Validation Plan

- Required contract lane tests:
  - `tests/rl/harness/test_v2_service.py::test_released_native_scratch_lease_publishes_closed` drives a full episode whose lease close returns the steps of a real `SandboxRuntimeManager._close_lease` receipt with released native scratch. The episode must reach CLOSED with closed publication and no quarantine. The real evidence validator must accept the receipt, and the verifier set must reject it. The test fails at `1a1668bf`.
  - `test_unreleased_native_scratch_never_claims_closed[FAILED|QUARANTINED]` requires quarantine with `cleanup_not_released` and no closed publication.
  - The test fixture repository mirrors the evidence rule through the same `_PRIMARY_OPTIONAL_CLEANUP_RESOURCES` constant.
  - `tests/rl/harness/test_v2_service.py` plus `tests/rl/harness/test_evidence.py`: 271 passed.
  - `scripts/rl_phase5/run_f5_target_faults.py` keeps its exact-set check; its fixed fault cases do not create native scratch.
- Required replay/parity checks: the installed native replays of the E4 profiles must end without `cleanup_not_released` quarantine.
- Required conformance/ablation checks: `scripts/check_danger_zone_acr.py` on the changed-file list.
- Required evidence bundles to refresh: none. Previously published closed envelopes had no `native_scratch` step, so they validate unchanged.
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
- Exact rollback commands: revert the commits touching `breadboard/rl/harness/service.py`, `breadboard/rl/harness/evidence.py`, the focused tests and fixture, and this ACR as one reviewed change.
- Artifact/state restoration steps: closed envelopes published with a `native_scratch` step would stop validating after rollback. Do not roll back after such envelopes exist unless they are migrated or re-quarantined.
- Post-rollback verification: rerun `tests/rl/harness/test_v2_service.py` and the danger-zone ACR check.

## 8) Approvals

- Kernel reviewer: required on the exact candidate head.
- Contracts reviewer: required on the exact candidate head.
- Ops reviewer: not required.
- Final decision: pending exact-head review; this ACR does not authorize merge by itself.
