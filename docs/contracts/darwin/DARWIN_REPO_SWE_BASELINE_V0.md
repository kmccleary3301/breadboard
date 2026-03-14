# DARWIN Repo-SWE Baseline V0

Phase-1 treats `lane.repo_swe` as a bounded patch-and-workspace correctness lane, not a broad autonomous SWE benchmark.

## Baseline evaluator slice

- `tests/test_opencode_patch_apply_codex.py`
- `tests/test_workspace_tracker.py`
- `tests/test_diff_terminal_semantics.py`
- `tests/test_langflow_patch.py`

## Why this slice

- objective and cheap
- patch / workspace semantics are central repo-SWE primitives
- low contamination risk
- deterministic enough for Phase-1 comparative DARWIN plumbing

## Explicit non-goals

- broad issue-fixing
- agentic repository navigation
- long-horizon branch surgery
- claim-bearing software engineering performance
