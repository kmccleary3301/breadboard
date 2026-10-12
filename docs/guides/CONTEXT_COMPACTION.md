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

The dated gpt-5.5 profile advertises stock's eleven upfront tools, among them
the custom-grammar `apply_patch`, client `tool_search`, text-and-image
`web_search` and original-detail `view_image`. `multi_agent_v1` is deferred,
and the model discovers it through `tool_search`. Build the native helpers
before daily driving: `python -m breadboard_engine.execution.codex_native build all`.
All three helpers share one Cargo workspace and committed lockfile; builds use
`--locked`. Sources ship with the wheel, so this command also works outside the
checkout. Set `BREADBOARD_CODEX_PATCH_BINARY`, `BREADBOARD_CODEX_IMAGE_BINARY` or
`BREADBOARD_CODEX_SEARCH_BINARY` to use a prebuilt helper instead. A missing helper fails
with its build command; nothing runs cargo at runtime, and there's no Pillow
fallback. Exec/PTY, patch and image helpers launch through BreadBoard's
restricted command builder and keep the protected credential paths.
Conductor-owned execution adapters register with the stock-session lifecycle.
Root shutdown cancels and drains child conductors and their command groups,
including background descendants whose leader exited and closed its output.
Native image outputs use the closed `openai.responses.tool_output.v1` carrier only
when `responses_tool_output_items: true`. Source citations are in
`implementations/tools/README.md`. Run the lane with
`python -m scripts.compaction_lanes.codex_lane --out <dir> --window 16000`. Its
only guarded deviations are `codex_exec_chunk_id` and `codex_exec_wall_time`,
values stock generates at random or from the clock.

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

The dated profile requires the unmodified pinned Claude Code CLI at the documented
cache path, or `tools.stock_package`. It starts a persistent stock process in the
BB session's real workspace. Startup and current context come from that live
process, never from recordings. BB still sends its own provider requests and runs
the production controller and summary model. The optional
`request_layout.response_callable` sends genuine BB SDK responses into the paused
stock turn, preserving signed thinking, usage, grouped tool calls and read state.
The summary projection finishes stock's automatic `SG6`/`ne` restoration
(`cli.js:2207-2211`), including read attachments and the continuation. It does not
send `/compact` or a probe turn, which would introduce local-command wrappers or
consume skill reminders. Profiles without these selected hooks are unchanged.
Explicit UUID session admissions pass through unchanged. Opaque BB session IDs
derive a stable native UUID5 from that admission, since stock rejects non-UUID
`--session-id` values; BB's own session identity is not rewritten.
Call `AgenticCoder.close()` when the owning agent shuts down. Claude and OpenCode
register their native sessions through the same stock-tool lifecycle. Claude
shutdown rejects pending UI permission requests through the permission broker.
Explicit shutdown then closes every registered process, proxy and reader,
including a Claude turn paused during terminal overflow. Failed cleanup remains
registered and retryable. Late Claude provider responses cannot rearm a stopped
session. `atexit` is unregistered on every close attempt and is only a last-resort
path.
The CLI bridge also closes its agent when the session ends: on stop, terminal
session completion, or error. This applies to stock and default profiles.
A successful run in an interactive session keeps the agent and sidecars alive
for follow-up turns. Closing a remote agent waits through the existing Ray
`get` path; repeated stop/finalizer calls do not repeat successful agent cleanup.
The dated Claude profile declares `tools.native_local_only: true` so remote
admission rejects this local stock-process placement through a capability,
not a harness-name check.

Run the stock/production differential lane with:

```sh
BB_WORKSPACE_ROOT=$PWD PYTHONPATH=. python -m scripts.compaction_lanes.claude_lane --out /tmp/claude-e4-lane
```

Both sides keep the 200,000-token window and 32,000-token output cap. The lane
applies stock's supported 10% auto-compact threshold override to both runners.
It compares raw request-body bytes, without a canonical-JSON fallback.
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

