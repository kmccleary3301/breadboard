# ACR-20260924-hermes-raw-trace-roots

- `acr_id`: `ACR-20260924-hermes-raw-trace-roots`
- `title`: Preserve raw Hermes tool-call evidence and compare only declared roots
- `author`: BreadBoard E4 campaign
- `date`: 2026-09-24
- `status`: implemented

## 1) Problem Statement

The Hermes conductor decoded `raw_response_b64` from `sample` before the worker had returned it from `provider_response`. It then constructed replay tool calls only from prepared actions, losing original names and the invalid `NOT_A_TOOL` call in H-02. The comparator inferred the supplier workspace from the packet filesystem path even though the capture's source system prompt declared `/opt/hermes/case/workspace`; structured supplier paths therefore stayed absolute while BreadBoard paths normalized.

## 2) Scope and Surfaces

- Kernel module touched: `breadboard/rl/harness/runners/conductor.py` records raw calls from the worker's completed provider response; prepared actions alone retain execution authority.
- Comparator touched: `conformance/comparators/hermes_agent.py` requires consistent workspace declarations and retains each raw supplier tool-call sample, including native-deduplicated samples.
- Contract surface: Hermes `replay_trace.tool_calls` and comparator workspace normalization. No request bytes, tool dispatch protocol, profile selection or prompt text is rewritten.
- Is this a **kernel danger-zone** change? `yes`.

## 3) Coupling and Generalization Impact

- Danger-zone: yes. The conductor change is inside the shared native-stream loop, but only records response evidence after `provider_response`; prepared-action validation and execution are unchanged.
- Other profiles: no change to Pi's streaming phase or OpenHands' event-based trace projection. No profile-name branch was introduced.
- The supplier root is declared by pinned Hermes `agent/prompt_builder.py:942-956` and `agent/coding_context.py:519-526`, not guessed from the capture directory or arbitrary path strings. Candidate `runtime.cwd` is required; a contradictory system-prompt root is rejected. Source prompt text remains byte-exact in comparison.

## 4) Change Classification

- Classification: `internal` (trace-fidelity defect and comparator provenance correction).
- Compatibility window: none; a replay trace with omitted raw calls or an undeclared workspace must not pass.
- Required schema/version bumps: none; existing `tool_calls` and `runtime.cwd` fields retain their schema.

## 5) Evidence and Validation Plan

- Required evidence: red/green `tests/rl/harness/test_hermes_conductor_replay_trace.py` covers response decode order, mixed invalid call visibility, duplicate samples and valid-only dispatch. Red/green `tests/e4_parity/test_hermes_agent_comparator.py` covers source-declared roots, conflicting/missing declarations, precise structured-path normalization, prompt mismatches and duplicate raw samples. Logs and SHA256 checksums are in `.tmp/bbe4/hermes-fix-w26-status.md`.
- Required contract checks: `scripts/check_danger_zone_acr.py`, `scripts/check_kernel_contract_pack_v1.py`, `scripts/dev/build_script_index.py --check`, plus the packet's scoped conductor/comparator tests. Thirteen replay-kit input tests remain unavailable in this lane because the existing immutable `docs_tmp/e4_immutable_inputs/hermes` is absent; this ACR does not claim an installed rerun pass.
- Acceptance: raw invalid/duplicate calls remain in order, no invalid action dispatch occurs, and comparison cannot normalize a path using a guessed root.

## 6) Rollout Plan

1. Independently review the exact committed head.
2. Main owns the rebuilt image, installed replay and further promotion decision; this ACR does not authorize push or merge.

## 7) Rollback Plan

- Trigger: a raw trace incorrectly authorizes dispatch or an undeclared root compares as equivalent.
- Rollback: revert the conductor, comparator, related tests, package-contract note and this ACR together. Do not repin the old result as equivalent; it omitted invalid calls and normalized the wrong prefix.
- Verification: rerun the focused red/green tests and all named contract checks.

## 8) Approvals

- Kernel reviewer: independent review required on the exact head.
- Contracts reviewer: independent review required on the exact head.
- Final decision: pending Main's installed evidence and review; no parity claim awarded.
