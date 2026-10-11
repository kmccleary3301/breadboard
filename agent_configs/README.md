# Agent configuration catalog

The current E4 catalog is the four BreadBoard agent profiles dated `2026-10-09` plus the two target-config projections dated `2026-09-10`. The date is the BreadBoard catalog refresh date, not an upstream capture date. Each file preserves the version and claim boundary of the latest accepted source in `docs/conformance/e4_lane_inventory.json` or, for oh-my-opencode, the pinned freeze-manifest replay row.

Each `2026-10-09` profile is its `2026-09-10` predecessor plus the harness's own context compaction: a `compaction:` block selecting the preset cloned from the pinned source (native default settings) and the model's context window. OpenCode and oh-my-opencode also declare the model's input and output limits, which their thresholds use. `docs/guides/CONTEXT_COMPACTION.md` documents each preset. The `2026-09-10` profiles remain as compaction-off baselines.

| Family | Current file | Upstream identity | Compaction | Scope |
|---|---|---|---|---|
| Codex CLI | `codex_0-139-0_gpt55_e4_10-9-2026.yaml` | Codex CLI 0.139.0, `gpt-5.5` | `codex@0.139.0`, window 272000 (`models.json` gpt-5.5) | Runnable BreadBoard capture/replay profile. Acceptance is limited to the read-only capture probe; this is not a full Codex parity claim. |
| Claude Code | `claude_code_2-1-63_e4_10-9-2026.yaml` | Claude Code 2.1.63 capture package | `claude_code@2.1.63`, window 200000 (`cli.js` default) | Runnable standalone BreadBoard dossier for the accepted static-package/replay surface. |
| OpenCode | `opencode_1-2-17_e4_10-9-2026.yaml` | OpenCode 1.2.17 | `opencode@1.2.17`, window 400000, input 272000, output 32000 (bundled models.dev snapshot) | Runnable standalone BreadBoard dossier for the accepted static-package/replay surface. |
| oh-my-opencode | `oh_my_opencode_3-10-0_e4_10-9-2026.yaml` | oh-my-opencode 3.10.0 at commit `5137df72d8fab3fec609c82f91387db8e3b13825` | `oh-my-opencode@3.10.0`, same limits as OpenCode | Runnable standalone BreadBoard dossier for the frozen Phase 8 async/subagent replay surface. |
| Oh My Pi | `oh_my_pi_16-2-13_e4_9-10-2026.yaml` | `@oh-my-pi/pi-coding-agent@16.2.13` at commit `5356713eae60e67ee64d9b02e3b5e377d248ee7f` | target revision `oh-my-pi-r3@16.2.13` | `bb.e4.target_config.v1` catalog projection. Load the canonical installed target package; do not pass this file to the BreadBoard agent-config CLI. |
| Pi | `pi_0-57-1_e4_9-10-2026.yaml` | `@mariozechner/pi-coding-agent@0.57.1` | target revision `pi-r4@0.57.1` | `bb.e4.target_config.v1` catalog projection. Load the canonical installed target package; do not pass this file to the BreadBoard agent-config CLI. |

## Run and inspect

The four BreadBoard agent profiles support the existing TUI config path:

```bash
node tui_skeleton/dist/main.js doctor --config agent_configs/codex_0-139-0_gpt55_e4_10-9-2026.yaml
node tui_skeleton/dist/main.js run \
  --config agent_configs/claude_code_2-1-63_e4_10-9-2026.yaml \
  --workspace ./agent_ws \
  "Describe this repository."
```

Those commands may contact the configured provider. The Codex file is intentionally narrow: its accepted evidence covers one read-only capture probe, not collaboration, write execution, or full-session parity.

The TUI defaults to the current Codex profile. Use `--config` or `BREADBOARD_DEFAULT_CONFIG` to select another profile.

Oh My Pi and Pi use the installed-target API. This reads the hashed descriptor and declared assets without invoking a provider:

