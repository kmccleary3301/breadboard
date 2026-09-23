#!/usr/bin/env node
/**
 * stdin: one call ``{"tool_id", "arguments", "cwd", "call_id"}``, or a batch
 * ``{"calls":[...], "cwd"}``. stdout is exactly one JSON tool result, or a
 * ``{"results":[...]}`` batch in input order; diagnostics go to stderr.
 *
 * PI_CODING_AGENT_NODE_MODULES may point at the Node installation containing
 * the pinned @mariozechner/pi-coding-agent and @mariozechner/pi-ai packages.
 */
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const MAX_REQUEST_BYTES = 1024 * 1024;
const TOOL_IDS = new Set(["read", "bash", "edit", "write"]);

function packageImport(nodeModules, packageName, entry) {
  if (!nodeModules) return import(packageName);
  return import(pathToFileURL(resolve(nodeModules, packageName, entry)).href);
}

const nodeModules = process.env.PI_CODING_AGENT_NODE_MODULES;
const codingAgent = await packageImport(nodeModules, "@mariozechner/pi-coding-agent", "dist/index.js");
const piAi = await packageImport(nodeModules, "@mariozechner/pi-ai", "dist/index.js");
const { createBashTool, createEditTool, createReadTool, createWriteTool } = codingAgent;
const { validateToolArguments } = piAi;
const TOOL_FACTORIES = Object.freeze({
  bash: createBashTool,
  edit: createEditTool,
  read: createReadTool,
  write: createWriteTool,
});

function fail(message) {
  throw new Error(message);
}

async function readRequest() {
  const chunks = [];
  let bytes = 0;
  for await (const chunk of process.stdin) {
    const value = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    bytes += value.byteLength;
    if (bytes > MAX_REQUEST_BYTES) fail(`request exceeds ${MAX_REQUEST_BYTES} bytes`);
    chunks.push(value);
  }
  if (bytes === 0) fail("request stdin is empty");
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch (error) {
    fail(`request is not valid JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function validateCall(call, defaultCwd) {
  if (call === null || typeof call !== "object" || Array.isArray(call)) fail("call must be an object");
  const toolId = call.tool_id;
  if (typeof toolId !== "string" || !TOOL_IDS.has(toolId)) fail(`unknown tool_id: ${String(toolId)}`);
  const argumentsValue = call.arguments;
  if (argumentsValue === null || typeof argumentsValue !== "object" || Array.isArray(argumentsValue)) {
    fail("arguments must be a JSON object");
  }
  const cwd = typeof call.cwd === "string" && call.cwd ? call.cwd : defaultCwd;
  const callId = typeof call.call_id === "string" && call.call_id ? call.call_id : "bb-native-call";
  return { toolId, argumentsValue, cwd, callId };
}

async function executeCall(call, defaultCwd, signal) {
  const request = validateCall(call, defaultCwd);
  const tool = TOOL_FACTORIES[request.toolId](request.cwd);
  try {
    const prepared = typeof tool.prepareArguments === "function"
      ? tool.prepareArguments(request.argumentsValue)
      : request.argumentsValue;
    const argumentsValue = validateToolArguments(tool, {
      id: request.callId,
      name: request.toolId,
      arguments: prepared,
    });
    const result = await tool.execute(request.callId, argumentsValue, signal);
    return {
      content: Array.isArray(result?.content) ? result.content : [],
      details: result?.details ?? {},
      isError: false,
      terminate: false,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      content: [{ type: "text", text: message }],
      details: {},
      isError: true,
      terminate: false,
    };
  }
}

async function main() {
  const input = await readRequest();
  if (input === null || typeof input !== "object" || Array.isArray(input)) {
    fail("request must be a JSON object");
  }
  const defaultCwd = typeof input.cwd === "string" && input.cwd ? input.cwd : process.cwd();
  const controller = new AbortController();
  const abort = () => controller.abort();
  process.once("SIGINT", abort);
  process.once("SIGTERM", abort);
  try {
    if (Array.isArray(input.calls)) {
      const results = await Promise.all(input.calls.map((call) => executeCall(call, defaultCwd, controller.signal)));
      process.stdout.write(`${JSON.stringify({ results })}\n`);
      return;
    }
    process.stdout.write(`${JSON.stringify(await executeCall(input, defaultCwd, controller.signal))}\n`);
  } finally {
    process.removeListener("SIGINT", abort);
    process.removeListener("SIGTERM", abort);
  }
}

main().catch((error) => {
  console.error(`pi-tools-0.73.1 protocol failure: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
