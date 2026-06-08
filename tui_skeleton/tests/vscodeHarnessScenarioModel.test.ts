import { describe, expect, test } from 'vitest'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)
const { validateScenario } = require('../tools/vscode-terminal-harness/src/scenarioModel.cjs')

describe('VSCode terminal harness scenario model', () => {
  test('accepts a minimal valid scenario', () => {
    const result = validateScenario({
      id: 'smoke',
      terminal: { initialCols: 100, initialRows: 30 },
      v6: {
        expectedFinalMode: 'repl',
        allowedWarnings: [],
        allowedDeferredCommands: [],
        allowRawJsonInModes: ['rawViewer'],
        forbiddenVisibleText: ['You are Codex'],
        maxProviderWarningCount: 0,
        maxTypeYourRequestCount: 1,
        minScreenshotCount: 1,
        requiresStableViewport: true,
      },
      steps: [
        { kind: 'write', text: 'echo hi\r' },
        { kind: 'waitForText', text: 'hi', timeoutMs: 1000 },
        { kind: 'key', key: 'enter' },
        { kind: 'waitForStableFrame', scope: 'viewport', stableMs: 100, timeoutMs: 1000 },
        {
          kind: 'waitForState',
          timeoutMs: 1000,
          fresh: true,
          pendingResponse: false,
          conversationCountAtLeast: 2,
          toolEventsCountAtLeast: 1,
          lastConversationSpeaker: 'assistant',
          lastConversationPhase: 'final',
          lastConversationPreviewIncludes: 'done',
          lastToolEventStatus: 'success',
        },
        { kind: 'resize', cols: 80, rows: 24 },
        { kind: 'snapshot', label: 'done' },
        { kind: 'assert', assertions: [{ kind: 'notContains', scope: 'raw', text: 'nope' }] },
      ],
      assertions: [{ kind: 'contains', text: 'hi' }, { kind: 'countAtMost', scope: 'viewport', text: 'hi', count: 1 }],
    })
    expect(result).toEqual({ ok: true, errors: [] })
  })

  test('rejects unsupported step kinds and invalid dimensions', () => {
    const result = validateScenario({
      id: 'bad',
      terminal: { initialCols: 10, initialRows: 2 },
      steps: [{ kind: 'wat', text: 'x' }, { kind: 'resize', cols: 1, rows: 1 }],
    })
    expect(result.ok).toBe(false)
    expect(result.errors.join('\n')).toContain('terminal.initialCols')
    expect(result.errors.join('\n')).toContain('unsupported')
    expect(result.errors.join('\n')).toContain('cols')
  })

  test('validates V6 metadata fields when present', () => {
    const result = validateScenario({
      id: 'bad-v6',
      v6: {
        expectedFinalMode: 'somewhere',
        allowedWarnings: 'no',
        allowedDeferredCommands: 'no',
        allowRawJsonInModes: ['rawViewer', 'badMode'],
        forbiddenVisibleText: 'no',
        maxProviderWarningCount: -1,
        maxTypeYourRequestCount: 1.5,
        minScreenshotCount: -1,
        requiresStableViewport: 'yes',
      },
      steps: [{ kind: 'write', text: 'echo hi\r' }],
    })
    expect(result.ok).toBe(false)
    expect(result.errors.join('\n')).toContain('v6.expectedFinalMode unsupported')
    expect(result.errors.join('\n')).toContain('v6.allowedWarnings')
    expect(result.errors.join('\n')).toContain('v6.allowRawJsonInModes')
    expect(result.errors.join('\n')).toContain('v6.requiresStableViewport')
  })
})
