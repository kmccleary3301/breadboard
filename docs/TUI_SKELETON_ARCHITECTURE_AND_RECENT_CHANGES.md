# TUI Skeleton Architecture And Recent Changes (Scrollback Ink TUI)

This document is a “mechanic’s manual” for the Ink-based scrollback TUI in `tui_skeleton/`.
It is written for engine developers who need to understand how the UI is wired, what data it consumes, where rendering happens, and what we changed recently while aligning taste/UX with Claude Code.

Scope:
- This covers `tui_skeleton/` (Ink/React) and its interactions with the BreadBoard engine / CLI-bridge event stream.
- “Goldens” and “replays” are historically engine concepts. As of February 2026, the Ink TUI also has a small, deterministic **internal UI baseline harness** (U1) that replays `SessionEvent` JSONL into the controller and gates a few text snapshots in CI (see “Internal TUI Baselines (U1)” below).

Non-goals:
- This is not a full engine architecture document. See `docs/CTREES_TUI_INTEGRATION.md` and related docs for broader system context.

---

## Quick Orientation

Primary entrypoint:
- `tui_skeleton/src/main.ts`

Primary command that runs the Ink TUI:
- `tui_skeleton/src/commands/repl/command.tsx`

Primary session controller (state machine + event-stream ingestion):
- `tui_skeleton/src/commands/repl/controller.ts`

Primary “render state to text lines” pipeline (classic scrollback rendering):
- `tui_skeleton/src/commands/repl/renderText.ts`

Primary React view tree for the OpenTUI-like “ReplViewShell” experience:
- `tui_skeleton/src/repl/components/ReplViewImpl.tsx`
- `tui_skeleton/src/repl/components/replView/ReplViewShell.tsx` (via `ReplViewImpl`)

Markdown streaming + diff tokenization (stream-mdx worker integration):
- `tui_skeleton/src/markdown/streamer.ts`
- `tui_skeleton/src/markdown/workerThread.ts`

Syntax highlighting (Shiki):
- `tui_skeleton/src/repl/shikiHighlighter.ts`

Landing “Claude-like” chrome:
- `tui_skeleton/src/repl/components/replView/landing/scrollbackLanding.tsx`

Composer/input bar:
- `tui_skeleton/src/repl/components/replView/composer/ComposerPanel.tsx`
- `tui_skeleton/src/repl/components/LineEditor.tsx`

Diff inline token spans (word-ish diffs):
- `tui_skeleton/src/repl/diff/inlineDiff.ts`

Token rendering (from stream-mdx / worker tokens):
- `tui_skeleton/src/repl/markdown/tokenRender.ts`

Design system (colors, icons, “Cooked for …” verbs, color mode handling):
- `tui_skeleton/src/repl/designSystem.ts`
- `tui_skeleton/src/repl/components/replView/theme.ts`

Internal UI baselines (deterministic `SessionEvent` replay -> text snapshot):
- `tui_skeleton/ui_baselines/u1/` (tracked fixtures + blessed snapshots)
- `tui_skeleton/scripts/run_tui_goldens.ts` (render runner)
- `tui_skeleton/scripts/compare_tui_goldens.ts` (text compare + normalization)
- `tui_skeleton/scripts/tui_u1_goldens_report.ts` (Phase 0 report-only)
- `tui_skeleton/scripts/tui_u1_goldens_strict.ts` (Phase 1 gate)

---

## Runtime Model: How The TUI Is Started

`tui_skeleton/src/main.ts` is the CLI root. It wires subcommands like:
- `repl` (Ink scrollback TUI and classic mode)
- `ui` (alias for the repl UI)
- `run`, `doctor`, `sessions`, `files`, etc.

Engine boot:
- For most commands, `main.ts` calls `ensureEngine()` (via `tui_skeleton/src/engine/engineSupervisor.ts`) unless the command is in a skip list (`connect`, `engine`, `auth`, etc.) or the TUI is OpenTUI mode.

TUI mode selection:
- The `repl` command supports selecting a UI implementation via `--tui` or `BREADBOARD_TUI_MODE`.
- `opentui` mode spawns the Bun-based OpenTUI slab (`opentui_slab/`).
- `classic` mode runs the Ink-based scrollback UI (this document).

Key file:
- `tui_skeleton/src/commands/repl/command.tsx`

---

## The ReplSessionController: State + Event Stream

The controller is the “brain” of the TUI. It:
- Starts/attaches to a session.
- Submits user input to the engine/provider layer.
- Receives SSE events (tool calls/results, assistant deltas, completion markers).
- Maintains derived UI state for menus, attachments, inline tool logs, status lines, etc.

Key file:
- `tui_skeleton/src/commands/repl/controller.ts`

