# KyleCode TUI / UX Overhaul Plan

_Source inputs: `UX_OVERHAUL_PLANNING_MODEL_RESPONSE.md`, `HANDOFF_V3.md`, TUI stress-suite docs, live-slot plan, engine coordination notes, Codex/Gemini/OpenCode reverse-engineering dossiers (`docs/*_re/CLI_TUI_AND_STREAMING.md`, `docs/opencode_re/TUI_AND_CLI.md`)._

## 1. Goals & Guardrails
- Deliver a Claude Code / Codex-class terminal UX without adding backend bloat; changes stay within the existing Ink + Effect CLI and FastAPI bridge.
- Focus on the planner’s five pillars: clipboard/input, model menu, transcript/tool visualization, vertical real-estate, and competitive feature parity.
- Avoid feature creep: every addition should map to Sprint 2 objectives (TUI polish + parity). Track dependencies on the engine team (streaming stability, image uploads) separately.

## Status (2025-11-14)
- ✅ M1 editor work has landed: the custom `LineEditor` now handles bracketed/burst paste, collapses large payloads into chips, and exposes undo/redo. `/models` overlay renders per-million pricing with responsive columns.
- ✅ Transcript viewport refactor keeps the ASCII header + status at the top, caps the live transcript to a dynamic window, and pins the composer/hints at the bottom. `layout_ordering_pty_after.txt` verifies the new ordering.
- ✅ Mock FastAPI bridge + stress scripts (`paste_flood`, `modal_overlay_stress`, `resize_storm`, `layout_ordering_pty`) were refreshed using `mock/dev` so we have clean baselines again.
- ✅ Guardrail banner now renders as a sticky band (toggle with `e`, dismiss with `x`), tool rows show status-dot transitions, collapsed summaries cover noisy tool logs, and attachment submissions include `[Attachment …]` helper lines until multipart upload lands.
- ✅ Collapsed transcript blocks now expose per-entry controls (`[` / `]` / `e`) plus diff previews (`Δ +/−` with filenames) so long replies stay readable without losing context; the scripted renderer mirrors the same summary lines for regression snapshots.
- ✅ `/view` slash commands plus compact transcript mode keep short terminals readable and expose explicit toggles for QA; scripted snapshots now include the `Compact transcript mode active — …` hint.
- ✅ Guardrail fixtures & shared-engine snapshots refreshed (`scripts/*_cli_shared_latest.txt`, `misc/cli_guardrail_tests/*.json`) using the 20251114 log directories so replay suites finally mirror real runs.
- ✅ Harness/tooling: spectator PTY now drives `ctrl+V`, layout ordering, and attachment captures with manifests; `npm run stress:ci` launches the mock FastAPI bridge, runs the modal/layout/paste subset, verifies SSE logs + guard metrics, and zips the bundle for CI/smoke tests.
- 🔜 Next focus (dual track):
  1. **Transcript polish & live slots (M2 carryover):** Finish shared animation clocks + live-slot diff previews and wire the `/view` settings into telemetry so CI can detect regressions automatically.
  2. **Attachment enablement:** Keep iterating on the FastAPI upload path—ensure the PTY harness + `scripts/attachment_submit.json` stays green, then expose attachment-id reuse in `/retry` before moving on to engine/CI hooks.
- 📄 Attachment upload contract drafted in `docs/phase_5/ATTACHMENT_UPLOAD_PLAN.md` (FastAPI `/attachments`, CLI multipart flow); FastAPI bridge now exposes `/attachments`, so the CLI needs to finalize serialization + success hints before M3 is marked complete.

> **Industry Reference Alignment:**  
> - Codex informs the multitool entrypoint + session logging toggles we’ll mirror during the future CLI unification workstream.  
> - Gemini demonstrates Ink-native virtualization and memoized history renders for large transcripts—our M2 transcript polish borrows its “scrollable log, fixed composer” recipe.  
> - OpenCode’s extmarks, clipboard adapters, and attachment chips guide M3; we’ll follow its `[Pasted ~N lines]` and `[5KB Image Attachment]` idioms verbatim where possible.

## 2. Milestones

### M1 — Editor & Model Menu Foundation (P0)
**Scope**
- Replace remaining `ink-text-input` usage with the custom `LineEditor` that supports:
  - Bracketed paste lifecycle, burst fallback, single-op undo frames.
  - HiddenPasteStore + chip rendering for large text pastes.
  - Initial UndoManager (Ctrl+Z / Shift+Ctrl+Z).
- Refresh `/models` overlay:
  - Column headers (`Provider · Model`, `Context`, `$ / M In`, `$ / M Out`).
  - Responsive width: hide Context → $/M In → $/M Out as terminal shrinks.
  - Prices normalized to per-million tokens; context in `k`.

**Acceptance criteria**
- `paste_flood.json` & `ctrl_v_paste.json` snapshots show chips (no raw 5k-char dumps); backspace/undo remove the entire insert.
- `/models` menu resizes gracefully during `resize_storm.json`: headers visible, columns collapse in priority order, no flicker.
- Unit tests cover `HiddenPasteStore`, `UndoManager`, burst detection.

