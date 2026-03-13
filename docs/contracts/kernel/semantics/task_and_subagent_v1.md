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
- supervisor ownership of mission completion when worker tasks only propose completion

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
- may be resumed because an accepted typed coordination signal matched a subscribed wake condition

This distinction is critical for cross-engine correctness.

---

## Sparse supervisor-worker reference slice

The current coordination reference lane proves one narrow but important pattern:

- one supervisor task owns the mission outcome
- one worker task owns the dense local loop
- the worker may emit accepted typed `complete` or `blocked` signals
- the supervisor wakes only when a typed `wake_subscription` matches those accepted signals
- the worker cannot finalize mission completion by itself

This is intentionally narrower than a full future coordination ontology. It is the first portable truth slice that must survive replay and backend adaptation.

### Completion ownership

The worker may truthfully say:

- `my task is complete`
- `I am blocked`
- `I require human attention`

The worker may not truthfully finalize:

- `the mission is complete`

In the reference lane, mission completion becomes durable truth only after the supervisor validates required deliverables or evidence and records a supervisor-side decision.
In the next reviewed-result tranche, that supervisor-side outcome should be a first-class review verdict, not a stringly runtime-private decision.

### Blocked is not generic failure

`blocked` is a typed coordination fact, not merely a failed step.

The reference lane carries structured blocked payloads such as:

- `blocking_reason`
- `recommended_next_action`
- optional `support_claim_ref`

That allows a sparse supervisor to choose retry, checkpoint, escalation, or human review without collapsing everything into plain-text failure output.

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
- `tests/test_supervisor_worker_reference_slice.py`
- `tests/test_worker_cannot_self_terminate_mission.py`
- replay/parity lanes for harnesses with subagent support

---

## Known ambiguities

Still unresolved:

- where background-task checkpoints live in the contract stack
- whether some scheduler-specific task kinds should remain engine-private
- how much of the current RLM/longrun branch metadata belongs directly in this contract family versus checkpoint/longrun

---

## Immediate next steps

1. keep the sparse supervisor-worker lane replayable across engines
2. extract supervisor review into explicit reviewed-result truth before adding broader coordination patterns
3. add conservative public config surfacing for coordination without widening ontology too early
4. promote the tracked coordination evidence bundle into manifest-backed conformance rows once the public surface is frozen
