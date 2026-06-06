import { describe, expect, test } from 'vitest'
import { createRequire } from 'node:module'
import path from 'node:path'
import fs from 'node:fs'
import os from 'node:os'

const require = createRequire(import.meta.url)
const { analyzeArtifactRun } = require('../tools/vscode-terminal-harness/src/v5ArtifactDetectors.cjs')

const deepQcRoot = path.resolve(
  __dirname,
  '../../docs_tmp/cli_phase_6/CODESIGN_p14/implementation_validation_v4/deep_manual_qc_20260519/artifacts',
)

const run = (name: string) => path.join(deepQcRoot, name)
const kinds = (name: string) => new Set(analyzeArtifactRun(run(name)).findings.map((finding: { kind: string }) => finding.kind))

const makeSyntheticRun = (files: Record<string, string>, scenario: Record<string, unknown> = {}) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bb-v5-detector-'))
  const artifactDir = path.join(dir, 'artifacts')
  fs.mkdirSync(path.join(artifactDir, 'screenshots'), { recursive: true })
  fs.mkdirSync(path.join(artifactDir, 'breadboard_artifacts'), { recursive: true })
  fs.writeFileSync(path.join(artifactDir, 'scenario.json'), JSON.stringify(scenario))
  fs.writeFileSync(path.join(artifactDir, 'pty_raw.ansi'), files['pty_raw.ansi'] ?? '')
  fs.writeFileSync(path.join(artifactDir, 'scrollback_final.txt'), files['scrollback_final.txt'] ?? '')
  fs.writeFileSync(path.join(artifactDir, 'viewport_final.txt'), files['viewport_final.txt'] ?? '')
  fs.writeFileSync(path.join(artifactDir, 'breadboard_artifacts', 'repl_state.ndjson'), files['repl_state.ndjson'] ?? '')
  for (const [name, content] of Object.entries(files)) {
    if (name.startsWith('screenshots/')) {
      fs.writeFileSync(path.join(artifactDir, name), content)
    }
  }
  return dir
}

