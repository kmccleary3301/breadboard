# External Session Import & Resume (Claude Code / Codex CLI / OpenCode)

**Goal:** In the finished KyleCode/BreadBoard TUI, support “resume in BreadBoard” from sessions originally run via other harnesses (Claude Code, Codex CLI, OpenCode) by **importing their session state into BreadBoard’s canonical session IR** and then continuing with an **E3-equivalent mimic config** inside BreadBoard.

This is a high-leverage “harness calculus” demonstration: *external harness session → canonical IR → mimic config → continue*.

---

## 1) What “Resume” Means Here

We mean **A) import + continue in BreadBoard**, not “attach to a still-running external harness process”.

Resume pipeline:
1. **Ingest** external session artifacts (from our logged wrappers and/or by parsing local state formats).
2. **Normalize** to a single **Session Capsule** schema.
3. **Select** the appropriate BreadBoard mimic config (e.g., “Codex CLI mimic”, “Claude Code mimic”, “OpenCode mimic”).
4. **Spawn** a new BreadBoard session seeded with the capsule’s boundary state.
5. Continue normally under BreadBoard (tools, guards, checkpoints, UI).

---

## 2) Why This Is Harder Than “Load Transcript”

To be “seamless” (and meaningfully E3-equivalent), we need more than visible chat text.

**Minimum boundary state required:**
- **Prompt state:** system prompt(s), memory files injected, tool schemas, harness/policy reminders, output style, compaction state (if any).
- **Tool transcript:** tool calls/results with stable IDs and ordering (these shape downstream behavior).
- **Permissions state:** permission mode + allow/ask/deny rules and any “don’t ask again” affordances.
- **Workspace state:** either a workspace snapshot/checkpoints or a deterministic patch chain that reconstructs the exact workspace at the resume boundary.
- **Model identity:** model + provider routing + “temperature/sampling” knobs if the goal is strict behavior matching.

**Practical note:** “E3-equivalent” after the boundary is only meaningful if the runtime conditions are comparable (model, system prompt, tool surface, workspace). Otherwise it becomes “E2-ish: same semantics, not the same stochastic continuation”.

---

## 3) Two Import Tiers (Recommended + Optional)

### Tier 0 (Recommended): “Logged Wrapper” Capsules

Only support resume from sessions that were run through our instrumented wrappers (or blessed goldens).

Benefits:
- Deterministic, supportable, and testable.
- We *already* have the critical artifacts (raw requests/responses, tool traces, golden workspaces) in various places.
- Enables conformance tests: “import capsule → reproduce N turns → compare”.

### Tier 1 (Optional): Parse Private Local State Formats

Add best-effort adapters that can discover and import sessions from the native local state stores of:
- Claude Code
- Codex CLI
- OpenCode

Benefits:
- “It just works” for real-world users, not only logged runs.

Costs / risks:
- Formats change; needs version detection + robust fallbacks.
- Some state may be unavailable (e.g., internal compaction summaries, hidden prompts).
- Must be treated as **best-effort**, not strict parity.

Recommendation:
- Build Tier 0 first (to validate the whole import/resume architecture).
- Add Tier 1 adapters incrementally, behind a “best-effort import” label in the UI.

---

## 4) Session Capsule: Canonical Artifact Schema

The capsule is what BreadBoard needs to “resume”.

Suggested on-disk layout (directory or zip):
```
capsules/<capsule_id>/
  manifest.json                # source tool + version, created_at, repo root, config hash, model, etc.
  boundary.json                # normalized boundary state (mode, permissions, tool schema fingerprints, memory refs)
  transcript.jsonl             # canonical events: user/assistant/tool_call/tool_result/etc.
  workspace/
    snapshot.tar.zst           # OR checkpoint chain / patch series
    metadata.json              # optional: git commit, untracked files policy, ignore patterns
  source/
    raw/                       # optional: raw provider dumps, raw CLI logs, request bodies, etc.
```

**Design preference:** keep `transcript.jsonl` aligned with BreadBoard’s internal event schema (or a thin adapter to it), so the rest of the system doesn’t care whether the session came from OpenCode/Claude/Codex.

---

## 5) Mimic Config Selection

On import, BreadBoard should choose (or recommend) a config based on capsule metadata:
- `source_tool`: `claude_code` | `codex_cli` | `opencode`
- `source_version`: string
- `model_id`: provider/model
- `permission_mode`, tool dialect, diff dialect, etc.

Two UX modes:
- **Auto**: pick the most likely mimic config (with a short “why” explanation).
- **Pick**: present a model/config picker if ambiguous.

---

## 6) TUI UX (Target)

### CLI entry
- `kyle import` → list discovered capsules and “best-effort” detected native sessions.
- `kyle import <id>` → open interactive resume picker or directly “Continue”.

### REPL entry
- `/import` → modal listing sessions.
- Each entry shows: source, version, time, repo/workdir, model, and whether it’s “deterministic capsule” vs “best-effort parse”.
- Actions: **Continue**, **Fork**, **View details**, **Export capsule**.

---

## 7) Engine / Bridge Integration (Shape Only)

Avoid hacky “paste transcript into prompt”.

Preferred approach:
- Add a bridge endpoint like `POST /imports` or `POST /sessions/import` that:
  - accepts a capsule path/ID/upload,
  - validates it,
  - creates a new session seeded from the capsule boundary state.

This keeps the TUI thin and makes resume usable from headless flows too.

---

## 8) Testing Strategy

Tier 0 unlocks real regression tests:
- fixture capsules checked into `misc/` or `goldens/` (or generated deterministically in CI)
- tests that:
  - import capsule,
  - run 1–3 turns under the selected mimic config,
  - assert normalized trace equivalence (E2/E3 targets),
  - assert workspace equivalence when applicable.

---

## 9) Open Questions (To Decide Before Implementation)

- Do we store workspace snapshots as full tarballs, patch series, or “git commit + patch chain”?
- How do we handle secrets in imported sessions (redaction vs user confirmation)?
- What’s the minimal “resume boundary” for each harness (Claude Code vs Codex CLI vs OpenCode)?
- How do we represent compaction/summaries in the capsule so that the resumed context matches?

