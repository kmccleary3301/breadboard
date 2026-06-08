# BreadBoard TUI Markdown Rendering And Dropout Evidence Index

Date: 2026-06-08
Branch: `codex/markdown-dropout-goal-20260608`
Implementation commit: `3982004` (`Harden markdown dropout QC gates`)
Baseline: `github/main` after PR #25 (`e14f3a5`)

## Defect Classes Covered

- Markdown wrapper blocks must render as Markdown, not as literal `code · markdown` wrappers.
- Raw Markdown delimiters for headings, emphasis, blockquotes, wrapper fences, and expected rich inline content must not leak into rendered assistant prose.
- Final assistant content must not be replaced by `Assistant latest`, `Assistant streaming`, a dangling fence, an empty assistant slot, or a placeholder after completion.
- Long multi-turn assistant responses must preserve the full committed answer in terminal history, including middle nested code content.
- Streaming markdown and final cumulative markdown must project the same semantic content and must not diverge materially.
- The Codex default model shown by the TUI and the session creation payload must agree on `openai/gpt-5.4-mini`.

## Implementation Summary

- Added deterministic mock SSE + PTY lane for the exact three-turn user flow: greeting, markdown wrapper, then long markdown exceeding terminal height.
- Added assertions for required user/assistant markers, final-state assistant entries, forbidden placeholders, forbidden wrapper artifacts, and duplicate partial projections.
- Added stream/final equivalence fixtures and gate for identical semantic markdown emitted through chunked deltas versus final cumulative message.
- Added a model payload recorder in the mock SSE layer and a gate proving visible startup model and create-session payload agree.
- Added a global artifact sentinel across saved markdown QC snapshots.
- Fixed the projection root cause by bounding rich streaming markdown to a tail preview in preserved scrollback mode and by allowing finalized/non-streaming assistant entries to move to append-only static feed even when stream-mdx block finalization metadata is still settling.

## Validation Commands

Run from `tui_skeleton/` unless noted.

- `npm run build --if-present`
  - Result: passed.
  - Note: installed local `breadboard` and `bb` wrappers into `/home/querylake_manager/.local/bin`.
- `npm run typecheck`
  - Result: passed.
- `npm test`
  - Result: passed, 212 test files, 1100 tests.
- `npm run -s qc:user-reported:markdown-wrapper-render`
  - Result: passed.
  - Snapshot: `tui_skeleton/scripts/_tmp_qc_markdown_wrapper_render.txt`
- `npm run -s qc:user-reported:two-turn-long-markdown-dropout`
  - Result: passed.
  - Snapshot: `tui_skeleton/scripts/_tmp_qc_two_turn_long_markdown.txt`
  - State dump: `tui_skeleton/scripts/_tmp_qc_two_turn_long_markdown_state.jsonl`
  - Mock log: `tui_skeleton/scripts/_tmp_qc_two_turn_long_markdown_mock.log`
- `npm run -s qc:user-reported:markdown-stream-final-equivalence`
  - Result: passed.
  - Stream snapshot: `tui_skeleton/scripts/_tmp_qc_markdown_equivalence_stream.txt`
  - Final snapshot: `tui_skeleton/scripts/_tmp_qc_markdown_equivalence_final.txt`
- `npm run -s qc:user-reported:markdown-artifact-sentinel`
  - Result: passed, 5 snapshot files checked.
- `npm run -s qc:user-reported:model-payload-default`
  - Result: passed.
  - Snapshot: `tui_skeleton/scripts/_tmp_qc_model_payload_default.txt`
  - Payload records: `tui_skeleton/scripts/_tmp_qc_model_payload_default_records.jsonl`
- `npx vitest run src/repl/components/replView/renderers/markdown/__tests__/streamMdxAdapter.test.ts src/repl/components/replView/renderers/__tests__/conversationRendererMarkdown.test.ts`
  - Result: passed, 22 tests.

## Key Evidence From The Long Markdown Gate

Final history snapshot now has the required committed shape:

- `Long Markdown Response`: 1 occurrence.
- `Code Section`: 1 occurrence.
- `LONG-MARKDOWN-CODE-SENTINEL`: 1 occurrence.
- `END-LONG-MARKDOWN-SENTINEL`: 1 occurrence.
- Third user request `Show me a **lot** more markdown.` remains present.
- Forbidden placeholders `Assistant latest` and `Assistant streaming` are absent from final committed content.
- Forbidden wrapper artifact `code · markdown` is absent.

The gate intentionally allows `code · python` labels for real nested code blocks; it does not allow Markdown wrapper labels or missing code bodies.

## Residual Risks

- These lanes are deterministic PTY/mock-SSE gates, not live nondeterministic model sessions. They are the right regression contract for this defect class, but live sessions should still be periodically covered by broader profile matrix/stress lanes.
- The static-feed readiness fix relies on raw-text fallback when stream-mdx metadata lags. This is intentional and validated by the dropout and equivalence gates, but future renderer refactors must preserve that fallback contract.
- The artifact sentinel is high-signal, not a full Markdown semantic oracle. New Markdown syntaxes should add corpus-specific assertions rather than broadening global bans.

## Completion Mapping

- Phase A: failure inventory and evidence channels are represented by gate names, sentinel rules, state dumps, mock logs, and payload records.
- Phase B: user-reported reproductions exist for wrapper render, long assistant dropout, stream/final mismatch, active height pressure via small resize, and model mismatch.
- Phase C: deterministic `npm run` gates exist and retain evidence under ignored temp paths.
- Phase D: renderer tests and stream/final equivalence gate cover canonical markdown projection behavior.
- Phase E: PTY frame/history snapshots prove terminal-visible behavior for the relevant lanes.
- Phase F: model/session payload gate proves `openai/gpt-5.4-mini` at visible and wire levels.
- Phase G: clean projection-layer fix implemented and validated.
- Phase H: this evidence index records commands, artifacts, results, residual risks, and commit hash.