describe('P14 V5 artifact defect detectors', () => {
  test('detects stale default session/context continuation responses', () => {
    expect(kinds('vscode_harness_smoke_20260519-153510-3112588-17889')).toContain('stale-context-continuation-response')
  })

  test('detects overlay screenshot blind spots, paste marker leakage, resume spam, and queue redraw spam', () => {
    const findingKinds = kinds('vscode_harness_smoke_20260519-153241-3109497-8609')
    expect(findingKinds).toContain('blank-overlay-screenshot')
    expect(findingKinds).toContain('visible-bracketed-paste-marker')
    expect(findingKinds).toContain('resume-stale-running-spam')
    expect(findingKinds).toContain('queued-prompt-redraw-spam')
    expect(findingKinds).toContain('visible-deferred-parity-surface')
  })

  test('detects modal/input target ambiguity from a failed post-overlay sentinel prompt', () => {
    expect(kinds('vscode_harness_smoke_20260519-152922-3105979-12381')).toContain('modal-input-target-ambiguous')
  })

  test('detects readable transcript pollution, duplicate headers, and clear ambiguity', () => {
    const findingKinds = kinds('vscode_harness_smoke_20260519-154232-3121407-15939')
    expect(findingKinds).toContain('duplicate-landing-header')
    expect(findingKinds).toContain('raw-diagnostic-readable-leak')
    expect(findingKinds).toContain('transcript-saved-path-leak')
    expect(findingKinds).toContain('clear-contract-unclear')
  })

  test('detects stop/retry, assistant/tool role, and tool-loop status contradictions in green-verdict artifacts', () => {
    const findingKinds = kinds('vscode_harness_smoke_20260519-153820-3116936-14905')
    expect(findingKinds).toContain('stale-pending-row')
    expect(findingKinds).toContain('retry-duplicate-transcript-state')
    expect(findingKinds).toContain('assistant-response-rendered-as-tool')
    expect(findingKinds).toContain('semantic-assistant-text-routed-to-tool')
    expect(findingKinds).toContain('tool-loop-status-contradiction')
    expect(findingKinds).toContain('semantic-tool-loop-status-contradiction')
  })

  test('startup guard requires the sentinel in an assistant transcript cell, not only raw text', () => {
    const dir = makeSyntheticRun(
      {
        'pty_raw.ansi': 'Reply with exactly BB_V5_FRESH_CONTEXT_OK and no other words.',
        'repl_state.ndjson': `${JSON.stringify({
          state: {
            transcriptCells: [
              {
                id: 'msg:conv-1',
                role: 'user-request',
                textPreview: 'Reply with exactly BB_V5_FRESH_CONTEXT_OK and no other words.',
              },
            ],
          },
        })}\n`,
      },
      {
        id: 'vscode_default_startup_stale_context_guard',
        v5ExpectedAssistantText: 'BB_V5_FRESH_CONTEXT_OK',
        v5PromptPattern: 'BB_V5_FRESH_CONTEXT_OK',
      },
    )

    const findingKinds = new Set(analyzeArtifactRun(dir).findings.map((finding: { kind: string }) => finding.kind))
    expect(findingKinds).toContain('fresh-start-assistant-sentinel-missing')
  })

  test('permits raw rows inside an explicitly labeled raw event viewer only', () => {
    const dir = makeSyntheticRun({
      'screenshots/01-raw-viewer.txt': 'breadboard raw event viewer\nraw event viewer · follow tail\nStatus [raw] {"event":"debug"}\n',
      'screenshots/02-copy-transcript.txt': 'BreadBoard v0.2.0\n❯ Type your request…\n',
      'viewport_final.txt': 'BreadBoard v0.2.0\n❯ Type your request…\n',
    })
    const findingKinds = new Set(analyzeArtifactRun(dir).findings.map((finding: { kind: string }) => finding.kind))
    expect(findingKinds).not.toContain('raw-diagnostic-readable-leak')
    expect(findingKinds).not.toContain('raw-viewer-ambiguous-label')
  })

  test('flags raw rows that leak into normal command-surface screenshots', () => {
    const dir = makeSyntheticRun({
      'screenshots/02-copy-transcript.txt': 'BreadBoard v0.2.0\nStatus [raw] {"event":"debug"}\n❯ Type your request…\n',
    })
    const findingKinds = new Set(analyzeArtifactRun(dir).findings.map((finding: { kind: string }) => finding.kind))
    expect(findingKinds).toContain('raw-diagnostic-readable-leak')
  })

  test('requires overlay screenshots to contain the overlay they claim to capture', () => {
    const dir = makeSyntheticRun({
      'screenshots/01-models-wide-open.txt': 'BreadBoard v0.2.0\n❯ Type your request…\n',
      'screenshots/02-usage-open.txt': 'Usage\nNo usage metrics reported yet.\nEsc close\n',
    })
    const findingKinds = new Set(analyzeArtifactRun(dir).findings.map((finding: { kind: string }) => finding.kind))
    expect(findingKinds).toContain('overlay-screenshot-label-missing')
    expect(findingKinds).not.toContain('blank-overlay-screenshot')
  })

  test('requires V5-F no-tool sentinel to be an assistant-message cell', () => {
    const dir = makeSyntheticRun(
      {
        'pty_raw.ansi': 'BBDEEPMARKDOWNOK',
        'repl_state.ndjson': `${JSON.stringify({
          state: {
            transcriptCells: [
              {
                id: 'msg:conv-1',
                role: 'user-request',
                textPreview: 'Reply with exactly the single word formed by joining BB, DEEP, MARKDOWN, and OK.',
              },
              {
                id: 'tool:bad',
                role: 'tool-result',
                textPreview: 'BBDEEPMARKDOWNOK',
              },
            ],
          },
        })}\n`,
      },
      { id: 'vscode_v5f_assistant_role_semantics' },
    )
    const findingKinds = new Set(analyzeArtifactRun(dir).findings.map((finding: { kind: string }) => finding.kind))
    expect(findingKinds).toContain('v5f-assistant-role-contract-failed')
  })

  test('requires V5-F tool completion to include tool-result plus final assistant without max steps', () => {
    const dir = makeSyntheticRun(
      {
        'pty_raw.ansi': 'max_steps_exhausted\nDone.',
        'repl_state.ndjson': `${JSON.stringify({
          state: {
            transcriptCells: [
              {
                id: 'msg:conv-1',
                role: 'user-request',
                textPreview: 'Create bb_v5f_result.txt containing BBDEEPCREATEOK.',
              },
              {
                id: 'msg:conv-2',
                role: 'assistant-message',
                textPreview: 'Done.',
              },
            ],
          },
        })}\n`,
      },
      { id: 'vscode_v5f_tool_completion_semantics' },
    )
    const findingKinds = new Set(analyzeArtifactRun(dir).findings.map((finding: { kind: string }) => finding.kind))
    expect(findingKinds).toContain('v5f-tool-completion-contract-failed')
    expect(findingKinds).toContain('v5f-tool-completion-max-steps')
  })

  test('flags duplicate visible queued-prompt surfaces in one frame', () => {
    const dir = makeSyntheticRun({
      'screenshots/01-queued.txt': 'Queued prompt\nWill send later\nQueued prompt\nSends after current response finishes\n',
    })
    const findingKinds = new Set(analyzeArtifactRun(dir).findings.map((finding: { kind: string }) => finding.kind))
    expect(findingKinds).toContain('queued-prompt-visible-duplicate')
  })

  test('requires V5-G queue contract to prove queued prompt becomes a later assistant turn', () => {
    const dir = makeSyntheticRun(
      {
        'repl_state.ndjson': `${JSON.stringify({
          state: {
            transcriptCells: [
              {
                role: 'user-request',
                textPreview: 'Use shell to echo QUEUEFIRSTSTART and then reply with QUEUE FIRST DONE.',
              },
              {
                role: 'assistant-message',
                textPreview: 'QUEUEFIRSTDONE',
              },
              {
                role: 'user-request',
                textPreview: 'Reply with exactly the single word formed by joining QUEUE, SECOND, OK, and DONE.',
              },
            ],
          },
        })}\n`,
      },
      { id: 'vscode_v5g_busy_queue_semantics' },
    )
    const findingKinds = new Set(analyzeArtifactRun(dir).findings.map((finding: { kind: string }) => finding.kind))
    expect(findingKinds).toContain('v5g-queue-contract-failed')
  })

  test('requires V5-G retry contract to prove clean retry completion after halt', () => {
    const dir = makeSyntheticRun(
      {
        'repl_state.ndjson': `${JSON.stringify({
          state: {
            transcriptCells: [
              {
                role: 'user-request',
                textPreview: 'Use shell to echo RETRYFIRSTSTART and then reply with RETRY FIRST DONE.',
              },
              {
                role: 'system',
                textPreview: '[halted] stopped_by_user',
              },
              {
                role: 'status',
                status: 'pending',
                textPreview: '[task] update',
              },
              {
                role: 'user-request',
                textPreview: 'Use shell to echo RETRYFIRSTSTART and then reply with RETRY FIRST DONE.',
              },
            ],
          },
        })}\n`,
      },
      { id: 'vscode_v5g_retry_command_semantics' },
    )
    const findingKinds = new Set(analyzeArtifactRun(dir).findings.map((finding: { kind: string }) => finding.kind))
    expect(findingKinds).toContain('v5g-retry-contract-failed')
  })

  test('requires V5-H visible command surfaces to hide deferred parity commands', () => {
    const dir = makeSyntheticRun(
      {
        'screenshots/01-visible-command-surface-help.txt': '/help /goal /resume /fork\n',
        'screenshots/03-explicit-deferred-invocations.txt': 'Permission command surface is deferred\nMultiagent controls are deferred\n/goal is deferred\nDiff viewer commands are deferred\n/fork is deferred\n',
      },
      { id: 'vscode_v5h_command_parity_decisions' },
    )
    const findingKinds = new Set(analyzeArtifactRun(dir).findings.map((finding: { kind: string }) => finding.kind))
    expect(findingKinds).toContain('v5h-deferred-command-advertised')
    expect(findingKinds).not.toContain('visible-deferred-parity-surface')
  })
})
