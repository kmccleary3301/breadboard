# Parity Kernel Boundaries (Draft)

This document enumerates the **parity‑critical modules and surfaces** that must remain byte‑stable for E4 parity. Any changes to these areas should require an explicit parity pack bump and regenerated goldens.

## Parity‑Critical Surfaces
These surfaces must remain byte‑stable in strict parity mode:

1) **SSE event envelope + ordering**
   - Monotonic `seq` assignment and event ordering.
   - Event serialization (field inclusion and ordering).

2) **CLI Bridge API contract**
   - OpenAPI endpoints, request/response payloads.
   - SSE streaming endpoint (`/sessions/{id}/events`).

3) **Tool schema & tool I/O surfaces**
   - Tool definitions (names, args, ordering, descriptions).
   - Tool execution results (success/failure formatting, errors, truncation).

4) **Permission prompts & guardrail phrasing**
   - Decision flow, wording, and message formatting.

5) **Provider request shaping**
   - System prompt wrappers, tool choice defaults, and any model‑visible payloads.

6) **Replay/parity normalizers**
   - Normalization rules that affect byte‑level equivalence checks.

## Concrete Module Boundaries (Current Repo)
These files/modules are parity‑critical and should be treated as the parity kernel:

### SSE ordering + envelope
- `agentic_coder_prototype/state/session_state.py` (seq assignment)
- `agentic_coder_prototype/api/cli_bridge/events.py` (event envelope schema)
- `agentic_coder_prototype/api/cli_bridge/service.py` (event stream + seq enforcement)
- `agentic_coder_prototype/api/cli_bridge/app.py` (StreamingResponse formatting)

### Tool surfaces
- `implementations/tools/defs/**` (tool schema definitions)
- `agentic_coder_prototype/tools.py` (tool registry loading / schema hashing)
- `agentic_coder_prototype/tool_calling/**` (tool call serialization + coercion)
- `agentic_coder_prototype/tool_prompt_planner.py` (tool prompt constraints)

### Permission/guardrail prompts
- `agentic_coder_prototype/guardrail_orchestrator.py`
- `agentic_coder_prototype/permission_broker.py`
- `agentic_coder_prototype/guardrails/**`

### Provider request shaping
- `agentic_coder_prototype/provider_runtime.py`
- `agentic_coder_prototype/provider_ir.py`
- `agentic_coder_prototype/agent_llm_openai.py` (provider call shaping)
- `agentic_coder_prototype/compilation/system_prompt_compiler.py`

### Parity & replay harness
- `agentic_coder_prototype/parity.py`
- `scripts/run_parity_replays.py`
- `scripts/replay_opencode_session.py`

## Change Discipline
- Changes to any parity‑kernel module require:
  1) Explicit parity pack bump (or documented override)
  2) Regenerated goldens
  3) CI parity gate pass

## Extension Safety
- Extensions/hooks must remain **disabled by default** in parity runs.
- Enabling extensions in parity mode requires an explicit parity pack bump and golden regeneration.

## Status
Draft — extend this list as new parity‑critical surfaces are identified.
