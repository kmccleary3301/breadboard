## Handoff V7

### TL;DR
- V2 schema port is largely done (0,1,2,3,4,5,7 complete). Concurrency (6), provider-native routing (9), and telemetry/HPO (10) are partially done. Subagents (8) not started.
- TPSL (Jinja2 tool prompts) is live; fallback removed; per-turn vs system duplication fixed.
- Logging V2 is live; each run has a structured folder with artifacts and an accurate `conversation.md`.
- Sandbox is virtualized with mirroring; cwd issues for `ls`/`bash` fixed; mirror path duplication fixed.
- Concurrency: any blocking tool forces sequential execution for the turn; nonblocking can execute concurrently.

### Environment & Quick Start
- Use conda env `ray_sce_test` [[memory:5503097]].
- Secrets are loaded from the repo `.env` by `main.py`.

Run a short agent session:
<BASH>
conda run -n ray_sce_test bash -lc 'export RAY_SCE_SKIP_LSP=1; python main.py --schema-v2 agent_configs/opencode_gpt5_nano_c_fs_v2.yaml --task implementations/test_tasks/protofs_c.md -m 2 | cat'
</BASH>

Run focused tests (skip LSP-heavy):
<BASH>
conda run -n ray_sce_test bash -lc 'export RAY_SCE_SKIP_LSP=1; pytest tests/tpsl -vv -s | cat'
</BASH>

Run broader tests (still skipping LSP):
<BASH>
conda run -n ray_sce_test bash -lc 'export RAY_SCE_SKIP_LSP=1; pytest -vv -s | cat'
</BASH>

### What’s Implemented
- Schema V2
  - Loader with `extends` and validation; normalized paths.
  - CLI flag `--schema-v2` wired in `main.py`.

- Tool Prompt Synthesis Layer (TPSL)
  - All tool availability/dialect text via Jinja2 templates. No hardcoded prompts remain.
  - Config-driven selection under `prompts.tool_prompt_synthesis` with `dialects`, `selection` (`by_model`, `by_mode`), and `detail`.
  - Fallback rendering removed; missing templates error at compile time (or produce empty per-turn if specifically missing that family).

- Logging V2
  - Per-run directory with: raw API request/response JSON, compiled prompts, provider-native schemas/calls/results, per-turn tool results, and a readable `conversation.md` with references.
  - Assistant messages, per-turn tool groups, and provider-native tool sections appear with clear headers.

- Dialects & Selection
  - `by_model` and `by_tool_kind` support. Diff formats prioritized when present.

- Concurrency & Execution
  - Turn strategy: if any blocking tool is present, run parsed calls sequentially; only if all are nonblocking, run concurrently.
  - Validator rules (e.g., one bash per turn) enforced.

- Sandbox & Mirroring
  - `VirtualizedSandbox` over `DevSandboxV2`; `SandboxFactory` creates isolated workspaces with optional mirroring.
  - `run_shell` executes with `cwd=self.workspace`; `ls` and other ops are workspace-consistent.
  - Mirroring normalizes paths to avoid duplicate segments (e.g., `agent_ws/.../agent_ws/...`).

### Key Files & Directories
- Agent runtime:
  - `agentic_coder_prototype/agent_llm_openai.py` (loop, tool routing, concurrency gating, logging wiring)
  - `agentic_coder_prototype/execution/agent_executor.py` (execution strategy)
  - `agentic_coder_prototype/execution/enhanced_executor.py` (policies: `create_file_policy`, path normalization)
  - `agentic_coder_prototype/execution/dialect_manager.py`

- Prompt compilation:
  - `agentic_coder_prototype/compilation/system_prompt_compiler.py` (packs, injection, TPSL orchestration, dedupe)
  - `agentic_coder_prototype/compilation/tool_prompt_synth.py` (Jinja2 renderer, family-specific fallback guards)

- Sandbox:
  - `kylecode/sandbox_v2.py` (base sandbox; `cwd=self.workspace`)
  - `kylecode/sandbox_virtualized.py` (virtualization, mirroring, `SandboxFactory`)

- Configs & Templates:
  - `agent_configs/base_v2.yaml` (baseline), `agent_configs/opencode_gpt5_nano_c_fs_v2.yaml` (current scenario)
  - Templates under `implementations/tool_prompt_synthesis/**`

- Logging:
  - `agentic_coder_prototype/logging_v2/*` (run directory, transcript writer)

