# DARWIN Stage-4 Route Economics Note

Date: 2026-03-20
Status: route-economics interpretation note
References:
- `docs/darwin_stage4_route_mix_note_2026-03-20.md`
- `artifacts/darwin/stage4/deep_live_search/route_mix_v0.json`

## Note

OpenRouter remains the preferred Stage-4 route in code.

In this workspace, the claim-bearing live rows still relied mostly on direct OpenAI fallback. The current Stage-4 economics therefore prove:

- live-provider execution is real
- cached-input-aware pricing is real
- bounded cost semantics are real

The current program does not prove:

- clean OpenRouter-first economics
- provider-agnostic economics parity

That remains future work, not unfinished Stage-4 debt.
