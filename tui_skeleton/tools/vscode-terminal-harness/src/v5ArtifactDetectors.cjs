const fs = require('node:fs')
const path = require('node:path')

function readText(file) {
  try {
    return fs.readFileSync(file, 'utf8')
  } catch {
    return ''
  }
}

function stripAnsi(value) {
  return String(value || '')
    .replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, '')
    .replace(/\x1b\][^\x07]*(\x07|\x1b\\)/g, '')
    .replace(/\r/g, '')
}

function countOccurrences(text, needle) {
  if (!needle) return 0
  return String(text || '').split(needle).length - 1
}

function listFilesRecursive(dir) {
  if (!fs.existsSync(dir)) return []
  const out = []
  const walk = (current) => {
    for (const name of fs.readdirSync(current).sort()) {
      const file = path.join(current, name)
      const stat = fs.statSync(file)
      if (stat.isDirectory()) walk(file)
      else out.push(file)
    }
  }
  walk(dir)
  return out
}

function parseNdjson(text) {
  return String(text || '')
    .split(/\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line)
      } catch {
        return null
      }
    })
    .filter(Boolean)
}

function loadLatestTranscriptCells(artifactDir) {
  const records = parseNdjson(readText(path.join(artifactDir, 'breadboard_artifacts', 'repl_state.ndjson')))
  for (let index = records.length - 1; index >= 0; index -= 1) {
    const cells = records[index]?.state?.transcriptCells
    if (Array.isArray(cells)) return cells
  }
  return []
}

function loadArtifactRun(runDir) {
  const artifactDir = fs.existsSync(path.join(runDir, 'artifacts')) ? path.join(runDir, 'artifacts') : runDir
  const screenshotDir = path.join(artifactDir, 'screenshots')
  const screenshots = listFilesRecursive(screenshotDir)
    .filter((file) => file.endsWith('.txt'))
    .map((file) => ({
      file,
      name: path.basename(file),
      text: readText(file),
    }))
  const rawAnsi = readText(path.join(artifactDir, 'pty_raw.ansi'))
  const rawPlain = stripAnsi(rawAnsi || readText(path.join(artifactDir, 'pty_plain.txt')))
  const scrollback = readText(path.join(artifactDir, 'scrollback_final.txt'))
  const viewport = readText(path.join(artifactDir, 'viewport_final.txt'))
  const inputLog = readText(path.join(artifactDir, 'input_log.ndjson'))
  const transcriptCells = loadLatestTranscriptCells(artifactDir)
  const verdict = (() => {
    try {
      return JSON.parse(readText(path.join(artifactDir, 'verdict.json')) || '{}')
    } catch {
      return {}
    }
  })()
  const scenario = (() => {
    try {
      return JSON.parse(readText(path.join(artifactDir, 'scenario.json')) || '{}')
    } catch {
      return {}
    }
  })()
  return { runDir, artifactDir, screenshots, rawPlain, scrollback, viewport, inputLog, transcriptCells, verdict, scenario }
}

function addFinding(findings, kind, severity, message, evidence = {}) {
  findings.push({ kind, severity, message, ...evidence })
}

function expectedOverlayPattern(name) {
  if (/models?/i.test(name)) return /\bModels\b|Select a model|Type to filter/i
  if (/todos?/i.test(name)) return /\bTodos\b|No todos yet|TodoWrite/i
  if (/usage/i.test(name)) return /\bUsage\b|No usage metrics|Token/i
  if (/skills?/i.test(name)) return /\bSkills\b|No skills|Skill/i
  if (/tasks?/i.test(name)) return /\bTasks\b|No tasks|Task/i
  if (/resume|sessions?/i.test(name)) return /\bResume\b|\bSessions\b|Session start|running|completed/i
  if (/rewind/i.test(name)) return /Rewind checkpoints|Session start|checkpoint/i
  if (/inspect/i.test(name)) return /\bInspect\b|Runtime|Session|Config/i
  if (/raw/i.test(name)) return /raw event viewer|Status \[raw\]|\bRaw\b/i
  return null
}

