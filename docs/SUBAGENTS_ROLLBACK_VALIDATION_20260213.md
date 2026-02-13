# Subagents Rollback Validation

Date: 2026-02-13  
Scope: Execution validation of rollback levels `L0`/`L1`/`L2`/`L3` for runtime + TUI subagent controls.

## Command Executed

```bash
npm run subagents:validate:rollback -- --out ../artifacts/subagents_rollback/summary.json --markdown-out ../artifacts/subagents_rollback/summary.md
```

## Result

- `artifacts/subagents_rollback/summary.json`:
  - `ok=true`
  - `totalLevels=4`
  - `failedLevels=0`

## Level Validation Summary

| Level | Intent | Result |
| --- | --- | --- |
| `L0` | Full subagents enabled | pass |
| `L1` | Focus disabled, keep strip/toasts/taskboard | pass |
| `L2` | Taskboard + focus disabled, keep strip/toasts | pass |
| `L3` | V2 disabled, restore legacy tool-rail routing | pass |

## Assertions Validated Per Level

1. Runtime flags match expected env toggle state.
2. TUI config flags (`subagents.taskboardEnabled`, `subagents.focusEnabled`) match expected env toggle state.
3. Routing consistency holds:
   - `v2=true` -> `routeTaskEventsToToolRail=false`
   - `v2=false` -> `routeTaskEventsToToolRail=true`

Verdict: **Rollback level validation is complete and passing for all defined levels.**
