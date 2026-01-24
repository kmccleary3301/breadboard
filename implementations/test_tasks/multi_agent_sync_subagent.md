# Multi-agent sync subagent smoke test

Goal: Verify that the Task tool can synchronously spawn a read-only subagent and return its output.

Steps:
1. Use the Task tool to launch the `reviewer` subagent.
2. Ask it to scan the workspace and summarize what files exist at the repo root.
3. Return a concise summary.

Notes:
- The subagent must only use read/list/glob/grep.
- The main agent should not wait for background jobs (sync only).
