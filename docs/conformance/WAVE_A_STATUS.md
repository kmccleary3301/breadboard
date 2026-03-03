# Wave A Status (Conformance Substrate)

Date: 2026-02-24

This ledger tracks the first implementation tranche for the quantified gap register.

## Implemented in this tranche

1. Repo-local conformance schema package staged at `docs/conformance/schemas/`.
2. Repo-local fixture corpus staged at `tests/fixtures/conformance_v1/fixtures/`.
3. Executable runner: `scripts/run_conformance_matrix.py`.
4. Runner tests: `tests/test_run_conformance_matrix.py`.
5. Replay determinism gate runner: `scripts/check_replay_determinism_gate.py`.
6. Replay gate tests: `tests/test_check_replay_determinism_gate.py`.
7. Bundle runner: `scripts/run_wave_a_conformance_bundle.sh`.
8. Bundle runner tests: `tests/test_run_wave_a_conformance_bundle.py`.
9. CT scenario manifest: `docs/conformance/ct_scenarios_v1.json`.
10. CT scenario runner: `scripts/run_ct_scenarios.py`.
11. CT scenario runner tests: `tests/test_run_ct_scenarios.py`.
12. CI workflow: `.github/workflows/conformance-wave-a-gate.yml`.
13. Matrix status sync runner: `scripts/sync_conformance_matrix_status.py`.
14. Matrix sync tests: `tests/test_sync_conformance_matrix_status.py`.
15. CT manifest hardening test: `tests/test_ct_scenarios_manifest_hardening.py`.
16. Pytest node gate wrapper: `scripts/run_pytest_node_gate.py`.
17. Pytest node gate tests: `tests/test_run_pytest_node_gate.py`.
18. Conformance artifact validator: `scripts/validate_conformance_artifacts.py`.
19. Artifact validator tests: `tests/test_validate_conformance_artifacts.py`.
20. Replay gate minimum evidence floor (`--min-files`) with required-files status outputs.
21. Replay gate schema added: `bb.replay_determinism_gate.v1`.
22. Projection semantic checker: `scripts/check_projection_semantics.py` (+ tests + fixture set).
23. Tool transition semantic checker: `scripts/check_tool_transition_semantics.py` (+ tests + fixture set).
24. Reliability lane policy checker: `scripts/check_reliability_lanes.py` (+ tests + fixture set).
25. Synced matrix freshness checker: `scripts/check_conformance_sync_freshness.py` (+ tests + CI step).
26. Approval semantic checker: `scripts/check_approval_semantics.py` (+ tests + fixture set).
27. Provider semantic checker: `scripts/check_provider_semantics.py` (+ tests + fixture set).
28. Semantic checker schema coverage added:
   1. `bb.reliability_lane_report.v1`
   2. `bb.approval_semantics_check.v1`
   3. `bb.provider_semantics_check.v1`
29. Family-level checker summary artifact:
   1. `scripts/summarize_conformance_checker_families.py`
   2. `conformance_family_summary_v1.json`
30. Replay gate quality sensitivity:
   1. `--max-drift-score` threshold support in replay determinism gate.
31. Projection overlay semantic checker:
   1. `scripts/check_projection_overlay_semantics.py`
   2. `tests/test_check_projection_overlay_semantics.py`
32. Rollback semantic checker:
   1. `scripts/check_rollback_semantics.py`
   2. `tests/test_check_rollback_semantics.py`
33. Plan envelope semantic checker:
   1. `scripts/check_plan_envelope_semantics.py`
   2. `tests/test_check_plan_envelope_semantics.py`
34. Additional semantic checker schema coverage:
   1. `bb.projection_overlay_semantics_check.v1`
   2. `bb.rollback_semantics_check.v1`
   3. `bb.plan_envelope_semantics_check.v1`
35. Reliability lane policy codification:
   1. `docs/conformance/reliability_lane_policy_v1.json`
   2. Bundle now uses policy-driven thresholds (`--policy-json`).
36. Cross-interface projection contract parity lane:
   1. `scripts/check_projection_contract_parity.mjs`
   2. Fixture set under `tests/fixtures/conformance_v1/projection_semantics/`
   3. Bundle artifact `projection_contract_parity_report_v1.json`.
37. Parity-profile guard audit lane:
   1. `scripts/audit_longrun_parity_disabled.py`
   2. Bundle artifact `longrun_parity_audit_v1.json`.
38. TUI transcript projection parity lane:
   1. `tui_skeleton/scripts/check_projection_contract_parity.ts`
   2. Fixture set under `tests/fixtures/conformance_v1/projection_semantics/`
   3. Bundle artifact `projection_contract_parity_tui_report_v1.json`.
39. Webapp projection contract parity lane:
   1. `bb_webapp/scripts/check_projection_contract_parity.ts`
   2. Fixture set under `tests/fixtures/conformance_v1/projection_semantics/`
   3. Bundle artifact `projection_contract_parity_webapp_report_v1.json`.
40. OpenTUI slab projection contract parity lane:
   1. `scripts/check_projection_contract_parity_opentui.mjs`
   2. Fixture set under `tests/fixtures/conformance_v1/projection_semantics/`
   3. Bundle artifact `projection_contract_parity_opentui_report_v1.json`.
41. Session semantic checker lane:
   1. `scripts/check_session_semantics.py`
   2. Fixture set under `tests/fixtures/conformance_v1/session_semantics/`
   3. CT rows `CT-SESSION-002` + `CT-SESSION-003` + `CT-SESSION-004` now run semantic resume/fork, pending-permission partition, and task-scope isolation validation (including conflict diagnostics + safe-reload invariants in `CT-SESSION-004`).
42. Provider semantic checker depth lane:
   1. `CT-PROVIDER-001` now runs `scripts/check_provider_semantics.py`
   2. Additional fixture `tests/fixtures/conformance_v1/provider_semantics/deterministic_tiebreak_ok_matrix.json`
   3. Provider family now has semantic checker coverage on multiple rows (`CT-PROVIDER-001`, `CT-PROVIDER-002`, `CT-PROVIDER-003`).
43. VSCode transcript-reducer projection parity lane:
   1. `vscode_sidebar/scripts/check_projection_reducer_parity.mjs`
   2. Bundle artifact `projection_reducer_parity_vscode_report_v1.json`
   3. Wave-A validator requires report presence and `ok=true`.
44. Approval semantic depth lane:
   1. `CT-APPROVAL-001` now runs `scripts/check_approval_semantics.py --mode expired_request_closed`
   2. Added fixtures:
      - `tests/fixtures/conformance_v1/approval_semantics/expired_request_closed_ok.json`
      - `tests/fixtures/conformance_v1/approval_semantics/expired_request_closed_bad.json`
45. Provider semantic depth lane (auth precedence):
   1. `CT-PROVIDER-002` now runs `scripts/check_provider_semantics.py --mode auth_precedence`
   2. Added fixtures:
      - `tests/fixtures/conformance_v1/provider_semantics/auth_precedence_ok.json`
      - `tests/fixtures/conformance_v1/provider_semantics/auth_precedence_bad.json`
46. Session semantic depth lane (pending permission partition):
   1. `CT-SESSION-003` now runs `scripts/check_session_semantics.py --mode pending_permission_partition`
   2. Added fixtures:
      - `tests/fixtures/conformance_v1/session_semantics/pending_permission_partition_ok.json`
      - `tests/fixtures/conformance_v1/session_semantics/pending_permission_partition_bad.json`
47. Session semantic depth lane (task scope isolation):
   1. `CT-SESSION-004` now runs `scripts/check_session_semantics.py --mode task_scope_isolation`
   2. Added fixtures:
      - `tests/fixtures/conformance_v1/session_semantics/task_scope_isolation_ok.json`
      - `tests/fixtures/conformance_v1/session_semantics/task_scope_isolation_bad.json`
   3. Checker now enforces conflict diagnostic and safe-reload policy coherence:
      - unique conflict IDs + unresolved conflict extraction
      - unresolved conflicts must block reload
      - blocking conflict IDs must match unresolved conflict IDs
      - deterministic expected conflict/reload fields are asserted
48. Assertion strictness hardening lane:
   1. `CT-SESSION-002/003/004` now assert deterministic fixture cardinalities (`prefix_length`, turn counts, pending counts, bucket counts, conflict counts, unresolved/blocking counts, and reload-block flags).
   2. `CT-PROVIDER-001/002/003` now assert exact fixture cardinalities (`candidate_count`, `available_count`) rather than lower bounds.
   3. `CT-APPROVAL-002` now asserts exact `decision_count`.
49. Replay gate evidence-floor hardening lane:
   1. Added second fixture-backed replay determinism report:
      - `tests/fixtures/conformance_v1/replay_determinism/ok/replay_determinism_report_ok_002.json`
   2. Wave A bundle now requires at least two replay determinism reports (`--min-files 2`).
50. Extension semantic depth lane:
   1. Added `scripts/check_extension_semantics.py` with explicit modes:
      - `phase_priority_order`
      - `dependency_order`
   2. Added fixtures under `tests/fixtures/conformance_v1/extension_semantics/` (+ pass/fail pairs).
   3. CT rows `CT-EXT-002` and `CT-EXT-003` now run explicit semantic checker assertions.
51. Session placeholder semantic lane:
   1. Added `scripts/check_replay_placeholder_semantics.py` for deterministic todo placeholder resolution checks.
   2. Added fixtures:
      - `tests/fixtures/conformance_v1/session_semantics/placeholder_resolution_ok.json`
      - `tests/fixtures/conformance_v1/session_semantics/placeholder_resolution_bad.json`
   3. CT row `CT-SESSION-001` now uses explicit semantic checker assertions (no pytest-node wrapper).
52. Surface-manifest semantic lane:
   1. Added `scripts/check_surface_manifest_semantics.py` for deterministic MCP replayable propagation checks.
   2. Added fixtures:
      - `tests/fixtures/conformance_v1/extension_semantics/mcp_replayable_propagates_ok.json`
      - `tests/fixtures/conformance_v1/extension_semantics/mcp_replayable_propagates_bad.json`
   3. CT row `CT-EXT-004` now uses explicit semantic checker assertions.
53. Provider replay-runtime semantic lane:
   1. Added mode `replay_runtime_delegate` in `scripts/check_provider_semantics.py`.
   2. Added fixtures:
      - `tests/fixtures/conformance_v1/provider_semantics/replay_runtime_delegate_ok.json`
      - `tests/fixtures/conformance_v1/provider_semantics/replay_runtime_delegate_bad.json`
   3. CT row `CT-PROVIDER-004` now uses explicit semantic checker assertions.
54. Approval policy semantic lane:
   1. Added modes in `scripts/check_approval_semantics.py`:
      - `webfetch_default_deny`
      - `network_allowlist_enforced`
   2. Added fixtures under `tests/fixtures/conformance_v1/approval_semantics/` for pass/fail coverage.
   3. CT rows `CT-APPROVAL-003` + `CT-APPROVAL-004` now use explicit semantic checker assertions.
55. Recursion semantic lane:
   1. Added `scripts/check_longrun_semantics.py` with explicit modes:
      - `episode_max_steps_enabled_override`
      - `episode_max_steps_disabled_default`
      - `policy_profile_env_override`
      - `single_rollback_then_stop`
   2. Added fixtures under `tests/fixtures/conformance_v1/recursion_semantics/`.
   3. CT rows `CT-RECURSE-001/002/003/004` now use explicit semantic checker assertions.
56. Extension plugin-discovery semantic lane:
   1. Added `scripts/check_plugin_discovery_semantics.py`.
   2. Added fixtures:
      - `tests/fixtures/conformance_v1/extension_semantics/plugin_discovery_ok.json`
      - `tests/fixtures/conformance_v1/extension_semantics/plugin_discovery_bad.json`
   3. CT row `CT-EXT-001` now uses explicit semantic checker assertions.
57. Protocol semantic depth lane:
   1. Added `scripts/check_protocol_semantics.py` with explicit modes:
      - `contract_exports`
      - `envelope_variants`
      - `minimal_payload_samples`
      - `runtime_event_translation`
   2. Added fixtures under `tests/fixtures/conformance_v1/protocol_semantics/`.
   3. CT rows `CT-PROTO-001/002/003/004` now use explicit semantic checker assertions.
58. Tool policy/runtime semantic depth lane:
   1. Added `scripts/check_tool_policy_semantics.py` with explicit modes:
      - `one_bash_first_allowed`
      - `disabled_edit_mask`
      - `shell_deny_mask`
      - `runtime_invalid_transition`
   2. Added fixtures under `tests/fixtures/conformance_v1/tool/`.
   3. CT rows `CT-TOOL-002/003/004/005` now use explicit semantic checker assertions.
59. Reliability semantic depth lane:
   1. Added `scripts/check_conformance_matrix_semantics.py`.
   2. Added `scripts/check_sync_mapping_semantics.py`.
   3. Added fixtures under `tests/fixtures/conformance_v1/reliability_lanes/`.
   4. CT rows `CT-REL-003/004` now use explicit semantic checker assertions.
60. Artifact validator consistency hardening:
   1. `scripts/validate_conformance_artifacts.py` now verifies:
      - sync summary counters are internally consistent
      - sync summary mapped rows match CT row count
      - synced CSV status distribution matches sync summary counters
      - manifest-declared node-gate artifact outputs are present
   2. Added explicit negative-path tests in `tests/test_validate_conformance_artifacts.py`.
61. Bundle reproducibility hardening:
   1. `scripts/run_wave_a_conformance_bundle.sh` now deletes stale `node_gate/*.json` and `junit/*.xml` before each run.
   2. Fresh bundle output is now deterministic relative to current CT manifest execution.
62. Reliability command pinning hardening:
   1. `tests/test_ct_scenarios_manifest_hardening.py` now enforces exact command contracts for `CT-REL-001/002/003/004`.
   2. Replay gate policy alignment is explicitly pinned for `CT-REL-002` (`--min-files 2`).
63. Bundle stale-artifact cleanup verification:
   1. `tests/test_run_wave_a_conformance_bundle.py` now verifies stale `node_gate` and `junit` artifacts are deleted before bundle execution.
64. CT row status coherence hardening:
   1. `scripts/validate_conformance_artifacts.py` now fails when `ct_scenarios_rows_v1.json` contains any non-pass rows in bundle validation.
   2. Added explicit negative-path coverage in `tests/test_validate_conformance_artifacts.py`.
65. Family summary integrity hardening:
   1. `scripts/validate_conformance_artifacts.py` now verifies `conformance_family_summary_v1.json` family counters are non-negative, internally consistent, and total to CT row count.
   2. `scripts/summarize_conformance_checker_families.py` semantic token detection now covers the expanded checker set.
   3. Added explicit negative-path coverage for family-summary total mismatch in `tests/test_validate_conformance_artifacts.py`.
66. Synced CSV per-row status coherence hardening:
   1. `scripts/validate_conformance_artifacts.py` now verifies exact per-`test_id` status agreement between `ct_scenarios_rows_v1.json` and `CONFORMANCE_TEST_MATRIX_V1.synced.csv`.
   2. Added explicit negative-path coverage in `tests/test_validate_conformance_artifacts.py`.
67. Runtime tool transition enforcement depth increased:
   1. `breadboard/tool_transition_runtime.py` now emits deterministic transition telemetry (`events`, `event_count`) and machine-readable reject codes (`invalid_transition_edge`, `unknown_target_state`, `terminal_state_transition`, etc.).
   2. `agentic_coder_prototype/agent_session.py` now propagates `transition_trace` for tool results and transition failures.
   3. `CT-TOOL-005` now validates a legal/illegal edge matrix fixture via `check_tool_policy_semantics.py --mode runtime_transition_matrix`.
68. Session conflict + safe-reload runtime lane added:
   1. `breadboard/session_conflict_runtime.py` implements runtime conflict taxonomy, conflict-id uniqueness, and safe-reload decision semantics.
   2. `SessionState` now exposes runtime conflict APIs (`record_session_conflict`, `resolve_session_conflict`, `evaluate_safe_reload`, `session_conflict_snapshot`) and emits lifecycle events for conflict/reload state changes.
   3. Added coverage: `tests/test_session_conflict_runtime.py` and extended `tests/test_session_state_events.py`.
