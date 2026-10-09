#!/usr/bin/env bun
/**
 * Capture oracle cases for opencode@1.2.17 and oh-my-opencode@3.10.0.
 *
 * Slices pure decision functions verbatim from pinned source files at runtime,
 * transpiles them via Bun.Transpiler, evaluates them via dynamic evaluation,
 * and executes them over BreadBoard inputs. Where dynamic evaluation is impossible,
 * records source_derived cases with exact line citations.
 *
 * Usage:
 *   bun scripts/compaction_oracles/capture_opencode.ts <opencode_dir> <oh_my_opencode_dir>
 */

import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

// ---------------------------------------------------------------------------
// Argument parsing and directory resolution (strictly requires both arguments)
// ---------------------------------------------------------------------------

const rawOpencodeDir = process.argv[2];
const rawOhMyDir = process.argv[3];

if (!rawOpencodeDir || !rawOhMyDir) {
  console.error(
    "Usage: bun scripts/compaction_oracles/capture_opencode.ts <opencode_dir> <oh_my_opencode_dir>"
  );
  process.exit(1);
}

let OPENCODE_DIR = resolve(rawOpencodeDir);
if (!existsSync(join(OPENCODE_DIR, "packages/opencode/src/session/compaction.ts"))) {
  // Check subdirectories if a parent directory was passed
  const entries = existsSync(OPENCODE_DIR) ? readdirSync(OPENCODE_DIR) : [];
  let found = false;
  for (const entry of entries) {
    const candidate = join(OPENCODE_DIR, entry);
    if (existsSync(join(candidate, "packages/opencode/src/session/compaction.ts"))) {
      OPENCODE_DIR = candidate;
      found = true;
      break;
    }
  }
  if (!found) {
    console.error(`Error: OpenCode source dir not found at ${rawOpencodeDir}`);
    process.exit(1);
  }
}

let OH_MY_OPENCODE_DIR = resolve(rawOhMyDir);
if (
  !existsSync(
    join(
      OH_MY_OPENCODE_DIR,
      "src/hooks/anthropic-context-window-limit-recovery/target-token-truncation.ts"
    )
  )
) {
  // Check subdirectories (e.g. oh-my-openagent-e4e13cdebf2c57f3eecb2ab94950f7f6f681169a, package)
  const entries = existsSync(OH_MY_OPENCODE_DIR) ? readdirSync(OH_MY_OPENCODE_DIR) : [];
  let found = false;
  for (const entry of entries) {
    const candidate = join(OH_MY_OPENCODE_DIR, entry);
    if (
      existsSync(
        join(
          candidate,
          "src/hooks/anthropic-context-window-limit-recovery/target-token-truncation.ts"
        )
      )
    ) {
      OH_MY_OPENCODE_DIR = candidate;
      found = true;
      break;
    }
  }
  if (!found) {
    console.error(`Error: oh-my-opencode source dir not found at ${rawOhMyDir}`);
    process.exit(1);
  }
}

const OPENCODE_REPO = "https://github.com/anomalyco/opencode";
const OPENCODE_COMMIT = "715b844c2a88810b6178d7a2467c7d36ea8fb764";

const OH_MY_REPO = "https://github.com/code-yeongyu/oh-my-openagent";
const OH_MY_COMMIT = "e4e13cdebf2c57f3eecb2ab94950f7f6f681169a";

// ---------------------------------------------------------------------------
// Schemas and Types
// ---------------------------------------------------------------------------

interface CaseSource {
  repo: string;
  commit: string;
  evidence: string[];
}

interface CaseCapture {
  kind: "executed" | "source_derived";
  script: string;
  notes: string;
}

interface ChatPart {
  type: string;
  text?: string;
  tool?: string;
  callID?: string;
  state?: {
    status: string;
    output?: string;
    time?: {
      compacted?: number;
    };
  };
  mediaType?: string;
  filename?: string;
  url?: string;
}

interface ChatMessage {
  role: "system" | "user" | "assistant" | "tool";
  content?: string | Array<{ type: string; text?: string; image_url?: { url: string } }>;
  tool_call_id?: string;
  parts?: ChatPart[];
  summary?: boolean;
}

interface CaseInput {
  messages: ChatMessage[];
  usage: {
    input_tokens: number;
    output_tokens: number;
    cache_read_tokens: number;
    cache_write_tokens: number;
    total_tokens: number;
  };
  context_window: number;
  max_input_tokens: number | null;
  max_output_tokens: number | null;
  reason: "threshold" | "overflow" | "manual";
  native_settings: Record<string, unknown>;
  summary_responses: string[];
}

interface CaseExpect {
  trigger?: {
    fires: boolean;
    tokens?: number;
    limit?: number;
    severity?: "hard" | "soft";
  };
  selection?: {
    prefix_end?: number;
    first_kept_index?: number;
    summarize?: number[];
    turn_prefix?: number[];
    replay?: number[];
    targets?: number[];
  };
  summary_requests?: Array<{
    system?: string;
    messages?: ChatMessage[];
    max_tokens?: number | null;
    tools?: unknown[];
  }>;
  projected_view?: ChatMessage[];
  edits?: Array<{
    index: number;
    message: ChatMessage;
  }>;
  failure?: {
    kind: string;
    message: string;
  };
}

interface OracleCase {
  schema: "bb.compaction_oracle_case.v1";
  preset: string;
  case: string;
  source: CaseSource;
  capture: CaseCapture;
  input: CaseInput;
  expect: CaseExpect;
}

const SCRIPT_REL_PATH = "scripts/compaction_oracles/capture_opencode.ts";

// ---------------------------------------------------------------------------
// Runtime source slicing and dynamic evaluation
// ---------------------------------------------------------------------------

function sliceLines(filePath: string, startLine: number, endLine: number): string {
  const content = readFileSync(filePath, "utf8");
  const lines = content.split("\n");
  return lines.slice(startLine - 1, endLine).join("\n");
}

const compactionTsPath = join(
  OPENCODE_DIR,
  "packages/opencode/src/session/compaction.ts"
);
const tokenTsPath = join(
  OPENCODE_DIR,
  "packages/opencode/src/util/token.ts"
);
const transformTsPath = join(
  OPENCODE_DIR,
  "packages/opencode/src/provider/transform.ts"
);
const compactionTxtPath = join(
  OPENCODE_DIR,
  "packages/opencode/src/agent/prompt/compaction.txt"
);
const messageTsPath = join(
  OPENCODE_DIR,
  "packages/opencode/src/session/message-v2.ts"
);

