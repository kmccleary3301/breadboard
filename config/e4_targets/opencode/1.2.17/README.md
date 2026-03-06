# OpenCode E4 Target Package - 1.2.17

This package is the tracked target bundle for the OpenCode 1.2.17 refresh tranche.

The current repo already had most OpenCode prompt and tool material in `implementations/`.
This package republishes the visible prompt assets nearby so the latest E4 lanes stop feeling
like tiny overlays with invisible guts.

Local assets included here:

- `prompts/system.md`
- `prompts/plan.md`
- `prompts/builder.md`
- `prompts/plan_bootstrap_protofs.yaml`
- `prompts/shell_write_block_opencode.prompt.md`
- `refs/agents.mdx`
- `refs/mcp-servers.mdx`
- `refs/tools.mdx`

The package remains the common harness base for the replay lanes; individual E4 overlays keep
scenario-specific replay assertions and tool narrowing.