69. Provider retry-after runtime coverage deepened:
   1. Added Anthropic runtime tests for numeric and ISO retry-after parsing path in `_compute_rate_limit_retry_delay`.
   2. This complements checker-level retry-after normalization semantics (`CT-PROVIDER-003`).
70. Replay determinism gate evidence quality hardening:
   1. `check_replay_determinism_gate.py` now enforces a minimum non-fixture report floor (`--min-non-fixture-files`).
   2. Wave A bundle requires at least one non-fixture replay report in addition to fixture reports.
   3. Added pass/fail coverage for non-fixture floor in `tests/test_check_replay_determinism_gate.py`.
71. Wave A CI trigger/test-lane blind-spot closure:
   1. `conformance-wave-a-gate.yml` now watches runtime conflict/session files:
      - `breadboard/session_conflict_runtime.py`
      - `agentic_coder_prototype/state/session_state.py`
      - `tests/test_session_conflict_runtime.py`
      - `tests/test_session_state_events.py`
   2. Wave A CI unit test list now executes:
      - `tests/test_session_conflict_runtime.py`
      - `tests/test_session_state_events.py`
72. Workflow drift guard added:
   1. New guard test `tests/test_conformance_wave_a_workflow_guard.py` enforces that required runtime paths remain in both `pull_request` and `push` filters.
   2. Guard test also enforces runtime conflict/session tests remain in the Wave A CI pytest command.
   3. `conformance-wave-a-gate.yml` now includes `tests/test_conformance_wave_a_workflow_guard.py` in both path filters and pytest execution list.

## Verified

1. `python scripts/run_conformance_matrix.py` passes against current schema+fixture assets.
2. `pytest -q tests/test_run_conformance_matrix.py` passes.
3. `pytest -q tests/test_check_replay_determinism_gate.py` passes.
4. `pytest -q tests/test_run_wave_a_conformance_bundle.py` passes.
5. `pytest -q tests/test_run_ct_scenarios.py` passes.
6. `python scripts/run_ct_scenarios.py --json-out artifacts/conformance/ct_scenarios_result_v1.json --rows-out artifacts/conformance/ct_scenarios_rows_v1.json` passes.
7. `python scripts/run_ct_scenarios.py --fail-on-unimplemented-all --json-out artifacts/conformance/ct_scenarios_result_v1.json --rows-out artifacts/conformance/ct_scenarios_rows_v1.json` passes.
8. Current `ct_scenarios_v1` manifest has 39/39 implemented scenarios (full matrix coverage, no placeholders).
9. `scripts/run_wave_a_conformance_bundle.sh artifacts/conformance` passes and emits synced matrix artifacts.
10. Matrix sync summary currently reports: `mapped=39/39`, `passing=39`, `failing=0`, `planned=0`.
11. Full-row mapping is exact `test_id` matching only (family fallback projection removed).
12. CT rows are now pinned to row-level pytest node targets (or explicit reliability scripts) rather than family-shared file-level commands.
13. CI now validates CT manifest coverage and row-level target specificity.
14. Current CT targeting split: 0 pytest node-level rows + 39 semantic/reliability script rows.
15. Non-reliability CT rows now emit per-row structured artifacts under `artifacts/conformance/node_gate/`.
16. Bundle now validates required conformance artifacts with schema-aware checks.
17. Replay gate now fails when report volume is below configured floor (`--min-files`, currently `2` in Wave A bundle).
18. `pytest -q tests/test_check_projection_semantics.py` passes.
19. `pytest -q tests/test_check_tool_transition_semantics.py` passes.
20. CT manifest now includes semantic checker rows for projection (`CT-PROJ-001/002/003/004`) and tool lifecycle (`CT-TOOL-001`, `CT-TOOL-005`).
21. `pytest -q tests/test_check_reliability_lanes.py` passes.
22. Wave A bundle now emits and validates `reliability_lane_report_v1.json` with hard policy checks.
23. Checked-in synced matrix freshness is now enforced against generated bundle output.
24. `CT-APPROVAL-002` now uses semantic approval coherence checks (not only node-level pytest).
25. `CT-PROVIDER-003` now uses semantic provider tie-break checks (not only node-level pytest).
26. `CT-SESSION-005` now enforces dropped-range rewind semantics via explicit checker.
27. `CT-PLAN-001` now enforces deterministic decision-envelope lifecycle semantics.
28. Wave A bundle now emits and validates cross-interface projection parity report (`projection_contract_parity_report_v1.json`).
29. Full Wave A CI gate-equivalent unit suite passes locally:
    - `177 passed` across the exact `conformance-wave-a-gate.yml` pytest list.
30. Full Wave A bundle execution passes locally end-to-end:
    - schema fixture gate pass
    - replay determinism gate pass (`min-files` + `min-non-fixture-files`)
    - CT scenarios pass (`39/39`)
    - reliability lane policy pass
    - cross-surface projection parity bundle pass
    - synced matrix freshness + artifact validation pass.
31. WS-2/WS-3/WS-7/WS-8 closure evidence is now present in one reproducible lane:
    - projection substrate parity (SDK/TUI/webapp/OpenTUI/VSCode)
    - lifecycle/audit semantic checker family coverage
    - CI policy promotion via Wave A workflow + kernel boundary workflow
    - integrated closure report artifacts under `artifacts/conformance/`.
29. Wave A bundle now emits and validates parity-profile guard report (`longrun_parity_audit_v1.json`).
30. Wave A bundle now emits and validates TUI projection parity report (`projection_contract_parity_tui_report_v1.json`).
31. Wave A bundle now emits and validates webapp projection parity report (`projection_contract_parity_webapp_report_v1.json`).
32. Wave A bundle now emits and validates OpenTUI slab projection parity report (`projection_contract_parity_opentui_report_v1.json`).
33. Full Wave-A unit test gate passes locally (`131 passed` across gate-targeted suites).
34. Synced matrix freshness gate currently passes against generated artifacts.
35. Latest bundle run summary: `schemas=38`, `valid_fixtures=39`, `invalid_fixtures=39`, `passed=116`, `failed=0`.
36. Custom artifact-dir Wave-A bundle run now passes end-to-end, including `node_gate/*.json` and `junit/*.xml` under the selected output root.
37. Replay determinism gate now consumes multiple report globs in a single run (fixture baseline + optional local replay outputs), reducing single-source blind spots.
38. Session family semantic depth increased: `CT-SESSION-002` + `CT-SESSION-003` + `CT-SESSION-004` now enforce explicit resume/fork prefix, pending-permission partition, and task-scope isolation invariants via checker artifacts.
39. Provider family semantic depth increased: `CT-PROVIDER-001` now enforces explicit deterministic tie-break semantics via checker artifact.
40. Projection parity lane now includes VSCode transcript-reducer semantics (`projection_reducer_parity_vscode_report_v1.json`).
41. Approval family semantic depth increased: `CT-APPROVAL-001` + `CT-APPROVAL-002` are checker-backed semantic rows.
42. Provider family semantic depth increased further: `CT-PROVIDER-001/002/003` are checker-backed semantic rows.
43. Semantic checker rows now use deterministic fixture cardinality assertions for session/provider/approval families (reduced tolerance to silent drift).
44. Replay gate now enforces a two-report minimum evidence floor in Wave A bundle runs.
45. Extension family semantic depth increased: `CT-EXT-002` and `CT-EXT-003` are now checker-backed semantic rows.
46. Extension semantic checker lane passes with fixture-backed deterministic ordering assertions (`phase_priority_order`, `dependency_order`).
47. Session placeholder row (`CT-SESSION-001`) is now checker-backed with deterministic snapshot resolution assertions.
48. Extension determinism row (`CT-EXT-004`) is now checker-backed with deterministic MCP propagation assertions.
49. Provider family semantic depth increased further: all provider rows (`CT-PROVIDER-001/002/003/004`) are checker-backed semantic rows.
50. Approval policy rows are now checker-backed semantic rows:
   1. `CT-APPROVAL-003` uses `webfetch_default_deny`.
   2. `CT-APPROVAL-004` uses `network_allowlist_enforced`.
51. Recursion rows are now checker-backed semantic rows:
   1. `CT-RECURSE-001` uses `episode_max_steps_enabled_override`.
   2. `CT-RECURSE-002` uses `episode_max_steps_disabled_default`.
   3. `CT-RECURSE-003` uses `policy_profile_env_override`.
   4. `CT-RECURSE-004` uses `single_rollback_then_stop`.
52. Extension rows are now checker-backed semantic rows:
   1. `CT-EXT-001` uses `check_plugin_discovery_semantics.py`.
   2. `CT-EXT-002/003` use `check_extension_semantics.py`.
   3. `CT-EXT-004` uses `check_surface_manifest_semantics.py`.
53. Protocol rows are now checker-backed semantic rows:
   1. `CT-PROTO-001` uses `check_protocol_semantics.py --mode contract_exports`.
   2. `CT-PROTO-002` uses `check_protocol_semantics.py --mode envelope_variants`.
   3. `CT-PROTO-003` uses `check_protocol_semantics.py --mode minimal_payload_samples`.
   4. `CT-PROTO-004` uses `check_protocol_semantics.py --mode runtime_event_translation`.
54. Tool rows are now checker-backed semantic rows:
   1. `CT-TOOL-001` uses `check_tool_transition_semantics.py`.
   2. `CT-TOOL-002` uses `check_tool_policy_semantics.py --mode one_bash_first_allowed`.
   3. `CT-TOOL-003` uses `check_tool_policy_semantics.py --mode disabled_edit_mask`.
   4. `CT-TOOL-004` uses `check_tool_policy_semantics.py --mode shell_deny_mask`.
   5. `CT-TOOL-005` uses `check_tool_policy_semantics.py --mode runtime_transition_matrix` (full legal/illegal edge matrix, deterministic runtime rejection codes, and evented transition telemetry through runtime snapshot artifacts).
55. All CT rows are now checker/script-driven (`non_rel_nodeids == 0` and `all_node_gate_rows == 0` enforced by manifest hardening tests; no `run_pytest_node_gate` rows remain in `ct_scenarios_v1.json`).
56. Reliability rows are now checker-backed semantic rows:
   1. `CT-REL-001` uses `run_conformance_matrix.py`.
   2. `CT-REL-002` uses `check_replay_determinism_gate.py`.
   3. `CT-REL-003` uses `check_conformance_matrix_semantics.py`.
   4. `CT-REL-004` uses `check_sync_mapping_semantics.py`.
57. `CT-REL-002` command contract is now pinned to Wave-A bundle replay policy inputs:
   1. Three explicit report globs (fixture baseline + checked-in non-fixture baselines + local conformance_qc reports).
   2. `--min-files 4`.
   3. `--min-non-fixture-files 2`.
   4. `--min-non-fixture-turns 10`.
   5. `--max-drift-score 0.0`.
58. Full Wave-A rerun on this checkpoint passes end-to-end:
   1. Full gate unit set: `131 passed`.
   2. Bundle summary: `ct-scenarios status=pass scenarios=39 failures=0`.
   3. Matrix sync summary: `mapped=39/39`, `passing=39`, `failing=0`, `planned=0`.
   4. Artifact validator + sync freshness checks both pass.
59. Projection reconnect/gap/coalescing semantics are now checker-backed:
   1. `check_projection_semantics.py --mode reconnect_gap_coalescing`.
   2. `CT-PROJ-001` now asserts resumed stream count and zero semantic violations on reconnect/coalescing/gap fixture.
60. Projection parity now has a single aggregate hard gate artifact:
   1. `check_projection_surface_parity_bundle.py`.
   2. Bundle emits `projection_surface_parity_bundle_v1.json` and validator fails on `all_ok=false`.
61. Approval policy lane now includes lifecycle/policy audit-stream semantics:
   1. `CT-APPROVAL-004` asserts audit event count and zero audit-violation invariants.
   2. `check_approval_semantics.py --mode network_allowlist_enforced` validates typed audit event fields, required event types, and monotonic audit timestamps.
62. Provider lane now includes retry-after normalization semantics:
   1. `CT-PROVIDER-003` now runs `check_provider_semantics.py --mode retry_after_normalization`.
   2. Checker validates normalized retry-after values and required bucket-name coherence across provider header variants.
63. Session lane now includes conflict diagnostics + safe-reload semantics on checker-backed rows:
   1. `check_session_semantics.py --mode task_scope_isolation` validates conflict ID uniqueness, unresolved-vs-blocking coherence, and reload gating behavior.
   2. `CT-SESSION-004` now asserts conflict/reload deterministic fields (`conflict_count`, `unresolved_conflict_count`, `blocking_conflict_count`, `safe_reload_allowed`, `safe_reload_blocked`).
64. Workflow guard + runtime lane focused suite passes:
   1. `pytest -q tests/test_conformance_wave_a_workflow_guard.py tests/test_session_conflict_runtime.py tests/test_session_state_events.py tests/test_tool_transition_runtime.py tests/test_check_tool_policy_semantics.py`
   2. Result: `33 passed`.
65. Full Wave-A CI-equivalent gate now includes runtime conflict/session tests and passes:
   1. Full gate run result: `154 passed`.
66. Full Wave-A bundle remains green after CI trigger/test-lane hardening:
   1. `ct-scenarios status=pass scenarios=39 failures=0`.
   2. `sync-conformance-matrix mapped=39/39 passing=39 failing=0 planned=0`.
   3. `validate-conformance-artifacts pass`.
67. Replay evidence hardening lane:
   1. Added checked-in non-fixture replay evidence baselines under `docs/conformance/replay_evidence/`.
   2. Replay gate now enforces:
      - `--min-files 4`
      - `--min-non-fixture-files 2`
      - `--min-non-fixture-turns 10`
   3. Added gate coverage for non-fixture turn-depth enforcement in `tests/test_check_replay_determinism_gate.py`.
68. Replay lane semantic-depth assertions increased on `CT-REL-002`:
   1. Assertions now include `required_non_fixture_files_met`, `min_non_fixture_files_required`, `non_fixture_files_checked`, and `min_non_fixture_turns`.
   2. Reliability command pinning tests now enforce the enriched replay policy contract and bundle alignment.
69. Added replay evidence baseline guard:
   1. `tests/test_replay_evidence_baselines.py` verifies checked-in non-fixture replay evidence exists, remains deterministic, and stays non-trivial (`turn_count >= 10`).
70. Full Wave-A rerun after replay-evidence hardening passes end-to-end:
   1. Full gate unit set: `161 passed`.
   2. Bundle summary: `replay-determinism-gate pass (4 files)`, `ct-scenarios status=pass scenarios=39 failures=0`.
   3. Matrix sync summary: `mapped=39/39`, `passing=39`, `failing=0`, `planned=0`.
   4. Artifact validator + sync freshness checks pass.
71. Replay evidence promotion automation lane added:
   1. Added `scripts/promote_replay_evidence_baselines.py` for strict promotion of live replay reports into deterministic baseline files.
   2. Promotion requires deterministic quality invariants (`schema_version`, `ok`, zero mismatch/missing/extra/drift, and configurable minimum turn depth).
   3. Added coverage in `tests/test_promote_replay_evidence_baselines.py` (insufficient-candidate fail path, deterministic write path, and min-turn-depth filtering).
72. Wave-A CI workflow now includes replay evidence promotion hardening:
   1. Watches `scripts/promote_replay_evidence_baselines.py` and `tests/test_promote_replay_evidence_baselines.py`.
   2. Executes `tests/test_promote_replay_evidence_baselines.py` in the gate test list.
73. Workflow guard depth increased:
   1. `tests/test_conformance_wave_a_workflow_guard.py` now asserts replay evidence promotion tests remain wired in both workflow path filters and execution list.
74. Session semantic depth increased on checker-backed rows:
   1. `check_session_semantics.py --mode resume_fork_prefix` now enforces:
      - no duplicate IDs in base/resume/fork turn lists
      - no overlap between resume/fork tails after shared prefix
      - richer lineage counters (`resume_tail_count`, `fork_tail_count`, duplicate counters).
   2. `check_session_semantics.py --mode pending_permission_partition` now enforces:
      - duplicate pending ID detection
      - cross-task bucket overlap detection
      - richer counters (`unique_pending_count`, `duplicate_id_count`, `cross_task_overlap_count`).
   3. `check_session_semantics.py --mode task_scope_isolation` now emits:
      - `resolved_conflict_count`
      - `safe_reload_attempted`.