```bash
python - <<'PY'
from breadboard_engine.e4_targets import load_e4_target

for target_id in ("oh-my-pi@16.2.13", "pi@0.57.1"):
    target = load_e4_target(target_id)
    print(target_id, target.descriptor["execution"])
PY
```

Canonical target packages live under `config/e4_targets/`. Historical frozen dossiers are under `deprecated/`; ATP and Hilbert comparator arms are under `research/`; scenario-specific and supporting profiles remain under `misc/`.

## Claim boundaries

- A current filename does not make an old capture current upstream behavior.
- `config/e4_targets/codex/0.107.0/` remains the historical GPT-5.1 package. The accepted Codex 0.139.0 probe uses the separately tracked 0.139.0 prompt asset.
- Oh My Pi and Pi package descriptors are the machine authority. Their top-level files expose the package config for readers but do not replace `target.json` or `config/e4_targets/index.json`.
- Frozen evidence and declarations retain their original logical paths and hashes. `deprecated/manifest.json` maps relocated inputs to byte-identical archives for E4 readers; active commands use current profiles.
- The LongRun parity audit checks active agent profiles, excludes `deprecated/`, and reports target-config projections separately as non-applicable.

## Dated OpenCode native execution

The October 9 OpenCode profiles select `tools.native_semantics` to execute
provider tool batches through stock OpenCode 1.2.17, not BB's tool aliases or
LSP wrapper. The omo profile requires the digest-pinned oh-my-opencode 3.10.0
runtime staged under `~/.cache/bb-compaction-e4/pkgs/oh-my-opencode@3.10.0-5137df72/`.
`OPENCODE_PLUGIN_ROOT` in lanes/tests points there. `tools.stock_binary`,
`stock_timeout`, and `stock_config` control the executable, deadline, and stock
configuration. `stock_read_only_roots` pins install trees; missing or changed
content rejects launch. No other profile uses this executor.

The dated OpenCode, oh-my-opencode, and Claude stock-sidecar profiles are local
daily-driving profiles. Remote Ray execution is unsupported and fails at
admission with an explicit error, before actor creation or provider calls.
Use `force_local_mode=True` for direct callers; CLI sessions require local mode.
RL target packages are separate and are not enabled by installing stock CLIs.
`tests/compaction/test_stock_sidecar_remote_admission.py` covers these gates.

`tools.native_local_only: true` owns the remote admission gate; it is not
inferred from executor identity. The two dated OpenCode profiles also opt into
`tools.native_concurrent_prompts: true`. Both capabilities default to false.
Concurrent prompts preserve stock child approval requests across parent turns
and admit OMO's actual background notification through the normal session input
queue. The stock-created message, model route, and native message ID remain
attributed to stock in BB events and transcripts, not to a human user.
The owning BB session commits its first canonical turn even before compaction,
so the next stock-originated turn retains the earlier user/tool history.

The two dated OpenCode profiles also declare `tools.pty: true`, an explicit
launch capability defaulting to false. The shared sandbox permits Darwin PTY
allocation and read/write/ioctl only for `/dev/ptmx` and `/dev/ttys*`; Linux adds
read/write access only to the fixed devpts mount and allocator. Protected
credential denies remain unchanged, including inside stock tmux panes.
The capability-off policy text is byte-identical to the earlier policy.
Codex already allocates its PTY in the host before sandboxing and inherits the
descriptors; it does not need a second PTY launch mechanism.

Real `websearch_web_search_exa` and `grep_app_searchGitHub` calls are compared
with direct stock in `test_opencode_stock_omo_groups.py`, skipping visibly only
when the endpoint is offline. Its tmux lifecycle test creates a session, sends
keys, captures actual output, and kills the session on both stock paths, skipping
only when tmux is absent. Stock blocks bare `capture-pane` in `interactive_bash`
and directs capture through its `bash` tool; the test preserves both results.
`test_opencode_stock_pty_isolation.py` proves the pane cannot read a protected
path. Concurrent headless completion and operator stop share one whole native
teardown mutex (`test_opencode_stock_lifecycle.py`).

