# Claude Code E4 Target Package - 2.1.63

This package is the tracked target bundle for the Claude Code 2.1.63 refresh tranche.

Unlike the old minimal replay overlay, this package keeps the visible prompt assets nearby
so the harness is inspectable in one place:

- `prompts/system-vendor-logged.prompt.md`
- `prompts/system-reminder-start.prompt.md`
- `prompts/system-reminder-end.prompt.md`
- `prompts/compact.prompt.md`
- `prompts/check-active-git-files.prompt.md`
- `prompts/check-new-topic.prompt.md`
- `prompts/summarize-previous-conversation.prompt.md`

The lane is still grounded in the replay evidence we captured from the logged Claude wrapper.
That means the package is explicit about the currently exercised Claude surfaces while keeping
extra prompt assets nearby for inspection and future expansion.
