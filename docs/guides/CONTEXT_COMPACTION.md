# Context compaction

BreadBoard can shrink a model's request history when it gets close to, or
past, the context window. Each supported harness's compaction is a **preset**:
a YAML recipe built from shared primitives, checked against that harness's
captured behavior. Compaction is **off by default**. When it is off, requests
are exactly what they were before this subsystem existed.

Code: `breadboard_engine/compaction/`. Tests: `tests/compaction/`.

## Turning it on

Add a `compaction` block to an agent config. `preset` picks the harness
behavior; the default is `omp@18.4.5`. The other keys are that harness's own
setting names, plus a few BreadBoard keys that work with every preset.

```yaml
compaction:
  enabled: true
  preset: pi@0.73.1
  reserveTokens: 16384      # Pi's setting
  keepRecentTokens: 20000   # Pi's setting
  overflowPolicy: compact   # BreadBoard: compact | terminal
```

| BreadBoard key | Meaning |
|---|---|
| `enabled` | Off unless `true`, whatever the harness default is. |
| `preset` | A packaged preset below; defaults to `omp@18.4.5`. |
| `overflowPolicy` | Optional; overrides the preset's `overflow.policy`. `compact` retries after compacting; `terminal` lets the overflow error end the run. |
| `contextWindow` | Optional; otherwise provider or model metadata. |
| `summaryModel` | Optional; defaults to the turn's model. |
| `maxPassesPerTurn` | Optional; overrides the preset's overflow attempts per turn. |

An unknown key, a bad value or an unknown preset fails config loading with
`invalid compaction config: ...` and lists the accepted names.

### `omp@18.4.5`

The block is read by OMP's own settings parser. camelCase and snake_case both
work, and the legacy `strategy`/`remoteEnabled` keys migrate to
`methodOrder` the way OMP's `settings.ts` does.

```yaml
compaction:
  enabled: true
  methodOrder: [remote, snapcompact, handoff, shake, soft]
  reserveTokens: 16384      # default: max(16384, 15% of window)
  keepRecentTokens: 20000
  thresholdPercent: -1      # -1: use window minus reserve
  thresholdTokens: -1
  maxPassesPerTurn: 2
  prune:
    enabled: false
  snapcompact:
    systemPrompt: none      # none | all | agents-md
    toolResults: none       # none | all
    shape: auto
```

| Stage | Kind | What it does |
|---|---|---|
| `remote` | boundary | Provider-native compaction. OpenAI Responses `/responses/compact` (V1 and streaming V2) and Anthropic on-demand compaction (`compact-2026-09-04` beta). `remoteEndpoint` posts to a custom endpoint instead. |
| `snapcompact` | boundary | Renders older history into PNG text frames (OMP bitmap fonts) and keeps recent turns as text. Vision models only. Needs the `snapcompact` extra (Pillow). |
| `handoff` | boundary | Summarizes the history before the cut point into a handoff document addressed to a successor, keeping recent turns. Not used for overflow recovery. |
| `shake` | edit | Replaces large tool results and fenced/XML blocks outside the protected tail with `[shaken ~N tokens]` placeholders. |
| `soft` | boundary | The OMP structured summary (Goal, Progress, Next Steps, Critical Context, files read and modified), with split-turn prefix summaries and updates on top of earlier summaries. |

Stages run in `methodOrder` until one gets the view under the target. These
run outside the pipeline:

- **Pruning** (`prune.enabled`) runs while each request view is built. It
  replaces superseded file reads and uneventful tool results with notices once
  savings pass `prune.minimumSavings`.
- **Image dropping** (`drop_images_now`) replaces every image with
  `[image removed]`.
- **Inline snapcompact** (`snapcompact.systemPrompt` / `toolResults`) renders
  the system prompt or large old tool results as images in the request view
  for vision models. The stored history is not changed.

### `pi@0.73.1`

Accepts Pi's `reserveTokens` (default 16384) and `keepRecentTokens` (default
20000). Behavior, from `@mariozechner/pi-coding-agent` 0.73.1:

- Threshold: before the first request after user input, when the last
  response's usage (`totalTokens`, or the sum of input, output and cache
  tokens) is over `window - reserveTokens`. No usage yet means no check.
- Cut: walk back from the newest message until `keepRecentTokens` is reached;
  cut at the next user or assistant message. A cut inside a turn summarizes
  the turn prefix separately.
- Summary: Pi's prompts, unescaped transcript, `<read-files>` and
  `<modified-files>` tags, and one summary message in Pi's wording.
- Overflow: one compact-and-retry until a response succeeds.

### `openhands_sdk@1.47.0`

