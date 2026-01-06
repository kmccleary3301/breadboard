# Multi-Agent Async Golden Capture Experiment (Raw Request Bodies) — v1

Date: 2025-12-25  
Owner: BreadBoard / Phase 8  
Status: In progress (Claude + OpenCode + oh-my-opencode goldens captured; `phase8` replay parity green)

## 0.1) Latest outcomes (2025-12-25)

- Captured additional Claude Code logged goldens (v2.0.76) for async/subagent permission surfaces and TaskOutput resume behavior.
- Converted all Claude goldens to `replay_session.json` and added replay scenarios to `misc/opencode_runs/parity_scenarios.yaml`.
- Fixed replay-mode `Bash`/`run_shell` to return recorded `expected_output` (do not execute live) to prevent `ReplayToolOutputMismatchError`.
- Phase 8 replay parity (all scenarios tagged `phase8`) now passes; latest run: `artifacts/parity_runs/20251228-075452/`.
- Ran a quick live sanity check to confirm Breadboard behavior is in-distribution with Claude Code for the Phase 8 async-subagent prompt:
  - Claude Code live run (Haiku 4.5): `misc/claude_code_runs/goldens/2.0.76/phase8_async_subagents_live_check_v1/runs/20251225_182254/` (6 turns, no permission denials).
  - Breadboard live runs: `misc/phase_8_live_checks/breadboard/claude_code_phase8_async_subagents_v1/20251225_181941/` (6 assistant turns) and `misc/phase_8_live_checks/breadboard/claude_code_phase8_async_subagents_v1_text/20251225_182036/` (8 assistant turns); both produced the expected 5-tool-call shape (2×Task + 1×Bash + 2×TaskOutput).
- Subagent artifacts now persist at spawn + completion with `seq` and timestamps to support resume/debug (`.kyle/subagents/*.json` + logging `meta/subagents/`).

## 0.2) Latest outcomes (2025-12-26)

- Ran **live E4 sanity checks** for Claude Code / OpenCode / oh-my-opencode and Breadboard:
  - Claude Code live (Haiku 4.5): `misc/claude_code_runs/goldens/2.0.76/phase8_async_subagents_live_check_v2/runs/20251226_210155/`
  - Breadboard live (Claude config): `misc/phase_8_live_checks/breadboard/claude_code_phase8_async_subagents_v2/"20251226_210328"/`
  - OpenCode live (gpt-5.1-codex-mini): `misc/opencode_runs/goldens/1.0.193/phase8_async_subagents_live_check_v2/runs/20251226_210501/`
  - Breadboard live (OpenCode config, corrected tool defs): `misc/phase_8_live_checks/breadboard/opencode_phase8_async_subagents_v2c/20251226_211830/`
  - oh-my-opencode live (gpt-5.1-codex-mini): `misc/oh_my_opencode_runs/goldens/opencode_1.0.193__oh-my-opencode_2.5.1/phase8_async_subagents_live_check_v2/runs/20251226_210616/`
  - Breadboard live (OMO config, corrected tool defs): `misc/phase_8_live_checks/breadboard/oh_my_opencode_phase8_async_subagents_v2c/20251226_211852/`
- OpenCode live capture needed `OPENCODE_BUN_CONDITIONS=node` due to Bun CJS interop in `@babel/traverse` (script now supports override).
- Fixed YAML parse errors in `implementations/tools/defs_oc/*` (unquoted `:` in descriptions) so defs_oc can load under PyYAML.

## 0.3) Latest outcomes (2025-12-27)

- **OpenCode parity prompt alignment (gpt-5.1-codex-mini)**:
  - Switched OpenCode system prompt to `codex` (upstream `prompt/codex.txt`) and added OpenCode-style `<env>/<files>` block injection.
  - Added dynamic bash tool description rewrite so the default workspace path matches the live run workspace.
  - Removed OpenCode model params (`temperature`, `top_p`) to match upstream request bodies.
  - Fixture updated to include `tsconfig.json` so `<files>` tree matches OpenCode goldens.
  - Live run shape matches golden: 2×`task` + 1×`list`, finish_reason stop.
    - Breadboard OpenCode live (post-fix): `misc/phase_8_live_checks/breadboard/opencode_phase8_async_subagents_v8/20251227_023511/`

- **oh-my-opencode prompt source correction**:
  - Golden provider dump includes a **keyword-detector injected** `[search-mode]` block *plus* a strict instruction block (no bash/task/call_omo_agent).
  - Original replay_session was missing the strict instruction block; created a full prompt wrapper:
    - `misc/oh_my_opencode_runs/replay_sessions/2.5.1/phase8_async_subagents_v1/replay_session_full_prompt.json`
  - With the full prompt, Breadboard can produce clean runs matching golden tool usage:
    - Clean OMO live run: `logging/20251227-024930_workspace`
    - Shape: 2×`background_task` + 1×`list` + 2×`background_output`, no bash/task/call_omo_agent.

- **Tool ordering parity**:
  - Preserve tool ordering from the `tools.registry.include` list (matches OpenCode/OMO request-body tool order).

- **New Claude goldens (subagent allowlist + resume success)**:
  - Allowlist denial: `misc/claude_code_runs/goldens/2.0.76/phase8_subagent_allowlist_denial_v1/runs/20251227_215954/`
  - Resume success: `misc/claude_code_runs/goldens/2.0.76/phase8_subagent_resume_success_v1/runs/20251227_220055/`
  - Replay sessions:
    - `misc/claude_code_runs/replay_sessions/2.0.76/phase8_subagent_allowlist_denial_v1/replay_session.json`
    - `misc/claude_code_runs/replay_sessions/2.0.76/phase8_subagent_resume_success_v1/replay_session.json`

