# Compaction differential lane support

This package owns recorded mock provider transports, stock process lifecycles,
scenario construction, native evidence guards and request-byte comparison.
`scripts/compaction_lanes` contains only command entrypoints.

OpenCode responsibilities are recording, MCP relay, scenarios, stock process
and API evidence, BreadBoard episode execution, guarded comparison and lane
orchestration. Pi separates target loading, scenarios, stock RPC settlement,
BreadBoard episodes, guarded request comparison and orchestration.

Acceptance compares raw request bodies first. Reports retain every differing
or missing request index and raw SHA-256 digest. Named deviations record their
source authority and exact edited JSON positions and values. Guards may change
only their declared fields; untouched wire member order, whitespace and escaping
remain strict. Parsed equality alone never admits a representation difference.
Dates, working directories and package paths have no implicit normalization.
Recorded JSON with duplicate object member names is rejected before guards run.
Rejection retains the request index and both raw body hashes, including the raw
mismatch evidence. Guard projections never compare collapsed duplicate objects.

The OpenCode native-created-time oracle selector remains independent of wire
comparison, and strict success still requires no unresolved request differences.
Existing pinned target documents and stock-owned runtime behavior are unchanged.

## Declared Codex HTTP metadata representation deviation

`codex_client_metadata_hashmap_member_order` applies only to the
`$/client_metadata` object in the pinned Codex HTTP Responses request.
At source commit `a7dff904308535e965aee87680c1fc5ef1d19eec`,
`codex-rs/core/src/client.rs:791-814` constructs its two identity members with
`HashMap::from`; lines 409-412 format the window identity as
`{thread_id}:{window_generation}`, whose generation is an `AtomicU64` (174-176).
`codex-rs/codex-api/src/common.rs:182-202` serializes that
`Option<HashMap<String, String>>`. Member iteration order is not predictable.

The guard accepts only the two compact, unescaped member permutations containing
unchanged lowercase UUIDv4 `x-codex-installation-id` and
`x-codex-window-id` containing a lowercase UUIDv7 thread followed by `:` and a
canonical decimal u64 generation. Other fields, duplicate members, whitespace, escaping
and changed values do not qualify. The rest of the original request remains
byte-strict; this is not canonical JSON equality.

`deviation_counts[].metadata_order` records the request index, JSON path,
source commit and citations, original and projected member orders, original
byte spans, raw request hashes and metadata-object hashes. The enclosing record
reports acceptance after all named guards, while `raw_differences` still retains
the original wire mismatch. Acceptance therefore means raw parity after named
guards, not zero raw differences. This PR's lane declaration does not modify
frozen target documents or dated production profiles.


## Production worker regressions

OMP process-inspection regressions import the actual `processTable` export from
`runners/omp_18_1_17/tool_session.ts`. Framing regressions import the actual
`writeFrame` implementation from `runners/omp_18_1_17/rpc_frames.ts`, which the
production worker also uses. Tests do not extract or execute copied source text.

## Pi source prompt environment guards

`Pi057PromptFooterDate` / `Pi057PromptFooterCwd` and
`Pi073PromptFooterDate` / `Pi073PromptFooterCwd` apply only to the unique final
footer of the first qualified native default system prompt. The pinned builders
own separate formats: 0.57.1 `dist/core/system-prompt.js:17-30,155-156` uses the
full locale date/time; 0.73.1 `:7-15,116-117` uses the calendar date.

The lane observes each side independently: actual runtime cwd, timezone,
launch/completion epoch bounds and the initialized Pi073 current date. Stock
Node observes its physical cwd, including `/private/var`; the actor retains its
admitted cwd and existing UTC behavior. Guards reproduce the pinned formatter
within those observed bounds, rather than trusting dates extracted from bodies.
No clock input, frozen target setting or actor prompt is changed.

`environment_observations` retains both sidecars. Each
`deviation_records[].environment[stock|bb]` entry carries the named field,
pinned source commit/citation, original raw request SHA, exact original UTF8
span/value, projected bytes and independent environment facts. These edits
run before other named projections; all remaining bytes stay strict.
Duplicate, malformed, misplaced or unadmitted date/cwd slots fail the guard.
Summary prompts have no environment footer and are left untouched.

Pi and OMP16 both admit `/v1` as the provider base endpoint, matching their
stock registry. Method/path differences are reported as `request_route`, not
mislabelled as equal-body differences.

## OMP18 Bash elapsed time

`omp_native_bash_elapsed_time` is grounded in commit
`3b3a6dc9bbd85102ce19d0b1c11bf6870915f6ec`,
`packages/coding-agent/src/tools/bash.ts:531-549,781-793,1009,1111-1121` and
`tools/tool-timeouts.ts:10-11,33-37`. A uniquely correlated Bash result admits
one final two-decimal wall-time header. It may precede the exact source clamp
notice for the matching call's integer timeout (default 300s) and this lane's
30s global ceiling. The notice itself is never changed or removed.
The original content must also have one exact native JSON string carrier;
alternate escaping in its unchanged output or notice is not normalized away.

The report retains the original raw difference, edited content, source
authority and admitted ceiling. Arbitrary suffixes, changed notices, wrong
call timeouts, duplicate calls/headers or ambiguous argument objects do not
qualify.

## Pi terminal outcome acceptance

Raw request parity alone cannot make a Pi lane pass. `stock-runtime.json`
preserves the actual stock process stdout/stderr/return code and its final
`agent_end` event. The report exposes `stock_terminal_state`,
`bb_terminal_state`, `terminal_differences` and `request_parity_passed`.

`overflow_recovery` and `final_over_threshold` require source `stop` and the
canonical actor `assistant_complete` outcome. `long_session` deliberately ends
with a second-overflow provider error, as does `repeated_failure`; both require
stock's native assistant `stopReason: error`, actor `policy_incomplete` and
the committed native error termination/message in the actor replay trace.
Stock must exit normally, or the RPC capturer must record its own SIGTERM
request after observing source settlement and `agent_end`, followed by a
successful owned-process wait with the actual `-15` return code. That exception
requires the owned PID/controller, exact observed settlement event, no reader
error or kill escalation, and preserved native terminal outcome evidence.
Unexplained signals or other negative exit codes fail.
Missing terminal evidence, an opaque actor exception or an unexpected native outcome
fails acceptance even if every request byte matches after named guards.
Error-message wording is retained as evidence but is not copied or compared.
