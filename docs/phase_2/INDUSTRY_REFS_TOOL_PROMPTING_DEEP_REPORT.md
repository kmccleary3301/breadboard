# Industry References – Tool Prompting Deep Report (V2: Comprehensive Analysis)

## Executive Summary

This report provides an extensive analysis of tool availability prompting patterns across major agentic coding systems: **Aider (anon-kode)**, **Crush**, **OpenCode**, **Claude Code**, and **Cursor**. The goal is to extract specific patterns, examples, and implementation details to guide the development of our Tool-Prompt Synthesis Layer (TPSL) Jinja templates.

**Key Findings:**
- Each system uses distinct tool prompt injection strategies (system-once vs per-turn vs provider-native)
- Significant variation in tool catalog formats (pythonic signatures vs XML vs narrative descriptions)
- Universal patterns: brevity constraints, concurrency policies, safety guardrails
- Critical insight: The most successful systems adapt their prompting based on provider capabilities

## Table of Contents

1. [Methodology and Reverse Engineering Approach](#methodology-and-reverse-engineering-approach)
2. [Cross-System Architecture Patterns](#cross-system-architecture-patterns)
3. [Detailed System Analysis](#detailed-system-analysis)
4. [Tool Catalog Format Analysis](#tool-catalog-format-analysis)
5. [Provider-Specific Adaptations](#provider-specific-adaptations)
6. [Constraint and Safety Pattern Analysis](#constraint-and-safety-pattern-analysis)
7. [Reverse Engineering Tutorials](#reverse-engineering-tutorials)
8. [Implementation Recommendations for TPSL](#implementation-recommendations-for-tpsl)
9. [Jinja Template Blueprints](#jinja-template-blueprints)

## Methodology and Reverse Engineering Approach

### Corpus Analysis Method
- **Source Material**: Complete codebases from `industry_coder_refs/` including configuration files, prompt templates, and tool definitions
- **Focus Areas**: System prompts, tool catalogs, provider adapters, and runtime prompt compilation
- **Extraction Technique**: Static code analysis combined with configuration pattern identification

### Data Points Extracted Per System
1. **Tool Catalog Structure**: How tools are described and presented to models  
2. **Prompt Injection Strategy**: When and where tool information gets inserted
3. **Provider Adaptations**: Model-specific formatting and behavior changes
4. **Safety and Constraint Patterns**: Rate limiting, permission systems, execution policies
5. **Concurrency and Execution Models**: How multiple tool calls are handled

## Cross-System Architecture Patterns

### Pattern 1: Prompt Injection Timing
**System-Once Pattern** (Crush, Claude Code default):
- Tool catalog embedded once in initial system prompt
- Pros: Consistent context, reduced token usage per turn
- Cons: Large initial context, harder to update dynamically

**Per-Turn Injection** (Aider, OpenCode modes):
- Tool information appended to each user message
- Pros: Fresh tool context, adaptable per interaction
- Cons: Higher token usage, potential repetition fatigue

**Hybrid Cached + Per-Turn** (Claude Code advanced, Our TPSL target):
- Comprehensive system prompt cached, minimal per-turn availability
- Best of both approaches with optimized token usage

### Pattern 2: Tool Discovery vs. Explicit Catalogs
**Implicit Discovery** (OpenCode):
- Tools inferred through code block recognition (`bash`, unified diff)
- Minimal explicit catalogs, rely on model understanding of conventions

**Explicit Catalogs** (Aider, Crush, Claude Code):
- Comprehensive tool signatures with parameters and descriptions
- Higher precision, better guidance for complex operations

### Pattern 3: Provider-Native Integration
**Text-Based Universal** (Most systems in fallback mode):
- Custom formats (XML, pythonic calls) regardless of provider
- Maximum portability but misses native capabilities

**Provider-Aware Hybrid** (Claude Code, Advanced Crush):
- Native function calling when available, text fallback otherwise
- Optimal performance but requires sophisticated routing

## Detailed System Analysis

### Aider (anon-kode-main) - "The Maximalist Approach"

**Core Philosophy**: Comprehensive tool documentation with extensive safety measures

**Tool Prompt Structure**:
```typescript
// From src/tools/FileEditTool/prompt.ts - Example of extensive tool documentation
export const DESCRIPTION = `This is a tool for editing files. For moving or renaming files, you should generally use the Bash tool with the 'mv' command instead.

Before using this tool:
1. Use the View tool to understand the file's contents and context
2. Verify the directory path is correct

CRITICAL REQUIREMENTS FOR USING THIS TOOL:
1. UNIQUENESS: The old_string MUST uniquely identify the specific instance
   - Include AT LEAST 3-5 lines of context BEFORE the change point
   - Include AT LEAST 3-5 lines of context AFTER the change point
2. SINGLE INSTANCE: This tool can only change ONE instance at a time
3. VERIFICATION: Before using this tool check how many instances exist

WARNING: If you do not follow these requirements:
   - The tool will fail if old_string matches multiple locations
   - You may change the wrong instance without enough context
`
```

**Key Insights:**
- **Defensive Documentation**: Extensive failure mode explanations and requirements
- **Context Requirements**: Specific guidance on providing sufficient code context
- **Progressive Disclosure**: Tool descriptions reference other tools, creating a knowledge web
- **Safety First**: Multiple warnings and validation steps before execution

**System Prompt Injection Strategy**:
```typescript
// From src/constants/prompts.ts
export async function getSystemPrompt(): Promise<string[]> {
  return [
    `You are ${PRODUCT_NAME}, a CLI for coding.
    
    # Tone and style
    You should be concise, direct, and to the point.
    IMPORTANT: You should minimize output tokens as much as possible
    IMPORTANT: Keep your responses short, since they will be displayed on a command line interface.
    You MUST answer concisely with fewer than 4 lines (not including tool use or code generation)`
    // ... extensive constraints and examples follow
  ]
}
```

**Tool Catalog Approach**: Provider-native function calling with comprehensive descriptions embedded in tool definitions rather than system prompts.

### Crush - "The Provider-Adaptive Specialist"

**Core Philosophy**: Minimize tokens, maximize efficiency with provider-specific optimizations

**Provider-Based Prompt Selection**:
```go
// From internal/llm/prompt/coder.go
func CoderPrompt(p string, contextFiles ...string) string {
  var basePrompt string
  
  basePrompt = string(anthropicCoderPrompt)  
  switch p {
  case string(catwalk.InferenceProviderOpenAI):
    basePrompt = string(coderV2Prompt)  // "seems to behave better"
  case string(catwalk.InferenceProviderGemini):
    basePrompt = string(geminiCoderPrompt)
  }
  // Dynamic environment and LSP info injection
  envInfo := getEnvironmentInfo()
  basePrompt = fmt.Sprintf("%s\n\n%s\n%s", basePrompt, envInfo, lspInformation())
}
```

**LSP Integration Pattern**:
```go
func lspInformation() string {
  return `# LSP Information
Tools that support it will also include useful diagnostics such as linting and typechecking.
- These diagnostics will be automatically enabled when you run the tool
- Take necessary actions to fix the issues.
- You should ignore diagnostics of files that you did not change unless explicitly asked`
}
```

**Key Insights:**
- **Provider Specialization**: Different base prompts optimized for each LLM provider
- **Runtime Context Injection**: Environment and LSP status dynamically added
- **Extreme Brevity**: "NEVER use emojis", "DO NOT ADD ANY COMMENTS unless asked"
- **Parallel Execution Emphasis**: "All tools are executed in parallel when multiple tool calls are sent"

### OpenCode - "The Mode-Driven Minimalist"

**Core Philosophy**: Implicit tool discovery with mode-based availability gating

**Mode-Based Tool Availability**:
```typescript
// From packages/opencode/src/session/mode.ts - Inferred structure
enum Mode {
  PLAN = "plan",     // Read-only tools: read_file, list_dir, grep, glob
  BUILD = "build",   // All tools enabled including write operations
  BEAST = "beast"    // Enhanced research mode with mandatory web search
}
```

**Provider-Specific System Prompts**:
```typescript
// From packages/opencode/src/session/system.ts
export function provider(modelID: string) {
  if (modelID.includes("gpt-") || modelID.includes("o1") || modelID.includes("o3")) 
    return [PROMPT_BEAST]
  if (modelID.includes("gemini-")) 
    return [PROMPT_GEMINI]  
  if (modelID.includes("claude")) 
    return [PROMPT_ANTHROPIC]
  return [PROMPT_ANTHROPIC_WITHOUT_TODO]
}
```

**Tool Documentation Style** (Minimal but precise):
```text
// From packages/opencode/src/tool/bash.txt
Executes a given bash command in a persistent shell session with optional timeout.

Before executing the command, please follow these steps:
1. Directory Verification: Use the LS tool to verify parent directory exists
2. Command Execution: Always quote file paths that contain spaces

Usage notes:
- VERY IMPORTANT: You MUST avoid using search commands like `find` and `grep`
- Instead use Grep, Glob, or Task to search
- Always USE ripgrep at `rg` first, which all opencode users have pre-installed
```

**Key Insights:**
- **Implicit Tool Discovery**: Relies on code fences (`bash`, unified diff) more than explicit catalogs
- **Mode Restrictions**: Tools availability changes based on current operational mode
- **Mandatory Web Research**: Beast mode requires internet research for any implementation
- **Tool Substitution Guidance**: Clear direction on which tools to use instead of shell commands

### Claude Code - "The Hybrid Precision System"

**Core Philosophy**: Provider-native when possible, with comprehensive tool management and precise brevity

**Advanced Tool Catalog Strategy**:
```markdown
<!-- From logs/basic_first_msg_system.md - Reconstructed system prompt -->
# Tool usage policy
- When doing file search, prefer to use the Agent tool in order to reduce context usage
- You should proactively use the Task tool with specialized agents when the task at hand matches the agent's description
- When WebFetch returns a message about redirect, immediately make new WebFetch request with redirect URL
- You have capability to call multiple tools in single response for optimal performance
```

**Brevity and Precision Focus**:
```markdown
# Tone and style  
You MUST answer concisely with fewer than 4 lines (not including tool use)
Answer the user's question directly, without elaboration, explanation, or details.
One word answers are best. Avoid introductions, conclusions, and explanations.

<example>
user: what is 2+2?
assistant: 4
</example>
```

**Task Management Integration**:
```markdown
# Task Management
You have access to TodoWrite tools - Use these tools VERY frequently
These tools are EXTREMELY helpful for planning tasks and breaking down complex tasks
It is critical that you mark todos as completed as soon as you are done with a task.
```

**Key Insights:**
- **Sub-Agent Architecture**: Specialized Task tool that launches focused sub-agents
- **Progressive Tool Loading**: Context-sensitive tool availability
- **Precision Over Explanation**: Extreme focus on minimal, accurate responses
- **Proactive Task Management**: Built-in todo/progress tracking integration

### Cursor - "The Context-Maximizing Strategist"  

**Core Philosophy**: Maximum context understanding with semantic search emphasis

**Context Maximization Strategy**:
```markdown
<maximize_context_understanding>
Be THOROUGH when gathering information. EXPLORE alternative implementations, edge cases.
Semantic search is your MAIN exploration tool.
- CRITICAL: Start with broad, high-level query (e.g. "authentication flow")
- Break multi-part questions into focused sub-queries
- MANDATORY: Run multiple searches with different wording
- Keep searching new areas until CONFIDENT nothing important remains
</maximize_context_understanding>
```

**Tool Abstraction Philosophy**:
```markdown
<tool_calling>
**NEVER refer to tool names when speaking to the USER.** 
Instead, just say what the tool is doing in natural language.
If you need additional information via tool calls, prefer that over asking user.
Only use standard tool call format and available tools.
You can autonomously read as many files as you need to clarify your questions.
</tool_calling>
```

**Semantic-First Tool Discovery**:
```markdown
// codebase_search tool description
Use `codebase_search` when you need to:
- Explore unfamiliar codebases  
- Ask "how / where / what" questions to understand behavior
- Find code by meaning rather than exact text

Good: "Where is interface MyInterface implemented in the frontend?"
BAD: "MyInterface frontend" (too vague)
```

**Key Insights:**
- **Tool Name Abstraction**: Never mention tool names to users, describe actions instead
- **Semantic-First Discovery**: Prioritize meaning-based search over text matching
- **Autonomous Exploration**: Encouraged to read extensively without asking permission
- **Context Completeness**: Must gather full picture before providing responses

## Tool Catalog Format Analysis

### Format Category 1: Pythonic Signatures (Aider Style)
```typescript
// Clean function signature format with detailed parameter descriptions
interface FileEditToolDefinition {
  name: "FileEditTool"
  description: "Edits files using search/replace with context requirements"
  parameters: {
    file_path: { type: "string", description: "Absolute path to file" }
    old_string: { type: "string", description: "Text to replace with 3-5 lines context" }
    new_string: { type: "string", description: "Replacement text" }
  }
  critical_requirements: [
    "UNIQUENESS: old_string must uniquely identify instance",
    "SINGLE INSTANCE: Only one change per call", 
    "VERIFICATION: Check for multiple instances before use"
  ]
}
```

### Format Category 2: Narrative Instructions (OpenCode/Claude Code Style)
```markdown
## Bash Tool

Executes a given bash command in persistent shell session with optional timeout.

**Before executing:**
1. Directory Verification: Use LS tool to verify parent directory exists
2. Command Execution: Quote file paths with spaces

**Usage notes:**
- VERY IMPORTANT: Avoid `find` and `grep`, use Grep/Glob tools instead
- Use ripgrep (`rg`) which all users have pre-installed  
- For pager commands, append `| cat` to prevent hanging

**Security constraints:**
- Some commands are banned: curl, wget, nc, telnet, lynx, w3m, links
- Non-interactive flags required (e.g. --yes for npx)
```

### Format Category 3: XML Schema Style (Provider Native Integration)
```xml
<tool_definition>
  <name>bash</name>
  <description>Execute shell commands with safety constraints</description>
  <parameters>
    <parameter name="command" type="string" required="true" />
    <parameter name="timeout" type="integer" default="30000" />
    <parameter name="description" type="string" required="false" />
  </parameters>
  <safety_constraints>
    <banned_commands>curl,wget,nc,telnet</banned_commands>
    <require_non_interactive>true</require_non_interactive>
  </safety_constraints>
</tool_definition>
```

### Format Category 4: Hybrid Signature + Context (Our TPSL Target)
```jinja2
{# Template combining multiple format strengths #}
## {{ tool.name }} 
{{ tool.description }}

### Parameters
{% for param in tool.parameters %}
- `{{ param.name }}` ({{ param.type }}){% if param.required %} *required*{% endif %}: {{ param.description }}
{% endfor %}

{% if tool.usage_notes %}
### Usage Notes
{% for note in tool.usage_notes %}
- {{ note }}
{% endfor %}
{% endif %}

{% if tool.examples %}
### Examples
```{{ tool.language }}
{{ tool.examples[0] }}
```
{% endif %}
```

## Provider-Specific Adaptations

### OpenAI/GPT Models Adaptations
**Observed Patterns:**
- More structured, detailed prompts (Crush uses "v2.md" for OpenAI)
- Function calling schemas preferred over text formats
- Emphasis on parallel tool execution capabilities
- JSON-structured responses more reliable

**Example Adaptation (Crush)**:
```go
switch provider {
case "openai":
  basePrompt = string(coderV2Prompt)  // More detailed, structured version
  // Leverages parallel function calling
case "anthropic": 
  basePrompt = string(anthropicCoderPrompt)  // More conversational
}
```

### Anthropic/Claude Models Adaptations  
**Observed Patterns:**
- More conversational, narrative-style prompts
- XML tool_use blocks supported natively
- Better at following complex multi-step procedures
- Responds well to example-driven guidance

**Example Adaptation (OpenCode)**:
```typescript
if (modelID.includes("claude")) 
  return [PROMPT_ANTHROPIC]  // Includes <thinking> blocks and detailed procedures
```

### Gemini Models Adaptations
**Observed Patterns:**
- Simplified prompts work better (OpenCode uses separate PROMPT_GEMINI)
- Less reliable with complex tool schemas
- Benefits from explicit step-by-step guidance
- Better performance with reduced constraint verbosity

## Constraint and Safety Pattern Analysis

### Universal Safety Patterns

**Command Restrictions** (Seen across all systems):
```typescript
// Aider BashTool banned commands
const BANNED_COMMANDS = [
  'curl', 'wget', 'nc', 'telnet', 'lynx', 'w3m', 'links',
  'httpie', 'xh', 'chrome', 'firefox', 'safari'
]
```

**File Access Constraints**:
- All systems require absolute paths or explicit path validation
- Directory traversal prevention through path normalization  
- Workspace root enforcement (can't access outside project)

**Execution Policies**:
- **Single Bash Rule**: "Only one bash command allowed per turn" (research finding)
- **Non-Interactive Enforcement**: "--yes flags required", "assume user not available" 
- **Timeout Constraints**: Default 30s-2min, maximum 10min across systems

### Concurrency Management Patterns

**Parallel Execution Rules** (Crush example):
```markdown
IMPORTANT: All tools are executed in parallel when multiple tool calls are sent
Only send multiple tool calls when they are safe to run in parallel (no dependencies)
```

**Tool Grouping Strategies**:
```yaml
# Pattern seen across systems
concurrency_groups:
  - name: fs_reads
    tools: [read_file, glob, grep, list_dir] 
    max_parallel: 4
  - name: edits_and_bash
    tools: [edit_file, apply_patch, run_shell]
    max_parallel: 1  # Sequential only
```

### Permission and Trust Patterns

**Explicit Permission Requests** (Aider/Claude Code):
- Commands that modify system require user approval
- File operations outside workspace require confirmation
- Network access and external tool usage flagged

**Trust Escalation Models**:
- Start with read-only tools (OpenCode "plan" mode)
- Require explicit user transition to write mode  
- Some operations always require user confirmation

## Reverse Engineering Tutorials

### Tutorial 1: Extracting Full Chat Histories from Aider

**Method: Log File Analysis**
```bash
# Aider stores conversations in ~/.aider/logs/
cd ~/.aider/logs/
ls -la *.jsonl  # Find recent session logs

# Extract tool calls and responses
cat session_20250101_123456.jsonl | jq '.messages[] | select(.tool_calls != null)'

# Extract system prompts (contains tool catalogs)
cat session_20250101_123456.jsonl | jq '.messages[] | select(.role == "system")'
```

**Tool Schema Extraction**:
```typescript  
// From Aider source: src/tools.ts shows tool registration
export const getAllTools = (): Tool[] => {
  return [AgentTool, BashTool, GlobTool, GrepTool, LSTool, ...]
}

// Each tool exports description and parameters
// Example: src/tools/BashTool/prompt.ts contains full tool documentation
```

### Tutorial 2: Reverse Engineering Crush Provider Adaptations

**Method: Configuration Analysis**
```go
// 1. Find prompt templates in internal/llm/prompt/
ls internal/llm/prompt/*.md
// anthropic.md, gemini.md, openai.md, v2.md

// 2. Trace provider selection logic
grep -r "InferenceProvider" internal/llm/prompt/coder.go

// 3. Extract environment injection patterns  
func getEnvironmentInfo() string {
  // Shows how context is dynamically added
}
```

**LSP Integration Discovery**:
```go
// From internal/llm/prompt/coder.go
func lspInformation() string {
  cfg := config.Get()
  hasLSP := false
  for _, v := range cfg.LSP {
    if !v.Disabled { hasLSP = true }
  }
  // Returns LSP-specific tool guidance when available
}
```

### Tutorial 3: OpenCode Mode and Tool Discovery

**Method: Session Flow Analysis**
```typescript
// 1. Find mode definitions
// packages/opencode/src/session/mode.ts

// 2. Trace tool availability by mode
// packages/opencode/src/tool/ - each .txt file is a tool definition

// 3. Provider-specific prompt routing
// packages/opencode/src/session/system.ts
export function provider(modelID: string) {
  // Maps model names to specific prompt variants
}
```

**Tool Definition Extraction**:
```bash
# Each tool has a .txt file with its prompt
find packages/opencode/src/tool -name "*.txt" | head -5
# bash.txt, glob.txt, read.txt, etc.

# Content shows narrative instruction style  
cat packages/opencode/src/tool/bash.txt
```

### Tutorial 4: Claude Code System Prompt Reconstruction

**Method: Log Analysis + Deduction**
```bash
# From claude-code-reverse/logs/basic_first_msg_system.md
# This shows the actual system prompt sent to Claude

# Key sections extracted:
# - Tool usage policy
# - Tone and style constraints  
# - Task management integration
# - Code style rules ("DO NOT ADD ANY COMMENTS unless asked")
```

**Tool Discovery Method**:
```yaml
# From claude-code-reverse/results/tools/*.yaml
# Shows tool definitions extracted from actual usage

# Example: Bash.tool.yaml shows provider-native function calling schema
name: Bash
description: "Executes a given bash command..."  
parameters:
  command: {type: string, required: true}
  timeout: {type: number, default: 120000}
```

### Tutorial 5: Cursor Context Maximization Strategy

**Method: System Prompt Analysis**
```markdown
# From cursor_system_prompt.md - Full system prompt revealed

# Key extraction points:
<maximize_context_understanding>
  # Shows semantic search emphasis
  - CRITICAL: Start with broad, high-level query
  - MANDATORY: Run multiple searches with different wording
</maximize_context_understanding>

<tool_calling>
  # Shows tool abstraction philosophy
  - NEVER refer to tool names when speaking to USER
  - Use standard tool call format only
</tool_calling>
```

**Tool Schema Discovery**:
```typescript
// Cursor tool definitions embedded in system prompt
type codebase_search = (_: {
  explanation: string,
  query: string, 
  target_directories: string[],
}) => any;

// Shows semantic-first discovery approach
```

## Implementation Recommendations for TPSL

### 1. Provider-Adaptive Template Selection

Based on analysis, our TPSL should implement provider-aware template selection:

```jinja2
{# implementations/tool_prompt_synthesis/provider_selection.j2 #}
{% set provider_id = model_id.split('/')[0] %}
{% if provider_id == 'anthropic' %}
  {% include 'anthropic_conversational.j2.md' %}
{% elif provider_id == 'openai' or 'gpt-' in model_id %}
  {% include 'openai_structured.j2.md' %}  
{% elif provider_id == 'google' or 'gemini' in model_id %}
  {% include 'gemini_simplified.j2.md' %}
{% else %}
  {% include 'universal_fallback.j2.md' %}
{% endif %}
```

### 2. Multi-Format Tool Catalog Support

```jinja2
{# Support multiple catalog formats based on configuration #}
{% if format == 'pythonic_signatures' %}
  {% include '_partials/pythonic_tool_catalog.j2' %}
{% elif format == 'narrative_instructions' %}
  {% include '_partials/narrative_tool_catalog.j2' %}
{% elif format == 'xml_schema' %}
  {% include '_partials/xml_tool_catalog.j2' %}
{% elif format == 'hybrid' %}
  {% include '_partials/hybrid_tool_catalog.j2' %}
{% endif %}
```

### 3. Constraint and Safety Blocks

```jinja2
{# implementations/tool_prompt_synthesis/common/_partials/safety_constraints.j2 #}
{% if config.safety.enable_command_restrictions %}
# Security Constraints
- Banned commands: {{ config.safety.banned_commands | join(', ') }}
- Non-interactive flags required (--yes, -y, etc.)
- Network access tools require user permission
{% endif %}

{% if config.concurrency.enabled %}  
# Concurrency Policy  
- Tools in {{ config.concurrency.parallel_groups | join(', ') }} run in parallel
- {{ config.concurrency.sequential_tools | join(', ') }} must run sequentially  
- Maximum {{ config.concurrency.max_parallel }} parallel executions
{% endif %}

{% if config.execution.bash_constraint %}
# Execution Constraints
- Only ONE bash command per turn (research-backed constraint)
- File operations should precede bash commands
- Timeout: {{ config.execution.default_timeout }}ms (max {{ config.execution.max_timeout }}ms)
{% endif %}
```

### 4. Mode-Based Tool Gating

```jinja2
{# Enable OpenCode-style mode restrictions #}
{% if mode == 'plan' %}
# PLAN MODE: Read-Only Operations
Available tools: {{ tools | selectattr('read_only') | map(attribute='name') | list | join(', ') }}
{% elif mode == 'build' %}
# BUILD MODE: All Operations Enabled  
Available tools: {{ tools | map(attribute='name') | list | join(', ') }}
{% elif mode == 'compact' %}
# COMPACT MODE: Essential Tools Only
Available tools: {{ tools | selectattr('essential') | map(attribute='name') | list | join(', ') }}
{% endif %}
```

### 5. Context and Environment Injection

```jinja2
{# Dynamic context injection like Crush #}
{% if environment.lsp_enabled %}
# LSP Integration Available
Tools that support it will include diagnostics (linting, typechecking).
- Diagnostics auto-enabled and shown in <file_diagnostics> tags
- Fix issues in files you modify
- Ignore diagnostics in unchanged files unless explicitly asked
{% endif %}

{% if environment.git_repo %}
# Git Repository Context
Working directory: {{ environment.cwd }}
Is git repo: {{ environment.git_repo | yesno }}
Platform: {{ environment.platform }}
Project structure: 
{{ environment.project_tree | indent(2) }}
{% endif %}
```

## Jinja Template Blueprints

### Template Family 1: Anthropic Conversational

```jinja2
{# implementations/tool_prompt_synthesis/anthropic/system_full.j2.md #}
You are an AI coding assistant. Use the tools available to help with software engineering tasks.

{% include '_partials/tone_conversational.j2.md' %}

# Available Tools

{% for tool in tools %}
## {{ tool.name }}
{{ tool.description }}

{% if tool.parameters %}
**Parameters:**
{% for param in tool.parameters %}
- `{{ param.name }}` ({{ param.type }}){% if param.required %} *required*{% endif %}: {{ param.description }}
  {% if param.default %} Default: {{ param.default }}{% endif %}
{% endfor %}
{% endif %}

{% if tool.usage_notes %}
**Usage Notes:**
{% for note in tool.usage_notes %}
- {{ note }}
{% endfor %}
{% endif %}

{% if tool.examples %}
**Example:**
```{{ tool.example_language | default('text') }}
{{ tool.examples[0] }}
```
{% endif %}

{% endfor %}

{% include '_partials/safety_constraints.j2' %}
{% include '_partials/execution_policies.j2' %}
```

### Template Family 2: OpenAI Structured

```jinja2  
{# implementations/tool_prompt_synthesis/openai/system_full.j2.md #}
You are an AI assistant with access to function calling capabilities.

{% include '_partials/tone_structured.j2.md' %}

# Function Definitions

{% for tool in tools %}
### {{ tool.name }}({{ tool.parameters | map(attribute='name') | join(', ') }})

{{ tool.description }}

**Parameters:**
{% for param in tool.parameters %}
- {{ param.name }}: {{ param.type }}{% if param.required %} (required){% endif %} - {{ param.description }}
{% endfor %}

**Returns:** {{ tool.return_type | default('Tool execution result') }}

{% if tool.constraints %}
**Constraints:**
{% for constraint in tool.constraints %}  
- {{ constraint }}
{% endfor %}
{% endif %}

{% endfor %}

# Execution Rules
{% include '_partials/parallel_execution_guidance.j2' %}
{% include '_partials/error_handling_structured.j2' %}
```

### Template Family 3: Universal Fallback

```jinja2
{# implementations/tool_prompt_synthesis/universal/system_full.j2.md #}
You are an AI coding assistant with access to the following tools:

# Tool Catalog

{% for tool in tools %}
**{{ tool.name }}**: {{ tool.description }}
{% if tool.parameters %}
  Parameters: {{ tool.parameters | map(attribute='name') | join(', ') }}
{% endif %}
{% endfor %}

# Usage Instructions

{% include '_partials/basic_usage_rules.j2' %}
{% include '_partials/safety_minimal.j2' %}

Call tools using this format:
```
<TOOL_CALL>
{{ example_tool_call }}
</TOOL_CALL>
```

For multiple tools, use multiple TOOL_CALL blocks.
```

### Per-Turn Templates

```jinja2
{# implementations/tool_prompt_synthesis/common/per_turn_short.j2.md #}
{% if mode == 'system_compiled_and_persistent_per_turn' %}
# Available Tools This Turn
{{ enabled_tools | join(', ') }} are available.
{% if provider_native_tools %}
Function calling enabled via {{ provider_id }} API.
{% endif %}
{% else %}
{% include 'per_turn_full_directive.j2.md' %}  
{% endif %}
```

### Partials Library

```jinja2
{# _partials/tone_conversational.j2.md #}  
# Communication Style
- Be helpful and thorough in your explanations
- Use natural language to describe your actions
- Ask for clarification when requirements are unclear
- Provide context for your tool usage decisions

{# _partials/tone_structured.j2.md #}
# Response Format
- Provide concise, direct answers  
- Use function calls for all operations
- Minimize explanatory text unless requested
- Focus on efficient task completion

{# _partials/parallel_execution_guidance.j2 #}
# Parallel Execution
- Independent operations ({{ config.concurrency.parallel_groups | join(', ') }}) can run simultaneously
- Sequential operations ({{ config.concurrency.sequential_tools | join(', ') }}) must run one at a time  
- Maximum {{ config.concurrency.max_parallel }} tools per turn

{# _partials/error_handling_structured.j2 #}
# Error Handling
- If a tool fails, analyze the error and retry with corrections
- For validation errors, adjust parameters and retry
- For permission errors, request user approval
- Abort chain execution on critical failures
```

## Conclusion and Next Steps

This comprehensive analysis reveals that successful agentic coding systems share several key patterns:

1. **Provider Adaptation is Critical**: One-size-fits-all prompts underperform compared to provider-specific optimizations
2. **Mode-Based Tool Gating**: Restricting tool availability by operational context (plan vs build) improves safety and focus  
3. **Constraint-Based Safety**: Universal patterns around command restrictions, execution policies, and permission management
4. **Context Maximization**: The most effective systems balance comprehensive information with token efficiency
5. **Format Flexibility**: Different tool catalog formats work better for different providers and use cases

**Implementation Priority for TPSL:**
1. **High Priority**: Provider-adaptive template selection, safety constraint blocks, mode-based gating
2. **Medium Priority**: Multi-format catalog support, dynamic context injection, parallel execution guidance  
3. **Low Priority**: Advanced constraint validation, tool dependency modeling, dynamic tool discovery

The extracted patterns provide a solid foundation for implementing TPSL templates that can replicate and exceed the capabilities of existing systems while maintaining the modularity and configurability required for research and production use.

<function_calls>
<invoke name="TodoWrite">
<parameter name="todos">[{"id": "1", "content": "Survey industry_coder_refs directory structure and contents", "status": "completed"}, {"id": "2", "content": "Extract and analyze Aider tool prompting patterns", "status": "completed"}, {"id": "3", "content": "Extract and analyze Crush tool prompting patterns", "status": "completed"}, {"id": "4", "content": "Extract and analyze OpenCode tool prompting patterns", "status": "completed"}, {"id": "5", "content": "Extract and analyze Claude Code tool prompting patterns", "status": "completed"}, {"id": "6", "content": "Extract and analyze Cursor tool prompting patterns", "status": "completed"}, {"id": "7", "content": "Create reverse engineering tutorials for each tool", "status": "completed"}, {"id": "8", "content": "Compile comprehensive deep report with examples", "status": "completed"}]