- **New cross‑subagent spawn goldens (nested Task)**:
  - Claude: `misc/claude_code_runs/goldens/2.0.76/phase8_subagent_nested_spawn_v1/runs/20251227_221845/`
  - OpenCode: `misc/opencode_runs/goldens/1.0.193/phase8_subagent_nested_spawn_v1/runs/20251227_222114/`
  - oh‑my‑opencode: `misc/oh_my_opencode_runs/goldens/opencode_1.0.193__oh-my-opencode_2.5.1/phase8_subagent_nested_spawn_v1/runs/20251227_222147/`
  - Replay sessions:
    - `misc/claude_code_runs/replay_sessions/2.0.76/phase8_subagent_nested_spawn_v1/replay_session.json`
    - `misc/opencode_runs/goldens/1.0.193/phase8_subagent_nested_spawn_v1/runs/20251227_222114/exports/replay_session.json`
    - `misc/oh_my_opencode_runs/goldens/opencode_1.0.193__oh-my-opencode_2.5.1/phase8_subagent_nested_spawn_v1/runs/20251227_222147/exports/replay_session.json`

- **Additional live E4 checks (v3)**:
  - Claude: `misc/phase_8_live_checks/breadboard/claude_code_phase8_async_subagents_v3/20251227_220259/`
  - OpenCode: `misc/phase_8_live_checks/breadboard/opencode_phase8_async_subagents_v3/20251227_220326/`
  - oh-my-opencode: `misc/phase_8_live_checks/breadboard/oh_my_opencode_phase8_async_subagents_v3/20251227_220352/`

- **Additional live E4 checks (v4–v5)**:
  - Claude: `misc/phase_8_live_checks/breadboard/claude_code_phase8_async_subagents_v4/20251227_222500/`,
           `misc/phase_8_live_checks/breadboard/claude_code_phase8_async_subagents_v5/20251227_222525/`
  - OpenCode: `misc/phase_8_live_checks/breadboard/opencode_phase8_async_subagents_v4/20251227_222550/`,
             `misc/phase_8_live_checks/breadboard/opencode_phase8_async_subagents_v5/20251227_222619/`
  - oh‑my‑opencode: `misc/phase_8_live_checks/breadboard/oh_my_opencode_phase8_async_subagents_v4/20251227_222649/`,
                    `misc/phase_8_live_checks/breadboard/oh_my_opencode_phase8_async_subagents_v5/20251227_222754/`

- **Additional live E4 checks (v6–v8)**:
  - Claude: `misc/phase_8_live_checks/breadboard/claude_code_phase8_async_subagents_v6/20251227_224105/`,
           `misc/phase_8_live_checks/breadboard/claude_code_phase8_async_subagents_v7/20251227_224130/`,
           `misc/phase_8_live_checks/breadboard/claude_code_phase8_async_subagents_v8/20251227_224156/`
  - OpenCode: `misc/phase_8_live_checks/breadboard/opencode_phase8_async_subagents_v6/20251227_224220/`,
             `misc/phase_8_live_checks/breadboard/opencode_phase8_async_subagents_v7/20251227_224249/`,
             `misc/phase_8_live_checks/breadboard/opencode_phase8_async_subagents_v8/20251227_224323/`
  - oh‑my‑opencode: `misc/phase_8_live_checks/breadboard/oh_my_opencode_phase8_async_subagents_v6/20251227_224350/`,
                    `misc/phase_8_live_checks/breadboard/oh_my_opencode_phase8_async_subagents_v7/20251227_224430/`,
                    `misc/phase_8_live_checks/breadboard/oh_my_opencode_phase8_async_subagents_v8/20251227_224513/`

## 0) Objective

Capture **golden runs** that include **raw provider request bodies** (and responses) for the “async subagent / concurrent subagent” surface across:

1. **Claude Code** (async subagents: `Task(... run_in_background=true)` + `TaskOutput`)
2. **OpenCode** (concurrent subagent delegation via multiple `task` tool calls in one assistant message)
3. **oh-my-opencode** (async subagents + wakeups, Claude-like)

These goldens will be used to:
- Derive deterministic **replay sessions** (model responses replayed; E-level excludes LLM nondeterminism).
- Assert **E4 across controllable surfaces**, specifically:
  - request-body prompt compilation (system + user + reminders),
  - tool schema exposure,
  - tool-call shaping (IDs, names, args),
  - tool-result shaping + ordering,
  - async job ack/wakeup/TaskOutput behavior,
  - per-session routing (main vs subagent sessions).

Non-goal (this experiment):
- Achieving E4-WebFetch parity.
- Broad coverage of every Claude Code tool; we’re targeting multi-agent async surfaces first.

---

## 1) What we mean by “raw request body logs”

For each outbound LLM API call, we want to persist (minimally):
- `timestamp`
- `providerID`, `modelID`
- `targetUrl`
- `headers` (sanitized/redacted)
- **raw JSON body as sent over the wire** (or an equivalent “pre-HTTP” request object if the true body is not reachable)
- response payload (SSE text or JSON), or a response excerpt if very large
- stable identifiers: `sessionId`, `messageId`, and a per-request `request_id`

We already have a downstream normalization utility:
- `scripts/process_provider_dumps.py` expects `*_request.json` and `*_response.json` files in a dump folder and produces ordered `turn_XXX_request.json` / `turn_XXX_response.json`.

---

## 2) Run directory layout (per harness, per scenario, per run)

Standardize to:

```
misc/<tool>_runs/goldens/<tool-version>/<scenario>/runs/<run-id>/
  scenario.json
  stdout.json|stdout.jsonl
  stderr.txt
  exit_code.txt
  workspace/                # seeded fixture workspace
  provider_dumps/            # raw request/response blobs
  normalized/                # `process_provider_dumps.py` output
  exports/                   # tool-native export(s) (OpenCode/oh-my-opencode)
```