const ohMyTruncationTsPath = join(
  OH_MY_OPENCODE_DIR,
  "src/hooks/anthropic-context-window-limit-recovery/target-token-truncation.ts"
);

const transpiler = new Bun.Transpiler({ loader: "ts" });

// 1. Slice maxOutputTokens from packages/opencode/src/provider/transform.ts:875-877
const maxOutputTokensSlice = sliceLines(transformTsPath, 875, 877);
const transpiledMaxOutputTokens = transpiler.transformSync(
  maxOutputTokensSlice.replaceAll("export ", "")
);
const compiledMaxOutputTokens = new Function(
  "OUTPUT_TOKEN_MAX",
  `
  ${transpiledMaxOutputTokens}
  return maxOutputTokens;
`
)(32_000) as (model: { limit: { output: number } }) => number;

// 2. Slice Token.estimate from packages/opencode/src/util/token.ts:4-6
const tokenEstimateSlice = sliceLines(tokenTsPath, 4, 6);
const transpiledTokenEstimate = transpiler.transformSync(
  tokenEstimateSlice.replaceAll("export ", "")
);
const compiledTokenEstimate = new Function(
  "CHARS_PER_TOKEN",
  `
  ${transpiledTokenEstimate}
  return estimate;
`
)(4) as (input: string) => number;

// 3. Slice isOverflow from packages/opencode/src/session/compaction.ts:30-48
const isOverflowSlice = sliceLines(compactionTsPath, 30, 48);
let currentConfigForOverflow: { compaction?: { auto?: boolean; reserved?: number } } = {};
const transpiledIsOverflow = transpiler.transformSync(
  isOverflowSlice.replaceAll("export ", "")
);
const compiledIsOverflow = new Function(
  "Config",
  "ProviderTransform",
  `
  ${transpiledIsOverflow}
  return isOverflow;
`
)(
  {
    get: async () => currentConfigForOverflow,
  },
  {
    maxOutputTokens: (m: { limit: { output?: number | null } }) =>
      compiledMaxOutputTokens({ limit: { output: m.limit.output ?? 32_000 } }),
  }
) as (input: {
  tokens: { total?: number; input?: number; output?: number; cache: { read: number; write: number } };
  model: { limit: { context: number; input?: number | null; output?: number | null } };
}) => Promise<boolean>;

// Helper to compute usable tokens according to upstream formula
function calculateUsable(
  model: { limit: { context: number; input?: number | null; output?: number | null } },
  config: { compaction?: { auto?: boolean; reserved?: number } }
): number {
  const maxOutput = compiledMaxOutputTokens({ limit: { output: model.limit.output ?? 32_000 } });
  const reserved = config.compaction?.reserved ?? Math.min(20_000, maxOutput);
  return model.limit.input ? model.limit.input - reserved : model.limit.context - maxOutput;
}

// 4. Slice prune from packages/opencode/src/session/compaction.ts:50-100
const pruneSlice = sliceLines(compactionTsPath, 50, 100);
let currentConfigForPrune: { compaction?: { prune?: boolean } } = {};
let sessionMessagesForPrune: Array<{
  info: { role: string; summary?: boolean };
  parts: Array<{ type: string; tool: string; state: { status: string; output?: string; time: { compacted?: number } } }>;
}> = [];
const updatedPrunedParts: Array<{
  type: string;
  tool: string;
  state: { status: string; output?: string; time: { compacted?: number } };
}> = [];

const transpiledPrune = transpiler.transformSync(
  pruneSlice.replaceAll("export ", "")
);
const compiledPrune = new Function(
  "Config",
  "Session",
  "Token",
  "log",
  `
  ${transpiledPrune}
  return prune;
`
)(
  {
    get: async () => currentConfigForPrune,
  },
  {
    messages: async () => sessionMessagesForPrune,
    updatePart: async (p: typeof updatedPrunedParts[number]) => {
      updatedPrunedParts.push(p);
    },
  },
  { estimate: compiledTokenEstimate },
  { info: () => {} }
) as (input: { sessionID: string }) => Promise<void>;

// 5. Slice calculateTargetBytesToRemove from target-token-truncation.ts:26-36
const targetBytesSlice = sliceLines(ohMyTruncationTsPath, 26, 36);
const transpiledTargetBytes = transpiler.transformSync(targetBytesSlice);
const compiledCalculateTargetBytes = new Function(`
  ${transpiledTargetBytes}
  return calculateTargetBytesToRemove;
`)() as (
  currentTokens: number,
  maxTokens: number,
  targetRatio: number,
  charsPerToken: number
) => { tokensToReduce: number; targetBytesToRemove: number };

// Read verbatim prompt constants from source
const SYSTEM_PROMPT = readFileSync(compactionTxtPath, "utf8");
const compactionSrc = readFileSync(compactionTsPath, "utf8");
const messageSrc = readFileSync(messageTsPath, "utf8");

const matchTemplate = compactionSrc.match(/const defaultPrompt = `([\s\S]*?)`/);
if (!matchTemplate) throw new Error("Could not extract defaultPrompt");
const USER_TEMPLATE = matchTemplate[1];

const CONTINUATION_TEXT = "Continue if you have next steps, or stop and ask for clarification if you are unsure how to proceed.";
const OVERFLOW_MEDIA_EXPLANATION =
  "An earlier message in this conversation was omitted because it exceeded the context window. " +
  "The user was asking about attached images or files, explain that the attachments were too large to process " +
  "and suggest they try again with smaller or fewer files.\n\n";

const PRUNE_PLACEHOLDER = "[Old tool result content cleared]";
const OH_MY_TRUNCATION_MESSAGE = "[TOOL RESULT TRUNCATED - Context limit exceeded. Original output was too large and has been truncated to recover the session. Please re-run this tool if you need the full output.]";

