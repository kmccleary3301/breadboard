const vscode = require('vscode')
const cp = require('node:child_process')
const fs = require('node:fs')
const net = require('node:net')
const os = require('node:os')
const path = require('node:path')
const { loadScenario } = require('./scenarioModel.cjs')
const { ensureDir, writeJson, appendJsonl, writeText } = require('./artifacts.cjs')
const { TerminalBufferMirror } = require('./terminalBuffer.cjs')

const nowIso = () => new Date().toISOString()
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

class HarnessPty {
  constructor(options) {
    this.options = options
    this.writeEmitter = new vscode.EventEmitter()
    this.closeEmitter = new vscode.EventEmitter()
    this.dimensionEmitter = new vscode.EventEmitter()
    this.onDidWrite = this.writeEmitter.event
    this.onDidClose = this.closeEmitter.event
    this.onDidOverrideDimensions = this.dimensionEmitter.event
    this.output = ''
    this.frames = []
    this.inputIndex = 0
    this.closed = false
    this.cols = options.cols
    this.rows = options.rows
    this.backend = 'unstarted'
    this.mirror = new TerminalBufferMirror(this.cols, this.rows)
    this.bridgeReady = false
    this.bridgeQueue = []
    this.bridgeBuffer = ''
  }

  open(initialDimensions) {
    this.cols = initialDimensions?.columns || this.cols
    this.rows = initialDimensions?.rows || this.rows
    this.dimensionEmitter.fire({ columns: this.cols, rows: this.rows })
    this.spawnShell()
  }

  close() {
    this.closed = true
    try { this.child?.kill('SIGTERM') } catch {}
    try { this.childProcess?.kill('SIGTERM') } catch {}
    try { this.bridgeClient?.write(`${JSON.stringify({ kind: 'kill' })}\n`) } catch {}
    try { this.bridgeClient?.destroy() } catch {}
    try { this.bridgeProcess?.kill('SIGTERM') } catch {}
    this.closeEmitter.fire(0)
  }

  handleInput(data) {
    this.writeInput(data, 'terminal-input')
  }

  spawnShell() {
    const cwd = this.options.cwd
    const env = { ...process.env, ...this.options.env }
    if (this.startExternalPtyBridge(cwd, env)) return
    this.spawnScriptFallback(cwd, env, 'external bridge unavailable')
  }

  startExternalPtyBridge(cwd, env) {
    this.backend = 'external-node-pty-starting'
    const socketPath = path.join(os.tmpdir(), `bb-vscode-pty-${process.pid}-${Date.now()}-${Math.floor(Math.random() * 100000)}.sock`)
    const serverPath = path.join(__dirname, 'ptyBridgeServer.cjs')
    const nodeBin = process.env.BREADBOARD_VSCODE_NODE_BIN || 'node'
    const bridge = cp.spawn(nodeBin, [serverPath, socketPath], { cwd, env, stdio: ['ignore', 'pipe', 'pipe'] })
    this.bridgeProcess = bridge
    appendJsonl(path.join(this.options.artifactDir, 'process_events.ndjson'), {
      t: nowIso(),
      event: 'backend-starting',
      backend: 'external-node-pty',
      nodeBin,
      socketPath,
    })
    bridge.stdout.on('data', (chunk) => {
      appendJsonl(path.join(this.options.artifactDir, 'process_events.ndjson'), { t: nowIso(), event: 'bridge-stdout', text: chunk.toString('utf8') })
    })
    bridge.stderr.on('data', (chunk) => {
      appendJsonl(path.join(this.options.artifactDir, 'process_events.ndjson'), { t: nowIso(), event: 'bridge-stderr', text: chunk.toString('utf8') })
    })
    bridge.on('exit', (code, signal) => {
      appendJsonl(path.join(this.options.artifactDir, 'process_events.ndjson'), { t: nowIso(), event: 'bridge-exit', code, signal })
      if (!this.bridgeReady && !this.childProcess) this.spawnScriptFallback(cwd, env, `external bridge exited before ready: ${code ?? signal}`)
    })
    bridge.on('error', (error) => {
      appendJsonl(path.join(this.options.artifactDir, 'process_events.ndjson'), { t: nowIso(), event: 'bridge-error', message: error.message })
      if (!this.bridgeReady && !this.childProcess) this.spawnScriptFallback(cwd, env, error.message)
    })
    this.connectExternalPtyBridge(socketPath, cwd, env, 0)
    return true
  }

