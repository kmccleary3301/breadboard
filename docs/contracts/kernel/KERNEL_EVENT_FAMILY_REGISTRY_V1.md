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

## Legacy or still-unclassified events

These should not be relied on as cross-engine truth until they are promoted into the registry.

- any runtime event not listed above
- macro event families emitted only for longrun observability
- host-bridge-specific convenience envelopes

## Rule

If a new event type is introduced and expected to matter for replay, conformance, transcript derivation, or alternative-engine work, it must be added to this registry before it is treated as kernel truth.