function analyzeArtifactRun(runDir, options = {}) {
  const run = loadArtifactRun(runDir)
  const findings = []
  const allScreenshotText = run.screenshots.map((item) => `\n--- ${item.name}\n${item.text}`).join('\n')
  const readableText = [run.scrollback, run.viewport, allScreenshotText].join('\n')
  const allText = [run.rawPlain, readableText].join('\n')
  const rawEventRowPattern = /● Raw · \{|Status \[raw\] \{/
  const isRawViewerText = (text) => /breadboard raw event viewer|raw event viewer/.test(String(text || ''))
  const nonRawViewerScreenshots = run.screenshots.filter((shot) => !isRawViewerText(shot.text))
  const nonRawViewerScreenshotText = nonRawViewerScreenshots.map((item) => `\n--- ${item.name}\n${item.text}`).join('\n')
  const readableNonRawViewerText = [run.scrollback, run.viewport, nonRawViewerScreenshotText].join('\n')

  for (const shot of run.screenshots) {
    const headerCount = countOccurrences(shot.text, 'BreadBoard v0.2.0')
    const configCount = countOccurrences(shot.text, 'Config: Codex')
    if (headerCount > 1 || configCount > 1) {
      addFinding(findings, 'duplicate-landing-header', 'P0', 'A single captured viewport contains multiple BreadBoard landing/header blocks.', {
        file: shot.file,
        count: Math.max(headerCount, configCount),
      })
    }
  }

  for (const shot of run.screenshots) {
    const afterOverlayClose = /after[-_].*(escape|close|exit)|after[-_](raw|usage|models?|todos?|skills?|tasks?|resume|rewind|inspect)[-_](escape|close|exit)/i.test(shot.name)
    const overlayName = !afterOverlayClose && /(models|todos|usage|skills|tasks|resume|rewind|inspect|raw)/i.test(shot.name)
    const nonblankLines = shot.text.split(/\n/).filter((line) => line.trim()).length
    if (overlayName && nonblankLines === 0) {
      addFinding(findings, 'blank-overlay-screenshot', 'P0', 'Overlay screenshot artifact is blank.', {
        file: shot.file,
      })
    }
    const expectedOverlay = overlayName ? expectedOverlayPattern(shot.name) : null
    if (expectedOverlay && nonblankLines > 0 && !expectedOverlay.test(shot.text)) {
      addFinding(findings, 'overlay-screenshot-label-missing', 'P0', 'Overlay screenshot artifact is nonblank but does not contain the overlay it claims to capture.', {
        file: shot.file,
      })
    }
  }

  if (/\[200~|\[201~/.test(readableText)) {
    addFinding(findings, 'visible-bracketed-paste-marker', 'P1', 'Visible rendered text contains bracketed paste markers.', {
      markers: {
        open: countOccurrences(readableText, '[200~'),
        close: countOccurrences(readableText, '[201~'),
      },
    })
  }

  if (rawEventRowPattern.test(readableNonRawViewerText)) {
    addFinding(findings, 'raw-diagnostic-readable-leak', 'P1', 'Raw diagnostic JSON/event rows appear in readable transcript captures.')
  }

  if (/Transcript saved\s*\n\/tmp\//.test(readableText) || /Transcript saved\s*\n\/shared_folders\//.test(readableText)) {
    addFinding(findings, 'transcript-saved-path-leak', 'P1', 'Transcript export saved-path notification appears in readable transcript captures.')
  }

  const queuedCount = countOccurrences(run.rawPlain, 'Queued prompt')
  if (queuedCount > (options.maxQueuedPromptRedraws ?? 100)) {
    addFinding(findings, 'queued-prompt-redraw-spam', 'P1', 'Queued prompt redraw count is excessive in raw stream.', {
      count: queuedCount,
      max: options.maxQueuedPromptRedraws ?? 100,
    })
  }

  for (const shot of run.screenshots) {
    const visibleQueuedCount = countOccurrences(shot.text, 'Queued prompt')
    if (visibleQueuedCount > 1) {
      addFinding(findings, 'queued-prompt-visible-duplicate', 'P1', 'A single visible frame contains duplicate queued-prompt surfaces.', {
        file: shot.file,
        count: visibleQueuedCount,
      })
    }
  }

  const startedNewCount = countOccurrences(run.rawPlain, 'Started new session')
  if (startedNewCount > (options.maxStartedNewSessionRedraws ?? 25)) {
    addFinding(findings, 'started-new-session-redraw-spam', 'P2', 'Started-new-session status redraw count is excessive in raw stream.', {
      count: startedNewCount,
      max: options.maxStartedNewSessionRedraws ?? 25,
    })
  }

  if (allText.includes('I’m ready to continue') || allText.includes("I'm ready to continue")) {
    addFinding(findings, 'stale-context-continuation-response', 'P0', 'A deterministic fresh-looking prompt received stale continuation-style assistant text.')
  }

  for (const shot of run.screenshots) {
    if (/resume/i.test(shot.name)) {
      const runningCount = countOccurrences(shot.text, '— running')
      if (runningCount >= (options.maxResumeRunningRows ?? 10)) {
        addFinding(findings, 'resume-stale-running-spam', 'P0', '/resume shows excessive running sessions.', {
          file: shot.file,
          count: runningCount,
          max: options.maxResumeRunningRows ?? 10,
        })
      }
    }
  }

  const stepTexts = Array.isArray(run.scenario.steps) ? run.scenario.steps.map((step) => String(step?.text || '')) : []
  if (
    stepTexts.some((text) => text.includes('/usage')) &&
    stepTexts.some((text) => text.includes('BB_DEEP_MARKDOWN_OK')) &&
    run.verdict?.ok === false &&
    /BB_DEEP_MARKDOWN_OK/.test(JSON.stringify(run.verdict || {})) &&
    !/BB_DEEP_MARKDOWN_OK/.test(run.rawPlain)
  ) {
    addFinding(findings, 'modal-input-target-ambiguous', 'P1', 'Scenario entered a prompt after an overlay command, but the expected assistant sentinel never reached raw output; this matches modal/input target ambiguity.', {
      scenario: run.scenario.id ?? null,
    })
  }

  if (readableText.includes('☐ pending')) {
    addFinding(findings, 'stale-pending-row', 'P1', 'Readable transcript contains stale pending row after stop/retry.')
  }

  if (readableText.includes('Resubmitting prompt #1') && countOccurrences(readableText, 'Write a slow response that counts') > 1) {
    addFinding(findings, 'retry-duplicate-transcript-state', 'P1', 'Retry surface shows duplicate prompt/response state without a clear transcript contract.')
  }

  if (/BBDEEPMARKDOWNOK[\s\S]{0,600}● Tool|● Tool[\s\S]{0,600}BBDEEPMARKDOWNOK/.test(allText)) {
    addFinding(findings, 'assistant-response-rendered-as-tool', 'P1', 'Simple deterministic assistant response appears in a tool row.')
  }

  const cells = run.transcriptCells
  const cellPreviews = cells.map((cell) => String(cell?.textPreview || ''))

  if (run.scenario?.id === 'vscode_default_startup_stale_context_guard') {
    const sentinel = String(run.scenario?.v5ExpectedAssistantText || 'BB_V5_FRESH_CONTEXT_OK')
    const promptPattern = run.scenario?.v5PromptPattern
      ? new RegExp(String(run.scenario.v5PromptPattern))
      : /BB_V5_FRESH_CONTEXT_OK|fresh context/i
    const promptIndex = cells.findIndex(
      (cell) => cell?.role === 'user-request' && promptPattern.test(String(cell?.textPreview || '')),
    )
    const assistantIndex =
      promptIndex >= 0
        ? cells.findIndex(
            (cell, index) =>
              index > promptIndex &&
              cell?.role === 'assistant-message' &&
              String(cell?.textPreview || '').trim() === sentinel,
          )
        : -1
    if (promptIndex < 0 || assistantIndex < 0) {
      addFinding(findings, 'fresh-start-assistant-sentinel-missing', 'P0', 'Default startup guard did not prove the expected sentinel came from an assistant transcript cell.', {
        scenario: run.scenario.id,
        expectedAssistantText: sentinel,
        promptFound: promptIndex >= 0,
      })
    }
  }

  if (run.scenario?.id === 'vscode_v5f_assistant_role_semantics') {
    const promptIndex = cells.findIndex(
      (cell) =>
        cell?.role === 'user-request' &&
        /single word formed by joining BB, DEEP, MARKDOWN, and OK/.test(String(cell?.textPreview || '')),
    )
    const assistantIndex =
      promptIndex >= 0
        ? cells.findIndex(
            (cell, index) =>
              index > promptIndex &&
              cell?.role === 'assistant-message' &&
              /BBDEEPMARKDOWNOK/.test(String(cell?.textPreview || '')),
          )
        : -1
    const toolIndex =
      promptIndex >= 0
        ? cells.findIndex(
            (cell, index) =>
              index > promptIndex &&
              /^tool-/.test(String(cell?.role || '')) &&
              /BBDEEPMARKDOWNOK/.test(String(cell?.textPreview || '')),
          )
        : -1
    if (promptIndex < 0 || assistantIndex < 0 || (toolIndex >= 0 && toolIndex < assistantIndex)) {
      addFinding(findings, 'v5f-assistant-role-contract-failed', 'P1', 'V5-F no-tool prompt did not produce the sentinel in an assistant-message cell before any tool cell.', {
        promptFound: promptIndex >= 0,
        assistantFound: assistantIndex >= 0,
        toolBeforeAssistant: toolIndex >= 0 && (assistantIndex < 0 || toolIndex < assistantIndex),
      })
    }
  }

  if (run.scenario?.id === 'vscode_v5f_tool_completion_semantics') {
    const promptIndex = cells.findIndex(
      (cell) =>
        cell?.role === 'user-request' &&
        /bb_v5f_result\.txt|BBDEEPCREATEOK/.test(String(cell?.textPreview || '')),
    )
    const assistantIndex =
      promptIndex >= 0
        ? cells.findIndex((cell, index) => index > promptIndex && cell?.role === 'assistant-message')
        : -1
    const toolResultIndex =
      promptIndex >= 0
        ? cells.findIndex((cell, index) => index > promptIndex && cell?.role === 'tool-result')
        : -1
    if (promptIndex < 0 || assistantIndex < 0 || toolResultIndex < 0) {
      addFinding(findings, 'v5f-tool-completion-contract-failed', 'P1', 'V5-F tool prompt did not produce the expected user/tool-result/assistant transcript cell sequence.', {
        promptFound: promptIndex >= 0,
        assistantFound: assistantIndex >= 0,
        toolResultFound: toolResultIndex >= 0,
      })
    }
    if (/max_steps_exhausted/.test(allText) || cells.some((cell) => /max_steps_exhausted/.test(String(cell?.textPreview || '')))) {
      addFinding(findings, 'v5f-tool-completion-max-steps', 'P1', 'V5-F tool completion scenario still exposes max_steps_exhausted.')
    }
  }

  if (run.scenario?.id === 'vscode_v5g_busy_queue_semantics') {
    const firstPromptIndex = cells.findIndex(
      (cell) =>
        cell?.role === 'user-request' &&
        /QUEUEFIRSTSTART|QUEUE, FIRST/.test(String(cell?.textPreview || '')),
    )
    const firstToolResultIndex =
      firstPromptIndex >= 0
        ? cells.findIndex(
            (cell, index) =>
              index > firstPromptIndex &&
              cell?.role === 'tool-result' &&
              /QUEUEFIRSTSTART|QUEUEFIRSTEND/.test(String(cell?.textPreview || '')),
          )
        : -1
    const queuedPromptIndex = cells.findIndex(
      (cell) =>
        cell?.role === 'user-request' &&
        /Q2 followed immediately by OK|QUEUE, SECOND, OK, and DONE|QUEUESECONDOKDONE/.test(String(cell?.textPreview || '')),
    )
    const queuedAssistantIndex =
      queuedPromptIndex >= 0
        ? cells.findIndex(
            (cell, index) =>
              index > queuedPromptIndex &&
              cell?.role === 'assistant-message' &&
              /Q2OK|QUEUE\s*SECOND\s*OK\s*DONE/.test(String(cell?.textPreview || '')),
          )
        : -1
    if (firstPromptIndex < 0 || firstToolResultIndex < 0 || queuedPromptIndex < 0 || queuedAssistantIndex < 0 || queuedPromptIndex < firstToolResultIndex) {
      addFinding(findings, 'v5g-queue-contract-failed', 'P1', 'V5-G queue scenario did not prove first tool turn followed by queued prompt submission and assistant response.', {
        firstPromptFound: firstPromptIndex >= 0,
        firstToolResultFound: firstToolResultIndex >= 0,
        queuedPromptFound: queuedPromptIndex >= 0,
        queuedAssistantFound: queuedAssistantIndex >= 0,
        queuedPromptAfterFirstToolResult: queuedPromptIndex > firstToolResultIndex,
      })
    }
  }

  if (run.scenario?.id === 'vscode_v5g_retry_command_semantics') {
    const retryPromptIndexes = cells
      .map((cell, index) => ({ cell, index }))
      .filter(
        ({ cell }) =>
          cell?.role === 'user-request' &&
          /RETRYFIRSTSTART|R1 followed immediately by OK|RETRY, FIRST/.test(String(cell?.textPreview || '')),
      )
      .map(({ index }) => index)
    const retryAssistantIndex =
      retryPromptIndexes.length >= 2
        ? cells.findIndex((cell, index) => index > retryPromptIndexes[1] && cell?.role === 'assistant-message')
        : -1
    const retryCompletionVisible = /R1OK|RETRY\s*FIRST\s*DONE/.test(allText)
    const pendingAfterHalt = cells.some((cell, index) => {
      if (cell?.status !== 'pending') return false
      const haltedBefore = cells.slice(0, index).some((candidate) => /\[halted\]|stopped_by_user/i.test(String(candidate?.textPreview || '')))
      return haltedBefore
    })
    if (retryPromptIndexes.length < 2 || retryAssistantIndex < 0 || !retryCompletionVisible || pendingAfterHalt) {
      addFinding(findings, 'v5g-retry-contract-failed', 'P1', 'V5-G retry scenario did not prove clean halted state followed by retried prompt and assistant response.', {
        retryPromptCount: retryPromptIndexes.length,
        retryAssistantFound: retryAssistantIndex >= 0,
        retryCompletionVisible,
        pendingAfterHalt,
      })
    }
  }

  const deterministicPromptIndex = cells.findIndex(
    (cell) => cell?.role === 'user-request' && /single word formed by joining BB, DEEP, MARKDOWN, and OK/.test(String(cell.textPreview || '')),
  )
  if (deterministicPromptIndex >= 0) {
    const nextAssistantIndex = cells.findIndex((cell, index) => index > deterministicPromptIndex && cell?.role === 'assistant-message')
    const routedToolIndex = cells.findIndex((cell, index) => index > deterministicPromptIndex && /^tool-/.test(String(cell?.role || '')) && /BBDEEPMARKDOWNOK|\| item \| status \|/.test(String(cell?.textPreview || '')))
    if (routedToolIndex >= 0 && (nextAssistantIndex < 0 || routedToolIndex < nextAssistantIndex)) {
      addFinding(findings, 'semantic-assistant-text-routed-to-tool', 'P1', 'Structured transcript cells route deterministic assistant content to tool rows before any assistant-message cell.', {
        promptCell: cells[deterministicPromptIndex]?.id,
        routedCell: cells[routedToolIndex]?.id,
      })
    }
  }

  if (allText.includes('max_steps_exhausted') && /Done\.|I created|BBDEEPCREATEOK/.test(allText)) {
    addFinding(findings, 'tool-loop-status-contradiction', 'P1', 'Tool-loop status shows max_steps_exhausted while visible transcript also claims completion.')
  }

  const maxStepsIndex = cells.findIndex((cell) => cell?.role === 'system' && /max_steps_exhausted/.test(String(cell?.textPreview || '')))
  if (maxStepsIndex >= 0 && cellPreviews.slice(0, maxStepsIndex).some((text) => /Done\.|I created|BBDEEPCREATEOK/.test(text))) {
    addFinding(findings, 'semantic-tool-loop-status-contradiction', 'P1', 'Structured transcript cells contain a completion-style assistant message before max_steps_exhausted halt status.', {
      haltCell: cells[maxStepsIndex]?.id,
    })
  }

  for (const shot of run.screenshots) {
    if (/breadboard transcript viewer/.test(shot.text) && rawEventRowPattern.test(shot.text)) {
      addFinding(findings, 'raw-viewer-ambiguous-label', 'P2', 'Raw viewer is labeled as transcript viewer while displaying raw events.', {
        file: shot.file,
      })
    }
  }

  if (run.scenario?.id === 'vscode_v5d_command_surface_cleanliness' && /raw event viewer/.test(run.viewport)) {
    addFinding(findings, 'raw-viewer-left-open', 'P1', 'V5-D command-surface scenario ended with the raw event viewer still active.')
  }

  if (run.scenario?.id === 'vscode_v5d_command_surface_cleanliness') {
    for (const shot of run.screenshots) {
      if (/after-raw-exit|copy-transcript|remote-toggle|mode-cycle|rewind|after-clear/.test(shot.name) && /raw event viewer/.test(shot.text)) {
        addFinding(findings, 'raw-viewer-not-exited-before-command-surface', 'P1', 'Command-surface screenshot was captured while the raw event viewer was still active.', {
          file: shot.file,
        })
      }
    }
  }

  if (/breadboard transcript viewer/.test(readableText) && rawEventRowPattern.test(readableText) && !isRawViewerText(readableText)) {
    addFinding(findings, 'raw-viewer-ambiguous-label', 'P2', 'Raw viewer is labeled as transcript viewer while displaying raw events.')
  }

  if (run.scenario?.id !== 'vscode_v5h_command_parity_decisions' && /Permission command surface is deferred|Multiagent controls are deferred|\/goal is deferred|Diff viewer commands are deferred|\/fork is deferred/.test(readableText)) {
    addFinding(findings, 'visible-deferred-parity-surface', 'P2', 'Visible command surface includes deferred parity features that require explicit V5 contracts.')
  }

  if (run.scenario?.id === 'vscode_v5h_command_parity_decisions') {
    const helpShots = run.screenshots.filter((shot) => /help|shortcuts|visible-command-surface/i.test(shot.name))
    const helpText = helpShots.map((shot) => shot.text).join('\n')
    const deferredNames = ['/permissions', '/agents', '/goal', '/diff', '/fork']
    const leakedNames = deferredNames.filter((name) => helpText.includes(name))
    if (leakedNames.length > 0) {
      addFinding(findings, 'v5h-deferred-command-advertised', 'P2', 'V5-H visible help/shortcut surface advertises deferred parity commands.', {
        leakedNames,
      })
    }
    const explicitDeferredContracts = [
      /Permission command surface is deferred|permission policy editing is productized/i,
      /Multiagent controls are deferred/i,
      /\/goal is deferred|durable goal persistence/i,
      /Diff viewer commands are deferred|diff viewer and approval workflow/i,
      /\/fork is deferred|session graph/i,
    ]
    const missingContracts = explicitDeferredContracts
      .map((pattern, index) => ({ pattern: String(pattern), index }))
      .filter(({ index }) => !explicitDeferredContracts[index].test(readableText))
    if (missingContracts.length > 0) {
      addFinding(findings, 'v5h-deferred-command-contract-missing', 'P2', 'V5-H exact deferred command invocations did not expose all explicit deferral reasons.', {
        missingContracts,
      })
    }
  }

  if (/\/clear/.test(run.rawPlain) || /"text":"\/clear\\r"/.test(run.inputLog)) {
    const afterClearScreenshots = run.screenshots.filter((shot) => /after-clear/i.test(shot.name))
    const afterClearText = afterClearScreenshots.map((shot) => shot.text).join('\n') || run.viewport
    const afterClearNonblankLines = afterClearText.split(/\n/).filter((line) => line.trim()).length
    if (afterClearNonblankLines < 3) {
      addFinding(findings, 'clear-contract-blank-viewport', 'P1', '/clear was invoked but the after-clear capture did not repaint a usable prompt surface.')
    }
    if (/checkpoint_list|Rewind checkpoints|Requested checkpoint list|Loaded \d+ checkpoint|Mode set request|Remote streaming preference|● Raw ·|Status \[raw\]|Transcript saved|● \[command\]/.test(afterClearText)) {
      addFinding(findings, 'clear-contract-unclear', 'P1', '/clear was invoked but old command/raw/checkpoint content remains visible in after-clear captures.')
    }
  }

  return {
    ok: findings.length === 0,
    runDir: run.runDir,
    artifactDir: run.artifactDir,
    screenshotCount: run.screenshots.length,
    verdictOk: run.verdict.ok ?? null,
    findings,
  }
}

function analyzeArtifactRuns(runDirs, options = {}) {
  return runDirs.map((runDir) => analyzeArtifactRun(runDir, options))
}

module.exports = {
  analyzeArtifactRun,
  analyzeArtifactRuns,
  countOccurrences,
  listFilesRecursive,
  loadArtifactRun,
  stripAnsi,
}