### Recent Fixes You Should Know
- TPSL duplication between system and per-turn prompts:
  - System TPSL chunk now added to a dedupe set; per-turn will not re-include identical content.
  - Removed cross-family fallback in TPSL renderer; per-turn won’t fall back to a system template.

- Sandbox cwd and mirroring:
  - Ensured `ls` and `run_shell` operate in the same workspace root.
  - Fixed `VirtualizedSandbox._mirror_directory_structure` to handle dict-shaped listings.
  - Normalized mirror paths to prevent recursive `agent_ws/.../agent_ws/...` creation.

- Concurrency:
  - If any blocking tool is in a turn, we execute all tool calls sequentially; concurrent execution only when all are nonblocking.

- Logging transcript:
  - Grouped text-based tool results per turn; assistant messages reliably recorded.

### How to Configure Prompts & TPSL
- In `agent_configs/opencode_gpt5_nano_c_fs_v2.yaml`:
  - `prompts.packs.base.system`: main system prompt markdown.
  - `prompts.tool_prompt_synthesis`: select dialects and details. Example uses `unified_diff.system_full` for system and `pythonic.per_turn_short` for per-turn.
  - No per-turn fallback: ensure you provide a `per_turn_*` template if you need per-turn tool listings.

### How to Reproduce & Inspect
- Short run with logging:
<BASH>
conda run -n ray_sce_test bash -lc 'export RAY_SCE_SKIP_LSP=1; python main.py --schema-v2 agent_configs/opencode_gpt5_nano_c_fs_v2.yaml --task implementations/test_tasks/protofs_c.md -m 2 | cat'
</BASH>
  - Open the newest `logging/<timestamp>_agent_ws_*/conversation/conversation.md` and verify:
    - Unified diff guidance appears only in System.
    - Per-turn shows short tool availability (pythonic).
    - Tool results grouped per turn, with artifact links.

### Pending / Next Steps (Recommended)
- Concurrency Guards (Milestone 6)
  - Expand and test `concurrency.groups` with `max_parallel`, `barrier_after`, and `at_most_one_of` across scenarios.
  - Add unit tests for blocking vs nonblocking mixes; include YAML `max_per_turn` enforcement.

- Provider-Native Tools (Milestone 9)
  - Verify OpenAI-native tool call segregation and logging with IDs.
  - Ensure transcript shows “Model Called Provider Schema‑Native Tools” and corresponding results sections.

- Telemetry + HPO Hooks (Milestone 10)
  - Add minimal event hooks around prompt compile, API calls, and tool execution for offline analysis.

- Subagents & Isolation (Milestone 8)
  - Not started. Define subagent schema, isolated workspaces, and orchestration.

- LSP Integration
  - Currently skipped via `RAY_SCE_SKIP_LSP=1` to keep runs fast. Re-enable incrementally and split slow tests.

- SandboxFactory Unification
  - Ensure all agent code paths consistently use `SandboxFactory` creation (already in use when mirroring is enabled). Validate production mode volume isolation with `runsc`.

### Gotchas
- Jinja2 is required for TPSL; missing templates will raise at compile time (per-turn sections will be empty rather than falling back to system).
- Ensure `.env` exists with API keys; `main.py` loads it automatically.
- Docker/gVisor: local fallback is used if `RAY_USE_DOCKER_SANDBOX` is not set to `1`.

### Useful Commands
- Full test run (no `-q`; show active test):
<BASH>
conda run -n ray_sce_test bash -lc 'export RAY_SCE_SKIP_LSP=1; pytest -vv -s | cat'
</BASH>

- Specific suites:
<BASH>
conda run -n ray_sce_test bash -lc 'export RAY_SCE_SKIP_LSP=1; pytest tests/test_dialect_selection_v2.py -vv -s | cat'
</BASH>

### Contact Points in Code
- Prompt & TPSL: `system_prompt_compiler.py`, `tool_prompt_synth.py`, templates under `implementations/tool_prompt_synthesis/`.
- Execution & Concurrency: `agent_executor.py`, `enhanced_executor.py`, `sequence_validator.py`.
- Sandbox: `sandbox_v2.py`, `sandbox_virtualized.py`, `SandboxFactory`.
- Logging: `logging_v2/*`, transcript in per-run `conversation/`.

### Final Notes
- Commands in this doc are provided using <BASH> tags to match project conventions [[memory:5629107]].
- Stick to small, surgical edits and keep logs clean—most debugging can be done by reading the latest run folder.


