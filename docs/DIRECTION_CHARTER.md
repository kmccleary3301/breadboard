# BreadBoard Direction Charter

BreadBoard is a maximalist engine for agent-harness design: one engine truth surface expressive
enough to build any modern harness and to clone existing harnesses as configuration plus a bounded
adapter, with evidence that names exactly what is proven and what is excluded.

**The product** is the runtime engine, the v2 config surface and compiler, the /v1 session API, and
the SDK/TUI/CLI clients over it. **E4 is the internal conformance backend** that makes our parity
claims trustworthy; its records, lanes, catalogs, ledgers, and regeneration machinery are not part
of the ordinary harness-builder experience and are not public API.

**Falsifiable thesis.** An outsider, using only published docs and the `bbh` front door, can clone a
bounded harness profile in one working day: hand-authored manifest ≤ 150 lines and adapter code
≤ 300 lines, producing a session-running harness and an accepted-scope conformance claim.
The flagship demo protocol (docs/plans/phase_20_right_shape/FLAGSHIP_DEMO_PROTOCOL.md) measures this;
measured numbers replace aspirations here after each run.

**Contract tiers.** Published schemas carry tiers in contracts/kernel/registries/contract_tiers.v1.json:
runtime_protocol / config_algebra / host_protocol / evidence / frozen_legacy. "Minimal" refers to the
runtime_protocol tier only. A kept schema names a real consumer or it is frozen.

**Layering.** Product harness configs are bb.agent_config_surface.v2 documents. E4 lane intent lives
in lane manifests (bb.e4.lane_manifest.v1); deterministic resolution lives in lane locks
(bb.e4.lane_lock.v1) and generated sidecars, machine-owned, never hand-edited.
Run evidence references locks by digest. The chain is manifest ← lock ← evidence, never reversed.
Every stage reports an explicit outcome (executed, reused-with-provenance, disabled-by-manifest, or structurally not-applicable); silent skips are defects.

**Change discipline.** Surface growth (schema families, SDK packages, lane kinds, ledgers,
scorecards) is frozen except through docs/plans/phase_20_right_shape/FREEZE_POLICY.md. External-target
capture MAY require adapter code; unbounded descriptors and replicated hash pins may not return.
The current campaign and its completion criteria: docs/plans/phase_20_right_shape/BB_RS_MASTER_PLAN.md.
