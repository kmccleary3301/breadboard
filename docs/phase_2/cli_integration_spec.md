# KyleCode CLI Integration Spec — Full Agentic Frontend

This document defines the end-to-end plan to transform `kylecode_cli_skeleton/` into a production-ready CLI that mirrors modern agentic IDEs (Claude Code, Cursor, OpenCode, Crush). It covers goals, architecture, APIs, UX flows, and an incremental delivery roadmap aligned with the Ray-based agent in `agentic_coder_prototype/`.

---

## 1. Vision & Goals

**Objective**
- Provide a first-class CLI experience that can run, monitor, and resume agentic coding sessions locally or against a remote Ray cluster.
- Match the interaction patterns of existing IDE agents: model switching, file attachments, tool-call observability, plan/build loops, and session history.

**Key Use Cases**
1. **Quick Tasks** – `kyle ask "refactor foo.py"` streams a live session with real-time tool outputs.
2. **Interactive Shell** – `kyle repl` opens a TUI with multi-pane output, slash commands, and file editing.
3. **Batch Runs** – `kyle run --config opencode --task implementations/test_tasks/protofs_c.md` executes non-interactively and exposes artifacts/logs.
4. **Session Resume** – `kyle resume <session-id>` reloads sandbox state, transcripts, and log artifacts for continuation or review.
5. **File Ops** – Attach local files, preview diffs, run targeted commands/tests, and surface LSP diagnostics when enabled.

**Success Criteria**
- Functional parity with existing agentic UI features (Claude Code/Cursor) within terminal constraints.
- Seamless integration with Schema V2 configs, logging v2 artifacts, and concurrency/tool policies introduced in the current codebase.
- Extensibility for future panes (e.g., test dashboards, reward metrics, HPO telemetry).

---

## 2. Current State Assessment

| Component | File(s) | Status |
|-----------|---------|--------|
| CLI entrypoint | `kylecode_cli_skeleton/src/main.ts` | Minimal stub commands (`resume`, `config`, `ask`, `apply-diff`); no backend integration.
| CLI renderer | None | No TUI/panel support; plain stdout only.
| Session backend | N/A | CLI calls nothing; Python agent accessible only via `main.py` or ray actors.
| Resume metadata | `logging/` tree + `SessionState` snapshots | Rich artifacts but no index/exposure.
| Completion reporting | `agentic_coder_prototype/agent_llm_openai.py` | Now returns `completion_summary`, enabling CLI to display status.

Constraints: CLI must operate in Node/TypeScript while the agent runs under Python+Ray; a transport layer is required.

---

## 3. High-Level Architecture

```
+----------------------+           +------------------------------+
|  CLI (Node/Effect)   |  JSON/SSE |  Python Backend (FastAPI?)   |
|  - Command router    | <-------> |  - Session controller        |
|  - TUI renderer      |           |  - Agent proxy (Ray actor)   |
|  - Local cache/db    |           |  - Log/Artifact service      |
+----------------------+    ^      +------------------------------+
          |                  |
          v                  v
  Local workspace    Ray sandbox + logging_v2 artifacts
```

**Data Flow Summary**
1. CLI sends `start_session` with config overrides → backend spins up Ray actor (`OpenAIConductor`) and begins streaming events.
2. Backend emits server-sent events (SSE) or websockets: assistant messages, tool calls, transcript entries, log paths.
3. CLI renders streams in panes, persists a session record, and surfaces commands (`/model`, `/files`, `/attach`).
4. Resume requests query backend history (from logging directories or an indexed DB) and reattach to an existing actor if available.

**Transport Considerations**
- Option A: HTTP + SSE/Websocket using FastAPI/Uvicorn (Python) to stay close to Ray environment.
- Option B: gRPC/Websockets bridging Node ↔ Python (requires additional codegen but offers bi-directional streaming).
- CLI will use a typed client; adopt `Effect` streaming abstractions for SSE consumption.

---

## 4. Backend Extensions (Python)

| Capability | Description | Implementation Notes |
|------------|-------------|----------------------|
| Session registry | Map session IDs to Ray actors, workspaces, configs, status. | Extend `AgenticCoder` to create/reuse sessions; persist metadata (JSON/SQLite).|
| Multipart file API | Upload/attach file, apply diffs, fetch file tree. | Wrap `VirtualizedSandbox` (`kylecode/sandbox_virtualized.py`) operations behind HTTP endpoints.|
| Event streaming | Provide live turn events, tool call details, artifact links. | Subscribe to `SessionState` updates; stream JSON events to CLI.|
| Resume support | Rehydrate existing logging directory, optionally restart actor. | Use `completion_summary` and saved config (`logging/.../meta`) to rebuild context.|
| Command execution | Expose `run_shell`, `run_tests`, etc., as ad-hoc commands from CLI. | Reuse `OpenAIConductor.run_shell` wrappers with concurrency guards.|

Endpoints (illustrative):
- `POST /sessions` – start new session (`config`, `task`, `model_override`).
- `GET /sessions` – list sessions with summaries (`completion_summary`, last activity).
- `POST /sessions/{id}/input` – send follow-up user prompt / slash command.
- `POST /sessions/{id}/files` – upload file or diff patch.
- `GET /sessions/{id}/stream` – SSE/Websocket endpoint for live events.
- `DELETE /sessions/{id}` – stop session, clean workspace (optional).

---

## 5. CLI Frontend Requirements (Node)