75. CT row assertion depth increased for session family:
   1. `CT-SESSION-002` now asserts tail-count/overlap and duplicate counters.
   2. `CT-SESSION-003` now asserts unique/duplicate/cross-task overlap counters.
   3. `CT-SESSION-004` now asserts `resolved_conflict_count` and `safe_reload_attempted`.
76. Projection parity expansion prep landed without altering current Wave-A contract:
   1. `check_projection_surface_parity_bundle.py` now supports optional `--surface-manifest`.
   2. Added baseline manifest: `docs/conformance/projection_surface_manifest_v1.json`.
   3. Added prep playbook: `docs/conformance/PROJECTION_SURFACE_EXPANSION_PREP_V1.md`.
   4. Added coverage for custom-manifest pass/fail paths in `tests/test_check_projection_surface_parity_bundle.py`.
77. Full Wave-A rerun after session-depth + projection-prep changes passes end-to-end:
   1. Full gate unit set: `163 passed`.
   2. Bundle summary: `ct-scenarios status=pass scenarios=39 failures=0`, `replay-determinism-gate pass (4 files)`.
   3. Matrix sync summary: `mapped=39/39`, `passing=39`, `failing=0`, `planned=0`.
   4. Artifact validator + sync freshness checks pass.
78. Projection row assertion depth increased:
   1. `CT-PROJ-001` now asserts deterministic reconnect/coalescing counters:
      - `initial_count=2`
      - `resumed_count=3`
      - `batch_count=2`
      - `violation_count=0`
   2. `CT-PROJ-002` now asserts `violations=[]` in addition to `ok=true`.
   3. `CT-PROJ-003` now asserts `event_count=5`, `revision_count=2`, `active_revision=2`, and `violations=[]`.
   4. `CT-PROJ-004` now asserts exact heartbeat semantics:
      - `recovered_within_ms=180`
      - `max_allowed_ms=500`
      - `violations=[]`.
79. Session semantic fixture diversity increased for new invariant paths:
   1. Added `resume_fork_prefix_overlap_bad.json` to isolate tail-overlap violation behavior.
   2. Added `pending_permission_partition_overlap_bad.json` to isolate cross-task overlap + duplicate pending-ID behavior.
   3. Added targeted test coverage in `tests/test_check_session_semantics.py` for both new failure modes.
80. Full Wave-A rerun after projection assertion tightening + session fixture expansion remains green:
   1. Full gate unit set: `165 passed`.
   2. Bundle summary: `ct-scenarios status=pass scenarios=39 failures=0`, `replay-determinism-gate pass (4 files)`.
   3. Matrix sync summary: `mapped=39/39`, `passing=39`, `failing=0`, `planned=0`.
81. Strict rerun against the current CI-equivalent Wave-A lane remains green:
   1. `scripts/run_wave_a_conformance_bundle.sh artifacts/conformance` passes.
   2. Targeted CI-equivalent pytest lane passes: `55 passed`.
   3. Projection aggregate parity remains pass for all currently declared surfaces:
      - SDK
      - OpenTUI
      - webapp
      - TUI
      - VSCode reducer.
82. Current Wave-A P0 contract is now considered fully closed:
   1. All `CT-*` rows are implemented and passing (`39/39`).
   2. Replay gate evidence floor and non-fixture floor are both enforced and passing.
   3. Cross-surface projection parity bundle gate is enforced and passing.
   4. Remaining work is now expansion/polish scope (new surfaces and future artifacts), not a Wave-A P0 blocker.
83. Provider retry-after normalization hardening expanded (non-blocking depth increase targeting `G-022`):
   1. `parse_rate_limit_headers` now supports:
      - `retry-after-ms` / `x-ratelimit-retry-after-ms`
      - non-`x-` `ratelimit-*` request bucket headers
      - robust RFC-date retry-after fallback parsing.
   2. Added fixture depth in:
      - `tests/fixtures/conformance_v1/provider_semantics/retry_after_normalization_ok.json`
      - `tests/fixtures/conformance_v1/provider_semantics/retry_after_normalization_bad.json`
   3. Added parser unit coverage:
      - `tests/test_parse_rate_limit_headers.py`
   4. Updated checker expectation coverage:
      - `tests/test_check_provider_semantics.py` (`retry_after_normalization` case count).
   5. Direct semantic checker evidence refreshed:
      - `artifacts/conformance/node_gate/ct_provider_003_refresh.json` (`ok=true`, `case_count=5`).
84. Full post-hardening conformance rerun remains green:
   1. `scripts/run_wave_a_conformance_bundle.sh artifacts/conformance` passes.
   2. Bundle summary remains:
      - `ct-scenarios status=pass scenarios=39 failures=0`
      - `replay-determinism-gate pass (4 files)`
      - `projection-surface-parity-bundle pass surfaces=5 cases=18`
      - `mapped=39/39 passing=39 failing=0 planned=0`
   3. Expanded CI-equivalent lane (including provider retry-after parser tests) passes:
      - `67 passed`.
85. Extension lifecycle/policy audit stream lane added (`G-019` depth increase, non-blocking expansion):
   1. New semantic checker:
      - `scripts/check_extension_audit_stream_semantics.py`
   2. New fixture pair:
      - `tests/fixtures/conformance_v1/extension_semantics/lifecycle_audit_stream_ok.json`
      - `tests/fixtures/conformance_v1/extension_semantics/lifecycle_audit_stream_bad.json`
   3. New CT row:
      - `CT-EXT-005` in `docs/conformance/ct_scenarios_v1.json`
      - artifact target: `artifacts/conformance/node_gate/ct_ext_005.json`
   4. New unit coverage:
      - `tests/test_check_extension_audit_stream_semantics.py`
86. Provider retry-after normalization now enforces bounded cap semantics (`G-022` depth increase):
   1. `parse_rate_limit_headers` now applies hard cap (`MAX_RETRY_AFTER_MS=3_600_000`).
   2. Added explicit cap fixture case in:
      - `tests/fixtures/conformance_v1/provider_semantics/retry_after_normalization_ok.json`
   3. Added parser cap unit test:
      - `tests/test_parse_rate_limit_headers.py`
   4. Updated checker expectations:
      - `CT-PROVIDER-003` `case_count=6`
      - `tests/test_check_provider_semantics.py`
87. Full Wave-A + CI-equivalent rerun remains green after `CT-EXT-005` and retry-after cap additions:
   1. Bundle summary:
      - `ct-scenarios status=pass scenarios=40 failures=0`
      - `sync-conformance-matrix mapped=40/40 passing=40 failing=0 planned=0`
      - replay determinism and projection parity bundles pass.
   2. Expanded CI-equivalent pytest lane passes:
      - `133 passed`.
88. Session conflict diagnostics/safe-reload governance depth increased (`G-018`):
   1. Added clear-path fixture:
      - `tests/fixtures/conformance_v1/session_semantics/task_scope_isolation_reload_clear_ok.json`
   2. Added explicit pass-path unit test:
      - `tests/test_check_session_semantics.py`
   3. Added CT row:
      - `CT-SESSION-006` in `docs/conformance/ct_scenarios_v1.json`
      - artifact target: `artifacts/conformance/node_gate/ct_session_006.json`
89. Full Wave-A rerun remains green after `CT-SESSION-006` expansion:
   1. Bundle summary:
      - `ct-scenarios status=pass scenarios=41 failures=0`
      - `sync-conformance-matrix mapped=41/41 passing=41 failing=0 planned=0`
   2. Expanded CI-equivalent pytest lane passes:
      - `135 passed`.
90. Projection substrate coherence depth increased (`G-031` partial closure):
   1. Surface parity report emitters now include:
      - `projection_contract_version=bb.projection.contract.v1`
   2. `check_projection_surface_parity_bundle.py` now enforces:
      - contract-version presence on every surface
      - cross-surface contract-version equality
   3. Validation gates now enforce contract version on all parity artifacts:
      - `scripts/validate_conformance_artifacts.py`
      - projection parity schemas updated in `docs/conformance/schemas/`
91. Protocol status/error taxonomy lane added (`G-032` depth increase):
   1. `scripts/check_protocol_semantics.py --mode status_error_taxonomy`
   2. Fixtures:
      - `tests/fixtures/conformance_v1/protocol_semantics/status_error_taxonomy_ok.json`
      - `tests/fixtures/conformance_v1/protocol_semantics/status_error_taxonomy_bad.json`
   3. CT row:
      - `CT-PROTO-005` in `docs/conformance/ct_scenarios_v1.json`
   4. Validation remains green:
      - `ct-scenarios status=pass scenarios=45 failures=0`
      - `sync-conformance-matrix mapped=45/45 passing=45 failing=0 planned=0`
      - expanded CI-equivalent lane: `177 passed`
92. Protocol PTY/session anti-bleed lane added (`G-023` depth increase):
   1. `scripts/check_protocol_semantics.py --mode session_stream_isolation`
   2. Fixtures:
      - `tests/fixtures/conformance_v1/protocol_semantics/session_stream_isolation_ok.json`
      - `tests/fixtures/conformance_v1/protocol_semantics/session_stream_isolation_bad.json`
   3. CT row:
      - `CT-PROTO-006` in `docs/conformance/ct_scenarios_v1.json`
   4. Validation remains green:
      - `ct-scenarios status=pass scenarios=45 failures=0`
      - `sync-conformance-matrix mapped=45/45 passing=45 failing=0 planned=0`
      - expanded CI-equivalent lane: `177 passed`
93. Provider retry/reset normalization depth increased (`G-022` depth increase):
   1. `parse_rate_limit_headers` now supports explicit `*-reset-ms` keys for:
      - `x-ratelimit-*`
      - `ratelimit-*`
      - `anthropic-ratelimit-*`
   2. `scripts/check_provider_semantics.py --mode retry_after_normalization` now supports deterministic `bucket_reset_bounds` assertions.
   3. `CT-PROVIDER-003` fixture set expanded; expected `case_count` increased to `9`.
   4. Validation remains green:
      - targeted provider/protocol/header suites: `29 passed`
      - `ct-scenarios status=pass scenarios=45 failures=0`
      - `sync-conformance-matrix mapped=45/45 passing=45 failing=0 planned=0`
      - expanded CI-equivalent lane: `177 passed`
94. Protocol operator diagnostics taxonomy lane added (`G-032` depth increase):
   1. `scripts/check_protocol_semantics.py --mode operator_diagnostics_pack`
   2. Fixtures:
      - `tests/fixtures/conformance_v1/protocol_semantics/operator_diagnostics_pack_ok.json`
      - `tests/fixtures/conformance_v1/protocol_semantics/operator_diagnostics_pack_bad.json`
   3. CT row:
      - `CT-PROTO-007` in `docs/conformance/ct_scenarios_v1.json`
   4. Validation remains green:
      - `ct-scenarios status=pass scenarios=45 failures=0`
      - `sync-conformance-matrix mapped=45/45 passing=45 failing=0 planned=0`
      - expanded CI-equivalent lane: `177 passed`
95. Extension lifecycle branch coverage lane added (`G-019` depth increase):
   1. `scripts/check_extension_audit_stream_semantics.py --mode lifecycle_branch_coverage`
   2. Fixtures:
      - `tests/fixtures/conformance_v1/extension_semantics/lifecycle_branch_coverage_ok.json`
      - `tests/fixtures/conformance_v1/extension_semantics/lifecycle_branch_coverage_bad.json`
   3. CT row:
      - `CT-EXT-006` in `docs/conformance/ct_scenarios_v1.json`
   4. Wave-A workflow now includes this checker family explicitly:
      - `scripts/check_extension_audit_stream_semantics.py`
      - `tests/test_check_extension_audit_stream_semantics.py`
   5. Validation remains green:
      - `ct-scenarios status=pass scenarios=45 failures=0`
      - `sync-conformance-matrix mapped=45/45 passing=45 failing=0 planned=0`
      - expanded CI-equivalent lane: `177 passed`

## Gap mapping

Primary gaps directly advanced by this tranche:

1. `G-008` (Conformance Suite): moved from planning-only to executable initial gate.
2. `G-029` (Schema Compatibility discipline): initial schema package and deterministic parse/validation checks are now runnable.
3. `G-008` deepened with implemented CT scenario commands across all listed Wave A families: protocol, tool, approval, session, projection, provider, extension, recursion, reliability.
4. `G-008` now includes automated matrix status synchronization from CT scenario outputs.
5. `G-008` semantic depth increased for projection/tool families via explicit trace/value semantics (not only node-level pytest execution artifacts).

Secondary support for:

1. `G-009` (Replay Determinism gate): executable gate logic implemented with typed failure signatures.
2. `G-025` (Recursion governor surface): initial CT recursion scenario execution path now wired via long-run flag checks.
3. `G-002` (transcript/runtime projection stability): initial projection CT scenario command is active via CLI bridge streaming tests.

## Remaining to close P0-level expectations

None for the current Wave-A contract. P0 expectations for the declared Wave-A surface are fully satisfied.

## Immediate next coding targets (Non-Blocking Expansion)

1. Expand projection parity to additional future surfaces (for example desktop/web client reducers) using `projection_surface_manifest_v1.json`.
2. Continue semantic fixture diversity increases in checker families where regression sensitivity is highest.
3. Continue periodic replay-evidence baseline refresh via `scripts/promote_replay_evidence_baselines.py`.

## Semantic-depth coverage matrix (current)

| Family | Rows | Current evidence depth | Status |
|---|---:|---|---|
| Protocol (`CT-PROTO-*`) | 7 | Explicit semantic checker rows (`CT-PROTO-001/002/003/004/005/006/007`) for contract export, envelope variants, payload samples, runtime translation, status/error taxonomy, PTY anti-bleed, and operator diagnostics semantics | High |
| Tool (`CT-TOOL-*`) | 5 | Explicit semantic checker rows (`CT-TOOL-001/002/003/004/005`) for transition graph + policy/runtime invariants | High |
| Approval (`CT-APPROVAL-*`) | 4 | Explicit semantic checker rows (`CT-APPROVAL-001`, `CT-APPROVAL-002`, `CT-APPROVAL-003`, `CT-APPROVAL-004`) | High |
| Plan (`CT-PLAN-*`) | 1 | Explicit decision-envelope semantic checker row (`CT-PLAN-001`) | Medium-High |
| Session (`CT-SESSION-*`) | 6 | Explicit semantic checker rows (`CT-SESSION-001`, `CT-SESSION-002`, `CT-SESSION-003`, `CT-SESSION-004`, `CT-SESSION-005`, `CT-SESSION-006`) | High |
| Projection (`CT-PROJ-*`) | 5 | Explicit stream semantics checker rows + cross-surface parity artifacts (SDK/sidebar/webapp/TUI/OpenTUI/VSCode reducer) + envelope snapshot baseline lane (`CT-PROJ-005`) | High |
| Provider (`CT-PROVIDER-*`) | 4 | Explicit semantic checker rows (`CT-PROVIDER-001`, `CT-PROVIDER-002`, `CT-PROVIDER-003`, `CT-PROVIDER-004`) | High |
| Extension (`CT-EXT-*`) | 6 | Explicit semantic checker rows (`CT-EXT-001`, `CT-EXT-002`, `CT-EXT-003`, `CT-EXT-004`, `CT-EXT-005`, `CT-EXT-006`) | High |
| Recursion (`CT-RECURSE-*`) | 7 | Explicit semantic checker rows (`CT-RECURSE-001`, `CT-RECURSE-002`, `CT-RECURSE-003`, `CT-RECURSE-004`, `CT-RECURSE-005`, `CT-RECURSE-006`, `CT-RECURSE-007`) | High |
| Reliability (`CT-REL-*`) | 4 | Script/checker rows for schema/fixture gate, replay determinism gate, conformance-matrix semantics, and exact-ID sync mapping semantics | High |

## Latest verification update (2026-02-25)

1. Resolved CT manifest hardening miss for the new projection envelope lane:
   1. `tests/test_ct_scenarios_manifest_hardening.py` now accepts `scripts/check_event_envelope_snapshots.py` row shape used by `CT-PROJ-005`.
