# claude-code-logged

Instrumentation layer for the Anthropic Claude Code CLI. This wrapper mirrors the stock CLI bits but injects a global `fetch` hook so every provider call (Anthropic, OpenRouter, etc.) is written to a structured JSON file for downstream analysis.

## Layout

```
tools/claude_code_logged/
├── README.md
├── scripts/
│   └── build.mjs
├── src/
│   ├── api_logger.js
│   └── fetch_logger.js
└── sample_output/
    ├── haiku45_v2035_request.json
    └── haiku45_v2035_response.json
```

## Prerequisites

- **Node.js ≥ 18** (ESM + global `fetch` support).
- A local copy of the vendor CLI. The build script defaults to the workspace copy installed at `industry_coder_refs/claude_code_cli/node_modules/@anthropic-ai/claude-code` but you can point to any extracted release.
- Optional: `npm install @anthropic-ai/claude-code@<version> --prefix industry_coder_refs/claude_code_cli` to download the latest bundle on demand.

## Build Instructions

1. Install / refresh the vendor package (optional):
   ```bash
   npm install @anthropic-ai/claude-code@latest --prefix industry_coder_refs/claude_code_cli
   ```
2. Run the build script:
   ```bash
   node tools/claude_code_logged/scripts/build.mjs \
     --vendor industry_coder_refs/claude_code_cli/node_modules/@anthropic-ai/claude-code
   ```
3. Add the resulting wrapper to your `PATH`:
   ```bash
   export PATH="$PWD/tools/claude_code_logged/dist:$PATH"
   ```
4. Invoke it like the regular CLI:
   ```bash
   CLAUDE_CODE_LOG_DIR=$PWD/misc/claude_logs \
   CLAUDE_CODE_DISABLE_LOGGING=0 \
   claude-code-logged --print "hello"
   ```

## Logging Behaviour

- Logs land in `~/.local/share/claude_code/log/provider_dumps` by default. Override with `CLAUDE_CODE_LOG_DIR=/tmp/logs`.
- Header redaction: `x-api-key`, `authorization`, and `x-client-api-key` are automatically zeroed out before writing.
- Disk guardrail: when the log directory exceeds 5 GiB we emit a warning (no auto-delete).
- Opt-out: set `CLAUDE_CODE_DISABLE_LOGGING=1`, `CLAUDE_CODE_DISABLE_PROVIDER_LOGGING=1`, or pass `--disable-provider-logging` / `--no-provider-logs` on the CLI.
- Metadata: `workspacePath` and `sessionId` are filled from `CLAUDE_CODE_WORKSPACE`, `CLAUDE_CODE_SESSION_ID`, or `CLAUDE_SESSION_ID` with fallbacks to `process.cwd()` / `null`.

## Sample Output

Scrubbed examples collected from a `claude-haiku-4-5-20251001` run live under `sample_output/`. Use them to validate ingestion pipelines without touching real API keys.

## Notes

- The build script copies the vendor tree verbatim, rewrites `package.json` to expose the `claude-code-logged` binary, injects `src/fetch_logger.js` + `src/api_logger.js`, and prepends the logger bootstrapping code to `cli.js`.
- Regenerate the wrapper any time Anthropic ships a new CLI version.
- The generated logs follow the schema documented in `docs/phase_5/claude_code_logging_report.md`.
