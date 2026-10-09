# Context compaction

BreadBoard can shrink a model's request history when it gets close to, or
past, the context window. The design follows oh-my-pi (OMP): the same
methods, cascade order, thresholds and prompts. Compaction is **off by
default**. When it is off, requests are exactly what they were before this
subsystem existed.

Code: `breadboard_engine/compaction/`. Tests: `tests/compaction/`.

## Turning it on

Add a `compaction` block to an agent config. Keys are accepted in camelCase
(OMP spelling) or snake_case.

```yaml
compaction:
  enabled: true
  # Cascade, tried in order until one gets the view under target.
  methodOrder: [remote, snapcompact, handoff, shake, soft]
  reserveTokens: 16384      # default: max(16384, 15% of window)
  keepRecentTokens: 20000   # recent history kept verbatim
  thresholdPercent: -1      # -1: use window minus reserve
  thresholdTokens: -1
  contextWindow: 200000     # optional; else provider/model metadata
  overflowPolicy: compact   # compact | terminal
  maxPassesPerTurn: 2
  summaryModel: null        # optional; defaults to the turn's model
  prune:
    enabled: false
  snapcompact:
    systemPrompt: none      # none | all | agents-md
    toolResults: none       # none | all
    shape: auto
```

The legacy OMP keys `strategy` and `remoteEnabled` still work and are
migrated to `methodOrder` the way OMP's `settings.ts` does it. An invalid
block fails config loading with `invalid compaction config: ...`.

## When it runs

| Trigger | What happens |
|---|---|
| Threshold | Before each model request, if the larger of the last reported prompt usage and the local estimate is over the threshold, the cascade runs with reason `threshold`. |
| Overflow | If the provider rejects a request as too long (`context_length_exceeded`, "prompt is too long", and the other OMP patterns), the cascade runs with reason `overflow` and the request is retried. At most `maxPassesPerTurn` times per turn. With `overflowPolicy: terminal`, the error ends the run as before. |
| Manual | `CompactionController.compact_now()` and `drop_images_now()`. |

Provider runtimes never copy provider error text into their exceptions. They
classify overflow at the SDK boundary and set
`details.code = "context_length_exceeded"`. Overflow errors do not count as
route-health failures, so they cannot open the route circuit before the retry.

## Methods

| Name | Kind | What it does |
|---|---|---|
| `remote` | boundary | Provider-native compaction. OpenAI Responses `/responses/compact` (V1 and streaming V2) and Anthropic on-demand compaction (`compact-2026-09-04` beta). `remoteEndpoint` posts to a custom endpoint instead. |
| `snapcompact` | boundary | Renders older history into PNG text frames (OMP bitmap fonts) and keeps recent turns as text. Vision models only. Needs the `snapcompact` extra (Pillow). |
| `handoff` | boundary | Summarizes the history before the cut point into a handoff document addressed to a successor, keeping recent turns. Not used for overflow recovery. |
| `shake` | edit | Replaces large tool results and fenced/XML blocks outside the protected tail with `[shaken ~N tokens]` placeholders. |
| `soft` | boundary | The standard OMP structured summary (Goal, Progress, Next Steps, Critical Context, files read and modified), with split-turn prefix summaries and updates on top of earlier summaries. |

These are not cascade steps:

- **Pruning** (`prune.enabled`) runs while each request view is built. It
  replaces superseded file reads and uneventful tool results with notices once
  savings pass `prune.minimumSavings`.
- **Image dropping** (`drop_images_now`) replaces every image with
  `[image removed]`.
- **Inline snapcompact** (`snapcompact.systemPrompt` / `toolResults`) renders
  the system prompt or large old tool results as images in the request view
  for vision models. The stored history is not changed.

Summary methods call the turn's provider runtime with no tools and no
streaming. Each summary request is recorded as
`meta/requests/turn_N_compaction_K.json`. The retried request after an
overflow is `turn_N_attempt_K.json`, and its `extra` carries
`compaction_record_id` and `compaction_first_kept_index`.

## History model

The history (`SessionState.provider_messages`) is never rewritten.
Compaction appends a `CompactionRecord` (`bb.compaction_record.v1`) to
`SessionState.compaction_state`, and each request view is a projection:

- system messages at the head;
- the latest boundary's summary messages;
- the messages from `first_kept_index` onward, with every record's message
  edits applied.

Cut points never separate a tool call from its result. A record that leaves an
orphaned tool result is rejected before it is appended.

Native remote payloads (OpenAI compaction items, Anthropic compaction blocks)
are replayed only to the same provider, API and model that produced them.
Runtimes without a compaction port get the plain-text summary instead. Session
snapshots include `compaction_state` once a record exists.

## RL harness

RL training treats context exhaustion as terminal. The BreadBoard Pi RL
target `pi@0.73.1` has `policy.provider.compaction: false` and is unchanged.
For eval, the `pi-r2@0.73.1` revision turns on stock Pi 0.73.1 compaction.
The pinned worker (`breadboard/rl/harness/pi_tools_0_73_1.mjs`) runs Pi's own
`prepareCompaction` and summary prompts, and recovery is attempted once per
overflow, as in stock Pi. It does not use the BreadBoard engine methods above.