High-level state model:
- `conversation`: ordered `ConversationEntry[]` (user/assistant/system text)
- `toolEvents`: `ToolLogEntry[]` (Write/Patch/etc tool artifacts)
- `liveSlots`: tool-running slots (to display “working” tool headers and streaming tool output)
- `stats`: event/tool counts, model, remote/local, usage/cost/latency (where provided)
- `viewPrefs`: display preferences (collapse modes, tool rails, raw stream view, diff line numbers, rich markdown on/off)
- menu states: model picker, skills picker, inspect panel, rewind menu

Rendering split:
- The OpenTUI-like UI path uses `ReplViewShell` + controller hooks to build a “panelized” UI.
- The classic scrollback path uses the controller state and `renderStateToText()` to turn state into terminal lines.

Important behavior:
- The controller can “collapse” large assistant messages in the UI for readability (view-level collapse, not destructive truncation of raw data). This is controlled by:
  - `tui_skeleton/src/repl/transcriptUtils.ts` (auto-collapse heuristics)
  - `viewPrefs.collapseMode`

If you see output like “N lines hidden …”:
- That is a UI rendering/collapse affordance, not the engine dropping content.
- The raw assistant text still exists in the controller state; the renderer chooses not to print it verbatim when it exceeds collapse thresholds or looks like a big diff block.

---

## Markdown Pipeline (stream-mdx Worker)

The TUI supports “rich markdown” rendering through stream-mdx:
- `tui_skeleton/src/markdown/streamer.ts` creates a `MarkdownStreamer`.
- The streamer uses a worker thread to parse markdown incrementally and emit `Block[]`.
- On failure (worker cannot start), it falls back to a simple inline block builder so the UI remains usable.

Worker bootstrap:
- `tui_skeleton/src/markdown/workerThread.ts`
- It resolves `@stream-mdx/worker` and points at the hosted bundle `dist/hosted/markdown-worker.js`.
- The worker bundle can be overridden by `BREADBOARD_MARKDOWN_WORKER_PATH`.

Diff + syntax highlight tokens:
- stream-mdx’s snapshot nodes can include “code-line” nodes with:
  - `tokens` (token-level coloring)
  - `diffKind` (add/del/neutral)
  - `oldNo/newNo` (line numbers)
- `attachCodeLineMeta()` in `streamer.ts` walks the snapshot graph and attaches `codeLines` into the `Block.payload.meta` for downstream renderers.

Why this matters:
- This is how we can do “diff background + syntax-highlighted foreground” at the same time.

---

## Syntax Highlighting (Shiki)

There are two related paths:

1. Shiki within the markdown pipeline (preferred for fenced code blocks and diff blocks).
2. Shiki ad-hoc highlighting used by the “classic” renderer when it is given raw code.

Key file:
- `tui_skeleton/src/repl/shikiHighlighter.ts`

Current default theme:
- `SHIKI_THEME = "andromeeda"`

Operational notes:
- Shiki is lazy-loaded (`import("shiki")`) so the first code block may render unhighlighted until the highlighter is ready.
- The helper will refuse to highlight extremely large snippets (`MAX_SHIKI_LINES`, `MAX_SHIKI_CHARS`) to avoid freezing the UI.
- Languages are pre-registered via `DEFAULT_LANGS` and additional languages can be loaded on demand.

---

## Diff Rendering: Write(...) and Patch(...)

Goals we’ve been driving toward (Claude-like taste):
- Show only the tool header line (e.g. `Write(hello.c)`, `Patch(hello.c)`) and the code/diff body.
- Hide noisy patch headers (full `diff --git`, `index`, etc.) unless needed.
- Support syntax highlighting within the diff body.
- For diffs, use:
  - line-level background colors for added/deleted lines
  - stronger background accent for inline (token-span) additions/deletions
  - keep unmodified text with no custom background

The inline token spans are computed here:
- `tui_skeleton/src/repl/diff/inlineDiff.ts`

How inline spans are computed:
- Tokenize both strings into “whitespace | alnum-ish | punctuation” tokens.
- Compute LCS keep-map (dynamic programming).
- Create spans for the tokens that are not part of the LCS (excluding pure whitespace tokens).

Renderer integration:
- `tui_skeleton/src/commands/repl/renderText.ts` uses:
  - `computeInlineDiffSpans()` to highlight only the changed substring segments within `-` and `+` lines.
  - stream-mdx token lines to color syntax tokens.

Truncation:
- The UI may cap very large diffs in the view (for speed and readability) and show a “... (N lines hidden)” marker.
- This is presentation-only; it should not delete underlying data.

---

## Landing Page (Top Chrome)