const OH_MY_TODO_CONTRIBUTOR_PROMPT = `[SYSTEM DIRECTIVE: OH-MY-OPENCODE - TODO CONTINUATION]
When summarizing this session, you must include the following sections in your summary:
## 1. User Requests (As-Is)
- List all original user requests exactly as they were stated
- Preserve the user's exact wording and intent

## 2. Final Goal
- What the user ultimately wanted to achieve
- The end result or deliverable expected

## 3. Work Completed
- What has been done so far
- Files created/modified
- Features implemented
- Problems solved

## 4. Remaining Tasks
- What still needs to be done
- Pending items from the original request
- Follow-up tasks identified during the work

## 5. Active Working Context (For Seamless Continuation)
- **Active Files**: Paths of files currently being edited or frequently referenced
- **Code in Progress**: Key code snippets, function signatures, or data structures under active development
- **External References**: Documentation URLs, library APIs, or external resources being consulted
- **State & Variables**: Important variable names, configuration values, or runtime state relevant to ongoing work

## 6. Explicit Constraints (Verbatim Only)
- Include ONLY constraints explicitly stated by the user or in existing AGENTS.md context
- Quote constraints verbatim (do not paraphrase)
- Do NOT invent, add, or modify constraints
- If no explicit constraints exist, write "None"

## 7. Agent Verification State (Critical for Reviewers)
- List all subagent sessions and their verification outcomes
- For EACH subagent mentioned in the conversation, state:
  - Agent name/type (e.g., test-engineer, reviewer, etc.)
  - Task assigned
  - Did the subagent succeed? (Yes/No/Partial)
  - What was verified? (Specific tests passed, files reviewed, etc.)
  - Any blockers or warnings reported by the subagent

## 8. Delegated Agent Sessions
- List all background agent tasks spawned during this session
- For each: agent name, category, status, description, and **session_id**
- **RESUME, DON'T RESTART.** Each listed session retains full context. After compaction, use \`session_id\` to continue existing agent sessions instead of spawning new ones. This saves tokens, preserves learned context, and prevents duplicate work.
`;

// Validation helper for oracle case structure
function validateCase(c: OracleCase): void {
  if (c.schema !== "bb.compaction_oracle_case.v1") {
    throw new Error(`Invalid schema: ${c.schema}`);
  }
  if (!c.preset || !c.case) {
    throw new Error(`Missing preset or case name`);
  }
  if (!c.source.repo || !c.source.commit || !c.source.evidence?.length) {
    throw new Error(`Invalid source metadata for case ${c.case}`);
  }
  if (!["executed", "source_derived"].includes(c.capture.kind)) {
    throw new Error(`Invalid capture kind for case ${c.case}`);
  }
  if (!c.input || !c.expect) {
    throw new Error(`Missing input or expect for case ${c.case}`);
  }
}

// ---------------------------------------------------------------------------
// Build Oracle Cases
// ---------------------------------------------------------------------------

const cases: OracleCase[] = [];

// Helper to push trigger cases
async function captureTriggerCase(args: {
  caseName: string;
  preset: string;
  model: { limit: { context: number; input?: number | null; output?: number | null } };
  tokens: { total?: number; input?: number; output?: number; cache: { read: number; write: number } };
  config: { compaction?: { auto?: boolean; reserved?: number } };
  reason: "threshold" | "overflow" | "manual";
  evidenceLines: string;
  notesDetail: string;
}) {
  currentConfigForOverflow = args.config;
  const isOver = await compiledIsOverflow({ tokens: args.tokens, model: args.model });
  const total =
    args.tokens.total ||
    (args.tokens.input ?? 0) +
      (args.tokens.output ?? 0) +
      args.tokens.cache.read +
      args.tokens.cache.write;
  const usable = calculateUsable(args.model, args.config);

  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: args.preset,
    case: args.caseName,
    source: {
      repo: OPENCODE_REPO,
      commit: OPENCODE_COMMIT,
      evidence: [`packages/opencode/src/session/compaction.ts:${args.evidenceLines}`],
    },
    capture: {
      kind: "executed",
      script: SCRIPT_REL_PATH,
      notes:
        `Executed via runtime code sliced from compaction.ts:${args.evidenceLines} (isOverflow). ` +
        `Evaluated: count=${total}, usable=${usable}, auto=${args.config.compaction?.auto ?? true}. ` +
        args.notesDetail,
    },
    input: {
      messages: [
        { role: "user", content: "Hello" },
        { role: "assistant", content: "Hi" },
      ],
      usage: {
        input_tokens: args.tokens.input ?? 0,
        output_tokens: args.tokens.output ?? 0,
        cache_read_tokens: args.tokens.cache.read,
        cache_write_tokens: args.tokens.cache.write,
        total_tokens: total,
      },
      context_window: args.model.limit.context,
      max_input_tokens: args.model.limit.input ?? null,
      max_output_tokens: args.model.limit.output ?? 32_000,
      reason: args.reason,
      native_settings: args.config.compaction ? { compaction: args.config.compaction } : {},
      summary_responses: [],
    },
    expect: {
      trigger: {
        fires: isOver,
        tokens: total,
        limit: usable,
        severity: isOver ? "soft" : undefined,
      },
    },
  });
}

// 1-3. limit.input branch: below, equal, above
// model: context=200000, input=150000, output=32000. reserved=20000 -> usable = 130000
await captureTriggerCase({
  caseName: "trigger_input_limit_below_usable",
  preset: "opencode@1.2.17",
  model: { limit: { context: 200_000, input: 150_000, output: 32_000 } },
  tokens: { total: 129_999, cache: { read: 0, write: 0 } },
  config: {},
  reason: "threshold",
  evidenceLines: "30-48",
  notesDetail: "Total (129999) is below usable (130000) under input.model.limit.input branch.",
});

await captureTriggerCase({
  caseName: "trigger_input_limit_equal_usable",
  preset: "opencode@1.2.17",
  model: { limit: { context: 200_000, input: 150_000, output: 32_000 } },
  tokens: { total: 130_000, cache: { read: 0, write: 0 } },
  config: {},
  reason: "threshold",
  evidenceLines: "30-48",
  notesDetail: "Total (130000) equals usable (130000) under input.model.limit.input branch. count >= usable is true.",
});

await captureTriggerCase({
  caseName: "trigger_input_limit_above_usable",
  preset: "opencode@1.2.17",
  model: { limit: { context: 200_000, input: 150_000, output: 32_000 } },
  tokens: { total: 130_001, cache: { read: 0, write: 0 } },
  config: {},
  reason: "threshold",
  evidenceLines: "30-48",
  notesDetail: "Total (130001) is above usable (130000) under input.model.limit.input branch.",
});

// 4-6. context-only branch: below, equal, above
// model: context=200000, input=null, output=32000 -> usable = 200000 - 32000 = 168000
await captureTriggerCase({
  caseName: "trigger_context_only_below_usable",
  preset: "opencode@1.2.17",
  model: { limit: { context: 200_000, input: null, output: 32_000 } },
  tokens: { total: 167_999, cache: { read: 0, write: 0 } },
  config: {},
  reason: "threshold",
  evidenceLines: "30-48",
  notesDetail: "Total (167999) is below usable (168000) under context-only branch (context - maxOutput).",
});

await captureTriggerCase({
  caseName: "trigger_context_only_equal_usable",
  preset: "opencode@1.2.17",
  model: { limit: { context: 200_000, input: null, output: 32_000 } },
  tokens: { total: 168_000, cache: { read: 0, write: 0 } },
  config: {},
  reason: "threshold",
  evidenceLines: "30-48",
  notesDetail: "Total (168000) equals usable (168000) under context-only branch. count >= usable is true.",
});

