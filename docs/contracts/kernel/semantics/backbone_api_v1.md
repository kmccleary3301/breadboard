# Backbone API v1

## Purpose

The Backbone API is the public adoption surface above the kernel for serious TypeScript hosts.

It exists so host developers do not have to integrate directly against kernel substrate nouns like `RunRequestV1` or `ProviderExchangeV1` unless they want to.

## Key public nouns

- `Backbone`
- `BackboneSession`
- `TurnHandle` (future widening)
- `ExecutionProfile`
- `SupportClaim`
- `ProjectionProfile`

## Role in the stack

Backbone sits:
- above kernel core
- above execution drivers
- below Host Kits
- below host application state and UI ownership

## Current v1 slice

The current implementation slice is intentionally narrow:
- session open
- support classification for provider turns and tool turns
- provider-backed text turn execution
- driver-mediated tool turn execution
- projection-profile exposure

It is not yet a full host-kit replacement layer.

## Non-goals

Backbone does not:
- own host transcript persistence
- own app routing
- own delivery-channel semantics
- redefine kernel contracts

## Coordination boundary

Backbone may expose coordination-derived projections, but it does not become the source of coordination truth.

For the current coordination slice:

- typed signals and wake subscriptions remain kernel/orchestration truth
- review verdicts and directives remain kernel/orchestration truth
- Backbone may surface wake-derived session updates or callbacks
- Backbone may surface read-only coordination inspection snapshots
- mission-completion ownership stays below Backbone and above raw transport
