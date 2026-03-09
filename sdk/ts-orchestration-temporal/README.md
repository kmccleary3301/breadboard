# @breadboard/orchestration-temporal

Temporal-oriented orchestration adapter package for BreadBoard V2.

Current scope:
- mapping `bb.distributed_task_descriptor.v1` into a Temporal-friendly start descriptor
- deriving workflow control-plane descriptors for:
  - signals
  - updates
  - queries
- deriving resume/update payloads from transcript continuation patches without letting Temporal become kernel truth
- documenting the chosen durable orchestration backend path for the TS runtime program

Non-goals:
- shipping a production Temporal deployment
- standardizing Temporal history/event formats as kernel truth
