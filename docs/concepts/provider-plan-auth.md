# Provider Plan Auth (Engine: In-Memory Attach/Detach/Status)

Some provider authentication sources are **compliance-sensitive** (for example, consumer subscription plans). BreadBoard’s engine treats these sources conservatively:

- The engine only accepts **short-lived** auth material.
- The engine stores auth material **in-memory only** (never persisted).
- When an auth source is compliance-sensitive, the engine can require a **sealed profile** conformance check before accepting it.

## Endpoints

All endpoints are part of the CLI bridge FastAPI service.

- `POST /v1/provider-auth/attach`
- `POST /v1/provider-auth/detach`
- `GET /v1/provider-auth/status`

All endpoints are protected by the same `BREADBOARD_API_TOKEN` bearer middleware as the rest of the CLI bridge surface (when enabled).

## Attach

Attach stores an `EngineAuthMaterial` overlay keyed by `provider_id` (alias is reserved for later multi-account support).

The engine will apply attached overlays to outgoing provider calls with the following precedence:

1. Attached auth material (in-memory)
2. Environment/config auth resolution (existing behavior)
3. Provider defaults

### Local-only gating (subscription-plan sources)

If `material.is_subscription_plan=true`, the engine rejects non-loopback requests by default. This prevents accidental forwarding of compliance-sensitive tokens to a remote engine.

### Sealed profile enforcement (scaffolding)

If `required_profile` is provided, the request must also include `config_path`. The engine will:

1. Load the resolved runtime config from `config_path`
2. Apply optional dotted-key `overrides`
3. Compute a conformance hash over `required_profile.locked_json_pointers`
4. Reject the attach request if the hash differs

## Detach

Detach removes any stored material for `(provider_id, alias)`.

## Status

Status returns **sanitized** metadata only:

- header key names (not values)
- presence/absence of `api_key`
- expiry timestamps
- base URL and routing key names

No secrets are returned.

## Related Contract

- `limits_update` event payload schema:
  - `docs/contracts/cli_bridge/schemas/session_event_payload_limits_update.schema.json`

