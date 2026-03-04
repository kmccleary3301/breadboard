# ATP/EvoLake Execution Checklist (V1)

Date: 2026-03-04  
Scope: BreadBoard kernel, ATP extension lane, EvoLake extension lane  
Objective: Convert roadmap intent into weekly executable work with owners, hard gates, and measurable KPIs.

## 1) Ownership Map

Assign one DRI per lane. One person can hold multiple lanes.

| Lane | DRI Role | Backup Role | Core Responsibility |
|---|---|---|---|
| Kernel Generalization | Kernel Lead | Runtime Lead | Preserve harness-agnostic primitives and extension boundaries |
| ATP Runtime | ATP Lead | Infra Lead | ATP REPL reliability, route contracts, replay/state-ref correctness |
| EvoLake Runtime | EvoLake Lead | ATP Lead | Campaign/checkpoint/replay loop correctness and policy guards |
| Benchmarks & Claims | Benchmark Lead | Research Ops | Frozen slices, reruns, drift attribution, claim evidence |
| Spend & Reliability | Ops Lead | Infra Lead | Cost controls, uptime/error budgets, automated gates |
| Governance & Contracts | Governance Lead | Kernel Lead | Schema/version discipline and change approval protocol |

## 2) Weekly Cadence (Hard Gates)

Use this every week. No exceptions without explicit waiver.

### Monday — Freeze + Plan
- [ ] Freeze benchmark slices (`frozen_slices_vN`), seeds, and configs for the week.  
  Owner: Benchmark Lead
- [ ] Freeze current contract schema versions for ATP/EvoLake surfaces.  
  Owner: Governance Lead
- [ ] Publish weekly run matrix (lanes, model configs, budget caps, stop criteria).  
  Owner: Ops Lead

### Tuesday–Wednesday — Execute
- [ ] Run baseline lane (BreadBoard-only) and record metrics artifact pack.  
  Owner: ATP Lead
- [ ] Run EvoLake campaign lane (minimal + hard campaign where applicable).  
  Owner: EvoLake Lead
- [ ] Run comparator lanes (external systems or adapters) on same frozen slices.  
  Owner: Benchmark Lead

### Thursday — Validate
- [ ] Run drift checks (quality, latency, cost, error classes) vs prior baseline.  
  Owner: Benchmark Lead
- [ ] Run contract/governance checks and route-prefix policy checks.  
  Owner: Governance Lead
- [ ] Run reliability gates (flake, timeout, crash, protocol mismatch budgets).  
  Owner: Ops Lead

### Friday — Decide + Promote
- [ ] Publish weekly scorecard and red/yellow/green decision packet.  
  Owner: Research Ops
- [ ] Promote only if all P0 gates pass and no unresolved red flags.  
  Owner: Kernel Lead
- [ ] Queue explicit remediation issues for every failed gate.  
  Owner: Lane DRIs

## 3) KPI Pack (Measurable, Required)

Track all KPIs every week. Store both raw and summary artifacts.

### A. Generalization / Contract KPIs
- `kernel_extension_boundary_violations` = 0
- `request_body_hash_regressions` = 0 on frozen parity cases
- `tool_schema_hash_regressions` = 0 unless version bump approved
- `route_prefix_policy_violations` = 0 (`/atp/*` and `/ext/*` only for extension endpoints)

### B. ATP KPIs
- `atp_success_rate` = solved_or_successful / total_requests
- `atp_state_ref_roundtrip_success` >= 99% on stateful harness tests
- `atp_batch_protocol_mismatch_rate` <= 0.5%
- `atp_error_unclassified_rate` <= 1% (errors lacking mapped diagnostic class)
- `atp_p95_latency_ms` tracked weekly (must not regress > agreed threshold without approval)

### C. EvoLake KPIs
- `campaign_submission_success_rate` >= 99%
- `checkpoint_write_success_rate` = 100%
- `checkpoint_resume_success_rate` >= 99% on controlled resume tests
- `replay_manifest_integrity_pass_rate` = 100%
- `budget_rejection_precision` = 100% on policy test corpus

### D. Reliability KPIs
- `lane_pass_rate_7d` >= 95% for each mandatory lane
- `flake_rate` <= 5% on repeated seeds
- `crash_free_runs` >= 99%
- `protocol_transport_error_rate` <= 1%

### E. Cost / Efficiency KPIs
- `cost_per_run_usd` (per lane; tracked trend)
- `cost_per_success_usd` (ATP/EvoLake)
- `latency_per_dollar` (optional composite)
- `budget_cap_breaches` = 0

## 4) Milestone Checklists

## 4.1 3-Month Milestone (Foundation Lock-In)

- [ ] Extension-gated ATP/EvoLake routes stable and covered by CI gate set.
- [ ] Feature audit endpoint and extension enablement matrix permanently green.
- [ ] Frozen-slice benchmark workflow running weekly with reproducible artifacts.
- [ ] Drift report pipeline generated every week and reviewed.
- [ ] Governance templates/checkers mandatory for contract-surface changes.
- [ ] 7-day reliability window passing for mandatory lanes.

Exit Criteria:
- All Section 3 KPIs reporting automatically.
- No P0 gate failures for two consecutive weekly cycles.

## 4.2 6-Month Milestone (Capability Scale-Up)

- [ ] Multi-lane comparator pipeline (BreadBoard + external comparators) fully automated.
- [ ] EvoLake hard-campaign suite integrated into routine weekly flow.
- [ ] ATP ablation matrix (prompt/tool/policy/config variants) running on frozen slices.
- [ ] Quality-cost-latency frontier dashboard maintained week-over-week.
- [ ] Deterministic replay and checkpoint recovery validated on incident drills.

Exit Criteria:
- Comparator scorecards and claim packets generated without manual patching.
- Documented improvement trend on at least one primary benchmark family.

## 4.3 12-Month Milestone (Substrate Leadership)

- [ ] New harness behavior can be ported through primitives without kernel narrowing.
- [ ] Evidence-backed claims are reproducible from stored manifests/checkpoints.
- [ ] EvoLake campaign promotion policy operates with quarantine/integrity controls.
- [ ] ATP lane demonstrates stable competitive behavior under repeated seeded reruns.

Exit Criteria:
- Mature weekly operating cadence continues with low operational toil.
- Contract/version break incidents are rare, detected early, and cleanly remediated.

## 5) Promotion Rules (Go/No-Go)

A weekly promotion is **GO** only if all are true:
- [ ] No contract/generalization regressions.
- [ ] Reliability KPIs meet thresholds.
- [ ] Budget caps respected.
- [ ] Comparator and baseline artifacts both present and valid.
- [ ] No unresolved P0 incident in the promoted lane.

If any fail: **NO-GO**, open remediation issue(s), rerun after fixes.

## 6) Red-Line Risk Controls

- Kernel narrowing changes require explicit governance review and sign-off.
- Any schema/route contract change requires version discipline and migration note.
- Claims must be attached to reproducible artifacts (no artifact, no claim).
- Spend cap breach triggers automatic lane pause until reviewed.

## 7) Execution Template (Per Run Batch)

Use this checklist per batch execution:
- [ ] Confirm frozen slice/version IDs.
- [ ] Confirm config hash + prompt hash + tool schema hash snapshot.
- [ ] Execute lanes in declared order.
- [ ] Validate artifacts and schema contracts.
- [ ] Compute KPI deltas vs previous baseline.
- [ ] Publish summary + decision + remediation actions.

---

This checklist is intentionally strict. If a gate is too strict or too loose, adjust the threshold explicitly and record rationale in the weekly decision packet.