The two dated `10-9-2026` engine profiles select a digest-pinned, unmodified stock
tool executor. Stock generates the live system text and catalog and executes
native calls, including Bash timeout/output semantics; other profiles retain
their existing execution path. `prompts.initial_context` obtains stock's
persisted `chat.message` transformation once. In-flight current-user additions
from `experimental.chat.messages.transform` are request-only overlays, not
canonical history. Native SDK argument strings are projected only into matched
`call_id` replay DTOs, so whitespace and JavaScript numeric serialization match
without a Python converter. Canonical provider results remain unchanged.
`prompts.summary_projection` obtains the source summary instruction/contributor
and applies the same argument projection. BreadBoard retains its own history,
compactor and provider calls; successful summary usage/reasoning is handed to
the native mirror.
The profiles declare `tools.native_concurrent_prompts: true`: stock's
`session/prompt.ts:238-282` serializes a single session, not independent child
and parent prompts. They also declare `tools.native_local_only: true`, because
the source sidecar launches locally in the workspace; remote execution must
reject this placement at admission rather than fall back to local tools.
`tools.pty: true` lets the native bash and `interactive_bash` tmux sessions
allocate a pseudo-terminal under the restricted launch policy: `/dev/ptmx` and
`/dev/ttys*` read, write and ioctl on Darwin, and fixed devpts rights on Linux.
Protected credential paths stay denied inside the tmux pane.

Before using the language tools, stage the pinned LSP servers with
`python scripts/compaction_lanes/stage_opencode_lsp.py`. The sidecar sets
`OPENCODE_DISABLE_LSP_DOWNLOAD=1`, so a missing server stays missing rather
than being downloaded at runtime. Resuming a session reopens the same native
store under `.breadboard/sessions/<id>/stock-opencode`.

Both runtimes set `ProviderToolCall.allow_raw_arguments`. A tool call whose
arguments aren't valid JSON keeps the exact text in `raw_arguments`, with
`parsed_arguments` and `arguments_json` set to `None`. It becomes the canonical
block `{type: "tool_call", call_id, name, raw_arguments}`; a block carries
either `raw_arguments` or `arguments`/`arguments_json`, never both. Stream end
events carry the matching optional discriminator. The recorder and normalizer
keep it as text. Stock's own repair (`llm.ts:167-191`) turns the call into the
`invalid` tool; BreadBoard never parses or repairs these arguments. Every other
profile keeps the strict `ProviderToolCall` contract.

The lane imports the same pre-run empty stock root session on both sides
(`cli/cmd/import.ts:134-169`). That real source-created ID exists before either
model request. `tools.stock_session_imports` is a runtime lane setting, never
dated production profile data. Both sides keep one native process alive for the
whole session; the plugin's task history and execution hooks do not reset
between user turns. Model windows, LSP settings and the external workspace path
are equal at the source. A lane-only MCP relay forwards real bytes and releases
connection readiness in a fixed order; it never sorts or replaces tool arrays.
Two independent stock runs have identical complete request bodies.

The `delegate_prune` scenario launches a real background task in the same
invocation before compaction. Genuine native API evidence guards a bijection
for runtime-generated background/child IDs only in the exact background output
fields, delegated `prompt_cache_key`, Active/Recent contributor, and the
completed-list notification below. Every substitution is counted. Unknown
IDs, other positions, inconsistent mappings
and root ID differences fail. Source formats are `background-task.ts:80-91`,
`manager.ts:156`, `id/id.ts:49-74`, `task-history.ts:61-73` and the exact
continuation hint in `task-resume-info/hook.ts:28-33`. The compiled stock binary
ignores runtime preloads, so these IDs cannot use the root's import seam.
Raw bytes remain unchanged; comparison canonicalizes JSON member order and
applies only the report's guarded deviations.

