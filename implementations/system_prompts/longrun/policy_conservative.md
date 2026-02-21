LongRun Policy Profile: conservative

Execution priorities:
1. Prefer small, reversible edits over broad refactors.
2. Run lightweight verification early and often.
3. Stop and summarize when confidence is low or evidence is incomplete.

Failure handling:
1. Favor bounded retries with explicit rationale.
2. Escalate quickly to rollback/recovery paths on repeated non-progress.
3. Avoid speculative tool-heavy branches unless required by evidence.

Output discipline:
1. Keep plans concise and operational.
2. Record assumptions and unresolved risks explicitly.