### 5.1 Command Surface
- `kyle ask <prompt>` – single-task run with streaming output, optional flags (`--model`, `--profile`, `--config`).
- `kyle repl` – interactive TUI session; support slash commands (`/plan`, `/build`, `/attach`, `/files`, `/tests`).
- `kyle resume [session-id]` – list or resume previous sessions; default to most recent if omitted.
- `kyle sessions` – show table of past runs with status, timestamps, models, exit reasons.
- `kyle files [path]` – inspect workspace tree, open files, download artifacts.
- `kyle config` – manage profiles, default model, sandbox options.

### 5.2 TUI/UX Expectations
- Multi-pane layout (chat transcript, tool call log, file viewer, status bar).
- Real-time updates: highlight active tool, show exit reason (from `completion_summary`).
- Keyboard shortcuts: `j/k` to navigate turns, `t` to toggle tool log, `/` for command palette.
- Attachment workflow: `:attach ./foo.c` uploads file, shows diff preview, sends to agent.
- Model switching: command to switch provider/model mid-session (requires backend to swap config).

### 5.3 Local State & Resume
- Store session metadata under `~/.kyle/sessions.json` (or SQLite) for offline listing.
- Cache downloaded artifacts/diffs for quick re-open.
- Provide `--pull <session>` to sync logs from remote backend (if running on shared server).

### 5.4 Observability & Debugging
- Display concurrency plan (tools executed concurrently vs sequential) per turn.
- Show `completion_summary` details (`method`, `confidence`, `steps_taken`).
- Optional telemetry overlay once HPO/metrics pipeline (AUTO_OPTIM) is wired.

---

## 6. Integration Roadmap

| Phase | Focus | Deliverables |
|-------|-------|--------------|
| **Phase 0: Foundation** | Set up Python service skeleton + CLI HTTP client. | FastAPI app with `/health`; CLI config to choose endpoint; restructure CLI commands.|
| **Phase 1: Session Lifecycle** | Start/stop sessions, stream transcript. | `/sessions` POST/GET/stream; CLI `ask`, `sessions`, `resume` minimal views; store local session cache.|
| **Phase 2: Tool & File Ops** | Attach files, view tool logs, run commands. | File upload/download endpoints; CLI panes for tool outputs; `/files` command.|
| **Phase 3: Interactive Shell** | Full TUI with command palette and streaming updates. | Ink/OpenTUI renderer, slash-command routing, status bar.|
| **Phase 4: Advanced Controls** | Model switching, mode toggles, plan/build management. | Backend support for switching provider mid-run; CLI UI to select modes; hooking into Schema V2 features.|
| **Phase 5: Resume & History** | Deep resume, diff browsing, artifact download. | Persist icons (snapshots, prompts, `completion_summary`); CLI diff viewer; `kyle resume --inspect`.|
| **Phase 6: Polish & Parity** | LSP diagnostics, tests dashboard, metrics overlays. | Reactivate LSP event stream; scoreboard for failing tests; optional telemetry summary.|

Each phase includes automated tests (Node integration tests, Python endpoint tests) and documented user flows.

---

## 7. Implementation Notes & Dependencies

1. **Ray + HTTP Coexistence** – Ensure FastAPI app runs in same process as Ray initialization (consider `asyncio` event loop compatibility).
2. **Streaming Format** – Define canonical event schema (JSON lines/SSE fields) for uniform CLI rendering (e.g., `turn_start`, `tool_call`, `tool_result`, `completion_summary`).
3. **Authentication** – Optional for later; start with local connections but design headers/token support.
4. **Config Overrides** – CLI flags mutate base YAML: use Schema V2 loader to apply overrides (e.g., `providers.default_model`, `features.plan`).
5. **Testing** – Provide mock backend for CLI unit tests; add integration test that spins up FastAPI app + CLI command to ensure end-to-end success.
6. **Logging** – CLI should tail `logging_v2` artifacts (existing structure) and optionally download `conversation.md`, diff files, etc.
7. **Error Recovery** – Gracefully handle agent crashes or network disconnects; CLI prompts to retry/resume.

---

## 8. Open Questions

1. **UI Toolkit** – Confirm final renderer (Ink, Blessed, custom). Ink offers React-like architecture but may impact performance; Blessed provides lower-level control.
2. **Remote Deployment** – Should the backend run locally only, or support remote Ray clusters? If remote, authentication and artifact sync need design.
3. **LSP Re-Enablement** – LSP is currently disabled (`RAY_SCE_SKIP_LSP=1`); CLI should plan for future diagnostics streaming once reintroduced.
4. **Multi-session Concurrency** – Determine limit on active sessions; UI for switching among them.
5. **Security & Sandbox** – File attachments/executions may require policy gating; how to enforce safe commands in CLI.

---

## 9. References & Supporting Docs
- `kylecode_cli_skeleton/src/main.ts` – current CLI prototype to replace.
- `agentic_coder_prototype/agent.py` – entry point orchestrating Ray actor.
- `agentic_coder_prototype/agent_llm_openai.py` – main loop; now exposes completion summaries and needs new interfaces.
- `kylecode/sandbox_virtualized.py` – virtualized filesystem entrypoints for file ops.
- Specs: `AGENT_SCHEMA_V2.md`, `AGENT_SCHEMA_V2_PORT_PLAN.md`, `HANDOFF_V7.md` for context on Schema V2, logging, concurrency.

---

## 10. Summary

Upgrading the CLI from a stub to a full-featured agentic frontend requires:
- A dedicated backend façade around the Ray agent for session control and streaming.
- A rich Node CLI/TUI that leverages Effect-TS for commands, streaming, and local state.
- Phased development covering lifecycle management, tool/file handling, interactive UI, and parity polish.

This spec serves as the blueprint for planning, estimation, and implementation, aligning CLI evolution with the ongoing Schema V2/runtime work.
