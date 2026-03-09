# Support Claim and Projection Profile v1

## SupportClaim

`SupportClaim` is the public statement of what BreadBoard supports for a requested host/runtime slice.

It should answer:
- is this slice supported?
- under which execution profile?
- what fallback, if any, exists?
- what unsupported fields or constraints drove the decision?

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
