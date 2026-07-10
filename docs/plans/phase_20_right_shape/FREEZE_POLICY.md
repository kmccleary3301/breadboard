# Phase 20 Surface Freeze

Effective from the commit introducing this file until the Phase 20 completion criteria pass
(BB_RS_MASTER_PLAN.md §6), the following surfaces are FROZEN:
- New schema families under contracts/kernel/schemas/ — except bb.e4.lane_manifest.v1,
  bb.e4.lane_lock.v1, bb.contract_tiers.v1 (the three Phase 20 schemas). Constraint tightening of
  existing schemas is permitted when a plan packet requires it; each occurrence is named in that
  packet's ledger-item evidence with the packet id, covered by a red-gate test, and reviewed by the
  packet's verifier (AM10; the central freeze script enforces semantic-ID additions only).
- New SDK packages (sdk/*/package.json count is fixed).
- New lane kinds and new lanes under config/e4_lanes/ (the flagship demo lane is allowlisted).
- New ledgers, scorecards, campaign evidence snapshot forms (BB_RS_PROGRESS.json is the only
  campaign scoreboard).
Unfreeze requires ONE of: (a) a failing flagship-demo or consumer case that names the missing
surface; (b) a demonstrated correctness defect fixable only by a new surface; (c) explicit user
sign-off recorded in docs/plans/phase_20_right_shape/SPEC_AMENDMENTS.md. Every unfreeze is an
amendment entry: what, why, evidence, scope.
Enforcement: scripts/check_phase20_freeze.py runs in the product-spine CI job and fails the build
on violations. The baseline SHA is recorded in the script.