The `delegate_idle` scenario holds a real child completion until its parent
is idle. The pinned plugin then initiates its own parent turn
(`manager.ts:1255-1287,1359-1367`), which crosses the threshold twice before
a real user follow-up. BB receives that source input through the production
`SessionService`/`SessionRunner` queue, not a lane-injected notification or a
direct `run_task` call. Native timestamps prove both compactions occurred
between the source notification and follow-up; queued turn records prove the
notification and follow-up completed in order.
The existing runtime-ID deviation covers only the ID in the exact source
completed-list line, including its internal initiator marker. The guard requires
the exact native user text, the matching task call and description, and genuine
child completion evidence. Unproven IDs, wrong wire positions, and mapping
mismatches cannot pass.
The driver also preserves observed queued events, turn outcomes and native idle
checkpoints in `bb_queued_observation.json` before cleanup, even when the final
native observation raises. The original exception still fails the lane.

The approved `opencode_created_time_tie_order` guard covers the stock
`message-v2.ts:740` query, which orders only by `time_created`. Genuine stock
records can give a replay user and its assistant the same millisecond. Only
those tied request slots may move, and only to the order established by BB's
distinct native creation times. The entire resulting request must match.
Missing evidence, distinct stock times, changed content and other-field
differences cannot pass. Reports retain raw requests and count admitted tie
groups per request, including zeroes; no production message ordering changes.
An assistant with tool parts projects to one contiguous slice: its
`function_call` items followed by their `function_call_output` items. Genuine
native call IDs, tool names, parsed arguments and exact outputs validate the
raw slice before any already-admitted ID projection. The slice moves as one
unit; split slices, mismatched calls/results and moves across non-tied messages
cannot pass. This extends the same recorded clock-order deviation, not a new one.
The guard admits only root-owned requests: each original `prompt_cache_key`
must equal its own side's genuine native root session ID. A child with identical
content cannot borrow its parent's timestamps, even after an admitted child-ID
projection makes the request cache keys equal.

Native ties involving a summary or compaction part can instead change stock
control flow and produce extra summaries. These are stock defects, not BB
deviations. The same native-record-only oracle rule applies to every scenario:
select each side with at most five attempts, rejecting only native
control-boundary ties before looking at request bytes. BB applies the same
selector to its sidecar's own unmodified stock records: this is the same
`message-v2.ts:740` defect, not a production retry or a new wire deviation.
All attempts, native tie groups, request/summary counts and artifact paths are
recorded. Five tied attempts on either side fail. A tie-free byte/count/error
difference fails without another attempt; no fallback or normalization exists.
Each closed lane-owned BB product/native session store is archived with its
attempt, then recreated fresh at the same workspace and native session ID.
The selected raw requests, result and queued observation remain available at
the report root as well.
If a foreground POST times out, the driver retains the original traceback and
reads genuine native evidence from that same live stock API before cleanup.
Only the native selector above can reject the attempt. Without a proven
control-boundary tie, the timeout fails the scenario rather than causing a
retry. Observation failures are recorded, never replaced with invented evidence.

The stock driver observes mock-script exhaustion and the native root's idle,
completed normal answer before shutting down. A foreground prompt returning is
not enough: the plugin's deferred summarize/prompt calls can still be running.

### `oh-my-opencode@3.10.0`

Accepts the same settings and follows OpenCode's native overflow summarization.
Pinned continuity instructions preserve task context. OpenCode
`processor.ts:359-364,420` publishes the overflow error then compacts. The
deferred plugin hook skips only when the latest assistant is itself a summary
(`recovery-hook.ts:125-142`), not when a normal answer follows that summary.
The OMO preset therefore declares `pending_error` at `assistant_step_end`:
its package-restricted `error_parser` arms recovery, `skip_summary_last`
defaults to true, and required positive `max_attempts` is 2
(`types.ts:31-36`). No other preset selects this trigger, and no agent
`compaction:` keys change.

