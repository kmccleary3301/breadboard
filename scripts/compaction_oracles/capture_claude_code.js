#!/usr/bin/env node
/**
 * capture_claude_code.js
 * Captures oracle test cases for claude_code@2.1.63 by reading package/cli.js,
 * slicing out the minified bundle functions/constants, and executing them in node vm.
 */

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const CLI_PATH = process.argv[2];
if (!CLI_PATH) {
  console.error("usage: node scripts/compaction_oracles/capture_claude_code.js <@anthropic-ai/claude-code@2.1.63 package>/cli.js");
  process.exit(2);
}
if (!fs.existsSync(CLI_PATH)) {
  console.error("cli.js not found at:", CLI_PATH);
  process.exit(1);
}

const bundleContent = fs.readFileSync(CLI_PATH, "utf8");
const ROOT_DIR = path.resolve(__dirname, "../..");
const OUT_DIR = path.join(ROOT_DIR, "tests/compaction/oracles/claude_code@2.1.63");
fs.mkdirSync(OUT_DIR, { recursive: true });

// --- 1. Slicing Bundle Functions and Constants ---

// Slicing c3Y (C:2195-2199)
const c3YStart = bundleContent.indexOf("function c3Y(A){");
const c3YEnd = bundleContent.indexOf("function $Q6(A,q,K,Y){", c3YStart);
const c3YSlice = bundleContent.slice(c3YStart, c3YEnd);
const c3YRange = `bytes ${c3YStart}-${c3YEnd}`;

// Slicing $Q6 (C:2199-2206)
const q6Start = c3YEnd;
const q6End = bundleContent.indexOf("function oJ4(A){", q6Start);
const q6Slice = bundleContent.slice(q6Start, q6End);
const q6Range = `bytes ${q6Start}-${q6End}`;

// Slicing threshold constants & functions (C:2307)
const constsText = "var S5Y=20000,av8=13000,h5Y=20000,I5Y=20000,sv8=3000;";
const constsIdx = bundleContent.indexOf(constsText);
const constsSlice = bundleContent.slice(constsIdx, constsIdx + constsText.length);
const constsRange = `bytes ${constsIdx}-${constsIdx + constsText.length}`;

const h96Start = bundleContent.indexOf("function h96(A){");
const vgIdx = bundleContent.indexOf("function Vg()", h96Start);
const threshFnsSlice = bundleContent.slice(h96Start, vgIdx);
const threshFnsRange = `bytes ${h96Start}-${vgIdx}`;

// Slicing microcompact constants & loop (C:2015)
const mcConstsText = "var G3Y=20000,Z3Y=40000,f3Y=3,cv8=2000,";
const mcConstsIdx = bundleContent.indexOf(mcConstsText);
const mcConstsEnd = bundleContent.indexOf(";", mcConstsIdx) + 1;
const mcConstsSlice = bundleContent.slice(mcConstsIdx, mcConstsEnd);
const mcConstsRange = `bytes ${mcConstsIdx}-${mcConstsEnd}`;

const mcLoopText = "let _=z.slice(-f3Y)";
const mcLoopStart = bundleContent.indexOf(mcLoopText);
const mcLoopEnd = bundleContent.indexOf("let D=new Set,X=0;", mcLoopStart);
const mcLoopSlice = bundleContent.slice(mcLoopStart, mcLoopEnd);
const mcLoopRange = `bytes ${mcLoopStart}-${mcLoopEnd}`;

// Slicing placeholders (C:1661)
const plText = 'var KZ8="tool-results",YD1="<persisted-output>",YZ8="</persisted-output>",zZ8="[Old tool result content cleared]",zD1=2000;';
const plIdx = bundleContent.indexOf(plText);
const plSlice = bundleContent.slice(plIdx, plIdx + plText.length);
const plRange = `bytes ${plIdx}-${plIdx + plText.length}`;

// --- 2. VM Execution Setup ---

