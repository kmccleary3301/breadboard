# Hybrid Delegation Boundaries V1

This document defines the first honest boundary for a future hybrid BreadBoard runtime where a TypeScript kernel may delegate heavy services to Python-backed infrastructure.

## Delegation candidates

The following are valid first-class delegation candidates:

1. sandbox execution
2. Ray-backed distributed execution
3. heavyweight workspace execution services that remain tightly coupled to Python-only infrastructure

## Shared boundary rules

Delegated services must expose:

- a request envelope
- a response envelope
- an error envelope
- retry semantics
- timeout semantics
- evidence and audit references where applicable

The delegated service boundary must not redefine kernel semantics. It only executes a bounded heavy-service responsibility on behalf of the kernel.

## Request envelope expectations

The first-pass request envelope should carry:

- request id
- service family
- operation name
- workspace/session/run lineage
- input payload
- timeout budget
- policy/permission context refs

## Response envelope expectations

The first-pass response envelope should carry:

- request id
- service family
- operation name
- success/failure status
- normalized result payload
- usage/latency metadata
- evidence refs

## Error envelope expectations

The first-pass error envelope should carry:

- request id
- service family
- error kind
- retryable boolean
- human-readable summary
- machine-readable details payload

## Rule

The hybrid path is real enough to test only when the delegated service contracts can be validated through the same conformance program as the rest of the kernel substrate.