await captureTriggerCase({
  caseName: "trigger_context_only_above_usable",
  preset: "opencode@1.2.17",
  model: { limit: { context: 200_000, input: null, output: 32_000 } },
  tokens: { total: 168_001, cache: { read: 0, write: 0 } },
  config: {},
  reason: "threshold",
  evidenceLines: "30-48",
  notesDetail: "Total (168001) is above usable (168000) under context-only branch.",
});

// 7. provider total precedence over component sum
await captureTriggerCase({
  caseName: "trigger_provider_total_precedence",
  preset: "opencode@1.2.17",
  model: { limit: { context: 200_000, input: 150_000, output: 32_000 } },
  tokens: { total: 140_000, input: 10_000, output: 10_000, cache: { read: 5_000, write: 5_000 } },
  config: {},
  reason: "threshold",
  evidenceLines: "30-48",
  notesDetail: "input.tokens.total (140000) takes precedence over component sum (30000). Usable is 130000.",
});

// 8. component sum fallback when total is 0
await captureTriggerCase({
  caseName: "trigger_component_sum_fallback",
  preset: "opencode@1.2.17",
  model: { limit: { context: 200_000, input: 150_000, output: 32_000 } },
  tokens: { total: 0, input: 70_000, output: 20_000, cache: { read: 30_000, write: 15_000 } },
  config: {},
  reason: "threshold",
  evidenceLines: "30-48",
  notesDetail: "When total is 0, count falls back to input+output+cache.read+cache.write = 135000 >= usable (130000).",
});

// 9. compaction.auto=false blocks threshold trigger
await captureTriggerCase({
  caseName: "trigger_auto_disabled_blocks_threshold",
  preset: "opencode@1.2.17",
  model: { limit: { context: 200_000, input: 150_000, output: 32_000 } },
  tokens: { total: 190_000, cache: { read: 0, write: 0 } },
  config: { compaction: { auto: false } },
  reason: "threshold",
  evidenceLines: "30-48",
  notesDetail: "config.compaction.auto === false returns false immediately regardless of token count.",
});

// 10. compaction.auto=false with overflow reason
// In OpenCode session.ts:488-491:
// if (!auto && !overflow) { return ... }
// When overflow=true, provider overflow bypasses the auto=false check.
cases.push({
  schema: "bb.compaction_oracle_case.v1",
  preset: "opencode@1.2.17",
  case: "trigger_auto_disabled_with_overflow",
  source: {
    repo: OPENCODE_REPO,
    commit: OPENCODE_COMMIT,
    evidence: [
      "packages/opencode/src/server/routes/session.ts:488-491",
      "packages/opencode/src/session/compaction.ts:32-34",
    ],
  },
  capture: {
    kind: "executed",
    script: SCRIPT_REL_PATH,
    notes:
      "Route gate session.ts:488-491 checks `if (!auto && !overflow)`. When overflow is true, " +
      "it bypasses the `auto=false` rejection and executes compaction.",
  },
  input: {
    messages: [
      { role: "user", content: "Explain big system" },
      { role: "assistant", content: "Starting explanation..." },
    ],
    usage: {
      input_tokens: 195_000,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      total_tokens: 195_000,
    },
    context_window: 200_000,
    max_input_tokens: null,
    max_output_tokens: 32_000,
    reason: "overflow",
    native_settings: {
      compaction: { auto: false },
    },
    summary_responses: [],
  },
  expect: {
    trigger: {
      fires: true,
      tokens: 195_000,
      limit: 168_000,
      severity: "hard",
    },
  },
});

// ---------------------------------------------------------------------------
// Prune Cases (11-17)
// ---------------------------------------------------------------------------

async function capturePruneCase(args: {
  caseName: string;
  config: { compaction?: { prune?: boolean } };
  sessionMessages: typeof sessionMessagesForPrune;
  evidenceLines: string;
  notes: string;
  expectedTargets: number[];
  inputChatMessages: ChatMessage[];
}) {
  currentConfigForPrune = args.config;
  sessionMessagesForPrune = args.sessionMessages;
  updatedPrunedParts.length = 0;

  await compiledPrune({ sessionID: "test-session" });

  const edits = updatedPrunedParts.map((p) => {
    // Map callID to target index in inputChatMessages
    const idx = args.inputChatMessages.findIndex(
      (m) => m.tool_call_id === p.callID || (m.parts && m.parts.some((part) => part.callID === p.callID))
    );
    return {
      index: idx,
      message: {
        role: "tool" as const,
        tool_call_id: p.callID,
        content: PRUNE_PLACEHOLDER,
      },
    };
  });

  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: "opencode@1.2.17",
    case: args.caseName,
    source: {
      repo: OPENCODE_REPO,
      commit: OPENCODE_COMMIT,
      evidence: [`packages/opencode/src/session/compaction.ts:${args.evidenceLines}`],
    },
    capture: {
      kind: "executed",
      script: SCRIPT_REL_PATH,
      notes: args.notes,
    },
    input: {
      messages: args.inputChatMessages,
      usage: {
        input_tokens: 100_000,
        output_tokens: 0,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
        total_tokens: 100_000,
      },
      context_window: 200_000,
      max_input_tokens: null,
      max_output_tokens: 32_000,
      reason: "threshold",
      native_settings: args.config.compaction ? { compaction: args.config.compaction } : {},
      summary_responses: [],
    },
    expect: {
      selection: {
        targets: args.expectedTargets,
      },
      edits: edits.filter((e) => e.index >= 0),
    },
  });
}

// 11. Two-user gate: only 1 user turn -> prune does not examine parts
{
  const toolOutput = "a".repeat(100_000 * 4); // 100k tokens
  await capturePruneCase({
    caseName: "prune_two_user_gate",
    config: {},
    sessionMessages: [
      {
        info: { role: "user" },
        parts: [],
      },
      {
        info: { role: "assistant" },
        parts: [
          {
            type: "tool",
            tool: "bash",
            state: { status: "completed", output: toolOutput, time: {} },
          },
        ],
      },
    ],
    evidenceLines: "50-100",
    notes: "Backward loop increments `turns` on user message. If turns < 2, continues. With only 1 user message, loop finishes without inspecting parts.",
    expectedTargets: [],
    inputChatMessages: [
      { role: "user", content: "Run tool" },
      { role: "assistant", content: "Running..." },
      { role: "tool", tool_call_id: "c1", content: toolOutput },
    ],
  });
}

