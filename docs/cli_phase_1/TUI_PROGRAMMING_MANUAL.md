Total output lines: 622

# KyleCode TUI Architecture & Behavior Deep‑Dive

**Target stack:** Node 18+, Ink (React renderer for terminals), Effect runtime, TypeScript.
**Secondary stacks for comparison:** Blessed/neo‑blessed (Node), Charm (Bubble Tea/Lip Gloss/Glamour in Go), Gum (shell UI wrapper), termbox/ncurses family.

> This briefing is tailored to the current KyleCode CLI skeleton: `kyle repl` boots an Ink view (`ReplView`) via a `ReplSessionController`, streams events, shows a model picker overlay, and manages rerenders from a controller `.onChange()` callback. The code already includes `/models` overlay, suggestions, a spinner, and a plain conversation feed, so the guidance below is incremental and directly mappable to your files.

---

## Where you are today (as anchors for recommendations)

* **Ink entry + state bridging.** `repl/command.tsx` constructs a `ReplSessionController`, subscribes to its `.onChange`, and calls `ink.rerender(<ReplView .../>)`. It also cleanly unmounts the Ink instance on exit. This is a solid foundation for a single‑root “TUI app” that we can harden. 
* **Main screen.** `ReplView` renders: header, status glyphs, conversation list, tool log, slash suggestions, and a model picker overlay that grabs focus when `modelMenu.status !== "hidden"`. Keyboard handling flows through `useInput`; `ink-text-input` manages the prompt. 
* **Streams, commands, and API.** Phase‑1 spec defines `/sessions`, `/events` (SSE/WebSocket), and core event types you’re rendering (`assistant_message`, `tool_event`, `status`, `completion`). Quick‑start and handoff describe end‑to‑end run and the intent to match Claude‑style UX.
* **Open issues/next steps.** Checklist and handoff call out polish items around completion UX, `/models` behavior, reconnection, and streaming snapshots. This doc addresses those with production‑grade patterns.

---

# 1) Static scrolling conversation log (immutable scrollback + live updates)

### What Claude‑class CLIs do

* Keep **copyable** scrollback (real text in the terminal buffer).
* Allow **in‑place updates** to a handful of earlier lines (e.g., a step that finishes, a tool status going from “running” → “done”).
* Use a **single full‑screen render** most of the time; avoid raw cursor hacks unless targeting very high‑frequency status tickers.

### Ink patterns that map cleanly

**A. “Static above, live below” with `<Static>` and a current frame**

* Use Ink’s `<Static>` to print immutable lines that never re‑render; keep only the *currently streaming message* and any *live slots* in the normal tree.

```tsx
import { Box, Text, Static } from "ink"

type LogLine =
  | {id: string; kind: "final"; text: string}
  | {id: string; kind: "streaming"; text: string}
  | {id: string; kind: "live"; render: () => string} // e.g., progress/spinner

export function Transcript({lines}: {lines: LogLine[]}) {
  const finals = lines.filter(l => l.kind === "final")
  const streaming = lines.find(l => l.kind === "streaming")
  const lives = lines.filter(l => l.kind === "live")

  return (
    <Box flexDirection="column">
      <Static items={finals}>
        {item => <Text key={item.id}>{item.text}</Text>}
      </Static>

      {streaming && <Text>{streaming.text}</Text>}

      {lives.map(l => (
        <Text key={l.id}>{l.render()}</Text>
      ))}
    </Box>
  )
}
```

* This preserves copyable scrollback while ensuring new tokens and “live” areas update smoothly. Integrate by splitting your `conversation` into `final` vs `streaming` entries; **only the still‑streaming assistant turn** lives outside `<Static>`. (Today, `ReplView` renders all entries as a single array; this change is minimal there. )

**B. Keyed arrays + deterministic “finalization”**

* Each message/step gets a **stable `id`** and a `phase: "pending" | "final"`. When the backend emits a completion for that turn, convert that node to `final` once and push it into the `<Static>` list. Your current controller already centralizes state; add a tiny reducer to “finalize” items so the UI never tries to edit a `<Static>` line. 

**C. When to drop down to ANSI (save/restore)**

* Prefer Ink reconciliation for 99% of cases. Consider raw cursor ops only when:

  * You need **sub‑100ms tickers** on a line that sits 100+ lines above the prompt.
  * You’re rendering **ASCII progress bars** at ≥30 FPS.
* If you do, *reserve* a line with a unique token (e.g., `⟪slot:task-42⟫`) and rewrite only that line. Never mix free‑form `stdout.write` into Ink’s region; write through a **slot manager** (see §3) to avoid tearing/double‑printing.

**D. Virtualization & windowing**

* For very large transcripts, keep only the last N “final” nodes in memory for reconciliation (you still have full terminal scrollback), something like:

