# ACR-20260901-trusted-process-lifecycle-repair

- `acr_id`: `ACR-20260901-trusted-process-lifecycle-repair`
- `title`: Repair trusted-process admission and verifier cleanup lifecycle
- `author`: Codex (engine stop-and-PR closure)
- `date`: 2026-09-01
- `status`: implemented

## 1) Problem Statement

The clean Linux installed-artifact journey exposed four defects in the trusted-process lifecycle: dash rejected a multi-digit inherited marker descriptor, verifier identity recording was bound twice, production qualification treated the verifier script as the shell bootstrap runtime, and verifier cleanup evidence did not consistently require the sealed snapshot. These defects prevented the installed lifecycle from reaching a successful, evidence-published terminal state.

## 2) Scope and Surfaces

- Trusted process bootstrap admission in `breadboard.rl.harness.sandbox`.
- Production verifier runtime and executable identities in `breadboard.rl.harness.qualification`.
- Verifier cleanup resource validation in the service and evidence repository.
- Linux process integration and deterministic lifecycle fixtures.
- Danger-zone: yes.
- Outside scope: new sandbox capabilities, stronger isolation claims, compatibility fallbacks, provider behavior, or TUI changes.

## 3) Coupling and Generalization Impact

- Core to extension dependency introduced: no.
- Coupling and generalization impact: positive. One process bootstrap mechanism now works independently of inherited descriptor number, and one cleanup resource set is enforced across service and evidence boundaries.
- Generalization risk assessment: low. The stdout marker is consumed before application output is delegated; verifier execution remains bound to the existing pinned-shell contract.
- Coupling risk: medium because admission ordering, executable identity, and cleanup evidence cross sandbox, qualification, service, and repository boundaries.

## 4) Change Classification

- Classification: `internal`.
- Compatibility: no public request, response, or SDK contract changes.
- Runtime behavior: corrective; previously rejected valid Linux process layouts and valid released cleanup receipts now complete.

## 5) Evidence and Validation Plan

- Local focused lifecycle/evidence/composition/process suite must pass.
- Linux sealed-process regressions must cover a descriptor number above 9, verifier execution, cleanup, and the public lifecycle.
- A clean installed wheel must execute inspect, create, run, verify, publish completed and closed envelopes, and release episode cleanup.
- Exact-head independent review, danger-zone guard, and required CI must pass before merge.

## 6) Rollout Plan

1. Merge only after exact-head review and required CI.
2. Rebuild all frozen SDK, Darwin, and Linux artifacts from the resulting merge identity.
3. Repeat the Linux installed journey and official Docker canary against that identity.

No new capability or promotion claim is created by this repair.

## 7) Rollback Plan

- Revert the repair merge as one unit if admission ordering, output integrity, verifier identity, or cleanup publication regresses.
- Invalidate artifacts and handoffs bound to the reverted identity.
- Do not restore the inherited shell descriptor marker or omit snapshot cleanup; fail the lifecycle closed until a replacement repair is reviewed.

## 8) Approvals

- Owner decision: the engine stop-and-PR request authorizes this bounded corrective PR and non-squash merge after evidence gates.
- Independent exact-head correctness review: required.
- Final decision: merge only after required CI passes with zero unresolved review threads.
