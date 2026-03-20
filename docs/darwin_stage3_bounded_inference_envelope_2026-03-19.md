# DARWIN Stage-3 Bounded Real-Inference Envelope

Date: 2026-03-19
Status: fixed envelope

## Routing

- worker route: `gpt-5.4-mini`
- filter route: `gpt-5.4-nano`
- stronger tier: exception-only

## Campaign limits

- small repeated campaigns only
- matched-budget comparisons only
- explicit replication and control reserve behavior
- lane count held to the authorized set

## Abort conditions

- evaluator closure missing
- budget telemetry missing
- runtime-truth drift
- replay/parity regression
- invalid-comparison pressure too high
