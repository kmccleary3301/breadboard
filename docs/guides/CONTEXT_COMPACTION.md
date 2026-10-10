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
  for vision models. The stored history is not changed. As in OMP, the system
  prompt (`all`) or its context-file sections (`agents-md`) are swapped only
  when the whole text fits in at most 6 frames and imaging saves tokens.
  Otherwise the text stays.

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
overflow is terminal. Five cases (local summary request, summary exclusion on
re-install, total-scope threshold, mid-turn follow-up, terminal overflow) are
executed against the real Codex 0.139.0 binary through
`scripts/compaction_lanes/mock_provider.py`
(`scripts/compaction_oracles/capture_codex_live.py`); the rest are
source-derived. The trigger stays at 90% of the window whatever the output
cap: at a 131,072 window with a 32,000 output cap Codex compacts at 117,964,
not at 99,072.

### `claude_code@2.1.63`

Accepts `autoCompactEnabled`, `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` and
`DISABLE_MICROCOMPACT`. Threshold compaction summarizes whole history and
installs the pinned bridge and automatic continuation. Each request can mask
old tool outputs after the threshold check, preserving recent calls and savings
gates. Ordinary overflow is terminal. Bundle captures cover these operations,
not Claude Code's full attachment, persistence or routing behavior.

The preset's `chat_summary.reduce.request` selects streaming, exact episode tool
names, and provider-native summary parameters. Claude uses `stream: true`,
`tools: [Read]`, and `params: {temperature: 1}` with the existing 20,000-token cap.
An unknown selected tool is an error. An absent `request` retains the original
summary invocation. Summary clients use the production provider credential lease.
The dated 2026-10-09 profile enables stock's 31,999-token thinking budget and
omits ordinary-turn temperature and tool choice. SDKs without legacy sampling
kwargs receive those wire fields through documented `extra_body`.

Run the stock/production differential lane with:

```sh
BB_WORKSPACE_ROOT=$PWD PYTHONPATH=. ~/projects/breadboard-compaction-ref-20261009/.venv/bin/python -m scripts.compaction_lanes.claude_lane --out /tmp/claude-e4-lane
```

Both sides keep the 200,000-token window and 32,000-token output cap. The lane
applies stock's supported 10% auto-compact threshold override to both runners.
It records raw bytes and compares canonical JSON without removing any field.
The lane report distinguishes stock compact boundaries from BB's compaction
events and fails on every unadmitted difference. These runs are not evidence of
full request parity until the report passes.


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
as dotted or nested settings. Provider totals trigger at usable input capacity
after each assistant step, including final answers. A summary schedules the
native continuation before completion. Pruning runs after the user-turn loop
exits, protects recent turns and tool tokens, exempts skill outputs, and requires
over 20,000 saved tokens. Overflow can exclude and replay the latest user when
an earlier user remains. Summaries use pinned media placeholders and the summary
bridge, stream with a 32,000-token output cap, and send complete history rather
than a stateful Responses delta. User-turn-end pruning is retained in the product
snapshot. Queued overflow replay text receives the ephemeral reminder from
`session/prompt.ts:630-645` only until an assistant response or another user is
appended. Canonical replay and later summary input stay raw. A completed answer
reopened for compaction does not count as an idle zero-tool step. Bound native
tool continuations carry tool results without synthesized user stubs. Lifecycle
sources are OpenCode `session/processor.ts:281-287,419-423` and
`session/prompt.ts:704-716`. Other presets keep their existing checkpoints.
Summary caps require an explicit `request` block; full-history projection and
provider-reference invalidation require `request.stateless: true` (default false).

Both OpenCode Bash catalogs declare `execution.timeout_unit: milliseconds` and
`default_timeout_ms: 120000`, from `tool/bash.ts:22,65-83`. Dispatch translates
that declaration before alias routing, honors `workdir`, and projects combined
process output to the model without unrelated budget reminders. Other catalogs
retain their existing second-based shell behavior. Explicit zero still reaches
the sandbox's 30-second fallback,
unlike stock; that shared sandbox corner is not repaired here.

