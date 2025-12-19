# [RECOVERY MISSING LINE 1]
# [RECOVERY MISSING LINE 2]
# [RECOVERY MISSING LINE 3]
# [RECOVERY MISSING LINE 4]
# [RECOVERY MISSING LINE 5]
# [RECOVERY MISSING LINE 6]
# [RECOVERY MISSING LINE 7]
# [RECOVERY MISSING LINE 8]
# [RECOVERY MISSING LINE 9]
# [RECOVERY MISSING LINE 10]
# [RECOVERY MISSING LINE 11]
# [RECOVERY MISSING LINE 12]
# [RECOVERY MISSING LINE 13]
# [RECOVERY MISSING LINE 14]
# [RECOVERY MISSING LINE 15]
# [RECOVERY MISSING LINE 16]
# [RECOVERY MISSING LINE 17]
# [RECOVERY MISSING LINE 18]
# [RECOVERY MISSING LINE 19]
# [RECOVERY MISSING LINE 20]
# [RECOVERY MISSING LINE 21]
# [RECOVERY MISSING LINE 22]
# [RECOVERY MISSING LINE 23]
# [RECOVERY MISSING LINE 24]
# [RECOVERY MISSING LINE 25]
# [RECOVERY MISSING LINE 26]
# [RECOVERY MISSING LINE 27]
# [RECOVERY MISSING LINE 28]
# [RECOVERY MISSING LINE 29]
# [RECOVERY MISSING LINE 30]
# [RECOVERY MISSING LINE 31]
# [RECOVERY MISSING LINE 32]
# [RECOVERY MISSING LINE 33]
# [RECOVERY MISSING LINE 34]
# [RECOVERY MISSING LINE 35]
# [RECOVERY MISSING LINE 36]
# [RECOVERY MISSING LINE 37]
# [RECOVERY MISSING LINE 38]
# [RECOVERY MISSING LINE 39]
# [RECOVERY MISSING LINE 40]
# [RECOVERY MISSING LINE 41]
# [RECOVERY MISSING LINE 42]
# [RECOVERY MISSING LINE 43]
# [RECOVERY MISSING LINE 44]
# [RECOVERY MISSING LINE 45]
# [RECOVERY MISSING LINE 46]
# [RECOVERY MISSING LINE 47]
# [RECOVERY MISSING LINE 48]
# [RECOVERY MISSING LINE 49]
# [RECOVERY MISSING LINE 50]
# [RECOVERY MISSING LINE 51]
# [RECOVERY MISSING LINE 52]
# [RECOVERY MISSING LINE 53]
# [RECOVERY MISSING LINE 54]
# [RECOVERY MISSING LINE 55]
# [RECOVERY MISSING LINE 56]
# [RECOVERY MISSING LINE 57]
# [RECOVERY MISSING LINE 58]
# [RECOVERY MISSING LINE 59]
# [RECOVERY MISSING LINE 60]
# [RECOVERY MISSING LINE 61]
# [RECOVERY MISSING LINE 62]
# [RECOVERY MISSING LINE 63]
# [RECOVERY MISSING LINE 64]
# [RECOVERY MISSING LINE 65]
# [RECOVERY MISSING LINE 66]
# [RECOVERY MISSING LINE 67]
# [RECOVERY MISSING LINE 68]
# [RECOVERY MISSING LINE 69]
# [RECOVERY MISSING LINE 70]
# [RECOVERY MISSING LINE 71]
# [RECOVERY MISSING LINE 72]
# [RECOVERY MISSING LINE 73]
# [RECOVERY MISSING LINE 74]
# [RECOVERY MISSING LINE 75]
# [RECOVERY MISSING LINE 76]
# [RECOVERY MISSING LINE 77]
# [RECOVERY MISSING LINE 78]
# [RECOVERY MISSING LINE 79]
# [RECOVERY MISSING LINE 80]
# [RECOVERY MISSING LINE 81]
# [RECOVERY MISSING LINE 82]
# [RECOVERY MISSING LINE 83]
# [RECOVERY MISSING LINE 84]
# [RECOVERY MISSING LINE 85]
# [RECOVERY MISSING LINE 86]
# [RECOVERY MISSING LINE 87]
# [RECOVERY MISSING LINE 88]
# [RECOVERY MISSING LINE 89]
# [RECOVERY MISSING LINE 90]
# [RECOVERY MISSING LINE 91]
# [RECOVERY MISSING LINE 92]
# [RECOVERY MISSING LINE 93]
# [RECOVERY MISSING LINE 94]
# [RECOVERY MISSING LINE 95]
# [RECOVERY MISSING LINE 96]
# [RECOVERY MISSING LINE 97]
# [RECOVERY MISSING LINE 98]
# [RECOVERY MISSING LINE 99]
This refines the v0 diagram by adding **checkpoints** and **web/IDE surfaces**. 