Key file:
- `tui_skeleton/src/repl/components/replView/landing/scrollbackLanding.tsx`

It builds the Claude-like header box:
- warm border accent
- split layout (left: ASCII art + config/model/cwd, right: tips + recent activity)

Important implementation detail:
- It uses ANSI-aware padding and width calculations (`visibleWidth`, `stringWidth`, `stripAnsiCodes`) so gradients and colored borders do not cause off-by-one wrapping.

Known failure modes when it tears/wraps:
- width computations that ignore ANSI sequences
- off-by-one when computing inner width vs border width
- printing a line with visible length == terminal width in some terminals can trigger a wrap (depending on terminal settings)

---

## Composer / Input Bar

Key file:
- `tui_skeleton/src/repl/components/replView/composer/ComposerPanel.tsx`

Key properties:
- The “Claude chrome” mode draws a rule above and below the input line (border-like).
- Placeholder behavior differs between “Claude chrome” vs default:
  - Claude chrome uses a “Try …” placeholder and hides caret when placeholder is visible.

The actual text editor:
- `tui_skeleton/src/repl/components/LineEditor.tsx`

Enter vs shift-enter considerations:
- We treat “Enter” as submit by default (`submitOnEnter`).
- For multiline input, the editor needs a separate “insert newline” affordance, and must match provider UI behavior if we’re emulating it (Claude uses a visible `\` behavior on shift-enter).

If you see an “Enter creates a newline” mismatch:
- That is an input-bus or editor event mapping issue, not a rendering issue.

---

## Transcript Viewer / Scrollback

Two related pieces:

1. “Classic” renderer
- `tui_skeleton/src/commands/repl/renderText.ts` emits terminal lines (chalk-colored strings).
- It includes helpers to:
  - render markdown blocks
  - render tool logs
  - render diff blocks with line backgrounds and inline spans
  - render horizontal rules: if the assistant emits `---` we normalize that into a full-width terminal rule (Claude-like)

2. UI component scrollback viewer
- `tui_skeleton/src/repl/components/TranscriptViewer.tsx`
- It supports a Claude-like variant that reduces chrome and renders search status below the body.

Transcript window truncation:
- `tui_skeleton/src/repl/transcriptUtils.ts` caps the number of transcript entries kept in the “visible window”.
- This is to prevent UI slowdown and gigantic renders. This is not intended to “truncate the assistant” as an engine behavior.

---

## Recent Changes (What We Did “In Recent Memory”)

This is a non-exhaustive list of changes we made while aligning the TUI with Claude Code style:

- stream-mdx integration improvements:
  - using stream-mdx token lines for syntax highlighting
  - retaining diffKind and code-line metadata for combined diff background + token foreground

- Shiki theme and unification:
  - default theme set to `andromeeda` (`tui_skeleton/src/repl/shikiHighlighter.ts`)
  - avoiding ad-hoc per-render theme selection; prefer a single theme constant

---

## Internal TUI Baselines (U1)

Why this exists:
- The engine golden system answers: “Does the engine reproduce the model-visible contract?”
- The Ink TUI baselines answer: “Given a fixed event stream, does the UI render a stable, readable transcript?”
- U1 is intentionally limited to **ASCII + no-color** text snapshots to maximize determinism and CI reliability.

Where the baselines live:
- `tui_skeleton/ui_baselines/u1/`
- Inputs are `SessionEvent` JSONL files per scenario:
  - `tui_skeleton/ui_baselines/u1/scenarios/*/input/events.jsonl`
- Blessed snapshots are plain text:
  - `tui_skeleton/ui_baselines/u1/scenarios/*/blessed/render.txt`
- The manifest lists which scenarios to run and which render flags to apply:
  - `tui_skeleton/ui_baselines/u1/manifests/u1.yaml`

How baselines are generated:
- Render (candidate) by replaying events into `ReplSessionController.applyEvent()` and then calling `renderStateToText()`:
  - `tui_skeleton/scripts/run_tui_goldens.ts`
- Compare candidate vs blessed with normalization (timestamps/tokens/paths):
  - `tui_skeleton/scripts/compare_tui_goldens.ts`

CI wiring:
- Workflow: `breadboard_repo/.github/workflows/tui-u1-goldens.yml`
- Phase 0 (report-only): `node --import tsx scripts/tui_u1_goldens_report.ts`
- Phase 1 (gate): `node --import tsx scripts/tui_u1_goldens_strict.ts`

Developer usage (local):
```bash
cd tui_skeleton
node --import tsx scripts/tui_u1_goldens_report.ts
node --import tsx scripts/tui_u1_goldens_strict.ts
```

Notes:
- Candidate outputs are written under `tui_skeleton/ui_baselines/**/_runs/` and are gitignored.
- These baselines are intentionally narrow; higher-fidelity style/pixel baselines should be layered in later once U1 is stable.

## Internal TUI Baselines (U2)

U2 extends U1 with:
- Unicode glyphs enabled (`ascii_only: false`)
- Color rendering enabled (`colors: true`, `color_mode: truecolor`)
- Structural comparisons that **strip ANSI** during equality checks while still asserting that ANSI is present (`normalize.strip_ansi: true`, `style.require_ansi: true`)

Paths:
- Manifest: `tui_skeleton/ui_baselines/u2/manifests/u2.yaml`
- Scenarios: `tui_skeleton/ui_baselines/u2/scenarios/*`

CI wiring:
- Workflow: `breadboard_repo/.github/workflows/tui-u2-goldens.yml`
- Report-only: `node --import tsx scripts/tui_u2_goldens_report.ts`
- Gate: `node --import tsx scripts/tui_u2_goldens_strict.ts`

## Internal TUI Baselines (U3)

U3 extends U2 with a focused “diff style” gate:
- Inputs are deterministic `SessionEvent` JSONL streams.
- Equality comparisons can strip ANSI (for stability), but style checks still inspect the raw candidate text.
- We additionally assert:
  - ANSI exists somewhere in the candidate output (`style.require_ansi`)
  - added lines have background ANSI sequences (`style.diff_add_bg`)
  - deleted lines have background ANSI sequences (`style.diff_del_bg`)

This is the first place we explicitly gate “diff look” (backgrounds) without moving all the way to pixel diffs.

Paths:
- Manifest: `tui_skeleton/ui_baselines/u3/manifests/u3.yaml`
- Scenario: `tui_skeleton/ui_baselines/u3/scenarios/diff_patch_preview/`

CI wiring:
- Workflow: `breadboard_repo/.github/workflows/tui-u3-goldens.yml`
- Report-only: `node --import tsx scripts/tui_u3_goldens_report.ts`
- Gate: `node --import tsx scripts/tui_u3_goldens_strict.ts`

- Tool artifact rendering (Write/Patch):
  - show only the concise tool header (file path) + body
  - render diffs “simple” (no full diff headers unless needed)
  - cap/truncate huge diffs consistently, with “... (N lines hidden)” markers
  - add syntax highlighting in Write previews and Patch diffs
  - unify add-line green background with token-level add highlight background

- Inline word-level diffs:
  - improved tokenization (Unicode-aware regex) and LCS-based span selection
  - highlight only changed spans within `-`/`+` lines

- Status line and completion UX:
  - adopting Claude-like “Cooked for …” (active) and “Cooked for …” (completed) lines
  - placing the cooked status line above the input bar; keeping “? for shortcuts” below the input bar

- Layout/wrapping fixes:
  - multiple passes on off-by-one and padding issues (landing + composer borders)
  - ensuring horizontal rules (`---`) render to full terminal width (Claude-like)

---

## Practical Debugging Checklist

When the UI “looks wrong”, locate the failure category first:

Layout/wrapping tears:
- Check ANSI-aware width functions:
  - `tui_skeleton/src/repl/components/replView/utils/ansi.ts`
  - `tui_skeleton/src/repl/components/replView/landing/scrollbackLanding.tsx`

Input bar extra blank line:
- Look at `ComposerPanel.tsx` and `LineEditor.tsx`
- Verify margin/padding and placeholder padding behavior.
- Verify prompt rule (top/bottom) is not double-rendered.

Lost syntax highlighting:
- Confirm `SHIKI_THEME` and `ensureShikiLoaded()` is called at least once.
- Confirm stream-mdx blocks have code-line tokens attached (worker healthy).
- Confirm we are rendering token lines (not raw plain text fallback).

Diff color mismatches:
- Search for the add/del background hex tokens in:
  - `tui_skeleton/src/repl/designSystem.ts`
  - `tui_skeleton/src/commands/repl/renderText.ts`

Weird “lines hidden …” banners:
- Confirm whether it is transcript auto-collapse or a markdown block collapsing behavior.
- Ensure the raw assistant payload is still present in controller state (it should be).

---

## “What The Engine Dev Usually Needs”

If you’re trying to connect engine output to what’s on-screen:

1. Start at the stream:
   - session SSE events -> controller reducers -> state

2. Identify the renderer:
   - classic: `renderStateToText()` (chalk strings)
   - view shell: render nodes/hooks, then Ink components

3. For diffs and markdown:
   - if it looks “structured”, it likely came from stream-mdx blocks
   - if it looks plain, it may be a fallback path (worker failed) or legacy renderer

4. For regressions:
   - prefer writing a capture scenario and comparing against goldens (engine-side today).
