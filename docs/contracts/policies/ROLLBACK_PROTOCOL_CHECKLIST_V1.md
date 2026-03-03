# Rollback Protocol Checklist V1

Use this checklist for any kernel danger-zone rollout.

## A) Pre-rollout readiness

- [ ] Previous stable commit/tag identified.
- [ ] Rollback owner assigned.
- [ ] Rollback commands rehearsed on staging or dry-run environment.
- [ ] Data/state artifacts to preserve are listed.
- [ ] Monitoring and alerts are active.

## B) Rollout guardrails

- [ ] Release is behind explicit scope controls (flag/target path/limited surface).
- [ ] Baseline conformance + replay outputs captured before rollout.
- [ ] Success thresholds are defined (functional + latency + determinism).
- [ ] Abort thresholds are defined.

## C) Rollback triggers

- [ ] Trigger #1: contract gate failure.
- [ ] Trigger #2: replay determinism regression.
- [ ] Trigger #3: boundary/coupling violation.
- [ ] Trigger #4: sustained operational instability.

## D) Rollback execution

- [ ] Stop or quarantine the new rollout path.
- [ ] Restore stable commit/build artifact.
- [ ] Restore relevant state snapshots/checkpoints.
- [ ] Re-run contract/replay smoke gates.
- [ ] Confirm service health and parity status return to baseline.

## E) Post-rollback closure

- [ ] Incident summary documented.
- [ ] Root-cause issue opened with owner/date.
- [ ] ACR updated with rollback results.
- [ ] Follow-up validation plan approved before re-attempt.