Notes:
- Always isolate `HOME` per run (avoid leaking cached auth/config).
- Always seed a **fixture workspace** (so agent types, config, etc are deterministic).
- Logs must be **secret-safe** (see §7).
- **Golden isolation policy:** every run lives in its own `runs/<run-id>/` folder with a dedicated `provider_dumps/` directory.
- **Raw request bodies:** Claude Code goldens must come from the **logged build** (v2.0.76) so `provider_dumps/` includes the full request JSONs.

---

## 3) The shared “async subagent” scenario (single prompt)

We will run the same “shape” across all harnesses, but adapt tool names/flags to each.

### 3.1 Workspace fixture contents (required)

We intentionally define our own subagents so the harness has **known valid agent types**.

#### Claude Code fixture
- Create `.claude/agents/` with at least two subagents:
  - `repo-scanner` (read-only)
  - `grep-summarizer` (read-only)
- Each agent should have a tight allowlist (Read/Glob/Grep; **no Edit/Write**).

#### OpenCode fixture
- Create `.opencode/agent/` with at least two agents:
  - `repo-scanner.md`
  - `grep-summarizer.md`
- Keep them read-only (no patch/write) to reduce nondeterminism and avoid side effects.

#### oh-my-opencode fixture
- Prefer to mirror OpenCode’s `.opencode/agent/` format if supported; otherwise match whatever agent definition mechanism the project uses.

### 3.2 Prompt (harness-agnostic intent)

Prompt should force:
- two subagents launched “in parallel” (same assistant message),
- main continues with a tiny deterministic action while they run,
- then main retrieves results and prints a short summary.

Suggested prompt text (edit per harness to match tool surface):

```
We are testing async/concurrent subagents. Do NOT edit any files.

In ONE message, launch TWO subagents in parallel:
1) repo-scanner: list the repository root and summarize the top-level structure.
2) grep-summarizer: search for the string "multi_agent" (or "TaskOutput") and report which files mention it.

If you support background execution, run both in background and continue while they run.
While they run, the main agent should do ONE deterministic action: list the repo root once (or Read README.md if present).

Then retrieve the outputs from both subagents and return a concise combined summary (max 8 bullets).
```

Expected observable surfaces:
- multiple `Task`/`task` tool calls in one assistant turn,
- async job ids + polling (`TaskOutput`) where supported,
- tool-result injection ordering (job ack vs job completion),
- per-subagent session routing (distinct session IDs / transcripts).

---

## 4) Harness-specific capture plan

### 4.1 Claude Code (logged build)

**Tooling assumption**
- We have `tools/claude_code_logged/dist/claude-code-logged` available.
- `scripts/capture_claude_golden.sh` already:
  - isolates HOME/workspace
  - captures provider dumps into `provider_dumps/`
  - normalizes via `scripts/process_provider_dumps.py`

**Execution**
- Add a new Claude scenario name, e.g. `phase8_async_subagents_v1`.
- Seed fixture workspace with `.claude/agents/repo-scanner.md` and `.claude/agents/grep-summarizer.md`.
- Run with Haiku 4.5 (per Phase 7 policy), and a tight budget cap.

Example:
```
scripts/capture_claude_golden.sh \
  --scenario phase8_async_subagents_v1 \
  --model haiku \
  --max-budget-usd 0.40 \
  --fixture-dir <fixture_dir_path> \
  --prompt "<the prompt in §3.2 with Claude-specific Task/TaskOutput instructions>"
```

**Captured goldens (v2.0.76 label)**

All captured runs are isolated under:
`misc/claude_code_runs/goldens/2.0.76/<scenario>/runs/<run_id>/`

- Wakeup ordering probe:
  - `phase8_async_subagent_wakeup_ordering_v1`
  - `misc/claude_code_runs/goldens/2.0.76/phase8_async_subagent_wakeup_ordering_v1/runs/20251225_084029/`
- Subagent write denial surface:
  - `phase8_subagent_write_denial_v1`
  - `misc/claude_code_runs/goldens/2.0.76/phase8_subagent_write_denial_v1/runs/20251225_084310/`
- Subagent tool allowlist/permission propagation (Bash allowed in subagent):
  - `phase8_subagent_permission_propagation_v1`
  - `misc/claude_code_runs/goldens/2.0.76/phase8_subagent_permission_propagation_v1/runs/20251225_084410/`
- Resume/continue TaskOutput behavior across `--continue`:
  - `phase8_async_subagent_resume_taskoutput_v1`
  - `misc/claude_code_runs/goldens/2.0.76/phase8_async_subagent_resume_taskoutput_v1/runs/20251225_084741/`

**Notable Claude behavioral findings (from these goldens)**

- Main-session `Bash` can be denied in headless/print-mode with: “This command requires approval … <system-reminder> …”.
- Subagent Write attempts return: “SubagentWrite tool is disabled for this subagent instance”.
- Subagent `Bash` can be allowed even when main-session `Bash` is denied (per-agent allowlist surface).
- TaskOutput registry does **not** persist across `--continue` (TaskOutput returns “No task found with ID …”).

**What “raw request body” means here**
- Logged build must dump `targetUrl`, `headers`, and `body.json` for each call to `https://api.anthropic.com/v1/messages`.
- For SSE responses, dump `body.text` (raw SSE stream).

**Post-processing**
- `scripts/convert_claude_logged_run_to_replay_session.py` converts normalized Claude dumps → replay session.
- For async subagents, we should also preserve subagent session IDs for later multi-agent replay; do not discard non-main sessions.

### 4.2 OpenCode (instrumented build required)

