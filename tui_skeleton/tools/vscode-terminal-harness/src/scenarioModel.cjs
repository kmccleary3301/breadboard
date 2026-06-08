const fs = require('node:fs')

const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value)
const FINAL_MODES = new Set(['repl', 'rawViewer', 'transcriptViewer', 'picker', 'palette', 'liveBusy', 'shell', 'any'])

function validateScenario(value) {
  const errors = []
  if (!isObject(value)) return { ok: false, errors: ['scenario must be an object'] }
  if (typeof value.id !== 'string' || value.id.length === 0) errors.push('id must be a non-empty string')
  if (!Array.isArray(value.steps) || value.steps.length === 0) errors.push('steps must be a non-empty array')
  const v6 = value.v6 ?? value.v6Expectations
  if (v6 !== undefined) {
    if (!isObject(v6)) {
      errors.push('v6 must be an object')
    } else {
      if (v6.expectedFinalMode !== undefined && !FINAL_MODES.has(v6.expectedFinalMode)) errors.push(`v6.expectedFinalMode unsupported: ${v6.expectedFinalMode}`)
      if (v6.allowedWarnings !== undefined && !Array.isArray(v6.allowedWarnings)) errors.push('v6.allowedWarnings must be an array')
      if (v6.allowedDeferredCommands !== undefined && !Array.isArray(v6.allowedDeferredCommands)) errors.push('v6.allowedDeferredCommands must be an array')
      if (v6.allowRawJsonInModes !== undefined && (!Array.isArray(v6.allowRawJsonInModes) || v6.allowRawJsonInModes.some((mode) => !FINAL_MODES.has(mode)))) errors.push('v6.allowRawJsonInModes must be an array of supported modes')
      if (v6.forbiddenVisibleText !== undefined && !Array.isArray(v6.forbiddenVisibleText)) errors.push('v6.forbiddenVisibleText must be an array')
      if (v6.maxProviderWarningCount !== undefined && (!Number.isInteger(v6.maxProviderWarningCount) || v6.maxProviderWarningCount < 0)) errors.push('v6.maxProviderWarningCount must be a non-negative integer')
      if (v6.maxTypeYourRequestCount !== undefined && (!Number.isInteger(v6.maxTypeYourRequestCount) || v6.maxTypeYourRequestCount < 0)) errors.push('v6.maxTypeYourRequestCount must be a non-negative integer')
      if (v6.minScreenshotCount !== undefined && (!Number.isInteger(v6.minScreenshotCount) || v6.minScreenshotCount < 0)) errors.push('v6.minScreenshotCount must be a non-negative integer')
      if (v6.requiresStableViewport !== undefined && typeof v6.requiresStableViewport !== 'boolean') errors.push('v6.requiresStableViewport must be a boolean')
    }
  }
  const terminal = value.terminal ?? {}
  if (terminal !== undefined && !isObject(terminal)) errors.push('terminal must be an object')
  if (isObject(terminal)) {
    if (terminal.initialCols !== undefined && (!Number.isInteger(terminal.initialCols) || terminal.initialCols < 20)) errors.push('terminal.initialCols must be an integer >= 20')
    if (terminal.initialRows !== undefined && (!Number.isInteger(terminal.initialRows) || terminal.initialRows < 5)) errors.push('terminal.initialRows must be an integer >= 5')
  }
  ;(value.steps ?? []).forEach((step, index) => {
    if (!isObject(step)) {
      errors.push(`steps[${index}] must be an object`)
      return
    }
    const kind = step.kind
    if (!['write', 'paste', 'key', 'waitForText', 'waitForRegex', 'waitForNoText', 'waitForStableFrame', 'waitForState', 'resize', 'snapshot', 'screenshot', 'exportBreadBoardState', 'assert', 'sleep', 'terminate'].includes(kind)) {
      errors.push(`steps[${index}].kind unsupported: ${kind}`)
    }
    if ((kind === 'write' || kind === 'paste') && typeof step.text !== 'string') errors.push(`steps[${index}].text must be a string`)
    if (kind === 'key' && typeof step.key !== 'string') errors.push(`steps[${index}].key must be a string`)
    if ((kind === 'waitForText' || kind === 'waitForNoText') && typeof step.text !== 'string') errors.push(`steps[${index}].text must be a string`)
    if (kind === 'waitForRegex' && typeof step.pattern !== 'string') errors.push(`steps[${index}].pattern must be a string`)
    if (kind === 'waitForStableFrame') {
      if (step.scope !== undefined && !['buffer', 'viewport', 'raw'].includes(step.scope)) errors.push(`steps[${index}].scope unsupported: ${step.scope}`)
      if (step.stableMs !== undefined && (!Number.isInteger(step.stableMs) || step.stableMs < 0)) errors.push(`steps[${index}].stableMs must be a non-negative integer`)
      if (step.timeoutMs !== undefined && (!Number.isInteger(step.timeoutMs) || step.timeoutMs < 0)) errors.push(`steps[${index}].timeoutMs must be a non-negative integer`)
    }
    if (kind === 'waitForState') {
      if (step.timeoutMs !== undefined && (!Number.isInteger(step.timeoutMs) || step.timeoutMs < 0)) errors.push(`steps[${index}].timeoutMs must be a non-negative integer`)
      if (step.fresh !== undefined && typeof step.fresh !== 'boolean') errors.push(`steps[${index}].fresh must be a boolean`)
      for (const key of ['pendingResponse', 'disconnected', 'mainFollowTail']) {
        if (step[key] !== undefined && typeof step[key] !== 'boolean') errors.push(`steps[${index}].${key} must be a boolean`)
      }
      for (const key of ['statusIncludes', 'lastConversationSpeaker', 'lastConversationPhase', 'lastConversationPreviewIncludes', 'lastToolEventKind', 'lastToolEventStatus', 'lastToolEventTextIncludes']) {
        if (step[key] !== undefined && typeof step[key] !== 'string') errors.push(`steps[${index}].${key} must be a string`)
      }
      for (const key of ['conversationCountAtLeast', 'eventCountAtLeast', 'toolEventsCountAtLeast']) {
        if (step[key] !== undefined && (!Number.isInteger(step[key]) || step[key] < 0)) errors.push(`steps[${index}].${key} must be a non-negative integer`)
      }
    }
    if (kind === 'resize') {
      if (!Number.isInteger(step.cols) || step.cols < 20) errors.push(`steps[${index}].cols must be integer >= 20`)
      if (!Number.isInteger(step.rows) || step.rows < 5) errors.push(`steps[${index}].rows must be integer >= 5`)
    }
    if (kind === 'sleep' && (!Number.isInteger(step.ms) || step.ms < 0)) errors.push(`steps[${index}].ms must be non-negative integer`)
    if ((kind === 'snapshot' || kind === 'screenshot' || kind === 'exportBreadBoardState') && (step.label !== undefined && typeof step.label !== 'string')) errors.push(`steps[${index}].label must be a string`)
    if (kind === 'assert') {
      const assertions = Array.isArray(step.assertions) ? step.assertions : step.assertion ? [step.assertion] : []
      if (assertions.length === 0) errors.push(`steps[${index}].assertions must be a non-empty array`)
      assertions.forEach((assertion, assertionIndex) => {
        if (!isObject(assertion)) {
          errors.push(`steps[${index}].assertions[${assertionIndex}] must be an object`)
          return
        }
        if (!['contains', 'notContains', 'regex', 'countAtMost', 'countAtLeast'].includes(assertion.kind)) errors.push(`steps[${index}].assertions[${assertionIndex}].kind unsupported: ${assertion.kind}`)
        if ((assertion.kind === 'contains' || assertion.kind === 'notContains' || assertion.kind === 'countAtMost' || assertion.kind === 'countAtLeast') && typeof assertion.text !== 'string') errors.push(`steps[${index}].assertions[${assertionIndex}].text must be a string`)
        if (assertion.kind === 'regex' && typeof assertion.pattern !== 'string') errors.push(`steps[${index}].assertions[${assertionIndex}].pattern must be a string`)
        if ((assertion.kind === 'countAtMost' || assertion.kind === 'countAtLeast') && (!Number.isInteger(assertion.count) || assertion.count < 0)) errors.push(`steps[${index}].assertions[${assertionIndex}].count must be a non-negative integer`)
        if (assertion.scope !== undefined && !['buffer', 'viewport', 'raw'].includes(assertion.scope)) errors.push(`steps[${index}].assertions[${assertionIndex}].scope unsupported: ${assertion.scope}`)
      })
    }
  })
  ;(value.assertions ?? []).forEach((assertion, index) => {
    if (!isObject(assertion)) {
      errors.push(`assertions[${index}] must be an object`)
      return
    }
    if (!['contains', 'notContains', 'regex', 'countAtMost', 'countAtLeast'].includes(assertion.kind)) errors.push(`assertions[${index}].kind unsupported: ${assertion.kind}`)
    if ((assertion.kind === 'contains' || assertion.kind === 'notContains' || assertion.kind === 'countAtMost' || assertion.kind === 'countAtLeast') && typeof assertion.text !== 'string') errors.push(`assertions[${index}].text must be a string`)
    if (assertion.kind === 'regex' && typeof assertion.pattern !== 'string') errors.push(`assertions[${index}].pattern must be a string`)
    if ((assertion.kind === 'countAtMost' || assertion.kind === 'countAtLeast') && (!Number.isInteger(assertion.count) || assertion.count < 0)) errors.push(`assertions[${index}].count must be a non-negative integer`)
    if (assertion.scope !== undefined && !['buffer', 'viewport', 'raw'].includes(assertion.scope)) errors.push(`assertions[${index}].scope unsupported: ${assertion.scope}`)
  })
  return { ok: errors.length === 0, errors }
}

function loadScenario(scenarioPath) {
  const parsed = JSON.parse(fs.readFileSync(scenarioPath, 'utf8'))
  const validation = validateScenario(parsed)
  if (!validation.ok) {
    const error = new Error(`Invalid VSCode harness scenario: ${validation.errors.join('; ')}`)
    error.validationErrors = validation.errors
    throw error
  }
  return parsed
}

module.exports = { validateScenario, loadScenario }
