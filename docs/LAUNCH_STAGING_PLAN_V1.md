# Launch Staging Plan (v1)

This plan governs repo-first launch preparation and channel sequencing.

## Decision

Hacker News posting is **deferred** until traction from other channels is established.

## Repo-First Scope (Current)

### Stage A: Canonical Positioning and Branding

- [x] Lock canonical SVG branding asset and hash record.
- [x] Add `docs/BRAND.md` with tone, claims guardrails, and logo policy.
- [x] Add `docs/CLAIMS_EVIDENCE_LEDGER.md` to keep messaging evidence-bound.
- [x] Add small-icon/favicons package (separate from full banner).

### Stage B: Documentation and Onboarding

- [x] Add a single launch-facing docs entrypoint (`docs/RELEASE_LANDING_V1.md`).
- [x] Update README to kernel-first narrative and conservative claims.
- [x] Keep "quickstart" runnable and accurate.
- [x] Add explicit "when to use / when not to use" section.
- [x] Add links to contracts, replay docs, and troubleshooting.

### Stage C: Proof and Media Packaging

- [x] Generate deterministic screenshot and one short cast from fixture flow.
- [x] Ensure media references map to reproducible commands.
- [x] Publish one compact replay bundle example.

## Channel Staging (HN Deferred)

### Phase 1: Early Traction Channels

Order:

1. X/Twitter
2. Reddit
3. LinkedIn

Each post must include:

- one concrete proof artifact,
- one clear caveat/limitation,
- one CTA (`quickstart` or `replay demo`).

### Phase 2: HN Gate Review

Only proceed to HN when all conditions pass:

- [x] Quickstart completion flow has been validated recently.
- [x] Core docs links are stable and not stale.
- [x] At least one reproducible replay evidence bundle is in-repo (`docs/media/proof/bundles/launch_proof_bundle_v1.zip`).
- [x] No known high-severity docs/claim mismatches.
- [ ] Early channel feedback has been incorporated.

## Metrics Gate for HN Readiness

Suggested minimums before HN:

- >= 2 stable proof posts on early channels.
- >= 1 docs iteration based on external feedback.
- Quickstart issue rate trending down over at least 1 iteration.

## Operational Rules

- Keep messaging and claims synchronized with `docs/CLAIMS_EVIDENCE_LEDGER.md`.
- Treat launch copy changes like code changes: review, diff, evidence.
- Do not claim parity/completeness outside documented coverage.

## Safety Guardrails (Instituted)

- Follow `docs/SAFE_MODE_EXECUTION_POLICY.md`.
- Run `scripts/preflight_workspace_safety.py` before runtime task commands.
- Do not execute task/runtime commands directly in canonical repo without safe preflight.

Latest gate evidence:

- `docs/ci/QUICKSTART_SAFE_VALIDATION_20260217.md`
- `docs/ci/RELEASE_LOW_RISK_GATES_20260217.md`
- `docs/ci/LAUNCH_STAGING_REVIEW_20260217.md`
- `docs/ci/CHANNEL_FEEDBACK_INCORPORATION_CHECKLIST_V1.md`

Current staging decision:

- Broad launch posting: **not ready** (pending early channel feedback incorporation).
