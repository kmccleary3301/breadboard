# @breadboard/ts-host-bridges

Host-facing bridge package for adapting external TypeScript applications onto BreadBoard kernel contracts.

Current scope:
- OpenClaw embedded-runner bridge package
- `createOpenClawHostKit(...)` as the first reusable Host Kit realization
- Backbone-first provider-turn execution for the supported embedded slice
- provider-turn continuity managed through the reusable `@breadboard/host-kits` session helper on the supported path
- strict supported-slice detection
- fallback-safe routing to a native host executor
- projection of canonical BreadBoard kernel events into OpenClaw-style callbacks
- transcript-continuation bridge path for host-owned transcript persistence
- execution capability / placement records for the supported embedded slice
- narrow one-tool embedded slice via the execution-driver family and injected sandbox execution
- direct trusted-local narrow tool execution through the execution-driver family
- OCI-backed narrow tool execution through the execution-driver family and OCI runtime adapters
- delegated remote narrow tool execution through the execution-driver family and a remote adapter or endpoint
- provider-quirk preservation for the narrow supported embedded slice
- unsupported-case classification for fallback and unsupported requests

Non-goals in the current tranche:
- full OpenClaw runtime replacement
- broad tool-rich embedded Pi parity
- hidden in-process BreadBoard engine
- transcript persistence ownership takeover
- ACP or broad channel-routing parity

This package exists so host-specific bridge logic does not leak into:
- `sdk/ts-kernel-contracts`
- `sdk/ts-kernel-core`
- UI/client packages
