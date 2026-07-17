import assert from "node:assert/strict"
import { execFile, spawn } from "node:child_process"
import { createServer } from "node:http"
import {
  chmod,
  cp,
  lstat,
  link,
  mkdir,
  mkdtemp,
  readFile,
  readdir,
  realpath,
  rename,
  rm,
  stat,
  symlink,
  unlink,
  writeFile,
} from "node:fs/promises"
import { tmpdir } from "node:os"
import { dirname, isAbsolute, join, relative, resolve } from "node:path"
import test from "node:test"
import { promisify } from "node:util"

import {
  REPLAY_RETENTION_MAX_AGE_MS,
  REPLAY_RETENTION_MAX_EVENTS,
  computeSessionReplayDigest,
  replayConfigurationDigest,
} from "../dist/session-runtime.js"
import {
  GateFailure,
  assertNoGitReplacementRefsForTest,
  computeApprovedBackendRuntimeCandidateForTest,
  readOwnedBackendProvenanceForTest,
  parseArgs,
  validateGateEvidence,
} from "../scripts/p30-bb89n14-gate.mjs"

const execFileAsync = promisify(execFile)
const SDK_ROOT = resolve(dirname(new URL(import.meta.url).pathname), "..")
const REPOSITORY_ROOT = resolve(SDK_ROOT, "../..")
const SCRIPT_PATH = join(SDK_ROOT, "scripts", "p30-bb89n14-gate.mjs")
const CLIENT_COMMIT = "2889272d7f56dbc5e03f4c770d447b513269060d"
const BACKEND_COMMIT = "a".repeat(40)
const EXPECTED_MODEL = "openai/gpt-live"
const MAX_CAPTURE_BYTES = 1024 * 1024
const DEADLINE_TOLERANCE_MS = 300

const fixtureFailure = (code) => {
  const error = new Error(code)
  error.name = "FixtureError"
  error.code = code
  throw error
}