Accepts `max_size`, `keep_first`, `max_tokens`, `minimum_progress`,
`hard_context_reset_max_retries` and `hard_context_reset_context_scaling`.
Event-count and input-token pressure select an atomic prefix/suffix view.
Summaries replace forgotten events at their offset and coalesce adjacent users.
Hard pressure can fall back to bounded whole-view reset retries; soft failure
keeps the view unchanged. The preset uses compact-and-retry overflow recovery.
The SDK's direct-agent no-condenser default is not an enabled compaction recipe.

### `codex@0.139.0`

Accepts `model_auto_compact_token_limit`, `model_auto_compact_token_limit_scope`,
`compact_prompt`, `phase`, `needs_follow_up` and `features`. Uses byte-based
accounting and the 90% window limit, retains user text within a 20,000-token
budget, and installs the pinned handoff summary. `RemoteCompactionV2` selects
native replacement with a 64,000-token retained-user budget. Ordinary sampling
overflow is terminal. These cases are source-derived, not a full Codex runtime.

### `claude_code@2.1.63`

Accepts `autoCompactEnabled`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` and
`DISABLE_MICROCOMPACT`. Threshold compaction summarizes whole history and
installs the pinned bridge and automatic continuation. Each request can mask
old tool outputs after the threshold check, preserving recent calls and savings
gates. Ordinary overflow is terminal. Bundle captures cover these operations,
not Claude Code's full attachment, persistence or routing behavior.

### `hermes_agent@2026.9.11`

Accepts `threshold`, `context_length`, `max_tokens`, `threshold_tokens`,
`protect_first_n`, `protect_last_n`, `tail_mode`, `min_tail_user_messages`
and `target_ratio`. Prunes duplicate tool results, selects a decaying protected
prefix/tail, and writes checkpoint summaries with alternation-safe carriers.
Merged carrier facts live in the ledger, not provider messages. Empty and
length-truncated summaries fail explicitly. Overflow compacts and retries.
Captured compressor helpers do not establish parity with the whole agent.

### `openclaw@2026.9.4`

Accepts `keepRecentTokens`, `mode` and `identifierPolicy`. Uses a floored,
capped reserve, Pi-style recent-token selection and the pinned user summary
wrapper. Safeguard mode audits required sections and identifiers before
accepting the summary. Overflow compacts and retries. Captures cover reserve,
selection, quality checks and placement, not all OpenClaw hooks or runtime.

### `mini_swe_agent@2.4.6`

Accepts no harness-specific settings. No proactive threshold compaction runs.
The request pass renders tool observations through the pinned clipping
template, including the exact 10,000-character boundary. Overflow is terminal.
This preset does not add summarization to a harness that has none.

### `opencode@1.2.17`

Accepts `compaction.auto`, `compaction.prune` and `compaction.reserved`,
as dotted or nested settings. Provider totals trigger at usable input capacity.
Request pruning runs at user-turn start after threshold summarization, protects
recent turns and tool tokens, exempts skill outputs, and requires over 20,000
saved tokens. Overflow can exclude and replay the latest user when an earlier
user remains. Summaries use pinned media placeholders and the summary bridge.
Prune timing is a user-turn-start approximation, not upstream post-turn timing.

### `oh-my-opencode@3.10.0`

Accepts the same settings. Adds largest-first output masking toward half the
failing request's parsed provider limit before overflow summarization.
Sufficient masking avoids the summary; otherwise the summary pipeline runs.
Pinned continuity instructions preserve task context. The unwired preemptive
path remains disabled. External todo restoration, arbitrary hooks, destructive
storage, debounce timers and live tool-after truncation are not cloned.

## When it runs

| Trigger | What happens |
|---|---|
| Threshold | Before a model request, if the preset's trigger fires, the pipeline runs with reason `threshold`. |
| Overflow | If the provider rejects a request as too long (`context_length_exceeded`, "prompt is too long", and the other OMP patterns), the pipeline runs with reason `overflow` and the request is retried, up to the preset's attempts per turn. With `overflowPolicy: terminal`, the error ends the run as before. |
| Manual | `CompactionController.compact_now()` and `drop_images_now()`. |

Provider runtimes never copy provider error text into their exceptions. They
classify overflow at the SDK boundary and set
`details.code = "context_length_exceeded"`. Overflow errors do not count as
route-health failures, so they cannot open the route circuit before the retry.

Every run records lifecycle events: `compaction_started` (preset, reason,
tokens, target), `compaction_record_appended` per record, and
`compaction_finished` with one entry per stage. Each stage reports
`committed`, `edited`, `noop`, `unavailable` or `failed`. Successful stages have
no detail; noop, unavailable and failed stages explain why they did not commit.
No stage is skipped silently. A cancelled run ends with
`compaction_finished` status `cancelled`, and the cancellation propagates.

`request_view` lists OMP builtins or declared stage IDs. Ledger-producing
entries run in order after threshold compaction, then the controller projects
the wire messages. `omp_inline_snapcompact` may only be the final entry.
Request stages use reason `request`, the threshold target, and one aggregated
`compaction_finished` event with every stage result. A stage's own estimator
always overrides the recipe's estimator. Ledger metadata stays in record details.

Summary stages call the turn's provider runtime with no tools and no
streaming. Each summary request is recorded as
`meta/requests/turn_N_compaction_K.json`. The retried request after an
overflow is `turn_N_attempt_K.json`, and its `extra` carries
`compaction_record_id` and `compaction_first_kept_index`.

## History model

The history (`SessionState.provider_messages`) is never rewritten.
Compaction appends a `CompactionRecord` (`bb.compaction_record.v1`) to
`SessionState.compaction_state`, and each request view is a projection:

- system messages at the head;
- for a record with `prefix_end`, the messages before it (harnesses that keep
  an early prefix);
- the latest boundary's summary messages;
- the messages from `first_kept_index` onward, with every record's message
  edits applied.

Cut points never separate a tool call from its result. A record that leaves an
orphaned tool result is rejected before it is appended.

Native remote payloads (OpenAI compaction items, Anthropic compaction blocks)
are replayed only to the same provider, API and model that produced them.
Runtimes without a compaction port get the plain-text summary instead. Session
snapshots include `compaction_state` once a record exists.

## Presets and primitives

A preset is `breadboard_engine/compaction/presets/<harness>@<version>.yaml`
(schema `bb.compaction_preset.v1`), with byte-exact prompt copies and a
`SOURCE.json` under `presets/prompts/<harness>@<version>/`. It names kinds
from `primitives/` and their parameters:

| Part | Kinds |
|---|---|
| `estimator` | `bb_chars4`, `pi_chars4`, `bytes4`, `chars4_round`, `event_count`, `chars_div4_floor` |
| trigger `accounting` | `max_usage_estimate`, `usage_plus_trailing`, `provider_total`, `estimate_only` |
| trigger `limit`, `target` | `omp_settings`, `window_minus_reserve`, `window_fraction`, `output_reserve_plus_buffer`, `fixed`, `effective_budget`, `floored_capped_reserve`, `input_or_window_reserve` |
| stage `select` | `recent_tokens`, `prefix_suffix_events`, `whole_history`, `user_messages_budget`, `latest_tool_outputs`, `decaying_prefix_tail`, `visible_tool_outputs`, `protected_tool_outputs`, `largest_first_masking` |
| stage `reduce` | `summarize`, `event_summary`, `chat_summary`, `mask_outputs`, `duplicate_tool_results`, `observation_clip`, `checkpoint_summary`, `message_summary` |
| stage `place` | `template`, `offset_summary`, `bridge`, `alternation_template`, `summary_replay` |
| stage `algorithm` | `omp_remote`, `omp_snapcompact`, `omp_handoff`, `omp_shake` |

`pipeline.mode` decides what happens after each stage result: `fallback`
(OMP: try the next stage until the target is reached), `sequence` (run every
stage, stop on failure) or `until_boundary` (stop at the first committed
boundary).

Stages can declare `phase: every_request` or `user_turn_start`. Compaction and
request stages share one context builder for route limits, summarization,
instructions, usage freshness, prior boundaries and numeric overflow bounds.
Provider error text stays redacted; only safe token counts reach recovery.

`native_settings.adapter: paths` maps each harness setting to recipe paths;
`omp_settings` hands the block to OMP's parser, and recipe values read it
with `{setting: <field>}`.

## Oracle tests

`tests/compaction/test_oracle_cases.py` runs every case in
`tests/compaction/oracles/<preset>/` (`bb.compaction_oracle_case.v1`) and
compares trigger decisions, selections, summary requests, summaries, file
details and projected views. Cases come from `scripts/compaction_oracles/`;
`capture_pi.mjs` executes the installed Pi package:

```bash
node scripts/compaction_oracles/capture_pi.mjs node_modules/@mariozechner/pi-coding-agent
```

Case directories for harnesses without a packaged preset are reported as
skipped, by preset name.

## RL harness

RL training treats context exhaustion as terminal. The BreadBoard Pi RL
target `pi@0.73.1` has `policy.provider.compaction: false` and is unchanged.
For eval, the `pi-r2@0.73.1` revision turns on stock Pi 0.73.1 compaction.
The pinned worker (`breadboard/rl/harness/pi_tools_0_73_1.mjs`) runs Pi's own
`prepareCompaction` and summary prompts, and recovery is attempted once per
overflow, as in stock Pi. It does not use the engine presets above.
