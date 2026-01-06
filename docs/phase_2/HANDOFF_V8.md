## Handoff V8

### Sprint Snapshot
- **Scope:** Polish Schema V2 rollout (dialect preference clarity, provider-native flow) and stabilize local CLI runs inside the sandboxed environment.
- **Runtime Mode:** Tests executed where Ray sockets are blocked. The conductor now auto-falls back to an in-process runtime when Ray cannot initialize.

### What Landed
- **Schema V2 Config Clarity**
  - Added `tools.dialects.preference` (spec + `base_v2.yaml` + OpenCode profile) to expose native → text fallback order.
  - Runtime merges the new preference map with legacy selection and surfaces the native-hint used for prompt tuning (`agent_llm_openai.py`).
  - Tests (`tests/test_dialect_selection_v2.py`) exercise ordering + native hints without Ray actors.
- **Local (No-Ray) Fallback**
  - `AgenticCoder` + `OpenAIConductor` detect failed `ray.init()` (or `RAY_SCE_LOCAL_MODE=1`) and spin up the conductor/DevSandbox in-process via a lightweight proxy.
  - `main.py --schema-v2 …` now succeeds in the sandbox; workspace artifacts land in `agent_ws_opencode/`.
- **Profile Fixes**
  - Cleaned `agent_configs/opencode_gpt5_nano_c_fs_v2.yaml` (YAML typo fixed, sandbox driver=process, mirroring disabled) to avoid recursive mirrors during tests.

### Outstanding / Next Focus
- **Workspace Lifecycle (New Request)**
  - Need to wipe `agent_ws_opencode/` at run start and copy the final workspace into `logging/<timestamp...>/final_container_dir/` (uncompressed) to inspect results. Not implemented yet.
- **Directory Recursion Investigation**
  - Prior long run produced recursive paths inside `agent_ws_opencode`. Once workspace cleanup & copy exist, re-run the task to inspect the final tree and determine whether prompts/tool hints introduced nested mirrors.
- **Ray vs Local Parity**
  - Local fallback keeps APIs stable, but virtualization/LSP features still depend on Ray. Verify Ray-mode behavior once sockets are allowed again.

### Attempts & Notes
- Tried Ray `local_mode=True`; seccomp still blocked sockets. Solution: direct class instantiation in tests + in-process sandbox for runtime.
- Verified `pytest tests/test_dialect_selection_v2.py tests/test_provider_native_routing.py -q`.
- CLI run now prints `[Ray disabled] Using local in-process execution mode.` when fallback engages.

### Recommended Next Steps
1. Implement workspace cleanup + post-run copy into logging (requested above).
2. Re-run the OpenCode CLI test, inspect `final_container_dir/` to debug recursion.
3. Re-test with real Ray once available to confirm virtualization + logging parity.
4. Harden path normalization (`_virtualize_path`, prompts) to strip mirror/workspace prefixes proactively.

### Quick Reference
- Test command (works in current sandbox):
  ```bash
  python main.py --schema-v2 agent_configs/opencode_gpt5_nano_c_fs_v2.yaml \
    --task implementations/test_tasks/protofs_c.md -m 10
  ```
- Force local fallback: `export RAY_SCE_LOCAL_MODE=1` before running.
- Logs live under `logging/<timestamp>_*`; prompts/conversation captured there.