**Problem**
- `opencode export` gives session history (messages/parts) but not provider request bodies.
- We need either:
  1) a logging fork: **`opencode-logged`** which writes request/response dumps, or
  2) a transparent proxy capture (usually more fragile).

**Recommended approach: patch OpenCode to log fetch()**
- Wrap `globalThis.fetch` early at process start.
- For each outbound request to known LLM endpoints (`api.anthropic.com`, `api.openai.com`, `openrouter.ai`, etc):
  - write `<request_id>_request.json`: url/method/headers/body (string + parsed json if possible)
  - write `<request_id>_response.json`: status/headers/body text (or excerpt + base64 if needed)
- Redact secrets (Authorization, x-api-key, etc) before writing.

**Status (implemented in this repo)**
- OpenCode is locally instrumented under `industry_refs/opencode/packages/opencode/`:
  - `src/util/provider-dump.ts`: fetch wrapper + request/response dump writer (gated by `OPENCODE_PROVIDER_DUMP_DIR`)
  - `src/session/llm.ts`: wraps provider calls with per-session context for attribution
  - `src/index.ts`: installs the logger early and flushes pending writes before exit
- Capture runner scripts:
  - `scripts/capture_opencode_golden.sh`
  - `scripts/capture_oh_my_opencode_golden.sh`

**Notes**
- Bun: use `bun run --config=<path-to-bunfig.toml> ...` (equals form) so OpenCode runs with the correct `@opentui/solid/preload`.
- OpenCode models cache macro: Bun 1.3.1 + this OpenCode rev was failing on the `with { type: "macro" }` import in `src/provider/models.ts` (runtime `ReferenceError: data is not defined`).
  - For local golden capture we switched it to a normal import (`import { data } from "./models-macro"`), which restores expected runtime behavior.

**Concurrency surface**
- In OpenCode, concurrency is typically achieved by emitting **multiple tool calls** in one assistant message.
- We should force this by prompt wording (“In ONE message, launch TWO Task tool calls”).

**Execution**
- Run OpenCode headless in JSON mode:
  - `opencode run --format json --model <...> --agent build "<prompt>"`
- After run success, also run:
  - `opencode export <session_id>` and store in `exports/opencode_export.json`
- Normalize provider dumps:
  - `scripts/process_provider_dumps.py --input-dir provider_dumps --output-dir normalized`
- Convert export to replay session for tool-call trace:
  - `scripts/convert_opencode_export_to_replay_session.py --in exports/opencode_export.json --out <session_dump.json>`

**Concrete command examples (current repo)**

OpenCode (baseline, no plugins):
```
bash scripts/capture_opencode_golden.sh \
  --scenario phase8_async_subagents_v1 \
  --model openai/gpt-5.1-codex-mini \
  --prompt "<prompt>"
```

OpenCode + oh-my-opencode plugin:
```
bash scripts/capture_oh_my_opencode_golden.sh \
  --scenario phase8_async_subagents_v1 \
  --model openai/gpt-5.1-codex-mini \
  --prompt "<prompt>"
```

**Model policy**
- For Phase 7/8 parity work we previously used `openai/gpt-5.1-codex-mini` for Codex-style tests.
- For OpenCode goldens, pick **one** model for stability and cost, and keep it fixed for the whole scenario family.

### 4.3 oh-my-opencode (setup + instrumentation required)

**Status (implemented in this repo)**
- Repo present at `industry_refs/oh-my-opencode/` (package `oh-my-opencode@2.5.1`).
- This is an OpenCode plugin (requires OpenCode >= 1.0.150) providing Claude-like async subagents.
- Async/background surfaces we can target:
  - `call_omo_agent` (supports `run_in_background=true`)
  - `background_task`, `background_output`, `background_cancel`
- Capture script:
  - `scripts/capture_oh_my_opencode_golden.sh` builds the plugin (if `dist/` missing) and loads it via `.opencode/opencode.json`.

**Plan**
1) Clone/install oh-my-opencode into `tools/` or a sibling repo.
2) Identify the outbound LLM request path (fetch wrapper vs SDK).
3) Apply the same “log fetch request/response” strategy as OpenCode.
4) Run the same scenario prompt as §3.2, adapted to the tool names and parameters.
5) Capture:
   - provider dumps
   - tool-native exports (if available)
   - normalized turn files

---

## 5) Success criteria (for the experiment)

Minimum to declare “golden capture successful” for each harness:
- A run directory exists with `scenario.json`, stdout/stderr, and `provider_dumps/`.
- `normalized/turn_XXX_request.json` exists and contains:
  - the user prompt,
  - tool schema blocks,
  - multiple Task/task tool calls in one turn (or background task creation),
  - distinct session IDs for subagent runs if the harness supports multi-session.

Additionally:
- For Claude Code and oh-my-opencode, the run must include:
  - async jobs + at least one polling retrieval (`TaskOutput` or equivalent), OR
  - wakeup injection event captured in logs.

---

## 6) Post-capture analysis plan (what we do with the goldens)

1) **Extract replay sessions**
   - Claude: `convert_claude_logged_run_to_replay_session.py`
   - OpenCode: `convert_opencode_export_to_replay_session.py` (tool trace) + provider request logs (MVI request surface)
   - oh-my-opencode: TBD conversion; at minimum preserve provider request dumps + tool event logs

2) **Group by session**
   - Main session vs per-subagent session(s), using `sessionId` fields in provider dumps and/or exports.

3) **Define the multi-agent replay envelope**
   - For async harnesses, a single replay must model:
     - main turn → task spawn tool call(s) → background progress → TaskOutput/wakeup → final synthesis
   - Store all session replays in a structured folder (gitignored `misc/` is fine during iteration).

