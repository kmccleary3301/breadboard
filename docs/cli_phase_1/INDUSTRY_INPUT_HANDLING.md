# Industry Input Handling Survey

This note distills how the reference CLIs inside `industry_coder_refs/` manage keyboard and clipboard input. Each section highlights the relevant files plus the takeaways that should influence KyleCode's TUI.

## Gemini CLI (`industry_coder_refs/gemini-cli`)
- **Bracketed paste lifecycle** — `packages/cli/src/ui/hooks/useBracketedPaste.ts` toggles `\x1b[?2004h`/`\x1b[?2004l` when the Ink app mounts or exits, and registers OS signal handlers so the terminal is always restored. This keeps stray `~` characters from leaking even if the process crashes.
- **Input routing + tests** — `packages/cli/src/ui/components/InputPrompt.test.tsx` and `packages/cli/src/ui/contexts/KeypressContext.test.tsx` simulate full bracketed paste flows (including unfocused prompts) so regressions are caught without manual tmux sessions. Their pattern: feed `ESC [200~` / payload / `ESC [201~` into the hook and assert the buffered text.
- **Modal editor contract** — the shared text buffer (`packages/cli/src/ui/components/shared/text-buffer.ts`) treats printable text, control movement, and clipboard state as separate reducers. Clipboard writes never run inside `useInput`; instead, the buffer records when the pipeline is in “clipboard mode” so Ctrl+V can be delegated cleanly to a shell command.

**KyleCode implications**: keep bracketed‑paste enable/disable side effects isolated (our `terminalControl.ts`), and mirror Gemini’s test strategy by exercising the pure input pipeline rather than trying to drive the whole Ink tree.

## OpenCode CLI (`industry_coder_refs/opencode`)
- **OS-specific clipboard adapters** — `packages/opencode/src/cli/cmd/tui/util/clipboard.ts` detects macOS/Wayland/X11/Windows at runtime. It uses `osascript`, `wl-copy`, `xclip`, `xsel`, or PowerShell before falling back to `clipboardy`, and streams large payloads through temp files to avoid choking stdout.
- **Configurable bindings** — `packages/opencode/src/config/config.ts` exposes an `input_paste` option (default `ctrl+v`) so rebinding is user-visible. Internal components read that config rather than hardcoding key combos.
- **Image-aware pastes** — the clipboard util inspects MIME type; screenshots are converted to base64 and tagged with a placeholder inside the prompt so the UI can render an attachment bubble while the backend uploads the file. This keeps the text buffer clean even when binary data hits the pipeline.

**KyleCode implications**: our new clipboard hook should eventually call into a platform-aware helper (vs. only `clipboardy`) and store metadata when non-text data arrives, so the REPL can acknowledge attachments the way OpenCode does.

## Codex TUI (`industry_coder_refs/codex/codex-rs`)
- **Bracketed + non-bracketed heuristics** — `tui/src/bottom_pane/chat_composer.rs` tracks wall-clock intervals (`PASTE_BURST_CHAR_INTERVAL`) and bursts of plain characters to detect pastes even when the user’s terminal fails to emit bracket markers. It buffers such bursts, suppresses accidental `Enter`, and replaces massive payloads with a placeholder when they exceed `LARGE_PASTE_CHAR_THRESHOLD`.
- **Pending paste queue** — the same composer keeps `pending_pastes: Vec<(String, String)>` so transcripts log both the human-readable placeholder and the raw payload. This makes it trivial to retry a failed upload or convert the buffer back into editable text.
- **Unit coverage** — Rust tests (`codex-rs/tui/tests/...` with the `vt100-tests` feature) replay vt100 streams to assert bracketed paste, cursor travel, and burst heuristics. They don’t mock crossterm; they capture replay data and diff it, which aligns with the replay harness we’re building.

**KyleCode implications**: we should borrow Codex’s idea of a fallback “burst detector” so a noisy terminal (or SSH session without bracketed paste) doesn’t reintroduce stray `~`. Logging paste metadata separately also helps with auditability.

## Actionable Checklist for KyleCode
1. **Pure input pipeline tests** — keep expanding `tests/inputChunkProcessor.test.ts` with scenarios inspired by Gemini/Codex (e.g., unfocused paste, burst detection). This gives us regression coverage without fighting PTY limitations.
2. **Clipboard abstraction** — introduce an OpenCode-style `Clipboard` util that prefers platform-native commands but still supports `KYLECODE_FAKE_CLIPBOARD` for headless CI. Hook `LineEditor` to it instead of directly calling `clipboardy`.
3. **Burst heuristics** — add a timer-based detector (similar to Codex) for non-bracketed pastes so ssh sessions that strip `\x1b[200~` still collapse into a single insert.
4. **Signal-safe bracket toggling** — follow Gemini’s `useBracketedPaste` example by registering exit handlers inside `terminalControl.ts` so `enableBracketedPaste()` can’t leave the terminal in a broken state.

Pulling these ideas together keeps our REPL aligned with what the other code terminals already shipped into production.
