# W6 ANN-CHAR — preference-label characterization

## Scenario (secret-free and concrete)

A user submits one labeling request to session `session-label-01`:

```text
POST /v1/internal/sessions/session-label-01/input
{
  "client_message_id": "label-request-01",
  "content": "Compare candidate answers A and B and mark the preferred answer."
}
```

The current API admits this as one turn. For concreteness, call the returned identities
`input-01` and `turn-01` (the live service currently generates `input-<uuid>` and
`turn-<uuid>`). A preference-label record would need to say, at minimum:

```text
label_id=label-01
trajectory_id=trajectory-A / trajectory-B
chosen_message_id=message-A
rejected_message_id=message-B
author=assistant
generation=generation-A / generation-B
label=preferred
```

These are illustrative values, not a claim that the current service already emits such a
record. The point is to make the required joins explicit: both candidate messages must be
immutable, each must belong to an immutable trajectory, and `author` and `generation` must be
stable label facts rather than reconstructed from display text.

## Current owners and what exists today

| Fact needed by the scenario | Current owner | Current behavior | Characterization |
|---|---|---|---|
| Session / turn admission | `SessionService.send_input` and `TurnRecord` | `send_input` creates `client_message_id`, `input_id`, and `turn_id`, deduplicates by the client key/digest, and stores the turn in `SessionRecord` ([`breadboard_engine/api/cli_bridge/service.py:1734-1787`](../../../breadboard_engine/api/cli_bridge/service.py); [`breadboard_engine/api/cli_bridge/registry/records.py:158-175,200-234`](../../../breadboard_engine/api/cli_bridge/registry/records.py)). | Stable turn correlation exists; it is not a message/trajectory label ledger. |
| Provider message identity and author | `ProviderMessage` / provider runtimes | `ProviderMessage` has `role`, optional `message_id`, and `annotations`; `as_dict()` includes `message_id` only when present ([`breadboard_engine/provider/contract_messages.py:139-153,186-222`](../../../breadboard_engine/provider/contract_messages.py)). OpenAI/Anthropic runtimes use provider response IDs as message IDs ([`breadboard_engine/provider/runtimes/anthropic.py:353-355,470-481`](../../../breadboard_engine/provider/runtimes/anthropic.py); [`breadboard_engine/provider/runtimes/openai/responses.py:1307-1310,1216-1265`](../../../breadboard_engine/provider/runtimes/openai/responses.py)). | Provider response identity is available at the provider boundary. `role` is the only current author fact; there is no durable BreadBoard message object keyed by it. |
| Public assistant message event | `RuntimeEventProjector` + `SessionRunner` | Stream lifecycle events carry `message_id` in their replay allowlist, while fallback `assistant_message` is normalized to `{text,message}` ([`breadboard_engine/api/cli_bridge/runtime_event_projector.py:88-186,705-815`](../../../breadboard_engine/api/cli_bridge/runtime_event_projector.py)). Fallback publication may preserve a provider `message_id` nested inside `message`, but completion-summary fallback creates a message without one ([`breadboard_engine/api/cli_bridge/task_execution.py:778-815`](../../../breadboard_engine/api/cli_bridge/task_execution.py)). | This is an event/display correlation, not a canonical immutable message identity with author/generation/trajectory joins. |
| Generation identity | Provider stream/response IDs and provider metadata | OpenAI streaming uses one response ID as the stream `message_id`; stateful Responses keeps `previous_response_id` as provider metadata ([`breadboard_engine/provider/runtimes/openai/streaming.py:319-327,425-428`](../../../breadboard_engine/provider/runtimes/openai/streaming.py); [`breadboard_engine/provider/runtimes/openai/responses.py:1095-1100,1307-1310`](../../../breadboard_engine/provider/runtimes/openai/responses.py)). | There is no general immutable BreadBoard `generation_id` owned by the session or persisted with a message. |
| Trajectory identity | RL export (`VerlProbeRow`) | `trajectory_id` is required in the RL row and is synthesized as `run_id.task_id.trajectory` by `build_verl_probe_rows_from_m6_summary` ([`breadboard/rl/export/schema.py:45-74,76-108`](../../../breadboard/rl/export/schema.py); [`breadboard/rl/export/verl.py:170-187`](../../../breadboard/rl/export/verl.py)). | This is an export identity, not a session-owned trajectory. It has no join to `session_id`, `turn_id`, or provider `message_id`. |
| Durable storage and replay | `SessionRegistry` persistence + `SessionRecord` | The dispatcher assigns event sequence, persists the registry record, and appends the event to the in-memory replay log ([`breadboard_engine/api/cli_bridge/service.py:1416-1456`](../../../breadboard_engine/api/cli_bridge/service.py)). Persistence serializes turn/admission identities and terminal event envelopes, with a replay head; it does not serialize a message or trajectory ledger ([`breadboard_engine/api/cli_bridge/registry/persistence.py:265-382,384-459`](../../../breadboard_engine/api/cli_bridge/registry/persistence.py)). | Durable session/turn and bounded event replay exist; durable preference labels do not. |

## Public event kind requirement

**Yes, a public event kind is required** if a label is to be observable, durable, and replayable.
The current bridge `EventType` has message, tool, reward, completion, and task events but no
annotation or preference-label kind ([`breadboard_engine/api/cli_bridge/events.py:82-126`](../../../breadboard_engine/api/cli_bridge/events.py)). The projector's runtime-event mapping likewise has no label/annotation mapping ([`breadboard_engine/api/cli_bridge/runtime_event_projector.py:705-751`](../../../breadboard_engine/api/cli_bridge/runtime_event_projector.py)), and the replay payload allowlist has no label payload contract ([`breadboard_engine/api/cli_bridge/runtime_event_projector.py:88-186`](../../../breadboard_engine/api/cli_bridge/runtime_event_projector.py)). `reward_update` is not an equivalent: its allowed replay payload is only `summary`, and a preference judgment needs chosen/rejected message and trajectory identities. This report does not add or select that public schema.

## First exact gaps against E1.2 and E4.11

* **E1.2 (immutable message/trajectory annotation join):** the first missing owner is a
  canonical message record linking `message_id` to `session_id`, `input_id`/`turn_id`,
  `author`, `generation`, and `trajectory_id`. The current provider `message_id` is optional,
  the product `SessionEvent` carries only session/sequence plus an unconstrained payload, and
  the RL `trajectory_id` is synthesized independently during export. Therefore a label cannot
  be safely attached to an immutable message or trajectory and recovered after restart.
* **E4.11 (preference label):** after that identity join, the first missing public/durable
  contract is a label event and retained record. No `EventType`, runtime mapping, replay
  payload schema, or `SessionRegistry` serialized field currently owns `label_id`, chosen /
  rejected message IDs, trajectory IDs, author, or generation. Reusing `assistant_message`,
  `reward_update`, or opaque metadata would leave the label outside the current event/replay
  contract.

## Bounded W6.3 handoff

W6.3 should consume these facts without changing the current identities retroactively: preserve
`session_id`/`input_id`/`turn_id` for admission correlation, explicitly bind message and
trajectory identity, and make author/generation immutable label fields. W7.1 is the durable
child/session and replay-owner handoff where child trajectories are involved. W11.1 is the
public annotation/sidecar and preference-label event/schema handoff. No W6 design choice is
made here; in particular, this report does not choose the storage layout or public event shape.
