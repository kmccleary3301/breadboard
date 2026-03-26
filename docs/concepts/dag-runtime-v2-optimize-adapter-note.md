# DAG Runtime V2 Optimize Adapter Note

DAG Runtime V2 does not absorb optimize public nouns.

The intended composition direction is one-way:

- DAG runtime exposes assessment-grounded search artifacts
- optimize may consume those artifacts outside the DAG kernel

## Stable handoff surface

The adapter boundary is intentionally small:

- `SearchAssessment`
- `SearchRun.assessments`
- `SearchEvent.assessment_ids`
- `SearchTrajectoryStep.assessment_ids`
- `SearchTrajectoryExport.selected_candidate_id`

This is enough for optimize-side consumers to:

- inspect grounded verifier or judge evidence
- align candidate ranking with explicit search assessments
- reuse trajectory exports without coupling optimize into DAG internals

## What does not happen here

The DAG kernel does **not** grow:

- optimize-specific manifests
- optimize-specific promotion nouns
- reward-suite or search-policy public surfaces

Any optimize adapter should remain:

- outside `agentic_coder_prototype.search`
- derivative of exported DAG artifacts
- replaceable without changing the DAG truth surface