---

## 4) Installation & setup

* **Install:** native installers (macOS/Linux/Windows), Homebrew, or a curl bootstrap; first run prompts login. ([Claude Docs][7])
* **Authenticate:** Claude Console (OAuth), Claude App (Pro/Max), or enterprise via **Amazon Bedrock** / **Google Vertex AI**. ([Claude Docs][7])
* **Quick start:** `cd your-project && claude`—terminal REPL launches. ([Claude Docs][2])
* **VS Code extension:** install from Marketplace; features include **plan mode with editing**, **auto‑accept edits**, **inline diffs**, **MCP reuse**, **multi‑session**, plus security notes for auto‑edit permissions. ([Claude Docs][3])

---

## 5) Configuration & settings

### 5.1 Settings files (project/user/managed)

* Per‑project settings under `.claude/` (e.g., `settings.json`), user‑level overrides, and **enterprise‑managed** policies. 
* Settings cover **permissions**, **model defaults**, **status line config**, **terminal keybindings**, **memory**, **MCP servers**, etc. ([Claude Docs][8])

### 5.2 Model configuration

Define default model and enable **extended thinking** where supported; prefer Sonnet for coding. ([Claude Docs][9])

### 5.3 Memory

Use `CLAUDE.md` (project root) and related memory files to steer style, conventions, and guardrails; quick‑add with `#` input prefix in REPL. 

### 5.4 Status line configuration

Configurable **status line** (type, powerline style, sections like mode/model/branch/cost). ([Claude Docs][10])

### 5.5 Terminal configuration & keybindings

* **Multiline:** backslash+Enter, or set up **Shift+Enter** via `/terminal-setup`; **Option+Enter** alternatives documented.
* **Notifications:** iTerm2 notifications and **custom notification hooks** supported.
* **Vim mode:** `/vim` toggles a subset of motions/edits. ([Claude Docs][11])

> **Parity note:** Your CLI can mirror this with a `settings.json` schema and a **managed‑policy overlay** for enterprise. 

---

## 6) CLI: commands, flags & modes

### 6.1 Invocation patterns

* `claude` → interactive REPL
* `claude "seed message"` → REPL pre‑seeded
* `claude -p "query"` → **headless one‑shot** (print mode; supports JSON/stream‑JSON)
* Piping is idiomatic: `cat file | claude -p "explain"` 

### 6.2 Notable flags (canonical subset)

* `--model <alias|full>`
* `--permission-mode <default|plan|acceptEdits|bypassPermissions>`
* `--allowedTools`, `--disallowedTools`
* `--output-format <text|json|stream-json>`, `--input-format stream-json`, `--include-partial-messages`
* `--continue`, `--resume <id>`
* `--dangerously-skip-permissions` (sandboxed only)
* `--append-system-prompt` (print‑mode)
* `--verbose` 

### 6.3 Plan mode from CLI

`claude --permission-mode plan` or toggle via **Shift+Tab** during a session. ([Claude Docs][12])

---

## 7) Interactive mode (REPL) UX

* **Shortcuts:** `Ctrl+C` cancel, `Ctrl+D` exit, `Ctrl+L` clear, ↑/↓ history; **multiline** as above; **quick prefixes:** `#` (memory), `/` (slash), `!` (run bash unmediated, still logged). 
* **Mode badge** and **status line** show **normal/plan/auto‑accept** and model/current dir/branch. 
* **Background tasks:** long‑running bash can be backgrounded; task IDs & output retrieval are exposed. 

---

## 8) Checkpointing (rewind)

* Claude creates a **checkpoint before each edit**.
* Trigger **rewind** with **Esc Esc** or **`/rewind`**.
* Choose to restore **code**, **conversation**, or **both**; Git is still your canonical VCS. ([Claude Docs][1])

> **Parity note:** Implement a checkpoint driver that (1) snapshots only **Claude‑applied diffs**, and (2) stores **conversation snapshots** in parallel; surface a selector for **code vs conversation vs both**. 

---

## 9) Permissions & safety model

Modes:

* **default (ask on first use)**
* **plan (read‑only planning)**
* **acceptEdits (auto‑approve edits; optionally other tools)**
* **bypass/skip** (only for sandboxed environments)
  Hierarchy supports allow/deny lists and managed settings. Toggle with **Shift+Tab**; set defaults in settings. 

---

## 10) Slash commands

### 10.1 Built‑ins (common set)

`/agents`, `/compact [instructions]`, `/config`, `/cost`, `/doctor`, `/help`, `/init`, `/login`, `/logout`, `/mcp`, `/memory`, `/model`, `/permissions`, `/review`, `/status`, `/terminal-setup`, `/vim`, **`/rewind`**.  ([Claude Docs][13])

### 10.2 Custom slash commands

Markdown files in **`.claude/commands/`** (project) or **`~/.claude/commands/`** (user) with frontmatter + arguments, optional bash blocks; may surface MCP prompts. 

---

## 11) Tool catalog (core)

Representative tools and behaviors (semantic parity set for replication):
**Read/Write/Edit/MultiEdit**, **Glob/Grep**, **Bash** (+ output/kill), **NotebookEdit**, **WebFetch/WebSearch** (permissioned), **TodoWrite** (internal agent todo), **ListMcpResources/ReadMcpResource**, **ExitPlanMode**. 

> **Parity note:** replicate **multi‑file diff** semantics and a hardened bash executor with backgrounding and output buffering; adhere to allow‑list scoping. 

---

## 12) Subagents

# [RECOVERY MISSING LINE 221]
# [RECOVERY MISSING LINE 222]
# [RECOVERY MISSING LINE 223]
# [RECOVERY MISSING LINE 224]
# [RECOVERY MISSING LINE 225]
# [RECOVERY MISSING LINE 226]
# [RECOVERY MISSING LINE 227]
# [RECOVERY MISSING LINE 228]
# [RECOVERY MISSING LINE 229]
# [RECOVERY MISSING LINE 230]
# [RECOVERY MISSING LINE 231]
# [RECOVERY MISSING LINE 232]
# [RECOVERY MISSING LINE 233]
# [RECOVERY MISSING LINE 234]
# [RECOVERY MISSING LINE 235]
# [RECOVERY MISSING LINE 236]
# [RECOVERY MISSING LINE 237]
# [RECOVERY MISSING LINE 238]
# [RECOVERY MISSING LINE 239]

Programmatically define subagents via an `agents` parameter; each agent has **description, prompt, tools, model**; SDK can **auto‑invoke** based on context. ([Claude Docs][15])

---

## 13) Hooks (lifecycle automation)

Hooks let you run logic **before/after** key events (e.g., run tests after edits, send notifications, enforce policy checks). See **hooks reference** for events and examples; notification hooks are also referenced in terminal config. ([Claude Docs][16])

---

## 14) MCP & plugins

**Model Context Protocol** connects external systems (resources, prompts, tools). Configure servers (project/user), scope permissions, and expose resources/prompts—including via slash commands. ([Claude Docs][17])

---

## 15) SDK & headless / streaming JSON

Claude Agent SDK (TS/Python) exposes **`query()`**, streaming events, tool invocations, permission hooks, MCP helpers, and subagents APIs. CLI **print mode** supports **`--output-format json|stream-json`** and **`--input-format stream-json`** for **pipelines** and **CI**. 

---

## 16) IDE integrations (VS Code)

**Native VS Code extension (beta)**

* **Inline diffs**, **plan mode with editing**, **auto‑accept**, **sidebar**, **multi‑session**, and **(most) slash commands**.
* Reuses CLI **MCP** config; supports **Bedrock/Vertex** via env vars; includes **security guidance** when enabling auto‑edits. ([Claude Docs][3])

---

## 17) Claude Code on the web (research preview)

Start tasks from **claude.ai**, where Anthropic provisions an **isolated VM** that clones your repo, runs tests, applies edits, and pushes to a branch; you can **“Open in CLI”** to resume locally. Available to **Pro/Max** (Team/Enterprise premium seats “coming soon”). ([Claude Docs][4])

---

## 18) Enterprise features & admin

Team/Enterprise **premium seats** combine Claude + Claude Code and offer:
**seat management**, **granular spend caps**, **usage analytics** (e.g., lines accepted, accept rate), **managed policy deployment** (tool permissions, file access, MCP configs), and a **Compliance API** for programmatic auditing. ([anthropic.com][5])

---

## 19) Security & data usage