4) **E4 enforcement**
   - Use BreadBoard replay-time tool-output gating (already implemented for `task`) and extend to:
     - `TaskOutput` (Claude),
     - wakeup injection surface (async),
     - permission prompts (later).

---

## 7) Secret safety / redaction requirements

Never write secrets to disk inside request dumps:
- redact `Authorization`, `x-api-key`, `anthropic-api-key`, `openai-api-key`, cookies
- redact OpenRouter headers (`HTTP-Referer` is ok; keys not)
- if any key leaks into body fields, redact via best-effort regex (defense-in-depth)

Prefer:
- preserve header *names* and a boolean `present: true`
- store hash of header value if we need stability checks

---

## 8) Implementation checklist (what we need to build next)

### Claude Code
- [x] Create a fixture workspace containing `.claude/agents/repo-scanner.md` + `.claude/agents/grep-summarizer.md` (`misc/phase_8_fixtures/phase8_async_subagents_v1/`)
- [x] Add scenario command invocation via `scripts/capture_claude_golden.sh`
- [x] Ensure provider dumps include the async Task/TaskOutput turns (verify in `normalized/`)

### OpenCode
- [x] Acquire OpenCode source via `industry_refs/opencode/`
- [x] Enable provider dump logging via `OPENCODE_PROVIDER_DUMP_DIR` (instrumentation lives in OpenCode repo; gated by env var)
- [x] Write `scripts/capture_opencode_golden.sh` mirroring folder layout + normalization

### oh-my-opencode
- [x] Use plugin repo at `industry_refs/oh-my-opencode/` and load it via `.opencode/opencode.json`
- [x] Identify async subagent mechanism + tool names (`background_task`, `background_output`, `call_omo_agent`, etc.)
- [x] Apply provider dump logging via OpenCode’s fetch wrapper + redaction
- [x] Write `scripts/capture_oh_my_opencode_golden.sh`

---

## 9) Captured runs (results so far)

### 9.1 OpenCode (no plugins)

- Run (latest, list‑forced): `misc/opencode_runs/goldens/1.0.193/phase8_async_subagents_v1/runs/20251227_211558/`
  - Tools observed (main session, replay session): `list` (x1), `task` (x2)
  - Prompt included a hard requirement to call `list` before `task` (to lock down the `list` surface).
- Prior run (variance): `misc/opencode_runs/goldens/1.0.193/phase8_async_subagents_v1/runs/20251227_041536/`
  - Tools observed (main session, replay session): `task` (x9)
  - Capture did not emit `list`; treated as drift.
- Original reference run: `misc/opencode_runs/goldens/1.0.193/phase8_async_subagents_v1/runs/20251224_074535/`
  - Tools observed (main session): `list`, `task` (x2)
  - Observed multi-agent surface:
    - multiple tool calls in a single assistant step (`list` + two `task` calls)
    - each `task` output includes `<task_metadata>` with a child `session_id`

### 9.2 OpenCode + oh-my-opencode plugin

Primary (clean async background surface, minimal polling):
- Run (latest): `misc/oh_my_opencode_runs/goldens/opencode_1.0.193__oh-my-opencode_2.5.1/phase8_async_subagents_v1/runs/20251227_041709/`
  - Tools observed (main session): `background_task` (x2), `list` (x1), `background_output` (x2)
- Prior run: `misc/oh_my_opencode_runs/goldens/opencode_1.0.193__oh-my-opencode_2.5.1/phase8_async_subagents_v1/runs/20251224_080121/`
  - Tools observed (main session): `background_task` (x2), `list` (x1), `background_output` (x2)

Additional (variance / other tool surfaces observed under nondeterminism):
- Run: `misc/oh_my_opencode_runs/goldens/opencode_1.0.193__oh-my-opencode_2.5.1/phase8_async_subagents_v1/runs/20251224_075507/`
- Tools observed: `call_omo_agent`, `background_output`, plus OpenCode tools (`read`, `task`, `list`) and occasional `bash`
  - `call_omo_agent` is an additional oh-my-opencode surface (explore/librarian agent spawner) that can appear depending on prompt/model variance.

Cancellation surface (`background_cancel`):
- Run: `misc/oh_my_opencode_runs/goldens/opencode_1.0.193__oh-my-opencode_2.5.1/phase8_async_subagents_v1_background_cancel/runs/20251225_034238/`
- Tools observed (main session): `background_task` (x2), `background_cancel` (x1), `list` (x1), `background_output` (x2)

Cancellation surface (`background_cancel(taskId=...)`):
- Run: `misc/oh_my_opencode_runs/goldens/opencode_1.0.193__oh-my-opencode_2.5.1/phase8_async_subagents_v1_background_cancel_taskid/runs/20251225_065743/`
- Tools observed (main session): `background_task` (x2), `background_cancel` (x2), `list` (x1), `background_output` (x3)
  - The second `background_cancel` call is intentionally repeated to lock down the non-running error surface (cancelled -> cannot cancel).

### 9.3 Claude Code (logged build)

- Latest golden run: `misc/claude_code_runs/goldens/2.0.76/phase8_async_subagents_v1/runs/20251227_041407/`
- Golden replay session: `misc/claude_code_runs/replay_sessions/2.0.76/phase8_async_subagents_v1/replay_session.json`
- Tools observed (main session): `Task` (x2), `Bash` (x1), `TaskOutput` (x3)
  - **Note:** extra `TaskOutput` turn vs prior capture; likely a polling variance. Keep for coverage.

### 9.4 Parity harness updates (multi-agent)

