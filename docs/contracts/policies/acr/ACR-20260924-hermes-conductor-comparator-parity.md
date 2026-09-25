# ACR-20260924-hermes-conductor-comparator-parity

- `acr_id`: `ACR-20260924-hermes-conductor-comparator-parity`
- `title`: Hermes conductor and comparator parity with oracle rerun3 packet
- `author`: HermesPort, BreadBoard E4 campaign
- `date`: 2026-09-24
- `status`: implemented

## 1) Problem Statement

The Hermes Agent 2026.9.11 native port previously suffered from three parity gaps against the oracle rerun3 supplier packet:
1. Workspace seed: Hermes' supplier capture executes in a fresh non-git directory containing only the seed file `AGENTS.md` (mode 0o644, directory mode 0o700). Hermes replay operators previously fell back to repository-mode sealing.
2. Tool schemas: BreadBoard candidate fixtures and `hermes_tools.py` mutated and stripped the tool schemas down to name-only entries (`{"type": "function", "function": {"name": ...}}`) or modified tool descriptions/parameters, whereas the oracle packet emits full, unmodified upstream tool schemas with complete descriptions and parameter properties.
3. Comparator controls: `conformance/comparators/hermes_agent.py` only projected basic execution mode and streaming flags, omitting `max_tokens`, deadlines (provider 45s, native 35s, watchdog 40s, terminal 30s), and retry/fallback flags.

The measurable outcome is full byte-exact schema and controls parity between BreadBoard's replay emission and the oracle rerun3 capture packet.

## 2) Scope and Surfaces

- Kernel and harness modules touched:
  - `breadboard/rl/harness/hermes_tools.py`
  - `breadboard/rl/harness/runners/conductor.py`
  - `conformance/comparators/hermes_agent.py`
  - `config/e4_targets/hermes_agent/2026.9.11/native-config.json`
  - `config/e4_targets/hermes_agent/2026.9.11/tool-surface.json`
  - `tests/e4_parity/fixtures/hermes_agent/`
- Tests: `tests/e4_parity/test_hermes_agent_comparator.py`, `tests/rl/harness/test_hermes_conductor_replay_trace.py`.
- Contract surfaces touched: conductor replay trace controls and request bodies, comparator canonical trace projection, target tool schemas.
- Kernel danger-zone change? yes

## 3) Coupling and Generalization Impact

- Does this add any core -> extension dependency? no. Changes align Hermes harness and comparator projections with existing contracts.
- Does this narrow cross-harness parity behavior? no. It restores faithful byte-exact alignment with upstream Hermes supplier execution.
- Does this alter default endpoint semantics? no. Non-Hermes profiles and standard routes are unaffected.
- Coupling risk score (`low`) and rationale: changes are strictly confined to the Hermes conductor replay loop, Hermes tool schema definition, and the Hermes comparator.

## 4) Change Classification

- Classification: `additive`
- Compatibility window: existing fixtures and comparator tests pass; replay traces gain complete schemas and control attributes.
- Required schema/version bumps: none; traces conform to `bb.e4.hermes-agent-trace.v1` and comparator report `bb.e4.comparator_report.v1`.

## 5) Evidence and Validation Plan

- Required contract lane tests:
  - `tests/e4_parity/test_hermes_agent_comparator.py` (28 passed) verifying parity comparison across all 6 cases, mutation gates for controls and schemas, and failure on name-only tool schemas.
  - `tests/rl/harness/test_hermes_conductor_replay_trace.py` (3 passed) verifying conductor replay trace matches the committed rerun3 fixture, tool schemas are byte-exact, and name-only schemas are rejected.
- Required replay/parity checks: exact replay traces and parity comparator pass across all 6 rerun3 cases against the packet.

## 6) Rollout Plan

- Rollout phases: merged into `e4/hermes-20260924` and rebased for live replay runs.
- Flags/toggles: none.
- Blast radius constraints: changes affect only Hermes profile components.
- Monitoring hooks: comparator report assertions verify trace equality.

## 7) Rollback Plan

- Trigger conditions: any mismatch in Hermes comparator or replay trace generation.
- Exact rollback commands: revert the commit with `git revert <commit>`.
- Artifact/state restoration steps: restore previous comparator projection and conductor loop fields.
- Post-rollback verification: rerun pytest on comparator and conductor suites.

## 8) Approvals

- Kernel reviewer: Main (campaign orchestrator) under standing approval
- Contracts reviewer: Main (campaign orchestrator)
- Ops reviewer: Main (campaign orchestrator)
- Final decision: Main (campaign orchestrator)
