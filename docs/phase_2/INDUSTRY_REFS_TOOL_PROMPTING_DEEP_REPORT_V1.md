### Industry References – Tool Prompting Deep Report

#### Table of Contents
- Overview and Objectives
- Source Corpus and Method
- Cross-System Patterns in Tool Availability Prompting
- System-Specific Findings and Excerpts
  - Crush (Charm/Crush)
  - OpenCode (SST)
  - Cursor (System Prompt Snapshot)
  - Claude Code (reverse notes)
- Comparative Matrix: Dialects, Delimiters, and Call Semantics
- Design Implications for Our TPSL Jinja Templates
- Migration Notes and Edge Cases
- Appendix: Raw Excerpts

### Overview and Objectives
This report surveys concrete “tool availability prompting” patterns in popular agentic coding systems and distills them into actionable specifications for our Tool‑Prompt Synthesis Layer (TPSL). The goal is to faithfully reproduce external behaviors (syntax, delimiters, tone, safety constraints) via swappable Jinja2 templates, enabling exact behavioral clones or hybrids.

### Source Corpus and Method
- Repositories and docs consolidated under `industry_coder_refs/`:
  - Crush: internal Go prompts and tool schemas
  - OpenCode: docs and SDK deep dives, `AGENTS.md`
  - Cursor: system prompt snapshot (`cursor_system_prompt.md`)
  - Claude Code: reverse-engineered notes (if present in folder)
- Extracted prompt excerpts and tool schemas; identified delimiter conventions, format blocks, parallelization guidance, and safety constraints.

### Cross-System Patterns in Tool Availability Prompting
- Tool catalog placement and persistence:
  - Crush: system-level instructions embed environment and LSP diagnostics policy; tool usage policy appears in system prompt, not per‑turn.
  - OpenCode: tool list implicitly known; dialects inferred via code blocks (`bash`, unified diff). Mode toggles (Plan vs Build) gate availability rather than rendering long catalogs each turn.
  - Cursor: provides meta‑rules and explicit tool usage directives; emphasizes “don’t mention tools” to user but still dictates tool policy.
- Delimiters and dialects:
  - Explicit code fences signal execution: `bash` blocks; `patch`/unified diff fences; SEARCH/REPLACE blocks (Aider style in other systems).
  - XML‑like wrappers are rare in these references; YAML/Markdown predominates.
- Concurrency and sequencing:
  - Crush: “All tools executed in parallel when multiple tool calls are sent” + explicit caution to only parallelize when independent.
  - OpenCode: streaming loop interleaves tool runs with reasoning; parser expects delimiters/tokens in TS server.
- Safety and permissions:
  - OpenCode: permission prompts and scope‑based allowlists; Plan mode disables mutating tools.
  - Crush: token‑minimization, strict brevity, and LSP diagnostics sections; no commit unless asked.

### System‑Specific Findings and Excerpts

#### Crush (Charm/Crush)
- System prompt variants per provider with embedded files:
```go
//go:embed anthropic.md
var anthropicCoderPrompt []byte
//go:embed v2.md
var coderV2Prompt []byte
```
- Environment and LSP sections appended to system:
```go
basePrompt = fmt.Sprintf("%s\n\n%s\n%s", basePrompt, envInfo, lspInformation())
```
- LSP diagnostics guidance (anthropic.md/v2.md):
```
# LSP Information
Tools that support it will also include useful diagnostics...
- You should ignore diagnostics of files you did not change...
```
- Tool usage policy (anthropic.md/v2.md):
```
- IMPORTANT: All tools are executed in parallel when multiple tool calls are sent...
- The user does not see the full output of the tool responses; summarize if needed.
```
- Style/tone constraints: extremely concise, <4 lines outside tool use; never emojis; do not add comments unless asked.
- Context injection: project file tree and “Project‑Specific Context” via file ingestion.

Implications for TPSL:
- Provide Jinja blocks to optionally inject LSP policy and env snapshot sections.
- Include parallelization policy and brevity constraints knobs.
- Offer provider‑tuned variants (anthropic/openai/gemini) via template selection.

#### OpenCode (SST)
- Tools and dialects inferred by blocks rather than a rendered catalog every turn:
  - `bash` code block for shell; unified diff fenced blocks for patches; structured write/edit.
