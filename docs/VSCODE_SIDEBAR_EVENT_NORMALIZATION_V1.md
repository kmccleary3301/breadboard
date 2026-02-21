# VSCode Sidebar Event Normalization (V1)

Last updated: `2026-02-21`

This document defines how the extension host maps CLI bridge session events into stable sidebar view-model nodes.

## Contract Inputs

Canonical envelope and payload schemas:

- `docs/contracts/cli_bridge/schemas/session_event_envelope.schema.json`
- `docs/contracts/cli_bridge/schemas/session_event_payload_*.schema.json`

Runtime source enums:

- `agentic_coder_prototype/api/cli_bridge/events.py`
- `sdk/ts/src/types.ts`

## Normalization Rules

1. Preserve original event order.
2. Preserve original event id as opaque resume token.
3. Map equivalent legacy/dotted/underscore variants into a shared internal event class.
4. Retain unknown events as `unknown_event` nodes instead of dropping them.

## Event Class Mapping

| Raw type(s) | Normalized class | Target pane |
| --- | --- | --- |
| `user_message` | `transcript.user_message` | Chat |
| `assistant_message`, `assistant.message.start`, `assistant.message.delta`, `assistant.message.end`, `assistant_delta` | `transcript.assistant_stream` | Chat |
| `assistant.reasoning.delta`, `assistant.thought_summary.delta` | `transcript.reasoning_stream` | Chat |
| `tool_call` | `tool.call` | Chat |
| `tool_result`, `tool.result` | `tool.result` | Chat |
| `permission_request` | `permission.request` | Chat + inline approvals |
| `permission_response` | `permission.response` | Chat |
| `checkpoint_list` | `checkpoint.list` | Run |
| `checkpoint_restored` | `checkpoint.restored` | Run + Chat notice |
| `task_event` | `task.event` | Tasks |
| `skills_catalog` | `skills.catalog` | Run/Debug |
| `skills_selection` | `skills.selection` | Run/Debug |
| `ctree_node` | `ctree.node` | Tasks/Debug |
| `ctree_snapshot` | `ctree.snapshot` | Run/Debug |
| `reward_update` | `reward.update` | Run |
| `log_link` | `run.log_link` | Run |
| `completion`, `run_finished` | `run.finished` | Chat + Run |
| `error`, `stream.gap` | `run.error_or_gap` | Chat + Run |
| `turn_start` | `turn.start` | Chat meta |
| any unknown value | `unknown_event` | Debug |

## View-Model Mutation Rules

## Transcript lane

1. `transcript.user_message` appends immutable user block.
2. `transcript.assistant_stream` appends/updates active assistant block by turn id.
3. `tool.call` opens a tool block with `status=running`.
4. `tool.result` closes matching tool block and attaches compact preview.
5. `permission.request` appends actionable approval card.
6. `permission.response` resolves matching approval card state.
7. `run.error_or_gap` appends high-visibility warning/error card.

## Tasks lane

1. `task.event` updates task tree by `task_id` and optional `parent_task_id`.
2. Unknown parent references are attached to root under `orphans` group.
3. Status precedence per task id:
   - `failed` > `running` > `completed` > `queued`.

## Run lane

1. `checkpoint.*` events update checkpoint list status.
2. `run.log_link` updates artifact links table.
3. `run.finished` finalizes run summary record.

## Streaming Behavior Rules

1. Host batches event updates before forwarding to webview.
2. Assistant stream deltas coalesce by active message id + turn.
3. Tool stdout/stderr long payloads are truncated for inline view; full body remains accessible through artifact/open action.

## Resume and Gaps

1. Host stores `lastEventId` per session.
2. Reconnect uses `Last-Event-ID`.
3. If continuity gap detected:
   - emit `bb/connection { gapDetected: true }`
   - append explicit transcript warning
   - offer run artifact rebuild path.

## Determinism Invariants

1. Same ordered event sequence -> same normalized state.
2. Reducers are pure and side-effect free.
3. Unknown events do not mutate known-state lanes beyond debug append.

## Verification Checklist

- [ ] Unit tests for alias mapping (`tool.result` vs `tool_result`, assistant dotted variants).
- [ ] Reducer determinism tests from fixture streams.
- [ ] Gap detection tests for replay window exceedance.
- [ ] Unknown event retention tests.