let canonicalLoaderPythonPromise
const resolveCanonicalLoaderPython = () => {
  canonicalLoaderPythonPromise ??= (async () => {
    const configured = process.env.BREADBOARD_TEST_PYTHON
    const candidates = []
    if (typeof configured === "string" && isAbsolute(configured)) candidates.push(configured)
    for (const directory of ["/opt/homebrew/bin", "/usr/local/bin"]) {
      const names = await readdir(directory).catch(() => [])
      for (const name of names.filter((value) => /^python3(?:\.\d+)?$/.test(value)).sort().reverse()) {
        candidates.push(join(directory, name))
      }
    }
    candidates.push("/usr/bin/python3")
    for (const candidate of [...new Set(candidates)]) {
      try {
        await execFileAsync(candidate, ["-I", "-c", "import jsonschema, yaml"], {
          env: { LANG: "C", LC_ALL: "C", PATH: "/usr/bin:/bin" },
          timeout: 5_000,
          maxBuffer: 1024,
        })
        return candidate
      } catch {
        // Continue through the bounded absolute candidate set.
      }
    }
    fixtureFailure("fixture_canonical_loader_python_unavailable")
  })()
  return canonicalLoaderPythonPromise
}
const readRequestBody = async (request) => {
  const chunks = []
  let length = 0
  for await (const chunk of request) {
    length += chunk.length
    assert.ok(length <= MAX_CAPTURE_BYTES)
    chunks.push(chunk)
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"))
}
const exactKeys = (value, expected, code) => {
  if (
    value === null
    || typeof value !== "object"
    || Array.isArray(value)
    || JSON.stringify(Object.keys(value).sort()) !== JSON.stringify([...expected].sort())
  ) fixtureFailure(code)
}

const readReplayFixture = async (path, ordinal) => {
  if (!isAbsolute(path)) fixtureFailure("fixture_replay_path_not_absolute")
  const bytes = await readFile(path)
  if (bytes.length === 0 || bytes.length > 4096) fixtureFailure("fixture_replay_size")
  const source = bytes.toString("utf8")
  if (!source.endsWith("\n")) fixtureFailure("fixture_replay_framing")
  const lines = source.slice(0, -1).split("\n")
  if (lines.length !== 3 || lines.some((line) => line.length === 0)) {
    fixtureFailure("fixture_replay_record_count")
  }
  let records
  try {
    records = lines.map((line) => JSON.parse(line))
  } catch {
    fixtureFailure("fixture_replay_json")
  }
  exactKeys(records[0], ["type", "payload", "delay_ms"], "fixture_replay_assistant_schema")
  exactKeys(records[0].payload, ["text"], "fixture_replay_assistant_payload")
  if (
    records[0].type !== "assistant_message"
    || records[0].payload.text !== `synthetic-control-${ordinal}`
    || !Number.isSafeInteger(records[0].delay_ms)
    || records[0].delay_ms !== (ordinal === 1 ? 10_000 : 25)
  ) fixtureFailure("fixture_replay_delayed_assistant")
  for (const [index, expectedType] of [[1, "completion"], [2, "run_finished"]]) {
    const record = records[index]
    exactKeys(record, ["type", "payload"], "fixture_replay_terminal_schema")
    exactKeys(record.payload, ["completed"], "fixture_replay_terminal_payload")
    if (record.type !== expectedType || record.payload.completed !== true) {
      fixtureFailure("fixture_replay_terminal_value")
    }
  }
  return records
}


const replayFacts = async (headSequence, headEventId) => {
  const facts = {
    replayRetention: {
      maxEvents: REPLAY_RETENTION_MAX_EVENTS,
      maxAgeMs: REPLAY_RETENTION_MAX_AGE_MS,
      configurationDigest: replayConfigurationDigest,
    },
    earliestRetainedSequence: headSequence === 0 ? null : 1,
    earliestRetainedEventId: headSequence === 0 ? null : "event-1",
    headSequence,
    headEventId,
    retainedHistory: "complete",
  }
  return { ...facts, sessionReplayContractDigest: await computeSessionReplayDigest(facts) }
}

const wireTerminal = (receipt) => ({
  input_id: receipt.input_id,
  turn_id: receipt.turn_id,
  outcome: "completed",
  original_disposition: receipt.original_disposition,
})

class FakeSession {
  constructor(server, id, body) {
    this.server = server
    this.id = id
    this.body = body
    this.isControl = body.metadata?.proof === "provider-free-synthetic-control"
    this.model = this.isControl ? "replay" : (server.options.modelMismatch ?? body.metadata?.model)
    this.events = []
    this.receipts = []
    this.subscribers = new Set()
    this.turnAdmission = "idle"
    this.activeTurnId = null
    this.queuedTurnCount = 0
    this.terminals = []
  }

  append(type, payload, receipt, correlation = true) {
    const sequence = this.events.length + 1
    const event = {
      stable_cursor: true,
      id: `event-${sequence}`,
      seq: sequence,
      type,
      session_id: this.id,
      timestamp_ms: 1_789_000_000_000 + sequence,
      ...(correlation ? { input_id: receipt.input_id, turn_id: receipt.turn_id } : {}),
      payload,
    }
    this.events.push(event)
    for (const subscriber of this.subscribers) this.sendEvent(subscriber, event)
    return event
  }

  sendEvent(subscriber, event) {
    if (event.seq <= subscriber.sentSequence) return
    subscriber.response.write(`id: ${event.seq}\ndata: ${JSON.stringify(event)}\n\n`)
    subscriber.sentSequence = event.seq
  }

  async snapshot() {
    const head = this.events.at(-1) ?? null
    return {
      session_id: this.id,
      status: "running",
      created_at: "2026-07-17T00:00:00Z",
      last_activity_at: "2026-07-17T00:00:01Z",
      model: this.model,
      mode: this.server.options.modeEcho ?? "interactive",
      turn_admission: this.turnAdmission,
      active_turn_id: this.activeTurnId,
      queued_turn_count: this.queuedTurnCount,
      terminalTurns: this.terminals,
      ...(await replayFacts(this.events.length, head?.id ?? null)),
    }
  }

  async openEvents(request, response) {
    response.writeHead(200, {
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
      connection: "keep-alive",
    })
    const requestedId = request.headers["last-event-id"] ?? new URL(request.url, this.server.origin).searchParams.get("from_id")
    const requestedSequence = requestedId === null || requestedId === undefined
      ? 0
      : Number(String(requestedId).replace(/^event-/, ""))
    const head = this.events.at(-1) ?? null
    const open = {
      stable_cursor: false,
      type: "stream.open",
      session_id: this.id,
      timestamp_ms: 1_789_000_000_000,
      payload: await replayFacts(this.events.length, head?.id ?? null),
    }
    response.write(`data: ${JSON.stringify(open)}\n\n`)
    if (this.server.options.streamGapCode !== undefined) {
      response.write(`data: ${JSON.stringify({
        stable_cursor: false,
        type: "stream.gap",
        session_id: this.id,
        timestamp_ms: 1_789_000_000_001,
        payload: { code: this.server.options.streamGapCode },
      })}\n\n`)
      return
    }
    const subscriber = { response, sentSequence: Number.isSafeInteger(requestedSequence) ? requestedSequence : 0 }
    this.subscribers.add(subscriber)
    response.on("close", () => this.subscribers.delete(subscriber))
    for (const event of this.events) this.sendEvent(subscriber, event)
  }

  mainSubmit(body) {
    const receipt = {
      status: "accepted",
      client_message_id: body.client_message_id,
      input_id: this.server.options.inputIdEcho ?? "input-main",
      turn_id: "turn-main",
      disposition: "started",
      original_disposition: "started",
    }
    this.receipts.push(receipt)
    if (!this.server.options.slowStream) {
      const nonce = `${this.server.options.noncePrefix ?? ""}${body.content.split(": ").at(-1)}${this.server.options.nonceSuffix ?? ""}`
      this.append("user_message", { text: body.content }, receipt)
      this.append("turn_start", this.server.options.unsafeTurnPayload ?? {}, receipt)
      this.append("assistant_delta", { text: nonce }, receipt)
      this.append("assistant_message", {
        text: this.server.options.oversizedAssistant ? "x".repeat(5000) : nonce,
      }, receipt)
      this.append("turn_completed", {}, receipt)
      this.terminals = [wireTerminal(receipt)]
      if (this.server.options.duplicateTerminal) this.append("turn_completed", {}, receipt)
      if (this.server.options.conflictingTerminal) this.append("turn_failed", { error: { code: "provider_failed" } }, receipt)
    }
    return receipt
  }

  async controlSubmit(body) {
    const number = this.receipts.length + 1
    const disposition = number === 1 ? "started" : "queued"
    const receipt = {
      status: "accepted",
      client_message_id: body.client_message_id,
      input_id: `input-control-${number}`,
      turn_id: `turn-control-${number}`,
      disposition,
      original_disposition: disposition,
    }
    const fixturePath = body.content.replace(/^replay:/, "")
    if (number === 1 && this.server.options.controlFixtureMutation === "malformed") {
      await writeFile(fixturePath, "{\n", { mode: 0o600 })
    } else if (number === 1 && this.server.options.controlFixtureMutation === "zero-delay") {
      const source = await readFile(fixturePath, "utf8")
      await writeFile(fixturePath, source.replace(`"delay_ms":10000`, `"delay_ms":0`), { mode: 0o600 })
    }
    const fixture = await readReplayFixture(fixturePath, number)
    this.receipts.push(receipt)
    this.server.fixturePaths.push(fixturePath)
    this.replayFixtures ??= []
    this.replayFixtures.push(fixture)
    if (number === 1) {
      this.turnAdmission = "active"
      this.activeTurnId = receipt.turn_id
      this.append("user_message", { text: body.content }, receipt)
      this.append("turn_start", {}, receipt)
    } else if (number === 2) {
      this.queuedTurnCount = 1
    } else if (number === 3) {
      this.queuedTurnCount = 2
      const [first, second, third] = this.receipts
      for (const [index, current] of [first, second, third].entries()) {
        const replay = this.replayFixtures[index]
        if (index > 0) {
          this.append("user_message", { text: `replay:${this.server.fixturePaths[index]}` }, current)
          this.append("turn_start", {}, current)
        }
        let completionSeen = false
        for (const record of replay) {
          if (record.type === "assistant_message") {
            this.append("assistant_message", { text: record.payload.text }, current)
          } else if (record.type === "completion") {
            completionSeen = record.payload.completed
          } else if (record.type === "run_finished") {
            if (!completionSeen || record.payload.completed !== true) fixtureFailure("fixture_replay_transition_order")
            this.append("turn_completed", {}, current)
          }
        }
      }
      this.terminals = this.receipts.map(wireTerminal)
      this.turnAdmission = "idle"
      this.activeTurnId = null
      this.queuedTurnCount = 0
    }
    return receipt
  }

  submit(body) {
    return this.isControl ? this.controlSubmit(body) : this.mainSubmit(body)
  }
}

class FakeCanonicalServer {
  constructor(options = {}) {
    this.options = options
    this.requests = []
    this.creates = []
    this.fixturePaths = []
    this.loadedConfigurations = []
    this.fixtureErrors = []
    this.sessions = new Map()
    this.sockets = new Set()
    this.server = createServer((request, response) => {
      this.handle(request, response).catch((error) => {
        this.fixtureErrors.push({
          name: typeof error?.name === "string" && /^[A-Za-z][A-Za-z0-9]*$/.test(error.name) ? error.name : "Error",
          code: (
            typeof error?.message === "string" && /^fixture_[a-z0-9_]{1,96}$/.test(error.message)
              ? error.message
              : typeof error?.code === "string" && /^[A-Za-z0-9_.-]{1,128}$/.test(error.code)
                ? error.code
                : "redacted"
          ),
        })
        if (!response.headersSent) response.writeHead(500, { "content-type": "application/json" })
        response.end(JSON.stringify({ error: "server-error" }))
      })
    })
    this.server.on("connection", (socket) => {
      this.sockets.add(socket)
      socket.on("close", () => this.sockets.delete(socket))
    })
  }

  async start() {
    await new Promise((resolveStart) => this.server.listen(0, "127.0.0.1", resolveStart))
    const address = this.server.address()
    this.upstreamOrigin = `http://127.0.0.1:${address.port}`
    if (this.options.directFake === true) {
      this.origin = this.upstreamOrigin
      this.commit = BACKEND_COMMIT
    } else {
      this.proxy = await startTrackedPythonProxy(this.upstreamOrigin)
      this.origin = this.proxy.origin
      this.commit = this.proxy.commit
    }
    return this
  }

  async stop() {
    let proxyFailure = null
    try {
      if (this.proxy) await this.proxy.stop()
    } catch (error) {
      proxyFailure = error
    } finally {
      for (const socket of this.sockets) socket.destroy()
      this.server.closeAllConnections?.()
      await new Promise((resolveStop) => this.server.close(resolveStop))
    }
    if (proxyFailure !== null) throw proxyFailure
  }

  async handle(request, response) {
    const url = new URL(request.url, this.origin)
    const requestTrace = { method: request.method, path: url.pathname, authorization: request.headers.authorization, status: null }
    this.requests.push(requestTrace)
    response.once("finish", () => {
      requestTrace.status = response.statusCode
    })
    if (request.method === "GET" && url.pathname === "/v1/status") {
      if (this.options.onStatus) await this.options.onStatus()
      const servedRevision = { commit: this.commit }
      if (this.options.dirty !== undefined) servedRevision.dirty = this.options.dirty
      else servedRevision.dirty = false
      if (this.options.stallStatusBody) {
        response.writeHead(200, { "content-type": "application/json" })
        response.write('{"served_revision":')
        return
      }
      if (this.options.oversizedStatusBody) {
        response.writeHead(200, { "content-type": "application/json" })
        response.end("x".repeat(64 * 1024 + 1))
        return
      }
      response.writeHead(200, { "content-type": "application/json" })
      response.end(JSON.stringify({
        served_revision: servedRevision,
        protocol_version: this.options.protocolVersionEcho ?? "e4-test",
        engine_version: this.options.engineVersionEcho ?? "fake-canonical",
      }))
      return
    }
    if (request.method === "POST" && url.pathname === "/v1/sessions") {
      const body = await readRequestBody(request)
      const id = `session-${this.sessions.size + 1}`
      const configInfo = await stat(body.config_path)
      const configBytes = await readFile(body.config_path)
      const effectiveConfig = JSON.parse(configBytes)
      exactKeys(effectiveConfig, ["extends", "provider_tools", "providers"], "fixture_effective_config_schema")
      exactKeys(effectiveConfig.provider_tools, ["responses_stateful", "store"], "fixture_effective_provider_tools_schema")
      exactKeys(effectiveConfig.providers, ["routing"], "fixture_effective_providers_schema")
      exactKeys(effectiveConfig.providers.routing, ["disable_stream_on_probe_failure"], "fixture_effective_routing_schema")
      if (typeof effectiveConfig.extends !== "string") fixtureFailure("fixture_effective_config_extends")
      if (effectiveConfig.provider_tools.store !== false) fixtureFailure("fixture_effective_config_store")
      if (effectiveConfig.provider_tools.responses_stateful !== false) fixtureFailure("fixture_effective_config_conversation_state")
      if (effectiveConfig.providers.routing.disable_stream_on_probe_failure !== false) fixtureFailure("fixture_effective_config_streaming")
      const mirroredConfig = JSON.parse(await readFile(effectiveConfig.extends, "utf8"))
      if (this.options.verifyConfigClosure) {
        if (!Array.isArray(mirroredConfig.extends)) fixtureFailure("fixture_closure_extends_array")
        if (JSON.stringify(JSON.parse(await readFile(mirroredConfig.extends[0], "utf8"))) !== '{"version":2}') {
          fixtureFailure("fixture_closure_extends_content")
        }
        if (await readFile(mirroredConfig.prompts.system, "utf8") !== "bound prompt\n") {
          fixtureFailure("fixture_closure_prompt_content")
        }
        if (!(await stat(mirroredConfig.tools.defs_dir)).isDirectory()) {
          fixtureFailure("fixture_closure_defs_directory")
        }
      }
      if (this.options.verifyActualLoader) {
        this.loadedConfigurations.push(await loadWithCanonicalConfigStack(
          body.config_path,
          this.options.loaderCwd,
          this.options.loaderForbiddenText,
        ))
      }
      this.creates.push({ body, configMode: configInfo.mode & 0o777, configBytes, workspace: body.workspace })
      if (this.options.mutateSnapshotDuringCreateCount === this.creates.length) {
        await chmod(body.config_path, 0o600)
        await writeFile(body.config_path, Buffer.from('{"version":999}\n', "utf8"), { mode: 0o600 })
        await writeFile(body.config_path, configBytes, { mode: 0o600 })
        await chmod(body.config_path, 0o400)
      }
      if (this.options.lockSnapshotAfterCreateCount === this.creates.length) {
        let cursor = dirname(body.config_path)
        while (dirname(cursor) !== cursor && cursor.split("/").at(-1) !== "closure") cursor = dirname(cursor)
        const snapshotRoot = dirname(cursor)
        this.lockedSnapshotRoot = `${snapshotRoot}.locked`
        this.lockedSnapshotLink = snapshotRoot
        await rename(snapshotRoot, this.lockedSnapshotRoot)
        await symlink(this.lockedSnapshotRoot, this.lockedSnapshotLink)
      }
      const session = new FakeSession(this, id, body)
      this.sessions.set(id, session)
      response.writeHead(200, { "content-type": "application/json" })
      response.end(JSON.stringify({ session_id: id, status: "starting", created_at: "2026-07-17T00:00:00Z" }))
      return
    }
    const match = /^\/v1\/sessions\/([^/]+)(?:\/(events|input))?$/.exec(url.pathname)
    if (match) {
      const session = this.sessions.get(match[1])
      if (!session) {
        response.writeHead(404, { "content-type": "application/json" })
        response.end(JSON.stringify({ code: "session_not_found" }))
        return
      }
      if (request.method === "GET" && match[2] === "events") {
        await session.openEvents(request, response)
        return
      }
      if (request.method === "POST" && match[2] === "input") {
        const receipt = await session.submit(await readRequestBody(request))
        response.writeHead(202, { "content-type": "application/json" })
        response.end(JSON.stringify(receipt))
        return
      }
      if (request.method === "GET" && match[2] === undefined) {
        response.writeHead(200, { "content-type": "application/json" })
        response.end(JSON.stringify(await session.snapshot()))
        return
      }
    }
    response.writeHead(404, { "content-type": "application/json" })
    response.end(JSON.stringify({ code: "unexpected_route" }))
  }
}

const makeRun = async (name, { separateOutput = false } = {}) => {
  const root = await mkdtemp(join(tmpdir(), `bb89n14-${name}-`))
  await chmod(root, 0o700)
  const config = join(root, "config.yaml")
  const workspace = join(root, "workspace")
  const outputDirectory = separateOutput ? join(root, "output") : root
  const output = join(outputDirectory, "evidence.json")
  await writeFile(join(root, "base.yaml"), "version: 2\n", { mode: 0o600 })
  await writeFile(join(root, "prompt.md"), "bound prompt\n", { mode: 0o600 })
  await writeFile(config, "extends: base.yaml\nprompts:\n  system: prompt.md\nproviders:\n  default_model: openai/gpt-live\n", { mode: 0o600 })
  await mkdir(workspace, { mode: 0o700 })
  if (separateOutput) await mkdir(outputDirectory, { mode: 0o700 })
  return { root, config, workspace, outputDirectory, output }
}

const gateArguments = (run, server, extras = []) => [
  "--base-url", server.origin,
  "--config-path", run.config,
  "--backend-root", server.proxy?.root ?? REPOSITORY_ROOT,
  "--backend-python", server.proxy?.python ?? "/usr/bin/python3",
  "--workspace", run.workspace,
  "--output", run.output,
  "--expected-backend-commit", server.commit,
  "--expected-client-commit", CLIENT_COMMIT,
  "--expected-provider-model", EXPECTED_MODEL,
  ...extras,
]

const runGate = async (arguments_, environment = {}, options = {}) => {
  const startedAt = Date.now()
  const childEnvironment = { ...process.env }
  delete childEnvironment.BB89N14_AUTH_TOKEN
  Object.assign(childEnvironment, environment)
  const scriptPath = options.scriptPath ?? SCRIPT_PATH
  const entrypoint = options.production === true ? "main" : "runSyntheticGateForTest"
  const invocation = [
    "--input-type=module",
    "--eval",
    `const module = await import(${JSON.stringify(new URL(`file://${scriptPath}`).href)}); process.exitCode = await module[${JSON.stringify(entrypoint)}](process.argv.slice(1))`,
    "--",
    ...arguments_,
  ]
  const child = spawn(process.execPath, invocation, {
    cwd: options.cwd ?? SDK_ROOT,
    env: childEnvironment,
    stdio: ["ignore", "pipe", "pipe"],
  })
  let stdout = ""
  let stderr = ""
  child.stdout.on("data", (chunk) => {
    stdout += chunk.toString("utf8")
    if (stdout.length > MAX_CAPTURE_BYTES) child.kill("SIGKILL")
  })
  child.stderr.on("data", (chunk) => {
    stderr += chunk.toString("utf8")
    if (stderr.length > MAX_CAPTURE_BYTES) child.kill("SIGKILL")
  })
  const code = await new Promise((resolveExit, rejectExit) => {
    child.on("error", rejectExit)
    child.on("close", resolveExit)
  })
  return { code, stdout, stderr, elapsedMs: Date.now() - startedAt, spawnargs: child.spawnargs }
}

const assertNoOutput = async (path) => {
  await assert.rejects(readFile(path), (error) => error.code === "ENOENT")
}
const unlockFixtureTree = async (path) => {
  const info = await lstat(path)
  assert.equal(info.isSymbolicLink(), false)
  if (info.isDirectory()) {
    await chmod(path, 0o700)
    for (const name of await readdir(path)) await unlockFixtureTree(join(path, name))
  } else {
    assert.equal(info.isFile(), true)
    await chmod(path, 0o600)
  }
}


const parseFailure = (result) => {
  assert.equal(result.code, 1, JSON.stringify(result))
  assert.equal(result.stdout, "")
  const line = JSON.parse(result.stderr)
  assert.equal(line.ok, false)
  return line.error
}
const CANONICAL_CONFIG_LOADER = String.raw`
import hashlib, json, os, sys, tempfile

control = json.loads(sys.stdin.buffer.read())
sys.path.insert(0, control["repositoryRoot"])
os.environ["BREADBOARD_CONFIG_AUTHORITY"] = "config"
from agentic_coder_prototype.compilation.system_prompt_compiler import SystemPromptCompiler
from agentic_coder_prototype.compilation.tool_yaml_loader import load_yaml_tools
from agentic_coder_prototype.compilation.v2_loader import _config_resolution_base_dirs, load_agent_config

config_path = control["configPath"]
config = load_agent_config(config_path)
tools = config.get("tools") or {}
registry = tools.get("registry") or {}
tool_paths = list(registry.get("paths") or [])
if not tool_paths and tools.get("defs_dir"):
    tool_paths = [tools["defs_dir"]]
tool_count = 0
for path in tool_paths:
    tool_count += len(load_yaml_tools(str(path)).tools)
with tempfile.TemporaryDirectory(prefix="bb89n14-prompt-cache-") as cache:
    compiled = SystemPromptCompiler(cache_dir=cache).compile_v2_prompts(
        config,
        "build",
        [],
        [],
        prompt_base_dirs=list(_config_resolution_base_dirs(config_path)),
    )
prompt_paths = []
for pack in ((config.get("prompts") or {}).get("packs") or {}).values():
    if isinstance(pack, dict):
        prompt_paths.extend(value for value in pack.values() if isinstance(value, str) and os.path.isabs(value))
cursor = os.path.dirname(config_path)
while os.path.basename(cursor) != "closure" and os.path.dirname(cursor) != cursor:
    cursor = os.path.dirname(cursor)
snapshot_root = os.path.dirname(cursor)
private_references = all(
    os.path.commonpath((snapshot_root, os.path.normpath(path))) == snapshot_root
    for path in [config_path, *tool_paths, *prompt_paths]
)
def short_resolved_leaf(value):
    if not isinstance(value, str):
        return None
    if os.path.isfile(value):
        with open(value, "r", encoding="utf-8") as leaf_file:
            value = leaf_file.read(257)
    return value if len(value.encode("utf-8")) <= 256 else None
base_pack = (((config.get("prompts") or {}).get("packs") or {}).get("base") or {})
inline_literal = short_resolved_leaf(base_pack.get("inline_literal"))
resolved_system_leaf = short_resolved_leaf(base_pack.get("system"))
system_bytes = compiled["system"].encode("utf-8")
forbidden_text = control.get("forbiddenText")
result = {
    "toolCount": tool_count,
    "systemPromptSha256": hashlib.sha256(system_bytes).hexdigest(),
    "systemPromptBytes": len(system_bytes),
    "resolvedSystemLeaf": resolved_system_leaf,
    "forbiddenTextObserved": isinstance(forbidden_text, str) and forbidden_text in compiled["system"],
    "privateReferences": private_references,
    "inlineLiteral": inline_literal,
    "providerStore": (config.get("provider_tools") or {}).get("store"),
    "providerResponsesStateful": (config.get("provider_tools") or {}).get("responses_stateful"),
    "disableStreamOnProbeFailure": (((config.get("providers") or {}).get("routing") or {}).get("disable_stream_on_probe_failure")),
}
sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")))
`
const PYTHON_AUTH_CANARY = "AUTH_CANARY_DO_NOT_PERSIST_89N14"
const TRACKED_PYTHON_TCP_PROXY = String.raw`
import argparse
import json
import socket
import sys
import threading

def copy_stream(source, destination):
    try:
        while True:
            chunk = source.recv(65536)
            if not chunk:
                break
            destination.sendall(chunk)
    except OSError:
        pass
    finally:
        try:
            destination.shutdown(socket.SHUT_WR)
        except OSError:
            pass

def proxy_connection(client, upstream_host, upstream_port):
    upstream = socket.create_connection((upstream_host, upstream_port))
    threads = [
        threading.Thread(target=copy_stream, args=(client, upstream), daemon=True),
        threading.Thread(target=copy_stream, args=(upstream, client), daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    client.close()
    upstream.close()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    control = json.loads(sys.stdin.readline())
    upstream_host = control["upstreamHost"]
    upstream_port = int(control["upstreamPort"])
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.host, args.port))
    listener.listen()
    print("READY", flush=True)
    while True:
        client, _ = listener.accept()
        threading.Thread(
            target=proxy_connection,
            args=(client, upstream_host, upstream_port),
            daemon=True,
        ).start()

if __name__ == "__main__":
    main()
`


const allocateLoopbackPort = async () => {
  const reservation = createServer()
  await new Promise((resolveListen) => reservation.listen(0, "127.0.0.1", resolveListen))
  const address = reservation.address()
  await new Promise((resolveClose) => reservation.close(resolveClose))
  return address.port
}

const startTrackedPythonProxy = async (upstreamOrigin) => {
  const upstream = new URL(upstreamOrigin)
  const root = await mkdtemp(join(tmpdir(), "bb89n14-tracked-proxy-"))
  const entrypoint = join(root, "tcp_proxy.py")
  const gitEnvironment = {
    LANG: "C",
    LC_ALL: "C",
    PATH: "/usr/bin:/bin",
    GIT_AUTHOR_NAME: "bb89n14-test",
    GIT_AUTHOR_EMAIL: "bb89n14-test@example.invalid",
    GIT_COMMITTER_NAME: "bb89n14-test",
    GIT_COMMITTER_EMAIL: "bb89n14-test@example.invalid",
  }
  await writeFile(entrypoint, TRACKED_PYTHON_TCP_PROXY, { mode: 0o600 })
  await execFileAsync("/usr/bin/git", ["init", "-q", root], { env: gitEnvironment })
  await execFileAsync("/usr/bin/git", ["-C", root, "add", "--", "tcp_proxy.py"], { env: gitEnvironment })
  await execFileAsync(
    "/usr/bin/git",
    ["-C", root, "commit", "-q", "-m", "tracked TCP proxy fixture"],
    { env: gitEnvironment },
  )
  const { stdout: commitOutput } = await execFileAsync(
    "/usr/bin/git",
    ["-C", root, "rev-parse", "HEAD"],
    { env: gitEnvironment },
  )
  const commit = commitOutput.trim()
  const port = await allocateLoopbackPort()
  const python = await resolveCanonicalLoaderPython()
  const child = spawn(
    python,
    [entrypoint, "--host", "127.0.0.1", "--port", String(port)],
    {
      cwd: root,
      env: {
        LANG: "C",
        LC_ALL: "C",
        PATH: "/usr/bin:/bin",
        PYTHONDONTWRITEBYTECODE: "1",
      },
      stdio: ["pipe", "pipe", "pipe"],
    },
  )
  child.stdin.end(JSON.stringify({
    upstreamHost: upstream.hostname,
    upstreamPort: Number(upstream.port),
  }) + "\n")
  let stderr = ""
  child.stderr.on("data", (chunk) => {
    stderr += chunk.toString("utf8")
    if (stderr.length > MAX_CAPTURE_BYTES) child.kill("SIGKILL")
  })
  const exit = new Promise((resolveExit) => {
    child.once("error", (error) => resolveExit({ error, code: null, signal: null }))
    child.once("close", (code, signal) => resolveExit({ error: null, code, signal }))
  })
  try {
    await new Promise((resolveReady, rejectReady) => {
      const timeout = setTimeout(
        () => rejectReady(new Error(`tracked_python_fixture_timeout:${stderr}`)),
        5000,
      )
      child.stdout.on("data", (chunk) => {
        if (!chunk.toString("utf8").includes("READY")) return
        clearTimeout(timeout)
        resolveReady()
      })
      child.once("close", (code, signal) => {
        clearTimeout(timeout)
        rejectReady(new Error(`tracked_python_fixture_exit:${code}:${signal}:${stderr}`))
      })
    })
  } catch (error) {
    child.kill("SIGKILL")
    await exit
    await rm(root, { recursive: true, force: true })
    throw error
  }
  return {
    root: await realpath(root),
    entrypoint,
    commit,
    python,
    origin: `http://127.0.0.1:${port}`,
    async stop() {
      child.stdin.destroy()
      child.kill("SIGTERM")
      let timeout = null
      try {
        const exited = await Promise.race([
          exit.then(() => true),
          new Promise((resolveTimeout) => {
            timeout = setTimeout(() => resolveTimeout(false), 2000)
          }),
        ])
        if (!exited) {
          child.kill("SIGKILL")
          await exit
        }
      } finally {
        clearTimeout(timeout)
        await rm(root, { recursive: true, force: true })
      }
    },
  }
}

const loadWithCanonicalConfigStack = async (configPath, cwd = REPOSITORY_ROOT, forbiddenText = null) => {
  const python = await resolveCanonicalLoaderPython()
  const child = spawn(python, ["-I", "-c", CANONICAL_CONFIG_LOADER], {
    cwd,
    env: { LANG: "C", LC_ALL: "C", PATH: "/usr/bin:/bin" },
    stdio: ["pipe", "pipe", "pipe"],
  })
  child.stdin.end(JSON.stringify({ repositoryRoot: REPOSITORY_ROOT, configPath, forbiddenText }))
  const stdout = []
  const stderr = []
  let bytes = 0
  for (const [stream, chunks] of [[child.stdout, stdout], [child.stderr, stderr]]) {
    stream.on("data", (chunk) => {
      bytes += chunk.byteLength
      if (bytes > MAX_CAPTURE_BYTES) child.kill("SIGKILL")
      else chunks.push(chunk)
    })
  }
  const code = await new Promise((resolveExit, rejectExit) => {
    child.once("error", rejectExit)
    child.once("close", resolveExit)
  })
  if (code !== 0) fixtureFailure("fixture_canonical_loader_process")
  return JSON.parse(Buffer.concat(stdout).toString("utf8"))
}

const makePrivateRepositoryFixture = async (name) => {
  const parent = await mkdtemp(join(tmpdir(), `bb89n14-private-${name}-`))
  const root = join(parent, "repository")
  await mkdir(join(parent, "other_harness_refs"), { mode: 0o700 })
  const gitEnvironment = {
    LANG: "C",
    LC_ALL: "C",
    PATH: "/usr/bin:/bin",
    GIT_AUTHOR_NAME: "bb89n14-test",
    GIT_AUTHOR_EMAIL: "bb89n14-test@example.invalid",
    GIT_COMMITTER_NAME: "bb89n14-test",
    GIT_COMMITTER_EMAIL: "bb89n14-test@example.invalid",
  }
  await execFileAsync("/usr/bin/git", ["clone", "--shared", "--no-checkout", REPOSITORY_ROOT, root], { env: gitEnvironment })
  await mkdir(join(root, "sdk"), { recursive: true, mode: 0o700 })
  const sdkRoot = join(root, "sdk", "ts")
  await cp(SDK_ROOT, sdkRoot, { recursive: true, preserveTimestamps: true })
  await mkdir(join(root, "implementations", "tools"), { recursive: true, mode: 0o700 })
  await cp(
    join(REPOSITORY_ROOT, "implementations", "tools", "defs"),
    join(root, "implementations", "tools", "defs"),
    { recursive: true },
  )
  const { stdout: tree } = await execFileAsync("/usr/bin/git", ["rev-parse", `${CLIENT_COMMIT}^{tree}`], { cwd: root, env: gitEnvironment })
  const { stdout: descendant } = await execFileAsync(
    "/usr/bin/git",
    ["commit-tree", tree.trim(), "-p", CLIENT_COMMIT, "-m", "private descendant fixture"],
    { cwd: root, env: gitEnvironment },
  )
  await execFileAsync("/usr/bin/git", ["update-ref", "HEAD", descendant.trim()], { cwd: root, env: gitEnvironment })
  return { parent, root, sdkRoot, scriptPath: join(sdkRoot, "scripts", "p30-bb89n14-gate.mjs") }
}


const prepareCanonicalConfigurationFixture = async (fixture) => {
  const agentConfigs = join(fixture.root, "agent_configs")
  const nestedConfigs = join(agentConfigs, "nested")
  const miscConfigs = join(agentConfigs, "misc")
  const canonicalBaseConfig = join(miscConfigs, "base_v2.yaml")
  const promptDirectory = join(fixture.root, "implementations", "system_prompts")
  const toolRoot = join(fixture.root, "implementations", "tools")
  const toolDirectory = join(toolRoot, "defs_cc")
  const externalPromptPath = join(fixture.parent, "other_harness_refs", "codex", "codex-rs", "core", "gpt_5_1_prompt.md")
  await mkdir(nestedConfigs, { recursive: true, mode: 0o700 })
  await mkdir(miscConfigs, { recursive: true, mode: 0o700 })
  await mkdir(dirname(promptDirectory), { recursive: true, mode: 0o700 })
  await mkdir(toolRoot, { recursive: true, mode: 0o700 })
  await mkdir(dirname(externalPromptPath), { recursive: true, mode: 0o700 })
  await writeFile(externalPromptPath, "trusted external prompt\n", { mode: 0o600 })
  await cp(join(REPOSITORY_ROOT, "implementations", "system_prompts"), promptDirectory, { recursive: true })
  await cp(join(REPOSITORY_ROOT, "implementations", "tools", "defs"), join(toolRoot, "defs"), { recursive: true })
  await cp(join(REPOSITORY_ROOT, "implementations", "tools", "defs_cc"), toolDirectory, { recursive: true })
  await cp(
    join(REPOSITORY_ROOT, "agent_configs", "misc", "base_v2.yaml"),
    canonicalBaseConfig,
  )
  const canonical = await readFile(join(REPOSITORY_ROOT, "agent_configs", "misc", "claude_code_haiku45_c_fs_v2.yaml"), "utf8")
  const canonicalBody = canonical
    .split("\n")
    .slice(1)
    .join("\n")
    .replace(
      "      compact: implementations/system_prompts/claude_code/system-vendor-logged.prompt.md\n",
      "      compact: implementations/system_prompts/claude_code/system-vendor-logged.prompt.md\n      external_probe: ../../../other_harness_refs/codex/codex-rs/core/gpt_5_1_prompt.md\n      inline_literal: Use A/B\n",
    )
    .replace("provider_tools:\n", "provider_tools:\n  store: true\n  responses_stateful: true\n")
  const topConfig = join(miscConfigs, "gate-top.yaml")
  const nestedConfig = join(nestedConfigs, "gate-nested.yaml")
  const baseConfig = join(nestedConfigs, "base.yaml")
  const deepConfig = join(nestedConfigs, "deep.yaml")
  await writeFile(topConfig, `extends:\n  - ../nested/base.yaml\n  - base_v2.yaml\n${canonicalBody}`, { mode: 0o600 })
  await writeFile(nestedConfig, `extends:\n  - base.yaml\n  - ../misc/base_v2.yaml\n${canonicalBody}`, { mode: 0o600 })
  await writeFile(baseConfig, "extends: deep.yaml\n", { mode: 0o600 })
  await writeFile(deepConfig, "{}\n", { mode: 0o600 })
  const toolName = (await readdir(toolDirectory)).filter((name) => /\.ya?ml$/.test(name)).sort()[0]
  assert.ok(toolName)
  return {
    topConfig,
    nestedConfig,
    baseConfig,
    deepConfig,
    promptPath: join(promptDirectory, "claude_code", "system-vendor-logged.prompt.md"),
    externalPromptPath,
    toolPath: join(toolDirectory, toolName),
    canonicalBaseConfig,
    topBytes: await readFile(topConfig),
    nestedBytes: await readFile(nestedConfig),
    canonicalBaseBytes: await readFile(canonicalBaseConfig),
  }
}
const makeCleanBackendWorktree = async () => {
  const parent = await mkdtemp(join(tmpdir(), "bb89n14-owned-backend-"))
  const root = join(parent, "checkout")
  await execFileAsync("/usr/bin/git", ["worktree", "add", "--detach", root, CLIENT_COMMIT], {
    cwd: REPOSITORY_ROOT,
    env: { ...process.env, LANG: "C", LC_ALL: "C" },
  })
  const canonicalRoot = await realpath(root)
  return {
    parent,
    root: canonicalRoot,
    async cleanup() {
      await execFileAsync("/usr/bin/git", ["worktree", "remove", "--force", canonicalRoot], {
        cwd: REPOSITORY_ROOT,
        env: { ...process.env, LANG: "C", LC_ALL: "C" },
      }).catch(() => undefined)
      await rm(parent, { recursive: true, force: true })
    },
  }
}

const replaceArgument = (arguments_, name, value) => {
  const changed = [...arguments_]
  changed[changed.indexOf(name) + 1] = value
  return changed
}


test("CLI is secret-free, loopback-only, and rejects every exact or prefixed synthetic identity", { concurrency: false }, () => {
  const base = [
    "--base-url", "http://127.0.0.1:9099",
    "--config-path", "/tmp/config.yaml",
    "--backend-root", REPOSITORY_ROOT,
    "--backend-python", "/usr/bin/python3",
    "--workspace", "/tmp/workspace",
    "--output", "/tmp/evidence.json",
    "--expected-backend-commit", BACKEND_COMMIT,
    "--expected-client-commit", CLIENT_COMMIT,
    "--expected-provider-model", EXPECTED_MODEL,
  ]
  assert.equal(parseArgs(base).expectedProviderModel, EXPECTED_MODEL)
  for (const identity of ["replay", "replay/playback", "replay-playback", "mock", "mock/echo", "smoke", "smoke/dev", "cli_mock", "cli_mock/test"]) {
    const changed = [...base]
    changed[changed.indexOf("--expected-provider-model") + 1] = identity
    assert.throws(() => parseArgs(changed), (error) => error instanceof GateFailure && error.code === "synthetic_model_not_gate_eligible")
  }
  const unapprovedRoute = replaceArgument(base, "--expected-provider-model", "openrouter/openai/gpt-5.4")
  assert.throws(() => parseArgs(unapprovedRoute), (error) => error instanceof GateFailure && error.code === "provider_model_route_unapproved")
  const remote = [...base]
  remote[1] = "https://example.test"
  assert.throws(() => parseArgs(remote), /base_url_not_literal_loopback/)
  assert.throws(() => parseArgs([...base, "--auth-token", "secret"]), /unknown_cli_argument/)
  assert.throws(() => parseArgs([...base, "--output", "/tmp/other.json"]), /missing_or_duplicate_output/)
  const valueAmbiguity = [...base]
  valueAmbiguity[valueAmbiguity.indexOf("--config-path") + 1] = "--output"
  assert.throws(() => parseArgs(valueAmbiguity), /missing_cli_value/)
  assert.throws(() => parseArgs([...base, "positional"]), /invalid_cli_arity/)
})

test("Git replacement refs are rejected under replacement-disabled provenance", { concurrency: false }, async () => {
  const root = await mkdtemp(join(tmpdir(), "bb89n14-replace-ref-"))
  const gitEnvironment = {
    ...process.env,
    LANG: "C",
    LC_ALL: "C",
    GIT_AUTHOR_NAME: "bb89n14-test",
    GIT_AUTHOR_EMAIL: "bb89n14-test@example.invalid",
    GIT_COMMITTER_NAME: "bb89n14-test",
    GIT_COMMITTER_EMAIL: "bb89n14-test@example.invalid",
  }
  try {
    await execFileAsync("/usr/bin/git", ["init", "-q"], { cwd: root, env: gitEnvironment })
    await execFileAsync("/usr/bin/git", ["commit", "-q", "--allow-empty", "-m", "original"], { cwd: root, env: gitEnvironment })
    const { stdout: original } = await execFileAsync("/usr/bin/git", ["rev-parse", "HEAD"], { cwd: root, env: gitEnvironment })
    await execFileAsync("/usr/bin/git", ["commit", "-q", "--allow-empty", "-m", "replacement"], { cwd: root, env: gitEnvironment })
    const { stdout: replacement } = await execFileAsync("/usr/bin/git", ["rev-parse", "HEAD"], { cwd: root, env: gitEnvironment })
    await execFileAsync("/usr/bin/git", ["replace", original.trim(), replacement.trim()], { cwd: root, env: gitEnvironment })

    await assert.rejects(
      assertNoGitReplacementRefsForTest(root),
      (error) => error instanceof GateFailure && error.code === "git_replacement_refs_forbidden",
    )
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test("production provider forwarding rejects an unapproved credential endpoint", { concurrency: false }, async () => {
  const run = await makeRun("provider-endpoint")
  const server = await new FakeCanonicalServer().start()
  try {
    const result = await runGate(
      gateArguments(run, server),
      {
        BB89N14_AUTH_TOKEN: PYTHON_AUTH_CANARY,
        BREADBOARD_OPENAI_AUTH_BASE_URL: "http://127.0.0.1:65535/capture",
        OPENAI_API_KEY: "provider-key-do-not-persist",
      },
      { production: true },
    )
    assert.equal(parseFailure(result).code, "provider_endpoint_unapproved")
    assert.equal(result.stdout.includes("provider-key-do-not-persist"), false)
    assert.equal(result.stderr.includes("provider-key-do-not-persist"), false)
    assert.deepEqual(server.requests, [])
    await assertNoOutput(run.output)
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("strict output preparse rejects duplicate and positional ambiguity before filesystem or server work", { concurrency: false }, async () => {
  const run = await makeRun("preparse")
  const server = await new FakeCanonicalServer().start()
  try {
    const duplicate = parseFailure(await runGate([...gateArguments(run, server), "--output", join(run.root, "other.json")]))
    assert.equal(duplicate.code, "missing_or_duplicate_output")
    const positional = parseFailure(await runGate([...gateArguments(run, server), "positional"]))
    assert.equal(positional.code, "invalid_cli_arity")
    assert.equal(server.requests.length, 0)
    await assertNoOutput(run.output)
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("module initialization consumes inherited auth before any runtime work", { concurrency: false }, async () => {
  const canary = "INHERITED_AUTH_CANARY_89N14"
  const childEnvironment = { ...process.env, BB89N14_AUTH_TOKEN: canary }
  const source = `await import(${JSON.stringify(new URL("../scripts/p30-bb89n14-gate.mjs", import.meta.url).href)}); process.stdout.write(process.env.BB89N14_AUTH_TOKEN === undefined ? "cleared" : "present")`
  const { stdout, stderr } = await execFileAsync(process.execPath, ["--input-type=module", "--eval", source], { env: childEnvironment })
  assert.equal(stdout, "cleared")
  assert.equal(stderr, "")
})

test("existing final output fails closed and remains byte-mode-inode identical", { concurrency: false }, async () => {
  const run = await makeRun("stale")
  const server = await new FakeCanonicalServer().start()
  try {
    const original = Buffer.from("stale-success\n", "utf8")
    await writeFile(run.output, original, { mode: 0o640 })
    const before = await lstat(run.output)
    const error = parseFailure(await runGate(gateArguments(run, server)))
    assert.equal(error.code, "output_already_exists")
    const after = await lstat(run.output)
    assert.deepEqual(await readFile(run.output), original)
    assert.equal(after.mode & 0o777, before.mode & 0o777)
    assert.equal(after.dev, before.dev)
    assert.equal(after.ino, before.ino)
    assert.equal(server.requests.length, 0)
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("configuration snapshot helper binds every framed root before backend access", { concurrency: false }, async () => {
  const run = await makeRun("snapshot-framed-roots")
  const server = await new FakeCanonicalServer({ dirty: true }).start()
  try {
    const error = parseFailure(await runGate(gateArguments(run, server)))
    assert.equal(error.code, "backend_revision_not_clean")
    assert.deepEqual(server.requests.map(({ method, path }) => ({ method, path })), [
      { method: "GET", path: "/v1/status" },
    ])
    await assertNoOutput(run.output)
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("configuration snapshot parser accepts valid YAML with explicit references and a slash literal", { concurrency: false }, async () => {
  const run = await makeRun("snapshot-valid-yaml")
  const server = await new FakeCanonicalServer({ dirty: true }).start()
  try {
    await writeFile(
      run.config,
      "extends: base.yaml\nprompts:\n  system: prompt.md\n  packs:\n    base:\n      inline_literal: Use A/B\nproviders:\n  default_model: openai/gpt-live\n",
      { mode: 0o600 },
    )
    const error = parseFailure(await runGate(gateArguments(run, server)))
    assert.equal(error.code, "backend_revision_not_clean")
    assert.deepEqual(server.requests.map(({ method, path }) => ({ method, path })), [
      { method: "GET", path: "/v1/status" },
    ])
    await assertNoOutput(run.output)
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("local synthetic subprocess success binds model, provenance, snapshotted config, FIFO, cleanup, schema, and 0600 atomic output", { concurrency: false }, async () => {
  const run = await makeRun("success")
  const originalConfig = await readFile(run.config)
  const canary = "AUTH_CANARY_DO_NOT_PERSIST_89N14"
  const server = await new FakeCanonicalServer({
    verifyConfigClosure: true,
    onStatus: async () => {
      const reservation = await lstat(join(run.outputDirectory, ".evidence.json.bb89n14.lock"))
      assert.equal(reservation.isFile(), true)
      assert.equal(reservation.mode & 0o777, 0o600)
      await writeFile(run.config, "providers:\n  default_model: changed/model\n", { mode: 0o600 })
    },
  }).start()
  try {
    const result = await runGate(gateArguments(run, server))
    assert.equal(result.code, 0, JSON.stringify({ stderr: result.stderr, fixtureErrors: server.fixtureErrors }))
    assert.equal(result.stderr, "")
    assert.equal(result.spawnargs.some((value) => value.includes(canary)), false)
    const outputBytes = await readFile(run.output)
    assert.equal(result.stdout.includes(canary), false)
    assert.equal(result.stderr.includes(canary), false)
    assert.equal(outputBytes.includes(canary), false)
    assert.equal(result.stdout.includes(run.output), false)
    assert.equal((await lstat(run.output)).mode & 0o777, 0o600)
    const evidence = JSON.parse(outputBytes)
    assert.equal(evidence.threatModel, "trusted-local-user-no-hostile-same-uid-process")
    assert.equal(evidence.providerPersistence, "disabled")
    assert.equal(evidence.providerStreaming, "required")
    assert.equal(evidence.providerConversationState, "stateless")
    assert.equal(evidence.provenance.backendDirty, false)
    assert.equal(evidence.provenance.listenerKind, "tracked-python-fixture")
    assert.equal(evidence.mainProof.classification, "local synthetic backend observation")
    assert.equal(evidence.provenance.clientCommit, CLIENT_COMMIT)
    assert.match(evidence.provenance.clientBuildManifestSha256, /^sha256:[0-9a-f]{64}$/)
    assert.match(evidence.provenance.providerEndpointSha256, /^sha256:[0-9a-f]{64}$/)
    assert.equal(outputBytes.includes("provider-correlated nonce observation"), false)
    assert.equal(evidence.mainProof.selected_model, EXPECTED_MODEL)
    assert.equal(evidence.mainProof.preSubmitSnapshot.model, EXPECTED_MODEL)
    assert.equal(evidence.mainProof.finalSnapshot.model, EXPECTED_MODEL)
    assert.equal(evidence.mainProof.reconnect.cursorCommittedThroughHead, true)
    assert.equal(evidence.mainProof.streamedTerminal.kind, "turn_completed")
    assert.equal(evidence.syntheticControl.classification, "provider-free synthetic control")
    assert.equal(evidence.syntheticControl.fifoSequences.terminalFirst < evidence.syntheticControl.fifoSequences.startSecond, true)
    assert.equal(evidence.syntheticControl.sequenceTrace.length > 0, true)
    assert.deepEqual(evidence.syntheticControl.capturedHead, {
      sequence: evidence.syntheticControl.sequenceTrace.at(-1).sequence,
      eventId: evidence.syntheticControl.sequenceTrace.at(-1).eventId,
    })
    assert.deepEqual(validateGateEvidence(evidence), evidence)
    const openEvidence = structuredClone(evidence)
    openEvidence.secret = canary
    assert.throws(() => validateGateEvidence(openEvidence), /evidence_schema/)
    const semanticContradictions = [
      (candidate) => { candidate.threatModel = "hostile-same-uid-process" },
      (candidate) => { candidate.providerPersistence = "enabled" },
      (candidate) => { candidate.providerStreaming = "optional" },
      (candidate) => { candidate.providerConversationState = "stateful" },
      (candidate) => { candidate.provenance.clientCommit = "b".repeat(40) },
      (candidate) => { candidate.provenance.listenerKind = "synthetic" },
      (candidate) => { candidate.provenance.providerEndpointSha256 = `sha256:${"0".repeat(64)}` },
      (candidate) => { candidate.mainProof.classification = "provider-correlated nonce observation" },
      (candidate) => { candidate.mainProof.requestText = `${candidate.mainProof.requestText} altered` },
      (candidate) => { candidate.mainProof.canonicalEventEnvelopes.find((event) => event.kind === "input_observed").payload.text = "wrong input" },
      (candidate) => { candidate.mainProof.canonicalEventEnvelopes.find((event) => event.kind === "assistant_text_completed").payload.text = "wrong assistant" },
      (candidate) => { candidate.mainProof.canonicalEventEnvelopes.find((event) => event.kind === "assistant_text_delta").payload.text = "wrong delta" },
      (candidate) => { candidate.mainProof.canonicalEventEnvelopes[0].sequence += 100 },
      (candidate) => { candidate.mainProof.disconnect.stableEventId = "missing-stable-event" },
      (candidate) => { candidate.mainProof.reconnect.firstEventId = "missing-resumed-event" },
      (candidate) => { candidate.mainProof.streamedTerminal.eventId = candidate.mainProof.canonicalEventEnvelopes[0].eventId },
      (candidate) => { candidate.mainProof.capturedHead.eventId = "missing-head-event" },
      (candidate) => { candidate.mainProof.preSubmitSnapshot.sessionId = "wrong-session" },
      (candidate) => { candidate.mainProof.finalSnapshot.terminalTurns.push(structuredClone(candidate.mainProof.finalSnapshot.terminalTurns[0])) },
      (candidate) => { candidate.syntheticControl.finalSnapshot.sessionId = "wrong-control-session" },
      (candidate) => {
        candidate.syntheticControl.attachSnapshotBefore.turnAdmission = "idle"
        candidate.syntheticControl.attachSnapshotBefore.activeTurnId = null
        candidate.syntheticControl.attachSnapshotAfter = structuredClone(candidate.syntheticControl.attachSnapshotBefore)
      },
      (candidate) => { candidate.syntheticControl.attachSnapshotBefore.activeTurnId = candidate.syntheticControl.secondReceipt.turnId },
      (candidate) => { candidate.syntheticControl.attachSnapshotBefore.queuedTurnCount = 1 },
      (candidate) => { candidate.syntheticControl.thirdReceipt.clientMessageId = candidate.syntheticControl.secondReceipt.clientMessageId },
      (candidate) => { candidate.syntheticControl.thirdReceipt.inputId = candidate.syntheticControl.secondReceipt.inputId },
      (candidate) => { candidate.syntheticControl.thirdReceipt.turnId = candidate.syntheticControl.secondReceipt.turnId },
      (candidate) => {
        candidate.syntheticControl.fifoSequences = {
          terminalFirst: 1,
          startSecond: 2,
          terminalSecond: 3,
          startThird: 4,
          terminalThird: 5,
        }
      },
      (candidate) => { candidate.syntheticControl.sequenceTrace.find((event) => event.kind === "turn_started").turnId = "wrong-turn" },
      (candidate) => {
        const firstAssistant = candidate.syntheticControl.sequenceTrace.find(
          (event) => event.turnId === candidate.syntheticControl.firstReceipt.turnId && event.kind === "assistant_text_completed",
        )
        firstAssistant.kind = "turn_started"
      },
      (candidate) => {
        const secondAssistant = candidate.syntheticControl.sequenceTrace.find(
          (event) => event.turnId === candidate.syntheticControl.secondReceipt.turnId && event.kind === "assistant_text_completed",
        )
        secondAssistant.kind = "turn_completed"
      },
      (candidate) => { candidate.syntheticControl.capturedHead.eventId = "wrong-control-head" },
      (candidate) => { candidate.syntheticControl.finalSnapshot.headEventId = "wrong-final-control-head" },
      (candidate) => { candidate.syntheticControl.finalSnapshot.terminalTurns[2].turnId = "wrong-terminal-set" },
      (candidate) => {
        const input = candidate.mainProof.canonicalEventEnvelopes.find((event) => event.kind === "input_observed")
        const completed = candidate.mainProof.canonicalEventEnvelopes.find((event) => event.kind === "assistant_text_completed")
        const inputTrace = candidate.mainProof.sequenceTrace.find((event) => event.eventId === input.eventId)
        const completedTrace = candidate.mainProof.sequenceTrace.find((event) => event.eventId === completed.eventId)
        input.kind = "assistant_text_completed"
        completed.kind = "input_observed"
        inputTrace.kind = input.kind
        completedTrace.kind = completed.kind
      },
      (candidate) => {
        const assistant = candidate.mainProof.canonicalEventEnvelopes.find((event) => event.kind === "assistant_text_completed")
        const terminal = candidate.mainProof.canonicalEventEnvelopes.find((event) => event.kind === "turn_completed")
        const assistantTrace = candidate.mainProof.sequenceTrace.find((event) => event.eventId === assistant.eventId)
        const terminalTrace = candidate.mainProof.sequenceTrace.find((event) => event.eventId === terminal.eventId)
        ;[assistant.sequence, terminal.sequence] = [terminal.sequence, assistant.sequence]
        assistantTrace.sequence = assistant.sequence
        terminalTrace.sequence = terminal.sequence
        candidate.mainProof.sequenceTrace.sort((left, right) => left.sequence - right.sequence)
        candidate.mainProof.streamedTerminal.sequence = terminal.sequence
        candidate.mainProof.capturedHead.eventId = assistant.eventId
        candidate.mainProof.finalSnapshot.headEventId = assistant.eventId
      },
      (candidate) => {
        candidate.mainProof.assistantText = ` ${candidate.mainProof.nonce}`
      },
    ]
    for (const mutate of semanticContradictions) {
      const contradiction = structuredClone(evidence)
      mutate(contradiction)
      assert.throws(() => validateGateEvidence(contradiction), GateFailure)
    }

    assert.equal(server.creates.length, 2)
    assert.equal(server.creates[0].body.config_path, server.creates[1].body.config_path)
    assert.notEqual(server.creates[0].body.config_path, run.config)
    assert.notDeepEqual(server.creates[0].configBytes, originalConfig)
    assert.equal(server.creates[0].body.metadata.proof, "local-synthetic-backend-observation")
    assert.deepEqual(server.creates[1].configBytes, server.creates[0].configBytes)
    assert.equal(server.creates.every((create) => create.configMode === 0o400), true)
    assert.equal(server.creates[0].body.metadata.model, EXPECTED_MODEL)
    assert.equal(server.creates[0].body.max_steps, 1)
    assert.match(relative(await realpath(run.workspace), server.creates[0].workspace), /^bb89n14-main-[0-9a-f-]+$/)
    assert.equal(dirname(server.creates[1].workspace), server.creates[0].workspace)
    assert.equal(server.creates[1].body.metadata.model, "replay")
    assert.match(server.creates[0].body.metadata.configuration_sha256, /^sha256:[0-9a-f]{64}$/)
    assert.equal(
      server.creates[1].body.metadata.configuration_sha256,
      server.creates[0].body.metadata.configuration_sha256,
    )
    assert.equal(
      server.requests.every((request) => request.path === "/v1/status" || request.path.startsWith("/v1/sessions")),
      true,
    )
    assert.equal(server.requests.some((request) => request.path.startsWith("/sessions")), false)
    assert.equal(server.requests[0].authorization, undefined)

    await assert.rejects(stat(server.creates[0].body.config_path), (error) => error.code === "ENOENT")
    await assert.rejects(stat(server.creates[0].workspace), (error) => error.code === "ENOENT")
    await assert.rejects(stat(server.creates[1].workspace), (error) => error.code === "ENOENT")
    for (const fixture of server.fixturePaths) await assert.rejects(stat(fixture), (error) => error.code === "ENOENT")
    assert.deepEqual(await readdir(run.workspace), [])
    assert.equal((await readdir(run.root)).some((name) => name.startsWith(".bb89n14-config-")), false)
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})
test("tracked synthetic fixture rejects bearer before any HTTP", { concurrency: false }, async () => {
  const run = await makeRun("tracked-auth-rejected")
  const server = await new FakeCanonicalServer().start()
  try {
    const result = await runGate(
      gateArguments(run, server),
      { BB89N14_AUTH_TOKEN: PYTHON_AUTH_CANARY },
    )
    const error = parseFailure(result)
    assert.equal(error.code, "synthetic_backend_auth_forbidden")
    assert.deepEqual(server.requests, [])
    assert.equal(result.stdout.includes(PYTHON_AUTH_CANARY), false)
    assert.equal(result.stderr.includes(PYTHON_AUTH_CANARY), false)
    await assertNoOutput(run.output)
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("short bearer tokens fail before any backend request", { concurrency: false }, async () => {
  const run = await makeRun("short-auth")
  const server = await new FakeCanonicalServer().start()
  try {
    const result = await runGate(gateArguments(run, server), { BB89N14_AUTH_TOKEN: "too-short" })
    assert.equal(parseFailure(result).code, "invalid_auth_token")
    assert.deepEqual(server.requests, [])
    await assertNoOutput(run.output)
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("listener repository untracked files fail before HTTP", { concurrency: false }, async () => {
  const run = await makeRun("listener-untracked")
  const server = await new FakeCanonicalServer().start()
  try {
    await writeFile(join(server.proxy.root, "untracked-canary"), "untracked\n", { mode: 0o600 })
    const result = await runGate(gateArguments(run, server))
    assert.equal(parseFailure(result).code, "backend_listener_not_clean")
    assert.deepEqual(server.requests, [])
    await assertNoOutput(run.output)
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("direct in-process backend is rejected before bearer transmission", { concurrency: false }, async () => {
  const run = await makeRun("direct-fake-rejected")
  const server = await new FakeCanonicalServer({ directFake: true }).start()
  try {
    const result = await runGate(
      gateArguments(run, server),
      { BB89N14_AUTH_TOKEN: PYTHON_AUTH_CANARY },
    )
    const error = parseFailure(result)
    assert.equal(error.code, "backend_listener_identity_invalid")
    assert.deepEqual(server.requests, [])
    assert.equal(result.stdout.includes(PYTHON_AUTH_CANARY), false)
    assert.equal(result.stderr.includes(PYTHON_AUTH_CANARY), false)
    await assertNoOutput(run.output)
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("backend-controlled evidence strings cannot persist private paths", { concurrency: false }, async (context) => {
  for (const [name, optionFor, persists] of [
    ["protocol", (run) => ({ protocolVersionEcho: run.config }), true],
    ["engine", (run) => ({ engineVersionEcho: run.workspace }), true],
    ["mode", (run) => ({ modeEcho: run.outputDirectory }), false],
    ["receipt-id", (run) => ({ inputIdEcho: run.config }), true],
    ["output-parent", (run) => ({ modeEcho: dirname(run.outputDirectory) }), false],
    ["unrelated-absolute", () => ({ modeEcho: join(tmpdir(), "unrelated-private-path") }), false],
  ]) {
    await context.test(name, async () => {
      const run = await makeRun(`evidence-path-${name}`)
      const options = optionFor(run)
      const injectedValue = Object.values(options)[0]
      const server = await new FakeCanonicalServer(options).start()
      try {
        const result = await runGate(gateArguments(run, server))
        if (persists) {
          assert.equal(parseFailure(result).code, "evidence_forbidden_string")
          await assertNoOutput(run.output)
        } else {
          assert.equal(result.code, 0, JSON.stringify({ stderr: result.stderr, fixtureErrors: server.fixtureErrors }))
          assert.equal((await readFile(run.output, "utf8")).includes(injectedValue), false)
        }
        assert.equal(result.stdout.includes(run.root), false)
        assert.equal(result.stderr.includes(run.root), false)
      } finally {
        await server.stop()
        await rm(run.root, { recursive: true, force: true })
      }
    })
  }
})

test("provider environment values echoed by backend status are rejected without persistence", { concurrency: false }, async (context) => {
  for (const [name, environment, echoedValue, expectedCode] of [
    [
      "api-key",
      { OPENAI_API_KEY: "provider-api-secret-canary-89n14" },
      "provider-api-secret-canary-89n14",
      "evidence_forbidden_string",
    ],
    [
      "projected-base-url",
      { BREADBOARD_OPENAI_AUTH_BASE_URL: "https://provider-account-canary-89n14.example.invalid/v1" },
      "https://provider-account-canary-89n14.example.invalid/v1",
      "evidence_forbidden_string",
    ],
    [
      "projected-header-value",
      { BREADBOARD_OPENAI_AUTH_HEADERS_JSON: JSON.stringify({ Authorization: "Bearer provider-header-secret-canary-89n14" }) },
      "Bearer provider-header-secret-canary-89n14",
      "evidence_forbidden_string",
    ],
    [
      "short-projected-header-value",
      { BREADBOARD_OPENAI_AUTH_HEADERS_JSON: JSON.stringify({ "X-Account": "secret" }) },
      "secret",
      "provider_environment_invalid",
    ],
  ]) {
    await context.test(name, async () => {
      const run = await makeRun(`provider-env-echo-${name}`)
      const server = await new FakeCanonicalServer({ protocolVersionEcho: echoedValue }).start()
      try {
        const result = await runGate(gateArguments(run, server), environment)
        assert.equal(parseFailure(result).code, expectedCode)
        for (const value of [...Object.values(environment), echoedValue]) {
          assert.equal(result.stdout.includes(value), false)
          assert.equal(result.stderr.includes(value), false)
        }
        if (expectedCode === "provider_environment_invalid") assert.deepEqual(server.requests, [])
        await assertNoOutput(run.output)
      } finally {
        await server.stop()
        await rm(run.root, { recursive: true, force: true })
      }
    })
  }
})

test("production provider mode refuses external listeners and fail-closes owned launch authority", { concurrency: false }, async (context) => {
  await context.test("external listener in unrelated git root", async () => {
    const run = await makeRun("owned-external-refused")
    const server = await new FakeCanonicalServer().start()
    try {
      const result = await runGate(
        gateArguments(run, server),
        { BB89N14_AUTH_TOKEN: PYTHON_AUTH_CANARY },
        { production: true },
      )
      assert.equal(parseFailure(result).code, "backend_git_common_dir_mismatch")
      assert.deepEqual(server.requests, [])
      assert.equal(result.stderr.includes(PYTHON_AUTH_CANARY), false)
      await assertNoOutput(run.output)
    } finally {
      await server.stop()
      await rm(run.root, { recursive: true, force: true })
    }
  })

  await context.test("untracked backend root", async () => {
    const checkout = await makeCleanBackendWorktree()
    const run = await makeRun("owned-untracked")
    const server = await new FakeCanonicalServer().start()
    try {
      await writeFile(join(checkout.root, "untracked-shadow"), "shadow\n", { mode: 0o600 })
      const python = await resolveCanonicalLoaderPython()
      let arguments_ = gateArguments(run, server)
      arguments_ = replaceArgument(arguments_, "--backend-root", checkout.root)
      arguments_ = replaceArgument(arguments_, "--backend-python", python)
      arguments_ = replaceArgument(arguments_, "--expected-backend-commit", CLIENT_COMMIT)
      const result = await runGate(arguments_, {}, { production: true })
      assert.equal(parseFailure(result).code, "backend_listener_not_clean")
      assert.deepEqual(server.requests, [])
      await assertNoOutput(run.output)
    } finally {
      await server.stop()
      await checkout.cleanup()
      await rm(run.root, { recursive: true, force: true })
    }
  })

  await context.test("dirty backend root", async () => {
    const checkout = await makeCleanBackendWorktree()
    const run = await makeRun("owned-dirty")
    const server = await new FakeCanonicalServer().start()
    try {
      const app = join(checkout.root, "agentic_coder_prototype", "api", "cli_bridge", "app.py")
      await writeFile(app, `${await readFile(app, "utf8")}\n# dirty shadow\n`, { mode: 0o600 })
      const python = await resolveCanonicalLoaderPython()
      let arguments_ = gateArguments(run, server)
      arguments_ = replaceArgument(arguments_, "--backend-root", checkout.root)
      arguments_ = replaceArgument(arguments_, "--backend-python", python)
      arguments_ = replaceArgument(arguments_, "--expected-backend-commit", CLIENT_COMMIT)
      const result = await runGate(arguments_, {}, { production: true })
      assert.equal(parseFailure(result).code, "backend_listener_not_clean")
      assert.deepEqual(server.requests, [])
      await assertNoOutput(run.output)
    } finally {
      await server.stop()
      await checkout.cleanup()
      await rm(run.root, { recursive: true, force: true })
    }
  })

  await context.test("closed environment and bounded child exit", async () => {
    const checkout = await makeCleanBackendWorktree()
    const run = await makeRun("owned-child-exit")
    const server = await new FakeCanonicalServer().start()
    const marker = join(run.root, "shadow-loaded")
    const python = join(checkout.root, "python3.99")
    const gitEnvironment = {
      ...process.env,
      LANG: "C",
      LC_ALL: "C",
      GIT_AUTHOR_NAME: "bb89n14-test",
      GIT_AUTHOR_EMAIL: "bb89n14-test@example.invalid",
      GIT_COMMITTER_NAME: "bb89n14-test",
      GIT_COMMITTER_EMAIL: "bb89n14-test@example.invalid",
    }
    try {
      await writeFile(
        python,
        `#!/bin/sh\nIFS= read -r control\nprintf executed > ${JSON.stringify(marker)}\nexit 71\n`,
        { mode: 0o700 },
      )
      await execFileAsync("/usr/bin/git", ["add", "--", "python3.99"], { cwd: checkout.root, env: gitEnvironment })
      await execFileAsync("/usr/bin/git", ["commit", "-q", "-m", "owned python fixture"], { cwd: checkout.root, env: gitEnvironment })
      const { stdout: commitOutput } = await execFileAsync("/usr/bin/git", ["rev-parse", "HEAD"], { cwd: checkout.root, env: gitEnvironment })
      let arguments_ = gateArguments(run, server)
      arguments_ = replaceArgument(arguments_, "--backend-root", checkout.root)
      arguments_ = replaceArgument(arguments_, "--backend-python", python)
      arguments_ = replaceArgument(arguments_, "--expected-backend-commit", commitOutput.trim())
      const result = await runGate(arguments_, {
        PYTHONPATH: join(run.root, "shadow"),
        PYTHONHOME: join(run.root, "shadow-home"),
        PYTHONSTARTUP: join(run.root, "shadow-startup"),
        BASH_ENV: join(run.root, "shadow-loader"),
      }, { production: true })
      assert.equal(parseFailure(result).code, "backend_python_unapproved")
      await assert.rejects(stat(marker), (error) => error.code === "ENOENT")
      assert.deepEqual(server.requests, [])
      await assertNoOutput(run.output)
    } finally {
      await server.stop()
      await checkout.cleanup()
      await rm(run.root, { recursive: true, force: true })
    }
  })

  await context.test("committed snapshot ignores transient live mutation and uvicorn shadow while cwd remains writable", async () => {
    const checkout = await makeCleanBackendWorktree()
    const run = await makeRun("approved-runtime-snapshot")
    const server = await new FakeCanonicalServer().start()
    const gitEnvironment = {
      ...process.env,
      LANG: "C",
      LC_ALL: "C",
      GIT_AUTHOR_NAME: "bb89n14-test",
      GIT_AUTHOR_EMAIL: "bb89n14-test@example.invalid",
      GIT_COMMITTER_NAME: "bb89n14-test",
      GIT_COMMITTER_EMAIL: "bb89n14-test@example.invalid",
    }
    const snapshotMarker = join(run.root, "snapshot-module-loaded")
    const liveMarker = join(run.root, "live-module-loaded")
    const uvicornShadowMarker = join(run.root, "repository-uvicorn-loaded")
    const runtimeArtifact = join(run.root, "runtime-artifact-observed")
    const providerEnvironmentMarker = join(run.root, "provider-environment-observed")
    try {
      const application = join(checkout.root, "agentic_coder_prototype", "api", "cli_bridge", "app.py")
      const liveProbe = join(checkout.root, "agentic_coder_prototype", "owned_snapshot_probe.py")
      const uvicornShadow = join(checkout.root, "uvicorn.py")
      const snapshotProbeSource = [
        "from pathlib import Path",
        `Path(${JSON.stringify(snapshotMarker)}).write_text("snapshot\\n", encoding="utf-8")`,
        "EXIT_CODE = 71",
        "",
      ].join("\n")
      const liveProbeMutation = [
        "from pathlib import Path",
        `Path(${JSON.stringify(liveMarker)}).write_text("live\\n", encoding="utf-8")`,
        "EXIT_CODE = 72",
        "",
      ].join("\n")
      const applicationSource = [
        "from pathlib import Path",
        "import os",
        "_runtime_artifact = Path('runtime-artifact')",
        "_runtime_artifact.write_text('runtime\\n', encoding='utf-8')",
        `Path(${JSON.stringify(runtimeArtifact)}).write_text(_runtime_artifact.read_text(encoding="utf-8"), encoding="utf-8")`,
        `Path(${JSON.stringify(providerEnvironmentMarker)}).write_text(f"{'OPENAI_API_KEY' in os.environ}:{'OPENROUTER_API_KEY' in os.environ}\\n", encoding="utf-8")`,
        `_live_probe = Path(${JSON.stringify(liveProbe)})`,
        "_original_probe = _live_probe.read_bytes()",
        "try:",
        `    _live_probe.write_text(${JSON.stringify(liveProbeMutation)}, encoding="utf-8")`,
        "    from agentic_coder_prototype import owned_snapshot_probe as _probe",
        "finally:",
        "    _live_probe.write_bytes(_original_probe)",
        "raise SystemExit(_probe.EXIT_CODE)",
        "",
      ].join("\n")
      const uvicornShadowSource = [
        "from pathlib import Path",
        `Path(${JSON.stringify(uvicornShadowMarker)}).write_text("shadow\\n", encoding="utf-8")`,
        "raise SystemExit(73)",
        "",
      ].join("\n")
      await writeFile(application, applicationSource, { mode: 0o600 })
      await writeFile(liveProbe, snapshotProbeSource, { mode: 0o600 })
      await writeFile(uvicornShadow, uvicornShadowSource, { mode: 0o600 })
      await execFileAsync(
        "/usr/bin/git",
        ["add", "--", "agentic_coder_prototype/api/cli_bridge/app.py", "agentic_coder_prototype/owned_snapshot_probe.py", "uvicorn.py"],
        { cwd: checkout.root, env: gitEnvironment },
      )
      await execFileAsync("/usr/bin/git", ["commit", "-q", "-m", "owned snapshot fixture"], { cwd: checkout.root, env: gitEnvironment })
      const { stdout: commitOutput } = await execFileAsync("/usr/bin/git", ["rev-parse", "HEAD"], { cwd: checkout.root, env: gitEnvironment })
      const python = await resolveCanonicalLoaderPython()
      let arguments_ = gateArguments(run, server)
      arguments_ = replaceArgument(arguments_, "--backend-root", checkout.root)
      arguments_ = replaceArgument(arguments_, "--backend-python", python)
      arguments_ = replaceArgument(arguments_, "--expected-backend-commit", commitOutput.trim())
      const result = await runGate(
        arguments_,
        {
          BB89N14_AUTH_TOKEN: PYTHON_AUTH_CANARY,
          OPENAI_API_KEY: "openai-route-secret-do-not-persist",
          OPENROUTER_API_KEY: "openrouter-route-secret-do-not-forward",
        },
        { production: true },
      )
      assert.equal(parseFailure(result).code, "owned_backend_exited")
      assert.equal(await readFile(snapshotMarker, "utf8"), "snapshot\n")
      assert.equal(await readFile(runtimeArtifact, "utf8"), "runtime\n")
      assert.equal(await readFile(providerEnvironmentMarker, "utf8"), "True:False\n")
      assert.equal(await readFile(liveProbe, "utf8"), snapshotProbeSource)
      await assert.rejects(stat(liveMarker), (error) => error.code === "ENOENT")
      await assert.rejects(stat(uvicornShadowMarker), (error) => error.code === "ENOENT")
      assert.deepEqual(server.requests, [])
      await assertNoOutput(run.output)
    } finally {
      await server.stop()
      await checkout.cleanup()
      await rm(run.root, { recursive: true, force: true })
    }
  })

  await context.test("byte-changed interpreter copy is rejected before execution", async () => {
    const checkout = await makeCleanBackendWorktree()
    const run = await makeRun("changed-python")
    const server = await new FakeCanonicalServer().start()
    const python = join(checkout.root, "python3.11")
    const marker = join(run.root, "changed-python-executed")
    const gitEnvironment = {
      ...process.env,
      LANG: "C",
      LC_ALL: "C",
      GIT_AUTHOR_NAME: "bb89n14-test",
      GIT_AUTHOR_EMAIL: "bb89n14-test@example.invalid",
      GIT_COMMITTER_NAME: "bb89n14-test",
      GIT_COMMITTER_EMAIL: "bb89n14-test@example.invalid",
    }
    try {
      const approvedPython = await resolveCanonicalLoaderPython()
      const originalBytes = await readFile(approvedPython)
      await writeFile(python, Buffer.concat([originalBytes, Buffer.from([0])]), { mode: 0o700 })
      await execFileAsync("/usr/bin/git", ["add", "--", "python3.11"], { cwd: checkout.root, env: gitEnvironment })
      await execFileAsync("/usr/bin/git", ["commit", "-q", "-m", "changed python fixture"], { cwd: checkout.root, env: gitEnvironment })
      const { stdout: commitOutput } = await execFileAsync("/usr/bin/git", ["rev-parse", "HEAD"], { cwd: checkout.root, env: gitEnvironment })
      let arguments_ = gateArguments(run, server)
      arguments_ = replaceArgument(arguments_, "--backend-root", checkout.root)
      arguments_ = replaceArgument(arguments_, "--backend-python", python)
      arguments_ = replaceArgument(arguments_, "--expected-backend-commit", commitOutput.trim())
      const result = await runGate(arguments_, { BB89N14_AUTH_TOKEN: PYTHON_AUTH_CANARY }, { production: true })
      assert.equal(parseFailure(result).code, "backend_python_unapproved")
      await assert.rejects(stat(marker), (error) => error.code === "ENOENT")
      assert.deepEqual(server.requests, [])
      await assertNoOutput(run.output)
    } finally {
      await server.stop()
      await checkout.cleanup()
      await rm(run.root, { recursive: true, force: true })
    }
  })
})

test("owned immutable canonical app reports the independently attested commit and clean tree", { concurrency: false }, async () => {
  const checkout = await makeCleanBackendWorktree()
  const run = await makeRun("owned-canonical-status")
  try {
    const python = await resolveCanonicalLoaderPython()
    const provenance = await readOwnedBackendProvenanceForTest({
      baseUrl: "http://127.0.0.1:1/",
      backendRoot: checkout.root,
      backendPython: python,
      expectedBackendCommit: CLIENT_COMMIT,
      expectedProviderModel: EXPECTED_MODEL,
      workspace: run.workspace,
    }, Date.now() + 300_000)
    assert.equal(provenance.commit, CLIENT_COMMIT)
    assert.equal(provenance.dirty, false)
  } finally {
    await checkout.cleanup()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("runtime candidate binds executable bytes and every isolated runtime root file", { concurrency: false }, async () => {
  const root = await mkdtemp(join(tmpdir(), "bb89n14-runtime-candidate-"))
  try {
    const runtimeRoot = join(root, "site-packages")
    const runtimeFile = join(runtimeRoot, "dependency.py")
    const python = join(root, "python3.99")
    const linkedFrameworkBinary = join(root, "Runtime.framework", "Versions", "A", "Runtime")
    const linkedFrameworkEntry = join(runtimeRoot, "Runtime.framework")
    await mkdir(runtimeRoot, { mode: 0o700 })
    await mkdir(dirname(linkedFrameworkBinary), { recursive: true, mode: 0o700 })
    await writeFile(linkedFrameworkBinary, "framework-binary-v1\n", { mode: 0o600 })
    await symlink(relative(runtimeRoot, join(root, "Runtime.framework")), linkedFrameworkEntry)
    await writeFile(runtimeFile, "value = 1\n", { mode: 0o600 })
    const resolvedPython = await realpath(root).then((canonicalRoot) => join(canonicalRoot, "python3.99"))
    const resolvedRuntimeRoot = await realpath(runtimeRoot)
    const probe = JSON.stringify({
      basePrefix: resolvedRuntimeRoot,
      dontWriteBytecode: true,
      executable: resolvedPython,
      ignoreEnvironment: true,
      isolated: true,
      noUserSite: true,
      prefix: resolvedRuntimeRoot,
      sysPath: [resolvedRuntimeRoot],
      userSite: null,
      version: "3.99.0",
    })
    const quotedProbe = probe.replaceAll("'", "'\\''")
    await writeFile(python, `#!/bin/sh\nprintf '%s' '${quotedProbe}'\n`, { mode: 0o700 })
    const first = await computeApprovedBackendRuntimeCandidateForTest(python, Date.now() + 30_000)
    assert.deepEqual(Object.keys(first).sort(), [
      "bytes",
      "count",
      "executableSha256",
      "resolvedPath",
      "runtimeClosureSha256",
      "version",
    ])
    await writeFile(runtimeFile, "value = 2\n", { mode: 0o600 })
    const runtimeChanged = await computeApprovedBackendRuntimeCandidateForTest(python, Date.now() + 30_000)
    assert.notEqual(runtimeChanged.runtimeClosureSha256, first.runtimeClosureSha256)
    assert.equal(runtimeChanged.executableSha256, first.executableSha256)
    await writeFile(runtimeFile, "value = 1\n", { mode: 0o600 })
    await writeFile(linkedFrameworkBinary, "framework-binary-v2\n", { mode: 0o600 })
    const linkedFrameworkChanged = await computeApprovedBackendRuntimeCandidateForTest(python, Date.now() + 30_000)
    assert.notEqual(linkedFrameworkChanged.runtimeClosureSha256, first.runtimeClosureSha256)
    assert.equal(linkedFrameworkChanged.executableSha256, first.executableSha256)
    await writeFile(python, `${await readFile(python, "utf8")}# byte change\n`, { mode: 0o700 })
    const executableChanged = await computeApprovedBackendRuntimeCandidateForTest(python, Date.now() + 30_000)
    assert.notEqual(executableChanged.executableSha256, first.executableSha256)
  } finally {
    await rm(root, { recursive: true, force: true })
  }
})

test("installed isolated Python runtime produces a bounded approval candidate", { concurrency: false }, async () => {
  const python = await resolveCanonicalLoaderPython()
  const candidate = await computeApprovedBackendRuntimeCandidateForTest(python, Date.now() + 300_000)
  assert.equal(candidate.resolvedPath, await realpath(python))
  assert.match(candidate.version, /^\d+\.\d+\.\d+$/)
  assert.match(candidate.executableSha256, /^sha256:[0-9a-f]{64}$/)
  assert.match(candidate.runtimeClosureSha256, /^sha256:[0-9a-f]{64}$/)
  assert.equal(Number.isSafeInteger(candidate.count) && candidate.count > 0, true)
  assert.equal(Number.isSafeInteger(candidate.bytes) && candidate.bytes > 0, true)
})


test("synthetic control consumes submitted replay fixtures and rejects malformed or zero-delay schedules", { concurrency: false }, async (context) => {
  for (const [mutation, expectedFixtureCode] of [
    ["malformed", "fixture_replay_record_count"],
    ["zero-delay", "fixture_replay_delayed_assistant"],
  ]) {
    await context.test(mutation, async () => {
      const run = await makeRun(`control-fixture-${mutation}`)
      const server = await new FakeCanonicalServer({ controlFixtureMutation: mutation }).start()
      try {
        const error = parseFailure(await runGate(gateArguments(run, server)))
        assert.equal(error.type, "canonical_client_failure")
        assert.equal(server.fixtureErrors.at(-1)?.code, expectedFixtureCode)
        await assertNoOutput(run.output)
      } finally {
        await server.stop()
        await rm(run.root, { recursive: true, force: true })
      }
    })
  }
})

test("main proof rejects nonce responses with any leading or trailing whitespace", { concurrency: false }, async (context) => {
  for (const [name, options] of [
    ["leading-space", { noncePrefix: " " }],
    ["trailing-space", { nonceSuffix: " " }],
    ["trailing-newline", { nonceSuffix: "\n" }],
  ]) {
    await context.test(name, async () => {
      const run = await makeRun(`nonce-${name}`)
      const server = await new FakeCanonicalServer(options).start()
      try {
        const error = parseFailure(await runGate(gateArguments(run, server)))
        assert.equal(error.code, "assistant_nonce_mismatch")
        await assertNoOutput(run.output)
      } finally {
        await server.stop()
        await rm(run.root, { recursive: true, force: true })
      }
    })
  }
})

test("canonical loader consumes mirrored top-level and nested transitive config, prompt, and tool closures with digest binding", { concurrency: false }, async () => {
  const repository = await makePrivateRepositoryFixture("canonical-config")
  const configuration = await prepareCanonicalConfigurationFixture(repository)
  const runOnce = async (name, config) => {
    const run = await makeRun(name)
    const server = await new FakeCanonicalServer({ verifyActualLoader: true }).start()
    try {
      const result = await runGate(
        gateArguments({ ...run, config }, server),
        {},
        { scriptPath: repository.scriptPath, cwd: repository.sdkRoot, invokeModule: true },
      )
      assert.equal(result.code, 0, JSON.stringify({ stderr: result.stderr, fixtureErrors: server.fixtureErrors }))
      const evidence = JSON.parse(await readFile(run.output, "utf8"))
      assert.equal(server.loadedConfigurations.length, 2)
      for (const loaded of server.loadedConfigurations) {
        assert.equal(loaded.privateReferences, true)
        assert.ok(loaded.toolCount > 0)
        assert.ok(loaded.systemPromptBytes > 0)
        assert.match(loaded.systemPromptSha256, /^[0-9a-f]{64}$/)
        assert.equal(loaded.inlineLiteral, "Use A/B\n")
        assert.equal(loaded.providerStore, false)
        assert.equal(loaded.providerResponsesStateful, false)
        assert.equal(loaded.disableStreamOnProbeFailure, false)
      }
      assert.equal(server.creates[0].body.config_path, server.creates[1].body.config_path)
      return evidence.provenance.configurationSha256
    } finally {
      await server.stop()
      await rm(run.root, { recursive: true, force: true })
    }
  }

  try {
    await runOnce("canonical-top", configuration.topConfig)
    const initialDigest = await runOnce("canonical-nested", configuration.nestedConfig)
    await writeFile(configuration.deepConfig, "# changed transitive base\n{}\n", { mode: 0o600 })
    const extendsDigest = await runOnce("canonical-nested-extends", configuration.nestedConfig)
    assert.notEqual(extendsDigest, initialDigest)
    await writeFile(configuration.externalPromptPath, "changed trusted external prompt\n", { mode: 0o600 })
    const externalDigest = await runOnce("canonical-nested-external-prompt", configuration.nestedConfig)
    assert.notEqual(externalDigest, extendsDigest)
    await writeFile(configuration.promptPath, `${await readFile(configuration.promptPath, "utf8")}\nchanged prompt closure\n`, { mode: 0o600 })
    const promptDigest = await runOnce("canonical-nested-prompt", configuration.nestedConfig)
    assert.notEqual(promptDigest, externalDigest)
    await writeFile(configuration.toolPath, `${await readFile(configuration.toolPath, "utf8")}\n# changed tool closure\n`, { mode: 0o600 })
    const toolDigest = await runOnce("canonical-nested-tool", configuration.nestedConfig)
    assert.notEqual(toolDigest, promptDigest)
    const promptMode = (await lstat(configuration.promptPath)).mode & 0o777
    await chmod(configuration.promptPath, promptMode === 0o600 ? 0o640 : 0o600)
    const modeDigest = await runOnce("canonical-nested-mode", configuration.nestedConfig)
    assert.notEqual(modeDigest, toolDigest)
    assert.equal(configuration.topBytes.includes(Buffer.from("store: true")), true)
    assert.equal(configuration.nestedBytes.includes(Buffer.from("store: true")), true)
    assert.equal(configuration.topBytes.includes(Buffer.from("responses_stateful: true")), true)
    assert.equal(configuration.nestedBytes.includes(Buffer.from("responses_stateful: true")), true)
    assert.equal(configuration.canonicalBaseBytes.includes(Buffer.from("disable_stream_on_probe_failure: true")), true)
    assert.deepEqual(await readFile(configuration.topConfig), configuration.topBytes)
    assert.deepEqual(await readFile(configuration.nestedConfig), configuration.nestedBytes)
    assert.deepEqual(await readFile(configuration.canonicalBaseConfig), configuration.canonicalBaseBytes)
  } finally {
    await rm(repository.parent, { recursive: true, force: true })
  }
})
test("configuration digest binds source prompt bytes before loader newline materialization", { concurrency: false }, async () => {
  const run = await makeRun("source-prompt-bytes")
  const prompt = join(run.root, "prompt.md")
  await writeFile(
    run.config,
    "version: 2\nprompts:\n  system: ./prompt.md\nproviders:\n  default_model: openai/gpt-live\n",
    { mode: 0o600 },
  )
  const server = await new FakeCanonicalServer({ verifyActualLoader: true }).start()
  try {
    await writeFile(prompt, "byte-distinct prompt", { mode: 0o600 })
    const first = await runGate(gateArguments(run, server))
    assert.equal(first.code, 0, first.stderr)
    const firstEvidence = JSON.parse(await readFile(run.output, "utf8"))
    const firstPromptHash = server.loadedConfigurations[0].systemPromptSha256

    const secondOutput = join(run.outputDirectory, "second.json")
    await writeFile(prompt, "byte-distinct prompt\n", { mode: 0o600 })
    const second = await runGate(gateArguments({ ...run, output: secondOutput }, server))
    assert.equal(second.code, 0, second.stderr)
    const secondEvidence = JSON.parse(await readFile(secondOutput, "utf8"))
    const secondPromptHash = server.loadedConfigurations[2].systemPromptSha256

    assert.notEqual(firstEvidence.provenance.configurationSha256, secondEvidence.provenance.configurationSha256)
    assert.equal(firstPromptHash, secondPromptHash)
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("selected live config snapshots project-root and trusted-sibling prompt and tool references through the canonical loader", { concurrency: false }, async () => {
  const run = await makeRun("selected-live-config")
  const config = join(REPOSITORY_ROOT, "agent_configs", "misc", "codex_cli_gpt54mini_e4_live.yaml")
  const server = await new FakeCanonicalServer({ verifyActualLoader: true }).start()
  try {
    const result = await runGate(gateArguments({ ...run, config }, server))
    assert.equal(result.code, 0, JSON.stringify({ stderr: result.stderr, fixtureErrors: server.fixtureErrors }))
    assert.equal(server.loadedConfigurations.length, 2)
    assert.equal(server.loadedConfigurations.every((loaded) => loaded.privateReferences), true)
    assert.equal(server.loadedConfigurations.every((loaded) => loaded.toolCount > 0), true)
    assert.equal(server.loadedConfigurations.every((loaded) => loaded.systemPromptBytes > 0), true)
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("configuration paths keep nofollow boundaries while inline prompt content is snapshot-owned", { concurrency: false }, async (context) => {
  await context.test("source symlink ancestor", async () => {
    const run = await makeRun("config-source-symlink")
    const actualDirectory = join(run.root, "actual-config")
    const linkedDirectory = join(run.root, "linked-config")
    await mkdir(actualDirectory, { mode: 0o700 })
    await writeFile(join(actualDirectory, "config.yaml"), "version: 2\n", { mode: 0o600 })
    await symlink(actualDirectory, linkedDirectory)
    const server = await new FakeCanonicalServer().start()
    try {
      const error = parseFailure(await runGate(gateArguments({ ...run, config: join(linkedDirectory, "config.yaml") }, server)))
      assert.equal(error.code, "config_closure_symlink_unsupported")
      assert.equal(server.requests.length, 0)
      await assertNoOutput(run.output)
    } finally {
      await server.stop()
      await rm(run.root, { recursive: true, force: true })
    }
  })

  await context.test("shallow config cannot read an absolute private file", async () => {
    const run = await makeRun("config-prompt-absolute-private")
    const shallowRoot = await mkdtemp("/tmp/bb89n14-shallow-config-")
    const config = join(shallowRoot, "config.yaml")
    await writeFile(
      config,
      "version: 2\nprompts:\n  system: /etc/passwd\nproviders:\n  default_model: openai/gpt-live\n",
      { mode: 0o600 },
    )
    const server = await new FakeCanonicalServer().start()
    try {
      const error = parseFailure(await runGate(gateArguments({ ...run, config }, server)))
      assert.equal(error.code, "config_reference_outside_closure")
      assert.equal(server.requests.length, 0)
      await assertNoOutput(run.output)
    } finally {
      await server.stop()
      await rm(run.root, { recursive: true, force: true })
      await rm(shallowRoot, { recursive: true, force: true })
    }
  })

  await context.test("unresolved inline prompt is snapshot-owned even when backend cwd has a matching filename", async () => {
    const run = await makeRun("config-prompt-implicit-cwd")
    const loaderCwd = join(run.root, "backend-cwd")
    const cwdCanary = "CWD_PROMPT_CONTENT_MUST_NOT_LOAD"
    await mkdir(loaderCwd, { mode: 0o700 })
    await writeFile(join(loaderCwd, "secret file.md"), cwdCanary, { mode: 0o600 })
    await writeFile(
      run.config,
      [
        "version: 2",
        "prompts:",
        "  packs:",
        "    base:",
        '      system: "secret file.md"',
        "      inline_literal: other_harness_refs/codex/codex-rs/core/gpt_5_1_prompt.md",
        "  injection:",
        "    system_order:",
        '      - "@pack(base).system"',
        "providers:",
        "  default_model: openai/gpt-live",
        "",
      ].join("\n"),
      { mode: 0o600 },
    )
    const server = await new FakeCanonicalServer({
      verifyActualLoader: true,
      loaderCwd,
      loaderForbiddenText: cwdCanary,
    }).start()
    try {
      const result = await runGate(gateArguments(run, server), {}, { cwd: loaderCwd })
      assert.equal(result.code, 0, JSON.stringify({ stderr: result.stderr, fixtureErrors: server.fixtureErrors }))
      assert.equal(server.loadedConfigurations.length, 2)
      assert.equal(server.loadedConfigurations.every((loaded) => loaded.resolvedSystemLeaf === "secret file.md\n"), true)
      assert.equal(
        server.loadedConfigurations.every(
          (loaded) => loaded.inlineLiteral === "other_harness_refs/codex/codex-rs/core/gpt_5_1_prompt.md\n",
        ),
        true,
      )
      assert.equal(server.loadedConfigurations.every((loaded) => loaded.forbiddenTextObserved === false), true)
      assert.equal(server.loadedConfigurations.every((loaded) => loaded.privateReferences), true)
      assert.equal((await readFile(run.output, "utf8")).includes(cwdCanary), false)
    } finally {
      await server.stop()
      await rm(run.root, { recursive: true, force: true })
    }
  })
  await context.test("hardlinked top-level, extends, prompt, and tool source files", async (nested) => {
    for (const kind of ["top-level", "extends", "prompt", "tool"]) {
      await nested.test(kind, async () => {
        const run = await makeRun(`config-hardlink-${kind}`)
        const canary = `HARDLINK_CONTENT_${kind.toUpperCase()}`
        const donor = join(run.root, `${kind}-donor`)
        let linkedPath = run.config
        if (kind === "top-level") {
          await unlink(run.config)
          await writeFile(donor, `version: 2\n# ${canary}\n`, { mode: 0o600 })
        } else if (kind === "extends") {
          linkedPath = join(run.root, "base.yaml")
          await writeFile(donor, `version: 2\n# ${canary}\n`, { mode: 0o600 })
          await writeFile(run.config, "version: 2\nextends:\n  - ./base.yaml\n", { mode: 0o600 })
        } else if (kind === "prompt") {
          linkedPath = join(run.root, "prompt.md")
          await writeFile(donor, canary, { mode: 0o600 })
          await writeFile(run.config, "version: 2\nprompts:\n  system: ./prompt.md\n", { mode: 0o600 })
        } else {
          const tools = join(run.root, "tools")
          await mkdir(tools, { mode: 0o700 })
          linkedPath = join(tools, "tool.yaml")
          await writeFile(donor, `name: ${canary}\n`, { mode: 0o600 })
          await writeFile(run.config, "version: 2\ntools:\n  registry:\n    paths:\n      - ./tools\n", { mode: 0o600 })
        }
        if (kind !== "top-level" && kind !== "tool") await unlink(linkedPath)
        if (kind === "tool") {
          await writeFile(
            run.config,
            `version: 2\ntools:\n  registry:\n    paths:\n      - ${JSON.stringify(await realpath(dirname(linkedPath)))}\n`,
            { mode: 0o600 },
          )
        }
        await link(donor, linkedPath)
        const server = await new FakeCanonicalServer().start()
        try {
          const result = await runGate(gateArguments(run, server))
          const error = parseFailure(result)
          assert.equal(error.code, "config_closure_hardlink_unsupported")
          assert.equal(result.stdout.includes(canary), false)
          assert.equal(result.stderr.includes(canary), false)
          assert.deepEqual(server.requests, [])
          await assertNoOutput(run.output)
        } finally {
          await server.stop()
          await rm(run.root, { recursive: true, force: true })
        }
      })
    }
  })

})

test("snapshot overwrite and exact restore still fails closed before evidence", { concurrency: false }, async () => {
  const run = await makeRun("snapshot-overwrite-restore")
  const server = await new FakeCanonicalServer({ mutateSnapshotDuringCreateCount: 1 }).start()
  try {
    const error = parseFailure(await runGate(gateArguments(run, server)))
    assert.equal(error.code, "config_snapshot_mutated")
    await assertNoOutput(run.output)
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})


test("writer abort refuses success when output sidecars cannot be removed", { concurrency: false }, async () => {
  const run = await makeRun("writer-abort-unlink", { separateOutput: true })
  const server = await new FakeCanonicalServer({
    dirty: true,
    onStatus: async () => {
      await chmod(run.outputDirectory, 0o500)
    },
  }).start()
  try {
    const error = parseFailure(await runGate(gateArguments(run, server)))
    assert.equal(error.code, "required_cleanup_failed")
    await assertNoOutput(run.output)
    const sidecars = await readdir(run.outputDirectory)
    assert.equal(sidecars.some((name) => name.endsWith(".bb89n14.lock")), true)
    assert.equal(sidecars.some((name) => name.includes(".tmp.")), false)
  } finally {
    await chmod(run.outputDirectory, 0o700)
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("required configuration cleanup failure invalidates evidence and leaves no writer temporary", { concurrency: false }, async () => {
  const run = await makeRun("cleanup-failure", { separateOutput: true })
  const server = await new FakeCanonicalServer({ lockSnapshotAfterCreateCount: 2 }).start()
  try {
    const error = parseFailure(await runGate(gateArguments(run, server)))
    assert.equal(error.code, "required_cleanup_failed")
    await assertNoOutput(run.output)
    assert.deepEqual(await readdir(run.outputDirectory), [])
    assert.ok(server.lockedSnapshotRoot)
    assert.equal((await lstat(server.lockedSnapshotLink)).isSymbolicLink(), true)
  } finally {
    try {
      await server.stop()
    } finally {
      if (server.lockedSnapshotLink) await unlink(server.lockedSnapshotLink).catch(() => undefined)
      if (server.lockedSnapshotRoot) {
        await unlockFixtureTree(server.lockedSnapshotRoot)
        await rm(server.lockedSnapshotRoot, { recursive: true, force: true })
      }
      await rm(run.root, { recursive: true, force: true })
    }
  }
})

test("dirty and indeterminate backend provenance fail closed", { concurrency: false }, async (context) => {
  for (const dirty of [true, null]) {
    await context.test(String(dirty), async () => {
      const run = await makeRun(`dirty-${dirty}`)
      const server = await new FakeCanonicalServer({ dirty }).start()
      try {
        const error = parseFailure(await runGate(gateArguments(run, server)))
        assert.equal(error.code, "backend_revision_not_clean")
        await assertNoOutput(run.output)
      } finally {
        await server.stop()
        await rm(run.root, { recursive: true, force: true })
      }
    })
  }
})

test("private descendant repository artifact mismatch is rejected before auth or backend access", { concurrency: false }, async () => {
  const run = await makeRun("artifact-mismatch")
  const server = await new FakeCanonicalServer().start()
  const fixture = await makePrivateRepositoryFixture("artifact-mismatch")
  const artifact = join(fixture.sdkRoot, "dist", "index.js")
  const canary = "ARTIFACT_AUTH_CANARY_89N14"
  try {
    const original = await readFile(artifact)
    await writeFile(artifact, Buffer.concat([original, Buffer.from("\n// private mismatch\n")]))
    const result = await runGate(
      gateArguments(run, server),
      { BB89N14_AUTH_TOKEN: canary },
      { scriptPath: fixture.scriptPath, cwd: fixture.sdkRoot, invokeModule: true },
    )
    const error = parseFailure(result)
    assert.equal(error.code, "client_artifact_mismatch")
    assert.equal(result.stderr.includes(canary), false)
    assert.equal(server.requests.length, 0)
    await assertNoOutput(run.output)
  } finally {
    await server.stop()
    await rm(fixture.parent, { recursive: true, force: true })
    await rm(run.root, { recursive: true, force: true })
  }
})

test("untrusted stream codes are redacted and never echo provider bodies", { concurrency: false }, async () => {
  const run = await makeRun("stream-redaction")
  const canary = "STREAM_SECRET\nprovider-body"
  const server = await new FakeCanonicalServer({ streamGapCode: canary }).start()
  try {
    const result = await runGate(gateArguments(run, server))
    const error = parseFailure(result)
    assert.equal(error.type, "canonical_client_failure")
    assert.equal(error.code, "redacted")
    assert.equal(result.stderr.includes("STREAM_SECRET"), false)
    await assertNoOutput(run.output)
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("closed per-kind projection and event string budget reject arbitrary or oversized payloads", { concurrency: false }, async (context) => {
  for (const [name, options, expectedCode] of [
    ["arbitrary", { unsafeTurnPayload: { unsafe: "PAYLOAD_CANARY_89N14" } }, "unexpected_event_payload_keys"],
    ["oversized", { oversizedAssistant: true }, "event_text_too_large"],
  ]) {
    await context.test(name, async () => {
      const run = await makeRun(`closed-${name}`)
      const server = await new FakeCanonicalServer(options).start()
      try {
        const result = await runGate(gateArguments(run, server))
        const error = parseFailure(result)
        assert.equal(error.code, expectedCode)
        assert.equal(result.stderr.includes("PAYLOAD_CANARY"), false)
        await assertNoOutput(run.output)
      } finally {
        await server.stop()
        await rm(run.root, { recursive: true, force: true })
      }
    })
  }
})

test("configuration snapshot rejects symlink ancestors and unresolved extends or tool references before backend access", { concurrency: false }, async (context) => {
  await context.test("symlink ancestor", async () => {
    const run = await makeRun("config-symlink")
    const outside = await mkdtemp(join(tmpdir(), "bb89n14-config-outside-"))
    const server = await new FakeCanonicalServer().start()
    try {
      await writeFile(join(outside, "private.md"), "must not be mirrored\n", { mode: 0o600 })
      await symlink(outside, join(run.root, "linked"))
      await writeFile(run.config, "prompts:\n  system: linked/private.md\n", { mode: 0o600 })
      const error = parseFailure(await runGate(gateArguments(run, server)))
      assert.equal(error.code, "config_closure_symlink_unsupported")
      assert.equal(server.requests.length, 0)
      await assertNoOutput(run.output)
    } finally {
      await server.stop()
      await rm(outside, { recursive: true, force: true })
      await rm(run.root, { recursive: true, force: true })
    }
  })
  await context.test("bounded YAML alias expansion", async () => {
    const run = await makeRun("config-alias-expansion")
    const server = await new FakeCanonicalServer().start()
    try {
      const levels = ["a: &a [x,x,x,x,x,x,x,x,x,x]"]
      for (const name of ["b", "c", "d", "e", "f", "g", "h"]) {
        const prior = String.fromCharCode(name.charCodeAt(0) - 1)
        levels.push(`${name}: &${name} [${Array(10).fill(`*${prior}`).join(",")}]`)
      }
      await writeFile(run.config, `${levels.join("\n")}\nprompts: *h\n`, { mode: 0o600 })
      const error = parseFailure(await runGate(gateArguments(run, server)))
      assert.equal(error.code, "config_yaml_invalid")
      assert.equal(server.requests.length, 0)
      await assertNoOutput(run.output)
    } finally {
      await server.stop()
      await rm(run.root, { recursive: true, force: true })
    }
  })
  for (const [name, source, code] of [
    ["extends", "extends: missing.yaml\n", "config_snapshot_helper_failed"],
    ["tool registry", "tools:\n  registry:\n    paths: [missing/tools]\n", "config_tool_reference_unresolved"],
  ]) {
    await context.test(name, async () => {
      const run = await makeRun(`config-unresolved-${name.replaceAll(" ", "-")}`)
      const server = await new FakeCanonicalServer().start()
      try {
        await writeFile(run.config, source, { mode: 0o600 })
        const error = parseFailure(await runGate(gateArguments(run, server)))
        assert.equal(error.code, code)
        assert.equal(server.requests.length, 0)
        await assertNoOutput(run.output)
      } finally {
        await server.stop()
        await rm(run.root, { recursive: true, force: true })
      }
    })
  }
})

test("unsafe stale reservation sidecars are handled without deleting existing evidence", { concurrency: false }, async (context) => {
  for (const kind of ["symlink", "wrong-mode", "mode-000", "directory", "nonempty-directory", "hardlink"]) {
    await context.test(kind, async () => {
      const run = await makeRun(`reservation-${kind}`, { separateOutput: true })
      const server = await new FakeCanonicalServer().start()
      const lockPath = join(run.outputDirectory, ".evidence.json.bb89n14.lock")
      const auxiliary = join(run.outputDirectory, `reservation-${kind}-target`)
      try {
        await writeFile(run.output, "stale-evidence\n", { mode: 0o600 })
        if (kind === "symlink") {
          await writeFile(auxiliary, "protected\n", { mode: 0o600 })
          await symlink(auxiliary, lockPath)
        } else if (kind === "wrong-mode" || kind === "mode-000") {
          await writeFile(lockPath, "unsafe\n", { mode: kind === "mode-000" ? 0o000 : 0o644 })
        } else if (kind === "directory" || kind === "nonempty-directory") {
          await mkdir(lockPath, { mode: 0o700 })
          if (kind === "nonempty-directory") {
            await writeFile(join(lockPath, "protected"), "protected\n", { mode: 0o600 })
          }
        } else {
          await writeFile(auxiliary, "protected\n", { mode: 0o600 })
          await link(auxiliary, lockPath)
        }
        const error = parseFailure(await runGate(gateArguments({ ...run, config: join(run.root, "missing.yaml") }, server)))
        assert.equal(error.code, "output_already_exists")
        assert.equal(await readFile(run.output, "utf8"), "stale-evidence\n")
        if (kind === "nonempty-directory") {
          assert.equal(await readFile(join(lockPath, "protected"), "utf8"), "protected\n")
        } else {
          await assertNoOutput(lockPath)
        }
        if (kind === "symlink" || kind === "hardlink") {
          assert.equal(await readFile(auxiliary, "utf8"), "protected\n")
        }
        assert.equal(server.requests.length, 0)
      } finally {
        await server.stop()
        await rm(run.root, { recursive: true, force: true })
      }
    })
  }
})

test("output symlinks are rejected without modifying their targets", { concurrency: false }, async () => {
  const run = await makeRun("output-symlink")
  const server = await new FakeCanonicalServer().start()
  const target = join(run.root, "target.json")
  try {
    await writeFile(target, "protected\n", { mode: 0o600 })
    await symlink(target, run.output)
    const error = parseFailure(await runGate(gateArguments(run, server)))
    assert.equal(error.code, "output_already_exists")
    assert.equal(await readFile(target, "utf8"), "protected\n")
    assert.equal((await lstat(run.output)).isSymbolicLink(), true)
    assert.equal(server.requests.length, 0)
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("output ancestor replacement is detected before no-replace commit", { concurrency: false }, async () => {
  const run = await makeRun("ancestor-swap", { separateOutput: true })
  const backup = `${run.outputDirectory}-original`
  const attacker = join(run.root, "attacker")
  await mkdir(attacker, { mode: 0o700 })
  const server = await new FakeCanonicalServer({
    onStatus: async () => {
      await rename(run.outputDirectory, backup)
      await symlink(attacker, run.outputDirectory)
    },
  }).start()
  try {
    const error = parseFailure(await runGate(gateArguments(run, server)))
    assert.equal(error.code, "output_directory_changed")
    await assertNoOutput(join(attacker, "evidence.json"))
    await assertNoOutput(join(backup, "evidence.json"))
  } finally {
    await server.stop()
    await unlink(run.outputDirectory).catch(() => undefined)
    await rm(run.root, { recursive: true, force: true })
  }
})

test("exclusive output reservation yields one success and one fail-closed collision without invalidating the winner", { concurrency: false }, async () => {
  const run = await makeRun("output-collision", { separateOutput: true })
  const server = await new FakeCanonicalServer({
    onStatus: async () => new Promise((resolveDelay) => setTimeout(resolveDelay, 250)),
  }).start()
  try {
    const [first, second] = await Promise.all([
      runGate(gateArguments(run, server)),
      runGate(gateArguments(run, server)),
    ])
    const successes = [first, second].filter((result) => result.code === 0)
    const failures = [first, second].filter((result) => result.code === 1)
    assert.equal(successes.length, 1)
    assert.equal(failures.length, 1)
    assert.equal(parseFailure(failures[0]).code, "output_reserved")
    assert.deepEqual(validateGateEvidence(JSON.parse(await readFile(run.output, "utf8"))), JSON.parse(await readFile(run.output, "utf8")))
    assert.deepEqual((await readdir(run.outputDirectory)).sort(), ["evidence.json"])
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("no-replace commit preserves a foreign output that reappears during the reserved run", { concurrency: false }, async () => {
  const run = await makeRun("output-reappeared", { separateOutput: true })
  const foreign = "foreign-output-must-survive\n"
  const server = await new FakeCanonicalServer({
    onStatus: async () => writeFile(run.output, foreign, { mode: 0o600, flag: "wx" }),
  }).start()
  try {
    const error = parseFailure(await runGate(gateArguments(run, server)))
    assert.equal(error.code, "output_reappeared")
    assert.equal(await readFile(run.output, "utf8"), foreign)
    assert.deepEqual((await readdir(run.outputDirectory)).sort(), ["evidence.json"])
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("isolated Python helpers ignore cwd import shadowing", { concurrency: false }, async () => {
  const run = await makeRun("python-isolation")
  const marker = join(run.root, "python-import-hijacked")
  const server = await new FakeCanonicalServer().start()
  try {
    await writeFile(
      join(run.root, "signal.py"),
      'from pathlib import Path\nPath("python-import-hijacked").write_text("hijacked")\n',
      { mode: 0o600 },
    )
    const result = await runGate(gateArguments(run, server), {}, { cwd: run.root })
    assert.equal(result.code, 0, result.stderr)
    await assert.rejects(readFile(marker), (error) => error.code === "ENOENT")
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})

test("one absolute deadline bounds stalled cleanup and cancels stalled or oversized provenance bodies", { concurrency: false }, async (context) => {
  for (const [name, options] of [
    ["stream", { slowStream: true }],
    ["provenance-body", { stallStatusBody: true }],
    ["oversized-provenance-body", { oversizedStatusBody: true }],
  ]) {
    await context.test(name, async () => {
      const run = await makeRun(`deadline-${name}`)
      const server = await new FakeCanonicalServer(options).start()
      try {
        const timeoutMs = 3000
        const result = await runGate(gateArguments(run, server, ["--timeout-ms", String(timeoutMs)]))
        const error = parseFailure(result)
        assert.ok(
          ["event_timeout", "response_timeout", "response_body_budget_exceeded", "absolute_deadline_exceeded", "evidence_production_timeout", "required_cleanup_failed"].includes(error.code),
          JSON.stringify({
            error,
            fixtureErrors: server.fixtureErrors,
            requests: server.requests.map(({ method, path, status }) => ({ method, path, status })),
          }),
        )
        if (name === "stream") {
          assert.equal(
            server.requests.some(({ path }) => /^\/v1\/sessions\/[^/]+\/events$/.test(path)),
            true,
          )
        } else {
          assert.equal(server.requests.some(({ path }) => path === "/v1/status"), true)
        }
        assert.ok(result.elapsedMs <= timeoutMs + DEADLINE_TOLERANCE_MS, `elapsed ${result.elapsedMs}ms`)
        await assertNoOutput(run.output)
      } finally {
        await server.stop()
        await rm(run.root, { recursive: true, force: true })
      }
    })
  }
})

test("duplicate and conflicting post-terminal events fail while draining the captured head", { concurrency: false }, async (context) => {
  for (const [name, options] of [
    ["duplicate", { duplicateTerminal: true }],
    ["conflict", { conflictingTerminal: true }],
  ]) {
    await context.test(name, async () => {
      const run = await makeRun(`terminal-${name}`)
      const server = await new FakeCanonicalServer(options).start()
      try {
        const result = await runGate(gateArguments(run, server))
        const error = parseFailure(result)
        assert.equal(error.type, "canonical_client_failure")
        assert.equal(error.code, "duplicate_terminal_transition")
        await assertNoOutput(run.output)
      } finally {
        await server.stop()
        await rm(run.root, { recursive: true, force: true })
      }
    })
  }
})

test("final snapshot must exactly match the expected live provider model", { concurrency: false }, async () => {
  const run = await makeRun("model")
  const server = await new FakeCanonicalServer({ modelMismatch: "openai/other-model" }).start()
  try {
    const error = parseFailure(await runGate(gateArguments(run, server)))
    assert.equal(error.code, "provider_model_mismatch")
    await assertNoOutput(run.output)
  } finally {
    await server.stop()
    await rm(run.root, { recursive: true, force: true })
  }
})