2. Expanded provider retry-after normalization depth (`G-022`):
   1. `parse_rate_limit_headers` now prefers explicit `retry-after-ms`/`x-ratelimit-retry-after-ms` over second-based headers when both are present.
   2. Added alias support coverage for `x-ratelimit-retry-after` and future HTTP-date edge fixtures.
   3. Expanded `CT-PROVIDER-003` fixture set and raised deterministic assertion count from `9` to `12`.
3. Expanded PTY/session anti-bleed depth (`G-023`):
   1. `check_protocol_semantics.py --mode session_stream_isolation` now enforces required reconnect/fork event-type presence.
   2. Added single-session/single-stream max-bound assertions to prevent hidden cross-stream bleed.
   3. Expanded `CT-PROTO-006` deterministic assertions (`event_count=7`, `unique_session_count=1`, `unique_stream_count=1`).
4. Expanded extension lifecycle branch-depth coverage (`G-019`):
   1. `CT-EXT-006` now covers six branches: `install`, `update`, `uninstall`, `install_retry`, `update_retry`, `reinstall`.
   2. Deterministic assertions now require `required_branch_count=6` and `present_branch_count>=6`.
5. Expanded projection reconnect/coalescing depth (`G-027`):
   1. `CT-PROJ-001` now enforces reconnect-coalescing quality bounds: `max_batch_size_observed=2` and `gap_distance=3`.
   2. The projection checker now supports fixture-level constraints for batch-size, batch-count, and post-gap distance.
6. Expanded recursion depth for convergence + queue semantics:
   1. Added `CT-RECURSE-005` (`convergence_stage_bounded`) to assert bounded no-progress convergence-stage stopping.
   2. Added `CT-RECURSE-006` (`queue_ordering_visibility`) to assert deterministic queue claim/defer/complete visibility.
7. Expanded recursion budget-stop depth (`G-025`):
   1. Added `CT-RECURSE-007` (`global_token_budget_stop`) to assert deterministic global token-budget stopping and usage totals.
8. Full Wave-A verification status after fixes:
   1. CI-equivalent pytest lane: `187 passed`.
   2. `run_wave_a_conformance_bundle.sh`: pass (`ct-scenarios status=pass scenarios=49 failures=0`).
   3. Matrix sync/freshness: `mapped=49/49 passing=49 failing=0 planned=0`, freshness check pass.

## Latest verification update (2026-02-25, provider retry/date drift expansion)

1. Expanded retry-after parser resilience for cross-provider header drift (`G-022`):
   1. `parse_rate_limit_headers` now falls back to `x-ratelimit-retry-after-ms` when `retry-after-ms` is present but malformed.
   2. `_parse_retry_after_ms` now accepts ISO-8601 datetime values in addition to HTTP-date formats.
2. Expanded deterministic semantic fixture depth for `CT-PROVIDER-003`:
   1. Added reconnect-style mixed-header fallback case (`retry-after-ms` malformed + `x-ratelimit-retry-after-ms` valid).
   2. Added cross-format date drift cases (`ISO-8601`, `asctime`).
   3. Raised deterministic assertion count from `12` to `15`.
3. Targeted validation for this tranche:
   1. `pytest -q tests/test_parse_rate_limit_headers.py tests/test_check_provider_semantics.py tests/test_ct_scenarios_manifest_hardening.py` => `22 passed`.
4. Full Wave-A validation remains green:
   1. `run_wave_a_conformance_bundle.sh`: pass (`ct-scenarios status=pass scenarios=49 failures=0`).
   2. `sync_conformance_matrix_status.py`: pass (`mapped=49/49 passing=49 failing=0 planned=0`).
   3. `check_conformance_sync_freshness.py`: pass.
   4. `validate_conformance_artifacts.py`: pass.

## Latest verification update (2026-02-25, PTY anti-bleed storm bound expansion)

1. Expanded protocol anti-bleed checker depth for `CT-PROTO-006` (`G-023`):
   1. `check_protocol_semantics.py --mode session_stream_isolation` now supports:
      - `expected.required_event_type_counts`
      - `expected.max_cursor_step`
   2. The checker now emits:
      - `event_type_counts`
      - `max_cursor_step_observed`
2. Expanded session-stream fixture semantics:
   1. `session_stream_isolation_ok.json` now models reconnect-heavy but bounded stream progression.
   2. `session_stream_isolation_bad.json` now includes deterministic count/step policy violations.
3. Raised deterministic assertions:
   1. `tests/test_check_protocol_semantics.py` pass-path now asserts:
      - `event_count=10`
      - `max_cursor_step_observed=1`
      - reconnect count `=3`
   2. `docs/conformance/ct_scenarios_v1.json` (`CT-PROTO-006`) now asserts:
      - `event_count=10`
      - `max_cursor_step_observed=1`
4. Full Wave-A validation remains green after the expansion:
   1. Expanded conformance lane: `170 passed`.
   2. `run_wave_a_conformance_bundle.sh`: pass (`ct-scenarios status=pass scenarios=49 failures=0`).
   3. Matrix sync/freshness: `mapped=49/49 passing=49 failing=0 planned=0`, freshness check pass.
   4. Artifact validation: pass.

## Latest verification update (2026-02-25, extension lifecycle conflict/failure branch expansion)

1. Expanded lifecycle branch semantic fixture depth for `CT-EXT-006` (`G-019`):
   1. Added explicit branch families in pass fixture:
      - `conflict` (`requested` + `approval.decided` + `failed`)
      - `reinstall_failure` (`requested` + `approval.decided` + `failed`)
   2. Expanded fail fixture required branches to include the same two families.
2. Raised deterministic extension branch assertions:
   1. `tests/test_check_extension_audit_stream_semantics.py`: `required_branch_count=8`.
   2. `docs/conformance/ct_scenarios_v1.json` (`CT-EXT-006`):
      - `required_branch_count=8`
      - `present_branch_count>=8`
3. Full Wave-A validation remains green after the expansion:
   1. Expanded conformance lane: `170 passed`.
   2. `run_wave_a_conformance_bundle.sh`: pass (`ct-scenarios status=pass scenarios=49 failures=0`).
   3. Matrix sync/freshness: `mapped=49/49 passing=49 failing=0 planned=0`, freshness check pass.
   4. Artifact validation: pass.

## Latest verification update (2026-02-25, run-controller policy and resume durability expansion)

1. Added recursion policy boundary matrix lane (`CT-RECURSE-008`):
   1. New checker mode: `check_longrun_semantics.py --mode policy_boundary_matrix`.
   2. Validates deterministic stop reasons/episode counts across token, cost, wall-clock, subcall, max-episode, and provider-error retry-exhaustion boundaries.
2. Added recursion queue pause/resume durability lane (`CT-RECURSE-009`):
   1. New checker mode: `check_longrun_semantics.py --mode queue_pause_resume_durability`.
   2. Validates two-run pause/resume continuity with durable macro-state restoration and deterministic queue status outcomes.
3. Expanded recursion fixtures:
   1. `policy_boundary_matrix_{ok,bad}.json`
   2. `queue_pause_resume_durability_{ok,bad}.json`
4. Expanded CT matrix/manifest scope:
   1. `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv` now includes `CT-RECURSE-008/009`.
   2. `docs/conformance/ct_scenarios_v1.json` now executes both new rows as P1-blocking gates.
5. Full Wave-A validation remains green after expansion:
   1. Expanded conformance lane: `174 passed`.
   2. `run_wave_a_conformance_bundle.sh`: pass (`ct-scenarios status=pass scenarios=51 failures=0`).
   3. Matrix sync/freshness: `mapped=51/51 passing=51 failing=0 planned=0`, freshness check pass.
   4. Artifact validation: pass.

## Latest verification update (2026-02-25, global subcall budget stop expansion)

1. Added global subcall-budget run-controller semantics (`G-025` depth):
   1. `LongRunController` now supports `long_running.budgets.max_total_subcalls` (and `total_subcalls` alias).
   2. Deterministic stop reason added: `total_subcalls_budget_reached`.
   3. Macro summary now emits `budget_limits.max_total_subcalls` and `budget_usage.total_subcalls_used`.
2. Added recursion semantic checker lane:
   1. New mode: `scripts/check_longrun_semantics.py --mode global_subcall_budget_stop`.
   2. Fixtures:
      - `tests/fixtures/conformance_v1/recursion_semantics/global_subcall_budget_stop_ok.json`
      - `tests/fixtures/conformance_v1/recursion_semantics/global_subcall_budget_stop_bad.json`
   3. Unit tests:
      - `tests/test_check_longrun_semantics.py` pass/fail coverage for the new mode.
      - `tests/test_longrun_controller.py` controller-level stop enforcement coverage.
3. Added CT row and matrix coverage:
   1. `CT-RECURSE-010` in `docs/conformance/ct_scenarios_v1.json`.
   2. `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv` now includes `CT-RECURSE-010`.
4. Full Wave-A validation remains green after expansion:
   1. Targeted tests: `47 passed`.
   2. `run_wave_a_conformance_bundle.sh`: pass (`ct-scenarios status=pass scenarios=52 failures=0`).
   3. Matrix sync/freshness: `mapped=52/52 passing=52 failing=0 planned=0`, freshness check pass.
   4. Artifact validation: pass.

## Latest verification update (2026-02-25, resumed-state subcall budget precheck expansion)

1. Added resumed-state subcall budget precheck lane (`G-025` depth):
   1. New checker mode: `scripts/check_longrun_semantics.py --mode subcall_budget_resume_precheck`.
   2. Validates resume-seeded pre-episode stop behavior when persisted `rlm_usage.subcalls_used` already meets budget cap.
2. Added recursion fixtures:
   1. `tests/fixtures/conformance_v1/recursion_semantics/subcall_budget_resume_precheck_ok.json`
   2. `tests/fixtures/conformance_v1/recursion_semantics/subcall_budget_resume_precheck_bad.json`
3. Added test and CT coverage:
   1. `tests/test_check_longrun_semantics.py` pass/fail lane for resumed-state precheck mode.
   2. `tests/test_longrun_controller.py` resume pre-episode subcall budget cap unit test.
   3. New CT row: `CT-RECURSE-011` in `docs/conformance/ct_scenarios_v1.json`.
   4. Matrix row added: `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
4. Full Wave-A validation remains green after expansion:
   1. Targeted tests: `47 passed`.
   2. `run_wave_a_conformance_bundle.sh`: pass (`ct-scenarios status=pass scenarios=53 failures=0`).
   3. Matrix sync/freshness: `mapped=53/53 passing=53 failing=0 planned=0`, freshness check pass.
   4. Artifact validation: pass.

## Latest verification update (2026-02-25, provider-coupled recursion trace expansion)

1. Added provider-coupled recursion trace lane (`G-025` depth):
   1. New checker mode: `scripts/check_longrun_semantics.py --mode provider_coupled_budget_trace`.
   2. Covers deterministic provider-error backoff + queue defer/claim + subcall-budget stop interplay.
2. Added recursion fixtures:
   1. `tests/fixtures/conformance_v1/recursion_semantics/provider_coupled_budget_trace_ok.json`
   2. `tests/fixtures/conformance_v1/recursion_semantics/provider_coupled_budget_trace_bad.json`
3. Added test and CT coverage:
   1. `tests/test_check_longrun_semantics.py` pass/fail cases for provider-coupled trace mode.
   2. New CT row `CT-RECURSE-012` in `docs/conformance/ct_scenarios_v1.json`.
   3. Matrix row added in `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
4. Full Wave-A validation remains green after expansion:
   1. Targeted tests: `52 passed`.
   2. `run_wave_a_conformance_bundle.sh`: pass (`ct-scenarios status=pass scenarios=54 failures=0`).
   3. Matrix sync/freshness: `mapped=54/54 passing=54 failing=0 planned=0`, freshness check pass.
   4. Artifact validation: pass.

## Latest verification update (2026-02-25, queue multi-resume durability expansion)

1. Added queue multi-resume recursion durability lane (`G-026` depth):
   1. New checker mode: `scripts/check_longrun_semantics.py --mode queue_multi_resume_durability`.
   2. Validates deterministic queue status transitions across three-run cycles with two resume boundaries.
2. Added recursion fixtures:
   1. `tests/fixtures/conformance_v1/recursion_semantics/queue_multi_resume_durability_ok.json`
   2. `tests/fixtures/conformance_v1/recursion_semantics/queue_multi_resume_durability_bad.json`
3. Added test and CT coverage:
   1. `tests/test_check_longrun_semantics.py` pass/fail cases for queue multi-resume mode.
   2. New CT row `CT-RECURSE-013` in `docs/conformance/ct_scenarios_v1.json`.
   3. Matrix row added in `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
4. Full Wave-A validation remains green after expansion:
   1. Targeted tests: `54 passed`.
   2. `run_wave_a_conformance_bundle.sh`: pass (`ct-scenarios status=pass scenarios=55 failures=0`).
   3. Matrix sync/freshness: `mapped=55/55 passing=55 failing=0 planned=0`, freshness check pass.
   4. Artifact validation: pass.

## Latest verification update (2026-02-25, queue multi-resume persisted-backend expansion)

1. Added persisted-backend queue multi-resume lane (`G-026` depth):
   1. New checker mode: `scripts/check_longrun_semantics.py --mode queue_multi_resume_persisted_backend`.
   2. Now routes through runtime `FeatureFileQueue` semantics (observed wrapper for assertions) across three runs and validates deterministic status transitions and resume behavior.
2. Added recursion fixtures:
   1. `tests/fixtures/conformance_v1/recursion_semantics/queue_multi_resume_persisted_backend_ok.json`
   2. `tests/fixtures/conformance_v1/recursion_semantics/queue_multi_resume_persisted_backend_bad.json`
3. Added test and CT coverage:
   1. `tests/test_check_longrun_semantics.py` pass/fail cases for persisted-backend queue mode.
   2. New CT row `CT-RECURSE-014` in `docs/conformance/ct_scenarios_v1.json`.
   3. Matrix row added in `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
4. Full Wave-A validation remains green after expansion:
   1. Targeted tests: `56 passed`.
   2. `run_wave_a_conformance_bundle.sh`: pass (`ct-scenarios status=pass scenarios=56 failures=0`).
   3. Matrix sync/freshness: `mapped=56/56 passing=56 failing=0 planned=0`, freshness check pass.
   4. Artifact validation: pass.

## Latest verification update (2026-02-26, queue artifact trace-backed coherence expansion)

1. Added queue artifact trace-backed coherence lane (`G-026` depth):
   1. New checker mode: `scripts/check_longrun_semantics.py --mode queue_backend_trace_coherence`.
   2. Executes a multi-resume run via runtime `FeatureFileQueue` and validates coherence across:
      - macro summary payload,
      - `meta/longrun_summary.json`,
      - `meta/longrun_state.json`,
      - `meta/longrun_queue_snapshot.json`,
      - `meta/episodes/longrun_episode_*.json`.
2. Added recursion fixtures:
   1. `tests/fixtures/conformance_v1/recursion_semantics/queue_backend_trace_coherence_ok.json`
   2. `tests/fixtures/conformance_v1/recursion_semantics/queue_backend_trace_coherence_bad.json`
3. Added test and CT coverage:
   1. `tests/test_check_longrun_semantics.py` pass/fail cases for trace coherence mode.
   2. New CT row `CT-RECURSE-015` in `docs/conformance/ct_scenarios_v1.json`.
   3. Matrix row added in `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
4. Full Wave-A validation remains green after expansion:
   1. Targeted tests: `58 passed`.
   2. `run_wave_a_conformance_bundle.sh`: pass (`ct-scenarios status=pass scenarios=57 failures=0`).
   3. Matrix sync/freshness: `mapped=57/57 passing=57 failing=0 planned=0`, freshness check pass.
   4. Artifact validation: pass.

## Latest verification update (2026-02-26, external trace bundle validation expansion)

1. Extended queue trace coherence lane to support external bundles:
   1. `scripts/check_longrun_semantics.py --mode queue_backend_trace_coherence` now accepts `external_trace_dir`.
   2. Validates the same invariants over pre-captured bundles:
      - summary/state/queue stop-reason and status coherence,
      - per-episode artifact count/index coverage,
      - backend/open-count/status expectations.
2. Added external bundle fixtures:
   1. `tests/fixtures/conformance_v1/recursion_semantics/trace_bundle_a/meta/...`
   2. payload fixtures:
      - `queue_backend_trace_external_ok.json`
      - `queue_backend_trace_external_bad.json`
3. Added test and CT coverage:
   1. `tests/test_check_longrun_semantics.py` external pass/fail cases.
   2. New CT row `CT-RECURSE-016` in `docs/conformance/ct_scenarios_v1.json`.
   3. Matrix row added in `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
