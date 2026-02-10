# TUI Skeleton Mechanics Manual (Ink Scrollback TUI)

This is an engine-developer-facing “how it works in practice” manual for the Ink/React TUI in `tui_skeleton/`.

It complements (and intentionally overlaps with) `docs/TUI_SKELETON_ARCHITECTURE_AND_RECENT_CHANGES.md`:
- Architecture doc: faster orientation + pointers.
- This manual: deeper “what happens when” details, debugging workflows, and the internal UI baselines/gates we added.

Scope:
- The Ink scrollback TUI in `tui_skeleton/`.
- The deterministic “internal UI baselines” harness (U1/U2/U3) that replays event JSONL into the controller and snapshots text output.
- The tmux capture tooling we use to produce high-frequency `.txt` panel captures and `.png` renders for external TUIs (Claude Code, Codex CLI) and our own UI.

Non-goals:
- This is not a full engine architecture or provider contract spec.
- This does not document OpenTUI (`opentui_slab/`) beyond a brief “where it differs” section.

---

## Directory Map (What Matters, What Doesn’t)

High-signal directories inside `tui_skeleton/`:
- `src/commands/repl/`: entrypoint, controller wiring, classic scrollback renderer (`renderText.ts`)
- `src/repl/components/replView/`: the Claude-like UI shell
- `src/markdown/`: stream-mdx integration (worker + streaming blocks)
- `src/repl/shikiHighlighter.ts`: Shiki theme + token highlighting
- `scripts/`: internal baseline runner / comparer / blesser + wrappers used by CI
- `ui_baselines/`: tracked deterministic UI baseline fixtures (U1/U2/U3)

Lower-signal directories:
- `dist/`: compiled JS for publishing; avoid editing directly
- `tools/mock/`: mock SSE/replay tools, mostly for local debugging

---

## Runtime Startup (From `node ... main.ts` to Rendering)

### Entrypoints

Primary CLI entry:
- `tui_skeleton/src/main.ts`

Ink REPL command:
- `tui_skeleton/src/commands/repl/command.tsx`

Two broad runtime modes:
- Ink scrollback (“classic”): state is rendered to lines via `renderStateToText()` and displayed in an Ink layout.
- “Claude-like shell”: a higher-level component tree produces structured panels and uses the controller state to build view nodes.

TUI mode selection typically flows through:
- CLI args like `repl` and `--tui ...`
- `BREADBOARD_TUI_MODE`

### Engine Supervision

The CLI typically ensures an engine is available via:
- `tui_skeleton/src/engine/engineSupervisor.ts`

The REPL UI consumes an SSE event stream emitted by the engine and translates that into controller state updates.

---

## The Repl Session Controller (State + Event Ingestion)

Core file:
- `tui_skeleton/src/commands/repl/controller.ts`

The controller is effectively a state machine with:
- Session lifecycle (start/attach, reconnect)
- Event ingestion (SSE events -> internal event model)
- User input submission (compose input -> submit -> wait for completion markers)
- Tool call / tool result tracking
- Render preference tracking (collapse, rich markdown on/off, etc.)

Key idea:
- The controller should keep raw content as faithfully as possible.
- The *view* may collapse/hide content for readability/performance, but that is presentation-only.

### “Lines Hidden …” Is A View Feature

If the UI shows:
- “N lines hidden …”
- “block X/Y …”

That is a collapse UI affordance, not truncation of raw assistant content. The controller still holds the underlying content, and the UI can expand it.

Primary collapse logic lives in:
- `tui_skeleton/src/repl/transcriptUtils.ts`

---

## Markdown Streaming (stream-mdx Worker)

Files:
- `tui_skeleton/src/markdown/streamer.ts`
- `tui_skeleton/src/markdown/workerThread.ts`

Summary:
- Assistant text deltas stream in.
- A worker thread incrementally parses markdown into a snapshot graph of blocks.
- For code blocks and diff blocks, stream-mdx emits token-level styling information (foreground color tokens).

Why the worker matters:
- We get both code structure and tokenization while content is streaming.
- When used for diffs, we can combine:
  - syntax-highlighted foreground tokens
  - diff add/del background blocks

Failure mode:
- If the worker cannot start, the system falls back to simpler (less structured) rendering.
- A common symptom is “we lost syntax highlighting” or tables render as plain pipes.

---

## Syntax Highlighting (Shiki)

