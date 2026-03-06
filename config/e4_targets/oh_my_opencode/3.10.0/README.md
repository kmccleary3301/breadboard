# oh-my-opencode E4 Target Package - 3.10.0

This package is the tracked target bundle for the oh-my-opencode 3.10.0 refresh tranche.

The current E4 lane is anchored to the Phase 8 async/subagent replay evidence. This package
keeps the common visible substrate nearby so the harness is inspectable in-repo instead of
being hidden behind a tiny snapshot file.

Local assets included here:

- `prompts/system.md`
- `prompts/plan.md`
- `prompts/builder.md`
- `refs/agents.mdx`
- `refs/mcp-servers.mdx`
- `refs/tools.mdx`
- `refs/permissions.mdx`

The package remains the common base for the latest versioned replay lane. The replay overlay
keeps only the async/background-task-specific assertions.