Both OpenCode presets set `overflow.error_classifier` to the pinned native
`provider/error.ts:8-23` patterns. This optional package-restricted callable
defaults to the existing generic classifier when absent. In-memory SDK
cause/context supplies native evidence without persisting provider text.
For example, `too many tokens: 74000 tokens > 64000 maximum` is recognized by
the plugin, but not native OpenCode. Its armed `pending_error` checkpoint runs
before the failed provider step becomes terminal; it does not enter native
overflow compaction.
The plugin parser decodes original HTTP response bytes with UTF-8 replacement,
matching JavaScript response text, rather than failing on malformed bytes.

The authenticated dated profile runs the plugin's largest-first recovery in
the native stock instance. BB copies genuine PATCHed outputs, keyed by call ID,
into request DTOs only; canonical history is unchanged. Genuine queued native
summary requests are served through BB's production provider path.
Successful summarize retains pending state (`summarize-retry-strategy.ts:111-117`);
the state clears on summary-last skip, native handled truncation, or the spent
two-attempt budget (:159). A native handled outcome does not wait for a summary.
Stock's handled branch submits `promptAsync({auto:true})` without the required
`parts`, producing a genuine 400 rather than a continuation; BB preserves this
dead end, and the next real human input may continue under the dated profile's
explicit `session.failed_turn_continues` capability. See
`artifacts/compaction_lanes/native-stock-defects/prompt_async_missing_parts.json`
and `agent_configs/README.md`; this is not a wire deviation.

The OMO overflow lane exercises the natural partial-truncation branch.
Two genuine human acknowledgements precede the overflow. A read-only native
event observer on each side gates the overflow input on six owned
`message.part.updated` prune events from the preceding turn (`prompt.ts:716`),
with unchanged output hashes and exact native message/part identities.
This removes the observed stock-internal race between its 300 ms recovery timer
and model latency: six remaining outputs save 144,000 original bytes, below
the 168,000-byte recovery target, so native summary fallbacks proceed.
No sleeps, fake parts, production timer changes or removed response descriptors
are used. The lane report retains both native fences and the measured timelines
in `artifacts/compaction_lanes/native-stock-defects/recovery_timer_prune_race.json`.
The source-captured summary-contributor oracle names the `summary` stage
explicitly; it does not select the deferred-recovery stage by pipeline order.

The `plugin_error` lane isolates plugin-recognized errors that native OpenCode
does not classify as overflow, including both deferred recovery attempts.
The preemptive path is not cloned: it requires `experimental.preemptive_compaction`,
and the shipped dispatcher never
feeds its token cache. The dated stock executor runs tool hooks once inside
the plugin; the current-input keyword hook and delegated-session summary
contributors come from that same source process rather than separate BB
clones. The source installation is pinned to `5137df72`.

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

### Runtime ownership and distribution

The conductor selects one immutable stock execution driver during setup.
Controllers use explicit execution, lifecycle and summary-catalog ports, not
vendor-private object traversal. Sampling phase and pending user position belong
to each compaction request, not the controller's lifetime.

OpenCode and OMO share one canonical tool-definition catalog and HTTP proxy.
Their version-specific tools and native session behavior remain separate.
Native permission IDs follow the retained response channel across runner turns;
a transient turn state cannot restart numbering while older requests wait.

Codex owns its process managers and child registry outside the generic sandbox.
Shutdown atomically closes the target subtree before stopping processes or
joining workers. Closed parents cannot admit descendants; worker startup uses
the same admission lock. Shutdown drains owned process groups and reader threads
even after a group leader exits. Build all native helpers explicitly with
`python -m breadboard_engine.execution.codex_native build all`. Runtime execution
never invokes Cargo; missing helpers fail closed through the tool boundary.
The closing subtree rejects resume and input until resource cleanup, worker
drain and shutdown persistence complete. Failed cleanup keeps admission blocked
until a successful close retry. Sequential resume remains supported afterward.