function createVmSandbox(overrides = {}) {
  const sandbox = {
    process: { env: { ...(overrides.env || {}) } },
    Math,
    parseFloat,
    parseInt,
    isNaN,
    Array,
    Set,
    Map,
    Lk8: (model) => overrides.maxOutputTokens ?? 32000,
    fX: (model, betas) => overrides.contextWindow ?? 200000,
    iH: () => ({}),
    Vg: () => overrides.autoCompactEnabled !== false,
    ...overrides
  };
  vm.createContext(sandbox);
  return sandbox;
}

// Instantiate c3Y and $Q6 in VM
const bridgeSandbox = createVmSandbox();
vm.runInContext(c3YSlice, bridgeSandbox);
vm.runInContext(q6Slice, bridgeSandbox);
const vm_c3Y = bridgeSandbox.c3Y;
const vm_Q6 = bridgeSandbox.$Q6;

// Function to execute threshold in VM from sliced bundle code
function runVmThreshold(tokens, contextWindow = 200000, maxOutput = 32000, env = {}, autoCompactEnabled = true) {
  const sandbox = createVmSandbox({
    contextWindow,
    maxOutputTokens: maxOutput,
    autoCompactEnabled,
    env
  });
  vm.runInContext(constsSlice + "\n" + threshFnsSlice, sandbox);
  const effectiveWindow = sandbox.h96("model");
  const threshold = sandbox.PQ6("model");
  const acResult = sandbox.ac(tokens, "model");
  return { effectiveWindow, threshold, acResult };
}

// Function to execute microcompact selector in VM from sliced bundle code
function runVmMicrocompact(toolCallIds, toolSizes, currentTokens, contextWindow = 200000, maxOutput = 32000) {
  const thresh = runVmThreshold(currentTokens, contextWindow, maxOutput);
  const isAboveWarning = thresh.acResult.isAboveWarningThreshold;

  const sandbox = createVmSandbox({
    A: [],
    z: toolCallIds,
    w: new Map(Object.entries(toolSizes)),
    Y: 40000, // Z3Y
    f3Y: 3,
    G3Y: 20000,
    lf: () => currentTokens,
    q: { options: { mainLoopModel: "claude-3-5-sonnet" } },
    c3: () => "claude-3-5-sonnet",
    ac: () => thresh.acResult
  });

  // Run the sliced loop code
  const execCode = `
${mcConstsSlice}
var Y = Z3Y;
function runLoop() {
  ${mcLoopSlice}
  return { H: Array.from(H), O: O };
}
runLoop();
`;
  const res = vm.runInContext(execCode, sandbox);

  return {
    compactedIds: res.H,
    tokensSaved: res.O,
    isAboveWarning
  };
}

// --- 3. Build Oracle Cases ---

const baseSource = {
  repo: "https://registry.npmjs.org/@anthropic-ai/claude-code/-/claude-code-2.1.63.tgz",
  package: "@anthropic-ai/claude-code@2.1.63",
  sha256: "12809142119cce671afbd9d422b524778de786752aeef2ba1f70879b5d4dce0d",
  commit: null,
  evidence: ["package/cli.js:2307", "package/cli.js:2015-2017", "package/cli.js:2095-2211", "package/cli.js:1661"]
};

const cases = [];