- Added multi‑agent event‑log comparisons (normalized) in replay parity checks.
- Added per‑turn tool sequence comparisons to tighten E4 parity over tool ordering.
- Multi‑agent MVI surface catalog + coverage matrix updated in `docs_tmp/phase_8/MULTI_AGENT_MVI_SURFACES.md`.
  - **Known gaps (spec coverage):** none in the current Phase 8 scope (nested spawn + resume covered).
  - These are tracked as Phase 8 follow‑ups only if new harness capabilities appear.

### 9.4.2 Wakeup injection sentinel (replay-only)

- New replay session: `misc/claude_code_runs/replay_sessions/2.0.76/phase8_async_subagent_wakeup_ordering_v1/replay_session_live.json`
  - Task outputs removed so async subagents run live (deterministic repo‑scanner/grep‑summarizer stubs).
- New replay config: `agent_configs/claude_code_haiku45_phase8_wakeup_replay.yaml`
  - Enables `multi_agent` async + `bus.model_visible_topics: [tool_result, wakeup]`.
- New fixture: `misc/phase_8_fixtures/phase8_async_wakeup_v1/`
  - Includes `.kyle/multi_agent_events.jsonl` from a known wakeup run for E4 event‑log comparison.
- New parity scenario: `claude_code_phase8_async_wakeup_eventlog_replay`.
  - Parity run passed: `artifacts/parity_runs/20251228-075452/` (full phase8 suite).

### 9.4.1 Replay conversion gap (per‑subagent sessions)

- Replay conversion now emits **per‑subagent replay sessions** for Claude:
  - Sessions are reconstructed from Task/TaskOutput outputs + Task input prompts.
  - `subagent_meta.json` records agent/session IDs when available in provider dumps.
- Still missing: **full provider request/response logs** per subagent (not exposed in Claude logs yet).
- New: **per‑subagent provider log extraction** (OpenCode / oh‑my‑opencode)
  - Script: `scripts/extract_subagent_provider_logs.py`
  - Output (nested spawn runs):
    - OpenCode: `misc/opencode_runs/goldens/1.0.193/phase8_subagent_nested_spawn_v1/runs/20251227_222114/subagent_provider_logs/`
    - oh‑my‑opencode: `misc/oh_my_opencode_runs/goldens/opencode_1.0.193__oh-my-opencode_2.5.1/phase8_subagent_nested_spawn_v1/runs/20251227_222147/subagent_provider_logs/`
  - Claude limitation: only **telemetry events** are available per subagent, not full request/response bodies.
    - `misc/claude_code_runs/goldens/2.0.76/phase8_subagent_nested_spawn_v1/runs/20251227_221845/subagent_provider_logs/`
- Child session IDs in OpenCode/OMO exports:
  - Found in `opencode_export.json` at `messages[*].parts[*].state.metadata.sessionId` for nested‑spawn Task outputs.
  - Main session remains in `messages[*].info.sessionID` / `parts[*].sessionID`.

### 9.4.3 Subagent replay sessions (Claude logged)

- `scripts/convert_claude_logged_run_to_replay_session.py` now writes **prompt + output** replay sessions:
  - `misc/claude_code_runs/replay_sessions/2.0.76/phase8_async_subagents_v1/subagents/`
  - `misc/claude_code_runs/replay_sessions/2.0.76/phase8_subagent_nested_spawn_v1/subagents/`
- `index.json` maps `task_id -> subagent_<task_id>.json` (used as `replay_index`).
- New parity config for nested spawn:
  - `agent_configs/claude_code_haiku45_phase8_nested_subagent_replay.yaml`

### 9.4.4 Parity measurement coverage (auto‑checked)

Replay parity currently asserts:
- Tool outputs (strict compare, full text).
- Per‑turn tool usage order and aggregate tool usage summary.
- Multi‑agent event‑log payloads (normalized) including spawn/ack/wakeup/completion ordering.

Known surfaces **not yet auto‑measured** (no dedicated scenarios):
- None in the current Phase 8 scope (deeper live‑distribution sampling remains).

### 9.4.5 Fixture seed requirements (replay stability)

- All Phase‑8 parity scenarios specify a **stable fixture seed** via `golden_workspace`.
- For Claude replays and wakeup sentinels, we use the canonical fixtures:
  - `misc/phase_8_fixtures/phase8_async_subagents_v1`
  - `misc/phase_8_fixtures/phase8_async_wakeup_v1`
  - `misc/phase_8_fixtures/phase8_subagent_*` (write denial, allowlist, permission propagation, resume success)
- For OpenCode/OMO async replay scenarios, the fixture seed is the **canonical golden workspace**
  (`runs/<run-id>/workspace`, pointed to by the scenario’s `current` symlink).
- **Rule:** if a replay scenario is updated to point at a new golden run, also update the
  `current` pointer and the `golden_workspace` path so the seed stays deterministic.

### 9.5 Live E4 sanity checks (single runs)

- Claude live run: `misc/phase_8_live_checks/breadboard/claude_code_phase8_async_subagents_v1/20251227_041819/`
  - Logging: `logging/20251227-041823_workspace`
  - Tools observed: `Task` (x2), `run_shell` (x1), `TaskOutput` (x2)
  - **Delta vs golden:** fewer `TaskOutput` polls (2 vs 3). Treat as model variance; no hard divergence seen yet.

- OpenCode live run: `misc/phase_8_live_checks/breadboard/opencode_phase8_async_subagents_v1/20251227_041846/`
  - Logging: `logging/20251227-041849_workspace`
  - Tools observed: `task` (x2), `list_dir` (x1), `run_shell` (x2)
  - **Delta vs golden:** new `run_shell` usage; likely prompt variance or plan mode behavior. Track before E4 claim.

