## TUI Mock Backend Playbook

Use this when you need to run the Ink REPL scripts (modal/resize/paste/token stress tests) without access to real provider credentials. The engine is already configured; follow the steps below to spin up the mock backend and rerun your `kyle repl --script …` suite.

### 1. Start the FastAPI bridge with the mock config
From the KyleCode repo root:

```bash
conda run -n ray_sce_test bash -lc './scripts/run_cli_bridge_mock.sh'
```

Notes:
- The helper script sets `PYTHONPATH`, launches the FastAPI bridge, and tees stdout/stderr into `logging/cli_bridge.log`. Use `tail -f logging/cli_bridge.log` whenever a session hangs.
- Host/port respect `KYLECODE_CLI_HOST` / `KYLECODE_CLI_PORT` if you need to override them.
- Health check: `curl http://127.0.0.1:9099/health` should return `{"status":"ok"}`.
- The hardened `npm run stress:ci` gate immediately fails if the bridge is down (missing `/sessions` outputs). Always start the mock bridge first and include a note in bug reports if you intentionally ran without it.
- Deterministic SSE playback: pass `--case-mock-sse` (or set `STRESS_CI_CASE_MOCK_SSE=1`) so scenarios such as `mock_hello` talk to the Node mock SSE bridge. This keeps a live-provider baseline even when Ray is unavailable.
  - Bridge chaos knobs: `KYLECODE_CLI_LATENCY_MS`, `KYLECODE_CLI_JITTER_MS`, and `KYLECODE_CLI_DROP_RATE` control the FastAPI bridge’s artificial delays/drop rate. The CLI propagates these values into `timeline_summary.json.chaos`, so bundle manifests and CI logs include the active settings.
  - Available scripts: `scripts/mock_sse_sample.json` (happy path), `scripts/mock_sse_guardrail.json` (policy halt), and `scripts/mock_sse_diff.json` (tool + diff). Scenarios declare their defaults via `mockSseScript`, but you can override with `--mock-sse-script <path>` for ad-hoc testing.

### 2. Point the CLI at the mock engine
In `kylecode_cli_skeleton/`, set the API URL and run the stress scripts. Example:

```bash
export KYLECODE_API_URL=http://127.0.0.1:9099
npm run build  # once per edit session
node dist/main.js repl \
  --config ../agent_configs/opencode_mock_c_fs.yaml \
  --script scripts/modal_overlay_stress.json \
  --script-output scripts/modal_overlay_stress_output.txt \
  --script-final-only
```

Swap the `--script`/`--script-output` pair for the other scenarios:
- `scripts/resize_storm.json`
- `scripts/paste_flood.json`
- `scripts/token_flood.json`

Each run will produce a new `<name>_output.txt` snapshot you can check into the diff harness.

> Tip: The REPL command now resolves `--config` paths to absolute locations before sending them to the backend, so relative paths such as `../agent_configs/opencode_mock_c_fs.yaml` work no matter where the FastAPI service is launched.

### 2a. Clipboard & attachment scripts
The PTY harness scenarios (`scripts/ctrl_v_paste.json`, `scripts/attachment_submit.json`) need a deterministic clipboard payload. Export `KYLECODE_FAKE_CLIPBOARD` before launching the harness or use the new helper script:

```bash
# Rebuild once, then refresh every snapshot (repl + PTY) in one go
npm run build
npm run stress:snapshots
```

The stress runner sets `KYLECODE_FAKE_CLIPBOARD="Stress runner clipboard payload"` for Ctrl+V and a tiny inline PNG for attachment submission. It also exercises `/retry`, which now resubmits the last prompt along with any uploaded attachment IDs—inspect the hints/logs after the run to ensure the “Uploaded N attachments…” message appears only once per submission.

### 3. What the mock config does
- File: `agent_configs/opencode_mock_c_fs.yaml`
- Extends the OpenCode Grok profile but forces `mock/dev` as the provider (`provider_runtime` → `MockRuntime`).
- Emits deterministic tool calls: first turn `list_dir`, second turn `apply_unified_patch` with a tiny protofs skeleton, then “build/test” guidance. This keeps the planner/build loop and tool schema identical to real runs while avoiding outbound provider traffic.

### 3a. Attachment retry scenario

1. Start the bridge and export `KYLECODE_FAKE_CLIPBOARD=data:image/png;base64,...` (the stress runner uses the tiny 1×1 PNG from `scripts/run_stress_snapshots.sh`).
2. Run the PTY script:
   ```bash
   npm run devtools:pty -- \
     --script scripts/attachment_submit.json \
     --config ../agent_configs/opencode_cli_mock_guardrails.yaml \
     --base-url http://127.0.0.1:9099 \
     --snapshots scripts/attachment_submit_output_cli_shared.txt
   ```
