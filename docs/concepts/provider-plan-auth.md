# Provider Authentication Boundaries

BreadBoard has two distinct authentication surfaces. They do not share storage,
lifecycle, or support claims:

1. The durable provider credential broker is the canonical product mechanism
   for supported API-key and OAuth schemes.
2. The legacy `/v1/provider-auth` surface is an ephemeral, in-memory
   compatibility overlay for explicitly admitted local integrations.

The bounded provider and scheme claims live in
[`conformance/provider_parity_claims/manifest.v1.json`](../../conformance/provider_parity_claims/manifest.v1.json).
Neither surface proves real login or live-provider behavior; those claims remain
unproved until the named L1/L2 external gates.

## Durable credential broker

The broker owns a user-private, permission-hardened plaintext SQLite store,
account selection, session affinity, refresh single-flight, logout, revoke,
durable tombstones, and restart recovery. Public routes exchange secret-free
metadata; raw material is issued only to provider routing. This is not
encryption, and the owning OS account and root remain outside the confidentiality
boundary.

Canonical routes:

- `GET /v1/auth/providers`
- `GET /v1/auth/credentials`
- `PUT /v1/auth/credentials/{provider_id}/{account_label}/api-key`
- `DELETE /v1/auth/credentials/{credential_ref}`
- `POST /v1/auth/credentials/{credential_ref}/revoke`
- `POST /v1/auth/login-sessions`
- `GET /v1/auth/login-sessions/{login_session_id}`
- `POST /v1/auth/login-sessions/{login_session_id}/complete`
- `DELETE /v1/auth/login-sessions/{login_session_id}`

The broker's highest-to-lowest source precedence is `runtime`, `config`,
`oauth`, `login_api_key`, `env`, `stored_api_key`, then `fallback`. Credential
origin metadata records the selected source without exposing material. Exact
storage, permission, migration, redaction, refresh, logout, revoke, and
tombstone guarantees are defined by
[`CREDENTIAL_SECURITY_CONTRACT.md`](../reference/CREDENTIAL_SECURITY_CONTRACT.md).

## Ephemeral compatibility overlay

The CLI bridge also exposes:

- `POST /v1/provider-auth/attach`
- `POST /v1/provider-auth/detach`
- `GET /v1/provider-auth/status`

`attach` stores an `EngineAuthMaterial` overlay in process memory, keyed by
`provider_id` and the currently reserved alias. It does not create a durable
broker credential, account identity, refresh record, affinity record, or
tombstone. Restart discards it.

Within this compatibility surface only, attached material precedes the
environment/config resolver and provider defaults. That local precedence does
not replace or redefine the durable broker's `AUTH_SOURCE_PRECEDENCE`.

All `/v1/auth` and `/v1/provider-auth` routes use the CLI bridge's
`BREADBOARD_API_TOKEN` bearer middleware when enabled.

### Local admission and sealed profiles

If `material.is_subscription_plan=true`, non-loopback attachment is rejected by
default. If `required_profile` is supplied, `config_path` is required and the
engine:

1. loads the resolved runtime configuration;
2. applies optional dotted-key overrides;
3. hashes the locked JSON pointers;
4. rejects attachment when the conformance hash differs.

Status returns only sanitized metadata: header names, API-key presence, expiry,
base URL, and routing-key names. It never returns secret values.

## Scheme-specific nonclaims

- Anthropic consumer-subscription material is unsupported. Use the cataloged
  broker API-key or OAuth schemes instead.
- The `openai` policy id `codex_chatgpt_subscription` corresponds to the
  product `codex` / OMP `openai-codex` route. Direct subscription material is
  default-off and harness-backed-only, requires explicit enablement, remains
  local-only, and requires a sealed profile.
- Catalog support for broker API-key/OAuth or provider-managed authentication
  does not imply support for direct consumer-subscription tokens.
- Mock, replay, smoke, and CLI-mock credentials are evidence-only; they do not
  establish a live provider claim.

The authoritative policy records are:

- [`anthropic_consumer_subscription.json`](../provider_plans/policy_manifests/anthropic_consumer_subscription.json)
- [`openai_codex_chatgpt_plan.json`](../provider_plans/policy_manifests/openai_codex_chatgpt_plan.json)

## Related contracts

- [`contracts/cli_bridge/openapi.json`](../contracts/cli_bridge/openapi.json)
- [`session_event_payload_limits_update.schema.json`](../contracts/cli_bridge/schemas/session_event_payload_limits_update.schema.json)
