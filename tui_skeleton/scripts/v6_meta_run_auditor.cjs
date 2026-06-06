#!/usr/bin/env node
const fs = require('node:fs')
const path = require('node:path')
const { analyzeArtifactRun, countOccurrences, listFilesRecursive, loadArtifactRun } = require('../tools/vscode-terminal-harness/src/v5ArtifactDetectors.cjs')

const PROVIDER_WARNING_PATTERNS = [
  /Provider\s+\S+\s+\([^)]*\)\s+rejected streaming/gi,
  /Expected to have received `response\.created` before `response\.keep_alive`/gi,
]
const FORBIDDEN_SYSTEM_PROMPT_PATTERNS = [
  /You are Codex, based on GPT-5/i,
  /## Editing constraints/i,
  /## Presenting your work and final message/i,
]
const DEFERRED_COMMAND_PATTERN = /Permission command surface is deferred|Multiagent controls are deferred|\/goal is deferred|Diff viewer commands are deferred|\/fork is deferred|durable goal persistence|session graph/i
const RAW_VIEWER_PATTERN = /breadboard raw event viewer|raw event viewer/i
const TRANSCRIPT_VIEWER_PATTERN = /breadboard transcript viewer/i
const MODEL_HINT_PATTERN = /Use selectModel action or interactive picker to choose a model/i
const NARROW_PICKER_WRAP_PATTERN = /Fuzzy search[^\n]*\n…|PgUp\/PgDn page •\s*\n…/i
const SETUP_SENTINEL_PATTERN = /\b[A-Z0-9]+(?:_[A-Z0-9]+){2,}\b/g

function readText(file) {
  try {
    return fs.readFileSync(file, 'utf8')
  } catch {
    return ''
  }
}

function readJson(file) {
  try {
    const text = readText(file)
    return text ? JSON.parse(text) : null
  } catch {
    return null
  }
}

