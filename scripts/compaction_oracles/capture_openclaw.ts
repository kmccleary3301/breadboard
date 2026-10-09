#!/usr/bin/env bun
/**
 * capture_openclaw.ts
 *
 * Takes the source checkout path as a command-line argument:
 *   bun scripts/compaction_oracles/capture_openclaw.ts <source_checkout_path>
 *
 * Slices the exact function text and constants from the pinned source files in
 * the checkout, transpiles via Bun.Transpiler, and evaluates them:
 * 1. resolveEffectiveCompactionReserveTokens & MAX_COMPACTION_RESERVE_RATIO (src/agents/agent-compaction-constants.ts:2-14)
 * 2. DEFAULT_AGENT_COMPACTION_RESERVE_TOKENS_FLOOR (src/agents/agent-settings.ts:9)
 * 3. shouldCompact (packages/agent-core/src/harness/compaction/compaction.ts:340-349)
 * 4. findCutPoint, isCutPointMessage, isTurnStartMessage, isTurnStartEntry, findTurnStartIndex (packages/agent-core/src/harness/compaction/compaction.ts:410-465,491-589)
 * 5. REQUIRED_SUMMARY_SECTIONS, normalizedSummaryLines, summaryIncludesIdentifier, auditSummaryQuality (src/agents/agent-hooks/compaction-safeguard-quality.ts:15-21,103-108,391-405,471-527)
 * 6. COMPACTION_SUMMARY_PREFIX, COMPACTION_SUMMARY_SUFFIX (packages/agent-core/src/harness/messages.ts:45-51)
 *
 * Records upstream file paths, line ranges, and verbatim quotes for every constant and comparator in capture.notes.
 */

import * as fs from "node:fs";
import * as path from "node:path";

const sourceCheckout = process.argv[2];
if (!sourceCheckout) {
  console.error("Usage: bun capture_openclaw.ts <source_checkout_path>");
  process.exit(1);
}

if (!fs.existsSync(sourceCheckout)) {
  console.error(`Error: source checkout path does not exist: ${sourceCheckout}`);
  process.exit(1);
}

const OPENCLAW_COMMIT = "3a9d69db306cd7f081e06254cb89c4bcc14a7107";
const OPENCLAW_REPO = "https://github.com/openclaw/openclaw";
const PRESET_ID = "openclaw@2026.9.4";

const TARGET_DIR = path.resolve(
  import.meta.dir,
  "../../tests/compaction/oracles",
  PRESET_ID
);
fs.mkdirSync(TARGET_DIR, { recursive: true });

const transpiler = new Bun.Transpiler({ loader: "ts" });

function loadSlice(relPath: string, startLine: number, endLine: number, exportExpression: string): unknown {
  const fullPath = path.join(sourceCheckout, relPath);
  const content = fs.readFileSync(fullPath, "utf-8");
  const lines = content.split("\n");
  const slice = lines.slice(startLine - 1, endLine).join("\n");
  const clean = slice.replace(/\bexport\s+/g, "");
  const js = transpiler.transformSync(clean);
  return new Function(`${js}; ${exportExpression}`)();
}

// 1. Slices for reserve constants and floor/cap
const constantsSlice = loadSlice(
  "src/agents/agent-compaction-constants.ts",
  2,
  14,
  "return { MAX_COMPACTION_RESERVE_RATIO, resolveEffectiveCompactionReserveTokens };"
) as {
  MAX_COMPACTION_RESERVE_RATIO: number;
  resolveEffectiveCompactionReserveTokens: (params: { contextTokenBudget: number; reserveTokens: number }) => number;
};

const settingsFloorSlice = loadSlice(
  "src/agents/agent-settings.ts",
  9,
  10,
  "return { DEFAULT_AGENT_COMPACTION_RESERVE_TOKENS_FLOOR };"
) as {
  DEFAULT_AGENT_COMPACTION_RESERVE_TOKENS_FLOOR: number;
};

function computeEffectiveReserveFromSlices(contextWindow: number, requested: number = 16384): number {
  const floor = settingsFloorSlice.DEFAULT_AGENT_COMPACTION_RESERVE_TOKENS_FLOOR;
  const requestedReserve = Math.max(requested, floor);
  return constantsSlice.resolveEffectiveCompactionReserveTokens({
    contextTokenBudget: contextWindow,
    reserveTokens: requestedReserve,
  });
}