* **IDE** & **auto‑edit** hardening guidance when running in VS Code. ([Claude Docs][3])
* **Cloud runs** (web): isolated VMs, network egress controls, credential scoping via secure proxy, branch restrictions, audit logging, auto cleanup. ([Claude Docs][18])
* **Data usage (web)**: code stored in an isolated VM for the session; outbound traffic proxied; retention follows account policy. ([Claude Docs][19])

---

## 20) UX aesthetics & terminal guide

* **Themes & appearance:** terminal‑driven; set CLI theme to match via `/config`.
* **Line breaks & keybindings:** configure **Shift+Enter** with `/terminal-setup`; **Option+Enter** and backslash+Enter alternatives.
* **Notifications:** iTerm2 + **custom notification hooks**.
* **Vim mode:** subset of motions/edits enabled via `/vim`. ([Claude Docs][11])
* **Status line:** choose minimal/extended/powerline styles and sections. ([Claude Docs][10])

---

## 21) Session state, compaction & memory

Session registry per working directory; **continue/resume** by ID; automatic **summarization/compaction** to stay within context; **system reminders** to keep threads on task; internal **todo** tracking via `TodoWrite`. 

---

## 22) Patterns & workflows (illustrative)

* **Explore → Plan → Execute** (start in **plan**, switch to **acceptEdits** when confident; use `/review`/PR flows). 
* **Subagent delegation** (reviewer/debugger/test‑runner/data‑scientist). ([Claude Docs][14])
* **Plan Mode from CLI**: `claude --permission-mode plan`, including headless queries. ([Claude Docs][12])

---

## 23) Troubleshooting & diagnostics

Run `/doctor`, inspect `/status`, enable `--verbose`; watch for MCP output/timeout warnings; validate permissions if edits/execs are blocked. 

---

# Appendix A — CLI & flags matrix (parity target)

| Area                | Command/Flag                          | Behavior                                         |                                              |                    |                              |
| ------------------- | ------------------------------------- | ------------------------------------------------ | -------------------------------------------- | ------------------ | ---------------------------- |
| **Start REPL**      | `claude`                              | Launch terminal REPL.                            |                                              |                    |                              |
| **Seeded start**    | `claude "..."`                        | Start REPL with initial user message.            |                                              |                    |                              |
| **Headless**        | `-p`, `--output-format json           | stream-json`                                     | Non‑interactive print mode; JSON streaming.  |                    |                              |
| **Model**           | `--model sonnet                       | opus                                             | haiku                                        | <full>`            | Override model for session.  |
| **Permissions**     | `--permission-mode default            | plan                                             | acceptEdits                                  | bypassPermissions` | Set mode at start.           |
| **Allow/Deny**      | `--allowedTools`, `--disallowedTools` | Scope tools/patterns (e.g., `Bash(git log:*)`).  |                                              |                    |                              |
| **Resume/Continue** | `--resume <id>`, `--continue`         | Reattach to session context.                     |                                              |                    |                              |
| **Safety**          | `--dangerously-skip-permissions`      | Skip prompts in locked‑down environments.        |                                              |                    |                              |
| **Append system**   | `--append-system-prompt`              | Print‑mode only (debug/introspection).           |                                              |                    |                              |
| **Verbosity**       | `--verbose`                           | Turn‑by‑turn diagnostics.                        |                                              |                    |                              |

---

# Appendix B — Example configs

### B.1 `.claude/settings.json` (excerpt – parity shape)

```json
{
  "model": { "default": "sonnet" },
  "permissions": {
    "defaultMode": "default",
    "allowedTools": ["Read", "Edit", "Grep", "Glob", "Bash(git:*)"],
    "disallowedTools": ["WebFetch", "WebSearch"]
  },
  "statusline": { "type": "powerline", "sections": ["mode","model","cwd","git","cost"] },
  "terminal": { "newline": "shift-enter", "vimMode": true }
}
```

(Fields reflect structures documented across **settings**, **status line**, and **terminal** pages.) ([Claude Docs][8])

### B.2 Subagent file `.claude/agents/test-runner.md`

```markdown
---
name: test-runner
description: Use proactively to run tests and triage failures
tools: Bash, Read, Grep
model: inherit
---
You are a test execution specialist...
```

([Claude Docs][14])

---

# Appendix C — Headless streaming (illustrative)

**Script → CLI** via `--output-format stream-json` (events: `init`, `assistant` partials, `tool_call`, `tool_result`, `result`). 

---

# Appendix D — Parity checklist (KyleCode mapping)

**Control loop & autonomy**

