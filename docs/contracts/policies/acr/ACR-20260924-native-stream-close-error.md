# ACR-20260924-native-stream-close-error

- `acr_id`: `ACR-20260924-native-stream-close-error`
- `title`: Preserve native-stream cleanup failures without a source finalizer
- `author`: BreadBoard E4 native-stream implementation
- `date`: 2026-09-24
- `status`: implemented (local scoped proof; independent exact-head review required)

## 1) Problem Statement

The seven native close tests in `test_runner_conductor.py` pass on remote main `0d7e80b` (237 tests passed) but fail on the OpenClaw lane at `0758b884`. Commit `7b81595c` added a second native-stream profile, while `_native_close_test_case` replaced the entire registry with a single test profile. The Conductor correctly rejects the changed measured module-identity list before the tests run. Preserving the other registered profiles in that fixture exposes a second regression: commit `d9f7e679` catches cleanup failure to report it through the optional supplier finalizer, but also suppresses the failure for profiles with no such finalizer and measures workspace effects after unverified retirement.

## 2) Scope and Surfaces

- Test fixture: `tests/rl/harness/test_runner_conductor.py` preserves every admitted profile while overriding only the profile exercised by the test.
- Product: `breadboard/rl/harness/runners/conductor.py` re-raises cleanup failure before effect measurement when there is no classification/finalization path to receive it. Existing source finalization still receives the failure after retirement.
- Kernel danger-zone: yes; the generic native-stream Conductor is protected.

## 3) Coupling and Generalization Impact

The fix uses only the existing `NativeStreamProfile.finalize_result_phase` capability and the presence of a classified result. It introduces no target identifiers, duplicate registries, or supplier behavior in the Conductor. Adapter module identity remains measured against the complete admitted registry. Without a finalization path, failed independent runtime retirement still prevents effect measurement. With one, the existing pinned finalizer receives the cleanup error and returns its source-owned error envelope.

## 4) Change Classification

- Classification: `internal` (restores the existing non-finalizer fail-closed contract and isolates the test override).
- Compatibility window: none; no protocol or schema changes.

## 5) Evidence and Validation Plan

- Remote main `0d7e80b`: `test_runner_conductor.py` 237 passed. Before fix at lane head `0758b884` and OpenClaw head `48070384`: the same seven tests fail with `conductor module artifact changed after bootstrap` (228 passed). With only the registry fixture fixed, six failures clear but the unverified-retirement test still fails to raise (234 passed, one failed). With cleanup propagation restored, `test_runner_conductor.py` passes all 235 tests.
- `test_openclaw_native_stream_conductor.py` passes all 16 tests using a lane-owned editable distribution shim; this includes the success and failed-cleanup finalization cases.
- Re-run the danger-zone ACR gate over `494dde75..HEAD`, the kernel contract pack, and script index check before handoff. Installed Linux replay and exact-head independent review remain separate gates owned by Main.

## 6) Rollout Plan

Main should obtain an independent review against the exact commit, then include both native-stream close paths in its admission gates. No automatic merge or promotion is authorized.

## 7) Rollback Plan

Revert this scoped change if it alters finalizer behavior; keep the failing-before evidence and repeat the two focused test files after a corrected fix.

## 8) Approvals

- Kernel reviewer: independent exact-head review required.
- Contracts reviewer: independent exact-head review required.
- Final decision: Main retains promotion authority.