4. Full Wave-A validation remains green after expansion:
   1. Targeted tests: `60 passed`.
   2. `run_wave_a_conformance_bundle.sh`: pass (`ct-scenarios status=pass scenarios=58 failures=0`).
   3. Matrix sync/freshness: `mapped=58/58 passing=58 failing=0 planned=0`, freshness check pass.
   4. Artifact validation: pass.

## Latest verification update (2026-02-26, gap control-plane hardening)

1. Added canonical gap closure scoring inputs under repo control:
   1. `docs/conformance/GAP_PRIORITY_WEIGHTS_V1.csv` (priority-weight source for all `G-*` rows).
   2. `docs/conformance/GAP_CLOSURE_CRITERIA_V1.md` (status vocabulary + completion weights + required fields).
2. Added gap status/control-plane semantic gates:
   1. `scripts/check_gap_status_vocabulary.py`
      - validates `G-###` ids, allowed gap statuses (`open|partial|covered`), and matrix status vocabulary.
      - enforces non-empty `remaining_closure_delta` for non-closed rows.
   2. `scripts/check_gap_closure_consistency.py`
      - validates `CT-*` references in `GAP_TO_CT_COVERAGE_MAP_V1.csv` exist in both matrix and scenario manifest.
      - enforces minimum CT reference requirement for `covered` rows.
   3. `scripts/generate_gap_progress_report.py`
      - emits weighted closure score report from gap-map + canonical weights.
3. Wired new checks into the Wave-A bundle:
   1. `scripts/run_wave_a_conformance_bundle.sh` now emits:
      - `gap_status_vocabulary_report_v1.json`
      - `gap_closure_consistency_report_v1.json`
      - `gap_progress_report_v1.json`
      - `gap_progress_report_v1.md`
4. Expanded CI filter/unit coverage:
   1. `.github/workflows/conformance-wave-a-gate.yml` now tracks new scripts/tests/docs paths.
   2. Added tests:
      - `tests/test_check_gap_status_vocabulary.py`
      - `tests/test_check_gap_closure_consistency.py`
      - `tests/test_generate_gap_progress_report.py`
      - workflow guard assertions in `tests/test_conformance_wave_a_workflow_guard.py`
5. Validation:
   1. Targeted tranche tests: `14 passed`.
   2. Full Wave-A bundle: pass (`ct-scenarios status=pass scenarios=58 failures=0`).
   3. Matrix freshness + semantics checks: pass.

## Latest verification update (2026-02-26, projection multi-gap/reorder lane)

1. Expanded projection reconnect semantics (`G-027`) with multi-gap/reorder support:
   1. `scripts/check_projection_semantics.py --mode reconnect_gap_coalescing` now supports:
      - `expected.min_gap_event_count`
      - `expected.allow_reordered_batches`
      - `gap_events[]` multi-gap input with strict increasing `next_available_seq`.
2. Added fixtures:
   1. `tests/fixtures/conformance_v1/projection_semantics/reconnect_multi_gap_reorder_ok.json`
   2. `tests/fixtures/conformance_v1/projection_semantics/reconnect_multi_gap_reorder_bad.json`
3. Added test + CT/matrix coverage:
   1. `tests/test_check_projection_semantics.py` pass/fail tests for new semantics.
   2. New CT row `CT-PROJ-006` in `docs/conformance/ct_scenarios_v1.json`.
   3. Matrix row `CT-PROJ-006` in `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
4. Updated mapping/evidence docs:
   1. `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{md,csv}` (`G-027` now includes `CT-PROJ-006`).
   2. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-027` expanded).
5. Validation:
   1. Projection + CT + bundle tests: `16 passed`.
   2. Wave-A bundle pass (`ct-scenarios status=pass scenarios=59 failures=0`).
   3. Matrix sync/freshness + semantics checks pass (`mapped=59/59 passing=59 failing=0 planned=0`).

## Latest verification update (2026-02-26, provider provenance runtime-path closure)

1. Closed provider provenance runtime-path gap (`G-011`) with canonical emission:
   1. `provider_router.build_resolution_provenance(...)` now accepts explicit `resolution_path` and optional `fallback_of_route_id`.
   2. Main loop emission now tags `resolution_path=main_loop`.
   3. Fallback runtime path now updates provider metadata (`provider_id`, `runtime_id`, `api_variant`, `resolved_model`) and emits:
      - `provider_resolution.resolution_path=fallback`
      - `provider_resolution.fallback_of_route_id=<primary route>`
   4. RLM subcall and batch-subcall artifacts now carry canonical provenance:
      - `runtime_id`
      - `api_variant`
      - `provider_resolution` with `resolution_path=subcall|batch_subcall`.
2. Added provider provenance semantic lane and fixtures:
   1. New fixtures:
      - `tests/fixtures/conformance_v1/provider_semantics/resolution_provenance_ok.json`
      - `tests/fixtures/conformance_v1/provider_semantics/resolution_provenance_bad.json`
   2. `scripts/check_provider_semantics.py --mode resolution_provenance` now enforces required provenance keys and path-aware expectations.
   3. Unit tests expanded in `tests/test_check_provider_semantics.py` with pass/fail coverage.
3. Added CT/matrix coverage:
   1. New CT row `CT-PROVIDER-005` in `docs/conformance/ct_scenarios_v1.json`.
   2. New matrix row `CT-PROVIDER-005` in `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
4. Updated gap/evidence mapping:
   1. `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` now mark `G-011` as `covered`.
   2. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` marks `G-011` closed with lane evidence.
5. Validation:
   1. Targeted suites pass (`23 passed`), including provider semantics + CT + bundle guards.
   2. Wave-A bundle pass (`ct-scenarios status=pass scenarios=60 failures=0`).
   3. Matrix sync/freshness + semantics checks pass (`mapped=60/60 passing=60 failing=0 planned=0`).
   4. Gap control-plane checks pass with updated weighted score (`overall_percent_complete=61.3`).

## Latest verification update (2026-02-26, tool runtime error-taxonomy hardening)

1. Expanded tool runtime semantics (`G-003` depth) with explicit rejection taxonomy checks:
   1. Added `scripts/check_tool_policy_semantics.py --mode runtime_error_taxonomy`.
   2. New lane validates deterministic reject codes across invalid lifecycle operations:
      - `invalid_call_id`
      - `duplicate_start`
      - `unknown_call`
      - `unknown_target_state`
      - `invalid_transition_edge`
      - `terminal_state_transition`
2. Added fixtures and tests:
   1. `tests/fixtures/conformance_v1/tool/runtime_error_taxonomy_ok.json`
   2. `tests/fixtures/conformance_v1/tool/runtime_error_taxonomy_bad.json`
   3. `tests/test_check_tool_policy_semantics.py` pass/fail additions for new mode.
3. Added CT/matrix coverage:
   1. New CT row `CT-TOOL-006` in `docs/conformance/ct_scenarios_v1.json`.
   2. New matrix row `CT-TOOL-006` in `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
4. Updated gap/evidence mapping:
   1. `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` now include `CT-TOOL-006` under `G-003` with narrowed remaining delta.
   2. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` refreshed with taxonomy-lane evidence.
5. Validation:
   1. Targeted suites pass (`29 passed`).
   2. Wave-A bundle pass (`ct-scenarios status=pass scenarios=61 failures=0`).
   3. Matrix sync/freshness + semantics checks pass (`mapped=61/61 passing=61 failing=0 planned=0`).

## Latest verification update (2026-02-26, projection strict-manifest parity lane)

1. Deepened projection substrate unification lane (`G-031`) with strict manifest enforcement:
   1. `scripts/check_projection_surface_parity_bundle.py` now validates optional per-surface manifest constraints:
      - `expected_schema_version`
      - `min_cases_total`
   2. Bundle report now emits:
      - `manifest_surface_count`
      - `manifest_constraints_valid`
2. Updated canonical projection surface manifest:
   1. `docs/conformance/projection_surface_manifest_v1.json` now specifies expected schema versions and minimum case floors for:
      - sdk, opentui, webapp, tui, vscode.
3. Expanded tests:
   1. `tests/test_check_projection_surface_parity_bundle.py` now covers:
      - manifest schema-version mismatch failures
      - manifest case-floor failures
      - manifest constraint validity checks.
   2. `tests/test_run_wave_a_conformance_bundle.py` now asserts `manifest_constraints_valid=true`.