Installed wheels include the target revisions, prompt and context resources,
tool catalogs, native worker modules and shared Cargo workspace. Stock caches
and documented dependency overrides remain external prerequisites.
Captured OMO material is subject to Sustainable Use License 1.0, not MIT.
Claude material remains proprietary. Included notices record provenance and
restrictions; they do not establish redistribution permission.

To test a candidate wheel, build and install it separately into a fresh venv
without editable installs or system site packages. Then run from the checkout
using the test runner's Python (the fixture launches the supplied interpreter
with `-I` from a temporary directory outside the checkout):

```bash
env -u PYTHONPATH \
  BREADBOARD_INSTALLED_WHEEL_PYTHON=/tmp/pr160-cleanup-proof/venv/bin/python \
  python -m pytest -q tests/test_runtime_resource_closure.py \
  tests/product/harness/test_templates.py::test_wheel_import_loads_template_from_distribution_data_root \
  tests/compaction/test_codex_native_resources.py
```

Without `BREADBOARD_INSTALLED_WHEEL_PYTHON`, the installed-wheel tests skip.
They never build, copy source trees, download dependencies or launch workers.
The wheel regression lowers all six compaction target revisions against their
pinned predecessors and resolves Codex's shared native workspace through the
installed consumer APIs. It also exercises Codex model capabilities, deferred
tool-catalog discovery's unavailable-helper response, and startup rendering
with permission prompts, skill instructions and admitted environment facts.

Native worker requests carry the pinned SDK's complete serialized
`request_body_json` through an explicit invocation-local provider port. This
transport carrier stays separate from semantic replay payloads and their digests.
Main, summary and continuation requests use the same boundary. Reported members
describe emitted JSON, excluding SDK properties whose values are `undefined`.
The provider checks JSON values against compiled authority, admitting only the
summary tool/output-cap overlays. Numeric spellings such as `1` and `1.0` are
equivalent; booleans are distinct from numbers, and duplicate members are rejected.
The original body remains outbound. Summary overlays replace only admitted root
member/key spans or append missing admitted members, preserving other numeric
spellings, escapes, whitespace and member order.

Codex and Claude ordering adapters copy SDK request options before serialization.
They depend narrowly on the private `_build_request`, options-copy and
extra-body merge interfaces. Source inspection covered OpenAI 3.26.1 and
Anthropic 1.12.1. This does not establish compatibility with every version
allowed by the declared OpenAI `>=1.0.0` dependency. Generic requests retain
SDK serialization; profile scopes reset after completion, cancellation or error.

### Differential lanes

`scripts/compaction_lanes/` runs the stock harness and the BreadBoard target
against the same mock provider and compares raw request-body bytes first.
Every raw mismatch retains its request index and both SHA-256 digests.
Only named, source-authorized edits whose guards pass on both sides may be
applied; the remaining bytes must match exactly. Member order, whitespace,
escaping and duplicate JSON names are not silently normalized.
Raw request bytes and per-edit deviation records are kept beside each report.

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

The OMP 18.1.17 lane may remove one terminal
`Wall time: N seconds` header from a correlated Bash tool result. The header
may precede the source-generated timeout-clamp notice only when the matched
request and admitted 30-second ceiling reproduce that exact notice. The notice
stays byte-significant. Ambiguous, embedded or unrelated headers are rejected;
each guarded removal is recorded.

The Codex lane admits only permutation of the two `client_metadata` members
constructed by the pinned source's `HashMap`. Installation and window values
must remain identical, both bodies must use the source's compact unescaped form,
and every byte outside that object remains significant. The window identifier
includes its source-owned generation suffix. Records retain original and
projected member order, exact spans and raw digests; duplicate names still fail.