### `oh-my-opencode@3.10.0`

Accepts the same settings and follows OpenCode's native overflow summarization.
Pinned continuity instructions preserve task context. The deferred plugin
`session.error` recovery hook does not precede native summarization: OpenCode
`processor.ts:359-364,420` publishes the error then compacts, while
`recovery-hook.ts:89-102,140-142` defers recovery and skips completed summaries.
Its largest-first primitive retains direct source-golden coverage. The separate
case where native classification fails but the delayed plugin hook recognizes
the `session.error` is not cloned. The preemptive path is also not cloned: it
requires `experimental.preemptive_compaction`, and the shipped dispatcher never
feeds its token cache. External todo restoration, arbitrary hooks, destructive
storage, debounce timers and live tool-after truncation are not cloned.
Pinned to the E4 catalog commit `5137df72`.

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
Overflow errors bypass unchanged-request transport retries and model fallback;
the compaction controller owns recovery. HTTP 429 and an explicit
`classification: rate_limited` remain ordinary retryable rate limits, even when
their wording mentions a token limit or an earlier chained error was overflow.

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
streaming, unless the reducer's `request` block (`chat_summary` or
`message_summary`) selects them: `stream`, exact episode tool names, provider
`params`, and `stateless` for full-history Responses summaries. The Codex,
Claude Code, OpenCode and oh-my-opencode presets set it to match their stock
summary requests. Each summary request is recorded as
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

RL targets do not use the engine presets above. Each compaction-enabled
target revision runs its harness's own compaction code, unmodified, inside the
pinned native worker. The Conductor routes only the summary requests through
the policy. The original targets (`pi@0.73.1`, `oh-my-pi@18.1.17`, ...) keep
`policy.provider.compaction: false` and are unchanged: context exhaustion ends
the episode.

| Target | Harness code that runs | Checkpoints | Overflow recoveries |
|---|---|---|---|
| `pi-r3@0.73.1` | Pi 0.73.1 `prepareCompaction` / `compact()` | overflow (error, silent, or zero-output `length`), agent_end | stock `_overflowRecoveryAttempted`: one recovery until a user or non-error assistant message resets it (conductor bound `None`) |
| `pi-r4@0.57.1` | Pi 0.57.1 `prepareCompaction` / `compact()` | overflow (error or silent), agent_end | stock `_overflowRecoveryAttempted`: one recovery until a user or non-error assistant message resets it (conductor bound `None`) |
| `oh-my-pi-r3@16.2.13` | OMP 16.2.13 session maintenance | overflow, before_request, agent_end (auto-continues only with recovery headroom) | no counter (`None`) |
| `oh-my-pi-r2@18.1.17` | OMP 18.1.17 session maintenance | overflow, before_request, agent_end (deferred handoff, drained before `prompt()` returns) | no counter (`None`) |
| `openclaw-r2@2026.9.4` | OpenClaw 2026.9.4 `compact()` | overflow, agent_end | 3 |
| `hermes-agent-r2@2026.9.11` | Hermes `ContextCompressor`, in source | the source's own | 3 per turn |
| `openhands-sdk@1.47.0` | none: the bare SDK agent has no condenser | — | terminal |
| `mini-swe-agent@2.4.6` | none: mini has no compaction | — | terminal |

OpenHands: the SDK agent runs without a condenser unless one is configured
(`openhands/sdk/agent/base.py:266-268`), and the worker configures none
(`breadboard/rl/harness/openhands_worker.py:385, 443`). mini-swe-agent 2.4.6 has no
compaction; exhausting its limits raises `LimitsExceeded`
(`minisweagent/agents/default.py:132-137`).