  connectExternalPtyBridge(socketPath, cwd, env, attempt) {
    if (this.closed || this.bridgeReady || this.childProcess) return
    if (attempt > 50) {
      appendJsonl(path.join(this.options.artifactDir, 'process_events.ndjson'), {
        t: nowIso(),
        event: 'bridge-connect-timeout',
        socketPath,
      })
      this.spawnScriptFallback(cwd, env, 'external bridge connect timeout')
      return
    }
    const client = net.createConnection(socketPath)
    client.on('connect', () => {
      this.bridgeClient = client
      this.bridgeReady = true
      this.backend = 'external-node-pty'
      appendJsonl(path.join(this.options.artifactDir, 'process_events.ndjson'), { t: nowIso(), event: 'backend', backend: this.backend })
      this.sendBridgeMessage({ kind: 'start', cwd, cols: this.cols, rows: this.rows, env: this.options.env })
      for (const message of this.bridgeQueue.splice(0)) this.sendBridgeMessage(message)
    })
    client.on('data', (chunk) => this.handleBridgeData(chunk.toString('utf8')))
    client.on('error', () => {
      try { client.destroy() } catch {}
      setTimeout(() => this.connectExternalPtyBridge(socketPath, cwd, env, attempt + 1), 100)
    })
  }

  handleBridgeData(chunk) {
    this.bridgeBuffer += chunk
    let newline
    while ((newline = this.bridgeBuffer.indexOf('\n')) >= 0) {
      const line = this.bridgeBuffer.slice(0, newline)
      this.bridgeBuffer = this.bridgeBuffer.slice(newline + 1)
      if (!line.trim()) continue
      try {
        const message = JSON.parse(line)
        if (message.event === 'data') this.recordOutput(message.data || '')
        if (message.event === 'exit') {
          appendJsonl(path.join(this.options.artifactDir, 'process_events.ndjson'), { t: nowIso(), event: 'exit', code: message.code, signal: message.signal })
          if (!this.closed) this.closeEmitter.fire(typeof message.code === 'number' ? message.code : 1)
        }
        if (message.event === 'error') appendJsonl(path.join(this.options.artifactDir, 'process_events.ndjson'), { t: nowIso(), event: 'bridge-message-error', message: message.message })
      } catch (error) {
        appendJsonl(path.join(this.options.artifactDir, 'process_events.ndjson'), { t: nowIso(), event: 'bridge-parse-error', message: error.message, line })
      }
    }
  }

  sendBridgeMessage(message) {
    if (!this.bridgeReady || !this.bridgeClient || this.bridgeClient.destroyed) {
      this.bridgeQueue.push(message)
      return
    }
    this.bridgeClient.write(`${JSON.stringify(message)}\n`)
  }

  spawnScriptFallback(cwd, env, reason) {
    if (this.childProcess || this.bridgeReady || this.closed) return
    this.backend = 'script-fallback'
    appendJsonl(path.join(this.options.artifactDir, 'process_events.ndjson'), {
      t: nowIso(),
      event: 'backend-fallback',
      backend: this.backend,
      message: reason,
    })
    const scriptArgs = ['-qfec', 'bash --noprofile --norc', '/dev/null']
    const child = cp.spawn('script', scriptArgs, { cwd, env, stdio: ['pipe', 'pipe', 'pipe'] })
    this.childProcess = child
    child.stdout.on('data', (chunk) => this.recordOutput(chunk.toString('utf8')))
    child.stderr.on('data', (chunk) => this.recordOutput(chunk.toString('utf8')))
    child.on('exit', (code, signal) => {
      appendJsonl(path.join(this.options.artifactDir, 'process_events.ndjson'), { t: nowIso(), event: 'exit', code, signal })
      if (!this.closed) this.closeEmitter.fire(typeof code === 'number' ? code : 1)
    })
    child.on('error', (error) => {
      appendJsonl(path.join(this.options.artifactDir, 'process_events.ndjson'), { t: nowIso(), event: 'error', message: error.message })
      this.recordOutput(`\r\n[VSCode harness shell error: ${error.message}]\r\n`)
    })
    for (const message of this.bridgeQueue.splice(0)) {
      if (message.kind === 'input') child.stdin.write((message.text || '').replace(/\r/g, '\n'))
    }
  }