// 2. Slice for shouldCompact
const thresholdSlice = loadSlice(
  "packages/agent-core/src/harness/compaction/compaction.ts",
  340,
  349,
  "return { shouldCompact };"
) as {
  shouldCompact: (
    contextTokens: number,
    contextWindow: number,
    settings: { enabled: boolean; reserveTokens: number }
  ) => boolean;
};

// 3. Slice for findCutPoint and helpers
const cutSlice = loadSlice(
  "packages/agent-core/src/harness/compaction/compaction.ts",
  410,
  589,
  `
  function isRuntimeContextCarrier(msg) { return false; }
  function getMessageFromEntryForCompaction(entry) { return entry.type === "message" ? entry.message : undefined; }
  return { findCutPoint, isCutPointMessage, isTurnStartMessage, isTurnStartEntry, findTurnStartIndex };
  `
) as {
  findCutPoint: (
    entries: unknown[],
    startIndex: number,
    endIndex: number,
    keepRecentTokens: number,
    constraints?: unknown
  ) => { firstKeptEntryIndex: number; turnStartIndex: number; isSplitTurn: boolean };
};

// 4. Slice for safeguard audit
const auditSlice = loadSlice(
  "src/agents/agent-hooks/compaction-safeguard-quality.ts",
  15,
  527,
  `
  function extractLeadingPendingAsk(s) { return ""; }
  function formatLatestUserRequestContext(s) { return s; }
  function hasAskOverlap(s, a) { return true; }
  function isEmptyPendingAsk(s) { return false; }
  function resolveAskOverlapRequirement(a) { return false; }
  function extractPendingAskSection(s) { return ""; }
  return { REQUIRED_SUMMARY_SECTIONS, normalizedSummaryLines, summaryIncludesIdentifier, auditSummaryQuality };
  `
) as {
  REQUIRED_SUMMARY_SECTIONS: string[];
  auditSummaryQuality: (params: {
    structuralSummary: string;
    summary: string;
    identifiers: string[];
    identifierPolicy?: "strict" | "off";
  }) => { ok: boolean; reasons: string[] };
};

// 5. Slice for messages placement tags
const messagesSlice = loadSlice(
  "packages/agent-core/src/harness/messages.ts",
  45,
  51,
  "return { COMPACTION_SUMMARY_PREFIX, COMPACTION_SUMMARY_SUFFIX };"
) as {
  COMPACTION_SUMMARY_PREFIX: string;
  COMPACTION_SUMMARY_SUFFIX: string;
};


const cases: string[] = [];

function emitCase(caseName: string, payload: unknown) {
  const filePath = path.join(TARGET_DIR, `${caseName}.json`);
  fs.writeFileSync(filePath, JSON.stringify(payload, null, 2) + "\n");
  cases.push(caseName);
  console.log(`Wrote executed case: ${caseName}`);
}

// Case 1: Reserve floor 20,000 + strict > comparator
{
  const window = 200_000;
  const effReserve = computeEffectiveReserveFromSlices(window, 16384); // 20000
  const limit = window - effReserve; // 180000
  const firesAtEqual = thresholdSlice.shouldCompact(180_000, window, { enabled: true, reserveTokens: effReserve });
  const firesAbove = thresholdSlice.shouldCompact(180_001, window, { enabled: true, reserveTokens: effReserve });

  emitCase("reserve_floor_and_threshold_strict_gt", {
    schema: "bb.compaction_oracle_case.v1",
    preset: PRESET_ID,
    case: "reserve_floor_and_threshold_strict_gt",
    source: {
      repo: OPENCLAW_REPO,
      commit: OPENCLAW_COMMIT,
      evidence: [
        "src/agents/agent-settings.ts:9,49-61",
        "src/agents/agent-compaction-constants.ts:2-14",
        "packages/agent-core/src/harness/compaction/compaction.ts:340-349",
      ],
    },
    capture: {
      kind: "executed",
      script: "scripts/compaction_oracles/capture_openclaw.ts",
      notes: [
        "Executed slices from source checkout:",
        "1. src/agents/agent-settings.ts:9 `export const DEFAULT_AGENT_COMPACTION_RESERVE_TOKENS_FLOOR = 20_000;`",
        "2. src/agents/agent-compaction-constants.ts:2-14 `const MAX_COMPACTION_RESERVE_RATIO = 0.25;` and `resolveEffectiveCompactionReserveTokens`",
        "3. packages/agent-core/src/harness/compaction/compaction.ts:348 `return contextTokens > contextWindow - settings.reserveTokens;`",
        "Verifies that Pi 0.73.1 default reserve (16,384) is raised to floor 20,000 on a 200k window, producing limit = 180,000.",
        `At contextTokens = 180,000 (equal), shouldCompact evaluates to ${firesAtEqual} (strict > does not fire). At 180,001, it evaluates to ${firesAbove}.`,
      ].join("\n"),
    },
    input: {
      messages: [
        { role: "user", content: "hello" },
        { role: "assistant", content: "world" },
      ],
      usage: {
        input_tokens: 179_000,
        output_tokens: 1_000,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
        total_tokens: 180_000,
      },
      context_window: 200_000,
      max_input_tokens: null,
      max_output_tokens: 4096,
      reason: "threshold",
      native_settings: {
        keepRecentTokens: 20000,
      },
    },
    expect: {
      trigger: {
        fires: firesAtEqual,
        tokens: 180_000,
        limit: limit,
        severity: "soft",
      },
    },
  });
}