File:
- `tui_skeleton/src/repl/shikiHighlighter.ts`

High-level:
- We use Shiki to color code tokens.
- Our default theme is intended to match the overall TUI palette while preserving contrast over diff backgrounds.

Important: theme unification
- We prefer a single theme constant used everywhere Shiki is invoked.
- Current default is intended to be `andromeeda`.

Operational constraints:
- The highlighter is lazy-loaded to keep startup fast.
- Large blocks may be skipped to avoid freezing the UI; this is an explicit tradeoff.

---

## Tool Artifacts: Write(...) and Patch(...)

### Render Intent (Claude-like)

Write tool:
- Show only a concise header (`Write(path)`) and a small preview block.
- For “write” previews, preserve syntax highlighting.
- The preview block background (add-color) should match the add-line background used by diffs.

Patch tool:
- Show a concise header (`Patch(path)`).
- Render unified diff bodies (no full diff headers unless necessary).
- Apply diff backgrounds:
  - green-ish background for `+` lines
  - red-ish background for `-` lines
- Apply inline (token-span) emphasis for changed substrings inside `+`/`-` lines.

Core renderer:
- `tui_skeleton/src/commands/repl/renderText.ts`

Inline diff span logic:
- `tui_skeleton/src/repl/diff/inlineDiff.ts`

### Practical Constraints

Very large diffs:
- The UI may cap rendered output and show “... (N lines hidden)” to avoid scrollback blow-ups.
- This is presentation-only and should not destroy raw content.

---

## Horizontal Rules (`---`) And “Full Width” Dividers

Renderer behavior (classic scrollback):
- When the assistant outputs a markdown horizontal rule (`---`), we normalize that to a full-width divider that matches the input bar’s top/bottom rules.

Why:
- It avoids “short dashed” lines that look accidental.
- It matches Claude Code taste.

Implementation lives in:
- `tui_skeleton/src/commands/repl/renderText.ts`

---

## Status Line (“Cooking … / Cooked …”) And Input Bar Layout

We mimic Claude Code style:
- An active status line while the agent is running: “Cooking …”
- A completion status line afterwards: “Cooked for …”

Placement:
- “Cooked …” line should be immediately above the input bar.
- “? for shortcuts” remains below the input bar.

Input bar:
- `tui_skeleton/src/repl/components/replView/composer/ComposerPanel.tsx`
- `tui_skeleton/src/repl/components/LineEditor.tsx`

Common layout bugs:
- Off-by-one width causing borders to wrap or “tear”.
- An extra blank line inside the input bar (usually a padding/margin or a double-render of a rule).

---

## Internal UI Baselines (U1/U2/U3)

Engine goldens answer:
- “Given a stored engine replay, does the engine reproduce the same contract outputs?”

UI baselines answer:
- “Given a deterministic `SessionEvent` stream, does the TUI render the same snapshot output?”

Baselines live in:
- `tui_skeleton/ui_baselines/`

Scripts:
- `tui_skeleton/scripts/run_tui_goldens.ts`
- `tui_skeleton/scripts/compare_tui_goldens.ts`
- `tui_skeleton/scripts/bless_tui_goldens.ts`

Wrappers used by CI:
- `tui_skeleton/scripts/tui_u1_goldens_report.ts`
- `tui_skeleton/scripts/tui_u1_goldens_strict.ts`
- `tui_skeleton/scripts/tui_u2_goldens_report.ts`
- `tui_skeleton/scripts/tui_u2_goldens_strict.ts`
- `tui_skeleton/scripts/tui_u3_goldens_report.ts`
- `tui_skeleton/scripts/tui_u3_goldens_strict.ts`

CI workflows:
- `breadboard_repo/.github/workflows/tui-u1-goldens.yml`
- `breadboard_repo/.github/workflows/tui-u2-goldens.yml`
- `breadboard_repo/.github/workflows/tui-u3-goldens.yml`

### U1: ASCII, No Color (Structural)

Design:
- High determinism, minimal reliance on terminal features.
- Snapshot equality after normalizations (timestamps/tokens/paths/status text).

### U2: Unicode + Color (Structural, ANSI-Tolerant)

Design:
- Compare structure while stripping ANSI for equality.
- Still assert that ANSI existed in the raw output (`style.require_ansi`) to catch “we accidentally disabled color”.