OMP 18.1.17 renders the local calendar date of its own process
(`sdk.ts:3460-3463`, `utils/local-date.ts:2-7`), not the UTC date. The profile
therefore declares the admitted instant `message_timestamp_ms` instead of
`current_date`. The worker formats it with the pinned `formatLocalCalendarDate`
in its own time zone and reports the result as `bootstrap.current_date`.
The comparator requires exact input sets: supplier captures carry `cwd`,
`home`, `current_date` and `package_dir`, and BreadBoard traces add a
13-digit `message_timestamp_ms`. Pi 0.73.1 and 0.57.1 retain the dated
profile's `TZ=UTC` prompt-builder admission. Their lanes no longer erase
date, working-directory or package-path differences with blanket regular
expressions. Such differences fail unless a named, source-authorized guard
admits the exact change; frozen profiles and older revisions remain unchanged.

The Pi lanes record independent working-directory, time-zone and launch-clock
observations. Named guards admit only the pinned first-system prompt's unique
footer date and directory spans that match those facts. Pi 0.73.1 also checks
its declared calendar date. Summary prompts, package paths, user and tool text,
and every byte outside the admitted spans remain significant.

Pi terminal parity also requires the actual stock `agent_end` and BreadBoard's
typed outcome. Exhausted or declined source recovery commits the SDK assistant
error and returns `POLICY_INCOMPLETE`, not an infrastructure exception. An owned
RPC process terminated after source settlement retains its real exit status;
only a recorded, authenticated SIGTERM cleanup may qualify. Missing completion
evidence, unexpected signals and opaque exceptions fail the lane.

### Codex dated-profile agent and goal execution

The `codex_0_139` native tool gate runs collaboration tools as real child
conductors in the parent's admitted workspace, with the same provider route,
configuration and tools. Each child has its own UUIDv7 thread and retained
history. Plain spawns start fresh; full-history forks retain system, developer,
user and final-answer messages, not tool traffic. Completed children still
count against the six-child limit until closed. The default maximum depth is
one. Closing a child closes its descendants; resume restores its retained
history. Child histories and goals persist under `.breadboard/codex/`.
Child checkpoints retain raw provider history and its compaction ledger together.
Every subsequent input and disk resume restores both from the thread's own
checkpoint, so compacted tool traffic does not reappear and the window count
does not reset. Forks preserve compacted replacement history and filter only
the visible suffix. Their hidden raw prefix remains solely to preserve ledger
indices (`core/src/agent/control/spawn.rs:470-490`;
`core/src/session/rollout_reconstruction.rs:112-128`).
At the depth limit, the derived v1 catalog omits collaboration tools. With no
deferred namespace left to search, it also omits `tool_search`; the inherited
configuration itself is unchanged. The stock default child therefore advertises
ten tools while its parent continues to advertise eleven, before and after
discovery (`core/src/tools/spec_plan.rs:304-312,698,849-856`).
Incoming hidden calls follow the same depth gate. A child `tool_search_call`
replays a completed client `tool_search_output` with an empty `tools` array;
a namespaced agent call returns `unsupported call: multi_agent_v1<name>`.
The namespace and function name have no separator in stock's error text
(`core/src/tools/registry.rs:443-461,741-745`;
`protocol/src/tool_name.rs:37-42`; `core/src/tools/parallel.rs:188-194`;
`core/src/context_manager/normalize.rs:41-63,160-169`).
Each thread owns its unified-exec process table even though the admitted sandbox
is shared. Terminal IDs cannot be polled from another thread; closing an agent
terminates its continuing processes and those of its closed descendants.

`responses_message_phase` preserves optional `commentary` and `final_answer`
phase through provider parsing, history and Responses replay. Without the flag,
existing request and history behavior is unchanged. Full-history forks keep only
assistant messages tagged `final_answer`. Local-image agent input accepts `auto`,
`low`, `high` and `original`; the first three share resize encoding but retain
their requested wire detail.

