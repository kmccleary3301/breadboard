# Channel Feedback Incorporation Checklist (v1)

Use this checklist to satisfy the final launch staging blocker:

- `Early channel feedback has been incorporated`

Scope: X/Twitter, Reddit, LinkedIn (Hacker News still deferred).

## Entry Criteria

- [ ] At least 2 proof-backed posts shipped across early channels.
- [ ] Each post includes one caveat/limitation and one concrete CTA.
- [ ] Post links point to current docs (`RELEASE_LANDING_V1`, proof bundle, quickstart).

## Feedback Intake

- [ ] Collect links/threads for all early-channel posts.
- [ ] Export top comments/questions into one working note.
- [ ] Group feedback by category:
  - onboarding friction,
  - docs clarity gaps,
  - claims skepticism/mismatch,
  - install/runtime failure reports,
  - feature requests (defer vs now).

## Triage and Decisions

- [ ] Mark each item with severity (`high`/`medium`/`low`) and confidence.
- [ ] Convert high-severity items into actionable tasks/docs edits.
- [ ] Reject/defer out-of-scope items with written rationale.
- [ ] Ensure no public claim language conflicts with evidence ledger.

## Required Updates Before Marking Incorporated

- [ ] Apply at least one docs iteration from real feedback.
- [ ] Update `docs/CLAIMS_EVIDENCE_LEDGER.md` if wording changes.
- [ ] Update `docs/RELEASE_LANDING_V1.md` if onboarding path changes.
- [ ] Add/refresh one reproducible proof artifact if requested by feedback.
- [ ] Record what changed and why in a dated validation note.

## Completion Evidence

- [ ] Create `docs/ci/CHANNEL_FEEDBACK_INCORPORATION_<DATE>.md` with:
  - source posts and links,
  - top feedback themes,
  - implemented changes,
  - deferred items and rationale,
  - before/after docs pointers.
- [ ] Update `docs/LAUNCH_STAGING_PLAN_V1.md`:
  - check `Early channel feedback has been incorporated`.
- [ ] Update `docs/RELEASE_LANDING_V1.md`:
  - add the new incorporation report under latest validation records.

## Exit Criteria

- [ ] The incorporation report exists and is linked from launch docs.
- [ ] The final unchecked HN gate is now checked.
- [ ] Staging decision can be re-reviewed for broad posting readiness.