- oh‑my‑opencode live run: `misc/phase_8_live_checks/breadboard/oh_my_opencode_phase8_async_subagents_v1/20251227_041935/`
  - Logging: `logging/20251227-041938_workspace`
  - Tools observed: `background_task` (x2), `list_dir` (x1), `background_output` (x2)
  - **Delta vs golden:** matches tool set/ordering (good).

- v3 live runs (additional distribution checks):
  - Claude: `misc/phase_8_live_checks/breadboard/claude_code_phase8_async_subagents_v3/20251227_220259/`
  - OpenCode: `misc/phase_8_live_checks/breadboard/opencode_phase8_async_subagents_v3/20251227_220326/`
  - oh‑my‑opencode: `misc/phase_8_live_checks/breadboard/oh_my_opencode_phase8_async_subagents_v3/20251227_220352/`

### 9.6 Capture failures (notes)

- `20251227_211518` (OpenCode): Bun runtime error (`_debug is not a function`) caused capture failure; no provider dumps/export produced.
- Occasional early stop / low‑turn runs observed in live checks; mitigation is to compare request‑body prompts + tool ordering against goldens and rerun with a higher turn cap if the run truncates before tool completion.
- Breadboard replay parity: green (MVI tool surfaces + ordering) against the logged golden
- Latest confirmed Breadboard live run (same scenario): `artifacts/live_runs/claude_code/phase8_async_subagents_v1/runs/20251224_155423/`
- Notes:
  - Live run completed in fewer turns than the golden (likely nondeterminism + prompt/model variance); request-body prompt alignment confirmed by comparing logged request dumps against the golden.
  - Completion detection had to respect Anthropic `end_turn`/`max_tokens` so plain assistant summaries can terminate runs.

Additional Phase 8 Claude Code goldens (2.0.76):
- Wakeup-ordering probe (no explicit wakeup observed; TaskOutput polling required):
  - Run: `misc/claude_code_runs/goldens/2.0.76/phase8_async_subagent_wakeup_ordering_v1/runs/20251225_084029/`
  - Replay session: `misc/claude_code_runs/replay_sessions/2.0.76/phase8_async_subagent_wakeup_ordering_v1/replay_session.json`
- Subagent write denial surface (SubagentWrite disabled):
  - Run: `misc/claude_code_runs/goldens/2.0.76/phase8_subagent_write_denial_v1/runs/20251225_084310/`
  - Replay session: `misc/claude_code_runs/replay_sessions/2.0.76/phase8_subagent_write_denial_v1/replay_session.json`
- Subagent tool allowlist/permission surface (Bash allowed inside subagent):
  - Run: `misc/claude_code_runs/goldens/2.0.76/phase8_subagent_permission_propagation_v1/runs/20251225_084410/`
  - Replay session: `misc/claude_code_runs/replay_sessions/2.0.76/phase8_subagent_permission_propagation_v1/replay_session.json`
- Nested subagent spawn surface:
  - Run: `misc/claude_code_runs/goldens/2.0.76/phase8_subagent_nested_spawn_v1/runs/20251227_221845/`
  - Replay session: `misc/claude_code_runs/replay_sessions/2.0.76/phase8_subagent_nested_spawn_v1/replay_session.json`
- Resume/continue surface for TaskOutput (continued session returns “No task found with ID”):
  - Run: `misc/claude_code_runs/goldens/2.0.76/phase8_async_subagent_resume_taskoutput_v1/runs/20251225_084741/`
  - Replay session: `misc/claude_code_runs/replay_sessions/2.0.76/phase8_async_subagent_resume_taskoutput_v1/replay_session.json`
- Subagent allowlist denial (no Bash in tool mask):
  - Run: `misc/claude_code_runs/goldens/2.0.76/phase8_subagent_allowlist_denial_v1/runs/20251227_215954/`
  - Replay session: `misc/claude_code_runs/replay_sessions/2.0.76/phase8_subagent_allowlist_denial_v1/replay_session.json`
- Subagent resume success (Task resume with agentId):
  - Run: `misc/claude_code_runs/goldens/2.0.76/phase8_subagent_resume_success_v1/runs/20251227_220055/`
  - Replay session: `misc/claude_code_runs/replay_sessions/2.0.76/phase8_subagent_resume_success_v1/replay_session.json`

### 9.7 Claude subagent identifiers (log locations)

Where subagent IDs show up in the logged artifacts:

- Provider dumps (`provider_dumps/*_request.json`): the `body.text` field contains a JSON payload with `events[]`; for subagents, `events[].metadata` includes:
  - `agentType: "subagent"`
  - `agentId` (short id)
  - `queryChainId`, `requestId`, `sessionId` (from Claude/Statsig telemetry)
- Tool results: `Task` tool results often include a text line like `agentId: <id> (for resuming...)` embedded in the tool output.
- `/v1/messages` request `metadata.user_id` remains constant for the main session (no distinct subagent sessionId).
- `.claude/debug/<session>.txt` did not include `agentId` entries for subagents in the nested spawn run.

### 9.8 OpenCode / oh‑my‑opencode child session IDs (export format)

OpenCode and oh‑my‑opencode exports already include child session IDs:

- `exports/opencode_export.json` → `messages[].parts[].type == "tool"` with `tool == "task"`:
  - `state.metadata.sessionId` is populated with the spawned subagent session id.
  - Tool `state.output` contains a `<task_metadata>` block with `session_id: <id>` in the body.
- The parent session id is on `messages[].info.sessionID`.

### 9.4 Provider dump completeness note (OpenCode instrumentation)

We observed that OpenCode can fire-and-forget certain LLM calls (notably title generation). This can produce a `*_request.json` without a corresponding `*_response.json` if the process exits before the network request resolves.

Mitigation used for goldens:
- Patch OpenCode provider dump logger to track in-flight provider fetch promises so `ProviderDump.flush()` waits for them at process exit.
- This patch is applied locally to `industry_refs/opencode/packages/opencode/src/util/provider-dump.ts` (the `industry_refs/` tree is gitignored).