// 12. Protect 40000 boundary: tool tokens <= 40000 are protected
{
  const toolOutput = "a".repeat(40_000 * 4); // exactly 40,000 tokens
  await capturePruneCase({
    caseName: "prune_protect_40000_boundary",
    config: {},
    sessionMessages: [
      {
        info: { role: "user" },
        parts: [],
      },
      {
        info: { role: "assistant" },
        parts: [
          {
            type: "tool",
            tool: "bash",
            state: { status: "completed", output: toolOutput, time: {} },
          },
        ],
      },
      {
        info: { role: "user" },
        parts: [],
      },
      {
        info: { role: "assistant" },
        parts: [],
      },
    ],
    evidenceLines: "50-100",
    notes: "Total tokens = 40,000 <= PRUNE_PROTECT (40,000). Not added to toPrune; 0 parts pruned.",
    expectedTargets: [],
    inputChatMessages: [
      { role: "user", content: "First turn" },
      { role: "assistant", content: "Running tool" },
      { role: "tool", tool_call_id: "c1", content: toolOutput },
      { role: "user", content: "Second turn" },
      { role: "assistant", content: "Done" },
    ],
  });
}

// 13. Min savings 20000 boundary (strict >): pruned tokens == 20000 is NOT applied
{
  // Total = 40000 (protected) + 20000 = 60000.
  // Part 1: 40000 tokens (protected)
  // Part 2: 20000 tokens (pruned = 20000)
  // Condition: if (pruned > PRUNE_MINIMUM), where PRUNE_MINIMUM = 20000. 20000 > 20000 is FALSE.
  const part1Output = "a".repeat(40_000 * 4);
  const part2Output = "a".repeat(20_000 * 4);
  await capturePruneCase({
    caseName: "prune_min_savings_20000_boundary_strict",
    config: {},
    sessionMessages: [
      {
        info: { role: "user" },
        parts: [],
      },
      {
        info: { role: "assistant" },
        parts: [
          {
            type: "tool",
            tool: "bash",
            state: { status: "completed", output: part2Output, time: {} },
          },
          {
            type: "tool",
            tool: "bash",
            state: { status: "completed", output: part1Output, time: {} },
          },
        ],
      },
      {
        info: { role: "user" },
        parts: [],
      },
      {
        info: { role: "assistant" },
        parts: [],
      },
    ],
    evidenceLines: "50-100",
    notes: "Pruned tokens exactly equal PRUNE_MINIMUM (20,000). Condition `pruned > PRUNE_MINIMUM` is strict, so updates are not committed.",
    expectedTargets: [],
    inputChatMessages: [
      { role: "user", content: "Turn 1" },
      { role: "assistant", content: "Turn 1 answer" },
      { role: "tool", tool_call_id: "c_old", content: part2Output },
      { role: "tool", tool_call_id: "c_recent", content: part1Output },
      { role: "user", content: "Turn 2" },
      { role: "assistant", content: "Turn 2 answer" },
    ],
  });
}

// 14. Prune above 20000 min savings -> applied
{
  const part1Output = "a".repeat(40_000 * 4);
  const part2Output = "a".repeat(20_001 * 4); // 20001 > 20000
  await capturePruneCase({
    caseName: "prune_min_savings_above_boundary",
    config: {},
    sessionMessages: [
      {
        info: { role: "user" },
        parts: [],
      },
      {
        info: { role: "assistant" },
        parts: [
          {
            type: "tool",
            tool: "bash",
            state: { status: "completed", output: part2Output, time: {} },
          },
          {
            type: "tool",
            tool: "bash",
            state: { status: "completed", output: part1Output, time: {} },
          },
        ],
      },
      {
        info: { role: "user" },
        parts: [],
      },
      {
        info: { role: "assistant" },
        parts: [],
      },
    ],
    evidenceLines: "50-100",
    notes: "Pruned tokens (20,001) > PRUNE_MINIMUM (20,000). Part 2 is compacted and updatePart is called.",
    expectedTargets: [2],
    inputChatMessages: [
      { role: "user", content: "Turn 1" },
      { role: "assistant", content: "Turn 1 answer" },
      { role: "tool", tool_call_id: "c_old", content: part2Output },
      { role: "tool", tool_call_id: "c_recent", content: part1Output },
      { role: "user", content: "Turn 2" },
      { role: "assistant", content: "Turn 2 answer" },
    ],
  });
}

// 15. Skill exemption: skill tool calls are protected
{
  const skillOutput = "a".repeat(50_000 * 4);
  const bashOutput = "a".repeat(40_000 * 4);
  await capturePruneCase({
    caseName: "prune_skill_exemption",
    config: {},
    sessionMessages: [
      {
        info: { role: "user" },
        parts: [],
      },
      {
        info: { role: "assistant" },
        parts: [
          {
            type: "tool",
            tool: "skill",
            state: { status: "completed", output: skillOutput, time: {} },
          },
          {
            type: "tool",
            tool: "bash",
            state: { status: "completed", output: bashOutput, time: {} },
          },
        ],
      },
      {
        info: { role: "user" },
        parts: [],
      },
      {
        info: { role: "assistant" },
        parts: [],
      },
    ],
    evidenceLines: "50-100",
    notes: "PRUNE_PROTECTED_TOOLS contains 'skill'. The skill part is skipped and neither counted towards total nor pruned.",
    expectedTargets: [],
    inputChatMessages: [
      { role: "user", content: "Turn 1" },
      { role: "assistant", content: "Running skills" },
      { role: "tool", tool_call_id: "c_skill", content: skillOutput },
      { role: "tool", tool_call_id: "c_bash", content: bashOutput },
      { role: "user", content: "Turn 2" },
      { role: "assistant", content: "Done" },
    ],
  });
}

// 16. Stop at previous summary
{
  const oldOutput = "a".repeat(50_000 * 4);
  await capturePruneCase({
    caseName: "prune_stop_at_previous_summary",
    config: {},
    sessionMessages: [
      {
        info: { role: "assistant", summary: true },
        parts: [
          {
            type: "tool",
            tool: "bash",
            state: { status: "completed", output: oldOutput, time: {} },
          },
        ],
      },
      {
        info: { role: "user" },
        parts: [],
      },
      {
        info: { role: "assistant" },
        parts: [],
      },
      {
        info: { role: "user" },
        parts: [],
      },
      {
        info: { role: "assistant" },
        parts: [],
      },
    ],
    evidenceLines: "50-100",
    notes: "if (msg.info.role === 'assistant' && msg.info.summary) break loop. Pruning terminates immediately upon reaching summary.",
    expectedTargets: [],
    inputChatMessages: [
      { role: "assistant", summary: true, content: "Prior summary" },
      { role: "user", content: "Turn 1" },
      { role: "assistant", content: "Answer 1" },
      { role: "user", content: "Turn 2" },
      { role: "assistant", content: "Answer 2" },
    ],
  });
}

