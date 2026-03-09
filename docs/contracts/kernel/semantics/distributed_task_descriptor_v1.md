# Distributed Task Descriptor V1

## Purpose

This dossier defines the shared kernel contract for durable and distributed task execution descriptors.

This family exists so that BreadBoard can express background tasks, subagents, and longrun work in a backend-neutral way instead of letting a queue system or workflow engine define kernel truth.

---

## Contract role

`bb.distributed_task_descriptor.v1` should sit above:

- BullMQ-style queues
- Temporal workflows and activities
- Python longrun controllers
- any remote worker fleet

and below:

- host bridge UX
- project-specific scheduler UI
- backend-specific operational concerns

---

## Shared semantics that must be frozen

The descriptor should cover:

- `task_id`
- `task_kind`
- `parent_task_id`
- lineage and correlation ids
- placement preferences
- checkpoint strategy
- wake conditions
- join policy
- retry policy
- priority
- budget / timeout information
- expected output contract
- artifact refs

This is the semantic description of the task, not the queue record or workflow history implementation.

---

## Non-goals

This contract does not freeze:

- Temporal event history format
- BullMQ job record shape
- Python queue internals
- exact persistence backend schema

---

## Why this matters

Without this contract, longrun and distributed execution semantics drift quickly because each backend starts to impose its own truth model on tasks, wakeups, and resumability.

---

## Immediate next steps

1. add schema and example
2. relate this dossier to `task_and_subagent_v1.md` and `checkpoint_and_longrun_v1.md`
3. later, add backend-specific adapters that consume this descriptor without redefining it
