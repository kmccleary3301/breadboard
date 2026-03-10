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
- terminal registry reduction and cleanup result construction
- effective tool-surface construction

It is not yet a full host-kit replacement layer.

## Non-goals

Backbone does not:
- own host transcript persistence
- own app routing
- own delivery-channel semantics
- redefine kernel contracts
