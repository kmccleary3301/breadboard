# Orchestration Backend Decision V1

## Decision

For the V2 TypeScript runtime program, the primary durable orchestration target is **Temporal**.

## Why Temporal

Temporal is the best fit for the current BreadBoard semantics because it gives us a durable substrate for:

- background tasks
- subagent wake/join behavior
- resumability
- retries with explicit workflow state
- longrun continuation boundaries
- evidence-bearing orchestration histories

The important architectural point is that Temporal is not kernel truth. It is the first chosen backend beneath the kernel contracts.

## Explicitly rejected as primary truth surface

The following are not chosen as the semantic source of truth:

- raw BullMQ job metadata
- ad hoc queue records
- Python-specific Ray task internals
- host-specific workflow artifacts

These may still exist as implementation substrates or migration paths, but they do not define the shared kernel semantics.

## BreadBoard-owned semantics above the backend

BreadBoard continues to own:

- task identity and lineage
- wake conditions
- join policy
- checkpoint strategy
- retry intent
- expected output contract
- evidence expectations

## Backend-owned semantics below the kernel

Temporal owns:

- workflow durability
- timers and wake scheduling
- execution retries
- activity dispatch
- persisted workflow history

## Near-term consequence

The TypeScript program is allowed to claim a credible heavy-service/orchestration story only to the extent that:

1. the kernel contracts describe the durable task semantics
2. a Temporal adapter can consume those contracts
3. the adapter does not redefine those semantics

## Scope boundary for V2

V2 does not claim a production-hardened Temporal deployment. It claims:

- a chosen backend
- a contract-aligned adapter path
- a credible route to durable orchestration that does not let the backend become kernel truth
