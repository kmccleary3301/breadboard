# DARWIN Stage-4 Route Mix Note

Date: 2026-03-20
Status: route/economics interpretation note
References:
- `artifacts/darwin/stage4/deep_live_search/route_mix_v0.json`
- `artifacts/darwin/stage4/deep_live_search/provider_telemetry_v0.json`

## Note

The current deep-live-search tranche used:

- `openai/gpt-5.4-mini` for all claim-bearing primary-lane live rows
- `openrouter/openai/gpt-5.4-mini` only on scaffold-only harness watchdog rows

## Interpretation

OpenRouter remains the preferred Stage-4 route in code.

In this workspace, claim-bearing live rows still fell back to direct OpenAI rather than remaining on OpenRouter. The current deep-search economics are therefore interpretable, but they are not yet a clean OpenRouter-first cost record.

This does not block the current deep-search tranche.

It does mean any later economics memo should state that the current provider mix is:

- OpenAI-backed for live claim rows
- OpenRouter only on scaffold watchdog paths
