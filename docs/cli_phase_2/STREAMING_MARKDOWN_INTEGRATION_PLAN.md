# Streaming Markdown V2 Integration Plan (TUI)

This checklist captures the steps to integrate the Streaming Markdown V2 TUI package into the KyleCode CLI for real-time markdown/code/diff rendering with a Claude/Codex-class experience.

---

## A. High-Level Decisions & Scope
- [ ] Choose scope for rich rendering:
  - Assistant messages (LLM replies) first.
  - Tool outputs with markdown (diffs/logs) second.
  - Keep user input/status lines plain initially.
- [ ] Worker lifecycle: one worker per REPL session (per `ReplSessionController`), not global.
- [ ] Document lifecycle: maintain a separate snapshot per assistant turn (reset on new turn).
- [ ] Feature flag: `KYLECODE_RICH_MARKDOWN=1` or `/view markdown on|off`; plaintext fallback available.

## B. Wire the Streaming Markdown Package
- [x] Vendor/install `markdown-v2-core` and `markdown-v2-worker` into the repo/workspace; update `package.json` (hoist rehype/unified/dompurify/lezer deps).
- [x] Use worker snapshots: `createInitialSnapshot` + `applyPatchBatch` (now exported) wired in `src/markdown/streamer.ts`.
- [x] Place the worker bundle `workers/markdown-worker.js` in an accessible path (copied during build to `dist/workers/markdown-worker.js`).
- [x] Wrapper (`src/markdown/streamer.ts`):
  - Owns a worker + snapshot via `worker_threads`; prewarm langs, doc plugins, env override `KYLECODE_MARKDOWN_WORKER_PATH`.
  - `initialize/append/finalize/subscribe/dispose` implemented.
  - Fallback path: if worker fails, inline parser builds blocks and emits a hint (`Rich markdown disabled: …`) when disabled.

## C. Extend REPL / Controller State
- [ ] Extend conversation entry type (assistant) with `richBlocks?: Block[]`, `isMarkdownStreaming?: boolean`.
- [ ] In `ReplSessionController`, manage `Map<id, MarkdownStreamer>`:
  - On first `assistant_message` of a turn: create streamer, `initialize` with prewarm langs (ts/tsx/bash/python/json/markdown/diff); mark streaming.
  - On text deltas: `streamer.append(chunk)`.
  - On completion/run_finished: `streamer.finalize()`, mark streaming false.
  - Subscribe to streamer updates; update `richBlocks` on change; emit `onChange`.

## D. Build Markdown Rendering Components (Ink)
- [ ] `MarkdownBlockView`:
  - Paragraph, heading (with level), list/blockquote, code → delegate to `CodeBlockView`, fallback to `payload.raw`.
  - Optional streaming indicator for non-final blocks.
- [ ] `InlineNodesView`:
  - `text`, `strong` (bold), `em` (italic), `code` (inverse or colored), `link` (underline), plugin nodes (e.g., `citation`).
- [ ] `CodeBlockView`:
  - Highlight using Shiki or `extractHighlightedLines`; map hex → terminal palette.
  - Convert tokens to `TerminalLine[]` (segments with fg/bg/style); render line-by-line with nested `<Text>` colors.
  - Show language label if desired.

## E. Diff-Specific Rendering
- [ ] Normalize diffs as markdown ` ```diff` (or set `meta.lang="diff"`).
- [ ] In `CodeBlockView` for `lang==="diff"`:
  - `+` lines → green fg/bg; `-` lines → red fg/bg; `@@` → dim/cyan; context → normal.
  - Preserve spacing for alignment.
- [ ] Integrate with collapse/expand:
  - Summary line when collapsed: `Δ +X/-Y lines hidden — press e to expand`.
  - Expanded view renders full diff with highlighting.

## F. Transcript Integration (ReplView)
- [ ] When `richBlocks` exist and markdown is enabled:
  - Render finalized blocks in the `<Static>` region (immutable scrollback).
  - Render non-final blocks in the streaming tail.
  - Fallback to plaintext rendering when disabled or on failure.
- [ ] Tool logs: use markdown renderer only for tool events marked as markdown; otherwise keep current log UI.

## G. Config, Errors, and Fallbacks
- [ ] Add `viewPrefs.richMarkdown` and expose toggles (`KYLECODE_RICH_MARKDOWN`, `/view markdown on|off`).
- [ ] Worker failure handling:
  - On error/INIT failure, set a disabled flag; fall back to plaintext for that session.
  - Log debug info when `KYLECODE_DEBUG_MARKDOWN=1`.

## H. Testing & Tooling
- [ ] Add scripts:
  - `markdown_basic.json` (headings, inline styles, code fence).
  - `markdown_diff.json` (diff block, large diff for collapse test).
- [ ] Run via `stress:bundle`/PTY harness; update snapshots (`*_output.txt`) and grid artifacts.
- [ ] Add layout/assertion checks: code/diff width, truncation, presence of markdown markers.

## I. Documentation & Done Criteria
- [ ] Update docs:
  - `UX_OVERHAUL_PLAN.md` with streaming markdown/diff integration overview.
  - `TUI_STRESS_TESTS.md` with new markdown/diff scripts and artifacts.
- [ ] Done when:
  - Assistant responses stream as rich markdown (headings/inline/code).
  - Diff outputs render with red/green highlighting and collapse/expand.
  - Feature flag allows plaintext fallback.
  - Stress/PTY suites reproduce rich output without layout regressions or flicker.

---

Notes:
- Map our engine’s tool/diff events to markdown upstream (e.g., wrap diffs in fenced blocks) or add a small diff plugin if needed.
- Keep batching of updates in the controller; the markdown worker’s “single dirty tail” aligns with our existing streaming-tail model.
