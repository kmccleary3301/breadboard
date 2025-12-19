# Sprint 4 Plan — Live Slots, Animation Clock, & Stress Harness

## Goals
1. **Live Slot Registry** — Allow historical log rows (tool progress, guardrail notices, remote stream state) to update in place without rerendering the entire transcript or relying on raw ANSI cursor tricks.
2. **Shared Animation Clock** — Consolidate all spinner/progress timers so we write to the terminal at a predictable cadence (<15 FPS) regardless of how many slots are active.
3. **Stress Harness Coverage** — Extend the replay scripts to cover resize storms, paste floods, and long streaming bursts so we can diff deterministic outputs in CI.

## Architecture Overview

### Live Slot Store

```
type SlotId = `tool:${toolId}` | `guardrail:${sessionId}` | ...

interface LiveSlotState {
  text: string
  color?: string
}

class LiveSlotRegistry {
  private slots = new Map<SlotId, LiveSlotState>()
  private listeners = new Map<SlotId, Set<() => void>>()

  set(id: SlotId, value: LiveSlotState) { ... }
  subscribe(id: SlotId, listener: () => void): () => void { ... }
  clear(id: SlotId) { ... }
}
```

* Controller updates slots as streaming events arrive (e.g., `tool_call` “running 45%”).
* `ReplView` renders `<LiveSlot id="tool:123" />` components inside the transcript. Each slot subscribes via `useSyncExternalStore`, so only that specific line re-renders when text changes.
* Finalizing a tool entry moves the slot’s last value into the `<Static>` history and removes the subscription.

### Animation Clock

* Introduce a single `useAnimationClock(intervalMs = 125)` hook that:
  - Starts a `setInterval` when there is at least one slot/spinner subscribed.
  - Emits tick counts to listeners so each slot or spinner can derive its frame deterministically (no per-slot timers).
* Existing `useSpinner` adapts to use the shared clock when active, falling back to a static glyph when idle.
* Registry auto-unsubscribes slots when the owning turn completes, ensuring the clock stops when nothing is live.

## Controller Changes

1. Extend `ReplSessionController` with `liveSlots: LiveSlotRegistry`.
2. When handling events:
   * `tool_call`: `liveSlots.set("tool:<id>", { text: "running 0%" })`.
   * `tool_result`: finalize slot, convert to static tool log entry.
   * `reward_update` or guardrail warnings: update `liveSlots` so system notices never duplicate lines.
3. Include slot snapshots in `getState()` so scripted playback/logging can render them deterministically (the textual renderer will read from the same store).

## Stress Harness Enhancements

| Script | Focus | Notes |
| --- | --- | --- |
| `scripts/modal_overlay_stress.json` | overlay thrash | Already landed; ensures modal stack stability. |
| `scripts/resize_storm.json` | SIGWINCH bursts | Use new step type (`resize`) to simulate columns jumping between 80↔160 while streaming tokens. |
| `scripts/paste_flood.json` | bracketed paste | Types long instructions via bracketed sequences, then replays history + autocomplete to ensure we stay stable. |
| `scripts/token_flood.json` | high-frequency events | Uses wait steps to ensure `stream_event_count` hits e.g. 200, validating batching + shared clock. |

Replay command (example):

```bash
node dist/main.js repl \
  --script scripts/resize_storm.json \
  --script-output scripts/resize_storm_output.txt \
  --script-final-only
```

Outputs live under `scripts/*_output.txt` so diffs surface regressions automatically.

## Definition of Done

1. **Live slots** render inside both Ink and text snapshots, with zero duplicate transcript lines during long tool runs.
2. **Animation clock** keeps max write frequency ≤10 Hz and shuts off when idle (verified via logging or unit test).
3. **Stress scripts** added + documented; their outputs checked into `docs/cli_phase_1/TUI_STRESS_TESTS.md`.
4. CI instructions updated so replay scripts run in “final-only” mode as a pre-merge gate (at least locally until we can mock the backend).
