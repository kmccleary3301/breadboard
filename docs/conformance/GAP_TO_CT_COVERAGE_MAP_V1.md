# Gap to CT Coverage Map (V1)

Date: 2026-02-24

Purpose:

1. Map each quantified competitor gap (`G-*`) to executable CT rows and semantic checker artifacts.
2. Make closure criteria auditable with explicit remaining deltas.
3. Provide one canonical bridge between:
   - `docs_tmp/COMPETITORS/BREADBOARD_COMPETITOR_DELTAS_V1.csv`
   - `docs/conformance/ct_scenarios_v1.json`
   - `scripts/check_*.py|.mjs` semantic/runtime gates.

## Status Legend

1. `covered`: direct CT/checker coverage exists and runs in Wave A bundle.
2. `partial`: some CT/checker coverage exists but runtime surface or strictness is incomplete.
3. `open`: no sufficient direct CT/checker proof yet.

## Map

| Gap | Current Status | CT Coverage | Primary Checker/Artifact | Remaining Closure Delta |
|---|---|---|---|---|
| G-001 | partial | CT-PROTO-001/002/003/004, CT-PROJ-001/002/004 | `check_protocol_semantics.py`, `check_projection_semantics.py` | Complete one projection substrate across all surfaces with no semantic forks |
| G-002 | partial | CT-PROJ-003/012/013/018/021 | `check_projection_overlay_semantics.py` (revision-overlay semantic checks across live families A/B/C/D/E) | Promote curated revision-overlay live lanes into captured runtime cache/redraw pressure variants across all active client surfaces |
| G-003 | partial | CT-TOOL-001..015 | `check_tool_transition_semantics.py`, `check_tool_policy_semantics.py` (`runtime_transition_matrix` + `runtime_error_taxonomy` + `runtime_correction_loop_coupling`) | Promote live taxonomy/correction traces beyond family-E into broader provider-coupled interference and higher-concurrency mixed-operation runtime lanes |
| G-004 | partial | CT-TOOL-001/003/004/005 | `bb.tool_call/result/transition` schemas + tool checker lanes | Add richer lineage/resource fields + compat policy coverage |
| G-005 | covered | CT-APPROVAL-001..005 | `check_approval_semantics.py` |  |
| G-006 | partial | CT-SESSION-001..005 | `check_session_semantics.py`, `check_replay_placeholder_semantics.py` | Finalize canonical envelope and portability round-trip gates |
| G-007 | partial | CT-SESSION-005 | `check_rollback_semantics.py` | Add export-side provenance and migration constraints |
| G-008 | partial | CT-REL-001..004 + full `ct_scenarios_v1` | `run_conformance_matrix.py`, `run_ct_scenarios.py` | Expand provider adapter package depth and closure proofs |
| G-009 | partial | CT-REL-002 | `check_replay_determinism_gate.py` | Expand beyond current 6-file non-fixture / 8-file total (>=15 turns) live replay floor into broader non-trivial scenario classes and release-branch policy hardening |
| G-010 | partial | CT-REL-001/003/004 | `check_reliability_lanes.py`, `check_phase5_reliability_flake_budget.py`, reliability policy JSON | Promote fixture-backed lane/trend budgets into live-history trend budgets and branch-protection enforcement |
| G-011 | covered | CT-PROVIDER-001/002/003/004/005 | `check_provider_semantics.py` + runtime `provider_resolution` emission in main/fallback/subcall/batch-subcall paths |  |
| G-012 | partial | CT-PROVIDER-002 | auth descriptor schema + provider checker lanes | Complete import/export + fallback semantics tests |
| G-013 | partial | CT-PLAN-001 | `check_plan_envelope_semantics.py` | Canonicalize envelope emission across all decision paths |
| G-014 | partial | CT-SESSION-001/002/004 + portability fixtures | session/parity fixtures and replay lanes | Land full Codex/Claude/OpenCode round-trip hash tests |
| G-015 | partial | CT-EXT-001/002/003/004 | `check_extension_semantics.py`, `check_plugin_discovery_semantics.py`, `check_surface_manifest_semantics.py` | Runtime-enforced precedence + conflict policy in all install paths |
| G-016 | covered | CT-APPROVAL-003/004, CT-EXT-007 | `check_approval_semantics.py`, `check_extension_audit_stream_semantics.py` |  |
| G-017 | partial | CT-EXT-002/003/008/009/010/011/013 | `check_extension_semantics.py` (`dependency_order` + `runtime_hook_execution` across live families A/B/C/D/E) | Promote stress/failure live hook families beyond family-E into broader provider-coupled interference and higher-cardinality multi-plugin contention runtime lanes |
| G-018 | partial | CT-SESSION-004/006/007/008 | `session_conflict_runtime.py`, `check_session_semantics.py` (`task_scope_isolation` across live families A/B) | Extend conflict diagnostics live lanes beyond family-B to enforce richer runtime taxonomy across all restore/resume entrypoints |
| G-019 | partial | CT-EXT-005/006/012/014 | `check_extension_audit_stream_semantics.py` (`lifecycle_audit_stream` + `lifecycle_branch_coverage` with install/update/uninstall/install_retry/update_retry/reinstall/conflict/reinstall_failure across live families A/B/C) | Expand beyond current captured lifecycle branch-coverage families into broader runtime conflict/failure trace packs with CI policy enforcement depth |
| G-020 | partial | CT-PROJ-007/010/011/014/015/019 | projection parity bundle + strict surface-manifest checks + live projection-surface runtime stress bundles (families A/B/C/D/E) | Promote curated live render/input/destroy stress lanes beyond family-E into captured provider-coupled interference and multi-surface redraw pressure variants |
| G-021 | partial | CT-RECURSE-006 | `check_longrun_semantics.py --mode queue_ordering_visibility` | Unify fallback queue kernel and visibility controls across queue backends and resume paths |
| G-022 | partial | CT-PROVIDER-003/006 | `check_provider_semantics.py` + `parse_rate_limit_headers` capped retry-after policy + explicit reset-ms key support + `retry-after-ms` precedence + `x-ratelimit-retry-after` alias + `x-ratelimit-retry-after-ms` fallback + HTTP-date/asctime/ISO-8601 edge fixtures + live header-drift lane | Expand normalization assertions to additional live reconnect-storm traces and broader provider-specific retry header drift variants |
| G-023 | partial | CT-PROTO-006/008/009/012/013 + CT-PROJ-001/004/006 + CT-SESSION-004/006 | `check_protocol_semantics.py --mode session_stream_isolation` + required reconnect/fork event types + required event-type count bounds + single-session/single-stream max bounds + cursor-step storm bound checks + session/pty runtime checks | Promote family-C/D live reconnect-storm/fork-race lanes into provider-coupled PTY interference and resume-race variants |
| G-024 | partial | CT-RECURSE-001..006, CT-RECURSE-008 | `check_longrun_semantics.py` + `policy_boundary_matrix` semantic checker | Extend policy-boundary matrix to live provider-coupled fault traces and fallback policy permutations |
| G-025 | partial | CT-RECURSE-001..004, CT-RECURSE-007, CT-RECURSE-008, CT-RECURSE-010, CT-RECURSE-011, CT-RECURSE-012 | recursion checker lanes + `global_token_budget_stop` + `global_subcall_budget_stop` + `subcall_budget_resume_precheck` + `policy_boundary_matrix` (provider-error branch) + `provider_coupled_budget_trace` semantic checkers | Promote fixture-backed provider-coupled traces into live provider-coupled delegation traces |
| G-026 | partial | CT-RECURSE-006, CT-RECURSE-009, CT-RECURSE-013, CT-RECURSE-014, CT-RECURSE-015, CT-RECURSE-016 | long-run/delegation lanes + queue_ordering_visibility + queue_pause_resume_durability + queue_multi_resume_durability + queue_multi_resume_persisted_backend + queue_backend_trace_coherence semantic checkers (generated + external trace bundles) | Runtime `FeatureFileQueue` integrated plus artifact-sidecar trace coherence checks; next promote these lanes to captured live-provider trace bundles |
| G-027 | partial | CT-PROJ-001/004/006/008/009/016/017/020 | `check_projection_semantics.py` (`reconnect_gap_coalescing` now enforces batch-size/gap-distance bounds plus multi-gap controlled-reorder semantics across live families A/B/C/D/E) | Promote curated live reconnect-storm lanes into captured provider-coupled stream-interference and cross-surface redraw pressure variants |
| G-028 | partial | CT-RECURSE-004, CT-RECURSE-005 | long-run rollback/bounded recovery lanes + convergence_stage_bounded semantic checker | Extend convergence boundedness checks into multi-signal convergence and fallback-stage paths |
| G-029 | covered | CT-REL-001/003/004 | schema package + validators |  |
| G-030 | covered | CT-REL-005 | `scripts/check_docs_sample_ladder_semantics.py` |  |
| G-031 | partial | projection parity bundle + strict manifest constraints + contract-version coherence checks + CT-PROJ-005 + CT-PROJ-007 + CT-PROJ-010 + CT-PROJ-011 + CT-PROJ-014 + CT-PROJ-015 + CT-PROJ-019 | `check_projection_surface_parity_bundle.py` + projection surface manifest + surface parity reporters + `check_event_envelope_snapshots.py` | Promote curated live render/input/destroy stress lanes beyond family-E into captured provider-coupled stream-interference and cross-interface redraw pressure variants |
| G-032 | covered | CT-PROTO-005/006/007/010/011/014/015/016 + protocol checker lanes | `check_protocol_semantics.py --mode status_error_taxonomy/session_stream_isolation/operator_diagnostics_pack` + runtime/checker artifacts |  |

## Immediate Prioritized Closures (Unblocked)

1. `G-003`: runtime transition graph enforcement extension + taxonomy events.
2. `G-031`: projection substrate unification and parity expansion.
3. `G-019`: lifecycle/policy audit stream normalization.
4. `G-022`: retry-after normalization across all provider runtimes.

## Blocked Closure

1. `G-014`/ATP parity lane fully clean requires replacing the temporary Anthropics-limited placeholder summary in:
   - `goldens/plan_todo_stress/claude_haiku45/current/meta/run_summary.json`
   - blocked until Anthropic quota reset window.
