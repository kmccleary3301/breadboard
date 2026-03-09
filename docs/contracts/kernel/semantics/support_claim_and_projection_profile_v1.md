# Support Claim and Projection Profile v1

## SupportClaim

`SupportClaim` is the public statement of what BreadBoard supports for a requested host/runtime slice.

It should answer:
- is this slice supported?
- under which execution profile?
- what does that execution profile actually imply about backend/security posture?
- what fallback, if any, exists?
- what unsupported fields or constraints drove the decision?
- how should a host present or schedule this turn?
- how much confidence should an integrator place in the support decision?

## ProjectionProfile

`ProjectionProfile` describes how runtime state is projected outward for a host or consumer.

Examples:
- host callbacks
- raw kernel events
- AI SDK transport-compatible output

## Why these are public primitives

These two objects let BreadBoard productize what is currently implicit in:
- host bridge behavior
- fallback code paths
- evidence/conformance posture

They are especially important for large TS teams because they make support boundaries and output surfaces explicit.

## Current implementation direction

The current public `SupportClaim` shape now carries:
- `executionProfileId`
- a richer `executionProfile` object
- `recommendedHostMode`
- `confidence`

That is intentional. The public product surface should expose more than a yes/no support bit if we want serious TS hosts to make good runtime choices.