Hermes compacts inside the stock agent loop, either while sampling or after a
tool round (`agent/turn_tool_round.py:188-201`). The worker therefore routes
each summary HTTP exchange through the Conductor before the source phase
completes. The Conductor counts logical summary calls, not HTTP requests:
stock retries a transient summary failure inside the same `_call_summary_llm`
call (`agent/auxiliary_client.py:3107-3115, 7357-7398`). The overflow bound is
stock `compression.max_attempts` (3) per turn. The pre-request gate cannot fire
on a single-prompt episode, because it needs more than one message
(`agent/turn_preflight.py:88-99`).

### Turning it on

Overflow recovery for an RL target needs two things:
- **The BreadBoard setting:** `policy.provider.compaction: true` in the target's `harness.yaml`.
- **Worker support:** the target's native stream profile (`breadboard/rl/harness/native_stream_profiles.py`) must set either:
  - `implements_compaction_phases`: the worker handles `prepare_compaction` and `finalize_compaction`; or
  - `compaction_in_source`: the stock harness compacts inside the worker and marks its summary requests `purpose: "compaction_summary"` (Hermes).

A target that turns compaction on without either is refused when the episode
starts, with `native_compaction_unsupported`. A source's own setting copied into
`native-config.json` (for example OMP's `agent.compaction_enabled`) describes the
stock harness; it does not turn on recovery.

A profile that lists `compaction` in `sealed_initialize_fields` (OMP 18.1.17)
receives the target's setting in the sealed `initialize` payload: `true` only
when `policy.provider.compaction` is `true`, otherwise `false`. Lowering adds
the field only for such profiles, so other targets compile byte-identically.
Prepare phases use the bound model's context window
(`compaction_model_window_field`), not a static target window.

`openclaw/2026.9.4-r2/target.json` lists `model.compaction: false` under
`overlay.settings`, while its `harness.yaml` and `native-config.json` enable
compaction. BreadBoard does not read `overlay.settings`; they only feed the
overlay digest (`breadboard/product/harness/targets.py`). Compaction runs for
that revision. The target files are immutable, so the entry stays.

### Phases

The Conductor side is `breadboard/rl/harness/runners/native_compaction.py`. At
each checkpoint the profile declares (`compaction_checkpoints`):
1. `prepare_compaction` decides with the source's own trigger and returns the source's summary requests. It replays stock code and stops it at its first model call.
2. The Conductor sends each request to the policy.
3. `finalize_compaction` reruns the stock code with the policy's answers. It returns the compacted history and may ask for dependent follow-up summaries (OMP's short summary).

Pi and OMP workers keep one stock session journal (`SessionManager`) per
episode across these phases and later turns. They append each new message
unchanged, and stock code commits each compaction. A second compaction
therefore sees what stock would: the earlier summary, kept-entry IDs and
file-operation details. Profiles that set `assistant_usage_phase` have the
worker parse each response's raw usage with the source's own parser, so the
source's trigger sees the same assistant `usage` that stock records. OMP's
trigger also counts stored messages and non-message tokens, and ignores billed
usage older than the latest compaction.

`compaction_finalized` may carry `"retry": false`. The Conductor then keeps the
compacted history but does not retry the overflowed request, and the turn ends
with the overflow error. This is how a source's own "the retry still would not
fit" decision (OMP's retry-fit check) ends recovery.

A source can rewrite history without compacting. OMP 18.1.17 shakes tool
results before retrying preparation (`session-maintenance.ts:3667-3695`). If
preparation still fails, the worker returns `compaction_unavailable` with
`history_rewritten: true` and the rewritten messages. The Conductor commits
that history as a `history_rewrite` event, records no compaction, and does not
retry (`session-maintenance.ts:3762-3771`).

Profiles that set `compaction_overflow_message_phase` have the worker project
the provider's overflow error into the source's own error assistant message,
with deterministic timestamps, before recovery runs (Pi, OMP 18.1.17). OMP
18.1.17 also records the error's `duration`: the Conductor passes the measured
exchange time and stock's own clock arithmetic reproduces it.

