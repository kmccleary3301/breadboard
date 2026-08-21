# Runtime records V2

Runtime v2 records are snake_case, closed JSON records with a `schema_version` const and common kernel definitions for actors, timestamps, visibility, metadata, and evidence references. The six records in this set supersede the first runtime scaffold schemas while leaving every v1 schema available for accepted evidence and fixture replay.

## Dialect rules

- `schema_version` is required on every record and matches the schema filename stem.
- Root records use `additionalProperties: false`.
- Nested records are closed unless the field is one of the explicitly open payload surfaces below.
- `metadata` is the only open extension bag. Producers must keep normative fields out of it.
- Runtime identifiers such as `event_id`, `run_id`, `session_id`, and `call_id` are non-empty strings. They are not constrained to the common lowercase identifier pattern.
- Timestamps use `bb.kernel.common.v1#/$defs/timestamp_utc`.
- Actors use `bb.kernel.common.v1#/$defs/actor_ref`.
- Visibility uses `bb.kernel.common.v1#/$defs/visibility`.

Open payload surfaces:

- `bb.kernel_event.v2.payload`
- `bb.tool_call.v2.args`
- `bb.tool_spec.v2.input_schema`
- `bb.tool_spec.v2.output_schema`
- `bb.tool_execution_outcome.v2.result`
- `bb.tool_model_render.v2.parts[].content`
- `bb.session_transcript.v2.items[].content`

## Core event kinds

The `kind` field on `bb.kernel_event.v2` uses a dotted taxonomy. These values are the core registry, documented here instead of encoded as an enum so additional owned families can be added without changing the envelope schema:

- `run.started`
- `run.completed`
- `run.failed`
- `turn.started`
- `turn.completed`
- `step.started`
- `step.completed`
- `model.requested`
- `model.delta`
- `model.completed`
- `tool.called`
- `tool.approval_requested`
- `tool.approved`
- `tool.denied`
- `tool.started`
- `tool.progress`
- `tool.completed`
- `tool.failed`
- `tool.cancelled`
- `permission.requested`
- `permission.decided`
- `task.spawned`
- `task.progress`
- `task.sleeping`
- `task.wakeup`
- `task.join_requested`
- `task.joined`
- `task.completed`
- `task.failed`
- `coordination.signal`
- `coordination.review_verdict`
- `coordination.directive`
- `compaction.started`
- `compaction.completed`
- `checkpoint.saved`
- `checkpoint.restored`
- `warning`
- `limits.updated`
- `message.assistant`
- `message.user`
- `provider.exchange`
- `run.lifecycle`
- `warning.guardrail`
- `compaction.ctree_node`

## V1 to V2 event kind mapping

| v1 kind or family | v2 kind |
| --- | --- |
| `assistant_message` | `message.assistant` |
| `user_message` | `message.user` |
| `provider_response` | `provider.exchange` |
| `tool_call` | `tool.called` |
| `tool_result` | `tool.completed` |
| `permission_request` | `permission.requested` |
| `permission_response` | `permission.decided` |
| `task_event` | `task.progress` |
| `coordination_signal` | `coordination.signal` |
| `coordination_review_verdict` | `coordination.review_verdict` |
| `coordination_directive` | `coordination.directive` |
| `turn_start` | `turn.started` |
| `guardrail_event` | `warning.guardrail` |
| `lifecycle_event` | `run.lifecycle` |
| `ctree_node` | `compaction.ctree_node` |

For any future `coordination_*` v1 family, replace the prefix with `coordination.` and keep the remaining token in snake_case unless a narrower registered kind exists.

## Tool call state mapping

| v1 state | v2 state |
| --- | --- |
| `requested` | `declared` |
| `errored` | `failed` |

Other v1 lifecycle states map to the same v2 string when that string is present in the v2 enum.

## Visibility mapping

The v1 enum maps to the v2 boolean triple as follows:

| v1 visibility | `model_visible` | `provider_visible` | `host_visible` |
| --- | --- | --- | --- |
| `model` | `true` | `true` | `true` |
| `host` | `false` | `false` | `true` |
| `audit` | `false` | `false` | `false` |

`redaction_state` defaults to `none` when omitted by a validator or producer. Producers may set `redacted`, `summarized`, or `elided` when the visible record intentionally withholds content.

## Actor mapping

| v1 actor | v2 actor_ref |
| --- | --- |
| `engine` | `{ "actor_kind": "system" }` plus an implementation-owned `actor_id` |
| `provider` | `{ "actor_kind": "provider" }` plus provider/model identity in `actor_id` |
| `tool` | `{ "actor_kind": "service" }` plus tool identity in `actor_id` |
| `subagent` | `{ "actor_kind": "subagent" }` plus subagent identity in `actor_id` |
| `human` | `{ "actor_kind": "user" }` plus user/session identity in `actor_id` |
| `service` | `{ "actor_kind": "service" }` plus service identity in `actor_id` |

The common `actor_ref` shape requires `actor_id`, so migration code must add one even when the v1 value was only an enum.

## `payload_schema_version` convention

`payload_schema_version` discriminates typed embedded payloads inside `bb.kernel_event.v2.payload`.

- Use the exact `bb.*.vN` schema version when `payload` embeds a record governed by a kernel schema, such as `bb.tool_call.v2` for a `tool.called` payload.
- Use `null` for free-form payloads or bridge payloads without a governed schema.
- Keep the envelope `kind` and `payload_schema_version` aligned. `kind` says what happened; `payload_schema_version` says how to validate the payload, when validation is available.