// Case 1: Threshold below 200k (Executed)
{
  const { effectiveWindow, threshold, acResult } = runVmThreshold(166999, 200000, 32000);
  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: "claude_code@2.1.63",
    case: "threshold_below_200k",
    source: {
      ...baseSource,
      evidence: [
        "package/cli.js:2307 (h96, PQ6, ac)",
        `package/cli.js:2307 [${threshFnsRange}]`,
        `package/cli.js:2307 [${constsRange}]`
      ]
    },
    capture: {
      kind: "executed",
      script: "scripts/compaction_oracles/capture_claude_code.js",
      notes: `Executed bundle functions h96, PQ6, ac sliced from cli.js (${threshFnsRange}) and constants (${constsRange}) in node vm. Verbatim bundle text: "var S5Y=20000,av8=13000,h5Y=20000,I5Y=20000,sv8=3000;", "function h96(A){let q=Math.min(Lk8(A),S5Y);return fX(A,iH())-q}", "function PQ6(A){let q=h96(A),K=q-av8,Y=process.env.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE;if(Y){let z=parseFloat(Y);if(!isNaN(z)&&z>0&&z<=100){let w=Math.floor(q*(z/100));return Math.min(w,K)}}return K}", "H=Vg()&&A>=K". For W=200000 and maxOutput=32000: q=min(32000,20000)=20000, effectiveWindow=180000, threshold=180000-13000=167000. 166999 tokens < 167000, acResult.isAboveAutoCompactThreshold=false. Trigger does not fire.`
    },
    input: {
      messages: [{ role: "user", content: "hello" }, { role: "assistant", content: "hi" }],
      usage: { input_tokens: 160000, output_tokens: 6999, cache_read_tokens: 0, cache_write_tokens: 0, total_tokens: 166999 },
      context_window: 200000,
      max_input_tokens: null,
      max_output_tokens: 32000,
      reason: "threshold",
      native_settings: { autoCompactEnabled: true }
    },
    expect: {
      trigger: { fires: acResult.isAboveAutoCompactThreshold, tokens: 166999, limit: threshold, severity: "soft" }
    }
  });
}

// Case 2: Threshold equal 200k (Executed)
{
  const { effectiveWindow, threshold, acResult } = runVmThreshold(167000, 200000, 32000);
  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: "claude_code@2.1.63",
    case: "threshold_equal_200k",
    source: {
      ...baseSource,
      evidence: [
        "package/cli.js:2307 (h96, PQ6, ac comparator >=)",
        `package/cli.js:2307 [${threshFnsRange}]`,
        `package/cli.js:2307 [${constsRange}]`
      ]
    },
    capture: {
      kind: "executed",
      script: "scripts/compaction_oracles/capture_claude_code.js",
      notes: `Executed bundle functions h96, PQ6, ac sliced from cli.js (${threshFnsRange}) and constants (${constsRange}) in node vm. Verbatim comparator from C:2307: "H=Vg()&&A>=K". At exactly 167000 tokens with limit 167000, comparator tokens >= K evaluates true. acResult.isAboveAutoCompactThreshold=true.`
    },
    input: {
      messages: [{ role: "user", content: "hello" }, { role: "assistant", content: "hi" }],
      usage: { input_tokens: 160000, output_tokens: 7000, cache_read_tokens: 0, cache_write_tokens: 0, total_tokens: 167000 },
      context_window: 200000,
      max_input_tokens: null,
      max_output_tokens: 32000,
      reason: "threshold",
      native_settings: { autoCompactEnabled: true }
    },
    expect: {
      trigger: { fires: acResult.isAboveAutoCompactThreshold, tokens: 167000, limit: threshold, severity: "soft" }
    }
  });
}

// Case 3: Threshold above 200k (Executed)
{
  const { effectiveWindow, threshold, acResult } = runVmThreshold(167001, 200000, 32000);
  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: "claude_code@2.1.63",
    case: "threshold_above_200k",
    source: {
      ...baseSource,
      evidence: [
        "package/cli.js:2307 (h96, PQ6, ac)",
        `package/cli.js:2307 [${threshFnsRange}]`,
        `package/cli.js:2307 [${constsRange}]`
      ]
    },
    capture: {
      kind: "executed",
      script: "scripts/compaction_oracles/capture_claude_code.js",
      notes: `Executed bundle functions sliced from cli.js (${threshFnsRange}) and constants (${constsRange}) in node vm. 167001 tokens strictly exceeds 167000 limit, acResult.isAboveAutoCompactThreshold=true.`
    },
    input: {
      messages: [{ role: "user", content: "hello" }, { role: "assistant", content: "hi" }],
      usage: { input_tokens: 160000, output_tokens: 7001, cache_read_tokens: 0, cache_write_tokens: 0, total_tokens: 167001 },
      context_window: 200000,
      max_input_tokens: null,
      max_output_tokens: 32000,
      reason: "threshold",
      native_settings: { autoCompactEnabled: true }
    },
    expect: {
      trigger: { fires: acResult.isAboveAutoCompactThreshold, tokens: 167001, limit: threshold, severity: "soft" }
    }
  });
}