  recordOutput(text) {
    this.output += text
    this.mirror.write(text)
    fs.appendFileSync(path.join(this.options.artifactDir, 'pty_raw.ansi'), text)
    this.writeEmitter.fire(text)
  }

  writeInput(text, source = 'scenario') {
    appendJsonl(path.join(this.options.artifactDir, 'input_log.ndjson'), { t: nowIso(), index: this.inputIndex++, source, text })
    if (this.backend === 'external-node-pty' || this.backend === 'external-node-pty-starting') {
      this.sendBridgeMessage({ kind: 'input', text })
    } else {
      this.childProcess?.stdin?.write(text.replace(/\r/g, '\n'))
    }
  }

  resize(cols, rows) {
    this.cols = cols
    this.rows = rows
    this.dimensionEmitter.fire({ columns: cols, rows })
    this.mirror.resize(cols, rows)
    if (this.backend === 'external-node-pty' || this.backend === 'external-node-pty-starting') this.sendBridgeMessage({ kind: 'resize', cols, rows })
    appendJsonl(path.join(this.options.artifactDir, 'dimension_events.ndjson'), { t: nowIso(), cols, rows, backend: this.backend })
  }

  async snapshot(label) {
    await this.mirror.flush()
    const frame = {
      t: nowIso(),
      label,
      rawTail: this.output.slice(-20000),
      plainTail: plainText(this.output.slice(-20000)),
      viewport: this.mirror.viewportText(),
      bufferTail: this.mirror.bufferText(500),
      terminalState: this.mirror.state(),
      backend: this.backend,
      cols: this.cols,
      rows: this.rows,
    }
    this.frames.push(frame)
    appendJsonl(path.join(this.options.artifactDir, 'terminal_frames.ndjson'), frame)
    return frame
  }

  async parsedTexts() {
    await this.mirror.flush()
    return {
      rawPlain: plainText(this.output),
      viewport: this.mirror.viewportText(),
      buffer: this.mirror.bufferText(),
      state: this.mirror.state(),
      backend: this.backend,
    }
  }
}

function plainText(raw) {
  return raw
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
    .replace(/\x1b\][^\x07]*(\x07|\x1b\\)/g, '')
    .replace(/\r/g, '')
}

async function waitFor(predicate, timeoutMs, label) {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    if (await predicate()) return true
    await sleep(100)
  }
  throw new Error(`Timed out waiting for ${label} after ${timeoutMs}ms`)
}

const KEY_INPUT = {
  enter: '\r',
  return: '\r',
  escape: '\u001b',
  esc: '\u001b',
  tab: '\t',
  backspace: '\u007f',
  delete: '\u001b[3~',
  up: '\u001b[A',
  down: '\u001b[B',
  right: '\u001b[C',
  left: '\u001b[D',
  home: '\u001b[H',
  end: '\u001b[F',
  pageup: '\u001b[5~',
  pagedown: '\u001b[6~',
  ctrlc: '\u0003',
  ctrld: '\u0004',
  ctrlo: '\u000f',
  ctrlt: '\u0014',
}

function keyInput(key) {
  const normalized = String(key || '').toLowerCase().replace(/[^a-z0-9]/g, '')
  const value = KEY_INPUT[normalized]
  if (!value) throw new Error(`Unsupported key: ${key}`)
  return value
}

function assertionText(scope, texts) {
  if (scope === 'raw') return texts.rawPlain
  if (scope === 'viewport') return texts.viewport
  return texts.buffer
}

async function waitStepText(pty, step) {
  const scope = step.scope || 'raw'
  if (scope === 'raw') return plainText(pty.output)
  return assertionText(scope, await pty.parsedTexts())
}

function evaluateAssertions(scenario, texts) {
  const anomalies = []
  for (const assertion of scenario.assertions || []) {
    const scope = assertion.scope || 'buffer'
    const text = assertionText(scope, texts)
    const occurrences = assertion.text ? text.split(assertion.text).length - 1 : 0
    if (assertion.kind === 'contains' && !text.includes(assertion.text)) anomalies.push({ kind: 'missing-text', scope, text: assertion.text })
    if (assertion.kind === 'notContains' && text.includes(assertion.text)) anomalies.push({ kind: 'forbidden-text', scope, text: assertion.text })
    if (assertion.kind === 'regex' && !(new RegExp(assertion.pattern).test(text))) anomalies.push({ kind: 'missing-regex', scope, pattern: assertion.pattern })
    if (assertion.kind === 'countAtMost' && occurrences > assertion.count) anomalies.push({ kind: 'too-many-occurrences', scope, text: assertion.text, count: occurrences, max: assertion.count })
    if (assertion.kind === 'countAtLeast' && occurrences < assertion.count) anomalies.push({ kind: 'too-few-occurrences', scope, text: assertion.text, count: occurrences, min: assertion.count })
  }
  return anomalies
}

