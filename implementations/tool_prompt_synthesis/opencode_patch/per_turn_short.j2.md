{% raw %}## Build-Mode Reminders{% endraw %}

- Emit a single OpenCode patch block per turn; no extra narration.
- Follow each patch with a readback turn before `<BASH>` commands.
- Run tests only after ensuring the Makefile targets exist.
- Call `mark_task_complete()` on a separate turn after successful verification.