`compaction_finalized` may also return `"continuation": [AgentMessage, ...]`.
These are the messages the source appends to resume after compaction, such as
its auto-continue developer prompt and any eager reminders. The worker uses
stock code or cited, verbatim private glue, with deterministic timestamps.
The Conductor admits continuation only at `agent_end`. It calls
`resume_after_compaction(messages)` to append the messages and reopen the
semantics state, then keeps stepping. Without continuation, the final answer
still ends the episode. Per harness:
- OMP 16.2.13 continues only when the compacted context leaves recovery
  headroom (`agent-session.ts:11791-11815`).
- OMP 18.1.17 drains deferred handoff tasks before `prompt()` returns. It does
  not auto-continue a terminal text answer unless an active goal requires it.
- Pi and OpenClaw compact at `agent_end` and never continue there. OpenClaw's
  overflow recovery is driven by its embedded-agent caller, which keeps one
  recovery counter per episode (three attempts) and sends a transient
  "Continue the current task" user message after compacting. That message is
  stamped by stock's own formatter and is not persisted or summarized
  (`embedded-agent-CE9KzQvy.mjs:3093-3095, 5898, 5961-5967, 6063-6068`).

`compaction_overflow_attempts` bounds compact-and-retry passes per overflow.
`None` means the Conductor keeps no count. Either the source has no count
(OMP) or the source tracks its own (Pi's `_overflowRecoveryAttempted`).
Recovery then ends only when the source returns `retry: false` or declines to
compact. For Pi, `retry` comes from stock `Agent.continue`, which refuses to
continue from a retained final assistant. A silent overflow on a successful
answer is compacted but not resent, as in headless stock.

### Deviations

Each revision's `harness.yaml` lists its deviations from stock under
`policy.deviations`:
- `compaction_summary_episode_tools`: summary requests carry the episode's
  tools, because the RL policy endpoint rejects a request whose tool schema
  differs from the episode's.
- `compaction_summary_max_tokens`: summary requests use the episode's output
  cap. The source's own summary budget is recorded on the replay trace.
- `compaction_session_lifecycle` (Pi only): stock Pi print mode ends the
  session as soon as the first prompt resolves. 0.73.1 disposes the session
  (`dist/modes/print-mode.js:127`, `dist/core/agent-session.js:508-513`), and
  0.57.1 exits the process (`dist/main.js:688`). That cuts off the harness's
  own overflow recovery. BreadBoard keeps the stock session live for the
  episode, as RPC and SDK use do.

The OMP 18.1.17 handoff side request already carries the live tools, a
2,048-token cap and `tool_choice: "none"` in stock
(`session-handoff.ts:150-157`). The two summary deviations remove nothing
there; the lane finds handoff requests byte-equal.

### Differential lanes

`scripts/compaction_lanes/` runs the stock harness and the BreadBoard target
against the same mock provider and compares every request in canonical JSON.
Canonical JSON sorts object members; nothing else is normalized. A deviation
is applied only after both the stock and BreadBoard values pass its guard.
Raw request bytes are kept beside each report.

| Module | Harness |
|---|---|
| `long_session_lane` | Pi 0.73.1, Pi 0.57.1 |
| `omp16_lane` | OMP 16.2.13 |
| `omp18_lane` | OMP 18.1.17 |
| `openclaw_lane` | OpenClaw 2026.9.4 |
| `hermes_lane` | Hermes 2026.9.11 |
| `codex_lane` | `codex@0.139.0` engine preset |
| `claude_lane` | `claude_code@2.1.63` engine preset |
| `opencode_lane` | `opencode@1.2.17`, `oh-my-opencode@3.10.0` engine presets |

The OMP 18.1.17 lane removes one anchored pattern,
`^Wall time: N seconds$`, from tool-result strings on both sides. It counts
each removal.