function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`)
}

function addFinding(findings, kind, severity, message, evidence = {}) {
  findings.push({ kind, severity, message, ...evidence })
}

function countRegex(text, pattern) {
  const matches = String(text || '').match(pattern)
  return matches ? matches.length : 0
}

function discoverRunDirs(inputs) {
  const out = new Set()
  const consider = (candidate) => {
    let abs = path.resolve(candidate)
    if (!fs.existsSync(abs)) return
    const stat = fs.statSync(abs)
    if (!stat.isDirectory()) return
    if (path.basename(abs) === 'artifacts') abs = path.dirname(abs)
    const hasArtifacts = fs.existsSync(path.join(abs, 'artifacts', 'scenario.json')) || fs.existsSync(path.join(abs, 'artifacts', 'pty_plain.txt'))
    const hasRunFiles = fs.existsSync(path.join(abs, 'verdict.json')) || fs.existsSync(path.join(abs, 'scenario.json')) || fs.existsSync(path.join(abs, 'pty_plain.txt'))
    if (hasArtifacts || hasRunFiles) out.add(abs)
  }

  const walk = (root) => {
    const abs = path.resolve(root)
    if (!fs.existsSync(abs)) return
    if (fs.statSync(abs).isFile()) return
    consider(abs)
    for (const file of listFilesRecursive(abs)) {
      const dir = path.dirname(file)
      if (/\/artifacts\//.test(file)) consider(path.dirname(dir) === 'artifacts' ? path.dirname(path.dirname(dir)) : dir)
      if (path.basename(file) === 'scenario.json' || path.basename(file) === 'pty_plain.txt' || path.basename(file) === 'verdict.json') {
        if (path.basename(dir) === 'artifacts') consider(path.dirname(dir))
        else consider(dir)
      }
    }
  }

  for (const input of inputs) walk(input)
  return [...out].sort()
}

function scenarioV6(scenario) {
  return scenario?.v6 ?? scenario?.v6Expectations ?? {}
}

function expectedFinalMode(run) {
  const meta = scenarioV6(run.scenario)
  if (typeof meta.expectedFinalMode === 'string') return meta.expectedFinalMode
  if (typeof run.scenario?.expectedFinalMode === 'string') return run.scenario.expectedFinalMode

  const id = String(run.scenario?.id || '')
  if (/transcript.*lifecycle|control_surface_key_sweep|command_overlays_isolated|attachment_read_semantics|deep_command_overlay_sweep/i.test(id)) return 'repl'
  if (/live_long_coding.*transcript-after-live-interrupt/i.test(id)) return 'transcriptViewer'
  return 'any'
}

function allowedDeferredScenario(run) {
  const meta = scenarioV6(run.scenario)
  if (Array.isArray(meta.allowedDeferredCommands) && meta.allowedDeferredCommands.length > 0) return true
  return /v5h_command_parity_decisions|r2_edge_autocomplete_deferred_resize_sweep/i.test(String(run.scenario?.id || ''))
}

function maxProviderWarningCount(run) {
  const meta = scenarioV6(run.scenario)
  if (Number.isInteger(meta.maxProviderWarningCount)) return meta.maxProviderWarningCount
  if (/provider_warning|live/i.test(String(run.scenario?.id || ''))) return 0
  return 0
}

function screenshotTexts(run) {
  return run.screenshots.map((shot) => `--- ${shot.name}\n${shot.text}`).join('\n')
}

function allReadableText(run) {
  return [run.scrollback, run.viewport, screenshotTexts(run)].join('\n')
}

function setupSentinelsFromScenario(run) {
  const steps = Array.isArray(run.scenario?.steps) ? run.scenario.steps : []
  const setupWriteText = steps
    .filter((step, index) => index < 4 && (step.kind === 'write' || step.kind === 'paste'))
    .map((step) => String(step.text || ''))
    .join('\n')
  const matches = setupWriteText.match(SETUP_SENTINEL_PATTERN) || []
  return [...new Set(matches.filter((item) => /READY|SHELL|SETUP|SENTINEL|R2_|BB_/.test(item)))]
}

function waitTextsFromScenario(run) {
  return (Array.isArray(run.scenario?.steps) ? run.scenario.steps : [])
    .filter((step) => step.kind === 'waitForText')
    .map((step) => String(step.text || ''))
    .filter(Boolean)
}

function analyzeV6Run(runDir, options = {}) {
  const run = loadArtifactRun(runDir)
  const findings = []
  const rootVerdict = readJson(path.join(runDir, 'verdict.json'))
  const artifactVerdict = readJson(path.join(run.artifactDir, 'verdict.json'))
  const verdict = rootVerdict || artifactVerdict || run.verdict || {}
  const detector = analyzeArtifactRun(runDir)
  const readable = allReadableText(run)
  const allText = [run.rawPlain, readable, run.inputLog].join('\n')
  const expectedMode = expectedFinalMode(run)

  if (verdict && verdict.ok === false) {
    addFinding(findings, 'harness-verdict-red', 'P0', 'Harness verdict is red and must fail aggregate validation.', {
      error: verdict.error || null,
      verdictFile: fs.existsSync(path.join(runDir, 'verdict.json')) ? path.join(runDir, 'verdict.json') : path.join(run.artifactDir, 'verdict.json'),
    })
    if (/stable viewport frame/i.test(String(verdict.error || ''))) {
      addFinding(findings, 'stable-viewport-timeout', 'P0', 'Harness timed out waiting for a stable viewport frame.', {
        error: verdict.error,
      })
    }
  }

  for (const finding of detector.findings || []) {
    if (finding.kind === 'visible-deferred-parity-surface' && allowedDeferredScenario(run)) {
      addFinding(findings, 'detector-false-positive-candidate', 'P3', 'Deferred command text is expected in this scenario; detector finding is calibration evidence, not product failure.', {
        originalFinding: finding,
      })
      continue
    }
    addFinding(findings, `v5-detector:${finding.kind}`, finding.severity || 'P2', finding.message || 'Existing V5 detector finding.', {
      originalFinding: finding,
    })
  }

  const providerWarningCount = PROVIDER_WARNING_PATTERNS.reduce((max, pattern) => Math.max(max, countRegex(allText, pattern)), 0)
  const providerWarningVisibleCount = PROVIDER_WARNING_PATTERNS.reduce((max, pattern) => Math.max(max, countRegex(readable, pattern)), 0)
  const providerMax = maxProviderWarningCount(run)
  if (providerWarningCount > providerMax) {
    addFinding(findings, 'provider-warning-churn', 'P1', 'Provider streaming fallback warning appears too many times in run artifacts.', {
      count: providerWarningCount,
      visibleCount: providerWarningVisibleCount,
      max: providerMax,
    })
  }

  for (const pattern of FORBIDDEN_SYSTEM_PROMPT_PATTERNS) {
    if (pattern.test(readable)) {
      addFinding(findings, 'system-prompt-visible-leak', 'P0', 'Readable terminal artifacts contain system-prompt text.', {
        pattern: String(pattern),
      })
    }
  }

  const forbiddenVisibleText = Array.isArray(scenarioV6(run.scenario).forbiddenVisibleText)
    ? scenarioV6(run.scenario).forbiddenVisibleText
    : []
  for (const forbidden of forbiddenVisibleText) {
    const needle = String(forbidden || '')
    if (needle && readable.includes(needle)) {
      addFinding(findings, 'scenario-forbidden-visible-text', 'P1', 'Readable terminal artifacts contain text forbidden by scenario metadata.', {
        text: needle,
      })
    }
  }

  if (expectedMode !== 'any') {
    const finalViewport = run.viewport || ''
    const modeMatches = {
      repl: !RAW_VIEWER_PATTERN.test(finalViewport) && !TRANSCRIPT_VIEWER_PATTERN.test(finalViewport) && /Type your request|❯/.test(finalViewport),
      rawViewer: RAW_VIEWER_PATTERN.test(finalViewport),
      transcriptViewer: TRANSCRIPT_VIEWER_PATTERN.test(finalViewport),
      liveBusy: /responding|elapsed|interrupt|follow live/i.test(finalViewport),
      picker: /Type to filter|Fuzzy search|Attach file|Models/i.test(finalViewport),
      palette: /Command palette/i.test(finalViewport),
      shell: /bash-[0-9.]+\$/.test(finalViewport),
    }
    if (!modeMatches[expectedMode]) {
      addFinding(findings, 'unexpected-final-ui-mode', 'P1', 'Final viewport is not in the scenario expected UI mode.', {
        expectedFinalMode: expectedMode,
        viewportPreview: finalViewport.split(/\n/).slice(0, 12).join('\n'),
      })
    }
  }

  if (TRANSCRIPT_VIEWER_PATTERN.test(run.viewport) && /"text":"\/(copy|clear)\\r"/.test(run.inputLog)) {
    addFinding(findings, 'viewer-command-routing-ambiguous', 'P1', 'Run ended in transcript viewer after /copy or /clear input, indicating ambiguous viewer-vs-REPL command routing.', {
      expectedFinalMode: expectedMode,
    })
  }

  if (RAW_VIEWER_PATTERN.test(run.viewport) && /"text":"\/(thinking|tool-display|ctree|todo-scope|status|copy|clear)\\r"/.test(run.inputLog)) {
    addFinding(findings, 'raw-viewer-command-routing-ambiguous', 'P1', 'Run ended in raw viewer after subsequent slash command input, indicating raw viewer focus trap or command routing ambiguity.', {
      expectedFinalMode: expectedMode,
    })
  }

  if (MODEL_HINT_PATTERN.test(readable)) {
    const hintShots = run.screenshots.filter((shot) => MODEL_HINT_PATTERN.test(shot.text)).map((shot) => path.basename(shot.file))
    addFinding(findings, 'stale-model-picker-hint-visible', 'P2', 'Model-picker selection hint appears in later readable REPL surfaces.', {
      screenshots: hintShots,
    })
  }

  if (/Search:\s*codex[\s\S]{0,200}No models match/i.test(readable)) {
    addFinding(findings, 'model-picker-codex-alias-miss', 'P2', 'Model picker search for Codex returns no matches in a Codex-configured session.')
  }

  if (NARROW_PICKER_WRAP_PATTERN.test(readable)) {
    addFinding(findings, 'narrow-picker-footer-wrap', 'P2', 'Narrow picker footer wraps into an ellipsis-only continuation line.')
  }

  for (const shot of run.screenshots) {
    const promptLikeRows = countOccurrences(shot.text, '❯ @')
    if (/picker|attach|at/i.test(shot.name) && promptLikeRows > 1) {
      addFinding(findings, 'narrow-picker-duplicate-prompt-surface', 'P2', 'Picker screenshot contains multiple prompt-looking attachment query rows.', {
        file: shot.file,
        count: promptLikeRows,
      })
    }
  }

  const setupSentinels = setupSentinelsFromScenario(run)
  const waits = waitTextsFromScenario(run)
  const suspectWaits = waits.filter((wait) => setupSentinels.some((sentinel) => wait.includes(sentinel) || sentinel.includes(wait)))
  if (suspectWaits.length > 0) {
    addFinding(findings, 'scenario-setup-sentinel-wait-risk', 'P2', 'Scenario waits for text created by setup shell commands, which can false-pass on shell history.', {
      setupSentinels,
      suspectWaits,
    })
  }

  const screenshotCount = run.screenshots.length
  const minScreenshots = Number.isInteger(scenarioV6(run.scenario).minScreenshotCount) ? scenarioV6(run.scenario).minScreenshotCount : 1
  if (screenshotCount < minScreenshots) {
    addFinding(findings, 'insufficient-screenshot-evidence', 'P2', 'Run did not emit enough screenshot artifacts for visual/focus validation.', {
      screenshotCount,
      minScreenshots,
    })
  }

  return {
    ok: findings.filter((finding) => finding.severity !== 'P3').length === 0,
    runDir,
    artifactDir: run.artifactDir,
    scenarioId: run.scenario?.id || null,
    expectedFinalMode: expectedMode,
    screenshotCount,
    verdictOk: verdict?.ok ?? null,
    providerWarningCount,
    providerWarningVisibleCount,
    detectorOk: detector.ok,
    detectorFindingCount: detector.findings?.length || 0,
    findings,
  }
}

function summarize(results) {
  const severityRank = { P0: 0, P1: 1, P2: 2, P3: 3 }
  const findings = results.flatMap((result) => result.findings.map((finding) => ({ ...finding, runDir: result.runDir, scenarioId: result.scenarioId })))
  const blockingFindings = findings.filter((finding) => finding.severity !== 'P3')
  const byKind = {}
  const bySeverity = {}
  for (const finding of findings) {
    byKind[finding.kind] = (byKind[finding.kind] || 0) + 1
    bySeverity[finding.severity] = (bySeverity[finding.severity] || 0) + 1
  }
  return {
    ok: blockingFindings.length === 0,
    runCount: results.length,
    findingCount: findings.length,
    blockingFindingCount: blockingFindings.length,
    bySeverity,
    byKind: Object.fromEntries(Object.entries(byKind).sort((a, b) => a[0].localeCompare(b[0]))),
    worstFindings: findings
      .slice()
      .sort((a, b) => (severityRank[a.severity] ?? 9) - (severityRank[b.severity] ?? 9) || a.kind.localeCompare(b.kind))
      .slice(0, 25),
  }
}

function analyzeV6Runs(inputs, options = {}) {
  const runDirs = discoverRunDirs(inputs)
  const results = runDirs.map((runDir) => analyzeV6Run(runDir, options))
  const report = { ...summarize(results), results }
  if (runDirs.length === 0) {
    report.ok = false
    report.error = 'No artifact run directories discovered.'
  }
  return report
}

function printUsage() {
  console.error('Usage: node tui_skeleton/scripts/v6_meta_run_auditor.cjs [--out report.json] <run-dir-or-root>...')
}

if (require.main === module) {
  const args = process.argv.slice(2)
  if (args.includes('--help') || args.includes('-h')) {
    printUsage()
    process.exit(0)
  }
  const outIndex = args.indexOf('--out')
  let outFile = null
  if (outIndex >= 0) {
    outFile = args[outIndex + 1]
    args.splice(outIndex, 2)
  }
  if (args.length === 0) {
    printUsage()
    process.exit(2)
  }
  const report = analyzeV6Runs(args)
  const text = `${JSON.stringify(report, null, 2)}\n`
  if (outFile) writeJson(path.resolve(outFile), report)
  else process.stdout.write(text)
  process.exit(report.ok ? 0 : 1)
}

module.exports = {
  analyzeV6Run,
  analyzeV6Runs,
  discoverRunDirs,
  setupSentinelsFromScenario,
  waitTextsFromScenario,
}