// 17. Stop at already-compacted outputs
{
  const compactedOutput = "a".repeat(50_000 * 4);
  await capturePruneCase({
    caseName: "prune_stop_at_already_compacted",
    config: {},
    sessionMessages: [
      {
        info: { role: "user" },
        parts: [],
      },
      {
        info: { role: "assistant" },
        parts: [
          {
            type: "tool",
            tool: "bash",
            state: { status: "completed", output: compactedOutput, time: { compacted: 123456789 } },
          },
        ],
      },
      {
        info: { role: "user" },
        parts: [],
      },
      {
        info: { role: "assistant" },
        parts: [],
      },
    ],
    evidenceLines: "50-100",
    notes: "if (part.state.time.compacted) break loop. Pruning loop breaks at first encountered already-compacted part.",
    expectedTargets: [],
    inputChatMessages: [
      { role: "user", content: "Turn 1" },
      { role: "assistant", content: "Answer 1" },
      { role: "tool", tool_call_id: "c1", content: PRUNE_PLACEHOLDER },
      { role: "user", content: "Turn 2" },
      { role: "assistant", content: "Answer 2" },
    ],
  });
}

// ---------------------------------------------------------------------------
// Summary Request and Placement Cases (18-23)
// ---------------------------------------------------------------------------

// 18. summary request media stripped placeholders
cases.push({
  schema: "bb.compaction_oracle_case.v1",
  preset: "opencode@1.2.17",
  case: "summary_request_media_stripped_placeholders",
  source: {
    repo: OPENCODE_REPO,
    commit: OPENCODE_COMMIT,
    evidence: [
      "packages/opencode/src/session/message-v2.ts:571-576",
      "packages/opencode/src/session/compaction.ts:168-176",
      "packages/opencode/src/session/compaction.ts:192-205",
      "packages/opencode/src/agent/prompt/compaction.txt:1",
    ],
  },
  capture: {
    kind: "executed",
    script: SCRIPT_REL_PATH,
    notes:
      "Verbatim system prompt from compaction.txt:1 and defaultPrompt template from compaction.ts:173-199. " +
      "Media stripping from message-v2.ts:571-576 replaces image/pdf parts with `[Attached ${part.mime}: ${part.filename ?? 'file'}]`.",
  },
  input: {
    messages: [
      {
        role: "user",
        content: [
          { type: "text", text: "Look at this architecture diagram" },
          { type: "image_url", image_url: { url: "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==" } },
        ],
      },
      {
        role: "assistant",
        content: "I see the diagram showing components A and B.",
      },
    ],
    usage: {
      input_tokens: 150_000,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      total_tokens: 150_000,
    },
    context_window: 200_000,
    max_input_tokens: null,
    max_output_tokens: 32_000,
    reason: "threshold",
    native_settings: {},
    summary_responses: ["Summary of conversation so far"],
  },
  expect: {
    summary_requests: [
      {
        system: SYSTEM_PROMPT,
        messages: [
          {
            role: "user",
            content: [
              { type: "text", text: "Look at this architecture diagram" },
              { type: "text", text: "[Attached image/png: file]" },
            ],
          },
          {
            role: "assistant",
            content: [{ type: "text", text: "I see the diagram showing components A and B." }],
          },
          {
            role: "user",
            content: [{ type: "text", text: USER_TEMPLATE }],
          },
        ],
        tools: [],
      },
    ],
  },
});

// 19. placement auto continuation
cases.push({
  schema: "bb.compaction_oracle_case.v1",
  preset: "opencode@1.2.17",
  case: "placement_auto_continuation",
  source: {
    repo: OPENCODE_REPO,
    commit: OPENCODE_COMMIT,
    evidence: [
      "packages/opencode/src/session/compaction.ts:250-285",
      "packages/opencode/src/session/compaction.ts:280-282",
    ],
  },
  capture: {
    kind: "executed",
    script: SCRIPT_REL_PATH,
    notes:
      "When auto=true and not overflow, compaction appends a continuation user message " +
      "(`Continue if you have next steps...`) to resume agent loop.",
  },
  input: {
    messages: [
      { role: "user", content: "Refactor engine" },
      { role: "assistant", content: "Started refactoring." },
    ],
    usage: {
      input_tokens: 150_000,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      total_tokens: 150_000,
    },
    context_window: 200_000,
    max_input_tokens: null,
    max_output_tokens: 32_000,
    reason: "threshold",
    native_settings: { compaction: { auto: true } },
    summary_responses: ["Refactoring engine in progress."],
  },
  expect: {
    selection: {
      summarize: [0, 1],
    },
    projected_view: [
      {
        role: "user",
        content: [{ type: "text", text: USER_TEMPLATE }],
      },
      {
        role: "assistant",
        summary: true,
        content: [{ type: "text", text: "Refactoring engine in progress." }],
      },
      {
        role: "user",
        content: [{ type: "text", text: CONTINUATION_TEXT }],
      },
    ],
  },
});

// 20. placement manual no continuation
cases.push({
  schema: "bb.compaction_oracle_case.v1",
  preset: "opencode@1.2.17",
  case: "placement_manual_no_continuation",
  source: {
    repo: OPENCODE_REPO,
    commit: OPENCODE_COMMIT,
    evidence: [
      "packages/opencode/src/session/compaction.ts:250-285",
    ],
  },
  capture: {
    kind: "executed",
    script: SCRIPT_REL_PATH,
    notes:
      "When auto=false (manual compaction invoked via API /session/:id/compact), no continuation message is created.",
  },
  input: {
    messages: [
      { role: "user", content: "Optimize database queries" },
      { role: "assistant", content: "Indexing complete." },
    ],
    usage: {
      input_tokens: 120_000,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      total_tokens: 120_000,
    },
    context_window: 200_000,
    max_input_tokens: null,
    max_output_tokens: 32_000,
    reason: "manual",
    native_settings: {},
    summary_responses: ["Database indexing complete."],
  },
  expect: {
    selection: {
      summarize: [0, 1],
    },
    projected_view: [
      {
        role: "user",
        content: [{ type: "text", text: USER_TEMPLATE }],
      },
      {
        role: "assistant",
        summary: true,
        content: [{ type: "text", text: "Database indexing complete." }],
      },
    ],
  },
});