* Modes: **default/plan/acceptEdits/bypass** with **Shift+Tab** cycling; **plan** is read‑only. ([Claude Docs][12]) 
* **Checkpoints**: snap before edits; **Esc Esc** or **`/rewind`** to restore **code / conversation / both**. ([Claude Docs][1])
* **Bash backgrounding** with task IDs & buffered output. 

**Config & UX**

* `.claude/settings.json` hierarchy (user/project/managed), **status line** profiles, terminal keybindings (`/terminal-setup`). ([Claude Docs][8])
* **Slash commands** catalog + custom commands in `.claude/commands/`. ([Claude Docs][13]) 

**Extensibility**

* **Subagents** (.md + YAML) and **SDK `agents`** parameter; **/agents** TUI. ([Claude Docs][14])
* **Hooks** (pre/post) for tests, notifications, policy. ([Claude Docs][16])
* **MCP** servers, resources/prompts; permissioned usage. ([Claude Docs][17])

**Surfaces**

* **VS Code extension (beta)**: inline diffs, plan, auto‑accept, MCP reuse, multi‑session. ([Claude Docs][3])
* **Claude Code on the web**: isolated VMs, Open‑in‑CLI. ([Claude Docs][4])
* **Enterprise**: managed policies, usage analytics, **Compliance API**. ([anthropic.com][5])

---

## Notes on fidelity & scope

* I’ve **avoided unverified internals** (e.g., private file paths/log layouts) and stuck to public behavior. For SDK/CLI flags where Anthropic docs are high‑level, I preserved your v0 reference behaviors (headless streaming, event types, etc.) and marked them as parity targets. 
* All **2.0 features** (checkpoints, Terminal v2, VS Code extension, web surface, enterprise controls) are grounded in Anthropic’s pages above. ([Claude Docs][1])

---

### If you want, I can now:

* **Generate a side‑by‑side parity diff** (Claude Code 2.0 → KyleCode flags, modes, hooks, subagents).
* **Draft `.claude/settings.json` and template subagents** tailored to your engine’s controls.
* **Output a CLI help page & man‑style reference** (flags + examples) that mirrors the table above for KyleCode.

[1]: https://docs.anthropic.com/en/docs/claude-code/overview "Claude Code overview - Claude Docs"
[2]: https://docs.claude.com/en/docs/claude-code/overview?utm_source=chatgpt.com "Claude Code overview"
[3]: https://docs.claude.com/en/docs/claude-code/vs-code "Visual Studio Code - Claude Docs"
[4]: https://docs.claude.com/en/docs/claude-code/claude-code-on-the-web?utm_source=chatgpt.com "Claude Code on the web"
[5]: https://www.anthropic.com/news/claude-code-on-team-and-enterprise "Claude Code and new admin controls for business plans \ Anthropic"
[6]: https://www.anthropic.com/news/claude-3-7-sonnet "Claude 3.7 Sonnet and Claude Code \ Anthropic"
[7]: https://docs.claude.com/en/docs/claude-code/setup?utm_source=chatgpt.com "Set up Claude Code"
[8]: https://docs.claude.com/en/docs/claude-code/settings "Claude Code settings - Claude Docs"
[9]: https://docs.claude.com/en/docs/claude-code/model-config "Model configuration - Claude Docs"
[10]: https://docs.claude.com/en/docs/claude-code/statusline "Status line configuration - Claude Docs"
[11]: https://docs.claude.com/en/docs/claude-code/terminal-config "Optimize your terminal setup - Claude Docs"
[12]: https://docs.anthropic.com/en/docs/claude-code/tutorials "Common workflows - Claude Docs"
[13]: https://docs.claude.com/en/docs/claude-code/slash-commands "Slash commands - Claude Docs"
[14]: https://docs.anthropic.com/en/docs/claude-code/sub-agents "Subagents - Claude Docs"
[15]: https://docs.anthropic.com/en/docs/claude-code/sdk/subagents "Subagents in the SDK - Claude Docs"
[16]: https://docs.claude.com/en/docs/claude-code/hooks "Hooks reference - Claude Docs"
[17]: https://docs.claude.com/en/docs/claude-code/mcp?utm_source=chatgpt.com "Connect Claude Code to tools via MCP"
[18]: https://docs.claude.com/en/docs/claude-code/security?utm_source=chatgpt.com "Security - Claude Docs"
[19]: https://docs.claude.com/en/docs/claude-code/data-usage?utm_source=chatgpt.com "Data usage"