The gpt-5.5 dated profile initially advertises client `tool_search`, not the
agent namespace. Searches rank the five stock search-info documents in their
source order with the same pinned Rust `bm25` 2.3.2 crate and English tokenizer.
The default result limit is eight; a blank query or zero limit returns a completed
search output with no tools. Matched functions are grouped into `multi_agent_v1`
in rank order with `defer_loading: true` and no `output_schema`. The discovered
namespace travels in `tool_search_output.tools`, not in later request catalogs.
Typed search call/output replay preserves arguments, execution and status;
response item IDs are not replayed. Build the scorer explicitly with
`python -m breadboard_engine.execution.codex_native build search`, as with the native patch/image helpers.
The production dispatcher remains gated by `native_semantics: codex_0_139`.

Router decoding failures remain distinct internally, but invalid unsigned limits
(`-1`, `1.5`), missing query and null query all replay as completed search outputs
with empty `tools`, confirmed by the real-stock oracles. History normalization
inserts the missing search output and removes the orphan empty-ID function output
(`core/src/context_manager/history.rs:111-114,327-335`;
`core/src/context_manager/normalize.rs:41-63,160-169`).
Selected child-model image, web-search and truncation capabilities come from a
vendored projection of the pinned `models-manager/models.json`, rather than
copying the parent's capabilities.
The pinned scorer uses Unicode word boundaries, deunicode normalization,
lowercase, English stop-word filtering and stemming; BM25 defaults are
`k1 = 1.2`, `b = 0.75`. It searches positive-overlap documents and takes the
requested count after descending score ordering. These are crate behavior,
not a second Python scoring implementation (`bm25-2.3.2/src/default_tokenizer.rs:267-286`,
`embedder.rs:150-162,230-234`, `scorer.rs:79-122`, `search.rs:118-131`).

Canonical tool admission retains flat Responses function, namespace and hosted
`web_search` schemas only when both the configured API variant and the resolved
runtime select Responses. Function parameters, boolean `strict`, namespace
children and hosted `external_web_access` are validated without stripping wire
members. Other API variants retain the original tool contract. A replay-safe
stream fallback preserves the admitted schema variant.

The dated Codex profile selects a repo-only `prompts.initial_context` callback.
It accepts an admitted environment rather than reading the clock while rendering
requests. Standalone admission with `providers.session_uuid_version: 7` generates
a UUIDv7 root; other profiles retain their original correlation IDs. Responses
`session_params` and `client_metadata_session` derive cache and window metadata
from that root and the production compaction ledger.
The dated profile opts into `resume.restore_compaction_state`, restoring the
checkpoint's compaction ledger before the pending user turn. Its window generation
continues from the restored boundary count (`session/session.rs:520-529,1049-1052`);
new sessions start at zero. Other profiles retain their existing resume behavior.
Paired contextual markers use stock's whitespace and ASCII-case rules. Explicit
pre-turn and mid-turn placement rules reinstall startup context after the summary
or before the last user message, respectively (`compact.rs:297-307,465-506`).
`responses_preserve_tool_call_items` retains source function-argument strings and
namespaces; `responses_tool_output_items` retains typed native image/text outputs.
Both are opt-in, closed canonical replay envelopes.

`responses_max_output_tokens_from_context` is opt-in: when selected, the
Responses output cap uses the episode's `max_tokens`; sealed summary parameters
still take precedence. Without it, the request payload is unchanged.

Stock references below are relative to `codex-rs/` at
`a7dff904308535e965aee87680c1fc5ef1d19eec`:

