import { execFileSync } from "node:child_process"
import { mkdtempSync, writeFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import { join, resolve } from "node:path"

const root = resolve(new URL("..", import.meta.url).pathname)
const temp = mkdtempSync(join(tmpdir(), "breadboard-sdk-consumer-"))
const artifact = join(temp, "artifact")
try {
  execFileSync("node", [join(root, "scripts/pack-canonical.mjs"), artifact], { cwd: root, stdio: "inherit" })
  const tarball = join(artifact, "breadboard-sdk-0.3.0.tgz")
  writeFileSync(join(temp, "package.json"), JSON.stringify({ type: "module", dependencies: { "@breadboard/sdk": `file:${tarball}` }, devDependencies: { typescript: "^5.5.4" }, scripts: { test: "node consumer.mjs" } }, null, 2))
  writeFileSync(join(temp, "consumer.ts"), 'import { ApiError, createBreadboardClient, streamSessionEvents, type SessionEvent } from "@breadboard/sdk"; import { AcquireOwnerInput } from "@breadboard/sdk/internal"; const input: AcquireOwnerInput = {}; void input; const client = createBreadboardClient({ baseUrl: "http://fixture.test" }); void client.resolveModelRoles; void streamSessionEvents; void ApiError; const event: SessionEvent = { id: "x", type: "completion", session_id: "s", turn: null, timestamp: 0, payload: {} }; void event;')
  writeFileSync(join(temp, "tsconfig.json"), JSON.stringify({ compilerOptions: { target: "ES2022", module: "NodeNext", moduleResolution: "NodeNext", strict: true, noEmit: true }, files: ["consumer.ts"] }, null, 2))
  writeFileSync(join(temp, "consumer.mjs"), 'import assert from "node:assert/strict"; import { createBreadboardClient, streamSessionEvents } from "@breadboard/sdk"; let calls = []; globalThis.fetch = async (input, init) => { calls.push([String(input), init]); return new Response(JSON.stringify({ ok: true, role: "builder" }), { headers: { "content-type": "application/json" } }); }; const client = createBreadboardClient({ baseUrl: "http://fixture.test", authToken: "fixture-token" }); await client.resolveModelRoles({ model_roles: { builder: "fixture" } }); assert.equal(calls[0][1].headers.Authorization, "Bearer fixture-token"); calls = []; globalThis.fetch = async (_input, init) => { assert.equal(init.headers.Authorization, "Bearer fixture-token"); return new Response(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode("id: e1\\ndata: {\\"type\\":\\"completion\\",\\"session_id\\":\\"s\\",\\"payload\\":{}}\\n\\n")); controller.close(); } }), { headers: { "content-type": "text/event-stream" } }); }; const events = []; for await (const event of streamSessionEvents("s", { config: { baseUrl: "http://fixture.test", authToken: "fixture-token" } })) events.push(event); assert.equal(events[0].id, "e1"); console.log("clean consumer fixture passed");')
  execFileSync("npm", ["install", "--ignore-scripts", "--no-audit", "--no-fund"], { cwd: temp, stdio: "inherit" })
  execFileSync(join(root, "node_modules/.bin/tsc"), ["-p", join(temp, "tsconfig.json")], { cwd: temp, stdio: "inherit" })
  execFileSync("npm", ["test"], { cwd: temp, stdio: "inherit" })
} finally { rmSync(temp, { recursive: true, force: true }) }