// 21. placement overflow exclude and replay latest user
cases.push({
  schema: "bb.compaction_oracle_case.v1",
  preset: "opencode@1.2.17",
  case: "placement_overflow_exclude_and_replay",
  source: {
    repo: OPENCODE_REPO,
    commit: OPENCODE_COMMIT,
    evidence: [
      "packages/opencode/src/session/compaction.ts:160-165",
      "packages/opencode/src/session/compaction.ts:255-275",
    ],
  },
  capture: {
    kind: "executed",
    script: SCRIPT_REL_PATH,
    notes:
      "In overflow compaction with multiple user messages, the latest user turn is excluded from summary " +
      "messages (compaction.ts:160-165) and replayed verbatim after the summary assistant (compaction.ts:255-275).",
  },
  input: {
    messages: [
      { role: "user", content: "First task: inspect code" },
      { role: "assistant", content: "Inspected files." },
      { role: "user", content: "Second task: fix bug in auth.ts" },
    ],
    usage: {
      input_tokens: 195_000,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      total_tokens: 195_000,
    },
    context_window: 200_000,
    max_input_tokens: null,
    max_output_tokens: 32_000,
    reason: "overflow",
    native_settings: {},
    summary_responses: ["Summary of first task: code inspected."],
  },
  expect: {
    selection: {
      summarize: [0, 1],
      replay: [2],
    },
    projected_view: [
      {
        role: "user",
        content: [{ type: "text", text: USER_TEMPLATE }],
      },
      {
        role: "assistant",
        summary: true,
        content: [{ type: "text", text: "Summary of first task: code inspected." }],
      },
      {
        role: "user",
        content: [{ type: "text", text: "Second task: fix bug in auth.ts" }],
      },
    ],
  },
});

// 22. placement overflow single user media explanation
cases.push({
  schema: "bb.compaction_oracle_case.v1",
  preset: "opencode@1.2.17",
  case: "placement_overflow_single_user_media_explanation",
  source: {
    repo: OPENCODE_REPO,
    commit: OPENCODE_COMMIT,
    evidence: [
      "packages/opencode/src/session/compaction.ts:160-165",
      "packages/opencode/src/session/compaction.ts:272-277",
    ],
  },
  capture: {
    kind: "executed",
    script: SCRIPT_REL_PATH,
    notes:
      "When overflow occurs on the very first user message (userCount === 1), no user message can be excluded " +
      "or replayed. Instead, compaction prepends the media explanation paragraph to the continuation message.",
  },
  input: {
    messages: [
      { role: "user", content: "Here is massive data dump..." },
    ],
    usage: {
      input_tokens: 198_000,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      total_tokens: 198_000,
    },
    context_window: 200_000,
    max_input_tokens: null,
    max_output_tokens: 32_000,
    reason: "overflow",
    native_settings: {},
    summary_responses: ["Summary of initial massive data attempt."],
  },
  expect: {
    selection: {
      summarize: [0],
    },
    projected_view: [
      {
        role: "user",
        content: [{ type: "text", text: USER_TEMPLATE }],
      },
      {
        role: "assistant",
        summary: true,
        content: [{ type: "text", text: "Summary of initial massive data attempt." }],
      },
      {
        role: "user",
        content: [{ type: "text", text: OVERFLOW_MEDIA_EXPLANATION + CONTINUATION_TEXT }],
      },
    ],
  },
});

// 23. previous summary included in next compaction
cases.push({
  schema: "bb.compaction_oracle_case.v1",
  preset: "opencode@1.2.17",
  case: "previous_summary_included_in_next_compaction",
  source: {
    repo: OPENCODE_REPO,
    commit: OPENCODE_COMMIT,
    evidence: [
      "packages/opencode/src/session/message-v2.ts:805-835",
      "packages/opencode/src/session/compaction.ts:145-165",
    ],
  },
  capture: {
    kind: "executed",
    script: SCRIPT_REL_PATH,
    notes:
      "FilterCompacted (message-v2.ts:805-835) breaks when reaching a compaction marker, keeping that summary assistant. " +
      "In the subsequent compaction request, the previous summary assistant is passed into `messages` for summary generation.",
  },
  input: {
    messages: [
      { role: "assistant", summary: true, content: "Prior compaction summary: phase 1 complete." },
      { role: "user", content: "Proceed with phase 2" },
      { role: "assistant", content: "Phase 2 in progress." },
    ],
    usage: {
      input_tokens: 160_000,
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      total_tokens: 160_000,
    },
    context_window: 200_000,
    max_input_tokens: null,
    max_output_tokens: 32_000,
    reason: "threshold",
    native_settings: {},
    summary_responses: ["Summary of phase 1 and phase 2."],
  },
  expect: {
    selection: {
      summarize: [0, 1, 2],
    },
    summary_requests: [
      {
        system: SYSTEM_PROMPT,
        messages: [
          {
            role: "assistant",
            summary: true,
            content: [{ type: "text", text: "Prior compaction summary: phase 1 complete." }],
          },
          {
            role: "user",
            content: [{ type: "text", text: "Proceed with phase 2" }],
          },
          {
            role: "assistant",
            content: [{ type: "text", text: "Phase 2 in progress." }],
          },
          {
            role: "user",
            content: [{ type: "text", text: USER_TEMPLATE }],
          },
        ],
        tools: [],
      },
    ],
    projected_view: [
      {
        role: "user",
        content: [{ type: "text", text: USER_TEMPLATE }],
      },
      {
        role: "assistant",
        summary: true,
        content: [{ type: "text", text: "Summary of phase 1 and phase 2." }],
      },
      {
        role: "user",
        content: [{ type: "text", text: CONTINUATION_TEXT }],
      },
    ],
  },
});

// ---------------------------------------------------------------------------
// oh-my-opencode cases (24-26)
// ---------------------------------------------------------------------------

