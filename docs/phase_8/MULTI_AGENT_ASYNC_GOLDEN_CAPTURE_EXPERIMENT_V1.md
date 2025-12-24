# Multi-Agent Async Golden Capture Experiment (Raw Request Bodies) — v1

Date: 2025-12-24  
Owner: BreadBoard / Phase 8  
Status: In progress (OpenCode + oh-my-opencode captured; Claude blocked by credits)

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
- [ ] Ensure provider dumps include the async Task/TaskOutput turns (verify in `normalized/`) (blocked: Anthropic credits too low)

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

- Run: `misc/opencode_runs/goldens/1.0.193/phase8_async_subagents_v1/runs/20251224_074535/`
- Tools observed (main session): `list`, `task` (x2)
- Observed multi-agent surface:
  - multiple tool calls in a single assistant step (`list` + two `task` calls)
  - each `task` output includes `<task_metadata>` with a child `session_id`

### 9.2 OpenCode + oh-my-opencode plugin

Primary (clean async background surface, minimal polling):
- Run: `misc/oh_my_opencode_runs/goldens/opencode_1.0.193__oh-my-opencode_2.5.1/phase8_async_subagents_v1/runs/20251224_080121/`
- Tools observed (main session): `background_task` (x2), `list` (x1), `background_output` (x2)

Additional (variance / other tool surfaces observed under nondeterminism):
- Run: `misc/oh_my_opencode_runs/goldens/opencode_1.0.193__oh-my-opencode_2.5.1/phase8_async_subagents_v1/runs/20251224_075507/`
- Tools observed: `call_omo_agent`, `background_output`, plus OpenCode tools (`read`, `task`, `list`) and occasional `bash`

### 9.3 Claude Code (logged build)

Blocked until credits restored:
- Anthropic API responds: “Your credit balance is too low to access the Anthropic API.”
- Verified via: `python scripts/smoke_anthropic_sonnet4.py --model anthropic/claude-haiku-4-5-20251001 --max-tokens 8 --temperature 0 --prompt 'Reply with OK.'`

### 9.4 Provider dump completeness note (OpenCode instrumentation)

We observed that OpenCode can fire-and-forget certain LLM calls (notably title generation). This can produce a `*_request.json` without a corresponding `*_response.json` if the process exits before the network request resolves.

Mitigation used for goldens:
- Patch OpenCode provider dump logger to track in-flight provider fetch promises so `ProviderDump.flush()` waits for them at process exit.
- This patch is applied locally to `industry_refs/opencode/packages/opencode/src/util/provider-dump.ts` (the `industry_refs/` tree is gitignored).
