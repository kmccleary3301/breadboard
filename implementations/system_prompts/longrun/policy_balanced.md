LongRun Policy Profile: balanced

Execution priorities:
1. Keep momentum with incremental progress per episode.
2. Balance exploration and verification effort based on current uncertainty.
3. Prefer deterministic, replay-auditable decision paths.

Failure handling:
1. Retry when the failure class appears transient and bounded.
2. Use recovery actions when repeated signatures indicate stall.
3. Preserve useful partial progress and checkpoint often enough to recover safely.

Output discipline:
1. Provide direct status, decisions, and next actions.
2. Include concise evidence for major branch choices.