function safeLabel(value, fallback) {
  return String(value || fallback).replace(/[^a-zA-Z0-9._-]+/g, '_').slice(0, 80) || fallback
}

async function writeVisualEquivalentScreenshot(pty, artifactDir, label) {
  const safe = safeLabel(label, 'screenshot')
  const screenshotDir = path.join(artifactDir, 'screenshots')
  ensureDir(screenshotDir)
  const parsed = await pty.parsedTexts()
  const textPath = path.join(screenshotDir, `${safe}.txt`)
  const jsonPath = path.join(screenshotDir, `${safe}.json`)
  writeText(textPath, parsed.viewport)
  writeJson(jsonPath, {
    kind: 'parsed-viewport-visual-equivalent',
    label,
    textPath,
    capturedAt: nowIso(),
    backend: parsed.backend,
    state: parsed.state,
    caveat: 'This is parsed terminal viewport truth from @xterm/headless, not a pixel screenshot.',
  })
}

function exportBreadBoardState(artifactDir, label) {
  const safe = safeLabel(label, 'state')
  const sourceDir = path.join(artifactDir, 'breadboard_artifacts')
  const targetDir = path.join(sourceDir, 'exports')
  ensureDir(targetDir)
  const files = fs.existsSync(sourceDir)
    ? fs.readdirSync(sourceDir).filter((name) => name !== 'exports').sort()
    : []
  writeJson(path.join(targetDir, `${safe}.json`), {
    kind: 'breadboard-state-export',
    label,
    exportedAt: nowIso(),
    files: files.map((name) => {
      const filePath = path.join(sourceDir, name)
      const stat = fs.statSync(filePath)
      return { name, bytes: stat.size }
    }),
  })
}

async function waitForStableFrame(pty, options) {
  const scope = options.scope || 'viewport'
  const stableMs = options.stableMs || 500
  const timeoutMs = options.timeoutMs || 10000
  const start = Date.now()
  let last = null
  let stableSince = Date.now()
  while (Date.now() - start < timeoutMs) {
    const texts = await pty.parsedTexts()
    const current = assertionText(scope, texts)
    if (current !== last) {
      last = current
      stableSince = Date.now()
    } else if (Date.now() - stableSince >= stableMs) {
      return true
    }
    await sleep(100)
  }
  throw new Error(`Timed out waiting for stable ${scope} frame after ${timeoutMs}ms`)
}

function readLatestStateRecord(statePath) {
  if (!fs.existsSync(statePath)) return null
  const raw = fs.readFileSync(statePath, 'utf8').trim()
  if (!raw) return null
  const lines = raw.split(/\n/).filter(Boolean)
  for (let i = lines.length - 1; i >= 0; i -= 1) {
    try {
      const parsed = JSON.parse(lines[i])
      if (parsed && typeof parsed === 'object' && parsed.state) return parsed
    } catch {}
  }
  return null
}

function stateTimestamp(record) {
  const value = Number(record?.timestamp ?? 0)
  return Number.isFinite(value) ? value : 0
}

