# BreadBoard Generalized Agentic Search Closeout Note

Date: 2026-05-08

Status: implementation complete against the phase-12 playbook

## What Shipped

The phase-12 maintainer-request program now has an additive artifact-task substrate for generalized external-evaluation campaigns.

Implemented surfaces:

- artifact requirements and contracts with path-safety, existence, size, and sha256 gates
- response materialization from strict fenced blocks
- required-artifact completion semantics independent of provider/text completion
- external evaluator command hooks with stdout, stderr, exit code, timeout, duration, and error capture
- evidence bundle export with task input, raw response, copied artifacts, artifact manifest, evaluator records, and sha256 manifest
- offline single artifact task runner
- workspace template import/export bridge with protected-root rejection
- campaign runner with ledger, resume, failed-candidate preservation, and bounded parallel execution
- downstream fixture adapters for candidate packets, optimize `EvaluationRecord`, and RL `TrajectoryEpisode`
- agent-readable operator smoke script with JSON output
- config explain script for the artifact-single preset
- doctor checks for artifact-task readiness

## Validation

Focused validation:

```bash
pytest tests/test_artifact_tasks.py -q
```

Result:

```text
14 passed
```

Compile validation:

```bash
python -m compileall agentic_coder_prototype/artifact_tasks scripts/dev/artifact_task_smoke.py scripts/dev/config_explain.py scripts/dev/first_time_doctor.py
```

Result: passed.

Operator/discoverability validation:

```bash
python scripts/dev/first_time_doctor.py --profile engine --json
python scripts/dev/artifact_task_smoke.py --out-dir /tmp/breadboard_artifact_task_smoke_phase12 --json
python scripts/dev/config_explain.py --json
```

Result: all passed.

Broader regression note:

```bash
pytest tests/test_artifact_tasks.py tests/test_search_runtime.py -q
```

This broader run was attempted but stalled after partial progress. It is not counted as completion evidence for this tranche.

## Behavior Change Scope

No existing BreadBoard runtime path was intentionally changed.

Untouched high-blast-radius surfaces:

- `main.py`
- provider runtime internals
- DAG runtime internals
- optimize core logic
- RL core logic
- C-Trees runtime internals

The only existing file modified is `scripts/dev/first_time_doctor.py`, where additive readiness checks were added.

## Claim Boundary

This tranche proves BreadBoard can own the generic orchestration/evidence boundary for artifact-gated tasks. It does not claim that BreadBoard now owns GPURL correctness/timing validation, hidden tests, benchmark score parsing, or domain-specific acceptance policy.

External projects still own:

- evaluator command content
- benchmark-specific pass/fail thresholds
- promotion policy thresholds
- hidden test governance
- domain-specific claim ledgers

## Deferred Work

No phase-12 checklist item is intentionally deferred.

Future high-EV work should be treated as a new tranche:

- real provider-backed artifact task generation using this substrate
- richer campaign ranking and promotion/quarantine policies
- deeper DAG branching over normalized artifact-task evidence
- richer optimize/RL consumers over campaign summaries
- publication-grade schema stabilization if external consumers begin depending on the bundle format