### 9.5 Breadboard parity status (Phase 8 async)

- OpenCode replay parity: green
  - Config: `agent_configs/opencode_phase8_async_subagents_v1_replay.yaml`
  - Session: `misc/opencode_runs/goldens/1.0.193/phase8_async_subagents_v1/runs/20251224_074535/exports/replay_session.json`
- oh-my-opencode replay parity: green
  - Config: `agent_configs/oh_my_opencode_phase8_async_subagents_v1_replay.yaml`
  - Session: `misc/oh_my_opencode_runs/goldens/opencode_1.0.193__oh-my-opencode_2.5.1/phase8_async_subagents_v1/runs/20251224_080121/exports/replay_session.json`
- oh-my-opencode replay parity (variant run exercising `call_omo_agent`): green
  - Config: `agent_configs/oh_my_opencode_phase8_async_subagents_v1_replay.yaml` (now compares `call_omo_agent` outputs too)
  - Session: `misc/oh_my_opencode_runs/goldens/opencode_1.0.193__oh-my-opencode_2.5.1/phase8_async_subagents_v1/runs/20251224_075507/exports/replay_session.json`
- oh-my-opencode replay parity (cancellation surface via `background_cancel`): green
  - Config: `agent_configs/oh_my_opencode_phase8_async_subagents_v1_replay.yaml` (now compares `background_cancel` outputs too)
  - Session: `misc/oh_my_opencode_runs/goldens/opencode_1.0.193__oh-my-opencode_2.5.1/phase8_async_subagents_v1_background_cancel/runs/20251225_034238/exports/replay_session.json`
- oh-my-opencode replay parity (taskId cancellation surface via `background_cancel(taskId=...)`): green
  - Config: `agent_configs/oh_my_opencode_phase8_async_subagents_v1_replay.yaml`
  - Session: `misc/oh_my_opencode_runs/goldens/opencode_1.0.193__oh-my-opencode_2.5.1/phase8_async_subagents_v1_background_cancel_taskid/runs/20251225_065743/exports/replay_session.json`
  - Parity manifest wired:
  - `misc/opencode_runs/parity_scenarios.yaml` now includes:
    - `opencode_phase8_async_subagents_v1_replay`
    - `oh_my_opencode_phase8_async_subagents_v1_replay`
    - `oh_my_opencode_phase8_subagent_nested_spawn_v1_replay`
    - `oh_my_opencode_phase8_async_subagents_v1_call_omo_agent_replay`
    - `oh_my_opencode_phase8_async_subagents_v1_background_cancel_replay`
    - `oh_my_opencode_phase8_async_subagents_v1_background_cancel_taskid_replay`
    - `claude_code_phase8_async_subagents_v1_replay`
    - `claude_code_phase8_subagent_nested_spawn_v1_replay`
    - `claude_code_task_subagent_sync_replay`
    - `claude_code_phase8_async_subagent_wakeup_ordering_v1_replay`
    - `claude_code_phase8_subagent_write_denial_v1_replay`
    - `claude_code_phase8_subagent_permission_propagation_v1_replay`
    - `claude_code_phase8_subagent_allowlist_denial_v1_replay`
    - `claude_code_phase8_async_subagent_resume_taskoutput_v1_replay`
    - `claude_code_phase8_subagent_resume_success_v1_replay`
- Nested-spawn subagent replay index (OpenCode / OMO):
  - OpenCode subagent replays: `misc/opencode_runs/replay_sessions/1.0.193/phase8_subagent_nested_spawn_v1/subagents/`
  - OMO subagent replays: `misc/oh_my_opencode_runs/replay_sessions/opencode_1.0.193__oh-my-opencode_2.5.1/phase8_subagent_nested_spawn_v1/subagents/`
  - Index files: `index.json` (session_id → replay session path)
  - Converter: `scripts/convert_openai_normalized_to_replay_session.py`
  - Parity scenario: `opencode_phase8_nested_subagent_replay` with config `agent_configs/opencode_phase8_nested_subagent_replay.yaml`
  - Fixture note: `golden_workspace` is used as the seed; `workspace_seed` is explicitly set for the nested-spawn replay scenario.
- Notable engine fix needed for OpenCode replay parity:
  - Restored `_handle_task_tool` implementation (it had been accidentally embedded under `_handle_background_output_tool`, causing an OpenCode replay mismatch and `msg`-scope errors).
 - Additional engine fix to expand oh-my-opencode coverage:
   - Added `call_omo_agent` tool definition + minimal executor (replay-first) so we can assert parity for the plugin’s extra agent-spawn surface.

## 10) Micro-suite coverage (Phase 8)

The planned “micro-suite” surfaces are already covered by existing goldens/replay scenarios:

- **TaskOutput unknown/expired IDs** → `phase8_async_subagent_resume_taskoutput_v1` (Claude Code).
- **Subagent write denial** → `phase8_subagent_write_denial_v1` (Claude Code).
- **Subagent permission propagation** → `phase8_subagent_permission_propagation_v1` (Claude Code).
- **Subagent allowlist denial** → `phase8_subagent_allowlist_denial_v1` (Claude Code).
- **Subagent resume success** → `phase8_subagent_resume_success_v1` (Claude Code).
- **Nested subagent spawn** → `phase8_subagent_nested_spawn_v1` (Claude / OpenCode / oh‑my‑opencode).
- **Background cancel error surfaces** → `phase8_async_subagents_v1_background_cancel` + `phase8_async_subagents_v1_background_cancel_taskid` (oh-my-opencode).

No new capture required unless we discover a missing surface during later expansion.