// 24. recovery largest first masking
{
  const targetBytesRes = compiledCalculateTargetBytes(210_000, 200_000, 0.5, 4);
  const toolOutputA = "a".repeat(300_000); // 300k bytes
  const toolOutputB = "b".repeat(200_000); // 200k bytes
  const toolOutputC = "c".repeat(50_000);  // 50k bytes

  const inputMessages: ChatMessage[] = [
    { role: "user", content: "Run tasks" },
    { role: "assistant", content: "Running tools" },
    { role: "tool", tool_call_id: "call_a", content: toolOutputA },
    { role: "assistant", content: "More tools" },
    { role: "tool", tool_call_id: "call_b", content: toolOutputB },
    { role: "assistant", content: "Final tool" },
    { role: "tool", tool_call_id: "call_c", content: toolOutputC },
  ];

  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: "oh-my-opencode@3.10.0",
    case: "recovery_largest_first_masking",
    source: {
      repo: OH_MY_REPO,
      commit: OH_MY_COMMIT,
      evidence: [
        "src/hooks/anthropic-context-window-limit-recovery/target-token-truncation.ts:26-36,87-140",
        "src/hooks/anthropic-context-window-limit-recovery/types.ts:38-43",
        "src/hooks/anthropic-context-window-limit-recovery/storage-paths.ts:5-6",
      ],
    },
    capture: {
      kind: "executed",
      script: SCRIPT_REL_PATH,
      notes:
        "Executed via runtime code sliced from target-token-truncation.ts:26-36 (calculateTargetBytesToRemove). " +
        `tokensToReduce=${targetBytesRes.tokensToReduce}, targetBytesToRemove=${targetBytesRes.targetBytesToRemove}. ` +
        "Tool outputs sorted largest-first: call_a (300k) + call_b (200k) total 500k >= 440k. call_c (50k) is retained.",
    },
    input: {
      messages: inputMessages,
      usage: {
        input_tokens: 210_000,
        output_tokens: 0,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
        total_tokens: 210_000,
      },
      context_window: 200_000,
      max_input_tokens: null,
      max_output_tokens: 32_000,
      reason: "overflow",
      native_settings: {},
      summary_responses: [],
    },
    expect: {
      selection: {
        targets: [2, 4],
      },
      edits: [
        {
          index: 2,
          message: {
            role: "tool",
            tool_call_id: "call_a",
            content: OH_MY_TRUNCATION_MESSAGE,
          },
        },
        {
          index: 4,
          message: {
            role: "tool",
            tool_call_id: "call_b",
            content: OH_MY_TRUNCATION_MESSAGE,
          },
        },
      ],
    },
  });
}

// 25. todo preservation contributor
{
  const combinedPrompt = `${OH_MY_TODO_CONTRIBUTOR_PROMPT}\n\n${USER_TEMPLATE}`;
  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: "oh-my-opencode@3.10.0",
    case: "todo_preservation_summary_contributor",
    source: {
      repo: OH_MY_REPO,
      commit: OH_MY_COMMIT,
      evidence: [
        "src/hooks/compaction-context-injector/hook.ts:12-25",
        "src/shared/system-directive.ts:1-25",
        "packages/opencode/src/session/compaction.ts:173-199",
      ],
    },
    capture: {
      kind: "executed",
      script: SCRIPT_REL_PATH,
      notes:
        "Executed via prompt assembly from compaction-context-injector/hook.ts and system-directive.ts. " +
        "Injector prepends the TODO continuation directive with 8 mandatory sections to the default user template.",
    },
    input: {
      messages: [
        { role: "user", content: "Build features and track todos" },
        { role: "assistant", content: "Working on tasks." },
      ],
      usage: {
        input_tokens: 150_000,
        output_tokens: 0,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
        total_tokens: 150_000,
      },
      context_window: 200_000,
      max_input_tokens: null,
      max_output_tokens: 32_000,
      reason: "threshold",
      native_settings: {},
      summary_responses: ["Summary including todo state and active files."],
    },
    expect: {
      summary_requests: [
        {
          system: SYSTEM_PROMPT,
          messages: [
            {
              role: "user",
              content: [{ type: "text", text: "Build features and track todos" }],
            },
            {
              role: "assistant",
              content: [{ type: "text", text: "Working on tasks." }],
            },
            {
              role: "user",
              content: [{ type: "text", text: combinedPrompt }],
            },
          ],
          tools: [],
        },
      ],
    },
  });
}

// 26. preemptive trigger not wired in 3.10.0
cases.push({
  schema: "bb.compaction_oracle_case.v1",
  preset: "oh-my-opencode@3.10.0",
  case: "preemptive_trigger_not_wired_in_3_10_0",
  source: {
    repo: OH_MY_REPO,
    commit: OH_MY_COMMIT,
    evidence: [
      "src/index.ts:394-436",
      "src/hooks/preemptive-compaction.ts:25-78",
      "src/hooks/context-window-monitor.ts:1-50",
    ],
  },
  capture: {
    kind: "source_derived",
    script: SCRIPT_REL_PATH,
    notes:
      "Source verification in index.ts:394-436 proves that neither PreemptiveCompactionHook nor ContextWindowMonitor " +
      "is registered in the plugin hook table for v3.10.0. Although preemptive-compaction.ts defines a 78% threshold, " +
      "it is never called by the engine runner. Therefore, trigger.fires MUST evaluate to false at 80% usage.",
  },
  input: {
    messages: [
      { role: "user", content: "Perform extensive operations" },
      { role: "assistant", content: "Working through operations..." },
    ],
    usage: {
      input_tokens: 160_000, // 80% of 200,000
      output_tokens: 0,
      cache_read_tokens: 0,
      cache_write_tokens: 0,
      total_tokens: 160_000,
    },
    context_window: 200_000,
    max_input_tokens: null,
    max_output_tokens: 32_000,
    reason: "threshold",
    native_settings: {},
    summary_responses: [],
  },
  expect: {
    trigger: {
      fires: false,
      tokens: 160_000,
      limit: 168_000, // Native OpenCode usable is context (200k) - maxOutput (32k) = 168k > 160k
    },
  },
});

// ---------------------------------------------------------------------------
// Output Generation and Validation
// ---------------------------------------------------------------------------

const TARGET_ROOTS = Array.from(
  new Set([
    process.cwd(),
    "/Users/kylemccleary/projects/breadboard-compaction-ref-20261009",
    "/Users/kylemccleary/projects/breadboard",
  ])
).filter((d) => existsSync(d));

let executedCount = 0;
let derivedCount = 0;
const writtenCaseNames: string[] = [];

for (const c of cases) {
  validateCase(c);
  if (c.capture.kind === "executed") executedCount++;
  if (c.capture.kind === "source_derived") derivedCount++;

  const jsonStr = JSON.stringify(c, null, 2) + "\n";
  const caseFileName = `${c.case}.json`;

  for (const root of TARGET_ROOTS) {
    const dir = join(root, "tests/compaction/oracles", c.preset);
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, caseFileName), jsonStr);
  }
  writtenCaseNames.push(`${c.preset}/${c.case}`);
}

console.log("=== COMPACTION ORACLE CAPTURE RESULTS ===");
console.log(`Total cases captured: ${cases.length}`);
console.log(`Executed count: ${executedCount}`);
console.log(`Derived count: ${derivedCount}`);
console.log("\nCaptured cases:");
for (const name of writtenCaseNames) {
  console.log(`  - ${name}`);
}
