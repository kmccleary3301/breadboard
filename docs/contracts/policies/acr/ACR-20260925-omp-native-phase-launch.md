# ACR-20260925-omp-native-phase-launch

- `acr_id`: `ACR-20260925-omp-native-phase-launch`
- `title`: Explicit OMP native-phase launch branch and date/cwd reminder normalization
- `author`: BreadBoard E4 OMP lane
- `date`: 2026-09-25
- `status`: implemented; independent exact-head review pending

## 1) Problem Statement

During source-native tool worker execution for Oh My Pi (OMP) 18.1.17, `TrustedProcessHandle.invoke_native_phase` previously routed all non-Pi tool adapters to a fallback `else:` branch that populated Python-specific environment variables (`PYTHONHOME`, `PYTHONNOUSERSITE`, `LD_LIBRARY_PATH`) intended for OpenHands SDK. OMP native worker executes via Bun (`omp_native_tool_worker.ts`) and resolves pinned modules strictly via absolute paths rooted at `${pinnedSourceRoot}` without relying on ambient Python or Node resolution variables. Setting Python runtime paths in OMP native workers leaked host/runtime state. Furthermore, unrecognized native adapter IDs were not rejected fail-closed during launch environment construction.

Concurrently, OMP injected system prompts emit a date/cwd reminder header (`Today: <date>; current working directory: '<cwd>'`). In conformance comparisons between supplier traces and BreadBoard replays, differing workspace paths and date references prevented exact string matching unless deterministically and symmetrically normalized against declared runtime inputs.

## 2) Scope and Surfaces

- `breadboard.rl.harness.sandbox`: `TrustedProcessHandle.invoke_native_phase` explicit branching for `OMP_NATIVE_LOCAL_ADAPTER_ID` without invented environment variables, explicit `OPENHANDS_SDK_LOCAL_ADAPTER_ID`, and typed `SandboxLaunchError(code="runtime_unsupported")` for unrecognized adapters.
- `conformance.comparators.oh_my_pi_18_1_17`: typed symmetric normalization of `<system-reminder>` date and cwd text against validated `_RuntimeInputs` with tracking via `_RuleCounts` and emission in `report["normalizations"]`.
- Tests: `tests/rl/harness/test_sandbox_native_phase_admission.py` and `tests/e4_parity/test_omp_18_1_17_comparator.py`.
- Danger-zone: yes. Modifies `breadboard/rl/harness/sandbox.py`. No changes to lease containment envelopes, process credential boundaries, or mount verifications.

## 3) Coupling and Generalization Impact

OMP native worker (`omp_native_tool_worker.ts`) receives `pinnedSourceRoot` through the typed `initialize` RPC payload (`payload.route_classifier.sourceRoot`) and sets only `process.env.HOME` from verified `runtime_inputs.home`. Leaving `environment` identical to `plan.runtime.fixed_environment` preserves containment hygiene and prevents runtime poisoning. Unknown adapter IDs are rejected before process spawn. In the comparator, reminder normalization is strictly gated on exact match with declared `runtime_inputs.current_date` and `runtime_inputs.cwd`; any mismatched date remains un-normalized and triggers a divergence failure.

## 4) Change Classification

- Classification: `additive`.

Corrective, fail-closed branching in native phase admission and symmetric comparator normalization. No public plan schema modification or persistence format change.

## 5) Evidence and Validation Plan

Unit tests verify that:
1. `test_omp_native_phase_launch_environment_has_no_python_overrides`: OMP adapter launch environment contains no `PYTHONHOME`, `LD_LIBRARY_PATH`, or `PYTHONNOUSERSITE`.
2. `test_native_phase_launch_rejects_unknown_adapter_id`: unrecognized adapter ID raises `SandboxLaunchError` with code `runtime_unsupported`.
3. `test_reminder_normalizes_with_declared_date_on_both_sides`: reminder with declared date normalizes symmetrically on supplier and BB sides, recording normalization counts.
4. `test_reminder_with_different_date_than_runtime_input_does_not_normalize_and_diverges`: reminder with mismatched date fails normalization and causes episode comparison to fail.
5. Reverse-apply proof (`git apply -R SCR/w45.patch` fails all 4 tests; `git apply SCR/w45.patch` passes all 4 tests).
6. Danger-zone ACR check (`scripts/check_danger_zone_acr.py`), kernel contract pack check (`scripts/check_kernel_contract_pack_v1.py`), and script index check (`scripts/dev/build_script_index.py --check`).

## 6) Rollout Plan

Land through exact-head independent review in the OMP lane branch `e4/omp-complete-20260923`. Merge into lane replay pipeline for installed equality and trace replay verification on DO-2.

## 7) Rollback Plan

Revert the commit cleanly. OMP adapter launch would revert to the prior fallback and date reminder normalization would be removed from comparator output. No external state or database migration is required.

## 8) Approvals

BreadBoard E4 OMP lane author and independent exact-head reviewer required prior to merge. This document does not grant autonomous merge authority.