3. Observe the transcript:
   - “Attachments queued” helper lines under the prompt.
   - After submission, a hint like “Uploaded 1 attachment (will resend on /retry)” plus a `[completion]` log entry referencing the upload.
4. Trigger `/retry 1` (in the PTY or interactive REPL) to confirm the cached attachment IDs are re-sent. `logging/cli_bridge.log` should show `POST /attachments` **once** and subsequent `/input` calls that reuse the returned `id`.

If `/retry` tries to re-upload the blob, inspect the CLI hints (`submissionHistory` should only grow when `/input` succeeds) and verify that you ran a build with the latest controller changes.

### 4. Switching back to real providers
- When OpenRouter/OpenAI creds are available again, change the `--config` flag back to whichever guardrailed YAML you normally use (e.g., `agent_configs/opencode_grok4fast_c_fs_guardrails.yaml`). No other CLI arguments need to change.
- The FastAPI bridge automatically uses whatever config path you pass in the `/sessions` request, so the same backend process works for both mock and real runs.

### 5. Quick checklist
- [ ] FastAPI bridge running (`./scripts/run_cli_bridge_mock.sh`)
- [ ] `KYLECODE_API_URL` exported in the CLI repo
- [ ] `--config ../agent_configs/opencode_mock_c_fs.yaml` set on every `kyle repl --script` invocation
- [ ] `npm run stress:snapshots` (or manual PTY runs) refreshed the clipboard + attachment outputs with `KYLECODE_FAKE_CLIPBOARD` set
- [ ] Regenerated `*_output.txt` snapshots stored next to their scripts

Ping if you need additional overrides (e.g., forcing a specific mode/model via `/sessions` metadata); the bridge already supports that through the request payload.

### 6. Lightweight Mock SSE Provider

When outbound provider traffic is unavailable you can replace the FastAPI bridge with the Node-based mock SSE service. It implements the minimal surface (`/sessions`, `/sessions/{id}/input`, `/sessions/{id}/events`, `/sessions/{id}` DELETE, `/health`) and replays a deterministic script instead of calling the engine.

1. Author a script – see `scripts/mock_sse_sample.json` for the expected `{ "delayMs": number, "event": SessionEvent }` shape.
2. Launch the server:

```bash
cd kylecode_cli_skeleton
tsx tools/mock/mockSseServer.ts --script scripts/mock_sse_sample.json --port 9191
```

3. Point the CLI at it (or let the stress runner manage it):

```bash
export KYLECODE_API_URL=http://127.0.0.1:9191
kyle repl --config ../agent_configs/opencode_cli_mock_guardrails.yaml --script scripts/mock_hello_script.json --script-output mock.txt --script-final-only
```

For the scripted suite, per-case mock playback is now automatic for any scenario that declares `mockSseScript`:

```bash
npm run stress:bundle -- --case mock_hello --no-zip --out-dir artifacts/stress_mock_sse
```

Flags:
- `--loop` keeps replaying the script for repeated `/input` calls (handy for long-running sessions).
- `--delay-multiplier <x>` scales every `delayMs` entry (e.g., `0.5` to speed up tokens).

Reference bundles for `mock_hello` and `mock_multi_tool` live under `artifacts/stress_mock_sse/reference_mock`. Pass `--no-case-mock-sse` if you need to hit the FastAPI bridge even when a scenario advertises a mock script.

### FastAPI helper script integration

`scripts/run_cli_bridge_mock.sh` can now launch the deterministic SSE player for you. Set:

```bash
export KYLECODE_CLI_MOCK_SSE_SCRIPT=scripts/mock_sse_sample.json
export KYLECODE_CLI_MOCK_SSE_HOST=127.0.0.1    # optional
export KYLECODE_CLI_MOCK_SSE_PORT=9191         # optional
export KYLECODE_CLI_MOCK_SSE_LOOP=1            # optional
export KYLECODE_CLI_MOCK_SSE_DELAY_MULTIPLIER=1 # optional
export KYLECODE_CLI_MOCK_SSE_JITTER_MS=0         # optional
export KYLECODE_CLI_MOCK_SSE_DROP_RATE=0        # optional
```

Running the script will spawn the SSE player (logs go to `logging/cli_bridge.log`). Set `KYLECODE_CLI_MOCK_SSE_ONLY=1` if you only want the SSE mock (no FastAPI bridge).

> Limitations: file browsing and attachment uploads are stubbed out; use this mock for deterministic streaming/guardrail testing when the real engine is unreachable. Timeline summaries (`timeline_summary.json`) now include a `chaos` block when mock SSE is active, and `ttydoc.txt` mirrors the same metadata.