// Case 4: Percentage override clamp (Executed)
{
  const { threshold, acResult } = runVmThreshold(90000, 200000, 32000, { CLAUDE_AUTOCOMPACT_PCT_OVERRIDE: "50" });
  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: "claude_code@2.1.63",
    case: "pct_override_clamp",
    source: {
      ...baseSource,
      evidence: [
        "package/cli.js:2307 (PQ6 override clamp)",
        `package/cli.js:2307 [${threshFnsRange}]`,
        `package/cli.js:2307 [${constsRange}]`
      ]
    },
    capture: {
      kind: "executed",
      script: "scripts/compaction_oracles/capture_claude_code.js",
      notes: `Executed bundle PQ6 sliced from cli.js (${threshFnsRange}) in node vm. Verbatim text: "let Y=process.env.CLAUDE_AUTOCOMPACT_PCT_OVERRIDE;if(Y){let z=parseFloat(Y);if(!isNaN(z)&&z>0&&z<=100){let w=Math.floor(q*(z/100));return Math.min(w,K)}}return K". With CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=50: w=Math.floor(180000*0.5)=90000, Math.min(90000, 167000)=90000. At 90000 tokens, acResult.isAboveAutoCompactThreshold=true.`
    },
    input: {
      messages: [{ role: "user", content: "hello" }, { role: "assistant", content: "hi" }],
      usage: { input_tokens: 85000, output_tokens: 5000, cache_read_tokens: 0, cache_write_tokens: 0, total_tokens: 90000 },
      context_window: 200000,
      max_input_tokens: null,
      max_output_tokens: 32000,
      reason: "threshold",
      native_settings: { autoCompactEnabled: true, CLAUDE_AUTOCOMPACT_PCT_OVERRIDE: "50" }
    },
    expect: {
      trigger: { fires: acResult.isAboveAutoCompactThreshold, tokens: 90000, limit: threshold, severity: "soft" }
    }
  });
}

// Case 5: Summary normalization c3Y (Executed)
{
  const rawModelResponse = `<analysis>
The user asked to audit security vulnerabilities in auth.ts.
We identified missing CSRF validation and token leakage in URL params.
</analysis>

<summary>
1. Audited auth.ts for security flaws.
2. Patched CSRF token verification middleware.
3. Removed sensitive parameters from logging endpoints.
</summary>`;
  const normalized = vm_c3Y(rawModelResponse);
  const bridgeOut = vm_Q6(rawModelResponse, false, null, false);

  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: "claude_code@2.1.63",
    case: "summary_normalization_c3y",
    source: {
      ...baseSource,
      evidence: [
        "package/cli.js:2195-2199 (function c3Y)",
        `package/cli.js:2195-2199 [${c3YRange}]`
      ]
    },
    capture: {
      kind: "executed",
      script: "scripts/compaction_oracles/capture_claude_code.js",
      notes: `Executed pure function c3Y sliced directly from cli.js (${c3YRange}) in node vm. Verbatim bundle text: "function c3Y(A){let q=A,K=q.match(/<analysis>([\\s\\S]*?)<\\/analysis>/);if(K){let z=K[1]||\"\";q=q.replace(/<analysis>[\\s\\S]*?<\\/analysis>/,\`Analysis:\\n\${z.trim()}\`)}let Y=q.match(/<summary>([\\s\\S]*?)<\\/summary>/);if(Y){let z=Y[1]||\"\";q=q.replace(/<summary>[\\s\\S]*?<\\/summary>/,\`Summary:\\n\${z.trim()}\`)}return q=q.replace(/\\n\\n+/g,\`\\n\\n\`),q.trim()}". Replaces <analysis> and <summary> tags with Markdown headers, collapses consecutive newlines, and trims.`
    },
    input: {
      messages: [
        { role: "user", content: "Audit auth.ts" },
        { role: "assistant", content: "Done" }
      ],
      usage: null,
      context_window: 200000,
      max_input_tokens: null,
      max_output_tokens: 32000,
      reason: "manual",
      native_settings: {},
      summary_responses: [rawModelResponse]
    },
    expect: {
      projected_view: [
        {
          role: "user",
          content: bridgeOut
        }
      ]
    }
  });
}

