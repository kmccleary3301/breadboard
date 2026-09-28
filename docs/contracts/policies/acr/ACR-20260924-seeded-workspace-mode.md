# ACR-20260924-seeded-workspace-mode

- `acr_id`: `ACR-20260924-seeded-workspace-mode`
- `title`: Seeded non-repository workspace mode for E4 supplier-equivalent replays
- `author`: Main, BreadBoard E4 campaign
- `date`: 2026-09-24
- `status`: implemented

## 1) Problem Statement

Supplier harness captures for the E4 profiles run each case in a fresh non-git directory that holds only declared seed files. Pi, Hermes and OpenClaw seed `AGENTS.md` (plus `SOUL.md` for OpenClaw). OpenHands and OMP start empty. BreadBoard could only materialize a sealed git repository, and its verifier diff required a measured base commit. Replays therefore ran in a different workspace from the supplier's. The Pi replay showed this directly: BreadBoard's own repository `CLAUDE.md` entered the model's system prompt where the supplier had the seed `AGENTS.md`.

The measurable outcome is an explicit, typed workspace mode. In that mode, the policy-visible workspace is byte-exactly the declared seed tree, and the verifier diff is computed against that tree using state outside the workspace. Repository mode must stay unchanged.

## 2) Scope and Surfaces

- Kernel modules touched:
  - `breadboard/rl/harness/headless.py`
  - `breadboard/rl/harness/materialization.py`
  - `breadboard/rl/harness/sandbox.py`
- Tests: `tests/rl/harness/test_headless_runner.py`, `test_materialization.py`, `test_sandbox_process_integration.py`, `test_verifier_snapshot.py`.
- Contract surfaces touched: headless workspace input identity (`workspace_mode`, `workspace_directory_mode`, `workspace_seed_digest`), the typed `WORKSPACE_SEED` CAS manifest, sandbox execution plan mount roles (`workspace_seed`), and the verifier seal/diff for seeded leases.
- Kernel danger-zone change? yes

## 3) Coupling and Generalization Impact

- Does this add any core -> extension dependency? no. Profiles declare seed entries and a directory mode as data; the kernel has no profile-specific branch.
- Does this narrow cross-harness parity behavior? no. It widens admissible workspaces to match supplier conditions, under the same authority rules as repository mode.
- Does this alter default endpoint semantics? no. Repository mode is the default and is byte-identical; seeded mode is never inferred from a missing base commit.
- Coupling risk score (`low`) and rationale: the mode is one typed field threaded through input identity, materialization and verifier seal. Every seeded authority value is recomputed from content (seed manifest identity, root mode, baseline digest) and fails closed.

## 4) Change Classification

- Classification: `additive`
- Compatibility window: repository-mode requests and evidence are unchanged.
- Required schema/version bumps: the new `WORKSPACE_SEED` manifest schema and media type; headless input identity gains seeded-mode fields only when seeded mode is declared.

## 5) Evidence and Validation Plan

- Required contract lane tests: seeded admission (declared directory mode equals the manifest root mode; exact-int permission bits), seed manifest identity recomputation and schema/media enforcement, the seed root mount role, baseline re-verification before diff, empty-seed leases staying file-free, and seeded diff canonical bytes.
- Required replay/parity checks: the Pi six-case replay with seed `AGENTS.md` (sha256 3da3ad1b…, mode 0600, directory mode 0700).
- Required conformance/ablation checks: the broad suite against baseline `9ff9e2f1`; accept only zero new failures.
- Acceptance criteria:
  - independent exact-head review over three rounds. r1 at `d016e7ad` REJECT (4 findings); r2 at `0c12c571` REJECT (1 finding); r3 at `61bf2a85` ACCEPT. Records are in `docs_tmp/wayfinder/e4-campaign-local-production/assets/execution-tracker.md`;
  - the broad suite at `61bf2a85`: 129 failed (all in the baseline set) / 3722 passed, with zero new failures;
  - each review finding carries a regression test that fails before its fix.

## 6) Rollout Plan

- Rollout phases: merge; E4 profile kits then declare seeded workspaces, and replays and live qualification consume them.
- Flags/toggles: none. Seeded mode is selected only by an explicit typed request field.
- Blast radius constraints: repository-mode code paths are unchanged, and native scratch stays lease-private outside the workspace.
- Monitoring hooks: lease proofs and evidence projection bind the workspace mode and seed digest.

## 7) Rollback Plan

- Trigger conditions: any repository-mode regression, a seeded lease whose workspace differs from its declared seed, a verifier diff computed against unverified state, or any new broad-suite failure against `9ff9e2f1`.
- Exact rollback commands: revert the PR merge commit with `git revert -m 1 <pr-merge-commit>` in a new rollback branch. Do not reset protected main.
- Artifact/state restoration steps: E4 kits fall back to not declaring seeded mode, and their replays are marked non-comparable. Retained evidence is not deleted.
- Post-rollback verification: rerun the focused harness suites, the broad-suite baseline comparison, and this danger-zone ACR check on the rollback candidate.

## 8) Approvals

- Kernel reviewer: Main (campaign orchestrator) under Kyle McCleary's standing approval
- Contracts reviewer: independent exact-head review `e4-review-pi-seam-r13-a`, ACCEPT at `61bf2a85`
- Ops reviewer: Main (campaign orchestrator) under Kyle McCleary's standing approval
- Final decision: Main (campaign orchestrator) under Kyle McCleary's standing approval
