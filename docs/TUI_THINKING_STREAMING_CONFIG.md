# TUI Thinking + Streaming Runtime Config

This document defines the runtime knobs for activity status, thinking artifacts, and streaming markdown behavior.

## Activity Runtime

- `BREADBOARD_ACTIVITY_ENABLED` (default: `true`)
  - Enables reducer-driven activity status surfaces.
- `BREADBOARD_ACTIVITY_MIN_DISPLAY_MS` (default: `200`)
  - Hysteresis window to suppress low-priority status flapping.
- `BREADBOARD_STATUS_UPDATE_MS` (default: `120`)
  - Coalescing cadence for repeated status updates.
- `BREADBOARD_EVENT_COALESCE_MS` (default: `80`)
  - Controller-side event flush cadence (ms) for streaming event bursts.
  - Set to `0` for microtask-only flushing.
- `BREADBOARD_EVENT_COALESCE_MAX_BATCH` (default: `128`)
  - Immediate flush threshold when queued events reach this batch size.
- `BREADBOARD_ACTIVITY_TRANSITION_DEBUG` (default: `false`)
  - Emits reducer transition traces to debug output.

## Thinking Runtime

- `BREADBOARD_THINKING_ENABLED` (default: `true`)
  - Enables per-turn thinking artifact lifecycle.
- `BREADBOARD_THINKING_PREVIEW_ENABLED` (default: `true`)
  - Enables ephemeral thinking preview cards above the composer in Claude-style scrollback mode.
- `BREADBOARD_THINKING_PREVIEW_MAX_LINES` (default: `5`)
  - Max retained preview lines in the ephemeral card (oldest lines are dropped first).
- `BREADBOARD_THINKING_PREVIEW_TTL_MS` (default: `1400`)
  - How long a closed preview remains visible after assistant response starts.
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
- `BREADBOARD_MARKDOWN_STABLE_TAIL_FLUSH_CHARS` (default: `96`)
  - Soft-tail escape hatch for long plain-text deltas without markdown boundaries.
  - Only applies when parser state is not inside fenced code/list/table regions.
  - Set to `0` to disable.
- `BREADBOARD_STREAM_MDX_LIVE_TOKENS` (default: `0`)
  - Optional live tokenization mode for stream-mdx worker path.

## Tool Rendering Policy Runtime

- Tool renderer registry is now pluggable via `useToolRenderer({ registry })`.
  - Default behavior is non-breaking: the preloaded legacy handler preserves existing rendering.
  - Source:
    - `tui_skeleton/src/repl/components/replView/renderers/toolRendererRegistry.ts`
- Per-tool compact/expanded policy is configurable via `useToolRenderer({ policyProvider })`.
  - `mode: "auto" | "compact" | "expanded"`
  - Optional overrides:
    - `collapseThreshold`
    - `collapseHead`
    - `collapseTail`
  - Source:
    - `tui_skeleton/src/repl/components/replView/renderers/toolRenderPolicy.ts`

## Tool Artifact Export Runtime (`artifact_ref_v1`)

- `BREADBOARD_TOOL_ARTIFACT_INLINE_MAX_BYTES` (default: `12000`)
  - If a tool output payload exceeds this size, the display path may export to an artifact reference.
- `BREADBOARD_TOOL_ARTIFACT_DIFF_INLINE_MAX_BYTES` (default: `16000`)
  - Same as above, but for unified diff payloads.
- `BREADBOARD_TOOL_ARTIFACT_OUTPUT_ROOT` (default: `docs_tmp/tui_tool_artifacts`)
  - Workspace-relative or absolute output root for exported artifact payload files.

Contract notes:
- Artifact refs use schema `artifact_ref_v1` and are carried in display payloads via `detail_artifact`.
- Non-ambiguous contract: for oversized payloads, renderer consumes artifact refs and avoids large inline detail/diff payload duplication.
- Replay fixture validation:
  - `python scripts/validate_replay_artifact_refs.py --glob "config/cli_bridge_replays/**/*.jsonl"`

## Transcript Memory Bounds Runtime

- `BREADBOARD_TRANSCRIPT_MEMORY_BOUNDS_V1` (default: `false`)
  - Enables transcript memory bounds and deterministic tail-preserving compaction.
- `BREADBOARD_TRANSCRIPT_MEMORY_BOUNDS_MAX_ITEMS` (default: `1200`)
  - Maximum transcript item count retained when memory bounds are enabled.
- `BREADBOARD_TRANSCRIPT_MEMORY_BOUNDS_MAX_BYTES` (default: `2000000`)
  - Maximum estimated transcript payload bytes retained when memory bounds are enabled.
