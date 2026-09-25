# ACR-20260924-headless-strict-json-tuples

- `acr_id`: `ACR-20260924-headless-strict-json-tuples`
- `title`: Restore strict JSON tuple loading for headless requests
- `author`: e4-w28-1, BreadBoard E4 campaign
- `date`: 2026-09-24
- `status`: implemented

## 1) Problem Statement

Commit `f78b3bd6` rejected the obsolete `outer_isolation` key with `@model_validator(mode="before")` on `HeadlessWorkspaceInput` and `HeadlessRunRequest`. With pydantic 2.13, a function-before or function-wrap model validator makes `model_validate_json(payload, strict=True)` re-validate the parsed payload in strict Python mode. Strict Python mode rejects a JSON array for every `tuple[...]` field (`tuple_type`). `load_headless_request`, and therefore the installed `python -m breadboard.rl.harness` headless path, rejected every real request. All six E4 profiles were affected.

The measurable outcome: a real request file with non-empty tuple fields loads through `load_headless_request`. The obsolete key must still raise `ObsoleteOuterIsolationError` on every path.

## 2) Scope and Surfaces

- Kernel modules touched:
  - `breadboard/rl/harness/headless.py`
- Tests: `tests/rl/harness/test_headless_runner.py`. Existing constructor, `model_validate` and `model_validate_json` rejection tests are in `tests/rl/harness/test_qualification_containment_receipt.py` and remain unchanged.
- Contract surfaces touched: the headless request loading boundary. The before model validators become a hidden `outer_isolation` field on both models. The field is typed `SkipJsonSchema[None]`, has `exclude=True` and `repr=False`, and a field-scoped `mode="before"` validator raises `ObsoleteOuterIsolationError` whenever the key is supplied. The field is absent from dumps, the JSON schema and identity dicts.
- Kernel danger-zone change? yes

## 3) Coupling and Generalization Impact

- Does this add any core -> extension dependency? no.
- Does this narrow cross-harness parity behavior? no. It restores request loading for every profile.
- Does this alter default endpoint semantics? no. Accepted requests and the typed rejection are the same as intended by `f78b3bd6`.
- Coupling risk score (`low`) and rationale: a field-scoped before validator runs only when its key is present, and it wraps only that field's schema. Probed on pydantic 2.13.5: model `before` and `wrap` validators both reject JSON arrays for tuples under strict JSON. The field-scoped form accepts them and still raises the typed error for JSON and dict input.

## 4) Change Classification

- Classification: `internal` (bug fix restoring strict JSON loading; the obsolete-key rejection contract is unchanged).
- Compatibility window: none needed. Requests without `outer_isolation` load as they did before `f78b3bd6`. Requests with it still raise `ObsoleteOuterIsolationError`.
- Required schema/version bumps: none. Headless identity dicts are byte-identical, because the hidden field is excluded from `model_dump`.

## 5) Evidence and Validation Plan

- Required contract lane tests: `test_load_headless_request_accepts_json_arrays_for_tuple_fields` writes a real request file with non-empty `tool_allowlist`, task labels and artifacts, `episode_overlays`, sandbox `mounts` and `egress_route_ids`, then loads it through `load_headless_request`. It failed at `b8866746` with six `tuple_type` errors. `test_headless_request_loading_rejects_obsolete_outer_isolation[request|workspace]` requires the typed error through `load_headless_request` and `model_validate` on a dict.
- Required replay/parity checks: none locally. Installed headless replays consume the fixed loader.
- Required conformance/ablation checks: a sweep of all 429 pydantic models in `breadboard/` and `breadboard_engine/` listed every function-before/wrap/plain validator. Each one was checked with a strict-JSON array probe. Every strict-loaded or strict-config model with such a validator already converts arrays to tuples, or wraps no tuple, set or bytes type. The only affected model, `breadboard.rl.phase5.score.ScoreEvaluation`, is never strict-loaded and is validated only from Python mappings in non-strict mode.
- Acceptance criteria: the RED test fails before the fix and passes after it; the headless, containment-receipt and conductor test files pass; this ACR gate, the kernel contract pack and the script index check pass; an independent exact-head review accepts.

## 6) Rollout Plan

- Rollout phases: merge; installed headless runs then load requests again.
- Flags/toggles: none.
- Blast radius constraints: only the obsolete-key rejection mechanism changes.
- Monitoring hooks: installed headless runs fail closed on request-load errors.

## 7) Rollback Plan

- Trigger conditions: any accepted request that previously failed validation for reasons other than `tuple_type`, or any `outer_isolation` input that is not rejected with `ObsoleteOuterIsolationError`.
- Exact rollback commands: revert the PR merge commit with `git revert -m 1 <pr-merge-commit>` in a new rollback branch. Do not reset protected main.
- Artifact/state restoration steps: none. No persisted artifact format changes.
- Post-rollback verification: rerun the headless and containment-receipt tests and this danger-zone ACR check on the rollback candidate.

## 8) Approvals

- Kernel reviewer: Main (campaign orchestrator) under Kyle McCleary's standing approval
- Contracts reviewer: independent exact-head review pending
- Ops reviewer: Main (campaign orchestrator) under Kyle McCleary's standing approval
- Final decision: Main (campaign orchestrator) under Kyle McCleary's standing approval
