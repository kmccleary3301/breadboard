# Handoff V10 — KyleCode (Grok 4 Fast C Filesystem Harness)

## 1. Project Overview

KyleCode is an agentic coding research platform intended to replicate the behavior of commercial IDE assistants (OpenCode, Claude Code, Cursor, Codex, etc.) while remaining fully configurable for experimentation. The system is designed so that **every behavior can be driven by Schema V2 YAML configs**—providers, tool suites, sandboxes, prompt strategies, turn sequencing, planning modes, completion arbitration, and telemetry. The repo you now own hosts the Ray-based backend that wires these abstractions together, enabling rapid A/B testing, RL training, and agentic behavior analysis.

The current focus is a Grok 4 Fast configuration that should reproduce the OpenCode filesystem build task: generate a C filesystem implementation from scratch, including header, source, tests, Makefile, demo, and README, then compile and validate it. The long-term goal is to use Grok as a fast, inexpensive model for automated benchmarking and fine-tuning of agent workflows.

## 2. Codebase Landmarks

- `agentic_coder_prototype/` — Core runtime: agent loop, providers, tool execution, Schema V2 compilation.
- `implementations/system_prompts/` — Prompt packs for planning/build modes.
- `implementations/tools/` — Text-dialect tool definitions (OpenCode-style patch, bash, read_file, etc.).
- `scripts/log_reduce.py` — Newly enhanced CLI for compressing run logs (see §7).
- `logging/<timestamp>_agent_ws_opencode/` — Run artifacts, including raw provider payloads, tool results, prompts, and workspace copies.
- `docs/phase_2/*` — Design specs and previous handoff documents (V7–V9) outlining Schema V2 evolution.

## 3. Recent Work Since Handoff V9

1. **Log Reduction CLI** (`scripts/log_reduce.py`).
   - Added tree view, per-turn summaries, tool synopses, validation detection, and provider error surfacing.
   - Collapses diff content into `patch add/update/delete` lines by default; `--show-diffs` reveals full hunks.
   - Detects text-mode tool calls (e.g. `<TOOL_CALL>`, `<BASH>`, OpenCode patch blocks) and merges them with provider-native counts.
   - Summarises merge conflicts (`<<<<<<< SEARCH`) into simple “conflict <file>” lines.
   - Output examples: `scripts/logreduce_default_v5.log`, `scripts/logreduce_toolonly_v3.log`.

2. **Prompt Overhaul for Grok**
   - Added planning prompt (`implementations/system_prompts/opencode/plan_grok4.md`) that enforces a concise plan, identifies deliverables, and reminds the agent about tool hygiene.
   - Added builder prompt (`implementations/system_prompts/opencode/builder_grok4.md`) instructing the agent to avoid redundant `list_dir`, enforce one blocking tool per turn, and ensure Makefile targets (`all`, `test`, `demo`, `clean`) exist before running shell commands.
   - Updated `agent_configs/opencode_grok4fast_c_fs_v2.yaml` to enable plan mode and use the new prompts, ensuring runs begin with read-only context gathering.

3. **Configuration Tweaks**
   - Restricted diff dialects to OpenCode patch blocks only (no unified diff fallback) for Grok.
   - Added plan mode to the loop sequence while keeping build mode unchanged to execute the actual edits.

4. **Run Analysis**
   - Multiple Grok runs attempted (`scripts/grok4_test_run.log`, `_v2.log`, `_v3.log`). Each ended with provider errors (non-JSON payload from OpenRouter) before finishing the filesystem task. After the prompt update, the agent still failed once the provider returned HTML rather than JSON.
   - Run directories of interest:
     - `logging/20251001-023944_agent_ws_opencode` — Old prompts, heavy validation errors (multiple `run_shell` per turn, missing `clean` target).
     - `logging/20251001-035203_agent_ws_opencode` — New plan/builder prompts; run aborted immediately with `ProviderRuntimeError` before substantive tool usage.

## 4. Problems Addressed

- **Log analysis pain**: prior runs produced multi-MB transcripts. The new reducer compresses noise, highlights tool usage/validation errors, and surfaces provider faults.
- **Prompt hygiene**: Grok previously spammed `list_dir` and executed multiple blocking tools per turn. The updated prompts explicitly discourage redundant reads, require separate turns for bash, and insist on a Makefile `clean` target before running cleanup commands.
- **Schema alignment**: Plan mode is now activated for this config, aligning Grok with the standardized Schema V2 workflow (plan → build).

## 5. Known Issues & Current Direction

1. **Provider instability**: OpenRouter’s Grok endpoint intermittently returns HTML instead of JSON. Every recent run in `_v2` and `_v3` logs fails for this reason. Until resolved, the agent cannot progress beyond the first call. The next engineer should:
   - Verify credentials/quotas.
   - Consider switching to an alternative Grok endpoint or a local fallback model for testing.
   - Update provider adapters to sanitize HTML error pages (e.g., detect `<html>` and raise a more actionable error).