Important environment footgun:
- Many CI environments export `NO_COLOR`.
- Our internal “color mode resolve” logic treated `NO_COLOR` as authoritative unless overridden.
- The manifest’s `render.color_mode: truecolor` exists to force color back on for U2/U3 scenarios.

### U3: Diff Style Gate (Backgrounds + Inline Diff)

Design:
- U3 is the first style-oriented gate:
  - checks diff add/del backgrounds exist (presence of `\u001b[48;` on `+`/`-` lines)
  - checks ANSI exists at all

This catches regressions where:
- we still render `+`/`-` lines, but without background colors
- a refactor accidentally routes diffs through a plain text path

### How To Add A New Baseline Scenario

1. Create a JSONL input stream:
- `tui_skeleton/ui_baselines/uX/scenarios/<scenario>/input/events.jsonl`

2. Add it to the manifest:
- `tui_skeleton/ui_baselines/uX/manifests/uX.yaml`

3. Render candidate:
```bash
cd tui_skeleton
node --import tsx scripts/run_tui_goldens.ts --manifest ui_baselines/uX/manifests/uX.yaml --out ui_baselines/uX/_runs --config ../agent_configs/codex_cli_gpt51mini_e4_live.yaml
```

4. Bless:
```bash
cd tui_skeleton
node --import tsx scripts/bless_tui_goldens.ts --manifest ui_baselines/uX/manifests/uX.yaml --source ui_baselines/uX/_runs/<run-dir> --blessed-root ui_baselines/uX/scenarios
```

Notes:
- By default, `bless_tui_goldens.ts` copies only `render.txt`.
- Use `--with-grid` or `--with-meta` only if you explicitly want to store those artifacts in the blessed baseline.

5. Compare strict:
```bash
cd tui_skeleton
node --import tsx scripts/compare_tui_goldens.ts --manifest ui_baselines/uX/manifests/uX.yaml --candidate ui_baselines/uX/_runs/<run-dir> --blessed-root ui_baselines/uX/scenarios --strict --summary
```

---

## tmux Panel Capture Tooling (txt + png)

This repo contains tmux capture helpers that we use for:
- “External reference baselines” (Claude Code, Codex CLI).
- High-frequency capture loops to spot transient rendering issues (wrapping, tearing, cursor quirks).

Primary scripts (repo root `scripts/`):
- `scripts/tmux_capture_poll.py`
- `scripts/tmux_capture_to_png.py`
- `scripts/run_tmux_capture_scenario.py`
- `scripts/run_claude_alignment.py` (paired Claude vs Breadboard runs + final-frame diff report)
- `scripts/claude_alignment_manifest.example.yaml` (safe-to-commit manifest example)

Recommended workflow:
1. Run a scenario driver that produces deterministic UI behavior (typed commands, waits, keypresses).
2. Poll-capture the tmux panel to `.txt` frames at a fixed interval.
3. Convert those frames to `.png` using a consistent terminal renderer so the images are stable and reviewable.

Important UI correctness gap:
- “Enter submits” vs “Enter inserts newline” depends on target app behavior and key mapping.
- Claude Code and Codex differ here; we need per-target scenario semantics.
- The scenario driver should enforce the correct behavior, and we should gate it with a small baseline capture.

---

## OpenTUI Slab Notes (Minimal)

OpenTUI lives in:
- `opentui_slab/`

It is a different UI stack and has different rendering and capture characteristics.
The internal UI baselines (U1/U2/U3) currently target only the Ink scrollback TUI.

---

## Debugging Playbooks

### When a diff loses backgrounds
- Confirm the tool result is being routed into `display.diff_blocks` rather than plain `display.detail`.
- Confirm `renderText.ts` is hitting `buildDiffBlockLines()`.
- Confirm the candidate output contains background ANSI sequences (`\u001b[48;`).
- U3 should fail when this regresses.

### When syntax highlighting disappears
- Confirm stream-mdx worker is alive (no “worker exited” banner).
- Confirm Shiki is loading and theme is stable.
- Confirm code blocks are being rendered from token lines, not raw text fallback.

### When the landing page tears/wraps
- Look for ANSI-aware width bugs.
- Ensure no line prints at exactly terminal width (some terminals wrap on the last cell).
- Confirm `max_width` is enforced consistently in baseline runs.

### When the input bar has an extra blank line
- Inspect `ComposerPanel.tsx` for double rules or stray padding/margins.
- Inspect `LineEditor` placeholder logic (it can render an extra row if the caret row is present but empty).