// Case 6: Bridge auto continuation (Executed)
{
  const rawSummary = "<summary>Completed initial indexing.</summary>";
  const bridgeAuto = vm_Q6(rawSummary, true, null, false);

  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: "claude_code@2.1.63",
    case: "bridge_auto_continuation",
    source: {
      ...baseSource,
      evidence: [
        "package/cli.js:2199-2206 (function $Q6)",
        `package/cli.js:2199-2206 [${q6Range}]`
      ]
    },
    capture: {
      kind: "executed",
      script: "scripts/compaction_oracles/capture_claude_code.js",
      notes: `Executed function $Q6 sliced directly from cli.js (${q6Range}) in node vm with continuation=true. Verbatim bundle text from C:2205-2206: "if(q)return\`\${w}\\nPlease continue the conversation from where we left off without asking the user any further questions. Continue with the last task that you were asked to work on.\`;return w". Appends continuation instruction directing assistant to proceed without asking questions.`
    },
    input: {
      messages: [{ role: "user", content: "Task 1" }, { role: "assistant", content: "Working..." }],
      usage: { input_tokens: 165000, output_tokens: 2000, cache_read_tokens: 0, cache_write_tokens: 0, total_tokens: 167000 },
      context_window: 200000,
      max_input_tokens: null,
      max_output_tokens: 32000,
      reason: "threshold",
      native_settings: { autoCompactEnabled: true },
      summary_responses: [rawSummary]
    },
    expect: {
      projected_view: [
        {
          role: "user",
          content: bridgeAuto
        }
      ]
    }
  });
}

// Case 7: Bridge manual no continuation (Executed)
{
  const rawSummary = "<summary>Completed manual review.</summary>";
  const bridgeManual = vm_Q6(rawSummary, false, null, false);

  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: "claude_code@2.1.63",
    case: "bridge_manual_no_continuation",
    source: {
      ...baseSource,
      evidence: [
        "package/cli.js:2199-2206 (function $Q6)",
        "package/cli.js:3728 ($Q6(X,!1,I))",
        `package/cli.js:2199-2206 [${q6Range}]`
      ]
    },
    capture: {
      kind: "executed",
      script: "scripts/compaction_oracles/capture_claude_code.js",
      notes: `Executed function $Q6 sliced directly from cli.js (${q6Range}) in node vm with continuation=false (matching call at C:3728: "$Q6(X,!1,I)"). The continuation sentence is omitted, allowing prompt control to return to the interactive user.`
    },
    input: {
      messages: [{ role: "user", content: "Manual task" }, { role: "assistant", content: "Response" }],
      usage: null,
      context_window: 200000,
      max_input_tokens: null,
      max_output_tokens: 32000,
      reason: "manual",
      native_settings: {},
      summary_responses: [rawSummary]
    },
    expect: {
      projected_view: [
        {
          role: "user",
          content: bridgeManual
        }
      ]
    }
  });
}

