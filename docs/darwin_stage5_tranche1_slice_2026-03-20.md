# DARWIN Stage-5 Tranche-1 Slice

Date: 2026-03-20
Status: initial Stage-5 tranche-1 implementation slice
References:
- `docs/darwin_stage5_doctrine_2026-03-20.md`
- `docs/darwin_stage5_execution_plan_2026-03-20.md`
- `docs/darwin_stage5_adr_set_2026-03-20.md`

## Purpose

This document defines the smallest useful Stage-5 tranche-1 implementation slice.

The slice is intentionally narrow. It does not attempt to make Stage 5 prove scalable compounding by itself. It hardens economics truth, freezes the compounding protocol, and establishes the first family-aware search skeleton needed before broader Stage-5 claims are allowed.

## Slice thesis

> canonicalize provider economics truth, make warm-start vs family-lockout a first-class comparison protocol, and add a bounded `SearchPolicyV2` skeleton on `lane.repo_swe` without changing runtime ownership.

## Included work

1. provider-origin, fallback-reason, and cache-aware cost truth hardening on Stage-5 claim-bearing surfaces
2. canonical `cold_start`, `warm_start`, and `family_lockout` comparison modes on Repo_SWE
3. first `CompoundingCaseV1` record emission for matched live comparisons
4. first `SearchPolicyV2` skeleton consuming current family state on Repo_SWE only
5. first `ComponentFamilyV2` lifecycle draft, limited to family-state semantics and no runtime-truth expansion

## Excluded work

- broad live campaign expansion
- systems family-aware search rollout
- broad transfer-matrix expansion
- family composition
- async or distributed work
- new lane introduction
- public or superiority packaging

## Authorized proving surfaces

- primary: `lane.repo_swe`
- watchdog/control: `lane.harness`
- systems canary only: `lane.systems`
- audit: `lane.atp`

## Hard gate for this slice

- claim-bearing live rows carry canonical provider-origin, fallback-reason, and cost-source truth
- `cold_start`, `warm_start`, and `family_lockout` can be expressed cleanly on Repo_SWE without widening runtime truth
- at least one `CompoundingCaseV1` path can be emitted from matched live comparisons
- `SearchPolicyV2` can consume current family state on Repo_SWE in a typed, inspectable way
- no BR4 runtime or replay regressions are introduced

## Code-touch ownership

### Economics truth

- `breadboard_ext/darwin/stage4.py`
- `scripts/run_darwin_stage4_live_economics_pilot_v0.py`
- `scripts/build_darwin_stage4_live_readiness_v0.py`
- `scripts/build_darwin_stage4_matched_budget_view_v1.py`
- `tests/test_darwin_stage4.py`
- `tests/test_run_darwin_stage4_live_economics_pilot_v0.py`
- `tests/test_build_darwin_stage4_live_readiness_v0.py`
- `tests/test_build_darwin_stage4_matched_budget_view_v1.py`

### Compounding protocol

- `breadboard_ext/darwin/stage4.py`
- new Stage-5-local helper surface, expected under `breadboard_ext/darwin/`
- `scripts/run_darwin_stage4_deep_live_search_v0.py`
- new Stage-5 tranche-1 builders or reports under `scripts/`
- new targeted tests under `tests/`

### Search policy skeleton

- `breadboard_ext/darwin/stage4.py`
- `scripts/run_darwin_stage4_deep_live_search_v0.py`
- `scripts/build_darwin_stage4_deep_live_reports_v0.py`
- new Stage-5-local search-policy report surfaces under `scripts/`
- `tests/test_darwin_stage4.py`
- `tests/test_run_darwin_stage4_deep_live_search_v0.py`

### Family lifecycle draft

- `breadboard_ext/darwin/stage4_family_program.py`
- `breadboard_ext/darwin/ledger.py`
- `scripts/build_darwin_stage4_family_registry_v0.py`
- `scripts/build_darwin_stage4_family_scorecard_v0.py`
- `scripts/build_darwin_stage4_family_memo_v0.py`
- new Stage-5-local family-state builders under `scripts/`
- `tests/test_build_darwin_stage4_family_program_v0.py`

## First bounded work packages

### WP-01 — Economics truth hardening

- canonicalize provider origin, fallback reason, and cache-aware cost fields
- segment OpenRouter vs direct OpenAI truth instead of blending economics implicitly
- keep Stage-4 live/scaffold truth intact

### WP-02 — Compounding protocol

- define typed comparison-mode values
- add Repo_SWE warm-start and family-lockout protocol hooks
- emit the first `CompoundingCaseV1` records on matched comparisons

### WP-03 — `SearchPolicyV2` skeleton

- add family priors to the current policy path
- add comparison-mode quotas
- keep campaign classes and abort thresholds small and typed
- keep scope to Repo_SWE only

### WP-04 — `ComponentFamilyV2` draft

- define lifecycle-state mapping from current Stage-4 family truth
- add retained/withheld/deprecated semantics where needed
- do not broaden family truth into a new ledger or ontology

### WP-05 — Tranche-1 readiness review

- confirm the slice can express warm-start vs lockout cleanly
- confirm economics truth is strong enough for Stage-5 claims
- decide whether Systems may enter the next Stage-5 slice as more than a canary

## Slice boundary rule

If this slice cannot produce a clean Repo_SWE warm-start vs family-lockout protocol with canonical provider economics truth, Stage 5 should not widen into family-aware search rollout yet.
