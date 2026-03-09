# Kernel Event Family Registry V1

This registry records the first explicit classification of runtime event families for the multi-engine program.

## Canonical kernel event families

These event types are treated as kernel-owned truth surfaces.

| Runtime event type | Kernel family | Actor | Visibility |
| --- | --- | --- | --- |
| `assistant_message` | `message.assistant` | `engine` | `model` |
| `user_message` | `message.user` | `human` | `model` |
| `tool_call` | `tool.called` | `engine` | `host` |
| `tool_result` | `tool.completed` | `tool` | `host` |
| `permission_request` | `permission.requested` | `service` | `host` |
| `permission_response` | `permission.decided` | `service` | `host` |
| `task_event` | `task.progress` | `subagent` | `host` |
| `turn_start` | `turn.started` | `engine` | `audit` |
| `guardrail_event` | `warning.guardrail` | `service` | `audit` |
| `lifecycle_event` | `run.lifecycle` | `engine` | `audit` |
| `ctree_node` | `compaction.ctree_node` | `service` | `audit` |

## Projection-only runtime events

These exist because current clients need direct convenience payloads. They are not yet kernel truth surfaces.

| Runtime event type | Projection family | Actor | Visibility |
| --- | --- | --- | --- |
| `todo_event` | `projection.todo_snapshot` | `service` | `host` |
| `ctree_snapshot` | `projection.ctree_snapshot` | `service` | `host` |

## CLI bridge stream-only and host-only events

The CLI bridge currently handles additional runtime event types that are important for live UX but are not yet treated as shared kernel truth.

### Stream-only bridge events

- `stream.gap`
- `assistant.message.start`
- `assistant.message.delta`
- `assistant.message.end`
- `assistant.reasoning.delta`
- `assistant.thought_summary.delta`
- `assistant_delta`

These should be treated as projection/transport artifacts until they are promoted into explicit kernel contracts.

### Host-only lifecycle/control events

- `conversation.compaction.start`
- `conversation.compaction.end`
- `checkpoint_list`
- `checkpoint_restored`
- `skills_catalog`
- `skills_selection`
- `warning`
- `reward_update`
- `limits_update`
- `completion`
- `log_link`
- `error`
- `run_finished`

These are useful host-facing events, but they are not part of the kernel event family registry.

### Notes on current owners

- `limits_update` is emitted as a best-effort host-facing bridge event from provider runtime code when provider rate-limit headers are parsed. It is intentionally not treated as kernel truth.
- long-running macro events are currently collapsed through `SessionState.record_lifecycle_event(...)`, so their cross-engine contract surface today is `lifecycle_event`, not a separate macro-event family.
- replay-only events like `completion` and `run_finished` are bridge/session orchestration surfaces, not kernel event families.

## Legacy or still-unclassified events

These should not be relied on as cross-engine truth until they are promoted into the registry.

- any runtime event not listed above
- macro event families emitted only for longrun observability
- host-bridge-specific convenience envelopes

## Rule

If a new event type is introduced and expected to matter for replay, conformance, transcript derivation, or alternative-engine work, it must be added to this registry before it is treated as kernel truth.
