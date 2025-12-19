CLI Phase 1 Integration Spec — KyleCode Agentic CLI
====================================================

## 0. Executive Summary
Phase 1 delivers the foundational infrastructure to turn the existing KyleCode engine into a Claude Code–style CLI experience. The backend façade (Python) will expose session lifecycle, streaming events, and file/tool operations via HTTP/SSE (or WebSocket). The Node/TypeScript client will consume those APIs, render streamed events in a terminal UI, and handle essential user commands. This phase targets parity with core Claude Code workflows—start a session, watch plan/build loops, run commands, resume history—without yet implementing advanced features (MCP, multi-subagent orchestrations, dashboards).

## 1. Goals & Non-Goals
### Goals
1. Provide a stable transport layer (REST + streaming) around the Ray-based conductor.  
2. Implement CLI commands that control sessions, stream output, and execute file/tool operations.  
3. Ensure logs/telemetry (conversation, diffs, reward metrics) accessible via CLI.  
4. Establish unit/integration testing scaffolding for backend & CLI.  
5. Document APIs, event schemas, and usage patterns for future phases.

### Non-Goals (Phase 1)
- Full Claude Code feature parity (MCP, slash command catalog, background bash, UI themes).  
- Persistent project memory/CLAUDE.md equivalent.  
- Remote deployment UX or auth beyond local tokens.  
- Advanced optimization/GEPA integration (not required for CLI functionality).  
- Multi-session dashboard or analytics UI.

## 2. Architecture Overview
### Components
1. **CLI Backend Service (Python/FastAPI)**  
   - Runs alongside Ray (`AgenticCoder`).  
   - Exposes REST endpoints & streaming endpoint (SSE/WebSocket).  
   - Manages session registry (SQLite/JSON).  
2. **CLI Client (TypeScript/Node)**  
   - Uses Effect/HTTP client to communicate with backend.  
   - Renders streaming events in TUI.  
   - Provides command router (`kyle ...`).  
3. **Logging & Storage**  
   - Reuse logging v2 artifacts (`logging/<run>`).  
   - Reward SQLite optional (for metrics).  
   - Session registry references logging locations for resume.

### Data Flow
```
CLI Command -> Backend POST /sessions -> Start Ray conductor -> Stream events -> CLI renders
```

## 3. Backend Specification
### 3.1 API Endpoints
| Method | Endpoint | Description |
| --- | --- | --- |
| POST | `/sessions` | Start new session (`config`, `task`, `overrides`, `metadata`). Returns session ID + initial metadata. |
| GET | `/sessions` | List sessions (status, start time, completion summary). |
| GET | `/sessions/{id}` | Fetch details (config, metrics, logs, reward summary). |
| POST | `/sessions/{id}/input` | Append user message or slash command. |
| POST | `/sessions/{id}/command` | Run tool/command (e.g., `run_shell`, `apply_diff`). |
| POST | `/sessions/{id}/files` | Upload/modify files (multipart or base64). |
| GET | `/sessions/{id}/files` | List tree/metadata (`path`, size, updated). |
| GET | `/sessions/{id}/download` | Fetch artifact (conversation, diff, telemetry). |
| POST | `/sessions/{id}/resume` | Resume a stopped session (rebuild state). |
| DELETE | `/sessions/{id}` | Terminate session and cleanup. |
| GET | `/sessions/{id}/events` | Streaming endpoint (SSE/WebSocket). |
| GET | `/health` | Health check (Ray + backend). |

### 3.2 Streaming Event Schema
Each event (JSON object) includes:
- `type`: `"turn_start"`, `"assistant_message"`, `"tool_call"`, `"tool_result"`, `"user_message"`, `"completion"`, `"reward_update"`, `"log_link"`, `"error"`.  
- `turn`: sequential turn number.  
- `timestamp`: ISO string.  
- `payload`: type-specific data (message content, tool details, paths, metrics).  
- `session_id`: for correlation.  

Streaming transport toggles:
- Local-mode sessions emit events by default.
- Ray remote sessions are guarded by `KYLECODE_ENABLE_REMOTE_STREAM=1` or the per-request metadata flag `{"enable_remote_stream": true}` to preserve current stability while the feature dogfoods.

Example `assistant_message` event:
```json
{
  "type": "assistant_message",
  "turn": 3,
  "timestamp": "2025-10-15T23:12:04.567Z",
  "payload": {
    "text": "I'll start by listing the workspace and reading foo.py.",
    "model": "openrouter/z-ai/glm-4.6"
  },
  "session_id": "sess-123"
}
```

### 3.3 Session Registry
- Store in SQLite (recommended) or JSON index.  
- Schema: `session_id`, `created_at`, `status`, `config_path`, `overrides`, `workspace_path`, `logging_dir`, `last_activity_at`, `completion_summary`, `reward_summary`.  
- Provide convenience functions: `create_session`, `update_status`, `list_sessions`, `get_session`, `mark_completed`.

### 3.4 Sandbox/File Operations
- Wrap `sandbox_virtualized` functions with safe endpoints.  
- Support operations: `read_file`, `write_file`, `list_dir`, `apply_patch`, `grep`, `glob`.  
- Enforce concurrency rules (re-use `AgentToolExecutor` policies).  
- Provide file diff preview via logging artifacts or on-demand.