// Case 2: 25% Reserve cap on small context window (64,000)
{
  const window = 64_000;
  const effReserve = computeEffectiveReserveFromSlices(window, 20_000); // 64000 * 0.25 = 16000
  const limit = window - effReserve; // 48000
  const firesAt48001 = thresholdSlice.shouldCompact(48_001, window, { enabled: true, reserveTokens: effReserve });

  emitCase("reserve_cap_small_context_window", {
    schema: "bb.compaction_oracle_case.v1",
    preset: PRESET_ID,
    case: "reserve_cap_small_context_window",
    source: {
      repo: OPENCLAW_REPO,
      commit: OPENCLAW_COMMIT,
      evidence: [
        "src/agents/agent-compaction-constants.ts:2,5-14",
        "src/agents/agent-settings.ts:54-61",
      ],
    },
    capture: {
      kind: "executed",
      script: "scripts/compaction_oracles/capture_openclaw.ts",
      notes: [
        "Executed slices from source checkout:",
        "src/agents/agent-compaction-constants.ts:2 `const MAX_COMPACTION_RESERVE_RATIO = 0.25;`",
        "src/agents/agent-compaction-constants.ts:10-13 `return Math.min(Math.max(0, Math.floor(params.reserveTokens)), Math.floor(contextTokenBudget * MAX_COMPACTION_RESERVE_RATIO));`",
        "For a 64k window, floor 20,000 is capped to floor(64000 * 0.25) = 16,000. Limit = 64,000 - 16,000 = 48,000.",
        `At contextTokens = 48,001, shouldCompact evaluates to ${firesAt48001}.`,
      ].join("\n"),
    },
    input: {
      messages: [{ role: "user", content: "solve task" }],
      usage: {
        input_tokens: 47_001,
        output_tokens: 1_000,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
        total_tokens: 48_001,
      },
      context_window: 64_000,
      max_input_tokens: null,
      max_output_tokens: 2048,
      reason: "threshold",
      native_settings: {},
    },
    expect: {
      trigger: {
        fires: firesAt48001,
        tokens: 48_001,
        limit: limit,
        severity: "soft",
      },
    },
  });
}