function recordStateMatches(record, criteria, baselineTimestamp = Number.NEGATIVE_INFINITY) {
  if (!record?.state) return false
  const state = record.state
  const conversation = Array.isArray(state.conversation) ? state.conversation : []
  const toolEvents = Array.isArray(state.toolEvents) ? state.toolEvents : []
  const lastConversation = state.lastConversation ?? (
    conversation.length > 0 ? conversation[conversation.length - 1] : null
  )
  const lastToolEvent = state.lastToolEvent ?? (
    toolEvents.length > 0 ? toolEvents[toolEvents.length - 1] : null
  )
  const conversationCount = Number(state.counts?.conversation ?? conversation.length)
  const toolEventCount = Number(state.counts?.toolEvents ?? toolEvents.length)
  const eventCount = Number(state.stats?.eventCount ?? 0)
  if (criteria.fresh && stateTimestamp(record) <= baselineTimestamp) return false
  if (criteria.pendingResponse != null && state.pendingResponse !== criteria.pendingResponse) return false
  if (criteria.disconnected != null && state.disconnected !== criteria.disconnected) return false
  if (criteria.mainFollowTail != null && state.mainFollowTail !== criteria.mainFollowTail) return false
  if (criteria.statusIncludes && !String(state.status ?? '').includes(criteria.statusIncludes)) return false
  if (criteria.conversationCountAtLeast != null && conversationCount < criteria.conversationCountAtLeast) return false
  if (criteria.toolEventsCountAtLeast != null && toolEventCount < criteria.toolEventsCountAtLeast) return false
  if (criteria.eventCountAtLeast != null && eventCount < criteria.eventCountAtLeast) return false
  const lastConversationPreview = String(lastConversation?.preview ?? lastConversation?.text ?? '')
  if (criteria.lastConversationSpeaker && String(lastConversation?.speaker ?? '') !== criteria.lastConversationSpeaker) return false
  if (criteria.lastConversationPhase && String(lastConversation?.phase ?? '') !== criteria.lastConversationPhase) return false
  if (criteria.lastConversationPreviewIncludes && !lastConversationPreview.includes(criteria.lastConversationPreviewIncludes)) return false
  if (criteria.lastToolEventKind && String(lastToolEvent?.kind ?? '') !== criteria.lastToolEventKind) return false
  if (criteria.lastToolEventStatus && String(lastToolEvent?.status ?? '') !== criteria.lastToolEventStatus) return false
  if (criteria.lastToolEventTextIncludes && !String(lastToolEvent?.text ?? '').includes(criteria.lastToolEventTextIncludes)) return false
  return true
}

async function waitForBreadBoardState(statePath, criteria) {
  const timeoutMs = criteria.timeoutMs || 10000
  const start = Date.now()
  const baselineRecord = readLatestStateRecord(statePath)
  const baselineTimestamp = stateTimestamp(baselineRecord)
  while (Date.now() - start < timeoutMs) {
    const record = readLatestStateRecord(statePath)
    if (recordStateMatches(record, criteria, baselineTimestamp)) return true
    await sleep(100)
  }
  throw new Error(`Timed out waiting for BreadBoard state after ${timeoutMs}ms: ${JSON.stringify(criteria)}`)
}

