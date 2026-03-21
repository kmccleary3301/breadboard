# DAG Runtime V2 Assessment Surface

DAG Runtime V2 is intentionally narrow.

It does **not** reopen the whole DAG kernel. It adds one shared shape that V1 pressure revealed repeatedly:

- `SearchAssessment`

## Why this exists

`DAG Runtime V1` already proved:

- explicit search truth
- barriered scheduling
- typed carry-over
- bounded message passing
- branch-local state
- trajectory export

The remaining repeated gap was grounded evaluator truth:

- exact verifier outputs
- judge/adjudicator outputs
- execute/verify results

Before V2, those judgments had to live indirectly in:

- `SearchEvent.metadata`
- `SearchMessage.summary_payload`
- candidate score vectors

That was inspectable enough for Phase 0 pressure studies, but too awkward to keep as the stable shared surface.

## What V2 adds

The narrow assessment layer consists of:

- `SearchAssessment`
- `SearchRun.assessments`
- `SearchEvent.assessment_ids`
- `SearchTrajectoryStep.assessment_ids`
- `SearchAssessmentRegistry`
- `RegisteredAssessmentBackend`
- `AssessmentOutput`
- barriered assessment gates:
  - require assessment before select
  - prune on verdict
  - terminate on verdict
  - `max_assessments`

## What this makes possible

This gives the runtime a first-class place to store:

- exact verification truth
- pairwise judge verdicts
- future barriered gate inputs

It also gives barriered search an inspectable way to:

- defer selection until grounded assessment exists
- prune candidates on explicit verdicts
- terminate early on explicit verdicts

without adding:

- a new message system
- a new state system
- async scheduler semantics
- public search policy
- DARWIN-style orchestration

## What remains out of scope

This assessment layer does **not** standardize:

- async frontier scheduling
- decision-policy ontology
- MCTS statistics
- public reward/search-policy surfaces
- training or RL control loops

The point is to widen search fidelity while keeping the runtime:

- maintainable
- composable
- interpretable
- easy to configure
- forward-compatible