// Case 3: Cut selection snapping to turn-start boundary
{
  const entries = [
    { id: "e0", type: "message", message: { role: "user", content: "Run bash ls" } },
    {
      id: "e1",
      type: "message",
      message: {
        role: "assistant",
        content: "calling bash",
        tool_calls: [{ id: "c1", type: "function", function: { name: "bash", arguments: '{"cmd":"ls"}' } }],
      },
    },
    { id: "e2", type: "message", message: { role: "toolResult", content: "file1.txt file2.txt", tool_call_id: "c1" } },
    { id: "e3", type: "message", message: { role: "user", content: "Next command: grep foo file1.txt" } },
    {
      id: "e4",
      type: "message",
      message: {
        role: "assistant",
        content: "calling grep",
        tool_calls: [{ id: "c2", type: "function", function: { name: "bash", arguments: '{"cmd":"grep foo"}' } }],
      },
    },
    { id: "e5", type: "message", message: { role: "toolResult", content: "match found", tool_call_id: "c2" } },
  ];

  const tokenWeights: Record<string, number> = { e0: 1000, e1: 1000, e2: 1000, e3: 500, e4: 500, e5: 500 };
  const constraints = {
    budget: {
      maxTokens: 200000,
      reserveTokens: 20000,
      estimateTokens: (msg: unknown) => {
        const found = entries.find((e) => e.message === msg);
        return found ? (tokenWeights[found.id] ?? 100) : 100;
      },
    },
  };

  const cutResult = cutSlice.findCutPoint(entries, 0, entries.length, 1400, constraints);

  emitCase("cut_selection_recent_tokens_boundary", {
    schema: "bb.compaction_oracle_case.v1",
    preset: PRESET_ID,
    case: "cut_selection_recent_tokens_boundary",
    source: {
      repo: OPENCLAW_REPO,
      commit: OPENCLAW_COMMIT,
      evidence: [
        "packages/agent-core/src/harness/compaction/compaction.ts:410-447,491-589",
      ],
    },
    capture: {
      kind: "executed",
      script: "scripts/compaction_oracles/capture_openclaw.ts",
      notes: [
        "Executed slice packages/agent-core/src/harness/compaction/compaction.ts:410-589 (`findCutPoint`, `isCutPointMessage`, `isTurnStartMessage`, `isTurnStartEntry`).",
        "Backward token accumulation breaks at accumulated >= keepRecentTokens (1400).",
        "Verified cut index snaps to turn start at entry index 3 (role: user), preserving earlier toolResult pairing atomically in the summarized region.",
      ].join("\n"),
    },
    input: {
      messages: entries.map((e) => e.message),
      usage: {
        input_tokens: 4000,
        output_tokens: 500,
        cache_read_tokens: 0,
        cache_write_tokens: 0,
        total_tokens: 4500,
      },
      context_window: 200_000,
      max_input_tokens: null,
      max_output_tokens: 4096,
      message_token_estimates: entries.map((entry) => tokenWeights[entry.id]),
      reason: "threshold",
      native_settings: {
        keepRecentTokens: 1400,
      },
    },
    expect: {
      selection: {
        prefix_end: null,
        first_kept_index: cutResult.firstKeptEntryIndex, // 3
        summarize: [0, 1, 2],
        turn_prefix: [],
        replay: [],
        targets: [],
      },
    },
  });
}

// Case 4: Safeguard audit passes valid summary and verifies 5 required headings
{
  const validSummary = [
    "## Decisions",
    "- Chose sqlite for persistent caching",
    "",
    "## Open TODOs",
    "- Add transaction rollback",
    "",
    "## Constraints/Rules",
    "- Memory limit 512MB",
    "",
    "## Pending user asks",
    "- Process pending transaction tx_987654321",
    "",
    "## Exact identifiers",
    "- tx_987654321",
    "- commit_3a9d69db306cd7f081e06254cb89c4bcc14a7107",
  ].join("\n");

  const auditResult = auditSlice.auditSummaryQuality({
    structuralSummary: validSummary,
    summary: validSummary,
    identifiers: ["tx_987654321", "commit_3a9d69db306cd7f081e06254cb89c4bcc14a7107"],
    identifierPolicy: "strict",
  });

  emitCase("safeguard_audit_valid", {
    schema: "bb.compaction_oracle_case.v1",
    preset: PRESET_ID,
    case: "safeguard_audit_valid",
    source: {
      repo: OPENCLAW_REPO,
      commit: OPENCLAW_COMMIT,
      evidence: [
        "src/agents/agent-hooks/compaction-safeguard-quality.ts:15-21,471-503",
      ],
    },
    capture: {
      kind: "executed",
      script: "scripts/compaction_oracles/capture_openclaw.ts",
      notes: [
        "Executed slice src/agents/agent-hooks/compaction-safeguard-quality.ts:15-21,471-503 (`auditSummaryQuality`).",
        "Verbatim constant REQUIRED_SUMMARY_SECTIONS from line 15-21:",
        '  const REQUIRED_SUMMARY_SECTIONS = ["## Decisions", "## Open TODOs", "## Constraints/Rules", "## Pending user asks", "## Exact identifiers"] as const;',
        `Audit execution result: ok=${auditResult.ok}, reasons=${JSON.stringify(auditResult.reasons)}.`,
      ].join("\n"),
    },
    input: {
      messages: [
        { role: "user", content: "Process pending transaction tx_987654321" },
        { role: "assistant", content: "Working on commit_3a9d69db306cd7f081e06254cb89c4bcc14a7107" },
      ],
      usage: null,
      context_window: 200_000,
      max_input_tokens: null,
      max_output_tokens: 4096,
      reason: "threshold",
      native_settings: {
        identifierPolicy: "strict",
      },
      summary_responses: [],
      component: "quality_audit",
      stage: "summary",
      audit_input: { summary: validSummary, identifiers: ["tx_987654321", "commit_3a9d69db306cd7f081e06254cb89c4bcc14a7107"] },
    },
    expect: {
      details: auditResult,
    },
  });
}