- `BREADBOARD_TRANSCRIPT_MEMORY_BOUNDS_MARKER` (default: `true`)
  - Prepends a deterministic compaction marker (`[transcript_compacted] ...`) when older entries are dropped.
  - Marker includes compacted-count and estimated byte metadata for replay/debug visibility.

## Runtime Telemetry Additions

- Thinking preview lifecycle counters are emitted under `/runtime telemetry`:
  - `thinkingPreviewOpened`
  - `thinkingPreviewClosed`
  - `thinkingPreviewExpired`
- Optimistic tool/diff reconciliation counters are emitted under `/runtime telemetry`:
  - `optimisticToolRows`
  - `optimisticToolReconciled`
  - `optimisticDiffRows`
  - `optimisticDiffReconciled`

## Accessibility Runtime

- `BREADBOARD_TUI_SCREEN_READER` (default: `false`)
  - Enables accessibility-first rendering mode.
- `BREADBOARD_TUI_ACCESSIBLE_LAYOUT` (default: `false`)
  - Legacy compatibility alias for enabling accessibility-first mode.
- `BREADBOARD_TUI_SCREEN_READER_PROFILE` (default: `balanced`)
  - Controls verbosity while accessibility mode is enabled.
  - Supported values:
    - `concise`: minimal chrome and shortest shortcut/hint summaries.
    - `balanced`: default profile preserving current accessibility behavior.
    - `verbose`: expanded shortcut context and larger hint windows.

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

## Operator Rollback + Debug Runbook (Registry/Policy/Bounds)

Fast rollback sequence:
1. Set `BREADBOARD_TRANSCRIPT_MEMORY_BOUNDS_V1=false` to disable compaction behavior.
2. Keep default renderer path (no custom registry handlers, no custom policy provider).
3. Run strict local verification:
   - `cd tui_skeleton && npm run runtime:gates:strict`
   - `cd tui_skeleton && npx vitest run src/repl/components/replView/renderers/__tests__/toolRenderer.test.tsx src/repl/components/replView/controller/__tests__/transcriptMemoryBounds.test.ts`

Debug sequence:
1. Enable bounds with conservative settings:
   - `BREADBOARD_TRANSCRIPT_MEMORY_BOUNDS_V1=true`
   - `BREADBOARD_TRANSCRIPT_MEMORY_BOUNDS_MAX_ITEMS=800`
   - `BREADBOARD_TRANSCRIPT_MEMORY_BOUNDS_MAX_BYTES=1500000`
2. Validate compaction marker appears only when needed.
3. Re-run strict replay/tmux scenarios for tool-heavy and long-session lanes before widening rollout.

## Ink Feature-Steal Validated Defaults (2026-02-18)

The following defaults were validated in strict replay and runtime gates during the >95% Ink references tranche:

- `BREADBOARD_ACTIVITY_ENABLED=true`
- `BREADBOARD_THINKING_PREVIEW_ENABLED=true`
- `BREADBOARD_THINKING_PREVIEW_MAX_LINES=5`
- `BREADBOARD_THINKING_PREVIEW_TTL_MS=1400`
- `BREADBOARD_MARKDOWN_COALESCING_ENABLED=true`
- `BREADBOARD_EVENT_COALESCE_MS=80`
- `BREADBOARD_EVENT_COALESCE_MAX_BATCH=128`
- `BREADBOARD_TOOL_ARTIFACT_INLINE_MAX_BYTES=12000`
- `BREADBOARD_TOOL_ARTIFACT_DIFF_INLINE_MAX_BYTES=16000`
- `BREADBOARD_TRANSCRIPT_MEMORY_BOUNDS_V1=false` (default-safe; bounded mode remains opt-in)
- `BREADBOARD_TUI_ALT_BUFFER_VIEWER=false` (default-safe; opt-in lane validated in strict replay)
- `BREADBOARD_TUI_SCREEN_READER=false` (default-safe; opt-in)
- `BREADBOARD_TUI_SCREEN_READER_PROFILE=balanced` (supports `concise|balanced|verbose`)

Validated strict scenario set:

- `phase4_replay/subagents_strip_churn_v1`
- `phase4_replay/resize_overlay_interaction_v1`
- `phase4_replay/thinking_lifecycle_expiration_v1`
- `phase4_replay/large_diff_artifact_v1`
- `phase4_replay/large_output_artifact_v1`
- `phase4_replay/alt_buffer_enter_exit_v1` (with target env `BREADBOARD_TUI_ALT_BUFFER_VIEWER=1`)

Operator runbook reference:

- `docs_tmp/cli_phase_5/ink_references/INK_FEATURE_STEAL_OPERATOR_RUNBOOK_20260218.md`