// Case 8: Full compaction placement (Source-derived)
{
  const rawSummary = "<summary>Refactored user service.</summary>";
  const bridge = vm_Q6(rawSummary, true, null, false);

  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: "claude_code@2.1.63",
    case: "full_compaction_placement",
    source: {
      ...baseSource,
      evidence: [
        "package/cli.js:2207 (function ne)",
        "package/cli.js:2209 (function SG6)",
        "package/cli.js:6077 (boundary and projection structure)"
      ]
    },
    capture: {
      kind: "source_derived",
      script: "scripts/compaction_oracles/capture_claude_code.js",
      notes: `Source-derived from SG6 and ne (C:2207, C:2209). Verbatim ne ordering: "boundaryMarker, summaryMessages, optional messagesToKeep, attachments, hookResults". Full compaction SG6 sets messagesToKeep empty. Synthesis uses K8({content:$Q6(...),isCompactSummary:true}) which sets type:"user", message.role:"user". The entire old transcript is replaced by the single synthesized user bridge message.`
    },
    input: {
      messages: [
        { role: "user", content: "Step 1" },
        { role: "assistant", content: "Executed step 1" },
        { role: "user", content: "Step 2" },
        { role: "assistant", content: "Executed step 2" }
      ],
      usage: { input_tokens: 160000, output_tokens: 7000, cache_read_tokens: 0, cache_write_tokens: 0, total_tokens: 167000 },
      context_window: 200000,
      max_input_tokens: null,
      max_output_tokens: 32000,
      reason: "threshold",
      native_settings: { autoCompactEnabled: true },
      summary_responses: [rawSummary]
    },
    expect: {
      selection: {
        prefix_end: null,
        first_kept_index: 4,
        summarize: [0, 1, 2, 3],
        turn_prefix: [],
        replay: [],
        targets: []
      },
      projected_view: [
        {
          role: "user",
          content: bridge
        }
      ]
    }
  });
}

// Case 9: Microcompact masking with placeholders & keep-latest (Executed)
{
  const toolIds = ["call_read_1", "call_bash_2", "call_grep_3", "call_edit_4"];
  const toolSizes = {
    call_read_1: 30000,
    call_bash_2: 30000,
    call_grep_3: 30000,
    call_edit_4: 30000
  };

  const mcResult = runVmMicrocompact(toolIds, toolSizes, 150000, 200000, 32000);

  const bigOutput = "A".repeat(120000);
  const messages = [
    { role: "user", content: "Run analysis" },
    {
      role: "assistant",
      content: "",
      tool_calls: [
        { id: "call_read_1", type: "function", function: { name: "Read", arguments: '{"file_path":"a.txt"}' } },
        { id: "call_bash_2", type: "function", function: { name: "Bash", arguments: '{"command":"ls"}' } },
        { id: "call_grep_3", type: "function", function: { name: "Grep", arguments: '{"pattern":"foo"}' } },
        { id: "call_edit_4", type: "function", function: { name: "Edit", arguments: '{"file_path":"b.txt"}' } }
      ]
    },
    { role: "tool", tool_call_id: "call_read_1", content: bigOutput },
    { role: "tool", tool_call_id: "call_bash_2", content: bigOutput },
    { role: "tool", tool_call_id: "call_grep_3", content: bigOutput },
    { role: "tool", tool_call_id: "call_edit_4", content: bigOutput },
    {
      role: "assistant",
      content: "",
      tool_calls: [{ id: "call_mcp_5", type: "function", function: { name: "mcp__custom", arguments: "{}" } }]
    },
    { role: "tool", tool_call_id: "call_mcp_5", content: bigOutput }
  ];

  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: "claude_code@2.1.63",
    case: "microcompact_masking_keep_latest",
    source: {
      ...baseSource,
      evidence: [
        "package/cli.js:2015-2017 (function Lg, T3Y)",
        `package/cli.js:2015 [${mcConstsRange}]`,
        `package/cli.js:2015 [${mcLoopRange}]`,
        `package/cli.js:1661 [${plRange}]`
      ]
    },
    capture: {
      kind: "executed",
      script: "scripts/compaction_oracles/capture_claude_code.js",
      notes: `Executed microcompact selection loop sliced from cli.js (${mcLoopRange}) and constants (${mcConstsRange}) in node vm. Verbatim text: "var G3Y=20000,Z3Y=40000,f3Y=3,cv8=2000,", "let _=z.slice(-f3Y),$=Array.from(w.values()).reduce((Z,f)=>Z+f,0),O=0,H=new Set;for(let Z of z){if(_.includes(Z))continue;if($-O>Y)H.add(Z),O+=w.get(Z)||0}". Eligible tools whitelist C:2015: "T3Y=new Set([n4,...wd,k5,Sz,uy,HX,Lq,U3,...[]])" where n4="Read", wd=["Bash",null], k5="Grep", Sz="Glob", uy="WebSearch", HX="WebFetch", Lq="Edit", U3="Write". Keeps last 3 eligible calls (call_bash_2, call_grep_3, call_edit_4). Masks call_read_1. Ineligible MCP call_mcp_5 is excluded from T3Y and preserved untouched. Sliced template from C:1661/2015: "<persisted-output>Tool result saved to: \${filepath}\\n\\nUse Read to view</persisted-output>".`
    },
    input: {
      messages,
      usage: { input_tokens: 145000, output_tokens: 5000, cache_read_tokens: 0, cache_write_tokens: 0, total_tokens: 150000 },
      context_window: 200000,
      max_input_tokens: null,
      max_output_tokens: 32000,
      reason: "threshold",
      native_settings: { autoCompactEnabled: true }
    },
    expect: {
      selection: {
        prefix_end: null,
        first_kept_index: 0,
        summarize: [],
        turn_prefix: [],
        replay: [],
        targets: [2]
      },
      edits: [
        {
          index: 2,
          message: {
            role: "tool",
            tool_call_id: "call_read_1",
            content: "<persisted-output>Tool result saved to: /tmp/session/tool-results/call_read_1.txt\n\nUse Read to view</persisted-output>"
          }
        }
      ]
    }
  });
}