- Modes gate availability:
  - Plan (read‑only) vs Build (mutating tools enabled).
- Event streaming and tool interleaving; parser expects specific tool‑call delimiters internally (e.g., `<|tool_calls_section_end|>` noted in analysis), fragile across models.
- `AGENTS.md` acts as a persistent, human‑editable meta‑prompt for style and rules.

Implications for TPSL:
- Provide per‑mode catalogs: short availability banner for Plan vs full catalog for Build.
- Dialect templates should emit exact fenced formats for unified diff and optional Aider SEARCH/REPLACE.
- Optional “permissions banner” and “plan/build toggles” blocks.

#### Cursor (System Prompt Snapshot)
- Strong meta‑rules: “don’t mention tool names; say what you’re doing,” maximize context understanding, tool discovery mandates, strong code‑change discipline.
- Tool layer abstracted; prompt emphasizes acting immediately on plans, not asking confirmation unless blocked.

Implications for TPSL:
- Include a “provider‑adapter overlay” section to restate house rules (e.g., don’t name tools) while still rendering catalogs for the model.
- Add optional “maximize context understanding” policy block.

#### Claude Code (reverse notes)
- Similar guardrails: brevity, proactive execution, IDE‑style diagnostics; XML blocks for tool_use are Anthropic‑native in some flows, but most code agents surface Markdown.

Implications for TPSL:
- Support an Anthropic‑flavored template that renders `tool_use` schema hints or brief catalog bullets that align with Claude’s tool_use.

### Comparative Matrix: Dialects, Delimiters, and Call Semantics
- Unified diff:
  - Header rules: `--- /dev/null` for new files; `+++ b/<path>`; small hunks; grouped changes.
- Aider SEARCH/REPLACE (common outside these repos but critical for us):
  - SEARCH/REPLACE blocks with explicit file, search, replace segments.
- Bash blocks:
  - Plain fenced `bash`; one command per turn (some systems enforce), summarize outputs back.
- Provider‑native:
  - Anthropic tool_use vs OpenAI functions: not shown in these refs as catalogs; handled as API schema. Our TPSL should render short “Tools via Provider API” lists when applicable.

### Design Implications for Our TPSL Jinja Templates
- Template families to provide:
  - pythonic: signature list and short bullets; compact per‑turn availability.
  - unified_diff: “How to propose edits” + helper list with brief descriptions.
  - aider_search_replace: explicit SEARCH/REPLACE usage examples and preferred policy.
  - anthropic_xml: minimal bullets + pointer that schemas are provided via native tool_use.
- Switches/partials:
  - env_snapshot, lsp_policy, brevity/tone constraints, parallelization policy.
  - plan/build gating banners with concise “available tools” list.
- Output placement:
  - System: full catalog + policy blocks (configurable).
  - Per‑turn: short availability banner + provider‑native “tools provided via API” notice when applicable.

### Migration Notes and Edge Cases
- Parser fragility in OpenCode implies our prompts should not depend on a single sentinel; we should render clear fenced blocks and rely on dialect parsers.
- Concurrency cautions: mirror Crush’s “parallel only when independent” language.
- Safety: expose permission banners and “no commit unless asked” variants.

### Appendix: Raw Excerpts
- Crush – LSP guidance (anthropic.md/v2.md):
```
# LSP Information
Tools that support it will also include useful diagnostics ...
- You should ignore diagnostics of files that you did not change ...
```
- Crush – Tool policy:
```
IMPORTANT: All tools are executed in parallel when multiple tool calls are sent...
The user does not see the full output of the tool responses; summarize it.
```
- OpenCode – Unified diff helpers (our unified_diff system_full template mirrors this):
```
**How to propose edits (Unified Diff)**
- Generate a valid git unified diff. For new files, use `--- /dev/null` and `+++ b/<path>`.
- Keep hunks small and logically grouped.
```
- Cursor – Meta‑rules (cursor_system_prompt.md):
```
You have tools at your disposal ... NEVER refer to tool names when speaking to the USER.
Be THOROUGH when gathering information. Semantic search is your MAIN exploration tool.
```
