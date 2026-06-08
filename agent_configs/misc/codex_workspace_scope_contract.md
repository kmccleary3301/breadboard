# BreadBoard Workspace Scope Contract

- Treat the current working directory as the complete repository root for this BreadBoard session.
- Do not inspect parent directories. Never run `find ..`, `rg ..`, `grep ..`, `ls ..`, `cat ..`, `sed ..`, or `cd ..`.
- If an `AGENTS.md` exists in the current working directory, it is the authoritative workspace instruction file for this run; do not search above it.
- For implementation tasks, create real requested deliverables. Do not satisfy requested files with placeholders, stubs, TODOs, or "will finalize later" content.
- When the user requests build, compile, smoke, or test verification, run the requested verification commands before giving the final answer.
- For direct conversational, explanatory, UX, or product questions that do not ask you to inspect files or make changes, answer directly in the same assistant message. Do not inspect the workspace, run tools, or emit a progress update first.
- In this BreadBoard REPL, do not send progress-only assistant messages before tool calls. For implementation or inspection tasks, call tools directly first, then give one final assistant answer after the work and verification are complete.
- If the harness sends a `<VALIDATION_ERROR>`, obey it immediately. When it says the next action must be a tool call, do not answer with progress text; make the required tool call next, then give the final answer only after the validation requirement is satisfied.
- If validation says verification is missing, your next action must be a shell tool call such as `make`, `cc`, `gcc`, a smoke-test script, or another concrete build/test command. Do not inspect files, update a plan, or describe what you will do first.
