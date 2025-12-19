# KyleCode CLI — Ink/Effect TUI Stabilization & Polish Plan

> Objective: Translate the TUI programming guidance into an actionable multi-sprint roadmap that hardens the `kyle repl` experience, unblocks the engine team’s parity efforts, and preserves our minimalist system design. This document complements `CLI_PHASE1_CHECKLIST.md` by detailing the specific UI/UX investments we will execute inside the Node client.

---

## 1. Context & Motivation

* The current KyleCode CLI already spins up an Ink-based REPL (`ReplView` + `ReplSessionController`) but suffers from transcript duplication, overlay flicker, and limited hotkey/editing ergonomics during long scripted sessions.
* We recently introduced scripted playback & logging; to take full advantage, the UI must render deterministically, clean up sessions reliably, and surface guardrail feedback immediately (guardrail completions vs streaming waits).
* The engine team is calibrating parity with other coding products; they need the CLI to avoid stray sessions, show actionable guardrail hints, and accept complex input sequences (slash commands, model picker, etc.) without corruption.

---

## 2. Goals & Non-Goals

### Goals
1. **Stable Transcript & Streaming** — Immutable scrollback, single live stream per turn, and batched controller updates so scripted runs and manual REPL sessions show the same “final snapshot.”
2. **Predictable Overlays & Hotkeys** — A modal stack with focus trapping, layered key routing, and responsive layout primitives so `/models`, confirmations, and future dialogs behave consistently.
3. **Shell-Class Editing & Autocomplete** — Cursor-aware slash commands, bracketed paste handling, and history-aware keybindings that reduce friction during long problem-solving sessions.
4. **Live Slots & Performance Guardrails** — Tool progress/live status slots, shared animation clocks, and resize-aware layouts that keep diff churn low while preserving copyable output.
5. **Regression Harness & Script Mode Integration** — Extend the scripted playback harness to cover resize storms, token floods, and overlay interactions, doubling as CI coverage for the changes above.

### Non-Goals (Phase 1)
* Porting the CLI to another stack (Bubble Tea/Blessed) or introducing GPU/graphics pipelines.
* Rewriting the backend bridge; FastAPI + Effect-driven controller remains the transport.
* Implementing an offline mock provider framework (documented as a future enhancement only).

---

## 3. Workstreams & Milestones

| Sprint | Theme | Key Deliverables | “Done” Definition |
| --- | --- | --- | --- |
| **1** | UI Stability Foundations | Batched controller reducer, `<Static>` transcript, spinner throttling, deterministic finalization | Script playback of long sessions shows no repeated headers/status lines; `kyle repl` exits cleanly with ≤1 live spinner; unit snap tests updated |
| **2** | Overlays & Hotkeys | Modal stack/backdrop, key router layers, `/` palette hook, responsive model picker | `/models` overlay no longer flickers; Esc & Ctrl-K work even while streaming; scripted overlay interactions mirror manual runs |
| **3** | Editor & Autocomplete | Custom `LineEditor` (or wrapped `ink-text-input`), bracketed paste, cursor-aware slash insertion, history polish | Large pastes treated as single updates; autocomplete preserves cursor; history navigation works during active streams |
| **4** | Live Slots & Stress Tests | Live slot registry, shared animation clock, resize-aware panes, scripted stress suites | Tool progress updates don’t duplicate lines; resizing during scripts produces a single clean snapshot; new integration tests guard regressions |

---

## 4. Sprint 1 — UI Stability Foundations

1. **Microtask-Batched State Updates**
   * Buffer SSE/model events inside `ReplSessionController` and flush via `queueMicrotask` (or Effect scheduler) to guarantee only one render per frame.
   * Document the API contract so future controllers hook into the same batching primitive.
2. **Immutable Transcript via `<Static>`**
   * Split conversation entries into `final`, `streaming`, and `live` buckets.
   * Finalized entries render inside `<Static>`; only the current assistant turn and live slots remain mutable.
   * Introduce an explicit `finalizeTurn(id)` reducer that fires only once per backend completion event.
3. **Spinner & Status Hygiene**
   * Share a single spinner interval at ~100–125 ms.
   * Ensure only one pending-response status line exists; guard against duplicate “Slash commands…” headers.
