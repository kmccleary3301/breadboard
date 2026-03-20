# DARWIN Stage-3 Component Promotion / Bounded Transfer Status

Date: 2026-03-19
Status: tranche executed

## What landed

- component-family candidates derived from bounded real-inference outputs
- bounded replay checks for repo_swe and systems topology families
- first repo_swe component-family promotion decision
- first systems non-promotion decision
- bounded transfer outcomes with one retained transfer and one failed transfer
- failed-transfer taxonomy
- Stage-3 decision ledger update and verification bundle

## Current read

- `lane.repo_swe` promoted the topology family `policy.topology.pev_v0`
- `lane.systems` remained transfer-candidate only and was not promoted
- the first retained bounded transfer is `lane.repo_swe` -> `lane.systems`
- the first failed transfer records `unsupported_lane_scope` on `lane.scheduling`
- `lane.harness` remains the watchdog/control lane for this tranche
- `lane.atp` remains audit-only
