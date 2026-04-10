# DARWIN Stage 6 main verification

Date: `2026-04-09`

Merged-state verification completed on the landed mainline candidate.

Verified branch state:

- verification branch: `stage6-main-verify-20260409`
- merge base with `github/main`: `9379f6f711bd3f91b44e8a3afab3a00265e68bf1`
- merged verification head before final verification updates: `bd033daab4efbffb8e8c4911dd883e895fdcb695`

Verification results:

- final targeted Stage-6 suite passed on the landed-mainline candidate: `38 passed`
- canonical Stage-6 artifacts rebuilt cleanly on the landed-mainline candidate
- docs indexes and DARWIN contract entrypoints remained aligned with the closeout branch
- signoff, completion gate, and canonical artifact freeze remained accurate after merged-state rebuilds

Merged-state notes:

- `lane.systems -> lane.scheduling` remained `retained` and replay-supported
- retained Systems-primary transfer continued to compound positively on the bounded scheduling target
- Repo_SWE challenge transfer reran as `inconclusive` on merged-state rebuild, but it remained bounded challenge context and did not change the retained Systems-primary proving center
- composition remained `composition_not_authorized`

Conclusion:

- merged-state verification passed
- Stage 6 closeout documents now reflect the actual landed-mainline candidate state
