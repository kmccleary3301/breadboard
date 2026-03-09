# OpenClaw Backbone Adoption V1

This document explains the current V3 adoption path for an OpenClaw-class host.

## BreadBoard owns

- runtime classification through `@breadboard/backbone`
- support-claim generation
- transcript continuation semantics
- tool-turn execution through the execution-driver family
- callback-facing event/transcript projection through the OpenClaw Host Kit

## The host still owns

- persisted session storage
- app routing and delivery channels
- account/profile selection UX
- fallback runtime selection
- any unsupported embedded-run paths

## Recommended integration shape

1. Build a `Workspace` for the host repo or session root.
2. Construct `createOpenClawHostKit(...)`.
3. Let the Host Kit:
   - classify support
   - invoke supported Backbone paths
   - preserve transcript continuity
   - fall back cleanly when unsupported
4. Keep host-specific persistence and delivery outside BreadBoard.

## Supported V3 slices

- provider-backed embedded text turns
- transcript-continuation embedded turns
- narrow single-tool embedded turns
- provider-quirk preservation for the tracked lane
- trusted-local, OCI-backed, and delegated-remote execution-driver paths for the narrow supported tool slice

## Explicit non-claims

V3 does not claim:

- full Pi replacement inside OpenClaw
- ACP parity
- broad channel/delivery parity
- multimodal parity
- broad tool-rich runtime parity

## Why this shape is attractive

- OpenClaw gets a host-facing TS surface instead of a Python dependency in the primary supported path
- BreadBoard owns the hard runtime semantics
- the host retains product ownership where it should
- support boundaries are explicit through `SupportClaim`