| Behavior | Stock source |
|---|---|
| Deferred corpus order and search-info text | `core/src/tools/spec_plan.rs:755-778,849-859`; `core/src/tools/handlers/multi_agents.rs:68-79`; `multi_agents/spawn.rs:37`, `send_input.rs:21`, `resume_agent.rs:22`, `wait.rs:42`, `close_agent.rs:21` (last five paths relative to `core/src/tools/handlers/`) |
| Exact English BM25 builder, trim/limit errors and ranked selection | `core/src/tools/handlers/tool_search.rs:39-46,85-123`; `tools/src/tool_discovery.rs:7`; `Cargo.lock:1407-1410` |
| Deferred namespace coalescing and output-schema removal | `tools/src/tool_search.rs:32-52`; `tools/src/responses_api.rs:77-104` |
| Typed search call/output replay and empty-tool error outputs | `protocol/src/models.rs:807-818,854-859`; `core/src/tools/context.rs:166-180,289-294` |
| Spawn model/effort/tier validation and supported built-in roles | `core/src/tools/handlers/multi_agents_common.rs:280-425`; `core/src/agent/role.rs:309-367` |
| Effective inherited config, model, provider, approval, sandbox and cwd | `core/src/tools/handlers/multi_agents_common.rs:197-277` |
| Separate thread, optional history fork | `core/src/tools/handlers/multi_agents/spawn.rs:124-140`; `core/src/agent/control/spawn.rs:33-63,378-515` |
| Thread/depth limits and reservation release | `core/src/config/mod.rs:192-195,240,1327-1329`; `core/src/agent/registry.rs:63-119` |
| Spawn output `{"agent_id":"...","nickname":"..."}` | `core/src/tools/handlers/multi_agents/spawn.rs:210-238` |
| Send output `{"submission_id":"..."}`; interrupt or queue | `core/src/tools/handlers/multi_agents/send_input.rs:56-81,103-127` |
| Wait output `{"status":{...},"timed_out":false}` | `core/src/tools/handlers/multi_agents/wait.rs:108-192,225-228` |
| Wait for any final result, not all; default 30s, clamp 10s to 3600s | `core/src/tools/handlers/multi_agents/wait.rs:84-92,146-175`; `core/src/agent/status.rs:23-28` |
| Close output `{"previous_status":...}`, descendant shutdown | `core/src/tools/handlers/multi_agents/close_agent.rs:62-115`; `core/src/agent/control/legacy.rs:27-83` |
| Thread-local unified-exec manager and shutdown of all continuing processes | `core/src/session/session.rs:992-994`; `core/src/session/handlers.rs:625-631` |
| Optional message phase and final-answer-only assistant fork history | `protocol/src/models.rs:735-765`; `core/src/agent/control/spawn.rs:33-40` |
| Resume output `{"status":...}` | `core/src/tools/handlers/multi_agents/resume_agent.rs:78-141,155-158` |
| Unknown id, closed agent, thread limit errors | `core/src/tools/handlers/multi_agents_common.rs:112-134`; `protocol/src/error.rs:86-87` |
| Input/depth errors | `core/src/tools/handlers/multi_agents_common.rs:163-195`; `core/src/tools/handlers/multi_agents/spawn.rs:70-75` |
| Status JSON strings and tagged completed/errored objects | `protocol/src/protocol.rs:1571-1590` |
| Image URL forwarded unchanged with default detail `high`; local image labels/encoding | `protocol/src/models.rs:733,1029-1036,1079-1123,1236-1272` |
| Goal create/get/update validation and errors | `ext/goal/src/tool.rs:154-273,375-381`; `protocol/src/protocol.rs:3641-3652` |
| Durable goal state, one goal per thread | `state/src/runtime/goals.rs:36-62,121-168,170-249` |
| Goal JSON `goal`, `remainingTokens`, `completionBudgetReport`; camelCase goal fields, seconds timestamps; absent budget omitted | `ext/goal/src/tool.rs:59-65,384-411,429-439,466-475`; `protocol/src/protocol.rs:3655-3668` |
| Goal token baseline, whole-second elapsed time, terminal update accounting | `ext/goal/src/accounting.rs:148-163,196-218`; `ext/goal/src/tool.rs:223-235` |

Run executor conformance with `python -m pytest
tests/compaction/test_codex_agent_tools.py`. The stock long-session scenarios
in `scripts/compaction_lanes/codex_lane.py` exercise successful `exec_command` and
`write_stdin`, two pre-turn compactions, mid-turn compaction and terminal overflow.
They do not execute collaboration or goal tools; those require the separate
executor conformance suite, not schema equality alone.