4. Added CT/matrix coverage:
   1. New CT row `CT-PROJ-007` in `docs/conformance/ct_scenarios_v1.json`.
   2. New matrix row `CT-PROJ-007` in `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
   3. Added projection fixture bundle for CT lane:
      - `tests/fixtures/conformance_v1/projection_semantics/surface_bundle_ok/*.json`
5. Wave-A bundle integration:
   1. `scripts/run_wave_a_conformance_bundle.sh` now runs parity bundle with:
      - `--surface-manifest docs/conformance/projection_surface_manifest_v1.json`.
6. Validation:
   1. Targeted suites pass (`25 passed` for projection+bundle tranche).
   2. Wave-A bundle pass (`ct-scenarios status=pass scenarios=62 failures=0`).
   3. Matrix sync/freshness + semantics checks pass (`mapped=62/62 passing=62 failing=0 planned=0`).

## Latest verification update (2026-02-26, approval replay-equivalence lane)

1. Closed approval semantics replay lane (`G-005`) with replay allow/deny equivalence checks:
   1. Added `scripts/check_approval_semantics.py --mode replay_allow_deny_equivalence`.
   2. New lane enforces deterministic approval replay parity by checking:
      - one canonical decision per `approval_id` in baseline and replay sets,
      - no missing/extra approval IDs between baseline and replay,
      - no allow/deny mismatches for shared approval IDs.
2. Added fixtures/tests plus captured live replay evidence:
   1. `tests/fixtures/conformance_v1/approval_semantics/replay_allow_deny_equivalence_ok.json`
   2. `tests/fixtures/conformance_v1/approval_semantics/replay_allow_deny_equivalence_bad.json`
   3. Captured live evidence payload:
      - `docs/conformance/approval_replay_evidence/approval_replay_equivalence_live_case_001.json`
   4. `tests/test_check_approval_semantics.py` now includes pass/fail coverage for replay equivalence mode.
3. Added CT/matrix coverage:
   1. New CT row `CT-APPROVAL-005` in `docs/conformance/ct_scenarios_v1.json`.
   2. New matrix row `CT-APPROVAL-005` in `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
4. Updated gap/evidence mapping:
   1. `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` now include `CT-APPROVAL-005` for `G-005` with narrowed closure delta.
   2. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` updated with replay-equivalence lane evidence.
   3. `G-005` now marked `covered/closed` in the gap maps and competitor delta ledger.
5. Validation:
   1. Targeted suites pass (`16 passed`).
   2. Wave-A bundle pass (`ct-scenarios status=pass scenarios=63 failures=0`).
   3. Matrix sync/freshness + semantics checks pass (`mapped=63/63 passing=63 failing=0 planned=0`).

## Latest verification update (2026-02-26, replay determinism stricter non-fixture floor)

1. Tightened replay determinism release policy (`G-009` depth):
   1. Added third captured live replay report:
      - `docs/conformance/replay_evidence/replay_determinism_live_baseline_003.json`
   2. Raised non-fixture replay floor from `2` to `3` in both:
      - `scripts/run_wave_a_conformance_bundle.sh`
      - `CT-REL-002` command in `docs/conformance/ct_scenarios_v1.json`.
2. Updated guard/tests:
   1. `tests/test_run_wave_a_conformance_bundle.py` now asserts `min_non_fixture_files_required == 3`.
   2. `tests/test_ct_scenarios_manifest_hardening.py` now pins `--min-non-fixture-files 3` in both CT row and bundle script.
3. Updated evidence docs/mapping:
   1. `docs/conformance/replay_evidence/README.md` now documents the 3-file non-fixture floor.
   2. `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` updates `G-009` remaining delta language to reflect new floor.
   3. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` updated with the stricter floor + third live baseline evidence.
4. Validation:
   1. Targeted suites pass (`16 passed`).
   2. Wave-A bundle pass (`ct-scenarios status=pass scenarios=63 failures=0`).
   3. Replay gate pass with stricter floor and control-plane checks green.

## Latest verification update (2026-02-26, tool taxonomy lane promoted to captured live evidence)

1. Promoted tool runtime error-taxonomy lane (`G-003` depth) from fixture-only payload to captured live evidence:
   1. Added live evidence payload:
      - `docs/conformance/tool_runtime_evidence/runtime_error_taxonomy_live_case_001.json`
   2. Updated `CT-TOOL-006` payload source in `docs/conformance/ct_scenarios_v1.json` to use the captured live evidence file.
   3. Updated matrix fixture source for `CT-TOOL-006` in `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
2. Updated mapping/evidence docs:
   1. `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` now reflect captured-live taxonomy evidence and a narrowed remaining delta.
   2. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` updated with captured-live evidence for `G-003`.
3. Validation:
   1. Targeted suites pass (`19 passed`).
   2. Wave-A bundle pass (`ct-scenarios status=pass scenarios=63 failures=0`).
   3. Control-plane checks pass (`gap-status-vocabulary`, `gap-closure-consistency`, `gap-progress-report`).

## Latest verification update (2026-02-26, extension lifecycle branch coverage promoted to captured live evidence)

1. Promoted extension lifecycle branch-coverage lane (`G-019` depth) from fixture-only payload to captured live evidence:
   1. Added live evidence payload:
      - `docs/conformance/extension_runtime_evidence/lifecycle_branch_coverage_live_case_001.json`
   2. Updated `CT-EXT-006` payload source in `docs/conformance/ct_scenarios_v1.json` to use captured live evidence.
   3. Updated matrix fixture source for `CT-EXT-006` in `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
2. Updated mapping/evidence docs:
   1. `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` now reflect captured-live branch-coverage evidence with narrowed remaining delta.
   2. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` updated with captured-live evidence for `G-019`.
3. Validation:
   1. Wave-A bundle and control-plane checks remain pass after lane promotion.

## Latest verification update (2026-02-26, projection overlay semantics promoted to captured live evidence)

1. Promoted projection overlay semantics lane (`G-002` depth) from fixture-only payload to captured live evidence:
   1. Added live evidence payload:
      - `docs/conformance/projection_runtime_evidence/revision_overlay_live_case_001.json`
   2. Updated `CT-PROJ-003` payload source in `docs/conformance/ct_scenarios_v1.json` to use captured live evidence.
   3. Updated matrix fixture source for `CT-PROJ-003` in `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
2. Updated mapping/evidence docs:
   1. `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` now reflect captured-live overlay evidence with narrowed remaining delta.
   2. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` updated with captured-live evidence for `G-002`.
3. Validation:
   1. Wave-A bundle and control-plane checks remain pass after lane promotion.

## Latest verification update (2026-02-26, trust/consent deny-by-default lane added and `G-016` closed)

1. Added a new extension semantic mode for runtime trust/consent closure (`G-016`):
   1. `scripts/check_extension_audit_stream_semantics.py --mode trust_consent_default_deny`.
   2. Mode enforces:
      - deny-by-default when workspace trust or consent requirements are unmet,
      - deterministic allow/deny expectations per operation,
      - complete audit stream requirements (`requested`, `approval.decided`, and exactly one terminal event),
      - required event-type coverage and monotonic timestamps.
2. Added fixtures/tests:
   1. `tests/fixtures/conformance_v1/extension_semantics/trust_consent_default_deny_ok.json`
   2. `tests/fixtures/conformance_v1/extension_semantics/trust_consent_default_deny_bad.json`
   3. `tests/test_check_extension_audit_stream_semantics.py` now includes pass/fail coverage for the new mode.
3. Added captured live evidence + CT/matrix lane:
   1. `docs/conformance/extension_runtime_evidence/trust_consent_default_deny_live_case_001.json`
   2. `CT-EXT-007` added to `docs/conformance/ct_scenarios_v1.json` (P0-blocking).
   3. `CT-EXT-007` added to `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
4. Updated gap mapping/ledger:
   1. `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` now mark `G-016` as `covered`.
   2. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` now marks `G-016` as `closed`.
5. Validation:
   1. Targeted suite pass (`17 passed`).
   2. Full Wave-A bundle pass (`ct-scenarios status=pass scenarios=64 failures=0`).
   3. Sync/freshness/consistency checks pass (`mapped=64/64 passing=64 failing=0 planned=0`).

## Latest verification update (2026-02-26, extension runtime hook execution lane added for `G-017` depth)

1. Added runtime middleware/hook execution semantic lane:
   1. `scripts/check_extension_semantics.py --mode runtime_hook_execution`.
   2. Lane validates runtime execution properties:
      - deterministic middleware execution order,
      - timeout behavior with isolation guarantees,
      - continuation after isolated timeout,
      - monotonic runtime timestamps and basic timeout duration sanity.
2. Added fixtures/tests:
   1. `tests/fixtures/conformance_v1/extension_semantics/runtime_hook_execution_ok.json`
   2. `tests/fixtures/conformance_v1/extension_semantics/runtime_hook_execution_bad.json`
   3. `tests/test_check_extension_semantics.py` now includes pass/fail coverage for runtime hook execution mode.
3. Added CT/matrix coverage:
   1. New CT row `CT-EXT-008` in `docs/conformance/ct_scenarios_v1.json`.
   2. New matrix row `CT-EXT-008` in `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
4. Updated mapping/evidence docs:
   1. `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` now include `CT-EXT-008` under `G-017` with narrowed remaining delta.
   2. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` now cites `CT-EXT-008` runtime lane evidence and narrowed next gate text.
5. Validation:
   1. Targeted suite pass (`19 passed`).
   2. Full Wave-A bundle pass (`ct-scenarios status=pass scenarios=65 failures=0`).
   3. Sync/control-plane checks pass (`mapped=65/65 passing=65 failing=0 planned=0`; `gap-closure-consistency` pass).

## Latest verification update (2026-02-26, tool correction-loop coupling lane added for `G-003` depth)

1. Added runtime correction-loop coupling semantic mode:
   1. `scripts/check_tool_policy_semantics.py --mode runtime_correction_loop_coupling`.
   2. Mode enforces:
      - strict per-operation attempt lineage (`retries_from_tool_call_id` parent linkage),
      - strict attempt increment semantics,
      - terminal completion constraints (single terminal completion at end),
      - failed-before-completed correction behavior invariants.
2. Added fixtures/tests:
   1. `tests/fixtures/conformance_v1/tool/runtime_correction_loop_coupling_ok.json`
   2. `tests/fixtures/conformance_v1/tool/runtime_correction_loop_coupling_bad.json`
   3. `tests/test_check_tool_policy_semantics.py` now includes pass/fail coverage for the new mode.
3. Added captured live evidence + CT/matrix lane:
   1. `docs/conformance/tool_runtime_evidence/runtime_correction_loop_live_case_001.json`
   2. New CT row `CT-TOOL-007` in `docs/conformance/ct_scenarios_v1.json`.
   3. New matrix row `CT-TOOL-007` in `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`.
4. Updated mapping/evidence docs:
   1. `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` now include `CT-TOOL-007` under `G-003`.
   2. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` now cites correction-loop live evidence and narrowed remaining gate language for `G-003`.
5. Validation:
   1. Targeted suite pass (`25 passed`).
   2. Full Wave-A bundle pass (`ct-scenarios status=pass scenarios=66 failures=0`).
   3. Sync/control-plane checks pass (`mapped=66/66 passing=66 failing=0 planned=0`; `gap-closure-consistency` pass; `gap-progress-report` pass at `64.04%`).

## Latest verification update (2026-02-26, replay determinism strictness raised again for `G-009` depth)

1. Tightened replay determinism release policy:
   1. Added fourth captured non-fixture replay baseline:
      - `docs/conformance/replay_evidence/replay_determinism_live_baseline_004.json`
   2. Raised gate floors in both bundle and CT row (`CT-REL-002`):
      - `--min-files` from `4` to `5`
      - `--min-non-fixture-files` from `3` to `4`
      - `--min-non-fixture-turns` from `10` to `15`
2. Updated tests/docs for new floor:
   1. `tests/test_run_wave_a_conformance_bundle.py`
   2. `tests/test_ct_scenarios_manifest_hardening.py`
   3. `tests/test_replay_evidence_baselines.py` (now requires at least 4 replay evidence files and `turn_count >= 15`)
   4. `docs/conformance/replay_evidence/README.md`
3. Updated mapping/evidence docs:
   1. `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-009` remaining delta text narrowed to reflect current floor).
   2. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` updated with 4-file floor + baseline_004 evidence.
4. Validation:
   1. Targeted suite pass (`31 passed`).
   2. Full Wave-A bundle pass:
      - replay gate pass with stricter floor (`[replay-determinism-gate] pass (6 files)`),
      - `ct-scenarios status=pass scenarios=66 failures=0`,
      - control-plane checks all pass (`mapped=66/66`, `gap-closure-consistency` pass, `gap-progress-report` pass `64.04%`).

## Latest verification update (2026-02-26, `CT-EXT-008` live-evidence promotion cleanup completed)

1. Closed remaining `CT-EXT-008` promotion drift:
   1. `ct_scenarios_v1.json` now consumes captured live payload `docs/conformance/extension_runtime_evidence/runtime_hook_execution_live_case_001.json`.
   2. Matrix/source-map/competitor-ledger wording for `G-017` narrowed from "promote to captured live traces" to "expand from canonical live case into broader runtime packs."
2. Updated gap/ledger files:
   1. `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}`
   2. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv`
3. Validation:
   1. Targeted suites pass:
      - `tests/test_check_extension_semantics.py` + `tests/test_ct_scenarios_manifest_hardening.py` (`9 passed`)
      - `tests/test_check_extension_audit_stream_semantics.py` + `tests/test_check_tool_policy_semantics.py` + `tests/test_replay_evidence_baselines.py` (`21 passed`)
   2. Full Wave-A bundle pass:
      - `[replay-determinism-gate] pass (6 files)`
      - `[ct-scenarios] status=pass scenarios=66 failures=0`
      - `[sync-conformance-matrix] mapped=66/66 passing=66 failing=0 planned=0`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
4. Refreshed generated views:
   1. `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.synced.csv`
   2. `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.md`
   3. `docs_tmp/COMPETITORS/UNRESOLVED_TOP_GAPS_V1.csv`

## Latest verification update (2026-02-26, `CT-EXT-008` multi-path phase coverage depth)

1. Strengthened extension runtime hook semantics (`G-017` depth):
   1. `check_extension_semantics.py --mode runtime_hook_execution` now supports optional `expected_required_phases`.
   2. Checker now emits phase coverage fields:
      - `phase_counts`,
      - `required_phase_count`,
      - `observed_phase_count`,
      - `missing_required_phases`.
2. Expanded runtime-hook fixtures and live evidence:
   1. `runtime_hook_execution_ok.json` now includes install/update/conflict phase events and required-phase assertions.
   2. `runtime_hook_execution_bad.json` now exercises missing required phase behavior.
   3. `docs/conformance/extension_runtime_evidence/runtime_hook_execution_live_case_001.json` now captures multi-path install/update/conflict runtime evidence.
3. Raised CT assertion depth for `CT-EXT-008`:
   1. `event_count=5`
   2. `required_phase_count=4`
   3. `observed_phase_count>=4`
4. Updated matrix + gap ledger wording:
   1. `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv` row notes now call out install/update/conflict phase coverage.
   2. `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` and `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` narrowed next gate to stress/failure variants beyond the new baseline.
5. Validation:
   1. Targeted suite pass (`15 passed`).
   2. Full Wave-A bundle pass:
      - `[ct-scenarios] status=pass scenarios=66 failures=0`
      - `[sync-conformance-matrix] mapped=66/66 passing=66 failing=0 planned=0`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
6. Refreshed generated outputs:
   1. `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.synced.csv`
   2. `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.md`
   3. `docs_tmp/COMPETITORS/UNRESOLVED_TOP_GAPS_V1.csv`

## Latest verification update (2026-02-25, `G-003` live family-B expansion + replay floor uplift)

1. Expanded `G-003` tool-runtime live evidence depth with two additional captured families:
   1. `docs/conformance/tool_runtime_evidence/runtime_error_taxonomy_live_case_002.json`
   2. `docs/conformance/tool_runtime_evidence/runtime_correction_loop_live_case_002.json`
2. Added two new CT tool rows and matrix lanes:
   1. `CT-TOOL-008` (`runtime_error_taxonomy`, family-B)
   2. `CT-TOOL-009` (`runtime_correction_loop_coupling`, family-B)
   3. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-003` narrowed delta text)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-003` required CT set now includes `CT-TOOL-008/009`)
3. Tightened replay determinism strict floor for `G-009`:
   1. Added fifth non-fixture replay baseline:
      - `docs/conformance/replay_evidence/replay_determinism_live_baseline_005.json`
   2. Raised deterministic gate floors:
      - `--min-files 6`
      - `--min-non-fixture-files 5`
      - `--min-non-fixture-turns 15` (unchanged)
   3. Updated command policy and tests:
      - `scripts/run_wave_a_conformance_bundle.sh`
      - `docs/conformance/ct_scenarios_v1.json` (`CT-REL-002` command + assertion minima)
      - `tests/test_ct_scenarios_manifest_hardening.py`
      - `tests/test_run_wave_a_conformance_bundle.py`
      - `tests/test_replay_evidence_baselines.py`
      - `docs/conformance/replay_evidence/README.md`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-009` narrowed delta text)
4. Additional test hardening:
   1. `tests/test_check_tool_policy_semantics.py` now includes pass coverage for both new live family-B tool-runtime payloads.
5. Validation (all green):
   1. Targeted suites: `22 passed` (`test_replay_evidence_baselines`, `test_ct_scenarios_manifest_hardening`, `test_run_wave_a_conformance_bundle`, `test_check_tool_policy_semantics`).
   2. CT scenarios: `[ct-scenarios] status=pass scenarios=68 failures=0`.
   3. Full refresh bundle:
      - `[replay-determinism-gate] pass (7 files)`
      - `[sync-conformance-matrix] mapped=68/68 passing=68 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=9`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-25, `G-017` runtime-hook stress/failure live families)

1. Expanded extension runtime-hook live evidence depth with two additional captured families:
   1. `docs/conformance/extension_runtime_evidence/runtime_hook_execution_live_case_002.json` (stress family-B: timeout + isolated failure continuation across expanded phase fanout).
   2. `docs/conformance/extension_runtime_evidence/runtime_hook_execution_live_case_003.json` (family-C: fatal-terminal failure semantics).
2. Added two new CT extension rows and matrix lanes:
   1. `CT-EXT-009` (`runtime_hook_execution`, family-B)
   2. `CT-EXT-010` (`runtime_hook_execution`, family-C)
   3. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-017` coverage + narrowed remaining delta)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-017` required CT/live CT set now includes `CT-EXT-009/010`)
3. Expanded extension semantic checker test coverage:
   1. `tests/test_check_extension_semantics.py` now includes direct pass checks for both new live payloads.
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-017` row evidence + remaining delta text).
5. Validation (all green):
   1. Targeted suites pass (`19 passed`).
   2. CT scenarios: `[ct-scenarios] status=pass scenarios=70 failures=0`.
   3. Full refresh bundle:
      - `[replay-determinism-gate] pass (7 files)`
      - `[sync-conformance-matrix] mapped=70/70 passing=70 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=11`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-25, `G-023` live anti-bleed reconnect/fork lanes)

1. Expanded protocol session-stream isolation into captured live runtime evidence:
   1. `docs/conformance/protocol_runtime_evidence/session_stream_isolation_live_case_001.json` (reconnect-storm lane)
   2. `docs/conformance/protocol_runtime_evidence/session_stream_isolation_live_case_002.json` (fork-race lane with terminal error close)
2. Added two new CT protocol rows and matrix lanes:
   1. `CT-PROTO-008` (`session_stream_isolation`, live reconnect-storm lane)
   2. `CT-PROTO-009` (`session_stream_isolation`, live fork-race lane)
   3. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-023` coverage + narrowed remaining delta)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-023` now requires live evidence for closure rubric checks)
3. Expanded protocol checker tests for live lanes:
   1. `tests/test_check_protocol_semantics.py` now includes direct pass checks for both new live payloads.
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-023` row evidence + narrowed delta).
5. Validation (all green):
   1. Targeted suites pass (`27 passed`).
   2. CT scenarios: `[ct-scenarios] status=pass scenarios=72 failures=0`.
   3. Full refresh bundle:
      - `[replay-determinism-gate] pass (7 files)`
      - `[sync-conformance-matrix] mapped=72/72 passing=72 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=13`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-25, `G-027` live reconnect multi-gap/reorder promotion)

1. Expanded projection reconnect/coalescing lanes into captured live runtime evidence:
   1. `docs/conformance/projection_runtime_evidence/reconnect_multi_gap_reorder_live_case_001.json`
   2. `docs/conformance/projection_runtime_evidence/reconnect_multi_gap_reorder_live_case_002.json`
2. Added two new CT projection rows and matrix lanes:
   1. `CT-PROJ-008` (live reconnect storm family-A, controlled reorder)
   2. `CT-PROJ-009` (live reconnect storm family-B, strict ordered batch flattening)
   3. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-027` coverage + narrowed remaining delta)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-027` now requires live evidence for closure rubric checks)
3. Expanded projection semantic checker tests for live lanes:
   1. `tests/test_check_projection_semantics.py` now includes pass checks for both new live reconnect payloads.
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-027` row evidence + narrowed delta).
5. Validation (all green):
   1. Targeted suites pass (`23 passed`).
   2. CT scenarios: `[ct-scenarios] status=pass scenarios=74 failures=0`.
   3. Full refresh bundle:
      - `[replay-determinism-gate] pass (7 files)`
      - `[sync-conformance-matrix] mapped=74/74 passing=74 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=15`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-25, `G-031` live projection surface render/input/destroy promotion)

1. Expanded projection surface parity lanes into captured live runtime evidence packs:
   1. `docs/conformance/projection_surface_runtime_evidence/render_input_destroy_live_case_001/`
   2. `docs/conformance/projection_surface_runtime_evidence/render_input_destroy_live_case_002/`
2. Added two new CT projection rows and matrix lanes:
   1. `CT-PROJ-010` (live render/input/destroy stress family-A)
   2. `CT-PROJ-011` (live render/input/destroy stress family-B)
   3. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-031` coverage + narrowed remaining delta)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-031` now requires live evidence for closure rubric checks)
3. Expanded projection-surface checker tests for new live lanes:
   1. `tests/test_check_projection_surface_parity_bundle.py` now includes direct pass checks for both live runtime evidence packs.
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-031` row evidence + narrowed delta).
5. Validation (all green):
   1. Targeted suites pass:
      - `tests/test_check_projection_surface_parity_bundle.py` (`9 passed`)
      - `tests/test_ct_scenarios_manifest_hardening.py` (`3 passed`)
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=76 failures=0`
      - `[sync-conformance-matrix] mapped=76/76 passing=76 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=17`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-25, `G-020` render/input/destroy lane rubric alignment)

1. Aligned `G-020` closure bookkeeping to the newly promoted live projection-surface stress lanes:
   1. `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` now maps `G-020` to `CT-PROJ-007/010/011`.
   2. `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` now marks `G-020` as live-evidence-gated with required live CT IDs `CT-PROJ-010/011`.
   3. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` now cites captured live render/input/destroy runtime evidence packs as closure evidence.
2. Validation (all green) after rubric/map alignment:
   1. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=76 failures=0`
      - `[sync-conformance-matrix] mapped=76/76 passing=76 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=17`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-25, `G-032` live operator diagnostics runtime lane promotion)

1. Expanded protocol diagnostics depth with captured live runtime evidence packs:
   1. `docs/conformance/protocol_runtime_evidence/operator_diagnostics_live_case_001.json`
   2. `docs/conformance/protocol_runtime_evidence/operator_diagnostics_live_case_002.json`
2. Added two new CT protocol rows and matrix lanes:
   1. `CT-PROTO-010` (live operator diagnostics family-A)
   2. `CT-PROTO-011` (live operator diagnostics family-B)
   3. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-032` coverage + narrowed remaining delta)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-032` now requires live evidence for closure rubric checks)
3. Expanded protocol semantic checker tests for new live lanes:
   1. `tests/test_check_protocol_semantics.py` now includes direct pass checks for both live operator diagnostics payloads.
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-032` row evidence + narrowed delta).
5. Validation (all green):
   1. Targeted suites pass:
      - `tests/test_check_protocol_semantics.py` (`18 passed`)
      - `tests/test_ct_scenarios_manifest_hardening.py` (`3 passed`)
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=78 failures=0`
      - `[sync-conformance-matrix] mapped=78/78 passing=78 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=19`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-25, `G-002` revision-overlay second live family promotion)

1. Expanded projection overlay live evidence depth with a second captured runtime family:
   1. `docs/conformance/projection_runtime_evidence/revision_overlay_live_case_002.json`
2. Added one new CT projection row and matrix lane:
   1. `CT-PROJ-012` (live revision-overlay family-B)
   2. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-002` coverage + narrowed remaining delta)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-002` required live CT set now includes `CT-PROJ-012`)
3. Expanded projection overlay checker tests:
   1. `tests/test_check_projection_overlay_semantics.py` now includes direct pass coverage for live case 002.
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-002` row evidence + narrowed delta).
5. Validation (all green):
   1. Targeted suites pass:
      - `tests/test_check_projection_overlay_semantics.py` (`3 passed`)
      - `tests/test_ct_scenarios_manifest_hardening.py` (`3 passed`)
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=79 failures=0`
      - `[sync-conformance-matrix] mapped=79/79 passing=79 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=20`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-25, `G-003` provider-coupled mixed-operation family-C promotion)

1. Expanded tool runtime live evidence depth with provider-coupled family-C traces:
   1. `docs/conformance/tool_runtime_evidence/runtime_error_taxonomy_live_case_003.json`
   2. `docs/conformance/tool_runtime_evidence/runtime_correction_loop_live_case_003.json`
2. Added two new CT tool rows and matrix lanes:
   1. `CT-TOOL-010` (runtime error taxonomy family-C)
   2. `CT-TOOL-011` (runtime correction-loop coupling family-C)
   3. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-003` coverage + narrowed remaining delta)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-003` required CT/live CT sets now include `CT-TOOL-010/011`)
3. Expanded tool policy semantic checker tests for new live lanes:
   1. `tests/test_check_tool_policy_semantics.py` now includes direct pass checks for both family-C live payloads.
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-003` row evidence + narrowed delta).
5. Validation (all green):
   1. Targeted suites pass:
      - `tests/test_check_tool_policy_semantics.py` (`18 passed`)
      - `tests/test_ct_scenarios_manifest_hardening.py` (`3 passed`)
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=81 failures=0`
      - `[sync-conformance-matrix] mapped=81/81 passing=81 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=22`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-25, `G-023` anti-bleed family-C/D promotion)

1. Expanded protocol session-stream isolation live evidence depth with two additional families:
   1. `docs/conformance/protocol_runtime_evidence/session_stream_isolation_live_case_003.json`
   2. `docs/conformance/protocol_runtime_evidence/session_stream_isolation_live_case_004.json`
2. Added two new CT protocol rows and matrix lanes:
   1. `CT-PROTO-012` (live reconnect-storm anti-bleed family-C)
   2. `CT-PROTO-013` (live fork-race anti-bleed family-D)
   3. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-023` coverage + narrowed remaining delta)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-023` required CT/live CT sets now include `CT-PROTO-012/013`)
3. Expanded protocol semantic checker tests for new live lanes:
   1. `tests/test_check_protocol_semantics.py` now includes direct pass checks for live cases 003 and 004.
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-023` row evidence + narrowed delta).
5. Validation (all green):
   1. Targeted suites pass:
      - `tests/test_check_protocol_semantics.py` (`20 passed`)
      - `tests/test_check_tool_policy_semantics.py` (`18 passed`)
      - `tests/test_ct_scenarios_manifest_hardening.py` (`3 passed`)
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=83 failures=0`
      - `[sync-conformance-matrix] mapped=83/83 passing=83 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=24`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-26, `G-009` replay determinism floor raise to 8/6)

1. Expanded non-fixture replay evidence depth with one additional live baseline:
   1. `docs/conformance/replay_evidence/replay_determinism_live_baseline_006.json`
2. Raised Wave-A replay gate floors (bundle + CT row):
   1. `--min-files` from `6` to `8`
   2. `--min-non-fixture-files` from `5` to `6`
   3. `--min-non-fixture-turns 15` unchanged
   4. Updated:
      - `scripts/run_wave_a_conformance_bundle.sh`
      - `docs/conformance/ct_scenarios_v1.json` (`CT-REL-002` command + assertions)
      - `tests/test_ct_scenarios_manifest_hardening.py` (policy pins for CT + bundle)
      - `docs/conformance/replay_evidence/README.md`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-009` remaining delta text)
3. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-009` evidence + threshold notes).
4. Validation (all green):
   1. Targeted suites pass:
      - `tests/test_check_replay_determinism_gate.py`
      - `tests/test_ct_scenarios_manifest_hardening.py`
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=83 failures=0`
      - `[sync-conformance-matrix] mapped=83/83 passing=83 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=24`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-26, `G-032` operator diagnostics family-C/D promotion + CT assertion path hardening)

1. Expanded protocol operator diagnostics live evidence depth with two additional families:
   1. `docs/conformance/protocol_runtime_evidence/operator_diagnostics_live_case_003.json`
   2. `docs/conformance/protocol_runtime_evidence/operator_diagnostics_live_case_004.json`
2. Added two new CT protocol rows and matrix lanes:
   1. `CT-PROTO-014` (live operator diagnostics family-C)
   2. `CT-PROTO-015` (live operator diagnostics family-D)
   3. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-032` coverage + narrowed remaining delta)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-032` required CT/live CT sets now include `CT-PROTO-014/015`)
3. Expanded protocol semantic checker tests for new live lanes:
   1. `tests/test_check_protocol_semantics.py` now includes direct pass checks for live cases 003 and 004.
4. Hardened CT runner artifact assertion semantics:
   1. `scripts/run_ct_scenarios.py` now rewrites assertion artifact paths through the same node/junit directory remap used by command args, eliminating stale default-path false-pass risk when custom artifact directories are used.
5. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-032` row evidence + narrowed delta).
6. Validation (all green):
   1. Targeted suites pass:
      - `tests/test_check_protocol_semantics.py` (`25 passed`)
      - `tests/test_ct_scenarios_manifest_hardening.py` (`3 passed`)
      - `tests/test_run_ct_scenarios.py` + `tests/test_run_wave_a_conformance_bundle.py::test_run_wave_a_conformance_bundle_emits_reports` (`5 passed`)
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=85 failures=0`
      - `[sync-conformance-matrix] mapped=85/85 passing=85 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=26`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-26, `G-010` reliability trend-budget gate integration)

1. Promoted reliability trend-budget checks into Wave-A bundle outputs:
   1. Added fixture-backed trend corpus:
      - `tests/fixtures/conformance_v1/reliability_trends/phase5_roundtrip_reliability_20260226-roundtrip1.json`
      - `tests/fixtures/conformance_v1/reliability_trends/phase5_roundtrip_reliability_20260226-roundtrip2.json`
   2. Bundle now emits:
      - `reliability_flake_report_v1.{json,md}`
      - `reliability_flake_budget_report_v1.{json,md}`
      via:
      - `scripts/build_phase5_reliability_flake_report.py`
      - `scripts/check_phase5_reliability_flake_budget.py`
2. Promoted trend-budget artifacts into artifact contract validation:
   1. `scripts/validate_conformance_artifacts.py` now requires and validates both trend report and trend budget gate outputs.
   2. `tests/test_validate_conformance_artifacts.py` seed fixtures updated accordingly.
3. Promoted trend-budget guards into CI workflow scope:
   1. `.github/workflows/conformance-wave-a-gate.yml` now tracks trend-budget scripts/tests in both `pull_request` and `push` path filters.
   2. Workflow unit-test step now executes:
      - `tests/test_build_phase5_reliability_flake_report.py`
      - `tests/test_check_phase5_reliability_flake_budget.py`
   3. Guard test coverage expanded in `tests/test_conformance_wave_a_workflow_guard.py`.
4. Coverage/ledger updates:
   1. `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-010` checker + remaining delta narrowed).
   2. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-010` closure evidence updated).
5. Validation (all green):
   1. Targeted suites pass:
      - `tests/test_build_phase5_reliability_flake_report.py`
      - `tests/test_check_phase5_reliability_flake_budget.py`
      - `tests/test_conformance_wave_a_workflow_guard.py`
      - `tests/test_validate_conformance_artifacts.py`
      - `tests/test_run_wave_a_conformance_bundle.py::test_run_wave_a_conformance_bundle_emits_reports`
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=85 failures=0`
      - `[sync-conformance-matrix] mapped=85/85 passing=85 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=26`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-26, `G-002` revision-overlay family-C promotion)

1. Expanded projection overlay live evidence depth with one additional family:
   1. `docs/conformance/projection_runtime_evidence/revision_overlay_live_case_003.json`
2. Added one new CT projection row and matrix lane:
   1. `CT-PROJ-013` (live revision-overlay family-C)
   2. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-002` coverage + narrowed remaining delta)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-002` required CT/live CT sets now include `CT-PROJ-013`)
3. Expanded projection overlay checker tests for the new live lane:
   1. `tests/test_check_projection_overlay_semantics.py` now includes direct pass coverage for live case 003.
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-002` row evidence + narrowed delta).
5. Validation (all green):
   1. Targeted suites pass:
      - `tests/test_check_projection_overlay_semantics.py` (`4 passed`)
      - `tests/test_ct_scenarios_manifest_hardening.py` (`3 passed`)
      - `tests/test_run_wave_a_conformance_bundle.py::test_run_wave_a_conformance_bundle_emits_reports`
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=86 failures=0`
      - `[sync-conformance-matrix] mapped=86/86 passing=86 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=27`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-26, `G-003` tool runtime family-D promotion)

1. Expanded tool runtime live evidence depth with provider/cross-provider family-D traces:
   1. `docs/conformance/tool_runtime_evidence/runtime_error_taxonomy_live_case_004.json`
   2. `docs/conformance/tool_runtime_evidence/runtime_correction_loop_live_case_004.json`
2. Added two new CT tool rows and matrix lanes:
   1. `CT-TOOL-012` (runtime error taxonomy family-D)
   2. `CT-TOOL-013` (runtime correction-loop coupling family-D)
   3. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-003` coverage + narrowed remaining delta)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-003` required CT/live CT sets now include `CT-TOOL-012/013`)
3. Expanded tool policy semantic checker tests for new live lanes:
   1. `tests/test_check_tool_policy_semantics.py` now includes direct pass checks for live cases 004 (taxonomy and correction loop).
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-003` row evidence + narrowed delta).
5. Validation (all green):
   1. Targeted suites pass:
      - `tests/test_check_tool_policy_semantics.py` (`23 passed`)
      - `tests/test_ct_scenarios_manifest_hardening.py` (`3 passed`)
      - `tests/test_run_wave_a_conformance_bundle.py::test_run_wave_a_conformance_bundle_emits_reports`
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=88 failures=0`
      - `[sync-conformance-matrix] mapped=88/88 passing=88 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=29`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-26, `G-020/G-031` projection-surface family-C/D promotion)

1. Expanded projection-surface live runtime evidence depth with two additional stress families:
   1. `docs/conformance/projection_surface_runtime_evidence/render_input_destroy_live_case_003/`
   2. `docs/conformance/projection_surface_runtime_evidence/render_input_destroy_live_case_004/`
2. Added two new CT projection rows and matrix lanes:
   1. `CT-PROJ-014` (render/input/destroy family-C, total_cases=58)
   2. `CT-PROJ-015` (render/input/destroy family-D, total_cases=63)
   3. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-020` + `G-031` coverage widened to include families A/B/C/D)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-020` + `G-031` required CT/live CT sets now include `CT-PROJ-014/015`)
3. Expanded projection-surface checker tests for the new live lanes:
   1. `tests/test_check_projection_surface_parity_bundle.py` now includes direct pass coverage for live cases 003 and 004.
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-020` + `G-031` row evidence + narrowed next gate text).
5. Validation (all green):
   1. Targeted suites pass:
      - `tests/test_check_projection_surface_parity_bundle.py`
      - `tests/test_ct_scenarios_manifest_hardening.py`
      - `tests/test_check_gap_closure_consistency.py`
      - `tests/test_check_gap_closure_rubric.py`
      - `tests/test_run_wave_a_conformance_bundle.py::test_run_wave_a_conformance_bundle_emits_reports`
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=90 failures=0`
      - `[sync-conformance-matrix] mapped=90/90 passing=90 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=31`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-26, `G-027` reconnect/coalescing family-C/D promotion)

1. Expanded projection reconnect/coalescing live runtime evidence depth with two additional stress families:
   1. `docs/conformance/projection_runtime_evidence/reconnect_multi_gap_reorder_live_case_003.json`
   2. `docs/conformance/projection_runtime_evidence/reconnect_multi_gap_reorder_live_case_004.json`
2. Added two new CT projection rows and matrix lanes:
   1. `CT-PROJ-016` (live reconnect/coalescing family-C)
   2. `CT-PROJ-017` (live reconnect/coalescing family-D)
   3. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-027` coverage widened to include families A/B/C/D)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-027` required CT/live CT sets now include `CT-PROJ-016/017`)
3. Expanded projection semantic checker tests for the new live lanes:
   1. `tests/test_check_projection_semantics.py` now includes direct pass coverage for live cases 003 and 004.
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-027` row evidence + narrowed next gate text).
5. Validation (all green):
   1. Targeted suites pass:
      - `tests/test_check_projection_semantics.py`
      - `tests/test_ct_scenarios_manifest_hardening.py`
      - `tests/test_check_gap_closure_consistency.py`
      - `tests/test_check_gap_closure_rubric.py`
      - `tests/test_run_wave_a_conformance_bundle.py::test_run_wave_a_conformance_bundle_emits_reports`
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=92 failures=0`
      - `[sync-conformance-matrix] mapped=92/92 passing=92 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=33`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-26, `G-002` revision-overlay family-D promotion)

1. Expanded projection overlay live runtime evidence depth with one additional family:
   1. `docs/conformance/projection_runtime_evidence/revision_overlay_live_case_004.json`
2. Added one new CT projection row and matrix lane:
   1. `CT-PROJ-018` (live revision-overlay family-D)
   2. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-002` coverage widened to include family-D)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-002` required CT/live CT sets now include `CT-PROJ-018`)
3. Expanded projection overlay checker tests for the new live lane:
   1. `tests/test_check_projection_overlay_semantics.py` now includes direct pass coverage for live case 004.
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-002` row evidence + narrowed next gate text).
5. Validation (all green):
   1. Targeted suites pass:
      - `tests/test_check_projection_overlay_semantics.py`
      - `tests/test_check_projection_semantics.py`
      - `tests/test_ct_scenarios_manifest_hardening.py`
      - `tests/test_check_gap_closure_consistency.py`
      - `tests/test_check_gap_closure_rubric.py`
      - `tests/test_run_wave_a_conformance_bundle.py::test_run_wave_a_conformance_bundle_emits_reports`
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=93 failures=0`
      - `[sync-conformance-matrix] mapped=93/93 passing=93 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=34`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-26, `G-032` operator diagnostics family-E promotion)

1. Expanded protocol operator diagnostics live runtime evidence depth with one additional family:
   1. `docs/conformance/protocol_runtime_evidence/operator_diagnostics_live_case_005.json`
2. Added one new CT protocol row and matrix lane:
   1. `CT-PROTO-016` (operator diagnostics family-E)
   2. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-032` coverage widened to include family-E)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-032` required CT/live CT sets now include `CT-PROTO-016`)
3. Expanded protocol semantic checker tests for the new live lane:
   1. `tests/test_check_protocol_semantics.py` now includes direct pass coverage for live case 005.
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-032` row evidence + narrowed next gate text).
5. Validation (all green):
   1. Targeted suites pass:
      - `tests/test_check_protocol_semantics.py`
      - `tests/test_ct_scenarios_manifest_hardening.py`
      - `tests/test_check_gap_closure_consistency.py`
      - `tests/test_check_gap_closure_rubric.py`
      - `tests/test_run_wave_a_conformance_bundle.py::test_run_wave_a_conformance_bundle_emits_reports`
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=94 failures=0`
      - `[sync-conformance-matrix] mapped=94/94 passing=94 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=35`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-26, `G-003` tool runtime family-E promotion)

1. Expanded tool runtime live evidence depth with one additional family:
   1. `docs/conformance/tool_runtime_evidence/runtime_error_taxonomy_live_case_005.json`
   2. `docs/conformance/tool_runtime_evidence/runtime_correction_loop_live_case_005.json`
2. Added two new CT tool rows and matrix lanes:
   1. `CT-TOOL-014` (runtime rejection taxonomy family-E)
   2. `CT-TOOL-015` (runtime correction-loop coupling family-E)
   3. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-003` coverage widened to include family-E)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-003` required CT/live CT sets now include `CT-TOOL-014/015`)
3. Expanded tool policy semantic checker tests for the new live lanes:
   1. `tests/test_check_tool_policy_semantics.py` now includes direct pass coverage for:
      - `runtime_error_taxonomy_live_case_005`
      - `runtime_correction_loop_live_case_005`
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-003` row evidence + narrowed next-gate text).

## Latest verification update (2026-02-26, `G-017` extension runtime hook family-D promotion)

1. Expanded extension runtime hook live evidence depth with one additional family:
   1. `docs/conformance/extension_runtime_evidence/runtime_hook_execution_live_case_004.json`
2. Added one new CT extension row and matrix lane:
   1. `CT-EXT-011` (runtime hook execution family-D)
   2. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-017` coverage widened to include family-D)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-017` required CT/live CT sets now include `CT-EXT-011`)
3. Expanded extension semantic checker tests for the new live lane:
   1. `tests/test_check_extension_semantics.py` now includes direct pass coverage for `runtime_hook_execution_live_case_004`.
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-017` row evidence + narrowed next-gate text).

## Latest verification update (2026-02-26, `G-019` lifecycle branch-coverage family-B promotion)

1. Expanded extension lifecycle branch-coverage live evidence depth with one additional family:
   1. `docs/conformance/extension_runtime_evidence/lifecycle_branch_coverage_live_case_002.json`
2. Added one new CT extension row and matrix lane:
   1. `CT-EXT-012` (lifecycle branch-coverage family-B)
   2. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-019` coverage widened to include family-B)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-019` required CT/live CT sets now include `CT-EXT-012`)
3. Expanded extension audit-stream semantic checker tests for the new live lane:
   1. `tests/test_check_extension_audit_stream_semantics.py` now includes direct pass coverage for `lifecycle_branch_coverage_live_case_002`.
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-019` row evidence + narrowed next-gate text).

## Latest verification update (2026-02-26, `G-018` session conflict diagnostics family-A live promotion)

1. Added session conflict/safe-reload live runtime evidence lane:
   1. `docs/conformance/session_runtime_evidence/task_scope_isolation_live_case_001.json`
2. Added one new CT session row and matrix lane:
   1. `CT-SESSION-007` (task-scope isolation live family-A)
   2. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-018` coverage widened to include live family-A)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-018` required CT/live CT sets now include `CT-SESSION-007`)
3. Expanded session semantic checker tests for the new live lane:
   1. `tests/test_check_session_semantics.py` now includes direct pass coverage for `task_scope_isolation_live_case_001`.
4. Updated competitor delta ledger for this tranche:
   1. `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv` (`G-018` row evidence + narrowed next-gate text).

## Latest verification update (2026-02-26, `G-020/G-031` projection-surface family-E promotion)

1. Expanded projection-surface live runtime evidence depth with one additional family:
   1. `docs/conformance/projection_surface_runtime_evidence/render_input_destroy_live_case_005/`
2. Added one new CT projection row and matrix lane:
   1. `CT-PROJ-019` (projection surface parity bundle family-E)
   2. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-020` and `G-031` widened to include family-E)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-020` and `G-031` required CT/live CT sets now include `CT-PROJ-019`)
3. Expanded projection surface parity bundle tests for the new live lane:
   1. `tests/test_check_projection_surface_parity_bundle.py` now includes direct pass coverage for `render_input_destroy_live_case_005`.

## Latest verification update (2026-02-26, `G-027` reconnect/coalescing family-E promotion)

1. Promoted projection reconnect/coalescing live runtime evidence to family-E closure depth:
   1. `docs/conformance/projection_runtime_evidence/reconnect_multi_gap_reorder_live_case_005.json`
2. Added one new CT projection row and matrix lane:
   1. `CT-PROJ-020` (reconnect/coalescing family-E)
   2. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-027` coverage widened to include family-E)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-027` required CT/live CT sets now include `CT-PROJ-020`)
3. Expanded projection reconnect semantic checker tests for the promoted live lane:
   1. `tests/test_check_projection_semantics.py` includes direct pass coverage for `reconnect_multi_gap_reorder_live_case_005`.
4. Validation (all green):
   1. Targeted suite pass:
      - `pytest -q tests/test_check_projection_semantics.py` (`15 passed`)
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=101 failures=0`
      - `[sync-conformance-matrix] mapped=101/101 passing=101 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=42`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-26, `G-002` revision-overlay family-E promotion)

1. Expanded projection revision-overlay live runtime evidence with one additional family:
   1. `docs/conformance/projection_runtime_evidence/revision_overlay_live_case_005.json`
2. Added one new CT projection row and matrix lane:
   1. `CT-PROJ-021` (revision-overlay family-E)
   2. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-002` coverage widened to include family-E)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-002` required CT/live CT sets now include `CT-PROJ-021`)
3. Expanded projection overlay semantic checker tests for the promoted live lane:
   1. `tests/test_check_projection_overlay_semantics.py` now includes direct pass coverage for `revision_overlay_live_case_005`.
4. Validation (all green):
   1. Targeted suite pass:
      - `pytest -q tests/test_check_projection_overlay_semantics.py` (`6 passed`)
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=102 failures=0`
      - `[sync-conformance-matrix] mapped=102/102 passing=102 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=43`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-26, `G-018` session conflict/safe-reload family-B promotion)

1. Expanded session conflict/safe-reload live runtime evidence with one additional family:
   1. `docs/conformance/session_runtime_evidence/task_scope_isolation_live_case_002.json`
2. Added one new CT session row and matrix lane:
   1. `CT-SESSION-008` (task-scope isolation family-B)
   2. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-018` coverage widened to include family-B)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-018` required CT/live CT sets now include `CT-SESSION-008`)
3. Expanded session semantic checker tests for the promoted live lane:
   1. `tests/test_check_session_semantics.py` now includes direct pass coverage for `task_scope_isolation_live_case_002`.
4. Validation (all green):
   1. Targeted suite pass:
      - `pytest -q tests/test_check_session_semantics.py` (`11 passed`)
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=103 failures=0`
      - `[sync-conformance-matrix] mapped=103/103 passing=103 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=44`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-26, `G-017` extension runtime-hook family-E promotion)

1. Expanded extension runtime-hook live runtime evidence with one additional family:
   1. `docs/conformance/extension_runtime_evidence/runtime_hook_execution_live_case_005.json`
2. Added one new CT extension row and matrix lane:
   1. `CT-EXT-013` (runtime-hook execution family-E)
   2. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-017` coverage widened to include family-E)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-017` required CT/live CT sets now include `CT-EXT-013`)
3. Expanded extension semantic checker tests for the promoted live lane:
   1. `tests/test_check_extension_semantics.py` now includes direct pass coverage for `runtime_hook_execution_live_case_005`.
4. Validation (all green):
   1. Targeted suite pass:
      - `pytest -q tests/test_check_extension_semantics.py` (`10 passed`)
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=104 failures=0`
      - `[sync-conformance-matrix] mapped=104/104 passing=104 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=45`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-26, `G-019` lifecycle branch-coverage family-C promotion)

1. Expanded extension lifecycle branch-coverage live runtime evidence with one additional family:
   1. `docs/conformance/extension_runtime_evidence/lifecycle_branch_coverage_live_case_003.json`
2. Added one new CT extension row and matrix lane:
   1. `CT-EXT-014` (lifecycle branch-coverage family-C)
   2. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-019` coverage widened to include family-C)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-019` required CT/live CT sets now include `CT-EXT-014`)
3. Expanded extension audit semantic checker tests for the promoted live lane:
   1. `tests/test_check_extension_audit_stream_semantics.py` now includes direct pass coverage for `lifecycle_branch_coverage_live_case_003`.
4. Validation (all green):
   1. Targeted suite pass:
      - `pytest -q tests/test_check_extension_audit_stream_semantics.py` (`8 passed`)
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=105 failures=0`
      - `[sync-conformance-matrix] mapped=105/105 passing=105 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=46`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-26, `G-022` provider retry-after normalization live lane promotion)

1. Added provider retry-after normalization live runtime evidence lane:
   1. `docs/conformance/provider_runtime_evidence/retry_after_normalization_live_case_001.json`
2. Added one new CT provider row and matrix lane:
   1. `CT-PROVIDER-006` (retry-after normalization live family-A)
   2. Updated:
      - `docs/conformance/ct_scenarios_v1.json`
      - `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.{csv,md}` (`G-022` coverage widened to include live family-A lane)
      - `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json` (`G-022` now requires live evidence and includes `CT-PROVIDER-006` in required live CT IDs)
3. Expanded provider semantic checker tests for the new live lane:
   1. `tests/test_check_provider_semantics.py` now includes direct pass coverage for `retry_after_normalization_live_case_001`.
4. Validation (all green):
   1. Targeted suite pass:
      - `pytest -q tests/test_check_provider_semantics.py` (`12 passed`)
   2. Full refresh bundle:
      - `[ct-scenarios] status=pass scenarios=106 failures=0`
      - `[sync-conformance-matrix] mapped=106/106 passing=106 failing=0 planned=0`
      - `[live-evidence-pack] pass entries=47`
      - `[gap-progress-report] pass overall_percent_complete=64.04`
      - `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-26, closure-hygiene sync and rubric-driven gap promotion)

1. Performed deterministic closure hygiene from validated artifacts only:
   1. Synced source conformance matrix status directly from scenario rows:
      - `python scripts/sync_conformance_matrix_status.py --matrix-csv docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv --rows-json artifacts/conformance/ct_scenarios_rows_v1.json --out-csv docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
   2. Promoted rubric-satisfied gap rows to `covered` and cleared `remaining_closure_delta` where applicable:
      - `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.csv`
2. Corrected bookkeeping-only gap rows to remain `partial` until explicit CT-backed closure criteria are added:
   1. `G-029` retained as `partial`
   2. `G-030` retained as `partial`
3. Closure gate validation (all green):
   1. `scripts/check_gap_status_vocabulary.py`
   2. `scripts/check_gap_closure_rubric.py`
   3. `scripts/check_gap_closure_consistency.py`
   4. `scripts/generate_gap_progress_report.py`
4. Full refresh bundle (all green):
   1. `[ct-scenarios] status=pass scenarios=106 failures=0`
   2. `[sync-conformance-matrix] mapped=106/106 passing=106 failing=0 planned=0`
   3. `[live-evidence-pack] pass entries=47`
   4. `[gap-closure-consistency] pass`
   5. `[gap-progress-report] pass overall_percent_complete=95.11`
   6. `[validate-conformance-artifacts] pass`

## Latest verification update (2026-02-26, `G-019` closure promotion + explicit CT coverage normalization)

1. Normalized `G-019` CT coverage field to explicit parseable IDs:
   1. `CT-EXT-005;CT-EXT-006;CT-EXT-012;CT-EXT-014`
2. Promoted `G-019` from `partial` to `covered` after confirming:
   1. Required CT rows are passing in the source matrix.
   2. Required live CT rows (`CT-EXT-006/012/014`) map to live runtime evidence sources.
3. Validation (all green):
   1. `[gap-closure-consistency] pass`
   2. `[gap-progress-report] pass overall_percent_complete=96.28`
4. Remaining non-covered gaps after this promotion:
   1. `G-029` (`partial`)
   2. `G-030` (`partial`)
   3. `G-032` (`partial`)

## Latest verification update (2026-02-26, `G-032` closure promotion)

1. Normalized `G-032` CT coverage field to explicit parseable protocol IDs:
   1. `CT-PROTO-005;CT-PROTO-006;CT-PROTO-007;CT-PROTO-010;CT-PROTO-011;CT-PROTO-014;CT-PROTO-015;CT-PROTO-016`
2. Promoted `G-032` from `partial` to `covered` after confirming:
   1. Required live protocol diagnostics lanes (`CT-PROTO-010/011/014/015/016`) are mapped to live runtime evidence and passing.
   2. Gap-closure consistency remains green against the current rubric and scenario matrix.
3. Validation (all green):
   1. `[gap-closure-consistency] pass`
   2. `[gap-progress-report] pass overall_percent_complete=97.42`
4. Remaining non-covered gaps after this promotion:
   1. `G-029` (`partial`)
   2. `G-030` (`partial`)

## Latest verification update (2026-02-26, `G-029` closure promotion)

1. Normalized `G-029` rubric and gap-map coverage to explicit CT IDs:
   1. `CT-REL-001;CT-REL-003;CT-REL-004`
2. Promoted `G-029` from `partial` to `covered` after confirming:
   1. Required CT rows are passing in the source matrix.
   2. Gap-closure rubric and consistency checks pass without exceptions.
3. Validation (all green):
   1. `[gap-closure-rubric] pass`
   2. `[gap-closure-consistency] pass`
   3. `[gap-progress-report] pass overall_percent_complete=98.72`
4. Remaining non-covered gap:
   1. `G-030` (`partial`)

## Latest verification update (2026-02-26, `G-030` closure promotion via executable docs/sample-ladder lane)

1. Added a new reliability docs semantic checker lane:
   1. Script: `scripts/check_docs_sample_ladder_semantics.py`
   2. Fixtures:
      - `tests/fixtures/conformance_v1/reliability_lanes/docs_sample_ladder_semantics_ok.json`
      - `tests/fixtures/conformance_v1/reliability_lanes/docs_sample_ladder_semantics_bad.json`
2. Added one new CT reliability row and scenario:
   1. `CT-REL-005` in `docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv`
   2. `CT-REL-005` scenario in `docs/conformance/ct_scenarios_v1.json`
   3. Manifest hardening tests updated to pin command shape for this lane.
3. Promoted `G-030` to covered with explicit rubric mapping:
   1. `docs/conformance/GAP_TO_CT_COVERAGE_MAP_V1.csv`: `G-030 -> covered`, `ct_coverage=CT-REL-005`
   2. `docs/conformance/GAP_CLOSURE_RUBRIC_V1.json`: `G-030 required_ct_ids=CT-REL-005`
4. Validation (all green):
   1. `pytest -q tests/test_check_reliability_mapping_semantics.py` (`6 passed`)
   2. `pytest -q tests/test_ct_scenarios_manifest_hardening.py` (`3 passed`)
   3. `scripts/run_wave_a_conformance_bundle.sh artifacts/conformance`
      - `[ct-scenarios] status=pass scenarios=107 failures=0`
      - `[sync-conformance-matrix] mapped=107/107 passing=107 failing=0 planned=0`
      - `[gap-closure-consistency] pass`
      - `[gap-progress-report] pass overall_percent_complete=100.0`
      - `[validate-conformance-artifacts] pass`
5. Final closure state:
   1. Remaining non-covered gaps: none
