# Parity kernel boundaries

This document lists the parity-critical modules and surfaces that must remain byte-stable for E4 parity. Any changes to these areas require an explicit parity pack bump and regenerated goldens.

---

## Parity-critical surfaces

### 1. SSE event envelope and ordering

- Monotonic `seq` assignment and event ordering
- Event serialization (field inclusion and ordering)

### 2. CLI bridge API contract

- OpenAPI endpoints, request/response payloads
- SSE streaming endpoint (`/sessions/{id}/events`)

### 3. Tool schema and tool I/O surfaces

- Tool definitions: names, args, ordering, descriptions
- Tool execution results: success/failure formatting, errors, truncation

### 4. Permission prompts and guardrail phrasing

- Decision flow, wording, and message formatting

### 5. Provider request shaping

- System prompt wrappers, tool choice defaults, and any model-visible payloads

### 6. Replay/parity normalizers

- Normalization rules that affect byte-level equivalence checks

---

## Concrete module boundaries

These files and modules are parity-critical and should be treated as the parity kernel.

**SSE ordering and envelope:**

- `agentic_coder_prototype/state/session_state.py` — seq assignment
- `agentic_coder_prototype/api/cli_bridge/events.py` — event envelope schema
- `agentic_coder_prototype/api/cli_bridge/service.py` — event stream and seq enforcement
- `agentic_coder_prototype/api/cli_bridge/app.py` — StreamingResponse formatting

**Tool surfaces:**

- `implementations/tools/defs/**` — tool schema definitions
- `agentic_coder_prototype/tools.py` — tool registry loading and schema hashing
- `agentic_coder_prototype/tool_calling/**` — tool call serialization and coercion
- `agentic_coder_prototype/tool_prompt_planner.py` — tool prompt constraints

**Permission and guardrail prompts:**

- `agentic_coder_prototype/guardrail_orchestrator.py`
- `agentic_coder_prototype/permission_broker.py`
- `agentic_coder_prototype/guardrails/**`

**Provider request shaping:**

- `agentic_coder_prototype/provider_runtime.py`
- `agentic_coder_prototype/provider_ir.py`
- `agentic_coder_prototype/agent_llm_openai.py`
- `agentic_coder_prototype/compilation/system_prompt_compiler.py`

**Parity and replay harness:**

- `agentic_coder_prototype/parity.py`
- `scripts/run_parity_replays.py`
- `scripts/replay_opencode_session.py`

---

## Change discipline

Before touching any parity-kernel module:

1. Bump the parity pack version, or document an explicit override with rationale.
2. Regenerate goldens.
3. Pass the CI parity gate.

---

## E4 target freeze

E4 target harness snapshots are pinned in:

- `config/e4_target_freeze_manifest.yaml`

Validation:

```bash
python scripts/check_e4_target_freeze_manifest.py --json
make e4-target-manifest
```

Policy: [conformance/E4_TARGET_VERSIONING.md](conformance/E4_TARGET_VERSIONING.md)

---

Extend this list as new parity-critical surfaces are identified. File a note in the evidence ledger ([CLAIMS_EVIDENCE_LEDGER.md](CLAIMS_EVIDENCE_LEDGER.md)) when boundaries change.
