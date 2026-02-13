# TUI Thinking + Streaming Runtime Config

This document defines the runtime knobs for activity status, thinking artifacts, and streaming markdown behavior.

## Activity Runtime

- `BREADBOARD_ACTIVITY_ENABLED` (default: `true`)
  - Enables reducer-driven activity status surfaces.
- `BREADBOARD_ACTIVITY_MIN_DISPLAY_MS` (default: `200`)
  - Hysteresis window to suppress low-priority status flapping.
- `BREADBOARD_STATUS_UPDATE_MS` (default: `120`)
  - Coalescing cadence for repeated status updates.
- `BREADBOARD_ACTIVITY_TRANSITION_DEBUG` (default: `false`)
  - Emits reducer transition traces to debug output.

## Thinking Runtime

- `BREADBOARD_THINKING_ENABLED` (default: `true`)
  - Enables per-turn thinking artifact lifecycle.
- `BREADBOARD_THINKING_FULL_OPT_IN` (default: `false`)
  - Required guard for full-mode reasoning retention/display.
- `BREADBOARD_THINKING_PEEK_RAW_ALLOWED` (default: `false`)
  - Allows raw reasoning peek surfaces; summary-only remains default-safe.
- `BREADBOARD_THINKING_INLINE_COLLAPSIBLE` (default: `false`)
  - Developer-only prototype for inline collapsible thinking summaries in transcript.
  - Safe default is disabled; when enabled, summary lines are shown while raw deltas stay gated behind full+raw opt-in.
- `BREADBOARD_THINKING_MAX_CHARS` (default: `600`)
  - Character cap for summary peek and retained summary blocks.
- `BREADBOARD_THINKING_MAX_LINES` (default: `6`)
  - Line cap for summary peek and retained summary blocks.

## Streaming Markdown Runtime

- `BREADBOARD_MARKDOWN_COALESCING_ENABLED` (default: `true`)
  - Enables buffered append policy instead of per-token appends.
- `BREADBOARD_MARKDOWN_COALESCE_MS` (default: inherits `BREADBOARD_STATUS_UPDATE_MS`)
  - Flush cadence for stable-prefix markdown chunks.
- `BREADBOARD_MARKDOWN_MIN_CHUNK_CHARS` (default: `24`)
  - Minimum buffered chunk size before immediate flush.
- `BREADBOARD_MARKDOWN_ADAPTIVE_CADENCE` (default: `false`)
  - Optional adaptive cadence for boundary/burst-aware markdown flushing.
- `BREADBOARD_MARKDOWN_ADAPTIVE_MIN_CHUNK_CHARS` (default: `8`)
  - Minimum chunk floor used by adaptive cadence.
- `BREADBOARD_MARKDOWN_ADAPTIVE_MIN_COALESCE_MS` (default: `12`)
  - Minimum coalescing delay used by adaptive cadence.
- `BREADBOARD_MARKDOWN_ADAPTIVE_BURST_CHARS` (default: `48`)
  - Burst threshold used to accelerate flush cadence.
- `BREADBOARD_STREAM_MDX_LIVE_TOKENS` (default: `0`)
  - Optional live tokenization mode for stream-mdx worker path.

## Provider Capability Layering

- `BREADBOARD_TUI_CAPABILITY_PRESET`
  - Built-in presets: `claude_like`, `codex_like`.
- `BREADBOARD_TUI_PROVIDER_CAPABILITIES_OVERRIDES`
  - JSON schema overlays applied in deterministic order:
  - `default -> preset -> provider -> model -> runtime`
  - Supported capability keys include:
  - `reasoningEvents`, `thoughtSummaryEvents`, `contextUsage`, `activitySurface`, `rawThinkingPeek`, `inlineThinkingBlock`
- `BREADBOARD_TUI_CAPABILITY_PROVIDER_WHITELIST`
  - Optional comma-separated provider ids used to validate override schema keys under `providers.*` and `models.<provider>/...`.
  - Defaults to: `anthropic,openai,openrouter,unknown`.
- `BREADBOARD_TUI_CAPABILITY_MODEL_WHITELIST`
  - Optional comma-separated model ids used to validate override schema keys under `models.*`.
  - When set, non-listed model keys are rejected with a warning.

Reference implementation:
- `tui_skeleton/src/commands/repl/providerCapabilityResolution.ts`