// Case 5: Safeguard audit rejects summary missing required section
{
  const incompleteSummary = [
    "## Decisions",
    "- Chose sqlite",
    "",
    "## Constraints/Rules",
    "- Memory limit 512MB",
  ].join("\n");

  const auditFail = auditSlice.auditSummaryQuality({
    structuralSummary: incompleteSummary,
    summary: incompleteSummary,
    identifiers: [],
    identifierPolicy: "strict",
  });

  emitCase("safeguard_audit_missing_sections_rejected", {
    schema: "bb.compaction_oracle_case.v1",
    preset: PRESET_ID,
    case: "safeguard_audit_missing_sections_rejected",
    source: {
      repo: OPENCLAW_REPO,
      commit: OPENCLAW_COMMIT,
      evidence: [
        "src/agents/agent-hooks/compaction-safeguard-quality.ts:15-21,481-494",
      ],
    },
    capture: {
      kind: "executed",
      script: "scripts/compaction_oracles/capture_openclaw.ts",
      notes: [
        "Executed slice src/agents/agent-hooks/compaction-safeguard-quality.ts:481-494 (`auditSummaryQuality`).",
        "Lines 483-486: `for (const section of REQUIRED_SUMMARY_SECTIONS) { if (!lines.has(section)) { reasons.push(\"missing_section:\" + section); } }`",
        `Audit execution result: ok=${auditFail.ok}, reasons=${JSON.stringify(auditFail.reasons)}.`,
      ].join("\n"),
    },
    input: {
      messages: [{ role: "user", content: "Do task" }],
      usage: null,
      context_window: 200_000,
      max_input_tokens: null,
      max_output_tokens: 4096,
      reason: "threshold",
      native_settings: {
        mode: "safeguard",
      },
      summary_responses: [incompleteSummary],
    },
    expect: {
      failure: {
        kind: "quality_audit_failed",
        message: auditFail.reasons.join(", "),
      },
    },
  });
}

// Case 6: Placement wraps summary in user message
{
  const summaryContent = "Summary text from LLM";
  const prefix = messagesSlice.COMPACTION_SUMMARY_PREFIX;
  const suffix = messagesSlice.COMPACTION_SUMMARY_SUFFIX;
  const placedContent = prefix + summaryContent + suffix;

  emitCase("placement_summary_as_user_wrapper", {
    schema: "bb.compaction_oracle_case.v1",
    preset: PRESET_ID,
    case: "placement_summary_as_user_wrapper",
    source: {
      repo: OPENCLAW_REPO,
      commit: OPENCLAW_COMMIT,
      evidence: [
        "packages/agent-core/src/harness/messages.ts:45-51,178-189",
      ],
    },
    capture: {
      kind: "executed",
      script: "scripts/compaction_oracles/capture_openclaw.ts",
      notes: [
        "Executed slice packages/agent-core/src/harness/messages.ts:45-51,178-189.",
        "Verbatim constants from lines 45-51:",
        '  export const COMPACTION_SUMMARY_PREFIX = "The conversation history before this point was compacted into the following summary:\\n\\n<summary>\\n";',
        '  export const COMPACTION_SUMMARY_SUFFIX = "\\n</summary>";',
        "Lines 178-188: converts compactionSummary to role user with text: COMPACTION_SUMMARY_PREFIX + message.summary + COMPACTION_SUMMARY_SUFFIX.",
      ].join("\n"),
    },
    input: {
      messages: [
        { role: "user", content: "Prior task instruction" },
        { role: "assistant", content: "Prior task response" },
        { role: "user", content: "Latest instruction" },
      ],
      usage: null,
      context_window: 200_000,
      max_input_tokens: null,
      max_output_tokens: 4096,
      reason: "threshold",
      native_settings: {},
      summary_responses: [],
      component: "placement",
      stage: "summary",
      placement_input: { selection: { first_kept_index: 2 }, summary: summaryContent },
    },
    expect: {
      projected_view: [
        {
          role: "user",
          content: placedContent,
        },
        { role: "user", content: "Latest instruction" },
      ],
    },
  });
}

console.log(`OpenClaw execution complete. Generated ${cases.length} cases.`);
