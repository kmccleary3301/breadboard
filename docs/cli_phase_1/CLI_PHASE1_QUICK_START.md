# Kyle CLI Quick Start (Phase 1)

## Prerequisites
1. Backend façade running locally (FastAPI service from `agentic_coder_prototype.api.cli_bridge`). Start with `python -m agentic_coder_prototype.api.cli_bridge.server`. Ensure `.env` contains provider API keys.
2. Node 18+ with npm installed.
3. Optional: set `KYLECODE_API_URL` if the backend is not on `http://127.0.0.1:9099`.

## Backend Defaults & Networking
- The CLI targets `http://127.0.0.1:9099` by default. Override via `KYLECODE_API_URL`, or set `KYLECODE_CLI_HOST` / `KYLECODE_CLI_PORT` before launching the FastAPI bridge (`python -m agentic_coder_prototype.api.cli_bridge.server`).
- Authentication: export `KYLECODE_API_TOKEN` for Bearer auth. The CLI automatically adds `Authorization: Bearer $KYLECODE_API_TOKEN` to every request.
- When running scripted tests, keep the bridge and engine on the same host so attachments and workspace paths line up (uploads land under `.kyle/attachments` in the engine workspace).
- Deterministic streaming reference: see `docs/cli_phase_1/MOCK_SSE_SPEC.md` for the mock SSE script format, provided fixtures, and CLI flags.

## Installation
```bash
cd kylecode_cli_skeleton
npm install
npm run build
```

### CLI-Friendly OpenCode Config
- Use `agent_configs/opencode_openai_gpt5nano_c_fs_cli_shared.yaml` for shared-engine testing. It clones the guardrailed GPT‑5 Nano profile but relaxes strict todo/guardrail rules so prompts like “Implement bubble sort…” return full plans instead of immediate guardrail interruptions. Pass it via `--config` when running `kyle ask`/`kyle repl --script` against the shared bridge.
- Example:
  ```bash
  KYLECODE_API_URL=http://127.0.0.1:9099 \
  node dist/main.js ask \
    --config /shared_folders/querylake_server/ray_testing/ray_SCE/agent_configs/opencode_openai_gpt5nano_c_fs_cli_shared.yaml \
    "Implement bubble sort in python"
  ```
  The captured transcript lives at `kylecode_cli_skeleton/scripts/coding_task_output_cli_shared_ask.txt` for reference.
- Scripted replay example (shared engine):
  ```bash
  cd kylecode_cli_skeleton
  KYLECODE_API_URL=http://127.0.0.1:9099 \
  node dist/main.js repl \
    --config /shared_folders/querylake_server/ray_testing/ray_SCE/agent_configs/opencode_openai_gpt5nano_c_fs_cli_shared.yaml \
    --script scripts/coding_task_cli_shared.json \
    --script-output scripts/coding_task_output_cli_shared.txt \
    --script-final-only
  ```
  This generates the transcript stored in `scripts/coding_task_output_cli_shared.txt`.

## Environment Variables
- `KYLECODE_API_URL` (default `http://127.0.0.1:9099`)
- `KYLECODE_API_TOKEN` (optional Bearer token)
- `KYLECODE_ENABLE_REMOTE_STREAM=1` to request remote SSE when talking to Ray sessions.
- `KYLECODE_SESSION_CACHE` to override the cache file location (default `~/.kyle/sessions.json`).

## Commands
- `kyle sessions --output table|json` — list backend + cached sessions.
- `kyle ask "Prompt" [--config cfg.yaml] [--workspace dir] [--model model-id] [--remote-stream]`
- `kyle resume [session-id] [--output json]`
- `kyle repl [--config cfg.yaml] [--model model-id] [--remote-stream]`

## Streaming Notes
- CLI requests streaming by default; use `--remote-stream` to opt-in to remote queue transport.
- While in `repl`, available slash commands: `/help`, `/quit`, `/clear`, `/status`.

## Testing
```bash
npm run typecheck
npm run build
npm test
```

### Stress CI Gate Reference
- `npm run stress:ci` launches the FastAPI mock bridge, runs the high-signal bundle (`modal_overlay`, `ctrl_v_paste`, `ctrl_v_paste_large`, `mock_hello`), executes the key-fuzz harness, and verifies that every case emits the required artifacts (see `TUI_STRESS_TESTS.md` for the full table). Override `STRESS_CI_CASES` if you need a narrower subset while debugging.
- Runtime budgets come from env vars: `TTFT_BUDGET_MS` (default `2500`), `SPINNER_BUDGET_HZ` (`12`), `MIN_SSE_EVENTS` (`0`), `MAX_TIMELINE_WARNINGS` (`1`), and `MAX_ANOMALIES` (`0`). Key-fuzz coverage is controlled via `STRESS_CI_KEY_FUZZ_ITERATIONS`, `STRESS_CI_KEY_FUZZ_STEPS`, and `STRESS_CI_KEY_FUZZ_SEED` (set iterations to `0` locally to skip fuzzing).
- Reference console logs for a successful and failed run live under `kylecode_cli_skeleton/artifacts/stress_ci_contract/{pass.log,fail.log}`. Attach those logs (or regenerate them) whenever you change the gate so reviewers can see the expected diagnostics. Key-fuzz artifacts land under `artifacts/stress/<ts>/key_fuzz/`—include them when filing bugs that originate from the fuzzer.

## Next Steps
- File/artifact commands now support local export (`files cat --out`), stdin-driven diff apply with summaries, and richer transcript rendering via `kyle render`.
- Additional integration tests should cover live backend runs once the server façade is stable.