2. **Run behavior (once provider works)**: The latest successful partial run (before provider failure) indicates the agent still attempts `make clean` before the Makefile has a `clean` target. This should be addressed either via prompt guidance (already added) or by enforcing a pre-flight check: only allow `run_shell` once the Makefile has the requisite sections.

3. **Merge conflicts from SEARCH/REPLACE**: In earlier runs the assistant responded with raw conflict markers instead of legitimate patch blocks, leading to repeated validation errors. You’ll want to ensure the prompts (or plan) remind the model to use the OpenCode patch format exclusively.

4. **Validation rule: single bash per turn**: The agent frequently violated this rule prior to the prompt change. Keep monitoring to ensure the new prompts keep it in line; adjust `concurrency` `at_most_one_of` if necessary.

## 6. Tooling & Scripts

- **Log reducer** usage summary (see `docs/log_reduce_tool.md` for full manual). Key commands:
  ```bash
  python scripts/log_reduce.py logging/<run_dir> --tool-only --turn-limit 20
  python scripts/log_reduce.py logging/<run_dir> --skip-tree --show-diffs --include-roles assistant
  ```
- **Artifacts**:
  - `scripts/logreduce_default_v5.log` — The most recent default reducer output.
  - `scripts/logreduce_toolonly_v3.log` — Tool-only output highlighting command failures.
  - `scripts/logreduce_run_v2.log` — Pre-prompt run showing validation/builder issues.

## 7. Workspace State

`agent_ws_opencode/` currently contains artifacts from the latest attempt (protofilesystem files, tests, Makefile). Before running new tests, consider cleaning or snapshotting this workspace to avoid cross-run contamination.

## 8. What’s Next (Action Plan)

1. **Stabilize Provider Access**
   - Re-run Grok with new prompts once OpenRouter is healthy. If the HTML payload persists, swap to an alternative endpoint or use a local model (e.g., GPT-4-o-mini) to validate the workflow.
   - Update `provider_runtime.py` to detect and log HTML responses cleanly; maybe retry with exponential backoff.

2. **Validate Pipeline**
   - After provider response is restored, run the command:
     ```bash
     conda run -n ray_sce_test python main.py agent_configs/opencode_grok4fast_c_fs_v2.yaml \
       --schema-v2 --task implementations/test_tasks/universal_agentic_debugging_task.md \
       --max-iterations 20
     ```
   - Immediately inspect with the reducer (`--tool-only`) to confirm the plan/build prompts are honored and that the agent executes edit → read → bash sequences properly.
   - Ensure the Makefile now includes `clean`; if not, adjust prompts or add an automated guard (e.g., reject `run_shell` until the file contains `clean:`).

3. **Improve Diff Handling**
   - Optionally, extend `_summarize_conflict` to parse rename/move directives and short diff previews for new files.
   - Add a reducer flag to highlight repeated validation errors or command failures.

4. **Documentation**
   - We now have `docs/log_reduce_tool.md`; keep it updated if parameters change.
   - Consider summarizing the plan/builder prompt behavior in README or additional documentation for future prompt tweaking.

## 9. Outstanding Questions

- Should the Grok harness enforce stricter tool sequencing (e.g., block `run_shell` unless prior diff applies cleanly)? If so, modify the validator or concurrency policy.
- Do we need a fallback provider for offline testing (e.g., GPT-4o-mini) while Grok is unstable?
- Should the reducer automatically archive `error/*.json` entries along with turn summaries for easier data mining?

## 10. Quick Reference Commands

```bash
# Inspect latest run quickly
latest=$(ls -1 logging | sort | tail -n 1)
python scripts/log_reduce.py "logging/$latest" --tool-only --turn-limit 20

# Run Grok config (20 iterations max)
conda run -n ray_sce_test python main.py agent_configs/opencode_grok4fast_c_fs_v2.yaml \
  --schema-v2 --task implementations/test_tasks/universal_agentic_debugging_task.md \
  --max-iterations 20

# View log tree with full diffs for the last run
python scripts/log_reduce.py "logging/$latest" --show-diffs --skip-tree --turn-limit 5
```

## 11. Summary Checklist for the Next Engineer

- [ ] Confirm provider credentials / fix non-JSON payloads from OpenRouter.
- [ ] Re-run Grok config using the updated plan/build prompts.
- [ ] Verify Makefile contains robust `clean` target before `make clean` is invoked.
- [ ] Ensure single blocking tool per turn behavior is respected; adjust concurrency if not.
- [ ] Use log reducer outputs (`scripts/logreduce_*`) to monitor tool usage, validation errors, and provider faults.
- [ ] Snapshot successful runs for regression comparison once provider issues are resolved.

With the reducer enhancements and prompt updates in place, the remaining blockers are provider stability and final workflow tuning. Once the model responds correctly, the plan is to iterate on the build/test loop until the filesystem implementation passes its full test suite and the agent confidently calls `mark_task_complete()` with clean logs.
