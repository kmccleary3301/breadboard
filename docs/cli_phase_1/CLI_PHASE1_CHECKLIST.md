CLI Phase 1 Checklist — KyleCode CLI Integration
================================================

> Objective: Deliver a functional CLI backend façade + Node client that mirrors core Claude Code workflows (session start/stream/resume, file ops, tool commands) on top of the existing KyleCode agent engine.

Status Legend: `[ ]` not started · `[~]` in progress · `[x]` complete

1. Planning & Foundations
-------------------------
- [ ] Confirm transport stack (FastAPI + SSE/WebSocket vs gRPC) and document event schema.  
- [ ] Define session metadata schema (SQLite/JSON) for history/resume.  
- [ ] Align CLI UX expectations with product stakeholders (minimum parity vs advanced features).  

2. Backend Façade (Python)
--------------------------
- [x] Implement session registry (`SessionRegistry` class + SQLite/JSON).  
- [x] Build `/sessions` REST APIs (create/list/get/resume/delete).  
- [x] Implement streaming endpoint (SSE/WebSocket) emitting turn events, tool calls, artifacts, rewards.  
- [ ] Expose file operations (read/write/edit/list/grep/glob) via HTTP endpoints.  
- [ ] Add command execution endpoints (run_shell, run_tests).  
- [ ] Wire completion summaries, reward metrics, provider stats into session responses.  
- [ ] Add resume logic that rehydrates existing runs (logs/metadata).  
- [ ] Provide authentication hook (optional placeholder for later phases).  

3. CLI Client (Node/TypeScript)
-------------------------------
- [ ] Replace stub commands (`ask`, `resume`, etc.) with API calls to backend.  
- [ ] Implement streaming consumer (SSE/WebSocket) with multi-pane TUI output.  
- [ ] Support config overrides (`--model`, `--overrides`) passed through to backend.  
- [ ] Implement file commands (`kyle ls`, `kyle cat`, `kyle apply-diff`).  
- [ ] Handle slash command UX (`/plan`, `/test`, `/model`, etc.) mapped to backend actions.  
- [ ] Persist local session cache (IDs, metadata) for quick resume.  
- [ ] Implement permission mode toggles and status display (minimal version).  
- [ ] Add error handling and reconnection flows for dropped sessions.  

4. Logging & Artifacts
----------------------
- [ ] Expose logging v2 artifacts through API (download conversation, diff files, telemetry).  
- [ ] CLI command to fetch/save run artifacts locally.  
- [ ] Ensure reward metrics and provider metrics accessible via CLI (summary command).  

5. Testing & QA
---------------
- [x] Unit tests for backend endpoints (FastAPI tests, mocked Ray sessions).  
- [ ] Integration test: start session → stream events → issue file commands → resume.  
- [ ] CLI integration tests using mocked backend (simulate streaming).  
- [ ] Manual smoke tests against live engine (local Ray, sample tasks).  
- [ ] Regression tests ensuring CLI flows don’t regress completion heuristics (reuse existing mock runtime).  

6. Documentation & Ops
----------------------
- [ ] Update `docs/cli_phase_1/HANDOFF_V1.md` as implementation evolves.  
- [ ] Provide quick-start guide for running backend + CLI locally (docker-compose or scripts).  
- [ ] Document API schema, event types, and CLI command reference.  
- [ ] Define monitoring/logging strategy for backend service (basic metrics).  

Milestone Exit Criteria
-----------------------
- CLI can start sessions, stream live events, run file/tool commands, and resume past sessions.  
- Backend exposes documented API with end-to-end tests passing.  
- Artifacts (logs, metrics) accessible via CLI.  
- Internal docs/handoff updated; ready for broader dogfooding.  
