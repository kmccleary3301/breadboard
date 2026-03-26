# Durable Orchestration And Resume V1

## Purpose

This dossier defines the shared kernel semantics that sit above durable orchestration backends.

It complements `bb.distributed_task_descriptor.v1` by clarifying what must remain kernel truth when the backend is something like Temporal.

## Shared semantics that must survive backend choice

The kernel-visible durable orchestration surface includes:

- task identity
- parent/child lineage
- wake conditions
- typed wake subscriptions
- join policy
- retry intent
- checkpoint strategy
- resume token/ref association
- expected output contract
- evidence expectations

## Resume semantics

A resumed task must preserve:

- original task id
- lineage
- checkpoint strategy
- reason for wake/resume
- trigger signal id / code when resume was signal-driven
- subscription cursor state for typed signal wakes
- source task id and subscription id when the resume came from a subscribed worker signal
- backend-visible resume metadata only as audit/supporting data

## Wake/join semantics

Wake and join rules are kernel-level meaning, not backend private rules.

Examples:

- wake when a child task completes
- wake when a timer fires
- wake when an accepted coordination signal matches a typed wake subscription
- join after all required children complete
- fail fast when a required child fails

In the current sparse supervisor-worker reference lane, the wake chain must remain inspectable:

1. worker emits accepted typed `complete` or `blocked`
2. subscription match produces a derived wake event for the supervisor
3. wake metadata carries `subscription_id`, `trigger_signal_id`, `trigger_code`, `cursor_event_id`, and `source_task_id`
4. supervisor review records a durable review verdict separately from the worker signal itself

That separation is what keeps completion truth durable without making backend wakeup transport itself the semantic source of truth.

## Retry semantics

Retry policy is part of the task descriptor and may be realized by the backend, but the backend must not invent new semantic meaning for retries.

## Non-goals

This dossier does not standardize:

- Temporal workflow history format
- BullMQ internal job records
- Python longrun database schemas

## Immediate V2 stopping point

For V2, the durable orchestration story is complete when:

1. the kernel contracts express the necessary semantics
2. one chosen backend is documented
3. one adapter package proves the contracts can be consumed without semantic drift across:
   - workflow start
   - workflow control-plane
   - resume/update