4. **Testing & Verification**
   * Update scripted playback cases (e.g., `scripts/policy_script_clean_final.txt`) to assert against the new transcript shape.
   * Add Jest/Ink snapshot tests for “long transcript with static history + live tail.”

---

## 5. Sprint 2 — Overlays & Hotkeys

* Implement `ModalHost` with backdrop + focus trap; migrate `/models` overlay into it and define stack semantics (`hidden → opening → open → closing`).
* Add a layered key router (`modal > palette > editor > global`) so Esc, Ctrl-K, `/`, and arrow navigation work even during heavy streaming.
* Make the model picker responsive (`useStdoutDimensions`) and debounce open/close to remove flicker.
* Extend scripted input files to include overlay usage (open `/models`, navigate, confirm) and assert post-run snapshots only show the final overlay state.

---

## 6. Sprint 3 — Editor & Autocomplete

* ✅ Introduce a `LineEditor` component (either bespoke or a wrapped `ink-text-input`) that tracks cursor position separately from the value.
* ✅ Enable bracketed paste mode (`\x1b[?2004h`) when the editor holds focus; treat pastes as a single update and disable on unmount.
* ⏳ Rework slash autocomplete insertion to splice at the cursor and reposition correctly; ensure Tab, Shift-Tab, and Enter behave under heavy streaming.
* ⏳ Improve history navigation (Up/Down or Ctrl-P/Ctrl-N) so it remains responsive when overlays are closed and while batched updates fire.

---

## 7. Sprint 4 — Live Slots, Performance, & Stress Tests

* Build a `LiveSlots` registry (Map + subscriptions) for tool progress, guardrail notices, and other mutable “historic” elements.
* Run all animations/spinners through a single shared clock to reduce timers and flicker.
* Make the layout responsive (panel widths tied to terminal columns) and add a “clear screen on drastic shrink” utility per Appendix C of the manual.
* Extend the scripted playback harness to simulate:
  * Resize storms (rapid `SIGWINCH` events),
  * Token floods (fast SSE bursts),
  * Paste floods (large bracketed pastes).
* Capture baseline snapshots and add them to CI to lock in behavior.

---

## 8. Dependencies & Coordination

* **Backend Engine** — No new endpoints required during Phase 1, but we depend on stable SSE payloads and guardrail completion summaries. Already aligned with the FastAPI bridge additions documented in `docs/phase_5/ENGINE_DEV_STATUS.md`.
* **Environment** — `.env` must include valid OpenRouter/OpenAI keys; load via `python-dotenv` before CLI launch (already in place).
* **Testing Harness** — Reuse the scripted playback runner plus Ink snapshot tests; coordinate with engine dev to replay the same scripts against their protofs configs for parity.

---

## 9. Completion Criteria & Reporting

* Each sprint ends with:
  * Updated scripted playback outputs stored under `render_samples/` (or equivalent) showing “final snapshot only.”
  * Added/updated automated tests (unit + integration) covering the new behavior.
  * A checklist update in `CLI_PHASE1_CHECKLIST.md` or equivalent status doc.
* Phase “Done” when:
  * Scripted runs (long + complex) produce a single deterministic transcript with no duplicated headers or orphaned sessions.
  * Manual REPL users experience the same ergonomics (modals, editing, live slots) as scripted playback.
  * Engine developers confirm parity scripts (e.g., protofs) complete without CLI-side blockers.

---

## 10. Reporting Cadence

* **Daily** — Update the built-in TODO/plan tracker with sprint item status; note any regressions or blockers.
* **Per Sprint Exit** — Summarize in `docs/phase_5/ENGINE_DEV_STATUS.md` (or successor) so the engine developer sees which CLI capabilities are ready for parity testing.
* **Ad-hoc** — If guardrail/provider behavior changes, document in `docs/phase_5/WORKSPACE_PROVIDER_BLOCKAGE.md` so both frontend and engine teams stay aligned.

---

By executing this plan sequentially, we tighten the CLI ↔ engine feedback loop, ensure scripted playback mirrors user-visible frames, and give the engine team a stable surface for their parity work—all while respecting the minimalist architecture we’ve committed to.