OMO enqueues the notification with `promptAsync(noReply: false)` when all
background tasks complete (`background-agent/manager.ts:1359`). Stock creates
that user before entering its session loop (`session/prompt.ts:238-282`).
An already-enqueued stock notification therefore precedes a simultaneous BB
user input. Parent model calls still use BB's provider pipeline and compaction
checkpoints; tool calls execute through the same stock sidecar.

Headless single-task sessions stop at the first final root idle and dispose the
stock instance, matching `cli/cmd/run.ts:531-537,640-646,655-662` and
`cli/bootstrap.ts:12-14`; they do not wait for a background child that finishes
after that idle. Notifications queued
before the idle run as ordinary stock-originated turns on the session runner.
Interactive native sessions require the live `SessionService` input queue.
A persistent direct caller without that queue gets an explicit error if stock
initiates a parent turn, rather than a parked request or a fabricated answer.
`test_opencode_stock_autonomous_input.py`,
`test_opencode_stock_cross_turn_permission.py`, and
`test_opencode_stock_headless.py` cover these transitions.

Stage the omo ast-grep dependency before launch:

```bash
PYTHONPATH=. python scripts/compaction_lanes/stage_opencode_stock_dependencies.py
```

The installer verifies npm integrity, stages upstream ast-grep 0.40.0 into the
plugin's bundled-package locator, and reports the new plugin tree digest.
Record that digest in the profile's read-only install roots. Missing or altered
staged `sg` rejects launch with this command; writable download caches are not
executable. The native binary SHA-256 is
`a8de4e534206a71030ae7151f049cb4513548b4e5e9e88a71ed0b73b041e5ace`.
The matching GitHub release archive is
`app-aarch64-apple-darwin.zip` for 0.40.0, SHA-256
`fabfd23fe06ed780ecfa2250adc7615221bfbfa962902b6f3807de67bcef253f`.
Sources: pinned omo `src/tools/ast-grep/downloader.ts:19-28,85-87` and
`sg-cli-path.ts:34-66` (cache, CLI package, platform package resolution order).
Stage the stock-selected offline TypeScript and Pyright servers before launch:

```bash
PYTHONPATH=. python scripts/compaction_lanes/stage_opencode_lsp.py
```

The installer checks npm integrity and records the read-only tree digest in
the profile's `stock_lsp_root`. Stock resolves the staged executables through
its own PATH/cache lookup, with runtime downloads disabled. TypeScript and
Python diagnostic tests compare successful native output to a direct stock CLI.

The dated OpenCode profiles clone stock served with
`OPENCODE_SERVER_PASSWORD` set. The sidecar authenticates for credential
isolation; direct-stock lanes use the same HTTP Basic authentication mode.
This is a configuration fact, not a parity deviation. Stock warns that an
unpassworded server is unsecured (`cli/cmd/serve.ts:14-15`,
`cli/cmd/web.ts:36-37`). In authenticated OMO sessions, the native plugin can
PATCH tool parts during aggressive overflow recovery
(`src/shared/opencode-http-api.ts:68-72`). An unauthenticated TUI instead logs
`No auth header available`, cannot PATCH, and falls through to summarize.

Each BB session owns a stock `serve` process in the same workspace. BB feeds
its parent model responses to a local proxy; identified child/title/summary
requests use BB's declared model routing and provider credentials. Unroutable
models fail by name; no substitution. Declared free Zen models make real daily
driving calls to `https://opencode.ai/zen/v1` with stock's anonymous `public` key.
The proxy strips private session/agent/provider label headers before forwarding
auxiliary requests and retains their original request-body bytes.
MCP endpoints remain stock-configured. Unknown requests and dead sidecars fail
without a native-executor fallback.