### 3.5 Command Execution
- `run_shell` with optional background flag (Phase 1: synchronous).  
- `run_tests` convenience wrapper (calls `run_shell` with expanded command).  
- Return structured output: `stdout`, `stderr`, `exit_code`, `duration`.

### 3.6 Completion & Rewards Exposure
- Include `completion_summary` in session metadata.  
- Provide `reward_summary` (from `SessionState.reward_metrics_payload`).  
- Surface provider metrics (calls, latency, PAS/HMR) in session response.

### 3.7 Error Handling & Auth
- For Phase 1, local-only; respond with HTTP 4xx/5xx and descriptive error JSON.  
- Hook for future auth (API key env var).  
- Gracefully handle conductor failures (send `error` event, mark session aborted).

## 4. CLI Client Specification
### 4.1 Command Overview
| Command | Description |
| --- | --- |
| `kyle ask "<prompt>" [flags]` | Start session with initial user message; stream results. |
| `kyle repl [flags]` | Interactive TUI session; slash commands enabled. |
| `kyle resume <session-id>` | Reattach to existing session; stream latest state. |
| `kyle sessions` | List known sessions with status. |
| `kyle files <session-id> [subcommand]` | `ls`, `cat`, `apply-diff`, `download`. |
| `kyle run --config ... --task ... [flags]` | Non-interactive run (batch). |
| `kyle artifacts <session-id>` | Fetch logs/telemetry to local path. |
| `kyle status <session-id>` | Show completion summary & rewards. |

Flags include: `--config`, `--task`, `--model`, `--overrides`, `--workspace`, `--permission-mode`, `--output json/text`, `--watch`.

### 4.2 TUI Rendering
- Use Ink or Blessed for multi-pane layout (top: conversation, bottom: input, sidebar: stats).  
- Real-time updates from streaming events.  
- Slash commands triggered via `/command` input; client maps to backend functions.  
- Provide status line (model, permission mode, session status).

### 4.3 Session Cache
- Local file (e.g., `~/.kyle/sessions.json`) storing session metadata (ID, title, timestamps).  
- CLI uses cache to display list, enable quick resume.  
- Sync with backend registry on startup (`GET /sessions`).

### 4.4 Error & Reconnection
- Handle network disconnect by retrying once; display notice.  
- On backend error event, prompt user to resume or exit.  
- `kyle resume` replays recent events to rebuild context.

### 4.5 Slash Command Mapping (Phase 1)
- `/plan` → send input with plan mode toggle (maybe `{"action":"set_mode","mode":"plan"}`).  
- `/model <id>` → call backend to switch model (if provider supports).  
- `/test` → run predefined test command.  
- `/files` → list updated files (calls file API).  
- For other commands, show not-implemented hint (preparing for future phases).

## 5. Logging & Telemetry
- Backend logs structured events (JSON) with session ID.  
- CLI optionally logs streaming events for debugging (`--verbose`).  
- Hook to aggregate reward metrics (expose new CLI command `kyle rewards <session-id>`).  
- Monitor backend health (access logs, error metrics) for ops readiness.

## 6. Testing Plan
- **Backend Unit Tests**: FastAPI tests with mocked `AgenticCoder`, verifying endpoints & SSE output.  
- **Backend Integration**: Spin up Ray local_mode + backend; start session; assert streaming sequence.  
- **CLI Unit**: Test command parsing, HTTP client, slash command mapping.  
- **CLI Integration**: Use mocked backend server (playback events) to ensure TUI updates.  
- **End-to-End Smoke**: combined Python backend + Node CLI run via CI script.

## 7. Deployment & Tooling
- Local dev script: `scripts/run_cli_backend.py` launching FastAPI + Ray (local).  
- Node CLI `npm start` or packaged binary.  
- For CI: docker-compose or `tox` environment running Python + Node tests.  
- Optional future remote deployment (Kubernetes) not in scope Phase 1.

## 8. Risks & Mitigations
| Risk | Mitigation |
| --- | --- |
| FastAPI and Ray event loop conflicts | Use `asyncio.run` carefully; ensure Ray runs in background thread or use `sync` endpoints with threadpool. |
| Long-running sessions & memory | Enforce session cleanup, limit active sessions, expose `DELETE /sessions`. |
| Streaming reliability | Implement heartbeat/ping; reconnect logic in CLI. |
| Sandbox safety | In Phase 1, rely on existing policies; before remote deployment, add auth & command gating. |
| Node TUI complexity | Start with minimal panes; fallback to plain stdout for early releases if needed. |

## 9. Roadmap Beyond Phase 1 (Foreshadow)
- Phase 2: add advanced UX (CLAUDE.md memory, background bash, slash command catalog, MCP integration).  
- Phase 3: remote deployment, authentication, multi-session dashboards, analytics.  
- Tie into optimization stack (display GEPA/HPO metrics, allow prompt experimentation from CLI).

## 10. References
- `docs/phase_2/cli_integration_spec.md` – original high-level CLI architecture.  
- `docs/phase_5/PHASE5_PLANNING_MODEL_RESPONSE.md` – overall Phase 5 roadmap (CLI bridging is Milestone M4).  
- `docs/claude_code_comprehensive_functionality_spec_v_0.md` – feature inspiration from Claude Code.  
- Implementation references: `agentic_coder_prototype/agent_llm_openai.py`, `logging_v2`, `sandbox_virtualized.py`, `SessionState`, reward metrics pipeline.
