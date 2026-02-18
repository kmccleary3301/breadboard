# BreadBoard Brand Guide (v1)

## Category and Positioning

BreadBoard is the **agent harness kernel**: a contract-backed engine and event stream that produces replayable artifacts and powers UI-agnostic clients.

## Core Message

- Canonical events are the product boundary.
- Runs are replayable artifacts, not ephemeral logs.
- UI surfaces are projections of contracts, not engine internals.
- Engine/client separation enables local and remote control patterns.

## Tone and Voice

- Crisp, technical, evidence-driven.
- Prefer concrete terms: `contract`, `replay`, `artifact`, `projection`.
- Avoid hype claims that are not tied to reproducible evidence.

## Public Claim Guardrails

Allowed phrasing:

- "Contract-backed event stream"
- "Replayable run artifacts"
- "Projection-driven UI surfaces"
- "Coverage is published and evolving"

Avoid unless explicitly proven by public fixtures and reports:

- "Drop-in replacement"
- "Perfect parity"
- "Enterprise-grade security"
- "Supports every provider perfectly"

## Logo

- Canonical SVG: `docs/media/branding/breadboard_ascii_logo_v1.svg`
- README raster: `docs/media/branding/breadboard_ascii_logo_v1.png`
- OG/social raster: `docs/media/branding/og_banner_v1.png`
- Icon set: `docs/media/branding/breadboard_icon_bb_v1.svg`
- Favicon set: `docs/media/branding/favicon.svg`, `favicon-32.png`, `favicon-64.png`
- Canonical hash and lock record: `docs/media/branding/README.md`

Rules:

- Terminal/docs default: monochrome or restrained color usage.
- Marketing contexts: use the canonical colored banner SVG.
- Small icon/favicons should use dedicated simplified variants, not the full banner.

## Color Tokens (v1)

- Magenta: `#FB30A4`
- Blue: `#6095F9`
- Violet: `#B16BFA`
- Ink: `#0B0B0F`
- Paper: `#F6F7FB`

Use brand colors for accents and media, not long body text.

## Typography

- Web/docs recommendation: Inter + JetBrains Mono.
- Terminal surfaces: rely on user terminal fonts; do not assume uniform rendering.

## Maintenance

When branding assets change:

1. Add a new versioned asset file.
2. Update canonical hash in `docs/media/branding/README.md`.
3. Record rationale and migration guidance in release notes/docs.
