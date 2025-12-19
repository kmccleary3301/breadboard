# V1 TUI Realignment Plan (Claude Code–Class Experience)

This document defines the scope, behavior, defaults, and testing strategy to deliver a stable V1 of the KyleCode TUI that feels comparable to Claude Code (minus advanced features). It is no-context-needed: follow this to build, run, and verify the TUI.

---

## 1) V1 Scope & Acceptance Criteria

### 1.1 Transcript & Streaming
- Assistant streams as a single updating block per turn.
- Auto-collapse long assistant/tool blocks (>20 lines) with `[` / `]` + `e` to target/expand. Collapsed hint shows line count and `Δ +/−` summary (with up to 3 filenames).
- Compact scroll mode auto-activates on short terminals (≤32 rows) or via `/view scroll compact`; displays a hint line showing window size and hidden count.
- Guardrail banner is sticky beneath the header, unique (no duplicates), expandable (`e`) and dismissible (`x`), never pushes the header away.
- Diff/patch responses render with syntax coloring (`+` green, `-` red, `@@` amber, `diff --git` teal). Collapsed hints show `Δ +/−` and filenames.

**Accept:** For simple prompts (“hello in markdown”; “show a quick diff”), transcript shows user → assistant → system; assistant block visible; no guardrail halt or `max_steps_exhausted`. `markdown_diff` script emits fenced ```diff and completes cleanly (no `loop exit`).

### 1.2 Model Picker & Palette
- `/models` opens a responsive table: `Provider · Model | Context | $/M In | $/M Out`, collapsing columns by priority on narrow widths (Context → $/M In → $/M Out).
- Arrow keys move selection; Enter confirms; Esc cancels; overlay remains stable during streaming/resizes.
- Command palette: either a dedicated overlay or enriched slash autocomplete with descriptions; Enter/Tab selects.

**Accept:** `modal_overlay_stress` PTY case shows one header row, stable overlay, final transcript with normal assistant completion (not just tool outputs + `max_steps_exhausted`), and correct column collapse at 80 cols (validated via VT grid).

### 1.3 Input & Paste Pipeline
- Line editing: backspace, Ctrl+Backspace, word jumps, Ctrl-A/E, etc.
- Bracketed paste enabled while focused; disabled on exit/crash; no stray `~`.
- Pasting:
  - Short text (<512 chars) inserts inline.
  - Long text renders as a chip `[Pasted Content N chars]` (blue); raw content stays in buffer; undo removes chip in one step.
- Clipboard images render as `[XKB Image Attachment]` chips under the prompt; bytes sent to `/attachments` on submit and reused on `/retry`.

**Accept:** `ctrl_v_paste` shows raw text; `ctrl_v_paste_large` shows chip only; no raw 5k dumps; undo works. `attachment_submit` shows chips + `[Attachment …]` lines; `/retry` reuses IDs.

### 1.4 Guardrails & Tool/Status Logs
- Coding configs: tool calls render as structured rows with status glyphs/colors; guardrails as a single banner; user can continue immediately.
- Snapshot/UX configs: plan/todos off, zero-tool/completion guards inert, short turn limit; never halts with `max_steps_exhausted` on simple prompts.

**Accept:** Snapshot scripts (`markdown_basic_nontool`, `markdown_diff_nontool`) contain assistant text (no tool calls) and a “completed” status. Guardrail stress scripts show exactly one banner, no layout breakage.

### 1.5 Attachments & Replay
- Attachment chips under prompt; helper lines echoed into transcript; `/retry [n]` reuses attachment IDs.
- `kyle artifacts download` / `kyle render` remain functional with attachments.

**Accept:** `attachment_submit` shows chips and IDs; retry behavior noted; transcript reflects helper lines.

### 1.6 Error States & Defaults
- If bridge unreachable, `kyle repl` prints a single friendly error and exits non-zero; no hang/stack dump.
- If remote streaming misconfigured, fall back to local streaming with a single warning.

**Accept:** “Bridge down” script shows a clear error; logs explain absence of SSE; exit code non-zero.

---

## 2) Default Config Strategy (“Just run `kyle repl`”)

### 2.1 Defaults
- API URL: `http://127.0.0.1:9099` (override via `KYLECODE_API_URL`).
- Default model: `openrouter/x-ai/grok-4-fast` (override via `KYLECODE_DEFAULT_MODEL`).
- Default config: `agent_configs/opencode_openrouter_grok4fast_cli_default.yaml` (baked into `kyle repl`) tuned for Grok with guard thresholds that don’t halt simple Q&A.
- View defaults: rich markdown on (if worker present); `/view collapse auto`; `/view scroll auto`.

### 2.2 Modes (by flag/config)
- Mode A (default): Grok via OpenRouter, coding-friendly guards.
- Mode B (snapshot): Codex-mini via OpenAI Responses, plan/todos off, guards inert, short turn limit; prompt prefix forbids tools.
- Mode C (mock): CLI mock guardrails + mock SSE for deterministic tests.

### 2.3 Packaging
- After `npm install && npm run build` and starting the bridge, `kyle repl` with no flags uses the default config + Grok. No config edits required for basic use. Snapshot/mock modes are optional flags.

---