The server runs under BB's credential-isolation policy, with isolated HOME/XDG
directories. User-level stock auth/config is not loaded. Project providers must
remain proxied or launch fails. Dated `stock_worktree_read_only` permits only
the git worktree resolved by stock project discovery, read-only, rejecting
protected-root overlap; writes remain confined to the BB workspace.
For linked worktrees, read-only git metadata access follows the actual `.git`
gitdir/commondir pointers only when the metadata's reciprocal `gitdir` backlink
matches that worktree. Submodule metadata must be under the superproject's
`.git/modules` and its `core.worktree` must resolve to the discovered worktree.
Fabricated pointers, unlinked bare git directories, protected overlaps, and
credential hardlinks reject launch. Tests compare native project IDs and
snapshots with direct stock for linked worktrees and submodules.

Stock auto-compaction/pruning settings remain configured, including independent
child compaction. Real BB main-turn usage drives the same root boundary.
Stock's queued summary request is answered by BB's genuine summary response
(text, reasoning, usage), and BB prune selections update native parts.
Provider failures use the preset's native overflow classifier and plugin error
parser, preserving the original HTTP status, headers, and body. Plugin recovery
can start after the original HTTP prompt returns. BB waits for that source-owned
compaction instead of creating another one, and binds each summary response to
its exact queued operation.
At the dated provider boundary, current-user hook text and stock's genuine
JS-serialized `function_call.arguments` and repaired tool names are overlaid
onto copied request DTOs only. Canonical BB history is unchanged. Missing native
call IDs fail visibly instead of replaying the sampled raw argument string.
Only these native profiles admit malformed JSON as bounded `raw_arguments`.
The shared provider contract keeps it exclusive of `arguments_json` and parsed
`arguments`; stream recording and sanitization preserve the lexical string.
Stock repairs malformed JSON to its `invalid` tool before continuing. JSON that
fails a tool schema keeps its stock tool name and produces stock's execution
error. Other profiles retain strict JSON argument validation.
After genuine automatic compaction, stock's synthetic current-user continuation
may omit the original media. The copied provider request follows that transition
without removing the authorized media from canonical BB history.
Stock owns guards, LSP, plugin state, and child sessions. Legacy enhanced-LSP
initialization in `conductor/components.py` is unchanged for other profiles.
Interactive stock permissions/questions use the existing permission queue.
Headless sessions use stock `run`'s question/plan deny rules and permission
auto-rejection. Close the agent to abort/dispose the instance and stop the
proxy/server. Lane-only `stock_session_imports` accepts actual pre-run empty
stock exports, never synthetic or post-run source history.
Root completion also follows stock's authenticated busy/idle session events,
including plugin-owned continuations that outlive the original prompt request.
At every BB run boundary, a still-active root is interrupted through stock's
`/session/{id}/abort` API. A budget stop keeps executed tool history available
for the next turn; it does not invent a model completion.
Stock permission/question events run in tracked workers, so an unanswered
background-child prompt cannot stop root busy/idle tracking. Root abort cancels
only that root's native request IDs through the existing approval queue and
authenticated stock replies. Child prompts and prompts from later turns remain
independent. Agent shutdown rejects all outstanding prompts and joins their
workers before disposing the stock instance.

Sources are OpenCode commit `715b844c2a88810b6178d7a2467c7d36ea8fb764`,
`src/server/routes/session.ts:354-381,484-540,692-798`,
`src/session/prompt.ts:256-267`, `src/session/status.ts:59-73`,
`src/session/llm.ts:133-145,167-191`, `src/session/compaction.ts:58-98,235-289`,
`src/provider/error.ts:179-198`,
`src/server/server.ts:257-275`, `src/cli/cmd/run.ts:352-368,539-550`,
`src/provider/provider.ts:131-150`, `src/project/project.ts:90-177`, and
`src/project/instance.ts:27-31`. Execution, lifecycle, isolation, import, and
permission conformance tests live in `tests/compaction/test_opencode_stock_*.py`.
They are not a full-catalog daily-driving parity certificate.
The plugin's recovery owner is pinned `5137df72`,
`src/hooks/anthropic-context-window-limit-recovery/summarize-retry-strategy.ts:104-117`.
`test_opencode_stock_budget_followup.py` compares a real BB budget stop and
same-session followup with a direct stock interruption. The plugin group tests
exercise recovery followed by real bash execution, completion or interruption,
and another user turn.