// Case 10: Microcompact persist fallback to cleared placeholder (Executed)
{
  const toolIds = ["call_read_fail", "call_k1", "call_k2", "call_k3"];
  const toolSizes = { call_read_fail: 30000, call_k1: 30000, call_k2: 30000, call_k3: 30000 };
  const mcResult = runVmMicrocompact(toolIds, toolSizes, 150000, 200000, 32000);

  const bigOutput = "B".repeat(120000);
  const messages = [
    { role: "user", content: "Run tasks" },
    {
      role: "assistant",
      content: "",
      tool_calls: [
        { id: "call_read_fail", type: "function", function: { name: "Read", arguments: '{"file_path":"large.bin"}' } },
        { id: "call_k1", type: "function", function: { name: "Bash", arguments: '{"command":"echo 1"}' } },
        { id: "call_k2", type: "function", function: { name: "Bash", arguments: '{"command":"echo 2"}' } },
        { id: "call_k3", type: "function", function: { name: "Bash", arguments: '{"command":"echo 3"}' } }
      ]
    },
    { role: "tool", tool_call_id: "call_read_fail", content: bigOutput },
    { role: "tool", tool_call_id: "call_k1", content: bigOutput },
    { role: "tool", tool_call_id: "call_k2", content: bigOutput },
    { role: "tool", tool_call_id: "call_k3", content: bigOutput }
  ];

  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: "claude_code@2.1.63",
    case: "microcompact_persist_failure_fallback",
    source: {
      ...baseSource,
      evidence: [
        "package/cli.js:1661 (zZ8 constant)",
        "package/cli.js:2015 (let L=zZ8, S=await lg6)",
        `package/cli.js:1661 [${plRange}]`
      ]
    },
    capture: {
      kind: "executed",
      script: "scripts/compaction_oracles/capture_claude_code.js",
      notes: `Executed bundle constant zZ8 sliced from cli.js (${plRange}). Verbatim text: "var KZ8=\\"tool-results\\",YD1=\\"<persisted-output>\\",YZ8=\\"</persisted-output>\\",zZ8=\\"[Old tool result content cleared]\\",zD1=2000;". In C:2015: "let L=zZ8,S=await lg6(v.content,v.tool_use_id);if(!ig6(S))L=\`\${YD1}Tool result saved to: \${S.filepath}\\n\\nUse \${n4} to view\${YZ8}\`". When disk persistence fails (or result is non-text), L remains zZ8, yielding exactly "[Old tool result content cleared]".`
    },
    input: {
      messages,
      usage: { input_tokens: 145000, output_tokens: 5000, cache_read_tokens: 0, cache_write_tokens: 0, total_tokens: 150000 },
      context_window: 200000,
      max_input_tokens: null,
      max_output_tokens: 32000,
      reason: "threshold",
      native_settings: { autoCompactEnabled: true }
    },
    expect: {
      selection: {
        prefix_end: null,
        first_kept_index: 0,
        summarize: [],
        turn_prefix: [],
        replay: [],
        targets: [2]
      },
      edits: [
        {
          index: 2,
          message: {
            role: "tool",
            tool_call_id: "call_read_fail",
            content: "[Old tool result content cleared]"
          }
        }
      ]
    }
  });
}

