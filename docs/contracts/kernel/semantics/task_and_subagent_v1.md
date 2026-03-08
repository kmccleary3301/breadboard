# Task And Subagent V1

## Purpose

This dossier defines the shared kernel contract for task, subagent, and background-task semantics.

BreadBoard already has rich multi-agent behavior, and future BreadBoard engines must preserve the meaning of that behavior without requiring identical implementation machinery.

---

## Contract role

This contract family must describe:

- foreground child tasks / subagents
- background tasks and wakeups
- task lineage and parent-child identity
- join semantics
- model-visible vs host-visible task events

This family should also leave room for future Ralph / longrun / RLM schedulers without collapsing every asynchronous activity into the same record shape.

---

## Shared semantics that must be frozen

A conforming engine must preserve:

- stable task identity
- parent-child lineage
- task kind / subagent type / mode
- task lifecycle milestones (`started`, `tool_call`, `assistant_message`, `completed`, `failed`, etc.)
- whether events are model-visible, host-visible, or audit-only
- join / wait semantics where applicable
- branch / longrun linkage where such features are enabled

---

## Foreground vs background

The contract must make a strong distinction between:

### Foreground / joined subagent work

- created by a parent run
- parent waits or rejoins deterministically
- outputs may affect the parent run immediately

### Background tasks

- may continue after the initiating turn
- may re-enter later through wakeup or merge semantics
- may be primarily host-visible rather than model-visible

This distinction is critical for cross-engine correctness.

---

## Non-goals

This contract does not freeze:

- exact UI presentation of task lists
- tmux or TUI grouping behavior
- Python scheduler internals
- thread/process model used by a given engine

---

## Recommended first schema content

A first-pass `bb.task.v1` should capture:

- `task_id`
- `parent_task_id`
- `session_id`
- `kind`
- `task_type`
- `status`
- `depth`
- `description`
- `visibility`
- `metadata`

Later event-stream schemas can reference task identity without forcing all lifecycle semantics into one record.

---

## Current Python owners

Current ownership spans:

- `SessionState.emit_task_event`
- task/subagent execution paths in `agent_llm_openai.py`
- task tool execution and streaming tests
- RLM/longrun lineage projections
- session runner and projection layers

---

## Current evidence / tests

Representative evidence already exists in:

- `tests/test_task_streaming_events.py`
- `tests/test_rlm_hybrid_integration.py`
- multi-agent and wakeup tests
- replay/parity lanes for harnesses with subagent support

---

## Known ambiguities

Still unresolved:

- where background-task checkpoints live in the contract stack
- whether some scheduler-specific task kinds should remain engine-private
- how much of the current RLM/longrun branch metadata belongs directly in this contract family versus checkpoint/longrun

---

## Immediate next steps

1. add `bb.task.v1.schema.json`
2. add a minimal task example
3. include task lifecycle comparator rules in engine fixtures