### Stock defects and native recovery ownership

Queued stock summary requests, including an extra native checkpoint, are served
through BB's genuine provider and compaction pipeline. They are not answered
with a previous summary or suppressed to force a request count. Native
recovery users, when stock actually creates them, enter the stock-origin
session queue; they are not reassigned to the earlier human input.

The pinned authenticated OMO aggressive-recovery path has a stock dead end.
After successful tool-part PATCHes, it clears its pending state and schedules
`promptAsync` with `{auto: true}` and no `parts`
(`aggressive-truncation-strategy.ts:61-77`, specifically `66-72`).
OpenCode 1.2.17 requires `parts` (`src/session/prompt.ts:111`) and returns HTTP
400 with this body:

```json
{"data":{"auto":true},"error":[{"expected":"array","code":"invalid_type","path":["parts"],"message":"Invalid input: expected array, received undefined"}],"success":false}
```

No recovery user or continuation is created. BB preserves the original
provider failure, clears the consumed pending-error state, and reports the
native dead end within the configured deadline. A real subsequent user can
continue the session. Its copied request DTOs carry stock-native patched
tool outputs matched by `call_id`; canonical history is not rewritten.
BB does not insert missing `parts`, invent a recovery user, or repair stock.
This upstream defect is not a request-comparison deviation.
The authenticated native validation response and empty native message list are
retained in
`artifacts/compaction_lanes/native-stock-defects/prompt_async_missing_parts.json`.

Both dated OpenCode profiles declare `session.failed_turn_continues: true`.
The capability is a strict optional boolean, defaults to false, and is separate
from `tools.native_concurrent_prompts`. It keeps an interactive session open
after a validated failed turn, while leaving that turn and its original error
visible. This matches stock returning an errored assistant without deleting
its session (`src/session/prompt.ts:716-723`). Headless sessions, malformed
exchanges, correlation failures, and duplicate terminal frames do not gain
this continuation permission.

OpenCode's native message stream orders only by millisecond creation time
(`src/session/message-v2.ts:740`). When a summary sorts before its tied
compaction marker, the pending-task scan can replay that marker
(`src/session/prompt.ts:304-314`). The replay summary belongs to the latest
synthetic continuation user, not the original marker (`prompt.ts:532`).
This also occurs inside a real `task` child. Auxiliary model calls retain
their native child ownership; neither the parent wait nor stock is patched.
The child regression oracle answers the authenticated assistant role and
admits extra summaries only with the exact tied-marker/replay ancestry.

OMO also has a recovery re-entry race after an interrupted continuation.
Concurrent `session.idle` handlers check pending recovery before awaiting
the native assistant and toast (`recovery-hook.ts:125-166`). Exhausting the
two summary attempts clears both the retry counter and the in-progress lock
(`summarize-retry-strategy.ts:159`, `state.ts:27-36`). An already-admitted idle
handler can then start a fresh attempt 1/2 and create another compaction
marker. This can happen without any native timestamp tie.
BB serves these genuine requests rather than suppressing them. The regression
test admits this branch only with native abort, exhausted-recovery, reset-attempt
SSE provenance and a new marker; an unqualified extra checkpoint fails.
The native trace is retained in
`artifacts/compaction_lanes/native-stock-defects/recovery_idle_reentry.json`.

[RL] The sidecar currently requires a local process workspace. It refuses
Docker/remote sandbox drivers rather than running tools outside the episode
sandbox. Installing the binaries alone does not establish RL support.