async function runScenario(context, scenarioPath, artifactDir) {
  ensureDir(artifactDir)
  const scenario = loadScenario(scenarioPath)
  const workspaceFolders = vscode.workspace.workspaceFolders || []
  const cwd = workspaceFolders[0]?.uri.fsPath || process.cwd()
  const cols = scenario.terminal?.initialCols || 100
  const rows = scenario.terminal?.initialRows || 30
  const breadboardArtifactDir = path.join(artifactDir, 'breadboard_artifacts')
  ensureDir(breadboardArtifactDir)
  writeJson(path.join(artifactDir, 'scenario.json'), scenario)
  writeJson(path.join(artifactDir, 'vscode_info.json'), {
    version: vscode.version,
    appName: vscode.env.appName,
    remoteName: vscode.env.remoteName || null,
    uiKind: vscode.env.uiKind,
    cwd,
    extensionPath: context.extensionPath,
    startedAt: nowIso(),
  })
  writeJson(path.join(artifactDir, 'extension_info.json'), {
    packagePath: path.join(context.extensionPath, 'package.json'),
    extensionPath: context.extensionPath,
    activationKind: 'auto-env-or-command',
    ptyBridgeServer: path.join(__dirname, 'ptyBridgeServer.cjs'),
    terminalBuffer: '@xterm/headless',
  })
  writeJson(path.join(artifactDir, 'workspace_settings.json'), {
    terminalIntegratedShellIntegrationEnabled: false,
    terminalIntegratedStickyScrollEnabled: false,
    terminalIntegratedScrollback: 10000,
    isolation: 'user-data-dir + extensions-dir + generated workspace',
  })
  writeJson(path.join(artifactDir, 'env.json'), {
    PATH: process.env.PATH || '',
    DISPLAY: process.env.DISPLAY || null,
    BREADBOARD_VSCODE_HARNESS_SCENARIO: scenarioPath,
    BREADBOARD_VSCODE_HARNESS_ARTIFACT_DIR: artifactDir,
    BREADBOARD_VSCODE_NODE_BIN: process.env.BREADBOARD_VSCODE_NODE_BIN || 'node',
  })
  writeJson(path.join(artifactDir, 'manifest.json'), {
    id: scenario.id,
    artifactDir,
    scenarioPath,
    startedAt: nowIso(),
    requiredFiles: ['scenario.json', 'vscode_info.json', 'extension_info.json', 'workspace_settings.json', 'env.json', 'pty_raw.ansi', 'pty_plain.txt', 'scrollback_final.txt', 'viewport_final.txt', 'terminal_state_final.json', 'terminal_frames.ndjson', 'input_log.ndjson', 'dimension_events.ndjson', 'screenshots/', 'breadboard_artifacts/', 'anomalies.json', 'verdict.json', 'summary.md'],
  })

  const pty = new HarnessPty({
    artifactDir,
    cwd,
    cols,
    rows,
    env: {
      BREADBOARD_TUI_VSCODE_HARNESS: '1',
      BREADBOARD_QC_BATCH_ID: 'p14v4-vscode',
      BREADBOARD_QC_CASE_ID: scenario.id,
      BREADBOARD_STATE_DUMP_PATH: path.join(breadboardArtifactDir, 'repl_state.ndjson'),
      BREADBOARD_STATE_DUMP_MODE: 'summary',
      BREADBOARD_STATE_DUMP_RATE_MS: '100',
      BREADBOARD_TUI_SURFACE_MODEL_FILE: path.join(breadboardArtifactDir, 'surface_model.ndjson'),
      BREADBOARD_TUI_SCROLLBACK_FEED_FILE: path.join(breadboardArtifactDir, 'scrollback_feed.ndjson'),
      BREADBOARD_TUI_COMPOSER_EVENTS_FILE: path.join(breadboardArtifactDir, 'composer_events.ndjson'),
      BREADBOARD_TUI_KEY_TRACE_FILE: path.join(breadboardArtifactDir, 'key_trace.ndjson'),
      BREADBOARD_TUI_RENDER_TIMELINE_FILE: path.join(breadboardArtifactDir, 'render_timeline.ndjson'),
      BREADBOARD_TUI_VIEWPORT_RESETS_FILE: path.join(breadboardArtifactDir, 'viewport_resets.ndjson'),
      BREADBOARD_TUI_MARKDOWN_METRICS_FILE: path.join(breadboardArtifactDir, 'markdown_metrics.ndjson'),
      BREADBOARD_TUI_MANAGED_REGION_BOUNDS_FILE: path.join(breadboardArtifactDir, 'managed_region_bounds.ndjson'),
      BREADBOARD_TUI_APP_START_ANCHOR_FILE: path.join(breadboardArtifactDir, 'app_start_anchor.json'),
      BREADBOARD_TUI_SCROLLBACK_CLAUSE_VERDICTS_FILE: path.join(breadboardArtifactDir, 'scrollback_clause_verdicts.json'),
    },
  })
  const terminal = vscode.window.createTerminal({ name: `BreadBoard Harness: ${scenario.id}`, pty })
  terminal.show(true)
  await sleep(1000)

  let ok = true
  let errorMessage = null
  for (let i = 0; i < scenario.steps.length; i += 1) {
    const step = scenario.steps[i]
    appendJsonl(path.join(artifactDir, 'step_events.ndjson'), { t: nowIso(), index: i, step })
    try {
      if (step.kind === 'write' || step.kind === 'paste') {
        pty.writeInput(step.text, step.kind)
      } else if (step.kind === 'key') {
        pty.writeInput(keyInput(step.key), `key:${step.key}`)
      } else if (step.kind === 'waitForText') {
        await waitFor(async () => (await waitStepText(pty, step)).includes(step.text), step.timeoutMs || 10000, `${step.scope || 'raw'} text ${step.text}`)
      } else if (step.kind === 'waitForRegex') {
        const re = new RegExp(step.pattern)
        await waitFor(async () => re.test(await waitStepText(pty, step)), step.timeoutMs || 10000, `${step.scope || 'raw'} regex ${step.pattern}`)
      } else if (step.kind === 'waitForNoText') {
        await waitFor(async () => !(await waitStepText(pty, step)).includes(step.text), step.timeoutMs || 10000, `${step.scope || 'raw'} no text ${step.text}`)
      } else if (step.kind === 'waitForStableFrame') {
        await waitForStableFrame(pty, step)
      } else if (step.kind === 'waitForState') {
        await waitForBreadBoardState(path.join(breadboardArtifactDir, 'repl_state.ndjson'), step)
      } else if (step.kind === 'resize') {
        pty.resize(step.cols, step.rows)
        await sleep(step.settleMs || 500)
      } else if (step.kind === 'snapshot') {
        await pty.snapshot(step.label || `step-${i}`)
      } else if (step.kind === 'screenshot') {
        await writeVisualEquivalentScreenshot(pty, artifactDir, step.label || `step-${i}`)
      } else if (step.kind === 'exportBreadBoardState') {
        exportBreadBoardState(artifactDir, step.label || `step-${i}`)
      } else if (step.kind === 'assert') {
        const stepAssertions = Array.isArray(step.assertions) ? step.assertions : [step.assertion]
        const stepAnomalies = evaluateAssertions({ assertions: stepAssertions }, await pty.parsedTexts())
        if (stepAnomalies.length > 0) throw new Error(`Step assertion failed: ${JSON.stringify(stepAnomalies)}`)
      } else if (step.kind === 'sleep') {
        await sleep(step.ms)
      } else if (step.kind === 'terminate') {
        pty.close()
      }
    } catch (error) {
      ok = false
      errorMessage = error.message
      appendJsonl(path.join(artifactDir, 'step_errors.ndjson'), { t: nowIso(), index: i, step, error: error.message })
      break
    }
  }

  await sleep(500)
  await pty.snapshot('final')
  const parsed = await pty.parsedTexts()
  writeText(path.join(artifactDir, 'pty_plain.txt'), parsed.rawPlain)
  writeText(path.join(artifactDir, 'scrollback_final.txt'), parsed.buffer)
  writeText(path.join(artifactDir, 'viewport_final.txt'), parsed.viewport)
  writeJson(path.join(artifactDir, 'terminal_state_final.json'), parsed.state)
  writeText(path.join(artifactDir, 'visible_frames.md'), pty.frames.map((f) => `## ${f.label}\n\nBackend: ${f.backend}\n\n### Parsed Viewport\n\n\`\`\`text\n${f.viewport}\n\`\`\`\n\n### Parsed Buffer Tail\n\n\`\`\`text\n${f.bufferTail}\n\`\`\`\n`).join('\n'))
  const anomalies = [...evaluateAssertions(scenario, parsed)]
  if (!ok) anomalies.push({ kind: 'scenario-error', message: errorMessage })
  writeJson(path.join(artifactDir, 'anomalies.json'), anomalies)
  const verdict = { ok: ok && anomalies.length === 0, error: errorMessage, anomalies, finishedAt: nowIso() }
  writeJson(path.join(artifactDir, 'verdict.json'), verdict)
  writeText(path.join(artifactDir, 'summary.md'), `# VSCode Harness Scenario ${scenario.id}\n\nStatus: ${verdict.ok ? 'green' : 'red'}\n\nArtifact dir: ${artifactDir}\n\nAnomalies: ${anomalies.length}\n`)
  pty.close()
  await sleep(500)
  await vscode.commands.executeCommand('workbench.action.closeWindow')
}

function activate(context) {
  const run = async () => {
    const scenarioPath = process.env.BREADBOARD_VSCODE_HARNESS_SCENARIO
    const artifactDir = process.env.BREADBOARD_VSCODE_HARNESS_ARTIFACT_DIR
    if (!scenarioPath || !artifactDir) {
      vscode.window.showInformationMessage('BreadBoard VSCode harness loaded; set BREADBOARD_VSCODE_HARNESS_SCENARIO to auto-run.')
      return
    }
    try {
      await runScenario(context, scenarioPath, artifactDir)
    } catch (error) {
      ensureDir(artifactDir)
      writeJson(path.join(artifactDir, 'verdict.json'), { ok: false, error: error.message, finishedAt: nowIso() })
      writeText(path.join(artifactDir, 'summary.md'), `# VSCode Harness Failure\n\nStatus: red\n\n${error.stack || error.message}\n`)
      await vscode.commands.executeCommand('workbench.action.closeWindow')
    }
  }
  context.subscriptions.push(vscode.commands.registerCommand('breadboardHarness.runScenario', run))
  setTimeout(run, 1500)
}

function deactivate() {}

module.exports = { activate, deactivate }