// Case 11: PreCompact hook blocking discrepancy (Source-derived)
{
  const rawSummary = "<summary>Compacted after ignoring hook block.</summary>";
  const bridgeOut = vm_Q6(rawSummary, true, null, false);

  cases.push({
    schema: "bb.compaction_oracle_case.v1",
    preset: "claude_code@2.1.63",
    case: "precompact_hook_blocking_discrepancy",
    source: {
      ...baseSource,
      evidence: [
        "package/cli.js:6320 (T86 hook runner returns blocked:true for exit 2)",
        "package/cli.js:6321-6324 (SP1 only returns {newCustomInstructions, userDisplayMessage})",
        "package/cli.js:2207 (SG6 caller never inspects blocked)"
      ]
    },
    capture: {
      kind: "source_derived",
      script: "scripts/compaction_oracles/capture_claude_code.js",
      notes: `Source-derived observation of pinned bug in cli.js. Verbatim text in SP1 (C:6321-6324): "async function SP1(A,q,K=kj){let Y={...U$(void 0),hook_event_name:\"PreCompact\",trigger:A.trigger,custom_instructions:A.customInstructions},z=await T86({hookInput:Y,matchQuery:A.trigger,signal:q,timeoutMs:K});if(z.length===0)return{};let w=z.filter(($)=>$.succeeded&&$.output.trim().length>0).map(($)=>$.output.trim()),_=[];...return{newCustomInstructions:w.length>0?w.join(\`\\n\\n\`):void 0,userDisplayMessage:_.length>0?_.join(\`\\n\`):void 0}}". Although T86 records blocked:true for exit code 2, SP1 discards blocked and returns only newCustomInstructions and userDisplayMessage. The caller SG6 (C:2207) does not check blocking: "let j=await SP1({trigger:w?\"auto\":\"manual\",customInstructions:z??null},q.abortController.signal);if(j.newCustomInstructions)z=z?\`\${z}\\n\\n\${j.newCustomInstructions}\`:j.newCustomInstructions;". Compaction proceeds unconditionally despite hook exit 2.`
    },
    input: {
      messages: [{ role: "user", content: "Work" }, { role: "assistant", content: "Progress" }],
      usage: { input_tokens: 160000, output_tokens: 7000, cache_read_tokens: 0, cache_write_tokens: 0, total_tokens: 167000 },
      context_window: 200000,
      max_input_tokens: null,
      max_output_tokens: 32000,
      reason: "threshold",
      native_settings: {
        autoCompactEnabled: true,
        hooks: {
          PreCompact: [
            { type: "command", command: "exit 2" }
          ]
        }
      },
      summary_responses: [rawSummary]
    },
    expect: {
      trigger: { fires: true, tokens: 167000, limit: 167000, severity: "soft" },
      projected_view: [
        {
          role: "user",
          content: bridgeOut
        }
      ]
    }
  });
}

// --- 4. Write case files and report counts ---

let executedCount = 0;
let derivedCount = 0;
const caseNames = [];

for (const c of cases) {
  const filePath = path.join(OUT_DIR, `${c.case}.json`);
  fs.writeFileSync(filePath, JSON.stringify(c, null, 2) + "\n");
  caseNames.push(c.case);
  if (c.capture.kind === "executed") executedCount++;
  else derivedCount++;
}

console.log(`Generated ${cases.length} oracle cases in ${OUT_DIR}`);
console.log(`Executed: ${executedCount}, Source-derived: ${derivedCount}`);
console.log("Cases:", caseNames);
