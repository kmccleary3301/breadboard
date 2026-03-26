# Integration Ledger — 2026-03-26

Date: 2026-03-26
Status: current upstream merge-state inventory
Repo head: `main` at `059d3a2`

## Purpose

This ledger records the current branch-integration state after:

- DARWIN Stage 5 closeout on `main`
- ATP Hilbert runner integration on `main`
- clean integration of the current concurrent workstreams on `main`

The goal is to make the remaining merge work explicit and keep future integration disciplined.

## Major effort groups

### 1. DARWIN program

Status: fully merged on `main`

Branches:
- `origin/codex/darwin-phase1-20260313`
- `origin/codex/darwin-phase2-tranche1-plan-20260314`
- `origin/codex/darwin-stage3-plan-20260319`
- `origin/codex/darwin-stage4-plan-20260319`
- `origin/codex/darwin-stage5-plan-20260320`

Read:
- all DARWIN phase/stage branches are fully merged
- no DARWIN integration work remains for this merge pass

### 2. ATP program

Status: fully merged on `main`

Branches:
- `origin/codex/atp-followup-20260310`
- `origin/codex/bb-atp-hilbert-runner-20260308`
- `origin/codex/atp-port-20260310`
- integration helper: `origin/codex/atp-integration-20260326`

Read:
- ATP Phase 1 closeout was already on `main`
- ATP Hilbert runner/config/builder/test surfaces were merged cleanly on 2026-03-26
- `atp-port` ancestry is now integrated through `codex/atp-integration-20260326`
- there is no remaining ATP merge debt from these branches

### 3. C-Trees effort

Status: fully merged on `main`

Branches:
- `origin/codex/ctrees-merge-prep-20260313`
- `origin/codex/ctrees-helper-rehydration-20260313`
- `origin/codex/ctrees-main-merge-20260326`
- `origin/codex/integration-main-20260326`

Read:
- the helper rehydration tranche is now merged
- the ctrees integration branch family is fully upstream
- there is no remaining C-Trees merge debt from these branches

### 4. Terminal / TUI / parity effort family

Status: mixed

Fully merged:
- `origin/codex/phase14-terminals-20260310`
- `origin/phase5/tmux-e2e-harness-trust-main`
- `origin/tui/todo-primitives-closeout`
- `origin/engine/provider-auth-import-ir-reapply`

Not cleanly mergeable as-is:
- `origin/phase12_restore`
- `origin/chore/tmux-poller-run-id`
- `origin/chore/tui-composer-spacing-fix`
- `origin/ci-tui-goldens-gates-clean`
- `origin/ci-tui-goldens-gates-restore`
- `origin/d-lane-finish`

Read:
- several TUI/tmux branches now sit on an old unrelated or rebased lineage with no clean merge-base against current `main`
- they should not be merged directly
- they require an archaeology/cherry-pick integration pass, not a normal branch merge

## Other branch families

### Webapp

Branch:
- `origin/bb-webapp-v1-p0`

Status:
- diverged from current `main`
- likely its own independent integration track

### Lean / older ATP substrate

Branch:
- `origin/codex/atp-lean-ship-20260307`

Status:
- diverged
- older branch with broad ATP/Lean scaffolding
- should be reviewed as a selective salvage candidate, not merged directly

### Provider auth WIP

Branch:
- `origin/wip/unrelated-provider-auth-policies-limits`

Status:
- diverged
- clearly labeled WIP
- not suitable for direct merge

## Branch classification

### Fully merged

- `origin/codex/darwin-phase1-20260313`
- `origin/codex/darwin-phase2-tranche1-plan-20260314`
- `origin/codex/darwin-stage3-plan-20260319`
- `origin/codex/darwin-stage4-plan-20260319`
- `origin/codex/darwin-stage5-plan-20260320`
- `origin/codex/atp-followup-20260310`
- `origin/codex/bb-atp-hilbert-runner-20260308`
- `origin/codex/atp-port-20260310`
- `origin/codex/atp-integration-20260326`
- `origin/codex/ctrees-merge-prep-20260313`
- `origin/codex/ctrees-helper-rehydration-20260313`
- `origin/codex/ctrees-main-merge-20260326`
- `origin/codex/phase14-terminals-20260310`
- `origin/codex/integration-main-20260326`
- `origin/phase5/tmux-e2e-harness-trust-main`
- `origin/tui/todo-primitives-closeout`
- `origin/engine/provider-auth-import-ir-reapply`

### Real remaining clean merge candidates

None identified from the current major branch set.

The remaining major branches are all divergent and should be treated as selective-salvage projects rather than direct merges.

### Diverged branches that need selective integration, not direct merge

- `origin/phase12_restore`
- `origin/chore/tmux-poller-run-id`
- `origin/chore/tui-composer-spacing-fix`
- `origin/ci-tui-goldens-gates-clean`
- `origin/ci-tui-goldens-gates-restore`
- `origin/d-lane-finish`
- `origin/bb-webapp-v1-p0`
- `origin/codex/atp-lean-ship-20260307`
- `origin/wip/unrelated-provider-auth-policies-limits`

## Merge notes from the ATP integration

- One real conflict was encountered:
  - `docs/contracts/benchmarks/README.md`
- Resolution was additive:
  - kept ATP tranche-selection / runbook references
  - kept Hilbert comparator runner and calibration guidance
- Focused ATP verification passed on `main` after merge:
  - `17 passed`

## Recommended next integration order

1. terminal / TUI archaeology pass
   - build a salvage ledger first
   - identify what is already subsumed
   - cherry-pick only the still-valuable deltas

2. webapp branch as a separate integration project
   - do not mix it into C-Trees or TUI salvage work

3. older ATP / Lean and provider-auth WIP only after explicit prioritization

## Integration rule going forward

- clean merge when there is a valid merge base and the branch is still current
- selective salvage when the branch is diverged or lineage-broken
- never merge no-merge-base branches directly into `main`