```ts
const MAX_VIRTUAL = 1200
const finalWindow = finals.slice(-MAX_VIRTUAL)
```

Ink doesn’t ship a built‑in virtual list, but this “soft window” dramatically reduces diff cost when transcripts exceed thousands of nodes (see §8).

---

# 2) Overlays / modals that paint 
[... omitted 366 of 622 lines ...]

→ UI state
                                                    │
                                                    ▼
                                                Ink render
                              ┌────────────────────────┴────────────────────────┐
                              │                ModalHost                         │
                              │  ┌──────── base app (Transcript, Tools) ───────┐│
                              │  │ <Static finals> + current stream + slots    ││
                              │  └──────────────────────────────────────────────┘│
                              │  [Backdrop]   [Top Modal] (Model Picker, etc.)  │
                              └─────────────────────────────────────────────────┘
```

**Live slots**

```
Controller updates slot("tool:42", "Running 67%")
         │
         ├──> LiveSlots store (Map + subscriptions)
         │
         └──> <LiveSlot id="tool:42"/> re-renders only itself
```

---

# Appendix B — Slash input & autocomplete (cursor‑safe insertion)

When a suggestion is accepted (Tab), **splice** at the current cursor:

```ts
function applyAutocomplete(before: string, after: string, suggestion: string) {
  const inserted = suggestion // "/model openrouter/x-ai/grok-4-fast"
  const value = before + inserted + after
  const cursor = before.length + inserted.length
  return { value, cursor }
}
```

If you keep `ink-text-input`, track a `desiredCursor` ref and re‑focus the input, then send `←/→` programmatically by adjusting the value string with a placeholder at the cursor and an inverse block (as shown in the custom `LineEditor`).

---

# Appendix C — Resize & full redraw

On drastic downsizes (e.g., 200→80 columns), do a one‑time **clear** to avoid partial wraps:

```ts
import ansiEscapes from "ansi-escapes"
process.stdout.write(ansiEscapes.clearScreen)
process.stdout.write(ansiEscapes.cursorTo(0, 0))
// then let Ink paint the frame
```

Use sparingly; normal size changes should rely on Ink diffs.

---

# Migration plan (incremental, matches Phase‑1 scope)

**Sprint 1 (UI stability)**

* [ ] Batch controller updates per microtask.
* [ ] `<Static>` for finalized lines; keep only current stream mutable.
* [ ] Spinner interval to 100–125 ms; verify no duplicate spinners. 

**Sprint 2 (Overlays & hotkeys)**

* [ ] Introduce `ModalHost` + stack; move model picker into it.
* [ ] Key router layers; bind `Esc`, `Ctrl‑K`, `/`.
* [ ] Debounce modal close with a 1‑tick “closing” phase to end flicker.

**Sprint 3 (Editor & paste)**

* [ ] Enable bracketed paste; treat as a single update.
* [ ] Implement `LineEditor` (or extend `ink-text-input` wrapper) for shell‑class controls.
* [ ] Autocomplete insertion with cursor placement.

**Sprint 4 (Performance & tests)**

* [ ] Live slots for tool progress; single animation clock.
* [ ] Resize responsiveness; panel width tied to columns.
* [ ] Add stress tests (resize storms, token floods, paste floods).

These steps align with the **Phase‑1 CLI goals** and polish items from the handoff/checklist while preserving the current architecture (Effect + Ink + controller store).

---

## References to the current codebase (for quick cross‑checking)

* `repl/command.tsx` — Ink instance lifecycle and rerender subscription. Use this file to add **batched updates** and SIGINT cleanup tweaks. 
* `repl/components/ReplView.tsx` — Conversation, tool log, suggestions, overlay, spinner; integrate `<Static>`, `ModalHost`, and responsive panel width here. 
* Phase‑1 Spec & Quick‑Start — Event types, API endpoints, and expected REPL/`/models` behavior you’re implementing and polishing.
* Handoff V2 — Architectural summary, current strengths/risks, and next‑engineer action plan; our proposals dovetail with those priorities. 
* Checklist — Track the slash‑command UX, remote streaming toggle, and reconnection work as you land the changes above. 

---

### Final notes

* Keep one **authoritative state store** (your controller) and render from it; avoid multi‑source local component state except for transient UI like cursor position and a modal’s local search string (as you already do for the model picker).
* Use **`<Static>`**, **modal stack**, **batched updates**, and **single animation clock** as your core pillars. These four changes eliminate the most common sources of TUI instability under rapid streaming and align your REPL with the “Claude Code‑class” feel outlined in your Phase‑1 documents. 

If you want, I can turn the snippets above into concrete PRs touching only `ReplView.tsx` and `repl/command.tsx`, plus a small `LiveSlots.ts` utility and `ModalHost.tsx`.