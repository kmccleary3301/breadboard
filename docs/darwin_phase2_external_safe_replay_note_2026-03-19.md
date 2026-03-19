# DARWIN External-Safe Replay Sufficiency Note

Date: 2026-03-19
Status: bounded replay note for the external-safe evidence tranche
Primary issue: `breadboard_repo_darwin_phase1_20260313-ovi`

## Replay obligations in scope

- `lane.repo_swe`: replay-stable representative rejected candidate plus explicit invalid-comparison exclusion
- `lane.scheduling`: replay-stable promoted hybrid candidate
- bounded harness→research transfer: replay-stable transferred prompt-family candidate
- `lane.atp`: audit-only, no new replay obligation introduced by this tranche

## Sufficiency read

Replay is sufficient for the current external-safe packet because:

- every included transfer-facing or lineage-facing lane has an explicit replay-conditioned story
- repo_swe invalidity remains disclosed rather than patched over
- no unreplayed improvement claim is being elevated into the external-safe subset

## Boundary

This is not a full new replay program.

If a future tranche wants stronger or broader claims, it must expand replay coverage explicitly rather than silently leaning on this note.
