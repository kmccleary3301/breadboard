# Agent Completion Strategies – Reference Notes

This is an aggregation of how popular agentic coding systems handle “task completion” so we can baseline our own configuration.

| System | Completion Mechanism | Notes |
| --- | --- | --- |
| **Codex CLI** | `mark_task_complete` tool | The CLI instructions typically include “When finished, call `mark_task_complete()`.” The agent relies on its tool-calling capability to signal structured completion. |
| **Claude Code** | Natural language sentinel in UI | Claude Code (Anthropic) usually prompts “Say DONE when you’re finished.” Its guardrails watch for `DONE` and present a completion button. |
| **Cursor** | Manual user confirmation + plan checklists | Cursor shows a sidebar checklist. The model often ends with “Let me know if you’d like more changes,” so completion is manual rather than programmatic. |
| **OpenCode** | Tool-based completion (`mark_task_complete`), similar to Codex | The OpenCode prompt library includes explicit instructions for the agent to end by calling the completion tool. |

### Takeaways for KyleCode

1. **Tool-based completion is the most reliable** (Codex/OpenCode). We already have `mark_task_complete` in the tool registry—use it.
2. **Natural-language sentinel** (e.g., `TASK COMPLETE`) is a useful fallback for provider contexts where tool calling might be unavailable.
3. **Manual confirmation** (Cursor) isn’t desirable for automated regressions; we should rely on a deterministic signal.

### Action Items

* Ensure our task prompts instruct models to call `mark_task_complete` when done and enable the completion detector to look for the sentinel as a backup.
* Update the regression prompts (e.g., “Enumerate workspace”) to include a clear termination instruction so we don’t rely on max-step exhaustion.