### M2 — Live Slots, Transcript Polish, Guardrails (P0/P1)
**Scope**
- Implement LiveSlotRegistry + shared animation clock (per Sprint-4 plan) for tool rows, with bash status dots (gray→green/red) and stable `<Static>` history finalization.
- Add expandable guardrail banner + diff previews + block auto-collapse rules (assistant >20 lines, aged tool logs).
- Keyboard affordances (`e` toggles expand/collapse).
- Borrow Gemini’s scroll virtualization idea for extremely long transcripts (fall back to streaming log style when `useStdoutDimensions().rows < 30`) and Codex’s typed slash-command palette so transcript controls can surface via `/view`.

**Acceptance criteria**
- Tool/batch scripts (“token flood”, tool failure scenario) show live dot transitions; guardrail banner never duplicates or pushes header off-screen.
- Auto-collapse summaries appear for older assistant/tool blocks; composer & hints always visible in 24-row terminals.
- Transcript virtualization kicks in (Gemini-style) for ≥200-row histories without breaking scripted snapshots; slash-command palette exposes `/view collapse toggle`.

### M3 — Rich Attachments & Backend Hooks (P2)
**Scope**
- ClipboardAdapter detects image payloads, computes approx size + average color, renders `[5KB Image Attachment]` chips.
- Serialize attachments for submission; gate upload on FastAPI multipart endpoint.
- Coordinate with engine for: guaranteed SSE completion events, attachment schema, default API base URL update.
- Mirror OpenCode’s extmark strategy so pasted blobs are tracked separately from visible chips; adopt Codex’s session logging toggles to note attachment metadata in transcripts.

**Acceptance criteria**
- Clipboard tests show text + image chips (image upload may be stubbed until backend ready).
- Engine contract for multipart established; docs updated (`ENGINE_DEV_REQUEST.md`, Quick Start).

## 3. Work Breakdown (per milestone)

| Task | Files/Areas | Notes |
| --- | --- | --- |
| LineEditor refactor | `src/repl/components/LineEditor.tsx`, `inputChunkProcessor.ts`, `terminalControl.ts` | Integrate HiddenPasteStore, UndoManager, chips, bracketed paste control. |
| Clipboard adapter | `src/util/clipboard.ts` | Platform probes (osascript/xclip/powershell); support `KYLECODE_FAKE_CLIPBOARD`. |
| Responsive model overlay | `src/repl/components/ModelMenu.tsx` (or `ReplView` overlay), `providers/modelCatalog.ts` | Column width calc via `useStdoutDimensions`; price/context normalization. |
| Live slots + animations | `src/repl/components/ReplView.tsx`, `src/repl/liveSlots.ts` | Shared clock, dot transitions, finalize to `<Static>`. |
| Guardrail banner & diff previews | `src/repl/components/TranscriptBlocks.tsx` | Sticky band, expand/collapse controls, block summaries. |
| Image attachments | `ClipboardAdapter`, `LineEditor`, `api/client.ts` (serialization) | Placeholder chips until backend upload is live. |

## 4. Dependencies & Coordination
- **Engine streaming:** Need at least one SSE event per session; tracked via `ENGINE_DEV_REQUEST.md`.
- **Multipart attachment endpoint:** Collaborate with bridge owners during M3; reference `ENGINE_FASTAPI_CLI_CONNECTION_PLAN.md`.
- **Telemetry & metrics:** Add optional logging counters for collapsed blocks, spinner tick rate, etc., during M2.

## 5. Tracking & “Done” Definition
- **Milestone Done** when all tasks + acceptance criteria above are implemented, scripted tests updated, docs refreshed, and stress suite snapshots regenerated.
- **Sprint Done** once M1 + M2 scopes land (P0 parity items satisfied) and engine dependencies are unblocked or documented for M3.

## 6. Next Immediate Actions
1. **Live Slot polish (M2):** wire the shared animation clock into `LiveSlot` + scripted renderers so bash status dots and diff previews stream identically in Ink and `renderText`. Re-run `scripts/token_flood.json` + `scripts/modal_overlay_stress.json` after each tweak.
2. **CI-ready guardrail metrics:** point `scripts/ci_guardrail_metrics.sh` at the refreshed 20251114 log bundle and document the glob in the replay README so CI can diff guard counts automatically.
3. **Attachment serialization (M3 bootstrap):** plumb queued attachments through `/retry` + history, then script the PTY harness (`scripts/attachment_submit.json`) in CI so uploads break loudly if `/attachments` regresses.

## 7. Open Issues
- **Layout ordering regression (M2)** — the PTY stress (`scripts/layout_ordering_pty.json`) still records two anomalies where finalized assistant messages escape above the header during resize storms. See `artifacts/stress/layout_ordering_failure/20251117-152135` for the captured run and `docs/cli_phase_1/TUI_STRESS_TESTS.md` (Known Issues) for gating guidance. Keep the case in `npm run stress:ci` so we detect regressions while a UI fix is pending.

_Last updated: 2025-11-14. Maintainer: TUI engineer on duty._
