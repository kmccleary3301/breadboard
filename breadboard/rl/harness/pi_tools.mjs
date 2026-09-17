#!/usr/bin/env node
/**
 * Invoke one official Pi 0.57.1 built-in tool without starting Pi's agent loop.
 *
 * stdin:  {"tool_id":"read|bash|edit|write|grep|find|ls","arguments":{...}}
 * stdout: exactly one JSON tool result (including isError:true on rejection)
 * stderr: diagnostics only
 */

import { delimiter } from "node:path";
import { fileURLToPath } from "node:url";

// Pi captures its managed-binary directory while importing tools-manager.
process.env.PI_CODING_AGENT_DIR = fileURLToPath(new URL(".", import.meta.url));
process.env.PI_OFFLINE = "1";
const binaryDirectory = fileURLToPath(new URL("./bin", import.meta.url));
process.env.PATH = binaryDirectory + (process.env.PATH ? delimiter + process.env.PATH : "");

const {
  createBashTool,
  createEditTool,
  createFindTool,
  createGrepTool,
  createLsTool,
  createReadTool,
  createWriteTool,
} = await import("@mariozechner/pi-coding-agent");

const MAX_REQUEST_BYTES = 1024 * 1024;
const TOOL_FACTORIES = Object.freeze({
  bash: createBashTool,
  edit: createEditTool,
  find: createFindTool,
  grep: createGrepTool,
  ls: createLsTool,
  read: createReadTool,
  write: createWriteTool,
});
const TOOL_IDS = new Set(Object.keys(TOOL_FACTORIES));

function fail(message) {
  throw new Error(message);
}

async function readRequest() {
  const chunks = [];
  let bytes = 0;
  for await (const chunk of process.stdin) {
    const value = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    bytes += value.byteLength;
    if (bytes > MAX_REQUEST_BYTES) {
      fail(`request exceeds ${MAX_REQUEST_BYTES} bytes`);
    }
    chunks.push(value);
  }
  if (bytes === 0) {
    fail("request stdin is empty");
  }
  let request;
  try {
    request = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch (error) {
    fail(`request is not valid JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
  if (request === null || typeof request !== "object" || Array.isArray(request)) {
    fail("request must be a JSON object");
  }
  const toolId = request.tool_id;
  if (typeof toolId !== "string" || !TOOL_IDS.has(toolId)) {
    fail(`unknown tool_id: ${String(toolId)}`);
  }
  const argumentsValue = request.arguments;
  if (argumentsValue === null || typeof argumentsValue !== "object" || Array.isArray(argumentsValue)) {
    fail("arguments must be a JSON object");
  }
  return { toolId, argumentsValue };
}

async function main() {
  const request = await readRequest();
  const cwd = process.cwd();
  const tool = TOOL_FACTORIES[request.toolId](cwd);
  const controller = new AbortController();
  const abort = () => controller.abort();
  process.once("SIGINT", abort);
  process.once("SIGTERM", abort);
  try {
    const result = await tool.execute(
      `bb-native-${request.toolId}`,
      request.argumentsValue,
      controller.signal,
    );
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    // This matches Pi's agent-loop rejection conversion: the tool's genuine
    // message is retained, details stay an empty object, and isError marks the
    // result instead of fabricating a successful result.
    const message = error instanceof Error ? error.message : String(error);
    process.stdout.write(`${JSON.stringify({
      content: [{ type: "text", text: message }],
      details: {},
      isError: true,
    })}\n`);
  } finally {
    process.removeListener("SIGINT", abort);
    process.removeListener("SIGTERM", abort);
  }
}

main().catch((error) => {
  console.error(`pi-tools protocol failure: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