## 3) Provider Modes & Fallbacks
- **Mode A:** Grok default config; if provider errors, show banner and suggest switching to mock.
- **Mode B:** Codex-mini Responses snapshot; guarantees assistant text within ≤4 turns, no tools; prompt prefix “Answer directly. Do not call tools. Return markdown; for diffs use fenced ```diff without filenames.”
- **Mode C:** Mock SSE + mock guardrails; default for CI/stress.
- **Fallback rule:** If no SSE/assistant tokens within TTFT budget, log warning and suggest/auto-fallback to mock when `KYLECODE_ALLOW_MOCK_FALLBACK=1`.

---

## 4) Testing & CI (Deterministic V1 Gate)

### 4.1 CI Subset (mock by default)
- `mock_hello`
- `modal_overlay`
- `layout_ordering_pty`
- `resize_storm`
- `ctrl_v_paste` / `ctrl_v_paste_large`
- `markdown_basic_nontool` / `markdown_diff_nontool`
- `attachment_submit`
- One guardrail policy violation script

Real-provider variants run nightly or behind `STRESS_FULL=1`.

### 4.2 Budgets & Assertions
- Budgets: `TTFT_BUDGET_MS=2500`, `SPINNER_BUDGET_HZ=12`, `MAX_ANOMALIES=0`, `MAX_TIMELINE_WARNINGS=1`.
- Assertions: header at row 0; composer at bottom; guardrail banner unique; model table columns collapse in priority order; paste shows chips (not raw); attachments show hints.

### 4.3 Regeneration Rules
- Never hand-edit outputs; regenerate via `npm run stress:bundle -- --case <ids>`.
- Commit script inputs and text outputs; keep zips in CI artifacts, not in repo.

---

## 5) Guardrail & Prompt Regimes
- **Capture/UX tests:** plan=false, todos=false, guardrails include=[], zero-tool/completion guards inert, turn_limit≤4; system/user prompt forbids tools and asks for markdown/diff.
- **Coding workflow:** active guards/plan/tools tuned to avoid immediate halts; guardrail banner rendered cleanly; user can continue after guardrail events.
- Guardrail rendering: single banner, short explanation, no duplicate messages.

---

## 6) User Setup & Docs (Simplified)

### 6.1 Quick Start
1) Start bridge: `python -m agentic_coder_prototype.api.cli_bridge.server` (engine repo).
2) CLI: `npm install && npm run build && kyle repl` (defaults to Grok, localhost).
3) Optional:
   - Set `KYLECODE_API_URL`/`KYLECODE_API_TOKEN`/`OPENROUTER_API_KEY`.
   - Run codex-mini snapshot: `kyle repl --config agent_configs/opencode_openai_codexmini_c_fs_cli_responses_snapshot.yaml`.
   - Run mock SSE: `kyle repl --config agent_configs/opencode_cli_mock_guardrails.yaml --base-url http://127.0.0.1:9191` (after starting mock SSE server).

### 6.2 Slash Commands to Expose
- `/view markdown on|off`, `/view collapse auto|all|none`, `/view scroll auto|compact`
- `/models`, `/remote on|off`, `/retry`, `/plan`, `/mode`, `/files`

### 6.3 Trim from Quick Start
- Legacy REPL reference, deep alt-config discussion; move to “Advanced.”

---

## 7) Claude Code Parity (V1 vs Later)
- **Must-match (V1):** streaming transcript with auto-collapse; sticky guardrails; diff coloring; paste chips (text/image); responsive model picker with pricing/context; attachments with `/retry` reuse; stable overlays under streaming/resize; deterministic test harness.
- **Later:** advanced command palette, full attachment previews, deep virtualization for huge transcripts, richer plan/build dashboards.

---

## 8) Execution Sprints (Suggested Order)
- **Sprint 1: Defaults & Docs**
  - Create default Grok CLI config; wire `kyle repl` defaults.
  - Document default/snapshot/mock modes; simplify Quick Start.
- **Sprint 2: UX/Layout/Guardrail**
  - Fix modal overlay/layout ordering; ensure markdown/diff scripts stream cleanly (no max_steps halts).
  - Enforce single guardrail banner behavior.
- **Sprint 3: Input/Attachments**
  - Finalize paste chips (text/image), undo; attachment upload + hints; `/retry` reuse.
- **Sprint 4: Testing/CI**
  - Lock `stress:ci` subset; enforce budgets; improve `ttydoc` clarity; add optional real-provider runs.

---

## 9) Checklist (To-do)
- [x] Default Grok config (`opencode_openrouter_grok4fast_cli_default.yaml`) created and set as `kyle repl` default.
- [x] Codex-mini Responses snapshot config documented as “snapshot” mode.
- [x] Mock SSE mode documented; runnable via single command.
- [x] Modal/layout regression fixed; anomalies=0 in `layout_ordering_pty`.
- [x] Markdown/diff scripts produce assistant text (no tool calls, no halts).
- [x] Paste/attachment scripts passing (chips, undo, attachment IDs reused on retry).
- [ ] Guardrail banner invariant verified (single banner, no duplicates).
- [x] CI gate (`stress:ci`) passing with mock configs; budgets enforced.
- [x] Quick Start trimmed; slash commands/toggles documented; defaults clear.
